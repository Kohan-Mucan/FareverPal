"""The one shared widget that outlived its module: `GlyphButton`.

A card frame, a fill bar, a hairline and a percent formatter used to live here
too. All four are gone as of 2026-09-27, and the shape of that is worth
keeping: each was the last of its design generation, left behind when the thing
that used it was rebuilt - the card frame by `cards.py` (NavItem / OverlayCard /
InfoCard), the bar by the per-overlay widget libraries, the hairline and the
formatter by the page that had owned them. Nothing referenced them; the dead
`Card` class in particular reads as very much alive to any grep, because three
other files call `setObjectName("Card")` for the QSS and that is a style hook,
not a use of the class.

So what is left is a single painted button, re-exported from `components.py`,
which is the module every call site actually reaches it through. If a second
shared widget accumulates here again, this is the file; if this one ever does
not, `GlyphButton` folds into `components.py` and the module goes with it.
"""
from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from . import theme


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
