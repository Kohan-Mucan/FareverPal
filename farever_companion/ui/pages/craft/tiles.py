"""Craft-page list roles, sizing, and the delegate for the recipe list:
full-width rows (icon + name wrapping up to two rows, job · LV meta and
the gold QUEUED ring on the right). The per-job group bars exist only
while a search is active — the browse view is flat (a chip filter
already says the job).
"""
from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from ... import theme
from ..tile_delegate import (mono_meta_font, paint_accent_header,
                             paint_indicator_ring)

# list-row roles
ID_ROLE = QtCore.Qt.UserRole
HEADER_ROLE = QtCore.Qt.UserRole + 1
DATA_ROLE = QtCore.Qt.UserRole + 2
CAT_ROLE = QtCore.Qt.UserRole + 3
NAME_ROLE = QtCore.Qt.UserRole + 4
QUEUED_ROLE = QtCore.Qt.UserRole + 7

HEADER_W = 3000
HEADER_H = 26

LIST_ICON = 42
LIST_ROW_H = 32


def _craft_row_height(font: QtGui.QFont | None = None) -> int:
    """List-row height sized for the item name wrapping up to TWO rows."""
    f = font or QtWidgets.QApplication.font()
    fm = QtGui.QFontMetrics(f)
    return max(52, LIST_ICON + 10, LIST_ICON + fm.lineSpacing())


class CraftTileDelegate(QtWidgets.QStyledItemDelegate):
    """Paints the compact recipe rows (icon + name) and the per-job group
    bars that split a search's results, plus the gold QUEUED ring at the
    row's right padding when the recipe is on the craft list.
    """

    def paint(self, painter, option, index):
        if index.data(HEADER_ROLE):
            paint_accent_header(painter, option, index.data() or "")
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
        mf = mono_meta_font(12)
        mw = QtGui.QFontMetrics(mf).horizontalAdvance(meta) if meta else 0
        name = opt.text
        opt.text = ""          # the style draws bg + icon only; text is ours
        widget = option.widget
        style = widget.style() if widget else QtWidgets.QApplication.style()
        style.drawControl(QtWidgets.QStyle.CE_ItemViewItem, opt, painter, widget)

        tr = style.subElementRect(
            QtWidgets.QStyle.SE_ItemViewItemText, opt, widget)
        name_left = tr.left() + 4
        # Dynamic name width filling the full block up to the right-aligned meta
        right_reserve = mw + (36 if index.data(QUEUED_ROLE) else 20)
        name_w = max(160, option.rect.right() - name_left - right_reserve)

        if name:
            painter.save()
            nf = QtGui.QFont(opt.font)
            nf.setPixelSize(14)
            nf.setBold(True)
            painter.setFont(nf)
            painter.setPen(QtGui.QColor(
                opt.palette.color(QtGui.QPalette.Text)))
            name_rect = QtCore.QRect(name_left, option.rect.top(), name_w, option.rect.height())
            painter.drawText(name_rect, (QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter
                                         | QtCore.Qt.TextWordWrap), name)
            painter.restore()

        meta_x = option.rect.right() - right_reserve + 8
        if meta:
            painter.save()
            painter.setFont(mf)
            painter.setPen(QtGui.QColor(theme.ACCENT if r.get("job") == "Blacksmith" else theme.MUTED))
            meta_rect = QtCore.QRect(meta_x, option.rect.top(), mw + 4, option.rect.height())
            painter.drawText(meta_rect,
                             (QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter),
                             meta)
            painter.restore()
        if not index.data(QUEUED_ROLE):
            return
        ring_x = meta_x + mw + 14
        c = QtCore.QPointF(ring_x, option.rect.center().y())
        paint_indicator_ring(painter, c, radius=3.5, color=theme.GOLD, line_width=1.5)

