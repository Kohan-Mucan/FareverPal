"""Read live unit attributes (read-only).

Current Health: f64 at UnitAttributes+0xF0 (UnitAttributes = unit+0x3D0).
Validated live 2026-05-25 for player (HeroAttributes) and enemies
(UnitAttributes); same offset across the Hero/Foe attribute subclasses.
MaxHealth has no confirmed offset, so callers track the highest HP seen per unit.
"""
from __future__ import annotations

import struct

from .hl import Hl
from ..constants import OFF_UATTR, OFF_HEALTH, OFF_LEVEL_UNIT

# Live unit level - an i32 on the UNIT/GameObject struct, right after the
# attributes pointer (OFF_UATTR), shared across ent.Foe/ent.boss.*/ent.Hero
# (same GameObject layout). `OFF_LEVEL_UNIT` lives in constants.py; the
# attributes-struct variant below stays None until calibrated live.
OFF_LEVEL: int | None = None          # i32 offset on the ATTRIBUTES struct, or None


def health(hl: Hl, unit: int) -> float | None:
    """Current Health of a unit (player or enemy), or None if unreadable."""
    if not unit:
        return None
    ua = hl.ptr(unit + OFF_UATTR)
    if not ua:
        return None
    raw = hl.proc.try_read(ua + OFF_HEALTH, 8)
    if raw is None:
        return None
    hp = struct.unpack("<d", raw)[0]
    if hp != hp or hp < 0 or hp > 1e9:   # NaN or out of range
        return None
    return hp


def _read_i32(hl: Hl, addr: int) -> int | None:
    raw = hl.proc.try_read(addr, 4)
    if raw is None:
        return None
    v = struct.unpack("<i", raw)[0]
    return v if 0 <= v <= 999 else None   # plausible level range


def level(hl: Hl, unit: int) -> int | None:
    """Live level of a unit, or None if unreadable or uncalibrated."""
    if not unit:
        return None
    if OFF_LEVEL is not None:
        ua = hl.ptr(unit + OFF_UATTR)
        if ua:
            v = _read_i32(hl, ua + OFF_LEVEL)
            if v is not None:
                return v
    if OFF_LEVEL_UNIT is not None:
        v = _read_i32(hl, unit + OFF_LEVEL_UNIT)
        if v is not None:
            return v
    return None
