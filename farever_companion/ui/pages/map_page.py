from __future__ import annotations

from PySide6 import QtWidgets
from .. import components as C
from ...data import loot
from ...data import icons
from ...geo import orbs as geo_orbs

class MapPageMixin:
    def _page_map(self):
        page, v = self._page_container()
        card = C.OverlayCard("map", "Open Minimap",
                             "Top-down POI radar centred on the player.")
        card.toggled.connect(lambda on: self._request_overlay("map", on))
        self._register_card("map", card)
        v.addWidget(card)

        v.addWidget(C.SectionHeader("Shape"))
        shape = C.SegmentedControl(["Circle", "Square"], self.s.minimap_shape)
        shape.currentChanged.connect(self._set_minimap_shape)
        v.addWidget(shape)
        rot = C.LabeledToggle("Rotate with player heading", self.s.minimap_rotate)
        rot.toggled.connect(self._set_minimap_rotate)
        v.addWidget(rot)
        tex = C.LabeledToggle("World map texture", self.s.minimap_texture)
        tex.toggled.connect(self._set_minimap_texture)
        v.addWidget(tex)
        bare = C.LabeledToggle("Bare map (no panel/titlebar)", self.s.minimap_bare)
        bare.toggled.connect(self._set_minimap_bare)
        v.addWidget(bare)
        ico = C.LabeledToggle("POI icons (else dots)", self.s.minimap_icons)
        ico.toggled.connect(lambda on: (self._set("minimap_icons", on), self._touch_minimap()))
        v.addWidget(ico)

        zoom = C.SliderRow("Zoom", 2, 60, int(self.s.minimap_zoom), str)
        zoom.valueChanged.connect(self._set_minimap_zoom)
        v.addWidget(zoom)
        szrow = QtWidgets.QGridLayout()
        szrow.setHorizontalSpacing(12)
        size = C.Stepper(self.s.minimap_size, 160, 640, 20)
        size.valueChanged.connect(self._set_minimap_size)
        isz = C.Stepper(self.s.minimap_icon_size, 8, 40, 2)
        isz.valueChanged.connect(lambda v_: (self._set("minimap_icon_size", v_), self._touch_minimap()))
        szrow.addWidget(C.Field("Size (px)", size), 0, 0)
        szrow.addWidget(C.Field("POI icon (px)", isz), 0, 1)
        v.addLayout(szrow)

        v.addWidget(C.SectionHeader("Layers"))
        grid = QtWidgets.QGridLayout()
        grid.setHorizontalSpacing(12)
        for i, (attr, label) in enumerate([
                ("minimap_enemies", "Enemies"),
                ("minimap_chests", "Chests / loot"),
                ("minimap_gatherables", "Gatherables"),
                ("minimap_obelisks", "Obelisks"),
                ("minimap_orbs", "Secret orbs"),
                ("minimap_dungeons", "Dungeons / teleports")]):
            t = C.LabeledToggle(label, getattr(self.s, attr))
            t.toggled.connect(lambda on, a=attr: self._set_minimap_layer(a, on))
            grid.addWidget(t, i // 2, i % 2)
        v.addLayout(grid)

        note = QtWidgets.QLabel(
            "Right-click a marker to mark it done (persists). The compass needle"
            " is set from the Entity overlay (click an enemy or secret orb row).")
        note.setWordWrap(True)
        note.setObjectName("Muted")
        v.addWidget(note)
        self._orb_progress = QtWidgets.QLabel()
        self._orb_progress.setObjectName("Muted")
        self._refresh_orb_progress()
        v.addWidget(self._orb_progress)
        v.addStretch(1)
        return page

    def _set_minimap_shape(self, shape):
        self._set("minimap_shape", shape)
        self._touch_minimap()

    def _set_minimap_rotate(self, on):
        self._set("minimap_rotate", on)
        self._touch_minimap()

    def _set_minimap_texture(self, on):
        self._set("minimap_texture", on)
        self._touch_minimap()

    def _set_minimap_bare(self, on):
        self._set("minimap_bare", on)
        ov = self.overlays.get("map")
        if ov is not None and hasattr(ov, "set_bare"):
            ov.set_bare(on)

    def _set_minimap_zoom(self, v):
        self._set("minimap_zoom", float(v))
        self._touch_minimap()

    def _set_minimap_size(self, v):
        self._set("minimap_size", v)
        ov = self.overlays.get("map")
        if ov is not None:
            ov.resize(v, v + 40)

    def _touch_minimap(self):
        ov = self.overlays.get("map")
        if ov is not None and hasattr(ov, "canvas"):
            ov.canvas.update()

    def _set_minimap_layer(self, attr, on):
        """Toggle a minimap POI layer + re-scan POIs now (don't wait for the
        slow tick) so the change is immediate."""
        self._set(attr, on)
        ov = self.overlays.get("map")
        if ov is not None and hasattr(ov, "canvas"):
            ov.canvas.refresh()

    def _refresh_orb_progress(self):
        profile = self.model.player_profile() if self.model else None
        prog = geo_orbs.region_progress(self.s.get_poi_done(profile))
        if not prog:
            self._orb_progress.setText("")
            return
        parts = [f"{geo_orbs.REGION_NAMES.get(r, r)} {d}/{t}"
                 for r, (d, t) in prog.items()]
        self._orb_progress.setText(
            "Orbs collected (auto-synced near orbs): " + " · ".join(parts))
