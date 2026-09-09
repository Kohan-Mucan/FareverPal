"""Head-to-head combat parse comparator with aligned skill breakdown.

Computes DPS deltas, damage shares, crit differentials, and renders an aligned
side-by-side skill comparison matrix comparing Player A and Player B skill by skill.

Under that matrix sits one more table of the same shape: the damage-by-type
split for the same two players (``_CmpTypeTable``). It is the same
``affinity_view`` breakdown the Combat rail draws as a bar, so a fight that
shows a type split on the rail shows it here too - including an archived fight,
which reaches this file as an ordinary ``CombatSession``.
"""
from __future__ import annotations

from PySide6 import QtCore, QtWidgets

from .. import affinity_view, theme
from ..memory_reclaimer import _forget_attrs
from ..skill_row import _skill_icon, tagged_skill_name
from ...core.dps_tracker import CombatSession, own_row
from .player_card import _ranked_skill_total


def _alive(w) -> bool:
    """True when a cached widget ref still points at a live Qt object.

    Page eviction deleteLater()s the whole combat tree while attributes on
    the page object keep pointing at the dead C++ objects; touching them
    raises "Internal C++ object already deleted".
    """
    if w is None:
        return False
    try:
        w.isHidden()
        return True
    except RuntimeError:
        return False


def _get_combo_player_name(cb) -> str:
    if not cb:
        return ""
    data = cb.currentData()
    if data and isinstance(data, str) and data not in ("(None)", "--", ""):
        return data
    txt = cb.currentText().strip()
    if not txt or txt in ("(None)", "--"):
        return ""
    return txt.split(" (")[0].strip()


# --- damage by type, side by side -------------------------------------------

def _cmp_value_label(color: str) -> QtWidgets.QLabel:
    lbl = QtWidgets.QLabel("")
    lbl.setMinimumWidth(58)
    lbl.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
    lbl.setStyleSheet(
        f"font-size: 11px; font-family: monospace; font-weight: 800; color: {color};")
    return lbl


def _cmp_share_bar(color: str) -> QtWidgets.QProgressBar:
    bar = QtWidgets.QProgressBar()
    bar.setFixedWidth(44)
    bar.setFixedHeight(5)
    bar.setTextVisible(False)
    bar.setRange(0, 100)
    bar.setStyleSheet(
        f"QProgressBar {{ background: {theme.SURFACE}; border: none; border-radius: 2px; }}"
        f"QProgressBar::chunk {{ background: {color}; border-radius: 2px; }}")
    return bar


def _cmp_pct_label() -> QtWidgets.QLabel:
    lbl = QtWidgets.QLabel("")
    lbl.setFixedWidth(38)
    lbl.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
    lbl.setStyleSheet(f"font-size: 10.5px; font-family: monospace; color: {theme.DIM};")
    return lbl


def _fmt_amount(v: float) -> str:
    return f"{v / 1_000_000:.2f}M" if v >= 1_000_000 else f"{v:,.0f}"


