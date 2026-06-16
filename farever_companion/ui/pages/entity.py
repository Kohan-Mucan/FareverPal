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
            ("show_companions", "Wild companions"), ("show_gatherables", "Gatherables"),
            ("show_orbs", "Secret orbs"), ("show_drops", "Closest drops"),
            ("enemies_only", "Enemies only"), ("class_only", "Class-relevant only"),
            ("show_compass", "Compass needle"), ("show_drop_window", "Show drop window"),
        ]
        for i, (attr, label) in enumerate(toggles):
            t = C.LabeledToggle(label, getattr(self.s, attr))
            if attr == "show_enemies":
                t.toggled.connect(lambda on: (self._set("show_enemies", on),
                                              self._enemy_filter_box.setVisible(on)))
            elif attr == "show_compass":
                t.toggled.connect(self._set_show_compass)
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
            ("companion_count", "Companions shown", 1, 30, 1),
            ("orb_count", "Orbs shown", 1, 30, 1),
            ("icon_size", "Icon size (px)", 12, 64, 1),
            ("level", "Predict level", 1, 60, 1),
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

        bv.addWidget(C.SectionHeader("Enemy Type Filter (Codex)",
                                     tag="UNCHECKED = HIDDEN"))
        hidden_types = set(self.s.entity_hidden_types)
        chips = QtWidgets.QGridLayout()
        chips.setHorizontalSpacing(6)
        chips.setVerticalSpacing(6)
        for i, (tid, tname) in enumerate(units.codex_types()):
            chip = C.FilterChip(tname, tid not in hidden_types)
            chip.setToolTip(tid)
            chip.toggled.connect(lambda on, t=tid: self._set_type_hidden(t, not on))
            chips.addWidget(chip, i // 6, i % 6)
        bv.addLayout(chips)

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

        hidden_units = set(self.s.entity_hidden_units)
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

    def _update_unit_tag(self) -> None:
        n = len(set(self.s.entity_hidden_units))
        total = len(self._unit_chips)
        self._unit_header.set_tag(f"{n} HIDDEN" if n else f"ALL {total} SHOWN")

    def _set_unit_hidden(self, uid: str, hidden: bool) -> None:
        cur = set(self.s.entity_hidden_units)
        (cur.add if hidden else cur.discard)(uid)
        self.s.entity_hidden_units = sorted(cur)
        self.s.save()
        self._update_unit_tag()

    def _set_all_units_hidden(self, hidden: bool) -> None:
        self.s.entity_hidden_units = sorted(u for u, _n, _c in self._unit_chips) \
            if hidden else []
        self.s.save()
        for _uid, _name, chip in self._unit_chips:
            chip.blockSignals(True)
            chip.setChecked(not hidden)
            chip.blockSignals(False)
            chip.restyle()
        self._update_unit_tag()

    def _set_type_hidden(self, utype: str, hidden: bool) -> None:
        cur = set(self.s.entity_hidden_types)
        (cur.add if hidden else cur.discard)(utype)
        self.s.entity_hidden_types = sorted(cur)
        self.s.save()

    def _set_show_compass(self, on: bool) -> None:
        self._set("show_compass", on)
        tracker = self.overlay_mgr.tracker
        if on:
            if tracker.model is not None and tracker.s.track_kind and tracker.s.track_id:
                tracker._start()
        else:
            tracker._timer.stop()
            if tracker._needle is not None:
                tracker._needle.hide()
