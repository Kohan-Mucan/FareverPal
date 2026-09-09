"""The "Contains" section on a container's item page.

A cache is a BOX: the interesting fact about it is not its own stats (it has
none) but what it opens into. The scan never captured the boss -> cache edge
or the cache's own drops, so this reads the container's loot table (see
`data.items.sources.container_contents`) and lists the actual pieces,
slot-ordered, with their rarity.

EVERY compiled `LootableContainer` is rendered here, not only the hand-specced
Heroic/Rift boxes: the generic `container_spec`/`container_contents` derive an
unclaimed box (the starter "Mysterious Cache") from its compiler-baked
`gain_item`, so the Items page surfaces it instead of hiding it behind a
test-only exclusion.

Two things the naive list gets wrong, both handled here:
  * a box ROLLS its contents — the Hero Gear Cache lists 25 pieces but an open
    yields at most 2, so the header says "up to 2 per open";
  * the weapon cache's table holds level-4 RARE craft templates, not the piece
    you get. Those rows are marked `template`, and the section says the weapon
    is rolled to the box's rarity/level rather than listing four Rares as
    contents.

Lives beside `detail.py` rather than in it: that file sits just under the
840-line budget the UI test enforces, and this is a self-contained concern.
"""
from __future__ import annotations

from PySide6 import QtCore, QtWidgets

from ... import components as C
from ... import theme
from .drops import _NPC_SPRITES, _codex_unit_sprite, vendor_sprite
from ....data import icons
from ....data.items.sources import (
    container_contents,
    container_spec,
    currency_label,
    mog_exchange,
    vendor_caches,
)


def _note(text: str) -> QtWidgets.QLabel:
    """A one-line DIM explainer under a section header."""
    lab = QtWidgets.QLabel(text)
    lab.setObjectName("Muted")
    lab.setWordWrap(True)
    lab.setMaximumWidth(620)
    return lab


def _rarity_span(spec: dict) -> tuple[str, str]:
    """The (floor, ceiling) rarities ONE OPEN can hand out.

    `heroic_cache_spec` is the single fact here: the span is a point for a
    fixed box and Epic..Legendary for the Hero Weapon Cache. Falls back to the
    container's own rarity, so a spec built without the keys still reads as a
    fixed box rather than rendering a bogus upgrade.
    """
    rar = spec.get("rarity") or ""
    return (spec.get("rarity_min") or rar, spec.get("rarity_max") or rar)


