"""Control panel, the main window (Tactical Overlay shell).

A left nav rail + a QStackedWidget body: Overlays, Entity, Codex, Combat/DPS,
Speedrun, Collection, Map, Friends, Server, Log. Attaches to the game
(pure-read player locate on a worker thread) and configures the overlays.
Owns the single shared LiveModel so every overlay reads one source of truth.
"""
from __future__ import annotations

import datetime
import time
from collections import deque

from PySide6 import QtCore, QtGui, QtWidgets
from shiboken6 import isValid as _is_valid

from . import theme
from . import components as C
from .layout import make_scroll
from .page_history import PageHistory
from .overlay_manager import OverlayManager
from .game_attach import GameAttachmentController
from .workers import CallWorker
from .account import AccountMixin
from .pages.speedrun_page import SpeedrunPageMixin
from .pages.friends_page import FriendsPageMixin
from .pages.overlays import OverlaysPageMixin
from .pages.codex import CodexPageMixin  # composed from page/grid/map_ui/settings mixins
from .pages.combat import CombatPageMixin
from .pages.craft import CraftPageMixin
from .pages.items import ItemPageMixin
from .pages.settings import SettingsPageMixin
from .pages.log import LogPageMixin
from .pages.collection import CollectionPageMixin
from .pages.server_page import ServerPageMixin
from ..config import Settings
from ..core.proc import backend_name
from ..core import updater
from ..data import names, icons
from .. import __version__
from ..api import FareverAPI
from ..sound import play_ding, play_sound

# nav: key, icon, label
NAV = [
    ("overlays", "layers", "Overlays"),
    ("settings", "settings", "Settings"),
    ("codex", "layout", "Codex"),
    ("combat", "swords", "Combat / DPS"),
    ("speedrun", "timer", "Speedrun"),
    ("gear", "box", "Gear"),
    ("craft", "settings", "Craft"),
    ("collection", "archive", "Collection"),
    ("friends", "users", "Friends"),
    ("server", "broadcast", "Server Ping"),
    ("log", "terminal", "Log"),
    # git-ignored dev icon-preview tool; only shown in dev/run mode
    # (FAREVER_EXPERIMENTAL + the devicons module present on this machine).
    ("devicons", "eye", "Dev Icons"),
]

def filtered_nav(settings: Settings) -> list:
    """Nav entries the user may see: sign-in gating is applied live by
    _refresh_nav_visibility, this drops the pages the user disabled
    (Settings → Pages) so they never appear or build."""
    disabled = set(getattr(settings, "disabled_pages", []) or [])
    return [(k, i, l) for k, i, l in NAV if k not in disabled and k != "log"]


def dev_icons_enabled() -> bool:
    """Dev-only icon-preview page gate: only visible in dev/run mode, never in a frozen/packaged final build."""
    import sys
    if getattr(sys, "frozen", False):
        return False
    try:
        import importlib.util
        return importlib.util.find_spec("farever_companion.ui.pages.devicons") is not None
    except Exception:
        return False


STEAM_RUN_URL = "steam://rungameid/3672400"


def _trim_working_set() -> None:
    """Flush Python garbage and release unreferenced pages from the Windows
    process working set back to the OS.

    Without SetProcessWorkingSetSize(-1, -1), the Windows memory manager keeps
    allocated/freed heap pages in the process's physical RAM (Working Set).
    Calling this brings idle RAM from ~250MB+ down to ~30-50MB.
    """
    import gc
    import sys
    gc.collect()
    if sys.platform == "win32":
        try:
            import ctypes
            k32 = ctypes.windll.kernel32
            k32.GetCurrentProcess.restype = ctypes.c_void_p
            h = k32.GetCurrentProcess()
            k32.SetProcessWorkingSetSize(h, ctypes.c_size_t(-1), ctypes.c_size_t(-1))
        except Exception:
            pass


class ClickFrame(QtWidgets.QFrame):
    """A QFrame that emits `clicked` on left-click (sidebar rift card)."""
    clicked = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._selected = False

    def setSelected(self, on: bool) -> None:
        self._selected = on

    def mousePressEvent(self, e):
        if e.button() == QtCore.Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(e)


