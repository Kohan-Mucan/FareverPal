"""Human text + colors for the DPS damage-source status line.

Shared by the Top DPS overlay and Combat page so both agree on reader state.
"""
from __future__ import annotations

from . import theme
from ..core import game_state


def _timing_suffix(dm) -> str:
    """"(calib 4.1s · map 1.3s)" suffix from the manager's stage timings, or ""."""
    try:
        t = dm.timing_text()
    except Exception:
        return ""
    return f" ({t})" if t else ""


_MODE_COLORS = {"normal": "MUTED", "hard": "ORANGE", "heroic": "GOLD"}


def dungeon_mode_badge(mode: str | None) -> str:
    """Rich-text title-bar span for the current dungeon mode, or "" outside
    an instance. Shared by the Top DPS meter and the Dungeon HUD so both
    title bars agree on wording + color (normal dim, hard orange, heroic
    gold)."""
    color = _MODE_COLORS.get(mode or "")
    if not color:
        return ""
    return (f"&nbsp;&nbsp;<span style='color:{getattr(theme, color)}; "
            f"font-size:10px; letter-spacing:1px;'>· {mode.upper()}</span>")


# --- instance difficulty, resolved once for every surface ----------------#
# The Run Timer has always resolved the difficulty as "the game's own read when
# there is one, else the manual fallback", and shown which of the two it used in
# the tooltip. The DPS surfaces read `model.dungeon_mode()` instead - the
# GameLayer config alone - so on a build where that one read is dead they showed
# NOTHING while the Run Timer still named a mode (live report 2026-09-30: in a
# dungeon, the Top DPS title and the DPS Analysis rail both blank, config
# resolving to a UI object). Resolution now lives here so all three surfaces
# answer the same question the same way; each keeps its own wording and paint.

_MODES = ("normal", "hard", "heroic")
MANUAL_FALLBACK_MODE = "hard"


def resolved_mode(model) -> tuple[str, str]:
    """``(mode, source)`` - the Run Timer's own resolution of the difficulty.

    ``source`` is ``"auto"`` when the game itself gave the difficulty and
    ``"manual"`` when the fallback supplied it, so a caller can never present
    a guess as a read (or the reverse). Never None: a surface that must not
    guess asks :func:`instance_mode` instead.
    """
    if model is not None:
        try:
            m = model.detected_mode()
        except Exception:
            m = None
        if isinstance(m, str) and m.lower() in _MODES:
            return m.lower(), "auto"
    # The fallback is fixed to Hard, exactly like the Run Timer's (Hard is the
    # mode the game's own content is balanced against; see DIFFICULTY_DETECTION).
    return MANUAL_FALLBACK_MODE, "manual"


def instance_mode(model) -> tuple[str | None, str]:
    """What a difficulty badge should say right now, or ``(None, "")``.

    Inside a dungeon/rift that is :func:`resolved_mode` - a named difficulty
    even when the game can't be read, which is what the Run Timer shows and
    what the DPS surfaces were missing. Outside one it stays the plain live
    read (usually None): the difficulty doc's rule is that the label hides in
    the open world rather than ghosting instance data while walking in town.
    """
    if game_state.in_instance(model):
        return resolved_mode(model)
    try:
        m = model.dungeon_mode() if model is not None else None
    except Exception:
        m = None
    return (m, "auto") if m else (None, "")


def mode_tooltip(mode: str | None, source: str) -> str:
    """Tooltip for a badged difficulty: reads are named, guesses are owned."""
    if not mode:
        return ""
    if source == "auto":
        return "Difficulty read from the live game state (instance config / boss level)."
    if source == "archived":
        return ("Difficulty recorded with this archived fight (the game's own "
                "read when it ran), not read from where you are standing now.")
    return ("Difficulty could not be read from the game right now, so this is "
            "the manual fallback — the same reading the Run Timer shows.")


