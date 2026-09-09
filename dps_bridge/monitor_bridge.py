#!/usr/bin/env python3
"""
FareverPal Combat Bridge Live Monitor

Two ways in, because the DLL's datagrams have exactly one receiver:

* RELAY (default when the app is running): the companion owns
  127.0.0.1:49152 and pushes a verbatim copy of every datagram to a loopback
  TCP port. This monitor attaches to that, so the app's Top DPS meter keeps
  working while you watch the raw stream. Start the app first.
* DIRECT (fallback): bind 49152 yourself and read the DLL directly. Only
  possible when the app is NOT running, because two listeners on one unicast
  port mean one of them silently receives nothing.

Force either with `--relay` or `--direct`.
"""
import sys
import os
import json
import socket
import datetime
import time

# One source of truth for the bridge's port + "who owns it" helpers - the app
# needs the same facts, and two copies of a netstat column index is how they
# drift apart. The core module is headless (no Qt), so this stays cheap.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from farever_companion.core.dps_bridge import (
    RELAY_HOST, RELAY_PORT, UDP_HOST, UDP_PORT,
    describe_port_clash,
)

# The app resolves skill ids through data/names.py before showing them; a
# monitor that printed the raw field alone would hide the failure you are
# hunting (a DLL sending an id the app cannot resolve looks exactly like one
# that works). Guarded because a debug tool that dies on a data import is no
# use at 2am - it falls back to the raw id, which is still readable.
try:
    from farever_companion.data import names as _names
except Exception:                                        # pragma: no cover
    _names = None

HOST = UDP_HOST
PORT = UDP_PORT


def skill_label(raw):
    """What the app would display, plus the id the DLL actually sent.

    Mirrors DpsTracker._skill_labels: an unreadable skill stays "Unknown" and
    is never dressed up as a basic attack, because "Attack" in a debug log is
    indistinguishable from a real auto-attack and sends you the wrong way.
    """
    raw = str(raw or "").strip()
    if not raw or raw == "?":
        return "Unknown"
    pretty = raw
    if _names is not None:
        try:
            pretty = _names.skill_name(raw) or raw
        except Exception:
            pretty = raw
    # Keep the raw id whenever it differs - the id is the thing under suspicion.
    return pretty if pretty == raw else f"{pretty} [{raw}]"


def format_ts():
    return datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]


def _pause():
    """Wait for Enter, but never explode when stdin is closed or piped."""
    try:
        input("\nPress Enter to exit...")
    except (EOFError, KeyboardInterrupt):
        pass


