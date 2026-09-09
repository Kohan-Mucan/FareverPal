"""The MEASURED gear-upgrade costs, and the one place that answers "what
does this +N cost".

Background. The shipped sheet computes a step's cost as
`base[rank - 1] x level ** levelExponent`, and the app applied that formula
in TWO places independently: `stats.upgrade_ladder` (the per-item ladder) and
`ui/pages/items/enchants_rows.py` (the Enchants cost matrix). Two
computations of one number is a drift risk, so both now call
`stats.upgrade_step_cost`.

Hand-measured in the live game on 2026-09-26 at item level 25, the formula is
wrong, and the measurement established three things:

  * CRYSTAL is EXACT - it matched the sheet's base array (0,0,0,3,15) to the
    unit at every step. So `base[rank - 1]` is the right indexing, the
    zero-cost first steps are real, and the figures are per-step, not
    cumulative. The off-by-one theory is refuted by this data.
  * A step charges SEVERAL materials, not one. Rare +2 is 112 Spark Dust AND
    18 Spark Shard. The sheet implies one material per rarity, so the old
    model could not even represent what the game does.
  * DUST and SHARD do not follow `base x level^exp`: the ratio of measured to
    sheet climbs ~1.05x per STEP (a level term is flat at 1.1746 for level
    25), and there is a per-rarity multiplier (~1.21x Rare->Epic, ~1.34x
    Rare->Legendary) that nothing in the shipped cost path applies.

A second round of measurements (rare at 20, epic at 12/14) showed the LEVEL
term is not a power law either: rare's 20->25 ratio differs per step
(1.293 / 1.287 / 1.275) and epic +3 rises only 1.053 from 12->14 but 1.798
from 14->25. So there is no formula to derive - only a table. Levels between
two measurements are interpolated log-linearly and labelled as such; outside
the measured range the shipped formula answers and says so, because the
measurements show that path is wrong by 3-5x.

What the same data DID settle is the RARITY multiplier, and that one is a
clean constant. Level 25 is the only level where all three rarities were read
on the same steps, so Epic/Rare and Legendary/Rare can be solved there. The
ratio is the same on every step it was solved from (spread 0.69% Epic, 0.12%
Legendary), and applied back to Rare it reproduces 4 of the 5 measured
Epic/Legendary level-25 cells exactly and the fifth to within 1. So a step
that was never measured for a rarity is DERIVED from Rare at that same level
times the multiplier - every material scaling together, so a derived step
still lists its shard and crystal. It cannot fire without a Rare anchor at
that level, so it can never invent a level curve.

RE-MEASURING after a game update: docs/UPGRADE_COSTS.md is the how-to - how to
read a step off the live upgrade panel, which rows to add, and which pinned
numbers in the UI tests move as a result. This file is the gate it has to pass.
"""
from __future__ import annotations

from farever_companion.data.items import stats as istats

RARITIES = ("Rare", "Epic", "Legendary")


# --- the measured table -----------------------------------------------------

def test_level_25_coverage_is_complete_except_where_it_is_genuinely_absent():
    """Most steps were read at level 25, but coverage is uneven by nature -
    epic +2 was only ever read at level 12. Where a measurement exists it must
    be exact; where it does not, the caller must get a labelled answer rather
    than a silent zero."""
    gaps = []
    for rar in RARITIES:
        for step in range(1, istats.upgrade_cap(rar) + 1):
            res = istats.measured_upgrade_cost(rar, step, 25)
            if res is None:
                gaps.append((rar, step))
                continue
            got, source = res
            assert source == istats.MEASURED
            assert got
    # the one known hole, recorded rather than papered over
    assert gaps == [("Epic", 2)]


def test_an_unmeasured_step_is_derived_from_the_rarity_multiplier():
    """Epic +2 has no level-25 reading. Rare's does, so the answer is scaled
    from it and labelled DERIVED - a far better number than the sheet, and
    honestly marked as not-measured."""
    costs, source = istats.upgrade_step_cost("Epic", 2, 25)
    assert source == istats.DERIVED
    assert costs
    # ...and it is NOT the level-12 measurement silently reused
    assert costs[0]["count"] != 72
    # it scales Rare's 112 dust by 1.2117, and carries the shard too
    assert costs[0]["count"] == round(112 * istats._RARITY_COST_MULT["Epic"])
    assert [c["material"] for c in costs] == ["Spark Dust", "Spark Shard"]


