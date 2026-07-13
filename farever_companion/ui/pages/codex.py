from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets
from .. import components as C
from .. import theme
from ...data import codex, names, units

_CARD_COLS = 6


class CodexPageMixin:
    def _page_codex(self):
        page, v = self._page_container()
        
        # Tabs for Regions
        regions = codex.region_names()
        self._codex_tabs = C.SegmentedControl(list(regions.values()), 
                                             current=list(regions.values())[0])
        self._codex_tabs.currentChanged.connect(self._refresh_codex_grid)
        v.addWidget(self._codex_tabs)
        
        # Search Bar and Status Filter
        bar = QtWidgets.QHBoxLayout()
        bar.setSpacing(10)
        
        self._codex_search = QtWidgets.QLineEdit()
        self._codex_search.setPlaceholderText("Search bestiary…")
        self._codex_search.setClearButtonEnabled(True)
        self._codex_search.setFixedWidth(250)
        self._codex_search.textChanged.connect(self._refresh_codex_grid)
        bar.addWidget(self._codex_search)
        
        bar.addStretch(1)
        
        self._btn_codex_enable = QtWidgets.QPushButton("Enable All in Zone")
        self._btn_codex_enable.setObjectName("Outline")
        self._btn_codex_enable.setFixedHeight(26)
        self._btn_codex_enable.clicked.connect(lambda: self._codex_set_all(True))
        bar.addWidget(self._btn_codex_enable)
        
        self._btn_codex_disable = QtWidgets.QPushButton("Disable All in Zone")
        self._btn_codex_disable.setObjectName("Outline")
        self._btn_codex_disable.setFixedHeight(26)
        self._btn_codex_disable.clicked.connect(lambda: self._codex_set_all(False))
        bar.addWidget(self._btn_codex_disable)
        
        self._codex_status = C.SegmentedControl(["ALL", "VISIBLE", "HIDDEN"], current="ALL")
        self._codex_status.currentChanged.connect(self._refresh_codex_grid)
        bar.addWidget(self._codex_status)

        v.addLayout(bar)
        
        # Count and any other footer info
        footer = QtWidgets.QHBoxLayout()
        footer.addStretch(1)

        self._codex_count = QtWidgets.QLabel("")
        self._codex_count.setStyleSheet(f"color:{theme.DIM};font-family:'{theme.MONO_FONT}';font-size:12px;")
        footer.addWidget(self._codex_count)

        v.addLayout(footer)
        
        v.addSpacing(6)
        
        # Scroll area for the grid
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff) # Hidden scrollbar
        
        body = QtWidgets.QWidget()
        self._codex_grid = QtWidgets.QGridLayout(body)
        self._codex_grid.setSpacing(8) # Reduced spacing
        self._codex_grid.setContentsMargins(0, 0, 0, 0)
        self._codex_grid.setAlignment(QtCore.Qt.AlignTop | QtCore.Qt.AlignLeft)
        scroll.setWidget(body)
        v.addWidget(scroll, 1)
        
        self._codex_cards: list[CodexUnitCard] = []
        self._refresh_codex_grid()
        
        return page

    def _refresh_codex_grid(self) -> None:
        """Re-build the grid based on active tab, search query, and status filter."""
        # 1. Get filters
        query = self._codex_search.text().strip().lower()
        status_filter = self._codex_status.currentText()
        
        # Toggle bulk buttons (hide when searching globally)
        if hasattr(self, "_btn_codex_enable"):
            self._btn_codex_enable.setVisible(not query)
        if hasattr(self, "_btn_codex_disable"):
            self._btn_codex_disable.setVisible(not query)
        
        # Performance: Disable updates during bulk grid rebuild
        self._codex_grid.parentWidget().setUpdatesEnabled(False)
        try:
            # 2. Clear grid
            while self._codex_grid.count():
                item = self._codex_grid.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
            self._codex_cards.clear()
            
            # 3. Determine regions to show
            all_regions = codex.region_names()
            tab_label = self._codex_tabs.currentText()
            
            # Identify current region ID from the active tab
            current_rid = "Z1"
            for rid, name in all_regions.items():
                if name == tab_label:
                    current_rid = rid
                    break

            if query:
                # Global search: show all regions, but prioritize the current tab at the top
                regions_to_show = {current_rid: all_regions[current_rid]}
                for rid, name in all_regions.items():
                    if rid != current_rid:
                        regions_to_show[rid] = name
                self._codex_grid.setAlignment(QtCore.Qt.AlignTop | QtCore.Qt.AlignLeft)
            else:
                # Tabbed view: show only selected region
                regions_to_show = {current_rid: tab_label}
                self._codex_grid.setAlignment(QtCore.Qt.AlignTop | QtCore.Qt.AlignHCenter)

            # 4. Populate grid
            profile = self.model.player_profile() if hasattr(self, "model") and self.model else None
            hidden_units = set(self.s.get_entity_hidden_units(profile))
            hidden_comps = set(self.s.get_companion_hidden_units(profile)) if hasattr(self.s, "get_companion_hidden_units") else set()
            
            grid_row = 0
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
                        
                    # Apply Search Filter
                    if query and query not in uname.lower() and query not in uid.lower():
                        continue
                    
                    matches.append((uid, item, is_active, is_pet))
                
                if not matches:
                    continue
                    
                # Add Zone Header if searching globally or multi-zone
                if query:
                    header = C.SectionHeader(rname)
                    self._codex_grid.addWidget(header, grid_row, 0, 1, _CARD_COLS)
                    grid_row += 1
                
                # Add cards
                for i, (uid, item, is_active, is_pet) in enumerate(matches):
                    card = CodexUnitCard(uid, item, 0, 10, is_active)
                    if is_pet:
                        card.toggled.connect(lambda on, u=uid: self._set_companion_hidden(u, not on))
                    else:
                        card.toggled.connect(lambda on, u=uid: self._set_unit_hidden(u, not on))
                    
                    self._codex_grid.addWidget(card, grid_row + (i // _CARD_COLS), i % _CARD_COLS)
                    self._codex_cards.append(card)
                    
                grid_row += (len(matches) + _CARD_COLS - 1) // _CARD_COLS
                total_shown += len(matches)
                if not query:
                    total_in_zone = len(items)

            if hasattr(self, "_codex_count"):
                if query:
                    self._codex_count.setText(f"FOUND: {total_shown}")
                else:
                    self._codex_count.setText(f"SHOWN: {total_shown}")
        finally:
            self._codex_grid.parentWidget().setUpdatesEnabled(True)

    def _refresh_codex_sync(self) -> None:
        """Lightweight sync of existing cards without rebuilding the whole grid."""
        profile = self.model.player_profile() if hasattr(self, "model") and self.model else None
        hidden_units = set(self.s.get_entity_hidden_units(profile))
        hidden_comps = set(self.s.get_companion_hidden_units(profile)) if hasattr(self.s, "get_companion_hidden_units") else set()
        status_filter = self._codex_status.currentText()
        
        # If we are in a filtered view, we might need a full refresh to move cards in/out
        if status_filter != "ALL":
            self._refresh_codex_grid()
            return

        for card in self._codex_cards:
            is_pet = units.is_companion(card.uid)
            is_active = card.uid not in (hidden_comps if is_pet else hidden_units)
            card.set_active(is_active)

    def _codex_set_all(self, visible: bool) -> None:
        """Set all units in the current region/view to visible or hidden."""
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
            for u, _n, chip in self._unit_chips:
                if u in uid_set:
                    chip.blockSignals(True)
                    chip.setChecked(visible)
                    chip.blockSignals(False)
                    chip.restyle()

        # 5. Refresh UI
        self._refresh_codex_grid()
        
        if hasattr(self, "_update_unit_tag"):
            self._update_unit_tag()
        if hasattr(self, "_update_companion_tag"):
            self._update_companion_tag()


class CodexUnitCard(QtWidgets.QFrame):
    """A visual card for a mob in the bestiary."""
    toggled = QtCore.Signal(bool)

    def __init__(self, uid: str, data: dict, kills: int, total: int, active: bool = True, parent=None):
        super().__init__(parent)
        self.uid = uid
        self.data = data
        self.active = active
        self.setObjectName("Cell")
        self.setFixedSize(130, 155)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(4)
        
        # Picture (Icon)
        self.icon_tile = C.IconTile(size=80) # Slightly smaller icon to give more room for text
        from ...data import icons
        
        # Primary identifier is the Unit ID (UID)
        self.icon_id = uid
        self.sheet = "units"
        
        # If UID isn't in the atlas, try extracting the filename from the data's icon path
        icon_path = data.get("icon") or ""
        if not icons.has_icon("units", self.icon_id) and icon_path:
            # Extract "Skunk_Z1W" from "icons/Units/Skunk_Z1W.png"
            self.icon_id = icon_path.split("/")[-1].split(".")[0]
        
        lay.addWidget(self.icon_tile, 0, QtCore.Qt.AlignTop | QtCore.Qt.AlignHCenter)
        
        # Name
        self.name_lbl = QtWidgets.QLabel(data["name"])
        self.name_lbl.setObjectName("FieldLabel")
        self.name_lbl.setStyleSheet("font-size: 11px; font-weight: bold; line-height: 12px;") 
        self.name_lbl.setAlignment(QtCore.Qt.AlignTop | QtCore.Qt.AlignHCenter)
        self.name_lbl.setWordWrap(True)
        # Ensure the label has enough height for 3-4 lines without pushing the icon
        self.name_lbl.setMinimumHeight(50)
        lay.addWidget(self.name_lbl, 1, QtCore.Qt.AlignTop)
        
        # Tooltip with raw ID
        self.setToolTip(f"ID: {data['id']}\nClick to toggle visibility")
        
        self.set_active(active)

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.LeftButton:
            self.active = not self.active
            self.set_active(self.active)
            self.toggled.emit(self.active)
        super().mousePressEvent(event)

    def set_active(self, active: bool) -> None:
        """Update visual state (dimming)."""
        self.active = active
        is_boss = self.data.get("is_boss", False)
        # Sparking detection: check ID suffix or readable name ONLY for critters/pets
        is_critter = self.data.get("is_critter", False)
        is_spark = is_critter and ("spark" in self.uid.lower() or "sparkling" in self.data.get("name", "").lower())
        
        # Use dim accent for icon if hidden
        accent = theme.ACCENT if active else theme.ACCENT_DIM
        self.icon_tile.set(self.sheet, self.icon_id, accent=accent)
        
        # Apply 2px gold border only to the icon if boss or sparkling PET
        if is_boss or is_spark:
            bcol = theme.GOLD if active else "#a16207"
            self.icon_tile.setStyleSheet(f"border: 2px solid {bcol};")
        else:
            self.icon_tile.setStyleSheet("border: 0;")

        # Optional: update border/background to reflect state
        if active:
            self.setStyleSheet("QFrame#Cell { background-color: rgba(56, 189, 248, 0.04); }")
            self.name_lbl.setStyleSheet(f"font-size: 11px; font-weight: bold; line-height: 12px; color: {theme.TEXT};")
        else:
            self.setStyleSheet("QFrame#Cell { background-color: transparent; border-color: #334155; }")
            self.name_lbl.setStyleSheet(f"font-size: 11px; font-weight: bold; line-height: 12px; color: {theme.DIM};")