class Printer:
    """Formats one bridge line, identically in both transports.

    Counting and rendering live here so DIRECT and RELAY cannot drift apart -
    a monitor that formats differently depending on how it attached is worse
    than no monitor when you are comparing two runs.
    """

    def __init__(self):
        self.packets = 0
        self.events = 0
        self.first_received = False

    def feed(self, text, source=""):
        for raw in text.splitlines():
            line = raw.strip()
            if not line:
                continue
            if line.startswith("#"):
                print(line)          # relay greeting
                continue
            self.packets += 0        # packet counting is per-datagram, not per-line
            self.events += 1
            if not self.first_received:
                self.first_received = True
                print(f"\n[{format_ts()}] >>> [CONNECTED] First data via {source} <<<\n")
            print(self.render(line, self.events))

    def render(self, line, n):
        ts = format_ts()
        try:
            obj = json.loads(line)
        except Exception:
            return f"[{ts}] [RAW] {line}"
        if not isinstance(obj, dict):
            return f"[{ts}] [RAW] {line}"

        t = str(obj.get("t") or obj.get("kind") or "").lower()

        if t == "status":
            stage = obj.get("s") or obj.get("stage") or "?"
            ok = obj.get("ok")
            msg = obj.get("m") or obj.get("msg") or ""
            flag = "[OK]" if ok else "[FAILED]"
            return f"[{ts}] [STATUS] {flag} Stage: {stage} -> {msg}"

        if t == "hit":
            amt = float(obj.get("amt") or obj.get("amount") or 0.0)
            sk = skill_label(obj.get("sk") or obj.get("skill"))
            src = obj.get("src") or obj.get("player") or "Unknown"
            tgt = obj.get("tgt") or obj.get("target") or "Enemy"
            flags = []
            if obj.get("me") or obj.get("is_me"):
                flags.append("ME")
            if obj.get("c") or obj.get("crit"):
                flags.append("CRIT")
            if obj.get("k") or obj.get("kill"):
                flags.append("KILL")
            flag_str = f" [{' | '.join(flags)}]" if flags else ""
            dir_tag = "[TAKEN]" if obj.get("inc") else "[DEALT]"
            return (f"[{ts}] {dir_tag} [HIT #{n}] {src} -> {tgt} | Skill: {sk} | "
                    f"{amt:,.1f} dmg{flag_str}")

        if t in ("heal", "h"):
            amt = float(obj.get("amt") or obj.get("landed") or 0.0)
            sk = skill_label(obj.get("sk") or obj.get("skill"))
            src = obj.get("src") or obj.get("caster") or "Player"
            tgt = obj.get("tgt") or obj.get("target") or "Target"
            me = " [ME]" if (obj.get("me") or obj.get("is_me")) else ""
            return (f"[{ts}] [HEAL #{n}] {src} -> {tgt} | Skill: {sk} | "
                    f"+{amt:,.1f} hp{me}")

        if t == "kill":
            return f"[{ts}] [KILL] Defeated Unit ID: {obj.get('uid') or '?'}"

        if t in ("heartbeat", "hb"):
            return (f"[{ts}] [HEARTBEAT] Bridge alive "
                    f"(tick: {obj.get('tick', 0)}, hooks: {obj.get('hooks', 4)})")

        return f"[{ts}] [EVENT:{t}] {line}"


def banner(title):
    print("=" * 68)
    print(f" FareverPal Combat Bridge Live Monitor - {title}")
    print("=" * 68)


def _connect_relay(timeout=1.0):
    """One relay connection, or None if the app is not offering one."""
    try:
        return socket.create_connection((RELAY_HOST, RELAY_PORT), timeout=timeout)
    except OSError:
        return None


def run_relay(printer, sock):
    """Stream from the relay, and survive it going away.

    The app restarts its capture listener whenever the capture mode changes, so
    a dropped connection is ROUTINE, not an error. Dying on it would make the
    monitor useless for the one thing it is for - so reconnect and say so. Any
    OSError is handled: a debug tap that crashes on a reset is a worse tool than
    no tap at all.
    """
    first = True
    while True:
        if first:
            banner(f"RELAY via the running app (TCP {RELAY_HOST}:{RELAY_PORT})")
            print("  The app owns 127.0.0.1:49152 and forwards every datagram here.")
            print("  Its Top DPS meter stays live. Ctrl+C to detach.")
        buf = b""
        reason = "closed by the app"
        try:
            while True:
                try:
                    chunk = sock.recv(65536)
                except socket.timeout:
                    continue
                if not chunk:
                    break                       # clean EOF
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    printer.feed(line.decode("utf-8", "replace"), "app relay")
        except KeyboardInterrupt:
            print()
            return
        except ConnectionResetError:
            reason = "connection reset by the app"
        except OSError as e:
            reason = f"connection lost ({e.__class__.__name__})"
        finally:
            try:
                sock.close()
            except Exception:
                pass
        print(f"[{format_ts()}] [DETACHED] {reason} - the app most likely "
              f"restarted capture. Reconnecting in 1.5s (Ctrl+C to quit)...")
        time.sleep(1.5)
        nxt = _connect_relay(timeout=1.0)
        if nxt is None:
            print(f"[{format_ts()}] [WAITING] No app relay on "
                  f"{RELAY_HOST}:{RELAY_PORT} - is FareverPal running? Retrying...")
            while nxt is None:
                time.sleep(2.0)
                nxt = _connect_relay(timeout=1.0)
        sock = nxt
        first = False


