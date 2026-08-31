"""Codex grid pipeline: rebuild, filter, sort, render, and chrome helpers."""
from __future__ import annotations

from PySide6 import QtCore, QtWidgets
from shiboken6 import isValid

from ... import components as C
from ... import theme
from ....data import codex
from .card import CodexUnitCard


class CodexGridMixin:
    def _refresh_codex_grid(self, reset_scroll: bool = False) -> None:
        """Re-build the grid based on active tab, search query, and status filter."""
        # the Codex page may have been evicted while the search debounce fired
        if not hasattr(self, "_codex_grid") or not isValid(self._codex_grid):
            return

        scroll_val = self._codex_scroll.verticalScrollBar().value() if (hasattr(self, "_codex_scroll") and self._codex_scroll) else 0
        old_height = self._codex_scroll.widget().height() if (hasattr(self, "_codex_scroll") and self._codex_scroll and self._codex_scroll.widget()) else 0
        if old_height > 0 and not reset_scroll:
            self._codex_scroll.widget().setMinimumHeight(old_height)

        current_rid, tab_label = self._codex_current_region()
        # Nearby only filters real world entities, so on the Collection tab it
        # only applies to the Pets sub-view. Sync the status key BEFORE the
        # filter state is read so a stale Nearby selection can never silently
        # empty the grid on Mounts/Gliders.
        self._sync_nearby_status_key(current_rid, tab_label)
        query, status_filter = self._codex_filter_state()
        is_global = self._codex_is_global(query, status_filter, tab_label)

        # Performance: disable updates during bulk grid rebuild
        self._codex_grid.parentWidget().setUpdatesEnabled(False)
        try:
            self._clear_codex_grid()
            self._update_codex_chrome(current_rid, tab_label, query, status_filter, is_global)
            regions_to_show = self._codex_regions_to_show(current_rid, tab_label, is_global)

            profile = self.model.player_profile() if hasattr(self, "model") and self.model else None
            hidden_units = set(self.s.get_entity_hidden_units(profile))
            hidden_comps = set(self.s.get_companion_hidden_units(profile)) if hasattr(self.s, "get_companion_hidden_units") else set()
            self._update_last_hidden_widget()

            nearby_info = self._codex_nearby_info() if status_filter == "NEARBY" else {}
            dungeon_tab = current_rid == "Bosses"
            collection_tab = current_rid == "Pets" or tab_label == "Collection"

            grid_row = 0
            total_shown = 0
            if getattr(self, "_chest_orb_kind", None) and (dungeon_tab or collection_tab):
                # 'Chests' / 'Orbs' view active: remaining uncollected items
                grid_row, total_shown = self._render_chest_orb_list(grid_row)
            elif getattr(self, "_soulstones_mode", False) and dungeon_tab:
                grid_row, total_shown = self._render_soulstone_list(grid_row)
            elif getattr(self, "_dungeon_list_mode", False) and dungeon_tab:
                # 'Dungeon List' view active: render the list instead of cards
                grid_row, total_shown = self._render_dungeon_list(grid_row)
            else:
                if collection_tab and not is_global:
                    # Collection sub-view: pets, mounts, gliders, or all three
                    regions_to_show = self._codex_collection_regions()
                for rid, rname in regions_to_show.items():
                    items = codex.units_by_region(rid, ingame_order=True)
                    matches = self._codex_item_matches(rid, items, query, status_filter,
                                                       nearby_info, hidden_units, hidden_comps,
                                                       dungeon_tab, collection_tab)
                    if not matches:
                        continue
                    self._sort_codex_matches(matches, rid, status_filter, query, collection_tab)
                    grid_row = self._render_codex_region(rid, rname, matches, grid_row,
                                                         is_global, status_filter, current_rid,
                                                         collection_tab)
                    total_shown += len(matches)

            self._codex_grid.setRowStretch(grid_row, 1)
            self._update_codex_count(total_shown, is_global)
            self._update_plot_pins_button(current_rid, tab_label)
            self._update_bulk_buttons_state()
        finally:
            self._codex_grid.parentWidget().setUpdatesEnabled(True)
            if hasattr(self, "_codex_scroll") and self._codex_scroll:
                if self._codex_scroll.widget():
                    self._codex_scroll.widget().setMinimumHeight(0)
                sb = self._codex_scroll.verticalScrollBar()
                if reset_scroll:
                    sb.setValue(0)
                else:
                    sb.setValue(scroll_val)
                    QtCore.QTimer.singleShot(0, lambda val=scroll_val: sb.setValue(val))

    def _codex_filter_state(self) -> tuple[str, str]:
        """(lowercased search query, status filter label) from the current controls."""
        query = self._codex_search.text().strip().lower() if hasattr(self, "_codex_search") else ""
        status_filter = "ALL"
        if hasattr(self, "_status_btns"):
            checked = self._status_btns.checkedButton()
            if checked:
                # The Collection tab styles the status labels with counts
                # ('Visible (51)'), so the filter key lives in a property.
                key = checked.property("statusKey")
                status_filter = (key if key else checked.text()).upper()
        return query, status_filter

    def _codex_is_global(self, query: str, status_filter: str, tab_label: str) -> bool:
        """True when the grid spans all regions (search or Nearby filter);
        collection-style tabs always stay local."""
        is_global = bool(query) or status_filter == "NEARBY"
        if tab_label.lower() in ("pets", "collection", "mounts", "gliders", "dungeons", "others", "other"):
            is_global = False
        return is_global

    def _clear_codex_grid(self) -> None:
        """Drop every widget from the grid and reset row stretches."""
        while self._codex_grid.count():
            item = self._codex_grid.takeAt(0)
            if item.widget():
                # Hide before deleteLater: removed widgets stay painted at
                # their old spot until the deferred delete runs, so an
                # unfiltered key click would briefly flash the stale cards
                # under/around the rebuilt grid.
                item.widget().setVisible(False)
                item.widget().deleteLater()
        self._codex_cards.clear()
        for r in range(self._codex_grid.rowCount()):
            self._codex_grid.setRowStretch(r, 0)
        for c in range(self._codex_grid.columnCount()):
            self._codex_grid.setColumnStretch(c, 0)

    def _codex_regions_to_show(self, current_rid: str, tab_label: str, is_global: bool) -> dict[str, str]:
        """{region_id: label} rendered by this refresh (all regions when global)."""
        all_regions = codex.region_names()
        if is_global:
            regions_to_show = {current_rid: all_regions[current_rid]}
            for rid, name in all_regions.items():
                if rid != current_rid:
                    regions_to_show[rid] = name
        else:
            regions_to_show = {current_rid: tab_label}
        return regions_to_show

    def _update_codex_chrome(self, current_rid: str, tab_label: str, query: str,
                             status_filter: str, is_global: bool) -> None:
        """Sync compact button, search placeholder, sub-tabs, legends, and bulk
        buttons to the current tab/filter state."""
        if hasattr(self, "_update_codex_profile_label"):
            self._update_codex_profile_label()

        # Compact button reflects the correct per-tab setting
        compact_mobs = self.s.codex_compact if hasattr(self.s, "codex_compact") else False
        compact_pets = self.s.codex_pets_compact if hasattr(self.s, "codex_pets_compact") else True
        is_compact = compact_pets if self._codex_is_collection_view(current_rid, tab_label) else compact_mobs
        if hasattr(self, "_btn_compact"):
            self._btn_compact.blockSignals(True)
            self._btn_compact.setChecked(is_compact)
            self._btn_compact.blockSignals(False)

        if hasattr(self, "_codex_search"):
            if tab_label == "Collection":
                self._codex_search.setPlaceholderText("Search collection…")
            elif tab_label == "Dungeons":
                self._codex_search.setPlaceholderText("Search dungeons…")
            else:
                self._codex_search.setPlaceholderText("Search Codex…")

        # Others sub-tabs (All/Shop/Unreleased/TODO). Defaults to All on
        # entry; the button is built checked, so nothing to force here.
        is_others_tab = current_rid == "Z0" or tab_label in ("Others", "Other")
        if hasattr(self, "_others_sub_bar"):
            self._others_sub_bar.setVisible(is_others_tab)
            self._was_others_tab = is_others_tab

        # Dungeons-tab view switcher: the list is the default view; 'Dungeon
        # Mobs' switches to the old card grid, 'Soulstones' to the summon-spot
        # list. Hidden on every other tab, and reset to the list default when
        # the tab is left.
        is_dungeon_tab = current_rid == "Bosses" or tab_label == "Dungeons"
        if hasattr(self, "_btn_dungeon_list"):
            view_btns = (self._btn_dungeon_list, self._btn_dungeon_mobs,
                         self._btn_dungeon_soulstones)
            for b in view_btns:
                b.blockSignals(True)
                b.setVisible(is_dungeon_tab)
            if not is_dungeon_tab:
                self._dungeon_list_mode = True  # default on next visit
                self._soulstones_mode = False
                self._btn_dungeon_list.setChecked(True)
                self._btn_dungeon_mobs.setChecked(False)
                self._btn_dungeon_soulstones.setChecked(False)
            else:
                self._btn_dungeon_list.setChecked(self._dungeon_list_mode)
                self._btn_dungeon_mobs.setChecked(
                    not self._dungeon_list_mode and not self._soulstones_mode)
                self._btn_dungeon_soulstones.setChecked(self._soulstones_mode)
            for b in view_btns:
                b.blockSignals(False)

        # Collection sub-tabs: Pets/Mounts/Gliders/Chests/Orbs. The checked
        # button mirrors the active sub-view; leaving the tab resets to Pets
        # (and drops the chest/orb mode) so nothing leaks across tabs.
        is_collection_tab = current_rid == "Pets" or tab_label == "Collection"
        if hasattr(self, "_collection_sub_bar"):
            self._collection_sub_bar.setVisible(is_collection_tab)
            if is_collection_tab:
                target = self._codex_collection_sub_tag()
                for lbl, btn in self._collection_sub_btns.items():
                    btn.blockSignals(True)
                    btn.setChecked(lbl.lower() == target)
                    btn.blockSignals(False)
            else:
                self._collection_sub = "pets"
                self._chest_orb_kind = None

        # Collection species-group sub-menu (the second sub-bar per unit
        # type) — owned by the grouping mixin, synced on every refresh (the
        # status filter feeds the chip counts for Visible/Hidden). The shared
        # status chips pick up their visible/hidden totals on the same views.
        self._sync_codex_group_bar(current_rid, tab_label, status_filter)
        self._update_codex_status_chips(current_rid, tab_label)

        # 'Grid / Outline' compact toggle only matters for the card grid, so
        # hide it only while a click-to-track list view is active (Dungeon
        # List, Soulstones, Chests/Orbs remaining lists). _dungeon_list_mode
        # is a Dungeons-tab concept that defaults True, so it must be gated by
        # is_dungeon_tab — otherwise the Collection card views (Pets /
        # Mounts / Gliders) would hide the toggle too and the outline look
        # would be unreachable there.
        if hasattr(self, "_btn_compact"):
            list_view_active = ((is_dungeon_tab and (getattr(self, "_dungeon_list_mode", True)
                                                     or getattr(self, "_soulstones_mode", False)))
                                or getattr(self, "_chest_orb_kind", None) is not None)
            self._btn_compact.setVisible(not list_view_active)

        # Legends only on open-world zone tabs; reset filters when leaving
        is_special = current_rid in ("Pets", "Mounts", "Gliders", "Bosses", "Z0") or tab_label in ("Pets", "Collection", "Mounts", "Gliders", "Dungeons", "Others", "Other")
        show_legends = not is_special
        if hasattr(self, "_dungeon_filter_btn"):
            self._dungeon_filter_btn.setVisible(show_legends)
        if hasattr(self, "_spark_filter_btn"):
            self._spark_filter_btn.setVisible(show_legends)
            if not show_legends:
                if self._spark_filter_btn.isChecked():
                    self._spark_filter_btn.setChecked(False)
                if self._dungeon_filter_btn.isChecked():
                    self._dungeon_filter_btn.setChecked(False)

        # Source-badge filter keys (Vendor / Achievement / Dungeon / Chest /
        # Shop / Mob Drop) on the collection-view tabs (Pets / Others) — the
        # zone tabs show the spark/dungeon filter toggles instead, so the two
        # legend sets are mutually exclusive. Keys that can't match anything on
        # the current tab are disabled in place (dimmed, unclickable). Reset the
        # source filters when leaving so they never leak into zone/dungeon views.
        # Source-badge keys on the card-grid views (Collection pets/mounts/
        # gliders + Others); the Chests/Orbs list views don't use the key
        # system — they get the dungeon zone block instead.
        show_source_keys = (current_rid in ("Pets", "Z0")
                            or tab_label in ("Pets", "Collection", "Others", "Other")) \
            and not (is_collection_tab and getattr(self, "_chest_orb_kind", None))
        if hasattr(self, "_source_legend_frame"):
            self._source_legend_frame.setVisible(show_source_keys)
            if show_source_keys:
                self._sync_source_key_visibility(current_rid, tab_label)
            else:
                self._source_filters = set()
                for btn in getattr(self, "_source_filter_btns", {}).values():
                    btn.blockSignals(True)
                    btn.setChecked(False)
                    btn.blockSignals(False)

        # Bulk buttons on every tab EXCEPT the Dungeons tab (where they don't
        # exist), and always in place so the status keys never shift: the
        # 'Enable All' / 'Disable All' text only shows in the All view —
        # elsewhere the buttons sit as empty, disabled cells so the controls
        # cell never reflows. Enabling is gated in _update_bulk_buttons_state.
        show_bulk = not is_dungeon_tab
        in_all_view = status_filter == "ALL" and not is_global
        if hasattr(self, "_btn_codex_enable"):
            if show_bulk:
                self._btn_codex_enable.setVisible(True)
                self._btn_codex_disable.setVisible(True)
                self._btn_codex_enable.setText("Enable All" if in_all_view else "")
                self._btn_codex_disable.setText("Disable All" if in_all_view else "")
            else:
                self._btn_codex_enable.setVisible(False)
                self._btn_codex_disable.setVisible(False)
                self._btn_codex_enable.setText("")
                self._btn_codex_disable.setText("")
            if not in_all_view:
                self._btn_codex_confirm.setVisible(False)
                self._btn_codex_cancel.setVisible(False)
            elif self._btn_codex_confirm.isVisible():
                self._btn_codex_enable.setVisible(False)
                self._btn_codex_disable.setVisible(False)

        # Dungeons tab: the status block (All/Nearby/Visible/Hidden) becomes
        # zone filters (All/Z1/Z2/Z3) to slice the dungeon list by region
        # (rifts by their fixed spawn point). The zone filter resets when the
        # tab is left, and the status buttons come back.
        if hasattr(self, "_zone_btns"):
            # The All/Z1/Z2/Z3 block replaces the status buttons on the
            # Dungeons tab and on the Collection Chests/Orbs views.
            zone_block_active = is_dungeon_tab or (is_collection_tab and getattr(self, "_chest_orb_kind", None))
            if zone_block_active:
                for b in self._zone_widgets.values():
                    b.setVisible(True)
                for b in self._status_btns.buttons():
                    b.setVisible(False)
                checked = self._zone_btns.checkedButton()
                self._dungeon_zone = next((t for t, b in self._zone_widgets.items() if b is checked), "All")
            else:
                for b in self._zone_widgets.values():
                    b.setVisible(False)
                if getattr(self, "_dungeon_zone", "All") != "All":
                    self._dungeon_zone = "All"
                    all_btn = self._zone_widgets.get("All")
                    if all_btn:
                        self._zone_btns.blockSignals(True)
                        all_btn.setChecked(True)
                        self._zone_btns.blockSignals(False)
                # Zone-filter pins only live while the Dungeons tab is active.
                if getattr(self, "_zone_pins_active", False):
                    self._clear_zone_dungeon_pins()
                for b in self._status_btns.buttons():
                    b.setVisible(True)

    def _sync_nearby_status_key(self, current_rid: str, tab_label: str) -> None:
        """Fade the Nearby status key out where it can't match anything
        (Nearby filters by real world entities, so it only makes sense for
        the Pets sub-view and the zone tabs); it's cleared if left checked
        so the filter never silently empties the grid."""
        if not hasattr(self, "_status_widgets") or "Nearby" not in self._status_widgets:
            return
        is_collection_tab = current_rid == "Pets" or tab_label == "Collection"
        nearby_ok = not (is_collection_tab and self._codex_collection_sub_tag() != "pets")
        btn = self._status_widgets["Nearby"]
        btn.setEnabled(nearby_ok)
        eff = btn.graphicsEffect()
        if nearby_ok:
            if eff is not None:
                btn.setGraphicsEffect(None)
        else:
            if not isinstance(eff, QtWidgets.QGraphicsOpacityEffect):
                eff = QtWidgets.QGraphicsOpacityEffect(btn)
                eff.setOpacity(0.45)
                btn.setGraphicsEffect(eff)
            if btn.isChecked():
                all_btn = self._status_widgets.get("All")
                if all_btn:
                    all_btn.setChecked(True)  # exclusive group unchecks Nearby

    def _codex_nearby_info(self) -> dict[str, float]:
        """uid -> nearest distance for enemies/companions within 300m."""
        nearby_info = {}
        if hasattr(self, "model") and self.model:
            xyz = self.model.player_xyz()
            if xyz:
                for e, d in self.model.nearest_enemies(xyz, 500, max_dist=300.0):
                    if e.unit_id:
                        nearby_info[e.unit_id] = min(nearby_info.get(e.unit_id, 999.0), d)
                for e, d in self.model.nearest_companions(xyz, 100, max_dist=300.0):
                    if e.unit_id:
                        nearby_info[e.unit_id] = min(nearby_info.get(e.unit_id, 999.0), d)
        return nearby_info

    def _codex_item_matches(self, rid: str, items: list[dict], query: str, status_filter: str,
                            nearby_info: dict, hidden_units: set, hidden_comps: set,
                            dungeon_tab: bool, collection_tab: bool = False) -> list[tuple]:
        """Filter one region's items; returns (uid, item, is_active, is_pet, dist) tuples."""
        cur_rid, _tab_label = self._codex_current_region()
        matches = []
        for item in items:
            uid = item["id"]
            uname = item["name"]

            # Mounts/gliders carry type "Mount"/"Glider" (no is_critter) but hide
            # through the collection/companion namespace.
            itype_l = (item.get("type") or "").lower()
            is_pet = rid in ("Pets", "Mounts", "Gliders") or item.get("is_critter", False) \
                or itype_l in ("mount", "glider", "mounts", "gliders", "companion", "pet") \
                or uid.startswith(("Mount_", "Glider_"))
            is_active = uid not in (hidden_comps if is_pet else hidden_units)

            if status_filter == "VISIBLE" and not is_active:
                continue
            if status_filter == "HIDDEN" and is_active:
                continue
            if status_filter == "NEARBY" and uid not in nearby_info:
                continue
            if query and query not in uname.lower() and query not in uid.lower():
                continue
            if getattr(self, "_spark_only", False) and not item.get("drops_spark"):
                continue
            # Dungeon tab mobs are already dungeon mobs, so never filter them
            if getattr(self, "_dungeon_only", False) and not item.get("is_dungeon") and not dungeon_tab:
                continue
            # Dungeon-tab zone filter (All / Z1 / Z2 / Z3 keys): keep only mobs
            # whose dungeon entrance (or rift spawn point) sits in the zone.
            zone = getattr(self, "_dungeon_zone", "All") or "All"
            if dungeon_tab and zone != "All" and codex.dungeon_zone_tag(uid) != zone:
                continue
            # Source-badge filter (Pets / Others legend keys): when any of the
            # Vendor / Achievement / Dungeon / Chest keys are checked, an item
            # must match at least one. Sources are treated as mutually
            # exclusive (the card badges also show only one, priority-chained),
            # so OR-over-the-checked-keys is the right semantics — shared
            # predicate with the badges via codex.item_sources().
            src_filters = getattr(self, "_source_filters", set())
            if src_filters and not (codex.item_sources(item) & src_filters):
                continue
            # Collection species-group filter (the secondary Pets/Mounts/
            # Gliders sub-menu): when a family key is active, keep only that
            # family's cards.
            group_filter = getattr(self, "_codex_group_filter", "All")
            if collection_tab and group_filter != "All" \
                    and self._codex_sub_group(item, rid, rid == "Z0") != group_filter:
                continue

            # Sub-view filters on the Collection / Others tabs: Z0 items split
            # by type on Collection (Mounts/Gliders, unreleased excluded) and
            # by status on Others (All/Shop/Unreleased/TODO). The global
            # search view uses the Others 'All' predicate for the Z0 rows so
            # released mounts/gliders never render under the Other header
            # there either.
            if rid == "Z0":
                if collection_tab:
                    sub_tag = self._codex_collection_sub_tag()
                    if not self._codex_collection_ok(item, sub_tag):
                        continue
                elif hasattr(self, "_others_btn_group"):
                    sub_tag = self._codex_others_sub_tag() if cur_rid == "Z0" else "all"
                    if not self._codex_others_sub_ok(item, sub_tag):
                        continue

            matches.append((uid, item, is_active, is_pet, nearby_info.get(uid, 0)))
        return matches

    def _sort_codex_matches(self, matches: list, rid: str, status_filter: str, query: str,
                            collection_tab: bool = False) -> None:
        """Sort a region's matches in place (in-game order preserved by default)."""
        if status_filter == "NEARBY":
            matches.sort(key=lambda x: x[4])
        elif rid == "Bosses":
            # Rift status, then level, then dungeon name, then boss-first, then name
            matches.sort(key=lambda x: (
                x[1].get("is_rift", False),
                x[1].get("level", 99),
                x[1].get("type") or "",
                not x[1].get("is_boss", False),
                x[1]["name"].lower()
            ))
        elif rid in ("Pets", "Z0"):
            # Pets group by species, Collection mounts/gliders by their
            # `group` family, and Z0 Others rows by type bucket — the shared
            # `_codex_sub_group` key keeps each family's cards contiguous so
            # the sub-headers render one per group.
            matches.sort(key=lambda x: (
                self._codex_sub_group(x[1], rid, collection_tab and rid == "Z0"),
                not x[1].get("is_boss", False),
                x[1]["name"].lower()
            ))
        elif query or status_filter != "ALL":
            matches.sort(key=lambda x: x[1]["name"].lower())

    def _render_codex_region(self, rid: str, rname: str, matches: list, grid_row: int,
                             is_global: bool, status_filter: str, current_rid: str,
                             collection_tab: bool = False) -> int:
        """Render one region's cards into the grid; returns the next grid row."""
        cols = 4
        compact_mobs = self.s.codex_compact if hasattr(self.s, "codex_compact") else False
        compact_pets = self.s.codex_pets_compact if hasattr(self.s, "codex_pets_compact") else True
        is_collection_view = self._codex_is_collection_view(current_rid, rname)

        if is_global:
            header = C.SectionHeader(rname)
            self._codex_grid.addWidget(header, grid_row, 0, 1, cols)
            grid_row += 1

        curr_col = 0
        last_type = None
        for uid, item, is_active, is_pet, dist in matches:
            # Species/category sub-headers (Pets, Dungeons, Others) on the
            # 'ALL' view. The Collection tab's Mounts/Gliders views key by the
            # compiled `group` family (Aries / Boar / Wolf / …), just like
            # Pets keys by species — via the shared `_codex_sub_group` helper.
            group_key = self._codex_sub_group(item, rid, collection_tab and rid == "Z0")
            if rid in ("Pets", "Bosses", "Z0") and not is_global and status_filter == "ALL" and group_key != last_type:
                if curr_col > 0:
                    grid_row += 1
                    curr_col = 0
                last_type = group_key
                if (collection_tab and rid == "Z0") or rid == "Pets":
                    # Species families read as spaced words: 'FlyingFish' ->
                    # 'FLYING FISH' (Mounts/Gliders) and 'DemonDog' ->
                    # 'DEMON DOG' (Pets) — the same labels as the group
                    # sub-menu buttons.
                    header_text = self._codex_group_label(group_key)
                else:
                    header_text = self._codex_type_header(rid, last_type)
                if header_text and str(header_text).upper() != "OTHERS":
                    header_text = str(header_text).upper()
                    if rid == "Bosses":
                        is_rift = item.get("is_rift", False)
                        level = item.get("level")
                        if level and not is_rift:
                            header_text = f"<b>{header_text}</b> (LEVEL {level})"
                    sub_header = QtWidgets.QLabel(header_text)
                    sub_header.setStyleSheet(f"color: {theme.ACCENT}; margin-top: 10px; margin-bottom: 4px; font-size: 13px;")
                    self._codex_grid.addWidget(sub_header, grid_row, 0, 1, cols)
                    grid_row += 1

            is_coll = is_pet or is_collection_view
            card = CodexUnitCard(uid, item, 0, 10, is_active,
                                 compact=compact_pets if is_coll else compact_mobs,
                                 hide_dungeon_badge=current_rid == "Bosses")
            card.is_pet = is_pet  # so _refresh_codex_sync hides through the same namespace
            if rid == "Bosses":
                card.locked = True
                card.set_active(True)

            if dist > 0:
                card.setToolTip(f"{card.toolTip()}\nDistance: {dist:.0f}m")

            if is_pet:
                card.toggled.connect(lambda on, u=uid: self._set_companion_hidden(u, not on))
            else:
                card.toggled.connect(lambda on, u=uid: self._set_unit_hidden(u, not on))
            card.selected.connect(lambda uid_arg, it=item: self._on_codex_mob_selected(uid_arg, it))

            self._codex_grid.addWidget(card, grid_row, curr_col)
            self._codex_cards.append(card)
            curr_col += 1
            if curr_col >= cols:
                curr_col = 0
                grid_row += 1

        if curr_col > 0:
            grid_row += 1
        return grid_row

    def _render_chest_orb_list(self, grid_row: int) -> tuple[int, int]:
        """Render the Chests & Orbs list (remaining chests + secret orbs from
        the profile's collected state) into the grid, replacing cards while
        the 'Chests & Orbs' toggle is active. Returns the next grid row and
        the number of items listed."""
        from .chest_orb_list import ChestOrbListView
        cols = 4
        view = ChestOrbListView(self)
        self._codex_chest_list = view   # jump-to-chest reveal targets a row
        self._codex_grid.addWidget(view, grid_row, 0, 1, cols)
        grid_row += 1
        for c in range(cols):
            self._codex_grid.setColumnStretch(c, 1)
        return grid_row, view.row_count

    def _render_dungeon_list(self, grid_row: int) -> tuple[int, int]:
        """Render the click-to-track dungeon list into the grid (replacing
        cards while the 'Dungeon List' toggle is active). Returns the next
        grid row and the number of dungeons listed."""
        from .dungeon_list import DungeonListView
        cols = 4
        view = DungeonListView(self)
        self._codex_dungeon_list = view  # jump-to-boss reveal targets a row
        self._codex_grid.addWidget(view, grid_row, 0, 1, cols)
        grid_row += 1
        # Let the outer columns share the panel width so the list fills it
        # (and the 2-column block centers itself) instead of hugging the left.
        for c in range(cols):
            self._codex_grid.setColumnStretch(c, 1)
        return grid_row, view.row_count

    def _render_soulstone_list(self, grid_row: int) -> tuple[int, int]:
        """Render the click-to-track soulstone summon-spot list into the grid
        (replacing cards while the 'Soulstones' toggle is active). Returns the
        next grid row and the number of spots listed."""
        from .soulstones import SoulstoneListView
        cols = 4
        view = SoulstoneListView(self)
        self._codex_soulstone_list = view  # jump-to-soulstone reveal targets a row
        self._codex_grid.addWidget(view, grid_row, 0, 1, cols)
        grid_row += 1
        for c in range(cols):
            self._codex_grid.setColumnStretch(c, 1)
        return grid_row, view.row_count

    def _codex_others_sub_tag(self) -> str:
        """Current Others sub-view ('all' / 'shop' / 'unreleased' / 'misc')."""
        if hasattr(self, "_others_btn_group"):
            checked = self._others_btn_group.checkedButton()
            return checked.text().lower() if checked else "all"
        return "all"

    def _codex_others_sub_ok(self, item: dict, sub_tag: str) -> bool:
        """True when an Others-tab (Z0) item belongs in the given sub-view
        (All / Shop / Unreleased / Misc). Shop items are buyable now;
        'unreleased' content isn't in the game yet; 'misc' entries are
        compiler todo placeholders."""
        if sub_tag == "all":
            return codex.belongs_in_others(item)
        if sub_tag == "shop":
            return codex.is_shop_item(item)
        if sub_tag == "unreleased":
            # Shop items are buyable NOW even though the raw data flags them
            # 'unreleased' — they belong in Shop, never in Unreleased.
            return (item.get("kind") or "").lower() == "unreleased" \
                and not codex.is_shop_item(item)
        if sub_tag in ("misc", "todo"):
            k = (item.get("kind") or "").lower()
            uid = (item.get("id") or "").lower()
            uname = (item.get("name") or "").lower()
            return k == "todo" or "todo" in uid or "todo" in uname
        return True

    def _codex_collection_sub_tag(self) -> str:
        """Active Collection sub-view: 'chests'/'orbs' when a remaining list
        is showing, otherwise the checked sub-bar button ('all'/'pets'/
        'mounts'/'gliders')."""
        if getattr(self, "_chest_orb_kind", None):
            return self._chest_orb_kind
        if hasattr(self, "_collection_btn_group"):
            checked = self._collection_btn_group.checkedButton()
            if checked:
                return checked.text().lower()
        return getattr(self, "_collection_sub", "pets")

    def _codex_collection_ok(self, item: dict, sub_tag: str) -> bool:
        """True when a Z0 item belongs in the active Collection sub-view
        (Mounts / Gliders / All). Pets never render Z0 (they're the 'Pets'
        region). Unreleased/todo items aren't collectible yet — they live on
        the Others tab; shop items are buyable now, so they stay."""
        uid = item["id"]
        itype = (item.get("type") or "").lower()
        is_m = "mount" in itype or uid.startswith("Mount_")
        is_g = "glider" in itype or uid.startswith("Glider_")
        # Shop items are buyable now — the raw 'unreleased' flag doesn't apply
        # to them, so they stay in the obtainable lists.
        is_unreleased = (item.get("kind") or "").lower() in ("unreleased", "todo") \
            and not codex.is_shop_item(item)
        if sub_tag == "mounts":
            return is_m and not is_unreleased
        if sub_tag == "gliders":
            return is_g and not is_unreleased
        return (is_m or is_g) and not is_unreleased  # 'all'

    def _codex_collection_regions(self) -> dict[str, str]:
        """{region_id: header label} rendered by the Collection tab's active
        sub-view: Pets region for 'pets', Z0 (type-filtered) for the rest.
        Pets is the default (there's no 'All' Collection view)."""
        sub = self._codex_collection_sub_tag()
        if sub == "mounts":
            return {"Z0": "Mounts"}
        if sub == "gliders":
            return {"Z0": "Gliders"}
        return {"Pets": "Pets"}  # 'pets' (and the safe default)

    def _codex_type_header(self, rid: str, ttype: str | None) -> str:
        """Card-grid category sub-header label: species names for Pets, and
        normalized Mounts/Gliders/Misc buckets for the Others/Collection Z0
        views (the raw data types are singular 'Mount'/'Glider')."""
        t = (ttype or "").lower()
        if rid == "Z0":
            if "mount" in t:
                return "Mounts"
            if "glider" in t:
                return "Gliders"
            if t in ("misc", "other", "todo"):
                return "Misc"
        return ttype or ""

    def _sync_source_key_visibility(self, current_rid: str, tab_label: str) -> None:
        """Keep the source-badge legend keys locked to their full 2x3
        footprint: every key stays visible in the fixed 2-column grid, while
        a key that can't match anything in the current sub-view is pruned
        when checked and disabled outright (greyed, not clickable). Clicking
        the active key again clears the filter."""
        present: set[str] = set()
        if current_rid == "Pets":
            regions = self._codex_collection_regions()
        else:
            regions = self._codex_regions_to_show(current_rid, tab_label, is_global=False)
        for rid in regions:
            for item in codex.units_by_region(rid, ingame_order=True):
                if rid == "Z0" and current_rid == "Pets" and not self._codex_collection_ok(item, self._codex_collection_sub_tag()):
                    continue
                if rid == "Z0" and current_rid == "Z0" and not self._codex_others_sub_ok(item, self._codex_others_sub_tag()):
                    continue
                present |= codex.item_sources(item)

        # Locked layout: the buttons keep the 2x3 slots they were built with
        # (no reflow between sub-views) — only the filter state prunes. A
        # checked key with nothing to match is unchecked so the grid never
        # silently empties, and keys that can't match anything are disabled
        # (dimmed, unclickable) instead of flashing on/off when clicked.
        for kind, btn in self._source_filter_btns.items():
            btn.setVisible(True)
            faded = kind not in present
            btn.setEnabled(not faded)
            # Fade keys that can't match the current view instead of just
            # dimming the label — same QGraphicsOpacityEffect approach as
            # ToggleCard.setEnabled, so the icon tile fades too.
            eff = btn.graphicsEffect()
            if faded:
                if not isinstance(eff, QtWidgets.QGraphicsOpacityEffect):
                    eff = QtWidgets.QGraphicsOpacityEffect(btn)
                    eff.setOpacity(0.45)
                    btn.setGraphicsEffect(eff)
            elif eff is not None:
                btn.setGraphicsEffect(None)
            if faded and btn.isChecked():
                btn.blockSignals(True)
                btn.setChecked(False)
                btn.blockSignals(False)
        if getattr(self, "_source_filters", set()):
            self._source_filters = {k for k in self._source_filters if k in present}

    def _codex_chest_orb_kind(self) -> str | None:
        """Active remaining list kind ('chests'/'orbs'), or None when the card
        grid or dungeon list is showing."""
        return getattr(self, "_chest_orb_kind", None)

    def _codex_chest_orb_view_active(self) -> bool:
        """True when a remaining Chests or Orbs list is the active grid view."""
        return self._codex_chest_orb_kind() is not None

    def _on_dungeon_list_toggled(self, checked: bool) -> None:
        """Dungeon List view active — swap the grid to the track list."""
        self._set_dungeon_view_mode("list")

    def _on_dungeon_soulstones_toggled(self, checked: bool) -> None:
        """Soulstones view active — swap the grid to the summon-spot list."""
        self._set_dungeon_view_mode("soulstones")

    def _on_dungeon_mobs_toggled(self, checked: bool) -> None:
        """Dungeon Mobs view active — swap the grid back to the card grid."""
        self._set_dungeon_view_mode("mobs")

    def _on_chest_list_toggled(self, checked: bool) -> None:
        """Chests view active — swap the grid to the remaining-chests list."""
        self._set_dungeon_view_mode("chests")

    def _on_orb_list_toggled(self, checked: bool) -> None:
        """Orbs view active — swap the grid to the remaining-secret-orbs list."""
        self._set_dungeon_view_mode("orbs")

    def _on_collection_sub_toggled(self, checked: bool) -> None:
        """Collection sub-view (All / Pets / Mounts / Gliders) selected — the
        card-grid modes. Chests/Orbs are handled by their own handlers. Reads
        the clicked button directly (not `_codex_collection_sub_tag`, which
        would still report the stale chest/orb mode)."""
        if not hasattr(self, "_collection_btn_group"):
            return
        checked_btn = self._collection_btn_group.checkedButton()
        if checked_btn is None:
            return
        tag = checked_btn.text().lower()
        if tag in ("chests", "orbs"):
            return
        self._collection_sub = tag
        self._chest_orb_kind = None
        self._dungeon_list_mode = False
        self._refresh_codex_grid(reset_scroll=True)

    def _set_dungeon_view_mode(self, mode: str) -> None:
        """Switch between the card grid ('mobs'), the dungeon list ('list'),
        the soulstone list ('soulstones'), and the chests/orbs lists. Skip
        the refresh when the view didn't change."""
        cur = (getattr(self, "_chest_orb_kind", None)
               or ("soulstones" if getattr(self, "_soulstones_mode", False)
                   else "list" if getattr(self, "_dungeon_list_mode", False)
                   else "mobs"))
        if mode == cur:
            return
        self._dungeon_list_mode = mode == "list"
        self._soulstones_mode = mode == "soulstones"
        self._chest_orb_kind = mode if mode in ("chests", "orbs") else None
        if mode in ("chests", "orbs"):
            self._collection_sub = mode
        self._refresh_codex_grid(reset_scroll=True)

    def _update_codex_count(self, total_shown: int, is_global: bool) -> None:
        if hasattr(self, "_codex_count"):
            # Same label in both Dungeon views: the list keeps 'ZONE TOTAL'
            # instead of swapping to a separate 'DUNGEON LIST' count.
            if is_global:
                lbl_text = f"SEARCH MATCHES: {total_shown}"
            else:
                lbl_text = f"ZONE TOTAL: {total_shown}"
            self._codex_count.setText(lbl_text)

