"""The unit-flag SMEAR census: where one bit answers two questions.

`tests/test_unit_flags.py` pins what each `unit.flags` bit MEANS. This file
pins where the readers still DISAGREE about a bit, or where a derivation
contradicts it - the same "two questions, one flag" shape the Spark fix
exposed. Measured 2026-10-06 on the shipped shims (see the probe in the
handoff; no game dump needed except where a test says so).

Three smears, all consequences of ONE compiler behaviour:

  * NO-CODEX (`0x40000`): `_compile_standard_sheets` drops every row carrying
    the bit (and every non-special Totem/Environment/Trigger/Marker). Then the
    enemies-manifest merge RE-ADDS each dropped row, copying only
    id/name/type - so the prune is BYPASSED, not applied, and the row returns
    with no `flags` at all. Every re-added row is a pruned row; no unit is
    actually missing.
  * UNIQUE (`0x80`) / BOSS (`0x10`): because the re-added rows carry no bits,
    nine unique rows and one boss row lose their flags in the shipped shim, so
    `units.is_unique()` / `units.is_boss()` deny units the game sheet marks.
  * DUNGEON (`is_dungeon`): the compiler rule would mark 167 rows but only 86
    are baked on codex entries, and 14 baked ones are Mount/Glider collection
    rows that `dungeons.json` does not contain at all.

Each set below is a CENSUS in the `_UNSPECCED_CONTAINERS` / `HAND_GAME_FACTS`
style: a named exception that keeps the gap visible and forces a conscious
update when the data moves. A fix for any of them should SHRINK these sets -
the test going red is the point, not a failure to paper over.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from farever_companion import unit_flags as F
from farever_companion.data import dungeons, raw_codex, raw_units, units

ROOT = Path(__file__).resolve().parents[1]


# The unit types the compiler prunes unless the row is special or a named boss.
_SPECIAL_TYPES = ("Totem", "Environment", "Trigger", "Marker")


# The 34 rows the enemies-manifest merge re-adds FLAGLESS: exactly the rows the
# compiler's unit prune removed (the NO-CODEX bit, plus five non-special
# special-type rows). The merge copies only id/name/type, so the prune is
# undone and the row survives with no `flags` key.
_FLAGLESS_RE_ADDED = frozenset({
    "Base_Critter", "Bee_Z1D", "ChaoticPortal",
    "Cleodora_Champion", "Cleodora_Champion2", "Cleodora_Champion3",
    "Cleodora_Feeder", "Cleodora_Heroic_Dark_Champion",
    "Crab_Crabgantua", "Crab_Crabgantua_Summon_Heroic", "Crab_Z1D",
    "Crab_Z2D_2", "Dog_Z3D", "Dummy", "Elemental_Z1D_Earth",
    "Elemental_Z1D_Lava", "Golcano_Minion", "Golem_Z2D_FireExplosive",
    "Kobold_Ratsar_Caster", "Nepsilon_Totem", "Phrixes_ChaoticPortal",
    "Phrixes_Crawler", "Phrixes_NightGodGate", "Phrixes_NightGod_NL",
    "Queenslayer", "R1KoboldBoss_Sparkling", "RobinHoofDog01",
    "Slime_Z1D_Honey", "Slime_Z1D_Honey_NoXP", "Staff_SummonDemon_Imp",
    "Summon_Bee", "Totem_BeaconOfHope", "Totem_Tidal", "World",
})

# Of those 34, the eleven the runtime's OWN codex heuristic does NOT re-admit:
# the six no-codex internals, plus the five special-type rows (which the
# type-based codex filter keeps out on its own). So the bit and the predicate
# agree on these eleven and disagree on the other 23.
_RUNTIME_NOT_CODEX = frozenset({
    "Base_Critter", "ChaoticPortal", "Dummy",
    "Phrixes_ChaoticPortal", "Phrixes_NightGodGate", "World",
    "Nepsilon_Totem", "Staff_SummonDemon_Imp", "Summon_Bee",
    "Totem_BeaconOfHope", "Totem_Tidal",
})

# The 34 that were re-added AND are listed in the compiled codex anyway: the
# bit was meant to drop them from it.
_RE_LISTED_IN_CODEX = frozenset({
    "Bee_Z1D", "Cleodora_Champion", "Cleodora_Champion2", "Cleodora_Champion3",
    "Cleodora_Feeder", "Crab_Crabgantua", "Crab_Z1D", "Crab_Z2D_2", "Dog_Z3D",
    "Elemental_Z1D_Earth", "Elemental_Z1D_Lava", "Golcano_Minion",
    "Golem_Z2D_FireExplosive", "Kobold_Ratsar_Caster",
    "R1KoboldBoss_Sparkling", "RobinHoofDog01", "Slime_Z1D_Honey",
    "Slime_Z1D_Honey_NoXP",
})

# The nine shim rows that lost `0x80` and the one that lost `0x10` when the
# prune was undone (loose flags -> flagless compiled row).
_LOST_UNIQUE = frozenset({
    "Cleodora_Champion", "Cleodora_Champion2", "Cleodora_Champion3",
    "Cleodora_Heroic_Dark_Champion", "Golcano_Minion", "Kobold_Ratsar_Caster",
    "Phrixes_NightGod_NL", "Queenslayer", "RobinHoofDog01",
})
_LOST_BOSS = frozenset({"Cleodora_Heroic_Dark_Champion"})

# Codex entries baked `is_dungeon: True` that `dungeons.json` does not contain:
# Mount/Glider collection rows. The runtime's Mounts/Gliders branch forces
# `is_dungeon` False, so they never reach a card - a bake the reader discards.
_DUNGEON_FALSE_POSITIVES = frozenset({
    "Glider_Bat_BlackWhite", "Glider_Bat_WhiteBlack", "Glider_Butterfly_Yellow",
    "Glider_Crimson01", "Glider_Demon_Purple", "Glider_Dragon_Lava",
    "Glider_FlyingFish_Acid", "Glider_FlyingFish_Orange", "Mount_Aries_05",
    "Mount_Crab_Blue", "Mount_Demon_06", "Mount_Ladybug_Purple",
    "Mount_Ladybug_Red", "Mount_Skunk_04",
})

# `units.is_boss()` promotes these four (Rift clones / a named Night God) but
# their codex entry carries no `is_boss`.
_CODEX_BOSS_GAP = frozenset({
    "DemonSuperElite_Fairy_FalseClone", "DemonSuperElite_Fairy_TrueClone",
    "Phrixes_NightGod", "Ulserous",
})


def _rows() -> dict[str, dict]:
    return {r["id"]: r for r in raw_units.DATA["units"] if r.get("id")}


def _flagless() -> set[str]:
    return {i for i, r in _rows().items() if r.get("flags") is None}


def _has(row: dict, bit: int) -> bool:
    flags = row.get("flags")
    return isinstance(flags, int) and bool(flags & bit)


def _codex_entries() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for entries in (raw_codex.DATA or {}).values():
        if isinstance(entries, list):
            for e in entries:
                if isinstance(e, dict) and e.get("id"):
                    out.setdefault(e["id"], e)
    return out


def _loose_rows() -> dict[str, dict]:
    """Rows straight off the game sheet - the only place the removed bits (and
    the prune they were removed by) can be seen. Skips without the game dump."""
    from farever_companion import paths
    sheet = paths.sheets_dir() / "unit.json"
    if not sheet.exists():
        pytest.skip("source unit sheet not present")
    raw = json.loads(sheet.read_text(encoding="utf-8"))
    rows = (raw if isinstance(raw, list)
            else (raw.get("lines") or raw.get("items") or []))
    return {r.get("id"): r for r in rows if r.get("id")}


def test_the_manifest_merge_bypass_census_is_stable():
    """The prune is BYPASSED, not applied: the compiled sheet carries a row for
    every pruned unit, and it carries no `flags` key. A fix that preserves the
    flags or stops the re-add turns this red - update the census then."""
    rows = _rows()
    flagless = _flagless()
    assert flagless == _FLAGLESS_RE_ADDED, (
        "the flagless re-add census moved:\n"
        f"  new     : {sorted(flagless - _FLAGLESS_RE_ADDED)}\n"
        f"  vanished: {sorted(_FLAGLESS_RE_ADDED - flagless)}")
    # every flagless row is still PRESENT (re-added), not dropped
    assert _FLAGLESS_RE_ADDED <= set(rows), sorted(_FLAGLESS_RE_ADDED - set(rows))
    # ...and it is flagless because the merge copies only id/name/type
    for uid in flagless:
        assert "flags" not in rows[uid], uid


def test_the_flagless_rows_are_exactly_the_pruned_set():
    """Proof the bypass is the PRUNE and nothing else: the flagless rows are
    exactly the rows the compiler's unit prune removed (NO-CODEX bit, or a
    non-special special-type). Skips without the game sheet."""
    from farever_companion.data import cdb
    loose = _loose_rows()
    named_boss_ids = {r["id"] for r in cdb.lines("lootTable")}
    pruned = set()
    for uid, row in loose.items():
        flags = row.get("flags", 0)
        if F.is_no_codex(flags):
            pruned.add(uid)
        elif row.get("type") in _SPECIAL_TYPES and not (
                F.is_special_flag(flags) or uid in named_boss_ids):
            pruned.add(uid)
    assert pruned == _FLAGLESS_RE_ADDED, (
        f"the prune set moved:\n  new     : {sorted(pruned - _FLAGLESS_RE_ADDED)}"
        f"\n  vanished: {sorted(_FLAGLESS_RE_ADDED - pruned)}")
    assert _flagless() == pruned


def test_the_pruned_rows_lose_their_unique_and_boss_bits():
    """The re-added rows carry none of their original bits, so the shim denies
    nine unique and one boss row that the game sheet marks. Skips without the
    dump (the bits only exist there)."""
    loose = _loose_rows()
    rows = _rows()
    lost_unique = {uid for uid in _FLAGLESS_RE_ADDED
                   if _has(loose.get(uid, {}), F.UNIQUE_FLAG_BIT)}
    lost_boss = {uid for uid in _FLAGLESS_RE_ADDED
                 if _has(loose.get(uid, {}), F.BOSS_FLAG_BIT)}
    assert lost_unique == _LOST_UNIQUE, sorted(lost_unique ^ _LOST_UNIQUE)
    assert lost_boss == _LOST_BOSS, sorted(lost_boss ^ _LOST_BOSS)
    # ...and the shim really denies what the sheet marks
    for uid in _LOST_UNIQUE:
        assert not units.is_unique(uid), uid
        assert not _has(rows.get(uid) or {}, F.UNIQUE_FLAG_BIT), uid
    assert not units.is_boss("Cleodora_Heroic_Dark_Champion")


def test_the_runtime_re_admits_no_codex_rows_its_bit_dropped():
    """`0x40000` is "not for the codex", but the rows come back flagless and
    the runtime's own heuristic re-admits 23 of them - and 18 are re-listed in
    the compiled codex outright. The eleven it does not re-admit are named."""
    flagless = _flagless()
    recovered = flagless & set(units.codex_unit_ids())
    assert recovered == _FLAGLESS_RE_ADDED - _RUNTIME_NOT_CODEX, (
        sorted(recovered ^ (_FLAGLESS_RE_ADDED - _RUNTIME_NOT_CODEX)))
    assert _RUNTIME_NOT_CODEX <= flagless, sorted(_RUNTIME_NOT_CODEX - flagless)
    listed = flagless & set(_codex_entries())
    assert listed == _RE_LISTED_IN_CODEX, sorted(listed ^ _RE_LISTED_IN_CODEX)


def test_the_dungeon_bake_keeps_the_mount_glider_false_positives():
    """`is_dungeon` is baked on 86 codex entries, but only 72 are in
    `dungeons.json`; the 14 extra are Mount/Glider collection rows. The runtime
    discards the flag for those regions, so the false positive is inert - but
    the bake and the reader still disagree."""
    baked = {i for i, e in _codex_entries().items() if e.get("is_dungeon")}
    d_mobs = set(dungeons.dungeon_unit_ids())
    assert baked - d_mobs == _DUNGEON_FALSE_POSITIVES, (
        sorted((baked - d_mobs) ^ _DUNGEON_FALSE_POSITIVES))
    assert not (d_mobs - baked), sorted(d_mobs - baked)


def test_the_codex_boss_bake_lags_is_boss_by_the_rift_clones():
    """`units.is_boss()` says 18, the codex bakes `is_boss` on 14: the four
    Rift clones / Night God are bosses to the runtime but not to the codex."""
    rows = _rows()
    runtime_boss = {i for i in rows if units.is_boss(i)}
    codex_boss = {i for i, e in _codex_entries().items() if e.get("is_boss")}
    assert runtime_boss - codex_boss == _CODEX_BOSS_GAP, (
        sorted((runtime_boss - codex_boss) ^ _CODEX_BOSS_GAP))
    assert not (codex_boss - runtime_boss), sorted(codex_boss - runtime_boss)
