"""test_bridge_native - consolidated subsystem tests.

Merged without changing any test, from:
  * tests/test_bridge.py
  * tests/test_bridge_log.py
  * tests/test_inject_retry.py
  * tests/test_pe_imports.py
  * tests/test_proxy_exports.py
  * tests/test_compiled_fields.py

Imports are deduped and the shared `_qapp` fixture kept once;
where two source files bound one name differently, the later
was renamed `<name>__<file>` and only its own file's references
follow. See AGENTS.md: the suite is the spec.
"""
from __future__ import annotations


# --- shared imports ---
import os
import shutil
import pytest
from farever_companion import __version__
from farever_companion.core import proc
import struct
import tempfile
from pathlib import Path
from farever_companion.core.autoattach import AutoAttach, Act
import time
from farever_companion.core import bridge_log, pe_imports
from PySide6 import QtWidgets  # noqa: E402
from farever_companion.config import Settings  # noqa: E402
from farever_companion.ui import game_attach  # noqa: E402
from farever_companion.ui.game_attach import GameAttachmentController  # noqa: E402
from farever_companion.ui.inject_retry import (  # noqa: E402
    RETRY_MAX, RETRY_MIN_INTERVAL_S, InjectRetryState, retry_decision)
import pathlib
from tests.pe_fakes import build_pe, write_game, write_module
from farever_companion.core import proxy_exports
from build_tools import compiled_fields as CF

# ========================================================================
# from tests/test_bridge.py
# ========================================================================

"""The DPS bridge DLL's lifecycle: attach, unload safely, and clean up.

The bridge is loaded into the game by being dropped next to Farever.exe as a
proxy DLL, so the whole surface is coupled - finding the process, injecting,
detaching a hook without tearing it out of a running game, unloading the module,
and removing the files we put in the game directory. These tests cover that
lifecycle as one thing, because a change to any one of them moves the others.
"""
_FAKE_3RD_PARTY = b"MZ" + b"\x00" * 64 + b"127.0.0.1:49152" + b"\x00" * 64
_REF_DIR = os.path.join(os.path.dirname(__file__), "..", "dps_bridge")
_REF_DLLS = ("version.dll", "dinput8.dll", "userenv.dll", "farever_dps.dll")
def _write_dll(folder: str, name: str, content: bytes) -> str:
    path = os.path.join(folder, name)
    with open(path, "wb") as f:
        f.write(content)
    return path
def test_reference_checksums_are_authoritative_over_port_signature(tmp_path):
    """A 3rd-party dinput8.dll embedding 127.0.0.1:49152 is NOT FareverPal's
    proxy while reference checksums are available — the old signature check
    accepted it, which is why the game-folder check never reported '3rd party'."""
    proc.invalidate_proxy_cache()
    if not proc.get_reference_checksums():
        pytest.skip("dps_bridge reference binaries are not present")
    p = _write_dll(str(tmp_path), "dinput8.dll", _FAKE_3RD_PARTY)
    assert proc.is_farever_proxy(p) is False
def test_farevermod_marker_survives_rebuild_hash_changes(tmp_path):
    """A rebuilt proxy remains ours through its stable FareverMod marker even
    when its bytes do not match the bundled reference SHA-256."""
    proc.invalidate_proxy_cache()
    p = _write_dll(
        str(tmp_path), "version.dll",
        b"MZFAREVERMOD_PROXY_ID=FareverMod;FAREVERMOD_PROXY_VERSION=1.2.3;"
        b"FAREVERMOD_PROXY_ABI=1;FAREVERMOD_PROXY_HLBOOT=updated-build;"
        b"\\x00rebuilt")
    assert proc.is_farever_proxy(p) is True
    identity = proc.get_farevermod_proxy_identity(p)
    assert identity["FAREVERMOD_PROXY_ID"] == "FareverMod"
    assert identity["FAREVERMOD_PROXY_VERSION"] == "1.2.3"
    assert identity["FAREVERMOD_PROXY_HLBOOT"] == "updated-build"
def test_reference_binaries_embed_current_semantic_release():
    """The DLLs shipped beside the app really contain the app's SemVer release."""
    for name in _REF_DLLS:
        path = os.path.join(_REF_DIR, name)
        if not os.path.isfile(path):
            continue
        identity = proc.get_farevermod_proxy_identity(path)
        assert identity.get("FAREVERMOD_PROXY_VERSION") == __version__, (
            f"{name} does not embed FareverMod {__version__}: {identity}")
        assert identity.get("FAREVERMOD_PROXY_ABI") == "1", name
        assert identity.get("FAREVERMOD_PROXY_HLBOOT"), name
def test_reference_dlls_classify_as_farever(tmp_path):
    """Every reference proxy DLL in dps_bridge/ matches its own hash and is
    accepted (skip when the bridge folder is absent)."""
    proc.invalidate_proxy_cache()
    if not any(os.path.isfile(os.path.join(_REF_DIR, d)) for d in _REF_DLLS):
        pytest.skip("dps_bridge reference DLLs not present")
    ref_checksums = proc.get_reference_checksums()
    assert ref_checksums
    for dll in _REF_DLLS:
        src = os.path.join(_REF_DIR, dll)
        if not os.path.isfile(src):
            continue
        # A copy under a different proxy name must still be recognized
        # (version.dll / dinput8.dll are interchangeable payloads).
        p = _write_dll(str(tmp_path), dll, open(src, "rb").read())
        assert proc.is_farever_proxy(p) is True
def test_detect_combat_bridge_flags_third_party_dll(tmp_path):
    """detect_combat_bridge() marks a foreign dinput8.dll in the game folder
    as 3rd-party, and keeps that flag when FareverPal's own proxy is present
    alongside it."""
    proc.invalidate_proxy_cache()
    if not any(os.path.isfile(os.path.join(_REF_DIR, name)) for name in _REF_DLLS):
        pytest.skip("dps_bridge reference binaries are not present")
    _write_dll(str(tmp_path), "dinput8.dll", _FAKE_3RD_PARTY)
    info = proc.detect_combat_bridge(pid=0, game_dir=str(tmp_path))
    assert info["third_party_dinput8"] is True
    assert info["third_party_version"] is False
    assert info["proxy_in_game_dir"] is False
    assert info["detected"] is False

    version_src = os.path.join(_REF_DIR, "version.dll")
    if os.path.isfile(version_src):
        shutil.copy(version_src, os.path.join(str(tmp_path), "version.dll"))
        info2 = proc.detect_combat_bridge(pid=0, game_dir=str(tmp_path))
        assert info2["third_party_dinput8"] is True
        assert info2["proxy_in_game_dir"] is True
        assert info2["dll_name"] == "version.dll"
def test_get_loaded_modules_caches_snapshot_per_pid(monkeypatch):
    """The Toolhelp32 module snapshot (the dominant detect_combat_bridge
    cost) is cached per-PID for _MODULE_SNAPSHOT_TTL seconds: repeated polls
    share one snapshot, fresh=True / invalidate_module_cache() bypass it, and
    failed snapshots are never cached."""
    calls = {"n": 0}

    def fake_snapshot(pid):
        calls["n"] += 1
        return [{"name": "farever_dps.dll", "path": "C:\\g\\farever_dps.dll",
                 "base": 0x1000, "size": 4096}]

    monkeypatch.setattr(proc, "_snapshot_modules", fake_snapshot)
    proc.invalidate_module_cache()
    proc._MODULE_SNAPSHOT_TTL = 2.0

    # Same PID inside the TTL window -> one snapshot serves both polls.
    assert proc.get_loaded_modules(1234)[0]["name"] == "farever_dps.dll"
    assert proc.get_loaded_modules(1234)[0]["name"] == "farever_dps.dll"
    assert calls["n"] == 1

    # A different PID snapshots independently.
    proc.get_loaded_modules(5678)
    assert calls["n"] == 2

    # fresh=True bypasses the cache even inside the window.
    proc.get_loaded_modules(1234, fresh=True)
    assert calls["n"] == 3

    # Explicit invalidation drops the entry -> next call re-snapshots.
    proc.invalidate_module_cache(1234)
    proc.get_loaded_modules(1234)
    assert calls["n"] == 4

    # Expired TTL re-snapshots.
    proc.get_loaded_modules(1234)
    proc._MODULE_SNAPSHOT_TTL = 0.0
    proc.get_loaded_modules(1234)
    assert calls["n"] == 5
    proc._MODULE_SNAPSHOT_TTL = 2.0

    # An empty (failed) snapshot is not cached, so the next call retries.
    def failing_snapshot(pid):
        calls["n"] += 1
        return []

    monkeypatch.setattr(proc, "_snapshot_modules", failing_snapshot)
    proc.invalidate_module_cache()
    assert proc.get_loaded_modules(9999) == []
    assert proc.get_loaded_modules(9999) == []
    assert calls["n"] == 7
def test_is_module_loaded_fresh_sees_unload_immediately(monkeypatch):
    """is_module_loaded(fresh=True) bypasses the snapshot cache — the uninject
    wait loop depends on this to notice the DLL unmapping within ~0.1 s."""
    state = {"loaded": True}

    def fake_snapshot(pid):
        if state["loaded"]:
            return [{"name": "farever_dps.dll", "path": "C:\\g\\f.dll",
                     "base": 0x1000, "size": 4096}]
        return []

    monkeypatch.setattr(proc, "_snapshot_modules", fake_snapshot)
    proc.invalidate_module_cache()
    proc._MODULE_SNAPSHOT_TTL = 60.0

    assert proc.is_module_loaded(4321) is True            # cached snapshot
    state["loaded"] = False
    assert proc.is_module_loaded(4321) is True            # still cached (stale)
    assert proc.is_module_loaded(4321, fresh=True) is False   # sees the unload
    proc._MODULE_SNAPSHOT_TTL = 2.0
def test_signature_fallback_only_without_reference_dlls(tmp_path, monkeypatch):
    """Without any reachable reference DLLs (old frozen build with no bundled
    dps_bridge) the embedded telemetry signature still recognizes FareverPal's
    proxy — the fallback must keep working, but only then."""
    proc.invalidate_proxy_cache()
    monkeypatch.setattr(proc, "get_reference_checksums", lambda: {})
    own = _write_dll(str(tmp_path), "version.dll",
                     b"MZ" + b"\x00" * 32 + b"127.0.0.1:49152" + b"\x00" * 32)
    assert proc.is_farever_proxy(own) is True

    foreign = _write_dll(str(tmp_path), "dinput8.dll",
                         b"MZ" + b"\x00" * 96)
    assert proc.is_farever_proxy(foreign) is False
class _Recorder:
    def __init__(self, codes, loaded_after: int = 0):
        self.codes = list(codes)
        self.calls = 0
        self.loaded_after = loaded_after   # calls until is_loaded() turns False
        self.ready_called = False

    def call_su(self, flags: int) -> int:
        self.calls += 1
        if not self.codes:
            return proc.SU_OK
        code = self.codes.pop(0)
        # SU_OK keeps returning OK on retries (already-done path in the DLL).
        if code == proc.SU_OK:
            self.codes.insert(0, proc.SU_OK)
        return code

    def call_ready(self) -> int | None:
        self.ready_called = True
        return 1

    def is_loaded(self) -> bool:
        # True for the first `loaded_after` polls, then the unmap lands.
        if self.loaded_after <= 0:
            return False
        self.loaded_after -= 1
        return True
def _no_sleep(_s: float) -> None:
    return None
def test_first_try_unloads_and_polls_unmap():
    rec = _Recorder([proc.SU_OK], loaded_after=1)
    ok, msg = proc._safe_unload_via_export(
        rec.call_su, rec.call_ready, rec.is_loaded, sleep=_no_sleep)
    assert ok and rec.calls == 1
    assert "fully unloaded" in msg
    assert rec.ready_called is False   # unmap landed; no need to ask
def test_unmap_is_slow_falls_back_to_ready_query():
    """Module still mapped after the poll window but IsSafeUnloadReady()==1:
    hooks are gone, DLL is inert — still a success."""
    rec = _Recorder([proc.SU_OK], loaded_after=10_000)  # never unmaps
    ok, msg = proc._safe_unload_via_export(
        rec.call_su, rec.call_ready, rec.is_loaded, sleep=_no_sleep)
    assert ok and "inert" in msg and rec.ready_called
def test_busy_retries_then_succeeds():
    rec = _Recorder([proc.SU_BUSY, proc.SU_OK], loaded_after=2)
    ok, msg = proc._safe_unload_via_export(
        rec.call_su, rec.call_ready, rec.is_loaded, sleep=_no_sleep)
    assert ok and rec.calls == 2
def test_all_busy_attempts_exhausted():
    rec = _Recorder([proc.SU_BUSY] * 10)
    ok, msg = proc._safe_unload_via_export(
        rec.call_su, rec.call_ready, rec.is_loaded, sleep=_no_sleep,
        attempts=3)
    assert not ok and "busy" in msg.lower()
def test_booting_retries_then_succeeds():
    rec = _Recorder([proc.SU_BOOTING, proc.SU_OK], loaded_after=2)
    ok, msg = proc._safe_unload_via_export(
        rec.call_su, rec.call_ready, rec.is_loaded, sleep=_no_sleep)
    assert ok and rec.calls == 2
