"""Reusable row widgets for the Entity HUD and the drop-table window.

`RowSpec` is the pure data description of one row; `_EntityRow` renders it
(icon tile + name + optional sub + right value, pooled); `_Section` groups
rows under a header; `_scroll_body` builds the shared scroll container.
"""
from __future__ import annotations

from dataclasses import dataclass

from PySide6 import QtCore, QtGui, QtWidgets

from .. import theme
from ..components import SectionHeader, IconTile
from ...core import chest_resolver


def _is_chest_orb_id(chest_id: str | None) -> bool:
    """ChestOrb / TimerCollectRun rows are event orbs, not loot chests — never
    list them under CHESTS in the entity HUD (pre-v0.3.5 behavior). End-chests
    (…_ChestOrb_3_Chest_7) are real loot chests and DO list, as do FightStone
    fight-spot chests.

    ⛔ ORB-CHEST ACTIVITY LOGIC — DO NOT EDIT.  Thin wrapper over
    chest_resolver.is_event_orb_id(); see docs/CHEST_ORB_LOGIC.md.  Do not
    change unless explicitly asked because a GAME UPDATE broke it."""
    if not chest_id:
        return False
    return chest_resolver.is_event_orb_id(chest_id)


@dataclass
class RowSpec:
    sheet: str | None
    id_: str | None
    accent: str | None
    name: str
    name_color: str
    sub: str = ""
    sub_color: str | None = None
    value: str = ""
    value_color: str | None = None
    bold: bool = False
    highlight: bool = False
    cb: object = None
    right_cb: object = None
    marker: str = ""      # map-marker icon name instead of a game-sheet icon
    marker_tint: str | None = None  # force-recolor the marker sprite (ores)
    icon_pixmap: object = None  # raw QPixmap to draw directly (merged custom icons)
    ui_icon: str = ""     # UI SVG icon name instead of a game-sheet icon
    outlined: bool = False   # use outlined style (minimap style) instead of tile
    outline_border: int = 2
    border_color: str | None = None  # draw a CSS border of this color around the icon
    extra_icon: object = None  # tuple: (sheet, id, marker, outlined)
    key: object = None         # (kind, key) for selection mapping
    bg_tint: str | None = None  # subtle full-row wash (e.g. faint accent tint)
    thin_ring: bool = False     # skip the dark halo on tinted-marker rings (thinner outline)
    ring: bool = True           # draw the accent ring around tinted markers (False = none)
    font_size: int | None = None
    size_override: int | None = None


