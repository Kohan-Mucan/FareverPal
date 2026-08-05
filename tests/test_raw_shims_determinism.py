"""Determinism guard: committed raw_*.py shims must equal a fresh rebuild.

The raw_*.py shims are compiled from assets/data by compiler.py. They embed
the game data as zlib+base85-compressed JSON and must be a pure function of
the inputs — rebuilding from unchanged sheets must produce byte-identical
files. That keeps `git status` clean after every build-mod and means a commit
only happens when the game data actually changed.

This test recompiles into a temp dir (never touching the working tree) and
compares every shim against the committed version. It SKIPS when the source
sheets are absent (assets/data and assets/atlas are gitignored and only exist
after extracting the game files), so it acts as a local dev guard: run it
after re-extracting data to confirm whether the shims need a rebuild.

Note: raw_shop.py is a hand-written placeholder (the compiler only writes it
when the dump ships shop.json) and is excluded on purpose.
"""

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

ASSETS_DATA = PROJECT_ROOT / "assets" / "data"
ASSETS_ATLAS = PROJECT_ROOT / "assets" / "atlas"
SHIMS_DIR = PROJECT_ROOT / "farever_companion" / "data"

# raw_shop.py is hand-written, not compiler output.
EXPECTED_SHIMS = ("raw_codex", "raw_units", "raw_items", "raw_skills", "raw_data")


def _inputs_present() -> bool:
    """The compiler needs the extracted sheets AND the atlas to reproduce the
    committed shims (without the atlas it builds the non-stripped variant)."""
    return (
        ASSETS_DATA.exists()
        and any(ASSETS_DATA.glob("*.json"))
        and any(ASSETS_ATLAS.glob("atlas_*.json"))
    )


@pytest.mark.skipif(
    not _inputs_present(),
    reason="assets/data or assets/atlas not extracted (gitignored); run Update_Raw_Data.bat first",
)
def test_committed_shims_match_fresh_rebuild(tmp_path):
    import compiler  # local import so missing source data can't break collection

    compiler.compile_to_py(PROJECT_ROOT, PROJECT_ROOT, tmp_path / "raw_data.py",
                           dev_dir=tmp_path / "tmp_preview")

    missing = []
    changed = []
    for stem in EXPECTED_SHIMS:
        rebuilt = tmp_path / f"{stem}.py"
        committed = SHIMS_DIR / f"{stem}.py"
        if not rebuilt.exists():
            missing.append(f"{stem}.py")
        elif not committed.exists():
            missing.append(f"committed {stem}.py")
        elif rebuilt.read_bytes() != committed.read_bytes():
            changed.append(stem)

    assert not missing, f"compiler did not emit: {missing}"
    assert not changed, (
        "raw_*.py shims differ from a fresh rebuild — the committed shims are "
        f"stale or the build is not deterministic: {changed}. "
        "Re-extract the game sheets and re-run compiler.py (Update_Raw_Data.bat), "
        "then commit the regenerated shims."
    )
