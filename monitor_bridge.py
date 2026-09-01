#!/usr/bin/env python3
"""
FareverPal Combat Bridge Live UDP Monitor
Listens on 127.0.0.1:49152 and prints all incoming bridge status and combat events.
"""
import sys
import os
import json
import socket
import datetime
import time

def format_ts():
    return datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]

def main():
    port = 49152
    host = "127.0.0.1"

    print("=" * 68)
    print(" FareverPal Combat Bridge UDP Live Monitor (127.0.0.1:49152)")
    print("=" * 68)
    print(f"[{format_ts()}] Binding to {host}:{port}...")

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    try:
        sock.bind((host, port))
    except Exception as e:
        print(f"[{format_ts()}] [ERROR] Could not bind to {host}:{port}: {e}")
        print("Note: Another process may have exclusive hold of the port.")
        input("\nPress Enter to exit...")
        return 1

    sock.settimeout(1.0)
    print(f"[{format_ts()}] [LISTENING] Ready! Waiting for packets from the DLL...")
    print("=" * 68)

    packet_count = 0
    event_count = 0
    last_wait_msg = time.time()
    first_received = False

    try:
        while True:
            try:
                data, addr = sock.recvfrom(65535)
            except socket.timeout:
                if not first_received and (time.time() - last_wait_msg > 5.0):
                    last_wait_msg = time.time()
                    print(f"[{format_ts()}] [WAITING] No UDP packets yet on {host}:{port}...")
                    print("  * Ensure Farever.exe is running.")
                    print("  * Check that dinput8.dll or version.dll is placed next to Farever.exe,")
                    print("    or run 'farever_dps.exe <PID>' to inject the DLL.")
                continue

            packet_count += 1
            now_ts = format_ts()
            if not first_received:
                first_received = True
                print(f"\n[{now_ts}] >>> [CONNECTED] Received first UDP datagram from {addr[0]}:{addr[1]}! <<<\n")

            text = data.decode("utf-8", errors="replace").replace("\x00", "")
            lines = text.splitlines()

            for line in lines:
                line = line.strip()
                if not line:
                    continue
                event_count += 1

                try:
                    obj = json.loads(line)
                except Exception:
                    print(f"[{now_ts}] [RAW] {line}")
                    continue

                if not isinstance(obj, dict):
                    print(f"[{now_ts}] [RAW] {line}")
                    continue

                t = str(obj.get("t") or obj.get("kind") or "").lower()

                if t == "status":
                    stage = obj.get("s") or obj.get("stage") or "?"
                    ok = obj.get("ok")
                    msg = obj.get("m") or obj.get("msg") or ""
                    status_flag = "[OK]" if ok else "[FAILED]"
                    print(f"[{now_ts}] [STATUS] {status_flag} Stage: {stage} -> {msg}")

                elif t == "hit":
                    amt = float(obj.get("amt") or obj.get("amount") or 0.0)
                    sk = obj.get("sk") or obj.get("skill") or "Attack"
                    src = obj.get("src") or obj.get("player") or "Unknown"
                    tgt = obj.get("tgt") or obj.get("target") or "Enemy"
                    crit = bool(obj.get("c") or obj.get("crit"))
                    kill = bool(obj.get("k") or obj.get("kill"))
                    me = bool(obj.get("me") or obj.get("is_me"))

                    flags = []
                    if me: flags.append("ME")
                    if crit: flags.append("CRIT")
                    if kill: flags.append("KILL")
                    flag_str = f" [{' | '.join(flags)}]" if flags else ""

                    print(f"[{now_ts}] [HIT #{event_count}] {src} -> {tgt} | Skill: {sk} | {amt:,.1f} dmg{flag_str}")

                elif t in ("heal", "h"):
                    amt = float(obj.get("amt") or obj.get("landed") or 0.0)
                    sk = obj.get("sk") or obj.get("skill") or "Heal"
                    src = obj.get("src") or obj.get("caster") or "Player"
                    tgt = obj.get("tgt") or obj.get("target") or "Target"
                    me = bool(obj.get("me") or obj.get("is_me"))
                    me_flag = " [ME]" if me else ""
                    print(f"[{now_ts}] [HEAL #{event_count}] {src} -> {tgt} | Skill: {sk} | +{amt:,.1f} hp{me_flag}")

                elif t == "kill":
                    uid = obj.get("uid") or "?"
                    print(f"[{now_ts}] [KILL] Defeated Unit ID: {uid}")

                elif t in ("heartbeat", "hb"):
                    tick = obj.get("tick", 0)
                    hooks = obj.get("hooks", 4)
                    print(f"[{now_ts}] [HEARTBEAT] Bridge alive (tick: {tick}, hooks: {hooks})")

                else:
                    print(f"[{now_ts}] [EVENT:{t}] {line}")

    except KeyboardInterrupt:
        print(f"\n[{format_ts()}] Stopped monitor. Total packets: {packet_count}, Total events: {event_count}")
    finally:
        try:
            sock.close()
        except Exception:
            pass

if __name__ == "__main__":
    main()
