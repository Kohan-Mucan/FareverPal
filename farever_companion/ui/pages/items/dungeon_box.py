"""Pane + row widgets for the gear tab's DUNGEONS view.

Split out of `dungeon_view.py`, which sits on the 840-line budget the UI
test enforces. `show_box` is the pane a click on a cache opens; `weapon_chip`
is the compact weapon tile a boss row carries. Both reach back into
`dungeon_view` for the shared row builders, imported INSIDE the functions so
`dungeon_view` can import this module at module level without a cycle.
"""
from __future__ import annotations

from PySide6 import QtCore, QtWidgets

from ... import theme
from ....data import icons
from ....data import items as idata


def show_box(page, cache_id: str) -> None:
    """The right-pane card for one CACHE: what is inside the box, in the same
    locked tile grid the faction view uses, under a header naming the box and
    how its roll works (a capped count, or that it scales to you).

    This is what a click on a box in the vendor line opens — the same
    'pick one thing, the pane narrows to it' gesture a boss pick makes, so
    the box does not send you off to the item page to discover its contents.
    Clicking the same box again (or the faction row) releases it.

    Reads the GENERIC container facts (`container_spec`/`container_contents`),
    the SAME source the item page's Contains section renders from: the hand
    table supplies the Heroic/Rift boxes (with their `via` prose for a
    recorded seller), and any box the hand table does not claim falls back to
    the spec derived from its compiler-baked `gain_item`. One source, so the
    two box surfaces cannot disagree about a box's contents.
    """
    from .dungeon_view import _epic_header, _filter_stamp, _locked_grid, _usable
    box = idata.container_contents().get(cache_id) or ()
    if not box:
        return
    spec = idata.container_spec().get(cache_id) or {}
    # the LISTED name, so a box opened from a list keeps the name it was
    # clicked under — the two "Chaotic Gear Cache" boxes must not converge
    name = idata.cache_display_name(cache_id)
    rar = spec.get("rarity") or ""
    # the box's OWN tier is `rar`, but what an open hands out is the span: the
    # Hero Weapon Cache clamps its roll at Epic and can climb to Legendary, so
    # printing the container's rarity alone reads as if every piece were Epic
    floor = spec.get("rarity_min") or rar
    ceiling = spec.get("rarity_max") or rar
    span = f"{floor}+" if ceiling and floor and ceiling != floor else rar
    un = idata.cache_is_unreleased(cache_id)
    page._dg_shown_faction = ""
    page._dg_rendered_boss = ""
    page._dg_rendered_box = cache_id
    page._dg_rendered_class = _filter_stamp(page)
    page._dg_pane_state = "box"
    page._items_shown_id = None
    lay = page._items_detail_lay
    page._items_clear_layout(lay)

    head = QtWidgets.QHBoxLayout()
    head.setSpacing(10)
    emo = QtWidgets.QLabel("🎁")
    emo.setStyleSheet("font-size:32px;background:transparent;")
    head.addWidget(emo)
    info = QtWidgets.QVBoxLayout()
    title = QtWidgets.QLabel(name)
    title.setStyleSheet(
        f"color:{theme.rarity_color(rar) if rar else theme.TEXT};"
        "font-weight:700;font-size:16px;background:transparent;")
    info.addWidget(title)
    bits = [b for b in (span, f"{len(box)} pieces") if b]
    if spec.get("player_scale"):
        bits.append(f"at YOUR level, up to {spec.get('level_max')}")
    elif spec.get("level_max"):
        bits.append(f"gear level {spec['level_max']}")
    if un:
        bits.append("UNRELEASED")
    sub = QtWidgets.QLabel(" · ".join(bits))
    sub.setStyleSheet(
        f"color:{theme.DIM};font-size:11.5px;background:transparent;")
    info.addWidget(sub)
    head.addLayout(info, 1)
    lay.addLayout(head)

    # who hands this box over, in prose: the hand table's `via` where it has
    # one, else the seller the scan actually recorded. Only drawn when the
    # box HAS a seller — a box the hand table never claimed has neither, and
    # the generic fallback must not invent a source for it.
    if not un and (spec.get("via") or idata.cache_vendor(cache_id)):
        vlab = QtWidgets.QLabel(f"via {idata.cache_via(cache_id)}")
        vlab.setWordWrap(True)
        vlab.setMaximumWidth(620)
        vlab.setStyleSheet(
            f"color:{theme.DIM};font-size:11px;background:transparent;")
        lay.addWidget(vlab)

    if un:
        note = QtWidgets.QLabel(
            "UNRELEASED — the box is fully authored, but no boss, vendor, "
            "chest or crate in the game data hands it out.")
        note.setWordWrap(True)
        note.setMaximumWidth(620)
        note.setStyleSheet(
            f"color:{theme.DIM};font-size:12px;background:transparent;")
        lay.addWidget(note)

    pieces = [r["item"] for r in box
              if _usable(page, r["item"], ignore_rarity=True)]
    if pieces:
        lay.addWidget(_epic_header(
            f"{span or 'GEAR'} · {len(pieces)} pieces"
            + (" · UNRELEASED" if un else ""),
            "what one open can hand you"))
        lay.addWidget(_locked_grid(page, pieces))
    page._items_refresh_scroll()


# ----------------------------------------------------------------------


def weapon_chip(page, iid: str) -> QtWidgets.QFrame:
    """Compact weapon tile for the boss row: icon + rarity-colored name,
    click opens the item detail (accepting the event keeps it from also
    triggering the row's select)."""
    it = idata.item(iid) or {}
    name = it.get("name") or iid
    ncol = theme.rarity_color(idata.item_display_rarity(iid))
    frame = QtWidgets.QFrame()
    frame.setObjectName("Card")
    h = QtWidgets.QHBoxLayout(frame)
    h.setContentsMargins(6, 4, 6, 4)
    h.setSpacing(6)
    icon = QtWidgets.QLabel()
    # the icon's fill is the item's own tier, like its name and like every
    # other item tile in the app (the browse grid, the Collection cards)
    icon.setPixmap(icons.item_tile(iid, name, 32, ncol))
    h.addWidget(icon)
    nm = QtWidgets.QLabel(name)
    nm.setStyleSheet(
        f"color:{ncol};font-weight:600;font-size:11.5px;"
        "background:transparent;")
    nm.setWordWrap(True)
    h.addWidget(nm, 1)
    frame.setCursor(QtCore.Qt.PointingHandCursor)

    def _open(ev, _p=page, _i=iid):
        _p._items_show_id(_i)
        ev.accept()
    frame.mouseReleaseEvent = _open
    return frame
