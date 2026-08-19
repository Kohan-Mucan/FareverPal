"""The item scan's crafted/WorldLoot classification tripwires + the
extractor's Step 7 gate.

The scan lives in the sibling GameFiles checkout (Item Lookup/scan_item_drops.py),
NOT in this app - these tests load it from there and pin the two guard
functions so a silent mislabel can't reappear:

  - _flag_worldloot_craft_conflicts: WorldLoot-flagged gear labeled crafted
    (the zone-set mislabel) - the _Craft/RCraft token, recipe-bearing gear,
    recipe-bearing consumables (exempt: they drop normally) and Guild-
    Merchant-sold town weapons (exempt: shop gear, not crafted).
  - _flag_crafted_drop_conflicts: crafted (FIXED-level) gear that gained
    drop rows (the Beekeeper's Scarf bug - dungeon rows stamped on the
    crafted set variants).

test_extractor_gate_stops_pipeline_on_scan_conflict additionally pins the
Step 7 tripwire gate in automated_live_extract.ps1: it extracts the gate
LIVE from the real ps1 and runs it under PowerShell with a stub scanner,
so the pipeline behavior is tested, not copied.

The whole module skips cleanly when the GameFiles checkout (or PowerShell)
isn't present, so the app tests stay runnable everywhere.
"""
import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_SCAN_PATH = (Path(__file__).resolve().parents[1].parent
              / "GameFiles" / "Farever" / "Item Lookup" / "scan_item_drops.py")
_PS1_PATH = (_SCAN_PATH.parents[1] / "Extract_Live_Data_1Click"
             / "automated_live_extract.ps1")


