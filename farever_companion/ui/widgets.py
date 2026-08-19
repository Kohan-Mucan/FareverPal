"""Shared UI components, the single source of the app's look.

Every window builds from these so the design is consistent (the fix for the old
tool's mismatched windows). Plain QWidgets + a little QPainter; styled by QSS
object names in theme.py.
"""
from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from . import theme


class Card(QtWidgets.QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)
        self.body = lay


class Bar(QtWidgets.QWidget):
    """A flat horizontal fill bar with an inline label."""
    def __init__(self, color: str = theme.DANGER, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(14)
        self._frac = 0.0
        self._label = ""
        self._color = QtGui.QColor(color)

    def set(self, frac: float, label: str = ""):
        self._frac = max(0.0, min(1.0, frac))
        self._label = label
        self.update()

    def set_color(self, color: str):
        self._color = QtGui.QColor(color)
        self.update()

    def paintEvent(self, _e):
        p = QtGui.QPainter(self)
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, QtGui.QColor(theme.PANEL_HI))
        p.fillRect(0, 0, int(w * self._frac), h, self._color)
        if self._label:
            p.setPen(QtGui.QColor(theme.TEXT))
            f = p.font(); f.setPointSize(8); p.setFont(f)
            p.drawText(QtCore.QRect(0, 0, w - 3, h),
                       QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter, self._label)
        p.end()


class Sparkline(QtWidgets.QWidget):
    """A scrolling line chart of recent values."""
    def __init__(self, color: str = theme.ACCENT, height: int = 46, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(height)
        self._data: list[float] = []
        self._color = QtGui.QColor(color)

    def set_data(self, data: list[float]):
        self._data = list(data)
        self.update()

    def set_color(self, color: str):
        self._color = QtGui.QColor(color)

    def paintEvent(self, _e):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, QtGui.QColor(theme.PANEL))
        data = self._data
        if len(data) >= 2:
            mx = max(data) or 1.0
            n = len(data)
            path = QtGui.QPainterPath()
            for i, v in enumerate(data):
                x = i * (w - 2) / (n - 1) + 1
                y = (h - 3) - (v / mx) * (h - 8)
                path.lineTo(x, y) if i else path.moveTo(x, y)
            pen = QtGui.QPen(self._color); pen.setWidth(1)
            p.setPen(pen); p.drawPath(path)
            p.setPen(QtGui.QColor(theme.MUTED))
            f = p.font(); f.setPointSize(7); p.setFont(f)
            p.drawText(QtCore.QRect(0, 1, w - 2, 12),
                       QtCore.Qt.AlignRight | QtCore.Qt.AlignTop, f"{mx:,.0f}")
        p.setPen(QtGui.QColor(theme.BORDER))
        p.drawLine(1, h - 2, w - 1, h - 2)
        p.end()


class ColorButton(QtWidgets.QPushButton):
    """A swatch button that opens a color picker and emits the chosen hex."""
    changed = QtCore.Signal(str)

    def __init__(self, color: str, parent=None):
        super().__init__(parent)
        self._color = color
        self.setFixedSize(34, 22)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setToolTip("Pick color")
        self.clicked.connect(self._pick)
        self._apply()

    def _apply(self):
        self.setStyleSheet(
            f"QPushButton {{ background: {self._color}; border: 1px solid {theme.BORDER}; }}")

    def color(self) -> str:
        return self._color

    def set_color(self, c: str):
        self._color = c
        self._apply()

    def _pick(self):
        col = QtWidgets.QColorDialog.getColor(QtGui.QColor(self._color), self,
                                              "Pick color")
        if col.isValid():
            self._color = col.name()
            self._apply()
            self.changed.emit(self._color)


def hairline() -> QtWidgets.QFrame:
    f = QtWidgets.QFrame()
    f.setObjectName("Hairline")
    return f


def fmt_pct(p: float) -> str:
    v = p * 100.0
    if v >= 10:
        return f"{v:.0f}%"
    if v >= 1:
        return f"{v:.1f}%"
    if v > 0:
        return f"{v:.2g}%"
    return "0%"


class GlyphButton(QtWidgets.QPushButton):
    """A QPushButton that draws its glyph dead-centered. The stepper's −
    and + are painted as LINE segments centered on the button's exact
    center — a 1px-tall glyph bar/cross can't be reliably centered by
    drawText (Qt centers by the font's em box, and the minus sign sits
    off-center inside it in most UI fonts, Inter included), so the glyph
    is drawn directly: pixel-perfect on every machine, no font metrics
    involved. Any other text falls back to the ink-nudge drawText
    (measured once per text+font by rasterizing and cropping to ink)."""

    _nudges: dict[tuple, tuple[int, int]] = {}

    def __init__(self, text: str):
        super().__init__(text)
        self.setFocusPolicy(QtCore.Qt.NoFocus)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self._nudge: tuple[int, int] | None = None

    @staticmethod
    def _glyph_lines(text: str, rect: QtCore.QRect) -> list[tuple[int, int, int, int]] | None:
        if text not in ("−", "+", "-"):
            return None
        cx, cy = rect.center().x(), rect.center().y()
        if text in ("−", "-"):
            return [(cx - 4, cy, cx + 4, cy)]
        return [(cx - 4, cy, cx + 4, cy), (cx, cy - 4, cx, cy + 4)]

    def _ink_nudge(self) -> tuple[int, int]:
        if self._nudge is not None:
            return self._nudge
        key = (self.text(), self.font().toString())
        n = self._nudges.get(key)
        if n is None:
            fm = self.fontMetrics()
            side = max(fm.boundingRect(self.text()).width(),
                       fm.height()) + 12
            canvas = QtGui.QPixmap(side, side)
            canvas.fill(QtCore.Qt.transparent)
            p = QtGui.QPainter(canvas)
            p.setRenderHint(QtGui.QPainter.Antialiasing)
            p.setFont(self.font())
            p.drawText(canvas.rect(), QtCore.Qt.AlignCenter, self.text())
            p.end()
            img = canvas.toImage()
            minx = miny = side
            maxx = maxy = -1
            for y in range(side):
                for x in range(side):
                    if img.pixelColor(x, y).alpha() > 40:
                        minx = min(minx, x); maxx = max(maxx, x)
                        miny = min(miny, y); maxy = max(maxy, y)
            half = side / 2
            n = (-round((minx + maxx) / 2 - half),
                 -round((miny + maxy) / 2 - half))
            self._nudges[key] = n
        self._nudge = n
        return n

    def paintEvent(self, e):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        col = (theme.BORDER if not self.isEnabled()
               else theme.ACCENT if self.underMouse() else theme.MUTED)
        pen = QtGui.QPen(QtGui.QColor(col), 2)
        pen.setCapStyle(QtCore.Qt.RoundCap)
        p.setPen(pen)
        lines = self._glyph_lines(self.text(), self.rect())
        if lines is not None:
            for x1, y1, x2, y2 in lines:
                p.drawLine(x1, y1, x2, y2)
        else:
            p.setFont(self.font())
            dx, dy = self._ink_nudge()
            p.drawText(self.rect().adjusted(dx, dy, dx, dy),
                       QtCore.Qt.AlignCenter, self.text())
        p.end()
