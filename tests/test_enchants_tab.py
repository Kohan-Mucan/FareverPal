"""Enchants-tab tests (headless Qt, offscreen platform).

The Items page's third tab swaps the browse layout for the enchant
database page (scrolls / gems / conversions). Covers the category tabs,
the Stat chip filter, and the card visibility rules.
"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtWidgets  # noqa: E402

from farever_companion import paths  # noqa: E402
from farever_companion.data import items as idata  # noqa: E402
from farever_companion.ui import components as C  # noqa: E402
from farever_companion.ui import theme  # noqa: E402
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
        """The Drops From body wires codex jumps; the test harness has no
        codex mixin, so render-gear-detail calls just no-op."""
        return None


class _CraftPage(QtWidgets.QWidget, ItemPageMixin, CraftDetailMixin):
    def _page_container(self, title=None):
        page = QtWidgets.QWidget()
        return page, QtWidgets.QVBoxLayout(page)

    def _codex_jump_to_unit(self, *args, **kwargs):
        return None

    def _select_nav(self, key):
        self._nav = key


def _page():
    p = _Page()
    page = p._page_gear()
    QtWidgets.QVBoxLayout(p).addWidget(page)
    p.show()
    return p


def test_items_page_has_enchants_tab():
    """The SegmentedControl offers the Enchants tab and the browse body is
    the default (Gear mode)."""
    p = _page()
    assert p._items_mode == "gear"
    # the body stack holds one page per tab: Gear + Loadout + Enchants + Farm
    assert p._items_body.currentWidget() is p._items_browse
    assert p._items_body.count() == 4


def test_enchants_mode_swaps_body_and_renders_database():
    """Switching to Enchants shows the enchant page: the data sources
    loaded, the count tag updated and the stat chips built from every stat
    any scroll / gem / conversion / crafting consumable touches."""
    p = _page()
    p._items_set_mode("Enchants")
    assert p._items_mode == "enchants"
    # by identity, not index: a new tab must not silently repoint this
    assert p._items_body.currentWidget() is p._items_enchants_scroll

    scrolls, gems, convs = p._ench_scrolls, p._ench_gems, p._ench_convs
    assert scrolls and gems and convs
    assert _counts(p)["SCROLLS"] == len(scrolls) + len(p._ench_corrupt)
    assert _counts(p)["GEMS"] == len(gems)
    assert _counts(p)["CONVERSIONS"] == len(convs)

    stats = _filter_stats(p)
    assert stats, "no stats to filter by"
    assert "Armor" in stats     # the consumables' stats joined the chips
    assert p._ench_stat.count() == len(stats) + 1   # + All stats


def test_enchants_stat_filter_narrows_and_reclick_clears():
    """Picking a stat chip narrows the cards to rows touching it; the
    count tag follows. Re-clicking the active chip clears back to all."""
    p = _page()
    p._items_set_mode("Enchants")
    stats = _filter_stats(p)
    stat = next(s for s in stats if s in p._ench_scrolls)
    idx = stats.index(stat) + 1                 # +1 for the All stats chip
    p._ench_stat.setCurrentIndex(idx)
    assert p._ench_stat.currentData() == stat

    if stat in p._ench_scrolls:
        assert len(p._ench_scrolls) > 0         # unfiltered set intact
    scrolls = {s for s in p._ench_scrolls if s == stat}
    corrupt = [c for c in p._ench_corrupt
               if stat in (c["stat"], c["penalty"])]
    assert _counts(p)["SCROLLS"] == len(scrolls) + len(corrupt)

    # re-click the active chip -> back to All stats
    p._ench_stat.setCurrentIndex(0)                # back to All stats
    assert p._ench_stat.currentData() == ""
    assert _counts(p)["SCROLLS"] == \
        len(p._ench_scrolls) + len(p._ench_corrupt)
    assert _counts(p)["GEMS"] == len(p._ench_gems)
    assert _counts(p)["CONVERSIONS"] == len(p._ench_convs)


def test_enchants_stat_chips_show_tab_counts():
    """The Stat chips carry the matching-row count for the ACTIVE tab —
    'Strength · 10' on Augments — and a stat with nothing on the tab is
    HIDDEN ('· 0' chips drop out instead of cluttering the row). The
    chip DATA stays the bare stat name, so filtering is untouched;
    switching tabs relabels and re-hides the chips."""
    p = _page()
    p._items_set_mode("Enchants")
    p._ench_tabs._btns["Augments"].click()

    def _chip(stat: str) -> QtWidgets.QPushButton:
        return next(b for b in p._ench_stat.findChildren(
            QtWidgets.QPushButton) if b.text().startswith(stat))

    # Augments: ten rows grant Strength (the +2 scroll, the corrupted
    # +4 trade, the HANDS formulas, the plates and embroideries)
    assert _chip("Strength").text() == "Strength · 10"
    assert _chip("Strength").isVisible()
    assert p._ench_stat.currentData() == ""   # data still the bare name
    # the Gems tab shows what's actually there — gems grant only the
    # penetration / crit / fervor / vitality family, so the Strength
    # chip is hidden rather than reading '· 0'
    p._ench_tabs._btns["Gems"].click()
    assert not _chip("Strength").isVisible()
    assert _chip("Critical").text() == "Critical · 10"
    assert _chip("Critical").isVisible()
    # an ACTIVE pick stays visible even at '· 0' — the gear page's
    # ENCHANTS chip jumps here pre-filtered, and the pick must survive
    # landing on a tab with no rows for it (the user then flips tabs)
    p._ench_tabs._btns["Augments"].click()
    p._ench_stat.setCurrentIndex(_filter_stats(p).index("Strength") + 1)
    assert p._ench_stat.currentData() == "Strength"
    p._ench_tabs._btns["Gems"].click()
    assert p._ench_stat.currentData() == "Strength"   # pick survives
    assert _chip("Strength").isVisible()              # …and stays visible
    assert _chip("Strength").text() == "Strength · 0"
    p._ench_stat.setCurrentIndex(0)                   # clear it back
    assert not _chip("Strength").isVisible()         # then it hides


def _counts(p) -> dict:
    """The filtered totals the refilter stores — {'SCROLLS': n, 'GEMS':
    n, 'CONVERSIONS': n} (the old top count tag is gone, but the page
    keeps the numbers)."""
    return dict(p._ench_counts)


def test_enchants_search_filters_scrolls_by_stat():
    """Typing a stat name narrows the scrolls card to the scrolls carrying
    it — the plain one and its corrupted variant; clearing the box
    restores the full database."""
    p = _page()
    p._items_set_mode("Enchants")
    stat = next(iter(sorted(p._ench_scrolls)))     # Dexterity
    p._ench_search.setText(stat)
    assert _counts(p)["SCROLLS"] == 1 + sum(
        1 for c in p._ench_corrupt if c["stat"] == stat)
    p._ench_search.clear()
    assert _counts(p)["SCROLLS"] == len(p._ench_scrolls) + len(p._ench_corrupt)


def test_enchants_search_filters_gems_by_name():
    """A gem display-name word narrows the gems card to the gems whose
    names carry it (the search reads the display name, not the raw id)."""
    p = _page()
    p._items_set_mode("Enchants")
    g = p._ench_gems[0]
    name = (idata.item(g["item"]) or {}).get("name") or g["item"]
    p._ench_search.setText(name.split()[0])
    counts = _counts(p)
    assert 0 < counts["GEMS"] < len(p._ench_gems)


def test_conversion_matrix_splits_rows_and_columns_by_stat():
    """The conversion card is a DEDUPLICATED MATRIX: rows are the stat
    given up, the four columns the stat gained (short names, no repeated
    numbers), and each stat pair is ONE marker cell — '↘' that the
    conversion exists, '↑' on the diagonal (no self-trade). Every trade
    is identical, so the numbers appear exactly once — in the corner
    line, which names the axes AND shows the active rarity's gain /
    loss. The markers are inert (no click-through), and the top line is
    closed: the WEAPON badge and the EPIC / RARE swap chips sit
    together on the left."""
    p = _page()
    p._items_set_mode("Enchants")
    c = p._ench_convs[0]
    assert c["source"] == "Critical" and c["target"] == "Armor Penetration"
    grid = _grid(p._ench_convs_lay)
    # the axes: the given-up stat column + the 4 gained-stat columns
    assert grid.columnCount() == 5
    assert grid.rowCount() == 6      # closed top line + header + 4 stats
    # the closed top line spans the full width — nothing spread right
    top = grid.itemAtPosition(0, 0).widget()
    assert grid.itemAtPosition(0, 4).widget() is top
    assert any(lb.text() == "WEAPON" for lb in top.findChildren(
        QtWidgets.QLabel))           # the badge, not a text label
    cht = top.findChildren(QtWidgets.QPushButton)
    assert [ch.text() for ch in cht] == ["EPIC", "RARE"]
    assert theme.rarity_color("Epic") in cht[0].styleSheet()   # live
    assert theme.rarity_color("Rare") not in cht[1].styleSheet()  # dim
    # the header row: the corner names the axes AND carries the active
    # rarity's trade; each column carries its short stat name
    targets = sorted({c["target"] for c in p._ench_convs})
    assert targets == ["Armor Penetration", "Critical", "Fervor",
                       "Magic Penetration"]
    corner = grid.itemAtPosition(1, 0).widget()
    assert [lb.text() for lb in corner.findChildren(QtWidgets.QLabel)] \
        == ["GIVE UP ↓ / GAIN →", "+40", "−40"]
    for ci, (t, s) in enumerate(zip(
            targets, ["ARMOR PEN", "CRITICAL", "FERVOR", "MAGIC PEN"]),
            start=1):
        head = grid.itemAtPosition(1, ci).widget()
        lbs = head.findChildren(QtWidgets.QLabel)
        # plain names — the identical trade numbers live once in the
        # corner line, not repeated beside every column
        assert [lb.text() for lb in lbs] == [s], t
    # row headers are the given-up stats; the diagonal is an '↑'
    rows = sorted({c["source"] for c in p._ench_convs})
    assert rows == targets
    for ri, s in enumerate(rows, start=2):
        assert grid.itemAtPosition(ri, 0).widget().text() == s
        diag = grid.itemAtPosition(ri, ri - 1).widget()   # col == row
        dl = diag.findChildren(QtWidgets.QLabel)
        assert [lb.text() for lb in dl] == ["↑"]
        # the diagonal reads blue, apart from the muted ↘ markers
        assert theme.BLUE in dl[0].styleSheet()
    # every off-diagonal cell is ONE marker — the trade is identical
    # everywhere, so the numbers aren't repeated; the cell is inert
    cell = grid.itemAtPosition(2, 2).widget()     # A.Pen → Critical
    labels = cell.findChildren(QtWidgets.QLabel)
    assert [lb.text() for lb in labels] == ["↘"]
    assert cell.toolTip() == ""   # no mouse-over tooltips on the Items page


def test_conversion_rarity_chips_swap_the_matrix():
    """The EPIC / RARE chips are a swap — exactly one is live (Epic by
    default), and clicking the other moves the highlight and switches
    which rarity's gain / loss the corner line shows. The matrix's
    markers stay put (every trade is identical, so the cells carry no
    numbers to switch)."""
    p = _page()
    p._items_set_mode("Enchants")

    def _marker():
        return [lb.text() for lb in _grid(p._ench_convs_lay)
                .itemAtPosition(2, 2).widget()
                .findChildren(QtWidgets.QLabel)]

    def _corner():
        return [lb.text() for lb in _grid(p._ench_convs_lay)
                .itemAtPosition(1, 0).widget()
                .findChildren(QtWidgets.QLabel)]

    def _chips():
        top = _grid(p._ench_convs_lay).itemAtPosition(0, 0).widget()
        return top.findChildren(QtWidgets.QPushButton)

    # Epic is the default — its chip is live, the corner shows +40 −40
    epic, rare = _chips()
    assert theme.rarity_color("Epic") in epic.styleSheet()
    assert theme.rarity_color("Rare") not in rare.styleSheet()
    assert _marker() == ["↘"]
    assert _corner() == ["GIVE UP ↓ / GAIN →", "+40", "−40"]
    # clicking RARE moves the live highlight AND the corner's trade
    rare.click()
    epic, rare = _chips()
    assert theme.rarity_color("Rare") in rare.styleSheet()
    assert theme.rarity_color("Epic") not in epic.styleSheet()
    assert _marker() == ["↘"]
    assert _corner() == ["GIVE UP ↓ / GAIN →", "+20", "−20"]
    # clicking EPIC swaps back
    epic.click()
    epic, rare = _chips()
    assert theme.rarity_color("Epic") in epic.styleSheet()
    assert theme.rarity_color("Rare") not in rare.styleSheet()
    assert _corner() == ["GIVE UP ↓ / GAIN →", "+40", "−40"]


def test_tables_stripe_alternate_rows():
    """The gem and conversion tables put a faint tint on every other data
    row (full-row bands, since the cells fill their grid cells) so the
    aligned columns scan vertically."""
    p = _page()
    p._items_set_mode("Enchants")
    tint = theme.with_alpha(theme.TEXT, 10)
    # conversions: the 4 data rows alternate tint / transparent (row 0
    # holds the WEAPON badge and the rarity chips, row 1 the column
    # headers)
    cgrid = _grid(p._ench_convs_lay)
    for r in range(2, cgrid.rowCount()):
        w = cgrid.itemAtPosition(r, 1).widget()
        assert w is not None
        assert (tint in w.styleSheet()) == (r % 2 == 0), (r, w.styleSheet())
    # gems: the second data row of a zone is tinted, the first is not —
    # read the row band (col 0), since the stat cells are now chips
    ggrid = _grid(p._ench_gems_lay)
    first = ggrid.itemAtPosition(2, 0).widget()
    second = ggrid.itemAtPosition(3, 0).widget()
    assert tint not in (first.styleSheet() or "")
    assert tint in (second.styleSheet() or "")


def test_enchants_search_filters_conversions_by_stat():
    """A traded stat narrows the conversions card to the conversions that
    trade it."""
    p = _page()
    p._items_set_mode("Enchants")
    c = p._ench_convs[0]
    p._ench_search.setText(c["target"])
    assert 0 < _counts(p)["CONVERSIONS"] <= len(p._ench_convs)


def test_enchants_search_filters_conversions_by_item_id():
    """The search box matches a conversion's item id — the shortened
    suffix 'crittoap' finds the Crit→A.Pen row (its Epic and Rare items
    both carry it), 'rare_crittoap' matches only through the Rare id, and
    the shared 'demongearupgrade' prefix finds every conversion."""
    p = _page()
    p._items_set_mode("Enchants")
    p._ench_search.setText("crittoap")
    assert _counts(p)["CONVERSIONS"] == 1
    p._ench_search.setText("rare_crittoap")
    assert _counts(p)["CONVERSIONS"] == 1
    p._ench_search.setText("demongearupgrade")
    assert _counts(p)["CONVERSIONS"] == len(p._ench_convs)
    p._ench_search.clear()
    assert _counts(p)["CONVERSIONS"] == len(p._ench_convs)


def _gem_table_rows(p) -> list:
    """QLabel texts per grid row of the gem card's table, in visual order:
    row 0 is the header (GEM + stat shorts), zone headers and gem rows
    follow. Name cells are widgets holding a tile + label."""
    grid = _grid(p._ench_gems_lay)
    rows = []
    for r in range(grid.rowCount()):
        texts = []
        for c in range(grid.columnCount()):
            item = grid.itemAtPosition(r, c)
            if item is None:
                continue
            w = item.widget()
            if w is None:
                continue
            if isinstance(w, QtWidgets.QLabel) and w.text():
                texts.append(w.text())
            else:
                # the compound row widget holds the clickable name button
                # then the socket-slot badge — collect in visual order
                texts.extend(b.text() for b in w.findChildren(
                    QtWidgets.QPushButton) if b.text())
                texts.extend(ch.text() for ch in w.findChildren(
                    QtWidgets.QLabel) if ch.text())
        rows.append(texts)
    return rows


def _find_grid(lay) -> QtWidgets.QGridLayout | None:
    """The card body's ONE table, descending into nested layouts.

    Most bodies add the table straight to the card layout (scrolls / gems /
    conversions / elixirs / food), but some wrap it in a QVBoxLayout - so a
    direct-children-only lookup misses it. Every card holds at most one table,
    so the first hit is the whole grid either way. (The Upgrades tab is no
    longer a table at all: it is read through its own data, see
    _upgrade_cells.)
    """
    for i in range(lay.count()):
        sub = lay.itemAt(i).layout()
        if isinstance(sub, QtWidgets.QGridLayout):
            return sub
    for i in range(lay.count()):
        sub = lay.itemAt(i).layout()
        if sub is not None:
            got = _find_grid(sub)
            if got is not None:
                return got
    return None


def _grid(lay) -> QtWidgets.QGridLayout:
    """The card body's ONE table - raises when the card holds none."""
    got = _find_grid(lay)
    assert got is not None, "no QGridLayout in this card body"
    return got


