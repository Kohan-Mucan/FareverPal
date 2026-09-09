"""Drop sources: where an item comes from (the Drops From list).

Resolves the raw `drops[]` rows against the `_sources` / `_locs` / `_tables`
indexes into readable rows, flags the rift/dungeon locations that are the
"real info", and groups the loot tables that drop gear (the gear tab's
loot-table filter).
"""
# Values below marked `# HAND-FACT: <name>` are recorded BY HAND — game facts
# that no sheet or compiled shim carries (a vendor's price, a box's prose, a
# pool size the game never exposes). tests/test_repo_hygiene.py registers each
# with the contract test that pins it, so an unguarded one is visible instead of
# silently trusted. Add the marker when you record one.
from __future__ import annotations

import copy
import json
import re
from collections import Counter
from functools import lru_cache

from ... import paths
from ...rules import (GUILD_MERCHANT_RE, crafted_item_ids,
                      heroic_boss_items, recipe_output_ids_from_payload,
                      shop_item_ids_from_payload)
from .. import names, raw_data
from .catalog import RARITY_ORDER, _data, _sources, _tables, _locs
from .labels import _GEAR_TYPES, category

# Location labels that are the "real info" the item page surfaces first: rift
# bosses, soulstone events and the endgame dungeons. Everything else is a
# world zone / hub / unknown.
_RIFT_LOC_RE = re.compile(r"(rift|soulstone|invader)", re.IGNORECASE)
_DUNGEON_LOC_RE = re.compile(
    r"\((?:temple|forbidden library|alkatram|cathedral|prison)", re.IGNORECASE)

# internal ids / coordinates buried in the raw location strings — stripped by
# human_loc so the item page shows only the readable names
_COORD_RE = re.compile(r"\(\s*-?\d+(?:\.\d+)?\s*,\s*-?\d+(?:\.\d+)?\s*\)")
_ZONE_ID_RE = re.compile(r"\b(?:Z\d|W\d)_[A-Za-z0-9_]+")

_KIND_LABEL = {
    "unit": "MOB",
    "chest": "CHEST",
    "cache": "BOX",
    "npc": "VENDOR",
    "gatherable": "GATHER",
    "achievement": "ACHIEVEMENT",
    "worldloot_affinity": "WORLD GEAR",
    "worldrecipe": "WORLD RECIPE",
}

_KIND_GROUP_LABEL = {
    "unit": "MOBS",
    "chest": "CHESTS",
    "cache": "BOXES",
    "npc": "VENDORS",
    "gatherable": "GATHER",
    "achievement": "ACHIEVEMENTS",
    "worldloot_affinity": "WORLD GEAR",
    "worldrecipe": "WORLD RECIPES",
}

_NODE_SIZE_SUFFIXES = ("_Large", "_Small", "_Medium")
_CHEST_COUNT_RE = re.compile(r"\s*\(x\d+\)\s*$")
_CHEST_GROUP_IDS = re.compile(r"(?i)(Crate|Activity|BonusChest|Bosschest|Tier\d+)$")
_CHEST_SINGLE_RE = re.compile(r"(?i)(WorldChest|VaultChest|FightStone|ChestOrb)")


@lru_cache(maxsize=1024)
def chest_zone_name(chest_id: str) -> str | None:
    """The readable zone name a chest sits in via its world position — None when it can't be resolved."""
    try:
        from ...geo import zones as geo_zones
        from .. import dungeons
        zid = geo_zones.chest_zone(chest_id)
        if zid and dungeons.zone_tag_from_zone_id(zid):
            return names.zone_name(zid)
    except Exception:
        pass
    return None


def chest_source_label(d: dict) -> str:
    """Human label for a chest drop source: strips loot table roll numbers (xN)
    and formats single chests with zone name."""
    nm = d.get("source", "")
    sid = d.get("source_id") or ""
    m = _CHEST_COUNT_RE.search(nm)
    if m:
        nm = nm[: m.start()].strip()
    if sid and _CHEST_SINGLE_RE.search(sid):
        label = names.poi_label(sid)
        if label:
            zone = chest_zone_name(sid)
            return f"{zone} · {label}" if zone else label
    return nm


_MAX_SHOWN_DROPS = 14      # cap per Drops From table ("+N more" beyond this)


# --- dungeon-set sources -----------------------------------------------
# The game dump has no per-item loot tables for the per-faction dungeon
# gear sets (Shoulders_RManfish_FigAss, Trinket_Kobold,
# Back_RBee_FigWiz_Craft, ...): the bosses' loot tables only list their
# signature weapons, so scan_item_drops records no drops rows for the set
# pieces and the Drops From list would report "no drop sources". The game
# distributes them at RUNTIME through the 'WorldLoot' token instead:
# faction crates (ManfishCrate/KoboldCrate/BeeCrate/CrimsonCrate ->
# WorldCrate) roll WorldLoot at 0.35, and Manfish/Kobold/Crimson faction
# mobs roll it at 0.01 (solo) / 0.05 (party). The token then picks one
# piece from the dropper's pool — the faction's R-set plus its signature
# weapons (pool sizes from the game's token expansion: Manfish 28, Kobold
# 28, Bee 30, Crimson 29) or the craft-variant pool (16). 'UpgradeRare'
# (pool 157) is rolled at 1.0 by upgrade activities / dungeon crates.
# A piece's chance from each source = the source's WorldLoot chance x
# 1/pool, so the derived rows carry REAL chances. The faction bosses'
# set rows (Reblochonk/Nepsilon/...) reference the faction material
# tables, which hold no gear — the item page shows no chance number
# there (it headlines the boss with its icon + name instead). _Craft
# variants are the same gear (the suffix only
# marks that a recipe exists — there is no separate base id for those
# pieces), and pieces whose faction field is 'Craft' sit in the craft
# pool, so they derive too. The Demon set is NOT derived: its pieces are
# recorded in the scan (Mira the Demon Huntress sells them via the Rift
# gear cache).
_DUNGEON_SET_RE = re.compile(
    r"(?:^|_)(?:R|Trinket_)(Manfish|Kobold|Bee|Crimson)(?:_|$)",
    re.IGNORECASE)
# pool sizes (the WorldLoot token's per-faction pools, from the game's
# token expansion) — the piece is one of N in its pool
# HAND-FACT: _DUNGEON_POOL_SIZES
_DUNGEON_POOL_SIZES = {"Manfish": 28, "Kobold": 28, "Bee": 30,
                       "Crimson": 29}
# HAND-FACT: _CRAFT_POOL_SIZE
_CRAFT_POOL_SIZE = 16          # the craft-variant pool spans all 4 factions
# HAND-FACT: _UPGRADE_RARE_POOL_SIZE
_UPGRADE_RARE_POOL_SIZE = 157  # UpgradeRare token pool (upgrade activities)
# the crates that roll the faction pools — World/Demon crates roll their
# own pools (the shop weapons / the Demon set), not the faction gear
_FACTION_CRATE_IDS = ("ManfishCrate", "KoboldCrate", "BeeCrate",
                      "CrimsonCrate")
# the faction loot tables that roll WorldLoot (the Bee table doesn't)
_FACTION_MOB_TABLES = ("Manfish", "Kobold", "Crimson")


# 'unknown location' rows — the scan couldn't attribute a location to these
# mob sources (they're instanced / summoned / variant units without a loot
# location). The app's own location data knows where they live, so the row's
# loc is resolved here instead of staying 'unknown': dungeon mobs -> their
# dungeon (dungeons.json), soulstone demon bosses -> their summon spot
# (poi_locs), world mobs -> their spawn zones (mob_locs). Both loc datasets
# are read from the compiled raw_data shim first (the frozen build has no
# loose JSONs), falling back to the JSON files in dev checkouts with a stale
# shim. Only unit sources are tried — chests/gatherables have no spawn
# registry.
_UNKNOWN_LOC = "unknown location"


def _unwrap_rows(rows) -> list | None:
    """The shims' location payloads may be a bare row list (old compile) or a
    dict wrapper ({'pois': [...]} / {'mobs': [...]} — the current compiler
    stamps one). None when nothing usable is there."""
    if isinstance(rows, list):
        return rows
    if isinstance(rows, dict):
        for v in rows.values():
            if isinstance(v, list) and v:
                return v
    return None


@lru_cache(maxsize=1)
def _poi_rows() -> tuple[dict, ...]:
    """POI rows (soulstones, dungeons, ...): the compiled raw_locs / raw_data shim
    first, then the loose JSON (stale shim / dev checkout).

    Cached like its siblings (`_mob_loc_rows`, `_soulstone_pois`): the resolver
    chain reads it once per process, and callers that swap the underlying data
    (tests dead-ending the JSON paths) must be able to `cache_clear()` it —
    which `_soulstone_pois` depends on, since it delegates here."""
    try:
        from .. import raw_locs
        rows = _unwrap_rows(raw_locs.DATA.get("poi_locs"))
    except Exception:
        rows = None
    if not rows:
        rows = _unwrap_rows(raw_data.DATA.get("poi_locs"))
    if not rows:
        try:
            data = json.loads(paths.poi_locs_path().read_text(encoding="utf-8"))
            rows = data.get("pois") if isinstance(data, dict) else data
        except (OSError, ValueError):
            return ()
    return tuple(r for r in (rows or ()) if isinstance(r, dict))


@lru_cache(maxsize=1)
def _mob_loc_rows() -> tuple[dict, ...]:
    """mob_locs rows (world spawns): the compiled raw_locs / raw_data shim first, then
    the loose JSON (stale shim / dev checkout)."""
    try:
        from .. import raw_locs
        rows = _unwrap_rows(raw_locs.DATA.get("mob_locs"))
    except Exception:
        rows = None
    if not rows:
        rows = _unwrap_rows(raw_data.DATA.get("mob_locs"))
    if not rows:
        try:
            data = json.loads(paths.mob_locs_path().read_text(encoding="utf-8"))
            rows = data.get("mobs") if isinstance(data, dict) else data
        except (OSError, ValueError):
            return ()
    return tuple(r for r in (rows or ()) if isinstance(r, dict))


@lru_cache(maxsize=1)
def _soulstone_pois() -> tuple[dict, ...]:
    """The soulstone POI rows (the 8 demon-boss summon spots), by zone."""
    return tuple(p for p in _poi_rows() if p.get("sub_kind") == "soulstone")


# The Guild Merchants (WanderingMerchant NPCs in the town hubs) sell the
# town weapons — Credence / Glory / Radiance / the Grimoire / Judgement —
# and the starter gear — scaled to each town's item level (Tyrna sells L20,
# Lower Ramburg L25). They're SHOP gear, not crafted pieces:
# item_fixed_level uses this regex to tell those `_Craft` ids apart from the
# genuinely crafted `_Craft` pieces, and the item page shows the per-town
# level next to each hub. Re-exported from the shared rule module, which the
# compiler's guild-merchant rewrite also reads.
_GUILD_MERCHANT_RE = GUILD_MERCHANT_RE

# The town hubs' item levels, per vendor source: each Guild Merchant town
# sells its shop stock scaled to the town's item level, so a vendor row can
# show the level next to the town name (Tyrna · L20, Lower Ramburg · L25).
# The Primeval Valley / Meridion merchants sell no scanned items yet, so
# they have no stamped level.
# HAND-FACT: _TOWN_ITEM_LEVELS
_TOWN_ITEM_LEVELS = {
    "WanderingMerchant_Npc_Azuram": 20,     # Tyrna
    "WanderingMerchant_Npc_Crimson": 25,    # Lower Ramburg
}


@lru_cache(maxsize=1)
def _unit_levels() -> dict[str, int]:
    """Unit id -> level, from the compiled units sheet (a mob/boss drop
    source's own level)."""
    try:
        from .. import cdb
        return {r.get("id"): r.get("lvl") for r in cdb.lines("units")
                if isinstance(r.get("lvl"), int)}
    except Exception:
        return {}


@lru_cache(maxsize=1)
def _mob_spawn_zones() -> dict[str, tuple[str, ...]]:
    """unit id -> its spawn zone ids, from mob_locs (world spawns)."""
    out: dict[str, set] = {}
    for m in _mob_loc_rows():
        zone = m.get("zone")
        if not zone:
            continue
        for u in m.get("units") or [m.get("unit")]:
            if u:
                out.setdefault(u, set()).add(zone)
    return {u: tuple(sorted(z)) for u, z in out.items()}


@lru_cache(maxsize=1)
def _mob_spawn_zones_normalized() -> dict[str, tuple[str, ...]]:
    """Variant-stripped unit id -> spawn zones, from mob_locs.

    The drop scan references numbered/FS variants ('Manfish_Z1W_Claws_2',
    'Skunk_Z1W_FS_2') while mob_locs keys the base id ('Manfish_Z1W_Claws',
    'Skunk_Z1W_FS'). Stripping a trailing _<digits> from both sides and
    requiring exact equality keeps this safe — only true variants merge."""
    out: dict[str, set] = {}
    for u, zones in _mob_spawn_zones().items():
        n = re.sub(r"_\d+$", "", u)
        out.setdefault(n, set()).update(zones)
    return {n: tuple(sorted(z)) for n, z in out.items()}


@lru_cache(maxsize=4096)
def _resolve_unknown_loc(source_id: str, source_name: str) -> str | None:
    """Human-readable location for a mob source the scan couldn't place, or
    None when nothing is known. Resolution chain: dungeon membership
    (dungeons.json) -> soulstone summon spot (poi_locs) -> exact world
    spawns (mob_locs)."""
    if not source_id:
        return None
    from .. import dungeons
    from .. import names
    # 1. dungeon mobs / bosses -> the dungeon that owns them. Rifts show as
    # the arena (the scan's own rift loc, flagged rift so they sort first).
    d = dungeons.unit_to_dungeon_map().get(source_id)
    if d:
        if (d.get("entrance_zone") == "Rifts" or d.get("is_rift")
                or (d.get("ingame_name") or "").lower() in ("maat",
                                                              "shaarlize")):
            return "Rift (arena entrance)"
        ig = d.get("ingame_name") or d.get("name") or ""
        ez = d.get("entrance_zone") or ""
        zname = names.zone_name(ez) if ez else None
        # humanize fallbacks ('Z 2 Crimson Island Barracks') are noise —
        # keep just the dungeon name then
        if zname and re.match(r"^[ZW]\s?\d", zname):
            zname = None
        if ig and zname and zname != ig:
            return f"{ig} · {zname}"
        return ig or zname or None
    # 2. soulstone demon bosses -> their summon spot's zone
    for p in _soulstone_pois():
        if p.get("spawn_unit") == source_id or p.get("name") == source_name:
            return names.zone_name(p.get("zone")) or None
    # 3. world mobs -> their spawn zones (mob_locs), exact id first,
    # then variant-stripped ids (numbered/FS drop variants share the base
    # spawner's zones)
    zones = _mob_spawn_zones().get(source_id)
    if not zones:
        zones = _mob_spawn_zones_normalized().get(
            re.sub(r"_\d+$", "", source_id))
    if zones:
        znames = [names.zone_name(z) for z in zones]
        znames = [z for z in znames if z]
        if znames:
            return " · ".join(dict.fromkeys(znames))
    return None


# The classes' starting armor (Squire's Brigandine, Apprentice's Tunic, ...)
# and the base clothes have no drop rows — the game gives them out at
# character creation. The unit.json class records (Warrior/Rogue/Mage/Priest)
# list the full loadout in `parts.gear`: the starter armor, the class glider
# and the gather tools every class starts with. See no_source_reason.
_STARTER_RE = re.compile(r"_Starter_", re.IGNORECASE)
# HAND-FACT: _BASE_CLOTHES_ID
_BASE_CLOTHES_ID = "Chest_C_BaseClothes"
# HAND-FACT: _CLASS_GLIDERS
_CLASS_GLIDERS = {
    "Glider_Owl_Brown": "Fighter",
    "Glider_Owl_Grey": "Assassin",
    "Glider_Raccoon_Grey": "Wizard",
    "Glider_Raccoon_Orange": "Cleric",
}
# HAND-FACT: _STARTER_TOOLS
_STARTER_TOOLS = ("Sickle", "Pickaxe")

# Items that DO sit in loot tables (lootTable.json) but whose tables aren't
# anchored by the scan (no scanned mob/chest owns them — rift rewards,
# craft-point nodes, starter tables, ...), so they report no drops. The
# reason names the actual table instead of claiming a generator token.
# Gear whose id is the unsourced BASE row of a family: it is referenced by
# no loot table, recipe or props block, so it never drops — the game hands
# out the family's zone and crafted variants instead. Saying so beats the
# bare 'No drop sources found.' line for a piece the sheet ships.
_BASE_RING_IDS = ("Finger_Ap", "Finger_Cri", "Finger_Fer", "Finger_Mp")

