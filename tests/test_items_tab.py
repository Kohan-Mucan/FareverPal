"""Items-tab tests (headless Qt, offscreen platform).

The Items page's second tab shows the non-gear catalog the Gear tab hides:
materials, consumables, crafting rows and misc (soulstones, caches, tools).
The rows share the search box, count header and detail card with the gear
browse — only the tile list (and its category chips) swap.
"""
import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtWidgets  # noqa: E402

from farever_companion import paths  # noqa: E402
from farever_companion.data import items as idata  # noqa: E402
from farever_companion.ui.pages.craft.detail import CraftDetailMixin  # noqa: E402
from farever_companion.ui.pages.items import ItemPageMixin, support  # noqa: E402


def _data_present() -> bool:
    try:
        return paths.item_drops_path().exists()
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _data_present(),
    reason="requires bundled data (assets/data/item_drops.json)")


@pytest.fixture(scope="module", autouse=True)
def _qapp():
    """One offscreen QApplication for the whole module (widgets need one)."""
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class _Page(QtWidgets.QWidget, ItemPageMixin):
    def _page_container(self, title=None):
        page = QtWidgets.QWidget()
        return page, QtWidgets.QVBoxLayout(page)

    def _codex_jump_to_unit(self, *args, **kwargs):
        return None

    def _select_nav(self, key):
        self._nav = key


class _CraftPage(_Page, CraftDetailMixin):
    def __init__(self):
        super().__init__()
        self._craft_jump_target = None

    def _craft_jump_to_recipe(self, item_id):
        self._craft_jump_target = item_id


def test_the_loadout_tab_is_gated_but_its_code_stays():
    """The Loadout tab is HIDDEN while its scoring model is rebuilt (see
    docs/LOADOUT_REDESIGN.md), not deleted — the code, its tests and a warm
    checkout are the point, so the gate has to be reversible and the page
    body has to stay lazy.

    Three properties, each a real failure mode:
      - a frozen build never offers the tab (`sys.frozen`, the same
        predicate the devicons page uses);
      - a dev checkout always does, or the rebuild has no surface to work on;
      - the env override can force it either way, so a dev run can exercise
        the OFF state without editing code.
    """
    assert "Loadout" in support.GEAR_TABS, "the tab was renamed away"

    real_frozen = getattr(sys, "frozen", False)
    real_env = os.environ.pop("FAREVER_LOADOUT", None)
    try:
        # a dev checkout: on
        setattr(sys, "frozen", False)
        assert support.loadout_enabled() is True
        assert "Loadout" in support.gear_tabs()

        # a frozen build: off, and the strip closes up
        setattr(sys, "frozen", True)
        assert support.loadout_enabled() is False
        assert "Loadout" not in support.gear_tabs()
        assert support.gear_tabs() == ("Gear", "Items", "Enchants", "Farm")

        # the override wins over both, either way
        os.environ["FAREVER_LOADOUT"] = "1"
        assert support.loadout_enabled() is True, \
            "the override cannot force the tab ON"
        os.environ["FAREVER_LOADOUT"] = "0"
        setattr(sys, "frozen", False)
        assert support.loadout_enabled() is False, \
            "the override cannot force the tab OFF"
    finally:
        setattr(sys, "frozen", real_frozen)
        os.environ.pop("FAREVER_LOADOUT", None)
        if real_env is not None:
            os.environ["FAREVER_LOADOUT"] = real_env

    # the page body stays lazy, so an unreachable tab costs nothing at build
    p = _page()
    assert getattr(p, "_items_loadout_built", False) is False, \
        "the gated tab still builds its body"
    # ...and the strip a real page builds carries the tab in dev
    assert "Loadout" in p._items_tabs._btns, list(p._items_tabs._btns)


def _page():
    p = _Page()
    page = p._page_gear()
    QtWidgets.QVBoxLayout(p).addWidget(page)
    p.show()
    return p


def _pool_ids():
    return support.category_item_ids("", "items")


def _pool_rows(p):
    """Visible data rows of the Items-tab list: [(id, category, type)]."""
    out = []
    for i in range(p._items_all_list.count()):
        li = p._items_all_list.item(i)
        if li.data(support.TYPE_HEADER_ROLE) or li.isHidden():
            continue
        out.append((li.data(support.ID_ROLE), li.data(support.CAT_ROLE),
                    li.data(support.TYPE_ROLE)))
    return out