def test_a_derived_cost_is_cross_validated_by_a_held_out_ratio():
    """The real check on the fill. If epic +2 at 25 is 136, its 12->25 ratio
    must match the ratio epic +3 actually shows over the same span - the two
    are independent readings of the same level curve."""
    derived, _ = istats.upgrade_step_cost("Epic", 2, 25)
    at_12 = {c["material"]: c["count"]
             for c in istats.measured_upgrade_cost("Epic", 2, 12)[0]}
    at_25 = {c["material"]: c["count"]
             for c in istats.measured_upgrade_cost("Epic", 3, 25)[0]}
    r_derived = derived[0]["count"] / at_12["Spark Dust"]
    r_measured = at_25["Spark Dust"] / 113        # epic +3 at level 12
    assert abs(r_derived - r_measured) / r_measured < 0.01


def test_the_rarity_multiplier_predicts_the_cells_it_was_solved_from():
    """Held-out validation of `_RARITY_COST_MULT`. Apply it back to Rare and
    it must land on the measured Epic/Legendary figures - this is what makes a
    single constant defensible rather than a per-step fudge."""
    primary = {k: v[0][1] for k, v in istats._MEASURED_UPGRADES.items()}
    checked = 0
    for rar in ("Epic", "Legendary"):
        mult = istats._RARITY_COST_MULT[rar]
        for step in range(1, 6):
            rare = primary.get(("Rare", step, 25))
            got = primary.get((rar, step, 25))
            if rare is None or got is None:
                continue
            checked += 1
            assert abs(round(rare * mult) - got) <= 1, (rar, step)
    assert checked >= 5, "the multiplier must be validated, not assumed"


def test_the_multiplier_does_not_invent_a_level_curve():
    """The derived path is anchored on Rare's answer AT THE SAME LEVEL. Past
    the measurements that anchor is Rare's EXTRAPOLATION, so Epic/Legendary
    still derive from it (marked DERIVED, the same provenance as always). For
    steps Rare never had (its +4/+5 — the ladder is shortest there) the anchor
    is the rarity's OWN highest measured row grown by the SAME level curve, so
    Legendary +4/+5 keep their full material list at any level."""
    assert istats.upgrade_step_cost("Epic", 2, 30)[1] == istats.DERIVED
    assert istats.upgrade_step_cost("Legendary", 2, 40)[1] == istats.DERIVED
    # level 12 is below every Rare reading, so Epic +2 answers from the
    # reading taken THERE, and the rarity's own row is not consulted
    assert istats.upgrade_step_cost("Epic", 2, 12)[1] == istats.MEASURED
    # and the grown row keeps every material — not a lone sheet crystal
    mats = [c["material"]
            for c in istats.upgrade_step_cost("Legendary", 4, 30)[0]]
    assert mats == ["Spark Dust", "Spark Shard", "Spark Crystal"]


def test_nothing_is_derived_below_the_evidence_floor():
    """The level curve is fitted on ONE short span (Rare's 20->25), so it is a
    local approximation. Running it far below the readings is extrapolating
    from nothing: it priced a level-1 Epic +1 at 2 Spark Dust and Legendary +5
    at 17, figures far CHEAPER than the measured level above them, so a step
    appeared to get less expensive as the gear got better.

    Below the anchor rarity's lowest measured level (20) the derivation now
    reports nothing at all, and the sheet formula does NOT step in to replace
    it — it is the formula the measurements overturned, and `base[0]` is 0 for
    the early steps, so it priced those at 0.0, which reads as free.

    A step read AT such a level still answers — that is a reading or an
    interpolation between two readings, not the curve run backwards (Epic +2
    was read at 12, and +3/+4 at 12 and 14, all below Rare's floor of 20).
    """
    floor = min(lv for r, _s, lv in istats._MEASURED_UPGRADES
                if r == istats._COST_ANCHOR_RARITY)
    for rar in RARITIES:
        for step in range(1, istats.upgrade_cap(rar) + 1):
            levels = istats.measured_levels_for(rar, step)
            for lv in range(1, floor):
                costs, source = istats.upgrade_step_cost(rar, step, lv)
                bracketed = any(a <= lv <= b
                                for a, b in zip(levels, levels[1:]))
                if lv in levels:
                    assert source == istats.MEASURED, (rar, step, lv, source)
                elif bracketed:
                    assert source == istats.INTERPOLATED, (rar, step, lv, source)
                else:
                    assert costs == [], (rar, step, lv, costs, source)


