"""Scene reader characterization tests (headless, no game).

Locks the unit/element parsing + ownership classification + the UNIT_BLOCK
field offsets against a hand-built HL heap, so a later refactor (or an offset
typo) can't silently change how the scene is read.

The later sections cover the rest of the same read surface, which used to be
three modules: the read BUDGET (one walk per window, a constant number of
process reads per unit walk), what the model asks of the unit list (the
enemy/companion filters), and the instance config read (difficulty, map id).
They share this file because they share the harness and because a change to the
reader's cost and a change to its answers land in the same review."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from fakemem import FakeProc, HeapBuilder

hl_mod = pytest.importorskip("farever_companion.core.hl")
scene_mod = pytest.importorskip("farever_companion.core.scene")
from farever_companion.core import attributes
from farever_companion.core.hl import Hl
from farever_companion.core.model import LiveModel
from farever_companion.core.scene import (
    Scene, OFF_GAMELAYER, OFF_UNITS_ARR, OFF_ELEMS_ARR, OFF_FOE_OWNER, OFF_POS,
    OFF_UNITID, OFF_ELEMID, OFF_ELEMSTATE, OFF_UATTR, OFF_HEALTH, OFF_LEVEL_UNIT,
)


def _build():
    proc = FakeProc()
    hb = HeapBuilder(proc)
    # type lattice: Boss descends from Foe (super-chain classification).
    hb.make_type("ent.Hero")
    foe = hb.make_type("ent.Foe")
    hb.make_type("ent.foe.Boss", super_type=foe)
    hb.make_type("st.LayerChunk")
    hb.make_type("ent.interactible.Chest")
    hb.make_type("ent.interactible.Gatherable")

    player = hb.make_instance("ent.Hero")
    chunk = hb.make_instance("st.LayerChunk")        # non-hero owner

    def unit(type_name, owner, uid, xyz):
        u = hb.make_instance(type_name)
        # Current calibration: foe/pet owner lives at OFF_FOE_OWNER (0x78);
        # the reader resolves a hero-owner at +0x498 via OFF_HERO_OWNERPLAYER.
        proc.put_u64(u + OFF_FOE_OWNER, owner)
        proc.put_f64(u + OFF_POS, xyz[0])
        proc.put_f64(u + OFF_POS + 8, xyz[1])
        proc.put_f64(u + OFF_POS + 16, xyz[2])
        proc.put_u64(u + OFF_UNITID, hb.make_string(uid))
        return u

    enemy = unit("ent.Foe", chunk, "Kobold", (10.0, 0.0, 5.0))
    boss = unit("ent.foe.Boss", chunk, "MunsterChuck", (1.5, 2.5, 3.5))
    companion = unit("ent.Foe", player, "Pet_Wolf", (0.0, 0.0, 0.0))
    hero2 = unit("ent.Hero", player, "Hero", (0.0, 0.0, 0.0))

    chest = hb.make_instance("ent.interactible.Chest")
    proc.put_f64(chest + OFF_POS, 7.0)
    proc.put_f64(chest + OFF_POS + 8, 8.0)
    proc.put_f64(chest + OFF_POS + 16, 9.0)
    proc.put_u64(chest + OFF_ELEMID, hb.make_string("WorldChest_1"))
    proc.put_u64(chest + OFF_ELEMSTATE, hb.make_string("Closed"))
    gatherable = hb.make_instance("ent.interactible.Gatherable")

    gl = hb.alloc(0x200)
    proc.put_u64(gl + OFF_UNITS_ARR, hb.make_array([enemy, boss, companion, hero2]))
    proc.put_u64(gl + OFF_ELEMS_ARR, hb.make_array([chest, gatherable]))
    proc.put_u64(player + OFF_GAMELAYER, gl)

    return Scene(proc, Hl(proc)), player


def test_units_parse_and_classify():
    scene, player = _build()
    units = scene.units(player)
    by_id = {u.unit_id: u for u in units}
    assert set(by_id) == {"Kobold", "MunsterChuck", "Pet_Wolf", "Hero"}

    kobold = by_id["Kobold"]
    assert kobold.cls == "ent.Foe"
    assert kobold.is_foe and not kobold.is_hero
    assert kobold.owner_cls == "st.LayerChunk"
    assert not kobold.is_player_owned
    assert kobold.is_enemy
    assert kobold.kind == "enemy"


def test_boss_subclass_is_enemy_via_superchain():
    scene, player = _build()
    boss = next(u for u in scene.units(player) if u.unit_id == "MunsterChuck")
    assert boss.cls == "ent.foe.Boss"
    assert boss.is_foe          # descends from ent.Foe (the old exact-match bug)
    assert boss.is_enemy


def test_companion_is_player_owned_not_enemy():
    scene, player = _build()
    pet = next(u for u in scene.units(player) if u.unit_id == "Pet_Wolf")
    assert pet.is_foe
    assert pet.owner_cls == "ent.Hero"
    assert pet.is_player_owned
    assert not pet.is_enemy
    assert pet.kind == "companion"


def test_own_hero_classified_as_hero():
    scene, player = _build()
    hero = next(u for u in scene.units(player) if u.unit_id == "Hero")
    assert hero.is_hero
    assert hero.kind == "hero"
    assert not hero.is_enemy


def test_unit_position_offset():
    scene, player = _build()
    kobold = next(u for u in scene.units(player) if u.unit_id == "Kobold")
    assert (kobold.x, kobold.y, kobold.z) == (10.0, 0.0, 5.0)


def test_elements_parse_chest_and_state():
    scene, player = _build()
    elems = scene.elements(player)
    chest = next(e for e in elems if e.is_chest)
    assert chest.elem_id == "WorldChest_1"
    assert chest.state == "Closed"
    assert (chest.x, chest.y, chest.z) == (7.0, 8.0, 9.0)
    assert any(e.is_gatherable for e in elems)


def test_no_gamelayer_returns_empty():
    proc = FakeProc()
    hb = HeapBuilder(proc)
    lone = hb.make_instance("ent.Hero")   # no +0x58 gamelayer pointer
    scene = Scene(proc, Hl(proc))
    assert scene.units(lone) == []
    assert scene.elements(lone) == []


def _food_scene(specs):
    """(class_name, skill_name_or_None) pairs -> (Scene, player).

    Each element carries an st.skill.Skill* in its OFF_ELEMID slot (the
    WorldConsumable / SkillObject layout: the id slot is reused as a Skill
    pointer, never a string); the Skill's head holds the display string, so
    _skill_display_name's string sweep resolves it."""
    proc = FakeProc()
    hb = HeapBuilder(proc)

    player = hb.make_instance("ent.Hero")
    gl = hb.alloc(0x200)
    elems = []
    for i, (cls, skill_name) in enumerate(specs):
        el = hb.make_instance(cls)
        proc.put_f64(el + OFF_POS, float(i) + 1.0)
        proc.put_f64(el + OFF_POS + 8, 2.0)
        proc.put_f64(el + OFF_POS + 16, 3.0)
        skill = hb.make_instance("st.skill.Skill")
        if skill_name:
            proc.put_u64(skill + 0x08, hb.make_string(skill_name))
        proc.put_u64(el + OFF_ELEMID, skill)
        elems.append(el)
    proc.put_u64(gl + OFF_ELEMS_ARR, hb.make_array(elems))
    proc.put_u64(player + OFF_GAMELAYER, gl)
    return Scene(proc, Hl(proc)), player


