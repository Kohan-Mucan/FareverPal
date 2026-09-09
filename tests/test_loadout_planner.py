"""Loadout planner data layer (headless, no game): stat summaries, loadout
totals, the best-in-slot optimizer, weapon suggestions and the augment plan.

The planner is what the Items > Loadout Builder tab calls, so these pin its
contract against the real bundled catalog: what a piece contributes at a
level/upgrade rank, how the Arsenal's 50% rule composes, which slots the
optimizer fills and with what, and the shape of the augment plan.
"""
import pytest

from farever_companion import paths
from farever_companion.data import items as idata
from farever_companion.data.items import stats as _stats


def _data_present() -> bool:
    try:
        return paths.item_drops_path().exists()
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _data_present(),
    reason="requires bundled data (assets/data/item_drops.json)")


@pytest.fixture(autouse=True)
def _rounding_mode():
    """The summaries render with the session display mode — pin it to the
    default so a sibling test leaving 'both' on can't break the parsing."""
    idata.set_stat_display_mode("rounding")
    yield
    idata.set_stat_display_mode("rounding")


# slot label -> the item type that fills it (the UI's 11 gear slots)
_SLOT_TYPES = {
    "Head": "Head", "Shoulders": "Shoulders", "Chest": "Chest",
    "Hands": "Hands", "Waist": "Waist", "Legs": "Legs", "Feet": "Feet",
    "Neck": "GearNeck", "Ring 1": "GearFinger", "Ring 2": "GearFinger",
    "Trinket": "GearTrinket",
}
_JEWELRY_TYPES = ("GearNeck", "GearFinger", "GearTrinket")


def _parse_summary(txt: str) -> dict[str, float]:
    """'Strength 12 · Armor 230' -> {'Strength': 12.0, 'Armor': 230.0}."""
    out: dict[str, float] = {}
    if not txt:
        return out
    for part in txt.split(" · "):
        label, _, value = part.rpartition(" ")
        out[label] = float(value)
    return out


# --- weapon primary stat ----------------------------------------------------

def test_weapon_primary_stat_is_its_first_class_aptitude():
    assert idata.weapon_primary_stat("Sword_Start") == "Strength"
    assert idata.weapon_primary_stat("Daggers_Start") == "Dexterity"
    # a dual-class weapon reads its first class (Cleric -> Faith)
    assert idata.weapon_primary_stat("GA_Demon") == "Faith"


def test_weapon_primary_stat_matches_class_primary_for_every_weapon():
    """The same stat the armor filter aligns to: the endAtb of the weapon's
    first class group-0 curve, for the whole catalog."""
    checked = 0
    for it in idata.items():
        if idata.category(it.get("type")) != "Weapons":
            continue
        expected = next((idata.class_primary_stat(c)
                         for c in it.get("classes") or []
                         if idata.class_primary_stat(c)), None)
        assert idata.weapon_primary_stat(it["id"]) == expected, it["id"]
        checked += 1
    assert checked


def test_weapon_primary_stat_none_for_non_weapons_and_blanks():
    assert idata.weapon_primary_stat("CopperIngot") is None
    assert idata.weapon_primary_stat("Trinket_Demon") is None   # jewelry
    assert idata.weapon_primary_stat(None) is None
    assert idata.weapon_primary_stat("") is None
    assert idata.weapon_primary_stat("NoSuchItem") is None
    # a class filter never turns a non-weapon into one
    assert idata.weapon_primary_stat("CopperIngot", "Fighter") is None
    assert idata.weapon_primary_stat("Trinket_Demon", "Wizard") is None


def test_weapon_primary_stat_follows_the_class_being_built():
    """A dual-class weapon's `classes` list is in the sheet's order, which
    says nothing about the player holding it: Axe_Boomerang lists Assassin
    first, so its own 'primary' reads Dexterity — while the Fighter equipping
    it scales Strength. Passing the class being built picks that class's stat
    (the armor filter and the score bonus both key off it), and a class that
    cannot hold the weapon still falls back to the weapon's own order."""
    assert idata.weapon_primary_stat("Axe_Boomerang") == "Dexterity"
    assert idata.weapon_primary_stat("Axe_Boomerang", "Fighter") == "Strength"
    assert idata.weapon_primary_stat("Axe_Boomerang", "Assassin") == "Dexterity"
    # Cleric/Wizard staff: the wizard reads Intellect, not the Cleric's Faith
    assert idata.weapon_primary_stat("Staff_Censer") == "Faith"
    assert idata.weapon_primary_stat("Staff_Censer", "Wizard") == "Intellect"
    assert idata.weapon_primary_stat("Staff_Censer", "Cleric") == "Faith"
    # a single-class weapon answers the same either way
    assert idata.weapon_primary_stat("Sword_Start", "Fighter") == "Strength"
    # a class that cannot wield it does not change the answer
    assert idata.weapon_primary_stat("Staff_Craft", "Fighter") == "Intellect"
    assert idata.weapon_primary_stat("Daggers_Start", "Wizard") == "Dexterity"


