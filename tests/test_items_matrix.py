"""Items subsystem: matrix layout, tile hit-testing, and chip filters."""
import os
import re

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402

from farever_companion import paths  # noqa: E402
from farever_companion.data import items as idata  # noqa: E402
from farever_companion.ui import theme  # noqa: E402
from farever_companion.ui import components as C  # noqa: E402
from farever_companion.ui.pages.items.upgrades_matrix import build_upgrades_matrix  # noqa: E402


def _data_present() -> bool:
    try:
        return paths.item_drops_path().exists()
    except Exception:
        return False


_SECTION_MARK = pytest.mark.skipif(
    not _data_present(),
    reason="requires bundled data (assets/data/item_drops.json)")


@pytest.fixture(scope="module", autouse=True)
def _qapp():
    """One offscreen QApplication for the whole module (widgets need one)."""
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


# --- helpers ---------------------------------------------------------------


def _grid(item_id: str, level: int = 25):
    it = idata.item(item_id)
    ladder = idata.upgrade_ladder(it, level=level)
    assert ladder, item_id
    return build_upgrades_matrix(ladder)


def _rows(grid):
    """[(row, col, colspan, widget)] for every widget in the grid."""
    out = []
    for i in range(grid.count()):
        w = grid.itemAt(i).widget()
        if w is None:
            continue
        row, col, _rspan, cspan = grid.getItemPosition(i)
        out.append((row, col, cspan, w))
    return out


def _labels(grid, row):
    """Sorted [(col, text)] of the text labels on a given row."""
    return sorted((c, w.text()) for (r, c, _, w) in _rows(grid)
                  if r == row and hasattr(w, "text") and w.text())


def _cell(grid, row, col):
    for (r, c, _, w) in _rows(grid):
        if r == row and c == col and hasattr(w, "text"):
            return w
    return None


def _tinted_cols(grid, row):
    """Columns of the tinted (filled) cells on a data row."""
    return sorted(c for (r, c, _, w) in _rows(grid)
                  if r == row and hasattr(w, "text")
                  and "background:rgba" in w.styleSheet())


def _color_rgb(css: str):
    m = re.search(r"color:(#[0-9a-f]{6})", css)
    return (int(m.group(1)[1:3], 16), int(m.group(1)[3:5], 16),
            int(m.group(1)[5:7], 16)) if m else None


def _fill_rgb(css: str):
    m = re.search(r"rgba\((\d+),(\d+),(\d+),[\d.]+\)", css)
    return tuple(int(x) for x in m.groups()) if m else None


def _bar_rows(grid):
    """[(row, col, colspan, widget)] for the underline bar rows.

    A normal rarity renders as a container (nub + bar + nub); a degenerate
    single-column rarity as one centered 8x8 nub frame.
    """
    out = []
    for (r, c, cs, w) in _rows(grid):
        if isinstance(w, QtWidgets.QLabel):
            continue
        lay = w.layout()
        if lay is not None and lay.count() == 3:
            out.append((r, c, cs, w))
        elif ("border-radius:4px" in w.styleSheet()
              and w.minimumWidth() == 8 and w.maximumWidth() == 8):
            out.append((r, c, cs, w))
    return out


def _assert_base_only_matrix(grid, ladder):
    """The show_steps=False contract: the STATS + one-rarity headers, only a
    base subheader (no max), exactly one base value per stat on the tinted
    column 1, and a degenerate centered nub instead of a bar."""
    assert _labels(grid, 0) == [(0, "STATS"), (1, "RARE")]
    assert _labels(grid, 1) == [(1, "base")]
    for row in range(2, 2 + len(_ladder_order(ladder))):
        cells = [(c, w.text()) for (r, c, _, w) in _rows(grid)
                 if r == row and c > 0 and hasattr(w, "text")]
        # exactly one cell: the base value, tinted
        assert cells == [(1, _cell(grid, row, 1).text())], (row, cells)
        assert _tinted_cols(grid, row) == [1], row
    # the degenerate single-column range renders a centered nub, no bar
    bars = _bar_rows(grid)
    assert [(r, c, cs) for (r, c, cs, _) in bars] == [
        (len(_ladder_order(ladder)) + 2, 1, 1)]


# --- ladder oracle ----------------------------------------------------------
# Derives the matrix's column contract straight from the ladder data (never
# from build_upgrades_matrix), so the rendered grid can be checked against an
# independent expectation across the whole catalog.


def _ladder_order(ladder):
    order = []
    for r in ladder:
        for b in r["base"]:
            if b["label"] not in order:
                order.append(b["label"])
    return order


def _lane_runs(ladder, label):
    """[(rarity, [cumulative values])] for one stat — the value run the
    matrix derives from each rarity's base plus its step gains."""
    runs = []
    for r in ladder:
        base = {b["label"]: b["value"] for b in r["base"]}
        run = base.get(label, 0)
        vals = [run]
        for s in r["steps"]:
            g = next((g for g in s["gains"] if g["label"] == label), None)
            run += g["value"] if g else 0
            vals.append(run)
        runs.append((r["rarity"], vals))
    return runs


def _rarity_cols(ladder):
    """{rarity: (col_start, width)}: sequential blocks from column 1, each
    as wide as the most NEW values any stat contributes in that rarity."""
    widths = {r["rarity"]: 0 for r in ladder}
    for label in _ladder_order(ladder):
        seen = set()
        for rarity, vals in _lane_runs(ladder, label):
            n = 0
            for v in vals:
                if v not in seen:
                    seen.add(v)
                    n += 1
            widths[rarity] = max(widths[rarity], n)
    cols = {}
    cur = 1
    for r in ladder:
        w = max(widths[r["rarity"]], 1)
        cols[r["rarity"]] = (cur, w)
        cur += w
    return cols


def _expected_base_cols(ladder, label, cols):
    """Columns whose displayed value is some rarity's base for `label` —
    the deduped value order across the rarity blocks. This is the contract
    the tinted cells must satisfy: tint iff the cell's value is a base."""
    value_col = {}
    bases = set()
    for rarity, vals in _lane_runs(ladder, label):
        col_start = cols[rarity][0]
        k = 0
        for v in vals:
            if v in value_col:
                continue
            value_col[v] = col_start + k
            k += 1
        bases.add(value_col[vals[0]])
    return sorted(bases)


# --- fixtures: expected geometry for the catalog items ----------------------
#
# Mace_Benediction (Rare mace, lvl 25): three rarities, overlapping ladders.
#   Rare cols 1-4, Epic cols 5-7, Legendary cols 8-10
#   Faith ladder:   Rare [24 26 28 30] | Epic new [32 36 38] | Leg new [40 44 48]
#   Epic's base 28 is shown inside Rare's block (col 3); Legendary's base 32
#   inside Epic's block (col 5).
# Book_Start (starter book sold as Rare): a full Rare/Epic/Legendary matrix
#   like Mace_Benediction — the vendor sells the starter weapons scaled.
# Chest_RDemon_Ass (Rare armor): one rarity, four stats; Armor's four values
#   drive the block width.
# Shoulders_RManfish_FigAss (Rare armor): one rarity, four stats.


@_SECTION_MARK
def test_subheaders_on_true_base_and_max_columns():
    grid = _grid("Mace_Benediction")
    # the refreshed stat sheet widened every rarity's climb, so the
    # columns no longer overlap: Epic's base clears Rare's max and
    # Legendary's clears Epic's (the old 24/28/32 sheet shared columns)
    assert _labels(grid, 1) == [
        (1, "base"), (4, "max"),                   # Rare base + its 3 upgrades
        (5, "base"), (7, "base"), (9, "max"),      # Epic base, Leg base, Epic max
        (12, "max"),                               # Legendary max
    ]
    # Guild Merchant gear expands from its shop quality: Book_Start (a
    # starter weapon sold as Rare) shows the same full Rare/Epic/Legendary
    # matrix as Mace_Benediction — with the refreshed sheet its columns no
    # longer share either (same shape as the mace above)
    assert _labels(_grid("Book_Start"), 1) == [
        (1, "base"), (4, "max"),
        (5, "base"), (7, "base"), (9, "max"),
        (12, "max"),
    ]


@_SECTION_MARK
def test_subheaders_dont_stack_on_shared_columns():
    # Book_WaterOrbs L1: Epic's base shares its column with Legendary's base
    # (Faith 3 lands on col 2 for both) — the subheader row collapses to one
    # label per column instead of stacking two in one cell.
    grid = _grid("Book_WaterOrbs", level=1)
    assert _labels(grid, 1) == [
        (1, "base"), (2, "base"), (6, "max"), (8, "max"),
    ]


@_SECTION_MARK
def test_uncommon_ladder_is_base_only():
    # Uncommon cannot upgrade live (cap 0): its ladder is a base-only
    # single column — no max label, a degenerate nub instead of a bar.
    ladder = idata.upgrade_ladder(idata.item("Chest_Z1U1_Fig"), level=25)
    assert [r["rarity"] for r in ladder] == ["Uncommon"]
    assert ladder[0]["upgrades"] == 0
    assert ladder[0]["steps"] == []
    grid = build_upgrades_matrix(ladder)
    assert _labels(grid, 0) == [(0, "STATS"), (1, "UNCOMMON")]
    assert _labels(grid, 1) == [(1, "base")]
    order = _ladder_order(ladder)
    for row in range(2, 2 + len(order)):
        assert _tinted_cols(grid, row) == [1], row
    assert [(r, c, cs) for (r, c, cs, _) in _bar_rows(grid)] == [
        (2 + len(order), 1, 1)]


@_SECTION_MARK
def test_tint_only_on_true_base_columns():
    grid = _grid("Mace_Benediction")
    ladder = idata.upgrade_ladder(idata.item("Mace_Benediction"), level=25)
    cols = _rarity_cols(ladder)
    order = _ladder_order(ladder)
    # every stat row tints exactly its true base columns (the oracle derives
    # them from the ladder data, independent of the matrix builder)
    for r_idx, label in enumerate(order):
        assert _tinted_cols(grid, 2 + r_idx) == \
            _expected_base_cols(ladder, label, cols), label
    # tinted cells render in the base rarity's color for text AND fill
    for row in range(2, 2 + len(order)):
        for col in _tinted_cols(grid, row):
            cell = _cell(grid, row, col)
            assert _color_rgb(cell.styleSheet()) == \
                _fill_rgb(cell.styleSheet()), (row, col)
    # max / step cells keep their rarity's text with no fill
    tinted = {(r, c) for r in range(2, 2 + len(order))
              for c in _tinted_cols(grid, r)}
    for (r, c, _, w) in _rows(grid):
        if r >= 2 and hasattr(w, "text") and w.text() and (r, c) not in tinted:
            assert "background:rgba" not in w.styleSheet(), (r, c)
            assert _color_rgb(w.styleSheet()) is not None, (r, c)


@_SECTION_MARK
def test_tint_not_shifted_by_repeated_values():
    # A stat that gains nothing on a step repeats its value inside its run.
    # Repeats must not consume a column, or every shared base after them
    # shifts right — before the fix the shared bases tinted a non-base
    # cell and the true base column lost its tint. Pinned at L1 (the
    # repeat-heavy case) against the ladder oracle.
    ladder = idata.upgrade_ladder(idata.item("Mace_Benediction"), level=1)
    grid = build_upgrades_matrix(ladder)
    cols = _rarity_cols(ladder)
    for r_idx, label in enumerate(_ladder_order(ladder)):
        assert _tinted_cols(grid, 2 + r_idx) == \
            _expected_base_cols(ladder, label, cols), label


@_SECTION_MARK
def test_value_cells_follow_the_two_display_modes():
    """Every value cell renders exactly what stat_text() gives at the current
    mode — the clean tooltip integer in 'rounding' (the default), the exact
    computed float from gear_stats in 'true' — so a cell can never disagree
    with the farm row showing the same stat. The retired third form ('both',
    '230 (230.07)') left the header toggle with the cycle, and the matrix
    must not paint it back as parens."""
    it = idata.item("Chest_RDemon_Ass")
    ladder = idata.upgrade_ladder(it, level=25)
    armor = next(b for b in ladder[0]["base"] if b["label"] == "Armor")
    assert abs(armor["raw"] - armor["value"]) >= 0.0005   # a true fraction
    raw_txt = f"{armor['raw']:.3f}".rstrip("0").rstrip(".")
    for mode, needle in (("rounding", str(armor["value"])),
                         ("true", raw_txt)):
        idata.set_stat_display_mode(mode)
        try:
            grid = build_upgrades_matrix(ladder)
            texts = {w.text() for (r, c, _, w) in _rows(grid)
                     if r >= 2 and hasattr(w, "text") and w.text()}
            assert texts                              # the matrix rendered
            assert needle in texts, (mode, needle)
            # every base cell carries its own stat_text() (the raw from
            # gear_stats in 'true', the rounded integer in 'rounding')
            for b in ladder[0]["base"]:
                assert idata.stat_text(b["value"], b.get("raw")) in texts, b
            assert not any("(" in t for t in texts), (mode, texts)
        finally:
            idata.set_stat_display_mode("rounding")


@_SECTION_MARK
def test_no_cell_hover_tooltips():
    grid = _grid("Mace_Benediction")
    for (r, c, _, w) in _rows(grid):
        if r >= 2 and hasattr(w, "toolTip"):
            assert w.toolTip() == "", (r, c)


@_SECTION_MARK
def test_tint_on_true_base_columns_catalog_sweep():
    """Full-catalog sweep: every gear item tints exactly the columns whose
    value is a rarity's base, and nothing else. Catches the dedup regression
    where a stat that gains nothing on a step shifted a shared base's tint
    onto a non-base column.

    Multi-rarity ladders (shared bases across rarity blocks) render at every
    level — the case the regression hit. Single-rarity ladders have no shared
    base, so the tint can only ever sit on their one base column; they render
    once at the item's top level. If a future update ships per-rarity stats
    for armor/jewelry, those become multi-rarity and are swept automatically."""
    max_level = idata.gear_scaling().get("max_level") or 25
    checked = 0
    for it in idata.items():
        if not idata.is_gear(it):
            continue
        iid = it.get("id") or ""
        ladder = idata.upgrade_ladder(it, level=max_level)
        if not ladder:
            continue
        multi = len(ladder) >= 2
        levels = range(1, max_level + 1) if multi else (max_level,)
        for level in levels:
            if level != max_level:
                ladder = idata.upgrade_ladder(it, level=level)
                if not ladder:
                    continue
            order = _ladder_order(ladder)
            cols = _rarity_cols(ladder)
            grid = build_upgrades_matrix(ladder)
            for r_idx, label in enumerate(order):
                expected = _expected_base_cols(ladder, label, cols)
                assert _tinted_cols(grid, 2 + r_idx) == expected, \
                    (iid, level, label)
            # the subheader row never stacks two labels in one column
            hdr = _labels(grid, 1)
            hdr_cols = [c for c, _ in hdr]
            assert len(hdr_cols) == len(set(hdr_cols)), (iid, level, hdr)
            checked += 1
    assert checked > 1000     # sanity: the catalog sweep isn't silently empty


@_SECTION_MARK
def test_bars_span_true_base_to_max_with_nubs():
    grid = _grid("Mace_Benediction")
    bars = _bar_rows(grid)
    assert [(r, c, cs) for (r, c, cs, _) in bars] == [
            (6, 1, 4),     # Rare (base + its 3 upgrades)
            (7, 5, 5),     # Epic — clears Rare's max with the refreshed stats
            (8, 7, 6),     # Legendary — clears Epic's max
        ]
    for (_r, _c, _cs, w) in bars:
        lay = w.layout()
        kids = [lay.itemAt(i).widget() for i in range(3)]
        for nub in (kids[0], kids[2]):
            assert nub.minimumWidth() == nub.maximumWidth() == 8
            assert nub.minimumHeight() == nub.maximumHeight() == 8
            assert "border-radius:4px" in nub.styleSheet()
        bar = kids[1]
        assert bar.minimumHeight() == bar.maximumHeight() == 4


@_SECTION_MARK
def test_single_column_item():
    # Chest_RDemon_Ass (Rare armor): per-rarity stats not in the game yet,
    # so ONE column — RARE — with four stats and per-rank gains (base col 1,
    # max col 4), each stat's true base tinted Rare blue
    grid = _grid("Chest_RDemon_Ass")
    assert _labels(grid, 0) == [(0, "STATS"), (1, "RARE")]
    assert _labels(grid, 1) == [(1, "base"), (4, "max")]
    for row, label in ((2, "Dexterity"), (3, "Armor"), (4, "Fervor"),
                       (5, "Vitality")):
        assert _tinted_cols(grid, row) == [1], row
        cell = _cell(grid, row, 1)
        assert _fill_rgb(cell.styleSheet()) == (83, 155, 245), row  # Rare
    # a real range (base != max): the nub+bar+nub container spans cols 1-4
    bars = _bar_rows(grid)
    assert [(r, c, cs) for (r, c, cs, _) in bars] == [(6, 1, 4)]
    w = bars[0][3]
    assert w.layout() is not None     # the nub + bar + nub container


@_SECTION_MARK
def test_single_rarity_multi_stat():
    # Shoulders_RManfish_FigAss: Rare only, four stats. The widest stat
    # (Armor, 4 unique values) drives the block; every stat's base is col 1.
    grid = _grid("Shoulders_RManfish_FigAss")
    assert _labels(grid, 0) == [(0, "STATS"), (1, "RARE")]
    assert _labels(grid, 1) == [(1, "base"), (4, "max")]
    for row in range(2, 8):
        assert _tinted_cols(grid, row) == [1], row
    assert [(r, c, cs) for (r, c, cs, _) in _bar_rows(grid)] == [(8, 1, 4)]
    # value cells carry no hover tooltip
    assert _cell(grid, 2, 1).toolTip() == ""


@_SECTION_MARK
def test_crafted_gear_matrix_hides_steps_show_base_only():
    """Crafted gear (fixed level, set stats, can't be upgraded) renders only
    its base column when show_steps=False — no step columns, no max label,
    one value per stat. The full ladder still renders with steps by default.
    """
    it = idata.item("Chest_RCrimson_WizCle_Craft")   # Sacrificial Vestments
    assert idata.item_fixed_level(it) == 20
    ladder = idata.upgrade_ladder(it, level=20)
    assert ladder and ladder[0]["steps"]             # the data keeps its steps

    base_only = build_upgrades_matrix(ladder, show_steps=False)
    _assert_base_only_matrix(base_only, ladder)

    # default (show_steps=True) still renders the full ladder with steps
    full = build_upgrades_matrix(ladder)
    assert _labels(full, 0) == [(0, "STATS"), (1, "RARE")]
    assert _labels(full, 1) == [(1, "base"), (4, "max")]
    # a step column exists for a stat with real upgrade gains (Armor climbs
    # 156 -> 180; Faith/Intellect stay flat across the steps, so their row
    # keeps only the base cell)
    armor_row = 2 + _ladder_order(ladder).index("Armor")
    assert _cell(full, armor_row, 2) is not None