# HAND-FACT: _LOOT_LISTED_UNANCHORED
_LOOT_LISTED_UNANCHORED = {
    "Soulstone_Z1_4": "the Rift tier-4 table (Rift_Tier4)",
    "Soulstone_Z2_4": "the Rift tier-4 table (Rift_Tier4)",
    "UpgradeRare": "the dungeon crates and the Upgrade Items activity "
                    "reward (DungeonCrate / R1ActivityUpgradeReward)",
    "CraftPoint": "the craft-point tables (BigCP_Z1 / SmallCP1 / Blacksmith_1)",
    "Hammer": "the Blacksmith starter table (Blacksmith_Starter)",
    "Particle_Z1": "the Scrap tables (Scrap / Scrap_Rare)",
    "SpiritHeart_Z2": "the Spirits table",
    "CoyoteMeat": "the Coyotes table",
}


@lru_cache(maxsize=None)
def acquisition_note(item_id: str) -> str | None:
    """The acquisition note the scan stamps on cache-obtained gear.

    The Rift Demon set, the Demon weapons and the Corrupted Gifts are opened
    from Mira's Chaotic Gear/Weapon/Gift caches — the scan records the
    vendor row (Mira sells the cache at 3 hubs) AND stamps this note
    ('Chaotic Gear Cache — player-scale chest (1-25).'). The player-scale
    chest rolls ONE piece at your level — the cache's own flavor text says
    'a Nightling piece/weapon for your level', and the sheet's
    gainItem.maxItems=2 is a UI hint, not the count — so the note names the
    chest and its level range instead of spelling out the count. The item
    page surfaces it in Drops From so the actual acquisition — a
    PLAYER-SCALE chest that rolls at your level — is visible, not just the
    vendor who sells the cache. None for everything else."""
    if not item_id:
        return None
    note = (_data().get("items", {}).get(item_id) or {}).get("note") or None
    if note and note.startswith("WorldLootWithAffinity"):
        # The zone gear's Drops From list names its own sources now (Zone
        # activities / Unique Foes / the faction mob unit groups — see
        # shown_drops), so the token's verbose mechanic note is redundant:
        # the rows are minimal on purpose, with no tooltip detail either.
        return None
    if (_data().get("items", {}).get(item_id) or {}) \
            .get("type") == "InfusionPattern":
        # the 2026 Infusion System: the pattern itself carries no scanned
        # note, but the mechanic is exactly what the Drops From note slot
        # exists for — one guaranteed pattern per heroic kill, consumed at
        # the crucible in the social hubs
        return ("One guaranteed pattern per Heroic kill — applied to "
                "heroic gear at the Infusion crucible in the social hubs.")
    return note


# The Medal-of-Glory vendor is Shiro James. unit.json carries him as
# `MedalogGloryTrader` — display name "Shiro James", with his own trader
# model and portrait. `TODO_MOG_Merchant`, the "Glory Merchant" placeholder on
# the generic BaseNPC_01, is a SEPARATE unit and is not him. An earlier note
# here claimed the 2026 patch removed both and that Shiro now sells the Hero
# caches; unit.json contradicts that, and the placeholders are still there.
# The Glory Merchant scan attributes those two boxes to the id-shaped FName
# `Glory_Merchant`, a placed-NPC id that matches no unit row —
# _HEROIC_CACHE_VENDORS says why the seller keeps the "Shiro James" name.
# Match on his NAME as well as the id, because the scan writes the
# npc_sources `name` from the unit's display name while the id may not
# carry it.
_MOG_VENDOR_KEY = "shirojames"


def is_mog_source(row_or_id) -> bool:
    """True for the Medal-of-Glory vendor (Shiro James). Accepts a source /
    npc_sources row (dict) or a bare id/name string; case and punctuation
    are ignored, so "Shiro James", "Shiro_James" and any id containing
    'ShiroJames' match (but not the pirate unit 'ShiroNext_Pirate')."""
    if isinstance(row_or_id, dict):
        parts = (row_or_id.get("id"), row_or_id.get("name"))
    else:
        parts = (row_or_id,)
    for p in parts:
        flat = re.sub(r"[^a-z0-9]", "", str(p or "").lower())
        if _MOG_VENDOR_KEY in flat:
            return True
    return False


def _mog_merchant_towns() -> tuple[str, ...]:
    """The towns Shiro James stands in, from the scan's npc_sources (the
    vendor rows — one per social hub); their zone ids resolve through
    names.zone_name. Progression order (Z1 -> Z3), deduped; empty when the
    scan predates the patch (no vendor captured).

    Kept for the vendor's own location only. It is deliberately NOT used to
    describe where the CACHES come from: a cache is a heroic BOSS drop, and
    listing hub towns for it sent the reader to the wrong place."""
    rows = [r for r in (_data().get("npc_sources") or [])
            if is_mog_source(r)]
    towns: dict[str, str] = {}
    for r in sorted(rows, key=lambda r: r.get("zone") or ""):
        town = names.zone_name(r.get("zone") or "")
        if town:
            towns[r.get("zone") or ""] = town
    return tuple(towns.values())


# The Heroic / Hardmode caches and the loot tables they open into. The cache
# ITEMS carry no drops[] rows of their own (the scan never captured the boss
# -> cache edge), but the sheet's `HM_*` loot tables hold the real contents,
# so the box is read from there instead of being described in prose.
#
# Which box each cache opens into, and the few facts the runtime cannot read
# back. The link itself comes from the source sheet's
# `props.gainItem.lootTable`, but the compiler PRUNES `props`, so the level
# range and `max_items` below have to be maintained here by hand (verified
# against assets/data/item.json on 2026-09-26). The compiler ALSO bakes the
# whole gainItem onto each container's drops row as `gain_item` (see
# compiled_container_facts below), so the hand facts are no longer unpinned:
# the cache contract tests in tests/test_item_drops.py check `table` /
# `level_min` / `level_max` / `max_items` against that compiled copy, which is
# why they no longer need the loose sheet and now run in CI. `rarity` is NOT
# here — the container's own `rarity` field survives compilation, so it is read
# live. The roll's rarity SPAN is not here either: the compiler bakes a
# gainItem clamp onto the drops row as `roll_rarity_min`/`roll_rarity_max`, and
# container_roll_span() reads it live (see below).
#
#   HM_Gear_Cache_25   "Hero Gear Cache"    -> HM_Items  (24 Epic, fixed 25)
#   HM_Weapon_Cache_25 "Hero Weapon Cache"   -> WorldWeaponWithAffinity
#   HM_Demon_Cache_25  "Chaotic Gear Cache"  -> HM_DemonGear (25 Epic, fixed 25)
#   Rift_Gear_Cache    "Chaotic Gear Cache"  -> Rift_GearWithAffinity
#   Rift_Weapon_Cache  "Chaotic Weapon Cache"-> Rift_WeaponWithAffinity
#   Rift_Gift_Cache    "Chaotic Gift Cache"  -> Demon_Leg_Upgrade
#
# WATCH THE NAME COLLISION: `Rift_Gear_Cache` and `HM_Demon_Cache_25` are both
# named "Chaotic Gear Cache" in game, and they are nothing alike — one is the
# RARE player-scale box Mira sells for rift currency (what you have been
# opening), the other the EPIC fixed-25 set. Nothing may resolve a box by
# display name; always by item id.
#
# `player_scale` marks a box that rolls at YOUR level (level_min < level_max
# with no fixed value), so a row's level is not knowable until it drops.
#
# `template` marks a table whose row NAMES are base templates rather than the
# pieces you actually receive. `WorldWeaponWithAffinity` is the only one: its
# four rows are the level-4 RARE craft-starter weapons (Glory, Dominion,
# Radiance, Credence), but the cache that points at it advertises "Contains an
# Epic world weapon level 25" — the weapon is rolled from the template at the
# box's level and rarity, so neither the template's Rare/4 nor its name is what
# you get. Without this flag the box renders as a Rare box, which is the exact
# misreading the rest of this module exists to prevent.
#
# `via` is prose naming WHO hands the box over, and it is OPTIONAL: it is only
# meaningful for a box that actually has a seller, and a hand-written string
# cannot be kept true as the scan grows. Where we have a RECORDED seller it
# lives in _HEROIC_CACHE_VENDORS below and the prose is derived from it, so
# there is exactly one fact to keep true. The one `HM_` box with no recorded
# seller carries no `via` at all: `HM_Demon_Cache_25` says UNRELEASED, which
# is what the data supports, and it clears itself the moment a scan records a
# source. The three Rift boxes keep theirs because Mira really does sell them
# for Nightblood — which `cache_vendor()` independently recovers from the
# sheet, so the string is a label, not the only evidence.
#
# THE TWO FACTS A SHOP CAPTURE CANNOT RECOVER. Keyed by item id, because the
# shop UI does not use the item ids: the Medal of Glory trader's stock list
# names them "HeroGearCache" and "HeroWeaponCache", which are the DISPLAY ids
# of `HM_Gear_Cache_25` and `HM_Weapon_Cache_25` — the same two boxes, under
# the names the sheet drops the HM_ prefix from.
#
# This started as a full hand-written record, added 2026-09-26 by dumping the
# open shop: a `ui.win.MerchantUI` over an `ent.interactible.Npc` with exactly
# two rows, "Hero Gear Cache" x1 @ 100 and "Hero Weapon Cache" x1 @ 100,
# category General, iLevel 200, budgetScale 0.6551, scaledPrice 66. The
# Glory Merchant scan has since recorded that SAME shop row for row, so
# everything it could prove is now read back from the scan by
# `_recorded_cache_vendor` and only the two leftovers below stay here.
#
# They matter because the item sheet records NEITHER box as a drop — the
# whole `HM_` family has `drops: []` — and the Hero Weapon Cache's loot table
# holds the level-4 RARE craft starters, whose own Guild Merchant rows
# describe the base weapon rather than the box. So nothing in the item data
# points at a seller, and reading it said "unreleased" for a box sitting in a
# vendor's stock at 100 medals.
#
# `currency` — the shop charges through a CurrencyCounter the scanner never
# read, so the scan's `price` is a bare number. THAT 100 is provable; that
# those 100 are medals is not. Reading it as Nightblood or Demonic Souls
# would be precisely the invention this module exists to prevent, so the
# currency is named here and the amount is taken from the scan.
#
# `vendor` — the scan records an id-shaped FName read off the live NPC,
# `Glory_Merchant`, which matches no unit in unit.json. That sheet holds two
# candidates: `MedalogGloryTrader`, display name "Shiro James", with its own
# trader model and portrait, and `TODO_MOG_Merchant`, display name "Glory
# Merchant", a placeholder on the generic BaseNPC_01. Humanising the scan's id
# would name the seller after the TODO stub, so the real trader keeps the
# name — an independent memory scan of the 2026-09-26 session found the FName
# `MedalogGloryTrader` sitting immediately beside both `HM_*_Cache_25` ids.
# This is the one string to revisit if a scan ever records a display name.
# HAND-FACT: _HEROIC_CACHE_VENDORS
_HEROIC_CACHE_VENDORS = {
    # `price` is the ONE hand fact left: the Hero caches have `drops: []` and
    # no Shop row in the index, so nothing derivable points at a seller. The
    # old MerchantUI sheet supplied the 100-medal amount; with the sheet gone
    # it is recorded here, and `_recorded_cache_vendor` reports no seller when
    # it is missing rather than inventing one.
    "HM_Gear_Cache_25": {"vendor": "Shiro James, the Medal of Glory vendor",
                         "currency": "BadgeOfGlory", "price": 100},
    "HM_Weapon_Cache_25": {"vendor": "Shiro James, the Medal of Glory vendor",
                           "currency": "BadgeOfGlory", "price": 100},
}


# HAND-FACT: _HEROIC_CACHE_SPECS
_HEROIC_CACHE_SPECS = {
    # no `via` on the `HM_` boxes: Shiro's two are derived from
    # _HEROIC_CACHE_VENDORS, and the third says UNRELEASED.
    "HM_Gear_Cache_25": {
        "table": "HM_Items", "level_min": 25, "level_max": 25,
        "max_items": 2, "player_scale": False, "template": False},
    # The ONE box whose roll is clamped rather than fixed: its source sheet
    # carries `props.gainItem.rarity = {min: "Epic"}` (re-read 2026-10-05) — a
    # FLOOR, not a rarity, so an open hands out Epic OR any rung above it (the
    # in-game Choose a Reward screen showed an Epic "Glory" beside a Legendary
    # "Dominion" from one open). Every other box has no `rarity` on its
    # gainItem and hands out the container's own rarity. The clamp is NOT
    # recorded here: the compiler bakes it onto this container's drops row as
    # `roll_rarity_min`, and container_roll_span() reads the span from there, so
    # a patch that adds or removes a clamp is picked up without editing this
    # table.
    "HM_Weapon_Cache_25": {
        "table": "WorldWeaponWithAffinity", "level_min": 25, "level_max": 25,
        "max_items": 2, "player_scale": False, "template": True},
    "HM_Demon_Cache_25": {
        "table": "HM_DemonGear", "level_min": 25, "level_max": 25,
        "max_items": 2, "player_scale": False, "template": False},
    "Rift_Gear_Cache": {
        "table": "Rift_GearWithAffinity", "level_min": 1, "level_max": 25,
        "max_items": 2, "player_scale": True, "template": False,
        "via": "Mira the Demon Huntress, for Rift currency"},
    "Rift_Weapon_Cache": {
        "table": "Rift_WeaponWithAffinity", "level_min": 1, "level_max": 25,
        "max_items": 2, "player_scale": True, "template": False,
        "via": "Mira the Demon Huntress, for Rift currency"},
    "Rift_Gift_Cache": {
        "table": "Demon_Leg_Upgrade", "level_min": None, "level_max": None,
        "max_items": 1, "player_scale": False, "template": False,
        "via": "Mira the Demon Huntress, for Rift currency"},
}

# cache id -> its loot table (the one fact every caller needs)
_HEROIC_CACHE_TABLES = {cid: s["table"] for cid, s in _HEROIC_CACHE_SPECS.items()}

# Boxes whose SHEET name collides with another box's, given a name to be
# listed under instead. This is a DISPLAY disambiguator, never a lookup key:
# `Rift_Gear_Cache` and `HM_Demon_Cache_25` are both "Chaotic Gear Cache" in
# game, and on Mira's vendor line both sit side by side, so the two chips
# read as the same box — different tier, different level model, different
# contents, same words.
#
# The qualifier is ours, not the game's. The game still calls it "Chaotic
# Gear Cache", which is what its own item page shows; this name is for LISTS,
# where telling two boxes apart is the whole job. The other two `HM_` boxes
# need nothing: "Hero Gear Cache" and "Hero Weapon Cache" are already unique,
# and following their "Hero" prefix is what makes the Nightling's read as the
# Heroic member of its own family rather than a second rarity of the Rare one.
# HAND-FACT: _CACHE_DISPLAY_NAME
_CACHE_DISPLAY_NAME = {
    "HM_Demon_Cache_25": "Heroic Chaotic Gear Cache",
}


@lru_cache(maxsize=None)
def cache_display_name(cache_id: str) -> str:
    """The name to LIST this box under — the sheet's own, unless that name is
    shared with a different box, in which case the disambiguated one.

    Reads the GENERIC container spec (`container_spec`), the same facts the
    Items page renders from, so a box the hand table never claimed is listed
    under its own sheet name rather than its raw id. Always resolve a box by
    ITEM ID; this is text for a human to read.
    """
    spec = container_spec().get(cache_id) or {}
    return _CACHE_DISPLAY_NAME.get(cache_id) or spec.get("name") or cache_id


def container_roll_span(row: dict) -> tuple[str, str]:
    """The rarity span an open of the container `row` can hand out, as
    (floor, ceiling).

    A container's `props.gainItem.rarity` clamps the roll; the compiler bakes
    its `min`/`max` onto the drops row as `roll_rarity_min`/`roll_rarity_max`.
    A FLOOR alone means the roll is clamped there and climbs to the best
    rarity the game has (the ladder top); both ends mean exactly that span;
    neither means the roll IS the container's own `rarity` — a point.

    Only one container in the game's data carries a clamp today (the Hero
    Weapon Cache, an Epic floor up to Legendary), and this READS it instead of
    hard-coding it, so a patch that adds or drops a clamp needs no edit here.

    NO CLAMP means the span is a POINT equal to the container's own `rarity`
    — which is the tier every surface already shows. That is the whole of the
    non-Heroic boxes: both Rift gear caches, the Rift gift cache and the
    starter "Mysterious Cache" (`Z1_WeaponBundle`) all come back as a single
    rarity, so printing a separate "span" for them would only repeat the
    tier. That is why nothing does; only the clamped box reads wider than its
    own tier, and only there is a span worth rendering.
    """
    box = row.get("rarity") or ""
    if row.get("roll_rarity_max"):
        ceiling = row["roll_rarity_max"]
    elif row.get("roll_rarity_min"):
        ceiling = RARITY_ORDER[-1]
    else:
        ceiling = box
    return (row.get("roll_rarity_min") or box, ceiling)


