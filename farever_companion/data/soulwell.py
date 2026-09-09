"""Soulwell offering tunables — the "Sacrifice Corrupted Gift" station.

The Soulwell is the offering station: sacrificing one of the 24
AugmentDemon gift items grants one of five luck buffs (which one is
chosen server-side, so the app can only list the possibilities).
Every tunable a buff timer needs is game-authored, in two sheets:

* constant.json's `Soulwell` row — a group of named values: the buff
  durations (rare/epic minutes), the reputation an offering gains,
  and the reputation-level duration bonus.
* constant.json's `Soulwell_Statuses` row — the five buff status ids
  an offering can grant.
* counter.json's `Luck_*` rows — each buff's luck math while it is
  up: base chance, per-stack increment, cap, and the item type (and
  minimum rarity) the counter ticks on.

Everything reads through cdb, so the compiled shim serves the frozen
build and the loose sheets cover a checkout whose shim predates them.
"""
from __future__ import annotations

from functools import lru_cache

from . import cdb

#: The constant row carrying the offering's tunables (a `group` of
#: named int values).
_SOULWELL_ID = "Soulwell"
#: The constant row listing the buffs an offering can grant.
_SOULWELL_STATUSES_ID = "Soulwell_Statuses"


@lru_cache(maxsize=1)
def _constants() -> dict[str, dict]:
    return cdb.by_id("constant")


def soulwell_tunables() -> dict[str, int | float]:
    """The Soulwell group's named values, as the sheet states them:

    `Status_Duration_Base` / `Status_Duration_Rare` /
    `Status_Duration_Epic` (minutes), `Reputation_Gain_Rare` /
    `Reputation_Gain_Epic` (reputation points per offering), and
    `Reputation_Duration_Bonus` (extra minutes at Riftstalkers
    reputation level 5).
    """
    row = _constants().get(_SOULWELL_ID) or {}
    out: dict[str, int | float] = {}
    for entry in (row.get("v") or {}).get("group") or []:
        val = entry.get("v") or {}
        for kind in ("int", "float"):
            if kind in val:
                out[str(entry.get("id", ""))] = val[kind]
                break
    return out


def soulwell_buff_ids() -> list[str]:
    """The five buff status ids an offering can grant, in sheet order
    (Luck_LegendaryWeapon_Status, Luck_Mount_Status,
    Luck_Glider_Status, Luck_RareMaterial_Status,
    Luck_PrismaticGear_Status)."""
    row = _constants().get(_SOULWELL_STATUSES_ID) or {}
    other = (row.get("v") or {}).get("other") or {}
    return [s["ref"] for s in other.get("skills") or [] if s.get("ref")]


def buff_duration_minutes(rarity: str) -> int:
    """How long an offering's buff lasts, in minutes: 12 for a Rare
    gift, 24 for an Epic one — before the reputation-level bonus,
    which the game adds at runtime (see
    `reputation_duration_bonus`)."""
    key = {"rare": "Status_Duration_Rare",
           "epic": "Status_Duration_Epic"}.get(str(rarity).lower())
    if key is None:
        raise ValueError(f"rarity must be 'rare' or 'epic', got {rarity!r}")
    return int(soulwell_tunables().get(key, 0))


def reputation_gain(rarity: str) -> int:
    """Reputation points one offering gives: 10 (Rare) / 20 (Epic)."""
    key = {"rare": "Reputation_Gain_Rare",
           "epic": "Reputation_Gain_Epic"}.get(str(rarity).lower())
    if key is None:
        raise ValueError(f"rarity must be 'rare' or 'epic', got {rarity!r}")
    return int(soulwell_tunables().get(key, 0))


def reputation_duration_bonus() -> int:
    """Extra buff minutes the game grants at Riftstalkers reputation
    level 5 (the sheet's Reputation_Duration_Bonus)."""
    return int(soulwell_tunables().get("Reputation_Duration_Bonus", 0))


@lru_cache(maxsize=1)
def luck_counters() -> dict[str, dict]:
    """counter.json's luck rows by counter id — only the rows that
    carry `luckParams` (the five `Luck_*` counters). Each value is
    that params block: the buff `status` it ticks, the `base`
    chance, the per-stack `increment`, the `max` cap, and, where the
    counter is scoped to one item type, `itemType` and `minRarity`."""
    out: dict[str, dict] = {}
    for row in cdb.lines("counter"):
        params = row.get("luckParams")
        if params and row.get("id"):
            out[str(row["id"])] = params
    return out


def luck_counter_for_status(status_id: str | None) -> str | None:
    """The luck counter whose buff status is `status_id`
    ('Luck_Mount_Status' -> 'Luck_Mount'), or None when no counter
    grants that buff."""
    if not status_id:
        return None
    for counter_id, params in luck_counters().items():
        if params.get("status") == status_id:
            return counter_id
    return None


def luck_scope(counter_id: str) -> tuple[str | None, str | None]:
    """The item type (and minimum rarity) a counter ticks on, where
    the counter is scoped: ('Mount', None), ('CraftingComponent',
    'Rare'), or (None, None) for the global counters."""
    params = luck_counters().get(counter_id) or {}
    item_type = params.get("itemType")
    min_rarity = params.get("minRarity")
    return (str(item_type) if item_type else None,
            str(min_rarity) if min_rarity else None)


def luck_chance(counter_id: str, stacks: int = 0) -> float | None:
    """The luck chance at `stacks` stacks: `base` + `increment` per
    stack, capped at `max`. Flat counters (increment 0) ignore the
    stack count. None when the counter id is unknown."""
    params = luck_counters().get(counter_id)
    if not params:
        return None
    chance = (float(params.get("base", 0))
              + float(params.get("increment", 0)) * stacks)
    return min(chance, float(params.get("max", chance)))