def _relay_advice(holders):
    """Precise next step when the app owns the port but no relay is answering.

    The generic "start the app" is actively misleading in this state - the app
    IS running, it just predates the relay (or has it switched off), so the
    monitor falls through to DIRECT, cannot bind, and tells the user to do the
    thing they already did. That dead end cost a debugging session once.
    """
    app_running = any("companion app" in h for h in holders)
    if not app_running:
        return []
    if _connect_relay(timeout=1.0) is not None:
        return []                       # relay is up; the caller got a stale answer
    return [
        "  The app IS running and owns 49152, but its debug relay is not",
        "  answering on " + f"{RELAY_HOST}:{RELAY_PORT}.",
        "  That means this app process predates the relay, or it was launched",
        "  with the relay switched off. Restart FareverPal, then run this",
        "  monitor again - it will attach instead of competing for the port.",
        "",
        "  Running from source? The relay is automatic for source-tree runs.",
        "  In a frozen build, launch with FAREVER_BRIDGE_RELAY=1.",
    ]


def run_direct(printer):
    """Bind the DLL's own port. Only works when the app is not running."""
    banner(f"DIRECT on {HOST}:{PORT} (the app must NOT be running)")
    print(f"[{format_ts()}] Binding to {HOST}:{PORT}...")
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    # NO SO_REUSEADDR here, on purpose. With it set, Windows happily lets a
    # second socket bind the same port and then delivers each datagram to only
    # ONE of them (unspecified which) - so a running app would silently swallow
    # every packet and this monitor would read as a dead DLL.
    try:
        sock.bind((HOST, PORT))
    except Exception as e:
        print(f"[{format_ts()}] [ERROR] Could not bind to {HOST}:{PORT}: {e}")
        print()
        holders = describe_port_clash(PORT)
        if holders:
            print("  This port is already taken by:")
            for line in holders:
                print(f"    - {line}")
            print()
            advice = _relay_advice(holders)
            if advice:
                for line in advice:
                    print(line)
            else:
                print("  Start the app and let the RELAY path serve you instead -")
                print("  only one process can receive these datagrams.")
        else:
            print("  No process could be identified on the port. Something else may")
            print("  still be holding it - close other FareverPal tools and retry.")
        _pause()
        return False

    sock.settimeout(1.0)
    print(f"[{format_ts()}] [LISTENING] Waiting for packets from the DLL...")
    last_wait = time.time()
    try:
        while True:
            try:
                data, addr = sock.recvfrom(65535)
            except socket.timeout:
                if not printer.first_received and (time.time() - last_wait > 5.0):
                    last_wait = time.time()
                    print(f"[{format_ts()}] [WAITING] No UDP packets yet on {HOST}:{PORT}...")
                    print("  This port is bound exclusively by this monitor, so nobody")
                    print("  else is stealing the traffic - the DLL genuinely is not")
                    print("  sending. In order of likelihood:")
                    print("   1. Farever.exe is not running, or is not loaded into a world.")
                    print("   2. The DLL is not loaded: place dinput8.dll next to Farever.exe,")
                    print("      or run 'farever_dps.exe <PID>' to inject it.")
                    print("   3. Hooks installed but silent - a STATUS line should arrive on")
                    print("      install; if none prints, the DLL never sent telemetry.")
                continue
            printer.packets += 1
            text = data.decode("utf-8", errors="replace").replace("\x00", "")
            printer.feed(text, f"UDP from {addr[0]}:{addr[1]}")
    except KeyboardInterrupt:
        pass
    finally:
        try:
            sock.close()
        except Exception:
            pass
    return True


def main():
    args = [a.lower() for a in sys.argv[1:]]
    printer = Printer()

    if "--direct" not in args:
        conn = _connect_relay()
        if conn is not None:
            run_relay(printer, conn)
            print(f"\n[{format_ts()}] Stopped. {printer.events} events, "
                  f"{printer.packets} packets.")
            return 0
        if "--relay" in args:
            print(f"[{format_ts()}] [ERROR] --relay was requested but no app "
                  f"relay answered on {RELAY_HOST}:{RELAY_PORT}.")
            print("  Start FareverPal first, or drop --relay to take 49152 directly.")
            _pause()
            return 1

    run_direct(printer)
    print(f"\n[{format_ts()}] Stopped. {printer.events} events, "
          f"{printer.packets} packets.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
