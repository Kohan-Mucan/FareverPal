"""Rift Schedule & Active State Tracker.

Rifts spawn on an hourly schedule starting at :00 of every hour.
A 15-minute warning window opens at :45 of every hour.
A 2-minute closing window runs from :00 to :02 of every hour.
Calculates state and countdowns using local clock & POI geo data.
Integrates live Announcements for accurate out-of-range info.
"""
from __future__ import annotations

import time
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING
from ..data import dungeons, names
from ..geo import pois as geo_pois

if TYPE_CHECKING:
    from .model import LiveModel

log = logging.getLogger(__name__)

@dataclass
class RiftStatus:
    state: str                 # "ACTIVE", "WARNING", "COUNTDOWN", "CLOSING"
    secs_until_start: int       # seconds until :00 start time
    secs_until_warning: int     # seconds until :45 warning time
    formatted_time: str         # human-readable timer text
    status_label: str           # clean header/badge text (e.g. "RIFT IN 14m 20s")
    rift_name: str = ""         # Specific Rift name (e.g. "Abandoned Mines")
    poi_x: float | None = None
    poi_y: float | None = None
    poi_z: float | None = None
    in_range: bool = False
    at_any_rift: bool = False


# ── LCG
MASK32 = 0xFFFFFFFF
SIGN32 = 0x80000000

def _i32(x: int) -> int:
    x &= MASK32
    return x - 0x100000000 if x & SIGN32 else x

def _u32(x: int) -> int:
    return x & MASK32

def _mul32(a: int, b: int) -> int:
    return _i32(a * b)

def _add32(a: int, b: int) -> int:
    return _i32(a + b)

def _xor32(a: int, b: int) -> int:
    return _i32(a ^ b)

def _or32(a: int, b: int) -> int:
    return _i32(a | b)

def _shl32(a: int, n: int) -> int:
    return _i32(a << n)

def _shr32(a: int, n: int) -> int:
    return a >> n

def _ushr32(a: int, n: int) -> int:
    return (_u32(a) >> n)

def _hxd_hash(n: int, seed: int | None = None) -> int:
    seed = 5381 if seed is None else seed
    n = _i32(n)
    n = _mul32(n, 3432918353)
    n = _or32(_shl32(n, 15), _ushr32(n, 17))
    n = _mul32(n, 461845907)
    seed = _xor32(seed, n)
    seed = _or32(_shl32(seed, 13), _ushr32(seed, 19))
    n = _add32(_mul32(seed, 5), 3864292196)
    n = _xor32(n, _shr32(n, 16))
    n = _mul32(n, 2246822507)
    n = _xor32(n, _shr32(n, 13))
    n = _mul32(n, 3266489909)
    n = _xor32(n, _shr32(n, 16))
    return n

_CACHED_RIFT_POIS = None

def _get_rift_pois():
    global _CACHED_RIFT_POIS
    if _CACHED_RIFT_POIS is None:
        _CACHED_RIFT_POIS = [p for p in geo_pois.load_pois() if p.sub_kind == "rift"]
    return _CACHED_RIFT_POIS

def predict_rift_zone_id(timestamp: float) -> str | None:
    """Predicts hourly rift zone ID using the game's exact hxd.Rand LCG algorithm."""
    import math
    boundary = int(math.floor((timestamp + 900.0) / 3600.0)) * 3600
    seed = _i32(boundary)
    seed2 = _hxd_hash(seed, None)
    if seed == 0: seed = 1
    if seed2 == 0: seed2 = 1
    
    s1 = _i32(36969 * (seed & 65535) + _shr32(seed, 16))
    s2 = _i32(18000 * (seed2 & 65535) + _shr32(seed2, 16))
    combined = _i32((s1 << 16) + s2) & 0x3FFFFFFF
    
    r_pois = _get_rift_pois()
    if not r_pois:
        return None
    idx = combined % len(r_pois)
    return r_pois[idx].zone or r_pois[idx].id


