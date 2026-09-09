"""The per-skill info popup opened by clicking a skills-rail row.

Split out of `combat_rail.py` to keep that mixin under the `ui/` line budget.
It is a self-contained widget: it reads the skill sheet and renders one card.
Nothing in the rail's own layout lives here.
"""
from __future__ import annotations

from PySide6 import QtCore, QtWidgets

from .. import theme
from .. import components as C
from ..skill_row import strip_tag_suffix
from ...data import items as idata, names, skills as skdata
from .items.skills_section import skill_color

# Weapon-family chip glyphs (family name -> emoji) for the "From Weapon"
# rows below. Presentation only: the family NAMES come from the items sheet,
# this is how each is drawn. An absent family renders with no glyph rather
# than a placeholder - an empty slot reads as "no family recorded", which is
# what the sheet said.
_WEAPON_FAMILY_GLYPHS = {
    "Sword": "⚔", "Great Sword": "⚔", "Dual Swords": "⚔",
    "Shield": "\U0001f6e1", "Axe": "\U0001fa93", "Great Axe": "\U0001fa93",
    "Dual Axes": "\U0001fa93", "Mace": "\U0001f528", "Great Mace": "\U0001f528",
    "Dual Maces": "\U0001f528", "Dagger": "\U0001f5e1", "Daggers": "\U0001f5e1",
    "Fists": "\U0001f44a", "Spear": "\U0001f531", "Staff": "✨",
    "Scepter": "✨", "Bow": "\U0001f3f9", "Thrown": "\U0001f3af",
    "Crescent": "\U0001f319", "Halos": "\U0001f4ab", "Book": "\U0001f4d6",
}


def _chip(text: str, color: str) -> QtWidgets.QLabel:
    """A header chip: small caps in ``color`` on a tint of it.

    One place because the header grew a second chip and the two must not drift
    apart visually - the TYPE chip and the ITEM PROC chip sit side by side, so
    a difference in padding or letter-spacing would read as a different kind of
    badge rather than the same one saying different things.
    """
    chip = QtWidgets.QLabel(text)
    chip.setObjectName("PopupChip")
    chip.setStyleSheet(
        f"color:{color};background:{theme.with_alpha(color, 16)};"
        f"border:1px solid {theme.with_alpha(color, 70)};"
        "border-radius:4px;padding:2px 6px;font-weight:700;"
        "font-size:10px;letter-spacing:1px;")
    return chip


