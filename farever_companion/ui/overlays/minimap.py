"""Minimap overlay.

Top-down radar centred on the player: chests, gatherables, enemies, obelisks
and secret orbs plotted from the shared LiveModel + the static chest/orb
indexes. Zoomable; POIs beyond the view clamp to the edge ring as direction
markers. Right-click a POI to mark it done (persists). The compass-needle
target (set from the entity HUD, ui/tracker.py) shows as an accent ring. The view rotates by the camera orbit yaw (mouse
look, via `model.camera_yaw()`) so it tracks where you're looking even when
standing still; it falls back to the movement heading when the camera yaw can't
be read. The player arrow shows the body heading within that frame.

Painting and hit-test helpers live in `minimap_render` (kept out so this file
stays under the UI line budget); the canvas below owns state and input.
"""
from __future__ import annotations

import math
import re

from PySide6 import QtCore, QtGui, QtWidgets

from .. import theme
from ..overlay_base import OverlayWindow
from ... import constants as C
from ...core import chest_resolver
from ...data import icons, names, units as udata
from ...geo import nav, orbs as geo_orbs, zones as geo_zones, gatherables as geo_gatherables, pois as geo_pois
from . import minimap_render as render

POLL_MS = 300     # POI rescan (heavy)
FAST_MS = 33      # position/heading repaint (cheap) -> smooth pan + rotation

# States meaning an event orb (ChestOrb / TimerCollectRun) is NOT available:
# disabled/inactive, or already done (opened/looted/completed).
_DONE_ORB_STATES = ("disabled", "disable", "opened", "open", "looted", "completed")


