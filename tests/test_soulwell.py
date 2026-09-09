"""The Soulwell offering tunables are the game's own, not ours.

`data/soulwell.py` reads the constant sheet's `Soulwell` rows and the
counter sheet's `Luck_*` rows through cdb, so buff timers follow
whatever the shipped sheets say. These tests pin the accessor against
the real baked data: which rows it needs (a shim that predates the
sheet sync must fail here, not silently serve empty tunables), how the
durations and luck math combine, and the rare/epic choice a sacrifice
makes.
"""
from __future__ import annotations

import pytest

from farever_companion.data import cdb, raw_data, soulwell


def test_the_shim_carries_the_soulwell_sheets():
    """The build contract: the baked raw_data actually holds the
    Soulwell rows and the luck counters. `test_compiler.py` proves a
    rebuild reproduces the committed shims, but a committed shim can
    itself be stale — this is the assertion that catches that."""
    constants = cdb.by_id("constant")
    assert "Soulwell" in constants, "constant.json lost its Soulwell row"
    assert "Soulwell_Statuses" in constants, (
        "constant.json lost its Soulwell_Statuses row")
    counters = cdb.lines("counter")
    assert counters, "counter.json is not baked into raw_data"
    assert any(row.get("luckParams") for row in counters), (
        "counter.json lost its luckParams rows")


def test_offering_tunables_are_the_sheet_six_values():
    """The constant sheet's Soulwell group, as the game authors it:
    buff minutes by rarity, reputation per offering, and the
    reputation-level duration bonus."""
    tunables = soulwell.soulwell_tunables()
    assert tunables == {
        "Status_Duration_Base": 0,
        "Status_Duration_Rare": 12,
        "Status_Duration_Epic": 24,
        "Reputation_Gain_Rare": 10,
        "Reputation_Gain_Epic": 20,
        "Reputation_Duration_Bonus": 15,
    }


def test_buff_durations_and_rep_follow_rarity():
    """A Rare gift runs half an Epic one and gives half the
    reputation; the base duration is the un-bonused floor."""
    assert soulwell.buff_duration_minutes("rare") == 12
    assert soulwell.buff_duration_minutes("epic") == 24
    assert soulwell.buff_duration_minutes("epic") == (
        2 * soulwell.buff_duration_minutes("rare"))
    assert soulwell.reputation_gain("rare") == 10
    assert soulwell.reputation_gain("epic") == 20
    with pytest.raises(ValueError):
        soulwell.buff_duration_minutes("legendary")
    with pytest.raises(ValueError):
        soulwell.reputation_gain("common")


def test_buff_ids_are_the_five_luck_statuses():
    """An offering grants exactly one of the five luck buffs (which
    one is the server's choice — the app lists the possibilities)."""
    buffs = soulwell.soulwell_buff_ids()
    assert set(buffs) == {
        "Luck_LegendaryWeapon_Status", "Luck_Mount_Status",
        "Luck_Glider_Status", "Luck_RareMaterial_Status",
        "Luck_PrismaticGear_Status",
    }
    assert len(buffs) == len(set(buffs)) == 5


def test_every_buff_has_a_luck_counter():
    """Each buff status is ticked by exactly one counter row, and the
    counter names the buff back — the pairing the buff timer uses to
    find the chance math for a given buff."""
    for buff in soulwell.soulwell_buff_ids():
        counter = soulwell.luck_counter_for_status(buff)
        assert counter, f"no luck counter grants {buff}"
        assert soulwell.luck_counters()[counter]["status"] == buff
    assert soulwell.luck_counter_for_status("Not_A_Buff_Status") is None
    assert soulwell.luck_counter_for_status(None) is None


def test_luck_chance_is_base_plus_increment_capped():
    """Chance at N stacks = base + N*increment, capped at max — the
    sheet's own numbers, so a game re-tune moves the test with it."""
    for counter_id, params in soulwell.luck_counters().items():
        base = float(params["base"])
        increment = float(params.get("increment", 0))
        cap = float(params["max"])
        assert soulwell.luck_chance(counter_id, 0) == pytest.approx(base)
        assert soulwell.luck_chance(counter_id, 1) == pytest.approx(
            min(base + increment, cap))
        assert soulwell.luck_chance(counter_id, 10_000) == pytest.approx(cap)
    # a flat counter (no increment) ignores the stack count entirely
    flat = [c for c, p in soulwell.luck_counters().items()
            if not p.get("increment")]
    assert flat, "the sheet lost its flat luck counter"
    for counter_id in flat:
        assert soulwell.luck_chance(counter_id, 7) == pytest.approx(
            soulwell.luck_chance(counter_id, 0))
    assert soulwell.luck_chance("Not_A_Counter", 3) is None


def test_luck_scope_names_the_counters_item_type():
    """Scoped counters tick on one item type (Mount luck on mounts,
    rare-material luck on Rare crafting components); global counters
    (legendary-weapon luck) are unscoped."""
    scoped = {
        counter: soulwell.luck_scope(counter)
        for counter in soulwell.luck_counters()
    }
    assert scoped["Luck_Mount"] == ("Mount", None)
    assert scoped["Luck_Glider"] == ("GearGlider", None)
    assert scoped["Luck_RareMaterial"] == ("CraftingComponent", "Rare")
    assert scoped["Luck_LegendaryWeapon"] == (None, None)


def test_reputation_duration_bonus_is_readable():
    """The extra minutes the game adds at Riftstalkers reputation
    level 5 — the timer shows it on top of the rarity duration."""
    assert soulwell.reputation_duration_bonus() == 15
