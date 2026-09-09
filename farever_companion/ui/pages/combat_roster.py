"""Combat Roster & Breakdown Mixin.

Provides the player table headers, meter table row widgets,
column-based sorting, class grouping, and breakdown rendering.
"""
from __future__ import annotations

from PySide6 import QtCore, QtWidgets

from .. import theme
from ...core.dps_tracker import own_row, view_totals
from ..combat import _ranked_skill_total, _cp_card_alive
from ..dps_source_text import empty_hint

# Segments whose empty board needs naming (an empty Boss board on a trash-only
# run is not "no combat recorded" - the reader is fine, that slice is empty).
_CP_SEGMENT_EMPTY_LABELS = {
    "boss": "boss fight",
    "trash": "trash pulls",
    "boss_adds": "boss adds",
}


def _cp_empty_hint(dm, session, segment: str) -> str:
    """Empty-state line for the breakdown board."""
    if (segment in _CP_SEGMENT_EMPTY_LABELS and session is not None
            and session.group_damage <= 0 and session.group_heals <= 0):
        return (f"No {_CP_SEGMENT_EMPTY_LABELS[segment]} recorded in this run."
                f" Pick Auto for the live fight.")
    return empty_hint(
        dm, "No combat data recorded yet. Attack enemies or dummy in-game.")


class _HeaderCellLabel(QtWidgets.QLabel):
    clicked = QtCore.Signal(str)

    def __init__(self, key: str, text: str, width=None, flex=0, align=QtCore.Qt.AlignLeft, parent=None):
        super().__init__(text.upper(), parent)
        self.key = key
        self.base_text = text.upper()
        self.setWordWrap(False)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setAlignment(align | QtCore.Qt.AlignVCenter)
        if width:
            self.setFixedWidth(width)

    def mousePressEvent(self, ev):
        if ev.button() == QtCore.Qt.LeftButton:
            self.clicked.emit(self.key)
            ev.accept()
        else:
            super().mousePressEvent(ev)


class _TableHeaderRow(QtWidgets.QFrame):
    """Header row sitting at index 0 inside the roster board layout with interactive column sorting."""

    sort_requested = QtCore.Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("TableHeaderRow")
        self.setFixedHeight(28)
        self.setStyleSheet(
            f"QFrame#TableHeaderRow {{ background: {theme.PANEL_LOW}; "
            f"border-bottom: 1px solid {theme.BORDER}; border-radius: 4px; }}"
        )

        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(8, 4, 8, 4)
        lay.setSpacing(6)

        def _cell(key: str, text: str, width=None, flex=0, align=QtCore.Qt.AlignLeft) -> _HeaderCellLabel:
            lbl = _HeaderCellLabel(key, text, width=width, flex=flex, align=align)
            lbl.setStyleSheet(
                f"QLabel {{ font-size: 11.5px; font-weight: 800; color: {theme.DIM}; letter-spacing: 0.5px; }}"
                f"QLabel:hover {{ color: {theme.TEXT}; }}"
            )
            lbl.clicked.connect(self._on_cell_clicked)
            if flex:
                lay.addWidget(lbl, flex)
            else:
                lay.addWidget(lbl)
            return lbl

        self.rank_lbl = _cell("rank", "Rank", width=30)
        self.name_lbl = _cell("name", "Players", flex=1, align=QtCore.Qt.AlignCenter)
        self.class_lbl = _cell("class", "Class", width=52, align=QtCore.Qt.AlignCenter)
        self.rate_lbl = _cell("dps", "DPS", width=52, align=QtCore.Qt.AlignCenter)
        self.total_lbl = _cell("total", "Total", width=62, align=QtCore.Qt.AlignCenter)
        self.share_lbl = _cell("share", "Share", width=48, align=QtCore.Qt.AlignCenter)
        self.crit_lbl = _cell("crit", "Crit %", width=42, align=QtCore.Qt.AlignCenter)

        self._active_sort = "dps"

    def _on_cell_clicked(self, key: str):
        self.sort_requested.emit(key)

    def set_metric(self, metric: str):
        if metric == "healing":
            self.rate_lbl.base_text = "HPS"
            self.total_lbl.base_text = "HEALS"
        elif metric in ("taken", "damage_taken"):
            self.rate_lbl.base_text = "DTPS"
            self.total_lbl.base_text = "TAKEN"
        else:
            self.rate_lbl.base_text = "DPS"
            self.total_lbl.base_text = "TOTAL"
        self.set_active_sort(self._active_sort)

    def set_active_sort(self, sort_key: str):
        self._active_sort = sort_key
        labels = [
            ("rank", self.rank_lbl),
            ("name", self.name_lbl),
            ("class", self.class_lbl),
            ("dps", self.rate_lbl),
            ("total", self.total_lbl),
            ("share", self.share_lbl),
            ("crit", self.crit_lbl),
        ]
        for key, lbl in labels:
            is_active = (key == sort_key)
            suffix = " ▼" if is_active else ""
            lbl.setText(f"{lbl.base_text}{suffix}")
            active_col = theme.GOLD if key == "class" else theme.ACCENT_LIGHT
            col = active_col if is_active else theme.DIM
            weight = "900" if is_active else "800"
            lbl.setStyleSheet(
                f"QLabel {{ font-size: 11.5px; font-weight: {weight}; color: {col}; letter-spacing: 0.5px; }}"
                f"QLabel:hover {{ color: {theme.TEXT}; }}"
            )


