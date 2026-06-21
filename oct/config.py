"""Application settings: a dataclass persisted as JSON.

This replaces the old positional-list config (please-do-not-delete.txt), where
every new setting meant editing a dozen version lists kept in lockstep and a
single reordering silently corrupted everyone's config. Here settings are keyed
by name, so adding a field is a one-line change and old/new files stay forward-
and backward-compatible.
"""
from __future__ import annotations

import ast
import json
import sys
from dataclasses import dataclass, field, asdict, fields
from pathlib import Path
from typing import Any

# Anchor config to a stable location so it survives restarts regardless of the
# working directory: next to the executable when frozen, else the project root.
if getattr(sys, "frozen", False):
    _BASE_DIR = Path(sys.executable).resolve().parent
else:
    _BASE_DIR = Path(__file__).resolve().parent.parent

CONFIG_PATH = _BASE_DIR / "oct_config.json"
# The old FreeSimpleGUI app's settings file, for one-time migration.
LEGACY_CONFIG_PATH = _BASE_DIR / "please-do-not-delete.txt"

# Default custom-theme colours (mirrors the built-in "Dark" theme).
DEFAULT_DARK_COLORS = {
    "bgColor": "#333333",
    "accentColor": "#4d4d4d",
    "fontColor": "grey85",
    "buttonColor": "#5e5e5e",
    "scrollbarColor": "#4d4d4d",
    "scrollbarBackgroundColor": "#4d4d4d",
    "tabBackgroundColor": "#4d4d4d",
    "tabTextColor": "grey85",
}


@dataclass
class Settings:
    # --- messaging ---
    message_delay: float = 1.5
    messageString: str = ""
    FileToRead: str = ""
    scrollText: bool = False
    sendBlank: bool = True
    suppressDuplicates: bool = False
    sendASAP: bool = False
    skipBlankSends: bool = False
    # --- song / media ---
    hideSong: bool = False
    hideOutside: bool = True
    showPaused: bool = True
    songDisplay: str = " \U0001F3B5'{title}' ᵇʸ {artist}\U0001F3B6"
    showOnChange: bool = False
    songChangeTicks: int = 1
    showSongInfo: bool = True
    useMediaManager: bool = True
    useSpotifyApi: bool = False
    spotifySongDisplay: str = "\U0001F3B5'{title}' ᵇʸ {artist}\U0001F3B6 『{song_progress}/{song_length}』"
    spotifyAccessToken: str = ""
    spotifyRefreshToken: str = ""
    spotify_client_id: str = "915e1de141b3408eb430d25d0d39b380"
    removeParenthesis: bool = False
    # --- layout ---
    layoutString: str = ""
    verticalDivider: str = "〣"
    topBar: str = "╔═════════════╗"
    middleBar: str = "╠═════════════╣"
    bottomBar: str = "╚═════════════╝"
    cpuDisplay: str = "ᴄᴘᴜ: {cpu_percent}%"
    ramDisplay: str = "ʀᴀᴍ: {ram_percent}%  ({ram_used}/{ram_total})"
    gpuDisplay: str = "ɢᴘᴜ: {gpu_percent}%"
    hrDisplay: str = "\U0001F493 {hr}"
    playTimeDisplay: str = "⏳{hours}:{remainder_minutes}"
    mutedDisplay: str = "Muted \U0001F507"
    unmutedDisplay: str = "\U0001F50A"
    timerDisplay: str = "{hours}:{minutes}:{seconds}"
    timerEndStamp: int = 0
    # --- time ---
    timeDisplayAM: str = "{hour}:{minute} AM"
    timeDisplayPM: str = "{hour}:{minute} PM"
    useTimeParameters: bool = False
    # --- speech to text ---
    sttDisplay: str = "{stt}"
    sttBackend: str = "faster-whisper"  # swappable backend id (room for moonshine etc.)
    sttDevice: str = "cpu"  # "cpu" or "cuda" (GPU needs CUDA + cuBLAS/cuDNN installed)
    whisperModel: str = "base.en"
    micDevice: str = ""  # "" or "Default" = system default input device
    sttLanguage: str = "en"  # ISO code, or "auto"
    sttTarget: str = ""  # translate output to this language code ("" = off;
    # "en" uses Whisper's offline translate, others use Google via deep-translator)
    sttNoiseGate: float = 0.02  # amplitude 0..1 to count a block as speech
    sttSilenceMs: int = 1500  # silence (ms) that ends a spoken phrase
    sttHoldSeconds: int = 8  # how long a transcription stays shown before clearing
    # --- keybinds ---
    keybind_run: str = "`"
    keybind_afk: str = "end"
    useAfkKeybind: bool = False
    # --- heart rate ---
    pulsoidToken: str = ""
    usePulsoid: bool = True
    useHypeRate: bool = False
    hypeRateKey: str = ""
    hypeRateSessionId: str = ""
    avatarHR: bool = False
    blinkOverride: bool = False
    blinkSpeed: float = 0.5
    toggleBeat: bool = True
    # --- OSC ---
    oscListenAddress: str = "127.0.0.1"
    oscListenPort: str = "9001"
    oscSendAddress: str = "127.0.0.1"
    oscSendPort: str = "9000"
    oscForewordAddress: str = "127.0.0.1"
    oscForewordPort: str = "9002"
    oscListen: bool = False
    oscForeword: bool = False
    # --- app ---
    selectedTheme: str = "Dark"
    customColors: dict = field(default_factory=lambda: dict(DEFAULT_DARK_COLORS))
    minimizeOnStart: bool = False
    updatePrompt: bool = True
    logOutput: bool = False


