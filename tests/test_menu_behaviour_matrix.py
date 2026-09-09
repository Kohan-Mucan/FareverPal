"""The menu matrix: for every menu the game can show, which overlays hide and
whether the cursor is recentered.

Hiding and recentering are two answers to the same question ("is a menu up?")
and they drifted apart in the 2026-09-11 patch, when the gate read
`additionalTips` instead: overlays stayed drawn over open menus AND the cursor
kept being recentred inside them. This drives the real
`OverlayManager._combat_tick` against the real `LiveModel.menu_state` over the
FakeProc/HeapBuilder harness - only the Win32 probes are stubbed - and asserts
both halves per scenario, so the two paths can't disagree about what counts as a
menu again.

`dungeon` and `compass` hide in every row for reasons that aren't menus (the
Dungeon HUD is instance-only and the compass has no target), which is why the
cross-check below subtracts them before blaming a menu.
"""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtWidgets

from farever_companion.core import game_state
from farever_companion.ui import overlay_manager as om
from tests.fakemem import FakeProc, HeapBuilder
from tests.menu_fakes import (NEW_WINDOWS, menu_model, shifted_fields,
                              ui_with_windows)

GAME_HWND = 0x1234

# scenario -> the windows open on ui.GameUI (a real window array is built from
# this, so the model's own gate classifies it - nothing here restates the rules)
_SCENARIOS = {
    "no menu": (),
    "escape menu": ("ui.win.EscapeMenu",),
    "inventory window": ("ui.win.InventoryUI",),
    "escape + inventory": ("ui.win.InventoryUI", "ui.win.EscapeMenu"),
}

# Keys that hide whatever the menu state is: the Dungeon HUD and the Speed Run
# timer are instance-only (Rule D — these scenarios are all open world), the Test
# Dummy HUD is dummy-only with its card off by default here (Rule F) and the
# compass has no live target, so none of them is a menu signal.
_ALWAYS_HIDDEN = {"dungeon", "speedrun", "dummy", "compass"}

# auto_hide_menus -> scenario -> keys that must end up hidden.
# Rule A (escape menu) exempts entity/map/dungeon/dps/speedrun; Rule B
# (auto_hide_menus, any other window) hides everything.
_EXPECTED_HIDDEN = {
    True: {
        "no menu": _ALWAYS_HIDDEN,
        "escape menu": {*_ALWAYS_HIDDEN},
        "inventory window": {"entity", "map", "dps", *_ALWAYS_HIDDEN},
        "escape + inventory": {"entity", "map", "dps", *_ALWAYS_HIDDEN},
    },
    False: {
        "no menu": _ALWAYS_HIDDEN,
        "escape menu": {*_ALWAYS_HIDDEN},
        "inventory window": set(_ALWAYS_HIDDEN),
        "escape + inventory": set(_ALWAYS_HIDDEN),
    },
}


class _Overlay:
    """Duck-typed overlay window: the tick only ever asks/acts on visibility."""

    def __init__(self, visible: bool = True):
        self.visible = visible
        self.shows = 0
        self.hides = 0

    def isVisible(self) -> bool:
        return self.visible

    def show(self) -> None:
        self.visible = True
        self.shows += 1

    def hide(self) -> None:
        self.visible = False
        self.hides += 1


@pytest.fixture(scope="module", autouse=True)
def _qapp():
    """One offscreen QApplication for the whole module (the manager is a QObject)."""
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _settings(**over) -> SimpleNamespace:
    s = SimpleNamespace(
        snap_mouse_to_player=True,
        auto_hide_menus=True,
        mouse_snap_rate=500,
        combat_click_through=False,
        lock_overlays=False,
        open_overlay_dungeon=True,
        open_overlay_speedrun=True,
        dps_instances_only=False,
        track_kind="",
        track_id="",
    )
    s.save = lambda: None
    for k, v in over.items():
        setattr(s, k, v)
    return s


def _tickable_model(proc, ui, in_dungeon: bool = False, at_dummy: bool = False,
                    in_rift: bool = False):
    """The menu gate stays REAL; the rest of the model surface `_combat_tick`
    touches is stubbed so the tick can run with no game attached."""
    m = menu_model(proc, ui, in_dungeon=in_dungeon, in_rift=in_rift)
    m.proc = None                                   # not attached
    m.scene = SimpleNamespace(map_id=lambda _a: None, activity_id=lambda _a: None)
    m.is_in_dungeon = lambda: in_dungeon
    m.is_in_rift = lambda: in_rift
    m.player_profile = lambda: "Tester"
    units = ([SimpleNamespace(unit_id="TrainingDummy", hp=1.0)] if at_dummy
             else [])
    m.enemies = lambda: units                       # the dummy read's scene walk
    return m


