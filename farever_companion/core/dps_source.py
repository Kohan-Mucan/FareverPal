"""DamageSourceManager: the one model-owned source of combat events.

Owns the event ring every consumer drains, and routes capture to exactly one of
two deliberately separate engines:

* ``dps_bridge.py`` - Options 1 & 2: the in-game DLL, either dropped in as a
  proxy (``version.dll`` / ``dinput8.dll``) or injected (``farever_dps.dll``).
  Both options are the same native capture, so they share that one file.
* ``dps_memory.py`` - Option 3: the DLL-free memory scanner. Different capture
  mechanism entirely (offset-sensitive process reads).

The two engines share no code on purpose: a fix for one must never break the
other. This module only coordinates - it picks the engine, owns the ring, and
reports one combined status to the UI.

Where to fix what:
  DLL / proxy / injector / UDP protocol  ->  dps_bridge.py
  offsets / floaty decoding / calibration ->  dps_memory.py
  ring, mode routing, combined status     ->  here

Consumers drain one shared ring with their own cursor (``events_after``).
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Callable

from .damage_events import DamageEvent
from .dps_bridge import BridgeSource, parse_hook_event
from .dps_memory import MemorySource

log = logging.getLogger(__name__)

RING_LIMIT = 20000          # events kept for lagging consumers

CAPTURE_MODES = ("proxy", "injector", "memory")   # Option 1 | Option 2 | Option 3


class DpsEventStats:
    """Where DPS events are getting to, stage by stage.

    The capture status line answers "is the engine healthy"; this answers the
    question a dead meter cannot answer on its own: did anything ARRIVE, did
    anything DECODE, did the tracker PULL it, and did attribution keep it? Each
    stage with a zero and the one above it non-zero names the broken link —
    which is the whole diagnosis, because the three failures look identical on
    a board that reads zero.

    Monotonic for the life of the source (a process, not a fight): the question
    is about the PATH, and a per-fight counter would reset exactly when someone
    is trying to read it. Lock-guarded, because the bridge listener thread, the
    memory poller and the tracker's tick all write here.
    """

    __slots__ = ("_lock", "payload_bytes", "payload_packets", "payload_counted",
                 "decoded", "pulled", "attributed", "filtered", "dropped",
                 "dropped_unresolved")

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.payload_bytes = 0
        self.payload_packets = 0
        # False until an engine actually hands us a payload to measure: the
        # memory reader has no stream at all, so its payload counters stay empty
        # BY DESIGN and a report must say "n/a" rather than "0 bytes".
        self.payload_counted = False
        self.decoded = 0
        self.pulled = 0
        self.attributed = 0
        # Deliberately not recorded (the solo filter, the post-wipe hold, a
        # zero-amount kill ping) — a separate bucket so a healthy solo session
        # never reads as lossy.
        self.filtered = 0
        self.dropped = 0
        # The subset dropped because the caster/target could not be identified.
        # On a bridge this is the actionable one: it means the DLL is sending
        # perfectly good events and the reading of WHO did what is failing.
        self.dropped_unresolved = 0

    def note_payload(self, n_bytes: int) -> None:
        """One datagram's worth of raw payload off the wire."""
        with self._lock:
            self.payload_bytes += max(0, int(n_bytes))
            self.payload_packets += 1
            self.payload_counted = True

    def note_decoded(self, n: int) -> None:
        """`n` events the engine turned into real :class:`DamageEvent`s."""
        with self._lock:
            self.decoded += max(0, int(n))

    def note_drained(self, pulled: int, attributed: int, unresolved: int = 0,
                     filtered: int = 0) -> None:
        """One tracker drain pass: `pulled` events off the ring, `attributed` of
        them credited to a session. Drops are DERIVED here, never counted at each
        exit, so they cannot drift from the numbers above them."""
        with self._lock:
            self.pulled += max(0, int(pulled))
            self.attributed += max(0, int(attributed))
            self.filtered += max(0, int(filtered))
            self.dropped += (max(0, int(pulled))
                             - max(0, int(attributed))
                             - max(0, int(filtered)))
            self.dropped_unresolved += max(0, int(unresolved))

    def snapshot(self) -> dict:
        """A consistent copy of every counter (one lock, no half-updates)."""
        with self._lock:
            return {
                "payload_bytes": self.payload_bytes,
                "payload_packets": self.payload_packets,
                "payload_counted": self.payload_counted,
                "decoded": self.decoded,
                "pulled": self.pulled,
                "attributed": self.attributed,
                "filtered": self.filtered,
                "dropped": self.dropped,
                "dropped_unresolved": self.dropped_unresolved,
            }


