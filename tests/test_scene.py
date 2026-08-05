"""Scene reader characterization tests (headless, no game).

Locks the unit/element parsing + ownership classification + the UNIT_BLOCK
field offsets against a hand-built HL heap, so a later refactor (or an offset
typo) can't silently change how the scene is read.
"""
from __future__ import annotations

import pytest

from fakemem import FakeProc, HeapBuilder

hl_mod = pytest.importorskip("farever_companion.core.hl")
scene_mod = pytest.importorskip("farever_companion.core.scene")
from farever_companion.core.hl import Hl
from farever_companion.core.scene import (
    Scene, OFF_GAMELAYER, OFF_UNITS_ARR, OFF_ELEMS_ARR, OFF_FOE_OWNER, OFF_POS,
    OFF_UNITID, OFF_ELEMID, OFF_ELEMSTATE,
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
