"""Local speech-to-text: microphone capture + noise-gate VAD + transcription.

Audio is captured with sounddevice in small blocks. A simple amplitude noise
gate decides which blocks are speech; a phrase ends after a configurable run of
silence (or a hard length cap), at which point the buffered audio is handed to a
transcriber running on its own thread. The default transcriber is faster-whisper
(local, offline, no API key, no torch - CTranslate2 with int8), but the capture
layer is backend-agnostic so another engine (e.g. Moonshine) can be slotted in
later by swapping ``_Transcriber`` without touching the VAD.

This blends the project's old ``stt`` branch (local Whisper + always-on
listening feeding a ``{stt}`` token) with MagicChatBox's silence-based
segmentation and partial/auto-finalise behaviour.
"""
from __future__ import annotations

import os
import queue
import threading
import time
from typing import Callable

# Quieten Hugging Face hub noise before faster-whisper imports it: Windows
# symlink fallback warning and usage telemetry. (Set before any HF import.)
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

# ctranslate2 uses OpenMP (Intel libiomp) on Windows. An OpenMP thread pool
# created on one thread and then reused from another - e.g. loading a SECOND
# model after switching, from a new loader thread - deadlocks (the first model
# loads fine, the next hangs forever). Forcing single-threaded OpenMP avoids the
# orphaned-pool deadlock. Must be set before ctranslate2 is imported.
os.environ.setdefault("OMP_NUM_THREADS", "1")


_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# NVIDIA pip wheels providing the runtime DLLs ctranslate2 needs for CUDA 12.
_CUDA_PACKAGES = [
    "nvidia-cuda-runtime-cu12",  # cudart64_12.dll
    "nvidia-cublas-cu12",        # cublas64_12.dll, cublasLt64_12.dll
    "nvidia-cudnn-cu12",         # cudnn*64_9.dll
    "nvidia-cuda-nvrtc-cu12",    # nvrtc64_120_0.dll
]


def _cuda_dirs():
    """Candidate folders for app-managed CUDA DLLs (first is preferred)."""
    import sys
    out = []
    if getattr(sys, "frozen", False):
        out.append(os.path.join(os.path.dirname(sys.executable), "cuda"))
    else:
        out.append(os.path.join(_PROJECT_ROOT, "cuda"))
    local = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    out.append(os.path.join(local, "OSC Chat Tools", "cuda"))
    return out


def _add_cuda_dll_dirs():
    """Register folders that may hold the CUDA DLLs (cudart/cublas/cudnn) so
    ctranslate2's GPU path can find them: pip's nvidia-* wheels (source runs) and
    the app-managed 'cuda' folders populated by download_cuda_libraries().
    No-op off Windows (os.add_dll_directory only exists there)."""
    if not hasattr(os, "add_dll_directory"):
        return
    dirs = []
    try:
        import nvidia
        for base in getattr(nvidia, "__path__", []):
            try:
                for sub in os.listdir(base):
                    bin_dir = os.path.join(base, sub, "bin")
                    if os.path.isdir(bin_dir):
                        dirs.append(bin_dir)
            except Exception:
                pass
    except Exception:
        pass
    dirs += _cuda_dirs()
    for d in dirs:
        if not os.path.isdir(d):
            continue
        try:
            os.add_dll_directory(d)
        except Exception:
            pass
        # Prepend to PATH so transitive deps (cublas -> cudart) also resolve.
        os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")


_add_cuda_dll_dirs()


def reset_cuda_probe():
    """Allow the GPU path to be re-probed (it's cached as unusable after a
    failure). Call after installing CUDA libraries so GPU can be enabled without
    a restart; also re-registers the newly-populated DLL folder."""
    global _cuda_usable, _cuda_error
    _cuda_usable = None
    _cuda_error = ""
    _add_cuda_dll_dirs()


def cuda_libraries_present() -> bool:
    """Whether the key CUDA DLL is available (app-managed dir or pip wheels)."""
    for d in _cuda_dirs():
        if os.path.exists(os.path.join(d, "cublas64_12.dll")):
            return True
    try:
        import nvidia
        for base in getattr(nvidia, "__path__", []):
            if os.path.exists(os.path.join(base, "cublas", "bin", "cublas64_12.dll")):
                return True
    except Exception:
        pass
    return False


