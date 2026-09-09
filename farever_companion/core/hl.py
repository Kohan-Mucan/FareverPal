"""HashLink runtime reflection over the live process (read-only).

Every Haxe->HashLink heap object carries its runtime type, so we read class
names, super-chains, strings and arrays straight from memory. Object model
(HL 64-bit, validated 2026-05; array layout = post-2026-05-21 patch):

    instance[+0]            -> hl_type*
    hl_type[+0]             : kind (i32)   HOBJ=11, HSTRUCT=21
    hl_type[+8]             -> hl_type_obj*
    hl_type_obj[+0]         : nfields (i32)
    hl_type_obj[+0x10]      -> name (uchar*, UTF-16LE, null-terminated)
    hl_type_obj[+0x18]      -> super (hl_type*)
    hl_type_obj[+0x20]      -> fields (hl_obj_field[]; each 0x18: name*, type*, hash)
    String[+0x08]           -> bytes (UTF-16LE);  String[+0x10] : length (i32)
    ArrayObj[+0x08]         : length (i32);  ArrayObj[+0x10] -> NativeArray
    NativeArray[+0x10]      : size (i32);  data @ +0x18 (8B object pointers)

Type pointers are stable for the process life, so class-name lookups cache by
type pointer; string reads cache by string-object pointer.
"""
from __future__ import annotations

import struct

from .proc import Proc, ProcError
from ..constants import (
    HOBJ as _HOBJ, HSTRUCT as _HSTRUCT, USER_MIN as _USER_MIN,
    USER_MAX as _USER_MAX, TO_NAME, TO_SUPER, TO_FIELDS, TO_RUNTIME,
    FIELD_STRIDE, RT_NFIELDS, RT_SIZE, RT_FI_CANDIDATES, HL_WSIZE,
    NA_SIZE_OFF, NA_DATA_OFF,
)


def is_ptr(v: int | None) -> bool:
    return v is not None and _USER_MIN < v < _USER_MAX


def utf16z(s: str) -> bytes:
    """A UTF-16LE, null-terminated class/name needle for a memory scan. Shared
    by the locators (appsingleton.py, player.py)."""
    return s.encode("utf-16-le") + b"\x00\x00"


