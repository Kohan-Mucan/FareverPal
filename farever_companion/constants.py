"""Single source of truth for App Globals, Calibration, and Engine Offsets.

Every magic number lives here exactly once so a concept has one name and one
value. Includes HashLink memory offsets, UI/Minimap calibration, and global
feature flags.
"""
from __future__ import annotations

# --- Branding & Versioning -------------------------------------------------
VERSION = "0.3.0"
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

RT_NFIELDS = 0x08        # total field count incl. inherited (i32)
RT_SIZE = 0x10           # instance size in bytes (i32; sanity-bounds an offset)
RT_FI_CANDIDATES = (0x28, 0x20, 0x30, 0x18, 0x38)  # fields_indexes slot, tried in order
HL_WSIZE = 8             # word size; first field sits right after the hl_type* at 0

# ArrayObj[+8]=length(i32); ArrayObj[+0x10] -> NativeArray
NA_SIZE_OFF = 0x10   # NativeArray size (i32)
NA_DATA_OFF = 0x18   # NativeArray data (8B object pointers)

# --- GameObject / unit layout ----------------------------------------------
OFF_GAMELAYER = 0x0
OFF_OWNER = 0x0
OFF_POS = 0x0
OFF_HEADING = 0x0
OFF_UNITID = 0x0
OFF_UATTR = 0x0
OFF_LEVEL_UNIT = 0x0
UNIT_BLOCK = 0x0

# st.GameLayer arrays
OFF_UNITS_ARR = 0x0
OFF_ELEMS_ARR = 0x0
OFF_MAIN_ACTIVITY = 0x0
OFF_CONFIG = 0x0

# ent.Hero specific fields
OFF_HERO_OWNERPLAYER = 0x0
OFF_HERO_ISCOMBAT = 0x0
OFF_FOE_OWNER = 0x0


# st.GameLayer.config
OFF_CONFIG_DIFFICULTY = 0x0
OFF_CONFIG_MAPID = 0x0
OFF_RIFT_BOOL = 0x0
OFF_WORLD_MAPID = 0x0
OFF_BOX_VALUE = 0x0
CONFIG_SCAN_BYTES = 0x800

OFF_CONFIG_CANDIDATES = [0x0]
OFF_CONFIG_MAPID_CANDIDATES = [0x0]
OFF_CONFIG_DIFF_CANDIDATES = [0x0]

# --- *Attributes struct ----------------------------------------------------
OFF_HEALTH = 0x0

# --- st.Player struct ------------------------------------------------------
OFF_PLAYER_NAME = 0x0
OFF_PLAYER_GROUP = 0x0
OFF_PLAYER_ISME = 0x0

# --- Minimap & Navigation Calibration --------------------------------------
X_OFFSET = 0.0
Y_OFFSET = 0.0
MAP_SCALE = 1.0
MAP_BOUNDS = (0.0, 0.0, 0.0, 0.0)

CAM_YAW_SIGN = 1.0
CAM_YAW_OFFSET = 0.0


# --- Calibrator Haxe Class & Field Mapping Metadata -------------------------
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
