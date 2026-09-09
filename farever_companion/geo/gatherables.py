"""Static gatherable node positions (Ores, Plants)."""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

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

_GATHER_TYPE_KEYS = {
    "lavendula": "lavendula",
    "r2plant": "lavendula",
    "r2plant1": "lavendula",
    "r2plant2": "lavendula",
    "r2plant3": "lavendula",
    "r2plantrare": "lavendula",
    "madrigold": "madrigold",
    "zealotus": "zealotus",
    "ancientthyme": "ancientthyme",
    "copperore": "copperore",
    "tinore": "tinore",
    "tungstene": "tungstene",
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

def get_type_key(label: str) -> str | None:
    """Get the canonical gatherable type key for filtering (e.g. "lavendula").

    Used by the overlays together with Settings.show_gatherable_types to decide
    whether a node of this type should be shown.
    """
    base_name = get_base_name(label)
    base_lower = base_name.lower()
    return _GATHER_TYPE_KEYS.get(base_lower)

# The static index is the OVERWORLD node map — it exists to plot fixed nodes
# out in the world. Nodes inside an instanced dungeon are read live from the
# scene (`model.gatherables`) when that instance is loaded, so they must not
# enter this index. The 2026-09-11 data regen added 18 instance nodes
# (Z1_POI_Dungeon_KoboldsMines, Z2_POI_Dungeon_ManfishAbyss) whose instance-
# local coordinates overlap the overworld minimap. Dungeon prefabs name their
# world `..._POI_Dungeon_...`; overworld nodes carry the world prefab
# (e.g. 'W1_Siagarta').
_INSTANCE_WORLD_MARKERS = ("_POI_", "Dungeon")


def is_overworld_node(world: str | None) -> bool:
    """False for a node whose `world` is a dungeon instance, not the overworld."""
    w = world or ""
    return not any(m in w for m in _INSTANCE_WORLD_MARKERS)


@lru_cache(maxsize=1)
def load_nodes() -> list[GatherableNode]:
    """Load all static OVERWORLD nodes from compiled raw data."""
    from ..data import cdb
    nodes = []
    for entry in cdb.lines("gatherable_locs"):
        world = entry.get("world", "W1_Siagarta")
        if not is_overworld_node(world):
            continue
        nodes.append(GatherableNode(
            name=entry["name"],
            x=float(entry["x"]),
            y=float(entry["y"]),
            z=float(entry["z"]),
            world=world
        ))
    return nodes
