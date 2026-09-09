"""Item-page list support: row roles, display constants, quick-tab sync,
the matched-stat tag delegate, flow layout, and the stat-card builder.
The Drops From body lives in drops.py and the UPGRADE LADDER matrix in
upgrades_matrix.py, so each file stays under the line budget.
"""
from __future__ import annotations

import sys
from functools import lru_cache

from PySide6 import QtCore, QtGui, QtWidgets

from ... import theme
from ...layout import CardScroll, FlowLayout, FlowRow, WrapLabel, clear_layout
from ....data import items as idata
from ....data import names
from ....rules import RARITY_RANK
from ..tile_delegate import paint_accent_header, paint_tag_chips

# Re-exported from ui/layout.py because the item page's siblings and the tests
# import them THROUGH here (`from .support import FlowLayout`), which is exactly
# what a linter cannot see: without __all__ the four read as unused imports.
# clear_layout is imported for this module's own use and stays out of the list.
__all__ = ["CardScroll", "FlowLayout", "FlowRow", "WrapLabel"]


# toggle / marker glyphs shared by the items pages — the right chevron (a
# collapsed row that opens to the right, like the In Recipes drawer), the
# down/up triangles for expand/collapse blocks (▾ on a collapsed header
# invites expansion, ▴ on an expanded one invites collapse), and the ◆
# that marks rift-acquired gear
GLYPH_RIGHT = "▸"
GLYPH_DOWN = "▾"
GLYPH_UP = "▴"
GLYPH_RIFT = "◆"


# the dungeon factions, in the Dungeons tab's canonical order, with their
# emoji — shared by that tab's faction chips and the Enchants page's
# Infusions columns. heroic_infusions() sorts alphabetically (Crimson ends
# up second there), so the DISPLAY order is this tuple's job, not the data
# layer's, and a faction from a future patch lands after these instead of
# vanishing. Nightling is the 2026 patch's Demon faction: the Infusion pool
# derives it from the item ids (it is literally named "Nightling" there),
# while the Dungeons tab reads its gear group under the item sheet's id
# "Demon" — both spellings map to the same blood-drop glyph and slot.
FACTION_ORDER = ("Bee", "Kobold", "Manfish", "Crimson", "Demon",
                 "Nightling")
FACTION_EMOJI = {"Bee": "🐝", "Kobold": "⛏", "Manfish": "🐟",
                 "Crimson": "🍷", "Demon": "🩸", "Nightling": "🩸"}

# item-sheet faction id -> the name the game shows. The 2026 patch renamed
# the Demon faction to Nightling (its infusion patterns and set pieces read
# "Nightling"), but the item sheet still keys the gear by "Demon", so the
# data key and the display name are kept apart here.
FACTION_LABEL = {"Demon": "Nightling"}


def faction_label(name: str) -> str:
    """Display name for a faction key ("Demon" -> "Nightling")."""
    return FACTION_LABEL.get(name, name)


def faction_rank(name: str) -> int:
    """Sort key for FACTION_ORDER; any faction the tuple doesn't know
    ranks last."""
    return (FACTION_ORDER.index(name) if name in FACTION_ORDER
            else len(FACTION_ORDER))


# --- quick gear-category tabs -------------------------------------------
# tab label -> category value: the app speaks "Jewelry" (the main page's
# equipment-tab name) where the data layer calls the category "Accessory".
# "@dungeon" is not a data category — the Dungeons tab swaps in the faction
# accordion view instead of the item list. "@items" is the same kind of
# sentinel: the Items tab shows every non-gear catalog row, so the quick
# category carries no meaning there (the matcher switches on it).
QUICK_TABS = (("Weapons", "Weapons"), ("Armor", "Armor"),
              ("Jewelry", "Accessory"), ("Dungeons", "@dungeon"))
# the Items tab's sentinel quick-category value — every NON-gear catalog row
ITEMS_TAB = "@items"
CAT_LABEL = {"Accessory": "Jewelry"}   # category display names

