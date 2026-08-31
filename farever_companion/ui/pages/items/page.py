"""Item database page (the new item lookup, offline).

Searchable catalog of every droppable item from the bundled `item_drops.json`
scan, with real Drops From sources and, for gear, the max-level-25 iLevel
tiers from the gear-scaling block. Contains the page assembly
(ItemPageBase) and grouped list population (ItemListMixin).
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
                    li = QtWidgets.QListWidgetItem(it.get("name") or it["id"])
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
        rows = [self._items_list.item(i)
                for i in range(self._items_list.count())
                if not self._items_list.item(i).data(_IS_TYPE_HEADER)]
        if not rows:
            return
        timer = QtCore.QTimer(self._items_list)
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
                    self._items_list.doItemsLayout()
            except RuntimeError:
                timer.stop()     # the page was torn down mid-fill

        timer.timeout.connect(_tick)
        timer.start()


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

        # Gear vs Enchants vs Farm — the Gear tab (default) swaps in the
        # class/category filters, the Enchants tab is the enchant database,
        # and the Farm tab is the saved gear-farm list
        self._items_mode = "gear"
        self._items_tabs = C.SegmentedControl(["Gear", "Enchants", "Farm"],
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

        self._items_stat_mode_btn = self._items_stat_mode_toggle()

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
        # the Min rarity floor: RARE by default — Uncommon/Common gear is
        # junk and auto-hidden (jewelry is exempt: some dropped green
        # pieces beat crafted). Clear-all restores this default.
        self._items_min = "Rare"
        # the combat-rating filter: single-select chips (ArPen / Crit /
        # MaPen / Fervor) — every rated piece rolls exactly one, so picking
        # one shows only gear with that rating. Re-clicking clears it.
        self._items_rating = ""
        self._items_rating_box = QtWidgets.QWidget(parent=self._items_filter_box)
        rl = QtWidgets.QVBoxLayout(self._items_rating_box)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(6)
        rlbl = QtWidgets.QLabel("Rating")
        rlbl.setObjectName("FieldLabel")
        rl.addWidget(rlbl)
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

        self._items_list = QtWidgets.QListWidget()
        # codex-style tile grid: icon-above-name tiles in columns, with the
        # full-width type-section rows wrapping the grid between types
        self._items_list.setViewMode(QtWidgets.QListView.IconMode)
        self._items_list.setResizeMode(QtWidgets.QListView.Adjust)
        self._items_list.setMovement(QtWidgets.QListView.Static)
        self._items_list.setWrapping(True)
        self._items_list.setWordWrap(True)
        # setSpacing MUST be called after setViewMode (setViewMode resets it to 0)
        self._items_list.setSpacing(9)
        # pixel-based scrolling: ScrollPerItem (the Qt default) counts rows,
        # and the mixed header/tile heights make scrolls jump by whole rows
        self._items_list.setVerticalScrollMode(
            QtWidgets.QAbstractItemView.ScrollPerPixel)
        self._items_list.setHorizontalScrollBarPolicy(
            QtCore.Qt.ScrollBarAlwaysOff)
        self._items_list.setIconSize(
            QtCore.QSize(support.TILE_ICON, support.TILE_ICON))
        # no forced minimum height — a fixed floor (430px) made the page's
        # minimum taller than the app viewport and pushed the list around
        self._items_list.setMinimumHeight(0)
        self._items_list.setItemDelegate(support.TileDelegate(self._items_list))
        self._items_list.setStyleSheet(
            f"QListWidget{{background:{theme.PANEL};border:1px solid {theme.BORDER};outline:none;}}"
            f"QListWidget::item{{padding:4px;margin:2px;border-radius:4px;}}"
            f"QListWidget::item:hover{{background:{theme.with_alpha(theme.ACCENT,15)};}}"
            f"QListWidget::item:selected{{background:{theme.with_alpha(theme.ACCENT,30)};"
            f"border:1px solid {theme.ACCENT};color:{theme.TEXT};}}")
        self._items_populate_list(all_items)
        self._items_list.currentItemChanged.connect(self._items_show)
        self._items_list.itemClicked.connect(self._items_show)

        left.addWidget(self._items_filter_box)
        self._items_chips_box = QtWidgets.QWidget()
        self._items_chips_lay = support.FlowLayout(self._items_chips_box)
        self._items_chips_box.hide()
        left.addWidget(self._items_chips_box)
        left.addWidget(self._items_list, 1)
        # the Dungeons tab's faction accordion swaps in place of the list
        from .dungeon_view import DungeonView
        self._items_dungeon_view = DungeonView(self)
        self._items_dungeon_view.hide()
        left.addWidget(self._items_dungeon_view, 1)
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
        self._items_body.addWidget(self._items_browse)            # 0
        self._items_body.addWidget(self._items_enchants_scroll)   # 1
        self._items_body.addWidget(self._items_farm_setup())      # 2: Farm
        self._items_farm_sync_tab()   # the Farm tab shows its count on open
        v.addWidget(self._items_body, 1)

        self._items_refilter()   # gear default: hide non-gear rows up front
        self._items_list.setCurrentItem(None)
        self._items_list.clearSelection()
        self._items_list.setCurrentIndex(QtCore.QModelIndex())
        self._items_show(None)
        return page

    def _items_header_reflow(self, hdr) -> None:
        """Lay out the header bar as: [SEARCH BOX (expanding)] [tick] [ITEMS 36 / 829] [STATS TOGGLE]"""
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
        lay.addWidget(self._items_stat_mode_btn, 0, QtCore.Qt.AlignVCenter)

    # gear-stat display mode toggle — the search header's right-side button
    # shows the current value form; clicking cycles
    # Rounded -> Exact -> Both and re-renders the open item immediately
    _STAT_MODE_LABELS = {
        "rounding": "Stats: Rounded (16)",
        "true": "Stats: Exact (15.987)",
        "both": "Stats: Both",
    }
    _STAT_MODE_CYCLE = ("rounding", "true", "both")

    def _items_stat_mode_toggle(self) -> QtWidgets.QPushButton:
        btn = QtWidgets.QPushButton(
            self._STAT_MODE_LABELS.get(idata.stat_display_mode(), "Stats: Rounded (16)"))
        btn.setCursor(QtCore.Qt.PointingHandCursor)
        btn.setToolTip("Gear stat precision: click to cycle "
                       "Rounded (16) → Exact (15.987) → Both (16 (15.987))")
        btn.setStyleSheet(
            f'QPushButton{{color:{theme.ACCENT};'
            f'font-family:"{theme.MONO_FONT}", "Consolas", monospace;'
            f"background:{theme.with_alpha(theme.ACCENT, 14)};"
            f"border:1px solid {theme.with_alpha(theme.ACCENT, 70)};"
            "border-radius:4px;padding:2px 10px;font-size:11px;"
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
        # Reset shown id so _items_show immediately re-renders the current item's stats
        self._items_shown_id = None
        self._items_show(self._items_list.currentItem())
        if getattr(self, "_items_mode", "") == "farm":
            self._items_show_farm()
