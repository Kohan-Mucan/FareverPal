"""Codex page assembly: layout, widget builders, and tab/search/filter handlers."""
from __future__ import annotations

import re

from PySide6 import QtCore, QtGui, QtWidgets

from ... import components as C
from ... import theme
from ....data import codex
from .card import CodexUnitCard


# Shared toggle style for the compact ('Grid / Outline') and dungeon view
# buttons (used under the search box and above the grid).
_CODEX_TOGGLE_QSS = (
    f"QPushButton{{background:transparent; border:1px solid {theme.BORDER}; color:{theme.MUTED}; "
    f"font-size:11px; font-weight:bold; padding: 2px 8px; border-radius:4px;}}"
    f"QPushButton:hover{{color:{theme.TEXT}; border-color:{theme.DIM};}}"
    f"QPushButton:checked, QPushButton:pressed{{background:{theme.with_alpha(theme.ACCENT, 42)}; "
    f"color:{theme.ACCENT}; border-color:{theme.ACCENT};}}"
)


class CodexPageBase:
    def _page_codex(self):
        page, v = self._page_container()
        self._spark_only = False
        self._dungeon_only = False
        self._source_filters: set[str] = set()  # active source-badge filters (Vendor/Achievement/Dungeon/Chest)
        self._dungeon_list_mode = True  # Dungeons tab defaults to the list view
        self._chest_orb_kind: str | None = None  # 'chests' / 'orbs' remaining-list view
        self._collection_sub = "pets"  # Collection-tab sub-view (pets/mounts/gliders/chests/orbs)
        self._dungeon_zone = "All"  # Dungeons-tab zone filter (All / Z1 / Z2 / Z3)
        self._zone_pins_active = False  # zone-filter pins currently on the map

        # Tabs for Regions
        regions = codex.region_names()
        self._codex_tabs = C.SegmentedControl(list(regions.values()),
                                              current=list(regions.values())[0])
        self._codex_tabs.currentChanged.connect(self._on_tab_changed)
        v.addWidget(self._codex_tabs)

        # 1. Initialize the grid FIRST to avoid crashes during restyle/refresh
        self._codex_scroll, self._codex_grid = self._build_codex_grid_scroll()
        self._codex_cards: list[CodexUnitCard] = []

        # 2. Header controls bar (search, last-hidden, legends, actions)
        bar = QtWidgets.QHBoxLayout()
        bar.setSpacing(10)
        bar.setAlignment(QtCore.Qt.AlignTop)
        bar.addLayout(self._build_codex_search_block())
        bar.addWidget(self._build_codex_last_hidden())
        bar.addSpacing(12)
        bar.addStretch(1)
        bar.addLayout(self._build_codex_legends())
        bar.addSpacing(12)
        bar.addWidget(self._build_codex_controls())
        v.addLayout(bar)

        # 3. Splitter: bestiary grid on the left, zone spawn map on the right
        content_splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        content_splitter.setChildrenCollapsible(False)

        left_widget = QtWidgets.QWidget()
        left_v = QtWidgets.QVBoxLayout(left_widget)
        left_v.setContentsMargins(0, 0, 0, 0)
        left_v.setSpacing(4)
        left_v.addLayout(self._build_codex_grid_header())
        left_v.addWidget(self._codex_scroll, 1)

        self._status_btns.buttonClicked.connect(lambda _: self._restyle_codex_controls())
        self._btn_compact.clicked.connect(lambda _: self._restyle_codex_controls())

        content_splitter.addWidget(left_widget)
        self._map_container = self._build_codex_map_panel()
        content_splitter.addWidget(self._map_container)
        content_splitter.setSizes([600, 360])
        v.addWidget(content_splitter, 1)

        return page

    def _codex_current_region(self) -> tuple[str, str]:
        """(region_id, tab_label) for the active Codex tab; ("", "") if not built."""
        if not hasattr(self, "_codex_tabs"):
            return "", ""
        all_regions = codex.region_names()
        tab_label = self._codex_tabs.currentText().strip()
        current_rid = "Z1"
        for rid, name in all_regions.items():
            if name.strip() == tab_label:
                current_rid = rid
                break
        return current_rid, tab_label

    def _codex_is_collection_view(self, current_rid: str, tab_label: str) -> bool:
        """True for the compact card style (Collection / Pets / Others /
        mounts / gliders)."""
        return current_rid in ("Pets", "Z0", "Mounts", "Gliders") or tab_label in ("Others", "Other", "Pets", "Collection", "Mounts", "Gliders")

    def _restyle_codex_controls(self) -> None:
        """Persist the compact setting for the current tab, then rebuild the grid."""
        current_rid, tab_label = self._codex_current_region()
        compact = self._btn_compact.isChecked()
        if self._codex_is_collection_view(current_rid, tab_label):
            if hasattr(self.s, "codex_pets_compact"): self.s.codex_pets_compact = compact
        else:
            if hasattr(self.s, "codex_compact"): self.s.codex_compact = compact
        if hasattr(self.s, "save"): self.s.save()
        self._refresh_codex_grid()

    # --- page builders ---------------------------------------------------
    def _build_codex_grid_scroll(self) -> tuple[QtWidgets.QScrollArea, QtWidgets.QGridLayout]:
        """Scroll area + grid body, returned as (scroll, grid)."""
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)

        body = QtWidgets.QWidget()
        grid = QtWidgets.QGridLayout(body)
        grid.setHorizontalSpacing(4)
        grid.setVerticalSpacing(4)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setAlignment(QtCore.Qt.AlignTop | QtCore.Qt.AlignLeft)
        scroll.setWidget(body)
        return scroll, grid

    def _build_codex_search_block(self) -> QtWidgets.QVBoxLayout:
        """Search box (180px) with the 'Grid / Outline' compact toggle stacked
        directly beneath it, both aligned to the left edge."""
        search_v = QtWidgets.QVBoxLayout()
        search_v.setSpacing(4)
        search_v.setContentsMargins(0, 0, 0, 0)

        self._codex_search = QtWidgets.QLineEdit()
        self._codex_search.setPlaceholderText("Search Codex…")
        self._codex_search.setClearButtonEnabled(True)
        self._codex_search.setFixedWidth(180)
        self._codex_search.setFixedHeight(36)
        self._codex_search.setStyleSheet(
            f"QLineEdit {{ background: {theme.BG}; color: {theme.TEXT}; border: 1px solid {theme.BORDER}; border-radius: 4px; padding: 4px 8px; font-size: 12px; }}"
        )
        self._codex_search.textChanged.connect(self._on_search_changed)
        search_v.addWidget(self._codex_search)

        # 'Grid / Outline' compact toggle — lives under the search box; the
        # grid header keeps the dungeon view switcher + count instead.
        self._btn_compact = QtWidgets.QPushButton("Grid / Outline")
        self._btn_compact.setCheckable(True)
        self._btn_compact.setFixedHeight(24)
        self._btn_compact.setStyleSheet(_CODEX_TOGGLE_QSS)
        search_v.addWidget(self._btn_compact, 0, QtCore.Qt.AlignLeft)
        return search_v

    def _update_codex_profile_label(self) -> None:
        """Header readout: the attached character (name + class, no 'Profile:'
        prefix) shows wherever per-character state applies — the Chests/Orbs
        remaining lists, the zone-tab mob grids, and the Dungeon Mobs view.
        Views that are the same for everyone (Dungeon List, Collection's
        pets/mounts/gliders, Others) read 'Profile Global'. The profile key is
        'name_class_uid'; the trailing uid hash is dropped for display."""
        if not hasattr(self, "_map_title_lbl"):
            return
        kind = getattr(self, "_chest_orb_kind", None)
        try:
            rid, tab_label = self._codex_current_region()
        except Exception:
            rid, tab_label = "", ""
        is_zone = bool(rid and rid.startswith("Z") and rid != "Z0")
        is_dungeon = rid == "Bosses" or tab_label == "Dungeons"
        show_char = (kind in ("chests", "orbs")
                     or is_zone
                     or (is_dungeon and not getattr(self, "_dungeon_list_mode", False)))
        profile = self.model.player_profile() if (hasattr(self, "model") and self.model) else None
        if not show_char:
            text, tip, accent = "Profile Global", "Global data — not tied to a character", True
        elif profile:
            parts = profile.split("_")
            if parts and parts[-1] not in ("Warrior", "Rogue", "Mage", "Priest"):
                parts = parts[:-1]          # trailing uid hash (e.g. S8c4ba8)
            text, tip, accent = " ".join(parts), profile, True
        else:
            text, tip, accent = "No profile", "", False
        from PySide6.QtGui import QFontMetrics
        fm = QFontMetrics(self._map_title_lbl.font())
        self._map_title_lbl.setText(fm.elidedText(text, QtCore.Qt.ElideRight, 220))
        self._map_title_lbl.setToolTip(tip)
        self._map_title_lbl.setStyleSheet(
            (f"color:{theme.ACCENT}; font-size:11px; font-weight:bold;" if accent
             else f"color:{theme.DANGER}; font-size:11px;"))

    def _build_codex_last_hidden(self) -> QtWidgets.QFrame:
        """Compact 'Last Hidden' undo frame (68px to avoid row expansion)."""
        self._last_hidden_frame = QtWidgets.QFrame()
        self._last_hidden_frame.setFixedSize(160, 68)
        self._last_hidden_frame.setCursor(QtCore.Qt.PointingHandCursor)
        self._last_hidden_frame.setStyleSheet(
            f"QFrame {{ background: transparent; border: 0; }}"
            f"QFrame:hover {{ background: {theme.with_alpha(theme.GOLD, 20)}; border-radius: 6px; }}"
        )
        lh_lay = QtWidgets.QHBoxLayout(self._last_hidden_frame)
        lh_lay.setContentsMargins(4, 1, 6, 1)
        lh_lay.setSpacing(8)

        self._lh_icon = C.IconTile(size=60)
        self._lh_icon.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents)
        lh_lay.addWidget(self._lh_icon)

        lh_txt_lay = QtWidgets.QVBoxLayout()
        lh_txt_lay.setSpacing(2)
        lh_txt_lay.setContentsMargins(0, 0, 0, 0)
        lh_txt_lay.setAlignment(QtCore.Qt.AlignVCenter)

        self._lh_title = QtWidgets.QLabel("LAST HIDDEN")
        self._lh_title.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents)
        self._lh_title.setStyleSheet(f"color: {theme.GOLD}; font-weight: bold; font-size: 11px; letter-spacing: 0.5px;")
        lh_txt_lay.addWidget(self._lh_title)

        self._lh_name = QtWidgets.QLabel("")
        self._lh_name.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents)
        self._lh_name.setStyleSheet(f"color: {theme.GOLD}; font-weight: bold; font-size: 12px;")
        lh_txt_lay.addWidget(self._lh_name)

        lh_lay.addLayout(lh_txt_lay)
        self._last_hidden_frame.mousePressEvent = self._on_last_hidden_clicked
        self._last_hidden_frame.setVisible(False)
        self._last_hidden_uid = None
        self._last_hidden_is_pet = False
        self._last_hidden_chest_orb = None
        return self._last_hidden_frame

    def _build_codex_others_sub_bar(self) -> QtWidgets.QFrame:
        """All/Shop/Unreleased/Misc status toggles for the Others tab — the
        mounts/gliders type lists moved to the Collection tab, so Others is
        now the 'not in your collection yet' bucket: cash-shop items, content
        not released yet, and misc/todo placeholders. Same look/logic as the
        Dungeon view switcher: flat exclusive checkable toggles in the grid
        header (the old boxed 2x2 sub-tab frame is gone)."""
        self._others_sub_bar = QtWidgets.QFrame()
        self._others_sub_bar.setStyleSheet("QFrame { background: transparent; border: 0; }")
        sub_lay = QtWidgets.QHBoxLayout(self._others_sub_bar)
        sub_lay.setContentsMargins(0, 0, 0, 0)
        sub_lay.setSpacing(6)

        self._others_btn_group = QtWidgets.QButtonGroup(self)
        self._others_btn_group.setExclusive(True)
        self._others_sub_btns = {}
        for label in ("All", "Shop", "Unreleased", "Misc"):
            btn = QtWidgets.QPushButton(label)
            btn.setCheckable(True)
            btn.setFixedHeight(24)
            btn.setCursor(QtCore.Qt.PointingHandCursor)
            btn.setStyleSheet(_CODEX_TOGGLE_QSS)
            if label == "All":
                btn.setChecked(True)  # default to All on the Others tab
            self._others_btn_group.addButton(btn)
            self._others_sub_btns[label] = btn
            sub_lay.addWidget(btn)

        self._others_sub_bar.setVisible(False)
        self._others_btn_group.buttonClicked.connect(lambda _: self._refresh_codex_grid(reset_scroll=True))
        return self._others_sub_bar

    def _build_codex_collection_sub_bar(self) -> QtWidgets.QFrame:
        """Pets/Mounts/Gliders/Chests/Orbs sub-view toggles for the
        Collection tab — pets (and mounts/gliders) render as card grids, the
        Chests/Orbs toggles swap to the profile-driven remaining lists (which
        use the dungeon zone block instead of the source keys). Pets is the
        default view."""
        self._collection_sub_bar = QtWidgets.QFrame()
        self._collection_sub_bar.setStyleSheet("QFrame { background: transparent; border: 0; }")
        sub_lay = QtWidgets.QHBoxLayout(self._collection_sub_bar)
        sub_lay.setContentsMargins(0, 0, 0, 0)
        sub_lay.setSpacing(6)

        self._collection_btn_group = QtWidgets.QButtonGroup(self)
        self._collection_btn_group.setExclusive(True)
        self._collection_sub_btns = {}
        for label in ("Pets", "Mounts", "Gliders", "Chests", "Orbs"):
            btn = QtWidgets.QPushButton(label)
            btn.setCheckable(True)
            btn.setFixedHeight(24)
            btn.setCursor(QtCore.Qt.PointingHandCursor)
            btn.setStyleSheet(_CODEX_TOGGLE_QSS)
            if label == "Pets":
                btn.setChecked(True)  # default to Pets on the Collection tab
            self._collection_btn_group.addButton(btn)
            self._collection_sub_btns[label] = btn
            sub_lay.addWidget(btn)
            if label == "Chests":
                btn.clicked.connect(self._on_chest_list_toggled)
            elif label == "Orbs":
                btn.clicked.connect(self._on_orb_list_toggled)
            else:
                btn.clicked.connect(self._on_collection_sub_toggled)

        self._collection_sub_bar.setVisible(False)
        return self._collection_sub_bar

    def _build_codex_legends(self) -> QtWidgets.QVBoxLayout:
        """Dungeon-mob and Spark-Dust clickable legend toggles."""
        legend_v = QtWidgets.QVBoxLayout()
        legend_v.setSpacing(2)
        legend_v.setContentsMargins(0, 0, 0, 0)

        # Dungeon legend
        self._dungeon_filter_btn = QtWidgets.QPushButton()
        self._dungeon_filter_btn.setCheckable(True)
        self._dungeon_filter_btn.setToolTip("Filter: Show only Dungeon mobs")
        self._dungeon_filter_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self._dungeon_filter_btn.setFixedWidth(110)
        self._dungeon_filter_btn.setFixedHeight(30)
        dungeon_qss = (
            f"QPushButton {{ background: transparent; border: 1px solid transparent; border-radius: 4px; color: {theme.DIM}; font-size: 11px; font-weight: bold; text-align: left; padding: 2px; }}"
            f"QPushButton:hover {{ background: {theme.with_alpha(theme.TEXT, 20)}; border-color: {theme.with_alpha(theme.TEXT, 40)}; color: {theme.TEXT}; }}"
            f"QPushButton:checked {{ background: {theme.with_alpha(theme.TEXT, 40)}; border-color: {theme.TEXT}; color: {theme.TEXT}; }}"
        )
        self._dungeon_filter_btn.setStyleSheet(dungeon_qss)
        dungeon_btn_lay = QtWidgets.QHBoxLayout(self._dungeon_filter_btn)
        dungeon_btn_lay.setContentsMargins(4, 0, 4, 0)
        dungeon_btn_lay.setSpacing(4)
        leg_dungeon = C.IconTile(size=21)
        leg_dungeon.set_marker("dungeon", theme.TEXT, outlined=True)
        leg_dungeon.setStyleSheet("background: black; border-radius: 4px;")
        leg_dungeon.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents)
        dungeon_btn_lay.addWidget(leg_dungeon)
        lbl_dungeon = QtWidgets.QLabel("Dungeon Mob")
        lbl_dungeon.setStyleSheet("font-size: 11px; font-weight: bold;")
        lbl_dungeon.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents)
        dungeon_btn_lay.addWidget(lbl_dungeon)
        dungeon_btn_lay.addStretch(1)
        self._dungeon_filter_btn.toggled.connect(self._on_dungeon_filter_toggled)
        legend_v.addWidget(self._dungeon_filter_btn)

        # Spark Dust legend
        self._spark_filter_btn = QtWidgets.QPushButton()
        self._spark_filter_btn.setCheckable(True)
        self._spark_filter_btn.setToolTip("Filter: Show only Spark Dust drops")
        self._spark_filter_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self._spark_filter_btn.setFixedWidth(100)
        self._spark_filter_btn.setFixedHeight(30)
        spark_qss = (
            f"QPushButton {{ background: transparent; border: 1px solid transparent; border-radius: 4px; color: {theme.GOLD}; font-size: 11px; font-weight: bold; text-align: left; padding: 2px; }}"
            f"QPushButton:hover {{ background: {theme.with_alpha(theme.GOLD, 20)}; border-color: {theme.with_alpha(theme.GOLD, 40)}; color: {theme.TEXT}; }}"
            f"QPushButton:checked {{ background: {theme.with_alpha(theme.GOLD, 40)}; border-color: {theme.GOLD}; color: {theme.GOLD}; }}"
        )
        self._spark_filter_btn.setStyleSheet(spark_qss)
        spark_btn_lay = QtWidgets.QHBoxLayout(self._spark_filter_btn)
        spark_btn_lay.setContentsMargins(4, 0, 4, 0)
        spark_btn_lay.setSpacing(4)
        self._leg_spark_ico = C.IconTile(size=21)
        self._leg_spark_ico.set_marker("sparkdust", theme.GOLD, outlined=False)
        self._leg_spark_ico.setStyleSheet("background: transparent; border: 0;")
        self._leg_spark_ico.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents)
        spark_btn_lay.addWidget(self._leg_spark_ico)
        self._lbl_spark = QtWidgets.QLabel("Spark Dust")
        self._lbl_spark.setStyleSheet(f"color: {theme.GOLD}; font-size: 11px; font-weight: bold;")
        self._lbl_spark.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents)
        spark_btn_lay.addWidget(self._lbl_spark)
        self._spark_filter_btn.toggled.connect(self._on_spark_filter_toggled)
        legend_v.addWidget(self._spark_filter_btn)

        # Pets / Others source-badge legend: clickable FILTER keys for the
        # badge icons on those cards (vendor / achievement / dungeon / chest /
        # shop / mob drop) — in the same key style as the zone spark/dungeon
        # toggles. The two legend sets are mutually exclusive: zones show the
        # filter toggles, Pets/Others show these keys. There's no Reset key:
        # clicking the active key again clears the filter.
        self._source_legend_frame = QtWidgets.QFrame()
        self._source_legend_frame.setStyleSheet("QFrame { background: transparent; border: 0; }")
        src_grid = QtWidgets.QGridLayout(self._source_legend_frame)
        src_grid.setContentsMargins(0, 0, 0, 0)
        src_grid.setSpacing(2)
        src_qss = (
            f"QPushButton {{ background: transparent; border: 1px solid transparent; border-radius: 4px; "
            f"color: {theme.MUTED}; font-size: 11px; font-weight: bold; text-align: left; padding: 2px; }}"
            f"QPushButton:hover {{ background: {theme.with_alpha(theme.ACCENT, 25)}; color: {theme.TEXT}; }}"
            f"QPushButton:checked {{ background: {theme.with_alpha(theme.ACCENT, 42)}; color: {theme.ACCENT}; }}"
            f"QPushButton:disabled {{ color: {theme.DIM}; }}"
        )
        self._source_filter_btns: dict[str, QtWidgets.QPushButton] = {}
        key_specs = [
            ("Vendor", "npc", "Filter: show only vendor-sourced items"),
            ("Achievement", "ach", "Filter: show only achievement rewards"),
            ("Dungeon", "dungeon", "Filter: show only dungeon mobs"),
            ("Chest", "chest", "Filter: show only chest-dropping items"),
            ("Shop", "shop", "Filter: show only cash-shop / early-access items"),
            ("Mob Drop", "mob", "Filter: show only mob-drop items"),
        ]
        for i, (label, kind, tip) in enumerate(key_specs):
            g_row, g_col = divmod(i, 2)
            btn = QtWidgets.QPushButton()
            btn.setCheckable(True)
            btn.setToolTip(tip)
            btn.setCursor(QtCore.Qt.PointingHandCursor)
            # Fixed width like the zone spark/dungeon toggles: a QPushButton's
            # sizeHint ignores its child layout, so without this the button
            # collapses to icon-width and clips the label.
            btn.setFixedWidth(110)
            btn.setStyleSheet(src_qss)
            row = QtWidgets.QHBoxLayout(btn)
            row.setContentsMargins(4, 0, 4, 0)
            row.setSpacing(4)
            ico = C.IconTile(size=21)
            if kind == "npc":
                ico.set_marker("npc", theme.GOLD, outlined=True)
            elif kind == "ach":
                ico.set_achievement(theme.BLUE)
            elif kind == "dungeon":
                ico.set_marker("dungeon", theme.TEXT, outlined=True)
            elif kind == "shop":
                ico.set_marker("shop", theme.GOOD, outlined=True)
            elif kind == "mob":
                ico.set_marker("sword", theme.BROWN, outlined=True)
            else:
                ico.set_marker("chest", theme.GOLD, outlined=True)
            ico.setStyleSheet("background: black; border-radius: 4px;")
            ico.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents)
            lbl = QtWidgets.QLabel(label)
            lbl.setStyleSheet("font-size: 11px; font-weight: bold;")
            lbl.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents)
            row.addWidget(ico)
            row.addWidget(lbl)
            row.addStretch(1)
            self._source_filter_btns[kind] = btn
            btn.toggled.connect(self._on_source_filter_toggled)
            src_grid.addWidget(btn, g_row, g_col)

        self._source_legend_frame.setVisible(False)
        legend_v.addWidget(self._source_legend_frame)
        return legend_v

    def _build_codex_controls(self) -> QtWidgets.QFrame:
        """Right-side cell: bulk actions + status filter buttons."""
        ctrl_container = QtWidgets.QFrame()
        ctrl_container.setObjectName("Cell")
        ctrl_grid = QtWidgets.QGridLayout(ctrl_container)
        ctrl_grid.setContentsMargins(4, 4, 4, 4)
        ctrl_grid.setSpacing(4)

        grp_btn_qss = (
            f"QPushButton{{background:transparent; border:0; color:{theme.MUTED}; "
            f"font-size:12px; font-weight:bold; padding: 4px;}}"
            f"QPushButton:hover{{color:{theme.TEXT};}}"
            f"QPushButton:checked, QPushButton:pressed{{background:{theme.with_alpha(theme.ACCENT, 42)}; "
            f"color:{theme.ACCENT}; border:0;}}"
        )

        # Bulk actions (col 1) + inline confirm/cancel
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

        # Status filters (cols 2-3): R0 All/Nearby, R1 Visible/Hidden
        self._status_btns = QtWidgets.QButtonGroup(self)
        self._status_btns.setExclusive(True)
        self._status_widgets = {}
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

        # Zone filters (Dungeons tab): All / <first word of each zone name>
        # replace the status block while that tab is active, slicing the
        # dungeon list by the mob's entrance zone (rifts by their fixed spawn
        # point). The keys are tagged internally as 'All'/'Z1'/'Z2'/'Z3' — the
        # labels derive from the zone sheet (Skover Island -> 'Skover'), so
        # new zones auto-name when they land in the game. Hidden by default;
        # _update_codex_chrome swaps them in on the Dungeons tab. Clicking a
        # zone key also plots that zone's dungeon entrance pins on the map;
        # 'All' clears them.
        self._zone_btns = QtWidgets.QButtonGroup(self)
        self._zone_btns.setExclusive(True)
        self._zone_widgets = {}
        zone_names = codex.region_names()
        for tag, row, col in (("All", 0, 1), ("Z1", 0, 2), ("Z2", 1, 1), ("Z3", 1, 2)):
            full = zone_names.get(tag, tag) if tag != "All" else ""
            label = "All" if tag == "All" else (full.split()[0] if full else tag)
            btn = QtWidgets.QPushButton(label)
            btn.setCheckable(True)
            btn.setFixedSize(110, 36)
            btn.setStyleSheet(grp_btn_qss)
            btn.setToolTip("All zones" if tag == "All" else f"Show only {full} dungeons")
            if tag == "All":
                btn.setChecked(True)
            ctrl_grid.addWidget(btn, row, col)
            self._zone_btns.addButton(btn)
            self._zone_widgets[tag] = btn
            btn.setVisible(False)
        self._zone_btns.buttonClicked.connect(self._on_dungeon_zone_clicked)
        return ctrl_container

    def _build_codex_grid_header(self) -> QtWidgets.QHBoxLayout:
        """Dungeon view switcher + codex count label above the grid."""
        grid_header = QtWidgets.QHBoxLayout()
        grid_header.setContentsMargins(0, 0, 0, 2)
        grid_header.setSpacing(10)

        btn_compact_qss = _CODEX_TOGGLE_QSS

        # Dungeons-tab view switcher: the list is the default view, with a
        # 'Dungeon Mobs' button for the old card-grid look. Lives in the grid
        # header, not the map. 'Dungeon Mobs' comes first, then 'Dungeon List'
        # (the default view sits on the right).
        self._btn_dungeon_mobs = QtWidgets.QPushButton("Dungeon Mobs")
        self._btn_dungeon_mobs.setCheckable(True)
        self._btn_dungeon_mobs.setFixedHeight(24)
        self._btn_dungeon_mobs.setStyleSheet(btn_compact_qss)
        self._btn_dungeon_mobs.setToolTip("Old look: the dungeon mob card grid")
        self._btn_dungeon_mobs.setVisible(False)
        self._btn_dungeon_mobs.clicked.connect(self._on_dungeon_mobs_toggled)
        self._btn_dungeon_list = QtWidgets.QPushButton("Dungeon List")
        self._btn_dungeon_list.setCheckable(True)
        self._btn_dungeon_list.setFixedHeight(24)
        self._btn_dungeon_list.setStyleSheet(btn_compact_qss)
        self._btn_dungeon_list.setToolTip("Click-to-track list of all dungeons")
        self._btn_dungeon_list.setVisible(False)
        self._btn_dungeon_list.clicked.connect(self._on_dungeon_list_toggled)

        self._dungeon_view_group = QtWidgets.QButtonGroup(self)
        self._dungeon_view_group.setExclusive(True)
        self._dungeon_view_group.addButton(self._btn_dungeon_list)
        self._dungeon_view_group.addButton(self._btn_dungeon_mobs)

        # Count label first, so it sits to the LEFT of the view-switch buttons
        # (Dungeons: Dungeon Mobs/List; Collection: All/Pets/Mounts/Gliders/
        # Chests/Orbs; Others: All/Shop/Unreleased/TODO) and stays in the same
        # left spot on every tab.
        self._codex_count = QtWidgets.QLabel("")
        self._codex_count.setStyleSheet(f"color:{theme.DIM};font-family:'{theme.MONO_FONT}';font-size:11px;font-weight:bold;")
        grid_header.addWidget(self._codex_count)
        self._others_sub_bar = self._build_codex_others_sub_bar()
        grid_header.addWidget(self._others_sub_bar)
        self._collection_sub_bar = self._build_codex_collection_sub_bar()
        grid_header.addWidget(self._collection_sub_bar)
        grid_header.addWidget(self._btn_dungeon_mobs)
        grid_header.addWidget(self._btn_dungeon_list)
        grid_header.addStretch(1)
        return grid_header

    # --- tab / search / filter handlers ---------------------------------
    def _on_tab_changed(self, index: int) -> None:
        """Clear the plotted map pins when switching tabs (they belong to the
        previous tab's context) — the live tracker (overlay compass / HUD
        waypoint) persists. Switch to 'All' status automatically when entering
        the Dungeons tab."""
        self._clear_codex_map_selection()
        self._zone_pins_active = False
        tab_label = self._codex_tabs.currentText().strip()
        if tab_label == "Dungeons":
            if hasattr(self, "_status_widgets"):
                self._status_widgets["All"].setChecked(True)
        self._refresh_codex_grid(reset_scroll=True)

    def _on_search_changed(self, text: str) -> None:
        """Type-to-search automatically switches to ALL status to show hidden matches with 150ms debounce."""
        if not hasattr(self, "_search_timer"):
            self._search_timer = QtCore.QTimer(self)
            self._search_timer.setSingleShot(True)
            self._search_timer.timeout.connect(lambda: self._refresh_codex_grid(reset_scroll=True))

        if text.strip() and hasattr(self, "_status_btns"):
            checked = self._status_btns.checkedButton()
            if checked and checked.text().lower() != "all":
                for btn in self._status_btns.buttons():
                    if btn.text().lower() == "all":
                        btn.setChecked(True)
                        break
        self._search_timer.start(150)

    def _on_spark_filter_toggled(self, checked: bool) -> None:
        """Spark Dust zone key — only ONE zone filter may be active at a time:
        checking it unchecks the Dungeon key, and clicking the active key
        again clears the filter (mirrors the Pets/Others source keys)."""
        if checked:
            self._dungeon_filter_btn.blockSignals(True)
            self._dungeon_filter_btn.setChecked(False)
            self._dungeon_filter_btn.blockSignals(False)
        self._spark_only = checked
        self._dungeon_only = False
        self._refresh_codex_grid(reset_scroll=False)

    def _on_dungeon_filter_toggled(self, checked: bool) -> None:
        """Dungeon Mob zone key — only ONE zone filter may be active at a time
        (mirrors the Pets/Others source keys)."""
        if checked:
            self._spark_filter_btn.blockSignals(True)
            self._spark_filter_btn.setChecked(False)
            self._spark_filter_btn.blockSignals(False)
        self._dungeon_only = checked
        self._spark_only = False
        self._refresh_codex_grid(reset_scroll=False)

    def _dungeon_zone_tag_for(self, btn) -> str:
        """Zone tag ('All'/'Z1'/'Z2'/'Z3') for a zone-filter button — the
        labels are display-only (zone-name first words), the tag is the key."""
        for tag, b in self._zone_widgets.items():
            if b is btn:
                return tag
        checked = self._zone_btns.checkedButton()
        return next((t for t, b in self._zone_widgets.items() if b is checked), "All")

    def _on_dungeon_zone_clicked(self, btn=None) -> None:
        """Zone filter key clicked: rebuild the grid for the zone AND plot the
        entrance pins of the filtered dungeons on the map — the same pin the
        dungeon list's tracking click plots. 'All' clears the pins."""
        zone = self._dungeon_zone_tag_for(btn)
        self._refresh_codex_grid(reset_scroll=False)
        if zone == "All":
            self._clear_zone_dungeon_pins()
        elif self._codex_chest_orb_view_active():
            # Chests / Orbs view: the zone keys plot that zone's remaining
            # chest or orb pins instead of dungeon entrances.
            self._plot_remaining_chest_orbs(zone)
        else:
            self._plot_zone_dungeon_pins(zone)

    def _clear_zone_dungeon_pins(self) -> None:
        """Return the codex map to its idle state (no zone pins)."""
        self._zone_pins_active = False
        if hasattr(self, "_codex_map_widget"):
            self._codex_map_widget.set_multi_pins("", [])
        if hasattr(self, "_map_info_lbl"):
            self._map_info_lbl.setText("Left-click card to plot spawns")

    def _plot_zone_dungeon_pins(self, zone: str) -> None:
        """Plot entrance pins for every dungeon in the selected zone (same
        coords as the dungeon list's tracking clicks)."""
        self._zone_pins_active = True
        if not hasattr(self, "_codex_map_widget"):
            return
        from ....data import dungeons
        pins = []
        for d in dungeons.load_dungeons():
            if dungeons.zone_tag(d) != zone:
                continue
            xy = self._dungeon_entrance_xy(d, zone)
            if xy is None:
                continue
            pins.append({
                "x": xy[0], "y": xy[1],
                "color": QtGui.QColor(theme.KIND_COLOR.get("dungeon", "#7c3aed")),
                "name": d.get("boss_name") or d.get("name") or "",
                "is_dungeon": True,
            })
        self._codex_map_widget.set_multi_pins(f"{zone} Dungeons", pins)
        if hasattr(self, "_map_info_lbl"):
            self._map_info_lbl.setText(f"{len(pins)} {zone} dungeon entrances plotted")

    def _plot_all_dungeon_pins(self) -> None:
        """Plot entrance pins for every dungeon across all zones — the
        Dungeons-tab 'Dungeon Locations' button, which the zone keys (one zone
        at a time) don't cover."""
        self._zone_pins_active = False
        if not hasattr(self, "_codex_map_widget"):
            return
        from ....data import dungeons
        pins = []
        for d in dungeons.load_dungeons():
            zone = dungeons.zone_tag(d)
            xy = self._dungeon_entrance_xy(d, zone)
            if xy is None:
                continue
            pins.append({
                "x": xy[0], "y": xy[1],
                "color": QtGui.QColor(theme.KIND_COLOR.get("dungeon", "#7c3aed")),
                "name": d.get("boss_name") or d.get("name") or "",
                "is_dungeon": True,
            })
        self._codex_map_widget.set_multi_pins("All Dungeons", pins)
        if hasattr(self, "_map_info_lbl"):
            self._map_info_lbl.setText(f"{len(pins)} dungeon entrances plotted")

    def _dungeon_entrance_xy(self, d: dict, zone: str) -> tuple[float, float] | None:
        """(x, y) for a dungeon's zone pin. For rifts this is the rift spawn
        point that actually sits in `zone`: both rifts resolve to both rift
        POIs (coords[0] is always the Z2 Krisomal spot), so pinning a Z1 rift
        at coords[0] would put it in the wrong zone."""
        if d.get("entrance_zone") == "Rifts":
            from ....geo import pois as geo_pois
            from ....data import dungeons
            for p in geo_pois.load_pois():
                if p.sub_kind == "rift" and dungeons.zone_tag_from_zone_id(p.zone or "") == zone:
                    return float(p.x), float(p.y)
            return None
        key = self._dungeon_track_key(d)
        if not key:
            return None
        try:
            xyz = key[1].split("|")[0]
            return float(xyz.split(",")[0]), float(xyz.split(",")[1])
        except (ValueError, AttributeError, IndexError):
            return None

    def _on_source_filter_toggled(self, checked: bool) -> None:
        """A source-badge key (Vendor / Achievement / Dungeon / Chest / Shop)
        was toggled on the Pets/Others legend — only ONE key may be active at
        a time: checking a key unchecks the others, and clicking the active
        key again clears the filter entirely. The Others sub-view (All /
        Mounts / Gliders / Misc) is NEVER moved: the key filters within
        whatever sub-view is currently selected."""
        if checked:
            sender = self.sender()
            for kind, btn in self._source_filter_btns.items():
                if btn is not sender and btn.isChecked():
                    btn.blockSignals(True)
                    btn.setChecked(False)
                    btn.blockSignals(False)
        self._source_filters = {kind for kind, btn in self._source_filter_btns.items()
                                if btn.isChecked()}
        self._refresh_codex_grid(reset_scroll=False)

    def _sync_codex_to_current_zone(self) -> None:
        """Switch the Codex tab to match the player's current region."""
        if not hasattr(self, "model") or not self.model:
            return
        xyz = self.model.player_xyz()
        if not xyz:
            return

        from ....geo import zones
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
            # Avoid redundant updates if already on the right tab. Plotted
            # pins belong to the previous tab's context — clear them like a
            # manual tab click would (the live tracker persists).
            if self._codex_tabs.currentText() != target_name:
                self._clear_codex_map_selection()
                self._zone_pins_active = False
                self._codex_tabs.setCurrentText(target_name)
