"""Boss aggro line tests (headless, no game).

Drives LiveModel._foe_target_state / foe_target_line with a stubbed memory
surface so the behavior is locked down: the line FOLLOWS LIVE (a genuine
retarget shows immediately, no debounce or stale hold) and only has a
never-blank fallback that keeps the last confirmed name briefly when the live
read comes up empty.
"""
from __future__ import annotations

import struct
import time
from types import SimpleNamespace

from farever_companion.core.model import LiveModel
from farever_companion.constants import (
    OFF_FOE_TARGET_OBJ, OFF_FOE_TARGET_HERO, OFF_FOE_TARGET_DIRECT,
    OFF_FOE_RECENT_HATE, OFF_FOE_HATE_LIST, OFF_FOE_TARGET_ALT,
    AGGRO_SWEEP_BYTES, FOE_TARGET_HOLD_S,
)


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
