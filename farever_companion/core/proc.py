"""Read-only process access over the Rust reader (preferred) or pymem (fallback).

The Rust backend adds batched reads and a fast scan that the player locator
needs; pymem covers basic reads only. The handle is opened without write rights
and no backend exposes a write primitive.
"""
from __future__ import annotations

import os
import struct
import time

from .pe_imports import PROXY_DLL_NAMES

try:
    import farever_native as _native
except ImportError:  # pragma: no cover - extension optional
    _native = None

try:
    import pymem as _pymem
except ImportError:  # pragma: no cover
    _pymem = None

PROCESS_NAME = "Farever.exe"


class ProcError(RuntimeError):
    pass


def backend_name() -> str:
    if _native is not None:
        return "native"
    if _pymem is not None:
        return "pymem"
    return "none"


def find_pid(name: str = PROCESS_NAME) -> int | None:
    """First PID whose image name == `name` (case-insensitive), or None.

    Read-only: walks the Win32 Toolhelp process snapshot via ctypes and opens no
    process handle, so it's cheap and safe to poll every couple of seconds. No
    new dependency (the app is Windows-only). Returns None on any failure or if
    the process isn't running.
    """
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:  # pragma: no cover - non-Windows / no ctypes
        return None

    TH32CS_SNAPPROCESS = 0x2

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
                    ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long),
                    ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260)]

    try:
        k32 = ctypes.windll.kernel32
    except Exception:  # pragma: no cover - non-Windows
        return None
    INVALID = ctypes.c_void_p(-1).value
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap in (0, None, -1, INVALID):
        return None
    try:
        e = PROCESSENTRY32W()
        e.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = k32.Process32FirstW(snap, ctypes.byref(e))
        target = name.lower()
        while ok:
            if e.szExeFile.lower() == target:
                return int(e.th32ProcessID)
            ok = k32.Process32NextW(snap, ctypes.byref(e))
        return None
    finally:
        k32.CloseHandle(snap)


# Per-PID Toolhelp32 module snapshots, cached for _MODULE_SNAPSHOT_TTL
# seconds. The snapshot is the dominant cost of detect_combat_bridge on a
# real game process (~4-10 ms at 200-400 loaded modules vs. ~0.3 ms on a
# small process), so repeated 1-2 Hz polls from the settings diagnostics,
# the damage source manager and the attach checks share ONE snapshot per
# window instead of re-enumerating every call. Module state only changes on
# attach / inject / uninject, which invalidate explicitly (or pass fresh=True
# in the uninject wait loop, which must see the unmap land immediately).
_MODULE_SNAPSHOT_CACHE: dict[int, tuple[float, list[dict]]] = {}
_MODULE_SNAPSHOT_TTL = 2.0


def invalidate_module_cache(pid: int | None = None) -> None:
    """Drop cached module snapshots for `pid` (all PIDs when None).

    Call after attach / inject / uninject so the next detect_combat_bridge
    sees the new module state immediately instead of within the TTL window.
    """
    if pid is None:
        _MODULE_SNAPSHOT_CACHE.clear()
    else:
        _MODULE_SNAPSHOT_CACHE.pop(pid, None)


def get_loaded_modules(pid: int, fresh: bool = False) -> list[dict]:
    """Return a list of loaded modules for pid with keys: 'name', 'path', 'base', 'size'.

    Cached per-PID for _MODULE_SNAPSHOT_TTL seconds; pass fresh=True when the
    module set may have just changed (attach / inject / uninject) to bypass
    the cache and re-snapshot immediately. Failed snapshots are never cached,
    so a transient error retries on the next call.
    """
    if not pid:
        return []
    import time as _t
    now = _t.monotonic()
    cached = _MODULE_SNAPSHOT_CACHE.get(pid)
    if not fresh and cached is not None and (now - cached[0]) < _MODULE_SNAPSHOT_TTL:
        return cached[1]
    mods = _snapshot_modules(pid)
    if mods:
        _MODULE_SNAPSHOT_CACHE[pid] = (now, mods)
    return mods


def _snapshot_modules(pid: int) -> list[dict]:
    """Raw Toolhelp32 module enumeration for `pid` (the expensive part)."""
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.windll.kernel32
    except Exception:
        return []

    TH32CS_SNAPMODULE = 0x00000008
    TH32CS_SNAPMODULE32 = 0x00000010

    class MODULEENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("th32ModuleID", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("GlblcntUsage", wintypes.DWORD),
            ("ProccntUsage", wintypes.DWORD),
            ("modBaseAddr", ctypes.c_void_p),
            ("modBaseSize", wintypes.DWORD),
            ("hModule", wintypes.HMODULE),
            ("szModule", ctypes.c_wchar * 256),
            ("szExePath", ctypes.c_wchar * 260),
        ]

    INVALID = ctypes.c_void_p(-1).value
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, pid)
    if snap in (0, None, -1, INVALID):
        return []
    res = []
    try:
        e = MODULEENTRY32W()
        e.dwSize = ctypes.sizeof(MODULEENTRY32W)
        ok = k32.Module32FirstW(snap, ctypes.byref(e))
        while ok:
            res.append({
                "name": str(e.szModule),
                "path": str(e.szExePath),
                "base": e.modBaseAddr,
                "size": int(e.modBaseSize),
            })
            ok = k32.Module32NextW(snap, ctypes.byref(e))
        return res
    finally:
        k32.CloseHandle(snap)


def is_module_loaded(pid: int, mod_name: str = "farever_dps.dll",
                     fresh: bool = False) -> bool:
    """True if a module matching `mod_name` is currently loaded in `pid`.

    fresh=True bypasses the snapshot cache — used by the uninject wait loop,
    which polls until the DLL actually unmaps.
    """
    mods = get_loaded_modules(pid, fresh=fresh)
    target = (mod_name or "").lower()
    return any(m["name"].lower() == target for m in mods)


def _remote_call(pid: int, proc_addr: int, arg: int, timeout_ms: int = 4000) -> int:
    """Run one stdcall(ptr) function inside `pid` via a remote thread.

    Returns the thread exit code (the callee's return value for MinHook's
    MH_STATUS functions, 0 == MH_OK) or -1 when the call could not be made.
    """
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.windll.kernel32
        h_proc = k32.OpenProcess(0x1F0FFF, False, pid)
        if not h_proc:
            return -1
        try:
            h_thread = k32.CreateRemoteThread(
                h_proc, None, 0, ctypes.c_void_p(proc_addr),
                ctypes.c_void_p(arg), 0, None)
            if not h_thread:
                return -1
            try:
                k32.WaitForSingleObject(h_thread, timeout_ms)
                code = wintypes.DWORD()
                if not k32.GetExitCodeThread(h_thread, ctypes.byref(code)):
                    return -1
                return code.value
            finally:
                k32.CloseHandle(h_thread)
        finally:
            k32.CloseHandle(h_proc)
    except Exception:
        return -1


def _read_remote(h_proc: int, addr: int, n: int) -> bytes | None:
    """ReadProcessMemory wrapper returning None instead of raising."""
    import ctypes
    k32 = ctypes.windll.kernel32
    buf = ctypes.create_string_buffer(n)
    got = ctypes.c_size_t(0)
    if not k32.ReadProcessMemory(h_proc, ctypes.c_void_p(addr), buf, n,
                                 ctypes.byref(got)):
        return None
    return buf.raw[:got.value]