def _grid_rows(lay) -> int:
    """Row count of a card body that's ONE table (the augments card has no
    column-header row, and neither do elixirs / food / the scrolls table)
    - 0 when the body holds no table at all (an empty 'no rows match' card)."""
    got = _find_grid(lay)
    return got.rowCount() if got is not None else 0


def _row_name(grid: QtWidgets.QGridLayout, r: int) -> str:
    """A one-table row's clickable name — the col-0 cell is either the
    name button itself or a stack (long grants wrap under the name)."""
    w = grid.itemAtPosition(r, 0).widget()
    if isinstance(w, QtWidgets.QPushButton):
        return w.text()
    for b in w.findChildren(QtWidgets.QPushButton):
        if b.text():
            return b.text()
    return ""


def _table_names(lay) -> list:
    """The clickable names of a one-table card body (elixirs / food) —
    the col-0 button texts, in row order."""
    grid = _grid(lay)
    return [_row_name(grid, r) for r in range(grid.rowCount())]


def _filter_stats(p) -> list:
    """Every stat the stat chips offer — the union of what the scrolls,
    gems, conversions and crafting consumables touch (mirrors the page's
    chip-set construction)."""
    return sorted({s for s in p._ench_scrolls}
                  | {s for c in p._ench_corrupt
                     for s in (c["stat"], c["penalty"])}
                  | {s for g in p._ench_gems for s in g["stats"]}
                  | {s for c in p._ench_convs
                     for s in (c["source"], c["target"])}
                  | {s["n"] for it in p._ench_elixirs + p._ench_foods
                     + p._ench_enchanter + p._ench_outfitter
                     + p._ench_blacksmith
                     for s in (idata.own_stats(it) or [])})


def _row_names(lay) -> list:
    """The clickable name of each augments row, in rendered order — the
    grid's col-0 name buttons (skipping the ▸/▾ sub-menu toggles)."""
    grid = _grid(lay)
    names = []
    for r in range(grid.rowCount()):
        cell = grid.itemAtPosition(r, 0)
        if cell is None:
            continue
        w = cell.widget()
        if w is None:
            continue
        btns = [b for b in w.findChildren(QtWidgets.QPushButton)
                if b.text() and b.text() not in ("▸", "▾")]
        if btns:
            names.append(btns[0].text())
    return names


def test_crafting_elixirs_and_food_cards():
    """The Enchants tab adds the crafting Elixirs (Alchemist) and Food
    dishes (Cook), both as serving rows showing the stat they grant
    ('+6 Strength', '+2 Vitality +2 Strength'); the job AND the craft
    duration ride the card title (elixirs 1H, food 15M), and the rows
    carry no craft meta (no MAT / GOLD); every row is craftable, a
    picked stat narrows them by the stat they grant (no jump off the
    tab), and the search narrows them by name, job, material and
    granted stat."""
    p = _page()
    p._items_set_mode("Enchants")
    assert len(p._ench_elixirs) == 12
    assert len(p._ench_foods) == 24
    assert all(idata.recipe(it["id"]) for it in p._ench_elixirs)
    assert all(idata.recipe(it["id"]) for it in p._ench_foods)
    # each card is ONE scrolls-style table: name · LV · grant
    assert len(_table_names(p._ench_elixirs_lay)) == 12
    assert len(_table_names(p._ench_foods_lay)) == 24
    assert _grid_rows(p._ench_elixirs_lay) == 12
    assert _grid_rows(p._ench_foods_lay) == 24
    # the durations ride the card titles — no repeated per-row tags; the
    # timer reads gold against the accent category/job text
    assert p._ench_elixirs_head._label.text() == \
        f"ELIXIRS · ALCHEMIST · <span style=\"color:{theme.GOLD};\">1H</span>"
    assert p._ench_foods_head._label.text() == \
        f"FOOD · COOK · <span style=\"color:{theme.GOLD};\">15M</span>"
    # each elixir row shows its granted stat in accent (11 of 12 grant
    # one — Abundance grants none); each food row shows its granted
    # stats too (23 of 24 — 'Eye Salad' has only the −4 Vitality
    # penalty). No row carries craft meta — duration, MAT, or GOLD
    elabels = [lb.text() for lb in p._ench_elixirs_card.findChildren(
        QtWidgets.QLabel) if lb.text()]
    assert "+6 Strength" in elabels and "+3 Dexterity" in elabels
    assert sum(1 for t in elabels if t.startswith("+")) == 11
    assert not any("MAT" in t or "GOLD" in t for t in elabels), elabels
    flabels = [lb.text() for lb in p._ench_foods_card.findChildren(
        QtWidgets.QLabel) if lb.text()]
    assert "+2 Vitality + +2 Strength" in flabels    # Flavored Candies
    # the Feast grants all five stats in one label (multi-stat joins)
    assert any("+15 Armor Penetration" in t for t in flabels)
    assert sum(1 for t in flabels if t.startswith("+")) == 23
    assert not any("MAT" in t or "GOLD" in t for t in flabels), flabels
    # durations come from the real item data: every elixir is 1H and
    # every dish 15M except the Plainswalker Feast (1H) — the Feast
    # carries a gold 1H pill beside its level; the 15M norm rides the
    # card title, so no other row repeats it
    fgrid = _grid(p._ench_foods_lay)
    feast = next(r for r in range(fgrid.rowCount())
                 if _row_name(fgrid, r) == "Plainswalker Feast")
    # the 1H gold pill rides next to the item name, inside its cell
    name_cell = fgrid.itemAtPosition(feast, 0).widget()
    pill = next(lb for lb in name_cell.findChildren(QtWidgets.QLabel)
                if lb.text() == "1H")
    assert theme.GOLD in pill.styleSheet()
    # the five-stat grant stays in the STATS column with the others —
    # wrapped accent, same column as every food row, no shifted cells
    feast_val = fgrid.itemAtPosition(feast, 2).widget()
    assert isinstance(feast_val, QtWidgets.QLabel)
    assert feast_val.text().startswith("+2 Vitality")
    assert theme.ACCENT in feast_val.styleSheet()
    assert all(fgrid.itemAtPosition(r, 3) is None
               for r in range(fgrid.rowCount()))
    # the Cauldron's four-stat grant wraps in the stats column too;
    # short grants stay as single-line accent value cells beside the level
    egrid = _grid(p._ench_elixirs_lay)
    cauldron = next(r for r in range(egrid.rowCount())
                    if _row_name(egrid, r) == "Minor Alchemist Cauldron")
    cval = egrid.itemAtPosition(cauldron, 2).widget()
    assert isinstance(cval, QtWidgets.QLabel)
    assert cval.text().startswith("+10 Strength")
    assert theme.ACCENT in cval.styleSheet()
    armor = next(r for r in range(egrid.rowCount())
                 if _row_name(egrid, r) == "Elixir of Armor")
    val = egrid.itemAtPosition(armor, 2).widget()
    assert val is not None and val.text() == "+400 Armor"
    # a stat filter now narrows the consumables by their granted stats
    # (the compiler stamped them) — the card stays put, filtered, and
    # clearing it restores the full set
    stats = _filter_stats(p)
    p._ench_tabs._btns["Elixirs"].click()
    assert p._ench_elixirs_card.isVisible()
    p._ench_stat.setCurrentIndex(stats.index("Strength") + 1)
    assert p._ench_elixirs_card.isVisible()          # no jump off
    assert _grid_rows(p._ench_elixirs_lay) == 3      # the Strength elixirs
    p._ench_stat.setCurrentIndex(0)   # back to All stats
    assert _grid_rows(p._ench_elixirs_lay) == 12
    p._ench_tabs._btns["Food"].click()
    assert p._ench_foods_card.isVisible()
    # the search matches materials ('sauce'), the job ('alchemist'), and
    # names — and clears back to the full set
    p._ench_search.setText("sauce")
    assert 0 < _grid_rows(p._ench_foods_lay) < 24
    p._ench_search.setText("alchemist")
    assert _grid_rows(p._ench_elixirs_lay) == 12
    assert _grid_rows(p._ench_foods_lay) == 0
    p._ench_search.setText("abundance")
    assert _grid_rows(p._ench_elixirs_lay) == 1
    p._ench_search.setText("intellect")
    assert _grid_rows(p._ench_elixirs_lay) == 3
    assert _grid_rows(p._ench_foods_lay) == 5
    p._ench_search.clear()
    assert _grid_rows(p._ench_foods_lay) == 24


def test_gem_card_renders_stat_columns():
    """The gem card is a table now: a GEM header plus one stat column per
    stat the database grants, values in their columns ('·' for stats a gem
    doesn't grant) — no per-row stat chips."""
    p = _page()
    p._items_set_mode("Enchants")
    rows = _gem_table_rows(p)
    assert rows[0][0] == "GEM"
    heads = rows[0]
    for full in ("CRITICAL", "FERVOR", "ARMOR PENETRATION",
                 "MAGIC PENETRATION", "VITALITY"):
        assert any(h.startswith(full) for h in heads), full
    # a gem row has its name, then its socket-slot badge (RING · NECK),
    # then one cell per stat column — the granted stat reads '+4' instead
    # of a chip
    gem_row = next(row for row in rows[1:] if "Cut Beryl" in row[0])
    assert len(gem_row) == 2 + len(p._ench_gem_cols)
    assert "RING · NECK" in gem_row
    assert "+4" in gem_row
    assert "·" in gem_row
    # no legend under the table — the −9 cells and +9s read from their
    # signed values, and the row tooltips spell out the drain
    gl = [lb.text() for lb in p._ench_gems_card.findChildren(
        QtWidgets.QLabel) if lb.text()]
    assert "−9 DRAINS" not in gl and "+9 GRANTS" not in gl


