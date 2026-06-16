"""Minimap overlay.

Top-down radar centred on the player: chests, gatherables, enemies, obelisks
and secret orbs plotted from the shared LiveModel + the static chest/orb
indexes. Zoomable; POIs beyond the view clamp to the edge ring as direction
markers. Right-click a POI to mark it done (persists). The compass-needle
target (set from the entity HUD, ui/tracker.py) shows as an accent ring. The view rotates by the camera orbit yaw (mouse
look, via `model.camera_yaw()`) so it tracks where you're looking even when
standing still; it falls back to the movement heading when the camera yaw can't
be read. The player arrow shows the body heading within that frame."""
from __future__ import annotations

import math

from PySide6 import QtCore, QtGui, QtWidgets

from . import theme
from .overlay_base import OverlayWindow
from ..data import icons
from ..geo import nav, orbs as geo_orbs

POLL_MS = 300     # POI rescan (heavy)
FAST_MS = 33      # position/heading repaint (cheap) -> smooth pan + rotation

# World -> map-image pixel transform (W1 / Siagarta). Derived from the community
# web map (IceCaveBear/farever-map map.js): coords = [0.89*(4096-y)-1595,
# 0.89*(x+1724)] over a 3584x5120 image. Collapses to: px = S*x + OX, py = S*y + OY.
MAP_SCALE = 0.89
MAP_OFF_X = 0.89 * 1724                       # 1534.36
MAP_OFF_Y = 5120 - (0.89 * 4096 - 1595)       # 3069.56
# camera-yaw -> rotation calibration lives in geo/nav.py (CAM_YAW_SIGN /
# CAM_YAW_OFFSET), shared with the compass needle.
_MAP_PM = None
_MAP_TRIED = False


def _map_pixmap():
    """The bundled W1 map image (lazy, cached). None if missing."""
    global _MAP_PM, _MAP_TRIED
    if not _MAP_TRIED:
        _MAP_TRIED = True
        try:
            from .. import paths
            p = paths.assets_dir() / "map" / "W1.png"
            if p.exists():
                pm = QtGui.QPixmap(str(p))
                _MAP_PM = pm if not pm.isNull() else None
        except Exception:
            _MAP_PM = None
    return _MAP_PM


