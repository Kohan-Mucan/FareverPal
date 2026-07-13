"""Codex / Bestiary data.

Maps units to regions for the Codex UI, and provides the region list.
"""
from __future__ import annotations

from functools import lru_cache

from . import cdb, units, names


@lru_cache(maxsize=1)
def region_names() -> dict[str, str]:
    """{region_id: region_name}, e.g. {'Z1': 'Skover Island'}."""
    out = {}
    for k, v in names._zone_region_names().items():
        out[f"Z{k}"] = v
    
    out["Bosses"] = "Dungeons"
    out["Pets"] = "Pets"
    out["Z0"] = "Others"
    return out


@lru_cache(maxsize=1)
def unit_regions() -> dict[str, str]:
    """Map of unit_id -> region_id (e.g. 'Z1') for all codex units."""
    out = {}
    
    # 1. Classify every concrete codex unit
    for uid in units.codex_unit_ids():
        uname = names.unit_name(uid) or ""
        if "todo" in uname.lower() or "todo" in uid.lower():
            out[uid] = "Z0"
            continue

        is_boss = units.is_boss(uid)
        
        # Priority 1: Real bosses and Dungeon-only mobs go to the Bosses tab
        r = units._units_by_id().get(uid, {})
        rid = r.get("region")

        if is_boss or rid == "Dungeon" or "_D_" in uid or "_D" in uid or "D_" in uid:
            out[uid] = "Bosses"
            continue

        # Priority 2: Get the default region mapping (from baked region or ID suffix)
        if not rid:
            if "Z3" in uid: rid = "Z3"
            elif "Z2" in uid: rid = "Z2"
            elif "Z1" in uid: rid = "Z1"
            
        if rid:
            out[uid] = rid
        else:
            out[uid] = "Z0"

    # 2. Add companions to the Pets region
    for uid in units._companion_ids():
        out[uid] = "Pets"

    return out


@lru_cache(maxsize=1)
def enemies_data() -> dict[str, dict]:
    """Consolidated enemy info from raw_data or enemies.json."""
    return {r["id"]: r for r in cdb.display_data("enemies")}


def units_by_region(region_id: str) -> list[dict]:
    """All codex units in a region, with details, sorted by name."""
    u_regions = unit_regions()
    e_data = enemies_data()
    
    # Cache for companion data
    from . import collections as col
    comp_data = {r["id"]: r for r in col.items("companions")}
    
    out = []
    seen_names = set()
    
    # Iterate over all known units that have been assigned to a region
    for uid, rid in u_regions.items():
        if rid == region_id:
            info = e_data.get(uid, {})
            
            # Prefer or fallback to companion catalog data if it's a companion
            cinfo = comp_data.get(uid)
            if cinfo:
                # Merge: companion catalog has better icons for the UI
                info = {**info, **cinfo}

            nm = info.get("name")
            # If name is missing or is just the ID, try the smarter resolver
            if not nm or nm == uid:
                nm = names.unit_name(uid)
                
            if nm:
                if nm.lower() in seen_names:
                    continue
                seen_names.add(nm.lower())

            out.append({
                "id": uid,
                "name": nm,
                "icon": info.get("icon"),
                "type": info.get("type"),
                "is_boss": units.is_boss(uid),
                "is_critter": info.get("isCritter", False) or info.get("type") == "Critter",
            })
            
    # Sort primarily by name, secondarily by ID
    return sorted(out, key=lambda x: (x["name"].lower(), x["id"]))
