"""Codex settings: last-hidden undo widget, bulk actions, and card sync."""
from __future__ import annotations

import os

from ... import theme
from ....data import codex


class CodexSettingsMixin:
    def _on_last_hidden_clicked(self, event=None) -> None:
        if (getattr(self, "_last_hidden_chest_orb", None)
                and self._last_hidden_uid == self._last_hidden_chest_orb[0]):
            # Undo a Chests/Orbs right-click: un-collect the item.
            item_id, _label, _kind = self._last_hidden_chest_orb
            profile = self.model.player_profile() if hasattr(self, "model") and self.model else None
            self.s.toggle_done(item_id, profile)
            self._last_hidden_chest_orb = None
            self._update_last_hidden_widget()
            if hasattr(self, "_refresh_codex_grid"):
                self._refresh_codex_grid()
            return
        if getattr(self, "_last_hidden_uid", None):
            if getattr(self, "_last_hidden_is_pet", False):
                self._set_companion_hidden(self._last_hidden_uid, False)
            else:
                self._set_unit_hidden(self._last_hidden_uid, False)

    def _update_last_hidden_widget(self) -> None:
        """Update the Last Hidden undo frame on the Codex top bar."""
        if not hasattr(self, "_last_hidden_frame"):
            return

        profile = self.model.player_profile() if hasattr(self, "model") and self.model else None
        hidden_units = set(self.s.get_entity_hidden_units(profile))
        hidden_comps = set(self.s.get_companion_hidden_units(profile)) if hasattr(self.s, "get_companion_hidden_units") else set()
        query = self._codex_search.text().strip().lower() if hasattr(self, "_codex_search") else ""

        last_u = getattr(self, "_last_hidden_unit", None)
        last_c = getattr(self, "_last_hidden_comp", None)
        last_co = getattr(self, "_last_hidden_chest_orb", None)

        done = set(self.s.get_poi_done(profile))

        show_lh = False
        if last_c and last_c[0] in hidden_comps:
            uid, name = last_c
            is_pet = True
            show_lh = True
        elif last_u and last_u[0] in hidden_units:
            uid, name = last_u
            is_pet = False
            show_lh = True
        elif last_co and last_co[0] in done:
            item_id, name, kind = last_co
            is_pet = False
            show_lh = True

        if show_lh and not query:
            if last_co and last_co[0] in done:
                # Chest / orb marked collected via the Chests & Orbs list
                # right-click — undo chip with the orb/chest marker.
                accent = theme.KIND_COLOR["orb"] if kind == "orb" else theme.CHEST
                self._lh_icon.set_marker("orb" if kind == "orb" else "chest", accent)
                self._lh_icon.setStyleSheet("border: 0; background: transparent;")
                self._lh_title.setText("LAST HIDDEN")
                self._lh_title.setStyleSheet(f"color: {accent}; font-weight: bold; font-size: 11px; letter-spacing: 0.5px;")
                self._lh_name.setText(name)
                self._lh_name.setStyleSheet(f"color: {theme.TEXT}; font-weight: bold; font-size: 12px;")
                self._last_hidden_frame.setToolTip(f"Last Hidden: {name}\nClick to unhide")
                self._last_hidden_uid = item_id
                self._last_hidden_is_pet = False
                self._last_hidden_frame.setVisible(True)
                return

            item_data = codex.enemies_data().get(uid, {"id": uid, "name": name, "is_critter": is_pet})
            sheet = "collection" if is_pet else "units"
            icon_val = item_data.get("icon") or uid
            if isinstance(icon_val, str):
                if "." in icon_val: icon_val = icon_val.rsplit(".", 1)[0]
                if "/" in icon_val or "\\" in icon_val: icon_val = os.path.splitext(os.path.basename(icon_val))[0]

            is_boss = item_data.get("is_boss", False)
            is_elite = item_data.get("is_elite", False)
            is_spark_pet = is_pet and ("spark" in name.lower() or "spark" in uid.lower())

            if is_boss or is_spark_pet:
                bcol = theme.GOLD
            elif is_elite:
                bcol = theme.SILVER
            else:
                bcol = None

            accent_col = bcol if bcol else theme.ACCENT
            self._lh_icon.set_outlined(sheet, str(icon_val), accent=accent_col, border=3 if bcol else 2)

            self._lh_icon.setStyleSheet("border: 0; background: transparent;")
            self._lh_title.setText("LAST HIDDEN")
            txt_color = bcol if bcol else theme.ACCENT
            self._lh_title.setStyleSheet(f"color: {txt_color}; font-weight: bold; font-size: 11px; letter-spacing: 0.5px;")
            self._lh_name.setText(name)
            self._lh_name.setStyleSheet(f"color: {theme.TEXT}; font-weight: bold; font-size: 12px;")
            self._last_hidden_frame.setToolTip(f"Last Hidden: {name}\nClick to unhide")
            self._last_hidden_uid = uid
            self._last_hidden_is_pet = is_pet
            self._last_hidden_frame.setVisible(True)
        else:
            self._last_hidden_frame.setVisible(False)

    def _update_bulk_buttons_state(self) -> None:
        """Bulk buttons exist on the open-world zone tabs only — hidden on
        Dungeons and the Pets/Others collection tabs. They only enable on the
        All view, with a usable profile and no active Dungeon/Spark filter
        key; elsewhere they sit disabled in place so the controls cell never
        reflows. The 'Enable All' / 'Disable All' text itself is handled by
        _update_codex_chrome (text only in the All view)."""
        if not hasattr(self, "_btn_codex_enable") or not hasattr(self, "_btn_codex_disable"):
            return

        current_rid, tab_label = self._codex_current_region()
        is_dungeon_tab = current_rid == "Bosses" or tab_label == "Dungeons"
        if is_dungeon_tab:
            self._btn_codex_confirm.setVisible(False)
            self._btn_codex_cancel.setVisible(False)
            self._btn_codex_enable.setVisible(False)
            self._btn_codex_disable.setVisible(False)
            return

        # The collection tabs (Pets / Others) have no bulk actions at all —
        # hide the buttons there entirely (Others' Misc sub-view in
        # particular is unreleased junk).
        is_collection_tab = current_rid in ("Pets", "Z0") or tab_label in ("Pets", "Others", "Other")
        if is_collection_tab:
            self._btn_codex_confirm.setVisible(False)
            self._btn_codex_cancel.setVisible(False)
            self._btn_codex_enable.setVisible(False)
            self._btn_codex_disable.setVisible(False)
            return

        query, status_filter = self._codex_filter_state()
        # Same 'All view' rule as _update_codex_chrome: a global search (or
        # Nearby) isn't the All view, so bulk stays inert there too.
        in_all_view = status_filter == "ALL" and not self._codex_is_global(query, status_filter, tab_label)
        has_profile = bool(hasattr(self, "model") and self.model and self.model.player_profile())
        filter_key_on = bool(getattr(self, "_dungeon_only", False) or getattr(self, "_spark_only", False))

        if in_all_view and not filter_key_on and has_profile:
            self._btn_codex_enable.setEnabled(True)
            self._btn_codex_disable.setEnabled(True)
            self._btn_codex_enable.setToolTip("Enable all items in this section")
            self._btn_codex_disable.setToolTip("Disable all items in this section")
        else:
            self._codex_cancel_bulk()
            self._btn_codex_enable.setEnabled(False)
            self._btn_codex_disable.setEnabled(False)
            if not in_all_view:
                tip = "Switch to the All view to bulk-toggle this section"
            elif filter_key_on:
                tip = "Clear the Dungeon/Spark filter to bulk-toggle zone mobs"
            else:
                tip = "Character profile required (start game to enable/disable zone mobs)"
            self._btn_codex_enable.setToolTip(tip)
            self._btn_codex_disable.setToolTip(tip)

    def _refresh_codex_sync(self) -> None:
        """Sync card active state in-place without rebuilding grid widgets or resetting scroll."""
        # the Codex page may have been evicted from the page cache
        if not getattr(self, "_widget_alive", lambda w: False)(
                getattr(self, "_codex_grid", None)):
            return
        self._update_last_hidden_widget()
        self._update_bulk_buttons_state()
        if not hasattr(self, "_codex_cards") or not self._codex_cards:
            return
        profile = self.model.player_profile() if hasattr(self, "model") and self.model else None
        hidden_units = set(self.s.get_entity_hidden_units(profile))
        hidden_comps = set(self.s.get_companion_hidden_units(profile)) if hasattr(self.s, "get_companion_hidden_units") else set()

        for card in self._codex_cards:
            uid = getattr(card, "uid", getattr(card, "unit_id", None))
            if not uid:
                continue
            is_pet = getattr(card, "is_pet", False) or (card.data.get("is_critter", False) if hasattr(card, "data") and card.data else False)
            is_active = uid not in (hidden_comps if is_pet else hidden_units)
            if hasattr(card, "set_active"):
                card.set_active(is_active)

    def _codex_prime_bulk(self, visible: bool) -> None:
        """Show confirmation buttons for bulk actions."""
        if not self._btn_codex_enable.isEnabled():
            return
        self._btn_codex_enable.setVisible(False)
        self._btn_codex_disable.setVisible(False)

        self._bulk_visible_target = visible
        # Keep text short to avoid clipping in the fixed-width grid buttons
        self._btn_codex_confirm.setText("CONFIRM")
        self._btn_codex_confirm.setStyleSheet(
            f"QPushButton#Accent {{ font-size: 11px; font-weight: bold; background: {theme.ACCENT}; color: {theme.ON_ACCENT}; }}"
        )
        self._btn_codex_cancel.setStyleSheet(
            f"QPushButton#Outline {{ font-size: 11px; font-weight: bold; background: transparent; border: 1px solid {theme.BORDER}; color: {theme.MUTED}; }}"
        )
        self._btn_codex_confirm.setVisible(True)
        self._btn_codex_cancel.setVisible(True)

    def _codex_cancel_bulk(self) -> None:
        """Reset bulk action buttons."""
        self._btn_codex_confirm.setVisible(False)
        self._btn_codex_cancel.setVisible(False)
        self._btn_codex_enable.setVisible(True)
        self._btn_codex_disable.setVisible(True)

    def _codex_set_all(self, visible: bool) -> None:
        """Set all units in the current region/view to visible or hidden."""
        self._codex_cancel_bulk()

        # 1. Determine region ID from the active tab
        region_id, _tab = self._codex_current_region()

        # Guard: Block zone mob bulk toggle if no active character profile
        if region_id not in ("Pets", "Z0") and not (hasattr(self, "model") and self.model and self.model.player_profile()):
            return

        # 2. Get all items for this region
        items = codex.units_by_region(region_id)
        uids = [it["id"] for it in items]

        # 3. Apply to settings (bulk)
        profile = self.model.player_profile() if hasattr(self, "model") and self.model else None

        if region_id == "Pets":
            for uid in uids:
                self.s.toggle_companion_hidden(uid, not visible, profile)
        else:
            self.s.bulk_toggle_units_hidden(uids, not visible, profile)

        # 4. Sync Entity tab chips if they exist
        if region_id == "Pets" and hasattr(self, "_companion_chips"):
            uid_set = set(uids)
            for u, _n, chip in self._companion_chips:
                if u in uid_set:
                    chip.blockSignals(True)
                    chip.setChecked(visible)
                    chip.blockSignals(False)
                    chip.restyle()
        elif region_id != "Pets" and hasattr(self, "_unit_chips"):
            uid_set = set(uids)
            for us, _n, chip in self._unit_chips:
                # us is a list[str] of UIDs in the group
                match = False
                for u in us:
                    if u in uid_set:
                        match = True
                        break
                if match:
                    chip.blockSignals(True)
                    chip.setChecked(visible)
                    chip.blockSignals(False)
                    chip.restyle()

        # 5. Refresh UI
        # Use sync for hiding to keep cards visible (faded) for accidental clicks.
        # Use grid rebuild for showing to ensure all newly enabled units appear.
        if visible:
            self._refresh_codex_grid()
        else:
            self._refresh_codex_sync()

        if hasattr(self, "_update_unit_tag"):
            self._update_unit_tag()
        if hasattr(self, "_update_companion_tag"):
            self._update_companion_tag()
