"""Settings page as a package of per-tab mixins.

The old single-file ui/pages/settings.py (3.4k+ lines) is split here into one
mixin per feature area — page shell/lifecycle, layer matrix, HUD/Dungeon/
Minimap cards, DPS engine + proxy lifecycle (its satellites in one
shard beside it), rift, and the Pages tab —
each under the ui line budget, plus ``_shared`` carrying the original
module-level namespace so moved method bodies keep their globals.

``SettingsPageMixin`` is composed from every mixin, so
``from ...ui.pages.settings import SettingsPageMixin`` (control_panel, tests)
keeps working unchanged.
"""
from ._shared import *  # noqa: F401,F403  legacy module-level names
from .shell import SettingsShellMixin
from .layers import SettingsLayersMixin
from .hud import SettingsHudMixin
from .dps_facets import (SettingsDpsBridgeLogMixin, SettingsDpsDiagMixin,
                        SettingsDpsMemoryMixin, SettingsDpsMeterMixin)
from .dps_engine import SettingsDpsEngineMixin
from .dps_modes import SettingsDpsModesMixin
from .dps_proxy import SettingsDpsProxyMixin
from .rift import SettingsRiftMixin
from .pages_tab import SettingsPagesMixin
from .dev import SettingsDevMixin


class SettingsPageMixin(
    SettingsShellMixin,
    SettingsLayersMixin,
    SettingsHudMixin,
    SettingsDpsMeterMixin,
    SettingsDpsEngineMixin,
    SettingsDpsModesMixin,
    SettingsDpsProxyMixin,
    SettingsDpsDiagMixin,
    SettingsDpsBridgeLogMixin,
    SettingsDpsMemoryMixin,
    SettingsRiftMixin,
    SettingsPagesMixin,
    SettingsDevMixin,
):
    """Combined Settings page mixin (see the per-area mixins above)."""


__all__ = ["SettingsPageMixin"]
