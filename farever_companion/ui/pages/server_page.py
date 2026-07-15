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
        "icon": "broadcast",
        "hosts": [
            ("148.113.226.212",    6677),
            ("148.113.228.4",      6389),
            ("37.187.157.32",      60442),
        ],
    },
    {
        "code": "eu",
        "label": "Europe",
        "flag": "🇪🇺",
        "icon": "broadcast",
        "hosts": [
            ("135.125.19.76",      6031),
        ],
    },
    {
        "code": "as",
        "label": "Asia",
        "flag": "🇸🇬",
        "icon": "broadcast",
        "hosts": [
            ("15.235.228.20",       7100),
            ("15.235.228.20",       6768),
        ],
    },
    {
        "code": "sa",
        "label": "South America",
        "flag": "🇧🇷",
        "icon": "broadcast",
        "hosts": [],
    },
    {
        "code": "cn",
        "label": "China",
        "flag": "🇨🇳",
        "icon": "broadcast",
        "hosts": [
            ("43.135.219.50",      6459),
            ("49.233.72.163",      60442),
            ("192.144.185.5",      9961),
        ],
    },
    # --- Test Servers ---
    {
        "code": "na_test",
        "label": "NA Test",
        "flag": "⚒️ 🇺🇸",
        "icon": "broadcast",
        "hosts": [
            ("148.113.187.116",    6604),
            ("148.113.226.212",    6777),
        ],
    },
    {
        "code": "eu_test",
        "label": "EU Test",
        "flag": "⚒️ 🇪🇺",
        "icon": "broadcast",
        "hosts": [
            ("135.125.19.80",      6244),
        ],
    },
    {
        "code": "as_test",
        "label": "Asia Test",
        "flag": "⚒️ 🇸🇬",
        "icon": "broadcast",
        "hosts": [
            ("15.235.228.20",       7036),
            ("15.235.228.20",       7012),
        ],
    },
    {
        "code": "sa_test",
        "label": "SA Test",
        "flag": "⚒️ 🇧🇷",
        "icon": "broadcast",
        "hosts": [],
    },
    {
        "code": "cn_test",
        "label": "China Test",
        "flag": "⚒️ 🇨🇳",
        "icon": "broadcast",
        "hosts": [
            ("43.135.219.50",      6443),
            ("49.233.72.163",      60442),
            ("192.144.185.5",      9912),
        ],
    },
    # --- Login Gateways ---
    {
        "code": "login_global",
        "label": "Global Login",
        "flag": "🌐",
        "icon": "terminal",
        "hosts": [
            ("37.187.157.32",      60442),
            ("51.83.66.84",        2003),
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


# ---------------------------------------------------------------------------
# Background workers
# ---------------------------------------------------------------------------

class _PingWorker(QtCore.QThread):
    result   = QtCore.Signal(str, str, bool, float, str)
    finished = QtCore.Signal()

    def __init__(self, regions, active_codes):
        super().__init__()
        self._regions = regions
        self._active_codes = active_codes
        self._running = True

    def stop(self):
        self._running = False

    def run(self):
        for r in self._regions:
            if not self._running:
                break
            if r["code"] not in self._active_codes:
                continue
            for host, port in r["hosts"]:
                if not self._running:
                    break
                try:
                    ip = socket.gethostbyname(host)
                except OSError:
                    self.result.emit(r["code"], host + ":" + str(port), False, 0.0, "DNS failed")
                    continue
                start = time.perf_counter()
                try:
                    with socket.create_connection((host, port), timeout=2.5):
                        pass
                    ok = True
                except (socket.timeout, ConnectionRefusedError, OSError):
                    ok = False
                ms = (time.perf_counter() - start) * 1000
                self.result.emit(r["code"], host + ":" + str(port), ok, ms, ip)
                time.sleep(0.05)
        self.finished.emit()


class _ConnectionScanWorker(QtCore.QThread):
    new_conn  = QtCore.Signal(str, int, str, float, str)
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
        start = time.perf_counter()
        try:
            with socket.create_connection((ip, port), timeout=1.5):
                pass
            ms = (time.perf_counter() - start) * 1000
        except Exception:
            ms = -1.0
        self.new_conn.emit(ip, port, hostname, ms, state)


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
        # Modes:
        # tracert: standard with hostnames
        # tracert_fast: tracert -d (no hostnames)
        # net_info: ipconfig /flushdns + basic info
        if self.mode == "tracert":
            cmd = ["tracert", self.ip]
        elif self.mode == "tracert_fast":
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
        self._server_tabs = C.SegmentedControl(["Game Servers", "Live Game IP Scanner", "Network Tracet"],
                                               current="Game Servers")
        self._server_tabs.currentChanged.connect(self._on_tab_changed)
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

        self._build_integrated_server_page(self.server_stack)
        
        return page

    def _on_tab_changed(self, text):
        mapping = {"Game Servers": 0, "Live Game IP Scanner": 1, "Network Tracet": 2}
        self.server_stack.setCurrentIndex(mapping.get(text, 0))

    def _build_integrated_server_page(self, stack):
        self._ping_worker   = None
        self._scan_worker   = None
        self._trace_worker  = None
        self._ping_best     = {}
        self._scan_captured = set()
        self._ping_cards    = {}

        stack.addWidget(self._build_ping_panel())
        stack.addWidget(self._build_scanner_panel())
        stack.addWidget(self._build_trace_panel())

        self._load_saved_card_states()

    # ===============================================================
    # TOP: Ping tester
    # ===============================================================
    def _build_ping_panel(self):
        w = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(10)

        top_bar = QtWidgets.QHBoxLayout()
        lbl = QtWidgets.QLabel("Region Ping Test (Dynamic IPs — May be outdated)")
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

        self._ping_table = QtWidgets.QTableWidget(0, 5)
        self._ping_table.setHorizontalHeaderLabels(
            ["Location", "IP", "Port", "Status", "Latency"])
        hdr = self._ping_table.horizontalHeader()
        hdr.setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        hdr.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(4, QtWidgets.QHeaderView.ResizeToContents)
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

        flag = QtWidgets.QLabel(region["flag"])
        flag.setStyleSheet("font-size:24px;background:transparent;")
        flag.setAlignment(QtCore.Qt.AlignCenter)
        vl.addWidget(flag)

        lbl_text = region["label"]
        if "Login" in lbl_text:
            lbl_text = lbl_text.replace("Login", "Log")
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
        
        # Hard reset regions hosts mapping dynamically to defaults
        default_regions = [
            ("na", [("148.113.226.212", 6677), ("148.113.228.4", 6389), ("37.187.157.32", 60442)]),
            ("eu", [("135.125.19.76", 6031)]),
            ("as", [("15.235.228.20", 7100), ("15.235.228.20", 6768)]),
            ("sa", []),
            ("cn", [("43.135.219.50", 6459), ("49.233.72.163", 60442), ("192.144.185.5", 9961)]),
            ("na_test", [("148.113.187.116", 6604), ("148.113.226.212", 6777)]),
            ("eu_test", [("135.125.19.80", 6244)]),
            ("as_test", [("15.235.228.20", 7036), ("15.235.228.20", 7012)]),
            ("sa_test", []),
            ("cn_test", [("43.135.219.50", 6443), ("49.233.72.163", 60442), ("192.144.185.5", 9912)]),
            ("login_global", [("37.187.157.32", 60442), ("51.83.66.84", 2003)]),
        ]
        for code, hosts in default_regions:
            for region in REGIONS:
                if region["code"] == code:
                    region["hosts"] = hosts
                    
        self._ping_status.setText("All server IPs have been reset to factory defaults.")
        self._ping_table.setRowCount(0)

    def _ping_run(self):
        if self._ping_worker and self._ping_worker.isRunning():
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
        self._ping_btn.setEnabled(False)
        self._ping_btn.setText("Testing...")
        self._ping_status.setText("Probing selected servers...")
        
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

    @QtCore.Slot(str, str, bool, float, str)
    def _ping_on_result(self, code, host_port, ok, ms, ip):
        row = self._ping_table.rowCount()
        self._ping_table.insertRow(row)

        def cell(txt, color=None, bold=False):
            it = QtWidgets.QTableWidgetItem(txt)
            it.setTextAlignment(QtCore.Qt.AlignCenter)
            if color:
                it.setForeground(QtGui.QColor(color))
            if bold:
                f = it.font()
                f.setBold(True)
                it.setFont(f)
            return it

        region = next((r for r in REGIONS if r["code"] == code), None)
        lbl = region["label"] if region else code
        if "Login" in lbl:
            lbl = lbl.replace("Login", "").strip()
        
        parts = host_port.split(":")
        port_txt = parts[1] if len(parts) > 1 else ""

        self._ping_table.setItem(row, 0, cell(lbl))
        self._ping_table.setItem(row, 1, cell(ip if ip != "DNS failed" else "--"))
        self._ping_table.setItem(row, 2, cell(port_txt))

        if ip == "DNS failed":
            sc, st = _STATUS_COLORS["dns_fail"], "DNS FAIL"
        elif ok:
            sc, st = _STATUS_COLORS["ok"], "OK"
        else:
            sc, st = _STATUS_COLORS["fail"], "TIMEOUT"
        self._ping_table.setItem(row, 3, cell(st, sc, bold=True))

        ms_txt = str(round(ms)) + " ms" if ms > 0 else "--"
        ms_c = (_STATUS_COLORS["ok"] if ok and ms < 150
                else _STATUS_COLORS["warn"] if ok and ms < 350
                else _STATUS_COLORS["fail"] if ok
                else _STATUS_COLORS["pending"])
        self._ping_table.setItem(row, 4, cell(ms_txt, ms_c))

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

    @QtCore.Slot()
    def _ping_on_finished(self):
        self._ping_btn.setEnabled(True)
        self._ping_btn.setText("Test Selected")
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

        self._scan_table = QtWidgets.QTableWidget(0, 6)
        self._scan_table.setHorizontalHeaderLabels(
            ["#", "Remote IP", "Port", "Hostname", "State", "Ping"])
        self._scan_table.setColumnHidden(3, True)
        hdr = self._scan_table.horizontalHeader()
        hdr.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(4, QtWidgets.QHeaderView.Stretch)
        hdr.setSectionResizeMode(5, QtWidgets.QHeaderView.ResizeToContents)
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
        ip_item = self._scan_table.item(row, 1)
        port_item = self._scan_table.item(row, 2)
        if not ip_item or not ip_item.text() or not port_item: return
        ip = ip_item.text()
        port = port_item.text()
        
        menu = QtWidgets.QMenu(self)
        menu.addAction(f"Diagnostic Trace", lambda: self._start_trace(ip, "tracert"))
        menu.addSeparator()
        copy_action = menu.addAction(f"Copy IP ({ip})")
        
        # Build "Add to Region" submenus dynamically
        add_menu = menu.addMenu("Add IP to Region")
        for region in REGIONS:
            act = add_menu.addAction(region["label"])
            # Bind parameters inside lambda closure
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
        for region in REGIONS:
            if region["code"] == code:
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
            ip    = self._scan_table.item(row, 1)
            port  = self._scan_table.item(row, 2)
            host  = self._scan_table.item(row, 3)
            state = self._scan_table.item(row, 4)
            ping  = self._scan_table.item(row, 5)
            if ip and port:
                ip_txt = ip.text()
                port_val = port.text()
                host_txt = host.text() if host else ""
                state_txt = state.text() if state else "UNKNOWN"
                ping_txt = ping.text() if ping else "--"
                
                # Classify type
                t_label = "Game Server"
                if port_val == "60442" or port_val == "2003":
                    t_label = "Login Gateway"
                
                suffix = f"  # {t_label} | State: {state_txt} | Ping: {ping_txt} | Host: {host_txt}"
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
            ip_item = self._scan_table.item(row, 1)
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

    @QtCore.Slot(str, int, str, float, str)
    def _scan_on_new_conn(self, remote_ip, port, hostname, ping_ms, state):
        key = remote_ip + ":" + str(port)
        if key in self._scan_captured:
            return
        self._scan_captured.add(key)

        row = self._scan_table.rowCount()
        self._scan_table.insertRow(row)

        def cell(txt, color=None, bold=False):
            it = QtWidgets.QTableWidgetItem(txt)
            it.setTextAlignment(QtCore.Qt.AlignCenter)
            if color:
                it.setForeground(QtGui.QColor(color))
            if bold:
                f = it.font()
                f.setBold(True)
                it.setFont(f)
            return it

        self._scan_table.setItem(row, 0, cell(str(row + 1), theme.ACCENT, bold=True))
        self._scan_table.setItem(row, 1, cell(remote_ip))
        port_color = (_STATUS_COLORS["ok"]
                      if port in (443, 7777, 7778, 80) else None)
        self._scan_table.setItem(row, 2, cell(str(port), port_color))

        # Hostname (hidden column)
        self._scan_table.setItem(row, 3, cell(hostname))

        state_color = (_STATUS_COLORS["ok"]   if state == "ESTABLISHED"
                       else _STATUS_COLORS["warn"] if state == "SYN_SENT"
                       else _STATUS_COLORS["pending"])
        self._scan_table.setItem(row, 4, cell(state, state_color))

        # Ping column
        ms_txt = f"{round(ping_ms)} ms" if ping_ms >= 0 else "--"
        ms_color = (_STATUS_COLORS["ok"] if ping_ms >= 0 and ping_ms < 150
                    else _STATUS_COLORS["warn"] if ping_ms >= 0 and ping_ms < 350
                    else _STATUS_COLORS["fail"] if ping_ms >= 0
                    else _STATUS_COLORS["pending"])
        self._scan_table.setItem(row, 5, cell(ms_txt, ms_color, bold=(ping_ms >= 0)))

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
        if self._trace_worker and self._trace_worker.isRunning():
            self._trace_worker.stop()
            self._trace_worker.wait()

        # Jump to Trace tab
        self._server_tabs.setCurrentText("Network Tracet")
        self._on_tab_changed("Network Tracet")
        
        label_text = f"{mode.upper()}"
        if ip: label_text += f": {ip}"
        self._trace_target_lbl.setText(label_text)
        
        self._trace_log.appendPlainText(f"\n> Starting {mode} {ip if ip else ''}...\n")
        self._trace_stop_btn.setEnabled(True)

        self._trace_worker = _TraceWorker(ip, mode)
        self._trace_worker.output_ready.connect(self._on_trace_output)
        self._trace_worker.finished.connect(self._on_trace_finished)
        self._trace_worker.start()

    def _stop_trace(self):
        if self._trace_worker and self._trace_worker.isRunning():
            self._trace_worker.stop()
            self._trace_worker.wait()
            self._trace_log.appendPlainText("\n[Diagnostic Stopped by User]")
        self._trace_stop_btn.setEnabled(False)

    @QtCore.Slot(str)
    def _on_trace_output(self, text):
        self._trace_log.insertPlainText(text)
        sb = self._trace_log.verticalScrollBar()
        sb.setValue(sb.maximum())

    @QtCore.Slot()
    def _on_trace_finished(self):
        self._trace_log.appendPlainText("\n[Diagnostic Complete]")
        self._trace_stop_btn.setEnabled(False)

    @QtCore.Slot(str)
    def _scan_on_status(self, msg):
        self._scan_status_lbl.setText(msg)

    @QtCore.Slot(int)
    def _scan_on_pid_found(self, pid):
        self._scan_pid_lbl.setText("Farever.exe  PID " + str(pid))

    @QtCore.Slot()
    def _scan_on_pid_lost(self):
        self._scan_pid_lbl.setText("Farever.exe not running")
