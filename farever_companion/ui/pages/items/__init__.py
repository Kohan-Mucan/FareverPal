"""Items page package (the item database / gear lookup).

Like the codex page, the item page is split into focused mixins so each file
stays under the project's 840-line UI budget:

- page.py    page assembly (ItemPageBase) plus ItemListMixin's grouped
             population of the catalog rows and the Items tab's non-gear
             pool view (mats / consumables / misc — its own tile list and
             category chips, sharing the search box + detail card)
- filters.py ItemFiltersMixin: search / filter pipeline, chip row, quick tabs
- detail.py  ItemDetailMixin: the detail pane render + collapse handling,
             plus the builder support functions and list widgets (stat tags,
             slot-combo headings, collapsible headers)
- drops.py   Drops From rendering (boss/vendor rows, the source grid, the
             '+N more sources' expander) — shared with the craft detail pane
- enchants.py / enchants_rows.py
             EnchantsPageBase: the Enchants tab (scrolls / gems / conversions,
             the heroic Infusion Patterns, the stat filter chip row, the
             upgrade cost matrix)
- loadout.py (+ loadout_weapons.py, loadout_collection.py)
             LoadoutMixin: the Loadout Builder & Mix-Match optimizer and its
             gear collection manager, weapon-slot half and item picker dialog
- dungeon_view.py / upgrades_matrix.py
             the 🏰 DUNGEONS tab (boss weapon + heroic set view) and the
             UPGRADE LADDER staircase matrix
- skills_section.py / support.py
             the weapon-skills action bar and the list/stat-card helpers
- farm.py    FarmMixin: the Gear Farm tab (saved gear list + class chips)
             and the ADD TO FARM pill on gear detail cards
- merchants.py
             MerchantsMixin: the Merchants tab (one section per recorded
             vendor: what their counter stocked and what it charged)

`control_panel.py` imports `ItemPageMixin` from here and mixes it into its base
list; no page imports another page's package.
"""
from .page import ItemPageBase, ItemListMixin
from .loadout import LoadoutMixin
from .enchants import EnchantsPageBase
from .filters import ItemFiltersMixin
from .detail import ItemDetailMixin
from .farm import FarmMixin
from .merchants import MerchantsMixin


class ItemPageMixin(ItemPageBase, LoadoutMixin, EnchantsPageBase, ItemFiltersMixin,
                    ItemListMixin, ItemDetailMixin, FarmMixin, MerchantsMixin):
    """Composed item page mixin (assembly + loadout + enchants + filters + list +
    detail + gear farm + merchants)."""


__all__ = ["ItemPageMixin"]