class Hl:
    def __init__(self, proc: Proc):
        self.proc = proc
        self._name_cache: dict[int, str | None] = {}
        self._str_cache: dict[int, str | None] = {}
        self._anc_cache: dict[int, frozenset[str]] = {}
        self._field_off_cache: dict[tuple[int, str], int | None] = {}
        self._field_type_cache: dict[tuple[int, str], int | None] = {}

    # --- primitives ------------------------------------------------------
    def ptr(self, addr: int) -> int | None:
        v = self.proc.try_read(addr, 8)
        if v is None:
            return None
        p = struct.unpack("<Q", v)[0]
        return p if is_ptr(p) else None

    # normalise native OSError to ProcError for the guards below
    def u64(self, addr: int) -> int:
        try:
            return self.proc.u64(addr)
        except OSError as e:
            raise ProcError(str(e)) from e

    def i32(self, addr: int) -> int:
        try:
            return self.proc.i32(addr)
        except OSError as e:
            raise ProcError(str(e)) from e

    def u8(self, addr: int) -> int:
        """One raw byte (the Haxe `Bool` / Int8 fields, e.g. ent.Unit.isInCombat)."""
        try:
            return self.proc.read(addr, 1)[0]
        except OSError as e:
            raise ProcError(str(e)) from e

    def f64(self, addr: int) -> float:
        try:
            return self.proc.f64(addr)
        except OSError as e:
            raise ProcError(str(e)) from e

    # --- type / class ----------------------------------------------------
    def _read_utf16(self, addr: int, max_chars: int = 256) -> str | None:
        raw = self.proc.try_read(addr, max_chars * 2)
        if raw is None:
            return None
        end = raw.find(b"\x00\x00")
        if end != -1 and end % 2 == 1:
            end += 1
        if end != -1:
            raw = raw[:end]
        return raw.decode("utf-16-le", errors="replace")

    def type_name(self, type_ptr: int) -> str | None:
        if type_ptr in self._name_cache:
            return self._name_cache[type_ptr]
        name = self._resolve_type_name(type_ptr)
        self._name_cache[type_ptr] = name
        return name

    # --- type unwrapping / chains ----------------------------------------
    def _unwrap_type(self, tp: int) -> tuple[int, int]:
        """`(concrete type ptr, hl_type kind)` for `tp`, HNULL unwrapped.

        A slot holding a class value is wrapped in an HNULL type (kind 15)
        whose +8 slot points at the real type. The wrapper is not itself
        HOBJ/HSTRUCT, so every walk has to unwrap before reading; and when the
        inner pointer is not a class either, the wrapper is treated as a
        struct with `tp` left alone - its +8 slot is then the inner pointer,
        which is what the readers below take it for."""
        kind = self.i32(tp)
        if kind == 15:
            inner_tp = self.ptr(tp + 8)
            if inner_tp:
                inner_kind = self.i32(inner_tp)
                if inner_kind in (_HOBJ, _HSTRUCT):
                    return inner_tp, inner_kind
                return tp, _HSTRUCT
        return tp, kind

    def _chain(self, tp: int, limit: int = 16):
        """Yield `(type ptr, hl_type_obj ptr)` up the super-chain from `tp`.

        Ends on a non-class kind, a cycle, `limit` levels, or an unreadable
        pointer - fail closed, the way each caller did inline. One walk, so
        the unwrap and the super step exist once for all of them."""
        seen: set[int] = set()
        while is_ptr(tp) and tp not in seen and len(seen) < limit:
            seen.add(tp)
            try:
                tp, kind = self._unwrap_type(tp)
                if kind not in (_HOBJ, _HSTRUCT):
                    return
                obj_ptr = self.u64(tp + 8)
                super_tp = self.u64(obj_ptr + TO_SUPER)
            except ProcError:
                return
            yield tp, obj_ptr
            tp = super_tp

    def _concrete(self, type_ptr: int) -> tuple[int, int] | None:
        """The one `(type ptr, hl_type_obj ptr)` for `type_ptr`, or None when
        it is not a readable HOBJ/HSTRUCT (HNULL unwrapped first)."""
        return next(self._chain(type_ptr, limit=1), None)

    def _chain_names(self, tp: int, limit: int = 16) -> list[str]:
        """Class names up the super-chain from `tp`; `?` for an unreadable one."""
        names: list[str] = []
        try:
            for _tp, obj_ptr in self._chain(tp, limit):
                names.append(self._read_utf16(self.u64(obj_ptr + TO_NAME)) or "?")
        except ProcError:
            pass
        return names

    def _resolve_type_name(self, type_ptr: int) -> str | None:
        if not is_ptr(type_ptr):
            return None
        concrete = self._concrete(type_ptr)
        if concrete is None or not is_ptr(concrete[1]):
            return None
        try:
            name_ptr = self.u64(concrete[1] + TO_NAME)
        except ProcError:
            return None
        if not is_ptr(name_ptr):
            return None
        return self._read_utf16(name_ptr)

    def class_of(self, instance: int) -> str | None:
        tp = self.ptr(instance)
        return self.type_name(tp) if tp is not None else None

    def super_chain(self, instance: int, limit: int = 16) -> list[str]:
        return self._chain_names(self.ptr(instance), limit)

    def is_a(self, instance: int, ancestor: str) -> bool:
        """True if `instance`'s class is, or descends from, `ancestor`."""
        return ancestor in self.super_chain(instance)

    def ancestors(self, type_ptr: int | None) -> frozenset[str]:
        """Class names in a type's super-chain (incl. itself), cached by type ptr."""
        if not is_ptr(type_ptr):
            return frozenset()
        cached = self._anc_cache.get(type_ptr)
        if cached is not None:
            return cached
        result = frozenset(self._chain_names(type_ptr))
        self._anc_cache[type_ptr] = result
        return result

    def type_is_a(self, type_ptr: int | None, ancestor: str) -> bool:
        return ancestor in self.ancestors(type_ptr)

    def field_names(self, type_ptr: int) -> list[str]:
        """Declared field names of an object type (own fields only), in order."""
        if not is_ptr(type_ptr):
            return []
        concrete = self._concrete(type_ptr)
        if concrete is None:
            return []
        try:
            n = self.i32(concrete[1])
            fields = self.u64(concrete[1] + TO_FIELDS)
        except ProcError:
            return []
        if not is_ptr(fields) or not (0 <= n < 4096):
            return []
        out = []
        for i in range(n):
            try:
                name_ptr = self.u64(fields + i * FIELD_STRIDE)
                out.append(self._read_utf16(name_ptr) or "?")
            except ProcError:
                break
        return out

    def field_type_ptr(self, type_ptr: int, name: str) -> int | None:
        """Declared type pointer of field `name`, searching the super-chain.

        The HL field table stores {name, type, hash} per field, so a type's own
        layout can be enumerated from the *type* alone — no live instance
        needed. The config-slot resolver uses this to learn what
        st.GameLayer.config points at (2026-09-19: the calibrated slot moved
        and the old candidate landed on an unrelated object). Cached by
        (type_ptr, name); None when the field or its declared type is
        unreadable."""
        key = (type_ptr, name)
        if key in self._field_type_cache:
            return self._field_type_cache[key]
        out: int | None = None
        try:
            for _tp, obj_ptr in self._chain(type_ptr, limit=32):
                n = self.i32(obj_ptr)
                fields = self.u64(obj_ptr + TO_FIELDS)
                if is_ptr(fields) and 0 < n < 4096:
                    for i in range(n):
                        base = fields + i * FIELD_STRIDE
                        if (self._read_utf16(self.u64(base)) or "") == name:
                            t = self.u64(base + 8)
                            out = t if is_ptr(t) else None
                            break
                    if out is not None:
                        break
        except ProcError:
            out = None
        self._field_type_cache[key] = out
        return out

    def field_offset(self, type_ptr: int, name: str) -> int | None:
        """Byte offset of field `name` within an instance of `type_ptr`, found by
        name across the whole super-chain (so inherited fields resolve too). None
        if it can't be resolved. Reflection-only - no hardcoded layout - so it
        survives patches; self-validating, so a wrong assumption degrades to None
        rather than a bad read. Cached by (type_ptr, name)."""
        key = (type_ptr, name)
        if key in self._field_off_cache:
            return self._field_off_cache[key]
        off = self._resolve_field_offset(type_ptr, name)
        self._field_off_cache[key] = off
        return off

    def _resolve_field_offset(self, type_ptr: int, name: str) -> int | None:
        if not is_ptr(type_ptr):
            return None
        # Build the global field order (super-first): collect each level's own
        # fields up the chain, then reverse. The HL runtime numbers fields the
        # same way, so a name's position here is its global field index.
        levels: list[list[str]] = []
        try:
            for tp, _obj_ptr in self._chain(type_ptr, limit=32):
                levels.append(self.field_names(tp))
        except ProcError:
            return None
        global_names = [n for lvl in reversed(levels) for n in lvl]
        try:
            gidx = global_names.index(name)
        except ValueError:
            return None
        # Read the byte offset from the concrete type's runtime layout table.
        try:
            # Re-resolve the concrete type in case the original was HNULL-wrapped.
            concrete_tp = self._unwrap_type(type_ptr)[0]
            obj_ptr = self.u64(concrete_tp + 8)
            rt = self.u64(obj_ptr + TO_RUNTIME)
            if not is_ptr(rt):
                return None
            nfields = self.i32(rt + RT_NFIELDS)
            size = self.i32(rt + RT_SIZE)
        except ProcError:
            return None
        # Consistency gate: our name-derived field count must match the runtime's.
        if len(global_names) != nfields or not (0 <= gidx < nfields):
            return None
        offsets = self._fields_indexes(rt, nfields)
        if offsets is None or gidx >= len(offsets):
            return None
        off = offsets[gidx]
        # The offset must be a real field slot (>0; slot 0 is the hl_type*) and,
        # when the instance size is sane, lie inside the instance.
        if off <= 0 or (0 < size < (1 << 20) and off + HL_WSIZE > size):
            return None
        return off


    def _fields_indexes(self, rt: int, nfields: int) -> list[int] | None:
        """The hl_runtime_obj field-offset array (int[nfields]). Its slot moved
        between builds, so probe the candidates and accept the one that reads as a
        plausible offset table: nfields entries, the first at HL_WSIZE, strictly
        ascending, all in object range. None if none qualify (-> caller bails)."""
        if not (0 < nfields < 4096):
            return None
        for slot in RT_FI_CANDIDATES:
            try:
                arr = self.u64(rt + slot)
            except ProcError:
                continue
            if not is_ptr(arr):
                continue
            raw = self.proc.try_read(arr, nfields * 4)
            if raw is None:
                continue
            vals = list(struct.unpack_from("<%di" % nfields, raw, 0))
            if (vals[0] == HL_WSIZE
                    and all(0 < vals[i] < (1 << 16) for i in range(nfields))
                    and all(vals[i] < vals[i + 1] for i in range(nfields - 1))):
                return vals
        return None

    # --- strings ---------------------------------------------------------
    def hl_string(self, strobj: int | None) -> str | None:
        if not is_ptr(strobj):
            return None
        if strobj in self._str_cache:
            return self._str_cache[strobj]
        val = self._read_string(strobj)
        self._str_cache[strobj] = val
        return val

    def _read_string(self, strobj: int) -> str | None:
        try:
            bptr = self.u64(strobj + 8)
            length = self.i32(strobj + 0x10)
        except ProcError:
            return None
        if not is_ptr(bptr) or not (0 <= length < 4096):
            return None
        raw = self.proc.try_read(bptr, length * 2)
        if raw is None:
            return None
        return raw.decode("utf-16-le", errors="replace")

    # --- arrays ----------------------------------------------------------
    def array(self, arrobj: int | None, max_elems: int = 1024) -> list[int]:
        if not is_ptr(arrobj):
            return []
        try:
            length = self.i32(arrobj + 8)
            backing = self.u64(arrobj + 0x10)
        except ProcError:
            return []
        if not is_ptr(backing) or not (0 <= length < 1_000_000):
            return []
        try:
            bsize = self.i32(backing + NA_SIZE_OFF)
        except ProcError:
            return []
        n = max(0, min(length, bsize, max_elems))
        raw = self.proc.try_read(backing + NA_DATA_OFF, n * 8)
        if raw is None:
            return []
        return [struct.unpack_from("<Q", raw, i * 8)[0] for i in range(n)]
