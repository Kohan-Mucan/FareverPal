"""Player locator characterization tests (headless, no game).

Covers the two locate paths that were previously untested:
  * GameAppLocator: the scan-free anchor chain (App static -> inst -> hero),
    plus its type-not-found and inst-not-found failure modes.
  * PlayerLocator: the primary anchor success, and the heap-scan fallback's
    "most-populated world" heuristic when the anchor can't resolve.

A FakeProc + HeapBuilder lay out the HL chain exactly as the runtime does
(offsets mirror core/appsingleton.py + core/scene.py).
"""
from __future__ import annotations

import pytest

from fakemem import FakeProc, HeapBuilder, TO_GLOBALVAL

pytest.importorskip("farever_companion.core.appsingleton")
from farever_companion.core.hl import Hl
from farever_companion.core.appsingleton import GameAppLocator
from farever_companion.core.player import PlayerLocator
from farever_companion.core.scene import OFF_GAMELAYER, OFF_UNITS_ARR


def _world_hero(hb, proc):
    """An ent.Hero that is genuinely in-world: it owns a GameLayer at +0x58, the
    signal the locator now requires (a menu/preview hero has none)."""
    hero = hb.make_instance("ent.Hero")
    gl = hb.make_instance("st.GameLayer")
    proc.put_u64(hero + OFF_GAMELAYER, gl)
    return hero


def _build_anchor(*, with_hero: bool = True, with_inst: bool = True,
                  hero_in_world: bool = True,
                  hero_fields: dict | None = None):
    """Build the full GameApp anchor chain:

        GameApp type_obj --super--> App hl_type --obj--> App type_obj
            App type_obj +0x38 (global_value) --> slot --> $App static object
                static.inst --> GameApp singleton
                    singleton.hero/me/layer --> ent.Hero / st.Player / st.GameLayer
    """
    proc = FakeProc()
    hb = HeapBuilder(proc)

    app_tp = hb.make_type("App")
    app_obj = hb.type_objs["App"]
    # GameApp's super is App's hl_type.
    gameapp_tp = hb.make_type("GameApp", super_type=app_tp)
    gameapp_obj = hb.type_objs["GameApp"]

    # Other classes the field-scan resolves by name. Passing `hero_fields`
    # gives ent.Hero a runtime layout table so Hl.field_offset resolves the
    # combat fields by name (the reflection path PlayerLocator.combat_state
    # prefers over the fixed constants).
    hb.make_type("ent.Hero", fields=hero_fields)
    hb.make_type("st.Player")
    hb.make_type("st.GameLayer")

    singleton = hb.make_instance("GameApp")
    hero = hb.make_instance("ent.Hero")
    me = hb.make_instance("st.Player")
    layer = hb.make_instance("st.GameLayer")
    # A located hero must be in-world (own a GameLayer); a menu/preview hero
    # exists but has none, which the locator must reject.
    if hero_in_world:
        proc.put_u64(hero + OFF_GAMELAYER, hb.make_instance("st.GameLayer"))
    # singleton fields (mirror the live offsets, but resolved by class anyway).
    if with_hero:
        proc.put_u64(singleton + 0xD0, hero)
    proc.put_u64(singleton + 0xC8, me)
    proc.put_u64(singleton + 0xE0, layer)

    static = hb.make_instance("st.AppStatic")   # arbitrary container class
    if with_inst:
        proc.put_u64(static + 0x20, singleton)  # static.inst -> the singleton

    # App type_obj.global_value -> slot -> static object.
    slot = hb.alloc(8)
    proc.put_u64(slot, static)
    proc.put_u64(app_obj + TO_GLOBALVAL, slot)

    return proc, hb, dict(gameapp_obj=gameapp_obj, static_obj=static,
                          off_inst=0x20, singleton=singleton,
                          hero=hero, me=me, layer=layer)


# --- GameAppLocator: the anchor chain --------------------------------------
def test_anchor_locates_singleton_and_offsets():
    proc, hb, ref = _build_anchor()
    loc = GameAppLocator(proc, Hl(proc))
    assert loc.locate() == ref["singleton"]
    assert loc.hero() == ref["hero"]
    assert loc.me() == ref["me"]
    assert loc.layer() == ref["layer"]
    # offsets were resolved by class, matching where we placed the fields
    assert loc.off_hero == 0xD0
    assert loc.off_me == 0xC8
    assert loc.off_layer == 0xE0


