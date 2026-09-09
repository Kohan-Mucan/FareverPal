"""Item database page (the new item lookup, offline).

Searchable catalog of every droppable item from the bundled `item_drops.json`
scan, with real Drops From sources and, for gear, the max-level-25 iLevel
tiers from the gear-scaling block. Contains the page assembly
(ItemPageBase), grouped list population (ItemListMixin) and the Items
tab's non-gear pool view (mats, consumables, misc) that swaps into the
browse page in place of the gear list.
"""
from __future__ import annotations

from collections import Counter

from PySide6 import QtCore, QtGui, QtWidgets

from ... import theme
from ... import components as C
from ...layout import make_scroll
from . import support
from ....data import icons
from ....data import items as idata

_ID = support.ID_ROLE
_IS_TYPE_HEADER = support.TYPE_HEADER_ROLE
_CAT = support.CAT_ROLE
_TYPE = support.TYPE_ROLE


def _header_class_segments(rows: list[dict]) -> tuple:
    """Per-class count segments for a type section's header line — one
    (label, color, count) per class with items, plus a muted 'Universal'
    segment for class-free gear. Zero-count classes drop out; an
    all-universal section returns ()."""
    cnt: Counter = Counter()
    for it in rows:
        cls = it.get("classes")
        if cls:
            cnt.update(cls)
        else:
            cnt[""] += 1
    out = []
    for c in idata.gear_classes():
        n = cnt.get(c, 0)
        if n:
            out.append((idata.class_label(c), theme.class_color(c), n))
    if out and cnt.get("", 0):
        out.append(("Universal", theme.MUTED, cnt[""]))
    return tuple(out)


