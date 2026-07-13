"""Data loader for game sheets and manifests.

Reads pre-cleaned data from `raw_data.py` (compiled from .pak files)
for fast runtime access.
"""
from __future__ import annotations

import json
from functools import lru_cache

from .. import paths
from . import raw_data


@lru_cache(maxsize=None)
def sheet(name: str) -> dict:
    """Raw sheet dict for `name`, e.g. sheet("item")."""
    # Try singular (CDB style) first, then plural (Icon folder / Manifest style)
    data = raw_data.DATA.get(name)
    if data is None:
        plural = f"{name}s"
        data = raw_data.DATA.get(plural)

    if data is not None:
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
    """A data manifest file (items / enemies) as a flat list of rows.
    Falls back to raw game sheets if the display file is missing."""
    d = raw_data.DATA.get(f"info_{name}")
    if d is None:
        try:
            # Fallback to JSON
            path = paths.display_data_dir() / f"{name}.json"
            d = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            d = None

    if d:
        if isinstance(d, list):
            return d
        # If it's a dict wrapping a list (e.g. {"items": [...]})
        for v in d.values():
            if isinstance(v, list):
                return v

    # Final fallback: map raw game sheets to a display format
    if name == "items":
        return [{
            "id": r["id"],
            "name": (r.get("texts") or {}).get("name") or r["id"],
            "rarity": r.get("rarity", "Common"),
            "type": r.get("type", "Unknown")
        } for r in lines("item")]
    
    if name == "enemies":
        # unitType has the fallback names; unit.texts.name has specific ones
        utypes = by_id("unitType")
        return [{
            "id": r["id"],
            "name": (r.get("texts") or {}).get("name") 
                    or utypes.get(r.get("type", ""), {}).get("name") 
                    or r["id"],
            "type": r.get("type", "Unknown")
        } for r in lines("unit")]

    return []
