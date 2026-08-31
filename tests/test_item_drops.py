"""Item database layer (headless, no game): item_drops.json resolution +
max-level gear scaling."""
import pytest

from farever_companion import paths
from farever_companion.data import items as idata


def _data_present() -> bool:
    try:
        return paths.item_drops_path().exists()
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _data_present(),
    reason="requires bundled data (assets/data/item_drops.json)")


def test_items_loaded():
    items = idata.items()
    # the scan bundles the full catalog: droppable items + crafted gear,
    # recipes, ingots, mounts/gliders, consumables (~1036 of the game's
    # ~1040 item rows; only Experience/CraftPoint/ShopCurrency/Hide_Gear are
    # skipped as non-inventory tokens).
    assert len(items) >= 1000
    assert all(it["id"] for it in items)
    assert items == sorted(items, key=lambda r: (r["name"] or r["id"]).lower())


def test_item_id_by_name_resolves_placed_food():
    """Placed food resolves its display name off a Skill and carries no id in
    memory; the catalog reverse-lookup maps the name back to the item id."""
    assert idata.item_id_by_name("Plainswalker Feast") == "Feast"
    assert idata.item_id_by_name("Minor Alchemist Cauldron") == \
        "SmallAlchemistCauldron"
    # case-insensitive and tolerant of stray whitespace
    assert idata.item_id_by_name("  plainswalker feast ") == "Feast"
    # unknown names degrade to None, never raise
    assert idata.item_id_by_name("No Such Dish") is None
    assert idata.item_id_by_name(None) is None


def test_resolve_food_info():
    """resolve_food_info maps display names, item IDs, skill IDs, and missing values."""
    assert idata.resolve_food_info("Plainswalker Feast") == ("Plainswalker Feast", "Feast")
    assert idata.resolve_food_info("Feast") == ("Plainswalker Feast", "Feast")
    assert idata.resolve_food_info("PlainswalkerFeast") == ("Plainswalker Feast", "Feast")
    assert idata.resolve_food_info("PrepareWorldConsumable") == ("Plainswalker Feast", "Feast")
    assert idata.resolve_food_info("Minor Alchemist Cauldron") == ("Minor Alchemist Cauldron", "SmallAlchemistCauldron")
    assert idata.resolve_food_info("SmallAlchemistCauldron") == ("Minor Alchemist Cauldron", "SmallAlchemistCauldron")
    assert idata.resolve_food_info("Cook_1") == ("Wild Boar Stew", "Cook_1")
    assert idata.resolve_food_info("Wild Boar Stew") == ("Wild Boar Stew", "Cook_1")
    assert idata.resolve_food_info(None) == ("Plainswalker Feast", "Feast")
    assert idata.resolve_food_info("Food") == ("Plainswalker Feast", "Feast")


def test_catalog_includes_crafted_and_non_droppable():
    # non-droppable gear / materials have no drop rows but still resolve;
    # the zone sets (Reinforced Hauberk of the Exile) are WorldLoot-flagged
    # mob drops — generated gear, NOT crafted (no FIXED level); the scan
    # attributes them to their region's WorldLoot sources (see
    # test_zone_gear_drops_real_chances)
    chest = idata.item("Chest_Z1U1_Fig")
    assert chest and chest["name"] == "Reinforced Hauberk of the Exile"
    assert chest["type"] == "Chest"
    assert idata.resolve_drops("Chest_Z1U1_Fig")
    assert idata.item_fixed_level(chest) is None
    assert idata.item("CopperIngot")["name"] == "Copper Ingot"
    # currencies are items (Gold already had drops); only true UI tokens are
    # excluded
    assert idata.item("CraftPoint")["name"] == "Craft point"
    assert idata.item("ShopCurrency") is not None
    assert idata.item("Experience") is None
    assert idata.item("Hide_Gear") is None


def test_search_matches_name_and_id():
    by_name = idata.search("Worldsplitter")
    assert any(it["id"] == "GA_Demon" for it in by_name)
    by_id = idata.search("GA_Demon")
    assert any(it["id"] == "GA_Demon" for it in by_id)
    by_type = idata.search("", item_type="Sword")
    assert all(it["type"] == "Sword" for it in by_type)


def test_search_finds_weapon_by_skill_name():
    # typing a skill name finds the weapon that grants it — the hay is
    # name/id/type, so the skill match is the reason the weapon appears
    assert any(it["id"] == "Axe_Boomerang"
               for it in idata.search("Bonethrow"))
    assert any(it["id"] == "Axe_Boomerang"
               for it in idata.search("Tear"))
    # text from a skill's resolved description matches too
    assert any(it["id"] == "Axe_Boomerang"
               for it in idata.search("bleed"))
    # non-weapons never match skills
    assert not idata.matches_skill("CopperIngot", "bonethrow")
    assert not idata.matches_skill("Axe_Boomerang", "")


def test_search_matches_class_names():
    # the game's class names are in the search hay: 'warrior' finds Fighter
    # aptitude gear, 'rogue' Assassin gear, etc. — and the raw aptitude
    # names work too ('fighter' finds the same gear)
    assert idata.matches_class("Sword_Start", "warrior")
    assert idata.matches_class("Sword_Start", "fighter")   # raw aptitude
    assert not idata.matches_class("Sword_Start", "rogue")
    assert not idata.matches_class("CopperIngot", "warrior")  # no classes
    assert not idata.matches_class("Sword_Start", "")
    # the full search finds the weapon by class name alone
    assert any(it["id"] == "Sword_Start"
               for it in idata.search("warrior"))
    assert any(it["id"] == "Sword_Start"
               for it in idata.search("fighter"))


def test_matched_skill_labels():
    # the tag shows the skill NAME even when the query matched the skill's
    # id or description
    assert "Bonethrow" in idata.matched_skill_labels(
        "Axe_Boomerang", "bone")
    assert idata.matched_skill_labels(
        "Axe_Boomerang", "critical chance") == ("Bloodrage Aura",)
    assert idata.matches_skill("Axe_Boomerang", "Axe_Boomerang_Skill1")
    assert idata.matched_skill_labels("Axe_Boomerang", "") == ()
    assert idata.matched_skill_labels("CopperIngot", "bone") == ()


def test_resolve_drops_dedupes_and_sorts():
    # GA_Demon: Mira the vendor sells it at 3 hubs (p=1.0) + the rift boss
    # Nightking Maat Demon (p=0.01). The rift source must come first.
    rows = idata.resolve_drops("GA_Demon")
    assert rows, "GA_Demon should have drop sources"
    assert rows[0]["rift"], "rift boss source should sort first"
    assert any(r["source"] == "Nightking Maat Demon" for r in rows)
    # same source + loc merge into one row (prob sums)
    assert len({(r["source"], r["loc"]) for r in rows}) == len(rows)


def test_resolve_drops_carries_amounts():
    # DemonicSoul rows record drop amounts (e.g. 1-5, 100, 300, 2-3).
    rows = idata.resolve_drops("DemonicSoul")
    assert rows
    amounts = {r["amount"] for r in rows if r["amount"]}
    assert "1-5" in amounts and "100" in amounts and "300" in amounts
    # amount survives the source+loc merge (prob sums to 1.0 for 100% rows)
    full = [r for r in rows if r["prob"] >= 0.999]
    assert full and all(r["amount"] for r in full)


def test_weapon_skills_stamped_and_resolved():
    # The compiler stamps each weapon's skills list (from item.json) onto
    # its drops row, and weapon_skills resolves each entry into {id, name,
    # type, description} in the game's authored order.
    it = idata.item("Axe_Boomerang")
    assert it["skills"] == [
        "Axe_Base_Attack", "Axe_Base_Attack2", "Axe_Base_Attack3",
        "Axe_Boomerang_Combo", "Axe_Boomerang_Skill1",
        "Axe_Boomerang_Skill_Passive",
    ]
    ws = idata.weapon_skills("Axe_Boomerang")
    assert [s["id"] for s in ws] == it["skills"]
    assert [s["type"] for s in ws] == [
        "Base Attack", "Base Attack", "Base Attack", "Combo",
        "Active", "Passive",
    ]
    # names come from the game data (Tear, Bonethrow, Bloodrage Aura)
    assert ws[3]["name"] == "Tear"
    assert ws[4]["name"] == "Bonethrow"
    assert ws[5]["name"] == "Bloodrage Aura"
    # descriptions are fully resolved — no leftover template tokens
    d = ws[5]["description"]
    assert d == ("Increases the Critical Chance of all allies within 40m "
                 "by 3%.")
    assert all("::" not in (s["description"] or "")
               and "[" not in (s["description"] or "")
               for s in ws)


def test_weapon_skills_empty_for_non_weapons():
    assert idata.weapon_skills("CopperIngot") == []
    assert idata.weapon_skills("NoSuchItem") == []
    # base attacks 2/3 have no description row in the sheet — the entry
    # stays present (name + type), with description None, never a guess
    ws = idata.weapon_skills("Axe_Boomerang")
    assert ws[1]["description"] is None
    assert ws[2]["description"] is None


def test_every_weapon_skills_resolve_cleanly():
    """Property: every stamped weapon skill list resolves without a single
    leftover '::' or '[' in any description."""
    n_weapons = 0
    for it in idata.items():
        if not it.get("skills"):
            continue
        n_weapons += 1
        ws = idata.weapon_skills(it["id"])
        assert [s["id"] for s in ws] == it["skills"]
        for s in ws:
            if s["description"]:
                assert "::" not in s["description"], (it["id"], s["id"])
                assert "[" not in s["description"], (it["id"], s["id"])
    assert n_weapons >= 50          # 72 weapons in the current data


def test_resolve_drops_carries_vendor_costs():
    # GA_Demon is also sold by rift vendors for 10 Nightblood each.
    rows = idata.resolve_drops("GA_Demon")
    vendors = [r for r in rows if r["kind"] == "npc"]
    assert vendors, "GA_Demon should have vendor sources"
    for v in vendors:
        assert v["cost"] and any(c["kind"] == "Nightblood" for c in v["cost"])
        assert v["prob"] >= 0.999         # vendor rows are guaranteed offers
    # every row knows its source kind
    assert {r["kind"] for r in rows} <= {"unit", "chest", "npc", "gatherable"}


def test_resolve_drops_carries_rolls_and_table():
    # Linen Cloth's world-mob row rolls its table 2x (r=2).
    rows = idata.resolve_drops("Cloth_Z1")
    rolled = [r for r in rows if r["rolls"] > 1]
    assert rolled, "some rows should roll their table multiple times"
    assert all(r["table"] for r in rows)
    assert rolled[0]["amount"]          # rolls ride on rows that also carry amounts


def test_human_loc_strips_internal_ids():
    # zone ids, coordinates and POI ids come out; human names stay
    assert idata.human_loc(
        "Z1_Primevalley_Hub (Navelin) | (-342.7,1415.0)") == "Navelin"
    assert idata.human_loc("Z2_Azuram_Hub (Tyrna) | (1309.3,73.3)") == "Tyrna"
    assert idata.human_loc(
        "Z2_Azuram_Bridge (East Majoram Bridge) | entrances: "
        "POI_Rift_Entrance_1@Z2_Krisomal_North, POI_Rift_Entrance_3@"
        "Z1_Enripit_Falls") == "East Majoram Bridge"
    # already-human strings pass through untouched
    assert idata.human_loc("Demon crates (soulstone events)") == \
        "Demon crates (soulstone events)"
    assert idata.human_loc("Bee Hive crates") == "Bee Hive crates"
    assert idata.human_loc("Rift (arena entrance)") == "Rift (arena entrance)"
    # area + zone both kept when the string carries both
    assert idata.human_loc(
        "Abandoned Mines | Z2_Azuram_Mound_Cave (Aurock Mound)") == \
        "Abandoned Mines · Aurock Mound"
    # nothing human -> None (bare coordinates, ids-only, unknown)
    assert idata.human_loc("(-1117.0,880.0)") is None
    assert idata.human_loc("W1_Siagarta | (-46.5,1080.0)") is None
    assert idata.human_loc("unknown location") is None
    assert idata.human_loc("") is None
    assert idata.human_loc(None) is None