def _tag_text(p):
    return p._items_search_header._tag.text()


def test_gear_build_spawns_no_top_level_window():
    """The first Gear build must not flash a transient top-level OS window.

    The filter-box rebuild used to call box.show() while the box was still
    parentless (it joins the page tree later in the build), so every first
    Gear visit spawned a brief app-titled window with undersize geometry —
    the QWindowsWindow::setGeometry warnings. Spy Show events for any window
    but the host while Gear builds and while hopping tabs; the filter panel
    itself must still come up visible on that first build."""
    from PySide6 import QtCore

    p = _Page()
    page = p._page_gear()
    QtWidgets.QVBoxLayout(p).addWidget(page)
    shown = []

    class _Spy(QtCore.QObject):
        def eventFilter(self, obj, event):
            if int(event.type()) == int(QtCore.QEvent.Show) \
                    and isinstance(obj, QtWidgets.QWidget) \
                    and obj.isWindow() and obj is not p:
                shown.append(type(obj).__name__)
            return False

    spy = _Spy()
    QtWidgets.QApplication.instance().installEventFilter(spy)
    try:
        p.show()
        for _ in range(5):
            QtWidgets.QApplication.processEvents()
        p._items_set_mode("Items")
        p._items_set_mode("Gear")
        for _ in range(5):
            QtWidgets.QApplication.processEvents()
    finally:
        QtWidgets.QApplication.instance().removeEventFilter(spy)
    assert shown == [], shown
    # the skipped shows must not have left the panel hidden: nothing was
    # ever hidden on the first build, so it all appears with the page
    assert p._items_filter_box.isVisible()
    assert p._items_class_chips_box.isVisible()
    assert p._items_rating_box.isVisible()
    assert p._items_quick_group.isVisible()


def test_items_tab_exists_and_swaps_the_list():
    """The tab strip offers Items, the body stack keeps its 4 pages (the
    Items tab swaps views INSIDE the browse page), and switching shows the
    pool list while hiding the gear list."""
    p = _page()
    assert p._items_mode == "gear"
    assert "Items" in p._items_tabs._btns
    assert p._items_body.count() == 4          # Gear/Loadout/Enchants/Farm
    assert not p._items_all_view.isVisible()

    p._items_set_mode("Items")
    assert p._items_mode == "items"
    assert p._items_all_view.isVisible()
    assert not p._items_list.isVisible()

    p._items_set_mode("Gear")
    assert p._items_mode == "gear"
    assert not p._items_all_view.isVisible()
    assert p._items_list.isVisible()


def test_items_tab_reachable_from_the_other_tabs():
    """Items is reachable from EVERY tab, not just Gear. The pool view swaps
    inside the browse page (stack 0), so the swap has to raise the stack as
    well: from Loadout/Enchants/Farm the row lit up on a page the stack
    wasn't showing, and the tab looked dead."""
    p = _page()
    for stack, tab in ((1, "Loadout"), (2, "Enchants"), (3, "Farm")):
        p._items_set_mode(tab)
        assert p._items_body.currentIndex() == stack, tab
        p._items_set_mode("Items")
        assert p._items_body.currentIndex() == 0, tab
        assert p._items_mode == "items", tab
        assert p._items_all_view.isVisible() and not p._items_list.isVisible(), tab
        assert _pool_rows(p), tab          # the pool really is on screen
    # and back out to Gear from Items still restores the gear list
    p._items_set_mode("Gear")
    assert p._items_body.currentIndex() == 0
    assert p._items_list.isVisible() and not p._items_all_view.isVisible()