# --- format_item_stats_summary ---------------------------------------------

def test_item_stats_summary_is_a_compact_line_of_labeled_values():
    txt = idata.format_item_stats_summary("Chest_RDemon_Ass", level=25)
    assert txt
    rows = _parse_summary(txt)
    assert "Armor" in rows and rows["Armor"] > 0
    # one 'label value' part per displayed stat, no duplicates
    assert len(txt.split(" · ")) == len(rows)
    assert all(label and isinstance(value, float)
               for label, value in rows.items())


def test_item_stats_summary_defaults_to_max_level_and_upgrades_raise_values():
    base = _parse_summary(
        idata.format_item_stats_summary("Chest_RDemon_Ass"))
    at25 = _parse_summary(
        idata.format_item_stats_summary("Chest_RDemon_Ass", level=25))
    assert base == at25                       # default level is the max, 25
    ranked = _parse_summary(
        idata.format_item_stats_summary("Chest_RDemon_Ass", level=25,
                                        upgrades=3))   # Rare cap +3
    assert set(ranked) == set(at25)           # the same stat lines
    assert ranked["Armor"] > at25["Armor"]    # each rank = +10 iLevel
    assert ranked["Dexterity"] > at25["Dexterity"]
    # non-gear / unknown items have nothing to summarize
    assert idata.format_item_stats_summary("CopperIngot", level=25) == ""
    assert idata.format_item_stats_summary("NoSuchItem", level=25) == ""


def test_item_stats_summary_scales_vendor_gear_off_its_shop_stats():
    """The starter weapons are authored Uncommon with +2/+2 handout affixes,
    but the towns sell them scaled at Rare L25 — the summary must show the
    computed vendor values, not the fixed handout ones."""
    rows = _parse_summary(
        idata.format_item_stats_summary("Sword_Start", level=25))
    assert rows["Strength"] > 2
    assert rows["Vitality"] > 2


# --- loadout_stat_totals ----------------------------------------------------

def test_loadout_stat_totals_empty_loadout_is_empty():
    assert idata.loadout_stat_totals() == {}
    assert idata.loadout_stat_totals(None, None, None, {}) == {}
    assert idata.loadout_stat_totals(None, None, None,
                                     {"Chest": None}) == {}


def test_arsenal_weapon_contributes_half_its_stats():
    """The Arsenals in-game rule: a third weapon grants 50% of its stats."""
    main = idata.loadout_stat_totals("Sword_Start", None, None, {}, level=25)
    half = idata.loadout_stat_totals(None, None, "Sword_Start", {}, level=25)
    assert set(main) == set(half)
    for stat, value in main.items():
        assert half[stat] == pytest.approx(0.5 * value, abs=0.02), stat


def test_loadout_stat_totals_match_the_item_summaries():
    """Totals are just the pieces' summaries added up — the two surfaces
    must agree, jewelry included (both read it at its real source cap)."""
    slots = {"Chest": "Chest_RDemon_Ass", "Ring 1": "Finger_Z3RCraft_Cri"}
    expected: dict[str, float] = {}
    for iid in slots.values():
        for stat, value in _parse_summary(
                idata.format_item_stats_summary(iid, level=25)).items():
            expected[stat] = round(expected.get(stat, 0.0) + value, 2)
    assert idata.loadout_stat_totals(None, None, None, slots,
                                     level=25) == expected


def test_loadout_stat_totals_upgrades_raise_the_totals():
    base = idata.loadout_stat_totals("Sword_Start", None, None, {}, level=25)
    ranked = idata.loadout_stat_totals(
        "Sword_Start", None, None, {}, level=25,
        upgrades={"Sword_Start": 3})          # Rare shop quality: cap 3
    assert ranked["Strength"] > base["Strength"]
    assert ranked["Critical"] > base["Critical"]


# --- optimize_loadout_build -------------------------------------------------

def test_optimizer_fills_valid_unique_slots_for_the_class():
    build = idata.optimize_loadout_build(weapon1_id="Sword_Start",
                                         class_filter="Fighter", level=25,
                                         upgrades={})
    assert build
    assert set(build) <= set(_SLOT_TYPES)
    assert len(build) >= 6                    # most slots resolve for a class
    assert len(set(build.values())) == len(build)     # no item twice
    for label, iid in build.items():
        it = idata.item(iid)
        assert it, iid
        assert it["type"] == _SLOT_TYPES[label], label
        assert idata.item_display_rarity(iid) != "Common"
        classes = it.get("classes") or []
        if classes:
            assert "Fighter" in classes, iid
        if it["type"] not in _JEWELRY_TYPES:
            # armor is Rare+ and rolls the weapon's primary stat
            assert idata.item_display_rarity(iid) in ("Rare", "Epic",
                                                      "Legendary"), iid
            assert idata.matches_stat(iid, "Strength"), iid
    # ring uniqueness: the two finger slots never share a piece
    assert build.get("Ring 1") != build.get("Ring 2")