def test_gem_groups_highest_tier_first():
    """The gem groups read highest-tier first — Cursed (Lv 6) above Z2
    (Jeweller Lv 4) above Z1 (Jeweller Lv 2), since the higher tier
    grants the better stats — and each zone's gems sort strongest-first
    by their top stat value. The group order is asserted as the rule
    (tiers non-increasing), not the current zone names, so a future
    mixed-tier zone would still read top-down."""
    p = _page()
    p._items_set_mode("Enchants")
    rows = _gem_table_rows(p)
    heads = [row[0] for row in rows if row and "JEWELLER LV" in row[0]]
    assert [h.split(" · ")[0] for h in heads] == ["CURSED", "Z2", "Z1"]
    # the ordering rule: group tiers descend (highest first) — parsed
    # from the 'ZONE · JEWELLER LV N' headers, not hardcoded
    tiers = [int(h.split("LV ")[1].split(" ")[0]) for h in heads]
    assert tiers == sorted(tiers, reverse=True), tiers
    # within Z1 the +4/+4 gems read before Strong (Vitality +2)
    z1 = next(i for i, row in enumerate(rows)
              if row and row[0].startswith("Z1 · JEWELLER LV 2"))
    z1_names = [row[0] for row in rows[z1 + 1:] if row]
    assert z1_names[-1] == "Strong Cut Amber"


def test_gem_rows_show_tier_when_zone_is_mixed():
    """A zone with mixed tiers renders ONE group header (no tier suffix)
    and each row carries its own 'Lv N' tag, sorted highest tier first —
    so a future mixed-tier zone still reads top-down."""
    p = _page()
    p._items_set_mode("Enchants")
    fake = {"Z2": [
        {"item": "AttunedCutBeryl", "zone": "Z2", "level": 2,
         "stats": {"Magic Penetration": 4, "Fervor": 4}},
        {"item": "AttunedCutRuby", "zone": "Z2", "level": 4,
         "stats": {"Magic Penetration": 7, "Fervor": 7}}]}
    grid = p._build_gem_table(fake)
    texts = []
    for r in range(grid.rowCount()):
        for c in range(grid.columnCount()):
            it = grid.itemAtPosition(r, c)
            if it is None or it.widget() is None:
                continue
            w = it.widget()
            if isinstance(w, QtWidgets.QLabel) and w.text():
                texts.append(w.text())
            else:
                texts += ([ch.text() for ch in w.findChildren(
                    QtWidgets.QLabel) if ch.text()]
                          + [b.text() for b in w.findChildren(
                              QtWidgets.QPushButton) if b.text()])
    # one header without a tier suffix or gem count, plus a per-row
    # level tag each — and the socket-slot badge on every row
    assert "Z2" in texts
    assert "Z2 · 2 GEMS" not in texts
    assert "Lv 4" in texts and "Lv 2" in texts
    assert texts.count("RING · NECK") == 2
    assert not any(t.startswith("Z2 · JEWELLER") for t in texts)

    def row0(r):
        w = grid.itemAtPosition(r, 0).widget()
        return ([ch.text() for ch in w.findChildren(QtWidgets.QLabel)
                 if ch.text()]
                + [b.text() for b in w.findChildren(
                    QtWidgets.QPushButton) if b.text()])
    # the higher tier sorts first within the mixed zone (row 1 is the
    # zone header, below the GEM/stat header row)
    assert "Attuned Cut Ruby" in row0(2) and "Lv 4" in row0(2)
    assert "Attuned Cut Beryl" in row0(3) and "Lv 2" in row0(3)


def test_gem_table_headers_have_no_counts():
    """The gem table's stat headers carry their full names in identity
    colors (no live counts), and the zone headers name the zone and tier
    without their gem count."""
    p = _page()
    p._items_set_mode("Enchants")
    rows = _gem_table_rows(p)
    heads = rows[0]
    assert heads[0] == "GEM"
    for c, stat in enumerate(p._ench_gem_cols, start=1):
        assert heads[c] == stat.upper(), (stat, heads[c])
    # each stat header is painted in its identity color
    crit = next(lb for lb in p._ench_gems_card.findChildren(
        QtWidgets.QLabel) if lb.text() == "CRITICAL")
    vit = next(lb for lb in p._ench_gems_card.findChildren(
        QtWidgets.QLabel) if lb.text() == "VITALITY")
    assert theme.ORANGE in crit.styleSheet()
    assert theme.GOOD in vit.styleSheet()
    # zone headers carry the zone and tier only — no '· N GEMS' count
    zheads = [row[0] for row in rows if row and "JEWELLER LV" in row[0]]
    assert zheads == ["CURSED · JEWELLER LV 6",
                      "Z2 · JEWELLER LV 4",
                      "Z1 · JEWELLER LV 2"]


def test_enchants_search_composes_with_stat_chip():
    """The search narrows within the picked stat: with the Vitality chip
    active, a gem-name search can only return Vitality-granting gems, and
    the scrolls card keeps just the Vitality scroll."""
    p = _page()
    p._items_set_mode("Enchants")
    stat = "Vitality"
    assert stat in p._ench_scrolls
    stats = _filter_stats(p)
    p._ench_stat.setCurrentIndex(stats.index(stat) + 1)

    target = next(g for g in p._ench_gems if stat in g["stats"])
    name = (idata.item(target["item"]) or {}).get("name") or target["item"]
    p._ench_search.setText(name.split()[-1])
    counts = _counts(p)
    # the search narrows within the chip: 'amber' matches no scroll stat,
    # so the scrolls card empties even though the Vitality chip is active
    assert counts["SCROLLS"] == 0
    assert 0 < counts["GEMS"] <= sum(
        1 for g in p._ench_gems if stat in g["stats"])


def _enchant_ids() -> set:
    """Every item id the Enchants tab owns: the +2/corrupted scrolls, the
    gem augments and the demon-gear conversions."""
    ids = {g["item"] for g in idata.gem_augments()}
    ids |= {c["rare"]["item"] for c in idata.enchant_conversions()}
    ids |= {c["epic"]["item"] for c in idata.enchant_conversions()}
    ids |= {it["id"] for it in idata.items()
            if (it.get("id") or "").startswith("ScrollOf")}
    return ids


def test_enchant_items_not_indexed_in_list():
    """The enchant items are gone from the browse list — like armor on the
    Gear tab and mounts in the Codex, they live on the Enchants tab now."""
    ids = _enchant_ids()
    assert ids, "enchant ids should resolve"
    for iid in ids:
        it = idata.item(iid)
        assert it and support.is_enchant_item(it), iid
        assert not support.is_listable_item(it), iid
    # the Cursed Eyes sit on the Enchants tab too (they leave the list);
    # the other augment families stay listable
    assert support.is_enchant_item(idata.item("BrutalityCursedCutEye"))
    assert not support.is_enchant_item(idata.item("HonedCopperPlate"))
    assert not support.is_enchant_item(idata.item("FormulaHandsVitality_Z2"))
    assert support.is_listable_item(idata.item("HonedCopperPlate"))
    # the page list carries none of them
    p = _page()
    listed = {p._items_list.item(i).data(support.ID_ROLE)
              for i in range(p._items_list.count())
              if not p._items_list.item(i).data(support.TYPE_HEADER_ROLE)}
    assert not (ids & listed)




def test_scroll_rows_jump_to_recipe():
    """The scrolls card rows share the Enchanter card's recipe affordance:
    clicking a scroll name opens its recipe on the Craft page — the plain
    +2 scroll by stat, the corrupted one by item id. Each row also
    carries its ENCHANTER · LV tag (LV 1 plain, LV 6 corrupted) like the
    crafted Food/Elixir rows."""
    p = _page()
    p._items_set_mode("Enchants")
    calls = []
    p._items_open_in_craft = lambda iid: calls.append(iid)
    btns = {b.text(): b for b in p.findChildren(QtWidgets.QPushButton)
            if b.text().startswith("Scroll of ")}
    btns["Scroll of Dexterity"].click()
    btns["Scroll of Corrupted Dexterity"].click()
    assert calls == ["ScrollOfDexterity", "ScrollOfCorruptedDexterity"]
    # the ENCHANTER · LV tag column sits beside each name
    grid = _grid(p._ench_scrolls_lay)
    lv_by_row = {_row_name(grid, r):
                 grid.itemAtPosition(r, 1).widget().text()
                 for r in range(grid.rowCount())}
    assert lv_by_row["Scroll of Dexterity"] == "ENCHANTER · LV 1"
    assert lv_by_row["Scroll of Corrupted Dexterity"] == "ENCHANTER · LV 6"


def test_gem_rows_jump_to_recipe():
    """The gem table rows share the recipe affordance too: clicking a gem
    name opens its Jeweller recipe on the Craft page."""
    p = _page()
    p._items_set_mode("Enchants")
    calls = []
    p._items_open_in_craft = lambda iid: calls.append(iid)
    gems = {b.text(): b for b in p.findChildren(QtWidgets.QPushButton)}
    gems["Attuned Cut Beryl"].click()
    gems["Cursed Eye of Brutality"].click()
    assert calls == ["AttunedCutBeryl", "BrutalityCursedCutEye"]
    cursed = gems["Cursed Eye of Brutality"]
    assert cursed.toolTip() == ""   # no mouse-over tooltips on the Items page


def test_category_tabs_switch_one_full_width_group():
    """Mockup-B layout: an underline tab bar (one category per tab)
    replaces the section dropdown and the two-column split — the default
    is Gems, each tab shows exactly its card full width, the durations
    live on the card tags, and picking a stat narrows every card in
    place, including the consumables by the stat they grant."""
    p = _page()
    p._items_set_mode("Enchants")
    tabs = p._ench_tabs
    assert tabs.currentText() == "Gems"
    assert p._ench_gems_card.isVisible()
    assert not p._ench_scrolls_card.isVisible()
    assert list(tabs._btns) == ["Gems", "Augments", "Conversions",
                                "Elixirs", "Food", "Scrolls", "Infusions",
                                "Upgrades"]
    # the card titles carry what the rows share — the scrolls' 30M
    # duration, the job names (only Alchemists craft elixirs, only
    # Cooks the dishes) — and no count tags (every row is visible). The
    # durations read GOLD (the timer is the point), the category/job text
    # keeps the accent title color
    assert p._ench_scrolls_head._label.text() == \
        f"ENCHANT SCROLLS · <span style=\"color:{theme.GOLD};\">30M</span>"
    assert p._ench_scrolls_head._tag.text() == ""
    assert p._ench_elixirs_head._label.text() == \
        f"ELIXIRS · ALCHEMIST · <span style=\"color:{theme.GOLD};\">1H</span>"
    assert p._ench_elixirs_head._tag.text() == ""
    assert p._ench_foods_head._label.text() == \
        f"FOOD · COOK · <span style=\"color:{theme.GOLD};\">15M</span>"
    assert p._ench_foods_head._tag.text() == ""
    # each tab shows only its card, full width
    for key, card in (("Gems", p._ench_gems_card),
                      ("Conversions", p._ench_convs_card),
                      ("Upgrades", p._ench_upgrades_card),
                      ("Elixirs", p._ench_elixirs_card),
                      ("Food", p._ench_foods_card),
                      ("Infusions", p._ench_infusions_card),
                      ("Augments", p._ench_augments_card)):
        tabs._btns[key].click()
        assert card.isVisible(), key
        assert not p._ench_scrolls_card.isVisible(), key
        assert sum(c.isVisible() for c in (p._ench_scrolls_card,
                                           p._ench_gems_card,
                                           p._ench_convs_card,
                                           p._ench_upgrades_card,
                                           p._ench_elixirs_card,
                                           p._ench_foods_card,
                                           p._ench_infusions_card,
                                           p._ench_augments_card)) == 1, key
    # a stat filter narrows the consumables in place — no jump off
    tabs._btns["Food"].click()
    stats = _filter_stats(p)
    p._ench_stat.setCurrentIndex(stats.index("Vitality") + 1)
    assert tabs.currentText() == "Food"              # stays on the tab
    assert p._ench_foods_card.isVisible()            # filtered in place
    assert _grid_rows(p._ench_foods_lay) == 24       # all dishes grant Vitality
    # clearing the stat restores the full set
    p._ench_stat.setCurrentIndex(0)
    assert _grid_rows(p._ench_foods_lay) == 24


def _upgrade_cells(p):
    """{(rank, rarity, material short): text} for every cell of the Upgrades
    table, read from the page's OWN data rather than from widget geometry.

    The cards are drawn from `_ench_upgrade_table`, so asserting here pins the
    NUMBERS however a card is laid out. The old helper walked grid coordinates,
    which meant any restyle broke the test even when every figure was right.
    """
    table = p._ench_upgrade_table
    assert table is not None, "no upgrade table was built"
    return {(c["rank"], c["rarity"], c["short"]): c["text"]
            for c in table["cells"]}


def _upgrade_grids(p) -> list:
    """The Upgrades card body's two grids: the summary tiles, then the
    per-step matrix.

    Walks the layout tree rather than findChildren: a refilter reparents and
    deleteLater()s the old widgets, and in a headless run those linger
    findChildren-visible (see tests/qt_helpers.drain_deleted).
    """
    out: list = []

    def walk(lay) -> None:
        for i in range(lay.count()):
            it = lay.itemAt(i)
            sub = it.layout()
            if sub is None and it.widget() is not None:
                sub = it.widget().layout()      # a wrapper with its own layout
            if isinstance(sub, QtWidgets.QGridLayout):
                out.append(sub)
            elif sub is not None:
                walk(sub)
    walk(p._ench_upgrades_lay)
    return out


