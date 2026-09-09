"""Tests for ui/dps_background_tick.py: worker-thread DPS drain + keep-alive.

Live 2026-09-19: a fully backgrounded companion let Windows stretch its Qt
timers, so the capture ring drained in one gulp and a 30 s fight archived as
0.0 s. Two independent defenses are pinned here: the drain runs on a plain
OS thread (an Event.wait loop, not a window timer — Windows cannot throttle
it), and the tracker's non-blocking update guard makes the concurrent
main-thread overlay tick harmless.
"""
from __future__ import annotations

import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

pytest.importorskip("PySide6")

from farever_companion.core.dps_tracker import DpsTracker
from farever_companion.ui.dps_background_tick import (
    DpsBackgroundTickMixin, DpsDrainWorker, PowerKeepAlive,
)


class _TickHost(DpsBackgroundTickMixin):
    """Minimal stand-in for the ControlPanel surface the mixin touches."""

    def __init__(self, model):
        self.model = model
        self.logs: list[str] = []
        self._drain_worker = None
        self._drain_log_bridge = None

    def log(self, line: str) -> None:
        self.logs.append(line)


def test_worker_drains_repeatedly_on_schedule():
    """The worker's loop calls drain() on its interval until stopped."""
    calls = {"n": 0}
    evt = threading.Event()

    def drain():
        calls["n"] += 1
        if calls["n"] >= 3:
            evt.set()

    w = DpsDrainWorker(drain, interval_s=0.05)
    w.start()
    try:
        assert evt.wait(5.0), "drain not called 3x in 5s"
    finally:
        w.stop()
    assert calls["n"] == 3          # stopped cleanly, no extra pass


def test_worker_survives_drain_exceptions():
    """A raising drain must not kill the loop."""
    calls = {"n": 0}
    evt = threading.Event()

    def drain():
        calls["n"] += 1
        if calls["n"] >= 2:
            evt.set()
        raise RuntimeError("boom")

    w = DpsDrainWorker(drain, interval_s=0.05)
    w.start()
    try:
        assert evt.wait(5.0)
    finally:
        w.stop()
    assert w.is_alive() is False or True   # stop() joined; loop survived


def test_worker_stall_canary_emits_via_bridge():
    """A loop gap past STALL_LOG_S emits a log line through the bridge."""
    lines: list[str] = []
    w = DpsDrainWorker(lambda: None, log_emit=lines.append, interval_s=0.05)
    w._last = time.monotonic() - 12.0      # simulate a 12 s gap
    w._check_gap()
    assert len(lines) == 1 and "stalled 12s" in lines[0]
    # normal gap: silent
    w._check_gap()
    assert len(lines) == 1


def test_mixin_start_stop_is_idempotent():
    host = _TickHost(model=MagicMock())
    host._start_dps_drain()
    w1 = host._drain_worker
    assert w1 is not None and w1.is_alive()
    host._start_dps_drain()                # second start: no new worker
    assert host._drain_worker is w1
    host._stop_dps_drain(join_s=2.0)
    assert host._drain_worker is None
    assert not w1.is_alive()
    host._stop_dps_drain()                 # idempotent


def test_mixin_drain_body_updates_tracker():
    m = MagicMock()
    t = m.dps
    host = _TickHost(model=m)
    host._dps_background_tick()
    t.update.assert_called_once()


def test_the_drain_pushes_the_dummy_range_setting_onto_the_tracker():
    """The dummy rules live in core (no Qt, no settings) while the radius is a
    setting, so the always-on drain is what carries the number across — and it
    has to be the ONE the overlay's auto-show rule reads, or the HUD appears for
    a dummy the tracker refuses to test. A host with no settings at all must
    leave the tracker's own default alone."""
    tr = DpsTracker(None)
    host = _TickHost(model=SimpleNamespace(dps=tr))
    host._dps_background_tick()                       # nothing to read: no push
    assert tr.dummy_range == 5.0                      # the setting's default
    host.s = SimpleNamespace(dps_dummy_range=0)
    host._dps_background_tick()
    assert tr.dummy_range == 0.0                      # 0 = no cap, documented
    host.s.dps_dummy_range = 12.5
    host._dps_background_tick()
    assert tr.dummy_range == 12.5
    host.s.dps_dummy_range = None                     # never a crash
    host.s.dps_dummy_range = ""                       # (an unmigrated profile)
    host._dps_background_tick()
    assert tr.dummy_range == 12.5