def build_cache_box(cache_id: str) -> QtWidgets.QWidget | None:
    """The Contains widget for `cache_id`, or None when it is not a cache or
    its contents are not recorded (the caller then says nothing rather than
    rendering an empty section header)."""
    box = (container_contents().get(cache_id) or ())
    spec = container_spec().get(cache_id) or {}
    if not box:
        return None

    w = QtWidgets.QWidget()
    lay = QtWidgets.QVBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(4)

    rarities = sorted({r["rarity"] for r in box if r["rarity"]})
    tag = " · ".join(rarities) if rarities else "gear"
    # `tag` above is the box's OWN tier; what an open hands out is the span
    # below it. They differ for the Hero Weapon Cache (its roll is clamped at
    # Epic and can climb to Legendary), and there a bare "Epic" is the floor
    # told as if it were the whole story.
    floor, ceiling = _rarity_span(spec)
    upgradable = bool(ceiling and floor and ceiling != floor)
    if upgradable:
        tag = f"{floor}+"
    lay.addWidget(C.SectionHeader(
        "Contains", tag=f"{len(box)} {tag.upper()} PIECE"
                        f"{'S' if len(box) != 1 else ''}"))

    per_open = spec.get("max_items")
    player_scale = bool(spec.get("player_scale"))
    lvl = spec.get("level_max")
    if per_open and per_open < len(box):
        # a player-scale box has no fixed level to name here; its own note
        # below says "your level", and saying "level 25" too would be both
        # wrong and contradictory
        tail = "" if player_scale else (f", at gear level {lvl}."
                                        if lvl else ".")
        lay.addWidget(_note(f"One open rolls at most {per_open} of these"
                            + tail))

    if player_scale:
        lay.addWidget(_note(
            f"Rolled at YOUR level{', up to ' + str(lvl) if lvl else ''} — "
            "the pieces keep their names but come out scaled to you, so the "
            "exact level is not knowable until you open one."))

    templ = bool(spec.get("template"))
    if templ:
        # the named rows are the base templates; say so before the list, or the
        # rarity tag on each row reads as the rarity you receive
        rar = f"{floor} or better" if upgradable else (
            spec.get("rarity") or "the box's")
        note = (f"Rolled to {rar}"
                + (f" gear level {lvl}" if lvl else "")
                + " from a base weapon — ")
        if upgradable:
            # the ceiling is a real outcome, not a range: one open showed an
            # Epic sword and a Legendary shield side by side
            note += (f"one open can come out as high as {ceiling} — "
                     "the name below is the template it is built from, not "
                     "the piece you get.")
        else:
            note += ("the name below is the template it is built from, not "
                     "the piece you get.")
        lay.addWidget(_note(note))

    for r in box:
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(8)
        # the slot is the useful column: a box lists 25 pieces across 8 slots,
        # so the name alone does not say what you can get out of it
        slot = QtWidgets.QLabel(r["slot"])
        slot.setObjectName("Mono")
        slot.setFixedWidth(84)
        slot.setStyleSheet(
            f"color:{theme.DIM};font-size:11px;background:transparent;")
        row.addWidget(slot, 0, QtCore.Qt.AlignVCenter)

        tile = C.IconTile(20)
        tile.set("item", r["item"], theme.rarity_color(r["rarity"]))
        row.addWidget(tile, 0, QtCore.Qt.AlignVCenter)

        nm = QtWidgets.QLabel(r["label"])
        nm.setStyleSheet(f"color:{theme.TEXT};background:transparent;")
        nm.setToolTip(f"{r['label']} — {r['rarity']}"
                      f"{' or better' if upgradable else ''} {r['slot']}")
        row.addWidget(nm, 1)

        if r["rarity"]:
            # the floor with a '+' when the roll can climb above it
            row.addWidget(C.RarityTag(f"{r['rarity']}+" if upgradable
                                      else r["rarity"]),
                          0, QtCore.Qt.AlignVCenter)
        lay.addLayout(row)
    return w


def build_mog_vendor(page=None) -> QtWidgets.QWidget:
    """The Medal of Glory vendor panel on the BadgeOfGlory page: what Shiro
    James stocks, priced in medals.

    Two sources, because he sells two kinds of thing. His CONSUMABLE stock
    rides the scan's BadgeOfGlory-cost shop rows (the same convention as Zoey's
    Spark Crystals and Mira's sigils) and is still unrecorded — the capture
    predates the priced rows. His CACHES are the two Hero boxes, which no
    sheet row can describe (the whole `HM_` family has `drops: []`, so nothing
    in the item data points at the seller) and which were read instead off his
    open shop on 2026-09-26: Hero Gear Cache and Hero Weapon Cache, 100
    medals each. Those are listed as the BOXES they are — one click opens what
    is inside — rather than flattened into their 28 pieces, which is the same
    treatment Mira's stock gets in the Dungeons tab.
    """
    stock = mog_exchange()
    boxes = vendor_caches("Shiro James")
    w = QtWidgets.QWidget()
    lay = QtWidgets.QVBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(4)

    if not stock and not boxes:
        lay.addWidget(C.SectionHeader("Shiro James Sells"))
        ph = QtWidgets.QLabel("Exchange stock not recorded by the scan yet "
                              "— check the vendor in-game.")
        ph.setObjectName("Muted")
        ph.setWordWrap(True)
        ph.setMaximumWidth(620)
        lay.addWidget(ph)
        return w

    n = len(stock) + len(boxes)
    lay.addWidget(C.SectionHeader(
        "Shiro James Sells",
        tag=f"{n} ITEM{'S' if n != 1 else ''}"))
    for r in stock:
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(8)
        nm = QtWidgets.QLabel(r["label"])
        nm.setStyleSheet(f"color:{theme.TEXT};background:transparent;")
        row.addWidget(nm, 1)
        price = QtWidgets.QLabel(f"{r['qty']:g} MoG" if r["qty"] else "MoG")
        price.setObjectName("Mono")
        price.setStyleSheet(
            f"color:{theme.GOLD};"
            f'font-family:"{theme.MONO_FONT}", "Consolas", monospace;'
            "font-size:11px;background:transparent;")
        price.setMinimumWidth(price.sizeHint().width())
        row.addWidget(price, 0, QtCore.Qt.AlignVCenter)
        lay.addLayout(row)

    for b in boxes:
        # the box is his product, not the pieces inside it; a click opens the
        # contents in the pane rather than the box's own statless page
        row = _mog_cache_row(page, b)
        if row is not None:
            lay.addWidget(row)
    return w