def _win_wheel_files(pkg: str):
    """(url, size) list of the newest version's win_amd64 wheels for a package."""
    import json
    import urllib.request
    with urllib.request.urlopen(f"https://pypi.org/pypi/{pkg}/json", timeout=30) as r:
        data = json.load(r)
    cur = [(f["url"], f.get("size", 0)) for f in data.get("urls", [])
           if f["filename"].endswith("win_amd64.whl")]
    if cur:
        return cur

    def vkey(v):
        return [int(p) if p.isdigit() else 0 for p in v.replace("-", ".").split(".")]

    for ver in sorted(data.get("releases", {}), key=vkey, reverse=True):
        wins = [(f["url"], f.get("size", 0)) for f in data["releases"][ver]
                if f["filename"].endswith("win_amd64.whl")]
        if wins:
            return wins
    return []


def download_cuda_libraries(on_status=lambda s: None) -> str:
    """Download the NVIDIA CUDA 12 + cuDNN 9 runtime DLLs from PyPI and extract
    them into an app-managed 'cuda' folder that the DLL search path includes.
    Returns the install dir. Blocking; raises on failure."""
    import shutil
    import tempfile
    import urllib.request
    import zipfile

    on_status("Preparing CUDA download...")
    plan, total = [], 0
    for pkg in _CUDA_PACKAGES:
        files = _win_wheel_files(pkg)
        if not files:
            raise RuntimeError(f"No Windows wheel found for {pkg}")
        url, size = files[0]
        plan.append((url, size))
        total += size or 0

    target = None
    for d in _cuda_dirs():
        try:
            os.makedirs(d, exist_ok=True)
            probe = os.path.join(d, ".write_test")
            with open(probe, "w") as fh:
                fh.write("x")
            os.remove(probe)
            target = d
            break
        except Exception:
            continue
    if target is None:
        raise RuntimeError("No writable location for CUDA libraries")

    done = 0
    for url, size in plan:
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".whl")
        try:
            with urllib.request.urlopen(url, timeout=60) as resp:
                while True:
                    chunk = resp.read(1 << 20)
                    if not chunk:
                        break
                    tmp.write(chunk)
                    done += len(chunk)
                    if total:
                        on_status(f"Downloading CUDA {int(done / total * 100)}%...")
            tmp.close()
            with zipfile.ZipFile(tmp.name) as z:
                for name in z.namelist():
                    norm = name.replace("\\", "/")
                    if norm.lower().endswith(".dll") and "/bin/" in norm:
                        out = os.path.join(target, os.path.basename(norm))
                        with z.open(name) as src, open(out, "wb") as dst:
                            shutil.copyfileobj(src, dst, 1 << 20)
        finally:
            try:
                os.unlink(tmp.name)
            except Exception:
                pass

    if hasattr(os, "add_dll_directory"):
        try:
            os.add_dll_directory(target)
        except Exception:
            pass
    os.environ["PATH"] = target + os.pathsep + os.environ.get("PATH", "")
    on_status("CUDA libraries installed")
    return target

try:
    import numpy as np
    import sounddevice as sd
    _AUDIO_OK = True
    _AUDIO_ERR = ""
except Exception as e:  # pragma: no cover - depends on host install
    np = None  # type: ignore
    sd = None  # type: ignore
    _AUDIO_OK = False
    _AUDIO_ERR = str(e)

SAMPLE_RATE = 16000
BLOCK = 1600          # 0.1s capture blocks
MAX_PHRASE_S = 15.0   # force-flush a phrase that runs this long
MIN_PHRASE_S = 0.4    # ignore blips shorter than this

# Curated model list for the UI. faster-whisper downloads these from Hugging
# Face on first use and caches them; ".en" variants are English-only and faster.
MODELS = [
    "tiny.en", "base.en", "small.en", "medium.en",
    "tiny", "base", "small", "medium",
    "distil-small.en", "distil-medium.en", "large-v3", "large-v3-turbo",
]


def audio_available() -> tuple[bool, str]:
    """Whether sounddevice/numpy imported (mic capture possible)."""
    return _AUDIO_OK, _AUDIO_ERR


def whisper_available() -> tuple[bool, str]:
    try:
        import faster_whisper  # noqa: F401
        return True, ""
    except Exception as e:
        return False, str(e)