class _CmpTypeTable(QtWidgets.QWidget):
    """A-vs-B damage-by-type rows, on the compare matrix's own columns.

    Built from the same cells as a matrix row - amount, 44 px share bar and
    percent per side with a centred delta between them - and given the same
    column shapes, so it reads as another section of that table rather than a
    chart bolted underneath it.

    `set_rows` is called with the pair the matrix is showing, so the two always
    describe the same fight.

    The shares come from ``affinity_view.compare_type_rows``: each side's
    percentage is against its OWN total, so the two columns stay comparable
    instead of both being renormalized to their tagged damage. A type only one
    side dealt keeps its row with ``--`` on the other side, because that is the
    fact worth seeing.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._data: list = []
        self._cells: list[tuple] = []
        lay = QtWidgets.QGridLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setHorizontalSpacing(6)
        lay.setVerticalSpacing(2)
        # Mirrors cp_cmp_rows_lay (combat_compare._build_compare_container) so a
        # type row sits under a skill row instead of drifting from it.
        for col in (1, 2, 7):
            lay.setColumnStretch(col, 1)
        self._lay = lay

    def rows(self) -> list:
        """The comparison rows currently rendered (for tests)."""
        return list(self._data)

    def set_rows(self, rows: list, name_a: str = "A", name_b: str = "B") -> None:
        """Render ``rows``, reusing the existing cells, and hide when empty."""
        self._data = list(rows)
        for i, row in enumerate(self._data):
            if i >= len(self._cells):
                self._cells.append(self._build_row(i))
            self._fill_row(self._cells[i], row, name_a, name_b)
        for j in range(len(self._data), len(self._cells)):
            for cell in self._cells[j]:
                cell.hide()
        # Explicit in BOTH directions: a container shown again must not
        # resurrect a table whose data is gone (the stale-widget trap that hid
        # a badge behind an isVisible() guard in components._badge once).
        self.setVisible(bool(self._data))

    def _build_row(self, i: int) -> tuple:
        # Col 0 is the matrix's 22 px icon cell and col 1 its 210 px skill
        # label, so both are reserved here: a blank icon box and the same max
        # width. Letting the type name span the two columns instead let its own
        # TEXT width decide where the boundaries fell, which is exactly what a
        # second grid must not do if it is meant to line up with the first.
        icon = QtWidgets.QLabel("")
        icon.setFixedSize(22, 22)
        self._lay.addWidget(icon, i, 0)
        name = QtWidgets.QLabel("")
        name.setMaximumWidth(210)
        self._lay.addWidget(name, i, 1)
        a_val = _cmp_value_label(theme.ACCENT_LIGHT)
        self._lay.addWidget(a_val, i, 2)
        a_bar = _cmp_share_bar(theme.ACCENT)
        self._lay.addWidget(a_bar, i, 3)
        a_pct = _cmp_pct_label()
        self._lay.addWidget(a_pct, i, 4)
        delta = QtWidgets.QLabel("")
        delta.setFixedWidth(64)
        delta.setAlignment(QtCore.Qt.AlignCenter)
        delta.setStyleSheet("font-size: 11px; font-family: monospace; font-weight: 800;")
        self._lay.addWidget(delta, i, 5, 1, 2)
        b_val = _cmp_value_label(theme.TEXT)
        self._lay.addWidget(b_val, i, 7)
        b_bar = _cmp_share_bar(theme.GOLD)
        self._lay.addWidget(b_bar, i, 8)
        b_pct = _cmp_pct_label()
        self._lay.addWidget(b_pct, i, 9)
        return (icon, name, a_val, a_bar, a_pct, delta, b_val, b_bar, b_pct)

    def _fill_row(self, cells: tuple, row, name_a: str, name_b: str) -> None:
        _icon, name, a_val, a_bar, a_pct, delta, b_val, b_bar, b_pct = cells
        colour = row.color if row.tagged else theme.MUTED
        name.setText(row.label)
        name.setStyleSheet(f"font-size: 12px; font-weight: 800; color: {colour};")
        name.setToolTip(affinity_view.compare_type_tooltip(row, name_a, name_b))

        for val, bar, pct, amount, share in (
                (a_val, a_bar, a_pct, row.a_amount, row.a_pct),
                (b_val, b_bar, b_pct, row.b_amount, row.b_pct)):
            if amount > 0:
                val.setText(_fmt_amount(amount))
                bar.setValue(int(share))
                pct.setText(f"{share:.0f}%")
                val.setToolTip(f"{amount:,.0f} ({share:.1f}%)")
            else:
                val.setText("--")
                bar.setValue(0)
                pct.setText("0%")
                val.setToolTip("")

        d = row.delta()
        delta.setText(f"{d / 1_000_000:+.2f}M" if abs(d) >= 1_000_000 else f"{d:+,.0f}")
        d_col = theme.GOOD if d >= 0 else theme.DANGER
        delta.setStyleSheet(
            f"font-size: 11px; font-family: monospace; font-weight: 800; color: {d_col};")
        for cell in cells:
            cell.show()


def _refresh_type_compare(page_obj, pa, pb) -> None:
    """Fill, or hide, the A-vs-B damage-by-type table.

    ``pa``/``pb`` may be None: the compare view bails before it has two players
    to compare, and the table must not keep the previous pair's rows on screen
    behind an early return. A page without the box (an older build, or a test
    stub) is left alone rather than raising.
    """
    box = getattr(page_obj, "cp_cmp_type_box", None)
    table = getattr(page_obj, "cp_cmp_type_table", None)
    if box is None or table is None or not _alive(table):
        return
    rows = affinity_view.compare_type_rows(pa, pb)
    table.set_rows(rows, getattr(pa, "name", "") or "A",
                   getattr(pb, "name", "") or "B")
    box.setVisible(bool(rows))


def refresh_rail_compare(page_obj, session: CombatSession, duration: float, metric: str) -> None:
    """Refresh compare tab with perfectly aligned skill-by-skill comparison matrix."""
    # Auto-prefer boss_session if available so compare tab evaluates damage dealt specifically to the boss
    tracker = getattr(page_obj, "_get_active_tracker", lambda: None)()
    if tracker and hasattr(tracker, "boss_session") and tracker.boss_session:
        if tracker.boss_session.players and tracker.boss_session.group_damage > 0:
            session = tracker.boss_session
            duration = max(1.0, session.duration)

    players = session.active_players()   # dummy: no chip-damage-only rows
    if len(players) < 2:
        if hasattr(page_obj, "cp_cmp_insight"):
            page_obj.cp_cmp_insight.setText("Need at least 2 players in combat to compare.")
        if hasattr(page_obj, "cp_plotter") and hasattr(page_obj.cp_plotter, "set_compare_players"):
            page_obj.cp_plotter.set_compare_players([])
        # No pair to compare, so no type table either - and the previous one
        # must go, not linger behind the early return.
        _refresh_type_compare(page_obj, None, None)
        return

    me = own_row(session) or session.players.get(getattr(page_obj, "_cp_selected_player", ""))
    me_class = me.hero_class if (me and me.hero_class) else ""

    same_class_only = bool(getattr(page_obj, "cp_cmp_same_class_cb", None) and page_obj.cp_cmp_same_class_cb.isChecked())
    shared_skills_only = bool(getattr(page_obj, "cp_cmp_shared_skills_cb", None) and page_obj.cp_cmp_shared_skills_cb.isChecked())

    all_ranked = session.ranked_players(view="damage")
    if same_class_only and me_class:
        class_players = [p for p in all_ranked if p.hero_class == me_class]
        names_list = [p.name for p in class_players] if len(class_players) >= 2 else [p.name for p in all_ranked]
    else:
        names_list = [p.name for p in all_ranked]

    # Populate combos ONLY when candidate names change (prevents combo flickering/re-selection)
    combo_items_main = []
    for nm in names_list:
        p_obj = session.players.get(nm)
        disp = f"{nm} ({p_obj.hero_class})" if (p_obj and p_obj.hero_class) else nm
        combo_items_main.append((disp, nm))

    combo_items_extra = [("(None)", "")] + combo_items_main

    combos_to_sync = [
        (page_obj.cp_compare_combo_a, combo_items_main),
        (page_obj.cp_compare_combo_b, combo_items_main),
    ]
    for cb_attr in ("cp_compare_combo_c", "cp_compare_combo_d", "cp_compare_combo_e"):
        if hasattr(page_obj, cb_attr):
            combos_to_sync.append((getattr(page_obj, cb_attr), combo_items_extra))

    for cb, items in combos_to_sync:
        if not cb:
            continue
        cur_items = [cb.itemText(i) for i in range(cb.count())]
        new_items = [it[0] for it in items]
        if cur_items != new_items:
            cb.blockSignals(True)
            cb.clear()
            for disp, data in items:
                cb.addItem(disp, data)
            cb.blockSignals(False)

    # Defaults for A and B
    if not getattr(page_obj, "_cp_compare_a", "") or page_obj._cp_compare_a not in session.players:
        page_obj._cp_compare_a = me.name if (me and me.name in names_list) else names_list[0]
    if not getattr(page_obj, "_cp_compare_b", "") or page_obj._cp_compare_b not in session.players:
        other = next((n for n in names_list if n != page_obj._cp_compare_a), names_list[-1])
        page_obj._cp_compare_b = other

    def _sync_idx(cb, val):
        if not cb or not val:
            return
        for i in range(cb.count()):
            if cb.itemData(i) == val or cb.itemText(i).startswith(val):
                if cb.currentIndex() != i:
                    cb.blockSignals(True)
                    cb.setCurrentIndex(i)
                    cb.blockSignals(False)
                break

    _sync_idx(page_obj.cp_compare_combo_a, page_obj._cp_compare_a)
    _sync_idx(page_obj.cp_compare_combo_b, page_obj._cp_compare_b)
    _sync_idx(getattr(page_obj, "cp_compare_combo_c", None), getattr(page_obj, "_cp_compare_c", ""))
    _sync_idx(getattr(page_obj, "cp_compare_combo_d", None), getattr(page_obj, "_cp_compare_d", ""))
    _sync_idx(getattr(page_obj, "cp_compare_combo_e", None), getattr(page_obj, "_cp_compare_e", ""))

    pA = session.players.get(page_obj._cp_compare_a)
    pB = session.players.get(page_obj._cp_compare_b)
    if not pA or not pB:
        _refresh_type_compare(page_obj, None, None)
        return

    # Collect active comparison players + sync the plotter FIRST: this must run
    # on every pass (even when the table fingerprint below is unchanged) so that
    # switching away and back to Compare re-arms compare mode on the plotter.
    extra_names = [getattr(page_obj, "_cp_compare_c", ""),
                   getattr(page_obj, "_cp_compare_d", ""),
                   getattr(page_obj, "_cp_compare_e", "")]
    active_players = [pA, pB]
    for en in extra_names:
        if en and en in session.players and en not in [p.name for p in active_players]:
            active_players.append(session.players[en])

    # Update Timeline Plotter compare players
    active_names = [p.name for p in active_players]
    if hasattr(page_obj, "cp_plotter") and hasattr(page_obj.cp_plotter, "set_compare_players"):
        page_obj.cp_plotter.set_compare_players(active_names)

    if hasattr(page_obj, "cp_plotter_info"):
        segment_tag = " [Boss Target]" if getattr(session, "kind", "") == "boss" else ""
        page_obj.cp_plotter_info.setText(f"📊 Timeline Bar Graph (10s bins){segment_tag}: Comparing {', '.join(active_names)}")

    pA_val = int(pA.total_damage) if pA else 0
    pB_val = int(pB.total_damage) if pB else 0
    fp = (
        getattr(page_obj, "_cp_compare_a", ""),
        getattr(page_obj, "_cp_compare_b", ""),
        getattr(page_obj, "_cp_compare_c", ""),
        getattr(page_obj, "_cp_compare_d", ""),
        getattr(page_obj, "_cp_compare_e", ""),
        same_class_only,
        shared_skills_only,
        int(duration),
        pA_val,
        pB_val,
        len(players)
    )
    if getattr(page_obj, "_last_cmp_fp", None) == fp:
        return
    page_obj._last_cmp_fp = fp

    # Class match badge
    classes = [p.hero_class or "?" for p in active_players]
    same_class = len(set(classes)) == 1
    match_col = theme.GOOD if same_class else theme.GOLD
    page_obj.cp_cmp_class_match.setText(
        f"Same Class: {classes[0]} ({len(active_players)} players)" if same_class
        else f"Comparing {len(active_players)} players ({', '.join(classes[:3])})")
    page_obj.cp_cmp_class_match.setStyleSheet(
        f"font-size: 10.5px; font-weight: 800; color: {match_col}; "
        f"border: 1px solid {match_col}; border-radius: 8px; padding: 2px 8px;")

    dpsA = pA.dps(duration)
    dpsB = pB.dps(duration)
    delta_dps = dpsA - dpsB
    pct_adv = ((delta_dps / max(1.0, dpsB)) * 100.0)

    lead = pA if delta_dps >= 0 else pB
    d_col = theme.GOOD if delta_dps >= 0 else theme.DANGER
    if hasattr(page_obj, "cp_cmp_winner"):
        page_obj.cp_cmp_winner.setText(f"{lead.name} leads")

    def _fmt_rate(v: float) -> str:
        return f"{v:,.0f}/s" if abs(v) < 100_000 else f"{v/1000:.1f}k/s"

    page_obj.cp_cmp_dps_delta.setText(f"+{_fmt_rate(delta_dps)}" if delta_dps >= 0 else f"-{_fmt_rate(abs(delta_dps))}")
    page_obj.cp_cmp_dps_delta.setStyleSheet(
        f"font-size: 18px; font-weight: 900; font-family: monospace; color: {d_col};")
    page_obj.cp_cmp_pct_delta.setText(f"+{abs(pct_adv):.1f}%")
    page_obj.cp_cmp_pct_delta.setStyleSheet(
        f"font-size: 14px; font-weight: 800; font-family: monospace; color: {d_col};")

    critsA = sum(s.crit_count for s in pA.skills.values())
    hitsA = sum(s.hit_count for s in pA.skills.values())
    critA_pct = (critsA / hitsA * 100.0) if hitsA > 0 else 0.0

    critsB = sum(s.crit_count for s in pB.skills.values())
    hitsB = sum(s.hit_count for s in pB.skills.values())
    critB_pct = (critsB / hitsB * 100.0) if hitsB > 0 else 0.0

    crit_gap = critA_pct - critB_pct
    page_obj.cp_cmp_crit_gap.setText(f"{crit_gap:+.1f}%")
    page_obj.cp_cmp_hit_rate.setText(f"{hitsA/duration:.1f} vs {hitsB/duration:.1f}")

    topA = _ranked_skill_total(pA)
    topB = _ranked_skill_total(pB)
    topA_val = topA[0].total if topA else 0.0
    topB_val = topB[0].total if topB else 0.0
    top_diff = topA_val - topB_val
    page_obj.cp_cmp_top_gap.setText(
        f"{top_diff:+,.0f}" if abs(top_diff) < 10_000_000 else f"{top_diff/1_000_000:+.2f}M")

    # Build per-player aggregated skill maps
    def _skill_key(sp) -> str:
        nm = (sp.name or "").strip()
        if nm and nm != "Unknown":
            return nm.lower()
        return str(sp.skill_id)

    class AggregatedSkill:
        def __init__(self, name: str, skill_id: str):
            self.name = name
            self.skill_id = skill_id
            self.damage = 0.0
            self.hit_count = 0
            self.crit_count = 0

        @property
        def total(self) -> float:
            return self.damage

        @property
        def crit_pct(self) -> float:
            return (self.crit_count / self.hit_count * 100.0) if self.hit_count > 0 else 0.0

    player_skill_maps: dict[str, dict[str, AggregatedSkill]] = {}
    for p in active_players:
        m: dict[str, AggregatedSkill] = {}
        for sp in p.skills.values():
            ckey = _skill_key(sp)
            if ckey in m:
                m[ckey].damage += sp.damage
                m[ckey].hit_count += sp.hit_count
                m[ckey].crit_count += sp.crit_count
            else:
                agg = AggregatedSkill(sp.name or ckey.title(), str(sp.skill_id))
                agg.damage = sp.damage
                agg.hit_count = sp.hit_count
                agg.crit_count = sp.crit_count
                m[ckey] = agg
        player_skill_maps[p.name] = m

    all_keys = set()
    for m in player_skill_maps.values():
        all_keys.update(m.keys())

    if shared_skills_only:
        filtered_keys = [k for k in all_keys if all(k in pm for pm in player_skill_maps.values())]
        if not filtered_keys:
            filtered_keys = list(all_keys)
            if hasattr(page_obj, "cp_cmp_insight"):
                page_obj.cp_cmp_insight.setText("Note: Selected players share no common skills — displaying all skills.")
                page_obj.cp_cmp_insight.show()
    else:
        filtered_keys = list(all_keys)

    def _max_total_key(k):
        return max((pm[k].total for pm in player_skill_maps.values() if k in pm), default=0.0)

    sorted_keys = sorted(filtered_keys, key=_max_total_key, reverse=True)

    # Header row lives in the SAME grid as the rows (with column spans) so
    # header columns always align with the row columns below.
    def _short(nm: str, lim: int = 20) -> str:
        return nm if len(nm) <= lim else nm[:lim - 1] + "…"

    # Header labels are cached on the page across refresh passes; a cache
    # entry surviving a page rebuild points at a deleted QLabel, so rebuild
    # the header whenever either cached ref is dead.
    if not (_alive(getattr(page_obj, "_cp_cmp_hdr_a", None))
            and _alive(getattr(page_obj, "_cp_cmp_hdr_b", None))):
        _forget_attrs(page_obj, ("_cp_cmp_hdr_a", "_cp_cmp_hdr_b"))
        hdr_base = (
            f"background: {theme.PANEL_LOW}; color: {theme.DIM}; "
            f"font-size: 10.5px; font-weight: 800; letter-spacing: 0.5px; padding: 4px 6px;"
        )
        h_skill = QtWidgets.QLabel("SKILL")
        h_skill.setStyleSheet(hdr_base)
        page_obj.cp_cmp_rows_lay.addWidget(h_skill, 0, 0, 1, 2)
        h_a = QtWidgets.QLabel("")
        h_a.setAlignment(QtCore.Qt.AlignCenter)
        h_a.setStyleSheet(hdr_base + f" color: {theme.ACCENT_LIGHT};")
        page_obj.cp_cmp_rows_lay.addWidget(h_a, 0, 2, 1, 3)
        h_d = QtWidgets.QLabel("DELTA / DRIVER")
        h_d.setAlignment(QtCore.Qt.AlignCenter)
        h_d.setStyleSheet(hdr_base)
        page_obj.cp_cmp_rows_lay.addWidget(h_d, 0, 5, 1, 2)
        h_b = QtWidgets.QLabel("")
        h_b.setAlignment(QtCore.Qt.AlignCenter)
        h_b.setStyleSheet(hdr_base + f" color: {theme.TEXT};")
        page_obj.cp_cmp_rows_lay.addWidget(h_b, 0, 7, 1, 3)
        page_obj._cp_cmp_hdr_a = h_a
        page_obj._cp_cmp_hdr_b = h_b
    page_obj._cp_cmp_hdr_a.setText(f"👤 {_short(pA.name)} ({dpsA:,.0f}/s)")
    page_obj._cp_cmp_hdr_a.setToolTip(f"{pA.name} ({dpsA:,.0f}/s)")
    page_obj._cp_cmp_hdr_b.setText(f"👤 {_short(pB.name)} ({dpsB:,.0f}/s)")
    page_obj._cp_cmp_hdr_b.setToolTip(f"{pB.name} ({dpsB:,.0f}/s)")

    totA = max(1.0, pA.total_damage)
    totB = max(1.0, pB.total_damage)

    # Render Aligned Skill Rows (re-uses existing row widgets in-place to prevent flash spamming)
    for i, key in enumerate(sorted_keys[:25]):
        sA = player_skill_maps[pA.name].get(key)
        sB = player_skill_maps[pB.name].get(key)

        display_name = ""
        skill_id = ""
        for pm in player_skill_maps.values():
            if key in pm:
                if pm[key].name and not display_name:
                    display_name = pm[key].name
                if pm[key].skill_id and not skill_id:
                    skill_id = pm[key].skill_id
        if not display_name:
            display_name = key.title()

        valA = sA.total if sA else 0.0
        valB = sB.total if sB else 0.0
        delta = valA - valB

        shareA = (valA / totA) * 100.0
        shareB = (valB / totB) * 100.0

        if sA and sB:
            if sA.hit_count > sB.hit_count + 5:
                driver = f"+{sA.hit_count - sB.hit_count} Hits"
            elif sB.hit_count > sA.hit_count + 5:
                driver = f"-{sB.hit_count - sA.hit_count} Hits"
            else:
                cA = sA.crit_pct
                cB = sB.crit_pct
                if cA > cB + 5:
                    driver = f"+{cA - cB:.0f}% Crit"
                elif cB > cA + 5:
                    driver = f"-{cB - cA:.0f}% Crit"
                else:
                    driver = "Avg Hit"
        elif sA and not sB:
            driver = "A only"
        else:
            driver = "B only"

        if i >= len(page_obj._cp_cmp_row_widgets):
            lbl_ico = QtWidgets.QLabel()
            lbl_ico.setFixedSize(22, 22)
            lbl_ico.setScaledContents(True)
            lbl_ico.setAlignment(QtCore.Qt.AlignCenter)

            lbl_sk = QtWidgets.QLabel("")
            lbl_sk.setMaximumWidth(210)
            lbl_sk.setStyleSheet(f"font-size: 13px; font-weight: 800; color: {theme.TEXT};")

            # Player A block
            lbl_vA = QtWidgets.QLabel("")
            lbl_vA.setMinimumWidth(58)
            lbl_vA.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            lbl_vA.setStyleSheet(f"font-size: 11px; font-family: monospace; font-weight: 800; color: {theme.ACCENT_LIGHT};")

            bar_A = QtWidgets.QProgressBar()
            bar_A.setFixedWidth(44)
            bar_A.setFixedHeight(5)
            bar_A.setTextVisible(False)
            bar_A.setRange(0, 100)
            bar_A.setStyleSheet(f"QProgressBar {{ background: {theme.SURFACE}; border: none; border-radius: 2px; }} QProgressBar::chunk {{ background: {theme.ACCENT}; border-radius: 2px; }}")

            lbl_pA = QtWidgets.QLabel("")
            lbl_pA.setFixedWidth(38)
            lbl_pA.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            lbl_pA.setStyleSheet(f"font-size: 10.5px; font-family: monospace; color: {theme.DIM};")

            # Center delta / driver block
            lbl_dlt = QtWidgets.QLabel("")
            lbl_dlt.setFixedWidth(64)
            lbl_dlt.setAlignment(QtCore.Qt.AlignCenter)
            lbl_dlt.setStyleSheet("font-size: 11px; font-family: monospace; font-weight: 800;")

            lbl_drv = QtWidgets.QLabel("")
            lbl_drv.setFixedWidth(64)
            lbl_drv.setAlignment(QtCore.Qt.AlignCenter)
            lbl_drv.setStyleSheet(f"font-size: 10px; font-weight: 700; color: {theme.GOLD};")

            # Player B block
            lbl_vB = QtWidgets.QLabel("")
            lbl_vB.setMinimumWidth(58)
            lbl_vB.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            lbl_vB.setStyleSheet(f"font-size: 11px; font-family: monospace; font-weight: 800; color: {theme.TEXT};")

            bar_B = QtWidgets.QProgressBar()
            bar_B.setFixedWidth(44)
            bar_B.setFixedHeight(5)
            bar_B.setTextVisible(False)
            bar_B.setRange(0, 100)
            bar_B.setStyleSheet(f"QProgressBar {{ background: {theme.SURFACE}; border: none; border-radius: 2px; }} QProgressBar::chunk {{ background: {theme.GOLD}; border-radius: 2px; }}")

            lbl_pB = QtWidgets.QLabel("")
            lbl_pB.setFixedWidth(38)
            lbl_pB.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            lbl_pB.setStyleSheet(f"font-size: 10.5px; font-family: monospace; color: {theme.DIM};")

            row_cells = (
                lbl_ico, lbl_sk, lbl_vA, bar_A, lbl_pA,
                lbl_dlt, lbl_drv,
                lbl_vB, bar_B, lbl_pB
            )
            for col, wdg in enumerate(row_cells):
                page_obj.cp_cmp_rows_lay.addWidget(wdg, i + 1, col)
            page_obj._cp_cmp_row_widgets.append(row_cells)

        (l_ico, l_sk, l_vA, b_A, l_pA, l_dlt, l_drv, l_vB, b_B, l_pB) = page_obj._cp_cmp_row_widgets[i]

        # Update Skill Icon on every pass
        ico_tmp = _skill_icon(skill_id or display_name, size=22)
        if ico_tmp.pixmap() and not ico_tmp.pixmap().isNull():
            l_ico.setPixmap(ico_tmp.pixmap())
            l_ico.setFixedSize(22, 22)
            l_ico.setScaledContents(True)
            l_ico.setAlignment(QtCore.Qt.AlignCenter)

        # The tag belongs on the main skill list, not only in the row tooltip
        # or the hover popup: this is the one place two players' skills are
        # ranked against each other, so "(Item proc)" / "(Passive)" is what
        # explains why a proc outranks a rotation skill. Capped by
        # tagged_skill_name so the tag survives the 22-char ceiling.
        tagged = tagged_skill_name(skill_id, display_name, 26)
        l_sk.setText(tagged)
        l_sk.setToolTip(f"{tagged_skill_name(skill_id, display_name)} (ID: {skill_id})")

        # Update Player A side
        if sA and sA.total > 0:
            vA_str = f"{sA.total:,.0f}" if sA.total < 1_000_000 else f"{sA.total/1_000_000:.2f}M"
            l_vA.setText(vA_str)
            b_A.setValue(int(shareA))
            l_pA.setText(f"{shareA:.0f}%")
            l_vA.setToolTip(f"{sA.hit_count} hits · {sA.crit_pct:.0f}% crit")
        else:
            l_vA.setText("--")
            b_A.setValue(0)
            l_pA.setText("0%")
            l_vA.setToolTip("")

        # Update Center Delta
        d_col = theme.GOOD if delta >= 0 else theme.DANGER
        if delta >= 0:
            d_str = f"+{delta:,.0f}" if delta < 1_000_000 else f"+{delta/1_000_000:.2f}M"
        else:
            d_str = f"{delta:,.0f}" if abs(delta) < 1_000_000 else f"{delta/1_000_000:.2f}M"
        l_dlt.setText(d_str)
        if getattr(l_dlt, "_cached_col", None) != d_col:
            l_dlt._cached_col = d_col
            l_dlt.setStyleSheet(f"font-size: 11px; font-family: monospace; font-weight: 800; color: {d_col};")

        l_drv.setText(driver)

        # Update Player B side
        if sB and sB.total > 0:
            vB_str = f"{sB.total:,.0f}" if sB.total < 1_000_000 else f"{sB.total/1_000_000:.2f}M"
            l_vB.setText(vB_str)
            b_B.setValue(int(shareB))
            l_pB.setText(f"{shareB:.0f}%")
            l_vB.setToolTip(f"{sB.hit_count} hits · {sB.crit_pct:.0f}% crit")
        else:
            l_vB.setText("--")
            b_B.setValue(0)
            l_pB.setText("0%")
            l_vB.setToolTip("")

        for cell in page_obj._cp_cmp_row_widgets[i]:
            cell.show()

    for j in range(len(sorted_keys[:25]), len(page_obj._cp_cmp_row_widgets)):
        for cell in page_obj._cp_cmp_row_widgets[j]:
            cell.hide()

    # The type split for the same pair, filled with the matrix it sits under.
    _refresh_type_compare(page_obj, pA, pB)

    lead = pA if delta_dps >= 0 else pB
    lag = pB if delta_dps >= 0 else pA
    multi_note = f" (Comparing {len(active_players)} players)" if len(active_players) > 2 else ""
    shared_note = " [Shared Skills Only]" if shared_skills_only else ""
    page_obj.cp_cmp_insight.setText(
        f"💡 Gap Driver: {lead.name} leads {lag.name} by {abs(delta_dps):,.0f}/s (+{abs(pct_adv):.1f}%).{multi_note}{shared_note}"
    )