def compiled_container_facts() -> dict[str, dict]:
    """Every `LootableContainer` the COMPILED drops shim declares, as
    `{item id: {"gain": <its props.gainItem>, "rarity": <the box's rarity>}}`.

    The item sheet prunes `props`, so the compiler bakes each container's
    whole `gainItem` (loot table, level range, `maxItems`, rarity clamp) onto
    its drops row as `gain_item` — beside `roll_rarity_min`. Reading it HERE,
    not the loose `assets/data/item.json`, is what lets the cache contract
    tests run in CI where the game dump is absent: the same spec-vs-truth
    check is now pinned against the shim the app actually ships.

    A container is recognised by its compiled `type`, never a hand-written id
    list, so a box the game gains turns up here — and fails the contract — as
    soon as a scan records it.
    """
    out: dict[str, dict] = {}
    for iid, row in (_data().get("items") or {}).items():
        if not isinstance(row, dict) or row.get("type") != "LootableContainer":
            continue
        gain = row.get("gain_item")
        if not isinstance(gain, dict) or not gain.get("lootTable"):
            continue
        out[iid] = {"gain": gain, "rarity": row.get("rarity") or ""}
    return out


@lru_cache(maxsize=None)
def heroic_cache_spec() -> dict[str, dict]:
    """Cache item id -> what the sheet says the box itself is, as
    {table, level_min, level_max, max_items, player_scale, template, rarity,
    rarity_min, rarity_max, name}. `rarity`/`name` are read live off the
    container item, and the `rarity_min`/`rarity_max` span off the container's
    compiled `roll_rarity_min` clamp; the rest comes from _HEROIC_CACHE_SPECS.
    Resolve by ITEM ID: two boxes share the name "Chaotic Gear Cache", so use
    cache_display_name() for anything a human reads and this only for facts.

    `rarity` is the CONTAINER's own field — the box's tier in a list. What an
    open HANDS OUT is the span `rarity_min`..`rarity_max`, and for five of the
    six boxes the span is a point equal to `rarity`. The Hero Weapon Cache is
    the exception: its gainItem clamps the roll at Epic and leaves the ceiling
    open, so its span is Epic..the ladder top (Legendary) — see
    container_roll_span()."""
    items = _data().get("items", {})
    out: dict[str, dict] = {}
    for cid, spec in _HEROIC_CACHE_SPECS.items():
        it = items.get(cid) or {}
        floor, ceiling = container_roll_span(it)
        out[cid] = {**spec,
                    "rarity": it.get("rarity") or "",
                    "rarity_min": floor,
                    "rarity_max": ceiling,
                    "name": it.get("name") or cid}
    return out


@lru_cache(maxsize=None)
def heroic_cache_contents() -> dict[str, tuple[dict, ...]]:
    """Cache item id -> what is inside it, one dict per piece.

    Read from the sheet's `HM_*` loot tables (via the normal cdb path, so it
    works in the frozen build and needs no scan of its own). Each entry:
    {item, label, slot, rarity, type, level, template}. Ordered by slot
    progression using the same order the gear tiles use, so the box reads
    head -> boots.

    Every row reports the BOX's `rarity`, not the table row's own: the table is
    the pool, the container's `rarity` is what the roll hands you. That agrees
    for five of the six boxes; it disagrees for the weapon cache, whose rows
    are level-4 RARE craft starters inside an Epic box.

    `rarity_min`/`rarity_max` report the SPAN an open can hand out — equal for
    five of the six boxes, and Epic..Legendary for the weapon cache, whose
    gainItem clamps the roll at Epic and lets it climb (see
    `heroic_cache_spec`). A surface that prints "Epic" alone is telling the
    floor as the whole story.

    `level` is the box's fixed level, or None for a `player_scale` box — Mira's
    Chaotic Gear/Weapon caches roll at YOUR level (1-25), so the level is not
    knowable until it drops and is reported as the range instead.

    `template` marks a box where the row's NAME is a base template rather than
    the granted piece (the Hero Weapon Cache, whose four names are craft-starter
    weapons). The rest name the real pieces.

    A cache with no table (or an empty one) maps to () rather than being
    omitted, so the UI can say "contents not recorded" honestly.
    """
    from .. import cdb
    order = _SLOT_ORDER
    specs = heroic_cache_spec()

    want = {s["table"] for s in specs.values()}
    tables = {}
    for r in cdb.lines("lootTable"):
        tid = r.get("id") or ""
        if tid in want:
            tables[tid] = [e.get("item") for e in (r.get("loot") or [])
                           if e.get("item")]

    items = _data().get("items", {})
    out: dict[str, tuple[dict, ...]] = {}
    for cache_id, spec in specs.items():
        box_rar = spec["rarity"]
        box_lvl = (None if spec["player_scale"]
                   else spec["level_max"])
        rows = []
        for iid in tables.get(spec["table"], []):
            it = items.get(iid) or {}
            typ = it.get("type") or ""
            rows.append({
                "item": iid,
                "label": (it.get("name") or names.item_name(iid) or iid),
                "slot": typ or "—",
                "type": typ,
                "rarity": box_rar,
                "rarity_min": spec.get("rarity_min") or box_rar,
                "rarity_max": spec.get("rarity_max") or box_rar,
                "level": box_lvl,
                "template": spec["template"],
            })
        rows.sort(key=lambda r: (order.index(r["type"])
                                 if r["type"] in order else 99,
                                 r["label"].lower()))
        out[cache_id] = tuple(rows)
    return out


def _derived_container_spec(cache_id: str, row: dict) -> dict | None:
    """The spec for a compiled `LootableContainer` the hand table does NOT
    claim, derived entirely from the compiler-baked `gain_item`.

    A specced box carries hand facts the game data does not hold (whether its
    table is a rolling TEMPLATE, and who sells it); a box the hand table never
    claimed is described from what the shim DOES carry — the loot table, the
    level range, `maxItems` and the rarity clamp — so it is surfaced instead
    of left as an invisible test-only exclusion.

    `template` is False here on purpose: the app does not claim a rolling
    template it cannot evidence, so the contents report each row's OWN
    rarity/level — the table's real pool — rather than the box's. `None` when
    the row is not a container with a gainItem, so the caller can skip it.
    """
    gain = row.get("gain_item")
    if not isinstance(gain, dict) or not gain.get("lootTable"):
        return None
    lr = gain.get("levelRange") or {}
    lo, hi = lr.get("min"), lr.get("max")
    floor, ceiling = container_roll_span(row)
    rarity = row.get("rarity") or ""
    return {
        "table": gain["lootTable"],
        "level_min": lo,
        "level_max": hi,
        "max_items": gain.get("maxItems"),
        "player_scale": bool(lo is not None and hi is not None and lo != hi),
        "template": False,
        "rarity": rarity,
        "rarity_min": floor,
        "rarity_max": ceiling,
        "name": row.get("name") or cache_id,
    }


@lru_cache(maxsize=None)
def container_spec() -> dict[str, dict]:
    """Container item id -> the facts an open needs, for EVERY compiled
    `LootableContainer`: the hand-specced Heroic/Rift boxes
    (`heroic_cache_spec`) plus the ones the hand table never claimed, derived
    from the compiled `gain_item` by `_derived_container_spec`.

    This generic layer is what makes the Items page SURFACE every box the game
    ships — the starter "Mysterious Cache" (`Z1_WeaponBundle`) included —
    instead of hiding the unclaimed ones behind a test-only exclusion list.
    `container_contents` lists what they hold and the item page's Contains
    section renders them exactly like a specced box.
    """
    out = dict(heroic_cache_spec())
    for cid, row in (_data().get("items") or {}).items():
        if cid in out or not isinstance(row, dict):
            continue
        if row.get("type") != "LootableContainer":
            continue
        spec = _derived_container_spec(cid, row)
        if spec is not None:
            out[cid] = spec
    return out


@lru_cache(maxsize=None)
def container_contents() -> dict[str, tuple[dict, ...]]:
    """Container item id -> what is inside it, for EVERY compiled container.

    A hand-specced box is served by `heroic_cache_contents` unchanged — its
    hand spec says the table is the pool and the container is the roll, so its
    rows report the BOX's rarity/level. A DERIVED box reports each table row's
    OWN rarity and level instead: the app has no hand claim that its table is a
    rolling template, so it states only the real pool it can read.

    Every container the shim declares maps to a (possibly empty) tuple, so a
    caller can tell "no pieces recorded" from "not a container".
    """
    from .. import cdb
    order = _SLOT_ORDER
    out = dict(heroic_cache_contents())
    specs = container_spec()
    want = {s["table"] for cid, s in specs.items() if cid not in out}
    if not want:
        return out
    tables: dict[str, list[str]] = {}
    for r in cdb.lines("lootTable"):
        tid = r.get("id") or ""
        if tid in want:
            tables[tid] = [e.get("item") for e in (r.get("loot") or [])
                           if e.get("item")]
    items = _data().get("items", {})
    for cid, spec in specs.items():
        if cid in out:
            continue
        rows = []
        for iid in tables.get(spec["table"], []):
            it = items.get(iid) or {}
            typ = it.get("type") or ""
            rar = it.get("rarity") or spec["rarity"]
            rows.append({
                "item": iid,
                "label": (it.get("name") or names.item_name(iid) or iid),
                "slot": typ or "—",
                "type": typ,
                "rarity": rar,
                "rarity_min": rar,
                "rarity_max": rar,
                "level": it.get("level"),
                "template": False,
            })
        rows.sort(key=lambda r: (order.index(r["type"])
                                 if r["type"] in order else 99,
                                 r["label"].lower()))
        out[cid] = tuple(rows)
    return out


def is_heroic_cache(item_id: str) -> bool:
    """True for the Hardmode/Heroic caches that open into a gear box."""
    return (item_id or "") in _HEROIC_CACHE_TABLES


@lru_cache(maxsize=1)
def _unreleased_cache_ids() -> frozenset[str]:
    """The boxes no source anywhere in the scan points at.

    A box is RELEASED when at least one of its contents carries a recorded
    drop row — Mira's Chaotic Gear Cache is, because every one of its 25
    pieces names her and a Nightblood price. The three `HM_` Hardmode boxes
    are not: `HM_DemonGear` and `HM_Items` have no vendor, boss, chest or
    crate row on any of their 49 pieces, and no vendor NPC exists in a rift
    to sell one (verified live 2026-09-26: a rift scene holds 25 elements,
    every one a chest / portal / trigger / spawn, zero NPCs).

    That is the signature of authored-but-unreachable content: the boxes ship
    with real tooltips and real loot tables, but nothing hands them out. They
    are labelled UNRELEASED rather than given an invented source, and the
    moment a scan records one they stop being unreleased with no code change.

    A vendor we have RECORDED beats any inference from the pieces: the two
    Hero caches are sold by the Medal of Glory trader (proved live, see
    _HEROIC_CACHE_VENDORS) even though nothing in the item sheet points at
    them, so they are released. That check has to come first — otherwise a
    box with a known seller can still read as unreleased, which is how the
    Hero Weapon Cache was briefly mislabelled.
    """
    out = set()
    for cid, box in heroic_cache_contents().items():
        if _HEROIC_CACHE_VENDORS.get(cid):
            continue          # we have seen this one sold
        # resolve_drops, NOT merge_drops: merge_drops appends the box row
        # this function is deciding whether to append, so asking it would
        # recurse (cache_is_unreleased -> _unreleased_cache_ids ->
        # merge_drops -> _with_cache_row -> cache_is_unreleased). Only a
        # REAL recorded source counts, which is exactly resolve_drops.
        #
        # NOTE the `template` box is judged on its rows like any other. Those
        # rows are the level-4 RARE craft starters, and the vendor row on
        # them describes the BASE weapon, not the box — but the box itself is
        # separately recorded as sold, so the label is right either way and
        # the two facts do not have to be reasoned into each other.
        if not any(resolve_drops(r["item"]) for r in box):
            out.add(cid)
    return frozenset(out)


def cache_is_unreleased(cache_id: str) -> bool:
    """True when nothing in the scan hands out this box — the data exists and
    is fully authored, but no source points at it. Not a guess about a gate:
    it is the absence of every recorded edge."""
    return (cache_id or "") in _unreleased_cache_ids()


def _recorded_cache_vendor(cache_id: str, rec: dict) -> dict | None:
    """A box's seller, from the one hand fact the index does not carry.

    The Hero caches have `drops: []` in the item sheet and no Shop row in the
    drops index, so nothing derivable points at Shiro James. `rec` is the
    whole record: the seller's name, the currency, and the listed price. None
    when the record carries no price, so a missing fact reports the box as
    having no seller rather than inventing one.
    """
    price = rec.get("price")
    if not isinstance(price, (int, float)):
        return None
    cost = [{"kind": rec.get("currency") or "", "amount": int(price)}]
    locs = list(_mog_merchant_towns())
    return {"source": rec.get("vendor") or "", "source_id": "",
            "cost": cost,
            "locs": locs, "n_locs": len(locs)}


@lru_cache(maxsize=None)
def cache_vendor(cache_id: str) -> dict | None:
    """The vendor that sells a box, and what it costs — or None.

    A RECORDED seller wins: _HEROIC_CACHE_VENDORS names the boxes we have
    actually watched being sold, which is stronger evidence than anything the
    sheet can offer, since the sheet records no container at all. The sale
    itself is read back from the scan, so that table is only the two facts
    the scan cannot see (see _recorded_cache_vendor).

    Failing that, the vendor is recovered indirectly. The scan never records
    the CONTAINER (every box's own `drops` is empty), but it records the
    vendor's LOOT TABLE against every item inside that table. So look for a
    piece of this box whose npc drop row names the box's own table: that row
    is the sale, one level up from the piece. This is how Mira is found.

    Returns {source, source_id, cost, locs, n_locs} — the same shape the Drops
    From npc rows use. None when there is neither a recorded seller nor a
    piece carrying such a row, which is the whole story for the one `HM_`
    box nothing in the data points at.
    """
    rec = _HEROIC_CACHE_VENDORS.get(cache_id)
    if rec:
        return _recorded_cache_vendor(cache_id, rec)
    spec = heroic_cache_spec().get(cache_id) or {}
    table = spec.get("table")
    if not table:
        return None
    box = heroic_cache_contents().get(cache_id) or ()
    for row in box:
        for d in merge_drops(row["item"]):
            if d.get("kind") == "npc" and d.get("table") == table:
                return {"source": d.get("source") or "",
                        "source_id": d.get("source_id") or "",
                        "cost": d.get("cost") or (),
                        "locs": list(d.get("locs") or ()),
                        "n_locs": d.get("n_locs") or 0}
    return None


def cache_vendors(cache_id: str) -> tuple[dict, ...]:
    """EVERY recorded seller of a box, the one cache_vendor names first.

    A box can sit on more than one counter. Rift_Gear_Cache is the case that
    proves it: the drops index puts Mira on it (recovered from the pieces
    inside, one level below the container), while the scan recorded the
    Intendant putting the box itself on his counter. Both are true, and
    cache_vendor alone could only ever report Mira — so a player watching the
    Intendant was told the box came from someone else.

    Each entry is the cache_vendor shape, with `shop_price` added for a
    recorded sale (the sheet names no currency, only an amount). Primary
    first, then the others in the order the drops list them. () when the box
    has no seller at all.
    """
    primary = cache_vendor(cache_id)
    out: list[dict] = [dict(primary)] if primary else []
    seen = {(primary or {}).get("source") or ""}
    for d in merge_drops(cache_id):
        if d.get("kind") != "npc":
            continue
        who = d.get("source") or ""
        if not who or who in seen:
            continue
        seen.add(who)
        out.append({"source": who,
                    "source_id": d.get("source_id") or "",
                    "cost": d.get("cost") or (),
                    "locs": list(d.get("locs") or ()),
                    "n_locs": d.get("n_locs") or 0,
                    "shop_price": d.get("shop_price")})
    return tuple(out)


# Currency ids the scan records, and what a player calls them. A cost renders
# as "100 BadgeOfGlory" otherwise, which is the sheet's id, not a word.
# HAND-FACT: _CURRENCY_LABEL
_CURRENCY_LABEL = {
    "BadgeOfGlory": "Medal of Glory",
    "Nightblood": "Nightblood",
    "DemonicSoul": "Demonic Soul",
}


def currency_label(kind: str) -> str:
    """A cost's currency, named the way the game names it."""
    return _CURRENCY_LABEL.get(kind or "", kind or "")


def cache_via(cache_id: str) -> str:
    """Who hands this box over, in prose. Only called for a RELEASED box.

    Prefers the hand-written `via` label, and falls back to the vendor the
    scan actually recorded — so a released box with no string still names its
    real seller instead of the old bare "a drop", which was both vague and
    wrong for every box here (they are bought, not killed for).
    """
    via = (heroic_cache_spec().get(cache_id) or {}).get("via")
    if via:
        return via
    v = cache_vendor(cache_id)
    if not v:
        return "a source this build's scan has not recorded"
    who = v.get("source") or "a vendor"
    # cache_vendor's cost rows are dicts, but a hand-entered record is easier
    # to write as (kind, amount) pairs — accept either rather than crash
    bits = []
    for c in v.get("cost") or ():
        if isinstance(c, dict):
            kind, amt = c.get("kind") or "", c.get("amount") or 0
        else:
            kind, amt = c[0], c[1]
        bits.append(f"{amt:,} {currency_label(kind)}")
    price = " · ".join(bits)
    return f"{who}, for {price}" if price else who