def test_worker_timeout_retries_then_succeeds():
    rec = _Recorder([proc.SU_WORKER, proc.SU_OK], loaded_after=1)
    ok, msg = proc._safe_unload_via_export(
        rec.call_su, rec.call_ready, rec.is_loaded, sleep=_no_sleep)
    assert ok and "fully unloaded" in msg
    assert rec.calls == 2
def test_worker_timeout_stays_bounded_when_wedged():
    rec = _Recorder([proc.SU_WORKER] * 5)
    ok, msg = proc._safe_unload_via_export(
        rec.call_su, rec.call_ready, rec.is_loaded, sleep=_no_sleep,
        attempts=3)
    assert not ok and "busy" in msg.lower()
    assert rec.calls == 3
def test_mh_failure_refuses_without_retry():
    rec = _Recorder([proc.SU_MH_FAIL])
    ok, msg = proc._safe_unload_via_export(
        rec.call_su, rec.call_ready, rec.is_loaded, sleep=_no_sleep)
    assert not ok and "restart the game" in msg.lower()
    assert rec.calls == 1
def test_remote_thread_failure_maps_to_clear_error():
    rec = _Recorder([])
    rec.call_su = lambda flags: -1
    ok, msg = proc._safe_unload_via_export(
        rec.call_su, rec.call_ready, rec.is_loaded, sleep=_no_sleep)
    assert not ok and "could not invoke" in msg.lower()
def test_unknown_status_reported():
    rec = _Recorder([4242])
    ok, msg = proc._safe_unload_via_export(
        rec.call_su, rec.call_ready, rec.is_loaded, sleep=_no_sleep)
    assert not ok and "4242" in msg
def test_status_codes_match_the_c_contract():
    """The hook source's safe_unload.c is the source of truth for these values
    (GameFiles/Farever/hooks/hook/farever_dps/safe_unload.c)."""
    assert (proc.SU_OK, proc.SU_BUSY, proc.SU_MH_FAIL,
            proc.SU_WORKER, proc.SU_BOOTING) == (0, 1001, 1002, 1003, 1004)
_REF_DLL = os.path.join(
    os.path.dirname(__file__), "..", "dps_bridge", "farever_dps.dll")
def _build_pe64_image(sections: list[tuple[str, int, bytes]],
                      exports: list[tuple[str, int]],
                      base_rva_of_name: int | None = None) -> bytes:
    """Minimal PE32+ image laid out exactly like a LOADED module.

    `sections` is a list of (name, rva, data); everything lives at its RVA
    (identity mapping, as the loader arranges). `exports` is (name, rva) pairs;
    name index i maps through the ordinals array to function slot `exports[i][1]`
    slot — deliberately shuffled for a later test.
    """
    dos = bytearray(0x40)
    dos[0:2] = b"MZ"
    e_lfanew = 0x40
    struct.pack_into("<I", dos, 0x3C, e_lfanew)

    # PE signature + 20-byte COFF header, then the optional header at +24 —
    # the same layout the parser expects in real images.
    sig = b"PE\x00\x00"
    file_hdr = struct.pack(
        "<HHIIIHH",
        0x8664,          # Machine
        1,               # NumberOfSections
        0, 0, 0,         # timestamps / symtab
        0xF0,            # SizeOfOptionalHeader
        0x2022,          # Characteristics: DLL
    )
    opt = bytearray(0xF0)
    struct.pack_into("<H", opt, 0, 0x20B)          # PE32+ magic
    # Export directory RVA at optional header offset 112 (data dir[0]).
    exp_rva = 0x200
    struct.pack_into("<I", opt, 112, exp_rva)
    struct.pack_into("<I", opt, 116, 0x80)         # export dir size

    # Export directory (40 bytes) at exp_rva, then the four tables laid out
    # sequentially — each placed at its own RVA below.
    names = [n for n, _ in exports]
    funcs = [f for _, f in exports]
    names_rva = 0x240
    funcs_rva = names_rva + 4 * len(names)
    ords_rva = funcs_rva + 4 * len(funcs)
    blob_rva = ords_rva + 2 * len(names)
    export_dir = bytearray(40)
    struct.pack_into("<I", export_dir, 24, len(names))    # NumberOfNames
    struct.pack_into("<I", export_dir, 28, funcs_rva)     # AddressOfFunctions
    struct.pack_into("<I", export_dir, 32, names_rva)     # AddressOfNames
    struct.pack_into("<I", export_dir, 36, ords_rva)      # AddrOfNameOrdinals

    # Name RVA table, function table, ordinal table, name strings.
    name_ent = bytearray()
    name_data = bytearray()
    off = blob_rva
    for n in names:
        name_ent += struct.pack("<I", off)
        raw = n.encode() + b"\x00"
        name_data += raw
        off += len(raw)
    func_ent = b"".join(struct.pack("<I", f) for f in funcs)
    # Deliberately shuffled ordinals: name i -> function slot (len-1-i).
    ord_ent = b"".join(struct.pack("<H", len(names) - 1 - i)
                       for i in range(len(names)))

    img = bytearray(0x400)
    img[0:len(dos)] = dos
    img[e_lfanew:e_lfanew + len(sig)] = sig
    img[e_lfanew + 4:e_lfanew + 4 + len(file_hdr)] = file_hdr
    img[e_lfanew + 24:e_lfanew + 24 + len(opt)] = opt
    img[exp_rva:exp_rva + len(export_dir)] = export_dir
    img[names_rva:names_rva + len(name_ent)] = name_ent
    img[funcs_rva:funcs_rva + len(func_ent)] = func_ent
    img[ords_rva:ords_rva + len(ord_ent)] = ord_ent
    img[blob_rva:blob_rva + len(name_data)] = name_data
    for _name, rva, data in sections:
        if len(img) < rva + len(data):
            img.extend(b"\x00" * (rva + len(data) - len(img)))
        img[rva:rva + len(data)] = data
    return bytes(img)
def test_real_dll_resolves_minhook_exports_like_getprocaddr():
    """Against the real farever_dps.dll mapped in memory, MH_DisableHook and
    MH_Uninitialize must resolve — with the same relative offsets a native
    GetProcAddress would yield — because safe_uninject_hook calls them."""
    if not os.path.isfile(_REF_DLL):
        pytest.skip("dps_bridge/farever_dps.dll not present")
    with open(_REF_DLL, "rb") as f:
        raw = f.read()

    # Simulate the loader: identity-map sections at their RVAs.
    e_lfanew = struct.unpack_from("<I", raw, 0x3C)[0]
    numsec = struct.unpack_from("<H", raw, e_lfanew + 6)[0]
    opt_size = struct.unpack_from("<H", raw, e_lfanew + 20)[0]
    sec0 = e_lfanew + 24 + opt_size
    size = max(
        (va + max(vsize, rsize))
        for i in range(numsec)
        for (vsize, va, rsize, ra) in [struct.unpack_from("<IIII", raw, sec0 + i * 40 + 8)]
    )
    img = bytearray(size)
    img[:len(raw)] = raw
    for i in range(numsec):
        vsize, va, rsize, ra = struct.unpack_from("<IIII", raw, sec0 + i * 40 + 8)
        img[va:va + rsize] = raw[ra:ra + rsize]
    mapped = bytes(img)

    BASE = 0x7FF800000000
    addr = proc._pe_export_addr(mapped, BASE, "MH_DisableHook")
    assert addr is not None, "MH_DisableHook must resolve from the real DLL"
    assert BASE < addr < BASE + len(mapped)

    addr2 = proc._pe_export_addr(mapped, BASE, "MH_Uninitialize")
    assert addr2 is not None, "MH_Uninitialize must resolve from the real DLL"
    assert BASE < addr2 < BASE + len(mapped)
    assert addr != addr2, "the two entry points must be distinct functions"

    assert proc._pe_export_addr(mapped, BASE, "NotAnExport") is None
def test_shuffled_ordinals_are_followed_not_assumed():
    """Name index i must go through the name-ordinal array; a naive
    'function slot == name slot' walker resolves the WRONG function when a
    linker emits shuffled ordinals (happens in practice)."""
    img = _build_pe64_image(
        [(".text", 0x1000, b"\x00" * 0x40)],
        exports=[("MH_DisableHook", 0x1000),
                 ("MH_Uninitialize", 0x1010)])
    BASE = 0x60000000
    # Names are in order but the ordinals array is reversed by the builder:
    # MH_DisableHook (name 0) must hit the LAST function slot (0x1000) and
    # MH_Uninitialize (name 1) the first (0x1010) per the shuffle.
    assert proc._pe_export_addr(img, BASE, "MH_DisableHook") == BASE + 0x1010
    assert proc._pe_export_addr(img, BASE, "MH_Uninitialize") == BASE + 0x1000
def test_export_directory_beyond_first_page_uses_read_more():
    """MinHook's export tables can sit past the header page; the remote reader
    fetches those bytes through read_more, so resolution must still work."""
    # Build the image, then emulate a 1-page remote read: only bytes below
    # 0x1000 come from `img`, everything else is fetched via read_more.
    img = _build_pe64_image(
        [(".text", 0x1000, b"\x00" * 0x40)],
        exports=[("MH_DisableHook", 0x1000),
                 ("MH_Uninitialize", 0x1010)])
    BASE = 0x70000000

    first_page = img[:0x1000]

    def read_more(rva: int, n: int) -> bytes | None:
        if 0 <= rva and rva + n <= len(img):
            return img[rva:rva + n]
        return None

    addr = proc._pe_export_addr(first_page, BASE, "MH_Uninitialize", read_more)
    assert addr == BASE + 0x1000
def test_garbage_and_truncated_images_fail_clean():
    assert proc._pe_export_addr(b"", 0x10000000, "X") is None
    assert proc._pe_export_addr(b"MZ" + b"\x00" * 10, 0x10000000, "X") is None
    assert proc._pe_export_addr(b"NOPE!!", 0x10000000, "X") is None
    # DOS header without a plausible PE offset.
    img = bytearray(0x400)
    img[0:2] = b"MZ"
    struct.pack_into("<I", img, 0x3C, 0x10_0000)   # beyond buffer
    assert proc._pe_export_addr(bytes(img), 0x10000000, "X") is None
def _mk_fake_farever_proxy(path: Path) -> None:
    """Write a file is_farever_proxy() accepts WITHOUT real reference DLLs
    present: the embedded-signature fallback (localhost UDP telemetry)."""
    path.write_bytes(b"MZ" + b"\x00" * 200 + b"127.0.0.1:49152" + b"\x00" * 64)
@pytest.fixture()
def isolated_bridge(monkeypatch):
    """No reference DLLs reachable -> signature fallback is authoritative."""
    monkeypatch.setattr(proc, "get_bridge_dir", lambda: "")
    monkeypatch.setattr(proc, "get_reference_checksums", lambda: {})
    monkeypatch.setattr(proc, "_REF_CHECKSUM_CACHE", {})
    yield
@pytest.fixture(autouse=True)
def _clean_snapshots():
    """Cleanup keeps the tail of every log it deletes; that store is process
    state, so it must not leak from one test into the next."""
    from farever_companion.core import bridge_log
    bridge_log.clear_snapshots()
    yield
    bridge_log.clear_snapshots()
def test_removes_log_and_farever_proxy(isolated_bridge):
    with tempfile.TemporaryDirectory() as td:
        gd = Path(td)
        _mk_fake_farever_proxy(gd / "version.dll")
        (gd / "farever_dps.log").write_text("=== attached ===\n")
        (gd / "Farever.exe").write_bytes(b"MZ")

        s = proc.cleanup_game_dir(str(gd), log_line=lambda m: None)

        assert not (gd / "farever_dps.log").exists()
        assert not (gd / "version.dll").exists()
        assert "farever_dps.log" in s["removed"] and "version.dll" in s["removed"]
def test_removes_proxy_diagnostic_files(isolated_bridge):
    """Every proxy checks for `<stem>_forward_only.flag` next to itself, and
    while that file exists it forwards calls but arms nothing - so a forgotten
    debugging flag makes every later launch look dead. `<stem>_grace_ms.txt` is
    the same story for its start-up timing. Both are ours by name, so cleanup
    removes them even when the DLL beside them is somebody else's."""
    with tempfile.TemporaryDirectory() as td:
        gd = Path(td)
        (gd / "iphlpapi.dll").write_bytes(b"MZ" + os.urandom(200))   # 3rd-party
        (gd / "iphlpapi_forward_only.flag").write_bytes(b"")
        (gd / "iphlpapi_grace_ms.txt").write_bytes(b"4000")
        (gd / "Farever.exe").write_bytes(b"MZ")

        s = proc.cleanup_game_dir(str(gd), log_line=lambda m: None)

        assert not (gd / "iphlpapi_forward_only.flag").exists()
        assert not (gd / "iphlpapi_grace_ms.txt").exists()
        assert "iphlpapi_forward_only.flag" in s["removed"]
        assert "iphlpapi_grace_ms.txt" in s["removed"]
        assert (gd / "iphlpapi.dll").exists()
        assert "iphlpapi.dll" in s["kept_3rd_party"]