def test_items_pool_is_the_non_gear_complement():
    """The pool carries every listable non-gear row plus the two gear-typed
    Crafting mats (cloth/leather) — and no equipment."""
    p = _page()
    p._items_set_mode("Items")
    rows = _pool_rows(p)
    ids = [iid for iid, _cat, _typ in rows]
    assert len(ids) == len(_pool_ids())
    assert p._items_all_total == len(_pool_ids())

    by_id = {it["id"]: it for it in idata.items()}
    ore = next(i for i, it in by_id.items() if it.get("name") == "Copper Ore")
    food = next(i for i, it in by_id.items() if it.get("type") == "Food")
    assert ore in ids and food in ids
    assert "Cloth_Z1" in ids and "Leather_Z1" in ids   # gear-typed Crafting
    assert "Axe_Boomerang" not in ids                  # equipment stays out
    # codex-only / enchant rows stay out too
    assert "Back_EBee_Wiz" not in ids
    scrolls = [i for i in ids if i.startswith("ScrollOf")]
    assert not scrolls
    # every row is non-gear or Crafting-category
    for iid, _cat, typ in rows:
        it = by_id[iid]
        assert (not idata.is_gear(it)
                or idata.category(typ) == "Crafting"), iid


def test_items_category_chips_filter_and_refire_all():
    """Picking a category chip narrows the pool to it (header count follows);
    re-clicking the active chip falls back to All."""
    p = _page()
    p._items_set_mode("Items")
    cat = "Crafting"
    assert cat in p._items_allcat_chips
    p._items_allcat_chips[cat].setChecked(True)
    rows = _pool_rows(p)
    assert rows and all(c == cat for _i, c, _t in rows)
    assert _tag_text(p) == f"ITEMS {len(rows)} / {p._items_all_total}"

    p._items_allcat_chips[cat].setChecked(False)   # re-click -> All
    assert p._items_allcat == support.ITEMS_TAB
    assert len(_pool_rows(p)) == p._items_all_total


def test_items_search_filters_the_pool():
    """The shared search box drives the pool list while the tab is live —
    matched rows + the header count agree."""
    p = _page()
    p._items_set_mode("Items")
    by_id = {it["id"]: it for it in idata.items()}
    ore = next(i for i, it in by_id.items() if it.get("name") == "Copper Ore")
    p._items_search.setText("copper")
    rows = _pool_rows(p)
    assert rows, "copper query matched nothing on the Items tab"
    assert ore in [iid for iid, _c, _t in rows]
    assert _tag_text(p) == f"ITEMS {len(rows)} / {p._items_all_total}"
    p._items_search.setText("")
    assert len(_pool_rows(p)) == p._items_all_total


def test_leaving_items_restores_the_gear_search_wiring():
    """Back to Gear (or straight to Enchants): the gear list returns and the
    search box filters it again — the swap rewires the shared box."""
    p = _page()
    p._items_set_mode("Items")
    p._items_set_mode("Gear")
    assert p._items_list.isVisible()
    p._items_search.setText("axe")
    shown = sum(1 for i in range(p._items_list.count())
                if not p._items_list.item(i).isHidden()
                and not p._items_list.item(i).data(support.TYPE_HEADER_ROLE))
    assert shown, "search wired to the pool refilter after leaving Items"
    # straight Items -> Enchants: the gear view restores underneath
    # (isHidden, not isVisible: the browse page sits in an unshown stack
    # page here, so a correctly-restored widget still reads not visible)
    p._items_search.setText("")
    p._items_set_mode("Items")
    p._items_set_mode("Enchants")
    assert p._items_mode == "enchants"
    assert p._items_all_view.isHidden()
    assert p._items_list.isHidden() is False


def test_family_counts_items_mode_matches_the_pool():
    """family_counts(mode='items') mirrors the pool: same total, and the
    gear mode is untouched by the new parameter."""
    pool = _pool_ids()
    counts = support.family_counts(mode="items")
    assert sum(counts.values()) == len(pool)
    # the gear-mode default is unchanged
    assert sum(support.family_counts().values()) == sum(
        1 for it in idata.items()
        if idata.is_gear(it)
        and idata.category(it.get("type")) != "Crafting"
        and not support.is_hidden_shop_item(it)
        and support.is_listable_item(it))


# the Guild Merchant's starter / town weapons: real equipment with a Shop
# drop row, excluded from the tab for a while when `_shop_ids()` widened to
# the drops index's shop-only set. They are ordinary gear and must list.
_SOURCED_SHOP_WEAPONS = (
    "Sword_Craft", "Shield_Craft", "Staff_Craft", "Bow_Craft",
    "Sword_Start", "Shield_Start", "Daggers_Start", "Scepter_Start",
    "Book_Start",
)