def test_merge_drops_merges_per_source():
    # Mittens of Lady Zaster: the rift vendor Mira sells at 3 hubs — one row,
    # human loc names collected, chance NOT summed (it is per-location).
    it = idata.search("Mittens of Lady")[0]
    rows = idata.merge_drops(it["id"])
    miras = [r for r in rows if r["source"] == "Mira, Demon Huntress"]
    assert len(miras) == 1
    m = miras[0]
    assert m["kind"] == "npc"
    assert set(m["locs"]) == {"Navelin", "Tyrna", "Lower Ramburg"}
    assert m["n_locs"] == 3
    assert m["prob"] == 1.0
    assert m["cost"] and any(c["kind"] == "Nightblood" for c in m["cost"])
    # Worldsplitter: the rift boss sorts first, then the vendor
    ga = idata.merge_drops("GA_Demon")
    assert ga[0]["source"] == "Nightking Maat Demon" and ga[0]["rift"]
    assert [r["source"] for r in ga].count("Mira, Demon Huntress") == 1
    # one row per source everywhere
    assert len({r["source"] for r in rows}) == len(rows)


def test_merge_drops_collapses_world_mob_spawns():
    import re
    raw = idata.resolve_drops("Cloth_Z1")
    merged = idata.merge_drops("Cloth_Z1")
    assert len(merged) <= len(raw)
    # no internal ids / coordinates left in any location label
    flat = " ".join(" ".join(r["locs"]) for r in merged)
    assert not re.search(r"\b[ZW]\d_", flat)
    assert "|" not in flat
    # same mob across zones is one row (prob stays the per-kill chance)
    whale = [r for r in merged if r["source"] == "Slick Nepsid Whale"]
    assert len(whale) == 1
    assert whale[0]["prob"] == 0.0875


def test_dungeon_set_drops_real_chances():
    """The per-faction dungeon gear sets (Shoulders_RManfish_FigAss, ...) have
    no loot-table rows in the scan — the game hands them out at RUNTIME via
    the 'WorldLoot' token (faction crates -> WorldCrate at 0.35, faction mobs
    at 0.01/0.05) and 'UpgradeRare' (upgrade activities at 1.0). The Drops
    From list derives REAL per-source chances: the source's WorldLoot chance
    x the piece's share of its faction pool."""
    rows = idata.resolve_drops("Shoulders_RManfish_FigAss")
    by_src = {r["source"]: r for r in rows}
    # Manfish pool = 28 (the R-set + its signature weapons) — each crate
    # rolls WorldLoot at 0.35, so the piece is 0.35/28 per crate
    for crate in ("Manfish Crates (x10)", "Kobold Crates (x10)",
                  "Bee Hive Crates (x20)", "Crimson Crates (x46)"):
        assert by_src[crate]["kind"] == "chest", crate
        assert by_src[crate]["prob"] == pytest.approx(0.35 / 28), crate
    # the faction mobs that roll WorldLoot (Bee mobs don't) — grouped per
    # faction, chance = the per-mob 0.06 roll x share
    for mobs in ("Manfish mobs", "Kobold mobs", "Crimson mobs"):
        assert by_src[mobs]["kind"] == "unit", mobs
        assert by_src[mobs]["prob"] == pytest.approx(0.06 / 28), mobs
    # UpgradeRare (pool 157) from the upgrade activities / dungeon crates
    assert by_src["Upgrade Items"]["prob"] == pytest.approx(1.0 / 157)
    # World/Demon crates roll their own pools — never listed for the sets
    assert "World Crates (x158)" not in by_src
    assert "Demon Crates (x2)" not in by_src
    # merged rows collapse each faction's mob zones into one row per source
    merged = idata.merge_drops("Shoulders_RManfish_FigAss")
    by_src = {r["source"]: r for r in merged}
    assert len({r["source"] for r in merged}) == len(merged)
    assert by_src["Manfish mobs"]["locs"]
    assert by_src["Manfish Crates (x10)"]["prob"] == pytest.approx(0.35 / 28)


def test_dungeon_set_pools_by_faction():
    """Every BASE set piece sits in its own faction pool (Kobold 28, Bee
    30, Crimson 29) — the dump records real crate/mob chances for all of
    them. The faction='Craft' pieces all carry a producing recipe now, so
    they're crafted gear and drop nothing (the 16-piece craft pool
    survives only in the no-scan derivation fallback, which no current
    item hits)."""
    pools = {
        "Shoulders_RManfish_FigAss": 28, "Trinket_Kobold": 28,
        "Waist_RBee_Wiz": 30, "Chest_RCrimson_FigCle": 29,
    }
    for iid, pool in pools.items():
        rows = idata.resolve_drops(iid)
        by_src = {r["source"]: r for r in rows}
        crate = by_src["Manfish Crates (x10)"]
        assert crate["prob"] == pytest.approx(0.35 / pool), iid
        assert by_src["Upgrade Items"]["prob"] == pytest.approx(1.0 / 157)
        # every piece has real chances — no 'derived' dash rows
        assert not any(r.get("derived") for r in rows), iid


def test_zone_gear_drops_real_chances():
    """The WorldLoot zone gear (the Z1U1/Z1U2/Z2U1/Z2U2 Uncommon sets and
    the Z1/Z2 Finger/Necklace jewelry) is generated at runtime from the
    WorldLoot token: the WorldCrate rolls it with `flags: 2` (0.35) and the
    region's faction mobs roll their own tables (Manfish/Kobold/Crimson at
    0.01 solo / 0.05 party, item level derived from the mob's level). Each
    piece is one of N zone pieces in its region's pool (Z1 = 126, Z2 = 166
    — the WorldLoot-flagged gear + jewelry of that region), so every row
    carries the source's real chance x the piece's 1/pool share, with the
    region's spawn zones as the location."""
    # Z1U1 (Skover Island): the scan emits the clean four-source shape
    # natively — the joined mob groups row, Unique Foes, Zone activities,
    # plus the WorldCrate
    rows = idata.resolve_drops("Chest_Z1U1_Fig")
    by_src = {r["source"]: r for r in rows}
    assert len(rows) == 4
    mobs = by_src["Crimson mobs · Kobold mobs · Manfish mobs"]
    assert mobs["kind"] == "unit"
    assert mobs["prob"] == pytest.approx(0.06 / 126)
    assert mobs["quiet"] is True
    assert by_src["World Crates (x158)"]["kind"] == "chest"
    assert by_src["World Crates (x158)"]["prob"] == pytest.approx(0.35 / 126)
    # the affinity rows are chance-less (the per-activity pool is untraced)
    aff = [r for r in rows if r["kind"] == "worldloot_affinity"]
    assert [r["source"] for r in aff] == ["Unique Foes", "Zone activities"]
    assert all(r["prob"] == 0.0 for r in aff)
    # Z2U2 (Valley of Eternal Autumn): pool 166
    rows = idata.resolve_drops("Chest_Z2U2_Fig")
    by_src = {r["source"]: r for r in rows}
    assert len(rows) == 4
    assert by_src["Crimson mobs · Kobold mobs · Manfish mobs"]["prob"] \
        == pytest.approx(0.06 / 166)
    assert by_src["World Crates (x158)"]["prob"] == pytest.approx(0.35 / 166)
    # the zone jewelry joins its region's pool (both tiers of each region
    # share the same WorldLoot sources — the rolled level picks the piece)
    for iid in ("Finger_Z1_Vit", "Necklace_Z2_Cri", "Chest_Z1U2_Fig",
                "Chest_Z2U1_Fig"):
        assert idata.resolve_drops(iid), iid


def test_scan_records_zone_rows_on_worldloot_gear():
    """The RAW data carries the zone gear's clean four-source shape
    natively — the scan collapses the ten rows into the joined mob groups,
    Unique Foes, Zone activities and the WorldCrate (quiet, loc-less rows),
    so the compiled item_drops.json needs no runtime collapse. Crafted
    variants stay row-less and the faction sets keep their own rows."""
    from farever_companion.data import raw_item_drops
    raw = raw_item_drops.DATA
    srcs = raw["_sources"]
    items = raw["items"]
    for iid in ("Chest_Z1U1_Fig", "Chest_Z1U2_Fig", "Chest_Z2U1_Fig",
                "Chest_Z2U2_Fig", "Finger_Z1_Vit", "Necklace_Z2_Cri"):
        drops = items[iid]["drops"]
        assert len(drops) == 4, iid
        sid = {srcs[d["s"]]["id"] for d in drops}
        assert "WorldCrate" in sid, iid
        # the three collapsed rows: joined mob groups + Unique Foes + Zone
        # activities, each quiet (no tooltip) and loc-less
        assert {"WorldLootMobs", "WorldLootUniqueFoes", "WorldActivities"} \
            <= sid, iid
        for d in drops:
            if srcs[d["s"]]["id"] != "WorldCrate":
                assert d.get("q") == 1, (iid, d)
        kinds = {srcs[d["s"]]["kind"] for d in drops}
        assert "worldloot_affinity" in kinds, iid
    # the WorldLoot flag is the only zone signal — recipes carry it too but
    # are crafting drops, and crafted/_Craft pieces stay untouched
    assert items["Back_RBee_FigWiz_Craft"]["drops"] == []
    assert items["Shoulders_RManfish_FigAss"]["drops"]


def test_player_scale_chest_acquisition_note():
    """The Rift Demon set (and the Demon weapons / Corrupted Gifts) are
    opened from Mira's player-scale caches — the scan stamps the acquisition
    note on the pieces so Drops From can surface the real source (a chest
    that rolls at YOUR level) next to the vendor row that sells the cache."""
    note = idata.acquisition_note("Chest_RDemon_WizCle")
    assert note and "Chaotic Gear Cache" in note
    assert "player-scale chest" in note
    assert "(1-25)" in note
    # the note is the shortened form: it names the player-scale chest and
    # its level range — the redundant "rolls 1 piece at your level" count
    # phrasing is gone (the cache's own flavor text says "a Nightling
    # piece/weapon for your level" and in-game opening grants a single piece)
    assert "rolls" not in note and "your level" not in note
    assert "2 piece" not in note and "piece(s)" not in note
    assert idata.acquisition_note("GA_Demon") and "Chaotic Weapon Cache" \
        in idata.acquisition_note("GA_Demon")
    assert "rolls" not in idata.acquisition_note("GA_Demon")
    # the non-leveled gift cache keeps a plain rolls note (proper plural,
    # no "1 piece(s)"); ordinary items have no note at all
    assert "rolls 1 piece" in idata.acquisition_note(
        "DemonGearUpgrade_FervToCrit")
    # the zone gear's verbose affinity-token note is gone — its Drops From
    # rows now name the sources directly (Zone activities / Unique Foes /
    # the faction mob unit groups) with no tooltip boilerplate at all
    # (see test_zone_gear_shown_drops_collapsed)
    assert idata.acquisition_note("Chest_Z1U1_Fig") is None


def test_no_source_reason_is_specific():
    """Every no-source gear piece gets a reason that says WHY its Drops From
    is empty — the WorldLoot boilerplate was wrong for most of them:
    starter gear is given at character creation, the Farseeker gear is shop
    stock, the White Shirt is base clothes, and crafted gear (including
    recipe-only InfusedTusk, which has no fixed level) is never dropped."""
    assert "Fighter class" in idata.no_source_reason("Chest_Starter_Fig")
    assert "Wizard class" in idata.no_source_reason("Feet_Starter_Wiz")
    assert "character creation" in idata.no_source_reason("Chest_Starter_Fig")
    assert "Base clothes" in idata.no_source_reason("Chest_C_BaseClothes")
    assert "Starter cache" in idata.no_source_reason("Z1_WeaponBundle")
    assert "vendor" in idata.no_source_reason("Back_Shop")
    assert "crafted or purchased" in idata.no_source_reason("InfusedTusk")
    assert "crafted or purchased" in idata.no_source_reason("Waist_RBee_FigWiz")
    # sourceless WorldLoot gear (the untagged attribute rings) gets the
    # plain readout — no invented token-mechanic text
    assert idata.no_source_reason("Finger_Cri") == "No drop sources found."
    # every no-source gear piece resolves to a reason
    from farever_companion.data.items.labels import _GEAR_TYPES
    for it in idata.search(""):
        if (it.get("type") or "") not in _GEAR_TYPES:
            continue
        if idata.resolve_drops(it["id"]):
            continue
        assert idata.no_source_reason(it["id"]), it["id"]


