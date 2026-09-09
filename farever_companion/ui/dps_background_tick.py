"""Background DPS drain: the 1 Hz tick that keeps the meter honest.

Why this exists: the ONLY things that drain the capture event ring into the
tracker are the DPS overlay's timer (only while that overlay is open) and the
1 Hz tick here. Live 2026-09-19, a fully backgrounded/minimized window let
Windows stretch or suspend Qt timers, so a whole 30 s fight drained in one
28 ms gulp at the next interaction — archived as a 0.0 s fight.

Three parts:
* ``PowerKeepAlive`` — a Windows ``PowerRequestExecutionRequired`` request so
  the process is exempt from background timer throttling while held (Win32
  only, no-op elsewhere). Belt: the *prevent* half.
* ``DpsDrainWorker`` — the drain runs on a plain worker thread, NOT a Qt
  timer: Windows can stretch/suspend a window's timers, but it cannot
  throttle an ordinary OS thread. Suspenders: the delay structurally cannot
  happen. The thread measures its own loop gap and names a stall in the
  Activity Log if one ever appears (the *detect* half).
* ``DpsBackgroundTickMixin`` — the drain body + start/stop wiring, and a
  signal bridge so worker-thread log lines reach the Activity Log without
  touching a widget off the main thread.

Concurrency: ``DpsTracker.update()`` carries a non-blocking reentrancy guard,
so the worker pass and the overlay's main-thread tick can never interleave —
whichever arrives first owns the pass, the other skips it (nothing drains
twice; capture cursors also make a double-drain a no-op).
"""
from __future__ import annotations

import threading
import time

from PySide6 import QtCore
from PySide6.QtCore import Signal

# Win32: PowerRequestExecutionRequired stops Windows from throttling this
# process's timers when its window is minimized/backgrounded. Verified shape:
# since Win8, POWER_REQUEST_CONTEXT must be a POWER_REQUEST_CONTEXT structure
# (Version+Flags+Reason) — a plain LPCWSTR is rejected (ERROR_INVALID_PARAMETER).
_POWER_REQUEST_EXECUTION_REQUIRED = 0x40000000  # Win8+; silently unavailable pre-Win8
_POWER_REQUEST_CONTEXT_VERSION = 0
_POWER_REQUEST_CONTEXT_FLAGS_SIMPLE = 0x1
_POWER_REQUEST_CONTEXT_FLAGS_DETAILED = 0x2
_POWER_REQUEST_CONTEXT_INVALID = 0x3  # both flags set is invalid
_POWER_REQUEST_CONTEXT_MAX_SIMPLE = 0x40
_POWER_REQUEST_CONTEXT_MAX_DETAILED = 0xE0
_POWER_REQUEST_AVAILABLE = None  # resolved once


def _power_request_available() -> bool:
    """Whether PowerRequestExecutionRequired exists on this Windows."""
    global _POWER_REQUEST_AVAILABLE
    if _POWER_REQUEST_AVAILABLE is None:
        try:
            import ctypes
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            _POWER_REQUEST_AVAILABLE = hasattr(k32, "PowerCreateRequest")
        except Exception:
            _POWER_REQUEST_AVAILABLE = False
    return _POWER_REQUEST_AVAILABLE


