"""Item-page list support: row roles, display constants, quick-tab sync,
the matched-stat tag delegate, flow layout, and the stat-card builder.
The Drops From body lives in drops.py and the UPGRADE LADDER matrix in
upgrades_matrix.py, so each file stays under the line budget.
"""
from __future__ import annotations

from functools import lru_cache

from PySide6 import QtCore, QtGui, QtWidgets

from ... import theme
from ...layout import (CardScroll, FlowLayout, FlowRow, HScrollCard,
                       WrapLabel, clear_layout)
from ....data import items as idata
from ....data import names
from ..tile_delegate import paint_accent_header, paint_tag_chips


# toggle / marker glyphs shared by the items pages — the right chevron (a
# collapsed row that opens to the right, like the In Recipes drawer), the
# down/up triangles for expand/collapse blocks (▾ on a collapsed header
# invites expansion, ▴ on an expanded one invites collapse), and the ◆
# that marks rift-acquired gear
GLYPH_RIGHT = "▸"
GLYPH_DOWN = "▾"
GLYPH_UP = "▴"
GLYPH_RIFT = "◆"


# --- quick gear-category tabs -------------------------------------------
# tab label -> category value: the app speaks "Jewelry" (the main page's
# equipment-tab name) where the data layer calls the category "Accessory".
# "@dungeon" is not a data category — the Dungeons tab swaps in the faction
# accordion view instead of the item list.
QUICK_TABS = (("Weapons", "Weapons"), ("Armor", "Armor"),
              ("Jewelry", "Accessory"), ("Dungeons", "@dungeon"))
CAT_LABEL = {"Accessory": "Jewelry"}   # category display names
# jewelry slots read as Ring / Neck / Trinket rather than the raw
# 'GearFinger' style ids — used for the type chips and sub-sections
_SLOT_LABELS = {"GearFinger": "Ring", "GearNeck": "Neck",
                "GearTrinket": "Trinket"}


def slot_label(t: str) -> str:
    return _SLOT_LABELS.get(t, names.humanize(t))


# weapon variants -> family: all axes / swords / maces share one chip so
# the chip row stays short (Fighter's 10 weapon types collapse to 4)
_WEAPON_FAMILIES = {"Axe": "Axes", "GreatAxe": "Axes",
                     "DualAxes": "Axes", "Sword": "Swords",
                     "GreatSword": "Swords", "DualSwords": "Swords",
                     "Mace": "Maces", "GreatMace": "Maces",
                     "DualMaces": "Maces"}


def type_family(t: str) -> str:
    """The family key a weapon type belongs to (filtering and chips use
    families)."""
    return _WEAPON_FAMILIES.get(t, t)


def family_label(t: str) -> str:
    """Display label for a type's family ('DualAxes' -> 'Axes')."""
    return _WEAPON_FAMILIES.get(t, slot_label(t))


def class_type_map() -> dict[str, set[str]]:
    """Class -> gear families visible to it: equipable + universal ones."""
    items = idata.items()
    universal = {type_family(it.get("type")) for it in items
                 if idata.is_gear(it) and not it.get("classes")}
    out: dict[str, set[str]] = {}
    for cls in idata.gear_classes():
        usable = {type_family(it.get("type")) for it in items
                  if cls in (it.get("classes") or [])}
        out[cls] = usable | universal
    return out


def family_counts(cls: str = "", only: bool = False, mn: str = "",
                  rating: str = "") -> dict[str, int]:
    """Family -> gear item count for the type chips, mirroring the gear-tab
    matcher (class, Only, Min floor, rating)."""
    out: dict[str, int] = {}
    for it in idata.items():
        if not idata.is_gear(it) \
                or idata.category(it.get("type")) == "Crafting":
            continue
        if idata.is_shop_item(it.get("id")):
            continue              # cash-shop cosmetics never show in the list
        if mn and idata.category(it.get("type")) != "Accessory" \
                and rarity_rank(idata.item_display_rarity(it.get("id") or "")) \
                < rarity_rank(mn):
            continue
        if rating and rating not in idata.gear_ratings(it.get("id") or ""):
            continue
        classes = it.get("classes") or []
        if classes:
            if cls and cls not in classes:
                continue         # not usable by the picked class
            if only and cls and len(classes) > 1:
                continue         # Only: dual-class gear drops out
        fam = type_family(it.get("type"))
        out[fam] = out.get(fam, 0) + 1
    return out
