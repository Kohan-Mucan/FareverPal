"""The compiler's byte-identical contract, enforced at build time.

`compiler.py`'s `_payload_meta` states the deal: the payload must be a pure
function of the game data, so a rebuild over unchanged data leaves
`git status` clean. `tests/test_compiler.py` proves the pure-function half
(rebuild into a temp dir, compare digests); this module enforces the other
half at the moment a build actually runs the compiler: after
`python compiler.py`, every shipped shim must match the index exactly, so the
exe that gets versioned with the current git rev carries data that rev
actually describes.

Scope is the shims ONLY (`farever_companion/data/raw_*.py`): a dirty
compiler.py or any other work-in-progress file is the developer's business
and must not block a build - but a dirty SHIM after a rebuild means either
the compile input changed without being committed (commit the dump and the
shims together) or something nondeterministic just crept into the compiler.

`FAREVER_ALLOW_DIRTY_SHIMS=1` downgrades the failure to a warning for the
intentional "package a test exe from an uncommitted dump" case; a build whose
version string will matter must not set it.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SHIM_PATHSPEC = "farever_companion/data/raw_*.py"


def shim_dirt(root: Path = ROOT) -> list[str] | None:
    """Uncommitted changes among the raw_*.py shims, as porcelain lines.

    None when git cannot answer (no checkout, no git binary, git trouble):
    the guard never fails on tooling absence, the same trade `_tracked_paths`
    makes in the hygiene suite - a guard that guesses is worse than one that
    stays quiet. An empty list means the contract holds.
    """
    try:
        r = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain", "--",
             SHIM_PATHSPEC],
            capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    return [ln for ln in r.stdout.splitlines() if ln.strip()]


def run() -> tuple[int, list[str]]:
    """`(failures, dirt lines)` for the build to branch on.

    One failure when dirt exists and FAREVER_ALLOW_DIRTY_SHIMS is not set;
    zero when the tree is clean, git is unavailable (skip, never guess), or
    the override is set (warn loudly, build anyway).
    """
    dirt = shim_dirt()
    if dirt is None:
        print("Shim Rebuild Contract : SKIPPED (git unavailable or not a checkout)")
        return 0, []
    if not dirt:
        print("Shim Rebuild Contract : OK (raw_*.py match the index)")
        return 0, []
    lines = ", ".join(dirt)
    if os.environ.get("FAREVER_ALLOW_DIRTY_SHIMS") == "1":
        print(f"Shim Rebuild Contract : WARN (FAREVER_ALLOW_DIRTY_SHIMS=1) - "
              f"uncommitted shims: {lines}")
        return 0, dirt
    print(f"Shim Rebuild Contract : FAILED - uncommitted shims: {lines}")
    return 1, dirt


if __name__ == "__main__":
    failures, dirt_lines = run()
    if failures:
        print()
        print("The rebuild left the raw_*.py shims different from the index.")
        print("Commit the shims together with the data that produced them,")
        print("or, if the change was accidental:")
        print(f"    git restore -- {SHIM_PATHSPEC}")
        print("Set FAREVER_ALLOW_DIRTY_SHIMS=1 to build anyway (test exes only).")
    sys.exit(1 if failures else 0)