class ItemListMixin:
    def _items_populate_list(self, all_items: list) -> None:
        """Fill the list once: a non-selectable type section header per gear
        type followed by its items as rarity-colored name tiles."""
        grouped: dict[str, dict[str, list]] = {}
        for it in all_items:
            typ = it.get("type") or ""
            grouped.setdefault(idata.category(typ), {}).setdefault(typ, []).append(it)
        self._items_total = len(all_items)
        for cat in idata.categories():
            types = grouped.get(cat) or {}
            for typ in sorted(types):
                rows = types[typ]
                # every gear section sorts by max item level, best first —
                # that's the rarity order (Epic 320, Rare 290, Uncommon 270,
                # Common 250), so weapons and armor read strongest-to-
                # weakest the same way jewelry does
                rows = sorted(rows, key=support.max_item_level,
                              reverse=True)
                th = QtWidgets.QListWidgetItem(
                    support.type_header_label(typ, len(rows)))
                th.setData(_IS_TYPE_HEADER, True)
                th.setData(_CAT, cat)
                th.setData(_TYPE, typ)
                # per-class count line on the header ('Warrior 4 · Rogue 3 ·
                # Universal 2') for sections with class gear; all-universal
                # sections (rings/necks) stay single-line
                segs = _header_class_segments(rows)
                th.setData(support.HEADER_CLASS_ROLE, segs)
                th.setFlags(QtCore.Qt.ItemIsEnabled)   # non-selectable section
                # full-width row: the tile grid wraps to a fresh line after it
                th.setSizeHint(QtCore.QSize(
                    support.HEADER_W,
                    support.HEADER_H
                    + (support.HEADER_EXTRA_CLASS if segs else 0)))
                self._items_list.addItem(th)
                for it in rows:
                    li = QtWidgets.QListWidgetItem(support.pool_row_label(it))
                    # the DISPLAY rarity — Guild Merchant gear (the starter
                    # weapons) is sold as Rare by the town vendors, so the
                    # tile shows the shop quality, not the authored starter
                    # rarity
                    rar = idata.item_display_rarity(it.get("id") or "")
                    col = theme.rarity_color(rar)
                    li.setForeground(QtGui.QColor(col))
                    # codex-style tile: icon on top, name underneath — the
                    # IconMode grid draws the icon above the wrapped text.
                    # The tile height grows with the name: the text area
                    # expands to fit (3 rows by default, 4 max) instead of
                    # clipping long names. The row starts with the cheap
                    # rarity-tinted placeholder (tile with no game art —
                    # cached per color, ~5 pixmaps total); the real icon is
                    # swapped in by _items_fill_icons after the page shows,
                    # so the first paint doesn't wait on ~830 atlas crops.
                    li.setIcon(QtGui.QIcon(
                        icons.tile(None, None, support.TILE_ICON, col)))
                    li.setSizeHint(QtCore.QSize(
                        support.TILE_W,
                        support.tile_height(it.get("name") or it["id"],
                                            self._items_list.font())))
                    # the name hugs the icon like the codex cards: without
                    # AlignTop, Qt centers the text in the area below the
                    # icon, so a short name floats in the middle of the tile
                    li.setTextAlignment(QtCore.Qt.AlignHCenter
                                        | QtCore.Qt.AlignTop)
                    li.setData(_ID, it["id"])
                    li.setData(_CAT, cat)
                    li.setData(_TYPE, typ)
                    self._items_list.addItem(li)
        self._items_fill_icons()

    def _items_fill_icons(self, batch: int = 160) -> None:
        """Swap the placeholder tiles for the real game icons in batches (~`batch`
        rows per 0-ms timer tick), so the page's first paint never blocks on
        the ~830 atlas crops. The timer yields to the event loop, so the
        tiles stream in over a few frames."""
        self._items_fill_icons_for(self._items_list, batch)

    def _items_populate_pool(self, lw: QtWidgets.QListWidget,
                             ids: list[str]) -> None:
        """Fill a tile list from an id pool, one type-section header per
        item type followed by its items — the gear population's shape, but
        sourced from ids (the Items tab's non-gear pool). Sorted by display
        name inside each type (mats have no iLevel ladder to sort by)."""
        grouped: dict[str, list] = {}
        for iid in ids:
            it = idata.item(iid) or {}
            grouped.setdefault(it.get("type") or "", []).append(it)
        for typ in sorted(grouped):
            rows = sorted(grouped[typ],
                          key=lambda r: (r.get("name") or r["id"]).lower())
            th = QtWidgets.QListWidgetItem(
                support.type_header_label(typ, len(rows)))
            th.setData(_IS_TYPE_HEADER, True)
            th.setData(_CAT, idata.category(typ))
            th.setData(_TYPE, typ)
            th.setFlags(QtCore.Qt.ItemIsEnabled)   # non-selectable section
            th.setSizeHint(QtCore.QSize(
                support.HEADER_W, support.HEADER_H))
            lw.addItem(th)
            for it in rows:
                li = QtWidgets.QListWidgetItem(support.pool_row_label(it))
                rar = idata.item_display_rarity(it["id"])
                col = theme.rarity_color(rar)
                li.setForeground(QtGui.QColor(col))
                # start on the cheap rarity-tinted placeholder; the real
                # icon swaps in via the batched fill below
                li.setIcon(QtGui.QIcon(
                    icons.tile(None, None, support.TILE_ICON, col)))
                li.setSizeHint(QtCore.QSize(
                    support.TILE_W,
                    support.tile_height(support.pool_row_label(it),
                                        lw.font())))
                li.setTextAlignment(QtCore.Qt.AlignHCenter
                                    | QtCore.Qt.AlignTop)
                li.setData(_ID, it["id"])
                li.setData(_CAT, idata.category(typ))
                li.setData(_TYPE, typ)
                lw.addItem(li)
        self._items_fill_icons_for(lw)

    def _items_fill_icons_for(self, lw: QtWidgets.QListWidget,
                              batch: int = 160) -> None:
        """`_items_fill_icons`' list-parameterized core: swap a tile list's
        placeholder tiles for the real game icons in batches (one ~0-ms
        timer tick per batch), so the page's first paint never blocks on
        the atlas crops. The gear list's _items_fill_icons delegates here."""
        rows = [lw.item(i) for i in range(lw.count())
                if not lw.item(i).data(_IS_TYPE_HEADER)]
        if not rows:
            return
        timer = QtCore.QTimer(lw)
        timer.setInterval(0)
        idx = 0

        def _tick():
            nonlocal idx
            try:
                end = min(idx + batch, len(rows))
                for li in rows[idx:end]:
                    it = idata.item(li.data(_ID)) or {}
                    rar = idata.item_display_rarity(it.get("id") or "")
                    col = theme.rarity_color(rar)
                    li.setIcon(QtGui.QIcon(
                        icons.tile("item", it["id"], support.TILE_ICON,
                                   col)))
                idx = end
                if idx >= len(rows):
                    timer.stop()
                    # icon swaps interleave with the icon-mode flow's
                    # layout and leave its hit-test tree stale — the
                    # first tile is painted but unclickable (indexAt
                    # misses it, so right-click only finds the viewport).
                    # Rebuild the flow once, after all the icons are in.
                    lw.doItemsLayout()
            except RuntimeError:
                timer.stop()     # the page was torn down mid-fill

        timer.timeout.connect(_tick)
        timer.start()

    def _items_all_refilter(self, _=None) -> None:
        """The Items tab's visibility pass over its own list: the category
        chips (single-select, All at rest) + the shared search query. The
        matched-stat tag chips apply here too, so a search reads the same
        on both tabs."""
        lw = getattr(self, "_items_all_list", None)
        if lw is None:
            return
        q = self._items_search.text().strip()
        cat = getattr(self, "_items_allcat", "@items")
        if cat == "@merchants":
            # the pool list is hidden behind the per-vendor view, so there is
            # nothing to pass over — and the header must keep saying what the
            # Merchants body says rather than dropping to "ITEMS 0 / N" for a
            # list nobody is looking at. `_items_sync_allcat_view` owns the
            # header while that chip is lit.
            return
        # the Unreleased chip is a pseudo-category: it is not a `category()`
        # value, it is a set of boxes the data defines but nothing hands out
        un = frozenset(b["cache"] for b in idata.unreleased_caches()) \
            if cat == "@unreleased" else frozenset()
        shown = 0
        visible_types: dict[str, int] = {}
        for i in range(lw.count()):
            li = lw.item(i)
            if li.data(_IS_TYPE_HEADER):
                li.setHidden(True)          # the grid stays flat
                continue
            it = idata.item(li.data(_ID)) or {}
            ok = (not q or self._items_text_match(li.data(_ID), it, q)) \
                and (cat == "@items"
                     or (li.data(_ID) in un if un else li.data(_CAT) == cat))
            li.setHidden(not ok)
            tags = idata.matched_stat_labels(li.data(_ID), q) \
                if ok and q else ()
            li.setData(support.TAG_ROLE, tags)
            if ok:
                shown += 1
                t = li.data(_TYPE) or ""
                visible_types[t] = visible_types.get(t, 0) + 1
        # rewrite each type header's count to its visible rows ('ORE · 4')
        for i in range(lw.count()):
            li = lw.item(i)
            if li.data(_IS_TYPE_HEADER):
                typ = li.data(_TYPE) or ""
                li.setText(support.type_header_label(
                    typ, visible_types.get(typ, 0)))
        self._items_search_header.set_tag(
            f"ITEMS {shown} / {getattr(self, '_items_all_total', 0)}")
        # keep the gear filter UI off this tab: the pool has its own
        # category chips, so the class / rating / quick-type panel and the
        # pinned Rarity row stay hidden here (their gear-tab state is
        # untouched — leaving Items rebuilds them).
        if hasattr(self, "_items_update_chips"):
            try:
                self._items_update_chips()
            except Exception:
                pass
        # same stale-hit-test heal as the gear pass (hidden rows leave the
        # icon-mode flow tree pointing at painted-but-unclickable tiles)
        lw.doItemsLayout()
        lw.viewport().update()


