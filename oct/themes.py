"""Theme definitions and Qt stylesheet (QSS) generation.

Each theme is a set of eight colours. ``build_qss`` turns a colour set into a Qt
stylesheet string. Unlike the old FreeSimpleGUI app (which could only change the
theme by rebuilding the window), in Qt you just call ``widget.setStyleSheet(...)``
and the whole UI re-themes instantly.
"""
from __future__ import annotations

import os

# Absolute path to the check-mark SVG used by checked checkboxes (forward slashes
# for QSS url()).
_CHECK_URL = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "ui", "assets", "check.svg"
).replace(os.sep, "/")

# Ordered colour fields every theme defines.
COLOR_KEYS = [
    "bgColor", "accentColor", "fontColor", "buttonColor",
    "scrollbarColor", "scrollbarBackgroundColor", "tabBackgroundColor", "tabTextColor",
]

# Built-in presets. Values are hex so they map straight to QSS.
THEMES: dict[str, dict[str, str]] = {
    "Dark": {
        "bgColor": "#333333", "accentColor": "#4d4d4d", "fontColor": "#d9d9d9",
        "buttonColor": "#5e5e5e", "scrollbarColor": "#4d4d4d",
        "scrollbarBackgroundColor": "#4d4d4d", "tabBackgroundColor": "#4d4d4d",
        "tabTextColor": "#d9d9d9",
    },
    "Light": {
        "bgColor": "#64778d", "accentColor": "#528b8b", "fontColor": "#ffffff",
        "buttonColor": "#283b5b", "scrollbarColor": "#283b5b",
        "scrollbarBackgroundColor": "#a6b2be", "tabBackgroundColor": "#ffffff",
        "tabTextColor": "#000000",
    },
    "Midnight": {
        "bgColor": "#1b1f2a", "accentColor": "#2b3142", "fontColor": "#d9d9d9",
        "buttonColor": "#3d465e", "scrollbarColor": "#2b3142",
        "scrollbarBackgroundColor": "#2b3142", "tabBackgroundColor": "#2b3142",
        "tabTextColor": "#d9d9d9",
    },
    "Forest": {
        "bgColor": "#22312a", "accentColor": "#33473d", "fontColor": "#d9d9d9",
        "buttonColor": "#436353", "scrollbarColor": "#33473d",
        "scrollbarBackgroundColor": "#33473d", "tabBackgroundColor": "#33473d",
        "tabTextColor": "#d9d9d9",
    },
    "Purple": {
        "bgColor": "#2a2139", "accentColor": "#3b2f5c", "fontColor": "#d9d9d9",
        "buttonColor": "#4f3f7a", "scrollbarColor": "#3b2f5c",
        "scrollbarBackgroundColor": "#3b2f5c", "tabBackgroundColor": "#3b2f5c",
        "tabTextColor": "#d9d9d9",
    },
}

# A few Tk colour names the old config might still contain, mapped to hex.
_TK_NAME_TO_HEX = {
    "grey85": "#d9d9d9", "gray85": "#d9d9d9",
    "white": "#ffffff", "black": "#000000",
}


def normalize_color(value: str) -> str:
    """Return a QSS-safe colour (hex passes through; known Tk names convert)."""
    if not value:
        return "#000000"
    v = value.strip()
    if v.startswith("#"):
        return v
    return _TK_NAME_TO_HEX.get(v.lower(), v)


def resolve_colors(theme_name: str, custom_colors: dict | None) -> dict[str, str]:
    """Return the colour set for a theme name, or the custom set for 'Custom'."""
    if theme_name == "Custom" and custom_colors:
        src = custom_colors
    else:
        src = THEMES.get(theme_name, THEMES["Dark"])
    return {k: normalize_color(src.get(k, THEMES["Dark"][k])) for k in COLOR_KEYS}


def theme_names() -> list[str]:
    return list(THEMES.keys()) + ["Custom"]


def _is_dark(hexcolor: str) -> bool:
    h = hexcolor.lstrip("#")
    if len(h) != 6:
        return True
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return (0.299 * r + 0.587 * g + 0.114 * b) < 128


def _shade(hexcolor: str, amount: float) -> str:
    """Lighten (amount > 0) or darken (amount < 0) a hex colour."""
    h = hexcolor.lstrip("#")
    if len(h) != 6:
        return hexcolor
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    if amount >= 0:
        r = int(r + (255 - r) * amount)
        g = int(g + (255 - g) * amount)
        b = int(b + (255 - b) * amount)
    else:
        f = 1 + amount
        r, g, b = int(r * f), int(g * f), int(b * f)
    return f"#{max(0, min(255, r)):02x}{max(0, min(255, g)):02x}{max(0, min(255, b)):02x}"