class _MeterTableRow(QtWidgets.QFrame):
    clicked = QtCore.Signal(str)
    compare_clicked = QtCore.Signal(str)

    def __init__(self, name: str, is_me: bool, parent=None):
        super().__init__(parent)
        self.name = name
        self.is_me = is_me
        self._is_selected = False

        self.setObjectName("MeterTableRow")
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setFixedHeight(38)

        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(8, 4, 8, 4)
        lay.setSpacing(6)

        # Rank
        self.rank_lbl = QtWidgets.QLabel("#1")
        self.rank_lbl.setFixedWidth(30)
        self.rank_lbl.setStyleSheet(f"font-size: 11.5px; font-weight: 800; color: {theme.GOLD}; font-family: monospace;")
        lay.addWidget(self.rank_lbl)

        # Name (Centered with Stretch on both sides)
        name_box = QtWidgets.QHBoxLayout()
        name_box.setSpacing(4)
        name_box.setContentsMargins(0, 0, 0, 0)
        name_box.addStretch(1)

        clean_nm = name.replace(" (You)", "").replace(" (YOU)", "")
        self.name_lbl = QtWidgets.QLabel(clean_nm)
        self.name_lbl.setStyleSheet(f"font-size: 12px; font-weight: 700; color: {theme.ACCENT_LIGHT if is_me else theme.TEXT};")
        name_box.addWidget(self.name_lbl)

        if is_me:
            you_tag = QtWidgets.QLabel("YOU")
            you_tag.setStyleSheet(
                f"font-size: 8px; font-weight: 800; color: {theme.ACCENT}; "
                f"background: {theme.ACCENT_DIM}; border-radius: 2px; padding: 1px 3px;"
            )
            name_box.addWidget(you_tag)

        name_box.addStretch(1)

        name_widget = QtWidgets.QWidget()
        name_widget.setLayout(name_box)
        lay.addWidget(name_widget, 1)

        # Class (52px - Centered)
        self.class_lbl = QtWidgets.QLabel("")
        self.class_lbl.setFixedWidth(52)
        self.class_lbl.setAlignment(QtCore.Qt.AlignCenter)
        self.class_lbl.setStyleSheet("font-size: 11px; font-weight: 700;")
        lay.addWidget(self.class_lbl)

        # DPS / HPS Rate (52px - Centered)
        self.rate_lbl = QtWidgets.QLabel("0/s")
        self.rate_lbl.setFixedWidth(52)
        self.rate_lbl.setAlignment(QtCore.Qt.AlignCenter)
        self.rate_lbl.setStyleSheet(f"font-size: 12.5px; font-weight: 800; color: {theme.ACCENT_LIGHT}; font-family: monospace;")
        lay.addWidget(self.rate_lbl)

        # Total (62px - Centered)
        self.total_lbl = QtWidgets.QLabel("0")
        self.total_lbl.setFixedWidth(62)
        self.total_lbl.setAlignment(QtCore.Qt.AlignCenter)
        self.total_lbl.setStyleSheet(f"font-size: 11px; color: {theme.TEXT}; font-family: monospace;")
        lay.addWidget(self.total_lbl)

        # Share + Bar (48px - Centered)
        share_box = QtWidgets.QVBoxLayout()
        share_box.setSpacing(2)
        share_box.setContentsMargins(0, 0, 0, 0)
        self.share_lbl = QtWidgets.QLabel("0.0%")
        self.share_lbl.setAlignment(QtCore.Qt.AlignCenter)
        self.share_lbl.setStyleSheet(f"font-size: 9.5px; font-weight: 700; color: {theme.TEXT};")
        self.bar = QtWidgets.QProgressBar()
        self.bar.setFixedHeight(4)
        self.bar.setTextVisible(False)
        self.bar.setRange(0, 100)
        self.bar.setStyleSheet(
            f"QProgressBar {{ background: {theme.SURFACE}; border: none; border-radius: 2px; }}"
            f"QProgressBar::chunk {{ background: {theme.ACCENT}; border-radius: 2px; }}"
        )
        share_box.addWidget(self.share_lbl)
        share_box.addWidget(self.bar)

        share_widget = QtWidgets.QWidget()
        share_widget.setLayout(share_box)
        share_widget.setFixedWidth(48)
        lay.addWidget(share_widget)

        # Crit % (42px - Centered)
        self.crit_lbl = QtWidgets.QLabel("--")
        self.crit_lbl.setFixedWidth(42)
        self.crit_lbl.setAlignment(QtCore.Qt.AlignCenter)
        self.crit_lbl.setStyleSheet(f"font-size: 10.5px; color: {theme.ORANGE}; font-family: monospace;")
        lay.addWidget(self.crit_lbl)
        self._last_bar_col = None
        self._last_cls_color = None

        self._update_style()

    def mousePressEvent(self, ev):
        if ev.button() == QtCore.Qt.LeftButton:
            self.clicked.emit(self.name)
            ev.accept()
        else:
            super().mousePressEvent(ev)

    def set_selected(self, sel: bool):
        if self._is_selected == sel:
            return
        self._is_selected = sel
        self._update_style()

    def _update_style(self):
        bg = "#0f2e3f" if self._is_selected else theme.PANEL
        border = theme.ACCENT if self._is_selected else theme.BORDER
        left_border = f"border-left: 3px solid {theme.ACCENT};" if self.is_me else ""
        self.setStyleSheet(
            f"QFrame#MeterTableRow {{ background-color: {bg}; border: 1px solid {border}; "
            f"{left_border} border-radius: 5px; }}"
            f"QFrame#MeterTableRow:hover {{ background-color: #334155; }}"
        )

    def update_stats(self, idx: int, p, duration: float, group_dmg: float, group_heals: float, tab: str, class_rank: int = 1, class_total: int = 1, group_taken: float = 0.0):
        cls_name = p.hero_class or "Hero"
        cls_color = theme.class_color(cls_name)
        self.class_lbl.setText(cls_name)
        if self._last_cls_color != cls_color:
            self._last_cls_color = cls_color
            self.class_lbl.setStyleSheet(f"font-size: 11.5px; font-weight: 700; color: {cls_color};")

        rank_txt = f"#{idx + 1}"
        self.rank_lbl.setText(rank_txt)

        dps_val = p.dps(duration)
        hps_val = p.hps(duration)
        dmg_share = p.share_pct(group_dmg)
        heal_share = p.heal_share_pct(group_heals)
        taken_val = getattr(p, "total_damage_taken", p.damage_taken)
        taken_share = (taken_val / max(1.0, group_taken)) * 100.0 if group_taken > 0 else 0.0

        if tab == "healing":
            total = p.heals
            rate = hps_val
            share_val = heal_share
            bar_col = theme.GOOD
        elif tab in ("taken", "damage_taken"):
            total = taken_val
            rate = total / max(1.0, duration)
            share_val = taken_share
            bar_col = theme.ORANGE
        else:
            total = p.total_damage
            rate = dps_val
            share_val = dmg_share
            bar_col = theme.ACCENT

        rate_str = f"{rate:,.0f}/s" if rate < 100_000 else f"{rate/1000:.1f}k/s"
        total_str = f"{total:,.0f}" if total < 10_000_000 else f"{total/1_000_000:.2f}M"

        self.rate_lbl.setText(rate_str)
        self.total_lbl.setText(total_str)
        self.share_lbl.setText(f"{share_val:.1f}%")
        self.bar.setValue(int(min(100.0, share_val)))
        if self._last_bar_col != bar_col:
            self._last_bar_col = bar_col
            self.bar.setStyleSheet(
                f"QProgressBar {{ background: {theme.SURFACE}; border: none; border-radius: 2px; }}"
                f"QProgressBar::chunk {{ background: {bar_col}; border-radius: 2px; }}"
            )

        total_crits = sum(getattr(s, "crit_count", 0) for s in p.skills.values())
        total_hits = sum(getattr(s, "hit_count", 0) for s in p.skills.values()) or getattr(p, "hit_count", 0)
        crit_pct = (total_crits / total_hits * 100.0) if total_hits > 0 else 0.0
        self.crit_lbl.setText(f"{crit_pct:.0f}%" if total_hits > 0 else "--")


