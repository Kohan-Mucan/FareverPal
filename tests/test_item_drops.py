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
    # Manfish pool = 28 (the R-set + its signature weapons) — the scan now
    # carries the per-camp crate rows natively: every faction camp's crate
    # rolls WorldLoot at 0.35, so the piece is 0.35/28 per crate
    for crate in ("Manfish Crates (x5)", "Kobold Crates (x5)",
                  "Bee Hive Crates (x10)", "Crimson Crates (x23)"):
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
    assert "World Crates (x79)" not in by_src
    assert "Demon Crates" not in str(list(by_src))
    # merged rows collapse each faction's mob zones into one row per source
    merged = idata.merge_drops("Shoulders_RManfish_FigAss")
    by_src = {r["source"]: r for r in merged}
    assert len({r["source"] for r in merged}) == len(merged)
    assert by_src["Manfish mobs"]["locs"]
    assert by_src["Manfish Crates (x5)"]["prob"] == pytest.approx(0.35 / 28)


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
        crate = by_src["Manfish Crates (x5)"]
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
    assert by_src["World Crates (x79)"]["kind"] == "chest"
    assert by_src["World Crates (x79)"]["prob"] == pytest.approx(0.35 / 126)
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
    assert by_src["World Crates (x79)"]["prob"] == pytest.approx(0.35 / 166)
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
    # the untagged attribute rings are the family's unsourced BASE row: the
    # card says so and names the obtainable variants from the sheet, rather
    # than rendering blank
    ring = idata.no_source_reason("Finger_Cri")
    assert "base row" in ring and "Ringlet of Precision" in ring, ring
    assert "Circle of Precision" in ring and "Set Eye" in ring, ring
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
    * a piece that lives in a Heroic box names the box it comes out of, and
      the rings' unsourced base rows name the family's zone/recipe variants
    * everything else (mounts/gliders without a concrete source, non-gear
      materials/packages/currencies) gets the plain 'No drop sources found.'
      readout — no invented generator/roll text
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
    assert by["Reblochonk"]["dungeon"] == "Mine Estrone"
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
        "World Crates (x79)",
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
    # the newest scan dropped Nightgod from Maat's signature table's
    # rollers — the weapon keeps just Maat
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
    # a weapon keeps just its own bosses (its signature table rolls nothing
    # else, so no family expansion) — the newest scan dropped Nightgod from
    # Maat's signature rollers (one boss, one signature set)
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
    # Corrupted Gift drops from the soulstone demon bosses. The compiled
    # poi_locs carries the 8 sub_kind='soulstone' summon spots again, so the
    # demon bosses resolve to their summon-spot zone instead of staying
    # loc-less (test_ui_utils pins the same row from the shim-only path).
    rows = idata.resolve_drops("DemonGearUpgradeRare_CritToAP")
    by_src = {r["source"]: r["loc"] for r in rows}
    assert by_src["Ariana Grandemon"] == "West Majoram Bridge"
    # dungeon mob -> its dungeon + entrance zone
    merged = {r["source"]: r for r in idata.merge_drops("Cloth_Z1")}
    # the refreshed scan emits the dungeon-joined form ('Abandoned Mines ·
    # Aurock Mound') for the Overseer's spot
    assert merged["Gorgon's Hollow Overseer"]["locs"] == \
        ["Abandoned Mines · Aurock Mound"]
    # the scan emits the bare zone AND the dungeon-joined form for the same
    # spot; the merge dedupes the tail-suffix copy
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
    assert by["Epic"]["base"] == 300
    assert by["Legendary"]["base"] == 320
    # max = base + 10 x live upgrade cap (Rare +3 / Epic +4 /
    # Legendary +5; Uncommon cannot upgrade)
    assert by["Uncommon"]["max"] == 250
    assert by["Uncommon"]["upgrades"] == 0
    assert by["Rare"]["max"] == 260 + 10 * 3
    assert by["Rare"]["upgrades"] == 3
    assert by["Epic"]["max"] == 300 + 10 * 4
    assert by["Epic"]["upgrades"] == 4
    assert by["Legendary"]["max"] == 320 + 10 * 5
    assert by["Legendary"]["upgrades"] == 5


def test_upgrade_path_shows_every_step_for_own_rarity():
    # Uncommon cannot upgrade live (cap 0): the path is the base iLevel
    # alone — Rare/Epic/Legendary climb +3/+4/+5
    sword = idata.item("Sword_Start")          # Uncommon
    path = idata.upgrade_path(sword)
    assert path is not None
    sc = idata.gear_scaling()
    base = 10 * sc["max_level"] + sc["rarity_ilevel"]["Uncommon"]
    assert path == [base]
    assert path == [250]
    assert idata.upgrade_path(sword, rarity="Rare") == [260, 270, 280, 290]
    assert idata.upgrade_path(sword, rarity="Epic") == [
        300, 310, 320, 330, 340]


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
    sword = idata.item("Sword_Start")     # Uncommon, cap 0 -> no cost rows
    assert idata.upgrade_costs(sword) == []
    # Rare, cap 3: at level 25 the MEASURED costs answer, not the sheet
    # formula - read off the live upgrade UI on 2026-09-26, and 3-5x the
    # published `base[rank] x level^exp`, which is wrong. Full evidence and
    # the sheet-fallback guard live in tests/test_upgrade_costs_measured.py.
    rare = idata.upgrade_costs({"type": "Sword", "rarity": "Rare"})
    assert [x["count"] for x in rare] == [53, 112, 176]
    # ...and a step charges SEVERAL materials, which the sheet's
    # one-material-per-rarity model could not represent at all
    assert [c["material"] for c in rare[1]["costs"]] == [
        "Spark Dust", "Spark Shard"]
    # Epic, cap 4. +2 was only ever measured at level 12, so at 25 it is
    # DERIVED from Rare x the rarity multiplier - labelled, not passed off
    # as measured.
    epic = idata.upgrade_costs({"type": "Sword", "rarity": "Epic"})
    assert [x["count"] for x in epic] == [64, 136, 214, 300]
    assert [x["source"] for x in epic] == [
        "measured", "derived", "measured", "measured"]
    # Legendary reuses the Epic crystal row, cap 5; all measured at 25
    leg = idata.upgrade_costs({"type": "Sword", "rarity": "Legendary"})
    assert [x["count"] for x in leg] == [71, 150, 236, 330, 434]
    # Common has no upgrade row at all -> None, which is NOT the same answer
    # as the cap-0 [] above
    assert idata.upgrade_costs({"type": "Sword", "rarity": "Common"}) is None


def test_upgrade_costs_fall_back_to_the_sheet_where_unmeasured():
    # Past the measurements the measured curve is EXTRAPOLATED (log-linear
    # through the two highest anchors, marked as such - see
    # tests/test_upgrade_costs_measured.py), and steps the anchor rarity never
    # had derive from the rarity's OWN measured row grown by the same curve.
    # The sheet fallback (`base[rank-1] x level^exp`, 3-5x off where checked)
    # now only answers for a step with NO measured row anywhere: Legendary +5
    # would be it, but Epic's list stops at its cap of 4 — so check the cap
    # boundary through upgrade_step_cost directly (see the measured tests).
    # What upgrade_costs shows for Epic at 30: every live step measured-curve,
    # and only cap-4 keeps +5 off the list entirely.
    far = idata.upgrade_costs({"type": "Sword", "rarity": "Epic"}, level=30)
    assert [x["source"] for x in far] == [
        "derived", "derived", "extrapolated", "extrapolated"]
    assert [x["count"] for x in far] == [79, 167, 257, 361]


def test_upgrade_materials_table():
    # the Enchants Upgrades tab's matrix columns: one row per upgradable
    # rarity (Uncommon cannot upgrade) with its material, live cap and
    # sheet costs — Legendary reuses the Epic crystal row
    mats = idata.upgrade_materials()
    assert [(m["rarity"], m["item"], m["cap"]) for m in mats] == [
        ("Rare", "UpgradeRare", 3),
        ("Epic", "UpgradeEpic", 4),
        ("Legendary", "UpgradeEpic", 5)]
    assert [m["name"] for m in mats] == [
        "Spark Shard", "Spark Crystal", "Spark Crystal"]
    assert next(m for m in mats
                if m["rarity"] == "Epic")["base"] == [0, 0, 0, 3, 15]


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
    # Epic climbs one further (+4)
    epic = {"id": "SynthEpicSword", "type": "Sword", "rarity": "Epic",
            "classes": ["Fighter"], "faction": "World"}
    egains = idata.upgrade_gains(epic, level=25)
    assert egains is not None
    assert [g["upgrade"] for g in egains] == [1, 2, 3, 4]
    # Uncommon cannot upgrade live (cap 0): the starter weapons (Guild
    # Merchant shop gear, authored Uncommon) have no per-rank gains
    assert idata.upgrade_gains(idata.item("Sword_Start"), level=25) is None
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
    # Merchants, so the columns start at Rare +3, not the authored Uncommon
    # (which cannot upgrade live)
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
    # Rare's MEASURED level-25 costs answer here, not the sheet's base array:
    # the live game charges 53 / 112 / 176 Spark Dust for +1 / +2 / +3.
    counts = [s["count"] for s in lad[0]["steps"]]
    assert counts == [53, 112, 176]
    # `material` stays the PRIMARY (first) material for existing callers, so
    # the ladder keeps reporting the sheet's shard row even though the step
    # really charges dust AND shard - `costs` carries the full list.
    assert lad[0]["material"] == "Spark Shard"
    assert [c["material"] for c in lad[0]["steps"][1]["costs"]] == [
        "Spark Dust", "Spark Shard"]
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
    # still charges 3x Spark Crystal, which is the one material the shipped
    # sheet got RIGHT (measured exactly at every level and step)
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
        # +4 is 300 Spark Dust + 75 Shard + 3 Crystal measured at level 25;
        # `count` is the PRIMARY material, so the crystal now shows in
        # `costs` and a regression check on the 3 has to read it there.
        assert cld[1]["steps"][3]["count"] == 300, iid
        crystal = [c for c in cld[1]["steps"][3]["costs"]
                   if c["material"] == "Spark Crystal"]
        assert crystal and crystal[0]["count"] == 3, iid
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
    assert idata.item_level(25, "Epic") == 300          # 250 + 50
    assert idata.item_level(25, "Legendary") == 320     # 250 + 70
    assert idata.item_level(10, "Common") == 100
    assert idata.item_level(25, "Rare", upgrades=9) == 290   # capped at 3
    assert idata.item_level(25, "Epic", upgrades=9) == 340   # capped at 4
    assert idata.item_level(25, "Uncommon", upgrades=9) == 250  # cap 0


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


def test_the_single_class_armor_boost_stays_on_sampled_slots():
    """The per-class armor multiplier is FITTED, not read: three in-game
    reports, all worn armor (Back, Head, Chest). So its scope is exactly the
    slots that were measured.

    This is the same bug as the Shield one, one notch down, and it is worth a
    test of its own because the first fix left it in place. The Shield was
    removed on "no calibration sample is an off-hand piece" — the same
    reasoning that removes a slot applies to the five OTHER unsampled slots
    that stayed in the set, and they were not inert: on Legs, Shoulders,
    Hands, Feet and Waist a single-class piece outranked a two-class piece
    purely on a multiplier measured on a chest, moving 10 of the 77 build
    slots (all Fighter, the class with the largest factor).

    The test is on the SET, not on a number: it pins which slots the factor
    may touch, so adding a slot to it later is a deliberate act that fails
    here, rather than a silent widening of a three-sample fit.
    """
    from farever_companion.data.items import stats as _stats

    # exactly the three sampled slots
    assert set(_stats._SINGLE_CLASS_ARMOR_TYPES) == {"Back", "Head", "Chest"}

    # and the factor really is inert outside them. Shield_Start is the piece
    # the ORIGINAL carve-out was about: 300 raw x 2.25 = 675, which is what
    # it read before, and 286 is the raw sheet math it reads now.
    shield = idata.item("Shield_Start")
    assert (shield.get("aptitudes") or shield.get("classes")) == ["Fighter"]
    sgs = idata.gear_stats(shield, level=25)
    sarmor = next(s for s in sgs["stats"] if s["key"] == "armor")["value"]
    assert sarmor == 286, (sarmor, "the boost reached a shield")

    # same on an unsampled worn slot: Legs_EKobold_Fig is Fighter-only and
    # reads 174 raw, not 174 x 2.25
    single = idata.item("Legs_EKobold_Fig")
    assert (single.get("aptitudes") or single.get("classes")) == ["Fighter"]
    gs = idata.gear_stats(single, level=25)
    armor = next(s for s in gs["stats"] if s["key"] == "armor")["value"]
    assert armor == 174, (armor, "the boost leaked to an unsampled slot")

    # a SAMPLED slot still gets it, or the restriction has over-corrected.
    # Crimson Wings is the piece the whole factor exists for: 45 raw x 2.25
    # = the 100 read in-game (pinned by test_single_class_armor_uses_live_factor)
    chest = idata.item("Back_RCrimson_Fig")
    cgs = idata.gear_stats(chest, level=25)
    carmor = next(s for s in cgs["stats"] if s["key"] == "armor")["value"]
    assert carmor == 100, (carmor, "a sampled slot no longer multiplies")


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


