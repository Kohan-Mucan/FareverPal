"""Toggle primitives: the rectangular switch and the eye toggle.

Split out of `components.py`, which re-exports them so every `C.ToggleSwitch`
/ `C.EyeToggle` call site keeps working.
"""
from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets
from shiboken6 import isValid as _is_valid

from . import theme
from ..data import icons


def _lerp(a: QtGui.QColor, b: QtGui.QColor, t: float) -> QtGui.QColor:
    t = max(0.0, min(1.0, t))
    return QtGui.QColor(
        round(a.red() + (b.red() - a.red()) * t),
        round(a.green() + (b.green() - a.green()) * t),
        round(a.blue() + (b.blue() - a.blue()) * t),
    )


# --- toggle switch (rectangular, square thumb) ----------------------------
class ToggleSwitch(QtWidgets.QAbstractButton):
    """Rectangular toggle. On = cyan track + dark square thumb (right);
    off = dark track + light thumb (left). Emits `toggled`."""
    _PAD = 2

    def __init__(self, checked: bool = False, w: int = 40, h: int = 20, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setFixedSize(w, h)
        self._pos = 1.0 if checked else 0.0
        self.setChecked(checked)
        self._anim = QtCore.QPropertyAnimation(self, b"knob", self)
        self._anim.setDuration(120)
        self.toggled.connect(self._animate)

    def _get_knob(self) -> float:
        return self._pos

    def _set_knob(self, v: float) -> None:
        self._pos = v
        self.update()

    knob = QtCore.Property(float, _get_knob, _set_knob)

    def _animate(self, on: bool) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def set_checked_silent(self, on: bool) -> None:
        try:
            if not _is_valid(self):
                return
            self.blockSignals(True)
            self.setChecked(on)
            self.blockSignals(False)
            self._pos = 1.0 if on else 0.0
            self.update()
        except (RuntimeError, Exception):
            pass

    def paintEvent(self, _e):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, False)   # crisp square edges
        w, h, pad = self.width(), self.height(), self._PAD
        track = _lerp(QtGui.QColor(theme.BORDER), QtGui.QColor(theme.ACCENT), self._pos)
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(track)
        p.drawRect(0, 0, w, h)
        thumb_w = h - 2 * pad
        travel = w - 2 * pad - thumb_w
        x = pad + self._pos * travel
        p.setBrush(_lerp(QtGui.QColor(theme.MUTED), QtGui.QColor(theme.TOGGLE_THUMB_ON), self._pos))
        p.drawRect(int(round(x)), pad, thumb_w, thumb_w)
        p.end()


class EyeToggle(QtWidgets.QAbstractButton):
    def __init__(self, checked: bool = False, size: int = 18, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setFixedSize(size, size)

    def paintEvent(self, _e):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        on = self.isChecked()
        name = "eye" if on else "eye-off"
        color = theme.ACCENT if on else theme.MUTED
        p.drawPixmap(0, 0, icons.ui_icon(name, color, self.width()))
        p.end()


