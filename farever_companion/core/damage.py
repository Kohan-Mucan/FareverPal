"""Read-only damage source: the game's own floating numbers.

``ui.comp.DamageDisplay`` floaties carry a ``st.skill.DamageResult`` payload.
Offset policy: fields resolve by name via runtime field tables; hlboot constants are the fallback.
"""
from __future__ import annotations

import os
import struct
import sys
import time

from ..constants import (
    TO_NAME,
    OFF_DPS_DISPLAY_DAMAGE, OFF_DPS_RESULT_BASE_SKILL, OFF_DPS_RESULT_SERVER_SOURCE,
    OFF_DPS_RESULT_TARGET, OFF_DPS_RESULT_AMOUNT, OFF_DPS_RESULT_KILL,
    OFF_DPS_RESULT_CRITICAL, OFF_DPS_SKILL_ID, OFF_DPS_SKILL_OWNER,
    OFF_DPS_DISPLAY_TYPE_NAME, OFF_DPS_HEAL_DISPLAY_TYPE_NAME, OFF_DPS_RESULT_TYPE_NAME,
    HOBJ,
)
from .damage_events import DamageEvent, K_DAMAGE, K_HEAL
from .hl import Hl, is_ptr
from .proc import Proc, ProcError

# --- cluster-scan tuning ---------------------------------------------------
# GC is non-moving/size-segregated; padded anchors cover spawn pages.
RANGE_PAD = 1 << 18          # 256 KiB around each anchor
RANGE_MERGE_GAP = 1 << 20    # merge ranges closer than 1 MiB
MAX_SCAN_BYTES = 512 << 20   # hard cap on per-poll scanned bytes
MAX_ANCHORS = 8000           # cap on accumulated anchor addresses


def _utf16z(s: str) -> bytes:
    return s.encode("utf-16-le") + b"\x00\x00"


def cluster_ranges(addrs: list[int], pad: int = RANGE_PAD,
                   merge_gap: int = RANGE_MERGE_GAP) -> list[tuple[int, int]]:
    """Collapse instance addresses into merged ``(base, len)`` scan ranges."""
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
    """Intersect scan ranges with the committed readable regions."""
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


def _region_ranges(proc: Proc, ro_only: bool = False,
                   min_size: int = 0) -> list[tuple[int, int]]:
    """Committed readable regions of `proc` as (base, len)."""
    out: list[tuple[int, int]] = []
    try:
        for base, size, _prot, writable in proc.regions():
            if size < min_size:
                continue
            if ro_only and writable:
                continue
            out.append((base, size))
    except Exception:
        return []
    return out


# HL objects live in small committed PAGE_READWRITE chunks; bounding hunts there keeps re-derives ~1s.
MAX_RW_REGION = 0x2000000      # 32 MiB per region (matches the locate fast path)


def _small_rw_ranges(proc: Proc, cap: int = MAX_RW_REGION) -> list[tuple[int, int]]:
    """Committed PAGE_READWRITE regions up to `cap` bytes, as (base, len)."""
    try:
        return [(b, s) for b, s, p, w in proc.regions()
                if w and p == 0x04 and s <= cap]
    except Exception:
        return []
