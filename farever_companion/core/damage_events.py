"""DamageEvent: unit of damage input to the DPS engine.

Produced by the game-memory readers, consumed by ``core/dps_tracker.py``.
Pure data, no process/Qt access. Heal-vs-damage classification lives in the tracker.
"""
from __future__ import annotations

from dataclasses import dataclass

# kind values
K_DAMAGE = "damage"
K_HEAL = "heal"

# The marker both pet taggers use, on the wire id and on the display name.
# Defined once because the canonicalisation in `DpsTracker._skill_labels` has
# to recognise exactly the suffix those two write - a pet skill that arrives
# tagged must resolve to the SAME bucket as the same skill untagged, or the
# HUD holds two rows for one skill and they hide each other in turn.
PET_SUFFIX = "(pet)"
K_SHIELD = "shield"

# ``affinity`` is the game's own per-hit damage type (st.skill.DamageResult
# .affinity). It is OPTIONAL DATA, three ways over:
#   * a patch may rename or null the field (the readers fall back to "");
#   * the live vocabulary is not the weapon set -- observed hits read
#     Physical / Magic / Raw / Chaos plus the mechanic tag ``threshold``, so
#     the taxonomy is finer than, and different from, Physical/Fire/Magic/Light;
#   * the floaty pool recycles, so a stale slot can decode a real String that
#     is not a school at all (``instStretch:y`` was observed live).
# A tag that does not LOOK like one identifier token therefore degrades to "",
# and the hit still counts -- a lost patch costs a breakdown row, never the hit.
AFFINITY_MAX = 24


def clean_affinity(raw: object) -> str:
    """Normalize a hit's damage-type tag; unusable values become ``""``.

    Shape, not vocabulary: a school the app has never seen must still pass, so
    this is deliberately not a whitelist. Rejects ``<null>`` renderings, a
    recycled pool slot's junk, and anything with punctuation or spaces.
    """
    if not isinstance(raw, str):
        return ""
    s = raw.strip()
    if not (2 <= len(s) <= AFFINITY_MAX):
        return ""
    return s if (s.isascii() and s.isalpha()) else ""


def clean_wire_name(raw: object) -> str:
    """A caster/target name from a bridge line, or ``""`` when it is junk.

    The DLL copies whatever sits behind the pointer it trusts ("read_hl_string"
    checks the length, not the contents), so a slot that drifted sends the low
    byte of some unrelated object as a name. Live 2026-10-01, every one of the
    player's own hits arrived as ``src":"\u0003"`` while the party's heals
    arrived as ``src":""`` - and the companion minted rows under those strings.
    A character name always has a letter or a digit in it, so anything else is
    treated as NO name: the event then takes the same evidence path as every
    other nameless cast (the local row only when nobody else is here) instead of
    inventing a player out of a control character.
    """
    s = " ".join(str(raw or "").split())
    if not s:
        return ""
    return s if any(ch.isalnum() for ch in s) else ""


def wire_addr(raw: object) -> int:
    """A unit ADDRESS off a bridge line, or ``0`` when absent or unusable.

    The bridge sends the target's address as ``taddr`` (decimal) on every hit -
    the same pointer the companion's own scene scan holds. It is OPTIONAL for
    the same reason ``aff`` is: a bridge older than the field simply omits it,
    and a missing address must degrade to name-keyed bucketing, never to a
    dropped hit. A hand-written payload may spell it in hex, so a ``0x`` prefix
    is read as base 16 rather than rejected.
    """
    if raw is None or raw == "":
        return 0
    try:
        if isinstance(raw, str):
            s = raw.strip()
            base = 16 if s[:2].lower() == "0x" else 10
            value = int(s, base)
        elif isinstance(raw, float):
            if not raw.is_integer():      # an address is a whole pointer, not 17.5
                return 0
            value = int(raw)
        else:
            value = int(raw)
    except (TypeError, ValueError):
        return 0
    return value if 0 < value < (1 << 64) else 0


@dataclass
class DamageEvent:
    """One per-hit damage/heal read; addrs resolved to names by the tracker."""

    amount: float = 0.0
    skill: str = "?"
    skill_name: str = ""
    crit: bool = False
    kill: bool = False
    kind: str = K_DAMAGE
    source_addr: int = 0
    target_addr: int = 0
    incoming: bool = False
    t: float = 0.0
    source_name: str = ""
    target_name: str = ""
    is_me: bool = False
    capture_mode: str = ""             # "proxy"/"injector" for bridge events
    affinity: str = ""                 # per-hit damage type; "" = untagged

    @property
    def is_crit(self) -> bool:
        return self.crit

    @property
    def is_kill(self) -> bool:
        return self.kill

    @property
    def is_heal(self) -> bool:
        return self.kind == K_HEAL
