"""Chip and compact-toggle controls: ChoiceChips, LabeledToggle, FilterChip,
SegmentedGrid, Field.

The toggle/chip family moved here out of `components.py`, which re-exports the
names so `C.FilterChip` / `C.SegmentedGrid` call sites keep working.
"""
from __future__ import annotations

from PySide6 import QtCore, QtWidgets
from shiboken6 import isValid as _is_valid

from . import theme
from .layout import FlowLayout
from .toggles import EyeToggle, ToggleSwitch


class ChoiceChips(QtWidgets.QWidget):
    """Labeled single-select chip row with an 'any' default option."""

    currentIndexChanged = QtCore.Signal(int)

    def __init__(self, options, any_label="Any", label="", parent=None, colors: dict | None = None):
        super().__init__(parent)
        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)
        if label:
            lbl = QtWidgets.QLabel(label.upper())
            lbl.setObjectName("FieldLabel")
            v.addWidget(lbl)
        row = FlowLayout(spacing=6)
        row.setContentsMargins(0, 0, 0, 0)
        v.addLayout(row)
        self._data = [""] + [d for d, _ in options]
        self._colors = colors or {}
        self._chips: list[QtWidgets.QPushButton] = []
        self._idx = 0
        for i, (_data, text) in enumerate([("", any_label)] + list(options)):
            chip = QtWidgets.QPushButton(text)
            chip.setCheckable(True)
            chip.setCursor(QtCore.Qt.PointingHandCursor)
            chip.clicked.connect(lambda _=False, ix=i: self._pick(ix))
            row.addWidget(chip)
            self._chips.append(chip)
        self._apply()

    def _pick(self, idx: int) -> None:
        if idx == self._idx:
            idx = 0
        if idx != self._idx:
            self._idx = idx
            self._apply()
            self.currentIndexChanged.emit(idx)

    def _apply(self) -> None:
        for i, chip in enumerate(self._chips):
            chip.setChecked(i == self._idx)
            chip.setStyleSheet(self._chip_qss(i == self._idx, self._colors.get(self._data[i])))

    @staticmethod
    def _chip_qss(active: bool, color: str | None = None) -> str:
        col = color or theme.ACCENT
        if active:
            return (
                f"QPushButton{{background:{theme.with_alpha(col, 36)};"
                f"color:{col};border:1px solid {col};"
                "border-radius:0;padding:4px 10px;font-weight:600;}")
        if color:
            return (
                f"QPushButton{{background:{theme.with_alpha(color, 14)};"
                f"color:{theme.with_alpha(color, 200)};"
                f"border:1px solid {theme.with_alpha(color, 90)};"
                "border-radius:0;padding:4px 10px;font-weight:600;}")
        return f"QPushButton{{background:transparent;color:{theme.DIM};border:1px solid {theme.BORDER};border-radius:0;padding:4px 10px;}}"

    def restyle(self) -> None:
        self._apply()

    def set_option_labels(self, labels: dict) -> None:
        for i, (d, chip) in enumerate(zip(self._data, self._chips)):
            if i > 0:
                chip.setText(labels.get(d, d))

    def set_visible_options(self, visible: set | None = None) -> None:
        for i, (d, chip) in enumerate(zip(self._data, self._chips)):
            chip.setVisible(True if i == 0 else (visible is None or d in visible))

    def currentIndex(self) -> int:
        return self._idx

    def currentData(self):
        return self._data[self._idx]

    def findData(self, data) -> int:
        try:
            return self._data.index(data)
        except ValueError:
            return -1

    def count(self) -> int:
        return len(self._chips)

    def setCurrentIndex(self, idx: int) -> None:
        if 0 <= idx < len(self._chips) and idx != self._idx:
            self._idx = idx
            self._apply()
            self.currentIndexChanged.emit(idx)


# --- labeled toggle (Cell row, for grids) ---------------------------------
class LabeledToggle(QtWidgets.QFrame):
    toggled = QtCore.Signal(bool)
    eye_toggled = QtCore.Signal(bool)

    def __init__(self, label: str, checked: bool = False, parent=None,
                 eye_icon: bool = False, eye_checked: bool = False,
                 eye_tooltip: str = "", label_style: str = ""):
        super().__init__(parent)
        self.setObjectName("Cell")
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(8)
        lbl = QtWidgets.QLabel(label)
        # Wrap long labels so the row never forces the page wider than the
        # viewport (the control-panel pages live in a no-horizontal-scroll area).
        lbl.setWordWrap(True)
        lbl.setStyleSheet(f"color:{theme.TEXT};background:transparent;{label_style}")
        self._toggle = ToggleSwitch(checked)
        self._toggle.toggled.connect(self.toggled.emit)
        lay.addWidget(lbl, 1, QtCore.Qt.AlignVCenter)
        self.eye_toggle = None
        if eye_icon:
            self.eye_toggle = EyeToggle(eye_checked)
            if eye_tooltip:
                self.eye_toggle.setToolTip(eye_tooltip)
            self.eye_toggle.toggled.connect(self.eye_toggled.emit)
            lay.addSpacing(4)
            lay.addWidget(self.eye_toggle, 0, QtCore.Qt.AlignVCenter)
            lay.addSpacing(6)
        lay.addWidget(self._toggle, 0, QtCore.Qt.AlignVCenter)

    def setChecked(self, on: bool) -> None:
        self._toggle.setChecked(on)

    def set_checked_silent(self, on: bool) -> None:
        try:
            if not _is_valid(self):
                return
            if hasattr(self, "_toggle") and self._toggle and _is_valid(self._toggle):
                self._toggle.set_checked_silent(on)
            self.setChecked(on)
        except (RuntimeError, Exception):
            pass


    def isChecked(self) -> bool:
        return self._toggle.isChecked()