class _Canvas(QtWidgets.QWidget):
    def __init__(self, model, settings):
        super().__init__()
        self.model = model
        self.s = settings
        self.setMinimumSize(220, 220)
        self._pois: list[tuple] = []      # (x, y, z, kind, label, id)
        self._px = self._py = self._pz = 0.0
        self._heading = self._cam_yaw = 0.0
        self._mtx = None
        self._tracker = None
        self._drag = self._right_drag_start = None
        self._pan_x = self._pan_y = 0.0
        self._pan_start_x = self._pan_start_y = 0.0
        self._right_click_panned = False
        self._last_player_pos = None
        self._tracking_pan = False  # True when pan was set by pan_to() (tracker)
        self._coord_str = ""
        self._zone_name = ""
        self.setMouseTracking(True)     # hover tooltips (e.g. soulstone costs)

    def _read_player(self) -> bool:
        xyz = self.model.player_xyz()
        if xyz is None:
            # No player data — logout, char change, or zone transition.
            # Clear any pan so the map starts fresh when the player reappears.
            if self._pan_x != 0.0 or self._pan_y != 0.0:
                self._pan_x = self._pan_y = 0.0
                self._tracking_pan = False
                self.update()
            self._last_player_pos = None
            return False

        if (self._pan_x != 0.0 or self._pan_y != 0.0) and self._last_player_pos is not None:
            dx, dy = xyz[0] - self._last_player_pos[0], xyz[1] - self._last_player_pos[1]
            if math.hypot(dx, dy) > 0.5:
                # Always clear pan when the player moves — whether the pan was
                # set by the tracker (pan_to) or by the user dragging.
                self._pan_x = self._pan_y = 0.0
                self._tracking_pan = False
                self.update()

        # Keep _last_player_pos current so the movement check is accurate every
        # frame (not just from when the right-click drag started).
        self._last_player_pos = xyz
        self._px, self._py, self._pz = xyz
        self._coord_str = f"x {self._px:.0f} y {self._py:.0f} z {self._pz:.0f}"
        self._heading = self.model.player_heading()
        self._cam_yaw = self.model.camera_yaw()
        self._mtx = self.model.view_matrix()
        return True

    def refresh_fast(self):
        if self._read_player():
            self.update()

    def _add_poi(self, pois, x, y, z, kind, label, poi_id, max_dist=0.0):
        if max_dist > 0 and math.hypot(x - self._px, y - self._py) > max_dist:
            return False
        pois.append((x, y, z, kind, label, poi_id))
        return True

    def refresh(self):
        if self.model is None:
            self._pois = []; self.update(); return

        profile = self.model.player_profile()
        done_list = self.s.get_poi_done(profile)

        if not self._read_player():
            self._pois = []; self.update(); return

        pois, s = [], self.s
        limit_z = getattr(s, "minimap_limit_by_zone", False)
        p_zone = geo_zones.resolve_zone(self._px, self._py, self._pz) if limit_z else None
        p_area = geo_zones.get_area_id(p_zone) if p_zone else None
        max_d = 400.0 if limit_z else (s.max_dist if s.max_dist > 0 else 600.0)
        hide_c = getattr(s, "minimap_hide_collected", False)

        merged_chests = self.model.nearest_chests_merged((self._px, self._py, self._pz), n=200, max_dist=max_d, player_zone=p_zone, use_2d=True)

        # ⛔ ORB-CHEST ACTIVITY LOGIC — DO NOT EDIT.  Everything below (the
        # end-chest done sets, section 1's activity handling, and section 5's
        # goldorb chain rendering) implements the ChestOrb / TimerCollectRun
        # activity state machine: Closed -> Locked+Enabled orbs -> Opened +
        # Disabled orbs.  It was rebuilt and verified live against the game
        # (see docs/CHEST_ORB_LOGIC.md).  Do not change it unless explicitly
        # asked because a GAME UPDATE broke it.
        #
        # Activity base ids whose end chest is done.  The LIVE state is
        # authoritative (end chest opened now = activity finished, and a
        # re-spawned activity shows again); the profile is only a fallback for
        # static anchors (older profiles store only the end-chest id, e.g.
        # …_ChestOrb_1_Chest_2, not the base id).
        end_open_live: set[str] = set()     # lower-cased bases, end chest opened live
        end_open_profile: set[str] = set()  # lower-cased bases, from the done list
        for _d in done_list:
            _b = chest_resolver.activity_base_id(_d)
            if _b:
                end_open_profile.add(_b)
        for _r in merged_chests:
            if _r.state and _r.state.lower() in ("opened", "open", "looted"):
                _b = chest_resolver.activity_base_id(_r.chest_id)
                if _b:
                    # A reward chest's `_Chest_<n>` number does NOT reliably name
                    # its activity (e.g. ..._ChestOrb_10_Chest_2 sits on the
                    # ..._ChestOrb_12 base).  Attribute it to the NEAREST static
                    # activity base instead of the id prefix, so opening it hides
                    # the right spot — and never an unrelated far-away base.
                    _nb, _nd = None, chest_resolver.ACTIVITY_BASE_MATCH_DIST
                    for _s in merged_chests:
                        if _s.live:
                            continue
                        _sl = (_s.chest_id or "").lower()
                        if "chestorb" not in _sl and "chest_orb" not in _sl and "timercollectrun" not in _sl:
                            continue
                        _d = math.hypot(_s.x - _r.x, _s.y - _r.y)
                        if _d <= _nd:
                            _nb, _nd = (_s.chest_id or "").lower(), _d
                    # No nearby base → the id prefix is not a reliable activity
                    # name, so don't attribute this chest to any activity.
                    if _nb:
                        end_open_live.add(_nb)

        # 1. Chests
        if s.minimap_chests:
            for r in merged_chests:
                cid_l = (r.chest_id or "").lower()
                if chest_resolver.is_event_orb_id(r.chest_id):
                    continue
                # ChestOrb / TimerCollectRun activities are transient: their
                # markers vanish the moment the activity is done (end chest
                # opened live).  A Closed/Locked end chest still shows while
                # the activity is live.  Static base anchors hide once their
                # end chest is done (live or recorded in the profile).
                if "chestorb" in cid_l or "chest_orb" in cid_l or "timercollectrun" in cid_l:
                    if r.live:
                        if r.state and r.state.lower() in ("opened", "open", "looted"):
                            continue
                    else:
                        # `_chest` is a substring of `chestorb` — only strip a
                        # REAL end-chest suffix, never the base id itself, or a
                        # garbage zone prefix hides every base in the zone.
                        _base_l = chest_resolver.activity_base_id(r.chest_id) or cid_l
                        if _base_l in end_open_live or _base_l in end_open_profile or r.chest_id in done_list:
                            continue
                if hide_c and r.chest_id and (r.chest_id in done_list or cid_l in end_open_live or cid_l in end_open_profile):
                    continue
                is_r = "recipe" in cid_l or (r.loot_table and "recipe" in r.loot_table.lower())
                self._add_poi(pois, r.x, r.y, r.z, "recipe" if is_r else "chest", names.chest_label(r.chest_id, r.loot_table), r.chest_id)

        # 2. Entities (Enemies, Group, Companions)
        show_e = s.minimap_enemies
        show_s = getattr(s, "minimap_spark_mobs", False)
        if show_e or show_s:
            pool = self.model.nearest_enemies((self._px, self._py, self._pz), n=150, max_dist=max_d, player_zone=p_zone, use_2d=True)
            hidden = set(s.get_entity_hidden_units(profile)) if show_e else set()
            if show_e and hide_c:
                hidden.update(done_list)

            for e, d in pool:
                is_spark = udata.drops_spark(e.unit_id)
                # Show if:
                # 1. Spark toggle is ON and it's a spark mob (bypass hidden)
                # 2. Enemies toggle is ON and it's not hidden
                if (show_s and is_spark) or (show_e and e.unit_id not in hidden):
                    # Use a special kind if it's a spark mob AND toggle is on for special rendering
                    kind = "spark_enemy" if (show_s and is_spark) else "enemy"
                    self._add_poi(pois, e.x, e.y, e.z, kind, e.unit_id or "?", f"e{e.addr}")
        if getattr(s, "minimap_players", True):
            for e, d in self.model.nearest_group_members((self._px, self._py, self._pz), n=20, max_dist=max_d, use_2d=True):
                if e.addr != self.model.player_addr:
                    h_cls = (e.cls or "").replace("ent.hero.", "").lower() if (e.cls or "").startswith("ent.hero.") else (e.unit_id or "warrior").lower()
                    if "warrior" in h_cls:
                        h_cls = "warrior"
                    elif "rogue" in h_cls:
                        h_cls = "rogue"
                    elif "mage" in h_cls:
                        h_cls = "mage"
                    elif "priest" in h_cls:
                        h_cls = "priest"
                    self._add_poi(pois, e.x, e.y, e.z, f"hero_{h_cls}", e.unit_id or "?", f"hero{e.addr}")
        if getattr(s, "minimap_companions", True):
            c_dist = float(s.show_companions_debug) if isinstance(getattr(s, "show_companions_debug", False), (int, float)) else (250.0 if not getattr(s, "show_companions_debug", False) else max_d)
            hide_comps = set(s.get_companion_hidden_units(profile))
            for e, d in self.model.nearest_companions((self._px, self._py, self._pz), n=100, max_dist=c_dist, player_zone=p_zone, use_2d=True, hide_units=hide_comps):
                self._add_poi(pois, e.x, e.y, e.z, "companion", e.unit_id or "?", f"comp{e.addr}")

        # 3. Gatherables
        if s.minimap_gatherables:
            for g in self.model.gatherables(player_zone=p_zone, max_dist=max_d, use_2d=True):
                if (geo_gatherables.get_type_key(g.elem_id or "") or "") in s.show_gatherable_types:
                    self._add_poi(pois, g.x, g.y, g.z, "ore" if any(x in (g.elem_id or "").lower() for x in ("ore", "tungstene", "tin", "copper")) else "flower", g.elem_id or "?", f"gl{g.addr}")
            for g in geo_gatherables.load_nodes():
                # Avoid heavy zone resolution for every node; only resolve if limit_z is active
                if p_area:
                    g_area = geo_zones.get_area_id(geo_zones.resolve_zone(g.x, g.y, g.z))
                    if g_area != p_area:
                        continue
                if any(math.hypot(g.x - p[0], g.y - p[1]) < 2.0 for p in pois if p[3] in ("ore", "flower")):
                    continue
                if (geo_gatherables.get_type_key(g.name) or "") in s.show_gatherable_types:
                    self._add_poi(pois, g.x, g.y, g.z, "ore" if any(x in g.name.lower() for x in ("ore", "tungstene", "tin", "copper")) else "flower", g.name, f"g{g.x}{g.y}", max_dist=max_d)

        # 4. Obelisks / Respawns / Checkpoints
        if s.minimap_obelisks:
            live_obs = list(self.model.obelisks(player_zone=p_zone, max_dist=max_d, use_2d=True))
            for o in live_obs:
                eid_l, state_l = (o.elem_id or "").lower(), (o.state or "").lower()
                if any(x in eid_l for x in ("checkpoint", "start_", "finish_")):
                    # Start points may be "..._Start_1" or bare "..._Start".
                    is_start = "start_" in eid_l or eid_l.endswith("_start")
                    # Ascension Start points are teleporters: always keep them
                    # on the map, even after the course completes or they're
                    # marked done. Only checkpoints/finish hide once collected.
                    if hide_c and o.elem_id and o.elem_id in done_list and not is_start:
                        continue
                    if is_start or state_l in ("waitactivation", "desactivated"):
                        label = names.poi_label(o.elem_id) or o.elem_id or o.kind
                        self._add_poi(pois, o.x, o.y, o.z, "checkpoint", label, o.elem_id or f"ob{o.addr}")
            for p in geo_pois.load_pois():
                if p.sub_kind not in ("obelisk", "respawn"):
                    continue
                if p_area and geo_zones.get_area_id(p.zone) != p_area:
                    continue
                # Static locations have no distance limit (allows panning to them)
                self._add_poi(pois, p.x, p.y, p.z, p.sub_kind, p.name or p.sub_kind, p.id, max_dist=0)

        # 4b. Vendors (pet + mount shop NPCs)
        if s.minimap_vendors:
            for p in geo_pois.load_pois():
                if p.sub_kind not in ("petshop", "mountshop", "vendor"):
                    continue
                if p_area and geo_zones.get_area_id(p.zone) != p_area:
                    continue
                # Static locations have no distance limit (allows panning to them)
                self._add_poi(pois, p.x, p.y, p.z, p.sub_kind, p.name or p.sub_kind, p.id, max_dist=0)

        render.add_soulstone_pois(self, pois, p_area)   # 4c. soulstone summon points

        # 5. Orbs
        if s.minimap_orbs:
            for ob in geo_orbs.load_orbs():
                if not hide_c or not ob.orb_id in done_list:
                    if p_area and geo_zones.get_area_id(ob.zone) != p_area:
                        continue
                    # Static locations have no distance limit (allows panning to them)
                    self._add_poi(pois, ob.x, ob.y, ob.z, "orb", ob.orb_id, ob.orb_id, max_dist=0)
            for e in self.model.live_orbs(player_zone=p_zone, max_dist=max_d, use_2d=True):
                if not hide_c or not e.elem_id in done_list:
                    self._add_poi(pois, e.x, e.y, e.z, "orb", e.elem_id or "orb", e.elem_id or f"orb{e.addr}")

            # Chest Orbs + TimerCollectRun event orbs (live elements only) —
            # goldorb markers.  While a chain is active, every available orb
            # renders as a goldorb.  Once the chain is done, no orbs linger:
            # the reward chest covers the spot instead.  Spent orbs
            # (disabled/opened/looted) never render.
            #
            # A chain's orbs render only while the activity is live: spent orbs
            # (disabled/opened/looted) never render, and once the end chest is
            # opened live the whole chain disappears (see end_open_live above).
            chains: dict[str, list] = {}
            for e in self.model.live_chest_orbs(player_zone=p_zone, max_dist=max_d, use_2d=True):
                if not e.elem_id:
                    continue
                pid = e.elem_id
                for sep in ("_StartOrb_", "_startorb_", "_Orb_", "_orb_", "_Start_", "_start_"):
                    if sep in e.elem_id:
                        pid = e.elem_id.split(sep)[0]
                        break
                chains.setdefault(pid, []).append(e)
            for pid, orbs in chains.items():
                live_done = pid.lower() in end_open_live
                for e in orbs:
                    if e.state and e.state.lower() in _DONE_ORB_STATES:
                        continue
                    if live_done:
                        continue
                    if not hide_c or (pid not in done_list and e.elem_id not in done_list):
                        self._add_poi(pois, e.x, e.y, e.z, "chest_orb", e.elem_id, e.elem_id, max_dist=max_d)

        # 6. Dungeons / Rifts (Static only)
        _zid = geo_zones.resolve_zone(self._px, self._py, self._pz)
        _zname = names.zone_name(_zid) if _zid else ""
        if _zname and re.fullmatch(r"Z\d+.*", _zname, re.IGNORECASE): _zname = ""
        self._zone_name = _zname

        if s.minimap_dungeons:
            rst = self.model.rift_status() if hasattr(self.model, "rift_status") else None
            for p in geo_pois.load_pois():
                if p.sub_kind in ("dungeon", "rift"):
                    if p_area and geo_zones.get_area_id(p.zone) != p_area:
                        continue
                    # Static locations have no distance limit (allows panning to them)
                    # Prioritize target_activity (technical ID) for dungeons so Entity HUD can look up boss info
                    label = p.target_activity or p.name
                    if p.sub_kind == "rift":
                        # If a rift is active/warning/closing, hide non-active rift POI icons from the minimap
                        if rst and rst.state in ("WARNING", "ACTIVE", "CLOSING") and rst.in_range and rst.rift_name:
                            act_n = rst.rift_name.lower()
                            poi_n = (p.name or p.target_activity or "").lower()
                            if act_n not in poi_n and poi_n not in act_n:
                                continue
                        if not label and p.zone:
                            label = names.zone_name(p.zone)
                        if label and not label.lower().startswith("rift"):
                            label = f"Rift {label}"

                    if not label:
                        label = p.sub_kind.capitalize()
                    self._add_poi(pois, p.x, p.y, p.z, p.sub_kind, label, p.id, max_dist=0)

        # 7. Tracked Target
        try:
            tk, tid = s.track_kind, s.track_id
            if tk == "orb" and tid and not any(p[3] == "orb" and p[5] == tid for p in pois):
                o = geo_orbs.by_id().get(tid)
                if o:
                    self._add_poi(pois, o.x, o.y, o.z, "orb", o.orb_id, o.orb_id)
            elif tk == "unit" and tid and not any(p[4] == tid for p in pois):
                for e in self.model.units():
                    if e.unit_id == tid:
                        self._add_poi(pois, e.x, e.y, e.z, "companion" if udata.is_companion(e.unit_id) else "enemy", e.unit_id or "?", f"{'comp' if udata.is_companion(e.unit_id) else 'e'}{e.addr}")
            elif tk == "hero" and tid and not any(p[4] == tid for p in pois):
                for e in self.model.units():
                    if e.unit_id == tid:
                        h_cls = (e.cls or "").replace("ent.hero.", "").lower() if (e.cls or "").startswith("ent.hero.") else (e.unit_id or "warrior").lower()
                        if "warrior" in h_cls:
                            h_cls = "warrior"
                        elif "rogue" in h_cls:
                            h_cls = "rogue"
                        elif "mage" in h_cls:
                            h_cls = "mage"
                        elif "priest" in h_cls:
                            h_cls = "priest"
                        self._add_poi(pois, e.x, e.y, e.z, f"hero_{h_cls}", e.unit_id or "?", f"hero{e.addr}")
            elif tk == "pos" and tid:
                coords, _, lbl = tid.partition("|")
                tx, ty, tz = (float(v) for v in coords.split(","))
                if not any(abs(p[0] - tx) < 0.5 and abs(p[1] - ty) < 0.5 for p in pois):
                    pois.append((tx, ty, tz, "pos", lbl or "Waypoint", "tracked_pos"))
            elif tk == "chest" and tid and any(s in tid.lower() for s in ("timercollectrun", "chestorb", "chest_orb")):
                # ChestOrb / TimerCollectRun event orbs are not chests: only
                # shown live via the chest_orb section while active, never as
                # a tracked chest box.  (FightStone is a real chest and IS
                # trackable as a normal chest.)
                pass
            elif tk in ("gather", "chest", "recipe") and tid:
                try:
                    parts = tid.split("|")
                    coords_str = parts[0]
                    lbl = parts[1] if len(parts) > 1 else ""
                    real_id = parts[2] if len(parts) > 2 else "tracked_static"

                    tx, ty, tz = (float(v) for v in coords_str.split(","))
                    if not any(abs(p[0] - tx) < 0.5 and abs(p[1] - ty) < 0.5 for p in pois):
                        kind = "recipe" if tk == "recipe" else "chest" if tk == "chest" else "flower"
                        if tk == "gather" and ("ore" in lbl.lower() or "tungstene" in lbl.lower() or "tin" in lbl.lower() or "copper" in lbl.lower()):
                            kind = "ore"
                        pois.append((tx, ty, tz, kind, lbl or tk.capitalize(), real_id))
                except Exception:
                    pass
        except Exception:
            pass

        pois.sort(key=lambda p: math.hypot(p[0] - self._px, p[1] - self._py), reverse=True)
        self._pois = pois
        self.update()

    def _scale(self):
        return (min(self.width(), self.height()) / 2 - 6) / max(20.0, self.s.minimap_zoom * 20.0)

    def _phi(self):
        if not self.s.minimap_rotate:
            return 0.0
        phi = nav.ground_view_phi(self._mtx, self._px, self._py, self._pz) if self._mtx else None
        return phi if phi is not None else nav.view_phi(self._cam_yaw, self._heading)

    def _rel(self, wx, wy, scale, phi):
        dx, dy = (wx - self._px) * scale, (self._py - wy) * scale
        if phi:
            c, s = math.cos(phi), math.sin(phi)
            dx, dy = dx * c - dy * s, dx * s + dy * c
        return dx, dy

    def pan_to(self, wx: float, wy: float):
        """Center the map on a specific world coordinate (overriding pan).

        Called by the tracker when the user selects a target.  Sets
        _tracking_pan so that _read_player() knows to clear this pan (and
        re-centre on the character) as soon as the player moves.
        """
        self._pan_x = self._pan_y = 0.0
        scale, phi = self._scale(), self._phi()
        dx, dy = self._rel(wx, wy, scale, phi)
        self._pan_x, self._pan_y = -dx, dy
        self._tracking_pan = True
        self.update()

    def paintEvent(self, _e):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        w, h = self.width(), self.height()
        cx, cy = w / 2, h / 2
        square = self.s.minimap_shape == "Square"
        rad_x, rad_y = (w / 2 - 4, h / 2 - 4) if square else (min(w, h) / 2 - 4, min(w, h) / 2 - 4)

        p.setBrush(QtGui.QColor(theme.PANEL))
        p.setPen(QtGui.QColor(theme.BORDER))
        clip = QtGui.QPainterPath()
        if square:
            clip.addRect(QtCore.QRectF(4, 4, w - 8, h - 8))
        else:
            clip.addEllipse(QtCore.QPointF(cx, cy), rad_x, rad_y)
        p.setClipPath(clip)
        p.drawPath(clip)

        scale, phi = self._scale(), self._phi()
        if self.s.minimap_texture:
            render.draw_map(self, p, cx + self._pan_x, cy + self._pan_y, scale, phi)

        p.setPen(QtGui.QPen(QtGui.QColor(theme.BORDER), 1))
        p.setBrush(QtCore.Qt.NoBrush)
        if square:
            p.drawRect(QtCore.QRectF(cx - rad_x, cy - rad_y, rad_x * 2, rad_y * 2))
        else:
            p.drawEllipse(QtCore.QPointF(cx, cy), rad_x, rad_y)

        track_pos, profile = render.track_pos(self), self.model.player_profile()
        for (wx, wy, _wz, kind, label, poi_id) in self._pois:
            orig_kind, hero_class = kind, None
            if kind.startswith("hero_"): hero_class = kind.split("_", 1)[1]; kind = "hero"
            dx, dy = self._rel(wx, wy, scale, phi)
            dx_c, dy_c = dx + self._pan_x, dy - self._pan_y
            edge = False
            if square:
                kx, ky = (rad_x - 4) / abs(dx_c) if dx_c != 0 else 1e9, (rad_y - 4) / abs(dy_c) if dy_c != 0 else 1e9
                k = min(kx, ky)
                if k < 1.0:
                    dx_c *= k
                    dy_c *= k
                    edge = True
            else:
                dist = math.hypot(dx_c, dy_c)
                if dist > rad_x - 4:
                    if dist == 0:
                        continue
                    dx_c *= (rad_x - 4) / dist
                    dy_c *= (rad_x - 4) / dist
                    edge = True

            done = bool(poi_id and self.s.is_done(poi_id, profile))
            is_wp = render.is_waypoint(self, kind, label, poi_id, wx, wy, track_pos)
            # Type-level gather tracking (e.g. "Madrigold") highlights nodes
            # with the green halo but never clamps them to the edge.
            tracked = is_wp or (not edge and kind in ("flower", "ore")
                                and render.gather_type_tracked(self, kind, label))
            if edge and not is_wp:
                continue

            sx, sy = cx + dx_c, cy - dy_c
            sz = self.s.minimap_icon_size
            if kind in ("dungeon", "rift"):
                sz = self.s.minimap_icon_size + 3
            elif kind == "obelisk":
                sz += 4
            elif kind in ("flower", "ore"):
                sz = max(1, sz - 2)
                if any(x in label.lower() for x in ("_large", "_big", "tungstene")):
                    sz = int(sz * 1.15)
                elif "_small" in label.lower():
                    sz = int(sz * 0.9)
            if is_wp:
                sz += 1

            pm = render.poi_pixmap(self, orig_kind, label, sz, done, tracked=tracked) if (self.s.minimap_icons or (render.USE_HERO_SVGS and orig_kind.startswith("hero_"))) else None

            # Base radius for ground circle/dot
            rad = (5 if edge else 6) if kind == "hero" else (3 if edge else 4)
            if is_wp:
                rad += 1

            if pm and not pm.isNull():
                p.drawPixmap(int(sx - pm.width() / 2), int(sy - pm.height() / 2), pm)
                if is_wp and kind in ("chest", "chest_orb"):
                    p.setPen(QtGui.QPen(QtGui.QColor(theme.CHEST), 2))
                    p.setBrush(QtCore.Qt.NoBrush)
                    halo_rad = (pm.width() / 2) + 2
                    p.drawEllipse(QtCore.QPointF(sx, sy), halo_rad, halo_rad)
                elif tracked and orig_kind in ("flower", "ore"):
                    # Tracked gatherable: green halo, icon keeps its own colors.
                    p.setPen(QtGui.QPen(QtGui.QColor(theme.GOOD), 2))
                    p.setBrush(QtCore.Qt.NoBrush)
                    halo_rad = (pm.width() / 2) + 2
                    p.drawEllipse(QtCore.QPointF(sx, sy), halo_rad, halo_rad)
            else:
                col = QtGui.QColor(theme.HERO.get(hero_class, theme.TEXT)) if kind == "hero" and hero_class else QtGui.QColor(theme.KIND_COLOR.get(kind, theme.TEXT))
                p.setPen(QtGui.QPen(QtGui.QColor(theme.BG), 1) if kind == "hero" else QtCore.Qt.NoPen)
                rad = (5 if edge else 6) if kind == "hero" else (3 if edge else 4)
                if is_wp:
                    rad += 1
                p.setBrush(col)
                p.drawEllipse(QtCore.QPointF(sx, sy), rad, rad)
                if is_wp:
                    # Draw accent circle for all tracked types except standard enemies (mobs)
                    draw_circle = kind != "enemy"
                    if draw_circle:
                        p.setPen(QtGui.QPen(QtGui.QColor(self.s.hud_accent or theme.ACCENT), 2))
                        p.setBrush(QtCore.Qt.NoBrush)
                        p.drawEllipse(QtCore.QPointF(sx, sy), rad + 2, rad + 2)

        p.setClipping(False)
        render.draw_compass(self, p, cx, cy, min(w, h) / 2 - 4, phi)
        accent = QtGui.QColor(self.s.hud_accent)
        fwd = (phi - self._heading) if self._heading is not None else (math.pi / 2)
        arrow_pm = icons.asset_icon("arrow", int(self.s.minimap_icon_size * 1.3))
        if arrow_pm and not arrow_pm.isNull():
            p.save()
            p.translate(cx + self._pan_x, cy + self._pan_y)
            p.rotate(90.0 - math.degrees(fwd))
            p.drawPixmap(int(-arrow_pm.width() / 2), int(-arrow_pm.height() / 2), arrow_pm)
            p.restore()
        else:
            p.setBrush(accent)
            p.setPen(QtCore.Qt.NoPen)
            p.drawEllipse(QtCore.QPointF(cx + self._pan_x, cy + self._pan_y), 4, 4)
            p.setPen(QtGui.QPen(accent, 2))
            p.drawLine(QtCore.QPointF(cx + self._pan_x, cy + self._pan_y), QtCore.QPointF(cx + self._pan_x + math.cos(fwd) * 13, cy + self._pan_y - math.sin(fwd) * 13))

        # Draw Coords & Zone Overlay
        bare = getattr(self.window(), "_bare", False)
        if not (bare and not square):
            p.save()
            p.setClipping(False)
            f = QtGui.QFont(theme.MONO_FONT, 8)
            f.setWeight(QtGui.QFont.Medium)
            p.setFont(f)
            fm = p.fontMetrics()
            th = fm.height()

            def draw_t(text, tx, ty, col):
                p.setPen(QtGui.QColor(0, 0, 0, 160))
                for ox, oy in [(-1, -1), (1, -1), (-1, 1), (1, 1)]:
                    p.drawText(tx + ox, ty + oy, text)
                p.setPen(col)
                p.drawText(tx, ty, text)

            # Calculate total lines and box dimensions
            if self._coord_str or self._zone_name:
                rect_h = th + 6
                # Top background box
                p.setPen(QtCore.Qt.NoPen)
                p.setBrush(QtGui.QColor(0, 0, 0, 110))
                p.drawRect(0, 0, w, rect_h)

                ty = th + 1
                if self._coord_str:
                    draw_t(self._coord_str, 8, ty, QtGui.QColor(theme.TEXT))

                if self._zone_name:
                    zw = fm.horizontalAdvance(self._zone_name)
                    draw_t(self._zone_name, w - zw - 8, ty, QtGui.QColor(theme.MUTED))
            p.restore()

        p.end()

    def mousePressEvent(self, e):
        if e.button() == QtCore.Qt.RightButton:
            self._right_drag_start = e.position()
            self._pan_start_x, self._pan_start_y = self._pan_x, self._pan_y
            self._right_click_panned = False
            self._tracking_pan = False  # User is manually dragging; clear tracker flag
            return
        if e.button() == QtCore.Qt.LeftButton and not getattr(self.window(), "_locked", False):
            tr, poi = self._tracker, render.poi_at(self, e.position())
            if poi and tr:
                wx, wy, wz, kind, label, poi_id = poi

                # ChestOrb / TimerCollectRun spawned orbs are informational
                # markers only — not clickable, never tracked as chests.
                # Right-click done-marking still works via _mark_done.
                if kind == "orb" and poi_id in geo_orbs.by_id():
                    tr.toggle("orb", poi_id)
                elif (kind.startswith("hero_") or kind in ("enemy", "companion", "spark_enemy")) and label != "?":
                    addr = None
                    if kind.startswith("hero_"):
                        try:
                            addr = int(str(poi_id)[4:])
                        except Exception:
                            pass
                        tr.toggle("hero", label, addr=addr)
                    elif kind == "enemy" or kind == "spark_enemy":
                        addr = None
                        if str(poi_id).startswith("e"):
                            try:
                                addr = int(str(poi_id)[1:])
                            except Exception:
                                pass
                        tr.toggle("unit", label, addr=addr)
                    elif kind == "companion":
                        addr = None
                        if str(poi_id).startswith("comp"):
                            try:
                                addr = int(str(poi_id)[4:])
                            except Exception:
                                pass
                        tr.toggle("unit", label, addr=addr)
                elif kind in ("ore", "flower"):
                    tr.toggle("gather", f"{wx:.1f},{wy:.1f},{wz:.1f}|{label or kind}|{poi_id}")
                elif kind == "recipe":
                    tr.toggle("recipe", f"{wx:.1f},{wy:.1f},{wz:.1f}|{label or kind}|{poi_id}")
                else:
                    tr.toggle(kind, f"{wx:.1f},{wy:.1f},{wz:.1f}|{label or kind}|{poi_id}")

                if tr.parent():
                    mgr = tr.parent()
                    ov = getattr(mgr, "overlays", {}).get("entity")
                    if ov and ov.isVisible():
                        norm_kind = kind
                        if kind.startswith("hero_"):
                            norm_kind = "hero"
                        elif kind == "companion":
                            norm_kind = "comp"
                        elif kind in ("dungeon", "rift", "obelisk", "respawn", "checkpoint", "pos", "soulstone"):
                            norm_kind = "static"
                        elif kind in ("flower", "ore"):
                            norm_kind = "gather"
                        elif kind == "recipe":
                            norm_kind = "recipe"
                        elif kind in ("chest", "chest_orb"):
                            norm_kind = "chest"

                        ck = poi_id
                        if norm_kind == "static":
                            ck = f"s{wx:.1f},{wy:.1f}"
                        elif norm_kind == "enemy" or norm_kind == "spark_enemy":
                            ck = label
                        elif norm_kind == "chest":
                            ck = str(poi_id)
                            for sep in ("_StartOrb_", "_startorb_", "_Orb_", "_orb_", "_Start_", "_start_"):
                                if sep in str(poi_id):
                                    ck = str(poi_id).split(sep)[0]
                                    break
                        elif norm_kind == "comp" and str(poi_id).startswith("comp"):
                            try:
                                ck = int(poi_id[4:])
                            except Exception:
                                pass
                        elif norm_kind == "hero":
                            try:
                                ck = int(str(poi_id)[4:])
                            except Exception:
                                pass

                        # Temporarily detach the Entity HUD's internal tracker listener to prevent recursion
                        old_tr = getattr(ov, "_tracker", None)
                        ov._tracker = None
                        try:
                            ov.select_by_key(norm_kind, ck)
                        finally:
                            ov._tracker = old_tr
                return
            self._drag = e.globalPosition().toPoint() - self.window().frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        render.poi_tooltip(self, e)
        if self._drag and e.buttons() & QtCore.Qt.LeftButton:
            self.window().move(e.globalPosition().toPoint() - self._drag)
        elif self._right_drag_start and e.buttons() & QtCore.Qt.RightButton:
            delta = e.position() - self._right_drag_start
            if delta.manhattanLength() > 3:
                self._right_click_panned, raw_x, raw_y = True, self._pan_start_x + delta.x(), self._pan_start_y + delta.y()
                if C.MAP_BOUNDS:
                    scale, phi = self._scale(), self._phi()
                    c, s = math.cos(phi), math.sin(phi)
                    rx, ry = -raw_x, raw_y
                    dx, dy = rx * c + ry * s, -rx * s + ry * c
                    tx, ty = max(C.MAP_BOUNDS[0], min(C.MAP_BOUNDS[2], self._px + dx / scale)), max(C.MAP_BOUNDS[1], min(C.MAP_BOUNDS[3], self._py - dy / scale))
                    dx, dy = (tx - self._px) * scale, (self._py - ty) * scale
                    self._pan_x, self._pan_y = -(dx * c - dy * s), dx * s + dy * c
                else:
                    self._pan_x, self._pan_y = raw_x, raw_y
                self.update()

    def mouseReleaseEvent(self, e):
        if self._drag:
            self._drag = None
            if hasattr(self.window(), "persist_geometry"):
                self.window().persist_geometry()
        if e.button() == QtCore.Qt.RightButton:
            if not self._right_click_panned:
                render.mark_done(self, e)
            self._right_drag_start = None

    def mouseDoubleClickEvent(self, e):
        if e.button() == QtCore.Qt.LeftButton and hasattr(self.window(), "set_bare"):
            self.window().set_bare(not self.s.minimap_bare)