class _EntityRow(QtWidgets.QFrame):
    """IconTile + name (+ optional mono sub-label) + right value. Pooled."""
    clicked = QtCore.Signal()
    rightClicked = QtCore.Signal()

    def __init__(self, icon_size: int):
        super().__init__()
        self._highlight, self._cb, self._right_cb = "", None, None
        self._bg_tint = None
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(6, 2, 6, 2)
        lay.setSpacing(9)
        self.icon_box = QtWidgets.QFrame()
        self.icon_box.setObjectName("IconBox")
        box_lay = QtWidgets.QHBoxLayout(self.icon_box)
        box_lay.setContentsMargins(0, 0, 0, 0)
        box_lay.setSpacing(0)
        self.tile = IconTile(icon_size)
        self.tile2 = IconTile(icon_size)
        self.tile2.hide()
        mid = QtWidgets.QVBoxLayout()
        mid.setContentsMargins(0, 0, 0, 0)
        mid.setSpacing(0)
        self.name, self.sub = QtWidgets.QLabel(), QtWidgets.QLabel()
        self.name.setWordWrap(True)
        self.name.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
        self.sub.setWordWrap(True)
        self.sub.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
        self.sub.setObjectName("Mono")
        mid.addWidget(self.name)
        mid.addWidget(self.sub)
        box_lay.addWidget(self.tile)
        box_lay.addWidget(self.tile2)
        self.value = QtWidgets.QLabel()
        self.value.setObjectName("Mono")
        self.value.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        self.value.setMinimumWidth(70)
        lay.addWidget(self.icon_box)
        lay.addLayout(mid, 1)
        lay.addWidget(self.value, 0)

    def apply(self, spec: RowSpec, icon_size: int):
        self._key = spec.key
        isz = spec.size_override if spec.size_override else icon_size
        icons_to_draw = [(self.tile, spec.sheet, spec.id_, spec.marker, spec.ui_icon, spec.outlined, spec.accent, spec.outline_border)]
        if spec.extra_icon:
            ex_sheet, ex_id, ex_marker, ex_outlined = spec.extra_icon[:4]
            ex_acc = spec.extra_icon[4] if len(spec.extra_icon) > 4 else spec.accent
            ex_border = spec.extra_icon[5] if len(spec.extra_icon) > 5 else 2
            icons_to_draw.append((self.tile2, ex_sheet, ex_id, ex_marker, None, ex_outlined, ex_acc, ex_border))

        for i, (t, s, id_, m, u, o, a, b) in enumerate(icons_to_draw):
            t.set_size(isz)
            t.show()
            if spec.icon_pixmap is not None:
                t.setPixmap(spec.icon_pixmap.scaled(
                    isz, isz, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation))
            elif u:
                t.set_ui_icon(u, a or theme.ACCENT)
            elif m:
                if spec.marker_tint and t is self.tile:
                    t.set_tinted_marker(m, spec.marker_tint, a,
                                        border=spec.outline_border,
                                        keyline=spec.thin_ring, ring=spec.ring)
                else:
                    t.set_marker(m, a, o, spec.outline_border)
            elif o:
                t.set_outlined(s, id_, a or theme.ACCENT, border=b)
            else:
                t.set(s, id_, a or theme.ACCENT)
        if not spec.extra_icon:
            self.tile2.hide()

        weight = "700" if spec.bold else "500"
        fs = f"font-size:{spec.font_size}px;" if (spec.font_size or 0) > 0 else ""
        self.name.setText(spec.name)
        if "<font" in spec.name or "<span" in spec.name:
            self.name.setStyleSheet(f"font-weight:{weight};{fs}background:transparent;")
        else:
            self.name.setStyleSheet(f"color:{spec.name_color};font-weight:{weight};{fs}background:transparent;")
        self.sub.setText(spec.sub)
        self.sub.setVisible(bool(spec.sub))
        if spec.sub:
            scol = spec.sub_color or theme.MUTED
            self.sub.setStyleSheet(f"color:{scol};{fs}background:transparent;")
        vcol = getattr(spec, "value_color", None) or spec.name_color
        self.value.setText(spec.value)
        self.value.setStyleSheet(f"color:{vcol};{fs}background:transparent;")
        self._highlight = spec.accent if spec.highlight else ""
        self._bg_tint = spec.bg_tint
        # Colored border via stylesheet — reliable, survives child-widget repaint
        bcol = getattr(spec, "border_color", None)
        if bcol != getattr(self, "_border_color", "__RESET__"):
            self._border_color = bcol
            if bcol:
                self.icon_box.setStyleSheet(f"QFrame#IconBox {{ border: 1px solid {bcol}; }}")
                self.icon_box.layout().setContentsMargins(2, 2, 2, 2)
            else:
                self.icon_box.setStyleSheet("QFrame#IconBox { border: none; }")
                self.icon_box.layout().setContentsMargins(0, 0, 0, 0)
        self.set_callback(spec.cb, spec.right_cb)
        self.update()

    def set_callback(self, cb, right_cb=None):
        if self._cb is not None:
            self.clicked.disconnect(self._cb)
            self._cb = None
        if cb is not None:
            self._cb = cb
            self.clicked.connect(cb)

        if self._right_cb is not None:
            self.rightClicked.disconnect(self._right_cb)
            self._right_cb = None
        if right_cb is not None:
            self._right_cb = right_cb
            self.rightClicked.connect(right_cb)

        self.setCursor(QtCore.Qt.PointingHandCursor if (cb or right_cb) else QtCore.Qt.ArrowCursor)

    def paintEvent(self, e):
        super().paintEvent(e)          # draw QSS background/frame first
        if self._bg_tint:
            p = QtGui.QPainter(self)
            c = QtGui.QColor(self._bg_tint)
            c.setAlpha(16)
            p.fillRect(self.rect(), c)
            p.end()
        if self._highlight:
            p = QtGui.QPainter(self)
            p.setRenderHint(QtGui.QPainter.Antialiasing, False)
            c = QtGui.QColor(self._highlight)
            bg = QtGui.QColor(c)
            bg.setAlpha(30)
            p.fillRect(self.rect(), bg)
            p.fillRect(0, 0, 2, self.height(), c)
            p.end()

    def mousePressEvent(self, e):
        if e.button() == QtCore.Qt.LeftButton:
            self.clicked.emit()
        elif e.button() == QtCore.Qt.RightButton:
            self.rightClicked.emit()
        super().mousePressEvent(e)


class _Section(QtWidgets.QWidget):
    def __init__(self, title, color):
        super().__init__()
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 5, 0, 5)
        lay.setSpacing(2)
        self.header = SectionHeader(title, color, colored_label=True)
        lay.addWidget(self.header)
        self.rows = QtWidgets.QVBoxLayout()
        self.rows.setSpacing(1)
        lay.addLayout(self.rows)
        self._pool: list[_EntityRow] = []

    def fill(self, specs: list[RowSpec], icon_size):
        while len(self._pool) < len(specs):
            r = _EntityRow(icon_size)
            self._pool.append(r)
            self.rows.addWidget(r)
        for i, spec in enumerate(specs):
            r = self._pool[i]
            r.apply(spec, icon_size)
            r.show()
        for j in range(len(specs), len(self._pool)):
            self._pool[j].hide()


def _scroll_body():
    scroll = QtWidgets.QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
    scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
    scroll.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
    body = QtWidgets.QWidget()
    lay = QtWidgets.QVBoxLayout(body)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(0)
    scroll.setWidget(body)
    return scroll, lay
