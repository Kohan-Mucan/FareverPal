"""Crafting & gear-farm planner: craft queue + gear-farm list persistence."""
import pytest

from farever_companion import paths
from farever_companion.data import items as idata


def _data_present() -> bool:
    try:
        return paths.craft_path().exists() and paths.job_path().exists()
    except Exception:
        return False


_SECTION_MARK = pytest.mark.skipif(
    not _data_present(),
    reason="requires bundled data (assets/data/craft.json + job.json)")


@_SECTION_MARK
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


@_SECTION_MARK
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


@_SECTION_MARK
def test_names_resolved():
    # item-DB names win (not the raw id)
    assert idata.recipe("Waist_RManfish_Fig")["name"] == "Caryapsid's Coccyx"
    # items-sheet / humanized fallback for materials outside the drop index
    assert idata.recipe("BronzeIngot")["name"] == "Bronze Ingot"


@_SECTION_MARK
def test_per_job_counts():
    counts = {jid: sum(1 for r in idata.recipes(job=jid))
              for jid in idata.craft_jobs()}
    assert counts == {"Blacksmith": 22, "Alchemist": 41, "Outfitter": 22,
                      "Jeweller": 40, "Cook": 27, "Enchanter": 38}
    assert sum(counts.values()) == 190


@_SECTION_MARK
def test_craft_jobs_and_levels():
    assert idata.craft_jobs() == ["Blacksmith", "Alchemist", "Outfitter",
                                  "Jeweller", "Cook", "Enchanter"]
    assert idata.craft_levels() == [1, 2, 3, 4, 5, 6]


@_SECTION_MARK
def test_jobs():
    js = idata.jobs()
    assert len(js) == 10
    by_id = {j["id"]: j for j in js}
    assert by_id["Mining"]["name"] == "Miner"
    assert by_id["Blacksmith"]["recipes"] == 22
    assert by_id["Mining"]["recipes"] == 0          # gathering: no recipes
    assert by_id["Cook"]["tool"] == "Tool Cook"     # humanized toolType
    assert len([j for j in js if j["recipes"]]) == 6


@_SECTION_MARK
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


@_SECTION_MARK
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


@_SECTION_MARK
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


@_SECTION_MARK
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


@_SECTION_MARK
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


@_SECTION_MARK
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


@_SECTION_MARK
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


@_SECTION_MARK
def test_craft_bill_full_gold_includes_subcrafts():
    """Feast's gold total adds every intermediate craft run (Smoked Coyote,
    Skover Root Soup, Garbure, Pumpkin Pie at 30g each) to the root cost:
    2 feasts × 30g + 2 runs of each of the 4 dishes × 30g."""
    b = idata.craft_bill("Feast", 2)
    # 2 feasts × 30g root + 2 runs of each of the 4 dishes × 30g
    assert b["gold"] == 2 * 30 + 4 * 2 * 30


@_SECTION_MARK
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


@_SECTION_MARK
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


@_SECTION_MARK
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


@_SECTION_MARK
def test_filters():
    assert all(r["job"] == "Cook" for r in idata.recipes(job="Cook"))
    assert all(r["level"] == 3 for r in idata.recipes(level=3))
    q = idata.recipes(query="coccyx")
    assert [r["item"] for r in q] == ["Waist_RManfish_Fig"]
    assert idata.recipes(query="blacksmith")            # job name matches
    assert idata.recipe("MissingItem_XYZ") is None


@_SECTION_MARK
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


# ======================================================================
# test_gear_farm
# ======================================================================

import json
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtWidgets  # noqa: E402

from farever_companion.craft import planner as pdata  # noqa: E402
from farever_companion import paths  # noqa: E402
from farever_companion.data import items as idata  # noqa: E402
from farever_companion.ui import components as C  # noqa: E402
from farever_companion.ui.pages.items import ItemPageMixin  # noqa: E402
from farever_companion.ui.pages.items import support  # noqa: E402

# a boss-dropped shoulders piece (Assassin/Fighter, Nepsilon), a
# dual-class hauberk (Cleric/Fighter, world drop) and a crafted mantle
# (Assassin, no drop sources) — the farm-tab test fixtures
_SHOULDERS = "Shoulders_RManfish_FigAss"
_HAUBERK = "Chest_Z2U1_FigCle"
_MANTLE = "Shoulders_RDemon_Ass_Craft"


