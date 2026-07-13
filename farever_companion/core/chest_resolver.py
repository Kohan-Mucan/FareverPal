"""Chest loot-table resolution and static/live merge (pure policy, no memory reads).

Owns the per-chest table cache, boss re-attribution, and the merge of the static
overworld chest index with the live scene's chests, so LiveModel stays thin.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from ..data import units as udata
from ..geo import chests as chestdb

log = logging.getLogger(__name__)


@dataclass
class ChestRow:
    chest_id: str
    dist: float
    loot_table: str | None
    level: int | None
    state: str | None
    live: bool
    anomaly: bool = False


class ChestResolver:
    def __init__(self):
        try:
            self.chests = chestdb.load_chests()
        except Exception as e:
            log.warning("chest geo load failed (overworld POIs disabled): %s", e)
            self.chests = []
        self._static_ids = {c.chest_id for c in self.chests}
        self._table_cache: dict[str, str | None] = {}

    def chest_table(self, chest_id: str, dungeon_boss: str | None,
                    default_table: str | None = None) -> str | None:
        """Resolve a chest's loot table. Non-boss chests are cached once so they
        can't flicker; boss chests are never cached, since they re-attribute to
        the live dungeon boss each frame."""
        boss_chest = chest_id.startswith("BossChest")
        if not boss_chest and chest_id in self._table_cache:
            return self._table_cache[chest_id]
            
        tbl = default_table or chestdb.loot_table_for(chest_id)
        
        # Fallback for generic world chests that missing a table assignment
        if not tbl:
            eid_l = chest_id.lower()
            if "chest" in eid_l or "crate" in eid_l:
                tbl = "WorldCrate"

        if boss_chest and dungeon_boss and (not tbl or not udata.is_boss(tbl)):
            tbl = udata.boss_loot_table(dungeon_boss) or tbl
        if tbl and not boss_chest:
            self._table_cache[chest_id] = tbl
        return tbl

    def nearest_chests_merged(self, xyz, n: int, dungeon_boss: str | None,
                              live_chests, max_dist: float = 0.0,
                              player_zone: str | None = None,
                              use_2d: bool = False) -> list[ChestRow]:
        rows: dict[str, ChestRow] = {}
        px, py, pz = xyz
        
        static_ranks = chestdb.nearest2d(self.chests, px, py, n=10 ** 6) if use_2d \
                       else chestdb.nearest(self.chests, *xyz, n=10 ** 6)
        
        for c, d in static_ranks:
            # Exclude BossChests, Activity triggers, and Camps
            if (c.chest_id.startswith("BossChest") or
                "activity" in c.chest_id.lower() or
                "camp" in c.chest_id.lower() or
                (c.loot_table and "activity" in c.loot_table.lower())):
                continue
            if player_zone:
                from ..geo import zones as geo_zones
                czone = geo_zones.chest_zone(c.chest_id)
                if czone != player_zone:
                    continue
            tbl = self.chest_table(c.chest_id, dungeon_boss, c.loot_table)
            # drop a static boss chest belonging to a different boss
            if (tbl and dungeon_boss and tbl != dungeon_boss
                    and udata.is_boss(tbl)):
                continue
            rows[c.chest_id] = ChestRow(c.chest_id, d, tbl, c.level, None, False)
        for e in live_chests:
            if not e.elem_id:
                continue
            # Exclude BossChests, checkpoints and Activity triggers from live scan
            elem_id_lower = e.elem_id.lower()
            if "activity" in elem_id_lower or "checkpoint" in elem_id_lower or e.elem_id.startswith("BossChest"):
                continue
            d = e.dist2d(px, py) if use_2d else e.dist(*xyz)
            rows[e.elem_id] = ChestRow(
                e.elem_id, d, self.chest_table(e.elem_id, dungeon_boss),
                None, e.state, True, anomaly=(e.elem_id not in self._static_ids))
        out = sorted(rows.values(), key=lambda r: r.dist)
        if max_dist > 0:
            out = [r for r in out if r.dist <= max_dist]
        return out[:n]
