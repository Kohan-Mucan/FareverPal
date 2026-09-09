"""test_lifecycle - consolidated subsystem tests.

Merged without changing any test, from:
  * tests/test_instance_gate.py
  * tests/test_instance_hardening.py
  * tests/test_instance_lock.py
  * tests/test_reentry.py
  * tests/test_workers.py

Imports are deduped and the shared `_qapp` fixture kept once;
where two source files bound one name differently, the later
was renamed `<name>__<file>` and only its own file's references
follow. See AGENTS.md: the suite is the spec.
"""
from __future__ import annotations


# --- shared imports ---
import os
from types import SimpleNamespace
from farever_companion.core.scene import Scene
from farever_companion.ui.overlay_manager import _instance_gate_line
import struct
import time
from farever_companion.core.model import LiveModel
from farever_companion.constants import (
    OFF_FOE_TARGET_OBJ, OFF_FOE_TARGET_HERO, OFF_FOE_TARGET_DIRECT,
    OFF_FOE_RECENT_HATE, FOE_TARGET_HOLD_S,
)
import pytest
from farever_companion.constants import OFF_MAIN_ACTIVITY, OFF_RIFT_BOOL
from farever_companion.core.proc import ProcError
from farever_companion.core.scene import Scene, _class_words
import sys
from PySide6 import QtCore, QtWidgets  # noqa: E402
from farever_companion import app as app_mod  # noqa: E402
from farever_companion.config import config_dir  # noqa: E402
from farever_companion.runtime import reentry  # noqa: E402
import gc
import threading
import weakref
from farever_companion.ui import workers  # noqa: E402

# ========================================================================
# from tests/test_instance_gate.py
# ========================================================================

"""Gameplay logic: Dungeon HUD instance gate and boss aggro latch."""
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
class _Hl:
    #: The stubbed GameLayer's base address (see `_scene(gamelayer=...)`).
    GAMELAYER = 0x1000

    def __init__(self, activity=0, chain=(), field_offsets=None,
                 type_ptr=0x00000300):
        self.activity = activity
        self.chain = list(chain)
        # Lets a test say "this field name resolves at this offset" - the
        # difference between a slot that was FOUND and a name that is gone.
        self.field_offsets = dict(field_offsets or {})
        self.type_ptr = type_ptr

    def ptr(self, addr):
        # The GameLayer base reads as a type pointer; anything else reads the
        # activity slot. Without that split, `activity=0` to model an EMPTY
        # mainActivity also makes the GameLayer unreadable, and reflection
        # never runs - so the "slot found but empty" case cannot be built.
        return self.type_ptr if addr == self.GAMELAYER else self.activity

    def class_of(self, instance):
        return self.chain[0] if self.chain else None

    def super_chain(self, instance, limit=16):
        return list(self.chain)

    def field_offset(self, type_ptr, name):
        return self.field_offsets.get(name)

    def type_name(self, type_ptr):
        return "st.Config" if type_ptr else None
class _Proc:
    def __init__(self, rift_byte=None):
        self.rift_byte = rift_byte

    def try_read(self, addr, n):
        return None if self.rift_byte is None else bytes([self.rift_byte])
def _scene(*, activity=0x0000014000005000, chain=(), rift_byte=0, map_id="POI_Forest_Z1",
           activity_id=None, gamelayer=0x1000, cfg_off=None,
           in_dungeon=False, in_rift=False, field_offsets=None) -> Scene:
    """A Scene with its reads stubbed (no process, no memory)."""
    s = Scene.__new__(Scene)
    s.proc = _Proc(rift_byte)
    s.hl = _Hl(activity, chain, field_offsets)
    s._cfg_off = cfg_off
    s._cfg_type = None
    s._cfg_slot_cache = {}
    s._act_off = None
    s._act_via = None
    s._act_scan_at = 0.0
    s.gamelayer = lambda pbase: gamelayer
    s.map_id = lambda pbase: map_id
    s.activity_id = lambda pbase: activity_id
    s.in_dungeon = lambda pbase: in_dungeon
    s.is_rift = lambda pbase: in_rift
    return s
def _state(in_dungeon=False, in_rift=False):
    return SimpleNamespace(in_dungeon=in_dungeon, in_rift=in_rift,
                           in_instance=(in_dungeon or in_rift))
def test_probe_reports_the_activity_class_and_its_super_chain():
    s = _scene(chain=["st.activity.Dungeon", "st.activity.Activity", "hl.Class"],
               rift_byte=0, cfg_off=0xb0)
    p = s.instance_probe(0x1234)
    assert p["activity_class"] == "st.activity.Dungeon"
    assert p["activity_chain"][1] == "st.activity.Activity"
    assert p["map_id"] == "POI_Forest_Z1"
    assert p["rift_byte"] == 0
    assert p["cfg_off"] == 0xb0