def test_a_cost_never_gets_cheaper_as_the_gear_gets_better():
    """The invariant the floor exists to protect, checked directly rather than
    through any one step: across every rarity, every step and every level in a
    wide sweep, a cost only ever rises. This is what caught a derived figure
    sitting BELOW a measurement above it (Legendary +4 read 423 Spark Dust at
    level 24 against a measured 330 at level 25)."""
    for rar in RARITIES:
        for step in range(1, istats.upgrade_cap(rar) + 1):
            prev = None
            for lv in range(1, 121):
                costs, _src = istats.upgrade_step_cost(rar, step, lv)
                if not costs or not costs[0]["count"]:
                    continue          # no answer at this level: nothing to order
                n = costs[0]["count"]
                if prev is not None:
                    assert n >= prev, (rar, step, prev, lv, n)
                prev = n


def test_the_own_row_fallback_does_not_double_count_the_rarity_premium():
    """When a step has no Rare anchor at all (Legendary +4/+5), the anchor is
    the RARITY'S OWN measured row — which already carries its 1.34x premium —
    grown by the level factor alone. Applying the multiplier on top of it
    counted the premium twice, which is what put Legendary +4 at 423 Spark Dust
    one level BELOW its own measured 330."""
    prev = None
    for lv in range(20, 31):
        costs, source = istats.upgrade_step_cost("Legendary", 4, lv)
        dust = {c["material"]: c["count"] for c in costs}["Spark Dust"]
        if lv == 25:
            assert source == istats.MEASURED
        else:
            assert source == istats.DERIVED, (lv, source)
        # the level-25 reading is the anchor, so nothing AT OR BELOW it may
        # exceed it (the bug read 423 there, 28% over its own measurement)
        if lv <= 25:
            assert dust <= 330, (lv, dust)
        if prev is not None:
            assert dust >= prev, (lv, prev, dust)   # and it never falls
        prev = dust
    # at the level it was measured, the measured row is used verbatim
    assert istats.upgrade_step_cost("Legendary", 4, 25)[1] == istats.MEASURED


def test_rare_is_never_derived_from_itself():
    """The anchor has no multiplier above 1.0, so it must never take the
    derived path - it is either measured, interpolated, or absent."""
    for step in range(1, istats.upgrade_cap("Rare") + 1):
        for level in (12, 20, 22, 25, 30, 40):
            assert istats.upgrade_step_cost("Rare", step, level)[1] != \
                istats.DERIVED


def test_measured_numbers_match_the_game_exactly():
    """The figures as read off the live upgrade UI. These are the whole point
    of the exercise - if one drifts, the override is wrong."""
    assert istats.measured_upgrade_cost("Rare", 2, 25)[0] == [
        {"material": "Spark Dust", "count": 112},
        {"material": "Spark Shard", "count": 18},
    ]
    assert istats.measured_upgrade_cost("Epic", 4, 25)[0] == [
        {"material": "Spark Dust", "count": 300},
        {"material": "Spark Shard", "count": 75},
        {"material": "Spark Crystal", "count": 3},
    ]
    assert istats.measured_upgrade_cost("Legendary", 5, 25)[0] == [
        {"material": "Spark Dust", "count": 434},
        {"material": "Spark Shard", "count": 115},
        {"material": "Spark Crystal", "count": 15},
    ]


