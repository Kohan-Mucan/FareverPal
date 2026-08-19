"""The UPGRADE LADDER staircase matrix: one row per stat, each rarity's
deduped value run in its own block with rarity headers, base/max subheaders
aligned to the true base/max columns, base-column cells tinted in the
rarity color, and stacked underline rows with a nub at each end. The
matrix sizes its value columns to their content and scrolls horizontally
instead of crushing the cells when the pane is narrow.
"""
from __future__ import annotations

from PySide6 import QtGui, QtCore, QtWidgets

from ... import theme
from ....data import items as idata

# value-column floor: two digits plus the cell padding. Sized so the widest
# ladder (4 rarities × 10 columns) fits the detail pane at the minimum
# window width — no scrollbar, no squeezed cells.
VALUE_COL_FLOOR = 24
# stat-name column: LOCKED to one fixed width (never grown per item) so the
# matrix doesn't shift when the selected item's widest stat name changes.
# Sized to the WRAPPED shape, not the full phrase: two-word names split onto
# two lines ("Armor / Penetration"), so the widest LINE is "Penetration" at
# 69px (the app's bundled Inter, 12px DemiBold) — plus its 6px QSS padding
# and slack. The full one-line phrase needs ~115px; the wrap keeps this
# column at the second-line width, and 82px keeps the widest ladder inside
# the detail pane with no horizontal scrollbar.
NAME_COL_FLOOR = 82


# font metrics are cached (the matrix rebuilds on every level change, so
# constructing a QFontMetrics per cell would be wasteful). Both fonts use
# pixel sizes so the measurements match the QSS `font-size:11px` rendering.
_FM_CACHE: dict[tuple[str, int], QtGui.QFontMetrics] = {}


def _metrics(family: str, px: int,
             weight: QtGui.QFont.Weight = QtGui.QFont.Weight.Normal) \
        -> QtGui.QFontMetrics:
    """Cached QFontMetrics for `family` at `px` pixel size and `weight` —
    the bold cut of the mono font is measurably wider than regular, so
    bold labels must be measured at their real weight."""
    key = (family, px, int(weight))
    fm = _FM_CACHE.get(key)
    if fm is None:
        f = QtGui.QFont(family)
        f.setPixelSize(px)
        f.setWeight(weight)
        fm = QtGui.QFontMetrics(f)
        _FM_CACHE[key] = fm
    return fm


def _text_px(text: str, family: str, px: int, pad: int,
             buf: int = 2,
             weight: QtGui.QFont.Weight = QtGui.QFont.Weight.Normal) -> int:
    """Pixels `text` needs in `family` at `px`, plus `pad` of horizontal
    padding and `buf` slack so anti-aliasing never shaves a glyph.
    `weight` must match the label's rendered cut (value cells and
    subheaders render bold)."""
    return _metrics(family, px, weight).horizontalAdvance(text) + pad + buf


def _mono_font(px: int,
               weight: QtGui.QFont.Weight = QtGui.QFont.Weight.Normal) \
        -> QtGui.QFont:
    """The matrix's mono font at `px` pixel size, set directly on labels so
    their size hints use the real mono metrics (at build time the
    stylesheet fonts aren't polished yet)."""
    f = QtGui.QFont(theme.MONO_FONT)
    f.setPixelSize(px)
    f.setWeight(weight)
    return f


