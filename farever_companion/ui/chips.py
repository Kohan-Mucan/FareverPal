"""Single-select chip rows with QComboBox-compatible API."""
from __future__ import annotations

from PySide6 import QtCore, QtWidgets

from . import theme
from .layout import FlowLayout


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
