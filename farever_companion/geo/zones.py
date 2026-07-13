"""Zone resolving utilities.

Computes the active zone dynamically based on the closest static anchor
(Orb, Chest, or POI), keeping the system robust against memory changes.
"""
from __future__ import annotations

import math
import json
from functools import lru_cache
from . import orbs as geo_orbs
from . import chests as geo_chests
from .. import paths

@lru_cache(maxsize=1)
def zone_parent_map() -> dict[str, str]:
    """Map each sublocation or sub-zone ID to its parent location/area ID."""
    from ..data import cdb
    out = {}
    try:
        # Columns in zone sheet: id, parent, type (0=Region, 1=Location, 2=SubLocation)
        for r in cdb.lines("zone"):
            zid = r.get("id")
            parent = r.get("parent")
            ztype = r.get("type")
            if zid:
                # If it's a SubLocation (2) and has a parent, resolve to the parent Location (1)
                if ztype == 2 and parent:
                    out[zid] = parent
                else:
                    out[zid] = zid
    except Exception:
        pass
    return out

def get_area_id(zone_id: str | None) -> str | None:
    """Get the parent Location/Area ID for a given zone ID."""
    if not zone_id:
        return None
    return zone_parent_map().get(zone_id, zone_id)

@lru_cache(maxsize=1)
def load_anchors() -> list[tuple[float, float, float, str]]:
    """Combined list of (x, y, z, zone_id) from orbs and POIs for zone resolution."""
    anchors = []
    
    # 1. Add Orbs
    for o in geo_orbs.load_orbs():
        if o.zone:
            anchors.append((o.x, o.y, o.z, o.zone))
            
    # 2. Add POIs (Obelisks, Respawn Points, Dungeons)
    try:
        path = paths.poi_locs_path()
        if path.exists():
            poi_data = json.loads(path.read_text(encoding="utf-8"))
            for p in poi_data.get("pois", []):
                zid = p.get("zone")
                wp = p.get("world_pos")
                if zid and wp:
                    anchors.append((float(wp["x"]), float(wp["y"]), float(p.get("z", 0)), zid))
    except Exception:
        pass
        
    return anchors

@lru_cache(maxsize=4096)
def resolve_zone(x: float, y: float, z: float) -> str | None:
    """Find the closest static anchor to determine the active location/area ID."""
    anchors = load_anchors()
    if not anchors:
        return None
        
    # Find closest anchor by 3D distance
    best_zone = None
    min_dist = float('inf')
    for ax, ay, az, azone in anchors:
        dist_sq = (ax - x)**2 + (ay - y)**2 + (az - z)**2
        if dist_sq < min_dist:
            min_dist = dist_sq
            best_zone = azone
            
    return get_area_id(best_zone)

@lru_cache(maxsize=1)
def static_chest_zones() -> dict[str, str | None]:
    """Map each static chest ID to its closest anchor's parent area ID."""
    chests = geo_chests.load_chests()
    out = {}
    for c in chests:
        out[c.chest_id] = resolve_zone(c.x, c.y, c.z)
    return out

def chest_zone(chest_id: str) -> str | None:
    """Get the precomputed area ID for a static chest."""
    return static_chest_zones().get(chest_id)