def test_a_step_can_cost_several_materials():
    """The structural finding: it is NOT one material per rarity. Rare +2
    needs dust AND shard; only +1 is a single material."""
    assert len(istats.measured_upgrade_cost("Rare", 1, 25)[0]) == 1
    assert len(istats.measured_upgrade_cost("Rare", 2, 25)[0]) == 2


def test_zero_cost_materials_are_not_listed():
    """A step lists exactly what the player must have, so a material the game
    charges nothing for must not appear as a 0."""
    for key, row in istats._MEASURED_UPGRADES.items():
        for mat, count in row:
            assert count > 0, f"{key} lists {mat} at 0"


# --- the level-25-only guard ------------------------------------------------

def test_a_level_outside_the_measured_range_is_not_claimed_as_measured():
    """None means 'nothing to say here', never 'free'. A level past every
    measurement is answered by EXTRAPOLATION (never MEASURED), and a level
    below every measurement still gets None rather than a downward
    extrapolation - the curve is extended up only."""
    costs, source = istats.measured_upgrade_cost("Rare", 2, 30)
    assert source == istats.EXTRAPOLATED and costs
    dust = {c["material"]: c["count"] for c in costs}["Spark Dust"]
    assert dust > 112                 # past the level-25 measurement, costs more
    _, source = istats.measured_upgrade_cost("Epic", 4, 100)
    assert source == istats.EXTRAPOLATED
    # level 12 sits BELOW every rare measurement: no downward extrapolation
    assert istats.measured_upgrade_cost("Rare", 2, 12) is None


def test_levels_are_interpolated_only_between_two_measurements():
    """Level 22 sits strictly between measured 20 and 25, so it is answered -
    and labelled as an interpolation, not as a measurement."""
    costs, source = istats.measured_upgrade_cost("Rare", 2, 22)
    assert costs and source == istats.INTERPOLATED
    dust = costs[0]["count"]
    assert 87 < dust < 112          # strictly between the two measurements


def test_interpolation_lands_close_on_a_held_out_level():
    """The honest check on the interpolation: epic +3 was measured at 12, 14
    and 25. Trained ONLY on 12 and 25, predicting 14 must be far better than
    the shipped formula - which is what justifies interpolating at all."""
    import math
    lo, hi, target, actual = 12, 25, 14, 119
    a, b = 113, 214
    t = (math.log(target) - math.log(lo)) / (math.log(hi) - math.log(lo))
    pred = a ** (1 - t) * b ** t
    sheet = 45 * (target ** 0.05)
    assert abs(pred - actual) / actual < 0.10      # within 10%
    assert abs(sheet - actual) / actual > 0.5      # the sheet is off by >50%


def test_provenance_is_reported_for_every_answer():
    """A caller can always tell what it is looking at, at any level."""
    for rar in RARITIES:
        for step in range(1, istats.upgrade_cap(rar) + 1):
            for level in (12, 14, 20, 22, 25, 30):
                _, source = istats.upgrade_step_cost(rar, step, level)
                assert source in (istats.MEASURED, istats.INTERPOLATED,
                                  istats.EXTRAPOLATED, istats.DERIVED,
                                  istats.UNVERIFIED)


def test_upgrade_step_cost_reports_whether_it_is_measured():
    costs, source = istats.upgrade_step_cost("Rare", 2, 25)
    assert source == istats.MEASURED
    assert costs[0]["count"] == 112

    costs, source = istats.upgrade_step_cost("Rare", 2, 40)
    assert source == istats.EXTRAPOLATED     # the curve, extended past 25
    assert costs and costs[0]["count"] > 0   # and it still answers


def test_the_sheet_fallback_is_the_old_formula():
    """The unmeasured path must still be `base[rank-1] x level^exp` - this is
    a fallback, not a new formula. With the curve extended and derivation
    anchored on own-row growth, it now only fires for a rarity with NO
    measured row for the step at all (Epic +5: cap 4, never measured, and
    Rare — its anchor — has no +5 either)."""
    _, source = istats.upgrade_step_cost("Epic", 5, 30)
    assert source == istats.UNVERIFIED
    costs, _ = istats.upgrade_step_cost("Epic", 5, 30)
    # UpgradeEpic base = (0,0,0,3,15), exp 0 — so +5 is a flat 15
    assert costs[0]["material"] == "Spark Crystal"
    assert costs[0]["count"] == 15


