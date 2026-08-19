"""Interactive Zone Spawn Map canvas widget for the Codex page."""
from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets
from ... import theme
from ....data import icons
from .... import constants as APP_C
from .... import paths


class CodexZoneMapCanvas(QtWidgets.QWidget):
    """Interactive Zone Spawn Map canvas widget for the Codex page."""
    def __init__(self, model, settings, parent=None):
        super().__init__(parent)
        self.model = model
        self.s = settings
        self.selected_mob_name = ""
        self.selected_coords = []
        self.map_pixmap = None
        self.fog_pixmap = None
        self.pins = []
        self._load_map_texture()
        self.setMouseTracking(True)

    def _load_map_texture(self):
        try:
            p_dir = paths.assets_dir() / "map"
            candidates = list(p_dir.glob("map.webp")) + list(p_dir.glob("map.png"))
            if candidates:
                candidates.sort(key=lambda x: x.stat().st_size, reverse=True)
                pm = QtGui.QPixmap(str(candidates[0]))
                if not pm.isNull():
                    self.map_pixmap = pm

            fog_path = p_dir / "fog.webp"
            if fog_path.exists():
                fog_pm = QtGui.QPixmap(str(fog_path))
                if not fog_pm.isNull():
                    self.fog_pixmap = fog_pm
        except Exception:
            self.map_pixmap = None
            self.fog_pixmap = None

    def set_selected_mob(self, name: str | None, coords: list[dict], item: dict | None = None):
        self.selected_mob_name = name or ""
        item = item or {}

        is_chest = bool(item.get("chest_loc") or item.get("chest_locs"))
        is_vendor = bool(item.get("vendor_npc") or item.get("vendor_npcs"))
        is_dung = bool((item.get("is_dungeon") or item.get("is_rift")) and not is_vendor and not is_chest)

        if is_chest:
            m_col = QtGui.QColor(theme.GOLD)
        elif is_vendor:
            m_col = QtGui.QColor(theme.ACCENT)
        elif is_dung:
            m_col = QtGui.QColor(theme.KIND_COLOR.get("dungeon", "#7c3aed"))
        elif item.get("is_boss"):
            m_col = QtGui.QColor(theme.GOLD)
        elif item.get("is_critter") or item.get("type") in ("Companion", "Pet"):
            m_col = QtGui.QColor(theme.KIND_COLOR.get("companion", "#7aa2f7"))
        else:
            m_col = QtGui.QColor(theme.DANGER)

        self.pins = []
        for c in (coords or []):
            # A coord that carries its own name (soulstone summon spots) labels
            # its pin on the map — everything else stays anonymous to avoid
            # painting the same spawn-mob name dozens of times.
            pin_name = c.get("name") or name or ""
            self.pins.append({
                "x": c.get("x", 0),
                "y": c.get("y", 0),
                "color": m_col,
                "name": pin_name,
                "show_label": bool(c.get("name")),
                "is_dungeon": is_dung,
                "is_vendor": is_vendor,
                "is_chest": is_chest
            })
        self.update()

    def set_multi_pins(self, label: str, pins: list[dict]):
        self.selected_mob_name = label or ""
        self.pins = pins or []
        self.update()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        w, h = self.width(), self.height()

        # Dark Base Background
        p.fillRect(self.rect(), QtGui.QColor("#0a0f12"))

        if not self.map_pixmap or self.map_pixmap.isNull():
            p.setPen(QtGui.QPen(QtGui.QColor(theme.BORDER), 1))
            p.drawRect(0, 0, w - 1, h - 1)
            return

        map_w = self.map_pixmap.width()
        map_h = self.map_pixmap.height()

        # Preserve aspect ratio & center map texture inside widget bounds
        scale_fit = min(w / map_w, h / map_h)
        target_w = map_w * scale_fit
        target_h = map_h * scale_fit
        ox = (w - target_w) / 2.0
        oy = (h - target_h) / 2.0

        target_rect = QtCore.QRectF(ox, oy, target_w, target_h)

        # 1. Draw Aspect-Ratio Preserved Map Texture
        scaled = self.map_pixmap.scaled(int(target_w), int(target_h), QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
        p.drawPixmap(int(ox), int(oy), scaled)

        # 2. Draw Fog Overlay matching minimap.py for empty margins and void bounds
        fog_pm = getattr(self, "fog_pixmap", None)
        if fog_pm and not fog_pm.isNull():
            p.save()
            fog_brush = QtGui.QBrush(fog_pm)
            p.setBrush(fog_brush)
            p.setPen(QtCore.Qt.NoPen)

            # Outer margins (outside target_rect)
            outer_path = QtGui.QPainterPath()
            outer_path.addRect(self.rect())
            outer_path.addRect(target_rect)
            outer_path.setFillRule(QtCore.Qt.OddEvenFill)
            p.drawPath(outer_path)

            # Void area outside MAP_BOUNDS
            map_bounds = getattr(APP_C, "MAP_BOUNDS", None)
            if map_bounds:
                try:
                    x1, y1, x2, y2 = map_bounds
                    scale_val = getattr(APP_C, "MAP_SCALE", 0.444780)
                    
                    px1 = ox + (((x1 + getattr(APP_C, "X_OFFSET", 1960.731)) * scale_val) / map_w) * target_w
                    py1 = oy + (((y1 + getattr(APP_C, "Y_OFFSET", 1757.164)) * scale_val) / map_h) * target_h
                    px2 = ox + (((x2 + getattr(APP_C, "X_OFFSET", 1960.731)) * scale_val) / map_w) * target_w
                    py2 = oy + (((y2 + getattr(APP_C, "Y_OFFSET", 1757.164)) * scale_val) / map_h) * target_h

                    inner_path = QtGui.QPainterPath()
                    inner_path.addRect(target_rect)
                    inner_path.addRect(QtCore.QRectF(px1, py1, max(0.0, px2 - px1), max(0.0, py2 - py1)))
                    inner_path.setFillRule(QtCore.Qt.OddEvenFill)
                    p.drawPath(inner_path)
                except Exception:
                    pass
            p.restore()

        # Subtle dark overlay for contrast
        p.fillRect(target_rect, QtGui.QColor(0, 0, 0, 80))

        # Border around container
        p.setPen(QtGui.QPen(QtGui.QColor(theme.BORDER), 1))
        p.drawRect(0, 0, w - 1, h - 1)

        if not getattr(self, "pins", None):
            return

        x_off = getattr(APP_C, "X_OFFSET", 1960.731)
        y_off = getattr(APP_C, "Y_OFFSET", 1757.164)
        scale = getattr(APP_C, "MAP_SCALE", 0.444780)

        # Plot mob spawn pins relative to centered map bounds
        try:
            dungeon_pm = icons.marker("dungeon", 22, accent=theme.KIND_COLOR.get("dungeon", "#7c3aed"))
        except Exception:
            dungeon_pm = None

        try:
            vendor_pm = icons.marker("shop", 22, accent=theme.GOLD)
            if not vendor_pm or vendor_pm.isNull():
                vendor_pm = icons.marker("currency", 22, accent=theme.GOLD)
            if not vendor_pm or vendor_pm.isNull():
                vendor_pm = icons.marker("vendor", 22, accent=theme.GOLD)
        except Exception:
            vendor_pm = None

        try:
            chest_pm = icons.marker("chest", 22, accent=theme.GOLD)
        except Exception:
            chest_pm = None

        for pin in self.pins:
            wx, wy = pin.get("x", 0), pin.get("y", 0)
            main_col = pin.get("color", QtGui.QColor(theme.DANGER))
            glow_col = QtGui.QColor(main_col)
            glow_col.setAlpha(80)
            is_dung = pin.get("is_dungeon", False)
            is_vend = pin.get("is_vendor", False)
            is_chst = pin.get("is_chest", False)

            ix = (wx + x_off) * scale
            iy = (wy + y_off) * scale

            # Convert full map pixel coordinate to static widget space
            px = ox + (ix / map_w) * target_w
            py = oy + (iy / map_h) * target_h

            if is_dung and dungeon_pm and not dungeon_pm.isNull():
                p.drawPixmap(int(px - dungeon_pm.width() / 2), int(py - dungeon_pm.height() / 2), dungeon_pm)
            elif is_vend and vendor_pm and not vendor_pm.isNull():
                p.drawPixmap(int(px - vendor_pm.width() / 2), int(py - vendor_pm.height() / 2), vendor_pm)
            elif is_chst and chest_pm and not chest_pm.isNull():
                p.drawPixmap(int(px - chest_pm.width() / 2), int(py - chest_pm.height() / 2), chest_pm)
            else:
                pt = QtCore.QPointF(px, py)

                # Soft Outer Translucent Radial Aura
                p.setPen(QtCore.Qt.NoPen)
                p.setBrush(QtGui.QBrush(glow_col))
                p.drawEllipse(pt, 7.0, 7.0)

                # Crisp Core Marker with Dark Outline
                p.setPen(QtGui.QPen(QtGui.QColor(10, 15, 18, 220), 1.5))
                p.setBrush(QtGui.QBrush(main_col))
                p.drawEllipse(pt, 3.8, 3.8)

            # Named pins (the soulstone summon spots resolve with their boss
            # name) draw a small label under the marker so the pin isn't
            # anonymous — everything else stays unlabeled to avoid clutter.
            if pin.get("show_label"):
                lbl = pin.get("name") or ""
                if lbl:
                    f = QtGui.QFont(self.font())
                    f.setPixelSize(10)
                    f.setBold(True)
                    p.setFont(f)
                    fm = p.fontMetrics()
                    tw = fm.horizontalAdvance(lbl)
                    bh = fm.height() + 2
                    bx = px - tw / 2 - 3
                    by = py + 9
                    p.setPen(QtCore.Qt.NoPen)
                    p.setBrush(QtGui.QColor(10, 15, 18, 205))
                    p.drawRoundedRect(QtCore.QRectF(bx, by, tw + 6, bh), 3, 3)
                    p.setPen(QtGui.QPen(QtGui.QColor(main_col)))
                    p.drawText(QtCore.QRectF(bx, by, tw + 6, bh),
                               QtCore.Qt.AlignCenter, lbl)