def find_type_by_name(proc: Proc, hl: Hl, class_name: str,
                      anchor_addr: int | None = None,
                      max_hits: int = 16) -> int | None:
    """Locate an hl_type in < 2 seconds by filtering out cold multi-GB heaps."""
    if not proc.has_scan:
        return None
    needle = _utf16z(class_name)

    try:
        all_regs = proc.regions()
    except (ProcError, OSError):
        all_regs = []

    names = []
    # 1. Anchor window first (<0.1s).
    if anchor_addr and all_regs:
        span = 0x8000000
        anchor_regs = [(b, s) for b, s, _, w in all_regs if w and b + s > anchor_addr - span and b < anchor_addr + span]
        if anchor_regs:
            names = proc.find_bytes_in(needle, anchor_regs, align=2, max_hits=4)

    # 2. Fast path: small RW metadata regions (skips cold multi-GB heap).
    if not names and all_regs:
        small_rw = [(b, s) for b, s, p, w in all_regs if w and p == 0x04 and s <= 0x2000000]
        if small_rw:
            names = proc.find_bytes_in(needle, small_rw, align=2, max_hits=4)

    # 3. Fallback: all RW, then all readable.
    if not names:
        names = proc.find_bytes(needle, align=2, rw_only=True, max_hits=max_hits)
    if not names:
        names = proc.find_bytes(needle, align=2, rw_only=False, max_hits=max_hits)
    if not names:
        return None

    for name_addr in names:
        try:
            if hl._read_utf16(name_addr) != class_name:
                continue
        except (ProcError, OSError):
            continue

        # Back-reference window: writable first, then all readable (type objects may live in read-only image).
        span = 0x8000000
        near_rw = ([(b, s) for b, s, _, w in all_regs
                    if w and b + s > name_addr - span and b < name_addr + span]
                   if all_regs else [])
        near_all = ([(b, s) for b, s, _, _ in all_regs
                     if b + s > name_addr - span and b < name_addr + span]
                    if all_regs else [])

        for ranges in (near_rw, near_all):
            if not ranges or (ranges is near_all and near_all == near_rw):
                continue
            for ref in proc.find_qword_in(name_addr, ranges, max_hits=16):
                type_obj = ref - TO_NAME
                for tref in proc.find_qword_in(type_obj, ranges, max_hits=16):
                    cand = tref - 8
                    try:
                        if (hl.i32(cand) == HOBJ
                                and hl.type_name(cand) == class_name):
                            return cand
                    except (ProcError, OSError):
                        continue

        # Last resort: full-process writable-only qword scans
        for ref in proc.find_qword(name_addr, rw_only=True, max_hits=16):
            type_obj = ref - TO_NAME
            for tref in proc.find_qword(type_obj, rw_only=True, max_hits=16):
                cand = tref - 8
                try:
                    if (hl.i32(cand) == HOBJ
                            and hl.type_name(cand) == class_name):
                        return cand
                except (ProcError, OSError):
                    continue
    return None


