"""Instance difficulty read (GameLayer.config.difficulty).

Locks the discover-by-signature read path: scan GameLayer for the config struct
(mapId contains 'POI'), then box at config+0x08 -> i32 (0=Normal, 1=Hard).
Headless, against a hand-built heap.
"""
from __future__ import annotations

import pytest

from fakemem import FakeProc, HeapBuilder

pytest.importorskip("farever_companion.core.scene")
from farever_companion.core.hl import Hl
from farever_companion.core.scene import Scene
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
    # a struct that isn't the instance config (mapId lacks 'POI') is not matched
    scene, player = _scene_with_difficulty(1, map_id="Overworld/Z1_Town")
    assert scene.difficulty(player) is None
