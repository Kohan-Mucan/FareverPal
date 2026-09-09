"""Combat-bridge capture: the in-game DLL — Option 1 (proxy) and Option 2 (injector).

Both options are the SAME native capture. ``farever_dps.dll`` hooks the HashLink
runtime and streams one JSON object per combat event over UDP
``127.0.0.1:49152``. They differ only in how the DLL gets into the process:

* **Option 1 - proxy**: a proxy DLL (``dinput8.dll`` by default, or
  ``version.dll`` / ``userenv.dll``) is dropped next to
  ``Farever.exe`` and the Windows loader pulls it in when the game imports that
  name.
* **Option 2 - injector**: ``farever_dps.dll`` is injected into the running game.

Everything downstream of "the DLL is loaded" is identical, so both options live
in this one file and share one parser, one listener and one status model. A fix
for either option belongs here.

Option 3 - the memory scanner - is a completely different capture: it reads the
game's damage floaty numbers out of the process with no DLL at all. It lives in
``dps_memory.py`` and must never be edited from here (and vice versa).

This module stores no events. Parsed events are handed to the ``emit`` callback
the coordinator passes in, and the coordinator owns the ring every consumer
drains.
"""
from __future__ import annotations

import json
import logging
import os
import socket
import subprocess
import threading
import time
from collections import deque
from typing import Callable

from ..constants import HL_FINDEX_INFLICT_DAMAGE, HL_FINDEX_NAMES
from .damage_events import (DamageEvent, K_DAMAGE, K_HEAL, clean_affinity,
                            clean_wire_name, wire_addr)
from .pe_imports import PROXY_DLL_NAMES
from .updater import is_frozen

log = logging.getLogger(__name__)

UDP_HOST = "127.0.0.1"
UDP_PORT = 49152

# Option 1: modules Windows loads into the game at boot (the drop-in proxy).
# ONE list, shared with the import-table scanner and the game-folder scan -
# winmm.dll was added to those and missed here once, so a proxy that was
# installed and working still read as "not loaded" through this probe.
PROXY_MODULES = PROXY_DLL_NAMES
# Option 2: the module the injector maps into a live process.
INJECT_MODULE = "farever_dps.dll"

# Both bridge options share this one file; only the module probe differs.
BRIDGE_MODES = ("proxy", "injector")
DEFAULT_MODE = "proxy"

LIVE_WINDOW_S = 5.0        # events this recent -> the bridge is LIVE
COUNT_WINDOW_S = 10.0      # counts() keeps reporting bridge numbers this long
HEARTBEAT_WINDOW_S = 6.0   # a heartbeat this recent means the DLL is alive
EVENT_VERIFY_S = 30.0      # events this recent -> no need to probe modules
LOADED_CACHE_S = 2.0       # module-presence probe cadence
STATUS_KEEP_S = 600.0      # one-shot status-telemetry retention window
# The "live stream healthy" roll-up is a LIVENESS note, so it is paced by time
# rather than by event count. Count-paced (the old every-50-events) meant the
# faster the player fought, the more it spoke — a hard-mode trash pull printed
# it every one to two seconds, which buried the first-10-hits lines and any
# attribution warning the log actually exists to surface. At this cadence a long
# fight still says "yes, still alive" every minute, and a quiet stretch says it
# once when the stream resumes.
HEALTH_LINE_PERIOD_S = 60.0
SOCKET_TIMEOUT_S = 0.5


def port_holder_pids(port: int) -> list[str]:
    """PIDs currently bound to ``port`` as UDP (best effort, via netstat).

    Used only to explain a failed bind. A UDP row is
    ``UDP  127.0.0.1:49152  *:*  6264`` - four fields, NO state column - so the
    PID is the last field and the local address is field 1.
    """
    try:
        out = subprocess.run(["netstat", "-ano", "-p", "UDP"],
                             capture_output=True, text=True, timeout=5,
                             check=False).stdout
    except Exception:
        return []
    pids: list[str] = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 4 or parts[0].upper() != "UDP":
            continue
        if parts[1].rsplit(":", 1)[-1] != str(port):
            continue
        pid = parts[-1]
        if pid not in pids:
            pids.append(pid)
    return pids


