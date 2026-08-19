"""Craft-page list roles, sizing, and the delegate for the recipe list:
full-width rows (icon + name wrapping up to two rows, job · LV meta and
the gold QUEUED ring on the right). The per-job group bars exist only
while a search is active — the browse view is flat (a chip filter
already says the job).
"""
from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from ... import theme

# list-row roles
ID_ROLE = QtCore.Qt.UserRole
HEADER_ROLE = QtCore.Qt.UserRole + 1
DATA_ROLE = QtCore.Qt.UserRole + 2
CAT_ROLE = QtCore.Qt.UserRole + 3
NAME_ROLE = QtCore.Qt.UserRole + 4
QUEUED_ROLE = QtCore.Qt.UserRole + 7

HEADER_W = 3000
HEADER_H = 26

LIST_ICON = 34
LIST_ROW_H = 28


def _craft_row_height(font: QtGui.QFont | None = None) -> int:
    """List-row height sized for the item name wrapping up to TWO rows."""
    f = font or QtWidgets.QApplication.font()
    fm = QtGui.QFontMetrics(f)
    return max(LIST_ROW_H * 2, LIST_ICON + 4 + 2 * fm.lineSpacing())


class CraftTileDelegate(QtWidgets.QStyledItemDelegate):
    """Paints the compact recipe rows (icon + name) and the per-job group
    bars that split a search's results, plus the gold QUEUED ring at the
    row's right padding when the recipe is on the craft list.
    """

    def paint(self, painter, option, index):
        if index.data(HEADER_ROLE):
            self._paint_header(painter, option, index)
            return
        self._paint_row(painter, option, index)

    def _paint_row(self, painter, option, index):
        """The item icon on the left and the name wrapping up to two rows
        (drawn manually so it stops short of the right zone); the job · LV
        meta right-aligned (the job name drops out when a single job
        filter already says it), and the gold QUEUED ring at the far right
        edge of the row.
        """
        r = index.data(DATA_ROLE)
        meta = ""
        if r:
            show_job = getattr(option.widget, "_craft_meta_job", True)
            job_part = (f"{r.get('job_name', '').upper()}  ·  "
                        if show_job else "")
            meta = f"{job_part}LV {r.get('level', '')}"
        opt = QtWidgets.QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.decorationSize = QtCore.QSize(LIST_ICON, LIST_ICON)
        opt.displayAlignment = (QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        mf = self._meta_font(option.font)
        mw = QtGui.QFontMetrics(mf).horizontalAdvance(meta) if meta else 0
        name = opt.text
        opt.text = ""          # the style draws bg + icon only; text is ours
        widget = option.widget
        style = widget.style() if widget else QtWidgets.QApplication.style()
        style.drawControl(QtWidgets.QStyle.CE_ItemViewItem, opt, painter, widget)
        name_w = 230
        tr = style.subElementRect(
            QtWidgets.QStyle.SE_ItemViewItemText, opt, widget)
        name_left = tr.left()
        if name:
            painter.save()
            painter.setFont(opt.font)
            painter.setPen(QtGui.QColor(
                opt.palette.color(QtGui.QPalette.Text)))
            name_rect = QtCore.QRect(name_left, option.rect.top(), name_w, option.rect.height())
            painter.drawText(name_rect, (QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter
                                         | QtCore.Qt.TextWordWrap), name)
            painter.restore()
        meta_x = name_left + name_w + 12
        if meta:
            painter.save()
            painter.setFont(mf)
            painter.setPen(QtGui.QColor(theme.MUTED))
            meta_rect = QtCore.QRect(meta_x, option.rect.top(), mw + 4, option.rect.height())
            painter.drawText(meta_rect,
                             (QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter),
                             meta)
            painter.restore()
        if not index.data(QUEUED_ROLE):
            return
        ring_x = meta_x + (mw + 14 if meta else 0)
        c = QtCore.QPointF(ring_x, option.rect.center().y())
        painter.save()
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        pen = QtGui.QPen(QtGui.QColor(theme.GOLD), 1.5)
        painter.setPen(pen)
        painter.setBrush(QtCore.Qt.NoBrush)
        painter.drawEllipse(c, 2.5, 2.5)
        painter.restore()

    @staticmethod
    def _meta_font(base: QtGui.QFont) -> QtGui.QFont:
        """The mono meta font for the row's job · LV text."""
        f = QtGui.QFont(theme.MONO_FONT, 11)
        f.setPixelSize(11)
        return f

    def _paint_header(self, painter, option, index):
        """The full-width job bar grouping a search's results."""
        painter.save()
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        r = option.rect
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(QtGui.QColor(
            theme.with_alpha(theme.ACCENT, 14)))
        painter.drawRoundedRect(r, 4, 4)
        f = QtGui.QFont(option.font)
        f.setBold(True)
        f.setPixelSize(12)
        painter.setFont(f)
        painter.setPen(QtGui.QColor(theme.MUTED))
        painter.drawText(r.adjusted(8, 0, -8, 0),
                         QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
                         index.data() or "")
        painter.restore()