def source_status(dm) -> tuple[str, str]:
    """``(text, color_hex)`` describing a model's DamageSourceManager (or None)."""
    if dm is None:
        return "DPS source: not attached", theme.MUTED
    try:
        st = dm.status()
        n, rows = dm.counts()
    except Exception:
        return "DPS source: not available", theme.MUTED
    if st == "noscan":
        return "DPS source: needs the native memory scanner", theme.DANGER
    if st == "meter":
        return (f"SOURCE: LIVE — group meter ({rows} rows)"
                + _timing_suffix(dm), theme.GOOD)
    if st == "missing_proxy":
        return ("SOURCE: ⚠ Option 1 — proxy DLL missing from game folder", theme.ORANGE)
    if st == "need_inject":
        return ("SOURCE: ⚠ Option 2 — injector not attached", theme.ORANGE)
    if st == "inject_failed":
        why = getattr(dm, "inject_problem", "") or "the injector refused"
        return (f"SOURCE: ⚠ Option 2 — INJECT FAILED ({why})", theme.DANGER)
    if st == "bridge_ready":
        issue = (dm.bridge_issue()
                 if getattr(dm, "bridge_issue", None) is not None else "")
        if issue:
            return (f"SOURCE: ⚠ combat bridge — {issue}", theme.ORANGE)
        return ("SOURCE: LIVE — combat bridge (ready)", theme.GOOD)
    if st == "live":
        src_name = getattr(dm, "live_source_name", lambda: "")() or "damage numbers"
        # The bridge names itself "combat bridge (proxy|injector)".
        if src_name.startswith("combat bridge"):
            return (f"SOURCE: LIVE — {src_name} ({n} events)"
                    + _timing_suffix(dm), theme.GOOD)
        return (f"SOURCE: LIVE — damage numbers ({n}/poll)"
                + _timing_suffix(dm), theme.GOOD)
    if st == "locating":
        try:
            prog = dm.locate_progress()
        except Exception:
            prog = ""
        if prog:
            return (f"Calibrating damage reader — locating type… ({prog})",
                    theme.GOLD)
        return "Calibrating damage reader — locating type…", theme.GOLD
    if st == "mapping":
        t = _timing_suffix(dm)
        return (f"Calibrating damage reader — mapping… attack to finish{t}",
                theme.GOLD)
    if st == "silent":
        # "silent" is normal between fights; only a FINDING reader decoding
        # nothing is genuinely stale (orange). Otherwise idle / standing by.
        t = _timing_suffix(dm)
        if getattr(dm, "damage_stale", False):
            # Found displays but decoded nothing (res = with dmg pointer).
            d = dm.diagnostics()
            return ("SOURCE: decoding displays, 0 events" + t + " — "
                    f"{d['displays']} displays, res={d.get('res', 0)}, "
                    f"bad_hdr={d.get('bad_hdr', 0)}, "
                    f"no_res={d.get('no_res', 0)}, "
                    f"bad_amount={d.get('bad_amount', 0)}, "
                    f"bad_skill={d.get('bad_skill', 0)}",
                    theme.ORANGE)
        if getattr(dm, "has_decoded", False):
            return ("SOURCE: idle — no damage events right now" + t,
                    theme.MUTED)
        return ("SOURCE: standing by — hit an enemy to start the meter" + t,
                theme.DIM)
    return "DPS source: off", theme.MUTED


def empty_hint(dm, default: str) -> str:
    """Empty-state text: honest about calibration while no real events flow."""
    if dm is None:
        return "No real combat events available — numbers only come from the game's damage events."
    try:
        st = dm.status()
    except Exception:
        return "No real combat events available yet."
    if st == "missing_proxy":
        return "Option 1 (Proxy) active, but no proxy DLL is in your game folder. Install dinput8.dll in Settings and restart the game."
    if st == "need_inject":
        return "Option 2 (Injector) active, but farever_dps.dll is not injected into the game. Restart the game to re-attach, or switch to Option 2 in Settings."
    if st == "inject_failed":
        why = getattr(dm, "inject_problem", "") or "the injector refused"
        return (f"Option 2 (Injector) FAILED: {why}. Nothing is injected, so no "
                "damage events can be captured. See the Activity Log for the "
                "injector's own output, or switch capture mode in Settings.")
    if st in ("live", "meter", "bridge_ready", "silent"):
        return default
    if st in ("locating", "mapping"):
        return "Waiting for real combat events… (damage reader still calibrating)"
    if st == "noscan":
        return "This build has no memory scanner, so Option 3 cannot capture anything. Switch to Option 1 or 2."
    return "No real combat events yet — this meter never shows HP-derived numbers."


