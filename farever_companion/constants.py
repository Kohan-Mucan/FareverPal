"""Single source of truth for App Globals, Calibration, and Engine Offsets.

Every magic number lives here exactly once so a concept has one name and one
value. Includes HashLink memory offsets, UI/Minimap calibration, and global
feature flags.
"""
from __future__ import annotations

# --- Branding & Versioning -------------------------------------------------
VERSION = "0.2.9"
APP_NAME = "FareverPal"


# --- HashLink runtime object model -----------------------------------------
# instance[+0] -> hl_type*; hl_type[+0]=kind(i32); hl_type[+8] -> hl_type_obj*.
HOBJ = 11            # hl_type kind: object
HSTRUCT = 21         # hl_type kind: struct
USER_MIN = 0x10000           # plausible user-space pointer range (is_ptr guard)
USER_MAX = 0x7FFFFFFFFFFF

# hl_type_obj fields
TO_NAME = 0x10       # -> name (uchar*, UTF-16LE, null-terminated)
TO_SUPER = 0x18      # -> super (hl_type*)
TO_FIELDS = 0x20     # -> fields (hl_obj_field[])
TO_GLOBALVAL = 0x38  # -> &globals_data slot (the type's static $Class object)
TO_RUNTIME = 0x48    # -> hl_runtime_obj* (rt; computed lazily by the HL runtime)
FIELD_STRIDE = 0x18  # sizeof(hl_obj_field) on 64-bit

# hl_runtime_obj (the type's resolved layout): used to look up a field's byte
# offset by name. fields_indexes[i] is the offset of the i-th field in global
# (super-first) order, so own fields are the last `hl_type_obj.nfields` entries.
# nfields(@0x08) and size(@0x10) are stable, but the fields_indexes pointer slot
# moved between builds (seen at 0x28, not the upstream 0x20), so it's auto-detected
# from these candidates: the right slot points at an int[nfields] array that starts
# at HL_WSIZE (8) and strictly ascends. Confirmed live 2026-06-04 (slot 0x28).
RT_NFIELDS = 0x08        # total field count incl. inherited (i32)
RT_SIZE = 0x10           # instance size in bytes (i32; sanity-bounds an offset)
RT_FI_CANDIDATES = (0x28, 0x20, 0x30, 0x18, 0x38)  # fields_indexes slot, tried in order
HL_WSIZE = 8             # word size; first field sits right after the hl_type* at 0

# ArrayObj[+8]=length(i32); ArrayObj[+0x10] -> NativeArray
NA_SIZE_OFF = 0x10   # NativeArray size (i32)
NA_DATA_OFF = 0x18   # NativeArray data (8B object pointers)

# --- GameObject / unit layout ----------------------------------------------
# ent.Hero / ent.Foe / ent.interactible.* share the ent.GameObject layout, so
# the same offsets read the player, enemies and interactibles.
OFF_GAMELAYER = 88
OFF_OWNER = 0x498
OFF_POS = 0x98
OFF_HEADING = 0xb0
OFF_UNITID = 0x250
OFF_UATTR = 0x3d0
OFF_LEVEL_UNIT = 0x3d8
UNIT_BLOCK = 0x4c0

# st.GameLayer arrays
OFF_UNITS_ARR = 0x128
OFF_ELEMS_ARR = 0x120
OFF_MAIN_ACTIVITY = 0xd8
                          #    null or different class in the open world / town)
OFF_CONFIG = 0xb8

# ent.Hero specific fields
OFF_HERO_OWNERPLAYER = 0x10  # ent.Hero -> st.Player (ownerPlayer)
OFF_HERO_ISCOMBAT = 0x2a8


# st.GameLayer.config {activityID:String, difficulty:Null<Int>, mapId:String}.
# The config pointer's offset on GameLayer drifts between builds (seen at 0x470
# and 0x508), so the reader discovers it: scan GameLayer for a pointer to a struct
# whose mapId (config+0x10) is a String containing 'POI' (always true inside an
# instance) and whose difficulty box (config+0x08) holds 0/1. difficulty is a
# boxed Null<Int>: box+0x08 -> i32 (0=Normal, 1=Hard). Confirmed live 2026-06-02.
OFF_CONFIG_DIFFICULTY = 0x08  # config -> difficulty (boxed Null<Int>)
OFF_CONFIG_MAPID = 0x10       # config -> mapId String (validates the struct)
OFF_BOX_VALUE = 0x08          # boxed Null<Int> -> i32 value
CONFIG_SCAN_BYTES = 0x800     # how far into GameLayer to hunt the config pointer

# interactible element fields
OFF_ELEMID = 0x268
OFF_ELEMSTATE = 0x2a0
OFF_ELEM_FX = 0x2b0

# --- *Attributes struct ----------------------------------------------------
OFF_HEALTH = 0xF0        # *Attributes + 0xF0 -> current Health (f64)

# --- st.Player struct ------------------------------------------------------
OFF_PLAYER_NAME = 0xa8
OFF_PLAYER_GROUP = 0xe8
OFF_PLAYER_ISME = 0x120

# --- Minimap & Navigation Calibration --------------------------------------
# Map-image pixel transform (px = (world + offset) * scale).
X_OFFSET = 1922.261
Y_OFFSET = 1686.423
MAP_SCALE = 0.454951
MAP_BOUNDS = (-1560.950, -1402.111, 2383.863, 2394.622)

# camera-yaw -> view rotation calibration (sign flips orbit direction, offset
# aligns "up" with the camera's forward).
CAM_YAW_SIGN = -1.0
CAM_YAW_OFFSET = 0.0


# --- Calibrator Haxe Class & Field Mapping Metadata -------------------------
# Maps Haxe class names -> Haxe field names -> constants.py variable names.
# Used by the Calibrator GUI to scan and patch offsets without requiring a separate JSON.
CALIBRATION_MAP = {
    "ent.Hero": {
        "player": "OFF_OWNER",
        "posx": "OFF_POS",
        "rotationZ": "OFF_HEADING",
        "kind": "OFF_UNITID",
        "attr": "OFF_UATTR",
        "_level": "OFF_LEVEL_UNIT",
        "gameLayer": "OFF_GAMELAYER",
        "layer": "OFF_GAMELAYER",
        "ownerPlayer": "OFF_HERO_OWNERPLAYER",
        "isInCombat": "OFF_HERO_ISCOMBAT"
    },
    "st.GameLayer": {
        "units": "OFF_UNITS_ARR",
        "interactibles": "OFF_ELEMS_ARR",
        "mainActivity": "OFF_MAIN_ACTIVITY",
        "config": "OFF_CONFIG"
    },
    "st.Player": {
        "name": "OFF_PLAYER_NAME",
        "group": "OFF_PLAYER_GROUP",
        "isMe": "OFF_PLAYER_ISME"
    },
    "ent.Element": {
        "kind": "OFF_ELEMID",
        "currentVisualState": "OFF_ELEMSTATE",
        "currentFx": "OFF_ELEM_FX"
    },
    "ent.interactible.Chest": {
        "kind": "OFF_ELEMID",
        "currentVisualState": "OFF_ELEMSTATE",
        "currentFx": "OFF_ELEM_FX"
    },
    "ent.Attributes": {
        "hp": "OFF_HEALTH",
        "life": "OFF_HEALTH",
        "health": "OFF_HEALTH"
    },
    "st.Config": {
        "difficulty": "OFF_CONFIG_DIFFICULTY",
        "mapId": "OFF_CONFIG_MAPID"
    }
}