def _manager(monkeypatch, settings, model, cursor_locked):
    """A real OverlayManager whose only stubs are the Win32 probes."""
    monkeypatch.setattr(om, "_foreground_state",
                        lambda pid: (GAME_HWND, True, False))   # game focused
    monkeypatch.setattr(om, "_cursor_lock_state", lambda hwnd: cursor_locked)
    monkeypatch.setattr(om, "_cursor_visible", lambda: True)    # no mouse-park
    recentered: list[int] = []
    monkeypatch.setattr(om, "_recenter_cursor",
                        lambda hwnd: recentered.append(hwnd) or True)

    mgr = om.OverlayManager(settings)
    mgr.model = model
    mgr._ever_located = True
    overlays = {k: _Overlay() for k in
                ("entity", "map", "speedrun", "dungeon", "dps", "dummy",
                 "compass")}
    mgr.overlays.update({k: v for k, v in overlays.items() if k != "compass"})
    mgr.tracker._needle = overlays["compass"]       # the tick injects this key
    mgr.dungeon_tracker._needle = None
    return mgr, overlays, recentered


def _run(scenario: str, auto_hide: bool, cursor_locked, monkeypatch):
    proc, ui = ui_with_windows(shifted_fields(), _SCENARIOS[scenario])
    model = _tickable_model(proc, ui)
    mgr, overlays, recentered = _manager(
        monkeypatch, _settings(auto_hide_menus=auto_hide), model, cursor_locked)
    mgr._combat_tick()
    hidden = {k for k, ov in overlays.items() if not ov.isVisible()}
    return model.menu_state(), hidden, bool(recentered)


@pytest.mark.parametrize("auto_hide", [True, False])
@pytest.mark.parametrize("scenario", list(_SCENARIOS))
@pytest.mark.parametrize("cursor_locked", [True, False])
def test_menu_matrix(monkeypatch, scenario, auto_hide, cursor_locked):
    _state, hidden, recentered = _run(scenario, auto_hide, cursor_locked, monkeypatch)

    assert hidden == _EXPECTED_HIDDEN[auto_hide][scenario]
    # only a menu-free screen AND a game-held cursor may move the mouse
    assert recentered == (scenario == "no menu" and cursor_locked)
    assert recentered == (scenario == "no menu" and cursor_locked)


@pytest.mark.parametrize("in_dungeon", [False, True])
def test_instance_only_huds_show_on_zone_in_and_only_then(monkeypatch, in_dungeon):
    """Rule D, the Speed Run timer included: outside an instance that window has
    nothing to show, so it hides exactly like the Dungeon HUD and comes back on
    zone-in. Both share one predicate, so the two can never disagree."""
    proc, ui = ui_with_windows(shifted_fields(), ())
    model = _tickable_model(proc, ui, in_dungeon=in_dungeon)
    mgr, overlays, _ = _manager(monkeypatch, _settings(), model, False)
    mgr._combat_tick()
    assert overlays["speedrun"].isVisible() is in_dungeon
    assert overlays["dungeon"].isVisible() is in_dungeon


@pytest.mark.parametrize("in_dungeon", [False, True])
def test_map_change_never_resets_or_starts_run_timer(monkeypatch, in_dungeon):
    """Map IDs can resolve late; only the real instance edge owns RUN."""
    proc, ui = ui_with_windows(shifted_fields(), ())
    model = _tickable_model(proc, ui, in_dungeon=in_dungeon)
    model.scene.map_id = lambda _a: "ZoneAfterChange"
    mgr, overlays, _ = _manager(monkeypatch, _settings(), model, False)
    mgr._last_map_id = "ZoneBeforeChange"

    calls = []
    overlays["speedrun"].reset = lambda prime_zone=None: calls.append(prime_zone)
    mgr._combat_tick()

    assert calls == []


def test_a_training_dummy_is_not_an_instance_for_the_run_timer(monkeypatch):
    """A training dummy is not an instance for the run timer: with no manual
    start and no instance, the Speed Run HUD stays hidden there — a clock that
    could never run would just be clutter."""
    proc, ui = ui_with_windows(shifted_fields(), ())
    model = _tickable_model(proc, ui, at_dummy=True)
    mgr, overlays, _ = _manager(monkeypatch, _settings(), model, False)
    mgr._combat_tick()
    assert not overlays["speedrun"].isVisible()
    assert not overlays["dungeon"].isVisible()


