"""Read-only process access over the Rust reader (preferred) or pymem (fallback).

The Rust backend adds batched reads and a fast scan that the player locator
needs; pymem covers basic reads only. The handle is opened without write rights
and no backend exposes a write primitive.
"""
from __future__ import annotations

import os
import struct

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


def get_loaded_modules(pid: int) -> list[dict]:
    """Return a list of loaded modules for pid with keys: 'name', 'path', 'base', 'size'."""
    if not pid:
        return []
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


def is_module_loaded(pid: int, mod_name: str = "farever_dps.dll") -> bool:
    """True if a module matching `mod_name` is currently loaded in `pid`."""
    mods = get_loaded_modules(pid)
    target = (mod_name or "").lower()
    return any(m["name"].lower() == target for m in mods)


def unload_module(pid: int, mod_name: str = "farever_dps.dll") -> bool:
    """Safely unloads a DLL module from target process using FreeLibrary."""
    if not pid:
        return False
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        mods = get_loaded_modules(pid)
        target = (mod_name or "").lower()
        matching = [m for m in mods if m["name"].lower() == target]
        if not matching:
            return True  # Module is already not loaded
        h_proc = k32.OpenProcess(0x1F0FFF, False, pid)
        if not h_proc:
            return False
        try:
            p_free_lib = k32.GetProcAddress(k32.GetModuleHandleW("kernel32.dll"), b"FreeLibrary")
            if not p_free_lib:
                return False
            for m in matching:
                base = m.get("base")
                if base:
                    h_thread = k32.CreateRemoteThread(h_proc, None, 0, p_free_lib, ctypes.c_void_p(base), 0, None)
                    if h_thread:
                        k32.WaitForSingleObject(h_thread, 2000)
                        k32.CloseHandle(h_thread)
            return True
        finally:
            k32.CloseHandle(h_proc)
    except Exception:
        return False


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

    # Find the first candidate directory that contains version.dll or dinput8.dll or farever_dps.dll
    for cand in candidates:
        if cand.is_dir():
            for dll_name in ("version.dll", "dinput8.dll", "farever_dps.dll"):
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

    names = ("dinput8.dll", "version.dll", "farever_dps.dll")
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
        'is_farever_dll': bool,
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
        "is_farever_dll": False,
    }

    norm_game_dir = os.path.normcase(game_dir) if game_dir else ""

    # 1. Always inspect disk in game_dir for proxies and 3rd-party mods
    if game_dir and os.path.isdir(game_dir):
        disk_dinput = os.path.join(game_dir, "dinput8.dll")
        if os.path.isfile(disk_dinput):
            if is_farever_proxy(disk_dinput):
                result["proxy_in_game_dir"] = True
                result["dll_name"] = "dinput8.dll"
                result["is_farever_dll"] = True
            else:
                result["third_party_dinput8"] = True
                result["third_party_dinput8_path"] = disk_dinput
                try:
                    result["third_party_dinput8_size"] = os.path.getsize(disk_dinput)
                except Exception:
                    result["third_party_dinput8_size"] = 0

        disk_version = os.path.join(game_dir, "version.dll")
        if os.path.isfile(disk_version):
            if is_farever_proxy(disk_version):
                result["proxy_in_game_dir"] = True
                result["dll_name"] = "version.dll"
                result["is_farever_dll"] = True
            else:
                result["third_party_version"] = True
                result["third_party_version_path"] = disk_version
                try:
                    result["third_party_version_size"] = os.path.getsize(disk_version)
                except Exception:
                    result["third_party_version_size"] = 0

    if not modules:
        if result["third_party_dinput8"] and result["third_party_version"]:
            result["status_summary"] = f"⚠ Conflict: Other mods are using both dinput8.dll and version.dll in {game_dir}"
        elif result["third_party_dinput8"]:
            result["status_summary"] = f"⚠ Conflict: Another mod is using dinput8.dll in {game_dir}"
        elif result["third_party_version"]:
            result["status_summary"] = f"⚠ Conflict: Another mod is using version.dll in {game_dir}"
        elif result["proxy_in_game_dir"]:
            result["status_summary"] = f"{result['dll_name']} found in game folder ({game_dir}). Start Farever.exe to activate."
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

    # 3. Option 1: version.dll or dinput8.dll Proxy loaded in memory
    loaded_farever_proxy = None
    for m in modules:
        name_lower = m["name"].lower()
        if name_lower in ("version.dll", "dinput8.dll"):
            path_lower = m["path"].lower()
            mod_dir = os.path.normcase(os.path.dirname(m["path"]))
            is_game_folder = bool(norm_game_dir and mod_dir == norm_game_dir)
            is_system = bool("system32" in path_lower or "syswow64" in path_lower or "\\windows\\" in path_lower)

            if is_game_folder or not is_system:
                if is_farever_proxy(m["path"]):
                    if not loaded_farever_proxy or name_lower == "version.dll":
                        loaded_farever_proxy = m
                elif name_lower == "dinput8.dll":
                    result["third_party_dinput8"] = True
                    result["third_party_dinput8_path"] = m["path"]
                elif name_lower == "version.dll":
                    result["third_party_version"] = True
                    result["third_party_version_path"] = m["path"]

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
        result["status_summary"] = (
            f"{chosen} found in game folder, but game has not loaded it yet. "
            "Restart the game so Windows loads the proxy DLL."
        )
        return result

    if result["third_party_dinput8"] and result["third_party_version"]:
        result["status_summary"] = (
            f"Third-party mods detected using both dinput8.dll and version.dll in {game_dir}. "
            "Consider using Option 2 (Injector) or Option 3 (Memory Reader)."
        )
    elif result["third_party_dinput8"]:
        result["status_summary"] = (
            f"Third-party mod detected using dinput8.dll in {game_dir}. "
            "Install version.dll to use FareverPal without conflicts."
        )
    elif result["third_party_version"]:
        result["status_summary"] = (
            f"Third-party mod detected using version.dll in {game_dir}. "
            "Install dinput8.dll to use FareverPal without conflicts."
        )
    else:
        result["status_summary"] = (
            f"No combat DLL detected in game process. Game directory: {game_dir}. "
            "For Option 1, install version.dll (or dinput8.dll) next to Farever.exe."
        )
    return result


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
        
        # Pymem fallback: scan each range
        out = []
        for base, size in ranges:
            try:
                # Pymem's pattern_scan_module/all doesn't take ranges easily
                # but we can read the whole range and search in python (slow)
                # or use pattern_scan_all and filter by range (faster if needle is rare)
                pass 
            except: pass
        
        # For now, if ranges are provided, just use the global scan and filter.
        # This is inefficient but correct.
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
