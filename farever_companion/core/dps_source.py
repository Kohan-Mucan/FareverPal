"""DamageSourceManager: one model-owned background source of combat events.

Arbitrates the floaty reader and the HUD meter (meter-takeover exclusivity:
meter wins once live so events are never double-counted). Consumers drain one
shared ring with their own cursor.
"""
from __future__ import annotations

import json
import logging
import socket
import threading
import time
from collections import deque
from typing import Callable

from .damage import DamageReader
from .damage_events import DamageEvent, K_DAMAGE, K_HEAL, K_SHIELD
from .hl import Hl
from .proc import Proc, ProcError

log = logging.getLogger(__name__)

RING_LIMIT = 20000          # events kept for lagging consumers
POLL_IN_COMBAT = 0.08       # s between polls while events flow
POLL_IDLE = 0.5             # s between polls in a lull
DERIVE_LULL_S = 45          # re-derive cadence when nothing anchors
DERIVE_HEALTHY_S = 300      # re-derive cadence while events decode
DERIVE_STALE_S = 8          # re-derive cadence while displays sit undecoded
DERIVE_FIRST_S = 4          # probe cadence before the first event ever
DERIVE_SILENCE_S = 0.3      # silent this long -> map ran out of pages
DERIVE_HOT_S = 1.0          # in-fight re-map cadence (fresh pool page per hit)
DERIVE_HOT_WINDOW_S = 6.0   # still "in combat" this long after last event