def test_the_off_hand_pick_follows_the_shields_kit_not_the_alphabet():
    """Armor cannot rank shields: the three Rare ones read an identical 564
    at their caps, and the two Cleric/Fighter shields even share a class
    list, so the off-hand used to fall through the tie-break to the item id
    and hand the Fighter the Cleric's shield. The kit decides instead —
    Dominion's Fortifying Cry buffs ALLIES' Armor (the tank's shield) and
    Crabgantua's Kneecap's Depth Shield HEALS allies (the healer's), so a
    Fighter takes Dominion and a Cleric takes the Kneecap, each the other's
    wrong answer.

    With no role passed the class fit is what applies, so this test is
    about the fallback; the role-first table is pinned in
    `test_the_role_chip_also_re_decides_the_off_hand_shield`.
    """
    fighter = idata.suggest_offhand_shield(class_filter="Fighter", level=25)
    cleric = idata.suggest_offhand_shield(class_filter="Cleric", level=25)
    assert fighter == "Shield_Craft"          # Dominion — party armor
    assert cleric == "Shield_OrbitWater"     # Kneecap — ally heal
    assert fighter != cleric
    # the kit is a RANKING term, not a stat: the armor is still identical
    assert (idata.item("Shield_Craft")["classes"]
            == idata.item("Shield_OrbitWater")["classes"] == ["Cleric", "Fighter"])
    # a class the table says nothing about keeps the plain armor ranking
    assert idata.suggest_offhand_shield(class_filter="Wizard",
                                        level=25) == "Shield_Firebreath"
    assert idata.suggest_offhand_shield(class_filter="Assassin",
                                        level=25) is None


def test_single_class_armor_boost_does_not_reach_shields():
    """The live per-class armor multiplier is fitted on WORN armor (Back /
    Head / Chest), so a Shield keeps the raw sheet math. A shield is
    armor-only, which made the boost the whole score: Shield_Start (the
    Fighter-only starter shield) read 300 x 2.25 = 676 armor against the
    two-class shields' 487 and the Ideal Catalog fill recommended the starter
    gear as best-in-slot. Raw sheet value, so BiS ranks on real armor.
    """
    start = idata.item("Shield_Start")
    assert start["classes"] == ["Fighter"]          # single class
    gs = idata.gear_stats(start, level=25, rarity="Rare")
    assert next(s for s in gs["stats"] if s["key"] == "armor")["value"] == 300
    # the two-class shields (no boost either way) sit ABOVE it now
    for iid in ("Shield_OrbitWater", "Shield_Craft", "Shield_Firebreath"):
        other = idata.gear_stats(idata.item(iid), level=25, rarity="Rare")
        assert next(s for s in other["stats"]
                    if s["key"] == "armor")["value"] > 300, iid
    assert idata.suggest_offhand_shield(class_filter="Fighter",
                                        level=25) == "Shield_Craft"
    # ...and the worn-armor factor is untouched on its own slots
    back = next(s for s in idata.gear_stats(idata.item("Back_RCrimson_Fig"),
                                            level=25)["stats"]
                if s["key"] == "armor")
    assert back["value"] == 100


def test_stat_text_modes_rounding_and_true():
    """stat_text renders per the display mode: 'rounding' (the DEFAULT — the
    clean game-tooltip integer) or 'true' (the exact computed float, 3
    decimals, trailing zeros stripped). Exact values and authored stats (no
    raw) always render plainly.

    The toggle cycles exactly those TWO forms, so the retired third one
    ('both', '4 (3.985)') is refused by the setter rather than latched: a
    mode the header button can't name must never be what renders."""
    assert idata.stat_display_mode() == "rounding"       # default is clean
    assert idata.stat_text(4, 3.9854) == "4"
    assert idata.stat_text(4, 3.9996) == "4"             # rounds up to 4
    idata.set_stat_display_mode("true")
    try:
        assert idata.stat_display_mode() == "true"
        assert idata.stat_text(4, 3.9854) == "3.985"
        assert idata.stat_text(64, 63.9) == "63.9"
        assert idata.stat_text(72, 72.214) == "72.214"
        assert idata.stat_text(5, None) == "5"           # authored stat
        assert "(" not in idata.stat_text(4, 3.9854)     # no parens form
    finally:
        idata.set_stat_display_mode("rounding")
    for mode in ("both", "bogus"):                      # both retired, ignored
        idata.set_stat_display_mode(mode)
        assert idata.stat_display_mode() == "rounding", mode
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
    piece in the table is dropped by boss source(s) only, and none is in a
    non-boss's table. Several bosses may share one signature set (Nightgod
    and Nightking Maat Demon both roll Maat's since the new scan)."""
    boss_tables = [t["id"] for t in idata.gear_tables() if t["group"] == "Boss"]
    assert len(boss_tables) >= 10
    for tbl in boss_tables:
        gear = [it for it in idata.items()
                if idata.drops_from_table(it["id"], tbl)]
        assert gear
        # every source of every piece is a boss (the set may span several)
        srcs = {r["source"] for it in gear for r in idata.resolve_drops(it["id"])
                if r["table"] == tbl}
        bosses = {r["source"] for it in gear
                  for r in idata.resolve_drops(it["id"])
                  if r["table"] == tbl and r["boss"]}
        assert srcs <= bosses, (tbl, srcs - bosses)


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
    (250, which cannot upgrade)."""
    book = idata.item("Book_Start")
    assert idata.upgrade_path(book, level=25, rarity="Rare")[-1] == 290
    assert idata.upgrade_path(book, level=25, rarity="Uncommon") == [250]
    assert idata.upgrade_path(book, level=25) == [250]  # authored by default
    assert idata.upgrade_path(book, level=25, rarity="Epic") == [
        300, 310, 320, 330, 340]





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
    gms = [r for r in m if r["kind"] == "npc" and r.get("cost")]
    assert len(gms) == 2
    by_loc = {r["locs"][0]: r for r in gms}
    assert set(by_loc) == {"Tyrna", "Lower Ramburg"}
    assert by_loc["Tyrna"]["hub_level"] == 20
    assert by_loc["Lower Ramburg"]["hub_level"] == 25
    assert all(r["cost"][0]["amount"] == 2000 for r in gms)
    # every Guild Merchant offer names its currency now (the drops index
    # carries the cost; the old scanned sheet authored none)
    assert all(r["cost"][0]["kind"] == "Gold" for r in gms)
    # the starter gear: the towns also charge differently (Tyrna 1000 /
    # Lower Ramburg 2000 Gold), so each row keeps its own price + level
    d = idata.merge_drops("Daggers_Start")
    gms = [r for r in d if r["kind"] == "npc" and r.get("cost")]
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
    L2 — zone gear is a mob drop, NOT crafted). The Rare scarf keeps its
    +3 ladder; Uncommon cannot upgrade live, so the Hauberk is a base-only
    single column with its material row."""
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
    assert [r["upgrades"] for r in lad] == [0]
    by = {b["label"]: b["value"] for b in lad[0]["base"]}
    assert by["Armor"] == 111                 # at L2, not 344 at L25
    assert by["Strength"] == 2
    # Uncommon cannot upgrade: no steps, but the material (Spark Dust)
    # is still named
    assert lad[0]["steps"] == []
    assert lad[0]["material"] == "Spark Dust"
    # the default-level base equals gear_stats at that level
    own = {s["label"]: s["value"]
           for s in idata.gear_stats(it, level=2)["stats"]}
    assert by == {k: v for k, v in own.items() if v}


def test_dungeon_epic_sets_are_attributed_to_their_hard_mode_bosses():
    """Each faction's Epic dungeon set (the `_E<Faction>` twins of the shared
    Rare set) is NOT in the shared pool: it rolls the bosses' HARD-MODE
    tables (`*_HM`) instead, read from the sheet because the live scan never
    captured that edge.

    Reading those tables attributes three `_HM` bosses x 6 pieces per family
    to a boss of that same family. Everything left over is a genuine gap and
    is expected to be: the six a family keeps in the shared Hero Gear Cache,
    Golcano's six Crimson pieces (a KOBOLD boss's table), and the Nightling's
    whole set (neither of its bosses has an `_HM` table). All of it is
    `epic_unknown`, which the gear list and the Collection view serve.
    """
    from farever_companion.data.items import sources

    groups = {g["faction"]: g for g in idata.dungeon_drop_groups()}
    assert set(groups) == {"Bee", "Kobold", "Manfish", "Crimson", "Demon"}

    for fac, g in groups.items():
        setids = set(sources._epic_set_ids(fac))
        shown = set(idata.epic_pieces(g))
        assert shown == setids, (fac, shown ^ setids)
        assert not (set(g["shared"]) & shown), fac   # the twins stay out
        assert all(idata.item_display_rarity(e) == "Epic" for e in shown), fac
        # No cross-family or cache block exists at all: a Heroic CACHE is not
        # a dungeon and nothing hands one out, and `Golcano_HM` is a KOBOLD
        # boss's table, so putting its Crimson six on the Crimson page linked
        # a Kobold dungeon to Crimson gear. Both are `epic_unknown` instead.
        assert "epic_cross" not in g and "epic_cache" not in g, fac
        # every piece sits in exactly one block: one boss of THIS family, or
        # nothing — never two, never a foreign dungeon
        per_boss = [set(b["epic"]) for b in g["bosses"] if b["epic"]]
        claimed = set().union(*per_boss) if per_boss else set()
        assert not (claimed & set(g["epic_unknown"])), fac
        assert claimed <= setids, fac
        for i, s in enumerate(per_boss):
            for t in per_boss[i + 1:]:
                assert not (s & t), (fac, s & t)
        # the unclaimed remainder is a real gap, not an empty claim: every
        # family keeps some pieces that no dungeon of its own hands out
        assert g["epic_unknown"], fac

    # the two bosses the scan never resolved now carry their sets: Cleodora
    # (Bee), MunsterChuck (Kobold), SpongeBlob (Manfish)
    for fac, bid in (("Bee", "Cleodora"), ("Kobold", "MunsterChuck"),
                     ("Manfish", "SpongeBlob")):
        by_boss = {b["boss_id"]: b["epic"] for b in groups[fac]["bosses"]}
        assert len(by_boss[bid]) == 6, (fac, bid)
    # and each of those three families' own sets are now three-way complete
    for fac in ("Bee", "Kobold", "Manfish"):
        n = sum(len(b["epic"]) for b in groups[fac]["bosses"])
        assert n == 18, fac

    # The Hero Gear Cache holds six of each of those families' sets, and
    # nothing in the game data hands that box out — so those six are the
    # unclaimed remainder on every one of them, not a block on the page.
    hero = {r["item"] for r in
            sources.heroic_cache_contents()["HM_Gear_Cache_25"]}
    for fac in ("Bee", "Kobold", "Manfish", "Crimson"):
        mine = {i for i in hero if i in sources._epic_set_ids(fac)}
        assert len(mine) == 6, fac
        assert mine <= set(groups[fac]["epic_unknown"]), fac

    # The Epic TRINKET, and the name collision that comes with it. Four
    # families' trinkets ride their boss's `_HM` table AND already sit in the
    # SHARED pool next to the Rare twin, so crediting them to a boss block too
    # would draw the same item id twice in one pane. They stay in the shared
    # grid. The Nightling's is in the Chaotic Gear Cache and is in no block —
    # a cache is not a dungeon — which is where it was before any of this.
    for fac, bid in (("Bee", "Cleodora"), ("Kobold", "Ratsar"),
                     ("Manfish", "Crabgantua"), ("Crimson", "RobinHoof")):
        trink = f"Trinket_{fac}_E"
        assert [i for i in sources._heroic_tables()[f"{bid}_HM"]
                if i == trink], (fac, bid)          # the table does roll it
        assert trink in groups[fac]["shared"], fac  # already in the shared grid
        assert trink not in idata.epic_pieces(groups[fac]), fac
    dem_trink = "Trinket_Demon_E"
    assert dem_trink not in groups["Demon"]["shared"]
    assert dem_trink in {r["item"] for r in
                         sources.heroic_cache_contents()["HM_Demon_Cache_25"]}
    assert dem_trink not in idata.epic_pieces(groups["Demon"])
    # and no family draws any item id twice
    for fac, g in groups.items():
        drawn = list(g["shared"]) + list(idata.epic_pieces(g))
        assert len(drawn) == len(set(drawn)), fac

    # the two bosses per faction the scan had hard-mode tables for still
    # resolve the same way, now alongside the ones it never captured
    for fac, ids in (("Manfish", ("Crabgantua", "Nepsilon")),
                     ("Kobold", ("Ratsar", "Reblochonk")),
                     ("Bee", ("Gatsbee", "Mokshi"))):
        g = groups[fac]
        by_boss = {b["boss_id"]: b["epic"] for b in g["bosses"]}
        assert all(len(by_boss[i]) == 6 for i in ids), fac
        for bid in ids:
            for iid in by_boss[bid]:
                rows = idata.resolve_drops(iid)
                assert any(r.get("source_id") == bid
                           and (r.get("table") or "").endswith("_HM")
                           for r in rows), iid

    # Golcano is a KOBOLD boss whose `Golcano_HM` rolls six CRIMSON pieces.
    # Crediting them to Kobold would put Crimson gear under a Kobold boss, and
    # the old fix — a cross-family block on the Crimson page — was no better:
    # a Kobold dungeon is not a Crimson one, so either way the six claim a
    # location they do not have. They stay in Crimson's unclaimed remainder,
    # and Kobold keeps exactly its three `_HM` bosses.
    gol = [i for i in sources._heroic_tables()["Golcano_HM"]
           if "_ECrimson_" in i]
    assert len(gol) == 6
    assert all(i in groups["Crimson"]["epic_unknown"] for i in gol), gol
    kobold_ids = {i for b in groups["Kobold"]["bosses"] for i in b["epic"]}
    assert not (kobold_ids & set(gol))
    assert not any(b["boss_id"] == "Golcano" and b["epic"]
                   for b in groups["Kobold"]["bosses"])

    # the Nightling: neither boss has an `_HM` table, so its whole 24-piece
    # set is unclaimed here and served by the gear list. The box holds 25 —
    # its Epic trinket rides along but is not a set piece — and nothing here
    # may claim any of them.
    dem = groups["Demon"]
    assert not any(b["epic"] for b in dem["bosses"])
    assert set(dem["epic_unknown"]) == set(sources._epic_set_ids("Demon"))
    assert len(sources.heroic_cache_contents()["HM_Demon_Cache_25"]) == 25
    assert "epic_cache" not in dem and "epic_cross" not in dem


