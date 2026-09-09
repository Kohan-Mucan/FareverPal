"""Item page filters: the search + filter pipeline — refiltering,
per-mode filter-panel rebuilds, the removable chip row, and the quick
Weapons/Armor/Jewelry tabs. The list rows and detail pane live in the
other mixins.
"""
from __future__ import annotations

from . import support
from ....data import items as idata

_ID = support.ID_ROLE
_IS_TYPE_HEADER = support.TYPE_HEADER_ROLE
_CAT = support.CAT_ROLE
_TYPE = support.TYPE_ROLE


def dungeon_group(page) -> dict | None:
    """The active faction's dungeon group (or None off the Dungeons tab) —
    shared by the rarity counts and the Dungeons refresh."""
    dv = getattr(page, "_items_dungeon_view", None)
    groups = getattr(dv, "_groups", None) or {}
    return groups.get(getattr(dv, "_fac", ""))


class _ClassClear:
    """Adapter so the chip row's clear-click drops the active class (and
    the Only chip, which can't mean anything without one)."""

    def __init__(self, page):
        self._page = page

    def clear(self) -> None:
        p = self._page
        if p._items_class:
            p._items_toggle_class(p._items_class, False)


class _RatingClear:
    """Adapter so the chip row's clear-click drops the active rating."""

    def __init__(self, page):
        self._page = page

    def clear(self) -> None:
        p = self._page
        rating = getattr(p, "_items_rating", "")
        chips = getattr(p, "_items_rating_chips", {})
        if rating and rating in chips:
            chips[rating].setChecked(False)