def test_removes_the_logs_of_proxies_we_are_leaving_behind(isolated_bridge):
    """Live Diagnostics quotes the ACTIVE mode's log, so a log from a mode we
    have left is worse than no log: it explains the wrong session. Leaving
    proxy mode scrubs every shipped name; a detach keeps only the installed
    proxy's own log, which is still this game's story."""
    with tempfile.TemporaryDirectory() as td:
        gd = Path(td)
        _mk_fake_farever_proxy(gd / "dinput8.dll")        # ours, and installed
        for name in ("dinput8_proxy.log", "version_proxy.log", "iphlpapi_proxy.log",
                     "farever_dps.log"):
            (gd / name).write_text("[boot] attached\n")
        (gd / "Farever.exe").write_bytes(b"MZ")

        # Plain detach: the game just closed, the proxy stays installed.
        s = proc.cleanup_game_dir(str(gd), include_proxies=False,
                                  log_line=lambda m: None)

        assert (gd / "dinput8_proxy.log").exists()      # still the active mode
        assert not (gd / "version_proxy.log").exists()
        assert not (gd / "iphlpapi_proxy.log").exists()
        assert not (gd / "farever_dps.log").exists()
        assert (gd / "dinput8.dll").exists()
        assert sorted(s["removed"]) == ["farever_dps.log", "iphlpapi_proxy.log",
                                        "version_proxy.log"]

        # Switching away from proxy mode: its log goes too.
        s = proc.cleanup_game_dir(str(gd), include_proxies=True,
                                  log_line=lambda m: None)
        assert not (gd / "dinput8_proxy.log").exists()
        assert not (gd / "dinput8.dll").exists()
        assert "dinput8_proxy.log" in s["removed"]
def test_sync_bridge_dev_flag_writes_in_a_dev_run_and_clears_otherwise(monkeypatch, tmp_path):
    """The one call the attach / install / launch paths use: in a run-log.bat
    session it points the DLLs at the app folder's log, and in any normal run it
    deletes the flag so nothing can log."""
    from farever_companion.core import bridge_log
    from farever_companion.core.pe_imports import proxy_debug_flag_name

    game = tmp_path / "game"
    game.mkdir()
    flag = game / proxy_debug_flag_name()

    monkeypatch.setenv(bridge_log.DEV_LOG_ENV, str(tmp_path / "run.log"))
    proc.sync_bridge_dev_flag(str(game))
    assert flag.read_text(encoding="utf-8").strip() == str(tmp_path / "bridge_dev.log")

    monkeypatch.delenv(bridge_log.DEV_LOG_ENV, raising=False)
    proc.sync_bridge_dev_flag(str(game))
    assert not flag.exists()

    # A missing folder is a no-op, never an exception on the attach path.
    proc.sync_bridge_dev_flag("")
@pytest.mark.parametrize("requested", ["injector", "memory"])
def test_attach_falls_back_to_proxy_when_option1_file_is_present(requested):
    from farever_companion.ui.game_attach import _attach_capture_mode

    mode, conflict = _attach_capture_mode(requested, {
        "proxy_in_game_dir": True,
        "detected": True,
        "option": 1,
        "dll_name": "userenv.dll",
    })
    assert (mode, conflict) == ("proxy", True)
@pytest.mark.parametrize("requested", ["proxy", "injector", "memory"])
def test_attach_keeps_requested_mode_when_no_option1_proxy_is_present(requested):
    from farever_companion.ui.game_attach import _attach_capture_mode

    mode, conflict = _attach_capture_mode(requested, {
        "proxy_in_game_dir": False,
        "detected": False,
        "option": 0,
    })
    assert (mode, conflict) == (requested, False)
def test_cleanup_removes_the_dev_logging_flag(isolated_bridge):
    """The flag is what turns bridge file logging on at all, so leaving it in a
    game folder would make every later normal run write a log - the one thing
    bridge logging must never do to a user."""
    from farever_companion.core.pe_imports import proxy_debug_flag_name

    with tempfile.TemporaryDirectory() as td:
        gd = Path(td)
        _mk_fake_farever_proxy(gd / "dinput8.dll")
        (gd / proxy_debug_flag_name()).write_text(
            r"D:\somewhere\bridge_dev.log" + "\n", encoding="utf-8")
        (gd / "Farever.exe").write_bytes(b"MZ")

        s = proc.cleanup_game_dir(str(gd), log_line=lambda m: None)

        assert not (gd / proxy_debug_flag_name()).exists()
        assert proxy_debug_flag_name() in s["removed"]
def test_a_detach_keeps_the_dev_flag(isolated_bridge):
    """include_proxies=False is the detach path: the DLLs stay, so the dev flag
    that turns their logging on must stay too."""
    from farever_companion.core.pe_imports import proxy_debug_flag_name

    with tempfile.TemporaryDirectory() as td:
        gd = Path(td)
        _mk_fake_farever_proxy(gd / "dinput8.dll")
        (gd / proxy_debug_flag_name()).write_text("x\n", encoding="utf-8")
        (gd / "Farever.exe").write_bytes(b"MZ")

        proc.cleanup_game_dir(str(gd), include_proxies=False,
                              log_line=lambda m: None)

        assert (gd / proxy_debug_flag_name()).exists()
def test_keeps_the_diagnostics_of_a_proxy_that_stays(isolated_bridge):
    """A forward-only flag or grace override is a deliberate diagnostic, so it
    survives a detach - the user is mid-experiment. It goes when the proxies go.
    """
    with tempfile.TemporaryDirectory() as td:
        gd = Path(td)
        _mk_fake_farever_proxy(gd / "dinput8.dll")
        (gd / "dinput8_forward_only.flag").write_bytes(b"")
        (gd / "dinput8_grace_ms.txt").write_text("4000")
        (gd / "Farever.exe").write_bytes(b"MZ")

        proc.cleanup_game_dir(str(gd), include_proxies=False, log_line=lambda m: None)
        assert (gd / "dinput8_forward_only.flag").exists()
        assert (gd / "dinput8_grace_ms.txt").exists()

        proc.cleanup_game_dir(str(gd), include_proxies=True, log_line=lambda m: None)
        assert not (gd / "dinput8_forward_only.flag").exists()
        assert not (gd / "dinput8_grace_ms.txt").exists()
def test_go_flag_is_a_proxy_diagnostic_too(isolated_bridge):
    """A GO handshake file is ours by name and goes with the proxies; a stale
    one must never arm a later launch at boot time. Retired with winmm, but
    cleanup still honours the name (a leftover from an older app version)."""
    with tempfile.TemporaryDirectory() as td:
        gd = Path(td)
        _mk_fake_farever_proxy(gd / "dinput8.dll")
        (gd / "winmm_go.flag").write_text("2026-09-23T18:00:00")
        (gd / "Farever.exe").write_bytes(b"MZ")

        proc.cleanup_game_dir(str(gd), include_proxies=True, log_line=lambda m: None)
        assert not (gd / "winmm_go.flag").exists()
def test_signal_proxy_go_is_inert_since_winmm_retired(isolated_bridge, monkeypatch):
    """The GO handshake retired with winmm: PROXY_GO_ENABLED is empty, so no
    shipped proxy gets a flag and the player-located hook is a no-op."""
    with tempfile.TemporaryDirectory() as td:
        gd = Path(td)
        _mk_fake_farever_proxy(gd / "dinput8.dll")
        (gd / "Farever.exe").write_bytes(b"MZ")
        monkeypatch.setattr(proc, "detect_combat_bridge",
                            lambda pid=0: {"game_dir": str(gd)})

        for name in ("dinput8.dll", "version.dll", "iphlpapi.dll", "userenv.dll",
                     "winmm.dll", "evil.dll"):
            assert proc.signal_proxy_go(1234, name) is False
        assert not (gd / "dinput8_go.flag").exists()
        assert not (gd / "winmm_go.flag").exists()
def test_stale_go_flag_is_cleared_before_the_next_locate(isolated_bridge):
    """A go flag left over from a previous session must not arm a later launch
    the moment its DLL loads (boot-time start = the crash window). The attach
    path clears it via clear_stale_proxy_go before any locate can write a
    fresh one."""
    from farever_companion.core.proc import clear_stale_proxy_go

    with tempfile.TemporaryDirectory() as td:
        gd = Path(td)
        _mk_fake_farever_proxy(gd / "dinput8.dll")
        (gd / "Farever.exe").write_bytes(b"MZ")
        (gd / "dinput8_go.flag").write_text("stale")

        clear_stale_proxy_go(str(gd), "dinput8.dll")
        assert not (gd / "dinput8_go.flag").exists()
        # Idempotent: clearing a folder without one is a no-op.
        clear_stale_proxy_go(str(gd), "dinput8.dll")
def test_keeps_third_party_files_alone(isolated_bridge):
    with tempfile.TemporaryDirectory() as td:
        gd = Path(td)
        (gd / "some_mod.flag").write_bytes(b"")
        (gd / "some_mod_grace_ms.txt").write_bytes(b"7")
        (gd / "some_mod_proxy.log").write_text("not ours\n")
        (gd / "Farever.exe").write_bytes(b"MZ")

        s = proc.cleanup_game_dir(str(gd), log_line=lambda m: None)

        assert (gd / "some_mod.flag").exists()
        assert (gd / "some_mod_grace_ms.txt").exists()
        assert (gd / "some_mod_proxy.log").exists()
        assert s["removed"] == []
def test_preserves_third_party_dll(isolated_bridge):
    with tempfile.TemporaryDirectory() as td:
        gd = Path(td)
        # No farever signature -> must be treated as 3rd-party and kept
        (gd / "dinput8.dll").write_bytes(b"MZ" + os.urandom(300))
        (gd / "Farever.exe").write_bytes(b"MZ")

        s = proc.cleanup_game_dir(str(gd), log_line=lambda m: None)

        assert (gd / "dinput8.dll").exists()
        assert "dinput8.dll" in s["kept_3rd_party"]
        assert s["removed"] == []
def test_missing_dir_is_noop(isolated_bridge):
    s = proc.cleanup_game_dir(r"Z:\definitely\not\here", log_line=lambda m: None)
    assert s["removed"] == [] and s["failed"] == []
