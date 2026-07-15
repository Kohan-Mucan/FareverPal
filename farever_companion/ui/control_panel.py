"""Control panel, the main window (Tactical Overlay shell).

A left nav rail + a QStackedWidget body: Overlays, Combat/DPS, Loot,
Map, Log. Attaches to the game (pure-read player locate on a worker thread),
configures the overlays, and runs the offline loot predictor. Owns the single
shared LiveModel so every overlay reads one source of truth.
"""
from __future__ import annotations

import datetime
import webbrowser

from PySide6 import QtCore, QtGui, QtWidgets

from . import theme
from . import components as C
from .overlay_manager import OverlayManager
from .game_attach import GameAttachmentController
from .workers import CallWorker
from .account import AccountMixin
from .pages.speedrun_page import SpeedrunPageMixin
from .pages.friends_page import FriendsPageMixin
from .pages.overlays import OverlaysPageMixin
from .pages.entity import EntityPageMixin
from .pages.codex import CodexPageMixin
from .pages.combat import CombatPageMixin
from .pages.loot import LootPageMixin
from .pages.map_page import MapPageMixin
from .pages.log import LogPageMixin
from .pages.collection import CollectionPageMixin
from .pages.server_page import ServerPageMixin
from ..config import Settings
from ..core.proc import backend_name
from ..core import updater
from ..data import icons
from .. import __version__
from .. import paths
from ..api import FareverAPI

# nav: key, icon, label
NAV = [
    ("overlays", "layers", "Overlays"),
    ("entity", "search", "Entity"),
    ("codex", "layout", "Bestiary"),
    ("combat", "swords", "Combat / DPS"),
    ("speedrun", "timer", "Speedrun"),
    ("loot", "box", "Loot"),
    ("collection", "archive", "Collection"),
    ("map", "map", "Map"),
    ("friends", "users", "Friends"),
    ("server", "broadcast", "Server Ping"),
    ("log", "terminal", "Log"),
]

def filtered_nav(settings: Settings) -> list:
    # Always show friends and collection, even if disable_web_features is True in saved settings.
    # Speedrun uploads are handled by disable_speedrun_upload separately.
    hidden = set()
    return [item for item in NAV if item[0] not in hidden]

CLASSES = ["Auto", "Warrior", "Rogue", "Mage", "Priest", "Off"]