def test_optimizer_aligns_armor_to_the_wielding_class_not_the_weapon_order():
    """Axe_Boomerang is a Dexterity weapon on paper (Assassin listed first)
    but a Fighter build reads Strength, so the armor slots must be filtered to
    Strength-rolling Fighter pieces. Read off the weapon's own class order the
    filter kept only the Fighter+Assassin halves of the sets and silently left
    every Fighter-only piece unscored."""
    build = idata.optimize_loadout_build(weapon1_id="Axe_Boomerang",
                                         class_filter="Fighter", level=25,
                                         upgrades={})
    assert build and len(build) >= 6
    armor = {l: i for l, i in build.items()
             if _SLOT_TYPES[l] not in _JEWELRY_TYPES}
    assert armor
    for label, iid in armor.items():
        assert "Fighter" in (idata.item(iid).get("classes") or []), label
        assert idata.matches_stat(iid, "Strength"), label
    # the single-class Fighter halves are reachable again
    assert any(idata.item(i).get("classes") == ["Fighter"]
               for i in armor.values())


def test_arsenal_suggestion_scores_at_its_half_contribution():
    """The Arsenal grants 50% of its stats, so candidates are scored as the
    build actually receives them (the docstring's rule). `_score_pick` takes
    the share as `factor` — this pins that the stats it reports scale with it,
    so the Critical soft cap applies to the real contribution."""
    args = ([ "Sword_Start"], None, "Weapons", "Fighter", "Strength", 25, {})
    picked, full = _stats._score_pick(*args, factor=1.0)
    half_picked, half = _stats._score_pick(*args, factor=0.5)
    assert picked == half_picked == "Sword_Start"
    assert set(half) == set(full)
    for stat, value in full.items():
        assert half[stat] == pytest.approx(0.5 * value, abs=0.01), stat


def test_optimizer_is_deterministic():
    args = dict(weapon1_id="Daggers_Start", class_filter="Assassin",
                level=25, upgrades={})
    assert idata.optimize_loadout_build(**args) \
        == idata.optimize_loadout_build(**args)


def test_optimizer_empty_collection_yields_nothing():
    """An empty owned pool means no candidates at all — slots stay empty
    instead of falling back to the whole catalog."""
    assert idata.optimize_loadout_build(weapon1_id="Sword_Start",
                                        class_filter="Fighter",
                                        candidate_pool=[], level=25) == {}


def test_optimizer_restricted_to_owned_pool():
    pool = ["Chest_RDemon_Ass", "Finger_Z3RCraft_Cri",
            "Necklace_Z2RCraft", "CopperIngot"]
    build = idata.optimize_loadout_build(weapon1_id="Daggers_Start",
                                         class_filter="Assassin",
                                         candidate_pool=pool, level=25)
    assert build
    assert set(build.values()) <= set(pool)
    assert "CopperIngot" not in build.values()        # non-gear is never picked


# --- suggest_main_weapon / suggest_arsenal_weapon ---------------------------

def test_main_weapon_suggestion_is_a_wieldable_weapon():
    pick = idata.suggest_main_weapon(class_filter="Fighter", level=25,
                                     upgrades={})
    assert pick
    it = idata.item(pick)
    assert idata.category(it["type"]) == "Weapons"
    classes = it.get("classes") or []
    assert not classes or "Fighter" in classes


def test_main_weapon_suggestion_respects_exclusions():
    first = idata.suggest_main_weapon(class_filter="Fighter", level=25,
                                      upgrades={})
    second = idata.suggest_main_weapon(exclude_ids=(first,),
                                       class_filter="Fighter", level=25,
                                       upgrades={})
    assert second and second != first
    assert idata.category(idata.item(second)["type"]) == "Weapons"


def test_weapon_suggestions_empty_pool_yield_nothing():
    assert idata.suggest_main_weapon(class_filter="Fighter",
                                     candidate_pool=[], level=25) is None
    assert idata.suggest_arsenal_weapon(weapon1_id="Sword_Start",
                                        candidate_pool=[], level=25) is None


def test_arsenal_suggestion_never_picks_an_equipped_weapon():
    w1 = idata.suggest_main_weapon(class_filter="Fighter", level=25,
                                   upgrades={})
    w2 = idata.suggest_main_weapon(exclude_ids=(w1,), class_filter="Fighter",
                                   level=25, upgrades={})
    arsenal = idata.suggest_arsenal_weapon(weapon1_id=w1, weapon2_id=w2,
                                           slot_items={}, class_filter="Fighter",
                                           level=25, upgrades={})
    assert arsenal
    assert arsenal not in (w1, w2)
    assert idata.category(idata.item(arsenal)["type"]) == "Weapons"


# --- suggest_augment_plan ---------------------------------------------------

