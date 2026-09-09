"""DPS Settings module - split from ui/pages/settings.py.
Imports the single shared namespace (constants, theme, Qt aliases)
from ._shared so moved method bodies keep their original globals.
"""

from ._shared import *  # noqa: F401,F403


class _MultiSegmentedControl(QtWidgets.QFrame):
    """Like components.SegmentedControl but non-exclusive: any number of
    segments can be checked at once. Module-local because the Layers tab's
    gatherables filter (flower / ore type picker) is its only user - it used
    to live in ui/components.py as MultiSegmentedControl."""

    optionToggled = QtCore.Signal(str, bool)

    def __init__(self, options: list[str], parent=None,
                 colors: dict[str, str] | None = None):
        super().__init__(parent)
        self.setObjectName("Cell")
        self._colors = colors or {}
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(3, 3, 3, 3)
        lay.setSpacing(3)
        self._btns: dict[str, QtWidgets.QPushButton] = {}
        for opt in options:
            b = QtWidgets.QPushButton(opt)
            b.setObjectName("SegmentBtn")
            b.setCheckable(True)
            b.setCursor(QtCore.Qt.PointingHandCursor)
            b.setStyleSheet(self._btn_qss(opt))
            lay.addWidget(b, 1)
            b.toggled.connect(lambda on, o=opt: self.optionToggled.emit(o, on))
            self._btns[opt] = b

    def _btn_qss(self, opt: str = "") -> str:
        return theme.segment_btn_qss(self._colors.get(opt))

    def set_checked(self, opt: str, on: bool, silent: bool = False) -> None:
        b = self._btns.get(opt)
        if b is None:
            return
        if silent:
            b.blockSignals(True)
            b.setChecked(on)
            b.blockSignals(False)
        else:
            b.setChecked(on)

    def checked(self, opt: str) -> bool:
        b = self._btns.get(opt)
        return b.isChecked() if b is not None else False

    def restyle(self) -> None:
        for opt, b in self._btns.items():
            b.setStyleSheet(self._btn_qss(opt))


