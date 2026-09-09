"""Party page, the STAT BLOCKS: the two pinned key/value tables in the detail
column and the one that fills the hero's totals.

Split out of `party_profile.py` (2026-09-26) — which keeps the detail column's
own construction, the selected piece's fill and the Buffs accordion — because
that file reached the 840-line budget for `ui/` and the stat blocks are a
concern of their own: one row shape, one height pin, one clamping rule, shared
by the selected piece and by the totals, plus the totals' own sheet-side
summing. Same split as `party_grid.py` and `party_roster.py` beside it:
`PartyProfileMixin` inherits `PartyStatsMixin` and re-exports `_BLOCK_ROWS`, so
no call site — including the tests' `from ...party_profile import _BLOCK_ROWS`
— had to move.
"""
from __future__ import annotations

from PySide6 import QtWidgets

from .. import theme
from ...data.items import catalog as gcatalog
from ...data.items import stats as gstats
from ...data.items.labels import is_bag_slot

# How many rows a stat block is given as a FLOOR (2026-09-26), so a hero with
# two stats and a hero with twelve occupy the SAME height in the common case
# and the block does not shove the gear notes up and down the page.
#
# It was a CEILING until 2026-09-26, with anything past the seventh summarised
# as "+N more". That was wrong for the totals block: summing the 15 worn slots
# by stat label yields 8-10 distinct stats for any hero above Common, so the
# marker was the NORMAL state and two or three real numbers were missing from
# the page's main readout on every refresh. A block with more rows now GROWS
# past this floor; nothing is ever dropped. See `_party_stat_rows`.
_BLOCK_ROWS = 7


