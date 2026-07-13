"""Unit tests for Hl.field_offset - the HL runtime field-offset-by-name lookup
that powers the minimap's camera-yaw read (model.camera_yaw reads
client.BaseCamera.curDirection via this). No live game: a synthetic two-level
HL type with a runtime layout table is laid out in a FakeProc.

The offsets here are arbitrary stand-ins; the test asserts field_offset reports
exactly what the runtime table says, finds inherited fields, and degrades to None
on any inconsistency (rather than returning a bad offset)."""
from __future__ import annotations

import struct

from farever_companion.core.hl import Hl
from farever_companion import constants as C
from tests.fakemem import FakeProc

HOBJ = 11


class _Builder:
    """Lays out hl_type / hl_type_obj (with a fields array) and an hl_runtime_obj
    so Hl can resolve field offsets exactly as it does against the live game."""

    def __init__(self, proc: FakeProc, base: int = 0x200000):
        self.proc = proc
        self.cur = base
        proc.add_region(base, 0x40000, writable=True)

    def alloc(self, n: int, align: int = 8) -> int:
        if self.cur % align:
            self.cur += align - (self.cur % align)
        a = self.cur
        self.proc.write(a, b"\x00" * n)
        self.cur += n
        return a

    def _utf16(self, s: str) -> int:
        a = self.alloc(len(s) * 2 + 2)
        self.proc.put_utf16(a, s)
        return a

    def make_type(self, name: str, own_fields: list[str], super_type: int = 0) -> int:
        """An hl_type (+hl_type_obj) declaring `own_fields` (names only)."""
        fields = self.alloc(C.FIELD_STRIDE * max(1, len(own_fields)))
        for i, fn in enumerate(own_fields):
            self.proc.put_u64(fields + i * C.FIELD_STRIDE, self._utf16(fn))
        obj = self.alloc(0x60)
        self.proc.put_i32(obj, len(own_fields))           # nfields (own)
        self.proc.put_u64(obj + C.TO_NAME, self._utf16(name))
        self.proc.put_u64(obj + C.TO_SUPER, super_type)
        self.proc.put_u64(obj + C.TO_FIELDS, fields)
        tp = self.alloc(0x10)
        self.proc.put_i32(tp, HOBJ)
        self.proc.put_u64(tp + 8, obj)
        self._last_obj = obj
        return tp

    def attach_rt(self, type_ptr: int, offsets: list[int], size: int) -> None:
        """Attach a runtime layout table: offsets[i] = byte offset of global
        (super-first) field i; `size` = instance size."""
        obj = self.proc.u64(type_ptr + 8)
        idx = self.alloc(4 * max(1, len(offsets)))
        for i, off in enumerate(offsets):
            self.proc.write(idx + 4 * i, struct.pack("<i", off))
        rt = self.alloc(0x40)
        self.proc.put_i32(rt + C.RT_NFIELDS, len(offsets))
        self.proc.put_i32(rt + C.RT_SIZE, size)
        # fields_indexes lives at the runtime's primary candidate slot
        self.proc.put_u64(rt + C.RT_FI_CANDIDATES[0], idx)
        self.proc.put_u64(obj + C.TO_RUNTIME, rt)


def _scene():
    """Super{a,b} <- Derived{c,bodyDir}; global order a,b,c,bodyDir."""
    proc = FakeProc()
    b = _Builder(proc)
    sup = b.make_type("Super", ["a", "b"])
    der = b.make_type("Derived", ["c", "bodyDir"], super_type=sup)
    # global field offsets: a@0x08, b@0x10, c@0x18, bodyDir@0x20; size 0x30
    b.attach_rt(der, [0x08, 0x10, 0x18, 0x20], size=0x30)
    return Hl(proc), der


def test_resolves_own_field():
    hl, der = _scene()
    assert hl.field_offset(der, "bodyDir") == 0x20
    assert hl.field_offset(der, "c") == 0x18


def test_resolves_inherited_field():
    hl, der = _scene()
    assert hl.field_offset(der, "a") == 0x08
    assert hl.field_offset(der, "b") == 0x10


def test_unknown_field_is_none():
    hl, der = _scene()
    assert hl.field_offset(der, "nope") is None


def test_result_is_cached():
    hl, der = _scene()
    assert hl.field_offset(der, "bodyDir") == 0x20
    assert (der, "bodyDir") in hl._field_off_cache


def test_nfields_mismatch_bails():
    """If the runtime field count disagrees with the name-derived view, the
    assumption is broken -> None, never a guessed offset."""
    proc = FakeProc()
    b = _Builder(proc)
    sup = b.make_type("Super", ["a", "b"])
    der = b.make_type("Derived", ["c", "bodyDir"], super_type=sup)
    b.attach_rt(der, [0x08, 0x10, 0x18], size=0x30)   # only 3, name view says 4
    assert Hl(proc).field_offset(der, "bodyDir") is None


def test_offset_outside_instance_bails():
    proc = FakeProc()
    b = _Builder(proc)
    sup = b.make_type("Super", ["a", "b"])
    der = b.make_type("Derived", ["c", "bodyDir"], super_type=sup)
    b.attach_rt(der, [0x08, 0x10, 0x18, 0x999], size=0x30)  # bodyDir past size
    assert Hl(proc).field_offset(der, "bodyDir") is None


def test_missing_rt_bails():
    proc = FakeProc()
    b = _Builder(proc)
    der = b.make_type("Derived", ["c", "bodyDir"])    # no attach_rt
    assert Hl(proc).field_offset(der, "bodyDir") is None
