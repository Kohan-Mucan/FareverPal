"""Headless fake of the read-only Proc + a HashLink heap builder.

Lets the core memory-readers (`Hl`, `Scene`, the player locators) be unit-tested
with no live game: a sparse byte address space duck-types the `Proc` surface
(`try_read`/`u64`/`i32`/`f64`/`read_many`/`find_bytes*`/`regions`), and
`HeapBuilder` lays out HL types, instances, strings and arrays exactly as the
real runtime does (offsets mirror `core/hl.py`). Used by test_scene.py and
test_player_locate.py.
"""
from __future__ import annotations

import struct

# HL layout constants (mirror core/hl.py / core/appsingleton.py).
HOBJ = 11
TO_NAME = 0x10
TO_SUPER = 0x18
TO_GLOBALVAL = 0x38


class FakeProc:
    """Sparse, read-only byte memory that duck-types core.proc.Proc."""

    def __init__(self):
        self.mem: dict[int, int] = {}
        self._regions: list[tuple[int, int, int, bool]] = []
        self.kind = "native"
        self.pid = 4242
        self.name = "Farever.exe"

    # --- authoring -------------------------------------------------------
    def write(self, addr: int, data: bytes) -> None:
        for i, b in enumerate(data):
            self.mem[addr + i] = b

    def put_u64(self, addr: int, val: int) -> None:
        self.write(addr, struct.pack("<Q", val & 0xFFFFFFFFFFFFFFFF))

    def put_i32(self, addr: int, val: int) -> None:
        self.write(addr, struct.pack("<i", val))

    def put_f64(self, addr: int, val: float) -> None:
        self.write(addr, struct.pack("<d", val))

    def put_utf16(self, addr: int, s: str) -> None:
        self.write(addr, s.encode("utf-16-le") + b"\x00\x00")

    def add_region(self, base: int, size: int, writable: bool = True,
                   prot: int = 0x04) -> None:
        self._regions.append((base, size, prot, writable))

    # --- read surface ----------------------------------------------------
    def _get(self, addr: int, n: int) -> bytes | None:
        # Model committed pages: a read fully inside a registered region
        # succeeds, zero-filling any byte not explicitly written (just like a
        # freshly committed page). A read straying outside every region fails,
        # exactly as the real reader returns None / raises on unmapped memory.
        if not any(base <= addr and addr + n <= base + size
                   for (base, size, _p, _w) in self._regions):
            return None
        return bytes(self.mem.get(addr + i, 0) for i in range(n))

    @property
    def has_scan(self) -> bool:
        return True

    def try_read(self, addr: int, n: int) -> bytes | None:
        return self._get(addr, n)

    def read(self, addr: int, n: int) -> bytes:
        b = self._get(addr, n)
        if b is None:
            raise OSError(f"bad read {n}B @ {addr:#x}")
        return b

    def u64(self, addr: int) -> int:
        b = self._get(addr, 8)
        if b is None:
            raise OSError(f"bad u64 @ {addr:#x}")
        return struct.unpack("<Q", b)[0]

    def i32(self, addr: int) -> int:
        b = self._get(addr, 4)
        if b is None:
            raise OSError(f"bad i32 @ {addr:#x}")
        return struct.unpack("<i", b)[0]

    def f64(self, addr: int) -> float:
        b = self._get(addr, 8)
        if b is None:
            raise OSError(f"bad f64 @ {addr:#x}")
        return struct.unpack("<d", b)[0]

    def read_many(self, addrs: list[int], size: int) -> list[bytes | None]:
        return [self._get(a, size) for a in addrs]

    def read_many_u64(self, addrs: list[int]) -> list[int | None]:
        out: list[int | None] = []
        for a in addrs:
            b = self._get(a, 8)
            out.append(struct.unpack("<Q", b)[0] if b else None)
        return out

    # --- scan surface ----------------------------------------------------
    def regions(self) -> list[tuple[int, int, int, bool]]:
        return list(self._regions)

    def _scan(self, needle: bytes, ranges: list[tuple[int, int]], align: int,
              max_hits: int) -> list[int]:
        hits: list[int] = []
        L = len(needle)
        step = align if align > 1 else 1
        for base, size in ranges:
            a = base
            if a % step:
                a += step - (a % step)
            end = base + size
            while a + L <= end:
                if self._get(a, L) == needle:
                    hits.append(a)
                    if len(hits) >= max_hits:
                        return hits
                a += step
        return hits

    def _all_ranges(self, rw_only: bool) -> list[tuple[int, int]]:
        return [(b, s) for (b, s, _p, w) in self._regions if (w or not rw_only)]

    def find_bytes(self, needle: bytes, align: int = 1, rw_only: bool = False,
                   max_hits: int = 4096) -> list[int]:
        return self._scan(needle, self._all_ranges(rw_only), align, max_hits)

    def find_qword(self, value: int, rw_only: bool = True,
                   max_hits: int = 4096) -> list[int]:
        return self.find_bytes(struct.pack("<Q", value), 8, rw_only, max_hits)

    def find_bytes_in(self, needle: bytes, ranges: list[tuple[int, int]],
                      align: int = 1, max_hits: int = 4096) -> list[int]:
        return self._scan(needle, ranges, align, max_hits)

    def find_qword_in(self, value: int, ranges: list[tuple[int, int]],
                      max_hits: int = 4096) -> list[int]:
        return self.find_bytes_in(struct.pack("<Q", value), ranges, 8, max_hits)


