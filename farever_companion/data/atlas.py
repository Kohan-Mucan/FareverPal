"""Atlas sprite-sheet data management.

Handles merging JSON coordinate files, category-based namespacing, and entry
lookup. Used by icons.py to resolve IDs to texture coordinates.
"""
from __future__ import annotations

import json
from pathlib import Path
from functools import lru_cache

from .. import paths
from . import raw_data

# Dev-only source switch: when bypassed, `find_entry` returns None so every
# icon renderer falls through to the loose fallback folder instead of the
# compiled atlas. Driven by the (git-ignored) dev icon-preview page so a new
# icon can be tested from the fallback folder before it is compiled in.
_BYPASS = False


def set_bypass(value: bool) -> None:
    global _BYPASS
    _BYPASS = bool(value)


def bypass() -> bool:
    return _BYPASS

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


@lru_cache(maxsize=1)
def data():
    """Atlas sprite-sheet coordinates. Merges all JSONs in the atlas folder.
    Returns a nested dict: {category_lower: {id_lower: entry_dict}}.
    Fallbacks and individual files without category go into a 'misc' category.
    The embedded raw_* payloads and the bundled atlas JSONs ship in the same
    coordinate space as the sheets (compiler.py compiles them together), so
    no resolution scaling is needed here.
    """
    combined = {}
    atlas_dir = paths.atlas_dir()
    
    def _add_entry(entry_id, entry_val):
        cat = str(entry_val.get("category", "misc")).lower()
        clean_id = str(entry_id).lower()
        if clean_id.startswith("[") and "]" in clean_id:
            clean_id = clean_id.split("]", 1)[1].strip()
            
        target_cats = {cat}
        if cat.startswith("enemies") or cat in ("enemies", "units", "unit", "enemy"):
            target_cats.update(["enemies", "units", "unit", "enemy"])
        if cat.startswith("dungeon"):
            target_cats.update(["dungeons", "dungeon", "enemies", "units", "unit"])
        if cat.startswith("collection"):
            target_cats.update(["collection", "units", "unit"])
        if cat.startswith("item"):
            target_cats.update(["items", "item"])
        if cat.startswith("skill"):
            target_cats.update(["skills", "skill"])
        if cat.startswith("minimap") or cat in ("minimap", "map"):
            target_cats.update(["minimap", "map", "map_icons", "map_icon"])
            
        clean_nodash = clean_id.replace("_", "").replace("-", "").replace(" ", "")
        for c in target_cats:
            if c not in combined:
                combined[c] = {}
            combined[c][clean_id] = entry_val
            combined[c][str(entry_id).lower()] = entry_val
            combined[c][clean_nodash] = entry_val

    # 1. Start with lazy-loaded module fallbacks (Lowest priority)
    from . import cdb
    for sheet_name in ("units", "items", "skills"):
        cdb.sheet(sheet_name) # This triggers lazy load
        mod_atlas = {}
        if sheet_name == "units" and raw_units:
            mod_atlas = raw_units.DATA.get("atlas", {})
        elif sheet_name == "items" and raw_items:
            mod_atlas = raw_items.DATA.get("atlas", {})
        elif sheet_name == "skills" and raw_skills:
            mod_atlas = raw_skills.DATA.get("atlas", {})
            
        for k, v in mod_atlas.items():
            _add_entry(k, v)

    # 2. Fall back to data from core game extraction (Medium priority)
    fallback = raw_data.DATA.get("ATLAS_DATA", {})
    for k, v in fallback.items():
        _add_entry(k, v)
                
    # 3. Merge atlas JSON files found in the atlas directory (Highest priority - Dev overrides).
    if atlas_dir.exists():
        _CATS = ("enemies", "items", "skills", "collection", "minimap", "dungeons", "units")
        sheet_files = [
            p for p in atlas_dir.glob("atlas_*.json")
            if "index" not in p.name
            and any(p.name.startswith(f"atlas_{c}") for c in _CATS)
        ]
        if not sheet_files:
            legacy = atlas_dir / "atlas_map.json"
            if legacy.exists():
                sheet_files = [legacy]
        for path in sorted(sheet_files):
            try:
                entries = json.loads(path.read_text(encoding="utf-8"))
                for k, v in entries.items():
                    if isinstance(v, dict):
                        _add_entry(k, v)
            except Exception:
                pass
            
    return combined

_SHEET_MAP = {
    "units": "units",
    "unit": "units",
    "enemies": "units",
    "enemy": "units",
    "items": "items",
    "item": "items",
    "skills": "skills",
    "skill": "skills",
    "dungeons": "dungeons",
    "dungeon": "dungeons",
    "collection": "collection",
    "minimap": "minimap",
    "map": "minimap",
    "map_icons": "minimap",
    "map_icon": "minimap",
}

def find_entry(category: str | None, entry_id: str):
    """Search for an icon ID in the atlas, strictly scoping to the category when given."""
    if _BYPASS:
        return None
    if not entry_id:
        return None
    all_data = data()
    entry_id = str(entry_id).lower().strip()
    clean_id = entry_id
    if clean_id.startswith("[") and "]" in clean_id:
        clean_id = clean_id.split("]", 1)[1].strip()
    clean_nodash = clean_id.replace("_", "").replace("-", "").replace(" ", "")

    def _lookup(d):
        if not d: return None
        for k in (clean_id, entry_id, clean_nodash):
            if k in d: return d[k]
        return None
    
    # 1. Try specified category
    if category:
        cat = category.lower()
        cat = _SHEET_MAP.get(cat, cat)
        res = _lookup(all_data.get(cat))
        if res:
            return res
        # Unit-family fallback (enemies / collection / dungeons)
        if cat in ("units", "enemies", "collection", "dungeons", "unit", "enemy"):
            for fallback in ("units", "enemies", "collection", "dungeons"):
                res = _lookup(all_data.get(fallback))
                if res:
                    return res
        return None

    # 2. Only when no category is specified at all: search all categories
    for cat_dict in all_data.values():
        res = _lookup(cat_dict)
        if res:
            return res
    return None

@lru_cache(maxsize=None)
def resolve_path(gfx_file: str) -> Path | None:
    """Resolve an atlas gfx path to an actual file on disk.

    Cached: this is called once per icon id on every cold page build (the
    items list alone resolves ~850 entries), and each call sweeps the disk
    with several `exists()` stats. The bundled asset tree never changes at
    runtime, so the mapping is stable for the app's lifetime."""
    name = Path(gfx_file).name
    # 1. Try local atlas folder first
    p = paths.atlas_dir() / name
    if p.exists():
        return p
        
    # 2. Fall back to standard icon bases
    for base in (
        paths.assets_dir(),
        paths.icons_dir(),
        paths.icons_dir() / "Skills",
        paths.icons_dir() / "Items",
        paths.icons_dir() / "Units",
    ):
        if not base.exists():
            continue
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
