"""Crafting + jobs data layer (headless, no game): craft.json recipes and
job.json professions — enrichment, filtering, cross-links.
"""
import pytest

from farever_companion import paths
from farever_companion.data import items as idata


def _data_present() -> bool:
    try:
        return paths.craft_path().exists() and paths.job_path().exists()
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _data_present(),
    reason="requires bundled data (assets/data/craft.json + job.json)")


def test_recipes_loaded():
    rs = idata.recipes()
    assert len(rs) == 190
    # every recipe output exists in the item DB (the crafted catalog)
    for r in rs:
        assert idata.item(r["item"]), r["item"]
    # grouped by job (craft order), level HIGHEST first within each job
    jobs = idata.craft_jobs()
    order = {j: i for i, j in enumerate(jobs)}
    assert [order[r["job"]] for r in rs] == sorted(order[r["job"]] for r in rs)
    seen = set()
    for r in rs:
        assert (r["job"], r["item"]) not in seen   # no dupes, grouped
        seen.add((r["job"], r["item"]))
    per_job = {}
    for r in rs:
        per_job.setdefault(r["job"], []).append(r["level"])
    for job, lvls in per_job.items():
        assert lvls == sorted(lvls, reverse=True), job


def test_first_craft_xp_formula():
    """Every recipe carries its first-craft job XP gain, from the game's
    Job_FirstCraft_XPFormula: XP = 120·level + 30·level² (repeat crafts
    grant nothing extra). The data has no per-recipe craft-point COST —
    only the gold `cost` — so XP is the point-side number we can show."""
    assert idata.first_craft_xp(1) == 150      # 120 + 30
    assert idata.first_craft_xp(2) == 360      # 240 + 120
    assert idata.first_craft_xp(3) == 630      # 360 + 270
    assert idata.first_craft_xp(6) == 1800     # 720 + 1080
    for r in idata.recipes():
        assert r["xp"] == idata.first_craft_xp(r["level"]), r["item"]
        assert r["xp"] > 0


def test_names_resolved():
    # item-DB names win (not the raw id)
    assert idata.recipe("Waist_RManfish_Fig")["name"] == "Caryapsid's Coccyx"
    # items-sheet / humanized fallback for materials outside the drop index
    assert idata.recipe("BronzeIngot")["name"] == "Bronze Ingot"


def test_per_job_counts():
    counts = {jid: sum(1 for r in idata.recipes(job=jid))
              for jid in idata.craft_jobs()}
    assert counts == {"Blacksmith": 22, "Alchemist": 41, "Outfitter": 22,
                      "Jeweller": 40, "Cook": 27, "Enchanter": 38}
    assert sum(counts.values()) == 190


def test_craft_jobs_and_levels():
    assert idata.craft_jobs() == ["Blacksmith", "Alchemist", "Outfitter",
                                  "Jeweller", "Cook", "Enchanter"]
    assert idata.craft_levels() == [1, 2, 3, 4, 5, 6]


def test_jobs():
    js = idata.jobs()
    assert len(js) == 10
    by_id = {j["id"]: j for j in js}
    assert by_id["Mining"]["name"] == "Miner"
    assert by_id["Blacksmith"]["recipes"] == 22
    assert by_id["Mining"]["recipes"] == 0          # gathering: no recipes
    assert by_id["Cook"]["tool"] == "Tool Cook"     # humanized toolType
    assert len([j for j in js if j["recipes"]]) == 6


def test_recipe_fields():
    b = idata.recipe("BronzeIngot")
    assert b["count"] == 2                      # output quantity
    assert b["level"] == 3 and b["job"] == "Blacksmith"
    assert b["materials"] == [
        {"item": "TinOre", "name": "Tin Ore", "count": 5, "crafted": False},
        {"item": "CopperIngot", "name": "Copper Ingot", "count": 1,
         "crafted": True},
        {"item": "SparkSample", "name": "Spark Sample", "count": 1,
         "crafted": False},
    ]
    assert b["gear"] is False and b["type"] == "CraftingComponent"
    assert b["loot"] == "SmallCP1"