def _tiles(grid) -> list:
    """The summary tiles in the tile grid, in column order."""
    return [grid.itemAt(i).widget() for i in range(grid.count())
            if grid.itemAt(i).widget() is not None
            and grid.itemAt(i).widget().objectName() == "UpgradeTile"]


def _cell(grid, row: int, col: int) -> str:
    """One matrix cell's text (empty when the position holds no widget)."""
    it = grid.itemAtPosition(row, col)
    w = it.widget() if it is not None else None
    return w.text() if isinstance(w, QtWidgets.QLabel) else ""


def _cell_widget(grid, row: int, col: int):
    """One matrix position's widget (None when the position is empty)."""
    it = grid.itemAtPosition(row, col)
    return it.widget() if it is not None else None


def test_upgrades_tab_totals_over_matrix():
    """The Upgrades tab is three summary tiles over one per-step matrix.

    Each tile names its rarity and cap and carries that rarity's real
    per-material totals (summed from the same cells the matrix prints); the
    matrix below spans one column group per rarity with the material icons over
    their columns. The `+N` is a row header INSIDE the grid — zero horizontal
    spacing and the same row band as the cells beside it — so it reads as part
    of the table. Every figure is asserted through the page's data.

    The numbers themselves are unchanged from the old grid: an uncharged
    material still reads an explicit 0 and a past-cap rank '—'.
    """
    p = _page()
    p._items_set_mode("Enchants")
    p._ench_tabs._btns["Upgrades"].click()
    assert p._ench_upgrades_card.isVisible()
    assert p._ench_upgrade_type == "Weapon"
    assert _counts(p)["UPGRADES"] == 3
    # nothing in here is width-bounded any more — no capped block, no tile with
    # a maximum: the tab fills the page and scales there (the geometry that
    # backs that is test_upgrades_tab_fills_the_width)
    assert not [w for w in p._ench_upgrades_card.findChildren(QtWidgets.QWidget)
                if w.objectName() == "UpgradeBlock"]
    grids = _upgrade_grids(p)
    assert len(grids) == 2                  # tiles, then the matrix
    tiles = _tiles(grids[0])
    assert len(tiles) == 3
    tile_text = [[lb.text() for lb in t.findChildren(QtWidgets.QLabel)]
                 for t in tiles]
    # each tile: the rarity, its cap, its real totals, and how many steps —
    # and the material lines follow the ROSTER in order, a material the rarity
    # never charges shown as a dim 0 (Rare has no Spark Crystal), so the three
    # tiles' lines line up row for row instead of Rare's card ending early
    assert "RARE" in tile_text[0] and "→ +3" in tile_text[0]
    assert "341" in tile_text[0] and "57" in tile_text[0]
    assert "0" in tile_text[0]
    assert tile_text[0].index("341") < tile_text[0].index("57") < tile_text[0].index("0")
    assert "EPIC" in tile_text[1] and "→ +4" in tile_text[1]
    assert "714" in tile_text[1] and "144" in tile_text[1] and "3" in tile_text[1]
    assert "LEGENDARY" in tile_text[2] and "→ +5" in tile_text[2]
    assert "1221" in tile_text[2] and "274" in tile_text[2] and "18" in tile_text[2]
    # every tile shows the material art, not just a coloured number
    assert all(t.findChildren(C.IconTile) for t in tiles)
    # ...as THREE COLUMNS, one per material, each an icon over a figure over a
    # name — stacked lines left the card ragged (a fixed-width figure column
    # with a name of varying length after it), so three equal-width cards read
    # as three different widths. The columns are sub-layouts of ONE row, each
    # with equal stretch, which is what makes every card end flush.
    for t in tiles:
        rows = [t.layout().itemAt(i).layout() for i in range(t.layout().count())]
        mats = next(r for r in rows if isinstance(r, QtWidgets.QHBoxLayout)
                    and r is not t.layout().itemAt(0).layout())
        assert mats.count() == 3
        assert all(mats.itemAt(i).layout() is not None for i in range(3))
        assert all(mats.stretch(i) == 1 for i in range(3))
        # the figure sits ABOVE the name inside its column
        for i in range(3):
            cell = mats.itemAt(i).layout()
            labels = [cell.itemAt(j).widget() for j in range(cell.count())]
            counts = [lb for lb in labels
                      if isinstance(lb, QtWidgets.QLabel)
                      and lb.alignment() & QtCore.Qt.AlignHCenter]
            assert counts and counts[0].text()
    # the totals ARE the matrix's cells summed (level = the bundled max)
    table = p._ench_upgrade_table
    assert table["level"] == (idata.gear_scaling().get("max_level") or 25)
    assert [round(sum(c["count"] or 0 for c in table["cells"]
                      if c["rarity"] == r)) for r in
            ("Rare", "Epic", "Legendary")] == [398, 861, 1513]
    # the matrix: one OUTLINED LADDER per rarity, aligned under its tile — a
    # plain 1px border around the rows (no card chrome: the tile above carries
    # the name, cap and top rule). The ladders sit in their own grid, three
    # across, sharing the width.
    mx = grids[1]
    assert mx.columnCount() == 3
    ladders = [mx.itemAtPosition(0, c).widget() for c in range(3)]
    assert all(w.objectName() == "UpgradeLadderBox" for w in ladders)
    assert (f"border:1px solid {theme.BORDER}"
            in ladders[0].styleSheet())          # the outline, and nothing else
    assert "border-top:2px" not in ladders[0].styleSheet()
    inner = ladders[0].layout()
    assert isinstance(inner, QtWidgets.QGridLayout)
    # ...and a gutter inside it on ALL FOUR sides: with none left/right the row
    # rules ran into the outline and the head icons sat flush against it, so
    # the frame read as uneven against the tile above
    m = inner.contentsMargins()
    assert m.left() == m.right() and m.top() == m.bottom()
    assert m.left() > 0 and m.top() > 0
    # inside: ranks in the rarity's colour on the page background, icon-only
    # heads with the name on the tooltip, faint rarity rules between rows
    assert [_cell(inner, r, 0) for r in range(1, 6)] == [
        "+1", "+2", "+3", "+4", "+5"]
    rank = _cell_widget(inner, 1, 0)
    # the rank wears the SAME accent as the figure beside it, not the rarity
    # colour: a purple or orange +N among blue values read as a different kind
    # of number, when it is the same table. The rarity owns the rules, the
    # outline and the tile above — not the digits.
    assert f"color:{theme.ACCENT}" in rank.styleSheet()
    assert theme.rarity_color("Rare") not in rank.styleSheet()
    assert "background:transparent" in rank.styleSheet()
    assert _cell_widget(inner, 0, 1).text() == '<img src="icon://UpgradeAll">'
    # ...and NO tooltip anywhere in the card, the rest of the Items page's rule:
    # the heads only repeat the names the tile columns already print, and the
    # cells' provenance explanation belongs in docs/UPGRADE_COSTS.md, not behind
    # a hover. Colour is the whole trust signal.
    tippy = [w for w in p._ench_upgrades_card.findChildren(QtWidgets.QWidget)
             if w.toolTip()]
    assert tippy == [], [w.toolTip() for w in tippy]
    # the cells carry the figures: Rare +1 Dust measured (accent), Epic +2 Dust
    # derived from Rare x the rarity multiplier, Rare +4 '—' past the cap
    assert _cell(inner, 1, 1) == "53"
    assert theme.ACCENT in _cell_widget(inner, 1, 1).styleSheet()
    assert ("border-bottom:1px solid "
            f"{theme.with_alpha(theme.rarity_color('Rare'), 50)}"
            in _cell_widget(inner, 1, 1).styleSheet())
    epic_inner = ladders[1].layout()
    assert _cell(epic_inner, 2, 1) == "136"
    # Epic +2 at 25 is DERIVED (Rare's 112 x 1.2117) and still wears the same
    # accent as the measured cells beside it: a derived figure is an amount you
    # pay, and with no tooltips in the tab a dim number was a lesser number for
    # a reason the reader could not see.
    assert theme.ACCENT in _cell_widget(epic_inner, 2, 1).styleSheet()
    assert theme.DIM not in _cell_widget(epic_inner, 2, 1).styleSheet()
    assert _cell(inner, 4, 1) == "—"
    assert theme.DIM in _cell_widget(inner, 4, 1).styleSheet()
    cells = _upgrade_cells(p)
    # every material in its own cell: Rare +2 = Dust 112 + Shard 18,
    # Epic +4 = Dust 300 + Shard 75 + Crystal 3, Legendary +5 = Dust 434
    # + Shard 115 + Crystal 15 (measured level-25 costs, not the formula)
    assert cells[(2, "Rare", "Dust")] == "112"
    assert cells[(2, "Rare", "Shard")] == "18"
    assert cells[(4, "Epic", "Dust")] == "300"
    assert cells[(4, "Epic", "Shard")] == "75"
    assert cells[(4, "Epic", "Crystal")] == "3"
    assert cells[(5, "Legendary", "Dust")] == "434"
    assert cells[(5, "Legendary", "Shard")] == "115"
    assert cells[(5, "Legendary", "Crystal")] == "15"
    assert cells[(1, "Rare", "Dust")] == "53"
    # an uncharged material reads an explicit 0, never a missing cell:
    # Crystal costs nothing until Epic +4, Shard nothing at +1
    assert cells[(1, "Rare", "Crystal")] == "0"
    assert cells[(2, "Rare", "Crystal")] == "0"
    assert cells[(3, "Epic", "Crystal")] == "0"
    assert cells[(1, "Legendary", "Shard")] == "0"
    # ...and that 0 reads DIM, like the past-cap —: neither is a price, so
    # neither may sit in the accent channel among the figures that are
    assert theme.DIM in _cell_widget(inner, 1, 2).styleSheet()  # Rare +1 Shard
    assert theme.ACCENT not in _cell_widget(inner, 1, 2).styleSheet()
    assert theme.DIM in _cell_widget(inner, 1, 3).styleSheet()  # Rare +1 Crystal
    assert theme.DIM in _cell_widget(inner, 4, 1).styleSheet()  # past-cap —
    # past-the-cap ranks read '—': Rare has no +4/+5, Epic no +5
    assert cells[(4, "Rare", "Dust")] == "—"
    assert cells[(5, "Rare", "Dust")] == "—"
    assert cells[(5, "Epic", "Dust")] == "—"
    # ARMOR is hidden: its upgrades are not in the game yet, so the gear
    # row carries only the Weapon chip and the table stays on weapons
    assert "Armor" not in p._ench_upgrade_btns
    assert p._ench_upgrade_type == "Weapon"
    # the search narrows the rarities (shard = Rare only)
    p._ench_search.setText("shard")
    assert _counts(p)["UPGRADES"] == 1
    shcells = _upgrade_cells(p)
    assert shcells[(2, "Rare", "Shard")] == "18"
    assert {r for (_, r, _) in shcells} == {"Rare"}
    grids = _upgrade_grids(p)
    assert len(_tiles(grids[0])) == 1
    # the material roster follows the LIVE columns: with only Rare left nothing
    # charges a Spark Crystal, so the group is two columns wide, not three
    assert p._ench_upgrade_table["roster"] == ["Spark Dust", "Spark Shard"]
    # the narrowed table keeps the panels' three-slot layout: one ladder panel
    # plus two empty stretch columns, the same way the tile row behaves
    assert grids[1].columnCount() == 3
    p._ench_search.setText("")
    assert _counts(p)["UPGRADES"] == 3
    assert len(_tiles(_upgrade_grids(p)[0])) == 3
    assert p._ench_upgrade_table["roster"] == ["Spark Dust", "Spark Shard",
                                               "Spark Crystal"]
    # nothing matches -> the table is gone and the card says so
    p._ench_search.setText("no-such-material")
    assert p._ench_upgrade_table is None
    assert _upgrade_grids(p) == []
    assert any("No upgrade material matches" in lb.text()
               for lb in p._ench_upgrades_card.findChildren(QtWidgets.QLabel))


def test_upgrades_tab_offers_the_measured_levels_only():
    """The LV chips are the levels the costs were MEASURED at, not a schedule.

    The tab used to offer `max_level` and `max_level + 5` — a level the game
    has no tier for yet, priced by extending the measured curve. Offering a
    level nothing was read at is exactly the thing the provenance chain exists
    to avoid, so the chips read `stats.upgrade_cost_levels()`: the anchor
    rarity's own measured levels, capped at the game's max (20 and 25), with
    the highest as the default. Measure Rare at a new level in
    `_MEASURED_UPGRADES` and the chip appears with no change here.
    """
    p = _page()
    p._items_set_mode("Enchants")
    p._ench_tabs._btns["Upgrades"].click()
    levels = idata.upgrade_cost_levels()
    assert levels == sorted(set(levels)) and levels
    assert max(levels) <= (idata.gear_scaling().get("max_level") or 25)
    assert [b.text() for b in p._ench_upgrade_lvl_btns.values()] == [
        f"LV {lv}" for lv in levels]
    # the tab opens on the highest offered level — the cap the game allows
    assert p._ench_upgrade_level == max(levels)
    assert p._ench_upgrade_table["level"] == max(levels)