def _process_details(pids: list[str]) -> dict[str, tuple[str, str]]:
    """pid -> (image name, command line), via PowerShell CIM.

    No psutil dependency (it is not in the venv) and no bare ``tasklist``,
    which gives the image name but not enough of the command line to tell a
    stale debug monitor from the companion app - and telling those two apart is
    the entire point of the message.
    """
    if not pids:
        return {}
    flt = " OR ".join(f"ProcessId={p}" for p in pids)
    ps = ("$ErrorActionPreference='SilentlyContinue';"
          f"Get-CimInstance Win32_Process -Filter \"{flt}\" | "
          "ForEach-Object { \"$($_.ProcessId)|$($_.Name)|$($_.CommandLine)\" }")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                             capture_output=True, text=True, timeout=10,
                             check=False).stdout
    except Exception:
        return {}
    info: dict[str, tuple[str, str]] = {}
    for line in out.splitlines():
        parts = line.split("|", 2)
        if len(parts) == 3 and parts[0].strip().isdigit():
            info[parts[0].strip()] = (parts[1].strip(), parts[2].strip())
    return info


def describe_port_clash(port: int) -> list[str]:
    """One actionable line per process holding ``port``; [] when unknowable.

    Naming the PID alone is not enough: the two rivals are the companion app and
    a leftover monitor window, and the fix for each is different. Guessing wrong
    ("most likely the app") sends the user to close the one process that was
    never holding it.
    """
    pids = port_holder_pids(port)
    if not pids:
        return []
    info = _process_details(pids)
    lines: list[str] = []
    for pid in pids:
        name, cmd = info.get(pid, ("?", ""))
        low = cmd.lower()
        # Image name minus extension, so "FareverPal.exe" -> "fareverpal".
        base = name.rsplit(".", 1)[0].lower()
        if "monitor_bridge" in low:
            what = ("a stale copy of this monitor - close that window; it is "
                    "holding the port and swallowing every event")
        elif base == "fareverpal" or "run.py" in low or "farever_companion" in low:
            what = ("the FareverPal companion app - its bridge listener takes "
                    "the traffic, so this monitor sees nothing")
        else:
            what = (f"an unrecognised process ({name}) - close it if it is a "
                    "FareverPal tool")
        lines.append(f"PID {pid} [{name}]: {what}")
    return lines


def parse_hook_event(obj: dict | str, now: float | None = None) -> DamageEvent | None:
    """One JSON bridge line -> ``DamageEvent`` (None when not a combat event).

    Payload contract is documented in ``dps_bridge/README.md``; ``heartbeat``
    and ``status`` lines are handled by the listener and never reach this.
    """
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
        # Names off the wire are filtered for junk (`clean_wire_name`): a
        # drifted bridge slot sends control characters as a "name", and a row
        # minted under one is invisible while it eats the fight's damage.
        src = clean_wire_name(obj.get("src") or obj.get("player") or obj.get("caster") or "")
        tgt = clean_wire_name(obj.get("tgt") or obj.get("target") or "")
        # A pure kill line carries only the killed unit's raw id under "uid" -
        # no amount, no killer. The bridge reports every death this way, from
        # ent.Foe.onDie's receiver (the Player-side notify never fires).
        uid = str(obj.get("uid") or "")
        if not tgt and uid:
            tgt = uid
        me = bool(obj.get("me") or obj.get("is_me") or False)
        crit = bool(obj.get("c") or obj.get("crit") or False)
        pet = str(obj.get("p") or obj.get("pet") or "")
        if pet and not sk.endswith("(pet)"):
            sk = f"{sk} (pet)"
        # Per-hit damage type. The native bridge emits it short ("aff", like
        # amt/sk/tgt); "affinity" is accepted for hand-written payloads. An
        # absent key is normal (a bridge older than the field) -> untagged hit.
        aff = clean_affinity(obj.get("aff") or obj.get("affinity"))
        # The target unit's ADDRESS, short "taddr" like the rest of the wire.
        # Optional for the same reason as `aff` (an older bridge omits it, and
        # a missing address must not drop the hit): the tracker keys its
        # per-target split by address when it has one and by name when it does
        # not, so a name-only wire folds a multi-dummy yard onto one row. The
        # address is what splits it per dummy.
        taddr = wire_addr(obj.get("taddr") or obj.get("tgt_addr")
                          or obj.get("target_addr"))


        inc = bool(obj.get("inc") or obj.get("incoming") or False)
        if not src:
            src = "You" if me else "Player"
        if not tgt:
            tgt = "Enemy"

        if kind_raw in ("heal", "h"):
            # Heals carry the raw amount ("amt") and, when the DLL knows it, the
            # effective amount that actually landed ("landed"). A heal meter
            # counts what landed, so prefer it when present.
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
                target_addr=taddr,
                incoming=False,
                is_me=me,
                t=now,
                affinity=aff,
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
            target_addr=taddr,
            incoming=inc,
            is_me=me,
            t=now,
            affinity=aff,
        )
    except Exception:
        return None


RELAY_HOST = "127.0.0.1"
RELAY_PORT = 49153          # TCP. The app keeps 49152 and fans out from here.
RELAY_ENV = "FAREVER_BRIDGE_RELAY"
RELAY_MAX_CLIENTS = 4
RELAY_SEND_TIMEOUT_S = 0.25  # a wedged subscriber must not stall the DPS listener
RELAY_TICK_S = 0.5