class ItemPageBase:
    def _page_gear(self):
        page, v = self._page_container()

        if not idata.available():
            v.addWidget(C.InfoCard("archive", "Data missing",
                                   "item_drops.json isn't bundled in this build — "
                                   "re-run the data compile."))
            return page

        all_items = idata.items()
        # codex-only types (mounts/gliders/critters) live in the Codex
        # Collection tab, and pure-token scan artifacts have nothing to
        # show — the list carries only real listable items
        all_items = [it for it in all_items if support.is_listable_item(it)]

        # Gear vs Items vs Loadout vs Enchants vs Farm — Loadout is gated
        # (support.gear_tabs) while its scoring model is rebuilt. Merchants is
        # not a tab: it is a chip inside the Items tab.
        self._items_mode = "gear"
        # the tab the strip is on, tracked separately from _items_mode: the
        # mode is a VIEW (items/gear/loadout/…) while this is the strip's
        # identity, and it is what nav history records a sub-tab hop from
        # (see _items_set_mode — the strip reports the new tab too early)
        self._items_active_tab = "Gear"
        self._items_tabs = C.SegmentedControl(list(support.gear_tabs()),
                                              current="Gear")
        self._items_tabs.currentChanged.connect(self._items_set_mode)
        v.addWidget(self._items_tabs)

        cols = QtWidgets.QHBoxLayout()
        cols.setSpacing(16)
        left = QtWidgets.QVBoxLayout()
        left.setSpacing(10)
        self._items_search = C.SearchInput(
            "Search name, type, stat or skill…",
            on_text_changed=self._items_refilter)
        self._items_search.setFixedHeight(34)
        # which refilter the shared search box drives right now — the Items
        # tab's swap rewires it (see _items_show_all_view)
        self._items_search_target = self._items_refilter

        self._items_stat_mode_btn = None

        self._items_search_header = C.SectionHeader("Search",
                                                    tag=f"ITEMS {len(all_items)} TOTAL")
        self._items_header_reflow(self._items_search_header)
        left.addWidget(self._items_search_header)

        # Gear category is a plain value driven by the Quick tabs below — no
        # dropdown, the tabs ARE the category picker (Weapons/Armor/Jewelry)
        self._items_category = "Weapons"
        # website-style filter panel: each control gets a labeled cell; the
        # grid is rebuilt per mode (gear: class + the Quick group,
        # items: the category cascade dropdown)
        self._items_filter_box = QtWidgets.QWidget()
        self._items_filter_lay = QtWidgets.QGridLayout(self._items_filter_box)
        self._items_filter_lay.setContentsMargins(0, 0, 0, 0)
        self._items_filter_lay.setSpacing(8)
        self._items_filter_lay.setColumnStretch(0, 1)
        self._items_filter_lay.setColumnStretch(1, 1)
        # Class filter: pick your class first and the weapon-type chips
        # collapse to that class's weapons. Single-select; armor and
        # jewelry (no classes field) always show.
        self._items_class = ""
        self._items_only = False
        self._class_types = support.class_type_map()
        self._items_class_chips_box = QtWidgets.QWidget(parent=self._items_filter_box)
        self._items_class_chips_lay = support.FlowLayout(self._items_class_chips_box)
        self._items_class_chips_lay.setContentsMargins(0, 0, 0, 0)
        self._items_class_chips: dict[str, C.FilterChip] = {}
        for cls in idata.gear_classes():
            # the chip shows the game's class name ('Warrior') while the
            # toggle still filters on the raw aptitude ('Fighter')
            chip = C.FilterChip(idata.class_label(cls), checked=False,
                                color=theme.class_color(cls),
                                parent=self._items_class_chips_box)
            chip.toggled.connect(lambda on, c=cls: self._items_toggle_class(c, on))
            self._items_class_chips_lay.addWidget(chip)
            self._items_class_chips[cls] = chip
        # the Only chip: with a class picked, drop dual-class gear so the
        # rows are items only this class can use (universal items always
        # stay). Clicking it without a class is rejected.
        self._items_only_chip = C.FilterChip("Only", checked=False,
                                             parent=self._items_class_chips_box)
        self._items_only_chip.toggled.connect(self._items_toggle_only)
        self._items_class_chips_lay.addWidget(self._items_only_chip)
        # Rarity buttons: single-select FLOOR, built once in the pinned row
        # above the list (see _items_chips_box) — the filter panel doesn't
        # repeat them. Exactly one is ALWAYS lit: picking a tier clears the
        # others and shows that tier and above (EPIC = the epic gear), and
        # the lit one can't be switched off (Clear all restores it). Rare is
        # the default floor, so the list opens on Rare+ (Uncommon/Common
        # hidden).
        self._items_rarity_filter = set(support.RARITY_DEFAULT)
        # the combat-rating filter: single-select chips (ArPen / Crit /
        # MaPen / Fervor) — every rated piece rolls exactly one, so picking
        # one shows only gear with that rating. Re-clicking clears it.
        self._items_rating = ""
        self._items_rating_box = QtWidgets.QWidget(parent=self._items_filter_box)
        rl = QtWidgets.QVBoxLayout(self._items_rating_box)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(6)
        self._items_rating_chips_lay = support.FlowLayout()
        self._items_rating_chips_lay.setContentsMargins(0, 0, 0, 0)
        self._items_rating_chips: dict[str, C.FilterChip] = {}
        for rating in idata.RATING_STATS:
            chip = C.FilterChip(rating, checked=False,
                                color=theme.stat_color(rating),
                                parent=self._items_rating_box)
            chip.toggled.connect(
                lambda on, r=rating: self._items_toggle_rating(r, on))
            self._items_rating_chips_lay.addWidget(chip)
            self._items_rating_chips[rating] = chip
        rl.addLayout(self._items_rating_chips_lay)
        # Quick group: the category tabs (Weapons/Armor/Jewelry) with the
        # gear-type chips beneath them — the tabs ARE the section, and the
        # type chips load only once a category is picked
        self._items_quick_group = QtWidgets.QWidget(parent=self._items_filter_box)
        gv = QtWidgets.QVBoxLayout(self._items_quick_group)
        gv.setContentsMargins(0, 0, 0, 0)
        gv.setSpacing(8)
        self._items_quick_body = QtWidgets.QWidget(self._items_quick_group)
        bv = QtWidgets.QVBoxLayout(self._items_quick_body)
        bv.setContentsMargins(0, 0, 0, 0)
        bv.setSpacing(8)
        self._items_quick = C.UnderlineTabs([t for t, _ in support.QUICK_TABS],
                                            current="Weapons",
                                            parent=self._items_quick_body)
        self._items_quick.currentChanged.connect(
            lambda text: support.quick_pick(self, text))
        bv.addWidget(self._items_quick)
        # gear type filter: one toggle chip per weapon FAMILY (all axes /
        # swords / maces share a chip) plus one per armor slot and jewelry
        # piece, so the row stays short
        self._items_type_filter: set[str] = set()
        self._items_type_chips_box = QtWidgets.QWidget(self._items_quick_body)
        self._items_type_chips_lay = support.FlowLayout(self._items_type_chips_box)
        self._items_type_chips_lay.setContentsMargins(0, 0, 0, 0)
        self._items_type_chips: dict[str, C.FilterChip] = {}
        self._items_family_type: dict[str, str] = {}   # family -> a type in it
        for t in idata.gear_slots():
            if idata.category(t) == "Crafting":
                continue                       # no cloth/leather in gear
            fam = support.type_family(t)
            if fam in self._items_type_chips:
                continue                       # already covered by the family
            chip = C.FilterChip(support.family_label(t), checked=False,
                                parent=self._items_type_chips_box)
            chip.toggled.connect(lambda on, f=fam: self._items_toggle_type(f, on))
            self._items_type_chips_lay.addWidget(chip)
            self._items_type_chips[fam] = chip
            self._items_family_type[fam] = t
        bv.addWidget(self._items_type_chips_box)
        gv.addWidget(self._items_quick_body)
        self._items_rebuild_filters()

        self._items_list = self._items_make_tile_list()
        self._items_populate_list(all_items)
        self._items_list.currentItemChanged.connect(self._items_show)
        self._items_list.itemClicked.connect(self._items_show)

        left.addWidget(self._items_filter_box)
        # the row above the list: the pinned Rarity buttons (one at a time,
        # always present — the quickest place to drop a tier) beside the
        # removable per-filter chips + Clear all
        self._items_chips_box = QtWidgets.QWidget()
        cbl = QtWidgets.QHBoxLayout(self._items_chips_box)
        cbl.setContentsMargins(0, 0, 0, 0)
        cbl.setSpacing(6)
        self._items_rarity_toggle_box = QtWidgets.QWidget(self._items_chips_box)
        # the buttons keep their own width: on the Dungeons tab the removable
        # chips are gone, and the row's slack would stretch them across it
        self._items_rarity_toggle_box.setSizePolicy(
            QtWidgets.QSizePolicy.Maximum, QtWidgets.QSizePolicy.Preferred)
        tbl = QtWidgets.QHBoxLayout(self._items_rarity_toggle_box)
        tbl.setContentsMargins(0, 0, 0, 0)
        tbl.setSpacing(6)
        self._items_rarity_toggle_chips: dict[str, C.FilterChip] = {}
        for rar in support.RARITY_BUTTONS:
            chip = C.FilterChip(rar, checked=rar in self._items_rarity_filter,
                                color=theme.rarity_color(rar),
                                parent=self._items_rarity_toggle_box)
            chip.toggled.connect(lambda on, r=rar: self._items_toggle_rarity(r, on))
            tbl.addWidget(chip)
            self._items_rarity_toggle_chips[rar] = chip
        # AlignLeft matters: with the removable chips gone (the Dungeons tab)
        # the row's slack would centre the buttons in the middle of the row
        cbl.addWidget(self._items_rarity_toggle_box, 0,
                      QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        self._items_chips_flow = QtWidgets.QWidget(self._items_chips_box)
        self._items_chips_lay = support.FlowLayout(self._items_chips_flow)
        cbl.addWidget(self._items_chips_flow, 1)
        self._items_chips_box.hide()
        left.addWidget(self._items_chips_box)
        left.addWidget(self._items_list, 1)
        # the Dungeons tab's faction accordion swaps in place of the list
        from .dungeon_view import DungeonView
        self._items_dungeon_view = DungeonView(self)
        self._items_dungeon_view.hide()
        left.addWidget(self._items_dungeon_view, 1)
        # the Items tab's non-gear pool view swaps in the same way: its own
        # tile list + category chips replace the gear list, while the search
        # box, the count header and the detail card stay shared
        self._items_all_view = self._page_all_items_view()
        self._items_all_view.hide()
        left.addWidget(self._items_all_view, 1)
        cols.addLayout(left, 1)

        right = QtWidgets.QVBoxLayout()
        right.setSpacing(12)
        self._items_detail = QtWidgets.QFrame()
        self._items_detail.setObjectName("Card")
        self._items_detail_lay = QtWidgets.QVBoxLayout(self._items_detail)
        self._items_detail_lay.setContentsMargins(16, 12, 16, 12)
        self._items_detail_lay.setSpacing(10)
        self._items_scroll = support.CardScroll(self._items_detail)
        # CardScroll keeps the card at its content height (widgetResizable
        # would stretch it to fill the column, leaving a huge empty card)
        right.addWidget(self._items_scroll, 1)
        cols.addLayout(right, 1)

        # the Enchants tab is a full second page — a stacked widget swaps
        # it in place of the Items/Gear browse layout without rebuilding
        self._items_browse = QtWidgets.QWidget()
        bl = QtWidgets.QVBoxLayout(self._items_browse)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(12)
        bl.addLayout(cols, 1)
        # Loadout tab container (lazy built on first switch)
        self._items_loadout = QtWidgets.QWidget()
        ll = QtWidgets.QVBoxLayout(self._items_loadout)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(16)
        self._items_loadout_lay = ll
        self._items_loadout_built = False
        self._items_loadout_scroll = make_scroll(self._items_loadout, h_scroll=False)

        self._items_enchants = QtWidgets.QWidget()
        el = QtWidgets.QVBoxLayout(self._items_enchants)
        el.setContentsMargins(0, 0, 0, 0)
        el.setSpacing(16)
        # the Enchants tab builds lazily on its first switch, so opening
        # the Items page doesn't pay for a hidden page; only the widget
        # build is deferred
        self._items_enchants_lay = el
        self._items_enchants_built = False
        self._items_enchants_scroll = make_scroll(self._items_enchants, h_scroll=True)
        self._items_body = QtWidgets.QStackedWidget()
        self._items_body.addWidget(self._items_browse)            # 0: Gear + Items
        self._items_body.addWidget(self._items_loadout_scroll)    # 1: Loadout
        self._items_body.addWidget(self._items_enchants_scroll)   # 2: Enchants
        self._items_body.addWidget(self._items_farm_setup())      # 3: Farm
        # no Merchants page here: it is a chip on the Items tab now, and its
        # body is hosted inside that tab's own view (see
        # `_page_all_items_view`).
        self._items_farm_sync_tab()   # the Farm tab shows its count on open
        v.addWidget(self._items_body, 1)

        self._items_refilter()   # gear default: hide non-gear rows up front
        self._items_list.setCurrentItem(None)
        self._items_list.clearSelection()
        self._items_list.setCurrentIndex(QtCore.QModelIndex())
        self._items_show(None)
        page.consume_pane_back = self.consume_pane_back
        return page

    def _items_header_reflow(self, hdr) -> None:
        """Lay out the header bar as: [SEARCH BOX (expanding)] [tick]
        [ITEMS 36 / 829].

        The STATS display-mode toggle is NOT part of this bar: it is built
        with the item card and rides the UPGRADE LADDER matrix's STATS
        header cell (detail.py hands it over as the matrix's stats_control),
        so the guard below only ever fires if a card was already rendered
        when the header is reflowed."""
        lay = hdr.layout()
        while lay.count():
            lay.takeAt(0)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        lay.addWidget(self._items_search, 1)
        if hasattr(hdr, "_tick"):
            lay.addWidget(hdr._tick, 0, QtCore.Qt.AlignVCenter)
        if hasattr(hdr, "_tag"):
            lay.addWidget(hdr._tag, 0, QtCore.Qt.AlignVCenter)
        if getattr(self, "_items_stat_mode_btn", None) is not None:
            lay.addWidget(self._items_stat_mode_btn, 0, QtCore.Qt.AlignVCenter)

    def _items_make_tile_list(self) -> QtWidgets.QListWidget:
        """The codex-style tile grid used by both browse lists (the gear list
        and the Items tab's pool): icon-above-name tiles in columns, with
        the full-width type-section rows wrapping the grid between types.
        Extracted verbatim from the gear build so the Items tab's list is
        the same widget with the same look and scroll behaviour."""
        lw = QtWidgets.QListWidget()
        lw.setViewMode(QtWidgets.QListView.IconMode)
        lw.setResizeMode(QtWidgets.QListView.Adjust)
        lw.setMovement(QtWidgets.QListView.Static)
        lw.setWrapping(True)
        lw.setWordWrap(True)
        # setSpacing MUST be called after setViewMode (setViewMode resets it to 0)
        lw.setSpacing(9)
        # pixel-based scrolling: ScrollPerItem (the Qt default) counts rows,
        # and the mixed header/tile heights make scrolls jump by whole rows
        lw.setVerticalScrollMode(
            QtWidgets.QAbstractItemView.ScrollPerPixel)
        lw.setHorizontalScrollBarPolicy(
            QtCore.Qt.ScrollBarAlwaysOff)
        lw.setIconSize(
            QtCore.QSize(support.TILE_ICON, support.TILE_ICON))
        # no forced minimum height — a fixed floor (430px) made the page's
        # minimum taller than the app viewport and pushed the list around
        lw.setMinimumHeight(0)
        lw.setItemDelegate(support.TileDelegate(lw))
        lw.setStyleSheet(
            f"QListWidget{{background:{theme.PANEL};border:1px solid {theme.BORDER};outline:none;}}"
            f"QListWidget::item{{padding:4px;margin:2px;border-radius:4px;}}"
            f"QListWidget::item:hover{{background:{theme.with_alpha(theme.ACCENT,15)};}}"
            f"QListWidget::item:selected{{background:{theme.with_alpha(theme.ACCENT,30)};"
            f"border:1px solid {theme.ACCENT};color:{theme.TEXT};}}")
        return lw

    # --- Items tab: the non-gear pool (mats, consumables, misc) -----------
    # The list was split in two (gear tab vs items tab), but the rows never
    # left: one list widget's model keeps the whole catalog while each tab
    # shows its half. The gear matcher hides everything non-gear; the Items
    # tab swaps in a second tile list whose pool is the complement, sharing
    # the search box, the count header and the detail card with the gear
    # browse — so it behaves like a second view of one page, not a tab of
    # new widgets.

    def _page_all_items_view(self) -> QtWidgets.QWidget:
        """Build the Items tab's view: the All-category chips row above its
        own tile list. Both are swapped in/out of the left column by
        _items_show_all_view; the search header and detail card stay shared
        with the gear browse."""
        box = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)
        # the category chips: All first, then the categories the non-gear
        # pool actually carries, each with its live count ('Crafting · 158')
        # like the gear tab's type chips
        self._items_allcat_chips_box = QtWidgets.QWidget(box)
        chips_lay = support.FlowLayout(self._items_allcat_chips_box)
        chips_lay.setContentsMargins(0, 0, 0, 0)
        chips_lay.setSpacing(6)
        self._items_allcat = "@items"   # All — the single-select chip state
        self._items_allcat_chips: dict[str, C.FilterChip] = {}
        cats = idata.categories()
        counts: Counter = Counter()
        for iid in support.category_item_ids("", "items"):
            counts[idata.category((idata.item(iid) or {}).get("type"))] += 1
        all_chip = C.FilterChip(f"All · {sum(counts.values())}", checked=True,
                                parent=self._items_allcat_chips_box)
        all_chip.toggled.connect(
            lambda on: self._items_toggle_allcat("@items", on))
        chips_lay.addWidget(all_chip)
        self._items_allcat_chips["@items"] = all_chip
        for cat in cats:
            n = counts.get(cat, 0)
            if not n:
                continue
            chip = C.FilterChip(f"{cat} · {n}", checked=False,
                                parent=self._items_allcat_chips_box)
            chip.toggled.connect(
                lambda on, c=cat: self._items_toggle_allcat(c, on))
            chips_lay.addWidget(chip)
            self._items_allcat_chips[cat] = chip
        # UNRELEASED: the boxes that exist in the game data but that nothing
        # hands out — no vendor, boss, chest or crate references them. They
        # are spread across Misc with everything else unlistable-in-practice,
        # so without their own chip there is no way to see authored-but-
        # unreachable content without reading the item page of each.
        n_un = len(idata.unreleased_caches())
        if n_un:
            chip = C.FilterChip(f"Unreleased · {n_un}", checked=False,
                                color=theme.DIM,
                                parent=self._items_allcat_chips_box)
            chip.setToolTip(
                "Boxes the game data defines but that nothing currently "
                "drops or sells — the contents are listed on each one.")
            chip.toggled.connect(
                lambda on: self._items_toggle_allcat("@unreleased", on))
            chips_lay.addWidget(chip)
            self._items_allcat_chips["@unreleased"] = chip
        # MERCHANTS, last of the pseudo-categories. Its chip and its body live
        # in merchants.py — the concern is a merchant view, and page.py is at
        # its line budget.
        self._items_add_merchants_chip(chips_lay)
        v.addWidget(self._items_allcat_chips_box)
        # the pool list: same tile grid, all rows in (the refilter pass
        # does the hiding per category/search)
        self._items_all_list = self._items_make_tile_list()
        self._items_populate_pool(self._items_all_list,
                                  support.category_item_ids("", "items"))
        self._items_all_list.currentItemChanged.connect(self._items_show)
        self._items_all_list.itemClicked.connect(self._items_show)
        v.addWidget(self._items_all_list, 1)
        # the Merchants body shares this view, hidden until its chip is picked
        self._items_host_merchants(v)
        # the count tag reads data rows only (the type headers are rows too)
        self._items_all_total = sum(
            1 for i in range(self._items_all_list.count())
            if not self._items_all_list.item(i).data(_IS_TYPE_HEADER))
        return box

    def _items_show_all_view(self, on: bool) -> None:
        """Swap the Items tab's view in (on) or back to the gear list. The
        shared search box is rewired onto whichever list is live (tracked
        in _items_search_target), and the query is blanked on every swap
        while nothing is wired — each side then refills its own rows and
        count tag, so a search never leaks across the swap half-applied."""
        target = self._items_all_refilter if on else self._items_refilter
        cur = getattr(self, "_items_search_target", None)
        if cur is not None:
            try:
                self._items_search.textChanged.disconnect(cur)
            except (RuntimeError, TypeError):
                pass
        if self._items_search.text():
            self._items_search.setText("")   # fires nothing: detached
        self._items_all_view.setVisible(on)
        if on:
            # the pool replaces BOTH gear views (list + Dungeons
            # accordion) — arriving from Dungeons with its accordion
            # still shown left both stacked over the pool
            self._items_list.hide()
            dv = getattr(self, "_items_dungeon_view", None)
            if dv is not None:
                dv.hide()
        else:
            self._items_all_view.hide()
            # restore whichever gear view the category calls for
            # (the Dungeons accordion when its tab is live)
            if getattr(self, "_items_category", "") == "@dungeon":
                dv = getattr(self, "_items_dungeon_view", None)
                if dv is not None:
                    dv.show()
                self._items_list.hide()
            else:
                dv = getattr(self, "_items_dungeon_view", None)
                if dv is not None:
                    dv.hide()
                self._items_list.show()
        self._items_search.textChanged.connect(target)
        self._items_search_target = target
        if on:
            # the Merchants chip stays lit across a tab round trip, so the
            # body it picked has to be re-shown on the way back in — without
            # this the pool list returns under a chip that still reads as the
            # active filter.
            self._items_sync_allcat_view()
        target()

    def _items_toggle_allcat(self, cat: str, on: bool) -> None:
        """An Items-tab category chip toggled: single-select over All —
        picking a category clears the others; re-clicking the active chip
        falls back to All. The sweep's programmatic unchecks are ignored."""
        if on:
            self._items_allcat = cat
            for other, chip in self._items_allcat_chips.items():
                if other != cat and chip.isChecked():
                    chip.setChecked(False)
        elif self._items_allcat == cat:
            self._items_allcat = "@items"
            chip = self._items_allcat_chips.get("@items")
            if chip is not None and not chip.isChecked():
                chip.setChecked(True)
            self._items_sync_allcat_view()
            return
        else:
            return
        self._items_sync_allcat_view()
        self._items_all_refilter()

    def _items_clear_all_filters(self, _=None) -> None:
        """Clear all scoped to the Items tab: the shared search box only —
        the global clear (gear category, class, rarity, rating) has no
        meaning over the pool list, and firing it here would re-run the
        gear refilter against a hidden list."""
        self._items_search.clear()

    # gear-stat display mode toggle — the search header's right-side button
    # shows the current value form; clicking cycles Rounded -> Exact and
    # re-renders the open item immediately (the labels and the cycle are the
    # same two modes by construction: the retired 'both' form is neither)
    _STAT_MODE_LABELS = {
        "rounding": "Stats: Rounded",
        "true": "Stats: Exact",
    }
    _STAT_MODE_CYCLE = ("rounding", "true")

    def _items_stat_mode_toggle(self) -> QtWidgets.QPushButton:
        btn = QtWidgets.QPushButton(
            self._STAT_MODE_LABELS.get(idata.stat_display_mode(), "Stats: Rounded"))
        btn.setCursor(QtCore.Qt.PointingHandCursor)
        btn.setToolTip("Gear stat precision: click to switch "
                       "Rounded (16) ↔ Exact (15.987)")
        btn.setStyleSheet(
            f'QPushButton{{color:{theme.ACCENT};'
            f'font-family:"{theme.MONO_FONT}", "Consolas", monospace;'
            f"background:{theme.with_alpha(theme.ACCENT, 14)};"
            f"border:1px solid {theme.with_alpha(theme.ACCENT, 70)};"
            # 4px side padding (was 10px): this button rides the STATS
            # column, so its hint sets the whole column's width — 4px drops
            # the column to the name floor (82px), 2 characters narrower
            "border-radius:4px;padding:2px 4px;font-size:11px;"
            "font-weight:700;letter-spacing:0.5px;}"
            f"QPushButton:hover{{background:{theme.with_alpha(theme.ACCENT, 28)};border-color:{theme.ACCENT};}}")
        btn.clicked.connect(self._items_cycle_stat_mode)
        return btn

    def _items_cycle_stat_mode(self) -> None:
        cur = idata.stat_display_mode()
        nxt = self._STAT_MODE_CYCLE[
            (self._STAT_MODE_CYCLE.index(cur) + 1) % len(self._STAT_MODE_CYCLE)]
        idata.set_stat_display_mode(nxt)
        self._items_stat_mode_btn.setText(self._STAT_MODE_LABELS[nxt])
        self._items_rerender_stat_block()