def _pe_export_addr(img: bytes, image_base: int, name: str,
                    read_more=None) -> int | None:
    """Resolve one export's absolute address from an IN-MEMORY PE image.

    `img` holds the bytes read from `image_base` (at least the header page).
    For a LOADED module the buffer offset of any RVA equals the RVA itself
    (the loader maps sections at their RVAs), so no section-table translation
    is applied — only the tables beyond `img` go through `read_more(rva, n)`.
    Walks the export directory properly (name -> name-ordinal index ->
    function RVA). Pure bytes-in/address-out: unit-testable without Windows.
    """
    import struct as _struct
    if len(img) < 0x40 or img[:2] != b"MZ":
        return None
    e_lfanew = _struct.unpack_from("<I", img, 0x3C)[0]
    if e_lfanew <= 0 or e_lfanew + 24 + 96 > len(img):
        return None
    opt_size = _struct.unpack_from("<H", img, e_lfanew + 20)[0]
    if e_lfanew + 24 + opt_size > len(img):
        return None
    opt = img[e_lfanew + 24:e_lfanew + 24 + opt_size]
    magic = _struct.unpack_from("<H", opt)[0]
    exp_rva = _struct.unpack_from("<I", opt, 112 if magic == 0x20B else 96)[0]
    if not exp_rva:
        return None

    def _read(rva: int, n: int) -> bytes | None:
        # Strict-length read. A remote ReadProcessMemory that straddles a page
        # boundary (or the tail of the mapped image) can return PARTIAL bytes;
        # unpacking those raised "unpack requires a buffer of 4 bytes" and
        # aborted the whole uninject (seen live 2026-09-19). Accumulate until
        # the full width is in hand or the source says stop.
        if 0 <= rva and rva + n <= len(img):
            return img[rva:rva + n]
        if read_more is None:
            return None
        out = bytearray()
        while len(out) < n:
            c = read_more(rva + len(out), max(n - len(out), 64))
            if not c:
                return None
            out += c
        return bytes(out[:n])

    hdr = _read(exp_rva, 40)
    if not hdr or len(hdr) < 40:
        return None
    # IMAGE_EXPORT_DIRECTORY: NumberOfNames @ +24, AddressOfFunctions @ +28,
    # AddressOfNames @ +32, AddressOfNameOrdinals @ +36.
    nnames = _struct.unpack_from("<I", hdr, 24)[0]
    names_rva = _struct.unpack_from("<I", hdr, 32)[0]
    funcs_rva = _struct.unpack_from("<I", hdr, 28)[0]
    ords_rva = _struct.unpack_from("<I", hdr, 36)[0]
    if nnames <= 0 or nnames > 0x10000:
        return None

    want = name.encode("ascii")
    for i in range(nnames):
        ent = _read(names_rva + i * 4, 4)
        if not ent or len(ent) < 4:
            continue   # unreadable entry — skip, don't abort the walk
        nrva = _struct.unpack("<I", ent)[0]
        raw = b""
        chunk_rva = nrva
        while len(raw) < 128:
            c = _read(chunk_rva, 64)
            if not c:
                break
            raw += c
            if b"\x00" in c:
                break
            chunk_rva += 64
        if raw.split(b"\x00", 1)[0] == want:
            # Name index -> function index via the name-ORDINALS array
            # (WORD per name) — never assume the arrays line up 1:1.
            oe = _read(ords_rva + i * 2, 2)
            if not oe or len(oe) < 2:
                continue   # unreadable entry — skip, don't abort the walk
            fidx = _struct.unpack("<H", oe)[0]
            fe = _read(funcs_rva + fidx * 4, 4)
            if not fe or len(fe) < 4:
                continue   # unreadable entry — skip, don't abort the walk
            frva = _struct.unpack("<I", fe)[0]
            if frva == 0:
                return None   # forwarder / unresolved export
            return image_base + frva
    return None


def _target_export_addr(h_proc: int, base: int, name: str) -> int | None:
    """Resolve export `name` of the module mapped at `base` inside `h_proc`."""
    img = _read_remote(h_proc, base, 0x1000)
    if not img:
        return None

    def read_more(rva: int, n: int) -> bytes | None:
        return _read_remote(h_proc, base + rva, max(n, 64))

    return _pe_export_addr(img, base, name, read_more)


# Exit codes of the bridge DLL's SafeUnload export
# (GameFiles/Farever/hooks/hook/farever_dps/safe_unload.c),
# returned as the remote thread's exit code.
SU_OK = 0        # fully unloaded (module unmapped)
SU_BUSY = 1001   # another SafeUnload in flight — retry shortly
SU_MH_FAIL = 1002  # MH_Uninitialize failed inside the DLL — do not unmap
SU_WORKER = 1003   # heartbeat thread did not exit in time — retry later
SU_BOOTING = 1004  # bridge boot not finished — retry later


def _safe_unload_via_export(call_su, call_ready, is_loaded,
                            sleep=None, attempts: int = 4) -> tuple[bool, str]:
    """Drive the bridge DLL's in-process SafeUnload export to completion.

    The DLL tears itself down on its OWN thread (stop worker -> disable hooks
    -> grace -> MH_Uninitialize -> FreeLibraryAndExitThread), so this only
    has to invoke it and interpret the status. The unmap lands asynchronously
    after SU_OK, hence the short polling loop.

    Injectable seams for tests: `sleep(s)`, `call_su(flags) -> SU_* code`,
    `call_ready() -> 1 when IsSafeUnloadReady says inert (None/-1 otherwise)`,
    `is_loaded() -> bool` (module still mapped?).
    """
    import time as _t
    doze = sleep or _t.sleep
    for _ in range(max(1, attempts)):
        code = call_su(0)
        if code == SU_OK:
            # FreeLibraryAndExitThread is asynchronous: give the unmap ~3 s
            # to land before consulting the inert-state query export.
            for _ in range(30):
                if not is_loaded():
                    return True, "farever_dps.dll fully unloaded from the game"
                doze(0.1)
            if call_ready() == 1:
                return True, ("combat bridge hooks removed — the DLL stays "
                              "resident but inert and clears on the next "
                              "game restart")
            return True, "combat bridge SafeUnload finished"
        if code == SU_BUSY:
            doze(0.5)
            continue
        if code == SU_BOOTING:
            doze(1.0)
            continue
        if code == SU_WORKER:
            # The bridge worker may still be unwinding its final UDP send when
            # the first unload request arrives. Keep the module mapped and
            # retry the safe export; a remote FreeLibrary here would be unsafe.
            doze(0.75)
            continue
        if code == SU_MH_FAIL:
            return False, "bridge failed to free its hooks — restart the game to remove it"
        if code == -1:
            return False, "could not invoke the bridge's SafeUnload export"
        return False, f"SafeUnload failed (status {code})"
    return False, "SafeUnload stayed busy — retry in a moment"


