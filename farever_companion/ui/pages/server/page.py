"""Server diagnostics page mixin (tab chrome + cleanup), combining the three
panel mixins. Kept in the `server` package so each module stays under the UI
line budget.
"""
from __future__ import annotations

from PySide6 import QtWidgets

from ... import theme
from ... import components as C
from .ping import PingPanelMixin
from .scanner import ScannerPanelMixin
from .trace import TracePanelMixin


class ServerPageMixin(PingPanelMixin, ScannerPanelMixin, TracePanelMixin):

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
