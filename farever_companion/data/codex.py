"""Codex / Bestiary data.

Maps units to regions for the Codex UI, and provides the region list.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache

from .. import paths
from . import cdb, units, names, collections as col

try:
    from . import raw_codex
except ImportError:
    raw_codex = None


@lru_cache(maxsize=1)
def region_names() -> dict[str, str]:
    """{region_id: region_name}, e.g. {'Z1': 'Skover Island'}."""
    out = {}
    for k, v in names._zone_region_names().items():
        out[f"Z{k}"] = v
    
    out["Bosses"] = "Dungeons"
    out["Pets"] = "Collection"
    out["Z0"] = "Other"
    return out


def is_zone_region(region_id: str) -> bool:
    """Returns True if region_id is an open-world zone (Z1, Z2, Z3, Z4... etc.), excluding Z0 (Other), Pets, and Bosses."""
    return bool(region_id and region_id.startswith("Z") and region_id != "Z0")


@lru_cache(maxsize=1)
def unit_regions() -> dict[str, str]:
    """Map of unit_id -> region_id (e.g. 'Z1') for all codex units."""
    out = {}
    
    # 1. Classify every concrete codex unit
    from . import dungeons
    d_mobs = dungeons.dungeon_unit_ids()

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

        is_dungeon = uid in d_mobs
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
            # The compiler bakes region="Dungeon" for dungeon-flagged units;
            # normalize it to the Bosses tab so they never vanish from the UI.
            out[uid] = "Bosses" if rid == "Dungeon" else rid
        else:
            out[uid] = "Z0"

    # 2. Add companions to the Pets region
    for uid in units._companion_ids():
        out[uid] = "Pets"

    return out


@lru_cache(maxsize=1)
def enemies_data() -> dict[str, dict]:
    """Consolidated enemy, pet, mount, and glider info from raw_data or raw_codex."""
    out = {r["id"]: r for r in cdb.display_data("enemies")}
    if raw_codex and hasattr(raw_codex, "DATA"):
        for zone_list in raw_codex.DATA.values():
            if isinstance(zone_list, dict):
                for sublist in zone_list.values():
                    for item in sublist:
                        if isinstance(item, dict) and "id" in item:
                            # Merge on top of the CDB row so codex-only keys
                            # (coords, vendor/chest/dungeon metadata) add to the
                            # base display data instead of replacing it.
                            out[item["id"]] = {**out.get(item["id"], {}), **item}
            elif isinstance(zone_list, list):
                for item in zone_list:
                    if isinstance(item, dict) and "id" in item:
                        out[item["id"]] = {**out.get(item["id"], {}), **item}
    return out


@lru_cache(maxsize=1)
def codex_order() -> dict:
    """Load the ground-truth unit order strictly from compiled raw_codex data.

    Region sections are always lists; anything else in the payload (e.g. the
    compiler's `__meta__` provenance block) is dropped so callers can iterate
    the whole dict safely."""
    if raw_codex and hasattr(raw_codex, "DATA"):
        return {k: v for k, v in raw_codex.DATA.items() if isinstance(v, list)}
    return {}


@lru_cache(maxsize=1)
def _dungeon_zone_by_uid() -> dict[str, str]:
    """Bosses-tab uid -> zone tag ('Z1'/'Z2'/'Z3'/'Rifts') for the Dungeons
    tab's zone filter keys.

    Derived from each entry's dungeon entrance zone (first entry wins, which
    matches units_by_region's dedup for mobs shared across dungeons).
    """
    from . import dungeons
    out: dict[str, str] = {}
    for e in codex_order().get("Bosses") or []:
        uid = e.get("id")
        if not uid or uid in out:
            continue
        d = dungeons.get_dungeon_info(e.get("type") or e.get("dungeon_name") or "")
        if not d:
            continue
        tag = dungeons.zone_tag(d)
        if tag:
            out[uid] = tag
    return out


def dungeon_zone_tag(uid: str) -> str | None:
    """Zone tag for a Dungeons-tab entry ('Z1'/'Z2'/'Z3'/'Rifts'), or None."""
    return _dungeon_zone_by_uid().get(uid)


# Entries flagged todo/unreleased have no locations by definition — the resolver
# short-circuits on these instead of running the expensive fallback chain.
# 'achievement' is the reflagged (compiler) form: an achievement reward is
# obtainable in-game but has no world spawn, so it resolves to its achievement
# title rather than the fuzzy species-group fallback.
_NO_LOC_KINDS = ("todo", "unreleased", "achievement")

# Rift reward tables (scan_item_drops anchors them at the arena entrance as
# chest sources) — display as their natural name instead of "Chest: Rift_Tier6".
_RIFT_REWARD_LABELS = {
    "Rift_BonusChest": "Rift Bonus Chest",
    "Rift_Bosschest": "Rift Boss Chest",
    "Rift_Tier6": "Rift Tier 6",
}


# Cash-shop / early-access premium items. The canonical list is the game's
# shop.json, compiled into raw_shop by compiler.py — until the dump ships it,
# the known premium set is identified by id: an `_EA_` / `EarlyAccess` token
# (Glider_Butterfly_EA_Spark, Rabbit_EarlyAccess_Spark) or the 'Spark'
# premium species PREFIX (SparkHorse_01). Wild 'spark-dust' pets use the
# `_Spark` SUFFIX on a normal species (Rabbit_Spark, Frog_Spark) and are
# deliberately NOT matched.
_SHOP_ID_RE = re.compile(r"(?i)(_ea_|earlyaccess|^spark)")

# Zone-mob ids embed their zone tag ('OgreManfish_Z2W_FS_Claws',
# 'Manfish_Z1D_Claws'). An ID-derived species group must never be one of
# these tokens — 'Z2W' would match every mob in the zone and paint the map
# with dozens of sibling spawns for a mob that has no recorded location.
# Curated `group` values baked by the compiler are trusted as-is.
_ZONE_TAG_RE = re.compile(r"(?i)^Z\d")


@lru_cache(maxsize=1)
def _shop_ids() -> frozenset[str]:
    """Cash-shop item ids. Prefers the compiled raw_shop (every row in the
    game's shop.json); falls back to an empty set until the dump ships it."""
    try:
        from . import raw_shop
        rows = getattr(raw_shop, "DATA", None)
        if rows:
            if isinstance(rows, dict):
                rows = rows.get("shop") or rows.get("items") or []
            if isinstance(rows, list):
                ids = {str(r.get("id")) for r in rows if isinstance(r, dict) and r.get("id")}
                if ids:
                    return frozenset(ids)
    except Exception:
        pass
    return frozenset()


def is_shop_item(item: dict) -> bool:
    """True for cash-shop / early-access premium items: the id is in the
    compiled shop.json list (raw_shop), or matches the known-premium id
    pattern (_SHOP_ID_RE) until the dump ships shop.json."""
    uid = item.get("id") or ""
    return uid in _shop_ids() or bool(_SHOP_ID_RE.search(uid))


def item_sources(item: dict) -> set[str]:
    """Source-badge keys an item carries: 'npc' (vendor), 'ach'
    (achievement), 'dungeon', 'chest', 'shop' (cash shop / early access),
    'mob' (mob drop).

    Single source of truth for the Pets/Others card badges AND the matching
    legend filters — keep the predicates here so the two can't drift.
    """
    sources: set[str] = set()
    if item.get("vendor_npc") or item.get("vendor_npcs"):
        sources.add("npc")
    if item.get("achievement"):
        sources.add("ach")
    if item.get("is_dungeon"):
        sources.add("dungeon")
    if item.get("chest_loc") or item.get("chest_locs"):
        sources.add("chest")
    if is_shop_item(item):
        sources.add("shop")
    # Mob-drop sourced — mounts/gliders that drop from specific mobs (the
    # resolver's 'Mob Drop:' chain; `drops_from` is the compiled marker, with
    # mob_id/drop_mob/mob_name kept for parity with the resolver).
    if item.get("drops_from") or item.get("mob_id") or item.get("drop_mob") or item.get("mob_name"):
        sources.add("mob")
    return sources


def belongs_in_others(item: dict) -> bool:
    """True when an item belongs in the Others bucket — the 'no home yet'
    list: cash-shop items, content not released yet, and todo placeholders.

    Released mounts/gliders (world drops and achievement rewards) are
    obtainable and live on the Collection tab's Mounts/Gliders views, so they
    must never render under Other. Shared predicate between the Others tab's
    'All' sub-view and the global search view so the two can't drift.
    """
    if is_shop_item(item):
        return True
    if (item.get("kind") or "").lower() in ("unreleased", "todo"):
        return True
    uid = (item.get("id") or "").lower()
    uname = (item.get("name") or "").lower()
    return "todo" in uid or "todo" in uname


def _valid_coords(coords) -> list[dict]:
    """Filter to real world positions (nonzero x/y)."""
    return [c for c in coords if isinstance(c, dict) and "x" in c and "y" in c
            and (abs(c["x"]) > 0.01 or abs(c["y"]) > 0.01)]


@lru_cache(maxsize=64)
def _group_mob_coords(group: str) -> tuple[tuple[dict, ...], tuple[str, ...]]:
    """(coords, matched_names) for every enemy whose id/name contains `group`.

    Cached per group name so the resolver's species-group fallback is a dict
    hit after the first call instead of a full enemies_data() scan per click.
    """
    gl = group.lower()
    coords: list[dict] = []
    names: list[str] = []
    for mid, m in enemies_data().items():
        ml = mid.lower()
        mn = (m.get("name") or "").lower()
        if "spark" in ml or "boss" in ml:
            continue
        if gl not in ml and gl not in mn:
            continue
        mc = _valid_coords(m.get("coords") or [])
        if mc:
            coords.extend(mc)
            if m.get("name") and m["name"] not in names:
                names.append(m["name"])
    return tuple(coords), tuple(names)


@lru_cache(maxsize=1)
def _dungeon_pois() -> tuple[tuple[dict, ...], tuple[dict, ...]]:
    """(all dungeon/rift entrance POIs, rift-only POIs) with world positions.

    Only 14 POIs total, so the entrance-POI scan stays trivially fast and
    preserves the exact name-matching behavior of the old per-click resolver.
    """
    from ..geo import pois
    d_pois: list[dict] = []
    rifts: list[dict] = []
    for p in pois.load_pois():
        if p.sub_kind not in ("dungeon", "rift"):
            continue
        d_pois.append({"x": p.x, "y": p.y, "name": p.name or "", "activity": p.target_activity or ""})
        if p.sub_kind == "rift" or "rift" in (p.name or "").lower() or "rift" in (p.target_activity or "").lower():
            rifts.append({"x": p.x, "y": p.y})
    return tuple(d_pois), tuple(rifts)


@lru_cache(maxsize=1024)
def resolve_locations(uid: str, dungeon_hint: str | None = None) -> tuple[tuple[dict, ...], str, bool]:
    """Map coordinates, a display title, and rift-ness for a codex unit.

    Pure lookup chain (vendor -> chest -> dungeon entrance -> entrance POI ->
    embedded coords -> mob-drop sources -> species group), cached per (uid,
    hint). Dungeon/rift mobs stop at their entrance; world mobs fall through to
    embedded spawns and drop sources. The returned coords are shared; callers
    must treat them as read-only.

    `dungeon_hint` is the dungeon this occurrence belongs to (the card's
    `dungeon_name`/`type`). A mob shared across several dungeons is emitted by
    the compiler as one entry per dungeon, but `enemies_data()` collapses them
    to a single uid row (last one wins). Re-selecting the matching per-dungeon
    entry here keeps the pin on the dungeon the user actually clicked.
    """
    item = enemies_data().get(uid)
    if not item:
        return (), "", False

    # Dungeon mobs can live in multiple dungeons; each raw_codex Bosses entry
    # carries its own baked entrance. Pick the one matching the clicked card.
    if dungeon_hint and (item.get("is_dungeon") or item.get("is_rift")):
        for e in codex_order().get("Bosses") or []:
            if e.get("id") == uid and (e.get("dungeon_name") == dungeon_hint
                                        or e.get("type") == dungeon_hint):
                item = e
                break

    # todo/unreleased entries have no locations by definition — short-circuit
    # before the fallback chain burns time on steps that can never succeed.
    # Achievement-awarded items still report their source as the title (e.g.
    # 'Achievement · Bestiary of Skover Island') so the UI shows where they
    # come from instead of a blank 'No Map Locations'.
    if (item.get("kind") or "").lower() in _NO_LOC_KINDS:
        ach = item.get("achievement")
        if ach and ach.get("name"):
            return (), f"Achievement · {ach['name']}", bool(item.get("is_rift"))
        return (), "", bool(item.get("is_rift"))

    d_title = ""
    is_rift = bool(item.get("is_rift"))
    coords: list[dict] = []

    # 1. Vendor NPCs (shop items). codex.json emits `vendor_npc` as a LIST of
    # NPC dicts (or a single dict) — normalize both shapes so vendor pins work.
    v_raw = item.get("vendor_npcs") or item.get("vendor_npc") or []
    if isinstance(v_raw, dict):
        v_list = [v_raw]
    elif isinstance(v_raw, list):
        v_list = v_raw
    else:
        v_list = []
    if v_list:
        for v_npc in v_list:
            if isinstance(v_npc, dict) and "x" in v_npc and "y" in v_npc:
                coords.append({"x": v_npc["x"], "y": v_npc["y"]})
        if coords:
            v_name = v_list[0].get("name", "Shop NPC") if isinstance(v_list[0], dict) else "Shop NPC"
            if len(coords) == 1:
                d_title = f"Vendor: {v_name}"
            else:
                # The same NPC often sells from several map spots (codex.json
                # repeats the vendor once per location). Keep the NPC name then;
                # only say "Vendors (N Locations)" when different NPCs are mixed.
                v_names = {v.get("name", "Shop NPC") for v in v_list if isinstance(v, dict)}
                d_title = (f"Vendor: {v_name}" if len(v_names) <= 1
                           else f"Vendors ({len(coords)} Locations)")

    # 2. Chests (world chest / vault drops). `chest_loc` is a dict,
    # `chest_locs` a list — normalize both.
    if not coords:
        c_raw = item.get("chest_locs") or item.get("chest_loc") or []
        if isinstance(c_raw, dict):
            c_list = [c_raw]
        elif isinstance(c_raw, list):
            c_list = c_raw
        else:
            c_list = []
        if c_list:
            for c_loc in c_list:
                if isinstance(c_loc, dict) and "x" in c_loc and "y" in c_loc:
                    coords.append({"x": c_loc["x"], "y": c_loc["y"]})
            if coords:
                c_id = item.get("chest_id") or (c_list[0].get("id") if isinstance(c_list[0], dict) else None) or item.get("chest_name") or "World Chest"
                if c_id in _RIFT_REWARD_LABELS:
                    d_title = _RIFT_REWARD_LABELS[c_id]
                else:
                    d_title = f"Chest: {c_id}" if len(coords) == 1 else f"Chests ({len(coords)} Locations)"

    # 3. Dungeon entrance coords from metadata (baked at compile time)
    if not coords and item.get("dungeon_loc") and isinstance(item["dungeon_loc"], dict):
        d_loc = item["dungeon_loc"]
        if "x" in d_loc and "y" in d_loc:
            coords = [d_loc]
            d_title = item.get("dungeon_name") or "Dungeon Entrance"

    # Dungeon/rift mobs resolve to their entrance — the baked dungeon_loc
    # above, or the entrance POI lookup here. Scattered interior spawns,
    # mob-drop sources, and species groups would otherwise paint dozens of
    # pins for a single dungeon (e.g. rift flyers) or point at the wrong one.
    # dungeons.json membership is the authority — some world mobs (Sparkling
    # variants, patrol dogs) carry a stale is_dungeon flag but are NOT in it.
    from . import dungeons
    is_dung_mob = bool(is_rift or uid in dungeons.dungeon_unit_ids())
    if not coords and is_dung_mob:
        d_info = dungeons.unit_to_dungeon_map().get(uid)
        # The per-dungeon entry (selected by the click hint) is the source of
        # truth for the name — unit_to_dungeon_map is last-wins for shared mobs.
        d_name = item.get("dungeon_name") or item.get("type") or ""
        d_type = d_name
        d_id = (d_info.get("id") if d_info else "") or ""
        d_ig = (d_info.get("ingame_name") if d_info else "") or ""

        if not is_rift:
            is_rift = bool(d_info and (d_info.get("entrance_zone") == "Rifts" or d_info.get("is_rift"))) or \
                      "rift" in d_name.lower() or "rift" in d_type.lower()

        search_keys = [d_name, d_id, d_ig, d_type]
        if "hive" in d_name.lower() or "hive" in d_ig.lower() or "bee" in d_name.lower():
            search_keys.extend(["bee", "hive", "hivetree"])

        d_pois, rift_pois = _dungeon_pois()
        for p in d_pois:
            p_name_clean = (p.get("name") or "").lower().replace(" ", "").replace("_", "")
            p_act_clean = (p.get("activity") or "").lower().replace(" ", "").replace("_", "")
            matched = False
            for k in search_keys:
                if not k: continue
                k_clean = k.lower().replace(" ", "").replace("_", "")
                if k_clean and (k_clean in p_name_clean or k_clean in p_act_clean or p_name_clean in k_clean or p_act_clean in k_clean):
                    coords = [{"x": p["x"], "y": p["y"]}]
                    d_title = d_name or p.get("name") or "Dungeon Entrance"
                    matched = True
                    break
            if matched:
                break

        # Rift mobs: fall back to all general rift-entrance POIs
        if not coords and is_rift and rift_pois:
            coords = [{"x": rp["x"], "y": rp["y"]} for rp in rift_pois]
            d_title = d_name or "Rift Entrance"

        # Entrance lookup failed (no baked loc, no POI match): fall through to
        # the normal chain below so the mob isn't left sourceless.
        is_dung_mob = is_dung_mob and bool(coords)

    # 4. Embedded spawn coords (wild mobs & critters)
    if not coords and not is_dung_mob:
        raw_c = item.get("coords") or item.get("locations") or []
        coords = _valid_coords(raw_c)

    # 5. Mob-drop sources (mob_id / drop_mob / mob_name / drops_from)
    if not coords and not is_dung_mob:
        mob_targets = []
        if item.get("mob_id"): mob_targets.append(item["mob_id"])
        if item.get("drop_mob"): mob_targets.append(item["drop_mob"])
        if item.get("mob_name"): mob_targets.append(item["mob_name"])
        df_list = item.get("drops_from") or []
        if isinstance(df_list, list):
            for df_entry in df_list:
                m_id = df_entry.get("id") if isinstance(df_entry, dict) else df_entry
                if m_id and m_id not in mob_targets:
                    mob_targets.append(m_id)

        if mob_targets:
            for m_target in mob_targets:
                m_info = enemies_data().get(m_target, {})
                if m_info and m_info.get("coords"):
                    m_coords = _valid_coords(m_info["coords"])
                    if m_coords:
                        coords.extend(m_coords)
                        if not d_title:
                            df_name = df_list[0].get("name") if (df_list and isinstance(df_list[0], dict)) else None
                            d_title = f"Mob Drop: {df_name or m_info.get('name', m_target)}"

    # 6. Species-group fallback (mounts/gliders dropping from mob families) —
    # cached per group so this is a dict hit, not a scan.
    if not coords and not is_dung_mob:
        group_name = item.get("group") or ""
        if not group_name and "_" in uid:
            parts = uid.split("_")
            group_name = parts[1] if len(parts) > 1 else parts[0]
        if group_name and len(group_name) >= 3 and not _ZONE_TAG_RE.match(group_name):
            grp_coords, matched = _group_mob_coords(group_name)
            if grp_coords:
                coords.extend(grp_coords)
                m_label = matched[0] if len(matched) == 1 else f"{group_name} Mobs ({len(matched)} Species)"
                d_title = f"Mob Drop: {m_label}"

    return tuple(coords), d_title, is_rift


_PET_SPECIES = ("Rabbit", "Sheep", "Squirrel", "Horse", "Lizard", "Frog",
                "Ladybug", "Turtle", "StinkBug", "Goat")


def _pet_display_type(entry: dict, uid: str) -> str:
    """Display grouping for a pet card: the compiled `group` (species family),
    else the ID prefix (e.g. 'Frog' from 'Frog_Blue'). Keeps the Pets tab
    grouped by species instead of one flat 'Critter' section."""
    grp = entry.get("group")
    if grp:
        return grp
    if "_" in uid:
        prefix = uid.split("_")[0]
        # Strip "Spark" prefix if it's attached to the species (SparkHorse -> Horse)
        if prefix.startswith("Spark") and len(prefix) > 5 and prefix[5:6].isupper():
            prefix = prefix[5:]
        if prefix:
            return prefix
    for species in _PET_SPECIES:
        if species in uid:
            return species
    return entry.get("type") or "Critter"


@lru_cache(maxsize=16)
def units_by_region(region_id: str, ingame_order: bool = True) -> list[dict]:
    """All codex units in a region, with details, sorted by natural game order.

    Cached: the returned entries are shared across calls, so callers must treat
    them as read-only (matching the loot module's pure-data contract).
    """
    from . import dungeons
    e_data = enemies_data()
    comp_data = {r["id"]: r for r in col.items("companions")}
    order_data = codex_order()
    d_mobs = dungeons.dungeon_unit_ids()

    # --- STRICT IN-GAME SEQUENCE ---
    # Build the list from compiled raw_codex data.
    if region_id == "Z0":
        order_entries = []
        for k in ("Z0", "Mounts", "Gliders"):
            val = order_data.get(k)
            if isinstance(val, list):
                order_entries.extend(val)
    else:
        order_entries = order_data.get(region_id)
        if not order_entries and order_data:
            for k, v in order_data.items():
                if k == region_id or k.startswith(f"{region_id} ") or k.startswith(f"{region_id}("):
                    order_entries = v
                    break

    if ingame_order and order_entries:
        if isinstance(order_entries, list) and order_entries and isinstance(order_entries[0], dict):
            out = []
            seen = set()
            for entry in order_entries:
                uid = entry['id']
                if uid in seen: continue
                seen.add(uid)
                info = e_data.get(uid, {})
                item = {
                    **entry,
                    "icon": entry.get("icon") or info.get("icon") or uid,
                    "coords": entry.get("coords") or entry.get("locations") or [],
                    "is_dungeon": entry.get("is_dungeon", uid in d_mobs)
                }
                # Pets group by species (compiled `group` or the ID prefix)
                if region_id == "Pets":
                    item["type"] = _pet_display_type(entry, uid)
                out.append(item)
            return out

        # Legacy string-based order
        # Build a searchable map of EVERYTHING in the catalog
        catalog = {}
        for uid, info in e_data.items():
            nm = info.get("name") or names.unit_name(uid) or ""
            nm_low = nm.lower()
            entry = {
                "id": uid, "name": nm,
                "icon": info.get("icon") or uid,
                "is_boss": units.is_boss(uid), "is_elite": info.get("isElite", False),
                "is_critter": info.get("isCritter", False),
                "drops_spark": units.drops_spark(uid),
                "coords": info.get("coords", []),
                "is_dungeon": uid in d_mobs
            }
            if nm_low: catalog[nm_low] = entry
            catalog[uid.lower()] = entry

        for uid, cinfo in comp_data.items():
            nm = cinfo.get("name") or names.unit_name(uid) or ""
            nm_low = nm.lower()
            entry = {
                "id": uid, "name": nm,
                "icon": cinfo.get("icon") or uid,
                "is_boss": units.is_boss(uid), "is_elite": False,
                "is_critter": True,
                "drops_spark": units.drops_spark(uid),
                "coords": cinfo.get("coords", []),
                "is_dungeon": uid in d_mobs
            }
            if nm_low: catalog[nm_low] = entry
            catalog[uid.lower()] = entry

        out = []
        seen = set()
        for target in order_entries:
            t_low = target.lower()
            match = None
            if t_low in catalog:
                match = catalog[t_low]
            else:
                # Fuzzy fallback: does any key contain the target string?
                for k, v in catalog.items():
                    if t_low in k:
                        match = v
                        break

            if match and match["id"] not in seen:
                out.append(match)
                seen.add(match["id"])

        return out

    # --- STANDARD LOGIC (ONLY IF INGAME_ORDER IS OFF OR SPECIAL TAB) ---
    if region_id == "Z0":
        # Automatic catch-all for anything not classified in the main regions + Mounts & Gliders
        out = []
        seen_names = set()
        
        # Add Mounts & Gliders to Others
        for cat in ("mounts", "gliders"):
            for r in col.items(cat):
                nm = r.get("name") or r["id"]
                if nm and nm.lower() not in seen_names:
                    seen_names.add(nm.lower())
                    out.append({
                        "id": r["id"],
                        "name": nm,
                        "type": cat.capitalize(),
                        "icon": r.get("icon") or r["id"],
                        "is_boss": False,
                        "is_elite": False,
                        "is_critter": True,
                        "coords": [],
                        "is_dungeon": False
                    })

        u_regions = unit_regions()
        all_main_ids = set()
        for rid in ("Z1", "Z2", "Z3", "Bosses", "Pets"):
            region_entries = order_data.get(rid, [])
            if isinstance(region_entries, dict):
                for tp_list in region_entries.values():
                    for entry in tp_list: all_main_ids.add(entry["id"])
            else:
                for entry in region_entries:
                    if isinstance(entry, dict): all_main_ids.add(entry["id"])

        for uid, rid in u_regions.items():
            if rid == "Z0" and uid not in all_main_ids:
                info = e_data.get(uid, {})
                nm = info.get("name") or names.unit_name(uid)
                if not nm or nm.lower() in seen_names: continue
                seen_names.add(nm.lower())
                out.append({
                    "id": uid, "name": nm,
                    "type": info.get("type") or "Misc",
                    "icon": info.get("icon") or uid,
                    "is_boss": units.is_boss(uid),
                    "is_elite": info.get("isElite", False),
                    "is_critter": info.get("isCritter", False),
                    "coords": info.get("coords", []),
                    "is_dungeon": uid in d_mobs
                })
        return sorted(out, key=lambda x: (x.get("type") or "", x["name"].lower()))

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
                nm = d.get("boss_name") or info.get("name") or names.unit_name(bid)
                out.append({
                    "id": bid,
                    "name": nm,
                    "type": dname,
                    "is_boss": True,
                    "is_elite": False,
                    "is_critter": False,
                    "level": d.get("level", 99),
                    "is_rift": d.get("entrance_zone") == "Rifts",
                    "drops_spark": units.drops_spark(bid),
                    "coords": info.get("coords", []),
                    "is_dungeon": True
                })
            
            # Mobs
            for mid in d.get("mobs", []):
                info = e_data.get(mid, {})
                nm = info.get("name") or names.unit_name(mid)
                out.append({
                    "id": mid,
                    "name": nm,
                    "type": dname,
                    "is_boss": False,
                    "is_elite": info.get("isElite", False),
                    "is_critter": False,
                    "level": d.get("level", 99),
                    "is_rift": d.get("entrance_zone") == "Rifts",
                    "drops_spark": units.drops_spark(mid),
                    "coords": info.get("coords", []),
                    "is_dungeon": True
                })
        return out

    if region_id in ("Mounts", "Gliders"):
        cat_key = region_id.lower()
        items = col.items(cat_key)
        out = []
        for r in items:
            out.append({
                "id": r["id"],
                "name": r.get("name") or r["id"],
                "type": region_id,
                "icon": r.get("icon") or r["id"],
                "is_boss": False,
                "is_elite": False,
                "is_critter": True,
                "coords": [],
                "is_dungeon": False
            })
        return sorted(out, key=lambda x: x["name"].lower())

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
                "is_critter": region_id == "Pets" or info.get("isCritter", False),
                "drops_spark": units.drops_spark(uid),
                "coords": info.get("coords", []),
                "is_dungeon": uid in d_mobs
            })
            
    # Sort primarily by type for pets and bosses, otherwise by name
    if region_id in ("Pets", "Bosses"):
        # Group by type (species/dungeon), then boss first, then alphabetical by name
        return sorted(out, key=lambda x: (x["type"] or "", not x.get("is_boss", False), x["name"].lower()))

    # Sort primarily by name, secondarily by ID
    return sorted(out, key=lambda x: (x["name"].lower(), x["id"]))
