"""Party page, the detail column: Total Stats, the selected piece, the Buffs
block, and the panel layout that arranges them beside the paper doll.

Split out of `party_page.py` (the 840-line budget) the same way the settings
and craft pages split; the paper-doll grid and the roster column then split
out of THIS file for the same reason (`party_grid.py`, `party_roster.py`,
both inherited by `PartyProfileMixin`, so no call site moved). Everything
here reads what `_refresh_party_detail` already read — the gear walk — so no
extra live reads.

- The equipment panel's LAYOUT lives here (which groups go in the worn column,
  the carried fold row, the detail column's fixed widths); the grid's cards,
  tiles and fills live in `party_grid.py` (`_SLOT_GROUPS` is re-exported
  here). Worn categories (Weapons/Armor/Jewelry) stack in the LEFT column;
  the carried categories are an ACCORDION under the Jewelry card (2026-09-25):
  fold captions side by side (Extras, Tools), and the clicked one expands
  its grid beneath the row, so expansion grows DOWNWARD and the columns'
  widths never move. Consumables LEFT the row for the right column
  (2026-09-26, `_STANDALONE_GROUPS`): it is the one people open on another
  player, the right column had the slack, and it now sits under the Buffs
  block with its own caption, still shipping open. Every *piece* slot appears
  exactly once; `test_every_equip_slot_is_in_one_group` fails if one is ever
  dropped.
- Detail column: STAT TOTALS on top (every equipped piece's stats summed),
  then the selected piece (click a slot; sheet-side computed stats), then the
  Buffs block, then any carried group that moved here (Consumables), then the
  gear notes. The blocks are Cards in one style
  (caption + rows) and all FIXED-width — their wrapped text used to re-hint
  the column width on every fill, which is what made the columns wander.
  The two stat blocks are given `_BLOCK_ROWS` rows as a FLOOR — so a hero with
  2 stats and a hero with 12 leave the page below them exactly where it was —
  and GROW past it rather than clamp, because a fully equipped hero sums to
  8-10 distinct stats and the old "+N more" ceiling was hiding two or three
  real numbers on every refresh. The Buffs block is pinned to `_BUFF_ROWS`
  and scrolls.

What is deliberately NOT here:

- A talent board. The replicated st.player.HeroSpecialization prop does not
  carry the tree's tier/branch/point picks for other players — those exist
  only as widgets on the local player's own talent screen while it is open —
  so a board could never read anyone but you. It was removed, not replaced.
- The class tag that survives is the hero avatar's class colour, driven by
  `model.hero_class_of` in `party_page._party_sync_chrome`.
- Buffs and the gear notes (NOT READ warnings + the pet row) are RENDERED by
  `party_page.py` (it owns the live reads and the row helpers); this module
  owns the Buffs block's Card, its caption and its pinned, scrolling height.
- The raw status id under every buff ('ItemStatus',
  'Axe_Boomerang_Skill_Passive_Status') is gone (2026-09-26): it is the
  sheet's key, the row above already names the status, and the id is the
  row's tooltip. Removed, not hidden.
- The PLAYERS roster column (cards + the eliding name label) lives in
  `party_roster.py`; `PartyProfileMixin` inherits it so the page's
  composition and every call site are unchanged.
- A DPS/HPS/taken/deaths strip. That was your own session restated inside an
  inspect page; the Combat page owns those numbers. Removed, not hidden.
- The six bag slots (`Slot_Bag1-6`). They hold CONTAINERS, not pieces: a tile
  could only ever say "Bag 3 — Bag_Z2", because a bag's contents are
  per-character and only your own are readable. Drawing them also gave the
  panel a category that existed to say nothing, so it was removed, not hidden
  (see `_SLOT_GROUPS` and `is_bag_slot`).
"""
from __future__ import annotations


from PySide6 import QtCore, QtGui, QtWidgets

from .. import theme
from ...data import names as gnames
from ...data.items import stats as gstats
from ...data.items import catalog as gcatalog
from .party_grid import PartyGridMixin, _SLOT_GROUPS
from .party_roster import PartyRosterMixin, _Elide2Label, _ElideLabel  # noqa: F401  (_ElideLabel re-exported: party_page imports it through here)
from .party_stats import PartyStatsMixin, _BLOCK_ROWS  # noqa: F401  (re-export)




# The panel is two columns: what you WEAR on the left, what you CARRY on the
# right (arrangement 2 of the gear mockup, chosen over one long stacked column).
# The panel is two columns: what you WEAR on the left, what you CARRY on the
# right (arrangement 2 of the gear mockup, chosen over one long stacked column).
# Extras leads the carried groups above Tools, and the selected piece sits
# under the CARRIED column: it is what you are inspecting, not something you
# wear.
#
# "Extras" is a DISPLAY name only (2026-09-26, renamed from the sheet's
# "Companions"): the slots behind it are unchanged (Glider 28, Mount 29, plus
# the pet tile the read builds), and the sheet still calls the group Companions
# everywhere else. "Extras" says "carried, but not worn gear" and so covers
# the glider, the mount and the pet equally — no single word names all three.
# It is also short enough to stop the fold row setting the block's width, which
# is the other half of the reason (see _FOLD_GROUPS).
#
# `_CARRIED_GROUPS` used to sit here too, as the full carried list. It died
# with the 2026-09-26 rework that put Jewelry on the fold row and pulled
# Consumables out into _STANDALONE_GROUPS, which between them cover exactly
# what it used to: the row below is the whole truth now.
_WORN_GROUPS = ("Weapons", "Armor", "Jewelry")

