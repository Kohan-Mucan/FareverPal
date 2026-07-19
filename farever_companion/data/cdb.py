"""Data loader for game sheets and manifests.

Reads pre-cleaned data from `raw_data.py` (compiled from .pak files)
for fast runtime access.
"""
from __future__ import annotations

import json
from functools import lru_cache

from .. import paths
from . import raw_data

try:
    from . import raw_units
except ImportError:
    raw_units = None

try:
    from . import raw_items
except ImportError:
    raw_items = None

try:
    from . import raw_skills
except ImportError:
    raw_skills = None


@lru_cache(maxsize=None)
def sheet(name: str) -> dict:
    """Raw sheet dict for `name`, e.g. sheet("item")."""
    # Unified atlas access
    if name == "atlas":
        from . import atlas
        all_atlas = atlas.data()
        flattened = []
        for cat, entries in all_atlas.items():
            for eid, entry in entries.items():
                row = entry.copy()
                row["id"] = eid
                row["category"] = cat
                flattened.append(row)
        return {"lines": flattened}

    # Try core raw_data first
    data = raw_data.DATA.get(name)
    if data is None:
        plural = f"{name}s"
        data = raw_data.DATA.get(plural)

    # If not in core, try lazy-loading from split modules
    if data is None:
        if name in ("unit", "units", "lootTable") and raw_units:
            data = raw_units.DATA.get("units" if name != "lootTable" else "lootTable")
        elif name in ("item", "items") and raw_items:
            data = raw_items.DATA.get("items")
        elif name in ("skill", "skills") and raw_skills:
            data = raw_skills.DATA.get("skills")

    if data is not None:
        if isinstance(data, dict) and name == "ATLAS_DATA":
            # Flatten dictionary into lines for consistency
            return {"lines": [{"id": k, **v} for k, v in data.items()]}
        return {"lines": data}

    # Fallback to JSON if not found in pre-compiled data
    path = paths.sheets_dir() / f"{name}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"lines": []}


def lines(name: str) -> list[dict]:
    """The rows of a sheet."""
    return sheet(name).get("lines", [])


@lru_cache(maxsize=None)
def by_id(name: str, key: str = "id") -> dict[str, dict]:
    """Rows of a sheet indexed by their id (or another key)."""
    return {r[key]: r for r in lines(name) if key in r}


@lru_cache(maxsize=None)
def display_data(name: str) -> list[dict]:
    """A data manifest file (items / enemies / skills) as a flat list of rows.
    Unified data now prefers the base sheet keys (units, items, skills)."""
    # Map display names to their unified sheet keys
    key_map = {
        "enemies": "units",
        "items": "items",
        "skills": "skills"
    }

    target = key_map.get(name, name)
    
    # Try core raw_data first
    d = raw_data.DATA.get(target)

    # Check for lazy-loaded split modules
    if d is None:
        if target == "units" and raw_units:
            d = raw_units.DATA.get("units")
        elif target == "items" and raw_items:
            d = raw_items.DATA.get("items")
        elif target == "skills" and raw_skills:
            d = raw_skills.DATA.get("skills")

    # Check for legacy info_ prefix if target not found
    if d is None:
        d = raw_data.DATA.get(f"info_{name}")

    if d:
        if isinstance(d, list):
            return d
        # If it's a dict wrapping a list (e.g. {"items": [...]})
        for v in d.values():
            if isinstance(v, list):
                return v

    # Fallback to JSON if not found in pre-compiled data
    try:
        path = paths.display_data_dir() / f"{name}.json"
        d = json.loads(path.read_text(encoding="utf-8"))
        if d:
            if isinstance(d, list):
                return d
            for v in d.values():
                if isinstance(v, list):
                    return v
    except (OSError, json.JSONDecodeError):
        pass

    # Final fallback: map raw game sheets to a display format
    if name == "items":
        return [{
            "id": r["id"],
            "name": r.get("name") or r["id"],
            "rarity": r.get("rarity", "Common"),
            "type": r.get("type", "Unknown")
        } for r in lines("item")]

    if name == "enemies":
        return [{
            "id": r["id"],
            "name": r.get("name") or r["id"],
            "type": r.get("type", "Unknown"),
            "isElite": r.get("isElite", False),
            "isBoss": r.get("isBoss", False),
            "isCritter": r.get("isCritter", False)
        } for r in lines("unit")]

    return []
