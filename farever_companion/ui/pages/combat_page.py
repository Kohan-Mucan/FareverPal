"""Combat & DPS Analysis Page.

Comprehensive DPS Breakdown, Combat Timeline & Burst Plotter,
and live encounter analysis.
"""
from __future__ import annotations

from PySide6 import QtCore, QtWidgets

from .. import theme
from ..dps_source_text import dungeon_mode_badge, instance_mode, mode_tooltip
from ..memory_reclaimer import _forget_attrs
from ...data import icons
from ...core.dps_tracker import (
    DpsTracker, CombatSession, view_totals,
)
from ..combat import (
    _CombatTimelinePlotter,
    _StatStrip,
    _PlayerCardWidget,
    _clean_session_name,
)
from .combat_history import (
    _fit_history_popup,
    CombatHistoryMixin,
    _RichComboDelegate,
)
from .combat_roster import (
    CombatRosterMixin,
    _cp_empty_hint,
)
from .combat_rail import (
    CombatRailMixin,
    _SkillWeaponPopup,
)
from .combat_compare import CombatCompareMixin


# The fight-detail rail auto-hides when the page is too narrow to hold it, so
# the roster keeps its room. The threshold is NOT a constant: it used to be
# CP_RAIL_AUTO_MIN_WIDTH = 1040, which was correct back when the rail's minimum
# was 470px, and then the rail grew (the TYPE column, then cell widths sized to
# their contents) until the PAGE's own minimum was 1186px. A 1040 threshold was
# then below the page's minimum, so between 1040 and 1186 the rail was shown in
# a page that could not physically hold it.
#
# The failure was silent and it clipped two unrelated things at the same edge.
# The Combat page is one of the pages ControlPanel._select_nav does NOT wrap in
# a QScrollArea — it is inserted straight into the QStackedWidget — so the page
# is sized to the stack, the splitter's rail overflows the page's right edge,
# and the page clips at its own boundary. No scrollbar, no warning: the tail of
# the skills table and the DMG TYPE bar above it simply stop mid-number.
#
# So the threshold is measured from the built page instead of remembered. See
# CombatRailMixin._update_combat_rail_for_width.
CP_RAIL_AUTO_MIN_WIDTH = 1040   # fallback only, before the page is measured

# Segment selector on the Combat & DPS page: (tracker segment, chip, tooltip).
_CP_SEGMENTS: tuple[tuple[str, str, str], ...] = (
    ("auto", "⟳ Auto",
     "Follow the fight that is running (dummy → boss → trash)"),
    ("boss", "👑 Boss", "Only the boss fight"),
    ("trash", "🗡 Trash", "Only the trash pulls"),
    ("boss_adds", "➕ Adds", "Only the adds that spawned during the boss fight"),
    ("overall", "Σ Overall", "The whole run, every segment combined"),
)
_CP_SEGMENT_ORDER: tuple[str, ...] = tuple(seg for seg, _, _ in _CP_SEGMENTS)

# Metrics that show a whole-encounter view instead of the segment board
_CP_WHOLE_ENCOUNTER_METRICS = ("fights", "compare")

#: On the Fights LIST the segment row doubles as that list's KIND filter. Only
#: the two chips that name a kind map anywhere; Auto / Overall / Adds show
#: every kind (there is no "adds" or "overall" file to filter to, since an
#: archived add pull folds into its boss's log). Kept as one pair of lookups so
#: the chip -> filter and filter -> chip directions can never disagree.
_SEGMENT_TO_FIGHTS_KIND = {"boss": "boss", "trash": "trash"}
#: The reverse direction, plus ``all`` -> Auto (the list's "every kind" state is
#: what the Auto chip means there). A kind with NO segment chip — ``dummy`` and
#: ``failed`` — maps to "" so the row lights nothing rather than claiming a kind
#: it is not filtering to.
_FIGHTS_KIND_TO_SEGMENT = {"boss": "boss", "trash": "trash", "all": "auto"}


class _RailResizeWatcher(QtCore.QObject):
    """Calls back on page resize so the rail can auto-hide on narrow windows."""

    def __init__(self, target: QtWidgets.QWidget, callback):
        super().__init__(target)
        self._cb = callback
        target.installEventFilter(self)

    def eventFilter(self, obj, ev):
        if ev.type() == QtCore.QEvent.Resize:
            try:
                self._cb()
            except Exception:
                pass
        return False