class HeapBuilder:
    """Bump-allocates HL types/instances/strings/arrays into a FakeProc."""

    def __init__(self, proc: FakeProc, base: int = 0x100000,
                 region: int = 0x80000):
        self.proc = proc
        self.cur = base
        proc.add_region(base, region, writable=True)
        self.types: dict[str, int] = {}      # class name -> hl_type ptr
        self.type_objs: dict[str, int] = {}  # class name -> hl_type_obj ptr

    def alloc(self, n: int, align: int = 8, zero: bool = True) -> int:
        if self.cur % align:
            self.cur += align - (self.cur % align)
        a = self.cur
        if zero:
            self.proc.write(a, b"\x00" * n)
        self.cur += n
        return a

    def make_type(self, name: str, super_type: int = 0) -> int:
        """Create an hl_type (+ its hl_type_obj) for `name`. Returns the hl_type
        pointer; the type_obj is recorded in `self.type_objs[name]`."""
        if name in self.types:
            return self.types[name]
        name_addr = self.alloc(len(name) * 2 + 2)
        self.proc.put_utf16(name_addr, name)
        obj = self.alloc(0x40)
        self.proc.put_i32(obj, 0)                       # nfields
        self.proc.put_u64(obj + TO_NAME, name_addr)
        self.proc.put_u64(obj + TO_SUPER, super_type)   # super hl_type ptr (or 0)
        tp = self.alloc(0x10)
        self.proc.put_i32(tp, HOBJ)                     # kind
        self.proc.put_u64(tp + 8, obj)
        self.types[name] = tp
        self.type_objs[name] = obj
        return tp

    def make_instance(self, type_name: str, size: int = 0x400) -> int:
        tp = self.types.get(type_name) or self.make_type(type_name)
        a = self.alloc(size)
        self.proc.put_u64(a, tp)
        return a

    def make_string(self, s: str) -> int:
        bptr = self.alloc(len(s) * 2 + 2)
        self.proc.put_utf16(bptr, s)
        strobj = self.alloc(0x18)
        # A real HL String carries its hl_type at +0, so readers that guard
        # with class_of(x) == "String" (e.g. the config signature scan) work.
        tp = self.types.get("String") or self.make_type("String")
        self.proc.put_u64(strobj, tp)
        self.proc.put_u64(strobj + 8, bptr)
        self.proc.put_i32(strobj + 0x10, len(s))
        return strobj

    def make_array(self, ptrs: list[int]) -> int:
        backing = self.alloc(0x18 + 8 * max(1, len(ptrs)))
        self.proc.put_i32(backing + 0x10, len(ptrs))    # NativeArray size
        for i, p in enumerate(ptrs):
            self.proc.put_u64(backing + 0x18 + 8 * i, p)
        arr = self.alloc(0x18)
        self.proc.put_i32(arr + 8, len(ptrs))           # ArrayObj length
        self.proc.put_u64(arr + 0x10, backing)
        return arr
