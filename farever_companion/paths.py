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
    """Directory for raw JSON sheets (lootTable, unit, etc)."""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        cand = Path(meipass) / "data" / "sheets"
        if cand.exists():
            return cand
    
    return project_root() / "assets" / "data"

def display_data_dir() -> Path:
    """Folder for the consolidated display JSON files (items, enemies, etc)."""
    return _game_data_dir()

def icons_dir() -> Path:
    """Folder containing Items/Units/Skills subfolders with PNGs/WebPs."""
    return assets_dir() / "icons"

def map_icons_dir() -> Path:
    """Folder containing map markers (chests, orbs, etc) as SVGs/WebPs."""
    return assets_dir() / "map_icons"

def atlas_dir() -> Path:
    """Folder containing the atlas sprite-sheets and index."""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass) / "assets" / "atlas"
    return project_root() / "assets" / "atlas"

def notes_dir() -> Path:
    return data_root() / "tools"

def _game_data_dir() -> Path:
    """assets/data/ — where all consolidated game data JSON lives."""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass) / "assets" / "data"
    return project_root() / "assets" / "data"

def item_drops_path() -> Path:
    """assets/data/item_drops.json — the item-centric drop index (item -> sources)."""
    return _game_data_dir() / "item_drops.json"


def craft_path() -> Path:
    """assets/data/craft.json — the crafting recipe sheet (recipe -> job / materials)."""
    return _game_data_dir() / "craft.json"


def job_path() -> Path:
    """assets/data/job.json — the job/profession sheet (job id -> name / tool)."""
    return _game_data_dir() / "job.json"


def chest_locs_path() -> Path:
    """Scan-generated chest locations (split from map_markers)."""
    return _game_data_dir() / "chest_locs.json"

def poi_locs_path() -> Path:
    """Scan-generated POI locations — dungeons, bosses (split from map_markers)."""
    return _game_data_dir() / "poi_locs.json"

def mob_locs_path() -> Path:
    """Scan-generated mob spawn locations (world spawns)."""
    return _game_data_dir() / "mob_locs.json"

def orb_positions_path() -> Path:
    """Scan-generated orb positions (not yet in map_markers — pending scan)."""
    return _game_data_dir() / "orb_positions.json"

def critter_locs_path() -> Path:
    """Scan-generated critter/companion spawner locations."""
    return _game_data_dir() / "critter_locs.json"

def gatherable_locs_path() -> Path:
    """Scan-generated static gatherable node locations."""
    return _game_data_dir() / "gatherable_locs.json"

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
