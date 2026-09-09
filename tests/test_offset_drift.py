"""Drift-audit hardening: reflected element/item/health offsets.

2026-09-18 audit of constants.py (the OFF_ELEM_FX lesson): OFF_ELEM_FX drifted
in that day's patch and a garbage read at the stale slot marked every orb
collected. This extends the same trust semantics to the other hardcoded
element/unit layout offsets the scene reads:

- ent.Element.kind / currentVisualState  (scene._scan_elements)
- ent.interactible.LootDrop.item/count   (scene.loot_drops)
- st.Item.kind                           (scene.loot_drops)
- the WorldConsumable skill slot         (scene.food_stations)
- *Attributes health                     (attributes.health / the batched scan)

Rules under test: a moved field still reads true (reflected wins); a healthy
build keeps the batched fast path; an unresolvable layout degrades to 'no
data', never a confident lie.
"""
from __future__ import annotations

import pytest

from fakemem import FakeProc, HeapBuilder

hl_mod = pytest.importorskip("farever_companion.core.hl")
scene_mod = pytest.importorskip("farever_companion.core.scene")
from farever_companion.core.attributes import health, health_offset, max_health
from farever_companion.core.hl import Hl
from farever_companion.core.scene import (
    Scene, OFF_GAMELAYER, OFF_ELEMS_ARR, OFF_POS, OFF_ELEMID, OFF_ELEMSTATE,
    OFF_UATTR, OFF_HEALTH,    OFF_LOOT_ITEM, OFF_LOOT_COUNT, OFF_ITEM_KIND,
    OFF_CONFIG_CANDIDATES, OFF_CONFIG_MAPID_CANDIDATES, OFF_CONFIG_MAPID,
    OFF_CONFIG_DIFF_CANDIDATES, OFF_CONFIG_DIFFICULTY, OFF_BOX_VALUE,
)

# the "patch moved the field" layouts used by the moved-field tests.
# NB: HeapBuilder writes fields_indexes in dict insertion order and the
# resolver only accepts strictly ascending tables, so entries must be listed
# by ASCENDING offset, not by importance.
ELEM_MOVED = {"pad": 0x8, "kind": 0x2a0, "currentFx": 0x2e8,
              "currentVisualState": 0x2f0}
LOOT_MOVED = {"pad": 0x8, "players": 0x2a0, "item": 0x2b0, "count": 0x2b8}
ITEM_MOVED = {"pad": 0x8, "kind": 0x88}
FOOD_MOVED = {"pad": 0x8, "skill": 0x2a0, "currentFx": 0x2b0}
ATTR_MOVED = {"pad": 0x8, "hp": 0x108}
ATTR_WITH_MAX = {"pad": 0x8, "hp": 0x108, "maxHp": 0x110}


def _world(*, elem_layout=None, loot_layout=None, item_layout=None,
           food_layout=None):
    """A minimal layer: one Gatherable, one LootDrop (+ st.Item), one
    WorldConsumable food station. The optional layouts simulate a patch that
    moved the fields; the values at the constants are left as stale-slot
    decoys so a constant-only read would return garbage."""
    proc = FakeProc()
    hb = HeapBuilder(proc)
    hb.make_type("ent.Hero")
    hb.make_type("ent.interactible.Gatherable", fields=elem_layout)
    hb.make_type("ent.interactible.LootDrop", fields=loot_layout)
    hb.make_type("st.Item", fields=item_layout)
    hb.make_type("st.skill.object.WorldConsumable", fields=food_layout)
    hb.make_type("st.skill.Skill")

    gather = hb.make_instance("ent.interactible.Gatherable")
    proc.put_f64(gather + OFF_POS, 1.0)
    proc.put_u64(gather + OFF_ELEMID, hb.make_string("Ore_Tungstene"))
    proc.put_u64(gather + OFF_ELEMSTATE, hb.make_string("Full"))

    drop = hb.make_instance("ent.interactible.LootDrop")
    proc.put_f64(drop + OFF_POS, 2.0)
    item = hb.make_instance("st.Item")
    proc.put_u64(item + OFF_ITEM_KIND, hb.make_string("MoteOfChaos"))
    proc.put_u64(drop + OFF_LOOT_ITEM, item)
    proc.put_i32(drop + OFF_LOOT_COUNT, 3)

    food = hb.make_instance("st.skill.object.WorldConsumable")
    proc.put_f64(food + OFF_POS, 3.0)
    skill = hb.make_instance("st.skill.Skill")
    proc.put_u64(skill + 0x08, hb.make_string("Plainswalker Feast"))
    proc.put_u64(food + OFF_ELEMID, skill)

    gl = hb.alloc(0x200)
    proc.put_u64(gl + OFF_ELEMS_ARR, hb.make_array([gather, drop, food]))
    player = hb.make_instance("ent.Hero")
    proc.put_u64(player + OFF_GAMELAYER, gl)
    return proc, hb, Scene(proc, Hl(proc)), player, gather, drop, food, item


