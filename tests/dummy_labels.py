"""The Test Dummy HUD's label grammar, transcribed in ONE place.

Why this exists
---------------
The Dummy HUD is a readout: almost everything it says, it says as text on a
label (`400 dmg`, `1:00 ready`, `0:48`, `worst gap 1s @ 0:02`, `BEST 50/s`). The
surface tests assert that text, and each one used to spell the format itself -
so changing a single label meant tracking down every test that happened to look
at it and editing each copy. One format change, five independent edits.

So the grammar lives here. The surface tests call these functions instead of
writing literals, and `test_dummy_overlay.py::test_the_hud_label_formats_are_the_shared_contract`
pins their output against explicit strings and against a rendered HUD. A label
change is then recorded in one place, and the tests that read these functions
follow that one edit rather than each carrying their own copy of the format.

Nothing here imports Qt, the tracker or the HUD - the contract is the STRING, so
this module can be read and tested without a window. Every format mirrors a real
call site in `ui/overlays/dummy_overlay.py`, `ui/overlays/dps/dummy_ui.py` or
`ui/overlays/dps/dummy_replay.py`; the module docstring of the test that pins
them names those call sites. When a format there changes, change it here and let
the pinning test tell you what moved.
"""
from __future__ import annotations

# --- fixed words and glyphs ----------------------------------------------#

DMG_SUFFIX = "dmg"                  # the total carries its own unit (dmg_lbl)
READY_SUFFIX = "ready"              # the idle clock's trailer
DONE_WORD = "DONE"                  # the finished clock's prefix
HITS_WORD = "hits"                  # the elapsed line's trailer
REARM_WORD = "auto re-arm in"       # the idle re-arm countdown
CHIP_TAKEN = "TAKEN"                # the damage-taken channel chip
CHIP_HEALING = "HEALING"            # the healing channel chip

REPLAY_TITLE = "REPLAY"
PLAY_GLYPH = "▶"
PAUSE_GLYPH = "❚❚"
GAP_OK = "no dead seconds"          # a finished run with no dead second

TARGET_NONE = "Target: —"           # no dummy pinned yet
BTN_STOP = "🎯 Stop Test"
BTN_START = "▶ Start Test"
HINT_STOPPED = "🎯 Test stopped — press Start Test."
HINT_ATTACK = "🎯 Attack the dummy to start."


# --- clock and times ------------------------------------------------------#

def mmss(seconds: float) -> str:
    """`93` -> `1:33`, `27.4` -> `0:27`: whole seconds, no padded minutes."""
    total = max(0, int(round(float(seconds or 0.0))))
    return f"{total // 60}:{total % 60:02d}"


def clock_ready(target_s: float) -> str:
    """The idle clock: no hit yet, so the whole target is still ahead."""
    return f"{mmss(target_s)} {READY_SUFFIX}"


def clock_done(elapsed_s: float) -> str:
    """The clock once the target length is spent."""
    return f"{DONE_WORD} {mmss(elapsed_s)}"


def elapsed_line(duration: float, hits: int) -> str:
    """The elapsed readout under the clock: `0:12 · 3 hits`."""
    return f"{mmss(duration)} · {int(hits):,} {HITS_WORD}"


def rearm(seconds_left: float) -> str:
    """The idle countdown to the test's own re-arm."""
    return f"⏳ {REARM_WORD} {max(0, int(round(float(seconds_left or 0.0))))}s"


# --- headline numbers ------------------------------------------------------#

def dmg(total: float) -> str:
    """The test's total damage, with its own unit."""
    return f"{float(total or 0.0):,.0f} {DMG_SUFFIX}"


def rate(value: float, places: int = 1) -> str:
    """A damage rate. `places=1` for the headline/chips, `0` for row labels."""
    return f"{float(value or 0.0):,.{int(places)}f}/s"


def delta_rate(delta: float) -> str:
    """A signed rate, as the vs-reference deltas render it."""
    return f"{float(delta or 0.0):+,.0f}/s"


def delta_line(delta: float, pct: float | None) -> str:
    """A per-skill delta row: the signed rate, then the percent when real."""
    text = delta_rate(delta)
    return text if pct is None else text + f"  {float(pct):+.1f}%"


def chip_value(value: float) -> str:
    """A channel chip's headline number (no `dmg`/`/s` unit, unlike `dmg`)."""
    return f"{float(value or 0.0):,.0f}"


# --- split rows ------------------------------------------------------------#

# The `~` the HUD puts on the ONE row whose damage had to be guessed (it
# absorbed hits that arrived with no target address). Per ROW, not per split:
# dummy_split_head_tag below has no `est.` variant, on purpose.
ESTIMATE_MARK = "~"


def dummy_split_head_text(n: int) -> str:
    """The split section's title, with its row count."""
    return f"SPLIT BY TARGET · {int(n or 0)}"


def dummy_split_head_tag(total: float) -> str:
    """The split section's trailing total — the plain sum, always.

    This used to grow a ` · est.` tag when any hit arrived without an address,
    which marked three measured dummies as uncertain because of one stray hit.
    """
    return f"{float(total or 0.0):,.0f} total"


def hits_label(n: int) -> str:
    """One target's hit count, singularised."""
    n = int(n or 0)
    return f"{n} hit" + ("s" if n != 1 else "")


def share_pct(pct: float) -> str:
    """One target's slice of the split total."""
    return f"{float(pct or 0.0):,.1f}%"


def split_row_total(damage: float, estimated: bool = False) -> str:
    """One row's damage cell, with `~` when that row is the estimate."""
    return (ESTIMATE_MARK if estimated else "") + f"{float(damage or 0.0):,.0f}"


# --- finished-test replay --------------------------------------------------#

def replay_read(since: float, duration: float, banked: float, dps: float) -> str:
    """The replay's readout: playhead / length, banked damage, banked DPS."""
    return (f"{mmss(since)} / {mmss(duration)}   "
            f"{float(banked or 0.0):,.0f} {DMG_SUFFIX}   {float(dps or 0.0):,.0f}/s")


def replay_gap(length_s: int, start_s: float) -> str:
    """The worst dead stretch, or `GAP_OK` when the run never went quiet."""
    if int(length_s or 0) <= 0:
        return GAP_OK
    return f"worst gap {int(length_s)}s @ {mmss(start_s)}"


# --- vs last test / personal best ------------------------------------------#

def best_headline(dps: float) -> str:
    """The personal-best line's opening number."""
    return f"BEST {float(dps or 0.0):,.0f}/s"


def new_best(dps: float) -> str:
    """The record-breaking variant of the best line."""
    return f"★ NEW BEST {float(dps or 0.0):,.0f}/s"


def best_line(dps: float, age_text: str, gap_pct: float | None) -> str:
    """The full best line: the ceiling, how old it is, and the gap to it."""
    gap = "—" if gap_pct is None else f"{float(gap_pct):+.1f}%"
    return f"{best_headline(dps)} · {age_text} · {gap}"


def best_tooltip_this_run(live_dps: float) -> str:
    """The best line's tooltip sentence about the run on screen now."""
    return f"This run: {float(live_dps or 0.0):,.0f}/s."


# --- the dirty-run warning --------------------------------------------------#

def dirty_warning(share_pct: float, limit_pct: float) -> str:
    """The whole sentence the HUD shows when a run is not a clean measurement."""
    return (f"⚠ {float(share_pct or 0.0):,.1f}% of this test's damage came "
            f"back at you (over {float(limit_pct or 0.0):,.0f}%) — not a clean "
            f"measurement")


def target_label(name: str) -> str:
    """The pinned target's label (the crown is stripped before this)."""
    return f"🎯 {name}"
