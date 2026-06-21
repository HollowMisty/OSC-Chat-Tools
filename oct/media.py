"""Now-playing media info.

Primary source is the Windows system media transport controls (the same data the
volume flyout shows) via the ``winrt`` projection, falling back to ``winsdk``.
Spotify Web API support is stubbed for a later pass; the media-manager path is
the default and covers most players.
"""
from __future__ import annotations

import asyncio

try:  # modern projection (has Python 3.12 wheels)
    from winrt.windows.media.control import (
        GlobalSystemMediaTransportControlsSessionManager as MediaManager,
    )
    import winrt.windows.media.control as wmc
    _MEDIA_AVAILABLE = True
except ImportError:
    try:  # legacy fallback
        from winsdk.windows.media.control import (
            GlobalSystemMediaTransportControlsSessionManager as MediaManager,
        )
        import winsdk.windows.media.control as wmc
        _MEDIA_AVAILABLE = True
    except ImportError:
        _MEDIA_AVAILABLE = False

# GlobalSystemMediaTransportControlsSessionPlaybackStatus.PLAYING == 4
_PLAYING = 4


async def _now_playing_async() -> dict | None:
    sessions = await MediaManager.request_async()
    current = sessions.get_current_session()
    if current is None:
        return None
    info = await current.try_get_media_properties_async()
    playback = current.get_playback_info()
    try:
        status = int(playback.playback_status)
    except Exception:
        status = -1
    return {
        "title": info.title or "",
        "artist": info.artist or "",
        "album_title": getattr(info, "album_title", "") or "",
        "album_artist": getattr(info, "album_artist", "") or "",
        "playing": status == _PLAYING,
    }


def get_now_playing() -> dict | None:
    """Return {'title','artist','playing'} for the active session, or None."""
    if not _MEDIA_AVAILABLE:
        return None
    try:
        return asyncio.run(_now_playing_async())
    except Exception:
        return None
