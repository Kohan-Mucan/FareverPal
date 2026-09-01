"""Human text + colors for the DPS damage-source status line.

Shared by the Top DPS overlay and Combat page so both agree on reader state.
"""
from __future__ import annotations

from . import theme


def _timing_suffix(dm) -> str:
    """"(calib 4.1s · map 1.3s)" suffix from the manager's stage timings, or ""."""
    try:
        t = dm.timing_text()
    except Exception:
        return ""
    return f" ({t})" if t else ""


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
    if st == "bridge_ready":
        issue = (dm.bridge_issue()
                 if getattr(dm, "bridge_issue", None) is not None else "")
        if issue:
            return (f"SOURCE: ⚠ combat bridge — {issue}", theme.ORANGE)
        return ("SOURCE: LIVE — combat bridge (ready)", theme.GOOD)
    if st == "live":
        src_name = getattr(dm, "live_source_name", lambda: "")() or "damage numbers"
        if src_name == "combat bridge":
            return (f"SOURCE: LIVE — combat bridge ({n} events)"
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
        return "Option 1 (Proxy) active, but proxy DLL (version.dll / dinput8.dll) is not in your game folder. Install it in Settings and restart the game."
    if st == "need_inject":
        return "Option 2 (Injector) active, but farever_dps.dll is not injected into the game. Restart the game to re-attach, or switch to Option 2 in Settings."
    if st in ("live", "meter", "bridge_ready"):
        return default
    if st in ("locating", "mapping"):
        return "Waiting for real combat events… (damage reader still calibrating)"
    return "No real combat events yet — this meter never shows HP-derived numbers."