def safe_uninject_hook(pid: int, mod_name: str = "farever_dps.dll") -> tuple[bool, str]:
    """Strip the bridge's hooks from `pid` WITHOUT crashing the game.

    Two tiers, best first:

      1. SafeUnload export (DLLs built with safe_unload.c, which lives in
         GameFiles/Farever/hooks/hook/farever_dps/): the
         DLL tears ITSELF down on its own thread — stops + joins the
         heartbeat worker, disables all hooks, grace-pauses, runs
         MH_Uninitialize, then FreeLibraryAndExitThread for a real unmap.
         Fully removes the DLL from the running game; no restart needed.

      2. Legacy fallback (old DLL / proxy mode): MH_DisableHook(MH_ALL_HOOKS)
         -> grace sleep -> MH_Uninitialize via remote calls, and NO unmap.
         A remote unmap while a game thread may still sit inside a trampoline
         (or while the worker thread is alive) is the crash that motivated
         all of this; the inert DLL clears on the next game restart.

    Returns (ok, "human reason"); refuses when nothing safe is possible.
    """
    if not pid:
        return False, "no game process"
    try:
        import ctypes
        import time
        k32 = ctypes.windll.kernel32
        mods = get_loaded_modules(pid)
        target = (mod_name or "").lower()
        matching = [m for m in mods if m["name"].lower() == target]
        if not matching:
            return True, "bridge already not loaded"

        h_proc = k32.OpenProcess(0x1F0FFF, False, pid)
        if not h_proc:
            return False, "cannot open game process"
        try:
            base = matching[0].get("base")
            if not base:
                return False, "bridge module base not found"

            # Tier 1: the DLL unloads itself on its own thread when the
            # rebuild with safe_unload.c ships the export.
            p_su = _target_export_addr(h_proc, base, "SafeUnload")
            if p_su:
                def call_su(flags: int) -> int:
                    return _remote_call(pid, p_su, flags)

                def call_ready() -> int | None:
                    p_ready = _target_export_addr(h_proc, base,
                                                  "IsSafeUnloadReady")
                    if not p_ready:
                        return None
                    return _remote_call(pid, p_ready, 0)

                # fresh=True: the cache would hide the unmap for up to
                # _MODULE_SNAPSHOT_TTL seconds, stalling this 0.1 s wait loop.
                return _safe_unload_via_export(
                    call_su, call_ready,
                    lambda: is_module_loaded(pid, target, fresh=True))

            # Tier 2 (legacy, no SafeUnload export): disable-only.
            MH_ALL_HOOKS = ctypes.c_void_p(-1).value

            p_disable = _target_export_addr(h_proc, base, "MH_DisableHook")
            p_uninit = _target_export_addr(h_proc, base, "MH_Uninitialize")
            if not p_disable or not p_uninit:
                return False, ("bridge DLL does not export MinHook cleanup "
                               "(proxy mode): restart the game to remove it")

            code = _remote_call(pid, p_disable, MH_ALL_HOOKS)
            if code != 0:   # MH_OK == 0
                return False, f"MH_DisableHook(MH_ALL_HOOKS) failed (status {code})"
            # Grace: after the disable, no NEW call enters a trampoline; any
            # thread already inside one finishes in microseconds. A short
            # pause makes the uninitialize below free only unused memory.
            time.sleep(0.75)
            code = _remote_call(pid, p_uninit, 0)
            if code != 0:   # MH_OK == 0
                return False, f"MH_Uninitialize() failed (status {code})"
            return True, ("combat bridge unhooked — hooks removed safely; "
                          "the inert DLL clears on the next game restart")
        finally:
            k32.CloseHandle(h_proc)
    except Exception as e:
        return False, f"uninject error: {e}"



_PROXY_VERIFY_CACHE: dict[tuple[str, float, int], bool] = {}


def get_bridge_dir() -> str:
    """Directory where reference combat bridge DLLs and helper tools reside.

    Supports running from source (run.bat/python) and frozen standalone executables
    (PyInstaller / Nuitka / cx_Freeze .exe).
    """
    import sys
    from pathlib import Path

    candidates: list[Path] = []

    # 1. PyInstaller frozen _MEIPASS bundle directory
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        candidates.append(Path(sys._MEIPASS) / "dps_bridge")
        candidates.append(Path(sys._MEIPASS))

    # 2. Directory where the compiled .exe resides
    if getattr(sys, "frozen", False) and getattr(sys, "executable", None):
        try:
            exe_dir = Path(sys.executable).resolve().parent
            candidates.append(exe_dir / "dps_bridge")
            candidates.append(exe_dir)
        except Exception:
            pass

    # 3. Source code path relative to proc.py (__file__)
    try:
        source_root = Path(__file__).resolve().parents[2]
        candidates.append(source_root / "dps_bridge")
    except Exception:
        pass

    # 4. Current working directory
    try:
        candidates.append(Path.cwd() / "dps_bridge")
        candidates.append(Path.cwd())
    except Exception:
        pass

    # Find the first candidate directory that contains any proxy or bridge DLL
    for cand in candidates:
        if cand.is_dir():
            for dll_name in ("dinput8.dll", "userenv.dll",
                             "version.dll", "farever_dps.dll"):
                if (cand / dll_name).is_file():
                    return str(cand)

    # Fallback to the first existing candidate folder if no DLLs found yet
    for cand in candidates:
        if cand.is_dir():
            return str(cand)

    return ""


_REF_CHECKSUM_CACHE: dict[tuple, dict[str, str]] = {}


def get_reference_checksums() -> dict[str, str]:
    """SHA256 hashes of the reference combat bridge DLLs in dps_bridge/.

    The reference DLLs themselves are the ground truth — the hashes are
    derived from them at runtime, so they can never drift out of sync (no
    checksums.json manifest to maintain). Cached keyed by file mtime/size so
    the 1-second DPS diagnostics poll doesn't re-hash them every tick.
    Returns {} when the bridge folder or all DLLs are missing — callers then
    fall back to the embedded-signature check.
    """
    import hashlib
    b_dir = get_bridge_dir()
    if not b_dir:
        return {}

    names = ("dinput8.dll", "userenv.dll", "version.dll",
             "farever_dps.dll")
    key_parts: list = [b_dir]
    for dll_name in names:
        try:
            st = os.stat(os.path.join(b_dir, dll_name))
            key_parts.append((dll_name, st.st_mtime, st.st_size))
        except OSError:
            key_parts.append((dll_name, None))
    cache_key = tuple(key_parts)
    cached = _REF_CHECKSUM_CACHE.get(cache_key)
    if cached is not None:
        return cached

    checksums = {}
    for dll_name in names:
        ref_path = os.path.join(b_dir, dll_name)
        try:
            with open(ref_path, "rb") as rf:
                checksums[dll_name.lower()] = hashlib.sha256(rf.read()).hexdigest().lower()
        except Exception:
            pass

    if checksums:
        _REF_CHECKSUM_CACHE[cache_key] = checksums
    return checksums


def invalidate_proxy_cache() -> None:
    """Clear the proxy verification cache."""
    _PROXY_VERIFY_CACHE.clear()


_FAREVERMOD_PROXY_MARKER = b"FAREVERMOD_PROXY_ID=FareverMod;"