def _load_scan():
    """The scan module, or None when the sibling GameFiles checkout is
    missing (module-level skip)."""
    if not _SCAN_PATH.exists():
        return None
    spec = importlib.util.spec_from_file_location("_scan_guard", _SCAN_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_SCAN = _load_scan()

pytestmark = pytest.mark.skipif(
    _SCAN is None,
    reason="requires the sibling GameFiles checkout (Item Lookup/scan_item_drops.py)")


# --- _flag_worldloot_craft_conflicts -----------------------------------


def test_worldloot_guard_flags_craft_tokened_gear():
    """A WorldLoot-flagged piece whose id carries the `_Craft`/`RCraft` token
    is the zone-set mislabel pattern: the sheet says the game generates it
    at runtime, the app would call it crafted. Exact reason string pinned."""
    sc = _SCAN
    items = {"Legs_RBee_FigWiz_Craft": {"type": "Legs", "flags": 2, "level": 10}}
    conf = sc._flag_worldloot_craft_conflicts(items, set(), [])
    assert conf == [("Legs_RBee_FigWiz_Craft",
                     "WorldLoot-flagged but app-crafted (_Craft/RCraft id token)")]


def test_worldloot_guard_flags_recipe_bearing_gear():
    """A WorldLoot-flagged GEAR piece with a producing recipe trips the
    guard (the app fixes any recipe-bearing gear as crafted)."""
    sc = _SCAN
    items = {"Chest_Honeycomb": {"type": "Chest", "flags": 2, "level": 15}}
    conf = sc._flag_worldloot_craft_conflicts(items, {"Chest_Honeycomb"}, [])
    assert conf == [("Chest_Honeycomb",
                     "WorldLoot-flagged but app-crafted (producing craft.json recipe)")]


def test_worldloot_guard_ignores_recipe_bearing_consumables():
    """Recipe-bearing CONSUMABLES carry the WorldLoot flag too but are not
    gear - the guard's gear gate keeps them out (they drop normally, and
    the app does not fix their level)."""
    sc = _SCAN
    items = {
        "Potion_Honey": {"type": "Consumable", "flags": 2, "level": 15},
        "Ingot_Copper": {"type": "Material", "flags": 2, "level": 5},
    }
    conf = sc._flag_worldloot_craft_conflicts(
        items, {"Potion_Honey", "Ingot_Copper"}, [])
    assert conf == []


def test_worldloot_guard_exempts_guild_merchant_stock():
    """The Guild Merchants (WanderingMerchant NPCs) sell `_Craft`-tokened
    town weapons scaled to each town - those are shop gear, NOT crafted, so
    a guild source clears the conflict; without it, the same item trips."""
    sc = _SCAN
    items = {"Sword_Craft": {"type": "Sword", "flags": 2, "level": 4}}
    npcs = [{"id": "WanderingMerchant_Tyrna", "loot": [{"item": "Sword_Craft"}]}]
    assert sc._flag_worldloot_craft_conflicts(items, set(), []) == [
        ("Sword_Craft", "WorldLoot-flagged but app-crafted (_Craft/RCraft id token)")]
    assert sc._flag_worldloot_craft_conflicts(items, set(), npcs) == []


def test_worldloot_guard_ignores_unflagged_and_recipe_ids():
    """Sanity: no WorldLoot flag -> no trip; `Recipe_*` ids carry the flag
    but are crafting drops, never gear (and have no level)."""
    sc = _SCAN
    items = {
        "Chest_Z1U1_Fig": {"type": "Chest", "level": 2},        # no flags
        "Recipe_CopperIngot": {"type": "Recipe", "flags": 2, "level": 1},
        "Sword_Basic": {"type": "Sword", "flags": 0, "level": 1},
    }
    assert sc._flag_worldloot_craft_conflicts(items, set(), []) == []


# --- _flag_crafted_drop_conflicts (inverse tripwire) -------------------


def test_crafted_drop_guard_flags_crafted_gear_with_rows():
    """Gear the app would FIX (crafted) that has drop rows is the
    Beekeeper's Scarf bug - the scan stamping dungeon rows onto crafted
    set variants. Recipe-bearing consumables drop normally, zone gear is
    not crafted, and guild-sold shop weapons are exempt."""
    sc = _SCAN
    items = {
        "Back_RBee_FigWiz_Craft": {"type": "Back", "level": 10},
        "Chest_Honeycomb": {"type": "Chest", "level": 15},
        "Potion_Honey": {"type": "Consumable", "level": 15},
        "Chest_Z1U1_Fig": {"type": "Chest", "level": 2},
        "Sword_Craft": {"type": "Sword", "level": 4},
    }
    idx = {
        "Back_RBee_FigWiz_Craft": {"drops": [{"source": "Gatsbee"}]},
        "Chest_Honeycomb": {"drops": [{"source": "WorldCrate"}]},
        "Potion_Honey": {"drops": [{"source": "Manfish mobs"}]},
        "Chest_Z1U1_Fig": {"drops": [{"source": "WorldCrate"}]},
        "Sword_Craft": {"drops": [{"source": "Guild Merchant"}]},
    }
    npcs = [{"id": "WanderingMerchant_Tyrna", "loot": [{"item": "Sword_Craft"}]}]
    conf = sc._flag_crafted_drop_conflicts(
        items, {"Chest_Honeycomb", "Potion_Honey"}, idx, npcs)
    by = {iid: reason for iid, reason in conf}
    assert "Back_RBee_FigWiz_Craft" in by         # _Craft token + row
    assert "Chest_Honeycomb" in by                # recipe gear + row
    assert "Potion_Honey" not in by               # recipe consumable drops normally
    assert "Chest_Z1U1_Fig" not in by             # zone gear is not crafted
    assert "Sword_Craft" not in by                # guild-sold shop gear
    assert "drop row(s)" in by["Back_RBee_FigWiz_Craft"]
    assert by["Chest_Honeycomb"].startswith("crafted (FIXED L15)")


# --- the real sheet must stay clean (the guards are tripwires) ---------


def test_guards_clean_on_real_sheet():
    """The live game data has zero conflicts today - both guards are pure
    tripwires and must NOT fire on the current extraction (skipped when the
    extracted database isn't in the GameFiles checkout)."""
    sc = _SCAN
    db = sc._find_database()
    if db is None:
        pytest.skip("no extracted database in the GameFiles checkout")
    items = {i.get("id"): i for i in sc._load_sheet(db, "item.json") if i.get("id")}
    recipes = {r.get("item") for r in sc._load_sheet(db, "craft.json") if r.get("item")}
    assert sc._flag_worldloot_craft_conflicts(items, recipes, []) == []
    assert sc._flag_crafted_drop_conflicts(items, recipes, {}, []) == []


# --- the extractor's Step 7 tripwire gate -------------------------------

_PWSH = shutil.which("powershell.exe") or shutil.which("pwsh")

ps1_skip = pytest.mark.skipif(
    _PWSH is None or not _PS1_PATH.exists(),
    reason="requires PowerShell + the sibling GameFiles checkout "
           "(Extract_Live_Data_1Click/automated_live_extract.ps1)")


@ps1_skip
def test_extractor_gate_stops_pipeline_on_scan_conflict(tmp_path):
    """Step 7 must hard-stop the whole extraction when the scan exits 2
    (classification conflict): the markdown/compact runs and the app copy
    must never happen. The gate block is extracted LIVE from the real ps1
    (not copied) and run under PowerShell with a stub scanner, so the
    pipeline behavior itself is pinned. Any non-zero scan exit aborts the
    pipeline; exit 0 continues."""
    ps1 = _PS1_PATH.read_text(encoding="utf-8")
    gate_start = ps1.index("    # Tripwire gate:")
    gate_end = ps1.index('    & $PythonExe "$ItemDropsScript" --markdown',
                         gate_start)
    gate = ps1[gate_start:gate_end]
    assert "exit 2" in gate and "classification conflict" in gate

    stub = tmp_path / "_stub_scan.py"
    stub.write_text("import os, sys\n"
                    "sys.exit(int(os.environ.get('STUB_EXIT', '2')))\n",
                    encoding="utf-8")
    harness = tmp_path / "_test_gate.ps1"
    harness.write_text(
        '$ItemDropsScript = "' + str(stub) + '"\r\n'
        '$ItemLookupDir   = "' + str(tmp_path) + '"\r\n'
        '$PythonExe       = "' + sys.executable + '"\r\n'
        '& $PythonExe "$ItemDropsScript" --output "$ItemLookupDir/item_drops.json"\r\n'
        + gate +
        "Write-Host 'REACHED-MARKDOWN-STEP'\r\n",
        encoding="utf-8")

    def _run(code):
        env = {**os.environ, "STUB_EXIT": str(code)}
        r = subprocess.run([_PWSH, "-NoProfile", "-ExecutionPolicy", "Bypass",
                            "-File", str(harness)],
                           capture_output=True, text=True, env=env, timeout=120)
        return r.returncode, (r.stdout or "") + (r.stderr or "")

    # classification conflict (exit 2): the pipeline stops, the markdown
    # step must never run, and the harness itself exits 2
    rc, out = _run(2)
    assert rc == 2, out
    assert "classification conflict detected" in out, out
    assert "Aborting extraction" in out, out
    assert "REACHED-MARKDOWN-STEP" not in out, out

    # generic scan failure (exit 1): same stop, different message
    rc, out = _run(1)
    assert rc == 2, out
    assert "failed with exit code 1" in out, out
    assert "REACHED-MARKDOWN-STEP" not in out, out

    # clean run (exit 0): the pipeline continues past the gate
    rc, out = _run(0)
    assert rc == 0, out
    assert "REACHED-MARKDOWN-STEP" in out, out
    assert "FATAL" not in out, out


# --- cache acquisition notes --------------------------------------------


def test_cache_note_grants_one_piece_for_player_scale_caches():
    """The Chaotic Gear/Weapon caches grant ONE piece at YOUR level —
    gainItem.maxItems (2) is a UI hint, not the count (the cache's own
    flavor text says 'a Nightling piece/weapon for your level'). The note
    names the player-scale chest and its level range — the count phrasing
    is dropped, so it stays short."""
    sc = _SCAN
    note = sc._cache_note("Chaotic Gear Cache", 2, {"min": 1, "max": 25})
    assert note == "Chaotic Gear Cache — player-scale chest (1-25)."
    assert "player-scale chest" in note and "(1-25)" in note
    assert "rolls" not in note and "your level" not in note
    assert "2 piece" not in note and "piece(s)" not in note
    assert sc._cache_note("Chaotic Weapon Cache", 2,
                          {"min": 1, "max": 25}) == (
        "Chaotic Weapon Cache — player-scale chest (1-25).")


def test_cache_note_plain_caches_pluralize_max_items():
    """Non-leveled caches keep the maxItems count with proper plural — the
    Gift cache's single piece reads '1 piece', never '1 piece(s)'."""
    sc = _SCAN
    assert sc._cache_note("Chaotic Gift Cache", 1) == \
        "Chaotic Gift Cache — rolls 1 piece."
    assert sc._cache_note("Some Cache", 2) == \
        "Some Cache — rolls 2 pieces."

def test_worldloot_notes_match_bytecode_semantics():
    """The WorldLoot-family notes must describe the real mechanic, not a
    fabricated one. Grounded in the game's bytecode (resolveLootItem /
    generateWorldLootItem / generateWorldRecipeItem):
      * WorldLoot              -> random generated gear (80/20 weapon/armor via
                                  Loot_FactionDropWeight, level mob-2..+1 via
                                  WorldLootLevel).
      * WorldLootWithAffinity  -> the 'affinity' flag only gates a
                                  hasUnitAptitude(item, hero) check: the piece
                                  is matched to the HERO's class aptitudes, not
                                  any dropper element.
      * WorldRecipeWithJob     -> the recipe is filtered by the HERO's known
                                  jobs (jobPossible/filterJob), not the
                                  dropper's profession.
    The old 'dropper's element affinity' / 'dropper's profession' wording
    misattributed the mechanic and must not come back.
    """
    notes = _SCAN._SPECIAL_ITEM_NOTES
    wl = notes["WorldLoot"]
    assert "80% armor / 20% weapon" in wl and "Loot_FactionDropWeight" in wl
    assert "mob -2..+1" in wl and "WorldLootLevel" in wl
    # short form — the wordy 'rolls a generated gear piece ... at loot time'
    # phrasing is gone for good
    assert "Rolls a generated gear piece" not in wl and "at loot time" not in wl

    aff = notes["WorldLootWithAffinity"]
    assert "dropper" not in aff.lower()
    assert "hero" in aff.lower() and "aptitude" in aff.lower()
    assert "hasUnitAptitude" in aff
    assert "Like WorldLoot" not in aff

    job = notes["WorldRecipeWithJob"]
    assert "dropper" not in job.lower()
    assert "your professions" in job.lower()
    assert "jobPossible" in job and "filterJob" in job
    assert "Rolls a random crafting recipe" not in job

    # and no note anywhere still misattributes to the dropper
    for n in notes.values():
        assert "dropper" not in n.lower(), n



def test_token_mechanics_stamps_documented_kinds():
    """_token_mechanics stamps the generator-token sources as documented
    source kinds so the app can mark world-generated drops by the mechanic:
      * source-less recipes -> 'worldrecipe' rows (WorldCrate 0.2 + Humanoid
        0.01, the token's real sources) with the jobPossible/filterJob note
      * WorldLoot-flagged zone gear -> 'worldloot_affinity' rows (the five
        zone activities + FoeUniqueDrops, chance-less - the per-activity pool
        is untraced) with the hasUnitAptitude note
      * vendor-stock recipes are NOT re-stamped; untagged rings are NOT
        stamped (the affinity gate's rating-aptitude path is untraced)
    """
    sc = _SCAN
    # constants documented with the bytecode-derived semantics
    assert set(sc._AFFINITY_ACTIVITY_TABLES) == {
        "WorldActivity", "ManfishActivity", "KoboldActivity", "BeeActivity",
        "CrimsonActivity"}
    assert sc._UNIQUE_FOE_TABLE == "FoeUniqueDrops"
    assert sc._RECIPE_TOKEN_SOURCES == (("WorldCrate", 0.2),
                                        ("Humanoid", 0.01))
    assert "hasUnitAptitude" in sc._AFFINITY_MECHANIC_NOTE
    assert "hero" in sc._AFFINITY_MECHANIC_NOTE.lower()
    assert "dropper" not in sc._AFFINITY_MECHANIC_NOTE.lower()
    # short form — the 'Generated by the ... loot token ...' lead-in is gone
    assert "Generated by the WorldLootWithAffinity loot token" \
        not in sc._AFFINITY_MECHANIC_NOTE
    assert "jobPossible" in sc._RECIPE_MECHANIC_NOTE
    assert "filterJob" in sc._RECIPE_MECHANIC_NOTE
    assert "dropper" not in sc._RECIPE_MECHANIC_NOTE.lower()
    assert "Generated by the WorldRecipeWithJob loot token" \
        not in sc._RECIPE_MECHANIC_NOTE

    items_idx = {
        "Recipe_BasicSword": {"name": "Sword Recipe", "drops": []},
        "Recipe_Vendored": {"name": "Vendor Recipe", "drops": []},
        "Chest_Z1U1_Fig": {"name": "Zone Chest", "drops": []},
        "Finger_Cri": {"name": "Crit Ring", "drops": []},
    }
    items = {
        "Recipe_BasicSword": {"id": "Recipe_BasicSword"},
        "Recipe_Vendored": {"id": "Recipe_Vendored"},
        "Chest_Z1U1_Fig": {"id": "Chest_Z1U1_Fig", "flags": 2, "level": 5},
        "Finger_Cri": {"id": "Finger_Cri", "flags": 2, "level": 10},
    }

    def _act(tid):
        return {"kind": "chest", "id": tid,
                "name": tid.replace("Activity", " Activities"),
                "location_label": tid + " label", "loc_short": tid + " label",
                "loot": [{"item": "WorldLootWithAffinity", "proba": 1.0}]}

    chest_sources = [
        {"kind": "chest", "id": "WorldCrate", "name": "World Crates (x10)",
         "location_label": "World crates (any zone)",
         "loc_short": "World crates (any zone)",
         "loot": [{"item": "WorldRecipeWithJob", "proba": 0.2},
                  {"item": "WorldLoot", "proba": 0.35}]},
    ] + [_act(t) for t in ("WorldActivity", "ManfishActivity",
                           "KoboldActivity", "BeeActivity",
                           "CrimsonActivity")]
    unit_sources: list[dict] = []
    npc_sources = [{"kind": "npc", "id": "Zoey", "name": "Zoey",
                    "loot": [{"item": "Recipe_Vendored", "proba": 1.0}]}]
    sc._token_mechanics(items_idx, items, chest_sources, unit_sources,
                        npc_sources)

    r = items_idx["Recipe_BasicSword"]["drops"]
    assert {d["kind"] for d in r} == {"worldrecipe"}
    assert any(d["table"] == "WorldCrate" and d["proba"] == 0.2 for d in r)
    assert any(d["table"] == "Humanoid" and d["proba"] == 0.01 for d in r)
    assert "jobPossible" in items_idx["Recipe_BasicSword"]["note"]

    # vendor stock stays untouched; untagged rings stay untouched
    assert items_idx["Recipe_Vendored"]["drops"] == []
    assert items_idx["Finger_Cri"]["drops"] == []

    z = items_idx["Chest_Z1U1_Fig"]["drops"]
    assert len(z) == 6  # the 5 activities + FoeUniqueDrops
    assert {d["kind"] for d in z} == {"worldloot_affinity"}
    assert all(d["proba"] is None for d in z)  # chance-less
    assert "hasUnitAptitude" in items_idx["Chest_Z1U1_Fig"]["note"]

    # synthetic mechanic-kind sources resolve the rows' locations in the
    # compact form (src_short keys on (kind, source id))
    assert any(s["kind"] == "worldrecipe" and s["id"] == "Humanoid"
               for s in unit_sources)
    assert any(s["kind"] == "worldloot_affinity"
               and s["id"] == "FoeUniqueDrops" for s in unit_sources)
    assert any(s["kind"] == "worldrecipe" and s["id"] == "WorldCrate"
               for s in chest_sources)
    assert any(s["kind"] == "worldloot_affinity" and s["id"] == "WorldActivity"
               for s in chest_sources)


