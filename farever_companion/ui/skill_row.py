"""Shared per-skill row widget for the DPS meters.

Both the DPS overlay (compact) and the Combat page (detailed) build the same
anatomy:
an icon, a name, a bar, and per-skill stats with a tooltip. This widget
encapsulates that layout once and lets callers pick a density.

Usage::

    row = SkillRow(density="detailed", parent=self)
    layout.addWidget(row)
    row.update(skill_parse, share_pct=42.0, mode="damage")
"""

from __future__ import annotations

import re
from PySide6 import QtCore, QtGui, QtWidgets

from . import theme
from ..data import icons, names, skills
from . import affinity_view
from .components import ElideLabel
import re


# (label, colour, hover) per tag kind. A PLAIN LABEL, styled like the tooltip's
# own lines ("Cooldown: 30s") rather than a round pill: two attempts at a pill
# were both rejected from the field ("some round pills", "instead u put a text
# lable next to the skill"). Flat, transparent background, no border - it reads
# as a caption beside the name and costs the name almost nothing.
TAG_BADGES: dict[str, tuple[str, str, str]] = {
    "item proc": ("⚙ proc", theme.GOLD,
                  "Damage from your gear, not from your rotation"),
    "passive": ("◈ passive", theme.ACCENT_LIGHT, "A skill in your rotation"),
    "summon": ("✦ summon", theme.GOLD, "Your weapon's summon, or its own attacks"),
}
TAG_BADGE_WIDTH = 58      # the label's own width, in px


def strip_tag_suffix(name: str) -> str:
    """Drop a trailing "(Item proc)" / "(Passive)" / "(Summon)" / "(pet)".

    Names that reached the ARCHIVE can already carry one: the tag used to be
    appended to the label, and an archived parse stores what the row was given
    (live screenshot 2026-10-03: the hover card titled "Flaming Weapon (Item
    proc)" while the row beside it carried its own badge). Now that the badge
    exists, a name that still has one is doubled up, so every surface that shows
    a skill NAME strips it first and lets the badge do the talking.
    """
    out = re.sub(r"\s*\((?:Item proc|Passive|Summon|[Pp]et)\)\s*$", "", name or "")
    return out or (name or "")


def apply_tag_badge(lbl: QtWidgets.QLabel, kind: str, name: str = "") -> None:
    """Show `kind` as a plain label beside the skill name, or hide it.

    "(pet)" wins outright: it is tagged upstream from the bridge having SEEN the
    owner entity, so it is a fact rather than an inference, and a row never
    carries two badges. Same rule the text suffix used (`tagged_skill_name`).
    """
    if kind == "summon" and "(pet)" in (name or "").lower():
        kind = ""
    spec = TAG_BADGES.get(kind)
    if not spec:
        lbl.setVisible(False)
        lbl.setText("")
        return
    text, color, tip = spec
    lbl.setText(text)
    lbl.setStyleSheet(
        f"font-size: 10px; font-weight: 700; color: {color};"
        "background: transparent; border: none;")
    lbl.setToolTip(tip)
    lbl.setVisible(True)

# The detailed row's name column ceiling. The skills table's header builds its
# own SKILL cell and MUST use this same number, or the TYPE column under the
# header lands in a different place than the TYPE cell in the rows.
DETAILED_NAME_MAX = 420

# The SHARE cell. The header is now just "%", and the cell holds the number
# ("100%" is 32px at 11px monospace), so this is sized by the cell and not by
# a five-character caption that is no longer there. The header builds its own
# label from this same number or the columns after it sit under the wrong
# captions.
SHARE_WIDTH = 36

# The numeric cells. Every one of these is a FIXED width, and a row is a plain
# QHBoxLayout — so a cell whose content is wider than its fixed width does not
# shrink, it overflows the row and the rightmost column (Avg Hit) is painted
# past the scroll viewport and cut off. The numbers below are therefore the
# MEASURED worst case of what each cell can be asked to paint at 11px
# monospace with the shipped fonts, plus a little slack — not a round guess.
# Re-derive with ai/workspace/dmgtype/probe_cell_text.py if a cell's content
# ever changes. The header builds its own labels from these same numbers.
#
# "999,999,999" is the widest a 32-bit damage total gets before _fmt_span takes
# over, and "999,999" the widest hit count.
TOTAL_WIDTH = 80
HITS_WIDTH = 52

# The AVG HIT cell: "3,118 [2,100-9,800]" — the average, then the min-max range
# the hits actually spanned. Both numbers earn their place: the average alone
# cannot distinguish a steady skill from one that swings, and the range alone
# cannot be read as a typical hit.
#
# 136 is the measured width of the widest cell a real fight produces: the
# four-digit case, "3,118 [2,100-9,800]", at 133px. That is the common case,
# so a narrower cell elides the column the user came here to read — which is
# the bug this number was measured to kill, not to reintroduce at 112px.
#
# The cell is bounded for everything short of a nine-digit average ("1.0b
# [1.0m-9.8m]", 161px), which no real fight produces. If one ever did, the
# cell elides and the exact values are in the row tooltip and are total/hits.
AVG_WIDTH = 136


