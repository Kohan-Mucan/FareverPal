"""Server diagnostics page mixin (tab chrome + cleanup), combining the three
panel mixins. Kept in the `server` package so each module stays under the UI
line budget.
"""
from __future__ import annotations

import time

from PySide6 import QtCore, QtGui, QtWidgets

from ... import theme
from ... import components as C
from .ping import PingPanelMixin
from .scanner import ScannerPanelMixin
from .trace import TracePanelMixin
from .workers import _SteamPlayerCountWorker


class ServerPageMixin(PingPanelMixin, ScannerPanelMixin, TracePanelMixin):

    def _page_server(self):
        return self._build_server_page()

    def _build_server_page(self):
        page, root = self._page_container()

        hdr_lay = QtWidgets.QHBoxLayout()
        hdr_lay.setSpacing(12)
        hdr = QtWidgets.QLabel("Server Diagnostics")
        hdr.setObjectName("H1")
        hdr_lay.addWidget(hdr)

        hdr_lay.addStretch(1)

        cached_count = getattr(self, "_last_steam_count", None)
        if cached_count is not None and cached_count >= 0:
            init_text = f"STEAM ONLINE · {cached_count:,}"
        elif cached_count is not None:
            init_text = "STEAM ONLINE · —"
        else:
            init_text = "STEAM ONLINE · …"

        self._steam_players_badge = QtWidgets.QPushButton(init_text)
        self._steam_players_badge.setCursor(QtCore.Qt.PointingHandCursor)
        self._steam_players_badge.setStyleSheet(
            f"QPushButton {{ color: {theme.GOOD}; background: {theme.with_alpha(theme.GOOD, 16)}; "
            f"border: 1px solid {theme.with_alpha(theme.GOOD, 60)}; border-radius: 4px; "
            f'padding: 3px 9px; font-family: "{theme.MONO_FONT}", "Consolas", monospace; '
            "font-size: 11px; font-weight: 700; }\n"
            f"QPushButton:hover {{ background: {theme.with_alpha(theme.GOOD, 30)}; color: {theme.TEXT}; }}")
        self._steam_players_badge.clicked.connect(
            lambda: QtGui.QDesktopServices.openUrl(QtCore.QUrl("https://steamdb.info/app/3672400/charts/#max")))
        hdr_lay.addWidget(self._steam_players_badge)

        hdr_lay.addStretch(1)

        # Tabs for Server Sub-pages
        self._server_tabs = C.SegmentedControl(["Game Servers", "Live Game IP Scanner", "Network Trace"],
                                               current="Game Servers")
        self._server_tabs.currentChanged.connect(self._server_tab_changed)
        for opt_text, btn in self._server_tabs._btns.items():
            btn.clicked.connect(lambda _, t=opt_text: self.server_stack.setCurrentIndex({"Game Servers": 0, "Live Game IP Scanner": 1, "Network Trace": 2}.get(t, 0)))
        hdr_lay.addWidget(self._server_tabs)

        root.addLayout(hdr_lay)

        self._ping_worker   = None
        self._scan_worker   = None
        self._trace_worker  = None
        self._steam_worker  = None
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

    def _fetch_steam_player_count(self) -> None:
        now = time.monotonic()
        last_t = getattr(self, "_last_steam_time", 0.0)
        last_count = getattr(self, "_last_steam_count", None)
        # Only fetch once per 30 minutes (1800s) unless the app is restarted
        if last_count is not None and (now - last_t < 1800.0):
            self._on_steam_player_count(last_count)
            return

        if hasattr(self, "_steam_worker") and self._steam_worker and self._steam_worker.isRunning():
            return
        self._steam_worker = _SteamPlayerCountWorker()
        self._steam_worker.result.connect(self._on_steam_player_count)
        self._steam_worker.start()

    def _on_steam_player_count(self, count: int) -> None:
        self._last_steam_count = count
        self._last_steam_time = time.monotonic()
        badge = getattr(self, "_steam_players_badge", None)
        if badge is None:
            return
        try:
            from shiboken6 import isValid
            if not isValid(badge):
                return
        except Exception:
            pass
        try:
            if count >= 0:
                badge.setText(f"STEAM ONLINE · {count:,}")
            else:
                badge.setText("STEAM ONLINE · —")
        except RuntimeError:
            pass

    def _server_tab_changed(self, text):
        mapping = {"Game Servers": 0, "Live Game IP Scanner": 1, "Network Trace": 2}
        self.server_stack.setCurrentIndex(mapping.get(text, 0))

    def _server_cleanup(self):
        """Force-stop all background diagnostic workers to prevent shutdown crashes."""
        workers = []
        if hasattr(self, "_ping_worker") and self._ping_worker:
            workers.append(self._ping_worker)
        if hasattr(self, "_scan_worker") and self._scan_worker:
            workers.append(self._scan_worker)
        if hasattr(self, "_trace_worker") and self._trace_worker:
            workers.append(self._trace_worker)
        if hasattr(self, "_steam_worker") and self._steam_worker:
            workers.append(self._steam_worker)

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
        self._steam_worker = None