class _SkillWeaponPopup(QtWidgets.QFrame):
    """Full skill detail for a skills-rail row, opened by clicking the row."""

    def __init__(self, sp, parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        self.setWindowFlags(QtCore.Qt.Popup)
        self.setAttribute(QtCore.Qt.WA_StyledBackground, True)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        sid = getattr(sp, "skill_id", "") or ""
        self.skill_id = sid
        typ = skdata.skill_type(sid)
        col = skill_color(typ)
        self.setStyleSheet(f"QFrame{{border-left:3px solid {col};}}")
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(5)
        lay.setSizeConstraint(QtWidgets.QLayout.SetFixedSize)
        self.setMinimumWidth(300)
        self.setMaximumWidth(360)

        # header: name + type chip
        hd = QtWidgets.QHBoxLayout()
        hd.setSpacing(6)
        # The name, without any tag baked into it: an archived parse can carry
        # a trailing "(Item proc)" from when the tag WAS the label, and the
        # row beside this card now shows a badge for the same fact. Stripped
        # here so the card cannot say it twice (live 2026-10-03).
        title = strip_tag_suffix(sp.name) or sid or "Skill"
        nm = QtWidgets.QLabel(title)
        nm.setWordWrap(True)
        nm.setStyleSheet(
            f"color:{col};font-weight:700;font-size:14px;background:transparent;")
        hd.addWidget(nm, 1)
        # ONE kind chip, most specific first. This module once claimed the
        # duplicate was already solved by demoting the item-proc case out of
        # the TYPE chip - but the TYPE chip is rendered ABOVE, straight from
        # `skill_type`, and `skill_type` returns "Passive" for a proc, so the
        # card showed PASSIVE beside ITEM PROC anyway (live 2026-10-03). The
        # collision is therefore resolved below, where the TYPE chip is chosen.
        kind = ("ITEM PROC" if skdata.item_proc_source(sid)
                else "PASSIVE" if skdata.skill_type(sid) == "Passive"
                else "SUMMON" if skdata.is_summon(sid) else "")
        # Suppress the TYPE chip only when it would REPEAT the kind chip, i.e.
        # when it reads PASSIVE - an item proc is usually typed Passive, and
        # PASSIVE beside ITEM PROC is one fact printed twice. But `skill_type`
        # is not always Passive for a proc row: some read Status, and then the
        # TYPE chip carries a fact the kind chip does not, so it stays. Keying
        # the suppression on the kind ("always hide the type") instead of on
        # the type's own text lost that one, caught by
        # test_a_derived_rows_popup_names_the_skill_it_belongs_to.
        if kind and typ.upper() == "PASSIVE":
            typ = ""
        if typ:
            hd.addWidget(_chip(typ.upper(), col), 0, QtCore.Qt.AlignTop)
        if kind:
            hd.addWidget(_chip(kind, theme.GOLD if kind != "PASSIVE"
                               else theme.ACCENT_LIGHT), 0, QtCore.Qt.AlignTop)
        # Which skill this derived row belongs to. A bleed, a slow, a DoT tick
        # — each is its own row in the sheet with its own TYPE chip, and on its
        # own it reads as a separate skill the player never used. Naming the
        # parent is what ties it back: `Axe_Boomerang_Skill1_Status` is STATUS,
        # and it is Bonethrow's bleed, not something else you cast.
        #
        # Self-gating: `parent_skill_id` returns None for a top-level skill
        # (verified — Bonethrow, Swarm Cleave, Prayer: Smite all return None),
        # so this chip only ever appears on a genuine derived row.
        parent = names.parent_skill_id(sid)
        if parent and parent != sid:
            pname = names.skill_name(parent) or parent
            # Suppressed when the parent carries the CARD'S OWN NAME. The sheet
            # reuses one name across a skill and its derived rows, so the chip
            # would just echo the title above it - "FROM SWARMSTRIKE ACCORD"
            # under a card titled "Swarmstrike Accord" (reported live). That is
            # 191 of the 266 derived rows; the other 75 name a genuinely
            # different parent ("Dart" from "Hive Assault") where the chip is
            # the ONLY thing tying the derived row back to the skill that
            # inflicts it. Compared case-folded, and against the stripped title
            # actually rendered, so an archived "(Item proc)" suffix cannot
            # make a duplicate look distinct.
            if pname.upper() != title.upper():
                hd.addWidget(_chip(f"FROM {pname.upper()}", theme.ACCENT),
                             0, QtCore.Qt.AlignTop)
        lay.addLayout(hd)

        # the real stat line (cooldown · range from the sheet)
        meta = skdata.skill_meta(sid)
        if meta:
            parts = []
            if "cooldown" in meta:
                parts.append(f"cooldown {meta['cooldown']:g}s")
            if "range" in meta:
                parts.append(f"range {meta['range']:g}m")
            if parts:
                ml = QtWidgets.QLabel(" · ".join(parts))
                ml.setObjectName("Mono")
                ml.setStyleSheet(
                    f'color:{theme.DIM};font-family:"{theme.MONO_FONT}", "Consolas", monospace;'
                    "font-size:11px;background:transparent;")
                lay.addWidget(ml)

        # the resolved description
        desc = skdata.skill_description(sid)
        if desc:
            de = QtWidgets.QLabel(desc)
            de.setObjectName("Mono")
            de.setStyleSheet(
                f'color:{theme.MUTED};font-family:"{theme.MONO_FONT}", "Consolas", monospace;'
                "font-size:12px;background:transparent;")
            de.setWordWrap(True)
            de.setTextFormat(QtCore.Qt.PlainText)
            lay.addWidget(de)

        # per-rank upgrade lines — real sheet data
        ranks = skdata.skill_rank_descriptions(sid)
        if ranks:
            lay.addSpacing(2)
            lay.addWidget(C.SectionHeader("Upgrades"))
            for i, line in enumerate(ranks, 1):
                row = QtWidgets.QHBoxLayout()
                row.setSpacing(8)
                badge = QtWidgets.QLabel(f"RANK {i}")
                badge.setStyleSheet(
                    f"color:{col};background:{theme.with_alpha(col, 16)};"
                    f"border:1px solid {theme.with_alpha(col, 70)};"
                    "border-radius:4px;padding:1px 5px;font-weight:700;"
                    "font-size:10px;letter-spacing:1px;")
                badge.setFixedWidth(56)
                row.addWidget(badge, 0, QtCore.Qt.AlignTop)
                tx = QtWidgets.QLabel(line)
                tx.setObjectName("Mono")
                tx.setStyleSheet(
                    f'color:{theme.MUTED};font-family:"{theme.MONO_FONT}", "Consolas", monospace;'
                    "font-size:12px;background:transparent;")
                tx.setWordWrap(True)
                tx.setTextFormat(QtCore.Qt.PlainText)
                row.addWidget(tx, 1)
                lay.addLayout(row)

        # the weapon(s) this skill comes from
        #
        # A DERIVED row (`..._Status` - a bleed, or the bonus damage a buff
        # inflicts) is in no weapon's skill list: the weapon grants the PARENT
        # that inflicts it. Querying the id alone therefore matched nothing and
        # the card silently dropped its whole "From Weapon" section - which is
        # exactly where "which weapon is this from?" gets answered (reported on
        # Swarmstrike Accord, Wingsabers' `DS_Bladeleaf_Skill2_Status`). Fall
        # back to the parent, the same walk `item_proc_source` makes.
        weapons = idata.weapons_for_skill(sid)
        if not weapons and parent and parent != sid:
            weapons = idata.weapons_for_skill(parent)
        if weapons:
            lay.addSpacing(2)
            lay.addWidget(C.SectionHeader("From Weapon"))
            for w in weapons:
                wrow = QtWidgets.QHBoxLayout()
                wrow.setSpacing(6)
                wl = QtWidgets.QLabel(
                    f"{_WEAPON_FAMILY_GLYPHS.get(w['type'], '')} {w['name']}")
                wl.setStyleSheet(
                    f"color:{theme.GOLD};font-weight:700;font-size:12px;"
                    "background:transparent;")
                wl.setWordWrap(True)
                wl.setTextFormat(QtCore.Qt.PlainText)
                wrow.addWidget(wl, 1)
                lay.addLayout(wrow)

        # footer: the skill's id (search matches it too — a useful handle)
        idl = QtWidgets.QLabel(sid)
        idl.setObjectName("Mono")
        idl.setStyleSheet(
            f'color:{theme.DIM};font-family:"{theme.MONO_FONT}", "Consolas", monospace;'
            "font-size:10px;background:transparent;")
        lay.addWidget(idl, 0, QtCore.Qt.AlignRight)

    def mousePressEvent(self, ev):
        if ev.button() == QtCore.Qt.LeftButton:
            self.close()
            ev.accept()
        else:
            super().mousePressEvent(ev)

    def hideEvent(self, ev):
        parent = self.parent()
        if hasattr(parent, "_on_skill_popup_closed") and self.skill_id:
            parent._on_skill_popup_closed(self.skill_id)
        super().hideEvent(ev)