def _drops_data_present() -> bool:
    try:
        return paths.item_drops_path().exists()
    except Exception:
        return False


_SECTION_MARK_1 = pytest.mark.skipif(
    not _drops_data_present(),
    reason="requires bundled data (assets/data/item_drops.json)")


@pytest.fixture(autouse=True)
def _moddata(tmp_path, monkeypatch):
    """A throwaway config dir + a cold planner.json cache per test."""
    monkeypatch.setenv("FAREVER_MODDATA_DIR", str(tmp_path))
    pdata._data.cache_clear()
    yield
    pdata._data.cache_clear()


@pytest.fixture(scope="module", autouse=True)
def _qapp():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


# --- persistence ---------------------------------------------------------
@_SECTION_MARK_1
def test_gear_persistence_roundtrip():
    assert pdata.gear_item_ids() == []
    assert pdata.add_gear(_SHOULDERS) is True
    assert pdata.add_gear(_HAUBERK) is True
    assert pdata.gear_item_ids() == [_SHOULDERS, _HAUBERK]   # add order
    assert pdata.has_gear(_SHOULDERS)
    assert pdata.add_gear(_SHOULDERS) is False               # dup is a no-op
    assert pdata.gear_item_ids() == [_SHOULDERS, _HAUBERK]

    # the file on disk matches (crash-safe atomic write)
    path = pdata._path()
    assert json.loads(path.read_text(encoding="utf-8")) == \
        {"farm": [_SHOULDERS, _HAUBERK],
         "craft_queue": {"entries": [], "got": {}}}

    pdata.remove_gear(_SHOULDERS)
    assert pdata.gear_item_ids() == [_HAUBERK]
    pdata.clear_gear()
    assert pdata.gear_item_ids() == []
    assert json.loads(path.read_text(encoding="utf-8")) == \
        {"farm": [], "craft_queue": {"entries": [], "got": {}}}


@_SECTION_MARK_1
def test_owned_collection_is_cached_and_a_save_invalidates_it():
    """loadout.json holds the owned-collection list, and the Loadout tab asks
    about every tile it draws (the gear picker asks once per listed row). The
    parse is cached per path so those lookups stop re-reading the file — 192
    of them cost ~92 ms of config_dir() + open + parse — while every write
    through the helpers clears the cache, so a save is never one read stale.
    """
    assert pdata.is_in_owned_collection(_SHOULDERS) is False
    before = pdata._owned_data.cache_info()
    for _ in range(50):
        assert pdata.is_in_owned_collection(_SHOULDERS) is False
        assert pdata.load_global_loadout() == {}
    after = pdata._owned_data.cache_info()
    assert after.misses == before.misses            # 100 lookups, zero re-reads
    assert after.hits == before.hits + 100

    # the helpers' own writes are visible immediately
    assert pdata.add_to_owned_collection(_SHOULDERS) is True
    assert pdata.is_in_owned_collection(_SHOULDERS) is True
    assert pdata.add_to_owned_collection(_SHOULDERS) is False   # dup no-op
    assert pdata.load_global_loadout()["collection"] == [_SHOULDERS]
    pdata.remove_from_owned_collection(_SHOULDERS)
    assert pdata.is_in_owned_collection(_SHOULDERS) is False
    assert pdata.load_global_loadout()["collection"] == []
    # ...and so is a whole-save
    pdata.save_global_loadout({"collection": [_HAUBERK]})
    assert pdata.is_in_owned_collection(_HAUBERK) is True
    assert pdata.is_in_owned_collection(_SHOULDERS) is False


@_SECTION_MARK_1
def test_gear_corrupt_file_falls_back_and_rewrites(tmp_path, monkeypatch):
    monkeypatch.setenv("FAREVER_MODDATA_DIR", str(tmp_path))
    pdata._data.cache_clear()
    pdata._path().write_text("{not json", encoding="utf-8")
    assert pdata.gear_item_ids() == []          # falls back, doesn't crash
    pdata.add_gear(_SHOULDERS)                  # and rewrites the file
    assert json.loads(pdata._path().read_text(encoding="utf-8")) == \
        {"farm": [_SHOULDERS], "craft_queue": {"entries": [], "got": {}}}


