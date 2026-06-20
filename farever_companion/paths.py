"""Central path resolver.

All reusable data lives in the existing workspace, not duplicated into this
project:
    - CDB sheets        D:\\Projects\\FareverFandom\\data\\sheets\\*.json
    - wiki data layer   ...\\htdocs\\assets\\data\\{items,enemies}.json
    - icons             ...\\htdocs\\assets\\icons\\{item,unit,skill,_shared}\\
    - chest index       <workspace>\\notes\\chest_loot_index.json (see notes_dir)

In a PyInstaller one-file build these are copied next to the bundle; we check
the frozen `_MEIPASS` dir first, then fall back to walking up to the repo root.
Every module imports its data location from here, one place to change.
"""
from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path

# Marker that identifies the repo root (present in dev; copied into bundles).
_MARKER = Path("data") / "sheets" / "lootTable.json"


@lru_cache(maxsize=1)
def data_root() -> Path:
    """Directory that contains `data/sheets/`, `htdocs/assets/`, etc."""
    # 1) PyInstaller one-file: bundled data sits under sys._MEIPASS.
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        cand = Path(meipass)
        if (cand / _MARKER).exists():
            return cand
    # 2) dev: walk up from this file until the marker is found.
    for parent in Path(__file__).resolve().parents:
        if (parent / _MARKER).exists():
            return parent
    raise FileNotFoundError(
        f"could not locate {_MARKER} above {__file__} or in a bundle"
    )


def sheets_dir() -> Path:
    return data_root() / "data" / "sheets"


def wiki_data_dir() -> Path:
    return data_root() / "htdocs" / "assets" / "data"


def icons_dir() -> Path:
    return data_root() / "htdocs" / "assets" / "icons"


def notes_dir() -> Path:
    return data_root() / "notes"


def chest_index_path() -> Path:
    return sheets_dir() / "chest_loot_index.json"


def chest_positions_path() -> Path:
    return sheets_dir() / "chest_positions.json"


def orb_positions_path() -> Path:
    return sheets_dir() / "orb_positions.json"


@lru_cache(maxsize=1)
def project_root() -> Path:
    """This project's own root (companion/), for caches and config defaults."""
    for parent in Path(__file__).resolve().parents:
        if parent.name == "companion" and (parent / "run.py").exists():
            return parent
    return Path(__file__).resolve().parent.parent


def cache_dir() -> Path:
    d = project_root() / "notes"
    d.mkdir(parents=True, exist_ok=True)
    return d


@lru_cache(maxsize=1)
def assets_dir() -> Path:
    """This app's own bundled assets (fonts, UI-chrome SVG icons).

    Unlike the game data, these live inside the project (`companion/assets/`)
    and are copied next to the bundle by `package.bat`. Frozen build first
    (`_MEIPASS/assets`), then the dev tree.
    """
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        cand = Path(meipass) / "assets"
        if cand.exists():
            return cand
    return project_root() / "assets"
