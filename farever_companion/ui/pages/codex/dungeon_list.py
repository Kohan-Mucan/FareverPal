"""Dungeon list view for the Codex Dungeons tab.

A centered 2-column list rendered *inside the grid panel* when the "Dungeon
List" toggle above the grid is active. One cell per dungeon: the boss icon
(half the codex-card icon size, gold-outlined) next to the boss name and
level · dungeon name. Flat rows (no boxes) with a subtle hover; tracked rows
tint the boss name the accent color and expand with a "◈ TRACKING" row.
Clicking a
cell tracks that dungeon's entrance exactly like clicking its marker on the
minimap (compass needle + Entity HUD WAYPOINT row); clicking the tracked
cell again stops. Rift cells track the ACTIVE / next-due rift via the
RiftTracker.

All data resolution lives in `CodexMapUiMixin` (`_dungeon_track_key`,
`_rift_live_track_key`, `_track_dungeon`); this file is a thin view.
"""
from __future__ import annotations

import os

from PySide6 import QtCore, QtGui, QtWidgets

from ... import components as C
from ... import theme
from ....data import dungeons


def _bare_icon(value) -> str:
    """'icons/units/Nepsilon.png' / 'X.prefab' -> bare id ('Nepsilon')."""
    v = value or ""
    if "/" in v or "\\" in v:
        v = os.path.splitext(os.path.basename(v))[0]
    elif "." in v:
        v = v.rsplit(".", 1)[0]
    return v


class _ElidedLabel(QtWidgets.QLabel):
    """QLabel that elides its text with '…' when it can't fit, instead of
    clipping or forcing the row wider than the panel. PySide6's QLabel has no
    setTextElideMode, so the text is painted manually; stylesheet-driven
    color/font are respected via the widget palette and font metrics."""

    def paintEvent(self, e) -> None:
        p = QtGui.QPainter(self)
        p.setPen(self.palette().color(self.foregroundRole()))
        p.drawText(
            self.rect(),
            QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
            self.fontMetrics().elidedText(self.text(), QtCore.Qt.ElideRight, self.width()),
        )
        p.end()