@_SECTION_MARK_1
def test_craft_queue_persistence_roundtrip():
    """The crafting queue shares planner.json with the farm list: entries
    and gathered counts round-trip, and a farm write never clobbers the
    queue (or vice versa)."""
    assert pdata.queue_entries() == []
    assert pdata.queue_got() == {}
    pdata.queue_save(
        [{"item": "SmallAlchemistCauldron", "qty": 10,
          "name": "Minor Alchemist Cauldron"}],
        {"Vial": 40})
    assert pdata.queue_entries() == [
        {"item": "SmallAlchemistCauldron", "qty": 10,
         "name": "Minor Alchemist Cauldron"}]
    assert pdata.queue_got() == {"Vial": 40}
    # the farm list survives a queue save in the same file
    pdata.add_gear(_SHOULDERS)
    assert pdata.gear_item_ids() == [_SHOULDERS]
    assert pdata.queue_entries()[0]["item"] == "SmallAlchemistCauldron"
    payload = json.loads(pdata._path().read_text(encoding="utf-8"))
    assert payload == {
        "farm": [_SHOULDERS],
        "craft_queue": {
            "entries": [{"item": "SmallAlchemistCauldron", "qty": 10,
                          "name": "Minor Alchemist Cauldron"}],
            "got": {"Vial": 40}}}
    # an empty queue persists as empty state
    pdata.queue_save([], {})
    assert json.loads(pdata._path().read_text(encoding="utf-8")) == \
        {"farm": [_SHOULDERS], "craft_queue": {"entries": [], "got": {}}}

# --- the Farm tab (composed Items page) ----------------------------------
def _make_page():
    class Dummy(ItemPageMixin):
        def _page_container(self, title=None):
            page = QtWidgets.QWidget()
            v = QtWidgets.QVBoxLayout(page)
            v.setContentsMargins(22, 20, 22, 20)
            v.setSpacing(16)
            return page, v

        _codex_jump_to_unit = staticmethod(lambda *a: None)

    d = Dummy()
    page = d._page_gear()
    win = QtWidgets.QMainWindow()
    win.setCentralWidget(page)
    win.resize(1150, 760)
    win.show()
    return d, win


def _farm_body(d):
    return d._farm_lay.parentWidget()


def _content_inset(w, stop):
    """Pixels of ancestor border between `w` and `stop`, summed.

    The farm rows sit in a QSS-styled QFrame named "Cell", and the app
    stylesheet gives that frame a 1px border the moment it is installed - so
    a child's `x()` inside the Cell already counts it, while a column header
    in a bare QWidget does not. Summing the ancestors' frameWidth puts `w`'s
    CONTENT origin in `stop`'s coordinates, which is the thing a column header
    has to line up with.
    """
    inset = 0
    parent = w.parentWidget()
    while parent is not None and parent is not stop:
        if isinstance(parent, QtWidgets.QFrame):
            inset += parent.frameWidth()
        parent = parent.parentWidget()
    return inset


