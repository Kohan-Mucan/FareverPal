"""The gear page's 🏰 DUNGEONS right-pane renderers — split out of
`dungeon_view.py`, which sits on the 840-line budget the UI test enforces
(the `dungeon_overlay.py` → `dungeon_render.py` split is the same shape).

`dungeon_view.py` keeps the left column (the `DungeonView` scroll area:
faction chips + boss rows) and RE-EXPORTS every name here, so all existing
callers — `dungeon_box.py` and `filters.py`, which import inside their
functions to avoid the import cycle, `page.py`, and the tests — are
unchanged.
"""
from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from ... import theme
from ....data import icons
from ....data import items as idata
from .support import FACTION_EMOJI as _FACTION_EMOJI, faction_label  # noqa: F401
from .support import rarity_passes, slot_label
from .cache_box import vendor_line

__all__ = [
    "show_faction", "_pool_block", "_dungeon_grid", "_picked_boss",
    "_locked_grid", "_dungeon_jump_button", "_epic_header", "_filter_stamp",
    "_usable", "_section_label", "_item_tile",
]


def show_faction(page, g: dict, boss_id: str = "") -> None:
    """The right-pane card for one faction. With no boss picked it is the
    whole family: the shared armor/trinket set in a LOCKED 2-column grid
    (fixed tile widths — the old flow layout made a ragged mess), then one
    EPIC heroic block per boss. With a boss picked (`boss_id`) it is
    that boss's own drops only: its signature weapons on top (the same
    tiles the left column carries) plus its heroic Epic set — the shared
    pool lives only in the faction view. The shared pool is family-wide
    (every dungeon of the family rolls the same tables). Both tiers always
    show — the Rarity buttons don't gate this tab, only the class/Only
    chips do. Signature weapons also live in the LEFT column under their
    boss. Remembers itself so mouse-BACK from an item detail returns
    here."""
    boss = _picked_boss(g, boss_id)
    page._dg_last_group = g
    page._dg_last_boss = (g, boss) if boss else None
    page._dg_shown_faction = g["faction"]
    page._dg_rendered_boss = boss_id if boss else ""
    page._dg_rendered_box = ""
    page._dg_rendered_class = _filter_stamp(page)
    page._dg_pane_state = "boss"
    # the faction card is not an item card: forget the last shown item, or
    # clicking its tile again hits _items_show's same-item guard and sticks
    page._items_shown_id = None
    fac = g["faction"]
    lay = page._items_detail_lay
    page._items_clear_layout(lay)

    head = QtWidgets.QHBoxLayout()
    head.setSpacing(10)
    emo = QtWidgets.QLabel(_FACTION_EMOJI.get(fac, "🏰"))
    emo.setStyleSheet("font-size:32px;background:transparent;")
    head.addWidget(emo)
    info = QtWidgets.QVBoxLayout()
    info.setSpacing(2)
    nm = QtWidgets.QLabel(f"{faction_label(fac)} Dungeons")
    nm.setStyleSheet(
        f"color:{theme.GOLD};font-weight:800;font-size:17px;"
        "background:transparent;")
    info.addWidget(nm)
    n_bosses = len(g["bosses"])
    n_epic_bosses = sum(1 for b in g["bosses"] if b.get("epic"))
    # Only what a boss in THIS family's dungeons actually drops counts here.
    # A Heroic CACHE is not a dungeon and has no drop location, so the page
    # makes no claim about it; those pieces are served by the gear list.
    dg = QtWidgets.QLabel(
        f"{n_bosses} dungeons · {boss['boss']} only · click it again for all"
        if boss else
        f"{n_bosses} dungeons · {n_epic_bosses} with Epic drops")
    dg.setObjectName("Mono")
    dg.setStyleSheet(
        f'color:{theme.MUTED};font-family:"{theme.MONO_FONT}", monospace;'
        "font-size:12px;background:transparent;")
    info.addWidget(dg)
    head.addLayout(info, 1)
    lay.addLayout(head)

    shared = [iid for iid in g["shared"]
              if _usable(page, iid, ignore_rarity=True)]
    dv = getattr(page, "_items_dungeon_view", None)
    # Where the pool really comes from — read from its own drop rows, never
    # assumed. Four of the five families' Rares roll their bosses'/chests',
    # so they ARE a shared set and the dungeon list is where they come from:
    # one block, header leading straight into the list. Nightling's 25 Rares
    # are BOUGHT from Mira and its two bosses carry no armour at all, so
    # calling that a "SHARED SET" with the dungeons listed under it welds
    # two unrelated things together and implies the bosses drop them. They
    # get their own blocks instead: a vendor pool that stands alone, and the
    # dungeons under a head of their own.
    ss = idata.shared_source(g)
    vendor = ss.get("kind") == "vendor"
    pool_note = ""
    if vendor:
        price = " · ".join(
            f"{amount:,} {idata.currency_label(kind)}"
            for kind, amount in ss.get("cost") or ())
        # NAME THE BOXES. The header used to say "VENDOR GEAR · 25 pieces ·
        # sold by Mira" and stop there, so the pane claimed 25 pieces from
        # somewhere that never said which boxes they come out of — and the
        # boxes are the whole point of buying them, since each rolls its own
        # table at your level. The title keeps who and how much; the note
        # under it says which boxes, which is the part that was missing.
        boxes = idata.faction_caches(fac)
        names = [b.get("name") or b.get("cache") or "" for b in boxes]
        names = [n for n in names if n]
        pool_title = (
            f"{_FACTION_EMOJI.get(fac, '🏰')} VENDOR GEAR · {len(shared)} "
            f"pieces in {len(boxes)} boxes"
            f" · {ss.get('source', 'a vendor')}"
            + (f" · {price} each" if price else ""))
        pool_note = " · ".join(names)
    else:
        pool_title = (
            f"{_FACTION_EMOJI.get(fac, '🏰')} SHARED SET · {len(shared)} pieces"
            + (" · drops in:" if g["bosses"] else ""))

    pool_w, pool_v = _pool_block(page, fac, pool_title, note=pool_note)
    if not vendor:
        pool_v.addWidget(_dungeon_grid(page, g, fac, dv))
    lay.addWidget(pool_w)
    if vendor and g["bosses"]:
        # No "DUNGEONS" header here: this family's dungeon content IS the
        # vendor's shop, so Mira gets a boss-style row (sprite, gold name,
        # her boxes as clickable chips) above the bare dungeon grid.
        mira = vendor_line(page, idata.faction_caches(fac),
                           ss.get("source", ""), ss.get("source_id", ""))
        if mira is not None:
            lay.addWidget(mira)
        lay.addWidget(_dungeon_grid(page, g, fac, dv))

    # the picked boss's signature weapons, mirrored from its left-column row
    # so the pane reads as that boss's whole drop view (its weapons + its
    # heroic Epic below — the shared tile grid stays in the faction
    # view). Gated like every other tile, so an empty pick under a class
    # filter draws no block.
    if boss:
        weapons = [w for w in (boss.get("weapons") or ())
                   if _usable(page, w, ignore_rarity=True)]
        if weapons:
            lay.addWidget(_epic_header(
                f"⚔ {boss['boss']} · SIGNATURE WEAPONS · {len(weapons)}",
                boss["dungeon"] if boss.get("dungeon")
                else f"drops from {boss['boss']}"))
            lay.addWidget(_locked_grid(page, weapons))

    # the shared tile grid belongs to the faction view only: a boss pick
    # shows that boss's own drops, so the family grid stays out of the way
    # until the "{Faction} Dungeons" row brings the whole family back.
    if not boss:
        lay.addWidget(_locked_grid(page, shared))

    # the Epic sets: heroic only drops epic gear — each boss's table holds
    # nothing but its Epic pieces, one guaranteed per heroic kill (the
    # scanned per-row 1% is the marginal rate across all difficulties, so
    # the pane states the live mechanic instead of the scan average).
    # Genuinely boss-specific: with no boss picked the pane carries one
    # block per boss (Bee / Crimson have none: no scanned `_HM` table), which
    # is what makes the faction's whole Epic line visible at once; picking a
    # boss in the left list drops the others. Like the rest of this tab the
    # blocks ignore the Rarity tier (both tiers always show) but keep the
    # class/Only/rating gates. The boss's Infusion Pattern rides inside its
    # Epic block; a boss with no Epic table keeps a lone pattern block.
    for b in ([boss] if boss else g["bosses"]):
        epic = [i for i in (b.get("epic") or ())
                if _usable(page, i, ignore_rarity=True)]
        inf = b.get("infusion") or ""
        inf_ok = bool(inf) and _usable(page, inf, ignore_rarity=True)
        if not epic and not inf_ok:
            continue
        if epic:
            if inf_ok:
                inf_name = (idata.item(inf) or {}).get("name") or inf
                lay.addWidget(_epic_header(
                    f"⚔ {b['boss']} · EPIC · {len(epic)} pieces · heroic mode",
                    hint=f"⚗ {b['boss']} · Infusion Pattern · {inf_name} "
                         f"· heroic mode"))
                lay.addWidget(_locked_grid(page, epic + [inf]))
            else:
                lay.addWidget(_epic_header(
                    f"⚔ {b['boss']} · EPIC · {len(epic)} pieces · heroic mode"))
                lay.addWidget(_locked_grid(page, epic))
        else:
            lay.addWidget(_epic_header(
                f"⚗ {b['boss']} · Infusion Pattern · heroic mode"))
            lay.addWidget(_locked_grid(page, [inf]))

    # NOTE: pieces with no dungeon of their own are NOT drawn here. The
    # Heroic Caches (the Nightling's whole set, and the other four families'
    # six apiece) and the handful of pieces a boss of ANOTHER family rolls
    # used to get blocks on this page, which filed gear against a location
    # that does not exist — a cache is not a dungeon, and Golcano's dungeon
    # is not a Crimson one. They are ordinary `epic_unknown` now, served by
    # the gear list and the Collection Manager, as before.

    # the pieces whose drop rows the data doesn't carry yet (every Bee /
    # Crimson piece, half of Kobold / Manfish) are NOT drawn here: they are
    # the same named set as the tiles above, so a second grid of them read as
    # a mess. The Collection view still lists every piece.
    lay.addStretch(1)
    page._items_refresh_scroll()


