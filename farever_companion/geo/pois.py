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
    sub_kind: str  # "dungeon", "obelisk", "respawn", "rift"
    x: float
    y: float
    z: float
    name: str | None = None
    zone: str | None = None
    target_activity: str | None = None

    def dist2d(self, x: float, y: float) -> float:
        return math.hypot(self.x - x, self.y - y)

@lru_cache(maxsize=1)
def load_pois() -> list[POI]:
    """All POIs from compiled raw data with valid world positions."""
    from ..data import cdb
    out = []
    for m in cdb.lines("poi_locs"):
        wp = m.get("world_pos")
        if not wp or "x" not in wp or "y" not in wp:
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
            target_activity=m.get("target_activity")
        ))
    return out

@lru_cache(maxsize=1)
def by_id() -> dict[str, POI]:
    return {p.id: p for p in load_pois()}