def relay_enabled() -> bool:
    """Should the app re-broadcast the DLL's raw stream to a debug monitor?

    The DLL sends to ONE unicast destination, so exactly one process can ever
    receive it. A second listener on 49152 wins or loses by bind order and the
    loser reads as a dead DLL - which is a terrible thing to hit while trying to
    debug the DLL. So the app keeps the port and pushes a verbatim copy of every
    datagram to :class:`LineRelay` instead, where any number of subscribers can
    read it and the meter never notices.

    On for source-tree runs (that is where debugging happens), off in a frozen
    build unless ``FAREVER_BRIDGE_RELAY=1`` - the shipped app should never open
    a listening socket nobody asked for.
    """
    val = os.environ.get(RELAY_ENV, "").strip().lower()
    if val in ("1", "true", "yes", "on"):
        return True
    if val in ("0", "false", "no", "off"):
        return False
    return not is_frozen()


def _close_client(conn: socket.socket) -> None:
    """Close a subscriber socket so the peer sees a clean EOF, not a reset.

    A bare close() on a stream socket that still has unread data in its receive
    queue makes Windows send RST instead of FIN, and the subscriber gets
    WSAECONNRESET (10054) - which reads like the relay crashed when it simply
    restarted. shutdown(SHUT_WR) flushes a FIN first; the peer then reads b""
    and can tell "app is restarting" from "app died".
    """
    try:
        conn.shutdown(socket.SHUT_WR)
    except Exception:
        pass                      # already gone, or never connected
    try:
        conn.close()
    except Exception:
        pass


class LineRelay:
    """Loopback-TCP fan-out of the raw bridge stream, for debug subscribers.

    Deliberately dumb: it forwards the DLL's lines byte-for-byte, so a
    subscriber sees what the DLL actually sent rather than what the app's parser
    made of it. A subscriber that stops reading is dropped instead of being
    allowed to block - a debug tap must never be able to stall the DPS meter.
    """

    def __init__(self, port: int = RELAY_PORT, host: str = RELAY_HOST) -> None:
        self.host = host
        self.port = port
        self._srv: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = False
        self._clients: list[socket.socket] = []
        self._lock = threading.Lock()

    @property
    def active(self) -> bool:
        return self._srv is not None

    @property
    def clients(self) -> int:
        with self._lock:
            return len(self._clients)

    def start(self) -> bool:
        """Bind and serve. False when the port is taken (never raises)."""
        srv = None
        try:
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            # NO SO_REUSEADDR - and the reason is the same one as the capture
            # socket, which is the trap here: on WINDOWS (unlike POSIX) this
            # flag lets a SECOND process bind a port that is already LISTENING.
            # Two relays then share 49153 and a subscriber connects to whichever
            # won, silently - the "raw stream" you are debugging would come from
            # the other process. A failed bind is loud and says so; this was
            # neither. Rebinding after a crash can wait out TIME_WAIT.
            srv.bind((self.host, self.port))
            srv.listen(RELAY_MAX_CLIENTS)
            srv.settimeout(RELAY_TICK_S)
        except Exception:
            if srv is not None:
                try:
                    srv.close()
                except Exception:
                    pass
            return False
        self._srv = srv
        self._stop = False
        self._thread = threading.Thread(target=self._accept_loop, daemon=True,
                                        name="dps-bridge-relay")
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop = True
        srv, self._srv = self._srv, None
        if srv is not None:
            try:
                srv.close()
            except Exception:
                pass
        with self._lock:
            clients, self._clients = self._clients, []
        for c in clients:
            _close_client(c)
        t, self._thread = self._thread, None
        if t is not None:
            try:
                t.join(timeout=2.0)
            except Exception:
                pass

    def _accept_loop(self) -> None:
        while not self._stop:
            srv = self._srv
            if srv is None:
                break
            try:
                conn, _addr = srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with self._lock:
                full = len(self._clients) >= RELAY_MAX_CLIENTS
                if not full:
                    self._clients.append(conn)
            if full:
                _close_client(conn)
                continue
            try:
                conn.settimeout(RELAY_SEND_TIMEOUT_S)
                conn.sendall(f"# attached to FareverPal bridge relay "
                             f"{self.host}:{self.port} - raw DLL stream; the app "
                             f"owns 127.0.0.1:49152\n".encode("utf-8"))
            except Exception:
                self._drop(conn)

    def broadcast(self, text: str) -> None:
        with self._lock:
            clients = list(self._clients)
        if not clients:
            return
        try:
            payload = (text.rstrip("\n") + "\n").encode("utf-8", "replace")
        except Exception:
            return
        for c in clients:
            try:
                c.sendall(payload)
            except Exception:
                self._drop(c)

    def _drop(self, conn: socket.socket) -> None:
        with self._lock:
            if conn in self._clients:
                self._clients.remove(conn)
        _close_client(conn)


