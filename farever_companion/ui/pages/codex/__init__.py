"""Codex page package.

The single 1088-line CodexPageMixin was split into focused mixins so each file
stays under the project's 800-line UI budget:

- page.py       page assembly, widget builders, tab/search/filter handlers
- grid.py       grid pipeline (rebuild, filter, sort, render, chrome)
- grouping.py   Collection species grouping (group key, group sub-menu)
- map_ui.py     map panel, coordinate resolution, pin plotting
- soulstones.py soulstone summon-spot tracking, plotting, jumps & list view
- settings.py   last-hidden undo, bulk actions, card sync
- card.py       CodexUnitCard widget
- map.py        CodexZoneMapCanvas widget

ControlPanel composes the resulting CodexPageMixin into its base list.
"""
from .page import CodexPageBase
from .grid import CodexGridMixin
from .grouping import CodexGroupingMixin
from .map_ui import CodexMapUiMixin
from .soulstones import SoulstoneMixin
from .settings import CodexSettingsMixin
from .card import CodexUnitCard
from .map import CodexZoneMapCanvas


class CodexPageMixin(CodexPageBase, CodexGridMixin, CodexGroupingMixin,
                     CodexMapUiMixin, SoulstoneMixin, CodexSettingsMixin):
    """Composed Codex page mixin (assembly + grid + grouping + map +
    soulstones + settings)."""


__all__ = ["CodexPageMixin", "CodexUnitCard", "CodexZoneMapCanvas"]
