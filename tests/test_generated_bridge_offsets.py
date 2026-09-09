"""The bridge's positional offsets must have exactly ONE source: constants.py.

Until 2026-10-02 the 22 struct offsets the DLL compiles were hand-copied
`#define`s in hook/bridge_core.h, while farever_companion/constants.py held its
own copy. Nothing kept them in step, and the C copy won by default: only 5 of
the 22 were present in the Python file at all. The whole st.skill.DamageResult
layout, the baseSkill credit chain, ent.UnitAttributes health and the HashLink
String shape were invisible to the offset calibrator, which is the tool that
exists to fix exactly those numbers when a patch moves them. An offset the tool
cannot see is an offset the tool cannot fix.

So constants.py is now the source, gen_bridge_offsets.py writes what the
compiler reads, and these tests make that arrangement load-bearing:

  * every offset the generator emits is DEFINED in constants.py (a name with no
    Python source is the original bug, so it must fail the build);
  * the committed header matches what the generator produces right now;
  * bridge_core.h defines no offset of its own and includes the generated one.

The last one is the one that matters going forward: it makes "just edit the C"
impossible to do quietly.
"""
from __future__ import annotations

import importlib.util
import pathlib
import re

import pytest

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_HOOKS = _ROOT.parent / "GameFiles" / "Farever" / "hooks"
_CORE = _HOOKS / "hook" / "bridge_core.h"
_GENERATED = _HOOKS / "hook" / "bridge_offsets.h"
_GENERATOR = _HOOKS / "gen_bridge_offsets.py"
_CONSTANTS = _ROOT / "farever_companion" / "constants.py"

pytestmark = pytest.mark.skipif(
    not _GENERATOR.is_file(),
    reason="sibling GameFiles native tree is not checked out")


def _load_generator():
    spec = importlib.util.spec_from_file_location("_gen_bridge_offsets", _GENERATOR)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _defines(path: pathlib.Path) -> dict[str, int]:
    text = path.read_text(encoding="utf-8", errors="replace")
    return {
        m[0]: int(m[1], 0)
        for m in re.findall(r"(?m)^#define\s+(OFF_[A-Z0-9_]+)\s+"
                            r"(0x[0-9a-fA-F]+|[0-9]+)", text)
    }


def test_every_bridge_offset_exists_in_constants_py():
    """The contract that would have caught the original drift."""
    gen = _load_generator()
    values = gen.collect(_CONSTANTS)
    assert len(gen.BRIDGE_OFFSET_NAMES) >= 20, (
        "the bridge reads more than 20 positional offsets; a much smaller list "
        "means the contract lost entries and stops protecting the rest")
    # collect() raises SystemExit on a missing name, so reaching here is the
    # assertion. Check a couple explicitly so the failure reads clearly.
    import farever_companion.constants as const
    for name in gen.BRIDGE_OFFSET_NAMES:
        assert hasattr(const, name), f"{name} is not in constants.py"
        assert isinstance(getattr(const, name), int)


def test_the_committed_header_matches_the_generator():
    gen = _load_generator()
    values = gen.collect(_CONSTANTS)
    expected = gen.render(values, _CONSTANTS, "STAMP-IGNORED")
    got = _GENERATED.read_text(encoding="utf-8")
    want = [l for l in expected.splitlines() if l.startswith("#define")]
    have = [l for l in got.splitlines() if l.startswith("#define")]
    assert want == have, (
        "hook/bridge_offsets.h no longer matches farever_companion/constants.py. "
        "Run: python GameFiles/Farever/hooks/gen_bridge_offsets.py  (build_all.bat "
        "does this for you)")


def test_the_core_defines_no_offset_of_its_own():
    """'Just edit the C' must not be possible to do quietly."""
    core = _CORE.read_text(encoding="utf-8", errors="replace")
    own = re.findall(r"(?m)^#define\s+(OFF_[A-Z0-9_]+)\s+", core)
    assert not own, (
        f"bridge_core.h defines its own offsets again: {own}. Every OFF_* the "
        f"DLL compiles belongs in farever_companion/constants.py, which the "
        f"offset calibrator updates - a copy in the C cannot be calibrated and "
        f"will drift. Put it back in constants.py and rebuild.")
    assert '#include "bridge_offsets.h"' in core, (
        "bridge_core.h must include the generated offsets header")


def test_the_two_constants_copies_stay_identical():
    """The Offsets/ copy is a backup; a backup that differs is a trap.

    It is what the calibrator falls back to when the companion file is missing,
    so a stale copy would quietly reintroduce every old offset.
    """
    backup = _ROOT.parent / "GameFiles" / "Farever" / "Offsets" / "constants.py"
    if not backup.is_file():
        pytest.skip("Offsets/constants.py backup not present")
    a = _CONSTANTS.read_bytes().replace(b"\r\n", b"\n")
    b = backup.read_bytes().replace(b"\r\n", b"\n")
    if a != b:
        # The calibrator has a "backup companion -> Offsets" button; say so
        # rather than asserting on bytes, which line endings would break.
        ra = _CONSTANTS.read_text(encoding="utf-8", errors="replace")
        rb = backup.read_text(encoding="utf-8", errors="replace")
        assert ra == rb, (
            "Farever/Offsets/constants.py differs from the companion's copy. The "
            "calibrator falls back to it when the companion file is missing, so "
            "a stale backup would reintroduce every old offset - re-run its "
            "'backup companion constants.py' action.")


def test_the_key_offsets_are_the_live_verified_values():
    """Pin the numbers live reflection proved, not just their plumbing.

    These were the corrections that mattered: isMe at 0x158 is what stops a
    whole rift's damage landing on the local player, and 0x4f0 is a real field
    where the old 1256 read `foeState`.
    """
    values = _defines(_GENERATED)
    assert values["OFF_PLAYER_ISME"] == 0x158
    assert values["OFF_HERO_OWNERPLAYER"] == 0x10, (
        "0x10 is the live ownerPlayer. 0x498 was here for weeks on the belief "
        "that the scan's 0x10 was a wrong answer - it was the right one")
    assert values["OFF_HERO_PLAYER"] == 0x4e0
    assert values["OFF_FOE_SUMMON_OWNER"] == 0x4f0
    assert values["OFF_UNITID"] == 0x280
    assert values["OFF_DR_AFFINITY"] == 0x38