def test_farm_header_alignment_survives_a_bordered_row():
    """The farm column header must line up with the rows once they are BORDERED.

    The sibling test above compares the same two edges with the app in whatever
    state it inherits, and alphabetically this file runs before
    `test_settings_page.py`, so it measures an unthemed app - where the row's
    "Cell" frame has no border and a raw `x()` comparison happens to agree. The
    real app themes itself first and the app stylesheet gives #Cell a 1px
    border, which moves the rows' content origin 1px right and breaks the raw
    comparison. That is the whole order-dependency.

    The border is installed on the farm body rather than by `theme.apply()` on
    purpose. `theme.apply()` also registers the Roboto Condensed faces, and
    those cannot be unloaded - restoring the app font and stylesheet does not
    undo them - so a themed fixture in this file silently remeasures every
    test that runs after it. (It is not hypothetical: doing exactly that made
    `test_enchants_tab.py::test_infusion_row_names_are_plain_text_not_buttons`
    fail, because the MORE/LESS expander it looks for is itself width-driven.
    That test is a separate finding; this one stays hermetic.)
    """
    pdata.add_gear(_SHOULDERS)
    pdata.add_gear(_HAUBERK)
    d, win = _make_page()
    try:
        d._items_set_mode("Farm")
        for _ in range(6):
            QtWidgets.QApplication.processEvents()
        body = _farm_body(d)
        # the one thing the real app stylesheet does to #Cell that moves the
        # content origin - installed locally, restored below
        body.setStyleSheet("QFrame#Cell { border: 1px solid #2a2f34; }")
        QtWidgets.QApplication.processEvents()

        head = next(l for l in body.findChildren(QtWidgets.QLabel)
                    if l.text() == "ICON")
        tiles = body.findChildren(C.IconTile)
        assert tiles, "the Farm tab rendered no icon tiles"
        cell = tiles[0].parentWidget()
        assert isinstance(cell, QtWidgets.QFrame), (
            f"expected the tile to sit in a QFrame, got "
            f"{type(cell).__name__}; _content_inset counts frame borders and "
            f"would silently under-count")
        assert cell.frameWidth() >= 1, (
            f"the bordered Cell frame reports frameWidth="
            f"{cell.frameWidth()}, so this test is not exercising the 1px the "
            f"raw x() comparison was blind to - if the QSS stopped giving "
            f"#Cell a border, this test has stopped covering its own subject")
        # the premise: with the border installed the RAW comparison really does
        # disagree, which is the assertion that used to be order-dependent. If
        # this ever stops holding, the border stopped mattering and the
        # corrected comparison below is no longer the thing under test.
        assert all(t.x() != head.x() for t in tiles), (
            f"the bordered rows still start at the header's x ({head.x()}) - "
            f"nothing is being corrected here any more")
        assert all(t.x() - _content_inset(t, body) == head.x() for t in tiles)
    finally:
        win.close()
        win.deleteLater()
        QtWidgets.QApplication.processEvents()


def _farm_labels(d):
    return [l.text() for l in _farm_body(d).findChildren(QtWidgets.QLabel)
            if l.text()]


@_SECTION_MARK_1
def test_farm_tab_renders_rows_with_class_stats_boss():
    pdata.add_gear(_SHOULDERS)
    pdata.add_gear(_HAUBERK)
    idata.set_stat_display_mode("rounding")   # the header's default form
    d, win = _make_page()
    try:
        # the count is set at page build — it reads before opening the tab
        assert "· 2 Items" in d._items_tabs._btns["Farm"].text()
        d._items_set_mode("Farm")
        for _ in range(6):
            QtWidgets.QApplication.processEvents()
        lbls = _farm_labels(d)
        assert any("GEAR FARM" in t for t in lbls)
        # the count lives on the Farm tab, not in the header
        assert not any(t == "2 ITEMS" for t in lbls)
        tab_text = d._items_tabs._btns["Farm"].text()
        assert "· 2 Items" in tab_text
        # class tag -> item name -> base stats, boss on the right (each
        # class renders as its own colored pill)
        sh_row = next(w for w in _farm_body(d).findChildren(
            QtWidgets.QFrame) if w.objectName() == "Cell")
        sh_lbls = [l.text() for l in sh_row.findChildren(
            QtWidgets.QLabel) if l.text()]
        assert "Rogue" in sh_lbls and "Warrior" in sh_lbls, sh_lbls
        assert any(t == "Abyssal Shoulderplates" for t in lbls)
        # the row shows the clean tooltip integers the default Rounded form
        # renders — the exact floats are the STATS toggle's Exact mode, and
        # the retired parens form ('Armor 208 (207.x…)') left the toggle with
        # it. L25: dungeon-scaled gear previews at char max (heroic mode
        # drops it there), not the per-family dungeon level.
        assert any("Armor 208" in t and "Armor Penetration 11" in t
                   and "(" not in t for t in lbls)
        assert any("Nepsilon" in t for t in lbls)          # the bosses:
        assert any("Crabgantua" in t for t in lbls)        # one stack per
        assert any("Sponge Blob" in t for t in lbls)       # family boss
        assert any("Faith 2" in t and "Armor 144" in t for t in lbls)
        # each row shows its game icon (a real rendered tile) and the class
        tiles = _farm_body(d).findChildren(C.IconTile)
        assert len(tiles) == 2
        assert all(not t.pixmap().isNull() for t in tiles)
        # the column header strip (ICON | ITEM · CLASS | DROPS FROM) sits
        # above the rows, with the icon column aligned over the tiles and
        # DROPS FROM pinned over the boss column
        body = _farm_body(d)
        head_lbls = {l.text(): l for l in body.findChildren(QtWidgets.QLabel)
                     if l.text() in ("ICON", "ITEM · CLASS", "DROPS FROM")}
        assert set(head_lbls) == {"ICON", "ITEM · CLASS", "DROPS FROM"}
        # at the CONTENT origin, not the widget origin: the row's "Cell" frame
        # gains a 1px border as soon as the app stylesheet is installed, so
        # tile.x() is 1px right of a bare widget's x() once it is. That border
        # is the row's own frame rather than a header/row disagreement, and
        # theme.apply() is what installs it - which is why comparing raw x()
        # made this assertion pass or fail purely on which test module ran
        # first (test_settings_page.py themes the app; alphabetically it does
        # not run before this file).
        assert all(t.x() - _content_inset(t, body)
                   == head_lbls["ICON"].x() for t in tiles)
        row = next(w for w in body.findChildren(QtWidgets.QFrame)
                   if w.objectName() == "Cell")
        boss = next(l for l in row.findChildren(QtWidgets.QLabel)
                    if l.text() == "Sponge Blob")
        hdr_right = head_lbls["DROPS FROM"].x() + \
            head_lbls["DROPS FROM"].width()
        # the boss label lives inside the row's source cell, so its right
        # edge is mapped into row coordinates before comparing to the
        # header (both share the row's right 50px: trash 26 + spacing 12)
        boss_right = boss.mapTo(row, QtCore.QPoint(0, 0)).x() \
            + boss.width()
        assert abs(hdr_right - boss_right) <= 4
        # one trash button per row
        trash = [b for b in _farm_body(d).findChildren(QtWidgets.QPushButton)
                 if b.objectName() == "FarmRemove"]
        assert len(trash) == 2
    finally:
        win.close()
        idata.set_stat_display_mode("rounding")


