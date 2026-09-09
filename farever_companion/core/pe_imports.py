"""Which DLLs a Windows PE image imports (stdlib only, no Qt, no process access).

Why this exists
---------------
Option 1 works by dropping a proxy DLL next to Farever.exe. Windows loads a DLL
from the application directory when *some module in the process imports that
name*, so whether a proxy ever loads is a question about import tables - and it
is answerable statically, before launching the game.

That question has to be asked about **every module in the game folder**, not
just Farever.exe and what it imports. For Farever's install:

    Farever.exe   -> libhl.dll, KERNEL32, VCRUNTIME140, api-ms-win-crt-*
    directx.hdll  -> DINPUT8.dll                 (HashLink's DirectInput binding)
    uv.hdll       -> IPHLPAPI.dll, USERENV.dll   (libuv)
    SDL3.dll      -> version.dll
    sl.*.dll      -> version.dll                 (Streamline)
    nvngx_*.dll   -> version.dll                 (DLSS)

So dinput8.dll and the uv.hdll names load a moment after boot, on every start;
version.dll only loads *if* SDL3 / Streamline / DLSS gets loaded (GPU and
settings dependent, not guaranteed). Reading just the exe (or one hop) would
get version.dll wrong - which is exactly the kind of wrong answer that sends
someone down a dead end for an evening.

Loading is only half of it: *when* a proxy attaches decides whether it is safe.
winmm.dll used to be shipped because libhl.dll imports it during import
resolution (t=0, the boot closure is {Farever.exe, libhl.dll, vcruntime140.dll})
- and it killed the game there, so it was retired 2026-09-23 (see
dps_bridge/README.md). PROXY_RECOMMENDED is dinput8.dll, verified streaming.

The reader is deliberately defensive and cheap: any malformed structure returns
an empty set rather than guessing, and a file is read through bounded seeks
(headers plus the import directories) rather than loading multi-MB DLLs.
"""

from __future__ import annotations

import os
import struct
from functools import lru_cache
from typing import Callable, Iterable

#: Proxy names we SHIP, in the order the UI lists them: recommended first,
#: then the runtime-class backup. winmm.dll was removed 2026-09-23 (the only
#: t=0 name, it killed the game), iphlpapi.dll the same day (userenv.dll is the
#: verified uv.hdll alt; one alt is enough), and version.dll followed on
#: 2026-09-24: its source stays ready in GameFiles/Farever/hooks and
#: proxy_exports still regenerates its .def/forwarders, but the native build
#: no longer produces or ships version.dll. A future game update that makes
#: its importers reliable can bring it back without
#: new C work - but it is not packed into the app (FareverPal.spec) and not
#: offered in the picker, because its importers (SDL3/DLSS/Streamline) may
#: never load at all. See dps_bridge/README.md.
PROXY_DLL_NAMES: tuple[str, ...] = ("dinput8.dll", "userenv.dll")

#: Suffix of the diagnostic file each proxy checks for next to itself
#: (``PROXY_FLAG_NAME`` in the C sources, e.g. ``winmm_forward_only.flag``).
#: While that file exists the proxy forwards calls but runs no worker, no
#: sockets and no hooks - so the bridge is silently inert. It is a debugging
#: aid, and a leftover one would make every later install look dead, so the
#: game-folder cleanup removes it along with the DLL.
PROXY_FLAG_SUFFIX = "_forward_only.flag"

#: Optional one-integer file a proxy reads next to itself (`PROXY_GRACE_FILE`)
#: to override its boot-grace floor without a rebuild. The companion launches
#: the game through Steam, so an environment variable set in a shell never
#: reaches it - a file is the only usable bisect lever, and it is ours by name.
PROXY_GRACE_SUFFIX = "_grace_ms.txt"

#: Log each proxy writes next to itself while it runs (`PROXY_LOG_NAME` in the C
#: sources). It is the bridge's own account of why it is silent, and Live
#: Diagnostics quotes it - which is exactly why a leftover from another mode has
#: to go: it would explain the wrong session. Cleanup removes the logs of the
#: proxies that are not installed, and all of them when the proxies are removed.
PROXY_LOG_SUFFIX = "_proxy.log"