# rows the browse lists keep out: premium store pieces the scan found no
# source for, plus the non-equipment shop stock (caches, gliders, sigils).
_HIDDEN_SHOP_ROWS = ("Back_Shop", "Head_Shop", "Rift_Gear_Cache",
                     "Glider_Shop_Dragon")


def test_weapons_pool_keeps_the_sourced_shop_gear_the_drops_index_sells():
    """The Weapons pool is every listable weapon, not every weapon EXCEPT
    the ones a vendor counter sells.

    Wiring `_shop_ids()` to the drops index's shop-only rows (the retired
    shop.json replacement) turned `is_shop_item` into "sells with no other
    source", which swept up the Guild Merchant's ordinary starter and town
    weapons — nine of them vanished from the tab. Only the non-equipment
    shop stock and no-source premium pieces stay out.
    """
    pool = set(support.category_item_ids("Weapons"))
    expected = {it["id"] for it in idata.items()
                if idata.is_gear(it)
                and idata.category(it.get("type")) == "Weapons"
                and support.is_listable_item(it)
                and not support.is_hidden_shop_item(it)}
    assert pool == expected
    assert len(pool) == 36, sorted(pool)
    # the gear-tab matcher for one item with no class/type/rating filter and
    # every tier lit (RARITY_BUTTONS covers the Common practice sword), so
    # only the list gates (shop / listable / gear) decide
    p = _page()

    def matches(iid: str) -> bool:
        return p._items_match(iid, "", "Weapons", "", set(), False,
                              support.RARITY_BUTTONS)

    for iid in _SOURCED_SHOP_WEAPONS:
        it = idata.item(iid)
        assert it is not None, iid
        assert idata.is_shop_item(iid), iid     # the regression's precondition
        assert iid in pool, iid
        assert not support.is_hidden_shop_item(it), iid
        assert matches(iid), iid                # the filter path agrees too
    for iid in _HIDDEN_SHOP_ROWS:
        it = idata.item(iid) or {}
        assert support.is_hidden_shop_item(it), iid
        assert iid not in pool, iid
        assert not matches(iid), iid


def test_every_pool_row_opens_a_detail_card():
    """Crash sweep: selecting each pool row renders its detail card (the
    pane is item-agnostic — currency, recipes, caches and tools all go
    through the same renderer)."""
    p = _page()
    p._items_set_mode("Items")
    for iid in _pool_ids():
        p._items_show_id(iid)
        assert p._items_shown_id == iid, iid
        assert p._items_detail_lay.count() > 0, iid


def test_craft_open_in_items_routes_non_gear_outputs():
    """Non-gear, non-craftable outputs open on the Items tab with the row's
    detail card; non-gear CRAFTABLE outputs stay on the craft page (the
    recipe card IS their page)."""
    p = _CraftPage()
    page = p._page_gear()
    QtWidgets.QVBoxLayout(p).addWidget(page)
    p.show()

    by_id = {it["id"]: it for it in idata.items()}
    gold = next(i for i, it in by_id.items()
                if it.get("type") == "Currency" and it.get("name") == "Gold")
    p._craft_open_in_items(gold)
    assert p._nav == "gear"
    assert p._items_mode == "items"
    assert p._items_all_view.isVisible()
    assert p._items_shown_id == gold

    # a non-gear CRAFTABLE output that is not an enchant item (enchant
    # outputs take the Enchants route above this branch)
    craftable_nongear = next(
        i for i, it in by_id.items()
        if not idata.is_gear(it) and idata.is_craftable(i)
        and not support.is_enchant_item(it))
    p._craft_open_in_items(craftable_nongear)
    # still on the craft page's own route (the real _craft_jump_to_recipe
    # switches the nav; the stub just records the target) — the point is it
    # did NOT open a detail card on the Items tab (the stale gold card from
    # the call above is still shown; no re-route happened)
    assert p._craft_jump_target == craftable_nongear
    assert p._items_shown_id == gold


def _pane_labels(host) -> set:
    return {lbl.text() for lbl in host.findChildren(QtWidgets.QLabel)
            if lbl.text()}


