"""Combat-bridge DLL classification: FareverPal's own proxy vs 3rd-party mods.

Pins the DPS-settings "3rd party <name>.dll in the folder" detection:

  * is_farever_proxy() must classify ONLY by the reference checksums (hashes
    of the reference DLLs in dps_bridge/, derived at runtime — no manifest
    file). A 3rd-party DLL that happens to embed the same localhost/UDP
    telemetry string as FareverPal's proxy (127.0.0.1:49152) must NOT be
    accepted — that signature fallback is only a crutch for builds with no
    reference DLLs at all.
  * detect_combat_bridge() must surface 3rd-party dinput8.dll / version.dll
    found in the game folder, even when FareverPal's own proxy sits alongside.
"""
from __future__ import annotations

import os
import shutil

import pytest

from farever_companion.core import proc

# A 3rd-party-style stub: real PE-ish header + the port string that the old
# signature check latched onto (hash deliberately NOT in the manifest).
_FAKE_3RD_PARTY = b"MZ" + b"\x00" * 64 + b"127.0.0.1:49152" + b"\x00" * 64

_REF_DIR = os.path.join(os.path.dirname(__file__), "..", "dps_bridge")
_REF_DLLS = ("version.dll", "dinput8.dll", "farever_dps.dll")


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
    p = _write_dll(str(tmp_path), "dinput8.dll", _FAKE_3RD_PARTY)
    assert proc.get_reference_checksums(), "reference DLLs must exist for this test"
    assert proc.is_farever_proxy(p) is False


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