def test_upgrades_tab_reprices_at_the_other_measured_level():
    """Clicking the lower chip really re-reads the table at that level, and
    the figures are the ones measured there — 20 is a full Rare reading, so
    Rare's column is exact and Epic/Legendary derive from it."""
    p = _page()
    p._items_set_mode("Enchants")
    p._ench_tabs._btns["Upgrades"].click()
    low = min(idata.upgrade_cost_levels())
    p._ench_upgrade_lvl_btns[low].click()
    assert p._ench_upgrade_level == low
    assert p._ench_upgrade_table["level"] == low
    cells = _upgrade_cells(p)
    # the measured level-20 rows, not the level-25 ones scaled down
    assert cells[(1, "Rare", "Dust")] == "41"
    assert cells[(2, "Rare", "Dust")] == "87"
    assert cells[(2, "Rare", "Shard")] == "14"
    assert cells[(3, "Rare", "Shard")] == "30"
    # nothing on an offered level is a guess: every live cell is measured,
    # interpolated or derived (never the sheet's unverified formula)
    live = [c for c in p._ench_upgrade_table["cells"] if c["live"]]
    assert {c["source"] for c in live} <= {"measured", "interpolated", "derived"}
    # ...and NONE of them is painted dim for it. Level 20 is where this bites:
    # Epic +1/+2 and all five Legendary steps are derived there, so a
    # provenance-coloured tab showed eight of its fifteen real figures dim.
    for c in live:
        if not c["count"]:
            continue        # an uncharged 0 is dim by design — it is not a price
        style = p._upgrade_cell(c, theme.rarity_color(c["rarity"]),
                                low, True).styleSheet()
        assert theme.DIM not in style, (c["rarity"], c["rank"], c["source"])
    # and the tile above the ladder carries the new level in its footer
    tile = _tiles(_upgrade_grids(p)[0])[0]
    assert f"at level {low}" in " ".join(
        lb.text() for lb in tile.findChildren(QtWidgets.QLabel))


def test_upgrades_tab_fills_the_width():
    """The tab FILLS the page it is given, and scales as the page grows.

    Both growth channels, measured on live geometry — the half a stylesheet
    assertion cannot see:

      * the tiles share the whole row, so a wider page widens every tile (and
        their material lines flow into it, see _TileFlow);
      * the matrix's material columns share the width evenly, so the table ends
        flush with the card's edge instead of leaving a dead band at the right
        (what the old 1150px body did).
    """
    p = _page()
    p._items_set_mode("Enchants")
    p._ench_tabs._btns["Upgrades"].click()
    # the page has a minimum width of its own, so measure the narrow case at a
    # window that is still at or below it rather than at "the default"
    p.resize(1200, 900)
    for _ in range(4):
        QtWidgets.QApplication.processEvents()
    tgrid, mx = _upgrade_grids(p)
    # ints, not the widgets: the same QWidget reports the NEW width after the
    # window grows, so a held reference would compare a number with itself
    card0 = p._ench_upgrades_card.width()
    tile0 = _tiles(tgrid)[0].width()
    # the ladders are bare layouts now — measure the ladder GRID's extent
    def ladder_width(g):
        xs = [g.cellRect(0, c).x() for c in range(g.columnCount())]
        return xs[-1] - xs[0] + g.cellRect(0, g.columnCount() - 1).width()
    ladder0 = ladder_width(mx)
    p.resize(3200, 900)
    for _ in range(4):
        QtWidgets.QApplication.processEvents()
    assert p._ench_upgrades_card.width() > card0     # the page did get wider
    tile = _tiles(tgrid)[0]
    assert tile.width() > tile0                      # the tiles share the row
    assert ladder_width(mx) > ladder0                # and the ladders share it
    # nothing is left hanging at the right: the row ends at the card's edge
    rect = mx.cellRect(0, mx.columnCount() - 1)
    assert rect.x() + rect.width() >= p._ench_upgrades_card.width() - 24
    # a search that leaves ONE rarity keeps the ladders' three-slot layout (the
    # grid carries empty stretch columns), so the lone ladder stays ladder-sized
    p._ench_search.setText("shard")
    for _ in range(4):
        QtWidgets.QApplication.processEvents()
    solo = _upgrade_grids(p)
    assert len(solo) == 2
    assert (solo[1].itemAtPosition(0, 0).widget().objectName()
            == "UpgradeLadderBox")


def test_refilter_keeps_card_headers_alive():
    """Re-filtering clears only the card row bodies, never the
    SectionHeader — after deferred deletes flush (see qt_helpers, the
    way the main event loop does), a refilter and a direct set_tag still
    work. The old code cleared the whole card layout, so the header's tag
    label was deleteLater'd and died on the next refilter (RuntimeError:
    C++ object already deleted)."""
    from tests.qt_helpers import drain_deleted

    p = _page()
    p._items_set_mode("Enchants")
    p._ench_search.setText("vitality")
    drain_deleted()
    p._ench_search.setText("")
    assert _counts(p)["SCROLLS"] == 9
    # a direct set_tag would raise if the refilter had deleted the header
    p._ench_scrolls_head.set_tag("9 SCROLLS · 30M")
    assert p._ench_scrolls_head._tag.text() == "9 SCROLLS · 30M"


def test_enchant_depth_counts_database_items_per_stat():
    """support.enchant_depth totals every scroll / gem / conversion that
    touches a stat — Vitality is the plain +2 scroll, the four corrupted
    drains and the two Strong gems; Strength and Faith are just the plain
    + corrupted scrolls."""
    assert support.enchant_depth("Vitality") == 1 + len(
        idata.corrupted_scrolls()) + 2
    assert support.enchant_depth("Strength") == 2
    assert support.enchant_depth("Faith") == 2



def test_craft_open_in_items_routes_enchant_outputs():
    """The craft page's OPEN IN ENCHANTS link for an enchant recipe output
    (e.g. Attuned Cut Beryl) jumps to the Enchants tab instead of the
    browse list, where the item no longer indexes."""
    p = _CraftPage()
    page = p._page_gear()
    QtWidgets.QVBoxLayout(p).addWidget(page)
    p.show()
    p._craft_open_in_items("AttunedCutBeryl")
    assert p._nav == "gear"
    assert p._items_mode == "enchants"
    assert p._items_body.currentWidget() is p._items_enchants_scroll
    # a normal gear output still opens on the Gear tab, selected
    p._craft_open_in_items("Axe_Boomerang")
    assert p._items_mode == "gear"
    assert p._items_body.currentWidget() is p._items_browse
    assert p._items_list.currentItem().data(support.ID_ROLE) \
        == "Axe_Boomerang"


def test_headless_driver_rejects_hidden_leftover_items():
    """FAREVER_OPEN_ITEM only drives VISIBLE rows now — the leftover
    non-gear items (Gold, ores, recipe scrolls, tools) left the browse
    list when the Items tab was removed, so selecting one must fail
    loudly (return False) instead of building a dead selection; visible
    gear still opens."""
    p = _page()
    assert p._items_headless_open("Axe_Boomerang", False)
    assert p._items_list.currentItem().data(support.ID_ROLE) \
        == "Axe_Boomerang"
    for leftover in ("Gold", "IronOre", "Recipe_HonedCopperPlate"):
        assert not p._items_headless_open(leftover, False), leftover


def test_leaving_enchants_restores_browse_mode():
    """Switching back to Gear restores the browse body and its filters
    rebuild for the mode (Gear is the only browse tab now)."""
    p = _page()
    p._items_set_mode("Enchants")
    assert p._items_body.currentWidget() is p._items_enchants_scroll
    p._items_set_mode("Gear")
    assert p._items_mode == "gear"
    assert p._items_body.currentWidget() is p._items_browse


def test_corrupted_scrolls_show_signed_trade():
    """The corrupted scrolls join the scrolls table with the FULL trade in
    their value column — the +4 stat they enchant AND the −4 Vitality they
    drain ('+4 Dex → −4 Vit'), the same two-value treatment as the
    conversions — so the − side of a scroll reads in the columns, not
    hidden (the bundled scan records these scrolls with no stats at all)."""
    p = _page()
    p._items_set_mode("Enchants")
    assert _counts(p)["SCROLLS"] == len(p._ench_scrolls) + len(p._ench_corrupt)
    corrupt = next(c for c in p._ench_corrupt if c["stat"] == "Dexterity")
    nm = f"Scroll of Corrupted {corrupt['stat']}"
    grid = _grid(p._ench_scrolls_lay)
    row_texts = []
    plain = None
    for r in range(grid.rowCount()):
        texts = [_row_name(grid, r)]
        # col 1 is the ENCHANTER · LV tag, col 2 the value cell
        for c in (1, 2):
            cell = grid.itemAtPosition(r, c).widget()
            if isinstance(cell, QtWidgets.QLabel):
                texts.append(cell.text())
            elif cell is not None:
                texts += [lb.text() for lb in cell.findChildren(
                    QtWidgets.QLabel) if lb.text()]
        if texts[0] == nm:
            row_texts = texts
        elif texts[0] == "Scroll of Dexterity":
            plain = texts
    assert row_texts, "corrupted Dexterity row not found"
    assert "+4 Dex" in row_texts and "−4 Vit" in row_texts \
        and "→" in row_texts
    # the plain +2 scroll still reads as a single + value
    assert plain and "+2" in plain
    # the search finds the corrupted family by their name word
    p._ench_search.setText("corrupted")
    assert _counts(p)["SCROLLS"] == len(p._ench_corrupt)


def test_cursed_eye_gems_show_penalty_in_columns():
    """The Cursed Eye gems read their +/− in the stat columns: the three
    +9s in accent and the one −9 penalty in danger red — not tucked into a
    tooltip."""
    p = _page()
    p._items_set_mode("Enchants")
    grid = _grid(p._ench_gems_lay)
    found = False
    for r in range(grid.rowCount()):
        item = grid.itemAtPosition(r, 0)
        w = item.widget() if item is not None else None
        if w is None:
            continue
        names = ([ch.text() for ch in w.findChildren(QtWidgets.QLabel)
                  if ch.text()]
                 + [b.text() for b in w.findChildren(
                     QtWidgets.QPushButton) if b.text()])
        if not any("Brutality" in n for n in names):
            continue
        found = True
        pos = grid.itemAtPosition(r, 1).widget()
        pen = grid.itemAtPosition(r, 4).widget()
        dash = grid.itemAtPosition(r, 5).widget()
        assert pos.text() == "+9" and theme.ACCENT in pos.styleSheet()
        assert pen.text() == "−9" and theme.DANGER in pen.styleSheet()
        assert dash.text() == "·"
        break
    assert found, "Cursed Eye of Brutality row not found"


def test_header_has_no_legend_labels():
    """The page header carries no legend — the +/− signed-value coloring
    is read from the rows themselves (+4 → −4, the −9 penalties), so both
    the '+ gained' and '− drained' labels are gone."""
    p = _page()
    p._items_set_mode("Enchants")
    labels = [lb.text() for lb in p.findChildren(QtWidgets.QLabel)
              if lb.text() in ("+ gained", "− drained")]
    assert labels == []