def _pool_block(page, fac: str, title: str, clickable: bool = True,
                note: str = ""):
    """One labelled section of the pane: the small-caps label over a body the
    caller fills. Returns (widget, layout).

    `note` is an optional quieter line UNDER the label, for the detail the
    title has no room for — the vendor section names its boxes there rather
    than turning the title into a paragraph.

    `clickable` makes the label jump back to the whole family, which is how
    the boss view reaches the shared pool at all (its tile grid is hidden
    there) — and a no-op in the faction view, where it is already shown.
    Fully qualified rules only: mixing bare declarations with a :hover rule
    does not parse on a shown widget.
    """
    w = QtWidgets.QWidget()
    v = QtWidgets.QVBoxLayout(w)
    v.setContentsMargins(0, 0, 0, 0)
    v.setSpacing(2)
    lbl = _section_label(title)
    if clickable:
        lbl.setCursor(QtCore.Qt.PointingHandCursor)
        lbl.setStyleSheet(
            f"QLabel{{color:{theme.MUTED};font-size:11.5px;font-weight:700;"
            "letter-spacing:1.5px;background:transparent;}"
            f"QLabel:hover{{color:{theme.TEXT};}}")
        lbl.mouseReleaseEvent = lambda ev, _p=page, f=fac: (
            _p._items_dungeon_view.select(f, ""), ev.accept())
    v.addWidget(lbl)
    if note:
        nl = QtWidgets.QLabel(note)
        nl.setObjectName("Mono")
        nl.setWordWrap(True)
        nl.setStyleSheet(
            f'color:{theme.MUTED};font-family:"{theme.MONO_FONT}", monospace;'
            "font-size:11px;background:transparent;")
        v.addWidget(nl)
    return w, v