class ControlPanel(AccountMixin, SpeedrunPageMixin, FriendsPageMixin,
                   OverlaysPageMixin, EntityPageMixin, CodexPageMixin,
                   CombatPageMixin, LootPageMixin, CollectionPageMixin,
                   MapPageMixin, ServerPageMixin, LogPageMixin,
                   QtWidgets.QMainWindow):
    def __init__(self, settings: Settings):
        super().__init__()
        self.s = settings
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

        self.setWindowTitle("Farever Pal — by Escanor")
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
        self._set_overlay_cards_enabled(False)   # gated until attached + located

        # system-wide hotkeys for loot-target selection (work while game-focused)
        from .hotkeys import GlobalHotkeys
        self.hotkeys = GlobalHotkeys(QtWidgets.QApplication.instance())
        self.hotkeys.triggered.connect(self._on_hotkey)
        self._register_hotkeys()

        # Friends presence: heartbeat + list refresh while signed in (the app
        # being open = "online (companion)"). Cheap; runs off the UI thread.
        self._friends_timer = QtCore.QTimer(self)
        self._friends_timer.setInterval(45_000)
        self._friends_timer.timeout.connect(self._friends_poll)
        # Always enable friend gating logic regardless of disable_web_features
        self._refresh_friends_gating()

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

        # Self-update: clear any leftover *.old from a prior update, then check
        # GitHub Releases (via the website) for a newer exe - off the UI thread.
        updater.cleanup_old()
        self._update_info = None
        self._update_check_worker: CallWorker | None = None
        self._update_dl_worker: CallWorker | None = None
        self._updating = False
        if self.s.auto_check_updates and self.s.api_base:
            base = self.s.api_base
            self._update_check_worker = CallWorker(
                lambda _w: updater.check(FareverAPI(base)))
            self._update_check_worker.done.connect(
                lambda _t, info: self._on_update_found(info))
            self._update_check_worker.start()

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

    def _brand_pixmap(self, size: int) -> QtGui.QPixmap:
        """The moustache brand mark for the header, tinted to the current accent on
        a transparent background. The bundled app_icon is opaque (moustache on a
        dark #0e0f13 square), so we derive an alpha mask from each pixel's distance
        to that background colour and refill it with the accent.
        """
        for name in ("app_icon.png", "app_icon.ico"):
            p = paths.assets_dir() / name
            if p.exists():
                img = QtGui.QImage(str(p))
                if not img.isNull():
                    return self._tint_brand(img, theme.ACCENT, size)
        return icons.ui_icon("radio", theme.ACCENT, size)

    @staticmethod
    def _tint_brand(img: QtGui.QImage, color: str, size: int) -> QtGui.QPixmap:
        scaled = img.convertToFormat(QtGui.QImage.Format_ARGB32).scaled(
            size, size, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
        accent = QtGui.QColor(color)
        ar, ag, ab = accent.red(), accent.green(), accent.blue()
        # If the source is already transparent (the moustache shape on a clear bg),
        # use its alpha as the mask; otherwise derive a mask from each pixel's
        # distance to the baked dark background (#0e0f13).
        transparent_src = scaled.pixelColor(0, 0).alpha() < 250
        br, bg, bb = 14, 15, 19
        out = QtGui.QImage(scaled.size(), QtGui.QImage.Format_ARGB32)
        out.fill(QtCore.Qt.transparent)
        for y in range(scaled.height()):
            for x in range(scaled.width()):
                c = scaled.pixelColor(x, y)
                if transparent_src:
                    a = c.alpha()
                else:
                    d = max(abs(c.red() - br), abs(c.green() - bg), abs(c.blue() - bb))
                    a = max(0, min(255, int(d * 1.6)))
                if a:
                    out.setPixelColor(x, y, QtGui.QColor(ar, ag, ab, a))
        return QtGui.QPixmap.fromImage(out)

    # --- sidebar ---------------------------------------------------------
    def _build_sidebar(self):
        bar = QtWidgets.QFrame()
        bar.setObjectName("Sidebar")
        bar.setFixedWidth(212)
        lay = QtWidgets.QVBoxLayout(bar)
        lay.setContentsMargins(14, 16, 14, 14)
        lay.setSpacing(4)

        brand = QtWidgets.QLabel("FAREVER PAL")
        brand.setObjectName("Brand")
        ver = QtWidgets.QLabel(f"by Escanor · v{__version__}")
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
            item = C.NavItem(key, icon, label)
            item.clicked.connect(self._select_nav)
            self._nav_items[key] = item
            lay.addWidget(item)

        lay.addStretch(1)

        cls_lbl = QtWidgets.QLabel("CURRENT CLASS")
        cls_lbl.setObjectName("FieldLabel")
        self.class_combo = QtWidgets.QComboBox()
        self.class_combo.addItems(CLASSES)
        self.class_combo.setCurrentText(self.s.player_class)
        self.class_combo.currentTextChanged.connect(lambda v: self._set("player_class", v))
        lay.addWidget(cls_lbl)
        lay.addWidget(self.class_combo)
        # (account moved to a top-right button in the top bar - keeps the sidebar uncluttered)
        return bar

    # --- main column -----------------------------------------------------
    def _build_main(self):
        col = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(col)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(self._build_topbar())
        self.stack = QtWidgets.QStackedWidget()
        self._pages = {}
        for key, _icon, _label in filtered_nav(self.s):
            page = getattr(self, f"_page_{key}")()
            self._pages[key] = self.stack.addWidget(self._scroll(page)) \
                if key not in ("log", "server") else self.stack.addWidget(page)
        v.addWidget(self.stack, 1)
        return col

    def _scroll(self, inner):
        sa = QtWidgets.QScrollArea()
        sa.setWidgetResizable(True)
        sa.setFrameShape(QtWidgets.QFrame.NoFrame)
        sa.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        sa.setWidget(inner)
        return sa

    def _build_topbar(self):
        bar = QtWidgets.QFrame()
        bar.setObjectName("TopBar")
        bar.setFixedHeight(58)
        lay = QtWidgets.QHBoxLayout(bar)
        lay.setContentsMargins(18, 0, 18, 0)
        lay.setSpacing(10)
        self._logo = logo = QtWidgets.QLabel()
        logo.setFixedSize(22, 22)
        logo.setPixmap(self._brand_pixmap(22))
        title = QtWidgets.QLabel("Farever Pal")
        title.setObjectName("Title")
        lay.addWidget(logo)
        lay.addWidget(title)
        lay.addSpacing(8)
        self.status = QtWidgets.QLabel()
        self.status.setObjectName("Mono")
        lay.addWidget(self.status)
        # Indeterminate "busy" bar shown only while the pure-read locate scan is
        # running (it can take a while; the scan gives no real %, so it's a
        # marquee, not a percentage). Hidden whenever we're not actively locating.
        self._locate_bar = QtWidgets.QProgressBar()
        self._locate_bar.setRange(0, 0)            # indeterminate / marquee
        self._locate_bar.setTextVisible(False)
        self._locate_bar.setFixedSize(96, 6)
        self._locate_bar.setObjectName("LocateBar")
        self._locate_bar.setStyleSheet(
            f"QProgressBar{{background:{theme.PANEL};border:0;border-radius:0;}}"
            f"QProgressBar::chunk{{background:{theme.ACCENT};border-radius:0;}}")
        self._locate_bar.setVisible(False)
        lay.addWidget(self._locate_bar)
        lay.addStretch(1)
        # account button (sign in / avatar + name)
        self._acct_container = self._build_account_host()
        lay.addWidget(self._acct_container)
        # Always show account container
        # if self.s.disable_web_features:
        #     self._acct_container.hide()

        # Attach/detach is fully automatic (watches for Farever) - no buttons or
        # toggle; the status label above is the single source of truth for state.
        self._refresh_account_button()
        self._set_status(False, "detached")
        return bar

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
    def _select_nav(self, key: str):
        order = [k for k, _, _ in filtered_nav(self.s)]
        for k, item in self._nav_items.items():
            item.setSelected(k == key)
        self.stack.setCurrentIndex(order.index(key))
        # Freshen friend presence/list when opening a page that shows it.
        if key in ("friends", "speedrun") and self.s.account_token:
            self._friends_poll()
        # Freshen the account's collection when opening the tracker.
        if key == "collection" and self.s.account_token:
            self._col_pull()
        # Refresh the active page content for profile-specific settings
        if key == "entity" and hasattr(self, "_refresh_entity_page"):
            self._refresh_entity_page()
        if key == "codex" and hasattr(self, "_refresh_codex_grid"):
            self._refresh_codex_grid()

    # ====================================================================
    #  Pages
    # ====================================================================
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
        for action, key in (("prev", self.s.hotkey_loot_prev),
                            ("next", self.s.hotkey_loot_next),
                            ("close", self.s.hotkey_loot_close),
                            ("sr_toggle", self.s.hotkey_speedrun_toggle),
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
            return
        ov = self.overlays.get("entity")
        if ov is None:
            self.log(f"Hotkey '{action}' — open the Entity overlay first.")
            return
        n = len(getattr(ov, "_enemies", [])) + len(getattr(ov, "_chests", []))
        self.log(f"Hotkey '{action}' ({n} targets)")
        if action == "prev":
            ov.select_prev()
        elif action == "next":
            ov.select_next()
        elif action == "close":
            ov.close_drops()

    # ====================================================================
    #  Settings sync
    # ====================================================================
    def _set(self, attr, value):
        setattr(self.s, attr, value)
        self.s.save()

    def _set_accent(self, color):
        """Highlight color: re-tint the whole app live, the control panel and
        every open overlay. Updates the global accent + QSS, re-applies it, and
        refreshes the widgets that paint the accent directly (not via QSS).
        """
        self.s.hud_accent = color
        self.s.save()
        theme.set_accent(color)
        app = QtWidgets.QApplication.instance()
        if app is not None:
            app.setStyleSheet(theme.QSS)        # control panel + inheriting overlays
        self._restyle_accent()                  # painted/captured-color widgets
        self.overlay_mgr.apply_accent(color)
        self._sync_accent_to_account()          # keep the web account's accent in sync

    def _restyle_accent(self):
        """Refresh control-panel widgets that hold the accent as a captured value
        or paint it directly (QSS re-apply alone doesn't repaint these).
        """
        from .components import (SectionHeader, Stepper, NavItem,
                                 SegmentedControl, OverlayCard, SliderRow,
                                 FilterChip)
        for sh in self.findChildren(SectionHeader):
            sh.set_color(theme.ACCENT)          # control-panel headers are all accent
        # widgets that captured the accent at construction -> re-tint each kind
        for cls in (Stepper, NavItem, SegmentedControl, OverlayCard, SliderRow,
                    FilterChip):
            for w in self.findChildren(cls):
                w.restyle()
        if hasattr(self, "_logo"):
            self._logo.setPixmap(self._brand_pixmap(22))
        # the account button's avatar placeholder tile is accent-tinted too
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

    # ====================================================================
    #  Process + overlays
    # ====================================================================
    def log(self, msg: str):
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        if hasattr(self, "log_view"):
            self.log_view.appendPlainText(f"{ts}  {msg}")

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

    def _request_overlay(self, key: str, on: bool):
        self.overlay_mgr.request(key, on)

    def detach(self):
        """Tear down the live session. The controller closes overlays (via its
        `detaching` signal) before stopping the model + closing the handle.
        """
        self.attach_ctl.detach()

    # ====================================================================
    #  Loot prediction
    # ====================================================================

    def _set_unit_hidden(self, uid: str, hidden: bool) -> None:
        profile = self.model.player_profile() if self.model else None
        self.s.toggle_unit_hidden(uid, hidden, profile)

        # 1. Sync Entity tab chips
        if hasattr(self, "_unit_chips"):
            for u, _n, chip in self._unit_chips:
                if u == uid:
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
        
        # 1. Update Settings
        if hasattr(self, "_unit_chips"):
            uids = [u for u, _n, _c in self._unit_chips]
            self.s.set_all_units_hidden(uids, hidden, profile)
            
            # 2. Sync Entity chips
            for _uid, _name, chip in self._unit_chips:
                chip.blockSignals(True)
                chip.setChecked(not hidden)
                chip.blockSignals(False)
                chip.restyle()
        
        # 3. Sync Codex cards
        if hasattr(self, "_refresh_codex_sync"):
            self._refresh_codex_sync()

        if hasattr(self, "_update_unit_tag"):
            self._update_unit_tag()

    def _center(self, widget):
        w = QtWidgets.QWidget()
        lay = QtWidgets.QHBoxLayout(w)
        lay.setContentsMargins(6, 2, 6, 2)
        lay.addWidget(widget)
        lay.addStretch(1)
        return w

    def closeEvent(self, e):
        # Stop the hotkeys first (releases global hooks)
        try:
            self.hotkeys.clear()
        except Exception:
            pass
        # Stop the friends timer
        try:
            self._friends_timer.stop()
        except Exception:
            pass
        # Wait for CallWorkers to finish
        try:
            if self._friends_worker is not None and self._friends_worker.isRunning():
                self._friends_worker.wait(1500)
        except Exception:
            pass
        try:
            if self._col_worker is not None and self._col_worker.isRunning():
                self._col_worker.wait(1500)   # let a final collection push land
        except Exception:
            pass
        try:
            if self._update_check_worker is not None and self._update_check_worker.isRunning():
                self._update_check_worker.wait(1500)
            # the download worker is left to finish if a swap is mid-flight
        except Exception:
            pass

        # Stop Server Diagnostics workers
        try:
            if hasattr(self, "_scan_worker") and self._scan_worker:
                self._scan_worker.stop()
                self._scan_worker.wait(1000)
            if hasattr(self, "_ping_worker") and self._ping_worker:
                if hasattr(self._ping_worker, "stop"):
                    self._ping_worker.stop()
                self._ping_worker.wait(1000)
        except Exception:
            pass

        # Detach from the game (this now waits for the locate worker internally)
        self.detach()
        super().closeEvent(e)