@pytest.mark.parametrize("dummy_hud", [False, True])
def test_the_meter_keeps_no_bench_at_a_dummy(monkeypatch, dummy_hud):
    """Stripped dummy logic: with "Instances Only" on, the meter stays hidden
    at a training dummy whether the Test Dummy HUD is up or not — no
    pop-then-close handover when the dummy board surfaces. The bench belongs
    to the dummy HUD alone."""
    proc, ui = ui_with_windows(shifted_fields(), ())
    model = _tickable_model(proc, ui, at_dummy=True)
    # "at a dummy" is the TRACKER's radius verdict (DpsTracker.dummy_within),
    # so the harness model needs one: the scene alone is not the answer.
    model.dps = SimpleNamespace(dummy_within=lambda rng: True)
    mgr, overlays, _ = _manager(
        monkeypatch,
        _settings(dps_instances_only=True, open_overlay_dummy=dummy_hud),
        model, False)
    mgr._combat_tick()

    assert overlays["dps"].isVisible() is False
    assert overlays["dummy"].isVisible() is dummy_hud


@pytest.mark.parametrize("where", ["open world", "dungeon", "rift"])
def test_the_meters_instances_only_toggle_reads_one_zone_answer(
        monkeypatch, where):
    """The meter's "Instances Only" row, driven through the real loop: hidden in
    the open world, shown in a dungeon or a rift — and hidden at a training
    dummy too, since the row keeps no bench (that seat is the dummy HUD's).

    The three cases are one row's answer, which is the point of the placement
    table: the meter, the Dungeon HUD and the dummy HUD now read the same zone
    fact instead of each asking their own question.
    """
    proc, ui = ui_with_windows(shifted_fields(), ())
    model = _tickable_model(proc, ui,
                            in_dungeon=(where == "dungeon"),
                            in_rift=(where == "rift"))
    # no dummy anywhere in this half
    model.dps = SimpleNamespace(dummy_within=lambda rng: False)
    mgr, overlays, _ = _manager(
        monkeypatch, _settings(dps_instances_only=True), model, False)
    mgr._combat_tick()
    assert overlays["dps"].isVisible() is (where != "open world")

    # no bench kept: an open-world dummy hides it like the open world does
    proc2, ui2 = ui_with_windows(shifted_fields(), ())
    bench = _tickable_model(proc2, ui2, at_dummy=True)
    bench.dps = SimpleNamespace(dummy_within=lambda rng: True)
    mgr, overlays, _ = _manager(
        monkeypatch, _settings(dps_instances_only=True), bench, False)
    mgr._combat_tick()
    assert overlays["dps"].isVisible() is False


@pytest.mark.parametrize("where", ["open world", "dungeon", "rift"])
def test_the_dummy_hud_surfaces_at_a_dummy_only_outside_an_instance(
        monkeypatch, where):
    """Rule F's second gate: a dummy in range is not enough inside an instance.

    The dummies live in the open-world hubs, so a scan reporting one in a
    dungeon/rift is either a stale read or a unit that merely looks like one —
    and this window is Rule D's gate pointed at the other side, which is what
    keeps it from sitting over the Dungeon HUD with a test nobody can run.
    Outside an instance the same dummy is exactly where the window belongs.
    """
    proc, ui = ui_with_windows(shifted_fields(), ())
    model = _tickable_model(proc, ui, at_dummy=True,
                            in_dungeon=(where == "dungeon"),
                            in_rift=(where == "rift"))
    # "at a dummy" is the TRACKER's radius verdict, not the scene's
    model.dps = SimpleNamespace(dummy_within=lambda rng: True)
    mgr, overlays, _ = _manager(
        monkeypatch, _settings(open_overlay_dummy=True), model, False)
    mgr._combat_tick()

    assert overlays["dummy"].isVisible() is (where == "open world")


def test_a_dummy_range_change_reaches_the_core_read_and_repaints_the_hud(
        monkeypatch):
    """Dummy Range is not a display knob: `dummy_in_range` is
    `dummy_within(dummy_range)`, and it gates engaging a test, ending one when
    the player walks away, the meter's dummy board and this HUD's own row. The
    1 Hz drain usually carries the number, so a stepper moved while the answer
    is on screen would leave the tracker acting on — and the window painting —
    the radius it had before the edit. The manager hands it over and repaints in
    the same frame; the show/hide half stays the tick's job.
    """
    proc, ui = ui_with_windows(shifted_fields(), ())
    model = _tickable_model(proc, ui)
    mgr, overlays, _recentered = _manager(monkeypatch, _settings(), model, False)

    core = SimpleNamespace(dummy_range=5.0)
    model.dps = core
    repaints: list[str] = []
    overlays["dummy"].refresh_now = lambda: repaints.append("dummy")

    mgr.apply_dummy_range(35)
    assert core.dummy_range == 35.0                 # the gate's own number
    assert repaints == ["dummy"]                    # repainted in the same frame

    # An unmigrated profile hands over a value the tracker cannot take: the
    # number is left alone, and the repaint still happens (the window reads the
    # setting itself, so it is right either way).
    mgr.apply_dummy_range("")
    assert core.dummy_range == 35.0
    assert repaints == ["dummy", "dummy"]

    # A detached model and a window that does not exist yet are both fine:
    # this runs from a settings click, which can happen before an attach.
    model.dps = None
    mgr.overlays["dummy"] = None
    mgr.apply_dummy_range(5)
    assert repaints == ["dummy", "dummy"]