# compact stat names for the upgrade-gain chips / search tags — short
# forms keep the step cells narrow
CHIP_SHORT = {
    "Armor Penetration": "A.Pen", "Magic Penetration": "M.Pen",
    "Critical": "Crit", "Fervor": "Fervor", "Health": "HP",
    "Dexterity": "Dex", "Strength": "Str", "Faith": "Faith",
    "Intelligence": "Int", "Vitality": "Vit", "Parry": "Parry",
    "Armor": "Armor", "Spirit": "Spirit", "Will": "Will"}
# list-row roles (single source of truth for the page and its helpers)
ID_ROLE = QtCore.Qt.UserRole          # item id on every data row
HEADER_ROLE = QtCore.Qt.UserRole + 1  # category section rows
CAT_ROLE = QtCore.Qt.UserRole + 2     # category on every list row
TAG_ROLE = QtCore.Qt.UserRole + 3     # matched-stat labels for tag chips
TYPE_HEADER_ROLE = QtCore.Qt.UserRole + 4  # type sub-section rows
TYPE_ROLE = QtCore.Qt.UserRole + 5         # item type on data + header rows

HEADER_CLASS_ROLE = QtCore.Qt.UserRole + 8  # per-class count on a header


def max_item_level(it: dict) -> int:
    """The item's max iLevel — its fully upgraded value at the level it can
    actually reach. Drives the jewelry sort and tile badge."""
    fixed = idata.item_fixed_level(it)
    lvl = fixed if fixed else idata.item_scale_max_level(it.get("id") or "")
    path = idata.upgrade_path(
        it, level=lvl,
        rarity=idata.item_display_rarity(it.get("id") or ""))
    return path[-1] if path else 0


_RARITY_RANK = {"Common": 0, "Uncommon": 1, "Rare": 2, "Epic": 3,
                "Legendary": 4}


def rarity_rank(rarity: str) -> int:
    """Numeric rarity order for the Min floor filter (Common 0 ..
    Legendary 4)."""
    return _RARITY_RANK.get(rarity or "", 0)


def type_header_label(typ: str, count: int) -> str:
    """Type section text: 'SWORD · 12' (one section per gear type)."""
    base = slot_label(typ).upper()
    return f"{base} · {count}" if count else base


def chip_style(color: str, size: int = 11) -> str:
    """A value chip's stylesheet: tinted background, matching border,
    rounded corners. Shared by the Enchants page's signed stat cells and
    rarity trades."""
    return (f"color:{color};font-family:\"{theme.MONO_FONT}\", "
            "\"Consolas\", monospace;"
            f"font-size:{size}px;font-weight:700;"
            f"background:{theme.with_alpha(color, 25)};"
            f"border:1px solid {theme.with_alpha(color, 90)};"
            "border-radius:3px;padding:1px 5px;")


# --- tile-grid sizing -------------------------------------------------
# icon-above-name tiles (the codex look); type headers are full-width items
# whose huge width makes the icon grid wrap to a fresh row (the classic
# grouped-icon-view trick: a header wider than the viewport takes its line)
TILE_W = 74             # tile width — 5 fit per row in the list column
TILE_ICON = 64        # icon size inside the tile (matches setIconSize)
TILE_TEXT_MAX = 4       # hard cap — longer names elide instead of growing
# header class-line font: fixed PIXEL size (DPI-independent)
_CLASS_FONT_PX = 11
HEADER_W = 3000         # header width — wider than any viewport
HEADER_H = 26           # header row height (single-line: title only)
HEADER_EXTRA_CLASS = 20 # extra height for the per-class count line