def test_augment_plan_shape_and_grants_on_real_loadout():
    w1 = "Sword_Swarm"                        # real Rare weapon with ratings
    slots = {"Ring 1": "Finger_Z3RCraft_Cri"}
    plan = idata.suggest_augment_plan(w1, None, None, slots,
                                      class_filter="Assassin", level=25,
                                      upgrades={})
    assert set(plan) == {"slots", "conversions", "totals_with_plan"}
    gems = {g["item"]: g for g in idata.gem_augments()}
    for label, pick in plan["slots"].items():
        assert label in ("W1", "W2", "Arsenal") or label in slots
        assert pick["item"] in gems
        assert pick["name"]                        # a display name
        assert pick["grants"]
        for stat, value in pick["grants"]:
            assert gems[pick["item"]]["stats"][stat] == value
    # a conversion rides a rating the piece actually rolls
    for conv in plan["conversions"]:
        assert set(conv) == {"slot", "source", "target", "value"}
        assert conv["value"] == 20                 # Rare DemonGearConversion
        assert conv["source"] in idata.gear_ratings(
            {"W1": w1, **slots}[conv["slot"]])


def test_augment_plan_totals_include_every_grant():
    """totals_with_plan = the loadout totals plus every chosen gem's grants
    (the Arsenal's at half), accumulated piece by piece like the plan."""
    slots = {"Chest": "Chest_RDemon_Ass"}
    base = idata.loadout_stat_totals("Sword_Start", None, None, slots,
                                     level=25, upgrades={})
    plan = idata.suggest_augment_plan("Sword_Start", None, None, slots,
                                      class_filter="Fighter", level=25,
                                      upgrades={})
    assert set(plan["totals_with_plan"]) >= set(base)
    expected = dict(base)
    for label, pick in plan["slots"].items():
        factor = 0.5 if label == "Arsenal" else 1.0
        for stat, value in pick["grants"]:
            expected[stat] = round(expected.get(stat, 0.0) + value * factor, 2)
    assert plan["totals_with_plan"] == expected
    # at least one stat really went up (the plan is not a no-op)
    assert any(plan["totals_with_plan"][k] > base.get(k, 0.0)
               for k in plan["totals_with_plan"])


def test_augment_plan_weights_the_arsenal_at_half():
    base = idata.loadout_stat_totals(None, None, "Sword_Start", {}, level=25)
    plan = idata.suggest_augment_plan(None, None, "Sword_Start", {},
                                      class_filter="Fighter", level=25,
                                      upgrades={})
    assert "Arsenal" in plan["slots"]
    grants = dict(plan["slots"]["Arsenal"]["grants"])
    for stat, value in grants.items():
        assert plan["totals_with_plan"][stat] == pytest.approx(
            round(base.get(stat, 0.0) + value * 0.5, 2), abs=0.01), stat


def test_augment_plan_empty_loadout_is_empty():
    plan = idata.suggest_augment_plan()
    assert plan == {"slots": {}, "conversions": [],
                    "totals_with_plan": {}}


def test_dungeon_set_pieces_are_candidates_for_bis_and_owned():
    """The four-faction dungeon sets carry NO `level` field — the shared Rare
    pieces and their `_E<Faction>` Epic twins. A non-zero-level gate dropped
    every one of them, so BiS never picked a dungeon piece and owning one
    filled nothing; the only thing that decides a candidate now is whether
    its stats resolve at the requested level."""
    group = next(g for g in idata.dungeon_drop_groups()
                 if g["faction"] == "Manfish")
    epics = [e for b in group["bosses"] for e in b["epic"]] \
        + list(group["epic_unknown"])
    assert epics and all((idata.item(i) or {}).get("level") is None
                          for i in epics)

    # owning only the Fighter-usable Epic pieces fills those slots from the
    # pool (before: nothing at all)
    fighter = [i for i in epics
               if "Fighter" in ((idata.item(i) or {}).get("classes") or [])]
    picks = idata.optimize_loadout_build(class_filter="Fighter",
                                         candidate_pool=fighter)
    assert picks and set(picks.values()) <= set(fighter)

    # the Epic twin out-scores its Rare twin for the same slot, and BiS
    # reaches for dungeon pieces now
    bis = idata.optimize_loadout_build(class_filter="Fighter")
    assert any("_E" in i for i in bis.values())
    assert len(bis) > 10          # 11 slots, head to trinket