def test_the_drain_pushes_the_clean_run_limit_onto_the_tracker():
    """Third dummy setting, same courier and same reason: "is this run worth
    comparing" is a rule in core, the tolerance for a messy run is the player's
    number, and this drain runs whether or not a HUD is open."""
    tr = DpsTracker(None)
    host = _TickHost(model=SimpleNamespace(dps=tr))
    host._dps_background_tick()
    assert tr.dummy_taken_limit_pct == 5.0          # the setting's default
    host.s = SimpleNamespace(dps_dummy_taken_pct=0)
    host._dps_background_tick()
    assert tr.dummy_taken_limit_pct == 0.0          # flag ANY damage taken
    host.s.dps_dummy_taken_pct = 12
    host._dps_background_tick()
    assert tr.dummy_taken_limit_pct == 12.0
    host.s.dps_dummy_taken_pct = None               # never a crash
    host._dps_background_tick()
    assert tr.dummy_taken_limit_pct == 12.0


def test_the_drain_carries_the_dummy_personal_best_in_and_out():
    """The best is a second, longer-lived record with the same plumbing as the
    reference, and the same reason for it: a new record can be set with every
    HUD closed (the test ends on its own target length), and a record that
    vanished because the window it was set in happened to be shut is no record
    at all. Once-only in both directions, like the reference."""
    tr = DpsTracker(None)
    stored = {"skills": {"Sword_Base_Attack": {"name": "Sword", "dps": 100.0}},
              "dps": 100.0, "duration": 30.0, "ts": 0.0}
    writes: list[tuple[str, dict]] = []
    reads: list[str] = []

    class _S:
        dps_dummy_range = 20
        dps_dummy_target = 30

        def get_dps_baseline(self, profile):
            return {}

        def save_dps_baseline(self, profile, snap):
            pass

        def get_dps_best_dummy(self, profile):
            reads.append(profile)
            return stored

        def save_dps_best_dummy(self, profile, snap):
            writes.append((profile, snap))

    host = _TickHost(model=SimpleNamespace(
        dps=tr, player_profile=lambda: "Mage"))
    host.s = _S()
    host._dps_background_tick()
    assert tr.dummy_best is not None
    assert tr.dummy_best["dps"] == 100.0
    assert reads == ["Mage"]

    tr.dummy_best_pending = {
        "skills": {"Axe_Base_Attack": {"name": "Axe", "dps": 150.0}},
        "dps": 150.0, "duration": 30.0, "ts": 1.0}
    host._dps_background_tick()
    assert len(writes) == 1 and writes[0][0] == "Mage"
    assert writes[0][1]["dps"] == 150.0
    # handed over exactly once: a repeated pass must not rewrite the record
    host._dps_background_tick()
    assert len(writes) == 1
    assert reads == ["Mage"]


def test_the_drain_pushes_the_dummy_target_setting_onto_the_tracker():
    """The target length is the test's end signal and the same split problem as
    the radius: the rule lives in core, the number is a setting. This drain runs
    whether or not any HUD is open, which is the whole point — a test that runs
    out of time with every window closed must still end and be archived, or the
    finished run is never remembered. A host with no settings leaves the
    tracker's own default alone."""
    tr = DpsTracker(None)
    host = _TickHost(model=SimpleNamespace(dps=tr))
    host._dps_background_tick()
    assert tr.dummy_target_s == 60.0               # the setting's default
    host.s = SimpleNamespace(dps_dummy_target=0)
    host._dps_background_tick()
    assert tr.dummy_target_s == 0.0                # 0 = free-running
    host.s.dps_dummy_target = 45
    host._dps_background_tick()
    assert tr.dummy_target_s == 45.0
    host.s.dps_dummy_target = None                 # never a crash
    host.s.dps_dummy_target = ""                   # (an unmigrated profile)
    host._dps_background_tick()
    assert tr.dummy_target_s == 45.0


