"""Gear classification, slot groupings, and category labels."""
from __future__ import annotations

from functools import lru_cache

from ...rules import GEAR_TYPES as _GEAR_TYPES, is_gear
from ...ui import theme


_AFFINITY_COLORS = {
    "Physical": "#e8b45a",
    "Fire": "#e05a3a",
    "Magic": "#7a7ad9",
    "Light": "#d9c97a",
}


def weapon_attack(item_row: dict) -> dict | None:
    """The weapon's base attack model {affinity, ratio} from skill data."""
    return item_row.get("attack") if item_row else None


def affinity_color(affinity: str | None) -> str:
    return _AFFINITY_COLORS.get(affinity or "", theme.ACCENT)


def own_stats(item_row: dict) -> list[dict] | None:
    """Static stat bonuses for authored/fixed items."""
    return item_row.get("stats") if item_row else None


_CATEGORIES = {
    "Weapons": (
        "Sword", "GreatSword", "DualSwords", "Shield", "Axe", "GreatAxe", "DualAxes",
        "Mace", "GreatMace", "DualMaces", "Daggers", "Fists", "Spear", "Staff",
        "Scepter", "Bow", "Thrown", "Crescent", "Halos", "Book",
    ),
    "Armor": ("Chest", "Legs", "Head", "Feet", "Waist", "Hands", "Shoulders", "Back"),
    "Accessory": (
        "GearGlider", "Mount", "Bag", "GearTrinket", "GearFinger", "GearNeck",
        "GearPickaxe", "GearSickle",
    ),
    "Consumable": ("Consumable", "Food", "HealthPotion", "Potion", "Elixir"),
    "Crafting": (
        "CraftingComponent", "Recipe", "Ore", "Cloth", "Leather", "Mastery",
        "SkillPointBook",
    ),
}
_CATEGORY_BY_TYPE = {t: cat for cat, types in _CATEGORIES.items() for t in types}
_CATEGORY_ORDER = tuple(_CATEGORIES.keys()) + ("Misc",)
_GEAR_CLASSES = ("Fighter", "Assassin", "Cleric", "Wizard")
_CLASS_LABELS = {"Fighter": "Warrior", "Assassin": "Rogue", "Cleric": "Priest", "Wizard": "Mage"}


def class_label(cls: str) -> str:
    """Display label for class aptitude (Fighter -> Warrior, etc.)."""
    return _CLASS_LABELS.get(cls, cls)


def category(item_type: str | None) -> str:
    """Broad category for item type (Weapons, Armor, Accessory, etc.)."""
    return _CATEGORY_BY_TYPE.get(item_type or "", "Misc")


@lru_cache(maxsize=1)
def categories() -> list[str]:
    return list(_CATEGORY_ORDER)


@lru_cache(maxsize=1)
def gear_classes() -> list[str]:
    return list(_GEAR_CLASSES)


@lru_cache(maxsize=1)
def gear_slots() -> list[str]:
    """Gear equipment slots in category order."""
    by_cat: dict[str, list] = {}
    for t in _GEAR_TYPES:
        by_cat.setdefault(_CATEGORY_BY_TYPE.get(t, "Misc"), []).append(t)
    out = []
    for cat in _CATEGORY_ORDER:
        out.extend(sorted(by_cat.get(cat, [])))
    return out


# The game's OWN equipment slots, in the order its content array holds them —
# the itemType sheet's `Slot_*` rows, which is also the order the game's own
# equipment UI uses. Verified against a live read (2026-09-15): every filled row
# of a real character landed on its game slot (0/1 weapons, 2 offhand, 3 head,
# 4 neck, 7 back, 12/14 fingers, 13 trinket, 21 consumable 4, 29 mount), which
# is what lets a slot the game reports as EMPTY still be named — a shield goes
# in index 2, and an unnamed index told the reader nothing.
#
# The sheet's trailing `Slot_None` is a sentinel AFTER the real slots, so the
# array is 30 long and index 30 means "no slot".
_EQUIP_SLOTS = (
    "Slot_Weapon1", "Slot_Weapon2", "Slot_OffhandWeapon", "Slot_Head",
    "Slot_Neck", "Slot_Shoulders", "Slot_Chest", "Slot_Back", "Slot_Hands",
    "Slot_Waist", "Slot_Legs", "Slot_Feet", "Slot_FingerLeft",
    "Slot_Trinket", "Slot_FingerRight", "Slot_JobTool", "Slot_Pickaxe",
    "Slot_Sickle", "Slot_Consumable1", "Slot_Consumable2",
    "Slot_Consumable3", "Slot_Consumable4", "Slot_Bag1", "Slot_Bag2",
    "Slot_Bag3", "Slot_Bag4", "Slot_Bag5", "Slot_Bag6", "Slot_Glider",
    "Slot_Mount",
)

