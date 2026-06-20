"""Zone resolving utilities.

Computes the active zone dynamically based on the closest static orb or chest
coordinate, keeping the system robust against memory changes.
"""
from __future__ import annotations

import math
from functools import lru_cache
from . import orbs as geo_orbs
from . import chests as geo_chests

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

@lru_cache(maxsize=4096)
def resolve_zone(x: float, y: float, z: float) -> str | None:
    """Find the closest static orb to determine the active location/area ID."""
    orbs = geo_orbs.load_orbs()
    if not orbs:
        return None
    closest_orb = min(orbs, key=lambda o: math.dist((o.x, o.y, o.z), (x, y, z)))
    return get_area_id(closest_orb.zone)

@lru_cache(maxsize=1)
def static_chest_zones() -> dict[str, str | None]:
    """Map each static chest ID to its closest static orb's parent area ID."""
    chests = geo_chests.load_chests()
    orbs = geo_orbs.load_orbs()
    out = {}
    if not orbs:
        return out
    for c in chests:
        closest_orb = min(orbs, key=lambda o: math.dist((o.x, o.y, o.z), (c.x, c.y, c.z)))
        out[c.chest_id] = get_area_id(closest_orb.zone)
    return out

def chest_zone(chest_id: str) -> str | None:
    """Get the precomputed area ID for a static chest."""
    return static_chest_zones().get(chest_id)
