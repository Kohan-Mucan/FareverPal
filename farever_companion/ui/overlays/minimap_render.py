"""Pure painting / hit-test helpers for the minimap canvas.

Kept out of `minimap.py` so that overlay stays under the UI line budget.
Every function takes the canvas (`cv`) explicitly and reads its state
(`_pois`, `_pan_*`, `_scale()`, `_phi()`, ...); nothing here knows about the
overlay window itself.
"""
from __future__ import annotations

import math

from PySide6 import QtCore, QtGui

from .. import theme
from ... import constants as C
from ...data import icons, names, units as udata
from ...geo import gatherables as geo_gatherables, orbs as geo_orbs

# Set to True to use player SVGs instead of dots for group members.
USE_HERO_SVGS = False

# --- map-image pixel transform ---------------------------------------------
_MAP_PM = None
_MAP_TRIED = False


def map_pixmap():
    """Detect which map asset to use. Prefer 'map.webp', then 'map.png'."""
    global _MAP_PM, _MAP_TRIED
    if not _MAP_TRIED:
        _MAP_TRIED = True
        try:
            from ... import paths
            QtGui.QImageReader.setAllocationLimit(1024)
            p_dir = paths.assets_dir() / "map"
            candidates = list(p_dir.glob("map.webp")) + list(p_dir.glob("map.png"))
            if candidates:
                candidates.sort(key=lambda x: x.stat().st_size, reverse=True)
                pm = QtGui.QPixmap(str(candidates[0]))
                if not pm.isNull():
                    _MAP_PM = pm
        except Exception:
            _MAP_PM = None
    return _MAP_PM


def draw_map(cv, p, cx, cy, scale, phi):
    pm = map_pixmap()
    if pm is None:
        return
    s, A = scale, C.MAP_SCALE
    cosf, sinf = math.cos(phi), math.sin(phi)
    u0, w0 = -C.X_OFFSET - cv._px, C.Y_OFFSET + cv._py
    t = QtGui.QTransform(s * cosf / A, -s * sinf / A, s * sinf / A, s * cosf / A,
                         cx + s * cosf * u0 - s * sinf * w0,
                         cy - s * sinf * u0 - s * cosf * w0)
    p.save()
    p.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
    p.setTransform(t, True)
    p.drawPixmap(0, 0, pm)
    if C.MAP_BOUNDS:
        try:
            from ... import paths
            fog_p = (paths.assets_dir() / "map" / "fog.webp") if (paths.assets_dir() / "map" / "fog.webp").exists() else ((paths.assets_dir() / "map" / "fog.png") if (paths.assets_dir() / "map" / "fog.png").exists() else None)
            if fog_p:
                fog_pm = QtGui.QPixmap(str(fog_p))
                if not fog_pm.isNull():
                    x1, y1, x2, y2 = C.MAP_BOUNDS
                    px1, py1, px2, py2 = (x1 + C.X_OFFSET) * A, (y1 + C.Y_OFFSET) * A, (x2 + C.X_OFFSET) * A, (y2 + C.Y_OFFSET) * A
                    path = QtGui.QPainterPath()
                    path.addRect(QtCore.QRectF(0, 0, pm.width(), pm.height()))
                    path.addRect(QtCore.QRectF(px1, py1, px2 - px1, py2 - py1))
                    path.setFillRule(QtCore.Qt.OddEvenFill)
                    p.setBrush(QtGui.QBrush(fog_pm))
                    p.setPen(QtCore.Qt.NoPen)
                    p.drawPath(path)
        except Exception:
            pass
    p.restore()


_MARKER = {
    "chest": "chest", "flower": "flower", "ore": "ore", "obelisk": "obelisk",
    "checkpoint": "goldorb", "orb": "orb", "activity": "activity",
    "dungeon": "dungeon", "respawn": "respawnpoint", "recipe": "recipe",
    "chest_orb": "goldorb", "rift": "rift", "spark_enemy": "swords",
    "petshop": "shop", "mountshop": "shop", "vendor": "shop",
}


