"""Unit metadata + boss detection. Pure (CDB-only), so a unit-id always
resolves the same way.

Boss detection: a unit whose id also names a loot table (named bosses), one
whose unit.flags carries BOSS_FLAG_BIT, or one in the curated Rift list
(`_CURATED_RIFT_BOSSES`) because the shipped sheets predate it.
"""
from __future__ import annotations

import os
import re
from functools import lru_cache

from . import cdb, names

# The `unit.flags` bits live in ONE place now (`farever_companion/unit_flags.py`)
# because the compiler read the same bare numbers differently (0x40 vs 0x80 for
# "unique", and 0x80 used as a dungeon hint there). Re-exported here so the
# names keep resolving from this module, where they have always lived, and so
# `is_boss`/`is_unique` provably share the compiler's bits. See that module for
# what each bit was measured to mean and how confident that reading is.
from ..unit_flags import (BOSS_FLAG_BIT, UNIQUE_FLAG_BIT,
                          is_boss_flag, is_unique_flag)


def canonical_unit_id(raw_id: str | None) -> str | None:
    """The ONE canonical form of a unit/entity id, whichever reader produced it.

    Two very different readers carry the same field and must compare equal:

      * the live scene walk, which reads the unit's own `unitId` hl String and
        stores it per foe address (core/dps_tracker_tick.py `_foe_raw_ids`),
      * the native bridge's kill notification, which reads that same field in
        C and puts it on the wire verbatim (`{"t":"kill","uid":...}`).

    What the bridge sends is the RAW value: `Units/Enemies/Wolf/Wolf_Z1W.prefab`
    for a prefab spawn, `Boss_Foo(Clone)` for an engine-pooled instance - while
    every static lookup in this module (is_boss, is_training_dummy, unit_info,
    loot tables) is keyed by the CDB's plain id. So the shape is reduced HERE,
    once, and both sides use it: `(Clone)`/`(Instance)` suffixes dropped, a
    prefab path reduced to its basename without extension, and the patrol/
    unique spawn suffixes (`_Elite`, `_1` ... `_5`) folded back onto the static
    id.

    Idempotent by construction, and that is load-bearing: a caller may reduce
    both sides of a comparison without knowing which one came off the wire.
    """
    if not raw_id:
        return raw_id
    # 1. Strip (Clone), (Instance), etc. - the engine's pooling suffixes.
    clean = raw_id.split("(")[0].strip()
    # 2. Handle path-like IDs (e.g. 'Units/Enemies/Wolf/Wolf_Z1W.prefab'
    #    -> 'Wolf_Z1W') - a prefab spawn names its own asset.
    if "/" in clean or "\\" in clean:
        clean = os.path.splitext(os.path.basename(clean))[0]
    # 3. Handle trailing variants for patrol/unique units (e.g. _Elite, _1)
    #    to ensure they match their static CDB definitions. Scoped to patrol
    #    ids on purpose: no other family documents a variant suffix, so
    #    elsewhere the tail is left exactly as the game wrote it.
    if "Patrol" in clean:
        for suffix in ("_Elite", "_1", "_2", "_3", "_4", "_5"):
            if clean.endswith(suffix):
                clean = clean[:-len(suffix)]
                break
    return clean


@lru_cache(maxsize=1)
def _units_by_id() -> dict[str, dict]:
    return cdb.by_id("unit")


@lru_cache(maxsize=1)
def _type_to_table() -> dict[str, str | None]:
    return {r["id"]: r.get("lootTable") for r in cdb.lines("unitType")}


@lru_cache(maxsize=1)
def _named_bosses() -> frozenset[str]:
    """Units whose id also names a loot table (the boss convention)."""
    table_ids = {r["id"] for r in cdb.lines("lootTable")}
    return frozenset(u for u in _units_by_id() if u in table_ids)


