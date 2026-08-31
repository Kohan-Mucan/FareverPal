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


class _MinFloor:
    """Adapter so the chip row's clear-click toggles the gear list's Min
    rarity floor between its Rare default and Any."""

    def __init__(self, page):
        self._page = page

    def clear(self) -> None:
        p = self._page
        p._items_min = "" if p._items_min else "Rare"
        p._items_refilter()


class _ClassClear:
    """Adapter so the chip row's clear-click drops the active class (and
    the Only chip, which can't mean anything without one)."""

    def __init__(self, page):
        self._page = page

    def clear(self) -> None:
        p = self._page
        if p._items_class:
            p._items_toggle_class(p._items_class, False)


class ItemFiltersMixin:
    def _items_refilter(self, _=None):
        if not hasattr(self, "_items_list"):
            return
        q = self._items_search.text().strip()
        cat = self._items_category or ""
        cls = getattr(self, "_items_class", "")
        types = getattr(self, "_items_type_filter", set())
        only = getattr(self, "_items_only", False)
        mn = getattr(self, "_items_min", "")
        rating = getattr(self, "_items_rating", "")
        shown = 0
        visible_types: dict[tuple[str, str], int] = {}
        for i in range(self._items_list.count()):
            li = self._items_list.item(i)
            if li.data(_IS_TYPE_HEADER):
                li.setHidden(True)          # headers follow their section
                continue
            ok = self._items_match(li.data(_ID), q, cat, cls, types, only,
                                   mn, rating)
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
                        cat, cls, types, only, mn, rating))
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

    def _items_match(self, item_id: str, q: str, cat: str, cls: str,
                     types: set[str], only: bool = False,
                     mn: str = "", rating: str = "") -> bool:
        if idata.is_shop_item(item_id):
            return False          # cash-shop cosmetics have no drops — not in the list
        it = idata.item(item_id) or {}
        if not support.is_listable_item(it):
            return False   # codex-only types + token rows are never listed
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
            if mn:
                # Min rarity floor: keep gear at/above the picked rarity.
                # Jewelry is exempt (most of it is Uncommon green, so it
                # always shows regardless of rarity).
                if idata.category(it.get("type")) != "Accessory" \
                        and support.rarity_rank(
                            idata.item_display_rarity(item_id)) \
                        < support.rarity_rank(mn):
                    return False
            if cat and idata.category(it.get("type")) != cat:
                return False
            classes = it.get("classes") or []
            if not classes:
                # Universal gear (rings, necks, trinkets, some armor — no
                # class field) has NO restriction: it is never filtered out
                # by the class filter or the Only chip. Only the category
                # tabs, type chips and search can hide it, like everything
                # else.
                pass
            else:
                if cls and cls not in classes:
                    return False      # not usable by the picked class
                if only and cls and len(classes) > 1:
                    return False      # Only: dual-class gear drops out
            if types and support.type_family(it.get("type")) not in types:
                return False          # family chip covers every variant
        if q:
            ql = q.lower()
            hay = (f"{it.get('name') or ''} {it.get('id') or ''} "
                   f"{it.get('type') or ''}").lower()
            if ql not in hay and not idata.matches_stat(item_id, ql) \
                    and not idata.matches_skill(item_id, ql) \
                    and not idata.matches_class(item_id, ql):
                return False
        return True

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

    def _items_set_mode(self, mode: str, record_history: bool = True) -> None:
        """Switch the Gear / Enchants / Farm tabs: Enchants swaps the body
        to the enchant database page, Farm to the saved gear list, Gear
        rebuilds the browse filter panel and refilters."""
        if record_history and hasattr(self, "_nav_history") and not getattr(self._nav_history, "_restoring", False):
            cur_tab = getattr(self, "_items_tabs", None)
            cur_text = cur_tab.currentText() if cur_tab else ""
            if cur_text and cur_text != mode:
                self._nav_history.record(f"gear:{cur_text}", f"gear:{mode}")
        if mode == "Enchants":
            self._items_mode = "enchants"
            self._items_ensure_enchants()
            body = getattr(self, "_items_body", None)
            if body is not None:
                body.setCurrentIndex(1)
            return
        if mode == "Farm":
            self._items_mode = "farm"
            body = getattr(self, "_items_body", None)
            if body is not None:
                body.setCurrentIndex(2)
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
        all_fields = (
            getattr(self, "_items_class_chips_box", None),
            getattr(self, "_items_rating_box", None),
            getattr(self, "_items_quick_group", None),
        )
        for f in all_fields:
            if f is not None:
                f.hide()
        if getattr(self, "_items_class_chips_box", None):
            lay.addWidget(self._items_class_chips_box, 0, 0, 1, 2)
            self._items_class_chips_box.show()
        if getattr(self, "_items_rating_box", None):
            lay.addWidget(self._items_rating_box, 1, 0, 1, 2)
            self._items_rating_box.show()
        if getattr(self, "_items_quick_group", None):
            lay.addWidget(self._items_quick_group, 2, 0, 1, 2)
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
        """Mouse-BACK inside the Dungeons tab: an open item detail returns
        to the faction sheet instead of leaving the page."""
        if getattr(self, "_items_category", "") != "@dungeon":
            return False
        last = getattr(self, "_dg_last_group", None)
        if not last or getattr(self, "_dg_pane_state", "") != "item":
            return False
        from .dungeon_view import show_faction
        show_faction(self, last)
        return True

    def _items_update_chips(self) -> None:
        """Rebuild the active-filter row above the list: a removable chip
        for the live search query plus Clear all whenever any filter is
        active. Only the picked Quick category's gear-type chips show."""
        if not hasattr(self, "_items_chips_lay"):
            return
        cat = getattr(self, "_items_category", "")
        if cat == "@dungeon":
            # the Dungeons accordion has no item rows / type chips
            box = getattr(self, "_items_type_chips_box", None)
            if box is not None:
                box.hide()
            return
        cls = getattr(self, "_items_class", "")
        class_types = getattr(self, "_class_types", {})
        fam_types = getattr(self, "_items_family_type", {})
        # the type chips only load once a Quick category is picked — like a
        # drop list, they're the submenu of the Weapons/Armor/Jewelry tabs.
        # Each chip carries its item count ('Back · 24'), matching the
        # list's BACK · 24 header, and the counts track every live filter
        # (class, Only, the Min floor, a picked rating) so the chip number
        # always matches how many rows clicking it would show.
        counts = support.family_counts(
            cls, getattr(self, "_items_only", False),
            getattr(self, "_items_min", ""),
            getattr(self, "_items_rating", ""))
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
        # the Min rarity floor always surfaces on the gear list so the
        # hidden Uncommon/Common junk is visible at a glance; clicking
        # the chip drops the floor (one-click show-all) and clicking
        # again restores the Rare default
        floor = getattr(self, "_items_min", "") or "Any"
        active.append((_MinFloor(self), "Min rarity", floor,
                       ("Rare floor — hides Uncommon/Common gear junk. "
                        "Click to show it." if floor != "Any" else
                        "No floor — all rarities show. "
                        "Click to restore the Rare default.")))
        clear_all = (bool(q)  # a typed search is a user filter too
                     or bool(getattr(self, "_items_type_filter", ()))
                     or bool(getattr(self, "_items_category", ""))
                     or bool(getattr(self, "_items_class", ""))
                     or bool(getattr(self, "_items_only", False))
                     or bool(getattr(self, "_items_rating", "")))
        # the Min floor is NOT in clear_all: it's the gear list's default
        # state (Rare), surfaced as its own toggle chip — not a user filter
        support.rebuild_filter_chips(self._items_chips_box,
                                    self._items_chips_lay, active,
                                    self._items_clear_filters,
                                    clear_all=clear_all)

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
        # the Min floor is a default (Rare), not a user choice — clearing
        # restores it
        self._items_min = "Rare"
        # the Rating filter is a user choice — clearing drops it
        self._items_rating = ""
        for chip in getattr(self, "_items_rating_chips", {}).values():
            chip.setChecked(False)
        self._items_type_filter.clear()
        for chip in getattr(self, "_items_type_chips", {}).values():
            chip.setChecked(False)
        self._items_search.clear()
        self._items_refilter()
