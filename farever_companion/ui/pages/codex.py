from __future__ import annotations

import re
from PySide6 import QtCore, QtGui, QtWidgets
from .. import components as C
from .. import theme
from ...data import codex, names

_CARD_COLS = 7


class CodexPageMixin:
    def _page_codex(self):
        page, v = self._page_container()
        
        # Tabs for Regions
        regions = codex.region_names()
        self._codex_tabs = C.SegmentedControl(list(regions.values()), 
                                             current=list(regions.values())[0])
        self._codex_tabs.currentChanged.connect(self._on_tab_changed)
        v.addWidget(self._codex_tabs)

        # 1. Initialize Grid FIRST to avoid crash during restyle/refresh
        self._codex_scroll = QtWidgets.QScrollArea()
        self._codex_scroll.setWidgetResizable(True)
        self._codex_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        self._codex_scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self._codex_scroll.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        
        body = QtWidgets.QWidget()
        self._codex_grid = QtWidgets.QGridLayout(body)
        self._codex_grid.setHorizontalSpacing(4)
        self._codex_grid.setVerticalSpacing(4)
        self._codex_grid.setContentsMargins(0, 0, 0, 0)
        self._codex_grid.setAlignment(QtCore.Qt.AlignTop | QtCore.Qt.AlignLeft)
        self._codex_scroll.setWidget(body)
        
        self._codex_cards: list[CodexUnitCard] = []

        # 2. Header Controls Bar
        bar = QtWidgets.QHBoxLayout()
        bar.setSpacing(10)
        bar.setAlignment(QtCore.Qt.AlignTop)
        
        # Left side: Search Box (Standard Height)
        self._codex_search = QtWidgets.QLineEdit()
        self._codex_search.setPlaceholderText("Search bestiary…")
        self._codex_search.setClearButtonEnabled(True)
        self._codex_search.setFixedWidth(250)
        self._codex_search.setFixedHeight(36) 
        self._codex_search.textChanged.connect(self._on_search_changed)
        bar.addWidget(self._codex_search)

        # STANDALONE Grid/Outline Button (1 row, with border)
        btn_qss = (
            f"QPushButton{{background:transparent; border:1px solid {theme.BORDER}; color:{theme.MUTED}; "
            f"font-size:12px; font-weight:bold; padding: 4px;}}"
            f"QPushButton:hover{{color:{theme.TEXT}; border-color:{theme.DIM};}}"
            f"QPushButton:checked, QPushButton:pressed{{background:{theme.with_alpha(theme.ACCENT, 42)}; "
            f"color:{theme.ACCENT}; border-color:{theme.ACCENT};}}"
        )
        self._btn_compact = QtWidgets.QPushButton("Grid/Outline")
        self._btn_compact.setCheckable(True)
        self._btn_compact.setFixedSize(110, 36)
        self._btn_compact.setStyleSheet(btn_qss)
        bar.addWidget(self._btn_compact)
        
        bar.addStretch(1)
        
        # Right side: 3x2 Grid inside a Styled Cell Frame (Tab Look)
        ctrl_container = QtWidgets.QFrame()
        ctrl_container.setObjectName("Cell")
        ctrl_grid = QtWidgets.QGridLayout(ctrl_container)
        ctrl_grid.setContentsMargins(4, 4, 4, 4)
        ctrl_grid.setSpacing(4)
        
        # Group button style (No individual borders, matching main box)
        grp_btn_qss = (
            f"QPushButton{{background:transparent; border:0; color:{theme.MUTED}; "
            f"font-size:12px; font-weight:bold; padding: 4px;}}"
            f"QPushButton:hover{{color:{theme.TEXT};}}"
            f"QPushButton:checked, QPushButton:pressed{{background:{theme.with_alpha(theme.ACCENT, 42)}; "
            f"color:{theme.ACCENT}; border:0;}}"
        )
        
        # Column 1: Bulk Actions
        self._btn_codex_enable = QtWidgets.QPushButton("Enable All")
        self._btn_codex_enable.setFixedSize(110, 36)
        self._btn_codex_enable.setStyleSheet(grp_btn_qss)
        self._btn_codex_enable.clicked.connect(lambda: self._codex_prime_bulk(True))
        ctrl_grid.addWidget(self._btn_codex_enable, 0, 0)
        
        self._btn_codex_disable = QtWidgets.QPushButton("Disable All")
        self._btn_codex_disable.setFixedSize(110, 36)
        self._btn_codex_disable.setStyleSheet(grp_btn_qss)
        self._btn_codex_disable.clicked.connect(lambda: self._codex_prime_bulk(False))
        ctrl_grid.addWidget(self._btn_codex_disable, 1, 0)

        # Inline Confirmation Buttons
        self._bulk_visible_target = True
        self._btn_codex_confirm = QtWidgets.QPushButton("CONFIRM?")
        self._btn_codex_confirm.setFixedSize(110, 36)
        self._btn_codex_confirm.setObjectName("Accent")
        self._btn_codex_confirm.setVisible(False)
        self._btn_codex_confirm.clicked.connect(lambda: self._codex_set_all(self._bulk_visible_target))
        ctrl_grid.addWidget(self._btn_codex_confirm, 0, 0)

        self._btn_codex_cancel = QtWidgets.QPushButton("CANCEL")
        self._btn_codex_cancel.setFixedSize(110, 36)
        self._btn_codex_cancel.setObjectName("Outline")
        self._btn_codex_cancel.setVisible(False)
        self._btn_codex_cancel.clicked.connect(self._codex_cancel_bulk)
        ctrl_grid.addWidget(self._btn_codex_cancel, 1, 0)
        
        # Status Filter Buttons (Columns 2-3)
        self._status_btns = QtWidgets.QButtonGroup(self)
        self._status_btns.setExclusive(True)
        self._status_widgets = {}
        
        # Arrangement: R0: All, Nearby | R1: Visible, Hidden
        filters = [("All", 0, 1), ("Nearby", 0, 2), ("Visible", 1, 1), ("Hidden", 1, 2)]
        for lbl, row, col in filters:
            btn = QtWidgets.QPushButton(lbl)
            btn.setCheckable(True)
            btn.setFixedSize(110, 36)
            btn.setStyleSheet(grp_btn_qss)
            if lbl == "All": btn.setChecked(True)
            
            ctrl_grid.addWidget(btn, row, col)
            self._status_btns.addButton(btn)
            self._status_widgets[lbl] = btn
        
        def _restyle_btns():
            # Update underlying settings
            all_regions = codex.region_names()
            tab_label = self._codex_tabs.currentText().strip()
            current_rid = "Z1"
            for rid, name in all_regions.items():
                if name.strip() == tab_label:
                    current_rid = rid
                    break
            
            compact = self._btn_compact.isChecked()
            if current_rid == "Pets":
                if hasattr(self.s, "codex_pets_compact"): self.s.codex_pets_compact = compact
            else:
                if hasattr(self.s, "codex_compact"): self.s.codex_compact = compact
            
            if hasattr(self.s, "save"): self.s.save()
            self._refresh_codex_grid()

        self._status_btns.buttonClicked.connect(lambda _: _restyle_btns())
        self._btn_compact.clicked.connect(lambda _: _restyle_btns())
        
        bar.addWidget(ctrl_container)
        v.addLayout(bar)
        
        # Count and any other footer info
        footer = QtWidgets.QHBoxLayout()
        footer.addStretch(1)

        self._codex_count = QtWidgets.QLabel("")
        self._codex_count.setStyleSheet(f"color:{theme.DIM};font-family:'{theme.MONO_FONT}';font-size:12px;")
        footer.addWidget(self._codex_count)

        v.addLayout(footer)
        v.addWidget(self._codex_scroll, 1)
        
        return page

    def _on_tab_changed(self, index: int) -> None:
        """Switch to 'All' status automatically when entering the Dungeons tab."""
        tab_label = self._codex_tabs.currentText().strip()
        if tab_label == "Dungeons":
            if hasattr(self, "_status_widgets"):
                self._status_widgets["All"].setChecked(True)
        self._refresh_codex_grid()

    def _on_search_changed(self, text: str) -> None:
        """Type-to-search automatically switches to ALL status to show hidden matches."""
        if text.strip() and hasattr(self, "_status_btns"):
            checked = self._status_btns.checkedButton()
            if checked and checked.text().lower() != "all":
                for btn in self._status_btns.buttons():
                    if btn.text().lower() == "all":
                        btn.setChecked(True)
                        break
        self._refresh_codex_grid()

    def _on_compact_toggled(self, state: int) -> None:
        """Toggle between Classic (boxed) and Compact (sticker) view."""
        compact = state == QtCore.Qt.Checked
        
        # Determine which setting to update based on current tab
        all_regions = codex.region_names()
        tab_label = self._codex_tabs.currentText().strip()
        current_rid = "Z1"
        for rid, name in all_regions.items():
            if name.strip() == tab_label:
                current_rid = rid
                break

        if current_rid == "Pets":
            if hasattr(self.s, "codex_pets_compact"):
                self.s.codex_pets_compact = compact
        else:
            if hasattr(self.s, "codex_compact"):
                self.s.codex_compact = compact
        
        if hasattr(self.s, "save"):
            self.s.save()
        self._refresh_codex_grid()

    def _on_undo_unit_clicked(self) -> None:
        if hasattr(self, "_unhide_last_unit"):
            self._unhide_last_unit()
        self._refresh_codex_grid()

    def _on_undo_comp_clicked(self) -> None:
        if hasattr(self, "_unhide_last_comp"):
            self._unhide_last_comp()
        self._refresh_codex_grid()

    def _sync_codex_to_current_zone(self) -> None:
        """Switch the Codex tab to match the player's current region."""
        if not hasattr(self, "model") or not self.model:
            return
        xyz = self.model.player_xyz()
        if not xyz:
            return
        
        from ...geo import zones
        zone_id = zones.resolve_zone(xyz[0], xyz[1], xyz[2])
        if not zone_id:
            return

        rid = None
        # 1. Dungeons / Bosses
        if "Dungeon" in zone_id or "_D_" in zone_id or zone_id.startswith("D_") or "Rift" in zone_id:
            rid = "Bosses"
        # 2. Crimson Island (Always Z3 in Codex, even if metadata says Z2)
        elif "Crimson" in zone_id:
            rid = "Z3"
        # 3. Bel-Etir (Part of Skover Island Z1)
        elif "Bel-Etir" in zone_id or "BelEtir" in zone_id:
            rid = "Z1"
        # 4. Standard Z# extraction
        else:
            m = re.search(r"Z(\d+)", zone_id)
            if m:
                rid = f"Z{m.group(1)}"
            elif "Greenlands" in zone_id or "Skover" in zone_id:
                rid = "Z1"
            elif "EternalAutumn" in zone_id or "Valley" in zone_id:
                rid = "Z2"
        
        if not rid:
            return
            
        all_regions = codex.region_names()
        target_name = all_regions.get(rid)
        if target_name and hasattr(self, "_codex_tabs"):
            # Avoid redundant updates if already on the right tab
            if self._codex_tabs.currentText() != target_name:
                self._codex_tabs.setCurrentText(target_name)

    def _refresh_codex_grid(self) -> None:
        """Re-build the grid based on active tab, search query, and status filter."""
        # Reset scroll position to top whenever the view changes
        if hasattr(self, "_codex_scroll"):
            self._codex_scroll.verticalScrollBar().setValue(0)

        # 1. Get filters
        query = self._codex_search.text().strip().lower()
        
        status_filter = "ALL"
        if hasattr(self, "_status_btns"):
            checked = self._status_btns.checkedButton()
            if checked: status_filter = checked.text().upper() # Use uppercase for logic

        # Performance: Disable updates during bulk grid rebuild
        self._codex_grid.parentWidget().setUpdatesEnabled(False)
        try:
            # 2. Clear grid
            while self._codex_grid.count():
                item = self._codex_grid.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
            self._codex_cards.clear()
            # Reset row stretches to avoid gaps from previous results
            for r in range(self._codex_grid.rowCount()):
                self._codex_grid.setRowStretch(r, 0)
            
            # 3. Determine regions to show
            all_regions = codex.region_names()
            tab_label = self._codex_tabs.currentText().strip()
            
            # Identify current region ID from the active tab
            current_rid = "Z1"
            for rid, name in all_regions.items():
                if name.strip() == tab_label:
                    current_rid = rid
                    break

            # Sync the single "Compact" button to the correct underlying setting
            compact_mobs = self.s.codex_compact if hasattr(self.s, "codex_compact") else False
            compact_pets = self.s.codex_pets_compact if hasattr(self.s, "codex_pets_compact") else True
            
            is_compact = compact_pets if current_rid == "Pets" else compact_mobs
            
            # Update button UI (block signals to avoid infinite loop)
            if hasattr(self, "_btn_compact"):
                self._btn_compact.blockSignals(True)
                self._btn_compact.setChecked(is_compact)
                self._btn_compact.blockSignals(False)

            cols = 7 if is_compact else 6

            # Update search placeholder based on context
            if hasattr(self, "_codex_search"):
                if tab_label == "Pets":
                    self._codex_search.setPlaceholderText("Search pets…")
                elif tab_label == "Dungeons":
                    self._codex_search.setPlaceholderText("Search dungeons…")
                else:
                    self._codex_search.setPlaceholderText("Search bestiary…")

            # Search is local for Pets, Dungeons (Bosses), and Others (Z0)
            is_global = bool(query) or status_filter == "NEARBY"
            if tab_label.lower() in ("pets", "dungeons", "others"):
                is_global = False
            
            is_special = current_rid in ("Pets", "Bosses", "Z0") or tab_label in ("Pets", "Dungeons", "Others")

            # Toggle bulk buttons (hide when searching globally, showing nearby, or on Pets/Bosses/Hidden/Visible tabs)
            # We only want these visible on the 'ALL' view of a standard Region tab.
            hide_bulk = is_global or current_rid in ("Pets", "Bosses") or status_filter != "ALL"
            if hasattr(self, "_btn_codex_enable"):
                self._btn_codex_enable.setVisible(not hide_bulk)
                self._btn_codex_disable.setVisible(not hide_bulk)
                # If we're hiding bulk, also hide any active confirmation
                if hide_bulk:
                    self._btn_codex_confirm.setVisible(False)
                    self._btn_codex_cancel.setVisible(False)
                # If we're showing bulk, but a confirmation is active, keep the confirmation visible instead
                elif self._btn_codex_confirm.isVisible():
                    self._btn_codex_enable.setVisible(False)
                    self._btn_codex_disable.setVisible(False)

            if is_global:
                # Global search or Nearby filter: show all regions, but prioritize current tab
                regions_to_show = {current_rid: all_regions[current_rid]}
                for rid, name in all_regions.items():
                    if rid != current_rid:
                        regions_to_show[rid] = name
            else:
                # Tabbed view: show only selected region
                regions_to_show = {current_rid: tab_label}
            
            # Always use AlignLeft to avoid "gaps" caused by centering partial rows
            self._codex_grid.setAlignment(QtCore.Qt.AlignTop | QtCore.Qt.AlignLeft)

            # 4. Populate grid
            profile = self.model.player_profile() if hasattr(self, "model") and self.model else None
            hidden_units = set(self.s.get_entity_hidden_units(profile))
            hidden_comps = set(self.s.get_companion_hidden_units(profile)) if hasattr(self.s, "get_companion_hidden_units") else set()
            
            grid_row = 0
            total_shown = 0

            # 4a. SPECIAL: Last Hidden Slot (OLD SYSTEM RECOVERY)
            last_u = getattr(self, "_last_hidden_unit", None)
            last_c = getattr(self, "_last_hidden_comp", None)
            undos = []
            
            # Contextual filtering: Pets undo only on Pets tab, Enemy undo on Region tabs (NOT Dungeons/Bosses)
            if current_rid == "Pets":
                if last_c and last_c[0] in hidden_comps: undos.append((last_c, True))
            elif current_rid != "Bosses":
                if last_u and last_u[0] in hidden_units: undos.append((last_u, False))
            
            if undos and not query:
                curr_col = 0
                for (uid, name), is_pet in undos:
                    data = {"id": uid, "name": name, "is_critter": is_pet}
                    card = CodexUnitCard(uid, data, 0, 0, active=False, compact=is_compact)
                    card.name_lbl.setText(f"LAST HIDDEN\n{name}")
                    card.name_lbl.setStyleSheet(f"color: {theme.GOLD}; font-weight: bold; font-size: 11px;")
                    
                    # Direct signal connection; ignore the bool emitted by card.toggled
                    if is_pet:
                        card.toggled.connect(lambda _: self._on_undo_comp_clicked())
                    else:
                        card.toggled.connect(lambda _: self._on_undo_unit_clicked())
                    
                    self._codex_grid.addWidget(card, grid_row, curr_col)
                    self._codex_cards.append(card)
                    curr_col += 1
                grid_row += 1
            
            # Get nearby units if filter is active
            nearby_info = {}  # uid -> distance
            if status_filter == "NEARBY" and hasattr(self, "model") and self.model:
                xyz = self.model.player_xyz()
                if xyz:
                    # Enumerate enemies and companions within 300m
                    for e, d in self.model.nearest_enemies(xyz, 500, max_dist=300.0):
                        if e.unit_id:
                            nearby_info[e.unit_id] = min(nearby_info.get(e.unit_id, 999.0), d)
                    for e, d in self.model.nearest_companions(xyz, 100, max_dist=300.0):
                        if e.unit_id:
                            nearby_info[e.unit_id] = min(nearby_info.get(e.unit_id, 999.0), d)

            total_shown = 0
            
            for rid, rname in regions_to_show.items():
                items = codex.units_by_region(rid)
                
                # Pre-filter items for this region
                matches = []
                for item in items:
                    uid = item["id"]
                    uname = item["name"]
                    
                    is_pet = rid == "Pets" or item.get("is_critter", False)
                    is_active = uid not in (hidden_comps if is_pet else hidden_units)
                    
                    # Apply Status Filter
                    if status_filter == "VISIBLE" and not is_active:
                        continue
                    if status_filter == "HIDDEN" and is_active:
                        continue
                    if status_filter == "NEARBY" and uid not in nearby_info:
                        continue
                        
                    # Apply Search Filter
                    if query and query not in uname.lower() and query not in uid.lower():
                        continue
                    
                    matches.append((uid, item, is_active, is_pet, nearby_info.get(uid, 0)))
                
                if not matches:
                    continue
                
                # Sort matches: 
                # For Pets & Dungeons: Group by Species/Dungeon (Type) first, then boss first, then by visibility (Active/Hidden), then name.
                # For others: visible first, then by distance (if nearby) or name.
                if status_filter == "NEARBY":
                    matches.sort(key=lambda x: (not x[2], x[4]))
                elif rid == "Bosses":
                    # Dungeons: Rift status, then Level, then Dungeon name, then boss-first, then unit name
                    matches.sort(key=lambda x: (
                        x[1].get("is_rift", False), 
                        x[1].get("level", 99), 
                        x[1].get("type") or "", 
                        not x[1].get("is_boss", False), 
                        x[1]["name"].lower()
                    ))
                elif rid == "Pets":
                    matches.sort(key=lambda x: (x[1].get("type") or "", not x[1].get("is_boss", False), not x[2], x[1]["name"].lower()))
                else:
                    matches.sort(key=lambda x: (not x[2], x[1]["name"].lower()))
                    
                # Add Zone Header if searching globally or multi-zone
                if is_global:
                    header = C.SectionHeader(rname)
                    self._codex_grid.addWidget(header, grid_row, 0, 1, cols)
                    grid_row += 1
                
                # Add cards with species subgroups for Pets
                curr_col = 0
                last_type = None
                
                for uid, item, is_active, is_pet, dist in matches:
                    # Insert species sub-header if we're in the Pets/Dungeons tab and using the 'ALL' filter (not searching)
                    # We keep 'VISIBLE' and 'HIDDEN' as a plain grid for a cleaner look when only few match.
                    if rid in ("Pets", "Bosses") and not is_global and status_filter == "ALL" and item.get("type") != last_type:
                        if curr_col > 0:
                            grid_row += 1
                            curr_col = 0
                        last_type = item.get("type")
                        
                        # Skip the "OTHERS" sub-header to avoid confusion with the "Others" region
                        if last_type and str(last_type).upper() != "OTHERS":
                            header_text = str(last_type).upper()
                            if rid == "Bosses":
                                is_rift = item.get("is_rift", False)
                                level = item.get("level")
                                if level and not is_rift:
                                    # Dungeon Name is bold, Level # is normal weight
                                    header_text = f"<b>{header_text}</b> (LEVEL {level})"
                                    
                            sub_header = QtWidgets.QLabel(header_text)
                            sub_header.setStyleSheet(f"color: {theme.ACCENT}; margin-top: 10px; margin-bottom: 4px; font-size: 13px;")
                            self._codex_grid.addWidget(sub_header, grid_row, 0, 1, cols)
                            grid_row += 1

                    card = CodexUnitCard(uid, item, 0, 10, is_active,
                                         compact=compact_pets if is_pet else compact_mobs)
                    if rid == "Bosses":
                        card.locked = True
                        card.set_active(True)

                    if dist > 0:
                        card.setToolTip(f"{card.toolTip()}\nDistance: {dist:.0f}m")

                    if is_pet:
                        card.toggled.connect(lambda on, u=uid: self._set_companion_hidden(u, not on))
                    else:
                        card.toggled.connect(lambda on, u=uid: self._set_unit_hidden(u, not on))
                    
                    self._codex_grid.addWidget(card, grid_row, curr_col)
                    self._codex_cards.append(card)
                    
                    curr_col += 1
                    if curr_col >= cols:
                        curr_col = 0
                        grid_row += 1
                
                if curr_col > 0:
                    grid_row += 1

                total_shown += len(matches)

            # Push all content to the top to avoid vertical gaps in the grid
            self._codex_grid.setRowStretch(grid_row, 1)

            if hasattr(self, "_codex_count"):
                if is_global:
                    self._codex_count.setText(f"FOUND: {total_shown}")
                else:
                    self._codex_count.setText(f"SHOWN: {total_shown}")
        finally:
            self._codex_grid.parentWidget().setUpdatesEnabled(True)

    def _refresh_codex_sync(self) -> None:
        """Lightweight sync of existing cards.
        Since the Codex now has dynamic 'Last Hidden' slots at the top, we must
        do a full rebuild to update the grid layout."""
        # Always trigger a full refresh to ensure the 'Last Hidden' card 
        # appears/disappears and re-ordering happens correctly.
        QtCore.QTimer.singleShot(0, self._refresh_codex_grid)

    def _codex_prime_bulk(self, visible: bool) -> None:
        """Show confirmation buttons for bulk actions."""
        self._btn_codex_enable.setVisible(False)
        self._btn_codex_disable.setVisible(False)
        
        self._bulk_visible_target = visible
        # Keep text short to avoid clipping in the fixed-width grid buttons
        self._btn_codex_confirm.setText("CONFIRM")
        self._btn_codex_confirm.setStyleSheet(
            f"QPushButton#Accent {{ font-size: 11px; font-weight: bold; background: {theme.ACCENT}; color: {theme.ON_ACCENT}; }}"
        )
        self._btn_codex_cancel.setStyleSheet(
            f"QPushButton#Outline {{ font-size: 11px; font-weight: bold; background: transparent; border: 1px solid {theme.BORDER}; color: {theme.MUTED}; }}"
        )
        self._btn_codex_confirm.setVisible(True)
        self._btn_codex_cancel.setVisible(True)

    def _codex_cancel_bulk(self) -> None:
        """Reset bulk action buttons."""
        self._btn_codex_confirm.setVisible(False)
        self._btn_codex_cancel.setVisible(False)
        self._btn_codex_enable.setVisible(True)
        self._btn_codex_disable.setVisible(True)

    def _codex_set_all(self, visible: bool) -> None:
        """Set all units in the current region/view to visible or hidden."""
        self._codex_cancel_bulk()
        
        # 1. Determine region ID from tab label
        tab_label = self._codex_tabs.currentText()
        region_id = "Z1"
        for rid, name in codex.region_names().items():
            if name == tab_label:
                region_id = rid
                break
        
        # 2. Get all items for this region
        items = codex.units_by_region(region_id)
        uids = [it["id"] for it in items]
        
        # 3. Apply to settings (bulk)
        profile = self.model.player_profile() if hasattr(self, "model") and self.model else None
        
        if region_id == "Pets":
            for uid in uids:
                self.s.toggle_companion_hidden(uid, not visible, profile)
        else:
            self.s.bulk_toggle_units_hidden(uids, not visible, profile)
        
        # 4. Sync Entity tab chips if they exist
        if region_id == "Pets" and hasattr(self, "_companion_chips"):
            uid_set = set(uids)
            for u, _n, chip in self._companion_chips:
                if u in uid_set:
                    chip.blockSignals(True)
                    chip.setChecked(visible)
                    chip.blockSignals(False)
                    chip.restyle()
        elif region_id != "Pets" and hasattr(self, "_unit_chips"):
            uid_set = set(uids)
            for us, _n, chip in self._unit_chips:
                # us is a list[str] of UIDs in the group
                match = False
                for u in us:
                    if u in uid_set:
                        match = True
                        break
                if match:
                    chip.blockSignals(True)
                    chip.setChecked(visible)
                    chip.blockSignals(False)
                    chip.restyle()

        # 5. Refresh UI
        # Use sync for hiding to keep cards visible (faded) for accidental clicks.
        # Use grid rebuild for showing to ensure all newly enabled units appear.
        if visible:
            self._refresh_codex_grid()
        else:
            self._refresh_codex_sync()
        
        if hasattr(self, "_update_unit_tag"):
            self._update_unit_tag()
        if hasattr(self, "_update_companion_tag"):
            self._update_companion_tag()


