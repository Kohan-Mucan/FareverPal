"""Collection-tracker catalog.

Loads the compiled collection catalog (derived from codex.json by the
compiler, never from the game dump's raw collection_catalog.json) and
provides the category list, item rows, and progress math.
"""
from __future__ import annotations

from functools import lru_cache

from . import raw_data


# Fields every catalog row must carry for the Collection page to render it
# (account sync keys by id; the tooltip reads name/subtype/source; summary
# counts only obtainable rows). Newer game dumps ship a lore 'Collection' list
# of {name, category, description, unlocked} that maps to NONE of these — the
# compiler derives the real catalog from codex.json, and this validation keeps
# a stale/mismatched payload from ever reaching the UI (it would crash on
# `it['subtype']` / `it['id']`).
_REQUIRED_ROW_FIELDS = ("id", "name", "category", "subtype", "obtainable", "source")


def _usable_catalog(data) -> bool:
    """True when `data` is the {version?, categories?, items} shape the page
    actually renders: a dict whose rows all carry the fields the UI reads."""
    if not isinstance(data, dict):
        return False
    items = data.get("items")
    if not isinstance(items, list) or not items:
        return False
    return all(isinstance(r, dict) and all(k in r for k in _REQUIRED_ROW_FIELDS)
               for r in items)


@lru_cache(maxsize=1)
def catalog() -> dict:
    """{version, categories:[{key,label,icon_sheet}], items:[...]}; {} if absent."""
    data = raw_data.DATA.get("info_collection_catalog")
    return data if _usable_catalog(data) else {}


# Canonical category order (the game's tab order). Used both to sort the
# derived categories and as a stable fallback when a source catalog omits
# its `categories` array (items carry `category` either way).
_CATEGORY_ORDER = ("mounts", "gliders", "companions")


def categories() -> list[dict]:
    cats = list(catalog().get("categories") or [])
    if cats:
        return cats
    # Derive from the items so the page keeps its tabs/progress even when a
    # source catalog ships only {version, items}.
    seen = []
    for r in catalog().get("items") or []:
        k = r.get("category")
        if k and k not in seen:
            seen.append(k)
    ordered = [k for k in _CATEGORY_ORDER if k in seen] + [k for k in seen if k not in _CATEGORY_ORDER]
    return [{"key": k, "label": k.capitalize(), "icon_sheet": "collection"}
            for k in ordered]


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