def test_food_stations_world_consumable_resolves_skill_name():
    """A WorldConsumable's id slot is a Skill*, not a string — the station
    must resolve the display name off that Skill and keep the position."""
    scene, player = _food_scene(
        [("st.skill.object.WorldConsumable", "Plainswalker Feast")])
    foods = scene.food_stations(player)
    assert len(foods) == 1
    f = foods[0]
    assert f.name == "Plainswalker Feast"
    assert (f.x, f.y, f.z) == (1.0, 2.0, 3.0)


def test_food_stations_world_consumable_always_counts():
    """Even with an unresolvable skill name a WorldConsumable is still food
    (render falls back to a generic 'Food' label)."""
    scene, player = _food_scene(
        [("st.skill.object.WorldConsumable", None)])
    foods = scene.food_stations(player)
    assert len(foods) == 1
    assert foods[0].name is None


def test_food_stations_skill_object_hint_filter():
    """SkillObject elements only count as food when the resolved skill name
    smells like one — combat casts are excluded. Also pins the _FOOD_NAME_HINTS
    regression: a SkillObject used to raise NameError and kill the whole scan."""
    scene, player = _food_scene([
        ("st.skill.SkillObject", "Fireball"),             # combat cast
        ("st.skill.SkillObject", "Plainswalker Feast"),   # placed food
        ("st.skill.SkillObject", None),                    # unreadable
    ])
    foods = scene.food_stations(player)
    assert [f.name for f in foods] == ["Plainswalker Feast"]


# --- the unit scan's read budget ---------------------------------------------
# HP is not in the unit block: it lives on a separate attributes allocation, so
# the old scan spent one process read per unit on it (plus three more resolving
# the owner's class, which every unit asks about the same handful of owners).
# These pin that a walk is now a CONSTANT number of reads, and that nothing about
# the values changed - the HP the DPS tracker diffs and the Dungeon HUD pegs is
# the same HP attributes.health() reports.


def _unit_scene(n_units, *, owners=1, hp=1000.0, attrs=True):
    """`n_units` foes each owned by one of `owners`, each with its own
    attributes object. Units are allocated wider than UNIT_BLOCK so the level
    field (OFF_LEVEL_UNIT == 0x400) can't spill into the next unit."""
    proc = FakeProc()
    hb = HeapBuilder(proc)
    for t in ("ent.Hero", "st.LayerChunk", "st.Player", "ent.Foe"):
        hb.make_type(t)
    owner_objs = [hb.make_instance("st.LayerChunk") for _ in range(owners)]
    units = []
    for i in range(n_units):
        u = hb.make_instance("ent.Foe", size=0x520)
        proc.put_u64(u + OFF_FOE_OWNER, owner_objs[i % owners])
        proc.put_f64(u + OFF_POS, float(i))
        proc.put_f64(u + OFF_POS + 8, 0.0)
        proc.put_f64(u + OFF_POS + 16, 0.0)
        proc.put_u64(u + OFF_UNITID, hb.make_string(f"Kobold_{i}"))
        proc.put_i32(u + OFF_LEVEL_UNIT, 60)
        if attrs:
            attr = hb.alloc(0x200)
            proc.put_f64(attr + OFF_HEALTH, hp)
            proc.put_u64(u + OFF_UATTR, attr)
        units.append(u)
    gl = hb.alloc(0x200)
    proc.put_u64(gl + OFF_UNITS_ARR, hb.make_array(units))
    player = hb.make_instance("ent.Hero")
    proc.put_u64(player + OFF_GAMELAYER, gl)
    return proc, Scene(proc, Hl(proc)), player


def _count_reads(proc):
    """Records every process read by method and address."""
    calls: dict[str, list] = {"read_many": [], "try_read": [], "u64": [], "i32": []}
    for name in calls:
        orig = getattr(proc, name)

        def f(*a, _n=name, _o=orig, **k):
            calls[_n].append(a[0] if a else None)
            return _o(*a, **k)

        setattr(proc, name, f)
    return calls