def test_dungeon_pane_shows_the_boss_infusion_pattern():
    """The Infusion System on the Dungeons tab: each boss block carries an
    'Infusion Pattern · heroic mode' section with that boss's guaranteed
    pattern, clickable into the item detail card."""
    p = _page()
    support.quick_pick(p, "Dungeons")
    dv = p._items_dungeon_view
    dv._pick("Bee")
    # the faction sheet (with the per-boss blocks) renders into the shared
    # detail pane
    labels = _pane_labels(p._items_detail)
    inf = [t for t in labels if "· Infusion Pattern ·" in t]
    # Bee has three heroic bosses, each with its own pattern block
    # (Gatsbee, Lady Bee, Queen Honeyzabeth)
    assert len(inf) == 3, inf
    assert any("Gatsbee" in t for t in inf)
    # opening the pattern shows its item card (Drops From + the crucible
    # note)
    p._items_show_id("InfusionPattern_Bee_DPS")
    assert p._items_shown_id == "InfusionPattern_Bee_DPS"
    detail = _pane_labels(p._items_detail)
    assert any("crucible" in t.lower() for t in detail)


def test_dungeon_tile_reopens_after_going_back():
    """Clicking a dungeon tile, going back to the faction card, then clicking
    the same tile again re-opens its card. The faction card used to leave the
    last shown item id behind, so the pane's same-item guard swallowed the
    second click and the tile went dead."""
    p = _page()
    support.quick_pick(p, "Dungeons")
    dv = p._items_dungeon_view
    dv._pick("Bee")
    pid = "InfusionPattern_Bee_DPS"
    p._items_show_id(pid)
    assert p._items_shown_id == pid
    dv.select("Bee", "")          # back to the faction card
    assert p._items_shown_id is None
    p._items_show_id(pid)         # same tile again: must re-render
    assert p._items_shown_id == pid
    assert any(t == "Bee DPS" for t in _pane_labels(p._items_detail))


def test_faction_chip_reclick_returns_from_item_card():
    """Re-clicking the lit faction chip goes back to the faction overview
    from an open item card. The un-click used to be refused, so without a
    mouse BACK button there was no way back and the chip felt dead."""
    p = _page()
    support.quick_pick(p, "Dungeons")
    dv = p._items_dungeon_view
    dv._pick("Bee")
    pid = "InfusionPattern_Bee_DPS"
    p._items_show_id(pid)
    assert p._dg_pane_state == "item"
    dv._chips["Bee"].click()      # un-click attempt on the lit chip
    assert dv._chips["Bee"].isChecked()
    assert p._dg_pane_state == "boss"
    assert any(t == "Bee Dungeons" for t in _pane_labels(p._items_detail))


def test_medal_of_glory_is_listable_and_carded():
    """The patch's Medal of Glory (BadgeOfGlory) — a currency with no loot
    rows — shows on the Items tab and its card carries the heroic
    completion note instead of an empty Drops From."""
    p = _page()
    p._items_set_mode("Items")
    ids = {p._items_all_list.item(i).data(support.ID_ROLE)
           for i in range(p._items_all_list.count())
           if not p._items_all_list.item(i).data(support.TYPE_HEADER_ROLE)}
    assert "BadgeOfGlory" in ids
    p._items_show_id("BadgeOfGlory")
    detail = _pane_labels(p._items_detail)
    # the note points at the heroic BOSS and the cache box, not at a list of
    # shared hub towns — the cache is not sold in the social hubs
    assert any("Heroic" in t and "boss" in t for t in detail)
    assert not any("social hubs" in t for t in detail)
    # the vendor panel still renders (placeholder until a scan prices it)
    assert any("Shiro James" in t for t in detail)


