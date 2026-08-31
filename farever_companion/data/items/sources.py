"""Drop sources: where an item comes from (the Drops From list).

Resolves the raw `drops[]` rows against the `_sources` / `_locs` / `_tables`
indexes into readable rows, flags the rift/dungeon locations that are the
"real info", and groups the loot tables that drop gear (the gear tab's
loot-table filter).
"""
from __future__ import annotations

import copy
import json
import re
from functools import lru_cache

from ... import paths
from .. import names, raw_data
from .catalog import _data, _sources, _tables, _locs
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
    "npc": "VENDOR",
    "gatherable": "GATHER",
    "achievement": "ACHIEVEMENT",
    "worldloot_affinity": "WORLD GEAR",
    "worldrecipe": "WORLD RECIPE",
}

_KIND_GROUP_LABEL = {
    "unit": "MOBS",
    "chest": "CHESTS",
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
_DUNGEON_POOL_SIZES = {"Manfish": 28, "Kobold": 28, "Bee": 30,
                       "Crimson": 29}
_CRAFT_POOL_SIZE = 16          # the craft-variant pool spans all 4 factions
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


@lru_cache(maxsize=1)
def _poi_rows() -> tuple[dict, ...]:
    """POI rows (soulstones, dungeons, ...): the compiled raw_locs / raw_data shim
    first, then the loose JSON (stale shim / dev checkout)."""
    try:
        from .. import raw_locs
        rows = raw_locs.DATA.get("poi_locs")
    except Exception:
        rows = None
    if not rows:
        rows = raw_data.DATA.get("poi_locs")
    if not rows:
        try:
            data = json.loads(paths.poi_locs_path().read_text(encoding="utf-8"))
            rows = data.get("pois") if isinstance(data, dict) else data
        except (OSError, ValueError):
            return ()
    return tuple(r for r in rows if isinstance(r, dict))


@lru_cache(maxsize=1)
def _mob_loc_rows() -> tuple[dict, ...]:
    """mob_locs rows (world spawns): the compiled raw_locs / raw_data shim first, then
    the loose JSON (stale shim / dev checkout)."""
    try:
        from .. import raw_locs
        rows = raw_locs.DATA.get("mob_locs")
    except Exception:
        rows = None
    if not rows:
        rows = raw_data.DATA.get("mob_locs")
    if not rows:
        try:
            data = json.loads(paths.mob_locs_path().read_text(encoding="utf-8"))
            rows = data.get("mobs") if isinstance(data, dict) else data
        except (OSError, ValueError):
            return ()
    return tuple(r for r in rows if isinstance(r, dict))


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
# level next to each hub.
_GUILD_MERCHANT_RE = re.compile(r"^WanderingMerchant", re.IGNORECASE)

# The town hubs' item levels, per vendor source: each Guild Merchant town
# sells its shop stock scaled to the town's item level, so a vendor row can
# show the level next to the town name (Tyrna · L20, Lower Ramburg · L25).
# The Primeval Valley / Meridion merchants sell no scanned items yet, so
# they have no stamped level.
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
_BASE_CLOTHES_ID = "Chest_C_BaseClothes"
_CLASS_GLIDERS = {
    "Glider_Owl_Brown": "Fighter",
    "Glider_Owl_Grey": "Assassin",
    "Glider_Raccoon_Grey": "Wizard",
    "Glider_Raccoon_Orange": "Cleric",
}
_STARTER_TOOLS = ("Sickle", "Pickaxe")

# Items that DO sit in loot tables (lootTable.json) but whose tables aren't
# anchored by the scan (no scanned mob/chest owns them — rift rewards,
# craft-point nodes, starter tables, ...), so they report no drops. The
# reason names the actual table instead of claiming a generator token.
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
    return note


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
        return ("Starter cache — opens into a class starter weapon "
                "(fixed-level, not scaled to your level).")
    if iid.endswith("_Shop"):
        return "Shop gear — sold by a vendor the scan data doesn't capture."
    if iid in _LOOT_LISTED_UNANCHORED:
        return ("Listed in loot tables the scan can't anchor — "
                f"{_LOOT_LISTED_UNANCHORED[iid]}.")
    if iid.startswith("Recipe_"):
        return ("Recipe drop — WorldRecipeWithJob token (world crates / "
                "humanoid mobs); recipes aren't listed individually in "
                "loot tables.")
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


@lru_cache(maxsize=None)
def _is_crafted(item_id: str) -> bool:
    """True when the item is crafted gear (made at a fixed level in-game), so
    its drop rows are noise and must be hidden. The scan recorded the base
    dungeon-set rows on the crafted `_Craft` variants too (Beekeeper's
    Scarf, Handguards of the Deep Sea, ...) and on the recipe-bearing
    faction pieces (Aura of the Honeycomb, Cantal Goya's Breastplate, ...),
    but those pieces are made by recipes — item_fixed_level is the single
    source of truth."""
    if not item_id:
        return False
    it = _data().get("items", {}).get(item_id)
    if not it:
        return False
    from .stats import item_fixed_level
    return item_fixed_level({"id": item_id, **it}) is not None


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
    for Guild Merchant rows, else None), locs (human names, deduped),
    n_locs (distinct raw locations merged)}. Sorted with rift/dungeon
    sources first, then by chance descending.
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
        })
        if d.get("quiet"):
            r["quiet"] = True
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
                     r.get("hub_level"))
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
    rows.sort(key=lambda r: (not r["rift"], -r["prob"], r["source"].lower()))
    return rows