def test_probe_reports_a_renamed_activity_as_still_instance_like():
    """A game update that renames the activity class used to shut the gate and
    hide the Dungeon HUD. The word-token match now reads `DungeonInstance` as a
    dungeon, and the probe still names the class and slot it found.

    (``in_dungeon`` here is the stubbed fold-in, not this layer's verdict.)"""
    s = _scene(chain=["st.activity.DungeonInstance", "st.activity.Activity"])
    p = s.instance_probe(0x1234)
    assert p["activity_class"] == "st.activity.DungeonInstance"
    assert "st.activity.Dungeon" not in p["activity_chain"]
    assert p["activity_kind"] == "dungeon"
    assert p["activity_off"] is not None
    assert p["activity_via"] == "constant"
def test_probe_handles_an_unreadable_gamelayer():
    s = _scene(gamelayer=None, map_id=None)
    p = s.instance_probe(0x1234)
    assert p["gamelayer"] is None
    assert p["activity_class"] is None
    assert p["in_dungeon"] is False and p["in_rift"] is False
def test_probe_survives_a_dead_rift_byte():
    s = _scene(rift_byte=None)
    assert s.instance_probe(0x1234)["rift_byte"] is None
def _model(scene):
    return SimpleNamespace(player_addr=0x1234, scene=scene)
def test_gate_line_names_the_reason_when_closed():
    s = _scene(chain=["st.activity.DungeonInstance", "st.activity.Activity"],
               rift_byte=0)
    line = _instance_gate_line(_model(s), _state())
    assert "Dungeon gate closed" in line
    assert "stays hidden" in line
    assert "DungeonInstance" in line          # the class it actually found
    assert "POI_Forest_Z1" in line
    assert "rift_byte=0" in line
def test_gate_line_says_when_the_activity_slot_is_empty():
    s = _scene(gamelayer=None, map_id=None)
    line = _instance_gate_line(_model(s), _state())
    assert "mainActivity slot empty" in line
def test_an_empty_activity_slot_is_not_reported_as_a_moved_offset():
    """A NULL slot is the overworld; only an unresolvable one is a moved offset.

    Both returned None, so the gate said "offset moved?" on every walk outside
    an instance - and that is what sent a reader looking for a drift that did
    not exist. Verified live 2026-10-01: st.GameLayer.mainActivity is still at
    0xf0, exactly what OFF_MAIN_ACTIVITY says, and the pointer was simply NULL.
    """
    s = _scene(activity=0, field_offsets={"mainActivity": 0xF0})
    p = s.instance_probe(0x1234)
    assert p["gamelayer"], "the GameLayer itself resolved - this is not that case"
    assert p["activity_off"] is None, "an empty slot has no activity to point at"
    assert p["activity_via"] == "empty", (
        "the slot was resolved by name; saying so is what tells the diagnostic "
        "this is an empty slot rather than a moved one")

    line = _instance_gate_line(_model(s), _state())
    assert "no activity" in line
    assert "offset moved" not in line, (
        "an empty slot is the overworld, and claiming the offset moved sends "
        "the reader after a drift that is not there")
def test_gate_line_names_the_config_slot_and_its_declared_type():
    """The config slot drifted on the 2026-09-19 build (map_id/difficulty went
    dead); the line has to say which slot resolved and as what."""
    s = _scene(chain=["st.activity.Dungeon"], map_id="POI_Crypt_Z1", cfg_off=0xd0)
    s._cfg_type = 0x5000
    line = _instance_gate_line(_model(s), _state(in_dungeon=True))
    assert "config st.Config @ 0xd0" in line

    s2 = _scene(chain=["st.activity.DungeonInstance"], map_id="POI_Forest_Z1")
    closed = _instance_gate_line(_model(s2), _state())
    assert "config no type @ not found" in closed
def test_gate_line_reports_an_open_dungeon_and_rift():
    s = _scene(chain=["st.activity.Dungeon"], map_id="POI_Crypt_Z1")
    line = _instance_gate_line(_model(s), _state(in_dungeon=True))
    assert "Dungeon gate OPEN (dungeon)" in line
    assert "POI_Crypt_Z1" in line

    s2 = _scene(chain=["st.activity.Rift"], map_id="Rift_Fire")
    line2 = _instance_gate_line(_model(s2), _state(in_rift=True))
    assert "Dungeon gate OPEN (rift)" in line2
def test_gate_line_never_raises_on_a_broken_probe():
    class _Boom:
        player_addr = 0x1

        @property
        def scene(self):
            raise RuntimeError("detached")

    assert "Dungeon gate closed" in _instance_gate_line(_Boom(), _state())
def _hero(addr: int, uid: str):
    return SimpleNamespace(addr=addr, unit_id=uid, is_hero=True, is_foe=False)
def _foe(addr: int):
    return SimpleNamespace(addr=addr, unit_id="Boss", is_hero=False,
                           is_foe=True)
