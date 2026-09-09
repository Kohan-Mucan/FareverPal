"""The Top DPS Meter HUD, a package rather than one over-budget module.

- overlay.py        the `DpsOverlay` window itself
- rows.py           the per-player rows (Dungeon-HUD style) + `TOP_SKILLS`
- widgets.py        the meter's small parts: the group chips, the mode
                    pill, the capture badge and the target line
- dummy_ui.py       everything the training-dummy test renders: the Stop / Start
                    Test control, the per-dummy split, the vs-last-test baseline

Split out of `dps_overlay.py` when that file crossed the UI line budget; the
old name is kept only in the commit history and in the retired
`FareverPal.spec` hiddenimport, which named a module that stopped existing and
now reads `farever_companion.ui.overlays.dps` - this package - because that is
what `_OVERLAY_SOURCES["dps"]` imports. `overlay_manager` reaches it by runtime
string, and the tests import `DpsOverlay` from here directly.
"""
from .overlay import DpsOverlay, GLOBAL_SKILL_CAP
from .rows import TOP_SKILLS, _PlayerRowWidget

__all__ = ["DpsOverlay", "GLOBAL_SKILL_CAP", "TOP_SKILLS", "_PlayerRowWidget"]