def tile_height(name: str, font: QtGui.QFont | None = None) -> int:
    """Tile height: icon + the wrapped name (1-4 lines), measured with
    QTextLayout so the count never clips the last line."""
    f = font or QtWidgets.QApplication.font()
    fm = QtGui.QFontMetrics(f)
    avail = TILE_W - 12 - 4     # 6px padding each side, minus safety
    tl = QtGui.QTextLayout(name or "", f)
    tl.beginLayout()
    lines = 0
    while True:
        ln = tl.createLine()
        if not ln.isValid():
            break
        ln.setLineWidth(avail)
        lines += 1
    tl.endLayout()
    lines = min(max(lines, 1), TILE_TEXT_MAX)
    return TILE_ICON + 6 + lines * fm.lineSpacing() + 16


def prewarm_tiles(parent=None, batch: int = 160) -> QtCore.QTimer | None:
    """Warm the Items page's tile icons off the critical path, chunked so
    startup never blocks on them. Returns the driving timer (None when
    there's nothing to warm); the caller keeps it alive."""
    from ....data import icons
    from ....data import items as idata
    from ... import theme
    items = [it for it in idata.items() if is_listable_item(it)]
    if not items:
        return None
    timer = QtCore.QTimer(parent)
    timer.setInterval(0)
    idx = 0

    def _tick():
        nonlocal idx
        end = min(idx + batch, len(items))
        for it in items[idx:end]:
            iid = it["id"]
            rar = idata.item_display_rarity(iid)
            icons.tile("item", iid, TILE_ICON, theme.rarity_color(rar))
        idx = end
        if idx >= len(items):
            timer.stop()

    timer.timeout.connect(_tick)
    timer.start()
    return timer


class TileDelegate(QtWidgets.QStyledItemDelegate):
    """Paints the item tile grid: type headers as full-width accent bars,
    item tiles as the codex-style icon-above-name, with the matched-stat
    chips at the tile's top-right on search results."""

    def paint(self, painter, option, index):
        if index.data(TYPE_HEADER_ROLE):
            segs = index.data(HEADER_CLASS_ROLE) or ()
            paint_accent_header(painter, option, index.data() or "", segs)
            return
        opt = QtWidgets.QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        widget = option.widget
        style = widget.style() if widget else QtWidgets.QApplication.style()
        style.drawControl(QtWidgets.QStyle.CE_ItemViewItem, opt, painter,
                          widget)
        tags = index.data(TAG_ROLE) or ()
        if tags:
            paint_tag_chips(painter, option, tags, CHIP_SHORT)


def quick_pick(page, text: str) -> None:
    """Quick-tab click: set gear category filter and scroll list to top."""
    cat = dict(QUICK_TABS)[text]
    page._items_category = "" if page._items_category == cat else cat
    page._items_type_filter.clear()
    for chip in getattr(page, "_items_type_chips", {}).values():
        chip.setChecked(False)
    # the Dungeons tab swaps the faction accordion in place of the tile list
    on = page._items_category == "@dungeon"
    if hasattr(page, "_items_dungeon_view"):
        page._items_dungeon_view.setVisible(on)
        page._items_list.setVisible(not on)
        if on:
            # first show (or returning from another tab): make sure the
            # right pane shows the active faction's gear card
            page._items_dungeon_view.ensure_card()
    page._items_refilter()
    if hasattr(page, "_items_list"):
        page._items_list.scrollToTop()


def sync_quick_tabs(page) -> None:
    """Check the quick tab matching the current category filter, else none."""
    if not hasattr(page, "_items_quick"):
        return
    cat = getattr(page, "_items_category", "")
    for text, c in QUICK_TABS:
        if c == cat:
            page._items_quick.setCurrentText(text)
            return
    page._items_quick.clear()


