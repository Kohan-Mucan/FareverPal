"""Chests & Orbs list view for the Codex Collection tab.

The zone filter keys (All / <first word of each zone name>) slice the list by
the item's zone (orbs carry a baked region; chests resolve theirs from world
position / id prefix).
"""
from __future__ import annotations

from functools import lru_cache

from PySide6 import QtCore, QtWidgets

from ... import components as C
from ... import theme
from ....data import names
from ....geo import chests as geo_chests
from ....geo import orbs as geo_orbs
from ....geo import zones as geo_zones
from .dungeon_list import _WrapLabel


def _chest_zone(c) -> str:
    """Zone tag ('Z1'/'Z2'/'Z3'/'Other') for a chest — world position first
    (covers id-less W1_* entries), id prefix as fallback."""
    from ....data import dungeons
    z = geo_zones.resolve_zone(c.x, c.y, c.z) or ""
    tag = dungeons.zone_tag_from_zone_id(z)
    if tag:
        return tag
    return dungeons.zone_tag_from_zone_id(c.chest_id) or "Other"


@lru_cache(maxsize=1)
def _chest_zone_cache() -> dict[str, str]:
    """chest_id -> zone tag, built once (positions are static)."""
    return {c.chest_id: _chest_zone(c) for c in geo_chests.load_chests()}


def remaining_items(ui, kinds=None) -> list[dict]:
    """Secret orbs + chests the attached profile hasn't collected yet.

    Each item: {kind ('orb'|'chest'), id, label, x, y, z, zone}. `kinds`
    limits to a subset ({'chest'} / {'orb'}); None means both. Read-only;
    callers may filter/sort freely. With no profile attached the shared
    (non-profile) poi_done list is used.
    """
    kinds = {"chest", "orb"} if kinds is None else set(kinds)
    profile = ui.model.player_profile() if (hasattr(ui, "model") and ui.model) else None
    done = _done_id_set(ui, profile)
    items: list[dict] = []
    if "orb" in kinds:
        for o in geo_orbs.load_orbs():
            if o.orb_id.lower() in done:
                continue
            items.append({
                "kind": "orb",
                "id": o.orb_id,
                "label": geo_orbs.orb_label(o.orb_id),
                "x": o.x, "y": o.y, "z": o.z,
                "zone": o.region if o.region in ("Z1", "Z2", "Z3") else "Other",
            })
    if "chest" in kinds:
        zones = _chest_zone_cache()
        for c in geo_chests.load_chests():
            # Recipe chests are their own thing — the minimap renders them as
            # recipe icons and the entity HUD never lists them, so they don't
            # belong in the remaining-chests hunt either.
            if "recipe" in c.chest_id.lower() or "recipe" in (c.loot_table or "").lower():
                continue
            if c.chest_id.lower() in done:
                continue
            items.append({
                "kind": "chest",
                "id": c.chest_id,
                "label": names.poi_label(c.chest_id) or c.chest_id,
                "x": c.x, "y": c.y, "z": c.z,
                "zone": zones.get(c.chest_id) or "Other",
            })
    return items


def _done_id_set(ui, profile: str | None) -> set[str]:
    """Collected ids (lower-cased) plus the activity base of any end-chest id.

    The minimap marks event chests done with their full end-chest id (e.g.
    ``..._ChestOrb_10_Chest_1``, ``..._FightStone_11_Chest_1``) but the static
    index stores the activity base (``..._ChestOrb_10``), so a raw membership
    test leaves completed event chests listed as uncollected.  Same
    normalization the minimap uses at render time (activity_base_id).
    """
    from ....core import chest_resolver
    raw = ui.s.get_poi_done(profile)
    out = {d.lower() for d in raw}
    for d in raw:
        b = chest_resolver.activity_base_id(d)
        if b:
            out.add(b.lower())
    return out


def _sort_key(it: dict):
    zone_order = {"Z1": 0, "Z2": 1, "Z3": 2}.get(it["zone"], 3)
    kind_order = 0 if it["kind"] == "orb" else 1
    if it["kind"] == "orb":
        try:
            num = int(it["id"].rsplit("_", 1)[-1])
        except ValueError:
            num = 0
        return (zone_order, kind_order, num)
    return (zone_order, kind_order, it["label"].lower())