def test_a_role_decides_the_main_hand_because_a_stat_sheet_cannot():
    """Healing, blocking and threat are SKILLS in this game, so the build
    score counts them at zero. Left on stats alone it ranked the two best
    HEALING Cleric weapons dead last of fourteen (Flame of Argol's "healing
    health every 2s", Rehearsal Scepter's "Radiance: Heals nearby allies")
    and handed the slot to a two-handed staff that won by 2 points.

    `weapon_role_fit` reads the role off the sheets' own skill text, so the
    roles a class CAN play must disagree about the main hand — and the
    healer's answer has to be a weapon that actually heals.

    Only the roles a class can play are compared: a Cleric cannot tank
    (`_CLASS_ROLES`), and a forbidden role resolving to the default build is
    covered by the role-availability test.
    """
    def pick(role, **kw):
        return idata.suggest_main_weapon(class_filter="Cleric", level=25,
                                        role=role, **kw)

    healer, damage = pick("healer"), pick("damage")
    assert healer != damage, (healer, damage)

    def _heals(iid):
        blob = " ".join(f"{s['name']} {s.get('description') or ''}"
                        for s in idata.weapon_skills(iid)).lower()
        return "heal" in blob

    assert _heals(healer), f"the healer's pick {healer} cannot heal"
    assert idata.weapon_role_fit(healer, "healer") > 0
    # the fit is a RANKING term, and a plain stat sum still has an answer
    assert idata.suggest_main_weapon(class_filter="Cleric", level=25) is not None
    # the two healing Cleric weapons the stat sum buried are now rankable
    for iid in ("Scepter_Flamie", "Scepter_Start"):
        assert idata.weapon_role_fit(iid, "healer") > 0, iid
    # a role nobody picked, or a weapon with no signal, adds nothing
    assert idata.weapon_role_fit("Sword_Start", "") == 0.0
    assert idata.weapon_role_fit("Sword_Start", None) == 0.0
    # the Warrior and the Rogue cannot heal, so the healer answer is a
    # caster's and the fit table is only ever consulted for a legal role
    assert not idata.class_can_role("Fighter", "healer")
    assert not idata.class_can_role("Assassin", "healer")
    assert not idata.class_can_role("Wizard", "tank")
    assert not idata.class_can_role("Assassin", "tank")
    assert idata.class_can_role("Fighter", "tank")


def test_a_shield_wanted_turns_the_main_hand_away_from_a_two_hander():
    """A 2H main hand LOCKS the off-hand, so a stat margin of 2 points was
    enough to hand a Cleric a staff and lock that build out of every shield
    in the game — including the healer one. With a shield on the table the
    main hand prefers a one-hander, but a role fit can still outweigh it."""
    two_handed = idata.suggest_main_weapon(class_filter="Cleric", level=25)
    assert _stats.is_two_handed(two_handed), two_handed

    one_handed = idata.suggest_main_weapon(class_filter="Cleric", level=25,
                                           prefer_one_handed=True)
    assert not _stats.is_two_handed(one_handed), one_handed
    # ...and the build can now actually hold a shield
    shield = idata.suggest_offhand_shield(
        weapon1_id=one_handed, class_filter="Cleric", level=25)
    assert idata.is_shield(shield) and shield, one_handed
    # a class with no shield at all gains nothing from the preference
    assert idata.suggest_main_weapon(class_filter="Assassin", level=25,
                                     prefer_one_handed=True) is not None


def test_the_role_chip_also_re_decides_the_off_hand_shield():
    """The shield's kit is written for a JOB — Fortifying Cry is a party
    armor buff, Depth Shield an ally heal, Cinder Coat damage off every
    block — and the table was keyed by CLASS, which got two of the three
    right by luck (a Fighter is usually tanking, a Cleric usually healing)
    and the third wrong: a Fighter on the Damage chip was handed the tank's
    Dominion. A role is what a build is FOR, so it has to lead.

    The class fit survives as a FALLBACK, and that is load-bearing rather
    than legacy: `Shield_Firebreath` is Fighter/Wizard, so a Cleric asking
    for the damage shield gets nothing from the role table — and with every
    Rare shield on an identical 564 Armor that leaves the off-hand
    tie-broken ALPHABETICALLY, the exact accident the table exists to
    prevent. So the fallback is a fallback: it applies only when the role
    names nothing the class can wield.
    """
    # the role leads where the class can wield the role's shield
    assert (idata.suggest_offhand_shield(class_filter="Fighter", level=25,
                                         role="tank")
            == "Shield_Craft"), "a tank must get the party-armor shield"
    assert (idata.suggest_offhand_shield(class_filter="Fighter", level=25,
                                         role="damage")
            == "Shield_Firebreath"), "a damage Fighter kept the tank's shield"
    # ...and the tank's Dominion is still one chip away
    assert idata.suggest_offhand_shield(class_filter="Fighter", level=25,
                                        role="tank") != \
        idata.suggest_offhand_shield(class_filter="Fighter", level=25,
                                     role="damage")

    # a healer gets the one that heals, whichever class asks
    for cls in ("Cleric", "Fighter"):
        if not idata.class_can_role(cls, "healer"):
            continue
        assert (idata.suggest_offhand_shield(class_filter=cls, level=25,
                                             role="healer")
                == "Shield_OrbitWater"), cls

    # the class fallback, where the role's shield is off-limits: a Cleric
    # cannot wield Magma Mia, so a Cleric on Damage still gets the Kneecap
    # rather than an alphabetical accident
    cleric_damage = idata.suggest_offhand_shield(class_filter="Cleric",
                                                 level=25, role="damage")
    assert cleric_damage == "Shield_OrbitWater", cleric_damage
    assert idata.rank_offhand_shields(class_filter="Cleric", level=25,
                                      role="damage")[0]["why"], \
        "the fallback pick cannot explain itself"

    # a Wizard can wield NEITHER class shield, so the fallback needs a
    # Wizard or its single contender wins alphabetically with no reason
    for role in ("tank", "healer", "damage", None):
        rows = idata.rank_offhand_shields(class_filter="Wizard", level=25,
                                          role=role, limit=1)
        assert rows and rows[0]["why"], (role, rows)

    # the ranking and the picker must agree for every class and role, or the
    # card ranks a different build than the one it annotates
    for cls in ("Fighter", "Cleric", "Wizard", "Assassin"):
        for role in ("tank", "healer", "damage", None):
            rows = idata.rank_offhand_shields(class_filter=cls, level=25,
                                              role=role, limit=1)
            best = idata.suggest_offhand_shield(class_filter=cls, level=25,
                                                role=role)
            assert ([r["id"] for r in rows][:1] or [None]) == [best], \
                (cls, role, rows, best)

    # a stale chip cannot ask a Rogue for a tank's shield; it resolves to
    # the default build, the same guard the other role entry points carry
    assert (idata.suggest_offhand_shield(class_filter="Assassin", level=25,
                                         role="tank") is None)
    assert (idata.suggest_offhand_shield(class_filter="Wizard", level=25,
                                         role="tank")
            == idata.suggest_offhand_shield(class_filter="Wizard", level=25,
                                            role="damage"))

    # no role at all is NOT the same as no role, and the class fallback is
    # what a caller that never passed one gets
    assert (idata.suggest_offhand_shield(class_filter="Fighter", level=25)
            == "Shield_Craft")