class ItemFiltersMixin:
    def _items_refilter(self, _=None):
        """The gear tab's visibility pass. On the Items tab this is a no-op
        (the pool list has its own pass — see _items_all_refilter)."""
        if getattr(self, "_items_mode", "gear") == "items":
            return
        if not hasattr(self, "_items_list"):
            return
        q = self._items_search.text().strip()
        cat = self._items_category or ""
        cls = getattr(self, "_items_class", "")
        types = getattr(self, "_items_type_filter", set())
        only = getattr(self, "_items_only", False)
        # the tab decides which tiers exist, so an unreachable pick is moved
        # BEFORE the rows are classified (see _items_snap_rarity_to_tab)
        self._items_snap_rarity_to_tab(cat)
        rarities = getattr(self, "_items_rarity_filter", ())
        rating = getattr(self, "_items_rating", "")
        shown = 0
        visible_types: dict[tuple[str, str], int] = {}
        for i in range(self._items_list.count()):
            li = self._items_list.item(i)
            if li.data(_IS_TYPE_HEADER):
                li.setHidden(True)          # headers follow their section
                continue
            ok = self._items_match(li.data(_ID), q, cat, cls, types, only,
                                   rarities, rating)
            li.setHidden(not ok)
            tags = idata.matched_stat_labels(li.data(_ID), q) if ok and q else ()
            if ok and q:
                # skill-name tags only when the query does NOT match the
                # item's own name/id/type: 'bonethrow' tags Cheese Moon,
                # while a generic 'axe' query would spam every axe
                it = idata.item(li.data(_ID)) or {}
                hay = (f"{it.get('name') or ''} {it.get('id') or ''} "
                       f"{it.get('type') or ''}").lower()
                if q.lower() not in hay:
                    tags += idata.matched_skill_labels(li.data(_ID), q)
            li.setData(support.TAG_ROLE, tags)
            if ok:
                shown += 1
                c = li.data(_CAT)
                t = li.data(_TYPE) or ""
                visible_types[(c, t)] = visible_types.get((c, t), 0) + 1
        # type-section headers stay hidden — the list is a flat tile grid
        # (no per-type bars); they remain in the model only as grouping
        # rows the flow skips
        for i in range(self._items_list.count()):
            li = self._items_list.item(i)
            if li.data(_IS_TYPE_HEADER):
                li.setHidden(True)
        if hasattr(self, "_items_search_header"):
            if q:
                # a search looks up the whole scope, so the found count
                # reads against the normal chip-filtered count (the chips
                # without the query) — '12 FOUND · 345 FILTERED'
                filtered = sum(
                    1 for i in range(self._items_list.count())
                    if not self._items_list.item(i).data(_IS_TYPE_HEADER)
                    and self._items_match(
                        self._items_list.item(i).data(_ID), "",
                        cat, cls, types, only, rarities, rating))
                self._items_search_header.set_tag(
                    f"ITEMS {shown} FOUND · {filtered} FILTERED")
            else:
                self._items_search_header.set_tag(
                    f"ITEMS {shown} / "
                    f"{getattr(self, '_items_total', 0) or self._items_list.count()}")
        self._items_update_chips()
        support.sync_quick_tabs(self)
        # matched-skill highlight on the open weapon's skill rows (live, as
        # the query changes — same search feedback as the tile chips)
        self._items_update_skill_highlight()
        # a synchronous re-flow after the visibility pass: Qt's icon-mode
        # hit-test tree goes stale when rows are hidden (the first tile is
        # painted but indexAt misses it), and the deferred layout that
        # follows setHidden doesn't always heal it — this makes the tiles
        # clickable immediately after every filter change
        self._items_list.doItemsLayout()
        self._items_list.viewport().update()   # repaint the stat tag chips

    @staticmethod
    def _items_text_match(item_id: str, it: dict, q: str) -> bool:
        """The free-text half of _items_match (name / id / type / stat /
        skill / class), shared by the gear refilter and the Items tab's
        pool refilter."""
        ql = q.lower()
        hay = (f"{it.get('name') or ''} {it.get('id') or ''} "
               f"{it.get('type') or ''}").lower()
        if ql in hay:
            return True
        return bool(idata.matches_stat(item_id, ql)
                    or idata.matches_skill(item_id, ql)
                    or idata.matches_class(item_id, ql))

    def _items_match(self, item_id: str, q: str, cat: str, cls: str,
                     types: set[str], only: bool = False,
                     rarities=(), rating: str = "", mode: str = "gear") -> bool:
        it = idata.item(item_id) or {}
        if support.is_hidden_shop_item(it):
            return False          # shop stock the list never carries
        if not support.is_listable_item(it):
            return False   # codex-only types + token rows are never listed
        # the Items tab carries the complement: the non-gear pool plus the
        # gear-typed Crafting mats (cloth/leather) the gear tab drops. The
        # gear tabs keep the equipment gate.
        if mode == "items":
            if idata.is_gear(it) \
                    and idata.category(it.get("type")) != "Crafting":
                return False
        else:
            if not idata.is_gear(it):
                return False
            if idata.category(it.get("type")) == "Crafting":
                return False              # gear tab is equipment only
        # a search looks up ALL gear: the class/rating/category/type
        # chips are suspended for the query (not cleared), so clearing
        # the search restores the filters
        if not q:
            if rating:
                # Combat-rating filter: only gear that rolls the picked
                # rating (ArPen / Crit / MaPen / Fervor — from the aptitude
                # curves, gated by faction). Zone gear with no faction rolls
                # no rating, so it never matches a rating chip.
                if rating not in idata.gear_ratings(item_id):
                    return False
            # the Rarity buttons: one tier at a time and strict — the lit
            # tier is the only one that shows (an empty set = no gate).
            if not support.rarity_passes(item_id, rarities):
                return False
            if cat and cat != "@dungeon" \
                    and idata.category(it.get("type")) != cat:
                return False
            classes = it.get("classes") or []
            if not classes:
                # Universal gear (rings, necks, trinkets, some armor — no
                # class field) has NO restriction: it is never filtered out
                # by the class filter panel. Only the category tabs, type
                # chips and search can hide it, like everything else.
                pass
            else:
                if cls and cls not in classes:
                    return False      # not usable by the picked class
                if only and cls and len(classes) > 1:
                    return False      # Only: dual-class gear drops out
            if types and support.type_family(it.get("type")) not in types:
                return False          # family chip covers every variant
        if q:
            if not self._items_text_match(item_id, it, q):
                return False
        return True

    def _items_ensure_loadout(self) -> None:
        """Build the Loadout tab body the first time the tab is opened."""
        if getattr(self, "_items_loadout_built", False):
            return
        lay = getattr(self, "_items_loadout_lay", None)
        if lay is None:
            return
        if hasattr(self, "_page_loadout"):
            self._page_loadout(lay)
        self._items_loadout_built = True

    def _items_ensure_enchants(self) -> None:
        """Build the Enchants tab body the first time the tab is opened.
        The page assembly creates the container + layout up front; only the
        widget build is deferred, so opening the Items page never pays for
        a hidden page."""
        if getattr(self, "_items_enchants_built", False):
            return
        lay = getattr(self, "_items_enchants_lay", None)
        if lay is None:
            return
        self._page_enchants(lay)
        self._items_enchants_built = True

    def _items_ensure_merchants(self) -> None:
        """Build the Merchants body the first time the chip is picked.

        Same lazy contract as Enchants: the container is there from page
        assembly, only the widget build waits. Merchants is the one view whose
        body depends on a data file that a scan rewrites, so it rebuilds on
        every open rather than once - see `_items_show_merchants`.
        """
        if getattr(self, "_merchants_lay", None) is None:
            return
        self._items_show_merchants()
        self._merchants_built = True

    def _items_set_mode(self, mode: str, record_history: bool = True) -> None:
        """Switch the Gear / Items / Loadout / Enchants / Farm tabs.

        Merchants is not one of them - it is a chip on the Items tab, and its
        body is built by `_items_sync_allcat_view` when that chip is picked.
        """
        # the tab we are LEAVING, tracked by the page rather than read off
        # the strip: the strip's currentChanged fires after the clicked
        # segment is already checked, so currentText() reports the tab we
        # are moving TO — every sub-tab hop then recorded nothing and mouse
        # BACK jumped clean out of the page instead of returning to the tab
        # you came from (Craft/Codex/Settings track their own active tab for
        # the same reason)
        old_tab = getattr(self, "_items_active_tab", "")
        if (record_history and old_tab and old_tab != mode
                and hasattr(self, "_nav_history")
                and not getattr(self._nav_history, "_restoring", False)):
            self._nav_history.record(f"gear:{old_tab}", f"gear:{mode}")
        self._items_active_tab = mode
        if mode == "Items":
            # the non-gear pool view swaps into the browse page in place of
            # the gear list — the search box, count header and detail card
            # stay shared, so the list itself is a visibility swap, not a
            # stack change. The stack still has to come back to page 0: the
            # swap happens INSIDE the browse page, so arriving here from
            # Loadout/Enchants/Farm (stack 1/2/3) and only
            # toggling the two lists left the tab looking dead — the row that
            # lit up was on a page the stack wasn't showing
            self._items_mode = "items"
            body = getattr(self, "_items_body", None)
            if body is not None:
                body.setCurrentIndex(0)
            self._items_rebuild_filters()
            self._items_show_all_view(True)
            return
        if self._items_mode == "items":
            # leaving Items: restore the gear mode BEFORE the swap so its
            # refilter pass runs over the gear list again, then let the
            # branch below (Loadout/Enchants/Farm) overwrite it
            self._items_mode = "gear"
            self._items_show_all_view(False)
        if mode == "Loadout":
            self._items_mode = "loadout"
            self._items_ensure_loadout()
            body = getattr(self, "_items_body", None)
            if body is not None:
                body.setCurrentIndex(1)
            return
        if mode == "Enchants":
            self._items_mode = "enchants"
            self._items_ensure_enchants()
            body = getattr(self, "_items_body", None)
            if body is not None:
                body.setCurrentIndex(2)
            return
        if mode == "Farm":
            self._items_mode = "farm"
            body = getattr(self, "_items_body", None)
            if body is not None:
                body.setCurrentIndex(3)
            self._items_show_farm()
            return
        self._items_mode = "gear"
        body = getattr(self, "_items_body", None)
        if body is not None:
            body.setCurrentIndex(0)
        self._items_rebuild_filters()
        self._items_refilter()
        if hasattr(self, "_items_list"):
            self._items_list.scrollToTop()

    def _items_rebuild_filters(self) -> None:
        """Rebuild the labeled filter grid for the current mode using the
        pre-built permanent Field widgets (no un-parenting, so Qt never
        creates brief top-level OS windows on mode switch)."""
        lay = getattr(self, "_items_filter_lay", None)
        if lay is None:
            return
        while lay.count():
            lay.takeAt(0)
        if getattr(self, "_items_mode", "gear") == "items":
            # the Items tab carries its own category chips above its pool
            # list — the gear class / rating / quick-type panel has no
            # meaning over the non-gear pool, so the whole filter grid
            # (and its parent box) stays hidden here. Leaving Items
            # re-runs this for gear and shows it again.
            for f in (getattr(self, "_items_class_chips_box", None),
                      getattr(self, "_items_rating_box", None),
                      getattr(self, "_items_quick_group", None)):
                if f is not None:
                    f.hide()
            box = getattr(self, "_items_filter_box", None)
            if box is not None:
                box.hide()
            return
        box = getattr(self, "_items_filter_box", None)
        # show() on a still-parentless box spawns a brief top-level OS
        # window mid-build; skip the shows/hides until it joins the page.
        embedded = box is not None and not box.isWindow()
        if embedded:
            box.show()
        all_fields = (
            getattr(self, "_items_class_chips_box", None),
            getattr(self, "_items_rating_box", None),
            getattr(self, "_items_quick_group", None),
        )
        if embedded:
            for f in all_fields:
                if f is not None:
                    f.hide()
        if getattr(self, "_items_class_chips_box", None):
            lay.addWidget(self._items_class_chips_box, 0, 0, 1, 2)
            if embedded:
                self._items_class_chips_box.show()
        if getattr(self, "_items_rating_box", None):
            lay.addWidget(self._items_rating_box, 1, 0, 1, 2)
            if embedded:
                self._items_rating_box.show()
        if getattr(self, "_items_quick_group", None):
            lay.addWidget(self._items_quick_group, 2, 0, 1, 2)
            if embedded:
                self._items_quick_group.show()

    def _dg_refresh(self) -> None:
        """Re-run the class/Only filter over the Dungeons tab: re-fill the
        boss rows (weapon chips) and re-render the open faction card."""
        if getattr(self, "_items_category", "") != "@dungeon" \
                or not hasattr(self, "_items_dungeon_view"):
            return
        dv = self._items_dungeon_view
        fac = getattr(dv, "_fac", "")
        if fac and fac in getattr(dv, "_groups", {}):
            dv._fill_list(fac)
        dv.ensure_card()

    def consume_pane_back(self) -> bool:
        """Mouse-BACK inside the Gear page:
        1. Inside Dungeons: an open item detail returns to the faction sheet.
        2. Inside Dungeons: if at faction sheet and factions were stepped through,
           returns to the previous faction.
        3. Returns to the previous Quick category if one was switched from.
        """
        if getattr(self, "_items_category", "") == "@dungeon":
            if getattr(self, "_dg_pane_state", "") == "item":
                dv = getattr(self, "_items_dungeon_view", None)
                last = getattr(self, "_dg_last_group", None)
                if not last and dv is not None:
                    fac = getattr(dv, "_fac", "")
                    last = getattr(dv, "_groups", {}).get(fac)
                if last:
                    from .dungeon_view import show_faction
                    # keep the boss the user was looking at (the pane carries
                    # that boss's Epic heroic drops)
                    sel = getattr(dv, "_sel", None) if dv is not None else None
                    boss_id = (sel[1] if sel and sel[0] == last.get("faction")
                               else "")
                    show_faction(self, last, boss_id)
                    if dv is not None and getattr(dv, "_fac", "") != last.get("faction"):
                        dv._pick(last.get("faction"), record_history=False)
                    return True
            dv = getattr(self, "_items_dungeon_view", None)
            if dv is not None and getattr(dv, "_faction_history", None):
                prev_fac = dv._faction_history.pop()
                dv._pick(prev_fac, record_history=False)
                return True
        cat_hist = getattr(self, "_items_category_history", None)
        if cat_hist:
            prev_cat = cat_hist.pop()
            support.quick_pick(self, prev_cat, record_history=False)
            return True
        return False

    def _items_update_chips(self) -> None:
        """Rebuild the active-filter row above the list: the pinned Rarity
        buttons (one at a time), a removable chip for the live search query,
        and Clear all whenever any filter is active. Only the picked Quick
        category's gear-type chips show."""
        if not hasattr(self, "_items_chips_lay"):
            return
        self._items_sync_rarity_chips()
        cat = getattr(self, "_items_category", "")
        mode = getattr(self, "_items_mode", "gear")
        self._items_sync_rarity_visibility(cat, mode)
        self._items_sync_rarity_counts(cat)
        if mode == "items":
            # the Items tab: the class / rating / quick-type panel has no
            # meaning over the non-gear pool (its own category chips live
            # above its list), so the filter panel drops to nothing along
            # with the pinned Rarity row
            box = getattr(self, "_items_type_chips_box", None)
            if box is not None:
                box.hide()
            for f in (getattr(self, "_items_class_chips_box", None),
                      getattr(self, "_items_rating_box", None),
                      getattr(self, "_items_quick_group", None)):
                if f is not None:
                    f.hide()
            toggle = getattr(self, "_items_rarity_toggle_box", None)
            if toggle is not None:
                toggle.hide()
            active = []
            q = self._items_search.text().strip()
            if q:
                active.append((self._items_search, "Search", q))
            clear_all = bool(q)
            flow = getattr(self, "_items_chips_flow", None)
            if flow is not None:
                support.rebuild_filter_chips(
                    flow, self._items_chips_lay, active,
                    self._items_clear_all_filters, clear_all=clear_all)
            if hasattr(self, "_items_chips_box"):
                self._items_chips_box.setVisible(bool(active) or clear_all)
            return
        if cat == "@dungeon":
            # the Dungeons accordion has no item rows / type chips — it
            # carries its own filtered tiles, both tiers at once, so the
            # Rarity buttons would be dead weight here and stay hidden. The
            # removable chips DO show (the class/rating panel above filters
            # these tiles): they get the full row to themselves, so nothing
            # overlaps. Clear all stays scoped to this tab — the global one
            # would reset the category and leave Dungeons entirely.
            box = getattr(self, "_items_type_chips_box", None)
            if box is not None:
                box.hide()
            toggle = getattr(self, "_items_rarity_toggle_box", None)
            if toggle is not None:
                toggle.hide()
            active = []
            cls = getattr(self, "_items_class", "")
            if cls:
                active.append((_ClassClear(self), "Class",
                               idata.class_label(cls)))
            rating = getattr(self, "_items_rating", "")
            if rating:
                active.append((_RatingClear(self), "Rating", rating))
            clear_all = bool(cls or getattr(self, "_items_only", False)
                             or rating)
            flow = getattr(self, "_items_chips_flow", None)
            if flow is not None:
                support.rebuild_filter_chips(
                    flow, self._items_chips_lay, active,
                    self._dg_clear_filters, clear_all=clear_all)
            if hasattr(self, "_items_chips_box"):
                self._items_chips_box.setVisible(bool(active) or clear_all)
            return
        cls = getattr(self, "_items_class", "")
        class_types = getattr(self, "_class_types", {})
        fam_types = getattr(self, "_items_family_type", {})
        # the type chips only load once a Quick category is picked — like a
        # drop list, they're the submenu of the Weapons/Armor/Jewelry tabs.
        # Each chip carries its item count ('Back · 24'), matching the
        # list's BACK · 24 header, and the counts track every live filter
        # (class, Only, the Rarity buttons, a picked rating) so the chip
        # number always matches how many rows clicking it would show.
        counts = support.family_counts(
            cls, getattr(self, "_items_only", False),
            getattr(self, "_items_rarity_filter", ()),
            getattr(self, "_items_rating", ""),
            mode=getattr(self, "_items_mode", "gear"))
        for fam, chip in getattr(self, "_items_type_chips", {}).items():
            rep = fam_types.get(fam, fam)
            cat_ok = bool(cat) and idata.category(rep) == cat
            cls_ok = not cls or fam in class_types.get(cls, ())
            chip.setVisible(cat_ok and cls_ok)
            chip.setText(f"{support.family_label(rep)} · {counts.get(fam, 0)}")
        box = getattr(self, "_items_type_chips_box", None)
        if box is not None:
            box.setVisible(bool(cat))
        active = []
        q = self._items_search.text().strip()
        if q:
            active.append((self._items_search, "Search", q))
        if cls and not q:
            # the active class as a removable chip — the same label as the
            # class chip in the filter panel (Warrior), cleared the same way;
            # hidden while a search runs (the search suspends the chips)
            active.append((_ClassClear(self), "Class",
                           idata.class_label(cls)))
        clear_all = (bool(q)  # a typed search is a user filter too
                     or bool(getattr(self, "_items_type_filter", ()))
                     or bool(getattr(self, "_items_category", ""))
                     or bool(getattr(self, "_items_class", ""))
                     or bool(getattr(self, "_items_only", False))
                     or bool(getattr(self, "_items_rating", "")))
        # the Rarity buttons are NOT in clear_all: their all-checked state
        # is the gear list's default look, and they have their own row.
        # Clear all resets them to that default.
        support.rebuild_filter_chips(self._items_chips_flow,
                                    self._items_chips_lay, active,
                                    self._items_clear_filters,
                                    clear_all=clear_all)
        toggle = getattr(self, "_items_rarity_toggle_box", None)
        if toggle is not None:
            toggle.show()
        self._items_chips_box.show()

    def _items_toggle_type(self, typ: str, on: bool) -> None:
        """A gear-type chip toggled: single-select — picking one type clears
        the previous selection; re-clicking the active chip clears the
        filter. The sweep's programmatic unchecks are idempotent, so they
        can't clobber the just-picked chip."""
        if on:
            self._items_type_filter.clear()
            for other, chip in getattr(self, "_items_type_chips", {}).items():
                if other != typ and chip.isChecked():
                    chip.setChecked(False)
            self._items_type_filter.add(typ)
        else:
            self._items_type_filter.discard(typ)
        self._items_refilter()

    def _items_sync_only_label(self) -> None:
        """The Only chip's label follows the picked class ('Warrior Only'
        with Warrior selected, plain 'Only' otherwise)."""
        chip = getattr(self, "_items_only_chip", None)
        if chip is None:
            return
        cls = getattr(self, "_items_class", "")
        chip.setText(f"{idata.class_label(cls)} Only"
                     if self._items_only and cls else "Only")

    def _items_toggle_class(self, cls: str, on: bool) -> None:
        """A class chip toggled: single-select — picking a class prunes the
        weapon-type chips to that class's weapons; re-clicking the active
        class clears the filter (and the Only chip). The sweep's
        programmatic unchecks are ignored so they can't clobber the pick."""
        if on:
            self._items_class = cls
            for other, chip in getattr(self, "_items_class_chips", {}).items():
                if other != cls and chip.isChecked():
                    chip.setChecked(False)
        elif self._items_class == cls:
            self._items_class = ""
            self._items_only = False
            chip = getattr(self, "_items_only_chip", None)
            if chip is not None:
                chip.setChecked(False)
        else:
            return                       # unchecking a non-selected chip
        self._items_sync_only_label()
        self._items_type_filter.clear()
        for chip in getattr(self, "_items_type_chips", {}).values():
            chip.setChecked(False)
        self._items_refilter()
        # the Dungeons tab filters BOTH columns by the class/Only chips
        self._dg_refresh()

    def _items_toggle_only(self, on: bool) -> None:
        """The Only chip toggled: with a class picked, every shown item
        becomes one only that class can use; without a class the click is
        rejected and it snaps back off."""
        if on and not getattr(self, "_items_class", ""):
            chip = getattr(self, "_items_only_chip", None)
            if chip is not None:
                chip.setChecked(False)
            return
        self._items_only = on
        self._items_sync_only_label()
        self._items_refilter()
        self._dg_refresh()

    def _items_snap_rarity_to_tab(self, cat: str) -> None:
        """Move the lit tier to one the current tab actually carries: a tab
        with no such tier (Uncommon lit, then Weapons) used to filter every
        row away with no visible button left to explain it, so the pick
        becomes the tab's own tier — Rare where the tab has one, else the
        best it does have. Called ahead of the row pass, so the snapped
        filter is the one the rows are classified with."""
        tiers = support.RARITY_BUTTONS if not cat \
            else support.category_rarities(cat)
        picked = self._items_rarity_pick()
        if not tiers or not picked or picked in tiers:
            return
        self._items_rarity_filter = {
            "Rare" if "Rare" in tiers
            else max(tiers, key=support.rarity_rank)}
        self._items_sync_rarity_chips()

    def _items_sync_rarity_visibility(self, cat: str,
                                      mode: str = "gear") -> None:
        """Show only the Rarity buttons the current tab can actually match:
        the data rolls no Epic weapon, so the Epic button is hidden on the
        Weapons tab instead of sitting there filtering nothing."""
        if mode == "items":
            # the pool is not tier-driven — the Rarity row is dead weight
            # here (the Dungeons tab hides it for the same reason)
            for chip in getattr(self, "_items_rarity_toggle_chips", {}).values():
                chip.setVisible(False)
            return
        tiers = support.RARITY_BUTTONS if not cat \
            else support.category_rarities(cat)
        for rar, chip in getattr(self, "_items_rarity_toggle_chips", {}).items():
            chip.setVisible(rar in tiers)

    def _items_sync_rarity_counts(self, cat: str) -> None:
        """Write each Rarity button's row count ('Rare · 135') — how many rows
        of the current tab carry that tier under the live class / Only /
        rating chips, so the number says what the tier holds before it is
        toggled. The Dungeons row hides (both tiers always show there), so
        its counts skip the rating chip — the dungeon tiles themselves still
        follow it, like the class/Only chips."""
        rating = "" if cat == "@dungeon" else getattr(self, "_items_rating", "")
        ids = None
        if cat == "@dungeon":
            # the Dungeons tab shows one faction at a time, so its numbers
            # tally that faction's drops rather than every faction's — two
            # rows down the same faction: this holds every dungeon of the
            # family. The heroic EPIC set is part of it: without those
            # pieces the Epic button reads '1' while the pane shows a set
            g = dungeon_group(self)
            if g is not None:
                ids = (list(g["shared"])
                       + [w for b in g["bosses"] for w in b["weapons"]]
                       + list(idata.epic_pieces(g)))
        counts = support.rarity_counts(
            getattr(self, "_items_class", ""),
            bool(getattr(self, "_items_only", False)), rating, cat, ids)
        for rar, chip in getattr(self, "_items_rarity_toggle_chips", {}).items():
            chip.setText(f"{rar} · {counts.get(rar, 0)}")

    def _items_rarity_pick(self) -> str:
        """The picked tier — the one chip that reads as checked. The buttons
        are single-select and strict, so the set holds one tier; the lowest
        rank is returned should a stale state ever hold more."""
        sel = getattr(self, "_items_rarity_filter", ()) or ()
        return min(sel, key=support.rarity_rank) if sel else ""

    def _items_sync_rarity_chips(self) -> None:
        """Check the picked Rarity button and uncheck the others — the row is
        single-select, so exactly ONE is always lit. An empty set is repaired
        to the default tier rather than left as a row with nothing lit (the
        filter and the buttons can't disagree). Silent (no toggled
        re-entry), so programmatic changes (Clear all, restore-default)
        can't double-fire a toggle."""
        if getattr(self, "_items_rarity_filter", None) is not None \
                and not self._items_rarity_filter:
            self._items_rarity_filter = set(support.RARITY_DEFAULT)
        pick = self._items_rarity_pick()
        for rar, chip in getattr(self, "_items_rarity_toggle_chips", {}).items():
            set_silent = getattr(chip, "set_checked_silent", None)
            if callable(set_silent):
                set_silent(rar == pick)
            else:
                try:
                    chip.blockSignals(True)
                    chip.setChecked(rar == pick)
                finally:
                    chip.blockSignals(False)

    def _items_toggle_rarity(self, rarity: str, on: bool) -> None:
        """A Rarity button toggled: ONE at a time and never none. Picking a
        tier lights it and clears the others (Epic on means Rare off), and
        the lit tier is a plain filter — Rare shows only the rare gear, Epic
        only the epic gear. No button is a floor, so no tier drags the ones
        above it along. The lit button can't be switched off: it is re-lit,
        so the row always keeps exactly one tier and the list never falls
        back to every tier by accident (Clear all restores the default
        tier)."""
        if getattr(self, "_items_rarity_filter", None) is None:
            return
        if on:
            self._items_rarity_filter = {rarity}
        elif not self._items_rarity_filter:
            self._items_rarity_filter = set(support.RARITY_DEFAULT)
        # else: an attempt to un-light the picked tier — refused below, where
        # the chips are re-checked from the (unchanged) floor
        self._items_sync_rarity_chips()
        self._items_refilter()
        # the Dungeons tab's weapon/gear tiles follow the rarity buttons too
        self._dg_refresh()

    def _items_toggle_rating(self, rating: str, on: bool) -> None:
        """A Rating chip toggled: single-select — picking one rating clears
        the previous; re-clicking the active rating clears it entirely.
        The sweep's programmatic unchecks are ignored."""
        if on:
            self._items_rating = rating
            for other, chip in getattr(self, "_items_rating_chips", {}).items():
                if other != rating and chip.isChecked():
                    chip.setChecked(False)
        elif self._items_rating == rating:
            self._items_rating = ""
        else:
            return                       # unchecking a non-selected chip
        self._items_refilter()
        # the Dungeons tab's boss rows and faction tiles follow the rating
        # chips too (off the tab this is a no-op — see _dg_refresh)
        self._dg_refresh()

    def _items_clear_filters(self) -> None:
        """Reset every active filter for the current mode at once."""
        self._items_category = ""
        self._items_class = ""
        self._items_only = False
        for chip in getattr(self, "_items_class_chips", {}).values():
            chip.setChecked(False)
        only_chip = getattr(self, "_items_only_chip", None)
        if only_chip is not None:
            only_chip.setChecked(False)
        self._items_sync_only_label()
        # the Rarity buttons' default tier is the default look, not a user
        # choice — clearing restores it
        self._items_rarity_filter = set(support.RARITY_DEFAULT)
        # the Rating filter is a user choice — clearing drops it
        self._items_rating = ""
        for chip in getattr(self, "_items_rating_chips", {}).values():
            chip.setChecked(False)
        self._items_type_filter.clear()
        for chip in getattr(self, "_items_type_chips", {}).values():
            chip.setChecked(False)
        self._items_search.clear()
        self._items_refilter()
        self._dg_refresh()

    def _dg_clear_filters(self, _=None) -> None:
        """Clear the Dungeons-tab filters (class, Only, rating) without
        leaving the tab — the global Clear all resets the category too,
        which would drop the user out of Dungeons entirely."""
        self._items_class = ""
        self._items_only = False
        for chip in getattr(self, "_items_class_chips", {}).values():
            chip.setChecked(False)
        only_chip = getattr(self, "_items_only_chip", None)
        if only_chip is not None:
            only_chip.setChecked(False)
        self._items_sync_only_label()
        self._items_rating = ""
        for chip in getattr(self, "_items_rating_chips", {}).values():
            chip.setChecked(False)
        self._items_refilter()
        self._dg_refresh()