# Rift bosses the shipped sheets do not carry.
#
# The unit table (530 rows) and the dungeons table (14 rows) were compiled
# before this season's Rift encounters, so ids the live scene hands out can
# resolve to nothing at all: no unit row, no display name, `is_boss` no. The
# boss fight then had nothing to recognise it by except the instance's boss
# read - and that read is `dungeon_boss`, which model.units() derives from
# THIS function, so a gap here is circular: it silently costs the DPS meter
# its boss row AND the Run Timer its boss kill (live 2026-10-01, the rift
# fight split into mob rows and the run never finished on the boss dying).
#
#   Asuna                 the rift's second boss; no row anywhere in the CDB,
#                         yet the live scene spawns and reports her.
#   Nightking_Maat_Demon  the underscored asset id for the boss whose CDB row
#                         is `DemonSuperElite` (display name "Nightking Maat
#                         Demon"). The row spelling is already a boss, so this
#                         second entry only matters if the scene sends the
#                         asset id instead of the row id - then the fight must
#                         not depend on which spelling arrived.
_CURATED_RIFT_BOSSES: frozenset[str] = frozenset({
    "Asuna",
    "Nightking_Maat_Demon",
})

# The Rift family in the CDB: `DemonSuperElite` and its clones.
RIFT_BOSS_PREFIX: str = "DemonSuperElite"


def _is_rift_boss(unit_id: str) -> bool:
    """True for the Rift boss line, its curated siblings, and nothing else.

    `DemonSuperElite_Fairy_Guardian` ("Shaarlize's Guardian") is excluded on
    purpose: it is an ADD that spawns during the Fairy fight, and letting it
    take the boss board handed one fight's whole damage total to an add.
    Every other member of the family is the boss or a true clone of it.
    """
    if unit_id in _CURATED_RIFT_BOSSES:
        return True
    if not unit_id.startswith(RIFT_BOSS_PREFIX):
        return False
    return "FalseClone" not in unit_id and "_Guardian" not in unit_id


# Training dummies are NOT bosses (no loot table, no boss flag) but the DPS
# tracker treats them boss-like so dummy parses get their own boss-session
# group instead of polluting trash. Accepts raw unit ids (PunchingBag,
# PunchingBagArmor/MagicRes/Invulnerable/Shield, TrainingDummy, TestDummy,
# and the bare Dummy the open-world hubs actually spawn) as well as display
# names ("Test Dummy", "Training Dummy", "Punching Bag", "Dummy", plus the
# variant short names "Armor"/"MagicRes"/"Invu"/"Shielded", resolved back to
# their unit through the enemies manifest). The manifest's OTHER "Dummy_*"
# rows are props, not targets and stay out: Dummy_Runner is "Fleeing Bag",
# Dummy_Support an "Ally dummy", Dummy_FX a "FXTest" — none is a parse bench.
# Deliberately NOT part of is_boss(): dummies must stay out of the Bosses
# codex tab and boss-marked drop rows.
@lru_cache(maxsize=1)
def _display_name_to_id() -> dict[str, str]:
    """Lowercased display name -> unit id (first hit wins), from the same
    enemies manifest the game renders."""
    out: dict[str, str] = {}
    for r in cdb.display_data("enemies"):
        nm = (r.get("name") or "").strip().lower()
        if nm and nm not in out:
            out[nm] = r["id"]
    return out


def _is_dummy_compact(compact: str) -> bool:
    # The bare "dummy" is the game's own plain training dummy (`id='Dummy'`,
    # display name "Dummy") — the one the open-world hubs spawn, and the whole
    # reason a parse against it never engaged: every member of this family was
    # matched EXCEPT the commonest one.
    return (compact.startswith("punchingbag")
            or compact in ("dummy", "testdummy", "trainingdummy"))


def is_training_dummy(unit_id: str | None) -> bool:
    """True for the training-dummy family, by unit id or display name."""
    if not unit_id:
        return False
    s = (str(unit_id).replace("👑", "").replace("🎯", "")
         .strip().lower())
    if not s:
        return False
    compact = s.replace("_", "").replace("-", "").replace(" ", "")
    if _is_dummy_compact(compact):
        return True
    # Variant short display names ("Invu", "Armor", ...) carry no family
    # marker themselves — resolve back to the unit id first.
    uid = _display_name_to_id().get(s)
    if uid:
        c2 = uid.lower().replace("_", "").replace("-", "").replace(" ", "")
        return _is_dummy_compact(c2)
    return False


