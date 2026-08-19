"""Craft page package: the Craft database window.

The craft.json / job.json sheets drive the single view (craft v2 — the
Gear-tab look), composed like the items page (assembly + detail):

- page.py   assembly: header, two tabs (RECIPES | CRAFT LIST), the filter
            panel (JOB chips: the five professions in the user's order,
            no ALL chip — the grid auto-sorts highest level first within
            each job, no level chips) and the recipe TILE GRID — the
            Gear tab's codex tiles with the level pill on the icon and a
            muted GOLD band
- detail.py the variant-B split recipe card (Materials | Total Needed side
            by side) + the raw-material card (drops + what uses it),
            reached by clicking a material inside a recipe card
- queue.py  the Craft List tab (added recipes + summed Total Needed bill)
            — the queue's own full-width page, no cramped docked rail

ControlPanel composes the resulting CraftPageMixin into its base list.
"""
from .page import CraftPageBase
from .detail import CraftDetailMixin


class CraftPageMixin(CraftPageBase, CraftDetailMixin):
    """Composed Craft page mixin (assembly + recipe detail)."""


__all__ = ["CraftPageMixin"]