# The DETAILED row's name ceiling, in CHARACTERS as well as pixels. The pixel
# ceiling (DETAILED_NAME_MAX) is what stops the layout; this one is what stops
# one 39-character passive name from setting the width of the whole rail. Of the
# 164 real skill names in this player's own archives, 30 characters is enough
# for all but a handful, and capping there takes 29px off the rail's minimum
# for free. The full name stays in the row tooltip.
#
# Compact rows (the overlay) are left alone: they are not in a table with six
# numeric columns beside them, and the overlay has its own width budget.
NAME_MAX_CHARS = 30


def skill_row_tag(skill_id: str) -> str:
    """The tag for `skill_id`'s row, or "" — WHY this damage appeared.

    Three kinds, which the meter otherwise shows identically as one flat list:

    - `(Item proc)` — the damage comes from GEAR you own, not from your
      rotation. Bloodrage Aura, Zeal, Water Veil: you never pressed anything.
      Named from the granting item, because "which axe" is the question a
      player asks when their damage jumps after a gear change.
    - `(Passive)` — a passive in your rotation, granted by your build.
    - `(Summon)` — a summon family: the cast AND the pet's own attacks
      (`SummonBee_HoneyBolt`, `Summon_Imp_Auto`, `Staff_SummonDemon_Combo`).
      Reported live 2026-10-03 for Summon Bee Honey Bolt, which the meter
      showed as an ordinary base attack you pressed.
    - `(pet)` — appended upstream by the attribution layer, only on rows the
      bridge proved came from a pet entity, so it stays the stronger claim.

    The two passives are separated by `skills.item_proc_source`, which is a
    LOOKUP rather than a guess and says so in its docstring: gear lists a
    weapon's whole rotation, so "an item grants this skill" is true of every
    base attack you swing, and the resolver has to exclude them explicitly.

    Ordered most-specific first, and idempotent at the call site: the label is
    rebuilt on every identity change, so a second pass must not double a tag.
    """
    if skills.item_proc_source(skill_id):
        return "item proc"
    if skills.skill_type(skill_id) == "Passive":
        return "passive"
    if skills.is_summon(skill_id):
        return "summon"
    return ""


def tagged_skill_name(skill_id: str, name: str, limit: int = 0) -> str:
    """The row's name, tagged with what KIND of thing produced the damage.

    A passive's ticks and a pet's hits arrive on the meter looking exactly like
    a spell the player cast, which is why "Shattered Rage" read as just another
    rotation entry (reported live 2026-10-03). The sheet already knows: a
    passive is `nature` 5, and `skills.skill_type` decodes it.

    Summons already carry their tag — the attribution layer appends "(pet)" to
    the name — so this only adds the one that was missing. An item proc gets
    `(Item proc)` INSTEAD of `(Passive)`: both are passives, and saying which
    is the whole point (see `skill_row_tag`).

    Idempotent, so a name that already carries a tag is left alone: the label
    is rebuilt on every identity change, and a second pass must not produce
    "(Passive) (Passive)".

    `limit` caps the NAME, never the tag. Tagging first and truncating after
    would silently eat the tag on exactly the long names that need it, which
    is the one case where the label matters most.
    """
    label = name or ""
    tag = ""
    kind = skill_row_tag(skill_id)
    # "(pet)" already says summon, and stronger (the bridge saw the owner) —
    # adding "(Summon)" beside it reads as two different facts.
    if kind == "summon" and "(pet)" in label.lower():
        kind = ""
    if kind and f"({kind})" not in label.lower():
        tag = {"item proc": " (Item proc)",
               "passive": " (Passive)",
               "summon": " (Summon)"}[kind]
    if limit > 0:
        label = _cap_name(label, max(1, limit - len(tag)))
    return f"{label}{tag}"


def _cap_name(name: str, limit: int) -> str:
    if not name or len(name) <= limit:
        return name
    return name[:limit - 1].rstrip() + "…"


def _trim_zero(s: str) -> str:
    """"12.0m" -> "12m", "1.0b" -> "1b". The suffix is kept: the ".0" is the
    part that costs a column, and the reader still needs to know the unit.

    Every character in this table is a character the skill name does not get.
    """
    i = s.find(".0")
    return s if i < 0 or i + 2 != len(s) - 1 else s[:i] + s[-1]


