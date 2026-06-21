"""Reusable styled widgets, the Tactical Overlay component library.

Matches the user's exported Stitch design system (sharp 0px geometry,
rectangular toggles with square thumbs, layered charcoal surfaces, cyan section
labels). Painting-heavy controls are plain QWidgets + QPainter; the rest lean on
the QSS object names in theme.py.
"""
from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from . import theme
from .widgets import ColorButton
from ..data import icons


def _lerp(a: QtGui.QColor, b: QtGui.QColor, t: float) -> QtGui.QColor:
    t = max(0.0, min(1.0, t))
    return QtGui.QColor(
        round(a.red() + (b.red() - a.red()) * t),
        round(a.green() + (b.green() - a.green()) * t),
        round(a.blue() + (b.blue() - a.blue()) * t),
    )


# --- section header (cyan tick + cyan uppercase mono label) ---------------
class SectionHeader(QtWidgets.QWidget):
    """A 4px accent tick + uppercase mono label, optional right-aligned tag.

    The label is colored (`color`, default cyan), control-panel sections are
    cyan; the colored HUD headers pass DANGER / GOLD / ACCENT."""

    def __init__(self, text: str, color: str | None = None,
                 colored_label: bool = True, tag: str = "", parent=None):
        super().__init__(parent)
        # Resolve the live accent at construction, NOT as a default arg (which is
        # bound once at import and would stay the stale design cyan after the user
        # picks a Highlight color before the UI is built).
        color = color or theme.ACCENT
        self._color = color
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(0, 2, 6, 2)
        lay.setSpacing(8)
        self._tick = QtWidgets.QFrame()
        self._tick.setFixedWidth(4)
        self._tick.setMinimumHeight(14)
        self._label = QtWidgets.QLabel(text.upper())
        self._label.setObjectName("Section")
        self._tag = QtWidgets.QLabel(tag)
        self._tag.setObjectName("Mono")
        lay.addWidget(self._tick)
        lay.addWidget(self._label)
        lay.addStretch(1)
        lay.addWidget(self._tag)
        self._tag.setVisible(bool(tag))
        self.set_color(color)

    def set_color(self, color: str) -> None:
        self._color = color
        self._tick.setStyleSheet(f"background:{color};border:0;")
        self._label.setStyleSheet(f"color:{color};")
        self._tag.setStyleSheet(f"color:{color};")

    def set_text(self, text: str) -> None:
        self._label.setText(text.upper())

    def set_tag(self, text: str) -> None:
        self._tag.setText(text)
        self._tag.setVisible(bool(text))


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
        self.blockSignals(True)
        self.setChecked(on)
        self.blockSignals(False)
        self._pos = 1.0 if on else 0.0
        self.update()

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