class CombatRosterMixin:
    """Roster board UI construction, sorting, class columns, and breakdown rendering."""

    def _build_roster_board(self) -> QtWidgets.QWidget:
        self.cp_header_row = _TableHeaderRow()
        self.cp_header_row.sort_requested.connect(self._set_combat_sort_mode)

        self.cp_class_board = QtWidgets.QWidget()
        self.cp_board_lay = QtWidgets.QVBoxLayout(self.cp_class_board)
        self.cp_board_lay.setContentsMargins(0, 0, 0, 0)
        self.cp_board_lay.setSpacing(4)
        self.cp_board_lay.addStretch(1)

        self._cp_col_lays: dict[str, QtWidgets.QVBoxLayout] = {}
        self._cp_col_frames: dict[str, QtWidgets.QFrame] = {}
        self._cp_col_headers: dict[str, tuple[QtWidgets.QLabel, QtWidgets.QLabel]] = {}
        self._cp_col_scroll = QtWidgets.QScrollArea()
        self._cp_col_scroll.setWidgetResizable(True)
        self._cp_col_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        self._cp_col_scroll.setStyleSheet("background: transparent;")
        self._cp_col_scroll.setWidget(self.cp_class_board)

        self.cp_board_container = QtWidgets.QWidget()
        bc_lay = QtWidgets.QVBoxLayout(self.cp_board_container)
        bc_lay.setContentsMargins(0, 0, 0, 0)
        bc_lay.setSpacing(0)
        bc_lay.addWidget(self.cp_header_row, 0)
        bc_lay.addWidget(self._cp_col_scroll, 1)
        return self.cp_board_container

    def _set_combat_sort_mode(self, mode: str):
        """Switch player sorting mode between overall DPS rank vs class grouping."""
        self._cp_sort_mode = mode
        if hasattr(self, "cp_sort_dps_btn"):
            self.cp_sort_dps_btn.setChecked(mode == "dps")
        if hasattr(self, "cp_sort_class_btn"):
            self.cp_sort_class_btn.setChecked(mode == "class")
        self._render_combat_breakdown()


    def _prune_class_columns(self, keep: set[str]):
        """Hide class columns whose players are not active in the current view."""
        for cls, frame in self._cp_col_frames.items():
            if not _cp_card_alive(frame):
                continue
            if cls in keep:
                if frame.isHidden():
                    frame.show()
            else:
                if not frame.isHidden():
                    frame.hide()

    def _render_combat_breakdown(self):
        metric = getattr(self, "_cp_metric", "damage")
        is_compare = (metric == "compare")
        is_fights = (metric == "fights") or (getattr(self, "_cp_segment", "auto") == "past_fights")

        if hasattr(self, "cp_board_container"):
            self.cp_board_container.setVisible(not (is_compare or is_fights))
        if hasattr(self, "_cp_rail_container"):
            if is_compare or is_fights:
                # The compare matrix and the fights list own the whole body:
                # the rail has no place there, so off is unconditional.
                self._cp_rail_container.setVisible(False)
            else:
                # The damage view is the rail's home, so its visibility is the
                # WIDTH rule's call, not this render's. Setting it visible here
                # unconditionally undid the hide ``_update_combat_rail_for_width``
                # performs earlier in the same live-refresh tick, so at a narrow
                # page the 250ms timer flickered the rail back on over the
                # clipped table (the bug ``fit_rail_to_page`` exists to stop).
                fit = getattr(self, "_update_combat_rail_for_width", None)
                if fit is not None:
                    fit()
        if hasattr(self, "cp_full_compare_container"):
            self.cp_full_compare_container.setVisible(is_compare)
        scroll = getattr(self, "_cp_past_scroll", None)
        if scroll is not None:
            scroll.setVisible(is_fights)

        if is_fights:
            if hasattr(self, "cp_plotter") and hasattr(self.cp_plotter, "set_compare_players"):
                self.cp_plotter.set_compare_players([])
            if hasattr(self, "cp_plotter_info"):
                self.cp_plotter_info.setText("📈 Encounter DPS Timeline")
            self._render_past_fights()
            return
        elif is_compare:
            session = self._get_displayed_session()
            if session:
                self._refresh_rail_compare(session, max(1.0, session.duration), "damage")
            return

        if hasattr(self, "cp_plotter") and hasattr(self.cp_plotter, "set_compare_players"):
            self.cp_plotter.set_compare_players([])
        if hasattr(self, "cp_plotter_info"):
            self.cp_plotter_info.setText("📈 Encounter DPS Timeline")

        session = self._get_displayed_session()
        if not session:
            return

        metric = getattr(self, "_cp_metric", "damage")
        if hasattr(self, "cp_header_row"):
            self.cp_header_row.set_metric(metric)

        duration = max(1.0, session.duration)
        solo_view = self._cp_solo_filter_on()
        me_p = own_row(session) if solo_view else None

        players = session.ranked_players(view=metric, pin_me=True)
        if solo_view:
            players = [me_p] if me_p is not None else []

        sort_mode = getattr(self, "_cp_sort_mode", "dps")
        if hasattr(self, "cp_header_row"):
            self.cp_header_row.set_active_sort(sort_mode)

        if not solo_view:
            if sort_mode == "class":
                by_cls: dict[str, list] = {}
                for p in players:
                    raw = (p.hero_class or "").strip()
                    cls = raw.capitalize() if raw.lower() in theme.HERO else "Other"
                    by_cls.setdefault(cls, []).append(p)

                def _cls_total(cls_nm):
                    return max((p.heals if metric == "healing" else p.total_damage for p in by_cls[cls_nm]), default=0.0)

                sorted_classes = sorted(by_cls.keys(), key=_cls_total, reverse=True)
                ordered_p = []
                for cls_nm in sorted_classes:
                    p_sorted = sorted(
                        by_cls[cls_nm],
                        key=lambda p: p.heals if metric == "healing" else p.total_damage,
                        reverse=True
                    )
                    ordered_p.extend(p_sorted)
                players = ordered_p

            elif sort_mode in ("dps", "total", "share"):
                def _val_func(p):
                    if metric == "healing":
                        return p.heals
                    elif metric in ("taken", "damage_taken"):
                        return getattr(p, "total_damage_taken", p.damage_taken)
                    return p.total_damage
                players = sorted(players, key=_val_func, reverse=True)

            elif sort_mode == "crit":
                def _crit_pct(p):
                    crits = sum(s.crit_count for s in p.skills.values())
                    hits = sum(s.hit_count for s in p.skills.values())
                    return (crits / hits * 100.0) if hits > 0 else 0.0
                players = sorted(players, key=_crit_pct, reverse=True)

            elif sort_mode == "name":
                players = sorted(players, key=lambda p: p.name.lower())

            elif sort_mode == "rank":
                players = sorted(players, key=lambda p: session.get_rank(p, view=metric))

        # Always pin the local character at the top (row 0) regardless of sort
        if not solo_view:
            me_p_list = [p for p in players if p.is_me]
            oth_p_list = [p for p in players if not p.is_me]
            players = me_p_list + oth_p_list

        if metric == "skills":
            def _top_skill_val(p):
                sk = _ranked_skill_total(p)
                return sk[0].total if sk else 0.0
            active = session.active_players()   # dummy: no chip-damage-only rows
            natural = sorted(active, key=_top_skill_val, reverse=True)
            me_list = [p for p in natural if p.is_me]
            others = [p for p in natural if not p.is_me]
            players = (me_list + others) if me_list else natural
            if solo_view:
                players = [p for p in players if p.is_me]

        cls_filter = getattr(self, "_cp_class_filter", set())
        by_class: dict[str, list] = {}
        for p in players:
            raw = (p.hero_class or "").strip()
            cls = raw.capitalize() if raw.lower() in theme.HERO else "Other"
            if cls_filter and cls not in cls_filter:
                continue
            by_class.setdefault(cls, []).append(p)

        if not by_class:
            if hasattr(self, "cp_empty_lbl"):
                dm = getattr(self.model, "damage", None) if self.model else None
                hint = _cp_empty_hint(
                    dm, session, getattr(self, "_cp_segment", "auto"))
                if self.cp_empty_lbl.parent() is not getattr(self, "cp_class_board", None):
                    self.cp_empty_lbl.setParent(getattr(self, "cp_class_board", None))
                if self.cp_empty_lbl.text() != hint:
                    self.cp_empty_lbl.setText(hint)
                self.cp_empty_lbl.show()
            self._hide_all_column_cards()
            self._prune_class_columns(set())
            return

        if hasattr(self, "cp_empty_lbl"):
            self.cp_empty_lbl.hide()

        # Row share basis, from the same helper as the stat cards above -
        # solo rows are 100% of your own parse, exactly as on the overlay.
        group_dmg, group_heals, group_taken = view_totals(session, solo_view)

        active_names: set[str] = set()
        visible_idx = 0
        for p in players:
            raw = (p.hero_class or "").strip()
            cls = raw.capitalize() if raw.lower() in theme.HERO else "Other"
            if cls_filter and cls not in cls_filter:
                continue

            cache_key = f"meter\x1f{p.name}"
            active_names.add(cache_key)
            card = self._cp_player_cards.get(cache_key)
            if card is not None and not _cp_card_alive(card):
                del self._cp_player_cards[cache_key]
                card = None
            if card is None:
                card = _MeterTableRow(p.name, is_me=p.is_me)
                card.clicked.connect(self._on_player_card_clicked)
                card.compare_clicked.connect(self._on_player_compare_clicked)
                self._cp_player_cards[cache_key] = card
                self.cp_board_lay.insertWidget(visible_idx, card)
            if card.isHidden():
                card.show()

            true_rank = session.get_rank(p, view=metric)
            card.update_stats(true_rank - 1, p, duration,
                              group_dmg, group_heals, group_taken=group_taken, tab=metric,
                              class_rank=visible_idx + 1, class_total=len(players))
            card.set_selected(getattr(self, "_cp_selected_player", "") == p.name)

            cur = self.cp_board_lay.indexOf(card)
            if cur != visible_idx:
                self.cp_board_lay.insertWidget(visible_idx, card)
            visible_idx += 1

        for key, card in list(self._cp_player_cards.items()):
            if key not in active_names:
                try:
                    if not card.isHidden():
                        card.hide()
                except RuntimeError:
                    self._cp_player_cards.pop(key, None)

        self._update_rail_content(session, duration)

    def _hide_all_column_cards(self):
        """Hide every breakdown card (empty session / reset)."""
        for name, card in list(getattr(self, "_cp_player_cards", {}).items()):
            try:
                if not card.isHidden():
                    card.hide()
            except RuntimeError:
                del self._cp_player_cards[name]
