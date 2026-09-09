"""Party page, the paper-doll grid: the slot groups, their cards and tiles,
and the fills that put a live piece (or the pet) into them.

Split out of `party_profile.py` — which keeps the detail column (Total Stats,
the selected piece, the Buffs block) — so neither file reabsorbs the other
past the 840-line budget. `PartyProfileMixin` inherits this mixin and
re-exports `_SLOT_GROUPS`, so the page's composition and every call site
(including the tests that read the group table) are unchanged.

Every piece slot the item sheet lists belongs to exactly one group here, so
"this category is empty" and "this category is not drawn" can never be
confused; `test_every_equip_slot_is_in_one_group` pins that. The group
TABLE lives here; which groups go in the WORN column and which ride the fold
row is the page's layout decision, so `_WORN_GROUPS`/`_FOLD_GROUPS`/
`_STANDALONE_GROUPS` stayed in `party_profile.py` with the build.
"""
from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from .. import theme
from ...data import names as gnames
from ...data.items import catalog as gcatalog
from ...data.items import stats as gstats
from .party_roster import _ElideLabel

# The paper-doll TILE: 72x94 with a 44px sprite (2026-09-26, up from 64x72 /
# 38px). The tiles are the page's picture — the biggest thing in the middle
# column — and at the old size the column was mostly empty space beside them,
# so the space went into the tiles instead of into more page width. Bigger
# than 68 (the first attempt was 78) is what put a horizontal scrollbar on a
# default-sized window: every card, and the page's minimum width with it,
# grows with the tile.
#
# The height is 94 because that is the sum of what is INSIDE the tile: the
# 13px slot name and the 11px "no stats" mark under the 44px sprite. The name
# was removed once (2026-09-26) as clutter — "Consumable 1..4" down four
# tiles — which left the icon label stretched to 66x88 around the sprite and
# the doll a field of bare icons; it was restored the same day, and trimming
# the tile to 60 to suit that bare icon was tried and reverted. The height is
# not padding.
_TILE_W, _TILE_H = 72, 94
# The piece sprite inside one paper-doll tile (the pet tile uses the same
# number).
_TILE_ICON_PX = 44

# The groups whose pieces carry NO stats BY NATURE, so the statless treatment
# says nothing there and is not drawn (2026-09-26).
#
# Checked against the real sheet rather than assumed: every item in these three
# groups yields zero stat rows (Mortar/Pickaxe/Sickle, HealthPotion/Whetstone/
# ElixirOfAbundance/TPDeathStone, Glider/Mount), while every item in Weapons,
# Armor and Jewelry yields one to six. So a "no stats" mark on a consumable was
# telling you something you could read off the group caption — under the tiles
# of the one group you open most, repeated four times.
#
# A statless piece in a STAT-bearing group is still marked and dimmed: there it
# is genuinely surprising (a ring that grants nothing looks exactly like one
# that grants a stat), which is the whole reason the treatment exists.
_STATELESS_GROUPS = ("Tools", "Consumables", "Extras")

# The equipment panel: category -> the game slots it holds. The indices are the
# sheet's own slot order (`itemType`'s `Slot_*` rows, see data/items/labels);
# the ORDER inside a group is the display order — Weapons reads Weapon 1,
# Offhand, Weapon 2, and Neck/Trinket trade places (necklace reads with the
# jewelry, trinket with the armor).
#
# Every PIECE slot the sheet lists belongs to exactly one group — nothing is
# skipped because it looks uninteresting, so "there is nothing in this
# category" and "this category is not drawn" can never be confused again.
#
# Two deliberate gaps, both because they are not pieces:
#   * the pet is NOT a slot at all — the id lives on `st.Loadout.pet`, so its
#     tile is built by the read and led into Extras;
#   * `Slot_Bag1-6` (22-27) are drawn NOWHERE: they hold containers, so a tile
#     could only name the container (`Bag_Z2`) and never its contents, which are
#     per-character and readable only for your own character (`is_bag_slot`).
# `test_every_equip_slot_is_in_one_group` pins that gap explicitly, so adding a
# container slot to the sheet fails there instead of going unnoticed on the page.
_SLOT_GROUPS: tuple[tuple[str, tuple[int, ...]], ...] = (
    ("Weapons", (0, 2, 1)),                        # Weapon 1, Offhand, Weapon 2
    ("Armor", (3, 13, 5, 6, 7, 8, 9, 10, 11)),     # Head, Trinket, Shoulders .. Feet
    ("Jewelry", (12, 4, 14)),                      # Ring 1, Neck, Ring 2
    ("Tools", (15, 16, 17)),                       # Job Tool, Pickaxe, Sickle
    ("Consumables", (18, 19, 20, 21)),
    ("Extras", (28, 29)),                      # Glider, Mount (+ the pet)
)

