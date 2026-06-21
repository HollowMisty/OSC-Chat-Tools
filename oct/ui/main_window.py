"""Main application window (PySide6) - hub-and-spoke navigation.

Instead of tabs, a Home page shows big cards that branch into each section. The
Behavior section is itself a grid of element cards leading to that element's
settings. Every sub-page has a Back button. Theming is applied on Apply.
"""
from __future__ import annotations

import re
import threading
import time
import webbrowser
from datetime import datetime

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QAction, QBrush, QColor
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QColorDialog, QComboBox, QDoubleSpinBox, QFileDialog,
    QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
    QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QRadioButton,
    QScrollArea, QSizePolicy, QSlider, QSpinBox, QStackedWidget, QVBoxLayout, QWidget,
)

from .. import __version__, themes, media, updater, heartrate, vrc, keybinds, spotify, stt
from ..config import Settings, load_settings, save_settings, CONFIG_PATH
from ..chatbox import ChatboxContext, build_message
from ..osc_io import OSCClient, OSCListener

ELEMENT_LABELS = {
    "text": "\U0001F4ACText", "time": "\U0001F552Time", "song": "\U0001F3B5Song",
    "cpu": "⏱️CPU Usage", "ram": "\U0001F6A6RAM Usage", "gpu": "⏳GPU Usage",
    "hr": "\U0001F493Heart Rate", "mute": "\U0001F507Mute Status",
    "playtime": "⌚Play Time", "stt": "⌨Speech To Text",
    "div": "☵Divider", "timer": "⏲️Timer",
}
ADD_ELEMENTS = [
    ("text", "\U0001F4ACText", "A configurable text object", True),
    ("time", "\U0001F552Time", "Display your current time", True),
    ("song", "\U0001F3B5Song", "Customizable song display", True),
    ("cpu", "⏱️CPU", "Display CPU Utilization %", True),
    ("ram", "\U0001F6A6RAM", "Display RAM Usage %", True),
    ("gpu", "⏳GPU", "Display GPU Utilization %", True),
    ("hr", "\U0001F493HR", "Display Heart Rate", True),
    ("mute", "\U0001F507Mute", "Display Mic Mute Status", True),
    ("playtime", "⌚Play Time", "Show Play Time", True),
    ("stt", "⌨STT", "Speech recognition object", True),
    ("div", "☵Divider", "Horizontal Divider", True),
    ("timer", "⏲️Timer", "Countdown Timer", True),
]
# Section cards on the Home page: (page key, emoji, title)
HOME_CARDS = [
    ("layout", "\U0001F9E9", "Layout"),
    ("preview", "\U0001F4FA", "Preview"),
    ("options", "\U0001F4BB", "Options"),
    ("osc", "\U0001F4F2", "OSC Options"),
    ("output", "\U0001F4BE", "Output"),
    ("help", "❓", "Help"),
]
# Element cards on the Behavior hub: (page key, emoji, title)
BEHAVIOR_CARDS = [
    ("behavior_misc", "❔", "Misc."),
    ("behavior_text", "\U0001F4AC", "Text"),
    ("behavior_time", "\U0001F552", "Time"),
    ("behavior_song", "\U0001F3B5", "Song"),
    ("behavior_cpu", "⏱️", "CPU"),
    ("behavior_ram", "\U0001F6A6", "RAM"),
    ("behavior_gpu", "⏳", "GPU"),
    ("behavior_hr", "\U0001F493", "HR"),
    ("behavior_mute", "\U0001F507", "Mute"),
    ("behavior_playtime", "⌚", "Play Time"),
    ("behavior_stt", "⌨", "STT"),
    ("behavior_div", "☵", "Divider"),
    ("behavior_timer", "⏲️", "Timer"),
]

_TOKEN_RE = re.compile(r"\{(\w+)\((\d)\)\}")

# (display, Whisper code). "Auto-detect" sits at the top of the dropdown; English
# is the default selection. Only multilingual models use this (.en models ignore it).
STT_LANGUAGES = [
    ("Auto-detect", "auto"), ("English", "en"),
    ("Arabic", "ar"), ("Chinese", "zh"), ("Czech", "cs"), ("Danish", "da"),
    ("Dutch", "nl"), ("Finnish", "fi"), ("French", "fr"), ("German", "de"),
    ("Greek", "el"), ("Hebrew", "he"), ("Hindi", "hi"), ("Hungarian", "hu"),
    ("Indonesian", "id"), ("Italian", "it"), ("Japanese", "ja"), ("Korean", "ko"),
    ("Norwegian", "no"), ("Polish", "pl"), ("Portuguese", "pt"), ("Romanian", "ro"),
    ("Russian", "ru"), ("Spanish", "es"), ("Swedish", "sv"), ("Thai", "th"),
    ("Turkish", "tr"), ("Ukrainian", "uk"), ("Vietnamese", "vi"),
]