def test_crafted_material_tag():
    # every material that is itself a recipe output is flagged `crafted`
    outs = {r["item"] for r in idata.recipes()}
    for r in idata.recipes():
        for m in r["materials"]:
            assert m["crafted"] == (m["item"] in outs), (r["item"], m["item"])
    # spot check: Copper Ingot is smelted, Tin Ore / Spark Sample are raw
    ing = next(m for r in idata.recipes() for m in r["materials"]
               if m["item"] == "CopperIngot")
    assert ing["crafted"] is True
    tin = next(m for r in idata.recipes() for m in r["materials"]
               if m["item"] == "TinOre")
    assert tin["crafted"] is False


def test_unlock_and_gear():
    # recipes gated behind recipe scrolls carry the unlock item
    honed = next(r for r in idata.recipes() if r.get("unlock"))
    assert honed["unlock_name"] and "Recipe" in honed["unlock"]
    # crafted gear outputs are flagged as gear; class-bound pieces carry
    # their classes, jewelry (trinket/ring/neck) is class-agnostic
    gear_outs = [r for r in idata.recipes() if r["gear"]]
    assert gear_outs
    for r in gear_outs:
        if r["classes"]:
            continue
        assert r["type"] in ("GearTrinket", "GearFinger", "GearNeck"), r


def test_recipe_unlocked_by_reverse_link():
    """Recipe items (type 'Recipe', id 'Recipe_*') are recipe scrolls — each
    is the unlockSource of exactly one craft recipe, so the item page can
    link a scroll straight to the recipe it teaches (the reverse of `unlock`)."""
    # a scroll unlocks the recipe whose output it names
    honed = idata.recipe_unlocked_by("Recipe_HonedCopperPlate")
    assert honed is not None
    assert honed["item"] == "HonedCopperPlate"
    assert honed["name"] == "Honed Bronze Plate"
    assert honed["job_name"] == "Blacksmith" and honed["level"] == 4
    # 1:1 both ways: every Recipe item unlocks one recipe, and every
    # unlock-bearing recipe is unlocked by a Recipe_ item
    unlocked = {r["unlock"]: r for r in idata.recipes() if r.get("unlock")}
    assert len(unlocked) == 73
    for it in idata.items():
        if it.get("type") != "Recipe":
            continue
        r = idata.recipe_unlocked_by(it["id"])
        assert r is not None, it["id"]
        assert unlocked[it["id"]]["item"] == r["item"]
    # non-recipe ids unlock nothing
    assert idata.recipe_unlocked_by("CopperIngot") is None
    assert idata.recipe_unlocked_by("") is None
    assert idata.recipe_unlocked_by("MissingItem_XYZ") is None


def test_craft_chain_expands_to_raw():
    c = idata.craft_chain("BronzeIngot")
    assert c == [
        {"item": "TinOre", "name": "Tin Ore", "count": 5,
         "depth": 1, "crafted": False},
        {"item": "CopperIngot", "name": "Copper Ingot", "count": 1,
         "depth": 1, "crafted": True},
        {"item": "CopperOre", "name": "Copper Ore", "count": 5,
         "depth": 2, "crafted": False},
        {"item": "SparkSample", "name": "Spark Sample", "count": 1,
         "depth": 2, "crafted": False},
        {"item": "SparkSample", "name": "Spark Sample", "count": 1,
         "depth": 1, "crafted": False},
    ]
    # every chain entry has a readable name and a sane count
    assert all(e["name"] and e["count"] >= 1 for e in c)