def test_augments_card_merges_the_profession_recipes():
    """The three profession recipe lists merge into one sortable augments
    card — the Enchanter +2/corrupted scrolls and Magic Formulas, the
    Outfitter Soft Embroideries, the Blacksmith Bronze Plates. Clickable
    NAME / JOB / LV / STATS column headers re-order the rows (the JOB
    default keeps the profession order); every row badged with its job
    color and slot, showing the stats it grants, each jumping to its
    Craft-page recipe: searchable by name/job/material, and narrowed in
    place by a picked stat."""
    p = _page()
    p._items_set_mode("Enchants")
    p._ench_tabs._btns["Augments"].click()
    assert len(p._ench_enchanter) == 34
    assert len(p._ench_outfitter) == 7
    assert len(p._ench_blacksmith) == 7
    rows = p._ench_enchanter + p._ench_outfitter + p._ench_blacksmith
    assert all(idata.recipe(it["id"]) for it in rows)
    assert _grid_rows(p._ench_augments_lay) == 48
    # no card header — the job sub-headers already say what's inside
    assert not any(lb.text().startswith("CRAFTABLE") for lb in
                   p._ench_augments_card.findChildren(QtWidgets.QLabel))
    # every row shows the stats it grants (43 of 48 — only the five
    # weapon formulas carry no flat stats; the corrupted scrolls now
    # read their +4 grant AND −4 Vitality drain)
    glabels = [lb.text() for lb in p._ench_augments_card.findChildren(
        QtWidgets.QLabel) if lb.text().startswith("+")]
    assert "+2 Strength + +2 Dexterity" in glabels  # Honed Bronze Plate
    assert "+2 Intellect + +2 Faith" in glabels     # Divine Embroidery
    assert "+15 Critical" in glabels                # Formula: Critical
    assert "+4 Strength" in glabels                 # corrupted scroll
    drain = next(lb for lb in p._ench_augments_card.findChildren(
        QtWidgets.QLabel) if lb.text() == "−4 Vitality")
    assert theme.DANGER in drain.styleSheet()
    assert sum(1 for t in glabels if t.startswith("+")) == 43
    # the five weapon Magic Formulas carry no flat stats — each row
    # shows its resolved enchant effect IN THE STATS column instead, so
    # a weapon enchant compares against the stat rows without opening
    # Craft (Devote = stacking Fervor, Zealot = stacking Critical
    # Chance, the procs and the kill-heal)
    notes = [lb.text() for lb in p._ench_augments_card.findChildren(
        QtWidgets.QLabel)
        if lb.text().startswith(("Your Attacks", "Your Combo Attack",
                                 "Participating"))]
    assert len(notes) == 5
    assert any("Fervor" in t and "stacking" in t for t in notes)     # Devote
    assert any("Critical Chance" in t and "stacking" in t
               for t in notes)                                         # Zealot
    assert any("30% chance" in t for t in notes)      # Flaming Weapon
    assert any("50% chance" in t for t in notes)      # Lifestealing
    assert any("killing" in t for t in notes)         # Spark Harvesting
    # the note sits in the STATS column, not under the name — the
    # Flaming Weapon row's col-3 cell IS the effect, its name cell clean
    grid = _grid(p._ench_augments_lay)
    fr = next(r for r in range(grid.rowCount())
              if "Magic Formula: Flaming Weapon" in
              [b.text() for b in
               grid.itemAtPosition(r, 0).widget().findChildren(
                   QtWidgets.QPushButton) if b.text()])
    n3 = grid.itemAtPosition(fr, 3).widget()
    assert isinstance(n3, QtWidgets.QLabel) and "30% chance" in n3.text()
    n0 = grid.itemAtPosition(fr, 0).widget()
    assert not any(lb.text().startswith("Your Attacks")
                   for lb in n0.findChildren(QtWidgets.QLabel))
    # no column-header row — the table starts at row 0, and the STATS
    # column stretches to take the leftover width
    assert grid.itemAtPosition(0, 0).widget() is not None
    assert grid.columnMinimumWidth(3) == 380
    # every row carries its job badge, in the job color — no level (the
    # card sorts highest first, so the rows needn't repeat it)
    badges = [lb for lb in p._ench_augments_card.findChildren(
        QtWidgets.QLabel)
        if lb.text().startswith(("ENCHANTER", "OUTFITTER", "BLACKSMITH"))
        and not lb.text().endswith("AUGMENTS")]
    assert len(badges) == 48
    assert all("LV" not in b.text() for b in badges)
    # each badge names the slot the augment applies to — the Outfitter
    # embroideries go on the Back, the Blacksmith plates on the Chest,
    # the Enchanter formulas on Feet (boots) / Hands / Weapon; the
    # +2/corrupted scrolls enchant any gear, so their badge names the
    # TEMP slot with the buff's timer as the gold accent
    texts = [b.text() for b in badges]
    assert texts.count("OUTFITTER · BACK") == 7
    assert texts.count("BLACKSMITH · CHEST") == 7
    assert texts.count("ENCHANTER · FEET") == 10
    assert texts.count("ENCHANTER · HANDS") == 10
    assert texts.count("ENCHANTER · WEAPON") == 5
    # the TEMP badges carry the embedded clock + gold timer as rich text,
    # so they never equal the bare string — match the segments (a
    # duplicated 'ENCHANTER · TEMP · 30M · 30M' would fail below)
    temp_txts = [t for t in texts
                 if t.startswith("ENCHANTER") and "TEMP" in t]
    assert len(temp_txts) == 9
    assert all(t.count("30M") == 1 for t in temp_txts)
    # each badge wears its SLOT's color, matching the slot chips —
    # the three Enchanter slots each their own (FEET orange, HANDS
    # green, WEAPON gold), BACK epic-purple, CHEST rare-blue; the
    # TEMP buffs get the soft red
    def _bcolor(prefix: str) -> str:
        return next(lb for lb in badges
                    if lb.text().startswith(prefix)).styleSheet()
    assert theme.ORANGE in _bcolor("ENCHANTER · FEET")
    assert theme.GOOD in _bcolor("ENCHANTER · HANDS")
    assert theme.GOLD in _bcolor("ENCHANTER · WEAPON")
    temp_b = next(lb for lb in badges
                  if lb.text().startswith("ENCHANTER") and "TEMP" in lb.text())
    assert theme.DANGER in temp_b.styleSheet()
    assert theme.GOLD in temp_b.text()      # the timer segment is gold
    # the timer clock is embedded inside the pill, right next to the TEMP
    # text — the <img> tag precedes the TEMP segment in the rich text
    assert "<img" in temp_b.text(), temp_b.text()
    assert temp_b.text().index("<img") < temp_b.text().index("TEMP")
    assert theme.rarity_color("Epic") in _bcolor("OUTFITTER · BACK")
    assert theme.rarity_color("Rare") in _bcolor("BLACKSMITH · CHEST")
    # a row button opens the recipe on the Craft page
    calls = []
    p._items_open_in_craft = lambda iid: calls.append(iid)
    first = _grid(p._ench_augments_lay).itemAtPosition(0, 0).widget()
    btn = next(b for b in first.findChildren(QtWidgets.QPushButton)
               if b.text() and b.text() not in ("▸", "▾"))
    btn.click()
    assert calls, "augment row button did not open the recipe"
    # each row's ▸ sub-menu expands its recipe's materials (a corrupted
    # scroll row: Blank Page ×1, Bright Powder ×2, Demonic Horn ×3)
    sc_grid = _grid(p._ench_augments_lay)
    sc_host = next(sc_grid.itemAtPosition(r, 0).widget()
                   for r in range(sc_grid.rowCount())
                   if any("Corrupted Strength" in b.text()
                          for b in sc_grid.itemAtPosition(r, 0).widget()
                          .findChildren(QtWidgets.QPushButton)))
    chev = next(b for b in sc_host.findChildren(QtWidgets.QPushButton)
                if b.text() == "▸")
    detail = next(lb for lb in sc_host.findChildren(QtWidgets.QLabel)
                  if "Demonic Horn" in lb.text())
    assert not detail.isVisible()
    chev.click()
    assert detail.isVisible() and chev.text() == "▾"
    assert "Blank Page ×1" in detail.text()
    # the search narrows by job (each family by name), a material
    p._ench_search.setText("enchanter")
    assert _grid_rows(p._ench_augments_lay) == 34
    p._ench_search.setText("outfitter")
    assert _grid_rows(p._ench_augments_lay) == 7
    p._ench_search.setText("blacksmith")
    assert _grid_rows(p._ench_augments_lay) == 7
    p._ench_search.setText("paper")
    assert _grid_rows(p._ench_augments_lay) == 25   # all formulas
    p._ench_search.setText("scroll")
    assert _grid_rows(p._ench_augments_lay) == 9    # the nine scrolls
    p._ench_search.clear()
    # a stat filter narrows the card in place — no jump off
    stats = _filter_stats(p)
    p._ench_stat.setCurrentIndex(stats.index("Strength") + 1)
    assert p._ench_augments_card.isVisible()         # stays put
    assert p._ench_tabs.currentText() == "Augments"
    assert 0 < _grid_rows(p._ench_augments_lay) < 48   # Strength-granting only
    p._ench_stat.setCurrentIndex(0)   # back to All stats
    assert _grid_rows(p._ench_augments_lay) == 48


def test_augments_slot_chips_and_shared_stat_filter():
    """The augments card carries its Slot chip row above the sortable
    table — the slot each augment applies to (BACK / CHEST / FEET /
    HANDS / WEAPON, colored by its owning job, plus TEMP for the
    temp-buff scrolls) — one click shows only the rows for that slot,
    so the long merged list narrows without scrolling. The stat
    dimension is shared: the page-wide Stat chips (the single stat
    filter across the Enchants page) narrow this table too — only rows
    granting the stat remain, including the corrupted scrolls' +N / −N
    trades, which carry no own_stats. The two compose; All restores."""
    p = _page()
    p._items_set_mode("Enchants")
    p._ench_tabs._btns["Augments"].click()

    def _badges():
        grid = _grid(p._ench_augments_lay)
        return [grid.itemAtPosition(r, 1).widget().text()
                for r in range(grid.rowCount())]

    all_n = len(_row_names(p._ench_augments_lay))
    assert all_n > 10
    # each slot chip wears its own color — BACK purple, CHEST blue,
    # and the three Enchanter slots each their own (FEET orange, HANDS
    # green, WEAPON gold) — so the filter row reads every option apart
    def _chip_style(text: str) -> str:
        return next(b.styleSheet() for b in
                    p._ench_aug_slot.findChildren(QtWidgets.QPushButton)
                    if b.text() == text)
    assert theme.with_alpha(theme.rarity_color("Epic"), 200) \
        in _chip_style("BACK")
    assert theme.with_alpha(theme.rarity_color("Rare"), 200) \
        in _chip_style("CHEST")
    assert theme.with_alpha(theme.ORANGE, 200) in _chip_style("FEET")
    assert theme.with_alpha(theme.GOOD, 200) in _chip_style("HANDS")
    assert theme.with_alpha(theme.GOLD, 200) in _chip_style("WEAPON")
    assert theme.with_alpha(theme.DANGER, 200) in _chip_style("TEMP")
    assert theme.ACCENT in _chip_style("All slots")  # the any chip stays neutral
    # the card's slot chips narrow to one slot — every badge matches
    p._ench_aug_slot.setCurrentIndex(p._ench_aug_slot.findData("BACK"))
    badges = _badges()
    assert badges and all(b == "OUTFITTER · BACK" for b in badges), badges
    assert 0 < len(badges) < all_n
    p._ench_aug_slot.setCurrentIndex(p._ench_aug_slot.findData("CHEST"))
    badges = _badges()
    assert badges and all(b == "BLACKSMITH · CHEST" for b in badges), badges
    # the TEMP chip narrows to the temp-buff scrolls (their badges carry
    # the embedded clock + gold timer as rich text, so match the segments)
    p._ench_aug_slot.setCurrentIndex(p._ench_aug_slot.findData("TEMP"))
    badges = _badges()
    assert len(badges) == 9, badges
    assert all(b.startswith("ENCHANTER") and "TEMP" in b
               for b in badges), badges
    # All slots restores the full table
    p._ench_aug_slot.setCurrentIndex(0)
    assert len(_row_names(p._ench_augments_lay)) == all_n
    # the SHARED page-wide Stat chips narrow the augments rows to those
    # granting the stat — the corrupted Strength scroll matches via its
    # +4 trade, the plain Strength scroll via its +2 own stat
    p._ench_stat.setCurrentIndex(
        _filter_stats(p).index("Strength") + 1)
    names = _row_names(p._ench_augments_lay)
    assert names, "no augments grant Strength"
    by_name = {it["name"]: it for it in (p._ench_enchanter
                                          + p._ench_outfitter
                                          + p._ench_blacksmith)}
    for n in names:
        it = by_name[n]
        grants = any(s["n"] == "Strength"
                     for s in (idata.own_stats(it) or []))
        c = p._ench_corrupt_by_item.get(it["id"])
        corrupt = c is not None and "Strength" in (c["stat"], c["penalty"])
        assert grants or corrupt, n
    # composing: slot + shared stat narrow within the slot (the two
    # HANDS Strength formulas remain)
    p._ench_aug_slot.setCurrentIndex(p._ench_aug_slot.findData("HANDS"))
    joined = _row_names(p._ench_augments_lay)
    assert 0 < len(joined) <= len(names)
    assert all(b == "ENCHANTER · HANDS" for b in _badges())
    # clearing both restores the full table
    p._ench_stat.setCurrentIndex(0)
    p._ench_aug_slot.setCurrentIndex(0)
    assert len(_row_names(p._ench_augments_lay)) == all_n


def test_craft_lists_order_highest_level_first():
    """The crafted lists read highest craftable level first (the gem
    table's top-down convention) and lock the sort keys against
    regressions: elixirs and food by level, the merged augments card by
    job group then level, the scrolls table by level (corrupted LV 6
    lead the plain LV 1) — alphabetical names within a level."""
    p = _page()
    p._items_set_mode("Enchants")
    # elixirs + food: level descending, names alphabetical within a level
    for items, lay in ((p._ench_elixirs, p._ench_elixirs_lay),
                       (p._ench_foods, p._ench_foods_lay)):
        lv = {it["name"]: (idata.recipe(it["id"]) or {}).get("level") or 0
              for it in items}
        names = _table_names(lay)   # the cards are one table each
        assert len(names) == len(items)
        levels = [lv[n] for n in names]
        assert levels == sorted(levels, reverse=True), names
        for lev in sorted(set(levels), reverse=True):
            grp = [n for n, l in zip(names, levels) if l == lev]
            assert grp == sorted(grp, key=str.lower), (lev, grp)
    # augments: job groups in badge order, level descending within each
    info = {}
    for it in p._ench_enchanter + p._ench_outfitter + p._ench_blacksmith:
        r = idata.recipe(it["id"]) or {}
        info[it["name"]] = (r.get("job_name") or "", r.get("level") or 0)
    rank = {"Enchanter": 0, "Outfitter": 1, "Blacksmith": 2}
    names = _row_names(p._ench_augments_lay)
    jobs = [info[n][0] for n in names]
    levels = [info[n][1] for n in names]
    assert [rank[j] for j in jobs] == sorted(rank[j] for j in jobs), names
    for job in ("Enchanter", "Outfitter", "Blacksmith"):
        grp = [l for n, j, l in zip(names, jobs, levels) if j == job]
        assert grp == sorted(grp, reverse=True), (job, grp)
    # scrolls: corrupted LV 6 lead the plain LV 1, alphabetical within
    lvl = {}
    for s in p._ench_scrolls:
        lvl[f"Scroll of {s}"] = (
            (idata.recipe(p._ench_scroll_items[s]) or {}).get("level") or 0)
    for c in p._ench_corrupt:
        lvl[f"Scroll of Corrupted {c['stat']}"] = (
            (idata.recipe(c["item"]) or {}).get("level") or 0)
    grid = _grid(p._ench_scrolls_lay)
    names = [_row_name(grid, r) for r in range(grid.rowCount())]
    levels = [lvl[n] for n in names]
    assert levels == sorted(levels, reverse=True), names
    assert names == sorted(names, key=str.lower), names