def _mog_cache_row(page, b: dict) -> QtWidgets.QFrame | None:
    """One Hero cache in Shiro's stock: icon, name, what it holds, and the
    medal price. Uses the shared vendor stock row so it reads exactly like
    Mira's boxes do in the Dungeons tab, and opens the same way."""
    if page is None:
        return None
    return cache_stock_row(page, b)


def vendor_upper(name: str) -> str:
    """Uppercase a vendor's name for a block header, leaving the apostrophe
    to the caller so 'Mira, Demon Huntress' reads as "MIRA, DEMON HUNTRESS'S"
    rather than "MIRA'S, DEMON HUNTERSS"."""
    return (name or "").upper()


def cache_stock_row(page, b: dict) -> QtWidgets.QFrame:
    """One box in a vendor's stock: its name, how many pieces it holds, and
    what it costs. The row is the vendor's real product — the pieces it opens
    into are listed separately — so it says how the roll works (a capped
    count, or that it scales to you) rather than implying a flat contents
    list."""
    row = QtWidgets.QFrame()
    row.setObjectName("Card")
    h = QtWidgets.QHBoxLayout(row)
    h.setContentsMargins(8, 6, 8, 6)
    h.setSpacing(10)

    tile = C.IconTile(30)
    tile.set(b["cache"], theme.rarity_color(b.get("rarity")))
    tile.setFocusPolicy(QtCore.Qt.NoFocus)
    h.addWidget(tile)

    col = QtWidgets.QVBoxLayout()
    col.setContentsMargins(0, 0, 0, 0)
    col.setSpacing(1)
    nm = QtWidgets.QLabel(b.get("name") or b.get("cache") or "")
    nm.setStyleSheet(
        f"color:{theme.TEXT};font-weight:700;font-size:13px;"
        "background:transparent;border:0;outline:0;")
    col.addWidget(nm)
    if b.get("rarity"):
        n, p = b["n_pieces"], b.get("player_scale")
        how = (f"{n} pieces · at YOUR level" if p
               else f"{n} pieces" + (f" · gear level {b['level']}"
                                     if b.get("level") else ""))
        sub = QtWidgets.QLabel(f"{b['rarity']} · {how}")
        sub.setStyleSheet(
            f"color:{theme.DIM};font-size:11px;"
            "background:transparent;border:0;outline:0;")
        col.addWidget(sub)
    # A box can sit on more than one counter, and this pane is reached from
    # ONE of them — so say so, rather than letting the vendor's pane read as
    # the only way to buy it.
    also = tuple(b.get("also_sold_by") or ())
    if also:
        alt = QtWidgets.QLabel("also sold by " + ", ".join(also))
        alt.setStyleSheet(
            f"color:{theme.DIM};font-size:11px;"
            "background:transparent;border:0;outline:0;")
        col.addWidget(alt)
    h.addLayout(col, 3)

    # A recorded sale names no currency, only an amount, so it renders as the
    # bare number the SHOP column shows rather than an invented coin.
    bits = [f"{amt:,} {currency_label(kind)}"
            for kind, amt in b.get("cost") or ()]
    if not bits and b.get("shop_price") is not None:
        bits.append(f"{b['shop_price']:,} shop")
    price = " · ".join(bits)
    pr = None
    if price:
        pr = QtWidgets.QLabel(price)
        pr.setObjectName("Mono")
        pr.setStyleSheet(
            f"color:{theme.GOLD};"
            f'font-family:"{theme.MONO_FONT}", "Consolas", monospace;'
            "font-size:11px;background:transparent;border:0;outline:0;")
        pr.setMinimumWidth(pr.sizeHint().width())
        h.addWidget(pr, 0, QtCore.Qt.AlignVCenter)
    if b.get("locs"):
        where = "sold at: " + ", ".join(b["locs"])
        if pr is not None:
            pr.setToolTip(where)
        else:
            row.setToolTip(where)
    return row