class DungeonListCell(QtWidgets.QFrame):
    """One 2-column cell: gold-outlined boss icon + boss/dungeon names."""

    pick = QtCore.Signal(object)  # dungeon dict

    def __init__(self, ui, dungeon: dict, key, parent=None):
        super().__init__(parent)
        self.ui = ui
        self.dungeon = dungeon
        self.key = key
        self.setCursor(QtCore.Qt.PointingHandCursor if key else QtCore.Qt.ArrowCursor)
        # Flat: transparent, no border, no rounded box. Only a subtle hover
        # tint so the list reads as text, not a stack of boxes.
        self.setStyleSheet(
            "QFrame { background: transparent; border: 0; }"
            f"QFrame:hover {{ background: {theme.with_alpha(theme.TEXT, 10)}; }}")

        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(8, 4, 10, 4)
        lay.setSpacing(10)

        # Boss icon — half the codex-card icons (80px -> 42px), with the gold
        # outline (organic glow that hugs the sprite, not a box).
        boss_id = dungeon.get("boss_id") or ""
        icon_id = _bare_icon(dungeon.get("boss_icon")) or boss_id
        icon = C.IconTile(size=42)
        if icon_id:
            icon.set_outlined("units", icon_id, accent=theme.GOLD, border=2)
        else:
            icon.set_marker("dungeon", theme.GOLD, outlined=True)
        lay.addWidget(icon)

        txt = QtWidgets.QVBoxLayout()
        txt.setSpacing(1)
        txt.setContentsMargins(0, 0, 0, 0)
        txt.setAlignment(QtCore.Qt.AlignVCenter)

        self._name_lbl = _ElidedLabel(dungeon.get("boss_name") or icon_id or "?")
        self._name_lbl.setStyleSheet(f"color:{theme.TEXT}; font-size:14px; font-weight:bold;")
        # Let long names shrink + elide instead of forcing the row (and the
        # 2-column block) wider than the panel under the map.
        self._name_lbl.setMinimumWidth(0)
        txt.addWidget(self._name_lbl)

        dname = dungeon.get("name") or ""
        if dungeon.get("entrance_zone") == "Rifts" and dname and not dname.lower().startswith("rift"):
            dname = f"Rift {dname}"
        lvl = dungeon.get("level")
        sub = dname if not lvl else f"Lv {lvl} · {dname}"
        self._sub_lbl = _ElidedLabel(sub)
        self._sub_lbl.setStyleSheet(f"color:{theme.MUTED}; font-size:12px;")
        self._sub_lbl.setMinimumWidth(0)
        txt.addWidget(self._sub_lbl)

        # Tracked rows expand with a '◈ TRACKING' row below the name/sub.
        # Growth is purely vertical, so the columns never move — no reserved
        # side slot needed, and untracked cells stay compact.
        self._track_lbl = QtWidgets.QLabel("◈ TRACKING")
        self._track_lbl.setStyleSheet(f"color:{theme.ACCENT}; font-size:11px; font-weight:bold;")
        self._track_lbl.setVisible(False)
        txt.addWidget(self._track_lbl)
        lay.addLayout(txt, 1)

        tip = sub
        if dungeon.get("entrance_zone") == "Rifts":
            tip += " — tracks the active rift (or the next one due)"
        elif not key:
            tip += " — no entrance location found"
            self._name_lbl.setStyleSheet(f"color:{theme.DIM}; font-size:14px; font-weight:bold;")
            self._sub_lbl.setStyleSheet(f"color:{theme.DIM}; font-size:12px;")
        self.setToolTip(tip)

        self.refresh_tracked()

    def refresh_tracked(self) -> None:
        """Tracked rows: accent boss name + a '◈ TRACKING' row that expands
        the cell vertically (columns never move)."""
        on = self.ui._is_dungeon_tracked(self.dungeon)
        accent = self.ui.s.hud_accent or theme.ACCENT
        self._track_lbl.setStyleSheet(f"color:{accent}; font-size:11px; font-weight:bold;")
        if not self.key:
            # Not clickable / no entrance location: never show the label.
            self._track_lbl.setVisible(False)
            return
        self._track_lbl.setVisible(on)
        self._name_lbl.setStyleSheet(
            f"color:{accent}; font-size:14px; font-weight:bold;" if on
            else f"color:{theme.TEXT}; font-size:14px; font-weight:bold;")

    def mousePressEvent(self, e):
        if e.button() == QtCore.Qt.LeftButton and self.key:
            self.pick.emit(self.dungeon)
        super().mousePressEvent(e)


class DungeonListView(QtWidgets.QFrame):
    """Centered 2-column grid of all dungeons; one widget spanning the codex
    grid when the 'Dungeon List' toggle is active."""

    def __init__(self, ui, parent=None):
        super().__init__(parent)
        self.ui = ui
        self.row_count = 0
        self._cells: list[DungeonListCell] = []

        grid = QtWidgets.QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(4)
        # Center the 2-column block in the grid panel
        grid.setAlignment(QtCore.Qt.AlignHCenter | QtCore.Qt.AlignTop)

        # Zone filter (All / Z1 / Z2 / Z3 keys on the Dungeons tab): only list
        # dungeons whose entrance (or rift spawn point) sits in the zone.
        zone = getattr(ui, "_dungeon_zone", "All") or "All"
        dungeons_to_show = [d for d in dungeons.load_dungeons()
                            if zone == "All" or dungeons.zone_tag(d) == zone]
        for i, d in enumerate(dungeons_to_show):
            key = ui._dungeon_track_key(d)
            cell = DungeonListCell(ui, d, key)
            cell.pick.connect(ui._track_dungeon)
            # AlignTop: when a tracked cell expands with its '◈ TRACKING' row,
            # only that cell grows downward — the sibling in the row stays at
            # its natural height instead of stretching with the taller row.
            grid.addWidget(cell, i // 2, i % 2, QtCore.Qt.AlignTop)
            self._cells.append(cell)
        self.row_count = len(self._cells)

        self._tr = getattr(getattr(ui, "overlay_mgr", None), "tracker", None)
        if self._tr is not None:
            self._tr.changed.connect(self.refresh_tracked)
        self.refresh_tracked()

    def refresh_tracked(self) -> None:
        if not self.isVisible():
            return
        for cell in self._cells:
            cell.refresh_tracked()
