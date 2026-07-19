"""Codex / Bestiary data.

Maps units to regions for the Codex UI, and provides the region list.
"""
from __future__ import annotations

import json
from functools import lru_cache

from .. import paths
from . import cdb, units, names, collections as col


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
        
        # Priority 1: Real bosses and Dungeon-only mobs go to the Bosses tab.
        # Open-world bosses (with Z1/Z2/Z3 in ID) go to their respective regions.
        r = units._units_by_id().get(uid, {})
        rid = r.get("region")

        is_dungeon = rid == "Dungeon" or "_D_" in uid or uid.endswith("_D") or uid.startswith("D_") or "Z1D" in uid or "Z2D" in uid or "Z3D" in uid
        is_world_boss = is_boss and ("Z1" in uid or "Z2" in uid or "Z3" in uid) and not is_dungeon

        if (is_boss and not is_world_boss) or is_dungeon:
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
    e_data = enemies_data()
    
    # Special case: Dungeons tab is driven directly by dungeons.json
    if region_id == "Bosses":
        from . import dungeons
        out = []
        for d in dungeons.load_dungeons():
            dname = d.get("name")
            if not dname:
                continue

            if d.get("entrance_zone") == "Rifts" and not dname.lower().startswith("rift"):
                dname = f"Rift {dname}"

            # Boss
            bid = d.get("boss_id")
            if bid:
                info = e_data.get(bid, {})
                out.append({
                    "id": bid,
                    "name": d.get("boss_name") or info.get("name") or names.unit_name(bid),
                    "type": dname,
                    "is_boss": True,
                    "is_elite": False,
                    "is_critter": False,
                    "level": d.get("level", 99),
                    "is_rift": d.get("entrance_zone") == "Rifts",
                })
            
            # Mobs
            for mid in d.get("mobs", []):
                info = e_data.get(mid, {})
                out.append({
                    "id": mid,
                    "name": info.get("name") or names.unit_name(mid),
                    "type": dname,
                    "is_boss": False,
                    "is_elite": info.get("isElite", False),
                    "is_critter": False,
                    "level": d.get("level", 99),
                    "is_rift": d.get("entrance_zone") == "Rifts",
                })
        return out

    u_regions = unit_regions()
    # Cache for companion data
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

            # Use species (from ID prefix) as the primary type for Pets to group them
            tp = info.get("type")
            if region_id == "Pets":
                if "_" in uid:
                    prefix = uid.split("_")[0]  # e.g. 'Rabbit' from 'Rabbit_Yellow'
                    # Strip "Spark" prefix if it's attached to the species name (e.g. SparkHorse -> Horse)
                    if prefix.startswith("Spark") and prefix[5:6].isupper():
                        prefix = prefix[5:]
                    tp = prefix
                else:
                    # Fallback for IDs without underscores (like 'YellowRabbits')
                    tp = info.get("subtype") or tp
                    for species in ["Rabbit", "Sheep", "Squirrel", "Horse", "Lizard", "Frog"]:
                        if species in uid:
                            tp = species
                            break

            out.append({
                "id": uid,
                "name": nm,
                "type": tp,
                "icon": info.get("icon") or uid,
                "is_boss": units.is_boss(uid),
                "is_elite": info.get("isElite", False),
                "is_critter": info.get("isCritter", False) or info.get("type") == "Critter",
            })
            
    # Sort primarily by type for pets and bosses, otherwise by name
    if region_id in ("Pets", "Bosses"):
        # Group by type (species/dungeon), then boss first, then alphabetical by name
        return sorted(out, key=lambda x: (x["type"] or "", not x.get("is_boss", False), x["name"].lower()))

    # Sort primarily by name, secondarily by ID
    return sorted(out, key=lambda x: (x["name"].lower(), x["id"]))