# The Gear page's top-level tab strip. Loadout is gated, not deleted: the
# scoring model behind it is being rebuilt against the character skill /
# talent tree (docs/LOADOUT_REDESIGN.md), and the current one is a stat sum
# with a phrase list bolted on, which cannot see a class's token economy —
# a Mage's Spark gauge, a Warrior's Rage, a Priest's Prayers — and so cannot
# tell a build that works from one that starves.
#
# The gate follows the devicons precedent (`nav_registry.dev_icons_enabled`):
# present in a dev checkout, absent in a frozen build, one flag to reverse.
# The code, its ~2.2k lines and its tests all stay exactly where they are,
# so the rebuild starts from a warm checkout rather than a cold one. The page
# body is already lazy (`_items_ensure_loadout`), so an unreachable tab costs
# nothing at runtime either way.
# Merchants is NOT here. It started as a sixth top-level tab, which was two
# levels of navigation for a question the Items page already owns — what does
# each vendor stock — and it made the strip wide enough to crowd the tabs a
# player actually uses. It is now a chip in the Items tab's own category row
# (`@merchants` in `_page_all_items_view`), where it filters like Unreleased
# does, and the per-vendor view it holds is unchanged.
GEAR_TABS = ("Gear", "Items", "Loadout", "Enchants", "Farm")


def gear_tabs() -> tuple[str, ...]:
    """The Gear page's tab strip, Loadout included only where it is
    reachable. See `GEAR_TABS` for why it is gated rather than removed."""
    if loadout_enabled():
        return GEAR_TABS
    return tuple(t for t in GEAR_TABS if t != "Loadout")


def loadout_enabled() -> bool:
    """True when the Loadout tab is reachable. A dev checkout always has it;
    a frozen build never does. A build flag overrides, so the tab can be
    forced OFF in a dev run without editing code (the tab is under rewrite
    and a stale one is worse than none)."""
    import os
    flag = os.environ.get("FAREVER_LOADOUT", "").strip().lower()
    if flag in ("0", "off", "false", "no"):
        return False
    if flag in ("1", "on", "true", "yes"):
        return True
    return not getattr(sys, "frozen", False)# jewelry slots read as Ring / Neck / Trinket rather than the raw
# 'GearFinger' style ids — used for the type chips and sub-sections
_SLOT_LABELS = {"GearFinger": "Ring", "GearNeck": "Neck",
                "GearTrinket": "Trinket"}


def slot_label(t: str) -> str:
    return _SLOT_LABELS.get(t, names.humanize(t))


def pool_row_label(item: dict) -> str:
    """The text a tile row shows for an item.

    `item.json` carries "Chaotic Gear Cache" for BOTH `Rift_Gear_Cache` (Rare,
    the one vendors actually sell) and `HM_Demon_Cache_25` (Epic, the fixed-25
    Hardmode set). Two boxes, identical words, different tier, contents and
    reachability — so a list that shows the raw name renders them
    indistinguishable, and the Unreleased chip ends up naming the very box a
    vendor is standing behind.

    `cache_display_name` exists to break exactly that tie, but it was only
    reached from the cache helpers, never from the tile rows, which is why the
    collision survived. It applies here for LISTS only; a box's own item page
    still shows the game's real name, which is the sheet's own and the one
    the player will read in their inventory.

    ONLY boxes are renamed. `cache_display_name` falls back to the id for
    anything it does not recognise, so calling it on every item turned every
    tile into its raw id — which is how the dungeon sets' Rare/Epic twins
    ("Royal Chamber Helmet") stopped matching each other.
    """
    from ....data.items import sources

    iid = item.get("id") or ""
    if iid and iid in sources.heroic_cache_spec():
        return sources.cache_display_name(iid)
    return item.get("name") or iid


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


def family_counts(cls: str = "", only: bool = False, rarities=(),
                  rating: str = "", mode: str = "gear") -> dict[str, int]:
    """Family -> gear item count for the type chips, mirroring the gear-tab
    matcher (class, Only, the rarity button set, rating). `mode` mirrors
    _items_match's gear gate: "items" (the Items tab) inverts it — non-gear
    rows plus the Crafting-category cloth/leather mats the gear tab drops."""
    out: dict[str, int] = {}
    for it in idata.items():
        if mode == "items":
            if idata.is_gear(it) \
                    and idata.category(it.get("type")) != "Crafting":
                continue
        elif not idata.is_gear(it) \
                or idata.category(it.get("type")) == "Crafting":
            continue
        if is_hidden_shop_item(it):
            continue              # the shop stock the list never carries
        if not is_listable_item(it):
            continue              # codex-only rows the list never carries
        if not rarity_passes(it.get("id") or "", rarities):
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


