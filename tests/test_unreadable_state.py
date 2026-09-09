"""A failed game read must not change what the HUD is doing.

`OverlayManager._combat_tick` makes several decisions from game state every
`mouse_snap_rate` ms, and each of them can be asked a question the reads can't
answer: a stale UI pointer, a scene swap, a freed object, a combat field that
didn't come back. Treating "couldn't read" as a definite answer changes the HUD
for no reason - the HUD flashes back over an open menu, the minimap reappears
inside a dungeon, or the click-through lock releases and the overlay eats the
clicks the player meant for the game.

The rule (documented in farever_companion/ui/game_window.py): act only on a
value the read actually produced, otherwise keep the last PROVEN one. This file
pins that per decision, driving the real `_combat_tick` with only the Win32
probes and the model's answers stubbed. The one deliberate exception is hiding
on focus loss - asserted below - because a hidden overlay self-heals on the
next tick while a wrong recentre or a stolen click does not.
"""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtWidgets

from farever_companion.core.model import LiveModel
from farever_companion.ui import overlay_manager as om
from farever_companion.core.game_state import Held
from farever_companion.ui.game_window import _click_through_decision

GAME_HWND = 0x1234


# --- the two pure pieces -----------------------------------------------------
@pytest.mark.parametrize("state,expected", [(True, True), (False, False)])
def test_held_state_passes_through_a_definite_answer(state, expected):
    h = Held(default=True)
    assert h.update(state) is expected
    assert h.value is expected          # ...and remembers it


def test_held_state_keeps_the_last_proven_value_when_the_read_fails():
    h = Held()
    assert h.update(False) is False
    assert h.update(None) is False      # a hiccup doesn't turn it into a menu
    assert h.update(True) is True
    for _ in range(5):
        assert h.update(None) is True   # ...or clear it


def test_held_state_defaults_before_the_first_read():
    assert Held().update(None) is False
    assert Held(default=True).update(None) is True


@pytest.mark.parametrize("sources,expected", [
    ([], False),                      # nothing switched on: proven free
    ([False], False),
    ([False, False], False),
    ([True], True),
    ([True, False], True),            # positive evidence wins outright
    ([True, None], True),
    ([None], None),                   # only unknowns: don't touch the lock
    ([False, None], None),
    ([None, None], None),
    ([False, None, False], None),
])
def test_click_through_decision(sources, expected):
    assert _click_through_decision(sources) is expected


# --- the tick: menu hiding ---------------------------------------------------
class _Overlay:
    """Duck-typed overlay: the tick only asks/acts on visibility + click-through."""

    def __init__(self, visible: bool = True):
        self.visible = visible
        self.click_through: list[bool] = []

    def isVisible(self) -> bool:
        return self.visible

    def show(self) -> None:
        self.visible = True

    def hide(self) -> None:
        self.visible = False

    def set_click_through(self, on: bool) -> None:
        self.click_through.append(on)


class _Model:
    """A model whose answers the test can flip, including to 'unreadable'."""

    def __init__(self):
        self.menu: bool | None = False        # None = the gate can't be evaluated
        self.menu_plain: bool | None = False  # same read, minus the escape menu
        self.menu_walks = 0                   # proves the tick walks the list once
        self.instance: tuple[bool, bool] | None = (False, False)
        self.player_addr: int | None = 0x1000
        self.proc = None
        self.scene = SimpleNamespace(map_id=lambda _a: None,
                                     activity_id=lambda _a: None)
        self.locator = SimpleNamespace(in_combat=lambda: False)

    def set_menu(self, any_state: bool | None,
                 plain_state: bool | None = None) -> None:
        """Set the gate: `any_state` includes the escape menu, `plain_state`
        (defaulting to the same) is the gameplay-windows read Rule B uses."""
        self.menu = any_state
        self.menu_plain = any_state if plain_state is None else plain_state

    def menu_windows(self):
        """The real model's single-walk gate: both answers or None."""
        self.menu_walks += 1
        if self.menu is None:
            return None
        return (self.menu, self.menu_plain)

    def menu_state(self, include_escape: bool = True):
        return self.menu if include_escape else self.menu_plain

    # The boolean fallback: a model with no `instance_state` (and, in this
    # fake, the same "read failed" shape - the scene raising).
    def is_in_dungeon(self) -> bool:
        if self.instance is None:
            raise RuntimeError("scene read failed")
        return bool(self.instance[0])

    def is_in_rift(self) -> bool:
        if self.instance is None:
            raise RuntimeError("scene read failed")
        return bool(self.instance[1])

    def instance_state(self):
        return self.instance

    def player_profile(self):
        return "Tester"