def poi_pixmap(cv, kind, label, size, done=False, tracked=False):
    if USE_HERO_SVGS and kind.startswith("hero_"):
        return icons.ui_icon("player", theme.HERO.get(kind.split("_", 1)[1], theme.TEXT), size)

    if kind in ("enemy", "companion", "spark_enemy") and label:
        sheet = "collection" if kind == "companion" else "Units"
        if icons.has_icon(sheet, label):
            is_special = False
            if kind == "companion":
                is_spark = "spark" in (names.unit_name(label) or "").lower()
                col = theme.GOLD if is_spark else theme.GOOD
                is_special = is_spark
            elif kind == "spark_enemy":
                col, is_special = "#a855f7", True
            else:  # regular enemy
                col = theme.DANGER
                if udata.is_boss(label):
                    col, is_special = theme.GOLD, True
                elif udata.is_elite(label):
                    col, is_special = theme.SILVER, True

            if (kind == "enemy" or kind == "spark_enemy") and not tracked and not is_special:
                return icons.pixmap(sheet, label, size)
            # 2px outline for tracked/special mobs
            return icons.outlined(sheet, label, size, col, border=3)

    # Determine accent for outlines on other items
    accent = None
    is_gather = kind in ("flower", "ore")

    if tracked:
        # Tracked gatherables use green, Chests use Gold, everything else uses HUD accent
        if is_gather:
            accent = theme.GOOD
        elif kind in ("chest",):
            accent = theme.CHEST
        else:
            accent = cv.s.hud_accent or theme.ACCENT

    if kind in ("flower", "ore") and label:
        m_name = label.lower() if icons.has_icon("minimap", label.lower()) else geo_gatherables.get_icon_name(label)

        # Use marker style for gatherables (glow/accent) instead of thick solid outline
        pm = icons.marker(m_name, size, accent=accent if (not done or tracked) else None)
        if pm:
            return pm

    name = _MARKER.get(kind)
    if name:
        m_name = name + ("2" if done and name in ("chest", "orb") else "")
        # Apply thin gold border to all uncollected chests as requested; shop
        # vendors (pet/mount) render the gold $ marker from the minimap atlas.
        eff_accent = theme.GOLD if (not done and kind in ("chest", "petshop", "mountshop", "vendor")) else accent
        pm = icons.marker(m_name, size, accent=eff_accent)
        if pm:
            return pm

    glyph = {"chest": "box", "obelisk": "radio", "checkpoint": "radio", "flower": "dice-5", "ore": "dice-5", "enemy": "swords", "orb": "broadcast", "activity": "box", "dungeon": "map", "rift": "map", "companion": "heart", "pos": "map-pin", "recipe": "box", "chest_orb": "broadcast"}.get(kind)
    return icons.ui_icon(glyph, theme.KIND_COLOR.get(kind, theme.TEXT), size) if glyph else None


def draw_compass(cv, p, cx, cy, rad, phi):
    base_fsize, bsize = max(7, int(rad / 12) - 3), max(7, int(rad / 12) - 3) * 3
    r, f = rad - (base_fsize / 2 + 1), p.font()
    f.setBold(True)
    for lbl, w_ang, d_ang, col in [("N", -math.pi / 2, math.pi / 2, theme.TEXT), ("E", 0.0, 0.0, theme.TEXT), ("S", math.pi / 2, -math.pi / 2, theme.TEXT), ("W", math.pi, math.pi, theme.TEXT)]:
        facing = cv._heading is not None and abs((cv._heading - w_ang + math.pi) % (math.pi * 2) - math.pi) < math.radians(50)
        f.setPointSize(base_fsize + 2 if facing else base_fsize)
        p.setFont(f)
        lx, ly = cx + math.cos(d_ang + phi) * r, cy - math.sin(d_ang + phi) * r
        p.setPen(QtGui.QColor(0, 0, 0, 160))
        p.drawText(QtCore.QRectF(lx - bsize / 2 + 1, ly - bsize / 2 + 1, bsize, bsize), QtCore.Qt.AlignCenter, lbl)
        p.setPen(QtGui.QColor(cv.s.hud_accent) if facing else QtGui.QColor(col))
        p.drawText(QtCore.QRectF(lx - bsize / 2, ly - bsize / 2, bsize, bsize), QtCore.Qt.AlignCenter, lbl)


def track_pos(cv):
    if cv.s.track_kind != "pos":
        return None
    try:
        coords = cv.s.track_id.split("|", 1)[0].split(",")
        return (float(coords[0]), float(coords[1]))
    except (ValueError, IndexError):
        return None


def is_waypoint(cv, kind, label, poi_id, wx, wy, track_pos) -> bool:
    if kind in ("respawn", "checkpoint"):
        return False
    tk, tid = cv.s.track_kind, cv.s.track_id
    if not tk or not tid:
        return False

    # 1. Unit/Hero tracking (ID-based)
    if tk in ("unit", "hero"):
        # Check if kind matches
        kind_match = (tk == "unit" and (kind in ("enemy", "companion", "spark_enemy"))) or \
                     (tk == "hero" and (kind == "hero" or kind.startswith("hero_")))

        # Match either raw unit_id or display name, or specific addr ID
        matched_name = (label == tid) or (names.unit_name(label) == names.unit_name(tid)) or \
                       (poi_id == f"e{tid}") or (poi_id == f"comp{tid}") or (poi_id == f"hero{tid}")
        if not kind_match or not matched_name:
            return False

        # If we have a tracker, highlight all matching unit_id mobs in the group
        return True

    # 2. Orb tracking (ID-based or position proximity)
    if tk == "orb":
        if kind == "orb":
            if poi_id == tid:
                return True
            o = geo_orbs.by_id().get(tid)
            if o and abs(wx - o.x) < 2.0 and abs(wy - o.y) < 2.0:
                return True

    # 3. Position-based tracking (everything else: Chests, Recipes, Gatherables, Static POIs)
    # These usually have coordinates in the track_id, e.g., "123.4,567.8,90.1|Chest Name"
    if tk in ("chest", "recipe", "chest_orb", "gather", "dungeon", "rift", "obelisk", "pos"):
        if tk == "gather" and "|" not in str(tid):
            if kind in ("flower", "ore"):
                if geo_gatherables.get_display_name(label) == tid:
                    return True

        if "|" in str(tid):
            try:
                coords_part = str(tid).split("|", 1)[0]
                coords = coords_part.split(",")
                if len(coords) >= 2:
                    tx, ty = float(coords[0]), float(coords[1])
                    # Tolerance for coordinate parsing/rounding
                    if abs(wx - tx) < 1.5 and abs(wy - ty) < 1.5:
                        if tk in ("chest", "recipe", "chest_orb") and kind in ("chest", "chest_orb", "recipe"):
                            return True
                        elif tk == "gather" and kind in ("flower", "ore"):
                            return True
                        elif tk == kind or tk == "pos":
                            return True
            except (ValueError, IndexError):
                pass

    # 4. Fallback for generic waypoints
    if tk == "pos" and track_pos:
        return abs(wx - track_pos[0]) < 1.0 and abs(wy - track_pos[1]) < 1.0

    return False


