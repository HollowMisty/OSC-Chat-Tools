"""Update checker against the upstream GitHub releases.

Compares versions as integer tuples so being *ahead* of upstream never reports
out-of-date, and multi-segment versions (1.5.9.1, 1.5.69.42) order correctly.
"""
from __future__ import annotations

import requests

REPO_RELEASES_URL = "https://api.github.com/repos/Lioncat6/OSC-Chat-Tools/releases"


def version_tuple(s: str) -> tuple[int, ...]:
    """Parse 'v1.5.73' / 'Version 1.5.73' into a tuple of ints for comparison."""
    s = s.lower().replace("version", "").replace("v", "").replace(" ", "").strip()
    parts = []
    for p in s.split("."):
        digits = "".join(ch for ch in p if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def check_for_update(current_version: str, timeout: float = 10.0):
    """Return (latest_version, is_out_of_date). On any error: (None, False)."""
    try:
        resp = requests.get(REPO_RELEASES_URL, timeout=timeout)
        if not resp.ok:
            return None, False
        data = resp.json()
        if not data:
            return None, False
        latest = data[0].get("tag_name", "")
        return latest, version_tuple(latest) > version_tuple(current_version)
    except Exception:
        return None, False