def _coerce(value: Any, default: Any) -> Any:
    """Best-effort coerce a loaded value to the type of its default."""
    if isinstance(default, bool):
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        return bool(value)
    if isinstance(default, float):
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
    if isinstance(default, int):
        try:
            return int(value)
        except (TypeError, ValueError):
            return default
    return value


def from_dict(data: dict) -> "Settings":
    """Build Settings from a dict, ignoring unknown keys and coercing types."""
    s = Settings()
    for f in fields(Settings):
        if f.name in data:
            setattr(s, f.name, _coerce(data[f.name], getattr(s, f.name)))
    return s


def load_settings(path: Path = CONFIG_PATH) -> "Settings":
    """Load settings from JSON; migrate from the legacy file on first run."""
    if path.is_file():
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return from_dict(json.load(fh))
        except Exception:
            return Settings()
    migrated = migrate_legacy()
    if migrated is not None:
        save_settings(migrated, path)
        return migrated
    return Settings()


def save_settings(settings: "Settings", path: Path = CONFIG_PATH) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(asdict(settings), fh, ensure_ascii=False, indent=2)

LEGACY_FIELDS_1_5_73 = [
    "confVersion", "message_delay", "messageString", "FileToRead", "scrollText",
    "hideSong", "hideOutside", "showPaused", "songDisplay", "showOnChange",
    "songChangeTicks", "minimizeOnStart", "keybind_run", "keybind_afk", "topBar",
    "middleBar", "bottomBar", "pulsoidToken", "avatarHR", "blinkOverride",
    "blinkSpeed", "useAfkKeybind", "toggleBeat", "updatePrompt", "oscListenAddress",
    "oscListenPort", "oscSendAddress", "oscSendPort", "oscForewordAddress",
    "oscForeword", "oscListen", "oscForeword", "logOutput", "layoutString",
    "verticalDivider", "cpuDisplay", "ramDisplay", "gpuDisplay", "hrDisplay",
    "playTimeDisplay", "mutedDisplay", "unmutedDisplay", "darkMode", "sendBlank",
    "suppressDuplicates", "sendASAP", "useMediaManager", "useSpotifyApi",
    "spotifySongDisplay", "spotifyAccessToken", "spotifyRefreshToken", "usePulsoid",
    "useHypeRate", "hypeRateKey", "hypeRateSessionId", "timeDisplayPM",
    "timeDisplayAM", "showSongInfo", "spotify_client_id", "useTimeParameters",
    "removeParenthesis", "timerDisplay", "timerEndStamp", "selectedTheme",
    "customColors",
]

