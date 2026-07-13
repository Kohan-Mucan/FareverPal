"""Read-only per-skill damage source via `ui.comp.DamageDisplay`.

Each tick, enumerate the live DamageDisplay objects (the floating numbers the
game renders for the local client), read each one's `st.skill.DamageResult`,
dedupe over its short life, and feed DpsMeter.add_event. Self-only by design.
Until the field offsets below are calibrated, `calibrated()` is False, `poll()`
returns [], and the meter falls back to HP-diff. Never fabricates an event.
"""
from __future__ import annotations

import os
import struct
import sys
import time

from .hl import Hl, is_ptr, _HOBJ
from ..constants import TO_NAME
from .proc import Proc, ProcError
from ..combat.dps import DamageEvent

# Calibrated live 2026-05-30 (build 13,358,488 B) from the runtime fields_indexes
# table. Struct field offsets, stable for the build; the type ptr is still located
# by name in `_find_type`. Re-validate live if a patch shifts the layout.
OFF_DISPLAY_RESULT: int | None = 0x498  # DamageDisplay.dmg -> st.skill.DamageResult
OFF_RESULT_AMOUNT: int | None = 0x50    # DamageResult._amount (f64)
OFF_RESULT_SKILL: int | None = 0x08     # DamageResult.baseSkill -> st.skill.BaseSkill
OFF_RESULT_CRIT: int | None = 0x69      # DamageResult._critical (bool, 1 byte)
OFF_RESULT_KILL: int | None = 0x68      # DamageResult._kill (bool, 1 byte)
OFF_RESULT_TARGET: int | None = 0x28    # DamageResult.target -> ent.GameObject
OFF_SKILL_ID: int | None = 0xA0         # BaseSkill.kind (String), per-skill name
OFF_RESULT_KIND: int | None = None      # DamageResult.kind enum (i32); no source yet

# raw DamageResult.kind enum -> our label; unmapped values degrade to "damage".
KIND_MAP: dict[int, str] = {}

# Cluster-scan tuning. The GC is non-moving and size-class-segregated, so padding
# known instance addresses and merging neighbours covers the pages where future
# DamageDisplays spawn. Generous but tiny next to the ~23 GB heap.
RANGE_PAD = 1 << 18
RANGE_MERGE_GAP = 1 << 20
MAX_SCAN_BYTES = 512 << 20
MAX_ANCHORS = 8000


def cluster_ranges(addrs: list[int], pad: int = RANGE_PAD,
                   merge_gap: int = RANGE_MERGE_GAP) -> list[tuple[int, int]]:
    """Collapse instance addresses into merged `(base, len)` scan ranges."""
    pts = sorted(set(a for a in addrs if a > 0))
    if not pts:
        return []
    ranges: list[tuple[int, int]] = []
    lo = max(0, pts[0] - pad)
    hi = pts[0] + pad
    for a in pts[1:]:
        if a - pad <= hi + merge_gap:
            hi = max(hi, a + pad)
        else:
            ranges.append((lo, hi - lo))
            lo, hi = max(0, a - pad), a + pad
    ranges.append((lo, hi - lo))
    return ranges


def clip_ranges(ranges: list[tuple[int, int]],
                regions: list[tuple]) -> list[tuple[int, int]]:
    """Intersect scan ranges with committed readable regions."""
    if not ranges:
        return []
    regs = sorted((b, b + s) for b, s, *_ in regions)
    out: list[tuple[int, int]] = []
    for base, length in ranges:
        rs, re_ = base, base + length
        for rb, rend in regs:
            if rend <= rs:
                continue
            if rb >= re_:
                break
            a, b = max(rs, rb), min(re_, rend)
            if b > a:
                out.append((a, b - a))
    return out


def _utf16z(s: str) -> bytes:
    return s.encode("utf-16-le") + b"\x00\x00"