def test_anchor_fast_path_is_session_stable():
    proc, hb, ref = _build_anchor()
    loc = GameAppLocator(proc, Hl(proc))
    first = loc.locate()
    # second call must reuse the cached singleton, not rescan
    assert loc.locate() == first == ref["singleton"]


def test_anchor_type_not_found_returns_none():
    # No GameApp type/name in memory at all -> the name scan finds nothing.
    proc = FakeProc()
    hb = HeapBuilder(proc)
    hb.make_type("SomethingElse")
    loc = GameAppLocator(proc, Hl(proc))
    assert loc.locate() is None


def test_anchor_reachable_at_menu_then_self_heals_in_world():
    # Starting the companion at the main menu: GameApp.hero is null, but the
    # chain (App static -> .inst -> GameApp) is still fully reachable. The anchor
    # must commit its stable offsets NOW (so no heap scan / no 100s hang) and
    # resolve the hero field lazily the moment the player loads in.
    proc, hb, ref = _build_anchor(with_hero=False)
    loc = GameAppLocator(proc, Hl(proc))
    assert loc.locate() == ref["singleton"]    # singleton found with no hero
    assert loc.reachable is True
    assert loc.anchored is False               # hero offset not resolvable yet
    assert loc.hero() is None                  # 'waiting for world'

    world_hero = _world_hero(hb, proc)
    proc.put_u64(ref["singleton"] + 0xD0, world_hero)   # player loads in
    assert loc.hero() == world_hero            # self-healed: hero offset resolved
    assert loc.anchored is True


def test_anchor_inst_not_found_returns_none():
    # Chain exists but the static object has no GameApp `inst` pointer.
    proc, hb, ref = _build_anchor(with_inst=False)
    loc = GameAppLocator(proc, Hl(proc))
    assert loc.locate() is None


# --- PlayerLocator: primary (anchor) ---------------------------------------
def test_player_primary_uses_anchor_hero():
    proc, hb, ref = _build_anchor()
    pl = PlayerLocator(proc, Hl(proc))
    assert pl.locate() == ref["hero"]
    assert pl.address == ref["hero"]


def test_live_address_follows_anchor_through_menu_and_swap():
    # The session-stable anchor must re-resolve the live hero each call so the
    # player follows menu<->world + character changes with NO rescan.
    proc, hb, ref = _build_anchor()
    pl = PlayerLocator(proc, Hl(proc))
    assert pl.locate() == ref["hero"]

    # character swap: GameApp.hero now points at a different in-world ent.Hero
    hero2 = _world_hero(hb, proc)
    proc.put_u64(ref["singleton"] + 0xD0, hero2)
    assert pl.live_address() == hero2          # re-resolved, not the cached first hero
    assert pl.address == hero2

    # main menu: GameApp.hero is null -> there is no live hero right now
    proc.put_u64(ref["singleton"] + 0xD0, 0)
    assert pl.live_address() is None           # so the watcher can mark 'located lost'
    assert pl.address is None

    # back in-world: hero present again -> re-resolves with no heap scan
    proc.put_u64(ref["singleton"] + 0xD0, ref["hero"])
    assert pl.live_address() == ref["hero"]


def test_live_address_follows_recreated_singleton():
    # The actual menu->world bug: the game RECREATES the GameApp singleton on a
    # world reload, so $App.inst points at a NEW instance. Caching the old
    # singleton reads freed memory; the locator must re-read $App.inst.
    proc, hb, ref = _build_anchor()
    pl = PlayerLocator(proc, Hl(proc))
    assert pl.locate() == ref["hero"]

    new_singleton = hb.make_instance("GameApp")
    new_hero = _world_hero(hb, proc)
    proc.put_u64(new_singleton + 0xD0, new_hero)             # new GameApp.hero
    proc.put_u64(ref["static_obj"] + ref["off_inst"], new_singleton)  # $App.inst swap
    assert pl.live_address() == new_hero
    assert pl.app._addr == new_singleton                     # cache followed the slot