# Tiles per row inside one category card. ONE number for every card (2026-09-26):
# Consumables used to be the odd one out at 4, and its 4-across card was the
# single widest thing in the middle column — wider than the fold strip and than
# any 3-across card — so it, not the doll, set the page's minimum width and put
# a horizontal scrollbar on a default-sized window. At 3 everywhere the four
# consumables stack into a second row and the doll loses ~90px of width.
_GROUP_TILE_COLS: dict[str, int] = {
    "Weapons": 3,
    "Armor": 3,
    "Jewelry": 3,
    "Tools": 3,
    "Consumables": 3,
    "Extras": 3,
}
_DEFAULT_TILE_COLS = 3

# The emoji a tile shows until (and unless) a real sprite can be drawn for its
# item (2026-09-26). Two jobs, and both were originally one dead branch:
#
#   * a NO-SPRITE fallback. The old `if pm.isNull()` test could never fire —
#     `item_tile` never returns a null pixmap, it draws the tinted placeholder
#     box at its last step — so the hollow square ("▣") was not a fallback
#     failing, it WAS the fallback. A box on a filled tile reads as a broken
#     image; a slot's own glyph says what KIND of thing the slot holds.
#   * the tile's RESTING state. Every tile draws its emoji from the moment it
#     is built, so the doll is a tidy grid of slots waiting for the gear walk
#     rather than 20 tiles of nothing. Before this, the empty marker was a
#     bare "·" and a loading page looked broken. (This reverses the
#     "empty reads as empty, draw no glyph" rule from earlier the same day: a
#     dimmed glyph per slot is a better "nothing here yet" than a dot, and it
#     is the same glyph the slot will keep if the item never gets art.)
#
# The glyphs are the mockup's own (ai/workspace/opencode/mockups/
# player_page_mockups.html, the B paper-doll grid), with the slot each one
# actually belongs to:
#
#     🗡️ Weapon 1  ⚔️ Weapon 2  🛡️ Offhand  🧢 Head  👕 Shoulders
#     🧥 Chest  🧣 Back  🤲🏼 Hands  ➰ Waist  👖 Legs  👣 Feet
#     📿 Neck  💍 Ring  💎 Trinket  🔨 Tool  🧪 Consumable  🍖 Food
#     🐾 Glider/Mount
#
# Back is 🧣 because Unicode has no cape: 🦸 is the only caped figure in the
# set and it is a whole person where every other glyph here is one garment.
# Chest keeps 🧥 and Back keeps the shawl rather than trading them, so a coat
# never means two different slots.
#
# Weapon 1 and Weapon 2 are deliberately different glyphs (crossed swords and
# a single blade). They sit side by side in the same row, and one dagger twice
# reads as a duplicated tile rather than as two slots. Neither glyph claims a
# weapon TYPE, which is the thing to avoid here: either slot can hold anything,
# so an axe or a staff would be asserting something neither slot knows.
#
# The slot numbers are the game's own array indices (`equip_slot_label`): 0 and
# 1 are Weapon 1 and Weapon 2, and 2 is the OFFHAND. Index -> slot name is
# `_EQUIP_SLOTS` in data/items/labels, which is the only authority for it.
#
# This table was written once against the mockup and drifted off it: gloves
# landed on Chest, jeans on Hands and Feet, and all three jewelry slots shared
# one gem. Nothing on the page could catch that, because the glyph is only
# drawn when the atlas has no sprite for the item, so a wrong one just sits
# there being quietly wrong. `test_slot_emoji_matches_slot_names` builds its
# expectation from the slot NAME rather than from a second hand-copied index,
# so a reordered or added slot fails there instead.
#
# Neck / Trinket / Ring are three different glyphs on purpose (prayer beads, a
# cut gem, a mounted stone): as one gem they read as the same row three times,
# and only two of the three ship item art.
#
# A real sprite always wins over the glyph — the sprite is what tells two rings
# apart. Only when the item has no drawable sprite does the glyph stay.
_SLOT_EMOJI: dict[int, str] = {
    0: "\U0001F5E1", 1: "\u2694\ufe0f", 2: "\U0001F6E1",   # w1 / w2 / offhand
    3: "\U0001F9E2", 5: "\U0001F455", 6: "\U0001F9E5",     # head / shoulders / chest
    7: "\U0001F9E3", 8: "\U0001F932\U0001F3FC", 9: "\U000027B0",  # back / hands / waist
    10: "\U0001F456", 11: "\U0001F463",                    # legs / feet
    4: "\U0001F4FF", 12: "\U0001F48D", 13: "\U0001F48E",   # neck / ring / trinket
    14: "\U0001F48D",                                      # ring 2
    15: "\U0001F528", 16: "\U0001F528", 17: "\U0001F528",  # job tool / tools
    18: "\U0001F9EA", 19: "\U0001F9EA", 20: "\U0001F9EA",  # potions
    21: "\U0001F356",                                      # food
    28: "\U0001F43E", 29: "\U0001F43E",                    # glider / mount
}
_SLOT_EMOJI_DEFAULT = "▪"