def get_farevermod_proxy_identity(file_path: str) -> dict[str, str]:
    """Read the stable identity embedded in a FareverMod proxy DLL."""
    if not file_path or not os.path.isfile(file_path):
        return {}
    try:
        with open(file_path, "rb") as f:
            content = f.read()
    except OSError:
        return {}
    pos = content.find(_FAREVERMOD_PROXY_MARKER)
    if pos < 0:
        return {}
    end = content.find(b"\x00", pos)
    raw = content[pos:end if end >= 0 else pos + 512].decode(
        "ascii", errors="ignore")
    result: dict[str, str] = {}
    for part in raw.split(";"):
        if "=" in part:
            key, value = part.split("=", 1)
            result[key] = value
    return result


def is_farever_proxy(file_path: str) -> bool:
    """Verify whether a given DLL file is FareverPal's combat hook proxy.

    1. When reference checksums are available (hashes of the reference DLLs in
       dps_bridge/), they are authoritative: a SHA256 match means FareverPal's
       proxy, any mismatch means a 3rd-party DLL (trainer, reshade, minimap
       dinput8.dll, ...).
    2. The embedded telemetry signature (127.0.0.1:49152 or farever_dps + FareverPal)
       is only consulted when no reference DLLs are reachable at all (e.g. an
       older frozen build that shipped without them) - other mods can embed the
       same localhost/UDP string, so a signature must never override a hash
       mismatch.
    Works dynamically for any user, any install path, and any DLL size.
    """
    if not file_path or not os.path.isfile(file_path):
        return False

    try:
        st = os.stat(file_path)
        cache_key = (os.path.normcase(os.path.abspath(file_path)), st.st_mtime, st.st_size)
        if cache_key in _PROXY_VERIFY_CACHE:
            return _PROXY_VERIFY_CACHE[cache_key]
    except Exception:
        return False

    def _eval() -> bool:
        import hashlib
        try:
            with open(file_path, "rb") as f:
                content = f.read()
        except Exception:
            return False

        # A marked FareverMod rebuild remains ours even when its SHA-256
        # differs from the bundled reference binary.
        if _FAREVERMOD_PROXY_MARKER in content:
            return True

        file_hash = hashlib.sha256(content).hexdigest().lower()

        # 1. Reference checksums (hashes of the reference DLLs in dps_bridge/)
        #    are authoritative: hash match = FareverPal proxy, mismatch = 3rd-party
        #    DLL. No signature fallback while they are available - other mods can
        #    embed the same localhost/UDP strings and would otherwise be misidentified.
        ref_checksums = get_reference_checksums()
        if ref_checksums:
            return file_hash in ref_checksums.values()

        # 2. Signature fallback (only when no reference DLLs are reachable, e.g.
        #    an old frozen build without a bundled dps_bridge): the proxy embeds
        #    its local UDP telemetry destination.
        if b"127.0.0.1:49152" in content or (b"farever_dps" in content and b"FareverPal" in content):
            return True

        return False

    verdict = _eval()
    _PROXY_VERIFY_CACHE[cache_key] = verdict
    return verdict