def cache_boss_chip(page, c: dict) -> QtWidgets.QFrame:
    """One box on a vendor line: icon + rarity-tinted name.

    The chip is the box itself rather than one of the pieces inside it, so it
    has to sit beside the weapon chips and read the same way. A box nothing
    hands out yet (see `data.items.sources.cache_is_unreleased`) is told apart
    by its DIM name and by the tooltip — the vendor list carries no
    UNRELEASED tag, because two boxes already share the name "Chaotic Gear
    Cache" and stamping that word on one of them read as noise, not as
    information. The box's own pane is where that story is told.
    """
    cid = c.get("cache") or ""
    name = c.get("name") or cid
    un = bool(c.get("unreleased"))
    ncol = theme.DIM if un else theme.rarity_color(c.get("rarity") or "")
    frame = QtWidgets.QFrame()
    frame.setObjectName("Card")
    h = QtWidgets.QHBoxLayout(frame)
    h.setContentsMargins(6, 4, 6, 4)
    h.setSpacing(6)
    icon = QtWidgets.QLabel()
    icon.setPixmap(icons.item_tile(cid, name, 32, ncol))
    h.addWidget(icon)
    col = QtWidgets.QVBoxLayout()
    col.setContentsMargins(0, 0, 0, 0)
    col.setSpacing(0)
    nm = QtWidgets.QLabel(name)
    nm.setStyleSheet(
        f"color:{ncol};font-weight:600;font-size:11.5px;"
        "background:transparent;")
    nm.setWordWrap(True)
    col.addWidget(nm)
    h.addLayout(col, 1)
    tip = [f"{name} — {c.get('rarity') or 'gear'} box, "
           f"{c.get('n_pieces', 0)} pieces"]
    if un:
        tip.append("UNRELEASED: no boss, vendor, chest or crate in the game "
                    "data hands this box out")
    elif c.get("vendor"):
        tip.append(f"sold by {c['vendor']}")
    frame.setToolTip(chr(10).join(tip)
                     + chr(10) + "Click to list what is inside")
    frame.setCursor(QtCore.Qt.PointingHandCursor)

    def _open(ev, _p=page, _i=cid):
        # a box is not an item you equip: clicking one narrows the pane to its
        # contents, the same pick gesture a boss row makes, instead of opening
        # the box's own (statless) item page
        dv = getattr(_p, "_items_dungeon_view", None)
        if dv is not None:
            dv.select_box(_p._dg_shown_faction
                          or getattr(dv, "_sel", ("", ""))[0], _i)
        else:
            _p._items_show_id(_i)
        ev.accept()
    frame.mouseReleaseEvent = _open
    return frame


