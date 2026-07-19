"""Collection-tracker catalog.

Loads the collection catalog (extracted from game files) and provides the
category list, item rows, and progress math.
"""
from __future__ import annotations

import json
from functools import lru_cache

from .. import paths
from . import raw_data


@lru_cache(maxsize=1)
def catalog() -> dict:
    """{version, categories:[{key,label,icon_sheet}], items:[...]}; {} if absent."""
    data = raw_data.DATA.get("info_collection_catalog")
    if data:
        return data

    # Fallback to JSON
    path = paths.display_data_dir() / "collection_catalog.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) and data.get("items") else {}
    except (OSError, ValueError):
        return {}


def categories() -> list[dict]:
    return list(catalog().get("categories") or [])


def items(category: str | None = None) -> list[dict]:
    rows = catalog().get("items") or []
    if category:
        rows = [r for r in rows if r.get("category") == category]
    return list(rows)


def icon_sheet(category: str) -> str:
    if category in ("mounts", "gliders", "companions"):
        return "collection"
    for c in categories():
        if c.get("key") == category:
            return c.get("icon_sheet") or "item"
    return "item"


def summary(owned: set[str]) -> dict:
    """Per-category progress {key: (collected, total)} + '_overall'."""
    out: dict[str, tuple[int, int]] = {}
    coll_all = total_all = 0
    for c in categories():
        rows = [r for r in items(c["key"]) if r.get("obtainable")]
        coll = sum(1 for r in rows if r["id"] in owned)
        out[c["key"]] = (coll, len(rows))
        coll_all += coll
        total_all += len(rows)
    out["_overall"] = (coll_all, total_all)
    return out


def matches(row: dict, query: str = "", subtype: str = "", rarity: str = "",
            state: str = "", owned: set[str] | None = None) -> bool:
    """One item row against the page's filters."""
    if subtype and row.get("subtype") != subtype:
        return False
    if rarity and row.get("rarity") != rarity:
        return False
    if state:
        is_owned = bool(owned) and row["id"] in owned
        if state == "missing" and is_owned:
            return False
        if state == "collected" and not is_owned:
            return False
    if query:
        hay = " ".join((row.get("name") or "", row.get("subtype") or "",
                        row.get("source") or "")).lower()
        if query.lower() not in hay:
            return False
    return True