def detect_combat_bridge(pid: int = 0, game_dir: str = "") -> dict:
    """Analyze loaded modules in game process or check game directory on disk.

    Works whether the game is currently running or completely closed.
    Returns:
      {
        'detected': bool,
        'dll_name': str,
        'dll_path': str,
        'option': int,              # 1 for Proxy, 2 for Injector, 0 for none
        'game_dir': str,
        'game_exe': str,
        'proxy_in_game_dir': bool,
        'status_summary': str,
        'third_party_dinput8': bool,
        'third_party_dinput8_path': str,
        'third_party_version': bool,
        'third_party_version_path': str,
        'third_party_version': bool,
        'third_party_version_path': str,
        'is_farever_dll': bool,
        'game_loadable_proxies': list[str],   # proxies the game really imports
        'proxy_names': list[str],             # Farever proxies found on disk
        'proxy_loadable': bool | None,        # None = game headers unreadable
        'proxy_importers': dict[str, list[str]],   # proxy -> importers in the game folder
        'proxy_load_stage': dict[str, str],   # proxy -> boot | runtime | none
        'proxy_shadowed_by': str,             # loaded copy of that name that is not ours
      }
    """
    modules = get_loaded_modules(pid) if pid else []
    game_exe = ""
    if modules:
        game_exe = modules[0]["path"]
        if not game_dir:
            game_dir = os.path.dirname(game_exe)

    if not game_dir:
        # Fallback to saved game_dir or default Steam install location
        try:
            from ..config import config_dir
            import json
            spath = config_dir() / "settings.json"
            if spath.is_file():
                with open(spath, "r", encoding="utf-8") as f:
                    sdata = json.load(f)
                    saved = sdata.get("farever_game_dir", "")
                    if saved and os.path.isdir(saved):
                        game_dir = saved
        except Exception:
            pass

    if not game_dir:
        for default_cand in (
            r"D:\Steam\steamapps\common\Farever",
            r"C:\Program Files (x86)\Steam\steamapps\common\Farever",
            r"C:\Steam\steamapps\common\Farever",
        ):
            if os.path.isdir(default_cand):
                game_dir = default_cand
                break

    result = {
        "detected": False,
        "dll_name": "",
        "dll_path": "",
        "option": 0,
        "game_dir": game_dir,
        "game_exe": game_exe,
        "proxy_in_game_dir": False,
        "status_summary": "",
        "third_party_dinput8": False,
        "third_party_dinput8_path": "",
        "third_party_version": False,
        "third_party_version_path": "",
        "third_party_userenv": False,
        "third_party_userenv_path": "",
        "is_farever_dll": False,
        "game_loadable_proxies": [],
        "proxy_loadable": None,
        "proxy_names": [],
        "proxy_importers": {},
        "proxy_load_stage": {},
        "proxy_shadowed_by": "",
    }

    norm_game_dir = os.path.normcase(game_dir) if game_dir else ""

    # Every Farever proxy found on disk, in scan order. A stale leftover and a
    # freshly installed one can coexist (switching proxy name copies the new
    # file first), so the scan alone must not decide which one is "the" proxy.
    found_proxies: list[str] = []

    # 1. Always inspect disk in game_dir for proxies and 3rd-party mods
    if game_dir and os.path.isdir(game_dir):
        disk_dinput = os.path.join(game_dir, "dinput8.dll")
        if os.path.isfile(disk_dinput):
            if is_farever_proxy(disk_dinput):
                result["proxy_in_game_dir"] = True
                result["dll_name"] = "dinput8.dll"
                result["is_farever_dll"] = True
                found_proxies.append("dinput8.dll")
            else:
                result["third_party_dinput8"] = True
                result["third_party_dinput8_path"] = disk_dinput
                try:
                    result["third_party_dinput8_size"] = os.path.getsize(disk_dinput)
                except Exception:
                    result["third_party_dinput8_size"] = 0

        for other_name in ("userenv.dll",):
            disk_other = os.path.join(game_dir, other_name)
            key = other_name.split(".")[0]
            if os.path.isfile(disk_other):
                if is_farever_proxy(disk_other):
                    result["proxy_in_game_dir"] = True
                    result["dll_name"] = other_name
                    result["is_farever_dll"] = True
                    found_proxies.append(other_name)
                else:
                    result[f"third_party_{key}"] = True
                    result[f"third_party_{key}_path"] = disk_other

        disk_version = os.path.join(game_dir, "version.dll")
        if os.path.isfile(disk_version):
            if is_farever_proxy(disk_version):
                result["proxy_in_game_dir"] = True
                result["dll_name"] = "version.dll"
                result["is_farever_dll"] = True
                found_proxies.append("version.dll")
            else:
                result["third_party_version"] = True
                result["third_party_version_path"] = disk_version
                try:
                    result["third_party_version_size"] = os.path.getsize(disk_version)
                except Exception:
                    result["third_party_version_size"] = 0

        # A proxy in the game folder only ever loads if some module in the
        # process imports that name, so read the import tables of every module
        # shipped in the folder. An empty map means "unknown" (folder or exe
        # unreadable), never "nothing is loadable" - a status message must not
        # accuse the user's install on the strength of a failed parse.
        try:
            from .pe_imports import proxy_import_map
            importers = proxy_import_map(game_dir)
        except Exception:
            importers = {}
        loadable = frozenset(n for n, mods in importers.items() if mods)
        result["proxy_importers"] = importers
        try:
            from .pe_imports import proxy_load_stage
            result["proxy_load_stage"] = proxy_load_stage(game_dir)
        except Exception:
            result["proxy_load_stage"] = {}
        try:
            from .pe_imports import headers_readable
            readable = headers_readable(game_dir)
        except Exception:
            readable = False
        if readable:
            # Headers parsed: the map is authoritative for every shipped name,
            # so an empty loadable set is a real "nothing loads here", not an
            # unknown. Only a failed parse keeps None - never accuse the
            # user's install on the strength of a failed read.
            result["game_loadable_proxies"] = sorted(loadable)
            if result["proxy_in_game_dir"]:
                # Prefer a proxy the game can actually load: a leftover
                # version.dll must never mask a working proxy that sits
                # next to it, or the diagnostics would point at the dead file.
                preferred = next((n for n in found_proxies if n in loadable), "")
                if preferred:
                    result["dll_name"] = preferred
                result["proxy_loadable"] = result["dll_name"] in loadable
        result["proxy_names"] = found_proxies

    if not modules:
        if (result["third_party_dinput8"] or result["third_party_version"]
                or result["third_party_userenv"]):
            conflicts = [name for name, present in (
                ("dinput8.dll", result["third_party_dinput8"]),
                ("version.dll", result["third_party_version"]),
                ("userenv.dll", result["third_party_userenv"])) if present]
            if conflicts:
                result["status_summary"] = f"⚠ Conflict: Other mod(s) are using {', '.join(conflicts)} in {game_dir}"
        elif result["third_party_dinput8"] and result["third_party_version"]:
            result["status_summary"] = f"⚠ Conflict: Other mods are using both dinput8.dll and version.dll in {game_dir}"
        elif result["third_party_dinput8"]:
            result["status_summary"] = f"⚠ Conflict: Another mod is using dinput8.dll in {game_dir}"
        elif result["third_party_version"]:
            result["status_summary"] = f"⚠ Conflict: Another mod is using version.dll in {game_dir}"
        elif result["proxy_in_game_dir"]:
            note = proxy_load_note(result)
            if result.get("proxy_loadable") is False:
                result["status_summary"] = (
                    f"{result['dll_name']} is installed, but {note} Install "
                    "dinput8.dll instead (the game's DirectX binding imports it, "
                    "so it loads on every game start)."
                )
            else:
                detail = f" ({note})" if note else ""
                if (result.get("proxy_load_stage") or {}).get(result["dll_name"]) == "optional":
                    detail += (
                        " This name is only pulled in when SDL3 / DLSS / Streamline "
                        "load, if they ever do; dinput8.dll arrives with the game's "
                        "runtime on every launch."
                    )
                result["status_summary"] = f"{result['dll_name']} found in game folder ({game_dir}). Start Farever.exe to activate.{detail}"
        else:
            result["status_summary"] = f"Game not running. Checked game directory: {game_dir or 'Not found'}."
        return result

    # 2. Option 2: farever_dps.dll (Injected)
    for m in modules:
        if m["name"].lower() == "farever_dps.dll":
            result["detected"] = True
            result["dll_name"] = "farever_dps.dll"
            result["dll_path"] = m["path"]
            result["option"] = 2
            result["is_farever_dll"] = True
            result["status_summary"] = f"farever_dps.dll detected in game (Option 2 Injector) @ {m['path']}"
            return result

    # 3. Option 1: a proxy DLL (version/dinput8/userenv) loaded
    # in memory. Ordered by PROXY_DLL_NAMES; the ranks keep the report stable
    # when more than one of ours is mapped.
    loaded_farever_proxy = None
    for m in modules:
        name_lower = m["name"].lower()
        if name_lower in ("dinput8.dll", "userenv.dll", "version.dll"):
            path_lower = m["path"].lower()
            mod_dir = os.path.normcase(os.path.dirname(m["path"]))
            is_game_folder = bool(norm_game_dir and mod_dir == norm_game_dir)
            is_system = bool("system32" in path_lower or "syswow64" in path_lower or "\\windows\\" in path_lower)

            if is_game_folder or not is_system:
                if is_farever_proxy(m["path"]):
                    # With more than one of ours mapped, report the one Windows
                    # loads at boot rather than whichever Windows listed first.
                    if (loaded_farever_proxy is None
                            or _proxy_rank(name_lower) < _proxy_rank(
                                loaded_farever_proxy["name"].lower())):
                        loaded_farever_proxy = m
                elif name_lower == "dinput8.dll":
                    result["third_party_dinput8"] = True
                    result["third_party_dinput8_path"] = m["path"]
                elif name_lower == "version.dll":
                    result["third_party_version"] = True
                    result["third_party_version_path"] = m["path"]
                elif name_lower == "userenv.dll":
                    result["third_party_userenv"] = True
                    result["third_party_userenv_path"] = m["path"]

    if loaded_farever_proxy:
        result["detected"] = True
        result["dll_name"] = loaded_farever_proxy["name"]
        result["dll_path"] = loaded_farever_proxy["path"]
        result["option"] = 1
        result["is_farever_dll"] = True
        summary = f"{loaded_farever_proxy['name']} proxy detected in game (Option 1 Proxy) @ {loaded_farever_proxy['path']}"
        coexist = []
        if result["third_party_dinput8"]:
            coexist.append("3rd-party dinput8.dll")
        if result["third_party_version"]:
            coexist.append("3rd-party version.dll")
        if coexist:
            summary += f" (coexisting alongside {' + '.join(coexist)})"
        result["status_summary"] = summary
        return result

    if result["proxy_in_game_dir"]:
        chosen = result["dll_name"]
        note = proxy_load_note(result)
        shadow = proxy_shadowed_path(modules, chosen, game_dir)
        if shadow:
            # Nothing to try: the name is already taken in this process.
            # Reusing a loaded module by name is what the loader does, so the
            # file in the game folder is dead weight until the mode changes.
            result["proxy_shadowed_by"] = shadow
            result["status_summary"] = (
                f"{chosen} is in the game folder, but the game already has {shadow} "
                "loaded, so Windows binds Farever's imports to that copy and never "
                "looks in the game folder - restarting will not change that. Install "
                "dinput8.dll instead (the game's DirectX binding imports it, so it "
                "always loads)."
            )
        elif result.get("proxy_loadable") is False:
            result["status_summary"] = (
                f"{chosen} is installed, but {note} Install dinput8.dll instead "
                "(the game's DirectX binding imports it, so it loads on every "
                "game start)."
            )
        elif (result.get("proxy_load_stage") or {}).get(chosen) == "runtime":
            # Imported straight by one of the game's own HashLink libraries, so
            # it comes up every launch - a moment after boot rather than during
            # it (directx.hdll -> dinput8.dll). Nothing is wrong; it just needs
            # the runtime to get that far.
            detail = f" {note}." if note else ""
            result["status_summary"] = (
                f"{chosen} found in game folder, but game has not loaded it yet.{detail} "
                "It arrives a moment after boot, when the game's runtime loads that "
                "component - it should appear by itself within a few seconds."
            )
        elif (result.get("proxy_load_stage") or {}).get(chosen) == "optional":
            # Its importers are all optional extras: SDL3, DLSS, Streamline. They
            # may simply never load, and an already-loaded System32 copy of the
            # name takes precedence even when they do.
            detail = f" {note}." if note else ""
            result["status_summary"] = (
                f"{chosen} found in game folder, but game has not loaded it yet.{detail} "
                "Those importers are optional and may never load, and an already-loaded "
                "System32 copy of the same name wins over the game folder, so a "
                "restart may never bring it up. dinput8.dll arrives with the game's "
                "runtime on every launch."
            )
        else:
            detail = f" {note}; it loads once that module does." if note else ""
            result["status_summary"] = (
                f"{chosen} found in game folder, but game has not loaded it yet.{detail} "
                "Restart the game so Windows loads the proxy DLL."
            )
        return result

    if (result["third_party_dinput8"] or result["third_party_version"]
            or result["third_party_userenv"]):
        conflicts = [name for name, present in (
            ("dinput8.dll", result["third_party_dinput8"]),
            ("version.dll", result["third_party_version"]),
            ("userenv.dll", result["third_party_userenv"])) if present]
        result["status_summary"] = f"Third-party mod detected using {', '.join(conflicts)} in {game_dir}. Consider using Option 2 (Injector) or Option 3 (Memory Reader)."
    elif result["third_party_dinput8"] and result["third_party_version"]:
        result["status_summary"] = (
            f"Third-party mods detected using both dinput8.dll and version.dll in {game_dir}. "
            "Consider using Option 2 (Injector) or Option 3 (Memory Reader)."
        )
    elif result["third_party_dinput8"]:
        result["status_summary"] = (
            f"Third-party mod detected using dinput8.dll in {game_dir}. "
            "Install userenv.dll to use FareverPal without conflicts."
        )
    elif result["third_party_version"]:
        # version.dll stopped shipping (2026-09-24), so there is no second
        # proxy name to suggest over a 3rd-party copy of it.
        result["status_summary"] = (
            f"Third-party mod detected using version.dll in {game_dir}. "
            "Install dinput8.dll to use FareverPal without conflicts."
        )
    else:
        result["status_summary"] = (
            f"No combat DLL detected in game process. Game directory: {game_dir}. "
            "For Option 1, install dinput8.dll next to Farever.exe (the game's "
            "DirectX binding imports it)."
        )
    return result