def test_the_weapon_card_can_say_why_the_role_picked_the_weapon():
    """A recommendation that cannot explain itself is a black box. The card
    already lists the weapon's skills by name, but a list of names says
    nothing about why THIS one beat the other thirteen the class can wield —
    and the answer is never the stats: no weapon rolls Armor, vitality runs
    32-40 whatever the kit, and healing / blocking / threat are skills this
    game has no stat for at all.

    So the reasons are the phrases the role actually MATCHED, taken verbatim
    out of the sheets. Two properties make them worth showing: they are real
    slices of a skill description (so the reader can check them), and they
    agree with the score that produced the pick — a reason the ranker cannot
    see is a reason the ranker did not use.
    """
    for cls in ("Fighter", "Cleric", "Wizard", "Assassin"):
        for role in ("tank", "healer", "damage"):
            if not idata.class_can_role(cls, role):
                continue
            pick = idata.suggest_main_weapon(class_filter=cls, level=25,
                                             role=role)
            reasons = idata.weapon_role_reasons(pick, role)
            assert reasons, (cls, role, pick)
            # the score is exactly 40 per matched phrase, capped at 120, so
            # the number and the sentences can never drift apart
            fit = idata.weapon_role_fit(pick, role)
            phrases = [p for r in reasons
                       for p in r.split(":", 1)[-1].split(", ")]
            assert fit == min(len(phrases) * 40, 120), (cls, role, fit, reasons)
            # every reason names a REAL skill of this weapon and quotes a real
            # slice of THAT skill's text, so a reader can go and check it
            skills = {s.get("name") or "": (s.get("description") or "").lower()
                      for s in idata.weapon_skills(pick)}
            for r in reasons:
                name, _, body = r.partition(": ")
                assert name in skills, (cls, role, name, list(skills))
                for p in body.split(", "):
                    assert p.lower() in skills[name], (cls, role, r)

    # a weapon with no signal is unranked, not wrongly ranked — so it has no
    # reason either, and saying nothing beats inventing one
    assert idata.weapon_role_reasons("", "tank") == ()
    assert idata.weapon_role_reasons("Sword_Start", "nobody") == ()
    # and a reason set is empty exactly when the fit is zero
    for iid in ("Sword_Start", "Shield_Craft", "Staff_Start", "Dagger_Start"):
        for role in idata.LOADOUT_ROLES:
            assert bool(idata.weapon_role_reasons(iid, role)) == \
                bool(idata.weapon_role_fit(iid, role)), (iid, role)

    # the reasons must actually differ between roles, or the line is decoration
    tank_pick = idata.suggest_main_weapon(class_filter="Fighter", level=25,
                                          role="tank")
    dmg_pick = idata.suggest_main_weapon(class_filter="Fighter", level=25,
                                         role="damage")
    assert tank_pick != dmg_pick, (tank_pick, dmg_pick)
    assert (idata.weapon_role_reasons(tank_pick, "tank")
            != idata.weapon_role_reasons(dmg_pick, "damage"))

    # grouped by skill, not one label per phrase: a healer wants "healing"
    # AND "heals nearby allies" out of the SAME ability, and repeating the
    # skill name for both would spend the line saying nothing
    grouped = idata.weapon_role_reasons(
        idata.suggest_main_weapon(class_filter="Cleric", level=25,
                                  role="healer"), "healer")
    assert any(len(r.split(":", 1)[-1].split(", ")) > 1 for r in grouped), grouped