@pytest.fixture(scope="module", autouse=True)
def _qapp():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _settings(**over) -> SimpleNamespace:
    s = SimpleNamespace(
        snap_mouse_to_player=False,
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


def _manager(monkeypatch, settings, model, *, cursor_visible=True,
             game_focused=True, app_focused=False):
    monkeypatch.setattr(om, "_foreground_state",
                        lambda pid: (GAME_HWND, game_focused, app_focused))
    monkeypatch.setattr(om, "_cursor_visible", lambda: cursor_visible)
    mgr = om.OverlayManager(settings)
    mgr.model = model
    mgr._ever_located = True
    mgr.overlays.update({k: _Overlay() for k in
                        ("entity", "map", "speedrun", "dungeon", "dps")})
    mgr.tracker._needle = None
    mgr.dungeon_tracker._needle = None
    return mgr


def _hidden(mgr) -> set[str]:
    return {k for k, ov in mgr.overlays.items() if ov is not None and not ov.isVisible()}


# --- menus: hold, in both directions -----------------------------------------
def test_a_hidden_hud_stays_hidden_when_the_menu_read_fails(monkeypatch):
    """The inventory window hides everything (Rule B); a gate that then fails
    must not re-show the HUD on top of it."""
    model = _Model()
    mgr = _manager(monkeypatch, _settings(auto_hide_menus=True), model)
    model.set_menu(True)
    mgr._combat_tick()
    assert _hidden(mgr) == {"entity", "map", "speedrun", "dps", "dungeon"}

    model.set_menu(None)                  # stale UI pointer / read didn't come back
    mgr._combat_tick()
    assert _hidden(mgr) == {"entity", "map", "speedrun", "dps", "dungeon"}

    model.set_menu(False)                 # ...and it recovers the moment it can read
    mgr._combat_tick()
    # Both instance-only HUDs stay hidden: this model is open world (Rule D).
    assert _hidden(mgr) - {"dungeon", "speedrun"} == set()


# --- menus: one walk, both rules ---------------------------------------------
def test_the_menu_walk_runs_once_per_tick(monkeypatch):
    """Rule A (escape menu), Rule B (auto-hide) and the cursor gate are three
    questions about the same tick; one walk of `ui.GameUI.windows` answers all
    three, so the tick doesn't pay for the same memory walk three times."""
    model = _Model()
    mgr = _manager(monkeypatch, _settings(), model)
    model.set_menu(True, True)
    mgr._combat_tick()
    assert model.menu_walks == 1
    mgr._combat_tick()
    assert model.menu_walks == 2


def test_both_rules_read_the_same_walk(monkeypatch):
    """A model whose walk is inconsistent (a gameplay window with no window at
    all) can't split the tick: both flags come from that single call."""
    model = _Model()
    mgr = _manager(monkeypatch, _settings(auto_hide_menus=True), model)
    model.set_menu(True, False)          # what the walk reports: escape only
    mgr._combat_tick()
    assert "entity" not in _hidden(mgr)


def test_a_clear_screen_stays_clear_when_the_menu_read_fails(monkeypatch):
    """The inverse: unknown must not read as 'a menu is up', or the HUD blinks
    out on every read hiccup."""
    model = _Model()
    mgr = _manager(monkeypatch, _settings(auto_hide_menus=True), model)
    mgr._combat_tick()                    # proven clear: HUD is up
    assert _hidden(mgr) - {"dungeon", "speedrun"} == set()

    model.set_menu(None)
    mgr._combat_tick()
    assert _hidden(mgr) - {"dungeon", "speedrun"} == set()


def test_the_escape_menu_still_hides_by_rule_a_through_a_failed_read(monkeypatch):
    """Rule A (escape menu) has its own held latch: combat hides, entity stays."""
    model = _Model()
    mgr = _manager(monkeypatch, _settings(auto_hide_menus=False), model)
    model.set_menu(True, False)           # escape menu only
    mgr._combat_tick()
    assert "entity" not in _hidden(mgr)

    model.set_menu(None)                  # the whole read failed
    mgr._combat_tick()
    assert "entity" not in _hidden(mgr)


# --- dungeon / rift ----------------------------------------------------------
# --- the model's tri-state itself -------------------------------------------
def _bare_model(scene):
    """A LiveModel with only the scene surface `instance_state` touches."""
    m = LiveModel.__new__(LiveModel)
    m.locator = SimpleNamespace(live_address=lambda: 0x1000)
    m.scene = scene
    return m


def test_instance_state_reports_the_instance():
    m = _bare_model(SimpleNamespace(gamelayer=lambda _a: 0x2000,
                                    in_dungeon=lambda _a: True,
                                    is_rift=lambda _a: False))
    assert m.instance_state() == (True, False)


def test_instance_state_is_empty_without_a_located_player():
    """A player who isn't there is knowledge, not a missing read."""
    m = _bare_model(SimpleNamespace(gamelayer=lambda _a: 0x2000,
                                    in_dungeon=lambda _a: True,
                                    is_rift=lambda _a: True))
    m.locator = SimpleNamespace(live_address=lambda: None)
    assert m.instance_state() == (False, False)


@pytest.mark.parametrize("scene", [
    SimpleNamespace(gamelayer=lambda _a: None),                 # no GameLayer
    SimpleNamespace(gamelayer=lambda _a: (_ for _ in ()).throw(
        RuntimeError("stale scene"))),                          # read failed
])
def test_instance_state_is_none_when_the_scene_cant_be_read(scene):
    assert _bare_model(scene).instance_state() is None


# --- dungeon / rift: the tick


def test_an_instance_stays_an_instance_when_the_scene_read_fails(monkeypatch):
    """Un-hiding the minimap inside a dungeon (and hiding the Dungeon HUD) was
    the old failure mode of a failed scene read."""
    model = _Model()
    mgr = _manager(monkeypatch, _settings(), model)
    model.instance = (True, False)
    mgr._combat_tick()
    assert "map" in _hidden(mgr) and "entity" in _hidden(mgr)
    assert "dungeon" not in _hidden(mgr)

    model.instance = None                 # scene swap / freed GameLayer
    mgr._combat_tick()
    assert "map" in _hidden(mgr) and "entity" in _hidden(mgr)
    assert "dungeon" not in _hidden(mgr)

    model.instance = (False, False)       # back outside: recovers
    mgr._combat_tick()
    assert "map" not in _hidden(mgr)
    assert "dungeon" in _hidden(mgr)      # instance-only overlay


def test_the_boolean_fallback_holds_a_raising_read_too(monkeypatch):
    """A model with no `instance_state` (duck-typed) takes the boolean pair;
    a raise there must still hold, not un-hide the HUD inside an instance."""
    model = _Model()
    monkeypatch.delattr(_Model, "instance_state")
    mgr = _manager(monkeypatch, _settings(), model)
    model.instance = (True, False)
    mgr._combat_tick()
    assert "map" in _hidden(mgr)

    model.instance = None                 # the pair now raises
    mgr._combat_tick()
    assert "map" in _hidden(mgr)
    assert "dungeon" not in _hidden(mgr)


# --- click-through -----------------------------------------------------------
def _click_through_state(mgr) -> list[bool]:
    return [ov.click_through[-1] for ov in mgr.overlays.values()
            if ov is not None and ov.click_through]


def test_the_lock_survives_an_unreadable_combat_read(monkeypatch):
    """Releasing the lock on a failed read hands the player's clicks to the
    overlay mid-fight - the exact thing the feature exists to prevent."""
    model = _Model()
    model.locator = SimpleNamespace(in_combat=lambda: True)
    mgr = _manager(monkeypatch, _settings(combat_click_through=True), model,
                   cursor_visible=True)          # no mouse-park evidence
    mgr._combat_tick()
    assert mgr._combat_lock_active and set(_click_through_state(mgr)) == {True}

    model.locator = SimpleNamespace(in_combat=lambda: None)   # can't judge
    mgr._combat_tick()
    assert mgr._combat_lock_active

    model.locator = SimpleNamespace(in_combat=lambda: False)  # proven: not fighting
    mgr._combat_tick()
    assert not mgr._combat_lock_active
    assert set(_click_through_state(mgr)) == {False}


def test_a_disabled_combat_source_does_not_freeze_the_lock(monkeypatch):
    """A switched-off check is absent, not unknown - or the lock would never
    release while the game is not focused."""
    model = _Model()
    model.locator = SimpleNamespace(in_combat=lambda: True)
    mgr = _manager(monkeypatch, _settings(combat_click_through=False), model,
                   game_focused=False, cursor_visible=True)
    mgr._combat_tick()
    assert not mgr._combat_lock_active


def test_no_located_hero_is_not_in_combat(monkeypatch):
    """`in_combat()` answers None with no hero. That is knowledge (you can't be
    fighting with no located hero), so it must release rather than hold."""
    model = _Model()
    mgr = _manager(monkeypatch, _settings(combat_click_through=True), model)
    assert mgr._combat_source(model) is False          # located: defers to locator

    model.player_addr = None
    model.locator = SimpleNamespace(in_combat=lambda: None)
    assert mgr._combat_source(model) is False

    model.player_addr = 0x1000
    assert mgr._combat_source(model) is None           # hero located, read failed


def test_a_raising_combat_read_is_unknown_not_free(monkeypatch):
    model = _Model()

    def _boom():
        raise RuntimeError("stale read")

    model.locator = SimpleNamespace(in_combat=_boom)
    mgr = _manager(monkeypatch, _settings(combat_click_through=True), model)
    assert mgr._combat_source(model) is None


# --- the deliberate exception ------------------------------------------------
def test_focus_loss_still_fails_closed(monkeypatch):
    """Hiding is the one decision allowed to act on a failed read: a hidden
    overlay self-heals on the next tick, and the alternative (HUD left over
    another app) is the thing focus loss is meant to stop."""
    model = _Model()
    mgr = _manager(monkeypatch, _settings(), model, game_focused=False,
                   app_focused=False)     # neither the game nor the app is up
    mgr._combat_tick()
    assert _hidden(mgr) - {"dungeon"} == {"entity", "map", "speedrun", "dps"}