class _FakeHL:
    """hl.ptr over a canned pointer map."""

    def __init__(self, ptrs: dict[int, int]):
        self._ptrs = ptrs

    def ptr(self, addr: int) -> int:
        return self._ptrs.get(addr, 0)
class _FakeProc:
    """proc.read over canned qword blocks: slot 8,16,24... hold the hero addrs."""

    def __init__(self):
        self._blocks: dict[int, list[int]] = {}

    def read(self, addr: int, n: int) -> bytes:
        buf = bytearray(n)
        for i, q in enumerate(self._blocks.get(addr, [])):
            off = 8 + 8 * i
            if off + 8 <= n:
                buf[off:off + 8] = struct.pack("<Q", q)
        return bytes(buf)
class _Model:
    """A real LiveModel instance without __init__ (no Proc needed) + a stub
    memory surface. `units()`/`player_name()` are patched per test."""

    def __init__(self, units, main_hero, aggro_qwords=(), direct_hero=0,
                 hate_qwords=()):
        self._units = units
        self._main = main_hero
        self._direct = direct_hero
        self._hl = _FakeHL({})
        self._proc = _FakeProc()
        foe = next(u for u in units if not u.is_hero)
        self._hl._ptrs[foe.addr + OFF_FOE_TARGET_OBJ] = 0x400
        self._hl._ptrs[0x400 + OFF_FOE_TARGET_HERO] = main_hero
        self._hl._ptrs[foe.addr + OFF_FOE_TARGET_DIRECT] = direct_hero
        # the MAIN aggro obj carries the hate table too (sweep reads it)
        self._proc._blocks[0x400] = list(aggro_qwords)
        for base, qs in ((OFF_FOE_RECENT_HATE, hate_qwords),):
            obj = 0x500
            self._hl._ptrs[foe.addr + base] = obj
            self._proc._blocks[obj] = list(qs)
        self._live = object.__new__(LiveModel)
        self._live.units = lambda: self._units
        self._live.player_name = lambda addr: \
            next((u.unit_id for u in self._units
                  if u.is_hero and u.addr == addr), None)
        self._live._hero_player_ptr = lambda addr: 0
        self._live._player_name_at = lambda p: None
        self._live.hl = self._hl
        self._live.proc = self._proc

    def line(self, foe):
        return self._live.foe_target_line(foe)
def _boss_units(main_addr: int):
    a, b = _hero(0x100, "Tank"), _hero(0x200, "Healer")
    foe = _foe(0x300)
    return [a, b, foe]
def test_follows_live_retarget_immediately(monkeypatch):
    """A genuine retarget shows at once — no debounce, no stale hold."""
    clock = [1000.0]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    units = _boss_units(0x100)
    m = _Model(units, main_hero=0x100, aggro_qwords=[0x100])
    foe = units[-1]
    assert m.line(foe) == "◎ Tank"
    # boss switches to Healer -> shown on this very tick
    m._main = 0x200
    m._hl._ptrs[0x400 + OFF_FOE_TARGET_HERO] = 0x200
    m._proc._blocks[0x400] = [0x200]
    assert m.line(foe) == "◎ Healer"
def test_never_blanks_when_main_slot_nulls(monkeypatch):
    """The row must not flash blank between swings: if MAIN nulls on a tick
    but the hate table still names a target, that target shows live."""
    clock = [1000.0]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    units = _boss_units(0x100)
    m = _Model(units, main_hero=0x100, aggro_qwords=[0x100])
    foe = units[-1]
    assert m.line(foe) == "◎ Tank"
    # game nulls MAIN between swings; the hate table still holds Tank
    m._main = 0
    m._hl._ptrs[0x400 + OFF_FOE_TARGET_HERO] = 0
    assert m.line(foe) == "◎ Tank"
def test_never_blanks_fallback_on_empty_tick(monkeypatch):
    """Even a fully empty tick (MAIN null, no hate) briefly keeps the last
    confirmed name within the hold window, then clears."""
    clock = [1000.0]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    units = _boss_units(0x100)
    m = _Model(units, main_hero=0x100, aggro_qwords=[0x100])
    foe = units[-1]
    assert m.line(foe) == "◎ Tank"
    # genuinely nothing this tick (MAIN null, no hate entries)
    m._main = 0
    m._hl._ptrs[0x400 + OFF_FOE_TARGET_HERO] = 0
    m._proc._blocks[0x400] = []
    assert m.line(foe) == "◎ Tank"          # fallback within hold window
    clock[0] += FOE_TARGET_HOLD_S + 0.1
    assert m.line(foe) is None              # stale -> cleared
def test_second_threat_shows_lightning(monkeypatch):
    """Top hate entry that differs from the displayed target shows as ⚡."""
    clock = [1000.0]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    units = _boss_units(0x100)
    m = _Model(units, main_hero=0x100, aggro_qwords=[0x100, 0x200])
    foe = units[-1]
    # Tank at slot 8, Healer at slot 16 -> Healer is top hate, not the target
    # (the ⚡ suffix has no space, matching the row format)
    assert m.line(foe) == "◎ Tank · ⚡Healer"