class ControlPanel(AccountMixin, SpeedrunPageMixin, FriendsPageMixin,
                   OverlaysPageMixin, CodexPageMixin,
                   CombatPageMixin, ItemPageMixin, CraftPageMixin,
                   CollectionPageMixin, SettingsPageMixin,
                   ServerPageMixin, LogPageMixin,
                   QtWidgets.QMainWindow):
    def __init__(self, settings: Settings):
        super().__init__()
        self.s = settings
        # Log lines outlive the (evictable) Log page widget; the page replays
        # this on build so opening it shows recent history, not just new events.
        self._log_buffer: deque[str] = deque(maxlen=200)
        # The live game session (Proc handle + LiveModel + locate / auto-attach)
        # is owned by a controller; `self.proc` / `self.model` below delegate to
        # it. Created before the UI builds so the page builders can read the
        # (initially None) model via the property.
        self.attach_ctl = GameAttachmentController(settings, self)
        # The overlay windows + their toggle-cards live in a dedicated manager;
        # `self.overlays` / `self._overlay_cards` below delegate to it so the page
        # code and minimap setters keep their existing access.
        self.overlay_mgr = OverlayManager(settings, self)
        self.overlay_mgr.log.connect(self.log)
        self.overlay_mgr.request_page.connect(self._select_nav)
        self.overlay_mgr.request_page.connect(self._raise_self)
        self._nav_items: dict[str, C.NavItem] = {}
        # friends + presence
        self._friends: list = []
        self._friends_worker: CallWorker | None = None
        # collection tracker (account-synced; pending sets survive a failed push)
        self._col_owned: set = set()
        self._col_pending_add: set = set()
        self._col_pending_remove: set = set()
        self._col_worker: CallWorker | None = None
        self._last_hidden_unit: tuple[str, str] | None = None  # (uid, name)
        self._last_hidden_comp: tuple[str, str] | None = None  # (uid, name)
        self._nav_history = PageHistory(self._select_nav)   # mouse back/forward
        QtWidgets.QApplication.instance().installEventFilter(self)

        self.setWindowTitle("Farever Pal")
        self.setMinimumSize(1000, 640)
        self.resize(1180, 740)

        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        root = QtWidgets.QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_sidebar())
        root.addWidget(self._build_main(), 1)

        self._select_nav("overlays")

        # system-wide hotkeys (work while game-focused)
        from .hotkeys import GlobalHotkeys
        self.hotkeys = GlobalHotkeys(QtWidgets.QApplication.instance())
        self.hotkeys.triggered.connect(self._on_hotkey)
        self._register_hotkeys()

        # Friends presence: heartbeat + list refresh (app open = "online").
        self._friends_timer = QtCore.QTimer(self)
        self._friends_timer.setInterval(45_000)
        self._friends_timer.timeout.connect(self._friends_poll)
        # Always enable friend gating logic regardless of disable_web_features
        self._refresh_friends_gating()
        self._refresh_nav_visibility()

        # Auto-attach watcher (in the controller): a cheap 2 s poll that attaches
        # when Farever opens, keeps trying to locate the player until they're
        # in-world, and detaches when the game closes - all read-only. The panel
        # is a view: it just reacts to the controller's signals.
        self.attach_ctl.log.connect(self.log)
        self.attach_ctl.status.connect(self._set_status)
        self.attach_ctl.locating.connect(self._set_locating)
        self.attach_ctl.located_changed.connect(self._set_overlay_cards_enabled)
        self.attach_ctl.model_changed.connect(self._on_model_changed)
        self.attach_ctl.detaching.connect(self._on_detaching)
        self.attach_ctl.goto_log.connect(lambda: self._select_nav("log"))
        self.attach_ctl.start()
        # NOTE: no startup icon prewarm — rendering every item tile costs tens
        # of MB of cached pixmaps the moment the app opens. It now runs on the
        # first visit to a tile-heavy page instead (see _select_nav).

        # Memory reclamation timers: shed pages shortly after the user leaves
        # them (done-with-it pages shouldn't stay resident), and run the full
        # icon-cache sweep when the app as a whole goes unused.
        self._last_interaction = time.monotonic()
        self._mem_timer = QtCore.QTimer(self)
        self._mem_timer.setInterval(10_000)
        self._mem_timer.timeout.connect(self._memory_tick)
        self._mem_timer.start()

        # Initial post-startup memory sweep: shed temporary initialization allocations
        QtCore.QTimer.singleShot(3000, _trim_working_set)

        # Self-update: clear any leftover *.old from a prior update, then check
        # GitHub Releases (via the website) for a newer exe - off the UI thread.
        updater.cleanup_old()
        self._update_info = None
        self._update_check_worker: CallWorker | None = None
        self._update_dl_worker: CallWorker | None = None
        self._updating = False

        self.log(f"Read-only. Memory backend: {backend_name()}. Press Attach "
                 "(if another memory tool is running, unload it first to avoid "
                 "interference).")

    # The live session is owned by `self.attach_ctl`; the overlay windows + cards
    # by `self.overlay_mgr`. These properties keep the existing `self.proc` /
    # `self.model` / `self.overlays` / `self._overlay_cards` access working
    # unchanged across the page builders, status log, and minimap setters.
    @property
    def proc(self):
        return self.attach_ctl.proc

    @property
    def model(self):
        return self.attach_ctl.model

    @property
    def overlays(self) -> dict:
        return self.overlay_mgr.overlays

    @property
    def _overlay_cards(self) -> dict:
        return self.overlay_mgr.cards

    # --- controller signal handlers (the panel is a view) ----------------
    def _set_locating(self, on: bool):
        self._locate_bar.setVisible(on)

    def _on_model_changed(self, model):
        self.overlay_mgr.set_model(model)
        # Refresh the active page if it supports profile-based data
        idx = self.stack.currentIndex()
        order = [k for k, _, _ in NAV]
        if 0 <= idx < len(order):
            key = order[idx]
            if key == "entity" and hasattr(self, "_refresh_entity_page"):
                self._refresh_entity_page()

    def _on_detaching(self):
        # The controller is about to stop the model's threads; close overlays
        # first (they read the model).
        self.overlay_mgr.close_all()

    def _refresh_nav_visibility(self):
        """Show/hide nav items: sign-in gated pages + user-disabled pages."""
        signed_in = bool(self.s.account_token)
        disabled = set(getattr(self.s, "disabled_pages", []) or [])
        for key, item in self._nav_items.items():
            if key == "log":
                item.setVisible(False)
            elif key in ("friends", "collection"):
                item.setVisible(signed_in and key not in disabled)
            else:
                item.setVisible(key not in disabled)
        # If the current page was hidden, jump back to overlays
        if not hasattr(self, "stack"):
            return
        idx = self.stack.currentIndex()
        order = [k for k, _, _ in NAV]
        if 0 <= idx < len(order):
            current_key = order[idx]
            if current_key in ("friends", "collection") and not signed_in \
                    or current_key in disabled:
                self._select_nav("overlays")

    # --- sidebar ---------------------------------------------------------
    def _build_sidebar(self):
        bar = QtWidgets.QFrame()
        bar.setObjectName("Sidebar")
        bar.setFixedWidth(212)
        lay = QtWidgets.QVBoxLayout(bar)
        lay.setContentsMargins(14, 16, 14, 14)
        lay.setSpacing(4)
        self._sidebar_lay = lay  # kept for injecting reclaimed nav items below

        brand = QtWidgets.QLabel("FAREVER PAL")
        brand.setObjectName("Brand")
        ver = QtWidgets.QLabel(f'<a href="https://kohan-mucan.github.io/FareverPal/" style="color:#bdc8d1; text-decoration:none;">Git Version v{__version__}</a>')
        ver.setOpenExternalLinks(True)
        ver.setObjectName("Mono")
        lay.addWidget(brand)
        lay.addWidget(ver)
        # Update pill - hidden until the startup check finds a newer release.
        self._update_btn = QtWidgets.QPushButton("")
        self._update_btn.setObjectName("Accent")
        self._update_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self._update_btn.setVisible(False)
        self._update_btn.clicked.connect(self._on_update_clicked)
        lay.addWidget(self._update_btn)
        lay.addSpacing(18)

        for key, icon, label in filtered_nav(self.s):
            if key == "devicons" and not dev_icons_enabled():
                continue
            item = C.NavItem(key, icon, label)
            item.clicked.connect(self._select_nav)
            self._nav_items[key] = item
            lay.addWidget(item)
        # Slot index right after the nav list (before the tail stretch): a page
        # re-enabled at runtime gets injected here so no restart is needed.
        self._nav_insert_at = lay.count()

        lay.addStretch(1)
        lay.addWidget(self._build_rift_box())
        lay.addWidget(self._build_launch_button())
        lay.addWidget(self._build_sidebar_footer())

        # Attach/detach is fully automatic (watches for Farever) - no buttons or
        # toggle; the status label in the footer is the single source of truth.
        self._refresh_account_button()
        self._apply_sidebar_element_visibility()
        self._set_status(False, "detached")
        return bar

    def _apply_sidebar_element_visibility(self) -> None:
        """Settings → Pages → Sidebar Elements: hide the rift predictor card,
        Launch Game button, account row, log footer button, or dev icons nav item."""
        targets = (
            ("show_rift_box", getattr(self, "_rift_box", None)),
            ("show_launch_button", getattr(self, "_launch_btn", None)),
            ("show_account_button", getattr(self, "_acct_container", None)),
            ("show_log", getattr(self, "_log_btn", None)),
        )
        for attr, w in targets:
            if w is not None:
                w.setVisible(bool(getattr(self.s, attr, True)))

        log_item = self._nav_items.get("log")
        if log_item is not None:
            log_item.setVisible(False)  # log is accessed cleanly via the footer icon

        dev_item = self._nav_items.get("devicons")
        if dev_item is not None:
            dev_item.setVisible(bool(getattr(self.s, "show_devicons", False)) and dev_icons_enabled())

    def _build_rift_box(self):
        """Compact next-rift predictor pinned above the sidebar footer. Pure
        clock + LCG (no memory reads); clicking it opens Settings · Rift.
        The whole card is state-tinted (red live / green warning / cyan idle)."""
        from ..core.rift_tracker import rift_summary
        self._rift_summary = rift_summary
        box = ClickFrame()
        box.setObjectName("Card")
        self._rift_box = box
        box.setCursor(QtCore.Qt.PointingHandCursor)
        box.setToolTip("Rift schedule — click to open Settings · Rift")
        v = QtWidgets.QVBoxLayout(box)
        v.setContentsMargins(10, 8, 10, 8)
        v.setSpacing(3)
        head = QtWidgets.QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(6)
        self._rift_icon = QtWidgets.QLabel()
        self._rift_icon.setFixedSize(15, 15)
        self._rift_icon.setPixmap(icons.marker("rift", 15, accent=theme.ACCENT))
        head.addWidget(self._rift_icon)
        head_lbl = QtWidgets.QLabel("RIFT")
        head_lbl.setStyleSheet(
            f"color:{theme.ACCENT};font-size:11px;font-weight:700;"
            "letter-spacing:2px;background:transparent;border:none;")
        head.addWidget(head_lbl)
        head.addStretch(1)
        v.addLayout(head)
        self._rift_side_zone = QtWidgets.QLabel("--")
        self._rift_side_zone.setObjectName("Mono")
        self._rift_side_zone.setStyleSheet(
            "font-size:14px;font-weight:700;background:transparent;border:none;")
        v.addWidget(self._rift_side_zone)
        self._rift_side_status = QtWidgets.QLabel("")
        self._rift_side_status.setObjectName("Mono")
        self._rift_side_status.setStyleSheet(
            "font-size:12px;font-weight:600;background:transparent;border:none;")
        v.addWidget(self._rift_side_status)
        self._rift_side_follow = QtWidgets.QLabel("")
        self._rift_side_follow.setObjectName("Mono")
        self._rift_side_follow.setStyleSheet(
            "font-size:11px;background:transparent;border:none;")
        v.addWidget(self._rift_side_follow)
        box.clicked.connect(lambda: self._select_nav("settings:rift"))
        self._update_sidebar_rift()
        self._rift_side_timer = QtCore.QTimer(box)
        self._rift_side_timer.setInterval(1000)
        self._rift_side_timer.timeout.connect(self._update_sidebar_rift)
        self._rift_side_timer.start()
        return box

    def _update_sidebar_rift(self):
        r = self._rift_summary()
        self._check_rift_ding(r)
        color = r["color"]
        # Zone name styled with theme.GOLD
        self._rift_side_zone.setText(r["zone"])
        self._rift_side_zone.setStyleSheet(
            f"color:{theme.GOLD};font-size:14px;font-weight:700;"
            "background:transparent;border:none;")

        GREEN = "#66BB6A"
        secs = r.get("secs_until", 10**9)
        time_green = secs < 900 and r["state"] not in ("ACTIVE", "CLOSING")

        if r["state"] == "ACTIVE":
            prefix, tstr = "LIVE NOW", ""
        elif r["state"] == "CLOSING":
            prefix, tstr = "Closing ", r["formatted"]
        elif r["state"] == "WARNING":
            prefix, tstr = "Starting ", r["formatted"]
        else:
            prefix, tstr = "In ", r["formatted"]

        # Only the countdown turns green under 15 min; the label words stay neutral.
        tcolor = GREEN if time_green else color
        html = f'<font color="#bdc8d1">{prefix}</font>'
        if tstr:
            html += f'<font color="{tcolor}">{tstr}</font>'
        self._rift_side_status.setTextFormat(QtCore.Qt.RichText)
        self._rift_side_status.setText(html)
        self._rift_side_status.setStyleSheet(
            "font-size:12px;font-weight:600;"
            "background:transparent;border:none;")
        self._rift_side_follow.setText(f"Then: {r['follow_zone']} · {r['follow_in']}")
        self._rift_side_follow.setStyleSheet(
            "color:#87929a;font-size:11px;background:transparent;border:none;")
        self._rift_icon.setPixmap(icons.marker("rift", 15, accent=color))
        # Highlight the box only while the Rift view is the active one; derive
        # it from the live current view every tick so it never gets "stuck"
        # selected after navigating to another tab/page.
        cur = (self._current_page() or "").lower()
        on_rift = cur in ("settings:rift", "rift") or (
            cur.startswith("settings") and hasattr(self, "_settings_tabs")
            and (self._settings_tabs.currentText() or "").lower() == "rift"
        )
        self._rift_box._selected = on_rift
        if on_rift:
            border = theme.ACCENT
            bw = 2
        else:
            border = theme.BORDER
            bw = 1
        self._rift_box.setStyleSheet(
            f"QFrame#Card {{ background: {theme.PANEL}; border: {bw}px solid {border}; }}")

    def _refresh_rift_box_selected(self) -> None:
        """Force an immediate sync of the sidebar RIFT box highlight."""
        if hasattr(self, "_rift_box"):
            self._update_sidebar_rift()

    def _check_rift_ding(self, r: dict) -> None:
        """Play a ding at the configured lead times (multi-select) before a rift.

        Thresholds > 0 fire during the 15-min WARNING window (at N minutes out);
        the special 0 threshold fires once when the rift goes LIVE (ACTIVE). The
        sidebar timer ticks every second, so we latch which thresholds have
        already fired for the current rift cycle and clear the latch when the
        window resets (new hour)."""
        if getattr(self.s, "show_rift_timer", "Always") == "Off":
            return
        raw = getattr(self.s, "rift_ding", ["15", "10", "5", "1"])
        if isinstance(raw, str):
            if raw.lower() == "off":
                return
            if raw.lower() == "all":
                thresholds = [15, 10, 5, 1, 0]
            else:
                try:
                    thresholds = [int(raw.replace("m", ""))]
                except ValueError:
                    thresholds = [15, 10, 5, 1, 0]
        else:
            try:
                thresholds = sorted({int(str(x).replace("m", "")) for x in raw})
            except Exception:
                thresholds = [15, 10, 5, 1, 0]
        if not thresholds:
            return
        secs = r.get("secs_until", 10**9)
        state = r["state"]
        if not hasattr(self, "_rift_dinged"):
            self._rift_dinged = set()
        # Reset the latch once we're outside the 15-min warning window.
        if secs > 15 * 60:
            self._rift_dinged = set()
            return
        for m in thresholds:
            if m == 0:
                # "Live" fires at the final second before the rift opens.
                if state == "WARNING" and secs <= 1 and 0 not in self._rift_dinged:
                    self._rift_dinged.add(0)
                    play_sound(getattr(self.s, "rift_sound", "ding"))
            else:
                if state == "WARNING" and secs <= m * 60 and m not in self._rift_dinged:
                    self._rift_dinged.add(m)
                    play_sound(getattr(self.s, "rift_sound", "ding"))

    def _launch_game(self):
        QtGui.QDesktopServices.openUrl(QtCore.QUrl(STEAM_RUN_URL))
        self.log("Launching Farever via Steam…")

    def _build_launch_button(self):
        """Launch Farever via Steam (works whether or not the game is running)."""
        launch = QtWidgets.QPushButton("  Launch Game")
        launch.setObjectName("Outline")
        launch.setCursor(QtCore.Qt.PointingHandCursor)
        launch.setMinimumHeight(30)
        launch.setIcon(QtGui.QIcon(icons.asset_icon("gameicon", 16)))
        launch.setIconSize(QtCore.QSize(16, 16))
        launch.setFocusPolicy(QtCore.Qt.NoFocus)
        launch.clicked.connect(self._launch_game)
        self._launch_btn = launch
        return launch

    def _build_sidebar_footer(self):
        """Bottom of the sidebar: account (sign-in / avatar) button above an
        attach-status strip with the marquee locate bar."""
        foot = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(foot)
        v.setContentsMargins(0, 10, 0, 0)
        v.setSpacing(8)
        # account button (sign in / avatar + name), full sidebar width
        self._acct_container = self._build_account_host()
        v.addWidget(self._acct_container)
        line = QtWidgets.QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet(f"background:{theme.BORDER};border:0;")
        v.addWidget(line)
        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(2, 0, 2, 0)
        row.setSpacing(8)
        self.status = QtWidgets.QLabel()
        self.status.setObjectName("Mono")
        row.addWidget(self.status)
        # Indeterminate "busy" bar shown only while the pure-read locate scan is
        # running (it can take a while; the scan gives no real %, so it's a
        # marquee, not a percentage). Hidden whenever we're not actively locating.
        self._locate_bar = QtWidgets.QProgressBar()
        self._locate_bar.setRange(0, 0)            # indeterminate / marquee
        self._locate_bar.setTextVisible(False)
        self._locate_bar.setFixedSize(64, 6)
        self._locate_bar.setObjectName("LocateBar")
        self._locate_bar.setStyleSheet(
            f"QProgressBar{{background:{theme.PANEL};border:0;border-radius:0;}}"
            f"QProgressBar::chunk{{background:{theme.ACCENT};border-radius:0;}}")
        self._locate_bar.setVisible(False)
        row.addWidget(self._locate_bar)
        row.addStretch(1)
        # Activity Log shortcut button in footer
        log_btn = QtWidgets.QPushButton()
        log_btn.setObjectName("Icon")
        log_btn.setIcon(icons.ui_qicon("terminal", theme.MUTED, 16))
        log_btn.setToolTip("Activity Log")
        log_btn.setCursor(QtCore.Qt.PointingHandCursor)
        log_btn.setFocusPolicy(QtCore.Qt.NoFocus)
        log_btn.setFixedSize(22, 22)
        log_btn.clicked.connect(lambda: self._select_nav("log"))
        self._log_btn = log_btn
        row.addWidget(log_btn)
        v.addLayout(row)
        return foot

    # --- main column -----------------------------------------------------
    def _build_main(self):
        col = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(col)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        self.stack = QtWidgets.QStackedWidget()
        self._pages = {}
        self._pages_built = {}
        # LRU clock for the page-cache eviction policy (see _evict_idle_pages).
        self._page_last_used: dict[str, float] = {}
        # One stack slot per nav key from the FULL list (never filtered):
        # indices must stay stable even when the user disables/enables pages
        # at runtime, or every order.index(key) lookup would shift.
        for key, _icon, _label in NAV:
            self._pages[key] = self.stack.addWidget(QtWidgets.QWidget())
            self._pages_built[key] = False
        v.addWidget(self.stack, 1)
        return col

    def _scroll(self, inner):
        return make_scroll(inner)

    def _build_statusstrip(self):
        strip = QtWidgets.QFrame()
        strip.setObjectName("StatusStrip")
        strip.setFixedHeight(28)
        lay = QtWidgets.QHBoxLayout(strip)
        lay.setContentsMargins(16, 0, 16, 0)
        lay.setSpacing(10)
        dot = QtWidgets.QFrame()
        dot.setFixedSize(8, 8)
        dot.setStyleSheet(f"background:{theme.ACCENT};border:0;")
        self._strip_left = QtWidgets.QLabel()
        self._strip_left.setObjectName("Mono")
        right = QtWidgets.QLabel("DOCS   SUPPORT   API")
        right.setObjectName("Mono")
        lay.addWidget(dot)
        lay.addWidget(self._strip_left)
        lay.addStretch(1)
        lay.addWidget(right)
        self._refresh_strip()
        return strip

    def _refresh_strip(self):
        self._strip_left.setText(
            f"READ-ONLY · BACKEND: {backend_name().upper()} · "
            "PER-SKILL DPS: CALIBRATING")

    # --- nav -------------------------------------------------------------
    def _nav_order(self) -> list:
        """Full nav key order — matches stack indices exactly (never
        filtered; disabled pages keep their placeholder slot)."""
        return [k for k, _, _ in NAV]

    def _select_nav(self, target: str):
        key, _sep, sub = target.partition(":")
        # Route merged pages / legacy keys to settings
        if key in ("entity", "map", "dungeon", "hud"):
            key = "settings"
            if not sub:
                sub = "HUD's"
        if key in set(getattr(self.s, "disabled_pages", []) or []):
            return
        order = self._nav_order()
        if key not in order:
            return
        idx = order.index(key)
        # record the state we're leaving (skipped while back/forward restores)
        cur_target = self._current_page()
        self._nav_history.record(cur_target, target)
        if not self._pages_built.get(key, False):
            page_fn = getattr(self, f"_page_{key}", None)
            if page_fn:
                # items, craft, settings, etc. scroll internally or fit viewport
                if key not in ("log", "server", "gear", "craft", "settings", "devicons"):
                    w = self._scroll(page_fn())
                else:
                    w = page_fn()
                old_w = self.stack.widget(idx)
                self.stack.insertWidget(idx, w)
                self.stack.removeWidget(old_w)
                old_w.deleteLater()
                self._pages_built[key] = True

        for k, item in self._nav_items.items():
            item.setSelected(k == key)
        self.stack.setCurrentIndex(idx)

        # Touch the LRU clock, then shed any pages over the cache budget
        # (never the one we just opened). Evicted pages rebuild lazily on the
        # next visit, so this only costs a rebuild, not data.
        self._page_last_used[key] = time.monotonic()
        self._evict_idle_pages(key)

        # Restore sub-tab if specified
        if sub:
            if key == "craft" and hasattr(self, "_craft_tabs"):
                if self._craft_tabs.currentText() != sub:
                    self._craft_tabs.setCurrentText(sub)
                    self._craft_tab_changed(sub, record_history=False)
            elif key == "gear" and hasattr(self, "_items_tabs"):
                if self._items_tabs.currentText() != sub:
                    self._items_tabs.setCurrentText(sub)
                    self._items_set_mode(sub, record_history=False)
            elif key == "settings" and hasattr(self, "_settings_tabs"):
                self._on_settings_tab(sub)
            elif key == "codex" and hasattr(self, "_codex_tabs"):
                tab, _sep, view = sub.partition(":")
                if self._codex_tabs.currentText() != tab:
                    self._codex_tabs.setCurrentText(tab)
                    self._codex_tab_changed(0)
                if tab == "Collection" and view:
                    v_low = view.lower()
                    if v_low in ("chests", "orbs") and hasattr(self, "_set_dungeon_view_mode"):
                        self._set_dungeon_view_mode(v_low)
                    elif hasattr(self, "_collection_sub_btns"):
                        btn = self._collection_sub_btns.get(view.capitalize())
                        if btn is not None:
                            btn.click()
                elif tab == "Dungeons" and view and hasattr(self, "_set_dungeon_view_mode"):
                    v_low = view.lower()
                    if v_low in ("list", "soulstones", "mobs"):
                        self._set_dungeon_view_mode(v_low)

        # Freshen friend presence/list when opening a page that shows it.
        if key in ("friends", "speedrun") and self.s.account_token:
            # the friends poll timer is paused while the page is evicted
            if hasattr(self, "_friends_timer") and not self._friends_timer.isActive():
                self._friends_timer.start()
            self._friends_poll()
        # Freshen the account's collection when opening the tracker.
        if key == "collection" and self.s.account_token:
            self._col_pull()
        # Refresh the active page content for profile-specific settings
        if key == "entity" and hasattr(self, "_refresh_entity_page"):
            self._refresh_entity_page()
        if key == "settings" and hasattr(self, "_refresh_settings_page"):
            self._refresh_settings_page()
        if key == "codex" and hasattr(self, "_refresh_codex_grid"):
            # An explicit 'codex:<Tab>[:<view>]' request wins — the zone sync
            # would yank the tab back to the player's current region.
            if not sub and hasattr(self, "_sync_codex_to_current_zone"):
                self._sync_codex_to_current_zone()
                self._refresh_codex_grid()
        # Refresh live Steam player count when opening or reopening the Server Diagnostics page.
        if key == "server" and hasattr(self, "_fetch_steam_player_count"):
            self._fetch_steam_player_count()

        # First visit to a tile-heavy page: warm the item-tile pixmap caches
        # off the critical path (deferred from startup to keep the idle
        # footprint small — see the note in __init__).
        if key in ("gear", "craft", "collection") \
                and not getattr(self, "_tiles_prewarmed", False):
            self._tiles_prewarmed = True
            QtCore.QTimer.singleShot(400, self._prewarm_icon_cache)

        # Keep the sidebar RIFT box highlight in sync with the active view.
        self._refresh_rift_box_selected()

    # --- dev icon-preview page (git-ignored; dev/run mode only) -----------
    def _page_devicons(self):
        """Lazily build the git-ignored Dev Icons preview page. Guarded so a
        missing module (e.g. a clean clone) never breaks the nav slot."""
        try:
            from .pages.devicons import DevIconsPage
            return DevIconsPage(self, self.s)
        except Exception as exc:  # pragma: no cover - dev-only safety net
            lbl = QtWidgets.QLabel(f"Dev Icons page failed to load:\n{exc}")
            lbl.setObjectName("Muted")
            lbl.setWordWrap(True)
            return lbl

    # --- page-cache eviction ---------------------------------------------
    # 2 resident pages max: instant switching between your active pages without
    # lag; older pages are evicted, and idle/minimize sweeps reclaim memory automatically.
    _PAGE_CACHE_BUDGET = 2

    @staticmethod
    def _widget_alive(w) -> bool:
        """True when a widget reference still points at a live (non-deleted)
        Qt object — used to guard async callbacks (timers, workers, signals)
        that may fire after the page they belong to was evicted."""
        return w is not None and _is_valid(w)

    def _evict_page(self, key: str) -> None:
        """Shed one built page's widget tree (swapped for a fresh placeholder
        so its stack index is preserved); it rebuilds lazily on the next
        visit. No data is lost — pages re-read settings/model state."""
        order = self._nav_order()
        if key not in order or not self._pages_built.get(key):
            return
        idx = order.index(key)
        w = self.stack.widget(idx)
        if w is not None:
            self.stack.removeWidget(w)
            w.deleteLater()
            # slot keeps its index: put a placeholder back in place
            self.stack.insertWidget(idx, QtWidgets.QWidget())
        self._pages_built[key] = False
        self._page_last_used.pop(key, None)
        self._on_page_evicted(key)

    def _evict_idle_pages(self, current_key: str) -> None:
        """LRU page-cache eviction. Keeps only the currently viewed page resident;
        the previous page is shed immediately so widget trees (codex ≈ 58 MB,
        gear ≈ 40 MB) do not accumulate in memory."""
        if not hasattr(self, "_pages_built"):
            return
        order = self._nav_order()
        built = [k for k in order if self._pages_built.get(k)]
        evicted = False
        while len(built) > self._PAGE_CACHE_BUDGET:
            candidates = [k for k in built if k != current_key]
            if not candidates:
                break
            lru = min(candidates,
                      key=lambda k: self._page_last_used.get(k, 0.0))
            self._evict_page(lru)
            evicted = True
            built = [k for k in order if self._pages_built.get(k)]
        if evicted:
            _trim_working_set()

    # --- memory reclamation --------------------------------------------------
    _IDLE_AFTER_S = 20         # full sweep (caches + gc + working set trim) after 20s of quiet
    _PAGE_IDLE_EVICT_S = 120   # shed 2nd inactive tab after 2 mins (120s) of navigating away

    def _memory_tick(self) -> None:
        self._evict_stale_pages()
        self._idle_sweep_check()

    def _evict_stale_pages(self) -> None:
        """Unload built pages the user navigated away from more than
        `_PAGE_IDLE_EVICT_S` ago."""
        if not hasattr(self, "_pages_built"):
            return
        now = time.monotonic()
        cur_key = (self._current_page() or "").partition(":")[0]
        evicted = False
        for k in self._nav_order():
            if k == cur_key or not self._pages_built.get(k):
                continue
            if now - self._page_last_used.get(k, 0.0) >= self._PAGE_IDLE_EVICT_S:
                self._evict_page(k)
                evicted = True
        if evicted:
            _trim_working_set()

    def _live_overlays_active(self) -> bool:
        """True when any HUD overlay / compass needle is on screen. While the
        user is playing, those render icons from the pixmap caches every
        tick — clearing them mid-game would cause constant re-decode stutter."""
        om = self.overlay_mgr
        for ov in om.overlays.values():
            if ov is not None and ov.isVisible():
                return True
        for tr in (om.tracker, om.dungeon_tracker):
            needle = getattr(tr, "_needle", None)
            if needle is not None and needle.isVisible():
                return True
        return False

    def _idle_sweep_check(self) -> None:
        """When nobody has touched the panel (or its overlays) for a while,
        unload everything that can be rebuilt so the idle footprint stays
        small — this is where the app sits all day while the game runs."""
        if time.monotonic() - self._last_interaction < self._IDLE_AFTER_S:
            return
        order = self._nav_order()
        cur_key = (self._current_page() or "").partition(":")[0]
        unloaded = [k for k in order if k != cur_key and self._pages_built.get(k)]
        for k in unloaded:
            self._evict_page(k)
        # With the game running + HUD/minimap up, the atlas sheets and icon
        # pixmaps ARE live data — only shed them when nothing is rendering.
        if not (self.model is not None and self._live_overlays_active()):
            icons.trim_caches()
            QtGui.QPixmapCache.clear()
        _trim_working_set()
        if unloaded:
            self.log(f"Idle {self._IDLE_AFTER_S}s — unloaded "
                     f"{len(unloaded)} page(s) to free memory.")

    def changeEvent(self, event: QtCore.QEvent) -> None:
        """When minimized, unload all pages and trim working set. When restored,
        rebuild the active page lazily."""
        if event.type() == QtCore.QEvent.WindowStateChange:
            if self.isMinimized():
                self._on_minimized()
            elif getattr(self, "_was_minimized", False) and not self.isMinimized():
                self._on_restored()
        elif event.type() == QtCore.QEvent.ActivationChange:
            if not self.isActiveWindow() and not self.isMinimized():
                QtCore.QTimer.singleShot(1500, _trim_working_set)
        super().changeEvent(event)

    def _on_minimized(self) -> None:
        """Unload ALL pages and sprite caches when minimized so memory drops to minimum."""
        self._was_minimized = True
        self._minimized_page = self._current_page()
        order = self._nav_order()
        for k in order:
            if self._pages_built.get(k):
                self._evict_page(k)
        if not (self.model is not None and self._live_overlays_active()):
            icons.trim_caches()
            QtGui.QPixmapCache.clear()
        _trim_working_set()

    def _on_restored(self) -> None:
        """Restore the active page lazily when window is restored from minimized."""
        self._was_minimized = False
        target = getattr(self, "_minimized_page", None) or "overlays"
        self._minimized_page = None
        self._select_nav(target)

    def _on_page_evicted(self, key: str) -> None:
        """Per-page cleanup when a page's widget tree is shed. Currently only
        the Friends page has a panel-owned poll timer (the rest own their
        timers inside the page and die with it), so pause it here and let
        _select_nav restart it when the page is revisited."""
        if key == "friends" and hasattr(self, "_friends_timer"):
            self._friends_timer.stop()

    def _set_page_disabled(self, key: str, disabled: bool) -> None:
        """Settings → Pages: a disabled page is removed from the sidebar and
        never built (no widget tree, no data dicts). Takes effect live; if
        it's the page on screen we jump back to Overlays first."""
        dis = list(getattr(self.s, "disabled_pages", []) or [])
        if disabled and key not in dis:
            dis.append(key)
        elif not disabled and key in dis:
            dis.remove(key)
        else:
            return
        self.s.disabled_pages = dis
        self.s.save()
        if not disabled:
            # A page turned back on that was disabled at startup never got a
            # sidebar slot (filtered_nav skipped it) — create it live so the
            # page is reachable without restarting the app.
            self._ensure_nav_item(key)
        if hasattr(self, "_nav_items"):
            self._refresh_nav_visibility()
        if disabled and hasattr(self, "stack"):
            cur_key = (self._current_page() or "").partition(":")[0]
            if cur_key == key:
                self._select_nav("overlays")
            self._evict_page(key)
            _trim_working_set()

    def _nav_insert_index(self, key: str) -> int:
        """Sidebar index that keeps a reclaimed page in canonical NAV order —
        right before the first already-present successor, or at the tail of the
        nav list when every later page is still disabled."""
        order = [k for k, _, _ in NAV]
        for k in order[order.index(key) + 1:]:
            if k in self._nav_items:
                i = self._sidebar_lay.indexOf(self._nav_items[k])
                if i != -1:
                    return i
        return getattr(self, "_nav_insert_at", self._sidebar_lay.count())

    def _ensure_nav_item(self, key: str) -> None:
        """Materialize the sidebar entry for a page that was disabled when the
        app started, so turning it back on shows up immediately."""
        if key in self._nav_items:
            return
        entry = next((e for e in NAV if e[0] == key), None)
        if entry is None:
            return
        _k, icon, label = entry
        if key == "devicons" and not dev_icons_enabled():
            return
        item = C.NavItem(key, icon, label)
        item.clicked.connect(self._select_nav)
        self._sidebar_lay.insertWidget(self._nav_insert_index(key), item)
        self._nav_items[key] = item
        self._nav_insert_at = (getattr(self, "_nav_insert_at", 0) or 0) + 1
        self._apply_sidebar_element_visibility()

    # --- Pages -----------------------------------------------------------
    def _register_card(self, key: str, card):
        self.overlay_mgr.register_card(key, card)

    def _page_container(self, title: str | None = None):
        page = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(page)
        v.setContentsMargins(22, 20, 22, 20)
        v.setSpacing(16)
        return page, v

    # --- global hotkeys --------------------------------------------------
    def _register_hotkeys(self):
        res = []
        for action, key in (("sr_toggle", self.s.hotkey_speedrun_toggle),
                            ("sr_reset", self.s.hotkey_speedrun_reset)):
            ok = self.hotkeys.set_binding(action, key)
            res.append(f"{action}={key}" + ("" if ok else " (FAILED — combo taken?)"))
        self.log("Hotkeys: " + " · ".join(res))

    def _set_hotkey(self, attr, seq):
        self._set(attr, seq.toString())
        self._register_hotkeys()

    def _on_hotkey(self, action):
        if action in ("sr_toggle", "sr_reset"):
            ov = self.overlays.get("speedrun")
            if ov is None:
                self.log(f"Speedrun hotkey '{action}' — open the Speedrun overlay first.")
                return
            if action == "sr_toggle":
                ov.toggle()
                self.log("Speedrun: " + ("started" if ov.timer.state == "running"
                                         else f"stopped @ {ov.timer.elapsed():.2f}s"))
            else:
                ov.reset()
                self.log("Speedrun: reset")

    # --- Settings sync ---------------------------------------------------
    def _set(self, attr, value):
        setattr(self.s, attr, value)
        self.s.save()

    def _set_accent(self, color):
        """Re-tint the whole app live — global accent + QSS, plus the widgets
        that paint the accent directly (not via QSS)."""
        self.s.hud_accent = color
        self.s.save()
        theme.set_accent(color)
        app = QtWidgets.QApplication.instance()
        if app is not None:
            app.setStyleSheet(theme.QSS)        # control panel + inheriting overlays
        self._restyle_accent()                  # painted/captured-color widgets
        self.overlay_mgr.apply_accent(color)
        self._sync_accent_to_account()

    def _restyle_accent(self):
        """Refresh control-panel widgets that hold the accent as a captured value
        or paint it directly (QSS re-apply alone doesn't repaint these).
        """
        from .components import (SectionHeader, Stepper, NavItem,
                                 SegmentedControl, OverlayCard, SliderRow,
                                 FilterChip)
        from .chips import ChoiceChips
        for sh in self.findChildren(SectionHeader):
            sh.set_color(theme.ACCENT)          # control-panel headers are all accent
        for cls in (Stepper, NavItem, SegmentedControl, OverlayCard, SliderRow,
                    FilterChip, ChoiceChips):
            # widgets that captured the accent at construction -> re-tint
            for w in self.findChildren(cls):
                w.restyle()
        if self.s.account_token and getattr(self, "_avatar_pm", None) is None \
                and hasattr(self, "_acct_btn"):
            self._acct_btn.setIcon(QtGui.QIcon(self._account_avatar_pixmap(20)))
        for w in self.findChildren(QtWidgets.QWidget):
            w.update()                          # repaint live painters (toggles, etc.)

    def _set_opacity(self, v):
        self.overlay_mgr.set_opacity(v)

    def _set_lock(self, on):
        self.overlay_mgr.set_lock(on)

    def _set_snap_mouse(self, on: bool) -> None:
        self._set("snap_mouse_to_player", on)
        self.log("Mouse snapping ENABLED." if on else "Mouse snapping disabled.")

    # --- Process + overlays ----------------------------------------------
    def log(self, msg: str):
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        line = f"{ts}  {msg}"
        self._log_buffer.append(line)
        if self._widget_alive(getattr(self, "log_view", None)):
            self.log_view.appendPlainText(line)

    def _set_status(self, attached: bool, text: str):
        dot = theme.GOOD if attached else theme.NEUTRAL
        self.status.setText(f"●  {text}")
        self.status.setStyleSheet(
            f"color:{dot};font-family:'{theme.MONO_FONT}','Consoles';font-size:11px;")

    # --- self-update ----------------------------------------------------
    def _on_update_found(self, info):
        if info is None:
            return
        self._update_info = info
        self._update_btn.setText(f"↑  Update to v{info.version}")
        self._update_btn.setEnabled(True)
        self._update_btn.setVisible(True)
        self.log(f"Update available: v{__version__} → v{info.version}. "
                 "Click the sidebar button to install.")

    def _on_update_clicked(self):
        info = self._update_info
        if info is None or self._updating:
            return
        # From source (dev) the self-swap can't work - just open the release page.
        if not updater.is_frozen():
            import webbrowser
            webbrowser.open(info.html_url or f"{self.s.api_base}/download.php")
            return
        notes = (info.notes or "").strip()
        if len(notes) > 600:
            notes = notes[:600] + "…"
        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle("Update Farever Pal")
        box.setIcon(QtWidgets.QMessageBox.Question)
        box.setText(f"Update from v{__version__} to v{info.version}?\n\n"
                    "The app will download the new version, restart, and reconnect "
                    "automatically.")
        if notes:
            box.setInformativeText(notes)
        box.setStandardButtons(QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No)
        box.setDefaultButton(QtWidgets.QMessageBox.Yes)
        if box.exec() != QtWidgets.QMessageBox.Yes:
            return
        self._updating = True
        self._update_btn.setEnabled(False)
        self._update_btn.setText("Downloading…  0%")
        self.log(f"Downloading v{info.version}…")
        def _download(w):
            cb = lambda d, t: w.progress.emit(int(d * 100 / t)) if t else None
            try:
                return updater.download_and_stage(info, cb)
            except Exception as e:
                return e
        self._update_dl_worker = CallWorker(_download)
        self._update_dl_worker.progress.connect(self._on_update_progress)
        self._update_dl_worker.done.connect(lambda _t, r: self._on_update_downloaded(r))
        self._update_dl_worker.start()

    def _on_update_progress(self, pct: int):
        self._update_btn.setText(f"Downloading…  {pct}%")

    def _on_update_downloaded(self, result):
        if isinstance(result, Exception):
            self._updating = False
            self._update_btn.setEnabled(True)
            self._update_btn.setText(f"↑  Update to v{self._update_info.version} (retry)")
            self.log(f"Update failed: {result}")
            QtWidgets.QMessageBox.warning(
                self, "Update failed",
                f"Couldn't install the update:\n{result}\n\n"
                "You can retry, or download it from the website.")
            return
        # Staged successfully: swap the exe, relaunch, quit.
        self.log(f"Installing v{self._update_info.version} and restarting…")
        try:
            self.detach()                       # release the game cleanly first
            updater.apply_and_relaunch(result)
        except Exception as e:
            self._updating = False
            self._update_btn.setEnabled(True)
            self.log(f"Update install failed: {e}")
            QtWidgets.QMessageBox.warning(self, "Update failed", str(e))
            return
        QtWidgets.QApplication.quit()

    def _raise_self(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def eventFilter(self, obj, event):
        """Mouse BACK/FORWARD in the main window walks page history (game/overlays unaffected).
        The current page gets first say: the Dungeons tab's item detail uses
        BACK to return to the boss sheet instead of leaving the page."""
        et = event.type()
        if et in (QtCore.QEvent.MouseMove, QtCore.QEvent.MouseButtonPress,
                  QtCore.QEvent.MouseButtonRelease, QtCore.QEvent.KeyPress,
                  QtCore.QEvent.Wheel):
            # app-wide filter: any input this process receives (panel OR its
            # overlay windows) counts as "not idle" for the memory sweeper.
            # While the game is focused the app gets no input events, so the
            # sweep runs exactly when it should — during play.
            self._last_interaction = time.monotonic()
        if (event.type() == QtCore.QEvent.MouseButtonPress
                and isinstance(obj, QtWidgets.QWidget)
                and obj.window() is self):
            step = {QtCore.Qt.MouseButton.XButton1: self._nav_history.back,
                    QtCore.Qt.MouseButton.XButton2: self._nav_history.forward}.get(event.button())
            if step:
                cur = self.stack.currentWidget()
                if cur is not None \
                        and getattr(cur, "consume_pane_back", lambda: False)():
                    return True
                step(self._current_page())
                return True
        return super().eventFilter(obj, event)

    def _current_page(self) -> str | None:
        if not hasattr(self, "stack") or self.stack is None:
            return None
        order = self._nav_order()
        cur = self.stack.currentIndex()
        if not (0 <= cur < len(order)):
            return None
        key = order[cur]
        if key == "craft" and hasattr(self, "_craft_tabs"):
            tab = getattr(self, "_craft_active_tab", None) or self._craft_tabs.currentText()
            if tab:
                base_tab = "Craft List" if tab.startswith("Craft List") else tab
                return f"craft:{base_tab}"
        elif key == "gear" and hasattr(self, "_items_tabs"):
            tab = self._items_tabs.currentText()
            if tab:
                return f"gear:{tab}"
        elif key == "codex" and hasattr(self, "_codex_tabs"):
            tab = self._codex_tabs.currentText()
            if tab:
                if tab == "Collection":
                    sub_view = None
                    if hasattr(self, "_codex_collection_sub_tag"):
                        sub_view = self._codex_collection_sub_tag()
                    elif getattr(self, "_chest_orb_kind", None):
                        sub_view = self._chest_orb_kind
                    else:
                        sub_view = getattr(self, "_collection_sub", "pets")
                    if sub_view:
                        return f"codex:Collection:{sub_view.capitalize()}"
                elif tab == "Dungeons":
                    if getattr(self, "_soulstones_mode", False):
                        return "codex:Dungeons:Soulstones"
                    elif getattr(self, "_dungeon_list_mode", False):
                        return "codex:Dungeons:List"
                    else:
                        return "codex:Dungeons:Mobs"
                elif tab in ("Others", "Other") and hasattr(self, "_others_btn_group"):
                    btn = self._others_btn_group.checkedButton()
                    if btn:
                        return f"codex:Others:{btn.text()}"
                return f"codex:{tab}"
        elif key == "settings" and hasattr(self, "_settings_tabs"):
            tab = self._settings_tabs.currentText()
            if tab:
                return f"settings:{tab.lower()}"
        return key

    def _request_overlay(self, key: str, on: bool):
        self.overlay_mgr.request(key, on)

    def detach(self):
        """Tear down the live session. The controller closes overlays (via its
        `detaching` signal) before stopping the model + closing the handle.
        """
        self._last_hidden_unit = None
        self._last_hidden_comp = None
        self._last_hidden_chest_orb = None
        self.attach_ctl.detach()
        _trim_working_set()

    # --- Entity visibility sync -------------------------------------------

    def _set_unit_hidden(self, uid_or_uids, hidden: bool) -> None:
        profile = self.model.player_profile() if self.model else None
        if not profile:
            if hasattr(self, "_refresh_codex_sync"):
                self._refresh_codex_sync()
            return
        uids = [uid_or_uids] if isinstance(uid_or_uids, str) else uid_or_uids
        for u in uids:
            self.s.toggle_unit_hidden(u, hidden, profile)

        if hidden:
            tr = getattr(self, "tracker", None) or getattr(self, "_tracker", None)
            if tr and self.s.track_kind == "unit":
                tid = self.s.track_id
                if any(u == tid for u in uids):
                    tr.clear()

        if hidden and len(uids) == 1:
            self._last_hidden_unit = (uids[0], names.unit_name(uids[0]) or uids[0])
        elif not hidden and self._last_hidden_unit and self._last_hidden_unit[0] in uids:
            self._last_hidden_unit = None

        # 1. Sync Entity tab chips (the Entity page may be evicted from cache)
        if hasattr(self, "_unit_chips") and self._unit_chips:
            for us, _n, chip in self._unit_chips:
                if not self._widget_alive(chip):
                    continue
                if (isinstance(us, list) and uids == us) or (not isinstance(us, list) and us in uids):
                    chip.blockSignals(True)
                    chip.setChecked(not hidden)
                    chip.blockSignals(False)
                    chip.restyle()
                    break

        # 2. Sync Codex tab cards
        if hasattr(self, "_refresh_codex_sync"):
            self._refresh_codex_sync()

        if hasattr(self, "_update_unit_tag"):
            self._update_unit_tag()

    def _set_all_units_hidden(self, hidden: bool) -> None:
        profile = self.model.player_profile() if self.model else None
        
        if hidden:
            tr = getattr(self, "tracker", None) or getattr(self, "_tracker", None)
            if tr and self.s.track_kind == "unit":
                tr.clear()

        # 1. Update Settings
        if hasattr(self, "_unit_chips") and self._unit_chips:
            all_uids = []
            for item in self._unit_chips:
                uids = item[0]
                if isinstance(uids, list):
                    all_uids.extend(uids)
                else:
                    all_uids.append(uids)
            
            self.s.set_all_units_hidden(all_uids, hidden, profile)
            
            # 2. Sync Entity chips
            for _, _, chip in self._unit_chips:
                chip.blockSignals(True)
                chip.setChecked(not hidden)
                chip.blockSignals(False)
                chip.restyle()
        
        # 3. Sync Codex cards
        if hasattr(self, "_refresh_codex_sync"):
            self._refresh_codex_sync()

        if hasattr(self, "_update_unit_tag"):
            self._update_unit_tag()

    def _set_companion_hidden(self, uid: str, hidden: bool) -> None:
        profile = self.model.player_profile() if self.model else None
        self.s.toggle_companion_hidden(uid, hidden, profile)

        if hidden:
            tr = getattr(self, "tracker", None) or getattr(self, "_tracker", None)
            if tr and self.s.track_kind == "unit":
                tid = self.s.track_id
                if uid == tid:
                    tr.clear()

            nm = names.unit_name(uid)
            if not nm or nm == uid:
                from .data import codex
                info = codex.enemies_data().get(uid, {})
                nm = info.get("name") or uid

            self._last_hidden_comp = (uid, nm or uid)
        elif not hidden and self._last_hidden_comp and self._last_hidden_comp[0] == uid:
            self._last_hidden_comp = None

        # 1. Sync Entity tab chips
        if hasattr(self, "_companion_chips") and self._companion_chips:
            for u, _n, chip in self._companion_chips:
                if u == uid:
                    chip.blockSignals(True)
                    chip.setChecked(not hidden)
                    chip.blockSignals(False)
                    chip.restyle()
                    break

        # 2. Sync Codex tab cards
        if hasattr(self, "_refresh_codex_sync"):
            self._refresh_codex_sync()

        if hasattr(self, "_update_companion_tag"):
            self._update_companion_tag()

    def _unhide_last_unit(self) -> None:
        if not self._last_hidden_unit:
            return
        uid, _name = self._last_hidden_unit
        self._set_unit_hidden(uid, False)
        self._last_hidden_unit = None

    def _unhide_last_comp(self) -> None:
        if not self._last_hidden_comp:
            return
        uid, _name = self._last_hidden_comp
        self._set_companion_hidden(uid, False)
        self._last_hidden_comp = None

    def _center(self, widget):
        w = QtWidgets.QWidget()
        lay = QtWidgets.QHBoxLayout(w)
        lay.setContentsMargins(6, 2, 6, 2)
        lay.addWidget(widget)
        lay.addStretch(1)
        return w

    def _prewarm_icon_cache(self) -> None:
        """Warm the Items page's tile icons off the critical path (chunked
        in the items page support module — see support.prewarm_tiles)."""
        try:
            from .pages.items import support
            timer = support.prewarm_tiles(self)
            if timer is not None:
                self._prewarm_timer = timer
        except Exception:
            pass

    def closeEvent(self, e):
        try:
            self.hotkeys.clear()
            self._friends_timer.stop()
        except Exception:
            pass
        for attr in ("_friends_worker", "_col_worker", "_update_check_worker", "_update_dl_worker"):
            worker = getattr(self, attr, None)
            if worker is not None:
                try:
                    if hasattr(worker, "stop"):
                        worker.stop(800)
                    else:
                        worker.requestInterruption()
                        worker.quit()
                        worker.wait(800)
                except Exception:
                    pass
                setattr(self, attr, None)
        try:
            self._server_cleanup()
        except Exception:
            pass
        self.detach()
        super().closeEvent(e)