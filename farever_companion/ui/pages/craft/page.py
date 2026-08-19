"""Craft-page assembly with searchable recipe tile grid and queue tab."""
from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from ... import theme
from ... import components as C
from .... import planner
from ....data import icons
from ....data import items as idata
from ..items import support
from ..items.support import FlowLayout
from . import detail

_CRAFT_JOB_CHIPS = ["Blacksmith", "Outfitter", "Jeweller", "Alchemist", "Cook"]


class CraftPageBase:
    def _page_craft(self):
        page, v = self._page_container()

        # no CRAFT title or queue-count pill here — the two tabs ARE the
        # header, and the Craft List tab carries the queue count (gold)
        if not idata.available():
            v.addWidget(C.InfoCard("archive", "Data missing",
                                   "craft.json / job.json aren't bundled in this "
                                   "build — re-run the data compile to ship the "
                                   "crafting database."))
            return page

        all_recipes = idata.recipes()
        self._craft_job: str = ""
        self._craft_qty = 1
        self._craft_queue: list[dict] = planner.queue_entries()
        self._craft_got: dict[str, int] = planner.queue_got()

        self._craft_tabs = C.SegmentedControl(["Recipes", "Craft List"])
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
        self._craft_search = QtWidgets.QLineEdit()
        self._craft_search.setPlaceholderText("Search recipe, item or job…")
        self._craft_search.setClearButtonEnabled(True)
        self._craft_search.setFixedHeight(34)
        self._craft_search.textChanged.connect(self._craft_refilter)
        left.addWidget(self._craft_search)

        self._craft_chip_btns: dict[str, C.FilterChip] = {}
        job_flow = FlowLayout()
        job_flow.setContentsMargins(0, 0, 0, 0)
        job_flow.setSpacing(6)
        jobs_by_id = {j["id"]: j for j in idata.jobs()}
        for jid in _CRAFT_JOB_CHIPS:
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
            f"QListWidget::item{{padding:0px 8px 4px 8px;border-radius:4px;}}"
            f"QListWidget::item:hover{{background:{theme.with_alpha(theme.ACCENT,15)};}}"
            f"QListWidget::item:selected{{background:{theme.with_alpha(theme.ACCENT,30)};"
            f"border:1px solid {theme.ACCENT};color:{theme.TEXT};}}")
        self._craft_list.currentItemChanged.connect(self._craft_show)
        self._craft_list.itemClicked.connect(self._craft_item_clicked)
        self._craft_list.setMinimumWidth(380)
        left.addWidget(self._craft_list, 1)
        cols.addLayout(left, 2)

        right = QtWidgets.QVBoxLayout()
        right.setSpacing(12)
        self._craft_detail = QtWidgets.QFrame()
        self._craft_detail.setObjectName("Card")
        self._craft_detail_lay = QtWidgets.QVBoxLayout(self._craft_detail)
        self._craft_detail_lay.setContentsMargins(18, 16, 18, 16)
        self._craft_scroll = QtWidgets.QScrollArea()
        self._craft_scroll.setWidgetResizable(True)
        self._craft_scroll.setWidget(self._craft_detail)
        self._craft_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        self._craft_scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self._craft_scroll.setStyleSheet("QScrollArea{background:transparent;border:0;}")
        right.addWidget(self._craft_scroll, 1)
        cols.addLayout(right, 3)
        self._craft_stack.addWidget(browse)

        queue_page = QtWidgets.QWidget()
        qv = QtWidgets.QVBoxLayout(queue_page)
        qv.setContentsMargins(0, 0, 0, 0)
        qv.setSpacing(12)
        self._craft_rail = QtWidgets.QFrame()
        self._craft_rail.setStyleSheet(
            f"QFrame{{background:{theme.PANEL};border:1px solid {theme.with_alpha(theme.GOLD, 70)};}}")
        _rail_v = QtWidgets.QVBoxLayout(self._craft_rail)
        _rail_v.setContentsMargins(0, 0, 0, 0)
        _rail_sc = QtWidgets.QScrollArea()
        _rail_sc.setWidgetResizable(True)
        _rail_sc.setFrameShape(QtWidgets.QFrame.NoFrame)
        _rail_sc.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        _rail_sc.setStyleSheet("QScrollArea{background:transparent;border:0;}")
        self._craft_rail_body = QtWidgets.QWidget()
        self._craft_rail_lay = QtWidgets.QVBoxLayout(self._craft_rail_body)
        self._craft_rail_lay.setContentsMargins(18, 16, 18, 16)
        self._craft_rail_lay.setSpacing(8)
        _rail_sc.setWidget(self._craft_rail_body)
        _rail_v.addWidget(_rail_sc)

        self._craft_rail_empty = C.InfoCard(
            "archive", "Craft List is empty",
            "Add a recipe from the Recipes tab (ADD TO QUEUE) and its batch lands here.")
        qv.addWidget(self._craft_rail_empty, 1)
        qv.addWidget(self._craft_rail, 1)
        self._craft_stack.addWidget(queue_page)

        self._craft_all = all_recipes
        self._craft_build_list()
        # Blacksmith is the default job on open (the user's first chip) —
        # the list lands on its recipes instead of the bare section bars
        self._craft_job = "Blacksmith"
        self._craft_chip_btns["Blacksmith"].setChecked(True)
        self._craft_refilter()
        self._craft_list.setCurrentItem(None)
        self._craft_list.clearSelection()
        self._craft_list.setCurrentIndex(QtCore.QModelIndex())
        self._craft_show(None)
        self._craft_rail_built = False
        self._craft_rail_sync()
        return page

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
        if record_history and hasattr(self, "_nav_history") and not getattr(self._nav_history, "_restoring", False):
            old_tab = "Craft List" if text == "Recipes" else "Recipes"
            self._nav_history.record(f"craft:{old_tab}", f"craft:{text}")
        if text == "Craft List":
            self._craft_ensure_rail()
        if hasattr(self, "_craft_stack"):
            self._craft_stack.setCurrentIndex(0 if text == "Recipes" else 1)

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

                # Smart icon resolution (Centralized in icons.item_tile)
                li.setIcon(QtGui.QIcon(icons.item_tile(r["item"], r["name"], detail.LIST_ICON, col)))
                li.setSizeHint(QtCore.QSize(0, row_h))
                li.setTextAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
                li.setData(detail.ID_ROLE, r["item"])
                li.setData(detail.DATA_ROLE, r)
                li.setData(detail.QUEUED_ROLE, r["item"] in self._craft_queued_ids())
                self._craft_list.addItem(li)
        self._craft_list.blockSignals(False)

    def _craft_fill_icons(self, batch: int = 160) -> None:
        pass

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