def _gather(els):
    return next(e for e in els if e.cls == "ent.interactible.Gatherable")


# --- ent.Element.kind / currentVisualState --------------------------------

def test_elem_fields_read_at_constants_when_layout_matches():
    proc, _hb, scene, player, *_ = _world()
    g = _gather(scene.elements(player, fresh=True))
    assert g.elem_id == "Ore_Tungstene"
    assert g.state == "Full"


def test_elem_kind_moved_field_still_reads_true():
    """A patch moves kind to 0x2a0 and leaves garbage at the old slot: the
    reflected offset wins and the id is still read."""
    proc, hb, scene, player, gather, _d, _f, _i = _world(
        elem_layout=ELEM_MOVED)
    proc.put_u64(gather + ELEM_MOVED["kind"], hb.make_string("Ore_Tungstene_MOVED"))
    proc.put_u64(gather + OFF_ELEMID, 0xDEAD)          # stale slot garbage
    g = _gather(scene.elements(player, fresh=True))
    assert g.elem_id == "Ore_Tungstene_MOVED"


def test_elem_state_moved_field_still_reads_true():
    proc, hb, scene, player, gather, _d, _f, _i = _world(
        elem_layout=ELEM_MOVED)
    proc.put_u64(gather + ELEM_MOVED["currentVisualState"], hb.make_string("Harvested"))
    g = _gather(scene.elements(player, fresh=True))
    assert g.state == "Harvested"


def test_elem_id_stale_slot_non_string_is_no_data():
    """No runtime layout at all (reflection unavailable): the constant is the
    only path — and when IT holds a non-string, the field is None."""
    proc, _hb, scene, player, gather, _d, _f, _i = _world()
    proc.put_u64(gather + OFF_ELEMID, 0x1234)   # not a plausible pointer
    g = _gather(scene.elements(player, fresh=True))
    assert g.elem_id is None


def test_elem_scan_batched_block_survives_when_offsets_healthy():
    """The fast path: with the constants correct, the element walk is still
    ONE batched read (no per-element field reads)."""
    proc, _hb, scene, player, *_ = _world()
    state = {"n": 0}
    orig = proc.read_many

    def f(*a, **k):
        state["n"] += 1
        return orig(*a, **k)

    proc.read_many = f
    scene.elements(player, fresh=True)
    assert state["n"] == 1


# --- LootDrop.item / count / st.Item.kind ----------------------------------

def test_loot_fields_read_at_constants():
    proc, _hb, scene, player, _g, _d, _f, _i = _world()
    (drop,) = scene.loot_drops(player)
    assert drop.item_id == "MoteOfChaos"
    assert drop.count == 3


def test_loot_item_moved_field_still_reads_true():
    proc, hb, scene, player, _g, drop, _f, item = _world(
        loot_layout=LOOT_MOVED, item_layout=ITEM_MOVED)
    proc.put_u64(item + ITEM_MOVED["kind"], hb.make_string("MoteOfChaos_MOVED"))
    proc.put_u64(drop + LOOT_MOVED["item"], item)
    proc.put_u64(drop + OFF_LOOT_ITEM, 0xDEAD)         # stale slot
    (d,) = scene.loot_drops(player)
    assert d.item_id == "MoteOfChaos_MOVED"


def test_loot_count_moved_field_still_reads_true():
    proc, hb, scene, player, _g, drop, _f, _i = _world(loot_layout=LOOT_MOVED)
    proc.put_i32(drop + LOOT_MOVED["count"], 7)
    proc.put_i32(drop + OFF_LOOT_COUNT, 999999)        # stale slot junk
    (d,) = scene.loot_drops(player)
    assert d.count == 7


