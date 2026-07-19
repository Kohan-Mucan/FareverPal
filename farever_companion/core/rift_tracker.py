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


class RiftTracker:
    def __init__(self, model: LiveModel | None = None):
        self.model = model
        self._was_in_rift = False  # track previous in_rift state

    def _resolve_rift_info(self, ann_zone_id: str | None = None) -> tuple[str, str, str, float | None, float | None, float | None, bool]:
        """Try reading active Rift name, subzone, and main zone from memory, announcements, & geo_pois."""
        subzone = ""
        main_zone = ""
        px, py, pz = None, None, None
        in_range = False

        if self.model is not None:
            try:
                pbase = self.model.player_addr
                # 1. Memory check: inspect rift portal elements in scene
                for e in self.model.scene.elements(pbase):
                    eid = (e.elem_id or "").lower()
                    estate = (e.state or "").lower()
                    if "rift_entrance" in eid or "riftportal" in eid:
                        if estate in ("pendingevent", "enabled"):
                            px, py, pz = e.x, e.y, e.z
                            in_range = True
                            break

                # 2. Live check: read server-synced activeRift zone from WorldEvents ONLY if no chat announcement zone
                if not ann_zone_id:
                    live_zone = self.model.scene.get_live_worldevent_rift_zone(pbase)
                    if live_zone:
                        ann_zone_id = live_zone
            except Exception:
                pass

        def clean_name(n: str | None) -> str:
            if not n: return ""
            if n.lower().startswith("rift "):
                return n[5:].strip()
            return n

        # Try to resolve technical IDs
        def resolve_id(tid: str | None) -> str | None:
            if not tid: return None
            
            # Direct zone_name lookup (e.g. z1_enripit_falls -> Talitha Falls)
            res = names.zone_name(tid)
            if res:
                return res
                
            # Try prepending region prefixes if not present
            for prefix in ("Z1_", "Z2_", "Z3_"):
                pre_tid = f"{prefix}{tid}"
                res = names.zone_name(pre_tid)
                if res:
                    return res

            return names.humanize(tid)

        resolved_name = clean_name(resolve_id(ann_zone_id)) if ann_zone_id else ""


        r_pois = [p for p in geo_pois.load_pois() if p.sub_kind == "rift"]
        matched_poi = None

        # Match by proximity if in range
        if in_range and px is not None and py is not None:
            matched_poi = min(r_pois, key=lambda p: p.dist2d(px, py))
        
        # Match by technical ID if we have one (even if out of range)
        elif ann_zone_id:
            target = ann_zone_id.lower()
            # 1. Exact match on p.zone or substring in p.id
            for p in r_pois:
                p_zone = (p.zone or "").lower()
                p_id = (p.id or "").lower()
                if target == p_zone or target in p_id:
                    matched_poi = p
                    break
            
            # 2. Fuzzy match (e.g. 'enripit' matches 'Z1_Enripit_Falls')
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
            
            # PRIORITIZE: Announcement Resolved Name > Specific POI Zone Name > POI Label
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
        
        return subzone, subzone, main_zone, px, py, pz, in_range


    def get_status(self) -> RiftStatus:
        # ── Time state first, so we can skip memory reads when idle ──
        now_ts = time.time()
        struct_utc = time.gmtime(now_ts)
        curr_min = struct_utc.tm_min
        curr_sec = struct_utc.tm_sec
        secs_into_hour = curr_min * 60 + curr_sec
        secs_until_start = 3600 - secs_into_hour

        state_str = "CLOSING" if curr_min < 3 else ("WARNING" if curr_min >= 45 else "SCHEDULED")

        # Only poll announcements during WARNING or CLOSING windows.
        # During SCHEDULED (non-active) we skip the memory read entirely.
        ann = None
        ann_zone = None
        if state_str != "SCHEDULED" and self.model is not None:
            ann = self.model.announcements.update()
            ann_zone = ann.zone_id if ann else None

        in_rift = False
        if self.model is not None:
            try:
                in_rift = self.model.is_in_rift()
            except Exception:
                in_rift = False

        # ── Clear cache when zoned into the rift ──
        if in_rift and not self._was_in_rift:
            if self.model and self.model.announcements:
                self.model.announcements.clear()
                ann = None
                ann_zone = None
        self._was_in_rift = in_rift

        if in_rift:
            rift_name, subzone, main_zone, px, py, pz, in_range = self._resolve_rift_info(ann_zone)
            return RiftStatus(
                state="ACTIVE",
                secs_until_start=0,
                secs_until_warning=0,
                formatted_time="LIVE",
                status_label=subzone or "Rift",
                rift_name=subzone or "Rift",
                poi_x=px, poi_y=py, poi_z=pz,
                in_range=in_range
            )

        def _fmt_time(total_secs: int) -> str:
            m = total_secs // 60
            s = total_secs % 60
            if m > 0 and s > 0: return f"{m}m {s}s"
            elif m > 0: return f"{m}"
            else: return f"{s}s"

        rift_name, subzone, main_zone, px, py, pz, in_range = self._resolve_rift_info(ann_zone)

        formatted = _fmt_time(180 - secs_into_hour) if curr_min < 3 else _fmt_time(secs_until_start)

        prefix = f"Rift · {subzone}" if subzone else "Rift"
        if state_str == "WARNING":
            status_label = f"{prefix} Starting In"
        elif state_str == "CLOSING":
            status_label = f"{prefix} Closing In"
        else:
            status_label = "Rift out of active time"

        return RiftStatus(
            state=state_str,
            secs_until_start=secs_until_start,
            secs_until_warning=max(0, (45 * 60) - secs_into_hour),
            formatted_time=formatted,
            status_label=status_label,
            rift_name=subzone or "Rift",
            poi_x=px, poi_y=py, poi_z=pz,
            in_range=in_range
        )