def test_craft_chain_depth_and_leaves():
    # the plated copper chains reach depth 4; leaves are all raw
    deep = idata.craft_chain("ReinforcedCopperPlate")
    assert deep and max(e["depth"] for e in deep) == 4
    assert any(e["crafted"] for e in deep)
    # every crafted entry's recipe inputs appear at depth+1 with scaled counts
    # (indices via enumerate — identical dicts compare equal, so list.index
    # would resolve the first occurrence of a shared material)
    for i, e in enumerate(deep):
        if not e["crafted"]:
            continue
        for m in idata.recipe(e["item"])["materials"]:
            child = next((x for j, x in enumerate(deep)
                          if j > i and x["depth"] == e["depth"] + 1
                          and x["item"] == m["item"]), None)
            assert child is not None, (e["item"], m["item"])
            assert child["count"] == e["count"] * m["count"]


def test_craft_bill_aggregates_batch():
    """The queue total: every material in the chain summed per item,
    scaled by the batch count — shared intermediates counted once."""
    b = idata.craft_bill("SmallAlchemistCauldron", 10)
    assert b is not None and b["qty"] == 10
    by = {i["item"]: i for i in b["items"]}
    # per craft: 4 elixirs each need 1 Vial → 40 for the batch of 10
    assert by["Vial"]["count"] == 40
    assert by["Vial"]["depth"] == 2              # shallowest position
    # Fresh Flower Powder (one per elixir + one per essence) → 5 per craft,
    # each needing 2 petals → 100 petals per batch
    assert by["FreshFlowerPowder"]["count"] == 50
    assert by["MadrigoldPetal"]["count"] == 100
    assert by["StrangeSpores"]["count"] == 90     # 4 elixirs ×2 + 1 essence
    assert by["SimpleCauldron"]["count"] == 10    # raw first-level input
    assert by["SimpleCauldron"]["crafted"] is False
    assert by["ElixirOfFaith_Z2"]["crafted"] is True
    assert b["raw_total"] == 590                   # gathered units only
    # rows sorted by depth, then name
    depths = [i["depth"] for i in b["items"]]
    assert depths == sorted(depths)
    assert b["gold"] == 500                        # 10 × the 50g recipe cost


def test_craft_bill_full_gold_includes_subcrafts():
    """Feast's gold total adds every intermediate craft run (Smoked Coyote,
    Skover Root Soup, Garbure, Pumpkin Pie at 30g each) to the root cost:
    2 feasts × 30g + 2 runs of each of the 4 dishes × 30g."""
    b = idata.craft_bill("Feast", 2)
    # 2 feasts × 30g root + 2 runs of each of the 4 dishes × 30g
    assert b["gold"] == 2 * 30 + 4 * 2 * 30


def test_craft_bill_scales_and_edges():
    b = idata.craft_bill("BronzeIngot", 5)
    by = {i["item"]: i for i in b["items"]}
    # 2 ingots per smelt: 5 ingots need 25 Tin Ore, 5 Copper Ingots (25 Ore),
    # and 10 Spark Sample (5 copper smelts + 5 bronze)
    assert by["TinOre"]["count"] == 25
    assert by["CopperOre"]["count"] == 25
    assert by["SparkSample"]["count"] == 10
    assert b["raw_total"] == 60
    # qty clamps to >= 1; non-craftable items expand to nothing
    assert idata.craft_bill("SmallAlchemistCauldron", 0)["qty"] == 1
    assert idata.craft_bill("TinOre") is None
    assert idata.craft_bill("MissingItem_XYZ", 4) is None
    # every recipe's bill is sane and deduped (one row per item)
    for r in idata.recipes():
        cb = idata.craft_bill(r["item"])
        assert cb is not None
        ids = [i["item"] for i in cb["items"]]
        assert len(ids) == len(set(ids)), r["item"]
        assert all(i["count"] >= 1 and i["name"] for i in cb["items"])