def test_no_source_reason_fact_checked():
    """The no-source reasons say what the game data actually supports — no
    fabricated generator-token claims:

    * the classes' gliders and gather tools are starting gear (unit.json
      `parts.gear` lists them next to the starter armor)
    * the source-less recipes carry WorldRecipeWithJob token rows
      (worldrecipe kind), not the WorldLoot claim
    * items listed in loot tables the scan can't anchor name the real table
      (Rift_Tier4, Blacksmith_Starter, the Coyotes table, ...)
    * the starter cache opens into the fixed-level class starter weapons —
      NOT scaled to your level
    * everything else (mounts/gliders without a concrete source, sourceless
      WorldLoot gear, non-gear materials/packages/currencies) gets the plain
      'No drop sources found.' readout — no invented generator/roll text
    """
    assert "Assassin class" in idata.no_source_reason("Glider_Owl_Grey")
    assert "Fighter class" in idata.no_source_reason("Glider_Owl_Brown")
    assert "Wizard class" in idata.no_source_reason("Glider_Raccoon_Grey")
    assert "Cleric class" in idata.no_source_reason("Glider_Raccoon_Orange")
    for tool in ("Sickle", "Pickaxe"):
        assert "every class" in idata.no_source_reason(tool)
        assert "Starting gear" in idata.no_source_reason(tool)
    # recipes: the source-less ones now carry the WorldRecipeWithJob
    # token rows (WorldCrate 0.2 + Humanoid 0.01); the reason branch
    # stays as the fallback for any recipe without rows
    r = idata.no_source_reason("Recipe_HonedCopperPlate")
    assert "WorldRecipeWithJob" in r and "WorldLoot" not in r
    rd = idata.resolve_drops("Recipe_HonedCopperPlate")
    assert any(d["kind"] == "worldrecipe" and d["table"] == "WorldCrate"
               and d["prob"] == pytest.approx(0.2) for d in rd)
    assert any(d["kind"] == "worldrecipe" and d["table"] == "Humanoid"
               and d["prob"] == pytest.approx(0.01) for d in rd)
    # mounts/gliders with no concrete source get the plain readout — the
    # old 'family loot entry' placeholder text is gone
    r = idata.no_source_reason("Mount_Wolf_03")
    assert r == "No drop sources found."
    assert "Starting gear" not in r
    # loot-listed items the scan can't anchor name their actual tables
    assert "Rift_Tier4" in idata.no_source_reason("Soulstone_Z1_4")
    assert "Blacksmith_Starter" in idata.no_source_reason("Hammer")
    assert "Coyotes table" in idata.no_source_reason("CoyoteMeat")
    # starter cache: fixed starter weapons, not scaled
    c = idata.no_source_reason("Z1_WeaponBundle")
    assert "not scaled" in c and "fixed-level" in c
    # non-gear never gets the WorldLoot claim — just the plain readout
    for iid in ("LP_Z1_Crab", "IronIngot", "CraftPoint", "HealthPotion",
                "TeleportationStone"):
        r = idata.no_source_reason(iid)
        assert r and "WorldLoot" not in r, iid


def test_token_mechanic_source_kinds():
    """World-generated drops are marked by the mechanic that produces them
    (the scan's documented source kinds - see scan_item_drops._token_mechanics):

      * recipes -> 'worldrecipe' rows: WorldCrate 0.2 + Humanoid 0.01, with
        the jobPossible/filterJob note
      * zone gear -> 'worldloot_affinity' rows: the five zone activities +
        FoeUniqueDrops, chance-less (the per-activity pool is untraced), with
        the hasUnitAptitude note
      * vendor-sold recipes keep only their vendor rows
      * the untagged attribute rings stay sourceless (reason only)
    """
    # recipe: token rows + mechanic note, no reason string
    rd = idata.resolve_drops("Recipe_HonedCopperPlate")
    assert {d["kind"] for d in rd} == {"worldrecipe"}
    assert any(d["kind"] == "worldrecipe" and d["table"] == "WorldCrate"
               and d["prob"] == pytest.approx(0.2) for d in rd)
    assert any(d["kind"] == "worldrecipe" and d["table"] == "Humanoid"
               and d["prob"] == pytest.approx(0.01) for d in rd)
    note = idata.acquisition_note("Recipe_HonedCopperPlate") or ""
    assert "WorldRecipeWithJob" in note and "jobPossible" in note
    # vendor-sold recipe: untouched by the token rows
    assert all(d["kind"] == "npc"
               for d in idata.resolve_drops("Recipe_SacrificePotion"))
    assert not idata.acquisition_note("Recipe_SacrificePotion")
    # zone gear: the collapsed affinity rows (Unique Foes + Zone
    # activities), chance-less — the scan's clean four-source shape
    aff = [d for d in idata.resolve_drops("Chest_Z1U1_Fig")
           if d["kind"] == "worldloot_affinity"]
    assert [d["source"] for d in aff] == ["Unique Foes", "Zone activities"]
    assert {d["table"] for d in aff} == {"FoeUniqueDrops", ""}
    assert all(d["prob"] == 0.0 for d in aff)  # chance-less
    # the zone gear's token note is dropped — the cleaned Drops From rows
    # name the sources (see test_zone_gear_shown_drops_collapsed)
    assert idata.acquisition_note("Chest_Z1U1_Fig") is None
    # the untagged rings keep their reason, no affinity rows
    assert idata.resolve_drops("Finger_Cri") == []
    assert idata.no_source_reason("Finger_Cri")


def test_crafted_gear_has_no_drop_sources():
    """Crafted gear is made by recipes, never dropped — the scan stamped
    the base set's dungeon rows on the crafted pieces (Beekeeper's Scarf
    shares the Bee Hive crate / boss rows), so their Drops From list must
    come back empty instead of showing dungeon sources. That covers the
    `_Craft` variants AND every other recipe-bearing piece: Aura of the
    Honeycomb, Cantal Goya's Breastplate, Rival Sabatons of Ironhorn,
    Caryapsid's Coccyx and the Alchemist stones are all crafted gear."""
    for iid in ("Back_RBee_FigWiz_Craft", "Hands_RManfish_FigAss_Craft",
                "Waist_RKobold_AssCle_Craft", "Back_RCrimson_AssCle_Craft",
                "Chest_RCrimson_Fig_Craft", "Feet_RBee_WizCle_Craft",
                "Waist_RBee_FigWiz", "Chest_RKobold_FigAss",
                "Feet_RCrimson_FigCle", "Waist_RManfish_Fig",
                "PhilosopherStone", "StoneOfPower"):
        assert idata.item_fixed_level(idata.item(iid)) is not None, iid
        assert idata.resolve_drops(iid) == [], iid
        assert idata.shown_drops(iid) == [], iid
    # ... while the BASE faction set pieces keep their real dungeon drops
    # (no recipe — the crafted variants' raw rows are not lost on them)
    for iid in ("Shoulders_RManfish_FigAss", "Waist_RBee_Wiz",
                "Chest_RBee_Fig", "Trinket_Kobold"):
        assert idata.item_fixed_level(idata.item(iid)) is None, iid
        assert idata.resolve_drops(iid), iid
    # consumables/materials have recipes too but still drop normally
    for iid in ("MinorHealingPotion", "ScrollOfDexterity", "MoteOfFire"):
        assert idata.resolve_drops(iid), iid


def test_scan_records_no_dungeon_rows_on_crafted_variants():
    """The item scan itself must not stamp dungeon rows on the crafted
    dungeon-set variants (Beekeeper's Scarf etc.). They're recipe-only gear:
    faction=Craft with a fixed level, never referenced by any loot table, and
    WorldLoot rolls the base faction piece instead — so the RAW data carries
    no drop rows for them (not just the resolve-layer filter). The base set
    pieces keep their crate/mob/upgrade rows.
    """
    from farever_companion.data import raw_item_drops
    raw = raw_item_drops.DATA["items"]
    crafted = ("Back_RBee_FigWiz_Craft", "Back_RCrimson_AssCle_Craft",
               "Chest_RBee_AssWiz_Craft", "Chest_RCrimson_Fig_Craft",
               "Chest_RCrimson_WizCle_Craft", "Chest_RKobold_FigAss",
               "Feet_RBee_WizCle_Craft", "Feet_RCrimson_FigCle",
               "Feet_RKobold_FigCle_Craft", "Feet_RManfish_AssWiz_Craft",
               "Hands_RKobold_Cle_Craft", "Hands_RManfish_FigAss_Craft",
               "Waist_RBee_FigWiz", "Waist_RKobold_AssCle_Craft",
               "Waist_RManfish_Fig", "Waist_RManfish_Wiz_Craft")
    for iid in crafted:
        assert raw[iid]["drops"] == [], iid
    # the base faction pieces still carry the real token-derived rows
    for iid in ("Shoulders_RManfish_FigAss", "Trinket_Kobold",
                "Chest_RBee_Fig", "Waist_RKobold_Ass"):
        assert raw[iid]["drops"], iid


def test_boss_rows_flagged_with_source_for_icon():
    """Named-boss sources are flagged boss with their raw unit id, so the
    item page can headline them with the boss's own icon + name. No chance
    numbers are fabricated for them — the page shows no percentage (the
    kind tag / odds were useless)."""
    rows = idata.resolve_drops("GA_Demon")
    by = {r["source"]: r for r in rows}
    boss = by["Nightking Maat Demon"]
    assert boss["boss"] is True
    assert boss["source_id"] == "DemonSuperElite"   # drives the icon
    # the faction bosses' set rows are flagged too (the crafted Kobold
    # chest — Cantal Goya's Breastplate — is recipe gear and drops
    # nothing, so the non-crafted Raclette Pan stands in for the set)
    rows = idata.resolve_drops("Trinket_Kobold")
    by = {r["source"]: r for r in rows}
    assert by["Reblochonk"]["boss"] is True
    assert by["Reblochonk"]["source_id"] == "Reblochonk"
    # the faction mobs share the table but are NOT bosses
    assert by["Kobold mobs"]["boss"] is False
    # boss rows carry their dungeon name for the dimmed sub-line: the
    # dungeon's in-game name, 'Rift (arena entrance)' for the rift bosses
    assert by["Reblochonk"]["dungeon"] == "Kobold Mines"
    bee = {r["source"]: r for r in idata.resolve_drops("Chest_RBee_Fig")}
    assert bee["Gatsbee"]["dungeon"] == "Bee Hive"
    # merged rows keep the flag, the source id and the dungeon (the icon
    # and its sub-line survive the per-source merge)
    merged = {r["source"]: r for r in idata.merge_drops("GA_Demon")}
    assert merged["Nightking Maat Demon"]["boss"] is True
    assert merged["Nightking Maat Demon"]["source_id"] == "DemonSuperElite"
    assert merged["Nightking Maat Demon"]["dungeon"] == \
        "Rift (arena entrance)"


def test_boss_rows_without_gear_table_stay_unknown():
    """The Bee boss's set rows reference the faction material table (which
    holds no gear) — the boss row carries no chance number for them and
    the page shows nothing instead of a made-up percentage."""
    rows = idata.resolve_drops("Chest_RBee_Fig")
    by = {r["source"]: r for r in rows}
    gatsbee = by["Gatsbee"]
    assert gatsbee["boss"] is True
    assert gatsbee["source_id"] == "Gatsbee"


def test_zone_gear_shown_drops_collapsed():
    """The WorldLoot zone gear's Drops From list shows the clean four
    source groups — the crate, the faction mob UNIT GROUPS on one line,
    Unique Foes and one Zone activities row. The shape now lives in the
    data itself (the scan emits it natively, see
    test_scan_records_zone_rows_on_worldloot_gear), so shown_drops just
    renders the merged rows: no runtime collapse, and the collapsed rows
    stay minimal — no zone/activity sub-lists, no tooltip detail
    (quiet=True) — the source name is the whole story."""
    rows = idata.shown_drops("Finger_Z2_Cri")      # Ringlet of Precision
    assert [r["source"] for r in rows] == [
        "World Crates (x158)",
        "Crimson mobs · Kobold mobs · Manfish mobs",
        "Unique Foes",
        "Zone activities",
    ]
    # no zone-name sub-lines or tooltip detail left on the collapsed rows
    assert all(not r["locs"] for r in rows[1:])
    assert all(r.get("quiet") for r in rows[1:])
    assert all("detail" not in r for r in rows[1:])
    # the zone armor sets collapse the same way
    rows = idata.shown_drops("Chest_Z2U2_Fig")
    assert rows[1]["source"] == "Crimson mobs · Kobold mobs · Manfish mobs"
    assert rows[-1]["source"] == "Zone activities"
    # dungeon sets / Demon pieces show their faction's DUNGEON BOSSES ONLY:
    # one stamped row per same-family dungeon (alphabetical after the merge)
    kob = idata.shown_drops("Trinket_Kobold")
    assert [r["source"] for r in kob] == \
        ["Golcano", "King Ratsar", "Munster Chuck", "Reblochonk"]
    assert all(r["boss"] and r.get("dungeon") for r in kob)
    ga = idata.shown_drops("GA_Demon")
    assert [r["source"] for r in ga] == ["Nightking Maat Demon"]
    # plain gear groups its per-mob rows per faction instead of listing
    # every mob variant (see test_mob_rows_group_per_faction)
    cloth = idata.shown_drops("Cloth_Z1")
    assert len(cloth) < len(idata.merge_drops("Cloth_Z1"))
    assert any(r["source"] == "Crimson mobs" for r in cloth)


