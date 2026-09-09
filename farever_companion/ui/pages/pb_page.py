"""PB History page — the shared cross-character speedrun kill log.

Reached only via the DPS Analysis header link (never the sidebar): rows come
from moddata/speedrun_history.json through load_speedrun_history(). PBs
(speedrun_best / speedrun_boss_best) stay per-character; this page shows every
confirmed kill across characters, filterable by char and dungeon.
"""
from __future__ import annotations

from datetime import datetime

from PySide6 import QtCore, QtWidgets

from .. import components as C
from .. import theme
from ..chips import ChoiceChips
from ...config import load_speedrun_history
from ...core.speedrun import fmt_time
from ...data import names

_COLS = ("When", "Character", "Dungeon", "Boss", "Mode", "Full", "Boss split")


def _fmt_when(at) -> str:
    try:
        return datetime.fromtimestamp(float(at)).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError, OSError, OverflowError):
        return "—"


def _fmt_ms(ms) -> str:
    try:
        if ms is None:
            return "—"
        return fmt_time(float(ms) / 1000.0)
    except (TypeError, ValueError):
        return "—"


def _boss_label(row: dict) -> str:
    bid = row.get("boss") or ""
    return names.unit_name(bid) or bid or "—"


class PBPageMixin:
    """Shared speedrun history: summary cards, char/dungeon filters, table."""

    def _page_pb(self) -> QtWidgets.QWidget:
        page, lay = self._page_container()

        hdr = QtWidgets.QHBoxLayout()
        hdr.setSpacing(10)
        back = QtWidgets.QPushButton("← Back")
        back.setStyleSheet("padding: 6px 10px; font-weight: 600;")
        back.setToolTip("Back to DPS Analysis")
        back.clicked.connect(lambda: self._select_nav("combat"))
        hdr.addWidget(back)
        title = QtWidgets.QLabel("PB History")
        title.setStyleSheet(
            f"font-size: 15px; font-weight: 800; color: {theme.TEXT};")
        hdr.addWidget(title)
        hdr.addStretch(1)
        lay.addLayout(hdr)

        def _pb_back():
            self._select_nav("combat")
            return True
        page.consume_pane_back = _pb_back

        cards = QtWidgets.QHBoxLayout()
        cards.setSpacing(10)
        self._pb_card_runs = C.InfoCard("archive", "Runs", "0")
        self._pb_card_chars = C.InfoCard("users", "Characters", "0")
        self._pb_card_full = C.InfoCard("timer", "Best Full", "—")
        self._pb_card_boss = C.InfoCard("trophy", "Best Boss", "—")
        for card in (self._pb_card_runs, self._pb_card_chars,
                     self._pb_card_full, self._pb_card_boss):
            cards.addWidget(card, 1)
        lay.addLayout(cards)

        chips_row = QtWidgets.QHBoxLayout()
        chips_row.setSpacing(16)
        self._pb_char_host = QtWidgets.QWidget()
        char_lay = QtWidgets.QHBoxLayout(self._pb_char_host)
        char_lay.setContentsMargins(0, 0, 0, 0)
        self._pb_dungeon_host = QtWidgets.QWidget()
        dungeon_lay = QtWidgets.QHBoxLayout(self._pb_dungeon_host)
        dungeon_lay.setContentsMargins(0, 0, 0, 0)
        chips_row.addWidget(self._pb_char_host, 1)
        chips_row.addWidget(self._pb_dungeon_host, 1)
        lay.addLayout(chips_row)
        self._pb_char_chips: ChoiceChips | None = None
        self._pb_dungeon_chips: ChoiceChips | None = None
        self._pb_char_opts: list | None = None
        self._pb_dungeon_opts: list | None = None
        self._pb_all_rows: list = []

        self._pb_table = QtWidgets.QTableWidget(0, len(_COLS))
        self._pb_table.setHorizontalHeaderLabels(list(_COLS))
        hdr_view = self._pb_table.horizontalHeader()
        hdr_view.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        for i in range(1, len(_COLS) - 1):
            hdr_view.setSectionResizeMode(i, QtWidgets.QHeaderView.Interactive)
        hdr_view.setSectionResizeMode(len(_COLS) - 1,
                                      QtWidgets.QHeaderView.ResizeToContents)
        self._pb_table.setColumnWidth(1, 160)
        self._pb_table.setColumnWidth(2, 160)
        self._pb_table.setColumnWidth(3, 180)
        self._pb_table.setColumnWidth(4, 80)
        self._pb_table.setColumnWidth(5, 100)
        self._pb_table.verticalHeader().setVisible(False)
        self._pb_table.setEditTriggers(
            QtWidgets.QAbstractItemView.NoEditTriggers)
        self._pb_table.setSelectionBehavior(
            QtWidgets.QAbstractItemView.SelectRows)
        self._pb_table.setSelectionMode(
            QtWidgets.QAbstractItemView.SingleSelection)
        lay.addWidget(self._pb_table, 1)

        self._pb_empty = C.InfoCard(
            "archive", "No runs yet",
            "Confirmed dungeon kills land here automatically — one shared "
            "list for every character.")
        lay.addWidget(self._pb_empty)

        old_show = page.showEvent

        def _on_show(ev):
            old_show(ev)
            self._pb_refresh()
        page.showEvent = _on_show

        self._pb_page = page
        self._pb_refresh()
        return page

    # --- data ------------------------------------------------------------
    def _pb_refresh(self) -> None:
        if not self._widget_alive(getattr(self, "_pb_table", None)):
            return
        try:
            rows = load_speedrun_history()
        except Exception:
            rows = []
        self._pb_all_rows = list(rows)

        chars = sorted({r.get("char") or "" for r in rows if r.get("char")})
        dungeons = sorted({r.get("dungeon") or ""
                           for r in rows if r.get("dungeon")})
        self._pb_sync_chips(
            "char", [(c, c) for c in chars], "All characters", "Character")
        self._pb_sync_chips(
            "dungeon", [(d, d) for d in dungeons], "All dungeons", "Dungeon")
        self._pb_apply_filter()

    def _pb_update_cards(self, rows: list) -> None:
        """Summary cards reflect the ACTIVE char/dungeon filter so the table
        and the headline numbers always agree."""
        runs = len(rows)
        char_n = len({r.get("char") or "" for r in rows if r.get("char")})
        fulls = [r.get("full_ms") for r in rows
                 if isinstance(r.get("full_ms"), (int, float))]
        bosses = [r.get("boss_ms") for r in rows
                  if isinstance(r.get("boss_ms"), (int, float))]
        if self._widget_alive(self._pb_card_runs):
            self._pb_card_runs.set_value(str(runs))
        if self._widget_alive(self._pb_card_chars):
            self._pb_card_chars.set_value(str(char_n))
        if self._widget_alive(self._pb_card_full):
            self._pb_card_full.set_value(
                _fmt_ms(min(fulls)) if fulls else "—")
        if self._widget_alive(self._pb_card_boss):
            self._pb_card_boss.set_value(
                _fmt_ms(min(bosses)) if bosses else "—")

    def _pb_sync_chips(self, which: str, options: list,
                       any_label: str, label: str) -> None:
        """Rebuild the char/dungeon chips only when their option list changed
        (ChoiceChips has no set_options)."""
        opts_attr = f"_pb_{which}_opts"
        if getattr(self, opts_attr, None) == options:
            return
        setattr(self, opts_attr, list(options))
        host = getattr(self, f"_pb_{which}_host", None)
        if not self._widget_alive(host):
            return
        lay = host.layout()
        while lay.count():
            item = lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        chips = ChoiceChips(options, any_label=any_label, label=label)
        chips.currentIndexChanged.connect(lambda: self._pb_apply_filter())
        lay.addWidget(chips)
        setattr(self, f"_pb_{which}_chips", chips)

    def _pb_selected(self, which: str) -> str:
        chips = getattr(self, f"_pb_{which}_chips", None)
        if not self._widget_alive(chips):
            return ""
        try:
            return chips.currentData() or ""
        except Exception:
            return ""

    def _pb_apply_filter(self) -> None:
        if not self._widget_alive(getattr(self, "_pb_table", None)):
            return
        want_char = self._pb_selected("char")
        want_dungeon = self._pb_selected("dungeon")

        out: list[dict] = []
        for r in self._pb_all_rows:
            if not isinstance(r, dict):
                continue
            if want_char and (r.get("char") or "") != want_char:
                continue
            if want_dungeon and (r.get("dungeon") or "") != want_dungeon:
                continue
            out.append(r)
        out.sort(key=lambda r: float(r.get("at") or 0.0), reverse=True)
        self._pb_fill_table(out)
        self._pb_update_cards(out)

    def _pb_fill_table(self, rows: list) -> None:
        table = self._pb_table
        table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            vals = (
                _fmt_when(r.get("at")),
                r.get("char") or "—",
                r.get("dungeon") or "—",
                _boss_label(r),
                (r.get("mode") or "hard").capitalize(),
                _fmt_ms(r.get("full_ms")),
                _fmt_ms(r.get("boss_ms")),
            )
            for j, text in enumerate(vals):
                item = QtWidgets.QTableWidgetItem(text)
                if j in (0, 5, 6):
                    item.setTextAlignment(
                        int(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter))
                table.setItem(i, j, item)
        table.setVisible(bool(rows))
        if self._widget_alive(getattr(self, "_pb_empty", None)):
            self._pb_empty.setVisible(not rows)
