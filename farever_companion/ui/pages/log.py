from __future__ import annotations

from PySide6 import QtWidgets
from .. import components as C

class LogPageMixin:
    def _page_log(self):
        page = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(page)
        v.setContentsMargins(22, 20, 22, 20)
        v.setSpacing(10)
        v.addWidget(C.SectionHeader("Activity Log"))
        self.log_view = QtWidgets.QPlainTextEdit()
        self.log_view.setReadOnly(True)
        # unbounded appendPlainText growth is a slow leak over long sessions;
        # 1000 blocks keeps the recent history and sheds the rest
        self.log_view.setMaximumBlockCount(1000)
        if self._log_buffer:
            self.log_view.setPlainText("\n".join(self._log_buffer))
            sb = self.log_view.verticalScrollBar()
            sb.setValue(sb.maximum())
        v.addWidget(self.log_view, 1)
        return page