# The carried groups that do NOT ride the fold row (2026-09-26): Consumables is
# the one people open on another player, and the right column had room to
# spare, so it moved under the Buffs block as its own captioned card.
_STANDALONE_GROUPS = ("Consumables",)

# The fold row's tabs, in order, and the one that ships EXPANDED (2026-09-26).
# Jewelry joined the row: it was the card the row used to live INSIDE, which
# left it the only group on the page that could not be folded away, and its
# three tiles are the same size as the carried groups' — so as a tab it reads
# as the row's own first entry rather than as a separate block bolted above it.
# It ships open because rings and neck are what you open this page for, and an
# accordion that starts shut hides its own most-wanted tab.
#
# The row's width is set by its LONGEST caption, not by the tiles (measured
# 2026-09-26: three equal cells at the widest word = 260px against 242px of
# tile, so the row was 18px wider than the cards it sits over). "Extras" is
# 6 characters, under "Jewelry"'s 7, so Jewelry is now the longest cell and the
# row drops to 185px — under the tiles' own 242px. The tiles set the width and
# the row fits inside them. "Companions" (the sheet's own name) could not do
# that at 10 characters. 242px is a floor: no caption goes below it without
# shrinking the tiles.
_FOLD_GROUPS = ("Jewelry", "Extras", "Tools")
_FOLD_DEFAULT = "Jewelry"


class _MetaLabel(QtWidgets.QLabel):
    """The selected piece's `Rarity  +N  ·  Lv N` line, rarity IN COLOUR.

    Two things this has to be that a plain QLabel is not (2026-09-26):

    1. The rarity word is wrapped in a rich-text span in the sheet's own
       rarity colour, so Rare/Epic/Legendary stop being the same grey word
       and become the one fact on the line you can pick out. Qt needs
       `RichText` for the span to render at all.

    2. It ELIDES instead of clipping. A plain QLabel given text wider than
       its box silently cuts the overflow off the right edge — measured, the
       widest real line ("Legendary  +12  ·  Lv 120") wants 308px in a 242px
       card, so the level was being cut off with nothing on the page to say
       so. Eliding keeps the rarity word (the part that is coloured and the
       part that matters) and gives up the tail, which is the part that can
       be lost least: an upgrade and a level are also visible on the tile.

    The elide is computed on the PLAIN text and the span is re-applied around
    the result, because `QFontMetrics` cannot measure rich text — asking it
    for the width of the markup measures the tags.
    """

    def __init__(self) -> None:
        super().__init__("")
        self._rarity = ""
        self._rest = ""
        self.setTextFormat(QtCore.Qt.RichText)
        # Ignored width would hand this label a 0px box (see _ElideLabel's
        # note on the roster names); Preferred lets it ask for what it needs
        # and elide down from there.
        self.setSizePolicy(QtWidgets.QSizePolicy.Preferred,
                           QtWidgets.QSizePolicy.Fixed)

    def set_meta(self, rarity: str, rest: str) -> None:
        """`rarity` is the coloured word; `rest` is the elidable tail."""
        self._rarity = rarity or ""
        self._rest = rest or ""
        self._paint()

    def _paint(self) -> None:
        rest = self.fontMetrics().elidedText(
            self._rest, QtCore.Qt.ElideRight, max(0, self.width() - 4))
        self.setText(
            f'<span style="color:{theme.rarity_color(self._rarity)};'
            f'font-weight:700">{self._rarity}</span>'
            f'<span style="color:{theme.DIM}">{rest}</span>')

    def resizeEvent(self, ev):                            # noqa: N802 (Qt)
        super().resizeEvent(ev)
        self._paint()


# How many stat rows each readout in the detail column shows now lives in
# `party_stats.py` (2026-09-26) with the rows themselves, and is re-exported
# above so `party_profile._BLOCK_ROWS` — which the tests import — still
# resolves. Two blocks (Total Stats and the selected piece) are pinned to
# exactly that many rows: the stat list is different for every hero and every
# refresh, and an unpinned block grew and shrank, shoving the piece card, the
# buffs and the gear notes up and down the page.

# How much of the detail column's width the Buffs card spends on its own
# padding and border (8 + 8 margins + 1px each side) — a group living INSIDE
# that block has to fit what is left of it.
_BLOCK_PAD = 18

