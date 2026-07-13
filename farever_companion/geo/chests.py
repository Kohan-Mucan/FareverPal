"""Static chest index: id -> world position + loot table.

Reads from `htdocs/assets/data/map_markers.json` — the unified scan-generated
file that contains both chests (kind="chest") and POIs (kind="poi") with full
world positions and loot table assignments.

Coordinates are global world XYZ, 1:1 with the runtime player struct.
Pure data, no attached process.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from functools import lru_cache

from .. import paths
from ..data import cdb


@dataclass(frozen=True)
class Chest:
    chest_id: str
    loot_table: str | None
    x: float
    y: float
    z: float
    faction: str | None = None
    level: int | None = None
    name: str | None = None


    def dist(self, x: float, y: float, z: float) -> float:
        return math.dist((self.x, self.y, self.z), (x, y, z))

    def dist2d(self, x: float, y: float) -> float:
        return math.hypot(self.x - x, self.y - y)


@lru_cache(maxsize=1)
def _chest_markers() -> list[dict]:
    """Raw chest entries from chest_locs.json."""
    try:
        return json.loads(paths.chest_locs_path().read_text(encoding="utf-8")).get("chests", [])
    except (OSError, json.JSONDecodeError):
        return []


@lru_cache(maxsize=1)
def _poi_markers() -> list[dict]:
    """Raw POI entries from poi_locs.json."""
    try:
        return json.loads(paths.poi_locs_path().read_text(encoding="utf-8")).get("pois", [])
    except (OSError, json.JSONDecodeError):
        return []


@lru_cache(maxsize=1)
def load_chests() -> list[Chest]:
    """All chests from chest_locs.json that have a world position."""
    out = []
    for m in _chest_markers():
        wp = m.get("world_pos") or {}
        x = wp.get("x")
        y = wp.get("y")
        if x is None or y is None:
            continue
        
        lt = m.get("lootTable")
        # Fix missing data: WorldChest is used in scan but missing from tables; use WorldCrate
        if lt == "WorldChest":
            lt = "WorldCrate"
            
        out.append(Chest(
            chest_id=m.get("chest_id") or m.get("id", ""),
            loot_table=lt,
            x=float(x),
            y=float(y),
            z=float(m.get("z", 0.0)),
        ))
    return out


@lru_cache(maxsize=1)
def _index() -> dict[str, str]:
    """{chest_id -> lootTable} built from chest_locs + poi_locs."""
    out: dict[str, str] = {}
    for m in _chest_markers():
        lt = m.get("lootTable")
        if not lt:
            continue
        
        # Fix missing data: WorldChest is used in scan but missing from tables; use WorldCrate
        if lt == "WorldChest":
            lt = "WorldCrate"

        cid = m.get("chest_id") or m.get("id")
        if cid:
            out[cid] = lt
    # POI entries carry chest_ids lists with boss loot tables
    for m in _poi_markers():
        lt = m.get("lootTable")
        if not lt:
            continue
        for cid in m.get("chest_ids") or []:
            if cid and cid not in out:
                out[cid] = lt
    return out


@lru_cache(maxsize=1)
def _table_ids() -> frozenset[str]:
    return frozenset(r["id"] for r in cdb.lines("lootTable"))


@lru_cache(maxsize=4096)
def loot_table_for(chest_id: str | None) -> str | None:
    """Resolve a chest id to its loot table, deterministically:
    1. exact match in the index;
    2. the id IS itself a loot-table id;
    3. strip trailing _<n> instance suffixes and retry 1+2;
    4. longest template-prefix match in the index.
    Pure + cached, so a given id always resolves identically (no flicker)."""
    if not chest_id:
        return None
    idx = _index()
    tables = _table_ids()
    if chest_id in idx:
        return idx[chest_id]
    if chest_id in tables:
        return chest_id
    base = chest_id
    while True:
        stripped = re.sub(r"_\d+$", "", base)
        if stripped == base:
            break
        base = stripped
        if base in idx:
            return idx[base]
        if base in tables:
            return base
    cands = [k for k in idx if chest_id.startswith(k)]
    if cands:
        return idx[max(cands, key=len)]
    return None


def nearest(chests, x: float, y: float, z: float, n: int = 10):
    ranked = sorted(((c, c.dist(x, y, z)) for c in chests), key=lambda t: t[1])
    return ranked[:n]


def nearest2d(chests, x: float, y: float, n: int = 10):
    ranked = sorted(((c, c.dist2d(x, y)) for c in chests), key=lambda t: t[1])
    return ranked[:n]