@_SECTION_MARK
def test_locked_matrix_hides_steps_show_base_only():
    """Locked armor AND jewelry (can't be upgraded in-game yet — weapons
    upgrade at Rare/Epic/Legendary, armor/jewelry do not) render only their
    base column when show_steps=False, exactly like crafted gear. Every locked
    slot is locked regardless of source — boss drops (Nepsilon), vendor
    caches (Smile of the Demolisher from the Chaotic Gear Cache), zone
    drops (Ringlet of Precision) — while weapons keep the full ladder.
    """
    it = idata.item("Shoulders_RManfish_FigAss")   # drops from boss Nepsilon
    assert idata.is_gear(it)
    assert idata.upgrade_locked(it["id"])
    # armor from a vendor cache is locked too (Back slot, Chaotic Gear Cache)
    assert idata.upgrade_locked("Back_RDemon_FigWiz")
    assert idata.upgrade_locked("Chest_RDemon_Ass")
    # jewelry (Ring / Neck / Trinket) is locked too — it only drops at its
    # authored zone-tier level and never upgrades (the armor_locked alias
    # agrees, since it is a pure alias of upgrade_locked)
    assert idata.upgrade_locked("Necklace_Z1_Ap")
    assert idata.upgrade_locked("Finger_Z2_Cri")   # Ringlet of Precision
    assert idata.upgrade_locked("Trinket_Demon")
    assert idata.armor_locked("Finger_Z2_Cri")     # deprecated alias
    # weapons stay unlocked even when boss-dropped (Cheese Moon, Mace)
    assert not idata.upgrade_locked("Axe_Boomerang")
    assert not idata.upgrade_locked("Mace_Benediction")
    assert not idata.upgrade_locked("Daggers_Start")

    ladder = idata.upgrade_ladder(it, level=25)
    assert ladder and ladder[0]["steps"]           # the data keeps its steps

    base_only = build_upgrades_matrix(ladder, show_steps=False)
    _assert_base_only_matrix(base_only, ladder)

    # a jewelry item renders the same base-only matrix (Ringlet of
    # Precision — Uncommon ring, zone drop at its authored level 20;
    # Uncommon keeps no steps since it cannot upgrade live)
    jit = idata.item("Finger_Z2_Cri")
    jladder = idata.upgrade_ladder(jit, level=20)
    assert jladder and jladder[0]["steps"] == []
    jbase = build_upgrades_matrix(jladder, show_steps=False)
    assert _labels(jbase, 1) == [(1, "base")]
    for row in range(2, 2 + len(_ladder_order(jladder))):
        assert _tinted_cols(jbase, row) == [1], row
        assert len([c for (r, c, _, w) in _rows(jbase)
                    if r == row and c > 0 and hasattr(w, "text")]) == 1, row
    # Rare jewelry still carries its steps in the data (Trinket_Demon +3)
    tlad = idata.upgrade_ladder(idata.item("Trinket_Demon"), level=25)
    assert tlad and len(tlad[0]["steps"]) == 3

    # default (show_steps=True) still renders the full ladder with steps
    full = build_upgrades_matrix(ladder)
    assert _labels(full, 0) == [(0, "STATS"), (1, "RARE")]
    assert _labels(full, 1) == [(1, "base"), (4, "max")]
    assert _cell(full, 2, 2) is not None             # a step column exists


@_SECTION_MARK
def test_epic_armor_is_locked_renders_base_only():
    """Epic armor joins the locked set — its +N path isn't live in-game yet.

    The Armor tab's UPGRADE LADDER advertised Epic armor's +1..+4 step gains,
    but the game has no way to apply them (live 2026-10-05: "epic gear cant be
    upgraded yet"). Epic armor now renders base-only like every other armor
    piece, while the shipped data keeps its steps and weapons stay upgradable.
    """
    it = idata.item("Back_EBee_AssCle")             # an Epic dungeon set piece
    assert idata.is_gear(it)
    assert idata.category(it["type"]) == "Armor"
    assert idata.item_display_rarity(it["id"]) == "Epic"
    assert idata.upgrade_locked(it["id"])

    ladder = idata.upgrade_ladder(it, level=25)
    assert ladder and ladder[0]["rarity"] == "Epic"
    assert ladder[0]["steps"]                       # the DATA keeps its steps

    # the page's show_steps=False render: one EPIC base column, no step cells
    base_only = build_upgrades_matrix(ladder, show_steps=False)
    assert _labels(base_only, 0) == [(0, "STATS"), (1, "EPIC")]
    assert _labels(base_only, 1) == [(1, "base")]
    for row in range(2, 2 + len(_ladder_order(ladder))):
        cells = [(c, w.text()) for (r, c, _, w) in _rows(base_only)
                 if r == row and c > 0 and hasattr(w, "text")]
        assert cells == [(1, _cell(base_only, row, 1).text())], (row, cells)
        assert _tinted_cols(base_only, row) == [1], row

    # weapons keep the full ladder with steps
    assert not idata.upgrade_locked("Axe_Boomerang")


@_SECTION_MARK
def test_level_label_above_base_column():
    """With a level_label, it sits at grid (0, 1) — directly above the first
    rarity's base column — and every other row shifts down by one."""
    it = idata.item("Mace_Benediction")
    ladder = idata.upgrade_ladder(it, level=25)
    plain = build_upgrades_matrix(ladder)
    assert [(r, c, w.text()) for (r, c, _, w) in _rows(plain)
                if r == 0 and hasattr(w, "text")] == \
            [(0, 0, "STATS"), (0, 1, "RARE"), (0, 5, "EPIC"),
             (0, 10, "LEGENDARY")]
    lbl = QtWidgets.QLabel("L25")
    lab = build_upgrades_matrix(ladder, level_label=lbl)
    assert lab.indexOf(lbl) >= 0
    assert lab.getItemPosition(lab.indexOf(lbl))[:2] == (0, 1)
    # the rarity headers (and the STATS tag) shift down one row to make
    # room for the label
    assert [(r, c, w.text()) for (r, c, _, w) in _rows(lab)
            if r == 1 and hasattr(w, "text")] == \
            [(1, 0, "STATS"), (1, 1, "RARE"), (1, 5, "EPIC"),
             (1, 10, "LEGENDARY")]


# ======================================================================
# test_items_list_hit_test
# ======================================================================

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtWidgets  # noqa: E402

from farever_companion import paths  # noqa: E402
from farever_companion.ui import copy_menu  # noqa: E402
from farever_companion.ui.pages.items import ItemPageMixin, support  # noqa: E402


def _drops_data_present() -> bool:
    try:
        return paths.item_drops_path().exists()
    except Exception:
        return False


_SECTION_MARK_1 = pytest.mark.skipif(
    not _drops_data_present(),
    reason="requires bundled data (assets/data/item_drops.json)")


class _Page(QtWidgets.QWidget, ItemPageMixin):
    def _page_container(self, title=None):
        page = QtWidgets.QWidget()
        return page, QtWidgets.QVBoxLayout(page)

    def _codex_jump_to_unit(self, *args, **kwargs):
        return None


def _page():
    p = _Page()
    page = p._page_gear()
    QtWidgets.QVBoxLayout(p).addWidget(page)
    p.resize(1200, 820)
    p.show()
    # drain the batched icon-fill timer (0-ms ticks) — the page's flow
    # rebuild happens when the fill completes
    app = QtWidgets.QApplication.instance()
    for _ in range(40):
        app.processEvents()
    return p


def _visible_tiles(lst):
    """(index, item) for every visible non-header row, in model order."""
    out = []
    for i in range(lst.count()):
        li = lst.item(i)
        if li is not None and not li.isHidden() \
                and not li.data(support.TYPE_HEADER_ROLE):
            out.append((i, li))
    return out


def _tile_hits(lst):
    """[(index, id) of visible tiles whose own center indexAt misses]."""
    bad = []
    for i, li in _visible_tiles(lst):
        vr = lst.visualRect(lst.indexFromItem(li))
        if vr.isNull() or vr.width() <= 0 or vr.height() <= 0:
            continue
        if not lst.indexAt(vr.center()).isValid():
            bad.append((i, li.data(support.ID_ROLE)))
    return bad


@_SECTION_MARK_1
def test_first_visible_tile_is_clickable():
    """The first tile of the default Gear list (the one right under the
    first type header) must hit-test to itself — it was painted but dead
    to clicks when hidden header rows desynced Qt's icon-mode layout."""
    p = _page()
    lst = p._items_list
    assert _visible_tiles(lst), "list should have visible tiles"
    i, li = _visible_tiles(lst)[0]
    vr = lst.visualRect(lst.indexFromItem(li))
    assert vr.isValid() and vr.width() > 0 and vr.height() > 0
    hit = lst.indexAt(vr.center())
    assert hit.isValid(), \
        f"first tile {li.data(support.ID_ROLE)!r} is not clickable " \
        f"(rect {vr})"
    assert hit.row() == i


@_SECTION_MARK_1
def test_every_visible_tile_is_clickable():
    """All visible tiles of the default Gear list hit-test to themselves
    — a refilter-triggered re-flow must not break tiles mid-list."""
    p = _page()
    bad = _tile_hits(p._items_list)
    assert not bad, f"tiles not clickable: {bad[:6]}"


@_SECTION_MARK_1
def test_tiles_clickable_after_refilter():
    """Filtering (a search query) reflows the grid; the tiles must stay
    clickable right after the refilter, not only after the deferred
    layout eventually runs."""
    p = _page()
    p._items_search.setText("axe")
    for _ in range(40):
        QtWidgets.QApplication.instance().processEvents()
    bad = _tile_hits(p._items_list)
    assert not bad, f"tiles not clickable after refilter: {bad[:6]}"


@_SECTION_MARK_1
def test_first_tile_copy_menu_finds_text():
    """Right-click on the first tile must surface its text in the copy
    menu (the app-wide copy filter reads indexAt through the viewport,
    which was the exact spot that returned 'no item' before the fix)."""
    p = _page()
    lst = p._items_list
    i, li = _visible_tiles(lst)[0]
    vr = lst.visualRect(lst.indexFromItem(li))
    gpos = lst.viewport().mapToGlobal(vr.center())
    text = copy_menu._item_text_at(lst, gpos)
    assert text == li.text()


# ======================================================================
# test_items_chips
# ======================================================================

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtWidgets  # noqa: E402

from farever_companion import paths  # noqa: E402
from farever_companion.data import items as idata  # noqa: E402
from farever_companion.ui.pages.items import ItemPageMixin, support  # noqa: E402


def _item_drops_data_present() -> bool:
    try:
        return paths.item_drops_path().exists()
    except Exception:
        return False


_SECTION_MARK_2 = pytest.mark.skipif(
    not _item_drops_data_present(),
    reason="requires bundled data (assets/data/item_drops.json)")


# --- oracle: family_counts vs the gear-tab matcher -------------------------


def _gear_match(item_id: str, cls: str, only: bool, rarities,
                rating: str) -> bool:
    """The gear-tab predicate (the class/only/rarity-set/rating parts of
    _items_match) applied to one item, as an independent oracle. The
    listable check matters: codex-only rows (e.g. Back_EBee_Wiz) carry a
    real type and rarity but never appear in the list, so a chip count that
    skips it would disagree with the visible rows."""
    it = idata.item(item_id) or {}
    # the browse shop gate is ONE contract (support.is_hidden_shop_item): the
    # oracle references it rather than re-spelling the rule, so a future
    # shop-source change cannot make the oracle and the page disagree. The
    # contract's own guard (tests/test_items_shop_gate.py) pins the equipment
    # invariant this gate exists for.
    if support.is_hidden_shop_item(it):
        return False
    if not support.is_listable_item(it):
        return False
    if not idata.is_gear(it):
        return False
    if idata.category(it.get("type")) == "Crafting":
        return False
    if rating and rating not in idata.gear_ratings(item_id):
        return False
    if rarities and idata.item_display_rarity(item_id) not in rarities:
        return False
    classes = it.get("classes") or []
    if classes:
        if cls and cls not in classes:
            return False
        if only and cls and len(classes) > 1:
            return False
    return True


@pytest.mark.parametrize("cls", ["", "Fighter", "Assassin", "Cleric",
                                 "Wizard"])
@pytest.mark.parametrize("only", [False, True])
@pytest.mark.parametrize("rarities", [(), support.RARITY_BUTTONS,
                                      ("Rare", "Epic"), ("Uncommon",)])
def test_family_counts_mirror_gear_matcher(cls, only, rarities):
    """Every armor family's chip count equals the matcher's count under the
    same class / Only / rarity-button state (rating off)."""
    for fam in ("Back", "Chest", "Feet", "Head"):
        expected = sum(
            1 for it in idata.items()
            if support.type_family(it.get("type")) == fam
            and _gear_match(it.get("id") or "", cls, only, rarities, ""))
        assert support.family_counts(cls, only, rarities) \
            .get(fam, 0) == expected, (fam, cls, only, rarities)


@_SECTION_MARK_2
def test_family_counts_rating_mirrors_matcher():
    """A picked rating narrows the chip counts to gear rolling it."""
    for rating in idata.RATING_STATS:
        expected = sum(
            1 for it in idata.items()
            if support.type_family(it.get("type")) == "Back"
            and _gear_match(it.get("id") or "", "", False,
                            support.RARITY_BUTTONS, rating))
        got = support.family_counts(
            "", False, support.RARITY_BUTTONS, rating).get("Back", 0)
        assert got == expected, rating


# --- full-page: chip text follows the live filters -------------------------


def _chip_counts(p):
    return {f: c.text() for f, c in p._items_type_chips.items()
            if c.isVisible()}


def _visible_by_family(p):
    out = {}
    for i in range(p._items_list.count()):
        li = p._items_list.item(i)
        if li.data(support.TYPE_HEADER_ROLE) or li.isHidden():
            continue
        fam = support.type_family(li.data(support.TYPE_ROLE))
        out[fam] = out.get(fam, 0) + 1
    return out


def _chip_number(text: str) -> int:
    return int(text.split("·")[-1].strip())


@_SECTION_MARK_2
def test_chip_counts_track_class_and_only():
    """Chips start at the floor-filtered total, drop with a picked class,
    drop again with Only — and always match the visible rows."""
    p = _page()
    support.quick_pick(p, "Armor")
    assert _chip_counts(p)["Back"] == \
        f"Back · {_visible_by_family(p)['Back']}"

    # Fighter: the Back chip drops to only Fighter-capable, floor-filtered
    p._items_class_chips["Fighter"].setChecked(True)
    assert _chip_counts(p)["Back"] == \
        f"Back · {_visible_by_family(p)['Back']}"

    # Only: dual-class gear drops out of both the chip and the list
    p._items_only_chip.setChecked(True)
    assert _chip_counts(p)["Back"] == \
        f"Back · {_visible_by_family(p)['Back']}"

    # dropping Only restores the dual-class count
    p._items_only_chip.setChecked(False)
    assert _chip_counts(p)["Back"] == \
        f"Back · {_visible_by_family(p)['Back']}"


@_SECTION_MARK_2
def test_chip_counts_track_rarity_buttons_and_rating():
    """The default Rarity floor (Rare lit) keeps Uncommon/Common out of the
    chip counts and can't be un-lit; lighting Epic — or picking a rating —
    narrows the count, and the chip always equals the live rows."""
    p = _page()
    support.quick_pick(p, "Armor")
    assert p._items_rarity_filter == set(support.RARITY_DEFAULT)
    rare_floor = _chip_number(_chip_counts(p)["Back"])

    # the lit button can't be un-lit: the floor — and so the count — holds
    # instead of widening to every tier
    p._items_rarity_toggle_chips["Rare"].setChecked(False)
    assert p._items_rarity_filter == set(support.RARITY_DEFAULT)
    assert p._items_rarity_toggle_chips["Rare"].isChecked()
    assert _chip_number(_chip_counts(p)["Back"]) == rare_floor

    # EPIC on -> RARE off: the chip narrows to the epic rows
    p._items_rarity_toggle_chips["Epic"].setChecked(True)
    assert p._items_rarity_filter == {"Epic"}
    assert not p._items_rarity_toggle_chips["Rare"].isChecked()
    epic_only = _chip_number(_chip_counts(p)["Back"])
    assert 0 < epic_only < rare_floor
    assert _chip_counts(p)["Back"] == \
        f"Back · {_visible_by_family(p)['Back']}"

    # RARE on again restores the floor, and a rating chip narrows it
    p._items_rarity_toggle_chips["Rare"].setChecked(True)
    assert p._items_rarity_filter == set(support.RARITY_DEFAULT)
    assert _chip_number(_chip_counts(p)["Back"]) == rare_floor
    assert _chip_counts(p)["Back"] == \
        f"Back · {_visible_by_family(p)['Back']}"
    p._items_rating_chips["Critical"].setChecked(True)
    assert _chip_counts(p)["Back"] == \
        f"Back · {_visible_by_family(p)['Back']}"
    assert _chip_number(_chip_counts(p)["Back"]) < rare_floor


def _dominant_colour(pm) -> str:
    """The commonest opaque colour in a pixmap — a tile icon's fill."""
    img = pm.toImage()
    seen: dict = {}
    for y in range(0, img.height(), 2):
        for x in range(0, img.width(), 2):
            c = img.pixelColor(x, y)
            if c.alpha() >= 40:
                seen[c.name()] = seen.get(c.name(), 0) + 1
    return max(seen, key=seen.get) if seen else ""


def _pane_labels(p) -> set:
    """Every label text in the right pane (the item card / dungeon sheet)."""
    return {lbl.text() for lbl in p._items_detail.findChildren(QtWidgets.QLabel)
            if lbl.text()}


@_SECTION_MARK_2
def test_dungeons_item_detail_mouse_back_restores_faction_items():
    """Inside Dungeons tab:
    1. Selecting Kobold shows Kobold faction card (pane_state='boss').
    2. Clicking an item sets pane_state='item'.
    3. consume_pane_back() returns True and restores the Kobold items card.
    4. Next back returns to the previous faction (Bee).
    5. Next back returns to the previous category (Weapons).
    """
    p = _page()
    # Start at Weapons by default, then switch to Dungeons
    support.quick_pick(p, "Dungeons")
    assert p._items_category == "@dungeon"
    assert p._items_dungeon_view.isVisible()

    # Switch faction to Kobold
    p._items_dungeon_view._pick("Kobold")
    assert p._items_dungeon_view._fac == "Kobold"
    assert p._dg_shown_faction == "Kobold"
    assert p._dg_pane_state == "boss"

    # Click an item to view item detail
    kobold_group = p._items_dungeon_view._groups.get("Kobold")
    assert kobold_group is not None and kobold_group["shared"]
    item_id = kobold_group["shared"][0]
    p._items_show_id(item_id)
    assert p._dg_pane_state == "item"

    # First back: restores the Kobold items card
    assert p.consume_pane_back() is True
    assert p._dg_pane_state == "boss"
    assert p._dg_shown_faction == "Kobold"

    # Second back: restores the previous faction (Bee)
    assert p.consume_pane_back() is True
    assert p._items_dungeon_view._fac == "Bee"
    assert p._dg_shown_faction == "Bee"

    # Third back: restores the previous category (Weapons)
    assert p.consume_pane_back() is True
    assert p._items_category == "Weapons"

    # Fourth back: no more category/faction history, returns False for nav history
    assert p.consume_pane_back() is False


# --- the Rarity buttons: own row, multi-select -----------------------------


def _visible_rarities(p):
    """{rarity: count} over the visible gear rows."""
    out = {}
    for i in range(p._items_list.count()):
        li = p._items_list.item(i)
        if li.data(support.TYPE_HEADER_ROLE) or li.isHidden():
            continue
        rar = idata.item_display_rarity(li.data(support.ID_ROLE))
        out[rar] = out.get(rar, 0) + 1
    return out


