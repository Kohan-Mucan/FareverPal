"""Combat Detail Rail & Skills Inspector Mixin.

Provides the player detail rail, skills breakdown, per-skill info popup,
and multi-player comparison matrices.
"""
from __future__ import annotations

import time
from PySide6 import QtCore, QtWidgets

from .. import theme
from .. import affinity_view
from ..layout import place_popup
from ..skill_row import (SkillRow, AVG_WIDTH, DETAILED_NAME_MAX, HITS_WIDTH,
                         SHARE_WIDTH, TOTAL_WIDTH, sync_subskill_rows)
from ...core.dps_tracker import CombatSession, own_row, view_totals
from ..combat import (
    _ranked_skill_total,
    _cp_card_alive,
    _DamageTypeBar,
    _PdMdLabel,
)
# Re-exported so `from .combat_rail import _SkillWeaponPopup` (the call sites
# and the tests) keeps resolving after the popup moved to its own module.
from .combat_rail_popup import _SkillWeaponPopup  # noqa: F401

def fit_rail_to_page(panel) -> None:
    """Show or hide the detail rail to fit the page it shares with the roster.

    Below the threshold the roster and the rail cannot both exist, and the rail
    is the one that goes. The alternative is not "the rail gets smaller" — its
    minimum is set by the widest skill name and the widest cell in the table.
    The alternative is that the page overflows, and the Combat page is inserted
    straight into the QStackedWidget (ControlPanel._select_nav does not wrap it
    in a scroll area), so the overflow is clipped at the page's own edge: no
    scrollbar, no warning, the tail of the skills table and the DMG TYPE bar
    above it just stopping mid-number.

    The threshold is summed from the two panes, NOT read off the page.
    ``page.minimumSizeHint()`` is the obvious thing to measure and it is wrong
    here: a hidden widget contributes nothing to a layout's minimum, so hiding
    the rail shrinks the page's own minimum, which then argues for showing the
    rail again at a width it does not fit. Dragging the window across that
    boundary makes the rail stutter on and off. The panes are the real inputs
    and they do not care what the other one is doing.
    """
    page = getattr(panel, "_cp_page", None)
    rail_container = getattr(panel, "_cp_rail_container", None)
    split = getattr(panel, "cp_body_split", None)
    rail_box = getattr(panel, "cp_rail_box", None)
    if page is None or rail_container is None or split is None \
            or rail_box is None:
        return
    from .combat_page import CP_RAIL_AUTO_MIN_WIDTH
    left_col = split.widget(0)
    for lay in (left_col.layout(), page.layout()):
        if lay is not None:
            lay.activate()
    margins = page.layout().contentsMargins() if page.layout() else None
    pad = (margins.left() + margins.right()) if margins else 0
    need = max(
        CP_RAIL_AUTO_MIN_WIDTH,
        left_col.minimumSizeHint().width() + rail_box.minimumWidth()
        + split.handleWidth() + pad)
    panel._cp_rail_min_page_width = need
    if page.width() < need:
        if not rail_container.isHidden():
            rail_container.hide()
    elif rail_container.isHidden():
        rail_container.show()


def sync_table_minimum_width(panel) -> None:
    """Make the scroll area scroll rather than clip, whatever happens.

    A row is a plain QHBoxLayout of fixed-width cells, so a row's minimum is a
    real number and the rows can be wider than the viewport. With
    ``widgetResizable(True)`` and no minimum on the container, the container was
    stretched to whatever the viewport happened to be and the tail of the row
    was simply clipped at the edge — which is what "the Avg Hit column is cut
    off" looks like from the outside, and it is a silent failure: no scrollbar,
    no warning, just a number that stops halfway.

    Pinning the container's minimum to the widest row's minimum turns that into
    a horizontal scrollbar, which is visible and recoverable. The rail's own
    minimum means this never comes up in normal use — it is the backstop for
    the case where it does, which is the whole reason it exists: the layout was
    wrong in a way that no test could see, because the tests set a width
    directly instead of letting something narrower happen to it.
    """
    container = getattr(panel, "cp_skills_container", None)
    scroll = getattr(panel, "cp_skills_scroll", None)
    if container is None or scroll is None:
        return
    widest = 0
    for row in getattr(panel, "_cp_rail_skill_widgets", {}).values():
        if not row.isHidden():
            widest = max(widest, row._layout.minimumSize().width())
    container.setMinimumWidth(widest)
    scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)


