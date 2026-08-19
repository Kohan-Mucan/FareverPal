"""Background QThread workers for the Server diagnostics page.

Ping tester, live connection scanner and traceroute runner. All socket /
subprocess work happens off the UI thread; results come back via signals.
"""
from __future__ import annotations

import socket
import threading
import time

from PySide6 import QtCore

from .net import _get_farever_pid, _get_ip_country, _get_tcp_connections, _reverse_dns


class _PingWorker(QtCore.QThread):
    result   = QtCore.Signal(str, str, bool, float, str, str, str)
    finished = QtCore.Signal()

    def __init__(self, regions, active_codes):
        super().__init__()
        self._regions = regions
        self._active_codes = active_codes
        self._running = True

    def stop(self):
        self._running = False

    def run(self):
        targets = []
        for r in self._regions:
            if r["code"] in self._active_codes:
                for host, port in r["hosts"]:
                    targets.append((r["code"], host, port))

        if not targets:
            self.finished.emit()
            return

        def do_ping(code, host, port):
            if not self._running:
                return
            try:
                ip = socket.gethostbyname(host)
                country_name, country_code = _get_ip_country(ip)
            except OSError:
                if self._running:
                    self.result.emit(code, host + ":" + str(port), False, 0.0, "DNS failed", "Unknown", "")
                return

            if not self._running:
                return

            start = time.perf_counter()
            ok = False
            # Try multiple ports for "Black Box" gateways (Shard Port, then common router ports)
            test_ports = [port, 80, 443]
            for test_port in test_ports:
                try:
                    with socket.create_connection((ip, test_port), timeout=1.2):
                        pass
                    ok = True
                    break
                except (ConnectionRefusedError, OSError) as e:
                    import socket as s
                    if not isinstance(e, s.timeout):
                        ok = True  # Refused means the host is alive!
                        break
                except Exception:
                    pass

            ms = (time.perf_counter() - start) * 1000
            if self._running:
                self.result.emit(code, host + ":" + str(port), ok, ms, ip, country_name, country_code)

        threads = []
        for t in targets:
            th = threading.Thread(target=do_ping, args=t, daemon=True)
            th.start()
            threads.append(th)
            time.sleep(0.01)  # Tiny stagger

        for th in threads:
            th.join()

        self.finished.emit()


class _ConnectionScanWorker(QtCore.QThread):
    new_conn  = QtCore.Signal(str, int, str, float, str, str, str)
    status    = QtCore.Signal(str)
    pid_found = QtCore.Signal(int)
    pid_lost  = QtCore.Signal()

    def __init__(self):
        super().__init__()
        self._running = False

    def stop(self):
        self._running = False

    def run(self):
        self._running = True
        seen = set()
        last_pid = None

        while self._running:
            pid = _get_farever_pid()

            if pid is None:
                if last_pid is not None:
                    self.pid_lost.emit()
                    self.status.emit("Farever.exe closed.")
                    seen.clear()
                    last_pid = None

                # Interrupted sleep for faster shutdown
                for _ in range(10):
                    if not self._running:
                        break
                    time.sleep(0.1)
                continue

            if pid != last_pid:
                seen.clear()
                last_pid = pid
                self.pid_found.emit(pid)
                self.status.emit("      Copy")

            for c in _get_tcp_connections(pid):
                key = c["remote"]
                if key not in seen:
                    seen.add(key)
                    ip    = c["remote_ip"]
                    port  = c["remote_port"]
                    state = c["state"]
                    threading.Thread(
                        target=self._emit_with_metrics,
                        args=(ip, port, state),
                        daemon=True).start()

            time.sleep(0.1)

    def _emit_with_metrics(self, ip, port, state):
        hostname = _reverse_dns(ip)
        country_name, country_code = _get_ip_country(ip)
        start = time.perf_counter()
        try:
            with socket.create_connection((ip, port), timeout=1.5):
                pass
            ms = (time.perf_counter() - start) * 1000
        except Exception:
            ms = -1.0
        self.new_conn.emit(ip, port, hostname, ms, state, country_name, country_code)


class _TraceWorker(QtCore.QThread):
    output_ready = QtCore.Signal(str)
    finished = QtCore.Signal()

    def __init__(self, ip: str, mode: str = "tracert"):
        super().__init__()
        self.ip = ip
        self.mode = mode
        self._proc = None
        self._running = True

    def stop(self):
        self._running = False
        if self._proc:
            try:
                self._proc.terminate()
            except Exception:
                pass

    def run(self):
        import subprocess
        import re
        ip_regex = re.compile(r"(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})")

        # Modes:
        # tracert: standard with hostnames
        # tracert_fast: tracert -d (no hostnames)
        # tracert_geo: tracert -d + GeoIP lookup
        # net_info: ipconfig /flushdns + basic info
        if self.mode == "tracert":
            cmd = ["tracert", self.ip]
        elif self.mode == "tracert_fast":
            cmd = ["tracert", "-d", self.ip]
        elif self.mode == "tracert_geo":
            cmd = ["tracert", "-d", self.ip]
        elif self.mode == "net_info":
            cmd = ["powershell", "-Command", "Write-Host '> Flushing DNS...'; ipconfig /flushdns; Write-Host '`n> Local Network Info:'; ipconfig"]
        else:
            cmd = ["tracert", self.ip]

        try:
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
            )

            while self._running:
                line = self._proc.stdout.readline()
                if not line:
                    break

                # If we are in a traceroute mode, decorate with country info
                if "tracert" in self.mode:
                    match = ip_regex.search(line)
                    if match:
                        hop_ip = match.group(1)
                        country_name, country_code = _get_ip_country(hop_ip)
                        if country_name != "Unknown":
                            # Use text emoji for text logs
                            flag = "".join(chr(127397 + ord(c)) for c in country_code.upper()) if country_code else ""
                            line = line.rstrip() + f"  ({flag} {country_name})\n"

                self.output_ready.emit(line)

            self._proc.wait()
        except Exception as e:
            self.output_ready.emit(f"\nError: {e}\n")
        finally:
            self.finished.emit()


class _SteamPlayerCountWorker(QtCore.QThread):
    result = QtCore.Signal(int)

    def __init__(self, app_id: int = 3672400):
        super().__init__()
        self._app_id = app_id
        self._running = True

    def stop(self):
        self._running = False

    def run(self):
        import json
        import urllib.request
        url = f"https://api.steampowered.com/ISteamUserStats/GetNumberOfCurrentPlayers/v1/?appid={self._app_id}"
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "FareverCompanion/1.0"}
            )
            with urllib.request.urlopen(req, timeout=6) as resp:
                if not self._running:
                    return
                data = json.loads(resp.read().decode("utf-8"))
                count = data.get("response", {}).get("player_count", -1)
                if self._running:
                    self.result.emit(int(count))
        except Exception:
            if self._running:
                self.result.emit(-1)