def vendor_line(page, boxes, vendor, source_id="") -> QtWidgets.QFrame | None:
    """The faction VENDOR line in the dungeon list, shaped like a BOSS row.

    A boss row is one horizontal line: sprite, name, then that boss's weapon
    chips to the RIGHT of the name. The vendor line was a vertical stack —
    sprite and name on top, the boxes on a SECOND row underneath — so reading
    down the list, every boss carried its own drops on its own line and then
    the vendor's boxes sat below everything as a separate block. It read as
    "under the vendor" rather than as the same kind of row, which is what it
    is: the same thing, for the same reason, bought instead of dropped.

    So the boxes now sit beside the name, in a two-column grid so a stock too
    long for the line wraps UNDER itself (two on the first line, the rest
    beneath) instead of forcing the list to scroll sideways. The grid splits
    the space evenly, which is what a boss row's stretch factors do for two
    weapons, so the wrap point is true by construction rather than by a width
    constant (a flow wrapped on each chip's own sizeHint and read as one box
    per line).

    A box is not a boss drop - Mira sells hers for Nightblood and no boss
    hands them out - so this is a line of her own below the bosses rather than
    chips on their rows. None when there is no vendor stock, so families
    without one carry no empty line.
    """
    if not boxes or not vendor:
        return None
    row = QtWidgets.QFrame()
    row.setObjectName("Card")
    h = QtWidgets.QHBoxLayout(row)
    h.setContentsMargins(8, 6, 8, 6)
    h.setSpacing(10)
    h.addWidget(_vendor_sprite(source_id, vendor))
    nm = QtWidgets.QLabel(vendor)
    nm.setStyleSheet(
        f"color:{theme.GOLD};font-weight:700;font-size:13px;"
        "background:transparent;border:0;outline:0;")
    nm.setWordWrap(True)
    nm.setFocusPolicy(QtCore.Qt.NoFocus)
    nm.setTextInteractionFlags(QtCore.Qt.NoTextInteraction)
    # the boss row's own factors: name 2, each drop 3. Two boxes at 3 each is
    # the 6 below, so the boxes take three times the name's width.
    h.addWidget(nm, 2)

    # The boxes go in a TWO-COLUMN grid, not a flow. A flow wraps on each chip's
    # own sizeHint — 266-290px, whatever the name wants — so in the ~396px left
    # beside the name it put one box per line and the vendor read as a tall
    # single column. A grid splits that space evenly instead, which is what a
    # boss row's two weapons get from the HBox's stretch factors, and it makes
    # "two on the first line, the third under the FIRST" true by construction
    # rather than by a width constant that has to be re-derived whenever the
    # column moves.
    grid_host = QtWidgets.QWidget(row)
    gl = QtWidgets.QGridLayout(grid_host)
    gl.setContentsMargins(0, 0, 0, 0)
    gl.setHorizontalSpacing(6)
    gl.setVerticalSpacing(4)
    for i, c in enumerate(boxes):
        gl.addWidget(cache_boss_chip(page, c), i // 2, i % 2)
    gl.setColumnStretch(0, 1)
    gl.setColumnStretch(1, 1)
    h.addWidget(grid_host, 6)      # two chips at the boss row's stretch 3
    return row


def _vendor_sprite(source_id: str, vendor: str) -> QtWidgets.QLabel:
    """The vendor's own unit sprite, in the gold-outlined treatment a boss row
    gets. Falls back to the shop glyph when no unit can be named for them:
    Mira resolves (DemonHuntMira_* -> DemonHunterMira), and Shiro James
    resolves through his display name — his recorded sales stamp no unit id,
    and his scanner key `Glory_Merchant` matches no unit, but the shared
    chain maps "Shiro James, the Medal of Glory vendor" to his own
    MedalogGloryTrader sprite, the same face the Merchants tab shows.
    """
    sprite = ""
    sid = source_id or ""
    for prefix, unit in _NPC_SPRITES:
        if sid.startswith(prefix):
            sprite = _codex_unit_sprite(unit)
            break
    if not sprite and vendor:
        for prefix, unit in _NPC_SPRITES:
            stem = prefix.split("NPC")[0]
            if stem and stem.lower() in vendor.lower():
                sprite = _codex_unit_sprite(unit)
                break
    if not sprite and vendor:
        # a recorded sale stamps no unit id at all (Shiro's hero
        # caches), so the name is the only handle left — the same
        # display-name chain the Merchants view resolves through
        sprite = vendor_sprite(vendor)
    if sprite and icons.has_icon("Units", sprite):
        lab = QtWidgets.QLabel()
        lab.setPixmap(icons.outlined("Units", sprite, 38, theme.GOLD,
                                     border=2, trim=True))
        lab.setFixedSize(38, 38)
        lab.setFocusPolicy(QtCore.Qt.NoFocus)
        lab.setTextInteractionFlags(QtCore.Qt.NoTextInteraction)
        return lab
    emo = QtWidgets.QLabel("🛒")
    emo.setStyleSheet("font-size:18px;background:transparent;")
    emo.setFocusPolicy(QtCore.Qt.NoFocus)
    return emo