def test_loot_count_junk_degrades_to_one():
    """The moved field reads out-of-range AND the stale constant slot is
    poisoned: the authoritative reflected read yields 'no data' -> count 1.
    (The constant is never consulted once reflection resolved the field.)"""
    proc, hb, scene, player, _g, drop, _f, _i = _world(loot_layout=LOOT_MOVED)
    proc.put_i32(drop + LOOT_MOVED["count"], -50)      # out of range = no data
    proc.put_i32(drop + OFF_LOOT_COUNT, 999999)        # stale slot junk
    (d,) = scene.loot_drops(player)
    assert d.count == 1


# --- placed food skill slot -------------------------------------------------

def test_food_skill_slot_moved_field_still_resolves_name():
    proc, hb, scene, player, _g, _d, food, _i = _world(food_layout=FOOD_MOVED)
    skill = hb.make_instance("st.skill.Skill")
    proc.put_u64(skill + 0x08, hb.make_string("Plainswalker Feast"))
    proc.put_u64(food + FOOD_MOVED["skill"], skill)
    proc.put_u64(food + OFF_ELEMID, 0xBEEF)            # stale slot
    (station,) = scene.food_stations(player)
    assert station.name == "Plainswalker Feast"


# --- *Attributes health ------------------------------------------------------

def _unit_with_attr(hb, proc, *, moved=False):
    """A unit -> OFF_UATTR -> attributes object. `moved` puts hp at 0x108 and
    leaves a NaN decoy at the constant.

    NB: the typed layout must exist BEFORE make_instance — make_type caches
    by name, so a bare type created first would win and the instance would
    carry a type with no runtime layout."""
    if moved:
        hb.make_type("ent.UnitAttributes", fields=ATTR_MOVED)
    else:
        hb.make_type("ent.UnitAttributes")             # bare: no runtime layout
    attr = hb.make_instance("ent.UnitAttributes")
    if moved:
        proc.put_f64(attr + ATTR_MOVED["hp"], 4321.0)
        proc.put_f64(attr + OFF_HEALTH, float("nan"))
    else:
        proc.put_f64(attr + OFF_HEALTH, 1234.0)
    unit = hb.make_instance("ent.Hero")
    proc.put_u64(unit + OFF_UATTR, attr)
    return unit, attr


def test_health_offset_reflected_when_field_moved():
    proc = FakeProc()
    hb = HeapBuilder(proc)
    unit, attr = _unit_with_attr(hb, proc, moved=True)
    hl = Hl(proc)
    assert health_offset(hl, attr) == ATTR_MOVED["hp"]
    assert health(hl, unit) == 4321.0


def test_health_offset_falls_back_to_constant():
    proc = FakeProc()
    hb = HeapBuilder(proc)
    unit, attr = _unit_with_attr(hb, proc)             # bare type, constant ok
    hl = Hl(proc)
    assert health_offset(hl, attr) == OFF_HEALTH
    assert health(hl, unit) == 1234.0


def test_health_offset_cached_per_object():
    """Second ask for the same attributes object must not re-read the type
    pointer — the scene's constant-read budget depends on it."""
    proc = FakeProc()
    hb = HeapBuilder(proc)
    _unit, attr = _unit_with_attr(hb, proc, moved=True)
    hl = Hl(proc)
    health_offset(hl, attr)
    calls = {"n": 0}
    orig = hl.ptr

    def f(*a, **k):
        calls["n"] += 1
        return orig(*a, **k)

    hl.ptr = f
    health_offset(hl, attr)
    health_offset(hl, attr)
    assert calls["n"] == 0


def test_health_unreadable_when_stale_slot_reads_nan():
    proc = FakeProc()
    hb = HeapBuilder(proc)
    unit, _attr = _unit_with_attr(hb, proc, moved=True)
    # drop the moved value so BOTH slots are junk -> decode_health rejects
    # (a unit with no readable HP reads as None, never a fabricated number)
    assert health(Hl(proc), unit) == 4321.0            # reflected slot wins


