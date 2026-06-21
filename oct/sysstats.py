"""System statistics: CPU, RAM and GPU usage providers."""
from __future__ import annotations

import psutil

try:
    import pynvml
    _NVML_AVAILABLE = True
except Exception:
    _NVML_AVAILABLE = False

_nvml_inited = False


def _ensure_nvml() -> bool:
    global _nvml_inited
    if _NVML_AVAILABLE and not _nvml_inited:
        try:
            pynvml.nvmlInit()
            _nvml_inited = True
        except Exception:
            return False
    return _nvml_inited


def cpu_percent() -> float:
    return psutil.cpu_percent()


def ram_info() -> dict:
    """RAM usage in GiB (matches the original): percent, used, available, total.

    used is computed as total - available, like the original.
    """
    m = psutil.virtual_memory()
    gib = 1073741824
    total = m.total / gib
    available = m.available / gib
    return {
        "percent": int(m.percent),
        "total": round(total, 1),
        "available": round(available, 1),
        "used": round(total - available, 1),
    }


def gpu_percent() -> int | None:
    """GPU utilisation percent for device 0, or None if unavailable."""
    if not _ensure_nvml():
        return None
    try:
        handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        return pynvml.nvmlDeviceGetUtilizationRates(handle).gpu
    except Exception:
        return None
