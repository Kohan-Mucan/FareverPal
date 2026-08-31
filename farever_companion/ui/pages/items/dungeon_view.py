"""The gear page's 🏰 DUNGEONS tab — weapons left, shared gear right.

Left column: faction chips (one active at a time, Bee default), then each
boss's row followed by its signature weapon tiles. RIGHT pane: the
faction's shared armor/trinket set in a locked 2-column grid — every
dungeon of a family drops the same pool, so one card covers them all.
Clicking a tile opens the item's detail card.
"""
from __future__ import annotations

from PySide6 import QtCore, QtWidgets

from ... import theme
from ... import components as C
from ....data import icons
from ....data import items as idata
from .farm import FarmMixin
from .support import slot_label

_FACTION_EMOJI = {"Bee": "🐝", "Kobold": "⛏", "Manfish": "🐟",
                  "Crimson": "🍷"}


def _stats_line(iid: str) -> str:
    """The piece's base stats at max level ('Armor 208 · Critical 11')."""
    return " · ".join(FarmMixin._farm_stats_parts(idata.item(iid) or {}))


class DungeonView(QtWidgets.QScrollArea):
    """Left column of the Dungeons tab: faction chips + boss list."""

    def __init__(self, page: QtWidgets.QWidget):
        super().__init__()
        self._page = page
        self.setWidgetResizable(True)
        self.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)

        body = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(body)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)

        groups = idata.dungeon_drop_groups()
        self._groups = {g["faction"]: g for g in groups}
        self._rows: dict[str, QtWidgets.QFrame] = {}
        self._sel: tuple[str, str] | None = None
        self._fac = ""

        # faction chips — one active at a time
        chips = QtWidgets.QWidget(body)
        cl = QtWidgets.QHBoxLayout(chips)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(6)
        self._chips: dict[str, C.Chip] = {}
        first = groups[0]["faction"] if groups else ""
        for g in groups:
            fac = g["faction"]
            chip = C.FilterChip(
                f"{_FACTION_EMOJI.get(fac, '🏰')} {fac}",
                checked=(fac == first),
                color=theme.class_color(fac) if fac in _FACTION_EMOJI
                else theme.ACCENT,
                parent=chips)
            chip.toggled.connect(lambda on, f=fac: self._pick(faction=f))
            cl.addWidget(chip)
            self._chips[fac] = chip
        cl.addStretch(1)
        v.addWidget(chips)

        # the boss list for the active faction
        self._list_host = QtWidgets.QWidget(body)
        self._list_lay = QtWidgets.QVBoxLayout(self._list_host)
        self._list_lay.setContentsMargins(0, 0, 0, 0)
        self._list_lay.setSpacing(6)
        v.addWidget(self._list_host)
        v.addStretch(1)
        self.setWidget(body)

        if first:
            self._fac = first
            self._fill_list(first)

    # ------------------------------------------------------------------
    def _pick(self, faction: str = "") -> None:
        """Chip click: switch the boss list to that faction. Single-select —
        `self._fac` is the truth, so the sweep's programmatic unchecks are
        ignored instead of re-triggering a pick ping-pong between factions."""
        chip = self._chips.get(faction)
        if not faction or chip is None:
            return
        if faction == self._fac:
            # the active chip can't be deselected (clicks and programmatic
            # toggles both land here) — snap it back on
            if not chip.isChecked():
                chip.setChecked(True)
            return
        if not chip.isChecked():
            return               # unchecking a non-selected chip
        self._fac = faction
        for f, c in self._chips.items():
            if f != faction and c.isChecked():
                c.setChecked(False)
        g = self._groups.get(faction)
        if not g:
            return
        self._fill_list(faction)
        if g["bosses"]:
            self.select(g["faction"], g["bosses"][0]["boss_id"])

    # ------------------------------------------------------------------
    def ensure_card(self) -> None:
        """Make sure the right pane shows the active faction's gear card —
        called when the Dungeons tab is first shown / returned to."""
        fac = getattr(self, "_fac", "") or next(iter(self._groups), "")
        g = self._groups.get(fac)
        if not g or not g["bosses"]:
            return
        bid = (self._sel[1] if getattr(self, "_sel", None)
               and self._sel[0] == fac else g["bosses"][0]["boss_id"])
        self.select(fac, bid)

    def _fill_list(self, faction: str) -> None:
        """Rebuild the faction's rows: one row per boss — boss name and its
        signature weapons side by side on the same line (class-filtered)."""
        self._rows.clear()          # drop refs to about-to-be-deleted rows
        while self._list_lay.count():
            it = self._list_lay.takeAt(0)
            if w := it.widget():
                w.deleteLater()
        g = self._groups[faction]
        for b in g["bosses"]:
            row = self._boss_row(g, b)
            self._list_lay.addWidget(row)
            self._rows[(faction, b["boss_id"])] = row

    def _boss_row(self, g: dict, b: dict) -> QtWidgets.QFrame:
        fac = g["faction"]
        row = QtWidgets.QFrame()
        row.setObjectName("Card")
        h = QtWidgets.QHBoxLayout(row)
        h.setContentsMargins(8, 6, 8, 6)
        h.setSpacing(10)
        sprite = QtWidgets.QLabel()
        sprite.setPixmap(icons.outlined("Units", b["boss_id"], 38,
                                        theme.GOLD, border=2, trim=True))
        h.addWidget(sprite)
        nm = QtWidgets.QLabel(b["boss"])
        nm.setStyleSheet(
            f"color:{theme.GOLD};font-weight:700;font-size:13px;"
            "background:transparent;")
        nm.setWordWrap(True)
        h.addWidget(nm, 2)
        for wid in b["weapons"]:
            if not _usable(self._page, wid):
                continue
            h.addWidget(_weapon_chip(self._page, wid), 3)
        row.setCursor(QtCore.Qt.PointingHandCursor)
        key = (fac, b["boss_id"])
        row.mouseReleaseEvent = lambda ev, k=key: (self.select(*k),
                                                   ev.accept())
        return row

    # ------------------------------------------------------------------
    def select(self, faction: str, boss_id: str) -> None:
        """Highlight the picked boss row and make sure its faction's gear
        card is showing in the right pane."""
        self._sel = (faction, boss_id)
        g = self._groups.get(faction)
        if g is None:
            return
        # one card per family — rebuild when the faction changes, the pane
        # is showing something else (an item detail cleared the card), or
        # the class/Only filter moved since this card was rendered
        stamp = (getattr(self._page, "_items_class", ""),
                 bool(getattr(self._page, "_items_only", False)))
        if getattr(self._page, "_dg_shown_faction", "") != faction \
                or getattr(self._page, "_dg_pane_state", "") != "boss" \
                or getattr(self._page, "_dg_rendered_class", None) != stamp:
            show_faction(self._page, g)


