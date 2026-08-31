"""Entity overlay (Tactical Overlay HUD).

The entity HUD lists nearby enemies (selectable with ↑/↓ or click), wild
companions, gatherables, secret orbs and chests - short, no endless scrolling.
If the selected target leaves range we flip to the first available one.
Clicking an enemy, wild companion or secret orb also points the centre-screen
compass needle at it (ui/tracker.py); click the tracked row again to stop.
Reads the shared LiveModel. Read-only.

Data gathering / done-state / selection lives in `entity_gather` (mixin), the
section rendering in `entity_render` (mixin), and the row widgets in
`entity_rows` — this file keeps the overlay shell and interaction so it stays
under the UI line budget.
"""
from __future__ import annotations

from PySide6 import QtCore, QtWidgets

from .. import theme
from ..overlay_base import OverlayWindow
from ...data import names
from ...geo import orbs as geo_orbs
from .entity_gather import EntityGatherMixin
from .entity_render import EntityRenderMixin
from .entity_rows import _Section, _scroll_body

POLL_MS = 350


class EntityOverlay(EntityRenderMixin, EntityGatherMixin, OverlayWindow):
    def __init__(self, model, settings, parent=None):
        super().__init__("HUD", settings, geo_key="entity", parent=parent)
        self._page_key = "settings:HUD's"
        self.titlebar.codex_btn.setVisible(True)
        self.titlebar.dungeon_btn.setVisible(True)
        self.titlebar.soulstone_btn.setVisible(True)
        self.model = model
        self.s = settings
        self._enemies: list = []          # [(entity, dist)]
        self._spark_mobs: list = []       # [(entity, dist)]
        self._group_members: list = []    # [(entity, dist)]
        self._comps: list = []            # [(entity, dist)] wild companions
        self._gatherables: list = []      # [((x,y,z,kind,label,id), dist)]
        self._orbs: list = []             # [(Orb, dist)] uncollected secret orbs
        self._chests: list = []           # [ChestRow]
        self._static_pois: list = []      # [((x,y,z,kind,label,id), dist)] tracked dungeons/obelisks
        self._owned: set | None = None    # account collection (None = signed out)
        self._sel = None                  # ("enemy", addr) | ("chest", chest_id)
        self._tracker = None              # shared compass-needle TrackController
        self._last_was_in_dungeon = False  # track zone transitions to clear tracker
        self.setFocusPolicy(QtCore.Qt.StrongFocus)

        scroll, self._body = _scroll_body()
        self._scroll = scroll
        self.content.addWidget(scroll, 1)
        self.rift_box = _Section("", theme.GOLD)
        self.rift_box.header.hide()
        self.enemy_box = _Section("ENEMIES", theme.DANGER)
        self.spark_box = _Section("SPARK MOBS", "#a855f7")
        self.comp_box = _Section("COMPANIONS", theme.GOOD)
        self.group_box = _Section("PLAYERS", theme.MUTED)
        self.gather_box = _Section("GATHERABLES", "#c2c6d0")
        self.orb_box = _Section("SECRET ORBS", theme.KIND_COLOR["orb"])
        self.chest_box = _Section("CHESTS", theme.CHEST)
        self.loot_box = _Section("LOOT", "#4ADE80")
        self.food_box = _Section("PLAYER FOOD", "#FB923C")
        # label-less sections: rows speak for themselves
        for b in (self.enemy_box, self.loot_box, self.food_box):
            b.header.hide()
        self.static_box = _Section("WAYPOINT", theme.ACCENT)
        for b in (self.rift_box, self.food_box, self.static_box, self.enemy_box, self.spark_box, self.comp_box, self.group_box, self.gather_box, self.orb_box, self.chest_box, self.loot_box):
            self._body.addWidget(b)

        # Add a fixed spacer at the bottom for clean padding / line break.
        # Updated dynamically in _check_auto_height.
        self._bottom_spacer = QtWidgets.QSpacerItem(0, 6, QtWidgets.QSizePolicy.Minimum, QtWidgets.QSizePolicy.Fixed)
        self._body.addSpacerItem(self._bottom_spacer)
        self._body.addStretch(1)

        self.enable_resize_grip()
        self._base_w, self._base_h = self.s.entity_width, 440
        self.setMinimumWidth(300)
        self.setMaximumWidth(800)
        self.apply_scale(self.s.entity_font_size / 14.0)     # scaled QSS + resize to base*scale
        self._last_h_config = None
        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(POLL_MS)

    def reset_state(self) -> None:
        """Completely reset all HUD selection, entity caches, and sub-windows on relog/logout."""
        self._sel = None
        self._enemies = []
        self._group_members = []
        self._player_deaths = {}
        self._player_prev_alive = {}
        self._comps = []
        self._gatherables = []
        self._orbs = []
        self._chests = []
        self._loots = []
        self._foods = []
        self._static_pois = []

    # --- selection -------------------------------------------------------
    def keyPressEvent(self, e):
        if e.key() == QtCore.Qt.Key_Up:
            self._move_selection(-1)
        elif e.key() == QtCore.Qt.Key_Down:
            self._move_selection(1)
        else:
            super().keyPressEvent(e)

    def _get_rift_mode(self) -> str:
        val = getattr(self.s, "show_rift_timer", "Always")
        if isinstance(val, bool):
            return "Always" if val else "Off"
        s = str(val).lower()
        if s in ("active",):
            return "Active"
        if s in ("off", "false"):
            return "Off"
        return "Always"

    def _targets(self):
        """Ordered selectable keys across every visible section: enemies,
        companions, orbs, chests (matching the HUD layout)."""
        t = []

        # Spark Mobs
        seen_spark_names = set()
        for e, _ in self._spark_mobs:
            name = names.unit_name(e.unit_id) or "?"
            if name not in seen_spark_names:
                seen_spark_names.add(name)
                t.append(("spark", e.unit_id))

        # Regular Enemies
        seen_enemy_names = set()
        for e, _ in self._enemies:
            name = names.unit_name(e.unit_id) or "?"
            if name not in seen_enemy_names:
                seen_enemy_names.add(name)
                t.append(("enemy", e.unit_id))

        t += [("comp", getattr(e, "addr", None)) for e, _ in self._comps
              if getattr(e, "addr", None) is not None]
        t += [("hero", getattr(e, "addr", None)) for e, _ in self._group_members
              if getattr(e, "addr", None) is not None]

        seen_gather_types = set()
        for g, _ in self._gatherables:
            from ...geo import gatherables as geo_gatherables
            base = geo_gatherables.get_display_name(g[4])
            if base not in seen_gather_types:
                seen_gather_types.add(base)
                t.append(("gather", base))

        t += [("orb", o.orb_id) for o, _ in self._orbs]
        t += [("recipe" if ("recipe" in c.chest_id.lower() or (c.loot_table and "recipe" in (c.loot_table or "").lower())) else "chest", c.chest_id) for c in self._chests]
        t += [("static", p[5]) for p, _ in self._static_pois]
        rift_mode = self._get_rift_mode()

        rst = self.model.rift_status() if rift_mode != "Off" else None
        if rst and rst.state in ("WARNING", "ACTIVE", "CLOSING", "SCHEDULED"):
            if rift_mode == "Always" or (rift_mode == "Active" and rst.state in ("WARNING", "ACTIVE", "CLOSING")):
                r_key = f"s{rst.poi_x:.1f},{rst.poi_y:.1f}" if rst.poi_x is not None else "rift_schedule"
                t.append(("rift_active", r_key))

        return t

    def _move_selection(self, delta):
        targets = self._targets()
        if not targets:
            return
        try:
            i = targets.index(self._sel)
        except ValueError:
            i = 0
        i = max(0, min(len(targets) - 1, i + delta))
        self._sel = targets[i]
        kind, key = self._sel

        # Ensure the compass follows the selection for all trackable types
        if kind in ("enemy", "spark"):
            self._track("unit", key, force=True)
        elif kind in ("chest", "recipe"):
            c = next((c for c in self._chests if c.chest_id == key), None)
            if c:
                cx, cy, cz = c.x, c.y, c.z
                label = names.chest_label(c.chest_id, c.loot_table)
                self._track(kind, f"{cx:.1f},{cy:.1f},{cz:.1f}|{label}|{c.chest_id}", force=True)
        elif kind == "comp":
            e = next((e for e, _ in self._comps if getattr(e, "addr", None) == key), None)
            if e:
                self._track("unit", e.unit_id, addr=key, force=True)
        elif kind == "hero":
            e = next((e for e, _ in self._group_members if getattr(e, "addr", None) == key), None)
            if e:
                self._track("hero", e.unit_id, addr=key, force=True)
        elif kind == "orb":
            self._track("orb", key, force=True)
        elif kind == "static":
            p_data = next((p[0] for p in self._static_pois if p[0][5] == key), None)
            if p_data:
                self._track(p_data[3], f"{p_data[0]:.1f},{p_data[1]:.1f},{p_data[2]:.1f}|{p_data[4]}|{key}", force=True)
        elif kind == "gather":
            # key is the display name (e.g. "Madrigold")
            self._track("gather", key, force=True)

        self._refresh()
        self._pan_minimap_to_sel()
        self._scroll_to_selected()

    def _scroll_to_selected(self):
        if self._sel is None:
            return
        # Give UI a moment to layout
        QtCore.QTimer.singleShot(10, self._do_scroll)

    def _do_scroll(self):
        if self._sel is None:
            return
        for box in (self.static_box, self.enemy_box, self.spark_box, self.comp_box, self.group_box, self.gather_box, self.orb_box, self.chest_box):
            for row in box._pool:
                if row.isVisible() and getattr(row, "_key", None) == self._sel:
                    self._scroll.ensureWidgetVisible(row)
                    return

    def _select(self, kind, key):
        # A click only untracks when the row is the live tracked target.
        # Selection alone goes stale the moment tracking moves elsewhere
        # (codex / minimap), so clicking an un-tracked row tracks it instead
        # of clearing the current target. Hero/companion rows are keyed by
        # instance address while the tracker keys by unit id, so resolve the
        # tracker key first or a tracked player could never be untracked.
        if kind == "hero":
            e = next((e for e, _ in self._group_members if getattr(e, "addr", None) == key), None)
            tk, tkey = "hero", e.unit_id if e else key
        elif kind == "comp":
            e = next((e for e, _ in self._comps if getattr(e, "addr", None) == key), None)
            tk, tkey = "unit", e.unit_id if e else key
        else:
            tk = "unit" if kind in ("enemy", "spark") else kind
            tkey = key
        is_tracked = self._tracker and self._tracker.is_tracked(tk, tkey)

        if is_tracked:
            self._sel = None
            if self._tracker is not None:
                # Only clear if it matches the item we are unselecting (handle background tracking)
                if not self.s.track_id or self._tracker.is_tracked(tk, tkey):
                    self._tracker.clear()

            mgr = self._tracker.parent() if self._tracker else None
            if mgr:
                map_ov = getattr(mgr, "overlays", {}).get("map")
                if map_ov and hasattr(map_ov, "canvas"):
                    map_ov.canvas._pan_x = map_ov.canvas._pan_y = 0.0
                    map_ov.canvas.update()
            self._refresh()
            return

        self._sel = (kind, key)
        if self._tracker is not None:
            if kind in ("enemy", "spark"):
                self._tracker.track("unit", key)
            elif kind == "hero":
                e = next((e for e, _ in self._group_members if getattr(e, "addr", None) == key), None)
                if e:
                    self._tracker.track("hero", e.unit_id, addr=key)
            elif kind == "comp":
                e = next((e for e, _ in self._comps if getattr(e, "addr", None) == key), None)
                if e:
                    self._tracker.track("unit", e.unit_id, addr=key)
            elif kind == "orb":
                self._tracker.track("orb", key)
            elif kind == "static":
                p_data = next((p[0] for p in self._static_pois if p[0][5] == key), None)
                if p_data:
                    self._tracker.track(p_data[3], f"{p_data[0]:.1f},{p_data[1]:.1f},{p_data[2]:.1f}|{p_data[4]}|{key}")
            elif kind == "rift_active":
                rst = self.model.rift_status() if self.model else None
                px, py, pz, label = None, None, None, "Rift"
                if rst and rst.poi_x is not None and rst.poi_y is not None:
                    px, py, pz = rst.poi_x, rst.poi_y, rst.poi_z
                    label = getattr(rst, "subzone", None) or rst.rift_name or "Rift"
                elif isinstance(key, str) and key.startswith("s"):
                    try:
                        parts = key[1:].split(",")
                        px, py, pz = float(parts[0]), float(parts[1]), 0.0
                    except Exception:
                        pass
                if px is not None and py is not None:
                    self._tracker.track("rift", f"{px:.1f},{py:.1f},{pz or 0.0:.1f}|{label}|{key}")
            elif kind == "gather":
                # key is now a display name for type-tracking (e.g. "Madrigold")
                self._tracker.track("gather", key)
            elif kind in ("chest", "recipe"):
                c = next((c for c in self._chests if c.chest_id == key), None)
                if c:
                    cx, cy, cz = c.x, c.y, c.z
                    label = names.chest_label(c.chest_id, c.loot_table)
                    self._tracker.track(kind, f"{cx:.1f},{cy:.1f},{cz:.1f}|{label}|{c.chest_id}")

        # Pan minimap to the selected entity
        self._pan_minimap_to_sel()
        self._refresh()
        self._scroll_to_selected()

    def _pan_minimap_to_sel(self):
        if self._sel is None or self._tracker is None:
            return
        kind, key = self._sel
        mgr = self._tracker.parent()
        if not mgr:
            return
        map_ov = getattr(mgr, "overlays", {}).get("map")
        if not map_ov or not map_ov.isVisible():
            return

        # Find coordinates for the selected item
        wx, wy = None, None
        if kind in ("enemy", "spark"):
            source = self._spark_mobs if kind == "spark" else self._enemies
            ent = next((e for e, _ in source if getattr(e, "unit_id", None) == key or getattr(e, "addr", None) == key), None)
            if ent:
                wx, wy = ent.x, ent.y
        elif kind == "hero":
            ent = next((e for e, _ in self._group_members if getattr(e, "addr", None) == key or getattr(e, "unit_id", None) == key), None)
            if ent:
                wx, wy = ent.x, ent.y
        elif kind == "comp":
            ent = next((e for e, _ in self._comps if getattr(e, "addr", None) == key or getattr(e, "unit_id", None) == key), None)
            if ent:
                wx, wy = ent.x, ent.y
        elif kind == "orb":
            o = next((orb for orb, _dist in self._orbs if orb.orb_id == key), None)
            if not o:
                o = geo_orbs.by_id().get(key)
            if o:
                wx, wy = o.x, o.y
        elif kind == "static":
            p_data = next((p[0] for p in self._static_pois if p[0][5] == key), None)
            if p_data:
                wx, wy = p_data[0], p_data[1]
        elif kind == "rift_active":
            rst = self.model.rift_status() if self.model else None
            if rst and rst.poi_x is not None and rst.poi_y is not None:
                wx, wy = rst.poi_x, rst.poi_y
            elif isinstance(key, str) and key.startswith("s"):
                try:
                    parts = key[1:].split(",")
                    wx, wy = float(parts[0]), float(parts[1])
                except Exception:
                    pass
        elif kind == "gather":
            from ...geo import gatherables as geo_gatherables
            # key is type name, find nearest instance currently in self._gatherables
            match = None
            for g_data, d in self._gatherables:
                if geo_gatherables.get_display_name(g_data[4]) == key:
                    if match is None or d < match[1]:
                        match = (g_data, d)
            if match:
                wx, wy = match[0][0], match[0][1]
        elif kind in ("chest", "recipe"):
            c = next((ch for ch in self._chests if ch.chest_id == key), None)
            if c:
                wx, wy = c.x, c.y

        if wx is not None and wy is not None:
            map_ov.pan_to(wx, wy)

    def select_by_key(self, kind, key) -> bool:
        """Select an item from the HUD list by kind and key (from external clicks)."""
        # Resolve gatherable IDs to type names for grouped selection
        if kind == "gather" and isinstance(key, str) and (key.startswith("gl") or key.startswith("g")):
            g_data = next((g[0] for g in self._gatherables if g[0][5] == key), None)
            if g_data:
                from ...geo import gatherables as geo_gatherables
                key = geo_gatherables.get_display_name(g_data[4])

        targets = self._targets()
        match = None
        for t_kind, t_key in targets:
            if t_kind == kind:
                if isinstance(t_key, int) and str(t_key) == str(key):
                    match = (t_kind, t_key)
                    break
                elif str(t_key).lower() == str(key).lower():
                    match = (t_kind, t_key)
                    break

        if not match:
            # If not in the current visible list, force it as the selection.
            # The next _refresh will ensure it's added to the list and properly rendered.
            try:
                self._sel = (kind, int(key) if isinstance(key, (int, str)) and str(key).isdigit() else key)
                self._refresh()
                self._scroll_to_selected()
                return True
            except Exception:
                return False

        self._select(*match)
        self._scroll_to_selected()
        return True

    def _retint(self, accent: str) -> None:
        # selected-row / icon-tile accents are read live each refresh
        pass

    def set_scale(self, scale):
        self.apply_scale(scale)

    def set_collection_owned(self, owned: set | None) -> None:
        self._owned = owned

    def set_tracker(self, tracker) -> None:
        self._tracker = tracker
        tracker.changed.connect(self._tick)

    def _track(self, kind: str, key: str | None, addr=None, force=False) -> None:
        if self._tracker is not None and key:
            if force:
                self._tracker.track(kind, key, addr=addr)
            else:
                self._tracker.toggle(kind, key, addr=addr)

    def _is_tracked(self, kind: str, key: str | None) -> bool:
        return self._tracker is not None and key is not None \
            and self._tracker.is_tracked(kind, key)

    def _is_tracked_instance(self, kind: str, unit_id: str | None, addr) -> bool:
        """Only the live instance the needle is locked on shows TRACKING -
        not every unit of the tracked type."""
        if not self._is_tracked(kind, unit_id):
            return False
        locked = self._tracker.locked_addr
        return locked is None or locked == addr

    def _hide_unit(self, unit_id: str):
        if self._tracker and self.s.track_kind == "unit":
            tid = self.s.track_id
            if tid == unit_id:
                self._tracker.clear()
        panel = getattr(self, "_main_win", None)
        if panel and hasattr(panel, "_set_unit_hidden"):
            panel._set_unit_hidden(unit_id, True)
        else:
            profile = self.model.player_profile()
            self.s.toggle_unit_hidden(unit_id, True, profile)
        self._refresh()

    def _hide_companion(self, unit_id: str):
        if self._tracker and self.s.track_kind == "unit":
            tid = self.s.track_id
            if tid == unit_id:
                self._tracker.clear()
        panel = getattr(self, "_main_win", None)
        if panel and hasattr(panel, "_set_companion_hidden"):
            panel._set_companion_hidden(unit_id, True)
        else:
            profile = self.model.player_profile()
            self.s.toggle_companion_hidden(unit_id, True, profile)
        self._refresh()

    # --- helpers ---------------------------------------------------------
    def _ranked_orbs(self, xyz, profile, player_zone=None, max_dist=0.0):
        """Nearest orbs, plain distance order."""
        hide_coll = getattr(self.s, "entity_hide_collected", True)
        pool = geo_orbs.load_orbs()
        if hide_coll:
            pool = [o for o in pool if not self.s.is_done(o.orb_id, profile)]

        cands = []
        for o in pool:
            if player_zone:
                from ...geo import zones as geo_zones
                ozone = geo_zones.get_area_id(o.zone)
                if ozone != player_zone:
                    continue
            d = o.dist2d(xyz[0], xyz[1])
            if max_dist > 0 and d > max_dist:
                continue
            cands.append((o, d))
        return sorted(cands, key=lambda t: t[1])[:self.s.orb_count]

    def _tick(self):
        try:
            self._refresh()
        except Exception as e:
            import sys
            import traceback
            print(f"[EntityOverlay Error] {e}", file=sys.stderr, flush=True)
            traceback.print_exc(file=sys.stderr)
            sys.stderr.flush()

    def _check_auto_height(self):
        # Maintain a clean bottom padding (subtle line break) for breathing room
        bottom_pad = max(3, round(6 * self._scale))
        self._bottom_spacer.changeSize(0, bottom_pad, QtWidgets.QSizePolicy.Minimum, QtWidgets.QSizePolicy.Fixed)
        self._body.invalidate()

        # Fingerprint includes counts and visibility state of items
        # Also include waypoint labels as they can have multi-line subtext
        grouped_enemy_count = len(set(names.unit_name(e.unit_id) for e, _ in self._enemies))
        grouped_spark_count = len(set(names.unit_name(e.unit_id) for e, _ in self._spark_mobs))

        # Undo row visibility
        profile = self.model.player_profile()
        last_u = getattr(self._main_win, "_last_hidden_unit", None) if hasattr(self, "_main_win") else None
        has_undo_u = bool(last_u and last_u[0] in set(self.s.get_entity_hidden_units(profile)))
        last_c = getattr(self._main_win, "_last_hidden_comp", None) if hasattr(self, "_main_win") else None
        has_undo_c = bool(last_c and last_c[0] in set(self.s.get_companion_hidden_units(profile)))

        # Rift visibility
        rift_vis = self.rift_box.isVisible()
        wp_fingerprint = tuple((p[4], p[8]) for p, _ in self._static_pois)

        curr = (
            grouped_enemy_count, grouped_spark_count, len(self._comps), len(self._group_members),
            len(self._gatherables), len(self._orbs), len(self._chests),
            len(self._loots) if hasattr(self, "_loots") else 0,
            len(self._foods) if hasattr(self, "_foods") else 0,
            len(self._static_pois), wp_fingerprint, has_undo_u, has_undo_c, rift_vis,
            self._sel,
            self.s.icon_size, self.s.entity_font_size, self.s.entity_width
        )

        if curr != self._last_h_config:
            self._last_h_config = curr
            # Apply live font-size change — rebuilds the per-overlay QSS
            new_scale = self.s.entity_font_size / 14.0
            if abs(new_scale - self._scale) > 0.005:
                self._base_w = max(300, min(800, self.s.entity_width))
                self.apply_scale(new_scale)

            # Apply live width change (independent of font scale)
            desired_w = max(300, min(800, self.s.entity_width))
            if desired_w != self.width():
                self._base_w = desired_w
                self.resize(desired_w, self.height())

            # Use a singleShot to let the layout settle before measuring height
            QtCore.QTimer.singleShot(0, self._do_auto_height)

    def _do_auto_height(self):
        try:
            if not hasattr(self, "_body") or self._body is None:
                return
            body_hint = self._body.sizeHint().height()
            CHROME = 50
            target_h = body_hint + CHROME
            screen = QtWidgets.QApplication.primaryScreen()
            screen_h = screen.availableGeometry().height() if screen else 900
            target_h = min(target_h, screen_h - 40)
            target_h = max(target_h, 60)
            if target_h != self.height():
                self._base_h = target_h
                self.resize(self.width(), target_h)
        except (RuntimeError, AttributeError):
            pass

    def closeEvent(self, e):
        self._timer.stop()
        super().closeEvent(e)