def test_max_health_is_reflected_and_has_no_fallback():
    proc = FakeProc()
    hb = HeapBuilder(proc)
    hb.make_type("ent.UnitAttributes", fields=ATTR_WITH_MAX)
    attr = hb.make_instance("ent.UnitAttributes")
    proc.put_f64(attr + ATTR_WITH_MAX["hp"], 90.0)
    proc.put_f64(attr + ATTR_WITH_MAX["maxHp"], 100.0)
    unit = hb.make_instance("ent.Hero")
    proc.put_u64(unit + OFF_UATTR, attr)
    assert max_health(Hl(proc), unit) == 100.0

    # A bare attributes type has no reflected max field. The reader must return
    # unknown rather than borrowing current HP and calling the boss pristine.
    proc2 = FakeProc()
    hb2 = HeapBuilder(proc2)
    unit2, _attr2 = _unit_with_attr(hb2, proc2)
    assert max_health(Hl(proc2), unit2) is None


# --- GameLayer.config slot + st.Config layout (2026-09-19) ------------------
# The 2026-09-19 build moved GameLayer.config: the calibrated candidate (0xc8)
# landed on an unrelated object, so map_id/difficulty both went dead while the
# element walk stayed healthy. These pin the reflection-first resolver: the
# `config` field resolves BY NAME, the mapId/difficulty fields inside it resolve
# by name too, and the constants remain the fallback for a build whose type
# tables carry no layout.

# the "patch moved it" layouts. offsets must strictly ascend (HeapBuilder
# writes fields_indexes in insertion order and the resolver rejects an
# unsorted table), hence the leading pad.
GL_CONFIG_MOVED = {"pad": 0x8, "config": 0xd0, "mainActivity": 0x100}
CONFIG_MOVED = {"pad": 0x8, "mapId": 0x90, "difficulty": 0x180}

# The decoy config's slot. Must be one of OFF_CONFIG_CANDIDATES (it exists to
# prove the reflected slot beats the calibrated list) but must NOT overlap the
# real config slot at 0xd0: a qword write at 0xcc spans 0xcc..0xd3 and would
# clobber the low half of the real pointer at 0xd0. 0xc8 is the current
# non-overlapping candidate.
_DECOY_SLOT = 0xc8
assert _DECOY_SLOT in OFF_CONFIG_CANDIDATES
assert _DECOY_SLOT + 8 <= GL_CONFIG_MOVED["config"]


def _config_world(*, gl_layout=GL_CONFIG_MOVED, cfg_layout=CONFIG_MOVED,
                  at=0xd0, decoy_at=None, stale_decoys=False):
    """A GameLayer with a real config: mapId + boxed difficulty at the MOVED
    offsets, plus (optionally) a valid-looking decoy config at the constant
    candidate slot and/or stale-slot decoys inside the config itself, so
    "reflected wins" is a choice, not a coincidence."""
    proc = FakeProc()
    hb = HeapBuilder(proc)
    # NB: declare st.Config BEFORE st.GameLayer — make_type dedupes by name and
    # would otherwise create it bare (no layout) while resolving `field_types`.
    hb.make_type("st.Config", fields=cfg_layout,
                 field_types={"mapId": "String"})
    hb.make_type("st.GameLayer", fields=gl_layout,
                 field_types={"config": "st.Config"})
    hb.make_type("ent.Hero")
    gl = hb.make_instance("st.GameLayer", size=0x200)
    player = hb.make_instance("ent.Hero")
    proc.put_u64(player + OFF_GAMELAYER, gl)

    cfg = hb.make_instance("st.Config")
    move = cfg_layout or {}
    mid = move.get("mapId") or OFF_CONFIG_MAPID_CANDIDATES[0]
    diff = move.get("difficulty") or OFF_CONFIG_DIFF_CANDIDATES[0]
    proc.put_u64(cfg + mid, hb.make_string("POI_Moved_Z9"))
    box = hb.alloc(0x10)
    proc.put_i32(box + OFF_BOX_VALUE, 1)
    proc.put_u64(cfg + diff, box)
    if stale_decoys:
        # plausible values at the calibrated offsets: a constant-only read
        # would answer "POI_Decoy_Z1" / Normal here.
        proc.put_u64(cfg + OFF_CONFIG_MAPID_CANDIDATES[0],
                     hb.make_string("POI_Decoy_Z1"))
        proc.put_u64(cfg + OFF_CONFIG_MAPID, hb.make_string("POI_Decoy_Z1"))
        dbox = hb.alloc(0x10)
        proc.put_i32(dbox + OFF_BOX_VALUE, 0)
        proc.put_u64(cfg + OFF_CONFIG_DIFF_CANDIDATES[0], dbox)
        proc.put_u64(cfg + OFF_CONFIG_DIFFICULTY, dbox)
    if at is not None:
        proc.put_u64(gl + at, cfg)

    if decoy_at is not None:
        decoy = hb.make_instance("st.Config")
        proc.put_u64(decoy + OFF_CONFIG_MAPID_CANDIDATES[0],
                     hb.make_string("POI_Decoy_Z1"))
        dbox = hb.alloc(0x10)
        proc.put_i32(dbox + OFF_BOX_VALUE, 0)
        proc.put_u64(decoy + OFF_CONFIG_DIFF_CANDIDATES[0], dbox)
        proc.put_u64(gl + decoy_at, decoy)
    return proc, hb, Scene(proc, Hl(proc)), player