@_SECTION_MARK_1
def test_farm_rows_show_drop_source_cell():
    """Every farm row's right column shows WHERE the piece comes from with
    an icon, so the DROPS FROM column is never empty: the boss's unit-sprite
    icon + gold name + dungeon sub-line for boss drops, the world-drop
    source (World Crates) for world gear, and a CRAFTED tag + the recipe
    job for crafted gear (no drop sources). The base stats split into two
    lines for pieces with 4+ stats."""
    pdata.add_gear(_SHOULDERS)     # boss Nepsilon · Manfish Ruins
    pdata.add_gear(_HAUBERK)       # world drop — World Crates
    pdata.add_gear(_MANTLE)        # crafted — no drops
    d, win = _make_page()
    try:
        d._items_set_mode("Farm")
        for _ in range(6):
            QtWidgets.QApplication.processEvents()
        body = _farm_body(d)
        rows = [w for w in body.findChildren(QtWidgets.QFrame)
                if w.objectName() == "Cell"]
        assert len(rows) == 3

        def row_labels(row):
            return [l.text() for l in row.findChildren(QtWidgets.QLabel)
                    if l.text()]

        def has_label(row, *parts):
            return any(all(p in t for p in parts)
                       for t in row_labels(row))

        # shoulders: one boss stack per family dungeon — sprite, gold name,
        # dungeon sub-line
        sh = next(r for r in rows if has_label(r, "Abyssal Shoulderplates"))
        rl = row_labels(sh)
        assert "Nepsilon" in rl and "Crabgantua" in rl and "Sponge Blob" in rl
        assert "Manfish Ruins" in rl and "Nepsid Boss" in rl \
            and "Manfish Abyss" in rl
        pics = [l for l in sh.findChildren(QtWidgets.QLabel)
                if l.pixmap() and not l.pixmap().isNull()]
        assert len(pics) == 4         # the item tile + 3 boss sprites

        # hauberk: the world-drop source with its kind icon (no boss)
        hb = next(r for r in rows if has_label(r, "Blessed Hauberk"))
        assert "World Crates" in row_labels(hb)

        # mantle: crafted — CRAFTED tag + job, and the stats split in 2
        mt = next(r for r in rows if has_label(r, "Demon Hunter"))
        rl = row_labels(mt)
        assert "CRAFTED · L25" in rl
        assert "OUTFITTER · LV 6" in rl
        st = next(l for l in mt.findChildren(QtWidgets.QLabel)
                  if "Dexterity 8" in l.text())
        assert "\n" in st.text()      # 'Dexterity 8 · Armor 166' /
        assert st.text().count("\n") == 1   #   'Critical 22 · Vitality 7'
    finally:
        win.close()