_CLICK_PRIORITY = {
    "orb": 0, "chest": 0, "recipe": 0,
    "dungeon": 1, "rift": 1, "obelisk": 1, "checkpoint": 1,
    "chest_orb": 2, "activity": 2,
    "flower": 3, "ore": 3,
    "enemy": 4, "spark_enemy": 4, "companion": 4,
    "hero": 5, "pos": 6,
}


def poi_at(cv, pos):
    w, h, scale, phi = cv.width(), cv.height(), cv._scale(), cv._phi()
    cx, cy, square = w / 2, h / 2, cv.s.minimap_shape == "Square"
    rad_x, rad_y = (w / 2 - 4, h / 2 - 4) if square else (min(w, h) / 2 - 4, min(w, h) / 2 - 4)
    best, best_rank = None, None
    for poi in cv._pois:
        kind = poi[3]
        if kind in ("respawn", "checkpoint", "chest_orb"):
            continue
        if kind.startswith("hero_"):
            kind = "hero"
        dx, dy = cv._rel(poi[0], poi[1], scale, phi)
        dx_c, dy_c = dx + cv._pan_x, dy - cv._pan_y
        if square:
            k = min((rad_x - 4) / abs(dx_c) if dx_c != 0 else 1e9, (rad_y - 4) / abs(dy_c) if dy_c != 0 else 1e9)
            if k < 1.0:
                dx_c *= k
                dy_c *= k
        else:
            dist = math.hypot(dx_c, dy_c)
            if dist > rad_x - 4 and dist > 0:
                dx_c *= (rad_x - 4) / dist
                dy_c *= (rad_x - 4) / dist
        d = math.hypot(cx + dx_c - pos.x(), cy - dy_c - pos.y())
        if d < 25.0:
            rank = (_CLICK_PRIORITY.get(kind, 9), d)
            if best_rank is None or rank < best_rank:
                best, best_rank = poi, rank
    return best


def mark_done(cv, e):
    cx, cy, scale, phi, best, best_kind, bestd = cv.width() / 2, cv.height() / 2, cv._scale(), cv._phi(), None, None, 12.0
    for (wx, wy, _wz, kind, _l, poi_id) in cv._pois:
        dx, dy = cv._rel(wx, wy, scale, phi)
        sx, sy = cx + dx + cv._pan_x, cy - (dy - cv._pan_y)
        d = math.hypot(sx - e.position().x(), sy - e.position().y())
        if d < bestd and poi_id:
            best, best_kind, bestd = poi_id, kind, d
    if best:
        # Chest orbs are marked done by their base activity id (e.g.
        # ..._ChestOrb_3) so the render check in section 5 matches. Keep
        # the full elem id for the poi lookup below.
        done_id = best
        if best_kind == "chest_orb":
            for sep in ("_StartOrb_", "_startorb_", "_Orb_", "_orb_", "_Start_", "_start_"):
                if sep in best:
                    done_id = best.split(sep)[0]
                    break
        # Bare "FightStone" is the generic live id shared by every fight
        # chest — never mark it done (that would hide them all).  It should
        # have been resolved to …_FightStone_N by live_chests(); if it
        # wasn't, refuse.  ⛔ ORB-CHEST ACTIVITY LOGIC — DO NOT EDIT.
        if done_id.lower() == "fightstone":
            return
        profile = cv.model.player_profile()
        cv.s.toggle_done(done_id, profile)
        tr = cv._tracker
        if tr and cv.s.is_done(done_id, profile):
            if tr.is_tracked("orb", done_id):
                tr.clear()
            elif cv.s.track_kind in ("pos", "chest", "gather") and cv.s.track_id:
                try:
                    tx, ty = (float(v) for v in cv.s.track_id.split("|", 1)[0].split(",")[:2])
                    p = next((p for p in cv._pois if p[5] == best), None)
                    if p and math.hypot(tx - p[0], ty - p[1]) < 1.0:
                        tr.clear()
                except Exception:
                    pass
        cv.update()
