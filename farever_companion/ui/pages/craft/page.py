"""Craft-page assembly with searchable recipe tile grid and queue tab."""
from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from ... import theme
from ... import components as C
from ...layout import FlowLayout, make_scroll
from .... import planner
from ....data import icons
from ....data import items as idata
from ..items import support
from . import detail

_CRAFT_JOB_CHIPS = ["Blacksmith", "Outfitter", "Jeweller", "Alchemist", "Cook"]


class CraftPageBase:
    def _page_craft(self):
        page, v = self._page_container()

        # no CRAFT title or queue-count pill here — the two tabs ARE the
        # header, and the Craft List tab carries the queue count (gold)
        all_recipes = idata.recipes()
        self._craft_all = all_recipes
        self._craft_job: str = ""
        self._craft_qty = 1
        self._craft_queue: list[dict] = planner.queue_entries()
        self._craft_got: dict[str, int] = planner.queue_got()

        self._craft_tabs = C.SegmentedControl(["Recipes", "Jobs", "Craft List"])
        self._craft_tabs.currentChanged.connect(self._craft_tab_changed)
        v.addWidget(self._craft_tabs)

        self._craft_stack = QtWidgets.QStackedWidget()
        v.addWidget(self._craft_stack, 1)

        browse = QtWidgets.QWidget()
        cols = QtWidgets.QHBoxLayout(browse)
        cols.setContentsMargins(0, 0, 0, 0)
        cols.setSpacing(24)

        left = QtWidgets.QVBoxLayout()
        left.setSpacing(8)      # tight: chips hug the search box
        self._craft_search = C.SearchInput(
            "Search recipe, item or job…", on_text_changed=self._craft_refilter)
        self._craft_search.setFixedHeight(34)
        left.addWidget(self._craft_search)

        self._craft_chip_btns: dict[str, C.FilterChip] = {}
        job_flow = FlowLayout()
        job_flow.setContentsMargins(0, 0, 0, 0)
        job_flow.setSpacing(6)
        jobs_by_id = {j["id"]: j for j in idata.jobs()}
        active_job_ids = [jid for jid in _CRAFT_JOB_CHIPS if any(r["job"] == jid for r in all_recipes)] or list(_CRAFT_JOB_CHIPS)
        for jid in active_job_ids:
            j = jobs_by_id.get(jid)
            chip = C.FilterChip(j["name"] if j else jid, checked=False, parent=browse)
            chip.clicked.connect(lambda _=False, jid=jid: self._craft_chip_clicked(jid))
            self._craft_chip_btns[jid] = chip
            job_flow.addWidget(chip)
        left.addLayout(job_flow)

        self._craft_list = QtWidgets.QListWidget()
        self._craft_list.setViewMode(QtWidgets.QListView.ListMode)
        self._craft_list.setMovement(QtWidgets.QListView.Static)
        self._craft_list.setWrapping(False)
        self._craft_list.setWordWrap(True)   # item names wrap up to two rows
        self._craft_list.setVerticalScrollMode(QtWidgets.QAbstractItemView.ScrollPerPixel)
        self._craft_list.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self._craft_list.setAutoScroll(False)
        self._craft_list.setIconSize(QtCore.QSize(detail.LIST_ICON, detail.LIST_ICON))
        self._craft_list.setMinimumHeight(0)
        self._craft_delegate = detail.CraftTileDelegate(self._craft_list)
        self._craft_list.setItemDelegate(self._craft_delegate)
        self._craft_list.setStyleSheet(
            f"QListWidget{{background:{theme.PANEL};border:1px solid {theme.BORDER};outline:none;}}"
            f"QListWidget::item{{padding:2px 8px;border-radius:4px;margin:1px 0px;}}"
            f"QListWidget::item:hover{{background:{theme.with_alpha(theme.ACCENT,15)};}}"
            f"QListWidget::item:selected{{background:{theme.with_alpha(theme.ACCENT,30)};"
            f"border:1px solid {theme.ACCENT};color:{theme.TEXT};}}")
        self._craft_list.currentItemChanged.connect(self._craft_show)
        self._craft_list.itemClicked.connect(self._craft_item_clicked)
        self._craft_list.setMinimumWidth(440)
        left.addWidget(self._craft_list, 1)
        cols.addLayout(left, 2)

        right = QtWidgets.QVBoxLayout()
        right.setSpacing(12)
        self._craft_detail = QtWidgets.QFrame()
        self._craft_detail.setObjectName("Card")
        self._craft_detail_lay = QtWidgets.QVBoxLayout(self._craft_detail)
        self._craft_detail_lay.setContentsMargins(18, 16, 18, 16)
        self._craft_scroll = make_scroll(self._craft_detail)
        right.addWidget(self._craft_scroll, 1)
        cols.addLayout(right, 3)
        self._craft_stack.addWidget(browse)  # Index 0: Recipes

        # Index 1: Jobs page from job.json (lazy-built on first visit)
        self._craft_jobs_placeholder = QtWidgets.QWidget()
        self._craft_stack.addWidget(self._craft_jobs_placeholder)
        self._jobs_page_built = False

        # Index 2: Craft List / Queue page
        queue_page = QtWidgets.QWidget()
        qv = QtWidgets.QVBoxLayout(queue_page)
        qv.setContentsMargins(0, 0, 0, 0)
        qv.setSpacing(12)
        self._craft_rail = QtWidgets.QFrame()
        self._craft_rail.setStyleSheet(
            f"QFrame{{background:{theme.PANEL};border:1px solid {theme.with_alpha(theme.GOLD, 70)};}}")
        _rail_v = QtWidgets.QVBoxLayout(self._craft_rail)
        _rail_v.setContentsMargins(0, 0, 0, 0)
        self._craft_rail_body = QtWidgets.QWidget()
        self._craft_rail_lay = QtWidgets.QVBoxLayout(self._craft_rail_body)
        self._craft_rail_lay.setContentsMargins(18, 16, 18, 16)
        self._craft_rail_lay.setSpacing(8)
        _rail_sc = make_scroll(self._craft_rail_body)
        _rail_v.addWidget(_rail_sc)

        self._craft_rail_empty = C.InfoCard(
            "archive", "Craft List is empty",
            "Add a recipe from the Recipes tab (ADD TO QUEUE) and its batch lands here.")
        qv.addWidget(self._craft_rail_empty, 1)
        qv.addWidget(self._craft_rail, 1)
        self._craft_stack.addWidget(queue_page)

        self._craft_build_list()
        # Blacksmith is the default job on open (the user's first chip) —
        # the list lands on its recipes instead of the bare section bars
        self._craft_job = "Blacksmith"
        if "Blacksmith" in self._craft_chip_btns:
            self._craft_chip_btns["Blacksmith"].setChecked(True)
        self._craft_refilter()
        self._craft_list.setCurrentItem(None)
        self._craft_list.clearSelection()
        self._craft_list.setCurrentIndex(QtCore.QModelIndex())
        self._craft_show(None)
        self._craft_rail_built = False
        self._craft_rail_sync()
        QtCore.QTimer.singleShot(50, self._craft_ensure_jobs)
        return page


    def _build_jobs_page(self) -> QtWidgets.QWidget:
        """The Jobs list tab: a clean, compact catalog of all recipes with
        job, recipe name, craft level, and unlock source (Auto-learn vs. Dropped Scroll)."""
        page = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)

        # Header filter bar: search input + job chips + count badge
        top_bar = QtWidgets.QHBoxLayout()
        top_bar.setSpacing(10)

        self._jobs_search = C.SearchInput(
            "Search recipe, job, level, unlock source…",
            on_text_changed=lambda _: self._filter_jobs_page())
        self._jobs_search.setFixedHeight(34)
        top_bar.addWidget(self._jobs_search, 1)

        self._jobs_active_job = ""
        self._jobs_job_chips: dict[str, C.FilterChip] = {}
        job_chip_flow = FlowLayout()
        job_chip_flow.setContentsMargins(0, 0, 0, 0)
        job_chip_flow.setSpacing(6)

        # Job chips: All + active jobs with recipes
        all_chip = C.FilterChip("All Jobs", checked=True, parent=page)
        all_chip.clicked.connect(lambda _=False: self._jobs_filter_job(""))
        self._jobs_job_chips[""] = all_chip
        job_chip_flow.addWidget(all_chip)

        active_jobs = idata.craft_jobs()
        for jid in active_jobs:
            chip = C.FilterChip(jid, checked=False, parent=page)
            chip.clicked.connect(lambda _=False, _j=jid: self._jobs_filter_job(_j))
            self._jobs_job_chips[jid] = chip
            job_chip_flow.addWidget(chip)

        top_bar.addLayout(job_chip_flow)

        self._jobs_dropped_only = False
        self._jobs_count_btn = QtWidgets.QPushButton(f"{len(self._craft_all)} RECIPES")
        self._jobs_count_btn.setObjectName("Mono")
        self._jobs_count_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self._jobs_count_btn.setToolTip("Click to toggle: show Dropped Scrolls Only vs. All Recipes")
        self._jobs_count_btn.setStyleSheet(
            f"QPushButton {{ color:{theme.GOLD}; font-family:'{theme.MONO_FONT}'; font-size:11px; font-weight:700; "
            f"background:{theme.with_alpha(theme.GOLD, 20)}; border:1px solid {theme.with_alpha(theme.GOLD, 80)}; "
            f"border-radius:4px; padding:3px 8px; }}"
            f"QPushButton:hover {{ background:{theme.with_alpha(theme.GOLD, 40)}; }}")
        self._jobs_count_btn.clicked.connect(self._jobs_toggle_dropped_only)
        top_bar.addWidget(self._jobs_count_btn)
        lay.addLayout(top_bar)

        # Table header
        th = QtWidgets.QFrame()
        th.setObjectName("Card")
        th.setStyleSheet(
            f"QFrame#Card {{ background: {theme.PANEL_LOW}; border: 1px solid {theme.BORDER}; "
            f"border-radius: 6px; padding: 0px; }}")
        th_lay = QtWidgets.QHBoxLayout(th)
        th_lay.setContentsMargins(14, 6, 26, 6)
        th_lay.setSpacing(14)

        j_hdr = QtWidgets.QLabel("JOB")
        j_hdr.setFixedWidth(120)
        j_hdr.setAlignment(QtCore.Qt.AlignCenter)
        j_hdr.setStyleSheet(
            f"color:{theme.DIM};font-size:10px;font-weight:700;letter-spacing:1.5px;background:transparent;")
        th_lay.addWidget(j_hdr)

        rec_hdr_box = QtWidgets.QHBoxLayout()
        rec_hdr_box.setSpacing(8)
        rec_hdr_spacer = QtWidgets.QWidget()
        rec_hdr_spacer.setFixedSize(26, 1)
        rec_hdr_box.addWidget(rec_hdr_spacer)
        r_hdr = QtWidgets.QLabel("RECIPE")
        r_hdr.setStyleSheet(
            f"color:{theme.DIM};font-size:10px;font-weight:700;letter-spacing:1.5px;background:transparent;")
        rec_hdr_box.addWidget(r_hdr, 1)
        th_lay.addLayout(rec_hdr_box, 1)

        lvl_hdr = QtWidgets.QLabel("LEVEL")
        lvl_hdr.setFixedWidth(70)
        lvl_hdr.setStyleSheet(
            f"color:{theme.DIM};font-size:10px;font-weight:700;letter-spacing:1.5px;background:transparent;")
        th_lay.addWidget(lvl_hdr)

        src_hdr_box = QtWidgets.QHBoxLayout()
        src_hdr_box.setSpacing(8)
        src_hdr_spacer = QtWidgets.QWidget()
        src_hdr_spacer.setFixedSize(26, 1)
        src_hdr_box.addWidget(src_hdr_spacer)
        src_hdr = QtWidgets.QLabel("LEARN SOURCE")
        src_hdr.setStyleSheet(
            f"color:{theme.DIM};font-size:10px;font-weight:700;letter-spacing:1.5px;background:transparent;")
        src_hdr_box.addWidget(src_hdr, 1)
        src_hdr_widget = QtWidgets.QWidget()
        src_hdr_widget.setFixedWidth(300)
        src_hdr_w_lay = QtWidgets.QHBoxLayout(src_hdr_widget)
        src_hdr_w_lay.setContentsMargins(0, 0, 0, 0)
        src_hdr_w_lay.addLayout(src_hdr_box)
        th_lay.addWidget(src_hdr_widget)

        lay.addWidget(th)

        # Scrollable rows body
        scroll_body = QtWidgets.QWidget()
        self._jobs_rows_lay = QtWidgets.QVBoxLayout(scroll_body)
        self._jobs_rows_lay.setContentsMargins(0, 0, 0, 0)
        self._jobs_rows_lay.setSpacing(3)

        self._jobs_rows: list[tuple[QtWidgets.QWidget, dict]] = []
        job_order = {j: i for i, j in enumerate(idata.craft_jobs())}
        for r in sorted(self._craft_all, key=lambda x: (-int(x.get("level", 1)), job_order.get(x.get("job"), 99), x.get("name", "").lower())):
            row = self._build_job_recipe_row(r)
            self._jobs_rows.append((row, r))
            self._jobs_rows_lay.addWidget(row)

        self._jobs_rows_lay.addStretch(1)
        scroll = make_scroll(scroll_body)
        lay.addWidget(scroll, 1)
        return page

    def _build_job_recipe_row(self, r: dict) -> QtWidgets.QWidget:
        """One compact recipe row in the Jobs catalog with full item tile boxes."""
        row = QtWidgets.QFrame()
        row.setObjectName("Card")
        row.setStyleSheet(
            f"QFrame#Card {{ background: {theme.PANEL}; border: 1px solid {theme.BORDER}; "
            f"border-radius: 6px; padding: 2px 4px; }}"
            f"QFrame#Card:hover {{ border-color: {theme.ACCENT}; background: {theme.with_alpha(theme.ACCENT, 20)}; }}")
        rl = QtWidgets.QHBoxLayout(row)
        rl.setContentsMargins(14, 6, 14, 6)
        rl.setSpacing(14)

        # Job badge
        job_name = r.get("job_name", r.get("job", "")).upper()
        col = theme.ACCENT if r.get("job") == "Blacksmith" else theme.ORANGE
        j_lbl = QtWidgets.QLabel(job_name)
        j_lbl.setFixedWidth(120)
        j_lbl.setObjectName("Mono")
        j_lbl.setStyleSheet(
            f"color:{col};font-family:'{theme.MONO_FONT}';font-size:12px;font-weight:700;"
            f"padding:2px 8px;border-radius:4px;background:{theme.with_alpha(col, 22)};"
            f"border:1px solid {theme.with_alpha(col, 60)};")
        j_lbl.setAlignment(QtCore.Qt.AlignCenter)
        rl.addWidget(j_lbl)

        # Recipe column (Tile Box + Clickable Name)
        rec_box = QtWidgets.QHBoxLayout()
        rec_box.setSpacing(8)
        rec_ic = QtWidgets.QLabel()
        rec_ic.setFixedSize(26, 26)
        r_rar_col = theme.rarity_color(r.get("rarity") or "")
        rec_ic.setPixmap(icons.item_tile(r["item"], r.get("name", ""), 26, r_rar_col))
        rec_box.addWidget(rec_ic)

        name_btn = QtWidgets.QPushButton(r.get("name", ""))
        name_btn.setFlat(True)
        name_btn.setCursor(QtCore.Qt.PointingHandCursor)
        name_btn.setStyleSheet(
            f"QPushButton {{ color: {theme.TEXT}; font-size: 14px; font-weight: 600; "
            f"text-align: left; border: 0; background: transparent; }}"
            f"QPushButton:hover {{ color: {theme.ACCENT}; text-decoration: underline; }}")
        name_btn.clicked.connect(lambda _=False, iid=r["item"]: self._craft_jump_from_jobs_tab(iid))
        rec_box.addWidget(name_btn, 1)
        rl.addLayout(rec_box, 1)

        # Level
        lvl_lbl = QtWidgets.QLabel(f"LV {r.get('level', 1)}")
        lvl_lbl.setFixedWidth(70)
        lvl_lbl.setObjectName("Mono")
        lvl_lbl.setStyleSheet(
            f"color:{theme.TEXT};font-family:'{theme.MONO_FONT}';font-size:13px;font-weight:700;background:transparent;")
        rl.addWidget(lvl_lbl)

        # Learn Source (Tile Box + Clean Text)
        src_widget = QtWidgets.QWidget()
        src_widget.setFixedWidth(300)
        src_box = QtWidgets.QHBoxLayout(src_widget)
        src_box.setContentsMargins(0, 0, 0, 0)
        src_box.setSpacing(8)

        src_ic = QtWidgets.QLabel()
        src_ic.setFixedSize(26, 26)

        unlock = r.get("unlock")
        if unlock:
            scroll_nm = r.get("unlock_name") or idata.item_name(unlock)
            if not scroll_nm or scroll_nm == unlock:
                scroll_nm = unlock.replace("Recipe_", "").replace("_", " ") + " Scroll"
            src_ic.setPixmap(icons.item_tile(unlock, scroll_nm, 26, theme.GOLD))
            src_box.addWidget(src_ic)

            src_lbl = QtWidgets.QLabel(scroll_nm)
            src_lbl.setStyleSheet(
                f"color:{theme.GOLD};font-size:12px;font-weight:600;background:transparent;")
            src_lbl.setToolTip(f"Recipe drops as a scroll / tome item ({scroll_nm})")
        else:
            src_ic.setPixmap(icons.tile_ui("check", 26, theme.GOOD))
            src_box.addWidget(src_ic)

            src_lbl = QtWidgets.QLabel(f"Auto-Learned (LV {r.get('level', 1)})")
            src_lbl.setStyleSheet(
                f"color:{theme.GOOD};font-size:12px;font-weight:500;background:transparent;")
            src_lbl.setToolTip(f"Automatically unlocked upon reaching {job_name} level {r.get('level', 1)}")

        src_lbl.setTextFormat(QtCore.Qt.PlainText)
        src_box.addWidget(src_lbl, 1)
        rl.addWidget(src_widget)

        return row

    def _craft_jump_from_jobs_tab(self, item_id: str) -> None:
        """Switch from Jobs tab to Recipes tab and focus the selected recipe."""
        if hasattr(self, "_nav_history") and not getattr(self._nav_history, "_restoring", False):
            self._nav_history.record("craft:Jobs", "craft:Recipes")
        self._craft_active_tab = "Recipes"
        self._craft_tabs.setCurrentText("Recipes")
        self._craft_tab_changed("Recipes", record_history=False)
        self._craft_jump_to_recipe(item_id)

    def _jobs_filter_job(self, jid: str) -> None:
        self._jobs_active_job = jid
        for j, chip in self._jobs_job_chips.items():
            chip.setChecked(j == jid)
        self._filter_jobs_page()

    def _jobs_toggle_dropped_only(self) -> None:
        self._jobs_dropped_only = not getattr(self, "_jobs_dropped_only", False)
        if self._jobs_dropped_only:
            self._jobs_count_btn.setStyleSheet(
                f"QPushButton {{ color:{theme.BG}; font-family:'{theme.MONO_FONT}'; font-size:11px; font-weight:700; "
                f"background:{theme.GOLD}; border:1px solid {theme.GOLD}; "
                f"border-radius:4px; padding:3px 8px; }}"
                f"QPushButton:hover {{ background:{theme.GOLD}; }}")
        else:
            self._jobs_count_btn.setStyleSheet(
                f"QPushButton {{ color:{theme.GOLD}; font-family:'{theme.MONO_FONT}'; font-size:11px; font-weight:700; "
                f"background:{theme.with_alpha(theme.GOLD, 20)}; border:1px solid {theme.with_alpha(theme.GOLD, 80)}; "
                f"border-radius:4px; padding:3px 8px; }}"
                f"QPushButton:hover {{ background:{theme.with_alpha(theme.GOLD, 40)}; }}")
        self._filter_jobs_page()

    def _filter_jobs_page(self) -> None:
        if not hasattr(self, "_jobs_rows"):
            return
        q = (self._jobs_search.text() if hasattr(self, "_jobs_search") else "").strip().lower()
        active_j = getattr(self, "_jobs_active_job", "")
        dropped_only = getattr(self, "_jobs_dropped_only", False)
        vis_cnt = 0
        for row_w, r in self._jobs_rows:
            if dropped_only and not r.get("unlock"):
                row_w.setVisible(False)
                continue
            match_job = not active_j or r.get("job") == active_j
            if not match_job:
                row_w.setVisible(False)
                continue
            if not q:
                row_w.setVisible(True)
                vis_cnt += 1
                continue
            hay = f"{r.get('name', '')} {r.get('job_name', '')} {r.get('job', '')} lv {r.get('level', '')} {r.get('unlock_name', '')}".lower()
            match_q = q in hay
            row_w.setVisible(match_q)
            if match_q:
                vis_cnt += 1
        if hasattr(self, "_jobs_count_btn"):
            if dropped_only:
                self._jobs_count_btn.setText(f"📜 {vis_cnt} DROPPED RECIPES")
            else:
                self._jobs_count_btn.setText(f"{vis_cnt} RECIPES")

    def _craft_chip_clicked(self, jid: str) -> None:
        if self._craft_job == jid:
            self._craft_job = ""
            for c in self._craft_chip_btns.values():
                c.setChecked(False)
        else:
            self._craft_job = jid
            for j, c in self._craft_chip_btns.items():
                c.setChecked(j == jid)
        self._craft_refilter()

    def _craft_item_clicked(self, item) -> None:
        """Open the clicked recipe's card in the detail pane (the group
        bars are inert — _craft_show ignores them)."""
        if item is not None and not item.data(detail.HEADER_ROLE):
            self._craft_show(item)

    def _craft_tab_changed(self, text: str, record_history: bool = True) -> None:
        target_name = "Craft List" if text.startswith("Craft List") else text
        old_tab = getattr(self, "_craft_active_tab", "Recipes")
        if record_history and hasattr(self, "_nav_history") and not getattr(self._nav_history, "_restoring", False):
            if old_tab != target_name:
                self._nav_history.record(f"craft:{old_tab}", f"craft:{target_name}")
        self._craft_active_tab = target_name

        if text.startswith("Craft List"):
            self._craft_ensure_rail()
            if hasattr(self, "_craft_stack"):
                self._craft_stack.setCurrentIndex(2)
        elif text == "Jobs":
            self._craft_ensure_jobs()
            if hasattr(self, "_craft_stack"):
                self._craft_stack.setCurrentIndex(1)
        else:
            if hasattr(self, "_craft_stack"):
                self._craft_stack.setCurrentIndex(0)

    def _craft_ensure_jobs(self) -> None:
        if getattr(self, "_jobs_page_built", False):
            return
        self._jobs_page_built = True
        jobs_page = self._build_jobs_page()
        if hasattr(self, "_craft_jobs_placeholder") and self._craft_jobs_placeholder is not None:
            self._craft_stack.removeWidget(self._craft_jobs_placeholder)
            self._craft_jobs_placeholder.deleteLater()
            self._craft_jobs_placeholder = None
        self._craft_stack.insertWidget(1, jobs_page)

    def _craft_ensure_rail(self) -> None:
        if getattr(self, "_craft_rail_built", False):
            return
        self._craft_rail_built = True
        self._craft_rail_build()

    def _craft_build_list(self) -> None:
        self._craft_list.blockSignals(True)
        self._craft_list.clear()
        row_h = detail._craft_row_height(self._craft_list.font())
        job_order = list(_CRAFT_JOB_CHIPS) + [j for j in idata.craft_jobs() if j not in _CRAFT_JOB_CHIPS]
        self._craft_job_order = job_order
        lazy_items: list[tuple[QtWidgets.QListWidgetItem, dict, str]] = []
        first_job = "Blacksmith"
        for jid in job_order:
            group = [r for r in self._craft_all if r["job"] == jid]
            if not group:
                continue
            jname = group[0]["job_name"]
            th = QtWidgets.QListWidgetItem(f"{jname.upper()}  ·  {len(group)}")
            th.setData(detail.HEADER_ROLE, True)
            th.setData(detail.CAT_ROLE, jid)
            th.setData(detail.NAME_ROLE, jname)
            th.setFlags(QtCore.Qt.ItemIsEnabled)
            th.setSizeHint(QtCore.QSize(detail.HEADER_W, detail.HEADER_H))
            self._craft_list.addItem(th)
            for r in group:
                text = r["name"] + (f"  ×{r['count']}" if r["count"] > 1 else "")
                col = theme.rarity_color(r.get("rarity") or "")
                li = QtWidgets.QListWidgetItem(text)
                li.setForeground(QtGui.QColor(col))
                li.setSizeHint(QtCore.QSize(0, row_h))
                li.setTextAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
                li.setData(detail.ID_ROLE, r["item"])
                li.setData(detail.DATA_ROLE, r)
                li.setData(detail.QUEUED_ROLE, r["item"] in self._craft_queued_ids())
                if jid == first_job:
                    li.setIcon(QtGui.QIcon(icons.item_tile(r["item"], r["name"], detail.LIST_ICON, col)))
                else:
                    lazy_items.append((li, r, col))
                self._craft_list.addItem(li)
        self._craft_list.blockSignals(False)
        self._craft_fill_icons(lazy_items)

    def _craft_fill_icons(self, items_to_fill: list[tuple], batch: int = 50) -> None:
        if not items_to_fill:
            return
        idx = 0
        timer = QtCore.QTimer(self._craft_list)
        timer.setInterval(0)

        def _tick():
            nonlocal idx
            end = min(idx + batch, len(items_to_fill))
            for li, r, col in items_to_fill[idx:end]:
                li.setIcon(QtGui.QIcon(icons.item_tile(r["item"], r["name"], detail.LIST_ICON, col)))
            idx = end
            if idx >= len(items_to_fill):
                timer.stop()
                timer.deleteLater()

        timer.timeout.connect(_tick)
        timer.start()

    def _craft_queued_ids(self) -> set[str]:
        return {e["item"] for e in self._craft_queue_get()}

    def _craft_tiles_sync_queue(self) -> None:
        if not hasattr(self, "_craft_list"):
            return
        base = self._craft_list
        qset = self._craft_queued_ids()
        for i in range(base.count()):
            li = base.item(i)
            r = li.data(detail.DATA_ROLE)
            if not r:
                continue
            queued = r["item"] in qset
            if bool(li.data(detail.QUEUED_ROLE)) != queued:
                li.setData(detail.QUEUED_ROLE, queued)
        base.viewport().update()

    def _craft_reorder_sections(self, order: list[str]) -> None:
        """Re-arrange the job sections (header + its rows) into `order` in
        place. takeItem/addItem keeps the QListWidgetItem objects, so
        queue badges and filter state survive; the selection is restored
        so the detail pane doesn't flicker while typing a search."""
        lst = self._craft_list
        prev_current = lst.currentItem()
        kept = [lst.takeItem(0) for _ in range(lst.count())]
        blocks: dict[str, list] = {}
        cur: str | None = None
        for li in kept:
            if li.data(detail.HEADER_ROLE):
                cur = li.data(detail.CAT_ROLE)
                blocks.setdefault(cur, [])
            if cur is not None:
                blocks[cur].append(li)
        lst.blockSignals(True)
        for jid in order:
            for li in blocks.get(jid, []):
                lst.addItem(li)
        if prev_current is not None:
            lst.setCurrentItem(prev_current)
        lst.blockSignals(False)

    def _craft_refilter(self, _=None) -> None:
        if not hasattr(self, "_craft_list"):
            return
        job = getattr(self, "_craft_job", "") or ""
        q = self._craft_search.text().strip().lower()
        searching = bool(q)
        order = getattr(self, "_craft_job_order", None)
        if order is None:
            order = list(_CRAFT_JOB_CHIPS) + [
                j for j in idata.craft_jobs() if j not in _CRAFT_JOB_CHIPS]
        if searching and job:
            # a search looks up ALL jobs (the chip is suspended, not
            # cleared — clearing the search restores it), and the selected
            # job's group leads the results
            self._craft_reorder_sections(
                [job] + [j for j in order if j != job])
        elif not searching and self._craft_header_order() != order:
            # back to the default group order once the search (and the
            # selected-group-first reorder) is over
            self._craft_reorder_sections(order)
        # the row meta keeps the job name only when results span jobs —
        # a chip filter already says it, so the browse rows read "LV N"
        self._craft_list._craft_meta_job = searching or not job
        vis_by_job: dict[str, int] = {}
        for i in range(self._craft_list.count()):
            li = self._craft_list.item(i)
            if li.data(detail.HEADER_ROLE):
                li.setHidden(not searching)   # group bars: search only
                continue
            r = li.data(detail.DATA_ROLE)
            if searching:
                hay = f"{r['name']} {r['item']} {r['job']}".lower()
                ok = q in hay
            else:
                ok = not job or r["job"] == job
            li.setHidden(not ok)
            if ok:
                vis_by_job[r["job"]] = vis_by_job.get(r["job"], 0) + 1
        if searching:
            for i in range(self._craft_list.count()):
                li = self._craft_list.item(i)
                if li.data(detail.HEADER_ROLE):
                    jid = li.data(detail.CAT_ROLE)
                    n = vis_by_job.get(jid, 0)
                    li.setText(
                        f"{li.data(detail.NAME_ROLE).upper()}  ·  {n}")
                    li.setHidden(n == 0)

    def _craft_header_order(self) -> list[str]:
        """The current left-to-right / top-to-bottom order of the section
        headers (job ids), in list-widget order."""
        out: list[str] = []
        for i in range(self._craft_list.count()):
            li = self._craft_list.item(i)
            if li.data(detail.HEADER_ROLE):
                out.append(li.data(detail.CAT_ROLE))
        return out