@_SECTION_MARK_2
def test_rarity_buttons_are_one_at_a_time_toggles():
    """The pinned Rarity row is single-select and strict: exactly one button
    is ALWAYS lit — picking Epic turns Rare OFF (and shows only the epic
    gear) — and the lit one can't be switched off, so the list never falls
    back to every tier."""
    p = _page()
    assert set(p._items_rarity_toggle_chips) == set(support.RARITY_BUTTONS)

    def lit():
        return {r for r, c in p._items_rarity_toggle_chips.items()
                if c.isChecked()}

    assert lit() == {"Rare"}            # the default tier, one button
    for chip in p._items_rarity_toggle_chips.values():
        assert chip.toolTip() == ""      # no hover tooltips

    support.quick_pick(p, "Armor")
    vis = _visible_rarities(p)           # strict: only the rare rows
    assert vis and set(vis) == {"Rare"}
    assert "Epic" not in vis and "Uncommon" not in vis and "Common" not in vis

    # EPIC on -> RARE off, and only the epic gear is left
    p._items_rarity_toggle_chips["Epic"].setChecked(True)
    assert lit() == {"Epic"}
    assert not p._items_rarity_toggle_chips["Rare"].isChecked()
    assert not p._items_rarity_toggle_chips["Uncommon"].isChecked()
    vis = _visible_rarities(p)
    assert vis and set(vis) == {"Epic"}

    # RARE on -> EPIC off again (still one at a time)
    p._items_rarity_toggle_chips["Rare"].setChecked(True)
    assert lit() == {"Rare"}
    assert _visible_rarities(p).get("Rare")

    # the lit button can't be switched off: it stays lit, the same tier
    # holds, and no other tier comes back (the list must never show
    # everything by accident — Clear all is the only way back)
    p._items_rarity_toggle_chips["Rare"].setChecked(False)
    assert lit() == {"Rare"}
    assert p._items_rarity_filter == set(support.RARITY_DEFAULT)
    vis = _visible_rarities(p)
    assert "Uncommon" not in vis and "Common" not in vis

    # Clear all restores the default tier, still exactly one lit
    p._items_clear_filters()
    assert p._items_rarity_filter == set(support.RARITY_DEFAULT)
    assert lit() == {"Rare"}

    # an emptied filter is repaired to the default tier, never left showing
    # every tier with no button lit
    p._items_rarity_filter = set()
    p._items_sync_rarity_chips()
    assert p._items_rarity_filter == set(support.RARITY_DEFAULT)
    assert lit() == {"Rare"}

    # the bottom tier is a plain filter too: Common is the starter clothes,
    # never 'Common and everything above it'
    support.quick_pick(p, "Armor")      # Clear all dropped the tab
    p._items_rarity_toggle_chips["Common"].setChecked(True)
    assert lit() == {"Common"}
    assert p._items_rarity_filter == {"Common"}
    vis = _visible_rarities(p)
    assert vis and set(vis) == {"Common"}
    assert _chip_number(p._items_rarity_toggle_chips["Common"].text()) == \
        vis["Common"]


@_SECTION_MARK_2
def test_rarity_counts_are_real_with_no_quick_tab_picked():
    """Clicking the active Quick tab off lists every tab at once — the Rarity
    buttons must still carry real counts there (a row of '· 0' reads as
    broken), and every tier's number must equal its rows."""
    p = _page()
    support.quick_pick(p, "Weapons")      # the active tab toggles off
    assert p._items_category == ""
    counts = {r: _chip_number(c.text())
              for r, c in p._items_rarity_toggle_chips.items() if c.isVisible()}
    assert all(counts.values()), counts

    # every tier at once: the numbers are exactly the visible rows
    p._items_rarity_filter = set(support.RARITY_BUTTONS)
    p._items_refilter()
    assert _visible_rarities(p) == counts


@_SECTION_MARK_2
def test_rarity_buttons_hide_tiers_the_tab_never_rolls():
    """Every weapon in the data is Rare, so the Weapons tab shows only the
    RARE button — a dead tier never sits in the row. Armor carries all
    four; Jewelry has no Common (accessories start at Uncommon); Dungeons
    hides the whole row (both tiers always show, so no tier switch)."""
    p = _page()
    assert p._items_category == "Weapons"        # the default tab
    assert {r: c.isVisible() for r, c in p._items_rarity_toggle_chips.items()} \
        == {"Common": False, "Uncommon": False, "Rare": True, "Epic": False}

    # the hidden tiers are cosmetic: dropping them can't change a weapon row
    before = _visible_rarities(p)
    assert before and "Epic" not in before and "Uncommon" not in before
    p._items_rarity_filter.discard("Epic")
    p._items_refilter()
    assert _visible_rarities(p) == before

    # Armor rolls every tier; Jewelry has no Common. Dungeons shows both
    # tiers at once with no tier switch, so its whole rarity row hides.
    for tab, hidden in (("Armor", set()), ("Jewelry", {"Common"}),
                        ("Dungeons", {"Common", "Uncommon",
                                      "Rare", "Epic"})):
        p._items_rarity_filter = set(support.RARITY_DEFAULT)
        support.quick_pick(p, tab)
        assert {r for r, c in p._items_rarity_toggle_chips.items()
                if not c.isVisible()} == hidden, tab


@_SECTION_MARK_2
def test_rarity_buttons_live_counts_track_the_tier_rows():
    """Every Rarity button carries its tier's row count ('Rare · 135'),
    mirroring the live class/Only chips — and a tier's number never moves
    when the other tier is toggled (it says what the tier holds)."""
    p = _page()
    vis = _visible_rarities(p)                    # Weapons: Rare only
    assert p._items_rarity_toggle_chips["Rare"].text() == f"Rare · {vis['Rare']}"

    support.quick_pick(p, "Armor")

    def texts():
        return {r: c.text() for r, c in p._items_rarity_toggle_chips.items()}

    def checked_rows():
        """{tier: 'Tier · n'} for the CHECKED tiers, off the visible rows."""
        vis = _visible_rarities(p)
        checked = {r for r, c in p._items_rarity_toggle_chips.items()
                   if c.isChecked()}
        return {r: f"{r} · {vis.get(r, 0)}" for r in checked}

    assert {r: texts()[r] for r in checked_rows()} == checked_rows()

    # toggling a tier leaves the other tier's number alone (it says what the
    # tier HOLDS, so an unchecked tier keeps its count too)
    before = texts()
    p._items_rarity_toggle_chips["Epic"].setChecked(False)
    assert texts() == before
    p._items_rarity_toggle_chips["Epic"].setChecked(True)

    # the class chip narrows the numbers with the rows
    p._items_class_chips["Fighter"].setChecked(True)
    assert {r: texts()[r] for r in checked_rows()} == checked_rows()


@_SECTION_MARK_2
def test_rarity_button_counts_follow_the_active_dungeon_faction():
    """On the Dungeons tab the counts tally the ACTIVE faction's drops (the
    card the user is looking at) under the class/Only gate the tiles use,
    and they follow the faction chips."""
    from farever_companion.ui.pages.items.dungeon_view import _usable

    p = _page()
    support.quick_pick(p, "Dungeons")
    dv = p._items_dungeon_view

    def expect(faction):
        g = dv._groups[faction]
        pool = (set(g["shared"])
                | {w for b in g["bosses"] for w in b["weapons"]}
                | set(idata.epic_pieces(g)))
        # a button's number says what its tier HOLDS, so the tier pick stays
        # out of the expectation — the number tracks the tiles' class/Only
        # gate, and _usable applies both
        picked = p._items_rarity_filter
        p._items_rarity_filter = set(support.RARITY_BUTTONS)
        try:
            return {rar: sum(1 for i in pool
                             if idata.item_display_rarity(i) == rar
                             and _usable(p, i))
                    for rar in p._items_rarity_toggle_chips}
        finally:
            p._items_rarity_filter = picked

    texts = lambda: {r: c.text() for r, c in p._items_rarity_toggle_chips.items()}
    for faction in dv._groups:
        dv._pick(faction)
        want = expect(faction)
        assert texts() == {r: f"{r} · {n}" for r, n in want.items()}, faction
        assert sum(want.values()) > 0                                      , faction


@_SECTION_MARK_2
def test_rarity_buttons_filter_jewelry_not_dungeon_gear():
    """Jewelry filters on its real tiers — its Uncommon pieces have a button
    of their own, so the tier numbers and the rows agree. The Dungeons tab
    is exempt: it shows both tiers at once, so its rarity row hides and its
    tiles ignore the lit tier (only the class/Only chips filter them)."""
    p = _page()
    support.quick_pick(p, "Jewelry")
    assert set(_visible_rarities(p)) == {"Rare"}   # the default tier

    def counts():
        # only the buttons this tab carries (Jewelry has no Common)
        return {r: int(c.text().split("·")[1])
                for r, c in p._items_rarity_toggle_chips.items()
                if c.isVisible()}

    # UNCOMMON on -> RARE off, and the list is exactly that tier: the rows
    # equal the button's own number (a floor would drag Rare + Epic in)
    p._items_rarity_toggle_chips["Uncommon"].setChecked(True)
    assert p._items_rarity_filter == {"Uncommon"}
    assert not p._items_rarity_toggle_chips["Rare"].isChecked()
    assert _visible_rarities(p) == {"Uncommon": counts()["Uncommon"]}
    assert counts()["Uncommon"] > 0

    # EPIC on -> RARE off: only the epic jewelry is left
    p._items_rarity_toggle_chips["Epic"].setChecked(True)
    assert p._items_rarity_filter == {"Epic"}
    assert set(_visible_rarities(p)) == {"Epic"}

    p._items_rarity_toggle_chips["Rare"].setChecked(True)
    support.quick_pick(p, "Dungeons")
    # the tier row hides on this tab — nothing here answers to it — while
    # the pane carries both tiers at once (the shared Rare pool next to the
    # bosses' Epic heroic sets)
    assert not p._items_chips_box.isVisible()
    assert not p._items_rarity_toggle_box.isVisible()
    dv = p._items_dungeon_view
    shared = list(dv._groups[dv._fac]["shared"])
    # the shared set is 24 Rare pieces + the Epic trinket, and the pane
    # shows the whole of it whatever tier is lit
    rare_shared = [i for i in shared
                   if idata.item_display_rarity(i) == "Rare"]
    epic_shared = [i for i in shared
                   if idata.item_display_rarity(i) == "Epic"]
    assert rare_shared and epic_shared
    labels = _pane_labels(p)
    assert {idata.item(i)["name"] for i in shared} <= labels

    # the boss's weapons mirror into the pane once it is picked (unpicked
    # they live in the left-column rows, not the pane): flipping the lit
    # tier to EPIC changes nothing either way — the picked boss keeps its
    # own drops while the shared pool stays in the faction view, and the
    # render stamp still records the (now irrelevant) tier pick
    p._items_rarity_toggle_chips["Epic"].setChecked(True)
    assert p._items_rarity_filter == {"Epic"}
    picked = dv._groups[dv._fac]["bosses"][0]
    dv.toggle(dv._fac, picked["boss_id"])
    labels = _pane_labels(p)
    assert {idata.item(i)["name"] for i in picked["weapons"]} <= labels
    assert not ({idata.item(i)["name"] for i in shared} & labels)
    assert p._dg_rendered_class[2] == frozenset(p._items_rarity_filter)

@_SECTION_MARK_2
def test_dungeons_pane_lists_the_picked_boss_epic_heroic_drops():
    """The Epic dungeon set is boss-specific (it rolls the boss's own heroic
    table, not the shared pool), so the pane follows the picked boss. Every
    boss in these families has an `_HM` table, so the sets are whole and
    nothing is left unresolved; the faction chips count what the live
    filters hold rather than reporting a data gap."""
    p = _page()
    support.quick_pick(p, "Dungeons")
    dv = p._items_dungeon_view
    dv._pick("Manfish")
    g = dv._groups["Manfish"]
    by_boss = {b["boss_id"]: b for b in g["bosses"]}
    crab = {idata.item(i)["name"] for i in by_boss["Crabgantua"]["epic"]}
    nep = {idata.item(i)["name"] for i in by_boss["Nepsilon"]["epic"]}
    spon = {idata.item(i)["name"] for i in by_boss["SpongeBlob"]["epic"]}
    # every boss in the family has an `_HM` table, so the pane carries all
    # three sets
    assert len(crab) == len(nep) == len(spon) == 6
    assert not (crab & nep) and not (crab & spon) and not (nep & spon)
    # the remaining six live in the Hero Gear Cache: no dungeon of this
    # family's hands them out, so they are `epic_unknown` and stay OFF this
    # page (the gear list serves them)
    cached = {idata.item(i)["name"] for i in g["epic_unknown"]}
    assert len(cached) == 6
    assert not (cached & (crab | nep | spon))

    # both tiers show at rest: RARE is lit but the pane already carries
    # every boss's heroic set next to the shared pool — no tier switch
    assert (crab | nep | spon) <= _pane_labels(p)
    # ...and flipping the lit tier changes nothing on this tab
    p._items_rarity_toggle_chips["Epic"].setChecked(True)
    assert (crab | nep | spon) <= _pane_labels(p)
    # nothing with no dungeon of its own is drawn here
    assert not (cached & _pane_labels(p))

    # no chip reports a data gap: the labels carry the faction's live count
    assert not any("no drop source" in t for t in _pane_labels(p))
    from farever_companion.ui.pages.items.dungeon_view import _usable

    for fac, chip in dv._chips.items():
        assert "❓" not in chip.text(), fac
        want = sum(1 for iid in dv._pool(fac)
                   if _usable(p, iid, ignore_rarity=True))
        # the live count rides the chip's TOOLTIP, not its label: five
        # labels plus their counts needed ~940 px in a 579 px pane, which
        # pushed the last faction (Nightling) off the bar entirely
        assert chip.toolTip().startswith(f"{want} "), (fac, chip.toolTip())
        assert "·" not in chip.text(), (fac, chip.text())

    # picking a boss brings ITS heroic pieces in — and only its own.
    # The shared pool stays in the faction view, so a pick drops it too.
    dv.select("Manfish", "Crabgantua")
    labels = _pane_labels(p)
    assert p._dg_rendered_boss == "Crabgantua"
    assert crab <= labels and not (nep & labels)
    assert not ({idata.item(i)["name"] for i in g["shared"]} & labels)
    assert any("· EPIC ·" in t and "heroic mode" in t for t in labels)

    dv.select("Manfish", "Nepsilon")
    labels = _pane_labels(p)
    assert p._dg_rendered_boss == "Nepsilon"
    assert nep <= labels and not (crab & labels)
    # the cache block leaves with the pick too: it belongs to the faction
    assert not (cached & labels)

    # Bee is whole as well — Gatsbee, Mokshi AND Cleodora all have a table
    dv._pick("Bee")
    g = dv._groups["Bee"]
    bee_by_boss = {b["boss_id"]: b for b in g["bosses"]}
    gat = {idata.item(i)["name"] for i in bee_by_boss["Gatsbee"]["epic"]}
    mok = {idata.item(i)["name"] for i in bee_by_boss["Mokshi"]["epic"]}
    cle = {idata.item(i)["name"] for i in bee_by_boss["Cleodora"]["epic"]}
    assert len(gat) == len(mok) == len(cle) == 6
    # Bee's remaining six live in the Hero Gear Cache — no dungeon of Bee's
    # hands them out, so they stay off this page
    assert len(g["epic_unknown"]) == 6
    # narrowing to a Bee boss brings exactly its own heroic pieces
    dv.select("Bee", "Gatsbee")
    labels = _pane_labels(p)
    assert p._dg_rendered_boss == "Gatsbee"
    assert gat <= labels and not (mok & labels)
    # a Bee pick without a boss shows every boss's set plus the shared pool
    dv.select("Bee", "")            # back to the faction-wide pane
    labels = _pane_labels(p)
    assert (gat | mok | cle) <= labels
    # the count says what picking this faction would show — both tiers, so
    # the whole pool: the shared pieces plus the 3 bosses' weapons. It rides
    # the chip's TOOLTIP now (five labels + counts overran the 579 px pane)
    want = sum(1 for iid in dv._pool("Bee")
               if _usable(p, iid, ignore_rarity=True))
    assert dv._chips["Bee"].toolTip().startswith(f"{want} ")
    trinket = [i for i in g["shared"] if idata.item_display_rarity(i) == "Epic"]
    assert len(trinket) == 1
    assert idata.item(trinket[0])["name"] in _pane_labels(p)
    assert {idata.item(i)["name"] for i in g["shared"]} <= _pane_labels(p)

    # the lit tier is irrelevant on this tab: EPIC lit keeps the boss's
    # heroic set while the Rare shared pool stays in the faction view
    dv._pick("Manfish")
    dv.select("Manfish", "Crabgantua")
    p._items_rarity_toggle_chips["Epic"].setChecked(True)
    labels = _pane_labels(p)
    assert crab <= labels
    rare_name = idata.item(dv._groups["Manfish"]["shared"][0])["name"]
    assert rare_name not in labels


@_SECTION_MARK_2
def test_dungeon_pane_carries_every_boss_epic_set_until_one_is_picked():
    """With the Epic tier lit the pane carries EVERY boss's heroic set —
    a faction pick no longer singles one boss out — and clicking a boss row
    in the left list narrows the pane to that boss alone. Clicking the lit
    row again lets go, so the whole faction comes back with nothing lit."""
    p = _page()
    support.quick_pick(p, "Dungeons")
    dv = p._items_dungeon_view
    dv._pick("Kobold")
    p._items_rarity_toggle_chips["Epic"].setChecked(True)
    g = dv._groups["Kobold"]
    by_boss = {b["boss_id"]: b for b in g["bosses"]}
    reb = {idata.item(i)["name"] for i in by_boss["Reblochonk"]["epic"]}
    rat = {idata.item(i)["name"] for i in by_boss["Ratsar"]["epic"]}

    def lit_rows():
        return [k for k, r in dv._rows.items()
                if theme.GOLD in r.styleSheet()]

    # a faction pick opens on the whole faction: both blocks, no row lit
    assert p._dg_rendered_boss == "" and dv._sel == ("Kobold", "")
    labels = _pane_labels(p)
    assert reb <= labels and rat <= labels           # every boss at once
    # one block per boss with an `_HM` table, and nothing else
    assert sum("· EPIC ·" in t for t in labels) == (
        sum(1 for b in g["bosses"] if b["epic"]))
    assert lit_rows() == []
    # The subtitle counts and stops (2026-09-27). It used to end "· click a
    # boss to narrow" — an instruction for a gesture the list advertises
    # nowhere else, and the only part of the line that was not a fact about
    # this family. A picked row is lit, so the affordance is visible rather
    # than described.
    assert any("dungeons" in t and "with Epic drops" in t for t in labels)
    assert not any("click a boss" in t for t in labels), labels

    # clicking a boss row narrows the pane to that boss and lights the row
    dv.toggle("Kobold", "Reblochonk")
    labels = _pane_labels(p)
    assert p._dg_rendered_boss == "Reblochonk"
    assert reb <= labels and not (rat & labels)
    assert lit_rows() == [("Kobold", "Reblochonk")]
    assert any("Reblochonk only" in t for t in labels)

    # clicking the lit row again lets go: every boss back, nothing lit
    dv.toggle("Kobold", "Reblochonk")
    labels = _pane_labels(p)
    assert p._dg_rendered_boss == "" and dv._sel == ("Kobold", "")
    assert reb <= labels and rat <= labels
    assert lit_rows() == []

    # picking another boss MOVES the pick rather than clearing it
    dv.toggle("Kobold", "Ratsar")
    labels = _pane_labels(p)
    assert p._dg_rendered_boss == "Ratsar"
    assert rat <= labels and not (reb & labels)
    assert lit_rows() == [("Kobold", "Ratsar")]

    # a new faction starts over on the whole faction, not on the boss the
    # last one was narrowed to
    dv._pick("Manfish")
    assert dv._sel == ("Manfish", "") and p._dg_rendered_boss == ""
    labels = _pane_labels(p)
    for b in dv._groups["Manfish"]["bosses"]:
        want = {idata.item(i)["name"] for i in b["epic"]}
        assert not want or want <= labels
    assert sum("· EPIC ·" in t for t in labels) == (
        sum(1 for b in dv._groups["Manfish"]["bosses"] if b["epic"]))
    assert lit_rows() == []


@_SECTION_MARK_2
def test_picked_boss_shows_only_its_own_drops():
    """Picking a boss shows that boss's own drops only — its mirrored
    weapons and its heroic Epic set — while the family-wide shared pool
    stays in the faction view. No tier switch is involved: both tiers always
    show, so the other bosses' Epic blocks are there until the pick narrows
    them away."""
    p = _page()
    support.quick_pick(p, "Dungeons")
    dv = p._items_dungeon_view
    dv._pick("Kobold")
    assert p._items_rarity_filter == set(support.RARITY_DEFAULT)
    g = dv._groups["Kobold"]
    by_boss = {b["boss_id"]: b for b in g["bosses"]}
    reb = {idata.item(i)["name"] for i in by_boss["Reblochonk"]["epic"]}
    rat = {idata.item(i)["name"] for i in by_boss["Ratsar"]["epic"]}
    assert reb and rat and not (reb & rat)
    shared = {idata.item(i)["name"] for i in g["shared"]}
    # no pick: every boss's Epic block plus the family's cache block stand
    # next to the shared pool, even with Rare lit — the Rarity buttons don't
    # gate this tab
    labels = _pane_labels(p)
    assert reb <= labels and rat <= labels
    assert shared <= labels
    # one block per boss that has an `_HM` table, and nothing else
    want_blocks = sum(1 for b in g["bosses"] if b["epic"])
    assert want_blocks == 3, "three Kobold bosses have an `_HM` table"
    assert sum("· EPIC ·" in t for t in labels) == want_blocks
    # pick: the shared pool leaves the pane — weapons + this boss's Epic only
    dv.toggle("Kobold", "Reblochonk")
    labels = _pane_labels(p)
    assert reb <= labels and not (rat & labels)
    assert sum("· EPIC ·" in t for t in labels) == 1
    assert not (shared & labels)
    # the pick's weapons mirror into the pane too
    rw = {idata.item(w)["name"] for w in by_boss["Reblochonk"]["weapons"]}
    assert rw and rw <= labels