def test_the_item_ladder_and_upgrade_costs_price_the_same_level():
    """The two per-ITEM accessors must answer for the SAME level.

    `upgrade_ladder` resolves the level through `_table_level` (the item's own
    scale top, or its fixed craft level). `upgrade_costs` used to default to
    the bundled max level instead, so the two disagreed for 155 of 309 gear
    items: a level-20 Rare drop showed 41/87/138 in its ladder and 53/112/176
    in its costs — two cost tables for one item, neither obviously the right
    one. This walks the real item sheet, so a new drop at a new level is
    covered the day it ships.
    """
    from farever_companion.data import items as idata

    checked = disagreeing = 0
    for it in idata.items():
        if not idata.is_gear(it):
            continue
        ladder = idata.upgrade_ladder(it)
        costs = idata.upgrade_costs(it)
        if not ladder or not costs:
            continue
        own = next((c for c in ladder
                    if (c.get("rarity") or "").lower()
                    == (it.get("rarity") or "").lower()), None)
        if not own:
            continue
        checked += 1
        by_rank = {c["upgrade"]: c for c in costs}
        for step in own.get("steps", []):
            row = by_rank.get(step["upgrade"])
            if row is None:
                # the ladder may price a step the cost lookup has no reading
                # for at that level — but then it must not carry a number
                assert "count" not in step, (it.get("id"), step)
                continue
            assert step["count"] == row["count"], (it.get("id"), step, row)
    assert checked > 100, "the sheet walk found nothing to compare"
    assert disagreeing == 0


# --- one source of truth ----------------------------------------------------
def test_ladder_and_cost_lookup_agree_at_level_25():
    """The bug this accessor exists to prevent: the ladder and the matrix used
    to compute the cost separately and could disagree. They must not."""
    for rar in RARITIES:
        for step in range(1, istats.upgrade_cap(rar) + 1):
            a, _ = istats.upgrade_step_cost(rar, step, 25)
            got = istats.measured_upgrade_cost(rar, step, 25)
            assert a == (got[0] if got else a)


def test_crystal_costs_are_unchanged_by_the_override():
    """Crystal matched the shipped sheet exactly, so it is a regression guard:
    the override must not have disturbed the one material that was already
    right."""
    for step, want in ((4, 3), (5, 15)):
        costs, _ = istats.upgrade_step_cost("Legendary", step, 25)
        crystal = [c for c in costs if c["material"] == "Spark Crystal"]
        assert crystal and crystal[0]["count"] == want


# --- the levels the tab offers ---------------------------------------------

def test_the_offered_levels_are_the_anchor_rarity_s_measurements():
    """`upgrade_cost_levels` is what the Upgrades tab's LV chips read, so the
    chips are the levels something was actually READ at rather than a
    `max_level + 5` schedule: the anchor rarity's own measured levels, capped
    at the game's max. Measuring Rare at a new level adds a chip with no UI
    change; the highest one is the default."""
    cap = istats.gear_scaling().get("max_level") or 25
    lvls = istats.upgrade_cost_levels()
    assert lvls == sorted(lvls) and lvls
    assert max(lvls) == min(cap, max(istats.measured_levels_for("Rare", 1)))
    for lv in lvls:
        assert lv <= cap
        assert lv in istats.MEASURED_UPGRADE_LEVELS


def test_no_offered_level_shows_a_guess():
    """The reason the offered levels are filtered to the anchor's: at any of
    them EVERY live cell is on real data - measured, interpolated between two
    measurements, or Rare's measured row scaled by the validated multiplier.
    A level past the measurements would put extrapolation and the 3-5x-off
    sheet formula on screen, which is what the chips refuse to do."""
    for lv in istats.upgrade_cost_levels():
        for rar in RARITIES:
            for step in range(1, istats.upgrade_cap(rar) + 1):
                _, source = istats.upgrade_step_cost(rar, step, lv)
                assert source in (istats.MEASURED, istats.INTERPOLATED,
                                  istats.DERIVED), (rar, step, lv, source)