class DamageReader:
    """Poll live ``DamageDisplay`` objects for damage/heal events (read-only)."""

    CLASS = OFF_DPS_DISPLAY_TYPE_NAME
    HEAL_CLASS = OFF_DPS_HEAL_DISPLAY_TYPE_NAME
    RESULT_CLASS = OFF_DPS_RESULT_TYPE_NAME

    # Reflection names per semantic; a runtime name match beats the constant.
    _DISPLAY_FIELDS = ("dmg", "result", "damageResult", "damage", "payload")
    _AMOUNT_FIELDS = ("_amount", "amount", "dmg", "value", "damage")
    _KILL_FIELDS = ("_kill", "kill", "isKill", "killed")
    _CRIT_FIELDS = ("_critical", "critical", "isCritical", "crit")
    _SOURCE_FIELDS = ("serverSource", "source", "_source", "caster", "owner",
                      "serverSourceUnit")
    _TARGET_FIELDS = ("target", "targetUnit", "_target", "unit", "targetHero")
    _SKILL_FIELDS = ("baseSkill", "skill", "skillId", "castSkill")
    _KIND_FIELDS = ("kind", "skillId", "id", "name", "_kind")
    _NAME_FIELDS = ("name", "skillName", "displayName", "title", "caption")
    # serverSource is often empty; caster then lives on the baseSkill object.
    _CASTER_FIELDS = ("owner", "caster", "source", "unit", "hero")

    def __init__(self, proc: Proc, hl: Hl, my_hero: int | None = None,
                 type_hint: str | None = None,
                 result_type_hint: str | None = None):
        """Type hints skip the full-process locate (class-verified, fallback to scan)."""
        self.proc = proc
        self.hl = hl
        self.my_hero = my_hero
        self._type_ptr: int | None = None
        self._heal_type_ptr: int | None = None
        self._type_hint: str | None = (type_hint
                                       or os.environ.get("FAREVER_DMG_TYPE"))
        # st.skill.DamageResult payload; trusted only when its header matches.
        self._result_type_ptr: int | None = None
        self._result_type_hint: str | None = (
            result_type_hint or os.environ.get("FAREVER_DMG_RESULT_TYPE"))
        # Dedup by content signature; GC recycles freed slots under combat.
        self._sig: dict[int, tuple] = {}
        self._scan_ranges: list[tuple[int, int]] = []
        self._ranges_ready: bool = False
        self._last_count: int = 0
        self._anchor_addrs: set[int] = set()
        self._last_full_derive: float = 0.0  # rate-gate the full-RW fallback
        self._last_poll_n: int = 0          # displays found on the last poll
        # reflection-first offsets: semantic -> (offset, used_reflection)
        self._offs: dict[str, tuple[int | None, bool]] = {}
        self._res_offsets: dict[int, dict[str, tuple[int | None, bool]]] = {}
        self._kind_offsets: dict[int, tuple[int | None, bool]] = {}
        self._caster_offsets: dict[int, tuple[int | None, bool]] = {}
        # type ptrs verified as hashlink String (guards wrong-offset decodes).
        self._str_ok: set[int] = set()
        self._str_bad: set[int] = set()
        self._skill_name_cache: dict[int, str] = {}
        # Sibling combat payload classes adopted at runtime.
        self._payload_types: dict[int, str] = {}
        # per-poll drop diagnostics (UI status line + debug log)
        self._diag: dict = {"displays": 0, "res": 0, "ok": 0, "no_res": 0,
                            "bad_hdr": 0, "bad_amount": 0, "bad_skill": 0,
                            "used_refl": False}
        self._last_log_ts: float = 0.0

    # --- calibration gate -------------------------------------------------
    @staticmethod
    def calibrated() -> bool:
        return None not in (OFF_DPS_DISPLAY_DAMAGE, OFF_DPS_RESULT_AMOUNT,
                            OFF_DPS_RESULT_TARGET)

    @staticmethod
    def _debug() -> bool:
        return os.environ.get("FAREVER_DMG_DEBUG", "").strip() not in ("", "0", "false")

    def _log(self, msg: str) -> None:
        if self._debug():
            print(f"[dmg-scan] {msg}", file=sys.stderr, flush=True)

    # --- reflection-first field resolution ---------------------------------
    def _first_field(self, type_ptr: int | None, names: tuple[str, ...],
                     fallback: int | None) -> tuple[int | None, bool]:
        if type_ptr:
            for n in names:
                try:
                    off = self.hl.field_offset(type_ptr, n)
                except (ProcError, OSError):
                    continue
                if off:
                    return off, True
        return fallback, False

    def _display_offset(self) -> tuple[int | None, bool]:
        off = self._offs.get("dmg")
        if off is None:
            off = self._first_field(self._type_ptr, self._DISPLAY_FIELDS,
                                    OFF_DPS_DISPLAY_DAMAGE)
            self._offs["dmg"] = off
        return off

    def _result_offsets(self, res_type: int | None) -> dict[str, tuple[int | None, bool]]:
        """Field offsets for the header-verified DamageResult type."""
        if res_type in self._res_offsets:
            return self._res_offsets[res_type]
        offs = {
            "amount": self._first_field(res_type, self._AMOUNT_FIELDS, OFF_DPS_RESULT_AMOUNT),
            "kill": self._first_field(res_type, self._KILL_FIELDS, OFF_DPS_RESULT_KILL),
            "crit": self._first_field(res_type, self._CRIT_FIELDS, OFF_DPS_RESULT_CRITICAL),
            "source": self._first_field(res_type, self._SOURCE_FIELDS, OFF_DPS_RESULT_SERVER_SOURCE),
            "target": self._first_field(res_type, self._TARGET_FIELDS, OFF_DPS_RESULT_TARGET),
            "skill": self._first_field(res_type, self._SKILL_FIELDS, OFF_DPS_RESULT_BASE_SKILL),
        }
        self._res_offsets[res_type] = offs
        if self._debug():
            self._log(f"DamageResult type@{'0x%x' % res_type if res_type else '?'}: "
                      + " ".join(f"{k}={v[0] and hex(v[0])}{'*' if v[1] else ''}"
                                 for k, v in offs.items()))
        return offs

    def _kind_offset(self, skill_type: int) -> tuple[int | None, bool]:
        if skill_type in self._kind_offsets:
            return self._kind_offsets[skill_type]
        off = self._first_field(skill_type, self._KIND_FIELDS, OFF_DPS_SKILL_ID)
        self._kind_offsets[skill_type] = off
        return off

    # --- type locate ------------------------------------------------------
    @property
    def type_ptr(self) -> int | None:
        return self._type_ptr

    def find_type(self) -> int | None:
        """Locate the DamageDisplay hl_type once (background thread)."""
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
        tp = find_type_by_name(self.proc, self.hl, self.CLASS)
        if tp is not None:
            self._log(f"located type {tp:#x}")
            self._type_ptr = tp
        else:
            self._log("type NOT found this pass")
        return tp

    def find_result_type(self) -> int | None:
        """Locate the st.skill.DamageResult hl_type once (required before poll trusts payloads)."""
        if self._result_type_ptr is not None or not self.proc.has_scan:
            return self._result_type_ptr
        if self._result_type_hint:
            try:
                cand = int(self._result_type_hint, 0)
                if self.hl.type_name(cand) == self.RESULT_CLASS:
                    self._log(f"using result-type hint {cand:#x}")
                    self._result_type_ptr = cand
                    return cand
                self._log("result-type hint stale (class mismatch); scanning")
            except (ProcError, OSError, ValueError):
                self._log("result-type hint unreadable; scanning")
            self._result_type_hint = None
        rt = find_type_by_name(self.proc, self.hl, self.RESULT_CLASS, anchor_addr=self._type_ptr)
        if rt is not None:
            self._log(f"located result type {rt:#x}")
            self._result_type_ptr = rt
        else:
            self._log("result type NOT found this pass")
        return rt

    def _caster_offset(self, skill_type: int | None) -> tuple[int | None, bool]:
        """Offset of the caster on the Skill/Status class."""
        if skill_type in self._caster_offsets:
            return self._caster_offsets[skill_type]
        off = self._first_field(skill_type, self._CASTER_FIELDS, OFF_DPS_SKILL_OWNER)
        self._caster_offsets[skill_type] = off
        return off

    def _is_string_obj(self, cand: int) -> bool:
        """True when `cand` is a hashlink String instance."""
        try:
            t = self.hl.ptr(cand)
        except (ProcError, OSError):
            return False
        if not is_ptr(t):
            return False
        if t in self._str_ok:
            return True
        if t in self._str_bad:
            return False
        try:
            nm = self.hl.type_name(t) or ""
        except (ProcError, OSError):
            nm = ""
        (self._str_ok if nm.endswith("String") else self._str_bad).add(t)
        return nm.endswith("String")

    def _candidate_strings(self, sk: int, st: int) -> list[int]:
        """Pointers that could hold a String at the skill kind/name offsets."""
        offs: list[int] = []
        koff = self._kind_offset(st)[0]
        if koff:
            offs.append(koff)
        for nm in self._NAME_FIELDS:
            try:
                o = self.hl.field_offset(st, nm)
            except (ProcError, OSError):
                o = None
            if o:
                offs.append(o)
        out: list[int] = []
        for off in offs:
            try:
                p = self.hl.ptr(sk + off)
            except (ProcError, OSError):
                continue
            if not is_ptr(p):
                continue
            out.append(p)
            try:
                p2 = self.hl.ptr(p)
            except (ProcError, OSError):
                p2 = 0
            if is_ptr(p2):
                out.append(p2)
        return out

    def _skill_name(self, sk: int) -> str:
        """Display name from a baseSkill object, with caching."""
        if sk in self._skill_name_cache:
            return self._skill_name_cache[sk]

        # 1. Direct offset path.
        if OFF_DPS_SKILL_ID:
            try:
                s_ptr = self.hl.ptr(sk + OFF_DPS_SKILL_ID)
                if is_ptr(s_ptr):
                    s = self.hl.hl_string(s_ptr)
                    if s and len(s) <= 64 and s.isascii() and s.isprintable():
                        self._skill_name_cache[sk] = s
                        return s
            except (ProcError, OSError):
                pass

        # 2. Reflection fallback.
        try:
            st = self.hl.ptr(sk)
        except (ProcError, OSError):
            return "?"
        if not is_ptr(st):
            return "?"
        cands = self._candidate_strings(sk, st)
        for cand in cands:
            if not self._is_string_obj(cand):
                continue
            try:
                s = self.hl.hl_string(cand)
            except (ProcError, OSError):
                continue
            if s and len(s) <= 64 and s.isascii() and s.isprintable():
                self._skill_name_cache[sk] = s
                return s
        for cand in cands:
            try:
                s = self.hl.hl_string(cand)
            except (ProcError, OSError):
                continue
            if s and len(s) <= 64 and s.isascii() and s.isprintable():
                self._skill_name_cache[sk] = s
                return s
        return "?"

    def _read_caster(self, res: int, offs: dict) -> int:
        """serverSource if set, else the baseSkill object's owner/caster."""
        source = self._ptr_or(res, offs["source"][0])
        if source:
            return source
        sk_off = offs["skill"][0]
        if not sk_off:
            return 0
        try:
            sk = self.hl.ptr(res + sk_off)
        except (ProcError, OSError):
            return 0
        if not is_ptr(sk):
            return 0
        try:
            st = self.hl.ptr(sk)
        except (ProcError, OSError):
            return 0
        if not is_ptr(st):
            return 0
        co, _ = self._caster_offset(st)
        return self._ptr_or(sk, co)


    def _hunt_instances(self, tp: int, heal_tp: int | None,
                        allow_full: bool) -> set[int]:
        """Qword-hits for `tp` (+ heal class); whole-RW sweep is a rate-gated fallback."""
        small = _small_rw_ranges(self.proc)
        insts: set[int] = set()
        if small:
            try:
                insts = set(self.proc.find_qword_in(tp, small, max_hits=4096))
                if heal_tp:
                    insts.update(self.proc.find_qword_in(heal_tp, small,
                                                         max_hits=4096))
            except (ProcError, OSError):
                insts = set()
        if insts or not allow_full:
            return insts
        now = time.monotonic()
        if now - self._last_full_derive < 120.0:
            return insts
        self._last_full_derive = now
        try:
            insts = set(self.proc.find_qword(tp, rw_only=True, max_hits=4096))
            if heal_tp:
                insts.update(self.proc.find_qword(heal_tp, rw_only=True,
                                                  max_hits=4096))
        except (ProcError, OSError):
            insts = set()
        return insts

    # --- cluster mapping --------------------------------------------------
    def refresh_ranges(self) -> int:
        """Re-derive the GC-page ranges the displays cluster into (~1s, maintenance thread)."""
        if not self.calibrated() or not self.proc.has_scan:
            return 0
        tp = self.find_type()
        if tp is None:
            return 0
        try:
            insts = self._hunt_instances(tp, self._heal_type_ptr,
                                         allow_full=not self._anchor_addrs)
        except (ProcError, OSError):
            return self._last_count
        # Map the current cluster only; pooled displays move pages each fight.
        # On an empty hunt keep the previous map (brief pool reset between pulls).
        if insts:
            self._anchor_addrs = set(insts)
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

    @property
    def ready_to_poll(self) -> bool:
        """True when both display and result types are located."""
        return self._type_ptr is not None and self._result_type_ptr is not None

    def _regions_or_empty(self) -> list[tuple]:
        try:
            return self.proc.regions()
        except (ProcError, OSError):
            return []

    # --- poll -------------------------------------------------------------
    def poll(self) -> list[DamageEvent]:
        """Fast range-bounded scan: an event per display whose number changed."""
        tp = self._type_ptr
        if tp is None or not self.calibrated():
            return []
        # Gate: trust the `dmg` slot only once the result class is located.
        if self._result_type_ptr is None:
            self.find_result_type()
            if self._result_type_ptr is None:
                return []
        if self._scan_ranges:
            try:
                d_insts = [(disp, False) for disp in self.proc.find_qword_in(tp, self._scan_ranges, max_hits=4096)]
            except (ProcError, OSError):
                d_insts = []
            h_insts = []
            if self._heal_type_ptr:
                try:
                    h_insts = [(disp, True) for disp in self.proc.find_qword_in(self._heal_type_ptr, self._scan_ranges, max_hits=4096)]
                except (ProcError, OSError):
                    h_insts = []
        else:
            # Fallback (pre-map): bounded small-RW hunt, never a full-heap sweep.
            small = _small_rw_ranges(self.proc)
            try:
                d_raw = (self.proc.find_qword_in(tp, small, max_hits=512)
                         if small else [])
                d_insts = [(disp, False) for disp in d_raw]
            except (ProcError, OSError):
                d_insts = []
            h_insts = []
            if self._heal_type_ptr and small:
                try:
                    h_raw = self.proc.find_qword_in(self._heal_type_ptr,
                                                    small, max_hits=512)
                    h_insts = [(disp, True) for disp in h_raw]
                except (ProcError, OSError):
                    h_insts = []
            if d_insts or h_insts:
                new_anchors = [disp for disp, _ in d_insts + h_insts]
                # Current-only anchors.
                self._anchor_addrs = set(new_anchors)
                self._scan_ranges = clip_ranges(cluster_ranges(sorted(self._anchor_addrs)), self._regions_or_empty())
                self._ranges_ready = bool(self._scan_ranges)
        insts = d_insts + h_insts
        diag = {"displays": len(insts), "res": 0, "ok": 0, "no_res": 0,
                "bad_hdr": 0, "bad_amount": 0, "bad_skill": 0,
                "used_refl": bool(self._offs.get("dmg", (None, False))[1])}
        events: list[DamageEvent] = []
        cur: dict[int, tuple] = {}
        has_offsets = False
        dmg_off, used_refl = self._display_offset()
        diag["used_refl"] = diag["used_refl"] or used_refl
        for disp, is_heal in insts:
            try:
                res = self.hl.ptr(disp + (dmg_off or 0)) if dmg_off else 0
            except (ProcError, OSError):
                res = 0
            if not is_ptr(res):
                diag["no_res"] += 1
                continue
            diag["res"] += 1
            # Header verify: only typed payloads decode; sibling combat classes adopted on sane trial decode.
            try:
                hdr = self.hl.ptr(res)
            except (ProcError, OSError):
                hdr = 0
            if not is_ptr(hdr):
                diag["bad_hdr"] += 1
                continue
            if hdr != self._result_type_ptr and hdr not in self._payload_types:
                try:
                    nm = self.hl.type_name(hdr) or ""
                except (ProcError, OSError):
                    nm = ""
                is_combat = (nm.startswith("st.skill.")
                             and (nm.endswith("Result") or "Heal" in nm
                                  or "Shield" in nm))
                if not is_combat:
                    diag["bad_hdr"] += 1
                    continue
                trial_offs = self._result_offsets(hdr)
                trial = self._read_event(res, trial_offs, is_heal=is_heal)
                if trial is None or not self._plausible(trial):
                    diag["bad_hdr"] += 1
                    continue
                self._payload_types[hdr] = nm
            offs = self._result_offsets(hdr)
            diag["used_refl"] = diag["used_refl"] or any(
                refl for _off, refl in offs.values())
            ev = self._read_event(res, offs, is_heal=is_heal)
            if ev is None:
                diag["bad_amount"] += 1
                continue
            if not self._plausible(ev):
                diag["bad_skill"] += 1
                continue
            if ev.skill == "?":
                diag["bad_skill"] += 1
            has_offsets = True
            sig = (res, int(abs(ev.amount)), ev.skill, ev.crit, ev.kill, is_heal)
            cur[disp] = sig
            if self._sig.get(disp) != sig:
                events.append(ev)
        self._sig = cur
        self._last_poll_n = len(insts)
        diag["ok"] = len(events)
        self._diag = diag
        if self._debug():
            now = time.monotonic()
            if now - self._last_log_ts >= 1.0:
                self._last_log_ts = now
                self._log(f"poll: {diag['displays']} displays, {diag['res']} results, "
                          f"{diag['ok']} events | drops: no_res={diag['no_res']} "
                          f"bad_hdr={diag['bad_hdr']} bad_amount={diag['bad_amount']} "
                          f"bad_skill={diag['bad_skill']} "
                          f"refl={'y' if diag['used_refl'] else 'n'}")
        return events

    @staticmethod
    def _plausible(ev: DamageEvent) -> bool:
        # Floor of 1.0 kills recycled-object subnormal noise; "?" skill never kills the event.
        return 1.0 <= abs(ev.amount) < 1e9

    def _read_event(self, res: int,
                    offs: dict[str, tuple[int | None, bool]],
                    is_heal: bool = False) -> DamageEvent | None:
        """Decode one st.skill.DamageResult into a DamageEvent (or None)."""
        amount_off = offs["amount"][0]
        raw = self.proc.try_read(res + amount_off, 8) if amount_off else None
        amount = 0.0
        if raw:
            amount = struct.unpack("<d", raw)[0]
            if amount != amount or abs(amount) > 1e10:      # NaN / not an f64
                try:
                    amount = float(self.hl.i32(res + amount_off))
                except (ProcError, OSError):
                    amount = 0.0
        if not raw or amount == 0.0:
            return None
        skill = "?"
        try:
            sk_off = offs["skill"][0]
            sk = self.hl.ptr(res + sk_off) if sk_off else 0
            if is_ptr(sk):
                skill = self._skill_name(sk)
        except (ProcError, OSError):
            skill = "?"
        crit = bool(self._flag(res, offs["crit"][0]))
        kill = bool(self._flag(res, offs["kill"][0]))
        source = self._read_caster(res, offs)
        target = self._ptr_or(res, offs["target"][0])
        incoming = bool(self.my_hero and target == self.my_hero)
        kind = K_HEAL if (is_heal or amount < 0) else K_DAMAGE
        return DamageEvent(amount=amount, skill=skill, crit=crit, kill=kill,
                           source_addr=source, target_addr=target,
                           incoming=incoming, kind=kind, t=time.time())

    def _ptr_or(self, obj: int, off: int | None) -> int:
        if not off:
            return 0
        try:
            p = self.hl.ptr(obj + off)
            return p if is_ptr(p) else 0
        except (ProcError, OSError):
            return 0

    def _flag(self, obj: int, off: int | None) -> int:
        # Adjacent 1-byte bools; single-byte read avoids bleeding the neighbour.
        if off is None:
            return 0
        try:
            raw = self.proc.try_read(obj + off, 1)
        except (ProcError, OSError):
            return 0
        return raw[0] if raw else 0