def test_hate_fallback_path_shows_live_target(monkeypatch):
    """When MAIN is empty but the recent-hate object names a hero, that hero
    is the live target (fallback sweep must feed the display)."""
    clock = [1000.0]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    units = _boss_units(0x200)
    m = _Model(units, main_hero=0, aggro_qwords=[],
               hate_qwords=[0x200])
    foe = units[-1]
    assert m.line(foe) == "◎ Healer"

# ========================================================================
# from tests/test_instance_hardening.py
# ========================================================================

"""Instance-gate hardening: renamed activity classes and moved offsets.

The Dungeon HUD's existence is gated by one boolean: ``Scene.instance_flags``.
When that gate silently returns False the HUD simply never appears, so it has to
survive a game update that renames an activity class or moves the GameLayer
member holding it. These tests pin the two properties that make it survive:

  * matching is by word token over every dotted segment, so a renamed
    ``st.activity.Dungeon``-derived class still reads as a dungeon, and
  * the activity slot itself is resolved by reflection -> constant -> bounded
    scan, so a moved ``OFF_MAIN_ACTIVITY`` no longer downgrades the gate.
"""
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
BASE = 0x0000014000000000
GL = BASE + 0x1000          # the game layer
GL_TYPE = BASE + 0x900      # st.GameLayer's class object
ACT = BASE + 0x2000         # the activity object
class _GateHl:
    """hl stub: address -> pointer, instance -> class chain, field -> offset."""

    def __init__(self, ptrs=None, chains=None, fields=None):
        self.ptrs = dict(ptrs or {})
        self.chains = dict(chains or {})
        self.fields = dict(fields or {})

    def ptr(self, addr):
        return self.ptrs.get(addr, 0)

    def class_of(self, instance):
        return (self.chains.get(instance) or [None])[0]

    def super_chain(self, instance, limit=16):
        return list(self.chains.get(instance, []))

    def field_offset(self, type_ptr, name):
        return self.fields.get((type_ptr, name))
class _GateProc:
    """proc stub: single bytes by address, plus canned header blocks."""

    def __init__(self, bytes_=None, blocks=None):
        self.bytes = dict(bytes_ or {})
        self.blocks = dict(blocks or {})
        self.reads: list[tuple[int, int]] = []

    def try_read(self, addr, n):
        self.reads.append((addr, n))
        if addr in self.blocks:
            blk = self.blocks[addr]
            return blk[:n] if n else blk
        if addr in self.bytes:
            return bytes([self.bytes[addr]])[:n]
        return None

    def header_reads(self):
        """Reads of the whole GameLayer header window (the slot scan)."""
        return [r for r in self.reads if r[1] == Scene.ACTIVITY_SCAN_BYTES]
class _BoomHl:
    """A detached process: every read raises."""

    def ptr(self, addr):
        raise ProcError("detached")

    def class_of(self, instance):
        raise ProcError("detached")

    def super_chain(self, instance, limit=16):
        raise ProcError("detached")

    def field_offset(self, type_ptr, name):
        raise ProcError("detached")
def _gate_scene(hl, proc, cfg_off=None) -> Scene:
    """A Scene with no process, only the reads the instance gate makes."""
    s = Scene.__new__(Scene)
    s.proc = proc
    s.hl = hl
    s._cfg_off = cfg_off
    s._act_off = None
    s._act_via = None
    s._act_scan_at = 0.0
    s.gamelayer = lambda pbase: GL
    return s
@pytest.mark.parametrize("cls", [
    "st.activity.Dungeon",              # the documented name
    "st.activity._DungeonBase",         # underscore-prefixed base class
    "st.activity.DungeonContext",       # the activity's context object
    "st.activity.WolfDungeonInstance",  # a derived, renamed leaf
    "st.activity.dungeon.Wolf",         # moved under a `dungeon` namespace
])
def test_a_renamed_dungeon_activity_still_opens_the_gate(cls):
    hl = _GateHl({GL: GL_TYPE, GL + OFF_MAIN_ACTIVITY: ACT},
                 {ACT: [cls, "st.activity.Activity", "hl.Class"]})
    s = _gate_scene(hl, _GateProc({GL + OFF_RIFT_BOOL: 0}))
    assert s.instance_flags(0x1234) == (True, False)
    assert s.in_dungeon(0x1234) is True
def test_a_renamed_rift_activity_without_the_rift_byte_opens_the_gate():
    """The class chain is authoritative on its own: no `isRift` field, no
    `OFF_RIFT_BOOL`, still a rift."""
    hl = _GateHl({GL: GL_TYPE, GL + OFF_MAIN_ACTIVITY: ACT},
                 {ACT: ["st.activity.RiftTimer", "st.activity.Rift"]})
    s = _gate_scene(hl, _GateProc({GL + OFF_RIFT_BOOL: 0}))
    assert s.instance_flags(0x1234) == (False, True)
