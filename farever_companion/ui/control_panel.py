"""Control panel, the main window (Tactical Overlay shell).

A left nav rail + a QStackedWidget body: Overlays, Settings, Codex,
Combat & DPS, Speedrun, Gear, Craft, Collection, Friends, Server, Log. Attaches to the game
(pure-read player locate on a worker thread) and configures the overlays.
Owns the single shared LiveModel so every overlay reads one source of truth.
"""
from __future__ import annotations

import datetime
import os
import time
from collections import deque

from PySide6 import QtCore, QtGui, QtWidgets

from . import theme
from . import components as C
from .layout import make_scroll
from .page_history import PageHistory
from .overlay_manager import OverlayManager
from .game_attach import GameAttachmentController
from .workers import CallWorker
from .pages.combat_page import CombatPageMixin
from .pages.pb_page import PBPageMixin
from .pages.party_page import PartyPageMixin
from .pages.overlays import OverlaysPageMixin
from .pages.codex import CodexPageMixin
from .pages.craft import CraftPageMixin
from .pages.items import ItemPageMixin
from .pages.settings import SettingsPageMixin
from .pages.log import LogPageMixin
from .pages.server.page import ServerPageMixin

# Modularized auxiliary mixins
from .rift_box import RiftSidebarMixin
from .memory_reclaimer import _trim_working_set, MemoryReclaimerMixin
from .shutdown import ShutdownMixin
from .updater_ui import UpdaterMixin
from .entity_visibility import EntityVisibilityMixin
from .dps_background_tick import DpsBackgroundTickMixin, PowerKeepAlive

from ..config import Settings
from ..core.proc import backend_name
from ..core import updater
from ..data import icons
from .. import __version__

# The sidebar registry (keys, icons, labels) plus its two predicates now live
# in ui/nav_registry.py; re-exported here because the Settings → Pages tab and
# tests import them from this module.
from .nav_registry import (NAV, filtered_nav, dev_icons_enabled,  # noqa: F401
                           load_dev_page)


STEAM_RUN_URL = "steam://rungameid/3672400"

# Event types the app-wide filter (ControlPanel.eventFilter) reacts to, as
# plain ints. The filter is installed on the QApplication, so it runs for
# EVERY event the process delivers — building one Settings sub-tab fires
# thousands of them — and resolving the QEvent enum members plus building the
# membership tuple on each call was ~30% of every page build (measured
# 2026-09-27: 4020 calls / 25 ms of a 77 ms DPS tab build). Comparing hoisted
# ints through one frozenset test keeps the filter to a dict lookup.
_QEV_MOUSE_MOVE = int(QtCore.QEvent.MouseMove)
_QEV_MOUSE_PRESS = int(QtCore.QEvent.MouseButtonPress)
_QEV_MOUSE_RELEASE = int(QtCore.QEvent.MouseButtonRelease)
_QEV_KEY_PRESS = int(QtCore.QEvent.KeyPress)
_QEV_WHEEL = int(QtCore.QEvent.Wheel)
_QEV_IDLE_TYPES = frozenset((_QEV_MOUSE_MOVE, _QEV_MOUSE_PRESS,
                             _QEV_MOUSE_RELEASE, _QEV_KEY_PRESS, _QEV_WHEEL))


