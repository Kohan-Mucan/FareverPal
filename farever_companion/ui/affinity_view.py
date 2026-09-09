"""Damage-by-type (affinity) presentation shared by the DPS views.

``SkillParse.affinity_damage`` carries the game's own per-hit damage type,
bucketed in ``core/dps_tracker_events``. Turning that into rows is a
presentation problem rather than a lookup, for three reasons:

* **The vocabulary is open.** The item sheets name Physical / Fire / Magic /
  Light, but live hits also read Raw, Chaos and the mechanic tag ``threshold``,
  and a patch can add more. So the curated schools are ordered first and every
  other tag follows as its own bucket — an unrecognised school is a row, never
  a silent drop.
* **Hits can be untagged.** A nulled or renamed field, or a bridge older than
  the key, leaves the buckets short of the total. That remainder is shown
  explicitly as ``untagged``: hiding it would make the parts look like they sum
  to the whole and hide that the data is incomplete.
* **The split is per skill**, so a player total has to be summed across skills.

The same rows back three surfaces: the rail's bar for one player, the name in
each skill row's own type column, and the head-to-head table in the Compare
view (``compare_type_rows``), which lines two players up on the UNION of their
types so a school only one of them dealt still has a row to compare against.

The skills table shows ONE column naming the type, not a column per school: a
weapon deals one school, the table already has a TOTAL, and four numeric
columns do not fit the rail beside the name (see ``TYPE_COLUMN_WIDTH``).
``dominant_type`` is what that column reads, and it refuses to name a single
type for a skill that is genuinely two.

No Qt widgets and no process access — arithmetic over a parse — so the shared
``SkillRow``, the Combat rail, the Compare matrix and a test can all use it
directly. Nothing here needs a live session: an archived fight is a
``CombatSession`` like any other, which is what lets a past fight show the same
breakdown.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..data.items import labels as item_labels
from . import theme

# The schools a weapon sheet can name (data/items/labels.py `_AFFINITY_COLORS`).
# Display order: what a weapon can be comes first.
CURATED = ("Physical", "Fire", "Magic", "Light")
# The explicit remainder bucket. Never merged into a school above.
UNTAGGED = "untagged"
# PD/MD merge: Physical + Raw read as physical damage; every other tagged
# school (Magic, Fire, Light, Chaos, a mechanic tag, a future school) reads
# as magic damage. Deliberate and total: the split names two numbers, so an
# unrecognised tag is a kind of magic rather than a silent drop.
PD_SCHOOLS = frozenset({"Physical", "Raw"})
# Below this, a remainder is float dust from summing in a different order than
# the total, not an untagged hit (the damage reader floors every hit at 1.0).
REMAINDER_EPS = 0.5

# --- the skills table's single type column ----------------------------------#

TYPE_HEADER = "TYPE"
# Measured, not guessed: the widest thing this cell can be asked to paint is
# "threshold +1" / "Physical +2" at 60px of advance and 64px of rich-text
# document width (ai/workspace/dmgtype/probe_split_fit.py), so 72 leaves the
# document a little room. This number is the reason the table shows one type
# rather than a column per school: four 56px columns leave the name column 6px,
# which is no column at all.
TYPE_COLUMN_WIDTH = 72
# The table width below which the column is dropped rather than shown.
#
# The table's fixed cells and their spacing come to 476 with this column
# included (434 of cells + 42 of gaps), so 620 leaves the skill NAME its 144px
# floor. The rail's own minimum (732) sits well above that, which is the point:
# the column is dropped only in the degenerate case of a splitter forced below
# its children's minimums, never in normal use. Dropping it is still the right
# answer there — a name squeezed to one character is worse than a missing
# column, and the split stays in the row tooltip.
TYPE_COLUMN_MIN_TABLE = 620
# A second school this close to the top one is proc noise, not a split worth
# writing "Physical +1" over. Below this share the table names the type and
# says nothing, because nothing is being claimed about the rest.
SPLIT_MIN_PCT = 5.0


@dataclass(frozen=True)
class TypeRow:
    """One row of a damage-by-type breakdown."""

    label: str
    amount: float
    pct: float
    hits: int
    color: str
    tagged: bool = True

    def pct_text(self) -> str:
        """Percent for display: whole numbers, but sub-1% keeps a decimal."""
        return f"{self.pct:.0f}%" if self.pct >= 1.0 else f"{self.pct:.1f}%"


def affinity_rows(affinity_damage: dict | None, total_damage: float,
                  affinity_hits: dict | None = None) -> list[TypeRow]:
    """Buckets -> ordered rows, curated schools first and ``untagged`` last.

    ``total_damage`` is the skill's or player's damage total, which is usually
    MORE than the sum of the buckets.

    Empty when nothing was classified at all: a capture with no types (an
    older bridge, a build that renamed or nulled the field) must not grow a
    permanent "untagged 100%" line that says nothing and shows on every fight.
    Rows appear once something IS classified, and then the remainder that could
    not be is shown beside them.
    """
    buckets = {str(k): float(v) for k, v in (affinity_damage or {}).items() if v}
    hits = {str(k): int(v) for k, v in (affinity_hits or {}).items()}
    total = float(total_damage or 0.0)
    tagged = sum(buckets.values())
    if not buckets:
        return []
    denom = total if total > 0.0 else tagged

    def _row(label: str, amount: float, tagged_row: bool) -> TypeRow:
        pct = (amount / denom * 100.0) if denom > 0 else 0.0
        return TypeRow(
            label=label, amount=amount, pct=pct,
            hits=hits.get(label, 0) if tagged_row else 0,
            color=(item_labels.affinity_color(label) if tagged_row else theme.MUTED),
            tagged=tagged_row)

    rows: list[TypeRow] = []
    for school in CURATED:
        amount = buckets.pop(school, 0.0)
        if amount:
            rows.append(_row(school, amount, True))
    # Everything else the game named — Raw, Chaos, a mechanic tag, a school a
    # future patch adds. Strongest first so the biggest bucket reads first.
    for label in sorted(buckets, key=lambda k: buckets[k], reverse=True):
        rows.append(_row(label, buckets[label], True))
    # Only a REAL remainder: a fully tagged parse must not grow a 0% row, and
    # the untagged hits are unknown by construction (the tracker never buckets
    # an untagged hit), so this row deliberately carries no hit count.
    if (total - tagged) > REMAINDER_EPS:
        rows.append(_row(UNTAGGED, total - tagged, False))
    return rows


@dataclass(frozen=True)
class NamedType:
    """What the skills table's TYPE column says about one skill.

    ``label`` is the school to name, ``others`` how many further schools are
    carrying ``SPLIT_MIN_PCT`` or more of the skill's tagged damage. A single
    weapon skill reads ``others == 0`` and the column is just the name; a skill
    that really does split reads ``Physical +2``, which is the whole point of
    keeping the count — naming the top school alone would claim the skill is
    pure fire when it is not.
    """

    label: str
    color: str
    pct: float
    others: int = 0

    def caption(self) -> str:
        return self.label if not self.others else f"{self.label} +{self.others}"


def dominant_type(rows: list[TypeRow]) -> NamedType | None:
    """The one name the TYPE column shows for a skill, or ``None``.

    ``None`` when nothing was classified — an older bridge or a renamed field —
    so the column stays blank rather than asserting a type it did not read.
    The ``untagged`` remainder is not a type and is never named: it says the
    field was missing, not that the skill dealt "untagged" damage, and the row
    tooltip is where that remainder belongs.
    """
    tagged = [r for r in rows if r.tagged]
    if not tagged:
        return None
    top = max(tagged, key=lambda r: r.amount)
    tagged_total = sum(r.amount for r in tagged)
    others = sum(
        1 for r in tagged
        if r is not top and (r.amount / tagged_total * 100.0) >= SPLIT_MIN_PCT
    ) if tagged_total > 0 else 0
    return NamedType(label=top.label, color=top.color, pct=top.pct, others=others)


def player_affinity(p: object) -> tuple[dict[str, float], dict[str, int], float]:
    """Sum one player's per-skill buckets into ``(damage, hits, total)``."""
    damage: dict[str, float] = {}
    hits: dict[str, int] = {}
    total = 0.0
    for sp in (getattr(p, "skills", None) or {}).values():
        total += float(getattr(sp, "damage", 0.0) or 0.0)
        for label, amount in (getattr(sp, "affinity_damage", None) or {}).items():
            damage[label] = damage.get(label, 0.0) + float(amount)
        for label, count in (getattr(sp, "affinity_hits", None) or {}).items():
            hits[label] = hits.get(label, 0) + int(count)
    return damage, hits, total


