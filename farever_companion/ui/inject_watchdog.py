"""The controller half of the Option 2 inject watchdog.

``inject_retry.py`` owns *whether* to retry — a pure function of plain values.
This module owns the two things that decision needs off the live session, and
the one thing it does about them: re-run the injector when it says yes.

Split out of ``ui/game_attach.py`` for the same reason the policy was (that
file sits near the 840-line budget), and deliberately import-free: the
launcher is reached through ``self._launch_injector``, so the mixin knows
nothing about subprocesses, Qt or the game process, and a test can drive it
with nothing but fakes.

Import direction: ``ui/game_attach.py`` imports this module; nothing here
imports it back.
"""
from __future__ import annotations


class InjectWatchdogMixin:
    """Re-run Option 2's injector while a real fight stays deaf.

    The host supplies ``_stopped``, ``_located_shown``, ``proc``, ``model``,
    ``s`` (Settings), ``log`` (a Qt signal), ``_inject_retry``
    (:class:`~farever_companion.ui.inject_retry.InjectRetryState`) and
    ``_launch_injector()``.
    """

    def _capture_status(self) -> str:
        """The live capture status ("" when there is nothing to report)."""
        damage = getattr(self.model, "damage", None)
        if damage is None:
            return ""
        try:
            return damage.status()
        except Exception:
            return ""

    def _in_fight(self) -> bool:
        """True while the meter owns a boss or training-dummy encounter.

        Trash pulls and idle time legitimately decode nothing, so a timer
        alone would re-inject the DLL all evening; only inside a real
        encounter is the silence evidence of anything. The dummy case counts:
        it is the deliberate test bench for the meter.
        """
        dps = getattr(self.model, "dps", None)
        if dps is None:
            return False
        return bool(getattr(dps, "in_boss_fight", False)
                    or getattr(dps, "in_dummy_fight", False))

    def _tick_inject_retry(self) -> None:
        """5 s watchdog tick: re-run the injector if a fight is going deaf.

        The anomaly this exists for: Option 2 injected ONCE per session, on
        the located transition, so one miss (wrong binary on the candidate
        list, a locked or missing farever_dps.dll, a LoadLibraryW that failed
        while the world was still booting) left the meter at ``need_inject``
        with zero events for the entire run.

        The whole decision — attempt budget, spacing, the player-located gate
        it must NOT bypass, and "only a real encounter counts" — lives in
        ``inject_retry.retry_decision``; this only gathers the live state and
        acts on the verdict, so the policy stays drivable by a test with plain
        values. Every read is guarded: this tick fires while the model is
        being torn down on detach.
        """
        if self._stopped:
            return
        state = getattr(self, "_inject_retry", None)
        if state is None or self.proc is None or self.model is None:
            return
        mode = getattr(self.s, "dps_mode", "proxy")
        # Only Option 2 can be retried, so the other modes skip the status read
        # entirely ("capture mode is memory, not injector" is the better reason
        # anyway).
        try:
            status = self._capture_status() if mode == "injector" else ""
            state.observe(capture_status=status)        # refund when healthy
            retry, reason = state.decide(
                attached=True,
                located=bool(self._located_shown),
                mode=mode,
                hook_enabled=bool(getattr(self.s, "dps_hook_enabled", True)),
                capture_status=status,
                in_fight=self._in_fight(),
            )
        except Exception:
            return
        if not retry:
            return
        state.note_attempt()
        self.log.emit(f"Combat Bridge: {reason} — re-launching injector "
                      f"for PID {self.proc.pid}…")
        # forced: a run that TIMED OUT reports unknown-ok and so is NOT
        # discarded from the launcher's per-PID one-shot guard, which would
        # otherwise swallow this attempt and make the line above a lie. The
        # policy's spacing and budget are the guard against hammering.
        self._launch_injector(force=True)