def test_unit_hp_comes_from_one_batched_read_not_one_per_unit():
    proc, scene, player = _unit_scene(40)
    scene.units(player)                      # warm the hl caches
    calls = _count_reads(proc)
    units = scene.units(player)
    assert len(units) == 40
    assert all(u.hp == 1000.0 for u in units)

    attr_ptrs = {proc.u64(u.addr + OFF_UATTR) for u in units}
    hp_addrs = {a + OFF_HEALTH for a in attr_ptrs}
    # exactly two batches: the unit blocks, then every unit's HP field
    assert len(calls["read_many"]) == 2
    assert set(calls["read_many"][-1]) == hp_addrs
    # and not a single per-unit read at an attributes object or its HP field
    assert not [a for a in calls["try_read"] if a in hp_addrs or a in attr_ptrs]


def test_the_batched_hp_agrees_with_the_single_unit_reader():
    proc, scene, player = _unit_scene(6, hp=4321.5)
    units = scene.units(player)
    assert [u.hp for u in units] == [4321.5] * 6
    assert all(attributes.health(scene.hl, u.addr) == u.hp for u in units)


def test_a_unit_walk_costs_a_constant_number_of_reads():
    """The per-unit terms are gone: 10 units and 60 units cost the same."""
    def reads_for(n):
        proc, scene, player = _unit_scene(n, owners=3)
        scene.units(player)                  # warm the hl caches
        calls = _count_reads(proc)
        units = scene.units(player)
        return sum(len(v) for v in calls.values()), len(units)

    small, n_small = reads_for(10)
    large, n_large = reads_for(60)
    assert (n_small, n_large) == (10, 60)
    assert small == large, f"{n_small} units cost {small}, {n_large} cost {large}"
    assert small < 20


def _owned_ids_scene():
    """One hero-owned pet plus one world mob sharing a single LayerChunk."""
    proc = FakeProc()
    hb = HeapBuilder(proc)
    hb.make_type("ent.Hero")
    hb.make_type("ent.Foe")
    hb.make_type("st.LayerChunk")
    chunk = hb.make_instance("st.LayerChunk")
    hero = hb.make_instance("ent.Hero")

    def unit(uid, owner):
        u = hb.make_instance("ent.Foe", size=0x520)
        proc.put_u64(u + OFF_FOE_OWNER, owner)
        proc.put_u64(u + OFF_UNITID, hb.make_string(uid))
        attr = hb.alloc(0x200)
        proc.put_f64(attr + OFF_HEALTH, 500.0)
        proc.put_u64(u + OFF_UATTR, attr)
        return u

    pet = unit("Pet_Wolf", hero)
    mob = unit("Kobold", chunk)
    gl = hb.alloc(0x200)
    proc.put_u64(gl + OFF_UNITS_ARR, hb.make_array([pet, mob]))
    player = hb.make_instance("ent.Hero")
    proc.put_u64(player + OFF_GAMELAYER, gl)
    return proc, Scene(proc, Hl(proc)), player


def test_owner_classes_still_classify_exactly_as_before():
    """The memo must not change who owns what: the DPS tracker's pet
    attribution reads is_player_owned / owner_cls off these entities."""
    proc, scene, player = _owned_ids_scene()
    by_id = {u.unit_id: u for u in scene.units(player)}
    assert by_id["Pet_Wolf"].is_player_owned        # Hero-owned pet
    assert by_id["Kobold"].owner_cls == "st.LayerChunk"
    assert not by_id["Kobold"].is_player_owned
    assert by_id["Kobold"].is_enemy                 # foe, unowned -> enemy
    assert not by_id["Pet_Wolf"].is_enemy


def _hp_cases_scene():
    """Six units, each with a differently-broken attributes object."""
    proc = FakeProc()
    hb = HeapBuilder(proc)
    hb.make_type("ent.Hero")
    hb.make_type("ent.Foe")
    hb.make_type("st.LayerChunk")
    units = []
    for name, hp_value, mode in (("healthy", 777.0, "ok"),
                                 ("missing pointer", None, "null"),
                                 ("unmapped object", None, "unmapped"),
                                 ("NaN", float("nan"), "ok"),
                                 ("negative", -5.0, "ok"),
                                 ("absurd", 1e300, "ok")):
        u = hb.make_instance("ent.Foe", size=0x520)
        proc.put_u64(u + OFF_FOE_OWNER, 0)
        proc.put_u64(u + OFF_UNITID, hb.make_string(name))
        if mode == "ok":
            attr = hb.alloc(0x200)
            proc.put_f64(attr + OFF_HEALTH, hp_value)
        else:
            # null: no attributes object. unmapped: a pointer outside every
            # region, exactly like reading a freed object.
            attr = 0 if mode == "null" else 0xDEAD0000
        proc.put_u64(u + OFF_UATTR, attr)
        units.append(u)
    gl = hb.alloc(0x200)
    proc.put_u64(gl + OFF_UNITS_ARR, hb.make_array(units))
    player = hb.make_instance("ent.Hero")
    proc.put_u64(player + OFF_GAMELAYER, gl)
    return proc, Scene(proc, Hl(proc)), player


def test_hp_is_unknown_when_the_attributes_object_is_unusable():
    """0.0 means "unreadable" to every consumer (the Dungeon HUD skips it when
    pegging), so garbage must never arrive as a number."""
    proc, scene, player = _hp_cases_scene()
    got = {u.unit_id: u.hp for u in scene.units(player)}

    assert got["healthy"] == 777.0
    assert got["missing pointer"] == 0.0
    assert got["unmapped object"] == 0.0
    assert got["NaN"] == 0.0
    assert got["negative"] == 0.0
    assert got["absurd"] == 0.0


def test_decode_health_is_the_one_judge_of_a_health_field():
    import struct

    ok = struct.pack("<d", 12.5)
    assert attributes.decode_health(ok) == 12.5
    assert attributes.decode_health(None) is None
    assert attributes.decode_health(b"\x00\x00") is None       # short block
    assert attributes.decode_health(struct.pack("<d", float("nan"))) is None
    assert attributes.decode_health(struct.pack("<d", -1.0)) is None
    assert attributes.decode_health(struct.pack("<d", 1e12)) is None
    assert attributes.decode_health(struct.pack("<d", 0.0)) == 0.0