def capture_badge(dm) -> tuple[str, str, str]:
    """``(text, color_hex, tooltip)`` for the Top DPS meter's title-bar badge.

    Three visual states, derived from the SAME ``dm.status()`` vocabulary as
    :func:`source_status` so the overlay badge and the settings/page status
    line can never disagree:

    * **LIVE** (green) — real events are flowing (or the bridge is ready);
    * **CAL** (gold) — the reader is still locating/mapping its types;
    * **IDLE** (dim) — calibrated and healthy, just no damage right now;
    * **⚠** (orange/red) — a dependency problem (missing DLL, no scanner,
      or a reader finding displays but decoding nothing).

    The badge is also the report's trigger: every state is clickable and writes
    the event-path report to the Activity Log (see :func:`emit_capture_diagnostic`),
    because "the board reads zero" has at least three different causes and the
    engine status alone cannot tell them apart.

    Returns ``("", MUTED, "")`` when there is nothing to say (no source, or
    capture not started) so callers can hide the badge.
    """
    if dm is None:
        return "", theme.MUTED, ""
    try:
        st = dm.status()
    except Exception:
        return "", theme.MUTED, ""
    if st == "live":
        try:
            n, _rows = dm.counts()
        except Exception:
            n = 0
        try:
            src_name = dm.live_source_name() or "damage numbers"
        except Exception:
            src_name = "damage numbers"
        return ("LIVE", theme.GOOD,
                f"Capture live — {src_name} ({n} events)")
    if st == "bridge_ready":
        issue = ""
        try:
            issue = dm.bridge_issue() or ""
        except Exception:
            pass
        if issue:
            return "⚠", theme.ORANGE, f"combat bridge — {issue}"
        return ("LIVE", theme.GOOD,
                "Capture live — combat bridge ready, waiting for first event")
    if st in ("need_inject", "missing_proxy"):
        which = "Option 2 — injector not attached" \
            if st == "need_inject" else "Option 1 — proxy DLL missing from game folder"
        return "⚠", theme.ORANGE, which
    if st == "inject_failed":
        why = ""
        try:
            why = getattr(dm, "inject_problem", "") or "the injector refused"
        except Exception:
            pass
        return ("⚠", theme.DANGER,
                f"Option 2 — INJECT FAILED ({why}) — no damage events will be "
                f"captured; check the Activity Log")
    if st == "noscan":
        return "⚠", theme.DANGER, "This build has no memory scanner — Option 3 cannot capture"
    if st in ("locating", "mapping"):
        tip = "Calibrating damage reader — locating type…"
        if st == "locating":
            try:
                prog = dm.locate_progress()
            except Exception:
                prog = ""
            if prog:
                tip = f"Calibrating damage reader — locating type… ({prog})"
        else:
            tip = "Calibrating damage reader — mapping… attack to finish"
        return "CAL", theme.GOLD, tip + " — click the badge to rescan now"
    if st == "silent":
        if getattr(dm, "damage_stale", False):
            return ("⚠", theme.ORANGE,
                    "Reader finds displays but decodes 0 events — "
                    "pool may be stale (check the Activity Log) — "
                    "click the badge to rescan now")
        tip = ("No damage events right now" if getattr(dm, "has_decoded", False)
               else "Standing by — hit an enemy to start the meter")
        return "IDLE", theme.DIM, tip
    return "", theme.MUTED, ""          # "off" / unknown: nothing to show yet