def test_shown_drops_headlines_the_boss():
    """Gear with a boss shows its faction's DUNGEON BOSSES ONLY — one row
    per dungeon whose boss or mobs roll the same loot table as the
    recorded boss (all the bee dungeons drop bee gear). The shared crate /
    mob / vendor rows every set piece carries never show; non-gear items
    keep every source unchanged."""
    # Kobold set piece: one boss row per kobold-family dungeon (Kobold
    # Mines, King Ratsar Lair, Abandoned Mines, Gorgons Hollow), merged
    # into alphabetical order — no crates/mobs/vendors survive the collapse
    kob = idata.shown_drops("Trinket_Kobold")
    srcs = [r["source"] for r in kob]
    assert set(srcs) == {"Reblochonk", "King Ratsar", "Golcano",
                          "Munster Chuck"}
    assert all(r["boss"] for r in kob)
    assert not any("Crates" in s or "mobs" in s for s in srcs)
    assert not any("Mira" in s for s in srcs)
    # a weapon keeps just its own boss (its signature table rolls nothing
    # else, so no family expansion)
    assert [r["source"] for r in idata.shown_drops("GA_Demon")] \
        == ["Nightking Maat Demon"]
    # no boss -> the per-mob rows group per faction (see
    # test_mob_rows_group_per_faction), so the list stays short
    cloth = idata.shown_drops("Cloth_Z1")
    assert len(cloth) < len(idata.merge_drops("Cloth_Z1"))


def test_materials_keep_their_mob_sources_alongside_the_boss():
    """Craft materials with a boss source are NOT boss-only. The boss-only
    collapse is a GEAR rule (for a dungeon-set piece the shared faction
    crate/mob rows are noise); a material like Veiled Wing also drops from
    the demon MOBS — those are its farmable sources — so the full list
    shows, faction-grouped, with the boss leading. The training-dummy
    family (PunchingBag and its invulnerable/shielded variants) is never a
    real source and is filtered out everywhere."""
    rows = idata.shown_drops("VeiledWing")
    srcs = [r["source"] for r in rows]
    assert any(r.get("boss") for r in rows)          # the boss stays
    assert len(rows) > 1, srcs                        # but isn't the only row
    assert "Demon mobs" in srcs and "FaerieDemon mobs" in srcs
    assert "ImpDemon mobs" in srcs                    # the farmable mobs
    assert not any(s in ("Armor", "Invu", "MagicRes", "Punching Bag",
                         "Shielded") for s in srcs)  # no dummies
    for iid in ("DemonicHorn", "TailSlice", "FiendishEye"):
        more = idata.shown_drops(iid)
        assert len(more) > 1, (iid, [r["source"] for r in more])
    # gear lists only its faction's dungeon bosses — all four of them now
    kob = idata.shown_drops("Trinket_Kobold")
    assert len(kob) == 4
    assert {r["source"] for r in kob} == \
        {"Reblochonk", "King Ratsar", "Golcano", "Munster Chuck"}
    assert all(r["boss"] for r in kob)


def test_mob_rows_group_per_faction():
    """Plain gear's per-mob rows collapse into one cell per faction
    ('Crimson mobs', 'Manfish mobs', ...) instead of listing every mob
    variant — Cloth_Z1's 131 mob rows become a handful of faction cells.
    A faction only groups when it has MORE than one member: lone named mobs
    (Hunter Shepherd) and the TODO placeholder rows keep their own names.
    The group takes its first member's place in the list, unions the
    locations, and carries the faction as source_id (no underscore) so the
    cell renders the MOB sword marker."""
    rows = idata.shown_drops("Cloth_Z1")
    srcs = [r["source"] for r in rows]
    # faction groups replaced the individual mob rows
    assert "Crimson mobs" in srcs
    assert "Manfish mobs" in srcs and "Kobold mobs" in srcs
    assert not any("Blessed Crimson Executioner" in s for s in srcs)
    # lone named mob stays individual
    assert "Hunter Shepherd" in srcs
    assert not any("TODO" in s for s in srcs)
    # the group cell: faction source_id, unioned locations, kind unit
    crim = next(r for r in rows if r["source"] == "Crimson mobs")
    assert crim["kind"] == "unit"
    assert crim["source_id"] == "Crimson"
    assert crim["locs"]
    assert crim["n_locs"] == len(crim["locs"])
    # a faction with a single member does NOT group (still per-mob name)
    fly = [r for r in idata.shown_drops("DemonicSoul")
           if r["source"] == "Patrolfly Invader"]
    assert fly and fly[0]["source_id"] == "Fly_Demon_Rift"


def test_unknown_location_rows_resolved():
    """'unknown location' mob rows resolve to the source's location from the
    app's own data: soulstone demon bosses -> their summon-spot zone, dungeon
    mobs -> their dungeon + entrance, world mobs -> their spawn zones."""
    # Corrupted Gift drops from the soulstone demon bosses — each row now
    # shows the zone its soulstone sits in instead of 'unknown location'
    rows = idata.resolve_drops("DemonGearUpgradeRare_CritToAP")
    by_src = {r["source"]: r["loc"] for r in rows}
    assert by_src["Ariana Grandemon"] == "West Majoram Bridge"
    assert by_src["Asmodeaf"] == "Agias Theatre"
    assert by_src["Baphometal"] == "Isle of Flowers"
    assert by_src["Belzebeat"] == "Yesterland"
    # dungeon mob -> its dungeon + entrance zone
    merged = {r["source"]: r for r in idata.merge_drops("Cloth_Z1")}
    assert merged["Gorgon's Hollow Overseer"]["locs"] == \
        ["Abandoned Mines · Aurock Mound"]
    assert merged["Honey Slime of the Hivetree"]["locs"] == \
        ["Bee Hive · Rootbee Cave"]
    # world-mob variant -> its spawn zones (normalized mob_locs id)
    assert merged["Kobold Overseer"]["locs"] == ["Gorgon's Hollow"]


def test_unknown_location_resolution_is_conservative():
    """Summoned / TODO / training units have no recorded spawn — their rows
    stay 'unknown location' instead of guessing."""
    merged = {r["source"]: r for r in idata.merge_drops("Cloth_Z1")}
    assert merged["Kobold Raider"]["locs"] == []
    assert merged["Expert Nepsid Fighter"]["locs"] == []


def test_dungeon_set_derivation_is_conservative():
    """Only the dungeon-set ids get derived rows: the Demon set (already in
    the scan, sold by Mira), materials, shop gear and crafted pieces stay
    sourceless. (The zone sets are NOT sourceless anymore — the scan
    attributes their WorldLoot rows directly, see
    test_zone_gear_drops_real_chances.)"""
    for iid in ("Head_RDemon_Fig_Craft", "CopperIngot",
                "InfusedTusk", "Back_Shop"):
        assert idata.resolve_drops(iid) == [], iid


def test_rift_classification():
    assert idata.is_rift_location(
        "Z2_Azuram_Bridge (East Majoram Bridge) | entrances: POI_Rift_Entrance_1@Z2_Krisomal_North")
    assert idata.is_rift_location("Demon crates (soulstone events)")
    assert idata.is_rift_location("Z3_CrimsonIsland_Cathedral (Temple of Amon Ram)")
    assert not idata.is_rift_location("Z1_Enripit_Falls (Talitha Falls)")
    assert not idata.is_rift_location("unknown location")


def test_gear_ilevel_tiers_use_scaling_block():
    sc = idata.gear_scaling()
    assert sc.get("max_level") == 25
    sword = idata.item("Sword_Start")
    assert sword is not None
    tiers = idata.ilevel_tiers(sword)
    assert tiers is not None
    by = {t["rarity"]: t for t in tiers}
    # base iLevel = 10 x 25 = 250, + rarity bonuses from the scaling block
    assert by["Rare"]["base"] == 250 + sc["rarity_ilevel"]["Rare"] == 260
    assert by["Epic"]["base"] == 280
    assert by["Legendary"]["base"] == 300
    # max = base + 10 x upgrade cap (2/3/4/5)
    assert by["Rare"]["max"] == 260 + 10 * 3
    assert by["Legendary"]["max"] == 300 + 10 * 5
    assert by["Legendary"]["upgrades"] == 5


def test_upgrade_path_shows_every_step_for_own_rarity():
    sword = idata.item("Sword_Start")          # Uncommon (cap 2)
    path = idata.upgrade_path(sword)
    assert path is not None
    sc = idata.gear_scaling()
    step = sc.get("upgrade_ilevel", 10)
    cap = sc["rarity_upgrades"]["Uncommon"]
    base = 10 * sc["max_level"] + sc["rarity_ilevel"]["Uncommon"]
    assert path == [base + step * i for i in range(cap + 1)]
    assert path == [250, 260, 270]


def test_upgrade_materials_by_rarity():
    assert idata.upgrade_material("Uncommon") == "Spark Dust"
    assert idata.upgrade_material("Rare") == "Spark Shard"
    assert idata.upgrade_material("Epic") == "Spark Crystal"
    assert idata.upgrade_material("Legendary") == "Spark Crystal"
    assert idata.upgrade_material("Common") is None


def test_upgrade_costs_at_max_level():
    # cost = base[rank] x level ^ levelExponent, rounded to 2dp (constant.json
    # GearUpgrades, confirmed in GEAR_SCALING_README.md §8). At L25:
    # 25^0.05 = 1.17462...
    sword = idata.item("Sword_Start")     # Uncommon -> UpgradeAll, cap 2
    c = idata.upgrade_costs(sword)
    assert [x["count"] for x in c] == [round(15 * (25 ** 0.05), 2),
                                        round(30 * (25 ** 0.05), 2)]
    assert [x["count"] for x in c] == [17.62, 35.24]
    assert all(x["material"] == "Spark Dust" for x in c)
    # Rare -> UpgradeRare, cap 3
    rare = idata.upgrade_costs({"type": "Sword", "rarity": "Rare"})
    assert [x["count"] for x in rare] == [0.0, 5.87, 11.75]
    # Epic -> UpgradeEpic, exponent 0 -> flat base, cap 4
    epic = idata.upgrade_costs({"type": "Sword", "rarity": "Epic"})
    assert [x["count"] for x in epic] == [0, 0, 0, 3]
    # Legendary reuses UpgradeEpic, cap 5
    leg = idata.upgrade_costs({"type": "Sword", "rarity": "Legendary"})
    assert [x["count"] for x in leg] == [0, 0, 0, 3, 15]
    assert idata.upgrade_costs({"type": "Sword", "rarity": "Common"}) is None


def test_upgrade_gains_per_step():
    # Rift Demon Assassin chest (Rare, cap 3): each upgrade = +10 iLevel, so
    # the rank-1 gain equals gear_stats(L26) minus gear_stats(L25).
    it = idata.item("Chest_RDemon_Ass")
    gains = idata.upgrade_gains(it, level=25)
    assert gains is not None
    assert [g["upgrade"] for g in gains] == [1, 2, 3]
    assert all(g["gains"] for g in gains)      # every step adds something

    def vals(l):
        return {s["label"]: s["value"]
                for s in idata.gear_stats(it, level=l)["stats"] if s["value"]}

    d0, d1 = vals(25), vals(26)
    got = {g["label"]: g["value"] for g in gains[0]["gains"]}
    assert got == {k: d1[k] - d0[k] for k in d0 if d1[k] > d0[k]}
    # Guild Merchant gear (the starter weapons) scales like shop gear — the
    # vendor sells it at the town levels, so it has real per-rank gains
    gsw = idata.upgrade_gains(idata.item("Sword_Start"), level=25)
    assert gsw is not None and all(g["gains"] for g in gsw)
    # common rarity has no upgrade cap
    assert idata.upgrade_gains({"type": "Sword", "rarity": "Common"}) is None


def test_class_primary_stats():
    # derived from the aptitude block's group-0 curves
    assert idata.class_primary_stat("Assassin") == "Dexterity"
    assert idata.class_primary_stat("Fighter") == "Strength"
    assert idata.class_primary_stat("Wizard") == "Intellect"
    assert idata.class_primary_stat("Cleric") == "Faith"
    assert idata.class_primary_stat("Crit") is None     # no primary curve
    assert idata.class_primary_stat("Bogus") is None


