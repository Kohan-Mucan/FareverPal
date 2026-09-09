"""Warn when a compiled shim is older than the sheet it was built from.

`compiler.py` rewrites each shim from its source sheet, so a tree that was just
compiled is fresh by construction. The gap this closes is the silent one: when
the compile SKIPS a shim - `item_drops.json` missing, its payload empty, or the
read raised and the warning scrolled past - the previously committed shim is
left in place. It is still large enough to clear the size gate in
`build-mod.bat`, so nothing else notices, and the exe ships data older than the
sheets it claims to describe. Every shim the compiler writes behind a guard is
covered (`SHIM_SOURCES`) - the vendor list, the crafting sheet, the shop, the
world locations and the codex; the shims it rewrites unconditionally cannot go
stale this way (`_REWRITTEN_EVERY_BUILD`).

A shim older than its sheet is exactly that state, so this compares mtimes. It
WARNS and never fails: the caller may be verifying a tree it did not compile,
and a stale-data warning must not block an otherwise fine build. It still runs
inside the build (verify step and the pre-package step), which is what keeps a
stale vendor list from shipping *silently*.

`assets/data/` is git-ignored, so a checkout without it has nothing to compare
and reports SKIPPED rather than guessing.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# shim -> the sheet(s) it is compiled from. These are the shims the compiler
# writes CONDITIONALLY (a missing source sheet, an empty payload, or a raised
# read skips the write and leaves the previously committed file in place) — the
# only way a shim goes stale while the build still passes. The comparison uses
# the NEWEST source sheet, so a multi-sheet shim is covered the moment any of
# its inputs moves. Every shim is either listed here or declared in
# `_REWRITTEN_EVERY_BUILD`; `test_every_shipped_shim_is_classified` enforces it.
SHIM_SOURCES = (
    ("raw_item_drops.py", ("item_drops.json",)),
    ("raw_craft.py", ("craft.json", "job.json")),
    # The location shims: every world-location sheet the `raw_locs` block reads
    # (see `_compile_loc_datasets` / the location loop in compile_to_py).
    ("raw_locs.py", ("poi_locs.json", "mob_locs.json", "chest_locs.json",
                     "gatherable_locs.json", "critter_locs.json",
                     "orb_positions.json")),
    # Multi-input, but still a dedicated page shim written behind
    # `codex_order.json`'s existence guard: it also reads codex.json, ach.json,
    # zone.json, dungeons.json and the two spawn sheets (plus the units/items it
    # inherits from the standard pipeline, which are covered by the newest-sheet
    # comparison the same way).
    ("raw_codex.py", ("codex.json", "codex_order.json", "ach.json",
                      "zone.json", "dungeons.json", "mob_locs.json",
                      "critter_locs.json")),
)

#: Shims the compiler rewrites on EVERY run (`write_data_file` at the end of
#: `compile_to_py`), so a skipped write cannot leave one stale — there is no
#: "source sheet is newer than the shim" state to catch. `raw_data.py` is the
#: aggregate for the same reason plus one more: it bundles a dozen sheets and
#: the ever-rewritten `_version.json`, whose mtime would make every comparison
#: warn. Named here (not merely omitted) so a new shim must be classified.
_REWRITTEN_EVERY_BUILD = frozenset({
    "raw_units.py", "raw_items.py", "raw_skills.py", "raw_data.py",
})

SHIM_DIR = ROOT / "farever_companion" / "data"
SHEET_DIR = ROOT / "assets" / "data"


def _age_text(seconds: float) -> str:
    """A compact human age for a positive delta (e.g. ``45m``, ``3.2h``)."""
    if seconds < 90:
        return f"{seconds:.0f}s"
    minutes = seconds / 60
    if minutes < 90:
        return f"{minutes:.0f}m"
    hours = minutes / 60
    if hours < 48:
        return f"{hours:.1f}h"
    return f"{hours / 24:.1f}d"


def shim_ages(shim_dir: Path = SHIM_DIR, sheet_dir: Path = SHEET_DIR,
              table=SHIM_SOURCES) -> list[tuple[str, float, str, float]]:
    """`(shim name, shim mtime, newest sheet name, sheet mtime)` per pair.

    Only pairs where BOTH the shim and at least one source sheet exist: a shim
    with no sheet on disk has nothing to be compared against, and a sheet with
    no shim is the size/fallback checks' business, not this one's.
    """
    rows = []
    for shim_name, sheet_names in table:
        shim = shim_dir / shim_name
        sheets = [sheet_dir / name for name in sheet_names]
        present = [p for p in sheets if p.is_file()]
        if not shim.is_file() or not present:
            continue
        newest = max(present, key=lambda p: p.stat().st_mtime)
        rows.append((shim_name, shim.stat().st_mtime, newest.name,
                     newest.stat().st_mtime))
    return rows


def stale(rows) -> list:
    """The rows whose shim is older than the sheet it was built from.

    Pure, on ``shim_ages``' rows; equal mtimes count as fresh (a same-second
    write must not be reported as stale).
    """
    return [row for row in rows if row[1] < row[3]]


def run(shim_dir: Path = SHIM_DIR, sheet_dir: Path = SHEET_DIR) -> list:
    """Print the freshness line and return the stale rows (never fails a build)."""
    rows = shim_ages(shim_dir, sheet_dir)
    if not rows:
        print("Shim Freshness : SKIPPED (no shim and source sheet pair on disk)")
        return []
    bad = stale(rows)
    if not bad:
        print(f"Shim Freshness : OK ({len(rows)} compiled shim(s) newer than "
              f"their source sheet)")
        return []
    print(f"Shim Freshness : WARN - {len(bad)} compiled shim(s) are OLDER than "
          f"the sheet they were built from:")
    for shim, shim_mtime, sheet, sheet_mtime in bad:
        print(f"    {shim} is {_age_text(sheet_mtime - shim_mtime)} older "
              f"than {sheet}")
    print("    -> re-run compiler.py (or Update_Raw_Data.bat) so the shim "
          "carries the current list")
    return bad


if __name__ == "__main__":
    run()
    sys.exit(0)          # warn-only: this check never fails a build