@pytest.mark.parametrize("cls", [
    "st.activity.WorldCamp",     # an overworld activity
    "st.activity.WorldElite",
    "st.activity.MountRush",
    "st.activity.Drift",         # contains the letters, not the word
    "st.activity.Activity",
])
def test_an_overworld_activity_does_not_open_the_gate(cls):
    hl = _GateHl({GL: GL_TYPE, GL + OFF_MAIN_ACTIVITY: ACT},
                 {ACT: [cls, "st.activity.Activity"]})
    s = _gate_scene(hl, _GateProc({GL + OFF_RIFT_BOOL: 0}))
    assert s.instance_flags(0x1234) == (False, False)
def test_class_words_span_namespaces_without_substring_matches():
    assert "dungeon" in _class_words("st.activity._DungeonBase")
    assert "dungeon" in _class_words("st.activity.dungeon.Wolf")
    assert "rift" in _class_words("st.activity.RiftTimer")
    assert "rift" not in _class_words("st.activity.Drift")
    assert "rift" not in _class_words("st.activity.MountRush")
    assert "rift" not in _class_words("")
    assert "rift" not in _class_words(None)
def test_a_moved_activity_slot_is_found_by_reflection():
    moved = 0x118
    hl = _GateHl({GL: GL_TYPE, GL + moved: ACT},
                 {ACT: ["st.activity.Dungeon", "st.activity.Activity"]},
                 {(GL_TYPE, "mainActivity"): moved})
    s = _gate_scene(hl, _GateProc({GL + OFF_RIFT_BOOL: 0}))
    assert s.instance_flags(0x1234) == (True, False)
    assert s._act_via == "reflected"
def test_a_moved_activity_slot_is_found_by_a_bounded_scan():
    """The field was renamed too, so only the header scan can find the slot."""
    moved = 0x118
    blk = bytearray(Scene.ACTIVITY_SCAN_BYTES)
    struct.pack_into("<Q", blk, moved, ACT)
    # the constant reads 0, but the header block does hold the slot at `moved`
    hl = _GateHl({GL: GL_TYPE, GL + moved: ACT},
                 {ACT: ["st.activity.Dungeon", "st.activity.Activity"]})
    proc = _GateProc({GL + OFF_RIFT_BOOL: 0}, {GL: bytes(blk)})
    s = _gate_scene(hl, proc)
    assert s.instance_flags(0x1234) == (True, False)
    assert s._act_via == "scanned"
    assert s._act_off == moved
    # the resolved slot is cached: the second tick must not rescan the header
    assert s.instance_flags(0x1234) == (True, False)
    assert len(proc.header_reads()) == 1
def test_the_scan_only_sees_activity_objects():
    """A header full of unrelated pointers must not be mistaken for a slot."""
    blk = bytearray(Scene.ACTIVITY_SCAN_BYTES)
    for off in range(0, len(blk), 8):
        # valid-looking pointers to objects the stub has no class for
        struct.pack_into("<Q", blk, off, BASE + 0x30000 + off)
    hl = _GateHl({GL: GL_TYPE})
    proc = _GateProc({GL + OFF_RIFT_BOOL: 0}, {GL: bytes(blk)})
    s = _gate_scene(hl, proc)
    assert s.instance_flags(0x1234) == (False, False)
def test_the_gate_never_raises_on_a_detached_process():
    s = _gate_scene(_BoomHl(), _GateProc())
    assert s.instance_flags(0x1234) == (False, False)

# ========================================================================
# from tests/test_instance_lock.py
# ========================================================================

"""Single-instance guard (fareverpal.lock): the refused-launch contract.

A second companion pointed at the same moddata folder must refuse to launch
instead of fighting over settings.json — collection.json / settings.json are
rewritten wholesale from in-memory state, so two writers lose data.

Pins the whole contract of app._acquire_instance_guard:
  * first launch acquires, a second contender on the same folder is refused
  * FAREVER_ALLOW_MULTI bypasses the guard (tooling escape hatch)
  * a crash-orphaned lock is reclaimed (Win32 dead-PID check and, portably,
    the stale-mtime path)
  * every outcome lands in settings_audit.log as `launch lock=<outcome>` —
    the refused "conflict" case especially, which used to go unaudited.

Lock-file format note (probed on PySide 6.11.1, ai/workspace probe):
QLockFile writes `pid\\nappname\\nuser\\nuuid\\n`, and while the lock is
genuinely held the OS write-locks the file itself — so orphaned locks can
only be simulated by fabricating a well-formed file by hand.
"""
pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ON_WINDOWS = sys.platform == "win32"
@pytest.fixture()
def lock_env(monkeypatch, tmp_path):
    """Fresh moddata dir per test + a Qt app object for QLockFile.

    A QApplication (not a bare QCoreApplication): the rest of the suite's
    widget fixtures do `instance() or QApplication([])`, which refuses to
    build a QApplication while a core-only app exists — a QCoreApplication
    here would kill the UI suites that run after this file.
    """
    monkeypatch.setenv("FAREVER_MODDATA_DIR", str(tmp_path))
    monkeypatch.delenv("FAREVER_ALLOW_MULTI", raising=False)
    monkeypatch.setenv("FAREVER_SESSION_AUDIT", "1")
    if QtWidgets.QApplication.instance() is None:
        _app = QtWidgets.QApplication([])  # noqa: F841
    return config_dir() / "fareverpal.lock"