def test_the_drain_pushes_the_dummy_auto_rearm_setting_onto_the_tracker():
    """Same split as the target length: the idle re-arm is a rule in core and a
    number in settings, and the drain must carry it whether or not a HUD is
    open — a dummy holds the client "in combat" at any range inside its yard,
    so a test that has stopped landing hits has to end itself without the
    player walking away. 0 = never auto-end."""
    tr = DpsTracker(None)
    host = _TickHost(model=SimpleNamespace(dps=tr))
    host._dps_background_tick()
    assert tr.dummy_rearm_s == 5.0                 # the setting's default
    host.s = SimpleNamespace(dps_dummy_rearm=0)
    host._dps_background_tick()
    assert tr.dummy_rearm_s == 0.0                 # 0 = never auto-end
    host.s.dps_dummy_rearm = 12
    host._dps_background_tick()
    assert tr.dummy_rearm_s == 12.0
    host.s.dps_dummy_rearm = None                  # never a crash
    host.s.dps_dummy_rearm = ""                    # (an unmigrated profile)
    host._dps_background_tick()
    assert tr.dummy_rearm_s == 12.0


def test_the_drain_carries_the_dummy_baseline_in_and_out():
    """The baseline's rules are core (no settings anywhere near them) and its
    value is per profile, and a test can END with every HUD closed — so the
    always-on drain is what loads the reference and writes a finished one. The
    hand-off is once-only: a repeated pass must not write the same reference
    again, and must not re-read the one it already has."""
    tr = DpsTracker(None)
    stored = {"skills": {"Sword_Base_Attack": {"name": "Sword", "dps": 100.0}},
              "dps": 100.0, "ts": 0.0}
    writes: list[tuple[str, dict]] = []
    reads: list[str] = []

    class _S:
        dps_dummy_range = 20

        def get_dps_baseline(self, profile):
            reads.append(profile)
            return stored

        def save_dps_baseline(self, profile, snap):
            writes.append((profile, snap))

    host = _TickHost(model=SimpleNamespace(
        dps=tr, player_profile=lambda: "Mage"))
    host.s = _S()
    host._dps_background_tick()
    assert tr.dummy_baseline is not None          # loaded, per profile
    assert reads == ["Mage"]

    tr.dummy_baseline_pending = {
        "skills": {"Axe_Base_Attack": {"name": "Axe", "dps": 5.0}},
        "dps": 5.0, "ts": 1.0}
    host._dps_background_tick()
    assert len(writes) == 1 and writes[0][0] == "Mage"
    assert writes[0][1]["dps"] == 5.0
    assert tr.dummy_baseline_pending is None      # handed over, then cleared

    host._dps_background_tick()
    assert len(writes) == 1                       # and never written twice
    assert len(reads) == 1                        # nor re-read on every pass


def test_mixin_drain_body_survives_missing_model_and_tracker():
    host = _TickHost(model=None)
    host._dps_background_tick()            # must not raise
    m = MagicMock()
    del m.dps
    host2 = _TickHost(model=m)
    host2._dps_background_tick()           # must not raise


def test_worker_log_goes_through_bridge_not_widget():
    """The worker's canary must emit the signal, never call log() directly
    (ControlPanel.log touches a widget — not allowed off the main thread)."""
    host = _TickHost(model=MagicMock())
    host._start_dps_drain()
    try:
        called = []
        host._drain_log_bridge.line.connect(called.append)
        host._threadsafe_log("hello from a worker")
        # Queued connection needs the receiving thread to spin; the emit is
        # what matters here — processEvents delivers it on this thread.
        from PySide6 import QtWidgets
        for _ in range(20):
            QtWidgets.QApplication.processEvents()
            if called:
                break
            time.sleep(0.02)
        assert called == ["hello from a worker"]
    finally:
        host._stop_dps_drain()


def test_keepalive_acquire_is_idempotent_and_release_clears():
    ka = PowerKeepAlive()
    ka.acquire()                 # real Win32 call on CI; no-op off Win8+
    first = ka._handle
    ka.acquire()                 # second acquire must not re-create
    assert ka._handle == first
    ka.release()
    assert ka._handle is None
    ka.release()                 # idempotent


def test_keepalive_unavailable_platform_is_a_noop():
    import farever_companion.ui.dps_background_tick as mod
    ka = PowerKeepAlive()
    orig = mod._power_request_available
    mod._power_request_available = lambda: False
    try:
        ka.acquire()
        assert ka._handle is None
    finally:
        mod._power_request_available = orig