_EQUIP_SLOT_LABELS = {
    "Slot_Weapon1": "Weapon 1", "Slot_Weapon2": "Weapon 2",
    "Slot_OffhandWeapon": "Offhand", "Slot_Head": "Head",
    "Slot_Neck": "Neck", "Slot_Shoulders": "Shoulders",
    "Slot_Chest": "Chest", "Slot_Back": "Back", "Slot_Hands": "Hands",
    "Slot_Waist": "Waist", "Slot_Legs": "Legs", "Slot_Feet": "Feet",
    "Slot_FingerLeft": "Ring 1", "Slot_Trinket": "Trinket",
    "Slot_FingerRight": "Ring 2", "Slot_JobTool": "Job Tool",
    "Slot_Pickaxe": "Pickaxe", "Slot_Sickle": "Sickle",
    "Slot_Consumable1": "Consumable 1", "Slot_Consumable2": "Consumable 2",
    "Slot_Consumable3": "Consumable 3", "Slot_Consumable4": "Consumable 4",
    "Slot_Bag1": "Bag 1", "Slot_Bag2": "Bag 2", "Slot_Bag3": "Bag 3",
    "Slot_Bag4": "Bag 4", "Slot_Bag5": "Bag 5", "Slot_Bag6": "Bag 6",
    "Slot_Glider": "Glider", "Slot_Mount": "Mount",
}


@lru_cache(maxsize=1)
def equip_slots() -> list[str]:
    """The game's equipment slots in ITS OWN array order.

    Read from the bundled itemType sheet when it is reachable (so a patch that
    reorders or adds slots is picked up from the data), else the constant above
    — the same 30 names either way."""
    try:
        from .. import cdb
        ids = [str(r.get("id") or "") for r in cdb.lines("itemType")]
        live = [i for i in ids if i.startswith("Slot_") and i != "Slot_None"]
        if len(live) >= len(_EQUIP_SLOTS):
            return live[:len(_EQUIP_SLOTS)]
    except Exception:                    # no sheet: the constant is the table
        pass
    return list(_EQUIP_SLOTS)


def is_bag_slot(index: int | None) -> bool:
    """True when this live slot holds a BAG (the game's `Slot_Bag1..Slot_Bag6`).

    Bags are containers, not gear: one reads `Bag_Z2` per slot, and their
    contents are what the carried-items view lists, so a row per bag slot says
    the same thing several times over — see the Party page's gear section, which
    names them once."""
    slots = equip_slots()
    try:
        i = int(index)                       # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    return 0 <= i < len(slots) and slots[i].startswith("Slot_Bag")


_TWO_HANDED_TYPES = frozenset({
    "greatsword", "staff", "greataxe", "great_axe", "greathammer",
    "polearm", "bow", "crossbow", "spear", "scythe", "heavybow",
})


def is_two_handed(item_id: str | None) -> bool:
    """Best-effort: True when `item_id` looks like a weapon that takes BOTH
    hands (2026-09-26).

    Best-effort is the operative word, and it is why the Player Inspect doll
    does NOT use this (it draws an empty off-hand dim instead, 2026-09-26).
    Most weapon rows carry no `hands` flag at all, so this falls back to
    reading the type name — and a type-name guess that is wrong dims or locks
    a slot for a hero who is holding something perfectly one-handed. On this
    page it only decides whether the loadout PLANNER will offer an off-hand,
    where being wrong costs a click the planner refuses; on a page that merely
    reports live state it costs trust in every other number on the page.

    Lives here, in the data layer, rather than in the Items page's picker
    dialog: it is a property of the sheet, and asking a page to import another
    page's dialog to learn it would be a cycle waiting to happen.
    `items.loadout_weapons.is_2h_weapon` re-exports this name, so the Items page's call
    sites did not move.
    """
    if not item_id:
        return False
    from . import catalog as _catalog
    it = _catalog.item(item_id) or {}
    if it.get("hands") == 2 or it.get("two_handed") or it.get("is_2h"):
        return True
    wtype = (it.get("type") or "").strip().lower()
    return (wtype in _TWO_HANDED_TYPES or "great" in wtype
            or "2h" in wtype or "staff" in wtype)


# The off-hand takes a shield and nothing else. Unlike the two-handed rule
# above this is NOT a heuristic: the sheet has an exact `Shield` type (four
# rows, all one-handed, all class-restricted to Fighter/Cleric/Wizard), so
# the planner can answer exactly instead of guessing from a name.
_SHIELD_TYPES = frozenset({"shield"})


def is_shield(item_id: str | None) -> bool:
    """True when `item_id` is a shield — the only piece the Loadout page
    will put in the off-hand slot. By type, exactly; see `_SHIELD_TYPES`."""
    if not item_id:
        return False
    from . import catalog as _catalog
    it = _catalog.item(item_id) or {}
    return (it.get("type") or "").strip().lower() in _SHIELD_TYPES


def equip_slot_label(index: int | None) -> str:
    """A live content-array index -> the game's own slot name ("Offhand",
    "Ring 1", "Consumable 4", "Mount"), or "" when the index is outside the
    30-slot array."""
    slots = equip_slots()
    if index is None:
        return ""
    try:
        i = int(index)
    except (TypeError, ValueError):
        return ""
    if not 0 <= i < len(slots):
        return ""
    return _EQUIP_SLOT_LABELS.get(slots[i]) or slots[i]