def test_a_card_switched_off_never_comes_back_inside_a_dungeon(monkeypatch):
    """The gate honors card intent in BOTH directions: switching the Speed Run
    card off mid-session must not be undone by walking into a dungeon."""
    proc, ui = ui_with_windows(shifted_fields(), ())
    model = _tickable_model(proc, ui, in_dungeon=True)
    mgr, overlays, _ = _manager(
        monkeypatch, _settings(open_overlay_speedrun=False), model, False)
    mgr._combat_tick()
    assert not overlays["speedrun"].isVisible()
    assert overlays["dungeon"].isVisible()      # the other card is untouched


@pytest.mark.parametrize("scenario", list(_SCENARIOS))
def test_the_tick_walks_the_window_list_exactly_once(monkeypatch, scenario):
    """Rule A, Rule B and the cursor gate are three questions about one tick,
    all answered by ONE walk of `ui.GameUI.windows` - it used to be three
    separate passes over the array (plus three reflection probes)."""
    proc, ui = ui_with_windows(shifted_fields(), _SCENARIOS[scenario])
    model = _tickable_model(proc, ui)
    walks: list[int] = []
    real = model.menu_windows
    model.menu_windows = lambda: (walks.append(1), real())[1]

    mgr, _overlays, _recentered = _manager(
        monkeypatch, _settings(), model, cursor_locked=True)
    mgr._combat_tick()

    assert len(walks) == 1
    assert model.menu_state() is (scenario != "no menu")   # still the same gate


def test_the_tick_publishes_one_snapshot_every_reader_shares(monkeypatch):
    """The rules and the overlays read the SAME object: the tick publishes it on
    the model, so an overlay asking for the instance state gets that answer
    instead of walking memory again (and can't contradict the rules)."""
    proc, ui = ui_with_windows(shifted_fields(), ("ui.win.InventoryUI",))
    model = _tickable_model(proc, ui)
    mgr, _overlays, _recentered = _manager(
        monkeypatch, _settings(), model, cursor_locked=True)
    mgr._combat_tick()

    state = model.published_state
    assert state is not None and state is mgr.state
    assert state.tick == 1
    assert state.held_menu_gameplay is True       # the open inventory window
    assert state.in_instance is False
    # ...and it is what the shared accessors answer with
    assert game_state.published_state(model) is state
    assert game_state.in_instance(model) is False
    assert game_state.boss(model) == state.dungeon_boss


def test_a_malformed_window_list_holds_the_hud(monkeypatch):
    """End-to-end over the REAL gate and REAL manager: when the window list
    stops adding up, both paths take the fail-safe side - the HUD holds the
    state it had, and the cursor stops being recentred. The old boolean folded
    this into "no menu", which re-showed everything and moved the mouse."""
    proc = FakeProc()
    hb = HeapBuilder(proc)
    base = hb.make_type("ui.win.BaseWindow")
    hb.make_type("ui.win.InventoryUI", super_type=base)
    hb.make_type("ui.GameUI", fields=shifted_fields())
    ui = hb.make_instance("ui.GameUI")
    proc.put_u64(ui + NEW_WINDOWS, hb.make_array([]))    # a proven-clear screen

    model = _tickable_model(proc, ui)
    mgr, overlays, recentered = _manager(
        monkeypatch, _settings(auto_hide_menus=True), model, cursor_locked=True)
    mgr._combat_tick()
    assert {k for k, ov in overlays.items() if not ov.isVisible()} == _ALWAYS_HIDDEN
    assert recentered                       # clean screen + held cursor: it did

    recentered.clear()
    proc.put_u64(ui + NEW_WINDOWS, hb.make_array([hb.make_string("junk")]))
    assert model.menu_windows() is None     # nothing about this adds up
    mgr._combat_tick()
    assert {k for k, ov in overlays.items() if not ov.isVisible()} == _ALWAYS_HIDDEN
    assert not recentered                   # unknown is as much a no as a menu