# How many buff CARDS the Buffs block shows before it starts scrolling (the
# same reason as _BLOCK_ROWS: a status list that grows and shrinks with the
# hero moved everything under it). Nothing is hidden by this — the block
# scrolls, it does not truncate.
#
# 5, not 7 (2026-09-26): a buff row is a Card of its own now, so a row is
# ~45px (two lines of text + 12px of padding + a 1px border) where the bare
# line it replaced was ~20. Seven cards would be 175px taller than the block
# they replaced and push the gear notes under the fold; five cards come out
# within a few pixels of the old height and still show the whole common
# case (a hero is usually under five statuses at once).
_BUFF_ROWS = 5


class _FoldCaption(QtWidgets.QLabel):
    """A group card's caption with the collapsible affordance (fold arrow,
    pointing hand, hover brighten); emits `collapsedChanged` so the card can
    hide its grid. The row is an ACCORDION: exactly one group is expanded at
    all times, and clicking the already-open tab does nothing (see
    mousePressEvent) — so the body under the row is never empty.

    12px, not 14px (2026-09-26): the fold row is a fixed floor on the middle
    column's width (three equal cells as wide as the longest caption), and at
    14px that floor — 311px, wider than any group card — was what set the
    page's minimum width and put a horizontal scrollbar on a default-sized
    window. It is a tab strip, not a caption, so it reads fine a step down.
    """

    collapsedChanged = QtCore.Signal(bool)

    def __init__(self, title: str) -> None:
        super().__init__(f"▸  {title}")
        self._title = title
        self._collapsed = True
        # The OPEN arrow is green (2026-09-26), which needs rich text: the
        # glyph carries its own colour span while the title keeps the label's
        # muted one. So `.text()` is no longer the plain title — `title()` is
        # the accessor, and every caller that used to strip the arrow by hand
        # uses it instead.
        self.setTextFormat(QtCore.Qt.RichText)
        self.setStyleSheet(
            f"font-size: 14px; font-weight: 600; color: {theme.MUTED};")
        # The caption's text is centered inside its own cell of the fold row
        # (2026-09-25): Expanding gives each of the three captions an equal
        # third of the row, so they read as three evenly-spaced tabs rather
        # than a left-packed run of hint-width labels.
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding,
                           QtWidgets.QSizePolicy.Preferred)
        self.setAlignment(QtCore.Qt.AlignCenter)

    def set_collapsed(self, collapsed: bool) -> None:
        if collapsed == self._collapsed:
            return
        self._collapsed = collapsed
        self.setText(f"▸  {self._title}" if collapsed else
                     f'<span style="color:{theme.GOOD};">▾</span>'
                     f'&nbsp;&nbsp;{self._title}')
        self.collapsedChanged.emit(collapsed)

    def is_collapsed(self) -> bool:
        return self._collapsed

    def title(self) -> str:
        """The group name, WITHOUT the arrow — the only safe way to read one
        of these now that the open state is rich text (2026-09-26)."""
        return self._title

    # The hover brighten (enterEvent/leaveEvent) and the pointing-hand cursor
    # that used to live here are gone (2026-09-26): this page has no hover
    # states at all, so a tab looks the same under the mouse as at rest. The
    # arrow still says open or shut, and the tab is still clickable.

    def mousePressEvent(self, e: QtGui.QMouseEvent) -> None:
        if e.button() != QtCore.Qt.LeftButton:
            return
        # Clicking the OPEN tab does NOT close it (2026-09-26). The row is an
        # accordion with a standing rule: exactly one group is expanded, all
        # the time. Toggling here used to let a click fold the last open group,
        # which left the body under the row empty — a block of nothing, with no
        # way to tell from the row which group had been open. So the open tab is
        # a no-op; opening another tab is how the choice changes.
        if self._collapsed:
            self.set_collapsed(False)
        super().mousePressEvent(e)