class MinimapOverlay(OverlayWindow):
    def __init__(self, model, settings, parent=None):
        super().__init__("Minimap", settings, geo_key="minimap", parent=parent)
        self._page_key = "settings:HUD's"
        self.model = model
        self.s, self._tracker, self._sync_fn, self._zoom_sync_fn = settings, None, None, None

        zin, zout, hbtn = QtWidgets.QPushButton("+"), QtWidgets.QPushButton("−"), QtWidgets.QPushButton()
        for b, o, s, c in [(zin, "Icon", "color: #22c55e; font-size: 20px; font-weight: bold; margin-bottom: 2px;", lambda: self._zoom(0.8)), (zout, "Icon", "color: #38bdf8; font-size: 20px; font-weight: bold; margin-bottom: 2px;", lambda: self._zoom(1.25)), (hbtn, "Icon", "", self._toggle_hide_collected)]:
            b.setObjectName(o)
            b.setStyleSheet(s)
            b.clicked.connect(c)
        self._hide_btn = hbtn
        self._hide_btn.setFixedSize(22, 22)
        self._update_hide_btn()
        for w in (zout, zin, self._hide_btn): self.titlebar.extra.insertWidget(self.titlebar.extra.count() - 2, w)

        self.canvas = _Canvas(model, settings)
        self.content.addWidget(self.canvas, 1)
        self._hint = QtWidgets.QLabel("Double click to hide/show border")
        self._hint.setObjectName("Muted")
        self._hint.setAlignment(QtCore.Qt.AlignCenter)
        self.content.addWidget(self._hint)

        self.resize(settings.minimap_size, settings.minimap_size + 40)
        self._fast, self._slow = QtCore.QTimer(self), QtCore.QTimer(self)
        self._fast.timeout.connect(self.canvas.refresh_fast)
        self._fast.start(FAST_MS)
        self._slow.timeout.connect(self.canvas.refresh)
        self._slow.start(POLL_MS)
        self.canvas.refresh()

    def set_bare(self, on: bool) -> None:
        super().set_bare(on)
        self._hint.setVisible(not on)
        self.canvas.update()

    def _zoom(self, f):
        self.s.minimap_zoom = max(2.0, min(60.0, self.s.minimap_zoom * f))
        self.s.save()
        if self._zoom_sync_fn:
            self._zoom_sync_fn(int(self.s.minimap_zoom))
        self.canvas.update()

    def _toggle_hide_collected(self):
        self.set_hide_collected(not getattr(self.s, "minimap_hide_collected", False))

    def set_hide_collected(self, on: bool) -> None:
        self.s.minimap_hide_collected = on
        self.s.save()
        self._update_hide_btn()
        self.canvas.refresh()
        if self._sync_fn:
            self._sync_fn(on)

    def _update_hide_btn(self) -> None:
        on = getattr(self.s, "minimap_hide_collected", False)
        self._hide_btn.setIcon(QtGui.QIcon(icons.ui_icon("eye-off" if on else "eye", self.s.hud_accent if on else theme.MUTED, 18)))
        self._hide_btn.setIconSize(QtCore.QSize(18, 18))
        self._hide_btn.setToolTip("Show collected orbs & chests" if on else "Hide collected orbs & chests")

    def set_tracker(self, tracker) -> None:
        self._tracker = tracker
        self.canvas._tracker = tracker
        tracker.changed.connect(self.canvas.update)

    def pan_to(self, wx: float, wy: float):
        self.canvas.pan_to(wx, wy)

    def closeEvent(self, e):
        self._fast.stop()
        self._slow.stop()
        super().closeEvent(e)
