"""Settings page — the new consolidated overlay hub.

Built incrementally from the World-page mockup (tmp_preview/
entity_map_merge_mockup.html): every overlay's visibility layers in one
matrix with HUD / Minimap / Dungeon columns side by side, so the six layers
shared between the Entity HUD and the minimap are configured once. The old
Entity and Map pages stay untouched until every section is ported here —
sections not moved yet keep their header with a "coming next" note.

Linking: rows with both a HUD and a MAP switch can be linked (chain button,
or the MASTER bar above the matrix) so flipping one drives the other.
"""
from __future__ import annotations

import os
from PySide6 import QtCore, QtGui, QtWidgets
from shiboken6 import isValid as _is_valid

from ... import components as C
from ... import theme
from ...layout import make_scroll, span_trailing_row, StretchFlow
from ....data import icons, names, units
from ....geo.gatherables import get_display_name
from ....core.updater import is_frozen  # noqa: F401  (dps_engine uses it)
from ....runtime.sound import play_ding, play_sound, list_sounds, SOUND_LABELS

_CHIP_COLS = 3      # filter chip grid columns

# (group label, [(key, layer, color, hud_attr, map_attr, dungeon_attr)])
# None attr = that surface has no such layer (a dash is rendered instead).
_LAYER_GROUPS = [
    ("HUD & MINIMAP", [
        ("enemies", "Enemies", theme.DANGER,
         "show_enemies", "minimap_enemies", None),
        ("spark_mobs", "Spark Mobs", theme.ORANGE,
         "show_spark_mobs", "minimap_spark_mobs", None),
        ("companions", "Companions", "#7aa2f7",
         "show_companions", "minimap_companions", None),
        ("chests", "Chests", theme.GOLD,
         "show_chests", "minimap_chests", None),
        ("gatherables", "Gatherables", theme.GOOD,
         "show_gatherables", "minimap_gatherables", None),
        ("orbs", "Secret orbs", theme.KIND_COLOR["orb"],
         "show_orbs", "minimap_orbs", "dungeon_show_orbs"),
        # Players / Dropped loot (2026-09-19): these two used to be their own
        # "DUNGEON HUD" group at the very bottom of the tab, which nobody
        # scrolled to — so the switches looked missing. They are HUD layers
        # like the rest, so they live in this group now.
        ("players", "Players", theme.GOOD,
         "show_group_members", "minimap_players", "dungeon_show_players"),
        ("loot", "Dropped loot", theme.KIND_COLOR["activity"],
         "show_loot", None, "dungeon_show_loots"),
    ]),
    ("MAP POINTS OF INTEREST", [
        ("obelisks", "Obelisks / Respawn", theme.KIND_COLOR["obelisk"],
         None, "minimap_obelisks", None),
        ("dungeons", "Dungeons", theme.KIND_COLOR["dungeon"],
         None, "minimap_dungeons", None),
        ("vendors", "Vendors", theme.KIND_COLOR["vendor"],
         None, "minimap_vendors", None),
        ("soulstones", "Soulstone Bosses", theme.KIND_COLOR["soulstone"],
         None, "minimap_soulstones", None),
    ]),
]

# Extra per-layer options revealed by clicking a matrix row's name.
# key -> [(attr, label, lo, hi, step)]
_LAYER_EXTRA = {
    "gatherables": [
        ("gatherable_count", "Gatherables shown", 1, 30, 1),
    ],
}

# Gatherable type picker shown when the Gatherables matrix row is expanded.
# (group label, [type_key, ...]) — display names come from geo.gatherables.
# Flowers vs Ore (rendered as ore-orb nodes) so the long flat list folds away.
_GATHER_GROUPS = [
    ("Flowers", ["lavendula", "madrigold", "zealotus", "ancientthyme"]),
    ("Ore", ["copperore", "tinore", "tungstene"]),
]

# Layers drawn from the single master glyph recolored to the layer's accent
# (same approach as the minimap's ore rendering in minimap_render).
_RECOLOR_GLYPHS = {
    "gatherables": "ore",
}

# Real atlas sprite (assets/atlas/atlas_minimap_01.webp) for each layer row
# that has one — rendered via icons.marker() with the layer color outline, so
# a missing sheet still degrades to a plain color dot. Layers without a
# suitable sprite (enemies, companions, players, loot) keep the dot.
_LAYER_SPRITES = {
    "enemies": "sword",
    "spark_mobs": "sparkdust",
    "companions": "heart",        # overridden by the cat emoji below
    "chests": "chest",
    "gatherables": "gatherable",  # recolored to the ore glyph (see _RECOLOR_GLYPHS)
    "orbs": "orb",
    "obelisks": "respawnpoint",
    "dungeons": "dungeon",
    "vendors": "shop",
    "soulstones": "soulstone",
    "players": "player",
    "loot": "box",
}

# A few layers have no matching atlas sprite, so they get an inline glyph/emoji
# drawn in the row's accent color instead (cheaper than a one-off SVG).
_LAYER_EMOJI = {
    "companions": "😺",
}

_LAYER_DESCS = {
    "enemies": "Hostile enemy radar, level & health bars",
    "spark_mobs": "Special spark dust elite mob tracking",
    "companions": "Tamed pets, companions & summon minions",
    "chests": "Treasure chests, loot containers & locks",
    "gatherables": "Harvestable flowers, herbs & ore nodes",
    "orbs": "Secret realm discovery orbs",
    "obelisks": "Fast travel waypoints & respawn beacons",
    "dungeons": "Instanced dungeon & raid portal entrances",
    "vendors": "NPC merchants, Mounts, Gliders, Pets, Recipe shops",
    "soulstones": "Soulstone Boss arenas & summon shrines",
    "players": "Multiplayer co-op party member radar",
    "loot": "Ground item drops & gear loot beacons",
}



# Rarity floor for both LOOT sections: Off / All show every drop, the rest
# hide anything below the picked floor (matches loot_rows.build_loot_specs).
_LOOT_OPTS = ["Off", "All", "Uncommon+", "Rare+", "Epic+", "Legendary"]

_LOOT_COLORS = {
    "Off": theme.MUTED,
    "All": theme.TEXT,
    "Common+": theme.RARITY.get("Common", "#c2c6d0"),
    "Uncommon+": theme.RARITY.get("Uncommon", "#56d364"),
    "Rare+": theme.RARITY.get("Rare", "#539bf5"),
    "Epic+": theme.RARITY.get("Epic", "#c297ff"),
    "Legendary": theme.RARITY.get("Legendary", "#f0a836"),
}


# Star-import surface used by every split mixin module (and the settings
# package __init__): the full module-level namespace of the original
# settings.py, so moved method bodies resolve the same global names.
__all__ = [n for n in globals() if not n.startswith("__")]
