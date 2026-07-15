"""Atlas sprite-sheet data management.

Handles merging JSON coordinate files, category-based namespacing, and entry
lookup. Used by icons.py to resolve IDs to texture coordinates.
"""
from __future__ import annotations

import json
from pathlib import Path
from functools import lru_cache

from .. import paths

@lru_cache(maxsize=1)
def data():
    """Atlas sprite-sheet coordinates. Merges all JSONs in the atlas folder.
    Returns a nested dict: {category_lower: {id_lower: entry_dict}}.
    Fallbacks and individual files without category go into a 'misc' category.
    """
    from ..data import raw_data
    
    combined = {}
    atlas_dir = paths.atlas_dir()
    
    def _add_entry(entry_id, entry_val):
        cat = str(entry_val.get("category", "misc")).lower()
        if cat not in combined:
            combined[cat] = {}
        combined[cat][entry_id.lower()] = entry_val

    # 1. Merge all JSON files found in the atlas directory
    if atlas_dir.exists():
        for p in atlas_dir.glob("*.json"):
            if p.name == "atlas_index.json":
                continue
            try:
                content = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(content, dict):
                    for key, value in content.items():
                        _add_entry(key, value)
            except Exception:
                continue
                
    # 2. Fall back to data from game extraction
    fallback = raw_data.DATA.get("ATLAS_DATA", {})
    for k, v in fallback.items():
        # Only add if not already present from JSON (JSON takes priority)
        cat = str(v.get("category", "misc")).lower()
        if cat not in combined or k.lower() not in combined[cat]:
            _add_entry(k, v)
            
    return combined

_SHEET_MAP = {
    "units": "enemies",
    "enemies": "enemies",
    "items": "items",
    "skills": "skills",
}

def find_entry(category: str | None, entry_id: str):
    """Search for an icon ID in the atlas, optionally preferring a category."""
    all_data = data()
    entry_id = entry_id.lower()
    
    # 1. Try specified category (mapped to canonical plural if applicable)
    if category:
        cat = category.lower()
        cat = _SHEET_MAP.get(cat, cat)
        if cat in all_data and entry_id in all_data[cat]:
            return all_data[cat][entry_id]
            
    # 2. Try 'misc' or 'minimap' as common fallbacks
    for fallback in ("misc", "minimap"):
        if fallback in all_data and entry_id in all_data[fallback]:
            return all_data[fallback][entry_id]

    # 3. Last resort: search all categories
    for cat_dict in all_data.values():
        if entry_id in cat_dict:
            return cat_dict[entry_id]
    return None

def resolve_path(gfx_file: str) -> Path | None:
    """Resolve an atlas gfx path to an actual file on disk."""
    name = Path(gfx_file).name
    # 1. Try local atlas folder first
    p = paths.atlas_dir() / name
    if p.exists():
        return p
        
    # 2. Fall back to standard icon bases
    for base in (
        paths.icons_dir(),
        paths.icons_dir() / "Skills",
        paths.icons_dir() / "Items",
        paths.icons_dir() / "Units",
    ):
        for ext in ("webp", "png"):
            p = base / f"{name}.{ext}" if "." not in name else base / name
            if not p.exists() and "." not in name:
                p = base / name
            if p.exists():
                return p
    # Last resort: preserve relative structure under icons_dir
    p = paths.icons_dir() / gfx_file
    if p.exists():
        return p
    return None