# ==========================================================================
# scene-model reads (was test_scene_scan_cache.py)
# ==========================================================================

import os
from types import SimpleNamespace


from farever_companion.constants import (
    OFF_ELEM_FX, OFF_ELEMID, OFF_ELEMSTATE, OFF_GAMELAYER, OFF_ELEMS_ARR,
    OFF_ITEM_KIND, OFF_LOOT_COUNT, OFF_LOOT_ITEM, OFF_POS, OFF_UNITS_ARR,
    OFF_UNITID,
)
from farever_companion.core.chest_resolver import ChestResolver
from farever_companion.core.model import LiveModel
from farever_companion.core.scene import Scene


def _elem(hb, proc, cls, eid, x, state="Closed", fx=0):
    el = hb.make_instance(cls)
    proc.put_f64(el + OFF_POS, float(x))
    proc.put_f64(el + OFF_POS + 8, 0.0)
    proc.put_f64(el + OFF_POS + 16, 0.0)
    if eid is not None:
        proc.put_u64(el + OFF_ELEMID, hb.make_string(eid))
    proc.put_u64(el + OFF_ELEMSTATE, hb.make_string(state))
    proc.put_u64(el + OFF_ELEM_FX, fx)
    return el


def _elem_world(*, with_elements=True):
    """A GameLayer holding one of every element kind the accessors look for."""
    proc = FakeProc()
    hb = HeapBuilder(proc)
    for cls in ("ent.Hero", "ent.interactible.Gatherable",
                "ent.interactible.Chest", "ent.interactible.Obelisk",
                "ent.interactible.InstanceOrb", "ent.interactible.Teleporter",
                "ent.interactible.LootDrop", "st.skill.object.WorldConsumable",
                "st.Item", "st.skill.Skill"):
        hb.make_type(cls)

    elems = [
        _elem(hb, proc, "ent.interactible.Gatherable", "Ore_Tungstene", 1),
        _elem(hb, proc, "ent.interactible.Chest", "WorldChest_1", 2),
        _elem(hb, proc, "ent.interactible.Obelisk", "Obelisk_1", 3),
        _elem(hb, proc, "ent.interactible.InstanceOrb", "RedOrb_Instance_1", 4),
        _elem(hb, proc, "ent.interactible.Teleporter", "Dungeon_Door", 5),
        _elem(hb, proc, "ent.interactible.InstanceOrb", "WorldEvent_Orb_1", 6),
        _elem(hb, proc, "ent.interactible.InstanceOrb", "RedOrb_World_1", 7,
              fx=0x1000),
    ]

    # a loot drop: LootDrop -> st.Item (item id @+0x78) + stack count
    drop = _elem(hb, proc, "ent.interactible.LootDrop", None, 8)
    item = hb.make_instance("st.Item")
    proc.put_u64(item + OFF_ITEM_KIND, hb.make_string("MoteOfChaos"))
    proc.put_u64(drop + OFF_LOOT_ITEM, item)
    proc.put_i32(drop + OFF_LOOT_COUNT, 3)
    elems.append(drop)

    # placed food: the id slot is a Skill*, not a string
    food = _elem(hb, proc, "st.skill.object.WorldConsumable", None, 9)
    skill = hb.make_instance("st.skill.Skill")
    proc.put_u64(skill + 0x08, hb.make_string("Plainswalker Feast"))
    proc.put_u64(food + OFF_ELEMID, skill)
    elems.append(food)

    gl = hb.alloc(0x200)
    proc.put_u64(gl + OFF_ELEMS_ARR,
                 hb.make_array(elems if with_elements else []))
    player = hb.make_instance("ent.Hero")
    proc.put_u64(player + OFF_GAMELAYER, gl)
    return proc, hb, Scene(proc, Hl(proc)), player, gl


def _model(scene, proc, player):
    """A LiveModel with the real scene accessors and only the locator stubbed."""
    m = LiveModel.__new__(LiveModel)          # headless: __init__ never ran
    m.hl = Hl(proc)
    m.scene = scene
    m.chests_resolver = ChestResolver()
    m.locator = SimpleNamespace(live_address=lambda: player,
                                read_xyz=lambda: (0.0, 0.0, 0.0))
    m._units_cache, m._units_at, m._units_key = [], 0.0, None
    return m


def _count_read_many(proc):
    """A getter for the number of batched element scans so far.

    One `elements()` walk is exactly one `read_many` (the whole header block of
    the element array), so this is a direct count of walks.
    """
    state = {"n": 0}
    orig = proc.read_many

    def f(*a, **k):
        state["n"] += 1
        return orig(*a, **k)

    proc.read_many = f
    return lambda: state["n"]


def _count_calls(obj, name):
    """Counts calls to `obj.name`, delegating to the original."""
    state = {"n": 0}
    orig = getattr(obj, name)

    def f(*a, **k):
        state["n"] += 1
        return orig(*a, **k)

    setattr(obj, name, f)
    return lambda: state["n"]


# --- the coalescing ----------------------------------------------------------
def test_one_element_walk_serves_every_reader_in_the_window():
    """The whole point: ten readers, one walk."""
    proc, _hb, scene, player, _gl = _elem_world()
    m = _model(scene, proc, player)
    walks = _count_read_many(proc)

    # one frame's worth of readers, in the order the overlays call them
    m.gatherables()
    m.obelisks()
    m.live_orbs()
    m.live_chest_orbs()
    m.live_chests()
    m.teleporters()
    m.elements()
    m.world_orb_fx()
    m.loot_drops()
    m.food_stations()
    scene.elements(player)               # the rift tracker's own read

    assert walks() == 1, "the element array was re-walked for a second reader"