def _fabricate_lock(path, pid: int, age_s: float = 0.0) -> None:
    """Write a well-formed (orphaned) QLockFile whose holder is `pid`."""
    path.write_text(
        f"{pid}\npython\nP4\n00000000-0000-0000-0000-000000000000\n",
        encoding="utf-8")
    if age_s:
        old = time.time() - age_s
        os.utime(path, (old, old))
def _dead_pid() -> int:
    """A PID that no live process owns (Win32 OpenProcess probe)."""
    import ctypes
    SYNCHRONIZE = 0x00100000
    for cand in range(99990, 1000, -4):
        h = ctypes.windll.kernel32.OpenProcess(SYNCHRONIZE, False, cand)
        if h:
            ctypes.windll.kernel32.CloseHandle(h)
        else:
            return cand
    raise AssertionError("no dead pid found")
def test_ok_then_conflict(lock_env):
    status, lock = app_mod._acquire_instance_guard()
    assert status == "ok"
    assert lock is not None

    # First instance still running (lock held): a second contender on the
    # same folder is refused and gets no lock of its own.
    status2, lock2 = app_mod._acquire_instance_guard()
    assert status2 == "conflict"
    assert lock2 is None

    # After the first instance exits (unlock), the folder is launchable again.
    lock.unlock()
    status3, lock3 = app_mod._acquire_instance_guard()
    assert status3 == "ok"
    assert lock3 is not None
    lock3.unlock()
def test_allow_multi_bypasses_held_lock(lock_env):
    holder = QtCore.QLockFile(str(lock_env))
    holder.setStaleLockTime(30000)
    assert holder.tryLock(0)

    monkey = os.environ
    monkey["FAREVER_ALLOW_MULTI"] = "1"
    try:
        status, lock = app_mod._acquire_instance_guard()
    finally:
        monkey.pop("FAREVER_ALLOW_MULTI", None)
    assert status == "ok"
    assert lock is None   # bypass holds no lock of its own
    holder.unlock()
def test_live_owner_is_conflict(lock_env):
    # A fabricated lock naming a *live* pid (this process) must be refused:
    # that is exactly what a real second instance looks like.
    _fabricate_lock(lock_env, os.getpid())
    status, lock = app_mod._acquire_instance_guard()
    assert status == "conflict"
    assert lock is None
@pytest.mark.skipif(not ON_WINDOWS, reason="dead-pid probe uses Win32 OpenProcess")
def test_crash_orphan_with_dead_owner_reclaimed(lock_env):
    _fabricate_lock(lock_env, _dead_pid())
    status, lock = app_mod._acquire_instance_guard()
    assert status == "ok"
    assert lock is not None
    lock.unlock()
def test_stale_orphan_reclaimed(lock_env):
    # Portable reclaim path: staleLockTime(30s) + mtime far in the past.
    _fabricate_lock(lock_env, os.getpid(), age_s=120)
    status, lock = app_mod._acquire_instance_guard()
    assert status == "ok"
    assert lock is not None
    lock.unlock()
def test_every_outcome_is_audited(lock_env):
    audit_path = config_dir() / "settings_audit.log"

    for outcome in ("ok", "conflict", "bypassed-allow-multi", "skip"):
        app_mod._audit_launch(outcome)

    lines = [ln for ln in audit_path.read_text(encoding="utf-8").splitlines()
             if " launch " in ln]
    assert [ln.split("lock=")[-1].split()[0] for ln in lines] == [
        "ok", "conflict", "bypassed-allow-multi", "skip"]

# ========================================================================
# from tests/test_reentry.py
# ========================================================================

"""The re-entrancy tripwire: depth accounting, reporting, and the guard.

A layout or signal cycle overflows the C stack BELOW the interpreter, so the
crash log for one names nothing (`app.py:390 in main` plus a C stack the
platform cannot walk — the 2026-09-28 21:02 session, on clicking 📦
Collection Manager). These are the tests for the thing that would have
named it.
"""
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
@pytest.fixture(scope="module", autouse=True)
def _qapp():
    """One offscreen QApplication for the module.

    Not optional: the tests build real QWidgets to feed the filter, and a
    QWidget constructed with no QApplication takes the process down with an
    access violation instead of raising — the run dies at 100% with no
    traceback. Same fixture shape as the other Qt test modules.
    """
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
def _app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
class _Ev:
    """Just enough QEvent for the filter: it only ever asks `.type()`."""

    def __init__(self, t):
        self._t = t

    def type(self):
        return self._t
