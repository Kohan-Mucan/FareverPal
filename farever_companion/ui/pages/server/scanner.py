"""Live connection scanner panel mixin for the Server diagnostics page."""
from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from ... import theme
from .net import (
    REGIONS, _STATUS_COLORS, _default_hosts_for, _get_flag_pixmap, _label_for_connection,
)
from .workers import _ConnectionScanWorker


class ScannerPanelMixin:

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
        self._scan_table.setColumnWidth(1, 200)  # Loc
        self._scan_table.setColumnWidth(2, 160)  # Country
        self._scan_table.setColumnWidth(4, 80)   # Port
        self._scan_table.setColumnWidth(5, 200)  # Status
        self._scan_table.setColumnWidth(6, 110)  # Latency
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
        if not item:
            return
        row = item.row()
        ip_item = self._scan_table.item(row, 3)
        port_item = self._scan_table.item(row, 4)
        if not ip_item or not ip_item.text() or not port_item:
            return
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
            if "_test" in region["code"] or "login" in region["code"]:
                continue
            act = add_menu.addAction(region["label"])
            act.triggered.connect(lambda checked=False, r_code=region["code"], r_ip=ip, r_port=port: self._manual_add_ip(r_code, r_ip, r_port))

        add_menu.addSeparator()

        # 2. Test Servers
        for region in REGIONS:
            if "_test" not in region["code"]:
                continue
            act = add_menu.addAction(region["label"])
            act.triggered.connect(lambda checked=False, r_code=region["code"], r_ip=ip, r_port=port: self._manual_add_ip(r_code, r_ip, r_port))

        add_menu.addSeparator()

        # 3. Login Gateways
        for region in REGIONS:
            if "login" not in region["code"]:
                continue
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
                # Add to existing hosts if not duplicate (compare tuple-form)
                new_entry = (ip, port)
                if new_entry not in {tuple(h) for h in region["hosts"]}:
                    region["hosts"].append(new_entry)
                new_hosts = region["hosts"]
                break

        # Save updates to settings.json (user-added IPs only - defaults are hard-coded)
        base = {tuple(hp) for hp in _default_hosts_for(code)}
        custom_hosts[code] = [[h, p] for h, p in new_hosts if (h, p) not in base]
        self.s.server_custom_hosts = custom_hosts
        self.s.save()

        # Update UI table immediately to show the new location
        for row in range(self._scan_table.rowCount()):
            ip_item = self._scan_table.item(row, 3)
            if ip_item and ip_item.text() == ip:
                loc_item = self._scan_table.item(row, 1)
                if loc_item:
                    loc_item.setText(region_label)

        region_obj = next((r for r in REGIONS if r["code"] == code), None)
        if region_obj:
            self._update_card_hosts_label(region_obj)

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
            if "login" in r["code"]:
                continue
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

        # Determine Location Label - resolve every known source in priority
        # order (per-region game IPs -> login/master control plane -> user-added
        # IPs). The helper centralizes this so hardcoded hosts never fall
        # through to 'Unassigned' just because they aren't region game IPs.
        custom_hosts = getattr(self.s, "server_custom_hosts", {})
        loc_label = _label_for_connection(remote_ip, port, hostname, custom_hosts) or "Unassigned"

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

    def _scan_on_status(self, msg):
        self._scan_status_lbl.setText(msg)

    @QtCore.Slot(int)
    def _scan_on_pid_found(self, pid):
        self._scan_pid_lbl.setText("Farever.exe  PID " + str(pid))

    @QtCore.Slot()
    def _scan_on_pid_lost(self):
        self._scan_pid_lbl.setText("Farever.exe not running")
