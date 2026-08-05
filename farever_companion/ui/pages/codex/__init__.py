"""Codex page package.

The single 1088-line CodexPageMixin was split into focused mixins so each file
stays under the project's 800-line UI budget:

- page.py     page assembly, widget builders, tab/search/filter handlers
- grid.py     grid pipeline (rebuild, filter, sort, render, chrome)
- map_ui.py   map panel, coordinate resolution, pin plotting
- settings.py last-hidden undo, bulk actions, card sync
- card.py     CodexUnitCard widget
- map.py      CodexZoneMapCanvas widget

ControlPanel composes the resulting CodexPageMixin into its base list.
"""
from .page import CodexPageBase
from .grid import CodexGridMixin
from .map_ui import CodexMapUiMixin
from .settings import CodexSettingsMixin
from .card import CodexUnitCard
from .map import CodexZoneMapCanvas


class CodexPageMixin(CodexPageBase, CodexGridMixin, CodexMapUiMixin, CodexSettingsMixin):
    """Composed Codex page mixin (assembly + grid + map + settings)."""


__all__ = ["CodexPageMixin", "CodexUnitCard", "CodexZoneMapCanvas"]