@_SECTION_MARK_2
def test_rating_chip_filters_dungeon_boss_loot():
    """A picked rating narrows the Dungeons tab like the class chip does:
    the faction counts, the boss-row weapon chips and the pane tiles all
    keep only gear rolling that rating; clearing it restores the whole view."""
    from farever_companion.ui.pages.items.dungeon_view import _usable

    p = _page()
    support.quick_pick(p, "Dungeons")
    dv = p._items_dungeon_view
    dv._pick("Manfish")
    g = dv._groups["Manfish"]
    pool = dv._pool("Manfish")
    full = sum(1 for iid in pool if _usable(p, iid, ignore_rarity=True))
    assert full == len(pool)      # nothing picked: the whole pool counts
    assert dv._chips["Manfish"].toolTip().startswith(f"{full} ")

    # 'Crown of the Sea' rolls Armor Penetration only, so Critical drops it
    # everywhere: its name is unique pool-wide, no twin carries it
    p._items_rating_chips["Critical"].setChecked(True)
    assert p._items_rating == "Critical"
    want = sum(1 for iid in pool if _usable(p, iid, ignore_rarity=True))
    assert 0 < want < full
    assert dv._chips["Manfish"].toolTip().startswith(f"{want} ")
    assert "Crown of the Sea" not in _pane_labels(p)

    # the boss rows lose their off-rating weapon chips too: Crabgantua keeps
    # Clawdius (Critical) and drops the rating-free Kneecap
    row = dv._rows[("Manfish", "Crabgantua")]
    row_labels = {lbl.text() for lbl in row.findChildren(QtWidgets.QLabel)}
    assert "Clawdius" in row_labels
    assert "Crabgantua's Kneecap" not in row_labels

    # a boss pick narrows the already-rated pane: only its Critical Epic
    # pieces, counted in the block header
    by_boss = {b["boss_id"]: b for b in g["bosses"]}
    dv.toggle("Manfish", "Crabgantua")
    crit = [e for e in by_boss["Crabgantua"]["epic"]
            if "Critical" in idata.gear_ratings(e)]
    assert crit
    labels = _pane_labels(p)
    assert f"EPIC · {len(crit)} pieces" in \
        next(t for t in labels if "Crabgantua" in t and "· EPIC ·" in t)
    assert {idata.item(e)["name"] for e in crit} <= labels

    # clearing the rating restores counts, rows and pane at once (the pane
    # is still boss-narrowed, so release the pick for the whole family pool)
    p._items_rating_chips["Critical"].setChecked(False)
    assert p._items_rating == ""
    assert dv._chips["Manfish"].toolTip().startswith(f"{full} ")
    row = dv._rows[("Manfish", "Crabgantua")]
    row_labels = {lbl.text() for lbl in row.findChildren(QtWidgets.QLabel)}
    assert "Crabgantua's Kneecap" in row_labels
    dv.toggle("Manfish", "Crabgantua")
    assert "Crown of the Sea" in _pane_labels(p)


@_SECTION_MARK_2
def test_dungeon_tab_shows_scoped_filter_chips():
    """The Dungeons tab keeps its own removable-chip row: the active class
    and rating as clearable chips plus a Clear all scoped to the tab (the
    global one would reset the category and leave Dungeons). The Rarity
    buttons stay hidden, so the row has the full width to itself."""
    p = _page()
    support.quick_pick(p, "Dungeons")
    # nothing active: no row at all
    assert not p._items_chips_box.isVisible()

    def _chip_texts():
        return [b.text() for b in p._items_chips_flow.findChildren(
            QtWidgets.QPushButton)]

    p._items_class_chips["Fighter"].setChecked(True)
    p._items_rating_chips["Critical"].setChecked(True)
    assert p._items_chips_box.isVisible()
    assert not p._items_rarity_toggle_box.isVisible()
    assert any(t.startswith("CLASS ·") for t in _chip_texts())
    assert any(t.startswith("RATING ·") for t in _chip_texts())
    assert "Clear all" in _chip_texts()

    # the CLASS chip clears just the class — and stays on Dungeons
    cls_btn = next(b for b in p._items_chips_flow.findChildren(
        QtWidgets.QPushButton) if b.text().startswith("CLASS ·"))
    cls_btn.click()
    assert p._items_class == "" and p._items_category == "@dungeon"
    assert not any(t.startswith("CLASS ·") for t in _chip_texts())

    # Clear all drops the rating too, still without leaving the tab, and
    # the row hides itself once nothing is left to clear
    clear = next(b for b in p._items_chips_flow.findChildren(
        QtWidgets.QPushButton) if b.text() == "Clear all")
    clear.click()
    assert p._items_rating == "" and p._items_category == "@dungeon"
    assert not p._items_chips_box.isVisible()


@_SECTION_MARK_2
def test_all_dungeons_row_releases_a_boss_pick():
    """The "Kobold Dungeons" row tops the boss list, so the list always shows
    one lit entry — the faction for the whole family pool, or exactly one
    boss for its own drops. Clicking it releases a boss pick back to the
    whole-faction pane."""
    p = _page()
    support.quick_pick(p, "Dungeons")
    dv = p._items_dungeon_view
    dv._pick("Kobold")
    all_row = dv._all_row
    assert all_row is not None
    texts = {lbl.text() for lbl in all_row.findChildren(QtWidgets.QLabel)}
    assert "Kobold Dungeons" in texts
    assert any(t.endswith("pieces") for t in texts)
    lit = lambda w: theme.GOLD in w.styleSheet()
    # no pick: the faction row lit, no boss row lit, shared pool in the pane
    assert lit(all_row)
    assert not any(lit(r) for r in dv._rows.values())
    g = dv._groups["Kobold"]
    shared = {idata.item(i)["name"] for i in g["shared"]}
    assert shared <= _pane_labels(p)
    # pick a boss: the faction row goes dark, that row lights, and the pane
    # drops the shared pool — this boss's weapons + Epic only
    dv.toggle("Kobold", "Reblochonk")
    assert not lit(all_row)
    assert lit(dv._rows[("Kobold", "Reblochonk")])
    assert p._dg_rendered_boss == "Reblochonk"
    labels = _pane_labels(p)
    assert not (shared & labels)
    assert any("SIGNATURE WEAPONS" in t for t in labels)
    # clicking the faction row releases back to the whole family: both Epic
    # blocks, the family pool under its "drops in:" header, no weapons block
    class _Ev:
        accepted = False

        def accept(self):
            self.accepted = True

    ev = _Ev()
    all_row.mouseReleaseEvent(ev)
    assert ev.accepted
    assert dv._sel == ("Kobold", "") and p._dg_rendered_boss == ""
    assert lit(all_row)
    assert not any(lit(r) for r in dv._rows.values())
    labels = _pane_labels(p)
    for b in g["bosses"]:
        want = {idata.item(i)["name"] for i in b["epic"]}
        assert not want or want <= labels
    assert not any("SIGNATURE WEAPONS" in t for t in labels)
    assert shared <= labels
    assert any("drops in:" in t for t in labels)


@_SECTION_MARK_2
def test_dungeon_rows_pick_their_boss():
    """Every dungeon line carries its boss's icon and is a plain text row
    (no block fill) in a 2-column grid, and the list always shows — faction
    view and boss view alike. Clicking one picks its boss — the same single
    logic as the left-column rows — so the pane just shows that boss's
    drops; the SHARED SET header jumps back to the full list."""
    p = _page()
    support.quick_pick(p, "Dungeons")
    dv = p._items_dungeon_view
    dv._pick("Kobold")
    g = dv._groups["Kobold"]
    dungeons = {b["dungeon"]: b["boss_id"] for b in g["bosses"]
                if b.get("dungeon")}
    assert dungeons

    def _buttons(p):
        return [w for w in p._items_detail.findChildren(QtWidgets.QPushButton)
                if w.text() in dungeons]

    def _dungeon_grid(p):
        for l in p._items_detail.findChildren(QtWidgets.QGridLayout):
            btns = [l.itemAt(i).widget() for i in range(l.count())]
            btns = [w for w in btns
                    if isinstance(w, QtWidgets.QPushButton)
                    and w.text() in dungeons]
            if btns:
                return l, btns
        raise AssertionError("no dungeon grid in the pane")

    # one pick link per dungeon, each wearing its boss's icon, none of them
    # a filled block, in 2 columns — not one stacked line each
    btns = _buttons(p)
    assert {b.text() for b in btns} == set(dungeons)
    assert all(not b.icon().isNull() for b in btns)
    assert all("rgba" not in b.styleSheet() for b in btns)
    grid, gbtns = _dungeon_grid(p)
    assert grid.columnCount() == 2
    assert {grid.getItemPosition(grid.indexOf(b))[1]
            for b in gbtns} == {0, 1}

    # clicking Reblochonk's dungeon picks it: its Epic set plus its mirrored
    # weapons, no shared pool — exactly like its left-column row
    by_boss = {b["boss_id"]: b for b in g["bosses"]}
    reb = {idata.item(i)["name"] for i in by_boss["Reblochonk"]["epic"]}
    rat = {idata.item(i)["name"] for i in by_boss["Ratsar"]["epic"]}
    shared = {idata.item(i)["name"] for i in g["shared"]}
    target = next(b for b in btns if dungeons[b.text()] == "Reblochonk")
    target.click()
    assert dv._sel == ("Kobold", "Reblochonk")
    assert p._dg_rendered_boss == "Reblochonk"
    labels = _pane_labels(p)
    assert reb <= labels and not (rat & labels)
    assert not (shared & labels)

    # the list stays up in the boss view too, header and all: another
    # dungeon switches the pick, the current one releases back to all
    btns = _buttons(p)
    assert {b.text() for b in btns} == set(dungeons)
    assert any("drops in:" in t for t in _pane_labels(p))
    grid, gbtns = _dungeon_grid(p)
    assert grid.columnCount() == 2
    other = next(b for b in btns if dungeons[b.text()] == "Ratsar")
    other.click()
    assert dv._sel == ("Kobold", "Ratsar")
    assert p._dg_rendered_boss == "Ratsar"
    btns = _buttons(p)
    current = next(b for b in btns if dungeons[b.text()] == "Ratsar")
    current.click()
    assert dv._sel == ("Kobold", "") and p._dg_rendered_boss == ""
    assert shared <= _pane_labels(p)

    # Bee has no Epic blocks at all: its dungeon lines pick the boss all the
    # same, exactly like clicking the left-column row
    dv._pick("Bee")
    g = dv._groups["Bee"]
    btn = next(w for w in p._items_detail.findChildren(QtWidgets.QPushButton)
               if w.text() == g["bosses"][0]["dungeon"])
    btn.click()
    assert dv._sel == ("Bee", g["bosses"][0]["boss_id"])
    assert p._dg_rendered_boss == g["bosses"][0]["boss_id"]


@_SECTION_MARK_2
def test_shared_header_opens_the_full_shared_list():
    """The SHARED SET header jumps to the full shared list (the faction
    view): in the boss view the tile grid is hidden, so the header is how
    its pieces are reached. It reads clickable (pointer + hover)."""
    from PySide6 import QtCore

    p = _page()
    support.quick_pick(p, "Dungeons")
    dv = p._items_dungeon_view
    dv._pick("Kobold")
    g = dv._groups["Kobold"]
    shared = {idata.item(i)["name"] for i in g["shared"]}
    dv.toggle("Kobold", "Reblochonk")
    assert not (shared & _pane_labels(p))
    hdr = next(lbl for lbl in p._items_detail.findChildren(QtWidgets.QLabel)
               if "SHARED SET" in lbl.text())
    assert hdr.cursor().shape() == QtCore.Qt.PointingHandCursor
    assert ":hover" in hdr.styleSheet()

    class _Ev:
        accepted = False

        def accept(self):
            self.accepted = True

    ev = _Ev()
    hdr.mouseReleaseEvent(ev)
    assert ev.accepted
    assert dv._sel == ("Kobold", "") and p._dg_rendered_boss == ""
    assert shared <= _pane_labels(p)


@_SECTION_MARK_2
@_SECTION_MARK_2
def test_nightling_vendor_gear_is_not_called_a_shared_set():
    """A family that BUYS its Rares has no shared set, and its dungeons are
    not where they come from, so the two must not be welded into one block
    labelled "SHARED SET · drops in:" over the boss list. Nightling gets a
    standalone VENDOR GEAR block naming Mira and the price, and Mira herself
    stands as a boss-style row (sprite, gold name, her boxes as chips) over
    the bare dungeon list — no "DUNGEONS" header speaking for bosses that
    drop no Epic."""
    p = _page()
    support.quick_pick(p, "Dungeons")
    dv = p._items_dungeon_view
    dv._pick("Demon")
    labels = _pane_labels(p)
    assert not any("SHARED SET" in t for t in labels), labels
    hdr = next(t for t in labels if "VENDOR GEAR" in t)
    assert "Mira, Demon Huntress" in hdr
    assert "10 Nightblood each" in hdr
    # …and the boxes are NAMED (2026-09-27). The header used to stop at "25
    # pieces · sold by Mira", which claimed 25 pieces from a source that never
    # said which boxes they come out of — and which box you buy is the whole
    # point, since each rolls its own table at your level.
    assert "3 boxes" in hdr
    for box in ("Chaotic Gear Cache", "Chaotic Weapon Cache",
                "Heroic Chaotic Gear Cache"):
        assert any(box in t for t in labels), box
    assert not any("drops in:" in t for t in labels), labels
    # no DUNGEONS header: Mira's boss-style row stands over the bare dungeon
    # list instead, and her row carries her exact name like a boss row does
    assert not any(t.startswith("⚔ DUNGEONS") for t in labels), labels
    assert any(t == "Mira, Demon Huntress" for t in labels), labels
    btns = {b.text() for b in p._items_detail.findChildren(QtWidgets.QPushButton)}
    assert any("Rift Nightking" in t for t in btns), btns
    # the header counts only what a boss in this family's dungeons drops, and
    # neither Nightling boss drops an Epic — the cache is not a dungeon
    assert any("0 with Epic drops" in t for t in labels), labels
    assert not any("Chaotic" in t and "EPIC" in t for t in labels), labels

    # a drop family is untouched: still one SHARED SET block, still leading
    # into the dungeon list, no vendor wording
    for fac in ("Bee", "Kobold", "Manfish", "Crimson"):
        dv._pick(fac)
        labels = _pane_labels(p)
        assert any("SHARED SET" in t and "drops in:" in t
                   for t in labels), fac
        assert not any("VENDOR GEAR" in t or "boxes" in t
                       for t in labels), fac
        assert not any(t.startswith("⚔ DUNGEONS") for t in labels), fac


def test_cache_pieces_are_not_drawn_on_the_dungeons_page():
    """A Heroic CACHE is not a dungeon and nothing in the game data hands one
    out, so its pieces have no drop location to be filed under. The Dungeons
    page used to draw them as blocks anyway (the Nightling's whole 25-piece
    set, and the other four families' six apiece), which asserted a location
    that does not exist. They are `epic_unknown` again and stay off this
    page; the gear list and the Collection Manager serve them.

    Same for the six CRIMSON pieces `Golcano_HM` rolls: he is a Kobold boss,
    so putting them on the Crimson page linked a Kobold dungeon to Crimson
    gear."""
    from farever_companion.data import items as idata
    groups = {g["faction"]: g for g in idata.dungeon_drop_groups()}
    dem = groups["Demon"]
    assert all(not b["epic"] for b in dem["bosses"])
    assert len(dem["epic_unknown"]) == 24, "the whole set, no boss drops it"
    for fac in ("Bee", "Kobold", "Manfish", "Crimson"):
        assert "epic_cache" not in groups[fac] and "epic_cross" not in \
            groups[fac], fac
        assert len(groups[fac]["epic_unknown"]) >= 6, fac

    p = _page()
    support.quick_pick(p, "Dungeons")
    dv = p._items_dungeon_view
    # no family may show a piece it has no dungeon for
    for fac, g in groups.items():
        dv._pick(fac)
        labels = _pane_labels(p)
        stray = {idata.item(i)["name"] for i in g["epic_unknown"]} & labels
        assert not stray, (fac, sorted(stray))
        assert not any("· EPIC ·" in t and "Cache" in t for t in labels), fac


def test_items_tab_has_an_unreleased_filter_for_the_unreachable_boxes():
    """The boxes nothing hands out live under Misc with everything else, so
    there was no way to see authored-but-unreachable content without opening
    each box's page. The Items tab's single-select category row gains an
    Unreleased pseudo-category listing exactly them."""
    from farever_companion.data import items as idata
    from farever_companion.ui.pages.items.support import ID_ROLE
    want = {b["cache"] for b in idata.unreleased_caches()}
    assert want, "no unreleased boxes — the chip would not be built"
    # ONE box: the Epic Demon set. The two Hero caches Shiro James sells were
    # mislabelled here until his open shop was read directly (2026-09-26) —
    # the item sheet has no row for either, so absence alone said "unreleased".
    assert want == {"HM_Demon_Cache_25"}

    p = _page()
    box = p._page_all_items_view()          # keep the widget tree alive
    p._box_ref = box
    assert "@unreleased" in p._items_allcat_chips
    assert "1" in p._items_allcat_chips["@unreleased"].text()

    lw = p._items_all_list
    p._items_toggle_allcat("@unreleased", True)
    p._items_all_refilter()
    vis = {lw.item(i).data(ID_ROLE) for i in range(lw.count())
           if not lw.item(i).isHidden() and lw.item(i).data(ID_ROLE)}
    assert vis == want, sorted(vis ^ want)

    # and it is a real filter, not a dead chip: All still shows the whole pool
    p._items_toggle_allcat("@items", True)
    p._items_all_refilter()
    allv = {lw.item(i).data(ID_ROLE) for i in range(lw.count())
            if not lw.item(i).isHidden() and lw.item(i).data(ID_ROLE)}
    assert len(allv) > len(vis)
    assert want <= allv, sorted(want - allv)