class _Canvas(QtWidgets.QWidget):
    def __init__(self, model, settings):
        super().__init__()
        self.model = model
        self.s = settings
        self.setMinimumSize(220, 220)
        self._pois: list[tuple] = []      # (x, y, z, kind, label, id)
        self._px = self._py = self._pz = 0.0
        self._heading: float | None = None    # movement facing
        self._cam_yaw: float | None = None    # free-look camera yaw
        self._mtx = None                      # engine view-proj matrix
        self._drag = None

    def _read_player(self) -> bool:
        xyz = self.model.player_xyz()
        if xyz is None:
            return False
        self._px, self._py, self._pz = xyz
        self._heading = self.model.player_heading()       # movement facing (arrow)
        self._cam_yaw = self.model.camera_yaw()           # free-look yaw (fallback)
        self._mtx = self.model.view_matrix()              # exact view rotation
        return True

    def refresh_fast(self):
        """Cheap: player position + camera yaw / heading (a few tiny reads) so
        the map pans/rotates smoothly. Runs at ~30fps; negligible cost."""
        if self._read_player():
            self.update()

    def refresh(self):
        """Heavy: rebuild the POI list (enemy/chest/gatherable scan). Runs at a
        low rate; the fast tick keeps motion smooth in between."""
        if not self._read_player():
            self._pois = []
            self.update()
            return
        xyz = (self._px, self._py, 0.0)
        pois = []
        s = self.s
        # static + live chests / crates / world-activity loot drops
        if s.minimap_chests:
            try:
                for r in self.model.nearest_chests_merged(xyz, n=60):
                    c = next((c for c in self.model.chests if c.chest_id == r.chest_id), None)
                    if c:
                        pois.append((c.x, c.y, c.z, "chest",
                                     r.loot_table or r.chest_id, r.chest_id))
                # live-only chests (world-activity loot drops) have no static
                # position row; plot them from the live element directly
                static_ids = {c.chest_id for c in self.model.chests}
                for e in self.model.live_chests():
                    if (e.elem_id or "") not in static_ids:
                        pois.append((e.x, e.y, e.z, "chest", e.elem_id or "loot",
                                     e.elem_id or f"ch{e.addr}"))
            except Exception:
                pass
        # live entities (each layer independently toggleable)
        try:
            if s.minimap_enemies:
                for e in self.model.enemies():
                    pois.append((e.x, e.y, e.z, "enemy", e.unit_id or "?", f"e{e.addr}"))
            if s.minimap_gatherables:
                for g in self.model.gatherables():
                    pois.append((g.x, g.y, g.z, "gatherable", g.elem_id or "?",
                                 g.elem_id or ""))
            if s.minimap_obelisks:
                for o in self.model.obelisks():
                    pois.append((o.x, o.y, o.z, "obelisk", o.elem_id or "obelisk",
                                 o.elem_id or f"ob{o.addr}"))
            if s.minimap_orbs:
                # static world orbs (Collector achievements) + live dungeon
                # orbs; collected ones (auto-synced or right-clicked) draw
                # greyed via the done state, like chests
                for ob in geo_orbs.load_orbs():
                    pois.append((ob.x, ob.y, ob.z, "orb", ob.orb_id, ob.orb_id))
                for e in self.model.live_orbs():
                    pois.append((e.x, e.y, e.z, "orb", e.elem_id or "orb",
                                 e.elem_id or f"orb{e.addr}"))
            if s.minimap_dungeons:
                for t in self.model.teleporters():
                    pois.append((t.x, t.y, t.z, "dungeon", t.elem_id or "teleport",
                                 t.elem_id or f"tp{t.addr}"))
        except Exception:
            pass

        # Sort all POIs by 2D distance to player so the closest are processed first
        pois.sort(key=lambda p: math.hypot(p[0] - self._px, p[1] - self._py))

        self._pois = pois
        self.update()

    def _scale(self):
        view = max(20.0, self.s.minimap_zoom * 20.0)   # world-units radius shown
        return (min(self.width(), self.height()) / 2 - 6) / view

    def _phi(self):
        """Rotation applied so the view direction points up; 0 = north-up.
        Prefer the exact rotation from the engine camera matrix (any yaw, no
        calibration); fall back to the camera-yaw field, then the movement
        heading (see geo/nav.py)."""
        if not self.s.minimap_rotate:
            return 0.0
        if self._mtx:
            phi = nav.ground_view_phi(self._mtx, self._px, self._py, self._pz)
            if phi is not None:
                return phi
        return nav.view_phi(self._cam_yaw, self._heading)

    def _rel(self, wx, wy, scale, phi):
        """World point -> rotated screen-space delta (dx right, dy up).
        North is low world-Y (matches the web map projection: low Y = image
        top = north), so the up component is (py - wy) - a POI north of the
        player has wy < py and lands above centre."""
        dx = (wx - self._px) * scale
        dy = (self._py - wy) * scale
        if phi:
            c, s = math.cos(phi), math.sin(phi)
            dx, dy = dx * c - dy * s, dx * s + dy * c
        return dx, dy

    def paintEvent(self, _e):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        w, h = self.width(), self.height()
        cx, cy = w / 2, h / 2
        rad = min(w, h) / 2 - 4
        square = self.s.minimap_shape == "Square"
        # backdrop + clip
        p.setBrush(QtGui.QColor(theme.PANEL))
        p.setPen(QtGui.QColor(theme.BORDER))
        # clip with a QPainterPath (transform-aware, so the rotated map texture
        # below is clipped correctly - a QRegion clip is not)
        clip = QtGui.QPainterPath()
        if square:
            box = QtCore.QRectF(cx - rad, cy - rad, rad * 2, rad * 2)
            p.drawRect(box)
            clip.addRect(box)
        else:
            p.drawEllipse(QtCore.QPointF(cx, cy), rad, rad)
            clip.addEllipse(QtCore.QPointF(cx, cy), rad, rad)
        p.setClipPath(clip)
        scale = self._scale()
        phi = self._phi()
        # world map texture (under the rings/POIs)
        if self.s.minimap_texture:
            self._draw_map(p, cx, cy, scale, phi)
        # rings (outline only - NoBrush, else they'd fill over the map texture)
        p.setPen(QtGui.QColor(theme.BORDER))
        p.setBrush(QtCore.Qt.NoBrush)
        for f in (0.5, 1.0):
            if square:
                p.drawRect(QtCore.QRectF(cx - rad * f, cy - rad * f, rad * 2 * f, rad * 2 * f))
            else:
                p.drawEllipse(QtCore.QPointF(cx, cy), rad * f, rad * f)
        # POIs
        track_pos = self._track_pos()
        profile = self.model.player_profile()
        edge_counts = {"chest": 0, "orb": 0}
        for (wx, wy, _wz, kind, label, poi_id) in self._pois:
            dx, dy = self._rel(wx, wy, scale, phi)
            edge = False
            if square:
                m = max(abs(dx), abs(dy))
                if m > rad - 4:
                    if m == 0:
                        continue
                    k = (rad - 4) / m
                    dx *= k; dy *= k
                    edge = True
            else:
                dist = math.hypot(dx, dy)
                if dist > rad - 4:
                    if dist == 0:
                        continue
                    k = (rad - 4) / dist
                    dx *= k; dy *= k
                    edge = True

            if edge and kind in edge_counts:
                if edge_counts[kind] >= 5:
                    continue
                edge_counts[kind] += 1

            sx, sy = cx + dx, cy - dy
            done = bool(poi_id and self.s.is_done(poi_id, profile))
            if self._is_waypoint(kind, label, poi_id, wx, wy, track_pos):
                # the compass waypoint: accent ring so the target is obvious
                ring = QtGui.QColor(self.s.hud_accent)
                p.setPen(QtGui.QPen(ring, 2))
                p.setBrush(QtCore.Qt.NoBrush)
                r_hl = (self.s.minimap_icon_size / 2 + 3) if not edge else 6
                p.drawEllipse(QtCore.QPointF(sx, sy), r_hl, r_hl)
            pm = self._poi_pixmap(kind, label, self.s.minimap_icon_size, done) \
                if (self.s.minimap_icons and not edge) else None
            if pm is not None:
                if done:
                    p.setOpacity(0.4)
                z = pm.width()
                p.drawPixmap(int(sx - z / 2), int(sy - z / 2), pm)
                if done:
                    p.setOpacity(1.0)
            else:
                col = QtGui.QColor(theme.KIND_COLOR.get(kind, theme.TEXT))
                if done:
                    col.setAlpha(70)
                p.setPen(QtCore.Qt.NoPen)
                p.setBrush(col)
                p.drawEllipse(QtCore.QPointF(sx, sy), 3 if edge else 4, 3 if edge else 4)
        # compass + player (drawn unclipped so edge letters aren't cut)
        p.setClipping(False)
        self._draw_compass(p, cx, cy, rad, phi)
        # player marker + facing arrow (uses the live Highlight color or arrow.png)
        accent = QtGui.QColor(self.s.hud_accent)
        # north-up frame: world heading's y-component inverts, so -heading
        fwd = (phi - self._heading) if self._heading is not None else (math.pi / 2)

        arrow_pm = icons.asset_icon("arrow", 24)
        if arrow_pm is not None and not arrow_pm.isNull():
            p.save()
            p.translate(cx, cy)
            deg = 90.0 - math.degrees(fwd)
            p.rotate(deg)
            p.drawPixmap(int(-arrow_pm.width() / 2), int(-arrow_pm.height() / 2), arrow_pm)
            p.restore()
        else:
            p.setBrush(accent)
            p.setPen(QtCore.Qt.NoPen)
            p.drawEllipse(QtCore.QPointF(cx, cy), 4, 4)
            p.setPen(QtGui.QPen(accent, 2))
            p.drawLine(QtCore.QPointF(cx, cy),
                       QtCore.QPointF(cx + math.cos(fwd) * 13, cy - math.sin(fwd) * 13))
        p.end()

    def _draw_map(self, p, cx, cy, scale, phi):
        """Blit the world map so the player sits at centre, facing up, matching
        the POI projection. image px = (S*x+OX, S*y+OY); we map image->screen
        with the same rotate(phi)+scale the POIs use and let Qt clip."""
        pm = _map_pixmap()
        if pm is None:
            return
        s, A = scale, MAP_SCALE
        cosf, sinf = math.cos(phi), math.sin(phi)
        u0 = -MAP_OFF_X / A - self._px      # world x at image px 0
        w0 = MAP_OFF_Y / A + self._py       # paired with the north-up Y row below
        t = QtGui.QTransform(
            s * cosf / A, -s * sinf / A,    # m11, m12  (coeffs of image x)
            s * sinf / A, s * cosf / A,     # m21, m22  (coeffs of image y; Y unflipped)
            cx + s * cosf * u0 - s * sinf * w0,
            cy - s * sinf * u0 - s * cosf * w0)
        p.save()
        p.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
        p.setTransform(t, True)
        p.drawPixmap(0, 0, pm)
        p.restore()
        # subtle veil so POIs/markers read clearly over the art
        veil = QtGui.QColor(theme.BG); veil.setAlpha(70)
        p.fillRect(self.rect(), veil)

    # bundled flat marker icons (assets/map_icons) per layer
    _MARKER = {"chest": "chest", "gatherable": "gatherable", "obelisk": "obelisk",
               "orb": "orb", "activity": "activity", "dungeon": "dungeon"}

    def _poi_pixmap(self, kind, label, size, done=False):
        """A POI marker pixmap: enemies show the real per-unit game icon (organic
        outline); chests/crates, gatherables and obelisks use the bundled flat
        marker icons (assets/map_icons). Falls back to a tinted UI
        glyph, then to a plain dot."""
        if kind == "enemy" and label and icons.has_icon("unit", label):
            return icons.outlined("unit", label, size, theme.DANGER)
        name = self._MARKER.get(kind)
        if name:
            if done and name in ("chest", "orb"):
                name = name + "2"
            pm = icons.marker(name, size)
            if pm is not None:
                return pm
        glyph = {"chest": "box", "obelisk": "radio", "gatherable": "dice-5",
                 "enemy": "swords", "orb": "broadcast", "activity": "box",
                 "dungeon": "map"}.get(kind)
        if glyph:
            return icons.ui_icon(glyph, theme.KIND_COLOR.get(kind, theme.TEXT), size)
        return None

    def _draw_compass(self, p, cx, cy, rad, phi):
        r = rad - 11
        f = p.font(); f.setPointSize(8); f.setBold(True); p.setFont(f)
        for label, ang, col in (("N", math.pi / 2, theme.DANGER),
                                ("E", 0.0, theme.MUTED),
                                ("S", -math.pi / 2, theme.MUTED),
                                ("W", math.pi, theme.MUTED)):
            a = ang + phi
            lx = cx + math.cos(a) * r
            ly = cy - math.sin(a) * r
            p.setPen(QtGui.QColor(col))
            p.drawText(QtCore.QRectF(lx - 8, ly - 8, 16, 16),
                       QtCore.Qt.AlignCenter, label)

    def _track_pos(self):
        """(x, y) of a tracked 'pos' waypoint, or None."""
        if self.s.track_kind != "pos":
            return None
        try:
            coords = self.s.track_id.split("|", 1)[0].split(",")
            return (float(coords[0]), float(coords[1]))
        except (ValueError, IndexError):
            return None

    def _is_waypoint(self, kind, label, poi_id, wx, wy, track_pos) -> bool:
        tk, tid = self.s.track_kind, self.s.track_id
        if not tk:
            return False
        if tk == "orb":
            return kind == "orb" and poi_id == tid
        if tk == "unit":
            return kind == "enemy" and label == tid
        if tk == "pos" and track_pos is not None:
            return abs(wx - track_pos[0]) < 0.5 and abs(wy - track_pos[1]) < 0.5
        return False

    # overlapping markers: the click prefers the kind the player most likely
    # aims at (orbs are small primary targets and lose a raw nearest-test)
    _CLICK_PRIORITY = {"orb": 0, "enemy": 1, "chest": 2, "activity": 2,
                       "dungeon": 3, "obelisk": 4, "gatherable": 5}

    def _poi_at(self, pos):
        """The POI under a click, hit-testing the *drawn* position - same edge
        clamp as paintEvent, so far markers are clickable on the ring. Among
        everything within the hit radius, kind priority breaks the overlap."""
        cx, cy = self.width() / 2, self.height() / 2
        rad = min(self.width(), self.height()) / 2 - 4
        square = self.s.minimap_shape == "Square"
        scale = self._scale()
        phi = self._phi()
        best, best_rank = None, None
        for poi in self._pois:
            wx, wy = poi[0], poi[1]
            dx, dy = self._rel(wx, wy, scale, phi)
            m = max(abs(dx), abs(dy)) if square else math.hypot(dx, dy)
            if m > rad - 4 and m > 0:
                k = (rad - 4) / m
                dx *= k; dy *= k
            d = math.hypot(cx + dx - pos.x(), cy - dy - pos.y())
            if d >= 12.0:
                continue
            rank = (self._CLICK_PRIORITY.get(poi[3], 9), d)
            if best_rank is None or rank < best_rank:
                best, best_rank = poi, rank
        return best

    def mousePressEvent(self, e):
        if e.button() == QtCore.Qt.RightButton:
            self._mark_done(e)
            return
        if e.button() == QtCore.Qt.LeftButton and not getattr(self.window(), "_locked", False):
            # any marker is a compass waypoint: enemies track the unit, orbs
            # the orb, everything else a fixed position
            tr = getattr(self.window(), "_tracker", None)
            poi = self._poi_at(e.position())
            if poi is not None and tr is not None:
                wx, wy, wz, kind, label, poi_id = poi
                
                # Highlight and select the clicked item in the EntityOverlay if it is open
                if tr.parent() is not None:
                    entity_ov = getattr(tr.parent(), "overlays", {}).get("entity")
                    if entity_ov is not None and entity_ov.isVisible():
                        clean_key = poi_id
                        if kind == "enemy" and isinstance(poi_id, str) and poi_id.startswith("e"):
                            try:
                                clean_key = int(poi_id[1:])
                            except ValueError:
                                pass
                        entity_ov.select_by_key(kind, clean_key)
                
                if kind == "orb" and poi_id and poi_id in geo_orbs.by_id():
                    tr.toggle("orb", poi_id)
                elif kind == "enemy" and label and label != "?":
                    tr.toggle("unit", label)
                else:
                    # incl. live dungeon orbs (not in the static index):
                    # any other marker becomes a fixed-position waypoint
                    tr.toggle("pos", f"{wx:.1f},{wy:.1f},{wz:.1f}|{label or kind}")
                return
            # drag the window from the map body (the only grip in bare mode)
            self._drag = e.globalPosition().toPoint() - self.window().frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._drag is not None and e.buttons() & QtCore.Qt.LeftButton:
            self.window().move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, _e):
        if self._drag is not None:
            self._drag = None
            win = self.window()
            if hasattr(win, "persist_geometry"):
                win.persist_geometry()

    def _mark_done(self, e):
        # mark the nearest plotted POI done (hit-test in the same rotated space)
        cx, cy = self.width() / 2, self.height() / 2
        scale = self._scale()
        phi = self._phi()
        best, bestd = None, 12.0
        for (wx, wy, _wz, _k, _l, poi_id) in self._pois:
            dx, dy = self._rel(wx, wy, scale, phi)
            sx, sy = cx + dx, cy - dy
            d = math.hypot(sx - e.position().x(), sy - e.position().y())
            if d < bestd and poi_id:
                best, bestd = poi_id, d
        if best:
            profile = self.model.player_profile()
            self.s.toggle_done(best, profile)
            # collecting the needle's target ends the tracking
            tr = getattr(self.window(), "_tracker", None)
            if tr is not None and tr.is_tracked("orb", best) and self.s.is_done(best, profile):
                tr.clear()
            self.update()