def is_known_unit(unit_id: str | None) -> bool:
    """Whether the shipped unit table has a row for this id.

    The gate on `is_boss`'s ENGINE fallback (the scene's boss class / boss
    flag, read in core/dps_tracker_tick.py `_scan_foes`): those may only speak
    for units the data layer has never heard of — content the sheets predate,
    which is the whole reason the fallback exists. When the CDB knows the
    unit, the CDB's verdict IS the verdict: Lost City of Mayda's trash crab
    is spawned from the `ent.boss.Crabgantua` prefab, so the class alone
    promoted a dungeon mob to "the boss" and the pre-boss pulls were swallowed
    into a boss fight that never split (live 2026-10-02). Every `ent.boss.*`
    unit in the shipped table is already a boss by `is_boss`, so nothing the
    engine can say is lost for a unit the data knows.
    """
    return bool(unit_id) and unit_id in _units_by_id()


def is_boss(unit_id: str | None) -> bool:
    """A named boss (has a signature loot table), Rift boss, or flagged as one
    once the boss flag bit is calibrated."""
    if not unit_id:
        return False
    # The Rift family (CDB row ids + the curated ids the sheets predate).
    # _Guardian (Shaarlize's Guardian) is an add, not a boss — excluded.
    if _is_rift_boss(unit_id):
        return True
    if unit_id in _named_bosses():
        return True
    row = _units_by_id().get(unit_id)
    flags = row.get("flags") if row else None
    if is_boss_flag(flags):
        return True
    return False


def resolve_hero_class(cls_name: str | None = None, unit_id: str | None = None) -> str:
    """Canonical class resolution for heroes, matching the entity HUD.
    Returns capitalized class name (e.g. 'Warrior', 'Rogue', 'Mage', 'Priest')."""
    cls_s = (cls_name or "").strip()
    if cls_s.startswith("ent.hero."):
        leaf = cls_s[len("ent.hero."):].title()
        if leaf:
            return leaf

    # Check unit_id and cls_name for known class keywords
    combined = f"{cls_s} {unit_id or ''}".lower()
    for c in ("warrior", "rogue", "mage", "priest",
              "paladin", "hunter", "bard", "druid",
              "fighter", "assassin", "wizard", "cleric"):
        if c in combined:
            if c in ("fighter",):
                return "Warrior"
            if c in ("assassin",):
                return "Rogue"
            if c in ("wizard",):
                return "Mage"
            if c in ("cleric",):
                return "Priest"
            return c.title()
    return ""


@lru_cache(maxsize=1)
def _is_elite_map() -> dict[str, bool]:
    """Map of unit_id -> isElite flag from the enemy display data."""
    return {r["id"]: r.get("isElite", False) for r in cdb.display_data("enemies")}


def is_elite(unit_id: str | None) -> bool:
    """True if the unit is flagged as an Elite in the display metadata."""
    if not unit_id:
        return False
    return _is_elite_map().get(unit_id, False)


@lru_cache(maxsize=1)
def spark_unit_ids() -> frozenset[str]:
    try:
        from . import raw_codex
        if raw_codex and hasattr(raw_codex, "DATA"):
            spark_ids = set()
            for region_data in raw_codex.DATA.values():
                if isinstance(region_data, list):
                    for entry in region_data:
                        if isinstance(entry, dict) and entry.get("drops_spark"):
                            spark_ids.add(entry["id"])
                elif isinstance(region_data, dict):
                    for category_list in region_data.values():
                        if isinstance(category_list, list):
                            for entry in category_list:
                                if isinstance(entry, dict) and entry.get("drops_spark"):
                                    spark_ids.add(entry["id"])
            return frozenset(spark_ids)
    except ImportError:
        pass
    return frozenset()