def test_heroic_cache_card_lists_the_box_it_opens():
    """A Hero Gear Cache is a BOX, so its detail card leads with what is
    inside it — the Epic set pieces, slot by slot — rather than an empty
    Drops From. The count and rarity are read off the box the SOURCE sheet
    says the cache opens (HM_Items for this one), never hardcoded: the count
    used to be pinned to 25 from a swapped mapping and drifted the moment
    the mapping was corrected."""
    from farever_companion.data.items.sources import heroic_cache_contents
    box = heroic_cache_contents()["HM_Gear_Cache_25"]
    p = _page()
    p._items_set_mode("Items")
    p._items_show_id("HM_Gear_Cache_25")
    detail = _pane_labels(p._items_detail)
    assert any("Contains" in t for t in detail)
    assert any(f"{len(box)} {box[0]['rarity'].upper()} PIECES" in t
               for t in detail)
    # real piece names, not ids
    assert any(box[0]["label"] in t for t in detail)


def test_infusion_pattern_card_shows_the_granted_skill():
    """A pattern's detail card explains what the pattern does: the granted
    skill (and its ultimate, when the sheet names one) with the skill
    sheet's own description, in a gold effect box above Drops From."""
    p = _page()
    p._items_set_mode("Items")
    p._items_show_id("InfusionPattern_Bee_Support")
    detail = _pane_labels(p._items_detail)
    # the card title is the cleaned display name ('Bee Support' — the
    # manifest's raw id / broken token never renders)
    assert any(t == "Bee Support" for t in detail), detail
    assert not any("InfusionPattern_" in t for t in detail)
    # the granted skill renders with its sheet description (the Nectar
    # Balls pickup mechanic)
    assert any("Nectar Balls" in t and "GRANTED SKILL" in t for t in detail)
    assert any("chance to drop Nectar Balls" in t for t in detail)
    # the ultimate rides under its own tag (its sheet row has no desc yet,
    # so the box shows the name without a paragraph — no invented text)
    assert any("Honey Shot" in t and "ULTIMATE" in t for t in detail)
    # the crucible's set tiers ride the granted skill's box: the (4) stat
    # affix and (6) rank text from the skill sheet (the InfusionUI dump's
    # InfusionSkillDesc shape)
    assert any("(4) Set :" in t and "+2.5% Critical Chance" in t
               for t in detail)
    assert any("(6) Set :" in t for t in detail)
    # a pattern whose ultimate the sheet doesn't name (Manfish DPS) still
    # gets its granted-skill box
    p._items_show_id("InfusionPattern_Manfish_DPS")
    detail = _pane_labels(p._items_detail)
    assert any("Overwhelming Tides" in t and "GRANTED SKILL" in t
               for t in detail)
    # non-patterns get no effect box
    p._items_show_id("BadgeOfGlory")
    assert not any("GRANTED SKILL" in t for t in _pane_labels(p._items_detail))


def test_mog_card_shows_the_shiro_james_exchange(monkeypatch):
    """The Medal of Glory's card carries a 'Shiro James Sells' section.

    It lists two kinds of stock. His CACHES are recorded outright — read off
    his open shop on 2026-09-26, because the item sheet cannot describe them:
    the whole `HM_` family has `drops: []`, so no row names the seller. His
    CONSUMABLE stock still rides the scan's priced MoG shop rows, and while a
    capture holds none it is simply absent rather than placeholder text: the
    section is no longer empty, so there is nothing to apologise for.
    """
    from farever_companion.data.items import sources
    p = _page()
    p._items_set_mode("Items")
    p._items_show_id("BadgeOfGlory")
    detail = _pane_labels(p._items_detail)
    assert any("SHIRO JAMES SELLS" in t for t in detail)
    # the two recorded boxes, at the price the shop listed
    for nm in ("Hero Gear Cache", "Hero Weapon Cache"):
        assert any(nm in t for t in detail), (nm, detail)
    assert any("100 Medal of Glory" in t for t in detail), detail
    assert any("2 ITEMS" in t for t in detail), detail
    # the recorded stock is the reason the placeholder is gone
    assert not any("not recorded by the scan yet" in t for t in detail)
    # a captured stock renders as priced rows
    def fake_data():
        return {"npc_sources": [], "items": {
            "Sword_Heroic_A": {"name": "Auroral Edge", "drops": [
                {"s": 0, "t": 0, "p": 1.0, "l": 7,
                 "cost": [{"kind": "BadgeOfGlory", "amount": 120}]}]},
        }}
    monkeypatch.setattr(sources, "_data", fake_data)
    monkeypatch.setattr(sources, "_sources",
                        lambda: [{"id": "ShiroJames_NPC_Primevalley",
                                  "kind": "npc", "name": "Shiro James"}])
    monkeypatch.setattr(sources, "_tables", lambda: ["Shop"])
    sources.mog_exchange.cache_clear()
    try:
        p._items_show_id("Gold")   # park on another item so the next
        p._items_show_id("BadgeOfGlory")   # open isn't skipped as no-op
        detail = _pane_labels(p._items_detail)
        assert any("SHIRO JAMES SELLS" in t for t in detail)
        assert any("Auroral Edge" in t for t in detail)
        assert any("120 MoG" in t for t in detail)
        assert not any("not recorded by the scan yet" in t
                       for t in detail)
        # the recorded boxes ride alongside a captured consumable
        assert any("Hero Weapon Cache" in t for t in detail), detail
    finally:
        sources.mog_exchange.cache_clear()


