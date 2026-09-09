"""Read live unit attributes (read-only).

Current Health: f64 on the *Attributes struct. Validated live 2026-05-25 for
player (HeroAttributes) and enemies (UnitAttributes) at +0xF0; same offset
across the Hero/Foe attribute subclasses. MaxHealth has no calibrated fallback
and callers still track the highest HP seen for DPS; Run Timer's attach-time
pristine check uses only a same-type field resolved by reflection.

2026-09-18 audit (the OFF_ELEM_FX lesson): +0xF0 is exactly the kind of
hardcoded layout slot that drifts, and a stale slot reads garbage that
`decode_health` usually catches — but a plausible-looking float would mis peg
HP bars. The offset now resolves by reflection (hp/life/health/currentHealth)
per attributes type first, cached by type pointer, with OFF_HEALTH as the
calibrated fallback; an unresolvable field keeps OFF_HEALTH, and a value that
fails `decode_health` stays 'unreadable' rather than trusted.

`decode_health` is the one judge of that field: both this module's single-unit
reader and the scene's batched unit scan go through it, so the HP the DPS
tracker diffs and the HP the Dungeon HUD pegs can never disagree.
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

# Field names probed (in order) for the health field, per attributes type.
# The documented name first, then the renames a patch is most likely to use.
_HEALTH_FIELDS = ("hp", "life", "health", "currentHealth")
_MAX_HEALTH_FIELDS = (
    "maxHp", "maxHealth", "maxLife", "healthMax", "lifeMax",
    "maximumHealth", "MaxHealth",
)

# The resolved offset lives on the Hl INSTANCE (hl._health_off_cache), keyed
# by the attributes object pointer: HL objects are per-attach, so a game
# restart naturally starts fresh, and the scene's batched unit walk stays a
# constant number of process reads (first walk pays, later walks are dict
# lookups — the read-budget test pins exactly that). Bounded for safety.
_HEALTH_OFF_CACHE_CAP = 100_000


def _cache_for(hl: Hl) -> dict[int, int]:
    cache = getattr(hl, "_health_off_cache", None)
    if cache is None:
        cache = {}
        try:
            hl._health_off_cache = cache
        except Exception:
            pass
    return cache


def health_offset(hl: Hl, attr_ptr: int) -> int:
    """Byte offset of the health field on this attributes object.

    Reflects the field by name on the object's concrete type (cached on the
    Hl instance per object pointer); falls back to the calibrated OFF_HEALTH
    when reflection can't resolve it. Never raises — a bad pointer just means
    the fallback."""
    if not attr_ptr:
        return OFF_HEALTH
    cache = _cache_for(hl)
    cached = cache.get(attr_ptr)
    if cached is not None:
        return cached
    off = OFF_HEALTH
    try:
        tp = hl.ptr(attr_ptr)
        if tp is not None:
            for nm in _HEALTH_FIELDS:
                o = hl.field_offset(tp, nm)
                if o:
                    off = o
                    break
    except Exception:
        off = OFF_HEALTH
    if len(cache) >= _HEALTH_OFF_CACHE_CAP:
        cache.clear()
    cache[attr_ptr] = off
    return off


# Sanity bound on a health field. The scene's batched unit scan and the
# single-unit `health()` reader share this decoder, so the HP the DPS tracker
# diffs and the HP the Dungeon HUD pegs can never disagree about what counts as
# a readable value - and a wild pointer that reads as garbage is `None` (0.0 on
# an `Entity`) rather than a bogus 10^300 peak.
MAX_HEALTH = 1e9


def decode_health(raw: bytes | None) -> float | None:
    """Current Health from a raw 8-byte field, or None if unreadable / insane.

    Shared by the single-unit read and the scene's batched unit scan, so the two
    can't disagree about what counts as a health value.
    """
    if raw is None or len(raw) < 8:
        return None
    hp = struct.unpack_from("<d", raw, 0)[0]
    if hp != hp or hp < 0 or hp > MAX_HEALTH:   # NaN or out of range
        return None
    return hp


def health(hl: Hl, unit: int) -> float | None:
    """Current Health of a unit (player or enemy), or None if unreadable."""
    if not unit:
        return None
    ua = hl.ptr(unit + OFF_UATTR)
    if not ua:
        return None
    off = health_offset(hl, ua)
    return decode_health(hl.proc.try_read(ua + off, 8))


def max_health(hl: Hl, unit: int) -> float | None:
    """Reflected maximum Health, or None when this build exposes no trusted field.

    Unlike :func:`health`, this deliberately has no calibrated fallback. A stale
    constant or the first HP observed after attach cannot prove that a boss was
    pristine; callers that gate timing on full health must fail closed instead.
    """
    if not unit:
        return None
    ua = hl.ptr(unit + OFF_UATTR)
    if not ua:
        return None
    try:
        tp = hl.ptr(ua)
        if tp is None:
            return None
        for name in _MAX_HEALTH_FIELDS:
            off = hl.field_offset(tp, name)
            if not off:
                continue
            value = decode_health(hl.proc.try_read(ua + off, 8))
            if value is not None and value > 0.0:
                return value
    except Exception:
        return None
    return None


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
