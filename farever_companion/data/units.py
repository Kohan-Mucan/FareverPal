"""Per-unit loot: CDB unit-id -> drop table -> predicted drops, plus boss
detection. Pure (CDB-only), so a unit-id always resolves to the same table.

Chain (verified against the CDB):
    unit.json      id    -> type, lvl, maxLvl, faction, flags
    unitType.json  type  -> lootTable (None for non-droppers)
    lootTable.json       -> recursive expansion (loot.predict)

Boss detection: a unit whose id also names a loot table (named bosses), or one
whose unit.flags carries BOSS_FLAG_BIT.
"""
from __future__ import annotations

from functools import lru_cache

from . import cdb, loot

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


def named_bosses() -> frozenset[str]:
    return _named_bosses()


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


# Wild catchable companions: authoritative list = the collection catalog's
# "companions" category (the same file the Collection tab / website show).
# unit.type == "Critter" is the union fallback - the 60 catalog companions are
# exactly the Critter units minus the Base_Critter template and the
# YellowRabbits spawner row (verified against the CDB), and it still works
# when the catalog json isn't bundled.
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


def type_name(type_id: str | None) -> str:
    for tid, name in codex_types():
        if tid == type_id:
            return name
    return type_id or ""


@lru_cache(maxsize=1)
def codex_unit_ids() -> tuple[str, ...]:
    """All concrete enemy unit ids whose type is a Codex type, excluding
    templates and internals. Bosses and Unique units are always included."""
    ctypes = {tid for tid, _ in codex_types()}
    return tuple(sorted(
        u for u, r in _units_by_id().items()
        if (r.get("type") in ctypes or is_boss(u) or is_unique(u))
        and u not in _TEMPLATE_IDS
        and not u.endswith("_Base")
        and (is_boss(u) or is_unique(u) or not ("patrol" in u.lower() or "spawn" in u.lower() or
                 "trigger" in u.lower() or "marker" in u.lower() or
                 "bumper" in u.lower() or "idle" in u.lower()))
    ))


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


def loot_table_for_unit(unit_id: str | None) -> str | None:
    """Boss signature table if it's a named boss, else the type's trash table."""
    bt = boss_loot_table(unit_id)
    if bt:
        return bt
    info = unit_info(unit_id)
    return info.get("lootTable") if info else None


def predict_unit(unit_id: str, level: int | None = None
                 ) -> list[tuple[str, float, str, str]]:
    """Predicted drops for a unit-id at `level` (defaults to the unit's lvl)."""
    table = loot_table_for_unit(unit_id)
    if not table:
        return []
    info = unit_info(unit_id)
    lvl = level if level is not None else ((info.get("lvl") if info else None) or 1)
    return loot.predict_sorted(table, lvl)


@lru_cache(maxsize=1)
def _loot_to_unit_types() -> dict[str, list[str]]:
    """Map of loot_table_id -> list of unit_type_ids that use it."""
    out: dict[str, list[str]] = {}
    for ut in cdb.lines("unitType"):
        lt = ut.get("lootTable")
        if lt:
            out.setdefault(lt, []).append(ut["id"])
    return out


def who_drops(item_id: str) -> list[str]:
    """List of unit type names that can drop this item."""
    from . import names, loot
    
    # 1. Find all tables containing this item
    tables = loot.reverse_loot_map().get(item_id, [])
    
    # 2. Find unit types using those tables
    ut_map = _loot_to_unit_types()
    u_types = set()
    for tid in tables:
        for utid in ut_map.get(tid, []):
            u_types.add(utid)
            
    # 3. Return readable names
    out_names = []
    for utid in u_types:
        out_names.append(names.unit_name(utid))
    return sorted(out_names)


def static_spawns(unit_id: str) -> list[list[float]]:
    """Not currently used by the main app (Enemies are live memory scans)."""
    return []