def test_every_reader_is_handed_the_same_list():
    """Identity, not equality: two overlays in one frame cannot disagree about
    whether an orb that just despawned is still there."""
    _proc, _hb, scene, player, _gl = _elem_world()
    first = scene.elements(player)
    assert scene.elements(player) is first


def test_a_second_frame_inside_the_window_still_shares():
    proc, _hb, scene, player, _gl = _elem_world()
    walks = _count_read_many(proc)
    for _ in range(5):
        scene.elements(player)
    assert walks() == 1


# --- invalidation ------------------------------------------------------------
def test_the_cache_expires_at_the_end_of_the_window():
    proc, _hb, scene, player, _gl = _elem_world()
    walks = _count_read_many(proc)
    scene.elements(player)
    assert walks() == 1
    scene._elems_at -= Scene.ELEMS_TTL + 0.01        # the window elapsed
    scene.elements(player)
    assert walks() == 2


def test_fresh_bypasses_the_cache():
    """The escape hatch for a caller that must see a scene change now."""
    proc, _hb, scene, player, _gl = _elem_world()
    walks = _count_read_many(proc)
    scene.elements(player)
    scene.elements(player, fresh=True)
    assert walks() == 2
    assert scene.elements(player) is scene.elements(player)   # still cached


def test_a_different_player_is_a_different_scene():
    """A zone swap re-resolves the scene under the same reader."""
    proc, hb, scene, player, gl = _elem_world()
    walks = _count_read_many(proc)
    other = hb.make_instance("ent.Hero")   # same layer, different address
    proc.put_u64(other + OFF_GAMELAYER, gl)
    scene.elements(player)
    scene.elements(other)
    assert walks() == 2
    scene.elements(other)
    assert walks() == 2                    # ...and the new one is cached


def test_a_scan_that_finds_nothing_is_never_cached():
    """An empty result is what a loading screen and a failed gamelayer resolve
    both look like; pinning it for 150 ms would blank the minimap's POIs well
    after the scene came back."""
    proc, _hb, scene, player, _gl = _elem_world(with_elements=False)
    scans = _count_calls(scene, "_scan_elements")
    assert scene.elements(player) == []
    assert scene.elements(player) == []
    assert scans() == 2


def test_a_scene_that_fails_to_resolve_is_never_cached():
    proc = FakeProc()
    hb = HeapBuilder(proc)
    player = hb.make_instance("ent.Hero")     # no gamelayer pointer at all
    scene = Scene(proc, Hl(proc))
    scans = _count_calls(scene, "_scan_elements")
    assert scene.elements(player) == []
    assert scene.elements(player) == []
    assert scans() == 2


# --- the trade the TTL makes -------------------------------------------------
def _set_chest_state(proc, hb, scene, player, chest_id, state):
    """Plant a brand-new String for the state, as the game writes a new one -
    reusing the old String object would only exercise Hl's own string cache."""
    chest = next(e for e in scene.elements(player, fresh=True)
                 if e.elem_id == chest_id)
    proc.put_u64(chest.addr + OFF_ELEMSTATE, hb.make_string(state))


def _chest_state_now(scene, player, chest_id, *, fresh=False):
    return next(e for e in scene.elements(player, fresh=fresh)
                if e.elem_id == chest_id).state


def test_a_very_recent_scene_change_waits_for_the_window():
    """Documents the deliberate staleness: a chest that opens is seen on the
    next window (<=150 ms), not on the read that follows it - and `fresh=True`
    is how a caller opts out."""
    proc, hb, scene, player, _gl = _elem_world()
    scene.elements(player)                              # warm the window
    _set_chest_state(proc, hb, scene, player, "WorldChest_1", "Opened")

    assert _chest_state_now(scene, player, "WorldChest_1") == "Closed"
    assert _chest_state_now(scene, player, "WorldChest_1", fresh=True) == "Opened"
    scene._elems_at -= Scene.ELEMS_TTL + 0.01
    assert _chest_state_now(scene, player, "WorldChest_1") == "Opened"


def test_the_window_is_shorter_than_the_heaviest_rescan():
    """<=one stale tick: the element readers refresh on 300-350 ms timers, so a
    window under the shortest of them can never show a list two ticks old."""
    # The budget, unconditionally - this half runs even with no Qt installed.
    assert Scene.ELEMS_TTL * 1000 <= 150

    # ...and against the real timer values when the overlays can be imported.
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from farever_companion.ui.overlays import (dungeon_overlay, entity_overlay,
                                               minimap)
    fastest_rescan = min(minimap.POLL_MS, entity_overlay.POLL_MS,
                         dungeon_overlay.POLL_MS)
    assert Scene.ELEMS_TTL * 1000 < fastest_rescan


# --- the unit side -----------------------------------------------------------
def _two_zone_scene():
    """Two heroes, each with its own GameLayer and one distinct unit."""
    proc = FakeProc()
    hb = HeapBuilder(proc)
    hb.make_type("ent.Foe")
    hb.make_type("ent.Hero")

    def zone(uid, x):
        u = hb.make_instance("ent.Foe")
        proc.put_f64(u + OFF_POS, float(x))
        proc.put_f64(u + OFF_POS + 8, 0.0)
        proc.put_f64(u + OFF_POS + 16, 0.0)
        proc.put_u64(u + OFF_UNITID, hb.make_string(uid))
        gl = hb.alloc(0x200)
        proc.put_u64(gl + OFF_UNITS_ARR, hb.make_array([u]))
        hero = hb.make_instance("ent.Hero")
        proc.put_u64(hero + OFF_GAMELAYER, gl)
        return hero

    return proc, Scene(proc, Hl(proc)), zone("Kobold", 1), zone("Goblin", 2)