@lru_cache(maxsize=None)
def vendor_caches(vendor: str) -> tuple[dict, ...]:
    """Every box a vendor sells, as the vendor's actual STOCK.

    The gear tab flattened each box into the pieces inside it, so a vendor's
    pane read "VENDOR GEAR - 25 pieces - sold by Mira" and never showed the
    three boxes you actually walk up and buy. This returns the boxes, so the
    pane can name them.

    Each: {cache, name, rarity, cost, locs, n_pieces, player_scale, level}.
    Boxes with no recorded vendor (the `HM_` Hardmode three) are NOT here —
    use unreleased_caches() for those. Ordered gear / weapon / gift, then by
    name, so the pane reads in the order a vendor's stock is presented.

    `vendor` matches case-insensitively and need not be the full name, so
    "Mira" finds "Mira, Demon Huntress"; an exact match wins over a partial
    one, and a partial match over a longer unrelated name. An unknown vendor
    returns () rather than raising.
    """
    want = (vendor or "").strip().lower()
    if not want:
        return ()
    names = {v["source"] for cid in heroic_cache_contents()
             for v in cache_vendors(cid) if v.get("source")}
    exact = [n for n in names if n.strip().lower() == want]
    partial = sorted((n for n in names if want in n.strip().lower()
                      and n not in exact), key=len)
    match = (exact[0] if exact
             else (partial[0] if partial else None))
    if match is None:
        return ()
    order = {"Rift_Gear_Cache": 0, "Rift_Weapon_Cache": 1, "Rift_Gift_Cache": 2}
    out: list[dict] = []
    for cid, box in heroic_cache_contents().items():
        sellers = cache_vendors(cid)
        mine = [v for v in sellers if v["source"] == match]
        if not mine:
            continue
        v = mine[0]
        spec = heroic_cache_spec().get(cid) or {}
        out.append({
            "cache": cid,
            "name": cache_display_name(cid),
            "rarity": spec.get("rarity") or "",
            "cost": tuple((c.get("kind") or "", c.get("amount") or 0)
                          for c in (v.get("cost") or ())),
            "locs": tuple(v.get("locs") or ()),
            "n_pieces": len(box),
            "player_scale": bool(spec.get("player_scale")),
            "level": spec.get("level_max"),
            "shop_price": v.get("shop_price"),
            # the other counters this same box sits on, so a vendor's pane
            # doesn't read as the only way to buy what others also sell
            "also_sold_by": tuple(o["source"] for o in sellers
                                  if o["source"] != match),
        })
    out.sort(key=lambda r: (order.get(r["cache"], 9), r["name"].lower()))
    return tuple(out)


@lru_cache(maxsize=1)
def unreleased_caches() -> tuple[dict, ...]:
    """The boxes nothing hands out, as rows a page can list. Each:
    {cache, name, rarity, n_pieces, level, player_scale}."""
    out = []
    for cid, box in heroic_cache_contents().items():
        if not cache_is_unreleased(cid):
            continue
        spec = heroic_cache_spec().get(cid) or {}
        out.append({"cache": cid, "name": cache_display_name(cid),
                    "rarity": spec.get("rarity") or "",
                    "n_pieces": len(box),
                    "level": spec.get("level_max"),
                    "player_scale": bool(spec.get("player_scale"))})
    out.sort(key=lambda r: r["name"].lower())
    return tuple(out)


# Boxes a faction's boss list does NOT carry. The Nightling's gift box is an
# augment roll (12 Corrupted Gifts, 5 Nightblood), not gear — it belongs on
# the vendor's item page, not beside her two gear boxes in a drop list.
_CACHE_ROW_EXCLUDE = frozenset({"Rift_Gift_Cache"})


@lru_cache(maxsize=None)
def faction_caches(faction: str) -> tuple[dict, ...]:
    """The boxes a faction's bosses are credited with, at any rarity.

    A box belongs to ONE faction only when a single faction owns a clear
    majority of its pieces. Two cases decide it:

    * a clear majority — `Rift_Weapon_Cache` is 3 Demon + 1 Crimson, so it
      belongs to the Nightling despite leaking a piece;
    * a TIE — `HM_Gear_Cache_25` is 6 Bee / 6 Kobold / 6 Manfish / 6 Crimson,
      so no family owns it. Crediting it to whichever faction sorted first
      handed a shared Hero-mode box to one arbitrary family and implied that
      family's boss drops it. A tied box is skipped entirely.

    Boxes whose pieces carry no faction at all fall back to the id prefix —
    the Nightling's Corrupted Gifts are `DemonGearUpgrade_*`.

    Ordered Rare before Epic (the vendor tier, then the hardmode tier), then
    by name, so a boss row reads cheapest-first. Each: {cache, name, rarity,
    n_pieces, unreleased, vendor}.
    """
    fac = (faction or "").lower()
    if not fac:
        return ()
    out: list[dict] = []
    for cid, box in heroic_cache_contents().items():
        if not box or cid in _CACHE_ROW_EXCLUDE:
            continue
        facs = Counter()
        for r in box:
            it = _data().get("items", {}).get(r["item"]) or {}
            f = (it.get("faction") or "").lower()
            if f:
                facs[f] += 1
        if facs:
            ranked = facs.most_common()
            if len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
                continue          # tied across families — nobody owns it
            if ranked[0][0] != fac:
                continue
        else:
            # no faction on any piece — the id prefix names it
            if not any(r["item"].lower().startswith(fac)
                       for r in box):
                continue
        spec = heroic_cache_spec().get(cid) or {}
        v = cache_vendor(cid)
        out.append({
            "cache": cid,
            "name": cache_display_name(cid),
            "rarity": spec.get("rarity") or "",
            "n_pieces": len(box),
            "unreleased": cache_is_unreleased(cid),
            "vendor": (v or {}).get("source") or "",
        })
    out.sort(key=lambda r: (0 if r["rarity"] == "Rare" else 1,
                            r["name"].lower()))
    return tuple(out)


@lru_cache(maxsize=1)
def _cache_by_item() -> dict[str, str]:
    """Item id -> the cache it is one of the contents of, i.e. the reverse
    of heroic_cache_contents(). A piece that lives in a Heroic box is
    obtained by opening that box, so its card names the box instead of
    reporting no sources at all. Built from the same boxes, so it can never
    disagree with them."""
    out: dict[str, str] = {}
    for cache_id, box in heroic_cache_contents().items():
        for row in box:
            out.setdefault(row["item"], cache_id)
    return out


def _heroic_cache_summary() -> str:
    """ — the Hero Gear Cache, 24 Epic pieces" — the short box clause the
    acquisition note hangs off the boss. Empty when the contents are not
    recorded, so the note never claims a count it cannot back."""
    box = heroic_cache_contents().get("HM_Gear_Cache_25") or ()
    if not box:
        return " (Hero Gear Cache)"
    rarities = {r["rarity"] for r in box if r["rarity"]}
    rar = f"{rarities.pop()} " if len(rarities) == 1 else ""
    return f" — the Hero Gear Cache, {len(box)} {rar}pieces"


@lru_cache(maxsize=1)
def mog_exchange() -> tuple[dict, ...]:
    """Shiro James's stock (the Medal of Glory vendor) — items priced at
    Medal of Glory cost (the BadgeOfGlory spend list).

    Vendor stock rides the ITEM's drops[] rows with table=Shop and a cost
    list (the same convention as Zoey's Spark Crystals at 100 DemonicSoul
    and Mira's sigils at 5 Nightblood): a Shiro James source row on the
    item + cost entries naming BadgeOfGlory. Each dict: {item, label, qty,
    currency}, in the sheet's order. His capture may predate the priced
    rows (the shop rows exist but carry no costs), so unpriced rows are
    skipped rather than rendered as
    invented listings; a future scan that records the real stock lights
    this up with no code change. () while the stock is unrecorded."""
    srcs, tbls = _sources(), _tables()
    mog_srcs = {i for i, s in enumerate(srcs) if is_mog_source(s)}
    if not mog_srcs:
        return ()
    out: list[dict] = []
    for iid, it in (_data().get("items") or {}).items():
        for dr in it.get("drops", []):
            if (dr.get("s") not in mog_srcs
                    or not (0 <= dr.get("t", -1) < len(tbls))
                    or tbls[dr["t"]] != "Shop"):
                continue
            for c in dr.get("cost") or []:
                if str(c.get("kind") or "") != "BadgeOfGlory":
                    continue
                if any(r["item"] == iid for r in out):
                    break
                out.append({
                    "item": iid,
                    "label": (it.get("name") or names.item_name(iid)
                              or iid),
                    "qty": c.get("amount") or 0,
                    "currency": "BadgeOfGlory",
                })
                break
    return tuple(out)


@lru_cache(maxsize=None)
def no_source_reason(item_id: str) -> str:
    """The human reason an item's Drops From is empty, so the page says WHY
    instead of the generic 'no drop sources recorded'. Order: crafted gear
    (made at a fixed level by a recipe — never dropped), the classes'
    starting loadout (armor + class glider + gather tools, from unit.json
    `parts.gear`), the base clothes, the starter cache (opens into the
    fixed-level class starter weapons), the `_Shop` gear (sold by a vendor
    the scan couldn't capture), items listed in loot tables the scan can't
    anchor (rift rewards, craft-point nodes, ...), the recipes (rolled by
    the WorldRecipeWithJob token), the mount/glider families (the loot
    tables roll one family representative), and finally the WorldLoot token
    gear that stays sourceless — only actual gear gets the WorldLoot claim,
    never materials, packages, currencies or other non-gear."""
    it = _data().get("items", {}).get(item_id) or {}
    from .stats import item_fixed_level
    from .craft import is_craftable
    if item_fixed_level({"id": item_id, **it}) is not None \
            or is_craftable(item_id):
        return "No drop sources — crafted or purchased item."
    iid = item_id or ""
    itype = it.get("type") or ""
    items = _data().get("items", {})
    if _STARTER_RE.search(iid):
        cls = (it.get("classes") or ["?"])[0]
        return (f"Starting gear — {cls} class starter piece at character "
                "creation.")
    if iid in _CLASS_GLIDERS:
        return (f"Starting gear — {_CLASS_GLIDERS[iid]} class starter "
                "glider at character creation.")
    if iid in _STARTER_TOOLS:
        return ("Starting gear — gather tools every class starts with at "
                "character creation.")
    if iid == _BASE_CLOTHES_ID:
        return "Base clothes — the default shirt worn at character creation."
    if itype == "LootableContainer":
        if is_heroic_cache(iid):
            box = heroic_cache_contents().get(iid) or ()
            rarities = {r["rarity"] for r in box if r["rarity"]}
            rar = (f"{rarities.pop()} " if len(rarities) == 1
                   else "mixed-rarity ")
            if cache_is_unreleased(iid):
                return ("UNRELEASED — fully authored box (see Contains), but "
                        "nothing in the game data hands it out: no vendor, "
                        "boss, chest or crate references it. The moment a "
                        "scan records a source this clears.")
            via = cache_via(iid)
            if box:
                return (f"From {via} — opens into a box holding "
                        f"{len(box)} {rar}pieces (see Contains).")
            return (f"From {via} — opens into a box; its contents "
                    "are not recorded by the scan yet.")
        # a container the HAND table does not claim: describe it from the
        # compiled `gain_item` + the pieces the generic path lists, so the note
        # stays true as boxes come and go, and point at the Contains section
        # the item page now renders for it instead of leaving it invisible.
        spec = container_spec().get(iid) or {}
        box = container_contents().get(iid) or ()
        n = len(box)
        if not box:
            return ("Container — opens into a box the scan has not recorded "
                    "the contents of yet.")
        # the game's OWN table name marks a starter (`Z1_Start_*`); read that
        # from the data rather than assuming "starter" for every box the hand
        # table never claimed
        starter = "start" in (spec.get("table") or "").lower()
        head = "Starter cache" if starter else "Container"
        kind = "" if spec.get("player_scale") else "fixed-level "
        note = f"{head} — opens into {n} {kind}piece"
        note += "s" if n != 1 else ""
        per = spec.get("max_items")
        if per:
            note += f", at most {per} per open"
        if starter and not spec.get("player_scale"):
            note += "; not scaled to your level"
        return note + " (see Contains)."
    if iid == "BadgeOfGlory":
        stock = mog_exchange()
        # The CACHES are heroic BOSS drops, and the vendor is a single NPC
        # reached from the boss — so the note points at the boss and the box,
        # never at a list of shared hub towns (which sent the reader looking
        # for the cache in the social hubs, where it is not sold).
        cache = _heroic_cache_summary()
        stock_txt = ("; spent at Shiro James on: "
                     + " · ".join(
                         f"{r['label']} ({r['qty']} MoG)" if r["qty"]
                         else r["label"] for r in stock)
                     ) if stock else (
            "; spent at Shiro James on the Hero Gear Cache and the Hero "
            "Weapon Cache")
        return ("Heroic completion reward — earned by killing a Heroic "
                f"boss, which drops the cache{cache}{stock_txt}.")
    if (items.get(iid) or {}).get("type") == "InfusionPattern":
        return ("Infusion pattern — one guaranteed per Heroic kill "
                "(the boss's own pattern); applied to heroic gear at "
                "the Infusion crucible in the social hubs.")
    if iid.endswith("_Shop"):
        return "Shop gear — sold by a vendor the scan data doesn't capture."
    if iid in _LOOT_LISTED_UNANCHORED:
        return ("Listed in loot tables the scan can't anchor — "
                f"{_LOOT_LISTED_UNANCHORED[iid]}.")
    if iid.startswith("Recipe_"):
        return ("Recipe drop — WorldRecipeWithJob token (world crates / "
                "humanoid mobs); recipes aren't listed individually in "
                "loot tables.")
    cache = _cache_by_item().get(iid)
    if cache:
        # the piece is one of a box's contents — that box is how it is
        # actually obtained. merge_drops already lists it as a BOXES row;
        # this is the prose under the item's own header.
        label = cache_display_name(cache)
        box = heroic_cache_contents().get(cache) or ()
        if cache_is_unreleased(cache):
            return (f"Cache contents — one of the {len(box)} pieces the "
                    f"{label} opens into. That box is unreleased: nothing in "
                    "the game data currently hands it out.")
        via = cache_via(cache)
        return (f"Cache contents — one of the {len(box)} pieces the "
                f"{label} opens into; get the box from {via}.")
    if iid in _BASE_RING_IDS:
        # the same stat's real pieces share the id suffix (Ringlet_Z2,
        # Circle/Signet/Set Eye from the recipes), so name them from the
        # sheet rather than a hand-written list that can drift
        stat = iid.split("_", 1)[-1]
        kin = sorted((items.get(k) or {}).get("name") or k for k in items
                     if k != iid and k.startswith("Finger_")
                     and k.endswith(f"_{stat}"))
        if kin:
            return ("Unused base row — never dropped or crafted; the "
                    f"obtainable rings are {', '.join(kin)}.")
    # Everything else gets the plain readout — no invented mechanics text:
    # the mount/glider family, the WorldLoot gear and the generic non-gear
    # fallback all just say the scan recorded no sources.
    return "No drop sources found."


@lru_cache(maxsize=64)
def _worldloot_chance(table_id: str) -> float:
    """The total WorldLoot roll chance of a loot table: the sum of the
    proba of its WorldLoot entries (the faction tables roll it at 0.01
    solo + 0.05 party = 0.06; WorldCrate at 0.35). 0.0 when the table
    doesn't roll WorldLoot (the Bee table)."""
    if not table_id:
        return 0.0
    from .. import cdb
    for r in cdb.lines("lootTable"):
        if r.get("id") == table_id:
            return sum(e.get("proba", 0.0) for e in (r.get("loot") or [])
                       if e.get("item") == "WorldLoot")
    return 0.0


def _is_boss(source_id: str) -> bool:
    """True when the drop source is a named boss (its id also names a loot
    table, or the unit carries the boss flag bit). Lazy import — units
    pulls the CDB, which the items package otherwise avoids at import time."""
    if not source_id:
        return False
    from .. import units
    return units.is_boss(source_id)


@lru_cache(maxsize=1)
def _recipe_output_ids() -> frozenset[str]:
    """Ids any recipe produces (`craft.is_craftable`'s key set), through the
    shared payload-pure derivation the compiler also uses. Imported lazily:
    `craft` imports this package's siblings, and this is only ever reached
    through `_is_crafted` at call time."""
    try:
        from . import craft
        return recipe_output_ids_from_payload(craft._recipes_raw())
    except Exception:
        return frozenset()


# `crafted_item_ids` / `shop_item_ids_from_payload` used to be defined here; they
# now live in the stdlib-only `rules` module (re-exported above) so the compiler
# reaches them without importing this data layer. `_recipe_output_ids` above and
# `_crafted_ids` below are the runtime's input assembly around them.


