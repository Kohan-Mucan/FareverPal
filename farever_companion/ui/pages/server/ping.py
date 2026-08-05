"""Region ping tester panel mixin for the Server diagnostics page."""
from __future__ import annotations

from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from ... import theme
from ....config import config_dir
from .cards import ClickableCard
from .net import (
    REGIONS, _STATUS_COLORS, _default_hosts_for, _download_flag, _get_flag_pixmap,
)
from .workers import _PingWorker


class PingPanelMixin:

    # ===============================================================
    # TOP: Ping tester
    # ===============================================================
    def _build_ping_panel(self):
        w = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(10)

        top_bar = QtWidgets.QHBoxLayout()
        lbl = QtWidgets.QLabel("Region Ping Test — per-region game servers + Global Login (control plane)")
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

        for region in REGIONS:
            self._update_card_hosts_label(region)

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
        self._ping_table.setColumnWidth(1, 200)  # Loc
        self._ping_table.setColumnWidth(2, 160)  # Country
        self._ping_table.setColumnWidth(4, 80)   # Port
        self._ping_table.setColumnWidth(5, 200)  # Status
        self._ping_table.setColumnWidth(6, 110)  # Latency
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
            if "login" in code:
                continue
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
        if not item:
            return
        row = item.row()
        ip_item = self._ping_table.item(row, 3)
        if not ip_item or not ip_item.text():
            return

        menu = QtWidgets.QMenu(self)
        menu.addAction(f"Diagnostic Trace", lambda: self._start_trace(ip_item.text(), "tracert"))
        menu.addSeparator()
        copy_action = menu.addAction(f"Copy IP ({ip_item.text()})")
        selected = menu.exec_(self._ping_table.viewport().mapToGlobal(pos))
        if selected == copy_action:
            QtWidgets.QApplication.clipboard().setText(ip_item.text())

    def _load_saved_card_states(self):
        # Merge user-added IPs (settings.json) over the hardcoded defaults.
        custom_hosts = getattr(self.s, "server_custom_hosts", {})
        for region in REGIONS:
            code = region["code"]
            region["hosts"] = [tuple(hp) for hp in region["hosts"]]
            base = {tuple(hp) for hp in region["hosts"]}
            for entry in custom_hosts.get(code) or []:
                if tuple(entry) not in base:
                    region["hosts"].append(tuple(entry))

        for region in REGIONS:
            self._update_card_hosts_label(region)

        # Legacy file cleanup
        try:
            legacy_file = Path(config_dir()) / "server_pings.json"
            if legacy_file.exists():
                legacy_file.unlink(missing_ok=True)
        except Exception:
            pass

    def _update_card_hosts_label(self, region):
        # Cards show ping results only. Tooltip lists any user-added IPs.
        card = self._ping_cards.get(region["code"])
        if not card:
            return
        base = {tuple(hp) for hp in _default_hosts_for(region["code"])}
        user = [(h, p) for h, p in region["hosts"] if (h, p) not in base]
        if user:
            card["widget"].setToolTip("User-added IPs:\n" + "\n".join(f"{h}:{p}" for h, p in user))
        else:
            card["widget"].setToolTip("Built-in region defaults. Press Test Selected to ping.")

    def _save_card_states(self):
        try:
            states = {code: card["widget"].active for code, card in self._ping_cards.items()}
            self.s.server_pings = states
            # Persist only IPs the user added themselves.
            user_hosts = {}
            for r in REGIONS:
                code = r["code"]
                base = {tuple(hp) for hp in _default_hosts_for(code)}
                extra = [[h, p] for h, p in r["hosts"] if (h, p) not in base]
                if extra:
                    user_hosts[code] = extra
            self.s.server_custom_hosts = user_hosts
            self.s.save()
        except Exception:
            pass

    def _reset_to_default_ips(self):
        # Clear custom server hosts config override
        self.s.server_custom_hosts = {}
        self.s.save()

        # Hard reset regions to the defaults
        for region in REGIONS:
            region["hosts"] = _default_hosts_for(region["code"])

        for region in REGIONS:
            self._update_card_hosts_label(region)

        self._ping_status.setText("All server IPs have been reset to defaults (Login servers kept).")
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
        self._ping_dns_fail = 0
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

        # 2. Show failures too, with a clear reason (DNS dead vs no response)
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

        if not ok:
            # Don't clutter the results list with dead hosts - the region card
            # shows FAIL and the summary line reports unreachable regions.
            if ip == "DNS failed":
                self._ping_dns_fail = getattr(self, "_ping_dns_fail", 0) + 1
            return

        row = self._ping_table.rowCount()
        self._ping_table.insertRow(row)

        self._ping_table.setItem(row, 0, cell(str(row + 1), _STATUS_COLORS["ok"], bold=True))
        self._ping_table.setItem(row, 1, cell(lbl))
        self._ping_table.setItem(row, 2, cell(country_name, icon_code=country_code))
        self._ping_table.setItem(row, 3, cell(ip))
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
        dns_n = getattr(self, "_ping_dns_fail", 0)
        if dns_n:
            msg += "   (" + str(dns_n) + " DNS dead - game servers only resolve while a session is running)"
        self._ping_status.setText(msg)
