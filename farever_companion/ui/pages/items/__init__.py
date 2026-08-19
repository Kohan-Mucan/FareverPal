"""Items page package (the new item database / gear lookup).

Like the codex page, the item page is split into focused mixins so each file
stays under the project's 800-line UI budget:

- page.py    page assembly (ItemPageBase): the layout + widget build
- enchants.py EnchantsPageBase: the Enchants tab (scrolls / gems /
             conversions + the stat filter chip row)
- filters.py ItemFiltersMixin: search / filter pipeline, chip row, quick tabs
- list.py    ItemListMixin: grouped population of the catalog rows
- detail.py  ItemDetailMixin: the detail pane render + collapse handling,
             plus the builder support functions and list widgets (stat tags,
             slot-combo headings, collapsible headers)
- farm.py    FarmMixin: the Gear Farm tab (saved gear list + class chips)
             and the ADD TO FARM pill on gear detail cards

ControlPanel composes the resulting ItemPageMixin into its base list.
"""
from .page import ItemPageBase, ItemListMixin
from .enchants import EnchantsPageBase
from .filters import ItemFiltersMixin
from .detail import ItemDetailMixin
from .farm import FarmMixin


class ItemPageMixin(ItemPageBase, EnchantsPageBase, ItemFiltersMixin,
                    ItemListMixin, ItemDetailMixin, FarmMixin):
    """Composed item page mixin (assembly + enchants + filters + list +
    detail + gear farm)."""


__all__ = ["ItemPageMixin"]