def test_mog_card_needs_priced_stock(monkeypatch):
    """Sanity: unpriced shop rows (the current stub's Gold entry) never
    render as exchange listings — only cost entries naming BadgeOfGlory
    count as stock."""
    from farever_companion.data.items import sources
    def fake_data():
        return {"npc_sources": [], "items": {
            "Gold": {"name": "Gold", "drops": [
                {"s": 0, "t": 0, "p": 1.0}]},
            "Sword_Heroic_A": {"name": "Auroral Edge", "drops": [
                {"s": 0, "t": 0, "p": 1.0,
                 "cost": [{"kind": "Gold", "amount": 5000}]}]},
        }}
    monkeypatch.setattr(sources, "_data", fake_data)
    monkeypatch.setattr(sources, "_sources",
                        lambda: [{"id": "ShiroJames_NPC_Primevalley",
                                  "kind": "npc", "name": "Shiro James"}])
    monkeypatch.setattr(sources, "_tables", lambda: ["Shop"])
    sources.mog_exchange.cache_clear()
    try:
        assert sources.mog_exchange() == ()
    finally:
        sources.mog_exchange.cache_clear()


def test_locked_gear_shows_an_upgrades_not_available_note():
    """Armor and jewelry can't be upgraded in-game yet, so their gear-scale
    block renders the ladder base-only. The stat block says why, so the
    absent step columns read as a game state rather than a rendering bug —
    and a weapon, which DOES upgrade, stays silent."""
    from tests.qt_helpers import drain_deleted
    p = _page()

    locked_id = "Back_EBee_AssCle"           # Epic armor — no upgrade path yet
    assert idata.upgrade_locked(locked_id)
    p._items_show_id(locked_id)
    assert "Upgrades not available yet" in _pane_labels(p._items_detail)

    weapon_id = "Axe_Boomerang"              # upgradable — no note
    assert not idata.upgrade_locked(weapon_id)
    p._items_show_id(weapon_id)
    drain_deleted()                          # the old card is deleteLater'd
    assert "Upgrades not available yet" not in _pane_labels(p._items_detail)


def test_hero_weapon_cache_contents_name_the_roll_span():
    """The Hero Weapon Cache's roll is CLAMPED at Epic, not fixed there — one
    in-game open (2026-10-05) showed an Epic sword beside a Legendary shield —
    so its Contents section must not print a bare "Epic" as if that were all it
    hands out. Driven through the ITEM PAGE, the surface a user reads: the
    header, the note and every row tag carry the floor, and the note names the
    ceiling.

    A fixed box is untouched: the Hero GEAR cache still reads a flat Epic, so
    the span can never be applied where the sheet gives no floor.
    """
    from tests.qt_helpers import drain_deleted
    p = _page()

    def texts():
        # the pane's labels IN ORDER (a set would lose the per-row count)
        return [lbl.text() for lbl in p._items_detail.findChildren(
            QtWidgets.QLabel) if lbl.text()]

    p._items_show_id("HM_Weapon_Cache_25")
    wtexts = texts()
    assert "4 EPIC+ PIECES" in wtexts, wtexts
    assert any("as high as Legendary" in t for t in wtexts), wtexts
    # one floor tag per row, and nothing anywhere still claiming flat Epic
    assert wtexts.count("EPIC+") == 4, wtexts
    assert "EPIC" not in wtexts, wtexts

    p._items_show_id("HM_Gear_Cache_25")
    drain_deleted()                        # the box's card is deleteLater'd
    gtexts = texts()
    assert "24 EPIC PIECES" in gtexts, gtexts
    assert "EPIC+" not in gtexts, gtexts