@lru_cache(maxsize=1)
def _crafted_ids() -> frozenset[str]:
    """The runtime's crafted-item set, cached (the path is set-shaped and the
    data is static)."""
    return crafted_item_ids(_data(), _recipe_output_ids())


def _is_crafted(item_id: str) -> bool:
    """True when the item is crafted gear (made at a fixed level in-game), so
    its drop rows are noise and must be hidden. The scan recorded the base
    dungeon-set rows on the crafted `_Craft` variants too (Beekeeper's
    Scarf, Handguards of the Deep Sea, ...) and on the recipe-bearing
    faction pieces (Aura of the Honeycomb, Cantal Goya's Breastplate, ...),
    but those pieces are made by recipes — `crafted_item_ids` is the single
    source of truth."""
    return bool(item_id) and item_id in _crafted_ids()


@lru_cache(maxsize=256)
def _boss_dungeon_name(source_id: str) -> str | None:
    """The dungeon a boss lives in, for the item page's boss sub-line: the
    dungeon's in-game name (Bee Hive, Kobold Mines, Crimson Sacristy, ...).
    Rift bosses resolve to the arena ('Rift (arena entrance)'), the same
    label the unknown-location resolver uses. None when the source isn't a
    dungeon boss."""
    if not source_id:
        return None
    from .. import dungeons
    d = dungeons.unit_to_dungeon_map().get(source_id)
    if not d:
        return None
    if d.get("entrance_zone") == "Rifts":
        return "Rift (arena entrance)"
    return d.get("ingame_name") or d.get("name") or None


@lru_cache(maxsize=None)
def item_source_max_level(item_id: str) -> int | None:
    """The highest level at which a scalable item can actually be obtained.

    A real mob/boss drop source governs the cap — a drop rolls at the
    dropper's level (Judgement also drops from the level-19 boss Munster
    Chuck, so it caps at 19; Worldsplitter drops from the level-25 rift
    boss). Everything else — shop gear (the Guild Merchants sell the town
    weapons at item level 20 in Tyrna and 25 in Lower Ramburg), player-
    scale chests (Mira's Demon set scales to YOUR level) and pure drops —
    has no cap: the global max level. None for crafted gear with no
    sources."""
    if not item_id:
        return None
    it = _data().get("items", {}).get(item_id) or {}
    srcs = _sources()
    ul = _unit_levels()
    sc = _data().get("scaling", {})
    max_level = sc.get("max_level") or 25
    unit_levels: list[int] = []
    for dr in it.get("drops", []):
        s = srcs[dr["s"]] if 0 <= dr["s"] < len(srcs) else {}
        if s.get("kind") != "unit":
            continue
        lvl = ul.get(s.get("id"))
        # skip placeholder units (training dummies / prisoners are stamped
        # level 100) — they never roll real gear
        if lvl and 0 < lvl <= max_level:
            unit_levels.append(lvl)
    if unit_levels:
        return max(unit_levels)
    # No unit drop sources: crafted gear has no sources (its FIXED level
    # governs — callers use item_fixed_level first), and the WorldLoot-
    # flagged zone gear (Reinforced Hauberk of the Exile, the Blessed
    # sets) is generated from the WorldLoot token at its authored zone-tier
    # level — fall back to that sheet level so the slider / badge open
    # where the piece is actually found instead of the global max. Shop
    # gear (the town weapons, sold scaled to each town) has no zone cap
    # and stays on the global max.
    lvl = it.get("level")
    if (isinstance(lvl, int) and 1 < lvl <= max_level
            and not has_guild_merchant(item_id)):
        return lvl
    return None


@lru_cache(maxsize=None)
def item_vendor_levels(item_id: str) -> tuple[int, ...]:
    """The item levels at which the Guild Merchants sell an item — one per
    stamped town (Tyrna L20, Lower Ramburg L25), sorted ascending. Shop
    gear sold by the town vendors scales to each town's item level, so the
    item page restricts the gear-scale slider to exactly these positions
    and opens on the highest one. Empty when no stamped Guild Merchant
    sells the piece (or it has no scanned merchant rows at all)."""
    if not item_id:
        return ()
    it = _data().get("items", {}).get(item_id) or {}
    srcs = _sources()
    lvls = set()
    for dr in it.get("drops", []):
        s = srcs[dr["s"]] if 0 <= dr["s"] < len(srcs) else {}
        lvl = _TOWN_ITEM_LEVELS.get(s.get("id") or "")
        if lvl:
            lvls.add(lvl)
    return tuple(sorted(lvls))


@lru_cache(maxsize=None)
def item_scale_max_level(item_id: str) -> int:
    """The top level of the gear-scale slider for an item: the highest town
    level when a Guild Merchant sells it (vendor gear's slider is
    restricted to the vendor levels and opens on the top sale — the Rare
    stats at L25 are what the vendor actually sells, even when the piece
    also drops elsewhere, e.g. Judgement from the L19 boss Munster Chuck),
    else the real-source cap (item_source_max_level), else the global max
    level."""
    lvls = item_vendor_levels(item_id)
    if lvls:
        return lvls[-1]
    sc = _data().get("scaling", {})
    max_level = sc.get("max_level") or 25
    return min(item_source_max_level(item_id) or max_level, max_level)


# The quality the Guild Merchants sell their stock at: the town vendors
# sell their gear as RARE, scaled to each town's item level (Tyrna L20 /
# Lower Ramburg L25). The starter weapons (Apprentice's Grimoire, Rusty
# Knives, ...) are authored Uncommon in the item data — that's the
# character-creation handout quality, not the shop's — so the items page
# displays the shop quality on gear a Guild Merchant sells.
_GUILD_MERCHANT_RARITY = "Rare"


@lru_cache(maxsize=None)
def item_display_rarity(item_id: str) -> str:
    """The rarity an item's tiles / header / badge should display: 'Rare'
    for gear a Guild Merchant sells (the town vendors' shop quality — the
    piece is bought at Rare, L20/L25, even when the base row is authored
    Uncommon, like the starter weapons), else the item's authored rarity.
    Non-gear merchant stock (materials, tools) keeps its authored rarity —
    the shop-quality claim only applies to gear."""
    it = _data().get("items", {}).get(item_id) or {}
    if has_guild_merchant(item_id) and (it.get("type") or "") in _GEAR_TYPES:
        return _GUILD_MERCHANT_RARITY
    return it.get("rarity") or ""





@lru_cache(maxsize=None)
def has_guild_merchant(item_id: str) -> bool:
    """True when any of the item's drop sources is a Guild Merchant (a
    WanderingMerchant NPC — the town shops). The town weapons carry the
    `_Craft` id token but are shop gear sold scaled to each town —
    item_fixed_level uses this to tell them apart from the genuinely
    crafted `_Craft` pieces."""
    if not item_id:
        return False
    it = _data().get("items", {}).get(item_id) or {}
    srcs = _sources()
    return any(_GUILD_MERCHANT_RE.match(
        (srcs[dr["s"]] if 0 <= dr["s"] < len(srcs) else {}).get("id") or "")
        for dr in it.get("drops", []))


@lru_cache(maxsize=1024)
def _dungeon_set_rows(item_id: str) -> list[dict]:
    """Real drop rows for a dungeon-set gear piece the scan has no rows
    for, derived from the game's WorldLoot/UpgradeRare token mechanics:
    one row per faction crate and per faction-mob group (each scaled by
    the piece's pool share), plus the Upgrade Items activity row. [] when
    the id names no dungeon set (or it's a Demon piece)."""
    m = _DUNGEON_SET_RE.search(item_id or "")
    if not m:
        return []
    faction = m.group(1).title()
    it = _data().get("items", {}).get(item_id) or {}
    pool = (_CRAFT_POOL_SIZE
            if (it.get("faction") or "").lower() == "craft"
            else _DUNGEON_POOL_SIZES.get(faction))
    if not pool:
        return []
    share = 1.0 / pool
    rows: list[dict] = []
    for r in resolve_drops("WorldLoot"):
        if r["kind"] == "chest":
            # only the faction crates — World/Demon crates roll other pools
            if r.get("source_id") not in _FACTION_CRATE_IDS:
                continue
        elif r["kind"] == "unit" and r["table"] in _FACTION_MOB_TABLES:
            # the faction mobs that roll WorldLoot, grouped per faction
            # (merge_drops then collects every mob zone into one row)
            r = dict(r, source=f"{r['table']} mobs")
        else:
            continue
        rows.append(dict(r, prob=r["prob"] * share, rift=False))
    # UpgradeRare: rolled at 1.0 by upgrade activities / dungeon crates
    rows.append({
        "source": "Upgrade Items", "kind": "chest",
        "loc": "Upgrade activities · boss chests",
        "table": "UpgradeItems_Activity",
        "prob": 1.0 / _UPGRADE_RARE_POOL_SIZE,
        "amount": None, "cost": None, "rolls": 1, "rift": False,
        "source_id": "UpgradeItems_Activity",
    })
    return rows


def human_loc(loc: str | None) -> str | None:
    """The human-readable name of a drop location.

    The scan records raw strings full of internal ids — 'Z1_Primevalley_Hub
    (Navelin) | (-342.7,1415.0)' or '... | entrances: POI_Rift_Entrance_1@
    Z2_Krisomal_North' — this strips the zone ids, POI ids and coordinates
    and keeps the readable names ('Navelin', 'East Majoram Bridge', 'Demon
    crates (soulstone events)', 'Abandoned Mines · Aurock Mound'). None when
    nothing human remains (unknown location / bare coordinates), so callers
    can skip the loc line entirely.
    """
    if not loc or loc.strip().lower() == "unknown location":
        return None
    s = _COORD_RE.sub("", loc)
    out = []
    for seg in s.split("|"):
        seg = seg.strip()
        if not seg:
            continue
        has_zone = bool(_ZONE_ID_RE.search(seg))
        seg = _ZONE_ID_RE.sub("", seg).strip(" |")
        if not seg or "POI_" in seg:
            continue
        if has_zone:
            m = re.search(r"\(([^()]*)\)", seg)
            if m:
                seg = m.group(1).strip()
        if seg and seg not in out:
            out.append(seg)
    return " · ".join(out) if out else None


def _dedupe_locs(locs: list[str]) -> list[str]:
    """Drop location names that are the tail of another kept name — the
    current scan emits the bare zone AND the dungeon-joined form for the
    same spot ('Rootbee Cave' inside 'Bee Hive · Rootbee Cave'), and the
    merged row should show the specific one once, not both."""
    out: list[str] = []
    for loc in locs or []:
        if not loc:
            continue
        if any(o != loc and o.lower().endswith(" " + loc.lower())
               for o in locs):
            continue
        if loc not in out:
            out.append(loc)
    return out


def merge_drops(item_id: str) -> list[dict]:
    """Drop sources merged per SOURCE for the item page.

    resolve_drops emits one row per source + location, so a vendor that sells
    at three hubs (Mira, Demon Huntress) or a mob that spawns across zones
    shows as several near-identical rows. This merges them into one row per
    source, collecting the human location names. Vendor rows split per
    VENDOR when their offers differ — the Guild Merchants sell the starter
    gear at different prices per town (Tyrna 1000 / Lower Ramburg 2000 Gold)
    and the town weapons at different town levels (L20 / L25), so those
    rows stay separate with each town's own price + level; vendors whose
    offers match (Mira's three hubs) collapse into one row. Each row:
    {source, source_id, kind, table, prob (the per-location chance, not a
    sum), amount, cost, rolls, rift, boss, hub_level (the town's item level
    for Guild Merchant rows, else None), shop_price (what a recorded
    MerchantUI counter charged, else None), shop_qty, locs (human names,
    deduped), n_locs (distinct raw locations merged)}. Sorted with
    rift/dungeon sources first, then by chance descending.
    """
    agg: dict[tuple, dict] = {}
    for d in resolve_drops(item_id):
        if d["kind"] == "npc":
            key = (d["source"], d["kind"], d.get("source_id", ""))
        else:
            key = (d["source"], d["kind"])
        r = agg.setdefault(key, {
            "source": d["source"], "kind": d["kind"],
            "table": d.get("table") or "",
            "prob": 0.0, "amount": d.get("amount"), "cost": d.get("cost"),
            "rolls": 1, "rift": False, "derived": False, "quiet": False,
            "boss": False, "source_id": d.get("source_id", ""),
            "hub_level": (_TOWN_ITEM_LEVELS.get(d.get("source_id") or "")
                           if d["kind"] == "npc" else None),
            "dungeon": None, "locs": [], "locs_raw": set(),
            # carried through so the page can tell a merchant sale (which
            # names its charge) from a guild stall (which sells at Rare
            # quality and no price)
            "shop_price": d.get("shop_price"),
            "shop_qty": d.get("shop_qty") or 1,
            # and the coordinates behind the location line, for a caller that
            # wants to PLACE the vendor instead of naming their town
            "vendor_pos": d.get("vendor_pos") or {},
        })
        if d.get("quiet"):
            r["quiet"] = True
        if r["shop_price"] is None:
            r["shop_price"] = d.get("shop_price")
        r["prob"] = max(r["prob"], d["prob"])
        if d.get("derived"):
            r["derived"] = True
        if d.get("boss"):
            r["boss"] = True
        if not r["source_id"]:
            r["source_id"] = d.get("source_id", "")
        if r["amount"] is None:
            r["amount"] = d.get("amount")
        if r["cost"] is None:
            r["cost"] = d.get("cost")
        r["rolls"] = max(r["rolls"], d.get("rolls", 1))
        if d["rift"]:
            r["rift"] = True
        r["locs_raw"].add(d["loc"] or "")
        hl = human_loc(d["loc"])
        if hl and hl not in r["locs"]:
            r["locs"].append(hl)

    for r in agg.values():
        r["n_locs"] = len(r["locs_raw"])
        del r["locs_raw"]
        # the boss's dungeon name follows the merged source (its icon/name
        # sub-line), resolved once per source rather than copied per row
        if r["boss"]:
            r["dungeon"] = _boss_dungeon_name(r["source_id"])
    rows = list(agg.values())
    # vendor rows whose offers match EXACTLY (same price + same town level)
    # collapse back into one row — Mira's three hubs are one offer repeated,
    # so they merge; the Guild Merchants' per-town rows keep their own price
    # and level
    final: list[dict] = []
    by_offer: dict[tuple, dict] = {}
    for r in rows:
        if r["kind"] == "npc":
            offer = (r["source"],
                     tuple((c["kind"], c["amount"])
                           for c in (r["cost"] or ())),
                     r.get("hub_level"), r.get("shop_price"))
            t = by_offer.get(offer)
            if t is not None:
                for loc in r["locs"]:
                    if loc not in t["locs"]:
                        t["locs"].append(loc)
                t["n_locs"] += r["n_locs"]
                t["rolls"] = max(t["rolls"], r["rolls"])
                continue
            by_offer[offer] = r
        final.append(r)
    rows = final
    for r in rows:
        r["locs"] = _dedupe_locs(r["locs"])
    rows.sort(key=lambda r: (not r["rift"], -r["prob"], r["source"].lower()))
    return _with_cache_row(item_id, rows)


def _with_cache_row(item_id: str, rows: list[dict]) -> list[dict]:
    """Append the BOX a piece is cached in, as its own Drops From row.

    The scan never records the container itself (every box's `drops` is
    empty), so a piece that only ever comes out of one reported "no drop
    sources" while its own page said which box it lived in. The box is the
    real acquisition, so it belongs in the list.

    Only appended when the piece has no OTHER source: a Nightling piece that
    already shows 'Mira, Demon Huntress - 10 Nightblood' keeps that row and
    does not also get the box, or the same fact would be listed twice. A box
    with no recorded source is flagged `unreleased` so the row can say so.
    """
    if rows:
        return rows
    cache = _cache_by_item().get(item_id)
    if not cache:
        return rows
    spec = heroic_cache_spec().get(cache) or {}
    return [{
        "source": spec.get("name") or cache,
        "kind": "cache",
        "table": spec.get("table", ""),
        "prob": 0.0,
        "amount": None,
        "cost": None,
        "rolls": 1,
        "rift": cache.startswith("Rift_"),
        "derived": True,
        "quiet": False,
        "boss": False,
        "source_id": cache,
        "hub_level": None,
        "dungeon": None,
        "locs": [],
        "n_locs": 0,
        "unreleased": cache_is_unreleased(cache),
    }]