class ControlPanel(CombatPageMixin, PBPageMixin, PartyPageMixin,
                   OverlaysPageMixin, CodexPageMixin,
                   ItemPageMixin, CraftPageMixin,
                   SettingsPageMixin,
                   ServerPageMixin, LogPageMixin,
                   RiftSidebarMixin, MemoryReclaimerMixin,
                   UpdaterMixin, EntityVisibilityMixin,
                   DpsBackgroundTickMixin,
                   # closeEvent and the two saves it must do first. Last
                   # before QMainWindow so its super().closeEvent(e) chains
                   # straight through to the real window teardown.
                   ShutdownMixin,
                   QtWidgets.QMainWindow):
    def __init__(self, settings: Settings):
        super().__init__()
        self.s = settings
        self._power_keepalive = PowerKeepAlive()
        self._log_buffer: deque[str] = deque(maxlen=200)
        # Alert sounds report a dropped burst into this same log, so an audio
        # glitch names itself (which tone, how many, why) instead of having to
        # be tracked down from the noise.
        from ..runtime.sound import set_sound_logger
        set_sound_logger(self.log)
        self.attach_ctl = GameAttachmentController(settings, self)
        self.overlay_mgr = OverlayManager(settings, self)
        self.overlay_mgr.log.connect(self.log)
        self.overlay_mgr.request_page.connect(self._select_nav)
        self.overlay_mgr.request_page.connect(self._raise_self)
        self._nav_items: dict[str, C.NavItem] = {}
        self._last_hidden_unit: tuple[str, str] | None = None
        self._last_hidden_comp: tuple[str, str] | None = None
        self._nav_history = PageHistory(self._select_nav)
        QtWidgets.QApplication.instance().installEventFilter(self)

        self.setWindowTitle("Farever Pal")
        self.setMinimumSize(1000, 640)
        # 1400, not the old 1180: the nav sidebar takes 212px, and the Combat
        # page's own minimum is ~1130px, so anything under about 1340 opens
        # into a page the rail does not fit in. It used to open at 1180, which
        # is 160px short — the rail overflowed the page and was clipped at the
        # window edge, silently, taking the tail of the skills table with it.
        # The minimum stays at 1000: below ~1340 the Combat page hides the rail
        # and gives the width to the roster, which is a graceful answer rather
        # than a truncated one.
        self.resize(1400, 860)

        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        root = QtWidgets.QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_sidebar())
        root.addWidget(self._build_main(), 1)

        self._select_nav("overlays")

        self._refresh_nav_visibility()

        # Auto-attach watcher (in the controller)
        self.attach_ctl.log.connect(self.log)
        self.attach_ctl.status.connect(self._set_status)
        self.attach_ctl.locating.connect(self._set_locating)
        self.attach_ctl.located_changed.connect(self._set_overlay_cards_enabled)
        self.attach_ctl.located_changed.connect(self.overlay_mgr.on_located)
        self.attach_ctl.model_changed.connect(self._on_model_changed)
        self.attach_ctl.detaching.connect(self._on_detaching)
        self.attach_ctl.goto_log.connect(lambda: self._select_nav("log"))
        self.attach_ctl.start()

        QtCore.QTimer.singleShot(0, self.overlay_mgr.open_startup_overlays)

        # Memory reclamation timers
        self._last_interaction = time.monotonic()
        self._mem_timer = QtCore.QTimer(self)
        self._mem_timer.setInterval(10_000)
        self._mem_timer.timeout.connect(self._memory_tick)
        self._mem_timer.start()

        # DPS live tick: the tracker must drain even when neither the Top DPS
        # overlay (10 Hz tick) nor the Combat tab (page refresh) is on screen.
        # Without it events pile up undrained for seconds and archive as 0.0s
        # fights (a full burst consumed in one gulp). Runs on a plain worker
        # thread, NOT a Qt timer: Windows stretches/suspends a backgrounded
        # window's timers (the 2026-09-19 gulp) but cannot throttle an OS
        # thread — see DpsDrainWorker. 1 Hz is plenty (lull windows are 6s+);
        # sharing one tracker keeps every surface consistent, and the
        # tracker's non-blocking update guard serializes the two drivers.
        self._start_dps_drain()

        QtCore.QTimer.singleShot(3000, _trim_working_set)

        # Self-update check
        updater.cleanup_old()
        self._update_info = None
        self._update_check_worker: CallWorker | None = None
        self._update_dl_worker: CallWorker | None = None
        self._updating = False
        # The check reaches the release oracle and the install path needs a
        # frozen exe, so `check_updates` decides by MODE: "Always" polls even
        # here in a source run, "Packaged only" (the default) only where an
        # update could be installed, "Never" never - the opt-out a packaged
        # build could not express while this was a boolean. Settings -> Dev
        # owns the picker, and `_on_update_found` raises the sidebar pill. An
        # unset or unreadable value reads as the default, which is what keeps a
        # source run (and the test suite) off the network.
        if updater.should_check(getattr(self.s, "check_updates", None),
                                updater.is_frozen()):
            self._start_update_check()

        self.log(f"Read-only. Memory backend: {backend_name()}. Press Attach "
                 "(if another memory tool is running, unload it first to avoid "
                 "interference).")

        # Check for 3rd-party DLL conflicts on disk at startup (even when game is closed)
        try:
            from ..core.proc import detect_combat_bridge
            binfo = detect_combat_bridge(pid=0)
            if binfo.get("third_party_dinput8") or binfo.get("third_party_version"):
                which = "dinput8.dll and version.dll" if (binfo.get("third_party_dinput8") and binfo.get("third_party_version")) else ("dinput8.dll" if binfo.get("third_party_dinput8") else "version.dll")
                self.log(f"Combat Bridge: 🚨 Detected another mod using {which} in game directory ({binfo.get('game_dir')})")
        except Exception:
            pass

    @property
    def proc(self):
        return self.attach_ctl.proc

    @property
    def model(self):
        return self.attach_ctl.model

    @property
    def overlays(self) -> dict:
        return self.overlay_mgr.overlays


    # --- controller signal handlers ---------------------------------------
    def _set_locating(self, on: bool):
        self._locate_bar.setVisible(on)

    def _on_model_changed(self, model):
        if model is not None:
            if hasattr(model, "damage") and model.damage:
                model.damage.log_line = self.log
            if hasattr(model, "dps") and model.dps:
                model.dps.log_line = self.log
            # Game attached: exempt the process from Windows background
            # timer throttling while we hold it (DPS drain lives on timers).
            self._power_keepalive.acquire()
        self.overlay_mgr.set_model(model)
        idx = self.stack.currentIndex()
        order = [k for k, _, _ in NAV]
        if 0 <= idx < len(order):
            key = order[idx]
            if key == "entity" and hasattr(self, "_refresh_entity_page"):
                self._refresh_entity_page()

    def _on_detaching(self):
        self._power_keepalive.release()
        self.overlay_mgr.close_all()
    def _refresh_nav_visibility(self):
        """Show/hide nav items: the user-disabled pages (Settings → Pages)."""
        disabled = set(getattr(self.s, "disabled_pages", []) or [])
        for key, item in self._nav_items.items():
            if key == "log":
                item.setVisible(False)
            elif key == "devicons":
                # Owned by _apply_sidebar_element_visibility (show_devicons +
                # the dev gate): the generic rule below would force it visible
                # whenever it isn't in disabled_pages, making the toggle
                # decorative.
                continue
            else:
                item.setVisible(key not in disabled)
        if not hasattr(self, "stack"):
            return
        idx = self.stack.currentIndex()
        order = [k for k, _, _ in NAV]
        if 0 <= idx < len(order):
            current_key = order[idx]
            if current_key in disabled:
                self._select_nav("overlays")

    # --- sidebar ---------------------------------------------------------
    def _build_sidebar(self):
        bar = QtWidgets.QFrame()
        bar.setObjectName("Sidebar")
        bar.setFixedWidth(212)
        lay = QtWidgets.QVBoxLayout(bar)
        lay.setContentsMargins(14, 16, 14, 14)
        lay.setSpacing(4)
        self._sidebar_lay = lay

        brand = QtWidgets.QLabel("FAREVER PAL")
        brand.setObjectName("Brand")
        ver = QtWidgets.QLabel(f'<a href="https://kohan-mucan.github.io/FareverPal/" style="color:#bdc8d1; text-decoration:none;">Git Version v{__version__}</a>')
        ver.setOpenExternalLinks(True)
        ver.setObjectName("Mono")
        lay.addWidget(brand)
        lay.addWidget(ver)

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
        self._nav_insert_at = lay.count()

        lay.addStretch(1)
        lay.addWidget(self._build_rift_box())
        lay.addWidget(self._build_launch_button())
        lay.addWidget(self._build_sidebar_footer())

        self._apply_sidebar_element_visibility()
        self._set_status(False, "detached")
        return bar

    def _apply_sidebar_element_visibility(self) -> None:
        """Settings → Pages → Sidebar Elements: toggle visibility."""
        targets = (
            ("show_rift_box", getattr(self, "_rift_box", None)),
            ("show_launch_button", getattr(self, "_launch_btn", None)),
            ("show_log", getattr(self, "_log_btn", None)),
        )
        for attr, w in targets:
            if w is not None:
                w.setVisible(bool(getattr(self.s, attr, True)))

        log_item = self._nav_items.get("log")
        if log_item is not None:
            log_item.setVisible(False)

        dev_item = self._nav_items.get("devicons")
        if dev_item is not None:
            dev_item.setVisible(bool(getattr(self.s, "show_devicons", False)) and dev_icons_enabled())

    def _launch_game(self):
        QtGui.QDesktopServices.openUrl(QtCore.QUrl(STEAM_RUN_URL))
        self.log("Launching Farever via Steam…")

    def _build_launch_button(self):
        """Launch Farever via Steam."""
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
        """Bottom of the sidebar: the attach-status strip."""
        foot = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(foot)
        v.setContentsMargins(0, 10, 0, 0)
        v.setSpacing(8)
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

        self._locate_bar = QtWidgets.QProgressBar()
        self._locate_bar.setRange(0, 0)
        self._locate_bar.setTextVisible(False)
        self._locate_bar.setFixedSize(64, 6)
        self._locate_bar.setObjectName("LocateBar")
        self._locate_bar.setStyleSheet(
            f"QProgressBar{{background:{theme.PANEL};border:0;border-radius:0;}}"
            f"QProgressBar::chunk{{background:{theme.ACCENT};border-radius:0;}}")
        self._locate_bar.setVisible(False)
        row.addWidget(self._locate_bar)
        row.addStretch(1)

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
        self._page_last_used: dict[str, float] = {}
        for key, _icon, _label in NAV:
            self._pages[key] = self.stack.addWidget(QtWidgets.QWidget())
            self._pages_built[key] = False
        v.addWidget(self.stack, 1)
        return col

    def _scroll(self, inner):
        return make_scroll(inner)


    # --- nav -------------------------------------------------------------
    # NAV key -> the builder that lazily assembles that page. Spelled out by
    # hand rather than rebuilt at the call site as f"_page_{key}": a name scan
    # (ai/workspace/buffy/dead_scan.py and the hygiene censuses) cannot see a
    # name that lives only inside an f-string, so these builders read as dead /
    # "kept alive only by a doc" when they are in fact how each page is built.
    _PAGE_BUILDERS = {
        "overlays": "_page_overlays",
        "settings": "_page_settings",
        "codex": "_page_codex",
        "combat": "_page_combat",
        "pb": "_page_pb",
        "party": "_page_party",
        "gear": "_page_gear",
        "craft": "_page_craft",
        "server": "_page_server",
        "log": "_page_log",
        "devicons": "_page_devicons",
    }

    def _nav_order(self) -> list:
        return [k for k, _, _ in NAV]

    def _select_nav(self, target: str):
        key, _sep, sub = target.partition(":")
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
        cur_target = self._current_page()
        self._nav_history.record(cur_target, target)
        if not self._pages_built.get(key, False):
            builder = self._PAGE_BUILDERS.get(key)
            page_fn = getattr(self, builder, None) if builder else None
            if page_fn:
                if key not in ("log", "server", "gear", "craft", "settings", "devicons", "combat"):
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

        if key == "combat" and hasattr(self, "_set_combat_metric"):
            self._set_combat_metric("damage")

        self._page_last_used[key] = time.monotonic()
        self._evict_idle_pages(key)

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
                self._on_settings_tab(sub, record_history=False)
            elif key == "codex" and hasattr(self, "_codex_tabs"):
                tab, _sep, view = sub.partition(":")
                if self._codex_tabs.currentText() != tab:
                    self._codex_tabs.setCurrentText(tab)
                    self._codex_tab_changed(0, record_history=False)
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

        if key == "entity" and hasattr(self, "_refresh_entity_page"):
            self._refresh_entity_page()
        if key == "settings" and hasattr(self, "_refresh_settings_page"):
            try:
                self._refresh_settings_page()
            except Exception:
                pass
        if key == "combat" and hasattr(self, "_select_live_combat"):
            try:
                self._select_live_combat()
            except Exception:
                pass
        if key == "pb" and hasattr(self, "_pb_refresh"):
            try:
                self._pb_refresh()
            except Exception:
                pass
        if key == "codex" and hasattr(self, "_refresh_codex_grid"):
            if not sub and hasattr(self, "_sync_codex_to_current_zone"):
                self._sync_codex_to_current_zone()
                self._refresh_codex_grid()
        if key == "server" and hasattr(self, "_fetch_steam_player_count"):
            self._fetch_steam_player_count()

        if key in ("gear", "craft") and not getattr(self, "_tiles_prewarmed", False):
            self._tiles_prewarmed = True
            QtCore.QTimer.singleShot(400, self._prewarm_icon_cache)

        self._refresh_rift_box_selected()

    def _page_devicons(self):
        """Lazily build the Dev Icons preview page.

        The page is a dev-only tool that lives outside this package
        (build_tools/dev/), so it is loaded from its file path — never a package
        import, and absent entirely in a frozen build.
        """
        try:
            devicons = load_dev_page("devicons")
            return devicons.DevIconsPage(self, self.s)
        except Exception as exc:
            lbl = QtWidgets.QLabel(f"Dev Icons page failed to load:\n{exc}")
            lbl.setObjectName("Muted")
            lbl.setWordWrap(True)
            return lbl

    def _set_page_disabled(self, key: str, disabled: bool) -> None:
        """Settings → Pages: toggle a page enabled/disabled."""
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
        order = [k for k, _, _ in NAV]
        for k in order[order.index(key) + 1:]:
            if k in self._nav_items:
                i = self._sidebar_lay.indexOf(self._nav_items[k])
                if i != -1:
                    return i
        return getattr(self, "_nav_insert_at", self._sidebar_lay.count())

    def _ensure_nav_item(self, key: str) -> None:
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

    # --- Settings sync ---------------------------------------------------
    def _set(self, attr, value):
        setattr(self.s, attr, value)
        self.s.save()

    def _set_accent(self, color):
        self.s.hud_accent = color
        self.s.save()
        theme.set_accent(color)
        app = QtWidgets.QApplication.instance()
        if app is not None:
            app.setStyleSheet(theme.QSS)
        self._restyle_accent()
        self.overlay_mgr.apply_accent(color)

    def _restyle_accent(self):
        from .components import (SectionHeader, Stepper, NavItem,
                                 SegmentedControl, OverlayCard, SliderRow,
                                 FilterChip)
        from .chips import ChoiceChips
        for sh in self.findChildren(SectionHeader):
            sh.set_color(theme.ACCENT)
        for cls in (Stepper, NavItem, SegmentedControl, OverlayCard, SliderRow,
                    FilterChip, ChoiceChips):
            for w in self.findChildren(cls):
                w.restyle()
        for w in self.findChildren(QtWidgets.QWidget):
            w.update()

    def _set_opacity(self, v):
        self.overlay_mgr.set_opacity(v)

    def _set_lock(self, on):
        self.overlay_mgr.set_lock(on)

    # --- Process + overlays ----------------------------------------------
    def log(self, msg: str):
        """One Activity Log line: ``HH:MM:SS  <msg>``.

        Thread-safe by construction. Every capture engine logs from its own
        thread — the bridge's UDP loop writes the first ten hits and a health
        line every 50 events, the DLL-free scanner and the tracker's roster
        lines log from theirs — and a QWidget may only be written on the GUI
        thread. That off-thread `appendPlainText` is what corrupted Qt's state
        and faulted later in an unrelated allocation (access violation in
        QThread.__init__ while the bridge was mid-fight, 2026-09-25). A foreign
        thread therefore hands the message to the queued bridge and returns;
        the GUI thread re-enters here with it and does the widget write.
        """
        if QtCore.QThread.currentThread() is not self.thread():
            self._threadsafe_log(msg)
            return
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        line = f"{ts}  {msg}"
        self._log_buffer.append(line)
        # Hand the tail to the dev crash log's pre-allocated buffer, so a
        # hard crash lands next to what the app was DOING — the Activity Log
        # lives only in RAM, and the 2026-09-28 stack overflows each died
        # beside a dump that named nothing. The buffer write is a list slice
        # + one bytes build (a few µs); the fault path itself only reads it.
        if os.environ.get("FAREVER_DEV_CRASH_LOG", "").strip():
            try:
                from ..runtime import crash_log
                crash_log.set_activity_tail(list(self._log_buffer))
            except Exception:
                pass
        if self._widget_alive(getattr(self, "log_view", None)):
            self.log_view.appendPlainText(line)

    def _set_status(self, attached: bool, text: str):
        dot = theme.GOOD if attached else theme.NEUTRAL
        self.status.setText(f"●  {text}")
        self.status.setStyleSheet(
            f"color:{dot};font-family:'{theme.MONO_FONT}','Consoles';font-size:11px;")

    def _raise_self(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

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

    def eventFilter(self, obj, event):
        """Mouse BACK/FORWARD walks pane, sub-tab, and page history universally.

        Installed on the QApplication, so this is the hottest Python in the
        process — it sees every event from every widget, including the ones a
        half-built Settings page fires while it lays itself out. Only a mouse
        press can change behaviour, so everything else returns straight after
        the idle-timestamp bookkeeping instead of walking the isinstance /
        super() tail (QObject.eventFilter's default return, which is False).
        """
        et = int(event.type())
        if et in _QEV_IDLE_TYPES:
            self._last_interaction = time.monotonic()
        if et != _QEV_MOUSE_PRESS or not isinstance(obj, QtWidgets.QWidget):
            return False
        btn = event.button()
        if btn == QtCore.Qt.MouseButton.XButton1:
            win = obj.window()
            if isinstance(win, QtWidgets.QDialog):
                win.reject()
                return True
            # Check widget hierarchy upwards for any widget defining consume_pane_back()
            w = obj
            while w is not None:
                fn = getattr(w, "consume_pane_back", None)
                if callable(fn) and fn():
                    return True
                if w.isWindow():
                    break
                w = w.parentWidget()
            if win is self:
                cur = self.stack.currentWidget() if hasattr(self, "stack") and self.stack else None
                if cur is not None and getattr(cur, "consume_pane_back", lambda: False)():
                    return True
                if hasattr(self, "consume_pane_back") and self.consume_pane_back():
                    return True
                self._nav_history.back(self._current_page())
                return True
        elif btn == QtCore.Qt.MouseButton.XButton2 and obj.window() is self:
            self._nav_history.forward(self._current_page())
            return True
        return False

    def _current_page(self) -> str | None:
        """The active page key, with its sub-view when one is readable."""
        if not hasattr(self, "stack") or self.stack is None:
            return None
        order = self._nav_order()
        cur = self.stack.currentIndex()
        if not (0 <= cur < len(order)):
            return None
        key = order[cur]
        if key == "craft":
            tabs = getattr(self, "_craft_tabs", None)
            if tabs is not None and self._widget_alive(tabs):
                tab = (getattr(self, "_craft_active_tab", None) or tabs.currentText())
                if tab:
                    base_tab = "Craft List" if tab.startswith("Craft List") else tab
                    return f"craft:{base_tab}"
        elif key == "gear":
            tabs = getattr(self, "_items_tabs", None)
            if tabs is not None and self._widget_alive(tabs):
                tab = tabs.currentText()
                if tab:
                    return f"gear:{tab}"
        elif key == "codex":
            tabs = getattr(self, "_codex_tabs", None)
            if tabs is not None and self._widget_alive(tabs):
                tab = tabs.currentText()
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
        elif key == "settings":
            tabs = getattr(self, "_settings_tabs", None)
            if tabs is not None and self._widget_alive(tabs):
                tab = tabs.currentText()
                if tab:
                    return f"settings:{tab.lower()}"
        return key

    def _request_overlay(self, key: str, on: bool):
        self.overlay_mgr.request(key, on)

    def detach(self):
        """Tear down the live session."""
        self._last_hidden_unit = None
        self._last_hidden_comp = None
        self._last_hidden_chest_orb = None
        self.attach_ctl.detach()
        _trim_working_set()


    def _prewarm_icon_cache(self) -> None:
        try:
            from .pages.items import support
            timer = support.prewarm_tiles(self)
            if timer is not None:
                self._prewarm_timer = timer
        except Exception:
            pass