class CombatPageMixin(CombatHistoryMixin, CombatRosterMixin, CombatRailMixin, CombatCompareMixin):
    """ControlPanel mixin providing the Combat & DPS page."""

    def _page_combat(self) -> QtWidgets.QWidget:
        return self._build_combat_page()

    def _build_combat_page(self) -> QtWidgets.QWidget:
        page, lay = self._page_container()
        self._cp_player_cards: dict[str, _PlayerCardWidget] = {}
        self._selected_history_idx: int = 0  # 0 = Live Encounter
        self._cp_selected_player: str = ""
        self._cp_compare_a: str = ""
        self._cp_compare_b: str = ""
        self._cp_rail_skill_widgets: dict[str, QtWidgets.QWidget] = {}
        self._cp_cmp_row_widgets: list[tuple] = []
        _forget_attrs(self, ("_cp_cmp_hdr_a", "_cp_cmp_hdr_b", "_last_cmp_fp"))
        self._cp_fights_filter: str = "all"
        self._cp_fights_show_all_days: bool = False
        self._cp_fights_query: str = ""
        self._browse_sessions: list[CombatSession] | None = None
        self._browse_char: str = ""
        self._auto_last_fight: bool = False
        self._auto_last_suppressed: bool = True
        self._cp_segment: str = "auto"

        hdr_box = QtWidgets.QHBoxLayout()
        hdr_box.setSpacing(10)

        title_lbl = QtWidgets.QLabel("DPS Analysis")
        title_lbl.setStyleSheet(f"font-size: 15px; font-weight: 800; color: {theme.TEXT};")
        hdr_box.addWidget(title_lbl)

        # Instance difficulty beside the page title (· NORMAL / HARD / HEROIC),
        # the same resolution and the same badge text the Top DPS HUD and the
        # Run Timer use (see ui/dps_source_text.instance_mode). It lives in the
        # header rather than on the rail's weapon line so it shows whether or
        # not a weapon was recognised — a dungeon whose difficulty is known
        # should say so on this page (2026-09-30).
        self.cp_mode_badge = QtWidgets.QLabel("")
        self.cp_mode_badge.setTextFormat(QtCore.Qt.RichText)
        self.cp_mode_badge.setVisible(False)
        hdr_box.addWidget(self.cp_mode_badge)

        hdr_box.addStretch(1)

        # Fight selector dropdown
        self.cp_history_combo = QtWidgets.QComboBox()
        self.cp_history_combo.setItemDelegate(_RichComboDelegate(self.cp_history_combo))
        self.cp_history_combo.addItem("● Live Encounter (Active)")
        self.cp_history_combo.setToolTip("Encounter shown: the live session or any archived fight")
        self.cp_history_combo.setStyleSheet(
            f"QComboBox {{ min-width: 360px; max-width: 640px; padding: 5px 10px; "
            f"font-weight: 700; font-size: 11.5px; border: 1px solid {theme.BORDER}; border-radius: 4px; "
            f"background: {theme.PANEL}; color: {theme.TEXT}; }}"
            f"QComboBox::drop-down {{ border: none; width: 20px; }}"
            f"QComboBox QAbstractItemView {{ background: {theme.PANEL}; border: 1px solid {theme.BORDER}; "
            f"selection-background-color: {theme.ACCENT_DIM}; selection-color: {theme.TEXT}; padding: 4px; }}"
        )
        self.cp_history_combo.setSizeAdjustPolicy(
            QtWidgets.QComboBox.AdjustToContents)
        # Fit the popup to its widest entry now, not only after the first
        # repopulate: the closed combo carries `max-width: 640px`, and that
        # clamp is inherited by the DROPDOWN, so until the fit runs the list
        # clips its own labels (see combat_history._fit_history_popup).
        _fit_history_popup(self.cp_history_combo)
        self.cp_history_combo.currentIndexChanged.connect(self._on_history_selection_changed)
        hdr_box.addWidget(self.cp_history_combo)

        rst_btn = QtWidgets.QPushButton("Reset")
        rst_btn.setIcon(icons.ui_qicon("refresh-cw", theme.MUTED, 14))
        rst_btn.setStyleSheet("padding: 6px 10px; font-weight: 600;")
        rst_btn.clicked.connect(self._reset_combat_session)
        hdr_box.addWidget(rst_btn)

        ov_btn = QtWidgets.QPushButton()
        ov_btn.setIcon(icons.ui_qicon("layers", theme.ACCENT, 14))
        ov_btn.setToolTip("Show / hide the Top DPS overlay")
        ov_btn.setStyleSheet(f"padding: 6px 8px; color: {theme.ACCENT};")
        ov_btn.clicked.connect(lambda: self._request_overlay("dps", True))
        hdr_box.addWidget(ov_btn)

        pb_btn = QtWidgets.QPushButton("PB History")
        pb_btn.setIcon(icons.ui_qicon("trophy", theme.GOLD, 14))
        pb_btn.setToolTip("View shared Run Timer history across characters")
        pb_btn.setStyleSheet(
            f"padding: 6px 10px; font-weight: 600; color: {theme.GOLD};")
        pb_btn.clicked.connect(
            lambda: self._select_nav("pb") if hasattr(self, "_select_nav") else None)
        hdr_box.addWidget(pb_btn)

        dps_settings_btn = QtWidgets.QPushButton("⚙ DPS Settings")
        dps_settings_btn.setStyleSheet("padding: 6px 10px; font-weight: 600;")
        dps_settings_btn.clicked.connect(lambda: self._select_nav("settings:dps") if hasattr(self, "_select_nav") else None)
        hdr_box.addWidget(dps_settings_btn)

        lay.addLayout(hdr_box)

        # Shown while displaying an auto-adopted archived fight.
        self.cp_hist_banner = QtWidgets.QLabel("")
        self.cp_hist_banner.setVisible(False)
        self.cp_hist_banner.setWordWrap(True)
        self.cp_hist_banner.setStyleSheet(
            f"font-size: 10px; font-weight: 700; color: {theme.GOLD}; "
            f"background: {theme.PANEL_LOW}; border: 1px solid {theme.BORDER}; "
            f"border-radius: 4px; padding: 4px 8px;")
        lay.addWidget(self.cp_hist_banner)

        self.cp_stat_strip = _StatStrip()
        lay.addWidget(self.cp_stat_strip)
        self.cp_card_dur = self.cp_card_dmg = self.cp_card_heal = \
            self.cp_card_taken = self.cp_card_target = self.cp_stat_strip

        # Combat Timeline & Burst Plotter
        plotter_box = QtWidgets.QVBoxLayout()
        plotter_box.setSpacing(4)
        plotter_hdr = QtWidgets.QHBoxLayout()
        plotter_hdr.addStretch(1)

        self.cp_plotter_info = QtWidgets.QLabel("")
        self.cp_plotter_info.setStyleSheet(f"font-size: 10px; color: {theme.MUTED}; font-family: monospace;")
        plotter_hdr.addWidget(self.cp_plotter_info)
        self.cp_plotter_info.hide()
        plotter_box.addLayout(plotter_hdr)

        self.cp_plotter = _CombatTimelinePlotter()
        plotter_box.addWidget(self.cp_plotter)
        lay.addLayout(plotter_box)

        # Left column (chips + board) vs rail
        left_col = QtWidgets.QWidget()
        left_lay = QtWidgets.QVBoxLayout(left_col)
        left_lay.setContentsMargins(0, 0, 0, 0)
        left_lay.setSpacing(8)

        self._cp_metric = "damage"
        self._cp_class_filter: set[str] = set()

        control_row = QtWidgets.QHBoxLayout()
        control_row.setSpacing(6)

        def _chip(text: str, tip: str, active_color: str = theme.ACCENT) -> QtWidgets.QPushButton:
            b = QtWidgets.QPushButton(text)
            b.setCheckable(True)
            b.setToolTip(tip)
            b.setStyleSheet(
                f"QPushButton {{ padding: 4px 12px; font-size: 10.5px; "
                f"font-weight: 700; border-radius: 4px; border: 1px solid {theme.BORDER}; color: {theme.TEXT}; }}"
                f"QPushButton:checked {{ background-color: {theme.with_alpha(active_color, 40)}; "
                f"color: {active_color}; border: 1px solid {active_color}; }}"
            )
            return b

        self.cp_metric_chips: dict[str, QtWidgets.QPushButton] = {}
        chip_configs = [
            ("damage", "⚔ Damage", theme.ACCENT_LIGHT),
            ("healing", "✚ Healing", theme.GOOD),
            ("taken", "🛡 Taken", theme.ORANGE),
            ("compare", "⚖ Compare", theme.GOLD),
            ("fights", "📜 Fights", theme.ACCENT),
        ]
        for m, lbl, col in chip_configs:
            b = _chip(lbl, f"View {m}", active_color=col)
            b.clicked.connect(lambda _, mm=m: self._set_combat_metric(mm))
            self.cp_metric_chips[m] = b
            control_row.addWidget(b)

        self._cp_sort_mode = "dps"
        control_row.addStretch(1)
        left_lay.addLayout(control_row)
        self.cp_class_chips: dict[str, QtWidgets.QPushButton] = {}

        # Segment selector
        self.cp_segment_chips: dict[str, QtWidgets.QPushButton] = {}
        seg_row = QtWidgets.QHBoxLayout()
        seg_row.setSpacing(6)
        seg_cap = QtWidgets.QLabel("SEGMENT")
        seg_cap.setStyleSheet(
            f"font-size: 9.5px; font-weight: 800; color: {theme.DIM};"
            f"letter-spacing: 0.5px;")
        seg_cap.setToolTip(
            "Which part of the encounter this page analyses. Page-local: the\n"
            "Top DPS overlay keeps following the live fight.")
        seg_row.addWidget(seg_cap)
        for seg, lbl, tip in _CP_SEGMENTS:
            b = _chip(lbl, tip, active_color=theme.GOLD)
            b.clicked.connect(lambda _, ss=seg: self._set_combat_page_segment(ss))
            self.cp_segment_chips[seg] = b
            seg_row.addWidget(b)
        seg_row.addStretch(1)
        left_lay.addLayout(seg_row)
        self._sync_segment_chips()

        # Modular views: past fights, roster board, compare matrix, and rail
        scroll = self._build_past_fights_view()
        board_container = self._build_roster_board()
        full_compare = self._build_compare_container()
        rail_container = self._build_combat_rail()

        left_lay.addWidget(board_container, 1)
        left_lay.addWidget(full_compare, 1)
        left_lay.addWidget(scroll, 1)

        self.cp_body_split = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        self.cp_body_split.setChildrenCollapsible(False)
        self.cp_body_split.setHandleWidth(6)
        self.cp_body_split.setStyleSheet(
            f"QSplitter::handle {{ background-color: {theme.BORDER}; border-radius: 2px; margin: 0px 2px; }}"
            f"QSplitter::handle:hover {{ background-color: {theme.ACCENT}; }}"
        )
        self.cp_body_split.addWidget(left_col)
        self.cp_body_split.addWidget(rail_container)
        self.cp_body_split.setStretchFactor(0, 3)
        self.cp_body_split.setStretchFactor(1, 2)
        # The divider is drawn but NOT draggable, and that is the point rather
        # than a limitation. The rail carries a seven-column table whose minimum
        # is set by the widest real skill name, so it cannot be resized by
        # hand: dragging the handle left does not narrow the rail, it only
        # takes width from the roster while the rail stays put, which reads as
        # the handle being broken. The rail still grows with the WINDOW on its
        # own (stretch 2, capped at 860px) — this only takes the drag away.
        # Disabling the handle rather than collapsing the splitter keeps the
        # divider visible, because the gap is what separates the two panes
        # visually.
        self.cp_body_split.handle(1).setEnabled(False)
        self.cp_body_split.handle(1).setToolTip(
            "The skills rail is sized by its table; widen the window instead")

        lay.addWidget(self.cp_body_split, 1)

        page.consume_pane_back = self._on_combat_pane_back

        old_show = page.showEvent
        def _on_combat_show(ev):
            old_show(ev)
            if getattr(self, "_cp_metric", "damage") != "damage":
                self._set_combat_metric("damage")
        page.showEvent = _on_combat_show

        self._cp_timer = QtCore.QTimer(page)
        self._cp_timer.timeout.connect(self._refresh_combat_page_live)
        self._cp_timer.start(250)

        self._cp_page = page
        # Measured, not remembered: the page's own minimum is the width at which
        # the roster and the rail can both exist, and it moves whenever either
        # of them changes. A hand-kept constant cannot keep up with that, and
        # when it falls behind the page clips instead of hiding. The real
        # measurement happens in _update_combat_rail_for_width, which activates
        # the layout first; the value here is only a placeholder for the window
        # between building the page and the first paint.
        self._cp_rail_min_page_width = CP_RAIL_AUTO_MIN_WIDTH
        self._cp_resize_watch = _RailResizeWatcher(
            page, self._update_combat_rail_for_width)
        self._update_combat_rail_for_width()
        # One more pass once the event loop has laid the page out, because the
        # call above runs before the splitter has any real geometry to report.
        QtCore.QTimer.singleShot(0, self._update_combat_rail_for_width)
        self._sync_history_combo()
        return page

    def _on_combat_pane_back(self) -> bool:
        metric = getattr(self, "_cp_metric", "damage")
        if metric in ("compare", "fights"):
            self._set_combat_metric("damage")
            return True
        return False

    def _set_combat_page_segment(self, segment: str) -> None:
        """Segment chip clicked: select which slice of the run to analyze.

        On the Fights LIST the same row is that list's KIND filter: Boss and
        Trash pick which logs are shown, Auto / Overall / Adds show every kind,
        and the chips mirror the filter actually in force. The row used to be
        meaningless there - a click either closed the list (metric "fights" was
        a whole-encounter view) or, with a past fight picked, was swallowed
        with no feedback at all (live 2026-10-04: "SEGMENT is still buggy with
        the filters ... they dont do anything when i click them"). Off the list
        the chips slice the live run exactly as before.

        An unknown segment (a stale view name, or a caller typo) falls back to
        Auto rather than returning early and leaving the previously picked
        slice silently selected while the chip row claims something else.
        """
        if getattr(self, "_cp_metric", "damage") == "fights":
            self._set_fights_filter(
                _SEGMENT_TO_FIGHTS_KIND.get(segment, "all"))
            return
        # Segments slice the LIVE run: the tracker's boss/trash/adds sessions
        # exist only while it is running, so a chip cannot apply to a frozen
        # archived fight. It USED to solve that by snapping the fight selector
        # back to Live behind the user's back, which threw away the fight they
        # had just picked and read as the dropdown changing on its own (live
        # 2026-10-03: "clicking a SEGMENT changes the drop down to live").
        # The selector is the user's choice and nothing else moves it; instead
        # the chips SAY they do not apply, by being disabled while a past fight
        # is displayed (see _sync_segment_chips).
        if not self._segments_apply_to_shown_session():
            self._sync_segment_chips()
            return
        self._cp_segment = segment if segment in _CP_SEGMENT_ORDER else "auto"
        self._sync_segment_chips()
        if getattr(self, "_cp_metric", "damage") in _CP_WHOLE_ENCOUNTER_METRICS:
            self._set_combat_metric("damage")
        else:
            self._render_combat_breakdown()

    def _segments_apply_to_shown_session(self) -> bool:
        """Whether the segment chips can re-slice what is on screen.

        Only the LIVE run has boss/trash/adds slices to choose between; an
        archived fight is one frozen session.

        This used to be answered from the DISPLAYED session
        (`shown is tracker.session_for(...)`) so the row would grey itself out
        on a past fight. It reported False on the LIVE run in the shipped app,
        which made every chip a no-op - the player could press them and nothing
        happened (reported live 2026-10-03, twice: first "there are disabled",
        then "can click them but nothing happens"). A predicate that can lock
        the main screen is worse than the wart it was meant to remove, so the
        answer is now the one thing that is always true on Live: the fight
        selector is on the Live row. Segments on a past fight simply re-render
        the same frozen fight, which is what they did before any of this.
        """
        return getattr(self, "_selected_history_idx", 0) == 0

    def _sync_segment_chips(self) -> None:
        """Keep the chip visual state in lockstep with ``_cp_segment``.

        The chips are NEVER disabled. Disabling them was my attempt to say
        "segments don't apply to an archived fight", and it went wrong twice:
        first the whole row locked while the live run was on screen (reported
        live 2026-10-03: "now i cant click any SEGMENT there are disabled"),
        and the replacement - asking whether the displayed session is the live
        one - reported False on the live run in the shipped app too. A control
        the player cannot press is a far worse failure than a click that
        quietly does nothing, so the row stays fully live and the limitation
        is stated on hover instead.
        """
        active = getattr(self, "_cp_segment", "auto")
        if getattr(self, "_cp_metric", "damage") == "fights":
            # On the Fights list these chips ARE the kind filter, so they show
            # whichever kind the list is filtered to rather than the live-run
            # slice (which is not on screen at all).
            active = _FIGHTS_KIND_TO_SEGMENT.get(
                getattr(self, "_cp_fights_filter", "all"), "")
        for seg, btn in getattr(self, "cp_segment_chips", {}).items():
            btn.setChecked(seg == active)
        self._refresh_segment_chips_hint()

    def _refresh_segment_chips_hint(self) -> None:
        """Say why a click will not change anything - as a hint, never a lock.

        On an archived fight the segments genuinely have nothing to slice (it is
        one frozen session with no boss/trash/adds split), so the hover says
        so. The chip itself stays pressable, and a click on a fight that
        cannot be sliced leaves the fight picked rather than moving the
        selector (that snap-back was the original reported bug).
        """
        if getattr(self, "_cp_metric", "damage") == "fights":
            tip = ("Filter the archived fights by kind — Boss and Trash pick "
                   "one, Auto / Overall show every kind")
        else:
            applicable = self._segments_apply_to_shown_session()
            tip = ("" if applicable else
                   "Segments split the live run — pick ● Live Encounter to use them")
        for btn in getattr(self, "cp_segment_chips", {}).values():
            btn.setToolTip(tip)

    def _get_active_tracker(self) -> DpsTracker | None:
        """Return the running tracker instance."""
        if hasattr(self, "model") and hasattr(self.model, "dps"):
            return self.model.dps
        if not hasattr(self, "_fallback_dps_tracker"):
            self._fallback_dps_tracker = DpsTracker(getattr(self, "model", None))
        return self._fallback_dps_tracker

    def _reset_combat_session(self):
        self._user_explicitly_chose_live = True
        self._auto_last_fight = False
        self._auto_last_suppressed = True
        self._browse_sessions = None
        self._browse_char = ""
        self._selected_history_idx = 0
        if hasattr(self, "cp_history_combo"):
            self.cp_history_combo.blockSignals(True)
            self.cp_history_combo.setCurrentIndex(0)
            self.cp_history_combo.blockSignals(False)
        self._update_hist_banner(False)
        t = self._get_active_tracker()
        if t:
            # Meter state only: the capture engine keeps running (same zone,
            # same fight context) — DamageSourceManager has no reset, and the
            # scanner must not re-derive mid-dungeon. (A stale damage.reset()
            # call here used to raise AttributeError, aborting the click
            # before the rail + breakdown re-rendered.)
            t.reset(reason="UI manual reset")
        self._reset_combat_rail()
        if hasattr(self, "cp_plotter"):
            self.cp_plotter.clear()
        self._render_combat_breakdown()

    def _set_combat_metric(self, metric: str):
        """Chip handler: switch the class-board ranking metric."""
        self._cp_metric = metric
        for m, b in self.cp_metric_chips.items():
            b.setChecked(m == metric)
        # The SEGMENT row is the Fights list's kind filter while that view is
        # up, so entering or leaving it re-labels the same chips (see
        # `_sync_segment_chips`).
        if callable(getattr(self, "_sync_segment_chips", None)):
            self._sync_segment_chips()
        if hasattr(self, "cp_header_row"):
            self.cp_header_row.set_metric(metric)
        self._render_combat_breakdown()


    def _refresh_combat_page_live(self):
        cur_nav = getattr(self, "_current_page", lambda: "")()
        if "combat" not in str(cur_nav).lower():
            return
        t = self._get_active_tracker()
        if not t:
            return

        self._refresh_combat_mode_badge()
        t.update()
        self._auto_exit_to_live(t)
        self._sync_history_combo()
        self._update_combat_rail_for_width()

        session = self._get_displayed_session()
        if not session:
            return

        # The TICKING timer, so wall_duration: `duration` is the rate
        # denominator and is anchored to the last event (see CombatSession),
        # which would freeze this readout between hits.
        dur = session.wall_duration
        mins = int(dur // 60)
        secs = dur % 60
        self.cp_card_dur.set_value("dur", f"{mins:02d}:{secs:04.1f}")
        if self._selected_history_idx != 0 or self._browse_sessions is not None:
            self.cp_card_dur.set_sub("dur", f"ARCHIVED · {_clean_session_name(session.name)}")
        else:
            self.cp_card_dur.set_sub("dur", f"Status: {session.state} · [{session.name}]")

        grp_dmg, grp_heals, grp_taken = view_totals(
            session, self._cp_solo_filter_on())
        grp_dps = grp_dmg / max(1.0, dur)
        grp_hps = grp_heals / max(1.0, dur)
        self.cp_card_dmg.set_value("dmg", f"{grp_dmg:,.0f}")
        self.cp_card_dmg.set_sub("dmg", f"DPS: {grp_dps:,.1f}/s")

        self.cp_card_heal.set_value("heal", f"{grp_heals:,.0f}")
        self.cp_card_heal.set_sub("heal", f"HPS: {grp_hps:,.1f}/s")

        if grp_taken > 0:
            self.cp_card_taken.set_value("taken", f"{grp_taken:,.0f}")
            self.cp_card_taken.set_sub("taken", "from damage events")
        else:
            self.cp_card_taken.set_value("taken", "0")
            self.cp_card_taken.set_sub("taken", "from damage events")

        t_name = session.target_name.replace("👑 ", "")
        if session.target_max_hp > 0:
            pct = (session.target_hp / session.target_max_hp) * 100.0
            self.cp_card_target.set_value("target", t_name[:16])
            self.cp_card_target.set_sub("target", f"{session.target_hp:,.0f} / {session.target_max_hp:,.0f} ({pct:.0f}%)")
        else:
            self.cp_card_target.set_value("target", t_name[:16])
            if (t_name not in ("None", "") and (self._selected_history_idx != 0 or self._browse_sessions is not None)):
                self.cp_card_target.set_sub("target", "archived · HP not recorded")
            else:
                self.cp_card_target.set_sub("target", "No active target")

        if hasattr(self, "cp_plotter"):
            self.cp_plotter.set_session(session)
            peak_txt = f"⚡ {session.burst_peak:,.0f}/s" if session.burst_peak > 0 else "⚡ --"
            death_txt = f"☠ {len(session.deaths)}" if session.deaths else "☠ 0"
            if hasattr(self, "cp_stat_strip"):
                self.cp_stat_strip.set_value("burst", peak_txt)
                self.cp_stat_strip.set_value("deaths", death_txt)

        self._render_combat_breakdown()

    def _refresh_combat_mode_badge(self):
        """Difficulty badge beside the DPS Analysis title.

        Same question, same answer as the Top DPS HUD and the Run Timer: the
        game's own read inside an instance, the manual fallback when it cannot
        be read, nothing in the open world. Best-effort — a badge must never
        raise into the page tick.
        """
        lbl = getattr(self, "cp_mode_badge", None)
        if lbl is None:
            return
        # An ARCHIVED fight answers for itself: its difficulty was recorded with
        # the run (the log's name carries it), so the badge must not re-derive
        # one from the zone the player happens to be standing in now. Falls
        # through to the live read for the live board.
        archived = self._archived_mode()
        try:
            mode, source = ((archived, "archived") if archived
                            else instance_mode(self.model))
        except Exception:
            mode, source = None, ""
        if not mode:
            lbl.setVisible(False)
            return
        lbl.setText(dungeon_mode_badge(mode))
        lbl.setToolTip(mode_tooltip(mode, source))
        lbl.setVisible(True)

    def _archived_mode(self) -> str:
        """The difficulty recorded with the past fight on screen, or "".

        Only while a past fight IS on screen: the live board's difficulty is
        the live read, and an empty archive (everything written before the
        mode was named) stays unknown rather than borrowing today's.
        """
        showing_past = (getattr(self, "_browse_sessions", None) is not None
                        or getattr(self, "_selected_history_idx", 0) != 0)
        if not showing_past:
            return ""
        try:
            session = self._get_displayed_session()
        except Exception:
            return ""
        mode = getattr(session, "mode", "") if session is not None else ""
        return mode or ""

    def _cp_solo_filter_on(self) -> bool:
        """The Combat & DPS Analysis page is an ALL-PLAYER view by design."""
        return False