def _wasapi_index():
    """Index of the WASAPI host API, or None. WASAPI lists only active/enabled
    endpoints, so it excludes Windows-disabled devices and the duplicate entries
    that MME/DirectSound/WDM-KS would otherwise add."""
    if not _AUDIO_OK:
        return None
    try:
        for i, ha in enumerate(sd.query_hostapis()):
            if "wasapi" in ha.get("name", "").lower():
                return i
    except Exception:
        pass
    return None


def _input_devices():
    """(index, name) of usable input devices, restricted to WASAPI when present."""
    if not _AUDIO_OK:
        return []
    wa = _wasapi_index()
    out = []
    try:
        for i, d in enumerate(sd.query_devices()):
            if d.get("max_input_channels", 0) <= 0:
                continue
            if wa is not None and d.get("hostapi") != wa:
                continue
            out.append((i, d.get("name", "")))
    except Exception:
        pass
    return out


def list_input_devices() -> list[str]:
    """Names of available (enabled) input devices for the settings dropdown."""
    seen, out = set(), []
    for _i, name in _input_devices():
        if name and name not in seen:
            seen.add(name)
            out.append(name)
    return out


def _device_index(name: str):
    """Resolve a stored device name to an index, or None for the default."""
    if not name or name == "Default":
        return None
    for i, n in _input_devices():
        if n == name:
            return i
    return None


# Serialises model construction and downloads so they never collide on Hugging
# Face's per-file locks. Only ever held by background threads (loader/download),
# never the UI thread.
_LOAD_LOCK = threading.Lock()

# Tri-state CUDA usability, probed once per process: None = not tried yet,
# True = a CUDA model loaded and ran, False = CUDA failed (e.g. missing cuBLAS).
# Once False, we never retry the GPU path - a second failed CUDA init hangs.
_cuda_usable = None
_cuda_error = ""  # the real exception text from the failed CUDA attempt


def _repo_id(model_name: str) -> str:
    try:
        from faster_whisper import utils as fw_utils
        return getattr(fw_utils, "_MODELS", {}).get(model_name, model_name)
    except Exception:
        return model_name


def _hf_cache_dir() -> str:
    try:
        from huggingface_hub.constants import HF_HUB_CACHE
        return HF_HUB_CACHE
    except Exception:
        return os.path.join(os.path.expanduser("~"), ".cache", "huggingface", "hub")


def is_model_cached(model_name: str) -> bool:
    """True if the model weights are on disk. Pure filesystem check - no network
    and no file locks, so it's safe from the UI thread even mid-download."""
    repo_dir = os.path.join(_hf_cache_dir(), "models--" + _repo_id(model_name).replace("/", "--"))
    snapshots = os.path.join(repo_dir, "snapshots")
    if not os.path.isdir(snapshots):
        return False
    for rev in os.listdir(snapshots):
        if os.path.exists(os.path.join(snapshots, rev, "model.bin")):
            return True
    return False


def cached_models() -> list[str]:
    """Curated models that are currently downloaded."""
    return [m for m in MODELS if is_model_cached(m)]


def download(model_name: str, on_status: Callable[[str], None] = lambda s: None):
    """Download a model into the cache, reporting integer %% progress. Blocking;
    raises on failure."""
    from faster_whisper import utils as fw_utils
    repo_id = _repo_id(model_name)
    on_status("Downloading 0%...")
    with _LOAD_LOCK:
        try:
            from huggingface_hub import snapshot_download
            from huggingface_hub.utils import tqdm as hf_tqdm

            class _ProgressTqdm(hf_tqdm):
                def update(self, n=1):
                    result = super().update(n)
                    try:
                        # Only the large weight files are worth reporting; the small
                        # "Fetching N files" bar (total = file count) is ignored.
                        if self.total and self.total > 1_000_000:
                            pct = min(100, int(self.n / self.total * 100))
                            if pct != getattr(self, "_last_pct", -1):
                                self._last_pct = pct
                                on_status(f"Downloading {pct}%...")
                    except Exception:
                        pass
                    return result

            snapshot_download(
                repo_id, tqdm_class=_ProgressTqdm,
                allow_patterns=["config.json", "preprocessor_config.json",
                                "model.bin", "tokenizer.json", "vocabulary.*"],
            )
        except TypeError:
            # Older huggingface_hub without tqdm_class support: plain download.
            on_status("Downloading (no progress available)...")
            fw_utils.download_model(model_name)
    on_status("Downloaded")


