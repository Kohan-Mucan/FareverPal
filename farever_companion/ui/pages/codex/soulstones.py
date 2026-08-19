"""Soulstone summon-spot UI logic & list view for the Codex Dungeons tab.

The Soulstones view lists the 8 soulstone demon-boss summon spots (poi_locs)
the way the Dungeon List lists dungeons. A centered 2-column list rendered
inside the grid panel shows the demon's unit icon (outlined silver, like the
codex's elite badges), the demon name, and the summon zone.

Clicking a row tracks that summon spot exactly like clicking its marker on the
minimap (compass needle + Entity HUD WAYPOINT row with the subzone label) and
plots the labeled pin on the codex map; clicking the tracked cell again stops.
"""
from __future__ import annotations

import re

from PySide6 import QtCore, QtGui, QtWidgets

from ... import components as C
from ... import theme
from ....data import codex
from ....data import dungeons
from ....data import names
from ....data import units as udata


def _cost_label(poi: dict) -> str:
    """'1× Soulstone Tier 4' from the poi's cost_item/cost_count, or ''."""
    cost_item = poi.get("cost_item") or ""
    n = poi.get("cost_count") or 1
    m = re.search(r"_(\d+)$", cost_item)
    label = f"Soulstone Tier {m.group(1)}" if m else (cost_item or "Soulstone")
    return f"{n}× {label}"


class SoulstoneListCell(QtWidgets.QFrame):
    """One 2-column cell: gold-outlined boss icon + boss name + zone, the
    same shape as the dungeon list's cells (see DungeonListCell)."""

    # left-click tracks the summon spot; keep the app-wide copy menu off it
    _no_copy_menu = True
    pick = QtCore.Signal(object)  # poi row dict

    def __init__(self, ui, poi: dict, parent=None):
        super().__init__(parent)
        self.ui = ui
        self.poi = poi
        self.setCursor(QtCore.Qt.PointingHandCursor)
        # Flat: transparent, no border, no rounded box. Only a subtle hover
        # tint so the list reads as text, not a stack of boxes.
        self.setStyleSheet(
            "QFrame { background: transparent; border: 0; }"
            f"QFrame:hover {{ background: {theme.with_alpha(theme.TEXT, 10)}; }}")

        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(8, 4, 10, 4)
        lay.setSpacing(10)

        # Boss icon — the summon unit's sprite (half the codex-card icon size,
        # outlined to match the codex's unit badge colors). The soulstone
        # demons are ELITES, not bosses, so the outline is silver like the
        # codex cards (gold only for true bosses). Every soulstone demon has
        # a unit sprite; the gem marker is the fallback.
        boss_id = poi.get("spawn_unit") or ""
        icon = C.IconTile(size=42)
        if boss_id:
            accent = theme.SILVER if udata.is_elite(boss_id) else theme.GOLD
            icon.set_outlined("units", boss_id, accent=accent, border=2)
        else:
            icon.set_marker("soulstone", theme.KIND_COLOR.get("soulstone", "#e879f9"),
                            outlined=True)
        lay.addWidget(icon)

        txt = QtWidgets.QVBoxLayout()
        txt.setSpacing(1)
        txt.setContentsMargins(0, 0, 0, 0)
        txt.setAlignment(QtCore.Qt.AlignVCenter)

        self._name_lbl = QtWidgets.QLabel(poi.get("name") or boss_id or "?")
        self._name_lbl.setStyleSheet(f"color:{theme.TEXT}; font-size:14px; font-weight:bold;")
        # Let long names shrink instead of pushing the 2-column block wider
        # than the panel under the map.
        self._name_lbl.setMinimumWidth(0)
        txt.addWidget(self._name_lbl)

        zone = names.zone_name(poi.get("zone")) or ""
        self._sub_lbl = QtWidgets.QLabel(zone)
        self._sub_lbl.setStyleSheet(f"color:{theme.MUTED}; font-size:12px;")
        self._sub_lbl.setMinimumWidth(0)
        txt.addWidget(self._sub_lbl)

        # Tracked rows expand with a '◈ TRACKING' row below the name/sub —
        # same treatment as the dungeon list cells.
        self._track_lbl = QtWidgets.QLabel("◈ TRACKING")
        self._track_lbl.setStyleSheet(f"color:{theme.ACCENT}; font-size:11px; font-weight:bold;")
        self._track_lbl.setVisible(False)
        txt.addWidget(self._track_lbl)
        lay.addLayout(txt, 1)

        # Flat rows with subtle hover; no tooltips on the list.
        self.setToolTip("")
        self.refresh_tracked()

    def refresh_tracked(self) -> None:
        """Tracked rows: accent boss name + a '◈ TRACKING' row."""
        on = self.ui._is_soulstone_tracked(self.poi)
        accent = self.ui.s.hud_accent if hasattr(self.ui.s, "hud_accent") else theme.ACCENT
        self._track_lbl.setStyleSheet(f"color:{accent}; font-size:11px; font-weight:bold;")
        self._track_lbl.setVisible(on)
        self._name_lbl.setStyleSheet(
            f"color:{accent}; font-size:14px; font-weight:bold;" if on
            else f"color:{theme.TEXT}; font-size:14px; font-weight:bold;")

    def mousePressEvent(self, e):
        if e.button() == QtCore.Qt.LeftButton:
            self.pick.emit(self.poi)
        super().mousePressEvent(e)


