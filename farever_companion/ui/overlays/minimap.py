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

from .. import theme
from ..overlay_base import OverlayWindow
from ... import constants as C
from ...data import icons, names
from ...geo import nav, orbs as geo_orbs, zones as geo_zones, gatherables as geo_gatherables, pois as geo_pois

POLL_MS = 300     # POI rescan (heavy)
FAST_MS = 33      # position/heading repaint (cheap) -> smooth pan + rotation
USE_HERO_SVGS = False  # Set to True to use player SVGs instead of dots for group members

# --- map-image pixel transform ---------------------------------------------
# camera-yaw -> rotation calibration lives in geo/nav.py (CAM_YAW_SIGN /
# CAM_YAW_OFFSET), shared with the compass needle.
_MAP_PM = None
_MAP_TRIED = False


def _map_pixmap():
    """Detect which map asset to use. Prefer 'map.webp', then 'map.png'."""
    global _MAP_PM, _MAP_TRIED
    if not _MAP_TRIED:
        _MAP_TRIED = True
        try:
            from ... import paths
            QtGui.QImageReader.setAllocationLimit(1024)
            p_dir = paths.assets_dir() / "map"

            # 1. Look for various naming patterns
            candidates = []
            patterns = [
                "map.webp", "map.png"
            ]
            for pat in patterns:
                candidates.extend(list(p_dir.glob(pat)))

            p = None
            if candidates:
                # Prefer the highest resolution available (largest file size)
                # This also prioritizes specific names if they are higher resolution
                candidates.sort(key=lambda x: x.stat().st_size, reverse=True)
                p = candidates[0]

            if p and p.exists():
                pm = QtGui.QPixmap(str(p))
                if not pm.isNull():
                    _MAP_PM = pm
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
        self._pan_x = 0.0
        self._pan_y = 0.0
        self._pan_start_x = 0.0
        self._pan_start_y = 0.0
        self._right_drag_start = None
        self._right_click_panned = False
        self._last_player_pos = None

    def _read_player(self) -> bool:
        xyz = self.model.player_xyz()
        if xyz is None:
            return False

        # Reset pan if player moved from the position where panning started/occurred
        if (self._pan_x != 0.0 or self._pan_y != 0.0) and self._last_player_pos is not None:
            dx = xyz[0] - self._last_player_pos[0]
            dy = xyz[1] - self._last_player_pos[1]
            if math.hypot(dx, dy) > 0.5:
                self._pan_x = 0.0
                self._pan_y = 0.0
                self.update()

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
        xyz = (self._px, self._py, self._pz)
        pois = []
        s = self.s
        limit_by_range = getattr(s, "minimap_limit_by_zone", False)
        player_zone = geo_zones.resolve_zone(self._px, self._py, self._pz) if limit_by_range else None
        max_dist = 400.0 if limit_by_range else 0.0
        
        # static + live chests / crates / world-activity loot drops
        hide_coll = getattr(s, "minimap_hide_collected", False)
        profile = self.model.player_profile()
        if s.minimap_chests:
            try:
                for r in self.model.nearest_chests_merged(xyz, n=100, max_dist=max_dist, player_zone=player_zone, use_2d=True):
                    c = next((c for c in self.model.chests if c.chest_id == r.chest_id), None)
                    if c:
                        if hide_coll and r.chest_id and s.is_done(r.chest_id, profile):
                            continue
                        is_recipe = "recipe" in r.chest_id.lower() or (r.loot_table and "recipe" in r.loot_table.lower())
                        kind = "recipe" if is_recipe else "chest"
                        label = names.chest_label(r.chest_id, r.loot_table)
                        pois.append((c.x, c.y, c.z, kind, label, r.chest_id))
                # live-only chests (world-activity loot drops) have no static
                # position row; plot them from the live element directly
                static_ids = {c.chest_id for c in self.model.chests}
                for e in self.model.live_chests(player_zone=player_zone, max_dist=max_dist, use_2d=True):
                    if (e.elem_id or "") not in static_ids:
                        if hide_coll and e.elem_id and s.is_done(e.elem_id, profile):
                            continue
                        is_recipe = "recipe" in (e.elem_id or "").lower()
                        kind = "recipe" if is_recipe else "chest"
                        label = names.chest_label(e.elem_id)
                        pois.append((e.x, e.y, e.z, kind, label,
                                     e.elem_id or f"ch{e.addr}"))
            except Exception:
                pass
        # live entities (each layer independently toggleable)
        try:
            if s.minimap_enemies:
                for e, d in self.model.nearest_enemies(xyz, n=100, max_dist=max_dist, player_zone=player_zone, use_2d=True):
                    pois.append((e.x, e.y, e.z, "enemy", e.unit_id or "?", f"e{e.addr}"))
            if getattr(s, "show_group_members", False):
                for e, d in self.model.nearest_group_members(xyz, n=20, max_dist=max_dist, player_zone=player_zone, use_2d=True):
                    if e.addr != self.model.player_addr:
                        cls_name = e.cls or ""
                        h_class = "warrior"
                        if cls_name.startswith("ent.hero."):
                            h_class = cls_name.replace("ent.hero.", "").lower()
                        elif e.unit_id:
                            h_class = e.unit_id.lower()
                        pois.append((e.x, e.y, e.z, f"hero_{h_class}", e.unit_id or "?", f"hero{e.addr}"))
            if getattr(s, "minimap_companions", True):
                # Companion range: False = 200m, Number = that range, True = max_dist (fallback)
                c_debug = getattr(s, "show_companions_debug", False)
                if isinstance(c_debug, (int, float)) and not isinstance(c_debug, bool):
                    c_dist = float(c_debug)
                else:
                    c_dist = 200.0 if c_debug is False else max_dist
                for e, d in self.model.nearest_companions(xyz, n=100, max_dist=c_dist, player_zone=player_zone, use_2d=True):
                    pois.append((e.x, e.y, e.z, "companion", e.unit_id or "?", f"comp{e.addr}"))
            if s.minimap_gatherables:
                # 1. Live memory elements first (accurate current state)
                for g in self.model.gatherables(player_zone=player_zone, max_dist=max_dist, use_2d=True):
                    label = g.elem_id or "?"
                    name_l = label.lower()
                    is_ore = "ore" in name_l or "tungstene" in name_l or "tin" in name_l or "copper" in name_l
                    kind = "ore" if is_ore else "flower"
                    
                    # Check if this gatherable type is enabled in settings
                    setting_attr = geo_gatherables.get_setting_attr(label)
                    if setting_attr and not getattr(s, setting_attr, True):
                        continue
                    
                    pois.append((g.x, g.y, g.z, kind, label, f"gl{g.addr}"))

                # 2. Static nodes from JSON (long-range potential spawns)
                for g in geo_gatherables.load_nodes():
                    if player_zone and geo_zones.resolve_zone(g.x, g.y, g.z) != player_zone:
                        continue
                    if not player_zone and max_dist > 0 and math.hypot(g.x - self._px, g.y - self._py) > max_dist:
                        continue
                    
                    # Avoid duplicates with live memory nodes
                    if any(math.hypot(g.x - px, g.y - py) < 2.0 for px, py, pz, pk, pl, pi in pois if pk in ("ore", "flower")):
                        continue

                    label = g.name
                    name_l = g.name.lower()
                    is_ore = "ore" in name_l or "tungstene" in name_l or "tin" in name_l or "copper" in name_l
                    kind = "ore" if is_ore else "flower"
                    
                    # Check if this gatherable type is enabled in settings
                    setting_attr = geo_gatherables.get_setting_attr(label)
                    if setting_attr and not getattr(s, setting_attr, True):
                        continue
                    
                    pois.append((g.x, g.y, g.z, kind, label, f"g{g.x}{g.y}"))
            if s.minimap_obelisks:
                # 1. Live memory elements (status-aware)
                for o in self.model.obelisks(player_zone=player_zone, max_dist=max_dist, use_2d=True):
                    eid_l = (o.elem_id or "").lower()
                    state_l = (o.state or "").lower()
                    if any(x in eid_l for x in ("checkpoint", "start_", "finish_")):
                        if state_l in ("disabled", "disable", "activated"):
                            continue
                    elif state_l in ("disabled", "disable", "desactivated"):
                        continue
                    pois.append((o.x, o.y, o.z, o.kind, o.elem_id or o.kind,
                                 o.elem_id or f"ob{o.addr}"))

                # 2. Static POIs (obelisks and respawn points)
                for p in geo_pois.load_pois():
                    if p.sub_kind not in ("obelisk", "respawn"):
                        continue
                    if player_zone and geo_zones.get_area_id(p.zone) != player_zone:
                        continue
                    if not player_zone and max_dist > 0 and p.dist2d(self._px, self._py) > max_dist:
                        continue
                    # Avoid duplicates with live memory obelisks/checkpoints
                    if any(math.hypot(p.x - px, p.y - py) < 5.0 for px, py, pz, pk, pl, pi in pois if pk in ("obelisk", "respawn", "chest_orb")):
                        continue
                    pois.append((p.x, p.y, p.z, p.sub_kind, p.name or p.sub_kind, p.id))
            if s.minimap_orbs:
                # static world orbs (Collector achievements) + live dungeon
                # orbs; collected ones (auto-synced or right-clicked) draw
                # greyed via the done state (or hide if hide_collected is on)
                for ob in geo_orbs.load_orbs():
                    if player_zone and geo_zones.get_area_id(ob.zone) != player_zone:
                        continue
                    if not player_zone and max_dist > 0 and ob.dist2d(self._px, self._py) > max_dist:
                        continue
                    if hide_coll and s.is_done(ob.orb_id, profile):
                        continue
                    pois.append((ob.x, ob.y, ob.z, "orb", ob.orb_id, ob.orb_id))
                for e in self.model.live_orbs(player_zone=player_zone, max_dist=max_dist, use_2d=True):
                    eid = e.elem_id or f"orb{e.addr}"
                    if hide_coll and e.elem_id and s.is_done(e.elem_id, profile):
                        continue
                    pois.append((e.x, e.y, e.z, "orb", e.elem_id or "orb", eid))
                for e in self.model.live_chest_orbs():
                    if not e.elem_id:
                        continue
                    # Common prefix for orbs in the same group (Chest or TimerRun)
                    parent_id = e.elem_id
                    for sep in ("_StartOrb_", "_startorb_", "_Orb_", "_orb_"):
                        if sep in e.elem_id:
                            parent_id = e.elem_id.split(sep)[0]
                            break
                    is_disabled = e.state and e.state.lower() in ("disabled", "disable")
                    parent_done = s.is_done(parent_id, profile)
                    if is_disabled or parent_done:
                        continue
                    pois.append((e.x, e.y, e.z, "chest_orb", e.elem_id, e.elem_id))
            if s.minimap_dungeons:
                # 1. Live memory teleporters
                for t in self.model.teleporters():
                    pois.append((t.x, t.y, t.z, "dungeon", t.elem_id or "teleport",
                                 t.elem_id or f"tp{t.addr}"))

                # 2. Static dungeon entrances
                for p in geo_pois.load_pois():
                    if p.sub_kind != "dungeon":
                        continue
                    if player_zone and geo_zones.get_area_id(p.zone) != player_zone:
                        continue
                    if not player_zone and max_dist > 0 and p.dist2d(self._px, self._py) > max_dist:
                        continue
                    # Avoid duplicates with live memory dungeons
                    if any(math.hypot(p.x - px, p.y - py) < 5.0 for px, py, pz, pk, pl, pi in pois if pk == "dungeon"):
                        continue
                    label = p.name or p.target_activity or "Dungeon"
                    pois.append((p.x, p.y, p.z, "dungeon", label, p.id))
        except Exception:
            pass

        # Force include the player's currently tracked target
        try:
            tk, tid = s.track_kind, s.track_id
            if tk == "orb" and tid:
                if not any(p[3] == "orb" and p[5] == tid for p in pois):
                    o = geo_orbs.by_id().get(tid)
                    if o:
                        pois.append((o.x, o.y, o.z, "orb", o.orb_id, o.orb_id))
            elif tk == "unit" and tid:
                if not any(p[4] == tid for p in pois):
                    from ...data import units as udata
                    for e in self.model.units():
                        if e.unit_id == tid:
                            kind = "companion" if udata.is_companion(e.unit_id) else "enemy"
                            pois.append((e.x, e.y, e.z, kind, e.unit_id or "?", f"{'comp' if kind == 'companion' else 'e'}{e.addr}"))
            elif tk == "pos" and tid:
                try:
                    coords, _, label = tid.partition("|")
                    tx, ty, tz = (float(v) for v in coords.split(","))
                    if not any(abs(p[0] - tx) < 0.5 and abs(p[1] - ty) < 0.5 for p in pois):
                        pois.append((tx, ty, tz, "pos", label or "Waypoint", "tracked_pos"))
                except ValueError:
                    pass
        except Exception:
            pass

        # Sort all POIs by 2D distance to player so the closest are drawn LAST (on top)
        pois.sort(key=lambda p: math.hypot(p[0] - self._px, p[1] - self._py), reverse=True)

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
        square = self.s.minimap_shape == "Square"
        if square:
            rad_x = w / 2 - 4
            rad_y = h / 2 - 4
        else:
            rad = min(w, h) / 2 - 4
            rad_x = rad
            rad_y = rad
        # backdrop + clip
        p.setBrush(QtGui.QColor(theme.PANEL))
        p.setPen(QtGui.QColor(theme.BORDER))
        # clip with a QPainterPath (transform-aware, so the rotated map texture
        # below is clipped correctly - a QRegion clip is not)
        clip = QtGui.QPainterPath()
        if square:
            box = QtCore.QRectF(4, 4, w - 8, h - 8)
            p.drawRect(box)
            clip.addRect(box)
        else:
            p.drawEllipse(QtCore.QPointF(cx, cy), rad_x, rad_y)
            clip.addEllipse(QtCore.QPointF(cx, cy), rad_x, rad_y)
        p.setClipPath(clip)
        scale = self._scale()
        phi = self._phi()
        # world map texture (under the rings/POIs)
        if self.s.minimap_texture:
            self._draw_map(p, cx + self._pan_x, cy + self._pan_y, scale, phi)
        # rings (outline only - NoBrush, else they'd fill over the map texture)
        p.setPen(QtGui.QPen(QtGui.QColor(theme.BORDER), 1))
        p.setBrush(QtCore.Qt.NoBrush)
        for f in (1.0,):
            if square:
                p.drawRect(QtCore.QRectF(cx - rad_x * f, cy - rad_y * f, rad_x * 2 * f, rad_y * 2 * f))
            else:
                p.drawEllipse(QtCore.QPointF(cx, cy), rad_x * f, rad_y * f)
        # POIs
        track_pos = self._track_pos()
        profile = self.model.player_profile()
        edge_counts = {"chest": 0, "orb": 0}
        for (wx, wy, _wz, kind, label, poi_id) in self._pois:
            orig_kind = kind
            hero_class = None
            if kind.startswith("hero_"):
                hero_class = kind.split("_", 1)[1]
                kind = "hero"
            dx, dy = self._rel(wx, wy, scale, phi)
            dx_c = dx + self._pan_x
            dy_c = dy - self._pan_y
            edge = False
            if square:
                limit_x = rad_x - 4
                limit_y = rad_y - 4
                kx = limit_x / abs(dx_c) if dx_c != 0 else float('inf')
                ky = limit_y / abs(dy_c) if dy_c != 0 else float('inf')
                k = min(kx, ky)
                if k < 1.0:
                    dx_c *= k; dy_c *= k
                    edge = True
            else:
                dist = math.hypot(dx_c, dy_c)
                limit_r = rad_x - 4
                if dist > limit_r:
                    if dist == 0:
                        continue
                    k = limit_r / dist
                    dx_c *= k; dy_c *= k
                    edge = True

            done = bool(poi_id and self.s.is_done(poi_id, profile))
            is_wp = self._is_waypoint(kind, label, poi_id, wx, wy, track_pos)
            if edge:
                if kind in ("obelisk", "respawn", "dungeon"):
                    continue
                dist_to_player = math.hypot(wx - self._px, wy - self._py)
                if not is_wp and (done or dist_to_player > 500.0):
                    continue

            if edge and kind in edge_counts:
                if not is_wp and edge_counts[kind] >= 5:
                    continue
                if not is_wp:
                    edge_counts[kind] += 1

            sx, sy = cx + dx_c, cy - dy_c
            sz = self.s.minimap_icon_size
            if kind == "dungeon":
                sz = int(sz * 1.25)
            elif kind == "obelisk":
                sz += 2
            elif kind in ("flower", "ore"):
                sz = max(1, sz - 2)
                # Auto-scale based on resource size suffix or type
                label_l = label.lower()
                if "_large" in label_l or "_big" in label_l or "tungstene" in label_l:
                    sz = int(sz * 1.3)
                elif "_small" in label_l:
                    sz = int(sz * 0.85)
            pm = self._poi_pixmap(orig_kind, label, sz, done) \
                if (self.s.minimap_icons or (USE_HERO_SVGS and orig_kind.startswith("hero_"))) else None
            if self._is_waypoint(kind, label, poi_id, wx, wy, track_pos):
                # the compass waypoint: accent ring so the target is obvious
                if kind == "enemy":
                    ring = QtGui.QColor(theme.DANGER)
                elif kind == "companion":
                    is_spark = "spark" in (names.unit_name(label) or "").lower()
                    ring = QtGui.QColor(theme.GOLD if is_spark else theme.GOOD)
                else:
                    ring = QtGui.QColor(self.s.hud_accent)
                p.setPen(QtGui.QPen(ring, 2))
                p.setBrush(QtCore.Qt.NoBrush)
                r_hl = (sz / 2 + 3) if pm is not None else 6
                p.drawEllipse(QtCore.QPointF(sx, sy), r_hl, r_hl)
            if pm is not None:
                if done:
                    p.setOpacity(0.60)
                z = pm.width()
                p.drawPixmap(int(sx - z / 2), int(sy - z / 2), pm)
                if done:
                    p.setOpacity(1.0)
            else:
                if kind == "hero" and hero_class:
                    col_hex = {
                        "warrior": "#f1a02b",
                        "rogue": "#4ade80",
                        "mage": "#38bdf8",
                        "priest": "#eac331",
                    }.get(hero_class, theme.TEXT)
                    col = QtGui.QColor(col_hex)
                else:
                    col = QtGui.QColor(theme.KIND_COLOR.get(kind, theme.TEXT))
                if done:
                    col.setAlpha(160)
                if kind == "hero":
                    p.setPen(QtGui.QPen(QtGui.QColor(theme.BG), 1))
                    p.setBrush(col)
                    p.drawEllipse(QtCore.QPointF(sx, sy), 5 if edge else 6, 5 if edge else 6)
                else:
                    p.setPen(QtCore.Qt.NoPen)
                    p.setBrush(col)
                    p.drawEllipse(QtCore.QPointF(sx, sy), 3 if edge else 4, 3 if edge else 4)
        # compass + player (drawn unclipped so edge letters aren't cut)
        p.setClipping(False)
        self._draw_compass(p, cx, cy, min(w, h) / 2 - 4, phi)
        # player marker + facing arrow (uses the live Highlight color or arrow.png)
        accent = QtGui.QColor(self.s.hud_accent)
        # north-up frame: world heading's y-component inverts, so -heading
        fwd = (phi - self._heading) if self._heading is not None else (math.pi / 2)

        # Player arrow size (x1.3 multiplier)
        arrow_sz = int(self.s.minimap_icon_size * 1.3)
        arrow_pm = icons.asset_icon("arrow", arrow_sz)

        if arrow_pm is not None and not arrow_pm.isNull():
            p.save()
            p.translate(cx + self._pan_x, cy + self._pan_y)
            deg = 90.0 - math.degrees(fwd)
            p.rotate(deg)
            p.drawPixmap(int(-arrow_pm.width() / 2), int(-arrow_pm.height() / 2), arrow_pm)
            p.restore()
        else:
            p.setBrush(accent)
            p.setPen(QtCore.Qt.NoPen)
            p.drawEllipse(QtCore.QPointF(cx + self._pan_x, cy + self._pan_y), 4, 4)
            p.setPen(QtGui.QPen(accent, 2))
            p.drawLine(QtCore.QPointF(cx + self._pan_x, cy + self._pan_y),
                       QtCore.QPointF(cx + self._pan_x + math.cos(fwd) * 13, cy + self._pan_y - math.sin(fwd) * 13))
        p.end()

    def _draw_map(self, p, cx, cy, scale, phi):
        """Blit the world map so the player sits at centre, facing up, matching
        the POI projection. image px = (S*x+OX, S*y+OY); we map image->screen
        with the same rotate(phi)+scale the POIs use and let Qt clip."""
        pm = _map_pixmap()
        if pm is None:
            return
        s, A = scale, C.MAP_SCALE
        cosf, sinf = math.cos(phi), math.sin(phi)
        u0 = -C.X_OFFSET - self._px      # world x at image px 0
        w0 = C.Y_OFFSET + self._py       # paired with the north-up Y row below
        t = QtGui.QTransform(
            s * cosf / A, -s * sinf / A,    # m11, m12  (coeffs of image x)
            s * sinf / A, s * cosf / A,     # m21, m22  (coeffs of image y; Y unflipped)
            cx + s * cosf * u0 - s * sinf * w0,
            cy - s * sinf * u0 - s * cosf * w0)
        p.save()
        p.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
        p.setTransform(t, True)
        p.drawPixmap(0, 0, pm)

        # Apply Fog of War boundary if defined
        if C.MAP_BOUNDS:
            try:
                from ... import paths
                fog_p = None
                for name in ("fog.webp", "fog.png"):
                    if (paths.assets_dir() / "map" / name).exists():
                        fog_p = paths.assets_dir() / "map" / name
                        break

                if fog_p:
                    fog_pm = QtGui.QPixmap(str(fog_p))
                    if not fog_pm.isNull():
                        # We are inside the transformed space 't'.
                        # The coordinate system here is raw image pixels (0..width, 0..height).
                        # We must convert MAP_BOUNDS (world) to these raw image pixels.
                        w_x1, w_y1, w_x2, w_y2 = C.MAP_BOUNDS

                        # World -> Image pixels (0..dim)
                        pix_x1 = (w_x1 + C.X_OFFSET) * C.MAP_SCALE
                        pix_y1 = (w_y1 + C.Y_OFFSET) * C.MAP_SCALE
                        pix_x2 = (w_x2 + C.X_OFFSET) * C.MAP_SCALE
                        pix_y2 = (w_y2 + C.Y_OFFSET) * C.MAP_SCALE

                        brush = QtGui.QBrush(fog_pm)
                        path = QtGui.QPainterPath()
                        # Outer: the whole image
                        path.addRect(QtCore.QRectF(0, 0, pm.width(), pm.height()))
                        # Inner: the playable hole (ensure it's a valid rect)
                        hole = QtCore.QRectF(pix_x1, pix_y1, pix_x2 - pix_x1, pix_y2 - pix_y1)
                        path.addRect(hole)

                        path.setFillRule(QtCore.Qt.OddEvenFill)
                        p.setBrush(brush)
                        p.setPen(QtCore.Qt.NoPen)
                        p.drawPath(path)
            except Exception:
                pass

        p.restore()
        # No veil: keep the map art crisp

    # bundled flat marker icons (assets/map_icons) per layer
    _MARKER = {"chest": "chest", "flower": "flower", "ore": "ore", "obelisk": "obelisk",
               "orb": "orb", "activity": "activity", "dungeon": "dungeon", "respawn": "respawnpoint",
               "recipe": "recipe", "chest_orb": "goldorb"}

    def _poi_pixmap(self, kind, label, size, done=False):
        """A POI marker pixmap: enemies show the real per-unit game icon (organic
        outline); chests/crates, gatherables and obelisks use the bundled flat
        marker icons (assets/map_icons). Falls back to a tinted UI
        glyph, then to a plain dot."""
        if USE_HERO_SVGS and kind.startswith("hero_"):
            hero_class = kind.split("_", 1)[1]
            col_hex = {
                "warrior": "#f1a02b",
                "rogue": "#4ade80",
                "mage": "#38bdf8",
                "priest": "#eac331",
            }.get(hero_class, theme.TEXT)
            return icons.ui_icon("player", col_hex, size)
        if kind in ("enemy", "companion") and label and icons.has_icon("Units", label):
            outline_col = theme.GOOD if kind == "companion" else theme.DANGER
            if kind == "companion" and "spark" in (names.unit_name(label) or "").lower():
                outline_col = theme.GOLD
            return icons.outlined("Units", label, size, outline_col)

        # Specific icons for ores/flowers based on their name
        if kind in ("flower", "ore") and label:
            label_l = label.lower()
            # 1. Try full name (e.g. copperore_large)
            pm = icons.marker(label_l, size)
            if pm: return pm
            # 2. Try base name with shared mapping from gatherables.py
            icon_name = geo_gatherables.get_icon_name(label)
            if icon_name != label_l:
                pm = icons.marker(icon_name, size)
                if pm: return pm

        name = self._MARKER.get(kind)
        if name:
            if done and name in ("chest", "orb", "recipe"):
                name = name + "2"
            pm = icons.marker(name, size)
            if pm is not None:
                return pm
        glyph = {"chest": "box", "obelisk": "radio", "flower": "dice-5", "ore": "dice-5",
                 "enemy": "swords", "orb": "broadcast", "activity": "box",
                 "dungeon": "map", "companion": "heart", "pos": "map-pin",
                 "recipe": "box", "chest_orb": "broadcast"}.get(kind)
        if glyph:
            return icons.ui_icon(glyph, theme.KIND_COLOR.get(kind, theme.TEXT), size)
        return None

    def _draw_compass(self, p, cx, cy, rad, phi):
        # Scale font and box size with the minimap radius
        base_fsize = max(7, int(rad / 12) - 3)
        bsize = base_fsize * 3  # Enough room for growth
        r = rad - (base_fsize / 2 + 1)

        f = p.font(); f.setBold(True)
        # label, world_angle (for highlight), draw_angle (for position), color
        cardinals = [
            ("N", -math.pi / 2, math.pi / 2, theme.TEXT),
            ("E", 0.0, 0.0, theme.TEXT),
            ("S", math.pi / 2, -math.pi / 2, theme.TEXT),
            ("W", math.pi, math.pi, theme.TEXT),
        ]

        for label, world_ang, draw_ang, col in cardinals:
            # Highlight the letter if the character is facing that way
            is_facing = False
            if self._heading is not None:
                # Calculate angular distance between player world heading and cardinal direction
                diff = abs((self._heading - world_ang + math.pi) % (math.pi * 2) - math.pi)
                if diff < math.radians(50):  # 50 degree window allows lighting up both for NE/NW/etc
                    is_facing = True

            final_col = QtGui.QColor(self.s.hud_accent) if is_facing else QtGui.QColor(col)
            f.setPointSize(base_fsize + 2 if is_facing else base_fsize)
            p.setFont(f)

            a = draw_ang + phi
            lx = cx + math.cos(a) * r
            ly = cy - math.sin(a) * r

            # Draw a subtle dark shadow first for high contrast over map art
            p.setPen(QtGui.QColor(0, 0, 0, 160))
            p.drawText(QtCore.QRectF(lx - bsize / 2 + 1, ly - bsize / 2 + 1, bsize, bsize),
                       QtCore.Qt.AlignCenter, label)

            p.setPen(final_col)
            p.drawText(QtCore.QRectF(lx - bsize / 2, ly - bsize / 2, bsize, bsize),
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
            return kind in ("enemy", "companion") and label == tid
        if tk == "gather":
            if kind not in ("ore", "flower", "gather"):
                return False
            # Check if this node matches the tracked coordinates
            try:
                coords = tid.split("|", 1)[0].split(",")
                tx, ty = float(coords[0]), float(coords[1])
                return abs(wx - tx) < 0.5 and abs(wy - ty) < 0.5
            except (ValueError, IndexError):
                return False
        if tk == "pos" and track_pos is not None:
            return (abs(wx - track_pos[0]) < 0.5 and abs(wy - track_pos[1]) < 0.5) or poi_id == "tracked_pos"
        return False

    # overlapping markers: the click prefers the kind the player most likely
    # aims at (orbs are small primary targets and lose a raw nearest-test)
    _CLICK_PRIORITY = {"orb": 0, "enemy": 1, "chest": 2, "activity": 2,
                       "dungeon": 3, "obelisk": 4, "flower": 5, "ore": 5}

    def _poi_at(self, pos):
        """The POI under a click, hit-testing the *drawn* position - same edge
        clamp as paintEvent, so far markers are clickable on the ring. Among
        everything within the hit radius, kind priority breaks the overlap."""
        w, h = self.width(), self.height()
        cx, cy = w / 2, h / 2
        square = self.s.minimap_shape == "Square"
        if square:
            rad_x = w / 2 - 4
            rad_y = h / 2 - 4
        else:
            rad = min(w, h) / 2 - 4
            rad_x = rad
            rad_y = rad
        scale = self._scale()
        phi = self._phi()
        best, best_rank = None, None
        for poi in self._pois:
            wx, wy = poi[0], poi[1]
            dx, dy = self._rel(wx, wy, scale, phi)
            dx_c = dx + self._pan_x
            dy_c = dy - self._pan_y
            if square:
                limit_x = rad_x - 4
                limit_y = rad_y - 4
                kx = limit_x / abs(dx_c) if dx_c != 0 else float('inf')
                ky = limit_y / abs(dy_c) if dy_c != 0 else float('inf')
                k = min(kx, ky)
                if k < 1.0:
                    dx_c *= k; dy_c *= k
            else:
                dist = math.hypot(dx_c, dy_c)
                limit_r = rad_x - 4
                if dist > limit_r and dist > 0:
                    k = limit_r / dist
                    dx_c *= k; dy_c *= k
            d = math.hypot(cx + dx_c - pos.x(), cy - dy_c - pos.y())
            if d >= 16.0:
                continue
            rank = (self._CLICK_PRIORITY.get(poi[3], 9), d)
            if best_rank is None or rank < best_rank:
                best, best_rank = poi, rank
        return best

    def mousePressEvent(self, e):
        if e.button() == QtCore.Qt.RightButton:
            self._right_drag_start = e.position()
            self._pan_start_x = self._pan_x
            self._pan_start_y = self._pan_y
            self._right_click_panned = False
            self._last_player_pos = (self._px, self._py, self._pz)
            return
        if e.button() == QtCore.Qt.LeftButton and not getattr(self.window(), "_locked", False):
            # any marker is a compass waypoint: enemies track the unit, orbs
            # the orb, everything else a fixed position
            tr = getattr(self.window(), "_tracker", None)
            poi = self._poi_at(e.position())
            if poi is not None and tr is not None:
                wx, wy, wz, kind, label, poi_id = poi

                # Block tracking/clicking for certain static reference markers
                if kind in ("obelisk", "respawn", "dungeon"):
                    return

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
                        # Temporarily remove tracker to prevent select_by_key from double-tracking
                        old_tracker = entity_ov._tracker
                        entity_ov._tracker = None
                        try:
                            # Map minimap internal types back to entity HUD group types
                            ekind = "gather" if kind in ("ore", "flower") else kind
                            entity_ov.select_by_key(ekind, clean_key, open_drops=False)
                        finally:
                            entity_ov._tracker = old_tracker

                if kind == "orb" and poi_id and poi_id in geo_orbs.by_id():
                    tr.toggle("orb", poi_id)
                elif kind in ("enemy", "companion") and label and label != "?":
                    tr.toggle("unit", label)
                elif kind in ("ore", "flower"):
                    tr.toggle("gather", f"{wx:.1f},{wy:.1f},{wz:.1f}|{label or kind}")
                else:
                    # Specific kinds for chests or generic pos for anything else
                    tk = "chest" if kind in ("chest", "recipe", "activity") else "pos"
                    tr.toggle(tk, f"{wx:.1f},{wy:.1f},{wz:.1f}|{label or kind}")
                return
            # drag the window from the map body (the only grip in bare mode)
            self._drag = e.globalPosition().toPoint() - self.window().frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._drag is not None and e.buttons() & QtCore.Qt.LeftButton:
            self.window().move(e.globalPosition().toPoint() - self._drag)
        elif self._right_drag_start is not None and e.buttons() & QtCore.Qt.RightButton:
            delta = e.position() - self._right_drag_start
            if delta.manhattanLength() > 3:
                self._right_click_panned = True

                # Current intended pan in screen pixels
                raw_pan_x = self._pan_start_x + delta.x()
                raw_pan_y = self._pan_start_y + delta.y()

                if C.MAP_BOUNDS:
                    # Calculate the world position the center of the minimap is looking at.
                    # Pan shifts the "camera target" away from the player's position.
                    scale = self._scale()
                    phi = self._phi()
                    c, s = math.cos(phi), math.sin(phi)

                    # Screen pan -> unrotated screen delta (rx, ry)
                    # Note: sy = cy - dy_c in paintEvent, so we flip pan_y sign
                    rx, ry = -raw_pan_x, raw_pan_y
                    dx = rx * c + ry * s
                    dy = -rx * s + ry * c

                    # Unrotated screen delta -> world delta
                    tx = self._px + dx / scale
                    ty = self._py - dy / scale

                    # Clamp the "view center" to the playable fog-free bounds
                    x1, y1, x2, y2 = C.MAP_BOUNDS
                    tx = max(x1, min(x2, tx))
                    ty = max(y1, min(y2, ty))

                    # Convert clamped world position back to screen pan pixels
                    dx = (tx - self._px) * scale
                    dy = (self._py - ty) * scale
                    rx = dx * c - dy * s
                    ry = dx * s + dy * c

                    self._pan_x = -rx
                    self._pan_y = ry
                else:
                    self._pan_x = raw_pan_x
                    self._pan_y = raw_pan_y

                self.update()

    def mouseReleaseEvent(self, e):
        if self._drag is not None:
            self._drag = None
            win = self.window()
            if hasattr(win, "persist_geometry"):
                win.persist_geometry()
        if e.button() == QtCore.Qt.RightButton:
            if not self._right_click_panned:
                self._mark_done(e)
            self._right_drag_start = None

    def mouseDoubleClickEvent(self, e):
        if e.button() == QtCore.Qt.LeftButton:
            win = self.window()
            if hasattr(win, "set_bare"):
                win.set_bare(not win.s.minimap_bare)

    def _mark_done(self, e):
        # mark the nearest plotted POI done (hit-test in the same rotated space)
        cx, cy = self.width() / 2, self.height() / 2
        scale = self._scale()
        phi = self._phi()
        best, bestd = None, 12.0
        for (wx, wy, _wz, _k, _l, poi_id) in self._pois:
            dx, dy = self._rel(wx, wy, scale, phi)
            dx_c = dx + self._pan_x
            dy_c = dy - self._pan_y
            sx, sy = cx + dx_c, cy - dy_c
            d = math.hypot(sx - e.position().x(), sy - e.position().y())
            if d < bestd and poi_id:
                best, bestd = poi_id, d
        if best:
            profile = self.model.player_profile()
            self.s.toggle_done(best, profile)
            # collecting the needle's target ends the tracking
            tr = getattr(self.window(), "_tracker", None)
            if tr is not None and self.s.is_done(best, profile):
                should_clear = False
                if tr.is_tracked("orb", best):
                    should_clear = True
                elif self.s.track_kind == "pos" and self.s.track_id:
                    try:
                        coords = self.s.track_id.split("|", 1)[0].split(",")
                        tx, ty = float(coords[0]), float(coords[1])
                        poi_entry = next((p for p in self._pois if p[5] == best), None)
                        if poi_entry:
                            pwx, pwy = poi_entry[0], poi_entry[1]
                            if math.hypot(tx - pwx, ty - pwy) < 1.0:
                                should_clear = True
                    except (ValueError, IndexError):
                        pass
                if should_clear:
                    tr.clear()
            self.update()


class MinimapOverlay(OverlayWindow):
    def __init__(self, model, settings, parent=None):
        super().__init__("Minimap", settings, geo_key="minimap", parent=parent)
        self.s = settings
        self._tracker = None
        self._sync_fn = None   # callable(bool) set by overlay_manager to sync map-page toggle
        self._zoom_sync_fn = None # callable(int) set by overlay_manager to sync map-page zoom slider

        zin = QtWidgets.QPushButton("+"); zin.setObjectName("Icon")
        zout = QtWidgets.QPushButton("−"); zout.setObjectName("Icon")
        zin.setStyleSheet("color: #22c55e; font-size: 20px; font-weight: bold; margin-bottom: 2px;") # green
        zout.setStyleSheet("color: #38bdf8; font-size: 20px; font-weight: bold; margin-bottom: 2px;") # blue
        zin.clicked.connect(lambda: self._zoom(0.8))
        zout.clicked.connect(lambda: self._zoom(1.25))

        # Hide-collected toggle button (eye icon) in the titlebar
        self._hide_btn = QtWidgets.QPushButton()
        self._hide_btn.setObjectName("Icon")
        self._hide_btn.setFixedSize(22, 22)
        self._hide_btn.setToolTip("Hide collected orbs & chests")
        self._hide_btn.clicked.connect(self._toggle_hide_collected)
        self._update_hide_btn()

        for wdg in (zout, zin, self._hide_btn):
            self.titlebar.extra.insertWidget(self.titlebar.extra.count() - 2, wdg)

        self.canvas = _Canvas(model, settings)
        self.content.addWidget(self.canvas, 1)
        self._hint = QtWidgets.QLabel("Double click to hide/show boarder")
        self._hint.setObjectName("Muted")
        self._hint.setAlignment(QtCore.Qt.AlignCenter)
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
        super().set_bare(on)
        self._hint.setVisible(not on)
        self.canvas.update()

    def _zoom(self, f):
        self.s.minimap_zoom = max(2.0, min(60.0, self.s.minimap_zoom * f))
        self.s.save()
        if self._zoom_sync_fn is not None:
            self._zoom_sync_fn(int(self.s.minimap_zoom))
        self.canvas.update()

    def _toggle_hide_collected(self):
        self.set_hide_collected(not getattr(self.s, "minimap_hide_collected", False))

    def set_hide_collected(self, on: bool) -> None:
        """Toggle hide-collected; updates setting, button icon, canvas, and any
        registered sync callback (e.g. the map-page toggle)."""
        self.s.minimap_hide_collected = on
        self.s.save()
        self._update_hide_btn()
        self.canvas.refresh()
        if self._sync_fn is not None:
            self._sync_fn(on)

    def _update_hide_btn(self) -> None:
        on = getattr(self.s, "minimap_hide_collected", False)
        icon_name = "eye-off" if on else "eye"
        accent = self.s.hud_accent if on else theme.MUTED
        pm = icons.ui_icon(icon_name, accent, 18)
        self._hide_btn.setIcon(QtGui.QIcon(pm))
        self._hide_btn.setIconSize(QtCore.QSize(18, 18))
        tip = "Show collected orbs & chests" if on else "Hide collected orbs & chests"
        self._hide_btn.setToolTip(tip)

    def set_tracker(self, tracker) -> None:
        """The shared TrackController; the tracked orb shows as an accent ring
        and right-click-done on it ends the tracking."""
        self._tracker = tracker
        tracker.changed.connect(self.canvas.update)

    def closeEvent(self, e):
        self._fast.stop()
        self._slow.stop()
        super().closeEvent(e)