def player_rows(p: object) -> list[TypeRow]:
    """One player's aggregate breakdown, ready to render."""
    damage, hits, total = player_affinity(p)
    return affinity_rows(damage, total, hits)


def pd_md_split(damage: dict | None, total: float) -> tuple[float, float, float]:
    """Merge tagged buckets into ``(pd, md, untagged)`` totals.

    ``pd`` is Physical + Raw; ``md`` is every other tagged school. ``untagged``
    is the remainder of ``total`` past the tagged buckets (below
    ``REMAINDER_EPS`` counts as fully tagged). ``(0, 0, 0)`` when nothing was
    classified at all, so callers hide the split instead of showing zeros.
    """
    buckets = {str(k): float(v) for k, v in (damage or {}).items() if v}
    if not buckets:
        return (0.0, 0.0, 0.0)
    pd = sum(v for k, v in buckets.items() if k in PD_SCHOOLS)
    md = sum(v for k, v in buckets.items() if k not in PD_SCHOOLS)
    untagged = float(total or 0.0) - (pd + md)
    if untagged <= REMAINDER_EPS:
        untagged = 0.0
    return (pd, md, max(0.0, untagged))


def affinity_tooltip_html(rows: list[TypeRow], title: str = "Damage by type") -> str:
    """Multi-line breakdown with amounts and hit counts (hover detail)."""
    lines = [f"{title}:"]
    for r in rows:
        hit_tag = f" · {r.hits} hits" if r.hits else ""
        lines.append(f"&nbsp;&nbsp;{r.label}: <b>{r.amount:,.0f}</b> "
                     f"({r.pct:.1f}%){hit_tag}")
    return "<br>".join(lines)