def test_the_unit_cache_is_keyed_on_the_player():
    """The unit list is already coalesced (UNITS_TTL), but a time-only key would
    hand the previous zone's units to the first reader after a zone swap."""
    proc, scene, hero_a, hero_b = _two_zone_scene()
    m = _model(scene, proc, hero_a)
    assert [u.unit_id for u in m.units()] == ["Kobold"]
    m.locator = SimpleNamespace(live_address=lambda: hero_b,
                                read_xyz=lambda: (0.0, 0.0, 0.0))
    assert [u.unit_id for u in m.units()] == ["Goblin"]


def test_the_unit_scan_is_still_coalesced_across_readers():
    """Five readers, one walk - the property the element cache now matches."""
    proc, scene, hero_a, _hero_b = _two_zone_scene()
    m = _model(scene, proc, hero_a)
    walks = _count_calls(scene, "units")
    assert [u.unit_id for u in m.units()] == ["Kobold"]
    for _ in range(4):
        m.nearest_enemies((0.0, 0.0, 0.0), 10)
    assert walks() == 1


# ==========================================================================
# scene-model reads (was test_units.py)
# ==========================================================================

from farever_companion import paths
from farever_companion.core.scene import Entity
from farever_companion.data import units


def _game_data_present() -> bool:
    """True if the optional CDB sheets are reachable (data/sheets/lootTable.json
    marker resolves). False when the game data isn't present in the checkout ->
    these data-dependent tests skip. They run wherever the data is present
    (assertions unchanged)."""
    try:
        return (paths.sheets_dir() / "lootTable.json").exists()
    except Exception:
        return False


# Scoped per-test, NOT module-level: this module merges three groups, and only the
# unit-catalog group needs the optional game data. A module-level skipif would also
# skip the scan-cache and difficulty groups.
_units_need_game_data = pytest.mark.skipif(
    not _game_data_present(),
    reason="requires game data (data/sheets/*.json)")


@_units_need_game_data
def test_named_bosses_are_detected():
    assert units.is_boss("MunsterChuck")
    assert not units.is_boss("Kobold")


@_units_need_game_data
def test_every_catalog_companion_is_detected():
    # the collection catalog's companions category is the authoritative list
    from farever_companion.data import collections as col
    ids = [r["id"] for r in col.items("companions")]
    assert len(ids) >= 60   # grew from 60 to 74 as the catalog gained pets
    missed = [i for i in ids if not units.is_companion(i)]
    assert not missed, f"catalog companions not detected: {missed}"


@_units_need_game_data
def test_companion_detection():
    # wild catchable companions are the Critter-type units
    assert units.is_companion("Rabbit_Yellow")      # Buttontail
    assert units.is_companion("Lizard_Green")       # Saladmander
    assert not units.is_companion("Kobold")
    assert not units.is_companion("MunsterChuck")
    assert not units.is_companion(None)


@_units_need_game_data
def test_unit_types_cover_filter_choices():
    types = units.unit_types()
    assert units.COMPANION_TYPE in types
    assert "Wolf" in types and "Kobold" in types
    assert types == sorted(types)


def _ent(unit_id, owner_cls=None, x=0.0):
    return Entity(addr=id(unit_id) + int(x), cls="ent.Foe", unit_id=unit_id,
                  owner_cls=owner_cls, x=x, y=0.0, z=0.0, is_foe=True)


def _model_with(units_list):
    m = LiveModel.__new__(LiveModel)       # headless: no proc, stubbed units()
    m.units = lambda: units_list
    class StubLocator:
        def live_address(self) -> int | None: return None
    m.locator = StubLocator()
    return m


@_units_need_game_data
def test_companion_and_enemy_lists_split():
    wild = _ent("Rabbit_Yellow", x=1.0)
    owned = _ent("Lizard_Green", owner_cls="ent.Hero", x=2.0)  # someone's pet
    kobold = _ent("Kobold_Z1W_Mace", x=3.0)
    m = _model_with([wild, owned, kobold])
    xyz = (0.0, 0.0, 0.0)
    # wild critters only - no equipped pets, no enemies
    comps = [e for e, _ in m.nearest_companions(xyz, 10)]
    assert comps == [wild]
    # enemies never contain critters, owned or wild
    foes = [e for e, _ in m.nearest_enemies(xyz, 10)]
    assert foes == [kobold]
    # and the type filter still applies to enemies
    assert m.nearest_enemies(xyz, 10, hide_types={"Kobold"}) == []


# ==========================================================================
# scene-model reads (was test_difficulty.py)
# ==========================================================================

pytest.importorskip("farever_companion.core.scene")
from farever_companion.constants import (
    OFF_GAMELAYER, OFF_CONFIG_DIFFICULTY, OFF_CONFIG_MAPID, OFF_BOX_VALUE,
)

CONFIG_AT = 0x470   # arbitrary GameLayer offset; the reader must discover it


def _scene_with_difficulty(value: int | None,
                           map_id: str = "POI/Z2Levels/Z2_POI_Boss_Cleodora"):
    """Build hero -> GameLayer -> config at an arbitrary offset. The config's
    mapId String is the signature the reader scans for; `value` is the boxed
    difficulty (None = box pointer null, i.e. not inside an instance)."""
    proc = FakeProc()
    hb = HeapBuilder(proc)
    player = hb.make_instance("ent.Hero")
    gl = hb.alloc(0x1400)                       # within CONFIG_SCAN_BYTES
    cfg = hb.alloc(0x20)
    proc.put_u64(player + OFF_GAMELAYER, gl)
    proc.put_u64(gl + CONFIG_AT, cfg)
    proc.put_u64(cfg + OFF_CONFIG_MAPID, hb.make_string(map_id))
    if value is not None:
        box = hb.alloc(0x10)
        proc.put_i32(box + OFF_BOX_VALUE, value)
        proc.put_u64(cfg + OFF_CONFIG_DIFFICULTY, box)
    return Scene(proc, Hl(proc)), player