def rebuild_filter_chips(box: QtWidgets.QWidget, lay, active: list,
                         on_clear, clear_all: bool = False) -> None:
    """Rebuild the removable filter-chip row: one chip per active filter
    plus a Clear all link. `active` is [(control, label, value)]; the row
    hides itself when nothing is active unless `clear_all`."""
    clear_layout(lay)
    for entry in active:
        combo, label, val = entry[0], entry[1], entry[2]
        chip = QtWidgets.QPushButton(f"{label.upper()} · {val}  ×")
        chip.setCursor(QtCore.Qt.PointingHandCursor)
        chip.setStyleSheet(
            f"QPushButton {{ background: {theme.with_alpha(theme.ACCENT, 16)}; "
            f"color: {theme.TEXT}; border: 1px solid {theme.with_alpha(theme.ACCENT, 55)}; "
            "border-radius: 4px; padding: 3px 9px; font-size: 10px; font-weight: 600; }\n"
            f"QPushButton:hover {{ background: {theme.with_alpha(theme.ACCENT, 32)}; }}")

        def _clear_chip(_, ctl=combo):
            if hasattr(ctl, "setCurrentIndex"):
                ctl.setCurrentIndex(0)
            else:
                ctl.clear()

        chip.clicked.connect(_clear_chip)
        lay.addWidget(chip)
    if clear_all:
        clear = QtWidgets.QPushButton("Clear all")
        clear.setCursor(QtCore.Qt.PointingHandCursor)
        clear.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {theme.MUTED}; "
            "border: 0; padding: 3px 6px; font-size: 10px; font-weight: 600; }\n"
            f"QPushButton:hover {{ color: {theme.ACCENT}; }}")
        clear.clicked.connect(on_clear)
        lay.addWidget(clear)
    box.setVisible(bool(active) or clear_all)


# mounts/gliders and the two collection critters live in the Codex
# Collection tab — the Items list doesn't carry them (they have no
# drops/recipes here; the codex card is the info)
CODEX_ONLY_TYPES = frozenset({"Mount", "GearGlider", "Collection"})


def in_codex_collection(typ: str | None) -> bool:
    """True for a type that lives in the Codex Collection tab instead of the
    Items list: mounts, gliders and the collection critters."""
    return (typ or "") in CODEX_ONLY_TYPES


def is_enchant_item(it: dict) -> bool:
    """True for items owned by the Enchants tab: the enchant scrolls
    (ScrollOf*), the Jeweller gem augments (Cut stones, Cursed Eyes) and
    the demon-gear conversions (DemonGearUpgrade*). Other augment families
    stay on the Items list."""
    iid = str(it.get("id") or "")
    if iid.startswith(("ScrollOf", "DemonGearUpgrade")):
        return True
    return iid in {g["item"] for g in idata.gem_augments()}


def enchant_depth(stat: str) -> int:
    """How many enchant-database items touch a stat (scrolls, gems,
    conversions), for the gear page's ENCHANTS chips."""
    n = int(stat in idata.enchant_scrolls())
    n += sum(1 for c in idata.corrupted_scrolls()
             if stat in (c["stat"], c["penalty"]))
    n += sum(1 for g in idata.gem_augments() if stat in g["stats"])
    n += sum(1 for c in idata.enchant_conversions()
             if stat in (c["source"], c["target"]))
    return n


@lru_cache(maxsize=None)
def _listable(item_id: str) -> bool:
    """Cached per-id core of is_listable_item — the build checks every
    item up to three times and the catalog is static. Drops truthiness
    uses has_drops, never the copy-returning shown_drops."""
    it = idata.item(item_id) or {}
    if in_codex_collection(it.get("type")):
        return False
    if is_enchant_item(it):
        return False            # the Enchants tab owns these
    if (it.get("type") or "") == "Currency" \
            and not idata.has_drops(item_id):
        return False            # currency counters that never drop
    if item_id.startswith("LP_"):
        return False            # Lost Package scan rows — no name or info
    nm = it.get("name") or ""
    if nm == item_id or (nm.startswith("[") and nm.endswith("]")):
        # raw-id placeholder name — keep when the row has drops/recipes
        if not (idata.has_drops(item_id) or idata.recipe(item_id)
                or idata.recipe_unlocked_by(item_id)):
            return False
    return True


def is_listable_item(it: dict) -> bool:
    """True when the Items list should carry the catalog row: gear, mats
    and consumables with real info to show. False for codex-only types,
    enchant items, currency counters that never drop, and unreachable scan
    artifacts with raw-id names and no acquisition data."""
    return _listable(it.get("id") or "")