def _skill_icon(skill_id: str, size: int = 18, fallback_color: str | None = None) -> QtWidgets.QLabel:
    """Icon label for a skill: resolves pixmap, else a theme glyph.

    A skill with no atlas art gets a glyph that says something: a summon's
    missing icon becomes an orb rather than a zap, because six of the twenty
    summon-family rows ship no art (Summon Bee Honey Bolt among them — no item
    grants that skill either, so the weapon-icon fallback has nothing to
    resolve and cannot be used).

    The glyph list is ordered and the FIRST RESOLVING one wins. `zap` alone was
    the whole list until 2026-10-03, and it is not in the shipped minimap
    atlas or the master icons.svg, so `ui_icon` returned a null pixmap and the
    fallback drew nothing at all. A null check per candidate is what keeps the
    next missing glyph from silently re-hollowing every iconless row.
    """
    ico = QtWidgets.QLabel()
    ico.setFixedSize(size, size)
    clean_id = re.sub(r"\s*\([Pp]et\)$", "", skill_id or "").strip()
    pm = None
    if clean_id:
        cands = [clean_id, clean_id.lower(), clean_id.replace(" ", "_"), clean_id.replace("_", " ")]
        for cand in cands:
            if icons.has_icon("skills", cand):
                pm = icons.pixmap("skills", cand, size)
                break
            if icons.has_icon("items", cand):
                pm = icons.pixmap("items", cand, size)
                break
    if pm and not pm.isNull():
        ico.setPixmap(pm)
    else:
        ico.setPixmap(_fallback_glyph(clean_id, fallback_color, size))
    return ico


# Glyphs tried, in order, when a skill has no atlas icon. Verified to resolve
# in this checkout (ai/workspace/buffy: `ui_icon(...).isNull()`); an unresolvable
# name is a NULL pixmap, so a bad entry here costs a step, not the icon.
_FALLBACK_GLYPHS = ("zap", "sparkdust")
_SUMMON_GLYPHS = ("orb", "sparkdust")


def _fallback_glyph(skill_id: str, fallback_color: str | None, size: int):
    """A theme-tinted glyph for an iconless skill; summon families get an orb."""
    names_ = _SUMMON_GLYPHS if skills.is_summon(skill_id) else _FALLBACK_GLYPHS
    color = fallback_color or (theme.ACCENT_LIGHT if skills.is_summon(skill_id)
                               else theme.ACCENT)
    for name in names_:
        pm = icons.ui_icon(name, color, max(8, size - 2))
        if pm is not None and not pm.isNull():
            return pm
    return icons.ui_icon("targetdummy", color, max(8, size - 2))


