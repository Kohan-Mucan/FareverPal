from __future__ import annotations
from functools import lru_cache
from .raw_data import DATA

@lru_cache(maxsize=1)
def load_dungeons() -> list[dict]:
    """Returns the list of dungeons from raw_data, sorted by level then name, Rifts last."""
    ds = DATA.get("dungeons", [])
    # Sort by: is_rift (False < True), then level, then name
    return sorted(ds, key=lambda d: (
        d.get("entrance_zone") == "Rifts",
        d.get("level", 99),
        d.get("name", "").lower()
    ))

@lru_cache(maxsize=1)
def by_id() -> dict[str, dict]:
    return {d["id"]: d for d in load_dungeons()}

@lru_cache(maxsize=1)
def by_entrance_zone() -> dict[str, dict]:
    return {d["entrance_zone"]: d for d in load_dungeons()}

@lru_cache(maxsize=1)
def dungeon_unit_ids() -> set[str]:
    """Returns a set of all unit IDs (bosses and mobs) that appear in dungeons."""
    ids = set()
    for d in load_dungeons():
        bid = d.get("boss_id")
        if bid: ids.add(bid)
        for mid in d.get("mobs", []): ids.add(mid)
    return ids

@lru_cache(maxsize=1)
def unit_to_dungeon_map() -> dict[str, dict]:
    """Returns a mapping of unit ID -> dungeon dict for all dungeon bosses and mobs."""
    mapping = {}
    for d in load_dungeons():
        bid = d.get("boss_id")
        if bid:
            mapping[bid] = d
        for mid in d.get("mobs", []):
            mapping[mid] = d
    return mapping

# The two Rift bosses each spawn at a fixed spot — one in Z1 (Enripit Falls),
# one in Z2 (Krisomal North) — but the compiled data carries no boss -> spot
# mapping (both rift POIs are named 'POI Rift 01' and every rift mob resolves
# to both). These come from the known in-game spawn points; flip if the game
# changes them.
_RIFT_ZONE_BY_ID = {
    "rift_maat": "Z1",        # Rift Nightking Maat Demon
    "rift_shaarlize": "Z2",   # Rift Nightqueen Shaarlize Te'ror
}


def zone_tag_from_zone_id(zone_id: str) -> str | None:
    """'Z1_Enripit_Falls' -> 'Z1' (Crimson -> Z3), or None."""
    if not zone_id:
        return None
    if "Crimson" in zone_id:
        return "Z3"
    for prefix in ("Z1_", "Z2_", "Z3_"):
        if zone_id.startswith(prefix):
            return prefix[:2]
    return None


def zone_tag(d: dict) -> str | None:
    """Zone filter tag for a dungeon ('Z1'/'Z2'/'Z3'), or None.

    Crimson entrances map to Z3 — the codex treats Crimson Island as Z3 even
    though its metadata entrance zones are Z2_CrimsonIsland_*. Rifts resolve
    by their fixed spawn point (one in Z1, one in Z2).
    """
    ez = d.get("entrance_zone") or ""
    if ez == "Rifts":
        return _RIFT_ZONE_BY_ID.get(d.get("id"))
    return zone_tag_from_zone_id(ez)


def get_dungeon_info(activity_id_or_name: str | None) -> dict | None:
    if not activity_id_or_name:
        return None
    
    # 1. Try by ID (exact)
    d_map = by_id()
    if activity_id_or_name in d_map:
        return d_map[activity_id_or_name]
        
    # 2. Try by entrance zone
    e_map = by_entrance_zone()
    if activity_id_or_name in e_map:
        return e_map[activity_id_or_name]
        
    # 3. Try by Name or In-game Name (fuzzy)
    low_val = activity_id_or_name.lower()
    
    # Handle "Rift " prefix (try both with and without)
    search_vals = [low_val]
    if low_val.startswith("rift "):
        search_vals.append(low_val[5:].strip())
    
    for d in load_dungeons():
        d_name = d.get("name", "").lower()
        d_ig = d.get("ingame_name", "").lower()
        
        for v in search_vals:
            # Pre-process for common typos/quirks in poi_locs.json names
            v_clean = v.replace("abbandoned", "abandoned").replace("mines", " mines")
            v_clean = v_clean.replace("barraks", "barracks").replace("goulp", "gulp")
            v_clean = v_clean.replace("manfish", "manfish ").strip()

            if d_name == v or d_ig == v or d_name == v_clean or d_ig == v_clean:
                return d
            
    return None
