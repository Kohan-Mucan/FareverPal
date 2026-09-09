"""When to re-run the Option 2 injector, and when to give up.

Live 2026-09-29: Option 2 injected exactly ONCE per session, on the
player-located transition. A single miss - the wrong binary on the candidate
list, a game folder whose farever_dps.dll was locked, a LoadLibraryW that
failed while the world was still booting - then killed capture for the whole
run: the meter sat at ``need_inject`` with zero events through a full boss
fight, and the only clue was one line in the Activity Log.

This module is the retry POLICY, kept out of ``ui/game_attach.py`` (which is
near the 840-line budget) and free of Qt and of any process access, so the
whole decision is one pure function a test can drive with plain values.

Three rules, all deliberate:

* **The player-located gate still applies.** A retry is NOT a way around
  ``_located_shown``. Farever can have a process while its world and runtime
  table are still booting, and injecting then only makes the DLL wait for a
  table that is not ready. Nothing here may weaken that - the first attempt
  and every retry are equally downstream of a successful locate.
* **Only a real encounter counts.** Trash fights and idle time legitimately
  produce no events, so a timer alone would re-inject the DLL all evening.
  A boss (or training-dummy) fight with a live scene and not one decoded event
  is the actual anomaly worth retrying.
* **Bounded.** ``RETRY_MAX`` attempts, at most one per ``RETRY_MIN_INTERVAL_S``,
  and the budget is refunded as soon as the capture is healthy again, so a
  later regression gets a full allowance instead of an exhausted one.
"""
from __future__ import annotations

import time

# Attempts allowed per attach, on top of the initial one.
RETRY_MAX = 3
# Floor between two injector runs. The injector itself resolves a DLL beside
# its own exe and does a remote LoadLibrary; re-running it inside a couple of
# seconds of a failure is just the same failure louder.
RETRY_MIN_INTERVAL_S = 20.0
# How often the controller offers the decision to this policy.
RETRY_TICK_S = 5.0


def retry_decision(*, attached: bool, located: bool, mode: str,
                   hook_enabled: bool, capture_status: str, in_fight: bool,
                   attempts: int, last_attempt_ts: float,
                   now: float | None = None) -> tuple[bool, str]:
    """``(retry, reason)`` for one tick of the Option 2 watchdog.

    ``reason`` is a short human phrase for the Activity Log, and is returned
    even when the answer is no - that is what makes "why did it not retry"
    answerable without a debugger.
    """
    now = time.monotonic() if now is None else now
    if not attached:
        return False, "not attached"
    if not located:
        # The gate the user has to see: never inject before the player
        # resolves, no matter how sick the capture looks.
        return False, "player not located yet"
    if mode != "injector":
        return False, f"capture mode is {mode or 'unset'}, not injector"
    if not hook_enabled:
        return False, "Option 2 hook is disabled in settings"
    if capture_status not in ("need_inject", "inject_failed"):
        # bridge_ready / live / an off state - either the DLL is in and this
        # watchdog has nothing to do, or Option 3 owns the capture entirely.
        return False, f"capture is {capture_status or 'unknown'}"
    if not in_fight:
        return False, "no boss/dummy fight in progress"
    if attempts >= RETRY_MAX:
        return False, f"retry budget spent ({RETRY_MAX} attempts)"
    if last_attempt_ts and (now - last_attempt_ts) < RETRY_MIN_INTERVAL_S:
        return False, "too soon after the last attempt"
    return True, (f"boss fight live, capture is {capture_status}, "
                  f"0 events — retry {attempts + 1}/{RETRY_MAX}")


class InjectRetryState:
    """Per-attach attempt bookkeeping for the watchdog.

    Deliberately tiny and Qt-free: the controller owns the timer, this owns
    the counters, and ``reset()`` is what a detach calls.
    """

    def __init__(self) -> None:
        self.attempts = 0
        self.last_attempt_ts = 0.0
        self.refund_on_healthy = True

    def reset(self) -> None:
        self.attempts = 0
        self.last_attempt_ts = 0.0

    def note_attempt(self, now: float | None = None) -> None:
        """Record one injector run (the initial one counts against the budget)."""
        self.attempts += 1
        self.last_attempt_ts = time.monotonic() if now is None else now

    def observe(self, *, capture_status: str) -> None:
        """Refund the budget when the capture is healthy.

        Without this, one bad stretch in the first ten minutes would leave the
        watchdog spent for the rest of the session, and a genuine later
        regression would be met with silence.
        """
        if not self.refund_on_healthy:
            return
        if capture_status in ("bridge_ready", "live", "silent", "meter"):
            self.reset()

    def decide(self, **kw) -> tuple[bool, str]:
        """Delegate to :func:`retry_decision` with this state's counters."""
        return retry_decision(
            attempts=self.attempts,
            last_attempt_ts=self.last_attempt_ts,
            **kw,
        )