def pd_md_text(damage: dict | None, total: float) -> str:
    """The merged PD / MD pair as the rail's rich-text label, or `""`.

    Same merge as ``pd_md_split`` (PD = Physical + Raw, MD = every other
    tagged school, `untagged` carried when real) and the same shape as the
    Test Dummy HUD's line — but read from ONE player, and empty when nothing
    was classified so an untagged capture grows no label at all. Percentages
    beside the amounts because the bar already owns the raw split: the text
    adds the share a glance cannot get from segments alone.
    """
    pd, md, untagged = pd_md_split(damage, total)
    if pd <= 0.0 and md <= 0.0:
        return ""
    denom = pd + md + untagged
    pd_c = item_labels.affinity_color("Physical")
    md_c = item_labels.affinity_color("Magic")
    parts = [
        f"<font color=\"{pd_c}\">PD {pd:,.0f} ({pd / denom * 100.0:.0f}%)</font>",
        f"<font color=\"{md_c}\">MD {md:,.0f} ({md / denom * 100.0:.0f}%)</font>",
    ]
    if untagged > 0.0:
        parts.append(f"<font color=\"{theme.MUTED}\">untagged {untagged:,.0f}</font>")
    sep = f"<font color=\"{theme.DIM}\"> &nbsp;·&nbsp; </font>"
    return sep.join(parts)


# --- head-to-head (the Compare view) ---------------------------------------#

@dataclass(frozen=True)
class CompareTypeRow:
    """One damage type as BOTH players dealt it, for a side-by-side table.

    A side with no damage of this type keeps 0.0/0.0 rather than ``None``: the
    table stays rectangular, so a row cannot silently shift the columns under
    it. ``tagged`` is False only for the ``untagged`` remainder row.
    """

    label: str
    color: str
    a_amount: float = 0.0
    a_pct: float = 0.0
    b_amount: float = 0.0
    b_pct: float = 0.0
    a_hits: int = 0
    b_hits: int = 0
    tagged: bool = True

    def delta(self) -> float:
        """Signed A−B, so the table can colour it like the skill rows do."""
        return self.a_amount - self.b_amount


def compare_type_rows(pa: object, pb: object) -> list[CompareTypeRow]:
    """Two players' type buckets merged into one ordered table.

    The UNION is the point, not an intersection: a school only ONE side dealt
    still gets a row, because dropping it would make the two sides describe
    different tables and leave nothing to compare it against.

    Each percentage stays against its OWN player's total, exactly as the single
    -player rows compute it. That is what makes 60/40 and 30/70 comparable —
    renormalizing both sides to 100% of their *tagged* damage would hide the two
    things worth seeing, which are the share of the whole and how much of each
    parse went untagged.

    Ordering matches one player's rows (curated schools, then other tags by
    size, ``untagged`` last) so the same fight does not reorder between the rail
    and this table. Empty when neither side has typed damage — same rule as the
    bar, so an untagged capture grows no table instead of an all-zero one.
    """
    ra = {r.label: r for r in player_rows(pa)}
    rb = {r.label: r for r in player_rows(pb)}
    if not ra and not rb:
        return []

    def _magnitude(label: str) -> float:
        return max(ra[label].amount if label in ra else 0.0,
                   rb[label].amount if label in rb else 0.0)

    labels = [school for school in CURATED if school in ra or school in rb]
    labels += sorted((lbl for lbl in (set(ra) | set(rb))
                      if lbl not in CURATED and lbl != UNTAGGED),
                     key=_magnitude, reverse=True)
    if UNTAGGED in ra or UNTAGGED in rb:
        labels.append(UNTAGGED)

    out: list[CompareTypeRow] = []
    for label in labels:
        a, b = ra.get(label), rb.get(label)
        out.append(CompareTypeRow(
            label=label,
            # A lone side still has a colour; the curated palette falls back to
            # the theme accent for an unrecognised tag.
            color=(a or b).color,
            a_amount=a.amount if a else 0.0,
            a_pct=a.pct if a else 0.0,
            b_amount=b.amount if b else 0.0,
            b_pct=b.pct if b else 0.0,
            a_hits=a.hits if a else 0,
            b_hits=b.hits if b else 0,
            tagged=(a or b).tagged,
        ))
    return out


def compare_type_tooltip(row: CompareTypeRow, name_a: str = "A",
                         name_b: str = "B") -> str:
    """Hover for one comparison row: both sides' amounts and hit counts."""
    def _side(who: str, amount: float, pct: float, hits: int) -> str:
        hit_tag = f" · {hits} hits" if hits else ""
        return f"{who}: <b>{amount:,.0f}</b> ({pct:.1f}%){hit_tag}"

    return "<br>".join([
        f"{row.label} — Δ <b>{row.delta():+,.0f}</b>",
        _side(name_a or "A", row.a_amount, row.a_pct, row.a_hits),
        _side(name_b or "B", row.b_amount, row.b_pct, row.b_hits),
    ])
