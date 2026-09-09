"""Card-shaped containers: sidebar nav row, overlay card, info card, swatch.

Split out of `components.py`, which re-exports them so every `C.OverlayCard`
/ `C.NavItem` / `C.InfoCard` / `C.ColorSwatch` call site keeps working.
"""
from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets
from shiboken6 import isValid as _is_valid

from . import theme
from .toggles import ToggleSwitch
from ..data import icons


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
        lay.setAlignment(QtCore.Qt.AlignVCenter)
        self._icon = QtWidgets.QLabel()
        self._icon.setFixedSize(20, 20)
        self._text = QtWidgets.QLabel(label)
        lay.addWidget(self._icon, 0, QtCore.Qt.AlignVCenter)
        lay.addWidget(self._text, 0, QtCore.Qt.AlignVCenter)
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


# --- overlay card (modular container frame) -------------------------------
class OverlayCard(QtWidgets.QFrame):
    toggled = QtCore.Signal(bool)
    bareToggled = QtCore.Signal(bool)
    transparentToggled = QtCore.Signal(bool)

    def __init__(self, icon_name: str, title: str, desc: str, bare_checked: bool = False,
                 has_bare: bool = True, bare_label: str = "Borderless",
                 transparent_checked: bool = False, has_transparent: bool = False,
                 parent=None):
        super().__init__(parent)
        self.setObjectName("OverlayCardFrame")
        self.setStyleSheet(
            f"QFrame#OverlayCardFrame {{ background-color: {theme.PANEL}; "
            f"border: 1px solid {theme.BORDER}; border-radius: 6px; }}")
        self._active = False
        self._icon_name = icon_name

        main_v = QtWidgets.QVBoxLayout(self)
        main_v.setContentsMargins(12, 10, 12, 10)
        main_v.setSpacing(8)

        # Header Row: Icon + Title + Main Switch
        hdr_row = QtWidgets.QHBoxLayout()
        hdr_row.setSpacing(8)

        self._icon = QtWidgets.QLabel()
        self._icon.setFixedSize(20, 20)
        pm = icons.ui_icon(icon_name, theme.ACCENT, 20)
        if not pm or pm.isNull():
            pm = icons.marker(icon_name, 20, accent=theme.ACCENT)
        self._icon.setPixmap(pm)
        hdr_row.addWidget(self._icon, 0, QtCore.Qt.AlignVCenter)

        self._title_lbl = QtWidgets.QLabel(title)
        self._title_lbl.setStyleSheet(
            f"color:{theme.TEXT};font-weight:700;font-size:15px;background:transparent;border:0;")
        hdr_row.addWidget(self._title_lbl, 1, QtCore.Qt.AlignVCenter)

        # Main Switch
        self._toggle = ToggleSwitch()
        self._toggle.toggled.connect(self._on_toggle)
        hdr_row.addWidget(self._toggle, 0, QtCore.Qt.AlignVCenter)
        main_v.addLayout(hdr_row)

        # Divider line
        div = QtWidgets.QFrame()
        div.setFixedHeight(1)
        div.setStyleSheet(f"background:{theme.BORDER};border:none;")
        main_v.addWidget(div)

        # Description
        desc_lbl = QtWidgets.QLabel(desc)
        desc_lbl.setWordWrap(True)
        desc_lbl.setStyleSheet(f"color:{theme.TEXT};font-size:12px;background:transparent;line-height:1.3;")
        main_v.addWidget(desc_lbl)

        # Options Row: Borderless / Transparent pills (styling only — the
        # behaviour switches live on the page that owns the feature).
        self._bare_toggle = None
        self._transparent_toggle = None
        if has_bare or has_transparent:
            opt_row = QtWidgets.QHBoxLayout()
            opt_row.setSpacing(6)
            opt_row.addStretch(1)

            if has_bare:
                self._bare_toggle = QtWidgets.QPushButton(bare_label)
                self._bare_toggle.setCheckable(True)
                self._bare_toggle.setChecked(bare_checked)
                self._bare_toggle.setCursor(QtCore.Qt.PointingHandCursor)
                self._bare_toggle.setFixedHeight(24)
                self._bare_toggle.setStyleSheet(self._pill_qss())
                self._bare_toggle.toggled.connect(self.bareToggled.emit)
                self._bare_toggle.toggled.connect(lambda _on, b=self._bare_toggle: b.setStyleSheet(self._pill_qss()))
                opt_row.addWidget(self._bare_toggle)

            if has_transparent:
                self._transparent_toggle = QtWidgets.QPushButton("Transparent")
                self._transparent_toggle.setCheckable(True)
                self._transparent_toggle.setChecked(transparent_checked)
                self._transparent_toggle.setCursor(QtCore.Qt.PointingHandCursor)
                self._transparent_toggle.setFixedHeight(24)
                self._transparent_toggle.setStyleSheet(self._pill_qss())
                self._transparent_toggle.toggled.connect(self.transparentToggled.emit)
                self._transparent_toggle.toggled.connect(lambda _on, b=self._transparent_toggle: b.setStyleSheet(self._pill_qss()))
                opt_row.addWidget(self._transparent_toggle)

            main_v.addLayout(opt_row)

    def _pill_qss(self) -> str:
        return (
            f"QPushButton {{"
            f"  background: {theme.with_alpha(theme.PANEL_HI, 40)};"
            f"  border: 1px solid {theme.BORDER};"
            f"  border-radius: 4px;"
            f"  padding: 3px 8px;"
            f"  color: {theme.MUTED};"
            f"  font-size: 11px;"
            f"  font-weight: 600;"
            f"}}"
            f"QPushButton:hover {{"
            f"  color: {theme.TEXT};"
            f"  background: {theme.with_alpha(theme.PANEL_HI, 90)};"
            f"  border-color: {theme.with_alpha(theme.ACCENT, 60)};"
            f"}}"
            f"QPushButton:checked {{"
            f"  background: {theme.with_alpha(theme.ACCENT, 45)};"
            f"  border: 1px solid {theme.ACCENT};"
            f"  color: {theme.ACCENT};"
            f"  font-weight: 600;"
            f"}}")

    def _on_toggle(self, on: bool) -> None:
        self._active = on
        self.update()
        self.toggled.emit(on)

    def setChecked(self, on: bool) -> None:
        self._toggle.setChecked(on)

    def set_checked_silent(self, on: bool) -> None:
        try:
            if not _is_valid(self):
                return
            if hasattr(self, "_toggle") and self._toggle and _is_valid(self._toggle):
                self._toggle.set_checked_silent(on)
            self._active = on
            self.update()
        except (RuntimeError, Exception):
            pass


    def set_bare_checked_silent(self, on: bool) -> None:
        if self._bare_toggle:
            self._bare_toggle.blockSignals(True)
            self._bare_toggle.setChecked(on)
            self._bare_toggle.blockSignals(False)
            self._bare_toggle.setStyleSheet(self._pill_qss())


    def set_transparent_checked_silent(self, on: bool) -> None:
        if self._transparent_toggle:
            self._transparent_toggle.blockSignals(True)
            self._transparent_toggle.setChecked(on)
            self._transparent_toggle.blockSignals(False)
            self._transparent_toggle.setStyleSheet(self._pill_qss())

    def isChecked(self) -> bool:
        return self._toggle.isChecked()

    def restyle(self) -> None:
        """Re-tint the card icon after the theme accent changes."""
        pm = icons.ui_icon(self._icon_name, theme.ACCENT, 20)
        if not pm or pm.isNull():
            pm = icons.marker(self._icon_name, 20, accent=theme.ACCENT)
        self._icon.setPixmap(pm)
        if self._bare_toggle:
            self._bare_toggle.setStyleSheet(self._pill_qss())
        if self._transparent_toggle:
            self._transparent_toggle.setStyleSheet(self._pill_qss())
        self.update()

    def setEnabled(self, on: bool) -> None:
        super().setEnabled(on)
        self._toggle.setEnabled(on)
        if on:
            self.setGraphicsEffect(None)
        else:
            eff = QtWidgets.QGraphicsOpacityEffect(self)
            eff.setOpacity(0.4)
            self.setGraphicsEffect(eff)


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
        lay.addWidget(icon, 0, QtCore.Qt.AlignVCenter)
        lay.addLayout(col, 1)

    def set_value(self, value: str) -> None:
        self._value.setText(value)


# --- color swatch (name chip + swatch) ------------------------------------
_COLOR_NAMES = {
    theme.ACCENT.lower(): "CYAN", theme.ACCENT_LIGHT.lower(): "CYAN",
    theme.GOLD.lower(): "GOLD", theme.ORANGE.lower(): "ORANGE",
    theme.DANGER.lower(): "RED", theme.GOOD.lower(): "GREEN", "#62ff88": "MINT",
}


class _ColorButton(QtWidgets.QPushButton):
    """A swatch button that opens a color picker and emits the chosen hex.
    Module-local: ColorSwatch below is its only user (it used to live in
    widgets.py as ColorButton, and was re-exported from components.py)."""

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


class ColorSwatch(QtWidgets.QWidget):
    changed = QtCore.Signal(str)

    def __init__(self, color: str, parent=None):
        super().__init__(parent)
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        self._name = QtWidgets.QLabel()
        self._name.setObjectName("MonoText")
        self._btn = _ColorButton(color)
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