class PowerKeepAlive:
    """Hold a PowerRequestExecutionRequired for the attach lifetime.

    Rationale: the DPS capture drains on Qt timers in this process. When the
    companion's window is backgrounded (game fullscreen), Windows throttles
    those timers to multi-second gaps — the whole event ring then drains in
    one gulp and a fight can archive as 0.0 s. The request tells Windows this
    process stays fully schedulable while it holds the game attached (and
    clears it on detach so the process can be suspended normally otherwise).
    """

    def __init__(self) -> None:
        self._handle = None
        self._failed_once = False

    def acquire(self) -> None:
        """Take the power request (idempotent; silent no-op off Windows 8+)."""
        if self._handle is not None or self._failed_once:
            return
        if not _power_request_available():
            self._failed_once = True
            return
        try:
            import ctypes
            from ctypes import wintypes

            k32 = ctypes.WinDLL("kernel32", use_last_error=True)

            class _POWER_REQUEST_CONTEXT(ctypes.Structure):
                _fields_ = [
                    ("Version", wintypes.DWORD),
                    ("Flags", wintypes.DWORD),
                    ("Reason", wintypes.LPVOID),
                ]

            ctx = _POWER_REQUEST_CONTEXT(
                Version=_POWER_REQUEST_CONTEXT_VERSION,
                Flags=_POWER_REQUEST_CONTEXT_FLAGS_SIMPLE,
                Reason=ctypes.cast(
                    ctypes.create_unicode_buffer(
                        "FareverPal: DPS capture drains on timers; game attached"),
                    wintypes.LPVOID),
            )
            handle = k32.PowerCreateRequest(ctypes.byref(ctx))
            if not handle or handle == -1:
                raise OSError(ctypes.get_last_error())
            if not k32.PowerSetRequest(
                    wintypes.HANDLE(handle),
                    _POWER_REQUEST_EXECUTION_REQUIRED):
                raise OSError(ctypes.get_last_error())
            self._handle = handle
        except Exception:
            self._handle = None
            self._failed_once = True

    def release(self) -> None:
        """Clear the request (idempotent)."""
        if self._handle is None:
            return
        try:
            import ctypes
            from ctypes import wintypes
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.PowerClearRequest(wintypes.HANDLE(self._handle),
                                  _POWER_REQUEST_EXECUTION_REQUIRED)
            k32.CloseHandle(wintypes.HANDLE(self._handle))
        except Exception:
            pass
        self._handle = None


class DpsDrainWorker(threading.Thread):
    """Plain OS thread: the event drain no window throttle can delay.

    A daemon thread on a 1 Hz ``Event.wait`` loop. ``Event.wait`` is not a
    window timer — Windows never stretches it for backgrounded windows, so
    the gulp-from-throttling failure mode is structurally gone. The canary
    remains as the detector: a loop gap >5 s means the thread itself was
    starved or a pass is stuck, which is a different (real) problem.
    """

    STALL_LOG_S = 5.0

    def __init__(self, drain, log_emit=None, interval_s: float = 1.0,
                 name: str = "dps-drain") -> None:
        super().__init__(daemon=True, name=name)
        self._drain = drain
        self._log_emit = log_emit
        self._interval = max(0.05, float(interval_s))
        self._stop_evt = threading.Event()
        self._last = time.monotonic()

    def run(self) -> None:
        while not self._stop_evt.wait(self._interval):
            self._check_gap()
            try:
                self._drain()
            except Exception:
                pass

    def _check_gap(self) -> None:
        """Canary: log when a loop pass gaps past STALL_LOG_S."""
        try:
            now = time.monotonic()
            gap = now - self._last
            self._last = now
            if gap > self.STALL_LOG_S and self._log_emit is not None:
                self._log_emit(
                    f"DPS drain stalled {gap:.0f}s (worker thread starved?) "
                    "— events drained late; timeline may compress")
        except Exception:
            pass

    def stop(self, join_s: float = 1.5) -> None:
        """Signal the loop to end and (best-effort) wait for it."""
        self._stop_evt.set()
        if self.is_alive() and threading.current_thread() is not self:
            self.join(timeout=join_s)


class _WorkerLogBridge(QtCore.QObject):
    """Queued signal bridge: worker thread -> Activity Log (main thread).

    ``ControlPanel.log`` touches a widget, so a worker thread must never call
    it directly; emitting a signal from a non-GUI thread is queued onto the
    GUI thread instead — the standard Qt pattern.
    """

    line = Signal(str)


