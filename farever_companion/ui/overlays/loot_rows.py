"""Shared loot-row builder + the dungeon HUD's loot section.

The grouped rarity-first LOOT RowSpec list is identical for the Dungeon HUD
and the Entity HUD, so both call `build_loot_specs()` here instead of keeping
two copies. `DungeonLootMixin` stays dungeon-only (titlebar toggle + reading
the dungeon's loot sweep); the Entity HUD calls the builder directly.
"""
from __future__ import annotations

from PySide6 import QtGui, QtCore

from .. import theme
from .entity_rows import RowSpec

_RARITY_RANK = {"Legendary": 4, "Epic": 3, "Rare": 2, "Uncommon": 1,
                "Common": 0}


def build_loot_specs(drops, xyz, floor: str, is_tracked, track,
                     accent=None, copy_id=None) -> list[RowSpec]:  # noqa: E501
    """Rows for a LOOT section: grouped by item, best rarity first then
    nearest, honoring `floor` (Off/All/Common+/Uncommon+/Rare+/Epic+). Empty
    list when nothing qualifies (caller hides the section). `accent` is the
    HUD highlight color used for tracked rows."""
    if not drops:
        return []
    from ...data.items import catalog

    def _drop_dist(dd):
        return dd.dist2d(xyz[0], xyz[1])

    # Off = filter disabled (every rarity shows, incl. Common); All is the
    # explicit same choice; the floors hide anything below them.
    min_rank = {"Off": -1, "All": -1, "Common+": 0, "Uncommon+": 1,
                "Rare+": 2, "Epic+": 3}.get(floor, -1)

    groups: dict[str, list] = {}
    for d in drops:
        groups.setdefault(d.item_id or "?", []).append(d)

    def _rarity(iid: str, ds) -> str | None:
        # the drop's live rolled rarity wins (dungeon gear rolls above its
        # authored template); catalog rarity is the fallback
        live = [d.rarity for d in ds if getattr(d, "rarity", None)]
        if live:
            return max(live, key=lambda r: _RARITY_RANK.get(r, -1))
        return (catalog.item(iid) or {}).get("rarity")

    acc = accent or theme.ACCENT
    specs: list[RowSpec] = []
    for iid, ds in sorted(
            groups.items(),
            key=lambda kv: (
                -_RARITY_RANK.get(_rarity(kv[0], kv[1]) or "", 0),
                min(_drop_dist(x) for x in kv[1]))):
        row = catalog.item(iid) if iid != "?" else None
        rar = _rarity(iid, ds)
        if min_rank >= 0 and \
                _RARITY_RANK.get(rar or "", 0) < min_rank:
            continue          # below the picked rarity floor
        label = (row or {}).get("name") or iid
        total = sum(d.count for d in ds)
        if total > 1:
            label = f"{label} ×{total}"   # stack count inline = one line
        nd = min(ds, key=_drop_dist)
        col = theme.rarity_color(rar) if rar else "#4ADE80"
        pos_key = f"{nd.x:.1f},{nd.y:.1f},{nd.z:.1f}|{label}"
        tracked = is_tracked("pos", pos_key)
        specs.append(RowSpec(
            "item", iid, col, label,
            acc if tracked else col,
            value=f"{_drop_dist(nd):>6.0f}m",
            bold=tracked, highlight=tracked,
            border_color=col,
            # Legendary rows get the gold background fill; every other
            # rarity keeps its rarity-colored border on a plain row.
            bg_tint=(col if (rar or "") == "Legendary" and not tracked
                     else None),
            cb=(lambda k=pos_key: track("pos", k)),
            right_cb=(lambda v=iid: copy_id(v)) if copy_id else None,
            key=("loot", iid)))
    return specs


class DungeonLootMixin:
    """Dungeon HUD: titlebar loot toggle + the LOOT section builder (delegates
    the row math to build_loot_specs)."""

    # --- titlebar toggle ---------------------------------------------------
    def set_show_loots(self, on: bool):
        self.s.dungeon_show_loots = bool(on)
        self.s.save()
        self._update_loot_btn()
        self._tick()

    def _update_loot_btn(self) -> None:
        from ...data import icons
        on = getattr(self.s, "dungeon_show_loots", True)
        self._loot_btn.setIcon(QtGui.QIcon(icons.ui_icon(
            "box" if on else "eye-off",
            self.s.hud_accent if on else theme.MUTED, 18)))
        self._loot_btn.setIconSize(QtCore.QSize(18, 18))
        self._loot_btn.setToolTip("Show loot" if on else "Hide loot")

    # --- specs -------------------------------------------------------------
    def loot_specs(self, isz: int) -> list[RowSpec] | None:
        """Rows for the LOOT section: grouped by item, rarity-first ordering,
        filtered by the Entity page's rarity floor. None hides the section."""
        if getattr(self.s, "dungeon_show_loots", True) is False:
            return None
        drops = self._dg_loot
        if not drops:
            return None
        xyz = self._dg_xyz or (0.0, 0.0, 0.0)
        floor = getattr(self.s, "dungeon_loot_filter", "Off")
        specs = build_loot_specs(
            drops, xyz, floor,
            is_tracked=self._is_tracked, track=self._track,
            accent=self.s.hud_accent, copy_id=self._dg_copy_id)
        return specs or None