def test_unspecced_container_contents_are_surfaced_on_the_item_page():
    """The starter "Mysterious Cache" (`Z1_WeaponBundle`) is a compiled
    LootableContainer the hand cache table never claimed — and NOT a test-only
    exclusion. Its ITEM PAGE, the surface a user reads, renders a Contains
    section listing the table's real pool (the six class starter weapons),
    driven by the compiler-baked `gain_item`, with the honest derived note.
    """
    p = _page()
    p._items_show_id("Z1_WeaponBundle")
    texts = [lbl.text() for lbl in p._items_detail.findChildren(
        QtWidgets.QLabel) if lbl.text()]
    # the Contents section: header + the six real pieces
    assert "6 RARE PIECES" in texts, texts
    assert "Credence" in texts and "Radiance" in texts, texts
    assert "Judgement" in texts, texts
    assert any("One open rolls at most 2 of these" in t for t in texts), texts
    # the derived note, honest about what the box is and where to look
    assert any("Starter cache" in t and "see Contains" in t for t in texts), (
        texts)


def test_dungeons_pane_names_the_weapon_cache_roll_span():
    """The Dungeons pane a box opens into makes the same claim as its item
    page, so the two surfaces cannot disagree about what one open hands out."""
    from farever_companion.ui.pages.items.dungeon_box import show_box
    p = _page()
    support.quick_pick(p, "Dungeons")
    show_box(p, "HM_Weapon_Cache_25")
    labels = _pane_labels(p._items_detail)
    assert any("Epic+" in t for t in labels), sorted(labels)[:20]
    assert not any(t.startswith("Epic ·") for t in labels), sorted(labels)[:20]


def test_dungeons_pane_falls_back_to_the_generic_box_spec():
    """A box the hand cache table never claimed still opens in the Dungeons
    pane. The pane reads the SAME generic container facts the item page's
    Contains section renders (`container_spec`/`container_contents`) — the
    hand table for its boxes, the derived spec for anything it does not
    claim — so the starter "Mysterious Cache" lists its real pool here
    instead of the pane silently refusing to open it.
    """
    from farever_companion.data.items import sources
    from farever_companion.ui.pages.items.dungeon_box import show_box
    p = _page()
    support.quick_pick(p, "Dungeons")
    box = sources.container_contents()["Z1_WeaponBundle"]
    assert box, "the shim must declare the starter cache's pool"
    show_box(p, "Z1_WeaponBundle")
    labels = _pane_labels(p._items_detail)
    # the sheet's own name, not the raw id: the derived spec is a real name
    assert "Mysterious Cache" in labels, sorted(labels)[:20]
    assert any(box[0]["label"] in t for t in labels), sorted(labels)[:20]
    assert any(f"{len(box)} pieces" in t for t in labels), sorted(labels)[:20]
    # and no seller is invented for a box the hand table does not know
    assert not any(t.startswith("via ") for t in labels), sorted(labels)[:20]


def test_dungeons_pane_lists_the_recorded_seller_prose():
    """The Dungeons pane names who hands a box over, in prose: the hand
    table's `via` for Mira's Rift boxes, and the seller the scan actually
    recorded (Shiro James, 100 medals) for the two Hero boxes that carry no
    hand `via`. A box with a recorded seller must never read as anonymous.
    """
    from farever_companion.ui.pages.items.dungeon_box import show_box
    p = _page()
    support.quick_pick(p, "Dungeons")
    show_box(p, "Rift_Weapon_Cache")
    labels = _pane_labels(p._items_detail)
    assert any(t.startswith("via ") and "Mira" in t for t in labels), (
        sorted(labels)[:20])
    show_box(p, "HM_Gear_Cache_25")
    labels = _pane_labels(p._items_detail)
    assert any(t.startswith("via ") and "Shiro James" in t for t in labels), (
        sorted(labels)[:20])
