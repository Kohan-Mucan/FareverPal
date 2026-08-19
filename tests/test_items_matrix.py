"""UPGRADE LADDER matrix widget tests (headless Qt, offscreen platform).

Builds build_upgrades_matrix (ui/pages/items/upgrades_matrix.py) for real
catalog items and asserts the visual contract: the base/max subheaders sit
on each rarity's TRUE base/max columns, the tint fills land only on true
base columns (shared values in the higher rarity's color), and the underline
bars span base→max with an 8px nub at each end (single-column rarities
collapse to one centered nub).
"""
import os
import re

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtWidgets  # noqa: E402

from farever_companion import paths  # noqa: E402
from farever_companion.data import items as idata  # noqa: E402
from farever_companion.ui.pages.items.upgrades_matrix import build_upgrades_matrix  # noqa: E402


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


def test_subheaders_on_true_base_and_max_columns():
    grid = _grid("Mace_Benediction")
    assert _labels(grid, 1) == [
        (1, "base"), (3, "base"), (4, "max"),      # Rare 24→30, Epic base 28
        (5, "base"), (7, "max"), (10, "max"),      # Leg base 32, Epic/Leg max
    ]
    # Guild Merchant gear expands from its shop quality: Book_Start (a
    # starter weapon sold as Rare) shows the same full Rare/Epic/Legendary
    # matrix as Mace_Benediction — same shared-base column pattern
    assert _labels(_grid("Book_Start"), 1) == [
        (1, "base"), (3, "base"), (4, "max"),      # Rare 30→69, Epic base 35
        (5, "base"), (7, "max"), (10, "max"),      # Leg base 40, Epic/Leg max
    ]


def test_subheaders_dont_stack_on_shared_columns():
    # Book_WaterOrbs L1: Epic's base shares its column with Legendary's base
    # (Faith 3 lands on col 2 for both) — the subheader row collapses to one
    # label per column instead of stacking two in one cell.
    grid = _grid("Book_WaterOrbs", level=1)
    assert _labels(grid, 1) == [
        (1, "base"), (2, "base"), (4, "max"), (6, "max"),
    ]


def test_tint_only_on_true_base_columns():
    grid = _grid("Mace_Benediction")
    # every stat row tints exactly its true base columns: Rare's base (col 1),
    # Epic's shared base 28/37/38 (col 3), Legendary's shared base 32/40/42 (5)
    for row in (2, 3, 4):
        assert _tinted_cols(grid, row) == [1, 3, 5], row
    # tinted cells render in the base rarity's color for text AND fill
    expect = {1: (83, 155, 245), 3: (194, 151, 255), 5: (240, 168, 54)}
    for col, rgb in expect.items():
        cell = _cell(grid, 2, col)
        assert _color_rgb(cell.styleSheet()) == rgb, col
        assert _fill_rgb(cell.styleSheet()) == rgb, col
    # max / step cells keep their own rarity's text with no fill
    for col in (2, 4, 7, 10):
        cell = _cell(grid, 2, col)
        assert "background:rgba" not in cell.styleSheet(), col
        assert _color_rgb(cell.styleSheet()) is not None, col


def test_tint_not_shifted_by_repeated_values():
    # A stat that gains nothing on a step repeats its value inside its run
    # (e.g. Faith at L1 stays 3 across Epic's steps, Vitality 6 across
    # Rare+Epic's). Repeats must not consume a column, or every shared base
    # after them shifts right — before the fix the shared bases tinted a
    # non-base cell and the true base column lost its tint.
    grid = _grid("Mace_Benediction", level=1)
    # Faith row: Rare base 2 (1), Epic+Legendary shared base 3 (2)
    assert _tinted_cols(grid, 2) == [1, 2]
    # Strength row (the primary's per-aptitude split): same shared base
    assert _tinted_cols(grid, 3) == [1, 2]
    # Fervor row: Rare base 28 (1), Epic base 30 (2), Legendary base 32 (3)
    # — each tint lands on its true column, none on step/max cells
    assert _tinted_cols(grid, 4) == [1, 2, 3]
    # Vitality row: Rare base 6 (1), Epic+Legendary shared base 8 (2)
    assert _tinted_cols(grid, 5) == [1, 2]


def test_value_cells_show_true_computed_value():
    """Value cells render the rounded value with the TRUE float in parens
    ('4 (3.877)') — the raw from gear_stats, so the exact computed number is
    visible next to the integer the game's tooltip rounds to. Base cells
    carry the ladder's raw directly; step cells accumulate the raw deltas.
    """
    it = idata.item("Chest_RDemon_Ass")
    idata.set_stat_display_mode("both")
    try:
        ladder = idata.upgrade_ladder(it, level=25)
        grid = build_upgrades_matrix(ladder)
        texts = {w.text() for (r, c, _, w) in _rows(grid)
                 if r >= 2 and hasattr(w, "text") and w.text()}
        assert texts                              # the matrix rendered
        for b in ladder[0]["base"]:
            expected = idata.stat_text(b["value"], b.get("raw"))
            assert expected in texts, b
            if abs((b.get("raw") or b["value"]) - b["value"]) >= 0.0005:
                assert "(" in expected, b    # a real fraction -> parens shown
        # a stat whose raw is a true fraction shows it (Armor's raw is not
        # an exact integer)
        armor = next(b for b in ladder[0]["base"] if b["label"] == "Armor")
        assert idata.stat_text(armor["value"], armor["raw"]) == \
            f"{armor['value']} ({armor['raw']:.3f}".rstrip("0").rstrip(".") + ")"
    finally:
        idata.set_stat_display_mode("rounding")


