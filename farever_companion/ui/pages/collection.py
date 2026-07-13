"""Collection tracker page.

The full collectible catalog (mounts / gliders / companions, from the bundled
collection_catalog.json) as a checkable list, synced to the signed-in Farever
Pal account — the same state the website's Collection page edits. Toggles are
optimistic and pushed in the background; a failed push is re-queued and
retried on the next change or refresh.
"""
from __future__ import annotations

import webbrowser

from PySide6 import QtCore, QtGui, QtWidgets

from .. import theme
from .. import components as C
from ..workers import CallWorker
from ...api import FareverAPI
from ...data import collections as coldata
from ...data import icons

_ID = QtCore.Qt.UserRole
_ROW = QtCore.Qt.UserRole + 1


class CollectionPageMixin:
    def _page_collection(self):
        page, v = self._page_container()
        v.addWidget(C.SectionHeader("Collection", tag="ACCOUNT SYNCED"))

        intro = QtWidgets.QLabel(
            "Every mount, glider and companion in the game — check off what "
            "you've collected. Saved to your account and shared with the "
            "website.")
        intro.setObjectName("Muted")
        intro.setWordWrap(True)
        v.addWidget(intro)

        # signed-out card (same shape as Friends)
        self._col_signedout = QtWidgets.QFrame()
        self._col_signedout.setObjectName("Card")
        so = QtWidgets.QVBoxLayout(self._col_signedout)
        so.setContentsMargins(16, 16, 16, 16)
        so.setSpacing(10)
        so_lbl = QtWidgets.QLabel("Sign in to your Farever Pal account to track "
                                  "your collection across the app and the website.")
        so_lbl.setObjectName("Muted")
        so_lbl.setWordWrap(True)
        so_btn = QtWidgets.QPushButton("Sign in")
        so_btn.setObjectName("Accent")
        so_btn.setMinimumHeight(32)
        so_btn.setCursor(QtCore.Qt.PointingHandCursor)
        so_btn.clicked.connect(self._open_login)
        so.addWidget(so_lbl)
        so.addWidget(so_btn, 0, QtCore.Qt.AlignLeft)
        v.addWidget(self._col_signedout)

        self._col_main = QtWidgets.QWidget()
        cm = QtWidgets.QVBoxLayout(self._col_main)
        cm.setContentsMargins(0, 0, 0, 0)
        cm.setSpacing(12)

        # progress summary
        self._col_prog = QtWidgets.QLabel("")
        self._col_prog.setObjectName("Mono")
        self._col_prog.setWordWrap(True)
        self._col_prog.setStyleSheet(
            f"color:{theme.TEXT};font-family:'{theme.MONO_FONT}','Consolas';"
            "font-size:11px;background:transparent;")
        cm.addWidget(self._col_prog)

        # filter row
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(8)
        self._col_cat = QtWidgets.QComboBox()
        self._col_cat.addItem("All categories", "")
        for c in coldata.categories():
            self._col_cat.addItem(c["label"], c["key"])
        self._col_sub = QtWidgets.QComboBox()
        self._col_sub.addItem("Any type", "")
        self._col_state = QtWidgets.QComboBox()
        for label, val in (("All", ""), ("Missing", "missing"), ("Collected", "collected")):
            self._col_state.addItem(label, val)
        self._col_search = QtWidgets.QLineEdit()
        self._col_search.setPlaceholderText("Search name, type or source…")
        self._col_search.setClearButtonEnabled(True)
        refresh = QtWidgets.QPushButton("Refresh")
        refresh.setObjectName("Outline")
        refresh.setCursor(QtCore.Qt.PointingHandCursor)
        refresh.clicked.connect(lambda: self._col_pull(force=True))
        web = QtWidgets.QPushButton("Open on web")
        web.setObjectName("Outline")
        web.setCursor(QtCore.Qt.PointingHandCursor)
        web.clicked.connect(lambda: webbrowser.open(f"{self.s.api_base}/collection.php"))
        self._col_share_btn = QtWidgets.QPushButton("Copy share link")
        self._col_share_btn.setObjectName("Outline")
        self._col_share_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self._col_share_btn.setToolTip(
            "Copy a public link to this view of your collection — current "
            "category, type and Missing/Collected filters included.")
        self._col_share_btn.clicked.connect(self._col_copy_share_link)
        for w in (self._col_cat, self._col_sub, self._col_state):
            row.addWidget(w)
        row.addWidget(self._col_search, 1)
        row.addWidget(refresh)
        row.addWidget(self._col_share_btn)
        row.addWidget(web)
        cm.addLayout(row)

        # sync status (only visible when a push failed and is queued)
        self._col_sync_lbl = QtWidgets.QLabel("")
        self._col_sync_lbl.setObjectName("Mono")
        self._col_sync_lbl.setStyleSheet(
            f"color:{theme.GOLD};background:transparent;font-size:11px;")
        self._col_sync_lbl.setVisible(False)
        cm.addWidget(self._col_sync_lbl)

        # the list — checkable icons in a grid, filtered via setHidden
        self._col_list = QtWidgets.QListWidget()
        self._col_list.setViewMode(QtWidgets.QListView.IconMode)
        self._col_list.setResizeMode(QtWidgets.QListView.Adjust)
        self._col_list.setMovement(QtWidgets.QListView.Static)
        self._col_list.setSpacing(12)
        self._col_list.setWordWrap(True)
        self._col_list.setUniformItemSizes(True)
        self._col_list.setIconSize(QtCore.QSize(48, 48))
        self._col_list.setMinimumHeight(420)
        self._col_list.setStyleSheet(
            f"QListWidget{{background:{theme.PANEL};border:1px solid {theme.BORDER};outline:none;}}"
            f"QListWidget::item{{padding:8px; border:1px solid transparent; border-radius:4px;}}"
            f"QListWidget::item:hover{{background:{theme.with_alpha(theme.ACCENT, 15)};}}"
            f"QListWidget::item:selected{{background:{theme.with_alpha(theme.ACCENT, 30)};"
            f"border:1px solid {theme.ACCENT};color:{theme.TEXT};}}")
        self._col_populate()
        self._col_list.itemChanged.connect(self._col_item_changed)
        cm.addWidget(self._col_list, 1)

        v.addWidget(self._col_main, 1)

        self._col_cat.currentIndexChanged.connect(self._col_cat_changed)
        for w in (self._col_sub, self._col_state):
            w.currentIndexChanged.connect(self._col_apply_filters)
        self._col_search.textChanged.connect(self._col_apply_filters)

        self._col_apply_filters()
        self._render_collection_summary()
        self._refresh_collection_gating()
        return page

    # --- list build / filter ---------------------------------------------
    def _col_populate(self):
        self._col_list.blockSignals(True)
        for it in coldata.items():
            sheet = coldata.icon_sheet(it["category"])
            li = QtWidgets.QListWidgetItem()
            lvl = f"  ·  Lv {it['level']}" if it.get("level") else ""
            li.setText(it['name'])
            li.setToolTip(f"{it['name']}\n{it['subtype']}{lvl}\n{it['source']}")
            li.setIcon(QtGui.QIcon(icons.tile(sheet, it["id"], 48,
                                              theme.rarity_color(it.get("rarity")))))
            li.setData(_ID, it["id"])
            li.setData(_ROW, it)
            if it.get("obtainable"):
                li.setFlags(li.flags() | QtCore.Qt.ItemIsUserCheckable)
                li.setCheckState(QtCore.Qt.Unchecked)
            else:
                li.setFlags(li.flags() & ~QtCore.Qt.ItemIsEnabled)
                li.setText(li.text() + "   [unreleased]")
            self._col_list.addItem(li)
        self._col_list.blockSignals(False)

    def _col_cat_changed(self):
        """Refill the subtype combo for the active category, then refilter."""
        cat = self._col_cat.currentData()
        subs = sorted({r["subtype"] for r in coldata.items(cat or None)})
        self._col_sub.blockSignals(True)
        self._col_sub.clear()
        self._col_sub.addItem("Any type", "")
        if cat:
            for s in subs:
                self._col_sub.addItem(s, s)
        self._col_sub.blockSignals(False)
        self._col_apply_filters()

    def _col_apply_filters(self):
        cat = self._col_cat.currentData()
        sub = self._col_sub.currentData() or ""
        state = self._col_state.currentData() or ""
        q = self._col_search.text().strip()
        for i in range(self._col_list.count()):
            li = self._col_list.item(i)
            row = li.data(_ROW)
            ok = (not cat or row["category"] == cat) and coldata.matches(
                row, query=q, subtype=sub, state=state, owned=self._col_owned)
            li.setHidden(not ok)

    def _col_copy_share_link(self):
        """Copy the web share URL for the current filter view (same params the
        website's Share button emits, so both produce identical links)."""
        code = (getattr(self.s, "account_code", "") or "").strip()
        if not code:
            self._col_sync_lbl.setText("sign in first — the share link needs your friend code")
            self._col_sync_lbl.setVisible(True)
            return
        from urllib.parse import urlencode
        params = {"u": code}
        if self._col_cat.currentData():
            params["tab"] = self._col_cat.currentData()
        if self._col_sub.currentData():
            params["type"] = self._col_sub.currentData()
        if self._col_state.currentData():
            params["state"] = self._col_state.currentData()
        q = self._col_search.text().strip()
        if q:
            params["q"] = q
        url = f"{self.s.api_base}/collection.php?{urlencode(params)}"
        QtWidgets.QApplication.clipboard().setText(url)
        self._col_share_btn.setText("Copied!")
        QtCore.QTimer.singleShot(1500, lambda: self._col_share_btn.setText("Copy share link"))

    # --- account sync ------------------------------------------------------
    def _refresh_collection_gating(self):
        if not hasattr(self, "_col_main"):
            return
        signed = bool(self.s.account_token)
        self._col_signedout.setVisible(not signed)
        self._col_main.setVisible(signed)
        if signed:
            self._col_pull(force=True)
        else:
            self._col_owned = set()
            self._col_pending_add = set()
            self._col_pending_remove = set()
            self._col_apply_owned()

    def _col_pull(self, force: bool = False):
        if not self.s.account_token:
            return
        if self._col_worker is not None and self._col_worker.isRunning():
            return
        base, token = self.s.api_base, self.s.account_token
        self._col_worker = CallWorker(
            lambda _w: FareverAPI(base, token).collection_list(), tag="pull")
        self._col_worker.done.connect(self._col_done)
        self._col_worker.start()

    def _col_flush(self):
        """Push queued toggles. One worker at a time; the done-handler flushes
        again if more queued up meanwhile."""
        if not self.s.account_token:
            return
        if self._col_worker is not None and self._col_worker.isRunning():
            return
        add = sorted(self._col_pending_add)
        rem = sorted(self._col_pending_remove)
        if not add and not rem:
            return
        self._col_pending_add = set()
        self._col_pending_remove = set()
        base, token = self.s.api_base, self.s.account_token
        self._col_worker = CallWorker(
            lambda _w: FareverAPI(base, token).collection_set(add, rem), tag="push")
        self._col_worker.done.connect(
            lambda t, res, a=add, r=rem: self._col_done(t, res, a, r))
        self._col_worker.start()

    def _col_done(self, tag: str, res: dict, add: list | None = None,
                  rem: list | None = None):
        self._col_worker = None
        if not isinstance(res, dict) or not res.get("ok"):
            if tag == "push":
                # re-queue, retry on the next toggle / refresh
                self._col_pending_add.update(add or [])
                self._col_pending_remove.update(rem or [])
                self._col_sync_lbl.setText("sync failed — queued, will retry")
                self._col_sync_lbl.setVisible(True)
            return
        self._col_sync_lbl.setVisible(False)
        if tag == "pull":
            owned = set(res.get("owned") or [])
            # don't lose in-flight local toggles to a concurrent pull
            owned |= self._col_pending_add
            owned -= self._col_pending_remove
            self._col_owned = owned
            self._col_apply_owned()
        self._render_collection_summary()
        self._col_flush()                     # anything queued while busy

    def _col_apply_owned(self):
        """Reflect self._col_owned into the check states (no signals)."""
        if not hasattr(self, "_col_list"):
            return
        self._col_list.blockSignals(True)
        for i in range(self._col_list.count()):
            li = self._col_list.item(i)
            if li.flags() & QtCore.Qt.ItemIsUserCheckable:
                li.setCheckState(QtCore.Qt.Checked if li.data(_ID) in self._col_owned
                                 else QtCore.Qt.Unchecked)
        self._col_list.blockSignals(False)
        self._col_apply_filters()
        self._col_push_owned_to_overlays()

    def _col_push_owned_to_overlays(self):
        """Keep the entity HUD's not-yet-collected marking in sync (None when
        signed out so nothing gets falsely flagged)."""
        mgr = getattr(self, "overlay_mgr", None)
        if mgr is not None:
            mgr.set_collection_owned(
                set(self._col_owned) if self.s.account_token else None)

    def _col_item_changed(self, li: QtWidgets.QListWidgetItem):
        item_id = li.data(_ID)
        if not item_id:
            return
        want = li.checkState() == QtCore.Qt.Checked
        if want:
            self._col_owned.add(item_id)
            self._col_pending_add.add(item_id)
            self._col_pending_remove.discard(item_id)
        else:
            self._col_owned.discard(item_id)
            self._col_pending_remove.add(item_id)
            self._col_pending_add.discard(item_id)
        self._render_collection_summary()
        if self._col_state.currentData():
            self._col_apply_filters()
        self._col_push_owned_to_overlays()
        self._col_flush()

    def _render_collection_summary(self):
        if not hasattr(self, "_col_prog"):
            return
        s = coldata.summary(self._col_owned)
        if not s:
            self._col_prog.setText("collection catalog missing")
            return
        coll, total = s.pop("_overall", (0, 0))
        pct = round(coll * 100 / total) if total else 0
        parts = [f"OVERALL {coll}/{total} ({pct}%)"]
        labels = {c["key"]: c["label"].upper() for c in coldata.categories()}
        parts += [f"{labels.get(k, k.upper())} {c}/{t}" for k, (c, t) in s.items()]
        self._col_prog.setText("   ·   ".join(parts))