class PartyStatsMixin:
    """The detail column's two stat tables and the totals fill.

    Reads its state off the page the way the other split-out mixins do —
    `_party_piece_gear` (the live gear walk's filled rows) and
    `_party_totals_lay` / `_party_piece` (the layouts the build created) — so
    it can be mixed into the page without the build having to pass anything.
    """

    # ------------------------------------------------------- stat block rows
    @staticmethod
    def _party_stat_row(label: str, value: str,
                        tip: str = "") -> QtWidgets.QWidget:
        """One `key ......... value` row — the shape BOTH readouts in the
        detail column use (Total Stats and the selected piece): the key in
        DIM on the left, the value in its stat identity colour and bold on
        the right, so the two blocks read as the same kind of table."""
        row = QtWidgets.QWidget()
        h = QtWidgets.QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)
        k = QtWidgets.QLabel(label)
        k.setStyleSheet(f"color:{theme.DIM};font-size:15px;")
        v = QtWidgets.QLabel(value)
        v.setStyleSheet(
            f"color:{theme.stat_color(label)};font-size:15px;font-weight:700;")
        h.addWidget(k)
        h.addStretch(1)
        h.addWidget(v)
        # `tip` is accepted and IGNORED (2026-09-26): hover tooltips are gone
        # from this page, so the overflow row's omitted values have nowhere to
        # go. It still reports HOW MANY are hidden, in the row's own label.
        return row

    @staticmethod
    def _party_lock_rows(lay: QtWidgets.QVBoxLayout) -> None:
        """Size a stat block's rows area to its rows, but never below
        `_BLOCK_ROWS`.

        The height comes from a real (unparented) row's own sizeHint, so it
        tracks the row style instead of a guessed pixel count; the rows
        themselves are single-line by construction (no word wrap), so nothing
        can outgrow the height set here.

        `_BLOCK_ROWS` is a FLOOR, not a ceiling (2026-09-26 — it used to be a
        hard ceiling and rows past the seventh were dropped). It is what makes
        a hero with two stats and a hero with ten occupy the same height in the
        common case, so the block does not shove the gear notes around. A block
        with more rows than the floor GROWS to fit them, because the one thing
        this must never do is silently hide a number.
        """
        host = lay.parentWidget()
        if host is None:
            return
        probe = PartyStatsMixin._party_stat_row("", "")
        row_h = max(1, probe.sizeHint().height())
        del probe
        n_rows = max(_BLOCK_ROWS, sum(1 for i in range(lay.count())
                                      if lay.itemAt(i).widget() is not None))
        host.setFixedHeight(row_h * n_rows
                            + lay.spacing() * (n_rows - 1))

    def _party_stat_rows(self, lay: QtWidgets.QVBoxLayout,
                         rows: list[tuple[str, str, str]]) -> None:
        """Add `rows` (key, value, tooltip) to a stat block and size it.

        EVERY row is laid out. There is no clamp and no "+N more" marker any
        more (2026-09-26): the marker reported a count and dropped the values,
        and with tooltips gone the values were on the page nowhere at all. The
        measurement that killed it — 15 worn slots summed by label produce 8
        to 10 distinct stats for any hero above Common, so the marker was not
        an edge case, it was the normal state and two or three real numbers
        were always missing from the page's main readout.

        The third element of each row is the tooltip the row USED to carry and
        no longer does (2026-09-26: no hover popups on this page). It is still
        unpacked so a call site that builds (key, value, tip) tuples keeps
        working unchanged.
        """
        for key, value, tip in rows:
            lay.addWidget(self._party_stat_row(key, value, tip))
        self._party_lock_rows(lay)

    def _fill_party_totals(self) -> None:
        """The STAT TOTALS block: every equipped piece's computed stats,
        summed by stat label.

        Same sheet-side math per piece as `_fill_party_piece` (gear_stats ->
        stat_rows), added up over every filled, non-container row the grid
        shows — so the block is exactly the sum of what the tiles wear/carry.
        A stat whose parts carry computed `raw` values sums the TRUE floats
        and renders per stat_display_mode; one authored (raw-less)
        contributor downgrades that row to the rounded display value.
        Rebuilt from `_party_piece_gear` on every fill; no live reads.
        """
        lay = getattr(self, "_party_totals_lay", None)
        if lay is None or not self._widget_alive(lay.parentWidget()):
            return
        while lay.count():
            it = lay.takeAt(0)
            w = it.widget()
            if w is not None:
                w.deleteLater()
        totals: dict[str, dict] = {}
        order: list[str] = []
        hero_level = 0
        for g in self._party_piece_gear:
            kind = g.get("kind") or ""
            if not kind or is_bag_slot(g.get("slot")):
                continue                    # containers are not pieces
            item = (gcatalog._data().get("items", {}) or {}).get(kind) or {}
            # equipped_stats, not gear_stats: the walk has always handed us
            # the live `upgrade` rank and this call site dropped it, so every
            # worn piece was summed as if freshly dropped at +0.
            gs = gstats.equipped_stats(item, g.get("level") or None,
                                       g.get("rarity") or None,
                                       g.get("upgrade") or 0)
            # the piece's own `level` IS the character's level — the tooltip
            # prints "Level 25" beside a level-25 item — so the highest one
            # worn is the hero's, which is what the crit base is keyed on.
            lv = g.get("level") or 0
            if isinstance(lv, int) and lv > hero_level:
                hero_level = lv
            for s in gstats.stat_rows(gs["stats"] if gs else []):
                lbl = s["label"]
                t = totals.get(lbl)
                if t is None:
                    t = totals[lbl] = {"value": 0.0, "raw": 0.0,
                                       "has_raw": True}
                    order.append(lbl)
                t["value"] += s["value"]
                if s.get("raw") is not None:
                    t["raw"] += s["raw"]
                else:
                    t["has_raw"] = False
        if not order:
            # No line here (2026-09-26), for the same reason as the piece
            # block's: an empty Total Stats is an empty block. The lock rows
            # still run so the block keeps its height and the column below
            # it does not jump when the first piece lands.
            self._party_lock_rows(lay)
            return
        rows = [(lbl, gstats.stat_text(
            round(totals[lbl]["value"], 2),
            round(totals[lbl]["raw"], 3)
            if totals[lbl]["has_raw"] else None), "")
            for lbl in order]
        # Critical Chance, straight under the Critical rating it comes from
        # (2026-09-26). The sheet shows this number and we showed only the
        # flat rating, so the block read as if it were the character's crit
        # when it is the equipment's half of it. The conversion is measured
        # (see stats.crit_chance_pct) and returns None for any level without
        # a measured base, in which case the row is simply absent — the one
        # thing worse than not showing it would be showing a borrowed one.
        crit = totals.get("Critical")
        if crit is not None:
            rating = crit["raw"] if crit["has_raw"] else crit["value"]
            pct = gstats.crit_chance_pct(rating, hero_level)
            if pct is not None:
                at = (order.index("Critical") + 1
                      if "Critical" in order else len(rows))
                rows.insert(at, ("Critical Chance", f"{pct:.1f}%", ""))
        self._party_stat_rows(lay, rows)