class DpsBackgroundTickMixin:
    """Owns the drain worker; the drain body is shared by any host panel."""

    def _start_dps_drain(self) -> None:
        """Start the worker-thread drain (idempotent)."""
        if getattr(self, "_drain_worker", None) is not None:
            return
        # Parent the bridge to the host when it is a QObject (real panel =
        # lifetime-owned); a plain-object test host just leaks one QObject.
        parent = self if isinstance(self, QtCore.QObject) else None
        bridge = _WorkerLogBridge(parent)
        self._drain_log_bridge = bridge
        # The bridge's receiver is `log`, delivered on the GUI thread: that is
        # where the timestamp is stamped and the widget appended. Connecting it
        # is what makes the bridge useful — and what lets `log` itself be safe
        # to call from any thread (it routes a foreign thread through this
        # signal instead of touching the widget).
        sink = getattr(self, "log", None)
        if callable(sink):
            bridge.line.connect(sink)
        self._drain_worker = DpsDrainWorker(
            self._dps_background_tick, log_emit=self._threadsafe_log)
        self._drain_worker.start()

    def _stop_dps_drain(self, join_s: float = 1.5) -> None:
        """Stop the worker (idempotent; called on app quit)."""
        w = getattr(self, "_drain_worker", None)
        if w is not None:
            self._drain_worker = None
            w.stop(join_s=join_s)

    def _threadsafe_log(self, msg: str) -> None:
        """One Activity Log message from ANY thread (queued signal, widget-safe).

        The RAW message goes over the bridge; `log` receives it on the GUI
        thread, where the timestamp and the widget write belong. With no bridge
        up yet the message is dropped on purpose — falling back to a direct
        `self.log(msg)` would re-enter this method from a foreign thread and
        never reach the widget anyway (and a widget write off the GUI thread is
        the corruption this exists to prevent).
        """
        bridge = getattr(self, "_drain_log_bridge", None)
        if bridge is None:
            return
        try:
            bridge.line.emit(msg)
        except Exception:
            pass

    def _drain_profile(self) -> str:
        """The character profile this drain belongs to ('' when unknown)."""
        try:
            m = self.model
            return (m.player_profile() or "") if m is not None else ""
        except Exception:
            return ""

    def _sync_dummy_baseline(self, tracker) -> None:
        """Carry the remembered dummy test between the tracker and disk.

        Both directions, both here: the rules live in `core/dps_dummy.py` (no
        settings anywhere near them) and the value is per profile, so this drain
        is the courier — chosen because it runs whether or not a HUD is open, so
        a test that ends with every window closed still leaves a reference for
        the next one instead of being lost with the window.
        """
        s = getattr(self, "s", None)
        if s is None:
            return
        try:
            if getattr(tracker, "dummy_baseline", None) is None:
                tracker.set_dummy_baseline(
                    s.get_dps_baseline(self._drain_profile()))
            pending = tracker.take_dummy_baseline_pending()
            if pending:
                s.save_dps_baseline(self._drain_profile(), pending)
        except Exception:
            pass

    def _sync_dummy_best(self, tracker) -> None:
        """Carry the personal-best dummy test between the tracker and disk.

        Same courier and same reason as the reference, both directions again:
        a new record can be set with every HUD closed (the test ends on its own
        target length), and it would be a poor record that vanished because the
        window it was set in happened to be shut.
        """
        s = getattr(self, "s", None)
        if s is None:
            return
        try:
            if getattr(tracker, "dummy_best", None) is None:
                tracker.set_dummy_best(s.get_dps_best_dummy(self._drain_profile()))
            pending = tracker.take_dummy_best_pending()
            if pending:
                s.save_dps_best_dummy(self._drain_profile(), pending)
        except Exception:
            pass

    def _sync_dummy_taken_limit(self, tracker) -> None:
        """Push the "not a clean measurement" limit onto the tracker (1 Hz).

        Third dummy setting on the same courier, same reason: the flag is a
        rule in core (it decides what a run is worth comparing) while the
        number is the player's tolerance for a messy run.
        """
        pct = getattr(getattr(self, "s", None), "dps_dummy_taken_pct", None)
        if pct is None:
            return
        try:
            tracker.dummy_taken_limit_pct = max(0.0, float(pct))
        except (TypeError, ValueError):
            pass

    def _sync_dummy_range(self, tracker) -> None:
        """Push the Dummy Range setting onto the tracker (1 Hz).

        The dummy rules live in `core/` (no Qt, no settings) while the radius is
        a setting, so the number has to be handed over — and this drain is the
        right courier because it runs whether or not any DPS surface is open.
        It must match the overlay manager's auto-show rule exactly: the HUD
        would otherwise appear for a dummy the tracker refuses to test.
        """
        rng = getattr(getattr(self, "s", None), "dps_dummy_range", None)
        if rng is None:
            return
        try:
            tracker.dummy_range = float(rng)
        except (TypeError, ValueError):
            pass

    def _sync_dummy_free(self, tracker) -> None:
        """Push the Free Test setting onto the tracker (1 Hz).

        Same courier as every other dummy preference: the rule lives in
        `core/` and the toggle is a user setting. It has to arrive whether or
        not a DPS surface is open, because a free run is a run you took
        deliberately - a stale value from before the toggle would record it.
        """
        free = getattr(getattr(self, "s", None), "dps_dummy_free", None)
        if free is None:
            return
        tracker.dummy_free = bool(free)

    def _sync_boss_clear_range(self, tracker) -> None:
        """Push the Boss Clear Range setting onto the tracker (1 Hz).

        Same courier and same reason as the dummy radius: the rule that closes
        the trash run when the player walks into the boss's area lives in
        `core/` (no Qt, no settings) while the number is a user preference, and
        it has to arrive whether or not any DPS surface is open — otherwise a
        closed app leaves a stale radius on the tracker.
        """
        rng = getattr(getattr(self, "s", None), "dps_boss_clear_range", None)
        if rng is None:
            return
        try:
            tracker.boss_clear_range = max(0.0, float(rng))
        except (TypeError, ValueError):
            pass

    def _sync_dummy_target(self, tracker) -> None:
        """Push the dummy test's target length onto the tracker (1 Hz).

        Same courier and same reason as the radius: the end-signal rules live
        in `core/` (no Qt, no settings) while the length is a user preference.
        This drain is the right place because it runs whether or not any DPS
        surface is open — a test that runs out of time must end and be archived
        on a timer the player is not looking at, or the finished run is never
        remembered and the next comparison has nothing to measure against.
        """
        tgt = getattr(getattr(self, "s", None), "dps_dummy_target", None)
        if tgt is None:
            return
        try:
            tracker.dummy_target_s = max(0.0, float(tgt))
        except (TypeError, ValueError):
            pass

    def _sync_dummy_rearm(self, tracker) -> None:
        """Push the Dummy Auto Re-arm setting onto the tracker (1 Hz).

        Same courier and same reason as the radius and the target length: the
        end rules live in `core/` (no Qt, no settings) while the number is a
        user preference. A dummy keeps the game "in combat" at any range inside
        its yard, so this end has to be reachable without the player walking
        ~30 m out — and it must run whether or not any DPS surface is open.
        """
        secs = getattr(getattr(self, "s", None), "dps_dummy_rearm", None)
        if secs is None:
            return
        try:
            tracker.dummy_rearm_s = max(0.0, float(secs))
        except (TypeError, ValueError):
            pass

    def _dps_background_tick(self) -> None:
        """Drain the DPS event ring even with all DPS surfaces hidden.

        Runs on the DpsDrainWorker thread (never throttled); the tracker's
        update guard makes concurrent calls from the overlay's main-thread
        tick harmless — one pass, one owner.
        """
        try:
            m = self.model
            t = getattr(m, "dps", None) if m is not None else None
            if t is not None:
                self._sync_dummy_range(t)
                self._sync_boss_clear_range(t)
                self._sync_dummy_free(t)
                self._sync_dummy_target(t)
                self._sync_dummy_rearm(t)
                self._sync_dummy_taken_limit(t)
                self._sync_dummy_baseline(t)
                self._sync_dummy_best(t)
                t.update()
        except Exception:
            pass