def test_nightling_lists_mira_as_her_own_vendor_line_not_a_boss_drop():
    """A box is not a boss drop. Mira sells hers for Nightblood and no boss
    hands them out, so her caches sat on the BOSS rows and the app claimed a
    kill drops them. The vendor gets her own line under the bosses; the boss
    rows carry only what the boss actually drops."""
    from farever_companion.data import items as idata
    boxes = idata.faction_caches("Demon")
    # three: the two Rare gear boxes Mira sells, plus the unreleased Epic one.
    # The gift box is NOT here — it is an augment roll, not gear, and the
    # user does not want it in a drop list. There is no Nightling epic WEAPON
    # box; HM_Weapon_Cache_25's contents are faction "World", not Demon.
    assert [b["rarity"] for b in boxes] == ["Rare", "Rare", "Epic"]
    assert [b["cache"] for b in boxes] == [
        "Rift_Gear_Cache", "Rift_Weapon_Cache", "HM_Demon_Cache_25"]
    assert "Chaotic Gift Cache" not in [b["name"] for b in boxes]
    assert {b["cache"] for b in boxes if b["unreleased"]} == {
        "HM_Demon_Cache_25"}
    # the two sold ones name her; the unreleased one names no vendor at all
    assert {b["vendor"] for b in boxes if not b["unreleased"]} == {
        "Mira, Demon Huntress"}
    assert [b["vendor"] for b in boxes if b["cache"] == "HM_Demon_Cache_25"] \
        == [""]
    # her prices are still recoverable from the loot-table attribution
    stock = idata.vendor_caches("Mira, Demon Huntress")
    assert stock[0]["cost"] == (("Nightblood", 10),)
    assert stock[2]["cost"] == (("Nightblood", 5),)
    # Shiro James is a different vendor entirely — the Medal of Glory trader
    # in the hubs, recorded from his open shop on 2026-09-26. He sells the two
    # Hero caches at 100 medals each, which is why the Nightling's vendor line
    # must not acquire them and why neither reads as unreleased.
    shiro = idata.vendor_caches("Shiro James")
    assert [b["cache"] for b in shiro] == ["HM_Gear_Cache_25",
                                           "HM_Weapon_Cache_25"]
    assert {b["cost"] for b in shiro} == {(("BadgeOfGlory", 100),)}

    p = _page()
    support.quick_pick(p, "Dungeons")
    dv = p._items_dungeon_view
    dv._pick("Demon")

    # the boss rows carry weapons only — no box may appear on a drop list
    for boss in ("Nightking Maat Demon", "Nightqueen Shaarlize Te'ror"):
        row = _boss_row_labels(dv, boss)
        assert row, boss
        assert not any("Chaotic" in x for x in row), (boss, row)
        assert "UNRELEASED" not in row, (boss, row)

    # and the vendor is a line of her own, carrying her three
    vrow = _boss_row_labels(dv, "Mira, Demon Huntress")
    assert vrow, "no vendor line for the Nightling"
    for nm in ("Chaotic Gear Cache", "Chaotic Weapon Cache",
               "Heroic Chaotic Gear Cache"):
        assert nm in vrow, (nm, vrow)
    assert "Chaotic Gift Cache" not in vrow, vrow
    # Every chip on the line names a DIFFERENT box. Two of her three share
    # the sheet name "Chaotic Gear Cache", which used to render as two chips
    # reading identically — different tier, different level model, different
    # contents, same words. The Epic one is listed as the Heroic box, matching
    # its siblings' "Hero *" prefix, so the line can be read at a glance.
    chips = [t for t in vrow if t.endswith("Cache")]
    assert len(chips) == len(set(chips)) == 3, chips
    # no UNRELEASED tag on the vendor line: a list that is only meant to say
    # "she sells these" does not need one chip carrying that word. The
    # distinction lives in the chip's tooltip and on the box's own pane.
    assert "UNRELEASED" not in vrow, vrow

    # a family with no vendor-sold stock gets no empty vendor line. Assert on
    # the view's own registry, not findChildren: the Nightling's row is only
    # deleteLater'd on the switch, and headless Qt does not deliver that
    # without qt_helpers.drain_deleted(), so it is still findable. The
    # registry holds the CURRENT faction's vendor rows only, so Bee — which
    # owns no vendor-sold box — leaves it empty.
    assert "Demon" in dv._vendor_rows
    dv._pick("Bee")
    assert not dv._vendor_rows
    dv._pick("Demon")          # back to the Nightling for the pane checks

    # the "buy the box, not the piece" banner stays gone — it was noise
    assert not any("STOCK" in t for t in _pane_labels(p))
    # ...and there is no pane-level EPIC block for the unreleased box at all.
    # It used to be drawn, marked UNRELEASED so it would not claim a heroic
    # kill. That was half a fix: a box nothing hands out is not a dungeon,
    # so listing its 25 pieces here filed gear against a location that does
    # not exist. The vendor line above is where a box belongs — it is what
    # tells the player where (if anywhere) the thing can be obtained.
    assert not any("EPIC" in t and "Chaotic Gear Cache" in t
                   for t in _pane_labels(p)), _pane_labels(p)
    # the pieces themselves are still reachable, from the gear list
    from farever_companion.data import items as _idata
    dem = next(g for g in _idata.dungeon_drop_groups()
               if g["faction"] == "Demon")
    assert len(dem["epic_unknown"]) == 24
    assert "Trinket_Demon_E" not in idata.epic_pieces(dem)


def test_a_box_split_evenly_across_families_is_credited_to_none():
    """`HM_Gear_Cache_25` holds 6 Bee / 6 Kobold / 6 Manfish / 6 Crimson, so no
    family owns it. Crediting it to whichever faction happened to sort first
    handed the shared Hero-mode box to one arbitrary family and implied that
    family's boss drops it — and the app did exactly that, on the Bee row.
    A tied box belongs to nobody's boss row."""
    from farever_companion.data import items as idata
    for fac in ("Bee", "Kobold", "Manfish", "Crimson"):
        assert idata.faction_caches(fac) == (), fac
    # the Nightling is unaffected — it owns its boxes outright (3, with the
    # gift box excluded: an augment roll is not gear)
    assert len(idata.faction_caches("Demon")) == 3

    p = _page()
    support.quick_pick(p, "Dungeons")
    dv = p._items_dungeon_view
    for fac in ("Bee", "Kobold", "Manfish", "Crimson"):
        dv._pick(fac)
        groups = {g["faction"]: g for g in idata.dungeon_drop_groups()}
        for b in groups[fac]["bosses"]:
            row = _boss_row_labels(dv, b["boss"])
            assert "Hero Gear Cache" not in row, (fac, b["boss"], row)
            assert "Chaotic" not in " ".join(row), (fac, b["boss"], row)


def test_clicking_a_cache_opens_its_contents_in_the_pane():
    """A box is not a statless container to page through — clicking one in the
    vendor line narrows the pane to what is inside, the same pick gesture a
    boss row makes,    in the same locked tile grid."""
    p = _page()
    support.quick_pick(p, "Dungeons")
    dv = p._items_dungeon_view
    dv._pick("Demon")

    dv.select_box("Demon", "Rift_Weapon_Cache")
    assert p._dg_pane_state == "box"
    assert p._dg_rendered_box == "Rift_Weapon_Cache"
    labs = _pane_labels(p)
    assert "Chaotic Weapon Cache" in labs
    assert any("4 pieces" in t for t in labs), labs[:6]
    assert any("YOUR level" in t for t in labs), labs[:6]

    # the unreleased box says so on its own pane too, never claiming a drop
    dv.select_box("Demon", "HM_Demon_Cache_25")
    labs = _pane_labels(p)
    assert p._dg_rendered_box == "HM_Demon_Cache_25"
    assert any("UNRELEASED" in t for t in labs), labs[:6]
    assert not any("heroic mode" in t for t in labs), labs[:6]

    # clicking the same box again releases back to the faction view
    dv.select_box("Demon", "HM_Demon_Cache_25")
    assert p._dg_pane_state == "boss"
    assert p._dg_rendered_box == ""


def _boss_row_labels(dv, boss_name: str) -> list[str]:
    """The label texts of the list row named `boss_name`. The name is the
    first label that is not a leading emoji (the vendor row carries a shop
    glyph before Mira's name, the boss rows carry none)."""
    for row in dv.findChildren(QtWidgets.QFrame):
        labs = [c.text() for c in row.findChildren(QtWidgets.QLabel)
                if c.text()]
        named = [t for t in labs if not (len(t) <= 2 and t.isascii() is False)]
        if named and named[0] == boss_name:
            return labs
    return []


@_SECTION_MARK_2
def test_collection_manager_lists_both_halves_of_every_dungeon_set():
    """The Loadout Collection Manager carries the Epic twins as well as the
    shared Rare set (the heroic boss drops plus the pieces no drop row
    resolves), every slot the sets use gets a section — Back used to be
    dropped — with one card per piece, so both halves are reachable. The
    weapons are listed too, as their own slot: no dungeon set owns them, so
    they come from the catalog, but the collection is where you go to see
    what you can equip."""
    p = _page()
    holder = QtWidgets.QWidget()          # parentless holder keeps it alive
    lay = QtWidgets.QVBoxLayout(holder)
    p._page_loadout(lay)
    # the manager now opens pre-narrowed (Warrior + Head by default); clear
    # every filter to census the whole shelf
    p._collection_class_filter = ""
    p._coll_slot_filter = ""
    p._coll_rarity_filter = ""
    p._loadout_set_mode("collection")
    app = QtWidgets.QApplication.instance()
    for _ in range(40):
        app.processEvents()

    labels = [lbl.text() for lbl in holder.findChildren(QtWidgets.QLabel)
              if lbl.text()]
    groups = list(idata.dungeon_drop_groups())
    rare = {i for g in groups for i in g["shared"]
            if idata.item_display_rarity(i) == "Rare"}
    epic = {i for g in groups
            for i in (list(g["shared"]) + list(idata.epic_pieces(g)))
            if idata.item_display_rarity(i) == "Epic"}
    # the weapons ride along as their own slot: they belong to no dungeon
    # set, so they are collected from the catalog instead of the drop
    # groups, and every one of them gets a card like any other piece
    weapons = {it["id"] for it in idata.items()
               if idata.category(it.get("type")) == "Weapons"
               and (it.get("level") or 0)
               and idata.item_display_rarity(it["id"]) not in ("Common", "Uncommon")}
    # one card per piece — the two halves are both listed, each with its own
    # Owned/Add button (the Epic and Rare twins are separate item ids)
    tiles = [b.text() for b in holder.findChildren(QtWidgets.QPushButton)
             if b.text() in ("✓ Owned", "+ Add")]
    assert len(tiles) == len(rare) + len(epic) + len(weapons)
    assert weapons, "the catalog has no weapons to list"
    # 121 Rare = 24 per launch faction (96) + Nightling's 25 (its Rare set
    # rides the rift affinity table, not a per-faction dungeon table).
    # 124 Epic = the 24-piece set in each of the five families. The Epic
    # TRINKET each `_HM` table also rolls is not one of the 24 (no
    # `_E<Family>_` marker) and has no block of its own.
    assert len(rare) == 121 and len(epic) == 124

    sections = {t.split(" ", 1)[1] for t in labels
                if t.startswith(("🪖 ", "🛡️ ", "🥋 ", "🥊 ", "🎗️ ", "👖 ",
                                 "🥾 ", "🧣 ", "🧿 "))}
    assert {"HEAD", "SHOULDERS", "CHEST", "HANDS", "WAIST", "LEGS", "FEET",
            "BACK", "TRINKETS"} <= sections
    assert "Deluxe Replacement Wings" in labels      # an Epic Back piece
    assert "Flight of the Rumblebee" in labels       # its Rare twin


@_SECTION_MARK_2
def test_totals_block_leads_with_the_builds_primary_stat():
    """The Loadout Totals panel is a stat sheet, not a dict dump.

    It used to open with a green "Build Synergy 95.2% ★" box — a hardcoded
    string that never changed — and then list `loadout_stat_totals` in
    whatever order the dict came out in, so a Fighter's Strength sat second
    behind Dexterity and Magic Penetration closed the list. The panel also
    stacked three bordered buttons on top of the card's own border, and the
    rows were free to grow (a 15px line rendered 29px tall).
    """
    p = _page()
    holder = QtWidgets.QWidget()
    p._page_loadout(QtWidgets.QVBoxLayout(holder))
    p._loadout_toggle_class("Fighter", True)
    app = QtWidgets.QApplication.instance()
    for _ in range(6):
        app.processEvents()

    rows = []
    for i in range(p._totals_container.count()):
        w = p._totals_container.itemAt(i).widget()
        if w is None or w.layout() is None:
            continue
        lay = w.layout()
        texts = [lay.itemAt(j).widget().text()
                 for j in range(lay.count())
                 if lay.itemAt(j).widget() is not None]
        rows.append((w, texts))

    # the scaling stat leads, in the panel's own emphasis treatment
    assert rows[0][1][0] == "STRENGTH", rows[0][1]
    assert rows[0][0].height() > rows[1][0].height()
    # then the optimizer's fixed order, with the build's own leftover off-stats
    # after it. WHICH off-stat is left over is a consequence of the piece the
    # class chip happens to auto-fill (it was Dexterity off Twin Pillars, and
    # is Faith off the Amon Ram the kit fit now picks), so pinning a name here
    # would pin a weapon choice, not the panel. What is the panel's contract:
    # the six fixed rows in order, no duplicate of them in the tail, and one
    # row per distinct stat the build actually totals.
    assert [texts[0] for _w, texts in rows[1:7]] == [
        "Critical", "Fervor", "Armor Penetration", "Magic Penetration",
        "Armor", "Vitality"], [texts[0] for _w, texts in rows[1:]]
    fixed = {"Critical", "Fervor", "Armor Penetration", "Magic Penetration",
             "Armor", "Vitality"}
    tail = [texts[0] for _w, texts in rows[7:]]
    assert not set(tail) & fixed, tail
    totals = idata.loadout_stat_totals(
        p._loadout_w1, p._loadout_w2, p._loadout_ars, p._loadout_slots,
        level=25, upgrades=p._loadout_upgrades)
    # the lead row is uppercased for emphasis, so compare case-folded
    assert {t.upper() for t in (texts[0] for _w, texts in rows)} == {
        k.upper() for k in totals}, (
            [texts[0] for _w, texts in rows], sorted(totals))
    # a stat row is a fixed single line, so leftover column height cannot
    # pad it (that is what made the old block look loose). 20px carries the
    # page's 13px type.
    assert all(w.height() == 20 for w, _ in rows[1:])

    # and it is transparent: a bare QWidget picks up the app stylesheet's
    # `QWidget { background: SURFACE }`, which painted an opaque rect over
    # the card's PANEL and gave every stat its own dark block.
    for w, _ in rows:
        assert "background: transparent" in (w.styleSheet() or ""), w

    # the dead mockup box is gone, and the two copies are borderless links
    assert not [lbl for lbl in holder.findChildren(QtWidgets.QLabel)
                if "95.2" in lbl.text() or "Build Synergy" in lbl.text()]
    links = [b for b in holder.findChildren(QtWidgets.QPushButton)
             if "Copy" in b.text()]
    assert len(links) == 2
    for b in links:
        assert "border: 1px" not in (b.styleSheet() or ""), b.text()


@_SECTION_MARK_2
def test_loadout_page_body_type_runs_two_pixels_larger():
    """The Loadout tab is dense 11px type, so the page carries its own
    two-pixel bump for everything that does not set a size itself — the mode
    buttons, the class chips, and SectionHeader's objectName-styled labels
    (which the app stylesheet pins at 11px). Sized widgets keep their own,
    now larger, values; the item picker is a top-level dialog and stays out.
    """
    p = _page()
    holder = QtWidgets.QWidget()
    p._page_loadout(QtWidgets.QVBoxLayout(holder))
    sheet = holder.findChildren(QtWidgets.QWidget)
    page = next(w for w in sheet if "QLabel#Section" in (w.styleSheet() or ""))
    assert "font-size: 16px" in page.styleSheet()
    assert "QLabel#Section, QLabel#Mono, QLabel#FieldLabel { font-size: 13px; }" \
        in page.styleSheet()


@_SECTION_MARK_2
def test_class_chips_sit_right_of_the_collection_manager_button():
    """The Loadout class chips live on the mode bar, right of the 📦 Collection
    button, on the same line — they used to sit under a bare "Equipment
    Slots" title, which cost the gear list a row.

    That title is GONE now (the slot labels say what they are), so the gear
    column carries no header at all. The Collection header is still there and
    is still pinned to its own width: a `SectionHeader` carries an internal
    stretch, so one free to grow swallows its row (the Collection header used
    to shove its own chips to the far edge).
    """
    app = QtWidgets.QApplication.instance()
    p = _page()
    holder = QtWidgets.QWidget()
    p._page_loadout(QtWidgets.QVBoxLayout(holder))
    holder.resize(1200, 820)
    holder.show()
    for _ in range(6):
        app.processEvents()

    def _header(text, absent=False):
        hits = [lbl.parentWidget()
                for lbl in holder.findChildren(QtWidgets.QLabel, "Section")
                if lbl.text() == text]
        if absent:
            assert not hits, f"{text!r} header is still on the page"
            return None
        assert len(hits) == 1, f"expected one {text!r} header, got {len(hits)}"
        return hits[0]

    coll_btn = p._loadout_coll_btn
    chips = p._cgroup
    assert chips.parentWidget() is coll_btn.parentWidget(), "not on the mode bar"
    assert chips.x() > coll_btn.x() + coll_btn.width(), "chips are not right of it"
    assert abs(chips.y() - coll_btn.y()) <= 8, \
        f"chips are on another line (y {chips.y()} vs {coll_btn.y()})"

    # the gear column has no section title left at all
    assert _header("EQUIPMENT SLOTS", absent=True) is None
    p._loadout_set_mode("collection")
    for _ in range(6):
        app.processEvents()
    assert not chips.isVisible(), "collection mode shows two sets of chips"
    coll = _header("COLLECTION OWNED")
    assert coll.sizePolicy().horizontalPolicy() == QtWidgets.QSizePolicy.Fixed, \
        "the Collection header swallows the row, pushing its chips right"


@_SECTION_MARK_2
def test_auto_fill_keeps_every_slot_the_optimizer_filled():
    """Picking a class must leave all 11 resolved slots on screen.

    The dungeon sets carry NO `level` field, and the slot-row prune treated
    "no level" as junk (its test was `Common, or level 0`), so it deleted 7
    of the 11 slots the optimizer had just filled — the tab showed 'Select
    Gear…' for Head/Shoulders/Chest/… and the totals, the farm list and the
    copied loadout all silently lost them.
    """
    p = _page()
    holder = QtWidgets.QWidget()
    p._page_loadout(QtWidgets.QVBoxLayout(holder))
    p._loadout_toggle_class("Fighter", True)

    filled = idata.optimize_loadout_build(
        weapon1_id=p._loadout_w1, weapon2_id=p._loadout_w2,
        class_filter="Fighter", level=25)
    assert len(filled) == 11
    for label, iid in filled.items():
        assert p._loadout_slots.get(label) == iid, label
    assert len(p._loadout_slots) == 11

    # the level-less dungeon pieces are exactly what used to vanish
    picks = [idata.item(i) or {} for i in p._loadout_slots.values()]
    assert any(it.get("level") is None for it in picks)
    # …and every row really renders its piece, none is back to the empty
    # state (drain the rebuilt rows first: processEvents alone never delivers
    # their deleteLater, so pruned rows linger and look alive)
    from tests.qt_helpers import drain_deleted
    drain_deleted()
    assert not [lbl for lbl in holder.findChildren(QtWidgets.QLabel)
                if lbl.text() == "Select Gear…"]
    names = [lbl.text() for lbl in holder.findChildren(QtWidgets.QLabel)
             if lbl.text()]
    for iid in p._loadout_slots.values():
        assert (idata.item(iid) or {}).get("name") in names, iid


@_SECTION_MARK_2
def test_weapon_slots_are_never_the_same_weapon():
    """One weapon lives in one hand, the Arsenal included.

    Auto-fill has to exclude the other two slots (a manual Arsenal could
    otherwise be re-picked as a main hand), and a manual pick of a weapon
    another slot holds has to MOVE it rather than clone it — a duplicate was
    both impossible in game and wrong in the totals, which counted the same
    piece at 100% and 50%.
    """
    from farever_companion.ui.pages.items.loadout_weapons import is_2h_weapon

    for cls in idata.gear_classes():
        p = _page()
        holder = QtWidgets.QWidget()
        p._page_loadout(QtWidgets.QVBoxLayout(holder))
        p._loadout_toggle_class(cls, True)
        trio = [i for i in (p._loadout_w1, p._loadout_w2, p._loadout_ars) if i]
        assert len(set(trio)) == len(trio), f"{cls} filled a slot twice: {trio}"
        # a 2h main hand leaves the off-hand empty on purpose
        if p._loadout_w2:
            assert not is_2h_weapon(p._loadout_w1)

    p = _page()
    holder = QtWidgets.QWidget()
    p._page_loadout(QtWidgets.QVBoxLayout(holder))
    w1 = idata.suggest_main_weapon(class_filter="Fighter", level=25)
    w2 = idata.suggest_main_weapon(exclude_ids=(w1,), class_filter="Fighter",
                                   level=25)
    p._on_weapon_picked(1, w1)
    p._on_weapon_picked(2, w2)
    # picking Slot 1's weapon for the Arsenal swaps: the arsenal's old weapon
    # (none) goes back to Slot 1, and Slot 1 does NOT keep a copy
    p._on_weapon_picked(3, w1)
    assert p._loadout_ars == w1
    assert p._loadout_w1 != w1
    filled = [i for i in (p._loadout_w1, p._loadout_w2, p._loadout_ars) if i]
    assert len(set(filled)) == len(filled)
    # …and the totals count the moved weapon once, at the arsenal's 50%
    assert idata.loadout_stat_totals(p._loadout_w1, p._loadout_w2,
                                     p._loadout_ars, {}, level=25) == \
        idata.loadout_stat_totals(None, w2, w1, {}, level=25)