@lru_cache(maxsize=None)
def upgrade_locked(item_id: str) -> bool:
    """True when a piece's UPGRADE LADDER must render base-only.

    ALL armor (Chest, Legs, Head, Feet, Waist, Hands, Shoulders, Back) and
    ALL jewelry (Ring / Neck / Trinket — the Accessory slots) render base-only.
    Weapons upgrade at Rare/Epic/Legendary; armor's Epic +1..+4 path is
    authored in the rarity sheet but the live game has no way to apply it yet,
    so showing those step gains would advertise upgrades a player cannot buy
    (live 2026-10-05: "epic gear cant be upgraded yet"). Like crafted gear,
    every locked piece hides its step gains and shows only the base stats.
    Jewelry also only ever drops at its authored zone-tier level (a max drop
    level, never scaled up), so the base value there is the whole story.
    Source-agnostic: boss drops, faction crates, vendor caches (Smile of the
    Demolisher from the Chaotic Gear Cache) and town sales are all locked the
    same way. When Epic armor upgrades ship, narrow this back to the
    own-rarity check."""
    if not item_id:
        return False
    it = _data().get("items", {}).get(item_id) or {}
    cat = category(it.get("type") or "")
    if cat == "Accessory":
        return True
    if cat != "Armor":
        return False
    # Armor is base-only for now: non-Epic armor never had an upgrade path, and
    # Epic armor's +1..+4 path is authored in the sheet but not live in-game,
    # so its steps must not render as reachable. This is the one place the
    # detail page's `show_steps` gate reads, so the Armor tab's UPGRADE LADDER
    # stays base-only for every piece. Narrow this when Epic upgrades ship.
    return True


def armor_locked(item_id: str) -> bool:
    """Deprecated alias of upgrade_locked — kept for callers that predate
    the jewelry lock (armor and jewelry render base-only the same way)."""
    return upgrade_locked(item_id)


_MOB_FACTION_RE = re.compile(r"^([A-Za-z]+)_")


def _group_mob_factions(rows: list[dict]) -> list[dict]:
    """Collapse the per-mob unit rows into one cell per faction so a plain
    drop list doesn't flood with every mob variant (Cloth_Z1's 131 mob
    rows become 'Crimson mobs', 'Manfish mobs', ...). A faction only groups
    when it has MORE than one member — a lone named mob (Hunter Shepherd)
    and the TODO placeholder rows keep their own names. The group takes
    its first member's place in the list, merges the members' locations
    (deduped) and keeps the max per-kill chance / rolls; its `source_id` is
    the bare faction (no underscore), so the cell renders the MOB sword
    marker rather than a single mob's sprite."""
    counts: dict[str, int] = {}
    for r in rows:
        if r["kind"] != "unit" or r.get("boss"):
            continue
        m = _MOB_FACTION_RE.match(r.get("source_id") or "")
        if m and m.group(1) != "TODO":
            counts[m.group(1)] = counts.get(m.group(1), 0) + 1
    out: list[dict] = []
    groups: dict[str, dict] = {}
    for r in rows:
        m = _MOB_FACTION_RE.match(r.get("source_id") or "") if \
            r["kind"] == "unit" and not r.get("boss") else None
        fac = m.group(1) if m and m.group(1) != "TODO" else ""
        if fac and counts[fac] > 1:
            g = groups.get(fac)
            if g is None:
                g = dict(r, source=f"{fac} mobs", source_id=fac,
                         locs=[], n_locs=0)
                groups[fac] = g
                out.append(g)
            g["prob"] = max(g["prob"], r["prob"])
            g["rolls"] = max(g["rolls"], r.get("rolls", 1))
            for loc in r.get("locs") or ():
                if loc not in g["locs"]:
                    g["locs"].append(loc)
            g["n_locs"] = len(g["locs"])
            if r.get("rift"):
                g["rift"] = True
            continue
        out.append(r)
    return out


@lru_cache(maxsize=1)
def _table_unit_rollers() -> dict[str, frozenset[str]]:
    """Loot table -> every unit source id recorded rolling it, across all
    raw item_drops rows. The data behind the faction-family boss expansion:
    a set piece's own-faction dungeons are the ones whose boss or mobs roll
    the same table its recorded boss rolls (Bee Hive AND Mokshis Hivetree
    AND Cleodoras Nest units all roll the Bee table, so bee gear lists all
    three bosses)."""
    srcs, tbls = _sources(), _tables()
    out: dict[str, set] = {}
    for it in _data().get("items", {}).values():
        for dr in it.get("drops", []):
            if not 0 <= dr["t"] < len(tbls):
                continue
            s = srcs[dr["s"]] if 0 <= dr["s"] < len(srcs) else {}
            sid = s.get("id")
            if s.get("kind") == "unit" and sid:
                out.setdefault(tbls[dr["t"]],
                               set()).add(s["id"])
    return {t: frozenset(v) for t, v in out.items()}


@lru_cache(maxsize=None)
def _family_boss_rows(boss_tables: frozenset[str]) -> tuple[dict, ...]:
    """Synthesized boss rows for a piece's own-faction dungeons: every
    dungeon whose boss or mobs roll one of `boss_tables` (the loot tables
    the piece's recorded boss rolls) contributes its boss row — icon +
    name + dungeon sub-line. Signature tables (a boss's own weapon list)
    roll nothing but that boss, so weapons keep just their own boss;
    everything here is derived from item_drops.json + dungeons.json."""
    from .. import dungeons
    rollers = _table_unit_rollers()
    out: list[dict] = []
    for d in dungeons.load_dungeons():
        bid = d.get("boss_id")
        if not bid:
            continue
        units = set(d.get("mobs") or ())
        units.add(bid)
        if not any(units & rollers.get(t, frozenset()) for t in boss_tables):
            continue
        out.append({
            "source": d.get("boss_name") or bid,
            "source_id": bid,
            "kind": "unit",
            "table": "",
            "prob": 0.0,
            "amount": None,
            "cost": None,
            "rolls": 1,
            "rift": d.get("entrance_zone") == "Rifts",
            "derived": True,
            "quiet": False,
            "boss": True,
            "source_id": bid,
            "hub_level": None,
            "dungeon": _boss_dungeon_name(bid) or d.get("ingame_name"),
            "locs": [],
            "n_locs": 0,
            "_lvl": d.get("level") or 0,
        })
    out.sort(key=lambda r: (r["_lvl"], r["source"].lower()))
    for r in out:
        del r["_lvl"]
    return tuple(out)


@lru_cache(maxsize=None)
def _shown_drops_raw(item_id: str) -> list[dict]:
    """The merged + grouped Drops From rows for an item, cached per id.

    The page build resolves the same ids repeatedly (the listable filter,
    the refilter), and the drops math (merge_drops -> resolve_drops) is
    the build's second-largest cost after the icons, so the resolution is
    computed once per item."""
    rows = merge_drops(item_id)
    bosses = [r for r in rows if r.get("boss")]
    if not bosses:
        return _group_mob_factions(rows)
    it = _data().get("items", {}).get(item_id) or {}
    if (it.get("type") or "") in _GEAR_TYPES:
        # GEAR: the dungeon bosses ARE the drop info — one row per dungeon
        # of the piece's own faction (its recorded boss leads, then the
        # other same-table dungeons' bosses). The shared crate / mob /
        # upgrade rows every set piece carries never make the list.
        fam = _family_boss_rows(frozenset(
            r.get("table") or "" for r in bosses))
        known = {r["source_id"] for r in bosses}
        return bosses + [r for r in fam if r["source_id"] not in known]
    return _group_mob_factions(rows)


def has_drops(item_id: str) -> bool:
    """True when the item has any Drops From rows.

    The cheap truthiness mirror of shown_drops: crafted gear never drops,
    raw drop rows mean the resolution is non-empty, and items the scan
    recorded no rows for still derive from the WorldLoot/UpgradeRare tokens
    (_dungeon_set_rows) — the exact non-emptiness rule resolve_drops
    implements, without the per-row merge/boss/location work. The page
    build asks this ~300 times (is_listable_item), so it must not deep-copy
    or resolve full rows."""
    if not item_id:
        return False
    it = _data().get("items", {}).get(item_id) or {}
    if _is_crafted(item_id):
        return False
    return bool(it.get("drops")) or bool(_dungeon_set_rows(item_id))


def shown_drops(item_id: str) -> list[dict]:
    """The Drops From rows the item pages show.

    WorldLoot zone gear (zone sets + jewelry) carries its clean four-source
    shape NATIVELY in the data — the scan emits the crate, the faction mob
    unit groups on one line, Unique Foes and Zone activities (quiet rows,
    no tooltip detail, no zone sub-lists). No runtime collapse needed. GEAR
    with a boss source shows DUNGEON BOSSES ONLY: its recorded boss leads,
    followed by every other dungeon of its faction (dungeons whose boss or
    mobs roll the same loot table — see _family_boss_rows). Non-gear items
    (craft materials like Veiled Wing, Demonic Horn) keep the full list —
    the mobs are their farmable sources, with the boss leading. Everything
    else groups its per-mob rows per faction (see _group_mob_factions) so
    the list stays short.

    The resolution is cached (_shown_drops_raw), but the Drops From
    renderers merge node variants IN PLACE on the returned rows, so each
    call hands back a fresh deep copy — the cache never shares row dicts
    between two renderers."""
    return copy.deepcopy(_shown_drops_raw(item_id))


def is_rift_location(loc: str | None) -> bool:
    """True for rift / soulstone / endgame-dungeon location labels — the
    sources the item page flags as the real info."""
    if not loc:
        return False
    return bool(_RIFT_LOC_RE.search(loc) or _DUNGEON_LOC_RE.search(loc))


# ---------------------------------------------------------------------------
# Vendor stock — read straight from the drops index
# ---------------------------------------------------------------------------
# A merchant's counter is not a separate observation any more. The drops index
# already carries one npc row per (vendor, item, town) a shop sells, WITH the
# currency it charges, because the same data that authors a shop authors its
# rows. The old `npc_shop.json` MerchantUI sheet duplicated every one of those
# rows and could name no currency; it is gone, and so is the resolution
# machinery that put its display-derived ids back on real items.
#
# What still needs a home is the per-vendor GROUPING the Merchants view is
# built from — "what does each counter carry" — which a flat Drops From list
# cannot answer, because two shops' rows would interleave.

#: The `kind` an offer that is a critter-unit (a pet) carries. Kept for API
#: compatibility with the old sheet; the index resolves pets as items, so it is
#: no longer stamped at runtime.
CRITTER_KIND = "critter"


def vendor_display_name(vendor: str) -> str:
    """The vendor's name, as the drops index already writes it.

    The index stores display names (`Perina Wann`, `Mira, Demon Huntress`),
    not scanner-shaped ids, so this is the identity for every vendor it
    carries and never invents one.
    """
    return str(vendor or "").strip()


def vendor_charge(row: dict) -> int | None:
    """What the counter charges: the cost amount the index records, else the
    bare `price`, else None. The currency rides alongside on the row's `cost`."""
    for c in (row or {}).get("cost") or ():
        if isinstance(c, dict) and isinstance(c.get("amount"), (int, float)):
            return int(c["amount"])
        if isinstance(c, (list, tuple)) and len(c) >= 2 \
                and isinstance(c[1], (int, float)):
            return int(c[1])
    for key in ("price", "scaled_price"):
        val = (row or {}).get(key)
        if isinstance(val, (int, float)):
            return int(val)
    return None


@lru_cache(maxsize=1)
def vendor_stock() -> tuple[dict, ...]:
    """Every merchant offer the drops index records, one row per
    (vendor, item): {item, name, vendor, source_id, rarity, category, level,
    qty, cost, price, scaled_price, kind, codex, locs}.

    Built from the item sheet's own npc drop rows — the same rows
    `resolve_drops` returns — grouped so several towns selling one item become
    one offer carrying several locations. `cost` is the index's currency row
    (`[{kind, amount}]`); `price` is that amount, `scaled_price` None (the
    index records no scaled price).
    """
    from . import catalog

    srcs, locs = _sources(), _locs()
    out: dict[tuple[str, str], dict] = {}
    for iid, it in (_data().get("items") or {}).items():
        for dr in it.get("drops") or []:
            s = srcs[dr["s"]] if 0 <= dr["s"] < len(srcs) else {}
            if s.get("kind") != "npc":
                continue
            vendor = s.get("name") or s.get("id") or ""
            li = dr["l"] if 0 <= dr["l"] < len(locs) else None
            loc = locs[li] if li is not None else _UNKNOWN_LOC
            row = out.get((vendor, iid))
            if row is None:
                cat = catalog.item(iid) or {}
                cost = list(dr.get("cost") or ())
                amount = None
                for c in cost:
                    if isinstance(c, dict) \
                            and isinstance(c.get("amount"), (int, float)):
                        amount = int(c["amount"])
                        break
                row = {
                    "item": iid,
                    "name": cat.get("name") or iid,
                    "vendor": vendor,
                    "source_id": s.get("id") or "",
                    "rarity": str(cat.get("rarity") or ""),
                    "category": str(cat.get("type") or ""),
                    "level": cat.get("level"),
                    "qty": 1,
                    "cost": cost,
                    "price": amount,
                    "scaled_price": None,
                    "kind": "npc",
                    "codex": None,
                    "locs": [],
                }
                out[(vendor, iid)] = row
            if loc and loc not in row["locs"]:
                row["locs"].append(loc)
    # The Heroic cache counters. A cache BOX carries no `drops` row of its own
    # in the sheet (`HM_*_Cache_25` is `drops: []`), so the index alone never
    # shows the vendor that sells it — the Medal of Glory trader (Shiro James)
    # had no counter here at all. Its offer comes from `cache_vendors()`, the
    # SAME recorded/derived seller the item page's Drops From uses, so this
    # view and the item page can never disagree about who sells a box, and no
    # vendor is hand-listed here. Deduped against the index rows: Mira's Rift
    # caches already arrive from her own piece rows, so she keeps one entry
    # per box rather than two.
    for cid in heroic_cache_contents():
        spec = heroic_cache_spec().get(cid) or {}
        for v in cache_vendors(cid):
            who = v.get("source") or ""
            if not who or (who, cid) in out:
                continue
            cost = [c if isinstance(c, dict)
                    else {"kind": c[0], "amount": c[1]}
                    for c in (v.get("cost") or ())]
            amount = next((int(c["amount"]) for c in cost
                           if isinstance(c.get("amount"), (int, float))), None)
            out[(who, cid)] = {
                "item": cid,
                "name": cache_display_name(cid),
                "vendor": who,
                "source_id": v.get("source_id") or "",
                "rarity": str(spec.get("rarity") or ""),
                "category": "LootableContainer",
                "level": spec.get("level_max"),
                "qty": 1,
                "cost": cost,
                "price": amount,
                "scaled_price": None,
                "kind": "npc",
                "codex": None,
                "locs": list(v.get("locs") or ()),
            }
    rows = list(out.values())
    rows.sort(key=lambda r: (r["vendor"].lower(), r["name"].lower()))
    return tuple(rows)


def vendor_stock_for(item_id: str) -> tuple[dict, ...]:
    """The index's offers for one item. () when no vendor sells it."""
    if not item_id:
        return ()
    return tuple(r for r in vendor_stock() if r["item"] == item_id)


def has_vendor_stock(item_id: str) -> bool:
    """True when a merchant in the index offers this item."""
    return bool(vendor_stock_for(item_id))


@lru_cache(maxsize=1)
def shop_item_ids() -> frozenset[str]:
    """Shop-ONLY items: a counter sells them and nothing else obtains them.

    The cash-shop / premium set has always meant "you can only buy this" —
    cosmetic gear with no drop row (`catalog.is_shop_item`). The index's own
    marker for that counter is the `Shop` loot table: a mount/glider sold at a
    counter carries a `Shop` row, while a gear vendor's stock (Mira's
    `Rift_GearWithAffinity` pieces) is a different table and stays ordinary
    gear. So an item counts only when EVERY source it has is a `Shop`-table
    vendor row — a piece a vendor sells AND a boss drops keeps its drops and is
    not pulled out of the loot lists. Derived from the item data itself, so it
    replaces the old `raw_shop`/`shop.json` plumbing (retired 2026-10-05: the
    dump never shipped the sheet, so `raw_shop` was a placeholder and every
    read fell back to an id pattern).

    The derivation itself lives in `shop_item_ids_from_payload` so the compiler
    derives the identical set from the same JSON; this wrapper only supplies
    the live payload and its crafted-item set.
    """
    return shop_item_ids_from_payload(_data(), _crafted_ids())


def vendor_names() -> tuple[str, ...]:
    """Every vendor the index records, alphabetically."""
    return tuple(sorted({r["vendor"] for r in vendor_stock()}))


def vendor_name() -> str:
    """The vendor's name when the index records exactly one, else ""."""
    found = vendor_names()
    return found[0] if len(found) == 1 else ""


def vendor_scan_date(vendor: str = "") -> str:
    """'' — the drops index records no per-vendor scan time."""
    return ""


def unresolved_vendor_stock() -> tuple[dict, ...]:
    """None: every offer the index records names a real item, so nothing is
    left unresolved (the old sheet's display-derived rows were the only
    source of those)."""
    return ()


def unresolved_vendor_lists() -> tuple[dict, ...]:
    """The per-vendor shape of `unresolved_vendor_stock()` — always ()."""
    return ()