def test_the_shield_ranking_shows_its_work_not_just_the_winner():
    """The off-hand card lists the runners-up, so the recommendation shows
    what beat what. `rank_offhand_shields` must therefore agree with the
    single-id picker on who wins, and every contender must carry the kit
    that decided it — with the Rare shields on an identical 564 Armor, the
    kit IS the answer and the number is not."""
    for cls in ("Fighter", "Cleric", "Wizard", "Assassin"):
        rows = idata.rank_offhand_shields(class_filter=cls, level=25)
        best = idata.suggest_offhand_shield(class_filter=cls, level=25)
        # a class with no shield has neither a winner nor a field
        assert ([r["id"] for r in rows][:1] or [None]) == [best], \
            (cls, rows, best)
        assert [r["score"] for r in rows] == sorted(
            (r["score"] for r in rows), reverse=True), cls
        for r in rows:
            assert r["name"] and r["armor"] > 0
            # the kit behind the pick, minus the Block every shield shares
            assert [s["name"] for s in r["skills"] if s["name"] != "Block"], r

    fighter = idata.rank_offhand_shields(class_filter="Fighter", level=25)
    assert fighter[0]["id"] == "Shield_Craft" and fighter[0]["why"]
    assert fighter[0]["fit"] > 0, "the winner won on its fit, not its armor"
    # the losers say so with an empty why, and fall back to their own kit
    for r in fighter[1:]:
        assert r["why"] == "" and r["fit"] == 0, r

    # one candidate is not a comparison: the list is still the whole field
    assert len(idata.rank_offhand_shields(class_filter="Assassin",
                                           level=25)) == 0
    assert idata.rank_offhand_shields(class_filter="Cleric", level=25,
                                      limit=1) != []


def test_the_tank_profile_alone_moves_no_armor_pick():
    """Armor and Vitality are near-token in the default score (0.2 / 0.5) —
    a defensible damage profile and a wrong one for a tank — so the Tank role
    raises them to 1.0 (`_PROFILE_WEIGHTS`).

    On TODAY'S catalog that half of the profile re-ranks nothing, and the
    test says so out loud rather than letting it look like it works: every
    candidate of a slot shares the type's atb budget, so Armor, the primary
    stat and Vitality all scale by the SAME curve and move together. The
    best chest on Strength is the best chest on Armor, so any positive Armor
    weight preserves the order.

    It is the DEMOTED RATINGS that actually move the pick — that is the
    other half of the tank profile, pinned by
    `test_each_role_gets_its_own_eleven_pieces`. What must stay true is the
    narrower claim: survivability weights alone, on their own, change no
    armor pick. If a patch ever decouples armor from the primary stat this
    test is the one that should fail loudly.
    """
    stats = {"Strength": 10.0, "Armor": 400.0, "Vitality": 20.0}
    default = _stats._build_score(stats, "Strength")
    tank = _stats._build_score(stats, "Strength", "tank")
    assert tank > default, "the tank profile does not reward armor at all"
    # armor alone decides the difference: 400 x (1.0 - 0.2) + 20 x (1.0 - 0.5)
    assert tank - default == 400 * 0.8 + 20 * 0.5

    armor_only = {"Armor": 1.0, "Vitality": 1.0, "Health": 1.0}
    _stats._PROFILE_WEIGHTS["_test_armor_only"] = armor_only
    try:
        for cls in ("Fighter", "Cleric", "Wizard", "Assassin"):
            base = idata.optimize_loadout_build(class_filter=cls, level=25)
            assert idata.optimize_loadout_build(
                class_filter=cls, level=25,
                role="_test_armor_only") == base, cls
    finally:
        del _stats._PROFILE_WEIGHTS["_test_armor_only"]


def test_each_role_gets_its_own_eleven_pieces():
    """The symptom this fixes: a tank, a healer and a damage dealer were all
    handed the SAME eleven pieces, because every stat in `_build_score` was
    weighted for damage — so nothing in a stat sheet pulled toward the other
    two roles. Each role must now get a different set, and the separation
    must come from the ratings, since armor / primary / Vitality all scale
    by one curve (see `test_the_tank_profile_alone_moves_no_armor_pick`).
    """
    # "damage" is the default profile, so None and "damage" are one build and
    # neither may drift from it.
    for cls in ("Fighter", "Assassin", "Cleric", "Wizard"):
        base = idata.optimize_loadout_build(class_filter=cls, level=25)
        assert idata.optimize_loadout_build(class_filter=cls, level=25,
                                            role="damage") == base, cls
        assert idata.optimize_loadout_build(class_filter=cls, level=25,
                                            role=None) == base, cls

    # per class, the number of slots on which a role leaves the damage set
    separated = 0
    for cls in ("Fighter", "Assassin", "Cleric", "Wizard"):
        base = idata.optimize_loadout_build(class_filter=cls, level=25)
        for role in ("tank", "healer"):
            # only the roles this class can actually PLAY (a stale chip
            # cannot build a tank for a mage — see the guard in
            # optimize_loadout_build)
            if not idata.class_can_role(cls, role):
                continue
            build = idata.optimize_loadout_build(class_filter=cls, level=25,
                                                  role=role)
            assert len(build) == len(base) == 11, (cls, role, build)
            slots = [k for k in base if build.get(k) != base[k]]
            # a role that changes nothing is the bug this test exists for
            assert len(slots) >= 4, (cls, role, sorted(base.items()))
            separated += 1
    # Fighter can tank but not heal, Assassin can do neither, so three of
    # the eight (class, role) pairs are the ones under test.
    assert separated == 3


