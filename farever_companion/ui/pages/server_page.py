"""Server diagnostics page - ping tester + live connection scanner.

Two panels:
  1. Region ping tester  - probes known server hostnames. Click cards to toggle inclusion.
  2. Live connection scanner - polls Windows GetExtendedTcpTable to capture
                               every TCP connection Farever.exe makes.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes
import socket
import struct
import time
import threading
import json
import urllib.request
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from .. import theme
from .. import components as C
from ...config import config_dir

# ---------------------------------------------------------------------------
# Region definitions
# Note: Main 2 (Global Log 1/2) and normal region configs
# ---------------------------------------------------------------------------
REGIONS = [
    # --- Main Game Servers ---
    {
        "code": "na",
        "label": "North America",
        "flag": "🇺🇸",
        "iso": "us",
        "icon": "broadcast",
        "hosts": [],
    },
    {
        "code": "eu",
        "label": "Europe",
        "flag": "🇪🇺",
        "iso": "eu",
        "icon": "broadcast",
        "hosts": [],
    },
    {
        "code": "as",
        "label": "Asia",
        "flag": "🇸🇬",
        "iso": "sg",
        "icon": "broadcast",
        "hosts": [],
    },
    {
        "code": "sa",
        "label": "South America",
        "flag": "🇧🇷",
        "iso": "br",
        "icon": "broadcast",
        "hosts": [],
    },
    {
        "code": "cn",
        "label": "China",
        "flag": "🇨🇳",
        "iso": "cn",
        "icon": "broadcast",
        "hosts": [],
    },
    # --- Test Servers ---
    {
        "code": "na_test",
        "label": "NA Test",
        "flag": "⚒️ 🇺🇸",
        "iso": "us",
        "icon": "broadcast",
        "hosts": [],
    },
    {
        "code": "eu_test",
        "label": "EU Test",
        "flag": "⚒️ 🇪🇺",
        "iso": "eu",
        "icon": "broadcast",
        "hosts": [],
    },
    {
        "code": "as_test",
        "label": "Asia Test",
        "flag": "⚒️ 🇸🇬",
        "iso": "sg",
        "icon": "broadcast",
        "hosts": [],
    },
    {
        "code": "sa_test",
        "label": "SA Test",
        "flag": "⚒️ 🇧🇷",
        "iso": "br",
        "icon": "broadcast",
        "hosts": [],
    },
    {
        "code": "cn_test",
        "label": "China Test",
        "flag": "⚒️ 🇨🇳",
        "iso": "cn",
        "icon": "broadcast",
        "hosts": [],
    },
    # --- Login Gateways ---
    {
        "code": "login_global",
        "label": "Global Login",
        "flag": "🌐",
        "iso": "fr",
        "icon": "terminal",
        "hosts": [
            ("37.187.157.32",      60442),
            ("51.83.66.84",        2003),
            ("51.178.64.48",       60442),
        ],
    },
]

_STATUS_COLORS = {
    "ok":       "#4ade80",
    "fail":     "#f87171",
    "pending":  "#94a3b8",
    "dns_fail": "#fb923c",
    "warn":     "#fbbf24",
}

# ---------------------------------------------------------------------------
# Windows TCP table scanner
# ---------------------------------------------------------------------------
AF_INET = 2
TCP_TABLE_OWNER_PID_ALL = 5
PROCESS_NAME = "Farever.exe"

TCP_STATES = {
    1: "CLOSED", 2: "LISTEN", 3: "SYN_SENT", 4: "SYN_RCVD",
    5: "ESTABLISHED", 6: "FIN_WAIT_1", 7: "FIN_WAIT_2", 8: "CLOSE_WAIT",
    9: "CLOSING", 10: "LAST_ACK", 11: "TIME_WAIT", 12: "DELETE_TCB",
}


def _get_farever_pid():
    try:
        TH32CS_SNAPPROCESS = 0x2

        class PE32W(ctypes.Structure):
            _fields_ = [
                ("dwSize",              ctypes.wintypes.DWORD),
                ("cntUsage",            ctypes.wintypes.DWORD),
                ("th32ProcessID",       ctypes.wintypes.DWORD),
                ("th32DefaultHeapID",   ctypes.POINTER(ctypes.c_ulong)),
                ("th32ModuleID",        ctypes.wintypes.DWORD),
                ("cntThreads",          ctypes.wintypes.DWORD),
                ("th32ParentProcessID", ctypes.wintypes.DWORD),
                ("pcPriClassBase",      ctypes.c_long),
                ("dwFlags",             ctypes.wintypes.DWORD),
                ("szExeFile",           ctypes.c_wchar * 260),
            ]

        k32 = ctypes.windll.kernel32
        snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if snap in (0, None, -1):
            return None
        try:
            e = PE32W()
            e.dwSize = ctypes.sizeof(PE32W)
            ok = k32.Process32FirstW(snap, ctypes.byref(e))
            while ok:
                if e.szExeFile.lower() == PROCESS_NAME.lower():
                    return int(e.th32ProcessID)
                ok = k32.Process32NextW(snap, ctypes.byref(e))
        finally:
            k32.CloseHandle(snap)
    except Exception:
        pass
    return None


def _get_tcp_connections(pid: int) -> list:
    results = []
    try:
        iphlp = ctypes.windll.iphlpapi
        size = ctypes.wintypes.DWORD(0)
        iphlp.GetExtendedTcpTable(
            None, ctypes.byref(size), True, AF_INET, TCP_TABLE_OWNER_PID_ALL, 0)
        buf = ctypes.create_string_buffer(size.value)
        ret = iphlp.GetExtendedTcpTable(
            buf, ctypes.byref(size), True, AF_INET, TCP_TABLE_OWNER_PID_ALL, 0)
        if ret != 0:
            return results
        offset = 0
        count = struct.unpack_from("<I", buf.raw, offset)[0]
        offset += 4
        for _ in range(count):
            if offset + 24 > len(buf.raw):
                break
            state, laddr, lport, raddr, rport, owner_pid = struct.unpack_from(
                "<IIIIII", buf.raw, offset)
            offset += 24
            if owner_pid != pid or raddr == 0:
                continue
            rport_h = socket.ntohs(rport & 0xFFFF)
            remote_ip = socket.inet_ntoa(struct.pack("<I", raddr))
            results.append({
                "state":       TCP_STATES.get(state, f"STATE{state}"),
                "remote_ip":   remote_ip,
                "remote_port": rport_h,
                "remote":      remote_ip + ":" + str(rport_h),
            })
    except Exception:
        pass
    return results


def _reverse_dns(ip: str) -> str:
    try:
        return socket.gethostbyaddr(ip)[0]
    except OSError:
        return ip


_COUNTRY_CACHE = {}
FLAGS_DIR = Path(config_dir()) / "cache" / "flags"
FLAGS_DIR.mkdir(parents=True, exist_ok=True)


def _download_flag(code: str) -> Path | None:
    if not code or len(code) != 2:
        return None
    code = code.lower()
    path = FLAGS_DIR / f"{code}.png"
    if path.exists():
        return path
    try:
        # Using flagcdn.com for high quality small flags
        url = f"https://flagcdn.com/w40/{code}.png"
        req = urllib.request.Request(url, headers={"User-Agent": "FareverPal/1.0"})
        with urllib.request.urlopen(req, timeout=2.0) as response:
            path.write_bytes(response.read())
        return path
    except Exception:
        return None


def _get_ip_country(ip: str) -> tuple[str, str]:
    """Returns (country_name, iso_code)"""
    if ip in _COUNTRY_CACHE:
        return _COUNTRY_CACHE[ip]

    # Quick check for private/local IPs
    if ip.startswith(("127.", "192.168.", "10.", "172.16.")):
        return "LAN", "lan"

    try:
        url = f"http://ip-api.com/json/{ip}?fields=status,country,countryCode"
        with urllib.request.urlopen(url, timeout=1.5) as response:
            data = json.loads(response.read().decode())
            if data.get("status") == "success":
                code = data.get("countryCode", "").lower()
                name = data.get("country", "Unknown")
                _download_flag(code)
                res = (name, code)
                _COUNTRY_CACHE[ip] = res
                return res
    except Exception:
        pass

    return "Unknown", ""


def _get_flag_pixmap(code: str, size: int = 24):
    if not code:
        return None
    p = FLAGS_DIR / f"{code.lower()}.png"
    if p.exists():
        pm = QtGui.QPixmap(str(p))
        if not pm.isNull():
            return pm.scaled(size, size, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
    return None


# ---------------------------------------------------------------------------
# Background workers
# ---------------------------------------------------------------------------

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
            if not self._running: return
            try:
                ip = socket.gethostbyname(host)
                country_name, country_code = _get_ip_country(ip)
            except OSError:
                if self._running:
                    self.result.emit(code, host + ":" + str(port), False, 0.0, "DNS failed", "Unknown", "")
                return
            
            if not self._running: return
            
            start = time.perf_counter()
            ok = False
            # Try multiple ports for "Black Box" gateways (Shard Port, then common router ports)
            for test_port in [port, 80, 443]:
                try:
                    with socket.create_connection((ip, test_port), timeout=1.5):
                        pass
                    ok = True
                    break
                except (ConnectionRefusedError, OSError) as e:
                    import socket as s
                    if not isinstance(e, s.timeout):
                        ok = True # Refused means the host is alive!
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
            time.sleep(0.01) # Tiny stagger

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
                    if not self._running: break
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
            except:
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


# ---------------------------------------------------------------------------
# Clickable Card Frame
# Allows toggling selected state to exclude from pings
# ---------------------------------------------------------------------------
class ClickableCard(QtWidgets.QFrame):
    toggled = QtCore.Signal(str, bool)

    def __init__(self, code, parent=None):
        super().__init__(parent)
        self.code = code
        self.active = True
        self.setObjectName("Card")
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.update_style()

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            self.active = not self.active
            self.update_style()
            self.toggled.emit(self.code, self.active)
        super().mousePressEvent(event)

    def update_style(self):
        if self.active:
            self.setStyleSheet(
                "QFrame#Card { border: 1.5px solid " + theme.ACCENT + "; background-color: rgba(56, 189, 248, 0.08); border-radius: 6px; }"
                "QFrame#Card:hover { background-color: rgba(56, 189, 248, 0.15); }"
                "QLabel { color: " + theme.TEXT + "; }"
            )
        else:
            self.setStyleSheet(
                "QFrame#Card { border: 1px solid #334155; background-color: rgba(15, 23, 42, 0.2); border-radius: 6px; }"
                "QFrame#Card:hover { border-color: #475569; background-color: rgba(15, 23, 42, 0.4); }"
                "QLabel { color: #64748b; }"
            )


# ---------------------------------------------------------------------------
# Page mixin
# ---------------------------------------------------------------------------

class ServerPageMixin:

    def _page_server(self):
        return self._build_server_page()

    def _build_server_page(self):
        page, root = self._page_container()
        
        hdr_lay = QtWidgets.QHBoxLayout()
        hdr = QtWidgets.QLabel("Server Diagnostics")
        hdr.setObjectName("H1")
        hdr_lay.addWidget(hdr)
        
        hdr_lay.addStretch(1)
        
        # Tabs for Server Sub-pages
        self._server_tabs = C.SegmentedControl(["Game Servers", "Live Game IP Scanner", "Network Trace"],
                                               current="Game Servers")
        self._server_tabs.currentChanged.connect(self._on_tab_changed)
        for opt_text, btn in self._server_tabs._btns.items():
            btn.clicked.connect(lambda _, t=opt_text: self.server_stack.setCurrentIndex({"Game Servers": 0, "Live Game IP Scanner": 1, "Network Trace": 2}.get(t, 0)))
        hdr_lay.addWidget(self._server_tabs)
        
        root.addLayout(hdr_lay)

        self._ping_worker   = None
        self._scan_worker   = None
        self._trace_worker  = None
        self._ping_best     = {}
        self._scan_captured = set()
        self._ping_cards    = {}
        
        self.server_stack = QtWidgets.QStackedWidget()
        root.addWidget(self.server_stack, 1)

        self.server_stack.addWidget(self._build_ping_panel())
        self.server_stack.addWidget(self._build_scanner_panel())
        self.server_stack.addWidget(self._build_trace_panel())

        self._load_saved_card_states()
        return page

    def _on_tab_changed(self, text):
        mapping = {"Game Servers": 0, "Live Game IP Scanner": 1, "Network Trace": 2}
        self.server_stack.setCurrentIndex(mapping.get(text, 0))

    # ===============================================================
    # TOP: Ping tester
    # ===============================================================
    def _build_ping_panel(self):
        w = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(10)

        top_bar = QtWidgets.QHBoxLayout()
        lbl = QtWidgets.QLabel("Region Ping Test (Dynamic IPs — Per Shard. Use IP Scanner then right-click to add IPs here)")
        lbl.setStyleSheet("font-size:13px;font-weight:700;color:" + theme.TEXT + ";")
        top_bar.addWidget(lbl)
        
        top_bar.addStretch(1)
        
        # Global Login card will be placed in the grid, but we need the toggle here
        self._view_toggle_btn = QtWidgets.QPushButton(chr(0x2692))
        self._view_toggle_btn.setObjectName("Outline")
        self._view_toggle_btn.setFixedSize(28, 28)
        self._view_toggle_btn.setStyleSheet("font-size: 14px; padding: 0px;")
        self._view_toggle_btn.setCheckable(True)
        self._view_toggle_btn.setToolTip("Toggle Normal/Test Servers")
        self._view_toggle_btn.toggled.connect(self._on_view_toggle)
        top_bar.addWidget(self._view_toggle_btn)
        
        self._reset_ips_btn = QtWidgets.QPushButton("Reset IPs")
        self._reset_ips_btn.setObjectName("Outline")
        self._reset_ips_btn.setFixedHeight(28)
        self._reset_ips_btn.clicked.connect(self._reset_to_default_ips)
        top_bar.addWidget(self._reset_ips_btn)

        self._ping_btn = QtWidgets.QPushButton("Test Selected")
        self._ping_btn.setObjectName("Accent")
        self._ping_btn.setFixedHeight(28)
        self._ping_btn.setMinimumWidth(110)
        self._ping_btn.clicked.connect(self._ping_run)
        top_bar.addWidget(self._ping_btn)
        v.addLayout(top_bar)

        # Load settings states before rendering cards
        saved_states = getattr(self.s, "server_pings", {})

        self._ping_grid_widget = QtWidgets.QWidget()
        self._ping_grid = QtWidgets.QGridLayout(self._ping_grid_widget)
        self._ping_grid.setContentsMargins(0, 0, 0, 0)
        self._ping_grid.setSpacing(6)

        # Pre-create ALL cards
        for region in REGIONS:
            card = self._build_ping_card(region)
            self._ping_cards[region["code"]] = card
            
            # Apply saved state if it exists in user settings
            if region["code"] in saved_states:
                card["widget"].active = saved_states[region["code"]]
                card["widget"].update_style()
        
        self._refresh_ping_grid()
        v.addWidget(self._ping_grid_widget)

        self._ping_table = QtWidgets.QTableWidget(0, 7)
        self._ping_table.setHorizontalHeaderLabels(
            ["#", "Location", "Country", "IP", "Port", "Status", "Latency"])
        hdr = self._ping_table.horizontalHeader()
        hdr.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(1, QtWidgets.QHeaderView.Interactive)
        hdr.setSectionResizeMode(2, QtWidgets.QHeaderView.Interactive)
        hdr.setSectionResizeMode(3, QtWidgets.QHeaderView.Stretch)
        hdr.setSectionResizeMode(4, QtWidgets.QHeaderView.Interactive)
        hdr.setSectionResizeMode(5, QtWidgets.QHeaderView.Interactive)
        hdr.setSectionResizeMode(6, QtWidgets.QHeaderView.Interactive)
        self._ping_table.setColumnWidth(1, 200) # Loc
        self._ping_table.setColumnWidth(2, 160) # Country
        self._ping_table.setColumnWidth(4, 80)  # Port
        self._ping_table.setColumnWidth(5, 200) # Status
        self._ping_table.setColumnWidth(6, 110) # Latency
        self._ping_table.verticalHeader().setVisible(False)
        self._ping_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self._ping_table.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        
        # Right-click copy IP functionality
        self._ping_table.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self._ping_table.customContextMenuRequested.connect(self._ping_table_menu)
        
        v.addWidget(self._ping_table, 1)

        self._ping_status = QtWidgets.QLabel("Click cards to toggle selection, then press Test Selected.")
        self._ping_status.setObjectName("Muted")
        self._ping_status.setWordWrap(True)
        v.addWidget(self._ping_status)
        return w

    def _on_view_toggle(self, checked):
        self._refresh_ping_grid()

    def _refresh_ping_grid(self):
        # Remove widgets from grid
        while self._ping_grid.count():
            item = self._ping_grid.takeAt(0)
            if item.widget():
                item.widget().hide()
        
        is_test_mode = self._view_toggle_btn.isChecked()

        # 1. Gather regions (Normal or Test)
        regions = []
        for region in REGIONS:
            code = region["code"]
            if "login" in code: continue
            if is_test_mode and "_test" in code:
                regions.append(region)
            elif not is_test_mode and "_test" not in code:
                regions.append(region)
        
        # 2. Inject Global Login at index 0 (Start of row)
        global_login = next((r for r in REGIONS if "login" in r["code"]), None)
        if global_login:
            regions.insert(0, global_login)
        
        # High col_max to ensure all cards fit on a single line
        col_max = 10
        for idx, region in enumerate(regions):
            card = self._ping_cards[region["code"]]
            self._ping_grid.addWidget(card["widget"], idx // col_max, idx % col_max)
            card["widget"].show()

    def _build_ping_card(self, region):
        card = ClickableCard(region["code"])
        card.setMinimumWidth(100)
        card.toggled.connect(self._save_card_states)
        
        vl = QtWidgets.QVBoxLayout(card)
        vl.setContentsMargins(10, 10, 10, 10)
        vl.setSpacing(4)

        flag_container = QtWidgets.QLabel()
        flag_container.setAlignment(QtCore.Qt.AlignCenter)
        flag_container.setMinimumHeight(24)
        
        # Try loading PNG, fallback to emoji
        iso = region.get("iso", "")
        _download_flag(iso)
        pm = _get_flag_pixmap(iso, 32)
        if pm:
            flag_container.setPixmap(pm)
        else:
            flag_container.setText(region["flag"])
            flag_container.setStyleSheet("font-size:24px;background:transparent;")
            
        vl.addWidget(flag_container)

        lbl_text = region["label"]
        name = QtWidgets.QLabel(lbl_text)
        name.setAlignment(QtCore.Qt.AlignCenter)
        name.setStyleSheet("font-size:12px;font-weight:700;color:" + theme.TEXT + ";background:transparent;")
        name.setWordWrap(True)
        vl.addWidget(name)

        status = QtWidgets.QLabel("--")
        status.setAlignment(QtCore.Qt.AlignCenter)
        status.setStyleSheet("font-size:10px;font-weight:bold;color:" + _STATUS_COLORS["pending"] + ";background:transparent;")
        vl.addWidget(status)

        return {"widget": card, "status_lbl": status}

    def _ping_table_menu(self, pos):
        item = self._ping_table.itemAt(pos)
        if not item: return
        row = item.row()
        ip_item = self._ping_table.item(row, 1)
        if not ip_item or not ip_item.text() or ip_item.text() == "--": return
        
        menu = QtWidgets.QMenu(self)
        menu.addAction(f"Diagnostic Trace", lambda: self._start_trace(ip_item.text(), "tracert"))
        menu.addSeparator()
        copy_action = menu.addAction(f"Copy IP ({ip_item.text()})")
        selected = menu.exec_(self._ping_table.viewport().mapToGlobal(pos))
        if selected == copy_action:
            QtWidgets.QApplication.clipboard().setText(ip_item.text())

    def _load_saved_card_states(self):
        # Apply saved custom hosts overrides from settings.json
        custom_hosts = getattr(self.s, "server_custom_hosts", {})
        for region in REGIONS:
            code = region["code"]
            if code in custom_hosts and custom_hosts[code]:
                region["hosts"] = custom_hosts[code]
                
        # Legacy file cleanup
        try:
            legacy_file = Path(config_dir()) / "server_pings.json"
            if legacy_file.exists():
                legacy_file.unlink(missing_ok=True)
        except Exception:
            pass

    def _save_card_states(self):
        try:
            states = {code: card["widget"].active for code, card in self._ping_cards.items()}
            self.s.server_pings = states
            self.s.save()
        except Exception:
            pass

    def _reset_to_default_ips(self):
        # Clear custom server hosts config override
        self.s.server_custom_hosts = {}
        self.s.save()
        
        # Hard reset regions hosts mapping to empty, except for Global Login
        for region in REGIONS:
            if region["code"] == "login_global":
                region["hosts"] = [
                    ("37.187.157.32", 60442),
                    ("51.83.66.84", 2003),
                    ("51.178.64.48", 60442)
                ]
            else:
                region["hosts"] = []
                    
        self._ping_status.setText("All server IPs have been reset (Login servers kept).")
        self._ping_status.setStyleSheet("")
        self._ping_table.setRowCount(0)

    def _ping_run(self):
        if self._ping_worker and self._ping_worker.isRunning():
            self._ping_worker.stop()
            self._ping_status.setText("Stopping test...")
            return
            
        # Only ping cards that are both visible in the current view and active (selected)
        active_codes = []
        for code, card in self._ping_cards.items():
            if card["widget"].isVisible() and card["widget"].active:
                active_codes.append(code)

        if not active_codes:
            self._ping_status.setText("Select at least one visible region card to ping.")
            return

        self._ping_table.setRowCount(0)
        self._ping_best = {}
        self._ping_btn.setText("Stop")

        # Count total hosts to be tested
        total_hosts = 0
        for r in REGIONS:
            if r["code"] in active_codes:
                total_hosts += len(r["hosts"])

        self._ping_status.setText(f"Probing {total_hosts} selected servers...")
        self._ping_status.setStyleSheet("color: " + _STATUS_COLORS["ok"] + "; font-weight: bold;")
        
        for code, card in self._ping_cards.items():
            if code in active_codes:
                card["status_lbl"].setText("...")
                card["status_lbl"].setStyleSheet(
                    "font-size:11px;font-weight:bold;color:" + _STATUS_COLORS["pending"] + ";background:transparent;")
            elif card["widget"].isVisible():
                card["status_lbl"].setText("")
        
        self._ping_worker = _PingWorker(REGIONS, active_codes)
        self._ping_worker.result.connect(self._ping_on_result)
        self._ping_worker.finished.connect(self._ping_on_finished)
        self._ping_worker.start()

    @QtCore.Slot(str, str, bool, float, str, str, str)
    def _ping_on_result(self, code, host_port, ok, ms, ip, country_name, country_code):
        # 1. Update best metrics and Card UI regardless of success (so card shows FAIL if all fail)
        prev_ok, prev_ms = self._ping_best.get(code, (False, float("inf")))
        if ok and (not prev_ok or ms < prev_ms):
            self._ping_best[code] = (True, ms)
        elif not ok and code not in self._ping_best:
            self._ping_best[code] = (False, ms)

        card = self._ping_cards.get(code)
        if card:
            best_ok, best_ms = self._ping_best.get(code, (False, 0.0))
            if best_ok:
                card["status_lbl"].setText(str(round(best_ms)) + " ms")
                card["status_lbl"].setStyleSheet(
                    "font-size:11px;font-weight:bold;color:" + _STATUS_COLORS["ok"] + ";background:transparent;")
            else:
                card["status_lbl"].setText("FAIL")
                card["status_lbl"].setStyleSheet(
                    "font-size:11px;font-weight:bold;color:" + _STATUS_COLORS["fail"] + ";background:transparent;")

        # 2. Only show successful connections in the results table
        if not ok:
            return

        row = self._ping_table.rowCount()
        self._ping_table.insertRow(row)

        def cell(txt, color=None, bold=False, icon_code=None):
            it = QtWidgets.QTableWidgetItem(txt)
            it.setTextAlignment(QtCore.Qt.AlignCenter)
            if color:
                it.setForeground(QtGui.QColor(color))
            if bold:
                f = it.font()
                f.setBold(True)
                it.setFont(f)
            if icon_code:
                pm = _get_flag_pixmap(icon_code, 16)
                if pm:
                    it.setIcon(QtGui.QIcon(pm))
            return it

        region = next((r for r in REGIONS if r["code"] == code), None)
        lbl = region["label"] if region else code
        
        parts = host_port.split(":")
        port_txt = parts[1] if len(parts) > 1 else ""

        self._ping_table.setItem(row, 0, cell(str(row + 1), _STATUS_COLORS["ok"], bold=True))
        self._ping_table.setItem(row, 1, cell(lbl))
        self._ping_table.setItem(row, 2, cell(country_name, icon_code=country_code))
        self._ping_table.setItem(row, 3, cell(ip if ip != "DNS failed" else "--"))
        self._ping_table.setItem(row, 4, cell(port_txt))

        sc, st = _STATUS_COLORS["ok"], "ESTABLISHED"
        self._ping_table.setItem(row, 5, cell(st, sc, bold=True))

        ms_txt = str(round(ms)) + " ms"
        ms_c = (_STATUS_COLORS["ok"] if ms < 150
                else _STATUS_COLORS["warn"] if ms < 350
                else _STATUS_COLORS["fail"])
        self._ping_table.setItem(row, 6, cell(ms_txt, ms_c))

    @QtCore.Slot()
    def _ping_on_finished(self):
        self._ping_btn.setEnabled(True)
        self._ping_btn.setText("Test Selected")
        self._ping_status.setStyleSheet("")
        
        if not self._ping_best:
            self._ping_status.setText("Test stopped or no results.")
            return

        ok_r   = [c for c, (ok, _) in self._ping_best.items() if ok]
        fail_r = [c for c, (ok, _) in self._ping_best.items() if not ok]
        ok_s   = ", ".join(r.upper() for r in ok_r) or "none"
        fail_s = ", ".join(r.upper() for r in fail_r) or "none"
        msg = "OK: " + ok_s + "  |  Unreachable: " + fail_s
        self._ping_status.setText(msg)

    # ===============================================================
    # RIGHT: Live Connection Scanner
    # ===============================================================
    def _build_scanner_panel(self):
        w = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(10)

        hrow = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("Live IP Scanner")
        title.setStyleSheet(
            "font-size:13px;font-weight:700;color:" + theme.TEXT + ";")
        hrow.addWidget(title)

        hrow.addStretch(1)

        self._scan_btn = QtWidgets.QPushButton("Start Scanning")
        self._scan_btn.setObjectName("Accent")
        self._scan_btn.setFixedHeight(28)
        self._scan_btn.clicked.connect(self._scan_toggle)
        hrow.addWidget(self._scan_btn)

        self._vpn_help_btn = QtWidgets.QPushButton("VPN Help")
        self._vpn_help_btn.setObjectName("Outline")
        self._vpn_help_btn.setFixedHeight(28)
        self._vpn_help_btn.setCheckable(True)
        self._vpn_help_btn.toggled.connect(self._vpn_help_toggle)
        hrow.addWidget(self._vpn_help_btn)

        btn_clear = QtWidgets.QPushButton("Clear")
        btn_clear.setObjectName("Outline")
        btn_clear.setFixedHeight(28)
        btn_clear.clicked.connect(self._scan_clear)
        hrow.addWidget(btn_clear)
        v.addLayout(hrow)

        self._vpn_help_box = QtWidgets.QFrame()
        self._vpn_help_box.setObjectName("Card")
        self._vpn_help_box.setStyleSheet("background-color: rgba(15, 23, 42, 0.3); border: 1px dashed #334155;")
        vhv = QtWidgets.QVBoxLayout(self._vpn_help_box)
        vhv.setContentsMargins(10, 10, 10, 10)
        
        help_text = (
            "<b style='color:#38bdf8;'>Split Tunneling – Manual IP Capture:<br/>"
            "<span style='font-size:12px; color:#94a3b8;'> Every world/shard is on its own ip"
            "<li>So you will need redo this for each world/shard.</li>"
            "</span>"
            "<ol style='font-size:12px; color:#94a3b8;'>"
            "<li>Click <b>Start Scanning</b>.</li>"
            "<li>Try to connect to the server you want to play on.</li>"
            "<li>You should see about <b>2–3 IPs</b>: account/login, game server.</li>"
            "<li>Copy these IPs into your VPN’s split-tunneling list.</li>"
            "</ol>"
            "<b style='color:#38bdf8;'>Full / Global VPN Fallback Scan:<br/>"
            "<span style='font-size:12px; color:#94a3b8;'>Use this if split tunneling won’t connect</span></b>"
            "<ol style='font-size:12px; color:#94a3b8;'>"
            "<li>Switch your VPN to <b>Full / Global Mode</b>.</li>"
            "<li>Click <b>Start Scanning</b>.</li>"
            "<li>Start <b>Farever.exe</b>.</li>"
            "<li>Connect into the server you want to join.</li>"
            "<li>Click <b>Copy IP List</b>.</li>"
            "<li>You should see all login and server IPs for that server.</li>"
            "<li>Add these IPs to your VPN’s split-tunneling list.</li>"
            "</ol>"
        )
        self._vpn_help_lbl = QtWidgets.QLabel(help_text)
        self._vpn_help_lbl.setWordWrap(True)
        self._vpn_help_lbl.setTextFormat(QtCore.Qt.RichText)
        vhv.addWidget(self._vpn_help_lbl)
        self._vpn_help_box.setVisible(False)
        v.addWidget(self._vpn_help_box)

        srow = QtWidgets.QHBoxLayout()
        self._scan_pid_lbl = QtWidgets.QLabel("Scanning")
        self._scan_pid_lbl.setObjectName("Mono")
        srow.addWidget(self._scan_pid_lbl)
        srow.addStretch(1)
        self._scan_status_lbl = QtWidgets.QLabel("")
        self._scan_status_lbl.setObjectName("Muted")
        srow.addWidget(self._scan_status_lbl)
        
        srow.addSpacing(10)
        self._scan_count_lbl = QtWidgets.QLabel("0 captured")
        self._scan_count_lbl.setObjectName("Muted")
        srow.addWidget(self._scan_count_lbl)
        v.addLayout(srow)

        self._scan_table = QtWidgets.QTableWidget(0, 8)
        self._scan_table.setHorizontalHeaderLabels(
            ["#", "Location", "Country", "IP", "Port", "Status", "Latency", "Hostname"])
        self._scan_table.setColumnHidden(7, True)
        hdr = self._scan_table.horizontalHeader()
        hdr.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(1, QtWidgets.QHeaderView.Interactive)
        hdr.setSectionResizeMode(2, QtWidgets.QHeaderView.Interactive)
        hdr.setSectionResizeMode(3, QtWidgets.QHeaderView.Stretch)
        hdr.setSectionResizeMode(4, QtWidgets.QHeaderView.Interactive)
        hdr.setSectionResizeMode(5, QtWidgets.QHeaderView.Interactive)
        hdr.setSectionResizeMode(6, QtWidgets.QHeaderView.Interactive)
        self._scan_table.setColumnWidth(1, 200) # Loc
        self._scan_table.setColumnWidth(2, 160) # Country
        self._scan_table.setColumnWidth(4, 80)  # Port
        self._scan_table.setColumnWidth(5, 200) # Status
        self._scan_table.setColumnWidth(6, 110) # Latency
        self._scan_table.verticalHeader().setVisible(False)
        self._scan_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self._scan_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self._scan_table.setAlternatingRowColors(True)
        
        # Right-click copy IP context menu for scan table
        self._scan_table.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self._scan_table.customContextMenuRequested.connect(self._scan_table_menu)
        
        v.addWidget(self._scan_table, 1)

        bot = QtWidgets.QHBoxLayout()
        bot.addStretch(1)

        btn_copy = QtWidgets.QPushButton("Full Info")
        btn_copy.setObjectName("Outline")
        btn_copy.setFixedHeight(28)
        btn_copy.clicked.connect(self._scan_copy_ips)
        bot.addWidget(btn_copy)

        btn_copy_list = QtWidgets.QPushButton("IP List")
        btn_copy_list.setObjectName("Outline")
        btn_copy_list.setFixedHeight(28)
        btn_copy_list.setToolTip("Copy just the unique IP addresses (ideal for VPN split-tunneling lists)")
        btn_copy_list.clicked.connect(self._scan_copy_unique_ips)
        bot.addWidget(btn_copy_list)

        btn_vpn = QtWidgets.QPushButton("VPN List")
        btn_vpn.setObjectName("Outline")
        btn_vpn.setFixedHeight(28)
        btn_vpn.setToolTip("Copy VPN list for a specific region + Login Gateways")
        btn_vpn.clicked.connect(self._scan_copy_vpn_menu)
        bot.addWidget(btn_vpn)
        
        v.addLayout(bot)

        return w

    def _scan_table_menu(self, pos):
        item = self._scan_table.itemAt(pos)
        if not item: return
        row = item.row()
        ip_item = self._scan_table.item(row, 3)
        port_item = self._scan_table.item(row, 4)
        if not ip_item or not ip_item.text() or not port_item: return
        ip = ip_item.text()
        port = port_item.text()
        
        menu = QtWidgets.QMenu(self)
        menu.addAction(f"Diagnostic Trace", lambda: self._start_trace(ip, "tracert"))
        menu.addSeparator()
        copy_action = menu.addAction(f"Copy IP ({ip})")
        
        # Build "Add to Region" submenus dynamically
        add_menu = menu.addMenu("Add IP to Region")
        
        # 1. Main Game Servers
        for region in REGIONS:
            if "_test" in region["code"] or "login" in region["code"]: continue
            act = add_menu.addAction(region["label"])
            act.triggered.connect(lambda checked=False, r_code=region["code"], r_ip=ip, r_port=port: self._manual_add_ip(r_code, r_ip, r_port))
        
        add_menu.addSeparator()
        
        # 2. Test Servers
        for region in REGIONS:
            if "_test" not in region["code"]: continue
            act = add_menu.addAction(region["label"])
            act.triggered.connect(lambda checked=False, r_code=region["code"], r_ip=ip, r_port=port: self._manual_add_ip(r_code, r_ip, r_port))
            
        add_menu.addSeparator()
        
        # 3. Login Gateways
        for region in REGIONS:
            if "login" not in region["code"]: continue
            act = add_menu.addAction(region["label"])
            act.triggered.connect(lambda checked=False, r_code=region["code"], r_ip=ip, r_port=port: self._manual_add_ip(r_code, r_ip, r_port))
            
        selected = menu.exec_(self._scan_table.viewport().mapToGlobal(pos))
        if selected == copy_action:
            QtWidgets.QApplication.clipboard().setText(ip)

    def _manual_add_ip(self, code, ip, port_str):
        try:
            port = int(port_str)
        except ValueError:
            return
            
        # Update custom hosts configuration
        custom_hosts = getattr(self.s, "server_custom_hosts", {})
        
        # Update active in-memory table and get new list
        new_hosts = []
        region_label = ""
        for region in REGIONS:
            if region["code"] == code:
                region_label = region["label"]
                # Add to existing hosts if not duplicate
                new_entry = (ip, port)
                if new_entry not in region["hosts"]:
                    region["hosts"].append(new_entry)
                new_hosts = region["hosts"]
                break
        
        # Save updates to settings.json
        custom_hosts[code] = new_hosts
        self.s.server_custom_hosts = custom_hosts
        self.s.save()
        
        # Update UI table immediately to show the new location
        for row in range(self._scan_table.rowCount()):
            ip_item = self._scan_table.item(row, 3)
            if ip_item and ip_item.text() == ip:
                loc_item = self._scan_table.item(row, 1)
                if loc_item:
                    loc_item.setText(region_label)

        self._scan_status_lbl.setText(f"Added {ip}:{port} to {code.upper()} targets.")
        self._ping_status.setText(f"{code.upper()} targets updated. Ready to re-test.")

    def _vpn_help_toggle(self, checked):
        self._vpn_help_box.setVisible(checked)

    def _scan_toggle(self):
        if self._scan_worker and self._scan_worker.isRunning():
            self._scan_stop()
        else:
            self._scan_start()

    def _scan_start(self):
        # Ensure any old worker is properly cleaned up
        if self._scan_worker:
            if self._scan_worker.isRunning():
                self._scan_worker.stop()
                self._scan_worker.wait()
            
        self._scan_captured = set()
        self._scan_count_lbl.setText("0 captured")
        self._scan_worker = _ConnectionScanWorker()
        self._scan_worker.new_conn.connect(self._scan_on_new_conn)
        self._scan_worker.status.connect(self._scan_on_status)
        self._scan_worker.pid_found.connect(self._scan_on_pid_found)
        self._scan_worker.pid_lost.connect(self._scan_on_pid_lost)
        self._scan_worker.start()
        
        # Auto-stop after 5 minutes
        QtCore.QTimer.singleShot(300000, self._scan_stop_if_running)
        
        self._scan_btn.setText("Stop Scanning")
        self._scan_btn.setObjectName("Outline")
        self._scan_btn.style().unpolish(self._scan_btn)
        self._scan_btn.style().polish(self._scan_btn)
        
        self._scan_pid_lbl.setText("Waiting for Farever.exe")
        self._scan_status_lbl.setText("Scanner started")

    def _scan_stop(self):
        if self._scan_worker:
            self._scan_worker.stop()
            self._scan_worker.wait()
            self._scan_worker = None
            
        self._scan_btn.setText("Start Scanning")
        self._scan_btn.setObjectName("Accent")
        self._scan_btn.style().unpolish(self._scan_btn)
        self._scan_btn.style().polish(self._scan_btn)
        
        self._scan_pid_lbl.setText("Stopped")
        self._scan_status_lbl.setText("Scanner stopped.")

    def _scan_stop_if_running(self):
        if self._scan_worker and self._scan_worker.isRunning():
            self._scan_stop()
            self._scan_status_lbl.setText("5min auto-stopped")

    def _scan_clear(self):
        self._scan_table.setRowCount(0)
        self._scan_captured = set()
        self._scan_count_lbl.setText("0 captured")
        # Do not stop the worker thread here. The scanning button status remains unaffected.

    def _scan_copy_ips(self):
        lines = []
        for row in range(self._scan_table.rowCount()):
            loc     = self._scan_table.item(row, 1)
            country = self._scan_table.item(row, 2)
            ip      = self._scan_table.item(row, 3)
            port    = self._scan_table.item(row, 4)
            state   = self._scan_table.item(row, 5)
            ping    = self._scan_table.item(row, 6)
            host    = self._scan_table.item(row, 7)
            if ip and port:
                ip_txt = ip.text()
                port_val = port.text()
                loc_txt = loc.text() if loc else "Unassigned"
                country_txt = country.text() if country else ""
                host_txt = host.text() if host else ""
                state_txt = state.text() if state else "UNKNOWN"
                ping_txt = ping.text() if ping else "--"
                
                suffix = f"  # {loc_txt} | {country_txt} | State: {state_txt} | Latency: {ping_txt} | Host: {host_txt}"
                lines.append(ip_txt + ":" + port_val + suffix)
        if lines:
            QtWidgets.QApplication.clipboard().setText("\n".join(lines))
            self._scan_status_lbl.setText(
                "Copied " + str(len(lines)) + " full IP Servers")
        else:
            self._scan_status_lbl.setText("Nothing captured yet.")

    def _scan_copy_unique_ips(self):
        ips = set()
        for row in range(self._scan_table.rowCount()):
            ip_item = self._scan_table.item(row, 3)
            if ip_item and ip_item.text():
                ips.add(ip_item.text())
        
        if ips:
            sorted_ips = sorted(ips)
            QtWidgets.QApplication.clipboard().setText("\n".join(sorted_ips))
            self._scan_status_lbl.setText(
                f"Copied {len(sorted_ips)} unique IPs")
        else:
            self._scan_status_lbl.setText("Nothing captured yet.")

    def _scan_copy_vpn_menu(self):
        menu = QtWidgets.QMenu(self)
        
        # Main Regions
        for r in REGIONS:
            if "login" in r["code"]: continue
            act = menu.addAction(r["label"])
            act.triggered.connect(lambda checked=False, c=r["code"]: self._copy_vpn_for_regions([c]))
            
        menu.exec_(QtGui.QCursor.pos())

    def _copy_vpn_for_regions(self, codes):
        # Always include global login gateways
        target_codes = set(codes)
        target_codes.add("login_global")
        
        ips = set()
        for r in REGIONS:
            if r["code"] in target_codes:
                for host, _ in r["hosts"]:
                    ips.add(host)
        
        if ips:
            sorted_ips = sorted(ips)
            QtWidgets.QApplication.clipboard().setText("\n".join(sorted_ips))
            self._scan_status_lbl.setText(f"Copied {len(sorted_ips)} Regional IPs.")
        else:
            self._scan_status_lbl.setText("No IPs found for those regions.")

    @QtCore.Slot(str, int, str, float, str, str, str)
    def _scan_on_new_conn(self, remote_ip, port, hostname, ping_ms, state, country_name, country_code):
        key = remote_ip + ":" + str(port)
        if key in self._scan_captured:
            return
        self._scan_captured.add(key)

        row = self._scan_table.rowCount()
        self._scan_table.insertRow(row)

        def cell(txt, color=None, bold=False, icon_code=None):
            it = QtWidgets.QTableWidgetItem(str(txt))
            it.setTextAlignment(QtCore.Qt.AlignCenter)
            if color:
                it.setForeground(QtGui.QColor(color))
            if bold:
                f = it.font()
                f.setBold(True)
                it.setFont(f)
            if icon_code:
                pm = _get_flag_pixmap(icon_code, 16)
                if pm:
                    it.setIcon(QtGui.QIcon(pm))
            return it

        self._scan_table.setItem(row, 0, cell(str(row + 1), theme.ACCENT, bold=True))
        
        # Determine Location Label
        loc_label = "Unassigned"
        for r in REGIONS:
            found = False
            for h, _ in r.get("hosts", []):
                if h == remote_ip:
                    found = True
                    break
            if found:
                loc_label = r["label"]
                break
            
        self._scan_table.setItem(row, 1, cell(loc_label))

        # Country
        self._scan_table.setItem(row, 2, cell(country_name, icon_code=country_code))

        # IP
        self._scan_table.setItem(row, 3, cell(remote_ip))

        # Port
        port_color = (_STATUS_COLORS["ok"]
                      if port in (443, 7777, 7778, 80) else None)
        self._scan_table.setItem(row, 4, cell(str(port), port_color))

        # Status (State)
        state_color = (_STATUS_COLORS["ok"]   if state == "ESTABLISHED"
                       else _STATUS_COLORS["warn"] if state == "SYN_SENT"
                       else _STATUS_COLORS["pending"])
        self._scan_table.setItem(row, 5, cell(state, state_color))

        # Ping column
        ms_txt = f"{round(ping_ms)} ms" if ping_ms >= 0 else "--"
        ms_color = (_STATUS_COLORS["ok"] if ping_ms >= 0 and ping_ms < 150
                    else _STATUS_COLORS["warn"] if ping_ms >= 0 and ping_ms < 350
                    else _STATUS_COLORS["fail"] if ping_ms >= 0
                    else _STATUS_COLORS["pending"])
        self._scan_table.setItem(row, 6, cell(ms_txt, ms_color, bold=(ping_ms >= 0)))

        # Hostname (hidden column)
        self._scan_table.setItem(row, 7, cell(hostname))

        self._scan_table.scrollToBottom()
        self._scan_count_lbl.setText(str(len(self._scan_captured)) + " captured")

    # ===============================================================
    # Integrated Diagnostic Log
    # ===============================================================
    def _build_trace_panel(self):
        w = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(10)

        hrow = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("Diagnostic Tools")
        title.setStyleSheet(
            "font-size:13px;font-weight:700;color:" + theme.TEXT + ";")
        hrow.addWidget(title)
        
        hrow.addStretch(1)

        self._manual_ip_input = QtWidgets.QLineEdit()
        self._manual_ip_input.setPlaceholderText("Paste IP here...")
        self._manual_ip_input.setFixedHeight(28)
        self._manual_ip_input.setMinimumWidth(150)
        self._manual_ip_input.returnPressed.connect(lambda: self._start_trace(self._manual_ip_input.text(), "tracert"))
        hrow.addWidget(self._manual_ip_input)

        self._trace_btn = QtWidgets.QPushButton("Trace")
        self._trace_btn.setObjectName("Accent")
        self._trace_btn.setFixedHeight(28)
        self._trace_btn.setToolTip("Full Trace (with Hostnames)")
        self._trace_btn.clicked.connect(lambda: self._start_trace(self._manual_ip_input.text(), "tracert"))
        hrow.addWidget(self._trace_btn)

        self._trace_fast_btn = QtWidgets.QPushButton("Fast")
        self._trace_fast_btn.setObjectName("Outline")
        self._trace_fast_btn.setFixedHeight(28)
        self._trace_fast_btn.setToolTip("Fast Trace (No Hostnames)")
        self._trace_fast_btn.clicked.connect(lambda: self._start_trace(self._manual_ip_input.text(), "tracert_fast"))
        hrow.addWidget(self._trace_fast_btn)

        self._trace_geo_btn = QtWidgets.QPushButton("Geo")
        self._trace_geo_btn.setObjectName("Accent")
        self._trace_geo_btn.setFixedHeight(28)
        self._trace_geo_btn.setToolTip("Fast Trace + Country Lookup")
        self._trace_geo_btn.clicked.connect(lambda: self._start_trace(self._manual_ip_input.text(), "tracert_geo"))
        hrow.addWidget(self._trace_geo_btn)

        self._net_info_btn = QtWidgets.QPushButton("Net Info")
        self._net_info_btn.setObjectName("Outline")
        self._net_info_btn.setFixedHeight(28)
        self._net_info_btn.setToolTip("Flush DNS & Show IP Info")
        self._net_info_btn.clicked.connect(lambda: self._start_trace("", "net_info"))
        hrow.addWidget(self._net_info_btn)

        self._trace_stop_btn = QtWidgets.QPushButton("Stop")
        self._trace_stop_btn.setObjectName("Outline")
        self._trace_stop_btn.setFixedHeight(28)
        self._trace_stop_btn.clicked.connect(self._stop_trace)
        self._trace_stop_btn.setEnabled(False)
        hrow.addWidget(self._trace_stop_btn)

        btn_clear = QtWidgets.QPushButton("Clear")
        btn_clear.setObjectName("Outline")
        btn_clear.setFixedHeight(28)
        btn_clear.clicked.connect(lambda: self._trace_log.clear())
        hrow.addWidget(btn_clear)
        v.addLayout(hrow)
        
        self._trace_target_lbl = QtWidgets.QLabel("Select a tool or paste an IP to begin.")
        self._trace_target_lbl.setObjectName("Mono")
        v.addWidget(self._trace_target_lbl)

        self._trace_log = QtWidgets.QPlainTextEdit()
        self._trace_log.setReadOnly(True)
        self._trace_log.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
        self._trace_log.setStyleSheet(f"""
            QPlainTextEdit {{
                background-color: {theme.BG};
                color: {theme.TEXT};
                font-family: "{theme.MONO_FONT}", "Consolas", monospace;
                font-size: 12px;
                border: 1px solid {theme.BORDER};
                padding: 10px;
            }}
        """)
        v.addWidget(self._trace_log, 1)
        return w

    def _start_trace(self, ip: str, mode: str = "tracert"):
        # Stop any existing worker before starting a new one
        if self._trace_worker and self._trace_worker.isRunning():
            self._trace_worker.stop()
            self._trace_worker.wait(1000) # Give it a second to die

        # Jump to Trace tab
        self._server_tabs.setCurrentText("Network Trace")
        self.server_stack.setCurrentIndex(2)
        
        label_text = f"{mode.upper()}"
        if ip: label_text += f": {ip}"
        self._trace_target_lbl.setText(label_text)
        
        # Use append with clear line breaks to prevent jumbled logs
        self._trace_log.appendPlainText(f"\n[ --- Starting {mode} {ip if ip else ''} --- ]")
        self._trace_stop_btn.setEnabled(True)

        self._trace_worker = _TraceWorker(ip, mode)
        self._trace_worker.output_ready.connect(self._on_trace_output)
        self._trace_worker.finished.connect(self._on_trace_finished)
        self._trace_worker.start()

    def _stop_trace(self):
        if self._trace_worker and self._trace_worker.isRunning():
            self._trace_worker.stop()
            self._trace_worker.wait(500)
            self._trace_log.appendPlainText("\n[Diagnostic Stopped by User]")
        self._trace_stop_btn.setEnabled(False)

    @QtCore.Slot(str)
    def _on_trace_output(self, text):
        # Use appendPlainText for thread-safe sequential logging
        self._trace_log.appendPlainText(text.strip())

    @QtCore.Slot()
    def _on_trace_finished(self):
        self._trace_log.appendPlainText("\n[Diagnostic Complete]")
        self._trace_stop_btn.setEnabled(False)

    def _scan_on_status(self, msg):
        self._scan_status_lbl.setText(msg)

    @QtCore.Slot(int)
    def _scan_on_pid_found(self, pid):
        self._scan_pid_lbl.setText("Farever.exe  PID " + str(pid))

    @QtCore.Slot()
    def _scan_on_pid_lost(self):
        self._scan_pid_lbl.setText("Farever.exe not running")

    def _server_cleanup(self):
        """Force-stop all background diagnostic workers to prevent shutdown crashes."""
        workers = []
        if hasattr(self, "_ping_worker") and self._ping_worker:
            workers.append(self._ping_worker)
        if hasattr(self, "_scan_worker") and self._scan_worker:
            workers.append(self._scan_worker)
        if hasattr(self, "_trace_worker") and self._trace_worker:
            workers.append(self._trace_worker)

        for w in workers:
            if w.isRunning():
                w.stop()
                # Wait up to 2s for graceful exit
                if not w.wait(2000):
                    w.terminate()
                    w.wait()

        # Only clear references after we are sure threads are joined
        self._ping_worker = None
        self._scan_worker = None
        self._trace_worker = None