def _dungeon_grid(page, g: dict, fac: str, dv) -> QtWidgets.QWidget:
    """One icon + name button per dungeon of the family, in 2 columns (a
    stacked line per dungeon was a wall). Every button picks its boss — the
    same single logic as the left-column rows — so the pane just shows that
    boss's drops.
    """
    w = QtWidgets.QWidget()
    dgrid = QtWidgets.QGridLayout(w)
    dgrid.setContentsMargins(0, 0, 0, 0)
    dgrid.setHorizontalSpacing(8)
    dgrid.setVerticalSpacing(1)
    col = 0
    for b in g["bosses"]:
        if not b.get("dungeon"):
            continue
        btn = _dungeon_jump_button(b)
        dgrid.addWidget(btn, col // 2, col % 2)
        if dv is not None:
            btn.clicked.connect(
                lambda _=False, d=dv, f=fac, i=b["boss_id"]: d.toggle(f, i))
        col += 1
    dgrid.setColumnStretch(0, 1)
    dgrid.setColumnStretch(1, 1)
    return w


def _picked_boss(g: dict, boss_id: str = "") -> dict | None:
    """The boss the pane is narrowed to, or None when it speaks for the whole
    faction (no row picked) — a faction pick starts that way, so no boss is
    singled out before the user asks for one."""
    if not boss_id:
        return None
    for b in g.get("bosses") or ():
        if b["boss_id"] == boss_id:
            return b
    return None


def _locked_grid(page, ids) -> QtWidgets.QWidget:
    """The pane's tile grid: every cell the same width, tiles stretch to
    fill it — no flow-wrap raggedness."""
    host = QtWidgets.QWidget()
    gl = QtWidgets.QGridLayout(host)
    gl.setContentsMargins(0, 0, 0, 0)
    gl.setHorizontalSpacing(8)
    gl.setVerticalSpacing(8)
    cols = 2
    for i, iid in enumerate(ids):
        tile = _item_tile(page, iid, wt=slot_label(
            (idata.item(iid) or {}).get("type") or ""))
        tile.setMinimumHeight(64)
        gl.addWidget(tile, i // cols, i % cols)
    for c in range(cols):
        gl.setColumnStretch(c, 1)
    return host


def _dungeon_jump_button(b: dict) -> QtWidgets.QPushButton:
    """One dungeon line: the boss's icon + the dungeon name as a pick link.
    Deliberately a plain text row, not a button block — no fill, no border,
    just brighter text on hover. Returned unwired — show_faction connects it
    to the boss pick."""
    btn = QtWidgets.QPushButton(b["dungeon"])
    btn.setIcon(QtGui.QIcon(icons.outlined(
        "Units", b["boss_id"], 20, theme.GOLD, border=2, trim=True)))
    btn.setIconSize(QtCore.QSize(20, 20))
    btn.setFlat(True)
    btn.setCursor(QtCore.Qt.PointingHandCursor)
    btn.setStyleSheet(
        f'QPushButton{{color:{theme.MUTED};font-family:"{theme.MONO_FONT}", '
        '"Consolas", monospace;font-size:11.5px;background:transparent;'
        'border:0;padding:1px 0;text-align:left;}'
        f"QPushButton:hover{{color:{theme.TEXT};}}")
    return btn


def _epic_header(title: str, hint: str | None = None) -> QtWidgets.QWidget:
    """Section label for a dungeon drop block."""
    box = QtWidgets.QWidget()
    v = QtWidgets.QVBoxLayout(box)
    v.setContentsMargins(0, 4, 0, 0)
    v.setSpacing(1)
    v.addWidget(_section_label(title))
    if hint:
        sub = QtWidgets.QLabel(hint)
        sub.setWordWrap(True)
        sub.setStyleSheet(
            f"color:{theme.DIM};font-size:11px;background:transparent;")
        v.addWidget(sub)
    return box


def _filter_stamp(page) -> tuple:
    """The filter state a rendered faction card was built with — the card
    re-renders when it changes (class, Only, the rating chip, and the
    Rarity buttons)."""
    return (getattr(page, "_items_class", ""),
            bool(getattr(page, "_items_only", False)),
            frozenset(getattr(page, "_items_rarity_filter", ()) or ()),
            getattr(page, "_items_rating", ""))


def _usable(page, iid: str, ignore_rarity: bool = False) -> bool:
    """Gate for the faction card — same rules as the browse list: the
    Rarity buttons decide which tiers drop, a picked rating keeps only gear
    rolling it, then a piece with a classes field must include the picked
    class; universal gear (rings/necks — no field) always passes; with the
    Only chip active, multi-class pieces drop out too. `ignore_rarity`
    skips only the tier gate, never the rating/class/Only gates — the whole
    Dungeons tab renders with it set, because that tab shows both tiers at
    once and the Rarity buttons don't apply to it."""
    if not ignore_rarity \
            and not rarity_passes(iid, getattr(page, "_items_rarity_filter", ())):
        return False
    rating = getattr(page, "_items_rating", "")
    if rating and rating not in idata.gear_ratings(iid):
        return False
    cls_pick = getattr(page, "_items_class", "")
    if not cls_pick:
        return True
    classes = (idata.item(iid) or {}).get("classes") or []
    if not classes:
        return True
    if cls_pick not in classes:
        return False
    if getattr(page, "_items_only", False) and len(classes) > 1:
        return False
    return True


def _section_label(text: str) -> QtWidgets.QLabel:
    lbl = QtWidgets.QLabel(text)
    lbl.setStyleSheet(
        f"color:{theme.MUTED};font-size:11.5px;font-weight:700;letter-spacing:1.5px;"
        "background:transparent;")
    return lbl


def _item_tile(page, iid: str, wt: str) -> QtWidgets.QFrame:
    """Compact clickable tile: icon + name both in the item's TIER colour
    (the class colour is not the piece's own — a faction name has no class,
    so it painted every tile MUTED grey); right side has the slot type over
    the usable-class tags (class colors like the Farm rows). The class
    filter hides pieces upstream (`_usable`)."""
    it = idata.item(iid) or {}
    name = it.get("name") or iid
    is_pattern = (it.get("type") or "") == "InfusionPattern"
    if is_pattern:
        wt = ""
    skill = ""
    if is_pattern:
        for p in idata.heroic_infusions():
            if p.get("item") == iid:
                skill = p.get("skill") or ""
                break
    ncol = theme.rarity_color(idata.item_display_rarity(iid))
    classes = it.get("classes") or []
    frame = QtWidgets.QFrame()
    h = QtWidgets.QHBoxLayout(frame)
    frame.setObjectName("Card")
    h.setContentsMargins(8, 6, 8, 6)
    h.setSpacing(8)
    icon = QtWidgets.QLabel()
    icon.setPixmap(icons.item_tile(iid, name, 38, ncol))
    h.addWidget(icon, 0, QtCore.Qt.AlignTop)
    col = QtWidgets.QVBoxLayout()
    col.setSpacing(2)
    col.setAlignment(QtCore.Qt.AlignTop)
    nm = QtWidgets.QLabel(name)
    nm.setStyleSheet(
        f"color:{ncol};font-weight:700;font-size:12.5px;background:transparent;")
    nm.setWordWrap(True)
    col.addWidget(nm, 0, QtCore.Qt.AlignTop)
    if is_pattern:
        if skill:
            sk = QtWidgets.QLabel(skill)
            sk.setStyleSheet(
                f"color:{theme.GOLD};font-size:11px;background:transparent;")
            sk.setWordWrap(True)
            col.addWidget(sk, 0, QtCore.Qt.AlignTop)
        tp = QtWidgets.QLabel("Infusion Pattern")
        tp.setStyleSheet(
            f"color:{theme.ACCENT};font-size:11px;background:transparent;")
        tp.setWordWrap(True)
        col.addWidget(tp, 0, QtCore.Qt.AlignTop)
    h.addLayout(col, 1)
    # right side: the slot type on top, the usable classes stacked under it —
    # each tag in its class color, same treatment as the Farm rows. Patterns
    # spell type + skill under their name instead, so no side tag here.
    if wt or classes:
        side = QtWidgets.QVBoxLayout()
        side.setSpacing(3)
        if wt:
            tp = QtWidgets.QLabel(wt.upper())
            tp.setStyleSheet(
                f'color:{theme.ACCENT};font-family:"{theme.MONO_FONT}", '
                "monospace;font-size:9.5px;letter-spacing:1px;"
                "background:transparent;")
            tp.setAlignment(QtCore.Qt.AlignHCenter)
            side.addWidget(tp)
        for c in classes:
            c_col = theme.class_color(c)
            tag = QtWidgets.QLabel(idata.class_label(c))
            tag.setStyleSheet(
                f"color:{c_col};background:{theme.with_alpha(c_col, 22)};"
                f"border:1px solid {theme.with_alpha(c_col, 80)};"
                "border-radius:3px;padding:1px 6px;font-size:9px;"
                "font-weight:700;background-clip:padding;")
            tag.setAlignment(QtCore.Qt.AlignHCenter)
            side.addWidget(tag)
        side.addStretch(1)
        h.addLayout(side, 0)
    frame.setCursor(QtCore.Qt.PointingHandCursor)

    def _open(ev, _p=page, _i=iid):
        _p._items_show_id(_i)
        ev.accept()
    frame.mouseReleaseEvent = _open
    frame.setMaximumHeight(74)
    return frame