def codex_entry_for(row: dict) -> dict | None:
    """The codex entry an offer refers to, or None.

    Kept for the Merchants view's unread block: an offer whose name no ITEM
    sheet carries may still be a codex unit (a pet or critter), and this is
    what lets the view say where it lives instead of calling it unnamed.
    """
    from .. import codex

    label = str((row or {}).get("name") or "").strip()
    if not label:
        return None
    uid = codex.find_by_name(label)
    if not uid:
        return None
    entry = codex.enemies_data().get(uid) or {}
    return {"id": uid,
            "name": str(entry.get("name") or label),
            "region": codex.find_unit_region(uid) or ""}


def _stock_lists(rows: tuple[dict, ...]) -> tuple[dict, ...]:
    """`rows` grouped per vendor, each as {vendor, scanned_at, source, rows,
    n_items, towns}. `towns` are the readable places the offers resolve to,
    deduped, sorted by vendor name."""
    by_vendor: dict[str, list[dict]] = {}
    for row in rows:
        by_vendor.setdefault(row["vendor"], []).append(row)
    out: list[dict] = []
    for name, group in sorted(by_vendor.items()):
        towns: list[str] = []
        for r in group:
            for loc in r.get("locs") or ():
                town = human_loc(loc)
                if town and town not in towns:
                    towns.append(town)
        out.append({"vendor": name, "scanned_at": "", "source": "",
                    "rows": tuple(group), "n_items": len(group),
                    "towns": tuple(towns)})
    return tuple(out)


def vendor_stock_lists() -> tuple[dict, ...]:
    """Every index vendor's stock as one entry each — the accessor that keeps
    the vendors apart for the Merchants view."""
    return _stock_lists(vendor_stock())


def vendor_sections() -> tuple[dict, ...]:
    """ONE entry per index vendor: their offers, plus the (always empty) pet
    and unread blocks the view still renders."""
    return tuple({**e, "pet_rows": (), "n_pets": 0,
                  "unread_rows": (), "n_unread": 0}
                 for e in vendor_stock_lists())


def with_recorded_shop(rows: list[dict], item_id: str) -> list[dict]:
    """`rows` plus this item's vendor rows from the index, skipping ones
    already there.

    The Items page prefers a mount/glider's CODEX card over the index (the
    card authors the exact vendors and mobs; the index only infers loot-table
    families). A card cannot hold a counter, though: the Riftstalkers'
    Nightdog card lists eight mobs and no vendor, so letting the card replace
    the index would hide the sale. Appended, never substituted, so the card
    keeps its say about what drops the piece.
    """
    have = {(r.get("source"), r.get("kind")) for r in rows}
    return list(rows) + [r for r in resolve_drops(item_id)
                         if r.get("kind") == "npc"
                         and (r.get("source"), "npc") not in have]


def resolve_drops(item_id: str) -> list[dict]:
    """Resolved drop rows for an item, deduped and sorted.

    Rows with the same source + location (e.g. a mob that rolls two loot
    tables) are merged by summing probability. Rift/dungeon locations come
    first, then alphabetical location, then chance descending. Each row:
    {source, kind, loc, table, prob, amount, cost, rolls, rift, boss} —
    `boss` flags named-boss sources so the item page can headline them
    with their unit icon + name. `amount` is a "min-max" string (or a bare
    count) when the scan recorded one, `cost` is the vendor price as
    [{kind, amount}] (currency items), and `rolls` is how many times the
    table rolls.

    Crafted gear keeps only its npc rows — the scan stamped the base set's
    drop rows on the crafted variants too (Beekeeper's Scarf and the other
    `_Craft` pieces share the Bee Hive crate / boss rows), but crafted pieces
    are made by recipes, never dropped, so their Drops From must not list
    dungeon sources.
    """
    srcs, locs, tbls = _sources(), _locs(), _tables()
    it = _data().get("items", {}).get(item_id) or {}
    agg: dict[tuple[str, int], dict] = {}
    for dr in it.get("drops", []):
        s = srcs[dr["s"]] if 0 <= dr["s"] < len(srcs) else {}
        li = dr["l"] if 0 <= dr["l"] < len(locs) else None
        key = (s.get("id", "?"), li if li is not None else -1)
        row = agg.setdefault(key, {
            "source": s.get("name") or s.get("id", "?"),
            "source_id": s.get("id", ""),
            "kind": s.get("kind", ""),
            "loc": locs[li] if li is not None else _UNKNOWN_LOC,
            "table": tbls[dr["t"]] if 0 <= dr["t"] < len(tbls) else "",
            "prob": 0.0,
            "amount": None,
            "cost": None,
            "rolls": 1,
            "quiet": bool(dr.get("q")),   # collapsed WorldLoot rows
        })
        row["prob"] += dr.get("p", 0.0)
        if row["amount"] is None and (dr.get("min") is not None
                                       or dr.get("max") is not None):
            lo, hi = dr.get("min"), dr.get("max")
            if lo is None:
                lo = hi
            if hi is None:
                hi = lo
            row["amount"] = (lo, hi)
        if row["cost"] is None and dr.get("cost"):
            row["cost"] = dr["cost"]
        if dr.get("r", 1) > 1:
            row["rolls"] = dr["r"]
    rows = []
    for v in agg.values():
        # the training-dummy family (PunchingBag / its invulnerable,
        # shielded, magic-resist and armored variants) rolls the table in
        # the scan but is not a farmable source — never a real drop row
        if v["kind"] == "unit" and (v["source_id"] or "").startswith(
                "PunchingBag"):
            continue
        amt = v["amount"]
        amount = None
        if amt:
            amount = f"{amt[0]}-{amt[1]}" if amt[0] != amt[1] else str(amt[0])
        loc = v["loc"]
        # Rift bosses: the current scan stores the arena's raw coordinates
        # ('(198,-210)'), which no longer reads as a rift location. The boss
        # still sits in a rift dungeon (dungeons.json knows it), so resolve
        # the readable rift name whenever the loc ISN'T already human —
        # otherwise the row loses its rift flag and sorts behind vendors.
        if v["kind"] == "unit" and ((loc or "").strip().lower() == _UNKNOWN_LOC
                                    or is_rift_location(
                                        _resolve_unknown_loc(
                                            v["source_id"], v["source"]))):
            loc = _resolve_unknown_loc(v["source_id"], v["source"]) or loc
        boss = v["kind"] == "unit" and _is_boss(v["source_id"])
        rows.append({"source": v["source"], "source_id": v["source_id"],
                     "kind": v["kind"], "loc": loc,
                     "table": v["table"], "prob": v["prob"], "amount": amount,
                     "cost": v["cost"], "rolls": v["rolls"],
                     "quiet": v.get("quiet", False),
                     "rift": is_rift_location(loc),
                     "boss": boss,
                     "dungeon": _boss_dungeon_name(v["source_id"]) if boss
                     else None})
    # Crafted pieces carry the base set's drop rows (a scan artifact) but are
    # made by recipes, never dropped — only a merchant's counter is real
    # evidence for them, so keep the vendor rows and drop the rest.
    if _is_crafted(item_id):
        rows = [r for r in rows if r.get("kind") == "npc"]
    elif not rows:
        # the scan has no rows for the dungeon gear sets — the game hands
        # them out at runtime via the WorldLoot/UpgradeRare tokens, so the
        # real per-source chances are derived here instead of reporting
        # 'no drop sources'. (The Epic `_E<Faction>_` set pieces' heroic
        # boss edge is NOT derived here: the compiler stamps it onto the
        # drops index from the same lootTable sheet — see
        # `rules.heroic_boss_items` / compiler._stamp_heroic_boss_edges —
        # so every consumer of the index sees it.)
        rows.extend(_dungeon_set_rows(item_id))
    rows.sort(key=lambda r: (not r["rift"], r["loc"].lower(), -r["prob"]))
    return rows


# Loot tables that drop gear, grouped by what rolls them: the rift vendor's
# affinity tables (Rift_GearWithAffinity / Rift_WeaponWithAffinity), the Shop
# table (guild vendors), the per-faction dungeon-set tables (Bee/Kobold/
# Manfish/Crimson/Demon — every set piece shares one), the named-boss
# signature tables (Maat, Reblochonk, ...), and the generic world tables
# (Clothes / Leather). Gear that shares a table is the "same mob type of
# dungeon drop" — filterable together on the gear tab.
_FACTION_SET_TABLES = ("Bee", "Kobold", "Manfish", "Crimson", "Demon")

# The Nightling family (item-sheet faction id "Demon") is the one whose
# armour set is NOT carried by a per-faction dungeon table: its Rare pieces
# are sold from Mira's Chaotic Gear caches and ride the RIFT affinity table,
# so the group's shared pool reads from that table instead of the boss-roller
# table. The item `faction` field stays the membership filter, so only the
# Nightling pieces come out of the (multi-faction) rift table.
_FACTION_GEAR_TABLE = {"Demon": "Rift_GearWithAffinity"}


@lru_cache(maxsize=1)
def _boss_signature_tables() -> frozenset[str]:
    """Loot tables that are a named boss's signature set: every gear piece
    in the table references it only through boss sources. The faction set
    tables (Bee/Kobold/...) fail the check — dozens of mobs roll them, so
    they're not boss sets even though the faction boss rolls them too.
    Several bosses may share one table (Nightgod and Nightking Maat Demon
    both roll Maat's) — what matters is that NO non-boss source does."""
    srcs: dict[str, set[str]] = {}
    tbls = _tables()
    src_arr = _sources()
    for row in _data().get("items", {}).values():
        if (row.get("type") or "") not in _GEAR_TYPES:
            continue
        for dr in row.get("drops", []):
            if not 0 <= dr["t"] < len(tbls):
                continue
            s = src_arr[dr["s"]] if 0 <= dr["s"] < len(src_arr) else {}
            srcs.setdefault(tbls[dr["t"]], set()).add(s.get("id", "?"))
    return frozenset(t for t, s in srcs.items()
                     if s and all(_is_boss(x) for x in s))


_TABLE_GROUPS = (
    ("Rift", lambda t: t.startswith("Rift_")),
    ("Vendor", lambda t: t == "Shop"),
    ("Faction", lambda t: t in _FACTION_SET_TABLES),
    ("Boss", lambda t: t in _boss_signature_tables()),
    ("World", lambda t: True),
)


@lru_cache(maxsize=1)
def _gear_table_index() -> dict[str, list[str]]:
    """Loot table -> sorted gear item ids that drop from it (raw rows)."""
    tbls = _tables()
    idx: dict[str, set] = {}
    for iid, it in _data().get("items", {}).items():
        if (it.get("type") or "") not in _GEAR_TYPES:
            continue
        for dr in it.get("drops", []):
            # rows without a table (the collapsed Zone activities group) are
            # not "from" any named table — skip the empty name so it never
            # becomes a filter entry
            if 0 <= dr["t"] < len(tbls) and tbls[dr["t"]]:
                idx.setdefault(tbls[dr["t"]], set()).add(iid)
    return {t: sorted(s) for t, s in idx.items()}


def gear_tables() -> list[dict]:
    """Loot tables that drop gear, each {id, count, group} — Rift, Vendor,
    Faction, Boss and World groups, bosses alphabetical within their group.
    This is the gear tab's loot-table filter: picking one shows every piece
    that shares that table (same mob type / same boss / faction set / rift
    set)."""
    idx = _gear_table_index()
    out = []
    for t, ids in idx.items():
        group = next(g for g, fn in _TABLE_GROUPS if fn(t))
        out.append({"id": t, "count": len(ids), "group": group})
    order = {"Rift": 0, "Vendor": 1, "Faction": 2, "Boss": 3,
             "World": 4}
    out.sort(key=lambda r: (order[r["group"]], r["id"].lower()))
    return out


# shared-set tile order for the Dungeons tab (slot progression, jewelry last)
# HAND-FACT: _SLOT_ORDER
_SLOT_ORDER = ("Head", "Shoulders", "Chest", "Hands", "Waist", "Legs",
               "Feet", "Back", "GearFinger", "GearNeck", "GearTrinket")


def _set_sort_key(iid: str) -> tuple:
    """Slot order, then name — the shared set's reading order."""
    it = _data().get("items", {}).get(iid) or {}
    typ = it.get("type")
    return (_SLOT_ORDER.index(typ) if typ in _SLOT_ORDER else 99,
            (item_label(iid) or iid).lower())


@lru_cache(maxsize=None)
def _epic_set_ids(faction: str) -> tuple[str, ...]:
    """A faction's Epic dungeon set: the `_E<Faction>` twins of the shared
    Rare set (Head_EKobold_*, Chest_EManfish_*, ...). They are NOT in the
    shared pool — they roll the bosses' HEROIC tables (`*_HM`) instead
    of the world/crate pool every dungeon shares."""
    out = []
    for iid, it in _data().get("items", {}).items():
        if f"_E{faction}_" not in iid or _is_crafted(iid):
            continue
        if (it.get("faction") or "").lower() != faction.lower():
            continue
        if category(it.get("type")) == "Weapons":
            continue
        if item_display_rarity(iid) != "Epic":
            continue
        out.append(iid)
    return tuple(sorted(out, key=_set_sort_key))


def epic_pieces(group: dict) -> tuple[str, ...]:
    """Every Epic piece belonging to one family: what its own dungeons'
    bosses guarantee on their `_HM` tables, plus whatever has no dungeon
    source at all (a cache, or another family's boss — neither of which
    belongs on the Dungeons page). Deduplicated.

    The single place to read, so the gear list (`@dungeon`), the Collection
    Manager and the per-rarity chip counts can never disagree about which
    Epic pieces belong to a family — a piece added here for one of them and
    forgotten in the other two would read as 'Epic 24' in a count and 20
    tiles on screen.
    """
    out: list[str] = []
    for b in group.get("bosses") or ():
        out.extend(b.get("epic") or ())
    out.extend(group.get("epic_unknown") or ())
    return tuple(dict.fromkeys(out))


@lru_cache(maxsize=1)
def _heroic_tables() -> dict[str, tuple[str, ...]]:
    """`<BossId>_HM` loot table id -> the items that table rolls.

    Each heroic (Hardmode) boss's guarantee, named after the BOSS, read from
    the same `lootTable` sheet the caches come from. This is where the
    `*_HM` tables live: twelve of the fourteen bosses have one, and each
    rolls exactly six of its family's Epic set pieces (plus that family's
    Epic trinket for four of them). The two without one are the Nightling
    bosses — Maat and Shaarlize — whose set is the Chaotic Gear Cache
    instead (see epic_cache).

    The sheet is the ground truth, not the scan: the live scan never
    captured the boss -> `_HM` edge, which is why every family's Epic set
    looked half unattributed even though the game always knew. Reading it
    costs nothing at runtime — the sheet is a compiled dict already in
    memory behind cdb's lru_cache, the same as the caches' own tables.
    """
    from .. import cdb
    return heroic_boss_items(cdb.lines("lootTable"))


@lru_cache(maxsize=None)
def _epic_set_by_boss(faction: str) -> dict[str, tuple[str, ...]]:
    """Boss id -> the Epic pieces THAT boss guarantees in heroic mode.

    Sheet first (`<boss_id>_HM`), unioned with whatever the scanned drop
    rows already resolved, so nothing that used to attribute stops doing so.

    The caller only ever reads a key that is one of ITS OWN family's bosses,
    so a boss of another family (Golcano is Kobold, and `Golcano_HM` rolls
    six CRIMSON pieces) is simply never asked for — his Crimson pieces stay
    in `epic_unknown` rather than being filed under a Kobold or a Crimson
    dungeon. A drop is only attributed where the dungeon it lives in is
    actually that item's family.

    Only the family's 24-piece SET is in scope. `Trinket_<Family>_E` is
    rolled by the same table but carries no `_E<Family>_` marker, so it is
    not a set piece and stays out — each family's Rare trinket is already
    in the shared pool, and adding the Epic twin to these blocks would put
    a second tile on a name the pane already draws in the other tier.
    """
    fac = faction.lower()
    setids = set(_epic_set_ids(faction))
    out: dict[str, list[str]] = {}
    for tid, items in _heroic_tables().items():
        bid = tid[:-3]
        for iid in items:
            it = _data().get("items", {}).get(iid) or {}
            if (it.get("faction") or "").lower() != fac:
                continue
            if category(it.get("type")) == "Weapons":
                continue
            if item_display_rarity(iid) != "Epic":
                continue
            if iid not in setids:
                continue          # the family's Epic trinket, not a set piece
            out.setdefault(bid, []).append(iid)
    for iid in setids:
        for src in {r.get("source_id") for r in resolve_drops(iid)
                    if r.get("source_id")}:
            if iid not in out.setdefault(src, []):
                out[src].append(iid)
    return {b: tuple(sorted(ids, key=_set_sort_key))
            for b, ids in out.items()}


def epic_cache(faction: str, claimed: frozenset[str] = frozenset()) -> dict:
    """REMOVED from the Dungeons page on purpose.

    This used to return the family's Epic pieces that live in a Heroic
    CACHE rather than behind any boss — the Nightling's whole 25-piece set
    (the Chaotic Gear Cache) and the other four families' six apiece (the
    Hero Gear Cache) — and the Dungeons pane drew each as a block of tiles.

    That was wrong: a cache is not a dungeon, it is not behind a boss, and
    the game's own data hands it out to nothing at all (nothing references
    `HM_Demon_Cache_25` or `HM_Gear_Cache_25` as a source). So the page was
    filing items against a location that does not exist. Those pieces are
    ordinary `epic_unknown` again, which is what the gear list and the
    Collection Manager serve them from, and where they were before.
    """
    return {}


