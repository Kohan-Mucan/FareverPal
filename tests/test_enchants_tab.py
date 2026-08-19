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
    assert p._items_body.currentIndex() == 0
    # the body stack holds browse + the enchant page + the Farm tab
    assert p._items_body.count() == 3


def test_enchants_mode_swaps_body_and_renders_database():
    """Switching to Enchants shows the enchant page: the data sources
    loaded, the count tag updated and the stat chips built from every stat
    any scroll / gem / conversion / crafting consumable touches."""
    p = _page()
    p._items_set_mode("Enchants")
    assert p._items_mode == "enchants"
    assert p._items_body.currentIndex() == 1

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


def _grid(lay) -> QtWidgets.QGridLayout:
    """The card body's ONE table — the first QGridLayout child (every
    card body holds a single table, so this is the whole grid)."""
    return next(it.layout() for i in range(lay.count())
                if (it := lay.itemAt(i)).layout() is not None
                and isinstance(it.layout(), QtWidgets.QGridLayout))


def _grid_rows(lay) -> int:
    """Row count in a card body that's ONE table (the augments card has no
    column-header row, and neither do elixirs / food / the scrolls table)
    — the first QGridLayout's row count."""
    for i in range(lay.count()):
        it = lay.itemAt(i)
        if (it.layout() is not None
                and isinstance(it.layout(), QtWidgets.QGridLayout)):
            return it.layout().rowCount()
    return 0


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
                                "Elixirs", "Food", "Scrolls"]
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
                      ("Elixirs", p._ench_elixirs_card),
                      ("Food", p._ench_foods_card),
                      ("Augments", p._ench_augments_card)):
        tabs._btns[key].click()
        assert card.isVisible(), key
        assert not p._ench_scrolls_card.isVisible(), key
        assert sum(c.isVisible() for c in (p._ench_scrolls_card,
                                           p._ench_gems_card,
                                           p._ench_convs_card,
                                           p._ench_elixirs_card,
                                           p._ench_foods_card,
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


def test_refilter_keeps_card_headers_alive():
    """Re-filtering clears only the card row bodies, never the
    SectionHeader — after deferred deletes flush (sendPostedEvents, the
    way the main event loop does), a refilter and a direct set_tag still
    work. The old code cleared the whole card layout, so the header's tag
    label was deleteLater'd and died on the next refilter (RuntimeError:
    C++ object already deleted)."""
    p = _page()
    p._items_set_mode("Enchants")
    p._ench_search.setText("vitality")
    QtWidgets.QApplication.sendPostedEvents(
        None, QtCore.QEvent.DeferredDelete)
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
    assert p._items_body.currentIndex() == 1
    # a normal gear output still opens on the Gear tab, selected
    p._craft_open_in_items("Axe_Boomerang")
    assert p._items_mode == "gear"
    assert p._items_body.currentIndex() == 0
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
    assert p._items_body.currentIndex() == 1
    p._items_set_mode("Gear")
    assert p._items_mode == "gear"
    assert p._items_body.currentIndex() == 0


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
