"""Human-readable summaries for preserved *.bak files (pure, no Qt).

The backup-recovery dialog shows these next to the raw contents so a user can
tell at a glance what a backup holds ("128 POIs · 3 hidden · DPS best") and
compare it against the current file's summary without reading raw JSON.
"""
from __future__ import annotations

import json


def kind_for(name: str) -> str:
    """What kind of data a moddata file holds, for summary purposes. Accepts
    the live name (settings.json) or the preserved backup name
    (settings.json.bak)."""
    if name.endswith(".bak"):
        name = name[:-4]
    if name.startswith("progress_"):
        return "profile"
    if name == "settings.json":
        return "settings"
    if name == "collection.json":
        return "collection"
    return "raw"


def _load(text: str) -> dict | None:
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def _count(items) -> int:
    return len(items or [])


def summarize(name: str, text: str) -> str:
    """One-line summary of a file's contents; 'not readable' when the text
    isn't a JSON object."""
    data = _load(text)
    if data is None:
        return "not readable"
    kind = kind_for(name)
    if kind == "profile":
        parts = []
        pois = _count(data.get("poi_done"))
        if pois:
            parts.append(f"{pois} POI{'s' if pois != 1 else ''}")
        hidden = _count(data.get("entity_hidden_units"))
        if hidden:
            parts.append(f"{hidden} hidden")
        if data.get("dps_best"):
            parts.append("DPS best")
        if data.get("speedrun_best"):
            parts.append("speedrun")
        if data.get("speedrun_boss_best"):
            parts.append("boss best")
        return " · ".join(parts) if parts else "no data"
    if kind == "collection":
        return (f"{_count(data.get('pets'))} pets · "
                f"{_count(data.get('mounts'))} mounts · "
                f"{_count(data.get('gliders'))} gliders")
    if kind == "settings":
        parts = [f"{len(data)} settings"]
        acct = data.get("account_name")
        if acct:
            parts.append(f"account: {acct}")
        return " · ".join(parts)
    return f"{len(data)} keys"


def summarize_snapshot(snap: dict) -> str:
    """Human-readable summary of a master backup snapshot."""
    profiles = snap.get("profiles", {})
    prof_count = len(profiles)
    total_pois = sum(_count(p.get("poi_done")) for p in profiles.values())
    total_orbs = sum(_count(p.get("dungeon_orb_done")) for p in profiles.values())
    col = snap.get("collection", {})
    pets = _count(col.get("pets"))
    mounts = _count(col.get("mounts"))
    gliders = _count(col.get("gliders"))
    farm = _count(snap.get("planner", {}).get("farm"))
    
    parts = []
    if prof_count:
        parts.append(f"{prof_count} profile{'s' if prof_count != 1 else ''} ({total_pois} POIs" + (f", {total_orbs} orbs" if total_orbs else "") + ")")
    if pets or mounts or gliders:
        parts.append(f"collection ({pets}p/{mounts}m/{gliders}g)")
    if farm:
        parts.append(f"{farm} gear farm")
    return " · ".join(parts) if parts else "empty backup"