# --- filter chip (compact checkable tag for multi-select filters) ----------
class FilterChip(QtWidgets.QPushButton):
    """Checked = included, unchecked = filtered out. Flat, sharp."""

    def __init__(self, label: str, checked: bool = True, parent=None,
                 color: str | None = None):
        super().__init__(label, parent)
        self._color = color
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.toggled.connect(lambda _on: self.restyle())
        self.restyle()

    def restyle(self) -> None:
        col = self._color or theme.ACCENT
        if not self.isEnabled():
            # A disabled chip used to keep its live styling and just swallow
            # the click, which reads as a broken control. The loadout's role
            # chips disable the roles a class cannot play, so the unavailable
            # ones have to LOOK unavailable. Checked still wins on top: a chip
            # that is both checked and disabled keeps its selected colour.
            if self.isChecked():
                self.setStyleSheet(
                    f"QPushButton{{background:{theme.with_alpha(col, 36)};"
                    f"color:{col};border:1px solid {col};"
                    f"border-radius:0;padding:4px 10px;font-weight:600;}}")
            else:
                self.setStyleSheet(
                    f"QPushButton{{background:transparent;"
                    f"color:{theme.with_alpha(theme.MUTED, 90)};"
                    f"border:1px solid {theme.with_alpha(theme.BORDER, 70)};"
                    f"border-radius:0;padding:4px 10px;font-weight:600;}}")
            return
        if self.isChecked():
            self.setStyleSheet(
                f"QPushButton{{background:{theme.with_alpha(col, 36)};"
                f"color:{col};border:1px solid {col};"
                f"border-radius:0;padding:4px 10px;font-weight:600;}}")
        elif self._color:
            self.setStyleSheet(
                f"QPushButton{{background:{theme.with_alpha(col, 12)};"
                f"color:{theme.with_alpha(col, 200)};"
                f"border:1px solid {theme.with_alpha(col, 80)};"
                f"border-radius:0;padding:4px 10px;font-weight:600;}}"
                f"QPushButton:hover{{background:{theme.with_alpha(col, 24)};color:{col};}}")
        else:
            self.setStyleSheet(
                f"QPushButton{{background:transparent;color:{theme.DIM};"
                f"border:1px solid {theme.BORDER};border-radius:0;padding:4px 10px;}}"
                f"QPushButton:hover{{border-color:{theme.MUTED};color:{theme.TEXT};}}")

    def set_checked_silent(self, on: bool) -> None:
        try:
            if not _is_valid(self):
                return
            self.blockSignals(True)
            self.setChecked(on)
            self.blockSignals(False)
            self.restyle()
        except (RuntimeError, Exception):
            pass

    def set_color(self, color: str | None) -> None:
        """Recolour the chip's checked accent (None = the neutral unchecked
        style for a chip that is not the picked one) and restyle it. Used by
        radio-style rows: when every chip shares one colour, a row where only
        one is picked reads as if several were."""
        self._color = color
        self.restyle()


# --- segmented grid (multi-row toggle grid in a QFrame#Cell container) -----
class SegmentedGrid(QtWidgets.QFrame):
    """A multi-row / multi-column grid of toggle buttons housed in a QFrame#Cell
    container with exact SegmentedControl / Entity Loot Filter styling and inset padding."""

    def __init__(self, items: list[tuple[str, str, bool, callable]], cols: int = 3, parent=None):
        super().__init__(parent)
        self.setObjectName("Cell")
        grid = QtWidgets.QGridLayout(self)
        grid.setContentsMargins(3, 3, 3, 3)
        grid.setHorizontalSpacing(3)
        grid.setVerticalSpacing(3)
        self._btns: dict[str, QtWidgets.QPushButton] = {}

        for i, (key, label, checked, cb) in enumerate(items):
            b = QtWidgets.QPushButton(label)
            b.setCheckable(True)
            b.setCursor(QtCore.Qt.PointingHandCursor)
            b.setFixedHeight(32)
            b.setStyleSheet(self._btn_qss())
            b.setChecked(checked)
            if cb:
                b.toggled.connect(cb)
            b.toggled.connect(lambda _on, btn=b: (_is_valid(btn) and btn.setStyleSheet(self._btn_qss())) if _is_valid(btn) else None)
            # Provide set_checked_silent on the button itself for easy sync & test compatibility
            def _silent_set(on: bool, btn=b) -> None:
                try:
                    if _is_valid(btn):
                        btn.blockSignals(True)
                        btn.setChecked(on)
                        btn.blockSignals(False)
                        btn.setStyleSheet(self._btn_qss())
                except (RuntimeError, Exception):
                    pass
            b.set_checked_silent = _silent_set
            grid.addWidget(b, i // cols, i % cols)
            self._btns[key] = b

    def _btn_qss(self) -> str:
        return theme.checkable_btn_qss()

    def btn(self, key: str) -> QtWidgets.QPushButton | None:
        return self._btns.get(key)

    def set_checked_silent(self, key: str, on: bool) -> None:
        b = self._btns.get(key)
        if b is not None and _is_valid(b):
            try:
                b.blockSignals(True)
                b.setChecked(on)
                b.blockSignals(False)
                b.setStyleSheet(self._btn_qss())
            except (RuntimeError, Exception):
                pass

    def restyle(self) -> None:
        for b in self._btns.values():
            if b is not None and _is_valid(b):
                try:
                    b.setStyleSheet(self._btn_qss())
                except (RuntimeError, Exception):
                    pass


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