def _nest(filt, w, etype, depth):
    """Deliver `etype` inside the delivery of `etype` — a real cycle."""
    if depth:
        filt._enter(w, etype)
        try:
            _nest(filt, w, etype, depth - 1)
        finally:
            filt._exit(etype)
def test_depth_is_reentrancy_not_breadth():
    """A wide fan-out must not read as a cycle; a nested chain must unwind."""
    filt = reentry._EventDepthFilter(limit=1000)
    w = QtWidgets.QWidget()
    t = QtCore.QEvent.Type.Resize

    _nest(filt, w, t, 20)
    assert filt.depth() == 0, "depth must unwind"
    for _ in range(200):                    # breadth, not depth
        filt._enter(w, t)
        filt._exit(t)
    assert filt.depth() == 0
def test_reports_once_past_the_limit_naming_the_widget(tmp_path, monkeypatch):
    """Past the limit it names the culprit, ONCE per (event, widget): a live
    cycle fires this on every single delivery and must not fill the dev log
    with thousands of copies of itself."""
    log = tmp_path / "dev.log"
    monkeypatch.setenv("FAREVER_DEV_CRASH_LOG", str(log))
    filt = reentry._EventDepthFilter(limit=3)
    w = QtWidgets.QWidget()
    w.setObjectName("tripwire-probe")

    _nest(filt, w, QtCore.QEvent.Type.Resize, 12)
    text = log.read_text(encoding="utf-8", errors="replace")
    assert "re-entrancy tripwire" in text
    assert "tripwire-probe" in text, "the culprit widget must be named"
    assert "QWidget" in text
    assert text.count("re-entrancy tripwire") == 1, "reported once per cycle"
    assert filt.depth() == 0
def test_a_fan_out_never_trips_the_tripwire(tmp_path, monkeypatch):
    """The false positive that would kill the watchdog's usefulness: 200
    sibling resizes are not 200 nested ones."""
    log = tmp_path / "dev.log"
    monkeypatch.setenv("FAREVER_DEV_CRASH_LOG", str(log))
    filt = reentry._EventDepthFilter(limit=4)
    w = QtWidgets.QWidget()
    for _ in range(200):
        filt._enter(w, QtCore.QEvent.Type.Resize)
        filt._exit(QtCore.QEvent.Type.Resize)
    assert not log.exists(), "a fan-out must not report"
def test_unwatched_events_are_not_counted():
    """Mouse moves and the like are not layout; counting them would drown
    the signal in noise."""
    filt = reentry._EventDepthFilter(limit=5)
    w = QtWidgets.QWidget()
    ev = _Ev(QtCore.QEvent.Type.MouseMove)
    for _ in range(100):
        assert filt.eventFilter(w, ev) is False
    assert filt.depth() == 0
def test_python_guard_trips_and_releases(tmp_path, monkeypatch):
    """The Python half: a builder that re-enters itself is reported, and the
    depth returns to zero so ordinary nesting cannot keep it tripped."""
    log = tmp_path / "dev.log"
    monkeypatch.setenv("FAREVER_DEV_CRASH_LOG", str(log))
    g = reentry.guard("probe", limit=2)

    with g:
        with g:
            assert not g.tripped
            with g:
                assert g.tripped
    assert g._depth == 0
    assert "re-entrancy guard tripped" in log.read_text(
        encoding="utf-8", errors="replace")
def test_guard_works_as_a_decorator():
    g = reentry.guard("decorated", limit=1)
    calls = []

    @g
    def build(n):
        calls.append(n)
        if n:
            with g:
                build(n - 1)
        return n * 2

    assert build(2) == 4
    assert calls == [2, 1, 0]
    assert g._depth == 0
def test_install_defaults_on_and_is_env_gated(monkeypatch):
    """ON unless switched off. A cycle kills the process with a log that
    names nothing, so the watchdog earns its keep on every session rather
    than only the ones somebody remembered to arm."""
    monkeypatch.delenv("FAREVER_REENTRY", raising=False)
    filt = reentry.install(_app(), limit=500)
    try:
        assert filt is not None, "armed by default"
        assert reentry.active() is filt
    finally:
        _app().removeEventFilter(filt)
    for off in ("0", "off", "no", ""):
        monkeypatch.setenv("FAREVER_REENTRY", off)
        assert reentry.install(_app()) is None, off
    monkeypatch.setenv("FAREVER_REENTRY", "abort")
    filt = reentry.install(_app(), limit=500)
    try:
        assert filt is not None and filt._abort
    finally:
        _app().removeEventFilter(filt)

