"""`unit.flags` bits are named once and shared by the compiler and the runtime.

`farever_companion/unit_flags.py` exists because the two readers disagreed
about the same bitmask: `data/units.py` called `0x80` "unique" while the
compiler used `0x80` as a dungeon fallback, and the compiler used `0x40` for the
"unique -> drops Spark" test the runtime spelled `0x80`. These tests pin the
shared names, that the compiler no longer spells the bare masks, and the
MEASURED meaning of each bit against the shipped `unit.json`.

The rows come from the compiled shims (`cdb.by_id`), not the loose sheet, so
these run in CI. The id memberships are deliberately specific: they are what a
game patch would move, and that is exactly when the bit meanings want a second
look.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from farever_companion import unit_flags as F
from farever_companion.data import units

ROOT = Path(__file__).resolve().parents[1]


def _rows() -> dict[str, dict]:
    return units._units_by_id()


def _has(row: dict, bit: int) -> bool:
    flags = row.get("flags")
    return isinstance(flags, int) and bool(flags & bit)


def _ids_with(bit: int) -> set[str]:
    return {uid for uid, row in _rows().items() if _has(row, bit)}


def _loose_rows() -> dict[str, dict]:
    """Rows straight off the game sheet — needed for NO_CODEX, whose rows are
    dropped from the compiled unit sheet and survive only as flagless display
    rows. Skips without the game dump, like the other loose-sheet tests."""
    import json
    from farever_companion import paths
    sheet = paths.sheets_dir() / "unit.json"
    if not sheet.exists():
        pytest.skip("source unit sheet not present")
    raw = json.loads(sheet.read_text(encoding="utf-8"))
    rows = (raw if isinstance(raw, list)
            else (raw.get("lines") or raw.get("items") or []))
    return {r.get("id"): r for r in rows if r.get("id")}


def test_the_compiler_and_the_runtime_share_one_name_per_bit():
    """One name per bit, imported by both readers — the compiler must not go
    back to spelling the raw masks that made 0x40/0x80 drift."""
    # the runtime keeps its public names, now sourced from the shared module
    assert units.BOSS_FLAG_BIT == F.BOSS_FLAG_BIT
    assert units.UNIQUE_FLAG_BIT == F.UNIQUE_FLAG_BIT

    # ...and IMPORTS them: equal values alone would not notice units.py going
    # back to defining its own copy of the same bit
    usrc = (ROOT / "farever_companion" / "data" / "units.py").read_text(
        encoding="utf-8")
    assert "from ..unit_flags import" in usrc
    assert "BOSS_FLAG_BIT =" not in usrc and "UNIQUE_FLAG_BIT =" not in usrc

    src = (ROOT / "compiler.py").read_text(encoding="utf-8")
    for name in ("is_no_codex", "is_special_flag", "SPARK_PROXY_FLAG_BIT",
                 "UNIQUE_FLAG_BIT"):
        assert name in src, name
    for bare in ("(1 << 6)", "flags & 128", "fl & 0x40000"):
        assert bare not in src, f"compiler.py spells the raw mask again: {bare}"


def test_boss_bit_marks_bosses_and_not_trash():
    """`0x10` is the boss bit — the one bit whose reading is not in doubt."""
    boss = _ids_with(F.BOSS_FLAG_BIT)
    assert {"Nepsilon", "Reblochonk", "Phrixes", "Gatsbee"} <= boss, sorted(boss)
    assert "Boar_Z2W_U" not in boss
    # a small set: a flag that swept in a third of the table would not be a boss bit
    assert len(boss) < 40, len(boss)


def test_no_codex_bit_marks_the_internal_rows():
    """`0x40000` is what the compiler drops (Dummy, internal variants, portal).

    The dropped rows leave the compiled unit sheet, so the bit is read from the
    loose sheet — and the drop itself is asserted alongside it: a flagged row
    must not still be carrying its `flags` in the shipped table."""
    loose = _loose_rows()
    internal = {uid for uid, row in loose.items()
                if _has(row, F.NO_CODEX_FLAG_BIT)}
    assert "Dummy" in internal
    assert "ChaoticPortal" in internal
    assert "Nepsilon" not in internal
    compiled = _rows()
    for uid in internal:
        assert not _has(compiled.get(uid) or {}, F.NO_CODEX_FLAG_BIT), uid


def test_unique_and_spark_proxy_are_different_predicates():
    """`0x80` (units.py is_unique) and `0x40` (the compiler's Spark proxy) are
    NOT the same set — the divergence that made one shared name necessary. It is
    recorded, not resolved: neither value may be silently switched for the
    other, which is why each difference side is asserted non-empty."""
    unique = _ids_with(F.UNIQUE_FLAG_BIT)
    proxy = _ids_with(F.SPARK_PROXY_FLAG_BIT)
    assert unique != proxy
    assert proxy - unique, "no 0x40-only unit — the predicates collapsed"
    assert unique - proxy, "no 0x80-only unit — the predicates collapsed"


def test_spark_bit_is_the_real_one_and_the_proxy_is_not():
    """`0x400000` tracks Spark directly; the compiler's `0x40` proxy both
    over- and under-selects it. This is the measured case for keeping the proxy
    as a proxy rather than renaming it to \"Spark\"."""
    spark = _ids_with(F.SPARK_FLAG_BIT)
    proxy = _ids_with(F.SPARK_PROXY_FLAG_BIT)
    # the real spark bit catches the `_Spark` companions the proxy misses
    assert {"Frog_Spark", "Rabbit_Spark"} <= spark, sorted(spark)
    assert spark - proxy, "the proxy now covers every Spark id"
    assert proxy - spark, "the proxy is now exactly the Spark bit"
