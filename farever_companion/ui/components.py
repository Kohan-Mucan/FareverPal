"""Reusable styled widgets, the Tactical Overlay component library.

Matches the user's exported Stitch design system (sharp 0px geometry,
rectangular toggles with square thumbs, layered charcoal surfaces, cyan section
labels). Painting-heavy controls are plain QWidgets + QPainter; the rest lean on
the QSS object names in theme.py.

This module is also the one-stop namespace for the widget families that were
split out (`tabs`, `cards`, `chips`, `toggles`): they are re-exported below and
listed in `__all__`, which is what marks them as API for a linter instead of
leaving them looking like unused imports.
"""
from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from . import theme
from .widgets import GlyphButton
from ..data import icons


class ElideLabel(QtWidgets.QLabel):
    """QLabel that elides its text to "…" when narrower than the text,
    instead of stretching the layout. Long skill/player names otherwise make
    rows reflow whenever text changes (e.g. damage totals climbing each tick).
    The full text stays in the tooltip."""

    def __init__(self, text: str = "", max_width: int = 300, parent=None,
                 show_tooltip: bool = True):
        super().__init__("", parent)
        self._full = text or ""
        self._busy = False
        self._show_tooltip = show_tooltip
        self.setMaximumWidth(max_width)
        if show_tooltip:
            self.setToolTip(self._full)
        self._apply(force=True)

    def set_full(self, text: str):
        self._full = text or ""
        if self._show_tooltip:
            self.setToolTip(self._full)
        self._apply()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._apply()

    def _apply(self, force: bool = False):
        if self._busy:
            return
        self._busy = True
        try:
            avail = self.width() if not force else self.maximumWidth()
            if avail <= 0:
                return
            shown = self.fontMetrics().elidedText(
                self._full or "", QtCore.Qt.ElideRight, avail)
            if super().text() != shown:
                super().setText(shown)
        finally:
            self._busy = False


# --- section header (cyan tick + cyan uppercase mono label) ---------------
class SectionHeader(QtWidgets.QWidget):
    """A 4px accent tick + uppercase mono label, optional right-aligned tag.

    The label is colored (`color`, default cyan), control-panel sections are
    cyan; the colored HUD headers pass DANGER / GOLD / ACCENT. With
    `collapsible=True` the header shows a fold arrow and emits
    `collapsedChanged` when clicked, so callers can hide a body widget.
    """

    collapsedChanged = QtCore.Signal(bool)

    def __init__(self, text: str, color: str | None = None,
                 colored_label: bool = True, tag: str = "", parent=None,
                 collapsible: bool = False):
        super().__init__(parent)
        # Resolve the live accent at construction, NOT as a default arg (which is
        # bound once at import and would stay the stale design cyan after the user
        # picks a Highlight color before the UI is built).
        color = color or theme.ACCENT
        self._color = color
        self._collapsible = collapsible
        self._collapsed = False
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
        lay.addWidget(self._tick, 0, QtCore.Qt.AlignVCenter)
        lay.addWidget(self._label, 0, QtCore.Qt.AlignVCenter)
        lay.addStretch(1)
        lay.addWidget(self._tag, 0, QtCore.Qt.AlignVCenter)
        self._tag.setVisible(bool(tag))
        if collapsible:
            self._arrow = QtWidgets.QLabel("▾")
            self._arrow.setObjectName("Mono")
            lay.addWidget(self._arrow, 0, QtCore.Qt.AlignVCenter)
            self.setCursor(QtCore.Qt.PointingHandCursor)
        self.set_color(color)

    def set_color(self, color: str) -> None:
        self._color = color
        self._tick.setStyleSheet(f"background:{color};border:0;")
        self._label.setStyleSheet(f"color:{color};")
        self._tag.setStyleSheet(f"color:{color};")
        if self._collapsible:
            self._arrow.setStyleSheet(f"color:{color};")

    def set_text(self, text: str) -> None:
        self._label.setText(text.upper())

    def set_tag(self, text: str) -> None:
        self._tag.setText(text)
        self._tag.setVisible(bool(text))

    def set_collapsed(self, collapsed: bool) -> None:
        """Collapse (hides the body) or expand; emits `collapsedChanged` on change."""
        if not self._collapsible or collapsed == self._collapsed:
            return
        self._collapsed = collapsed
        self._arrow.setText("▸" if collapsed else "▾")
        self.collapsedChanged.emit(collapsed)

    def is_collapsed(self) -> bool:
        return self._collapsed

    def mousePressEvent(self, e: QtGui.QMouseEvent) -> None:
        if self._collapsible and e.button() == QtCore.Qt.LeftButton:
            self.set_collapsed(not self._collapsed)
        super().mousePressEvent(e)


