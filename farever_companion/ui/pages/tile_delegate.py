"""Shared list and grid tile delegate painting helpers.

Provides common QPainter routines for category header bars, tag chips, mono metadata
fonts, indicator rings, and word-wrapped item titles across Items, Craft, and Codex views.
"""
from __future__ import annotations

from typing import Sequence
from PySide6 import QtCore, QtGui, QtWidgets

from .. import theme


def mono_meta_font(pixel_size: int = 11) -> QtGui.QFont:
    """Return a mono metadata font of given pixel size."""
    f = QtGui.QFont(theme.MONO_FONT, pixel_size)
    f.setPixelSize(pixel_size)
    return f


def paint_accent_header(painter: QtGui.QPainter, option: QtWidgets.QStyleOptionViewItem,
                        title: str, subtitle_segs: Sequence[tuple[str, str, int | str]] | None = None,
                        bg_alpha: int = 14) -> None:
    """Paint a full-width accent-tinted header bar with optional multi-segment colored subline."""
    painter.save()
    painter.setRenderHint(QtGui.QPainter.Antialiasing)
    r = option.rect
    painter.setPen(QtCore.Qt.NoPen)
    painter.setBrush(QtGui.QColor(theme.with_alpha(theme.ACCENT, bg_alpha)))
    painter.drawRoundedRect(r, 4, 4)

    f = QtGui.QFont(option.font)
    f.setBold(True)
    f.setPixelSize(12)
    painter.setFont(f)
    painter.setPen(QtGui.QColor(theme.MUTED))

    if not subtitle_segs:
        painter.drawText(r.adjusted(8, 0, -8, 0),
                         QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
                         title)
        painter.restore()
        return

    painter.drawText(r.adjusted(8, 3, -8, -r.height() // 2),
                     QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop, title)
    cf = QtGui.QFont(option.font)
    cf.setPixelSize(10)
    fm = QtGui.QFontMetrics(cf)
    y = r.bottom() - fm.height() - 3
    x = r.left() + 8
    painter.setFont(cf)
    for i, (lbl, col, n) in enumerate(subtitle_segs):
        txt = f"{lbl} {n}"
        tr = QtCore.QRect(x, y, fm.horizontalAdvance(txt) + 2, fm.height())
        painter.setPen(QtGui.QColor(col))
        painter.drawText(tr, QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop, txt)
        x += tr.width()
        if i < len(subtitle_segs) - 1:
            sep = " · "
            sr = QtCore.QRect(x, y, fm.horizontalAdvance(sep) + 2, fm.height())
            painter.setPen(QtGui.QColor(theme.MUTED))
            painter.drawText(sr, QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop, sep)
            x += sr.width()
    painter.restore()


def paint_tag_chips(painter: QtGui.QPainter, option: QtWidgets.QStyleOptionViewItem,
                    tags: Sequence[str], short_map: dict[str, str] | None = None,
                    max_visible: int = 2) -> None:
    """Paint small rounded tag / badge chips at the top-right corner of a tile."""
    if not tags:
        return
    short_map = short_map or {}
    f = QtGui.QFont(option.font)
    f.setPixelSize(10)
    f.setBold(True)
    fm = QtGui.QFontMetrics(f)
    x = option.rect.right() - 8
    y = option.rect.top() + 4

    painter.save()
    for t in tags[:max_visible]:
        w = fm.horizontalAdvance(t) + 10
        r = QtCore.QRect(x - w, y, w, 16)
        painter.setBrush(QtGui.QBrush(QtGui.QColor(theme.with_alpha(theme.ACCENT, 25))))
        painter.setPen(QtGui.QPen(QtGui.QColor(theme.with_alpha(theme.ACCENT, 90))))
        painter.drawRoundedRect(r, 3, 3)
        painter.setFont(f)
        painter.setPen(QtGui.QColor(theme.ACCENT))
        painter.drawText(r, QtCore.Qt.AlignCenter, short_map.get(t, t))
        x -= w + 4

    if len(tags) > max_visible:
        more = "+%d" % (len(tags) - max_visible)
        w = fm.horizontalAdvance(more) + 10
        r = QtCore.QRect(x - w, y, w, 16)
        painter.setBrush(QtGui.QBrush(QtGui.QColor(theme.with_alpha(theme.MUTED, 30))))
        painter.setPen(QtGui.QPen(QtGui.QColor(theme.with_alpha(theme.MUTED, 70))))
        painter.drawRoundedRect(r, 3, 3)
        painter.setFont(f)
        painter.setPen(QtGui.QColor(theme.MUTED))
        painter.drawText(r, QtCore.Qt.AlignCenter, more)
    painter.restore()


def paint_indicator_ring(painter: QtGui.QPainter, center: QtCore.QPointF,
                         radius: float = 2.5, color: str = theme.GOLD,
                         line_width: float = 1.5) -> None:
    """Paint an antialiased circular indicator ring (e.g. craft queue badge)."""
    painter.save()
    painter.setRenderHint(QtGui.QPainter.Antialiasing)
    pen = QtGui.QPen(QtGui.QColor(color), line_width)
    painter.setPen(pen)
    painter.setBrush(QtCore.Qt.NoBrush)
    painter.drawEllipse(center, radius, radius)
    painter.restore()
