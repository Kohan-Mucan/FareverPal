"""Opt-in board trace for the Top DPS meter.

The meter's number can go to ZERO with no ``Top DPS: reset (...)`` line: the
tracker did not wipe, the board it is DISPLAYING simply changed. Those are
different bugs with different fixes, and the Activity Log only explains the
first one — every reset states its reason, but nothing states which session the
auto board picked or that it changed. Live 2026-10-04, in a dungeon: "top dps is
still resetting after every time i leave combat ... no reset line, it just
clears".

This records one JSONL line each time the auto board's session OR its total
changes, with the whole decision state beside it (the three session totals, the
instance gate, the boss flags, the game's combat flag), so ONE dungeon run names
the culprit instead of a day of speculation.

Deliberately OPT-IN — set ``FAREVER_DPS_TRACE=1`` — because the trace is a
diagnostic, not a feature: it keeps the default path free of both the file write
and the per-tick state collection, which is also what keeps it out of the test
suite. ``record`` never raises and the file is size-capped, so a long session
cannot fill a disk or take the meter down.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

#: Roll the trace over once it passes this: a diagnostic should not be able to
#: grow without bound during a long play session.
_MAX_BYTES = 512 * 1024

#: Truthy values for the env switch (1/true/yes/on, case-insensitive).
_TRUTHY = frozenset({"1", "true", "yes", "on"})


def enabled() -> bool:
    """Whether the trace is switched on. Read per call, so a test can set it."""
    try:
        return os.environ.get("FAREVER_DPS_TRACE", "").strip().lower() in _TRUTHY
    except Exception:
        return False


def trace_path() -> Path | None:
    """The trace file under the DPS folder, or None when it cannot be built."""
    try:
        from ..config import dps_dir
        return Path(dps_dir()) / "dps_trace.jsonl"
    except Exception:
        return None


def record(**fields) -> None:
    """Append one diagnostic line. Best-effort: never raises, never blocks long.

    A missing/unwritable file is silent by design — a diagnostic that can break
    the meter it is observing would be worse than the bug it is chasing.
    """
    try:
        path = trace_path()
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and path.stat().st_size > _MAX_BYTES:
                path.unlink()
        except OSError:
            return
        fields.setdefault("t", time.time())
        line = json.dumps(fields, separators=(",", ":"), default=str)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass
