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
        """Stop background diagnostic workers without killing Qt threads.

        QThread.terminate() is unsafe: it can stop a worker while it is inside
        a Python/C allocation, leaving the process heap corrupted. The next
        ordinary Qt allocation then appears as an access violation in an
        unrelated constructor (for example CallWorker.__init__). Every server
        worker has a cooperative stop() path, so wait for that path instead.
        """
        workers = (
            ("_ping_worker",),
            ("_scan_worker",),
            ("_trace_worker",),
            ("_steam_worker",),
        )
        for (attr,) in workers:
            w = getattr(self, attr, None)
            if w is None:
                continue
            try:
                if w.isRunning():
                    w.stop()
                    # Never terminate here. Keep the reference if a network or
                    # subprocess operation has not returned yet; shutdown_all()
                    # gets another chance during app teardown.
                    if not w.wait(2000):
                        print(f"[server] {attr} still stopping; leaving it alive")
                        continue
                setattr(self, attr, None)
            except Exception:
                pass
