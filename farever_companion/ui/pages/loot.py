from __future__ import annotations

from PySide6 import QtGui, QtWidgets
from .. import theme
from .. import components as C
from ..widgets import fmt_pct
from ...data import loot, names, tokens, rarity

class LootPageMixin:
    def _page_loot(self):
        page, v = self._page_container()
        head = QtWidgets.QHBoxLayout()
        h1 = QtWidgets.QLabel("DROP SIMULATION")
        h1.setObjectName("H1")
        tag = QtWidgets.QLabel("OFFLINE PREDICTOR")
        tag.setObjectName("Mono")
        tag.setStyleSheet(
            f"color:{theme.ACCENT};background:{theme.with_alpha(theme.ACCENT,30)};"
            f"border:1px solid {theme.with_alpha(theme.ACCENT,90)};border-radius:4px;"
            "padding:3px 8px;font-weight:700;")
        head.addWidget(h1)
        head.addStretch(1)
        head.addWidget(tag)
        v.addLayout(head)

        controls = QtWidgets.QHBoxLayout()
        controls.setSpacing(12)
        self.table_combo = QtWidgets.QComboBox()
        try:
            ids = loot.table_ids()
        except Exception as e:
            ids = []
            self.log(f"loot data error: {e}")
        for tid in ids:
            self.table_combo.addItem(names.loot_table_label(tid), tid)
        default_ix = self.table_combo.findData("WorldCrate")
        if default_ix >= 0:
            self.table_combo.setCurrentIndex(default_ix)
        controls.addWidget(C.Field("Loot source", self.table_combo), 1)
        self.level_stepper = C.Stepper(self.s.level, 1, 25)
        self.level_stepper.valueChanged.connect(self._on_level_changed)
        controls.addWidget(C.Field("Level", self.level_stepper))
        btn = QtWidgets.QPushButton("Predict")
        btn.setObjectName("Accent")
        btn.clicked.connect(self.predict)
        controls.addWidget(C.Field(" ", btn))
        v.addLayout(controls)

        v.addWidget(C.SectionHeader("Drop Table Results"))
        self.loot_caption = QtWidgets.QLabel("")
        self.loot_caption.setObjectName("Mono")
        v.addWidget(self.loot_caption)
        self.table = QtWidgets.QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["PROB.", "ITEM", "RARITY", "TYPE"])
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QtWidgets.QHeaderView.Interactive)
        self.table.setColumnWidth(0, 65)
        hh.setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
        hh.setSectionResizeMode(2, QtWidgets.QHeaderView.Interactive)
        self.table.setColumnWidth(2, 95)
        hh.setSectionResizeMode(3, QtWidgets.QHeaderView.Interactive)
        self.table.setColumnWidth(3, 160)
        self.table.setMinimumHeight(220)
        v.addWidget(self.table, 1)

        footer = QtWidgets.QHBoxLayout()
        footer.setSpacing(12)
        footer.addWidget(C.InfoCard("archive", "Data",
                                    f"CDB sheets (offline, {len(ids)} tables)"), 1)
        footer.addWidget(C.InfoCard("dice-5", "Predictor",
                                    "Deterministic recursive expansion", theme.ORANGE), 1)
        v.addLayout(footer)
        return page

    def _on_level_changed(self, v):
        self.s.level = v
        self.s.save()

    def predict(self):
        self.table.setRowCount(0)
        tid = self.table_combo.currentData() or self.table_combo.currentText().strip()
        if not tid:
            return
        level = self.level_stepper.value()
        rows = loot.predict_sorted(tid, level)
        self.table.setRowCount(len(rows))
        for r, (item, prob, rar, typ) in enumerate(rows):
            prob_item = QtWidgets.QTableWidgetItem(fmt_pct(prob))
            prob_item.setForeground(QtGui.QColor(theme.ACCENT))
            f = prob_item.font()
            f.setFamily(theme.MONO_FONT)
            if f.pointSize() <= 0:
                f.setPointSize(9)
            prob_item.setFont(f)
            self.table.setItem(r, 0, prob_item)
            self.table.setCellWidget(r, 1, self._loot_item_cell(item, rar))
            self.table.setCellWidget(r, 2, self._center(C.RarityTag(rar)))
            typ_item = QtWidgets.QTableWidgetItem(names.humanize(typ) + "   ")
            typ_item.setForeground(QtGui.QColor(theme.MUTED))
            self.table.setItem(r, 3, typ_item)
        self.table.resizeRowsToContents()
        self.loot_caption.setText(f"TABLE: {tid}")
        self.log(f"Predicted '{tid}' @ L{level}: {len(rows)} drops.")

    def _loot_item_cell(self, item: str, rarity: str):
        w = QtWidgets.QWidget()
        w.setMinimumHeight(40)
        lay = QtWidgets.QHBoxLayout(w)
        lay.setContentsMargins(4, 5, 4, 5)
        lay.setSpacing(10)
        tile = C.IconTile(30)
        tile.set("item", item, theme.rarity_color(rarity))
        name = QtWidgets.QLabel()
        if tokens.is_token(item):
            name.setText(f"🎲 random — {names.item_name(item)}")
            name.setStyleSheet(f"color:{theme.MUTED};font-style:italic;background:transparent;")
        else:
            name.setText(names.item_name(item) or item)
            name.setStyleSheet(
                f"color:{theme.rarity_color(rarity)};background:transparent;")
        lay.addWidget(tile)
        lay.addWidget(name, 1)
        return w