def test_the_two_paths_read_the_same_menu_gate(monkeypatch):
    """Cross-check over the WHOLE matrix: the recentre and the overlay rules
    must consume the SAME gate value. The gate is read straight off the model
    for each row, so this catches the 0x90-style drift - a window list the
    overlay path sees but the cursor path doesn't (or vice versa)."""
    for scenario in _SCENARIOS:
        for auto_hide in (True, False):
            for cursor_locked in (True, False):
                state, hidden, recentered = _run(
                    scenario, auto_hide, cursor_locked, monkeypatch)
                where = f"{scenario!r} auto_hide={auto_hide} locked={cursor_locked}"

                # the scenario really is the menu the gate reports
                assert state == (scenario != "no menu"), where
                # ...and the cursor path follows that gate, not a second read
                assert recentered == (state is False and cursor_locked), where
                if state is False:
                    assert hidden == _ALWAYS_HIDDEN, where
                # an escape menu leaves the normal HUD visible


def test_zone_in_only_builds_huds_that_can_exist_inside(monkeypatch):
    """Zoning into a rift must build the HUDs that can show there, one per
    tick — and must not spend a build on the windows that cannot.

    Two halves of one bug. The locate restore used to queue every enabled
    window that was merely HIDDEN, so a zone round-trip (whose loading screen
    loses and then finds the player, i.e. a locate flap) re-requested four
    windows and each request SHOWED one for the tick before the loop hid it
    again. And the restore runs once, so it cannot be the thing that builds the
    instance pair either — which is why the tick offers a missing window whose
    place has arrived. Together: map/entity are never built inside an instance
    (their row says they cannot exist there), while the Dungeon HUD and the Run
    Timer are built on zone-in without a burst.
    """
    proc, ui = ui_with_windows(shifted_fields(), ())
    model = _tickable_model(proc, ui, in_rift=True)
    s = _settings(open_overlay_entity=True, open_overlay_map=True,
                  open_overlay_dps=True, open_overlay_dummy=False)
    mgr, _overlays, _ = _manager(monkeypatch, s, model, False)
    for k in om.HUD_OVERLAYS:
        mgr.overlays[k] = None            # nothing built yet this session
    built: list[str] = []

    def _fake_request(key, on):
        # a real request() leaves the window EXISTING, and that is what stops
        # the tick from offering it again — so the stub has to do the same
        built.append(key)
        if on:
            mgr.overlays[key] = _Overlay()

    monkeypatch.setattr(mgr, "request", _fake_request)

    # one tick offers at most ONE window, and never an instance-blind one
    mgr._combat_tick()
    assert len(mgr._stagger_queue) == 1, mgr._stagger_queue
    assert not (set(mgr._stagger_queue) & {"entity", "map"}), mgr._stagger_queue

    for _ in range(12):                   # drain a build, then let the tick offer
        mgr._drain_stagger_sync()
        mgr._combat_tick()
    assert {"speedrun", "dungeon", "dps"} <= set(built), built
    assert "entity" not in built and "map" not in built, built

    # back in the open world their place exists again, so they are offered
    # (`instance_state` is the LIVE tri-state read, so the transition is made
    # where the tick actually reads it, not on the boolean shim)
    model.instance_state = lambda: (False, False)
    for _ in range(12):
        mgr._drain_stagger_sync()
        mgr._combat_tick()
    assert {"entity", "map"} <= set(built), built


def test_a_locate_flap_does_not_re_request_hidden_windows(monkeypatch):
    """Hidden is not missing: only windows that do not EXIST are re-queued.

    The second locate of a zone round-trip used to find four built windows
    hidden by their own placement rule and queue all of them — the all-at-once
    pass this queue exists to prevent, on a tick that is already loading a new
    zone.
    """
    proc, ui = ui_with_windows(shifted_fields(), ())
    model = _tickable_model(proc, ui)
    s = _settings(open_overlay_entity=True, open_overlay_map=True,
                  open_overlay_dps=True)
    mgr, overlays, _ = _manager(monkeypatch, s, model, False)
    for ov in overlays.values():
        ov.visible = False                # built, and placed out of sight
    mgr._pending_opens.clear()

    mgr.on_located(True)                  # the zone round-trip's second locate

    # nothing to build, so nothing is queued: the four hidden windows keep
    # their placement and the loop will surface whichever of them a place
    # allows on the very next tick
    assert mgr._stagger_queue == [], mgr._stagger_queue
