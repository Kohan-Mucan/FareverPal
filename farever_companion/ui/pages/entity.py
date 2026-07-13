from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets
from .. import components as C
from ...data import names, units

_CHIP_COLS = 3      # per-enemy chip grid columns (3 fits the page viewport)


class EntityPageMixin:
    def _page_entity(self):
        page, v = self._page_container()
        cols = QtWidgets.QHBoxLayout()
        cols.setSpacing(24)

        left = QtWidgets.QVBoxLayout()
        left.setSpacing(12)
        left.addWidget(C.SectionHeader("Display Configuration"))
        grid = QtWidgets.QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(8)
        toggles = [
            ("show_enemies", "Enemies"), ("show_chests", "Chests"),
            ("show_gatherables", "Gatherables (Ores/Plants)"),
            ("show_companions", "Wild companions"),
            ("show_orbs", "Secret orbs"),
            ("show_group_members", "Group Members"),
            ("show_compass", "Compass needle"), ("show_drop_window", "Show drop window"),
            ("auto_select_next_collectible", "Auto select next orb/chest"),
            ("limit_by_zone", "Limit by range (300m)"),
            ("entity_hide_collected", "Hide collected"),
        ]
        for i, (attr, label) in enumerate(toggles):
            t = C.LabeledToggle(label, getattr(self.s, attr))
            if attr == "show_enemies":
                t.toggled.connect(lambda on: (self._set("show_enemies", on),
                                              self._enemy_filter_box.setVisible(on)))
            elif attr == "show_companions":
                t.toggled.connect(lambda on: (self._set("show_companions", on),
                                              self._companion_filter_box.setVisible(on)))
                self._comp_toggle = t
            elif attr == "show_compass":
                t.toggled.connect(self._set_show_compass)
                self.entity_compass_toggle = t
            else:
                t.toggled.connect(lambda on, a=attr: self._set(a, on))
            grid.addWidget(t, i // 2, i % 2)
        left.addLayout(grid)
        left.addStretch(1)

        right = QtWidgets.QVBoxLayout()
        right.setSpacing(12)
        right.addWidget(C.SectionHeader("Sizing & Metrics"))
        srow = QtWidgets.QGridLayout()
        srow.setHorizontalSpacing(12)
        srow.setVerticalSpacing(10)
        steppers = [
            ("enemy_count", "Enemies shown", 1, 30, 1),
            ("chest_count", "Chests shown", 1, 30, 1),
            ("gatherable_count", "Gatherables shown", 1, 30, 1),
            ("companion_count", "Companions shown", 1, 30, 1),
            ("orb_count", "Orbs shown", 1, 30, 1),
            ("icon_size", "Icon size (px)", 12, 64, 1),
            ("max_dist", "Max distance m (0 = off)", 0, 1000, 25),
        ]
        for i, (attr, label, lo, hi, step) in enumerate(steppers):
            st = C.Stepper(int(getattr(self.s, attr)), lo, hi, step)
            st.valueChanged.connect(lambda v_, a=attr: self._set(a, v_))
            srow.addWidget(C.Field(label, st), i // 2, i % 2)
        right.addLayout(srow)
        right.addStretch(1)

        cols.addLayout(left, 1)
        cols.addLayout(right, 1)
        v.addLayout(cols)

        v.addWidget(self._build_enemy_filters())
        self._enemy_filter_box.setVisible(self.s.show_enemies)

        v.addWidget(self._build_companion_filters())
        self._companion_filter_box.setVisible(self.s.show_companions)

        esc = C.SliderRow("Overlay scale", 70, 160, int(self.s.entity_scale * 100),
                          lambda x: f"{x / 100:.2f}x")
        esc.valueChanged.connect(self._set_entity_scale)
        v.addWidget(esc)

        v.addWidget(C.SectionHeader("Loot Hotkeys (global · work while in-game)"))
        hk = QtWidgets.QGridLayout()
        hk.setHorizontalSpacing(12)
        for i, (attr, label) in enumerate([
                ("hotkey_loot_prev", "Prev target"),
                ("hotkey_loot_next", "Next target"),
                ("hotkey_loot_close", "Close loot")]):
            edit = QtWidgets.QKeySequenceEdit(QtGui.QKeySequence(getattr(self.s, attr)))
            try:
                edit.setMaximumSequenceLength(1)
            except (AttributeError, TypeError):
                pass
            edit.keySequenceChanged.connect(lambda seq, a=attr: self._set_hotkey(a, seq))
            hk.addWidget(C.Field(label, edit), 0, i)
        v.addLayout(hk)
        v.addStretch(1)
        return page

    # --- enemy filters (Codex types + per-enemy toggles) -------------------
    def _build_enemy_filters(self) -> QtWidgets.QWidget:
        """Type chips + a searchable per-enemy toggle list, all sourced from the
        Codex (bestiary) types only - internals like Totems / Mounts /
        Environment never show up. Hidden together while Enemies is off."""
        box = QtWidgets.QWidget()
        self._enemy_filter_box = box
        bv = QtWidgets.QVBoxLayout(box)
        bv.setContentsMargins(0, 0, 0, 0)
        bv.setSpacing(12)

        self._unit_header = C.SectionHeader("Enemy Toggles (Codex)")
        bv.addWidget(self._unit_header)
        bar = QtWidgets.QHBoxLayout()
        bar.setSpacing(8)
        self._unit_search = QtWidgets.QLineEdit()
        self._unit_search.setPlaceholderText("Search enemies…")
        self._unit_search.setClearButtonEnabled(True)
        self._unit_search.textChanged.connect(self._filter_unit_chips)
        all_btn = QtWidgets.QPushButton("TOGGLE ALL")
        none_btn = QtWidgets.QPushButton("UNTOGGLE ALL")
        all_btn.setToolTip("Show every enemy")
        none_btn.setToolTip("Hide every enemy (then search + toggle the ones you want)")
        all_btn.clicked.connect(lambda: self._set_all_units_hidden(False))
        none_btn.clicked.connect(lambda: self._set_all_units_hidden(True))
        bar.addWidget(self._unit_search, 1)
        bar.addWidget(all_btn)
        bar.addWidget(none_btn)
        bv.addLayout(bar)

        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        scroll.setFixedHeight(280)
        body = QtWidgets.QWidget()
        self._unit_grid = QtWidgets.QGridLayout(body)
        self._unit_grid.setHorizontalSpacing(6)
        self._unit_grid.setVerticalSpacing(6)
        self._unit_grid.setAlignment(QtCore.Qt.AlignTop)
        scroll.setWidget(body)
        bv.addWidget(scroll)

        profile = self.model.player_profile() if hasattr(self, "model") and self.model else None
        hidden_units = set(self.s.get_entity_hidden_units(profile))
        self._unit_chips: list[tuple[str, str, C.FilterChip]] = []   # (uid, name, chip)
        rows = sorted(((names.unit_name(u) or names.humanize(u), u)
                       for u in units.codex_unit_ids()), key=lambda t: t[0].lower())
        for uname, uid in rows:
            chip = C.FilterChip(uname, uid not in hidden_units)
            info = units.unit_info(uid) or {}
            chip.setToolTip(f"{uid} · {units.type_name(info.get('type'))}"
                            + (f" · L{info['lvl']}" if info.get("lvl") else ""))
            chip.toggled.connect(lambda on, u=uid: self._set_unit_hidden(u, not on))
            self._unit_chips.append((uid, uname, chip))
        self._filter_unit_chips("")
        self._update_unit_tag()
        return box

    def _filter_unit_chips(self, text: str) -> None:
        """Re-grid only the chips matching the search (name or raw id)."""
        q = (text or "").strip().lower()
        self._unit_grid.parentWidget().setUpdatesEnabled(False)
        try:
            while self._unit_grid.count():
                it = self._unit_grid.takeAt(0)
                if it.widget():
                    it.widget().hide()
            shown = 0
            for uid, uname, chip in self._unit_chips:
                if q and q not in uname.lower() and q not in uid.lower():
                    continue
                self._unit_grid.addWidget(chip, shown // _CHIP_COLS, shown % _CHIP_COLS)
                chip.show()
                shown += 1
        finally:
            self._unit_grid.parentWidget().setUpdatesEnabled(True)

    def _update_unit_tag(self) -> None:
        profile = self.model.player_profile() if hasattr(self, "model") and self.model else None
        n = len(set(self.s.get_entity_hidden_units(profile)))
        total = len(self._unit_chips)
        self._unit_header.set_tag(f"{n} HIDDEN" if n else f"ALL {total} SHOWN")

    def _set_unit_hidden(self, uid: str, hidden: bool) -> None:
        profile = self.model.player_profile() if hasattr(self, "model") and self.model else None
        self.s.toggle_unit_hidden(uid, hidden, profile)
        self._update_unit_tag()

    def _set_all_units_hidden(self, hidden: bool) -> None:
        profile = self.model.player_profile() if hasattr(self, "model") and self.model else None
        uids = [u for u, _n, _c in self._unit_chips]
        self.s.set_all_units_hidden(uids, hidden, profile)
        for _uid, _name, chip in self._unit_chips:
            chip.blockSignals(True)
            chip.setChecked(not hidden)
            chip.blockSignals(False)
            chip.restyle()
        self._update_unit_tag()

    def _refresh_entity_page(self) -> None:
        profile = self.model.player_profile() if hasattr(self, "model") and self.model else None
        hidden_units = set(self.s.get_entity_hidden_units(profile))
        for uid, uname, chip in self._unit_chips:
            chip.blockSignals(True)
            chip.setChecked(uid not in hidden_units)
            chip.blockSignals(False)
            chip.restyle()
        self._update_unit_tag()

        hidden_comps = set(self.s.get_companion_hidden_units(profile))
        for uid, uname, chip in self._companion_chips:
            chip.blockSignals(True)
            chip.setChecked(uid not in hidden_comps)
            chip.blockSignals(False)
            chip.restyle()
        self._update_companion_tag()


    # --- companion filters ------------------------------------------------
    def _build_companion_filters(self) -> QtWidgets.QWidget:
        box = QtWidgets.QWidget()
        self._companion_filter_box = box
        bv = QtWidgets.QVBoxLayout(box)
        bv.setContentsMargins(0, 0, 0, 0)
        bv.setSpacing(12)

        self._comp_header = C.SectionHeader("Companion Toggles (Wild)")
        bv.addWidget(self._comp_header)
        bar = QtWidgets.QHBoxLayout()
        bar.setSpacing(8)
        self._comp_search = QtWidgets.QLineEdit()
        self._comp_search.setPlaceholderText("Search critters…")
        self._comp_search.setClearButtonEnabled(True)
        self._comp_search.textChanged.connect(self._filter_companion_chips)
        all_btn = QtWidgets.QPushButton("TOGGLE ALL")
        none_btn = QtWidgets.QPushButton("UNTOGGLE ALL")
        all_btn.clicked.connect(lambda: self._set_all_companions_hidden(False))
        none_btn.clicked.connect(lambda: self._set_all_companions_hidden(True))
        bar.addWidget(self._comp_search, 1)
        bar.addWidget(all_btn)
        bar.addWidget(none_btn)
        bv.addLayout(bar)

        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll.setFixedHeight(200)
        body = QtWidgets.QWidget()
        self._comp_grid = QtWidgets.QGridLayout(body)
        self._comp_grid.setHorizontalSpacing(6)
        self._comp_grid.setVerticalSpacing(6)
        self._comp_grid.setAlignment(QtCore.Qt.AlignTop)
        scroll.setWidget(body)
        bv.addWidget(scroll)

        profile = self.model.player_profile() if hasattr(self, "model") and self.model else None
        hidden_comps = set(self.s.get_companion_hidden_units(profile))
        self._companion_chips: list[tuple[str, str, C.FilterChip]] = []

        # companions = Critter units
        rows = []
        for u, r in units._units_by_id().items():
            if r.get("type") == units.COMPANION_TYPE and u not in units._TEMPLATE_IDS and not u.endswith("_Base"):
                rows.append((names.unit_name(u) or names.humanize(u), u))
        rows.sort(key=lambda t: t[0].lower())

        for uname, uid in rows:
            chip = C.FilterChip(uname, uid not in hidden_comps)
            chip.toggled.connect(lambda on, u=uid: self._set_companion_hidden(u, not on))
            self._companion_chips.append((uid, uname, chip))
        self._filter_companion_chips("")
        self._update_companion_tag()
        return box

    def _filter_companion_chips(self, text: str) -> None:
        q = (text or "").strip().lower()
        self._comp_grid.parentWidget().setUpdatesEnabled(False)
        try:
            while self._comp_grid.count():
                it = self._comp_grid.takeAt(0)
                if it.widget(): it.widget().hide()
            shown = 0
            for uid, uname, chip in self._companion_chips:
                if q and q not in uname.lower() and q not in uid.lower(): continue
                self._comp_grid.addWidget(chip, shown // _CHIP_COLS, shown % _CHIP_COLS)
                chip.show()
                shown += 1
        finally:
            self._comp_grid.parentWidget().setUpdatesEnabled(True)

    def _update_companion_tag(self) -> None:
        profile = self.model.player_profile() if hasattr(self, "model") and self.model else None
        n = len(set(self.s.get_companion_hidden_units(profile)))
        total = len(self._companion_chips)
        self._comp_header.set_tag(f"{n} HIDDEN" if n else f"ALL {total} SHOWN")

    def _set_companion_hidden(self, uid: str, hidden: bool) -> None:
        profile = self.model.player_profile() if hasattr(self, "model") and self.model else None
        self.s.toggle_companion_hidden(uid, hidden, profile)
        self._update_companion_tag()

        # Auto-toggle off "Wild companions" if none are shown
        hidden_units = set(self.s.get_companion_hidden_units(profile))
        total = len(self._companion_chips)
        if len(hidden_units) >= total and getattr(self.s, "show_companions", True):
            self._set("show_companions", False)
            if hasattr(self, "_comp_toggle"):
                self._comp_toggle.blockSignals(True)
                self._comp_toggle.setChecked(False)
                self._comp_toggle.blockSignals(False)
            if hasattr(self, "_companion_filter_box"):
                self._companion_filter_box.hide()

        # Sync Codex tab cards if they exist
        if hasattr(self, "_refresh_codex_sync"):
            self._refresh_codex_sync()

    def _set_all_companions_hidden(self, hidden: bool) -> None:
        profile = self.model.player_profile() if hasattr(self, "model") and self.model else None
        uids = [u for u, _n, _c in self._companion_chips]
        for uid in uids:
            self.s.toggle_companion_hidden(uid, hidden, profile)
        for _uid, _name, chip in self._companion_chips:
            chip.blockSignals(True)
            chip.setChecked(not hidden)
            chip.blockSignals(False)
            chip.restyle()
        self._update_companion_tag()

        # Sync Codex tab cards if they exist
        if hasattr(self, "_refresh_codex_sync"):
            self._refresh_codex_sync()
