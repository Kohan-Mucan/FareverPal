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
            ("show_group_members", "Players"),
            ("show_compass", "Compass needle"), ("show_drop_window", "Show drop window"),
            ("auto_select_next_collectible", "Auto select next orb/chest"),
            ("limit_by_zone", "Limit by Zone"),
            ("entity_hide_collected", "Hide collected"),
        ]
        for i, (attr, label) in enumerate(toggles):
            t = C.LabeledToggle(label, getattr(self.s, attr))
            if attr == "show_compass":
                t.toggled.connect(self._set_show_compass)
                self.entity_compass_toggle = t
            else:
                t.toggled.connect(lambda on, a=attr: self._set(a, on))
            grid.addWidget(t, i // 2, i % 2)
        
        # 3-Way Button Group for Rift Display ("Off", "Active", "Always")
        raw_rift = getattr(self.s, "show_rift_timer", "Always")
        if isinstance(raw_rift, bool):
            curr_rift = "Always" if raw_rift else "Off"
        elif str(raw_rift).lower() in ("true", "always"):
            curr_rift = "Always"
        elif str(raw_rift).lower() in ("false", "off"):
            curr_rift = "Off"
        elif str(raw_rift).lower() == "active":
            curr_rift = "Active"
        else:
            curr_rift = "Always"
        
        rift_seg = C.SegmentedControl(["Off", "Active", "Always"], current=curr_rift)
        rift_seg.currentChanged.connect(lambda mode: self._set("show_rift_timer", mode))
        grid.addWidget(C.Field("Rift Schedule Display", rift_seg), len(toggles) // 2 + 1, 0, 1, 2)
        
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
            ("group_count", "Players shown", 1, 30, 1),
            ("orb_count", "Orbs shown", 1, 30, 1),
            ("icon_size", "Icon size (px)", 12, 64, 1),
            ("entity_font_size", "Font size (px)", 10, 24, 1),
            ("entity_width", "HUD Width (px)", 300, 800, 10),
            ("max_dist", "Max distance m (0 = off)", 0, 1000, 25),
        ]
        for i, (attr, label, lo, hi, step) in enumerate(steppers):
            st = C.Stepper(int(getattr(self.s, attr)), lo, hi, step)
            st.valueChanged.connect(lambda v_, a=attr: self._set(a, v_))
            srow.addWidget(C.Field(label, st), i // 2, i % 2)
        right.addLayout(srow)
        right.addSpacing(12)

        right.addWidget(C.SectionHeader("Loot Hotkeys (global · work while in-game)"))
        hk = QtWidgets.QGridLayout()
        hk.setHorizontalSpacing(12)
        hk.setVerticalSpacing(8)
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
        right.addLayout(hk)
        right.addStretch(1)

        cols.addLayout(left, 1)
        cols.addLayout(right, 1)
        v.addLayout(cols)
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

        # Search bar (matches Codex style)
        s_bar = QtWidgets.QHBoxLayout()
        self._unit_search = QtWidgets.QLineEdit()
        self._unit_search.setPlaceholderText("Search enemies…")
        self._unit_search.setClearButtonEnabled(True)
        self._unit_search.setFixedHeight(34)
        self._unit_search.setFixedWidth(250)
        self._unit_search.textChanged.connect(self._filter_unit_chips)
        s_bar.addWidget(self._unit_search)
        s_bar.addStretch(1)
        bv.addLayout(s_bar)

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
        self._unit_chips: list[tuple[list[str], str, C.FilterChip]] = []   # (uids, name, chip)

        grouped = {}
        for u in units.codex_unit_ids():
            uname = names.unit_name(u) or names.humanize(u)
            grouped.setdefault(uname, []).append(u)

        rows = sorted(grouped.items(), key=lambda t: t[0].lower())
        for uname, uids in rows:
            is_checked = False
            for u in uids:
                if u not in hidden_units:
                    is_checked = True
                    break
            chip = C.FilterChip(uname, is_checked)
            if len(uids) > 1:
                tip = f"{len(uids)} variants:\n" + "\n".join(uids)
            else:
                uid = uids[0]
                info = units.unit_info(uid) or {}
                tip = f"{uid} · {units.type_name(info.get('type'))}" \
                      + (f" · L{info['lvl']}" if info.get("lvl") else "")
            chip.setToolTip(tip)
            chip.toggled.connect(lambda on, us=uids: self._set_unit_hidden(us, not on))
            self._unit_chips.append((uids, uname, chip))
        self._filter_unit_chips("")
        self._update_unit_tag()
        return box

    def _filter_unit_chips(self, text: str) -> None:
        """Re-grid only the chips matching the search (name or raw id)."""
        if not hasattr(self, "_unit_chips") or not self._unit_chips or not hasattr(self, "_unit_grid"):
            return
        q = (text or "").strip().lower()
        self._unit_grid.parentWidget().setUpdatesEnabled(False)
        try:
            while self._unit_grid.count():
                it = self._unit_grid.takeAt(0)
                if it.widget():
                    it.widget().hide()

            if not q:
                return

            shown = 0
            for uids, uname, chip in self._unit_chips:
                is_match = q in uname.lower()
                if not is_match:
                    for u in uids:
                        if q in u.lower():
                            is_match = True
                            break
                if q and not is_match:
                    continue
                self._unit_grid.addWidget(chip, shown // _CHIP_COLS, shown % _CHIP_COLS)
                chip.show()
                shown += 1
        finally:
            self._unit_grid.parentWidget().setUpdatesEnabled(True)

    def _update_unit_tag(self) -> None:
        if not hasattr(self, "_unit_chips") or self._unit_chips is None or not hasattr(self, "_unit_header") or not self._unit_header:
            return
        profile = self.model.player_profile() if hasattr(self, "model") and self.model else None
        hidden_uids = set(self.s.get_entity_hidden_units(profile))
        hidden_chips = 0
        for uids, _, _ in self._unit_chips:
            if all(u in hidden_uids for u in uids):
                hidden_chips += 1
        total = len(self._unit_chips)
        self._unit_header.set_tag(f"{hidden_chips} HIDDEN" if hidden_chips else f"ALL {total} SHOWN")

    def _set_unit_hidden(self, uid_or_uids, hidden: bool) -> None:
        profile = self.model.player_profile() if hasattr(self, "model") and self.model else None
        uids = [uid_or_uids] if isinstance(uid_or_uids, str) else uid_or_uids
        for u in uids:
            self.s.toggle_unit_hidden(u, hidden, profile)
        self._update_unit_tag()

    def _set_all_units_hidden(self, hidden: bool) -> None:
        profile = self.model.player_profile() if hasattr(self, "model") and self.model else None
        if not hasattr(self, "_unit_chips") or not self._unit_chips:
            return
        all_uids = []
        for uids, _, _ in self._unit_chips:
            all_uids.extend(uids)
        self.s.set_all_units_hidden(all_uids, hidden, profile)
        for _, _, chip in self._unit_chips:
            chip.blockSignals(True)
            chip.setChecked(not hidden)
            chip.blockSignals(False)
            chip.restyle()
        self._update_unit_tag()

    def _refresh_entity_page(self) -> None:
        profile = self.model.player_profile() if hasattr(self, "model") and self.model else None
        if hasattr(self, "_unit_chips") and self._unit_chips:
            hidden_units = set(self.s.get_entity_hidden_units(profile))
            for uids, uname, chip in self._unit_chips:
                chip.blockSignals(True)
                is_checked = False
                for u in uids:
                    if u not in hidden_units:
                        is_checked = True
                        break
                chip.setChecked(is_checked)
                chip.blockSignals(False)
                chip.restyle()
            self._update_unit_tag()

        if hasattr(self, "_companion_chips") and self._companion_chips:
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

        # Search bar (matches Codex style)
        s_bar = QtWidgets.QHBoxLayout()
        self._comp_search = QtWidgets.QLineEdit()
        self._comp_search.setPlaceholderText("Search critters…")
        self._comp_search.setClearButtonEnabled(True)
        self._comp_search.setFixedHeight(34)
        self._comp_search.setFixedWidth(250)
        self._comp_search.textChanged.connect(self._filter_companion_chips)
        s_bar.addWidget(self._comp_search)
        s_bar.addStretch(1)
        bv.addLayout(s_bar)

        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll.setFixedHeight(280)
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
        if not hasattr(self, "_companion_chips") or not self._companion_chips or not hasattr(self, "_comp_grid"):
            return
        q = (text or "").strip().lower()
        self._comp_grid.parentWidget().setUpdatesEnabled(False)
        try:
            while self._comp_grid.count():
                it = self._comp_grid.takeAt(0)
                if it.widget(): it.widget().hide()

            if not q:
                return

            shown = 0
            for uid, uname, chip in self._companion_chips:
                if q not in uname.lower() and q not in uid.lower():
                    continue
                self._comp_grid.addWidget(chip, shown // _CHIP_COLS, shown % _CHIP_COLS)
                chip.show()
                shown += 1
        finally:
            self._comp_grid.parentWidget().setUpdatesEnabled(True)

    def _update_companion_tag(self) -> None:
        if not hasattr(self, "_companion_chips") or self._companion_chips is None or not hasattr(self, "_comp_header") or not self._comp_header:
            return
        profile = self.model.player_profile() if hasattr(self, "model") and self.model else None
        n = len(set(self.s.get_companion_hidden_units(profile)))
        total = len(self._companion_chips)
        self._comp_header.set_tag(f"{n} HIDDEN" if n else f"ALL {total} SHOWN")

    def _set_companion_hidden(self, uid: str, hidden: bool) -> None:
        profile = self.model.player_profile() if hasattr(self, "model") and self.model else None
        self.s.toggle_companion_hidden(uid, hidden, profile)
        self._update_companion_tag()

        # Auto-toggle off "Wild companions" if none are shown
        if hasattr(self, "_companion_chips") and self._companion_chips:
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
        if not hasattr(self, "_companion_chips") or not self._companion_chips:
            return
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