@_SECTION_MARK_1
def test_farm_class_filter_is_single_select_with_all():
    """The farm class chips are single-select over an All chip: picking a
    class shows only gear for it (replacing any previous pick), unticking
    or picking All restores every saved piece."""
    pdata.add_gear(_SHOULDERS)     # Assassin + Fighter
    pdata.add_gear(_HAUBERK)       # Cleric + Fighter
    d, win = _make_page()
    try:
        d._items_set_mode("Farm")
        for _ in range(6):
            QtWidgets.QApplication.processEvents()

        def chips():
            return {c.text(): c for c in
                    _farm_body(d).findChildren(C.FilterChip)}

        ch = chips()
        assert "All" in ch and ch["All"].isChecked()   # default = all

        # picking Priest narrows to the hauberk
        ch["Priest"].setChecked(True)
        for _ in range(6):
            QtWidgets.QApplication.processEvents()
        lbls = _farm_labels(d)
        assert any(t == "Blessed Hauberk of the Adventurer" for t in lbls)
        assert not any(t == "Abyssal Shoulderplates" for t in lbls)
        ch = chips()
        assert ch["Priest"].isChecked() and not ch["All"].isChecked()

        # picking Rogue REPLACES the pick (single-select): shoulders only
        ch["Rogue"].setChecked(True)
        for _ in range(6):
            QtWidgets.QApplication.processEvents()
        lbls = _farm_labels(d)
        assert any(t == "Abyssal Shoulderplates" for t in lbls)
        assert not any(t == "Blessed Hauberk of the Adventurer"
                       for t in lbls)
        ch = chips()
        assert ch["Rogue"].isChecked() and not ch["Priest"].isChecked()

        # picking All (or unticking the class) restores everything
        ch["All"].setChecked(True)
        for _ in range(6):
            QtWidgets.QApplication.processEvents()
        lbls = _farm_labels(d)
        assert any(t == "Abyssal Shoulderplates" for t in lbls)
        assert any(t == "Blessed Hauberk of the Adventurer" for t in lbls)
    finally:
        win.close()


@_SECTION_MARK_1
def test_farm_copy_button_exports_list_with_bosses():
    """The Farm tab's COPY button puts a markdown-flavored run list on the
    clipboard — '**GEAR FARM**' over one `- Name — Boss (Dungeon)` bullet
    per saved piece (world drops without a boss keep just the name),
    respecting the active class filter — and flashes ✓ COPIED."""
    pdata.add_gear(_SHOULDERS)     # boss: Nepsilon · Manfish Ruins
    pdata.add_gear(_HAUBERK)       # world drop, no boss
    d, win = _make_page()
    try:
        d._items_set_mode("Farm")
        for _ in range(6):
            QtWidgets.QApplication.processEvents()
        copy = next(b for b in _farm_body(d).findChildren(
            QtWidgets.QPushButton) if b.text() == "COPY")
        copy.click()
        clip = QtWidgets.QApplication.clipboard().text()
        lines = clip.splitlines()
        assert lines[0] == "**GEAR FARM**"
        assert ("- Abyssal Shoulderplates (Rogue · Warrior) — "
                "Crabgantua (Nepsid Boss) · Nepsilon (Manfish Ruins) · "
                "Sponge Blob (Manfish Abyss)") in clip
        assert "- Blessed Hauberk of the Adventurer (Priest · Warrior)" in clip
        non_empty = [ln for ln in clip.splitlines() if ln]
        assert non_empty[-1].startswith("Exported ")   # timestamp
        assert copy.text() == "✓ COPIED"

        # the class filter narrows the export too (Priest -> hauberk only)
        priest = next(c for c in _farm_body(d).findChildren(C.FilterChip)
                      if c.text() == "Priest")
        priest.setChecked(True)
        for _ in range(6):
            QtWidgets.QApplication.processEvents()
        copy.click()
        clip = QtWidgets.QApplication.clipboard().text()
        assert "- Blessed Hauberk of the Adventurer (Priest · Warrior)" in clip
        assert "Abyssal Shoulderplates" not in clip
    finally:
        win.close()