@lru_cache(maxsize=None)
def upgrade_locked(item_id: str) -> bool:
    """True when a piece's UPGRADE LADDER must render base-only: armor-slot
    gear (Chest, Legs, Head, Feet, Waist, Hands, Shoulders, Back) AND
    jewelry (Ring / Neck / Trinket — the Accessory slots). Neither category
    can be upgraded in-game yet — only weapons can — so like crafted gear
    their step gains are hidden and only the base stats show (the steps
    stay in the data; they're just not displayed until those upgrades
    land). Jewelry also only ever drops at its authored zone-tier level
    (a max drop level, never scaled up), so the base value there is the
    whole story. Source-agnostic: boss drops, faction crates, vendor
    caches (Smile of the Demolisher from the Chaotic Gear Cache) and town
    sales are all locked the same way. Weapons keep their full ladder."""
    if not item_id:
        return False
    it = _data().get("items", {}).get(item_id) or {}
    return category(it.get("type") or "") in ("Armor", "Accessory")


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

    Crafted gear returns [] — the scan stamped the base set's drop rows on
    the crafted variants too (Beekeeper's Scarf and the other `_Craft`
    pieces share the Bee Hive crate / boss rows), but crafted pieces are
    made by recipes, never dropped, so their Drops From must not list
    dungeon sources.
    """
    srcs, locs, tbls = _sources(), _locs(), _tables()
    it = _data().get("items", {}).get(item_id) or {}
    if _is_crafted(item_id):
        return []
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
        if v["kind"] == "unit" and (loc or "").strip().lower() == _UNKNOWN_LOC:
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
    rows.sort(key=lambda r: (not r["rift"], r["loc"].lower(), -r["prob"]))
    if not rows:
        # the scan has no rows for the dungeon gear sets — the game hands
        # them out at runtime via the WorldLoot/UpgradeRare tokens, so the
        # real per-source chances are derived here instead of reporting
        # 'no drop sources'
        rows.extend(_dungeon_set_rows(item_id))
    return rows


# Loot tables that drop gear, grouped by what rolls them: the rift vendor's
# affinity tables (Rift_GearWithAffinity / Rift_WeaponWithAffinity), the Shop
# table (guild vendors), the per-faction dungeon-set tables (Bee/Kobold/
# Manfish/Crimson — every set piece shares one), the named-boss signature
# tables (Maat, Reblochonk, ...), and the generic world tables (Clothes /
# Leather). Gear that shares a table is the "same mob type of dungeon drop"
# — filterable together on the gear tab.
_FACTION_SET_TABLES = ("Bee", "Kobold", "Manfish", "Crimson")


@lru_cache(maxsize=1)
def _boss_signature_tables() -> frozenset[str]:
    """Loot tables that are a single named boss's signature set: every gear
    piece in the table references it through exactly one source, and that
    source is a boss. The faction set tables (Bee/Kobold/...) fail the
    single-source check — dozens of mobs roll them, so they're not boss
    sets even though the faction boss rolls them too."""
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
                     if len(s) == 1 and _is_boss(next(iter(s))))


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
_SLOT_ORDER = ("Head", "Shoulders", "Chest", "Hands", "Waist", "Legs",
               "Feet", "Back", "GearFinger", "GearNeck", "GearTrinket")


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


@lru_cache(maxsize=1)
def dungeon_drop_groups() -> tuple[dict, ...]:
    """The Dungeons tab's faction groups, one per set family (Bee / Kobold /
    Manfish / Crimson): the family's dungeons with their boss (name, id,
    level) and signature weapons, plus the shared armor/trinket pool every
    dungeon of that family drops. Same membership rule the Drops From list
    uses — a dungeon belongs to the faction whose loot table its boss or
    mobs roll (_table_unit_rollers) — all derived from the JSONs."""
    from .. import dungeons
    rollers = _table_unit_rollers()
    weapons = _boss_weapon_ids()
    idx = _gear_table_index()
    out = []
    for table in _FACTION_SET_TABLES:
        rolls = rollers.get(table, frozenset())
        bosses, seen = [], set()
        shared = []
        for d in sorted(dungeons.load_dungeons(),
                        key=lambda x: x.get("level") or 0):
            bid = d.get("boss_id")
            if not bid or bid in seen:
                continue
            units = set(d.get("mobs") or ()) | {bid}
            if not (units & rolls):
                continue
            seen.add(bid)
            bosses.append({
                "boss_id": bid,
                "boss": d.get("boss_name") or bid,
                "dungeon": d.get("ingame_name") or d.get("name") or "",
                "level": d.get("level"),
                "weapons": sorted(weapons.get(bid, ())),
            })
        for iid in idx.get(table, ()):
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
            shared.sort(key=lambda iid: (
                _SLOT_ORDER.index(_data().get("items", {}).get(iid, {})
                                  .get("type"))
                if _data().get("items", {}).get(iid, {}).get("type")
                in _SLOT_ORDER else 99,
                (item_label(iid) or iid).lower()))
            out.append({"faction": table, "bosses": tuple(bosses),
                        "shared": tuple(shared)})
    return tuple(out)


def item_label(iid: str) -> str | None:
    """Display name for a Dungeons-tab tile (cached item sheet name)."""
    it = _data().get("items", {}).get(iid) or {}
    return it.get("name") or iid


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