# ----------------------------------------------------------------------
def show_faction(page, g: dict) -> None:
    """The right-pane card for one faction: the shared armor/trinket set in
    a LOCKED 2-column grid (fixed tile widths — the old flow layout made a
    ragged mess). Signature weapons live in the LEFT column under their
    boss. Remembers itself so mouse-BACK from an item detail returns here."""
    page._dg_last_group = g
    first = g["bosses"][0] if g["bosses"] else None
    page._dg_last_boss = (g, first) if first else None
    page._dg_shown_faction = g["faction"]
    page._dg_rendered_class = (getattr(page, "_items_class", ""),
                               bool(getattr(page, "_items_only", False)))
    page._dg_pane_state = "boss"
    fac = g["faction"]
    color = theme.class_color(fac) if fac in _FACTION_EMOJI else theme.ACCENT
    lay = page._items_detail_lay
    page._items_clear_layout(lay)

    head = QtWidgets.QHBoxLayout()
    head.setSpacing(10)
    emo = QtWidgets.QLabel(_FACTION_EMOJI.get(fac, "🏰"))
    emo.setStyleSheet("font-size:32px;background:transparent;")
    head.addWidget(emo)
    info = QtWidgets.QVBoxLayout()
    info.setSpacing(2)
    nm = QtWidgets.QLabel(f"{fac} Dungeons")
    nm.setStyleSheet(
        f"color:{theme.GOLD};font-weight:800;font-size:17px;"
        "background:transparent;")
    info.addWidget(nm)
    dg = QtWidgets.QLabel(f"{len(g['bosses'])} dungeons")
    dg.setObjectName("Mono")
    dg.setStyleSheet(
        f'color:{theme.MUTED};font-family:"{theme.MONO_FONT}", monospace;'
        "font-size:12px;background:transparent;")
    info.addWidget(dg)
    head.addLayout(info, 1)
    lay.addLayout(head)

    shared = [iid for iid in g["shared"] if _usable(page, iid)]
    # the dungeon names stacked, one per line — one " · " row was hard to scan
    sec = QtWidgets.QWidget()
    sv = QtWidgets.QVBoxLayout(sec)
    sv.setContentsMargins(0, 0, 0, 0)
    sv.setSpacing(1)
    sv.addWidget(_section_label(
        f"{_FACTION_EMOJI.get(fac, '🏰')} SHARED SET · {len(shared)} pieces"
        + (" · drops in:" if g["bosses"] else "")))
    for b in g["bosses"]:
        if not b.get("dungeon"):
            continue
        dgl = QtWidgets.QLabel(f"    {b['dungeon']}")
        dgl.setObjectName("Mono")
        dgl.setStyleSheet(
            f'color:{theme.MUTED};font-family:"{theme.MONO_FONT}", monospace;'
            "font-size:11.5px;background:transparent;")
        sv.addWidget(dgl)
    lay.addWidget(sec)

    # locked columns: every cell the same width, tiles stretch to fill it —
    # no flow-wrap raggedness
    host = QtWidgets.QWidget()
    gl = QtWidgets.QGridLayout(host)
    gl.setContentsMargins(0, 0, 0, 0)
    gl.setHorizontalSpacing(8)
    gl.setVerticalSpacing(8)
    cols = 2
    for i, iid in enumerate(shared):
        tile = _item_tile(page, iid, wt=slot_label(
            (idata.item(iid) or {}).get("type") or ""), color=color)
        tile.setMinimumHeight(64)
        gl.addWidget(tile, i // cols, i % cols)
    for c in range(cols):
        gl.setColumnStretch(c, 1)
    lay.addWidget(host)
    lay.addStretch(1)
    page._items_refresh_scroll()


def _weapon_chip(page, iid: str) -> QtWidgets.QFrame:
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
    icon.setPixmap(icons.item_tile(iid, name, 32, theme.ACCENT))
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


def _usable(page, iid: str) -> bool:
    """Class-filter gate for the faction card — same rules as the browse
    list: a piece with a classes field must include the picked class;
    universal gear (rings/necks — no field) always passes; with the Only
    chip active, multi-class pieces drop out too."""
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


def _item_tile(page, iid: str, wt: str, color: str) -> QtWidgets.QFrame:
    """Compact clickable tile: icon + rarity-colored name; right side has
    the slot type over the usable-class tags (class colors like the Farm
    rows). The class filter hides pieces upstream (`_usable`)."""
    it = idata.item(iid) or {}
    name = it.get("name") or iid
    ncol = theme.rarity_color(idata.item_display_rarity(iid))
    classes = it.get("classes") or []
    frame = QtWidgets.QFrame()
    h = QtWidgets.QHBoxLayout(frame)
    frame.setObjectName("Card")
    h.setContentsMargins(8, 6, 8, 6)
    h.setSpacing(8)
    icon = QtWidgets.QLabel()
    icon.setPixmap(icons.item_tile(iid, name, 38, color))
    h.addWidget(icon)
    col = QtWidgets.QVBoxLayout()
    col.setSpacing(2)
    nm = QtWidgets.QLabel(name)
    nm.setStyleSheet(
        f"color:{ncol};font-weight:700;font-size:12.5px;background:transparent;")
    nm.setWordWrap(True)
    col.addWidget(nm)
    h.addLayout(col, 1)
    # right side: the slot type on top, the usable classes stacked under it —
    # each tag in its class color, same treatment as the Farm rows
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