class _FasterWhisperTranscriber:
    """Lazy-loaded faster-whisper backend. transcribe(float32 mono 16k) -> str."""

    def __init__(self, model_name: str, language: str, device_pref: str = "cpu"):
        self.model_name = model_name
        self.language = (language or "").strip()
        self.device_pref = (device_pref or "cpu").lower()
        self._model = None
        self.device = "cpu"
        self.fell_back = False  # GPU was requested but CUDA wasn't usable
        self.cuda_error = ""    # real reason the GPU path failed, if any

    def load(self, on_status=lambda s: None):
        from faster_whisper import WhisperModel

        # Download first (with progress) if the model isn't cached, then always
        # construct with local_files_only=True so faster-whisper never pings
        # Hugging Face for a revision check (that network call is what made it
        # hang on "loading" on slow/offline connections).
        self._ensure_downloaded(on_status)

        def _make(device: str, compute_type: str):
            on_status(f"Loading {self.model_name} on {device.upper()}...")
            with _LOAD_LOCK:  # don't construct two ctranslate2 models at once
                model = WhisperModel(self.model_name, device=device,
                                     compute_type=compute_type, local_files_only=True)
            if device == "cuda":
                # Warm up so missing CUDA libraries (cublas64_12.dll / cudnn)
                # surface now rather than mid-session.
                segments, _ = model.transcribe(np.zeros(SAMPLE_RATE, dtype=np.float32), beam_size=1)
                list(segments)
            return model

        global _cuda_usable, _cuda_error
        if self.device_pref in ("cuda", "gpu"):
            if _cuda_usable is False:
                # CUDA already proven unusable this session - don't retry it
                # (a second failed CUDA init hangs instead of erroring).
                self.fell_back = True
                self.cuda_error = _cuda_error
            else:
                try:
                    self._model = _make("cuda", "float16")
                    self.device = "cuda"
                    _cuda_usable = True
                    return
                except Exception as e:
                    _cuda_usable = False
                    _cuda_error = str(e)
                    self.cuda_error = _cuda_error
                    self.fell_back = True  # fall through to CPU below
        self._model = _make("cpu", "int8")
        self.device = "cpu"

    def _ensure_downloaded(self, on_status):
        """Download on demand if Run is started with an un-cached model."""
        if not is_model_cached(self.model_name):
            download(self.model_name, on_status)

    def transcribe(self, audio) -> str:
        kwargs = {}
        if self.language and self.language.lower() != "auto":
            kwargs["language"] = self.language
        segments, _info = self._model.transcribe(audio, beam_size=1, **kwargs)
        return " ".join(seg.text.strip() for seg in segments).strip()


