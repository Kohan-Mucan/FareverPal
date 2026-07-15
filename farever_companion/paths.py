"""Central path resolver.

Data is pre-extracted from game .pak files and compiled into Python scripts
inside the project for performance and portability.
"""
from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path

@lru_cache(maxsize=1)
def data_root() -> Path:
    """Directory that contains the consolidated data script."""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    return project_root() / "farever_companion" / "data"

def sheets_dir() -> Path:
    """Fallback directory for raw JSON sheets (lootTable, unit, etc)."""
    # 1. Try the bundled data/sheets folder (from htdocs/data/sheets)
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        cand = Path(meipass) / "data" / "sheets"
        if cand.exists():
            return cand
    
    # 2. Try the local assets/data folder
    local = project_root() / "assets" / "data"
    if local.exists():
        return local
        
    # 3. Fallback to sibling
    return project_root().parent / "htdocs" / "data" / "sheets"

def display_data_dir() -> Path:
    """Folder for the consolidated display JSON files (items, enemies, etc)."""
    return _htdocs_data()

def icons_dir() -> Path:
    """Folder containing Items/Units/Skills subfolders with PNGs/WebPs."""
    # 1 & 2: Handles Bundled (EXE) and Local Project automatically via assets_dir()
    local = assets_dir() / "icons"
    if local.exists():
        # Health check: do any of our plural folders exist?
        if (local / "Items").exists() or (local / "Units").exists() or (local / "Skills").exists():
            return local
            
    # 3. Fallback to sibling htdocs (Legacy / Other Dev)
    return project_root().parent / "htdocs" / "assets" / "icons"

def map_icons_dir() -> Path:
    """Folder containing map markers (chests, orbs, etc) as SVGs/WebPs."""
    # 1 & 2: Bundled (EXE) and Local Project
    local = assets_dir() / "map_icons"
    if local.exists():
        return local

    # 3. Fallback to sibling htdocs (External)
    return project_root().parent / "htdocs" / "assets" / "map_icons"

def atlas_dir() -> Path:
    """Folder containing the atlas sprite-sheets and index."""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass) / "assets" / "atlas"
    return project_root() / "assets" / "atlas"

def notes_dir() -> Path:
    return data_root() / "tools"

def _htdocs_data() -> Path:
    """assets/data/ — where all consolidated game data JSON lives."""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass) / "assets" / "data"
    # Try local first
    local = project_root() / "assets" / "data"
    if local.exists():
        return local
    # Fallback to sibling
    return project_root().parent / "htdocs" / "assets" / "data"

def chest_locs_path() -> Path:
    """Scan-generated chest locations (split from map_markers)."""
    return _htdocs_data() / "chest_locs.json"

def poi_locs_path() -> Path:
    """Scan-generated POI locations — dungeons, bosses (split from map_markers)."""
    return _htdocs_data() / "poi_locs.json"

def orb_positions_path() -> Path:
    """Scan-generated orb positions (not yet in map_markers — pending scan)."""
    return _htdocs_data() / "orb_positions.json"

def critter_locs_path() -> Path:
    """Scan-generated critter/companion spawner locations."""
    return _htdocs_data() / "critter_locs.json"

def gatherable_locs_path() -> Path:
    """Scan-generated static gatherable node locations."""
    return _htdocs_data() / "gatherable_locs.json"

@lru_cache(maxsize=1)
def project_root() -> Path:
    """This project's own root (FareverPal/), for caches and config defaults."""
    for parent in Path(__file__).resolve().parents:
        if (parent / "run.py").exists() and (parent / "farever_companion").exists():
            return parent
    return Path(__file__).resolve().parent.parent

def cache_dir() -> Path:
    d = project_root() / "cache"
    d.mkdir(parents=True, exist_ok=True)
    return d

@lru_cache(maxsize=1)
def assets_dir() -> Path:
    """This app's own bundled assets (fonts, UI-chrome SVG icons)."""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        cand = Path(meipass) / "assets"
        if cand.exists():
            return cand
    return project_root() / "assets"