# Re-exported from the shared rule module (same ascending ranks catalog uses).
_RARITY_RANK = RARITY_RANK


def rarity_rank(rarity: str) -> int:
    """Numeric rarity order for rarity comparisons (Common 0 ..
    Legendary 4)."""
    return _RARITY_RANK.get(rarity or "", 0)


# the Rarity buttons (the pinned row above the list): exactly ONE is lit,
# and the lit tier is a plain filter — Rare shows only the rare gear, Epic
# only the epic gear. No button is a floor, so no tier drags the tiers
# above it in (see ItemFiltersMixin._items_toggle_rarity).
# Every tier the gear data holds gets a button where the tab has it
# (ascending): Common is the starter clothes plus one practice sword, so it
# only appears on Weapons/Armor — Jewelry and the dungeon pools have none.
# Rare is the tier at rest — the default look. No Legendary button: no gear
# rolls that tier, so it could never filter anything.
RARITY_BUTTONS = ("Common", "Uncommon", "Rare", "Epic")
RARITY_DEFAULT = frozenset({"Rare"})


def rarity_passes(item_id: str, rarities=()) -> bool:
    """Tier gate shared by the browse matcher, the type-chip counts and the
    Dungeons tab. The Rarity buttons leave exactly one tier in here, so it
    reads as a single-tier filter; an empty set means no tier gate at all.
    Every category filters on its real tiers, jewelry included — its
    Uncommon pieces have a button of their own."""
    if not rarities:
        return True
    return idata.item_display_rarity(item_id) in rarities


@lru_cache(maxsize=None)
def category_item_ids(category: str, mode: str = "gear") -> tuple[str, ...]:
    """The item ids a Quick category actually carries: the browse tabs'
    listable gear rows, or the faction drop pools the Dungeons tab renders.
    Backs both the Rarity buttons and their counts.
    An empty category — the active Quick tab clicked off, when the list shows
    every tab at once — carries the whole equipment pool, so the Rarity
    buttons keep real counts there instead of reading '· 0'.
    The Items tab (mode="items") carries the complement: every listable row
    the gear tab drops — non-gear catalog rows keyed off their broad
    category, plus the two cloth/leather mats that are gear-typed but
    Crafting-category (the gear tab is equipment only)."""
    if mode == "items":
        ids: list[str] = []
        for it in idata.items():
            iid = it.get("id") or ""
            if is_hidden_shop_item(it) or not is_listable_item(it):
                continue
            if idata.is_gear(it) \
                    and idata.category(it.get("type")) != "Crafting":
                continue
            if category and category != "@items" \
                    and idata.category(it.get("type")) != category:
                continue
            ids.append(iid)
        return tuple(ids)
    if category == "@dungeon":
        ids: list[str] = []
        for g in idata.dungeon_drop_groups():
            ids.extend(g["shared"])
            ids.extend(idata.epic_pieces(g))
            for b in g["bosses"]:
                ids.extend(b["weapons"])
        return tuple(dict.fromkeys(i for i in ids if i))
    out: list[str] = []
    for it in idata.items():
        iid = it.get("id") or ""
        if not idata.is_gear(it) or is_hidden_shop_item(it) \
                or not is_listable_item(it):
            continue
        cat = idata.category(it.get("type"))
        if cat == "Crafting":
            continue              # the gear tab is equipment only
        if category and cat != category:
            continue
        out.append(iid)
    return tuple(out)


@lru_cache(maxsize=None)
def category_rarities(category: str, mode: str = "gear") -> frozenset[str]:
    """The rarities the current tab's items actually roll — the Rarity
    buttons don't carry dead tiers (no weapon in the data is Epic, so the
    Epic button would filter nothing on the Weapons tab). The check is
    cosmetic only: a tier absent here can't change any row."""
    return frozenset(idata.item_display_rarity(i)
                     for i in category_item_ids(category, mode))


