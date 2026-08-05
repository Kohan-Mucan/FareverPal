"""Entity HUD refresh logic: gather live lists, auto-mark done state and
force tracked/selected targets back into the lists.

Split out of `entity_overlay.py` (whose `_refresh` had grown past the UI line
budget) into a mixin so the overlay file stays small. Everything here mutates
`self` state (`_enemies`, `_chests`, ...) that `entity_render` draws.
"""
from __future__ import annotations

import math
import re
from collections import defaultdict

from ...data import names, units as udata
from ...data import dungeons
from ...core import chest_resolver
from ...geo import orbs as geo_orbs, zones as geo_zones
from .entity_rows import _is_chest_orb_id


class EntityGatherMixin:
    """Provides `_refresh` and its data-collection helpers."""

    def _refresh(self):
        if self.model is None:
            return
        xyz = self.model.player_xyz()
        if xyz is None:
            return

        profile = self.model.player_profile()
        done_list = self.s.get_poi_done(profile)

        # Clear all lists while inside a dungeon/rift — overworld enemies, chests
        # and orbs are irrelevant there and stale data would be confusing.
        try:
            in_dg = self.model.is_in_dungeon() or self.model.is_in_rift()

            # If we just entered or just left a dungeon, clear ALL tracking
            if in_dg != self._last_was_in_dungeon:
                self._last_was_in_dungeon = in_dg  # Update first to prevent recursion
                if self._tracker is not None:
                    self._tracker.blockSignals(True)
                    try:
                        self._tracker.clear()
                    finally:
                        self._tracker.blockSignals(False)
                self._sel = None

            if in_dg:
                self._enemies = []
                self._spark_mobs = []
                self._group_members = []
                self._comps = []
                self._orbs = []
                self._gatherables = []
                self._chests = []
                self._static_pois = []
                return
        except Exception:
            pass

        _zid = geo_zones.resolve_zone(xyz[0], xyz[1], xyz[2])
        _zname = names.zone_name(_zid) if _zid else ""
        # Drop any raw technical IDs like "Z1", "Z2", "Z1_Enripit" that weren't mapped
        if _zname and re.fullmatch(r"Z\d+.*", _zname, re.IGNORECASE):
            _zname = ""
        isz = round(self.s.icon_size * self._scale)

        # Limit by range (400m) toggle restricts everything in the list to 400m.
        # Zone filtering was removed as it was unreliable.
        eff_max_dist = 400.0 if self.s.limit_by_zone else self.s.max_dist
        if eff_max_dist <= 0:
            eff_max_dist = 1000.0  # Plausible world-load limit if off
        enemy_max_dist = eff_max_dist

        # Companion range: False = 300m, Number = that range, True = eff_max_dist (fallback)
        c_debug = getattr(self.s, "show_companions_debug", False)
        if isinstance(c_debug, (int, float)) and not isinstance(c_debug, bool):
            comp_max_dist = float(c_debug)
        else:
            comp_max_dist = 300.0 if c_debug is False else eff_max_dist

        show_e = self.s.show_enemies
        show_s = getattr(self.s, "show_spark_mobs", False)

        self._gather_data(xyz, profile, done_list, eff_max_dist, comp_max_dist, show_e, show_s)
        done_list = self._sync_done_state(xyz, profile, done_list, eff_max_dist)
        self._force_selection_targets(xyz, profile, done_list, eff_max_dist)
        self._render_sections(profile, done_list, isz, show_e, show_s)
        self._update_drops()
        self._check_auto_height()

    # --- 1) gather every section's list (selection cycles across all of them)
    def _gather_data(self, xyz, profile, done_list, eff_max_dist, comp_max_dist, show_e, show_s):
        # Using 2D distance for filtering ensures consistency with the Minimap's top-down view

        # ENEMIES: Grouping first to ensure the 'enemy_count' limit applies to groups, not raw units
        self._spark_mobs = []
        self._enemies = []

        if show_e or show_s:
            # Get a large pool of all potential enemies first
            pool = self.model.nearest_enemies(xyz, 150, eff_max_dist, use_2d=True)

            # 1. Handle Spark Mobs (Always bypass hidden/collected if toggle is ON)
            if show_s:
                spark_pool = [(e, d) for e, d in pool if udata.drops_spark(e.unit_id)]
                groups = {}
                for e, d in spark_pool:
                    name = names.unit_name(e.unit_id) or "?"
                    groups.setdefault(name, []).append((e, d))
                sorted_names = sorted(groups.keys(), key=lambda n: min(v[1] for v in groups[n]))
                for name in sorted_names[:self.s.enemy_count]:
                    self._spark_mobs.extend(groups[name])

            # 2. Handle Regular Enemies
            if show_e:
                hidden = set(self.s.get_entity_hidden_units(profile))
                if getattr(self.s, "entity_hide_collected", True):
                    hidden.update(done_list)

                groups = {}
                for e, d in pool:
                    # Filter out hidden
                    if e.unit_id in hidden:
                        continue
                    name = names.unit_name(e.unit_id) or "?"
                    groups.setdefault(name, []).append((e, d))

                sorted_names = sorted(groups.keys(), key=lambda n: min(v[1] for v in groups[n]))
                for name in sorted_names[:self.s.enemy_count]:
                    self._enemies.extend(groups[name])

        # All items in the entity list respect the 400m limit if enabled.
        # Zone filtering was removed as it was unreliable.
        self._group_members = self.model.nearest_group_members(
            xyz, self.s.group_count, eff_max_dist, use_2d=True) \
            if getattr(self.s, "show_group_members", False) else []
        self._comps = self.model.nearest_companions(
            xyz, self.s.companion_count, eff_max_dist, player_zone=None, use_2d=True,
            hide_units=set(self.s.get_companion_hidden_units(profile))) \
            if self.s.show_companions else []
        self._orbs = self._ranked_orbs(xyz, profile, player_zone=None, max_dist=eff_max_dist) if self.s.show_orbs else []
        self._static_pois = []

        if getattr(self.s, "show_gatherables", True):
            from ...geo import gatherables as geo_gatherables
            raw_g = []

            # 1. Live memory elements first
            for g in self.model.gatherables(player_zone=None, max_dist=eff_max_dist, use_2d=True):
                eid = g.elem_id or ""
                eid_l = eid.lower()
                is_resource = any(s in eid_l for s in ("ore", "flower", "madrigold", "zealotus", "lavendula", "thyme", "tungstene", "tin", "copper", "r2plant"))
                is_internal = any(s in eid_l for s in ("spawn", "trigger", "point", "marker", "area", "volume", "target", "bumper", "idle"))
                if is_internal and not is_resource:
                    continue
                dg = g.dist2d(xyz[0], xyz[1])
                raw_g.append(((g.x, g.y, g.z, "gather", eid, f"gl{g.addr}"), dg))

            # 2. Static nodes from JSON
            for g in geo_gatherables.load_nodes():
                d = math.hypot(g.x - xyz[0], g.y - xyz[1])
                if d <= eff_max_dist:
                    if any(math.hypot(g.x - ex, g.y - ey) < 2.0 for (ex, ey, ez, ek, el, ei), ed in raw_g):
                        continue
                    raw_g.append(((g.x, g.y, g.z, "gather", g.name, f"g{g.x}{g.y}"), d))

            # Sort groups by nearest member distance and take top N types
            raw_g.sort(key=lambda t: t[1])
            # Limit search to 20 nearest nodes to keep grouped counts small and relevant
            near_g = raw_g[:20]

            groups = defaultdict(list)
            for g_data, d in near_g:
                base = geo_gatherables.get_display_name(g_data[4])
                groups[base].append((g_data, d))

            sorted_group_names = sorted(groups.keys(), key=lambda n: min(v[1] for v in groups[n]))
            limited_names = sorted_group_names[:getattr(self.s, "gatherable_count", 5)]

            self._gatherables = []
            for name in limited_names:
                self._gatherables.extend(groups[name])
        else:
            self._gatherables = []

        # Force tracked unit/hero/gather into the list so it doesn't auto-flip when leaving range
        if self._tracker is not None:
            tk, tid, taddr = self.s.track_kind, self.s.track_id, self._tracker.locked_addr
            if tk == "unit" and tid:
                # 1. Check if we have a specific address match already in the lists
                has_tracked = (taddr and (any(getattr(e, "addr", None) == taddr for e, _ in self._enemies) or
                                         any(getattr(e, "addr", None) == taddr for e, _ in self._spark_mobs) or
                                         any(getattr(e, "addr", None) == taddr for e, _ in self._comps))) or \
                              (not taddr and (any(e.unit_id == tid for e, _ in self._enemies) or
                                              any(e.unit_id == tid for e, _ in self._spark_mobs) or
                                              any(e.unit_id == tid for e, _ in self._comps)))

                if not has_tracked:
                    # Find the best match in memory
                    ent = None
                    if taddr:
                        ent = next((e for e in self.model.units() if e.addr == taddr), None)
                    else:
                        # Find the nearest one of this type
                        cands = [(e, e.dist2d(xyz[0], xyz[1])) for e in self.model.units() if e.unit_id == tid]
                        if cands:
                            ent = min(cands, key=lambda x: x[1])[0]

                    if ent:
                        dist = ent.dist2d(xyz[0], xyz[1])
                        if udata.is_companion(ent.unit_id):
                            self._comps.append((ent, dist))
                        elif show_s and udata.drops_spark(ent.unit_id):
                            self._spark_mobs.append((ent, dist))
                        else:
                            self._enemies.append((ent, dist))
            elif tk == "hero" and tid and taddr:
                if not any(getattr(e, "addr", None) == taddr for e, _ in self._group_members):
                    ent = next((e for e in self.model.units() if e.addr == taddr), None)
                    if ent:
                        self._group_members.append((ent, ent.dist2d(xyz[0], xyz[1])))
            elif tk in ("pos", "dungeon", "rift", "obelisk") and tid:
                try:
                    parts = tid.split("|")
                    coords_str = parts[0]
                    label = parts[1] if len(parts) > 1 else ""
                    tx, ty, tz = (float(v) for v in coords_str.split(","))
                    if not any(math.hypot(tx - p[0], ty - p[1]) < 0.5 for p, _ in self._static_pois):
                        dg = math.hypot(tx - xyz[0], ty - xyz[1])

                        # Use the dungeon data for boss info and clean names
                        boss_id, boss_level, dungeon_name = None, None, ""
                        if tk == "rift":
                            d_info = dungeons.get_dungeon_info(label)
                            if d_info:
                                display_name = d_info['name']
                                if not display_name.lower().startswith("rift"):
                                    display_name = f"Rift {display_name}"
                                boss_id = d_info['boss_id']
                            else:
                                display_name = label if (label and label.lower() != "rift") else "Rift"
                                if display_name.lower() != "rift" and not display_name.lower().startswith("rift"):
                                    display_name = f"Rift {display_name}"
                        elif tk == "respawn":
                            display_name = label if (label and label.lower() != "respawn") else "Respawn"
                        elif tk == "obelisk":
                            display_name = names.poi_label(label or tk)
                            dungeon_name = "Obelisk"
                        elif tk == "dungeon":
                            d_info = dungeons.get_dungeon_info(label)
                            if d_info:
                                display_name = d_info['boss_name']
                                dungeon_name = d_info['name']
                                boss_id = d_info['boss_id']
                                boss_level = d_info['level']
                            else:
                                # Use cleaned name from poi_locs if dungeon lookup fails
                                display_name = names.poi_label(label or tk)
                                for prefix in ("Dungeon ", "Boss "):
                                    if display_name.startswith(prefix):
                                        display_name = display_name[len(prefix):]
                                # Simple typo fix for display if we still don't have d_info
                                display_name = display_name.replace("Abbandoned", "Abandoned")
                                dungeon_name = "Dungeon"
                        else:
                            display_name = names.poi_label(label or tk)

                        sid = parts[2] if len(parts) > 2 else f"s{tx:.1f},{ty:.1f}"
                        self._static_pois.append(((tx, ty, tz, tk, display_name, sid, boss_id, boss_level, dungeon_name), dg))
                except (ValueError, IndexError):
                    pass
            elif tk == "gather" and tid:
                from ...geo import gatherables as geo_gatherables
                if "|" not in tid:
                    # Tracking by type name
                    if not any(geo_gatherables.get_display_name(g[4]) == tid for g, _ in self._gatherables):
                        # Find matching ones in memory/static and add at least the nearest one
                        cands = []
                        for g in self.model.gatherables(max_dist=eff_max_dist, use_2d=True):
                            if geo_gatherables.get_display_name(g.elem_id or "") == tid:
                                cands.append(((g.x, g.y, g.z, "gather", g.elem_id, f"gl{g.addr}"), g.dist2d(*xyz[:2])))
                        for node in geo_gatherables.load_nodes():
                            if geo_gatherables.get_display_name(node.name) == tid:
                                if not any(math.hypot(node.x - p[0], node.y - p[1]) < 2.0 for p, _ in cands):
                                    cands.append(((node.x, node.y, node.z, "gather", node.name, f"g{node.x}{node.y}"), math.hypot(node.x - xyz[0], node.y - xyz[1])))
                        if cands:
                            self._gatherables.append(min(cands, key=lambda x: x[1]))
                else:
                    # Tracking specific instance
                    try:
                        parts = tid.split("|")
                        coords_str = parts[0]
                        label = parts[1] if len(parts) > 1 else ""
                        tx, ty, tz = (float(v) for v in coords_str.split(","))
                        if not any(math.hypot(tx - g[0], ty - g[1]) < 0.5 for g, _ in self._gatherables):
                            dg = math.hypot(tx - xyz[0], ty - xyz[1])
                            gid = parts[2] if len(parts) > 2 else f"g{tx}{ty}"
                            self._gatherables.append(((tx, ty, tz, "gather", label, gid), dg))
                    except (ValueError, IndexError):
                        pass

        if self.s.track_kind == "orb" and self.s.track_id:
            if not any(o.orb_id == self.s.track_id for o, _ in self._orbs):
                o = geo_orbs.by_id().get(self.s.track_id)
                if o and not self.s.is_done(o.orb_id, profile):
                    self._orbs.append((o, o.dist2d(xyz[0], xyz[1])))

    # --- 2) auto-mark opened chests / collected orbs as done in settings
    def _sync_done_state(self, xyz, profile, done_list, eff_max_dist):
        # One-way only: we add to done_list when a chest/orb is opened, but NEVER
        # auto-remove. Live state is unreliable during load transitions (e.g. dungeon
        # exit) — chests reappear in memory as "closed" before the game syncs their
        # state, which would wipe collected progress. Un-marking is manual only
        # (right-click on the minimap).
        changed = False

        # 1. Chests
        # Do not limit live chests check to player_zone here (scans all loaded elements)
        opened_chests: list[tuple[str, float, float]] = []
        for e in self.model.live_chests(max_dist=eff_max_dist, use_2d=True):
            if not e.elem_id:
                continue
            eid = e.elem_id
            if eid.lower() == "fightstone":
                # Generic live id shared by EVERY fight-spot chest — resolve
                # it to the nearest static anchor so done/un-done stays
                # per-chest (and the generic id never lands in the profile).
                # live_chests() usually does this; safety net for odd cases.
                fs = [c for c in self.model.chests if "fightstone" in c.chest_id.lower()]
                nearest = min(fs, key=lambda c: math.hypot(c.x - e.x, c.y - e.y)) if fs else None
                if nearest is None or math.hypot(nearest.x - e.x, nearest.y - e.y) >= 60.0:
                    continue  # unresolvable — never touch the generic id
                eid = nearest.chest_id
                # ⛔ ORB-CHEST ACTIVITY LOGIC — DO NOT EDIT (see
                # docs/CHEST_ORB_LOGIC.md).
            is_open = e.state and e.state.lower() in ("opened", "open", "looted")
            if is_open:
                if eid not in done_list:
                    done_list.append(eid)
                    changed = True
                opened_chests.append((eid, e.x, e.y))
            else:
                # Two-way memory-scanner sync (the pre-v0.3.0 behaviour the
                # user relies on): a chest observed live and NOT opened is
                # not collected — undo a manual right-click mark so it
                # shows again automatically.
                # ⛔ ORB-CHEST ACTIVITY LOGIC — DO NOT EDIT (see
                # docs/CHEST_ORB_LOGIC.md).
                if eid in done_list:
                    done_list.remove(eid)
                    changed = True

        # 2. World Orbs (RedOrb_World)
        # Glow disappears reliably when collected
        for oid, glow in self.model.world_orb_fx():
            if not glow:
                if oid not in done_list:
                    done_list.append(oid)
                    changed = True
            # Note: We don't automatically un-mark orbs because glow can be missing
            # during load transitions or distance LOD. Manual reset only.

        # ⛔ ORB-CHEST ACTIVITY LOGIC — DO NOT EDIT (see docs/CHEST_ORB_LOGIC.md).
        #
        # 3. Activity orbs (ChestOrb / TimerCollectRun) — done when their end
        #    chest opens (e.g. Z1_World_Greenlands_ChestOrb_3_Chest_7 means the
        #    Z1_World_Greenlands_ChestOrb_3 activity is collected). End-chest ids
        #    may end in "_Chest_<n>" or just "_Chest". The base activity id
        #    (e.g. ..._ChestOrb_3) is exactly what the minimap's chest_orb
        #    section checks against the done list, so derive it straight from
        #    the opened chest id — no live-orb scan, and no prefix ambiguity
        #    between e.g. ChestOrb_3 and ChestOrb_30.
        for cid, cx, cy in opened_chests:
            base = chest_resolver.activity_base_id(cid)
            if not base:
                continue
            if not any(t in base for t in ("chestorb", "chest_orb", "timercollectrun")):
                continue
            # The reward chest's `_Chest_<n>` number does NOT reliably name its
            # activity (e.g. ..._ChestOrb_10_Chest_2 sits on the ..._ChestOrb_12
            # base), so the id prefix must not decide which activity is done —
            # mark the NEAREST activity base instead.  If none is close, the
            # chest stands alone (its own id is already in done_list above) and
            # no activity is attributed.
            near, nd = None, chest_resolver.ACTIVITY_BASE_MATCH_DIST
            for c in self.model.chests:
                cid_l = (c.chest_id or "").lower()
                if "chestorb" not in cid_l and "chest_orb" not in cid_l and "timercollectrun" not in cid_l:
                    continue
                d = math.hypot(c.x - cx, c.y - cy)
                if d <= nd:
                    near, nd = c, d
            if near and near.chest_id not in done_list:
                done_list.append(near.chest_id)
                changed = True

        # 4. Ascension activities — a Completed course is done, so its
        #    checkpoints/finish shouldn't linger as goldorb markers on the map.
        #    Start points are teleporters and stay visible regardless of course
        #    completion, so we never auto-mark them done.
        for e in self.model.obelisks(max_dist=eff_max_dist, use_2d=True):
            eid_l = (e.elem_id or "").lower()
            if not any(x in eid_l for x in ("checkpoint", "start_", "finish_")):
                continue
            if "start_" in eid_l or eid_l.endswith("_start"):
                continue  # teleporter — keep visible even when the course is complete
            if (e.state or "").lower() == "completed" and e.elem_id not in done_list:
                done_list.append(e.elem_id)
                changed = True

        # Drop stale generic "FightStone" done entries (written by older builds
        # before live fight-chest ids were resolved) — they'd hide every fight
        # chest.  ⛔ ORB-CHEST ACTIVITY LOGIC — DO NOT EDIT.
        if any(d.lower() == "fightstone" for d in done_list):
            done_list = [d for d in done_list if d.lower() != "fightstone"]
            changed = True

        if changed:
            if profile:
                self.s.save_profile_progress(profile, done_list)
            else:
                self.s.save()
        return done_list

    # --- 3) chest list + keep selection/tracker valid
    def _force_selection_targets(self, xyz, profile, done_list, eff_max_dist):
        raw_chests = self.model.nearest_chests_merged(
            xyz, self.s.chest_count + 20, eff_max_dist, player_zone=None, use_2d=True) if self.s.show_chests else []

        # Filter out recipes, event orbs (ChestOrb/TimerCollectRun) and collected chests
        raw_chests = [c for c in raw_chests if "recipe" not in c.chest_id.lower() and "recipe" not in (c.loot_table or "").lower() and not _is_chest_orb_id(c.chest_id)]
        if getattr(self.s, "entity_hide_collected", True):
            # Collected check mirrors the minimap: exact id match OR the
            # activity-base match.  Event end-chests re-spawn with new
            # `_Chest_<n>` numbers (…_ChestOrb_3_Chest_7 -> base …_ChestOrb_3),
            # so an exact-only check keeps showing a completed event chest.
            # Normalize BOTH sides: done end-chest ids contribute their base
            # (same end_open_profile set the minimap builds).
            done_lower = {d.lower() for d in done_list}
            for d in done_list:
                b = chest_resolver.activity_base_id(d)
                if b:
                    done_lower.add(b)
            def _is_done(cid: str) -> bool:
                if cid.lower() in done_lower:
                    return True
                b = chest_resolver.activity_base_id(cid)
                return bool(b and b in done_lower)
            raw_chests = [c for c in raw_chests if not _is_done(c.chest_id)]

        self._chests = raw_chests[:self.s.chest_count]

        if self.s.track_kind == "chest" and self.s.track_id:
            try:
                from ...core.chest_resolver import ChestRow
                parts = self.s.track_id.split("|")
                coords_str = parts[0]
                label = parts[1] if len(parts) > 1 else "chest"
                raw_id = parts[2] if len(parts) > 2 else label

                tx, ty, tz = (float(v) for v in coords_str.split(","))

                # Filter out recipes from the HUD list even if tracked
                if "recipe" in label.lower():
                    pass
                else:
                    if len(parts) <= 2:
                        matching_c = next((c for c in self.model.chests if c.chest_id == label), None)
                        if not matching_c:
                            matching_c = next((c for c in self.model.chests if math.hypot(tx - c.x, ty - c.y) < 1.0), None)
                        raw_id = matching_c.chest_id if matching_c else label

                    if not _is_chest_orb_id(raw_id) and (not getattr(self.s, "entity_hide_collected", True) or not _is_done(raw_id)):
                        is_in_list = False
                        for c in self._chests:
                            if c.chest_id == raw_id or math.hypot(tx - c.x, ty - c.y) < 1.0:
                                is_in_list = True
                                break
                        if not is_in_list:
                            # Explicit chest tracking (codex list / minimap) is a
                            # fixed waypoint — the chest is static, so distance
                            # must never drop the row or clear the tracker (a
                            # far target used to vanish at >800m).
                            dist = math.hypot(tx - xyz[0], ty - xyz[1])
                            matching_c = next((c for c in self.model.chests if c.chest_id == raw_id), None)
                            loot_table = matching_c.loot_table if matching_c else label
                            level = matching_c.level if matching_c else None
                            self._chests.append(ChestRow(raw_id, dist, loot_table, level, None, False, tx, ty, tz))
            except (ValueError, IndexError):
                pass

        # Force active selection into target lists so selection persists across ticks
        if self._sel is not None:
            skind, skey = self._sel
            if skind == "hero" and not any(str(getattr(e, "addr", None)) == str(skey) for e, _ in self._group_members):
                ent = next((e for e in self.model.units() if str(getattr(e, "addr", None)) == str(skey)), None)
                if ent:
                    self._group_members.append((ent, ent.dist2d(xyz[0], xyz[1])))
            elif skind == "comp" and not any(str(getattr(e, "addr", None)) == str(skey) for e, _ in self._comps):
                ent = next((e for e in self.model.units() if str(getattr(e, "addr", None)) == str(skey)), None)
                if ent:
                    self._comps.append((ent, ent.dist2d(xyz[0], xyz[1])))
            elif skind == "enemy":
                # For enemies, skey is the unit_id. If no unit of this type is in the list, force the nearest one.
                if not any(e.unit_id == skey for e, _ in self._enemies):
                    cands = [(e, e.dist2d(xyz[0], xyz[1])) for e in self.model.units() if e.unit_id == skey]
                    if cands:
                        ent, dist = min(cands, key=lambda x: x[1])
                        self._enemies.append((ent, dist))
            elif skind == "gather":
                from ...geo import gatherables as geo_gatherables
                # For gatherables, skey is the display name
                if not any(geo_gatherables.get_display_name(g[4]) == skey for g, _ in self._gatherables):
                    # Find matching ones in memory/static and add at least the nearest one
                    cands = []
                    for g in self.model.gatherables(max_dist=eff_max_dist, use_2d=True):
                        if geo_gatherables.get_display_name(g.elem_id or "") == skey:
                            cands.append(((g.x, g.y, g.z, "gather", g.elem_id, f"gl{g.addr}"), g.dist2d(*xyz[:2])))
                    for node in geo_gatherables.load_nodes():
                        if geo_gatherables.get_display_name(node.name) == skey:
                            if not any(math.hypot(node.x - p[0], node.y - p[1]) < 2.0 for p, _ in cands):
                                cands.append(((node.x, node.y, node.z, "gather", node.name, f"g{node.x}{node.y}"), math.hypot(node.x - xyz[0], node.y - xyz[1])))
                    if cands:
                        self._gatherables.append(min(cands, key=lambda x: x[1]))
            elif skind == "orb" and not any(str(o.orb_id) == str(skey) for o, _ in self._orbs):
                if not getattr(self.s, "entity_hide_collected", True) or not self.s.is_done(skey, profile):
                    o = geo_orbs.by_id().get(skey)
                    if o:
                        self._orbs.append((o, o.dist2d(xyz[0], xyz[1])))
            elif skind in ("chest", "recipe") and not _is_chest_orb_id(skey) and not any(str(c.chest_id) == str(skey) for c in self._chests):
                if not getattr(self.s, "entity_hide_collected", True) or skey not in done_list:
                    from ...core.chest_resolver import ChestRow
                    matching_c = next((c for c in self.model.chests if str(c.chest_id) == str(skey)), None)
                    if matching_c:
                        dist = matching_c.dist2d(xyz[0], xyz[1])
                        self._chests.append(ChestRow(matching_c.chest_id, dist, matching_c.loot_table, matching_c.level, None, False, matching_c.x, matching_c.y, matching_c.z))

        # Deduplicate unit lists by address to prevent duplicate HUD rows
        def _dedup_units(lst):
            seen, res = set(), []
            for item, dist in lst:
                addr = getattr(item, "addr", id(item))
                if addr not in seen:
                    seen.add(addr)
                    res.append((item, dist))
            return res

        self._enemies = _dedup_units(self._enemies)
        self._comps = _dedup_units(self._comps)
        self._group_members = _dedup_units(self._group_members)

        # 2) keep selection valid (auto-flip to first available target)
        targets = self._targets()
        if self._sel is not None and self._sel not in targets:
            old_sel = self._sel
            self._sel = None

            # Spark Mobs: Auto-select the next available spark mob ONLY if a spark mob was actively selected by user
            if old_sel and old_sel[0] == "spark":
                next_spark = next((t for t in targets if t[0] == "spark"), None)
                if next_spark:
                    self._sel = next_spark
                    self._track("unit", next_spark[1], force=True)

            # Collectibles: ONLY auto-select the next one if the toggle is actually ON in settings
            elif getattr(self.s, "auto_select_next_collectible", False):
                old_kind = old_sel[0]
                if old_kind in ("orb", "chest", "gather"):
                    # Prioritize finding the exact same kind first
                    next_coll = next((t for t in targets if t[0] == old_kind), None)

                    # Fallback to similar types only if the current category is empty
                    if not next_coll and old_kind in ("chest", "gather"):
                        # Gatherables can fall back to Orbs/Chests and vice versa
                        next_coll = next((t for t in targets if t[0] in ("orb", "chest", "gather")), None)

                    if next_coll:
                        limit = eff_max_dist if eff_max_dist > 0 else 500.0
                        if next_coll[0] == "orb":
                            o = next((orb for orb, _dist in self._orbs if orb.orb_id == next_coll[1]), None)
                            dist = o.dist2d(xyz[0], xyz[1]) if o else 9999.0
                            if dist <= limit:
                                self._sel = next_coll
                                self._track("orb", next_coll[1], force=True)
                        elif next_coll[0] == "chest":
                            c = next((ch for ch in self._chests if ch.chest_id == next_coll[1]), None)
                            if c and c.dist <= limit:
                                label = names.chest_label(c.chest_id, c.loot_table)
                                self._sel = next_coll
                                self._track("chest", f"{c.x:.1f},{c.y:.1f},{c.z:.1f}|{label}|{c.chest_id}", force=True)
                        elif next_coll[0] == "gather":
                            # next_coll[1] is the type name (e.g. "Madrigold")
                            self._sel = next_coll
                            self._track("gather", next_coll[1], force=True)

            # If the selected target is gone and no auto-replacement was found, clear the compass & map pan
            if self._sel is None and self._tracker is not None:
                self._tracker.clear()
                mgr = self._tracker.parent()
                if mgr:
                    map_ov = getattr(mgr, "overlays", {}).get("map")
                    if map_ov and hasattr(map_ov, "canvas") and (map_ov.canvas._pan_x != 0.0 or map_ov.canvas._pan_y != 0.0):
                        map_ov.canvas._pan_x = map_ov.canvas._pan_y = 0.0
                        map_ov.canvas.update()

        # 3) fallback: clear tracker if the target is now "done" (handles minimap clicks without HUD selection)
        if self._tracker is not None and self.s.track_kind in ("orb", "chest", "recipe", "gather", "pos"):
            tk, tid = self.s.track_kind, self.s.track_id
            parts = tid.split("|")
            target_id = parts[-1] if (tk in ("chest", "recipe", "gather", "pos") and len(parts) >= 3) else (tid if tk == "orb" else "")

            if tk in ("chest", "recipe", "gather", "pos") and not target_id:
                try:  # resolve from coords
                    coords_str = parts[0]
                    label = parts[1] if len(parts) > 1 else ""
                    tx, ty, _ = (float(v) for v in coords_str.split(","))
                    if tk in ("chest", "recipe"):
                        matching_c = next((c for c in self.model.chests if math.hypot(tx - c.x, ty - c.y) < 1.0), None)
                        target_id = matching_c.chest_id if matching_c else label
                    else:
                        target_id = label
                except Exception:
                    target_id = label

            # Only auto-clear the tracker if we are hiding collected items.
            # If the user is showing collected items, they likely want to be able to point at them.
            if target_id and self.s.is_done(target_id, profile) and getattr(self.s, "entity_hide_collected", True):
                self._tracker.clear()