class DamageReader:
    CLASS = "ui.comp.DamageDisplay"

    def __init__(self, proc: Proc, hl: Hl, my_hero: int | None = None):
        self.proc = proc
        self.hl = hl
        self.my_hero = my_hero
        self._type_ptr: int | None = None
        # FAREVER_DMG_TYPE: known type ptr for the session, skips the heap scan.
        # Verified by class name before use, so a stale ptr falls back to the scan.
        self._type_hint: str | None = os.environ.get("FAREVER_DMG_TYPE")
        # Dedup by content signature, not address: the GC recycles freed slots
        # under sustained combat, so a new number can reuse a just-freed address.
        self._sig: dict[int, tuple] = {}
        self._scan_ranges: list[tuple[int, int]] = []
        self._ranges_ready = False
        self._last_count = 0
        self._anchor_addrs: set[int] = set()

    @staticmethod
    def calibrated() -> bool:
        return None not in (OFF_DISPLAY_RESULT, OFF_RESULT_AMOUNT, OFF_RESULT_TARGET)

    def _log(self, msg: str) -> None:
        print(f"[dmg-scan] {msg}", file=sys.stderr, flush=True)

    def _find_type(self) -> int | None:
        if self._type_ptr is not None or not self.proc.has_scan:
            return self._type_ptr
        if self._type_hint:
            try:
                cand = int(self._type_hint, 0)
                if self.hl.type_name(cand) == self.CLASS:
                    self._log(f"using FAREVER_DMG_TYPE hint {cand:#x}")
                    self._type_ptr = cand
                    return cand
                self._log("FAREVER_DMG_TYPE hint stale (class mismatch); scanning")
            except (ProcError, OSError, ValueError):
                self._log("FAREVER_DMG_TYPE hint unreadable; scanning")
            self._type_hint = None
        # Back-references (type_obj.name, hl_type+8, instances) live in the
        # writable heap even though the name string may be read-only const data.
        t0 = time.monotonic()
        names = self.proc.find_bytes(_utf16z(self.CLASS), align=2,
                                     rw_only=False, max_hits=64)
        self._log(f"name-string scan: {len(names)} hit(s) in {time.monotonic()-t0:.1f}s")
        for name_addr in names:
            refs = self.proc.find_qword(name_addr, rw_only=True, max_hits=64)
            for ref in refs:
                type_obj = ref - TO_NAME
                for tref in self.proc.find_qword(type_obj, rw_only=True, max_hits=64):
                    cand = tref - 8
                    try:
                        if self.hl.i32(cand) == _HOBJ and self.hl.type_name(cand) == self.CLASS:
                            self._log(f"located type {cand:#x} in {time.monotonic()-t0:.1f}s")
                            self._type_ptr = cand
                            return cand
                    except (ProcError, OSError):
                        continue
        self._log(f"type NOT found this pass ({time.monotonic()-t0:.1f}s)")
        return None

    def refresh_ranges(self) -> int:
        """Slow (~heap sweep): re-derive the GC-page ranges the displays cluster
        into. Run occasionally on a maintenance thread; `poll()` scans the ranges."""
        if not self.calibrated():
            return 0
        tp = self._find_type()
        if tp is None:
            return 0
        try:
            insts = self.proc.find_qword(tp, rw_only=True, max_hits=4096)
        except (ProcError, OSError):
            return self._last_count
        # Accumulate anchors across derives: old anchors stay valid (non-moving GC),
        # clip_ranges drops freed pages. Converges to complete page coverage.
        self._anchor_addrs.update(insts)
        if len(self._anchor_addrs) > MAX_ANCHORS:
            self._anchor_addrs = set(sorted(self._anchor_addrs)[-MAX_ANCHORS:])
        anchors = sorted(self._anchor_addrs)
        ranges = cluster_ranges(anchors)
        try:
            ranges = clip_ranges(ranges, self.proc.regions())
        except (ProcError, OSError):
            pass
        total = sum(length for _, length in ranges)
        if total > MAX_SCAN_BYTES:
            self._log(f"cluster {total >> 20} MiB > cap; tightening pad")
            ranges = clip_ranges(
                cluster_ranges(anchors, pad=RANGE_PAD >> 3, merge_gap=RANGE_MERGE_GAP >> 3),
                self._regions_or_empty(),
            )
        self._scan_ranges = ranges
        self._last_count = len(insts)
        self._ranges_ready = bool(ranges)
        self._log(f"ranges: {len(insts)} insts (+acc {len(anchors)}) -> {len(ranges)} "
                  f"range(s), {sum(l for _, l in ranges) >> 20} MiB")
        return len(insts)

    def _regions_or_empty(self) -> list[tuple]:
        try:
            return self.proc.regions()
        except (ProcError, OSError):
            return []

    def poll(self) -> list[DamageEvent]:
        """Fast range-bounded scan: emit an event per display whose number changed.
        Incoming hits (target == our hero) are skipped; taken damage is HP-derived."""
        tp = self._type_ptr
        if tp is None or not self._scan_ranges:
            return []
        try:
            insts = self.proc.find_qword_in(tp, self._scan_ranges, max_hits=4096)
        except (ProcError, OSError):
            return []
        events: list[DamageEvent] = []
        cur: dict[int, tuple] = {}
        for disp in insts:
            try:
                res = self.hl.ptr(disp + OFF_DISPLAY_RESULT)
            except (ProcError, OSError):
                continue
            if not res:
                continue
            target = self.hl.ptr(res + OFF_RESULT_TARGET)
            if self.my_hero and target == self.my_hero:
                continue
            ev = self._read_event(res, target, False)
            if not self._plausible(ev):
                continue
            sig = (res, int(ev.amount), ev.skill, ev.crit, ev.kill)
            cur[disp] = sig
            if self._sig.get(disp) != sig:
                events.append(ev)
        self._sig = cur
        return events

    @staticmethod
    def _plausible(ev: DamageEvent) -> bool:
        # a real hit has a sane amount and a clean ASCII skill id; junk slots
        # decode to huge numbers or CJK
        s = ev.skill
        return (0.0 < ev.amount < 1e7
                and bool(s) and s != "?" and s.isascii() and s.isprintable())

    def _read_event(self, res: int, target: int | None,
                    incoming: bool = False) -> DamageEvent:
        amount = 0.0
        try:
            raw = self.proc.try_read(res + OFF_RESULT_AMOUNT, 8)
            amount = abs(struct.unpack("<d", raw)[0]) if raw else 0.0
            if amount > 1e9 or amount != amount:   # not an f64; try i32
                amount = float(self.hl.i32(res + OFF_RESULT_AMOUNT))
        except (ProcError, OSError):
            pass
        skill = "?"
        if OFF_RESULT_SKILL is not None and OFF_SKILL_ID is not None:
            try:
                sk = self.hl.ptr(res + OFF_RESULT_SKILL)
                if sk:
                    skill = self.hl.hl_string(self.hl.ptr(sk + OFF_SKILL_ID)) or "?"
            except (ProcError, OSError):
                skill = "?"
        crit = bool(self._flag(res, OFF_RESULT_CRIT))
        kill = bool(self._flag(res, OFF_RESULT_KILL))
        kind = KIND_MAP.get(self._flag(res, OFF_RESULT_KIND), "damage")
        return DamageEvent(amount=amount, skill=skill, crit=crit, kill=kill,
                           target=str(target), kind=kind, incoming=incoming)

    def _flag(self, res: int, off: int | None) -> int:
        # _kill (+0x68) and _critical (+0x69) are adjacent 1-byte bools; read one
        # byte so an i32 read can't bleed the neighbour in
        if off is None:
            return 0
        raw = self.proc.try_read(res + off, 1)
        return raw[0] if raw else 0
