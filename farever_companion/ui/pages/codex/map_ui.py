"""Codex map UI: map panel builder, coordinate resolution, and pin plotting."""
from __future__ import annotations

import json

from PySide6 import QtCore, QtGui, QtWidgets

from ... import theme
from ....core.updater import is_frozen
from ....data import codex
from .map import CodexZoneMapCanvas

# Secret-orb pins on the codex preview map plot red; everywhere else
# (list icons, HUD, minimap) the orb stays its blue KIND_COLOR.
ORB_PIN_COLOR = "#ef4444"


class CodexMapUiMixin:
    def _build_codex_map_panel(self) -> QtWidgets.QFrame:
        """Right column: zone spawn map canvas with header buttons."""
        self._map_container = QtWidgets.QFrame()
        self._map_container.setObjectName("Cell")
        map_v = QtWidgets.QVBoxLayout(self._map_container)
        map_v.setContentsMargins(6, 6, 6, 6)
        map_v.setSpacing(6)

        map_header = QtWidgets.QHBoxLayout()
        # Top-left header shows the attached character profile (name + class)
        # instead of a static 'MAP' title.
        self._map_title_lbl = QtWidgets.QLabel("")
        self._map_title_lbl.setStyleSheet(f"color:{theme.ACCENT}; font-size:11px; font-weight:bold;")
        map_header.addWidget(self._map_title_lbl)
        map_header.addStretch(1)
        if hasattr(self, "_update_codex_profile_label"):
            self._update_codex_profile_label()

        btn_text_qss = (
            f"QPushButton {{ background: transparent; border: 0; color: {theme.MUTED}; "
            f"font-size: 11px; font-weight: bold; padding: 2px 6px; }}"
            f"QPushButton:hover {{ color: {theme.TEXT}; }}"
        )

        self._btn_plot_all_pins = QtWidgets.QPushButton("Missing")
        self._btn_plot_all_pins.setFixedHeight(22)
        self._btn_plot_all_pins.setToolTip("Plot spawn/entrance pins for all missing/uncollected mobs")
        self._btn_plot_all_pins.setStyleSheet(btn_text_qss)
        self._btn_plot_all_pins.clicked.connect(self._plot_all_visible_pins)
        map_header.addWidget(self._btn_plot_all_pins)

        self._btn_clear_map_sel = QtWidgets.QPushButton("Clear Pins")
        self._btn_clear_map_sel.setFixedHeight(22)
        self._btn_clear_map_sel.setToolTip("Clear all map pins")
        self._btn_clear_map_sel.setStyleSheet(btn_text_qss)
        self._btn_clear_map_sel.clicked.connect(self._clear_codex_map_selection)
        map_header.addWidget(self._btn_clear_map_sel)

        map_v.addLayout(map_header)

        self._codex_map_widget = CodexZoneMapCanvas(self.model, self.s, parent=self._map_container)
        self._codex_map_widget.setMinimumWidth(320)
        map_v.addWidget(self._codex_map_widget, 1)

        self._map_info_lbl = QtWidgets.QLabel("Left-click card to plot spawns")
        self._map_info_lbl.setFixedHeight(30)
        self._map_info_lbl.setWordWrap(True)
        # Not text-selectable: copying goes through the right-click Copy menu
        # (dev-only) below, which covers the line and the raw JSON.
        self._map_info_lbl.setStyleSheet(f"color:{theme.ACCENT}; font-size:11px; font-weight:bold; font-family:'{theme.MONO_FONT}';")
        # Right-click opens the info menu: dev builds also get Copy / Copy Data
        # (JSON) debug entries; user builds get just the clean drop-mob names.
        self._map_info_lbl.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self._map_info_lbl.customContextMenuRequested.connect(self._show_map_info_menu)
        self._last_codex_item: dict | None = None
        map_v.addWidget(self._map_info_lbl)
        return self._map_container

    def _clear_codex_map_selection(self):
        if hasattr(self, "_codex_map_widget"):
            self._codex_map_widget.set_multi_pins("", [])
        if hasattr(self, "_map_info_lbl"):
            self._map_info_lbl.setText("Left-click card to plot spawns")
        self._last_codex_item = None
        self._zone_pins_active = False

    def _drop_mob_names(self) -> list[str]:
        """Clean human names of the mobs that drop the last selected item.

        Reads the compiled `drops_from` markers (dicts carry id + name; plain
        strings are raw ids) and falls back to resolving single
        mob_id/drop_mob/mob_name fields through enemies_data for a display
        name — matching the resolver's mob-drop chain. Deduped, order kept.
        """
        item = self._last_codex_item
        if not item:
            return []
        names: list[str] = []
        seen: set[str] = set()
        df = item.get("drops_from")
        if isinstance(df, list):
            for d in df:
                if isinstance(d, dict):
                    nm = (d.get("name") or d.get("id") or "").strip()
                elif isinstance(d, str):
                    nm = d.strip()
                else:
                    nm = ""
                if nm and nm not in seen:
                    seen.add(nm)
                    names.append(nm)
        for key in ("mob_id", "drop_mob", "mob_name"):
            raw = item.get(key)
            if not raw or raw in seen:
                continue
            nm = raw
            info = codex.enemies_data().get(raw, {}) if isinstance(raw, str) else {}
            if info.get("name"):
                nm = info["name"]
            if nm not in seen:
                seen.add(nm)
                names.append(nm)
        return names

    def _achievement_lines(self) -> list[str]:
        """Display lines for the last selected item's achievement reward.

        Mirrors the card tooltip: 'Achievement: <name> (<category> · <N> pts)'
        plus the description, so the right-click menu shows the full info.
        """
        item = self._last_codex_item
        ach = item.get("achievement") if isinstance(item, dict) else None
        if not isinstance(ach, dict) or not ach.get("name"):
            return []
        meta = []
        if ach.get("category"):
            meta.append(str(ach["category"]))
        if ach.get("points") is not None:
            meta.append(f"{ach['points']} pts")
        meta_str = f" ({' · '.join(meta)})" if meta else ""
        lines = [f"Achievement: {ach['name']}{meta_str}"]
        if ach.get("desc"):
            lines.append(f"   {ach['desc']}")
        return lines

    def _show_map_info_menu(self, pos: QtCore.QPoint) -> None:
        """Right-click menu for the info label: the mobs that drop the item
        and its full achievement reward info.

        Dev builds additionally get Copy / Copy Data (JSON) debug entries;
        user (frozen) builds show only the clean drop-mob names and the
        achievement info, display-only with no copy actions. With neither a
        drop source nor an achievement a user build has nothing useful to
        offer, so no menu is shown.
        """
        dev = not is_frozen()
        drop_names = self._drop_mob_names()
        ach_lines = self._achievement_lines()
        if not dev and not drop_names and not ach_lines:
            return

        menu = QtWidgets.QMenu(self._map_info_lbl)
        txt = self._map_info_lbl.text()
        copy_line = None
        copy_json = None
        if dev:
            copy_line = menu.addAction("Copy")
            copy_line.setEnabled(bool(txt))
            copy_json = menu.addAction("Copy Data (JSON)")
            copy_json.setEnabled(self._last_codex_item is not None)

        # Mob-drop sources in clean human names (disabled actions are
        # display-only). Dev builds nest them under a submenu and offer a
        # copy action; user builds list them flat with a header and nothing
        # else.
        copy_drops: QtWidgets.QAction | None = None
        if drop_names:
            if dev:
                sub = menu.addMenu(f"Dropped By ({len(drop_names)} Mobs)")
                for nm in drop_names:
                    act = sub.addAction(nm)
                    act.setEnabled(False)
                copy_drops = menu.addAction("Copy Drop Mobs")
            else:
                header = menu.addAction(f"Dropped By ({len(drop_names)} Mobs)")
                header.setEnabled(False)
                for nm in drop_names:
                    act = menu.addAction(nm)
                    act.setEnabled(False)

        # Full achievement reward info (name, category · points, desc) —
        # display-only in both builds. Separator only when something precedes
        # it (an achievement-only menu shouldn't open with a stray line).
        if ach_lines:
            if menu.actions():
                menu.addSeparator()
            for line in ach_lines:
                act = menu.addAction(line)
                act.setEnabled(False)

        chosen = menu.exec(self._map_info_lbl.mapToGlobal(pos))
        if chosen is copy_line:
            QtWidgets.QApplication.clipboard().setText(txt)
        elif chosen is copy_json and self._last_codex_item is not None:
            QtWidgets.QApplication.clipboard().setText(
                json.dumps(self._last_codex_item, ensure_ascii=False, indent=2))
        elif chosen is copy_drops:
            QtWidgets.QApplication.clipboard().setText(", ".join(drop_names))

    def _find_item_coords(self, item: dict, uid: str) -> tuple[list[dict], str, bool]:
        """Resolve map coordinates, a display title, and rift-ness for a codex
        item/unit. Thin wrapper over the cached data-layer resolver so the card
        click and the "Plot Missing" pins button agree. Passes the card's own
        dungeon context so mobs shared between dungeons pin the dungeon that
        was actually clicked (enemies_data collapses them to one uid row).
        Copies the coord dicts so callers can never mutate the shared cache."""
        hint = item.get("dungeon_name") or item.get("type") or None
        coords, d_title, is_rift = codex.resolve_locations(uid, hint)
        return [dict(c) for c in coords], d_title, is_rift

    def _resolve_item_coords(self, item: dict, uid: str) -> list[dict]:
        """Spawn/entrance coords for an item (full resolution chain)."""
        coords, _title, _rift = self._find_item_coords(item, uid)
        return coords

    def _plot_all_visible_pins(self) -> None:
        """Plot spawn/entrance pins for all visible (unhidden) cards currently
        shown — or, on the Chests / Orbs views, the remaining chest/orb pins."""
        if self._codex_chest_orb_view_active():
            zone = getattr(self, "_dungeon_zone", "All") or "All"
            self._plot_remaining_chest_orbs(zone if zone != "All" else None)
            return
        current_rid, tab_label = self._codex_current_region()
        # Soulstones view: plot every summon spot (matches the button count).
        if getattr(self, "_soulstones_mode", False):
            self._plot_all_soulstone_pins()
            return
        # Dungeons-tab list view: there are no cards to pin, so plot every
        # dungeon entrance (matches the 'Dungeon Locations (N)' button count
        # and tooltip). The card-grid views fall through to the pin loop.
        if (current_rid == "Bosses" or tab_label == "Dungeons") \
                and (getattr(self, "_dungeon_list_mode", False) or not self._codex_cards):
            self._plot_all_dungeon_pins()
            return
        if not hasattr(self, "_codex_cards") or not self._codex_cards:
            return

        all_pins = []
        mob_count = 0

        for card in self._codex_cards:
            if not card.active:
                continue

            coords = self._resolve_item_coords(card.data, card.uid)
            if not coords:
                continue

            item = card.data
            is_dung = bool(item.get("is_dungeon") or item.get("is_rift"))
            # Same color rules as the single-click map (map.py)
            if item.get("vendor_npc") or item.get("vendor_npcs"):
                m_col = QtGui.QColor(theme.ACCENT)
            elif item.get("chest_loc") or item.get("chest_locs"):
                m_col = QtGui.QColor(theme.GOLD)
            elif is_dung:
                m_col = QtGui.QColor(theme.KIND_COLOR.get("dungeon", "#7c3aed"))
            elif item.get("is_boss"):
                m_col = QtGui.QColor(theme.GOLD)
            elif item.get("is_critter") or item.get("type") in ("Companion", "Pet"):
                m_col = QtGui.QColor(theme.KIND_COLOR.get("companion", "#7aa2f7"))
            else:
                m_col = QtGui.QColor(theme.DANGER)

            mob_count += 1
            for c in coords:
                all_pins.append({
                    "x": c.get("x", 0),
                    "y": c.get("y", 0),
                    "color": m_col,
                    "name": item.get("name") or card.uid,
                    "is_dungeon": is_dung
                })

        if hasattr(self, "_codex_map_widget"):
            self._codex_map_widget.set_multi_pins("All Visible Mobs", all_pins)

        if hasattr(self, "_map_info_lbl"):
            self._map_info_lbl.setText(f"Plotted {len(all_pins)} Pins ({mob_count} Mobs)" if all_pins else "No visible pins to plot")

    def _on_codex_mob_selected(self, uid: str, item: dict):
        mname = item.get("name") or uid
        coords, d_title, is_rift = self._find_item_coords(item, uid)
        self._last_codex_item = item  # right-click menu can dump the full data dict

        # Update Right-Side Zone Spawn Map canvas
        if hasattr(self, "_codex_map_widget"):
            self._codex_map_widget.set_selected_mob(mname, coords, item=item)

        # Mounts/gliders that drop from mobs read as a drop count (unique
        # mobs) instead of a spawn-location count.
        drops_from = item.get("drops_from")
        mob_ids = ({d.get("id") for d in drops_from
                    if isinstance(d, dict) and d.get("id")}
                   if isinstance(drops_from, list) and drops_from else set())

        # Achievement reward rides along as a dict (name/desc/category/points)
        # on items that are BOTH a mob drop and an achievement reward (e.g. the
        # region-bestiary mounts) — appended below so the info line names both
        # sources.
        ach = item.get("achievement")
        ach_line = ""
        if isinstance(ach, dict) and ach.get("name"):
            ach_line = f" — Achievement: {ach['name']}"

        # Human name only — the uid stays available via the right-click Copy
        # Data (JSON) dev menu and the card tooltip, so the label reads
        # cleanly without the raw id.
        if mob_ids:
            label = f"{mname} (Dropped by {len(mob_ids)} Mobs)"
        elif coords:
            if d_title:
                label = f"{mname} — {d_title}"
            elif is_rift:
                label = f"{mname} ({len(coords)} Rift Entrances)"
            elif item.get("is_dungeon"):
                dname = item.get("dungeon_name") or "Dungeon Entrance"
                label = f"{mname} ({dname})"
            else:
                unit_label = "Location" if len(coords) == 1 else "Spawns"
                label = f"{mname} ({len(coords)} {unit_label})"
        elif d_title:
            # Sourceless items with a title (e.g. achievement rewards:
            # 'Achievement · Bestiary of Skover Island') show it here instead
            # of a blank 'No Map Locations'.
            label = f"{mname} — {d_title}"
        else:
            label = f"{mname} (No Map Locations)"

        # Only append when the label doesn't already mention the achievement
        # (pure achievement rewards resolve their title to
        # 'Achievement · <name>', so they'd double up).
        if ach_line and "Achievement" not in label:
            label += ach_line

        if hasattr(self, "_map_info_lbl"):
            self._map_info_lbl.setText(label)

    # --- jump into the Codex (the source-row / mount-glider links on the
    # Items/Craft pages) ---
    def _codex_jump_to_unit(self, unit_id: str, name: str) -> None:
        """Open the Codex entry for a drop source / item — the source-row and
        mount/glider links on the Items/Craft pages. Routes by what the
        target is: a boss -> the Dungeons tab's dungeon list (scrolls +
        clicks its row); a mount/glider or other unit with a card -> that
        card in the Collection / zone / Others grid; a single chest -> its
        row in the Collection Chests list."""
        self._select_nav("codex")
        if not hasattr(self, "_codex_tabs"):
            return
        self._codex_reset_jump_filters()
        from ....data import codex as cdx
        # Soulstone bosses have no card — reveal their spot in the Soulstones list.
        for p in cdx.soulstone_pois():
            if p.get("spawn_unit") == unit_id:
                self._codex_open_soulstone(p.get("id") or "")
                return
        rid = cdx.find_unit_region(unit_id)
        if rid is None:
            # a single chest (no unit card) -> the Collection Chests list
            self._codex_open_chest(unit_id)
            return
        if rid == "Bosses":
            # bosses keep the dungeon-list reveal
            self._codex_jump_to_boss(unit_id, name)
            return
        self._codex_open_card(rid, unit_id, name)

    def _codex_reset_jump_filters(self) -> None:
        """Clear every codex filter that could hide the jump target: the
        search query (and its debounce), the spark/dungeon toggles, the
        source-badge keys and the status filter, and drop any plotted pins
        from the previous view."""
        if hasattr(self, "_search_timer"):
            self._search_timer.stop()
        if hasattr(self, "_codex_search") and self._codex_search.text():
            self._codex_search.blockSignals(True)
            self._codex_search.setText("")
            self._codex_search.blockSignals(False)
        if hasattr(self, "_status_widgets") and "All" in self._status_widgets:
            self._status_widgets["All"].setChecked(True)
        for attr, btn_name in (("_spark_only", "_spark_filter_btn"),
                               ("_dungeon_only", "_dungeon_filter_btn")):
            if hasattr(self, attr):
                setattr(self, attr, False)
            btn = getattr(self, btn_name, None)
            if btn is not None and btn.isChecked():
                btn.blockSignals(True)
                btn.setChecked(False)
                btn.blockSignals(False)
        if getattr(self, "_source_filters", None):
            self._source_filters = set()
            for btn in getattr(self, "_source_filter_btns", {}).values():
                if btn.isChecked():
                    btn.blockSignals(True)
                    btn.setChecked(False)
                    btn.blockSignals(False)
        # Collection species-family filter (the secondary Pets/Mounts/Gliders
        # sub-menu): reset to All so the jump target can't be hidden.
        if getattr(self, "_codex_group_filter", "All") != "All":
            self._codex_group_filter = "All"
            all_btn = getattr(self, "_group_sub_btns", {}).get("All")
            if all_btn is not None:
                all_btn.blockSignals(True)
                all_btn.setChecked(True)  # exclusive group unchecks the rest
                all_btn.blockSignals(False)
        self._clear_codex_map_selection()

    def _codex_jump_to_boss(self, unit_id: str, name: str) -> None:
        """Jump the Codex tab to a boss. Lands on the Dungeons tab's dungeon
        list, scrolls the boss's row into view, and clicks it like a real
        user (tracks the entrance + plots it on the map)."""
        # Dungeons tab, dungeon-list view, all zones
        self._codex_tabs.setCurrentText("Dungeons")
        if hasattr(self, "_btn_dungeon_list"):
            self._dungeon_list_mode = True
            self._btn_dungeon_list.setChecked(True)
            self._btn_dungeon_mobs.setChecked(False)
        if hasattr(self, "_zone_widgets") and "All" in self._zone_widgets:
            self._zone_widgets["All"].setChecked(True)
        self._refresh_codex_grid(reset_scroll=True)
        # one tick for the rebuilt list's layout to settle before scrolling
        QtCore.QTimer.singleShot(
            0, lambda: self._codex_click_unit(unit_id, name))

    def _codex_open_card(self, rid: str, unit_id: str, name: str) -> None:
        """Switch the Codex to the region holding a unit's card and click it
        like a real user would. Mounts/gliders land on the Collection tab's
        Mounts/Gliders sub-view; pets on its Pets sub-view; everything else
        on its region tab (Z1/Z2/Z3 or the Others catch-all)."""
        from ....data import codex as cdx
        info = cdx.enemies_data().get(unit_id) or {}
        itype = (info.get("type") or "").lower()
        is_mount = "mount" in itype or unit_id.startswith("Mount_")
        is_glider = "glider" in itype or unit_id.startswith("Glider_")
        # the card grid is the target view — never the dungeon list
        if hasattr(self, "_dungeon_list_mode"):
            self._dungeon_list_mode = False
        if rid == "Pets" or is_mount or is_glider:
            # mounts/gliders (and pets) live on the Collection tab
            self._codex_tabs.setCurrentText("Collection")
            sub = "Mounts" if is_mount else "Gliders" if is_glider else "Pets"
            btn = self._collection_sub_btns.get(sub)
            if btn is not None:
                btn.click()
        else:
            tab = cdx.region_names().get(rid)
            if tab and hasattr(self, "_codex_tabs"):
                self._codex_tabs.setCurrentText(tab)
        self._refresh_codex_grid(reset_scroll=True)
        QtCore.QTimer.singleShot(
            0, lambda: self._codex_click_card(unit_id, name))

    def _codex_click_card(self, unit_id: str, name: str) -> None:
        """Click a codex card like a real user would: scroll it into view
        and emit its selection (plots spawns + updates the info line).
        No-op when the card isn't rendered (filtered out / not in view)."""
        cards = getattr(self, "_codex_cards", None) or []
        card = next((c for c in cards if c.uid == unit_id), None)
        if card is None:
            return
        self._codex_scroll.ensureWidgetVisible(card, 0, 60)
        card.selected.emit(card.uid)

    def _codex_open_chest(self, chest_id: str) -> None:
        """Reveal a chest in the Codex Collection Chests list: switch to the
        Collection tab's Chests view (zone filter All) and scroll to the
        chest's row, clicking it so it plots on the map like a manual click."""
        self._codex_tabs.setCurrentText("Collection")
        btn = self._collection_sub_btns.get("Chests")
        if btn is not None:
            btn.click()
        if hasattr(self, "_zone_widgets") and "All" in self._zone_widgets:
            self._zone_widgets["All"].setChecked(True)
        self._refresh_codex_grid(reset_scroll=True)
        QtCore.QTimer.singleShot(
            0, lambda: self._codex_click_chest(chest_id))

    def _codex_click_chest(self, chest_id: str) -> None:
        """Scroll to a chest's row in the open Chests list and click it like
        a real user (tracks it + plots the spot on the map). No-op when the
        chest isn't listed (already collected)."""
        view = getattr(self, "_codex_chest_list", None)
        if view is None:
            return
        cell = next((c for c in view._cells
                     if c.item.get("id") == chest_id), None)
        if cell is None:
            return
        self._codex_scroll.ensureWidgetVisible(cell, 0, 60)
        cell.pick.emit(cell.item)

    def _codex_click_unit(self, unit_id: str, name: str) -> None:
        """Click the dungeon-list row for `unit_id` like a real user would:
        scroll it into view and emit its pick signal (tracks the entrance
        + plots the map). No-op when the boss has no dungeon row."""
        view = getattr(self, "_codex_dungeon_list", None)
        if view is None or not getattr(self, "_dungeon_list_mode", False):
            return
        row = next((c for c in view._cells
                    if c.dungeon.get("boss_id") == unit_id
                    or c.dungeon.get("boss_name") == name), None)
        if row is None:
            # not a dungeon-list row (a world mob / pet shared with a
            # dungeon) — fall back to the card grid and click the card
            if hasattr(self, "_btn_dungeon_mobs"):
                self._dungeon_list_mode = False
                self._btn_dungeon_mobs.setChecked(True)
                self._btn_dungeon_list.setChecked(False)
                self._refresh_codex_grid(reset_scroll=True)
                QtCore.QTimer.singleShot(
                    0, lambda: self._codex_click_card(unit_id, name))
            return
        # the grid defers layout + the body resize (scrollbar range 0) to
        # the next event pass — force both so the scroll lands now
        body = self._codex_scroll.widget()
        if body is not None and body.layout() is not None:
            body.layout().activate()
            if view.layout() is not None:
                view.layout().activate()
            sh = body.layout().sizeHint()
            if body.height() < sh.height():
                body.resize(body.width(), sh.height())
        self._codex_scroll.ensureWidgetVisible(row, 0, 60)
        row.pick.emit(row.dungeon)

    def _update_plot_pins_button(self, current_rid: str, tab_label: str) -> None:
        """Retitle/hide the 'Missing' / 'Dungeon Locations' pins button."""
        if not hasattr(self, "_btn_plot_all_pins"):
            return
        is_dungeon_tab = current_rid == "Bosses" or tab_label == "Dungeons"
        # The Chests/Orbs remaining lists moved to the Collection tab, so the
        # pins button retitles there too (all other Collection sub-views keep
        # the plain 'Missing (N)' button).
        is_list_tab = is_dungeon_tab or ((current_rid in ("Pets", "Z0") or tab_label == "Collection") and self._codex_chest_orb_view_active())
        if is_list_tab:
            if self._codex_chest_orb_view_active():
                from .chest_orb_list import remaining_items
                kind = self._codex_chest_orb_kind()
                kinds = {"chest"} if kind == "chests" else {"orb"}
                zone = getattr(self, "_dungeon_zone", "All") or "All"
                items = [it for it in remaining_items(self, kinds) if zone == "All" or it["zone"] == zone]
                n = len(items)
                label = "Chests" if kind == "chests" else "Orbs"
                if n == 0:
                    self._btn_plot_all_pins.setVisible(False)
                else:
                    self._btn_plot_all_pins.setVisible(True)
                    self._btn_plot_all_pins.setText(f"{label} ({n})")
                    self._btn_plot_all_pins.setToolTip(f"Plot remaining {label.lower()}")
                return
            elif getattr(self, "_soulstones_mode", False):
                n = len(codex.soulstone_pois())
                self._btn_plot_all_pins.setVisible(True)
                self._btn_plot_all_pins.setText(f"Soulstones ({n})")
                self._btn_plot_all_pins.setToolTip("Plot every soulstone summon spot")
                return
            elif getattr(self, "_dungeon_list_mode", False) or not self._codex_cards:
                # List view replaces the cards, so count dungeons from the data
                from ....data import dungeons as dg
                location_count = len(dg.load_dungeons())
            else:
                unique_dungeons = {c.data.get("type") for c in self._codex_cards if c.active and c.data.get("type")}
                location_count = len(unique_dungeons)
            if location_count == 0:
                self._btn_plot_all_pins.setVisible(False)
            else:
                self._btn_plot_all_pins.setVisible(True)
                self._btn_plot_all_pins.setText(f"Dungeon Locations ({location_count})")
                self._btn_plot_all_pins.setToolTip("Plot entrance pins for all dungeons and rifts")
        else:
            active_cards_count = len([c for c in self._codex_cards if c.active])
            if active_cards_count == 0:
                self._btn_plot_all_pins.setVisible(False)
            else:
                self._btn_plot_all_pins.setVisible(True)
                self._btn_plot_all_pins.setText(f"Missing ({active_cards_count})")
                self._btn_plot_all_pins.setToolTip("Plot spawn pins for all missing/uncollected mobs")

    # --- dungeon list (inline tracking section) -------------------------
    def _dungeon_track_key(self, d: dict) -> tuple[str, str] | None:
        """(kind, tracker key) for a dungeon's entrance, or None when the
        entrance can't be resolved.

        Reuses the codex entrance resolver (the exact coords the map pins
        plot) and fills z + the POI id from the matching entrance POI, so the
        key is identical in shape to a minimap click on the same marker:
        "x,y,z|label|poi_id". The label is the clean dungeon name so the
        Entity HUD's waypoint row resolves boss/level/dungeon info via
        `dungeons.get_dungeon_info`."""
        from ....geo import pois as geo_pois
        dname = d.get("name") or ""
        boss_id = d.get("boss_id")
        is_rift = d.get("entrance_zone") == "Rifts"
        if is_rift and dname and not dname.lower().startswith("rift"):
            dname = f"Rift {dname}"
        coords = ()
        if boss_id:
            try:
                coords, _title, _rift = codex.resolve_locations(boss_id, d.get("name") or None)
            except Exception:
                coords = ()
        if not coords:
            return None
        x, y = float(coords[0]["x"]), float(coords[0]["y"])
        z, pid = 0.0, d.get("id") or ""
        for p in geo_pois.load_pois():
            if p.sub_kind not in ("dungeon", "rift"):
                continue
            if abs(p.x - x) < 1.5 and abs(p.y - y) < 1.5:
                z, pid = float(p.z), p.id or pid
                break
        kind = "rift" if is_rift else "dungeon"
        return kind, f"{x:.1f},{y:.1f},{z:.1f}|{dname}|{pid}"

    def _rift_live_track_key(self) -> tuple[str, str] | None:
        """(kind, tracker key) for the ACTIVE rift — or the next one due — via
        the RiftTracker, exactly like the Entity HUD's rift row: the
        announced/predicted rift POI with the subzone label. Works attached
        (live scene + announcements) or detached (hourly LCG prediction).
        None when no spot can be resolved."""
        model = getattr(self, "model", None)
        if model is not None:
            # Attached: use the model's stateful RiftTracker (it carries the
            # once-per-entry announcement-clear hysteresis the HUD relies on).
            try:
                rst = model.rift_status()
            except Exception:
                return None
        else:
            # Detached: pure hourly LCG prediction, still gives the next rift.
            from ....core.rift_tracker import RiftTracker
            try:
                rst = RiftTracker(None).get_status()
            except Exception:
                return None
        if rst is None or rst.poi_x is None or rst.poi_y is None:
            return None
        px, py, pz = rst.poi_x, rst.poi_y, rst.poi_z or 0.0
        label = getattr(rst, "subzone", None) or rst.rift_name or "Rift"
        sid = f"s{px:.1f},{py:.1f}"
        return "rift", f"{px:.1f},{py:.1f},{pz:.1f}|{label}|{sid}"

    def _is_dungeon_tracked(self, d: dict) -> bool:
        """True when the current tracker target is this dungeon's entrance —
        or, for rifts, the active/next-due rift (which lives at a different
        spot than the static gate)."""
        tk, tid = self.s.track_kind, self.s.track_id
        if not tid:
            return False
        if d.get("entrance_zone") == "Rifts":
            if tk != "rift":
                return False
            live = self._rift_live_track_key()
            if live and live[1].split("|")[0] == tid.split("|")[0]:
                return True
            # Also match a static rift gate tracked from the minimap/HUD
            key = self._dungeon_track_key(d)
            return bool(key) and tid.split("|")[-1] == key[1].split("|")[-1]
        if tk != "dungeon":
            return False
        key = self._dungeon_track_key(d)
        return bool(key) and tid.split("|")[0] == key[1].split("|")[0] \
            and tid.split("|")[-1] == key[1].split("|")[-1]

    # --- Chests & Orbs list (inline remaining-tracker section) -----------
    def _chest_track_key(self, item: dict) -> str:
        """Tracker waypoint key for a chest item ('x,y,z|label|id'), matching
        the minimap marker key shape so is_tracked() resolves it."""
        return (f"{item['x']:.1f},{item['y']:.1f},{item['z']:.1f}"
                f"|{item['label']}|{item['id']}")

    def _is_chest_orb_tracked(self, item: dict) -> bool:
        """True when the tracker target is this orb/chest row."""
        tr = getattr(getattr(self, "overlay_mgr", None), "tracker", None)
        if tr is None:
            return False
        if item["kind"] == "orb":
            return tr.is_tracked("orb", item["id"])
        return tr.is_tracked("chest", self._chest_track_key(item))

    def _track_chest_orb(self, item: dict) -> None:
        """Track a chest/orb row exactly like clicking its minimap marker
        (compass needle + Entity HUD waypoint row), and plot the same spot as
        a pin on the codex map. Click again to stop."""
        tr = getattr(getattr(self, "overlay_mgr", None), "tracker", None)
        if tr is None:
            return
        if item["kind"] == "orb":
            tr.toggle("orb", item["id"])
        else:
            tr.toggle("chest", self._chest_track_key(item))
        if hasattr(self, "_codex_map_widget"):
            color = ORB_PIN_COLOR if item["kind"] == "orb" else theme.CHEST
            self._codex_map_widget.set_multi_pins(item["label"], [{
                "x": item["x"], "y": item["y"],
                "color": QtGui.QColor(color),
                "name": item["label"],
                "is_chest": item["kind"] == "chest",
            }])
        if hasattr(self, "_map_info_lbl"):
            self._map_info_lbl.setText(item["label"])

    def _mark_chest_orb_done(self, item: dict) -> None:
        """Right-click on a Chests/Orbs row: record it as collected in the
        attached profile so it drops out of the remaining list (and is hidden
        on the minimap when hide-collected is on).  Any plotted zone pins are
        cleared and the list re-renders."""
        profile = self.model.player_profile() if (hasattr(self, "model") and self.model) else None
        self.s.toggle_done(item["id"], profile)
        tr = getattr(getattr(self, "overlay_mgr", None), "tracker", None)
        if tr is not None:
            if item["kind"] == "orb":
                if tr.is_tracked("orb", item["id"]):
                    tr.clear()
            elif tr.is_tracked("chest", self._chest_track_key(item)):
                tr.clear()
        if hasattr(self, "_zone_pins_active"):
            self._zone_pins_active = False
        if hasattr(self, "_codex_map_widget"):
            self._codex_map_widget.set_multi_pins("", [])
        if hasattr(self, "_map_info_lbl"):
            self._map_info_lbl.setText(f"Collected: {item['label']}")
        # Undo chip: show the Last Hidden frame so a mistaken right-click can
        # be reverted (click the chip to un-collect).
        if hasattr(self, "_last_hidden_chest_orb"):
            self._last_hidden_chest_orb = (item["id"], item["label"], item["kind"])
        if hasattr(self, "_update_last_hidden_widget"):
            self._update_last_hidden_widget()
        self._refresh_codex_grid()

    def _plot_remaining_chest_orbs(self, zone: str | None) -> None:
        """Plot pins for the remaining chests + secret orbs (all zones, or a
        single zone when `zone` is given) — used by the Chests / Orbs views'
        zone keys and their pins button. Only the active kind is plotted."""
        self._zone_pins_active = True
        if not hasattr(self, "_codex_map_widget"):
            return
        from .chest_orb_list import remaining_items
        kind = self._codex_chest_orb_kind()
        kinds = {"chest"} if kind == "chests" else {"orb"} if kind == "orbs" else None
        pins = []
        for it in remaining_items(self, kinds):
            if zone and it["zone"] != zone:
                continue
            color = ORB_PIN_COLOR if it["kind"] == "orb" else theme.CHEST
            pins.append({
                "x": it["x"], "y": it["y"],
                "color": QtGui.QColor(color),
                "name": it["label"],
                "is_chest": it["kind"] == "chest",
            })
        label = f"{zone} Remaining" if zone else "Remaining Chests & Orbs"
        self._codex_map_widget.set_multi_pins(label, pins)
        if hasattr(self, "_map_info_lbl"):
            self._map_info_lbl.setText(f"{len(pins)} remaining plotted")

    def _track_dungeon(self, d: dict) -> None:
        """Track a dungeon's entrance exactly like clicking its minimap marker
        (compass needle + Entity HUD WAYPOINT row), and plot the same spot as
        a pin on the codex map. Click again to stop — the pin clears too.
        Rifts track the ACTIVE / next-due rift via the RiftTracker instead of
        the static gate, falling back to the gate if no rift spot resolves."""
        result = None
        if d.get("entrance_zone") == "Rifts":
            result = self._rift_live_track_key()
        if not result:
            result = self._dungeon_track_key(d)
        if not result:
            return
        kind, key = result
        tr = getattr(getattr(self, "overlay_mgr", None), "tracker", None)
        if tr is None:
            return
        # The list view (when open) refreshes its highlight via tracker.changed.
        tr.toggle(kind, key)
        # Plot the tracked spot as a pin — or clear it when this click untracked.
        if tr.is_tracked(kind, key):
            self._plot_dungeon_on_map(d, kind, key)
        else:
            self._clear_codex_map_selection()

    def _plot_dungeon_on_map(self, d: dict, kind: str, key: str) -> None:
        """Plot the tracked dungeon/rift spot on the codex map (same coords
        as the waypoint — the entrance for dungeons, the live/next-due rift
        for rifts), so the list row does both: track + show on the map."""
        if not hasattr(self, "_codex_map_widget"):
            return
        try:
            xyz, spot, _pid = key.split("|")
            x, y, z = (float(v) for v in xyz.split(","))
        except (ValueError, AttributeError):
            return
        mname = d.get("boss_name") or d.get("name") or "Dungeon"
        coords = [{"x": x, "y": y, "z": z}]
        item = {"is_dungeon": True, "is_rift": kind == "rift"}
        self._codex_map_widget.set_selected_mob(mname, coords, item=item)
        if hasattr(self, "_map_info_lbl"):
            self._map_info_lbl.setText(f"{mname} — {spot}")