def test_config_slot_and_fields_resolve_by_reflection_when_moved():
    """The whole 2026-09-19 break, in one test: `config` sits at a new slot AND
    mapId/difficulty sit at new offsets inside it. Both reads must come from the
    reflected layout."""
    proc, _hb, scene, player = _config_world(decoy_at=_DECOY_SLOT)
    assert scene.map_id(player) == "POI_Moved_Z9"      # not the decoy slot
    assert scene.difficulty(player) == 1               # not the decoy's Normal
    assert scene._cfg_off == GL_CONFIG_MOVED["config"]
    assert scene.instance_probe(player)["cfg_type"] == "st.Config"


def test_config_reflected_slot_beats_a_valid_constant_slot_decoy():
    """Ordering proof: with a *validating* config at the constant candidate and
    the real one at the reflected slot, the reflected slot wins."""
    proc, hb, scene, player = _config_world(decoy_at=_DECOY_SLOT)
    decoy = proc.u64(scene.gamelayer(player) + _DECOY_SLOT)
    real = proc.u64(scene.gamelayer(player) + GL_CONFIG_MOVED["config"])
    assert decoy != real
    assert scene.map_id(player) == "POI_Moved_Z9"


def test_config_fields_reflected_when_only_the_layout_moved():
    """Slot unchanged (the constant candidate is right), struct layout moved:
    the reflected mapId/difficulty win over valid-looking decoys at the old
    offsets."""
    gl_layout = {"pad": 0x8, "config": OFF_CONFIG_CANDIDATES[0],
                 "mainActivity": 0x100}
    cfg_layout = {"pad": 0x8, "mapId": 0x90, "difficulty": 0x180}
    proc, hb, scene, player = _config_world(
        gl_layout=gl_layout, cfg_layout=cfg_layout,
        at=OFF_CONFIG_CANDIDATES[0], stale_decoys=True)
    cfg = scene.hl.ptr(scene.gamelayer(player) + OFF_CONFIG_CANDIDATES[0])
    stale = scene.hl.hl_string(
        scene.hl.ptr(cfg + OFF_CONFIG_MAPID_CANDIDATES[0]))
    assert stale == "POI_Decoy_Z1"        # the decoy really is there ...
    assert scene.map_id(player) == "POI_Moved_Z9"    # ... and loses
    assert scene.difficulty(player) == 1


def test_config_falls_back_to_constants_without_a_type_layout():
    """A build whose type tables carry no layout (reflection unusable) keeps
    working through the calibrated constants — the healthy-build path."""
    proc = FakeProc()
    hb = HeapBuilder(proc)
    hb.make_type("st.GameLayer")             # bare: no runtime layout
    hb.make_type("st.Config")
    hb.make_type("ent.Hero")
    gl = hb.make_instance("st.GameLayer", size=0x200)
    player = hb.make_instance("ent.Hero")
    proc.put_u64(player + OFF_GAMELAYER, gl)
    cfg = hb.make_instance("st.Config")
    proc.put_u64(cfg + OFF_CONFIG_MAPID, hb.make_string("POI_Forest_Z1"))
    box = hb.alloc(0x10)
    proc.put_i32(box + OFF_BOX_VALUE, 0)
    proc.put_u64(cfg + OFF_CONFIG_DIFFICULTY, box)
    proc.put_u64(gl + OFF_CONFIG_CANDIDATES[0], cfg)
    scene = Scene(proc, Hl(proc))
    assert scene.map_id(player) == "POI_Forest_Z1"
    assert scene.difficulty(player) == 0
    assert scene._cfg_off == OFF_CONFIG_CANDIDATES[0]