class MainWindow(QMainWindow):
    update_result = Signal(str, bool, str)
    run_toggle_signal = Signal()
    afk_toggle_signal = Signal()
    spotify_status_signal = Signal(str)
    log_signal = Signal(str)
    stt_text_signal = Signal(str, str, bool)
    stt_status_signal = Signal(str)
    stt_dl_progress = Signal(str)
    stt_dl_done = Signal(str)
    stt_cuda_done = Signal(str)

    def __init__(self):
        super().__init__()
        self.settings: Settings = load_settings()
        self._bindings: list[tuple] = []
        self._custom_colors: dict = dict(self.settings.customColors)
        self._swatches: dict = {}
        self._hexlabels: dict = {}
        self._row_widgets: dict = {}
        self._pages: dict = {}
        self._running = False
        self._osc: OSCClient | None = None
        self._syncing = False
        self._last_sent: str | None = None
        self._sent_elapsed = 0.0
        self._chatbox_state: dict = {}
        self._frame_index = 0
        self._cycle_accum = 0.0
        self._skipped = False
        self._heart_rate: int | None = None
        self._hr_provider = None
        self._hr_signature_current = None
        self._beat_thread = None
        self._beat_running = False
        self._osc_listener = None
        self._osc_sig = None
        self._osc_mute = False
        self._osc_afk = False
        self._keybinds = keybinds.KeybindManager()
        self._afk_index = 0
        self._scroll_pos = 0
        self._spotify = None
        self._spotify_now = None
        self._spotify_now_at = 0.0
        self._last_spotify_err = None
        self._media_now = None
        self._ribbon = None
        self._stt = None
        self._stt_text = ""
        self._stt_text_at = 0.0
        self._stt_sig = None

        self.setWindowTitle("OSC Chat Tools")
        self.resize(960, 680)
        self._build_menu()
        self._build_ui()
        self._apply_settings_to_ui()
        self._apply_theme()
        self.update_result.connect(self._show_update_result)
        self.run_toggle_signal.connect(self.run_check.toggle)
        self.afk_toggle_signal.connect(self.afk_check.toggle)
        self.spotify_status_signal.connect(self._on_spotify_status)
        self.log_signal.connect(self.log)
        self.stt_text_signal.connect(self._on_stt_text)
        self.stt_status_signal.connect(self._on_stt_status)
        self.stt_dl_progress.connect(self._on_stt_dl_progress)
        self.stt_dl_done.connect(self._on_stt_download_done)
        self.stt_cuda_done.connect(self._on_cuda_done)
        self._go("home")
        self.log(f"OSC Chat Tools {__version__} started.")
        self._run_update_check(force_popup=False)

        self._send_timer = QTimer(self)
        self._send_timer.timeout.connect(self._tick)
        self._send_timer.start(100)  # fixed 100ms; sending is gated to message_delay

        self._vrc_timer = QTimer(self)
        self._vrc_timer.timeout.connect(vrc.poll)
        self._vrc_timer.start(2000)
        vrc.poll()
        self._refresh_osc_listener()
        self._refresh_keybinds()
        self._refresh_hr()  # connects now if "pass through HR even when not running" is on
        self._refresh_stt()
        self._spotify_timer = QTimer(self)
        self._spotify_timer.timeout.connect(self._poll_spotify)
        self._spotify_timer.start(3000)
        self._time_timer = QTimer(self)
        self._time_timer.timeout.connect(self._send_time_params)
        self._time_timer.start(1000)
        if (self.settings.useSpotifyApi and self.settings.spotify_client_id
                and self.settings.spotifyAccessToken and self.settings.spotifyRefreshToken):
            self._spotify = spotify.SpotifyClient(
                self.settings.spotify_client_id, self.settings.spotifyAccessToken,
                self.settings.spotifyRefreshToken, on_tokens=self._save_spotify_tokens)
            self.spotify_status.setText("Linked (saved)")

        # Start in the Run state, matching the original app's default-on behaviour.
        self.run_check.setChecked(True)

    # ----------------------------------------------------------------- menu bar
    def _build_menu(self):
        menubar = self.menuBar()
        file_menu = menubar.addMenu("&File")
        for text, slot in [
            ("Home", lambda: self._go("home")), ("Apply", self._on_apply),
            ("Reset", self._on_reset), ("Open Config File", self._open_config),
            ("Open Debug Log", self._open_log), ("Exit", self.close),
        ]:
            act = QAction(text, self); act.triggered.connect(slot); file_menu.addAction(act)
        help_menu = menubar.addMenu("&Help")
        for text, slot in [
            ("About", self._about),
            ("Open Github Page", lambda: webbrowser.open("https://github.com/Lioncat6/OSC-Chat-Tools")),
            ("Check For Updates", self._check_updates),
        ]:
            act = QAction(text, self); act.triggered.connect(slot); help_menu.addAction(act)

    # ------------------------------------------------------------------ UI build
    def _bind(self, widget, field: str, kind: str):
        self._bindings.append((widget, field, kind))
        return widget

    @staticmethod
    def _scroll(widget: QWidget) -> QScrollArea:
        area = QScrollArea(); area.setWidgetResizable(True); area.setWidget(widget)
        return area

    def _add_page(self, key: str, widget: QWidget):
        self._pages[key] = widget
        self.stack.addWidget(widget)

    def _go(self, key: str):
        if key in self._pages:
            self.stack.setCurrentWidget(self._pages[key])
            if key in ("preview", "home"):
                self._update_preview()
            if key == "behavior_stt":
                self._refresh_model_lists()
                self._refresh_cuda_ui()

    def _card(self, key: str, emoji: str, title: str, enabled: bool = True) -> QPushButton:
        btn = QPushButton(f"{emoji}\n{title}")
        btn.setObjectName("card")
        btn.setEnabled(enabled)
        btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        btn.clicked.connect(lambda: self._go(key))
        return btn

    def _grid_page(self, title: str, cards: list, columns: int, back_key: str | None) -> QWidget:
        page = QWidget(); v = QVBoxLayout(page)
        if back_key is not None:
            v.addWidget(self._header(title, back_key))
        else:
            head = QVBoxLayout()
            t = QLabel(title); t.setObjectName("homeTitle"); t.setAlignment(Qt.AlignmentFlag.AlignCenter)
            sub = QLabel("Pick a section"); sub.setObjectName("homeSubtitle"); sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
            head.addWidget(t); head.addWidget(sub); v.addLayout(head)
        v.addSpacing(16)
        grid = QGridLayout(); grid.setSpacing(14)
        for i, card in enumerate(cards):
            key, emoji, label = card[0], card[1], card[2]
            enabled = card[3] if len(card) > 3 else True
            grid.addWidget(self._card(key, emoji, label, enabled), i // columns, i % columns)
        v.addLayout(grid)
        v.addStretch(1)
        return page

    def _header(self, title: str, back_key: str) -> QWidget:
        bar = QWidget(); bar.setObjectName("headerBar")
        h = QHBoxLayout(bar)
        back = QPushButton("← Back"); back.setObjectName("backBtn")
        back.clicked.connect(lambda: self._go(back_key))
        t = QLabel(title); t.setObjectName("pageTitle")
        h.addWidget(back); h.addWidget(t); h.addStretch(1)
        return bar

    def _shell(self, title: str, body: QWidget, back_key: str, scroll: bool = True) -> QWidget:
        page = QWidget(); v = QVBoxLayout(page)
        v.addWidget(self._header(title, back_key))
        v.addWidget(self._scroll(body) if scroll else body, 1)
        return page

    def _build_ui(self):
        central = QWidget(); root = QVBoxLayout(central)
        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)

        # Home + section hubs
        self._add_page("home", self._grid_page("OSC Chat Tools", HOME_CARDS, 3, None))

        # Top-level section pages
        self._add_page("layout", self._shell("\U0001F9E9 Layout", self._layout_body(), "home", scroll=False))
        self._add_page("preview", self._shell("\U0001F4FA Preview", self._preview_body(), "home"))
        self._add_page("options", self._shell("\U0001F4BB Options", self._options_body(), "home"))
        self._add_page("osc", self._shell("\U0001F4F2 OSC Options", self._osc_body(), "home"))
        self._add_page("output", self._shell("\U0001F4BE Output", self._output_body(), "home"))
        self._add_page("help", self._shell("❓ Help", self._help_body(), "home"))

        # Behavior element pages
        self._add_page("behavior_misc", self._shell("⚙ Chatbox Options", self._misc_sub(), "layout"))
        self._add_page("behavior_text", self._shell("\U0001F4AC Text", self._text_sub(), "layout"))
        self._add_page("behavior_time", self._shell("\U0001F552 Time", self._time_sub(), "layout"))
        self._add_page("behavior_song", self._shell("\U0001F3B5 Song", self._song_sub(), "layout"))
        self._add_page("behavior_cpu", self._shell("⏱️ CPU", self._template_sub("CPU display.\nVariables: {cpu_percent}", "cpuDisplay"), "layout"))
        self._add_page("behavior_ram", self._shell("\U0001F6A6 RAM", self._template_sub("RAM display.\nVariables: {ram_percent}, {ram_available}, {ram_total}, {ram_used}", "ramDisplay"), "layout"))
        self._add_page("behavior_gpu", self._shell("⏳ GPU", self._template_sub("GPU display.\nVariables: {gpu_percent}", "gpuDisplay"), "layout"))
        self._add_page("behavior_hr", self._shell("\U0001F493 HR", self._hr_sub(), "layout"))
        self._add_page("behavior_mute", self._shell("\U0001F507 Mute", self._mute_sub(), "layout"))
        self._add_page("behavior_playtime", self._shell("⌚ Play Time", self._template_sub("Play Time display.\nVariables: {hours}, {remainder_minutes}, {minutes}", "playTimeDisplay"), "layout"))
        self._add_page("behavior_stt", self._shell("⌨ STT", self._stt_sub(), "layout"))
        self._add_page("behavior_div", self._shell("☵ Divider", self._divider_sub(), "layout"))
        self._add_page("behavior_timer", self._shell("⏲️ Timer", self._timer_sub(), "layout"))

        # Persistent bottom bar, styled like the page-header strip.
        bottom = QWidget(); bottom.setObjectName("headerBar")
        bar = QHBoxLayout(bottom)
        apply_btn = QPushButton("Apply"); apply_btn.clicked.connect(self._on_apply)
        reset_btn = QPushButton("Reset"); reset_btn.clicked.connect(self._on_reset)
        self.run_check = QCheckBox("Run?"); self.run_check.toggled.connect(self._on_run_toggled)
        self.afk_check = QCheckBox("AFK")
        bar.addWidget(apply_btn); bar.addWidget(reset_btn)
        bar.addWidget(self.run_check); bar.addWidget(self.afk_check)
        bar.addStretch(1)
        self.ribbon_song = QLabel("")
        self.ribbon_song.setOpenExternalLinks(True)
        bar.addWidget(self.ribbon_song)
        bar.addStretch(1)
        bar.addWidget(QLabel(f"Version {__version__}"))
        root.addWidget(bottom)
        self.setCentralWidget(central)

    # ------------------------------------------------------------- Layout body
    def _section_title(self, text: str) -> QLabel:
        lbl = QLabel(text)
        f = lbl.font(); f.setBold(True); f.setPointSize(f.pointSize() + 1)
        lbl.setFont(f)
        return lbl

    def _layout_body(self) -> QWidget:
        w = QWidget(); outer = QVBoxLayout(w)
        top = QHBoxLayout()
        top.addWidget(self._bind(QCheckBox("Text file read (disables everything else)"), "scrollText", "check"))
        top.addStretch(1)
        misc_btn = QPushButton("Chatbox Options")
        misc_btn.clicked.connect(lambda: self._go("behavior_misc"))
        top.addWidget(misc_btn)
        outer.addLayout(top)
        cols = QHBoxLayout()

        # Add Elements: header + internally-scrolling list (no box)
        left = QWidget(); ll = QVBoxLayout(left); ll.setContentsMargins(0, 0, 0, 0)
        ll.addWidget(self._section_title("Add Elements"))
        ll.addWidget(QLabel("Add an element to your layout, or Edit to customise it"))
        add_rows = QWidget(); al = QVBoxLayout(add_rows)
        for token, label, desc, enabled in ADD_ELEMENTS:
            row = QHBoxLayout()
            row.addWidget(QLabel(label)); row.addStretch(1)
            row.addWidget(QLabel(desc)); row.addStretch(1)
            edit = QPushButton("Edit")
            edit.clicked.connect(lambda _=False, t=token: self._go(f"behavior_{t}"))
            row.addWidget(edit)
            btn = QPushButton("Add" if enabled else "Soon"); btn.setEnabled(enabled)
            btn.clicked.connect(lambda _=False, t=token: self._add_element(t))
            row.addWidget(btn); al.addLayout(row)
        al.addStretch(1)
        ll.addWidget(self._scroll(add_rows), 1)
        cols.addWidget(left, 1)

        # Arrange Elements: same styling
        right = QWidget(); rl = QVBoxLayout(right); rl.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(self._section_title("Arrange Elements"))
        rl.addWidget(QLabel("⤷ = New Line   ┋ = Vertical Divider"))
        self._editor_host = QWidget(); self._editor_layout = QVBoxLayout(self._editor_host)
        self._editor_layout.addStretch(1)
        rl.addWidget(self._scroll(self._editor_host), 1)
        rl.addWidget(self._section_title("Manual Edit"))
        rl.addWidget(QLabel("Wrap object in { }, spaces respected"))
        self.layout_manual = QPlainTextEdit(); self.layout_manual.setFixedHeight(90)
        self.layout_manual.textChanged.connect(self._on_manual_layout_changed)
        rl.addWidget(self.layout_manual)
        cols.addWidget(right, 1)
        outer.addLayout(cols)
        return w

    # ------------------------------------------------------------- element bodies
    def _misc_sub(self) -> QWidget:
        w = QWidget(); v = QVBoxLayout(w)
        v.addWidget(QLabel("File to use for the text file read functionality"))
        fr = QHBoxLayout()
        open_btn = QPushButton("Open File"); open_btn.clicked.connect(self._pick_text_file)
        self.file_label = QLabel(self.settings.FileToRead)
        fr.addWidget(open_btn); fr.addWidget(self.file_label); fr.addStretch(1); v.addLayout(fr)
        v.addWidget(QLabel("Delay between frame updates, in seconds"))
        delay_row = QHBoxLayout()
        delay = QSlider(Qt.Orientation.Horizontal)
        delay.setRange(15, 100)  # 1.5 - 10.0 seconds in 0.1 steps
        self.delay_value = QLabel("1.5s")
        delay.valueChanged.connect(lambda val: self.delay_value.setText(f"{val / 10:.1f}s"))
        delay_row.addWidget(self._bind(delay, "message_delay", "slider10"))
        delay_row.addWidget(self.delay_value)
        v.addLayout(delay_row)
        v.addWidget(QLabel("Advanced Sending Options"))
        v.addWidget(self._bind(QCheckBox("Clear the chatbox when toggled or on program close"), "sendBlank", "check"))
        v.addWidget(self._bind(QCheckBox("Skip sending duplicate messages"), "suppressDuplicates", "check"))
        v.addWidget(self._bind(QCheckBox("Send next message as soon as any data is updated"), "sendASAP", "check"))
        v.addWidget(self._bind(QCheckBox("Don't send when the message is blank"), "skipBlankSends", "check"))
        v.addStretch(1)
        return w

    def _text_sub(self) -> QWidget:
        w = QWidget(); v = QVBoxLayout(w)
        v.addWidget(QLabel("Text to display. One frame per line; use * alone for a blank frame."))
        self.message_edit = self._bind(QPlainTextEdit(), "messageString", "multiline")
        v.addWidget(self.message_edit)
        return w

    def _time_sub(self) -> QWidget:
        w = QWidget(); f = QFormLayout(w)
        f.addRow(QLabel("Variables: {hour}, {minute}, {time_zone}, {hour24}"))
        f.addRow("AM", self._bind(QLineEdit(), "timeDisplayAM", "text"))
        f.addRow("PM", self._bind(QLineEdit(), "timeDisplayPM", "text"))
        f.addRow("", self._bind(QCheckBox("Send time parameters to avatar"), "useTimeParameters", "check"))
        return w

    def _song_sub(self) -> QWidget:
        w = QWidget(); v = QVBoxLayout(w)
        v.addWidget(QLabel("Audio info source (pick one):"))
        src = QHBoxLayout()
        self.src_media = QRadioButton("Windows Now Playing")
        self.src_spotify = QRadioButton("Spotify API")
        self._src_group = QButtonGroup(self)
        self._src_group.setExclusive(True)
        self._src_group.addButton(self.src_media)
        self._src_group.addButton(self.src_spotify)
        src.addWidget(self.src_media); src.addWidget(self.src_spotify); src.addStretch(1)
        v.addLayout(src)
        v.addWidget(QLabel("Now Playing template. Variables: {artist}, {title}, {album_title}, {album_artist}"))
        v.addWidget(self._bind(QLineEdit(), "songDisplay", "text"))
        v.addWidget(QLabel("Spotify template. Adds {song_progress}, {song_length}, {volume}, {song_id}"))
        v.addWidget(self._bind(QLineEdit(), "spotifySongDisplay", "text"))
        cid_row = QHBoxLayout()
        cid_row.addWidget(QLabel("Spotify Client ID"))
        cid_help = QPushButton("?")
        cid_help.clicked.connect(lambda: webbrowser.open(
            "https://github.com/Lioncat6/OSC-Chat-Tools/wiki/Spotify-Client-ID"))
        cid_row.addWidget(cid_help)
        cid_row.addWidget(QLabel("← If linking fails, click here!"))
        cid_row.addStretch(1)
        v.addLayout(cid_row)
        v.addWidget(self._bind(QLineEdit(), "spotify_client_id", "text"))
        link = QHBoxLayout()
        link_btn = QPushButton("Link Spotify \U0001F517")
        link_btn.clicked.connect(self._link_spotify)
        manual_btn = QPushButton("Manual Code")
        manual_btn.clicked.connect(self._link_spotify_manual)
        self.spotify_status = QLabel("Unlinked")
        link.addWidget(link_btn); link.addWidget(manual_btn)
        link.addWidget(self.spotify_status); link.addStretch(1); v.addLayout(link)
        v.addWidget(self._bind(QCheckBox('Show "⏸️" after song when paused'), "showPaused", "check"))
        v.addWidget(self._bind(QCheckBox("Hide song when music is paused"), "hideSong", "check"))
        v.addWidget(self._bind(QCheckBox("Remove text inside parenthesis"), "removeParenthesis", "check"))
        v.addWidget(self._bind(QCheckBox("Only show music on song change"), "showOnChange", "check"))
        v.addWidget(QLabel("Frames to wait before the song name disappears"))
        trow = QHBoxLayout()
        ticks = QSlider(Qt.Orientation.Horizontal); ticks.setRange(1, 5)
        self.ticks_value = QLabel("2")
        ticks.valueChanged.connect(lambda val: self.ticks_value.setText(str(val)))
        trow.addWidget(self._bind(ticks, "songChangeTicks", "int")); trow.addWidget(self.ticks_value)
        v.addLayout(trow)
        v.addStretch(1)
        return w

    def _template_sub(self, hint: str, field: str) -> QWidget:
        w = QWidget(); v = QVBoxLayout(w)
        v.addWidget(QLabel("Template to use for " + hint))
        v.addWidget(self._bind(QLineEdit(), field, "text"))
        v.addStretch(1)
        return w

    def _hr_sub(self) -> QWidget:
        w = QWidget(); v = QVBoxLayout(w)
        v.addWidget(QLabel("Heart Rate display template. Variables: {hr}"))
        v.addWidget(self._bind(QLineEdit(), "hrDisplay", "text"))
        src = QHBoxLayout()
        src.addWidget(self._bind(QCheckBox("Pulsoid"), "usePulsoid", "check"))
        src.addWidget(self._bind(QCheckBox("HypeRate"), "useHypeRate", "check"))
        src.addStretch(1); v.addLayout(src)
        v.addWidget(self._bind(QCheckBox("Pass through HR avatar parameters even when not running"), "avatarHR", "check"))
        v.addWidget(self._bind(QCheckBox("Heart Rate Beat"), "toggleBeat", "check"))
        v.addWidget(self._bind(QCheckBox("Override Beat"), "blinkOverride", "check"))
        v.addWidget(QLabel("Blink Speed (if overridden)"))
        brow = QHBoxLayout()
        blink = QSlider(Qt.Orientation.Horizontal); blink.setRange(0, 500)
        self.blink_value = QLabel("0.50")
        blink.valueChanged.connect(lambda val: self.blink_value.setText(f"{val / 100:.2f}"))
        brow.addWidget(self._bind(blink, "blinkSpeed", "slider100")); brow.addWidget(self.blink_value)
        v.addLayout(brow)
        v.addWidget(QLabel("Pulsoid Token"))
        prow = QHBoxLayout()
        prow.addWidget(self._bind(QLineEdit(), "pulsoidToken", "text"))
        ptok = QPushButton("Get Token \U0001F493")
        ptok.clicked.connect(lambda: webbrowser.open(
            "https://pulsoid.net/oauth2/authorize?response_type=token"
            "&client_id=8070496f-f886-4030-8340-96d1d68b25cb&redirect_uri="
            "&scope=data:heart_rate:read&state=&response_mode=web_page"))
        prow.addWidget(ptok); v.addLayout(prow)
        v.addWidget(QLabel("HypeRate API Key"))
        hrow = QHBoxLayout()
        hrow.addWidget(self._bind(QLineEdit(), "hypeRateKey", "text"))
        hkey = QPushButton("Get Key \U0001F49E")
        hkey.clicked.connect(lambda: webbrowser.open(
            "https://github.com/Lioncat6/OSC-Chat-Tools/wiki/HypeRate-Keys"))
        hrow.addWidget(hkey); v.addLayout(hrow)
        v.addWidget(QLabel("HypeRate Session ID"))
        v.addWidget(self._bind(QLineEdit(), "hypeRateSessionId", "text"))
        v.addStretch(1)
        return w

    def _stt_sub(self) -> QWidget:
        w = QWidget(); v = QVBoxLayout(w)
        intro = QLabel("Transcribes your mic into the chatbox while Run is on. Add the ⌨STT "
                       "element to your layout, and use 'Get model' to download more models "
                       "(needs internet once, then fully offline).")
        intro.setWordWrap(True); v.addWidget(intro)
        v.addWidget(QLabel("Display template. Variables: {stt}"))
        v.addWidget(self._bind(QLineEdit(), "sttDisplay", "text"))
        f = QFormLayout(); self._stt_form = f
        self.stt_model_combo = QComboBox()  # only downloaded models
        f.addRow("Model", self.stt_model_combo)
        self.stt_device_combo = QComboBox(); self.stt_device_combo.addItems(["CPU", "GPU (CUDA)"])
        f.addRow("Processing", self.stt_device_combo)
        self.stt_cuda_btn = QPushButton("Download GPU libraries (CUDA)")
        self.stt_cuda_btn.clicked.connect(self._download_cuda)
        f.addRow("GPU support", self.stt_cuda_btn)
        get_row = QHBoxLayout()
        self.stt_get_combo = QComboBox()  # models available to download
        self.stt_get_btn = QPushButton("Download")
        self.stt_get_btn.clicked.connect(self._download_model)
        get_row.addWidget(self.stt_get_combo, 1); get_row.addWidget(self.stt_get_btn)
        gw = QWidget(); gw.setLayout(get_row)
        f.addRow("Get model", gw)
        mic_row = QHBoxLayout()
        self.stt_mic_combo = QComboBox()
        self.stt_mic_combo.addItem("Default")
        self.stt_mic_combo.addItems(stt.list_input_devices())
        mic_refresh = QPushButton("↻"); mic_refresh.setFixedWidth(34)
        mic_refresh.clicked.connect(self._refresh_mic_list)
        mic_row.addWidget(self.stt_mic_combo, 1); mic_row.addWidget(mic_refresh)
        mw = QWidget(); mw.setLayout(mic_row); f.addRow("Microphone", mw)
        self.stt_lang_combo = QComboBox()
        for disp, code in STT_LANGUAGES:
            self.stt_lang_combo.addItem(disp, code)
        f.addRow("Language", self.stt_lang_combo)
        self.stt_target_combo = QComboBox()
        self.stt_target_combo.addItem("Off (transcribe as spoken)", "")
        for disp, code in STT_LANGUAGES:
            if code != "auto":
                self.stt_target_combo.addItem(disp, code)
        f.addRow("Translate to", self.stt_target_combo)
        v.addLayout(f)
        v.addWidget(QLabel("Mic sensitivity (lower picks up quieter speech)"))
        grow = QHBoxLayout()
        gate = QSlider(Qt.Orientation.Horizontal); gate.setRange(0, 50)
        self.stt_gate_value = QLabel("0.02")
        gate.valueChanged.connect(lambda val: self.stt_gate_value.setText(f"{val / 100:.2f}"))
        grow.addWidget(self._bind(gate, "sttNoiseGate", "slider100")); grow.addWidget(self.stt_gate_value)
        v.addLayout(grow)
        v.addWidget(QLabel("Silence before a phrase ends (ms)"))
        srow = QHBoxLayout()
        sil = QSlider(Qt.Orientation.Horizontal); sil.setRange(300, 3000); sil.setSingleStep(100)
        self.stt_sil_value = QLabel("1500 ms")
        sil.valueChanged.connect(lambda val: self.stt_sil_value.setText(f"{val} ms"))
        srow.addWidget(self._bind(sil, "sttSilenceMs", "int")); srow.addWidget(self.stt_sil_value)
        v.addLayout(srow)
        v.addWidget(QLabel("Keep a transcription shown for (seconds)"))
        hrow = QHBoxLayout()
        hold = QSlider(Qt.Orientation.Horizontal); hold.setRange(2, 30)
        self.stt_hold_value = QLabel("8 s")
        hold.valueChanged.connect(lambda val: self.stt_hold_value.setText(f"{val} s"))
        hrow.addWidget(self._bind(hold, "sttHoldSeconds", "int")); hrow.addWidget(self.stt_hold_value)
        v.addLayout(hrow)
        self.stt_dl_status = QLabel("")
        v.addWidget(self.stt_dl_status)
        self.stt_progress = QProgressBar()
        self.stt_progress.setTextVisible(False)
        self.stt_progress.setVisible(False)
        v.addWidget(self.stt_progress)
        self.stt_status = QLabel("Idle"); v.addWidget(self.stt_status)
        v.addStretch(1)
        return w

    def _refresh_model_lists(self):
        cached = stt.cached_models()
        cur = self.settings.whisperModel
        self.stt_model_combo.blockSignals(True)
        self.stt_model_combo.clear()
        self.stt_model_combo.addItems(cached)
        if cur in cached:
            self.stt_model_combo.setCurrentText(cur)
        self.stt_model_combo.blockSignals(False)
        self.stt_get_combo.blockSignals(True)
        self.stt_get_combo.clear()
        self.stt_get_combo.addItems([m for m in stt.MODELS if m not in cached])
        self.stt_get_combo.blockSignals(False)

    def _refresh_cuda_ui(self):
        """Hide the CUDA download button once libraries exist; disable the GPU
        Processing option until they do."""
        present = stt.cuda_libraries_present()
        if hasattr(self, "stt_cuda_btn"):
            # Hide the whole form row (label + button), not just the button.
            try:
                self._stt_form.setRowVisible(self.stt_cuda_btn, not present)
            except Exception:
                self.stt_cuda_btn.setVisible(not present)
                lbl = self._stt_form.labelForField(self.stt_cuda_btn)
                if lbl is not None:
                    lbl.setVisible(not present)
        item = self.stt_device_combo.model().item(1)  # 0 = CPU, 1 = GPU (CUDA)
        if item is not None:
            item.setEnabled(present)
            item.setText("GPU (CUDA)" if present else "GPU (CUDA) — download libraries first")
            if present:
                item.setData(None, Qt.ItemDataRole.ForegroundRole)
            else:
                item.setForeground(QBrush(QColor(140, 140, 140)))  # greyed
        if not present and self.stt_device_combo.currentText().startswith("GPU"):
            self.stt_device_combo.setCurrentIndex(0)  # back to CPU

    def _download_model(self):
        name = self.stt_get_combo.currentText()
        if not name:
            return
        self.stt_get_btn.setEnabled(False)
        self.stt_dl_status.setText(f"Starting download of {name}...")
        def worker():
            try:
                stt.download(name, self.stt_dl_progress.emit)
                self.stt_dl_done.emit(name)
            except Exception as e:
                self.stt_dl_progress.emit(f"Download failed: {e}")
                self.stt_dl_done.emit("")
        threading.Thread(target=worker, daemon=True).start()

    def _download_cuda(self):
        if stt.cuda_libraries_present():
            if QMessageBox.question(
                    self, "CUDA",
                    "CUDA libraries already appear to be available. Download again?"
                    ) != QMessageBox.StandardButton.Yes:
                return
        if QMessageBox.question(
                self, "Download GPU libraries",
                "This downloads ~1.3 GB of NVIDIA CUDA 12 + cuDNN 9 libraries to enable "
                "GPU speech-to-text. You need an NVIDIA GPU. Continue?"
                ) != QMessageBox.StandardButton.Yes:
            return
        self.stt_cuda_btn.setEnabled(False)
        self.stt_dl_status.setText("Preparing CUDA download...")
        def worker():
            try:
                stt.download_cuda_libraries(self.stt_dl_progress.emit)
                self.stt_cuda_done.emit("ok")
            except Exception as e:
                self.stt_dl_progress.emit(f"CUDA download failed: {e}")
                self.stt_cuda_done.emit("")
        threading.Thread(target=worker, daemon=True).start()

    def _on_cuda_done(self, ok: str):
        self.stt_cuda_btn.setEnabled(True)
        self.stt_progress.setVisible(False)
        if ok:
            stt.reset_cuda_probe()  # let GPU be used this session without a restart
            self.stt_dl_status.setText("CUDA installed - set Processing to GPU and Apply.")
            self._refresh_cuda_ui()
            QMessageBox.information(
                self, "CUDA installed",
                "CUDA libraries installed. Set Processing to GPU (CUDA) and press Apply to "
                "use GPU acceleration.")

    def _on_stt_download_done(self, name: str):
        self.stt_get_btn.setEnabled(True)
        self.stt_progress.setVisible(False)
        self._refresh_model_lists()
        if name:
            self.stt_dl_status.setText(f"{name} ready.")
            if self.stt_model_combo.findText(name) >= 0:
                self.stt_model_combo.setCurrentText(name)
                self.settings.whisperModel = name

    def _refresh_mic_list(self):
        cur = self.stt_mic_combo.currentText()
        self.stt_mic_combo.blockSignals(True)
        self.stt_mic_combo.clear()
        self.stt_mic_combo.addItem("Default")
        self.stt_mic_combo.addItems(stt.list_input_devices())
        if self.stt_mic_combo.findText(cur) >= 0:
            self.stt_mic_combo.setCurrentText(cur)
        self.stt_mic_combo.blockSignals(False)

    def _mute_sub(self) -> QWidget:
        w = QWidget(); f = QFormLayout(w)
        f.addRow(QLabel("Template to use for Mute Toggle display"))
        f.addRow("Muted", self._bind(QLineEdit(), "mutedDisplay", "text"))
        f.addRow("Unmuted", self._bind(QLineEdit(), "unmutedDisplay", "text"))
        return w

    def _divider_sub(self) -> QWidget:
        w = QWidget(); f = QFormLayout(w)
        f.addRow("Top Divider", self._bind(QLineEdit(), "topBar", "text"))
        f.addRow("Middle Divider", self._bind(QLineEdit(), "middleBar", "text"))
        f.addRow("Bottom Divider", self._bind(QLineEdit(), "bottomBar", "text"))
        f.addRow("Vertical Divider", self._bind(QLineEdit(), "verticalDivider", "text"))
        f.addRow("", self._bind(QCheckBox("Remove outside dividers"), "hideOutside", "check"))
        return w

    def _timer_sub(self) -> QWidget:
        w = QWidget(); v = QVBoxLayout(w)
        v.addWidget(QLabel("Timer display template. Variables: {hours}, {minutes}, {seconds}"))
        v.addWidget(self._bind(QLineEdit(), "timerDisplay", "text"))
        cur = QHBoxLayout(); cur.addWidget(QLabel("Current Remaining Time:"))
        self.current_timer = QLabel("00:00:00"); cur.addWidget(self.current_timer); cur.addStretch(1); v.addLayout(cur)
        v.addWidget(QLabel("Add Time:"))
        self.add_hours = QLineEdit(); self.add_minutes = QLineEdit(); self.add_seconds = QLineEdit()
        for label, edit, unit in [("Hours", self.add_hours, 3600), ("Minutes", self.add_minutes, 60), ("Seconds", self.add_seconds, 1)]:
            r = QHBoxLayout(); r.addWidget(QLabel(label)); r.addWidget(edit)
            b = QPushButton("Add"); b.clicked.connect(lambda _=False, e=edit, u=unit: self._add_timer(e, u))
            r.addWidget(b); r.addStretch(1); v.addLayout(r)
        reset_btn = QPushButton("Reset Timer"); reset_btn.clicked.connect(self._reset_timer)
        v.addWidget(reset_btn); v.addStretch(1)
        return w

    # --------------------------------------------------------------- other bodies
    def _preview_body(self) -> QWidget:
        w = QWidget(); v = QVBoxLayout(w)
        self.sent_countdown = QLabel(""); v.addWidget(self.sent_countdown)
        self.preview_label = QLabel("")
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setWordWrap(True)
        v.addWidget(self.preview_label, 1)
        return w

    def _options_body(self) -> QWidget:
        w = QWidget(); outer = QVBoxLayout(w)
        general = QGroupBox("General"); g = QVBoxLayout(general)
        g.addWidget(self._bind(QCheckBox("Minimize on startup"), "minimizeOnStart", "check"))
        g.addWidget(self._bind(QCheckBox("Show update prompt"), "updatePrompt", "check"))
        g.addWidget(self._bind(QCheckBox("Show song info on bottom ribbon"), "showSongInfo", "check"))
        outer.addWidget(general)

        theme_box = QGroupBox("Theme"); tl = QVBoxLayout(theme_box)
        row = QHBoxLayout(); row.addWidget(QLabel("Theme"))
        self.theme_combo = QComboBox(); self.theme_combo.addItems(themes.theme_names())
        row.addWidget(self.theme_combo); row.addStretch(1); tl.addLayout(row)
        tl.addWidget(QLabel("Custom colours (used when Theme = Custom, then Apply):"))
        labels = {
            "bgColor": "Background", "accentColor": "Accent", "fontColor": "Font",
            "buttonColor": "Button", "scrollbarColor": "Scrollbar",
            "scrollbarBackgroundColor": "Scrollbar background",
            "tabBackgroundColor": "Tab background", "tabTextColor": "Tab text",
        }
        for key in themes.COLOR_KEYS:
            r = QHBoxLayout(); r.addWidget(QLabel(labels[key])); r.addStretch(1)
            hexlbl = QLabel(); self._hexlabels[key] = hexlbl
            swatch = QLabel(); swatch.setFixedSize(30, 18); self._swatches[key] = swatch
            pick = QPushButton("Pick"); pick.clicked.connect(lambda _=False, k=key: self._pick_color(k))
            r.addWidget(hexlbl); r.addWidget(swatch); r.addWidget(pick)
            tl.addLayout(r)
        reset_colors = QPushButton("Reset Colours to Selected Theme")
        reset_colors.clicked.connect(self._reset_custom_colors); tl.addWidget(reset_colors)
        outer.addWidget(theme_box)

        keys = QGroupBox("Keybindings (press Apply for changes to take effect)"); kf = QFormLayout(keys)
        self.keybind_run_label = QLabel(self.settings.keybind_run)
        run_btn = QPushButton("Bind Key"); run_btn.clicked.connect(lambda: self._bind_key("run"))
        rr = QHBoxLayout(); rr.addWidget(self.keybind_run_label); rr.addWidget(run_btn)
        rw = QWidget(); rw.setLayout(rr); kf.addRow("Toggle Run", rw)
        self.keybind_afk_label = QLabel(self.settings.keybind_afk)
        afk_btn = QPushButton("Bind Key"); afk_btn.clicked.connect(lambda: self._bind_key("afk"))
        ar = QHBoxLayout(); ar.addWidget(self.keybind_afk_label); ar.addWidget(afk_btn)
        aw = QWidget(); aw.setLayout(ar); kf.addRow("Toggle AFK", aw)
        kf.addRow("", self._bind(QCheckBox("Use AFK keybind (otherwise OSC checks AFK)"), "useAfkKeybind", "check"))
        outer.addWidget(keys); outer.addStretch(1)
        return w

    def _osc_body(self) -> QWidget:
        w = QWidget(); v = QVBoxLayout(w)
        note = QLabel("OCT receives avatar parameters (mute, AFK) automatically via "
                      "OSCQuery and coexists with other OSC apps - no setup needed. The "
                      "options below are advanced/fallback.")
        note.setWordWrap(True); v.addWidget(note)
        send = QGroupBox("OSC Send (to VRChat)"); sf = QFormLayout(send)
        sf.addRow("Address", self._bind(QLineEdit(), "oscSendAddress", "text"))
        sf.addRow("Port", self._bind(QLineEdit(), "oscSendPort", "text"))
        v.addWidget(send)
        listen = QGroupBox("OSC Listen (fallback - used only if OSCQuery is unavailable)")
        lf = QFormLayout(listen)
        lf.addRow("Address", self._bind(QLineEdit(), "oscListenAddress", "text"))
        lf.addRow("Port", self._bind(QLineEdit(), "oscListenPort", "text"))
        v.addWidget(listen)
        relay = QGroupBox("OSC Relay (forward received params to another PC/device)")
        rf = QFormLayout(relay)
        rf.addRow("", self._bind(QCheckBox("Enable relay"), "oscForeword", "check"))
        rf.addRow("Address", self._bind(QLineEdit(), "oscForewordAddress", "text"))
        rf.addRow("Port", self._bind(QLineEdit(), "oscForewordPort", "text"))
        v.addWidget(relay)
        dbg = QGroupBox("Avatar Debugging"); df = QFormLayout(dbg)
        self.debug_path = QLineEdit()
        df.addRow("Path", self.debug_path)
        valrow = QHBoxLayout()
        self.debug_value = QLineEdit()
        self.debug_type = QComboBox(); self.debug_type.addItems(["int", "float", "bool", "str"])
        valrow.addWidget(self.debug_value); valrow.addWidget(self.debug_type)
        vw = QWidget(); vw.setLayout(valrow); df.addRow("Value", vw)
        send_btn = QPushButton("Send"); send_btn.clicked.connect(self._send_debug)
        df.addRow("", send_btn)
        v.addWidget(dbg)
        v.addStretch(1)
        return w

    def _output_body(self) -> QWidget:
        w = QWidget(); v = QVBoxLayout(w)
        v.addWidget(self._bind(QCheckBox("Log to file (OCT_debug_log.txt)"), "logOutput", "check"))
        self.output_view = QPlainTextEdit(); self.output_view.setReadOnly(True)
        v.addWidget(self.output_view, 1)
        return w

    def _help_body(self) -> QWidget:
        w = QWidget(); v = QVBoxLayout(w)
        for label, url in [
            ("FAQ / Wiki", "https://github.com/Lioncat6/OSC-Chat-Tools/wiki"),
            ("Report an Issue", "https://github.com/Lioncat6/OSC-Chat-Tools/issues"),
            ("GitHub", "https://github.com/Lioncat6/OSC-Chat-Tools"),
        ]:
            b = QPushButton(label); b.clicked.connect(lambda _=False, u=url: webbrowser.open(u)); v.addWidget(b)
        v.addStretch(1)
        return w

    # ---------------------------------------------------------- layout editor
    def _parse_layout(self) -> list[list[str]]:
        return [[m.group(1), m.group(2)] for m in _TOKEN_RE.finditer(self.settings.layoutString or "")]

    def _write_layout(self, elements: list[list[str]]):
        self.settings.layoutString = "".join(f"{{{n}({d})}}" for n, d in elements)
        self._syncing = True
        self.layout_manual.setPlainText(self.settings.layoutString)
        self._syncing = False
        self._refresh_layout_editor()

    def _add_element(self, token: str):
        elements = self._parse_layout(); elements.append([token, "0"]); self._write_layout(elements)
        self._go("layout")

    def _on_manual_layout_changed(self):
        if self._syncing:
            return
        self.settings.layoutString = self.layout_manual.toPlainText()
        self._refresh_layout_editor()

    def _refresh_layout_editor(self):
        while self._editor_layout.count() > 1:
            item = self._editor_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._row_widgets = {}
        elements = self._parse_layout()
        for i, (name, digit) in enumerate(elements):
            row = QWidget(); h = QHBoxLayout(row); h.setContentsMargins(0, 0, 0, 0)
            delete = QPushButton("❌"); delete.setFixedWidth(34)
            delete.clicked.connect(lambda _=False, idx=i: self._del_element(idx))
            up = QPushButton("⬆️"); up.setFixedWidth(34); up.setEnabled(i > 0)
            up.clicked.connect(lambda _=False, idx=i: self._move_element(idx, -1))
            down = QPushButton("⬇️"); down.setFixedWidth(34); down.setEnabled(i < len(elements) - 1)
            down.clicked.connect(lambda _=False, idx=i: self._move_element(idx, 1))
            label = QLabel(ELEMENT_LABELS.get(name, name))
            div = QCheckBox("┋"); div.setChecked(digit in ("1", "3"))
            div.toggled.connect(lambda _=False, idx=i: self._toggle_sep(idx))
            nl = QCheckBox("⤷"); nl.setChecked(digit in ("2", "3"))
            nl.toggled.connect(lambda _=False, idx=i: self._toggle_sep(idx))
            self._row_widgets[i] = (div, nl)
            for x in (delete, up, down, label):
                h.addWidget(x)
            h.addStretch(1); h.addWidget(div); h.addWidget(nl)
            self._editor_layout.insertWidget(self._editor_layout.count() - 1, row)

    def _del_element(self, idx: int):
        elements = self._parse_layout()
        if 0 <= idx < len(elements):
            elements.pop(idx); self._write_layout(elements)

    def _move_element(self, idx: int, delta: int):
        elements = self._parse_layout(); j = idx + delta
        if 0 <= idx < len(elements) and 0 <= j < len(elements):
            elements[idx], elements[j] = elements[j], elements[idx]; self._write_layout(elements)

    def _toggle_sep(self, idx: int):
        div, nl = self._row_widgets.get(idx, (None, None))
        if div is None:
            return
        digit = {(False, False): "0", (True, False): "1", (False, True): "2", (True, True): "3"}[(div.isChecked(), nl.isChecked())]
        elements = self._parse_layout()
        if 0 <= idx < len(elements):
            elements[idx][1] = digit; self._write_layout(elements)

    # ---------------------------------------------------------------- behaviour
    def log(self, text: str):
        self.output_view.appendPlainText(text)
        if self.settings.logOutput:
            try:
                with open(CONFIG_PATH.with_name("OCT_debug_log.txt"), "a", encoding="utf-8") as fh:
                    fh.write(f"{datetime.now()} {text}\n")
            except Exception:
                pass

    def _apply_settings_to_ui(self):
        for widget, field, kind in self._bindings:
            value = getattr(self.settings, field)
            if kind == "check":
                widget.setChecked(bool(value))
            elif kind == "text":
                widget.setText(str(value))
            elif kind == "multiline":
                widget.setPlainText(str(value))
            elif kind == "float":
                widget.setValue(float(value))
            elif kind == "int":
                widget.setValue(int(value))
            elif kind == "slider10":
                widget.setValue(int(round(float(value) * 10)))
            elif kind == "slider100":
                widget.setValue(int(round(float(value) * 100)))
        self.src_spotify.setChecked(self.settings.useSpotifyApi)
        self.src_media.setChecked(not self.settings.useSpotifyApi)
        self.theme_combo.setCurrentText(self.settings.selectedTheme)
        self.stt_device_combo.setCurrentText("GPU (CUDA)" if self.settings.sttDevice == "cuda" else "CPU")
        lang_idx = self.stt_lang_combo.findData(self.settings.sttLanguage or "en")
        self.stt_lang_combo.setCurrentIndex(lang_idx if lang_idx >= 0 else self.stt_lang_combo.findData("en"))
        tgt_idx = self.stt_target_combo.findData(self.settings.sttTarget or "")
        self.stt_target_combo.setCurrentIndex(tgt_idx if tgt_idx >= 0 else 0)
        self._refresh_cuda_ui()
        mic = self.settings.micDevice or "Default"
        if self.stt_mic_combo.findText(mic) < 0:
            self.stt_mic_combo.addItem(mic)
        self.stt_mic_combo.setCurrentText(mic)
        self._custom_colors = dict(self.settings.customColors)
        self._refresh_swatches()
        self._syncing = True
        self.layout_manual.setPlainText(self.settings.layoutString)
        self._syncing = False
        self._refresh_layout_editor()
        self.file_label.setText(self.settings.FileToRead)
        self.keybind_run_label.setText(self.settings.keybind_run)
        self.keybind_afk_label.setText(self.settings.keybind_afk)

    def _collect_settings_from_ui(self):
        for widget, field, kind in self._bindings:
            if kind == "check":
                setattr(self.settings, field, widget.isChecked())
            elif kind == "text":
                setattr(self.settings, field, widget.text())
            elif kind == "multiline":
                setattr(self.settings, field, widget.toPlainText())
            elif kind in ("float", "int"):
                setattr(self.settings, field, widget.value())
            elif kind == "slider10":
                setattr(self.settings, field, widget.value() / 10)
            elif kind == "slider100":
                setattr(self.settings, field, widget.value() / 100)
        self.settings.useSpotifyApi = self.src_spotify.isChecked()
        self.settings.useMediaManager = self.src_media.isChecked()
        self.settings.selectedTheme = self.theme_combo.currentText()
        if self.stt_model_combo.currentText():  # keep configured model if none downloaded yet
            self.settings.whisperModel = self.stt_model_combo.currentText()
        self.settings.sttDevice = "cuda" if self.stt_device_combo.currentText().startswith("GPU") else "cpu"
        mic = self.stt_mic_combo.currentText()
        self.settings.micDevice = "" if mic == "Default" else mic
        self.settings.sttLanguage = self.stt_lang_combo.currentData() or "en"
        self.settings.sttTarget = self.stt_target_combo.currentData() or ""
        self.settings.customColors = dict(self._custom_colors)
        self.settings.keybind_run = self.keybind_run_label.text()
        self.settings.keybind_afk = self.keybind_afk_label.text()

    def _refresh_swatches(self):
        for key, swatch in self._swatches.items():
            hexv = themes.normalize_color(self._custom_colors.get(key, "#000000"))
            swatch.setStyleSheet(f"background-color: {hexv}; border: 1px solid #888888; border-radius: 3px;")
            if key in self._hexlabels:
                self._hexlabels[key].setText(hexv)

    def _pick_color(self, key: str):
        start = QColor(themes.normalize_color(self._custom_colors.get(key, "#000000")))
        chosen = QColorDialog.getColor(start, self, f"Pick {key}")
        if chosen.isValid():
            self._custom_colors[key] = chosen.name(); self._refresh_swatches()

    def _reset_custom_colors(self):
        base = themes.resolve_colors(self.theme_combo.currentText(), self._custom_colors)
        self._custom_colors = dict(base); self._refresh_swatches()

    def _apply_theme(self):
        self.setStyleSheet(themes.build_qss(self.settings.selectedTheme, self.settings.customColors))

    def _on_apply(self):
        self._collect_settings_from_ui()
        save_settings(self.settings)
        self._apply_theme()
        self._refresh_hr()
        self._refresh_osc_listener()
        self._refresh_keybinds()
        self._refresh_stt()
        self.log("Settings applied.")

    def _on_reset(self):
        if QMessageBox.question(self, "Reset", "Reset all settings to defaults?") != QMessageBox.StandardButton.Yes:
            return
        self.settings = Settings(); self._apply_settings_to_ui(); self._apply_theme()
        self.log("Settings reset to defaults (Apply to save).")

    # ------------------------------------------------------------- small actions
    def _pick_text_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select a File")
        if path:
            self.settings.FileToRead = path; self.file_label.setText(path)

    def _bind_key(self, which: str):
        current = self.settings.keybind_run if which == "run" else self.settings.keybind_afk
        text, ok = QInputDialog.getText(self, "Bind Key", "Enter key (e.g. ` or end):", text=current)
        if ok and text:
            (self.keybind_run_label if which == "run" else self.keybind_afk_label).setText(text)

    def _add_timer(self, edit: QLineEdit, unit_seconds: int):
        import time as _t
        try:
            amount = int(edit.text())
        except ValueError:
            return
        if self.settings.timerEndStamp < int(_t.time() * 1000):
            self.settings.timerEndStamp = int(_t.time() * 1000)
        self.settings.timerEndStamp += amount * unit_seconds * 1000
        edit.clear()

    def _reset_timer(self):
        import time as _t
        self.settings.timerEndStamp = int(_t.time() * 1000)
        self.current_timer.setText("00:00:00")

    def _open_config(self):
        self._open_file_os(str(CONFIG_PATH))

    def _open_log(self):
        self._open_file_os(str(CONFIG_PATH.with_name("OCT_debug_log.txt")))

    @staticmethod
    def _open_file_os(path: str):
        import os, sys, subprocess
        try:
            if sys.platform.startswith("win"):
                os.startfile(path)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception:
            pass

    def _send_debug(self):
        path = self.debug_path.text().strip()
        if not path:
            return
        raw = self.debug_value.text()
        t = self.debug_type.currentText()
        try:
            if t == "int":
                value: object = int(raw)
            elif t == "float":
                value = float(raw)
            elif t == "bool":
                value = raw.strip().lower() in ("1", "true", "yes", "on")
            else:
                value = raw
        except ValueError:
            self.log(f"Debug value '{raw}' is not a valid {t}.")
            return
        try:
            OSCClient(self.settings.oscSendAddress, self.settings.oscSendPort).send(path, value)
            self.log(f"Sent {path} = {value!r} ({t})")
        except Exception as e:
            self.log(f"Debug send error: {e}")

    def _about(self):
        QMessageBox.information(self, "About", f"OSC Chat Tools (rewrite) {__version__}\nby Lioncat6")

    def _run_update_check(self, force_popup: bool):
        import threading
        def worker():
            latest, ood = updater.check_for_update(__version__.split("-")[0])
            if latest is None:
                self.update_result.emit("Update check failed.", force_popup, "")
            elif ood:
                self.update_result.emit(
                    f"A new version is available: {latest}",
                    force_popup or self.settings.updatePrompt,
                    "https://github.com/Lioncat6/OSC-Chat-Tools/releases",
                )
            else:
                self.update_result.emit(f"Program is up to date! Version {__version__}", force_popup, "")
        threading.Thread(target=worker, daemon=True).start()

    def _check_updates(self):
        self._run_update_check(force_popup=True)

    def _show_update_result(self, message: str, popup: bool, url: str = ""):
        self.log(message)
        if not popup:
            return
        if url:
            box = QMessageBox(self)
            box.setWindowTitle("Update Available")
            box.setText(message)
            open_btn = box.addButton("Open Release", QMessageBox.ButtonRole.AcceptRole)
            box.addButton("Close", QMessageBox.ButtonRole.RejectRole)
            box.exec()
            if box.clickedButton() == open_btn:
                webbrowser.open(url)
        else:
            QMessageBox.information(self, "Update", message)

    # --------------------------------------------------------------- run / send
    def _on_run_toggled(self, checked: bool):
        self._running = checked
        if checked:
            self._collect_settings_from_ui()  # run with what's currently on screen
            try:
                self._osc = OSCClient(self.settings.oscSendAddress, self.settings.oscSendPort)
                self._last_sent = None
                self._sent_elapsed = 0.0
                self._cycle_accum = 0.0
                self._skipped = False
                self._frame_index = 0
                self.log("Run on.")
            except Exception as e:
                self.log(f"OSC error: {e}"); self._running = False
        else:
            # sendBlank: clear the chatbox when stopping (not every cycle).
            if self.settings.sendBlank and self._osc is not None:
                try:
                    self._osc.send_chatbox("", send_now=True)
                except Exception:
                    pass
            self.log("Run off.")
        self._refresh_hr()
        self._refresh_osc_listener()
        self._refresh_keybinds()
        self._refresh_stt()

    def _message_frames(self) -> list[str]:
        """Split the message into frames (one per line); '*' alone is a blank frame."""
        msg = self.settings.messageString
        return msg.split("\n") if msg != "" else [""]

    def _next_frame(self) -> str:
        """Return the current frame and advance to the next (cycling)."""
        frames = self._message_frames()
        if self._frame_index >= len(frames):
            self._frame_index = 0
        frame = frames[self._frame_index]
        self._frame_index = (self._frame_index + 1) % len(frames)
        return "" if frame == "*" else frame

    def _current_frame(self) -> str:
        """The current frame without advancing (for static previews)."""
        frames = self._message_frames()
        frame = frames[self._frame_index % len(frames)]
        return "" if frame == "*" else frame

    def _stop_hr(self):
        if self._hr_provider is not None:
            self._hr_provider.stop()
            self._hr_provider = None
        self._stop_beat()

    def _start_hr(self):
        self._stop_hr()
        s = self.settings
        if self._osc is None:
            try:
                self._osc = OSCClient(s.oscSendAddress, s.oscSendPort)
            except Exception:
                self._osc = None
        if s.useHypeRate and s.hypeRateKey:
            self._hr_provider = heartrate.HypeRateProvider(s.hypeRateKey, s.hypeRateSessionId, self._on_bpm)
            self.log("Connecting to HypeRate...")
        elif s.usePulsoid and s.pulsoidToken:
            self._hr_provider = heartrate.PulsoidProvider(s.pulsoidToken, self._on_bpm)
            self.log("Connecting to Pulsoid...")
        else:
            return
        self._hr_provider.start()
        self._start_beat()

    def _hr_signature(self):
        s = self.settings
        if s.useHypeRate:
            return ("hyperate", s.hypeRateKey, s.hypeRateSessionId)
        if s.usePulsoid:
            return ("pulsoid", s.pulsoidToken)
        return None

    def _refresh_hr(self):
        s = self.settings
        want = ((s.usePulsoid or s.useHypeRate)
                and ("{hr" in (s.layoutString or "") or s.avatarHR)
                and (self._running or s.avatarHR))
        sig = self._hr_signature()
        # Only (re)connect when the source/token actually changed.
        if want and (self._hr_provider is None or self._hr_signature_current != sig):
            self._start_hr()
            self._hr_signature_current = sig
        elif not want:
            self._stop_hr()
            self._hr_signature_current = None

    def _on_bpm(self, bpm: int):
        self._heart_rate = bpm
        if self._osc is not None:
            try:
                self._osc.send("/avatar/parameters/isHRActive", True)
                self._osc.send("/avatar/parameters/isHRConnected", True)
                self._osc.send("/avatar/parameters/HR", int(bpm))
            except Exception:
                pass

    def _ensure_osc(self):
        if self._osc is None:
            try:
                self._osc = OSCClient(self.settings.oscSendAddress, self.settings.oscSendPort)
            except Exception:
                self._osc = None
        return self._osc

    def _send_time_params(self):
        if not self.settings.useTimeParameters:
            return
        client = self._ensure_osc()
        if client is None:
            return
        now = datetime.now()
        is_pm = now.hour >= 12
        h12 = now.hour % 12 or 12
        try:
            client.send("/avatar/parameters/Hours", int(h12))
            client.send("/avatar/parameters/Minutes", int(now.minute))
            client.send("/avatar/parameters/Seconds", int(now.second))
            client.send("/avatar/parameters/Period", bool(is_pm))
        except Exception:
            pass

    def _start_beat(self):
        if self._beat_thread is not None and self._beat_thread.is_alive():
            return
        self._beat_running = True
        self._beat_thread = threading.Thread(target=self._beat_loop, daemon=True)
        self._beat_thread.start()

    def _stop_beat(self):
        self._beat_running = False

    def _beat_loop(self):
        # Pulse the avatar HR-beat parameter in time with the heart rate.
        while self._beat_running:
            if self.settings.toggleBeat and self._osc is not None:
                try:
                    self._osc.send("/avatar/parameters/isHRBeat", True)
                    time.sleep(0.1)
                    self._osc.send("/avatar/parameters/isHRBeat", False)
                except Exception:
                    pass
                if self.settings.blinkOverride:
                    time.sleep(max(0.0, self.settings.blinkSpeed))
                hr = self._heart_rate or 0
                if hr <= 0:
                    hr = 1
                interval = 60 / hr
                time.sleep(1 if interval > 5 else interval)
            else:
                time.sleep(0.5)

    # ---------------------------------------------------------- OSC listen
    def _stop_osc_listener(self):
        if self._osc_listener is not None:
            self._osc_listener.stop()
            self._osc_listener = None

    def _refresh_osc_listener(self):
        s = self.settings
        # Listen whenever something needs incoming avatar parameters: the mute
        # element, automatic AFK detection (not the AFK keybind), or an explicit
        # OSC Listen/Forward option. This makes mute/AFK work without the user
        # having to dig into OSC Options.
        want = (s.oscListen or s.oscForeword
                or "{mute" in (s.layoutString or "")
                or not s.useAfkKeybind)
        sig = (want, s.oscListenAddress, s.oscListenPort,
               s.oscForeword, s.oscForewordAddress, s.oscForewordPort)
        if sig == self._osc_sig:
            return  # nothing relevant changed; leave the listener running
        self._osc_sig = sig
        self._stop_osc_listener()
        if not want:
            return
        forward = None
        if s.oscForeword:  # relay received params on to another device
            try:
                forward = OSCClient(s.oscForewordAddress, s.oscForewordPort)
            except Exception:
                forward = None
        try:
            self._osc_listener = OSCListener(
                s.oscListenAddress, s.oscListenPort,
                on_message=self._on_osc_message, forward_client=forward,
            )
            self._osc_listener.start()
            how = "via OSCQuery" if self._osc_listener.via_oscquery else "on"
            self.log(f"OSC listening {how} {s.oscListenAddress}:{self._osc_listener.port}")
        except Exception as e:
            self.log(f"OSC listen error: {e}")
            self._osc_listener = None

    def _on_osc_message(self, addr: str, args: tuple):
        val = args[0] if args else None
        if addr.endswith("/MuteSelf"):
            self._osc_mute = bool(val)
        elif addr.endswith("/AFK"):
            self._osc_afk = bool(val)

    # ---------------------------------------------------------- keybinds
    def _refresh_keybinds(self):
        self._keybinds.unbind_all()
        s = self.settings
        if s.keybind_run:
            self._keybinds.bind("run", s.keybind_run, self.run_toggle_signal.emit)
        if s.keybind_afk:
            self._keybinds.bind("afk", s.keybind_afk, self.afk_toggle_signal.emit)

    # ---------------------------------------------------------- speech to text
    def _stt_signature(self):
        s = self.settings
        return (s.whisperModel, s.micDevice, s.sttLanguage,
                round(s.sttNoiseGate, 4), int(s.sttSilenceMs), s.sttDevice, s.sttTarget)

    def _stop_stt(self):
        if self._stt is not None:
            self._stt.stop()
            self._stt = None

    def _start_stt(self):
        self._stop_stt()
        import gc
        gc.collect()  # free the previous ctranslate2 model before loading the next
        s = self.settings
        self._stt = stt.SpeechToText(
            model_name=s.whisperModel, device_name=s.micDevice, language=s.sttLanguage,
            noise_gate=s.sttNoiseGate, silence_ms=s.sttSilenceMs, device_pref=s.sttDevice,
            target=s.sttTarget,
            on_text=self.stt_text_signal.emit, on_status=self.stt_status_signal.emit)
        self._stt.start()

    def _refresh_stt(self):
        s = self.settings
        want = ("{stt" in (s.layoutString or "")) and self._running
        sig = self._stt_signature()
        if want and (self._stt is None or self._stt_sig != sig):
            self._start_stt()
            self._stt_sig = sig
        elif not want:
            self._stop_stt()
            self._stt_sig = None

    def _on_stt_text(self, heard: str, output: str, is_final: bool):
        self._stt_text = output  # the (possibly translated) text the chatbox sends
        self._stt_text_at = time.monotonic()
        if hasattr(self, "stt_status"):
            prefix = "Heard: " if is_final else "… "
            if output != heard:
                self.stt_status.setText(f"{prefix}{heard[:50]}  →  {output[:50]}")
            else:
                self.stt_status.setText(f"{prefix}{heard[:60]}")
        if is_final:
            self.log(f"STT: {heard}" + (f"  →  {output}" if output != heard else ""))
            if self._running:
                self._send_stt_final()

    def _send_stt_final(self):
        # Push the finished phrase right away with the chatbox notification SFX
        # so people hear/see it, instead of waiting for the next gated cycle.
        try:
            message = build_message(self._build_context(self._current_frame()))
        except Exception:
            return
        self.preview_label.setText(message.replace("\v", "\n"))
        self._do_send(message, notify=True)

    def _on_stt_status(self, status: str):
        if hasattr(self, "stt_status"):
            self.stt_status.setText(status)
        self._update_stt_progress(status)
        self.log("STT: " + status)

    def _update_stt_progress(self, text: str):
        if not hasattr(self, "stt_progress"):
            return
        bar = self.stt_progress
        m = re.search(r"(\d+)\s*%", text)
        low = text.lower()
        if m:  # download with a real percentage -> determinate bar
            bar.setRange(0, 100); bar.setValue(int(m.group(1))); bar.setVisible(True)
        elif any(k in low for k in ("loading", "downloading", "warming", "starting", "constructing")):
            bar.setRange(0, 0); bar.setVisible(True)  # indeterminate "busy" animation
        else:  # listening / idle / ready / error / heard
            bar.setVisible(False)

    def _on_stt_dl_progress(self, text: str):
        self.stt_dl_status.setText(text)
        self._update_stt_progress(text)

    def _current_stt_text(self) -> str:
        if not self._stt_text:
            return ""
        if (time.monotonic() - self._stt_text_at) > max(1, self.settings.sttHoldSeconds):
            return ""
        return self._stt_text

    # ---------------------------------------------------------- Spotify
    def _on_spotify_status(self, msg: str):
        self.spotify_status.setText(msg)
        self.log("Spotify: " + msg)

    def _save_spotify_tokens(self, access: str, refresh: str):
        self.settings.spotifyAccessToken = access
        self.settings.spotifyRefreshToken = refresh
        try:
            save_settings(self.settings)
        except Exception:
            pass

    def _poll_spotify(self):
        s = self.settings
        if not s.useSpotifyApi:
            return
        if self._spotify is None:
            # Lazily create the client once Spotify API is on and tokens exist.
            if s.spotify_client_id and s.spotifyAccessToken and s.spotifyRefreshToken:
                self._spotify = spotify.SpotifyClient(
                    s.spotify_client_id, s.spotifyAccessToken, s.spotifyRefreshToken,
                    on_tokens=self._save_spotify_tokens)
            else:
                return
        if getattr(self, "_spotify_polling", False):
            return
        self._spotify_polling = True
        def worker():
            try:
                now = self._spotify.now_playing()
                had = self._spotify_now is not None
                self._spotify_now = now
                self._spotify_now_at = time.monotonic()
                if now and not had:
                    self.log_signal.emit("Spotify: now playing detected.")
                if now is None:
                    err = self._spotify.last_error or "no data"
                    if err != self._last_spotify_err:
                        self.log_signal.emit(f"Spotify: {err}")
                        if "403" in err or "not registered" in err.lower():
                            self.log_signal.emit(
                                "Spotify: the bundled Client ID isn't authorized for your "
                                "account. Create your own app (the ? next to Client ID), paste "
                                "its Client ID, Apply, and re-link.")
                        self._last_spotify_err = err
                else:
                    self._last_spotify_err = None
            finally:
                self._spotify_polling = False
        threading.Thread(target=worker, daemon=True).start()

    def _update_ribbon(self):
        if not hasattr(self, "ribbon_song"):
            return
        if not self.settings.showSongInfo:
            self.ribbon_song.setText("")
            return
        import html
        np = None
        url = ""
        has_dur = False
        if self.settings.useSpotifyApi and self._spotify_now:
            np = self._spotify_now
            url = np.get("url", "")
            has_dur = True
        elif self._media_now:
            np = self._media_now
        if not np or not np.get("title"):
            self.ribbon_song.setText("")
            return
        icon = "▶" if np.get("playing") else "⏸"
        title = html.escape(np.get("title", ""))
        artist = html.escape(np.get("artist", ""))
        title_html = f'<a href="{url}">{title}</a>' if url else title
        dur = f" 『{np.get('song_progress', '')}/{np.get('song_length', '')}』" if has_dur else ""
        self.ribbon_song.setText(f"{icon} {title_html} — {artist}{dur}")

    def _link_spotify_manual(self):
        cid = self._spotify_client_id_from_ui() or self.settings.spotify_client_id.strip()
        if not cid:
            QMessageBox.information(self, "Spotify", "Enter your Spotify Client ID first (see the ? guide).")
            return
        verifier, auth_url = spotify.build_manual_auth(cid)
        webbrowser.open_new(auth_url)
        code, ok = QInputDialog.getText(
            self, "Manual Spotify Link",
            "Authorize in your browser, then paste the 'code' value from the\n"
            "redirect URL (after ?code=) here:")
        if not ok or not code.strip():
            return
        self.spotify_status.setText("Linking...")
        def worker():
            try:
                access, refresh, name = spotify.exchange_code(cid, code.strip(), verifier)
                self.settings.spotifyAccessToken = access
                self.settings.spotifyRefreshToken = refresh
                save_settings(self.settings)
                self._spotify = spotify.SpotifyClient(cid, access, refresh, on_tokens=self._save_spotify_tokens)
                self.spotify_status_signal.emit(f"Linked: {name}" if name else "Linked!")
            except Exception as e:
                self.spotify_status_signal.emit(f"Link failed: {e}")
        threading.Thread(target=worker, daemon=True).start()

    def _spotify_client_id_from_ui(self) -> str:
        for widget, field, _kind in self._bindings:
            if field == "spotify_client_id":
                return widget.text().strip()
        return ""

    def _link_spotify(self):
        if getattr(self, "_linking", False):
            self.spotify_status.setText("Link already in progress — finish it in your browser.")
            return
        self._collect_settings_from_ui()
        cid = self.settings.spotify_client_id.strip()
        if not cid:
            QMessageBox.information(self, "Spotify", "Enter your Spotify Client ID first (see the ? guide).")
            return
        self._linking = True
        self.spotify_status.setText("Linking...")
        def worker():
            try:
                access, refresh, name = spotify.link(cid, on_log=self.spotify_status_signal.emit)
                self.settings.spotifyAccessToken = access
                self.settings.spotifyRefreshToken = refresh
                save_settings(self.settings)
                self._spotify = spotify.SpotifyClient(cid, access, refresh, on_tokens=self._save_spotify_tokens)
                self.spotify_status_signal.emit(f"Linked: {name}" if name else "Linked!")
            except Exception as e:
                msg = str(e)
                if "403" in msg or "registered" in msg.lower() or "verification" in msg.lower():
                    # Token doesn't work - don't leave stale tokens looking "linked".
                    self.settings.spotifyAccessToken = ""
                    self.settings.spotifyRefreshToken = ""
                    save_settings(self.settings)
                    self._spotify = None
                self.spotify_status_signal.emit(f"Link failed: {e}")
            finally:
                self._linking = False
        threading.Thread(target=worker, daemon=True).start()

    # ------------------------------------------------------ message modes
    def _afk_active(self) -> bool:
        if self.afk_check.isChecked():
            return True
        return (not self.settings.useAfkKeybind) and self._osc_afk

    def _special_mode_active(self) -> bool:
        return self._afk_active() or self.settings.scrollText

    def _afk_frame(self) -> str:
        frames = ["\vAFK\v", "\vㅤ\v"]
        f = frames[self._afk_index % len(frames)]
        self._afk_index = (self._afk_index + 1) % len(frames)
        return f

    def _scroll_chunk(self) -> str:
        try:
            with open(self.settings.FileToRead, "r", encoding="utf-8") as fh:
                text = fh.read()
        except Exception as e:
            self.log(f"Text file read error: {e}")
            return ""
        if not text:
            return ""
        chunk = text[self._scroll_pos:self._scroll_pos + 144]
        self._scroll_pos += 144
        if self._scroll_pos >= len(text):
            self._scroll_pos = 0
        return chunk

    def _compose_cycle_message(self) -> str:
        if self._afk_active():
            return self._afk_frame()
        if self.settings.scrollText:
            return self._scroll_chunk()
        return build_message(self._build_context(self._next_frame()))

    def _do_send(self, message: str, notify: bool = False):
        if self._osc is not None:
            try:
                self._osc.send_chatbox(message, send_now=True, notify=notify)
            except Exception as e:
                self.log(f"Send error: {e}")
        self._last_sent = message
        self._sent_elapsed = 0.0
        self._cycle_accum = 0.0
        self._skipped = False

    def _update_timer_label(self):
        import time as _t
        if not hasattr(self, "current_timer"):
            return
        ms = max(0, self.settings.timerEndStamp - int(_t.time() * 1000))
        self.current_timer.setText(f"{ms // 3600000:02d}:{(ms // 60000) % 60:02d}:{(ms // 1000) % 60:02d}")

    def closeEvent(self, event):
        self._stop_hr()
        self._stop_osc_listener()
        self._stop_stt()
        self._keybinds.unbind_all()
        super().closeEvent(event)

    def _build_context(self, message_text: str = "") -> ChatboxContext:
        import time as _t
        ctx = ChatboxContext(settings=self.settings, message_text=message_text)
        ctx.state = self._chatbox_state
        ctx.heart_rate = self._heart_rate
        ctx.muted = self._osc_mute
        ctx.play_seconds = vrc.play_seconds()
        ctx.timer_remaining_ms = max(0, self.settings.timerEndStamp - int(_t.time() * 1000))
        ctx.stt_text = self._current_stt_text()
        if self.settings.useSpotifyApi and self._spotify_now:
            ctx.song_source = "spotify"
            np = self._spotify_now
            ctx.song_title = np["title"]
            ctx.song_artist = np["artist"]
            ctx.song_album_title = np["album_title"]
            ctx.song_album_artist = np["album_artist"]
            # Interpolate progress between polls so the time ticks smoothly.
            prog_ms = np.get("progress_ms", 0)
            if np.get("playing"):
                prog_ms += (time.monotonic() - self._spotify_now_at) * 1000
            dur_ms = np.get("duration_ms", 0)
            if dur_ms:
                prog_ms = min(prog_ms, dur_ms)
            ctx.song_progress = f"{int(prog_ms // 60000)}:{int((prog_ms // 1000) % 60):02d}"
            ctx.song_length = f"{int(dur_ms // 60000)}:{int((dur_ms // 1000) % 60):02d}"
            ctx.song_volume = np["volume"]
            ctx.song_id = np["song_id"]
            ctx.song_playing = np["playing"]
        elif "{song" in (self.settings.layoutString or ""):
            # Windows Now Playing - also the fallback when Spotify has no active playback.
            ctx.song_source = "media"
            np = media.get_now_playing()
            self._media_now = np
            if np:
                ctx.song_title = np["title"]
                ctx.song_artist = np["artist"]
                ctx.song_album_title = np.get("album_title", "")
                ctx.song_album_artist = np.get("album_artist", "")
                ctx.song_playing = np["playing"]
        return ctx

    def _update_preview(self):
        try:
            self.preview_label.setText(
                build_message(self._build_context(self._current_frame())).replace("\v", "\n")
            )
        except Exception:
            pass

    def _tick(self):
        self._update_timer_label()
        self._update_ribbon()
        # Idle: static preview of the current frame.
        if not self._running:
            self.preview_label.setText(
                build_message(self._build_context(self._current_frame())).replace("\v", "\n")
            )
            return
        delay = max(0.1, self.settings.message_delay)
        self._sent_elapsed += 0.1
        self._cycle_accum += 0.1
        skip = " [Skipped Send]" if self._skipped else ""
        self.sent_countdown.setText(f"Last sent: {self._sent_elapsed:.1f}/{delay}{skip}")

        if self._cycle_accum + 1e-9 < delay:
            # sendASAP: push live-data changes early (normal mode only) without waiting.
            if self.settings.sendASAP and not self._special_mode_active():
                early = build_message(self._build_context(self._current_frame()))
                self.preview_label.setText(early.replace("\v", "\n"))
                if early != self._last_sent and not (self.settings.skipBlankSends and not early.strip()):
                    self._do_send(early)
            return

        self._cycle_accum = 0.0
        message = self._compose_cycle_message()
        self.preview_label.setText(message.replace("\v", "\n"))
        if self.settings.skipBlankSends and not message.strip():
            self._skipped = True
            return
        # Send on change, if not suppressing duplicates, or every ~30s (chatbox timeout).
        if (message != self._last_sent) or (not self.settings.suppressDuplicates) or (self._sent_elapsed > 30):
            self._do_send(message)
        else:
            self._skipped = True