def test_normal_reads_zero():
    scene, player = _scene_with_difficulty(0)
    assert scene.difficulty(player) == 0


def test_hard_reads_one():
    scene, player = _scene_with_difficulty(1)
    assert scene.difficulty(player) == 1


def test_null_box_outside_instance():
    scene, player = _scene_with_difficulty(None)
    assert scene.difficulty(player) is None


def test_implausible_value_rejected():
    scene, player = _scene_with_difficulty(0x6566)   # garbage -> not 0/1
    assert scene.difficulty(player) is None


def test_no_player_is_none():
    scene, _ = _scene_with_difficulty(1)
    assert scene.difficulty(None) is None


def test_non_poi_config_ignored():
    # a struct that isn't the instance config (mapId lacks every instance
    # signature the reader accepts: POI/World/Dungeon/Z1) is not matched
    scene, player = _scene_with_difficulty(1, map_id="Town_Central")
    assert scene.difficulty(player) is None


def test_asset_path_is_never_the_instance_config():
    """LIVE 2026-09-30 (dungeon visit): a UI icon path validated as the config.

    The old signature test was a substring check, and
    ``UI/icons/POI_DifficultySkulls_atlas_38PX.png`` contains "POI", so the
    first object the scan offered became the config. Everything read from it was
    then an asset path: ``map_id()`` returned that icon path and ``difficulty()``
    read None - which is what left the MODE badge blank on the Top DPS HUD and
    the DPS Analysis rail while the Run Timer still named a mode.
    """
    scene, player = _scene_with_difficulty(
        1, map_id="UI/icons/POI_DifficultySkulls_atlas_38PX.png")
    assert scene.difficulty(player) is None
    assert scene.map_id(player) is None


def test_asset_path_decoy_loses_to_the_real_config_behind_it():
    """The scan must SKIP an asset-shaped object and keep walking: the real
    config sits later in the same window, at the offset the live build uses."""
    proc = FakeProc()
    hb = HeapBuilder(proc)
    player = hb.make_instance("ent.Hero")
    gl = hb.alloc(0x1400)                       # within CONFIG_SCAN_BYTES
    proc.put_u64(player + OFF_GAMELAYER, gl)
    decoy = hb.alloc(0x20)                      # what the live scan hit first
    proc.put_u64(decoy + OFF_CONFIG_MAPID,      # ...and the old rule accepted
                 hb.make_string("UI/icons/POI_DifficultySkulls_atlas_38PX.png"))
    proc.put_u64(gl + 0x300, decoy)
    cfg = hb.alloc(0x20)
    proc.put_u64(cfg + OFF_CONFIG_MAPID, hb.make_string("POI_Forest_Z1"))
    box = hb.alloc(0x10)
    proc.put_i32(box + OFF_BOX_VALUE, 1)
    proc.put_u64(cfg + OFF_CONFIG_DIFFICULTY, box)
    proc.put_u64(gl + CONFIG_AT, cfg)
    scene = Scene(proc, Hl(proc))
    assert scene.difficulty(player) == 1        # hard, read from the real config
    assert scene.map_id(player) == "POI_Forest_Z1"
    assert scene._cfg_off == CONFIG_AT          # the decoy was not remembered


# --- orb fx reads are trusted or silent (2026-09-18 hide-collected fix) -----
def _orb_world_with_layout(*, layout=True, eid="RedOrb_1"):
    """A single InstanceOrb whose type carries a runtime layout resolving
    currentFx at 0x40 (first field slot 8 satisfies the HL plausibility gate).
    layout=False strips the runtime layout entirely. Returns
    (proc, hb, scene, player, orb_addr, fx_slot)."""
    proc = FakeProc()
    hb = HeapBuilder(proc)
    hb.make_type("ent.Hero")
    hb.make_type("ent.interactible.InstanceOrb",
                 fields={"pad": 8, "currentFx": 0x40} if layout else None)
    orb = hb.make_instance("ent.interactible.InstanceOrb")
    proc.put_f64(orb + OFF_POS, 4.0)
    proc.put_f64(orb + OFF_POS + 8, 0.0)
    proc.put_f64(orb + OFF_POS + 16, 0.0)
    proc.put_u64(orb + OFF_ELEMID, hb.make_string(eid))
    proc.put_u64(orb + OFF_ELEMSTATE, hb.make_string("Enabled"))
    gl = hb.alloc(0x200)
    proc.put_u64(gl + OFF_ELEMS_ARR, hb.make_array([orb]))
    player = hb.make_instance("ent.Hero")
    proc.put_u64(player + OFF_GAMELAYER, gl)
    scene = Scene(proc, Hl(proc))
    return proc, hb, scene, player, orb, 0x40 if layout else None


def test_orb_fx_null_at_reflected_slot_is_dark():
    proc, _hb, scene, player, _orb, _slot = _orb_world_with_layout()
    m = _model(scene, proc, player)
    (e,) = m.live_orbs()
    assert m.orb_fx_present(e) is False          # null = collected


def test_orb_fx_pointer_at_reflected_slot_is_glowing():
    proc, hb, scene, player, _orb, slot = _orb_world_with_layout()
    proc.put_u64(_orb + slot, hb.make_instance("ent.Hero"))   # plausible ptr
    m = _model(scene, proc, player)
    (e,) = m.live_orbs()
    assert m.orb_fx_present(e) is True


def test_orb_fx_junk_value_at_reflected_slot_is_no_opinion():
    proc, _hb, scene, player, _orb, slot = _orb_world_with_layout()
    proc.put_u64(_orb + slot, 0x1000)            # not a plausible pointer
    m = _model(scene, proc, player)
    (e,) = m.live_orbs()
    assert m.orb_fx_present(e) is None