class PartyProfileMixin(PartyGridMixin, PartyRosterMixin, PartyStatsMixin):
    """Mixin on PartyPageMixin: the detail column (Total Stats, the selected
    piece, the Buffs block) plus the two concerns split out of this file to
    stay under the 840-line budget — the paper-doll grid (`PartyGridMixin`,
    which owns the slot-group table and re-exports `_SLOT_GROUPS`) and the
    PLAYERS roster column (`PartyRosterMixin`, with its `_ElideLabel`)."""

    # ------------------------------------------------------------------ build

    # ------------------------------------------------------------------ build
    def _party_profile_build(self, right: QtWidgets.QVBoxLayout) -> None:
        self._party_piece_kind: str = ""
        self._party_piece_gear: list[dict] = []
        self._party_grid_cards: dict[int, QtWidgets.QFrame] = {}
        self._party_pet_box: QtWidgets.QFrame | None = None
        self._party_group_cards: list[QtWidgets.QFrame] = []

        # --- equipment panel: worn column + fold row | detail column -------
        # No section header: the tiles ARE the section (2026-09-25 — the
        # PLAYERS / PAPER DOLL / BUFFS caption bars were removed with the
        # page's own header; the group cards name themselves). Jewelry and
        # the carried groups (Extras/Tools) are NOT cards in this column
        # any more: their captions sit side by side in ONE fold row and the
        # clicked one expands its grid beneath it (2026-09-26 — Jewelry joined
        # the row; it used to be the card the row lived INSIDE).
        worn = QtWidgets.QVBoxLayout()
        worn.setSpacing(6)
        worn.setContentsMargins(0, 0, 0, 0)    # see the column-margin note
                                       # in party_page._page_party
        carried = QtWidgets.QVBoxLayout()
        carried.setSpacing(6)
        carried.setContentsMargins(0, 0, 0, 0)  # ditto
        by_title = dict(_SLOT_GROUPS)
        fold_caps: list[tuple[_FoldCaption, QtWidgets.QFrame]] = []
        moved_caps: list[tuple[_FoldCaption, QtWidgets.QFrame]] = []
        for title in _WORN_GROUPS:
            if title in _FOLD_GROUPS:
                continue            # Jewelry: a fold tab, built below
            card = self._party_group_card(title, by_title[title])
            self._party_group_cards.append(card)
            worn.addWidget(card, 0, QtCore.Qt.AlignLeft)
        for title in _FOLD_GROUPS + tuple(_STANDALONE_GROUPS):
            slots = by_title[title]
            if title == "Extras":
                # the pet tile leads this group: it is not a slot (the id lives
                # on st.Loadout.pet), so it is built by the pet read rather
                # than the gear walk (see _party_pet_fill).
                card = self._party_group_card(
                    title, slots, lead=self._party_pet_box_card(),
                    inline_head=False, flat=True)
            else:
                card = self._party_group_card(title, slots, inline_head=False,
                                              flat=True)
            self._party_group_cards.append(card)
            pair_ = (moved_caps if title in _STANDALONE_GROUPS else fold_caps)
            pair_.append((_FoldCaption(title), card))

        # --- fold row: the tab captions side by side, over a body that shows
        # ONE group's grid. Clicking one expands that group's grid in the body
        # BENEATH the row (accordion: opening one folds the other), so
        # expansion grows downward and never shoves the detail column sideways.
        # The row lives in its own Card (2026-09-26) — it used to sit INSIDE
        # the Jewelry card, which only worked while Jewelry was a card of its
        # own; now that Jewelry is a tab, the row is the card.
        fold_caps2 = []
        fold_row = QtWidgets.QHBoxLayout()
        fold_row.setSpacing(10)
        fold_row.setContentsMargins(2, 0, 0, 0)
        fold_body = QtWidgets.QWidget()
        fold_lay = QtWidgets.QVBoxLayout(fold_body)
        fold_lay.setContentsMargins(0, 0, 0, 0)
        fold_lay.setSpacing(0)
        for cap, card in fold_caps:
            fold_row.addWidget(cap)
            fold_lay.addWidget(card)
            card.hide()          # owned by the body; shown when expanded

        def _fold_changed(cap, card, collapsed):
            if collapsed:
                card.hide()
                return
            for other_cap, other_card in fold_caps:
                if other_cap is not cap:
                    other_cap.set_collapsed(True)
            for _c, other_card in fold_caps:
                other_card.hide()
            card.setVisible(True)

        for cap, card in fold_caps:
            cap.collapsedChanged.connect(
                lambda collapsed, c=cap, k=card: _fold_changed(c, k, collapsed))
        fold_caps2.extend(fold_caps)
        self._party_fold_caps = fold_caps2
        fold_host = QtWidgets.QWidget()
        fold_host.setLayout(fold_row)
        # The captions get EQUAL cells, each as wide as the widest of them, and
        # center their own text inside the cell (2026-09-25) — a minimum width
        # rather than fixed widths, so a wider window still splits the extra
        # evenly and a longer title only widens the minimum. Without it each
        # caption was hint-width, so the strip read as left-packed words
        # rather than centered tabs.
        cap_w = max((c.sizeHint().width() for c, _k in fold_caps), default=0)
        if cap_w:
            fold_host.setMinimumWidth(
                cap_w * len(fold_caps) + fold_row.spacing() * (len(fold_caps) - 1))
        # the body is as wide as the widest fold card, pinned BEFORE anything
        # expands — so even opening the widest group cannot nudge the columns;
        # a 3-tile card's grid parks its slack in its own stretch column (see
        # the grid comment in _party_group_card)
        fold_w = max((k.sizeHint().width() for _c, k in fold_caps), default=0)
        if fold_w:
            fold_body.setFixedWidth(fold_w)
        # …and as TALL as the tallest fold card (2026-09-26). Opening a group
        # used to grow the body from nothing to a full card, which grew the
        # doll column and shoved the whole page down every time you clicked a
        # caption. The body is now the same height open or shut — the same
        # trade the three stat blocks and the Buffs block make — so expanding
        # fills the space instead of pushing it.
        fold_h = max((k.sizeHint().height() for _c, k in fold_caps), default=0)
        if fold_h:
            fold_body.setFixedHeight(fold_h)
        # The row and its body share one Card, in the worn column BELOW the
        # cards that are still cards (Weapons, Armor). It is styled like every
        # other block so the row does not read as a loose strip of words
        # floating between two bordered cards.
        fold_card = QtWidgets.QFrame()
        fold_card.setObjectName("Card")
        fold_card.setStyleSheet(
            f"QFrame#Card{{background:{theme.PANEL};border:1px solid "
            f"{theme.BORDER};border-radius:6px;}}")
        fcol = QtWidgets.QVBoxLayout(fold_card)
        fcol.setContentsMargins(8, 6, 8, 6)
        fcol.setSpacing(4)
        fcol.addWidget(fold_host)
        fcol.addWidget(fold_body)
        self._party_fold_card = fold_card
        worn.addWidget(fold_card, 0, QtCore.Qt.AlignLeft)
        # The default tab ships OPEN (2026-09-26): Jewelry is what you open
        # this page for, and an accordion that starts shut hides its own
        # first tab. Done through the same signal a click uses, so the initial
        # state cannot disagree with what a click would produce.
        for cap, _card in fold_caps:
            if cap.title() == _FOLD_DEFAULT:
                cap.set_collapsed(False)

        # --- detail column: STAT TOTALS first, then piece, then buffs ------
        # (2026-09-25: totals moved to the top — it is the read most wanted —
        # and the column got a FIXED width: its content is wrapping text whose
        # natural hint changed every fill, which is what made the columns
        # wander. Now only text wraps; the column edges never move.)
        equip = max((w.sizeHint().width() for w in self._party_group_cards),
                    default=0)
        # The detail column is 10% NARROWER than the widest group card
        # (2026-09-26): it is the page's third column and its three blocks
        # (Total Stats, the piece, Buffs) are short key/value tables that
        # never used the extra 30px, while the paper doll next to it was
        # mostly empty space beside its tiles. The tiles took that space
        # (see party_grid._TILE_W); this keeps the page from widening. The
        # floor is the width below which a stat key + its value start to
        # collide, so a future smaller doll cannot squeeze the table.
        detail = max(equip - 30, int(equip * 0.9)) if equip else 0
        # A group that lives in this column (Consumables) has to fit INSIDE the
        # Buffs card: its card is a row of tiles, so the column widens to the
        # card plus the block's own padding rather than crushing the tiles
        # (2026-09-26). The right column had the slack — that is where the
        # space went.
        moved_w = max((k.sizeHint().width() for _c, k in moved_caps), default=0)
        detail = max(detail, moved_w + _BLOCK_PAD)
        # --- selected piece panel ------------------------------------------
        # The SAME shape as the Total Stats block below it (2026-09-25): a Card
        # with a quiet caption over key/value rows, so the two readouts in this
        # column read as one family instead of a bare list of "Faith: 12"
        # lines floating under a card. The caption IS the piece's name line
        # (it changes with the selection); the rows are that piece's own
        # stats, coloured by stat identity exactly like the totals.
        self._party_piece = QtWidgets.QVBoxLayout()
        self._party_piece.setSpacing(2)
        host = QtWidgets.QFrame()
        host.setObjectName("Card")
        host.setStyleSheet(
            f"QFrame#Card{{background:{theme.PANEL};border:1px solid "
            f"{theme.BORDER};border-radius:6px;}}")
        hcol = QtWidgets.QVBoxLayout(host)
        hcol.setContentsMargins(8, 6, 8, 6)
        hcol.setSpacing(3)
        # The caption is the item's NAME over two lines, then the rarity /
        # upgrade / level under it (2026-09-26). The name uses both lines and
        # only then clips ("Beefury, Blessed Blade of the Farseeker" -> two
        # lines with the tail dropped); run together and word-wrapped it went
        # over three and four lines, and single-line eliding threw away a whole
        # free line. The name area is ALWAYS two lines tall, so the card is the
        # same height for every item.
        cap = _Elide2Label("Selected Piece")
        # the name wears its RARITY colour too (2026-09-26) — the same one the
        # meta line's rarity word uses, set per fill by `_piece_cap_color`. The
        # style lives in one helper rather than being restyled inline, because
        # the font-size/weight are set here and a second copy of this string
        # would eventually drift from it.
        self._party_piece_cap = cap
        self._piece_cap_color("")
        meta = _MetaLabel()
        meta.setStyleSheet(f"color:{theme.DIM};font-size:14px;")
        self._party_piece_meta = meta
        hcol.addWidget(cap)
        hcol.addWidget(meta)
        rows_host = QtWidgets.QWidget()
        rows_host.setLayout(self._party_piece)
        hcol.addWidget(rows_host)
        host._party_rows = self._party_piece   # tests read the block's rows
        self._party_piece_host = host
        # --- stat totals: every equipped piece's stats, summed -------------
        # One block adding up the same computed stats the piece panel shows
        # one piece at a time (sheet math only — no process reads).
        tot = QtWidgets.QFrame()
        tot.setObjectName("Card")
        tot.setStyleSheet(
            f"QFrame#Card{{background:{theme.PANEL};border:1px solid "
            f"{theme.BORDER};border-radius:6px;}}")
        tcol = QtWidgets.QVBoxLayout(tot)
        tcol.setContentsMargins(8, 6, 8, 6)
        tcol.setSpacing(3)
        tcap = QtWidgets.QLabel("Total Stats")
        tcap.setStyleSheet(
            f"font-size: 16px; font-weight: 600; color: {theme.MUTED};")
        tcol.addWidget(tcap)
        # the caption on its own handle, like the Players and Buffs captions
        # (2026-09-26 — the block reads "Total Stats", not "Stat Totals")
        self._party_totals_cap = tcap
        self._party_totals_lay = QtWidgets.QVBoxLayout()
        self._party_totals_lay.setSpacing(2)
        thost = QtWidgets.QWidget()
        thost.setLayout(self._party_totals_lay)
        tcol.addWidget(thost)
        tot._party_rows = self._party_totals_lay   # same handle for tests
        if detail:
            tot.setFixedWidth(detail)
        self._party_totals_card = tot
        carried.addWidget(tot, 0, QtCore.Qt.AlignLeft)
        carried.addWidget(host, 0, QtCore.Qt.AlignLeft)
        if detail:
            host.setFixedWidth(detail)
        # Buffs + gear notes stack under the carried column — not in a third,
        # far-right column: they describe the selected piece, so they sit with
        # it (roster | doll is the whole page width).
        # The buff list is now a BLOCK in the same mould as the two readouts
        # above it (2026-09-26): the same Card, a "Buffs" caption, and a
        # height pinned to _BUFF_ROWS rows that SCROLLS when a hero carries
        # more statuses than fit — so three buffs and twenty leave the NOT READ
        # notes below them exactly where they were, instead of shoving them up
        # and down the page on every refresh.
        self._party_buffs_hdr = None   # gone (2026-09-25); kept unset for compat
        buffs = QtWidgets.QFrame()
        buffs.setObjectName("Card")
        buffs.setStyleSheet(
            f"QFrame#Card{{background:{theme.PANEL};border:1px solid "
            f"{theme.BORDER};border-radius:6px;}}")
        bcol = QtWidgets.QVBoxLayout(buffs)
        bcol.setContentsMargins(8, 6, 8, 6)
        bcol.setSpacing(3)
        self._party_buffs = QtWidgets.QVBoxLayout()
        self._party_buffs.setSpacing(4)
        # margin-free, like every other column wrapper (2026-09-26): the cards
        # inside bring their own 8/6, and the default 9px here meant the host
        # was 18px taller than the pin below — so the fifth buff card was
        # sliced by the pin's edge and the block scrolled for rows it was
        # already showing.
        self._party_buffs.setContentsMargins(0, 0, 0, 0)
        buffs_host = QtWidgets.QWidget()
        buffs_host.setLayout(self._party_buffs)
        area = QtWidgets.QScrollArea()
        area.setWidget(buffs_host)
        area.setWidgetResizable(True)
        area.setFrameShape(QtWidgets.QFrame.NoFrame)
        area.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        area.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        self._party_buffs_host = buffs_host
        self._party_buffs_area = area
        self._party_buffs_card = buffs
        # the pin, measured off a real (unparented, then dropped) buff CARD's
        # own sizeHint — a guessed pixel count would drift with the font, and
        # a card's height is padding + border + content, which no single label
        # metric knows about. The probe carries a parenthetical because that
        # is the TALLER row (name line + meta line); a one-line card is 9px
        # shorter and simply leaves slack.
        probe = self._party_buff_row("Status_Id", "Whetstone",
                                     "(from Cheese Moon)")
        row_h = probe.sizeHint().height()
        del probe
        area.setFixedHeight(row_h * _BUFF_ROWS
                            + self._party_buffs.spacing()
                            * (_BUFF_ROWS - 1))
        buff_h = area.height()
        # --- the block is an ACCORDION, the fold row's twin (2026-09-26) ----
        # "Buffs" and "Consumables" are now a row of equal, centred tabs over
        # a body showing ONE of them, exactly like "Jewelry / Extras / Tools"
        # in the doll column — same _FoldCaption, same equal cells, same
        # one-open-at-a-time rule, same pinned body. It used to be a static
        # caption over a list that was ALWAYS visible with the Consumables
        # caption and card appended under it, so the two halves of one block
        # were laid out two different ways.
        bcap = _FoldCaption("Buffs")
        self._party_buffs_cap = bcap
        tabs: list[tuple[_FoldCaption, QtWidgets.QWidget]] = [(bcap, area)]
        for _cap, card in moved_caps:
            card.setFixedHeight(card.sizeHint().height())
            tabs.append((_cap, card))

        brow = QtWidgets.QHBoxLayout()
        brow.setSpacing(10)
        brow.setContentsMargins(2, 0, 0, 0)
        bbody = QtWidgets.QWidget()
        blay = QtWidgets.QVBoxLayout(bbody)
        blay.setContentsMargins(0, 0, 0, 0)
        blay.setSpacing(0)
        for cap, w in tabs:
            brow.addWidget(cap)
            blay.addWidget(w)
            w.hide()          # owned by the body; shown when its tab is open
        brow_host = QtWidgets.QWidget()
        brow_host.setLayout(brow)
        bcol.addWidget(brow_host)
        bcol.addWidget(bbody)
        # equal cells, each as wide as the widest tab (see the fold row)
        bcap_w = max(c.sizeHint().width() for c, _w in tabs)
        if bcap_w:
            brow_host.setMinimumWidth(
                bcap_w * len(tabs) + brow.spacing() * (len(tabs) - 1))

        def _btab_changed(cap, widget, collapsed):
            """One open at a time; opening a tab folds the rest (see the fold
            row — _FoldCaption already refuses to close the open tab, so the
            body can never end up empty)."""
            if collapsed:
                widget.hide()
                return
            for other_cap, _ow in tabs:
                if other_cap is not cap:
                    other_cap.set_collapsed(True)
            for _oc, ow in tabs:
                ow.hide()
            widget.setVisible(True)

        for cap, w in tabs:
            cap.collapsedChanged.connect(
                lambda collapsed, c=cap, k=w: _btab_changed(c, k, collapsed))
        # The body is as TALL as its tallest member, so opening a tab fills the
        # space instead of pushing the gear notes below it down the page — the
        # same trade the doll column's fold body makes.
        bbody.setFixedHeight(max([buff_h] +
                                 [w.sizeHint().height() for _c, w in tabs]))
        # Buffs is the tab that ships OPEN: it is this block's own readout and
        # it used to be unconditionally visible, so opening Consumables by
        # default would take something away that used to always be there.
        bcap.set_collapsed(False)
        # The moved group is now a TAB of this block rather than a caption and
        # card appended under the list (see the accordion above); the handle
        # tests reach it by is unchanged.
        self._party_moved_caps = moved_caps
        carried.addWidget(buffs, 0, QtCore.Qt.AlignLeft)
        if detail:
            buffs.setFixedWidth(detail)
        # the text gear LIST is gone (the paper-doll grid replaces it);
        # NOT READ warnings and the pet still render into _party_gear below it
        self._party_gear = QtWidgets.QVBoxLayout()
        self._party_gear.setSpacing(4)
        gear_host = QtWidgets.QWidget()
        gear_host.setLayout(self._party_gear)
        carried.addWidget(gear_host)
        carried.addStretch(1)

        # Two columns side by side, each as wide as its own cards; the pair is
        # added at stretch 0 so the slack stays to its right (the same rule the
        # page's own three columns follow).
        worn_box = QtWidgets.QWidget()
        worn_box.setLayout(worn)
        carried_box = QtWidgets.QWidget()
        carried_box.setLayout(carried)
        self._party_worn_box = worn_box
        self._party_carried_box = carried_box
        pair = QtWidgets.QHBoxLayout()
        pair.setSpacing(12)
        pair.setContentsMargins(0, 0, 0, 0)
        pair.addWidget(worn_box, 0, QtCore.Qt.AlignTop)
        pair.addWidget(carried_box, 0, QtCore.Qt.AlignTop)
        pair.addStretch(1)
        right.addLayout(pair, 0)

    # ------------------------------------------------------------------ fill
    def _party_profile_fill(self, gear: list[dict]) -> None:
        """Called from _refresh_party_detail with the fresh gear walk."""
        self._party_piece_gear = [g for g in gear
                                  if g.get("state", "filled") == "filled"]
        # paper-doll grid: fill each card from the live walk (and blank the
        # cards whose slot came back empty/unreadable so a re-walk can't
        # leave a stale piece showing)
        for g in gear:
            slot = g.get("slot")
            if isinstance(slot, int) and slot in self._party_grid_cards:
                card = self._party_grid_cards[slot]
                if g.get("state", "filled") == "filled" and g.get("kind"):
                    self._party_slot_fill(slot, g)
                elif card._party_slot_kind or not card._party_slot_read:
                    # Empty now. Reset when the slot was showing something, AND
                    # when it has never been read (2026-09-26) — the second
                    # half is what lets a walk turn a neutral, unread tile
                    # into the dim "nothing in here" one. It used to be
                    # guarded on the tile having held a piece, so a slot the
                    # hero simply does not use stayed neutral for the whole
                    # session and never dimmed at all.
                    self._party_slot_reset(slot)
                # the walk has now answered for this slot, whatever it said
                card._party_slot_read = True             # type: ignore[attr-defined]
        # The selected piece must belong to the hero on screen (2026-09-26).
        # `_party_piece_kind` is an ITEM, not a slot, and it used to survive
        # every hero switch — so after clicking a chest on one hero and
        # switching to another, the panel showed that chest again, with
        # rarity "Common", no upgrade and no level, for a hero who was never
        # wearing it. Every one of those numbers was invented: the row lookup
        # found nothing and the defaults filled in. So a selection that is not
        # among this hero's pieces is dropped, and the default below takes
        # over.
        worn = {g.get("kind") for g in self._party_piece_gear if g.get("kind")}
        if self._party_piece_kind and self._party_piece_kind not in worn:
            self._party_piece_kind = ""
        if not self._party_piece_kind:
            # default: a weapon, else the first filled piece of any slot —
            # the detail always shows a live piece when one exists, so no
            # instructional placeholder is needed
            ordered = sorted(self._party_piece_gear,
                             key=lambda g: (g.get("slot") not in (0, 1, 2),
                                            g.get("slot", 99)))
            if ordered:
                self._party_piece_kind = ordered[0].get("kind") or ""
        self._fill_party_piece()
        self._fill_party_totals()

    def _piece_cap_color(self, rarity: str) -> None:
        """Paint the Selected Piece NAME in the piece's rarity colour.

        An empty `rarity` means "no piece selected" and paints the ordinary
        caption grey — a rarity colour on a placeholder would be a claim about
        an item that is not there. The whole stylesheet is rewritten rather
        than just the colour because the size and weight are part of the same
        declaration; keeping it in ONE place is what stops a later font change
        landing on one label and missing the other.

        Called from the build too, before any piece is selected, so the
        placeholder starts grey rather than unstyled.
        """
        cap = getattr(self, "_party_piece_cap", None)
        if cap is None or not self._widget_alive(cap):
            return
        colour = theme.rarity_color(rarity) if rarity else theme.MUTED
        sheet = f"font-size: 16px; font-weight: 600; color: {colour};"
        if cap.styleSheet() != sheet:
            cap.setStyleSheet(sheet)

    def _fill_party_piece(self) -> None:
        lay = getattr(self, "_party_piece", None)
        cap = getattr(self, "_party_piece_cap", None)
        meta = getattr(self, "_party_piece_meta", None)
        if lay is None or not self._widget_alive(lay.parentWidget()):
            return
        while lay.count():
            it = lay.takeAt(0)
            w = it.widget()
            if w is not None:
                w.deleteLater()
        kind = self._party_piece_kind
        if cap is not None and self._widget_alive(cap):
            # the card's caption is the piece itself (2026-09-25) — it was a
            # bare label line in an unframed column. With nothing selected
            # there is no rarity, so the placeholder wears the ordinary caption
            # grey: a name in Legendary orange would be claiming a rarity for
            # no item.
            cap.set_full("Selected Piece")
            self._piece_cap_color("")
        if meta is not None and self._widget_alive(meta):
            meta.setText("")
        if not kind:
            # Blank, not annotated (2026-09-26): "No gear live." said less
            # than the empty panel it sat in, and the caption above already
            # reads "Selected Piece" — a block with that heading and no rows
            # is a hero wearing nothing, not a reader that failed. The lock
            # rows still size it so the block keeps its height.
            self._party_lock_rows(lay)
            return
        row = next((g for g in self._party_piece_gear
                    if g.get("kind") == kind), None)
        rarity = (row or {}).get("rarity") or "Common"
        upgrade = (row or {}).get("upgrade") or 0
        level = (row or {}).get("level") or 0
        # `item_label` (item name, else humanized id), for the reason given in
        # party_grid: `item_name` returns an unknown id verbatim, so the
        # humanize that used to sit behind it was unreachable.
        nm = gnames.item_label(kind)
        if cap is not None and self._widget_alive(cap):
            # line 1: the name, ELIDED to the card's width (2026-09-26) and
            # painted in the piece's own rarity colour — the rarity is the
            # first thing you want off an item, and having it only on the small
            # word underneath meant reading the name before you could see it
            cap.set_full(nm)
            self._piece_cap_color(rarity)
        if meta is not None and self._widget_alive(meta):
            # line 2: what the sheet says about it — the two lines never wrap,
            # so the card is the same height for every item. The RARITY is
            # coloured in the sheet's own rarity colour (2026-09-26): "Rare",
            # "Epic" and "Legendary" all read as the same grey word otherwise,
            # so the one fact that decides whether a drop is worth keeping is
            # the one word on the line you cannot pick out. The same palette
            # the doll's tile borders use, so the two never disagree. The
            # tail elides rather than clips (see _MetaLabel).
            rest = (f"  +{upgrade}" if upgrade else "") \
                + (f"  ·  Lv {level}" if level else "")
            meta.set_meta(rarity, rest)

        item = (gcatalog._data().get("items", {}) or {}).get(kind) or {}
        # equipped_stats, not gear_stats: `upgrade` has been read from the
        # live item and printed as "+5" in the caption three lines above
        # since the panel was written, and the maths below ignored it — so a
        # maxed weapon's stats read as its +0 stats. The caption and the
        # numbers were describing two different weapons.
        gs = gstats.equipped_stats(item, level or None, rarity or None,
                                   upgrade)
        if gs:
            self._party_stat_rows(lay, [
                (s.get("label") or "",
                 gstats.stat_text(s["value"], s.get("raw")), "")
                for s in gstats.stat_rows(gs["stats"])])
        else:
            # No line here (2026-09-26): a piece with nothing computable is
            # an empty block, and "No computed stats for this piece." said
            # less than the blank it replaced. The caption above still
            # names the piece, so the block is not unexplained — it is
            # simply a piece that grants no stats. The lock rows still run,
            # or the block would collapse to nothing and shove the column.
            self._party_lock_rows(lay)
        # no Weapon Upgraded ladder, no Upgrade Cost here: passives and
        # crafting materials belong to the Gear and Craft pages, not the
        # Party inspect