@_SECTION_MARK_2
def test_picker_marks_weapons_equipped_in_another_slot():
    """The rows that would MOVE a weapon stay listed but say so, so a swap is
    never a surprise (and the picker never offers a duplicate silently)."""
    from PySide6 import QtCore
    from farever_companion.ui.pages.items.loadout_weapons import _ItemPickerDialog

    _page()                                   # a QApplication is required
    w1 = idata.suggest_main_weapon(class_filter="Fighter", level=25)
    w2 = idata.suggest_main_weapon(exclude_ids=(w1,), class_filter="Fighter",
                                   level=25)
    dlg = _ItemPickerDialog(category="Weapons", slot_type=None, parent=None,
                            elsewhere_ids=(w1, w2))
    try:
        marked = [dlg._list.item(i).data(QtCore.Qt.UserRole)
                  for i in range(dlg._list.count())
                  if "[⇄ equipped]" in dlg._list.item(i).text()]
        assert set(marked) == {w1, w2}
        # the same list without the hint carries no marker
        plain = _ItemPickerDialog(category="Weapons", slot_type=None,
                                  parent=None)
        try:
            assert not [i for i in range(plain._list.count())
                        if "[⇄ equipped]" in plain._list.item(i).text()]
        finally:
            plain.deleteLater()
    finally:
        dlg.deleteLater()


@_SECTION_MARK_2
def test_weapon_picker_opens_on_the_classes_weapons():
    """Picking a weapon is a per-class decision and the Loadout tab already
    knows the class before the popout opens, so the popout opens filtered to
    it — it used to open on "All Classes" (every class's weapons) and cost a
    hand-click to narrow on every pick.

    The popout carries NO class control of its own any more: the page's chip
    bar and the popout's used to sit on screen together controlling the same
    thing, and the page's is the one that matters (it auto-fills the slots
    and decides what the gear rows keep). So the popout shows the page's
    filter and nothing else. No class picked still means no filter.
    """
    from PySide6 import QtCore
    from farever_companion.ui.pages.items.loadout_weapons import _ItemPickerDialog

    _page()                                   # a QApplication is required

    dlg = _ItemPickerDialog(category="Weapons", slot_type=None, parent=None,
                            class_filter="Fighter")
    try:
        assert dlg._class_filter == "Fighter"
        listed = [dlg._list.item(i).data(QtCore.Qt.UserRole)
                  for i in range(dlg._list.count())]
        assert listed, "the class filter left the list empty"
        for iid in listed:
            assert "Fighter" in (idata.item(iid).get("classes") or []), iid
        filtered = len(listed)
        # the popout offers no second class control to disagree with the page
        assert not dlg.findChildren(C.FilterChip)
        assert not hasattr(dlg, "_class_chips")
        assert not hasattr(dlg, "_set_class_filter")
    finally:
        dlg.deleteLater()

    # no class picked -> unchanged behaviour: every class's weapons
    plain = _ItemPickerDialog(category="Weapons", slot_type=None, parent=None)
    try:
        assert plain._class_filter == ""
        every = [plain._list.item(i).data(QtCore.Qt.UserRole)
                 for i in range(plain._list.count())]
        assert len(every) > filtered, "the class filter did not narrow"
        assert any("Fighter" not in (idata.item(i).get("classes") or [])
                   for i in every), "the unfiltered list was already Fighter-only"
    finally:
        plain.deleteLater()

    # a class the data layer does not know is ignored, not honoured — it
    # must not empty the list on a stale filter
    bogus = _ItemPickerDialog(category="Weapons", slot_type=None, parent=None,
                              class_filter="Necromancer")
    try:
        assert bogus._class_filter == ""
        assert bogus._list.count() == len(every)
    finally:
        bogus.deleteLater()

    # and the page hands its own class over: open the off-hand slot's picker
    # with a class picked and it comes up on that class.
    p = _page()
    holder = QtWidgets.QWidget()
    p._page_loadout(QtWidgets.QVBoxLayout(holder))
    p._loadout_toggle_class("Fighter", True)
    p._open_weapon_picker(2)
    try:
        assert p._active_picker_dlg._class_filter == "Fighter"
    finally:
        p._active_picker_dlg.deleteLater()
        p._active_picker_dlg = None


@_SECTION_MARK_2
def test_gear_slot_picker_opens_on_the_selected_class():
    """The gear slot picker gets the same hand-over as the weapon one: a slot
    is filled from the class the build is, and the slot rows only ever keep
    that class's pieces (see `_rebuild_slot_rows`' class prune), so opening
    on every class's list was a dead end the user had to close by hand.

    Gear with no class list at all stays listed — the picker's class rule
    only drops an item that names classes and names a different one, so
    class-agnostic pieces are never filtered away.
    """
    from PySide6 import QtCore
    from farever_companion.ui.pages.items.loadout_weapons import _ItemPickerDialog

    p = _page()
    holder = QtWidgets.QWidget()
    p._page_loadout(QtWidgets.QVBoxLayout(holder))
    p._loadout_toggle_class("Fighter", True)
    p._open_gear_picker("Head", "Head")
    try:
        dlg = p._active_picker_dlg
        assert dlg._class_filter == "Fighter"
        heads = [dlg._list.item(i).data(QtCore.Qt.UserRole)
                 for i in range(dlg._list.count())]
        assert heads, "the class filter left the Head list empty"
        assert all("Fighter" in (idata.item(i).get("classes") or [])
                   for i in heads)
    finally:
        p._active_picker_dlg.deleteLater()
        p._active_picker_dlg = None

    # no class picked -> unfiltered, as before
    p2 = _page()
    holder2 = QtWidgets.QWidget()          # kept alive: the layout dies with it
    p2._page_loadout(QtWidgets.QVBoxLayout(holder2))
    p2._open_gear_picker("Head", "Head")
    try:
        dlg2 = p2._active_picker_dlg
        assert dlg2._class_filter == ""
        assert dlg2._list.count() > len(heads)
        # a class-agnostic piece survives the filter
        agnostic = _ItemPickerDialog(category="Gear", slot_type="Head",
                                     parent=None, class_filter="Fighter")
        try:
            listed = {agnostic._list.item(i).data(QtCore.Qt.UserRole)
                      for i in range(agnostic._list.count())}
            assert not [i for i in listed
                        if not (idata.item(i).get("classes") or [])]
        finally:
            agnostic.deleteLater()
    finally:
        p2._active_picker_dlg.deleteLater()
        p2._active_picker_dlg = None


@_SECTION_MARK_2
def test_weapon_cards_read_at_max_upgrade_and_every_tier_is_clickable():
    """Weapons in the collection are shown at the rank you would actually
    equip them at, with the rank on the card.

    Picking a weapon maxes it (`_on_weapon_picked` -> `_max_out_weapon`), so
    comparing +0 numbers was comparing a state you cannot end up in. The
    rank comes from the LIVE upgrade ladder via `upgrade_cap` (Rare +3 /
    Epic +4 / Legendary +5), never a literal. Armour stays at +0: the
    loadout does not auto-rank a piece you drop into a slot.

    And all three tier chips render on the WEAPONS slot, where a tier is a
    PROJECTION rather than a filter: every weapon is display-Rare, but
    weapons carry per-rarity stats in-game, so Epic and Legendary are
    "read these at +4 / +5" and the tiles say so. (This reverses an earlier
    version of this file, which rendered all three tiers everywhere on the
    reasoning that a vanishing filter is unaskable — right for weapons,
    wrong for gear, where a Legendary chip can never match anything.)
    """
    from tests.qt_helpers import drain_deleted

    p = _page()
    holder = QtWidgets.QWidget()
    p._page_loadout(QtWidgets.QVBoxLayout(holder))
    p._collection_class_filter = ""
    p._coll_slot_filter = "Weapons"
    p._coll_rarity_filter = ""
    p._loadout_set_mode("collection")
    app = QtWidgets.QApplication.instance()
    for _ in range(80):
        app.processEvents()
    drain_deleted()

    chips = {c.text() for c in holder.findChildren(QtWidgets.QPushButton)}
    assert {"All", "Rare", "Epic", "Legendary"} <= chips, \
        "every rarity tier must be clickable even when empty"

    weapon = next(it["id"] for it in idata.items()
                  if idata.category(it.get("type")) == "Weapons")
    name = idata.item(weapon).get("name")
    rank = idata.upgrade_cap(idata.item_display_rarity(weapon))
    assert rank > 0
    labels = [lbl.text() for lbl in holder.findChildren(QtWidgets.QLabel)]
    assert f"{name}  +{rank}" in labels, f"no maxed weapon card for {name}"

    # the stat line under it is the MAXED summary, not the +0 one. The line
    # is an ElideLabel, so compare the numbers it does show rather than the
    # whole string: under a fontless headless Qt the cap cuts at a couple
    # of segments, and a full-string compare would only prove the elision.
    from farever_companion.ui.components import ElideLabel
    stat_lines = [lbl.text() for lbl in holder.findChildren(ElideLabel)]
    maxed = idata.format_item_stats_summary(weapon, level=25, upgrades=rank)
    plain = idata.format_item_stats_summary(weapon, level=25)
    if maxed != plain:
        head_maxed = maxed.split(" · ")[0]
        head_plain = plain.split(" · ")[0]
        assert any(t.startswith(head_maxed) for t in stat_lines), \
            f"no maxed stat line (looking for {head_maxed!r})"
        assert not any(t.startswith(head_plain) for t in stat_lines), \
            f"a +0 stat line is still on screen ({head_plain!r})"

    # armour is untouched: no rank suffix, +0 numbers
    p._coll_slot_filter = "Head"
    p._loadout_set_mode("collection")
    for _ in range(80):
        app.processEvents()
    drain_deleted()
    head = next(it["id"] for it in idata.items()
                if (it.get("type") or "") == "Head")
    hname = idata.item(head).get("name")
    after = [lbl.text() for lbl in holder.findChildren(QtWidgets.QLabel)]
    assert hname in after, "armour card name"
    assert not [t for t in after if t.startswith(hname + "  +")], \
        "armour must not claim an upgrade rank"


@_SECTION_MARK_2
def test_collection_scroll_cannot_loop_on_a_wrapping_stat_line():
    """The collection body must never show a horizontal scrollbar.

    The tile stat line wraps (it shows the whole summary), and a wrapping
    label's height depends on its width. With the horizontal bar on Qt's
    default `AsNeeded`, content may exceed the viewport, so: wrapping
    grows the content -> a vertical scrollbar appears -> the viewport
    narrows -> the text wraps more -> the content grows. Each pass
    re-activates the layout inside the previous one, which is a C-level
    re-entrancy cycle that ends as `Windows fatal exception: stack
    overflow` with ONE Python frame in the log, because it never enters
    the interpreter. It shipped with a by-hand QScrollArea that set only
    `setWidgetResizable(True)`; every other scroll in the app is built by
    `layout.make_scroll`, which defaults to `ScrollBarAlwaysOff`.
    """
    p = _page()
    holder = QtWidgets.QWidget()
    p._page_loadout(QtWidgets.QVBoxLayout(holder))
    p._collection_class_filter = ""
    p._coll_slot_filter = ""
    p._coll_rarity_filter = ""
    p._loadout_set_mode("collection")
    app = QtWidgets.QApplication.instance()
    for _ in range(60):
        app.processEvents()

    scrolls = [w for w in holder.findChildren(QtWidgets.QScrollArea)
               if w.widget() is not None
               and w.widget() is not getattr(p, "_items_loadout", None)]
    assert scrolls, "no collection scroll found"
    for sa in scrolls:
        assert sa.horizontalScrollBarPolicy() == QtCore.Qt.ScrollBarAlwaysOff, (
            "a wrapping stat line in an AsNeeded horizontal scroll is the "
            "layout feedback loop that overflowed the C stack")
        assert sa.widget().minimumWidth() == 0


@_SECTION_MARK_2
def test_collection_cards_show_the_whole_stat_line():
    """The stat line on a collection card is ONE capped line: elided with a
    trailing "…" when a piece carries more stats than fit, and no hover
    tooltip anywhere — the gear page is a game-companion HUD and nothing
    on it reacts to hover. It was briefly a wrapped full-summary label
    (which re-introduced the mid-segment second line the user flagged:
    "Armor Penetration 15 · | Fervor 15 · Vitality 12") and before that an
    elided label WITH the tooltip; both are superseded."""
    p = _page()
    holder = QtWidgets.QWidget()
    holder.resize(1500, 1000)
    p._page_loadout(QtWidgets.QVBoxLayout(holder))
    p._collection_class_filter = ""
    p._coll_slot_filter = ""
    p._coll_rarity_filter = ""
    p._loadout_set_mode("collection")
    holder.show()
    app = QtWidgets.QApplication.instance()
    for _ in range(120):
        app.processEvents()

    from farever_companion.ui.components import ElideLabel
    stat_labels = [lbl for lbl in holder.findChildren(ElideLabel)
                   if "·" in lbl.text()]
    assert stat_labels, "no stat lines rendered"
    # every stat line is a single elided line (or fits uncapped) — never a
    # mid-segment wrap
    assert not [lbl.text() for lbl in stat_labels if "\\n" in lbl.text()]
    # and the page carries no hover tooltips at all
    assert not [w for w in holder.findChildren(QtWidgets.QWidget)
                if w.toolTip()], "no tooltips on the gear page"


@_SECTION_MARK_2
def test_the_off_hand_takes_shields_and_nothing_else():
    """The off-hand is a shield slot. It USED to be a second main hand wearing
    a shield slot's name: the popout asked for the whole Weapons category,
    so a greatsword could be equipped there, and the auto-fill put one there
    too.

    The auto-fill needed its own data helper, not a narrowed
    `suggest_main_weapon`: that scorer carries the primary-stat gate, which
    keeps out anything that cannot roll the build's scaling stat — and every
    shield is Armor-only, so the gate rejected all four of them and the
    off-hand came up EMPTY for a class that can plainly use one.
    `suggest_offhand_shield` scores shields with the gate off (a shield has
    no scaling stat to roll) and the class filter still on.
    """
    from PySide6 import QtCore
    from farever_companion.data.items import labels as glabels
    from farever_companion.ui.pages.items.loadout_weapons import _ItemPickerDialog

    p = _page()
    holder = QtWidgets.QWidget()
    p._page_loadout(QtWidgets.QVBoxLayout(holder))

    # the popout lists shields and only shields
    p._open_weapon_picker(2)
    try:
        dlg = p._active_picker_dlg
        ids = [dlg._list.item(i).data(QtCore.Qt.UserRole)
               for i in range(dlg._list.count())]
        assert ids, "the off-hand lists nothing"
        assert all(idata.is_shield(i) for i in ids), ids
        assert len(ids) == 4, ids          # every Shield row in the catalog
    finally:
        p._active_picker_dlg.deleteLater()
        p._active_picker_dlg = None

    # a main hand is still the whole category, shields included
    main = _ItemPickerDialog(category="Weapons", slot_type=None, parent=None)
    try:
        mids = [main._list.item(i).data(QtCore.Qt.UserRole)
                for i in range(main._list.count())]
        assert len(mids) > len(ids)
        assert any(idata.is_shield(i) for i in mids)
    finally:
        main.deleteLater()

    # the auto-fill puts a shield in the off-hand, and never a non-shield
    for cls in ("Fighter", "Cleric", "Assassin"):
        p._loadout_class_filter = ""
        p._loadout_slots = {}
        p._loadout_w1 = p._loadout_w2 = p._loadout_ars = None
        p._loadout_upgrades = {}
        p._auto_fill_slots_for_class(cls)
        w2 = p._loadout_w2
        assert w2 is None or idata.is_shield(w2), (cls, w2)
        if w2:
            # a filled off-hand means the main hand is not two-handed: that
            # is the other reason the slot stays empty
            assert not glabels.is_two_handed(p._loadout_w1), (cls, p._loadout_w1)

    # the data helper answers exactly, and a class with no shield gets None
    assert idata.suggest_offhand_shield(class_filter="Fighter", level=25)
    assert idata.is_shield(idata.suggest_offhand_shield(
        class_filter="Fighter", level=25))
    for cls in idata.gear_classes():
        pick = idata.suggest_offhand_shield(class_filter=cls, level=25)
        assert pick is None or idata.is_shield(pick), (cls, pick)


@_SECTION_MARK_2
def test_changing_the_class_chip_re_decides_an_auto_picked_off_hand():
    """The off-hand is class-specific (Dominion's party armor for a tank,
    the Kneecap's ally heal for a healer), so switching chips must re-decide
    a shield WE picked — the old `if not self._loadout_w2` guard kept
    whatever the previous chip auto-filled, and a hand that no longer suits
    the class just sat there.

    A hand-picked off-hand is the user's call and must survive the switch.

    No role chip is a DAMAGE build, so with no chip on a Fighter gets
    Magma Mia's Cinder Coat — the off-hand follows the same rule as the main
    hand and the gear slots. The Cleric still gets the Kneecap, because
    Magma Mia is Fighter/Wizard and a Cleric cannot wield it, so it falls
    back to the class fit (see `_shield_kit_fit`).
    """
    p = _page()
    holder = QtWidgets.QWidget()
    p._page_loadout(QtWidgets.QVBoxLayout(holder))

    # a one-hander in the main hand, or the off-hand stays empty on the
    # two-handed main hand every class but the Fighter defaults to
    p._on_weapon_picked(1, "Sword_Start")
    p._loadout_toggle_class("Fighter", True)
    assert p._loadout_w2 == "Shield_Firebreath"     # Magma Mia, damage
    assert p._loadout_w2_auto is True

    p._loadout_toggle_class("Cleric", True)
    assert p._loadout_w2 == "Shield_OrbitWater"     # Kneecap, ally heal

    # a hand-picked shield is never replaced
    p._on_weapon_picked(2, "Shield_Craft")
    assert p._loadout_w2_auto is False
    p._loadout_toggle_class("Fighter", True)
    assert p._loadout_w2 == "Shield_Craft"