def build_upgrades_matrix(ladder: list[dict],
                          level_label: QtWidgets.QWidget | None = None,
                          show_steps: bool = True,
                          ) -> QtWidgets.QGridLayout:
    """The staircase matrix: one row per stat — each rarity's ladder shows
    its unique values with rarity headers and base/max subheaders, base
    cells tinted in the rarity color (shared values in the higher rarity's
    color), and stacked underline rows per rarity with a nub at each end.
    `level_label`, when given, sits above the matrix's base column.
    `show_steps=False` drops the upgrade-step columns (crafted gear is
    fixed-level and can't be upgraded)."""
    grid = QtWidgets.QGridLayout()
    grid.setHorizontalSpacing(1)
    grid.setVerticalSpacing(2)
    # zero margins: the card's own padding already separates the matrix from
    # the pane edge, and every pixel counts toward the no-scrollbar goal
    grid.setContentsMargins(0, 0, 0, 0)

    # per-column minimum widths sized to the widest cell actually in that
    # column (values, base/max subheaders, level label) — so a column is
    # exactly as wide as its content needs and no wider
    col_min: dict[int, int] = {}

    def _bump(c_idx: int, text: str, family: str, px: int, pad: int,
              buf: int = 2,
              weight: QtGui.QFont.Weight = QtGui.QFont.Weight.Normal) \
            -> None:
        col_min[c_idx] = max(col_min.get(c_idx, 0),
                             _text_px(text, family, px, pad, buf, weight))

    order: list[str] = []
    lanes: dict[str, list[dict]] = {}
    for r in ladder:
        base = {b["label"]: b["value"] for b in r["base"]}
        base_raw = {b["label"]: b.get("raw", float(b["value"]))
                    for b in r["base"]}
        for label in base:
            if label not in order:
                order.append(label)
        for label in order:
            run = base.get(label, 0)
            raw_run = base_raw.get(label, float(run))
            vals = [run]
            raws = [raw_run]
            if show_steps:
                for s in r["steps"]:
                    g = next((g for g in s["gains"] if g["label"] == label),
                             None)
                    run += g["value"] if g else 0
                    raw_run += g.get("raw", float(g["value"])) if g else 0.0
                    vals.append(run)
                    raws.append(raw_run)
            lanes.setdefault(label, []).append(
                {"rarity": r["rarity"], "vals": vals, "raws": raws})

    if not order:
        return grid

    # the level label occupies a top row above the base column, pushing every
    # other row down by one when present
    top = 1 if level_label is not None else 0

    rars = [l["rarity"] for l in lanes[order[0]]] if order else []

    # Width of each rarity block = max count of NEW (unique) values across stats
    widths: dict[str, int] = {}
    for ls in lanes.values():
        seen: set[int] = set()
        for lane in ls:
            n_new = 0
            for v in lane["vals"]:
                if v not in seen:
                    seen.add(v)
                    n_new += 1
            widths[lane["rarity"]] = max(widths.get(lane["rarity"], 0), n_new)

    mono = f'font-family:"{theme.MONO_FONT}", "Consolas", monospace;'

    # Map column start and width for each rarity block
    col_map: dict[str, tuple[int, int]] = {}
    cur_col = 1
    for rar in rars:
        wdt = max(widths.get(rar, 1), 1)
        col_map[rar] = (cur_col, wdt)
        cur_col += wdt

    grid.setColumnMinimumWidth(0, NAME_COL_FLOOR)

    if level_label is not None:
        if isinstance(level_label, QtWidgets.QLabel):
            level_label.setAlignment(QtCore.Qt.AlignCenter)
        # col 1 is the first rarity's base column — the label hovers directly
        # above the base cell it describes
        grid.addWidget(level_label, 0, 1, 1, 1)
        # pad 2 covers the #Mono letter-spacing (font metrics don't see it)
        _bump(1, level_label.text() or "L99", theme.MONO_FONT, 12, 2)

    # pre-pass — map every value (duplicates included) to the column it is
    # displayed in, so the base/max subheaders and the cells can point at a
    # rarity's TRUE base / max position even when the value is shown inside
    # a lower rarity's block. Columns are counted in NEW-value order: a stat
    # that gains nothing on a step repeats its value inside the run, and
    # those repeats must not consume a column (the old raw-index count moved
    # every shared base/max after the first repeat one column to the right).
    val_cols: dict[tuple[str, int], int] = {}
    for label in order:
        seen: set[int] = set()
        for lane in lanes[label]:
            col_start, wdt = col_map[lane["rarity"]]
            k = 0
            for v in lane["vals"]:
                if v in seen:
                    continue
                if (label, v) not in val_cols:
                    val_cols[(label, v)] = col_start + k
                seen.add(v)
                k += 1

    # Row 0 (top): the stat-name column's own header — a small muted STATS
    # tag on the same row as the rarity headers, so the label column reads
    # as a header instead of floating bare above the first value row.
    sh = QtWidgets.QLabel("STATS")
    sh.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
    sh.setFont(_mono_font(11, QtGui.QFont.Weight.Bold))
    sh.setStyleSheet(
        f"color:{theme.DIM};{mono}font-size:11px;font-weight:700;"
        "letter-spacing:1px;background:transparent;padding:3px 6px 3px 2px;")
    grid.addWidget(sh, top, 0)

    # Row 0: Rarity Name Headers (clean, no side borders). A header spans its
    # whole rarity block; a spanning label wider than its block would clip,
    # so the block's columns are grown to fit in the final width pass below.
    header_needs: dict[str, int] = {}
    for rar in rars:
        col_start, wdt = col_map[rar]
        rcol = theme.rarity_color(rar)
        bg_alpha = theme.with_alpha(rcol, 22)

        hl = QtWidgets.QLabel(rar.upper())
        hl.setAlignment(QtCore.Qt.AlignCenter)
        hl.setFont(_mono_font(11, QtGui.QFont.Weight.Bold))
        # mono family is essential here: without it the header falls back to
        # the app's Inter font, which is far wider and clips (the original
        # stylesheet omitted font-family, so LEGENDARY rendered ~100px in a
        # 92px block)
        hl.setStyleSheet(
            f"color:{rcol};{mono}font-size:11px;font-weight:700;"
            f"letter-spacing:1px;background:{bg_alpha};"
            "border-radius:4px;padding:3px 6px;")
        grid.addWidget(hl, top, col_start, 1, wdt)
        # the label's own sizeHint is the ground truth for how wide the
        # rendered header is — it accounts for the bold cut, the 1px
        # letter-spacing and the padding, none of which plain font metrics
        # capture reliably (the block is grown to this below)
        header_needs[rar] = hl.sizeHint().width() + 2

    # Row 1: base / max subheaders — each aligned to its rarity's TRUE base /
    # max column (the same columns the base cells tint and the bar nubs sit
    # on), so the labels line up with the values they describe instead of
    # splitting the rarity block in half. Labels are deduped per column —
    # rarities can share a base column, and one rarity's max can land on
    # another's base — so a column renders at most one label (the highest
    # rarity wins the color) instead of several stacking in one cell.
    base_cols: dict[int, str] = {}
    max_cols: dict[int, str] = {}
    for rar in rars:
        col_start, wdt = col_map[rar]
        start_cols, end_cols = [], []
        for label in order:
            lane = next((l for l in lanes[label] if l["rarity"] == rar), None)
            if lane and lane["vals"]:
                b = val_cols.get((label, lane["vals"][0]))
                e = val_cols.get((label, lane["vals"][-1]))
                if b is not None:
                    start_cols.append(b)
                if e is not None:
                    end_cols.append(e)
        b_col = min(start_cols) if start_cols else col_start
        e_col = max(end_cols) if end_cols else (col_start + wdt - 1)
        base_cols[b_col] = rar          # rars ascend -> highest overwrites
        if e_col != b_col:
            max_cols[e_col] = rar

    for c in sorted(base_cols):
        rcol = theme.rarity_color(base_cols[c])
        lbl_base = QtWidgets.QLabel("base")
        lbl_base.setAlignment(QtCore.Qt.AlignCenter)
        lbl_base.setFont(_mono_font(12))
        lbl_base.setStyleSheet(
            f"color:{theme.with_alpha(rcol, 220)};{mono}font-size:12px;font-weight:700;"
            "background:transparent;padding:2px 3px;")
        grid.addWidget(lbl_base, top + 1, c, 1, 1)
        _bump(c, "base", theme.MONO_FONT, 12, 6, 2,
              weight=QtGui.QFont.Weight.Bold)
    for c in sorted(max_cols):
        if c in base_cols:
            continue                   # already labeled as a shared base
        rcol = theme.rarity_color(max_cols[c])
        lbl_max = QtWidgets.QLabel("max")
        lbl_max.setAlignment(QtCore.Qt.AlignCenter)
        lbl_max.setFont(_mono_font(12, QtGui.QFont.Weight.Bold))
        lbl_max.setStyleSheet(
            f"color:{rcol};{mono}font-size:12px;font-weight:700;"
            "background:transparent;padding:2px 3px;")
        grid.addWidget(lbl_max, top + 1, c, 1, 1)
        _bump(c, "max", theme.MONO_FONT, 12, 6, 2,
              weight=QtGui.QFont.Weight.Bold)

    # Data Rows (Row 2 .. 2+len(order)-1)

    for r_idx, label in enumerate(order):
        row = top + 2 + r_idx
        # long two-word names (Armor Penetration…) break onto two lines
        # deterministically — a 150px single-line name used to get crushed
        # into a 30px column (Qt's wordWrap can't break a long word, so a
        # manual line break is the only reliable wrap)
        disp = label.replace(" ", "\n") if (" " in label
                and len(label) > 8) else label
        rl = QtWidgets.QLabel(disp)
        # the name column is a LOCKED standard width — the label is pinned to
        # it so the column can never be pushed wider by a long stat name (and
        # never shrinks when the item changes), keeping the matrix from
        # shifting. NAME_COL_FLOOR fits the widest line ("Penetration"), so
        # no name clips.
        rl.setFixedWidth(NAME_COL_FLOOR)
        rl.setStyleSheet(
            f"color:{theme.TEXT};font-weight:600;font-size:12px;background:transparent;padding:4px 4px 4px 2px;")
        grid.addWidget(rl, row, 0)

        seen: set[int] = set()
        lane_new: list[list] = []
        # pass 1 — the per-lane deduped value runs (val_cols was already
        # mapped for every value in the header pre-pass above)
        for lane in lanes[label]:
            vals = lane["vals"]
            lane_new.append([(i, v) for i, v in enumerate(vals)
                             if v not in seen and not seen.add(v)])

        # pass 2 — every rarity's TRUE base column (max/end columns are NOT
        # tinted; the nubs on the underline bars mark the ends). base_at lists
        # the rarities whose base sits on each column in ascending rarity
        # order, so the last entry is the highest rarity and wins a shared
        # cell's tint.
        base_at: dict[int, list[str]] = {}
        for lane in lanes[label]:
            rar = lane["rarity"]
            vals = lane["vals"]
            if not vals:
                continue
            b_col = val_cols.get((label, vals[0]))
            if b_col is not None:
                base_at.setdefault(b_col, []).append(rar)

        # pass 3 — the value cells: tint only the cells sitting on each
        # rarity's true base column in that rarity's color (even a shared
        # value shown inside a lower rarity's block); max/end cells stay
        # plain — the nubs on the underline bars mark the ends
        for lane, new in zip(lanes[label], lane_new):
            rar = lane["rarity"]
            col_start, wdt = col_map[rar]
            rcol = theme.rarity_color(rar)

            for k, (i, v) in enumerate(new):
                c_idx = col_start + k
                val_cols[(label, v)] = c_idx
                # the cell text shows the rounded value with the TRUE float
                # in parens ('4 (3.877)'); the column is sized to its widest
                # text (the final pass below applies the minimums once every
                # cell is placed)
                txt = idata.stat_text(v, lane["raws"][i])
                _bump(c_idx, txt, theme.MONO_FONT, 13, 4,
                      weight=QtGui.QFont.Weight.Bold)

                cell = QtWidgets.QLabel(txt)
                cell.setAlignment(QtCore.Qt.AlignCenter)
                # tint only the true base columns — max/end cells stay plain.
                # An overlap value (shared between rarities) displays as the
                # HIGHEST rarity whose base sits on it, text + fill in that
                # rarity's color, so it reads as that base instead of two
                # blended rarities in one cell.
                bases = base_at.get(c_idx)
                if bases:
                    mrar = bases[-1]
                    mcol = theme.rarity_color(mrar)
                    bg = f"background:{theme.with_alpha(mcol, 30)};"
                    weight = 700
                else:
                    mrar = rar
                    mcol = rcol
                    bg = "background:transparent;"
                    weight = 600
                cell.setStyleSheet(
                    f"color:{mcol};{mono}font-size:13px;font-weight:{weight};"
                    f"{bg}padding:4px 2px;")

                grid.addWidget(cell, row, c_idx)

    # value columns get exactly the width their widest cell needs (with a
    # two-digit floor) — the grid can then never squeeze a cell below its
    # content and clip the number
    for c_idx, w in col_min.items():
        grid.setColumnMinimumWidth(c_idx, max(VALUE_COL_FLOOR, w))

    # grow each rarity block so its spanning header never clips: a header
    # wider than its block would be cut off, so the deficit is spread
    # evenly across the block's columns (keeps the columns uniform)
    for rar, (col_start, wdt) in col_map.items():
        hdr = header_needs[rar]
        block = sum(grid.columnMinimumWidth(c)
                    for c in range(col_start, col_start + wdt)) \
            + (wdt - 1) * grid.horizontalSpacing()
        if hdr > block:
            extra = -(-(hdr - block) // wdt)   # ceil(hdr-block / wdt)
            for c in range(col_start, col_start + wdt):
                grid.setColumnMinimumWidth(c,
                                           grid.columnMinimumWidth(c) + extra)

    # Dedicated Stacked Underline Rows per Rarity Tier (Uncommon, Rare, Epic, Legendary)
    # Underline bars start where each rarity's values begin in the matrix (including overlap start)
    base_bar_row = top + 2 + len(order)
    for rar_idx, rar in enumerate(rars):
        rrow = base_bar_row + rar_idx
        col_start, wdt = col_map[rar]
        rcol = theme.rarity_color(rar)

        # Find full span from first value to last value across stats for this rarity
        start_cols, end_cols = [], []
        for label in order:
            lane = next((l for l in lanes[label] if l["rarity"] == rar), None)
            if lane and lane["vals"]:
                v_start, v_end = lane["vals"][0], lane["vals"][-1]
                if (label, v_start) in val_cols:
                    start_cols.append(val_cols[(label, v_start)])
                if (label, v_end) in val_cols:
                    end_cols.append(val_cols[(label, v_end)])

        c_s = min(start_cols) if start_cols else col_start
        c_e = max(end_cols) if end_cols else (col_start + wdt - 1)
        bar_wdt = max(1, c_e - c_s + 1)

        if c_s == c_e:
            # degenerate range (one column): base and max are the same
            # value, so a single centered nub marks it instead of a bar
            # with two ends 100px apart implying a range that isn't there
            nub = QtWidgets.QFrame()
            nub.setFixedSize(8, 8)
            nub.setStyleSheet(
                f"background:{rcol};border-radius:4px;border:0;")
            grid.addWidget(nub, rrow, c_s, 1, 1,
                           QtCore.Qt.AlignCenter)
            continue

        # the underline bar with a small filled nub at each end, so a
        # rarity's base and max points read at a glance under the matrix
        nub_css = f"background:{rcol};border-radius:4px;border:0;"
        nub_l = QtWidgets.QFrame()
        nub_l.setFixedSize(8, 8)
        nub_l.setStyleSheet(nub_css)
        nub_r = QtWidgets.QFrame()
        nub_r.setFixedSize(8, 8)
        nub_r.setStyleSheet(nub_css)
        bar = QtWidgets.QFrame()
        bar.setFixedHeight(4)
        bar.setStyleSheet(
            f"background:{rcol};border-radius:2px;")
        row_w = QtWidgets.QWidget()
        row_l = QtWidgets.QHBoxLayout(row_w)
        row_l.setContentsMargins(0, 0, 0, 0)
        row_l.setSpacing(0)
        row_l.addWidget(nub_l, 0, QtCore.Qt.AlignVCenter)
        row_l.addWidget(bar, 1, QtCore.Qt.AlignVCenter)
        row_l.addWidget(nub_r, 0, QtCore.Qt.AlignVCenter)
        grid.addWidget(row_w, rrow, c_s, 1, bar_wdt)

    grid.setColumnStretch(cur_col, 1)
    return grid


def build_upgrades_matrix_scroll(ladder: list[dict],
                                 level_label: QtWidgets.QWidget | None = None,
                                 show_steps: bool = True,
                                 ) -> QtWidgets.QScrollArea:
    """The UPGRADE LADDER matrix wrapped so its cells are never squeezed:
    the grid keeps its content-sized width inside a transparent horizontal
    scroll area, so a narrow pane scrolls sideways instead of clipping the
    numbers. `show_steps` is forwarded to build_upgrades_matrix.
    """
    grid = build_upgrades_matrix(ladder, level_label=level_label,
                                 show_steps=show_steps)
    host = QtWidgets.QWidget()
    host.setLayout(grid)
    grid.activate()   # finalize the minimum sizes (two-line names, etc.)
    # the host shrinks to the pane but never below its content width — a
    # scrollbar appears only when the pane is genuinely narrower than the
    # widest ladder, instead of squeezing the cells (squeezing clips the
    # values)
    host.setMinimumWidth(grid.minimumSize().width())
    sa = QtWidgets.QScrollArea()
    sa.setWidgetResizable(True)
    sa.setFrameShape(QtWidgets.QFrame.NoFrame)
    sa.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
    sa.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
    sa.setStyleSheet("QScrollArea{background:transparent;border:0;}")
    sa.setWidget(host)
    # pin the scroll area to the matrix height + room for the horizontal
    # scrollbar (a QScrollArea's sizeHint doesn't track the widget's fixed
    # height, so the parent layout would otherwise shrink it and clip the
    # bottom bar row)
    sa.setFixedHeight(grid.minimumSize().height() + 14)
    return sa
