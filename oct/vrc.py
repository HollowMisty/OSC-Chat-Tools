"""VRChat process detection and play-time tracking.

Call ``poll()`` periodically (about once a second). ``play_seconds()`` returns
how long VRChat has been running, or 0 when it isn't.
"""
from __future__ import annotations

import time

import psutil

_pid: int | None = None
_play_start: float | None = None


def _alive(pid: int | None) -> bool:
    try:
        return pid is not None and psutil.pid_exists(pid)
    except Exception:
        return False


def poll() -> None:
    """Refresh the cached VRChat PID and its start time."""
    global _pid, _play_start
    if _alive(_pid):
        return
    _pid = None
    _play_start = None
    for proc in psutil.process_iter(["name", "pid"]):
        try:
            name = proc.info.get("name") or ""
            if "VRChat.exe" in name:
                _pid = proc.info["pid"]
                _play_start = psutil.Process(_pid).create_time()
                break
        except Exception:
            continue


def is_running() -> bool:
    return _pid is not None


def play_seconds() -> int:
    if _pid is None or _play_start is None:
        return 0
    return max(0, int(time.time() - _play_start))