@_SECTION_MARK_2
def test_the_weapon_card_says_why_the_role_picked_that_weapon():
    """The card names a weapon's skills but never says why THAT one was
    recommended, which makes the recommendation a black box: thirteen other
    weapons the class can wield, no visible reason. The WHY line carries the
    phrases the role actually matched, in the sheets' own words.

    Three boundaries, each a real case:
      - a HAND-picked weapon gets no WHY — it was nobody's recommendation,
        so there is nothing to justify;
      - the OFF-HAND gets none either — the SHIELD RANK block answers the
        same question one line below it;
      - an auto-pick whose kit matched nothing stays hidden, because the fit
        scored it 0 and it won on stats alone. Claiming a role reason there
        would be inventing one.
    """
    p = _page()
    holder = QtWidgets.QWidget()
    p._page_loadout(QtWidgets.QVBoxLayout(holder))
    app = QtWidgets.QApplication.instance()

    p._loadout_toggle_class("Fighter", True)
    p._loadout_toggle_role("tank", True)
    for _ in range(4):
        app.processEvents()

    w1 = p._loadout_w1
    assert w1 and p._loadout_w1_auto is True
    why = p._w1_card._why_lbl
    assert not why.isHidden(), "the main hand has no reason on the card"
    # `_full`, not `text()`: the label elides to fit the card, and the
    # reason has to survive being read whole
    txt = why._full
    assert txt.startswith("◆ TANK FIT"), txt
    for r in idata.weapon_role_reasons(w1, "tank"):
        assert r in txt, (r, txt)

    # the reason tracks the role: a different chip, a different explanation
    p._loadout_toggle_role("damage", True)
    for _ in range(4):
        app.processEvents()
    dmg_txt = p._w1_card._why_lbl._full
    assert dmg_txt.startswith("◆ DAMAGE FIT"), dmg_txt
    assert dmg_txt != txt, (txt, dmg_txt)

    # a hand-picked weapon is the user's call and has no recommendation
    p._on_weapon_picked(1, w1 if p._loadout_w1 == w1 else p._loadout_w1)
    for _ in range(4):
        app.processEvents()
    assert p._loadout_w1_auto is False
    assert p._w1_card._why_lbl.isHidden(), \
        "a hand-picked weapon was given a recommendation reason"

    # the off-hand answers with the shield rank instead, never both
    if p._loadout_w2 and idata.is_shield(p._loadout_w2):
        assert p._w2_card._why_lbl.isHidden(), \
            "the off-hand has two explanations at once"

    # an empty slot has nothing to explain
    p._clear_weapons()
    for _ in range(4):
        app.processEvents()
    assert p._w1_card._why_lbl.isHidden()

    # an auto-pick whose kit matched NOTHING stays hidden. Every real
    # auto-pick today scores a fit (measured across all four classes x three
    # roles), so this branch is defensive — and a defensive branch that
    # invents a reason when it should say nothing is exactly the failure
    # worth pinning, so it is driven directly rather than waited for.
    unranked = next(i for i in ("Sword_Start", "Staff_Start", "Dagger_Start")
                    if not idata.weapon_role_fit(i, "damage"))
    p._loadout_w1 = unranked
    p._loadout_w1_auto = True
    p._loadout_role = "damage"
    p._update_weapon_card_display(p._w1_card, unranked, 1)
    assert p._w1_card._why_lbl.isHidden(), \
        "a 0-fit pick was given a reason it does not have"


@_SECTION_MARK_2
def test_the_role_chips_re_decide_the_weapons_but_not_your_own():
    """A class does not say what a build is FOR, and a stat sheet cannot
    work it out either — healing, blocking and threat are skills here, so
    the stat score counts them at zero and the fill is role-blind without
    these chips. They sit on the mode bar beside the class chips, and a tap
    re-picks the weapons WE chose.

    A tap also re-picks the GEAR, which it used to leave alone: the role now
    reaches the gear score as well, and a healer that is handed a damage
    build's eleven pieces is the same bug one layer down. What must NOT move
    is any weapon the user hand-picked — that is their call, not the chip's.
    """
    p = _page()
    holder = QtWidgets.QWidget()
    p._page_loadout(QtWidgets.QVBoxLayout(holder))
    app = QtWidgets.QApplication.instance()

    chips = {c.text() for c in p._role_chips.values()}
    assert chips == {"Tank", "Healer", "Damage"}, chips
    assert p._role_group.parentWidget() is p._cgroup.parentWidget(), \
        "the role chips are not on the mode bar"

    p._loadout_toggle_class("Cleric", True)
    app.processEvents()
    tank = p._loadout_w1
    slots_before = dict(p._loadout_slots)
    assert tank and p._loadout_w1_auto is True

    p._loadout_toggle_role("healer", True)
    app.processEvents()
    assert p._loadout_role == "healer"
    healer = p._loadout_w1
    assert healer and healer != tank, (tank, healer)
    # the gear follows the role too: a healer's set must not be the damage
    # build's eleven pieces (the profile table, `_PROFILE_WEIGHTS`)
    assert p._loadout_slots != slots_before, "the role chip did nothing"
    # a Cleric build can now hold a shield it was locked out of by a 2H staff
    assert p._loadout_w2 and idata.is_shield(p._loadout_w2), p._loadout_w2

    # un-tapping the chip clears the role rather than stranding it
    p._loadout_toggle_role("healer", False)
    assert p._loadout_role == ""

    # no chip is a DAMAGE build, stated: the page has always assumed damage
    # (Fervor 1.1, Armor 0.2), and the kit term has to be live by default or
    # the weapons go back to the stat sum that ranks healers last
    p._loadout_toggle_role("damage", True)
    damage_w1 = p._loadout_w1
    p._loadout_toggle_role("damage", False)
    for _ in range(4):
        app.processEvents()
    assert p._loadout_role == ""
    p._loadout_toggle_class("Cleric", True)
    p._loadout_toggle_role("damage", True)
    assert p._loadout_w1 == damage_w1
    p._loadout_toggle_role("damage", False)
    for _ in range(4):
        app.processEvents()
    assert p._loadout_w1 == damage_w1, "no role chip is not a damage build"

    # a hand-picked main hand is the user's call and survives a role change
    p._on_weapon_picked(1, "Sword_Start")
    p._loadout_toggle_role("damage", True)
    app.processEvents()
    assert p._loadout_w1 == "Sword_Start"


@_SECTION_MARK_2
def test_the_off_hand_card_shows_the_shield_ranking_and_why():
    """The off-hand recommendation shows its work: the winner marked, the
    runners-up beneath it, and the skill that decided each one. The three
    Rare shields read an identical 564 Armor, so the kit is the only thing
    that explains the pick — a bare name left the choice looking arbitrary.

    The ranking is for the BUILD, so the Tank chip is turned on here: with
    no chip the page builds damage and the winner is Magma Mia, which is
    correct and is what the sibling role test pins.

    A hand-picked runner-up must read as a choice AGAINST a stated
    recommendation, not as the recommendation itself.
    """
    p = _page()
    holder = QtWidgets.QWidget()
    p._page_loadout(QtWidgets.QVBoxLayout(holder))
    app = QtWidgets.QApplication.instance()

    def _lines():
        lay = p._w2_card._fit_lay
        return [lay.itemAt(i).widget().text()
                for i in range(lay.count()) if lay.itemAt(i).widget()]

    p._loadout_toggle_class("Fighter", True)
    p._loadout_toggle_role("tank", True)
    for _ in range(4):
        app.processEvents()
    rows = _lines()
    assert len(rows) >= 3, rows              # a header plus two contenders
    assert rows[0] == "SHIELD RANK"
    # the winner is marked and carries the reason, not just a name
    assert rows[1].startswith("★ ") and "Dominion" in rows[1], rows
    assert "Fortifying Cry" in rows[2], rows
    # the runners-up are named with their own kit
    assert any("Magma Mia" in r and "Furnace Roar" in _lines()[i + 1]
               for i, r in enumerate(_lines()) if "Magma Mia" in r)

    # a hand-picked runner-up is marked as equipped, and the recommendation
    # still leads
    p._on_weapon_picked(2, "Shield_Firebreath")
    for _ in range(4):
        app.processEvents()
    rows = _lines()
    assert rows[1].startswith("★ ") and "Dominion" in rows[1], rows
    assert any(r.startswith("▸ ") and "(equipped)" in r for r in rows), rows

    # no class picked -> nothing to rank against
    p._loadout_class_filter = ""
    p._loadout_toggle_class("Fighter", False)
    for _ in range(4):
        app.processEvents()
    assert p._w2_card._fit_box.isHidden()

    # a 2H main hand locks the off-hand, and the block goes with it
    p._on_weapon_picked(2, "Shield_Craft")
    p._on_weapon_picked(1, "GS_Nova")          # two-handed greatsword
    for _ in range(4):
        app.processEvents()
    assert p._loadout_w2 is None
    assert p._w2_card._fit_box.isHidden()


@_SECTION_MARK_2
def test_the_role_chips_only_offer_roles_the_class_can_play():
    """A Warrior or a Rogue cannot heal and a mage or a Rogue cannot tank
    (user-confirmed game rules, `stats._CLASS_ROLES`), so those chips are
    dead for those classes. A dead chip that keeps its live styling just
    swallows the click and reads as a broken control, so `FilterChip`
    restyles when disabled.

    The catalog cannot supply this rule and actively disagrees — 4 Fighter
    and 2 Assassin weapons carry healing text — so the table is stated, not
    derived. Which is why the DATA layer refuses too: a stale role must not
    be able to build a tank for a mage from any caller.
    """
    p = _page()
    holder = QtWidgets.QWidget()
    p._page_loadout(QtWidgets.QVBoxLayout(holder))
    app = QtWidgets.QApplication.instance()

    expected = {"Fighter": {"tank", "damage"},
                "Assassin": {"damage"},
                "Cleric": {"healer", "damage"},
                "Wizard": {"healer", "damage"}}
    for cls, allowed in expected.items():
        p._loadout_toggle_class(cls, True)
        for _ in range(3):
            app.processEvents()
        live = {r for r, c in p._role_chips.items() if c.isEnabled()}
        assert live == allowed, (cls, live, allowed)
        # ...and a disabled chip does not keep the live styling
        for role, chip in p._role_chips.items():
            if role not in allowed:
                assert "36)" not in chip.styleSheet(), (cls, role)

    # a role the new class cannot play is cleared, not left selected
    p._loadout_toggle_class("Cleric", True)
    p._loadout_toggle_role("healer", True)
    cleric_w1 = p._loadout_w1
    p._loadout_toggle_class("Assassin", True)
    for _ in range(3):
        app.processEvents()
    assert p._loadout_role == "", p._loadout_role
    assert not p._role_chips["healer"].isChecked()
    # and the weapons WE picked followed the class, instead of leaving a
    # Rogue holding the Cleric's staff
    assert cleric_w1 and p._loadout_w1 != cleric_w1
    assert "Cleric" not in (idata.item(p._loadout_w1) or {}).get("classes", [])

    # the data layer refuses too, and refuses to the DEFAULT build rather
    # than to a bare stat sum
    for cls in ("Wizard", "Assassin", "Cleric"):
        for role in ("tank", "healer"):
            if idata.class_can_role(cls, role):
                continue
            assert idata.suggest_main_weapon(class_filter=cls, level=25,
                                             role=role) == \
                idata.suggest_main_weapon(class_filter=cls, level=25,
                                          role="damage"), (cls, role)
    # no class picked -> every role on offer
    assert set(idata.class_roles(None)) == set(idata.LOADOUT_ROLES)
    assert all(idata.class_can_role(None, r) for r in idata.LOADOUT_ROLES)


@_SECTION_MARK_2
def test_the_totals_say_how_much_came_from_the_upgrade_ranks():
    """Every weapon in this page is at its ladder max, so every weapon stat
    arrived pre-inflated and the sheet could not say which numbers the piece
    earned and which the ranks added. Each row now carries the rank's share
    in gold, and a line above the sheet names the ranked slots.

    The share is exact, not an estimate: it is `loadout_stat_totals` read a
    second time with the ranks dropped, per stat.
    """
    p = _page()
    holder = QtWidgets.QWidget()
    p._page_loadout(QtWidgets.QVBoxLayout(holder))
    app = QtWidgets.QApplication.instance()
    p._loadout_toggle_class("Fighter", True)
    for _ in range(4):
        app.processEvents()

    # the line names the ranked slots, and every weapon really is ranked
    lines = [w for w in holder.findChildren(QtWidgets.QWidget, "RankLine")]
    assert len(lines) == 1, lines
    assert "W1 +3" in lines[0].findChildren(QtWidgets.QLabel)[1].text()
    for slot in (p._loadout_w1, p._loadout_w2, p._loadout_ars):
        assert p._loadout_upgrades.get(slot) == p._weapon_max_upgrades(slot)

    ranked = idata.loadout_stat_totals(
        p._loadout_w1, p._loadout_w2, p._loadout_ars, p._loadout_slots,
        level=25, upgrades=p._loadout_upgrades)
    unranked = idata.loadout_stat_totals(
        p._loadout_w1, p._loadout_w2, p._loadout_ars, p._loadout_slots,
        level=25, upgrades={})
    want = {k: round(v - unranked.get(k, 0.0), 2)
            for k, v in ranked.items() if round(v - unranked.get(k, 0.0), 2)}

    # every gold share matches the real gap, and a stat the ranks never
    # touched carries no share at all (Magic Penetration is armor-slot only)
    got = {}
    for i in range(p._totals_container.count()):
        w = p._totals_container.itemAt(i).widget()
        if w is None or w.layout() is None:
            continue
        texts = [w.layout().itemAt(j).widget().text()
                 for j in range(w.layout().count())
                 if w.layout().itemAt(j).widget() is not None]
        gold = [t for lbl in w.findChildren(QtWidgets.QLabel, "RankBonus")
                for t in [lbl.text()]]
        if gold:
            got[texts[0].upper()] = float(gold[0][1:])
    # the panel uppercases its lead row, so fold both sides
    assert got == {k.upper(): v for k, v in want.items() if v}, (got, want)
    assert "MAGIC PENETRATION" not in got

    # and an empty build shows neither the line nor any share
    p._clear_weapons()
    for _ in range(4):
        app.processEvents()
    # processEvents alone never delivers deleteLater under headless Qt, so
    # the rebuilt rows stay findable (see tests/qt_helpers.drain_deleted)
    from tests.qt_helpers import drain_deleted
    drain_deleted()
    assert not [w for w in holder.findChildren(QtWidgets.QWidget, "RankLine")]
    assert not holder.findChildren(QtWidgets.QLabel, "RankBonus")


@_SECTION_MARK_2
def test_the_collection_rarity_chips_mean_different_things_per_slot():
    """A tier that can never match is worse than no tier, and a tier that
    means something is a control. Which is which depends on the slot:

      gear slot / All — a real rarity filter, so only tiers the visible set
        contains. The catalog has 160 Rare and 127 Epic gear and NO
        Legendary, so Legendary must not be offered there: it could only
        ever open the empty state.
      WEAPONS slot — a rarity PROJECTION. All 36 weapons are display-Rare,
        but they carry per-rarity stats (Rare +3 / Epic +4 / Legendary +5),
        so clicking Epic shows every weapon AT Epic, and the tile names the
        tier it is showing.
    """
    from tests.qt_helpers import drain_deleted

    def view(slot, rar):
        p = _page()
        holder = QtWidgets.QWidget()
        p._page_loadout(QtWidgets.QVBoxLayout(holder))
        p._collection_class_filter = ""
        p._coll_slot_filter = slot
        p._coll_rarity_filter = rar
        p._loadout_set_mode("collection")
        app = QtWidgets.QApplication.instance()
        for _ in range(80):
            app.processEvents()
        drain_deleted()
        chips = {c.text() for c in holder.findChildren(QtWidgets.QPushButton)}
        return p, holder, chips

    # a gear slot: no Legendary, because nothing in the catalog is Legendary
    _p, _h, chips = view("Chest", "")
    assert {"All", "Rare", "Epic"} <= chips, chips
    assert "Legendary" not in chips, chips
    assert not [i for i in idata.items()
                if idata.is_gear(i)
                and idata.item_display_rarity(i.get("id") or "") == "Legendary"], \
        "a Legendary chip appeared with no Legendary gear to show"

    # the weapons slot: every tier, because each one projects
    for tier, cap in (("Rare", 3), ("Epic", 4), ("Legendary", 5)):
        p, holder, chips = view("Weapons", tier)
        assert {"All", "Rare", "Epic", "Legendary"} <= chips, (tier, chips)
        labels = [lbl.text() for lbl in holder.findChildren(QtWidgets.QLabel)]
        # the tile names the tier AND the rank, so the numbers cannot be
        # mistaken for the piece's own Rare ones
        assert f"{tier} +{cap}" in " ".join(labels), \
            (tier, [x for x in labels if "+" in x][:4])
        assert idata.upgrade_cap(tier) == cap
        # and the stats really are that tier's. Only a tier ABOVE the
        # piece's own differs — projecting a display-Rare weapon to Rare is
        # just its own reading back.
        weapon = next(i["id"] for i in idata.items()
                      if idata.category(i.get("type")) == "Weapons")
        want = idata.format_item_stats_summary(weapon, level=25,
                                               upgrades=cap)
        own = idata.item_display_rarity(weapon)
        if tier != own:
            assert want != idata.format_item_stats_summary(
                weapon, level=25, upgrades=idata.upgrade_cap(own)), tier
        assert any(t.startswith(want.split(" · ")[0])
                   for t in [e.text() for e in holder.findChildren(C.ElideLabel)]), \
            (tier, want)

    # a tier the new slot cannot honour is cleared, not left filtering all
    p = _page()
    holder = QtWidgets.QWidget()
    p._page_loadout(QtWidgets.QVBoxLayout(holder))
    p._coll_slot_filter = "Weapons"
    p._coll_rarity_filter = "Legendary"
    p._toggle_coll_slot("Chest", True)
    assert p._coll_rarity_filter == "", p._coll_rarity_filter


@_SECTION_MARK_2
def test_each_weapon_card_shows_the_ladder_max_the_totals_count():
    """A weapon card states the piece's stats at its ladder MAX rank, and
    those are the numbers the rest of the page uses: a pick always maxes
    (`_max_out_weapon`), so the totals, the clipboard and the augment plan
    all read the weapon there. The card used to show a weapon with no
    numbers at all, so the loadout and the item page read differently for
    the same piece.

    "Identically" is literal: the max-rank reading equals the item page's
    UPGRADE LADDER end state (base + every step's gain), on every weapon in
    the catalog — not approximately.
    """
    p = _page()
    holder = QtWidgets.QWidget()
    p._page_loadout(QtWidgets.QVBoxLayout(holder))
    app = QtWidgets.QApplication.instance()
    p._loadout_toggle_class("Fighter", True)
    for _ in range(4):
        app.processEvents()
    ElideLabel = C.ElideLabel

    def _lines(box):
        return [w.text() for w in box.findChildren(ElideLabel)]

    for iid, box in ((p._loadout_w1, p._w1_card),
                     (p._loadout_w2, p._w2_card),
                     (p._loadout_ars, p._ars_card)):
        if not iid:
            continue
        cap = p._weapon_max_upgrades(iid)
        assert cap > 0, iid
        want = idata.format_item_stats_summary(iid, level=25, upgrades=cap)
        assert f"+{cap} · {want}" in _lines(box), (iid, _lines(box))
        # ...and that is the item page's ladder end state, stat for stat
        rung = idata.upgrade_ladder(idata.item(iid), level=25)[0]
        end = {s["label"]: s["value"] for s in rung["base"]}
        for step in rung["steps"]:
            for g in step["gains"]:
                end[g["label"]] = end.get(g["label"], 0) + g["value"]
        for row in want.split(" · "):
            label, _, val = row.rpartition(" ")
            assert float(val) == end[label], (iid, label, val, end[label])

    # an empty off-hand shows no numbers at all
    p._clear_weapons()
    for _ in range(4):
        app.processEvents()
    for box in (p._w1_card, p._w2_card, p._ars_card):
        assert box._stat_lbl.isHidden()


@_SECTION_MARK_2
def test_picking_a_weapon_equips_it_at_its_max_upgrade():
    """A weapon in a build is ALWAYS its ladder's max rank, and the card
    carries no rank control to say otherwise.

    The card used to have a `+N/max` stepper, but every pick maxed the piece
    on the way in, so it sat at `+3/3` on all three cards and could only ever
    walk a piece back down — three identical numbers reading as a dead
    widget. The rank is now derived, never chosen: `_max_out_weapon` reads the
    LIVE cap off the rarity's upgrade ladder (`stats.upgrade_path` → the
    `gearUpgrades` sheet — Rare +3 / Epic +4 / Legendary +5), never a literal,
    so a patch that moves a cap moves every number with it. This is the same
    cap the Collection Manager's weapon tiles already read at.

    Re-picking the same piece re-derives the same max, so a stale rank can
    never survive on a card that no longer offers a way to change it.
    """
    p = _page()
    holder = QtWidgets.QWidget()
    p._page_loadout(QtWidgets.QVBoxLayout(holder))

    w1 = idata.suggest_main_weapon(class_filter="Fighter", level=25)
    cap = idata.upgrade_cap(idata.item_display_rarity(w1))
    assert cap > 0, "the ladder has no cap for a display-Rare weapon"
    p._on_weapon_picked(1, w1)
    assert p._loadout_upgrades.get(w1) == cap

    # the stepper is GONE: no rank control anywhere on the weapon cards
    assert not hasattr(p._w1_card, "_upg_btn")
    assert not hasattr(p, "_cycle_weapon_upgrade")
    assert not hasattr(p, "_refresh_upgrade_button")
    assert not [b for b in holder.findChildren(QtWidgets.QPushButton)
                if b.text().startswith("+") and "/" in b.text()]

    # a stale rank cannot survive: re-picking re-derives the ladder max
    p._loadout_upgrades[w1] = 0
    p._on_weapon_picked(1, w1)
    assert p._loadout_upgrades.get(w1) == cap

    # a different piece is maxed at ITS rarity's cap
    w2 = idata.suggest_main_weapon(exclude_ids=(w1,), class_filter="Fighter",
                                   level=25)
    p._on_weapon_picked(2, w2)
    assert p._loadout_upgrades.get(w2) == idata.upgrade_cap(
        idata.item_display_rarity(w2))


