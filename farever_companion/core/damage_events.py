"""DamageEvent: unit of damage input to the DPS engine.

Produced by the game-memory readers, consumed by ``core/dps_tracker.py``.
Pure data, no process/Qt access. Heal-vs-damage classification lives in the tracker.
"""
from __future__ import annotations

from dataclasses import dataclass

# kind values
K_DAMAGE = "damage"
K_HEAL = "heal"
K_SHIELD = "shield"


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

    @property
    def is_crit(self) -> bool:
        return self.crit

    @property
    def is_kill(self) -> bool:
        return self.kill

    @property
    def is_heal(self) -> bool:
        return self.kind == K_HEAL
