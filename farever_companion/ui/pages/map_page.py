from PySide6 import QtCore, QtGui, QtWidgets
from .. import components as C
from ...data import loot
from ...data import icons
from ...geo import orbs as geo_orbs

class MinimapCard(QtWidgets.QFrame):
    toggled = QtCore.Signal(bool)

    def __init__(self, icon_name: str, title: str, desc: str, bare_checked: bool, parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        self._active = False
        self._icon_name = icon_name

        from .. import theme
        from ...data import icons

        main_lay = QtWidgets.QHBoxLayout(self)
        main_lay.setContentsMargins(16, 14, 16, 14)
        main_lay.setSpacing(12)

        # Left Column (labels)
        left_v = QtWidgets.QVBoxLayout()
        left_v.setSpacing(8)

        # Line 1: Icon and title label side-by-side
        row1 = QtWidgets.QHBoxLayout()
        row1.setSpacing(8)
        self._icon = QtWidgets.QLabel()
        self._icon.setFixedSize(28, 28)
        self._icon.setPixmap(icons.ui_icon(icon_name, theme.ACCENT, 28))
        
        title_lbl = QtWidgets.QLabel(title.upper())
        title_lbl.setStyleSheet(f"color:{theme.TEXT};font-weight:700;font-size:16px;background:transparent;")
        
        row1.addWidget(self._icon)
        row1.addWidget(title_lbl)
        row1.addStretch(1)
        left_v.addLayout(row1)

        # Line 2: Description text
        desc_lbl = QtWidgets.QLabel(desc)
        desc_lbl.setWordWrap(True)
        desc_lbl.setStyleSheet(f"color:{theme.MUTED};font-size:12px;background:transparent;margin-left:36px;")
        left_v.addWidget(desc_lbl)

        # Right Column (toggles aligned on the right)
        right_v = QtWidgets.QVBoxLayout()
        right_v.setSpacing(8)

        # Align main toggle with Line 1
        self._toggle = C.ToggleSwitch()
        self._toggle.toggled.connect(self._on_toggle)
        right_v.addWidget(self._toggle, 0, QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)

        # Align Boardless label and toggle on right side next to each other
        bare_row = QtWidgets.QHBoxLayout()
        bare_row.setSpacing(8)
        bare_row.setContentsMargins(0, 0, 0, 0)

        boardless_lbl = QtWidgets.QLabel("Boardless")
        boardless_lbl.setStyleSheet(f"color:{theme.TEXT};font-weight:700;font-size:14px;background:transparent;")
        
        self._bare_toggle = C.ToggleSwitch(bare_checked)
        
        bare_row.addWidget(boardless_lbl)
        bare_row.addWidget(self._bare_toggle)
        
        right_v.addLayout(bare_row)
        right_v.addStretch(1)

        main_lay.addLayout(left_v, 1)
        main_lay.addLayout(right_v, 0)

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
        from .. import theme
        from ...data import icons
        self._icon.setPixmap(icons.ui_icon(self._icon_name, theme.ACCENT, 28))
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

    def paintEvent(self, e):
        super().paintEvent(e)
        if self._active and self.isEnabled():
            from .. import theme
            p = QtGui.QPainter(self)
            w, h = self.width(), self.height()
            p.setPen(QtGui.QPen(QtGui.QColor(theme.ACCENT), 1))
            p.setBrush(QtCore.Qt.NoBrush)
            p.drawRect(0, 0, w - 1, h - 1)
            p.fillRect(0, 0, 3, h, QtGui.QColor(theme.ACCENT))
            p.end()

class MapPageMixin:
    def _page_map(self):
        page, v = self._page_container()
        card = MinimapCard("map", "Open Minimap",
                           "Top-down POI radar centred on the player.",
                           self.s.minimap_bare)
        card.toggled.connect(lambda on: self._request_overlay("map", on))
        self._register_card("map", card)
        self._bare_toggle = card._bare_toggle
        self._bare_toggle.toggled.connect(self._set_minimap_bare)

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
        layer_items = [
                ("minimap_enemies", "Enemies"),
                ("minimap_companions", "Companions"),
                ("minimap_chests", "Chests / loot"),
                ("minimap_gatherables", "Gatherables"),
                ("minimap_obelisks", "Obelisks"),
                ("minimap_orbs", "Secret orbs"),
                ("minimap_dungeons", "Dungeons / teleports")]
        for i, (attr, label) in enumerate(layer_items):
            t = C.LabeledToggle(label, getattr(self.s, attr))
            t.toggled.connect(lambda on, a=attr: self._set_minimap_layer(a, on))
            grid.addWidget(t, i // 2, i % 2)
        # "Hide collected" sits in the next grid cell so it matches the others
        hide_coll = C.LabeledToggle("Hide collected", self.s.minimap_hide_collected)
        hide_coll.toggled.connect(self._set_minimap_hide_collected)
        self._hide_collected_toggle = hide_coll
        n = len(layer_items)
        grid.addWidget(hide_coll, n // 2, n % 2)
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

    def _set_minimap_hide_collected(self, on):
        """Toggle hide-collected: update setting, overlay canvas, and its eye button."""
        self._set("minimap_hide_collected", on)
        ov = self.overlays.get("map")
        if ov is not None:
            if hasattr(ov, "set_hide_collected"):
                ov.set_hide_collected(on)
            elif hasattr(ov, "canvas"):
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
