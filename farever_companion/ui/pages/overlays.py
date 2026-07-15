from __future__ import annotations

from PySide6 import QtCore, QtWidgets
from .. import components as C
from ...data import names
from ...data import icons

class OverlaysPageMixin:
    def _page_overlays(self):
        page, v = self._page_container()
        cards = QtWidgets.QGridLayout()
        cards.setSpacing(12)
        from ...config import experimental_enabled
        specs = [
            ("entity", "layers", "Entity & Loot",
             "Nearby enemies, chests, and their drop tables.", self.s.entity_bare, True),
            ("dps", "swords", "DPS Meter",
             "Big live self-DPS, survivability, recent cycles.", self.s.dps_bare, True),
            # experimental; release builds hide it (FAREVER_EXPERIMENTAL exposes it)
            *([("skills", "layers", "Skill Breakdown",
                "Per-skill damage table — icons, real names, crit%. (experimental)", self.s.skills_bare, True)]
              if experimental_enabled() else []),
            ("map", "map", "Minimap",
             "Top-down POI radar of chests, foes, and gatherables.", self.s.minimap_bare, True),
            ("speedrun", "timer", "Speedrun",
             "Run timer — start by hotkey, auto-stops on boss kill.", self.s.speedrun_bare, True),
            None, # spacer to keep Compass under Map
            ("compass", "radio", "Compass Needle",
             "Accent ring on minimap + 3D pointer overlay.", False, False),
        ]
        for i, spec in enumerate(specs):
            if spec is None:
                continue
            key, icon, t, d, bare, has_bare = spec
            lbl = "Transparent" if key == "speedrun" else "Borderless"
            card = C.OverlayCard(icon, t, d, bare_checked=bare, has_bare=has_bare, bare_label=lbl)
            if key == "compass":
                card.toggled.connect(self._set_show_compass)
                card.set_checked_silent(self.s.show_compass)
                self.compass_toggle = card
            else:
                card.toggled.connect(lambda on, k=key: self._request_overlay(k, on))
                if has_bare:
                    card.bareToggled.connect(lambda on, k=key: self._set_bare(k, on))
            
            self._register_card(key, card)
            cards.addWidget(card, i // 3, i % 3)
        v.addLayout(cards)
        v.addSpacing(16)
        v.addWidget(C.SectionHeader("Lock / Auto-Hide UI"))
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(12)
        self.lock_toggle = C.LabeledToggle("Lock overlays (click-through)", self.s.lock_overlays)
        self.lock_toggle.toggled.connect(self._set_lock)
        self.combat_toggle = C.LabeledToggle("Auto lock while in combat", self.s.combat_click_through)
        self.combat_toggle.toggled.connect(self._set_combat_click_through)
        self.hide_toggle = C.LabeledToggle("Auto-hide overlays when in menus", self.s.auto_hide_menus)
        self.hide_toggle.toggled.connect(self._set_auto_hide_menus)
        row.addWidget(self.lock_toggle)
        row.addWidget(self.combat_toggle)
        row.addWidget(self.hide_toggle)
        v.addLayout(row)

        v.addWidget(C.SectionHeader("Mouse Snapping Behavior"))
        desc = QtWidgets.QLabel("Center cursor in game (prevents drifting to second monitor)")
        desc.setObjectName("Muted")
        desc.setWordWrap(True)
        v.addWidget(desc)

        snap_lay = QtWidgets.QHBoxLayout()
        snap_lay.setSpacing(12)
        snap_active = self.s.snap_mouse_to_player
        self.snap_500 = C.LabeledToggle("500ms", snap_active and self.s.mouse_snap_rate == 500)
        self.snap_500.toggled.connect(lambda on: self._set_snap_rate(500, on))
        self.snap_250 = C.LabeledToggle("250ms", snap_active and self.s.mouse_snap_rate == 250)
        self.snap_250.toggled.connect(lambda on: self._set_snap_rate(250, on))
        self.snap_100 = C.LabeledToggle("100ms", snap_active and self.s.mouse_snap_rate == 100)
        self.snap_100.toggled.connect(lambda on: self._set_snap_rate(100, on))
        snap_lay.addWidget(self.snap_500)
        snap_lay.addWidget(self.snap_250)
        snap_lay.addWidget(self.snap_100)
        v.addLayout(snap_lay)

        v.addWidget(C.SectionHeader("Appearance"))
        op = C.SliderRow("Overlay opacity", 30, 100, int(self.s.opacity * 100),
                         lambda x: f"{x}%", default_val=90)
        op.valueChanged.connect(self._set_opacity)
        v.addWidget(op)
        hl = C.ColorSwatch(self.s.hud_accent)
        hl.changed.connect(self._set_accent)
        v.addWidget(C.Field("Highlight color", hl))
        v.addStretch(1)
        return page

    def _set_bare(self, key: str, on: bool) -> None:
        attr = f"{key}_bare"
        self._set(attr, on)
        ov = self.overlay_mgr.overlays.get(key)
        if ov is not None and hasattr(ov, "set_bare"):
            ov.set_bare(on)

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
        
        # Sync toggles
        active_rate = self.s.mouse_snap_rate if self.s.snap_mouse_to_player else None
        for r, t in ((500, self.snap_500), (250, self.snap_250), (100, self.snap_100)):
            if t is not None:
                t.blockSignals(True)
                t.setChecked(active_rate == r)
                t.blockSignals(False)

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


    def _set_entity_scale(self, v):
        sc = v / 100.0
        self._set("entity_scale", sc)
        ov = self.overlays.get("entity")
        if ov is not None and hasattr(ov, "set_scale"):
            ov.set_scale(sc)

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
        self.overlay_mgr.set_cards_enabled(on)
        if on:
            for key in ("entity", "dps", "skills", "map", "speedrun"):
                setting_name = f"open_overlay_{key}"
                if getattr(self.s, setting_name, False):
                    if self.overlay_mgr.overlays.get(key) is None:
                        self._request_overlay(key, True)
