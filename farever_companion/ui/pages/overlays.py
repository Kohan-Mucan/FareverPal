from __future__ import annotations

from PySide6 import QtCore, QtWidgets
from .. import theme
from ...data import icons
from .. import components as C
from ..overlay_manager import dev_scanner_available

class OverlaysPageMixin:
    def _page_overlays(self):
        page, v = self._page_container()

        # --- Section 1: ACTIVE OVERLAYS (2-column tactical grid) ---
        v.addWidget(C.SectionHeader("Active Overlays & Radars"))
        v.addSpacing(2)

        cards = QtWidgets.QGridLayout()
        cards.setSpacing(12)
        from ...config import experimental_enabled

        specs = [
            ("entity", "layers", "Entity HUD",
             "Nearby enemies, chests, companions, orbs & tactical sensors.",
             self.s.entity_bare, getattr(self.s, "entity_transparent", False), True, True),
            ("dungeon", "swords", "Dungeon HUD",
             "Instanced dungeon & rift radar: bosses, clones, loot and party.",
             self.s.dungeon_bare, getattr(self.s, "dungeon_transparent", False), True, True),
            ("map", "map", "Minimap Radar",
             "Real-time top-down POI radar of chests, foes, and gatherables.",
             self.s.minimap_bare, getattr(self.s, "minimap_transparent", False), True, True),
            ("dps", "swords", "DPS Meter",
             "Live damage meter, survivability metrics and party breakdowns.",
             self.s.dps_bare, False, True, False),
            ("speedrun", "timer", "Speedrun",
             "Run timer — start by hotkey, auto-stops on boss kill.",
             self.s.speedrun_bare, getattr(self.s, "speedrun_transparent", False), True, True),
            # experimental; release builds hide it (FAREVER_EXPERIMENTAL exposes it)
            *([("skills", "layers", "Skill Breakdown",
                "Per-skill damage table — icons, real names, crit%.",
                self.s.skills_bare, False, True, False)]
              if experimental_enabled() else []),
            ("compass", "crosshair", "Compass Needle",
             "Accent ring on minimap + 3D pointer overlay.",
             False, False, False, False),
            # git-ignored dev tool; the card only appears when
            # ai/dev_scanner/ exists on this machine
            *([("devscanner", "terminal", "Dev Scanner",
               "All-in-one scanner: units, aggro, elements, player, offsets, "
               "loot watch, full dump. Always visible.", False, False, False, False)]
              if dev_scanner_available() else []),
        ]

        for i, spec in enumerate(specs):
            if spec is None:
                continue
            key, icon, t, d, bare, trans, has_bare, has_trans = spec
            card = C.OverlayCard(icon, t, d,
                                 bare_checked=bare, has_bare=has_bare,
                                 transparent_checked=trans, has_transparent=has_trans)
            if key == "compass":
                card.toggled.connect(self._set_show_compass)
                card.set_checked_silent(self.s.show_compass)
                self.compass_toggle = card
            else:
                card.toggled.connect(lambda on, k=key: self._request_overlay(k, on))
                if has_bare:
                    card.bareToggled.connect(lambda on, k=key: self._set_bare(k, on))
                if has_trans:
                    card.transparentToggled.connect(lambda on, k=key: self._set_transparent(k, on))
                init_checked = bool(self.overlay_mgr.overlays.get(key) is not None) if (self.model and self.model.player_addr) else bool(getattr(self.s, f"open_overlay_{key}", False))
                card.set_checked_silent(init_checked)
            
            self._register_card(key, card)
            cards.addWidget(card, i // 3, i % 3)

        v.addLayout(cards)
        v.addSpacing(14)

        # --- Section 2: BEHAVIOR & APPEARANCE CARDS (2 side-by-side modules) ---
        bot_row = QtWidgets.QHBoxLayout()
        bot_row.setSpacing(12)
        bot_row.setAlignment(QtCore.Qt.AlignTop)

        # Left Module: BEHAVIOR & AUTOMATION
        beh_card = QtWidgets.QFrame()
        beh_card.setObjectName("OverlayCardFrame")
        beh_card.setStyleSheet(
            f"QFrame#OverlayCardFrame {{ background-color: {theme.PANEL}; "
            f"border: 1px solid {theme.BORDER}; border-radius: 6px; }}")
        beh_lay = QtWidgets.QVBoxLayout(beh_card)
        beh_lay.setContentsMargins(14, 12, 14, 14)
        beh_lay.setSpacing(10)

        beh_hdr = QtWidgets.QHBoxLayout()
        beh_hdr.setSpacing(8)
        beh_ico = QtWidgets.QLabel()
        beh_ico.setFixedSize(20, 20)
        beh_ico.setPixmap(icons.ui_icon("settings", theme.ACCENT, 20))
        beh_hdr.addWidget(beh_ico, 0, QtCore.Qt.AlignVCenter)
        beh_hdr.addWidget(C.SectionHeader("Behavior & Automation"), 1, QtCore.Qt.AlignVCenter)
        beh_lay.addLayout(beh_hdr)

        div1 = QtWidgets.QFrame()
        div1.setFixedHeight(1)
        div1.setStyleSheet(f"background:{theme.BORDER};border:none;")
        beh_lay.addWidget(div1)

        beh_items = [
            ("lock_overlays", "Lock (Click-through)", self.s.lock_overlays, self._set_lock),
            ("combat_click_through", "Auto-Lock in Combat", self.s.combat_click_through, self._set_combat_click_through),
            ("auto_hide_menus", "Auto-Hide in Menus", self.s.auto_hide_menus, self._set_auto_hide_menus),
        ]
        beh_grid = C.SegmentedGrid(beh_items, cols=3)
        beh_lay.addWidget(beh_grid)
        self.lock_toggle = beh_grid.btn("lock_overlays")
        self.combat_toggle = beh_grid.btn("combat_click_through")
        self.hide_toggle = beh_grid.btn("auto_hide_menus")

        # Mouse Snapping Row
        snap_lbl = QtWidgets.QLabel("MOUSE CENTERING (GAME SCREEN)")
        snap_lbl.setStyleSheet(
            f"font-family:'{theme.MONO_FONT}','Consolas',monospace;font-size:10px;"
            f"font-weight:700;letter-spacing:1px;color:{theme.ACCENT};background:transparent;")
        beh_lay.addWidget(snap_lbl)

        curr_snap = "Off"
        if self.s.snap_mouse_to_player:
            curr_snap = f"{self.s.mouse_snap_rate}ms"
        if curr_snap not in ("Off", "500ms", "250ms", "100ms"):
            curr_snap = "Off"

        def _on_snap_change(val: str) -> None:
            if val == "Off":
                self._set("snap_mouse_to_player", False)
                self.overlay_mgr.log.emit("Mouse snapping disabled.")
            else:
                rate = int(val.replace("ms", ""))
                self._set("snap_mouse_to_player", True)
                self._set("mouse_snap_rate", rate)
                self.overlay_mgr.log.emit(f"Mouse snapping ENABLED at {rate}ms rate.")
            self.overlay_mgr.update_combat_timer_rate()

        snap_seg = C.SegmentedControl(["Off", "500ms", "250ms", "100ms"], current=curr_snap)
        snap_seg.currentChanged.connect(_on_snap_change)
        self._snap_seg = snap_seg
        beh_lay.addWidget(snap_seg)
        bot_row.addWidget(beh_card, 1)

        # Right Module: APPEARANCE & STYLING
        app_card = QtWidgets.QFrame()
        app_card.setObjectName("OverlayCardFrame")
        app_card.setStyleSheet(
            f"QFrame#OverlayCardFrame {{ background-color: {theme.PANEL}; "
            f"border: 1px solid {theme.BORDER}; border-radius: 6px; }}")
        app_lay = QtWidgets.QVBoxLayout(app_card)
        app_lay.setContentsMargins(14, 12, 14, 14)
        app_lay.setSpacing(10)

        app_hdr = QtWidgets.QHBoxLayout()
        app_hdr.setSpacing(8)
        app_ico = QtWidgets.QLabel()
        app_ico.setFixedSize(20, 20)
        app_ico.setPixmap(icons.ui_icon("layout", theme.ACCENT, 20))
        app_hdr.addWidget(app_ico, 0, QtCore.Qt.AlignVCenter)
        app_hdr.addWidget(C.SectionHeader("Global Appearance"), 1, QtCore.Qt.AlignVCenter)
        app_lay.addLayout(app_hdr)

        div2 = QtWidgets.QFrame()
        div2.setFixedHeight(1)
        div2.setStyleSheet(f"background:{theme.BORDER};border:none;")
        app_lay.addWidget(div2)

        op = C.SliderRow("Overlay Opacity", 30, 100, int(self.s.opacity * 100),
                         lambda x: f"{x}%", default_val=90)
        op.valueChanged.connect(self._set_opacity)
        app_lay.addWidget(op)

        hl = C.ColorSwatch(self.s.hud_accent)
        hl.changed.connect(self._set_accent)
        app_lay.addWidget(C.Field("Highlight Accent Color", hl))
        bot_row.addWidget(app_card, 1)

        v.addLayout(bot_row)
        v.addStretch(1)
        return page

    def _set_bare(self, key: str, on: bool) -> None:
        attr = f"{key}_bare"
        self._set(attr, on)
        ov = self.overlay_mgr.overlays.get(key)
        if ov is not None and hasattr(ov, "set_bare"):
            ov.set_bare(on)

    def _set_transparent(self, key: str, on: bool) -> None:
        attr = f"{key}_transparent"
        self._set(attr, on)
        ov = self.overlay_mgr.overlays.get(key)
        if ov is not None and hasattr(ov, "set_transparent"):
            ov.set_transparent(on)

    def _set_lock(self, on):
        self.overlay_mgr.set_lock(on)

    def _set_snap_rate(self, rate: int, on: bool) -> None:
        if on:
            self._set("snap_mouse_to_player", True)
            self._set("mouse_snap_rate", rate)
            self.overlay_mgr.log.emit(f"Mouse snapping ENABLED at {rate}ms rate.")
        else:
            if self.s.mouse_snap_rate == rate:
                self._set("snap_mouse_to_player", False)
                self.overlay_mgr.log.emit("Mouse snapping disabled.")
        
        self.overlay_mgr.update_combat_timer_rate()
        
        # Sync toggles / segmented control
        active_rate = self.s.mouse_snap_rate if self.s.snap_mouse_to_player else None
        for r, t in ((500, getattr(self, "snap_500", None)),
                     (250, getattr(self, "snap_250", None)),
                     (100, getattr(self, "snap_100", None))):
            if t is not None:
                t.blockSignals(True)
                t.setChecked(active_rate == r)
                t.blockSignals(False)
        if getattr(self, "_snap_seg", None) is not None:
            self._snap_seg.blockSignals(True)
            self._snap_seg.setCurrentText(f"{active_rate}ms" if active_rate else "Off")
            self._snap_seg.blockSignals(False)

    def _set_auto_hide_menus(self, on: bool) -> None:
        self._set("auto_hide_menus", on)
        if not on:
            for key, ov in list(self.overlay_mgr.overlays.items()):
                if ov is not None and getattr(ov, "_auto_hidden", False):
                    ov.show()
                    ov._auto_hidden = False
            needle = self.overlay_mgr.tracker._needle
            if needle is not None and getattr(needle, "_auto_hidden", False):
                needle.show()
                needle._auto_hidden = False

    def _set_combat_click_through(self, on: bool) -> None:
        self._set("combat_click_through", on)
        self.overlay_mgr.set_combat_click_through(on)

    def _set_show_compass(self, on: bool) -> None:
        self._set("show_compass", on)
        tracker = self.overlay_mgr.tracker
        if on:
            if tracker.model is not None and tracker.s.track_kind and tracker.s.track_id:
                tracker._start()
        else:
            tracker.clear()
        
        # Synchronize toggles on both pages if loaded
        for t in (getattr(self, "compass_toggle", None), getattr(self, "entity_compass_toggle", None)):
            if t is not None and t.isChecked() != on:
                t.blockSignals(True)
                t.setChecked(on)
                t.blockSignals(False)

    def _set_dps_scale(self, v):
        sc = v / 100.0
        self._set("dps_scale", sc)
        for key in ("dps", "skills"):
            ov = self.overlays.get(key)
            if ov is not None and hasattr(ov, "apply_scale"):
                ov.apply_scale(sc)

    def _set_dps_mode(self, label):
        mode = {"Small": "small", "Medium": "medium", "Full": "default"}.get(label, "default")
        self._set("dps_mode", mode)
        ov = self.overlays.get("dps")
        if ov is not None and hasattr(ov, "apply_dps_mode"):
            ov.apply_dps_mode(mode)

    def _set_dps_columns(self):
        from .skill_table import COLUMN_ORDER
        keys = [k for k in COLUMN_ORDER
                if self._dps_col_toggles[k].isChecked()]
        self._set("dps_columns", keys)
        ov = self.overlays.get("skills")
        if ov is not None and hasattr(ov, "set_columns"):
            ov.set_columns(keys)

    def _set_overlay_cards_enabled(self, on: bool):
        if on:
            for key in ("entity", "dps", "skills", "map", "speedrun", "dungeon"):
                setting_name = f"open_overlay_{key}"
                if getattr(self.s, setting_name, False):
                    if self.overlay_mgr.overlays.get(key) is None:
                        self._request_overlay(key, True)