def proxy_shadowed_path(modules: list[dict], name: str, game_dir: str) -> str:
    """Path of an already-loaded module that has claimed `name`, if any.

    Windows resolves an import by name against the modules already in memory
    before it searches anywhere: if System32's version.dll (or another mod)
    is already mapped, the loader binds to that copy and the file sitting next
    to Farever.exe is never used. That is not a restart problem, and the
    diagnostics used to recommend exactly that restart.
    """
    if not name:
        return ""
    target = name.lower()
    norm_game = os.path.normcase(game_dir or "")
    for m in modules or []:
        if (m.get("name") or "").lower() != target:
            continue
        path = m.get("path") or ""
        mod_dir = os.path.normcase(os.path.dirname(path))
        if norm_game and mod_dir == norm_game:
            continue        # that is our own copy, not a shadow
        return path or m.get("name") or ""
    return ""


def _proxy_rank(name: str) -> int:
    """Boot-load reliability order for a proxy name (PROXY_DLL_NAMES order)."""
    try:
        return PROXY_DLL_NAMES.index(name.lower())
    except ValueError:
        return len(PROXY_DLL_NAMES)


def proxy_load_note(result: dict) -> str:
    """Why the installed proxy does or does not load, from the game's imports.

    "Nothing imports it" and "it loads late" are very different problems:
    dinput8.dll is imported by the game's DirectX binding, so it loads on every
    game start, while version.dll is only pulled in when SDL3 / Streamline / DLSS
    loads. Saying which module asks for the name is the difference between a
    user knowing what to try next and restarting the game all evening.
    """
    name = result.get("dll_name", "")
    if not name:
        return ""
    importers = (result.get("proxy_importers") or {}).get(name) or []
    if result.get("proxy_loadable") is False:
        return (
            f"Nothing in the game folder imports {name}, so Windows will not "
            "load it and restarting will not help."
        )
    if importers:
        listed = ", ".join(importers[:3])
        more = "" if len(importers) <= 3 else f" (+{len(importers) - 3} more)"
        return f"{name} is imported by {listed}{more}"
    return ""