def _infusion_chips(p) -> dict:
    """The faction chips by faction name."""
    return p._ench_infusion_chips


def _row_text(p) -> str:
    """Every label in the card's row area, joined — row assertions read the
    tier prose through this (a tier's tag and body are separate labels)."""
    return "\n".join(lb.text() for lb in p._ench_infusion_rows.findChildren(
        QtWidgets.QLabel) if lb.text())


def _infusion_pills(p) -> list:
    """The pattern names, in row order (the card's MORE / LESS link is
    a button in there too, so it is filtered out)."""
    return [b.text() for b in p._ench_infusion_rows.findChildren(
        QtWidgets.QPushButton)
        if b.text() and not b.text().startswith(("MORE", "LESS"))]


def test_infusions_tab_lists_heroic_patterns():
    """The Infusions tab is the Infusion System's card: ONE chip row — the
    factions, one at a time, no all-factions state (the role chips were
    removed: the faction pick is mandatory, so a role could only narrow that
    one faction's three rows) — then that faction's patterns as full-width
    rows: the pattern named in its role color, the granted skill, and
    the (2)/(4)/(6) tiers the in-game InfusionUI skill panel shows. The card
    also has to stay narrow: its minimum width is what used to make the tab
    scroll sideways."""
    p = _page()
    p._items_set_mode("Enchants")
    p._ench_tabs._btns["Infusions"].click()
    assert p._ench_infusions_card.isVisible()
    pool = p._ench_infusions
    # Nightling is the 2026 patch's Demon faction: the pool derives straight
    # from the item sheet, so it appears on its own (no code change needed)
    assert {(x["faction"], x["role"]) for x in pool} == {
        (f, r) for f in ("Bee", "Kobold", "Manfish", "Crimson", "Nightling")
        for r in ("DPS", "Tank", "Support")}
    # wiring rides the pool, so the next faction can't break this test
    assert len(pool) == len({x["item"] for x in pool})
    # the faction chips, in the Dungeons tab's order (heroic_infusions()
    # sorts alphabetically, so this pins the DISPLAY order), exactly one lit
    chips = _infusion_chips(p)
    assert list(chips) == ["Bee", "Kobold", "Manfish", "Crimson",
                           "Nightling"], list(chips)
    assert [c.isChecked() for c in chips.values()].count(True) == 1
    assert not any("filter" in c.text().lower() for c in chips.values())
    # there is no role chooser any more: the roles survive as the row ORDER
    # (DPS → Tank → Support) and as the pill colors, not as a filter
    assert not hasattr(p, "_ench_infusion_role")
    # ...and no SectionHeader either: the tab strip reading "Infusions" was
    # already the section name, so the card opens straight on the chips
    # (SectionHeader is a container, so this counts its own label children)
    assert not [lb for lb in p._ench_infusions_card.findChildren(QtWidgets.QLabel)
                if lb.text().strip().upper().startswith("INFUSION PATTERNS")]
    # it opens on Bee: that faction's three patterns as rows, each naming
    # itself on a role-colored pill (DPS → Tank → Support)
    assert p._ench_infusion_fac == "Bee"
    assert _infusion_pills(p) == ["BEE DPS", "BEE TANK", "BEE SUPPORT"]
    # each pattern is one card (the app's row treatment), not a striped table
    # line — one lit frame per row, in the card body
    assert len([w for w in p._ench_infusion_rows.findChildren(
        QtWidgets.QFrame, "Card") if w.property("item_id")]) == 3
    body = _row_text(p)
    for want in ("Hive Venom", "Resine Armor", "Nectar Balls",
                 "(2) Set :", "(4) Set :", "(6) Set :"):
        assert want in body, want
    # the heroic source rides each row's HEADER line (the boss whose *_LT2
    # table guarantees the drop, and that boss's dungeon). The full-width
    # rewrite dropped it and this test then asserted its absence; it is back
    # up on the title line rather than under the tiers — one short fact about
    # the whole pattern, beside the name that opens its Drops From card
    by_id = {pat["item"]: pat for pat in p._ench_infusions}
    for iid in ("InfusionPattern_Bee_DPS", "InfusionPattern_Bee_Tank",
                "InfusionPattern_Bee_Support"):
        card = next(c for c in p._ench_infusion_rows.findChildren(
            QtWidgets.QFrame, "Card") if c.property("item_id") == iid)
        pat = by_id[iid]
        want = f"{pat['unlock_boss'] or pat['boss']} — {pat['dungeon']}"
        hlay = card.layout().itemAt(0).widget().layout()
        src_lbl = hlay.itemAt(hlay.count() - 1).widget()   # the line's end
        assert isinstance(src_lbl, QtWidgets.QLabel), iid
        assert src_lbl.text() == want, (iid, src_lbl.text())
        assert theme.DIM in src_lbl.styleSheet(), src_lbl.styleSheet()
        # and it is on the title line, not down in the tier block
        cell = card.layout().itemAt(1).widget()
        assert src_lbl not in cell.findChildren(QtWidgets.QLabel), iid
    # the frames carry no tooltip: the source is text on the line, not
    # something hidden behind a hover
    assert not any(c.toolTip() for c in p._ench_infusion_rows.findChildren(
        QtWidgets.QFrame, "Card") if c.property("item_id"))
    # each tier prints ONCE per row: the (4) affix used to render both beside
    # the skill and as the ladder's (4), which read as a duplicate row
    for tag in ("(2) Set :", "(4) Set :", "(6) Set :"):
        assert body.count(tag) == 3, tag
    assert body.count("+2.5% Magic Mastery") == 1
    # every pill is a link into that pattern's item card (the crucible
    # recipe, whose guaranteed heroic boss rides Drops From)
    seen = []
    p._items_show_id = seen.append
    p._ench_infusion_rows.findChildren(QtWidgets.QPushButton)[0].click()
    assert seen == ["InfusionPattern_Bee_DPS"]
    # the chips are a radio: picking Kobold swaps the rows
    chips["Kobold"].click()
    assert p._ench_infusion_fac == "Kobold"
    assert _infusion_pills(p) == ["KOBOLD DPS", "KOBOLD TANK",
                                  "KOBOLD SUPPORT"]
    assert "Pestilential Aura" in _row_text(p)
    # exactly one chip is PAINTED as selected: the old sweep blocked the
    # chip's toggled signal, so the previously lit chip kept the selected
    # fill while the new one lit up — two looked chosen at once
    plain = C.FilterChip("x", checked=False, color=theme.GOLD).styleSheet()
    lit = C.FilterChip("x", checked=True, color=theme.GOLD).styleSheet()
    assert plain != lit                    # the assertion has to bite
    for fac, chip in chips.items():
        assert chip.styleSheet() == (lit if fac == "Kobold" else plain), fac
    chips["Kobold"].click()
    assert chips["Kobold"].isChecked() and p._ench_infusion_fac == "Kobold"
    # ...and the lit chip cannot be un-picked: those rows need a faction, so
    # the click is a no-op rather than a blank card
    assert all(c.styleSheet() == (lit if f == "Kobold" else plain)
               for f, c in chips.items())
    assert _infusion_pills(p) == ["KOBOLD DPS", "KOBOLD TANK",
                                 "KOBOLD SUPPORT"]
    # a search that empties the picked faction moves the pick on its own
    # rather than leaving an empty card under a hidden chip
    p._ench_search.setText("hive venom")
    assert p._ench_infusion_fac == "Bee"
    assert _infusion_pills(p) == ["BEE DPS"]
    p._ench_search.setText("")
    assert p._ench_infusion_fac == "Bee"
    # the rows stay narrow enough not to force the tab sideways: the card's
    # own minimum is well under a 960px viewport (it was 1097 with the
    # faction columns + side pane)
    assert p._ench_infusions_card.minimumSizeHint().width() < 900
    # Crimson's patterns have no scanned heroic table yet, and nothing on
    # the card claims otherwise: three ordinary rows, no source note
    chips["Crimson"].click()
    crimson = [c for c in p._ench_infusion_rows.findChildren(
        QtWidgets.QFrame, "Card") if c.property("item_id")]
    assert len(crimson) == 3
    body = _row_text(p)
    assert "Unlock:" not in body and "scan yet" not in body


def test_infusion_row_title_line_carries_the_name_and_the_skill():
    """The row is ONE block: the pattern's name sits inline at the head of
    the granted skill (both on the card's title line) and the (2)/(4)/(6)
    ladder runs full width under it. It used to be two columns — a fixed
    identity column beside the tier block — which spent ~150px per row on a
    name at most 120px wide and boxed the prose into a column narrower than
    the card.

    The names also share one width: inline names of different widths would
    start each card's skill at its own indent.
    """
    p = _page()
    p._items_set_mode("Enchants")
    p._ench_tabs._btns["Infusions"].click()
    # Manfish, whose three pills are the most different in length (DPS vs
    # SUPPORT), so a shared width is a real claim here and not a coincidence
    _infusion_chips(p)["Manfish"].click()
    cards = [c for c in p._ench_infusion_rows.findChildren(
        QtWidgets.QFrame, "Card") if c.property("item_id")]
    assert len(cards) == 3
    skills = {"MANFISH DPS": "Overwhelming Tides",
              "MANFISH TANK": "Life-giving Spring",
              "MANFISH SUPPORT": "Empowered Chain Heal"}
    pills = []
    for card in cards:
        title = card.layout().itemAt(0).widget()      # the title line
        btn = title.findChildren(QtWidgets.QPushButton)
        assert len(btn) == 1, [b.text() for b in btn]  # exactly the pill
        pills.append(btn[0])
        skill = skills[btn[0].text()]
        # the skill rides the SAME line, and the ladder does not: the tiers
        # are the card's second block, below the title
        title_text = " ".join(lb.text() for lb in title.findChildren(
            QtWidgets.QLabel) if lb.text())
        assert skill in title_text, (skill, title_text)
        # the ladder is the card's second block, not part of the title line
        assert "(2) Set :" not in title_text, title_text
    # one shared pill width, so every skill name starts at the same x
    widths = {b.width() for b in pills}
    assert len(widths) == 1, sorted(widths)
    # ...and the pill is still the link into that row's item card
    seen = []
    p._items_show_id = seen.append
    pills[1].click()
    assert seen == ["InfusionPattern_Manfish_Tank"]


def test_infusion_row_cards_are_leveled():
    """The row cards share one height. Each card sizes to its own content, so
    a (6) rank sentence that wrapped to a second line left its card a line
    taller than its neighbours and the column's bottom edge went ragged; the
    host pins every card to the tallest one's natural height, and re-levels
    rather than ratcheting (a card that stops being the tall one has to give
    the extra height back).

    The tall card is BUILT here instead of borrowed from Manfish: the ladder
    shrank so three rows clear the fold (see _TIER_PX), and at 13px even
    Manfish's (6) sentences fit on one line at every width the card takes —
    its rows measure identically, so the old precondition (natural heights
    differ) silently stopped biting. What is under test is the mechanism:
    one taller row, and the host levels its neighbours up to it."""
    p = _page()
    p._items_set_mode("Enchants")
    p._ench_tabs._btns["Infusions"].click()
    _infusion_chips(p)["Manfish"].click()
    app = QtWidgets.QApplication.instance()
    for _ in range(4):
        app.processEvents()

    host = p._ench_infusion_rows
    host.level()                    # deterministic: don't wait on the timer
    cards = host.cards()
    assert len(cards) == 3

    def natural():
        return [c.layout().heightForWidth(c.width()) + 2 * c.frameWidth()
                for c in cards]

    def settle():
        for _ in range(6):
            app.processEvents()
        host.layout().activate()

    # one row's (6) rank sentence runs long enough to wrap past the card's
    # width, which is what makes its card taller than the other two
    taller = cards[0]
    body = [lb for lb in reversed(taller.findChildren(QtWidgets.QLabel))
            if lb.wordWrap()][0]
    original = body.text()
    body.setText(original + " rank text" * 30)
    taller.layout().invalidate()
    settle()

    assert len(set(natural())) > 1, natural()   # the assertion has to bite
    host.level()
    settle()
    heights = [c.height() for c in cards]
    assert len(set(heights)) == 1, heights
    assert heights[0] >= max(natural()), (heights, natural())

    # ratchet-free: with the long sentence gone the level comes back down
    # instead of pinning the taller value it was given a moment ago
    body.setText(original)
    taller.layout().invalidate()
    settle()
    host.level()
    settle()
    relaxed = [c.height() for c in cards]
    assert len(set(relaxed)) == 1, relaxed
    assert relaxed[0] < heights[0], (relaxed, heights)
    assert relaxed[0] >= max(natural()), (relaxed, natural())


