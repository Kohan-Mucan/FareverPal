"""Static POI index: id -> world position, type, and metadata.

Reads from `assets/data/poi_locs.json`. Handles obelisks, dungeons,
respawn points, and other fixed map markers.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache

@dataclass(frozen=True)
class POI:
    id: str
    kind: str      # "poi"
    sub_kind: str  # "dungeon", "obelisk", "respawn", "rift", "soulstone"
    x: float
    y: float
    z: float
    name: str | None = None
    zone: str | None = None
    target_activity: str | None = None
    spawn_unit: str | None = None   # soulstone: summoned boss unit id
    cost_item: str | None = None    # soulstone: item consumed to summon
    cost_count: int | None = None   # soulstone: amount consumed

    def dist2d(self, x: float, y: float) -> float:
        return math.hypot(self.x - x, self.y - y)

# Kinds whose POI rows include per-instance scenery, not just the world
# marker. The 2026-09-11 game-data regen reclassified every interior prop
# (DungeonExit_*, POI_Dungeon_*_CheckpointZone_*, BeeHive_HoneyStackPlatform*,
# Rift_Gate_*, Rift_Bonus_Chest_*, Monolith_*, …) as `dungeon`/`rift`, taking
# those kinds from 14 rows (12 dungeon + 2 rift entrances) to 243. The extras
# carry instance-local coordinates that overlap the overworld map, so plotting
# them plastered the minimap with dungeon/rift icons. A real entrance is the
# row that names the activity it opens (or at least a display name).
_ENTRANCE_KINDS = ("dungeon", "rift")


def _is_world_poi(m: dict) -> bool:
    """False for a dungeon/rift scenery row that is not a real entrance."""
    if m.get("sub_kind") not in _ENTRANCE_KINDS:
        return True
    return bool(m.get("target_activity") or m.get("name"))


@lru_cache(maxsize=1)
def load_pois() -> list[POI]:
    """All world POIs from compiled raw data with valid world positions.

    Dungeon/rift rows are reduced to their entrance markers (see
    `_is_world_poi`); obelisks, respawn points, vendors and soulstones are
    returned as-is.
    """
    from ..data import cdb
    out = []
    for m in cdb.lines("poi_locs"):
        wp = m.get("world_pos")
        if not wp or "x" not in wp or "y" not in wp:
            continue
        if not _is_world_poi(m):
            continue
        out.append(POI(
            id=m.get("id", ""),
            kind=m.get("kind", "poi"),
            sub_kind=m.get("sub_kind", ""),
            x=float(wp["x"]),
            y=float(wp["y"]),
            z=float(m.get("z", 0.0)),
            name=m.get("name"),
            zone=m.get("zone"),
            target_activity=m.get("target_activity"),
            spawn_unit=m.get("spawn_unit"),
            cost_item=m.get("cost_item"),
            cost_count=m.get("cost_count")
        ))
    return out

@lru_cache(maxsize=1)
def by_id() -> dict[str, POI]:
    return {p.id: p for p in load_pois()}