class SoulstoneListView(QtWidgets.QFrame):
    """Centered 2-column grid of the soulstone summon spots; one widget
    spanning the codex grid when the 'Soulstones' toggle is active."""

    def __init__(self, ui, parent=None):
        super().__init__(parent)
        self.ui = ui
        self.row_count = 0
        self._cells: list[SoulstoneListCell] = []

        grid = QtWidgets.QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(4)
        # Center the 2-column block in the grid panel
        grid.setAlignment(QtCore.Qt.AlignHCenter | QtCore.Qt.AlignTop)

        # Zone filter (All / Z1 / Z2 keys on the Dungeons tab): only list the
        # summon spots whose zone is selected.
        zone = getattr(ui, "_dungeon_zone", "All") or "All"
        pois = [p for p in codex.soulstone_pois()
                if zone == "All" or dungeons.zone_tag_from_zone_id(p.get("zone") or "") == zone]
        for i, poi in enumerate(pois):
            cell = SoulstoneListCell(ui, poi)
            cell.pick.connect(ui._track_soulstone)
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


class SoulstoneMixin:
    """Track / plot / jump helpers for the Soulstones summon-spot list."""

    def _soulstone_track_key(self, poi: dict) -> str:
        """Tracker waypoint key for a soulstone summon spot ('x,y,z|boss|id'),
        exactly the shape a minimap click on the same marker builds, so
        is_tracked() and the Entity HUD waypoint row resolve it (boss name +
        subzone label)."""
        wp = poi.get("world_pos") or {}
        return (f"{float(wp.get('x', 0)):.1f},{float(wp.get('y', 0)):.1f},"
                f"{float(poi.get('z', 0)):.1f}|{poi.get('name') or poi.get('id')}|"
                f"{poi.get('id')}")

    def _is_soulstone_tracked(self, poi: dict) -> bool:
        """True when the tracker target is this soulstone summon spot."""
        tr = getattr(getattr(self, "overlay_mgr", None), "tracker", None)
        if tr is None:
            return False
        return tr.is_tracked("soulstone", self._soulstone_track_key(poi))

    def _track_soulstone(self, poi: dict) -> None:
        """Track a soulstone summon spot exactly like clicking its minimap
        marker (compass needle + Entity HUD waypoint row), and plot the same
        spot as a labeled pin on the codex map. Click again to stop — the
        untrack also clears the pin, so the second click leaves nothing."""
        tr = getattr(getattr(self, "overlay_mgr", None), "tracker", None)
        key = self._soulstone_track_key(poi)
        if tr is not None:
            tr.toggle("soulstone", key)
        if tr is not None and tr.is_tracked("soulstone", key):
            self._plot_soulstone_on_map(poi)
        else:
            self._clear_codex_map_selection()

    def _plot_soulstone_on_map(self, poi: dict) -> None:
        """Plot a single soulstone summon spot as a labeled pin (boss name
        under the marker) and report it in the info line."""
        if not hasattr(self, "_codex_map_widget"):
            return
        wp = poi.get("world_pos") or {}
        boss = poi.get("name") or poi.get("spawn_unit") or "Soulstone"
        s_col = theme.KIND_COLOR.get("soulstone", "#e879f9")
        self._codex_map_widget.set_multi_pins(boss, [{
            "x": wp.get("x", 0), "y": wp.get("y", 0),
            "color": QtGui.QColor(s_col),
            "name": boss,
            "show_label": True,
        }])
        if hasattr(self, "_map_info_lbl"):
            from ....data import names as _names
            zone = _names.zone_name(poi.get("zone")) or ""
            self._map_info_lbl.setText(f"{boss} — {zone}" if zone else boss)

    def _soulstone_pins(self, zone: str | None = None) -> list[dict]:
        """Labeled pin dicts for the soulstone summon spots (one per spot, or
        just `zone` when given) — the pins the Soulstones view plots."""
        pins = []
        s_col = QtGui.QColor(theme.KIND_COLOR.get("soulstone", "#e879f9"))
        for poi in codex.soulstone_pois():
            if zone and dungeons.zone_tag_from_zone_id(poi.get("zone") or "") != zone:
                continue
            wp = poi.get("world_pos") or {}
            pins.append({
                "x": wp.get("x", 0), "y": wp.get("y", 0),
                "color": s_col,
                "name": poi.get("name") or poi.get("id") or "Soulstone",
                "show_label": True,
            })
        return pins

    def _plot_soulstone_zone_pins(self, zone: str) -> None:
        """Plot the labeled pins for every soulstone summon spot in `zone` —
        the zone keys on the Soulstones view (All clears, like the dungeon
        zone keys)."""
        self._zone_pins_active = True
        if not hasattr(self, "_codex_map_widget"):
            return
        pins = self._soulstone_pins(zone)
        self._codex_map_widget.set_multi_pins(f"{zone} Soulstones", pins)
        if hasattr(self, "_map_info_lbl"):
            self._map_info_lbl.setText(f"{len(pins)} {zone} soulstones plotted")

    def _plot_all_soulstone_pins(self) -> None:
        """Plot every soulstone summon spot — the Soulstones view's pins
        button."""
        self._zone_pins_active = False
        if not hasattr(self, "_codex_map_widget"):
            return
        pins = self._soulstone_pins()
        self._codex_map_widget.set_multi_pins("All Soulstones", pins)
        if hasattr(self, "_map_info_lbl"):
            self._map_info_lbl.setText(f"{len(pins)} soulstones plotted")

    def _codex_open_soulstone(self, poi_id: str) -> None:
        """Reveal a soulstone summon spot in the Dungeons tab's Soulstones
        list: switch there, scroll to the row, and click it like a real user
        (tracks it + plots the labeled pin)."""
        self._codex_tabs.setCurrentText("Dungeons")
        btn = getattr(self, "_btn_dungeon_soulstones", None)
        if btn is not None and not btn.isChecked():
            btn.setChecked(True)   # fires the toggle -> Soulstones view
        self._refresh_codex_grid(reset_scroll=True)
        QtCore.QTimer.singleShot(
            0, lambda: self._codex_click_soulstone(poi_id))

    def _codex_click_soulstone(self, poi_id: str) -> None:
        """Scroll to a soulstone row in the open Soulstones list and click it
        like a real user (tracks it + plots the pin). No-op when the row isn't
        rendered (zone filter hides it)."""
        view = getattr(self, "_codex_soulstone_list", None)
        if view is None:
            return
        cell = next((c for c in view._cells
                     if c.poi.get("id") == poi_id), None)
        if cell is None:
            return
        self._codex_scroll.ensureWidgetVisible(cell, 0, 60)
        cell.pick.emit(cell.poi)