# --- the on-demand event-path report -------------------------------------#
#
# The badge says whether the capture ENGINE is healthy. It cannot say whether
# events are actually reaching the meter, and those are different failures with
# the same symptom: a board that reads zero. This report walks the path in
# order — arrived, decoded, pulled, attributed — so the first stage that reads
# zero IS the diagnosis, plus a plain-English reading of it for the case where
# the numbers need interpreting.
#
# The counters are core's (DpsEventStats in core/dps_source.py); this is only
# wording. Kept here so the meter, the Test Dummy HUD and any future surface
# word the same report identically.

_REPORT_HINT = "Click the capture badge for this report."


def with_report_hint(tip: str) -> str:
    """Append the report trigger to a badge tooltip.

    The badge is the only on-demand diagnostic a player has in front of them
    while the meter reads zero, so its tooltip says what a click does. Applied
    by each surface at the point it applies the badge, which keeps every
    tooltip above free of this one sentence.
    """
    return f"{tip} — {_REPORT_HINT}" if tip else _REPORT_HINT


def _n(value) -> str:
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return "?"


def _bridge_name_loss(dm) -> tuple[int, bool]:
    """(hits the bridge sent with a placeholder name, whether it reports it).

    The boolean is the point: an older DLL never sends the counter, and reading
    that silence as a zero would turn this line into a claim the bridge never
    made - the same mistake the hit counter's ``hits_reported`` flag exists to
    prevent.
    """
    getter = getattr(dm, "bridge_unnamed_hits", None)
    if not callable(getter):
        return (0, False)
    try:
        n, known = getter()
        return (int(n or 0), bool(known))
    except Exception:
        return (0, False)


def _bytes(value) -> str:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return "?"
    if n >= 1024.0 * 1024.0:
        return f"{n / (1024.0 * 1024.0):.1f} MB"
    if n >= 1024.0:
        return f"{n / 1024.0:.1f} kB"
    return f"{int(n)} B"


def event_path_verdict(c: dict, mode: str = "") -> str:
    """One sentence naming the stage the events stop at, or that they arrive.

    A ladder, not a flag: the first stage with a zero over a non-zero one above
    it is the broken link. PARTIAL is a rung of its own because it is the one a
    binary verdict gets wrong — a stream that is 90% working reads exactly like
    a healthy one on the board, and the dropped tenth is the whole bug.
    """
    decoded = c.get("decoded", 0) or 0
    pulled = c.get("pulled", 0) or 0
    attributed = c.get("attributed", 0) or 0
    filtered = c.get("filtered", 0) or 0
    dropped = c.get("dropped", 0) or 0
    unresolved = c.get("dropped_unresolved", 0) or 0
    arrived = c.get("payload_packets", 0) or 0
    if not c.get("payload_counted") and (mode or "").lower() == "memory":
        arrived = decoded          # the memory reader has no stream to count
    if arrived <= 0 and decoded <= 0:
        return ("NOTHING ARRIVED — the game sent no combat data at all. In "
                "Option 1/2 that is the DLL not loaded, not listening, or "
                "another process holding the bridge port (the Activity Log "
                "names the holder).")
    if decoded <= 0:
        return ("PAYLOAD ARRIVED, NOTHING DECODED — the data reached us and no "
                "combat event could be read from it. A version/protocol "
                "mismatch with the DLL, or a heartbeat-only stream.")
    if pulled <= 0:
        return ("DECODED BUT NEVER PULLED — events are in the ring and the "
                "tracker's drain is not running. The meter is not reading a "
                "capture problem at all.")
    if attributed <= 0 and filtered <= 0:
        if unresolved > 0:
            return ("DROPPED BEFORE ATTRIBUTION — events decoded and were "
                    "refused because the caster could not be identified. The "
                    "capture is fine; the reading of WHO did what is failing "
                    "(player offsets / party roster).")
        return ("DROPPED BEFORE ATTRIBUTION — events decoded and the event "
                "path refused all of them. The Activity Log's "
                "'attribution unresolved' lines say which refusal.")
    if attributed <= 0:
        return (f"NOTHING KEPT, NOTHING LOST — all {_n(pulled)} events were "
                "deliberately filtered instead (solo filter / post-wipe hold). "
                "Turn off Solo Only or get into a fight to see your own DPS.")
    if dropped > 0:
        tail = (f", of which {_n(unresolved)} had an unidentifiable caster"
                if unresolved else "")
        return (f"PARTIAL — {_n(attributed)} of {_n(pulled)} events reached a "
                f"session; {_n(dropped)} were refused before attribution"
                f"{tail}. The capture works; the reading of WHO does not.")
    return "ARRIVING — events are reaching the meter."