@lru_cache(maxsize=1)
def _boss_weapon_ids() -> dict[str, tuple[str, ...]]:
    """Boss id -> signature weapon item ids: weapon-type gear whose drop
    rows reference the boss as a source (each boss rolls its own 2)."""
    srcs = _sources()
    out: dict[str, list[str]] = {}
    for iid, it in _data().get("items", {}).items():
        if category(it.get("type")) != "Weapons":
            continue
        if (it.get("faction") or "").lower() == "craft" or _is_crafted(iid):
            continue          # recipe-made variants never drop
        for dr in it.get("drops", []):
            s = srcs[dr["s"]] if 0 <= dr["s"] < len(srcs) else {}
            if s.get("kind") == "unit" and s.get("id"):
                out.setdefault(s["id"], []).append(iid)
    return {k: tuple(v) for k, v in out.items()}


@lru_cache(maxsize=None)
def _unit_faction(unit_id: str) -> str:
    """A unit's faction from the unit sheet, lowercased ('' when the sheet
    names none). The Dungeons tab keys a dungeon's family off its BOSS's
    faction rather than its mob roster: the mobs roll a whole spread of
    tables — the Nightling summons fight INSIDE Crimson Sacristy — so
    matching mobs pulled Crimson's High Inquisitor Chakram into the
    Nightling group even though it is already in the Crimson one."""
    if not unit_id:
        return ""
    from .. import units
    info = units.unit_info(unit_id) or {}
    return str(info.get("faction") or "").strip().lower()


def _dungeon_label(d: dict) -> str:
    """A dungeon's display name: `ingame_name` whenever the sheet spells
    one out ('Crimson Sacristy'), else its `name`. The two Nightling rifts
    are the only rows whose `ingame_name` is the raw key ('maat' /
    'shaarlize') while `name` carries the real title, so a single-token
    value falls back to it rather than printing the key."""
    ig = str(d.get("ingame_name") or "").strip()
    nm = str(d.get("name") or "").strip()
    if ig and (" " in ig or not nm):
        return ig
    return nm or ig


# Heroic-mode Infusion Patterns: one guaranteed drop per heroic boss, on
# its own `*_LT2` loot table (the 2026 patch's Infusion System — "you can
# loot Infusion recipes at the end of Heroic Instances"). Each boss's LT2
# table holds exactly one InfusionPattern entry (proba 1.0) plus a 1%
# mount/glider, so the boss -> pattern map is derived straight from the
# tables, no faction bookkeeping needed.
#
# Two wrinkles the wiring has to absorb. The Nightling rifts name their
# table after the DUNGEON, not the boss unit (`Maat_LT2` / `Shaarlize_LT2`
# against the boss ids DemonSuperElite / DemonSuperElite_Fairy), so a table
# key that isn't itself a boss unit resolves through the dungeon sheet.
# And this build ships the three `InfusionPattern_Nightling_*` items with
# no `*_LT2` row at all — neither rift lists one — so the scan's own drop
# rows are read too: whichever source lands first does the wiring.
@lru_cache(maxsize=1)
def _heroic_key_to_boss() -> dict[str, str]:
    """A `*_LT2` table key (lowercased) -> the dungeon's boss id. Identical
    for every table named after its own boss unit (Gatsbee_LT2 -> Gatsbee),
    which is why callers only consult it for keys that aren't a boss unit:
    the Nightling rifts are the exception (maat / shaarlize -> the
    DemonSuperElite pair)."""
    from .. import dungeons
    out: dict[str, str] = {}
    for d in dungeons.load_dungeons():
        bid = d.get("boss_id")
        if not bid:
            continue
        for alias in (d.get("name"), d.get("ingame_name")):
            if alias:
                out.setdefault(str(alias).strip().lower(), bid)
    return out


@lru_cache(maxsize=1)
def heroic_infusion_by_boss() -> dict[str, str]:
    """Heroic boss id -> the InfusionPattern item its `*_LT2` loot table
    carries (one guaranteed pattern per heroic boss — Gatsbee ->
    InfusionPattern_Bee_DPS, Ratsar -> InfusionPattern_Kobold_Tank, ...).
    The suffix is written both ways in the sheet (Gatsbee_LT2, RatsarLT2),
    so both spellings are stripped. A captured drop row pointing at a
    heroic boss wires a pattern too, so an item the build ships ahead of
    its table resolves as soon as the scan records the kill. Empty when
    the data has no `*_LT2` tables (pre-patch scan)."""
    out: dict[str, str] = {}
    items = _data().get("items", {})
    from .. import cdb
    for r in cdb.lines("lootTable"):
        m = re.match(r"^(.*?)(?:_LT2|LT2)$", r.get("id") or "")
        if not m:
            continue
        for e in (r.get("loot") or []):
            iid = e.get("item") or ""
            if (items.get(iid) or {}).get("type") == "InfusionPattern":
                out[m.group(1)] = iid
                break
    aliases = _heroic_key_to_boss()
    fixed: dict[str, str] = {
        (k if _is_boss(k) else aliases.get(k.strip().lower(), k)): v
        for k, v in out.items()}
    wired = set(fixed.values())
    for iid, it in items.items():
        if (it.get("type") or "") != "InfusionPattern" or iid in wired:
            continue
        srcs = sorted({rr.get("source_id") for rr in resolve_drops(iid)
                       if rr.get("source_id")})
        for src in srcs:
            if _is_boss(src):
                fixed.setdefault(src, iid)
                break
    return fixed


def heroic_infusions() -> tuple[dict, ...]:
    """The Infusion System's pattern pool, one dict per InfusionPattern item
    (the Enchants page's Infusions tab): item id, display name, faction
    (Bee / Kobold / Manfish), role variant (DPS / Tank / Support), the
    heroic boss + dungeon that guarantee it (the boss's *_LT2 loot table
    drops the pattern at proba 1.0) and the infusion skill the pattern
    grants once applied (Infusion_<Faction>_<Role> in the skill sheet —
    'Hive Venom', 'Pestilential Aura', ...; ultimate when the sheet names
    one, e.g. Bee Support's 'Honey Shot'). Boss/dungeon/skill fall back
    to '' when the scan predates the patch or the id is unresolved.
    A new faction/role from a future patch appears automatically — the
    pool derives straight from the item sheet."""
    items = _data().get("items", {})
    by_boss = heroic_infusion_by_boss()
    boss_by_item = {v: k for k, v in by_boss.items()}
    dungeons_by_boss: dict[str, str] = {}
    bosses_by_bid: dict[str, str] = {}
    if by_boss:
        from .. import dungeons
        for d in dungeons.load_dungeons():
            bid = d.get("boss_id")
            if bid and bid in by_boss and bid not in dungeons_by_boss:
                dungeons_by_boss[bid] = _dungeon_label(d)
            if bid:
                # the crucible UI's unlock text names the boss by its
                # DISPLAY name ('Defeat Heroic Lady Bee') — the dungeon
                # pane's boss_name, not the unit id ('Mokshi')
                bosses_by_bid[bid] = (d.get("boss_name") or bid)
    out = []
    for iid, it in items.items():
        if (it.get("type") or "") != "InfusionPattern":
            continue
        parts = iid.split("_")           # InfusionPattern_<Faction>_<Role>
        faction = parts[1] if len(parts) > 2 else ""
        role = parts[2] if len(parts) > 2 else ""
        bid = boss_by_item.get(iid, "")
        # display name: the load-normalized row name ('Bee Support' — see
        # catalog._normalize_infusion_pattern_names), else the humanized id
        nm = (it.get("name") or "").strip()
        if not nm or nm.startswith("Infusion:"):
            nm = names.humanize(iid) or iid
        # the granted infusion skill: Infusion_<Faction>_<Role> in the
        # skill sheet — the CDB row's `name` field only counts when the
        # manifest actually named it (unresolved rows echo the id as the
        # name, which must not render as if it were a label)
        from .. import cdb
        from .. import skills as _sk
        skill_rows = cdb.by_id("skill")

        def _skill_label(sid: str) -> str:
            snm = ((skill_rows.get(sid) or {}).get("name") or "").strip()
            return snm if snm and snm != sid else ""
        base_sid = f"Infusion_{faction}_{role}".strip("_")
        has_base = bool(_skill_label(base_sid))
        out.append({
            "item": iid,
            "name": nm,
            "faction": faction,
            "role": role,
            "boss": bid,
            # the crucible UI's 'Unlock: Defeat Heroic <name>' display
            # name ('Lady Bee'), falling back to the unit id
            "unlock_boss": bosses_by_bid.get(bid, bid),
            "dungeon": dungeons_by_boss.get(bid, ""),
            "skill": _skill_label(base_sid),
            "ultimate": _skill_label(f"{base_sid}_Ultimate"),
            "skill_id": base_sid if has_base else "",
            "ultimate_id": (f"{base_sid}_Ultimate"
                            if _skill_label(f"{base_sid}_Ultimate") else ""),
            # the crucible's set-bonus tiers, derived from the granted
            # skill's own sheet rows: (2) the skill's base description,
            # (4) its threshold-gated stat affixes, (6) its rank
            # description — the same three texts the crucible UI shows
            "set2": _sk.skill_description(base_sid) if has_base else "",
            "set4": _sk.skill_set_bonus_affixes(base_sid) if has_base
            else [],
            "set6": (_sk.skill_description(base_sid, rank=2)
                     if has_base else ""),
        })
    out.sort(key=lambda p: (p["faction"],
                            ("DPS", "Tank", "Support").index(p["role"])
                            if p["role"] in ("DPS", "Tank", "Support")
                            else 9))
    return tuple(out)


def shared_source(group: dict) -> dict:
    """Where a faction group's SHARED pool actually comes from, read from
    the pool's own resolved rows rather than declared for it.

    Every other family's 25 Rares drop from its bosses/chests, so the pool is
    a drop pool: ``{"kind": "drop"}``. Nightling is the exception the data
    spells out — all 75 of its resolved rows (25 pieces x 3 hubs) are one NPC
    vendor, so the pool is bought, not dropped:
    ``{"kind": "vendor", "source": "Mira, Demon Huntress", "locs": (...),
    "cost": (("Nightblood", 10),)}``. Calling it a boss drop would be wrong
    (the two Nightling bosses' tables hold only their signature weapons), so
    the Dungeons pane asks this instead of assuming.
    """
    shared = group.get("shared") or ()
    if not shared:
        return {"kind": "drop"}
    rows = merge_drops(shared[0])
    kinds = {r.get("kind") for r in rows}
    if rows and kinds == {"npc"}:
        return {
            "kind": "vendor",
            "source": rows[0].get("source") or "",
            "source_id": rows[0].get("source_id") or "",
            "locs": tuple(rows[0].get("locs") or ()),
            "cost": tuple((c.get("kind") or "", c.get("amount") or 0)
                          for c in (rows[0].get("cost") or ())),
        }
    return {"kind": "drop"}


@lru_cache(maxsize=1)
def dungeon_drop_groups() -> tuple[dict, ...]:
    """The Dungeons tab's faction groups, one per set family (Bee / Kobold /
    Manfish / Crimson / Nightling — the last keyed by its item-sheet id
    "Demon"): the family's dungeons with their boss (name, id, level),
    signature weapons and heroic EPIC set pieces, plus the shared
    armor/trinket pool every dungeon of that family drops and the Epic set
    pieces no drop row resolves (`epic_unknown`). A dungeon belongs to its
    BOSS unit's faction, so a boss sits in exactly one family — the mob
    roster is cross-faction noise (the Nightling summons stand in Crimson
    Sacristy) and only decides membership for a boss the unit sheet leaves
    untagged, via the loot tables its mobs roll (_table_unit_rollers). All
    derived from the JSONs."""
    from .. import dungeons
    rollers = _table_unit_rollers()
    weapons = _boss_weapon_ids()
    idx = _gear_table_index()
    out = []
    for table in _FACTION_SET_TABLES:
        rolls = rollers.get(table, frozenset())
        gear_table = _FACTION_GEAR_TABLE.get(table, table)
        bosses, seen = [], set()
        shared = []
        for d in sorted(dungeons.load_dungeons(),
                        key=lambda x: x.get("level") or 0):
            bid = d.get("boss_id")
            if not bid or bid in seen:
                continue
            # the family is the BOSS unit's faction; the table rollers only
            # answer for a boss the unit sheet leaves untagged
            bfac = _unit_faction(bid)
            if bfac:
                if bfac != table.lower():
                    continue
            elif not ((set(d.get("mobs") or ()) | {bid}) & rolls):
                continue
            seen.add(bid)
            bosses.append({
                "boss_id": bid,
                "boss": d.get("boss_name") or bid,
                "dungeon": _dungeon_label(d),
                "level": d.get("level"),
                "weapons": sorted(weapons.get(bid, ())),
            })
        # the heroic Infusion Pattern this boss guarantees on its *_LT2
        # table (the patch's Infusion System) — one per heroic boss
        infusions = heroic_infusion_by_boss()
        for b in bosses:
            b["infusion"] = infusions.get(b["boss_id"], "")
        for iid in idx.get(gear_table, ()):
            it = _data().get("items", {}).get(iid) or {}
            if _is_crafted(iid) or category(it.get("type")) == "Weapons":
                continue
            # every set piece rolls ALL FOUR faction tables (the WorldLoot
            # token shares them), so the table index alone is cross-faction
            # noise — the item's faction field is the truth
            if (it.get("faction") or "").lower() != table.lower():
                continue
            shared.append(iid)
        if bosses:
            shared.sort(key=_set_sort_key)
            epics = _epic_set_by_boss(table)
            # Only THIS family's own bosses get an Epic block, and only the
            # pieces their own `_HM` table rolls. Two things that once got a
            # block here are deliberately back out:
            #
            #  - a boss of ANOTHER family whose table rolls this family's
            #    pieces (Golcano is Kobold, his `Golcano_HM` is Crimson's).
            #    Showing those six under Crimson's pane put a Kobold dungeon
            #    in the Crimson page — the wrong dungeon for the item.
            #  - the family's pieces that live in a Heroic CACHE rather than
            #    behind any boss (the Nightling's whole set, the other four's
            #    six). A cache is not a dungeon and has no drop location, so
            #    it has no business on the Dungeons page at all.
            #
            # Both are ordinary `epic_unknown` now, which is where the gear
            # list and the Collection Manager serve them from.
            for b in bosses:
                b["epic"] = tuple(epics.get(b["boss_id"], ()))
            claimed = {i for b in bosses for i in b["epic"]}
            out.append({
                "faction": table, "bosses": tuple(bosses),
                "shared": tuple(shared),
                # the family's Epic pieces no boss of its own dungeons drops
                "epic_unknown": tuple(i for i in _epic_set_ids(table)
                                      if i not in claimed),
            })
    return tuple(out)


def item_label(iid: str) -> str | None:
    """Display name for a Dungeons-tab tile (cached item sheet name)."""
    it = _data().get("items", {}).get(iid) or {}
    return it.get("name") or iid


def infusion_skill_notes(item_id: str) -> tuple[dict, ...]:
    """The detail card's effect text for one InfusionPattern: the skill the
    pattern grants once applied (and its ultimate, when the sheet names
    one) with the skill sheet's own description — live from the same rows
    the skill pages read. [] for anything but a pattern (and for patterns
    whose skill rows the sheet doesn't name). Values the sheet leaves as
    template slots render as the sheet's own 'X' — no invented numbers."""
    p = next((x for x in heroic_infusions() if x["item"] == item_id), None)
    if not p:
        return ()
    from .. import skills as _sk
    out = []
    for tag, sid, nm in (("GRANTED SKILL", p.get("skill_id"),
                          p.get("skill") or ""),
                         ("ULTIMATE", p.get("ultimate_id"),
                          p.get("ultimate") or "")):
        if not sid:
            continue
        entry = {"tag": tag, "name": nm,
                 "desc": _sk.skill_description(sid) or ""}
        if tag == "GRANTED SKILL":
            # the crucible's set tiers for the (2)-piece base: its (4)
            # stat affixes and (6) rank text — the same three texts the
            # InfusionSkillDesc panel shows
            entry["set4"] = list(p.get("set4") or [])
            entry["set6"] = p.get("set6") or ""
        out.append(entry)
    return tuple(out)


def drops_from_table(item_id: str, table: str) -> bool:
    """True when any of the item's drop rows reference this loot table."""
    if not table:
        return True
    it = _data().get("items", {}).get(item_id) or {}
    tbls = _tables()
    return any(0 <= dr["t"] < len(tbls) and tbls[dr["t"]] == table
               for dr in it.get("drops", []))


def max_shown_drops() -> int:
    return _MAX_SHOWN_DROPS