class MinimapOverlay(OverlayWindow):
    def __init__(self, model, settings, parent=None):
        super().__init__("MAP", settings, geo_key="minimap", parent=parent)
        self.s = settings
        self._tracker = None
        zin = QtWidgets.QPushButton("+"); zin.setObjectName("Icon")
        zout = QtWidgets.QPushButton("−"); zout.setObjectName("Icon")
        zin.clicked.connect(lambda: self._zoom(1.25))
        zout.clicked.connect(lambda: self._zoom(0.8))
        for wdg in (zout, zin):
            self.titlebar.extra.insertWidget(self.titlebar.extra.count() - 1, wdg)

        self.canvas = _Canvas(model, settings)
        self.content.addWidget(self.canvas, 1)
        self._hint = QtWidgets.QLabel("click a marker = compass waypoint · right-click = mark done")
        self._hint.setObjectName("Muted")
        self.content.addWidget(self._hint)

        sz = settings.minimap_size
        self.resize(sz, sz + 40)
        if settings.minimap_bare:
            self.set_bare(True)
        # two cadences: fast = smooth pan/rotate (cheap reads); slow = POI rescan
        self._fast = QtCore.QTimer(self)
        self._fast.timeout.connect(self.canvas.refresh_fast)
        self._fast.start(FAST_MS)
        self._slow = QtCore.QTimer(self)
        self._slow.timeout.connect(self.canvas.refresh)
        self._slow.start(POLL_MS)
        self.canvas.refresh()   # initial POIs immediately

    def set_bare(self, on: bool) -> None:
        """Chromeless: hide the titlebar, hint, and card panel, just the map.
        Drag the map body to move it (when unlocked)."""
        self.titlebar.setVisible(not on)
        self._hint.setVisible(not on)
        if on:
            self._frame.setStyleSheet("background:transparent;border:0;")
            self.content.setContentsMargins(0, 0, 0, 0)
        else:
            self._frame.setStyleSheet("")     # revert to the QSS #Card look
            self.content.setContentsMargins(8, 6, 8, 8)
        self.canvas.update()

    def _zoom(self, f):
        self.s.minimap_zoom = max(2.0, min(60.0, self.s.minimap_zoom * f))
        self.s.save()
        self.canvas.update()

    def set_tracker(self, tracker) -> None:
        """The shared TrackController; the tracked orb shows as an accent ring
        and right-click-done on it ends the tracking."""
        self._tracker = tracker
        tracker.changed.connect(self.canvas.update)

    def closeEvent(self, e):
        self._fast.stop()
        self._slow.stop()
        super().closeEvent(e)
