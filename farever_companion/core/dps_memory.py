"""Memory-only capture: Option 3.

Reads the game's damage floaty numbers straight out of the process. No DLL, no
injection, nothing written into the game folder — it polls the ``DamageDisplay``
objects and decodes them, which means it is both slower and offset-sensitive.

That is exactly why it lives apart from the combat bridge in ``dps_bridge.py``:
a game update that moves a field offset must only ever touch *this* file. The
bridge (Options 1 & 2) shares none of this code.

Flow: locate the ``DamageDisplay`` / ``DamageResult`` types, map a range cluster
of live instances, then poll the cluster. ``poll()`` cannot decode a single
event until both of those are done, so this module drives the calibration itself
on the ``DERIVE_*`` cadences — full-process scans stay throttled, in-fight
re-maps follow pool page rotation.

This module stores no events: decoded events go to the ``emit`` callback the
coordinator passed in, and the coordinator owns the ring.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from .damage import FORCE_WIDE_TTL_S, DamageReader
from .hl import Hl
from .proc import ProcError

log = logging.getLogger(__name__)

# Poll cadence.
POLL_IN_COMBAT = 0.08       # s between polls while events flow
POLL_IDLE = 0.5             # s between polls in a lull

# Calibration cadences (how often the reader is allowed to re-derive).
DERIVE_LULL_S = 45          # re-derive cadence when nothing anchors
DERIVE_HEALTHY_S = 300      # re-derive cadence while events decode
DERIVE_STALE_S = 8          # re-derive cadence while displays sit undecoded
DERIVE_FIRST_S = 4          # probe cadence before the first event ever
DERIVE_SILENCE_S = 0.3      # silent this long -> map ran out of pages
DERIVE_HOT_S = 1.0          # in-fight re-map cadence (fresh pool page per hit)
DERIVE_HOT_WINDOW_S = 6.0   # still "in combat" this long after last event

# An idle-but-mapped reader reports "silent" instead of "live".
SILENT_AFTER_S = 5.0

START_SETTLE_S = 0.5        # let the app finish attaching before the first read


class MemorySource:
    """Option 3: the in-process damage-number scanner.

    Strictly DLL-free: this engine never imports, probes, or consults the
    combat bridge. ``emit`` is the coordinator's batch sink. ``hero_hint``
    returns the local hero address (owned by the tracker) so decoded hits on
    you flag as incoming; ``combat_hint`` returns True while the tracker
    considers a fight live (boss or dummy engaged), so re-derivation keeps
    its combat cadence even when the reader has gone deaf mid-fight — see
    _calibrate. ``active`` returns True while Option 3 is the selected
    capture (a coordinator-owned enabled flag, not DLL state — the loop
    parks itself otherwise, so switching to a bridge option never leaves
    two engines live).

    Exactly-one-engine-live is enforced by the coordinator
    (``dps_source.py`` starts/stops engines on mode switch); this loop
    never yields to a DLL stream.
    """

    def __init__(self, proc, emit: Callable[[list], None],
                 log_line: Callable[[str], None] | None = None,
                 type_hint: str | None = None,
                 result_type_hint: str | None = None,
                 hero_hint: Callable[[], int | None] | None = None,
                 combat_hint: Callable[[], bool] | None = None,
                 active: Callable[[], bool] | None = None,
                 **_legacy):
        self.proc = proc
        self.log_line = log_line
        self._emit = emit
        self._hero_hint = hero_hint
        self._combat_hint = combat_hint
        self._active = active

        self._stop = False
        self._started = False
        self._thread: threading.Thread | None = None
        self._redrive = False
        self._force_wide = ""   # pending forced re-hunt reason ("" = none)
        # Blind-window observability: monotonic timestamp + reason of the
        # earliest pending forced re-hunt request. When the wide sweep finally
        # runs, the elapsed time is the latency the user experienced as a
        # blank meter — logged once as `pool re-anchored in Xs (why)`.
        self._rehunt_requested_at = 0.0
        self._rehunt_why = ""

        self.reader: DamageReader | None = None
        self._type_hint = type_hint
        self._result_type_hint = result_type_hint

        # locate progress for the UI status line
        self._locate_attempts = 0
        self._locate_running = False
        self._locate_started = 0.0
        self._damage_locate_done = False

        # stage timings
        self._started_at = 0.0
        self.calib_s = 0.0        # both type locates done
        self.map_s = 0.0          # first successful map duration
        self.map_started = 0.0    # monotonic while a map is in flight

        # last poll
        self._last_batch_n = 0
        self._last_event_ts = 0.0

    # --- diagnostics --------------------------------------------------------
    def _emit_log(self, msg: str) -> None:
        log.info("[dps-memory] %s", msg)
        cb = self.log_line
        if cb is not None:
            try:
                cb(msg)
            except Exception:
                pass

    # --- lifecycle ----------------------------------------------------------
    def start(self) -> None:
        # Restartable on purpose: the coordinator keeps exactly one engine
        # live, so switching capture modes stops this poller and starts it
        # again later. A live thread means already started.
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop = False
        self._started = True
        self._started_at = time.monotonic()
        self._emit_log("Memory scanner started (Option 3)")
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="dps-memory-poller")
        self._thread.start()

    def stop(self) -> None:
        self._stop = True
        self._redrive = True
        t = self._thread
        if t is not None:
            try:
                t.join(timeout=2.0)
            except Exception:
                pass
        self._thread = None

    def recalibrate(self) -> None:
        """Re-map the range cluster on the next loop pass."""
        self._redrive = True

    def force_wide_rehunt(self, why: str = "") -> None:
        """Arm a blind rescue on the reader, bypassing the wide-sweep gate.
        Applied by the loop within one poll pass so a zone change re-anchors
        the cluster immediately instead of after the gate.

        The rescue keeps retrying while its sweeps find only objects the
        reader already holds (a zone-in sweep has no pool to find yet), and
        the request-to-anchor latency is logged to the Activity Log once a
        sweep actually re-anchors (`pool re-anchored in Xs (why)`) or the
        retries give up (`pool re-anchor FAILED after Xs (why)`), so a future
        blind window is visible instead of silent.
        """
        self._force_wide = why or "forced"
        if self._rehunt_requested_at <= 0.0:
            self._rehunt_requested_at = time.monotonic()
        self._rehunt_why = self._force_wide

    @property
    def started(self) -> bool:
        return self._started

    def set_type_hint(self, hint: str) -> None:
        """Seed the DamageDisplay type pointer (ignored once live)."""
        hint = (hint or "").strip()
        if hint:
            self._type_hint = hint
            dr = self.reader
            if dr is not None and dr._type_ptr is None:
                dr._type_hint = hint

    def set_result_type_hint(self, hint: str) -> None:
        """Seed the DamageResult type pointer (ignored once live)."""
        hint = (hint or "").strip()
        if hint:
            self._result_type_hint = hint
            dr = self.reader
            if dr is not None and dr._result_type_ptr is None:
                dr._result_type_hint = hint

    # --- reader -------------------------------------------------------------
    def ensure_reader(self) -> DamageReader:
        if self.reader is None:
            # Dedicated Hl: the bg poller's caches must not race the UI thread's.
            self.reader = DamageReader(self.proc, Hl(self.proc),
                                       type_hint=self._type_hint,
                                       result_type_hint=self._result_type_hint)
            # Cold Option 3 startup never launches the whole-process type
            # sweep. It becomes eligible when the poller reports a live fight.
            self.reader._allow_wide_type_scan = False
            self.reader._allow_wide_memory_scan = True
            self.reader._incremental_wide_scan = True
        return self.reader

    def located_pointers(self) -> tuple[int | None, int | None]:
        """(DamageDisplay type ptr, DamageResult type ptr); (None, None) until located."""
        dr = self.reader
        if dr is None:
            return None, None
        tp = getattr(dr, "type_ptr", None)
        if tp is None:
            tp = getattr(dr, "_type_ptr", None)
        return tp, getattr(dr, "_result_type_ptr", None)

    def diagnostics(self) -> dict:
        """Per-poll drop diagnostics from the reader (read by the UI)."""
        dr = self.reader
        if dr is not None:
            return dict(dr._diag)
        return {}

    # --- status -------------------------------------------------------------
    def status(self) -> str:
        """One of: noscan | off | locating | mapping | live | silent."""
        if not getattr(self.proc, "has_scan", True):
            return "noscan"
        if not self._started:
            return "off"
        dr = self.reader
        if dr is None or getattr(dr, "_type_ptr", None) is None:
            return "locating"
        if not getattr(dr, "_ranges_ready", False):
            return "mapping"
        if self._last_event_ts > 0.0 and \
                (time.monotonic() - self._last_event_ts) < SILENT_AFTER_S:
            return "live"
        return "silent"

    def locate_progress(self) -> str:
        """Type-locate status for the status line ("" while not locating)."""
        if not self._locate_running:
            return ""
        el = time.monotonic() - self._locate_started
        return f"scan {self._locate_attempts + 1} · {el:.0f}s"

    @property
    def damage_stale(self) -> bool:
        """Displays found but nothing decoded (stale offsets, not a quiet fight)."""
        d = self.diagnostics()
        return bool(d.get("displays") and not d.get("ok"))

    def had_events(self, window_s: float = SILENT_AFTER_S) -> bool:
        return self._last_event_ts > 0.0 and \
            (time.monotonic() - self._last_event_ts) < window_s

    @property
    def last_batch_n(self) -> int:
        """Displays decoded on the most recent poll."""
        return self._last_batch_n

    @property
    def mapping(self) -> bool:
        """True while the first range map is in flight."""
        return self.map_started > 0.0 and self.map_s <= 0.0

    # --- background loop ----------------------------------------------------
    def _refresh_timed(self, dr) -> int:
        """refresh_ranges() with the first-map duration recorded.

        Also closes the forced-re-hunt blind-window measurement: the reader's
        `_last_full_derive` advancing means the whole-RW sweep actually ran
        this pass, so the pending request's age is the re-anchor latency.
        """
        pre_derive = getattr(dr, "_last_full_derive", 0.0)
        self.map_started = time.monotonic()
        try:
            n = dr.refresh_ranges()
        finally:
            if self.map_s <= 0.0 and getattr(dr, "_ranges_ready", False):
                self.map_s = time.monotonic() - self.map_started
            self.map_started = 0.0
            post_derive = getattr(dr, "_last_full_derive", 0.0)
            if self._rehunt_requested_at > 0.0:
                now_m = time.monotonic()
                found = getattr(dr, "_last_sweep_hits", -1)
                armed_now = getattr(dr, "_force_wide_until", 0.0) > now_m
                age = now_m - self._rehunt_requested_at
                why = self._rehunt_why or "forced"
                # Only a sweep that FOUND displays is a re-anchor: a zone-in
                # sweep comes up empty whenever the new zone's pool does not
                # exist yet (a floaty is only created once something is hit),
                # and the rescue now retries -- claiming success on an empty
                # retry would report a recovery that never happened.
                if post_derive > pre_derive and found > 0:
                    self._emit_log(f"pool re-anchored in {age:.1f}s ({why})")
                    self._rehunt_requested_at = 0.0
                    self._rehunt_why = ""
                elif not armed_now and age > FORCE_WIDE_TTL_S:
                    # The rescue gave up (retry window closed) without ever
                    # anchoring: surface the blind window instead of the
                    # silence that made the 2026-09-19 dungeon pull invisible.
                    self._emit_log(f"pool re-anchor FAILED after {age:.1f}s "
                                   f"({why}) -- no displays found")
                    self._rehunt_requested_at = 0.0
                    self._rehunt_why = ""
        return n

    def _calibrate(self, dr, last_derive: float, now: float) -> float:
        """Drive type-locate + range-map on the DERIVE_* cadences; new last_derive."""
        try:
            _ddiag = dr._diag if isinstance(dr._diag, dict) else {}
        except Exception:
            _ddiag = {}
        stale = bool(_ddiag.get("displays") and not _ddiag.get("ok"))
        hot = ((now - self._last_event_ts) < DERIVE_HOT_WINDOW_S
               or (self._combat_hint is not None and self._combat_hint()))
        tp = dr._type_ptr
        if self._redrive:
            self._redrive = False
            last_derive = 0.0
        if tp is None or dr._result_type_ptr is None:
            if not self._locate_running:
                self._locate_running = True
                self._locate_started = now
            first = not self._damage_locate_done
            cadence = DERIVE_FIRST_S if first else (DERIVE_HOT_S if hot else DERIVE_LULL_S)
            if now - last_derive >= cadence:
                last_derive = now
                self._locate_attempts += 1
                if tp is None:
                    dr.find_type()
                    tp = dr._type_ptr
                if tp is not None and dr._result_type_ptr is None:
                    dr.find_result_type()
                if tp is not None and dr._heal_type_ptr is None:
                    dr.find_heal_type()
                if dr._type_ptr is not None and dr._result_type_ptr is not None:
                    self._locate_running = False
                    self._damage_locate_done = True
                    if self.calib_s <= 0.0 and self._started_at:
                        self.calib_s = now - self._started_at
                    # Map on the next tick: otherwise the fresh locate waits out
                    # the quiet LULL cadence (up to 45 s) before the first range
                    # map, which reads as "damage takes ages to show" cold.
                    last_derive = 0.0
        elif not dr._ranges_ready:
            self._locate_running = False
            cadence = DERIVE_HOT_S if hot else (DERIVE_STALE_S if stale else DERIVE_LULL_S)
            if now - last_derive >= cadence:
                last_derive = now
                self._refresh_timed(dr)
        else:
            self._locate_running = False
            cadence = DERIVE_HOT_S if hot else DERIVE_HEALTHY_S
            if now - last_derive >= cadence:
                last_derive = now
                self._refresh_timed(dr)
        return last_derive

    def _apply_forced_rehunt(self, dr) -> None:
        """Consume a pending forced re-hunt onto the reader (loop step)."""
        if not self._force_wide:
            return
        why = self._force_wide
        self._force_wide = ""
        try:
            dr.force_wide_rehunt(why)
        except Exception:
            pass

    def _rehunt_now_if_armed(self, dr) -> None:
        """An armed re-hunt must not wait out the derive scheduler.

        The reader's arm only bypasses the sweep's INTERNAL rate gate; the
        scheduler in ``_calibrate`` decides when a sweep runs at all, and a
        reader whose ranges still look ready sits on the HEALTHY cadence —
        up to 300 s when the combat hint is off (trash pulls before boss
        engage). Live 2026-09-19: zone-in re-anchored only when some cadence
        happened to fire, so outgoing damage lagged 10 s+ into a pull while
        taken estimates (which need no reader) still showed — exactly the
        'dmg taken fast, dmg/skills slow' report. When the arm is live and
        the reader has its types located, sweep in THIS pass.

        A successful sweep consumes the arm, so this cannot loop. A sweep that
        finds nothing leaves the rescue armed, and `rescue_sweep_due()` is
        what keeps that from re-sweeping every pass: it stays false until the
        backed-off retry interval has elapsed.
        """
        try:
            due = getattr(dr, "rescue_sweep_due", None)
            armed = (bool(due()) if callable(due)
                     else getattr(dr, "_force_wide_until", 0.0) > time.monotonic())
        except Exception:
            return
        if armed and getattr(dr, "_type_ptr", None) is not None:
            try:
                self._refresh_timed(dr)
            except Exception:
                pass

    def _loop(self) -> None:
        """Poll the damage-number cluster while Option 3 owns the capture.

        DLL-free by construction: a leftover mapped DLL is the bridge's
        problem (it is stopped while this loop runs), so this loop never
        checks for, or yields to, a DLL stream.
        """
        time.sleep(START_SETTLE_S)
        dr = None
        last_derive = 0.0
        while not self._stop:
            # Only one capture may be live: park unless Option 3 is selected.
            if self._active is not None:
                try:
                    if not self._active():
                        time.sleep(1.0)
                        continue
                except Exception:
                    pass

            if dr is None:
                try:
                    dr = self.ensure_reader()
                except Exception as e:
                    log.debug("[dps-memory] Failed to initialize DamageReader: %s", e)
                    time.sleep(1.0)
                    continue

            # Pending forced re-hunt (zone change): arm the reader's one-shot
            # wide sweep so the very next derive re-anchors past the rate gate.
            self._apply_forced_rehunt(dr)

            # An armed re-hunt must not wait out the 8-45 s derive scheduler
            # (live 2026-09-19: zone-in + a maintenance derive minutes earlier
            # = the whole first pull's outgoing damage invisible while taken
            # estimates, which need no reader, still showed). Fire it NOW.
            self._rehunt_now_if_armed(dr)

            # Local-hero addr (tracker-owned) so decoded hits on you flag incoming.
            if self._hero_hint is not None:
                try:
                    dr.my_hero = self._hero_hint()
                except Exception:
                    pass

            # The tracker's fight state, so a COLD reader mid-fight rescues on
            # the short blind cadence instead of waiting out the 120 s
            # maintenance gate (same `note_combat` signal that already keeps
            # the derive cadence alive mid-fight).
            if self._combat_hint is not None:
                try:
                    dr.in_combat = bool(self._combat_hint())
                except Exception:
                    pass

            try:
                last_derive = self._calibrate(dr, last_derive, time.monotonic())
            except (ProcError, OSError):
                pass
            except Exception as e:
                log.debug("[dps-memory] Derive exception: %s", e)

            try:
                evs = dr.poll()
            except (ProcError, OSError):
                evs = []
            except Exception as e:
                log.debug("[dps-memory] Poll exception: %s", e)
                evs = []

            if evs:
                now_ev = time.monotonic()
                self._last_event_ts = now_ev
                self._last_batch_n = len(evs)
                self._emit(evs)

            in_combat = (time.monotonic() - self._last_event_ts) < 3.0 \
                if self._last_event_ts else False
            time.sleep(POLL_IN_COMBAT if in_combat else POLL_IDLE)
