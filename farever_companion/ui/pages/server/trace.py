"""Network trace / diagnostics panel mixin for the Server diagnostics page."""
from __future__ import annotations

from PySide6 import QtCore, QtWidgets

from ... import theme
from .workers import _TraceWorker


class TracePanelMixin:

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
            self._trace_worker.wait(1000)  # Give it a second to die

        # Jump to Trace tab
        self._server_tabs.setCurrentText("Network Trace")
        self.server_stack.setCurrentIndex(2)

        label_text = f"{mode.upper()}"
        if ip:
            label_text += f": {ip}"
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