def test_locked_file_is_reported_not_raised(isolated_bridge, monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        gd = Path(td)
        _mk_fake_farever_proxy(gd / "version.dll")
        (gd / "Farever.exe").write_bytes(b"MZ")

        def _locked(self):
            raise PermissionError(32, "in use by another process")

        monkeypatch.setattr(Path, "unlink", _locked)

        s = proc.cleanup_game_dir(str(gd), log_line=lambda m: None)
        assert "version.dll" in s["locked"]
        assert s["failed"] == []
def test_log_only_mode_keeps_proxies(isolated_bridge):
    """include_proxies=False (detach path) must never touch the DLLs."""
    with tempfile.TemporaryDirectory() as td:
        gd = Path(td)
        _mk_fake_farever_proxy(gd / "version.dll")
        (gd / "farever_dps.log").write_text("log\n")

        s = proc.cleanup_game_dir(str(gd), include_proxies=False,
                                  log_line=lambda m: None)
        assert (gd / "version.dll").exists()
        assert "farever_dps.log" in s["removed"]
def test_detect_combat_bridge_disk_detection(isolated_bridge):
    """Proxy-in-dir still detects with no reference DLLs (signature fallback)."""
    with tempfile.TemporaryDirectory() as td:
        gd = Path(td)
        _mk_fake_farever_proxy(gd / "version.dll")
        binfo = proc.detect_combat_bridge(pid=0, game_dir=str(gd))
        assert binfo["proxy_in_game_dir"] is True
        assert binfo["is_farever_dll"] is True
def _a():
    return AutoAttach()
def test_not_running_is_noop():
    assert _a().decide(enabled=True, running_pid=None, attached_pid=None,
                       located=False, busy=False) is Act.NONE
def test_running_and_detached_attaches():
    assert _a().decide(enabled=True, running_pid=1234, attached_pid=None,
                       located=False, busy=False) is Act.ATTACH
def test_disabled_never_attaches():
    assert _a().decide(enabled=False, running_pid=1234, attached_pid=None,
                       located=False, busy=False) is Act.NONE
def test_busy_suppresses_attach():
    assert _a().decide(enabled=True, running_pid=1234, attached_pid=None,
                       located=False, busy=True) is Act.NONE
def test_attached_not_located_relocates():
    assert _a().decide(enabled=True, running_pid=1234, attached_pid=1234,
                       located=False, busy=False) is Act.RELOCATE
def test_attached_not_located_but_busy_is_noop():
    assert _a().decide(enabled=True, running_pid=1234, attached_pid=1234,
                       located=False, busy=True) is Act.NONE
def test_attached_and_located_is_noop():
    assert _a().decide(enabled=True, running_pid=1234, attached_pid=1234,
                       located=True, busy=False) is Act.NONE
def test_game_closed_detaches():
    assert _a().decide(enabled=True, running_pid=None, attached_pid=1234,
                       located=True, busy=False) is Act.DETACH
def test_game_restarted_new_pid_detaches():
    assert _a().decide(enabled=True, running_pid=5678, attached_pid=1234,
                       located=True, busy=False) is Act.DETACH
def test_detach_happens_even_when_disabled():
    # The hidden settings.json off-switch should still let go cleanly on close.
    assert _a().decide(enabled=False, running_pid=None, attached_pid=1234,
                       located=True, busy=False) is Act.DETACH
def test_restart_then_reattach():
    a = _a()
    # game closed while attached -> detach
    assert a.decide(enabled=True, running_pid=None, attached_pid=1234,
                    located=True, busy=False) is Act.DETACH
    # after the UI detaches, the relaunched game (new pid) -> attach again
    assert a.decide(enabled=True, running_pid=5678, attached_pid=None,
                    located=False, busy=False) is Act.ATTACH
def test_detect_combat_bridge_flags_a_proxy_the_game_cannot_load(isolated_bridge):
    """version.dll in the game folder must be reported as unloadable.

    Only uv.hdll and the DirectX binding import names we ship, so a synthetic
    install where nothing does leaves version.dll unloadable. The old message
    told the user to restart the game, and no amount of restarting would ever
    change it.
    """
    from tests.pe_fakes import write_game

    with tempfile.TemporaryDirectory() as td:
        gd = Path(td)
        write_game(gd, exe_imports=["libhl.dll", "KERNEL32.dll"],
                   modules={"libhl.dll": ["WS2_32.dll"]})
        _mk_fake_farever_proxy(gd / "version.dll")

        info = proc.detect_combat_bridge(pid=0, game_dir=str(gd))

        assert info["dll_name"] == "version.dll"
        assert info["proxy_loadable"] is False
        assert info["game_loadable_proxies"] == []
        assert "Restart the game" not in info["status_summary"]
        assert "dinput8" in info["status_summary"]
def test_a_proxy_loaded_by_a_later_module_is_reported_loadable(isolated_bridge):
    """userenv.dll is not imported by the exe or libhl, but uv.hdll imports it.

    The first cut of this check only read the exe and one hop, which reported
    the late names as unloadable on machines where Windows genuinely does load
    them (a moment after boot, when the game's own .hdll runtime loads) - the
    message must say which module pulls it in instead of sending the user to
    another dead end. (version.dll was the original subject here, via
    SDL3/DLSS/Streamline; it stopped shipping 2026-09-24 and is out of the
    shipped registry the scanner walks.)
    """
    from tests.pe_fakes import write_game

    with tempfile.TemporaryDirectory() as td:
        gd = Path(td)
        write_game(gd, exe_imports=["libhl.dll"],
                   modules={"libhl.dll": [],
                            "uv.hdll": ["USERENV.dll"]})
        _mk_fake_farever_proxy(gd / "userenv.dll")

        info = proc.detect_combat_bridge(pid=0, game_dir=str(gd))

        assert info["proxy_loadable"] is True
        assert info["proxy_importers"]["userenv.dll"] == ["uv.hdll"]
        assert "uv.hdll" in info["status_summary"]
def test_detect_combat_bridge_accepts_the_proxy_the_game_imports(isolated_bridge):
    from tests.pe_fakes import write_game

    with tempfile.TemporaryDirectory() as td:
        gd = Path(td)
        write_game(gd, exe_imports=["libhl.dll"],
                   modules={"directx.hdll": ["DINPUT8.dll"]})
        _mk_fake_farever_proxy(gd / "dinput8.dll")

        info = proc.detect_combat_bridge(pid=0, game_dir=str(gd))

        assert info["dll_name"] == "dinput8.dll"
        assert info["proxy_loadable"] is True
        assert "Start Farever.exe" in info["status_summary"]
def test_leftover_proxy_does_not_mask_the_working_one(isolated_bridge):
    """dinput8.dll + a leftover version.dll: the report must be about dinput8.

    Switching proxy name leaves the old file behind until it is cleaned up, and
    version.dll is scanned first - without the loadability preference the
    diagnostics would announce the dead file as the active proxy.
    """
    from tests.pe_fakes import write_game

    with tempfile.TemporaryDirectory() as td:
        gd = Path(td)
        write_game(gd, exe_imports=["libhl.dll"],
                   modules={"directx.hdll": ["DINPUT8.dll"]})
        _mk_fake_farever_proxy(gd / "version.dll")
        _mk_fake_farever_proxy(gd / "dinput8.dll")

        info = proc.detect_combat_bridge(pid=0, game_dir=str(gd))

        assert info["proxy_names"] == ["dinput8.dll", "version.dll"]
        assert info["dll_name"] == "dinput8.dll"
        assert info["proxy_loadable"] is True
        assert "will not load" not in info["status_summary"]
def test_unreadable_game_exe_never_accuses_the_install(isolated_bridge):
    """No exe / a stub header means "unknown", so the status must stay the
    plain restart hint instead of claiming the proxy will never load."""
    with tempfile.TemporaryDirectory() as td:
        gd = Path(td)
        (gd / "Farever.exe").write_bytes(b"MZ")
        _mk_fake_farever_proxy(gd / "version.dll")

        info = proc.detect_combat_bridge(pid=0, game_dir=str(gd))

        assert info["proxy_loadable"] is None
        assert info["game_loadable_proxies"] == []
        assert "Start Farever.exe to activate" in info["status_summary"]
def test_proxy_shadowed_path_finds_an_already_loaded_system_copy(tmp_path):
    """version.dll next to Farever.exe is dead weight while the game has
    already mapped System32's copy: the loader reuses a module by name and
    never searches the game folder, so no restart can change it."""
    mods = [{"name": "version.dll", "path": r"C:\Windows\System32\version.dll"},
            {"name": "libhl.dll", "path": os.path.join(str(tmp_path), "libhl.dll")}]

    shadow = proc.proxy_shadowed_path(mods, "version.dll", str(tmp_path))

    assert shadow.lower().endswith("version.dll")
    assert "system32" in shadow.lower()
def test_proxy_shadowed_path_ignores_our_own_copy(tmp_path):
    mods = [{"name": "version.dll", "path": os.path.join(str(tmp_path), "version.dll")}]
    assert proc.proxy_shadowed_path(mods, "version.dll", str(tmp_path)) == ""
def test_proxy_shadowed_path_is_empty_when_the_name_is_not_mapped(tmp_path):
    assert proc.proxy_shadowed_path([], "version.dll", str(tmp_path)) == ""
    assert proc.proxy_shadowed_path(
        [{"name": "dinput8.dll", "path": r"C:\Windows\System32\dinput8.dll"}],
        "version.dll", str(tmp_path)) == ""

# ========================================================================
# from tests/test_bridge_log.py
# ========================================================================

"""Reading the bridge's own log back for diagnostics.

The bridge DLL writes its progress to a log in the game folder, and that file is
the only place the reason for a loaded-but-silent bridge appears ("table not
present yet", "hooks partial arm", "hlboot mismatch"). Live Diagnostics quotes
its tail now, so these tests pin the reader: which file each capture mode uses,
that only the tail is read and never a half-cut line, that heartbeat spam is
counted instead of shown, and that a missing log explains itself rather than
raising.
"""
@pytest.fixture(autouse=True)
def _clean_snapshots__bridge_log():
    """Kept tails are process state; leaking them across tests would hide the
    very staleness the reader is supposed to catch."""
    bridge_log.clear_snapshots()
    yield
    bridge_log.clear_snapshots()
def _write(tmp_path, name: str, text: str):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path
def _write_log(tmp_path, name: str, text: str):
    """A log that looks like a real one: appended to across its lifetime."""
    with open(tmp_path / name, "a", encoding="utf-8") as f:
        f.write(text)
    return tmp_path / name
def test_proxy_mode_reads_the_log_of_the_active_proxy(tmp_path):
    _write(tmp_path, "dinput8_proxy.log", "[boot] dinput8.dll proxy bridge init start\n")
    _write(tmp_path, "userenv_proxy.log", "[boot] userenv.dll proxy bridge init start\n")

    info = bridge_log.tail_bridge_log(str(tmp_path), "proxy", "userenv.dll")

    assert info["found"] is True
    assert info["name"] == "userenv_proxy.log"
    assert info["lines"] == ["[boot] userenv.dll proxy bridge init start"]
def test_proxy_mode_reads_only_the_active_proxy_log(tmp_path):
    """A leftover log from another proxy is another session's evidence: quoting
    it would explain the wrong name, the same way a leftover DLL used to mask
    the working one."""
    _write(tmp_path, "dinput8_proxy.log", "[boot] dinput8.dll proxy bridge init start\n")

    info = bridge_log.tail_bridge_log(str(tmp_path), "proxy", "userenv.dll")

    assert info["found"] is False
    assert "userenv_proxy.log" in info["note"]
def test_proxy_mode_falls_back_when_the_active_name_is_unknown(tmp_path):
    """The scan may not know which name is installed yet; any log on disk beats
    the card saying nothing at all."""
    _write(tmp_path, "dinput8_proxy.log", "[table] functions_ptrs table located\n")
    info = bridge_log.tail_bridge_log(str(tmp_path), "proxy", "")
    assert info["name"] == "dinput8_proxy.log"
def test_injector_mode_reads_the_injected_dll_log(tmp_path):
    _write(tmp_path, "farever_dps.log", "[hook] onInflictDamage findex 4927 -> OK\n")
    info = bridge_log.tail_bridge_log(str(tmp_path), "injector")
    assert info["name"] == "farever_dps.log"
def test_memory_mode_has_no_log_to_read(tmp_path):
    """The memory reader is not inside the game, so it has nothing to log -
    and the card must stay silent rather than show a stray proxy log."""
    _write(tmp_path, "dinput8_proxy.log", "[boot] x\n")
    info = bridge_log.tail_bridge_log(str(tmp_path), "memory")
    assert info["found"] is False
    assert info["note"] == ""
def test_heartbeats_are_counted_instead_of_shown(tmp_path):
    lines = ["[boot] dinput8.dll proxy bridge init start"]
    lines += [f"[HEARTBEAT] Bridge alive (tick: {i}, hooks: 4)" for i in range(1, 30)]
    _write(tmp_path, "dinput8_proxy.log", "\n".join(lines) + "\n")

    info = bridge_log.tail_bridge_log(str(tmp_path), "proxy", "dinput8.dll")

    assert info["heartbeats"] == 29
    assert info["lines"] == ["[boot] dinput8.dll proxy bridge init start"]
def test_a_long_log_is_read_from_the_end(tmp_path):
    filler = "\n".join(f"[hunt] heap scan {i}% (1000 regions, 3 s elapsed)"
                       for i in range(4000))
    _write(tmp_path, "dinput8_proxy.log",
           filler + "\n[verify] hlboot verified 4a7c54be74b3 - 4/4 hooked findexes match\n")

    info = bridge_log.tail_bridge_log(str(tmp_path), "proxy", "dinput8.dll", max_lines=3)

    assert info["found"] is True
    assert len(info["lines"]) == 3
    assert info["lines"][-1].startswith("[verify] hlboot verified")
    assert all(len(ln) > 10 for ln in info["lines"])   # no half-cut line at the seek point
def test_a_missing_log_says_which_file_is_missing(tmp_path):
    proxy = bridge_log.tail_bridge_log(str(tmp_path), "proxy", "dinput8.dll")
    assert proxy["found"] is False
    assert "dinput8_proxy.log" in proxy["note"]
    assert "dinput8.dll" in proxy["note"]

    injector = bridge_log.tail_bridge_log(str(tmp_path), "injector")
    assert injector["found"] is False
    assert "farever_dps.log" in injector["note"]
def test_age_is_reported_so_a_stale_log_is_visible(tmp_path):
    path = _write(tmp_path, "dinput8_proxy.log", "[boot] x\n")
    old = time.time() - 120
    os.utime(path, (old, old))

    info = bridge_log.tail_bridge_log(str(tmp_path), "proxy", "dinput8.dll")

    assert info["age_s"] >= 100
def test_an_unchanged_log_is_parsed_once(tmp_path, monkeypatch):
    """Live Diagnostics repaints every second; an unchanged log must not be
    re-read on every frame."""
    _write(tmp_path, "dinput8_proxy.log", "[boot] one\n")
    parsed: list[str] = []
    real = bridge_log._parse_tail

    def counting(path, st, n):
        parsed.append(path)
        return real(path, st, n)

    monkeypatch.setattr(bridge_log, "_parse_tail", counting)
    first = bridge_log.tail_bridge_log(str(tmp_path), "proxy", "dinput8.dll")
    second = bridge_log.tail_bridge_log(str(tmp_path), "proxy", "dinput8.dll")

    assert len(parsed) == 1
    assert first == second
def test_no_game_dir_is_not_an_error():
    info = bridge_log.tail_bridge_log("", "proxy", "dinput8.dll")
    assert info["found"] is False
    assert bridge_log.bridge_log_candidates("", "proxy") == []
def test_a_kept_tail_survives_the_file_being_deleted(tmp_path):
    """A crash prompt the cleanup, and the log that explains it is exactly what
    cleanup deletes. The tail is kept, so the evidence outlives the file."""
    path = _write_log(tmp_path, "dinput8_proxy.log",
                      "[boot] loader idle after 512 ms (probe=idle, floor=0 ms)\n"
                      "[verify] hlboot MISMATCH 4a7c54be74b3 != manifest b1121d37bf33\n"
                      "[verify] REFUSING to arm 4 hooks (verdict=1, targets_ok=1)\n")

    assert bridge_log.snapshot_if_bridge_log(str(path)) is True
    path.unlink()

    info = bridge_log.tail_bridge_log(str(tmp_path), "proxy", "dinput8.dll")

    assert info["found"] is True
    assert info["snapshot"] is True
    assert info["lines"][-1].startswith("[verify] REFUSING to arm 4 hooks")
    assert "removed by the game-folder cleanup" in info["note"]
    # And it keeps aging, so a report cannot present it as freshly written.
    assert isinstance(info["age_s"], float)
def test_a_live_file_wins_over_a_kept_tail(tmp_path):
    """The next session's log is newer evidence; the kept one must not shadow it."""
    path = _write_log(tmp_path, "dinput8_proxy.log", "[old] previous session\n")
    bridge_log.snapshot_if_bridge_log(str(path))
    path.unlink()
    _write(tmp_path, "dinput8_proxy.log", "[new] hooks armed\n")

    info = bridge_log.tail_bridge_log(str(tmp_path), "proxy", "dinput8.dll")

    assert info["snapshot"] is False
    assert info["lines"] == ["[new] hooks armed"]
def test_a_kept_tail_honours_the_callers_line_budget(tmp_path):
    path = _write_log(tmp_path, "farever_dps.log",
                      "".join(f"[hook] findex {i} -> OK\n" for i in range(30)))
    bridge_log.snapshot_if_bridge_log(str(path))
    path.unlink()

    info = bridge_log.tail_bridge_log(str(tmp_path), "injector", max_lines=3)

    assert len(info["lines"]) == 3
    assert info["lines"][-1] == "[hook] findex 29 -> OK"
def test_a_kept_tail_belongs_to_one_folder(tmp_path):
    """Two installs, two stories: one folder's tail must not answer for another."""
    other = tmp_path / "other"
    other.mkdir()
    path = _write_log(tmp_path, "dinput8_proxy.log", "[boot] from the real install\n")
    bridge_log.snapshot_if_bridge_log(str(path))
    path.unlink()

    info = bridge_log.tail_bridge_log(str(other), "proxy", "dinput8.dll")

    assert info["found"] is False
    assert "dinput8_proxy.log" in info["note"]
def test_only_logs_are_kept(tmp_path):
    """A flag or a grace override is not a log: filing one as evidence would
    push a real tail out of the bounded store."""
    flag = _write(tmp_path, "dinput8_forward_only.flag", "")
    grace = _write(tmp_path, "dinput8_grace_ms.txt", "4000")
    assert bridge_log.snapshot_if_bridge_log(str(flag)) is False
    assert bridge_log.snapshot_if_bridge_log(str(grace)) is False
    assert bridge_log.snapshot_for(str(grace)) is None
def test_an_empty_or_missing_log_keeps_nothing(tmp_path):
    empty = _write(tmp_path, "dinput8_proxy.log", "")
    assert bridge_log.snapshot_if_bridge_log(str(empty)) is False
    assert bridge_log.snapshot_if_bridge_log(str(tmp_path / "nope.log")) is False
def test_the_store_is_bounded(tmp_path):
    for i in range(bridge_log._MAX_SNAPSHOTS + 4):
        path = _write_log(tmp_path, f"spam{i}_proxy.log", f"[boot] {i}\n")
        bridge_log.snapshot_if_bridge_log(str(path))
    assert len(bridge_log._snapshots) <= bridge_log._MAX_SNAPSHOTS
@pytest.fixture
def dev_run(monkeypatch, tmp_path):
    """A run-log.bat session: FAREVER_DEV_CRASH_LOG points at <app>/run.log."""
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    monkeypatch.setenv(bridge_log.DEV_LOG_ENV, str(app_dir / "run.log"))
    return app_dir
def _no_dev_run(monkeypatch):
    monkeypatch.delenv(bridge_log.DEV_LOG_ENV, raising=False)
    monkeypatch.delenv(bridge_log.DEV_LOG_DIR_ENV, raising=False)
def test_the_bridge_log_folder_can_differ_from_the_crash_logs(dev_run, tmp_path,
                                                             monkeypatch):
    """The two dev logs are separate files and may live in separate folders.

    They used to share FAREVER_DEV_CRASH_LOG - its folder was the hint for
    where bridge_dev.log went - so putting the bridge log next to the bridge
    binaries also dragged run.log out of the repo root. run-log.bat now sets
    FAREVER_BRIDGE_DEV_LOG_DIR for the bridge log only, and the crash log
    variable must keep meaning the crash log and nothing else.
    """
    bridge_dir = tmp_path / "dps_bridge"
    bridge_dir.mkdir()
    monkeypatch.setenv(bridge_log.DEV_LOG_DIR_ENV, str(bridge_dir))

    assert bridge_log.dev_log_dir() == str(bridge_dir)
    assert bridge_log.dev_log_path() == str(bridge_dir / "bridge_dev.log")
    # The crash log's own variable is untouched by that choice.
    assert os.environ[bridge_log.DEV_LOG_ENV] == str(dev_run / "run.log")
def test_without_the_override_the_crash_log_folder_is_still_the_hint(dev_run):
    """Falling back to the crash log's folder is the pre-split behaviour and
    stays supported, so a dev who sets only FAREVER_DEV_CRASH_LOG is fine."""
    assert bridge_log.DEV_LOG_DIR_ENV not in os.environ
    assert bridge_log.dev_log_dir() == str(dev_run)
    assert bridge_log.dev_log_path() == str(dev_run / "bridge_dev.log")
def test_no_dev_run_means_no_dev_log_anywhere(monkeypatch, tmp_path):
    _no_dev_run(monkeypatch)
    assert bridge_log.dev_mode() is False
    assert bridge_log.dev_log_candidates(str(tmp_path)) == []
def test_a_dev_run_writes_the_flag_and_reads_the_app_folder_log(dev_run):
    game = dev_run.parent / "game"
    game.mkdir()

    flag = bridge_log.write_dev_flag(str(game))

    assert flag
    assert os.path.basename(flag) == pe_imports.proxy_debug_flag_name()
    with open(bridge_log.dev_log_path(), "w", encoding="utf-8") as f:
        f.write("[verify] hlboot verified 4a7c54be74b3\n")

    info = bridge_log.tail_bridge_log(str(game), "proxy", "dinput8.dll")

    assert info["found"] is True
    assert info["name"] == bridge_log.DEV_LOG_NAME
    assert info["path"] == bridge_log.dev_log_path()
    assert info["lines"] == ["[verify] hlboot verified 4a7c54be74b3"]
def test_the_flag_itself_names_the_log(monkeypatch, tmp_path):
    """The flag is the same source of truth the DLL reads, so a dev pointing it
    at any path by hand is honoured - and the reader works without the env."""
    _no_dev_run(monkeypatch)
    game = tmp_path / "game"
    game.mkdir()
    custom = tmp_path / "custom.log"
    custom.write_text("[boot] custom path\n", encoding="utf-8")
    (game / pe_imports.proxy_debug_flag_name()).write_text(
        str(custom) + "\n", encoding="utf-8")

    info = bridge_log.tail_bridge_log(str(game), "proxy", "dinput8.dll")

    assert info["found"] is True
    assert info["path"] == str(custom)
def test_a_normal_run_removes_a_leftover_flag(monkeypatch, tmp_path):
    """A flag left in a user's game folder would make every later launch write
    a log - sync_dev_flag is the call every install / attach path uses."""
    _no_dev_run(monkeypatch)
    game = tmp_path / "game"
    game.mkdir()
    flag = game / pe_imports.proxy_debug_flag_name()
    flag.write_text(r"somewhere\bridge_dev.log" + "\n", encoding="utf-8")

    assert bridge_log.sync_dev_flag(str(game)) == ""
    assert not flag.exists()
    assert bridge_log.remove_dev_flag(str(game)) is False   # already gone
def test_a_dev_run_rewrites_a_stale_flag(dev_run):
    game = dev_run.parent / "game"
    game.mkdir()
    flag = game / pe_imports.proxy_debug_flag_name()
    flag.write_text("stale\n", encoding="utf-8")

    bridge_log.sync_dev_flag(str(game))

    assert flag.read_text(encoding="utf-8").strip() == bridge_log.dev_log_path()
def test_a_dev_run_that_has_written_nothing_says_so(dev_run):
    """The legacy "no <name>_proxy.log in the game folder" note would send a
    developer looking for a file nothing creates any more."""
    game = dev_run.parent / "game"
    game.mkdir()

    info = bridge_log.tail_bridge_log(str(game), "proxy", "dinput8.dll")

    assert info["found"] is False
    assert bridge_log.DEV_LOG_NAME in info["note"]
    assert "developer logging" in info["note"]
def test_a_legacy_game_folder_log_is_still_read_in_a_dev_run(dev_run):
    """An un-updated DLL still writes into the game folder; the dev log must
    not hide it."""
    game = dev_run.parent / "game"
    game.mkdir()
    _write(game, "dinput8_proxy.log", "[boot] legacy build\n")

    info = bridge_log.tail_bridge_log(str(game), "proxy", "dinput8.dll")

    assert info["name"] == "dinput8_proxy.log"
    assert info["lines"] == ["[boot] legacy build"]
def test_the_dev_log_is_never_snapshotted(dev_run):
    """The dev log lives beside the app, not in the game folder - the
    game-folder cleanup must not treat it as a bridge log to keep."""
    path = bridge_log.dev_log_path()
    with open(path, "w", encoding="utf-8") as f:
        f.write("[boot] dev\n")
    assert bridge_log.snapshot_if_bridge_log(path) is False
    assert bridge_log.snapshot_for(path) is None
def test_cleanup_keeps_the_tail_of_every_log_it_deletes():
    """End to end: the game-folder cleanup deletes the logs and the reasons they
    recorded are still readable afterwards."""
    import tempfile
    from pathlib import Path

    from farever_companion.core import proc

    with tempfile.TemporaryDirectory() as td:
        gd = Path(td)
        (gd / "dinput8_proxy.log").write_text(
            "[hunt] loader busy (a module is loading) - holding the hunt\n"
            "[hunt] table not present yet; retrying (29 s left)\n", encoding="utf-8")
        (gd / "farever_dps.log").write_text("[boot] injected\n", encoding="utf-8")
        (gd / "Farever.exe").write_bytes(b"MZ")

        summary = proc.cleanup_game_dir(str(gd), include_proxies=True,
                                        log_line=lambda m: None)

        assert not (gd / "dinput8_proxy.log").exists()
        assert not (gd / "farever_dps.log").exists()
        assert "dinput8_proxy.log" in summary["removed"]

        kept = bridge_log.tail_bridge_log(str(gd), "proxy", "dinput8.dll")
        assert kept["snapshot"] is True
        assert "table not present yet" in kept["lines"][-1]
        injected = bridge_log.tail_bridge_log(str(gd), "injector")
        assert injected["snapshot"] is True
        assert injected["lines"] == ["[boot] injected"]

# ========================================================================
# from tests/test_inject_retry.py
# ========================================================================

"""Tests for the Option 2 inject watchdog.

`inject_retry.py` is the policy (a pure function of plain values) and
`inject_watchdog.py` is the controller half that gathers live state and acts
on the verdict. Live 2026-09-29, Option 2 injected exactly once per session on
the player-located transition, so a single miss left the meter at
``need_inject`` with zero events through a whole boss fight — these tests pin
the retry that closes that hole, and the gate it must never bypass.
"""
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
PID = 4242
@pytest.fixture(scope="module", autouse=True)
def _qapp():
    """One offscreen QApplication for the module (the controller is a QObject
    and owns QTimers)."""
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
def _decide(**overrides):
    """A retry that WOULD fire, with one input swapped per test."""
    base = dict(attached=True, located=True, mode="injector", hook_enabled=True,
                capture_status="need_inject", in_fight=True, attempts=0,
                last_attempt_ts=0.0, now=1000.0)
    base.update(overrides)
    return retry_decision(**base)
def test_the_deaf_mid_fight_injector_is_the_case_that_retries():
    retry, reason = _decide()
    assert retry is True
    assert "need_inject" in reason and f"1/{RETRY_MAX}" in reason
def test_every_refusal_explains_itself():
    """A no is always paired with the reason, so "why did it not retry" is
    answerable from the Activity Log instead of a debugger."""
    for overrides, expected in (
        ({"attached": False}, "not attached"),
        ({"located": False}, "not located"),
        ({"mode": "memory"}, "not injector"),
        ({"mode": "proxy"}, "not injector"),
        ({"hook_enabled": False}, "disabled"),
        ({"capture_status": "live"}, "live"),
        ({"capture_status": "bridge_ready"}, "bridge_ready"),
        ({"capture_status": ""}, "unknown"),
        ({"in_fight": False}, "no boss/dummy fight"),
        ({"attempts": RETRY_MAX}, "budget spent"),
        ({"last_attempt_ts": 1000.0 - (RETRY_MIN_INTERVAL_S / 2)}, "too soon"),
    ):
        retry, reason = _decide(**overrides)
        assert retry is False, overrides
        assert expected in reason, (overrides, reason)
def test_the_located_gate_outranks_a_sick_capture():
    """Farever can have a process while its world is still booting; injecting
    then only makes the DLL wait for a runtime table that is not ready. The
    player-locate gate is checked before the capture status, so no amount of
    "deaf" can talk the watchdog past it."""
    for bad in ("need_inject", "inject_failed"):
        assert _decide(located=False, capture_status=bad)[0] is False
def test_retry_is_spaced_by_the_min_interval():
    too_soon = 1000.0 - (RETRY_MIN_INTERVAL_S - 0.1)
    assert _decide(last_attempt_ts=too_soon)[0] is False
    assert _decide(last_attempt_ts=1000.0 - RETRY_MIN_INTERVAL_S)[0] is True
def test_the_budget_is_refunded_once_the_capture_recovers():
    """Otherwise one bad stretch early in a session spends the watchdog for
    the rest of the run and a genuine later regression meets silence."""
    state = InjectRetryState()
    state.note_attempt(now=10.0)
    state.note_attempt(now=20.0)
    assert state.attempts == 2 and state.last_attempt_ts == 20.0
    state.observe(capture_status="live")
    assert state.attempts == 0 and state.last_attempt_ts == 0.0

    state.note_attempt(now=30.0)
    state.observe(capture_status="need_inject")     # not healthy: budget stays
    assert state.attempts == 1
    state.reset()                                   # what a detach calls
    assert state.attempts == 0 and state.last_attempt_ts == 0.0
def test_the_state_delegates_its_counters_to_the_policy():
    state = InjectRetryState()
    kw = dict(attached=True, located=True, mode="injector", hook_enabled=True,
              capture_status="inject_failed", in_fight=True, now=1000.0)
    assert state.decide(**kw)[0] is True
    for _ in range(RETRY_MAX):
        state.note_attempt(now=1000.0)
    assert state.decide(**kw)[0] is False
class _FakeDamage:
    def __init__(self, status: str):
        self._status = status

    def status(self) -> str:
        return self._status
class _FakeDps:
    def __init__(self, boss: bool, dummy: bool):
        self.in_boss_fight = boss
        self.in_dummy_fight = dummy
class _FakeModel:
    def __init__(self, status: str, boss: bool, dummy: bool):
        self.damage = _FakeDamage(status)
        self.dps = _FakeDps(boss, dummy)
class _FakeProc:
    pid = PID
def _watchdog(monkeypatch, *, status="need_inject", boss=True, dummy=False,
              located=True, mode="injector", stopped=False, hook_pending=False):
    """A controller wired for the watchdog only: fakes for the live session, and
    the subprocess spawn replaced by a recorder (the real one shells out)."""
    launches: list[bool] = []
    lines: list[str] = []
    monkeypatch.setattr(game_attach, "_try_launch_hook",
                        lambda pid, force=False, **kw: launches.append(force))

    settings = Settings()                # dps_mode defaults to "injector"
    settings.dps_mode = mode
    controller = GameAttachmentController(settings)
    controller.log.connect(lines.append)
    controller.proc = _FakeProc()
    controller.model = _FakeModel(status, boss, dummy)
    controller._located_shown = located
    controller._stopped = stopped
    controller._hook_pending = hook_pending
    return controller, launches, lines
def test_watchdog_reruns_the_injector_through_a_deaf_boss_fight(monkeypatch):
    controller, launches, lines = _watchdog(monkeypatch)
    controller._tick_inject_retry()
    assert launches == [True]        # forced past the launcher's per-PID dedupe
    assert controller._inject_retry.attempts == 1
    assert any("re-launching injector" in line and str(PID) in line
               for line in lines), lines
def test_watchdog_stays_put_without_a_real_encounter(monkeypatch):
    """Trash pulls and idle time decode nothing legitimately, so retrying there
    would re-inject the DLL all evening."""
    controller, launches, lines = _watchdog(monkeypatch, boss=False)
    controller._tick_inject_retry()
    assert launches == [] and lines == []
def test_watchdog_finds_a_fight_the_dummy_owns(monkeypatch):
    """A training-dummy pull is the deliberate test bench for the meter; a
    silent capture there is as much an anomaly as in a boss fight."""
    controller, launches, lines = _watchdog(monkeypatch, boss=False, dummy=True)
    controller._tick_inject_retry()
    assert launches == [True] and lines
def test_watchdog_respects_the_located_gate_and_a_healthy_capture(monkeypatch):
    controller, launches, lines = _watchdog(monkeypatch, located=False)
    controller._tick_inject_retry()
    assert launches == [] and lines == []
    assert controller._inject_retry.attempts == 0

    for status in ("live", "bridge_ready"):
        controller, launches, _lines = _watchdog(monkeypatch, status=status)
        controller._tick_inject_retry()
        assert launches == [], status
        assert controller._inject_retry.attempts == 0
def test_watchdog_is_idle_for_the_other_capture_modes(monkeypatch):
    for mode in ("memory", "proxy"):
        controller, launches, _lines = _watchdog(monkeypatch, mode=mode)
        controller._tick_inject_retry()
        assert launches == [], mode
def test_watchdog_stops_after_its_budget(monkeypatch):
    controller, launches, _lines = _watchdog(monkeypatch)
    for _ in range(RETRY_MAX + 2):
        controller._inject_retry.last_attempt_ts = 0.0     # ignore the spacing
        controller._tick_inject_retry()
    assert len(launches) == RETRY_MAX
    assert controller._inject_retry.attempts == RETRY_MAX
def test_watchdog_does_nothing_when_detached_or_stopped(monkeypatch):
    controller, launches, _lines = _watchdog(monkeypatch)
    controller.proc = None
    controller._tick_inject_retry()
    assert launches == []

    controller, launches, _lines = _watchdog(monkeypatch, stopped=True)
    controller._tick_inject_retry()
    assert launches == []
def test_watchdog_survives_a_model_torn_down_mid_tick(monkeypatch):
    """The timer fires while `detach()` is dropping the model, so a half-torn
    session must end the tick silently rather than raise into the Qt loop."""
    controller, launches, lines = _watchdog(monkeypatch)
    controller.model = None
    controller._tick_inject_retry()
    assert launches == [] and lines == []

    controller, launches, lines = _watchdog(monkeypatch)
    controller.model.damage.status = lambda: (_ for _ in ()).throw(RuntimeError("torn down"))
    controller._tick_inject_retry()
    assert launches == [] and lines == []
    assert controller._inject_retry.attempts == 0
def test_watchdog_spends_nothing_while_the_meter_is_healthy(monkeypatch):
    controller, _launches, _lines = _watchdog(monkeypatch, status="live")
    controller._inject_retry.note_attempt()
    controller._tick_inject_retry()
    assert controller._inject_retry.attempts == 0     # refunded, not just idle
def test_the_initial_inject_is_not_forced(monkeypatch):
    """The located transition is the FIRST attempt: it must go through the
    launcher's per-PID dedupe, so a re-locate cannot double-inject."""
    controller, launches, lines = _watchdog(monkeypatch, hook_pending=True)
    controller._on_located(0x1234)
    assert launches == [False]
    assert controller._inject_retry.attempts == 1
    assert any("launching injector" in line for line in lines), lines

# ========================================================================
# from tests/test_pe_imports.py
# ========================================================================

"""Which DLLs a game image imports — the rule that decides if a proxy loads.

A proxy DLL only works because Windows resolves a game-directory copy of a name
the game imports. For Farever the runtime-class names are DINPUT8 (directx.hdll)
and the libuv pair IPHLPAPI/USERENV (uv.hdll); version.dll needs optional
importers, which is why Option 1 with it sat there saying "the game has not
loaded it yet". winmm.dll used to be the boot-time answer until a t=0 attach
proved to kill the game; it is retired. These tests pin the reader that tells
the difference, plus the negative cases that must never be reported as "the
user's install is wrong".
"""
def _imports_of(blob: bytes) -> set[str]:
    return set(pe_imports.imported_dll_names(blob))
def test_static_imports_are_returned_lowercased():
    blob = build_pe(["KERNEL32.dll", "IPHLPAPI.dll", "WS2_32.dll"])
    assert _imports_of(blob) == {"kernel32.dll", "iphlpapi.dll", "ws2_32.dll"}
def test_delay_imports_count_too():
    """A delay-loaded DLL still loads, so it must not be missed."""
    blob = build_pe(["KERNEL32.dll"], delay_imports=["VERSION.dll"])
    assert _imports_of(blob) == {"kernel32.dll", "version.dll"}
def test_image_without_imports_is_empty_not_wrong():
    assert _imports_of(build_pe()) == set()
def test_non_pe_input_returns_empty_set():
    """Garbage must read as "unknown", never as "this proxy will not load"."""
    assert _imports_of(b"") == set()
    assert _imports_of(b"MZ" + b"\x00" * 400) == set()
    assert _imports_of(os.urandom(2048)) == set()
    assert pe_imports.pe_imports("does-not-exist.dll") == frozenset()
def test_game_imports_follows_the_games_own_dlls(tmp_path):
    """The dinput8 answer comes from directx.hdll, two hops past Farever.exe."""
    write_game(
        tmp_path,
        exe_imports=["libhl.dll", "KERNEL32.dll"],
        modules={"libhl.dll": ["WS2_32.dll", "USER32.dll"],
                 "directx.hdll": ["DINPUT8.dll"]},
    )
    assert "dinput8.dll" in pe_imports.game_imports(str(tmp_path))
    assert pe_imports.loadable_proxy_names(str(tmp_path)) == frozenset({"dinput8.dll"})
def test_only_modules_in_the_game_folder_are_read(tmp_path):
    """Only files actually shipped in the game folder are opened; a system DLL
    that exists solely in System32 contributes nothing (and is never read)."""
    write_game(tmp_path, exe_imports=["kernel32.dll", "libhl.dll"])
    # No libhl.dll on disk -> nothing else to read.
    assert pe_imports.game_imports(str(tmp_path)) == frozenset({"kernel32.dll", "libhl.dll"})
    assert pe_imports.loadable_proxy_names(str(tmp_path)) == frozenset()
def test_a_module_the_exe_never_imports_can_still_load_a_proxy(tmp_path):
    """Reading only Farever.exe and one hop gets the late names wrong.

    Nothing the exe loads imports the libuv pair - libhl.dll asks for nothing of
    ours - but uv.hdll in the same folder imports userenv.dll, so Windows does
    load our proxy, just a moment later, when the game's runtime comes up.
    (version.dll was the original subject of this test, with SDL3/DLSS/Streamline
    as its importers; it stopped shipping 2026-09-24 and is out of the shipped
    registry, so the map no longer reports it.)
    """
    write_game(
        tmp_path,
        exe_imports=["libhl.dll"],
        modules={"libhl.dll": [], "uv.hdll": ["USERENV.dll"]},
    )
    assert pe_imports.loadable_proxy_names(str(tmp_path)) == frozenset(
        {"userenv.dll"})

    importers = pe_imports.proxy_import_map(str(tmp_path))
    assert importers["userenv.dll"] == ["uv.hdll"]
    assert importers["dinput8.dll"] == []
def test_proxy_import_answers_are_cached_and_refresh(tmp_path):
    write_game(tmp_path, exe_imports=["libhl.dll"],
               modules={"directx.hdll": ["DINPUT8.dll"]})
    assert pe_imports.loadable_proxy_names(str(tmp_path)) == frozenset({"dinput8.dll"})

    # Rebuild directx.hdll to import userenv.dll instead: the cache key includes
    # mtime+size, so the answer must change without a process restart.
    path = os.path.join(str(tmp_path), "directx.hdll")
    with open(path, "wb") as f:
        f.write(build_pe(["USERENV.dll"]))
    os.utime(path, (1_700_000_000, 1_700_000_000))

    assert pe_imports.pe_imports(path) == frozenset({"userenv.dll"})
    assert pe_imports.loadable_proxy_names(str(tmp_path)) == frozenset({"userenv.dll"})
def test_hdll_modules_count_as_importers(tmp_path):
    """.hdll native libraries import system DLLs like anyone else.

    HashLink's own libraries carry the loaders: directx.hdll imports
    dinput8.dll, openal.hdll pulls in OpenAL32.dll (and through it WINMM),
    hlfmod.hdll pulls in FMOD. Skipping them hid real importers and made
    dinput8.dll look dead when directx.hdll imports it.
    """
    write_game(tmp_path, exe_imports=["libhl.dll"])
    write_module(tmp_path, "directx.hdll", imports=["dinput8.dll"])
    assert pe_imports.loadable_proxy_names(str(tmp_path)) == frozenset({"dinput8.dll"})
    assert pe_imports.proxy_import_map(str(tmp_path))["dinput8.dll"] == ["directx.hdll"]
def test_imported_function_names_are_read(tmp_path):
    p = write_module(tmp_path, "fmod.dll", imports=["WINMM.dll"],
                     functions={"winmm.dll": ["timeGetTime"]})
    assert pe_imports.pe_import_functions(p)["winmm.dll"] == frozenset({"timegettime"})
def test_imported_names_can_keep_their_case(tmp_path):
    """Generating an export list needs the game's own spelling: PE export
    lookup is case-sensitive, so a lowercased `timegettime` would not satisfy
    fmod.dll's import of `timeGetTime` - the crash this exists to prevent."""
    p = write_module(tmp_path, "fmod.dll", imports=["WINMM.dll"],
                     functions={"winmm.dll": ["timeGetTime", "waveOutOpen"]})
    kept = pe_imports.pe_import_functions(p, preserve_case=True)
    assert kept["winmm.dll"] == frozenset({"timeGetTime", "waveOutOpen"})
    # The DLL keys stay lowercased, because that is how every caller compares.
    assert "WINMM.dll" not in kept
def test_exported_names_are_read(tmp_path):
    p = write_module(tmp_path, "winmm.dll",
                     exports=["timeGetTime", "TimeBeginPeriod", "waveOutOpen"])
    assert pe_imports.exported_names(p) == frozenset(
        {"timegettime", "timebeginperiod", "waveoutopen"})
def test_export_gap_is_reported(tmp_path):
    """A proxy that does not export what the game imports is a crash.

    Windows fails the entire import on the first missing name, and the game
    dies inside the module that asked for it - that is how the retired winmm
    proxy killed the game at fmod.$Api.set_DEBUG_FLAGS ("Primitive or library
    is missing"): fmod.dll imported WINMM.timeGetTime and the proxy exported
    only the two timer functions libhl needs. This check is how that gets
    caught before a user sees it.
    """
    game = tmp_path / "game"
    bridge = tmp_path / "dps_bridge"
    write_game(game, exe_imports=["libhl.dll"],
               modules={"uv.hdll": ["USERENV.dll"]},
               functions={"uv.hdll": {"userenv.dll": ["GetUserProfileDirectoryW"]}})
    write_module(bridge, "userenv.dll", exports=[])

    gaps = pe_imports.proxy_export_gaps(str(game), str(bridge))
    assert gaps == {"userenv.dll": ["getuserprofiledirectoryw"]}

    # Exporting everything the game asks for clears the gap.
    write_module(bridge, "userenv.dll", exports=["GetUserProfileDirectoryW"])
    pe_imports.clear_cache()
    assert pe_imports.proxy_export_gaps(str(game), str(bridge)) == {}
def test_export_gap_ignores_ordinal_only_imports(tmp_path):
    """An ordinal import cannot be matched by name - reporting it as a gap
    would be a false alarm, which is worse than not checking it."""
    game = tmp_path / "game"
    bridge = tmp_path / "dps_bridge"
    write_game(game, exe_imports=["libhl.dll"], modules={"uv.hdll": ["USERENV.dll"]})
    write_module(bridge, "userenv.dll", exports=["GetUserProfileDirectoryW"])
    assert pe_imports.proxy_export_gaps(str(game), str(bridge)) == {}
def test_shipped_proxies_cover_the_installed_game(tmp_path):
    """The real thing: every function this install imports from a proxy name
    must be exported by the proxy we would copy in. Skipped without a game."""
    from farever_companion.core import proc

    try:
        info = proc.detect_combat_bridge(pid=0)
        game_dir = info.get("game_dir") or ""
    except Exception:
        game_dir = ""
    bridge_dir = os.path.join(os.path.dirname(__file__), "..", "dps_bridge")
    if not game_dir or not os.path.isdir(game_dir):
        pytest.skip("Farever install not found")
    if not any(os.path.isfile(os.path.join(bridge_dir, n))
               for n in pe_imports.PROXY_DLL_NAMES):
        pytest.skip("dps_bridge proxies not built")

    assert pe_imports.proxy_export_gaps(game_dir, bridge_dir) == {}
def _shipped_proxy_blobs() -> dict[str, bytes]:
    bridge_dir = os.path.join(os.path.dirname(__file__), "..", "dps_bridge")
    blobs: dict[str, bytes] = {}
    for name in pe_imports.PROXY_DLL_NAMES:
        path = os.path.join(bridge_dir, name)
        if os.path.isfile(path):
            with open(path, "rb") as f:
                blobs[name] = f.read()
    return blobs
def test_diagnostic_names_match_the_shipped_binaries():
    """Cleanup and the log reader find these files by name; the C sources compile
    the same names in (`PROXY_STEM` + suffix). If they ever drift, a leftover
    flag is never deleted (the bridge looks dead on every launch) or the
    diagnostics quote a file nobody writes.

    Only the names a bridge still *reads* are checked. `<stem>_proxy.log` was the
    third until file logging became dev-opt-in (2026-09-23): the companion drops
    a `FAREVERMOD_DEBUG_LOG` file naming the log path and a release build writes
    no log at all, so no shipped bridge carries that name any more - an
    unreferenced `static char[]` initialiser is dropped at `-O2`, and naming it
    from a log line would not make the build write it again. It stays a
    reader-side constant (`bridge_log.PROXY_LOG_FILES`), so a log an older build
    left behind is still found and quoted; that agreement is
    `test_diagnostics_reader_and_cleanup_agree_on_log_names`'s job.

    The names are checked as *prefixes* (`dinput8_grace`, not
    `dinput8_grace_ms.txt`): GNU ld merges string tails in an optimised build, so
    "dinput8_grace_ms.txt" can come out split around a shared suffix - its prefix
    cannot, which is what keeps this readable without running the game. Skipped
    while the shipped DLLs predate the files, so an un-rebuilt tree stays green;
    being out of date is test_shipped_proxies_cover_the_installed_game's job.
    """
    blobs = _shipped_proxy_blobs()
    if not blobs:
        pytest.skip("dps_bridge proxies not built")
    if not any(_name_heads(n, b) for n, b in blobs.items()):
        pytest.skip("dps_bridge/*.dll predate the diagnostic files - "
                    "run dps_bridge\\build_all.bat to refresh them")

    for name, blob in blobs.items():
        missing = [h for h in _name_heads(name, blob) if h.encode("ascii") not in blob]
        assert not missing, f"{name}: missing {missing}"
def _name_heads(name: str, blob: bytes) -> list[str]:
    """Merge-proof prefixes of the per-proxy file names one proxy answers to.

    Each stops at the word that distinguishes the file, so a renamed suffix
    (a different file altogether) still fails while tail-merging cannot hide it.
    No shipped proxy carries a `_go` name any more - the GO handshake retired
    with winmm - so none of these binaries may contain one.
    """
    stem = pe_imports._proxy_stem(name)
    # forward/grace are read by the bridge itself; the legacy log name is not
    # any more - see the caller's docstring.
    return [f"{stem}_forward_only", f"{stem}_grace"]
def test_proxy_go_name_derivation():
    """Derivation works for any name, but no shipped proxy enables the gate."""
    assert pe_imports.proxy_go_name("VERSION.DLL") == "version_go.flag"
    assert pe_imports.proxy_go_name("dinput8.dll") == "dinput8_go.flag"
    assert pe_imports.proxy_go_name("iphlpapi.dll") == "iphlpapi_go.flag"
def test_proxy_flag_name_derivation():
    assert pe_imports.proxy_flag_name("iphlpapi.dll") == "iphlpapi_forward_only.flag"
    assert pe_imports.proxy_flag_name("VERSION.DLL") == "version_forward_only.flag"
    assert pe_imports.proxy_flag_name("dinput8.dll") == "dinput8_forward_only.flag"
def test_proxy_grace_name_derivation():
    assert pe_imports.proxy_grace_name("userenv.dll") == "userenv_grace_ms.txt"
    assert pe_imports.proxy_grace_name("VERSION.DLL") == "version_grace_ms.txt"
    assert pe_imports.proxy_grace_name("dinput8.dll") == "dinput8_grace_ms.txt"
def test_proxy_log_name_derivation():
    assert pe_imports.proxy_log_name("iphlpapi.dll") == "iphlpapi_proxy.log"
    assert pe_imports.proxy_log_name("VERSION.DLL") == "version_proxy.log"
    assert pe_imports.proxy_log_name("dinput8.dll") == "dinput8_proxy.log"
def test_diagnostics_reader_and_cleanup_agree_on_log_names():
    """The reader quotes a log the cleanup can delete; both must derive the name
    from the same place, or the cleanup would miss the file being quoted."""
    from farever_companion.core import bridge_log

    assert bridge_log.PROXY_LOG_FILES == {
        n: pe_imports.proxy_log_name(n) for n in pe_imports.PROXY_DLL_NAMES}
def test_find_game_exe_is_case_insensitive(tmp_path):
    write_game(tmp_path, exe_imports=["libhl.dll"])
    assert pe_imports.find_game_exe(str(tmp_path)).endswith("Farever.exe")
    assert pe_imports.find_game_exe(str(tmp_path / "nope")) == ""
def test_proxy_load_stage_separates_boot_runtime_and_optional(tmp_path):
    """The situations behind "the game has not loaded it yet".

    dinput8 arrives when one of the game's own HashLink libraries
    (directx.hdll) loads - every launch, a moment later; the libuv pair rides
    with uv.hdll in the same class. version.dll needs an optional component
    (SDL3 / DLSS / Streamline), which a live session may never load at all.
    No shipped proxy is "boot" any more: winmm was the only t=0 name and it
    killed the game there, which is why it was retired.
    """
    write_game(tmp_path, exe_imports=["libhl.dll"],
               modules={"directx.hdll": ["DINPUT8.dll"],
                        "uv.hdll": ["USERENV.dll"]})

    stage = pe_imports.proxy_load_stage(str(tmp_path))

    assert stage["dinput8.dll"] == "runtime"
    assert stage["userenv.dll"] == "runtime"
    # version.dll kept this stage's only "optional" resident until it stopped
    # shipping (2026-09-24): its importers (SDL3/DLSS/Streamline) may never
    # load, which is exactly why it left the shipped registry. It is out of
    # the map now, so the stage dict has only names the picker can install.
    assert "version.dll" not in stage
def test_boot_import_closure_follows_static_imports_only(tmp_path):
    """The closure is made of *game-folder* modules the exe pulls in at start.

    A proxy name is boot-loaded only when one of its importers sits in this
    closure - which is what proxy_load_stage() checks. On the real install the
    closure is {farever.exe, libhl.dll, vcruntime140.dll}: winmm was the only
    name that ever qualified, and no shipped proxy does now.
    """
    write_game(tmp_path, exe_imports=["libhl.dll"],
               modules={"libhl.dll": [],
                        "SDL3.dll": ["VERSION.dll"],
                        "directx.hdll": ["DINPUT8.dll"]})

    closure = pe_imports.boot_import_closure(str(tmp_path))

    assert "libhl.dll" in closure
    assert "sdl3.dll" not in closure     # nothing statically loads these
    assert "directx.hdll" not in closure
def test_load_stage_covers_every_shipped_name(tmp_path):
    stage = pe_imports.proxy_load_stage(str(tmp_path / "not-a-game"))
    assert set(stage) == set(pe_imports.PROXY_DLL_NAMES)
def test_recommended_proxy_is_not_unverified():
    """Loading order is not the only question: the UI tags PROXY_RECOMMENDED
    "Recommended" and a fresh install starts on it, so it may not be a name
    whose own description admits an unresolved crash. dinput8 is verified
    streaming in-game and stays the recommendation."""
    assert pe_imports.PROXY_RECOMMENDED in pe_imports.PROXY_DLL_NAMES
    _when, why = pe_imports.PROXY_DLL_INFO[pe_imports.PROXY_RECOMMENDED]
    assert "crash" not in why.lower()
    assert pe_imports.PROXY_RECOMMENDED == "dinput8.dll"
    # The GO handshake retired with winmm: no shipped proxy reads a go flag.
    assert pe_imports.PROXY_GO_ENABLED == frozenset()
def test_recommended_proxy_matches_the_settings_default():
    """The picker's fallback, the combo's "Recommended" tag and the persisted
    default are the same name - three places that used to disagree (two fell
    back to version.dll, one to winmm.dll)."""
    from farever_companion.config import Settings
    assert Settings.dps_proxy_dll == pe_imports.PROXY_RECOMMENDED
    src = (pathlib.Path(__file__).resolve().parents[1]
           / "farever_companion" / "ui" / "pages" / "settings")
    # dps_modes.py is deliberately NOT listed: it stopped touching a proxy
    # default when its dead `_prompt_proxy_dll_copy` / `_on_dps_mode_selected`
    # handlers were removed, so asserting the token there would only be
    # satisfied by a dangling import. The two files below are the ones that
    # still choose a default and so are the ones that must agree with Settings.
    for name in ("dps_engine.py", "dps_proxy.py"):
        text = (src / name).read_text(encoding="utf-8", errors="ignore")
        assert "PROXY_RECOMMENDED" in text, f"{name} hardcodes a proxy default"
        assert '"version.dll") or "version.dll"' not in text

# ========================================================================
# from tests/test_proxy_exports.py
# ========================================================================

"""The generated proxy export lists.

A drop-in proxy must export every name the game imports from it, and Windows
fails the whole import on the first missing one - which is how the game died at
``fmod.$Api.set_DEBUG_FLAGS`` ("Primitive or library is missing") when
``fmod.dll`` once asked the (retired) winmm proxy for ``timeGetTime`` and the
proxy did not have it.

So the lists are generated from the installed game's import tables at build
time. These tests pin the two properties that make that safe:

* a symbol a patch newly imports is written with the game's **exact casing**
  (PE export lookup is case-sensitive, and ``timegettime`` would not satisfy the
  import of ``timeGetTime`` - the same crash, generated by us);
* regeneration only ever adds: a name already exported stays.
"""
IPHLPAPI_SOURCE = """
__declspec(dllexport) DWORD WINAPI ConvertInterfaceIndexToLuid(void *a1, void *a2) {
    return 0;
}
"""
def test_the_build_registry_covers_the_shipped_names_and_the_parked_ones():
    """The proxy registry exists twice, on purpose: `pe_imports.PROXY_DLL_NAMES`
    drives the UI, the install and the cleanup - the names we SHIP - while
    `proxy_exports.PROXY_NAMES` drives which `.def` files the build regenerates
    - the names the C code can still BECOME. A name retired from one but not
    the other is precisely how `iphlpapi.dll` kept being compiled (and kept
    failing, its `.def` long deleted) for a day after it stopped shipping.

    Since 2026-09-29 the two sets are identical. version.dll was parked here
    on 2026-09-24 (kept building, not shipped) and then fully retired, so it
    now appears in neither - the build stopped regenerating its .def and
    forwarders, and version.def survives on disk as a backup only. The build
    set must always be a SUPERSET of the shipped set - the reverse is the
    failure this test exists for: shipping a name the build no longer
    regenerates."""
    from farever_companion.core import pe_imports

    shipped = set(pe_imports.PROXY_DLL_NAMES)
    built = set(proxy_exports.PROXY_NAMES)
    assert shipped <= built, f"shipped but not built: {sorted(shipped - built)}"
    # ...and every shipped name needs the forwarder target its generated
    # forwarders call, or a rebuild emits code that does not compile.
    assert set(proxy_exports.REAL_PROC_FN) == built
    # version.dll is retired from BOTH registries: nothing regenerates it and
    # nothing ships it. Asserted by name because re-adding it to one list and
    # not the other is exactly the drift this test exists to catch - and
    # re-adding it to PROXY_NAMES alone is a KeyError in the generator.
    assert "version.dll" not in built
    assert "version.dll" not in shipped
def _game(tmp_path, functions):
    """A synthetic install: Farever.exe -> libhl.dll, uv.hdll -> USERENV.dll."""
    game = tmp_path / "game"
    write_game(game, exe_imports=["libhl.dll"],
               modules={"libhl.dll": [], "uv.hdll": ["USERENV.dll"]},
               functions={"uv.hdll": {"userenv.dll": ["GetUserProfileDirectoryW"]},
                          **functions})
    return game
def test_import_names_keep_the_games_casing(tmp_path):
    """Lowercasing these would generate a real crash, so they must not be."""
    game = _game(tmp_path, {"sdl3.dll": {"userenv.dll": ["GetAdaptersAddresses"]}})
    write_module(game, "sdl3.dll", imports=["USERENV.dll"],
                 functions={"userenv.dll": ["GetAdaptersAddresses"]})

    assert proxy_exports.imported_function_names(str(game), "userenv.dll") == {
        "GetUserProfileDirectoryW", "GetAdaptersAddresses"}
def test_importer_notes_are_keyed_by_the_games_spelling(tmp_path):
    game = _game(tmp_path, {"sdl3.dll": {"userenv.dll": ["GetAdaptersAddresses"]}})
    write_module(game, "sdl3.dll", imports=["USERENV.dll"],
                 functions={"userenv.dll": ["GetAdaptersAddresses"]})

    notes = proxy_exports.importer_notes(str(game), "userenv.dll")
    assert notes["GetAdaptersAddresses"] == ["sdl3.dll"]
    assert "getadaptersaddresses" not in notes
def test_a_newly_imported_symbol_is_exported_with_the_games_casing(tmp_path):
    """The patch scenario: one more symbol appears in the game's import table.

    The proxy is not rebuilt by hand any more - the generator must both list the
    name and generate the forwarder under that exact spelling, or the build
    would produce a DLL that still cannot satisfy the import.
    """
    game = _game(tmp_path, {"sdl3.dll": {"userenv.dll": ["GetAdaptersAddresses"]}})
    write_module(game, "sdl3.dll", imports=["USERENV.dll"],
                 functions={"userenv.dll": ["GetAdaptersAddresses"]})

    imported = proxy_exports.imported_function_names(str(game), "userenv.dll")
    existing, _private = proxy_exports.parse_def("LIBRARY userenv\nEXPORTS\n"
                                                 "    GetUserProfileDirectoryW\n")
    names = proxy_exports.merge_export_names(existing, imported)
    assert names == ["GetUserProfileDirectoryW", "GetAdaptersAddresses"]

    defined = proxy_exports.defined_exports(
        _source(tmp_path, IPHLPAPI_SOURCE))       # unrelated name only
    missing = proxy_exports.missing_forwarders(names, defined)
    assert missing == ["GetUserProfileDirectoryW", "GetAdaptersAddresses"]

    def_text = proxy_exports.render_def("userenv.dll", names, imported=imported,
                                        notes=proxy_exports.importer_notes(
                                            str(game), "userenv.dll"),
                                        generated=set(missing))
    assert f"    GetAdaptersAddresses = {proxy_exports.generated_impl_name('GetAdaptersAddresses')}\n" in def_text
    assert "getadaptersaddresses" not in def_text
    # The note names the module whose import pulled the name in.
    assert "; GetAdaptersAddresses - needed by sdl3.dll" in def_text

    header = proxy_exports.render_forwarders("userenv.dll", missing)
    # Same spelling as the .def alias target, so the export has an implementation.
    assert f"WINAPI {proxy_exports.generated_impl_name('GetAdaptersAddresses')}(" in header
    assert "getadaptersaddresses" not in header
def test_generated_header_is_valid_c():
    """It is included by a .c file, so ``;`` comments would be a build break.

    The first version of this wrote .def-style ``;`` comment lines into the
    header; it only compiled as long as nothing was missing, and the moment a
    patch added an import the proxy would stop building.
    """
    header = proxy_exports.render_forwarders("userenv.dll", ["CreateEnvironmentBlock"])
    for line in header.splitlines():
        stripped = line.strip()
        assert not stripped.startswith(";"), f"comment not C-legal: {line!r}"
    assert header.count("/*") == header.count("*/")
def test_generated_forwarder_is_not_defined_under_its_public_name():
    """A generic 12-slot forwarder cannot carry the public name: the SDK
    headers declare the real prototype and redefining it fails the build.
    So the public name is attached by a .def alias instead."""
    header = proxy_exports.render_forwarders("userenv.dll", ["CreateEnvironmentBlock"])
    impl = proxy_exports.generated_impl_name("CreateEnvironmentBlock")
    assert f"WINAPI {impl}(" in header
    assert "WINAPI CreateEnvironmentBlock(" not in header

    text = proxy_exports.render_def("userenv.dll", ["CreateEnvironmentBlock"],
                                   imported={"CreateEnvironmentBlock"},
                                   generated={"CreateEnvironmentBlock"})
    assert f"    CreateEnvironmentBlock = {impl}\n" in text
    # A plain `name` line would export a symbol with no implementation: the
    # link fails instead of shipping a DLL that breaks the game's import.
    assert "\n    CreateEnvironmentBlock\n" not in text
def test_hand_written_exports_are_not_aliased():
    text = proxy_exports.render_def("userenv.dll", ["GetUserProfileDirectoryW"],
                                   imported={"GetUserProfileDirectoryW"})
    assert "\n    GetUserProfileDirectoryW\n" in text
def test_failure_value_lookup_ignores_case():
    assert proxy_exports.failure_value("GetAdaptersAddresses") == 0
    assert proxy_exports.failure_value("getadaptersaddresses") == 0
    assert proxy_exports.failure_value("SomeOtherApi") == 0
def test_regeneration_never_drops_an_existing_export(tmp_path):
    """A name this game build does not import must survive: another consumer
    (or an older build) may still call it."""
    game = _game(tmp_path, {})
    names = proxy_exports.merge_export_names(
        ["GetUserProfileDirectoryW", "AnOldExport"], {"GetAdaptersAddresses"})
    assert names == ["GetUserProfileDirectoryW", "AnOldExport", "GetAdaptersAddresses"]
def test_existing_spelling_wins_over_the_games(tmp_path):
    """The .def already on disk decides spelling - that is what a rebuild of an
    unchanged game produces, so the file stays byte-identical (no churn)."""
    assert proxy_exports.merge_export_names(["GetUserProfileDirectoryW"],
                                            {"getuserprofiledirectoryw"}) == [
        "GetUserProfileDirectoryW"]
def test_def_notes_follow_a_differently_cased_export(tmp_path):
    """An existing .def entry spelled differently from the game's import still
    gets its importer comment - otherwise the list looks unexplained."""
    text = proxy_exports.render_def(
        "userenv.dll", ["GetAdaptersAddresses"], imported={"getadaptersaddresses"},
        notes={"GetAdaptersAddresses": ["uv.hdll"]})
    assert "; GetAdaptersAddresses - needed by uv.hdll" in text
def test_kept_exports_are_labelled_when_the_game_stops_importing_them():
    text = proxy_exports.render_def("userenv.dll", ["GetUserProfileDirectoryW"],
                                    imported=set())
    assert "; GetUserProfileDirectoryW - kept from the previous export list" in text
def test_ordinal_imports_are_not_guessed():
    """`#123` cannot be written into a .def; inventing a name would be worse."""
    assert proxy_exports.merge_export_names([], {"#123", "GetAdaptersAddresses"}) == ["GetAdaptersAddresses"]
def test_forwarders_reject_an_unknown_proxy_name():
    with pytest.raises(ValueError):
        proxy_exports.render_forwarders("nosuch.dll", ["SomeExport"])
def _source(tmp_path, text):
    path = tmp_path / "version_proxy.c"
    path.write_text(text, encoding="utf-8")
    return str(path)

# ========================================================================
# from tests/test_compiled_fields.py
# ========================================================================

"""A compiled shim must keep every field the data layer reads.

The compiler cherry-picks fields per sheet (`compiler.py`'s `sheets` table):
anything it does not name is dropped silently, and `cdb.sheet()` then falls
back to the loose JSON — so a trimmed field can keep working in a dev checkout
while the frozen build (which bundles the shims, not the source sheets) loses
it. That is how item `props` went missing and the consumable durations had to
be re-read source-first.

`build_tools/compiled_fields.py` measures the real dependency instead of
trusting a list: it wraps each shim's rows to record what the data layer ASKS
for, exercises the data layer, and reports fields that no row of the payload
carries. These tests pin the classification rules and the live result.
"""
def test_no_compiled_shim_drops_a_field_the_data_layer_reads():
    """The live check: fail with the offending reads named, not just a count."""
    result = CF.sweep()
    detail = "\n".join(
        f"  {shim}.{payload}['{field}'] ({reads} read(s)) <- {caller}"
        for shim, payload, field, reads, caller, _ in result["violations"])
    assert not result["violations"], (
        "the compiler drops these fields the data layer reads:\n" + detail)
    # ...and prove the measurement actually ran (a checker that silently
    # stopped tracking would otherwise pass trivially).
    assert len(result["before"]) >= 8, "no compiled shims were inspected"
    assert len(result["misses"]) >= 10, "the data layer was barely exercised"
def test_classification_is_not_vacuous():
    """Trimmed vs excused vs by-design fallback, on synthetic readings."""
    before = {"raw_items": {"items": {"id", "name", "rarity", "type"}},
              "raw_units": {"units": {"id", "lvl", "type"}},
              "raw_data": {"zone": {"id", "name"}}}
    misses = {("raw_items", "items", "aptitudes"): 1185,   # payload-wide, in source
              ("raw_items", "items", "statusRefs"): 1126,   # payload-wide, not in source
              ("raw_units", "units", "lvl"): 149,           # present on other rows
              ("raw_data", "zone", "texts"): 1}             # explicitly excused
    callers = {k: "data/x.py:1" for k in misses}
    in_source = {"raw_items": {"aptitudes", "type"}, "raw_data": {"texts"}}
    excused_key = {("raw_data", "zone", "texts"): "baked `name` covers it"}

    viol, excused, notes = CF.triage(misses, before, callers, in_source, excused_key)
    assert [v[2] for v in viol] == ["aptitudes"]
    assert [e[2] for e in excused] == ["texts"]
    assert [n[2] for n in notes] == ["statusRefs"]
    assert all(v[5] for v in viol), "a violation must report the input still has it"
def test_live_exceptions_are_documented_and_still_real():
    """Every tolerated absence needs a reason — and must still be observed, so
    fixing the underlying gap forces its exception to be deleted."""
    result = CF.sweep()
    observed = {(s, p, f) for s, p, f, *_ in result["excused"]}
    assert set(CF.EXCEPTIONS) == observed, (
        f"stale or unexplained exceptions: "
        f"declared-only={sorted(set(CF.EXCEPTIONS) - observed)}, "
        f"observed-only={sorted(observed - set(CF.EXCEPTIONS))}")
    for key, reason in CF.EXCEPTIONS.items():
        assert len(reason) > 60, f"{key} needs a real reason, got: {reason!r}"
def test_build_check_fails_on_a_violation(monkeypatch):
    """verify_assets.py fails the build: a non-empty run() must be non-zero."""
    from build_tools import check_compiled_fields as CLI

    monkeypatch.setattr(CLI.compiled_fields, "sweep", lambda: {
        "misses": {}, "before": {}, "callers": {}, "excused": [], "notes": [],
        "violations": [("raw_items", "items", "aptitudes", 1185, "data/classes.py:32", True)],
    })
    count, rows = CLI.run()
    assert count == 1 and rows[0][2] == "aptitudes"
    assert CLI.main([]) == 1
    monkeypatch.setattr(CLI.compiled_fields, "sweep", lambda: {
        "misses": {}, "before": {}, "callers": {}, "excused": [], "notes": [],
        "violations": [],
    })
    assert CLI.main([]) == 0