def test_enchant_conversions():
    # Demon-gear augments (DemonGearUpgrade*): every stat conversion exists
    # as a Rare +20 and an Epic +40 version — the +N replaces the source
    # stat with the target, and the higher rarity has the higher value.
    conv = idata.enchant_conversions()
    assert len(conv) == 12                       # 4 stats x 3 others
    assert {c["rare"]["value"] for c in conv} == {20}
    assert {c["epic"]["value"] for c in conv} == {40}
    assert sorted({c["target"] for c in conv}) == [
        "Armor Penetration", "Critical", "Fervor", "Magic Penetration"]
    pairs = {(c["target"], c["source"]) for c in conv}
    assert len(pairs) == 12                      # every conversion unique
    for c in conv:
        assert c["rare"]["item"].startswith("DemonGearUpgradeRare_")
        assert c["epic"]["item"].startswith("DemonGearUpgrade_")
        assert "Rare" not in c["epic"]["item"]
    # source/target come from the authored stats, not the id: the item that
    # adds Critical and removes Fervor is the Fervor -> Critical conversion
    by_pair = {(c["source"], c["target"]): c for c in conv}
    ferv = by_pair[("Fervor", "Critical")]
    assert ferv["rare"]["item"] == "DemonGearUpgradeRare_FervToCrit"
    assert ferv["epic"]["item"] == "DemonGearUpgrade_FervToCrit"


def test_enchant_scrolls():
    # The +2 enchant scrolls cover the five primary stats; the corrupted
    # scrolls carry no authored stats in the scan, so their +4/−4 values
    # come from fareverdb (like the gem values).
    assert idata.enchant_scrolls() == {
        "Dexterity": 2, "Faith": 2, "Intellect": 2,
        "Strength": 2, "Vitality": 2}
    assert idata.own_stats(idata.item("ScrollOfCorruptedDexterity")) is None
    corrupt = idata.corrupted_scrolls()
    assert len(corrupt) == 4
    for c in corrupt:
        assert c["item"].startswith("ScrollOfCorrupted")
        assert c["value"] == 4 and c["penalty"] == "Vitality"
        assert idata.item(c["item"]) is not None
    assert sorted(c["stat"] for c in corrupt) == [
        "Dexterity", "Faith", "Intellect", "Strength"]
    # values come from the authored stats, not the id
    assert idata.item("ScrollOfVitality")["rarity"] == "Common"


def test_gem_augments():
    # Jeweller gem augments (values from MetaForge / fareverdb — the
    # bundled scan has none): 18 gems — 14 plain (Z1/Z2, all positive) plus
    # the 4 Cursed Eyes (Jeweller Lv 6) that grant three +9 ratings and
    # drain one −9.
    gems = idata.gem_augments()
    assert len(gems) == 18
    assert sorted({g["zone"] for g in gems}) == ["Cursed", "Z1", "Z2"]
    for g in gems:
        it = idata.item(g["item"])
        assert it and it["type"] == "AugmentJeweller", g["item"]
        assert g["stats"]
    pairs = [g for g in gems
             if "Vitality" not in g["stats"] and g["zone"] != "Cursed"]
    assert all(len(g["stats"]) == 2 for g in pairs)
    assert all(all(v > 0 for v in g["stats"].values())
               for g in gems if g["zone"] != "Cursed")
    # Cursed Eyes: three +9s and one −9 each, every stat penalized once
    cursed = [g for g in gems if g["zone"] == "Cursed"]
    assert len(cursed) == 4 and all(g["level"] == 6 for g in cursed)
    for g in cursed:
        assert sorted(v for v in g["stats"].values() if v > 0) == [9, 9, 9]
        assert [v for v in g["stats"].values() if v < 0] == [-9]
    assert {next(s for s, v in g["stats"].items() if v < 0)
            for g in cursed} == {"Critical", "Fervor", "Armor Penetration",
                                 "Magic Penetration"}
    # Z1 stones (Beryl/Amber) +4, Z2 stones (Ruby/Agate) +7 — the highest
    # version always carries the highest stats
    assert {max(g["stats"].values()) for g in pairs if g["zone"] == "Z1"} == {4}
    assert {max(g["stats"].values()) for g in pairs if g["zone"] == "Z2"} == {7}
    strong = [g for g in gems if "Vitality" in g["stats"]]
    assert sorted(list(g["stats"].values())[0] for g in strong) == [2, 4]
    # each tier of a stone grants a distinct stat pair
    beryl = [g for g in gems if g["item"].endswith("CutBeryl")]
    assert len({frozenset(g["stats"]) for g in beryl}) == len(beryl)


def test_matches_stat():
    # computed gear stats: 'dex' -> Dexterity, 'armor' -> Armor
    assert idata.matches_stat("Chest_RDemon_Ass", "dex")
    assert idata.matches_stat("Chest_RDemon_Ass", "armor")
    assert not idata.matches_stat("Chest_RDemon_Ass", "zzz")
    # compact forms: 'mpen' -> Magic Penetration on the rift weapon
    assert idata.matches_stat("GA_Demon", "mpen")
    assert idata.matches_stat("GA_Demon", "magic")
    # authored affixes count too (no computed stats)
    assert idata.matches_stat("Sword_Start", "vitality")
    assert idata.matches_stat("Sword_Start", "str")
    # empty / non-gear queries never match
    assert not idata.matches_stat("Chest_RDemon_Ass", "")
    assert not idata.matches_stat("CopperIngot", "dex")


def test_matched_stat_labels():
    # 'dex' -> Dexterity, the label the search-result tag shows
    assert idata.matched_stat_labels("Chest_RDemon_Ass", "dex") == ("Dexterity",)
    # shorthand aliases resolve to the full label
    assert idata.matched_stat_labels("GA_Demon", "mpen") == ("Magic Penetration",)
    # a query can match several labels on one item
    assert idata.matched_stat_labels("Sword_Swarm", "pen") == (
        "Magic Penetration", "Armor Penetration")
    # empty queries / non-matches yield nothing
    assert idata.matched_stat_labels("Chest_RDemon_Ass", "") == ()
    assert idata.matched_stat_labels("Chest_RDemon_Ass", "zzz") == ()
    assert idata.matched_stat_labels("CopperIngot", "dex") == ()


def test_gear_rarity_tiers_own_rarity():
    # Armor: per-rarity stats are NOT in the game yet (a later update may
    # add them) — the expansion is commented out, so the table shows only
    # the base column: the item's OWN rarity at its max upgrade rank (+N)
    it = idata.item("Chest_RDemon_Ass")
    assert it["rarity"] == "Rare"
    tiers = idata.gear_rarity_tiers(it, level=25)
    assert tiers is not None
    assert [t["rarity"] for t in tiers] == ["Rare"]
    assert [t["upgrades"] for t in tiers] == [3]
    # the one column equals the item's own-rarity stats at the same level
    own = {s["label"]: s["value"]
           for s in idata.gear_stats(it, level=25)["stats"]}
    base_col = {s["label"]: s["value"] for s in tiers[0]["stats"]}
    assert base_col == own
    # Shields are weapons (they carry affinity + skills in the sheets), so
    # they expand to the full weapon ladder from their DISPLAY rarity —
    # Shield_Start (authored Uncommon) is sold as Rare by the Guild
    # Merchants, so the columns start at Rare +3, not the authored +2
    shield = idata.item("Shield_Start")
    assert shield["rarity"] == "Uncommon"
    assert idata.category("Shield") == "Weapons"
    assert [(t["rarity"], t["upgrades"])
            for t in idata.gear_rarity_tiers(shield, level=25)] == [
                ("Rare", 3), ("Epic", 4), ("Legendary", 5)]
    # Jewelry is like armor: per-rarity stats not in the game yet — base
    # column only (Trinket_Demon, Rare +3)
    trinket = idata.item("Trinket_Demon")
    assert [(t["rarity"], t["upgrades"])
            for t in idata.gear_rarity_tiers(trinket, level=25)] == [
                ("Rare", 3)]
    # Weapons DO carry per-rarity stats in-game: the ladder from own up
    axe = idata.item("GA_Demon")
    assert axe["rarity"] == "Rare"
    assert [(t["rarity"], t["upgrades"])
            for t in idata.gear_rarity_tiers(axe, level=25)] == [
                ("Rare", 3), ("Epic", 4), ("Legendary", 5)]
    # Guild Merchant gear scales (the vendor sells it scaled to each town):
    # Sword_Start shows the full Rare/Epic/Legendary ladder; non-gear none
    assert [(t["rarity"], t["upgrades"]) for t in
            idata.gear_rarity_tiers(idata.item("Sword_Start"), level=25)] == [
                ("Rare", 3), ("Epic", 4), ("Legendary", 5)]
    assert idata.gear_rarity_tiers(idata.item("CopperIngot"), level=25) is None


def test_gear_rarity_tiers_carried_data_overrides():
    # the expansion is DATA-driven: when an item's compiled row carries
    # shipped per-rarity stats (a later game update), those values
    # override the computed columns — for every category, including armor
    # and jewelry whose expansion is otherwise commented out
    for iid in ("GA_Demon", "Chest_RDemon_Ass", "Trinket_Demon"):
        base = idata.item(iid)
        row = dict(base)
        row["rarity_stats"] = {
            "Epic": [{"n": "Dexterity", "v": 99}],
            "Legendary": [{"key": "dex", "label": "Dexterity",
                            "value": 111}],
        }
        tiers = idata.gear_rarity_tiers(row, level=25)
        assert tiers is not None, iid
        assert [(t["rarity"], t["upgrades"]) for t in tiers] == [
            ("Rare", 3), ("Epic", 4), ("Legendary", 5)], iid
        by = {t["rarity"]: {s["label"]: s["value"] for s in t["stats"]}
              for t in tiers}
        assert by["Epic"]["Dexterity"] == 99, iid
        assert by["Legendary"]["Dexterity"] == 111, iid
        # rarities without carried data still compute from the curves (the
        # base column here)
        own = {s["label"]: s["value"]
               for s in idata.stat_rows(
                   idata.gear_stats(base, level=25)["stats"])}
        assert by["Rare"] == own, iid



def test_upgrade_ladder_per_rarity():
    # Rift Demon chest (Rare armor): per-rarity stats not in the game yet,
    # so only its OWN rarity column — Rare +3 — with the real cap, base
    # stats, per-step gains and material costs.
    it = idata.item("Chest_RDemon_Ass")
    lad = idata.upgrade_ladder(it, level=25)
    assert lad is not None
    assert [r["rarity"] for r in lad] == ["Rare"]
    assert [r["upgrades"] for r in lad] == [3]
    assert [len(r["steps"]) for r in lad] == [3]
    # the base column equals the item's own-rarity stat values
    own = {s["key"]: s["value"]
           for s in idata.gear_stats(it, level=25)["stats"]}
    assert sum(b["value"] for b in lad[0]["base"]) == sum(own.values())
    # Rare material row UpgradeRare: base [0,5,10,15,20] x 25^0.05
    counts = [s["count"] for s in lad[0]["steps"]]
    assert counts[0] == 0
    assert round(counts[1], 2) == 5.87
    assert round(counts[2], 2) == 11.75
    assert lad[0]["material"] == "Spark Shard"
    # Weapons carry the full per-rarity ladder — Rare +3 / Epic +4 /
    # Legendary +5 — computed from the curves (they use per-rarity stats
    # in-game)
    axe = idata.item("GA_Demon")
    axel = idata.upgrade_ladder(axe, level=25)
    assert [r["rarity"] for r in axel] == ["Rare", "Epic", "Legendary"]
    assert [r["upgrades"] for r in axel] == [3, 4, 5]
    assert [len(r["steps"]) for r in axel] == [3, 4, 5]
    assert all(s["gains"] for r in axel for s in r["steps"])
    # Jewelry is like armor: per-rarity stats not in the game yet, so one
    # column — Rare +3
    tr = idata.item("Trinket_Demon")
    trl = idata.upgrade_ladder(tr, level=25)
    assert [r["rarity"] for r in trl] == ["Rare"]
    assert [r["upgrades"] for r in trl] == [3]
    assert all(s["gains"] for r in trl for s in r["steps"])
    # Carried per-rarity data (a later game update) overrides the ladder's
    # base columns — including for armor/jewelry — and Epic's 4th upgrade
    # still costs 3x Spark Crystal (UpgradeEpic base [0,0,0,3,15])
    for iid in ("Chest_RDemon_Ass", "Trinket_Demon"):
        row = dict(idata.item(iid))
        row["rarity_stats"] = {
            "Epic": [{"n": "Strength", "v": 1}],
            "Legendary": [{"n": "Strength", "v": 1}],
        }
        cld = idata.upgrade_ladder(row, level=25)
        assert [r["rarity"] for r in cld] == ["Rare", "Epic", "Legendary"], iid
        assert [r["upgrades"] for r in cld] == [3, 4, 5], iid
        assert [len(r["steps"]) for r in cld] == [3, 4, 5], iid
        assert all(s["gains"] for r in cld for s in r["steps"]), iid
        # the carried Epic column's base uses the carried value, and the
        # Epic material row is real (caps/materials from rarity.json)
        assert cld[1]["base"] == [{"label": "Strength", "value": 1}], iid
        assert cld[1]["material"] == "Spark Crystal", iid
        assert cld[1]["steps"][3]["count"] == 3, iid
    # Guild Merchant gear (the starter weapons) is SHOP gear: the vendor
    # sells it scaled to each town, so even the fixed-affix handout row
    # scales via its type's budget — the ladder shows the full Rare/Epic/
    # Legendary columns with real values, like the town weapons
    sw = idata.upgrade_ladder(idata.item("Sword_Start"), level=25)
    assert [r["rarity"] for r in sw] == ["Rare", "Epic", "Legendary"]
    assert [r["upgrades"] for r in sw] == [3, 4, 5]
    assert all(s["gains"] for r in sw for s in r["steps"])
    assert {b["label"] for b in sw[0]["base"]} >= {"Vitality", "Strength"}
    # non-gear has no ladder
    assert idata.upgrade_ladder(idata.item("CopperIngot"), level=25) is None


