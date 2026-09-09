"""Deprecated shim: the `unit.flags` bits now live in `game_rules.py`.

Kept so the names keep resolving from this module, where they have always lived
(`data/units.py` re-exports two of them and `tests/test_unit_flags.py` reads
them), with no call site moving. The measured table, the confidence notes and the
one-job-per-bit rule are in the canonical leaf at the repo root; the build loads
that directly and bundles a copy into `farever_companion/rules.py`, which is what
this module (and the rest of the app) re-exports.
"""
from .rules import (  # noqa: F401  (re-exported on purpose)
    BOSS_FLAG_BIT,
    NO_CODEX_FLAG_BIT,
    SPARK_FLAG_BIT,
    SPARK_PROXY_FLAG_BIT,
    SPECIAL_FLAG_BIT,
    UNIQUE_FLAG_BIT,
    is_boss_flag,
    is_no_codex,
    is_special_flag,
    is_unique_flag,
)