def test_value_cells_respect_display_mode():
    """Value cells follow stat_display_mode(): clean integers by default
    ('rounding'), the exact float in 'true' mode, both ('230 (230.07)') in
    'both' mode — the toggle cycles these live."""
    it = idata.item("Chest_RDemon_Ass")
    ladder = idata.upgrade_ladder(it, level=25)
    armor = next(b for b in ladder[0]["base"] if b["label"] == "Armor")
    raw_txt = f"{armor['raw']:.3f}".rstrip("0").rstrip(".")
    for mode, needle in (("rounding", str(armor["value"])),
                         ("true", raw_txt),
                         ("both", f"{armor['value']} ({raw_txt})")):
        idata.set_stat_display_mode(mode)
        grid = build_upgrades_matrix(ladder)
        texts = {w.text() for (r, c, _, w) in _rows(grid)
                 if r >= 2 and hasattr(w, "text") and w.text()}
        assert needle in texts, (mode, needle)
    idata.set_stat_display_mode("rounding")


def test_no_cell_hover_tooltips():
    grid = _grid("Mace_Benediction")
    for (r, c, _, w) in _rows(grid):
        if r >= 2 and hasattr(w, "toolTip"):
            assert w.toolTip() == "", (r, c)


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


def test_bars_span_true_base_to_max_with_nubs():
    grid = _grid("Mace_Benediction")
    bars = _bar_rows(grid)
    assert [(r, c, cs) for (r, c, cs, _) in bars] == [
        (6, 1, 4),     # Rare   24 -> 30
        (7, 3, 5),     # Epic   28 -> 38 (starts on Rare's shared 28)
        (8, 5, 6),     # Legendary 32 -> 48 (starts on Epic's shared 32)
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


def test_locked_matrix_hides_steps_show_base_only():
    """Armor AND jewelry (can't be upgraded in-game yet — only weapons can)
    render only their base column when show_steps=False, exactly like
    crafted gear. Every armor/jewelry slot is locked regardless of source —
    boss drops (Nepsilon), vendor caches (Smile of the Demolisher from the
    Chaotic Gear Cache), zone drops (Ringlet of Precision) — while weapons
    keep the full ladder.
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
    # Precision — Uncommon ring, zone drop at its authored level 20)
    jit = idata.item("Finger_Z2_Cri")
    jladder = idata.upgrade_ladder(jit, level=20)
    assert jladder and jladder[0]["steps"]          # the data keeps its steps
    jbase = build_upgrades_matrix(jladder, show_steps=False)
    assert _labels(jbase, 1) == [(1, "base")]
    for row in range(2, 2 + len(_ladder_order(jladder))):
        assert _tinted_cols(jbase, row) == [1], row
        assert len([c for (r, c, _, w) in _rows(jbase)
                    if r == row and c > 0 and hasattr(w, "text")]) == 1, row

    # default (show_steps=True) still renders the full ladder with steps
    full = build_upgrades_matrix(ladder)
    assert _labels(full, 0) == [(0, "STATS"), (1, "RARE")]
    assert _labels(full, 1) == [(1, "base"), (4, "max")]
    assert _cell(full, 2, 2) is not None             # a step column exists


def test_level_label_above_base_column():
    """With a level_label, it sits at grid (0, 1) — directly above the first
    rarity's base column — and every other row shifts down by one."""
    it = idata.item("Mace_Benediction")
    ladder = idata.upgrade_ladder(it, level=25)
    plain = build_upgrades_matrix(ladder)
    assert [(r, c, w.text()) for (r, c, _, w) in _rows(plain)
            if r == 0 and hasattr(w, "text")] == \
        [(0, 0, "STATS"), (0, 1, "RARE"), (0, 5, "EPIC"),
         (0, 8, "LEGENDARY")]
    lbl = QtWidgets.QLabel("L25")
    lab = build_upgrades_matrix(ladder, level_label=lbl)
    assert lab.indexOf(lbl) >= 0
    assert lab.getItemPosition(lab.indexOf(lbl))[:2] == (0, 1)
    # the rarity headers (and the STATS tag) shift down one row to make
    # room for the label
    assert [(r, c, w.text()) for (r, c, _, w) in _rows(lab)
            if r == 1 and hasattr(w, "text")] == \
        [(1, 0, "STATS"), (1, 1, "RARE"), (1, 5, "EPIC"),
         (1, 8, "LEGENDARY")]