# --- stepper [- value +] --------------------------------------------------
class Stepper(QtWidgets.QFrame):
    valueChanged = QtCore.Signal(int)

    def __init__(self, value: int = 0, lo: int = 0, hi: int = 100, step: int = 1,
                 suffix: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("Cell")
        self.setFixedHeight(32)
        self._lo, self._hi, self._step, self._suffix = lo, hi, step, suffix
        self._v = max(lo, min(hi, value))
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(4, 0, 4, 0)
        lay.setSpacing(0)
        self._minus = self._mk_btn("−")
        self._plus = self._mk_btn("+")
        self._val = QtWidgets.QLineEdit()
        self._val.setAlignment(QtCore.Qt.AlignCenter)
        self._val.setMinimumWidth(28)
        self._val.editingFinished.connect(self._commit_edit)
        self._style_val()
        self._minus.clicked.connect(lambda: self._bump(-self._step))
        self._plus.clicked.connect(lambda: self._bump(self._step))
        lay.addWidget(self._minus, 0, QtCore.Qt.AlignVCenter)
        lay.addWidget(self._val, 1, QtCore.Qt.AlignVCenter)
        lay.addWidget(self._plus, 0, QtCore.Qt.AlignVCenter)
        self._render()

    def _style_val(self) -> None:
        self._val.setStyleSheet(
            f'color:{theme.ACCENT};font-family:"{theme.MONO_FONT}", "Consolas", monospace;'
            "font-size:14px;font-weight:600;background:transparent;border:0;padding:0;text-align:center;")

    def restyle(self) -> None:
        """Re-apply the accent-colored value label after the theme accent
        changes (the prominent accent on the stepper)."""
        self._style_val()

    def _mk_btn(self, text: str) -> QtWidgets.QPushButton:
        b = GlyphButton(text)
        b.setFixedSize(22, 28)
        return b

    def _bump(self, d: int) -> None:
        self.setValue(self._v + d)

    def _commit_edit(self) -> None:
        """Commit a typed value (clamped to lo..hi; junk input reverts)."""
        try:
            self.setValue(int(self._val.text().strip()))
        except ValueError:
            self.setValue(self._v)

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

    def __init__(self, options: list[str], current: str | None = None,
                 parent=None, colors: dict[str, str] | None = None):
        super().__init__(parent)
        self.setObjectName("Cell")
        self._colors = colors or {}
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(3, 3, 3, 3)
        lay.setSpacing(3)
        self._group = QtWidgets.QButtonGroup(self)
        self._group.setExclusive(True)
        self._btns: dict[str, QtWidgets.QPushButton] = {}
        for opt in options:
            b = QtWidgets.QPushButton(opt)
            b.setObjectName("SegmentBtn")
            b.setCheckable(True)
            b.setCursor(QtCore.Qt.PointingHandCursor)
            b.setStyleSheet(self._btn_qss(opt))
            lay.addWidget(b, 1)
            self._group.addButton(b)
            self._btns[opt] = b
        self._group.buttonClicked.connect(
            lambda b: self.currentChanged.emit(self._opt_of(b)))
        self.setCurrentText(current or (options[0] if options else ""))

    def _opt_of(self, b: QtWidgets.QPushButton) -> str:
        """The option key for a segment button — the tab's identity is the
        original option text, even when set_text changes the display (e.g.
        a live count appended to a tab)."""
        for opt, btn in self._btns.items():
            if btn == b or btn is b:
                return opt
        return b.text()

    def _btn_qss(self, opt: str = "") -> str:
        return theme.segment_btn_qss(self._colors.get(opt))

    def setCurrentText(self, text: str) -> None:
        if not text:
            return
        target_b = self._btns.get(text)
        if not target_b:
            for opt, b in self._btns.items():
                if opt.lower() == str(text).lower():
                    target_b = b
                    break
        if target_b:
            target_b.setChecked(True)

    def currentText(self) -> str:
        b = self._group.checkedButton()
        return self._opt_of(b) if b else ""

    def set_text(self, opt: str, text: str) -> None:
        """Change a tab's displayed text (e.g. append a live count) without
        changing its option key — selection, currentChanged and history
        keep using the original option text."""
        b = self._btns.get(opt)
        if b is not None:
            b.setText(text)

    def clear(self) -> None:
        """Uncheck the active segment (Qt ignores unchecking a checked button
        in an exclusive group, so lift exclusivity for the uncheck only)."""
        b = self._group.checkedButton()
        if b:
            self._group.setExclusive(False)
            b.setChecked(False)
            self._group.setExclusive(True)

    def restyle(self) -> None:
        """Re-apply the button QSS so the checked tab follows the theme accent
        (or each option's own color when one is set)."""
        for opt, b in self._btns.items():
            b.setStyleSheet(self._btn_qss(opt))


# --- underline tabs (left-accent, sharp) — extracted to ui/tabs.py ---------
from .tabs import UnderlineTabs


# --- slider row (label + value above a full-width slider) ------------------
class _MarkedSlider(QtWidgets.QSlider):
    def __init__(self, orientation, default_val=None, parent=None):
        super().__init__(orientation, parent)
        self._default_val = default_val

    def paintEvent(self, event):
        super().paintEvent(event)
        if self._default_val is not None:
            p = QtGui.QPainter(self)
            opt = QtWidgets.QStyleOptionSlider()
            self.initStyleOption(opt)
            gr = self.style().subControlRect(QtWidgets.QStyle.CC_Slider, opt, QtWidgets.QStyle.SC_SliderGroove, self)
            lo, hi = self.minimum(), self.maximum()
            if hi > lo:
                hw = 12 # handle width
                p.setPen(QtGui.QPen(QtGui.QColor(theme.MUTED), 1))
                curr = lo
                while curr <= hi:
                    if curr % 10 == 0 and curr != self._default_val:
                        frac = (curr - lo) / (hi - lo)
                        x = gr.left() + (hw / 2) + frac * (gr.width() - hw)
                        p.drawLine(int(x), gr.top() + 1, int(x), gr.bottom() - 1)
                    curr += 1
                frac = (self._default_val - lo) / (hi - lo)
                x = gr.left() + (hw / 2) + frac * (gr.width() - hw)
                p.setPen(QtGui.QPen(QtGui.QColor(theme.ACCENT), 2))
                p.drawLine(int(x), gr.top() - 4, int(x), gr.bottom() + 4)
            p.end()


class SliderRow(QtWidgets.QWidget):
    valueChanged = QtCore.Signal(int)

    def __init__(self, label: str, lo: int, hi: int, value: int, fmt=str, default_val: int = None, parent=None):
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
            f'color:{theme.ACCENT};font-family:"{theme.MONO_FONT}", "Consolas", monospace;'
            "font-size:11px;background:transparent;")
        top.addWidget(lbl)
        top.addStretch(1)
        top.addWidget(self._val)
        self._slider = _MarkedSlider(QtCore.Qt.Horizontal, default_val=default_val)
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

    def setValue(self, v: int) -> None:
        self._slider.setValue(v)

    def setValueSilent(self, v: int) -> None:
        self._slider.blockSignals(True)
        self._slider.setValue(v)
        self._slider.blockSignals(False)
        self._render(v)

    def restyle(self) -> None:
        """Re-tint the value readout after the theme accent changes."""
        self._val.setStyleSheet(
            f'color:{theme.ACCENT};font-family:"{theme.MONO_FONT}", "Consolas", monospace;'
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
        self.setAlignment(QtCore.Qt.AlignCenter)

    def set(self, sheet: str | None, id_: str | None, accent: str = theme.ACCENT) -> None:
        self.setPixmap(icons.tile(sheet, id_, self._size, accent))

    def set_outlined(self, sheet: str | None, id_: str | None, accent: str = theme.ACCENT, border: int = 2) -> None:
        """Set a game icon with an accent border hugging the icon's organic shape.
        Matches the minimap style."""
        self.setPixmap(icons.outlined(sheet, id_, self._size, accent, border=border))

    def set_marker(self, name: str, accent: str | None = None, outlined: bool = False,
                   border: int = 2) -> None:
        """A map-marker icon (assets/map_icons) instead of a game-sheet icon."""
        acc = accent if outlined else (accent or theme.ACCENT)
        pm = icons.tile_marker(name, self._size, acc, outlined=outlined,
                               border=border)
        # Missing marker assets degrade to an empty pixmap, never None
        # (a None here would crash QLabel.setPixmap in the Entity HUD).
        if pm is not None:
            self.setPixmap(pm)

    def set_tinted_marker(self, name: str, color: str, accent: str | None = None,
                           border: int = 2, keyline: bool = True,
                           ring: bool = True) -> None:
        """A map-marker sprite force-recolored to `color`, shape preserved
        (the single grey ore sprite becoming per-type copper/tin/… art),
        with an optional accent ring drawn around it for readability.

        `border` sets the ring thickness; `keyline=False` drops the dark halo
        so the ring reads as a single thin colored line instead of ~2px.
        `ring=False` skips the ring entirely — just the tinted sprite."""
        pm = icons.tinted_asset(name, color, self._size)
        if pm is None or pm.isNull():
            if accent:
                self.set_marker(name, accent)
            return
        QtGui, QtCore = icons._qt()
        if accent and ring:
            ox, oy = (self._size - pm.width()) // 2, (self._size - pm.height()) // 2
            out = QtGui.QPixmap(self._size, self._size)
            out.fill(QtGui.QColor(0, 0, 0, 0))
            p = QtGui.QPainter(out)
            p.setRenderHint(QtGui.QPainter.Antialiasing)
            ring = icons._silhouette(pm, accent)
            b = border
            if keyline:
                keyline_px = icons._silhouette(pm, "#0b0e14")
                for dx, dy in icons._disk_offsets(b + 1):
                    p.drawPixmap(ox + dx, oy + dy, keyline_px)
            for dx, dy in icons._disk_offsets(b):
                p.drawPixmap(ox + dx, oy + dy, ring)
            p.drawPixmap(ox, oy, pm)
            p.end()
            pm = out
        self.setPixmap(pm)

    def set_ui_icon(self, name: str, accent: str = theme.ACCENT) -> None:
        """A UI SVG icon (assets/icons_ui) instead of a game-sheet icon."""
        self.setPixmap(icons.tile_ui(name, self._size, accent))

    def set_achievement(self, accent: str = theme.ACCENT) -> None:
        """A drawn trophy glyph (the app ships no achievement icon asset)."""
        self.setPixmap(icons.achievement_marker(self._size, accent))

    def set_size(self, size: int) -> None:
        self._size = size
        self.setFixedSize(size, size)


# --- copy button (small flat COPY chip) ------------------------------------

def copy_button(text: str = "COPY") -> QtWidgets.QPushButton:
    """The small flat clipboard button: a dim accent chip that reads COPY and
    flashes its own confirmation on click.

    The craft bill's Total Needed list and the Items gear-farm list each built
    this by hand; this is the look in one place. The caller owns the click (and
    the flash), so only the button's appearance lives here."""
    btn = QtWidgets.QPushButton(text)
    btn.setCursor(QtCore.Qt.PointingHandCursor)
    btn.setStyleSheet(
        f"QPushButton{{color:{theme.MUTED};"
        f"background:{theme.with_alpha(theme.ACCENT, 12)};"
        f"border:1px solid {theme.BORDER};"
        "border-radius:4px;padding:2px 9px;font-size:10px;"
        "font-weight:700;letter-spacing:1px;}"
        f"QPushButton:hover{{color:{theme.TEXT};"
        f"background:{theme.with_alpha(theme.ACCENT, 30)};}}")
    return btn


# --- search input --------------------------------------------------------
class SearchInput(QtWidgets.QLineEdit):
    """Standardized search line edit with clear button and placeholder text."""

    def __init__(self, placeholder: str = "Search…", on_text_changed=None,
                 parent=None):
        super().__init__(parent)
        self.setPlaceholderText(placeholder)
        self.setClearButtonEnabled(True)
        if on_text_changed is not None:
            self.textChanged.connect(on_text_changed)

# --- extracted families (defined in ui/toggles.py, ui/cards.py, ui/chips.py)
# Re-exported so `components.X` stays the one-stop component namespace, and
# declared in __all__ for that same reason: a linter cannot see a re-export, so
# without this every one of these reads as an unused import - and a dozen
# phantom "unused imports" is what hides the real ones.
from .cards import ColorSwatch, InfoCard, NavItem, OverlayCard
from .chips import Field, FilterChip, LabeledToggle, SegmentedGrid
from .toggles import EyeToggle, ToggleSwitch

__all__ = [
    "UnderlineTabs",                     # ui/tabs.py
    "ColorSwatch", "InfoCard",           # ui/cards.py
    "NavItem", "OverlayCard",
    "Field", "FilterChip",               # ui/chips.py
    "LabeledToggle", "SegmentedGrid",
    "EyeToggle", "ToggleSwitch",         # ui/toggles.py
]
