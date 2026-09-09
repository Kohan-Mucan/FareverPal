"""Combat Compare Matrix Mixin.

The "Compare" side of the Combat & DPS page: the A/B/C/D/E selector bar, the
performance-gap summary and the aligned skill comparison matrix.

Split out of `combat_rail.py` (which keeps the skills rail and the per-skill
inspector) so each file stays a single concern. Composed onto the same page
mixin as `CombatRailMixin`, so `self` here is the combat page: it reads
`_cp_metric` / `_cp_solo_filter_on()` and calls `_get_displayed_session()` and
`_set_combat_metric()` from there.
"""
from __future__ import annotations

from PySide6 import QtCore, QtWidgets

from .. import theme
from ..copy_utils import copy_text
from ...core.dps_tracker import CombatSession, own_row, view_totals
from ..combat import (COMPARE_COLORS, _CmpTypeTable, _ranked_skill_total,
                      refresh_rail_compare)


class CombatCompareMixin:
    """Selection bar + performance-gap + aligned skill matrix."""

    def _build_compare_container(self) -> QtWidgets.QScrollArea:
        def _stat_cell(caption: str, color: str = theme.TEXT):
            w = QtWidgets.QFrame()
            w.setObjectName("StatCell")
            w.setStyleSheet(f"QFrame#StatCell {{ background: {theme.PANEL_LOW}; border: 1px solid {theme.BORDER}; border-radius: 4px; }}")
            l = QtWidgets.QVBoxLayout(w)
            l.setContentsMargins(8, 6, 8, 6)
            l.setSpacing(2)
            c = QtWidgets.QLabel(caption.upper())
            c.setAlignment(QtCore.Qt.AlignCenter)
            c.setStyleSheet(f"font-size: 9.5px; font-weight: 800; color: {theme.DIM}; letter-spacing: 0.5px;")
            v = QtWidgets.QLabel("--")
            v.setAlignment(QtCore.Qt.AlignCenter)
            v.setStyleSheet(f"font-size: 13.5px; font-weight: 800; color: {color}; font-family: monospace;")
            l.addWidget(c)
            l.addWidget(v)
            return w, v, c

        self.cp_full_compare_container = QtWidgets.QScrollArea()
        self.cp_full_compare_container.setWidgetResizable(True)
        self.cp_full_compare_container.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.cp_full_compare_container.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.cp_full_compare_container.setStyleSheet("background: transparent;")

        cmp_widget = QtWidgets.QWidget()
        cmp_lay = QtWidgets.QVBoxLayout(cmp_widget)
        cmp_lay.setContentsMargins(0, 0, 0, 0)
        cmp_lay.setSpacing(8)

        cmp_bar = QtWidgets.QFrame()
        # `.QFrame` (exact class): a bare `QFrame` rule cascades to every
        # QLabel inside (QLabel IS-A QFrame) — each A:/VS/B: label drew its
        # own 1px box on this bar
        cmp_bar.setStyleSheet(
            f".QFrame {{ background: {theme.PANEL}; border: 1px solid {theme.BORDER}; border-radius: 6px; }}"
        )
        cmp_bar_lay = QtWidgets.QHBoxLayout(cmp_bar)
        cmp_bar_lay.setContentsMargins(10, 5, 10, 5)
        cmp_bar_lay.setSpacing(8)

        def _v_sep():
            f = QtWidgets.QFrame()
            f.setFrameShape(QtWidgets.QFrame.VLine)
            f.setStyleSheet(f"color: {theme.BORDER}; border: none; border-left: 1px solid {theme.BORDER};")
            f.setFixedHeight(16)
            return f

        self.cp_cmp_same_class_cb = QtWidgets.QCheckBox("Same Class")
        self.cp_cmp_same_class_cb.setChecked(True)
        self.cp_cmp_same_class_cb.setToolTip("Auto-filter comparison candidates to your same class")
        self.cp_cmp_same_class_cb.setStyleSheet(f"font-size: 11px; font-weight: 700; color: {theme.TEXT};")
        self.cp_cmp_same_class_cb.toggled.connect(lambda _: self._on_compare_selection_changed())
        cmp_bar_lay.addWidget(self.cp_cmp_same_class_cb)

        cmp_bar_lay.addWidget(_v_sep())

        lbl_a = QtWidgets.QLabel("A:")
        lbl_a.setStyleSheet(f"font-size: 11.5px; font-weight: 900; color: {COMPARE_COLORS[0].name()};")
        cmp_bar_lay.addWidget(lbl_a)

        self.cp_compare_combo_a = QtWidgets.QComboBox()
        self.cp_compare_combo_a.setStyleSheet("QComboBox { padding: 3px 8px; font-size: 11px; font-weight: 700; min-width: 96px; }")
        self.cp_compare_combo_a.currentIndexChanged.connect(self._on_compare_selection_changed)
        cmp_bar_lay.addWidget(self.cp_compare_combo_a)

        vs_lbl = QtWidgets.QLabel("VS")
        vs_lbl.setStyleSheet(f"font-size: 11.5px; font-weight: 900; color: {theme.GOLD};")
        cmp_bar_lay.addWidget(vs_lbl)

        lbl_b = QtWidgets.QLabel("B:")
        lbl_b.setStyleSheet(f"font-size: 11.5px; font-weight: 900; color: {COMPARE_COLORS[1].name()};")
        cmp_bar_lay.addWidget(lbl_b)

        self.cp_compare_combo_b = QtWidgets.QComboBox()
        self.cp_compare_combo_b.setStyleSheet("QComboBox { padding: 3px 8px; font-size: 11px; font-weight: 700; min-width: 96px; }")
        self.cp_compare_combo_b.currentIndexChanged.connect(self._on_compare_selection_changed)
        cmp_bar_lay.addWidget(self.cp_compare_combo_b)

        cmp_bar_lay.addWidget(_v_sep())

        lbl_more = QtWidgets.QLabel("+ Extra:")
        lbl_more.setStyleSheet(f"font-size: 11px; font-weight: 800; color: {theme.DIM};")
        cmp_bar_lay.addWidget(lbl_more)

        lbl_c = QtWidgets.QLabel("C:")
        lbl_c.setStyleSheet(f"font-size: 11.5px; font-weight: 900; color: {COMPARE_COLORS[2].name()};")
        cmp_bar_lay.addWidget(lbl_c)
        self.cp_compare_combo_c = QtWidgets.QComboBox()
        self.cp_compare_combo_c.setStyleSheet("QComboBox { padding: 2px 6px; font-size: 10.5px; font-weight: 700; min-width: 64px; }")
        self.cp_compare_combo_c.currentIndexChanged.connect(self._on_compare_selection_changed)
        cmp_bar_lay.addWidget(self.cp_compare_combo_c)

        lbl_d = QtWidgets.QLabel("D:")
        lbl_d.setStyleSheet(f"font-size: 11.5px; font-weight: 900; color: {COMPARE_COLORS[3].name()};")
        cmp_bar_lay.addWidget(lbl_d)
        self.cp_compare_combo_d = QtWidgets.QComboBox()
        self.cp_compare_combo_d.setStyleSheet("QComboBox { padding: 2px 6px; font-size: 10.5px; font-weight: 700; min-width: 64px; }")
        self.cp_compare_combo_d.currentIndexChanged.connect(self._on_compare_selection_changed)
        cmp_bar_lay.addWidget(self.cp_compare_combo_d)

        lbl_e = QtWidgets.QLabel("E:")
        lbl_e.setStyleSheet(f"font-size: 11.5px; font-weight: 900; color: {COMPARE_COLORS[4].name()};")
        cmp_bar_lay.addWidget(lbl_e)
        self.cp_compare_combo_e = QtWidgets.QComboBox()
        self.cp_compare_combo_e.setStyleSheet("QComboBox { padding: 2px 6px; font-size: 10.5px; font-weight: 700; min-width: 64px; }")
        self.cp_compare_combo_e.currentIndexChanged.connect(self._on_compare_selection_changed)
        cmp_bar_lay.addWidget(self.cp_compare_combo_e)

        cmp_bar_lay.addStretch(1)
        cmp_lay.addWidget(cmp_bar)

        self.cp_cmp_summary_box = QtWidgets.QFrame()
        self.cp_cmp_summary_box.setObjectName("PerfGapBox")
        self.cp_cmp_summary_box.setStyleSheet(
            f"QFrame#PerfGapBox {{ background: {theme.PANEL}; border: 1px solid {theme.BORDER}; border-radius: 6px; }}"
        )
        cs_lay = QtWidgets.QVBoxLayout(self.cp_cmp_summary_box)
        cs_lay.setContentsMargins(12, 10, 12, 10)
        cs_lay.setSpacing(8)

        cs_top = QtWidgets.QHBoxLayout()
        cs_top.setSpacing(8)
        cs_title = QtWidgets.QLabel("PERFORMANCE GAP")
        cs_title.setStyleSheet(f"font-size: 12px; font-weight: 900; color: {theme.TEXT}; letter-spacing: 0.8px;")
        cs_top.addWidget(cs_title)
        self.cp_cmp_insight = QtWidgets.QLabel("")
        self.cp_cmp_insight.setWordWrap(True)
        self.cp_cmp_insight.setStyleSheet(f"font-size: 12.5px; font-weight: 700; color: {theme.TEXT};")
        cs_top.addWidget(self.cp_cmp_insight, 1)
        self.cp_cmp_class_match = QtWidgets.QLabel("")
        self.cp_cmp_class_match.setStyleSheet(
            f"font-size: 10.5px; font-weight: 800; color: {theme.GOLD}; "
            f"border: 1px solid {theme.BORDER}; border-radius: 8px; padding: 2px 8px;"
        )
        cs_top.addWidget(self.cp_cmp_class_match)
        cs_lay.addLayout(cs_top)

        gap_grid = QtWidgets.QGridLayout()
        gap_grid.setSpacing(6)
        for _c in range(6):
            gap_grid.setColumnStretch(_c, 1)

        w_lead, self.cp_cmp_winner, _ = _stat_cell("Leader", theme.TEXT)
        w_dps, self.cp_cmp_dps_delta, _ = _stat_cell("DPS Lead", theme.GOOD)
        w_pct, self.cp_cmp_pct_delta, _ = _stat_cell("% Edge", theme.GOOD)
        w_cg, self.cp_cmp_crit_gap, _ = _stat_cell("Crit Gap", theme.ORANGE)
        w_hr, self.cp_cmp_hit_rate, _ = _stat_cell("Hits/sec", theme.TEXT)
        w_tg, self.cp_cmp_top_gap, _ = _stat_cell("Top Gap", theme.ACCENT_LIGHT)

        self.cp_cmp_winner.setText("—")
        self.cp_cmp_dps_delta.setText("+0/s")
        self.cp_cmp_dps_delta.setStyleSheet(
            f"font-size: 18px; font-weight: 900; font-family: monospace; color: {theme.GOOD};")
        self.cp_cmp_pct_delta.setText("+0.0%")
        self.cp_cmp_pct_delta.setStyleSheet(
            f"font-size: 14px; font-weight: 800; font-family: monospace; color: {theme.GOOD};")

        gap_grid.addWidget(w_lead, 0, 0)
        gap_grid.addWidget(w_dps, 0, 1)
        gap_grid.addWidget(w_pct, 0, 2)
        gap_grid.addWidget(w_cg, 0, 3)
        gap_grid.addWidget(w_hr, 0, 4)
        gap_grid.addWidget(w_tg, 0, 5)
        cs_lay.addLayout(gap_grid)

        cmp_lay.addWidget(self.cp_cmp_summary_box)

        # Damage by type for the same two players, between the gap summary and
        # the skill matrix. Starts hidden and is shown only by a refresh that
        # finds classified damage, so a capture with no types (an older bridge,
        # or a build that renamed the field) grows no empty section.
        self.cp_cmp_type_box = QtWidgets.QFrame()
        self.cp_cmp_type_box.setObjectName("CompareTypeBox")
        self.cp_cmp_type_box.setStyleSheet(
            f"QFrame#CompareTypeBox {{ background: {theme.PANEL}; "
            f"border: 1px solid {theme.BORDER}; border-radius: 6px; }}"
        )
        cty_lay = QtWidgets.QVBoxLayout(self.cp_cmp_type_box)
        cty_lay.setContentsMargins(10, 8, 10, 8)
        cty_lay.setSpacing(6)
        cty_hdr = QtWidgets.QLabel("DAMAGE BY TYPE — A vs B")
        cty_hdr.setStyleSheet(
            f"font-size: 11.5px; font-weight: 900; color: {theme.TEXT}; "
            f"letter-spacing: 0.8px;")
        cty_lay.addWidget(cty_hdr)
        self.cp_cmp_type_table = _CmpTypeTable()
        cty_lay.addWidget(self.cp_cmp_type_table)
        cmp_lay.addWidget(self.cp_cmp_type_box)
        self.cp_cmp_type_box.hide()

        self.cp_cmp_table_box = QtWidgets.QFrame()
        self.cp_cmp_table_box.setObjectName("CompareTableBox")
        self.cp_cmp_table_box.setStyleSheet(
            f"QFrame#CompareTableBox {{ background: {theme.PANEL}; border: 1px solid {theme.BORDER}; border-radius: 6px; }}"
        )
        ct_lay = QtWidgets.QVBoxLayout(self.cp_cmp_table_box)
        ct_lay.setContentsMargins(10, 8, 10, 8)
        ct_lay.setSpacing(6)

        ct_hdr = QtWidgets.QLabel("ALIGNED SKILL PERFORMANCE COMPARISON MATRIX")
        ct_hdr.setStyleSheet(f"font-size: 11.5px; font-weight: 900; color: {theme.TEXT}; letter-spacing: 0.8px;")
        ct_lay.addWidget(ct_hdr)

        self.cp_cmp_rows_container = QtWidgets.QWidget()
        self.cp_cmp_rows_lay = QtWidgets.QGridLayout(self.cp_cmp_rows_container)
        self.cp_cmp_rows_lay.setContentsMargins(0, 0, 0, 0)
        self.cp_cmp_rows_lay.setHorizontalSpacing(6)
        self.cp_cmp_rows_lay.setVerticalSpacing(2)
        self.cp_cmp_rows_lay.setColumnStretch(1, 1)
        self.cp_cmp_rows_lay.setColumnStretch(2, 1)
        self.cp_cmp_rows_lay.setColumnStretch(7, 1)
        ct_lay.addWidget(self.cp_cmp_rows_container, 1)
        cmp_lay.addWidget(self.cp_cmp_table_box, 1)

        self.cp_full_compare_container.setWidget(cmp_widget)
        self.cp_full_compare_container.hide()
        return self.cp_full_compare_container

    # --- selection ------------------------------------------------------------

    def _on_player_compare_clicked(self, name: str) -> None:
        session = self._get_displayed_session()
        if not session:
            return

        ranked = session.ranked_players(view="damage")
        if not ranked:
            return

        me = own_row(session)
        top1 = ranked[0]
        top2 = ranked[1] if len(ranked) > 1 else top1
        p_sel = session.players.get(name)

        if p_sel and p_sel.is_me:
            self._cp_compare_a = p_sel.name
            if top1.name != p_sel.name:
                self._cp_compare_b = top1.name
            else:
                self._cp_compare_b = top2.name
        else:
            if me and me.name != name:
                self._cp_compare_a = me.name
                self._cp_compare_b = name
            else:
                self._cp_compare_a = top1.name if top1.name != name else top2.name
                self._cp_compare_b = name

        self._set_combat_metric("compare")

    def _on_hero_compare_clicked(self) -> None:
        sel = getattr(self, "_cp_selected_player", "")
        if sel:
            self._on_player_compare_clicked(sel)

    def _copy_selected_player_parse(self) -> None:
        session = self._get_displayed_session()
        if not session:
            return
        p_name = getattr(self, "_cp_selected_player", "")
        p = session.players.get(p_name)
        if not p:
            return
        duration = max(1.0, session.duration)
        top = _ranked_skill_total(p)
        top_name = top[0].name if top else "None"
        share = p.share_pct(view_totals(session, self._cp_solo_filter_on())[0])
        text = f"{p.name} ({p.hero_class or 'Hero'}): {p.dps(duration):,.1f} DPS | {p.total_damage:,.0f} Total ({share:.1f}%) | Top: {top_name}"
        copy_text(text)

    def _get_cb_val(self, cb) -> str:
        if not cb:
            return ""
        data = cb.currentData()
        if data and isinstance(data, str) and data not in ("(None)", "--", ""):
            return data
        txt = cb.currentText().strip()
        if not txt or txt in ("(None)", "--"):
            return ""
        return txt.split(" (")[0].strip()

    def _on_compare_selection_changed(self) -> None:
        if not hasattr(self, "cp_compare_combo_a") or not hasattr(self, "cp_compare_combo_b"):
            return
        self._cp_compare_a = self._get_cb_val(self.cp_compare_combo_a)
        self._cp_compare_b = self._get_cb_val(self.cp_compare_combo_b)
        self._cp_compare_c = self._get_cb_val(getattr(self, "cp_compare_combo_c", None))
        self._cp_compare_d = self._get_cb_val(getattr(self, "cp_compare_combo_d", None))
        self._cp_compare_e = self._get_cb_val(getattr(self, "cp_compare_combo_e", None))
        session = self._get_displayed_session()
        if session:
            self._refresh_rail_compare(session, max(1.0, session.duration), getattr(self, "_cp_metric", "damage"))

    # --- render ---------------------------------------------------------------

    def _refresh_rail_compare(self, session: CombatSession, duration: float, metric: str) -> None:
        refresh_rail_compare(self, session, duration, metric)


__all__ = ["CombatCompareMixin"]
