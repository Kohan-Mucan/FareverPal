"""Backward-compat shim — the Top DPS overlay lives in ui/overlays/dps/.

Kept so the overlay manager's module-path registry ("dps_overlay") and any
imports from the old flat module keep working after the split.
"""
from .dps import DpsOverlay, GLOBAL_SKILL_CAP, TOP_SKILLS, _PlayerRowWidget

__all__ = ["DpsOverlay", "GLOBAL_SKILL_CAP", "TOP_SKILLS", "_PlayerRowWidget"]