class ChestOrbListCell(QtWidgets.QFrame):
    """One 2-column cell: orb/chest marker icon + label + zone sub-label."""

    pick = QtCore.Signal(object)  # item dict

    def __init__(self, ui, item: dict, parent=None):
        super().__init__(parent)
        self.ui = ui
        self.item = item
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setStyleSheet(
            "QFrame { background: transparent; border: 0; }"
            f"QFrame:hover {{ background: {theme.with_alpha(theme.TEXT, 10)}; }}")

        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(8, 4, 10, 4)
        lay.setSpacing(10)

        icon = C.IconTile(size=42)
        if item["kind"] == "orb":
            icon.set_marker("orb", theme.KIND_COLOR["orb"])
        else:
            icon.set_marker("chest", theme.CHEST)
        lay.addWidget(icon)

        txt = QtWidgets.QVBoxLayout()
        txt.setSpacing(1)
        txt.setContentsMargins(0, 0, 0, 0)
        txt.setAlignment(QtCore.Qt.AlignVCenter)

        self._name_lbl = _WrapLabel(item["label"])
        self._name_lbl.setStyleSheet(f"color:{theme.TEXT}; font-size:14px; font-weight:bold;")
        self._name_lbl.setMinimumWidth(0)
        txt.addWidget(self._name_lbl)

        zone_name = geo_orbs.REGION_NAMES.get(item["zone"], item["zone"])
        sub = f"{zone_name} · {'Secret Orb' if item['kind'] == 'orb' else 'Chest'}"
        self._sub_lbl = _WrapLabel(sub)
        self._sub_lbl.setStyleSheet(f"color:{theme.MUTED}; font-size:12px;")
        self._sub_lbl.setMinimumWidth(0)
        txt.addWidget(self._sub_lbl)

        self._track_lbl = QtWidgets.QLabel("◈ TRACKING")
        self._track_lbl.setStyleSheet(f"color:{theme.ACCENT}; font-size:11px; font-weight:bold;")
        self._track_lbl.setVisible(False)
        txt.addWidget(self._track_lbl)
        lay.addLayout(txt, 1)

        self.setToolTip(f"{item['id']}\nRight-click to mark collected (hides it)")
        self.refresh_tracked()

    def refresh_tracked(self) -> None:
        """Tracked rows: accent name + a '◈ TRACKING' row (columns never move)."""
        on = self.ui._is_chest_orb_tracked(self.item)
        accent = self.ui.s.hud_accent or theme.ACCENT
        self._track_lbl.setStyleSheet(f"color:{accent}; font-size:11px; font-weight:bold;")
        self._track_lbl.setVisible(on)
        self._name_lbl.setStyleSheet(
            f"color:{accent}; font-size:14px; font-weight:bold;" if on
            else f"color:{theme.TEXT}; font-size:14px; font-weight:bold;")

    def mousePressEvent(self, e):
        if e.button() == QtCore.Qt.LeftButton:
            self.pick.emit(self.item)
        elif e.button() == QtCore.Qt.RightButton:
            # Record as collected in the attached profile -> row disappears.
            self.ui._mark_chest_orb_done(self.item)
        super().mousePressEvent(e)


class ChestOrbListView(QtWidgets.QFrame):
    """Centered 2-column list of remaining chests + secret orbs, grouped by
    zone with count headers; spans the codex grid when the toggle is active."""

    def __init__(self, ui, parent=None):
        super().__init__(parent)
        self.ui = ui
        self.row_count = 0
        self._cells: list[ChestOrbListCell] = []

        grid = QtWidgets.QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(4)
        grid.setAlignment(QtCore.Qt.AlignHCenter | QtCore.Qt.AlignTop)

        # The view is split by kind: the 'Chests' toggle lists only chests,
        # 'Orbs' only secret orbs. Zone filter (All / zone-name keys) slices
        # whatever kind is active.
        kind = getattr(ui, "_codex_chest_orb_kind", lambda: None)()
        kinds = ({"chest"} if kind == "chests" else {"orb"} if kind == "orbs"
                 else {"chest", "orb"})
        zone = getattr(ui, "_dungeon_zone", "All") or "All"
        items = [it for it in remaining_items(ui, kinds) if zone == "All" or it["zone"] == zone]
        items.sort(key=_sort_key)

        row = 0
        for ztag in ("Z1", "Z2", "Z3", "Other"):
            zi = [it for it in items if it["zone"] == ztag]
            if not zi:
                continue
            n_orb = sum(1 for it in zi if it["kind"] == "orb")
            n_chest = len(zi) - n_orb
            zname = geo_orbs.REGION_NAMES.get(ztag, "Other")
            counts = " · ".join(p for p in (f"{n_orb} orbs" if n_orb else "",
                                             f"{n_chest} chests" if n_chest else "") if p)
            header = QtWidgets.QLabel(f"{zname.upper()} — {counts}")
            header.setStyleSheet(f"color: {theme.ACCENT}; margin-top: 10px; margin-bottom: 4px; font-size: 13px;")
            grid.addWidget(header, row, 0, 1, 2)
            row += 1
            for i, it in enumerate(zi):
                cell = ChestOrbListCell(ui, it)
                cell.pick.connect(ui._track_chest_orb)
                grid.addWidget(cell, row + i // 2, i % 2, QtCore.Qt.AlignTop)
                self._cells.append(cell)
            row += (len(zi) + 1) // 2
        self.row_count = len(items)

        self._tr = getattr(getattr(ui, "overlay_mgr", None), "tracker", None)
        if self._tr is not None:
            self._tr.changed.connect(self.refresh_tracked)
        self.refresh_tracked()

    def refresh_tracked(self) -> None:
        if not self.isVisible():
            return
        for cell in self._cells:
            cell.refresh_tracked()