def test_upgrade_max_totals_match_rarity_tiers():
    """The BY STEP table's last column = BY RARITY base + every step's gain:
    the ladder's base equals the rarity tiers' values for each rarity, and
    base + the summed per-step gains equals the real stat values at the max
    upgrade level (the end state the BY STEP table's final column shows)."""
    for item_id in ("Chest_RDemon_Ass", "GA_Demon", "Shield_Start",
                    "Sword_Swarm"):
        it = idata.item(item_id)
        if not it or not it.get("atb"):
            continue
        tiers = idata.gear_rarity_tiers(it, level=25)
        lad = idata.upgrade_ladder(it, level=25)
        assert tiers and lad, item_id
        # the two tables describe the same rarity ladder with the same caps
        assert [t["rarity"] for t in tiers] == [r["rarity"] for r in lad]
        assert [t["upgrades"] for t in tiers] == [r["upgrades"] for r in lad]
        for t, r in zip(tiers, lad):
            by = {s["label"]: s["value"] for s in t["stats"]}
            base = {b["label"]: b["value"] for b in r["base"]}
            # BY RARITY base column and the ladder's base agree per rarity
            assert base == by, (item_id, r["rarity"])
            # MAX total = base + all step gains == real stats at lvl + cap
            end = idata.gear_stats(it, level=25 + r["upgrades"],
                                   rarity=r["rarity"])
            ev = {s["label"]: s["value"]
                  for s in idata.stat_rows(end["stats"])}
            for label, bv in by.items():
                gains = sum(g["value"] for s in r["steps"]
                            for g in s["gains"] if g["label"] == label)
                assert bv + gains == ev[label], \
                    (item_id, r["rarity"], label, bv, gains, ev[label])


def test_item_level_formula():
    assert idata.item_level(25, "Rare") == 260          # 250 + 10
    assert idata.item_level(25, "Epic") == 280          # 250 + 30
    assert idata.item_level(25, "Legendary") == 300     # 250 + 50
    assert idata.item_level(10, "Common") == 100
    assert idata.item_level(25, "Rare", upgrades=9) == 290   # capped at 3


def test_gear_stats_computed():
    # Rift Demon Assassin chest (Mira's Rift cache, scales to the player):
    # values from the game's model — exponential power ratio (see the c
    # comment in gear_stats), float math + math_round, and the live
    # single-class armor multiplier (_SINGLE_CLASS_ARMOR Assassin 1.80):
    # the sheets alone give 128 armor, the live model reads 230.
    it = idata.item("Chest_RDemon_Ass")
    gs = idata.gear_stats(it, level=25, rarity="Rare")
    assert gs is not None
    by = {s["label"]: s for s in gs["stats"]}
    assert by["Dexterity"]["value"] == 12
    assert by["Armor"]["value"] == 230
    assert by["Fervor"]["value"] == 30
    assert by["Vitality"]["value"] == 9
    assert gs["ilevel"] == 260
    # lower level -> lower numbers
    gs10 = idata.gear_stats(it, level=10, rarity="Rare")
    assert gs10["stats"][0]["value"] < gs["stats"][0]["value"]
    # gear with no atb stamp rolls its TYPE's slot budget (itemType.json):
    # armor like the Reinforced Hauberk shows real computed values too
    armor = idata.item("Chest_Z1U1_Fig")
    assert not armor.get("atb"), "fixture: this armor has no atb stamp"
    ags = idata.gear_stats(armor, level=25, rarity="Rare")
    assert ags is not None
    assert any(s["value"] > 0 for s in ags["stats"])
    # non-gear has no computed stats; Guild Merchant gear (the starter
    # weapons) scales like the town weapons
    assert idata.gear_stats(idata.item("CopperIngot")) is None
    assert idata.gear_stats(idata.item("Sword_Start"), level=25) is not None
    assert idata.gear_stats(idata.item("Honey_Z1")) is None
    assert idata.gear_stats({"type": "CopperIngot"}) is None


def test_gear_splits_show_per_aptitude_lines():
    """Multi-aptitude gear displays each aptitude's contribution as its own
    stat line — the game shows Ram Faceshield's Critical + Fervor (and
    Strength + Dexterity) separately, not one aggregated 'Critical 30' row
    that hides Fervor entirely."""
    it = idata.item("Head_RCrimson_FigAss")   # Ram Faceshield (Rare armor)
    lad = idata.upgrade_ladder(it, level=25)
    base = {b["label"]: b["value"] for b in lad[0]["base"]}
    assert base["Critical"] == 15
    assert base["Fervor"] == 15
    assert base["Dexterity"] == 6
    assert base["Strength"] == 5
    assert base["Armor"] == 224
    assert base["Vitality"] == 10
    # the BY RARITY tiers expand the same way
    tiers = idata.gear_rarity_tiers(it, level=25)
    tby = {s["label"]: s["value"] for s in tiers[0]["stats"]}
    assert tby["Critical"] == 15 and tby["Fervor"] == 15
    assert tby["Strength"] == 5 and tby["Dexterity"] == 6


def test_same_label_ratings_sum_across_classes():
    """When two classes roll the SAME rating (Fig+Cle Crimson both roll
    Fervor), the game sums the shares into one line instead of showing one
    class's contribution: the Breastplate of Recklessness reads Fervor
    ~27-30 in-game, not 15. Different-label splits (Ram Faceshield's
    Critical + Fervor) are unaffected."""
    chest = idata.item("Chest_RCrimson_FigCle")   # Fig+Cle, both -> Fervor
    gs = idata.gear_stats(chest, level=25)
    fervor = next(s for s in gs["stats"] if s["key"] == "ratings")
    assert fervor["label"] == "Fervor"
    assert fervor["value"] == 30                 # 2 x 15, not the deduped 15
    assert not fervor.get("splits")               # one summed line, no split
    # a same-label pair at a low level (Kobold starter): Armored Docker Cap
    # reads Critical 15 at ~L8 in-game vs 8 deduped
    cap = idata.item("Head_RKobold_FigCle")
    cgs = idata.gear_stats(cap, level=8)
    crit = next(s for s in cgs["stats"] if s["key"] == "ratings")
    assert crit["value"] == 16                   # 2 x 8, matches the live 15


def test_gear_ratings_include_split_labels():
    """The rollable-rating set mirrors the split stat lines: multi-class
    gear rolls EVERY aptitude's rating — Ram Faceshield's rating rows split
    into Critical + Fervor, so the rating filter / chips must offer both
    (a split label missing from the aggregate row is still rollable)."""
    rf = idata.item("Head_RCrimson_FigAss")   # Ram Faceshield (Fig+Assassin)
    assert "Critical" in rf["aptitudes"] or "Assassin" in rf["aptitudes"]
    ratings = idata.gear_ratings(rf["id"])
    assert "Critical" in ratings and "Fervor" in ratings
    # single-aptitude gear is unaffected: Crimson Wings rolls Fervor only
    cw = idata.item("Back_RCrimson_Fig")
    assert idata.gear_ratings(cw["id"]) == ("Fervor",)
    # non-rating split labels never leak in (Strength/Dexterity are primaries)
    assert not set(ratings) - set(idata.RATING_STATS)


def test_single_class_armor_uses_live_factor():
    """Single-class armor reads the live game's per-class multiplier
    (_SINGLE_CLASS_ARMOR) on top of the sheet math: Crimson Wings shows ~100
    Armor at L25 in-game vs 45 with the sheet's 0.05 budget alone, and the
    computed value lands in that range (100) instead. Multi-class gear is
    NOT multiplied — Ram Faceshield's 2-class sum already matches in-game
    (224 vs 225), so the factor is gated on a single aptitude."""
    it = idata.item("Back_RCrimson_Fig")      # Crimson Wings (Fighter, 1 class)
    gs = idata.gear_stats(it, level=25)
    armor = next(s for s in gs["stats"] if s["key"] == "armor")
    assert armor["value"] == 100
    assert 90 <= armor["value"] <= 110
    # multi-class Back (Brie von de Cape) keeps the sheet's 0.05 budget sum
    # — user-confirmed in-game at L25: Armor 72, Vit 6, Str 3, Int 4,
    # Crit 8, MaPen 8 (identical on both classes)
    brie = idata.item("Back_RKobold_FigWiz")
    bgs = idata.gear_stats(brie, level=25)
    bby = {}
    for s in bgs["stats"]:
        if s.get("splits"):
            for sp in s["splits"]:
                bby[sp["label"]] = sp["value"]
        elif s.get("label") and s["value"]:
            bby[s["label"]] = s["value"]
    assert bby["Armor"] == 72 and bby["Vitality"] == 6
    assert bby["Strength"] == 3
    # the primary floats are rounded (the game's math_round), not floored:
    # Intellect's nominal 3.88 rounds to 4, matching the live tooltip (the
    # old int(cv*t) truncation showed a lossy 3)
    assert bby["Intellect"] == 4
    assert bby["Critical"] == 8 and bby["Magic Penetration"] == 8
    # the sheet budget (0.05) also holds at a low level: L11 -> (Fig+Wiz) x 0.05
    bgs11 = idata.gear_stats(brie, level=11)
    barmor = next(s for s in bgs11["stats"] if s["key"] == "armor")
    assert barmor["value"] == 36


def test_stat_text_modes_rounding_true_both():
    """stat_text renders per the display mode: 'rounding' (the DEFAULT — the
    clean game-tooltip integer), 'true' (the exact computed float, 3
    decimals, trailing zeros stripped), or 'both' ('4 (3.985)'). Exact
    values and authored stats (no raw) always render plainly."""
    assert idata.stat_display_mode() == "rounding"       # default is clean
    assert idata.stat_text(4, 3.9854) == "4"
    idata.set_stat_display_mode("both")
    try:
        assert idata.stat_text(4, 3.9854) == "4 (3.985)"
        assert idata.stat_text(64, 63.9) == "64 (63.9)"
        assert idata.stat_text(72, 72.214) == "72 (72.214)"
        assert idata.stat_text(100, 100.0) == "100"      # exact -> no parens
        assert idata.stat_text(4, 3.9996) == "4"         # rounds to 4.000
    finally:
        idata.set_stat_display_mode("rounding")
    idata.set_stat_display_mode("true")
    try:
        assert idata.stat_display_mode() == "true"
        assert idata.stat_text(4, 3.9854) == "3.985"
        assert idata.stat_text(5, None) == "5"           # authored stat
    finally:
        idata.set_stat_display_mode("rounding")
    idata.set_stat_display_mode("bogus")                 # unknown ignored
    assert idata.stat_display_mode() == "rounding"
    # the ladder carries the true floats too: Brie's Intellect row shows
    # its nominal (the live game rounds it to 4)
    brie = idata.item("Back_RKobold_FigWiz")
    lad = idata.upgrade_ladder(brie, level=25)
    base = {b["label"]: b for b in lad[0]["base"]}
    int_row = base["Intellect"]
    assert int_row["value"] == 4
    assert 3.5 < int_row["raw"] < 4.0        # a true fraction, not an int


def test_categories_for_gear_tab():
    assert idata.category("Chest") == "Armor"
    assert idata.category("Sword") == "Weapons"
    assert idata.category("GearTrinket") == "Accessory"
    assert idata.category("Food") == "Consumable"
    assert idata.category("Recipe") == "Crafting"
    assert idata.category("WeirdType") == "Misc"
    assert idata.gear_classes() == ["Fighter", "Assassin", "Cleric", "Wizard"]
    # every gear slot maps to a non-Misc category
    for it in idata.items():
        if idata.is_gear(it):
            assert idata.category(it["type"]) != "Misc"


