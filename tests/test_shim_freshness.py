"""The compiled shims must not be older than the sheets they were built from.

`compiler.py` rewrites a shim from its sheet, but it SKIPS one whose sheet is
missing or whose payload is empty - leaving the previously committed shim in
place. It is still big enough to clear `build-mod.bat`'s size gate, so the build
reports success while the exe carries an old list. These pin the mtime check
that catches that, and that the build actually calls it.
"""
from __future__ import annotations

import os
from pathlib import Path

from build_tools import shim_freshness as fresh

ROOT = Path(__file__).resolve().parents[1]


def _write(path: Path, mtime: float) -> Path:
    """Create `path` with an explicit mtime (no filesystem-granularity flake)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}", encoding="utf-8")
    os.utime(path, (mtime, mtime))
    return path


def test_the_vendor_list_pair_is_one_of_them():
    """The case the check exists for: raw_item_drops <- item_drops.json."""
    assert ("raw_item_drops.py", ("item_drops.json",)) in fresh.SHIM_SOURCES


def test_every_conditionally_written_shim_is_covered():
    """Every page shim the compiler can SKIP must be compared, not just the
    vendor list: the locations and the codex all go stale the same way."""
    covered = {name for name, _ in fresh.SHIM_SOURCES}
    assert {"raw_item_drops.py", "raw_craft.py",
            "raw_locs.py", "raw_codex.py"} <= covered


def test_the_location_shim_lists_every_location_sheet():
    """raw_locs is built from all six world-location sheets — a newer one means
    the shim is stale, so each has to be in the compared set."""
    locs = dict(fresh.SHIM_SOURCES)["raw_locs.py"]
    assert set(locs) == {"poi_locs.json", "mob_locs.json", "chest_locs.json",
                         "gatherable_locs.json", "critter_locs.json",
                         "orb_positions.json"}


def test_every_shipped_shim_is_classified():
    """A `raw_*.py` nobody covers would ship stale silently — the exact gap.

    Every shim on disk must be either compared (`SHIM_SOURCES`) or declared
    unconditionally rewritten (`_REWRITTEN_EVERY_BUILD`), and the two sets must
    not overlap, so a new shim has to be classified rather than forgotten.
    """
    shims = {p.name for p in (ROOT / "farever_companion" / "data").glob(
        "raw_*.py")}
    assert shims, "no raw_*.py shims found; this test would vacuously pass"
    compared = {name for name, _ in fresh.SHIM_SOURCES}
    assert compared <= shims, f"SHIM_SOURCES names a missing shim: {compared - shims}"
    assert not (compared & fresh._REWRITTEN_EVERY_BUILD), \
        "a shim is both compared and declared rewritten"
    unclassified = shims - compared - fresh._REWRITTEN_EVERY_BUILD
    assert not unclassified, (
        f"unclassified shim(s): {sorted(unclassified)} - add to SHIM_SOURCES "
        f"or _REWRITTEN_EVERY_BUILD")


def test_every_source_name_is_a_json_sheet():
    for _, sheets in fresh.SHIM_SOURCES:
        assert sheets, "a shim was listed with no source sheet"
        for name in sheets:
            assert name.endswith(".json"), name


def test_stale_ignores_a_shim_written_in_the_same_second():
    assert fresh.stale([("a.py", 5.0, "a.json", 5.0)]) == []
    assert fresh.stale([("a.py", 4.0, "a.json", 5.0)]) == [("a.py", 4.0, "a.json", 5.0)]


def test_every_covered_shim_flags_when_any_source_moves(tmp_path):
    """Table-wide: seed every listed shim and sheet, make each sheet newer, and
    every pair must come back stale — so a newly added entry cannot be inert."""
    shim_dir, sheet_dir = tmp_path / "shims", tmp_path / "sheets"
    for shim, sheets in fresh.SHIM_SOURCES:
        _write(shim_dir / shim, 1_000_000)
        for name in sheets:
            _write(sheet_dir / name, 2_000_000)       # newer than every shim
    rows = fresh.shim_ages(shim_dir, sheet_dir)
    assert {r[0] for r in rows} == {s for s, _ in fresh.SHIM_SOURCES}
    assert len(fresh.stale(rows)) == len(rows)


def test_the_newest_source_sheet_decides(tmp_path):
    shim_dir, sheet_dir = tmp_path / "shims", tmp_path / "sheets"
    _write(shim_dir / "raw_craft.py", 1_000_000)
    _write(sheet_dir / "craft.json", 900_000)
    _write(sheet_dir / "job.json", 1_000_600)
    rows = fresh.shim_ages(shim_dir, sheet_dir)
    assert [row[0] for row in rows] == ["raw_craft.py"]
    assert rows[0][2] == "job.json", "the NEWEST source sheet must be the one compared"
    assert rows[0][1] < rows[0][3]


def test_a_shim_with_no_sheet_on_disk_is_skipped(tmp_path):
    shim_dir, sheet_dir = tmp_path / "shims", tmp_path / "sheets"
    _write(shim_dir / "raw_item_drops.py", 1_000_000)
    sheet_dir.mkdir()
    assert fresh.shim_ages(shim_dir, sheet_dir) == []


def test_run_returns_the_stale_rows_and_names_them(tmp_path, capsys):
    shim_dir, sheet_dir = tmp_path / "shims", tmp_path / "sheets"
    _write(shim_dir / "raw_item_drops.py", 1_000_000)
    _write(sheet_dir / "item_drops.json", 1_003_600)
    rows = fresh.run(shim_dir, sheet_dir)
    out = capsys.readouterr().out
    assert len(rows) == 1 and rows[0][0] == "raw_item_drops.py"
    assert "WARN" in out
    assert "raw_item_drops.py" in out and "item_drops.json" in out


def test_run_reports_ok_and_returns_nothing_when_fresh(tmp_path, capsys):
    shim_dir, sheet_dir = tmp_path / "shims", tmp_path / "sheets"
    _write(shim_dir / "raw_item_drops.py", 1_000_600)
    _write(sheet_dir / "item_drops.json", 1_000_000)
    assert fresh.run(shim_dir, sheet_dir) == []
    assert "OK" in capsys.readouterr().out


def test_run_skips_when_there_is_nothing_to_compare(tmp_path, capsys):
    assert fresh.run(tmp_path / "no-shims", tmp_path / "no-sheets") == []
    assert "SKIPPED" in capsys.readouterr().out


def test_a_stale_shim_never_fails_the_check(tmp_path):
    """Warn-only: the exit path must not raise or signal failure."""
    shim_dir, sheet_dir = tmp_path / "shims", tmp_path / "sheets"
    _write(shim_dir / "raw_item_drops.py", 1_000_000)
    _write(sheet_dir / "item_drops.json", 2_000_000)
    assert isinstance(fresh.run(shim_dir, sheet_dir), list)


def test_age_text_stays_readable():
    assert fresh._age_text(30) == "30s"
    assert fresh._age_text(600) == "10m"
    assert fresh._age_text(7200) == "2.0h"


def test_the_check_is_wired_into_the_build():
    """A build check nothing calls is not a build check."""
    verify = (ROOT / "build_tools" / "verify_assets.py").read_text(encoding="utf-8")
    build = (ROOT / "build-mod.bat").read_text(encoding="utf-8")
    assert "shim_freshness" in verify, (
        "verify_assets.py must run the freshness check so it reports in the build"
    )
    assert "shim_freshness.py" in build, (
        "build-mod.bat must run it before packaging, or a stale shim is only "
        "noticed after the exe is built"
    )