def test_craft_bill_many_sums_across_recipes():
    """The multi-recipe queue: several (item, qty) batches merged into one
    deduped bill — shared materials summed, shallowest depth kept, gold and
    raw totals added."""
    b = idata.craft_bill_many((("SmallAlchemistCauldron", 10),
                               ("BronzeIngot", 5)))
    assert b is not None
    by = {i["item"]: i for i in b["items"]}
    assert by["Vial"]["count"] == 40           # cauldron only
    assert by["MadrigoldPetal"]["count"] == 100
    assert by["TinOre"]["count"] == 25         # bronze only
    assert by["CopperOre"]["count"] == 25
    assert by["SparkSample"]["count"] == 60    # 50 cauldron + 10 bronze
    assert by["SparkSample"]["depth"] == 1     # shallowest across both
    assert b["gold"] == 500                     # 10 × 50g cauldron only
    assert b["raw_total"] == 590 + 60
    by_rec = {r["item"]: r for r in b["recipes"]}
    assert len(b["recipes"]) == 2
    assert by_rec["SmallAlchemistCauldron"]["qty"] == 10
    assert by_rec["SmallAlchemistCauldron"]["gold"] == 500
    assert by_rec["BronzeIngot"]["qty"] == 5
    assert by_rec["BronzeIngot"]["name"] == "Bronze Ingot"
    # same recipe twice merges into one material row
    two = idata.craft_bill_many((("BronzeIngot", 2), ("BronzeIngot", 3)))
    assert {i["item"]: i["count"] for i in two["items"]}["TinOre"] == 25
    # empty or nothing-craftable expands to nothing
    assert idata.craft_bill_many(()) is None
    assert idata.craft_bill_many((("TinOre", 3),)) is None


def test_is_craft_item():
    """The crafting-loop membership the Craft page uses: recipe outputs
    AND recipe materials are craft items; droppable leftovers (recipe
    scrolls, non-craft components, gold) are not."""
    assert idata.is_craftable("CopperIngot")           # smelted output
    assert idata.is_craftable("Cook_13")               # crafted food
    assert idata.is_craft_item("CopperIngot")
    assert idata.is_craft_item("CopperOre")            # material, not output
    assert idata.is_craft_item("MadrigoldPetal")       # plant material
    assert idata.is_craft_item("Cloth_Z1")             # cloth material
    # every recipe output is a craft item
    for r in idata.recipes():
        assert idata.is_craft_item(r["item"]), r["item"]
    # leftovers stay out: scrolls, Iron Ore (no recipe, unused), Gold
    assert not idata.is_craft_item("Recipe_HonedCopperPlate")
    assert not idata.is_craft_item("IronOre")
    assert not idata.is_craft_item("Gold")
    assert not idata.is_craft_item("MissingItem_XYZ")
    assert not idata.is_craft_item("")


def test_filters():
    assert all(r["job"] == "Cook" for r in idata.recipes(job="Cook"))
    assert all(r["level"] == 3 for r in idata.recipes(level=3))
    q = idata.recipes(query="coccyx")
    assert [r["item"] for r in q] == ["Waist_RManfish_Fig"]
    assert idata.recipes(query="blacksmith")            # job name matches
    assert idata.recipe("MissingItem_XYZ") is None


def test_recipes_using():
    # the reverse lookup — every recipe that consumes an item as a material,
    # enriched and grouped by job (craft order), level highest first
    uses = idata.recipes_using("CopperIngot")
    assert len(uses) == 5
    jobs = idata.craft_jobs()
    order = {j: i for i, j in enumerate(jobs)}
    assert [order[r["job"]] for r in uses] == sorted(order[r["job"]] for r in uses)
    lvls = [r["level"] for r in uses]
    assert lvls == sorted(lvls, reverse=True)
    # the consuming count per recipe matches the material list
    for r in uses:
        assert any(m["item"] == "CopperIngot" for m in r["materials"])
    # raw materials used nowhere (gathered fragments) come back empty
    assert idata.recipes_using("MissingItem_XYZ") == []
    assert idata.recipes_using("CopperIngot")[0]["job_name"] == "Blacksmith"
