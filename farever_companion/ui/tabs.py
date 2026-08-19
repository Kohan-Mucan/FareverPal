"""Underline-style accent tab strip."""
from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from . import theme


class _TabBtn(QtWidgets.QAbstractButton):
    """Underline tab button with active accent pill."""

    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self.setText(text)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setAttribute(QtCore.Qt.WA_Hover, True)
        self._active = False

    @staticmethod
    def _font() -> QtGui.QFont:
        f = QtGui.QFont(theme.MONO_FONT)
        f.setPixelSize(12)
        f.setBold(True)
        f.setLetterSpacing(QtGui.QFont.AbsoluteSpacing, 1)
        return f

    def sizeHint(self) -> QtCore.QSize:
        fm = QtGui.QFontMetrics(self._font())
        return QtCore.QSize(fm.horizontalAdvance(self.text().upper()) + 30, 28)

    def set_active(self, on: bool) -> None:
        self._active = on
        self.update()

    def paintEvent(self, _e):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        p.setFont(self._font())
        fm = p.fontMetrics()
        h = self.height()
        label = self.text().upper()
        tw = fm.horizontalAdvance(label)
        hover = self.underMouse()
        if self._active:
            p.setPen(QtGui.QPen(QtGui.QColor(theme.ACCENT), 1))
            p.setBrush(QtGui.QColor(theme.with_alpha(theme.ACCENT, 45)))
            p.drawRoundedRect(QtCore.QRectF(1, 1, self.width() - 2, h - 6), 6, 6)
            x = (self.width() - tw) // 2
            p.setPen(QtGui.QColor("#ffffff"))
            p.drawText(x, (h + fm.ascent() - fm.descent()) // 2 - 1, label)
            p.fillRect(x - 2, h - 4, tw + 4, 2, QtGui.QColor(theme.ACCENT))
        else:
            p.setPen(QtGui.QPen(QtGui.QColor(theme.with_alpha(theme.BORDER, 180) if not hover else theme.ACCENT), 1))
            p.setBrush(QtGui.QColor(theme.with_alpha(theme.ACCENT, 20) if hover else theme.with_alpha(theme.PANEL, 180)))
            p.drawRoundedRect(QtCore.QRectF(1, 1, self.width() - 2, h - 6), 6, 6)
            x = (self.width() - tw) // 2
            p.setPen(QtGui.QColor(theme.ACCENT_LIGHT if hover else theme.TEXT))
            p.drawText(x, (h + fm.ascent() - fm.descent()) // 2 - 1, label)
            if hover:
                p.fillRect(x - 2, h - 4, tw + 4, 2, QtGui.QColor(theme.ACCENT))
        p.end()


class UnderlineTabs(QtWidgets.QWidget):
    """Underline-style tab strip with SegmentedControl-compatible API."""

    currentChanged = QtCore.Signal(str)

    def __init__(self, options: list[str], current: str | None = None, parent=None):
        super().__init__(parent)
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(0, 4, 0, 5)
        lay.setSpacing(18)
        self._btns: dict[str, _TabBtn] = {}
        for opt in options:
            b = _TabBtn(opt, self)
            b.clicked.connect(lambda _=False, t=opt: self._pick(t))
            lay.addWidget(b)
            self._btns[opt] = b
        lay.addStretch(1)
        self._current: str | None = None
        self.setCurrentText(current or (options[0] if options else ""))

    def _pick(self, text: str) -> None:
        if text != self._current:
            self.setCurrentText(text)
        self.currentChanged.emit(text)

    def setCurrentText(self, text: str) -> None:
        """Activate matching tab."""
        if not text:
            return
        b = self._btns.get(text)
        if b is None:
            for opt, ob in self._btns.items():
                if opt.lower() == str(text).lower():
                    b = ob
                    break
        if b is None or b is self._btns.get(self._current):
            return
        prev = self._btns.get(self._current) if self._current else None
        if prev is not None:
            prev.set_active(False)
        b.set_active(True)
        self._current = text

    def currentText(self) -> str:
        if self._current and self._current in self._btns:
            return self._current
        return ""

    def clear(self) -> None:
        prev = self._btns.get(self._current) if self._current else None
        if prev is not None:
            prev.set_active(False)
        self._current = None

    def paintEvent(self, _e):
        p = QtGui.QPainter(self)
        p.fillRect(0, self.height() - 1, self.width(), 1, QtGui.QColor(theme.BORDER))
        p.end()
