"""Static gatherable node positions (Ores, Plants)."""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache

from .. import paths

@dataclass(frozen=True)
class GatherableNode:
    name: str
    x: float
    y: float
    z: float
    world: str = "W1_Siagarta"

# Shared mappings for gatherable names and icons
_DISPLAY_NAME_MAPPING = {
    "r2plant": "Lavendula",
    "r2plant1": "Lavendula",
    "r2plant2": "Lavendula",
    "r2plant3": "Lavendula",
    "r2plantrare": "Lavendula",
    "madrigold": "Madrigold",
    "zealotus": "Zealotus",
    "lavendula": "Lavendula",
    "ancientthyme": "AncientThyme",
    "copperore": "CopperOre",
    "tinore": "TinOre",
    "tungstene": "Tungstene",
}

_ICON_MAPPING = {
    "lavendula": "lavendula",
    "madrigold": "madrigold",
    "ancientthyme": "ancientthyme",
    "copperore": "copperore",
    "tinore": "tinore",
    "tungstene": "tungstene",
    # Technical name mappings
    "r2plant": "lavendula",
    "r2plant1": "lavendula",
    "r2plant2": "lavendula",
    "r2plant3": "lavendula",
    "r2plantrare": "lavendula",
}

_SETTING_MAPPING = {
    "lavendula": "show_lavendula",
    "r2plant": "show_lavendula",
    "r2plant1": "show_lavendula",
    "r2plant2": "show_lavendula",
    "r2plant3": "show_lavendula",
    "r2plantrare": "show_lavendula",
    "madrigold": "show_madrigold",
    "zealotus": "show_zealotus",
    "ancientthyme": "show_ancientthyme",
    "copperore": "show_copperore",
    "tinore": "show_tinore",
    "tungstene": "show_tungstene",
}

def get_base_name(label: str) -> str:
    """Extract base name from label by removing underscore suffixes."""
    return label.split('_')[0]

def get_display_name(label: str) -> str:
    """Get human-readable display name for a gatherable label."""
    base_name = get_base_name(label)
    base_lower = base_name.lower()
    return _DISPLAY_NAME_MAPPING.get(base_lower, base_name)

def get_icon_name(label: str) -> str:
    """Get icon file name for a gatherable label."""
    base_name = get_base_name(label)
    base_lower = base_name.lower()
    return _ICON_MAPPING.get(base_lower, base_lower)

def get_setting_attr(label: str) -> str | None:
    """Get settings attribute name for filtering a gatherable type."""
    base_name = get_base_name(label)
    base_lower = base_name.lower()
    return _SETTING_MAPPING.get(base_lower)

@lru_cache(maxsize=1)
def load_nodes() -> list[GatherableNode]:
    """Load all static nodes from gatherable_locs.json."""
    path = paths.gatherable_locs_path()
    if not path.exists():
        return []
            
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        nodes = []
        for entry in data.get("gatherables", []):
            nodes.append(GatherableNode(
                name=entry["name"],
                x=float(entry["x"]),
                y=float(entry["y"]),
                z=float(entry["z"]),
                world=entry.get("world", "W1_Siagarta")
            ))
        return nodes
    except (json.JSONDecodeError, KeyError, Exception):
        return []