# The ink an EMPTY slot is drawn in (2026-09-26). A slot with nothing in it is
# dim — glyph, label and tile alike — and a slot with a real piece in it is at
# full strength, so the whole doll reads at a glance: bright means equipped.
#
# It is applied ONLY once a gear walk has reported the slot empty. A tile that
# has not been asked about yet keeps the normal slot ink, because "unknown" and
# "empty" are different facts and only one of them has been read: the page
# takes 1-2s to answer, and a doll drawn dim for that whole time says "this
# hero has no gear".
#
# This replaces a two-handed-weapon rule (2026-09-26), which was the wrong
# mechanism twice over. The sheet carries no reliable "this takes both hands"
# flag — most weapon rows have none at all — so the rule had to guess from the
# type name, and a guess on the party page dimmed an off-hand for a hero
# holding daggers. More to the point, "no real item in this slot" is the fact
# actually worth showing; whether the game would have allowed something else
# there is a rule the sheet does not tell us, and a doll that guesses reads as
# a bug the moment the guess is wrong.
#
# A solid colour, not an alpha and not a QGraphicsOpacityEffect: an effect on
# a label that draws TEXT is not reliably honoured (the glyph came out at full
# strength with the effect set), and a dim grey that is simply darker cannot
# fail to apply.
_SLOT_EMPTY_INK = "#3b444b"

class _BadgePlacer(QtCore.QObject):
    """Pins a count badge to its icon's bottom-right corner (2026-09-26).

    A child QLabel does not get a resize event when its PARENT resizes, and the
    icon is re-laid-out every time the tile row reflows — so without this the
    badge drifts off the corner the first time the window width changes.
    """
    def __init__(self, badge: QtWidgets.QWidget) -> None:
        super().__init__(badge)
        self._badge = badge

    def eventFilter(self, obj, ev) -> bool:             # noqa: N802 (Qt)
        if ev.type() in (QtCore.QEvent.Type.Resize, QtCore.QEvent.Type.Show):
            self.place(obj)
        return False

    def place(self, icon: QtWidgets.QWidget) -> None:
        """Re-fit and re-seat the badge, ON the sprite.

        Anchored to the ICON LABEL's corner at first (2026-09-26), which put
        it beside and below the item rather than on it: the label is stretched
        by the tile's layout (66x56 measured) while the sprite is a centred
        44x44 pixmap inside it, so the label's bottom-right corner is ~10px
        right of and ~5px below the sprite's. A child is not clipped by its
        parent, so the badge simply drew outside the picture and read as a
        caption under the tile. It is anchored to the PIXMAP's rect now, so
        the number sits in the sprite's own corner whatever the label is
        sized to.

        Called after a text change too, not only on a resize: a wider number
        ("9" -> "123") resizes the badge itself, and nothing about the ICON
        changed, so the filter would never fire and the digits would be drawn
        inside the old two-digit box.
        """
        self._badge.adjustSize()
        box = QtCore.QRect(0, 0, icon.width(), icon.height())
        pm = icon.pixmap()
        if pm is not None and not pm.isNull():
            box = QtCore.QRect((icon.width() - pm.width()) // 2,
                               (icon.height() - pm.height()) // 2,
                               pm.width(), pm.height())
        inset = 2
        self._badge.move(
            max(0, box.right() - self._badge.width() - inset + 1),
            max(0, box.bottom() - self._badge.height() - inset + 1))