def update_type_column_for_width(panel) -> None:
    """Show the TYPE column only when the table can carry it and the name.

    The skill name is the one thing in this table that has no other home — a
    type is still in the row tooltip, a squeezed name is gone. The rail's
    width is whatever the window and the splitter leave it, so "too narrow"
    is a state this genuinely has to handle, not a hypothetical.
    """
    hdr = getattr(panel, "cp_skills_type_hdr", None)
    if hdr is None:
        return
    scroll = getattr(panel, "cp_skills_scroll", None)
    show = (scroll is not None
            and scroll.viewport().width() >= affinity_view.TYPE_COLUMN_MIN_TABLE)
    if getattr(panel, "_cp_type_col_shown", None) is show:
        return
    panel._cp_type_col_shown = show
    hdr.setVisible(show)
    for row in getattr(panel, "_cp_rail_skill_widgets", {}).values():
        if row._type_lbl is not None:
            row._type_lbl.setVisible(show)
    # Dropping the column gives its width back to the name, so the row's
    # minimum moves with it.
    sync_table_minimum_width(panel)


class CombatRailMixin:
    """Skills inspector rail, weapon popup, player compare, and rail sizing."""

    def _build_combat_rail(self) -> QtWidgets.QWidget:
        rail = QtWidgets.QVBoxLayout()
        rail.setSpacing(6)
        rail.setContentsMargins(0, 0, 0, 0)

        self.cp_rail_box = QtWidgets.QWidget()
        # The rail takes the width the page has left over after the roster,
        # rather than stopping at a cap and leaving dead space to its right
        # (the header above it — Fights, the segment chips — already runs to
        # the page edge).
        #
        # The minimum is the width at which the table can carry the TYPE
        # column AND a skill name that is still a name. It was 570, and that
        # number is what made this table look broken: the splitter pins the
        # rail to its minimum on any window up to about 1400px wide, the fixed
        # cells took 432px of the 558 that was left, and the name column was
        # left 126px — so "Resilience of the Unkillable Demon King" rendered
        # as "Resilience of the…".
        #
        # 690 is the arithmetic: 12px of frame + 476px of fixed cells and gaps
        # + 202px, the widest of the 164 real skill names in this player's own
        # archives once they are capped at NAME_MAX_CHARS. Without the cap the
        # same sum is 719, and the cap exists precisely so that one
        # 39-character passive name does not set the width of the whole rail —
        # the roster has to live in the rest of the page, and every pixel the
        # longest name claims is a pixel the player list does not get.
        self.cp_rail_box.setMinimumWidth(690)
        self.cp_rail_box.setMaximumWidth(860)
        r_lay = QtWidgets.QVBoxLayout(self.cp_rail_box)
        r_lay.setContentsMargins(0, 0, 0, 0)
        r_lay.setSpacing(4)

        # SKILLS INSPECTOR PANEL (Two distinct cards, aligned flush to top)
        self.cp_rail_panel_skills = QtWidgets.QWidget()
        sp_lay = QtWidgets.QVBoxLayout(self.cp_rail_panel_skills)
        sp_lay.setContentsMargins(0, 0, 0, 0)
        sp_lay.setSpacing(6)

        # 1. Top Card: Player Summary & Stats
        self.cp_hero_box = QtWidgets.QFrame()
        self.cp_hero_box.setStyleSheet(
            "QFrame { background-color: transparent; border: none; }"
        )
        hb_lay = QtWidgets.QVBoxLayout(self.cp_hero_box)
        hb_lay.setContentsMargins(8, 4, 8, 4)
        hb_lay.setSpacing(6)

        # Hidden dummy cp_hero_name to preserve property callbacks
        self.cp_hero_name = QtWidgets.QLabel("")
        self.cp_hero_name.hide()

        # Top Action & Badge Row (Copy + Compare on Left | Rank/Class Badge on Right)
        hb_top_bar = QtWidgets.QHBoxLayout()
        hb_top_bar.setContentsMargins(0, 0, 0, 0)
        hb_top_bar.setSpacing(6)

        copy_btn = QtWidgets.QPushButton("📋 Copy")
        copy_btn.setStyleSheet(
            f"QPushButton {{ padding: 3px 8px; font-weight: 700; font-size: 10px; background: {theme.SURFACE}; "
            f"color: {theme.TEXT}; border-radius: 4px; border: 1px solid {theme.BORDER}; }}"
            f"QPushButton:hover {{ border-color: {theme.ACCENT}; color: {theme.ACCENT_LIGHT}; }}"
        )
        copy_btn.setCursor(QtCore.Qt.PointingHandCursor)
        copy_btn.clicked.connect(self._copy_selected_player_parse)
        hb_top_bar.addWidget(copy_btn)

        self.cp_hero_compare_btn = QtWidgets.QPushButton("⚖ Compare with You")
        self.cp_hero_compare_btn.setStyleSheet(
            f"QPushButton {{ padding: 3px 8px; font-weight: 700; font-size: 10px; background: {theme.SURFACE}; "
            f"color: {theme.TEXT}; border-radius: 4px; border: 1px solid {theme.BORDER}; }}"
            f"QPushButton:hover {{ border-color: {theme.GOLD}; color: {theme.GOLD}; background: {theme.PANEL_LOW}; }}"
        )
        self.cp_hero_compare_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self.cp_hero_compare_btn.clicked.connect(self._on_hero_compare_clicked)
        hb_top_bar.addWidget(self.cp_hero_compare_btn)

        hb_top_bar.addStretch(1)

        self.cp_hero_badge = QtWidgets.QLabel("")
        self.cp_hero_badge.setStyleSheet(
            f"font-size: 9px; font-weight: 800; color: {theme.GOLD}; "
            f"background: {theme.PANEL_LOW}; border: 1px solid {theme.GOLD}; border-radius: 3px; padding: 2px 6px;"
        )
        hb_top_bar.addWidget(self.cp_hero_badge)
        hb_lay.addLayout(hb_top_bar)

        # Overall Hero Stats (borderless columns with dividers — no nested boxes)
        def _hero_stat(caption: str, color: str) -> tuple[QtWidgets.QWidget, QtWidgets.QLabel, QtWidgets.QLabel]:
            box = QtWidgets.QVBoxLayout()
            box.setContentsMargins(0, 0, 0, 0)
            box.setSpacing(1)
            cap = QtWidgets.QLabel(caption.upper())
            cap.setAlignment(QtCore.Qt.AlignCenter)
            cap.setStyleSheet(f"font-size: 9.5px; font-weight: 800; color: {theme.DIM}; letter-spacing: 0.5px;")
            val = QtWidgets.QLabel("--")
            val.setAlignment(QtCore.Qt.AlignCenter)
            val.setStyleSheet(f"font-size: 13.5px; font-weight: 800; color: {color}; font-family: monospace;")
            box.addWidget(cap)
            box.addWidget(val)
            holder = QtWidgets.QWidget()
            holder.setLayout(box)
            return holder, val, cap

        hb_stats = QtWidgets.QHBoxLayout()
        hb_stats.setSpacing(8)
        w_dps, self.cp_hero_dps, self.cp_hero_dps_cap = _hero_stat("DPS", theme.ACCENT_LIGHT)
        w_tot, self.cp_hero_tot, self.cp_hero_tot_cap = _hero_stat("Total Dmg", theme.TEXT)
        w_shr, self.cp_hero_shr, self.cp_hero_shr_cap = _hero_stat("Raid Share", theme.GOLD)
        w_crt, self.cp_hero_crt, self.cp_hero_crt_cap = _hero_stat("Crit Rate", theme.ORANGE)
        hb_stats.addWidget(w_dps, 1)
        hb_stats.addWidget(w_tot, 1)
        hb_stats.addWidget(w_shr, 1)
        hb_stats.addWidget(w_crt, 1)
        hb_lay.addLayout(hb_stats)

        # Damage by type: the game's own per-hit affinity, aggregated over the
        # selected player's skills, drawn as a proportional bar. The caption
        # matches the hero stats above it; the bar carries the split itself.
        # The whole row is hidden when the capture has no types (an older
        # bridge, or a patch that renamed the field) — an empty bar says less
        # than no bar.
        self.cp_dmg_type_row = QtWidgets.QWidget()
        dmg_type_row = QtWidgets.QHBoxLayout(self.cp_dmg_type_row)
        dmg_type_row.setContentsMargins(0, 0, 0, 0)
        dmg_type_row.setSpacing(6)
        self.cp_dmg_type_cap = QtWidgets.QLabel("DMG TYPE")
        self.cp_dmg_type_cap.setStyleSheet(
            f"font-size: 9px; font-weight: 800; color: {theme.DIM}; "
            f"letter-spacing: 0.5px; background: transparent;")
        dmg_type_row.addWidget(self.cp_dmg_type_cap)
        self.cp_dmg_type_bar = _DamageTypeBar()
        dmg_type_row.addWidget(self.cp_dmg_type_bar, 1)
        # The merged PD / MD pair (Physical+Raw vs every other tagged
        # school — the same merge pd_md_split draws on the Test Dummy HUD),
        # school — the same merge pd_md_split draws on the Test Dummy HUD),
        # so the two numbers most players read are text beside the bar
        # without needing a hover. Hidden with the row by default.
        self.cp_dmg_type_pdmd = _PdMdLabel()
        self.cp_dmg_type_pdmd.setTextFormat(QtCore.Qt.RichText)
        self.cp_dmg_type_pdmd.setStyleSheet(
            f"font-size: 10.5px; font-weight: 700; font-family: "
            f"\"{theme.MONO_FONT}\", monospace; color: {theme.DIM}; "
            f"background: transparent; border: none;")
        dmg_type_row.addWidget(self.cp_dmg_type_pdmd)
        self.cp_dmg_type_row.setVisible(False)
        hb_lay.addWidget(self.cp_dmg_type_row)

        sp_lay.addWidget(self.cp_hero_box)

        # 2. Bottom Card: Skills Breakdown Table
        self.cp_skills_box = QtWidgets.QFrame()
        self.cp_skills_box.setStyleSheet(
            "QFrame { background-color: transparent; border: none; }"
        )
        sk_box_lay = QtWidgets.QVBoxLayout(self.cp_skills_box)
        sk_box_lay.setContentsMargins(6, 4, 6, 4)
        sk_box_lay.setSpacing(4)

        tbl_hdr = QtWidgets.QHBoxLayout()
        tbl_hdr.setContentsMargins(0, 2, 0, 2)
        tbl_hdr.setSpacing(6)

        ico_spacer = QtWidgets.QLabel("")
        ico_spacer.setFixedWidth(20)
        tbl_hdr.addWidget(ico_spacer)

        th_skill = QtWidgets.QLabel("SKILL")
        th_skill.setMaximumWidth(DETAILED_NAME_MAX)
        th_skill.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
        th_skill.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        th_skill.setStyleSheet(f"font-size: 11.5px; font-weight: 800; color: {theme.DIM}; letter-spacing: 0.5px;")
        tbl_hdr.addWidget(th_skill, 1)

        # The damage-type column: each row names the school its skill deals.
        # One column, because a weapon deals one school and the TOTAL beside it
        # already carries the number.
        th_type = QtWidgets.QLabel(affinity_view.TYPE_HEADER)
        th_type.setFixedWidth(affinity_view.TYPE_COLUMN_WIDTH)
        th_type.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        th_type.setStyleSheet(
            f"font-size: 11px; font-weight: 800; color: {theme.DIM};")
        th_type.setToolTip("The damage type this skill deals")
        tbl_hdr.addWidget(th_type)
        self.cp_skills_type_hdr = th_type

        th_tot = QtWidgets.QLabel("TOTAL")
        th_tot.setFixedWidth(TOTAL_WIDTH)
        th_tot.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        th_tot.setStyleSheet(f"font-size: 11.5px; font-weight: 800; color: {theme.DIM};")
        tbl_hdr.addWidget(th_tot)

        th_bar = QtWidgets.QLabel("%")
        th_bar.setFixedWidth(SHARE_WIDTH)
        th_bar.setAlignment(QtCore.Qt.AlignCenter)
        th_bar.setStyleSheet(f"font-size: 11.5px; font-weight: 800; color: {theme.DIM};")
        tbl_hdr.addWidget(th_bar)

        th_hits = QtWidgets.QLabel("HITS")
        th_hits.setFixedWidth(HITS_WIDTH)
        th_hits.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        th_hits.setStyleSheet(f"font-size: 11.5px; font-weight: 800; color: {theme.DIM};")
        tbl_hdr.addWidget(th_hits)

        th_crit = QtWidgets.QLabel("CRIT%")
        th_crit.setFixedWidth(38)
        th_crit.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        th_crit.setStyleSheet(f"font-size: 11px; font-weight: 800; color: {theme.DIM};")
        tbl_hdr.addWidget(th_crit)

        th_avg = QtWidgets.QLabel("Avg Hit")
        th_avg.setFixedWidth(AVG_WIDTH)
        th_avg.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        th_avg.setStyleSheet(f"font-size: 11px; font-weight: 800; color: {theme.DIM};")
        tbl_hdr.addWidget(th_avg)

        sk_box_lay.addLayout(tbl_hdr)

        self.cp_skills_scroll = QtWidgets.QScrollArea()
        self.cp_skills_scroll.setWidgetResizable(True)
        self.cp_skills_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.cp_skills_scroll.setStyleSheet("background: transparent;")
        self.cp_skills_container = QtWidgets.QWidget()
        self.cp_skills_rows_lay = QtWidgets.QVBoxLayout(self.cp_skills_container)
        self.cp_skills_rows_lay.setContentsMargins(0, 0, 0, 0)
        self.cp_skills_rows_lay.setSpacing(5)
        self.cp_skills_rows_lay.addStretch(1)
        self.cp_skills_scroll.setWidget(self.cp_skills_container)
        sk_box_lay.addWidget(self.cp_skills_scroll, 1)
        sp_lay.addWidget(self.cp_skills_box, 1)

        r_lay.addWidget(self.cp_rail_panel_skills, 1)

        rail.addWidget(self.cp_rail_box)
        rail_container = QtWidgets.QWidget()
        rail_container.setLayout(rail)
        self._cp_rail_container = rail_container
        # Watch the TABLE, not the page: the splitter that sizes the rail does
        # not resize the page, so a drag would otherwise leave the column
        # showing in a name it no longer fits.
        from .combat_page import _RailResizeWatcher
        self._cp_type_width_watch = _RailResizeWatcher(
            self.cp_skills_box, self._update_type_column_for_width)
        self._update_type_column_for_width()
        return rail_container

    def _sync_table_minimum_width(self) -> None:
        """Width rule: see ``sync_table_minimum_width`` above."""
        sync_table_minimum_width(self)

    def _update_type_column_for_width(self) -> None:
        """Width rule: see ``update_type_column_for_width`` above."""
        update_type_column_for_width(self)

    # --- compare matrix: see combat_compare.py (CombatCompareMixin) -----------

    def _reset_combat_rail(self):
        self._cp_selected_player = ""
        self._cp_compare_a = ""
        self._cp_compare_b = ""
        self._cp_compare_c = ""
        self._cp_compare_d = ""
        self._cp_compare_e = ""
        for attr in ("cp_hero_dps", "cp_hero_tot", "cp_hero_shr", "cp_hero_crt"):
            if hasattr(self, attr):
                getattr(self, attr).setText("--")
        if hasattr(self, "cp_hero_name"):
            self.cp_hero_name.setText("")
        if hasattr(self, "cp_hero_badge"):
            self.cp_hero_badge.setText("")
        # The damage-by-type bar is hero-box state too: a reset must not leave
        # the previous player's split drawn until the next refresh lands.
        self._clear_damage_type()
        for row in getattr(self, "_cp_rail_skill_widgets", {}).values():
            try:
                row.hide()
            except RuntimeError:
                pass

    def _update_combat_rail_for_width(self) -> None:
        """Show or hide the detail rail to fit the page.

        Delegate — see ``fit_rail_to_page`` above, which owns every
        width rule the rail obeys."""
        fit_rail_to_page(self)

    def _update_combat_rail(self, session) -> None:
        """Fill the detail rail from the displayed session."""
        if not hasattr(self, "cp_rail_boss_box"):
            return
        t_name = (session.target_name or "None").replace("👑 ", "").replace("🎯 ", "")
        is_boss = bool(session.target_name and "👑" in session.target_name)
        self.cp_rail_boss_nm.setText(("👑 " if is_boss else "🎯 ") + t_name)
        if session.target_max_hp > 0:
            pct = max(0.0, min(100.0, (session.target_hp / session.target_max_hp) * 100.0))
            self.cp_rail_boss_hp.setText(
                f"{session.target_hp:,.0f} / {session.target_max_hp:,.0f} · {pct:.1f}%")
            self.cp_rail_boss_bar.setValue(int(pct))
            self.cp_rail_boss_box.setVisible(True)
        else:
            self.cp_rail_boss_box.setVisible(False)

        if session.burst_peak > 0:
            burst_txt = f"Peak burst: {session.burst_peak:,.0f}/s"
        else:
            burst_txt = "Peak burst: --"
        self.cp_rail_burst_lbl.setText(burst_txt)
        if session.deaths:
            lines = []
            for off, name in list(session.deaths)[-4:]:
                mm = int(off // 60)
                ss = off % 60
                lines.append(f"☠ {mm:02d}:{ss:04.1f}  {name}")
            self.cp_rail_deaths_lbl.setText("\n".join(lines))
            self.cp_rail_deaths_lbl.setVisible(True)
        else:
            self.cp_rail_deaths_lbl.setText("No deaths")
            self.cp_rail_deaths_lbl.setVisible(False)

        me = own_row(session)
        me = me or (next(iter(session.players.values()), None))
        if me:
            sk = _ranked_skill_total(me)
            if sk:
                s0 = sk[0]
                uses = s0.hit_count + s0.heal_count
                self.cp_rail_top_lbl.setText(s0.name)
                if s0.damage > 0 and s0.heals > 0:
                    sub = f"D {s0.damage:,.0f} · H {s0.heals:,.0f} · {uses} uses"
                else:
                    sub = f"{s0.total:,.0f} · {uses} uses"
                self.cp_rail_top_sub.setText(sub)
            else:
                self.cp_rail_top_lbl.setText("--")
                self.cp_rail_top_sub.setText("no skills yet")
        else:
            self.cp_rail_top_lbl.setText("--")
            self.cp_rail_top_sub.setText("no players yet")

        allies = [p for p in session.players.values() if not p.is_me]
        if not allies:
            self.cp_rail_ally_lbl.setText("No other players in this session's parse.")
        else:
            decoded = [p for p in allies if p.total_damage > 0 or p.heals > 0]
            with_dmg = sum(1 for p in decoded if p.total_damage > 0)
            with_heal = sum(1 for p in decoded if p.heals > 0)
            if decoded:
                self.cp_rail_ally_lbl.setText(
                    f"{len(decoded)} of {len(allies)} allies decoded hits ({with_dmg} dmg, {with_heal} heal).")
            else:
                self.cp_rail_ally_lbl.setText(
                    f"Reader is LIVE but {len(allies)} nearby allies sent no floaties — server delivery limit, not a reader fault.")

    def _clear_damage_type(self) -> None:
        """Hide the damage-by-type bar AND drop its segments.

        Clearing matters as much as hiding: a stale split left behind the hide
        is visible to whatever reads the bar's own state, and showing the row
        again would otherwise repaint the previous player's breakdown.
        """
        bar = getattr(self, "cp_dmg_type_bar", None)
        row = getattr(self, "cp_dmg_type_row", None)
        if bar is not None:
            bar.clear()
        pdmd = getattr(self, "cp_dmg_type_pdmd", None)
        if pdmd is not None:
            pdmd.clear()
        if row is not None:
            row.setVisible(False)

    def _refresh_damage_type(self, p, metric: str) -> None:
        """Draw the selected player's damage-by-type bar in the hero box.

        Damage only: the healing/taken tabs show a different channel, so a
        damage breakdown there would read as if it belonged to those numbers.
        The PD / MD labels beside the bar read the SAME split merged to two
        numbers (Physical+Raw vs everything else tagged), matching the Test
        Dummy HUD's line; the hover still carries the per-school table.
        """
        bar = getattr(self, "cp_dmg_type_bar", None)
        row = getattr(self, "cp_dmg_type_row", None)
        pdmd = getattr(self, "cp_dmg_type_pdmd", None)
        if bar is None or row is None:
            return
        if metric in ("healing", "taken", "damage_taken"):
            self._clear_damage_type()
            return
        rows = affinity_view.player_rows(p)
        bar.set_rows(rows)
        if pdmd is not None:
            pdmd.set_player(p)
        row.setVisible(bool(rows))

    def _on_skill_popup_closed(self, skill_id: str) -> None:
        if skill_id:
            self._last_closed_skill_id = skill_id
            self._last_closed_skill_ts = time.monotonic()
        if getattr(self, "_cp_skill_popup", None) is not None:
            self._cp_skill_popup = None

    def _open_skill_popup(self, sp, row) -> None:
        """Open the skill-info popup under a skills-rail row."""
        if not sp or not getattr(sp, "skill_id", None):
            return

        sid = sp.skill_id
        now = time.monotonic()

        cur_pop = getattr(self, "_cp_skill_popup", None)
        if cur_pop is not None and getattr(cur_pop, "skill_id", None) == sid:
            self._last_closed_skill_id = sid
            self._last_closed_skill_ts = now
            try:
                cur_pop.close()
                cur_pop.deleteLater()
            except RuntimeError:
                pass
            self._cp_skill_popup = None
            return

        last_closed_id = getattr(self, "_last_closed_skill_id", None)
        last_closed_ts = getattr(self, "_last_closed_skill_ts", 0.0)
        if last_closed_id == sid and (now - last_closed_ts) < 1.0:
            return

        if cur_pop is not None:
            try:
                cur_pop.close()
                cur_pop.deleteLater()
            except RuntimeError:
                pass
            self._cp_skill_popup = None

        pop = _SkillWeaponPopup(sp, self)
        pop.adjustSize()
        self._cp_skill_popup = pop
        pos = row.mapToGlobal(QtCore.QPoint(0, row.height() + 4))
        place_popup(pop, pos)

    def _on_player_card_clicked(self, name: str) -> None:
        """Card click selects that player for the (skills-only) detail rail."""
        self._cp_selected_player = name
        for card in getattr(self, "_cp_player_cards", {}).values():
            if _cp_card_alive(card):
                card.set_selected(card.name == name)
        session = self._get_displayed_session()
        if session:
            self._update_rail_content(session, max(1.0, session.duration))

    # --- compare selection: see combat_compare.py (CombatCompareMixin) --------

    def _update_rail_content(self, session: CombatSession, duration: float) -> None:
        if not session or not session.players:
            return
        if not getattr(self, "_cp_selected_player", "") or self._cp_selected_player not in session.players:
            me = own_row(session)
            self._cp_selected_player = me.name if me else next(iter(session.players.keys()))

        self._refresh_rail_skills(
            session, duration, getattr(self, "_cp_metric", "damage"))

    def _place_rail_row(self, row, idx: int) -> None:
        """Move a skill row to slot ``idx``, if it is not already there.

        The rail is ordered by insertion, not by a sort: each row is placed at
        its own rank as the list is walked. Shared by the parent rows and the
        nested sub-skill rows so both are positioned by the same rule — and so
        a nested row cannot land above the skill it belongs to.
        """
        if self.cp_skills_rows_lay.indexOf(row) != idx:
            self.cp_skills_rows_lay.insertWidget(idx, row)

    def _refresh_rail_skills(self, session: CombatSession, duration: float, metric: str) -> None:
        p = session.players.get(getattr(self, "_cp_selected_player", ""))
        if not p:
            p = own_row(session) or next(iter(session.players.values()))
            self._cp_selected_player = p.name

        rank = session.get_rank(p, view=metric)
        base_dmg, base_heals, base_taken = view_totals(
            session, self._cp_solo_filter_on())
        col = theme.class_color(p.hero_class)
        clean_name = p.name.replace(" (You)", "").replace(" (YOU)", "")
        self.cp_hero_name.setText(clean_name)
        self.cp_hero_name.setStyleSheet(f"font-size: 13.5px; font-weight: 800; color: {theme.ACCENT_LIGHT if p.is_me else theme.TEXT};")

        self.cp_hero_badge.setText(f"#{rank} · {p.hero_class or 'Hero'}")
        self.cp_hero_badge.setStyleSheet(
            f"font-size: 9px; font-weight: 800; color: {col}; "
            f"background: {theme.PANEL_LOW}; border: 1px solid {col}; border-radius: 3px; padding: 2px 6px;")

        if metric == "healing":
            rate_val = p.hps(duration)
            rate_txt = f"{rate_val:,.0f}/s" if rate_val < 100_000 else f"{rate_val/1000:.1f}k/s"
            total_val = p.heals
            tot_txt = f"{total_val:,.0f}" if total_val < 10_000_000 else f"{total_val/1_000_000:.2f}M"
            share = p.heal_share_pct(base_heals)
            if hasattr(self, "cp_hero_dps_cap"):
                self.cp_hero_dps_cap.setText("HPS")
                self.cp_hero_tot_cap.setText("TOTAL HEAL")
                self.cp_hero_shr_cap.setText("HEAL SHARE")
            self.cp_hero_dps.setStyleSheet(f"font-size: 13.5px; font-weight: 800; color: {theme.GOOD}; font-family: monospace;")
        elif metric in ("taken", "damage_taken"):
            group_taken = base_taken
            rate_val = p.total_damage_taken / max(1.0, duration)
            rate_txt = f"{rate_val:,.0f}/s" if rate_val < 100_000 else f"{rate_val/1000:.1f}k/s"
            total_val = p.total_damage_taken
            tot_txt = f"{total_val:,.0f}" if total_val < 10_000_000 else f"{total_val/1_000_000:.2f}M"
            share = (p.total_damage_taken / max(1.0, group_taken)) * 100.0 if group_taken > 0 else 0.0
            if hasattr(self, "cp_hero_dps_cap"):
                self.cp_hero_dps_cap.setText("DTPS")
                self.cp_hero_tot_cap.setText("TOTAL TAKEN")
                self.cp_hero_shr_cap.setText("TAKEN SHARE")
            self.cp_hero_dps.setStyleSheet(f"font-size: 13.5px; font-weight: 800; color: {theme.ORANGE}; font-family: monospace;")
        else:
            rate_val = p.dps(duration)
            rate_txt = f"{rate_val:,.0f}/s" if rate_val < 100_000 else f"{rate_val/1000:.1f}k/s"
            total_val = p.total_damage
            tot_txt = f"{total_val:,.0f}" if total_val < 10_000_000 else f"{total_val/1_000_000:.2f}M"
            share = p.share_pct(base_dmg)
            if hasattr(self, "cp_hero_dps_cap"):
                self.cp_hero_dps_cap.setText("DPS")
                self.cp_hero_tot_cap.setText("TOTAL DMG")
                self.cp_hero_shr_cap.setText("RAID SHARE")
            self.cp_hero_dps.setStyleSheet(f"font-size: 13.5px; font-weight: 800; color: {theme.ACCENT_LIGHT}; font-family: monospace;")

        self.cp_hero_dps.setText(rate_txt)
        self.cp_hero_tot.setText(tot_txt)
        self.cp_hero_shr.setText(f"{share:.1f}%")

        total_crits = sum(s.crit_count for s in p.skills.values())
        total_hits = sum(s.hit_count for s in p.skills.values())
        crit_pct = (total_crits / total_hits * 100.0) if total_hits > 0 else 0.0
        self.cp_hero_crt.setText(f"{crit_pct:.1f}%")

        if metric == "healing":
            channel_skills = p.ranked_heal_skills()
            skill_total = p.heals
            mode = "healing"
        elif metric == "skills":
            channel_skills = _ranked_skill_total(p)
            skill_total = max(p.total_damage, p.heals)
            mode = "both"
        else:
            channel_skills = p.ranked_skills()
            skill_total = p.total_damage
            mode = "damage"

        def _sk_total(sp):
            return getattr(sp, "heals" if metric == "healing" else "total", getattr(sp, "damage", 0.0))
        channel_skills = sorted(channel_skills, key=_sk_total, reverse=True)

        # `skill_total` still drives the per-row shares below; the rail's own
        # stacked bar is the damage-by-type one (see _refresh_damage_type).
        self._refresh_damage_type(p, metric)
        active_ids = set()
        slot = 0
        for sp in channel_skills:
            active_ids.add(sp.skill_id)
            s_pct = sp.share_pct(skill_total) if metric != "healing" else sp.heal_share_pct(skill_total)
            row = self._cp_rail_skill_widgets.get(sp.skill_id)
            if row is None:
                row = SkillRow(density="detailed", parent=self.cp_skills_container)
                row.set_skill(sp.skill_id, sp.name)
                self._cp_rail_skill_widgets[sp.skill_id] = row
            self._place_rail_row(row, slot)
            if row.isHidden():
                row.show()
            row.update(sp, share_pct=s_pct, mode=mode)
            # The share colour is NOT set here. update() already read this
            # skill's damage type and coloured the % to match its TYPE cell;
            # setting it again from the skill id here would overwrite that with
            # the skill's own hue, which is what this column used to do.
            row.set_on_skill_click(
                lambda _sid, _r, sp=sp: self._open_skill_popup(sp, _r))
            slot += 1
            # This skill's OWN derived rows, folded into it above (the bleed
            # a Bonethrow applies), nested beneath it by the same shared
            # helper the DPS meter uses so both surfaces group these alike.
            # `subskills` is the damage channel, so heal mode draws none.
            for key in sync_subskill_rows(
                    sp, self._cp_rail_skill_widgets,
                    make_row=lambda _p: SkillRow(
                        density="detailed", parent=self.cp_skills_container),
                    mode=mode, total_base=skill_total,
                    on_click=self._open_skill_popup):
                active_ids.add(key)
                self._place_rail_row(self._cp_rail_skill_widgets[key], slot)
                slot += 1

        for sid, row in self._cp_rail_skill_widgets.items():
            if sid not in active_ids:
                row.hide()

        # New rows were built without knowing the table's width, so the column
        # state is re-applied after them, not only on resize.
        self._cp_type_col_shown = None
        self._update_type_column_for_width()
        self._sync_table_minimum_width()

        if p.is_me:
            top_ranked = session.ranked_players(view="damage") if session else []
            top1 = top_ranked[0] if top_ranked else None
            if top1 and top1.name != p.name:
                self.cp_hero_compare_btn.setText(f"⚖ Compare with #1 ({top1.name})")
            else:
                top2 = top_ranked[1] if len(top_ranked) > 1 else None
                top2_nm = top2.name if top2 else "#2"
                self.cp_hero_compare_btn.setText(f"⚖ Compare with #2 ({top2_nm})")
        else:
            self.cp_hero_compare_btn.setText(f"⚖ Compare with You (vs {p.name})")