class SpeechToText:
    """Continuous mic listener that emits transcribed phrases via ``on_text``."""

    def __init__(self, model_name: str = "base.en", device_name: str = "",
                 language: str = "en", noise_gate: float = 0.02,
                 silence_ms: int = 1500, device_pref: str = "cpu",
                 on_text: Callable[[str], None] = lambda t: None,
                 on_status: Callable[[str], None] = lambda s: None):
        self.device_name = device_name
        self.noise_gate = float(noise_gate)
        self.silence_ms = int(silence_ms)
        self.on_text = on_text
        self.on_status = on_status
        self._transcriber = _FasterWhisperTranscriber(model_name, language, device_pref)

        self._run = False
        self._stream = None
        self._capture_rate = SAMPLE_RATE  # actual mic rate; resampled to 16k for Whisper
        self._audio_q: "queue.Queue[bytes]" = queue.Queue()
        self._worker: threading.Thread | None = None
        self._loader: threading.Thread | None = None
        # capture/VAD state
        self._buf = bytearray()
        self._lock = threading.Lock()
        self._speaking = False
        self._last_voice = 0.0
        self._phrase_start = 0.0

    # -- lifecycle -----------------------------------------------------------
    def start(self):
        ok, err = audio_available()
        if not ok:
            self.on_status(f"Audio unavailable ({err}). Run: pip install sounddevice")
            return
        wok, werr = whisper_available()
        if not wok:
            self.on_status(f"faster-whisper not installed ({werr}). Run: pip install faster-whisper")
            return
        if self._run:
            return
        self._run = True
        self.on_status("Loading speech model...")
        self._loader = threading.Thread(target=self._load_and_listen, daemon=True)
        self._loader.start()

    def _load_and_listen(self):
        try:
            self._transcriber.load(self.on_status)
        except Exception as e:
            self.on_status(f"Model load failed: {e}")
            self._run = False
            return
        if not self._run:
            return  # Run was switched off while the model was still loading
        dev = _device_index(self.device_name)
        rate = SAMPLE_RATE
        # Many devices (esp. WASAPI shared mode) reject 16 kHz; PortAudio won't
        # resample, so capture at the device's native rate and resample later.
        try:
            sd.check_input_settings(device=dev, samplerate=SAMPLE_RATE, channels=1, dtype="int16")
        except Exception:
            try:
                qd = sd.query_devices(dev if dev is not None else None, "input")
                rate = int(qd.get("default_samplerate") or 48000)
            except Exception:
                rate = 48000
        self._capture_rate = rate
        try:
            self._stream = sd.RawInputStream(
                samplerate=rate, blocksize=max(256, int(rate / 10)), dtype="int16",
                channels=1, callback=self._on_block, device=dev)
            self._stream.start()
        except Exception as e:
            self.on_status(f"Microphone error: {e}")
            self._run = False
            return
        self._worker = threading.Thread(target=self._transcribe_loop, daemon=True)
        self._worker.start()
        if self._transcriber.fell_back:
            err = (self._transcriber.cuda_error or "CUDA libs missing").strip()
            self.on_status(f"GPU unavailable, using CPU - {err[:160]}")
        else:
            self.on_status(f"Listening ({self._transcriber.device.upper()})")

    def stop(self):
        self._run = False
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        # Wait for the worker (and any in-flight loader) to actually exit, so no
        # transcription is running when the next model is constructed - otherwise
        # ctranslate2's thread pool can deadlock building a second model.
        for t in (self._worker, self._loader):
            if t is not None and t.is_alive() and t is not threading.current_thread():
                t.join(timeout=3.0)
        self._worker = None
        self._loader = None
        try:
            while True:
                self._audio_q.get_nowait()
        except queue.Empty:
            pass
        with self._lock:
            self._buf = bytearray()
        self._speaking = False
        self._transcriber._model = None  # release the model promptly

    # -- capture / VAD -------------------------------------------------------
    def _on_block(self, indata, frames, time_info, status):
        if not self._run:
            return
        raw = bytes(indata)
        samples = np.frombuffer(raw, dtype=np.int16)
        if samples.size == 0:
            return
        amp = float(np.abs(samples).max()) / 32768.0
        now = time.monotonic()
        if amp >= self.noise_gate:
            if not self._speaking:
                self._speaking = True
                self._phrase_start = now
                with self._lock:
                    self._buf = bytearray()
            with self._lock:
                self._buf.extend(raw)
            self._last_voice = now
            if now - self._phrase_start >= MAX_PHRASE_S:
                self._flush()
        elif self._speaking and (now - self._last_voice) * 1000.0 >= self.silence_ms:
            self._flush()

    def _flush(self):
        with self._lock:
            data = bytes(self._buf)
            self._buf = bytearray()
        self._speaking = False
        if len(data) >= int(self._capture_rate * MIN_PHRASE_S) * 2:  # int16 = 2 bytes/sample
            self._audio_q.put(data)

    # -- transcription -------------------------------------------------------
    def _transcribe_loop(self):
        while self._run:
            try:
                data = self._audio_q.get(timeout=0.2)
            except queue.Empty:
                continue
            if not self._run:
                break
            try:
                audio = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
                if self._capture_rate != SAMPLE_RATE and audio.size:
                    n_out = int(round(audio.size * SAMPLE_RATE / self._capture_rate))
                    if n_out > 0:
                        audio = np.interp(
                            np.linspace(0.0, 1.0, n_out, endpoint=False),
                            np.linspace(0.0, 1.0, audio.size, endpoint=False),
                            audio,
                        ).astype(np.float32)
                text = self._transcriber.transcribe(audio)
                if text:
                    self.on_text(text)
            except Exception as e:
                self.on_status(f"Transcription error: {e}")