def signal_proxy_go(pid: int | None, proxy_name: str = "",
                    log_cb=None) -> bool:
    """Write `<stem>_go.flag` into the game folder (the GO handshake).

    The t=0 proxy used to stay a pure forwarder until this file appeared next
    to it - the caller wrote it when the player locate had proven the world is
    up, the same gate that makes Option 2's injector safe. Retired with winmm
    (2026-09-23): no shipped proxy loads at t=0 any more, and PROXY_GO_ENABLED
    is empty, so this writes nothing for any current name. The machinery stays
    in case a future t=0 proxy needs the same gate. Never raises: a failed
    write is a no-op, never a hung game.
    """
    try:
        from .pe_imports import PROXY_GO_ENABLED, proxy_go_name
        name = (proxy_name or "").strip().lower()
        if name not in PROXY_GO_ENABLED:
            return False
        binfo = detect_combat_bridge(pid=pid or 0)
        game_dir = binfo.get("game_dir", "") or ""
        if not game_dir or not os.path.isdir(game_dir):
            return False
        flag = os.path.join(game_dir, proxy_go_name(name))
        with open(flag, "w", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%dT%H:%M:%S"))
        if callable(log_cb):
            log_cb(f"Combat Bridge: world is up — GO flag written ({os.path.basename(flag)}); "
                   "the proxy starts its bridge now.")
        return True
    except Exception:
        return False


def clear_stale_proxy_go(game_dir: str, proxy_name: str) -> None:
    """Delete a leftover `<stem>_go.flag` from a previous session (best-effort).

    A stale GO would arm a later launch the moment its proxy DLL loads - the
    boot-time start this whole handshake exists to prevent - so the caller
    clears it on attach, before any locate can write a fresh one.
    """
    try:
        from .pe_imports import proxy_go_name
        name = (proxy_name or "").strip().lower()
        if not game_dir or name not in PROXY_DLL_NAMES:
            return
        path = os.path.join(game_dir, proxy_go_name(name))
        if os.path.isfile(path):
            os.unlink(path)
    except Exception:
        pass


def sync_bridge_dev_flag(folder: str) -> None:
    """Best-effort: make `folder`'s dev logging flag match this run.

    Bridge file logging is dev-only. In a `run-log.bat` session this writes the
    `FAREVERMOD_DEBUG_LOG` flag beside the DLLs in `folder` (pointing the log
    into the app folder); in every other run it DELETES a leftover flag, which
    is what guarantees a normal user never ends up with a logger. Never raises,
    so a call site can put it on the attach / install / launch path.
    """
    try:
        from .bridge_log import sync_dev_flag
        sync_dev_flag(folder)
    except Exception:
        pass


def cleanup_game_dir(game_dir: str = "", include_proxies: bool = True,
                     log_line=None) -> dict:
    """Best-effort hygiene of the game folder for file-free capture modes.

    Removes the stray `farever_dps.log` the hook DLL writes next to
    Farever.exe (the user asked for NO log files in the game dir) and, when
    `include_proxies`, FareverPal's own proxy DLLs — identified by hash via
    is_farever_proxy() so a 3rd-party mod's version.dll/dinput8.dll is
    ALWAYS preserved and only reported.

    Also removes what our binaries leave behind: each proxy's
    `<stem>_forward_only.flag` / `<stem>_grace_ms.txt` (deliberate diagnostics,
    so only when the proxies go too), the dev-only `FAREVERMOD_DEBUG_LOG`
    logging flag (same: with the proxies, since it is what turns bridge file
    logging on at all), and the bridge logs — a leftover
    `<stem>_proxy.log` from a mode we are no longer in is worse than no log,
    because Live Diagnostics quotes the active mode's log and a stale file
    explains the wrong session. A preserved proxy keeps its own log, and every
    log that *is* deleted has its tail kept in memory first
    (bridge_log.snapshot_tail) - so the evidence for a crash outlives the file
    that recorded it and the game folder still ends up clean. In a developer
    run (run-log.bat) the flag is rewritten by the caller, so the log keeps
    flowing into the app folder.

    Never raises: every unlink is guarded (a DLL loaded by the running game
    is locked by Windows and simply stays). Returns a summary dict:
      {removed, kept_3rd_party, locked, failed, game_dir}
    """
    import os as _os
    from pathlib import Path as _Path

    def _keep_log_tail(path) -> None:
        """Keep a log's tail in memory before deleting the file.

        The reason a loaded-but-silent bridge was silent lives in that log, and
        Live Diagnostics shows it - so a cleanup must not be what destroys it.
        Deliberately swallowed: cleanup is best-effort by contract.
        """
        try:
            from .bridge_log import snapshot_if_bridge_log
            snapshot_if_bridge_log(str(path))
        except Exception:
            pass

    summary = {"removed": [], "kept_3rd_party": [], "locked": [],
               "failed": [], "game_dir": ""}
    try:
        if not game_dir:
            binfo = detect_combat_bridge(pid=0)
            game_dir = binfo.get("game_dir", "") or ""
        summary["game_dir"] = game_dir
        if not game_dir or not _os.path.isdir(game_dir):
            return summary
        gdir = _Path(game_dir)

        # 1. FareverPal proxy DLLs (hash-verified; 3rd-party files survive)
        if include_proxies:
            # iphlpapi.dll retired 2026-09-23; like winmm, cleanup still
            # honours the name so a leftover from this week's builds goes too.
            for cand in ("dinput8.dll", "userenv.dll", "version.dll",
                         "iphlpapi.dll"):
                p = gdir / cand
                if not p.is_file():
                    continue
                try:
                    if is_farever_proxy(str(p)):
                        try:
                            p.unlink()
                            summary["removed"].append(cand)
                        except PermissionError:
                            summary["locked"].append(cand)
                        except OSError as e:
                            summary["failed"].append(f"{cand}: {e}")
                    else:
                        summary["kept_3rd_party"].append(cand)
                except Exception:
                    pass

        # 2. Everything else of ours in the game folder, keyed by what it does
        #    to the next session:
        #      flag/grace  - a leftover forward-only flag leaves the proxy
        #                    forwarding but armed with nothing, and a grace
        #                    override changes when it starts. Both are deliberate
        #                    diagnostics, so they go only when the proxies do.
        #      go flag     - a stale GO would arm the NEXT launch the moment the
        #                    DLL loads (boot-time start, the crash window), so it
        #                    is cleared on every attach instead. Removed here
        #                    with the proxies like the other diagnostics.
        #      proxy log   - Live Diagnostics quotes the ACTIVE mode's log, so a
        #                    log from a mode we have left explains the wrong
        #                    session. It goes when we leave proxy mode, and also
        #                    when its own proxy is no longer installed at all
        #                    (the leftover case, whether or not proxies are in
        #                    scope here) - but NOT while that proxy stays put for
        #                    the next launch, where it is still this game's story.
        from .pe_imports import (proxy_debug_flag_name, proxy_flag_name,
                                 proxy_go_name, proxy_grace_name,
                                 proxy_log_name)
        byproducts: list[tuple[_Path, bool, bool]] = []   # (path, remove, is_log)
        # The dev-only logging flag goes with the proxies: leaving it behind
        # would turn a later normal run's bridge into a logger, which is the
        # one thing bridge logging must never do to a user.
        byproducts.append((gdir / proxy_debug_flag_name(),
                           include_proxies, False))
        for cand in ("dinput8.dll", "userenv.dll", "version.dll",
                     "iphlpapi.dll",   # removed 2026-09-23; legacy diagnostics
                     "winmm.dll"):
            installed = (gdir / cand).is_file()
            byproducts.append((gdir / proxy_flag_name(cand), include_proxies, False))
            byproducts.append((gdir / proxy_grace_name(cand), include_proxies, False))
            byproducts.append((gdir / proxy_go_name(cand), include_proxies, False))
            byproducts.append((gdir / proxy_log_name(cand),
                               include_proxies or not installed, True))
        for path, remove, is_log in byproducts:
            if not remove or not path.is_file():
                continue
            if is_log:
                _keep_log_tail(path)
            try:
                path.unlink()
                summary["removed"].append(path.name)
            except PermissionError:
                summary["locked"].append(path.name)
            except OSError as e:
                summary["failed"].append(f"{path.name}: {e}")

        # 3. The hook DLL's log file — ours, only ever in the game dir.
        logf = gdir / "farever_dps.log"
        if logf.is_file():
            _keep_log_tail(logf)
            try:
                logf.unlink()
                summary["removed"].append("farever_dps.log")
            except PermissionError:
                summary["locked"].append("farever_dps.log")
            except OSError as e:
                summary["failed"].append(f"farever_dps.log: {e}")

        if log_line is not None and callable(log_line):
            parts = []
            if summary["removed"]:
                parts.append(f"removed {', '.join(summary['removed'])}")
            if summary["locked"]:
                parts.append(f"locked by the running game: {', '.join(summary['locked'])}")
            if summary["kept_3rd_party"]:
                parts.append(f"preserved 3rd-party {', '.join(summary['kept_3rd_party'])}")
            if summary["failed"]:
                parts.append(f"failed: {'; '.join(summary['failed'])}")
            if parts:
                log_line(f"Combat Bridge cleanup ({game_dir}): " + "; ".join(parts) + ".")
    except Exception:
        pass
    return summary


class Proc:
    """Read-only handle to the game process."""

    def __init__(self, impl, kind: str, pid: int, name: str):
        self._impl = impl
        self.kind = kind
        self.pid = pid
        self.name = name

    def is_module_loaded(self, mod_name: str = "farever_dps.dll") -> bool:
        """Check if `mod_name` is loaded into this process."""
        return is_module_loaded(self.pid, mod_name)

    def get_loaded_modules(self) -> list[dict]:
        """List of all loaded modules (name, path, base, size) for this process."""
        return get_loaded_modules(self.pid)

    def detect_combat_bridge(self) -> dict:
        """Detect combat bridge DLLs (dinput8.dll, farever_dps.dll)."""
        return detect_combat_bridge(self.pid)

    # --- lifecycle -------------------------------------------------------
    @classmethod
    def attach(cls, name: str = PROCESS_NAME, backend: str | None = None) -> "Proc":
        """Attach to the process using the specified backend or auto-detect."""
        errors = []

        # 1. Try Native (Rust)
        if backend == "native" or (backend is None and _native is not None):
            try:
                r = _native.Reader.attach(name)
                return cls(r, "native", r.pid, name)
            except Exception as e:
                errors.append(f"native: {e}")
                if backend == "native":
                    raise ProcError(f"native attach failed: {e}") from e

        # 2. Try Pymem (Python)
        if backend == "pymem" or (backend is None and _pymem is not None):
            try:
                pm = _pymem.Pymem(name)
                return cls(pm, "pymem", pm.process_id, name)
            except Exception as e:
                errors.append(f"pymem: {e}")
                if backend == "pymem":
                    raise ProcError(f"pymem attach failed: {e}") from e

        # 3. Handle failure
        if errors:
            print(f"[Proc] Attachment fallback/failure log: {'; '.join(errors)}")

        if backend:
            raise ProcError(f"requested backend '{backend}' is not available or failed: {errors}")
        
        if not _native and not _pymem:
            raise ProcError("no memory backend available (build farever_native or install pymem)")
            
        raise ProcError(f"could not attach to {name}: {'; '.join(errors)}")

    def close(self) -> None:
        try:
            if self.kind == "native":
                self._impl.close()
            elif self.kind == "pymem":
                self._impl.close_process()
        except Exception:
            pass

    @property
    def has_scan(self) -> bool:
        return self.kind == "native"

    # --- reads -----------------------------------------------------------
    def read(self, addr: int, n: int) -> bytes:
        try:
            if self.kind == "native":
                return self._impl.read(addr, n)
            return self._impl.read_bytes(addr, n)
        except Exception as e:
            raise ProcError(f"read {n}B @ {addr:#x}: {e}") from e

    def try_read(self, addr: int, n: int) -> bytes | None:
        if self.kind == "native":
            return self._impl.try_read(addr, n)
        try:
            return self._impl.read_bytes(addr, n)
        except Exception:
            return None

    def u64(self, addr: int) -> int:
        if self.kind == "native":
            return self._impl.read_u64(addr)
        b = self.read(addr, 8)
        return struct.unpack("<Q", b)[0]

    def i32(self, addr: int) -> int:
        if self.kind == "native":
            return self._impl.read_i32(addr)
        b = self.read(addr, 4)
        return struct.unpack("<i", b)[0]

    def f64(self, addr: int) -> float:
        if self.kind == "native":
            return self._impl.read_f64(addr)
        b = self.read(addr, 8)
        return struct.unpack("<d", b)[0]

    def read_many(self, addrs: list[int], size: int) -> list[bytes | None]:
        """One batched read of many equal-size blocks (native), or a loop."""
        if self.kind == "native":
            return list(self._impl.read_many(addrs, size))
        out: list[bytes | None] = []
        for a in addrs:
            out.append(self.try_read(a, size))
        return out

    def read_many_u64(self, addrs: list[int]) -> list[int | None]:
        if self.kind == "native":
            return list(self._impl.read_many_u64(addrs))
        out: list[int | None] = []
        for a in addrs:
            b = self.try_read(a, 8)
            out.append(struct.unpack("<Q", b)[0] if b else None)
        return out

    # --- scan / modules --------------------------------------------------
    def find_bytes(self, needle: bytes, align: int = 1, rw_only: bool = False,
                   max_hits: int = 4096) -> list[int]:
        if self.kind != "native":
            raise ProcError("find_bytes needs the farever_native extension (memory scan)")
        return list(self._impl.find_bytes(needle, align, rw_only, max_hits))

    def find_qword(self, value: int, rw_only: bool = True, max_hits: int = 4096) -> list[int]:
        """Aligned scan for an 8-byte value (pointer / type tag)."""
        return self.find_bytes(struct.pack("<Q", value), 8, rw_only, max_hits)

    def find_bytes_in(self, needle: bytes, ranges: list[tuple[int, int]],
                      align: int = 1, max_hits: int = 4096) -> list[int]:
        """Scan ONLY `ranges` (each `(base, len)`) for `needle`."""
        if self.kind == "native":
            return list(self._impl.find_bytes_in(needle, ranges, align, max_hits))
        
        # Pymem fallback: if ranges are provided, just use the global scan and
        # filter. This is inefficient but correct.
        all_hits = self.find_bytes(needle, align, False, 10000)
        in_range = []
        for h in all_hits:
            for r_base, r_size in ranges:
                if r_base <= h < r_base + r_size:
                    in_range.append(h)
                    break
            if len(in_range) >= max_hits: break
        return in_range

    def find_qword_in(self, value: int, ranges: list[tuple[int, int]],
                      max_hits: int = 4096) -> list[int]:
        """Aligned 8-byte scan for `value`, restricted to `ranges`."""
        return self.find_bytes_in(struct.pack("<Q", value), ranges, 8, max_hits)

    def regions(self) -> list[tuple[int, int, int, bool]]:
        """Committed readable regions as (base, size, protect, writable). Native
        only (used by the AOB scanner)."""
        if self.kind != "native":
            raise ProcError("regions needs the farever_native extension")
        return list(self._impl.regions())

    def exe_path(self):
        """Query the running process for its exact executable Path on disk."""
        try:
            import ctypes
            from ctypes import wintypes
            from pathlib import Path
            k32 = ctypes.windll.kernel32
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            h_proc = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, self.pid)
            if h_proc:
                try:
                    buf = ctypes.create_unicode_buffer(1024)
                    size = wintypes.DWORD(1024)
                    if k32.QueryFullProcessImageNameW(h_proc, 0, buf, ctypes.byref(size)):
                        return Path(buf.value)
                finally:
                    k32.CloseHandle(h_proc)
        except Exception:
            pass
        return None