def test_orb_fx_no_layout_is_no_opinion_not_dark():
    # The pre-fix behaviour: with no runtime layout the hardcoded constant
    # slot was read and its value trusted. A drifted layout must answer
    # "no opinion", never a confident lie.
    proc, _hb, scene, player, _orb, _slot = _orb_world_with_layout(layout=False)
    m = _model(scene, proc, player)
    (e,) = m.live_orbs()
    assert m.orb_fx_present(e) is None


def test_world_orb_fx_drops_untrusted_rows():
    # A world orb without a resolvable currentFx offset yields NO row at all:
    # the syncers must not see a fake "dark" observation for it.
    proc, _hb, scene, player, _orb, _slot = _orb_world_with_layout(
        layout=False, eid="RedOrb_World_1")
    m = _model(scene, proc, player)
    assert m.world_orb_fx() == []


def test_party_health_state_uses_roster_and_live_hero_hp(monkeypatch):
    heroes = [SimpleNamespace(addr=11, is_hero=True),
              SimpleNamespace(addr=22, is_hero=True)]
    members = [SimpleNamespace(hero=11, is_me=False, connected=True),
               SimpleNamespace(hero=22, is_me=True, connected=True)]
    fake = SimpleNamespace(
        units=lambda: heroes,
        cached_group_roster=lambda: SimpleNamespace(members=members),
        hl=object(),
        player_addr=22,
    )
    hp = {11: 100.0, 22: 0.0}
    monkeypatch.setattr(attributes, "health", lambda _hl, addr: hp[addr])

    assert LiveModel.party_health_state(fake) == (True, {11}, {11, 22})


def _boss_pick_model(entities, player_addr=99):
    """A LiveModel whose scene walk returns `entities`, for the boss pick only.

    `units()` short-circuits on a warm cache, so a fresh `_units_at` forces one
    walk of the stubbed `scene.units` and then runs the real boss-selection
    block - which is the unit under test, not a re-implementation of it.
    """
    m = LiveModel.__new__(LiveModel)
    m.hl = None
    m.chests_resolver = None
    m.dungeon_boss = None
    m.scene = SimpleNamespace(units=lambda _pa: [SimpleNamespace(
        addr=player_addr, unit_id=None, x=0.0, y=0.0, z=0.0,
        dist=lambda _x, _y, _z: 0.0)] + list(entities))
    m._units_cache, m._units_at, m._units_key = [], 0.0, None
    # `player_addr` is a read-only property over the locator, so stub that.
    m.locator = SimpleNamespace(live_address=lambda: player_addr,
                                read_xyz=lambda: (0.0, 0.0, 0.0))
    return m


def _foe(addr, unit_id, x=0.0):
    return SimpleNamespace(addr=addr, unit_id=unit_id,
                           dist=lambda _x, _y, _z: abs(x))


def test_the_dungeon_boss_is_the_declared_one_not_the_nearest_clone(monkeypatch):
    """A rift's TRUE CLONE must not end the run.

    `units.is_boss` counts the clone as a boss - correctly, for DAMAGE: killing
    it is damage worth recording. It is not the boss, so its death must not end
    the Run Timer (`encounter_state` -> `kill_id`). Live 2026-10-03: the timer
    finished the moment the clone died with the real boss still standing,
    because the pick was NEAREST and the clone was closer.

    The game's own activity table says which unit is the boss, and that
    declaration outranks distance - the same precedence the DPS meter's scan
    already used, which is why the meter and the timer disagreed.
    """
    from farever_companion.core import game_state
    from farever_companion.data import dungeons as ddata

    CLONE = "DemonSuperElite_Fairy_TrueClone"
    BOSS = "DemonSuperElite_Fairy"
    assert udata_boss(CLONE) and udata_boss(BOSS), "both read as bosses"

    # The CLONE is listed first AND much closer to the player, so a
    # scene-order or nearest-both pick lands on it - which is the bug.
    m = _boss_pick_model([_foe(2, CLONE, x=1.0), _foe(1, BOSS, x=50.0)])
    monkeypatch.setattr(ddata, "declared_boss_id", lambda _z: BOSS)
    monkeypatch.setattr(game_state, "zone", lambda _m: "SomeRift")

    m.units()
    assert m.dungeon_boss == BOSS, "the declared boss wins over the nearer clone"


def udata_boss(unit_id):
    from farever_companion.data import units as udata
    return udata.is_boss(unit_id)


def test_the_dungeon_boss_does_not_hop_mid_fight_to_another_boss_tagged(monkeypatch):
    """Sticky: once engaged, a second boss-tagged unit cannot steal the fight.

    The declared boss is absent here (an unresolvable zone), so this is the
    stickiness rule alone: the boss already in hand is kept while it is still
    in the scene, instead of handing the fight to whatever is nearer this tick.
    """
    from farever_companion.core import game_state
    from farever_companion.data import dungeons as ddata

    BOSS = "Nightking_Maat_Demon"
    OTHER = "Asuna"
    monkeypatch.setattr(ddata, "declared_boss_id", lambda _z: None)
    monkeypatch.setattr(game_state, "zone", lambda _m: "Unknown")

    m = _boss_pick_model([_foe(2, OTHER, x=1.0), _foe(1, BOSS, x=50.0)])
    m.dungeon_boss = BOSS                       # already engaged on the boss
    m.units()
    assert m.dungeon_boss == BOSS, "must not hop to the nearer boss-tagged unit"

    # Once it is gone from the scene, the next boss may be taken.
    m2 = _boss_pick_model([_foe(2, OTHER, x=1.0)])
    m2.dungeon_boss = BOSS
    m2.units()
    assert m2.dungeon_boss == OTHER


def test_no_boss_in_scene_clears_the_dungeon_boss():
    """A prior dungeon's boss must not linger into the next one."""
    m = _boss_pick_model([_foe(1, "Honey_Slime")])
    m.dungeon_boss = "Nightking_Maat_Demon"
    m.units()
    assert m.dungeon_boss is None
