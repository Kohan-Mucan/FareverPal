"""Clickable region card for the ping tester grid."""
from __future__ import annotations

from PySide6 import QtCore, QtWidgets

from ... import theme


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