class SkillRow(QtWidgets.QWidget):
    """One per-skill row: icon · name · value · bar · (optional hits/crit/avg).

    ``density`` controls which columns appear:

    * ``"compact"`` — icon, elided name, single stat line, bar, pct.
    * ``"detailed"`` — icon, elided name, total, bar, hits, crit, avg.

    ``show_type`` adds the damage-type TYPE cell to compact rows (the Top DPS
    meter and the Test Dummy HUD), sized for an overlay. Detailed rows always
    have it. There is ONE damage-type cell per row by design: the school name
    already names the merged side (Physical/Raw read PD, every other school
    reads MD), so a second PD/MD tag beside it said the same thing twice.
    """

    # Width of the compact TYPE cell (show_type): "Physical" at 10px is the
    # widest school name the game ships; anything longer elides.
    TYPE_COMPACT_WIDTH = 54

    def __init__(self, density: str = "detailed", parent=None,
                 show_type: bool = False):
        super().__init__(parent)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding,
                            QtWidgets.QSizePolicy.Preferred)
        self._density = density
        self._layout = QtWidgets.QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(4 if density == "compact" else 6)

        # Columns that exist in both densities.
        self._icon_lbl = _skill_icon("", size=(14 if density == "compact" else 20))
        self._layout.addWidget(self._icon_lbl)

        # Name is the flexible column.
        max_name = (118 if density == "compact" else DETAILED_NAME_MAX)
        self._name_lbl: QtWidgets.QLabel = ElideLabel("", max_name)
        self._name_lbl.setStyleSheet(
            f"font-size: {10 if density == 'compact' else 12}px;"
            f"font-weight: 600; color: {theme.TEXT};"
        )
        self._name_lbl.setSizePolicy(QtWidgets.QSizePolicy.Ignored,
                                      QtWidgets.QSizePolicy.Preferred)
        self._layout.addWidget(self._name_lbl, 1)
        # The tag is a BADGE beside the name, not words inside it. Reported live
        # 2026-10-03: "Envenom (Passive) ... thats just text not a icon looking
        # tag button". Suffixing the name also ate the name's own budget, so the
        # badge takes that width back and the row reads name + pill.
        self._tag_lbl = QtWidgets.QLabel("")
        self._tag_lbl.setObjectName("SkillTag")
        self._tag_lbl.setVisible(False)
        self._tag_lbl.setAlignment(QtCore.Qt.AlignCenter)
        self._tag_lbl.setFixedWidth(TAG_BADGE_WIDTH)
        self._layout.addWidget(self._tag_lbl, 0)

        # The damage-type cell: the name of the school this skill deals,
        # between the skill name and its total. One column, not one per
        # school — a weapon deals one school, the table already has a TOTAL,
        # and a column per school does not fit beside the name in the 620px
        # rail. Compact rows opt in with ``show_type`` (the Top DPS meter and
        # the Test Dummy HUD): same cell, overlay-sized.
        self._type_lbl: QtWidgets.QLabel | None = None
        self._type: affinity_view.NamedType | None = None
        if density == "detailed" or show_type:
            tl = QtWidgets.QLabel("")
            tl.setObjectName("SkillType")
            if density == "detailed":
                tl.setFixedWidth(affinity_view.TYPE_COLUMN_WIDTH)
            else:
                tl.setFixedWidth(self.TYPE_COMPACT_WIDTH)
            tl.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
            tl.setStyleSheet(f"font-size: 11px; color: {theme.DIM};")
            self._type_lbl = tl
            self._layout.addWidget(tl)

        # The compact PD/MD tag used to sit here beside the TYPE cell: two
        # letters naming the merged side. It is gone — the school name in the
        # TYPE cell already says which side a skill deals ("Physical" is PD,
        # "Fire"/"Magic" is MD), so the pair duplicated one another and cost
        # 26px of a HUD row's name column. The merged PD / MD NUMBERS live on
        # the Test Dummy HUD's own line, where there is no per-skill column to
        # duplicate (see affinity_view.pd_md_text).

        # The click callback (detailed density only): when wired, clicking
        # the row opens the skill's info popup (skill effect + the weapon
        # it comes from). Overlay rows never wire it.
        self._on_skill_click = None
        self._skill_id = ""

        # The primary value label.
        self._total_lbl = QtWidgets.QLabel("")
        self._total_lbl.setObjectName("SkillTotal")
        self._total_lbl.setStyleSheet(
            f"font-size: {10 if density == 'compact' else 12}px;"
            f"font-weight: {'600' if density == 'compact' else '800'};"
            f"color: {theme.TEXT}; font-family: monospace;"
        )
        self._total_lbl.setAlignment(
            QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        # Fixed, not a minimum: a cell that can grow takes the space from
        # whatever is left, and in this row what is left is the name. The
        # compact row keeps its old minimum — the overlay has no name column
        # to squeeze, and its width is a separate decision.
        if density == "compact":
            self._total_lbl.setMinimumWidth(54)
        else:
            self._total_lbl.setFixedWidth(TOTAL_WIDTH)
        self._layout.addWidget(self._total_lbl)

        # The share cell. Compact (the overlay) keeps a progress bar, which is
        # the right shape for a glanceable proportion in a narrow window.
        # Detailed (the Combat table) states the number instead: a filled bar
        # with empty groove beside it reads as TWO segments, and next to a
        # TOTAL column a number is the consistent thing.
        self._bar = None
        self._share_lbl = None
        if density == "compact":
            self._bar = QtWidgets.QProgressBar()
            self._bar.setObjectName("SkillBar")
            self._bar.setFixedWidth(46)
            self._bar.setFixedHeight(3)
            self._bar.setTextVisible(False)
            self._bar.setRange(0, 100)
            self._layout.addWidget(self._bar)
        else:
            self._share_lbl = QtWidgets.QLabel("")
            self._share_lbl.setObjectName("SkillShare")
            self._share_lbl.setFixedWidth(SHARE_WIDTH)
            self._share_lbl.setAlignment(
                QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            self._share_lbl.setStyleSheet(
                f"font-size: 11px; color: {theme.DIM}; font-family: monospace;")
            self._share_lbl.setToolTip("This skill's share of the player's damage")
            self._layout.addWidget(self._share_lbl)

        # Optional columns, only in detailed mode.
        self._hits_lbl = None
        self._crit_lbl = None
        self._avg_lbl = None
        if density == "detailed":
            self._hits_lbl = QtWidgets.QLabel("")
            self._hits_lbl.setObjectName("SkillHits")
            self._hits_lbl.setFixedWidth(HITS_WIDTH)
            self._hits_lbl.setAlignment(
                QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            self._hits_lbl.setStyleSheet(
                f"font-size: 11px; color: {theme.DIM}; font-family: monospace;"
            )
            self._layout.addWidget(self._hits_lbl)

            self._crit_lbl = QtWidgets.QLabel("")
            self._crit_lbl.setObjectName("SkillCrit")
            self._crit_lbl.setFixedWidth(38)
            self._crit_lbl.setAlignment(
                QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            self._crit_lbl.setStyleSheet(
                f"font-size: 11px; color: {theme.ORANGE}; font-family: monospace;"
            )
            self._layout.addWidget(self._crit_lbl)

            # ElideLabel, not QLabel: a fixed-width QLabel paints text wider
            # than itself straight over the next column, and the scroll area
            # then cuts the tail. set_full() below, never setText(), so the
            # elide actually re-runs on every refresh.
            self._avg_lbl = ElideLabel("", AVG_WIDTH, show_tooltip=False)
            self._avg_lbl.setObjectName("SkillAvg")
            self._avg_lbl.setFixedWidth(AVG_WIDTH)
            self._avg_lbl.setAlignment(
                QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            self._avg_lbl.setStyleSheet(
                f"font-size: 11px; color: {theme.DIM}; font-family: monospace;"
            )
            self._layout.addWidget(self._avg_lbl)

        # Percentage chip (only in compact mode).
        if density == "compact":
            self._pct_lbl = QtWidgets.QLabel("")
            self._pct_lbl.setObjectName("SkillPct")
            self._pct_lbl.setFixedWidth(30)
            self._pct_lbl.setAlignment(
                QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            self._pct_lbl.setStyleSheet(
                f"font-size: 10px; color: {theme.DIM}; font-family: monospace;"
            )
            self._layout.addWidget(self._pct_lbl)
        else:
            self._pct_lbl = None

    # --- public API -------------------------------------------------------#

    def set_skill(self, skill_id: str, name: str) -> None:
        """Set the icon + name (call once or when the skill identity changes)."""
        self._skill_id = skill_id or ""
        new_icon = _skill_icon(skill_id,
                               size=(14 if self._density == "compact" else 20))
        self._layout.replaceWidget(self._icon_lbl, new_icon)
        self._icon_lbl.deleteLater()
        self._icon_lbl = new_icon
        if getattr(self, "_on_skill_click", None) is not None and self._density == "detailed":
            self._icon_lbl.setCursor(QtCore.Qt.PointingHandCursor)
        self._name_lbl.set_full(_cap_name(strip_tag_suffix(name or ""),
                                          NAME_MAX_CHARS))
        apply_tag_badge(self._tag_lbl, skill_row_tag(skill_id), name or "")

    def update(self, sp, share_pct: float, mode: str = "damage") -> None:
        """Refresh one row from a SkillParse + its share of the parent total.

        ``share_pct`` is a PERCENTAGE (0-100) — the unit
        :meth:`SkillParse.share_pct` returns, and what every caller passes.
        It used to be guessed at, on the theory that a value ``<= 1.0`` must be
        a ratio instead, but that cannot tell a 0.9% share from a 0.9 ratio:
        a skill doing 0.99% of a player's damage drew a bar at 99% wide, and
        an archived fight full of procs and statuses showed a rail of them.
        The guess is gone rather than tuned; a ratio was never actually passed.

        ``mode`` is one of ``"damage"``, ``"healing"``, ``"both"`` and
        controls how the stat line and tooltip are rendered.
        """
        is_heal = (mode == "healing")
        is_both = (mode == "both")

        pct_val = max(0.0, min(100.0, float(share_pct)))

        if is_heal:
            total = sp.heals
            hits = sp.heal_count
            min_val = sp.min_heal
            max_val = sp.max_heal
            avg_val = sp.heals / max(1, sp.heal_count)
            stat = (f"{sp.heals:,.0f} · {sp.heal_count} heals"
                    if self._density == "compact" else f"{sp.heals:,.0f}")
            tooltip = (
                f"<b>{sp.name}</b> ({sp.skill_id})<br>"
                f"Heals: <b>{sp.heals:,.1f}</b> ({pct_val:.1f}%)<br>"
                f"Casts: <b>{sp.heal_count}</b><br>"
                f"Min: <b>{self._fmt_minmax(min_val)}</b> | "
                f"Avg: <b>{avg_val:,.1f}</b> | "
                f"Max: <b>{self._fmt_minmax(max_val)}</b>"
            )
            # In detailed mode the hits column is heal-counts, crit/avg as appropriate.
            if self._hits_lbl is not None:
                self._hits_lbl.setText(f"{hits}")
                self._crit_lbl.setText("—")
                self._avg_lbl.set_full(
                    self._fmt_avg_cell(avg_val, min_val, max_val))
            if self._pct_lbl is not None:
                self._pct_lbl.setText(f"{pct_val:.0f}%")
            self.set_share(pct_val)
            self._total_lbl.setText(stat)  # compact: "6,000 · 4 heals"
            # The type columns are a damage-channel split; a heal row has none.
            self.set_types([])
            self.setToolTip(tooltip)
            return

        if is_both:
            uses = sp.hit_count + sp.heal_count
            if sp.damage > 0 and sp.heals > 0:
                stat = f"D {sp.damage:,.0f} · H {sp.heals:,.0f}"
            else:
                crit_info = f"{sp.crit_pct:.0f}%" if sp.crit_count > 0 else "—"
                stat = f"{sp.total:,.0f}"
                avg_val = sp.avg_hit()
                # Every one of these cells is detailed-density only; a compact
                # row has none of them, hence the single guard.
                if self._hits_lbl is not None:
                    self._hits_lbl.setText(f"{uses}")
                    self._crit_lbl.setText(crit_info)
                    self._avg_lbl.set_full(
                        self._fmt_avg_cell(avg_val, sp.min_hit, sp.max_hit))
            if self._pct_lbl is not None:
                self._pct_lbl.setText(f"{pct_val:.0f}%")
            tooltip = (
                f"<b>{sp.name}</b> ({sp.skill_id})<br>"
                f"Combined: <b>{sp.total:,.1f}</b> ({pct_val:.1f}%)<br>"
                f"Damage: <b>{sp.damage:,.1f}</b> ({sp.hit_count} hits) | "
                f"Heals: <b>{sp.heals:,.1f}</b> ({sp.heal_count} casts)"
            )
            self._total_lbl.setText(stat)
            self.set_share(pct_val)
            # Combined rows show a damage figure too, but the footer under them
            # is a heal total, so the cells stay blank rather than implying the
            # type split adds up to the number beside it.
            self.set_types([])
            self.setToolTip(tooltip)
            return

        # --- damage mode ---
        crit_info = f"{sp.crit_pct:.0f}%" if sp.crit_count > 0 else "—"
        min_str = self._fmt_minmax(sp.min_hit)
        max_str = self._fmt_minmax(sp.max_hit)
        stat = f"{sp.damage:,.0f} · {sp.hit_count}" if self._density == "compact" else f"{sp.damage:,.0f}"
        if self._hits_lbl is not None:
            self._hits_lbl.setText(f"{sp.hit_count}")
            self._crit_lbl.setText(crit_info)
            self._avg_lbl.set_full(
                self._fmt_avg_cell(sp.avg_hit(), sp.min_hit, sp.max_hit))
        if self._pct_lbl is not None:
            self._pct_lbl.setText(f"{pct_val:.0f}%")
        tooltip = (
            f"<b>{strip_tag_suffix(sp.name)}</b> ({sp.skill_id})<br>"
            f"Total: <b>{sp.damage:,.1f}</b> ({pct_val:.1f}%)<br>"
            f"Hits: <b>{sp.hit_count}</b> | Crits: <b>{sp.crit_count}</b> "
            f"({sp.crit_pct:.1f}%)<br>"
            f"Min: <b>{min_str}</b> | Avg: <b>{sp.avg_hit():,.1f}</b> | "
            f"Max: <b>{max_str}</b>"
        )
        # The gear line ("From your gear: ...") was REMOVED 2026-10-03: the row
        # now carries the kind as a plain label beside the name, and the item it
        # came from is a second statement of the same fact inside a hover the
        # player reads for numbers (reported: "thats something we made today
        # that i never wanted there"). The item is still named in the skill
        # popup's gear section, which is where a player goes looking for it.

        subs = getattr(sp, "subskills", None)
        if subs:
            # Damage folded in from this skill's own derived rows - the bleed a
            # Bonethrow applies, a passive's status ticks. They share the
            # parent's display name, which is exactly why they were folded in;
            # naming them here keeps the merge inspectable rather than a row
            # silently getting bigger.
            parts = " · ".join(
                f"{names.subskill_label(cid, sp.skill_id) or cid}: {child.damage:,.0f}"
                for cid, child in sorted(
                    subs.items(), key=lambda kv: -kv[1].damage))
            tooltip += (f"<br>Includes its own effects: <b>{parts}</b>")
        type_rows = affinity_view.affinity_rows(
            getattr(sp, "affinity_damage", None), sp.damage,
            getattr(sp, "affinity_hits", None))
        if type_rows:
            tooltip += "<br>" + affinity_view.affinity_tooltip_html(type_rows)
        self.set_types(type_rows)
        self._total_lbl.setText(stat)
        self.set_share(pct_val)
        self.setToolTip(tooltip)

    def set_types(self, rows) -> None:
        """Name this row's damage type from its own ``affinity_rows``.

        ``rows`` is what :func:`affinity_view.affinity_rows` returns for the
        skill. A skill that deals one school shows that school in its own
        colour; a skill that genuinely splits shows ``Physical +2`` rather than
        naming the top school as if the skill were pure. No rows means nothing
        was classified — an older bridge, or a non-damage channel — and the
        cell stays blank rather than claiming a type that was not read.
        """
        lbl = self._type_lbl
        if lbl is None:
            return
        named = affinity_view.dominant_type(rows or [])
        self._type = named
        # Compact (overlay) rows: same content, 10px to fit the meter's row
        # height. Detailed keeps its 11px rail styling.
        size_px = 10 if self._density == "compact" else 11
        # The share number is coloured by the same school as the TYPE cell, so
        # the two say one thing: this percentage is fire damage. It used to be
        # coloured by skill id, which put a third colour on the row and made
        # the number look like it belonged to whatever the skill's own hue was.
        # Called here rather than by the caller because the caller sets the
        # colour immediately after update() and would overwrite it.
        self.set_share_color(named.color if named else
                             theme.skill_color(self._skill_id))
        if named is None:
            lbl.setText("")
            lbl.setToolTip("")
            lbl.setStyleSheet(f"font-size: {size_px}px; color: {theme.DIM};")
            return
        # Rich text only for the split case, so the "+2" can sit back in the
        # dim colour instead of shouting in the school's.
        lbl.setText(
            f"{named.label} <span style='color:{theme.DIM}'>+{named.others}</span>"
            if named.others else named.label)
        lbl.setToolTip(
            f"{named.label}: {named.pct:.0f}% of this skill"
            + (f" (+{named.others} more)" if named.others else ""))
        lbl.setStyleSheet(
            f"font-size: {size_px}px; font-weight: 700; color: {named.color};")

    def type_caption(self) -> str:
        """The TYPE cell as plain text, e.g. ``"Physical"`` or ``"Fire +1"``."""
        return self._type.caption() if self._type is not None else ""

    def set_on_skill_click(self, on_click) -> None:
        """Wire (or clear) the skill-click callback for detailed rows.

        When set, a left click anywhere on the row fires
        ``on_click(skill_id, row)`` — opening the skill-info popup — and the
        row shows a pointing-hand cursor. Compact (overlay) rows are never
        clickable and keep the arrow cursor.

        The whole row is the hit target on purpose: the name column is the
        flexible one and collapses to zero width when the row is narrow (or
        laid out standalone), so a hit test against ``_name_lbl``'s geometry
        misses clicks that are visibly on the row.
        """
        self._on_skill_click = on_click
        is_clickable = (on_click is not None and self._density == "detailed")
        cursor = QtCore.Qt.PointingHandCursor if is_clickable else QtCore.Qt.ArrowCursor
        self.setCursor(cursor)
        self._icon_lbl.setCursor(cursor)
        self._name_lbl.setCursor(cursor)

    def mouseReleaseEvent(self, e: QtGui.QMouseEvent) -> None:
        if (e.button() == QtCore.Qt.LeftButton
                and self._density == "detailed"
                and self._on_skill_click is not None):
            self._on_skill_click(self._skill_id, self)
            e.accept()
            return
        super().mouseReleaseEvent(e)

    def set_share(self, pct: float) -> None:
        """Write this row's share of the player's total.

        Detailed rows show the number, compact rows fill the bar (the compact
        row already carries the number in its own percentage chip). ``pct`` is
        a percentage, already clamped to 0-100 by the caller.
        """
        if self._share_lbl is not None:
            self._share_lbl.setText(f"{pct:.0f}%")
        if self._bar is not None:
            self._bar.setValue(int(pct))

    def set_share_color(self, color: str) -> None:
        """Tint the share indicator with the skill's colour.

        The bar and the number are the same signal in different densities, so
        this paints whichever one this row has.
        """
        if getattr(self, "_current_share_color", None) == color:
            return
        self._current_share_color = color
        if self._bar is not None:
            self._bar.setStyleSheet(
                f"QProgressBar {{ background: {theme.SURFACE}; border: none; "
                f"border-radius: 1px; }}"
                f"QProgressBar::chunk {{ background: {color}; "
                f"border-radius: 1px; }}"
            )
        if self._share_lbl is not None:
            self._share_lbl.setStyleSheet(
                f"font-size: 11px; font-weight: 700; color: {color}; "
                f"font-family: monospace;")

    def setVisible(self, visible: bool) -> None:
        super().setVisible(visible)

    def set_indent(self, px: int) -> None:
        """Push the row's content right by ``px`` - a NESTED row.

        A folded sub-skill is drawn as its own row under its parent (the
        parent's numbers already include it), so the only thing separating
        the two is position: without this the meter shows "Bonethrow" and,
        directly beneath it, "Bonethrow" again.
        """
        self._layout.setContentsMargins(px, 0, 0, 0)

    @staticmethod
    def _fmt_minmax(v: float) -> str:
        return f"{v:,.0f}" if v != float("inf") else "—"

    @staticmethod
    def _fmt_span(v: float) -> str:
        """A hit range endpoint, abbreviated so the cell stays bounded.

        The AVG HIT cell is a fixed width and a QLabel does not clip: text
        wider than the cell paints past it and the scroll area cuts the tail
        off at the viewport edge. "4,520 [12,000-41,000]" already overflows
        104px, and a six-figure hit made it 150px. Abbreviating only the
        range endpoints bounds the whole cell at ~100px while leaving the
        average itself in full, since that is the number being read.

        The exact values are in the row tooltip, so nothing is lost.
        """
        if v == float("inf"):
            return "—"
        av = abs(v)
        # The billions tier matters: without it a 999,999,999 hit reads
        # "1000.0m" — wider than the digits it replaced, which is the exact
        # failure this function exists to prevent. The trailing ".0" goes for
        # the same reason: "88.0k" is four characters of column for a number
        # the reader already knows is eighty-eight thousand.
        if av >= 1_000_000_000:
            return _trim_zero(f"{v / 1_000_000_000:.1f}b")
        if av >= 1_000_000:
            return _trim_zero(f"{v / 1_000_000:.1f}m")
        if av >= 10_000:
            return f"{v / 1000:.0f}k"
        return f"{v:,.0f}"

    @classmethod
    def _fmt_avg_cell(cls, avg: float, lo: float, hi: float) -> str:
        """The whole Avg Hit cell: "3,118 [2,100-9,800]".

        One place because the three numbers have to agree on WHICH of them
        abbreviates. Only the range endpoints do: the average is the number
        being read, and "11.3k" is not what a reader wants where "11,300" fits
        in the cell. The exact average is in the row tooltip either way.
        """
        return f"{avg:,.0f} [{cls._fmt_span(lo)}–{cls._fmt_span(hi)}]"


# --- nested sub-skill rows ---------------------------------------------------#

SUB_ROW_INDENT = 12  # px a folded sub-skill row is pushed right of its parent


def sub_row_key(parent_id: str, child_id: str) -> str:
    """The widget key for a nested row.

    Composite because a folded child's id is NOT in `player.skills` (the fold
    pops it), so it needs a key space of its own that cannot collide with a
    real skill id.
    """
    return f"{parent_id}\x00{child_id}"


def sync_subskill_rows(sp, widgets: dict, make_row, indent: int = SUB_ROW_INDENT,
                       mode: str = "damage", total_base: float | None = None,
                       on_click=None, color_fn=None) -> list[str]:
    """Build/reuse/refresh the NESTED rows for one skill's folded sub-skills.

    Every skill surface groups derived rows the same way (see the fold in
    `dps_data.record_hit`), so every surface must also SHOW them the same way
    - otherwise the meter explains a row the Combat page does not. This is the
    shared half of that, so the two cannot drift apart again.

    A folded sub-skill is damage the player produced with this skill, so it
    gets a real row rather than living only in the tooltip - nested under its
    parent, because the parent's total already counts it.

    ``widgets`` is the caller's id->widget cache; it is both read (reuse) and
    written (create), so the caller can hide whatever is left over. Returns
    the keys created/updated, in display order, for the caller's ordering
    bookkeeping.

    ``make_row(parent_widget)`` builds a fresh widget of the caller's density.
    ``color_fn(skill_id)`` applies the caller's own share colour (the overlay
    tints a nested row by its child id; the Combat page leaves the colour to
    `update`, which reads the damage type).
    ``mode='healing'`` draws nothing: `subskills` is the DAMAGE channel, and a
    nested damage number under a healing list is from the wrong channel.
    """
    if mode == "healing":
        return []
    keys: list[str] = []
    for cid, child in sorted(sp.subskills.items(), key=lambda kv: -kv[1].damage):
        key = sub_row_key(sp.skill_id, cid)
        row = widgets.get(key)
        if row is None:
            row = make_row(None)
            row.set_indent(indent)
            widgets[key] = row
        else:
            row.show()
        # The child shares its parent's DISPLAY name by construction - that is
        # exactly what the fold keyed on - so the id tail is the only thing
        # that tells the two rows apart.
        label = names.subskill_label(cid, sp.skill_id) or cid
        if getattr(row, "_skill_label", None) != (cid, label):
            row.set_skill(cid, label)
            row._skill_label = (cid, label)
        share = child.share_pct(total_base if total_base is not None
                                else max(child.damage, 1.0))
        row.update(child, share_pct=share, mode=mode)
        if on_click is not None:
            row.set_on_skill_click(
                lambda _sid, _r, c=child: on_click(c, _r))
        if color_fn is not None:
            row.set_share_color(color_fn(cid))
        keys.append(key)
    return keys
