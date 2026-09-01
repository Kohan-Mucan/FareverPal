"""Rift alert chime scheduler (headless).

Drives ControlPanel._check_rift_ding with fake timer ticks (monkeypatched
`time.time`) and records `play_sound` calls. Verifies the once-per-lead-time-
per-rift contract: exactly one chime per selected lead time per hour, never a
duplicate in the same second, and never a burst when a later tick catches up
past several thresholds (sleep / busy UI / opening mid-warning).
"""
import time

import pytest

from farever_companion.ui import control_panel as cp_mod
from farever_companion.ui.control_panel import ControlPanel


class _S:
    def __init__(self):
        self.show_rift_timer = "Always"
        self.rift_ding = [15, 10, 5, 1]
        self.rift_sound = "ding"


class _Panel:
    """Minimal stand-in exposing what _check_rift_ding touches."""

    def __init__(self):
        self.s = _S()
        self._log_lines = []

    def log(self, msg: str) -> None:
        self._log_lines.append(msg)


class _Clock:
    """Mutable clock: `now` can be advanced between ticks."""

    def __init__(self):
        # Anchor "now" just inside a warning window: target = next top of hour.
        hour = int(time.time() // 3600)
        self.target = (hour + 1) * 3600
        self.now = self.target - 900 + 0.2   # :45:00.2, 15 min remaining

    def tick_to(self, secs_remaining: int) -> None:
        """Move the wall clock to (target - remaining) + a hair inside that
        second, matching how the sidebar sees whole-second countdowns."""
        self.now = self.target - secs_remaining + 0.2


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(cp_mod.time, "time", lambda: clock.now)
    played: list[str] = []
    monkeypatch.setattr(cp_mod, "play_sound", lambda name="ding": played.append(name))
    return clock, played


def _check(p: _Panel, secs: int, state: str = "WARNING") -> None:
    ControlPanel._check_rift_ding(p, {"state": state, "secs_until": secs})


def _default_panel() -> _Panel:
    p = _Panel()
    p.s.rift_ding = ["15", "10", "5", "1"]
    return p


def test_disabled_timer_never_dings(_env):
    p = _default_panel()
    p.s.show_rift_timer = "Off"
    _check(p, 899)
    _check(p, 1)
    assert _env[1] == []


def test_full_window_rings_each_threshold_once(_env):
    """Continuous ticking across one warning: four chimes, one per lead time,
    each at its own moment — never two for the same threshold."""
    clock, played = _env
    p = _default_panel()
    # Tick every second from the 15m mark down to the final second.
    for secs in range(900, 0, -1):
        clock.tick_to(secs)
        _check(p, secs)
    # Exactly one per lead time (no duplicates), and the "Live" (0) is off.
    assert len(played) == 4
    assert played == ["ding"] * 4
    # Same hour, again: nothing re-rings.
    clock.tick_to(300)
    _check(p, 300)
    assert len(played) == 4


def test_next_hour_reams_and_rings_once_each(_env):
    """Each hourly window re-arms: the same thresholds ring again, once each."""
    clock, played = _env
    p = _default_panel()
    for secs in range(900, 0, -1):
        clock.tick_to(secs)
        _check(p, secs)
    assert len(played) == 4

    # Next hour: target moves forward one hour.
    clock.target += 3600
    before = len(played)
    for secs in range(900, 0, -1):
        clock.tick_to(secs)
        _check(p, secs)
    assert len(played) - before == 4


def test_duplicate_eval_in_same_second_never_doubles(_env):
    """A second refresh evaluating the same second (timer + nav sync) must not
    ring a threshold twice."""
    clock, played = _env
    p = _default_panel()
    clock.tick_to(899)
    _check(p, 899)          # 15m moment, rings once
    _check(p, 899)          # same second re-evaluated -> latched
    _check(p, 898)          # a second later -> still latched
    assert len(played) == 1


def test_open_mid_warning_does_not_replay_past_chimes(_env):
    """Opening with 2 minutes left must not replay the 15m/10m/5m chimes —
    only the still-pending 1m chime rings when it is due."""
    clock, played = _env
    p = _default_panel()
    clock.tick_to(120)
    _check(p, 120)
    assert played == []

    clock.tick_to(59)
    _check(p, 59)           # 1m chime rings now
    assert len(played) == 1
    clock.tick_to(58)
    _check(p, 58)           # latched, no replay
    assert len(played) == 1


def test_open_at_exact_threshold_boundary_fires_once(_env):
    """Opening exactly as the 5m threshold hits rings it once and only once."""
    clock, played = _env
    p = _default_panel()
    clock.tick_to(300)
    _check(p, 300)
    assert len(played) == 1     # the 5m chime (15m/10m latched as missed)
    clock.tick_to(299)
    _check(p, 299)
    assert len(played) == 1     # no duplicate on the next tick


def test_live_chime_rings_only_in_final_second(_env):
    """The special 0 / Live threshold rings exactly once, in the last second."""
    clock, played = _env
    p = _default_panel()
    p.s.rift_ding = [0]
    for secs in range(60, 1, -1):
        clock.tick_to(secs)
        _check(p, secs)
    assert played == []
    clock.tick_to(1)
    _check(p, 1)
    _check(p, 1)            # second eval in the same second
    assert len(played) == 1


def test_catch_up_after_gap_never_bursts(_env):
    """A long gap (sleep / busy UI) that skips several thresholds must not
    replay all of them at once on the next tick."""
    clock, played = _env
    p = _default_panel()
    clock.tick_to(900)
    _check(p, 900)          # 15m rings on time
    # Sleep past the 10m and 5m moments, waking with ~4 minutes left.
    clock.tick_to(240)
    _check(p, 240)          # catch-up: missed thresholds latched silently
    clock.tick_to(59)
    _check(p, 59)           # only the 1m chime is still pending
    assert len(played) == 2