@_SECTION_MARK_1
def test_gear_detail_card_adds_to_farm_and_trash_removes():
    d, win = _make_page()
    try:
        # open the shoulders piece in the detail pane
        for i in range(d._items_list.count()):
            li = d._items_list.item(i)
            if li.data(support.ID_ROLE) == _SHOULDERS:
                d._items_list.setCurrentRow(i)
                break
        else:
            pytest.fail("shoulders piece not in the item list")
        for _ in range(6):
            QtWidgets.QApplication.processEvents()
        add = next(b for b in win.centralWidget().findChildren(
            QtWidgets.QPushButton) if b.text() == "ADD TO FARM")
        assert pdata.gear_item_ids() == []
        add.click()
        assert pdata.gear_item_ids() == [_SHOULDERS]
        assert add.text() == "✓ FARMED"     # flash state
        add.click()                          # re-add is a no-op
        assert pdata.gear_item_ids() == [_SHOULDERS]

        # the Farm tab shows it and the trash button removes + persists
        d._items_set_mode("Farm")
        for _ in range(6):
            QtWidgets.QApplication.processEvents()
        trash = next(b for b in _farm_body(d).findChildren(
            QtWidgets.QPushButton) if b.objectName() == "FarmRemove")
        trash.click()
        for _ in range(6):
            QtWidgets.QApplication.processEvents()
        assert pdata.gear_item_ids() == []
        assert any("Farm is empty" in t for t in _farm_labels(d))
    finally:
        win.close()


@_SECTION_MARK_1
def test_header_reflow_and_stats_mode_toggle():
    """The header bar reflows to [SEARCH BOX] [tick] [count] — the inline
    search box replaced the old SEARCH label — so the count sits next to the
    tick and the retired label is off the bar.

    The gear-stat display-mode toggle is NOT on that bar: it is the item
    card's ladder control, filling the STATS header cell of the UPGRADE
    LADDER matrix, so it is built with the card and only exists once an item
    is open. Clicking it cycles Rounded -> Exact -> Rounded and re-labels
    itself with its live sample, and the cycle holds exactly the two modes
    the button can name — the retired 'both' form is in neither, so no click
    can leave the display on a form the button can't show."""
    assert idata.stat_display_mode() == "rounding"
    d, win = _make_page()
    try:
        hdr = d._items_search_header
        lay = hdr.layout()
        # the search box leads the bar, then the tick, then the count
        assert lay.itemAt(0).widget() is d._items_search
        assert lay.indexOf(hdr._tag) == lay.indexOf(hdr._tick) + 1
        assert hdr._label not in [lay.itemAt(i).widget()
                                  for i in range(lay.count())]
        # nothing to toggle before a card exists — the button is the card's
        assert d._items_stat_mode_btn is None
        d._items_set_mode("Weapons")
        d._items_show_id("Axe_Boomerang")
        for _ in range(6):
            QtWidgets.QApplication.processEvents()
        btn = d._items_stat_mode_btn
        assert btn is not None and btn.isVisible()
        # it rides the card's stat block (the matrix's STATS cell) and is
        # not on the search bar
        assert d._items_stat_block.isAncestorOf(btn)
        assert lay.indexOf(btn) == -1
        # the button names the mode and shows its live sample; expected text
        # comes from the page's own table so this can't drift
        labels = d._STAT_MODE_LABELS
        # two forms, and the cycle names exactly those — the stale three-mode
        # cycle is what let the button and the rendered form drift apart
        assert set(d._STAT_MODE_CYCLE) == set(labels) == {"rounding", "true"}
        assert btn.text() == labels["rounding"]
        for expected in ("true", "rounding"):
            btn.click()
            for _ in range(6):
                QtWidgets.QApplication.processEvents()
            assert idata.stat_display_mode() == expected
            # the click re-renders the stat block, so the LIVE toggle is the
            # one the rebuild made — `btn` above is the retired instance
            btn = d._items_stat_mode_btn
            assert btn is not None and btn.isVisible()
            assert btn.text() == labels[expected]
            assert d._items_stat_block.isAncestorOf(btn)
    finally:
        win.close()
        idata.set_stat_display_mode("rounding")