def test_gear_slots_grouped_by_category():
    slots = idata.gear_slots()
    assert len(slots) > 30
    # jewelry slots are browsable in the gear tab
    assert {"GearFinger", "GearNeck", "GearTrinket"} <= set(slots)
    # non-gear / utility slots are not part of the gear tab
    assert "Mount" not in slots and "GearGlider" not in slots
    assert "Recipe" not in slots
    # grouped by category order: Weapons first, then Armor, Accessory, Crafting
    cats = [idata.category(t) for t in slots]
    order = {"Weapons": 0, "Armor": 1, "Accessory": 2, "Crafting": 3, "Misc": 4}
    assert cats == sorted(cats, key=order.get)
    # every listed slot actually has gear items
    for t in slots:
        assert any(it.get("type") == t for it in idata.items()), t


def test_gear_tables_grouped():
    """Gear tab loot-table filter: tables grouped by Rift / Vendor / Faction /
    Boss / World, each listing only gear items that share that table."""
    tables = idata.gear_tables()
    assert tables, "should be loot tables that drop gear"
    ids = [t["id"] for t in tables]
    # the key shared tables exist with their groups
    assert {"Rift_GearWithAffinity", "Rift_WeaponWithAffinity", "Shop",
            "Maat", "Clothes"} <= set(ids)
    by = {t["id"]: t for t in tables}
    assert by["Rift_GearWithAffinity"]["group"] == "Rift"
    assert by["Shop"]["group"] == "Vendor"
    assert by["Bee"]["group"] == "Faction"       # the faction set table
    assert by["Kobold"]["group"] == "Faction"
    assert by["Maat"]["group"] == "Boss"
    assert by["Clothes"]["group"] == "World"
    # groups sort Rift -> Vendor -> Faction -> Boss -> World
    order = [t["group"] for t in tables]
    assert order == sorted(order, key={"Rift": 0, "Vendor": 1,
                                       "Faction": 2, "Boss": 3,
                                       "World": 4}.get)
    # every table really drops the claimed gear count
    for t in tables:
        gear = [it for it in idata.items()
                if idata.is_gear(it) and idata.drops_from_table(it["id"], t["id"])]
        assert len(gear) == t["count"], t["id"]


def test_gear_table_shares_boss_set():
    """The named-boss tables are the 'same mob type' shared drops — each gear
    piece in the table is dropped by the same boss, and none is in another
    boss's table."""
    boss_tables = [t["id"] for t in idata.gear_tables() if t["group"] == "Boss"]
    assert len(boss_tables) >= 10
    for tbl in boss_tables:
        gear = [it for it in idata.items()
                if idata.drops_from_table(it["id"], tbl)]
        assert gear
        # every piece shares exactly the same source (the boss)
        srcs = {r["source"] for it in gear for r in idata.resolve_drops(it["id"])
                if r["table"] == tbl}
        assert len(srcs) == 1, (tbl, srcs)


def test_drops_from_table_filter():
    # rift gear vs rift weapons vs a specific boss's set
    assert idata.drops_from_table("Chest_RDemon_Ass", "Rift_GearWithAffinity")
    assert idata.drops_from_table("GA_Demon", "Rift_WeaponWithAffinity")
    assert not idata.drops_from_table("GA_Demon", "Rift_GearWithAffinity")
    assert idata.drops_from_table("GA_Demon", "")          # no filter = all
    assert idata.drops_from_table("GA_Demon", "Maat")       # its boss's set
    assert not idata.drops_from_table("GA_Demon", "Reblochonk")  # other boss
    assert not idata.drops_from_table("CopperIngot", "Rift_GearWithAffinity")


def test_weapon_attack_real_data():
    """Weapons carry their base attack (affinity + WeaponPower ratio) from
    the item's own skills list — the game's damage model, not a fake number."""
    ga = idata.weapon_attack(idata.item("GA_Demon"))
    assert ga == {"affinity": "Physical", "ratio": 0.7}   # GreatAxe 0.70x
    sword = idata.weapon_attack(idata.item("Sword_Start"))
    assert sword == {"affinity": "Physical", "ratio": 0.13}
    staff = idata.weapon_attack(idata.item("Staff_Craft"))
    assert staff is not None and staff["affinity"] == "Fire"
    # non-weapons / shields roll no base attack
    assert idata.weapon_attack(idata.item("Chest_RDemon_Ass")) is None
    assert idata.weapon_attack(idata.item("Shield_Craft")) is None
    # every weapon in the catalog carries an attack
    for it in idata.items():
        if it["type"] in ("GreatAxe", "Daggers", "Halos", "Staff", "DualAxes",
                           "Scepter", "Axe", "Crescent", "Spear", "Fists",
                           "DualMaces", "Thrown", "Sword", "Book", "Bow",
                           "Mace", "GreatMace", "DualSwords", "GreatSword"):
            assert idata.weapon_attack(it), it["id"]
    assert idata.affinity_color("Fire").startswith("#")


def test_own_stats_authored_affixes():
    """Authored gear shows its own flat stat values (the item sheet affixes)."""
    sword = idata.own_stats(idata.item("Sword_Start"))
    assert sword == [{"n": "Vitality", "v": 2}, {"n": "Strength", "v": 2}]
    # augments carry negative stats too
    aug = idata.own_stats(
        idata.item("DemonGearUpgradeRare_FervToCrit"))
    assert aug and any(s["v"] < 0 for s in aug)
    # generated gear has no authored stats (rolls from aptitude curves)
    assert idata.own_stats(idata.item("Chest_RDemon_Ass")) is None
    assert idata.own_stats(idata.item("CopperIngot")) is None


def test_non_gear_has_no_tiers():
    assert idata.ilevel_tiers(idata.item("Honey_Z1")) is None
    assert idata.upgrade_path(idata.item("Honey_Z1")) is None


def test_item_fixed_level_crafted_gear():
    """Crafted gear carries its OWN fixed level in the sheet (the level it
    is made at), so the item page shows its stats there with no scaling.
    Dropped and generated gear rolls stats from the source's level and has
    none."""
    # the zone sets (Reinforced Hauberk of the Exile, the Blessed gear) are
    # NOT crafted — they carry the sheet's WorldLoot flag, so the game
    # generates them from the WorldLoot token (zone mobs / crates) as
    # Uncommon drops with an authored zone-tier level. The old
    # "iLevel == 10 x level" craft signal (true of ANY Uncommon gear at
    # base iLevel) wrongly pinned them.
    for iid in ("Chest_Z1U1_Fig", "Legs_Z1U1_FigAss", "Chest_Z2U1_Fig",
                "Chest_Z2U1_FigCle", "Legs_Z2U1_FigCle", "Chest_Z2U2_Fig"):
        assert idata.item_fixed_level(idata.item(iid)) is None, iid
    # ... and their authored zone-tier level seeds the slider / badge
    # instead of the global max (no drop rows to derive a source level)
    assert idata.item_source_max_level("Chest_Z1U1_Fig") == 2
    assert idata.item_source_max_level("Chest_Z2U1_FigCle") == 14
    # the crafted _Craft / RCraft variants are made at their sheet level —
    # including the dungeon-set recipe variants like Beekeeper's Scarf
    # (crafted at L10, Blacksmith L2), which are crafted gear, NOT drops
    assert idata.item_fixed_level(idata.item("Shoulders_RDemon_Ass_Craft")) == 25
    assert idata.item_fixed_level(idata.item("Finger_Z3RCraft_Cri")) == 25
    assert idata.item_fixed_level(idata.item("Back_RBee_FigWiz_Craft")) == 10
    assert idata.item_fixed_level(idata.item("Hands_RManfish_FigAss_Craft")) == 10
    assert idata.item_fixed_level(idata.item("Back_RCrimson_AssCle_Craft")) == 20
    assert idata.item_fixed_level(idata.item("Feet_RBee_WizCle_Craft")) == 15
    # ... and ANY gear with a producing recipe is crafted too, even
    # without the id token: Aura of the Honeycomb (Blacksmith L3), the
    # faction-set pieces with recipes (Cantal Goya's Breastplate, Rival
    # Sabatons of Ironhorn, Caryapsid's Coccyx) and the Alchemist stones
    assert idata.item_fixed_level(idata.item("Waist_RBee_FigWiz")) == 15
    assert idata.item_fixed_level(idata.item("Chest_RKobold_FigAss")) == 6
    assert idata.item_fixed_level(idata.item("Feet_RCrimson_FigCle")) == 20
    assert idata.item_fixed_level(idata.item("Waist_RManfish_Fig")) == 6
    assert idata.item_fixed_level(idata.item("PhilosopherStone")) == 10
    assert idata.item_fixed_level(idata.item("StoneOfPower")) == 6
    # a recipe with no sheet level stays scalable (the level is unknown)
    assert idata.item_fixed_level(idata.item("InfusedTusk")) is None
    # ... but the town weapons carry the same _Craft token and are SHOP
    # gear — the Guild Merchants sell Credence / Glory / Radiance /
    # Judgement scaled to each zone, so they are NOT fixed
    for iid in ("Sword_Craft", "Bow_Craft", "Staff_Craft", "GA_Craft",
                "Shield_Craft", "Book_Start"):
        assert idata.item_fixed_level(idata.item(iid)) is None, iid
    # drops / generated gear has no fixed level — stats roll per source
    for iid in ("GA_Demon", "Chest_RDemon_Ass", "Shoulders_RManfish_FigAss",
                "Trinket_Demon", "Back_Shop", "Sword_Start"):
        assert idata.item_fixed_level(idata.item(iid)) is None, iid


def test_item_source_max_level_real_sources():
    """A gear item's max level comes from its REAL drop sources. A mob/boss
    drop governs even when a vendor also sells the piece: Judgement drops
    from the level-19 boss Munster Chuck, so it caps at 19 (the boss drop
    is the scale, not the shop); Worldsplitter drops from the level-25 rift
    boss. Everything else — shop gear (the Guild Merchants sell the town
    weapons at item level 20 in Tyrna and 25 in Lower Ramburg), player-
    scale chests (Mira's Demon set scales to YOUR level) and pure drops —
    has no cap (the global 25)."""
    # boss drop governs: Judgement -> Munster Chuck L19, GA_Demon -> 25
    assert idata.item_source_max_level("GA_Craft") == 19
    assert idata.item_source_max_level("GA_Demon") == 25
    # shop gear sells up to the global max: the town weapons top at L25
    # (Lower Ramburg's item level) — no zone-level cap
    assert idata.item_source_max_level("Sword_Craft") is None
    assert idata.item_source_max_level("Bow_Craft") is None
    assert idata.item_source_max_level("Staff_Craft") is None
    assert idata.item_source_max_level("Book_Start") is None
    # Mira's Rift chest: scales to the player -> no cap (the global 25)
    assert idata.item_source_max_level("Chest_RDemon_Ass") is None
    assert idata.item_source_max_level("Legs_RDemon_FigAss") is None
    assert idata.item_source_max_level("Trinket_Demon") is None
    # pure drops cap at their highest real mob level too: Cloth_Z1 tops at
    # the level-25 Knight Slime (= the global max), and Sword_Swarm only
    # drops from the level-13 Bee boss Lady Bee. Placeholder units (the
    # level-100 prisoners) never count; crafted gear has no sources at all.
    assert idata.item_source_max_level("Cloth_Z1") == 25
    assert idata.item_source_max_level("Sword_Swarm") == 13
    # WorldLoot zone gear (not crafted, not shop) caps at its authored
    # zone-tier level — the Reinforced Hauberk is a Z1U1 drop at L2
    assert idata.item_source_max_level("Chest_Z1U1_Fig") == 2


def test_item_vendor_levels():
    """Guild Merchant gear is sold at the town levels only (Tyrna L20 /
    Lower Ramburg L25) — the item page restricts the level slider to
    exactly these. The starter weapons and the town weapons are sold by
    both towns; gear with no merchant source has no vendor levels."""
    assert idata.item_vendor_levels("Daggers_Start") == (20, 25)
    assert idata.item_vendor_levels("Sword_Start") == (20, 25)
    assert idata.item_vendor_levels("Sword_Craft") == (20, 25)
    assert idata.item_vendor_levels("GA_Craft") == (20, 25)
    # non-vendor gear — no merchant levels at all
    assert idata.item_vendor_levels("GA_Demon") == ()
    assert idata.item_vendor_levels("Chest_RDemon_Ass") == ()
    assert idata.item_vendor_levels("Sword_Swarm") == ()
    assert idata.item_vendor_levels("") == ()