def test_locate_does_not_heapscan_when_anchored_but_no_hero():
    # At the menu (anchor resolved, GameApp.hero null) locate() must return None
    # WITHOUT falling back to the expensive heap scan every relocate tick.
    proc, hb, ref = _build_anchor()
    pl = PlayerLocator(proc, Hl(proc))
    assert pl.locate() == ref["hero"]
    proc.put_u64(ref["singleton"] + 0xD0, 0)   # main menu
    # _candidates would need the ent.Hero type bootstrap; assert it's never called.
    pl._candidates = lambda: (_ for _ in ()).throw(
        AssertionError("heap scan must not run while anchored"))
    assert pl.locate() is None


# --- PlayerLocator: combat_state (game-authoritative encounter fields) ----
def test_combat_state_reads_via_reflection_when_layout_resolvable():
    # ent.Hero carries a runtime layout here, so combat_state must resolve
    # the fields by NAME (survives the drift the fixed constants can't).
    # The type only has these four fields, so its runtime table starts at
    # HL_WSIZE (8) and ascends - the shape the real resolver accepts.
    hero_fields = {
        "isInCombat": 8,
        "combatStartTime": 16,
        "combatEndTime": 24,
        "combatId": 32,
    }
    proc, hb, ref = _build_anchor(hero_fields=hero_fields)
    hero = ref["hero"]
    proc.put_i32(hero + 8, 1)
    proc.put_i32(hero + 32, 42)
    proc.put_f64(hero + 16, 100.5)
    proc.put_f64(hero + 24, 120.25)

    pl = PlayerLocator(proc, Hl(proc))
    assert pl.locate() == hero
    st = pl.combat_state()
    assert st is not None
    assert st["in_combat"] is True
    assert st["combat_id"] == 42
    assert abs(st["combat_start"] - 100.5) < 1e-9
    assert abs(st["combat_end"] - 120.25) < 1e-9


def test_combat_state_falls_back_to_fixed_offsets():
    # ent.Hero has no runtime layout -> field_offset returns None and
    # combat_state must fall back to the calibrated constants.
    proc, hb, ref = _build_anchor()
    hero = ref["hero"]
    from farever_companion.constants import (
        OFF_HERO_ISCOMBAT, OFF_HERO_COMBAT_START,
        OFF_HERO_COMBAT_END, OFF_HERO_COMBAT_ID)
    proc.put_i32(hero + OFF_HERO_ISCOMBAT, 1)
    proc.put_i32(hero + OFF_HERO_COMBAT_ID, 7)
    proc.put_f64(hero + OFF_HERO_COMBAT_START, 300.0)
    proc.put_f64(hero + OFF_HERO_COMBAT_END, 0.0)

    pl = PlayerLocator(proc, Hl(proc))
    assert pl.locate() == hero
    st = pl.combat_state()
    assert st is not None
    assert st["in_combat"] is True
    assert st["combat_id"] == 7
    assert abs(st["combat_start"] - 300.0) < 1e-9
    assert st["combat_end"] == 0.0


def test_combat_state_sanitizes_garbage_times():
    # Uninitialised / bogus engine-time fields (NaN, huge, negative) must
    # read as 0.0 rather than leaking garbage into the tracker.
    proc, hb, ref = _build_anchor()
    hero = ref["hero"]
    import math as _math
    from farever_companion.constants import (
        OFF_HERO_COMBAT_START, OFF_HERO_COMBAT_END)
    proc.put_f64(hero + OFF_HERO_COMBAT_START, _math.nan)
    proc.put_f64(hero + OFF_HERO_COMBAT_END, 1e15)

    pl = PlayerLocator(proc, Hl(proc))
    assert pl.locate() == hero
    st = pl.combat_state()
    assert st is not None
    assert st["combat_start"] == 0.0
    assert st["combat_end"] == 0.0


def test_combat_state_none_when_no_hero():
    proc, hb, _ref = _build_anchor(with_hero=False)
    pl = PlayerLocator(proc, Hl(proc))
    assert pl.locate() is None
    assert pl.combat_state() is None