#: The GO handshake machinery (retired with winmm): the companion wrote
#: `<stem>_go.flag` next to a t=0 proxy when the player was located and its
#: bridge work could start. winmm was the only name that ever needed it - the
#: boot closure {Farever.exe, libhl.dll, vcruntime140.dll} makes every other
#: shipped name a runtime-class attach, the condition the working dinput8 build
#: proved safe. The helpers stay (inert: PROXY_GO_ENABLED is empty) so a future
#: t=0 name can be gated the same way without re-inventing the handshake.
PROXY_GO_SUFFIX = "_go.flag"

#: Which proxies actually read a GO flag. Empty since winmm was retired: no
#: shipped proxy loads at t=0 any more, so no proxy waits for a GO.
PROXY_GO_ENABLED: frozenset[str] = frozenset()


def _proxy_stem(proxy_name: str) -> str:
    return os.path.splitext(os.path.basename(proxy_name or ""))[0].lower()


def proxy_log_name(proxy_name: str) -> str:
    """Log file name for one proxy (winmm.dll -> winmm_proxy.log)."""
    return _proxy_stem(proxy_name) + PROXY_LOG_SUFFIX


#: Shared dev-opt-in flag file name. Every bridge binary (the three proxies
#: and farever_dps.dll) looks for this file next to itself; its first line is
#: the log path to write to. Written only when the app runs in developer mode
#: (run-log.bat) - normal users get no flag and therefore no log file anywhere.
PROXY_DEBUG_FLAG_NAME = "FAREVERMOD_DEBUG_LOG"


def proxy_debug_flag_name() -> str:
    """The one dev-only flag file every bridge binary honours."""
    return PROXY_DEBUG_FLAG_NAME


def proxy_flag_name(proxy_name: str) -> str:
    """Diagnostic flag file name for one proxy (winmm.dll -> winmm_forward_only.flag)."""
    return _proxy_stem(proxy_name) + PROXY_FLAG_SUFFIX


def proxy_grace_name(proxy_name: str) -> str:
    """Boot-grace override file for one proxy (winmm.dll -> winmm_grace_ms.txt)."""
    return _proxy_stem(proxy_name) + PROXY_GRACE_SUFFIX


def proxy_go_name(proxy_name: str) -> str:
    """GO-handshake file for one proxy (winmm.dll -> winmm_go.flag)."""
    return _proxy_stem(proxy_name) + PROXY_GO_SUFFIX

#: The proxy the UI recommends, and the one a fresh install starts on.
#:
#: dinput8.dll: loads on every start via directx.hdll and is verified
#: streaming in-game. A test guards this: the recommended name may not be one
#: whose PROXY_DLL_INFO text reports a known crash.
PROXY_RECOMMENDED = "dinput8.dll"

#: Why each shipped name does or does not load, in user-facing words:
#: ``name -> (when Windows loads it, whose import pulls it in)``. The names are
#: identical inside - *only* the importer differs - so a user picking
#: one is really choosing an importer, and the picker plus the option card read
#: the same strings so they cannot drift apart.
#: version.dll had an entry here until it stopped shipping (2026-09-24); its
#: hazard lives on in the README and in the C code that waits for a game update.
PROXY_DLL_INFO: dict[str, tuple[str, str]] = {
    "dinput8.dll": (
        "Loads every launch",
        "the game's DirectX binding imports it. Recommended — verified streaming "
        "combat events.",
    ),
    "userenv.dll": (
        "Loads every launch",
        "use when another mod already occupies dinput8.dll. The game's networking "
        "runtime imports it, so it loads just as reliably.",
    ),
}

#: The one line that makes a name a gamble rather than a choice, shown in the
#: warning colour right after its description. Kept out of PROXY_DLL_INFO so the
#: card can emphasise it without the plain-text copy carrying markup, and so no
#: caller has to unpack a wider tuple. Live Diagnostics reports the concrete
#: failure (a shadowing System32 copy, with its path) when it actually happens,
#: so the card only has to say the name may never load at all.
#: Empty since version.dll stopped shipping: every name we ship now loads on
#: every launch, so no shipped choice is a gamble. The machinery stays - the
#: next name that needs a caveat gets it by adding one entry here.
PROXY_DLL_CAVEATS: dict[str, str] = {}