class CodexUnitCard(QtWidgets.QFrame):
    """A visual card for a mob in the bestiary."""
    toggled = QtCore.Signal(bool)

    def __init__(self, uid: str, data: dict, kills: int, total: int, active: bool = True, compact: bool = False, parent=None):
        super().__init__(parent)
        self.uid = uid
        self.data = data
        self.active = active
        self.compact = compact
        self.locked = False
        self.setObjectName("Cell")
        
        if self.compact:
            self.setFixedWidth(110)
            self.setMinimumHeight(128)
        else:
            self.setFixedSize(130, 155)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(2)
        
        # Picture (Icon)
        self.icon_tile = C.IconTile(size=80)
        
        # Use the icon from data if available, otherwise fallback to UID
        icon_val = data.get("icon") or uid
        if not isinstance(icon_val, str):
            icon_val = str(uid)
        if "." in icon_val:
            icon_val = icon_val.rsplit(".", 1)[0]
            # Handle .prefab.png cases from catalog
            if icon_val.endswith(".prefab"):
                icon_val = icon_val.rsplit(".", 1)[0]
        # Some catalog icons have full paths or specific naming conventions
        if "/" in icon_val or "\\" in icon_val:
            import os
            icon_val = os.path.splitext(os.path.basename(icon_val))[0]
                
        self.icon_id = icon_val
        self.sheet = "collection" if data.get("is_critter") else "units"
        
        lay.addWidget(self.icon_tile, 0, QtCore.Qt.AlignTop | QtCore.Qt.AlignHCenter)
        
        # Name
        self.name_lbl = QtWidgets.QLabel(data["name"])
        self.name_lbl.setObjectName("FieldLabel")
        self.name_lbl.setStyleSheet("font-size: 11px; font-weight: bold;") 
        self.name_lbl.setAlignment(QtCore.Qt.AlignTop | QtCore.Qt.AlignHCenter)
        self.name_lbl.setWordWrap(True)
        # Compact cards only need 2 lines (~36px min), classic keep 3-4 lines (~50px min)
        if self.compact:
            self.name_lbl.setMinimumHeight(36)
        else:
            self.name_lbl.setMinimumHeight(50)
        
        lay.addWidget(self.name_lbl, 0, QtCore.Qt.AlignTop)
        lay.addStretch(1)
        
        # Tooltip with raw ID
        self.setToolTip(f"ID: {data['id']}\nClick to toggle visibility")
        
        self.set_active(active)

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.LeftButton:
            if self.locked:
                return
            self.active = not self.active
            self.set_active(self.active)
            self.toggled.emit(self.active)
        super().mousePressEvent(event)

    def set_active(self, active: bool) -> None:
        """Update visual state (dimming)."""
        self.active = active
        
        if self.locked:
            self.setToolTip(f"ID: {self.data['id']}\nVisibility locked in Dungeons")
        else:
            self.setToolTip(f"ID: {self.data['id']}\nClick to toggle visibility")

        is_boss = self.data.get("is_boss", False)
        is_elite = self.data.get("is_elite", False)
        is_critter = self.data.get("is_critter", False)
        is_spark = is_critter and ("spark" in self.uid.lower() or "sparkling" in self.data.get("name", "").lower())
        
        # Apply icon style
        if self.compact:
            # Grid/Outline view uses clean icons with no internal fill or border
            self.icon_tile.set(self.sheet, self.icon_id, accent="#00000000")
            
            # Apply organic outlines (circles for mobs, organic for pets)
            if is_spark or is_boss:
                bcol = theme.GOLD if active else "#a16207"
                if is_critter:
                    self.icon_tile.set_outlined(self.sheet, self.icon_id, accent=bcol, border=4)
                    self.icon_tile.setStyleSheet("border: 0; background: transparent;")
                else:
                    self.icon_tile.setStyleSheet(f"border: 4px solid {bcol}; border-radius: 40px; background: transparent;")
            elif is_elite:
                bcol = theme.SILVER if active else "#717171"
                self.icon_tile.setStyleSheet(f"border: 4px solid {bcol}; border-radius: 40px; background: transparent;")
            else:
                # Normal units/pets have NO outline/box in compact mode
                self.icon_tile.setStyleSheet("border: 0; background: transparent;")
            
            self.setStyleSheet("QFrame#Cell { background-color: transparent; border: 0; }")
        else:
            # Classic square tile for all entries
            accent = theme.ACCENT if active else theme.ACCENT_DIM
            self.icon_tile.set(self.sheet, self.icon_id, accent=accent)
            
            # Apply rectangular border for special enemies
            if is_boss or is_spark:
                bcol = theme.GOLD if active else "#a16207"
                self.icon_tile.setStyleSheet(f"border: 2px solid {bcol};")
            elif is_elite:
                bcol = theme.SILVER if active else "#717171"
                self.icon_tile.setStyleSheet(f"border: 2px solid {bcol};")
            else:
                self.icon_tile.setStyleSheet("border: 0;")

            # Classic blue-tinted card background
            if active:
                self.setStyleSheet("QFrame#Cell { background-color: rgba(56, 189, 248, 0.04); }")
            else:
                self.setStyleSheet("QFrame#Cell { background-color: transparent; border-color: #334155; }")
        
        # Update text colors
        color = theme.TEXT if active else theme.DIM
        self.name_lbl.setStyleSheet(f"font-size: 11px; font-weight: bold; color: {color};")