# --- stepper [- value +] --------------------------------------------------
class Stepper(QtWidgets.QFrame):
    valueChanged = QtCore.Signal(int)

    def __init__(self, value: int = 0, lo: int = 0, hi: int = 100, step: int = 1,
                 suffix: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("Cell")
        self.setFixedHeight(40)
        self._lo, self._hi, self._step, self._suffix = lo, hi, step, suffix
        self._v = max(lo, min(hi, value))
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(8, 0, 8, 0)
        lay.setSpacing(0)
        self._minus = self._mk_btn("−")
        self._plus = self._mk_btn("+")
        self._val = QtWidgets.QLabel()
        self._val.setAlignment(QtCore.Qt.AlignCenter)
        self._val.setMinimumWidth(48)
        self._style_val()
        self._minus.clicked.connect(lambda: self._bump(-self._step))
        self._plus.clicked.connect(lambda: self._bump(self._step))
        lay.addWidget(self._minus)
        lay.addWidget(self._val, 1)
        lay.addWidget(self._plus)
        self._render()

    def _style_val(self) -> None:
        self._val.setStyleSheet(
            f"color:{theme.ACCENT};font-family:'{theme.MONO_FONT}','Consolas';"
            "font-size:17px;font-weight:500;background:transparent;")

    def restyle(self) -> None:
        """Re-apply the accent-colored value label after the theme accent
        changes (the prominent accent on the stepper)."""
        self._style_val()

    def _mk_btn(self, text: str) -> QtWidgets.QPushButton:
        b = QtWidgets.QPushButton(text)
        b.setCursor(QtCore.Qt.PointingHandCursor)
        b.setFixedSize(26, 38)
        b.setStyleSheet(
            f"QPushButton{{background:transparent;border:0;color:{theme.MUTED};"
            "font-size:18px;font-weight:500;padding:0;}"
            f"QPushButton:hover{{color:{theme.ACCENT};}}"
            f"QPushButton:disabled{{color:{theme.BORDER};}}")
        return b

    def _bump(self, d: int) -> None:
        self.setValue(self._v + d)

    def _render(self) -> None:
        self._val.setText(f"{self._v}{self._suffix}")
        self._minus.setEnabled(self._v > self._lo)
        self._plus.setEnabled(self._v < self._hi)

    def setValue(self, v: int) -> None:
        v = max(self._lo, min(self._hi, int(v)))
        if v != self._v:
            self._v = v
            self._render()
            self.valueChanged.emit(v)
        else:
            self._render()

    def value(self) -> int:
        return self._v


# --- segmented control ----------------------------------------------------
class SegmentedControl(QtWidgets.QFrame):
    currentChanged = QtCore.Signal(str)

    def __init__(self, options: list[str], current: str | None = None, parent=None):
        super().__init__(parent)
        self.setObjectName("Cell")
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(3, 3, 3, 3)
        lay.setSpacing(3)
        self._group = QtWidgets.QButtonGroup(self)
        self._group.setExclusive(True)
        self._btns: dict[str, QtWidgets.QPushButton] = {}
        for opt in options:
            b = QtWidgets.QPushButton(opt)
            b.setCheckable(True)
            b.setCursor(QtCore.Qt.PointingHandCursor)
            b.setStyleSheet(self._btn_qss())
            lay.addWidget(b, 1)
            self._group.addButton(b)
            self._btns[opt] = b
        self._group.buttonClicked.connect(lambda b: self.currentChanged.emit(b.text()))
        self.setCurrentText(current or (options[0] if options else ""))

    def _btn_qss(self) -> str:
        return (
            f"QPushButton{{background:transparent;border:0;padding:7px 10px;"
            f"color:{theme.MUTED};font-weight:600;}}"
            f"QPushButton:hover{{color:{theme.TEXT};}}"
            f"QPushButton:checked{{background:{theme.with_alpha(theme.ACCENT,40)};"
            f"color:{theme.ACCENT};}}")

    def setCurrentText(self, text: str) -> None:
        b = self._btns.get(text)
        if b:
            b.setChecked(True)

    def currentText(self) -> str:
        b = self._group.checkedButton()
        return b.text() if b else ""

    def restyle(self) -> None:
        """Re-apply the button QSS so the checked tab follows the theme accent."""
        qss = self._btn_qss()
        for b in self._btns.values():
            b.setStyleSheet(qss)


# --- slider row (label + value above a full-width slider) ------------------
class SliderRow(QtWidgets.QWidget):
    valueChanged = QtCore.Signal(int)

    def __init__(self, label: str, lo: int, hi: int, value: int, fmt=str, parent=None):
        super().__init__(parent)
        self._fmt = fmt
        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(5)
        top = QtWidgets.QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        lbl = QtWidgets.QLabel(label.upper())
        lbl.setObjectName("FieldLabel")
        self._val = QtWidgets.QLabel()
        self._val.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        self._val.setStyleSheet(
            f"color:{theme.ACCENT};font-family:'{theme.MONO_FONT}','Consolas';"
            "font-size:11px;background:transparent;")
        top.addWidget(lbl)
        top.addStretch(1)
        top.addWidget(self._val)
        self._slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self._slider.setRange(lo, hi)
        self._slider.setValue(max(lo, min(hi, value)))
        v.addLayout(top)
        v.addWidget(self._slider)
        self._slider.valueChanged.connect(self._on)
        self._render(self._slider.value())

    def _render(self, v: int) -> None:
        self._val.setText(self._fmt(v))

    def _on(self, v: int) -> None:
        self._render(v)
        self.valueChanged.emit(v)

    def restyle(self) -> None:
        """Re-tint the value readout after the theme accent changes."""
        self._val.setStyleSheet(
            f"color:{theme.ACCENT};font-family:'{theme.MONO_FONT}','Consolas';"
            "font-size:11px;background:transparent;")

    def value(self) -> int:
        return self._slider.value()


# --- rarity tag (sharp) ---------------------------------------------------
class RarityTag(QtWidgets.QLabel):
    def __init__(self, rarity: str | None, parent=None):
        super().__init__(parent)
        self.setObjectName("Mono")
        self.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        self.set_rarity(rarity)

    def set_rarity(self, rarity: str | None) -> None:
        rarity = rarity or ""
        self.setText(rarity.upper())
        if not rarity:
            self.setStyleSheet("background:transparent;border:0;")
            return
        col = theme.rarity_color(rarity)
        self.setStyleSheet(
            f"color:{col};background:{theme.with_alpha(col, 26)};"
            f"border:1px solid {theme.with_alpha(col, 100)};"
            f"padding:2px 9px;font-weight:700;font-size:10px;letter-spacing:1px;")


# --- icon tile (real game icon on a sharp accent-tinted square) -----------
class IconTile(QtWidgets.QLabel):
    def __init__(self, size: int = 28, parent=None):
        super().__init__(parent)
        self._size = size
        self.setFixedSize(size, size)

    def set(self, sheet: str | None, id_: str | None, accent: str = theme.ACCENT) -> None:
        self.setPixmap(icons.tile(sheet, id_, self._size, accent))

    def set_marker(self, name: str, accent: str = theme.ACCENT) -> None:
        """A map-marker icon (assets/map_icons) instead of a game-sheet icon."""
        self.setPixmap(icons.tile_marker(name, self._size, accent))

    def set_ui_icon(self, name: str, accent: str = theme.ACCENT) -> None:
        """A UI SVG icon (assets/icons_ui) instead of a game-sheet icon."""
        self.setPixmap(icons.tile_ui(name, self._size, accent))

    def set_size(self, size: int) -> None:
        self._size = size
        self.setFixedSize(size, size)


# --- sidebar nav item -----------------------------------------------------
class NavItem(QtWidgets.QWidget):
    clicked = QtCore.Signal(str)

    def __init__(self, key: str, icon_name: str, label: str, parent=None):
        super().__init__(parent)
        self.key = key
        self._icon_name = icon_name
        self._selected = False
        self._hover = False
        self.setFixedHeight(40)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(16, 0, 12, 0)
        lay.setSpacing(12)
        self._icon = QtWidgets.QLabel()
        self._icon.setFixedSize(20, 20)
        self._text = QtWidgets.QLabel(label)
        lay.addWidget(self._icon)
        lay.addWidget(self._text)
        lay.addStretch(1)
        self._apply()

    def setSelected(self, on: bool) -> None:
        self._selected = on
        self._apply()

    def restyle(self) -> None:
        """Re-read the (now-changed) theme accent for the icon/label + repaint."""
        self._apply()

    def _apply(self) -> None:
        color = theme.ACCENT_LIGHT if self._selected else theme.MUTED
        self._icon.setPixmap(icons.ui_icon(self._icon_name, color, 20))
        weight = "600" if self._selected else "400"
        tcol = theme.ACCENT_LIGHT if self._selected else theme.MUTED
        self._text.setStyleSheet(f"color:{tcol};font-weight:{weight};background:transparent;")
        self.update()

    def enterEvent(self, e):
        self._hover = True; self.update(); super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover = False; self.update(); super().leaveEvent(e)

    def mousePressEvent(self, e):
        if e.button() == QtCore.Qt.LeftButton:
            self.clicked.emit(self.key)
        super().mousePressEvent(e)

    def paintEvent(self, _e):
        p = QtGui.QPainter(self)
        w, h = self.width(), self.height()
        if self._selected:
            p.fillRect(0, 0, w, h, QtGui.QColor(theme.HIGHEST))
            p.fillRect(0, 0, 2, h, QtGui.QColor(theme.ACCENT))
        elif self._hover:
            p.fillRect(0, 0, w, h, QtGui.QColor(theme.PANEL_HI))
        p.end()


# --- overlay card (big toggle) --------------------------------------------
class OverlayCard(QtWidgets.QFrame):
    toggled = QtCore.Signal(bool)

    def __init__(self, icon_name: str, title: str, desc: str, parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        self._active = False
        self._icon_name = icon_name
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(6)
        top = QtWidgets.QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        self._icon = QtWidgets.QLabel()
        self._icon.setFixedSize(28, 28)
        self._icon.setPixmap(icons.ui_icon(icon_name, theme.ACCENT, 28))
        self._toggle = ToggleSwitch()
        self._toggle.toggled.connect(self._on_toggle)
        top.addWidget(self._icon)
        top.addStretch(1)
        top.addWidget(self._toggle, 0, QtCore.Qt.AlignTop)
        t = QtWidgets.QLabel(title.upper())
        t.setStyleSheet(f"color:{theme.TEXT};font-weight:700;font-size:16px;background:transparent;")
        d = QtWidgets.QLabel(desc)
        d.setWordWrap(True)
        d.setStyleSheet(f"color:{theme.MUTED};font-size:12px;background:transparent;")
        lay.addLayout(top)
        lay.addWidget(t)
        lay.addWidget(d)

    def _on_toggle(self, on: bool) -> None:
        self._active = on
        self.update()
        self.toggled.emit(on)

    def setChecked(self, on: bool) -> None:
        self._toggle.setChecked(on)

    def set_checked_silent(self, on: bool) -> None:
        self._toggle.set_checked_silent(on)
        self._active = on
        self.update()

    def isChecked(self) -> bool:
        return self._toggle.isChecked()

    def restyle(self) -> None:
        """Re-tint the card icon after the theme accent changes."""
        self._icon.setPixmap(icons.ui_icon(self._icon_name, theme.ACCENT, 28))
        self.update()

    def setEnabled(self, on: bool) -> None:        # dim + disable the toggle when locked
        super().setEnabled(on)
        self._toggle.setEnabled(on)
        if on:
            self.setGraphicsEffect(None)
        else:
            eff = QtWidgets.QGraphicsOpacityEffect(self)
            eff.setOpacity(0.4)
            self.setGraphicsEffect(eff)

    def paintEvent(self, e):
        super().paintEvent(e)
        if self._active and self.isEnabled():
            p = QtGui.QPainter(self)
            w, h = self.width(), self.height()
            p.setPen(QtGui.QPen(QtGui.QColor(theme.ACCENT), 1))
            p.setBrush(QtCore.Qt.NoBrush)
            p.drawRect(0, 0, w - 1, h - 1)
            p.fillRect(0, 0, 3, h, QtGui.QColor(theme.ACCENT))   # left accent bar
            p.end()


# --- info card (icon + label + value) -------------------------------------
class InfoCard(QtWidgets.QFrame):
    def __init__(self, icon_name: str, label: str, value: str,
                 accent: str | None = None, parent=None):
        super().__init__(parent)
        accent = accent or theme.ACCENT
        self.setObjectName("Cell")
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(12)
        icon = QtWidgets.QLabel()
        icon.setFixedSize(20, 20)
        icon.setPixmap(icons.ui_icon(icon_name, accent, 20))
        col = QtWidgets.QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(2)
        lbl = QtWidgets.QLabel(label.upper())
        lbl.setObjectName("FieldLabel")
        self._value = QtWidgets.QLabel(value)
        self._value.setWordWrap(True)
        self._value.setStyleSheet(f"color:{theme.TEXT};font-size:13px;background:transparent;")
        col.addWidget(lbl)
        col.addWidget(self._value)
        lay.addWidget(icon, 0, QtCore.Qt.AlignTop)
        lay.addLayout(col, 1)

    def set_value(self, value: str) -> None:
        self._value.setText(value)


# --- color swatch (name chip + swatch) ------------------------------------
_COLOR_NAMES = {
    theme.ACCENT.lower(): "CYAN", theme.ACCENT_LIGHT.lower(): "CYAN",
    theme.GOLD.lower(): "GOLD", theme.ORANGE.lower(): "ORANGE",
    theme.DANGER.lower(): "RED", theme.GOOD.lower(): "GREEN", "#62ff88": "MINT",
}


class ColorSwatch(QtWidgets.QWidget):
    changed = QtCore.Signal(str)

    def __init__(self, color: str, parent=None):
        super().__init__(parent)
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        self._name = QtWidgets.QLabel()
        self._name.setObjectName("MonoText")
        self._btn = ColorButton(color)
        self._btn.setFixedSize(44, 24)
        self._btn.changed.connect(self._on_change)
        lay.addWidget(self._name)
        lay.addStretch(1)
        lay.addWidget(self._btn, 0, QtCore.Qt.AlignVCenter)
        self._set_name(color)

    def _set_name(self, color: str) -> None:
        self._name.setText(_COLOR_NAMES.get(color.lower(), color.upper()))

    def _on_change(self, color: str) -> None:
        self._set_name(color)
        self.changed.emit(color)

    def color(self) -> str:
        return self._btn.color()


# --- labeled toggle (Cell row, for grids) ---------------------------------
class LabeledToggle(QtWidgets.QFrame):
    toggled = QtCore.Signal(bool)

    def __init__(self, label: str, checked: bool = False, parent=None):
        super().__init__(parent)
        self.setObjectName("Cell")
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(8)
        lbl = QtWidgets.QLabel(label)
        # Wrap long labels so the row never forces the page wider than the
        # viewport (the control-panel pages live in a no-horizontal-scroll area).
        lbl.setWordWrap(True)
        lbl.setStyleSheet(f"color:{theme.TEXT};background:transparent;")
        self._toggle = ToggleSwitch(checked)
        self._toggle.toggled.connect(self.toggled.emit)
        lay.addWidget(lbl, 1)
        lay.addWidget(self._toggle, 0, QtCore.Qt.AlignTop)

    def setChecked(self, on: bool) -> None:
        self._toggle.setChecked(on)

    def set_checked_silent(self, on: bool) -> None:
        self._toggle.set_checked_silent(on)

    def isChecked(self) -> bool:
        return self._toggle.isChecked()


# --- filter chip (compact checkable tag for multi-select filters) ----------
class FilterChip(QtWidgets.QPushButton):
    """Checked = included, unchecked = filtered out. Flat, sharp."""

    def __init__(self, label: str, checked: bool = True, parent=None):
        super().__init__(label, parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.toggled.connect(lambda _on: self.restyle())
        self.restyle()

    def restyle(self) -> None:
        if self.isChecked():
            self.setStyleSheet(
                f"QPushButton{{background:{theme.with_alpha(theme.ACCENT, 36)};"
                f"color:{theme.ACCENT};border:1px solid {theme.ACCENT};"
                f"border-radius:0;padding:4px 10px;font-weight:600;}}")
        else:
            self.setStyleSheet(
                f"QPushButton{{background:transparent;color:{theme.DIM};"
                f"border:1px solid {theme.BORDER};border-radius:0;padding:4px 10px;}}")


# --- field (label above a control) ----------------------------------------
class Field(QtWidgets.QWidget):
    """UPPERCASE mono field label stacked above its control."""

    def __init__(self, label: str, control: QtWidgets.QWidget, parent=None):
        super().__init__(parent)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        lbl = QtWidgets.QLabel(label.upper())
        lbl.setObjectName("FieldLabel")
        lay.addWidget(lbl)
        lay.addWidget(control)
        self.control = control
