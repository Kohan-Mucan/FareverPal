"""Codex Collection species-grouping: the shared group key, the group
sub-menu (All + one key per species family), and its chrome sync. Pets
already group by species; this gives the same treatment to Mounts/Gliders
and provides the filter bar that narrows each card-grid sub-view to a
single family.
"""
from __future__ import annotations

import re

from PySide6 import QtCore, QtWidgets

from ... import theme
from ....data import codex


# Same flat toggle style as the Collection/Others sub-bar buttons — the
# species-group filter keys (All + one per family) wear it too.
_CODEX_GROUP_QSS = (
    f"QPushButton{{background:transparent; border:1px solid {theme.BORDER}; color:{theme.MUTED}; "
    f"font-size:11px; font-weight:bold; padding: 2px 8px; border-radius:4px;}}"
    f"QPushButton:hover{{color:{theme.TEXT}; border-color:{theme.DIM};}}"
    f"QPushButton:checked, QPushButton:pressed{{background:{theme.with_alpha(theme.ACCENT, 42)}; "
    f"color:{theme.ACCENT}; border-color:{theme.ACCENT};}}"
)


class CodexGroupingMixin:
    def _codex_sub_group(self, item: dict, rid: str, collection_z0: bool) -> str:
        """Sub-header / sort grouping key for a card: the species family for
        Pets and the Collection Mounts/Gliders views (the compiled `group`),
        the dungeon for Bosses, and the raw type bucket for the Z0 Others
        rows. Cash-shop / early-access items group under one general 'Shop'
        family; group-less entries fall back to the raw type."""
        if collection_z0 or rid == "Pets":
            if codex.is_shop_item(item):
                return "Shop"
            if collection_z0:
                return (item.get("group") or item.get("type") or "").strip()
            return item.get("type") or ""
        return item.get("type") or ""

    def _codex_group_label(self, group: str) -> str:
        """Display label for a species-family group: camelCase ids read as
        spaced words ('FlyingFish' -> 'Flying Fish')."""
        return re.sub(r"(?<!^)(?=[A-Z])", " ", group)

    def _codex_group_counts(self, sub_tag: str, status_filter: str = "ALL") -> list[tuple[str, int]]:
        """(family, card count) pairs for a Collection sub-view, in in-game
        order. Counts cover exactly the cards the sub-view renders, and the
        Visible/Hidden status filter narrows each family to its active /
        hidden cards, so the counts sum to the grid's ZONE TOTAL."""
        profile = self.model.player_profile() if hasattr(self, "model") and self.model else None
        hidden_comps = set(self.s.get_companion_hidden_units(profile)) \
            if hasattr(self.s, "get_companion_hidden_units") else set()
        # Pets/Mounts/Gliders all hide through the companion namespace.
        narrow = status_filter in ("VISIBLE", "HIDDEN")
        counts: dict[str, int] = {}
        order: list[str] = []

        def _ok(it: dict, g: str) -> bool:
            # register the family even when it has no cards under the filter
            # (zero-count chips stay in place instead of reflowing the bar)
            if g not in counts:
                counts[g] = 0
                order.append(g)
            if not narrow:
                return True
            is_active = it["id"] not in hidden_comps
            return is_active if status_filter == "VISIBLE" else not is_active

        if sub_tag == "pets":
            for it in codex.units_by_region("Pets"):
                g = "Shop" if codex.is_shop_item(it) else (it.get("type") or "").strip()
                if not g:
                    continue
                if _ok(it, g):
                    counts[g] += 1
        else:
            for it in codex.units_by_region("Z0"):
                if not self._codex_collection_ok(it, sub_tag):
                    continue
                g = "Shop" if codex.is_shop_item(it) else (it.get("group") or "").strip()
                if not g:
                    continue
                if _ok(it, g):
                    counts[g] += 1
        return [(g, counts[g]) for g in order]

    def _codex_status_counts(self, sub_tag: str) -> tuple[int, int, int]:
        """(all, visible, hidden) card totals for a Collection sub-view, for
        the shared status filter chips; 'all' merges the three sub-views.
        hidden = all - visible (every card is either hidden or not)."""
        tags = ("pets", "mounts", "gliders") if sub_tag == "all" else (sub_tag,)
        total = visible = 0
        for t in tags:
            total += sum(c for _g, c in self._codex_group_counts(t, "ALL"))
            visible += sum(c for _g, c in self._codex_group_counts(t, "VISIBLE"))
        return total, visible, total - visible

    def _codex_tab_status_counts(self, rid: str) -> tuple[int, int, int]:
        """(all, visible, hidden) totals for a non-Collection tab's region,
        via the same filter chain the grid uses with the status filter
        swapped and the search query ignored — the chips describe the whole
        tab, not the current hit set."""
        items = codex.units_by_region(rid, ingame_order=True)
        profile = self.model.player_profile() if hasattr(self, "model") and self.model else None
        hidden_units = set(self.s.get_entity_hidden_units(profile)) \
            if hasattr(self, "s") else set()
        hidden_comps = set(self.s.get_companion_hidden_units(profile)) \
            if hasattr(self, "s") and hasattr(self.s, "get_companion_hidden_units") else set()
        dungeon_tab = rid == "Bosses"
        out: dict[str, int] = {}
        for status in ("ALL", "VISIBLE", "HIDDEN"):
            matches = self._codex_item_matches(rid, items, "", status, {},
                                               hidden_units, hidden_comps, dungeon_tab)
            out[status] = len(matches)
        return out["ALL"], out["VISIBLE"], out["HIDDEN"]

    def _set_status_chip_counts(self, total: int, visible: int, hidden: int) -> None:
        """Label the All/Visible/Hidden status chips with their totals; the
        Nearby chip keeps its plain label (entity-based, faded on these
        views anyway)."""
        self._status_widgets["All"].setText(f"All ({total})")
        self._status_widgets["Visible"].setText(f"Visible ({visible})")
        self._status_widgets["Hidden"].setText(f"Hidden ({hidden})")

    def _reset_status_chips(self) -> None:
        """Restore the plain status labels after a count-styled view is left
        (the chips are shared with every tab)."""
        for key, btn in self._status_widgets.items():
            if key != "Nearby":
                btn.setText(key)

    def _update_codex_status_chips(self, current_rid: str, tab_label: str) -> None:
        """Show All/Visible/Hidden totals on the shared status filter chips
        ('All (52)' / 'Visible (51)' / 'Hidden (1)') on every card-grid view
        that keeps the status block: the Collection sub-views, the open-world
        zone/Mobs tabs, and the Others tab. Each tab counts exactly the cards
        it renders (same filter chain as the grid), so the chips reconcile
        with the ZONE TOTAL; everywhere else (Dungeons, which swaps in the
        zone filters; the Chests/Orbs lists) restores the plain labels.
        Called from `_update_codex_chrome` on every refresh."""
        if not hasattr(self, "_status_widgets"):
            return
        is_collection_tab = current_rid == "Pets" or tab_label == "Collection"
        if is_collection_tab:
            sub_tag = self._codex_collection_sub_tag()
            if (getattr(self, "_chest_orb_kind", None) is None
                    and sub_tag in ("pets", "mounts", "gliders", "all")):
                total, visible, hidden = self._codex_status_counts(sub_tag)
                self._set_status_chip_counts(total, visible, hidden)
                return
            self._reset_status_chips()
            return
        if current_rid == "Z0" or tab_label in ("Others", "Other"):
            total, visible, hidden = self._codex_tab_status_counts("Z0")
        elif codex.is_zone_region(current_rid):
            total, visible, hidden = self._codex_tab_status_counts(current_rid)
        else:
            self._reset_status_chips()
            return
        self._set_status_chip_counts(total, visible, hidden)

    def _build_codex_group_sub_bar(self) -> QtWidgets.QFrame:
        """Second sub-menu on the Collection Pets/Mounts/Gliders views: an
        All + one-toggle-per-species-family filter bar that narrows the grid
        to a single family. The buttons are (re)built by
        `_rebuild_codex_group_bar` — this owns the frame + flow layout they
        live in. Hidden on the Chests/Orbs lists and every other tab."""
        from ..items.support import FlowLayout
        self._group_sub_bar = QtWidgets.QFrame()
        self._group_sub_bar.setStyleSheet("QFrame { background: transparent; border: 0; }")
        self._group_sub_lay = FlowLayout(self._group_sub_bar, margin=0, spacing=6)
        self._group_sub_bar.setVisible(False)
        return self._group_sub_bar

    def _rebuild_codex_group_bar(self, sub_tag: str, status_filter: str = "ALL") -> None:
        """(Re)build the All + species-family filter keys for a Collection
        sub-view. The family set differs per sub-view, so the bar is rebuilt
        when the sub-view changes; the status filter only re-shapes the chip
        counts, so it rebuilds again when that changes."""
        if not hasattr(self, "_group_sub_lay"):
            return
        while self._group_sub_lay.count():
            item = self._group_sub_lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setVisible(False)
                w.deleteLater()
        self._group_btn_group = QtWidgets.QButtonGroup(self)
        self._group_btn_group.setExclusive(True)
        self._group_sub_btns = {}

        def _mk(label: str, key: str, count: int) -> None:
            btn = QtWidgets.QPushButton(f"{label} ({count})")
            btn.setCheckable(True)
            btn.setFixedHeight(24)
            btn.setCursor(QtCore.Qt.PointingHandCursor)
            btn.setStyleSheet(_CODEX_GROUP_QSS)
            btn.setProperty("groupKey", key)
            btn.clicked.connect(self._on_codex_group_toggled)
            self._group_btn_group.addButton(btn)
            self._group_sub_btns[key] = btn
            self._group_sub_lay.addWidget(btn)

        family_counts = self._codex_group_counts(sub_tag, status_filter)
        _mk("All", "All", sum(c for _g, c in family_counts))
        for g, count in family_counts:
            _mk(self._codex_group_label(g), g, count)
        all_btn = self._group_sub_btns.get("All")
        if all_btn is not None:
            all_btn.setChecked(True)

    def _on_codex_group_toggled(self, checked: bool) -> None:
        """A species-family key on the Collection sub-menu was clicked:
        filter the grid to that family (the filter is the exclusive group's
        checked key). Clicking the already-active family toggles it back
        off."""
        if not hasattr(self, "_group_btn_group"):
            return
        sender = self.sender()
        key = sender.property("groupKey") if sender is not None else None
        if key is None:
            checked_btn = self._group_btn_group.checkedButton()
            key = checked_btn.property("groupKey") if checked_btn is not None else "All"
        active = getattr(self, "_codex_group_filter", "All")
        if key == active and key != "All":
            # toggle-off: back to the whole sub-view
            self._codex_group_filter = "All"
            all_btn = self._group_sub_btns.get("All")
            if all_btn is not None:
                all_btn.blockSignals(True)
                all_btn.setChecked(True)
                all_btn.blockSignals(False)
        else:
            self._codex_group_filter = key if key in self._group_sub_btns else "All"
        self._refresh_codex_grid(reset_scroll=False)

    def _sync_codex_group_bar(self, current_rid: str, tab_label: str,
                              status_filter: str = "ALL") -> None:
        """Chrome sync for the group sub-menu on the Collection card-grid
        views. The buttons are rebuilt when the sub-view or the status
        filter changes; the active family survives a status change. Hiding
        (Chests/Orbs lists, other tabs) resets the filter to All so it never
        leaks across tabs."""
        if not hasattr(self, "_group_sub_bar"):
            return
        is_collection_tab = current_rid == "Pets" or tab_label == "Collection"
        group_bar_on = is_collection_tab and getattr(self, "_chest_orb_kind", None) is None
        if group_bar_on:
            sub_tag = self._codex_collection_sub_tag()
            if sub_tag not in ("pets", "mounts", "gliders"):
                group_bar_on = False
            else:
                # Visible/Hidden narrow the chip counts; ALL and Nearby keep
                # full family sizes.
                count_filter = status_filter if status_filter in ("VISIBLE", "HIDDEN") else "ALL"
                if sub_tag != getattr(self, "_codex_group_sub_tag", None):
                    self._codex_group_filter = "All"
                    self._codex_group_sub_tag = sub_tag
                    self._codex_group_count_status = count_filter
                    self._rebuild_codex_group_bar(sub_tag, count_filter)
                elif count_filter != getattr(self, "_codex_group_count_status", None):
                    # same families, new counts: re-render the chips
                    self._codex_group_count_status = count_filter
                    self._rebuild_codex_group_bar(sub_tag, count_filter)
        if not group_bar_on:
            if getattr(self, "_codex_group_filter", "All") != "All":
                self._codex_group_filter = "All"
            self._group_sub_bar.setVisible(False)
        else:
            self._group_sub_bar.setVisible(True)
            for key, btn in self._group_sub_btns.items():
                is_target = (key == "All" and self._codex_group_filter == "All") \
                    or (key == self._codex_group_filter)
                btn.blockSignals(True)
                btn.setChecked(is_target)
                btn.blockSignals(False)