# The game spells a Spark unit three ways, and nothing else does the matching:
# "Sparkling <creature>" in the display name (`Sparkling Wild Bee`), the `_Spark`
# / `Spark*` id the companions use (`Rabbit_Spark` "Sparkling Buttontail",
# `SparkHorse_01`), and `<creature> Sparkle` (`Sparkle of Estrone`, `Pyrh
# Sparkle`). The COMPILER's own name test looks for the single substring
# "sparkling" against the codex_order name, so it misses every `<creature>
# Sparkle` row and every `_Spark` companion — that gap is the build's, and it is
# the dust flag's (see `drops_spark`). This is the runtime's answer to the
# broader question, in one place, so no widget invents its own spelling test.
_SPARK_TOKEN_RE = re.compile(r"spark", re.IGNORECASE)


def is_spark_variant(unit_id: str | None) -> bool:
    """THE 'is this a Spark unit' question — one predicate, every caller.

    True for a unit the build flagged as a dust source, and for any unit whose
    id or display name carries one of the game's Spark spellings (which includes
    the catchable `_Spark` companions and the `<creature> Sparkle` elementals
    the compiler's single-substring test misses).

    This is the VARIANT question — the card's gold outline and anything else that
    marks "this is a sparkling one". It is deliberately NOT the dust question:
    Spark Dust comes from the `FoeUniqueDrops` table, which the game attaches to
    unique FOES, and critters are caught, not killed — see `drops_spark`.
    """
    if not unit_id:
        return False
    if unit_id in spark_unit_ids() or _SPARK_TOKEN_RE.search(unit_id):
        return True
    # The name the UI actually draws — `unit_name` appends "(Spark)" to an id
    # that names one — falling back to the sheet row the compiler baked. Reading
    # both is what keeps this predicate and the label on the card agreeing.
    shown = (names.unit_name(unit_id)
             or (_units_by_id().get(unit_id) or {}).get("name") or "")
    return bool(_SPARK_TOKEN_RE.search(shown))


def drops_spark(unit_id: str | None) -> bool:
    """THE 'does this unit drop Spark Dust' question — one predicate, every
    reader (the card's dust badge, the Codex "Spark Dust" filter, the entity HUD
    and minimap spark toggles, the tracker's spark bypass).

    It answers with the flag the build bakes, which is the only evidence the app
    has: `compiler.py` flags a unit when it carries the unique-foe bit and is not
    in its five-unit EXCLUDE_DUST list. Nothing here re-derives the rule.

    Narrower than `is_spark_variant` ON PURPOSE, and the two must not be
    conflated: Spark Dust is the `FoeUniqueDrops` table's `UpgradeAll`, and the
    game attaches that table to unique FOES. Critters and mounts/gliders are
    excluded by the build because they are caught, not killed — and the app's own
    item index agrees, attributing Spark Dust to activity chests/crates and one
    vendor (DemonHuntZoey) and to no unit at all. So a sparkling companion is a
    Spark VARIANT (`is_spark_variant`) and correctly NOT a dust source.
    """
    if not unit_id:
        return False
    return unit_id in spark_unit_ids()


def is_unique(unit_id: str | None) -> bool:
    """True if the unit is marked with the UNIQUE_FLAG_BIT (0x80), identifying
    it as a named/special unit that should typically bypass filters."""
    if not unit_id:
        return False
    row = _units_by_id().get(unit_id)
    flags = row.get("flags") if row else None
    return is_unique_flag(flags)


def boss_loot_table(unit_id: str | None) -> str | None:
    """The boss's signature table (same id as the unit), or None."""
    return unit_id if (unit_id and unit_id in _named_bosses()) else None


# Wild catchable companions: authoritative list = the compiled collection
# catalog's "companions" category (derived from codex.json; the Collection tab
# shows the same rows). unit.type == "Critter" is the union fallback - the 60
# catalog companions are exactly the Critter units minus the Base_Critter
# template and the YellowRabbits spawner row (verified against the CDB), and it
# still works when the compiled catalog is empty.
COMPANION_TYPE = "Critter"


@lru_cache(maxsize=1)
def _companion_ids() -> frozenset[str]:
    from . import collections as col
    return frozenset(r["id"] for r in col.items("companions"))


