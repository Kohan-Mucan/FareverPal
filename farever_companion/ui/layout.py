"""Reusable layout, scroll, and container utilities for Tactical Overlay.

Centralizes Qt layout helpers (FlowLayout, wrapping labels, frameless scroll areas,
layout clearing, and box constructors) so pages and components remain lean and modular.
"""
from __future__ import annotations

from typing import Iterable, Sequence
from PySide6 import QtCore, QtGui, QtWidgets


class FlowLayout(QtWidgets.QLayout):
    """Wrapping horizontal flex layout — items flow onto a new line when the row fills."""

    def __init__(self, parent=None, margin=0, spacing=4):
        super().__init__(parent)
        if parent is not None:
            if isinstance(margin, (int, float)):
                self.setContentsMargins(int(margin), int(margin), int(margin), int(margin))
            elif isinstance(margin, (tuple, list)) and len(margin) == 4:
                self.setContentsMargins(*margin)
        self.setSpacing(spacing)
        self._items: list = []

    def addItem(self, item):
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int):
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self):
        return QtCore.Qt.Orientations(QtCore.Qt.Orientation(0))

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._do_layout(QtCore.QRect(0, 0, width, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, False)

    def sizeHint(self):
        parent = self.parentWidget()
        width = parent.width() if parent is not None else 0
        m = self.contentsMargins()
        if width <= 0:
            return self.minimumSize()
        return QtCore.QSize(width, self.heightForWidth(width))

    def minimumSize(self):
        size = QtCore.QSize()
        for it in self._items:
            w = it.widget()
            if w is None or w.isHidden():
                continue
            size = size.expandedTo(it.minimumSize())
        m = self.contentsMargins()
        return size + QtCore.QSize(m.left() + m.right(), m.top() + m.bottom())

    def _do_layout(self, rect: QtCore.QRect, test_only: bool) -> int:
        m = self.contentsMargins()
        x, y = rect.x() + m.left(), rect.y() + m.top()
        line_h, used = 0, 0
        space = self.spacing()
        right = rect.right() - m.right()
        for it in self._items:
            w = it.widget()
            if w is None or w.isHidden():
                continue
            hint = w.sizeHint()
            if x + hint.width() > right and line_h > 0:
                x = rect.x() + m.left()
                y += line_h + space
                line_h = 0
            if not test_only:
                it.setGeometry(QtCore.QRect(QtCore.QPoint(x, y), hint))
            x += hint.width() + space
            line_h = max(line_h, hint.height())
            used = max(used, y + hint.height() + m.bottom())
        return used


class StretchFlow(FlowLayout):
    """FlowLayout whose items fill each row edge to edge.

    Items are grouped so a row only takes as many as can share the width at
    or above their minimum size; rows are then stretched so the items spread
    across the full width (like a grid row). When the viewport is too narrow
    to keep everything in one row the layout wraps to fewer per row instead of
    overflowing horizontally. A lone leftover item keeps its natural width.
    """

    def _do_layout(self, rect: QtCore.QRect, test_only: bool) -> int:
        m = self.contentsMargins()
        space = self.spacing()
        avail = max(0, rect.width() - m.left() - m.right())
        items = [it for it in self._items
                 if it.widget() is not None and not it.widget().isHidden()]
        if not items or avail <= 0:
            return 0

        def _share(n: int) -> float:
            return (avail - space * (n - 1)) / n if n else 0.0

        # Group into rows: a candidate row must fit every member at least at
        # its own minimum width when the row's width is shared equally.
        rows: list[list] = []
        row: list = []
        row_max_min = 0
        for it in items:
            w = it.widget()
            mn = max(it.minimumSize().width(), w.minimumSizeHint().width())
            n = len(row) + 1
            if row and _share(n) < row_max_min - 1:
                rows.append(row)
                row, row_max_min = [], 0
            row.append(it)
            row_max_min = max(row_max_min, mn)
        if row:
            rows.append(row)

        y = rect.y() + m.top()
        for row in rows:
            n = len(row)
            widths = [it.sizeHint().width() for it in row]
            w_i = (min(avail, widths[0]) if n == 1
                   else int((avail - space * (n - 1)) / n))
            row_h = 0
            for it in row:
                h = it.heightForWidth(w_i)
                if h < 0:
                    h = it.sizeHint().height()
                row_h = max(row_h, h)
            if not test_only:
                x = rect.x() + m.left()
                for it in row:
                    it.setGeometry(QtCore.QRect(x, y, w_i, row_h))
                    x += w_i + space
            y += row_h + space
        return y - space + m.bottom() if rows else 0


class FlowRow(QtWidgets.QWidget):
    """A widget hosting a wrapping FlowLayout whose sizeHint mirrors its
    container's width (less container margins), so nested wrapping layouts can't
    size it to a stale default width."""

    def __init__(self, parent=None, spacing: int = 6, margins: int = 0):
        super().__init__(parent)
        self._flow = FlowLayout(self, margin=margins, spacing=spacing)
        self.setLayout(self._flow)

    def layout(self) -> FlowLayout:
        return self._flow

    def sizeHint(self):
        gp = self.parentWidget()
        container = gp.parentWidget() if gp is not None else None
        w = container.width() if container is not None else self.width()
        lm = QtCore.QMargins()
        if container is not None and container.layout() is not None:
            lm = container.layout().contentsMargins()
        m = self._flow.contentsMargins()
        avail = max(0, w - lm.left() - lm.right() - m.left() - m.right())
        if avail <= 0:
            return self.minimumSize()
        return QtCore.QSize(avail, self._flow.heightForWidth(avail))


class WrapLabel(QtWidgets.QLabel):
    """Word-wrapping label that enforces its wrapped text height at the ACTUAL
    assigned width as a minimum height, preventing nested box/grid layouts from
    squashing its lines."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setWordWrap(True)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        w = self.width()
        if w > 30 and self.text():
            need = self.heightForWidth(w)
            if need > 0 and abs(need - self.minimumHeight()) > 1:
                self.setMinimumHeight(need)


class CardScroll(QtWidgets.QScrollArea):
    """Detail-pane scroll area whose card hugs its content: it fills the pane width,
    sizes to the content's sizeHint, and only scrolls when content is taller than the pane."""

    def __init__(self, card: QtWidgets.QWidget, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(False)
        self.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.setStyleSheet("QScrollArea{background:transparent;border:0;}")
        self.setWidget(card)
        self._card = card

    def _fit(self) -> None:
        if self._card is None:
            return
        w = self.viewport().width()
        if w <= 0:
            return
        card = self._card
        lay = card.layout()
        h = -1
        if lay is not None and lay.hasHeightForWidth():
            h = lay.heightForWidth(w)
        if h <= 0:
            h = card.sizeHint().height()
        card.resize(w, max(h, 1))
        if lay is not None:
            lay.activate()
        deficit = 0
        for lbl in card.findChildren(QtWidgets.QLabel):
            if lbl.isHidden() or not lbl.wordWrap() or not lbl.text():
                continue
            w_lbl = lbl.width()
            if w_lbl <= 30:
                continue
            need = lbl.heightForWidth(w_lbl)
            if need > 0 and lbl.height() < need:
                deficit = max(deficit, need - lbl.height())
        if deficit > 2:
            card.resize(w, card.height() + deficit)
            if lay is not None:
                lay.activate()

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._fit()


class HScrollCard(QtWidgets.QScrollArea):
    """Horizontal-only scroll card for a wide stat table: content keeps its natural
    width and reserves vertical height so the scrollbar never clips content."""

    def __init__(self, widget: QtWidgets.QWidget, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.setStyleSheet("QScrollArea{background:transparent;border:0;}")
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.setMinimumWidth(0)
        widget.setMinimumWidth(widget.sizeHint().width())
        self.setWidget(widget)
        self._content = widget
        self.setFixedHeight(widget.sizeHint().height()
                            + self.horizontalScrollBar().sizeHint().height())

    def sizeHint(self) -> QtCore.QSize:
        sh = super().sizeHint()
        return QtCore.QSize(sh.width(), sh.height())


def make_scroll(widget: QtWidgets.QWidget, h_scroll: bool = False,
                v_scroll: bool = True, parent=None) -> QtWidgets.QScrollArea:
    """Create a transparent, borderless QScrollArea hosting the given widget."""
    sa = QtWidgets.QScrollArea(parent)
    sa.setWidgetResizable(True)
    sa.setFrameShape(QtWidgets.QFrame.NoFrame)
    sa.setStyleSheet("QScrollArea{background:transparent;border:0;}")
    sa.setHorizontalScrollBarPolicy(
        QtCore.Qt.ScrollBarAsNeeded if h_scroll else QtCore.Qt.ScrollBarAlwaysOff)
    sa.setVerticalScrollBarPolicy(
        QtCore.Qt.ScrollBarAsNeeded if v_scroll else QtCore.Qt.ScrollBarAlwaysOff)
    sa.setWidget(widget)
    return sa


def clear_layout(layout: QtWidgets.QLayout | None) -> None:
    """Safely remove and delete all child widgets and nested items from a layout."""
    if layout is None:
        return
    while layout.count():
        item = layout.takeAt(0)
        w = item.widget()
        if w is not None:
            w.deleteLater()
        child_lay = item.layout()
        if child_lay is not None:
            clear_layout(child_lay)


def vbox(parent: QtWidgets.QWidget | None = None,
         margins: int | tuple[int, int, int, int] = 0,
         spacing: int = 6) -> QtWidgets.QVBoxLayout:
    """Construct a QVBoxLayout with default zero-margins and configured spacing."""
    lay = QtWidgets.QVBoxLayout(parent) if parent is not None else QtWidgets.QVBoxLayout()
    if isinstance(margins, (int, float)):
        lay.setContentsMargins(int(margins), int(margins), int(margins), int(margins))
    elif isinstance(margins, (tuple, list)) and len(margins) == 4:
        lay.setContentsMargins(*margins)
    lay.setSpacing(spacing)
    return lay


def hbox(parent: QtWidgets.QWidget | None = None,
         margins: int | tuple[int, int, int, int] = 0,
         spacing: int = 6) -> QtWidgets.QHBoxLayout:
    """Construct a QHBoxLayout with default zero-margins and configured spacing."""
    lay = QtWidgets.QHBoxLayout(parent) if parent is not None else QtWidgets.QHBoxLayout()
    if isinstance(margins, (int, float)):
        lay.setContentsMargins(int(margins), int(margins), int(margins), int(margins))
    elif isinstance(margins, (tuple, list)) and len(margins) == 4:
        lay.setContentsMargins(*margins)
    lay.setSpacing(spacing)
    return lay