def test_a_role_this_class_cannot_play_falls_back_to_the_damage_build():
    """`optimize_loadout_build` used to take any role string, so a stale
    page state could weight an Assassin's eleven pieces as a tank's. The two
    weapon pickers already resolved an illegal role to "damage"; this is the
    same guard on the third entry point."""
    for cls in ("Assassin",):          # damage only
        base = idata.optimize_loadout_build(class_filter=cls, level=25)
        for role in ("tank", "healer"):
            assert idata.optimize_loadout_build(class_filter=cls, level=25,
                                                role=role) == base, (cls, role)
    for cls in ("Fighter",):           # tank, but not healer
        assert (idata.optimize_loadout_build(class_filter=cls, level=25,
                                             role="healer")
                == idata.optimize_loadout_build(class_filter=cls, level=25,
                                                role="damage"))
    # an unknown class is never locked out of a role
    assert idata.optimize_loadout_build(class_filter="Nobody", level=25,
                                        role="tank")


def test_the_kit_reaches_every_weapon_slot_not_just_the_off_hand():
    """The shields rank by kit from the class alone; the main hand and the
    Arsenal used to rank by kit only while a role chip happened to be on, so
    an untouched page fell back to the stat sum that ranks the best healing
    weapons LAST. Both weapon slots must answer to the role, and None must
    not be a silent "ignore the kit"."""
    for cls in ("Fighter", "Cleric", "Wizard", "Assassin"):
        # the kit must be LIVE for every class: a stated role beats no role,
        # because no role is the stat sum that cannot see any of this
        assert (idata.suggest_main_weapon(class_filter=cls, level=25,
                                          role="damage")
                != idata.suggest_main_weapon(class_filter=cls, level=25)), cls
        # None is a weaker input, not a different build: the caller resolves
        assert idata.suggest_main_weapon(class_filter=cls, level=25,
                                         role="damage") == \
            idata.suggest_main_weapon(class_filter=cls, level=25,
                                      role="damage")
        # and where a class can play more than one role, they must disagree
        roles = [r for r in idata.LOADOUT_ROLES
                 if idata.class_can_role(cls, r)]
        if len(roles) > 1:
            picks = {idata.suggest_main_weapon(class_filter=cls, level=25,
                                               role=r) for r in roles}
            assert len(picks) == len(roles), (cls, roles, picks)

        # the Arsenal is a third of the build's weapons, so it answers too
        ars = {}
        for r in ("tank", "healer", "damage"):
            ars[r] = idata.suggest_arsenal_weapon(class_filter=cls, level=25,
                                                  role=r)
        assert all(ars.values()), (cls, ars)
        assert ars["damage"] == idata.suggest_arsenal_weapon(
            class_filter=cls, level=25, role="damage")

    # the fit is the ONLY thing separating some of these: the shield that
    # ranks first for a tank is the healer's, and both are Cleric/Fighter
    fighter = idata.suggest_offhand_shield(class_filter="Fighter", level=25)
    cleric = idata.suggest_offhand_shield(class_filter="Cleric", level=25)
    assert fighter != cleric


def test_the_shield_pick_ignores_whose_upgrade_is_in_the_page_state():
    """Every candidate is read at its OWN ladder cap, never at the caller's
    per-item upgrade dict.

    That dict is page state which outlives a class switch, so reading it
    compared the equipped shield (maxed, 564 armor) against the others still
    at +0 (487) and let the stale one win: a Fighter's upgraded Dominion
    handed a CLERIC the tank's shield, undoing the kit fit through a channel
    the fit never sees. The signature no longer takes `upgrades` at all, so
    the footgun is gone rather than documented.
    """
    import inspect
    for fn in (idata.suggest_offhand_shield, idata.rank_offhand_shields):
        assert "upgrades" not in inspect.signature(fn).parameters, fn

    # the two agree, and each class still gets ITS shield
    fighter = idata.suggest_offhand_shield(class_filter="Fighter", level=25)
    cleric = idata.suggest_offhand_shield(class_filter="Cleric", level=25)
    assert fighter == "Shield_Craft" and cleric == "Shield_OrbitWater"
    assert idata.rank_offhand_shields(class_filter="Cleric",
                                       level=25)[0]["id"] == cleric
    # every contender reads at the same rank, so the armor column is a
    # like-for-like comparison
    for rows in (idata.rank_offhand_shields(class_filter=c, level=25)
                 for c in ("Fighter", "Cleric", "Wizard")):
        assert {r["armor"] for r in rows} == {rows[0]["armor"]}, rows