def event_path_report(model) -> list[str]:
    """The full event-path report as Activity Log lines (empty when unattached).

    Counters are cumulative for the life of the process, so this answers "has
    anything EVER got through", which is the question when the board is empty
    and nothing is obviously wrong.
    """
    dm = getattr(model, "damage", None) if model is not None else None
    if dm is None:
        return ["DPS event path: no capture source attached — the meter has "
                "nothing to read. Press Attach."]
    try:
        mode = getattr(dm, "mode", "") or ""
        status = dm.status()
        counts = (dm.event_path_counts() if hasattr(dm, "event_path_counts")
                  else dm.stats.snapshot())
    except Exception as exc:                       # never raise into a click
        return [f"DPS event path: could not read the capture counters ({exc})"]

    source = ""
    try:
        source = dm.live_source_name() or ""
    except Exception:
        source = ""
    issue = ""
    try:
        issue = dm.bridge_issue() or ""
    except Exception:
        issue = ""
    ring = counts.get("in_ring")
    lines = [
        f"DPS event path — {('capture is ' + str(mode).upper() + ' ') if mode else ''}"
        f"via {source or 'unknown source'} (status: {status})"
        + (f" — {issue}" if issue else ""),
        f"  arrived : {_bytes(counts.get('payload_bytes', 0))} in "
        f"{_n(counts.get('payload_packets', 0))} datagrams"
        + ("" if counts.get("payload_counted")
           else "  (n/a — the memory reader has no stream)"),
        f"  decoded : {_n(counts.get('decoded', 0))} events "
        f"({_n(counts.get('ring_total', 0))} ever pushed)",
        f"  pulled  : {_n(counts.get('pulled', 0))} by the tracker"
        + (f"  ({_n(ring)} still in the ring)" if ring is not None else ""),
        f"  kept    : {_n(counts.get('attributed', 0))} attributed to a session",
        f"  filtered: {_n(counts.get('filtered', 0))} deliberately not recorded "
        "(solo filter / post-wipe hold — expected, not a loss)",
        f"  dropped : {_n(counts.get('dropped', 0))} before attribution"
        + (f", of which {_n(counts.get('dropped_unresolved', 0))} had an "
           "unidentifiable caster" if counts.get("dropped_unresolved") else ""),
    ]
    # Not a stage of the ladder: the events all arrived and the meter counts
    # them. What is missing is the NAME on each one, which the DLL can only
    # notice itself (it is the side that reads the unit) - so this is the one
    # line here that could not be derived from the counters above, and it is
    # the only place a player or a dev is told that the fight archive's target
    # names are placeholders rather than game facts.
    unnamed, unnamed_known = _bridge_name_loss(dm)
    if unnamed_known and unnamed > 0:
        lines.append(
            f"  names   : {_n(unnamed)} hits were sent as \"Enemy\" — the DLL "
            "could not read the unit's name and used its placeholder. Target "
            "names in the fight log, boss matching and party rows are wrong "
            "until the native foe-name offsets are re-verified")
    lines.append(f"  verdict : {event_path_verdict(counts, mode)}")
    return lines


def emit_capture_diagnostic(model, emit) -> None:
    """Write the event-path report to the Activity Log (the badge click).

    `emit` is the caller's log sink — the overlay's `log` signal, which the
    panel routes to the Activity Log. Best-effort by design: a diagnostic that
    can raise is worse than no diagnostic.
    """
    if not callable(emit):
        return
    try:
        lines = event_path_report(model)
    except Exception:
        lines = ["DPS event path: the report itself failed — see the log file."]
    for line in lines:
        try:
            emit(line)
        except Exception:
            pass