def test_item_scale_max_level_vendor_gear():
    """The gear-scale slider opens on the vendor's TOP sale for Guild
    Merchant gear — the Rare stats at L25 are what the vendor actually
    sells — even when the piece also drops elsewhere (Judgement drops from
    the L19 boss Munster Chuck, but the Guild Merchant sells it at L25).
    Non-vendor gear keeps its real-source cap."""
    assert idata.item_scale_max_level("Daggers_Start") == 25
    assert idata.item_scale_max_level("Sword_Craft") == 25
    assert idata.item_scale_max_level("GA_Craft") == 25   # vendor wins over the L19 boss
    assert idata.item_scale_max_level("Sword_Swarm") == 13  # pure drop: real source
    assert idata.item_scale_max_level("GA_Demon") == 25
    assert idata.item_scale_max_level("Chest_Z1U1_Fig") == 2


def test_item_display_rarity_shop_quality():
    """Guild Merchant gear displays the shop quality the vendor sells —
    Rare (at L20/L25) — not the authored starter rarity. The starter
    weapons are Uncommon in the item data (the character-creation handout
    quality), but the town vendors sell them as Rare, so the tiles / header
    show Rare. Non-gear merchant stock (materials) and non-vendor gear keep
    their authored rarity."""
    assert idata.item_display_rarity("Book_Start") == "Rare"
    assert idata.item_display_rarity("Daggers_Start") == "Rare"
    assert idata.item_display_rarity("Sword_Start") == "Rare"
    # the town weapons are already Rare in the data — unchanged
    assert idata.item_display_rarity("Sword_Craft") == "Rare"
    assert idata.item_display_rarity("GA_Craft") == "Rare"
    # non-vendor gear keeps the authored rarity
    assert idata.item_display_rarity("GA_Demon") == "Rare"
    assert idata.item_display_rarity("Sword_Swarm") == "Rare"
    assert idata.item_display_rarity("Chest_Z1U1_Fig") == "Uncommon"
    # non-gear merchant stock is NOT relabeled — the shop-quality claim
    # only applies to gear
    assert idata.item_display_rarity("Vial") == "Common"
    assert idata.item_display_rarity("SmallPouch") == "Uncommon"
    assert idata.item_display_rarity("") == ""


def test_upgrade_path_rarity_override():
    """upgrade_path can be told the DISPLAY rarity — the items page passes
    Rare for Guild Merchant gear, so the starter weapons badge and sort at
    the vendor's Rare iLevel (290 at L25) instead of the authored Uncommon
    (270)."""
    book = idata.item("Book_Start")
    assert idata.upgrade_path(book, level=25, rarity="Rare")[-1] == 290
    assert idata.upgrade_path(book, level=25, rarity="Uncommon")[-1] == 270
    assert idata.upgrade_path(book, level=25)[-1] == 270  # authored by default





def test_upgrade_ladder_own_rarity_is_display_rarity():
    """The ladder's own-rarity column follows the DISPLAY rarity, so Guild
    Merchant gear (the starter weapons, sold as Rare by the town vendors)
    expands from its shop-quality column — RARE — instead of the authored
    green Uncommon starter column. Non-vendor gear and crafted gear are
    unchanged (display = authored rarity)."""
    lad = idata.upgrade_ladder(idata.item("Book_Start"), level=25)
    assert [r["rarity"] for r in lad] == ["Rare", "Epic", "Legendary"]
    lad = idata.upgrade_ladder(idata.item("Daggers_Start"), level=25)
    assert [r["rarity"] for r in lad] == ["Rare", "Epic", "Legendary"]
    # the town weapons already expand from Rare — unchanged
    assert [r["rarity"] for r in idata.upgrade_ladder(
        idata.item("Sword_Craft"), level=25)] == ["Rare", "Epic", "Legendary"]
    # non-vendor gear keeps its authored rarity (display = authored)
    assert [r["rarity"] for r in idata.upgrade_ladder(
        idata.item("Chest_Z1U1_Fig"), level=25)] == ["Uncommon"]
    # crafted gear keeps its authored rarity too
    assert [r["rarity"] for r in idata.upgrade_ladder(
        idata.item("Feet_RBee_WizCle_Craft"), level=25)] == ["Rare"]


def test_upgrade_ladder_default_level_vendor_gear():
    """Default-level ladders for Guild Merchant gear open at the vendor's
    top level (L25) — the shop sale. Judgement also drops from the L19 boss
    Munster Chuck, but the vendor sells it at L25, so its default ladder
    (rarities, base values, material costs) is the L25 one — same as the
    explicit-level ladder. The Void Jerkin (a player-scale chest, not
    vendor gear) keeps its L25 default too."""
    jud = idata.item("GA_Craft")
    lad = idata.upgrade_ladder(jud)
    at25 = idata.upgrade_ladder(jud, level=25)
    assert [r["rarity"] for r in lad] == ["Rare", "Epic", "Legendary"]
    assert lad[0]["steps"][0]["count"] == at25[0]["steps"][0]["count"]
    assert {b["label"]: b["value"] for b in lad[0]["base"]} == \
        {s["label"]: s["value"] for s in
         idata.gear_stats(jud, level=25, rarity="Rare")["stats"]
         if s["value"]}
    # Glory is vendor gear too — same L25 default
    glory = idata.item("Sword_Craft")
    gl = idata.upgrade_ladder(glory)
    assert {b["label"]: b["value"] for b in gl[0]["base"]} == \
        {s["label"]: s["value"] for s in
         idata.stat_rows(idata.gear_stats(
             glory, level=25, rarity="Rare")["stats"])}
    # the Void Jerkin's default ladder is the L25 values (Armor 230, incl.
    # the single-class Assassin factor) — the chest scales to the player
    rift = idata.item("Chest_RDemon_Ass")
    lad = idata.upgrade_ladder(rift)
    assert {b["label"]: b["value"] for b in lad[0]["base"]}["Armor"] == 230


def test_vendor_rows_split_per_offer():
    """Vendor rows merge per OFFER, not just per name: same-name vendors
    with identical offers (Mira's three hubs — same price, no town level)
    collapse into one row; the Guild Merchants' per-town offers stay split
    so each town shows its own price and item level (Tyrna L20 / Lower
    Ramburg L25)."""
    # the town weapons: both Guild Merchant towns charge the same but sell
    # at different town levels, so they split into per-town rows with levels
    m = idata.merge_drops("Sword_Craft")
    gms = [r for r in m if r["kind"] == "npc"]
    assert len(gms) == 2
    by_loc = {r["locs"][0]: r for r in gms}
    assert set(by_loc) == {"Tyrna", "Lower Ramburg"}
    assert by_loc["Tyrna"]["hub_level"] == 20
    assert by_loc["Lower Ramburg"]["hub_level"] == 25
    assert all(r["cost"] and r["cost"][0]["amount"] == 2000 for r in gms)
    # the starter gear: the towns also charge differently (Tyrna 1000 /
    # Lower Ramburg 2000 Gold), so each row keeps its own price + level
    d = idata.merge_drops("Daggers_Start")
    gms = [r for r in d if r["kind"] == "npc"]
    assert len(gms) == 2
    by_loc = {r["locs"][0]: r for r in gms}
    assert by_loc["Tyrna"]["cost"][0]["amount"] == 1000
    assert by_loc["Lower Ramburg"]["cost"][0]["amount"] == 2000
    assert by_loc["Tyrna"]["hub_level"] == 20
    assert by_loc["Lower Ramburg"]["hub_level"] == 25
    # Mira's Demon chest: the three hub names merge (same price, no town
    # level) — unchanged
    m = idata.merge_drops("Chest_RDemon_Ass")
    miras = [r for r in m if r["kind"] == "npc"]
    assert len(miras) == 1
    assert set(miras[0]["locs"]) == {"Navelin", "Tyrna", "Lower Ramburg"}
    assert miras[0].get("hub_level") is None
    # non-vendor rows never carry hub levels either
    cloth = idata.merge_drops("Cloth_Z1")
    assert all(r.get("hub_level") is None for r in cloth)


def test_gear_tables_default_to_fixed_level():
    """Both stat tables — the BY STEP upgrade ladder and the BY RARITY
    tiers — default to the level the piece is actually found at when the
    caller passes none, so they agree everywhere: crafted gear uses its
    FIXED level, and WorldLoot zone gear (a mob drop, NOT crafted) uses
    its authored zone-tier level — the Reinforced Hauberk's tables show
    its level-2 values (Armor 49), not the max-level ones."""
    it = idata.item("Chest_Z1U1_Fig")
    fixed = idata.item_fixed_level(it)
    assert fixed is None                 # zone gear: WorldLoot mob drop
    assert idata.item_source_max_level("Chest_Z1U1_Fig") == 2
    # no level passed -> the zone-tier level is used, not max level 25
    lad = idata.upgrade_ladder(it)
    tiers = idata.gear_rarity_tiers(it)
    assert lad is not None and tiers is not None
    lad_by = {b["label"]: b["value"] for b in lad[0]["base"]}
    tiers_by = {s["label"]: s["value"] for s in tiers[0]["stats"]}
    assert lad_by["Armor"] == 111 == tiers_by["Armor"]
    assert lad_by == tiers_by
    # explicit levels still win (the slider path)
    lad25 = idata.upgrade_ladder(it, level=25)
    assert {b["label"]: b["value"] for b in lad25[0]["base"]}["Armor"] == 344
    # shop-sold gear has no fixed level and no source cap — the town
    # weapons (sold up to L25 in Lower Ramburg) default to the global max
    glory = idata.item("Sword_Craft")
    assert idata.item_fixed_level(glory) is None
    assert idata.item_source_max_level("Sword_Craft") is None
    assert {b["label"]: b["value"] for b in
            idata.upgrade_ladder(glory)[0]["base"]} == \
        {b["label"]: b["value"] for b in
         idata.upgrade_ladder(glory, level=25)[0]["base"]}
    # Mira's Rift chest scales to the PLAYER: its default ladder is the
    # max-level values (Armor 230, not the L21 105)
    rift = idata.item("Chest_RDemon_Ass")
    assert idata.item_fixed_level(rift) is None
    assert idata.item_source_max_level("Chest_RDemon_Ass") is None
    assert {b["label"]: b["value"] for b in
            idata.upgrade_ladder(rift)[0]["base"]}["Armor"] == 230
    # a drop-only weapon that drops from a single boss defaults to the
    # boss's level — Sword_Swarm only drops from Lady Bee (Mokshi, L13)
    drop = idata.item("Sword_Swarm")
    assert idata.item_source_max_level("Sword_Swarm") == 13
    assert {b["label"]: b["value"] for b in
            idata.upgrade_ladder(drop)[0]["base"]} == \
        {b["label"]: b["value"] for b in
         idata.upgrade_ladder(drop, level=13)[0]["base"]}


def test_gear_ladder_at_default_level():
    """Gear stats default to the level the piece is actually found at: a
    crafted piece's FIXED craft level (Beekeeper's Scarf L10) or a
    WorldLoot zone drop's authored zone-tier level (the Reinforced Hauberk
    L2 — zone gear is a mob drop, NOT crafted). Both keep the same upgrade
    ladder shape as weapons (own-rarity column with per-rank gains +
    material costs)."""
    # crafted: the ladder sits at the fixed craft level
    scarf = idata.item("Back_RBee_FigWiz_Craft")
    assert idata.item_fixed_level(scarf) == 10
    lad10 = idata.upgrade_ladder(scarf, level=10)
    assert [r["rarity"] for r in lad10] == ["Rare"]
    assert [r["upgrades"] for r in lad10] == [3]
    assert all(s["gains"] for s in lad10[0]["steps"])
    # WorldLoot zone gear (not crafted): defaults to its zone-tier level
    it = idata.item("Chest_Z1U1_Fig")
    assert idata.item_fixed_level(it) is None
    assert idata.item_source_max_level("Chest_Z1U1_Fig") == 2
    lad = idata.upgrade_ladder(it)
    assert [r["rarity"] for r in lad] == ["Uncommon"]
    assert [r["upgrades"] for r in lad] == [2]
    by = {b["label"]: b["value"] for b in lad[0]["base"]}
    assert by["Armor"] == 111                 # at L2, not 344 at L25
    assert by["Strength"] == 2
    # every step has real gains + the Uncommon material (Spark Dust)
    assert len(lad[0]["steps"]) == 2
    assert all(s["gains"] for s in lad[0]["steps"])
    assert lad[0]["material"] == "Spark Dust"
    assert all(s["count"] > 0 for s in lad[0]["steps"])
    # the default-level base equals gear_stats at that level
    own = {s["label"]: s["value"]
           for s in idata.gear_stats(it, level=2)["stats"]}
    assert by == {k: v for k, v in own.items() if v}
