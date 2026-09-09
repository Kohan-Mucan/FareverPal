"""Synthetic PE images for the import/export reader.

`core/pe_imports.py` answers questions about the *game's* headers - "will
Windows load this proxy name?" and "does our proxy export what the game
imports?" - so its tests need real PE tables to read. Building a
minimal-but-valid one here keeps those tests hermetic: no game install, no
compiler, no dps_bridge binaries, and no dependence on whatever Windows version
happens to be running the suite.

Layout written by build_pe() (one .rdata, RVA 0x1000 == file 0x1000):

    0x1000  import descriptors        (20 bytes each, zero-terminated)
    0x1200  delay-load descriptors    (32 bytes each, zero-terminated)
    0x1300  export directory          (IMAGE_EXPORT_DIRECTORY, 40 bytes)
    0x1330  export AddressOfFunctions
    0x1360  export AddressOfNames
    0x1390  export AddressOfNameOrdinals
    0x1400  imported DLL name strings
    0x1600  import-by-name entries    (WORD hint + name)
    0x1800  import thunk arrays       (qword per function, zero-terminated)
    0x1C00  exported name strings
"""
from __future__ import annotations

import os
import struct

_OPT = 0x58              # optional header offset
_OPT_SIZE = 0xF0
_SEC_TABLE = _OPT + _OPT_SIZE
_DIRS = _OPT + 112       # PE32+ data directory array
_RAW = 0x1000            # raw offset == RVA for the single section
_SECTION_SIZE = 0x1000

_IMPORT_DIR = 0x1000
_DELAY_DIR = 0x1200
_EXPORT_DIR = 0x1300
_EXPORT_FUNCS = 0x1330
_EXPORT_NAMES = 0x1360
_EXPORT_ORDS = 0x1390
_DLL_NAMES = 0x1400
_FN_ENTRIES = 0x1600
_THUNKS = 0x1800
_EXPORT_STRS = 0x1C00

_BLOB = 0x2000


def build_pe(imports=(), delay_imports=(), exports=(), functions=None) -> bytes:
    """A 64-bit PE image with the given imports (and their functions) + exports.

    `functions` maps a name from `imports` to the functions imported from it,
    which is what makes an export-coverage check testable.
    """
    data = bytearray(_BLOB)
    data[0:2] = b"MZ"
    struct.pack_into("<I", data, 0x3C, 0x40)

    data[0x40:0x44] = b"PE\x00\x00"
    # IMAGE_FILE_MACHINE_AMD64, 1 section, size of optional header 0xF0
    struct.pack_into("<HHIIIHH", data, 0x44, 0x8664, 1, 0, 0, 0, _OPT_SIZE, 0x2022)

    struct.pack_into("<H", data, _OPT, 0x20B)             # PE32+
    struct.pack_into("<Q", data, _OPT + 24, 0x140000000)  # ImageBase
    struct.pack_into("<I", data, _OPT + 108, 16)          # NumberOfRvaAndSizes
    struct.pack_into("<8sIIII", data, _SEC_TABLE,
                     b".rdata\x00\x00", _SECTION_SIZE, _RAW,
                     _SECTION_SIZE, _RAW)

    dll_cursor = [_DLL_NAMES]
    fn_cursor = [_FN_ENTRIES]
    thunk_cursor = [_THUNKS]
    str_cursor = [_EXPORT_STRS]

    def dll_name_rva(name: str) -> int:
        rva = dll_cursor[0]
        raw = name.encode("ascii") + b"\x00"
        data[rva:rva + len(raw)] = raw
        dll_cursor[0] = rva + len(raw) + 1
        return rva

    def fn_entry_rva(name: str) -> int:
        rva = fn_cursor[0]
        raw = struct.pack("<H", 0) + name.encode("ascii") + b"\x00"
        data[rva:rva + len(raw)] = raw
        fn_cursor[0] = rva + len(raw) + 1
        return rva

    funcs = {k.lower(): v for k, v in (functions or {}).items()}

    for i, dep in enumerate(imports):
        base = _IMPORT_DIR + i * 20
        rva = dll_name_rva(dep)
        thunk_start = thunk_cursor[0]
        thunk = thunk_start
        for fname in funcs.get(dep.lower(), []):
            struct.pack_into("<Q", data, thunk, fn_entry_rva(fname))
            thunk += 8
        struct.pack_into("<Q", data, thunk, 0)            # end of thunk array
        thunk_cursor[0] = thunk + 8
        struct.pack_into("<I", data, base + 0, thunk_start)    # OriginalFirstThunk
        struct.pack_into("<I", data, base + 12, rva)          # Name
        struct.pack_into("<I", data, base + 16, thunk_start)   # FirstThunk
    struct.pack_into("<II", data, _DIRS + 8, _IMPORT_DIR, (len(imports) + 1) * 20)

    for i, dep in enumerate(delay_imports):
        base = _DELAY_DIR + i * 32
        struct.pack_into("<I", data, base, 1)             # dlattrRva
        struct.pack_into("<I", data, base + 4, dll_name_rva(dep))
    if delay_imports:
        struct.pack_into("<II", data, _DIRS + 13 * 8, _DELAY_DIR,
                         (len(delay_imports) + 1) * 32)

    export_names = [e.lower() for e in exports]
    for i, name in enumerate(export_names):
        struct.pack_into("<I", data, _EXPORT_FUNCS + i * 4, _RAW)   # value unused
        rva = str_cursor[0]
        raw = name.encode("ascii") + b"\x00"
        data[rva:rva + len(raw)] = raw
        str_cursor[0] = rva + len(raw) + 1
        struct.pack_into("<I", data, _EXPORT_NAMES + i * 4, rva)
        struct.pack_into("<H", data, _EXPORT_ORDS + i * 2, i)
    if export_names:
        struct.pack_into("<IIHHIIIIIII", data, _EXPORT_DIR,
                         0, 0, 0, 0, _DLL_NAMES, 1, len(export_names),
                         len(export_names), _EXPORT_FUNCS, _EXPORT_NAMES,
                         _EXPORT_ORDS)
        struct.pack_into("<II", data, _DIRS + 0, _EXPORT_DIR, 40)

    return bytes(data)


def write_module(folder, name, imports=(), exports=(), functions=None) -> str:
    """Write one synthetic module (a game DLL, or a proxy with exports)."""
    path = os.path.join(str(folder), name)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "wb") as f:
        f.write(build_pe(imports, exports=exports, functions=functions))
    return path


def write_game(game_dir, exe_imports=(), modules=None, functions=None) -> None:
    """Drop a synthetic Farever.exe (plus optional same-folder DLLs) in place.

    `modules` maps a game-folder DLL name to the names it imports, which is how
    the real install reaches WINMM: Farever.exe -> libhl.dll -> WINMM.dll.
    `functions` maps a module name to {imported dll: [functions]}.
    """
    game_dir = str(game_dir)
    os.makedirs(game_dir, exist_ok=True)
    funcs = {k.lower(): v for k, v in (functions or {}).items()}
    with open(os.path.join(game_dir, "Farever.exe"), "wb") as f:
        f.write(build_pe(exe_imports, functions=funcs.get("farever.exe")))
    for name, dep_imports in (modules or {}).items():
        write_module(game_dir, name, imports=dep_imports,
                     functions=funcs.get(name.lower()))