def test_preview_hero_without_gamelayer_is_not_located():
    # The fake-attach bug: launching the companion at the main menu / character-
    # select, where GameApp.hero already points at a preview Hero that has NO
    # GameLayer. The anchor resolves, but locate() must NOT report that hero
    # (its scene is empty), so the tools stay locked until the player loads in.
    proc, hb, ref = _build_anchor(hero_in_world=False)
    pl = PlayerLocator(proc, Hl(proc))
    pl._candidates = lambda: (_ for _ in ()).throw(
        AssertionError("must not heap-scan once the anchor is resolved"))
    assert pl.locate() is None                 # preview hero rejected, no fake attach
    assert pl.live_address() is None
    assert pl.app.anchored                     # anchor (offsets) still resolved

    # player loads in: the same Hero now owns a GameLayer -> located, no rescan.
    proc.put_u64(ref["hero"] + OFF_GAMELAYER, ref["layer"])
    assert pl.locate() == ref["hero"]
    assert pl.live_address() == ref["hero"]


def test_menu_start_never_heapscans_then_locates_in_world():
    # The reported regression: launch at the menu (no hero), then load in. The
    # locator must NEVER touch the slow heap-scan fallback (it once wedged
    # 'located' for 100s+), and must pick up the player once in-world.
    proc, hb, ref = _build_anchor(with_hero=False)
    pl = PlayerLocator(proc, Hl(proc))
    pl._candidates = lambda: (_ for _ in ()).throw(
        AssertionError("must not heap-scan while the anchor chain is reachable"))
    assert pl.locate() is None                 # menu: reachable, waiting, no scan
    assert pl.live_address() is None

    world_hero = _world_hero(hb, proc)
    proc.put_u64(ref["singleton"] + 0xD0, world_hero)   # player loads in
    assert pl.locate() == world_hero
    assert pl.live_address() == world_hero


# --- PlayerLocator: heap-scan fallback -------------------------------------
def _build_fallback_heap(unit_counts):
    """No GameApp anchor; just ent.Hero instances each owning a GameLayer with
    a units array of the given length. Returns (proc, [hero addrs])."""
    proc = FakeProc()
    hb = HeapBuilder(proc)
    hero_tp = hb.make_type("ent.Hero")
    heroes = []
    for count in unit_counts:
        h = hb.make_instance("ent.Hero")
        gl = hb.alloc(0x200)
        # units array of `count` dummy pointers (values are irrelevant here)
        arr = hb.make_array([0x300000 + i * 8 for i in range(count)])
        proc.put_u64(gl + OFF_UNITS_ARR, arr)
        proc.put_u64(h + OFF_GAMELAYER, gl)
        heroes.append(h)
    return proc, hb, hero_tp, heroes


def test_fallback_picks_most_populated_world():
    # anchor absent -> app.hero() is None -> heap scan + heuristic. The scan is
    # opt-in (it's the slow path), so the test enables it explicitly.
    proc, hb, hero_tp, heroes = _build_fallback_heap([3, 11, 5])
    pl = PlayerLocator(proc, Hl(proc))
    pl.heapscan_fallback = True
    # sanity: the anchor really can't resolve (no GameApp type present)
    assert pl.app.locate() is None
    got = pl.locate()
    assert got == heroes[1]      # the hero whose world has the most units (11)


def test_fallback_none_when_no_heroes():
    proc = FakeProc()
    HeapBuilder(proc)            # empty heap, no ent.Hero type
    pl = PlayerLocator(proc, Hl(proc))
    pl.heapscan_fallback = True
    assert pl.locate() is None
    assert pl.address is None


def test_heapscan_fallback_off_by_default_never_scans():
    # The crux of the wedge fix: with the anchor unresolved (e.g. the game still
    # booting) and the fallback at its default (off), locate must return None
    # WITHOUT ever touching the heap scan, so the worker returns instantly and the
    # 2 s watcher keeps retrying instead of hanging.
    proc, hb, hero_tp, heroes = _build_fallback_heap([3, 11, 5])
    pl = PlayerLocator(proc, Hl(proc))
    assert pl.heapscan_fallback is False       # default
    pl._candidates = lambda: (_ for _ in ()).throw(
        AssertionError("heap scan must not run when the fallback is off"))
    assert pl.locate() is None
    assert pl.live_address() is None
