"""Unit metadata + boss detection. Pure (CDB-only), so a unit-id always
resolves the same way.

Boss detection: a unit whose id also names a loot table (named bosses), or one
whose unit.flags carries BOSS_FLAG_BIT.
"""
from __future__ import annotations

from functools import lru_cache

from . import cdb

# unit.flags boss bit, calibrated from the CDB: exactly 13 of 403 units carry
# 0x10 (all named bosses plus Phrixes/PhrixesP1/Ulserous), zero trash mobs.
BOSS_FLAG_BIT: int | None = 0x10
# Unique/named units (like the RamPatrol dogs) carry 0x80 (often 192/0xC0).
UNIQUE_FLAG_BIT: int = 0x80


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


def is_boss(unit_id: str | None) -> bool:
    """A named boss (has a signature loot table), or flagged as one once the
    boss flag bit is calibrated."""
    if not unit_id:
        return False
    if unit_id in _named_bosses():
        return True
    if BOSS_FLAG_BIT is not None:
        row = _units_by_id().get(unit_id)
        flags = row.get("flags") if row else None
        if isinstance(flags, int) and (flags & BOSS_FLAG_BIT):
            return True
    return False


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


def drops_spark(unit_id: str | None) -> bool:
    """True if the unit is a 'Spark' variant that drops Spark Dust."""
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
    return isinstance(flags, int) and (flags & UNIQUE_FLAG_BIT)


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