def unit_types() -> list[str]:
    """All unitType ids (CDB), sorted — the choices for the enemy-type filter."""
    return sorted(_type_to_table())


# Types kept out of the Codex enemy filter even though they carry a display
# name: Critter = the wild-companion section, Mount = non-attackable.
NON_CODEX_TYPES = frozenset({COMPANION_TYPE, "Mount"})
# Inheritance templates, not real spawnable enemies.
_TEMPLATE_IDS = frozenset({"BaseHero", "BaseMob", "BaseSummon", "BaseMount",
                           "Base_Critter"})


@lru_cache(maxsize=1)
def codex_types() -> tuple[tuple[str, str], ...]:
    """(unitType id, display name) for the bestiary/Codex enemy types, sorted
    by display name. A type is Codex-worthy when its unitType row carries a
    display name; nameless internals (Totem, Environment, Human) drop out, and
    Critter/Mount are excluded explicitly (companions / non-attackable)."""
    out = []
    for r in cdb.lines("unitType"):
        name = (r.get("name") or "").strip()
        if name and r["id"] not in NON_CODEX_TYPES:
            out.append((r["id"], name))
    return tuple(sorted(out, key=lambda t: t[1]))


def is_codex_type(type_id: str | None) -> bool:
    """True if the type is recognized as a valid mob category in the Codex."""
    if not type_id:
        return False
    # Use a loop instead of any() to avoid unresolved reference issues in some linters
    for tid, _ in codex_types():
        if tid == type_id:
            return True
    return False


def type_name(type_id: str | None) -> str:
    for tid, name in codex_types():
        if tid == type_id:
            return name
    return type_id or ""


@lru_cache(maxsize=1)
def codex_unit_ids() -> tuple[str, ...]:
    """All concrete enemy unit ids whose type is a Codex type, excluding
    templates and internals. Bosses and Unique units are always included."""
    return tuple(sorted(
        u for u, r in _units_by_id().items()
        if is_codex_unit(u)
    ))


def is_codex_unit(unit_id: str | None) -> bool:
    """True if the unit is a 'real' mob: recognized codex type, boss, or unique,
    and not an internal engine object."""
    if not unit_id:
        return False
    # Bosses and uniques (like RamPatrol dogs) always bypass keyword filters
    if is_boss(unit_id) or is_unique(unit_id):
        return True
    # Check if the type is allowed (excludes Mounts, Totems, etc.)
    if not is_codex_type(unit_type(unit_id)):
        return False
    # Check templates
    if unit_id in _TEMPLATE_IDS or unit_id.endswith("_Base"):
        return False
    # Check internal keywords
    uid_l = unit_id.lower()
    for s in ("spawn", "trigger", "marker", "point", "target", "area", "path",
              "route", "bumper", "idle", "portal", "cannon", "totem", "shield",
              "wall", "gate", "platform", "volume", "camera", "light", "effect",
              "visual", "patrol", "partol", "patrole"):
        if s in uid_l:
            return False
    return True


def unit_type(unit_id: str | None) -> str | None:
    row = _units_by_id().get(unit_id) if unit_id else None
    return row.get("type") if row else None


def is_companion(unit_id: str | None) -> bool:
    """A wild catchable companion (Buttontail, Leggybug, Saladmander, …)."""
    if not unit_id:
        return False
    return unit_id in _companion_ids() or unit_type(unit_id) == COMPANION_TYPE


def unit_info(unit_id: str | None) -> dict | None:
    """{type, lvl, maxLvl, faction, lootTable, flags} for a unit-id, or None."""
    if not unit_id:
        return None
    row = _units_by_id().get(unit_id)
    if row is None:
        return None
    utype = row.get("type")
    return {
        "type": utype,
        "lvl": row.get("lvl"),
        "maxLvl": row.get("maxLvl"),
        "faction": row.get("faction"),
        "flags": row.get("flags"),
        "lootTable": _type_to_table().get(utype) if utype else None,
    }


def static_spawns(unit_id: str) -> list[list[float]]:
    """Not currently used by the main app (Enemies are live memory scans)."""
    return []