def test_config_absent_degrades_to_no_data():
    """No config anywhere: both reads return None and nothing is recorded — a
    missing config must never fake a map id or a Normal difficulty."""
    proc = FakeProc()
    hb = HeapBuilder(proc)
    hb.make_type("st.GameLayer", fields=GL_CONFIG_MOVED)
    hb.make_type("ent.Hero")
    gl = hb.make_instance("st.GameLayer", size=0x200)
    player = hb.make_instance("ent.Hero")
    proc.put_u64(player + OFF_GAMELAYER, gl)
    scene = Scene(proc, Hl(proc))
    assert scene.map_id(player) is None
    assert scene.difficulty(player) is None
    assert scene._cfg_off is None


def test_map_id_scan_finds_a_mapid_shaped_string_when_the_layout_is_unknown():
    """If st.Config's own layout can't be reflected (no runtime table on the
    type), the last resort still recovers the map id — but only if the string
    looks like one."""
    proc, hb, scene, player = _config_world(stale_decoys=False)
    gl = scene.gamelayer(player)
    cfg = scene.hl.ptr(gl + GL_CONFIG_MOVED["config"])
    proc.put_u64(cfg + 0x120, hb.make_string("POI_Moved_Z9"))   # beyond 0x40
    assert scene.map_id(player) == "POI_Moved_Z9"


def test_map_id_scan_ignores_strings_that_are_not_map_ids():
    """The reflected mapId slot is empty, so the walk runs: an unrelated String
    must not be mistaken for a map id, and the real hit is remembered."""
    proc, hb, scene, player = _config_world(stale_decoys=False)
    gl = scene.gamelayer(player)
    cfg = scene.hl.ptr(gl + GL_CONFIG_MOVED["config"])
    proc.put_u64(cfg + CONFIG_MOVED["mapId"], 0)               # no reflected hit
    proc.put_u64(cfg + 0x20, hb.make_string("Synchronising chat channels"))
    proc.put_u64(cfg + 0x120, hb.make_string("POI_Moved_Z9"))
    assert scene.map_id(player) == "POI_Moved_Z9"
    assert scene._cfg_mapid_cache.get(scene._cfg_type) == 0x120


@pytest.mark.parametrize("mid", [
    "POI_Forest_Z1",
    "POI/Z2Levels/Z2_POI_Boss_Cleodora",
    "World/W1_Siagarta",
    "Town_Central",
    "R1_POI_ManfishRuins_Z1",
    "Dungeon_Wolf_Hard",
])
def test_map_id_scan_accepts_every_real_id_shape(mid):
    """The shape test is what licenses the blind scan, so it has to accept the
    ids the game really uses (including the path-like `POI/Z2Levels/...`)."""
    proc, hb, scene, player = _config_world(stale_decoys=False)
    gl = scene.gamelayer(player)
    cfg = scene.hl.ptr(gl + GL_CONFIG_MOVED["config"])
    proc.put_u64(cfg + CONFIG_MOVED["mapId"], 0)
    proc.put_u64(cfg + 0x120, hb.make_string(mid))
    assert scene.map_id(player) == mid


@pytest.mark.parametrize("prose", [
    "Synchronising chat channels",
    "World map opened",
    "WorldMap",
    "st.activity.Dungeon",
])
def test_map_id_scan_rejects_prose_and_lookalikes(prose):
    proc, hb, scene, player = _config_world(stale_decoys=False)
    gl = scene.gamelayer(player)
    cfg = scene.hl.ptr(gl + GL_CONFIG_MOVED["config"])
    proc.put_u64(cfg + CONFIG_MOVED["mapId"], 0)
    proc.put_u64(cfg + 0x120, hb.make_string(prose))
    assert scene.map_id(player) is None


def test_config_slot_resolution_is_cached_per_gamelayer_type():
    """The tick path (overlay_manager reads map_id every tick) must not re-walk
    the field table each time: the slot is cached by type pointer."""
    proc, _hb, scene, player = _config_world()
    gl = scene.gamelayer(player)
    calls = {"n": 0}
    orig = scene.hl.field_offset

    def f(tp, name):
        calls["n"] += 1
        return orig(tp, name)

    scene._config_slot(gl)         # warm the cache first
    scene.hl.field_offset = f
    scene._config_slot(gl)
    scene._config_slot(gl)
    assert calls["n"] == 0          # warm cache: no field-table lookup at all