_LEGACY_1_4_1 = [
    'confVersion', 'deprecated_topTextToggle', 'deprecated_topTimeToggle',
    'deprecated_topSongToggle', 'deprecated_topCPUToggle', 'deprecated_topRAMToggle',
    'deprecated_topNoneToggle', 'deprecated_bottomTextToggle',
    'deprecated_deprecated_bottomTimeToggle', 'deprecated_bottomSongToggle',
    'deprecated_bottomCPUToggle', 'deprecated_bottomRAMToggle',
    'deprecated_bottomNoneToggle', 'message_delay', 'messageString', 'FileToRead',
    'scrollText', 'hideSong', 'deprecated_hideMiddle', 'hideOutside', 'showPaused',
    'songDisplay', 'showOnChange', 'songChangeTicks', 'minimizeOnStart',
    'keybind_run', 'keybind_afk', 'topBar', 'middleBar', 'bottomBar',
    'deprecated_topHRToggle', 'deprecated_bottomHRToggle', 'pulsoidToken', 'avatarHR',
    'blinkOverride', 'blinkSpeed', 'useAfkKeybind', 'toggleBeat', 'updatePrompt',
]

_LEGACY_1_4_20 = _LEGACY_1_4_1 + [
    'oscListenAddress', 'oscListenPort', 'oscSendAddress', 'oscSendPort',
    'oscForewordAddress', 'oscForeword', 'oscListen', 'oscForeword', 'logOutput',
]
LEGACY_FIELD_ORDERS = {"1.4.1": _LEGACY_1_4_1, "1.4.20": _LEGACY_1_4_20}


def _convert_legacy_layout(d: dict) -> str:
    """Build a layout string from the old 1.4.x per-side toggle config."""
    def on(key: str) -> bool:
        return bool(d.get(key))
    s = ""
    if on("deprecated_topTextToggle"): s += "{text(0)}"
    if on("deprecated_topTimeToggle"): s += "{time(0)}"
    if on("deprecated_topSongToggle"): s += "{song(0)}"
    if on("deprecated_topCPUToggle"): s += "{cpu(0)}"
    if on("deprecated_topRAMToggle"): s += "{ram(0)}"
    top_any = any(on(k) for k in (
        "deprecated_topTextToggle", "deprecated_topTimeToggle", "deprecated_topSongToggle",
        "deprecated_topCPUToggle", "deprecated_topRAMToggle"))
    bottom_any = any(on(k) for k in (
        "deprecated_bottomTextToggle", "deprecated_deprecated_bottomTimeToggle",
        "deprecated_bottomSongToggle", "deprecated_bottomCPUToggle", "deprecated_bottomRAMToggle"))
    if not on("deprecated_hideMiddle") and top_any and bottom_any:
        s += "{div(0)}"
    if on("deprecated_bottomTextToggle"): s += "{text(0)}"
    if on("deprecated_deprecated_bottomTimeToggle"): s += "{time(0)}"
    if on("deprecated_bottomSongToggle"): s += "{song(0)}"
    if on("deprecated_bottomCPUToggle"): s += "{cpu(0)}"
    if on("deprecated_bottomRAMToggle"): s += "{ram(0)}"
    return s


def migrate_legacy(path: Path = LEGACY_CONFIG_PATH) -> "Settings | None":
    """Import the old positional-list config from any version.

    The file's version (element 0) selects the field order: 1.4.x use their own
    lists; every 1.5.x version is a prefix of the 1.5.73 order so they all map
    correctly against it.
    """
    if not path.is_file():
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            values = ast.literal_eval(fh.read())
        if not isinstance(values, list) or not values:
            return None
        version = values[0] if isinstance(values[0], str) else ""
        order = LEGACY_FIELD_ORDERS.get(version, LEGACY_FIELDS_1_5_73)
        data = {name: value for name, value in zip(order, values)}
        # 1.4.x had no layout string - build one from the old per-side toggles.
        if version in ("1.4.1", "1.4.20"):
            data["layoutString"] = _convert_legacy_layout(data)
        # Older configs predate selectedTheme; derive it from the darkMode flag.
        if "selectedTheme" not in data and "darkMode" in data:
            data["selectedTheme"] = "Dark" if data.get("darkMode") else "Light"
        return from_dict(data)
    except Exception:
        return None