class DamageSourceManager:
    def __init__(self, proc, type_hint: str | None = None,
                 result_type_hint: str | None = None):
        """Type hints skip the full-process locate on future launches."""
        self.proc = proc
        self._stop = False
        self._started = False

        self.mode: str = "proxy"   # Option 1 default

        # Event ring (cursor-drained) - the one thing all consumers share.
        self._ring: deque = deque(maxlen=RING_LIMIT)
        self._total = 0
        self._lock = threading.Lock()

        # Stage-by-stage event counters for the on-demand capture diagnostic
        # (see DpsEventStats). One object, read by the report, written by
        # whichever thread owns that stage.
        self.stats = DpsEventStats()

        # Stage timings shared by both engines.
        self._started_at: float = 0.0
        self._first_event_s: float = 0.0

        self._log_line: Callable[[str], None] | None = None

        # Local-hero addr for the memory reader's incoming flag, refreshed by
        # the tracker every tick (it owns player_addr); without it decoded hits
        # on you never mark incoming and the taken channel stays dark.
        self.my_hero_hint: int | None = None

        # True while a fight is live, refreshed by the tracker each tick so the
        # memory scanner's re-derive cadence stays in combat mode mid-fight
        # (event recency collapses the instant the reader goes deaf — exactly
        # when re-derivation is needed most).
        self._combat_hint_val: bool = False

        # True while the fight on the meter is one the log narrates — a
        # dungeon/rift run or a dummy test — refreshed by the tracker each tick
        # beside the combat hint. The bridge's periodic health roll-up is gated
        # on it (see BridgeSource._health_line_wanted), so open-world hits still
        # feed the meter without writing a line per 50 events forever.
        self._watched_hint_val: bool = False

        # --- the two capture engines (keep them apart!) --------------------
        # Strict isolation: the bridge (Options 1 & 2) and the scanner
        # (Option 3) share no callbacks. Exactly one is started at a time;
        # the other is stopped, so a leftover mapped DLL can never feed the
        # ring or hijack the status while memory owns the capture.
        self.bridge = BridgeSource(proc, emit=self._push)
        # The tracker's per-tick context (see note_watched) gates the bridge's
        # periodic health roll-up: narrated fights only, quiet open world.
        self.bridge.heartbeat_gate = self._watched_hint
        self._count_bridge_payload()
        self.memory = MemorySource(proc, emit=self._push,
                                   type_hint=type_hint,
                                   result_type_hint=result_type_hint,
                                   hero_hint=lambda: self.my_hero_hint,
                                   combat_hint=lambda: self._combat_hint(),
                                   active=lambda: self.mode == "memory")

    def _count_bridge_payload(self) -> None:
        """Measure the bridge's raw payload without touching the bridge.

        The bridge owns its socket, so the only place this coordinator can see
        a datagram's SIZE is the bridge's single ingest entry point. Counting it
        from out here is deliberate on both counts: the byte count is
        DIAGNOSTIC, so it must not be able to break a capture engine, and a
        future rewrite of that engine cannot silently lose the number — if the
        seam is ever renamed, the report degrades to "payload n/a" and the
        stages below it still work.
        """
        ingest = getattr(self.bridge, "_ingest", None)
        if not callable(ingest):
            return

        def _counted(text: str, now: float, _ingest=ingest) -> None:
            try:
                self.stats.note_payload(len(text))
            except Exception:
                pass
            _ingest(text, now)

        self.bridge._ingest = _counted

    def _combat_hint(self) -> bool:
        """The tracker's fight state, forwarded to the memory scanner so its
        calibration keeps combat cadence through a whole boss fight. The old
        event-recency window (6 s) collapsed the moment the reader went deaf
        mid-fight: no events -> "not hot" -> up to 300 s without a re-derive
        -> every remaining hit burst in when the fight ended (live
        2026-09-18: a 0.0 s 'fight' holding 81 hits)."""
        return self._combat_hint_val

    def note_combat(self, in_fight: bool) -> None:
        """Tracker hook: one line per tick (cheap bool write, no locks)."""
        self._combat_hint_val = bool(in_fight)

    def note_watched(self, in_watched: bool) -> None:
        """Tracker hook: is the live fight one the log narrates?

        True inside a dungeon/rift or at a training dummy (the tracker owns
        both reads); the bridge's periodic health roll-up only speaks then.
        Same one-bool-write-per-tick contract as `note_combat`, which this sits
        beside in the tracker's tick.
        """
        self._watched_hint_val = bool(in_watched)

    def _watched_hint(self) -> bool:
        return self._watched_hint_val

    # --- activity log -------------------------------------------------------
    @property
    def log_line(self) -> Callable[[str], None] | None:
        return self._log_line

    @log_line.setter
    def log_line(self, cb: Callable[[str], None] | None) -> None:
        """One sink for every engine's diagnostics (Activity Log)."""
        self._log_line = cb
        self.bridge.log_line = cb
        self.memory.log_line = cb

    def _emit_log(self, msg: str) -> None:
        log.info("[dps-source] %s", msg)
        cb = self._log_line
        if cb is not None:
            try:
                cb(msg)
            except Exception:
                pass

    # --- lifecycle ----------------------------------------------------------
    def ensure_started(self) -> None:
        """Start the selected capture engine (exactly one — never both)."""
        if self._started or self._stop:
            return
        self._started = True
        self._started_at = time.monotonic()
        self._emit_log(f"DPS Engine started (Mode: {self.mode.upper()})")
        if self.mode == "memory":
            self.memory.start()
        else:
            self.bridge.set_mode(self.mode)
            self.bridge.start()

    def shutdown(self) -> None:
        self._stop = True
        self.bridge.stop()
        self.memory.stop()

    def recalibrate(self) -> None:
        """Ask the memory scanner to re-map its cluster now (no-op for bridges)."""
        self.memory.recalibrate()

    def force_wide_rehunt(self, why: str = "") -> None:
        """Forward a forced whole-RW re-hunt to the memory scanner (Option 3).

        The scanner loop applies it within one poll pass; bridge modes have
        no scan cluster and simply ignore it.
        """
        fn = getattr(self.memory, "force_wide_rehunt", None)
        if callable(fn):
            fn(why)

    @property
    def started(self) -> bool:
        return self._started

    def set_mode(self, mode: str) -> None:
        """Switch capture: 'proxy' (Option 1) | 'injector' (Option 2) | 'memory' (Option 3).

        Exactly one engine stays live: the engine losing ownership is
        stopped before the winner starts, so a leftover mapped DLL stops
        feeding the ring the moment Option 3 takes over (and the scanner
        stops polling the moment a bridge option takes over).
        """
        mode = (mode or "proxy").strip().lower()
        if mode not in CAPTURE_MODES:
            mode = "proxy"
        old_mode = self.mode
        self.mode = mode
        if mode != "memory":
            self.bridge.set_mode(mode)
        log.info("[dps-source] Switched DPS capture mode to: %s", self.mode)
        if old_mode != mode:
            self._emit_log(f"DPS capture mode changed to: {self.mode.upper()}")
        if not self._started or self._stop:
            return  # ensure_started() will start the selected engine
        if mode == "memory":
            self.bridge.stop()
            self.memory.start()
        else:
            self.memory.stop()
            self.bridge.start()

    def set_type_hint(self, hint: str) -> None:
        self.memory.set_type_hint(hint)

    def set_result_type_hint(self, hint: str) -> None:
        self.memory.set_result_type_hint(hint)

    # --- event ring ---------------------------------------------------------
    def _push(self, events: list) -> None:
        """Sink both engines emit into; records the first-event timing."""
        if not events:
            return
        self.stats.note_decoded(len(events))
        with self._lock:
            for ev in events:
                self._ring.append(ev)
                self._total += 1
        if self._first_event_s <= 0.0 and self._started_at:
            self._first_event_s = time.monotonic() - self._started_at

    def events_after(self, cursor: int) -> tuple[int, list]:
        """``(new_cursor, events)`` for one consumer since ``cursor``."""
        with self._lock:
            base = self._total - len(self._ring)
            if cursor < base:
                cursor = base
            n = self._total - cursor
            if n <= 0:
                return self._total, []
            out = list(self._ring)[len(self._ring) - n:]
            return self._total, out

    # --- status for the UI --------------------------------------------------
    def status(self) -> str:
        """One of: live | bridge_ready | missing_proxy | need_inject |
        inject_failed | locating | mapping | silent | noscan | off.

        Strictly per selected engine: in memory mode a leftover mapped DLL
        never hijacks the status (it is stopped, and its stream is ignored
        even if the game still has it resident).
        """
        if not self._started:
            return "off"
        if self.mode == "memory":
            return self.memory.status()
        return self.bridge.status()

    def note_inject_result(self, ok: bool, reason: str = "") -> None:
        """Record one injector run's outcome (see ui.game_attach).

        Only meaningful for Option 2: a refusal is otherwise invisible,
        because the DLL genuinely is not loaded and the status reads exactly
        like an injection nobody has tried yet.
        """
        if self.mode == "memory":
            return
        self.bridge.inject_problem = "" if ok else (reason or "injector refused")

    @property
    def inject_problem(self) -> str:
        """Why the last injector run failed ('' when none / it worked)."""
        return self.bridge.inject_problem

    def counts(self) -> tuple[int, int]:
        """(events from the live capture, other-source event count)."""
        if self.mode == "memory":
            return self.memory.last_batch_n, 0
        return self.bridge.events_n, 0

    def live_source_name(self) -> str:
        """Which engine owns the capture right now (selected mode, not traffic)."""
        if self.mode == "memory":
            return "memory reader"
        return self.bridge.source_name()

    def diagnostics(self) -> dict:
        """Per-poll drop diagnostics from the memory scanner (read by the UI)."""
        return self.memory.diagnostics()

    def event_path_counts(self) -> dict:
        """Every stage counter in one dict, for the capture diagnostic.

        Ring occupancy rides along: events decoded but never drained is its own
        failure (a consumer that died mid-fight, or a drain that stopped), and
        it is the one number a caller cannot compute from the counters above.
        """
        counts = self.stats.snapshot()
        with self._lock:
            counts["in_ring"] = len(self._ring)
            counts["ring_total"] = self._total
        return counts

    def located_pointers(self) -> tuple[int | None, int | None]:
        """(DamageDisplay type ptr, DamageResult type ptr); (None, None) while locating."""
        return self.memory.located_pointers()

    def locate_progress(self) -> str:
        return self.memory.locate_progress()

    @property
    def damage_stale(self) -> bool:
        """Displays found but nothing decoded (stale offsets, not a quiet fight)."""
        return self.memory.damage_stale

    @property
    def has_decoded(self) -> bool:
        """True once at least one real event decoded this run."""
        return self._first_event_s > 0.0

    def bridge_issue(self) -> str:
        """Human reason the bridge is loaded but silent ('' when healthy)."""
        return self.bridge.bridge_issue()

    def bridge_unnamed_hits(self) -> tuple[int, bool]:
        """(hits sent with a placeholder name, whether the DLL reports it).

        The bridge resolves every unit name inside the game and, when both of
        its reads come back empty, sends the literal "Enemy" for the hit it
        could not name. That is not a silence problem - the capture works - so
        it is reported apart from :meth:`bridge_issue`: a build whose foe-name
        offsets stopped resolving looks healthy everywhere else, and the names
        it produces are the only thing that is wrong.

        The flag is what keeps an older DLL's silence apart from a reported
        zero; the event-path report must not read one as the other.
        """
        try:
            return (int(getattr(self.bridge, "unnamed_n", 0) or 0),
                    bool(getattr(self.bridge, "unnamed_reported", False)))
        except Exception:
            return (0, False)

    def timing_text(self) -> str:
        """Stage timings for the status line (only completed stages)."""
        m = self.memory
        parts: list[str] = []
        if m.calib_s > 0.0:
            parts.append(f"calib {m.calib_s:.1f}s")
        if m.mapping:
            parts.append(f"mapping {time.monotonic() - m.map_started:.0f}s…")
        elif m.map_s > 0.0:
            parts.append(f"map {m.map_s:.1f}s")
        if self._first_event_s > 0.0:
            parts.append(f"1st event {self._first_event_s:.1f}s")
        elif m.calib_s > 0.0 and self._started_at:
            el = time.monotonic() - self._started_at
            if el < 120.0:
                parts.append(f"no event yet ({el:.0f}s)")
        return " · ".join(parts)

    # --- compatibility surface ---------------------------------------------
    # Older call sites and headless tests reach into the engine internals by
    # name. Keep them working while the state itself lives in the split files.
    def _parse_hook_event(self, obj, now: float | None = None) -> DamageEvent | None:
        return parse_hook_event(obj, now)

    def _handle_bridge_status(self, obj: dict, now: float | None = None) -> None:
        self.bridge.handle_status(obj, now)

    def _ensure_reader(self):
        return self.memory.ensure_reader()

    @property
    def _reader(self):
        return self.memory.reader

    @_reader.setter
    def _reader(self, value) -> None:
        self.memory.reader = value

    @property
    def _locate_running(self) -> bool:
        return self.memory._locate_running

    @_locate_running.setter
    def _locate_running(self, value: bool) -> None:
        self.memory._locate_running = value

    @property
    def _locate_started(self) -> float:
        return self.memory._locate_started

    @_locate_started.setter
    def _locate_started(self, value: float) -> None:
        self.memory._locate_started = value

    @property
    def _locate_attempts(self) -> int:
        return self.memory._locate_attempts

    @_locate_attempts.setter
    def _locate_attempts(self, value: int) -> None:
        self.memory._locate_attempts = value

    @property
    def _calib_s(self) -> float:
        return self.memory.calib_s

    @_calib_s.setter
    def _calib_s(self, value: float) -> None:
        self.memory.calib_s = value

    @property
    def _map_s(self) -> float:
        return self.memory.map_s

    @_map_s.setter
    def _map_s(self, value: float) -> None:
        self.memory.map_s = value

    @property
    def _hook_events_n(self) -> int:
        return self.bridge.events_n

    @property
    def _hook_last_ts(self) -> float:
        return self.bridge.last_event_ts

    @property
    def _last_heartbeat_ts(self) -> float:
        return self.bridge.heartbeat_ts