def rarity_counts(cls: str = "", only: bool = False, rating: str = "",
                  category: str = "", ids=None) -> dict[str, int]:
    """Rarity -> row count for the Rarity buttons ('Rare · 135'): what each
    tier HOLDS once the class / Only / rating chips are applied. The rarity
    buttons themselves are not applied, so every tier keeps its own number
    whatever is checked — the numbers always agree with the list once every
    tier is checked.
    `ids` overrides the category's pool — the Dungeons tab counts the
    active faction's drops, not every faction's."""
    out: dict[str, int] = {}
    for iid in (category_item_ids(category) if ids is None else ids):
        it = idata.item(iid) or {}
        classes = it.get("classes") or []
        if classes:
            if cls and cls not in classes:
                continue         # not usable by the picked class
            if only and cls and len(classes) > 1:
                continue         # Only: dual-class gear drops out
        if rating and rating not in idata.gear_ratings(iid):
            continue
        rar = idata.item_display_rarity(iid)
        out[rar] = out.get(rar, 0) + 1
    return out


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


def quick_pick(page, text: str, record_history: bool = True) -> None:
    """Quick-tab click: set gear category filter and scroll list to top."""
    cat = dict(QUICK_TABS)[text]
    old_cat = getattr(page, "_items_category", "")
    if record_history and old_cat != cat:
        hist = getattr(page, "_items_category_history", None)
        if hist is None:
            hist = page._items_category_history = []
        for t, c in QUICK_TABS:
            if c == old_cat:
                hist.append(t)
                break
    page._items_category = "" if page._items_category == cat else cat
    page._items_type_filter.clear()
    for chip in getattr(page, "_items_type_chips", {}).values():
        chip.setChecked(False)
    sync_quick_tabs(page)
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


def is_hidden_shop_item(it: dict) -> bool:
    """THE BROWSE-LIST SHOP CONTRACT: True for a shop row the Items lists
    keep OUT. Every browse list reaches the shop rule through this function
    and nowhere else (`family_counts`, both `category_item_ids` branches and
    `_items_match`).

        hidden(item)  ==  is_shop_item(id)
                          and not (is_gear(item) and has_drops(id))

    The rule is deliberately NOT "is a shop item". That reading was safe only
    while the shop set came from the never-shipped `shop.json` (so `_shop_ids()`
    was empty and only ids literally containing `_shop` were caught — the
    premium store pieces). Wiring the set to the drops index's real shop-ONLY
    rows widened `is_shop_item` to "a counter sells it with no other source",
    and under the old rule that swept up ordinary EQUIPMENT: the Guild
    Merchant's starter and town weapons (`Sword_Craft` "Glory", `Shield_Craft`
    "Dominion", 1000/2000 GOLD). Nine of them silently vanished from the
    Weapons tab.

    The invariant that must survive ANY future change to where the shop source
    comes from: **equipment with a real source is never hidden.** So the
    lists keep sourced vendor gear and drop only the NON-equipment shop stock
    (sigils, caches, tokens, recipe rows — the Codex groups those under
    "Shop") and equipment the scan found NO source for (`Back_Shop` "Farseeker
    Cloak", the premium piece `no_source_reason` already explains).

    `tests/test_items_shop_gate.py` is the contract's guard: it fails if any
    other module under `ui/pages/items/` derives the shop gate again, and it
    holds the equipment invariant against a shop source that matches EVERY
    item — so widening the shop set can never silently drop gear once more.
    """
    iid = str(it.get("id") or "")
    if not idata.is_shop_item(iid):
        return False
    return not (idata.is_gear(it) and idata.has_drops(iid))


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
        # currency counters that never drop — except the Medal of Glory,
        # the Heroic instances' completion reward (the 2026 patch): it is
        # a real farmable goal, not a wallet counter, and its card carries
        # the acquisition note (heroic clears -> MoG vendor in the hubs)
        if item_id != "BadgeOfGlory":
            return False
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