class BridgeSource:
    """The DLL capture shared by Option 1 (proxy) and Option 2 (injector).

    ``emit`` is the coordinator's batch sink; this object only parses, counts,
    logs and reports status.
    """

    def __init__(self, proc, emit: Callable[[list], None],
                 log_line: Callable[[str], None] | None = None):
        self.proc = proc
        self.mode = DEFAULT_MODE
        self.log_line = log_line
        self._emit = emit

        self._stop = False
        self._started = False
        self._thread: threading.Thread | None = None

        # live counters (listener thread writes, UI reads)
        self._last_event_ts: float = 0.0
        self._last_heartbeat_ts: float = 0.0
        # monotonic stamp of the last "Live stream healthy" roll-up, so the
        # line is paced by TIME (a liveness signal's own axis) rather than by an
        # event count that made it speak faster the harder you fought.
        self._last_health_line_ts: float = 0.0
        self.events_n: int = 0
        self._first_packet_logged = False
        self._first_event_logged = False
        # Tracker-fed gate for the periodic health roll-up (see
        # `_health_line_wanted`): the meter says which fights the log narrates
        # (a dungeon/rift run or a dummy test), so the roll-up stays quiet in
        # the open world instead of writing one line per 50 events forever.
        self.heartbeat_gate: Callable[[], bool] | None = None

        # module-presence probe cache
        self._check_ts: float = 0.0
        self.is_loaded_flag: bool = False
        self._logged_proxy_on_disk = False

        # Hook-install status telemetry from the DLL (ts, stage, ok, msg); a
        # loaded-but-silent bridge can explain itself from these.
        self._status_log: deque = deque(maxlen=24)
        self._port_clash_ts: float = 0.0

        # Debug fan-out (see LineRelay). Off unless relay_enabled(); the app
        # always keeps 49152 to itself either way.
        self._relay = LineRelay()

        # Set by the injector's own result (ui.game_attach._try_launch_hook).
        # Option 2 otherwise reads as a neutral "not attached" forever when the
        # injector refused, because the DLL genuinely is not loaded - which is
        # indistinguishable from "has not been tried yet" without this.
        self.inject_problem: str = ""

        # The damage pipeline this DLL session actually runs: which hooked
        # method the arm lines named, which table it landed on, and whether the
        # heartbeat's hit counter has ever moved. All three come off the wire -
        # 2026-09-30 is why: classifying the pipeline by reading the dev log by
        # hand produced a wrong answer (a hook that fired three times was
        # written down as silent), so the companion now says it itself.
        self._damage_capture: dict = {}
        self._capture_line_sent = False
        self.hits_n: int = 0            # last heartbeat's captured-hit count
        self.hits_reported: bool = False  # False for a DLL without the counter
        # Hits whose unit name the DLL could not read, so it put its own
        # "Enemy" on the wire. A counter of its own because the consequence is
        # the opposite one: the capture is working, and every name it produced
        # describes the wrong thing (fight archives, boss matches, party rows).
        # ``unnamed_reported`` keeps "this DLL never said" apart from "this DLL
        # said none", the same distinction the hit counter makes.
        self.unnamed_n: int = 0
        self.unnamed_reported: bool = False
        self._unnamed_line_sent = False

    # --- diagnostics --------------------------------------------------------
    def _emit_log(self, msg: str) -> None:
        log.info("[dps-bridge] %s", msg)
        cb = self.log_line
        if cb is not None:
            try:
                cb(msg)
            except Exception:
                pass

    # --- lifecycle ----------------------------------------------------------
    def set_mode(self, mode: str) -> None:
        """Option 1 = 'proxy', Option 2 = 'injector' (same DLL, other loader).

        Anything else (notably 'memory') is not a bridge mode: the mode is
        left unchanged. The coordinator routes memory capture to
        ``dps_memory.py`` and never sends it here.
        """
        mode = (mode or "").strip().lower()
        if mode in BRIDGE_MODES:
            self.mode = mode

    def start(self) -> None:
        # Restartable on purpose: the coordinator keeps exactly one engine
        # live, so switching capture modes stops this listener and starts it
        # again later. A live thread means already started.
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop = False
        self._started = True
        # Bridge-only wording: this UDP listener serves Options 1 & 2. It is
        # stopped while Option 3 owns the capture, so it must never claim to
        # serve "all capture modes".
        self._emit_log(f"Combat Bridge listener started (UDP {UDP_HOST}:{UDP_PORT}, "
                       "Options 1 & 2)")
        self._start_relay()
        self._thread = threading.Thread(target=self._ipc_loop, daemon=True,
                                        name="dps-hook-ipc")
        self._thread.start()

    def _start_relay(self) -> None:
        """Open the debug fan-out, and say so - silently attaching a monitor is
        how nobody learns the feature exists."""
        if not relay_enabled():
            return
        if not self._relay.start():
            self._emit_log(
                f"Combat Bridge: debug relay NOT started - TCP "
                f"{RELAY_HOST}:{RELAY_PORT} is already in use (another monitor?). "
                "DPS itself is unaffected.")
            return
        self._emit_log(
            f"Combat Bridge: debug relay listening on TCP "
            f"{RELAY_HOST}:{RELAY_PORT} — run monitor_bridge.bat to watch the raw "
            "DLL stream while this app keeps the meter running.")

    def stop(self) -> None:
        self._stop = True
        self._relay.stop()
        t, self._thread = self._thread, None
        if t is not None:
            try:
                t.join(timeout=2.0)
            except Exception:
                pass

    @property
    def started(self) -> bool:
        return self._started

    # --- live-state reads (UI thread) --------------------------------------
    def had_events(self, window_s: float = LIVE_WINDOW_S) -> bool:
        return self._last_event_ts > 0.0 and \
            (time.monotonic() - self._last_event_ts) < window_s

    @property
    def last_event_ts(self) -> float:
        return self._last_event_ts

    def heartbeat_alive(self) -> bool:
        return self._last_heartbeat_ts > 0.0 and \
            (time.monotonic() - self._last_heartbeat_ts) < HEARTBEAT_WINDOW_S

    @property
    def heartbeat_ts(self) -> float:
        return self._last_heartbeat_ts

    def is_loaded(self) -> bool:
        """Is the bridge module loaded in the game process? (probe cached 2s)"""
        now = time.monotonic()
        # Recent traffic proves it is loaded - never probe against that.
        if self.had_events(EVENT_VERIFY_S):
            return True
        if now - self._check_ts > LOADED_CACHE_S:
            self._check_ts = now
            info = None
            bridge_fn = getattr(self.proc, "detect_combat_bridge", None)
            if callable(bridge_fn):
                info = bridge_fn()
            if info is not None:
                want = 1 if self.mode == "proxy" else 2
                new_loaded = (info.get("option") == want)
                if new_loaded and not self.is_loaded_flag:
                    self._emit_log("Combat Bridge: DLL detected loaded in game "
                                   f"— {info.get('status_summary', '')}")
                elif not new_loaded and info.get("proxy_in_game_dir") \
                        and not self._logged_proxy_on_disk:
                    self._logged_proxy_on_disk = True
                    self._emit_log(f"Combat Bridge: {info.get('status_summary', '')}")
                self.is_loaded_flag = new_loaded
            else:
                fn = getattr(self.proc, "is_module_loaded", None)
                if callable(fn):
                    if self.mode == "proxy":
                        # Probe every shipped proxy name: a hardcoded index per
                        # name is how winmm.dll went missing from this check.
                        self.is_loaded_flag = any(bool(fn(n)) for n in PROXY_MODULES)
                    else:
                        self.is_loaded_flag = bool(fn(INJECT_MODULE))
                else:
                    self.is_loaded_flag = False
        return self.is_loaded_flag

    def status(self) -> str:
        """One of: live | bridge_ready | missing_proxy | need_inject |
        inject_failed."""
        if self.had_events():
            return "live"
        if self.events_n > 0 or self.is_loaded() or self.heartbeat_alive():
            self.inject_problem = ""   # it worked after all; drop the stale reason
            return "bridge_ready"
        if self.mode == "proxy":
            return "missing_proxy"
        return "inject_failed" if self.inject_problem else "need_inject"

    def source_name(self) -> str:
        return f"combat bridge ({self.mode})"

    @property
    def relay_active(self) -> bool:
        """Is the debug fan-out serving? (UI diagnostics read this.)"""
        return self._relay.active

    # --- hook-install telemetry ("why is the bridge silent?") --------------
    def handle_status(self, obj: dict, now: float | None = None) -> None:
        """Consume one one-shot status line the DLL emits at hook install time.

        Stages: boot, net, table, mh_init, hook (per function), mh_enable.
        Every line goes to the Activity Log; failures are kept (per session)
        so ``bridge_issue()`` can explain a loaded-but-silent bridge.
        """
        now = time.time() if now is None else now
        stage = str(obj.get("s") or obj.get("stage") or "status")
        ok = bool(obj.get("ok"))
        msg = str(obj.get("m") or obj.get("msg") or "")
        try:
            self._status_log.append((now, stage, ok, msg))
        except Exception:
            pass
        if stage == "boot":
            # A boot line is a NEW DLL session: the capture facts start over,
            # so the next session's pipeline line cannot be suppressed by this
            # one's (and cannot inherit its findex or table either).
            self._damage_capture = {}
            self._capture_line_sent = False
            self.hits_n = 0
            self.hits_reported = False
            self.unnamed_n = 0
            self.unnamed_reported = False
            self._unnamed_line_sent = False
        elif stage == "hook" and ok:
            self._note_arm_line(msg)
        # The per-stage chatter (boot / net / table / mh_init / one line per
        # armed method) is a DEV-run line. A normal session's Activity Log
        # keeps what answers a question - a failure, the arm summary, the
        # canary verdict, the pipeline-live line - and drops the rest, which
        # is what run-log.bat's FAREVER_DEV_CRASH_LOG buys back.
        from .bridge_log import dev_mode
        if ok and stage not in ("mh_enable", "canary") and not dev_mode():
            return
        self._emit_log(f"Combat Bridge: {msg or stage}{'' if ok else ' — FAILED'}")

    def _note_arm_line(self, msg: str) -> None:
        """Remember the damage capture from the DLL's own arming lines.

        Two shapes carry it: `onInflictDamage findex 4932 -> OK` (which method,
        which findex) and `onInflictDamage armed on table 0 (0x...)` (which
        candidate table the detour landed on). Both are facts about THIS run,
        which is what ``capture_summary`` speaks.
        """
        name = msg.split(" ", 1)[0]
        findex = None
        if "findex" in msg:
            try:
                findex = int(msg.split("findex", 1)[1].split()[0])
            except (IndexError, ValueError):
                findex = None
        table = None
        if "armed on table" in msg:
            try:
                table = int(msg.split("armed on table", 1)[1].split("(")[0].strip())
            except (IndexError, ValueError):
                table = None
        # The damage hook is the one whose findex or wire name says so; every
        # other method's lines (the heal pair, the kill pair) are ignored here,
        # or a build that arms something else would be described as this one.
        if findex != HL_FINDEX_INFLICT_DAMAGE and name != "onInflictDamage":
            return
        self._damage_capture["name"] = name
        if findex is not None:
            self._damage_capture["findex"] = findex
        if table is not None:
            self._damage_capture["table"] = table

    def capture_summary(self) -> str:
        """One sentence naming the damage pipeline the DLL is running.

        Built entirely from the wire: the arm lines name the hooked method and
        its table, the findex maps to the game-side method this app documents,
        and the heartbeat's hit counter is what proves a hit reached it. An
        unknown findex is still named, by the DLL's own text.
        """
        cap = self._damage_capture
        findex = cap.get("findex")
        label = HL_FINDEX_NAMES.get(findex) or cap.get("name") or "the damage hook"
        bits = [f"findex {findex}"] if findex is not None else []
        if cap.get("table") is not None:
            bits.append(f"table {cap['table']}")
        where = f" ({', '.join(bits)})" if bits else ""
        return f"damage pipeline live — {label}{where} produced its first hit"

    def bridge_issue(self) -> str:
        """Human reason the bridge is loaded but silent ('' when healthy).

        Only failures from the NEWEST DLL session count: scanning backwards
        stops at that session's ``boot`` line, and a healthy ``mh_enable``
        closes the window for everything the arming steps said before it - so
        a working install never shows stale warnings.

        The one thing that outlives that window is a NEWER failure: the
        per-frame canary (stage ``canary``) runs after ``mh_enable`` and is
        the bridge's own answer to "armed but silent" - hooks all reported OK,
        yet the detour on a function the game runs every frame never fired.
        A later healthy ``canary`` supersedes an earlier silent one, so a
        transient silence (a long load, a paused window) self-heals instead of
        sticking for the rest of the retention window.
        """
        try:
            now = time.time()
            lines = [x for x in self._status_log if now - x[0] <= STATUS_KEEP_S]
            out: list[str] = []
            canary_ok = False
            for _ts, stage, ok, msg in reversed(lines):
                if stage == "boot":
                    break
                if ok and stage == "canary":
                    canary_ok = True
                    continue
                if ok and stage == "mh_enable":
                    # A healthy arm closes the window only when nothing newer
                    # has failed (nothing newer could, before the canary).
                    return "" if not out else " · ".join(out)
                if not ok and msg and msg not in out:
                    if stage == "canary" and canary_ok:
                        continue
                    out.append(msg)
                if len(out) >= 3:
                    break
            return " · ".join(out)
        except Exception:
            return ""

    # --- UDP event receiver -------------------------------------------------
    def _ipc_loop(self) -> None:
        sock = None
        while not self._stop:
            if sock is None:
                try:
                    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    # Exclusive on purpose - no SO_REUSEADDR, no 0.0.0.0
                    # fallback. Both let a second process (the debug monitor,
                    # a stale instance) bind the same port, and Windows then
                    # delivers each datagram to only ONE socket - the last to
                    # bind wins. A rival silently steals every combat event and
                    # the Top DPS HUD just reads empty, so losing the race
                    # loudly is strictly better than losing it quietly.
                    sock.bind((UDP_HOST, UDP_PORT))
                    sock.settimeout(SOCKET_TIMEOUT_S)
                    log.info("[dps-bridge] UDP IPC listening on port %d", UDP_PORT)
                    self._emit_log("Combat Bridge: Listening for DLL events on "
                                   f"UDP {UDP_HOST}:{UDP_PORT}")
                except Exception as e:
                    log.debug("[dps-bridge] UDP IPC bind %d: %s (will retry)",
                              UDP_PORT, e)
                    if sock:
                        try:
                            sock.close()
                        except Exception:
                            pass
                    sock = None
                    self._log_port_clash(e)
                    time.sleep(1.0)
                    continue

            try:
                data, addr = sock.recvfrom(65535)
                if not data:
                    continue
                if not self._first_packet_logged:
                    self._first_packet_logged = True
                    # addr is the SENDER's socket, not ours: the DLL sends from
                    # a fresh ephemeral port every launch. Printing it bare
                    # next to UDP_PORT reads as "the port changed", so name it.
                    self._emit_log("Combat Bridge: Connected — received UDP "
                                   f"packet from DLL (source {addr[0]}:{addr[1]}, "
                                   f"our listener stays on {UDP_HOST}:{UDP_PORT})")
                now = time.time()
                text = data.decode("utf-8", errors="replace").replace("\x00", "")
                # Verbatim, BEFORE parsing: a subscriber debugging the DLL must
                # see what it sent, not what we made of it.
                self._relay.broadcast(text)
                self._ingest(text, now)
            except socket.timeout:
                continue
            except Exception:
                time.sleep(0.05)
        if sock:
            try:
                sock.close()
            except Exception:
                pass

    def _log_port_clash(self, exc: Exception) -> None:
        """Say WHO holds the bridge port, once every 30s.

        A lost bind used to be a ``log.debug`` nobody reads, so a dead Top DPS
        meter had no on-screen cause. The holder is almost always the debug
        monitor or a second instance of this app, and both are fixable by the
        user - so it belongs in the Activity Log, throttled to stay readable.
        """
        now = time.monotonic()
        if self._port_clash_ts and (now - self._port_clash_ts) < 30.0:
            return
        self._port_clash_ts = now
        who = "; ".join(describe_port_clash(UDP_PORT)) or "another process"
        self._emit_log(
            f"Combat Bridge: CANNOT listen on {UDP_HOST}:{UDP_PORT} — held by "
            f"{who}. No combat events can arrive while it holds the port, so "
            "DPS will stay empty. Close it and DPS resumes automatically.")

    def _ingest(self, text: str, now: float) -> None:
        """Decode one datagram (possibly several newline-separated JSON lines).

        Status/heartbeat lines are consumed here; every combat event decoded is
        counted, logged and forwarded through one ``_accept`` call.
        """
        evs: list = []
        for line in text.splitlines():
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
            kind = str(obj.get("t") or "")
            if kind == "status":
                self.handle_status(obj, now)
                continue
            if kind == "heartbeat" or kind == "hb":
                self._last_heartbeat_ts = time.monotonic()
                self.is_loaded_flag = True
                self._note_heartbeat(obj)
                continue
            ev = parse_hook_event(obj, now)
            if ev is not None:
                ev.capture_mode = self.mode
                evs.append(ev)
            else:
                log.debug("[dps-hook-ipc] Discarded/unparsed line: %r", line)
        if evs:
            self._accept(evs)

    def _note_heartbeat(self, obj: dict) -> None:
        """Read the bridge's liveness counters (captured hits, unnamed hits).

        Two counters, each spoken once per DLL session, because `hits` alone
        cannot tell "the capture is naming what it hits" from "the capture is
        hitting things it cannot name" - and that difference decides whether a
        name in the fight log is what the game called the unit or a placeholder
        the DLL had to invent.
        """
        self._note_hits(obj)
        self._note_unnamed(obj)

    def _note_hits(self, obj: dict) -> None:
        """The captured-hit counter: is the damage capture on the live path?

        The pipeline line is spoken HERE, on the first hit a session produces -
        and only for a DLL that reports the counter: an armed bridge whose
        capture never fired must never be described as live, and without the
        counter that is not knowable from the wire.
        """
        if "hits" not in obj:
            return
        try:
            self.hits_n = int(obj.get("hits") or 0)
        except (TypeError, ValueError):
            return
        self.hits_reported = True
        if self.hits_n <= 0 or self._capture_line_sent:
            return
        self._capture_line_sent = True
        self._emit_log("Combat Bridge: " + self.capture_summary())

    def _note_unnamed(self, obj: dict) -> None:
        """The name-placeholder counter: can the bridge name what it hits?

        The DLL resolves every unit name inside the game. When both of its
        candidate reads come back empty it still has to put SOMETHING on the
        wire (both hit templates are `%ls`, and an empty name would lose the
        event with it), so it sends the literal `"Enemy"` and counts the miss.
        That count is the only thing on the wire that separates a game where
        the enemy is called Enemy from a build whose foe-name offsets stopped
        resolving - the second reads identically on the board, in the fight
        archive and in boss matching, and stays wrong forever.

        Absent for a DLL older than the counter: nothing is claimed and
        ``unnamed_reported`` stays False, which is what the event-path report
        reads as "not reported" rather than as zero.
        """
        if "unnamed" not in obj:
            return
        try:
            n = int(obj.get("unnamed") or 0)
        except (TypeError, ValueError):
            return
        self.unnamed_n = n
        self.unnamed_reported = True
        if n <= 0 or self._unnamed_line_sent:
            return
        self._unnamed_line_sent = True
        self._emit_log(
            f"Combat Bridge: ⚠ {n} captured hits went out as \"Enemy\" — the "
            "DLL could not read the unit's name, so it sent its placeholder "
            "instead. The damage is real; the NAMES are not: fight archives, "
            "boss matching and party rows will read 'Enemy' until the native "
            "foe-name offsets are re-verified against this game build.")

    def _accept(self, evs: list) -> None:
        """Count, log and forward one batch of parsed bridge events."""
        now = time.monotonic()
        self._last_event_ts = now
        prev_count = self.events_n
        self.events_n += len(evs)
        self._emit(evs)
        if not self._first_event_logged:
            self._first_event_logged = True
            self._emit_log("Combat Bridge: Live combat events streaming from DLL!")
        # Log the first 10 hits individually so the user sees confirmation in
        # the Activity Log, then roll up to a periodic health line.
        if prev_count < 10:
            for e in evs:
                if prev_count >= 10:
                    break
                prev_count += 1
                crit_flag = " (CRIT)" if e.crit else ""
                kill_flag = " [KILL]" if e.kill else ""
                tag = "[HEAL]" if e.is_heal else ("[TAKEN]" if e.incoming else "[HIT]")
                suffix = " heal" if e.is_heal else " dmg"
                self._emit_log(
                    f"Combat Bridge: {tag} #{prev_count} {e.source_name} -> "
                    f"{e.target_name} | {e.skill_name} | "
                    f"{e.amount:.1f}{suffix}{crit_flag}{kill_flag}")
            # First hits are already the confirmation this line would give; arm
            # the clock here so the first roll-up waits a full period.
            self._last_health_line_ts = now
        elif self._health_line_due(now) and self._health_line_wanted():
            self._last_health_line_ts = now
            self._emit_log("Combat Bridge: Live stream healthy — "
                           f"{self.events_n} combat events captured.")
        log.info("[dps-hook-ipc] Successfully applied %d combat event(s)", len(evs))

    def _health_line_due(self, now: float) -> bool:
        """True when a HEALTH_LINE_PERIOD_S gap has passed since the last
        roll-up.

        Time-paced rather than count-paced on purpose: this is a LIVENESS
        signal, so what matters is how long since the user heard anything, not
        how many events landed. A hard-mode trash pull generated ~50 events
        every one to two seconds, which printed the line nonstop and pushed the
        per-hit lines and any attribution warning it is meant to sit behind off
        the top of the Activity Log.
        """
        return (now - self._last_health_line_ts) >= HEALTH_LINE_PERIOD_S

    def _health_line_wanted(self) -> bool:
        """Whether the periodic roll-up should speak for this batch.

        The line exists so a long fight shows the capture is alive without one
        line per hit; it is not a per-fight score, and where the fight happens
        decides whether it is worth a line at all. The tracker forwards that
        through `heartbeat_gate` — True inside a dungeon/rift or at a training
        dummy, the combat the log actually narrates — so open-world hits still
        feed the meter while the Activity Log stays readable. No gate (a source
        built before the hook) logs as before, and a gate that raises fails
        OPEN: a broken hint must not be able to silence a health signal.
        """
        gate = self.heartbeat_gate
        if gate is None:
            return True
        try:
            return bool(gate())
        except Exception:
            return True