def test_compiled_heroic_set_pieces_carry_their_hard_mode_boss():
    """Every `<boss>_HM` set piece the scan left with NO drop row must carry
    its heroic boss in the COMPILED index.

    The live scan recorded drop rolls but never the boss -> `<boss>_HM` edge,
    so an Epic `_E<Faction>` piece a heroic boss guarantees had no rows and
    its card read 'No drop sources found.' The compiler stamps the edge from
    the same lootTable sheet the Dungeons page reads
    (`compiler._stamp_heroic_boss_edges` / `rules.heroic_boss_items`), so a
    consumer that never touches the item card still sees who drops the piece.
    """
    import json

    from farever_companion import paths
    from farever_companion.data.items import sources

    tables = sources._heroic_tables()
    compiled = sources._data().get("items", {})

    # the compiled index resolves every piece a heroic table rolls
    for tid, ids in tables.items():
        for iid in ids:
            assert idata.shown_drops(iid), iid
            assert (compiled.get(iid) or {}).get("drops"), iid

    # and it names THAT boss on every piece the SCAN left blank
    try:
        scan = json.loads(paths.item_drops_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return                      # loose scan sheet unavailable: skip half
    srcs = sources._sources()
    stamped = 0
    for tid, ids in tables.items():
        bid = tid[:-3]
        for iid in ids:
            if (scan.get("items", {}).get(iid) or {}).get("drops"):
                continue            # the scan recorded its own rows: untouched
            rows = (compiled.get(iid) or {}).get("drops") or []
            assert any(0 <= r["s"] < len(srcs)
                       and (srcs[r["s"]].get("id") or "") == bid
                       for r in rows), (iid, bid, rows)
            stamped += 1
    # the gap is real on this data — the assertion above is not vacuous
    assert stamped, "no heroic piece had a missing row — check is vacuous"


# --- the "ships with no acquisition path" guard -----------------------------

# The data layer's catch-all line: what an item with no drop rows and no
# named mechanic behind them renders on its card.
_GENERIC_NO_SOURCE = "No drop sources found."

# Items allowed to fall back to that bare line. EMPTY ON PURPOSE: every
# shipped item now either resolves a source, names its own mechanic, or
# belongs to a family the guard exempts below — including the pieces that
# only ever come out of a Heroic cache (they name the cache) and the rings'
# unsourced base rows (they name the family's real variants). The set stays
# so a gap can be parked here WITH a reason while it is being fixed; adding
# an id is a deliberate, reviewable act, and the guard still fails the
# moment the sheet ships an item that would otherwise render blank.
_SOURCELESS_GAPS: set[str] = set()

# Types that are never dropped individually: currencies, tokens, packages,
# emotes, tools, crafting components, and the mount/glider family (whose
# loot tables roll one family representative — see no_source_reason).
_NEVER_DROPPED_TYPES = {
    "Mount", "GearGlider", "Emote", "CraftingComponent", "Package",
    "CompletedPackage", "Misc", "Currency", "Usable", "Consumable",
    "HealthPotion",
}


def test_no_item_ships_with_neither_a_source_nor_a_named_reason():
    """A shipped item must never render blank. An item with no drop rows has
    to name its mechanic (crafted, starting gear, a cache's contents, a loot
    table the scan can't anchor, ...) or be one of the never-dropped tokens.
    The Heroic `_E<Faction>_` set pieces used to be exempted here — their
    boss edge the scan never captured left them with no rows — but the
    compiler now stamps that edge from the `<boss>_HM` table (see
    test_compiled_heroic_set_pieces_carry_their_hard_mode_boss), and the
    ones in an unreleased cache name the cache, so nothing is exempt any
    more.

    Whatever is left over shows the bare 'No drop sources found.' line,
    which is what a newly shipped, unwired item looks like — the way the
    three Nightling InfusionPatterns first appeared. Nothing is parked in
    the allowlist today: it is empty on purpose. This FAILS when such an
    item joins the sheet, and when a parked gap closes."""
    from farever_companion.data.items import sources
    gaps = set()
    for it in idata.items():
        iid = it["id"]
        if idata.has_drops(iid):
            continue
        if sources.no_source_reason(iid) != _GENERIC_NO_SOURCE:
            continue                       # a named mechanic: not blank
        itype = str(it.get("type") or "")
        if itype in _NEVER_DROPPED_TYPES or itype.startswith("Tool"):
            continue
        if iid.startswith("TODO_"):
            continue                       # the sheet's own stubs
        gaps.add(iid)
    assert gaps == _SOURCELESS_GAPS, (
        "item(s) shipped with no acquisition path and no named reason, so "
        "their card renders the bare 'No drop sources found.' line — wire "
        "the source into the scan + Update_Raw_Data.bat, or name the "
        "mechanic via sources.no_source_reason / _LOOT_LISTED_UNANCHORED, "
        "then fold the id into _SOURCELESS_GAPS:\n"
        f"  unexpected: {sorted(gaps - _SOURCELESS_GAPS)}\n"
        f"  no longer a gap (drop it from _SOURCELESS_GAPS): "
        f"{sorted(_SOURCELESS_GAPS - gaps)}")


def test_every_infusion_pattern_names_its_crucible_mechanic():
    """The class of item this guard exists for. The InfusionPatterns ship
    ahead of their heroic tables, so a wired boss is not guaranteed — the
    three Nightling patterns still have none (see
    test_heroic_infusion_keys_resolve_to_boss_units). What IS guaranteed is
    that their card says what they are and where they are applied, instead
    of leaving the Drops From slot blank."""
    from farever_companion.data.items import sources
    patterns = [it["id"] for it in idata.items()
                if it.get("type") == "InfusionPattern"]
    assert len(patterns) == 15, patterns
    for iid in patterns:
        note = sources.no_source_reason(iid)
        assert note != _GENERIC_NO_SOURCE, iid
        assert "crucible" in note, (iid, note)


def test_rows_the_sheet_left_unnamed_keep_their_id():
    """The names are the sheets' own, verbatim. A row whose name IS its id
    ('Back_RBee_Wiz' and its Epic twin, which the test-server sheets never
    named) shows that id: the companion does not synthesize a name from the
    id's parts, because a made-up name reads like real data and hides the
    fact that the name is missing. Real names are never rewritten either."""
    from farever_companion.data.items import catalog

    for iid in ("Back_RBee_Wiz", "Back_EBee_Wiz"):
        it = idata.item(iid)
        assert it["name"] == iid
        assert it["name"] == catalog._data()["items"][iid].get("name")
    assert not hasattr(catalog, "readable_name")     # no name factory

    # the rows around them keep their real names, untouched
    assert idata.item("Back_RBee_AssCle")["name"] == "Flight of the Rumblebee"
    assert idata.item("Back_RBee_FigWiz_Craft")["name"] == "Beekeeper's Scarf"
    assert idata.item("Head_EBee_FigWiz")["name"] == "Royal Chamber Helmet"

    # every item's display name is exactly the sheet's, for the whole catalog
    raw = catalog._data()["items"]
    mismatched = [it["id"] for it in idata.items()
                  if (it.get("name") or "") != (raw[it["id"]].get("name") or "")]
    assert mismatched == []


def test_unnamed_row_audit_reports_the_sheets_gaps():
    """`build_tools/check_unnamed_items.py` is what makes the sheets' missing
    names visible after a re-scan, so it has to be honest about which rows are
    gaps: every row it collects really is name == id in the compiled payload,
    placeholder ids ('Back_RBee_Wiz') are flagged while plain-word ids ('Gold',
    'Agate') are only counted, and only gear can fail a build step."""
    from build_tools import check_unnamed_items as audit
    from farever_companion.data.items import catalog

    raw = catalog._data()["items"]
    groups = audit.collect()
    collected = {i for ids in groups.values() for i in ids}
    assert collected                       # the sheets do still have gaps
    for iid in collected:                  # and nothing else gets collected
        assert (raw[iid].get("name") or "").strip() in ("", iid), iid

    # the report flags placeholders, keeps word ids apart, and stars gear
    fake = {"Back": ["Back_RBee_Wiz"],
            "CraftingComponent": ["Agate"],
            "Recipe": ["Recipe_Cook_1"]}
    text = audit.report(fake)
    flagged = text.split("Single-word")[0]
    assert "Placeholder ids" in text
    assert "Back_RBee_Wiz" in flagged and "Recipe_Cook_1" in flagged
    assert "Agate" not in flagged          # a real name, only counted
    assert "Single-word ids — 1 row(s)" in text
    assert "gear — shows in the Items pages" in flagged

    # reporting never fails a build; --fail-on-gear is the opt-in gate
    assert audit.main([]) == 0
    assert audit.main(["--fail-on-gear"]) == (1 if audit.gear_ids(groups) else 0)


def test_weapon_upgrade_passive_ladder():
    """The gear-upgrade system's "Weapon Upgraded" passives: no item row
    references them (the engine grants them), so the `<Type>_Upgrade` skill
    is the only place the per-rank bonus lives. The axes grant Critical
    Chance +1..+5% at ranks 1..5 — Cheese Moon (Axe_Boomerang) is one — and
    rarity only caps how many ranks exist (Rare 3 / Epic 4 / Legendary 5),
    which is what the card's ⚡ trait line spells out."""
    pas = idata.weapon_upgrade_passive("Axe_Boomerang")
    assert pas["id"] == "Axe_Upgrade"
    assert pas["name"] == "Weapon Upgraded"
    assert pas["attr"] == "CritChance"
    assert pas["values"] == {1: 1, 2: 2, 3: 3, 4: 4, 5: 5}
    assert "Critical Chance" in pas["description"]

    # each value is keyed to exactly ONE rank, so the ladder's own per-rarity
    # upgrade counts are what caps the bonus: a Rare axe stops at +3%, an
    # Epic one reaches +4% and only a Legendary reaches +5%
    ladder = idata.upgrade_ladder(idata.item("Axe_Boomerang"), level=25)
    assert {c["rarity"]: c["upgrades"] for c in ladder} == {
        "Rare": 3, "Epic": 4, "Legendary": 5}
    assert max(pas["values"]) == 5          # the top rank the bonus defines

    # the other numeric passives carry their own attribute, same mechanism
    fists = idata.weapon_upgrade_passive("Fists_LightMonk")
    assert (fists["id"], fists["attr"]) == ("Fists_Upgrade", "PhysicalMastery")
    assert fists["values"] == {1: 4, 2: 5, 3: 6, 4: 7, 5: 8}

    # the other 12 weapon types state the same ladder the OTHER way: no
    # affix, a script effect whose number sits in `vars` and grows through
    # props.rankOverride (minRank -> vars, cumulative). Both paths must
    # resolve — a bow is not a weapon without an upgrade passive
    bow = idata.weapon_upgrade_passive("Bow_BigGame")
    assert (bow["id"], bow["kind"], bow["attr"]) == ("Bow_Upgrade",
                                                       "effect", "")
    assert bow["values"] == {}
    assert bow["per_rank"] == {r: f"{2 * (r + 3)}%" for r in range(1, 6)}
    assert bow["max_rank"] == 5
    mace = idata.weapon_upgrade_passive("Mace_Benediction")
    assert mace["kind"] == "effect"
    assert mace["per_rank"] == {1: "4%", 2: "5%", 3: "6%", 4: "7%", 5: "8%"}
    shield = idata.weapon_upgrade_passive("Shield_Start")
    assert shield["per_rank"] == {1: "5%", 2: "7%", 3: "9%",
                                  4: "11%", 5: "13%"}

    # armor / jewelry / non-gear never have one at all
    assert idata.weapon_upgrade_passive("Chest_RDemon_Ass") is None
    assert idata.weapon_upgrade_passive("CopperIngot") is None


def test_weapon_upgrade_trait_line_and_skill_flag():
    """The item card's ⚡ block gets the "Weapon Upgraded" line: that passive
    has NO SkillBar tile (the engine grants it per upgrade rank, the item
    never authors it as a skill), so the card is the only place it can be
    documented — and the line spells its ladder out, naming the attribute the
    item's own weapon type grants."""
    tr = idata.weapon_upgrade_trait("Axe_Boomerang")
    assert tr["title"] == "Weapon Upgraded" and tr["type"] == "Passive"
    assert tr["skill"] == "Axe_Upgrade"
    # ONE VALUE PER RARITY, each labelled with the rarity it belongs to: the
    # sheet defines ranks 1–5, but a piece's rarity is what buys them, and the
    # engine hands the granted skill a rank shifted by that rarity (see
    # granted_rank) — so a Rare axe's ceiling is +2%, not the ladder's +
    assert tr["text"] == ("Weapon Upgraded — Critical Chance: Rare +2%"
                          " · Epic +3% · Legendary +4%.")
    # ...and each rarity bit is tagged so the card can colour it like that
    # rarity's ladder column; the parts must join back to the plain sentence
    assert [r for _, r in tr["parts"] if r] == ["Rare", "Epic", "Legendary"]
    assert "".join(t for t, _ in tr["parts"]) == tr["text"]
    assert [t for t, r in tr["parts"] if r] == ["Rare +2%", "Epic +3%",
                                                 "Legendary +4%"]
    # a fist weapon names its own attribute, same mechanism
    fists = idata.weapon_upgrade_trait("Fists_LightMonk")
    assert "Physical Mastery: Rare +5% · Epic +6% · Legendary +7%" \
        in fists["text"]
    # the vars-only types get their own shape: the sheet's sentence carrying the
    # value THIS piece is granted, labelled with its rarity, then EVERY
    # reachable rarity as a labelled bit, in rarity order — its own first
    bow = idata.weapon_upgrade_trait("Bow_BigGame")
    assert bow["skill"] == "Bow_Upgrade"
    # the prose is the GAME's own sentence for this piece, verbatim and
    # UNLABELLED: the value granted at the piece's own rarity (10% at Rare),
    # left exactly where the sheet put the number. The run then repeats it as
    # the piece's own bit, in order beside the higher rarities — so the value is
    # named twice but its rarity only once, and only ever as a colour
    assert bow["text"] == (
        "Weapon Upgraded — Damage dealt by your Basic Attacks to enemies at"
        " 20m minimum increased by 10% — Rare 10% · Epic 12% ·"
        " Legendary 14%.")
    assert [r for _, r in bow["parts"] if r] == ["Rare", "Epic",
                                                 "Legendary"]
    assert [t for t, r in bow["parts"] if r] == ["Rare 10%",
                                                 "Epic 12%", "Legendary 14%"]
    assert "".join(t for t, _ in bow["parts"]) == bow["text"]
    assert bow["text"].count("10%") == 2    # inline, then the run's own bit
    assert "by 8%" not in bow["text"]         # never the unreachable base
    # the run is ordered, and the piece's own rarity leads it
    at = {t: bow["text"].index(t)
          for t in ("Rare 10%", "Epic 12%", "Legendary 14%")}
    assert at["Rare 10%"] < at["Epic 12%"] < at["Legendary 14%"]


def test_effect_sentence_is_the_games_own_tooltip_text():
    """The effect path's prose is not a paraphrase of the game's: it IS the
    game's own sentence for the same weapon, at the rank that piece is granted.

    Settled 2026-09-15 by comparing the candidate shapes against that text for
    every effect type — resolving the sheet's desc template at the granted rank
    reproduces the app's sentence VERBATIM (12/12). A `(Rare)` label inserted a
    word the game never prints inside any of these sentences (the game's own
    strings carry no rarity token at all) and the earlier `X%` placeholder hid a
    number the game always states, so the value stays unlabelled and rarity
    reaches the reader by COLOUR alone, here as in the game. Reading the
    sentence at the ladder's rank 1 would break this too: that base value is
    granted to no rarity.
    """
    from farever_companion.data import cdb
    from farever_companion.data import skills as sk
    from farever_companion.data.items import stats as st

    types: set[str] = set()
    for item_id in cdb.by_id("item"):
        pas = idata.weapon_upgrade_passive(item_id)
        if not pas or pas["kind"] != "effect":
            continue
        tr = idata.weapon_upgrade_trait(item_id)
        col = (st.upgrade_ladder(idata.item(item_id)) or [{}])[0]
        rarity = col.get("rarity") or ""
        rank = st.granted_rank(rarity, pas["max_rank"])
        if not rank:
            continue        # a rarity that cannot buy a rank grants no passive
        # the app line is the title, then the game's own sentence, then the run
        game = sk.skill_description(pas["id"], rank=rank) or ""
        head = f"{pas['name']} — {game.rstrip('.')}"
        assert tr["text"].startswith(head), (item_id, tr["text"], head)
        # ...and the sentence is UNLABELLED: no rarity word, no placeholder
        tail = tr["text"][len(head):]
        sentence = tail[:tail.index(" — ")] if " — " in tail else tail
        assert rarity not in sentence, (item_id, sentence)
        assert "X%" not in sentence, (item_id, sentence)
        assert "".join(t for t, _ in tr["parts"]) == tr["text"], item_id
        types.add(pas["id"])
    # every rankOverride type is covered: 12 of them, one per weapon type
    assert len(types) == 12, sorted(types)


def test_weapon_upgrade_ladders_survive_the_frozen_build():
    """build-mod.bat runs compiler.py, and the frozen app has no loose
    skill.json — the ladders therefore have to come out of the packed
    raw_skills shim. That shim carries `skill_rows` VERBATIM from the source
    sheet, while the trimmed mirror cdb serves keeps only id/name/nature/texts
    (it has no `affixes` at all, which is exactly why `skills._rows()` prefers
    skill_rows).

    Two halves, so both failure modes are caught: the first fails if the
    compiler ever starts trimming `skill_rows`, the second if the reader ever
    starts needing the loose sheet.

    The damage from a trimmed shim is ASYMMETRIC, which is why this is worth a
    test: the mirror keeps `props`/`vars` but never `affixes`, so losing the
    rows would silently drop the ⚡ line on the 8 affix types (every axe
    included) while the 12 effect types kept rendering — a build-only bug that
    reads as "only some weapons have the passive"."""
    from pathlib import Path

    from farever_companion.data import raw_skills, skills
    from farever_companion.data.items import catalog

    rows = {r["id"]: r for r in (raw_skills.DATA.get("skill_rows") or [])}
    axe_row, bow_row = rows.get("Axe_Upgrade") or {}, rows.get("Bow_Upgrade") or {}
    assert len(axe_row.get("affixes") or []) == 5          # the affix path
    assert [o["minRank"] for o in
            (bow_row.get("props") or {}).get("rankOverride") or []] \
        == [2, 3, 4, 5]                                    # the effect path
    assert (bow_row.get("vars") or {}).get("damage") == 0.08
    # and the split itself — 8 affix / 12 rankOverride — is what the mapping
    # turns on, so it has to survive the compiler too
    affix = [t for t in idata.types()
             if (rows.get(f"{t}_Upgrade") or {}).get("affixes")]
    override = [t for t in idata.types()
                if ((rows.get(f"{t}_Upgrade") or {}).get("props") or {})
                .get("rankOverride")]
    assert (len(affix), len(override)) == (8, 12), (sorted(affix),
                                                    sorted(override))
    # ...and prove the trimmed mirror really is poorer, so those fields cannot
    # be satisfied through some other route: it carries no affixes at all
    mirror = {r["id"]: r for r in (raw_skills.DATA.get("skills") or [])}
    assert not (mirror.get("Axe_Upgrade") or {}).get("affixes")

    class NoSheets:
        def sheets_dir(self):
            return Path(__file__).resolve().parents[1] / "tmp_preview"

    orig = skills.paths
    try:
        skills.paths = NoSheets()
        skills._rows.cache_clear()
        catalog.weapon_upgrade_passive.cache_clear()
        catalog.weapon_upgrade_trait.cache_clear()
        # both paths resolve rank for rank, off the shim alone
        assert idata.weapon_upgrade_passive("Axe_Boomerang")["per_rank"] == \
            {r: f"+{r}%" for r in range(1, 6)}
        assert idata.weapon_upgrade_passive("Bow_BigGame")["per_rank"] == \
            {1: "8%", 2: "10%", 3: "12%", 4: "14%", 5: "16%"}
        # ...and the card's ⚡ line still renders its per-rarity values
        assert idata.weapon_upgrade_trait("Axe_Boomerang")["text"] == (
            "Weapon Upgraded — Critical Chance: Rare +2% · Epic +3% · "
            "Legendary +4%.")
        assert idata.weapon_upgrade_trait("GS_Nova")["text"] == (
            "Weapon Upgraded — Your Basic Attacks have a 5% chance to "
            "attack twice — Rare 5% · Epic 6% · Legendary 7%.")
    finally:
        skills.paths = orig
        skills._rows.cache_clear()
        catalog.weapon_upgrade_passive.cache_clear()
        catalog.weapon_upgrade_trait.cache_clear()


def test_both_weapon_upgrade_ladders_share_one_rank_map():
    """The 8 affix types and the 12 props.rankOverride types state the SAME
    thing to the engine — value(rank) for ranks 1..5 — so one rank rule
    serves all 20 weapon types and an effect-path weapon may not be shifted
    differently from an axe.

    Read straight off the SOURCE skill.json (both the compiled shim and the
    catalog model that row), so this fails if the sheet changes shape or if a
    reader starts treating the two paths as different ladders."""
    import json

    src = json.loads((paths.sheets_dir() / "skill.json")
                     .read_text(encoding="utf-8"))
    rows = {r["id"]: r for r in (src.get("lines") or []) if r.get("id")}

    # one representative item per weapon type, taken from the catalog itself
    reps: dict[str, str] = {}
    for it in idata.items():
        typ = it.get("type") or ""
        if typ and f"{typ}_Upgrade" in rows and typ not in reps:
            reps[typ] = it["id"]
    assert len(reps) == 20, sorted(reps)

    for typ, iid in sorted(reps.items()):
        row = rows[f"{typ}_Upgrade"]
        affixes = row.get("affixes") or []
        overrides = (row.get("props") or {}).get("rankOverride") or []
        # exactly one shape per type — never both, never neither
        assert bool(affixes) != bool(overrides), typ

        # the sheet's own ladder, read with no help from the catalog
        if affixes:
            raw = {int(a["conds"].get("minRank") or a["conds"]["maxRank"]):
                   a["val"] for a in affixes}
        else:
            keys = {k for o in overrides for k in (o.get("vars") or {})}
            assert len(keys) == 1, (typ, keys)   # one var carries the ladder
            key = next(iter(keys))
            # the base var IS rank 1: every override starts at rank 2
            assert min(int(o["minRank"]) for o in overrides) == 2, typ
            raw = {1: row["vars"][key]}
            for o in overrides:
                for r in range(int(o["minRank"]), 6):
                    raw[r] = o["vars"][key]
        assert sorted(raw) == [1, 2, 3, 4, 5], (typ, raw)

        pas = idata.weapon_upgrade_passive(iid)
        assert pas["kind"] == ("attribute" if affixes else "effect"), typ
        want = ({r: f"{v:g}%" for r, v in raw.items()} if affixes
                else {r: f"{v * 100:g}%" for r, v in raw.items()})
        assert {r: v.lstrip("+") for r, v in pas["per_rank"].items()} == want, \
            typ

    # ...and the ONE rank rule is asked of both kinds: the trait line reads the
    # granted rank off granted_rank whatever the ladder's shape, so an effect
    # type shows the same rank an affix type would (5% / 6% / 7% for the
    # greatsword's 4..8% ladder, not its raw ranks 6/7/8)
    for iid, own, kind in (("Axe_Boomerang", "Rare +2%", "attribute"),
                           ("GS_Nova", "Rare 5%", "effect"),
                           ("Bow_BigGame", "Rare 10%", "effect")):
        pas = idata.weapon_upgrade_passive(iid)
        tr = idata.weapon_upgrade_trait(iid)
        assert pas["kind"] == kind
        # every rarity the piece can reach is tagged, in rarity order, each
        # with the value granted_rank hands it — and both paths label the value
        # in front. The effect path's prose states that value too, but the
        # rarity is tagged once per rarity either way: that copy of the prose is
        # the GAME's own sentence, which carries no rarity word (only the run)
        assert [r for _, r in tr["parts"] if r] == \
            ["Rare", "Epic", "Legendary"], iid
        run = [f"{r} {pas['per_rank'][idata.granted_rank(r)]}"
               for r in ("Rare", "Epic", "Legendary")]
        tagged = [t for t, r in tr["parts"] if r]
        assert tagged[-3:] == run, iid
        # the piece's own value lands twice on an effect type (once where the
        # sheet put it, once in the run) and exactly once on an affix type
        val = pas["per_rank"][idata.granted_rank("Rare")]
        assert tr["text"].count(val) == (2 if kind == "effect" else 1), \
            (iid, tr["text"])
        # ...and the run's copy of it follows Epic and Legendary labels in order
        assert tr["text"].index(own) < tr["text"].index("Epic "), iid


def test_granted_rank_matches_the_in_game_reading():
    """The granted skill's rank is ONE BELOW the piece's upgrade cap, not the
    upgrade level: in game (2026-09-15) a Cheese Moon reads Critical Chance
    2% (Rare) / 3% (Epic) / 4% (Legendary) — the sheet ladder's ranks 2 / 3 /
    4 — where the sheet's own min/maxRank conditions would key a +3 piece to
    rank 3 (3%). Regression guard: put the rank back to the raw level and
    every weapon line stops matching the game."""
    pas = idata.weapon_upgrade_passive("Axe_Boomerang")
    shown = {rar: pas["per_rank"][idata.granted_rank(rar)]
             for rar in ("Rare", "Epic", "Legendary")}
    assert shown == {"Rare": "+2%", "Epic": "+3%", "Legendary": "+4%"}
    assert {r: idata.granted_rank(r) for r in ("Rare", "Epic", "Legendary")} \
        == {"Rare": 2, "Epic": 3, "Legendary": 4}
    # a rarity that cannot be upgraded grants nothing (never a rank)
    assert idata.granted_rank("Common") == 0
    assert idata.granted_rank("Uncommon") == 0
    assert idata.weapon_upgrade_trait("CopperIngot") is None
    assert idata.weapon_upgrade_trait("Chest_RDemon_Ass") is None


def test_granted_rank_matches_the_live_granted_skill_rank():
    """LIVE, in game (2026-09-15): the engine puts the granted rank on the
    skill instance itself, so the mapping can be read instead of inferred. A
    live read of the hero's granted skills returned kind="GreatAxe_Upgrade"
    with internalRank=4 — and, in the same session, "GreatSword_Upgrade",
    internalRank=4 — each with its originItem (st.item.Weapon) reading
    rarity="Legendary", upgradeLevel=5.

    That is the ceiling step, and it is the step that separates the candidate
    rules: cap-minus-one (what this app ships) says 4, `rank = the upgrade
    level` says 5, and `level + cap - 4` says 5. The engine says 4, so both
    level-driven readings are out. Regression guard: put a level term back
    into the rule and this fails."""
    assert idata.upgrade_cap("Legendary") == 5
    assert idata.granted_rank("Legendary", 5) == 4      # the live reading
    assert idata.granted_rank("Legendary", 5) != 5      # rank = level
    assert max(1, min(5 + idata.upgrade_cap("Legendary") - 4, 5)) == 5
    # the two types live-verified keep their sheet numbers at that rank
    for iid, want in (("GA_Demon", "Rare +2%"), ("GS_Nova", "Rare 5%")):
        pas = idata.weapon_upgrade_passive(iid)
        assert pas["per_rank"][4], "rank 4 (the granted one) must have a value"
        assert want in idata.weapon_upgrade_trait(iid)["text"]


def test_every_weapon_type_gets_a_capped_trait_line():
    """One id per weapon type: all 20 types carry a "Weapon Upgraded" line
    (8 through the affix ladder, 12 through props.rankOverride), and none may
    claim a rank its own rarity cannot buy. The catalog's weapons are all
    Rare/Uncommon/Common today, so every line stops inside the Rare cap."""
    reps = {
        "Axe": "Axe_Boomerang", "DualAxes": "DA_Water",
        "GreatAxe": "GA_Craft", "Fists": "Fists_WaterUppecut",
        "Spear": "Spear_Goo", "Staff": "Staff_Craft",
        "Book": "Book_Start", "Scepter": "Scepter_Start",
        "Sword": "Sword_Start", "Mace": "Mace_Benediction",
        "Daggers": "Daggers_Start", "Bow": "Bow_Craft",
        "Crescent": "Crescent_FlowerSpiral", "Halos": "Halos_Totem",
        "Thrown": "Thrown_Seeds", "Shield": "Shield_Start",
        "GreatSword": "GS_Nova", "DualSwords": "DS_Z1RBee_AssWiz",
        "DualMaces": "DM_Multispin", "GreatMace": "GM_MassGrab",
    }
    for typ, iid in reps.items():
        tr = idata.weapon_upgrade_trait(iid)
        assert tr, f"{typ} ({iid}) has no Weapon Upgraded line"
        rar = (idata.item_display_rarity(iid)
               or (idata.item(iid) or {}).get("rarity"))
        # the line runs from this piece's rarity up, and each bit states THAT
        # rarity's cap value — no rank the rarity cannot buy
        ladder = idata.upgrade_ladder(idata.item(iid)) or []
        cols = [c["rarity"] for c in ladder if c["upgrades"]]
        # every rarity this piece can reach is tagged, in rarity order, and
        # every bit names its rarity in front. The effect path's prose carries
        # its own value too, but never its rarity — the sentence is the game's
        # own — so the rarity is tagged once, by the run
        assert [r for _, r in tr["parts"] if r] == cols, (typ, tr["text"])
        per_rank = idata.weapon_upgrade_passive(iid)["per_rank"]
        for col in ladder:
            col_rar = col["rarity"]
            rank = idata.granted_rank(col_rar)
            if not rank:
                continue
            assert f"{col_rar} {per_rank[rank]}" in tr["text"], \
                (typ, tr["text"])
        assert idata.upgrade_cap(rar) >= 0

    # and a weapon that cannot be upgraded gets NO line at all: the engine
    # grants this passive by upgrade rank, so a Common piece never receives it
    # (the catalog's one Common weapon is the dev row `test`)
    assert idata.weapon_upgrade_trait("test") is None
    assert idata.upgrade_cap("Common") == 0


def test_trait_line_runs_from_the_piece_display_rarity(monkeypatch):
    """The rarity bits follow the DISPLAY rarity — the same source the
    UPGRADE LADDER columns come from — so an Epic or Legendary copy of the
    same axe states its own bits with no code change."""
    from farever_companion.data.items import catalog, sources

    def bits(rarity: str) -> list[str]:
        monkeypatch.setattr(sources, "item_display_rarity",
                            lambda iid, r=rarity: r)
        catalog.weapon_upgrade_trait.cache_clear()
        catalog.weapon_upgrade_passive.cache_clear()
        tr = catalog.weapon_upgrade_trait("Axe_Boomerang")
        catalog.weapon_upgrade_trait.cache_clear()
        catalog.weapon_upgrade_passive.cache_clear()
        return [t for t, r in tr["parts"] if r]

    assert bits("Rare") == ["Rare +2%", "Epic +3%", "Legendary +4%"]
    assert bits("Epic") == ["Epic +3%", "Legendary +4%"]
    assert bits("Legendary") == ["Legendary +4%"]

    # the weapon's OWN passives are flagged with the skill that owns them, so
    # a card that renders the SkillBar can leave them to its tiles — and the
    # flag is honest: every flagged line is already a resolved weapon skill
    traits = idata.item_special_traits("Axe_Boomerang")
    assert [t["skill"] for t in traits] == ["Axe_Boomerang_Skill_Passive"]
    descs = {s["description"] for s in idata.weapon_skills("Axe_Boomerang")}
    assert all(t["text"] in descs for t in traits), traits


# --- Heroic Infusion System (the 2026 patch) --------------------------------


def test_heroic_infusion_map_covers_every_heroic_boss():
    """The Infusion System: every heroic boss's own *_LT2 loot table
    carries exactly one InfusionPattern (guaranteed proba 1.0) — the map
    resolves 12 bosses (the four launch factions; the Crimson trio's tables
    landed with the refreshed scan), each pattern an Epic InfusionPattern
    item. Nightling's three patterns have no *_LT2 row yet, so they carry
    no boss and stay out of this map."""
    from farever_companion.data.items import sources
    m = sources.heroic_infusion_by_boss()
    assert len(m) == 12, m
    bosses = {"Gatsbee", "Mokshi", "Cleodora", "Nepsilon", "Crabgantua",
              "SpongeBlob", "Reblochonk", "Ratsar", "MunsterChuck",
              "Golcano", "RobinHoof", "Phrixes"}
    assert set(m) == bosses
    for iid in m.values():
        it = idata.item(iid) or {}
        assert it.get("type") == "InfusionPattern", iid
        assert it.get("rarity") == "Epic", iid


def test_dungeon_groups_carry_the_infusion_pattern():
    """Each dungeon group's boss rows hold the pattern its heroic table
    guarantees — Bee: Gatsbee DPS / Mokshi Support / Cleodora Tank, and the
    Crimson trio (Golcano / RobinHoof / Phrixes) that the refreshed scan
    resolved. A boss whose *_LT2 table the scan has not captured carries
    none (Nightling's has none yet)."""
    from farever_companion.data.items import sources
    m = sources.heroic_infusion_by_boss()
    # counted per BOSS, not per group row: a boss belongs to exactly one
    # family (its own unit's faction — see the membership test below)
    seen = set()
    by_id = {}
    for g in idata.dungeon_drop_groups():
        for b in g["bosses"]:
            want = m.get(b["boss_id"], "")
            assert b.get("infusion", "") == want, (g["faction"], b["boss_id"])
            by_id[b["boss_id"]] = want
            if want:
                seen.add(b["boss_id"])
    assert len(seen) == 12
    # the role variants land on the right bosses
    assert by_id["Gatsbee"] == "InfusionPattern_Bee_DPS"
    assert by_id["Mokshi"] == "InfusionPattern_Bee_Support"
    assert by_id["Cleodora"] == "InfusionPattern_Bee_Tank"
    assert by_id["Ratsar"] == "InfusionPattern_Kobold_Tank"
    assert by_id["SpongeBlob"] == "InfusionPattern_Manfish_DPS"
    assert by_id["Golcano"] == "InfusionPattern_Crimson_Support"


def test_every_boss_sits_in_exactly_one_faction_group():
    """A dungeon joins its BOSS unit's faction, so no boss is listed under
    two families. It used to happen: membership also matched the mob roster,
    and the Nightling summons stand INSIDE Crimson Sacristy, which pulled
    Crimson's High Inquisitor Chakram (Phrixes) into the Nightling group as
    well — a boss already present in Crimson's own."""
    from farever_companion.data import units
    seen = {}
    for g in idata.dungeon_drop_groups():
        for b in g["bosses"]:
            assert b["boss_id"] not in seen, (
                f"{b['boss_id']} sits in both {seen[b['boss_id']]} and "
                f"{g['faction']} — a dungeon belongs to its boss's faction")
            seen[b["boss_id"]] = g["faction"]
    assert seen["Phrixes"] == "Crimson"
    assert seen["DemonSuperElite"] == "Demon"
    assert seen["DemonSuperElite_Fairy"] == "Demon"
    # the group's family is the unit sheet's own faction where it names one
    for bid, fac in seen.items():
        ufac = (units.unit_info(bid) or {}).get("faction")
        if ufac:
            assert ufac == fac, (bid, ufac, fac)
    dem = [g for g in idata.dungeon_drop_groups()
           if g["faction"] == "Demon"][0]
    assert [b["boss_id"] for b in dem["bosses"]] == [
        "DemonSuperElite", "DemonSuperElite_Fairy"]


def test_shared_pool_source_is_read_not_assumed():
    """A faction's SHARED pool is only a boss/chest drop for four of the
    five families. Nightling's 25 Rares resolve to ONE NPC vendor (Mira,
    Demon Huntress, 10 Nightblood a piece, sold in all three hubs) and its
    two bosses' tables hold nothing but their signature weapons, so the
    Dungeons pane must be able to say so instead of claiming "drops in:
    <bosses>". Derived from the rows, so a patch that gives the bosses gear
    flips it back on its own."""
    from farever_companion.data import items as idata
    by_fac = {g["faction"]: g for g in idata.dungeon_drop_groups()}
    assert set(by_fac) == {"Bee", "Kobold", "Manfish", "Crimson", "Demon"}
    for fac in ("Bee", "Kobold", "Manfish", "Crimson"):
        ss = idata.shared_source(by_fac[fac])
        assert ss["kind"] == "drop", fac
    ss = idata.shared_source(by_fac["Demon"])
    assert ss["kind"] == "vendor"
    assert ss["source"] == "Mira, Demon Huntress"
    assert ss["cost"] == (("Nightblood", 10),)
    # all three hubs, named
    assert set(ss["locs"]) == {"Navelin", "Tyrna", "Lower Ramburg"}
    # and the bosses really do carry weapons only — nobody rolls the Rares
    shared = set(by_fac["Demon"]["shared"])
    assert shared and all(
        {r["kind"] for r in idata.resolve_drops(i)} == {"npc"}
        for i in shared)
    for b in by_fac["Demon"]["bosses"]:
        for w in b["weapons"]:
            assert {r["kind"] for r in idata.resolve_drops(w)} & {
                "unit", "chest"}, w


def test_heroic_infusion_keys_resolve_to_boss_units():
    """Every key of the boss -> pattern map is a boss unit the dungeon
    sheet can name, and the two Nightling rifts — whose `*_LT2` table is
    named after the DUNGEON (`Maat_LT2` / `Shaarlize_LT2`), not the boss
    unit — already translate to their boss ids. So the wiring is complete
    the moment a pattern row lands in one of those tables; today the build
    ships all three Nightling patterns with no `*_LT2` row at all, which is
    the only reason they carry no boss."""
    from farever_companion.data import units
    from farever_companion.data.items import sources
    aliases = sources._heroic_key_to_boss()
    assert aliases["maat"] == "DemonSuperElite"
    assert aliases["shaarlize"] == "DemonSuperElite_Fairy"
    # neither table key is a unit id, which is exactly why the dungeon
    # sheet has to do the translating
    assert not units.is_boss("Maat") and not units.is_boss("Shaarlize")
    assert all(units.is_boss(b) for b in sources.heroic_infusion_by_boss())
    # the three Nightling rows and the skill each granted infusion carries
    pool = {x["item"]: x for x in sources.heroic_infusions()}
    night = {pool[i]["role"]: pool[i] for i in pool
             if pool[i]["faction"] == "Nightling"}
    assert {r: p["skill"] for r, p in night.items()} == {
        "DPS": "Gift Beyond Realities", "Tank": "Blood Oath",
        "Support": "Cursed Link"}
    # the missing table row is the whole gap: nothing else is unresolved
    assert all(p["boss"] == "" and p["dungeon"] == ""
               and p["unlock_boss"] == "" for p in night.values())


def test_nightling_infusion_wires_up_the_moment_its_lt2_row_lands(
        monkeypatch):
    """The end-to-end wiring for the Nightling patterns: give one of the two
    rift tables the pattern row the build doesn't ship yet and the row must
    resolve the DUNGEON-named key to the real boss unit — boss, crucible
    unlock name and dungeon all filled in — with no further code change.
    The table key ('Shaarlize') must not leak through as a boss id."""
    from farever_companion.data import cdb
    from farever_companion.data.items import sources
    live_map = sources.heroic_infusion_by_boss()
    real_lines = cdb.lines

    def fake_lines(sheet):
        if sheet != "lootTable":
            return real_lines(sheet)
        rows = [dict(r) for r in real_lines(sheet)]
        for r in rows:
            if r.get("id") == "Shaarlize_LT2":
                r["loot"] = list(r.get("loot") or []) + [
                    {"item": "InfusionPattern_Nightling_Tank", "proba": 1.0}]
        return rows

    monkeypatch.setattr(cdb, "lines", fake_lines)
    sources.heroic_infusion_by_boss.cache_clear()
    try:
        m = sources.heroic_infusion_by_boss()
        assert m["DemonSuperElite_Fairy"] == "InfusionPattern_Nightling_Tank"
        assert "Shaarlize" not in m, m
        pool = {x["item"]: x for x in sources.heroic_infusions()}
        row = pool["InfusionPattern_Nightling_Tank"]
        assert row["boss"] == "DemonSuperElite_Fairy"
        assert row["unlock_boss"] == "Nightqueen Shaarlize Te'ror"
        assert row["dungeon"] == "Rift Nightqueen Shaarlize Te'ror"
    finally:
        sources.heroic_infusion_by_boss.cache_clear()
        monkeypatch.undo()
        sources.heroic_infusion_by_boss.cache_clear()
    # the live map is back exactly as it was (no rift row today)
    assert sources.heroic_infusion_by_boss() == live_map


def test_demon_rift_dungeon_labels_spell_out_the_title():
    """The two Nightling rifts are the only dungeons whose `ingame_name` is
    the raw key ('maat' / 'shaarlize') while `name` carries the title, so
    the label prefers the spelled-out one; everywhere else the sheet's
    `ingame_name` still wins."""
    g = [x for x in idata.dungeon_drop_groups()
         if x["faction"] == "Demon"][0]
    labels = {b["boss_id"]: b["dungeon"] for b in g["bosses"]}
    assert labels["DemonSuperElite"] == "Rift Nightking Maat Demon"
    assert labels["DemonSuperElite_Fairy"] == \
        "Rift Nightqueen Shaarlize Te'ror"
    assert "Phrixes" not in labels     # Crimson's boss, not Nightling's


def test_infusion_and_mog_acquisition_surface():
    """The pattern's card names the crucible mechanic under Drops From and
    resolves its heroic boss drop; the Medal of Glory — a currency with no
    loot rows — is listable with its heroic-completion note."""
    from farever_companion.data.items import sources
    from farever_companion.ui.pages.items import support
    # pattern: one guaranteed heroic drop + the crucible note
    rows = idata.shown_drops("InfusionPattern_Bee_DPS")
    assert any(r.get("source_id") == "Gatsbee" for r in rows)
    assert "crucible" in (sources.acquisition_note(
        "InfusionPattern_Bee_DPS") or "").lower()
    # MoG: currency with zero drops, yet listable (the patch exception)
    mog = idata.item("BadgeOfGlory")
    assert mog and not idata.has_drops("BadgeOfGlory")
    assert support.is_listable_item(mog)
    note = sources.no_source_reason("BadgeOfGlory")
    # the CACHE is a heroic boss drop, so the note points at the boss and the
    # box — NOT at the shared social hubs, which is where the vendor is and
    # not where the cache drops.
    assert "Heroic" in note and "boss" in note
    assert "social hubs" not in note
    assert "Shiro James" in note
    # the box count is read from the real table, not asserted in prose
    box = sources.heroic_cache_contents()["HM_Gear_Cache_25"]
    assert f"{len(box)} Epic pieces" in note
    assert {r["rarity"] for r in box} == {"Epic"}
    # The vendor's hub towns are deliberately NOT in this note. They are
    # where Shiro James stands, not where the cache drops, and listing them
    # sent the reader to the social hubs to look for a boss drop.
    for town in ("Navelin", "Tyrna", "Lower Ramburg"):
        assert town not in note


def test_mog_vendor_matches_shiro_james(monkeypatch):
    """The 2026 patch replaced the old Glory Merchant with Shiro James, who
    sells the Hero Gear Cache and the Hero Weapon Cache for Badge of Glory.
    The vendor lookup matches him BY NAME as well as by id, so a capture
    that records only his display name still resolves his hub; the pirate
    unit whose id merely shares the token must not."""
    from farever_companion.data.items import catalog as _catalog
    from farever_companion.data.items import sources
    assert sources.is_mog_source({"id": "ShiroJames_NPC_Azuram",
                                  "name": "Shiro James"})
    assert sources.is_mog_source("Shiro James")
    assert sources.is_mog_source({"id": "Shiro_James_Z2"})
    assert not sources.is_mog_source({"id": "ShiroNext_Pirate",
                                      "name": "Shiro Next"})
    real = _catalog._data

    def fake_data():
        d = dict(real())
        d["npc_sources"] = [{"id": "ShiroJames_NPC_Azuram",
                             "name": "Shiro James",
                             "zone": "Z2_Azuram_Hub"}]
        return d

    monkeypatch.setattr(sources, "_data", fake_data)
    try:
        # a name-only / id-only row still resolves the hub zone to its town
        assert sources._mog_merchant_towns() == ("Tyrna",)
    finally:
        monkeypatch.undo()


def test_mog_exchange_derives_from_merchant_shop_rows(monkeypatch):
    """The MoG spend list derives from the scan's BadgeOfGlory-cost shop
    rows (the vendor-stock convention: item's drops[] row with table=Shop,
    a Shiro James source and cost entries naming BadgeOfGlory — the
    same shape Zoey's DemonicSoul crystals ride). The pool is empty while
    the capture is a stub, and the note's spend list lights up with the real
    stock — the derivation is what this pins, not today's capture state.

    The row indexes are read off the COMPILED payload rather than hardcoded
    (they used to be 25/15): every vendor scan renumbers `_sources`/`_tables`,
    so a literal index silently stopped pointing at the Medal of Glory
    vendor after the 2026 refresh."""
    from farever_companion.data.items import sources
    # the live compiled indexes — read once, before the monkeypatch replaces
    # `_data` (both are lru_cached, so these are the scan's real numbering)
    srcs, tbls = sources._sources(), sources._tables()
    # is_mog_source matches Shiro James (by name or id), so this guard
    # tracks whatever the scan wrote
    mog_idx = next((i for i, s in enumerate(srcs)
                    if sources.is_mog_source(s)), None)
    shop_idx = next((i for i, t in enumerate(tbls) if t == "Shop"), None)
    assert shop_idx is not None, "no Shop loot table in the compiled payload"
    # The live capture has no Shiro James row (the 2026 vendor patch
    # post-dates the scan), so the derivation is exercised against an
    # INJECTED vendor row appended to the live sources rather than asserted
    # against today's payload — the derivation is what this pins, not
    # whether a rescan has happened yet.
    real_sources = list(srcs)
    if mog_idx is None:
        mog_idx = len(real_sources)
        real_sources.append({"id": "ShiroJames_NPC_Primevalley",
                             "kind": "npc", "name": "Shiro James",
                             "zone": "Z1_Primevalley_Hub"})
        monkeypatch.setattr(sources, "_sources", lambda: real_sources)
    # the live state, whatever the capture recorded (() while it is a stub)
    live_stock = sources.mog_exchange()
    live_note = sources.no_source_reason("BadgeOfGlory")

    def fake_data():
        return {"npc_sources": [
            {"id": "ShiroJames_NPC_Primevalley", "name": "Shiro James",
             "zone": "Z1_Primevalley_Hub"}],
            "items": {
            "Sword_Heroic_A": {"name": "Auroral Edge", "drops": [
                {"s": mog_idx, "t": shop_idx, "p": 1.0, "l": 7,
                 "cost": [{"kind": "BadgeOfGlory", "amount": 120}]}]},
            "Mount_Heroic_B": {"name": "Duskhorn", "drops": [
                {"s": mog_idx, "t": shop_idx, "p": 1.0, "l": 8,
                 "cost": [{"kind": "BadgeOfGlory", "amount": 60}]}]},
            "Gold": {"name": "Gold", "drops": [
                {"s": mog_idx, "t": shop_idx, "p": 1.0}]},  # unpriced
        }}

    monkeypatch.setattr(sources, "_data", fake_data)
    sources.mog_exchange.cache_clear()
    sources.no_source_reason.cache_clear()
    try:
        stock = sources.mog_exchange()
        assert [r["item"] for r in stock] == ["Sword_Heroic_A",
                                              "Mount_Heroic_B"]
        assert stock[0]["label"] == "Auroral Edge"
        assert stock[0]["qty"] == 120
        # unpriced rows are skipped, not invented into listings
        assert all(r["item"] != "Gold" for r in stock)
        note = sources.no_source_reason("BadgeOfGlory")
        assert "spent at Shiro James" in note
        assert "Auroral Edge (120 MoG)" in note
        assert "Duskhorn (60 MoG)" in note
        # the vendor's hub towns stay out of the cache note: the cache is a
        # boss drop, so naming towns sent the reader to the wrong place
        assert "Navelin" not in note
    finally:
        sources.mog_exchange.cache_clear()
        sources.no_source_reason.cache_clear()
        monkeypatch.undo()
        sources.mog_exchange.cache_clear()
        sources.no_source_reason.cache_clear()
    # after undo the live state is back exactly as it was
    assert sources.mog_exchange() == live_stock
    assert sources.no_source_reason("BadgeOfGlory") == live_note


# --- Heroic gear caches: the link the compiled sheet can't carry -------------


def test_heroic_caches_open_the_box_the_compiled_gain_item_names():
    """Each Heroic cache's box comes from the loot table its own
    `props.gainItem.lootTable` names — and the compiler PRUNES `props`, so
    the data layer keeps its own copy of that link. That copy went stale: the
    Hero Weapon Cache reported the 24-piece gear box and the Chaotic Gear
    Cache reported the class starter weapon bundle, so every Contents list
    and 'opens into' line showed the wrong box. The compiler now bakes the
    link onto the container's drops row as `gain_item`, so the truth survives
    where CI can read it — no loose sheet, no skip."""
    from farever_companion.data.items import sources
    gain = {cid: (info["gain"] or {}).get("lootTable")
            for cid, info in sources.compiled_container_facts().items()}
    for cache_id, table in sources._HEROIC_CACHE_TABLES.items():
        assert gain.get(cache_id) == table, (
            f"{cache_id} opens {gain.get(cache_id)!r} in the compiled data "
            f"but the data layer maps it to {table!r} — its Contents list and "
            "'opens into' line are showing the wrong box")
    boxes = sources.heroic_cache_contents()
    assert {c: len(b) for c, b in boxes.items()} == {
        "HM_Gear_Cache_25": 24, "HM_Weapon_Cache_25": 4,
        "HM_Demon_Cache_25": 25, "Rift_Gear_Cache": 25,
        "Rift_Weapon_Cache": 4, "Rift_Gift_Cache": 12}
    assert {r["rarity"] for r in boxes["HM_Gear_Cache_25"]} == {"Epic"}
    assert {r["rarity"] for r in boxes["HM_Weapon_Cache_25"]} == {"Epic"}
    assert {r["rarity"] for r in boxes["HM_Demon_Cache_25"]} == {"Epic"}
    assert {r["rarity"] for r in boxes["Rift_Gear_Cache"]} == {"Rare"}
    assert {r["rarity"] for r in boxes["Rift_Weapon_Cache"]} == {"Rare"}
    assert {r["rarity"] for r in boxes["Rift_Gift_Cache"]} == {"Epic"}


def test_chaotic_gear_cache_name_is_shared_by_two_different_boxes():
    """`Rift_Gear_Cache` and `HM_Demon_Cache_25` are BOTH named "Chaotic Gear
    Cache" in game, and they are nothing alike:

      Rift_Gear_Cache     RARE, player-scale 1-25, 25 Nightling pieces — the
                          one Mira the Demon Huntress sells for rift currency
      HM_Demon_Cache_25   EPIC, fixed 25, 25 Nightling pieces — the Heroic box

    Verified in-game on 2026-09-26: 20 Chaotic Gear Caches bought from Mira
    produced 20 Rare pieces, at the player's level, with names from the Rare
    table — so the Rare reading is the real one and anything resolving a box
    by display name would swap the two.
    """
    from farever_companion.data.items import sources
    specs = sources.heroic_cache_spec()
    rift, hm = specs["Rift_Gear_Cache"], specs["HM_Demon_Cache_25"]
    assert rift["name"] == hm["name"] == "Chaotic Gear Cache"
    # ...but they are LISTED under different names, because both appear side
    # by side on Mira's vendor line and two identical chips read as one box.
    # The sheet name stays the sheet name — this is a disambiguator for lists,
    # and the item's own page still shows what the game calls it.
    assert sources.cache_display_name("Rift_Gear_Cache") == "Chaotic Gear Cache"
    assert sources.cache_display_name("HM_Demon_Cache_25") == \
        "Heroic Chaotic Gear Cache"
    # and no two boxes share a listed name, so a collision cannot reappear
    listed = [sources.cache_display_name(c) for c in sources.heroic_cache_spec()]
    assert len(set(listed)) == len(listed), listed
    # same name, opposite everything else
    assert rift["rarity"] == "Rare" and hm["rarity"] == "Epic"
    assert rift["player_scale"] is True and hm["player_scale"] is False
    assert rift["table"] != hm["table"]
    # the two pools are disjoint, so no piece is claimed by both
    a = {r["item"] for r in sources.heroic_cache_contents()["Rift_Gear_Cache"]}
    b = {r["item"] for r in sources.heroic_cache_contents()["HM_Demon_Cache_25"]}
    assert not (a & b)
    # and each names what is actually known about where it comes from
    assert "Mira" in rift["via"] and "Rift currency" in rift["via"]
    # The two Hero caches are recorded as SOLD, with no hand-written string:
    # the shop listing was read live, so the seller is a fact and the prose is
    # derived from it. The Demon box has no seller at all and therefore no
    # string — it says UNRELEASED, which the data supports.
    for cid in ("HM_Gear_Cache_25", "HM_Weapon_Cache_25"):
        spec = sources.heroic_cache_spec()[cid]
        assert "via" not in spec, cid          # derived, not hand-typed
        assert not sources.cache_is_unreleased(cid), cid
        v = sources.cache_vendor(cid) or {}
        assert "Shiro James" in v.get("source", ""), (cid, v)
        assert tuple((c["kind"], c["amount"]) for c in v.get("cost") or ()) \
            == (("BadgeOfGlory", 100),), (cid, v)
    assert "via" not in hm
    assert sources.cache_is_unreleased("HM_Demon_Cache_25")
    # every RELEASED box names a real seller — never a bare "a drop"
    for cid in sources.heroic_cache_spec():
        if sources.cache_is_unreleased(cid):
            continue
        via = sources.cache_via(cid)
        assert via and "a drop" not in via, (cid, via)
    # ...and the piece-level note agrees
    trinket = sources.no_source_reason("Trinket_Demon_E")
    assert "unreleased" in trinket, trinket


def test_cache_pieces_name_the_vendor_that_actually_sells_their_box():
    """The fallback source prose must not call every box a Heroic boss drop:
    Mira's Rift boxes come from a vendor, and saying otherwise sends players
    hunting bosses for a box that is bought with rift currency. A box nothing
    hands out says UNRELEASED rather than naming a vendor at all."""
    from farever_companion.data.items import sources
    for cid in sources.heroic_cache_spec():
        note = sources.no_source_reason(cid)
        if sources.cache_is_unreleased(cid):
            # the box names NO seller at all, not even a hedged one
            assert note.startswith("UNRELEASED"), (cid, note)
            for who in ("Shiro", "Mira", "Heroic boss"):
                assert who not in note, (cid, who, note)
            continue
        assert sources.cache_via(cid).split(",")[0] in note, (cid, note)
    for row in sources.heroic_cache_contents()["Rift_Gear_Cache"]:
        got = sources.no_source_reason(row["item"])
        assert "Mira" in got, (row["item"], got)
        assert "Heroic boss" not in got, (row["item"], got)


def test_boxes_nothing_hands_out_are_labelled_unreleased():
    """A box is unreleased when no recorded source hands it over: no vendor we
    have seen selling it, and no drop row on any of its pieces. That is
    derived from the data, so a future scan that records a source clears the
    label with no code change.

    Live evidence (2026-09-26): a rift scene holds 25 elements, every one a
    chest / portal / trigger / spawn and zero NPCs, so there is no rift
    vendor — but Shiro James's hub shop sells two of the `HM_` boxes outright,
    which is why those two are not in the set.
    """
    from farever_companion.data.items import sources
    un = {cid for cid in sources.heroic_cache_contents()
          if sources.cache_is_unreleased(cid)}
    # ONE box, and it is the Demon one. The two Hero caches were briefly
    # mislabelled here: the sheet has no row for any of them, so the label
    # read from absence — but they are both sitting in Shiro James's stock at
    # 100 medals, which a recorded observation settles. A box we have watched
    # being sold can never be reported as unreachable, so the recorded seller
    # is checked BEFORE the piece rows.
    assert un == {"HM_Demon_Cache_25"}
    for cid in ("Rift_Gear_Cache", "Rift_Weapon_Cache", "Rift_Gift_Cache",
                "HM_Gear_Cache_25", "HM_Weapon_Cache_25"):
        assert not sources.cache_is_unreleased(cid), cid
    # the Hero Weapon Cache is the case that proves the ordering: its four
    # rows are the level-4 RARE craft starters, whose Guild Merchant rows
    # describe the BASE weapon, not the box. Nothing in the sheet points at
    # the box — and it is still not unreleased.
    assert sources.heroic_cache_spec()["HM_Weapon_Cache_25"]["template"]
    assert not any(sources.resolve_drops("HM_Weapon_Cache_25")), \
        "the box itself has no sheet row — that is why it is recorded"
    assert sources.no_source_reason("HM_Weapon_Cache_25").startswith("From ")


def test_cache_contents_with_no_other_source_list_the_box_as_a_drop():
    """The scan never records the container itself, so a piece that only ever
    comes out of a box reported 'no drop sources' while its own page named the
    box. The box IS the acquisition, so it belongs in the Drops From list —
    as a BOXES row, flagged unreleased when nothing hands the box out."""
    from farever_companion.data.items import sources
    epic = sources.heroic_cache_contents()["HM_Demon_Cache_25"][0]["item"]
    rows = sources.merge_drops(epic)
    assert len(rows) == 1
    r = rows[0]
    assert r["kind"] == "cache"
    assert r["source_id"] == "HM_Demon_Cache_25"
    assert r["table"] == "HM_DemonGear"
    assert r["unreleased"] is True
    assert r["locs"] == [] and r["prob"] == 0.0

    # a piece that ALREADY has a real source keeps it and does not also get
    # the box — the same fact must not be listed twice
    rare = sources.heroic_cache_contents()["Rift_Gear_Cache"][0]["item"]
    kinds = {r["kind"] for r in sources.merge_drops(rare)}
    assert "cache" not in kinds
    assert "npc" in kinds


def test_released_box_rows_are_not_flagged_unreleased():
    """A box with a recorded source must not carry the unreleased flag on the
    row, or Mira's own box would read as unobtainable."""
    from farever_companion.data.items import sources
    for cid, box in sources.heroic_cache_contents().items():
        if sources.cache_is_unreleased(cid):
            continue
        for row in box:
            for d in sources.merge_drops(row["item"]):
                if d["kind"] == "cache":
                    assert d["unreleased"] is False, (cid, d)


def test_player_scale_boxes_report_no_fixed_level():
    """Mira's boxes roll at YOUR level (1-25), so a row's level is unknowable
    until it drops. Reporting the cap as if it were the level is what made the
    Contains note claim 'gear level 25' on a box that scales."""
    from farever_companion.data.items import sources
    for cid, spec in sources.heroic_cache_spec().items():
        levels = {r["level"] for r in sources.heroic_cache_contents()[cid]}
        if spec["player_scale"]:
            assert levels == {None}, (cid, levels)
        elif spec["level_max"] is not None:
            assert levels == {spec["level_max"]}, (cid, levels)


def test_heroic_caches_report_the_rarity_they_actually_hand_out():
    """The Hero Weapon Cache points at `WorldWeaponWithAffinity`, whose four
    rows are the level-4 RARE craft-starter weapons (Glory, Dominion,
    Radiance, Credence). Those are BASE TEMPLATES: the cache's own flavor text
    says "Contains an Epic world weapon level 25" and its levelRange is 25-25,
    so the weapon is rolled up to the box's rarity. Reading the template rows
    literally made the app show a Rare tag on an Epic box.
    """
    from farever_companion.data.items import sources
    specs = sources.heroic_cache_spec()
    for cache_id, spec in specs.items():
        assert spec["rarity"] in ("Rare", "Epic"), (cache_id, spec["rarity"])
        box = sources.heroic_cache_contents()[cache_id]
        # every row reports the BOX's rarity, because the table is the pool
        # and the container's own rarity is the roll
        assert {r["rarity"] for r in box} == {spec["rarity"]}, (
            cache_id, sorted({r["rarity"] for r in box}))

    # the weapon box is the templated one, and says so rather than hiding it
    wbox = sources.heroic_cache_contents()["HM_Weapon_Cache_25"]
    assert specs["HM_Weapon_Cache_25"]["template"] is True
    assert all(r["template"] for r in wbox)
    # every other box names the real pieces
    for cid in specs:
        if cid != "HM_Weapon_Cache_25":
            assert not any(r["template"]
                           for r in sources.heroic_cache_contents()[cid]), cid


def test_hero_weapon_cache_rolls_above_its_floor():
    """The Hero Weapon Cache's `gainItem.rarity` is `{min: "Epic"}` — a FLOOR,
    not a fixed rarity. Verified in game 2026-10-05: one open of the box showed
    an Epic "Glory" (purple frame) beside a Legendary "Dominion" (gold frame),
    so the same box hands out any rung from Epic up. The app read the
    container's own `rarity` and called every row of it Epic, advertising the
    floor as the whole story.

    The span rides the spec AND every row, and every other box's span is a
    point — a fixed box must not start claiming an upgrade it cannot back.
    """
    from farever_companion.data.items import catalog, sources
    spec = sources.heroic_cache_spec()["HM_Weapon_Cache_25"]
    assert spec["rarity"] == "Epic"          # the box's own tier, in a list
    assert spec["rarity_min"] == "Epic"      # what an open can bottom out at
    assert spec["rarity_max"] == catalog.RARITY_ORDER[-1] == "Legendary"
    box = sources.heroic_cache_contents()["HM_Weapon_Cache_25"]
    assert box, "the weapon box's contents are read from the sheet"
    assert all(r["rarity_min"] == "Epic" and r["rarity_max"] == "Legendary"
               for r in box), box
    for cid, s in sources.heroic_cache_spec().items():
        if cid == "HM_Weapon_Cache_25":
            continue
        assert s["rarity_min"] == s["rarity_max"] == s["rarity"], (cid, s)
        assert {r["rarity_max"] for r in
                sources.heroic_cache_contents()[cid]} == {s["rarity"]}, cid


def test_heroic_cache_rows_never_leak_the_tables_own_rarity():
    """The un-overridden read is the bug this guards: `HM_DemonGear` stores
    its 24 armor rows at level 1 and `WorldWeaponWithAffinity` stores its four
    at level 4 / Rare, so a row-level read reports gear levels the boxes
    cannot possibly drop. The box's declared level is the one to show."""
    from farever_companion.data.items import sources
    items = sources._data().get("items", {})
    for cid, spec in sources.heroic_cache_spec().items():
        for row in sources.heroic_cache_contents()[cid]:
            src = items.get(row["item"]) or {}
            assert row["rarity"] == spec["rarity"]
            if not spec["template"]:
                # the non-templated rows genuinely are the granted pieces, so
                # their own rarity already matches what the box rolls
                assert src.get("rarity") == row["rarity"], row["item"]


# Containers the compiled drops shim declares as a `LootableContainer` with a
# `gainItem` but the app's HAND cache table (`_HEROIC_CACHE_SPECS`) does NOT
# spec. Named explicitly (never derived) so a NEW box turns the contract below
# red until someone either specs it or records why it is left to the generic
# path — the same "named exception, ratcheted" shape the repo's
# orphan/constant/key censuses use.
#
# This is NOT an app-level exclusion any more. `sources.container_spec()` /
# `container_contents()` DERIVE a spec and a Contains list for every container
# the hand table does not claim, from its compiler-baked `gain_item`, and the
# Items page renders them the same as a specced box (see
# test_unspecced_containers_are_surfaced_by_the_generic_path). So the listing
# here only says "the HAND table does not claim it", never "the app hides it".
_UNSPECCED_CONTAINERS = {
    # the Zone-1 starter weapon bundle: a Rare box the Items page makes no
    # heroic/vendor claim about, so _HEROIC_CACHE_SPECS never named it. Its
    # gainItem is plain (no rarity clamp and no level range), which is exactly
    # why it needs no roll-span machinery — its pieces come from the table at
    # their own fixed level, not rolled to the box.
    "Z1_WeaponBundle",
}


def _assert_cache_matches_gain(cache_id: str, spec: dict, info: dict) -> None:
    """One box: the COMPILED `gainItem` facts == the app's spec.

    The facts are read from the drops shim (`sources.compiled_container_facts`
    -> the row's `gain_item`), not the loose game sheet, so the check runs in
    CI where the dump is absent. table -> the loot table it opens;
    levelRange -> level_min/level_max (with player_scale iff the range is not a
    point); maxItems -> max_items; and the rarity `{min}` floor -> the span
    `container_roll_span()` derives (a floor alone clamps to the ladder top).
    Shared by the two contract tests below so the check has ONE implementation.
    """
    from farever_companion.data.items import catalog
    g = info["gain"]
    assert g.get("lootTable") == spec["table"], cache_id
    assert g.get("maxItems") == spec["max_items"], (
        cache_id, g.get("maxItems"), spec["max_items"])
    lr = g.get("levelRange") or {}
    if spec["level_min"] is None:
        assert not lr, (cache_id, lr)
    else:
        assert (lr.get("min"), lr.get("max")) == (
            spec["level_min"], spec["level_max"]), (
                cache_id, lr, spec["level_min"], spec["level_max"])
        # a player-scale box is exactly one whose range is not a point
        assert spec["player_scale"] == (lr["min"] != lr["max"]), cache_id
    # the rarity an open hands out: a recorded floor in the sheet means the
    # roll is CLAMPED (Epic..Legendary for the weapon box); no floor means it
    # is the container's own rarity, exactly and only
    floor = (g.get("rarity") or {}).get("min") or ""
    box_rar = info["rarity"]
    assert spec["rarity_min"] == (floor or box_rar), (
        cache_id, spec["rarity_min"], floor, box_rar)
    assert spec["rarity_max"] == (catalog.RARITY_ORDER[-1]
                                  if floor else box_rar), (
        cache_id, spec["rarity_max"], floor, box_rar)


def test_heroic_cache_specs_match_the_compiled_gain_item():
    """`level_min`/`level_max`/`max_items` are hand-maintained because the
    compiler prunes `props` — so they are pinned against the COMPILED copy of
    the gainItem (baked onto the drops row), the same way the table name
    already is. A patch that re-levels a box fails here instead of quietly
    rendering 'at most 2 per open' on a box that rolls 3.

    Reading the compiled `gain_item` instead of the loose sheet is what lets
    this run in CI, where the game dump is absent — no skip, so a drift cannot
    hide behind a green suite.

    The roll's RARITY span is cross-checked here too, but it is NO LONGER a
    hand fact: the compiler bakes `gainItem.rarity` onto the container's drops
    row and container_roll_span() reads it, so this only confirms the two
    reads agree — a `gainItem.rarity.min` means the span is clamped at that
    rung and can climb to the ladder top, and a container's own `rarity` means
    the span IS that rung.
    """
    from farever_companion.data.items import sources
    containers = sources.compiled_container_facts()
    specs = sources.heroic_cache_spec()
    assert specs, "no cache specs to check"
    for cache_id, spec in specs.items():
        assert cache_id in containers, (
            f"_HEROIC_CACHE_SPECS names {cache_id}, which the compiled shim "
            "does not declare as a LootableContainer with a gainItem")
        _assert_cache_matches_gain(cache_id, spec, containers[cache_id])


def test_compiled_gain_item_facts_are_the_contract_for_every_container():
    """The explicit Handoff/AGENTS contract for the cache table.

    `_HEROIC_CACHE_SPECS` is the app's hand-recorded claim about the boxes it
    knows; the COMPILED drops shim — baked from the game's item sheet — is the
    truth. The per-box test above pins the CLAIMED boxes' facts. This one
    closes the other half, walking EVERY container the shim declares — never a
    hand-written id list — and requiring each to be either specced or named in
    `_UNSPECCED_CONTAINERS`.

    So the contract is two-sided and explicit: a box that drops out of the
    shim fails (stale spec), a fact that drifts fails (the per-box test), and a
    box the game GAINS fails HERE rather than quietly never appearing in the
    Items page. Generic by construction: adding a container to item.json (and
    so to the compiled shim) is what breaks it, not editing this file.

    Unlike the loose-sheet read it replaces, this RUNS IN CI: the shim ships
    with the app, so there is no game dump to miss and no skip.
    """
    from farever_companion.data.items import sources
    containers = sources.compiled_container_facts()
    # floor the domain so the test can never pass on an empty/garbled read
    assert len(containers) >= 6, sorted(containers)

    specs = sources.heroic_cache_spec()
    for cache_id, info in sorted(containers.items()):
        spec = specs.get(cache_id)
        if spec is None:
            assert cache_id in _UNSPECCED_CONTAINERS, (
                f"the compiled shim declares container {cache_id} with a "
                "gainItem, but _HEROIC_CACHE_SPECS does not spec it and it is "
                "not in _UNSPECCED_CONTAINERS — spec it or record why it is out")
            continue
        _assert_cache_matches_gain(cache_id, spec, info)

    stale = set(specs) - set(containers)
    assert not stale, (
        f"_HEROIC_CACHE_SPECS names boxes the shim no longer declares: "
        f"{sorted(stale)}")


def test_unspecced_containers_really_have_no_rarity_clamp():
    """The hand table leaves these out honestly: each has a plain gainItem
    with no `rarity` clamp, so no roll span is being dropped on the floor by
    not naming it in `_HEROIC_CACHE_SPECS`."""
    from farever_companion.data.items import sources
    containers = sources.compiled_container_facts()
    for cache_id in _UNSPECCED_CONTAINERS:
        assert cache_id in containers, cache_id
        assert not (containers[cache_id]["gain"].get("rarity") or {}), cache_id


def test_unspecced_containers_are_surfaced_by_the_generic_path():
    """`_UNSPECCED_CONTAINERS` must not be a test-only blind spot.

    Every container the compiled shim declares — named in that set or not —
    gets a spec and NON-EMPTY contents from the generic `container_spec()` /
    `container_contents()`, derived from its baked `gain_item`. That is what
    the Items page renders, so the exclusion list records only "the HAND table
    does not claim it", never "the app hides it".

    The derived spec is honest about what the app cannot evidence: `template`
    is False (no hand claim that the table rolls), and its rows carry each
    piece's OWN rarity/level rather than a box-rarity the app cannot support.
    """
    from farever_companion.data.items import sources
    containers = sources.compiled_container_facts()
    specs = sources.container_spec()
    contents = sources.container_contents()

    assert set(specs) >= set(containers), (
        f"containers with no spec (invisible to the Items page): "
        f"{sorted(set(containers) - set(specs))}")
    for cache_id, info in containers.items():
        # the derived table must be the one the shim records, never invented
        assert specs[cache_id]["table"] == info["gain"]["lootTable"], cache_id
        box = contents.get(cache_id)
        assert box, f"{cache_id} has no contents, so its Contains is empty"

    # the named starter is surfaced like any other, with the real pool
    z = specs["Z1_WeaponBundle"]
    assert z["table"] == "Z1_Start_WeaponWithAffinity" and z["template"] is False
    assert {r["label"] for r in contents["Z1_WeaponBundle"]} >= {
        "Credence", "Radiance", "Judgement"}


def test_roll_rarity_span_is_compiled_not_hand_recorded():
    """The cache rarity span is DERIVED from the game data, not hand-set.

    Only one container in the whole game dump clamps its roll (the Hero
    Weapon Cache's `gainItem.rarity = {min: "Epic"}`); the compiler bakes that
    clamp onto the container's drops row as `roll_rarity_min`, and
    `container_roll_span` reads it. This pins both halves so the span cannot
    quietly go back to a hand-recorded string a game patch could strand:

    - `_HEROIC_CACHE_SPECS` must name NO rarity span (the hand fact is gone);
    - the one clamp must be present on the compiled row and nowhere else;
    - `container_roll_span` must widen only a clamped box.
    """
    from farever_companion.data.items import catalog, sources

    for cid, spec in sources._HEROIC_CACHE_SPECS.items():
        assert "rarity_min" not in spec and "rarity_max" not in spec, cid

    items = sources._data().get("items", {})
    clamped = {iid for iid, row in items.items()
               if isinstance(row, dict) and row.get("roll_rarity_min")}
    assert clamped == {"HM_Weapon_Cache_25"}, clamped

    top = catalog.RARITY_ORDER[-1]
    specs = sources.heroic_cache_spec()
    assert specs["HM_Weapon_Cache_25"]["rarity_min"] == "Epic"
    assert specs["HM_Weapon_Cache_25"]["rarity_max"] == top
    for cid, spec in specs.items():
        if cid != "HM_Weapon_Cache_25":
            assert spec["rarity_min"] == spec["rarity_max"] == spec["rarity"], cid

    assert sources.container_roll_span({"rarity": "Rare"}) == ("Rare", "Rare")
    assert sources.container_roll_span(
        {"rarity": "Epic", "roll_rarity_min": "Epic"}) == ("Epic", top)
    assert sources.container_roll_span(
        {"rarity": "Rare", "roll_rarity_min": "Epic",
         "roll_rarity_max": "Legendary"}) == ("Epic", "Legendary")


def test_the_roll_span_collapses_to_the_tier_for_every_non_clamped_container():
    """The span is only worth printing where the sheet CLAMPS the roll.

    This is the answer to "what is the span for a drop? if they all have the
    same number it is junk", pinned to the compiled shim for EVERY container
    rather than for the six the app happens to spec. Read across the whole set:

    - a container with no `gainItem.rarity` clamp derives a POINT span equal
      to its own `rarity` — the tier the surface already shows — so a separate
      span would be a duplicate, not information;
    - exactly one container widens (the Hero Weapon Cache's Epic floor);
    - the compiler stamps the clamp onto the container's drops row generically
      (any clamped container, specced or not), while `_HEROIC_CACHE_SPECS`
      still only exposes the boxes it knows.

    So the non-Heroic boxes named in the request — the two Rift gear caches,
    the Rift gift cache and the starter "Mysterious Cache" (`Z1_WeaponBundle`)
    — are all points. A patch that clamps one of them fails here, and the box
    then has to be *specced* (via `_UNSPECCED_CONTAINERS`/`_HEROIC_CACHE_SPECS`)
    before its wider span can surface.
    """
    from farever_companion.data.items import catalog, sources

    containers = sources.compiled_container_facts()
    assert len(containers) >= 7, sorted(containers)
    specs = sources.heroic_cache_spec()
    items = sources._data().get("items", {})
    top = catalog.RARITY_ORDER[-1]

    widened: set[str] = set()
    for cid, info in containers.items():
        clamp = (info["gain"].get("rarity") or {}).get("min") or ""
        box_rar = info["rarity"]
        floor, ceiling = ((clamp, top) if clamp else (box_rar, box_rar))
        if floor != ceiling:
            widened.add(cid)

        # the compiler bakes the clamp onto the container's row for ANY box,
        # not just the specced ones — so a Rift/Mysterious clamp would be
        # visible to container_roll_span() even before it were specced
        row = items.get(cid) or {}
        if clamp:
            assert row.get("roll_rarity_min") == clamp, (cid, row)
        else:
            assert not row.get("roll_rarity_min"), (cid, row)

        # the app's own derivation agrees with the compiled facts it specs
        if cid in specs:
            got = (specs[cid]["rarity_min"], specs[cid]["rarity_max"])
            assert got == (floor, ceiling), (cid, got, (floor, ceiling))

    assert widened == {"HM_Weapon_Cache_25"}, sorted(widened)

    # the non-Heroic boxes the request named are all point spans == their tier
    for cid in ("Rift_Gear_Cache", "Rift_Weapon_Cache", "Rift_Gift_Cache",
                "Z1_WeaponBundle"):
        assert cid in containers, cid
        assert not ((containers[cid]["gain"].get("rarity") or {}).get("min")), cid
        assert cid not in widened, cid


def test_cache_contents_name_the_box_they_come_from():
    """A piece that only ever comes out of a box names that box on its card
    instead of reporting no sources — the Nightling Epic trinket rides
    HM_DemonGear with the 24 set pieces and has no drop row of its own."""
    from farever_companion.data.items import sources
    note = sources.no_source_reason("Trinket_Demon_E")
    # named by the LISTED name, so the piece points at the box a player can
    # actually find rather than at the name two different boxes share
    assert "Heroic Chaotic Gear Cache" in note and "25 pieces" in note, note
    # the box's own source is unrecorded, and the note must SAY that rather
    # than assert a Heroic boss drop the scan never captured
    assert "unreleased" in note, note
    for cache_id, box in sources.heroic_cache_contents().items():
        label = idata.item(cache_id)["name"]
        assert box, cache_id
        for row in box:
            got = sources.no_source_reason(row["item"])
            assert label in got, (cache_id, row["item"], got)


def test_merchants_view_lists_the_cache_vendors():
    """The Merchants chip lists every vendor the app can evidence, including
    the Heroic CACHE sellers whose boxes carry no `drops` row of their own.

    The Medal of Glory trader (Shiro James) sells the Hero Gear / Hero Weapon
    caches, but the sheet records no container drop, so building the vendor
    list from the index alone dropped his counter entirely. Its offer now
    comes from the same `cache_vendors()` fact the item page's Drops From
    uses — no hand-listed merchant — while Mira's caches keep their index rows
    exactly once (deduped, not doubled).
    """
    from farever_companion.data.items import sources
    names = sources.vendor_names()
    assert "Shiro James, the Medal of Glory vendor" in names
    assert "Mira, Demon Huntress" in names

    secs = {e["vendor"]: e for e in sources.vendor_sections()}
    shiro = secs["Shiro James, the Medal of Glory vendor"]
    stock = {(r["item"], idata.vendor_charge(r)) for r in shiro["rows"]}
    assert stock == {("HM_Gear_Cache_25", 100), ("HM_Weapon_Cache_25", 100)}

    mira_items = [r["item"] for r in secs["Mira, Demon Huntress"]["rows"]]
    for cid in ("Rift_Gear_Cache", "Rift_Weapon_Cache", "Rift_Gift_Cache"):
        assert mira_items.count(cid) == 1, (cid, mira_items.count(cid))