def build_qss(theme_name: str, custom_colors: dict | None = None) -> str:
    """Generate a Qt stylesheet for the given theme."""
    c = resolve_colors(theme_name, custom_colors)
    bg = c["bgColor"]
    accent = c["accentColor"]
    fg = c["fontColor"]
    btn = c["buttonColor"]
    sbar = c["scrollbarColor"]
    sbar_bg = c["scrollbarBackgroundColor"]
    tab_bg = c["tabBackgroundColor"]
    tab_fg = c["tabTextColor"]
    # A distinct surface for text fields / output so they don't blend into the page.
    field_bg = _shade(bg, 0.16) if _is_dark(bg) else _shade(bg, -0.12)
    btn_hover = _shade(btn, 0.18) if _is_dark(btn) else _shade(btn, -0.12)
    btn_pressed = _shade(btn, -0.12) if _is_dark(btn) else _shade(btn, 0.12)
    check_url = _CHECK_URL
    return f"""
    QWidget {{
        background-color: {bg};
        color: {fg};
        font-family: 'Segoe UI', Arial, sans-serif;
        font-size: 11pt;
    }}
    QMainWindow, QDialog {{ background-color: {bg}; }}
    QGroupBox {{
        background-color: {accent};
        border: 1px solid {btn};
        border-radius: 6px;
        margin-top: 20px;
        padding: 12px 8px 8px 8px;
        font-weight: bold;
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        subcontrol-position: top left;
        left: 10px;
        padding: 2px 8px;
        background-color: {btn};
        border-radius: 4px;
        color: {fg};
    }}
    QPushButton {{
        background-color: {btn};
        color: {fg};
        border: none;
        border-radius: 4px;
        padding: 5px 12px;
    }}
    QPushButton:hover {{ background-color: {btn_hover}; }}
    QPushButton:pressed {{ background-color: {btn_pressed}; }}
    QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox {{
        background-color: {field_bg};
        color: {fg};
        border: 1px solid {accent};
        border-radius: 4px;
        padding: 3px;
        selection-background-color: {accent};
    }}
    QComboBox {{
        background-color: {btn};
        color: {fg};
        border: 1px solid {accent};
        border-radius: 4px;
        padding: 3px 6px;
    }}
    QComboBox QAbstractItemView {{
        background-color: {bg};
        color: {fg};
        selection-background-color: {accent};
    }}
    QCheckBox {{ background-color: transparent; color: {fg}; spacing: 6px; }}
    QCheckBox::indicator {{
        width: 16px;
        height: 16px;
        border: 1px solid {fg};
        border-radius: 3px;
        background: transparent;
    }}
    QCheckBox::indicator:checked {{
        border: 1px solid {fg};
        image: url("{check_url}");
        background: transparent;
    }}
    QLabel {{ background-color: transparent; color: {fg}; }}
    QTabWidget::pane {{ border: 1px solid {accent}; background-color: {bg}; }}
    QTabBar::tab {{
        background-color: {tab_bg};
        color: {tab_fg};
        padding: 6px 12px;
        border: none;
    }}
    QTabBar::tab:selected {{ background-color: {accent}; color: {fg}; }}
    QMenuBar {{ background-color: {accent}; color: {fg}; }}
    QMenuBar::item {{ background: transparent; padding: 6px 12px; }}
    QMenuBar::item:selected {{ background-color: {btn}; }}
    QMenu {{ background-color: {bg}; color: {fg}; border: 1px solid {accent}; }}
    QMenu::item {{ padding: 5px 20px; }}
    QMenu::item:selected {{ background-color: {accent}; }}
    QScrollBar:vertical, QScrollBar:horizontal {{ background: {sbar_bg}; border: none; }}
    QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{
        background: {sbar}; border-radius: 4px; min-height: 20px;
    }}
    QScrollBar::add-line, QScrollBar::sub-line {{ background: none; border: none; }}

    /* Hub navigation */
    QPushButton#card {{
        background-color: {accent};
        color: {fg};
        border: 1px solid {btn};
        border-radius: 12px;
        padding: 16px;
        font-size: 13pt;
        min-width: 150px;
        min-height: 96px;
    }}
    QPushButton#card:hover {{ background-color: {btn}; border: 1px solid {fg}; }}
    QPushButton#card:pressed {{ background-color: {bg}; }}
    QLabel#homeTitle {{ font-size: 22pt; font-weight: bold; }}
    QLabel#homeSubtitle {{ font-size: 11pt; color: {fg}; }}
    QLabel#pageTitle {{ font-size: 16pt; font-weight: bold; }}
    QPushButton#backBtn {{
        background-color: {btn};
        border-radius: 6px;
        padding: 6px 14px;
    }}
    QPushButton#backBtn:hover {{ background-color: {btn_hover}; }}
    QWidget#headerBar {{ background-color: {accent}; border-radius: 8px; }}
    """
