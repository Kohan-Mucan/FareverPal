"""Single source of truth for HashLink layout + game struct offsets.

Every offset lives here exactly once and is imported by the readers (`hl.py`,
`scene.py`, `attributes.py`, `player.py`, `appsingleton.py`) so a concept has
one name and one value. Values are validated against a specific Farever build
and resolved by class name through `hl.py` reflection where practical; the
build-validated dates stay with the readers that document each group.

Calibration-pending offsets (e.g. the per-skill level offset, the isMe path)
stay as `None`-defaulted constants in their own module, they're not shared, so
they don't belong here.
"""
from __future__ import annotations

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
OFF_GAMELAYER = 0x58     # GameObject -> st.GameLayer
OFF_OWNER = 0x60         # GameObject -> owner (a Hero for player-owned units)
OFF_POS = 0x98           # x,y,z contiguous f64 (the player's own position too)
OFF_HEADING = 0xB0       # facing angle, f64 radians (atan2: 0=+x, CCW)
OFF_UNITID = 0x250       # unit-id String
OFF_UATTR = 0x3D0        # GameObject -> ent.*Attributes
OFF_LEVEL_UNIT = 0x3D8   # live level (i32) on the unit struct
UNIT_BLOCK = 0x258       # batched header read: type(0) .. unit-id(0x250)

# st.GameLayer arrays
OFF_UNITS_ARR = 0x128    # -> ArrayObj of ent.Hero / ent.Foe subclasses
OFF_ELEMS_ARR = 0x120    # -> ArrayObj of interactibles (no units)

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
OFF_ELEMID = 0x268       # element-id String
OFF_ELEMSTATE = 0x2a0    # element state String

# --- *Attributes struct ----------------------------------------------------
OFF_HEALTH = 0xF0        # *Attributes + 0xF0 -> current Health (f64)
