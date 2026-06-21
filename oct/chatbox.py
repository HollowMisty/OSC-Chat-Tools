"""Chatbox layout engine - faithful port of the original msgGen().

The layout string is a sequence of element tokens ``{name(d)}`` with literal text
allowed between them. ``d`` is the data digit controlling separators appended
after the element:

    0 = nothing   1 = vertical divider   2 = new line   3 = divider + new line

New lines use VRChat's vertical-tab character (``\\v``). After assembly, dangling
separators are trimmed and - unless ``hideOutside`` - the message is wrapped in
the top/bottom bars, matching the original behaviour.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from . import sysstats

_TOKEN_RE = re.compile(r"\{(\w+)\((\d)\)\}")
_LF = "\v"  # VRChat chatbox line feed


@dataclass
class ChatboxContext:
    settings: object                       # oct.config.Settings
    message_text: str = ""                 # current text frame
    song_title: str = ""
    song_artist: str = ""
    song_album_title: str = ""
    song_album_artist: str = ""
    song_playing: bool = False
    song_progress: str = "0:00"
    song_length: str = "0:00"
    song_volume: str = "0"
    song_id: str = "N/A"
    song_source: str = ""  # "spotify" or "media" - selects the display template
    heart_rate: int | None = None
    muted: bool = False
    play_seconds: int = 0
    timer_remaining_ms: int = 0
    state: dict = field(default_factory=dict)  # persisted by caller (show-on-change)


def _fmt(template: str, **kwargs) -> str:
    try:
        return template.format_map(defaultdict(str, **kwargs))
    except Exception:
        return template


def _check_data(msg: str, digit: str, divider: str) -> str:
    if digit in ("1", "3"):
        msg = msg + " " + divider
    if digit in ("2", "3"):
        msg = msg + _LF
    return msg


def _song_info(ctx: ChatboxContext) -> str | None:
    """Return the formatted song string, or None if it should be hidden."""
    s = ctx.settings
    title = ctx.song_title
    if s.removeParenthesis and title:
        title = re.sub(r" ?\([^)]*\)", "", title)
    playing = ctx.song_playing
    template = s.spotifySongDisplay if ctx.song_source == "spotify" else s.songDisplay
    info = _fmt(
        template, artist=ctx.song_artist, title=title,
        album_title=ctx.song_album_title, album_artist=ctx.song_album_artist,
        song_progress=ctx.song_progress, song_length=ctx.song_length,
        volume=ctx.song_volume, song_id=ctx.song_id,
    )
    if not (playing or not s.showPaused):
        info = info + " ⏸️"
    if (s.hideSong and not playing) or title == "":
        return None
    if s.showOnChange:
        st = ctx.state
        if info != st.get("song_name"):
            st["tick_count"] = s.songChangeTicks
            st["song_name"] = info
        if st.get("tick_count", 0) != 0:
            st["tick_count"] = st.get("tick_count", 0) - 1
            return info
        return None
    return info


def _element_value(name: str, ctx: ChatboxContext) -> str:
    s = ctx.settings
    if name == "text":
        return ctx.message_text.replace("\\n", _LF).replace("\\v", _LF)
    if name == "time":
        now = datetime.now()
        hour24 = now.strftime("%H")
        minute = now.strftime("%M")
        tz = now.astimezone().tzname() or ""
        h = int(hour24)
        if h >= 12:
            hour = (h - 12) or 12
            template = s.timeDisplayPM
        else:
            hour = h or 12
            template = s.timeDisplayAM
        return _fmt(template, hour=hour, minute=minute, time_zone=tz, hour24=hour24)
    if name == "cpu":
        return _fmt(s.cpuDisplay, cpu_percent=str(sysstats.cpu_percent()))
    if name == "ram":
        r = sysstats.ram_info()
        return _fmt(s.ramDisplay, ram_percent=r["percent"], ram_used=r["used"],
                    ram_available=r["available"], ram_total=r["total"])
    if name == "gpu":
        g = sysstats.gpu_percent()
        return _fmt(s.gpuDisplay, gpu_percent=g if g is not None else "0", vram_percent="0")
    if name == "hr":
        hr = str(ctx.heart_rate if ctx.heart_rate is not None else 0)
        if hr in ("0", "1"):
            hr = "-"
        return _fmt(s.hrDisplay, hr=hr)
    if name == "mute":
        return s.mutedDisplay if ctx.muted else s.unmutedDisplay
    if name == "playtime":
        minutes = ctx.play_seconds // 60
        hours, remainder_minutes = divmod(minutes, 60)
        return _fmt(s.playTimeDisplay, hours=f"{hours:02d}",
                    remainder_minutes=f"{remainder_minutes:02d}", minutes=f"{minutes:02d}")
    if name == "timer":
        ms = max(0, ctx.timer_remaining_ms)
        hours, remainder = divmod(ms // 1000, 3600)
        minutes, seconds = divmod(remainder, 60)
        return _fmt(s.timerDisplay, hours=f"{hours:02d}", minutes=f"{minutes:02d}", seconds=f"{seconds:02d}")
    if name == "stt":
        return "Coming Soon"
    if name == "div":
        return s.middleBar
    return ""


def _trim_trailing(msg: str, divider: str, middle_bar: str) -> str:
    if msg[-len(divider + " "):] == divider + " ":
        msg = msg[:-len(divider + " ") - 1]
    if msg[-len(middle_bar + " "):] == middle_bar + " ":
        msg = msg[:-len(middle_bar + " ")]
    if "\v " in msg[-2:]:
        msg = msg[:-2]
    if "\v" in msg[-2:]:
        msg = msg[:-1]
    return msg


def build_message(ctx: ChatboxContext) -> str:
    """Render the configured layout string into a chatbox message (with \\v breaks)."""
    s = ctx.settings
    divider = s.verticalDivider
    layout = s.layoutString or ""
    out: list[str] = []
    pos = 0
    for m in _TOKEN_RE.finditer(layout):
        if m.start() > pos:
            out.append(layout[pos:m.start()])
        name, digit = m.group(1), m.group(2)
        if name == "song":
            value = _song_info(ctx)
            out.append("" if value is None else _check_data(value, digit, divider))
        else:
            out.append(_check_data(_element_value(name, ctx), digit, divider))
        pos = m.end()
    if pos < len(layout):
        out.append(layout[pos:])
    msg = "".join(out)
    if msg:
        msg = _trim_trailing(msg, divider, s.middleBar)
    if not s.hideOutside:
        msg = s.topBar + " " + msg + " " + s.bottomBar
    return msg.replace("\\n", _LF).replace("\\v", _LF)