class DamageSourceManager:
    def __init__(self, proc: Proc, type_hint: str | None = None,
                 result_type_hint: str | None = None):
        """Type hints skip the full-process locate on future launches."""
        self.proc = proc
        self._type_hint = type_hint
        self._result_type_hint = result_type_hint
        self._stop = False
        self._started = False
        self._thread: threading.Thread | None = None
        self._redrive = threading.Event()

        self.mode: str = "proxy"  # "proxy" (Option 1) | "injector" (Option 2) | "memory" (Option 3)
        self._reader: DamageReader | None = None
        self._damage_locate_done = False
        self._damage_found = False
        self._result_locate_done = False
        self._result_found = False

        # locate progress for the UI status line.
        self._locate_attempts = 0
        self._locate_running = False
        self._locate_started = 0.0

        # live counters (bg thread writes, UI reads)
        self._last_damage_ts: float = 0.0
        self._last_damage_n: int = 0

        # live hook counters (UDP/pipe listener writes, UI reads)
        self._hook_thread: threading.Thread | None = None
        self._hook_last_ts: float = 0.0
        self._last_heartbeat_ts: float = 0.0
        self._hook_events_n: int = 0
        self._hook_check_ts: float = 0.0
        self._hook_is_loaded: bool = False
        # Hook-install status telemetry from the DLL (ts, stage, ok, msg);
        # a loaded-but-silent bridge can explain itself from these.
        self._bridge_status_log: deque = deque(maxlen=24)

        # stage timings for the status line.
        self._started_at: float = 0.0
        self._calib_s: float = 0.0          # both type locates done
        self._map_s: float = 0.0            # first successful map duration
        self._first_event_s: float = 0.0    # first decoded event delay
        self._map_started: float = 0.0      # monotonic while a map is in flight

        # event ring (cursor-drained)
        self._ring: deque = deque(maxlen=RING_LIMIT)
        self._total = 0
        self._lock = threading.Lock()

        # Activity log callback and event flags
        self.log_line: Callable[[str], None] | None = None
        self._first_packet_logged: bool = False
        self._first_event_logged: bool = False

    def _emit_log(self, msg: str) -> None:
        """Forward diagnostic messages to Python logger and Activity Log."""
        log.info("[dps-bridge] %s", msg)
        if self.log_line is not None:
            try:
                self.log_line(msg)
            except Exception:
                pass

    # --- lifecycle --------------------------------------------------------
    def ensure_started(self) -> None:
        """Start both the hook IPC listener and fallback memory reader."""
        if self._started or self._stop:
            return
        self._started = True
        self._started_at = time.monotonic()
        self._emit_log(f"DPS Engine started (Mode: {self.mode.upper()})")
        self._hook_thread = threading.Thread(target=self._ipc_loop, daemon=True,
                                             name="dps-hook-ipc")
        self._hook_thread.start()
        if self.mode == "memory":
            self._thread = threading.Thread(target=self._loop, daemon=True,
                                            name="dps-memory-poller")
            self._thread.start()

    def shutdown(self) -> None:
        self._stop = True
        self._redrive.set()
        if self._thread is not None:
            try:
                self._thread.join(timeout=2.0)
            except Exception:
                pass
            self._thread = None
        if self._hook_thread is not None:
            self._hook_thread = None

    def recalibrate(self) -> None:
        """Ask the loop to re-map the cluster now."""
        self._redrive.set()

    def _refresh_timed(self, dr) -> int:
        """refresh_ranges() with the first-map duration recorded."""
        self._map_started = time.monotonic()
        try:
            n = dr.refresh_ranges()
        finally:
            if self._map_s <= 0.0 and getattr(dr, "_ranges_ready", False):
                self._map_s = time.monotonic() - self._map_started
            self._map_started = 0.0
        return n

    def set_type_hint(self, hint: str) -> None:
        """Seed the DamageDisplay type pointer (ignored once live)."""
        hint = (hint or "").strip()
        if hint:
            self._type_hint = hint
            if self._reader is not None and self._reader._type_ptr is None:
                self._reader._type_hint = hint

    def set_result_type_hint(self, hint: str) -> None:
        """Seed the DamageResult type pointer (ignored once live)."""
        hint = (hint or "").strip()
        if hint:
            self._result_type_hint = hint
            if self._reader is not None and self._reader._result_type_ptr is None:
                self._reader._result_type_hint = hint

    def set_mode(self, mode: str) -> None:
        """Switch between: 'proxy' (Option 1) | 'injector' (Option 2) | 'memory' (Option 3)."""
        mode = (mode or "proxy").strip().lower()
        if mode not in ("proxy", "injector", "memory"):
            mode = "proxy"
        old_mode = self.mode
        self.mode = mode
        log.info("[dps-source] Switched DPS capture mode to: %s", self.mode)
        if old_mode != mode:
            self._emit_log(f"DPS capture mode changed to: {self.mode.upper()}")
        if self._started and not self._stop and mode == "memory":
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._loop, daemon=True,
                                                name="dps-memory-poller")
                self._thread.start()

    @property
    def started(self) -> bool:
        return self._started

    # --- event ring -------------------------------------------------------
    def _push(self, events: list) -> None:
        if not events:
            return
        with self._lock:
            for ev in events:
                self._ring.append(ev)
                self._total += 1

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

    # --- status for the UI ------------------------------------------------
    def is_hook_available(self) -> bool:
        """Check if bridge module is loaded in the game process (cached 2s)."""
        now = time.monotonic()
        if self._hook_last_ts > 0.0 and now - self._hook_last_ts < 30.0:
            return True
        if now - self._hook_check_ts > 2.0:
            self._hook_check_ts = now
            bridge_fn = getattr(self.proc, "detect_combat_bridge", None)
            if callable(bridge_fn):
                info = bridge_fn()
                new_loaded = False
                if self.mode == "proxy":
                    new_loaded = (info.get("option") == 1)
                elif self.mode == "injector":
                    new_loaded = (info.get("option") == 2)
                if new_loaded and not self._hook_is_loaded:
                    self._emit_log(f"Combat Bridge: DLL detected loaded in game — {info.get('status_summary', '')}")
                elif not new_loaded and info.get("proxy_in_game_dir") and not getattr(self, "_logged_proxy_on_disk", False):
                    self._logged_proxy_on_disk = True
                    self._emit_log(f"Combat Bridge: {info.get('status_summary', '')}")
                self._hook_is_loaded = new_loaded
            else:
                fn = getattr(self.proc, "is_module_loaded", None)
                if callable(fn):
                    if self.mode == "proxy":
                        self._hook_is_loaded = bool(fn("version.dll") or fn("dinput8.dll"))
                    elif self.mode == "injector":
                        self._hook_is_loaded = bool(fn("farever_dps.dll"))
                    else:
                        self._hook_is_loaded = False
                else:
                    self._hook_is_loaded = False
        return self._hook_is_loaded

    def status(self) -> str:
        """One of: live | bridge_ready | missing_proxy | need_inject | off."""
        now = time.monotonic()
        if (self._hook_last_ts > 0.0 and now - self._hook_last_ts < 5.0) or \
           (self._last_damage_ts > 0.0 and now - self._last_damage_ts < 5.0):
            return "live"
        if self.mode in ("proxy", "injector"):
            hook_loaded = self.is_hook_available()
            hb_ts = getattr(self, "_last_heartbeat_ts", 0.0)
            hb_alive = (hb_ts > 0.0 and (now - hb_ts) < 6.0)
            if self._hook_events_n > 0 or hook_loaded or hb_alive:
                return "bridge_ready"
            if self.mode == "proxy":
                return "missing_proxy"
            elif self.mode == "injector":
                return "need_inject"
        elif self.mode == "memory":
            if self._started:
                return "live"
        return "off"

    def counts(self) -> tuple[int, int]:
        """(last damage numbers read per poll, last hook events count)."""
        now = time.monotonic()
        if self._hook_last_ts > 0.0 and (now - self._hook_last_ts) < 10.0:
            return self._hook_events_n, 0
        return self._last_damage_n, self._hook_events_n

    def located_pointers(self) -> tuple[int | None, int | None]:
        """(DamageDisplay type ptr, DamageResult type ptr); (None, None) while locating."""
        dr = self._reader
        if dr is not None:
            return dr._type_ptr, dr._result_type_ptr
        return None, None

    def live_source_name(self) -> str:
        now = time.monotonic()
        if self.mode == "proxy":
            return "combat bridge (proxy)"
        elif self.mode == "injector":
            return "combat bridge (injector)"
        elif self.mode == "memory":
            return "memory reader"
        if self._hook_last_ts > 0.0 and (now - self._hook_last_ts) < 10.0:
            return "combat bridge"
        return "damage numbers"

    def diagnostics(self) -> dict:
        """Per-poll drop diagnostics from the active reader (read by the UI)."""
        dr = self._reader
        if dr is not None:
            return dict(dr._diag)
        return {}

    def timing_text(self) -> str:
        """Stage timings for the status line (only completed stages)."""
        parts: list[str] = []
        if self._calib_s > 0.0:
            parts.append(f"calib {self._calib_s:.1f}s")
        if self._map_started and self._map_s <= 0.0:
            parts.append(f"mapping {time.monotonic() - self._map_started:.0f}s…")
        elif self._map_s > 0.0:
            parts.append(f"map {self._map_s:.1f}s")
        if self._first_event_s > 0.0:
            parts.append(f"1st event {self._first_event_s:.1f}s")
        elif self._calib_s > 0.0 and self._started_at:
            el = time.monotonic() - self._started_at
            if el < 120.0:
                parts.append(f"no event yet ({el:.0f}s)")
        return " · ".join(parts)

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

    @property
    def has_decoded(self) -> bool:
        """True once at least one real event decoded this run."""
        return self._first_event_s > 0.0

    # --- hook IPC event receiver ------------------------------------------
    # --- hook-install status telemetry ("why is the bridge silent?") ------
    def _handle_bridge_status(self, obj: dict, now: float | None = None) -> None:
        """Consume one one-shot status line the DLL emits at hook install time.

        Stages: boot, net, table, mh_init, hook (per function), mh_enable.
        Every line goes to the Activity Log; failures are kept (per session)
        so bridge_issue() can explain a loaded-but-silent bridge.
        """
        now = time.time() if now is None else now
        stage = str(obj.get("s") or obj.get("stage") or "status")
        ok = bool(obj.get("ok"))
        msg = str(obj.get("m") or obj.get("msg") or "")
        try:
            self._bridge_status_log.append((now, stage, ok, msg))
        except Exception:
            pass
        self._emit_log(f"Combat Bridge: {msg or stage}{'' if ok else ' — FAILED'}")

    def bridge_issue(self) -> str:
        """Human reason the bridge is loaded but silent ('' when healthy).

        Only failures from the NEWEST DLL session count: scanning backwards
        stops at that session's ``boot`` line, and a healthy ``mh_enable``
        closes the window — so a working install never shows stale warnings.
        """
        try:
            now = time.time()
            lines = [x for x in self._bridge_status_log if now - x[0] <= 600.0]
            out: list[str] = []
            for _ts, stage, ok, msg in reversed(lines):
                if stage == "boot":
                    break
                if ok and stage == "mh_enable":
                    return ""
                if not ok and msg and msg not in out:
                    out.append(msg)
                if len(out) >= 3:
                    break
            return " · ".join(out)
        except Exception:
            return ""

    def _parse_hook_event(self, obj: dict | str, now: float | None = None) -> DamageEvent | None:
        try:
            if isinstance(obj, str):
                obj = json.loads(obj)
            if not isinstance(obj, dict):
                return None
            now = time.time() if now is None else now
            kind_raw = str(obj.get("t") or obj.get("kind") or "hit").lower()
            amt = float(obj.get("amt") or obj.get("amount") or obj.get("landed") or 0.0)
            kill = bool(obj.get("k") or obj.get("kill") or kind_raw == "kill" or False)
            if amt <= 0.0 and not kill:
                return None
            sk = str(obj.get("sk") or obj.get("skill") or "")
            src = str(obj.get("src") or obj.get("player") or obj.get("caster") or "")
            tgt = str(obj.get("tgt") or obj.get("target") or "")
            # Pure kill notifications (st.Player.notifyUnitKilled__impl) carry
            # only the killed unit's raw id under "uid" — no amount, no killer.
            uid = str(obj.get("uid") or "")
            if not tgt and uid:
                tgt = uid
            me = bool(obj.get("me") or obj.get("is_me") or False)
            crit = bool(obj.get("c") or obj.get("crit") or False)
            pet = str(obj.get("p") or obj.get("pet") or "")
            if pet and not sk.endswith("(pet)"):
                sk = f"{sk} (pet)"

            inc = bool(obj.get("inc") or obj.get("incoming") or False)
            if not src:
                src = "You" if me else "Player"
            if not tgt:
                tgt = "Enemy"

            if kind_raw in ("heal", "h"):
                landed = float(obj.get("landed", amt))
                return DamageEvent(
                    amount=landed,
                    skill=sk or "Heal",
                    skill_name=sk,
                    crit=False,
                    kill=kill,
                    kind=K_HEAL,
                    source_name=src,
                    target_name=tgt,
                    incoming=False,
                    is_me=me,
                    t=now,
                )
            return DamageEvent(
                amount=amt,
                skill=sk or "Attack",
                skill_name=sk,
                crit=crit,
                kill=kill,
                kind=K_DAMAGE,
                source_name=src,
                target_name=tgt,
                incoming=inc,
                is_me=me,
                t=now,
            )
        except Exception:
            return None

    def _ipc_loop(self) -> None:
        """Listen for UDP combat events from farever_dps.dll on localhost:49152."""
        sock = None
        while not self._stop:

            if sock is None:
                try:
                    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    try:
                        sock.bind(("127.0.0.1", 49152))
                    except Exception:
                        sock.bind(("0.0.0.0", 49152))
                    sock.settimeout(0.5)
                    log.info("[dps-source] UDP IPC listening on port 49152")
                    self._emit_log("Combat Bridge: Listening for DLL events on UDP 127.0.0.1:49152")
                except Exception as e:
                    log.debug("[dps-source] UDP IPC bind 49152: %s (will retry)", e)
                    if sock:
                        try:
                            sock.close()
                        except Exception:
                            pass
                    sock = None
                    time.sleep(1.0)
                    continue

            try:
                data, addr = sock.recvfrom(65535)
                if not data:
                    continue
                if not self._first_packet_logged:
                    self._first_packet_logged = True
                    self._emit_log(f"Combat Bridge: Connected — received UDP packet from DLL ({addr[0]}:{addr[1]})")
                now = time.time()
                text = data.decode("utf-8", errors="replace").replace("\x00", "")
                lines = text.splitlines()
                evs = []
                for line in lines:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except Exception:
                        log.debug("[dps-hook-ipc] Non-JSON line: %r", line)
                        continue
                    if not isinstance(obj, dict):
                        log.debug("[dps-hook-ipc] Discarded non-object line: %r", line)
                        continue
                    # Hook-install status telemetry — never a combat event.
                    if str(obj.get("t") or "") == "status":
                        self._handle_bridge_status(obj, now)
                        continue
                    if str(obj.get("t") or "") == "heartbeat":
                        self._last_heartbeat_ts = time.monotonic()
                        self._hook_is_loaded = True
                        continue
                    try:
                        ev = self._parse_hook_event(obj, now)
                        if ev is not None:
                            evs.append(ev)
                        else:
                            log.debug("[dps-hook-ipc] Discarded/unparsed line: %r", line)
                    except Exception as pe:
                        log.warning("[dps-hook-ipc] Parse error for line %r: %s", line, pe)
                if evs:
                    self._push(evs)
                    self._hook_last_ts = time.monotonic()
                    prev_count = self._hook_events_n
                    self._hook_events_n += len(evs)
                    if self._first_event_s <= 0.0 and self._started_at:
                        self._first_event_s = time.monotonic() - self._started_at
                    if not self._first_event_logged:
                        self._first_event_logged = True
                        self._emit_log(f"Combat Bridge: Live combat events streaming from DLL!")
                    # Log the first 10 hits individually so user sees confirmation in Activity Log
                    if prev_count < 10:
                        for e in evs:
                            if prev_count < 10:
                                prev_count += 1
                                crit_flag = " (CRIT)" if e.crit else ""
                                kill_flag = " [KILL]" if e.kill else ""
                                tag = "[HEAL]" if e.is_heal else ("[TAKEN]" if e.incoming else "[HIT]")
                                suffix = " heal" if e.is_heal else " dmg"
                                self._emit_log(f"Combat Bridge: {tag} #{prev_count} {e.source_name} -> {e.target_name} | {e.skill_name} | {e.amount:.1f}{suffix}{crit_flag}{kill_flag}")
                    elif self._hook_events_n % 50 == 0:
                        self._emit_log(f"Combat Bridge: Live stream healthy — {self._hook_events_n} combat events captured.")
                    log.info("[dps-hook-ipc] Successfully applied %d combat event(s)", len(evs))
            except socket.timeout:
                continue
            except Exception:
                time.sleep(0.05)
        if sock:
            try:
                sock.close()
            except Exception:
                pass

    # --- background loop (memory reader fallback) -------------------------
    def _loop(self) -> None:
        """Background memory reader fallback (polls combat floaties when hook is inactive)."""
        time.sleep(0.5)
        dr = None
        last_ev = 0.0
        while not self._stop:
            if self.mode != "memory":
                time.sleep(1.0)
                continue
            # If native hook is actively streaming events, yield to it (no double counting)
            now = time.monotonic()
            if self._hook_last_ts > 0.0 and (now - self._hook_last_ts) < 5.0:
                time.sleep(1.0)
                continue

            if dr is None:
                try:
                    dr = self._ensure_reader()
                except Exception as e:
                    log.debug("[dps-source] Failed to initialize DamageReader: %s", e)
                    time.sleep(1.0)
                    continue

            try:
                evs = dr.poll()
            except (ProcError, OSError):
                evs = []
            except Exception as e:
                log.debug("[dps-source] Poll exception: %s", e)
                evs = []

            if evs:
                now_ev = time.monotonic()
                last_ev = now_ev
                self._push(evs)
                self._last_damage_ts = now_ev
                self._last_damage_n = len(evs)
                if self._first_event_s <= 0.0 and self._started_at:
                    self._first_event_s = now_ev - self._started_at

            in_combat = (time.monotonic() - last_ev) < 3.0
            time.sleep(POLL_IN_COMBAT if in_combat else POLL_IDLE)

    def _ensure_reader(self) -> DamageReader:
        if self._reader is None:
            # Dedicated Hl: bg poller caches must not race the UI thread's hl.
            self._reader = DamageReader(self.proc, Hl(self.proc),
                                        type_hint=self._type_hint,
                                        result_type_hint=self._result_type_hint)
        return self._reader