_DOS_MAGIC = b"MZ"
_PE_MAGIC = b"PE\x00\x00"
_PE32 = 0x10B
_PE32P = 0x20B

_DIR_EXPORT = 0
_DIR_IMPORT = 1
_DIR_DELAY_IMPORT = 13

#: Extensions that can be loaded into the game process. HashLink's native
#: libraries are ``.hdll`` and they import the same system DLLs everyone else
#: does - ``directx.hdll`` imports dinput8.dll, ``uv.hdll`` pulls in userenv
#: (libuv), ``hlfmod.hdll`` pulls in FMOD. Skipping them hides real importers.
_MODULE_EXTS = (".dll", ".exe", ".hdll")

_HEADER_BYTES = 8192      # DOS + COFF + optional header + section table
_MAX_SECTIONS = 96
_MAX_DESCRIPTORS = 256
_MAX_NAME = 260
_MAX_DIR_BYTES = 64 * 1024

_Reader = Callable[[int, int], bytes]


def _u16(data: bytes, off: int) -> int:
    return struct.unpack_from("<H", data, off)[0]


def _u32(data: bytes, off: int) -> int:
    return struct.unpack_from("<I", data, off)[0]


def _u64(data: bytes, off: int) -> int:
    return struct.unpack_from("<Q", data, off)[0]


def _cstring(chunk: bytes, off: int, lower: bool = True) -> str:
    # `off` may be 0: the chunked reader seeks first and hands us a slice.
    if off < 0 or off >= len(chunk):
        return ""
    lo = off
    end = chunk.find(b"\x00", lo)
    if end < 0:
        end = len(chunk)
    text = chunk[lo:end].decode("ascii", errors="ignore").strip()
    return text.lower() if lower else text


def _fn_name(chunk: bytes, lower: bool = True) -> str:
    """IMAGE_IMPORT_BY_NAME = WORD hint + NUL-terminated name.

    `chunk` is already the slice at the entry (the reader seeks first), so no
    offset is involved - comparing a file offset against the chunk length here
    silently produced empty function lists, i.e. a false "no missing exports".

    `lower=False` keeps the case as written in the binary, which matters when
    *generating* an export list: PE export lookup is case-sensitive, so a
    generated ``timegettime`` would not satisfy an import of ``timeGetTime``.
    """
    return _cstring(chunk, 2, lower=lower)