class PartyGridMixin:
    """The paper-doll GRID: one card per slot group, one tile per slot, and
    the fills that put the live walk (or the pet read) into them."""

    def _party_group_card(self, title: str, slots: tuple[int, ...], *,
                          lead: QtWidgets.QWidget | None = None,
                          inline_head: bool = True,
                          flat: bool = False) -> QtWidgets.QFrame:
        """One category card: its caption, then that category's tiles.

        `lead` is a pre-built tile shown first in the group (the pet).
        `inline_head=False` (the fold groups) builds the card WITHOUT its
        caption — the caption lives in the fold row beside the others, and
        the card is pure tiles shown/hidden by that row. The per-card slot
        count ("3 slot(s)", "pet + 2 slots") is gone (2026-09-25): the tiles
        are the count, and nobody read the number.

        `flat=True` drops the card's OWN frame and padding (2026-09-26): a
        fold group's body lives INSIDE the fold card, and a bordered,
        padded Card inside a bordered, padded Card read as a box inside a box
        and made the whole block read taller than it was. Flat, the fold card
        is the only frame, and the tiles sit straight in it.
        """
        card = QtWidgets.QFrame()
        # tests match cards by title; carried cards hold no caption widget
        # (the caption lives in the fold row), so the name rides on the card
        card._party_title = title
        # the group a tile belongs to, so the fill knows whether "no stats" is
        # news here (see _STATELESS_GROUPS)
        if flat:
            # not objectName "Card", so the app stylesheet's Card rule does
            # not paint a second border underneath the fold card's
            card.setObjectName("FoldBody")
        else:
            card.setObjectName("Card")
            card.setStyleSheet(
                f"QFrame#Card{{background:{theme.PANEL};border:1px solid "
                f"{theme.BORDER};border-radius:6px;}}")
        col = QtWidgets.QVBoxLayout(card)
        # The same padding as the page's other blocks (8/6) — a group card with
        # 6/5 put its caption 1px above the Total Stats / Buffs / Players
        # captions in the row above it, which read as a misalignment
        # (2026-09-26). The tiles' growth is still paid for: they are 68 wide,
        # not the 78 an earlier pass wanted. A flat body has none of its own:
        # the fold card around it is what pads.
        if flat:
            col.setContentsMargins(0, 0, 0, 0)
        else:
            col.setContentsMargins(8, 6, 8, 6)
        col.setSpacing(4)

        if inline_head:
            cap = QtWidgets.QLabel(title)
            cap.setStyleSheet(
                f"font-size: 16px; font-weight: 600; color: {theme.MUTED};")
            col.addWidget(cap)

        grid = QtWidgets.QGridLayout()
        grid.setSpacing(4)
        cols = _GROUP_TILE_COLS.get(title, _DEFAULT_TILE_COLS)
        tiles: list[QtWidgets.QWidget] = [self._party_slot_card(s)
                                         for s in slots]
        for t in tiles:
            # the group a tile belongs to, so the fill knows whether "no stats"
            # is news here (see _STATELESS_GROUPS)
            t._party_group = title                     # type: ignore[attr-defined]
        if lead is not None:
            tiles.insert(0, lead)
        for i, tile in enumerate(tiles):
            grid.addWidget(tile, i // cols, i % cols)
        # The slack column: with every tile column at stretch 0 Qt would split
        # the card's extra width BETWEEN the tiles, so a wide window would push
        # them apart instead of leaving the space to the right (measured: a
        # full 6-wide row spread 414px of tiles across a 916px card). One empty
        # stretching column parks that slack outside the tiles.
        grid.setColumnStretch(cols, 1)
        body = QtWidgets.QWidget()          # the grid host (the foldable part
        body.setLayout(grid)                # of a carried group's card)
        col.addWidget(body)
        col.addStretch(1)
        # tests (and the fold row) reach the foldable body directly
        card._party_body = body
        return card

    # ------------------------------------------------------------ grid cards
    def _party_slot_card(self, slot: int) -> QtWidgets.QFrame:
        """One empty-by-default box of the paper-doll grid; filled by
        _party_profile_fill from the fresh gear walk."""
        from ...data.items import labels as glabels
        card = QtWidgets.QFrame()
        card.setFixedSize(_TILE_W, _TILE_H)
        card.setObjectName("Card")
        # a normal tile, not the dim one — see the icon above: only a walk that
        # has actually reported the slot empty earns the dim frame
        card.setStyleSheet(
            f"QFrame#Card{{background:{theme.PANEL};border:1px solid "
            f"{theme.BORDER};border-radius:6px;}}")
        lay = QtWidgets.QVBoxLayout(card)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.setSpacing(1)
        # The tile's resting state is its slot's glyph, NOT a dot (2026-09-26):
        # the whole grid is built before the first gear walk answers, and a
        # column of bare tiles read as a page that had failed rather than one
        # that had not loaded yet.
        #
        # At full strength, deliberately (2026-09-26): a tile that has not been
        # asked about yet is UNKNOWN, and an unknown slot drawn in the
        # "nothing in here" ink claims something nobody has read. The page
        # takes 1-2s to answer, and for that whole time a doll drawn dim says
        # "this hero has no gear" — the same fabrication as the demo data, in
        # the other direction. So the dim ink waits for the walk that justifies
        # it: built tiles are neutral, a walk that reports the slot EMPTY dims
        # it, a walk with a piece in it fills it bright.
        icon = QtWidgets.QLabel(_SLOT_EMOJI.get(slot, _SLOT_EMOJI_DEFAULT))
        icon.setAlignment(QtCore.Qt.AlignCenter)
        icon.setStyleSheet(f"color:{theme.DIM};font-size:26px;")
        lay.addWidget(icon, 1)
        # The tile names its OWN slot ("Consumable 3", not "Consumable"):
        # four of them sit side by side and the first word alone would make
        # them indistinguishable.
        #
        # Removed 2026-09-26 as clutter, then restored the same day: with the
        # name gone the icon label stretched to 66x88 around a 44x44 sprite and
        # the doll was a field of bare icons with nothing to say which slot a
        # sprite belonged to. The count badge answers "how many", not "which
        # one", so both are needed.
        name = QtWidgets.QLabel(glabels.equip_slot_label(slot) or f"S{slot}")
        name.setAlignment(QtCore.Qt.AlignCenter)
        name.setStyleSheet(f"color:{theme.DIM};font-size:13px;")
        name.setWordWrap(True)
        lay.addWidget(name)
        # The "no computed stats" marker (2026-09-26): a filled tile whose
        # piece resolves no stat rows at all looks identical to a good one on
        # the doll — same sprite, same rarity border, and the piece panel then
        # says "No computed stats for this piece". So a statless piece is
        # dimmed (darker cell, grey border, faded sprite) and marked here.
        # Hidden, not absent, until a fill proves it applies.
        mark = QtWidgets.QLabel("no stats")
        mark.setAlignment(QtCore.Qt.AlignCenter)
        mark.setStyleSheet(f"color:{theme.DIM};font-size:11px;")
        mark.hide()
        lay.addWidget(mark)
        # The stack count, as a badge on the sprite's corner (2026-09-26): a
        # consumable slot holds N of the thing, and the tile could not say so —
        # four identical-looking potion sprites, none of which told you whether
        # it was the last one. `hero_gear` already returns the amount per row
        # (core.inspect._record_count, guarded by the item's own stackSize), so
        # this is a rendering of a live read, not a new one. Hidden until a
        # count above 1 arrives: a single item needs no badge, and the reader
        # returns exactly 1 for every non-stackable piece.
        badge = QtWidgets.QLabel(icon)
        badge.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignBottom)
        badge.setStyleSheet(
            f"color:{theme.TEXT};background:rgba(0,0,0,170);"
            f"font-size:12px;font-weight:800;padding:0px 3px;"
            f"border-radius:3px;")
        badge.hide()
        placer = _BadgePlacer(badge)
        icon.installEventFilter(placer)
        card._party_slot_icon = icon        # type: ignore[attr-defined]
        card._party_slot_kind = ""          # type: ignore[attr-defined]
        card._party_slot_mark = mark        # type: ignore[attr-defined]
        card._party_slot_name = name        # type: ignore[attr-defined]
        card._party_slot_badge = badge      # type: ignore[attr-defined]
        # False until a gear walk has answered for this slot. It is what lets
        # the fill tell "never read" from "read, and it is empty" — the first
        # is drawn neutral, the second dim (2026-09-26).
        card._party_slot_read = False       # type: ignore[attr-defined]
        # the filter has no owner but its badge, and an unparented QObject is
        # collected before the next paint — so the card holds it too
        card._party_badge_placer = placer    # type: ignore[attr-defined]
        self._party_grid_cards[slot] = card
        return card

    def _party_slot_fill(self, slot: int, row: dict) -> None:
        from ...data import icons as gicons

        card = self._party_grid_cards.get(slot)
        if card is None or not self._widget_alive(card):
            return
        kind = row.get("kind") or ""
        icon: QtWidgets.QLabel = card._party_slot_icon  # type: ignore[attr-defined]
        # The stack count is refreshed BEFORE the "same item" early return
        # below, because the amount changes while the item does not: drinking a
        # potion takes the slot from 20 to 19 with the same kind in it. That is
        # the exact case the badge exists for, and behind the early return it
        # froze on the first number it ever saw (2026-09-26).
        self._party_slot_badge_set(card, row.get("count"))
        if not kind or kind == card._party_slot_kind:   # type: ignore[attr-defined]
            return
        # `item_label`, not `item_name`: a piece the sheets do not carry would
        # otherwise render as its raw id, since `item_name` answers with the id
        # unchanged and the humanize this line used to fall back to therefore
        # never ran (2026-10-03 audit).
        nm = gnames.item_label(kind)
        rarity = row.get("rarity") or "Common"
        # A container slot has no tile at all (see _SLOT_GROUPS): a row that
        # names one is ignored by the `card is None` return above.
        col = theme.RARITY.get(rarity, theme.BORDER)
        # sheet-side stat math, the SAME call the piece panel and Total Stats
        # make — a piece that yields no stat rows is greyed out and marked
        # instead of pretending to be a normal drop
        item = (gcatalog._data().get("items", {}) or {}).get(kind) or {}
        gs = gstats.gear_stats(item, row.get("level") or None,
                               rarity or None)
        # A piece with no stats is dimmed and marked — UNLESS it sits in a group
        # where nothing has stats (see _STATELESS_GROUPS), where the treatment
        # is just noise repeated down the column.
        no_stats = not (gs and gs.get("stats"))
        statless = no_stats and getattr(
            card, "_party_group", "") not in _STATELESS_GROUPS
        if statless:
            col = theme.DIM
        card.setStyleSheet(
            f"QFrame#Card{{background:"
            f"{theme.PANEL_LOW if statless else theme.PANEL};border:1px solid "
            f"{col};border-radius:6px;}}")
        # The sprite, or the slot's emoji when there is no sprite to draw.
        # `item_tile` never returns a null pixmap — it draws the tinted
        # placeholder box instead — so the old `if pm.isNull()` test here was
        # dead code and the box was what actually reached the tile. Asking
        # `item_sprite_resolvable` is what makes the fallback live: it walks the
        # same three lookups `item_tile` does and says whether a real sprite is
        # coming (2026-09-26).
        pm = None
        if gicons.item_sprite_resolvable(kind, nm):
            try:
                pm = gicons.item_tile(kind, nm, _TILE_ICON_PX,
                                      theme.DIM if statless else col,
                                      strong=True)
            except Exception:
                pm = None
        drew_emoji = pm is None or pm.isNull()
        if drew_emoji:
            icon.setPixmap(QtGui.QPixmap())
            icon.setText(_SLOT_EMOJI.get(slot, _SLOT_EMOJI_DEFAULT))
        else:
            icon.setPixmap(pm)
        icon.setStyleSheet("")
        # The badge is anchored to the sprite, so it can only be seated once
        # the sprite is in the label (2026-09-26). The count was already set
        # above — before the "same item" early return, so a stack that ticked
        # down still updates — but the placement there had no pixmap to measure
        # and left the badge against the bare label.
        card._party_badge_placer.place(icon)           # type: ignore[attr-defined]
        # fade the sprite on a statless piece; setGraphicsEffect(None) clears
        # it again when a stat-bearing piece takes the slot. The effect is
        # PARENTED to the label — an unparented one has no Python owner left
        # and is collected before the next paint.
        if statless:
            fade = QtWidgets.QGraphicsOpacityEffect(icon)
            fade.setOpacity(0.45)
            icon.setGraphicsEffect(fade)
        else:
            icon.setGraphicsEffect(None)
        if drew_emoji:
            # after the clear AND after the fade block: the glyph is TEXT, so
            # at the default font size it is a 12px smudge in a 44px box, and
            # it needs the tile's own size to read at all
            icon.setStyleSheet(
                f"color:{theme.DIM if statless else col};font-size:26px;")
        # a filled slot's LABEL comes back up to full strength too (2026-09-26):
        # the dim ink is the empty slot's, and a slot with a real piece in it
        # is not an empty slot
        card._party_slot_name.setStyleSheet(                    # noqa: SLF001
            f"color:{theme.DIM if statless else theme.MUTED};"
            f"font-size:13px;")
        mark: QtWidgets.QLabel = card._party_slot_mark  # type: ignore[attr-defined]
        mark.setVisible(bool(statless))
        card._party_slot_kind = kind             # type: ignore[attr-defined]
        # No tooltip anywhere on this page (2026-09-26) — the name, the rarity
        # and the upgrade are all readable elsewhere: the sprite, the piece
        # panel under the doll (its caption, its meta line and its stat rows),
        # and the "no stats" mark right here on the tile.

        def _pick(_e=None, k=kind):
            self._party_piece_kind = k
            self._fill_party_piece()
        card.mousePressEvent = _pick             # type: ignore[method-assign]

    @staticmethod
    def _party_slot_badge_set(card: QtWidgets.QFrame, n) -> None:
        """Show `n` on the tile's corner badge, or hide it (2026-09-26).

        Only a real stack gets a badge: `count` is None for a non-stackable
        piece and for a slot whose amount the reader could not confirm, and
        exactly 1 for "one of them" — so both read as no badge rather than as a
        confident wrong number. The reader already guards the value against the
        item's own stackSize ceiling (core.inspect._record_count), so a
        nonsense amount never reaches here.
        """
        badge: QtWidgets.QLabel = card._party_slot_badge  # type: ignore[attr-defined]
        if isinstance(n, int) and n > 1:
            if badge.text() != str(n):
                badge.setText(str(n))
                card._party_badge_placer.place(          # type: ignore[attr-defined]
                    card._party_slot_icon)              # type: ignore[attr-defined]
            badge.setVisible(True)
        elif not badge.isHidden() or badge.text():
            badge.setText("")
            badge.hide()

    def _party_slot_reset(self, slot: int) -> None:
        """Put an emptied card back to its DIM slot glyph (2026-09-26).

        The same resting state a freshly built tile has: nothing in the slot
        means the slot recedes — glyph, label and frame all back — so "bright
        means equipped" holds for a slot emptied in game as well as for one
        that was never filled.
        """
        card = self._party_grid_cards.get(slot)
        if card is None or not self._widget_alive(card):
            return
        icon: QtWidgets.QLabel = card._party_slot_icon  # type: ignore[attr-defined]
        name: QtWidgets.QLabel = card._party_slot_name  # type: ignore[attr-defined]
        icon.setPixmap(QtGui.QPixmap())
        icon.setText(_SLOT_EMOJI.get(slot, _SLOT_EMOJI_DEFAULT))
        icon.setStyleSheet(f"color:{_SLOT_EMPTY_INK};font-size:26px;")
        # a slot emptied in game is not a statless piece: clear the fade and
        # the marker with it
        icon.setGraphicsEffect(None)
        card._party_slot_mark.setVisible(False)  # type: ignore[attr-defined]
        card._party_slot_badge.setText("")        # type: ignore[attr-defined]
        card._party_slot_badge.hide()             # type: ignore[attr-defined]
        if not name.text():
            from ...data.items.labels import equip_slot_label
            name.setText(equip_slot_label(slot) or f"S{slot}")
        name.setStyleSheet(f"color:{_SLOT_EMPTY_INK};font-size:13px;")
        card.setStyleSheet(
            f"QFrame#Card{{background:{theme.PANEL_LOW};border:1px solid "
            f"{theme.BORDER};border-radius:6px;}}")
        card._party_slot_kind = ""                          # type: ignore[attr-defined]

    # ------------------------------------------------------------------ fill
    def _party_pet_box_card(self) -> QtWidgets.QFrame:
        """Build the empty Companion box (filled later by _party_pet_fill)."""
        card = QtWidgets.QFrame()
        # the pet tile is the same size as a piece tile, so the Extras card
        # (pet + glider + mount) is one even row
        card.setFixedSize(_TILE_W, _TILE_H)
        card.setObjectName("Card")
        card.setStyleSheet(
            f"QFrame#Card{{background:{theme.PANEL};border:1px solid "
            f"{theme.BORDER};border-radius:6px;}}")
        lay = QtWidgets.QVBoxLayout(card)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.setSpacing(1)
        icon = QtWidgets.QLabel("🐾")
        icon.setAlignment(QtCore.Qt.AlignCenter)
        icon.setStyleSheet(f"color:{theme.DIM};font-size:26px;")
        lay.addWidget(icon, 1)
        name = _ElideLabel("Pet", max_w=_TILE_W - 12)
        name.setAlignment(QtCore.Qt.AlignCenter)
        name.setStyleSheet(f"color:{theme.DIM};font-size:13px;")
        lay.addWidget(name)
        card._party_pet_name = name        # type: ignore[attr-defined]
        card._party_pet_icon = icon        # type: ignore[attr-defined]
        card._party_pet_id = ""            # type: ignore[attr-defined]
        card._party_pet_live = False       # type: ignore[attr-defined]
        self._party_pet_box = card
        return card

    def _party_pet_fill(self, pet_id: str, live) -> None:
        """Fill the Companion box from the pet read (same read that feeds
        the Companion row): creature sprite, GOOD border while its entity is
        live in the scene, name + state in the tooltip. Falsy pet_id blanks
        it back to the placeholder so a re-walk can't leave a stale pet."""
        from ...data import icons as gicons
        from .party_rows import _pet_labels
        card = getattr(self, "_party_pet_box", None)
        if card is None or not self._widget_alive(card):
            return
        icon: QtWidgets.QLabel = card._party_pet_icon  # type: ignore[attr-defined]
        nlabel: _ElideLabel = card._party_pet_name    # type: ignore[attr-defined]
        if not pet_id:
            icon.setPixmap(QtGui.QPixmap())
            icon.setText("🐾")
            icon.setStyleSheet(f"color:{theme.DIM};font-size:26px;")
            nlabel.set_full("Pet")
            card.setStyleSheet(
                f"QFrame#Card{{background:{theme.PANEL};border:1px solid "
                f"{theme.BORDER};border-radius:6px;}}")
            card._party_pet_id = ""        # type: ignore[attr-defined]
            card._party_pet_live = False   # type: ignore[attr-defined]
            return
        is_live = live is not None
        if pet_id == card._party_pet_id and is_live == card._party_pet_live:  # type: ignore[attr-defined]
            return
        name, _sub = _pet_labels(pet_id, live)
        col = theme.GOOD if is_live else theme.DIM
        card.setStyleSheet(
            f"QFrame#Card{{background:{theme.PANEL};border:1px solid "
            f"{col};border-radius:6px;}}")
        pm = None
        try:
            if gicons.has_icon("unit", pet_id):
                pm = gicons.pixmap("unit", pet_id, _TILE_ICON_PX)
        except Exception:
            pm = None
        if pm is not None and not pm.isNull():
            icon.setPixmap(pm)
        else:
            icon.setPixmap(gicons.emoji_pixmap("🐾", _TILE_ICON_PX, theme.MUTED))
        icon.setStyleSheet("")
        card._party_pet_id = pet_id       # type: ignore[attr-defined]
        card._party_pet_live = is_live    # type: ignore[attr-defined]
        # The pet's NAME is on the tile, not in a tooltip (2026-09-26 — the
        # page has no hover popups). The sub-line's job — is the creature
        # actually in this scene — is already carried by the tile's border,
        # which goes GOOD when it is, and the id is the tile's own business.
        nlabel.set_full(name)