@_SECTION_MARK_2
def test_picker_rows_lead_with_their_rarity():
    """Every popout row names its tier in words, before the name.

    The rarity colour was the only signal, and colour is the one thing a
    glance down a list of thirty weapons cannot resolve. Weapons carry the
    gear slots' junk rule too — no Common, no Uncommon, no level-0 rows —
    which is a no-op on today's catalog (all 36 weapons are display-Rare,
    the town ones being authored Uncommon but sold as Rare) and a guard
    against a Common weapon appearing in a slot the loadout rejects. The
    stats beside the name are the piece's own scaled to 25, the level the
    loadout is locked to.
    """
    from farever_companion.ui.pages.items.loadout_weapons import _ItemPickerDialog

    _page()                                   # a QApplication is required
    tiers = ("Common", "Uncommon", "Rare", "Epic", "Legendary")

    for category, slot in (("Weapons", None), ("Gear", "Head"),
                           ("Gear", "GearNeck")):
        dlg = _ItemPickerDialog(category=category, slot_type=slot, parent=None,
                                class_filter="Fighter")
        try:
            rows = [dlg._list.item(i).text() for i in range(dlg._list.count())]
            assert rows, (category, slot)
            for row in rows:
                assert row.split(" ")[0] in tiers, row
            names = [r.split(" ", 2)[2].split("  (")[0] for r in rows]
            assert all(n for n in names), rows
        finally:
            dlg.deleteLater()

    # the weapon list carries no junk tier at all
    wpn = _ItemPickerDialog(category="Weapons", slot_type=None, parent=None,
                            class_filter="Fighter")
    try:
        assert wpn._list.count()
        for i in range(wpn._list.count()):
            iid = wpn._list.item(i).data(QtCore.Qt.UserRole)
            assert idata.item_display_rarity(iid) not in ("Common", "Uncommon")
            assert (idata.item(iid) or {}).get("level")
    finally:
        wpn.deleteLater()


@_SECTION_MARK_2
def test_ring_pair_cannot_hold_one_ring_twice():
    """Ring 1 and Ring 2 are the same item type; picking a ring the other
    finger holds moves it, so the pair stays distinct and the totals can't
    count one ring twice."""
    p = _page()
    holder = QtWidgets.QWidget()
    p._page_loadout(QtWidgets.QVBoxLayout(holder))
    ring = "Finger_Z3RCraft_Cri"
    other = "Finger_Z3RCraft_Fer"
    p._on_gear_picked("Ring 1", ring)
    p._on_gear_picked("Ring 2", other)
    p._on_gear_picked("Ring 2", ring)          # the pair would collide
    assert p._loadout_slots.get("Ring 1") == other    # swapped, not cloned
    assert p._loadout_slots.get("Ring 2") == ring
    assert p._loadout_slots.get("Ring 1") != p._loadout_slots.get("Ring 2")


@_SECTION_MARK_2
def test_collection_manager_view_is_kept_until_the_class_filter_changes():
    """Building the Collection Manager costs ~190 cards (~400 ms measured
    2026-09-25), and the class / slot / rarity filters are the only things
    that change which items it holds — ownership can only be toggled from
    this view, which relabels its own button in place. So leaving and
    re-entering the tab must hand back the SAME view widget instead of
    rebuilding it, and any of the three filters changing must rebuild."""

    def _scroll_view(host):
        for i in range(host._coll_container.count()):
            w = host._coll_container.itemAt(i).widget()
            if isinstance(w, QtWidgets.QScrollArea):
                return w
        return None

    p = _page()
    holder = QtWidgets.QWidget()
    p._page_loadout(QtWidgets.QVBoxLayout(holder))

    p._loadout_set_mode("collection")
    first = _scroll_view(p)
    assert first is not None and first.widget() is not None

    p._loadout_set_mode("bis")
    p._loadout_set_mode("collection")
    assert _scroll_view(p) is first                 # reused, not rebuilt

    # a class filter genuinely changes the item set, so that rebuilds…
    # (the page opens on Fighter; pick a different class)
    p._collection_class_filter = "Assassin"
    p._loadout_set_mode("collection")
    filtered = _scroll_view(p)
    assert filtered is not None and filtered is not first
    # …and the filtered view is kept the same way
    p._loadout_set_mode("bis")
    p._loadout_set_mode("collection")
    assert _scroll_view(p) is filtered

    # the slot filter is part of the same kept-view key: narrowing it
    # rebuilds too (the view keeps classes and slots independently)
    p._coll_slot_filter = "Chest"
    p._loadout_set_mode("collection")
    slotted = _scroll_view(p)
    assert slotted is not None and slotted is not filtered
    p._loadout_set_mode("bis")
    p._loadout_set_mode("collection")
    assert _scroll_view(p) is slotted


@_SECTION_MARK_2
def test_twin_names_read_apart_by_their_tier_colour_not_a_tag():
    """The dungeon sets ship a Rare and an Epic piece under the SAME display
    name, and there is no tier tag on those tiles: the name is painted in the
    tier's colour, which is what tells the pair apart — so the two rows must
    carry different colours. The pane lists both halves by tier."""
    p = _page()
    # the twins: 'Royal Chamber Helmet' is Head_RBee_FigWiz (Rare) and
    # Head_EBee_FigWiz (Epic)
    twin_ids = ("Head_RBee_FigWiz", "Head_EBee_FigWiz")
    rows = {}
    for i in range(p._items_list.count()):
        li = p._items_list.item(i)
        if li.data(support.ID_ROLE) in twin_ids:
            rows[li.data(support.ID_ROLE)] = li
    assert set(rows) == set(twin_ids)
    assert rows["Head_RBee_FigWiz"].text() == rows["Head_EBee_FigWiz"].text()
    rare_col = rows["Head_RBee_FigWiz"].foreground().color().name()
    epic_col = rows["Head_EBee_FigWiz"].foreground().color().name()
    assert rare_col == QtGui.QColor(theme.rarity_color("Rare")).name()
    assert epic_col == QtGui.QColor(theme.rarity_color("Epic")).name()
    assert rare_col != epic_col                    # same name, different tier
    # no tier data rides on the tiles any more: the colour is the whole signal
    assert not hasattr(support, "RARITY_BADGE_ROLE")

    # the faction view lists the twin under that same name, so both halves
    # stay reachable from the Dungeons tab — RARE is lit at rest
    support.quick_pick(p, "Dungeons")
    dv = p._items_dungeon_view
    dv._pick("Manfish")
    g = dv._groups["Manfish"]
    shared = {idata.item(i)["name"] for i in g["shared"]}
    assert shared <= set(_pane_labels(p))

    # ...while a boss pick carries only its own drops: its heroic pieces
    # plus its mirrored weapons, no shared pool (the 11 source-unknown ones
    # stay behind their own chip either way)
    dv.select("Manfish", "Crabgantua")
    crab = [i for b in g["bosses"] if b["boss_id"] == "Crabgantua"
            for i in b["epic"]]
    crab_names = {idata.item(i)["name"] for i in crab}
    crab_weapons = {idata.item(w)["name"]
                    for b in g["bosses"] if b["boss_id"] == "Crabgantua"
                    for w in b["weapons"]}
    labels = set(_pane_labels(p))
    assert crab_names <= labels and crab_weapons <= labels
    assert not (shared & labels)

    # ...and lighting the EPIC tier changes nothing on this tab: the Rare
    # pool stays next to this boss's heroic pieces (the 11
    # source-unknown ones stay behind their own chip)
    crab = [i for b in g["bosses"] if b["boss_id"] == "Crabgantua"
            for i in b["epic"]]
    # a "Rare-only" name is one no Epic piece in this faction carries: the
    # twins (and the trinket) share a name across the two tiers by design
    epics = list(g["shared"]) + list(idata.epic_pieces(g))
    epic_names = {idata.item(i)["name"] for i in epics
                  if idata.item_display_rarity(i) == "Epic"}
    rare_only = {idata.item(i)["name"] for i in g["shared"]
                 if idata.item_display_rarity(i) == "Rare"
                 and idata.item(i)["name"] not in epic_names}
    assert rare_only                      # the twins are the collisions
    # ...and lighting the EPIC tier changes nothing on this tab: the pick
    # still carries only its own drops, shared pool included out
    p._items_rarity_toggle_chips["Epic"].setChecked(True)
    labels = set(_pane_labels(p))
    assert crab_names <= labels and crab_weapons <= labels
    assert not (shared & labels)
    assert not (rare_only & labels)


@_SECTION_MARK_2
def test_a_tier_the_tab_does_not_carry_snaps_to_the_tabs_own_tier():
    """The row can never be empty, so a tab that carries no such tier
    (Uncommon lit, then Weapons) must not leave the list filtered to nothing
    with no lit button to explain it: the pick moves to the tab's own tier
    ahead of the row pass, so the rows follow immediately."""
    p = _page()
    support.quick_pick(p, "Armor")
    p._items_rarity_toggle_chips["Uncommon"].setChecked(True)
    assert set(_visible_rarities(p)) == {"Uncommon"}

    support.quick_pick(p, "Weapons")     # no Uncommon weapon exists
    assert p._items_category == "Weapons"
    assert p._items_rarity_filter == {"Rare"}    # the tab's own tier
    assert p._items_rarity_toggle_chips["Rare"].isChecked()
    assert not p._items_rarity_toggle_chips["Uncommon"].isVisible()
    vis = _visible_rarities(p)
    assert vis and set(vis) == {"Rare"}         # no stale empty list

    # a tab that DOES carry the tier keeps the pick
    support.quick_pick(p, "Jewelry")
    p._items_rarity_toggle_chips["Uncommon"].setChecked(True)
    assert p._items_rarity_filter == {"Uncommon"}
    support.quick_pick(p, "Armor")               # Armor carries Uncommon too
    assert p._items_rarity_filter == {"Uncommon"}
    assert set(_visible_rarities(p)) == {"Uncommon"}


@_SECTION_MARK_2
def test_dungeon_pane_tiles_are_filled_in_their_own_tier_colour():
    """A pane tile's icon fill follows the item's own TIER, exactly like its
    name does. It used to take the faction's class colour, and a faction name
    is not a class, so class_color() answered MUTED and every tile came out
    grey while its name was purple or blue."""
    from farever_companion.ui.pages.items import dungeon_view as dview

    p = _page()
    g = idata.dungeon_drop_groups()[0]
    rare_id = next(i for i in g["shared"]
                   if idata.item_display_rarity(i) == "Rare")
    epic_id = next(i for i in g["shared"]
                   if idata.item_display_rarity(i) == "Epic")
    assert QtGui.QColor(theme.class_color(g["faction"])).name() == \
        QtGui.QColor(theme.MUTED).name()      # the grey that used to leak in

    built = []
    orig = dview.icons.item_tile
    dview.icons.item_tile = lambda iid, name, size, colour: (
        built.append((iid, colour)), orig(iid, name, size, colour))[1]
    try:
        for iid, tier in ((rare_id, "Rare"), (epic_id, "Epic")):
            tile = dview._item_tile(p, iid, wt="HEAD")
            want = theme.rarity_color(tier)
            assert built[-1] == (iid, want), iid
            name = next(w for w in tile.findChildren(QtWidgets.QLabel)
                        if w.text() and not w.pixmap())
            assert f"color:{want}" in name.styleSheet(), iid
    finally:
        dview.icons.item_tile = orig

    # ...and the boss row's weapon chips take their tier colour too, instead
    # of the app accent
    support.quick_pick(p, "Dungeons")
    dv = p._items_dungeon_view
    dv._pick("Manfish")
    row = dv._rows[("Manfish", "Crabgantua")]
    chips = [w for w in row.findChildren(QtWidgets.QFrame)
             if w.objectName() == "Card"]
    assert chips
    for chip in chips:
        icon = next(w for w in chip.findChildren(QtWidgets.QLabel) if w.pixmap())
        # the fill is the pixmap's commonest opaque colour; it lands a hair
        # off the palette entry because the tile paints an alpha wash
        dom = _dominant_colour(icon.pixmap())
        rare = QtGui.QColor(theme.rarity_color("Rare"))
        got = QtGui.QColor(dom)
        assert abs(got.red() - rare.red()) <= 6 \
            and abs(got.green() - rare.green()) <= 6 \
            and abs(got.blue() - rare.blue()) <= 6, dom


@_SECTION_MARK_2
def test_dungeon_faction_chips_are_radio_style_and_weapons_stay_on_the_boss_row():
    """The faction row is one-at-a-time radio: clicking the lit chip leaves
    it lit — an un-check used to blank the row while the pane still showed
    that faction, so it took 2+ clicks to get back — and the Rare signature
    weapons stay in the boss column, not in the pane."""
    p = _page()
    support.quick_pick(p, "Dungeons")
    dv = p._items_dungeon_view
    chips = dv._chips
    lit = lambda: sorted(f for f, c in chips.items() if c.isChecked())
    assert lit() == ["Bee"] and dv._fac == "Bee"

    # the chips wear this page's own accent — the boss rows' GOLD — and ONLY
    # the lit one does. class_color() was the old source and answers MUTED for
    # a faction name (not a class), so all four looked picked at once
    for fac, chip in chips.items():
        ss = chip.styleSheet().lower()
        assert (theme.GOLD.lower() in ss) == (fac == dv._fac), fac
        # the hover rule greys the border on purpose, so judge the base rule
        assert theme.MUTED.lower() not in ss.split(":hover")[0], fac
    chips["Manfish"].setChecked(True)
    assert [f for f, c in chips.items()
            if theme.GOLD.lower() in c.styleSheet().lower()] == ["Manfish"]
    chips["Bee"].setChecked(True)         # back to the faction the test opened on

    # clicking the lit chip is refused in one click, not two
    chips["Bee"].setChecked(False)
    assert lit() == ["Bee"] and dv._fac == "Bee"

    # picking another lights exactly that one
    chips["Manfish"].setChecked(True)
    assert lit() == ["Manfish"] and dv._fac == "Manfish"

    # a stray un-check of a chip that isn't lit changes nothing
    chips["Kobold"].setChecked(False)
    assert lit() == ["Manfish"]

    # the boss column keeps its Rare weapons (the pane's Epic blocks are
    # additive — they don't move the weapons)
    dv._pick("Manfish")
    g = dv._groups["Manfish"]
    crab = next(b for b in g["bosses"] if b["boss_id"] == "Crabgantua")
    assert crab["weapons"]
    row = dv._rows[("Manfish", "Crabgantua")]
    row_labels = {lbl.text() for lbl in row.findChildren(QtWidgets.QLabel)}
    assert {idata.item(w)["name"] for w in crab["weapons"]} <= row_labels
    assert all(idata.item_display_rarity(w) == "Rare" for w in crab["weapons"])


# --- the weapon "Weapon Upgraded" passive is NOT a ladder row ---------------

@_SECTION_MARK_2
def test_ladder_leaves_the_upgrade_passive_to_the_trait_line():
    """The UPGRADE LADDER is gear stats only. The engine-granted "Weapon
    Upgraded" percentages are stated once on the card's ⚡ trait line, so the
    matrix must never paint a second, duplicate row of the same numbers under
    its step columns — the duplicate is the easy thing to reintroduce, and it
    was exactly what the ⚡ line was moved in to replace."""
    p = _page()
    p._items_show_id("Axe_Boomerang")
    labels = _pane_labels(p)
    assert "Weapon\nUpgraded" not in labels          # no passive row
    assert {"0%", "+1%", "+2%", "+3%", "+4%", "+5%"}.isdisjoint(labels)
    # the gear stats the ladder exists for are all still there
    assert {"Dexterity", "Strength", "Critical", "Vitality"} <= labels
    # and the one place the passive lives does state it
    assert any("⚡" in t and "Weapon Upgraded" in t for t in labels)


@_SECTION_MARK_2
def test_item_page_passive_block_owns_the_upgrade_passive():
    """The card's ⚡ block carries what neither the SkillBar nor the ladder
    does: the engine-granted "Weapon Upgraded" ranks. The weapon's own
    passive is NOT re-listed there any more — the bar's tile and effect text
    already say that sentence."""
    p = _page()
    p._items_show_id("Axe_Boomerang")
    glyph = [t for t in _pane_labels(p) if "⚡" in t]
    # one value per rarity, each labelled, and each painted in the SAME
    # colour the ladder tints that rarity's column with — a single merged
    # "+1/+2/+3/+4/+5" run is what this line must never go back to.
    # CALIBRATED IN GAME: a Cheese Moon reads 2% / 3% / 4% by rarity (the
    # ladder's ranks 2 / 3 / 4), i.e. one rank under each rarity's cap —
    # stats.granted_rank is the one place that shift lives
    for rar, val in (("Rare", "+2%"), ("Epic", "+3%"), ("Legendary", "+4%")):
        want = f"{rar} {val}"
        assert any(want in t and theme.rarity_color(rar) in t
                   and "(Passive)" in t for t in glyph), (want, glyph)
    # the type chip leads: '⚡  (Passive)  Weapon Upgraded — …'
    for t in glyph:
        assert t.index("(Passive)") < t.index("Weapon Upgraded"), t
    assert not any("all allies within 40m" in t for t in glyph), glyph


@_SECTION_MARK_2
def test_effect_path_trait_paints_every_rarity_in_one_ordered_run():
    """An effect type's number is a function of which rarity grants the rank, so
    the sentence states the value THIS piece is granted and every reachable
    rarity is a labelled bit beside the others, in rarity order — its own first,
    next to Epic. The sentence itself is the GAME's own sentence for this piece,
    so that number is UNLABELLED and the rarity reaches the reader by COLOUR
    (settled by comparing every candidate against the game's own text: see
    ai/workspace/buffy/compare_trait_vs_game.py).

    The ladder's rank 1 is a value granted_rank gives nobody and must not
    appear at all. Martyr of Enripit is Rare: 5% now, 6% / 7% higher up."""
    p = _page()
    p._items_show_id("GS_Nova")
    glyph = [t for t in _pane_labels(p) if "⚡" in t]
    assert glyph
    # each rarity's value is labelled AND painted in its own colour, in order
    for rar, val in (("Rare", "5%"), ("Epic", "6%"), ("Legendary", "7%")):
        want = f"{rar} {val}"
        assert any(want in t and theme.rarity_color(rar) in t for t in glyph), \
            (want, glyph)
    assert any("attack twice" in t for t in glyph), glyph
    for t in glyph:
        tail = t.split("Weapon Upgraded")[-1]
        assert tail.index("Rare 5%") < tail.index("Epic 6%") \
            < tail.index("Legendary 7%"), t
    # ...and the piece's own value is stated in the sentence too, UNLABELLED:
    # it is the game's sentence for this weapon, which names the number and no
    # rarity — the rarity only ever arrives as a colour, in the run below
    inline = [t for t in glyph if "5% chance to attack twice" in t]
    assert inline, glyph
    for t in inline:
        tail = t.split("Weapon Upgraded")[-1]
        sentence = tail[:tail.index(" — ")] if " — " in tail else tail
        assert "Rare" not in sentence and "X%" not in sentence, tail
    # the ladder's base (4%, rank 1) is unreachable and must never appear
    assert not any("4%" in t.split("Weapon Upgraded")[1] for t in glyph), glyph
