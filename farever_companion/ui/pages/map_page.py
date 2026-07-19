from PySide6 import QtCore, QtGui, QtWidgets
from .. import components as C

class MapPageMixin:
    def _page_map(self):
        page, v = self._page_container()
        card = C.OverlayCard("map", "Open Minimap",
                           "Top-down POI radar centred on the player.",
                           self.s.minimap_bare, has_bare=True)
        card.toggled.connect(lambda on: self._request_overlay("map", on))
        self._register_card("map", card)
        self._bare_toggle = card._bare_toggle
        card.bareToggled.connect(self._set_minimap_bare)

        v.addWidget(card)

        v.addWidget(C.SectionHeader("Shape"))
        shape = C.SegmentedControl(["Circle", "Square"], self.s.minimap_shape)
        shape.currentChanged.connect(self._set_minimap_shape)
        v.addWidget(shape)
        toggles_lay = QtWidgets.QHBoxLayout()
        toggles_lay.setSpacing(12)
        rot = C.LabeledToggle("Rotate map", self.s.minimap_rotate)
        rot.toggled.connect(self._set_minimap_rotate)
        toggles_lay.addWidget(rot)
        tex = C.LabeledToggle("Map texture", self.s.minimap_texture)
        tex.toggled.connect(self._set_minimap_texture)
        toggles_lay.addWidget(tex)
        ico = C.LabeledToggle("POI icons", self.s.minimap_icons)
        ico.toggled.connect(lambda on: (self._set("minimap_icons", on), self._touch_minimap()))
        toggles_lay.addWidget(ico)
        v.addLayout(toggles_lay)

        zoom = C.SliderRow("Zoom", 2, 60, int(self.s.minimap_zoom), str)
        zoom.valueChanged.connect(self._set_minimap_zoom)
        self._zoom_slider = zoom
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
        layer_items = [
                ("minimap_enemies", "Enemies"),
                ("minimap_companions", "Companions"),
                ("minimap_chests", "Chests"),
                ("minimap_gatherables", "Gatherables"),
                ("minimap_obelisks", "Obelisks / Respawn"),
                ("minimap_orbs", "Secret orbs"),
                ("minimap_dungeons", "Dungeons")]
        for i, (attr, label) in enumerate(layer_items):
            t = C.LabeledToggle(label, getattr(self.s, attr))
            t.toggled.connect(lambda on, a=attr: self._set_minimap_layer(a, on))
            grid.addWidget(t, i // 3, i % 3)
        # "Hide collected" sits in the next grid cell so it matches the others
        hide_coll = C.LabeledToggle("Hide collected", self.s.minimap_hide_collected)
        hide_coll.toggled.connect(self._set_minimap_hide_collected)
        self._hide_collected_toggle = hide_coll

        limit_range = C.LabeledToggle("Limit by range (400m)", self.s.minimap_limit_by_zone)
        limit_range.toggled.connect(lambda on: (self._set("minimap_limit_by_zone", on), self._touch_minimap()))

        n = len(layer_items)
        grid.addWidget(hide_coll, n // 3, n % 3)
        grid.addWidget(limit_range, (n + 1) // 3, (n + 1) % 3)
        v.addLayout(grid)

        note = QtWidgets.QLabel(
            "Right-click a marker to toggle done/undone (persists).        Hold right-click & drag to pan the minimap. "
            "      Chests and orbs in close range are automatically synced and marked done or undone.")
        note.setWordWrap(True)
        note.setObjectName("Muted")
        v.addWidget(note)
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

    def _set_minimap_hide_collected(self, on):
        """Toggle hide-collected: update setting, overlay canvas, and its eye button."""
        self._set("minimap_hide_collected", on)
        ov = self.overlays.get("map")
        if ov is not None:
            if hasattr(ov, "set_hide_collected"):
                ov.set_hide_collected(on)
            elif hasattr(ov, "canvas"):
                ov.canvas.refresh()