# ========================================================================
# from tests/test_workers.py
# ========================================================================

"""The worker registry must never let a running QThread be destroyed.

`workers.retire()` exists because freeing a QThread while its C++ thread is
still executing frees Qt's internals out from under `run()`; nothing raises at
that moment, and the corruption only surfaces later on some unrelated
allocation. That is the 2026-10-03 22:57 run.log crash: a Windows access
violation INSIDE `QThread.__init__` (`game_attach.py:323`, main thread) seconds
after a game close, i.e. the next allocation to fault after `detach()` had
dropped a locate worker whose cooperative `stop()` had timed out.

These tests pin the reference-holding contract directly. The crash itself needs
the game plus a live locate worker and cannot be staged headlessly; what is
testable is that the registry — not the caller's local variable — is what keeps
a still-running worker alive.
"""
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
class _Blocking(QtCore.QThread):
    """A worker that parks inside `run()` until the test releases it."""

    def __init__(self) -> None:
        super().__init__()
        self.release = threading.Event()

    def run(self) -> None:            # noqa: D102 (Qt entry point)
        self.release.wait(5.0)
def _wait_for(predicate, timeout: float = 3.0) -> bool:
    deadline = timeout
    step = 0.01
    while deadline > 0:
        if predicate():
            return True
        threading.Event().wait(step)
        deadline -= step
    return predicate()
@pytest.fixture(autouse=True)
def _clean_registry():
    """Every test starts and ends with an empty retirement list.

    Teardown JOINS every retired worker before dropping it: releasing the
    reference to a still-running QThread at interpreter shutdown is exactly the
    free-while-running crash these tests are about, and it shows up as a
    non-zero process exit even when every test passed.
    """
    workers._RETIRING.clear()
    yield
    for w in list(workers._RETIRING):
        try:
            w.release.set()
            w.wait(2000)
        except Exception:
            pass
    workers._RETIRING.clear()
def test_retire_holds_a_running_worker_alive_after_the_caller_drops_it():
    """The invariant the crash needs: dropping every local reference must NOT
    free a worker that is still running."""
    w = _Blocking()
    w.start()
    assert _wait_for(w.isRunning), "worker never started"

    workers.retire(w)
    ref = weakref.ref(w)
    del w                          # the caller's only reference goes away
    gc.collect()

    assert ref() is not None, \
        "a running QThread was freed after its last caller reference dropped"
    assert ref() in workers._RETIRING

    ref().release.set()            # let run() return
    assert _wait_for(lambda: not ref().isRunning()), "worker never stopped"

    workers.retire(ref())          # a later retire prunes the finished entry
    gc.collect()
    assert ref() is None, "the finished worker was not released"
def test_retire_releases_a_worker_that_is_already_finished():
    """A worker retired after it stopped is dropped at once — the registry
    holds only what is genuinely still in flight."""
    w = _Blocking()
    w.release.set()
    w.start()
    assert _wait_for(lambda: not w.isRunning()), "worker never stopped"

    workers.retire(w)
    assert w not in workers._RETIRING
def _wait_running(worker, timeout: float = 3.0) -> None:
    assert _wait_for(worker.isRunning, timeout), "worker never started"
class _FakeLocateWorker(QtCore.QThread):
    """Quacks enough like `_LocateWorker` for the controller's swap path."""

    done = QtCore.Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.release = threading.Event()

    def run(self) -> None:
        self.release.wait(5.0)
def test_swapping_the_controller_worker_retires_a_running_one():
    """The regression itself: `_swap_worker` must not drop the QThread it
    replaces when that thread is still running (the live `attach()`/
    `_relocate()` path from the crash)."""
    from farever_companion.config import Settings
    from farever_companion.ui.game_attach import GameAttachmentController

    controller = GameAttachmentController(Settings())
    old = _FakeLocateWorker()
    old.done.connect(lambda _r: None)   # as production connects _on_located
    old.start()
    _wait_running(old)
    controller._worker = old        # as if a previous locate had left it live

    new = _FakeLocateWorker()
    controller._swap_worker(new)

    assert controller._worker is new
    assert old in workers._RETIRING, \
        "a running locate worker was dropped by _swap_worker"
    assert new not in workers._RETIRING

    old.release.set()
    assert _wait_for(lambda: not old.isRunning()), "old worker never stopped"
def test_the_registry_only_keeps_workers_whose_thread_is_alive():
    """A mixed batch: running threads are held, finished ones are not."""
    running, finished = _Blocking(), _Blocking()
    finished.release.set()
    running.start()
    finished.start()
    assert _wait_for(lambda: not finished.isRunning()), "finished never ended"
    assert _wait_for(running.isRunning), "running never started"

    workers.retire(finished)
    workers.retire(running)

    assert running in workers._RETIRING
    assert finished not in workers._RETIRING
