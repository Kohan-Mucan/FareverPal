"""Entity HUD section rendering.

Builds the RowSpec lists for every HUD section (spark mobs, enemies, group,
companions, gatherables, orbs, chests, rift, waypoints) and fills the pooled
sections. Split out of `entity_overlay.py` to keep that file under the UI
line budget.
"""
from __future__ import annotations

import math
from collections import defaultdict

from .. import theme
from ...data import icons, names, units as udata
from ...geo import orbs as geo_orbs
from .entity_rows import RowSpec


def _static_waypoint_marker(kind: str) -> str:
    """Map-marker asset name for a tracked static-POI kind (the Entity HUD
    waypoint row icon, matching the minimap's marker art). Soulstones use the
    bundled `soulstone` gem marker; unknown kinds fall back to 'map-pin'."""
    if kind in ("dungeon", "rift"):
        return "rift" if kind == "rift" else "dungeon"
    if kind == "obelisk":
        return "obelisk"
    if kind == "respawn":
        return "respawnpoint"
    if kind == "soulstone":
        return "soulstone"
    return "map-pin"


class EntityRenderMixin:
    """Provides `_render_sections` and one helper per section."""

    def _render_sections(self, profile, done_list, isz, show_e, show_s):
        # 2.5) render spark mobs
        if show_s:
            self.spark_box.show()
            groups = defaultdict(list)
            for e, d in self._spark_mobs:
                name = names.unit_name(e.unit_id) or "?"
                groups[name].append((e, d))

            sorted_groups = sorted(groups.items(), key=lambda x: min(v[1] for v in x[1]))
            specs = []
            for name, variants in sorted_groups:
                variants.sort(key=lambda x: x[1])
                sel_pair = next((v for v in variants if ("spark", v[0].unit_id) == self._sel), None)
                e, d = sel_pair if sel_pair else variants[0]
                count = len(variants)
                key = ("spark", e.unit_id)
                tracked = self._is_tracked("unit", e.unit_id)

                # Icon color: Use standard enemy color (Red) so they look like enemies,
                # but use the Accent for the name color and tracking status.
                icon_col = theme.KIND_COLOR.get(e.kind, theme.DANGER)
                name_col = self.s.hud_accent or theme.ACCENT

                specs.append(RowSpec(
                    "Units", e.unit_id, icon_col, name if count <= 1 else f"{name} (x{count})", name_col,
                    sub="◈ TRACKING" if tracked else "",
                    value=f"{d:>6.0f}m", bold=tracked, highlight=tracked,
                    outlined=True, outline_border=1,
                    cb=(lambda k=key: self._select(*k)),
                    key=key))
            self.spark_box.fill(specs, isz)
            self.spark_box.setVisible(bool(specs))
        else:
            self.spark_box.hide()

        # 3) render enemies
        if show_e:
            self.enemy_box.show()

            # Sort groups by nearest member distance
            # (Note: self._enemies is already limited to N groups by the logic in _refresh)
            groups = defaultdict(list)
            for e, d in self._enemies:
                name = names.unit_name(e.unit_id) or "?"
                groups[name].append((e, d))

            sorted_groups = sorted(groups.items(), key=lambda x: min(v[1] for v in x[1]))

            specs = []
            for name, variants in sorted_groups:
                variants.sort(key=lambda x: x[1])
                # Check if the user selected a specific member of this group
                sel_pair = next((v for v in variants if ("enemy", v[0].unit_id) == self._sel), None)
                e, d = sel_pair if sel_pair else variants[0]
                count = len(variants)

                key = ("enemy", e.unit_id)
                # Badge shows if this unit type is tracked
                tracked = self._is_tracked("unit", e.unit_id)

                is_boss = udata.is_boss(e.unit_id)
                is_elite = udata.is_elite(e.unit_id)
                border_col = theme.GOLD if is_boss else None

                # Base color for this row (Default: Danger/Red for enemies)
                base_col = theme.KIND_COLOR.get(e.kind, theme.DANGER)

                # Icon and Outline color
                if is_boss:
                    col = theme.GOLD
                elif is_elite:
                    col = theme.SILVER
                else:
                    col = base_col

                # Name color - Accent if selected/tracked, otherwise Gold for boss, normal (Red) for elite/trash
                if is_boss:
                    name_col = theme.GOLD
                elif tracked:
                    name_col = self.s.hud_accent or theme.ACCENT
                else:
                    name_col = base_col

                display_name = name if count <= 1 else f"{name} (x{count})"
                specs.append(RowSpec(
                    "Units", e.unit_id, col, display_name, name_col,
                    sub="◈ TRACKING" if tracked else "",
                    value=f"{d:>6.0f}m", bold=is_boss or tracked, highlight=tracked,
                    outlined=True, outline_border=4 if (is_boss or is_elite) else 2,
                    border_color=border_col,
                    cb=(lambda k=key: self._select(*k)),
                    right_cb=(lambda u=e.unit_id: self._hide_unit(u)),
                    key=key))

            # Undo slot for enemies
            panel = getattr(self, "_main_win", None)
            last = getattr(panel, "_last_hidden_unit", None) if panel else None
            if last:
                hidden_units = set(self.s.get_entity_hidden_units(profile))
                if last[0] in hidden_units:
                    specs.append(RowSpec(
                        "Units", last[0], theme.MUTED, last[1], theme.MUTED,
                        sub="Undo Last Hidden", value="HIDDEN",
                        highlight=True, outlined=True,
                        cb=lambda: panel._unhide_last_unit() if panel else None,
                        right_cb=lambda: panel._unhide_last_unit() if panel else None,
                        key=("hidden", last[0])))

            self.enemy_box.fill(specs, isz)
            tag = ""
            if not getattr(self.model, "units_ok", True):
                tag = "READ FAILED · RETRYING"
            self.enemy_box.header.set_tag(tag)
            self.enemy_box.setVisible(bool(specs) or bool(tag))
        else:
            self.enemy_box.hide()

        # 3.5) render group members
        if getattr(self.s, "show_group_members", False):
            self.group_box.show()
            specs = []
            for e, d in self._group_members:
                addr = getattr(e, "addr", None)
                key = ("hero", addr)
                sel = addr is not None and key == self._sel
                tracked = sel and self._is_tracked("hero", e.unit_id)
                cls_name = e.cls or ""
                hero_class = "warrior"
                if cls_name.startswith("ent.hero."):
                    hero_class = cls_name.replace("ent.hero.", "").lower()
                elif e.unit_id:
                    hero_class = e.unit_id.lower()
                    if "warrior" in hero_class:
                        hero_class = "warrior"
                    elif "rogue" in hero_class:
                        hero_class = "rogue"
                    elif "mage" in hero_class:
                        hero_class = "mage"
                    elif "priest" in hero_class:
                        hero_class = "priest"

                col_hex = theme.HERO.get(hero_class, theme.TEXT)
                col = col_hex  # Keep class color even when selected/tracked
                specs.append(RowSpec(
                    None, None, col, self.model.hero_display_name(e), col,
                    sub="◈ TRACKING" if tracked else "",
                    value=f"{d:>6.0f}m", bold=tracked, highlight=tracked, outlined=True,
                    ui_icon="player",
                    cb=(lambda k=key: self._select(*k)) if addr is not None else None,
                    key=key))
            self.group_box.fill(specs, isz)
            tag = ""
            if not getattr(self.model, "units_ok", True):
                tag = "READ FAILED · RETRYING"
            self.group_box.header.set_tag(tag)
            self.group_box.setVisible(bool(specs) or bool(tag))
        else:
            self.group_box.hide()

        # 4) render wild companions (critters) - their own section, never
        # enemies, never distance-capped. Ones missing from the account
        # collection get flagged.
        if self.s.show_companions:
            self.comp_box.show()
            specs = []
            n_new = 0
            for e, d in self._comps:
                unit_id = e.unit_id
                display_name = names.unit_name(unit_id) or names.humanize(unit_id)

                missing = self._owned is not None and unit_id not in self._owned
                n_new += missing
                addr = getattr(e, "addr", None)
                sel = addr is not None and ("comp", addr) == self._sel
                # Badge follows the selected row — not the tracker's closest-mob lock
                tracked = sel and self._is_tracked("unit", unit_id)
                is_spark = "spark" in display_name.lower()

                if tracked:
                    col = self.s.hud_accent or theme.ACCENT
                else:
                    col = theme.GOLD if (missing or is_spark) else theme.GOOD

                sub_bits = [b for b in (
                    "★ SPARK" if is_spark else "",
                    "★ NOT COLLECTED" if missing else "",
                    "◈ TRACKING" if tracked else "") if b]
                if tracked:
                    sub_bits.append(unit_id)
                specs.append(RowSpec(
                    "collection", unit_id, col,
                    display_name, col,
                    sub="  ·  ".join(sub_bits),
                    value=f"{d:>6.0f}m", bold=missing or tracked or is_spark,
                    highlight=missing or tracked, outlined=True,
                    outline_border=2,
                    border_color=theme.GOLD if is_spark else None,
                    cb=(lambda k=("comp", addr): self._select(*k)) if addr is not None else None,
                    right_cb=(lambda u=unit_id: self._hide_companion(u)),
                    key=("comp", addr)))

            # Undo slot for companions (excludes mounts & gliders)
            panel = getattr(self, "_main_win", None)
            last = getattr(panel, "_last_hidden_comp", None) if panel else None
            if last and not (last[0].startswith("Mount_") or last[0].startswith("Glider_")):
                hidden_comps = set(self.s.get_companion_hidden_units(profile))
                if last[0] in hidden_comps:
                    specs.append(RowSpec(
                        "collection", last[0], theme.MUTED, last[1], theme.MUTED,
                        sub="Undo Last Hidden", value="HIDDEN",
                        highlight=True, outlined=True,
                        cb=lambda: panel._unhide_last_comp() if panel else None,
                        right_cb=lambda: panel._unhide_last_comp() if panel else None,
                        key=("hidden", last[0])))

            self.comp_box.fill(specs, isz)
            tag = "WILD"
            if not getattr(self.model, "units_ok", True):
                tag = "READ FAILED · RETRYING"
            self.comp_box.header.set_tag(tag)
            self.comp_box.setVisible(bool(specs))
        else:
            self.comp_box.hide()

        # 4.5) render gatherables (Ores/Plants)
        if getattr(self.s, "show_gatherables", True):
            self.gather_box.show()
            from ...geo import gatherables as geo_gatherables

            # Group them for rendering (similar to enemies)
            groups = defaultdict(list)
            for g_data, d in self._gatherables:
                base = geo_gatherables.get_display_name(g_data[4])
                groups[base].append((g_data, d))

            sorted_groups = sorted(groups.items(), key=lambda x: min(v[1] for v in x[1]))

            specs = []
            for name, variants in sorted_groups:
                variants.sort(key=lambda x: x[1])
                g_data, d = variants[0]
                count = len(variants)

                label = g_data[4]
                key = ("gather", name)
                tracked = self._is_tracked("gather", name)

                name_l = label.lower()
                is_ore = "ore" in name_l or "tungstene" in name_l or "tin" in name_l or "copper" in name_l
                accent = theme.KIND_COLOR["ore" if is_ore else "flower"]
                col = self.s.hud_accent if tracked else accent

                # Auto-scale logic match (for sub-text or visual emphasis)
                is_large = "_large" in name_l or "_big" in name_l or "tungstene" in name_l

                display_name = name if count <= 1 else f"{name} (x{count})"
                marker = geo_gatherables.get_icon_name(label)

                # Check if this gatherable type is enabled in settings
                setting_attr = geo_gatherables.get_setting_attr(label)
                if setting_attr and not getattr(self.s, setting_attr, True):
                    continue

                # Fallback to generic flower/ore if icon not found
                if not icons.asset_icon(marker, 16):
                    marker = "ore" if is_ore else "flower"

                sub = ""
                if tracked:
                    sub = "◈ TRACKING"

                specs.append(RowSpec(
                    None, None, accent, display_name, col,
                    sub=sub,
                    value=f"{d:>6.0f}m", bold=tracked or is_large,
                    highlight=tracked, outlined=True,
                    outline_border=2,
                    cb=(lambda k=key: self._select(*k)),
                    marker=marker,
                    key=key))
            self.gather_box.fill(specs, isz)
            self.gather_box.header.set_tag("")
            self.gather_box.setVisible(bool(specs))
        else:
            self.gather_box.hide()

        # 5) render the nearest uncollected secret orbs (click = compass needle)
        if self.s.show_orbs:
            self.orb_box.show()
            orb_col = theme.KIND_COLOR["orb"]
            specs = []
            for o, d in self._orbs:
                done = self.s.is_done(o.orb_id, profile)
                tracked = self._is_tracked("orb", o.orb_id)

                col = self.s.hud_accent if tracked else orb_col

                specs.append(RowSpec(
                    None, None, col if tracked else None, geo_orbs.orb_label(o.orb_id), col,
                    sub="◈ TRACKING" if tracked else "",
                    value=f"{d:>6.0f}m", bold=tracked,
                    highlight=tracked, outlined=True,
                    outline_border=2,
                    cb=(lambda oid=o.orb_id: self._select("orb", oid)),
                    marker="orb2" if done else "orb",
                    key=("orb", o.orb_id)))
            self.orb_box.fill(specs, isz)
            self.orb_box.header.set_tag(
                "" if self._orbs else "ALL MARKED")
            self.orb_box.setVisible(bool(specs))
        else:
            self.orb_box.hide()

        # 6) render chests (also selectable -> trackable on the map)
        if self.s.show_chests:
            self.chest_box.show()
            specs = []
            for c in self._chests:
                is_recipe = "recipe" in c.chest_id.lower() or (c.loot_table and "recipe" in (c.loot_table or "").lower())
                kind = "recipe" if is_recipe else "chest"
                color = theme.CHEST  # Keep gold color even when selected/tracked

                done = c.chest_id in done_list
                label = names.chest_label(c.chest_id, c.loot_table)

                cx, cy, cz = c.x, c.y, c.z
                # check if this specific chest is the tracked target
                is_tracked = self._is_tracked(kind, f"{cx:.1f},{cy:.1f},{cz:.1f}|{label}") or \
                             self._is_tracked("pos", f"{cx:.1f},{cy:.1f},{cz:.1f}|{label}")

                accent = theme.CHEST
                marker = "Recipe" if is_recipe else "chest"
                if done:
                    marker += "2"

                sub = ""
                if is_tracked:
                    state_tag = (c.state or ("OPENED" if done else "CLOSED")).upper()
                    sub = f"◈ TRACKING  ·  {state_tag}"

                name_col = self.s.hud_accent if is_tracked else color

                specs.append(RowSpec(
                    None, None, None if (done or is_recipe) else accent, label, name_col,
                    sub=sub, value=f"{c.dist:>6.0f}m",
                    bold=is_tracked, highlight=is_tracked, outlined=True,
                    outline_border=2,
                    cb=(lambda k=(kind, c.chest_id): self._select(*k)),
                    marker=marker,
                    key=(kind, c.chest_id)))
            self.chest_box.fill(specs, isz)
            self.chest_box.header.set_tag("")
            self.chest_box.setVisible(bool(specs))
        else:
            self.chest_box.hide()

        rift_mode = self._get_rift_mode()

        rst = self.model.rift_status() if rift_mode != "Off" else None
        if rst and rst.state in ("WARNING", "ACTIVE", "CLOSING", "SCHEDULED"):
            show_card = (rift_mode == "Always") or (rift_mode == "Active" and rst.state in ("WARNING", "ACTIVE", "CLOSING"))
            if show_card:
                self.rift_box.show()
                self.rift_box.header.set_tag("")
                r_key = ("rift_active", f"s{rst.poi_x:.1f},{rst.poi_y:.1f}" if rst.poi_x is not None else "rift_schedule")
                r_sel = self._sel == r_key

                # Highlight only during active/warning (15m + 3m window)
                is_active_window = rst.state in ("WARNING", "ACTIVE", "CLOSING")
                r_col = theme.GOLD if rst.state in ("WARNING", "SCHEDULED") else (theme.DANGER if rst.state == "CLOSING" else (theme.GOOD if rst.state == "ACTIVE" else theme.TEXT))

                in_range = getattr(rst, "in_range", False)
                at_any_rift = getattr(rst, "at_any_rift", False)
                subzone = getattr(rst, "subzone", rst.rift_name or "")

                area = subzone if subzone and subzone != "Rift" else ""
                t = rst.formatted_time

                # Determine labels based on range and state (7 granular states)
                if rst.state == "ACTIVE":
                    r_label = rst.status_label or (f"Rift · {area} (Active)" if area else "Rift Active")
                    r_sub = f"WRONG LOCATION · Head to {area}" if (area and not in_range and at_any_rift) else ""
                elif in_range:
                    if rst.state == "CLOSING":
                        # 3. CLOSING (In Range)
                        r_label = f"Rift · {area} Closing In · {t}" if area else f"Rift Closing In · {t}"
                        r_sub = ""
                    elif rst.state == "WARNING":
                        # 2. WARNING (In Range)
                        r_label = f"Rift · {area} Starting In · {t}" if area else f"Rift Starting In · {t}"
                        r_sub = ""
                    else:
                        # 1. SCHEDULED (In Range)
                        r_label = f"Next Rift · Starting In · {t}"
                        r_sub = ""
                else:
                    if rst.state == "CLOSING":
                        # 6. CLOSING (Out of Range)
                        r_label = f"Rift · {area} Closing In · {t}" if area else f"Rift Closing In · {t}"
                        r_sub = f"WRONG LOCATION · Head to {area}" if (area and at_any_rift) else ""
                    elif rst.state == "WARNING":
                        # 5. WARNING (Out of Range)
                        r_label = f"Rift · {area} Starting In · {t}" if area else f"Rift Starting In · {t}"
                        r_sub = f"Rift is at {area}" if (area and at_any_rift) else ""
                    else:
                        # 4. SCHEDULED (Out of Range)
                        r_label = f"Next Rift · {area} · {t}" if area else f"Next Rift In · {t}"
                        r_sub = ""

                r_val = ""
                if rst.poi_x is not None and self.model.player_xyz():
                    px, py, _ = self.model.player_xyz()
                    d = math.hypot(rst.poi_x - px, rst.poi_y - py)
                    r_val = f"{d:>5.0f}m"

                # Size: Active/Warning/Closing (The "Rift" is active) is 20px, Upcoming is 16px
                is_active_window = rst.state in ("WARNING", "ACTIVE", "CLOSING")
                r_fs = 16 if is_active_window else 17

                self.rift_box.fill([RowSpec(
                    None, None, "#C084FC" if is_active_window else None,
                    r_label, r_col,
                    sub=r_sub, value=r_val,
                    bold=is_active_window or r_sel,
                    highlight=is_active_window or r_sel,
                    outlined=True, marker="rift",
                    cb=(lambda k=r_key: self._select(*k)),
                    key=r_key,
                    font_size=r_fs
                )], isz)
            else:
                self.rift_box.hide()
        else:
            self.rift_box.hide()

        # 6.5) render tracked static POIs (Dungeons/Obelisks/Waypoints)
        if self._static_pois:
            self.static_box.show()
            self.static_box.header.set_text("WAYPOINT  ◈ TRACKING")
            specs = []

            for p_data, d in self._static_pois:
                label = p_data[4]
                kind = p_data[3]
                key = ("static", p_data[5])
                # boss_id is at index 6, level at index 7, dungeon_name at index 8
                boss_id = p_data[6] if len(p_data) > 6 else None
                level = p_data[7] if len(p_data) > 7 else None
                dname = p_data[8] if len(p_data) > 8 else ""
                sel = self._sel == key

                # Determine icon and accent based on kind
                ui_icon = ""
                marker_name = _static_waypoint_marker(kind)

                sub = ""
                sub_color = None

                if level:
                    # Line 1: Boss Name  ·  Level # (Boss Name bold, Level normal)
                    label = f"<b>{label}</b>  ·  Level {level}"
                elif dname:
                    # Line 1: Bold Title for Dungeons/Obelisks
                    label = f"<b>{label}</b>"

                if dname:
                    # Line 2: Dungeon Name / POI Type
                    sub = dname
                    sub_color = self.s.hud_accent if sel else theme.GOLD

                soul_col = theme.KIND_COLOR.get("soulstone", theme.ACCENT)
                if boss_id:
                    acc = self.s.hud_accent if sel else (soul_col if kind == "soulstone" else None)
                    boss_acc = self.s.hud_accent if sel else theme.GOLD
                    color = self.s.hud_accent if sel else theme.GOLD
                    # Same two-icon layout as dungeons: the kind marker as the
                    # primary tile (the soulstone gem) plus the boss sprite as
                    # the secondary tile. The soulstone demons are elites, not
                    # bosses, so the boss sprite's border is silver to match
                    # the codex's elite badges.
                    if kind == "soulstone" and not sel:
                        boss_acc = (theme.SILVER if udata.is_elite(boss_id)
                                    else theme.GOLD)
                    specs.append(RowSpec(
                        None, None, acc, label or kind.capitalize(), color,
                        sub=sub, sub_color=sub_color, value=f"{d:>6.0f}m",
                        bold=False, highlight=sel, outlined=True,
                        cb=(lambda k=key: self._select(*k)),
                        ui_icon=ui_icon, marker=marker_name,
                        extra_icon=("Units", boss_id, "", True, boss_acc),
                        key=key,
                        size_override=isz + 2 if kind in ("dungeon", "rift") else None))
                else:
                    acc = self.s.hud_accent if sel else (soul_col if kind == "soulstone" else None)
                    color = self.s.hud_accent if sel else theme.TEXT
                    specs.append(RowSpec(
                        None, None, acc, label or kind.capitalize(), color,
                        sub=sub, sub_color=sub_color, value=f"{d:>6.0f}m",
                        bold=False, highlight=sel, outlined=True,
                        cb=(lambda k=key: self._select(*k)),
                        marker=marker_name,
                        ui_icon=ui_icon,
                        key=key,
                        size_override=isz + 2 if kind in ("dungeon", "rift") else None))
            self.static_box.fill(specs, isz)
        else:
            self.static_box.hide()
            self.static_box.header.set_text("WAYPOINT")