class SettingsLayersMixin:
    """Layers matrix tab: HUD/Map/Dungeon layer toggles, link buttons and
the gatherables filter picker."""

    def _tab_layers(self):
        w, lv = self._tab_page()
        self._build_layer_matrix(lv)
        lv.addStretch(1)
        return w

    # --- world layers matrix (2-column tactical cards) --------------------
    def _switch_with_label(self, name: str, toggle: QtWidgets.QWidget) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        hl = QtWidgets.QHBoxLayout(w)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(5)
        lbl = QtWidgets.QLabel(name)
        lbl.setStyleSheet(
            f"font-family:'{theme.MONO_FONT}','Consolas',monospace;font-size:10px;"
            f"font-weight:700;letter-spacing:0.5px;color:{theme.MUTED};background:transparent;")
        hl.addWidget(lbl)
        hl.addWidget(toggle)
        return w

    def _build_layer_matrix(self, v) -> None:
        # (The MASTER LINK bar — a bulk "link every row" switch and the caption
        # explaining it — was removed 2026-09-19: it sat above the matrix as a
        # block nobody used. Each row keeps its own LINK button, so HUD and
        # Minimap layers are still mirrored individually.)

        # 2-Column Tactical Layer Cards Grid
        grid = QtWidgets.QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(10)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)

        self._settings_toggles: list[tuple[str, C.ToggleSwitch]] = []
        self._matrix_rows: dict[str, dict] = {}
        self._row_for_attr: dict[str, dict] = {}
        self._expanders: dict[str, tuple] = {}

        grid_row = 0
        for group, layers in _LAYER_GROUPS:
            g_lbl = QtWidgets.QLabel(f"// {group.upper()}")
            g_lbl.setStyleSheet(
                f"color:{theme.ACCENT};font-family:'{theme.MONO_FONT}',"
                "'Consolas',monospace;font-size:11px;font-weight:700;"
                "letter-spacing:1.5px;background:transparent;padding-top:8px;padding-bottom:2px;")
            grid.addWidget(g_lbl, grid_row, 0, 1, 2)
            grid_row += 1

            for i, (key, label, color, hud_a, map_a, dg_a) in enumerate(layers):
                card = QtWidgets.QFrame()
                card.setObjectName("LayerCard")
                card.setStyleSheet(
                    f"QFrame#LayerCard {{ background-color: {theme.PANEL}; "
                    f"border: 1px solid {theme.BORDER}; border-radius: 6px; }}"
                    f"QFrame#LayerCard:hover {{ border-color: {theme.with_alpha(theme.ACCENT, 90)}; }}")
                card_lay = QtWidgets.QVBoxLayout(card)
                card_lay.setContentsMargins(12, 10, 12, 10)
                card_lay.setSpacing(6)

                # Main Row: Icon + Title/Desc + Controls
                top_row = QtWidgets.QHBoxLayout()
                top_row.setSpacing(10)

                # Icon in rounded badge
                icon_badge = QtWidgets.QFrame()
                icon_badge.setFixedSize(30, 30)
                icon_badge.setStyleSheet(
                    f"background: {theme.with_alpha(color, 25)}; "
                    f"border: 1px solid {theme.with_alpha(color, 80)}; border-radius: 4px;")
                ib_lay = QtWidgets.QHBoxLayout(icon_badge)
                ib_lay.setContentsMargins(0, 0, 0, 0)
                ib_lay.setAlignment(QtCore.Qt.AlignCenter)
                ic = self._layer_icon(key, color)
                ib_lay.addWidget(ic)
                top_row.addWidget(icon_badge)

                # Title & Description
                text_lay = QtWidgets.QVBoxLayout()
                text_lay.setSpacing(1)
                nm = QtWidgets.QLabel(label)
                nm.setStyleSheet(
                    f"color:{theme.TEXT};font-size:15px;font-weight:700;background:transparent;")
                desc = QtWidgets.QLabel(_LAYER_DESCS.get(key, ""))
                desc.setStyleSheet(
                    f"color:{theme.MUTED};font-size:11px;background:transparent;")
                desc.setWordWrap(True)
                text_lay.addWidget(nm)
                text_lay.addWidget(desc)
                top_row.addLayout(text_lay, 1)

                # Switch controls
                ctrl_lay = QtWidgets.QHBoxLayout()
                ctrl_lay.setSpacing(8)
                ctrl_lay.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)

                hud_t = self._layer_toggle(hud_a) if hud_a else None
                map_t = self._layer_toggle(map_a) if map_a else None
                dg_t = self._layer_toggle(dg_a) if dg_a else None

                if hud_t:
                    ctrl_lay.addWidget(self._switch_with_label("HUD", hud_t))
                if map_t:
                    ctrl_lay.addWidget(self._switch_with_label("MAP", map_t))
                if dg_t:
                    ctrl_lay.addWidget(self._switch_with_label("DUNGEON", dg_t))

                # Rows that exist on both HUD and Minimap get a per-row LINK
                # button so flipping one surface mirrors the other.
                link_btn = None
                if hud_a and map_a:
                    link_btn = QtWidgets.QPushButton("LINK")
                    link_btn.setCheckable(True)
                    link_btn.setCursor(QtCore.Qt.PointingHandCursor)
                    link_btn.setFixedWidth(44)
                    link_btn.setStyleSheet(
                        f"QPushButton {{ background: transparent; "
                        f"border: 1px solid {theme.BORDER}; border-radius: 3px; "
                        f"color: {theme.DIM}; font-size: 9px; font-weight: 800; "
                        f"letter-spacing: 0.5px; padding: 2px 0; }}"
                        f"QPushButton:checked {{ border-color: {theme.ACCENT}; "
                        f"color: {theme.ACCENT}; }}"
                        f"QPushButton:hover {{ border-color: {theme.MUTED}; }}")
                    link_btn.toggled.connect(
                        lambda on, k=key: self._on_row_link(k, on))
                    ctrl_lay.addWidget(link_btn)

                top_row.addLayout(ctrl_lay)
                card_lay.addLayout(top_row)

                self._matrix_rows[key] = {
                    "key": key,
                    "icon": ic,
                    "hud_attr": hud_a,
                    "map_attr": map_a,
                    "hud": hud_t,
                    "map": map_t,
                    "link": link_btn,
                    "linkable": bool(hud_a and map_a),
                }
                if hud_a:
                    self._row_for_attr[hud_a] = self._matrix_rows[key]
                if map_a:
                    self._row_for_attr[map_a] = self._matrix_rows[key]

                # Gatherables: collapsible per-type picker
                if key == "gatherables":
                    exp_btn = QtWidgets.QPushButton("▾ Filter Herbs & Ores")
                    exp_btn.setCursor(QtCore.Qt.PointingHandCursor)
                    exp_btn.setStyleSheet(
                        f"QPushButton {{ background: transparent; border: none; color: {theme.GOLD}; "
                        f"font-size: 11px; font-weight: 700; text-align: left; padding: 2px 0px; }}"
                        f"QPushButton:hover {{ color: {theme.TEXT}; }}")
                    exp_btn.clicked.connect(lambda: self._toggle_gather_expander())
                    card_lay.addWidget(exp_btn)
                    self._gather_caret = exp_btn

                    self._gather_expander = self._build_gatherable_expander()
                    self._gather_expander.setVisible(False)
                    card_lay.addWidget(self._gather_expander)

                grid.addWidget(card, grid_row + (i // 2), i % 2)

            # A group with an odd number of layers leaves its last row half
            # empty; close it here, while this group's rows are still the
            # last ones in the grid (see span_trailing_row).
            span_trailing_row(grid, from_row=grid_row)
            grid_row += (len(layers) + 1) // 2

        v.addLayout(grid)
        v.addSpacing(6)
        self._fold_stale_master_link()
        self._sync_link_buttons()

        # Callout summary card
        info_box = QtWidgets.QFrame()
        info_box.setObjectName("Cell")
        info_box.setStyleSheet(
            f"QFrame#Cell {{ background-color: {theme.with_alpha(theme.PANEL_HI, 30)}; "
            f"border: 1px solid {theme.BORDER}; border-radius: 6px; }}")
        info_lay = QtWidgets.QVBoxLayout(info_box)
        info_lay.setContentsMargins(12, 10, 12, 10)
        info_lay.setSpacing(4)

        info_hdr = QtWidgets.QLabel("LAYER SYNCHRONIZATION & BEHAVIOR")
        info_hdr.setStyleSheet(
            f"font-family:'{theme.MONO_FONT}','Consolas',monospace;font-size:11px;"
            f"font-weight:700;letter-spacing:1px;color:{theme.GOLD};background:transparent;")
        info_lay.addWidget(info_hdr)

        note = QtWidgets.QLabel(
            "• Overlay Switches: Toggle HUD, Minimap Radar, and Dungeon tracking independently per layer.\n"
            "• Gatherable Filtering: Expand/Collapse to filter individual Flower & Ore types.\n"
            "• Instance HUD: Dungeon HUD automatically activates inside instanced dungeons and rifts.")
        note.setStyleSheet(f"color:{theme.TEXT};font-size:13px;line-height:1.5;background:transparent;")
        note.setWordWrap(True)
        info_lay.addWidget(note)
        v.addWidget(info_box)

    # --- gatherables expander ---------------------------------------------
    def _build_gatherable_expander(self) -> QtWidgets.QWidget:
        """Per-type picker revealed by clicking the Gatherables matrix row.

        Flowers and Ore (ore-orb nodes) are each a smooth multi-select segmented
        control (same flat feel as the Entity Loot Filter); any number of types
        can be on. A type is drawn only when its key is present in
        `show_gatherable_types` AND the Gatherables HUD/Map switch is on."""
        box = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(box)
        lay.setContentsMargins(10, 4, 10, 4)
        lay.setSpacing(8)
        enabled = set(getattr(self.s, "show_gatherable_types", []))
        self._gather_segs: list[_MultiSegmentedControl] = []
        self._gather_disp_to_key: dict[str, str] = {}
        for grp, keys in _GATHER_GROUPS:
            hdr = QtWidgets.QLabel(grp)
            hdr.setStyleSheet(
                f"color:{theme.ACCENT};font-family:'{theme.MONO_FONT}',"
                "'Consolas',monospace;font-size:10px;font-weight:700;"
                "letter-spacing:2px;background:transparent;")
            lay.addWidget(hdr)
            opts = [get_display_name(k) for k in keys]
            col_map = {}
            for k in keys:
                disp = get_display_name(k)
                col_map[disp] = theme.FLOWER_COLOR.get(k.lower(), theme.GOOD if grp == "Flowers" else theme.ORANGE)
            seg = _MultiSegmentedControl(opts, colors=col_map)
            for k in keys:
                disp = get_display_name(k)
                seg.set_checked(disp, k in enabled, silent=True)
                self._gather_disp_to_key[disp] = k
            seg.optionToggled.connect(
                lambda o, on: self._on_gather_type_toggle(
                    self._gather_disp_to_key[o], on))
            lay.addWidget(seg)
            self._gather_segs.append(seg)
        return box

    def _toggle_gather_expander(self) -> None:
        if not hasattr(self, "_gather_expander"):
            return
        vis = not self._gather_expander.isVisible()
        self._gather_expander.setVisible(vis)
        if hasattr(self, "_gather_caret"):
            self._gather_caret.setText("▴ Hide Herbs & Ores" if vis else "▾ Filter Herbs & Ores")

    def _on_gather_type_toggle(self, key: str, on: bool) -> None:
        cur = list(getattr(self.s, "show_gatherable_types", []))
        if on and key not in cur:
            cur.append(key)
        elif not on and key in cur:
            cur.remove(key)
        # Keep the on-disk list in canonical ALL_GATHER_TYPES order.
        from ....config import ALL_GATHER_TYPES
        self._set("show_gatherable_types",
                  [t for t in ALL_GATHER_TYPES if t in cur])

    def _layer_icon(self, key: str, color: str) -> QtWidgets.QLabel:
        """Leading cell for a matrix row: the real atlas_minimap_01.webp
        sprite with a `color` outline; falls back to the loose icons folder
        (assets/icons); a red dot marks a layer whose icon is missing so the
        gap is obvious instead of silently blending in."""
        lbl = QtWidgets.QLabel()
        lbl.setFixedSize(16, 16)
        lbl.setAlignment(QtCore.Qt.AlignCenter)
        glyph = _RECOLOR_GLYPHS.get(key)
        if glyph:
            pm = icons.ui_icon(glyph, color, 16)
            if pm and not pm.isNull():
                lbl.setPixmap(pm)
                return lbl
        emoji = _LAYER_EMOJI.get(key)
        if emoji:
            lbl.setPixmap(icons.emoji_pixmap(emoji, 16, color))
            return lbl
        sprite = _LAYER_SPRITES.get(key)
        if sprite and icons.has_icon("minimap", sprite):
            lbl.setPixmap(icons.marker(sprite, 16, color))
            return lbl
        if sprite:
            fb = icons.asset_icon(sprite, 16)
            if fb and not fb.isNull():
                lbl.setPixmap(fb)
                return lbl
        pm = QtGui.QPixmap(16, 16)
        pm.fill(QtGui.QColor(0, 0, 0, 0))
        p = QtGui.QPainter(pm)
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(QtGui.QColor(color or theme.ACCENT))
        p.drawEllipse(3, 3, 10, 10)
        p.end()
        lbl.setPixmap(pm)
        return lbl

    # --- layer switches ---------------------------------------------------
    def _dash(self) -> QtWidgets.QLabel:
        d = QtWidgets.QLabel("—")
        d.setAlignment(QtCore.Qt.AlignCenter)
        d.setStyleSheet(
            f"color:{theme.DIM};font-size:12px;background:transparent;")
        return d

    def _layer_toggle(self, attr):
        """A HUD / MAP / DUNGEON switch for one layer. Dashes when the surface
        has no such layer. Minimap toggles also refresh the minimap canvas."""
        if attr is None:
            return self._dash()
        t = C.ToggleSwitch(bool(getattr(self.s, attr)))
        t.toggled.connect(lambda on, a=attr: self._on_layer_toggle(a, on))
        self._settings_toggles.append((attr, t))
        return t

    def _on_layer_toggle(self, attr: str, on: bool) -> None:
        if attr.startswith("minimap_"):
            self._set_minimap_layer(attr, on)
        else:
            self._set(attr, on)
        # Mirror to the row's other switch when this row is linked.
        row = self._row_for_attr.get(attr)
        if row is None or not row["linkable"]:
            return
        if row["key"] not in getattr(self.s, "layer_links", []):
            return
        if attr == row["hud_attr"]:
            self._set_minimap_layer(row["map_attr"], on)
            row["map"].set_checked_silent(on)
        else:
            self._set(row["hud_attr"], on)
            row["hud"].set_checked_silent(on)

    # --- linking ----------------------------------------------------------
    def _fold_stale_master_link(self) -> None:
        """One-time: a settings.json still carrying the removed MASTER LINK
        switch keeps its intent — every linkable row linked — instead of
        leaving rows linked yet uneditable."""
        if not getattr(self.s, "layer_link_master", False):
            return
        linkable = sorted(k for k, r in self._matrix_rows.items() if r["linkable"])
        self._set("layer_links", linkable)
        self._set("layer_link_master", False)

    def _on_row_link(self, key: str, on: bool) -> None:
        keys = list(getattr(self.s, "layer_links", []))
        if on and key not in keys:
            keys.append(key)
        elif not on and key in keys:
            keys.remove(key)
        self._set("layer_links", sorted(keys))

    def _sync_link_buttons(self) -> None:
        links = set(getattr(self.s, "layer_links", []))
        for key, r in getattr(self, "_matrix_rows", {}).items():
            b = r.get("link")
            if b is None or not _is_valid(b):
                continue
            try:
                b.blockSignals(True)
                b.setChecked(key in links)
                b.blockSignals(False)
                if hasattr(b, "_restyle"):
                    b._restyle()
            except (RuntimeError, Exception):
                pass