def test_infusion_row_names_are_plain_text_not_buttons(monkeypatch):
    """The row's link buttons (the pattern name and the MORE / LESS link)
    read as plain text — no chip, no box, no hand cursor — while still being
    the click that opens the pattern's item card.

    Both are QPushButtons, so two things had to go: the states (the app sheet
    owns `QPushButton:hover`'s PANEL_HI fill and `QPushButton:pressed`'s
    bright ACCENT_DIM one for every button, and Qt only takes a state back
    where the widget's own sheet NAMES it, so an unnamed state grew the name
    a fill and flashed a brighter box on click) and the cursor. The cursor
    was the last tell: probing the running app showed a hand over a line of
    words that has nothing to point at, so the name declares no fill, no
    border, no hover of its own — type in the role color is the whole
    rendering — and neither link sets a cursor.

    The MORE / LESS link only renders when a tier is CLIPPED to the two-line
    preview (`if clipped or expanded`), and at the real app font the infusion
    sentences FIT their two-line budget — so the link was font-metric
    dependent: it appeared only under the wide fallback font and vanished once
    test_party_inspect had called theme.apply() and permanently registered the
    real font. Pin the preview width so the trim is forced and the link is
    under test whatever the ambient font."""
    from farever_companion.ui.pages.items import enchants as inf
    monkeypatch.setattr(inf, "_TIER_PREVIEW_W", 40)   # force the two-line trim
    p = _page()
    p._items_set_mode("Enchants")
    p._ench_tabs._btns["Infusions"].click()
    btns = p._ench_infusion_rows.findChildren(QtWidgets.QPushButton)
    pill = next(b for b in btns if b.text() == "BEE DPS")
    more = next(b for b in btns if b.text().startswith(("MORE", "LESS")))
    for b in (pill, more):
        css = b.styleSheet()
        # ONE rule holds the resting look across the states the app sheet
        # would otherwise fill, so hover/press can only change the text
        assert ("QPushButton, QPushButton:hover, QPushButton:pressed"
                in css), css
        assert b.isFlat(), b.text()
        assert b.focusPolicy() == QtCore.Qt.NoFocus, b.text()
        # nothing on the row turns the pointer into a hand...
        assert b.cursor().shape() != QtCore.Qt.PointingHandCursor, b.text()
    # ...nor does any ancestor (the row host, the card, the scroll area): a
    # hand inherited from a container would look exactly the same
    for anc in (pill.parentWidget(), more.parentWidget()):
        while anc is not None:
            assert anc.cursor().shape() != QtCore.Qt.PointingHandCursor, anc
            anc = anc.parentWidget()
    # the name is TEXT in its own role color (DPS is the danger red) and
    # nothing else: no fill behind it, no border around it, and no state rule
    # of its own — everything it paints, it paints at rest
    css = pill.styleSheet()
    # ONE declaration block, and nothing after it: any rule that named a
    # state on its own would be a state the name repaints under
    assert css.count("{") == 1 and css.count("}") == 1, css
    assert f"color:{theme.DANGER}" in css, css
    assert "background:transparent" in css and "border:0" in css, css
    # ...while the MORE link is text too, and keeps the app's link hover
    # colour (its only cue, since it has no box either)
    assert f"QPushButton:hover{{color:{theme.ACCENT_LIGHT};}}" in \
        more.styleSheet(), more.styleSheet()


def test_infusion_tier_tags_are_one_uniform_color():
    """The (2)/(4)/(6) tags share one label color — a gold (4) tag made the
    label read as part of the affix — while the BODIES carry the ladder: the
    (4) body, the only tier carrying numbers, is the app's plain text color,
    the (2) prose is MUTED, and the (6) rank text — the set's pinnacle — is
    GOLD, matching the skill header's own gold accents."""
    p = _page()
    p._items_set_mode("Enchants")
    p._ench_tabs._btns["Infusions"].click()
    labels = [lb for lb in p._ench_infusion_rows.findChildren(
        QtWidgets.QLabel) if lb.text() in ("(2) Set :", "(4) Set :", "(6) Set :")]
    assert len(labels) == 9            # three tags x the three Bee rows
    styles = {lb.styleSheet() for lb in labels}
    assert len(styles) == 1, styles    # one color for all three tags
    tag_style = styles.pop()
    assert theme.DIM in tag_style and theme.GOLD not in tag_style
    # the TAGS are never gold (the skill header's own gold is inline HTML in
    # its rich text, so it never shows up in a stylesheet — and the (2)/(4)
    # bodies do not wear a hue either; only the (6) bodies do, possibly
    # elided to the two-line preview)
    rows = p._ench_infusion_rows.findChildren(QtWidgets.QLabel)
    plain = [lb.text() for lb in rows
             if not lb.text().startswith("<")
             and theme.GOLD in lb.styleSheet()]
    assert plain, "no (6) body picked up the gold"
    lower_tiers = {lb.text() for lb in rows
                   if not lb.text().startswith("<")
                   and (f"color:{theme.MUTED};" in lb.styleSheet()
                        or f"color:{theme.TEXT};" in lb.styleSheet())}
    assert not (set(plain) & lower_tiers), (plain, lower_tiers)
    # the (4) bodies wear the plain text color — the brightest line, the only
    # tier with numbers in it — while (2) is MUTED and (6) is GOLD
    bright = sorted(lb.text() for lb in p._ench_infusion_rows.findChildren(
        QtWidgets.QLabel) if not lb.text().startswith("<")
        and f"color:{theme.TEXT};" in lb.styleSheet())
    assert bright == ["+2.5% Armor", "+2.5% Critical Chance",
                      "+2.5% Magic Mastery"], bright


def test_infusion_text_is_reading_sized_on_both_surfaces():
    """The crucible's tiers print on TWO surfaces — the Infusions ladder and
    the item card's granted-skill box — and both carry reading text (whole
    sentences, not labels), so they are sized in step rather than as two
    unrelated magic numbers.

    The ladder's size is capped by the FOLD, not by taste, and this test used
    to demand >= 16px for readability: the ladder then pushed a three-row
    faction 187px past the default window's fold (1180x740 opens the tab with
    641px of room and needed 645), so the tab scrolled and its three pattern
    boxes read as mostly empty cards. Re-measured at that window: 15px still
    scrolls 16px, 14px and 13px land on 0 — hence the ceiling the module
    carries, which is what this pins instead of a reading-size floor. The
    item card has no fold to clear, so its box stays a notch above."""
    from farever_companion.ui.pages.items import detail as detail_mod
    from farever_companion.ui.pages.items import enchants as inf_mod

    assert inf_mod._TIER_PX <= inf_mod._TIER_FOLD_PX   # clears the fold
    assert abs(inf_mod._TIER_PX - detail_mod._INFUSION_PX) <= 1
    assert inf_mod._SKILL_PX > inf_mod._TIER_PX   # the title stays on top

    p = _page()
    p._items_set_mode("Enchants")
    p._ench_tabs._btns["Infusions"].click()
    body = next(lb for lb in p._ench_infusion_rows.findChildren(
        QtWidgets.QLabel) if lb.text().startswith("+2.5%"))
    assert f"font-size:{inf_mod._TIER_PX}px" in body.styleSheet(), \
        body.styleSheet()

    # the item card's own box, on the same pattern
    p._items_set_mode("Items")
    p._items_show_id("InfusionPattern_Bee_Support")
    box = next(lb for lb in p._items_detail.findChildren(QtWidgets.QLabel)
               if "GRANTED SKILL" in lb.text())
    assert f"font-size: {detail_mod._INFUSION_PX}px" in box.styleSheet(), \
        box.styleSheet()


def test_infusions_search_narrows_by_boss_faction_role():
    """The search reads the pattern rows too — boss, faction, role, and
    the granted skill (incl. its ultimate) — and the filtered total rides
    _ench_counts like every other card."""
    p = _page()
    p._items_set_mode("Enchants")
    p._ench_tabs._btns["Infusions"].click()
    n_all = len(p._ench_infusions)
    assert _counts(p)["INFUSIONS"] == n_all
    p._ench_search.setText("ratsar")
    assert _counts(p)["INFUSIONS"] == 1
    p._ench_search.setText("bee")
    assert _counts(p)["INFUSIONS"] == 3      # Bee rows only; Kobold is not 'bee'
    p._ench_search.setText("tank")
    assert _counts(p)["INFUSIONS"] == 5      # one Tank per faction
    # the granted skill is searchable: its name and its ultimate
    p._ench_search.setText("hive venom")
    assert _counts(p)["INFUSIONS"] == 1
    p._ench_search.setText("empowered")
    assert _counts(p)["INFUSIONS"] == 1
    p._ench_search.setText("")
    assert _counts(p)["INFUSIONS"] == n_all


def test_heroic_infusion_pool_maps_bosses_to_dungeons():
    """The data-layer pool: every pattern carries the heroic boss whose
    *_LT2 table guarantees it, that boss's dungeon, and the skill the
    infusion grants once applied (the Infusion_<Faction>_<Role> skill
    row; ultimate when the sheet names one)."""
    pool = {x["item"]: x for x in idata.heroic_infusions()}
    # 15 = the four launch factions x DPS/Tank/Support, plus the 2026 patch's
    # Nightling (Demon) trio — the pool derives from the item sheet, so the
    # count grows with the sheet by design
    assert len(pool) == 15
    assert {(x["faction"], x["role"]) for x in pool.values()} == {
        (f, r) for f in ("Bee", "Kobold", "Manfish", "Crimson", "Nightling")
        for r in ("DPS", "Tank", "Support")}
    assert pool["InfusionPattern_Bee_DPS"]["boss"] == "Gatsbee"
    assert pool["InfusionPattern_Kobold_Tank"]["boss"] == "Ratsar"
    assert pool["InfusionPattern_Manfish_Support"]["boss"] == "Nepsilon"
    assert pool["InfusionPattern_Bee_DPS"]["dungeon"] == "Bee Hive"
    # Crimson's heroic dungeons resolved with the refreshed scan, so the four
    # launch factions all map; Nightling's (the new Demon dungeon is not in
    # the scan yet) honestly carries '' — the designed fallback for an
    # unresolved boss (re-scan after the new patch data)
    unresolved = {"Nightling"}
    mapped = [x for x in pool.values() if x["faction"] not in unresolved]
    assert all(x["dungeon"] for x in mapped)
    assert all(x["boss"] == "" and x["dungeon"] == ""
               for x in pool.values() if x["faction"] in unresolved)
    # the granted skills — every pattern resolves one, unresolved rows
    # would come back '' (an id echoed as a name must not render)
    assert all(x["skill"] for x in pool.values())
    assert pool["InfusionPattern_Bee_DPS"]["skill"] == "Hive Venom"
    assert pool["InfusionPattern_Kobold_Tank"]["skill"] == "Pestilential Aura"
    assert pool["InfusionPattern_Manfish_Tank"]["skill"] == "Life-giving Spring"
    # ultimates only where the sheet names them
    assert pool["InfusionPattern_Bee_Support"]["ultimate"] == "Honey Shot"
    assert pool["InfusionPattern_Manfish_Support"]["ultimate"] == \
        "Empowered Chain Heal"
    assert pool["InfusionPattern_Bee_DPS"]["ultimate"] == ""


def test_heroic_infusion_pool_carries_the_crucible_set_data():
    """The crucible's own data (from the in-game InfusionUI dump): the
    unlock text names the boss by its DISPLAY name ('Defeat Heroic Lady
    Bee' — Mokshi's dungeon persona, not the unit id), and each pattern's
    set tiers derive from the granted skill's sheet rows: (2) the base
    description, (4) the threshold-gated stat affixes, (6) the rank
    description — the same three texts the crucible's skill panel shows."""
    pool = {x["item"]: x for x in idata.heroic_infusions()}
    # display-name unlock bosses (Bee faction: the dungeons' boss_name,
    # which differ from the unit ids the scan records)
    assert pool["InfusionPattern_Bee_Support"]["unlock_boss"] == "Lady Bee"
    assert pool["InfusionPattern_Bee_Tank"]["unlock_boss"] == \
        "Queen Honeyzabeth"
    assert pool["InfusionPattern_Kobold_DPS"]["unlock_boss"] == \
        "Munster Chuck"
    assert pool["InfusionPattern_Kobold_Tank"]["unlock_boss"] == \
        "King Ratsar"
    assert pool["InfusionPattern_Manfish_DPS"]["unlock_boss"] == \
        "Sponge Blob"
    assert pool["InfusionPattern_Manfish_Tank"]["unlock_boss"] == \
        "Crabgantua"
    # set tiers: (2) base desc, (4) stat affixes, (6) rank desc — all 9
    assert all(x["set2"] for x in pool.values())
    assert all(x["set6"] for x in pool.values())
    assert pool["InfusionPattern_Kobold_DPS"]["set4"] == \
        ["+2.5% Critical Chance"]
    assert pool["InfusionPattern_Kobold_Tank"]["set4"] == \
        ["+2.5% Armor", "+2.5% Vitality"]
    assert pool["InfusionPattern_Manfish_DPS"]["set4"] == \
        ["+2.5% Armor Penetration", "+2.5% Spell Penetration"]
    assert pool["InfusionPattern_Bee_DPS"]["set4"] == \
        ["+2.5% Magic Mastery"]