def _read_import_functions(rva_to_off, read: _Reader,
                           first_thunk_rva: int, orig_thunk_rva: int,
                           lower: bool = True) -> set[str]:
    """Function names (or ``#ordinal``) named by one import descriptor."""
    thunk_rva = orig_thunk_rva or first_thunk_rva
    thunk_off = rva_to_off(thunk_rva) if thunk_rva else -1
    if thunk_off < 0:
        return set()
    size = 8                     # 64-bit images only (Farever is x64)
    raw = read(thunk_off, min(_MAX_DESCRIPTORS * size, _MAX_DIR_BYTES))
    out: set[str] = set()
    for i in range(len(raw) // size):
        entry = int.from_bytes(raw[i * size:(i + 1) * size], "little")
        if entry == 0:
            break
        if entry & (1 << 63):            # import by ordinal
            out.add(f"#{entry & 0xFFFF}")
            continue
        off = rva_to_off(entry & 0x7FFFFFFF)
        if off < 0:
            continue
        name = _fn_name(read(off, _MAX_NAME), lower=lower)
        if name:
            out.add(name)
    return out


def _parse(header: bytes, read: _Reader,
           lower: bool = True) -> dict[str, set[str]]:
    """{imported DLL: {function names}} from a PE header block + range reader.

    DLL names are always lowercased (every caller compares them that way);
    ``lower=False`` only preserves the case of the *function* names.
    """
    if len(header) < 0x40 or header[:2] != _DOS_MAGIC:
        return {}

    names: dict[str, set[str]] = {}
    try:
        e_lfanew = _u32(header, 0x3C)
        if e_lfanew <= 0 or e_lfanew + 24 > len(header):
            return {}
        if header[e_lfanew:e_lfanew + 4] != _PE_MAGIC:
            return {}

        coff = e_lfanew + 4
        n_sections = _u16(header, coff + 2)
        size_opt = _u16(header, coff + 16)
        opt = coff + 20
        if opt + size_opt > len(header) or n_sections > _MAX_SECTIONS:
            return {}

        magic = _u16(header, opt)
        if magic == _PE32P:
            image_base = _u64(header, opt + 24)
            n_dirs = _u32(header, opt + 108)
            dirs_off = opt + 112
        elif magic == _PE32:
            image_base = _u32(header, opt + 28)
            n_dirs = _u32(header, opt + 92)
            dirs_off = opt + 96
        else:
            return {}

        # Section table -> RVA translation
        sec_off = opt + size_opt
        sections: list[tuple[int, int, int]] = []
        for i in range(n_sections):
            base = sec_off + i * 40
            if base + 40 > len(header):
                break
            v_size = _u32(header, base + 8)
            v_addr = _u32(header, base + 12)
            raw_size = _u32(header, base + 16)
            raw_ptr = _u32(header, base + 20)
            sections.append((v_addr, max(v_size, raw_size), raw_ptr))

        def rva_to_off(rva: int) -> int:
            for v_addr, span, raw_ptr in sections:
                if v_addr <= rva < v_addr + span:
                    return raw_ptr + (rva - v_addr)
            return -1

        def read_dir(index: int) -> tuple[int, int]:
            if index >= n_dirs:
                return (0, 0)
            off = dirs_off + index * 8
            if off + 8 > len(header):
                return (0, 0)
            return (_u32(header, off), _u32(header, off + 4))

        def name_at(rva: int) -> str:
            off = rva_to_off(rva) if rva > 0 else -1
            if off < 0:
                return ""
            return _cstring(read(off, _MAX_NAME), 0)

        # --- Static imports: 20-byte IMAGE_IMPORT_DESCRIPTOR array ---
        imp_rva, imp_size = read_dir(_DIR_IMPORT)
        imp_off = rva_to_off(imp_rva) if imp_rva else -1
        if imp_off >= 0:
            want = min(imp_size or 0, _MAX_DIR_BYTES) or _MAX_DESCRIPTORS * 20
            block = read(imp_off, min(max(want, 20), _MAX_DIR_BYTES))
            for i in range(min(_MAX_DESCRIPTORS, len(block) // 20)):
                d = i * 20
                name_rva = _u32(block, d + 12)
                if name_rva == 0:
                    break
                nm = name_at(name_rva)
                if nm:
                    names.setdefault(nm, set()).update(
                        _read_import_functions(
                            rva_to_off, read,
                            _u32(block, d + 16), _u32(block, d),
                            lower=lower))

        # --- Delay imports: 32-byte ImgDelayDescr array ---
        del_rva, del_size = read_dir(_DIR_DELAY_IMPORT)
        del_off = rva_to_off(del_rva) if del_rva else -1
        if del_off >= 0:
            want = min(del_size or 0, _MAX_DIR_BYTES) or _MAX_DESCRIPTORS * 32
            block = read(del_off, min(max(want, 32), _MAX_DIR_BYTES))
            for i in range(min(_MAX_DESCRIPTORS, len(block) // 32)):
                d = i * 32
                attrs = _u32(block, d)
                name_field = _u32(block, d + 4)
                if attrs == 0 and name_field == 0:
                    break
                # dlattrRva (bit 0) clear => the field is a VA, not an RVA.
                name_rva = name_field if (attrs & 0x1) else name_field - image_base
                nm = name_at(name_rva)
                if nm:
                    names.setdefault(nm, set())
    except (struct.error, IndexError, ValueError):
        return {}

    return names


def imported_dll_names(data: bytes) -> frozenset[str]:
    """Lowercased DLL names referenced by an in-memory image's import tables.

    Reads both the static import directory and the delay-load directory (a
    proxy reached through a delay import still loads, so it still counts).
    Returns an empty set for anything that is not a readable PE image.
    """
    return frozenset(_parse(data[:_HEADER_BYTES], lambda off, n: data[off:off + n]))


@lru_cache(maxsize=128)
def _imports_cached(path: str, mtime_ns: int, size: int,
                    lower: bool = True) -> dict[str, frozenset[str]]:
    try:
        with open(path, "rb") as f:
            header = f.read(_HEADER_BYTES)

            def read(off: int, n: int) -> bytes:
                f.seek(off)
                return f.read(n)

            return {dll: frozenset(fns)
                    for dll, fns in _parse(header, read, lower=lower).items()}
    except OSError:
        return {}


@lru_cache(maxsize=128)
def _exports_cached(path: str, mtime_ns: int, size: int) -> frozenset[str]:
    try:
        with open(path, "rb") as f:
            header = f.read(_HEADER_BYTES)

            def read(off: int, n: int) -> bytes:
                f.seek(off)
                return f.read(n)

            return _parse_exports(header, read)
    except OSError:
        return frozenset()


def _parse_exports(header: bytes, read: _Reader) -> frozenset[str]:
    """Exported names of a PE image (IMAGE_EXPORT_DIRECTORY)."""
    if len(header) < 0x40 or header[:2] != _DOS_MAGIC:
        return frozenset()
    try:
        e_lfanew = _u32(header, 0x3C)
        if e_lfanew <= 0 or e_lfanew + 24 > len(header):
            return frozenset()
        if header[e_lfanew:e_lfanew + 4] != _PE_MAGIC:
            return frozenset()
        coff = e_lfanew + 4
        n_sections = _u16(header, coff + 2)
        size_opt = _u16(header, coff + 16)
        opt = coff + 20
        if opt + size_opt > len(header) or n_sections > _MAX_SECTIONS:
            return frozenset()
        magic = _u16(header, opt)
        if magic == _PE32P:
            n_dirs = _u32(header, opt + 108)
            dirs_off = opt + 112
        elif magic == _PE32:
            n_dirs = _u32(header, opt + 92)
            dirs_off = opt + 96
        else:
            return frozenset()
        if n_dirs <= _DIR_EXPORT:
            return frozenset()
        sec_off = opt + size_opt
        sections: list[tuple[int, int, int]] = []
        for i in range(n_sections):
            base = sec_off + i * 40
            if base + 40 > len(header):
                break
            sections.append((_u32(header, base + 12),
                             max(_u32(header, base + 8), _u32(header, base + 16)),
                             _u32(header, base + 20)))

        def rva_to_off(rva: int) -> int:
            for v_addr, span, raw_ptr in sections:
                if v_addr <= rva < v_addr + span:
                    return raw_ptr + (rva - v_addr)
            return -1

        exp_rva = _u32(header, dirs_off + _DIR_EXPORT * 8)
        exp_off = rva_to_off(exp_rva) if exp_rva else -1
        if exp_off < 0:
            return frozenset()
        d = read(exp_off, 40)
        if len(d) < 40:
            return frozenset()
        n_names = _u32(d, 24)
        names_rva = _u32(d, 32)
        if not n_names or not names_rva:
            return frozenset()
        names_off = rva_to_off(names_rva)
        if names_off < 0:
            return frozenset()
        count = min(n_names, _MAX_DESCRIPTORS)
        raw = read(names_off, count * 4)
        out: set[str] = set()
        for i in range(len(raw) // 4):
            nrva = _u32(raw, i * 4)
            off = rva_to_off(nrva) if nrva else -1
            if off < 0:
                continue
            nm = _cstring(read(off, _MAX_NAME), 0)
            if nm:
                out.add(nm)
        return frozenset(out)
    except (struct.error, IndexError, ValueError):
        return frozenset()


def pe_import_functions(path: str,
                        preserve_case: bool = False) -> dict[str, frozenset[str]]:
    """{imported DLL: {imported function names}} for one module.

    Names come back lowercased by default, which is what every comparison in
    this module wants. Pass ``preserve_case=True`` when the names are going to
    be *written down* (generating a proxy's export list): PE export lookup is
    case-sensitive, so ``timegettime`` would not satisfy an import of
    ``timeGetTime``.
    """
    if not path or not os.path.isfile(path):
        return {}
    try:
        st = os.stat(path)
    except OSError:
        return {}
    return dict(_imports_cached(path, st.st_mtime_ns, st.st_size,
                                not preserve_case))


def exported_names(path: str) -> frozenset[str]:
    """Lowercased names a DLL exports, cached until the file changes."""
    if not path or not os.path.isfile(path):
        return frozenset()
    try:
        st = os.stat(path)
    except OSError:
        return frozenset()
    return _exports_cached(path, st.st_mtime_ns, st.st_size)


def pe_imports(path: str) -> frozenset[str]:
    """Import names for one file, cached until the file changes."""
    if not path or not os.path.isfile(path):
        return frozenset()
    try:
        st = os.stat(path)
    except OSError:
        return frozenset()
    try:
        return frozenset(_imports_cached(path, st.st_mtime_ns, st.st_size))
    except TypeError:  # pragma: no cover - unexpected stat shape
        return frozenset()


def clear_cache() -> None:
    """Drop the per-file parse caches (tests / after an install)."""
    _imports_cached.cache_clear()
    _exports_cached.cache_clear()


def find_game_exe(game_dir: str) -> str:
    """Locate Farever.exe in a game folder, case-insensitively."""
    if not game_dir or not os.path.isdir(game_dir):
        return ""
    try:
        for entry in os.listdir(game_dir):
            if entry.lower() == "farever.exe":
                return os.path.join(game_dir, entry)
    except OSError:
        return ""
    return ""


def game_modules(game_dir: str) -> list[str]:
    """Every loadable module shipped in the game folder (top level only).

    Top level is the whole story for proxy loading: Windows resolves these
    names from the application directory first, and nothing in a subfolder can
    pull a proxy in from there.
    """
    if not game_dir or not os.path.isdir(game_dir):
        return []
    try:
        entries: Iterable[str] = os.listdir(game_dir)
    except OSError:
        return []
    out = []
    for entry in sorted(entries):
        if entry.lower().endswith(_MODULE_EXTS):
            out.append(os.path.join(game_dir, entry))
    return out


def game_imports(game_dir: str) -> frozenset[str]:
    """Every DLL name imported by any module in the game folder."""
    names: set[str] = set()
    for path in game_modules(game_dir):
        names |= pe_imports(path)
    return frozenset(names)


def proxy_import_map(game_dir: str) -> dict[str, list[str]]:
    """{proxy name: [modules in the game folder that import it]}, for each proxy.

    Proxies nothing imports are present as an empty list, so a caller can tell
    "provably unloadable" from "we could not read the folder".
    """
    out: dict[str, list[str]] = {name: [] for name in PROXY_DLL_NAMES}
    for path in game_modules(game_dir):
        imports = pe_imports(path)
        if not imports:
            continue
        base = os.path.basename(path).lower()
        for name in PROXY_DLL_NAMES:
            if name in imports:
                out[name].append(base)
    return out


def headers_readable(game_dir: str) -> bool:
    """Could we actually parse at least one module's import table here?

    proxy_import_map is an all-empty dict both when nothing imports a proxy
    (a real, provable fact) and when every header failed to parse (a stub
    exe, a permission problem) - the caller must not accuse the user's
    install on the strength of the latter, so this separates the two.
    """
    for path in game_modules(game_dir):
        if pe_imports(path):
            return True
    exe = find_game_exe(game_dir)
    return bool(exe and pe_imports(exe))


def loadable_proxy_names(game_dir: str) -> frozenset[str]:
    """Which shipped proxy DLLs this install of the game can load."""
    return frozenset(n for n, mods in proxy_import_map(game_dir).items() if mods)


def _module_paths(game_dir: str) -> dict[str, str]:
    """{lowercased basename: path} for everything shipped in the game folder."""
    out: dict[str, str] = {}
    exe = find_game_exe(game_dir)
    if exe:
        out[os.path.basename(exe).lower()] = exe
    for path in game_modules(game_dir):
        out.setdefault(os.path.basename(path).lower(), path)
    return out


def boot_import_closure(game_dir: str) -> frozenset[str]:
    """Module names the game maps before any of its own code runs.

    Walks the exe's static imports transitively, following only modules that
    actually sit in the game folder. Every one of those loads during process
    start, so a proxy name imported by one of them is claimed before the game
    can load anything else. For Farever the closure is exactly
    {farever.exe, libhl.dll, vcruntime140.dll} - which is why winmm.dll (via
    libhl) was the only t=0 name this game ever had, and why it was retired:
    a t=0 attach is the one position this game dies in.
    """
    paths = _module_paths(game_dir)
    start = os.path.basename(find_game_exe(game_dir) or "").lower()
    seen: set[str] = set()
    stack = [start] if start else []
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        path = paths.get(current)
        if not path:
            continue
        seen.add(current)
        try:
            for name in pe_imports(path):
                low = name.lower()
                if low in paths and low not in seen:
                    stack.append(low)
        except Exception:
            continue
    return frozenset(seen)


def proxy_load_stage(game_dir: str) -> dict[str, str]:
    """{proxy: "boot"|"runtime"|"optional"|"none"|"unknown"} - when it can load.

    ``boot``     a module the exe statically imports pulls the name in at
                 process start, so nothing else can have claimed it first.
                 No shipped proxy is this class since winmm retired - it was
                 the only t=0 name, and a t=0 attach kills this game.
    ``runtime``  a HashLink native library the game itself ships (an ``.hdll``)
                 imports it directly, so the game's runtime loads it a moment
                 after boot - every launch, just later. ``directx.hdll`` ->
                 ``dinput8.dll`` is this case, and the live module dumps confirm
                 the game always maps dinput8.dll.
    ``optional`` only plain DLLs import it (SDL3, DLSS, Streamline). Those are
                 components the game may never load: sdl3.dll was absent from
                 every live session dumped. Our file then also races whatever
                 already holds the name, and an in-memory System32 copy wins -
                 the loader reuses a loaded module by name and never searches
                 the game folder, so a restart cannot change the outcome.
    ``none``     nothing in the folder imports it at all.
    """
    try:
        importers = proxy_import_map(game_dir)
        closure = boot_import_closure(game_dir)
    except Exception:
        return {name: "unknown" for name in PROXY_DLL_NAMES}
    stage: dict[str, str] = {}
    for name in PROXY_DLL_NAMES:
        mods = importers.get(name) or []
        if any(m in closure for m in mods):
            stage[name] = "boot"
        elif any(m.endswith(".hdll") for m in mods):
            stage[name] = "runtime"
        elif mods:
            stage[name] = "optional"
        else:
            stage[name] = "none"
    return stage


def proxy_export_gaps(game_dir: str, bridge_dir: str) -> dict[str, list[str]]:
    """{proxy: [functions the game imports from it that it does NOT export]}.

    A proxy with a gap is a crash, not a cosmetic defect: Windows fails the
    whole import on the first missing name, and the game dies inside whichever
    module asked for it. That is exactly what happened to fmod.dll, which
    imports WINMM.timeGetTime while our winmm proxy exported only the two
    timer functions libhl needs - the game then died at
    fmod.$Api.set_DEBUG_FLAGS with "Primitive or library is missing".
    """
    needed: dict[str, set[str]] = {n: set() for n in PROXY_DLL_NAMES}
    for path in game_modules(game_dir):
        for dll, fns in pe_import_functions(path).items():
            target = needed.get(dll)
            if target is None:
                continue
            # Ordinal imports cannot be matched by name; silently skipping them
            # is right - a false "gap" here would be worse than no check.
            target.update(f for f in fns if not f.startswith("#"))

    gaps: dict[str, list[str]] = {}
    for name in PROXY_DLL_NAMES:
        proxy = os.path.join(bridge_dir, name) if bridge_dir else ""
        if not os.path.isfile(proxy):
            continue
        missing = sorted(needed[name] - exported_names(proxy))
        if missing:
            gaps[name] = missing
    return gaps