class RiftTracker:
    def __init__(self, model: LiveModel | None = None):
        self.model = model
        self._was_in_rift = False

    def _resolve_rift_info(self, ann_zone_id: str | None = None) -> tuple[str, str, str, float | None, float | None, float | None, bool, bool]:
        """Resolves active Rift name and position from scene memory, chat announcements, or LCG prediction."""
        subzone = ""
        main_zone = ""
        px, py, pz = None, None, None
        in_range = False
        at_any_rift = False

        is_attached = self.model is not None and getattr(self.model, "proc", None) is not None
        if is_attached:
            try:
                pbase = self.model.player_addr
                for e in self.model.scene.elements(pbase):
                    eid = (e.elem_id or "").lower()
                    estate = (e.state or "").lower()
                    if "rift_entrance" in eid or "riftportal" in eid:
                        at_any_rift = True
                        if estate in ("pendingevent", "enabled", "active", "waiting"):
                            px, py, pz = e.x, e.y, e.z
                            in_range = True
                            break

                if not ann_zone_id:
                    live_zone = self.model.scene.get_live_worldevent_rift_zone(pbase)
                    if live_zone:
                        ann_zone_id = live_zone
            except Exception:
                pass

        if not ann_zone_id:
            now_ts = time.time()
            struct_utc = time.gmtime(now_ts)
            curr_min = struct_utc.tm_min
            target_ts = now_ts + (3600 - (curr_min * 60 + struct_utc.tm_sec)) if curr_min >= 3 else now_ts
            ann_zone_id = predict_rift_zone_id(target_ts)

        def clean_name(n: str | None) -> str:
            if not n: return ""
            if n.lower().startswith("rift "):
                return n[5:].strip()
            return n

        def resolve_id(tid: str | None) -> str | None:
            if not tid: return None
            res = names.zone_name(tid)
            if res:
                return res
            for prefix in ("Z1_", "Z2_", "Z3_"):
                pre_tid = f"{prefix}{tid}"
                res = names.zone_name(pre_tid)
                if res:
                    return res
            return names.humanize(tid)

        resolved_name = clean_name(resolve_id(ann_zone_id)) if ann_zone_id else ""

        r_pois = _get_rift_pois()
        matched_poi = None

        if in_range and px is not None and py is not None and r_pois:
            matched_poi = min(r_pois, key=lambda p: p.dist2d(px, py))
        elif ann_zone_id and r_pois:
            target = ann_zone_id.lower()
            for p in r_pois:
                p_zone = (p.zone or "").lower()
                p_id = (p.id or "").lower()
                if target == p_zone or target in p_id:
                    matched_poi = p
                    break
            if not matched_poi:
                for p in r_pois:
                    p_zone = (p.zone or "").lower()
                    if target in p_zone or p_zone in target:
                        matched_poi = p
                        break

        if matched_poi:
            px, py, pz = matched_poi.x, matched_poi.y, matched_poi.z
            p_zone_id = matched_poi.zone
            is_krisomal = "krisomal" in (ann_zone_id or p_zone_id or "").lower()
            subzone = resolved_name or clean_name(resolve_id(p_zone_id)) or matched_poi.name or ("Outer Ruins of Tiocha" if is_krisomal else "Talitha Falls")
            subzone = clean_name(subzone)
            main_zone = "Krisomal" if is_krisomal else "Enripit"
        elif resolved_name:
            subzone = resolved_name
            res_lower = (ann_zone_id or resolved_name).lower()
            if "krisomal" in res_lower or "tiocha" in res_lower or "z2_" in res_lower:
                main_zone = "Krisomal"
            else:
                main_zone = "Enripit"
        
        return subzone, subzone, main_zone, px, py, pz, in_range, at_any_rift


    def get_status(self) -> RiftStatus:
        now_ts = time.time()
        struct_utc = time.gmtime(now_ts)
        curr_min = struct_utc.tm_min
        curr_sec = struct_utc.tm_sec
        secs_into_hour = curr_min * 60 + curr_sec
        secs_until_start = 3600 - secs_into_hour

        state_str = "CLOSING" if curr_min < 3 else ("WARNING" if curr_min >= 45 else "SCHEDULED")

        ann = None
        ann_zone = None
        is_attached = self.model is not None and getattr(self.model, "proc", None) is not None
        if state_str != "SCHEDULED" and is_attached:
            ann = self.model.announcements.update()
            ann_zone = ann.zone_id if ann else None

        in_rift = False
        if is_attached:
            try:
                in_rift = self.model.is_in_rift()
            except Exception:
                in_rift = False

        if in_rift and not self._was_in_rift:
            if self.model and self.model.announcements:
                self.model.announcements.clear()
                ann = None
                ann_zone = None
        self._was_in_rift = in_rift

        if in_rift:
            rift_name, subzone, main_zone, px, py, pz, in_range, at_any_rift = self._resolve_rift_info(ann_zone)
            return RiftStatus(
                state="ACTIVE",
                secs_until_start=0,
                secs_until_warning=0,
                formatted_time="LIVE",
                status_label=subzone or "Rift",
                rift_name=subzone or "Rift",
                poi_x=px, poi_y=py, poi_z=pz,
                in_range=in_range,
                at_any_rift=at_any_rift
            )

        def _fmt_time(total_secs: int) -> str:
            m = total_secs // 60
            s = total_secs % 60
            if m > 0 and s > 0: return f"{m}m {s}s"
            elif m > 0: return f"{m}"
            else: return f"{s}s"

        rift_name, subzone, main_zone, px, py, pz, in_range, at_any_rift = self._resolve_rift_info(ann_zone)

        formatted = _fmt_time(180 - secs_into_hour) if curr_min < 3 else _fmt_time(secs_until_start)

        prefix = f"Rift · {subzone}" if subzone else "Rift"
        if state_str == "WARNING":
            status_label = f"{prefix} Starting In"
        elif state_str == "CLOSING":
            status_label = f"{prefix} Closing In"
        else:
            status_label = f"{prefix}" if subzone else "Rift out of active time"

        return RiftStatus(
            state=state_str,
            secs_until_start=secs_until_start,
            secs_until_warning=max(0, (45 * 60) - secs_into_hour),
            formatted_time=formatted,
            status_label=status_label,
            rift_name=subzone or "Rift",
            poi_x=px, poi_y=py, poi_z=pz,
            in_range=in_range,
            at_any_rift=at_any_rift
        )
