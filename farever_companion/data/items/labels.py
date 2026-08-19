"""Gear classification, slot groupings, and category labels."""
from __future__ import annotations

from functools import lru_cache

from ...ui import theme

_GEAR_TYPES = frozenset({
    "GreatAxe", "Daggers", "Halos", "Staff", "DualAxes", "Scepter", "Axe",
    "Crescent", "Spear", "Shield", "Fists", "DualMaces", "Thrown", "Sword",
    "Book", "Bow", "Mace", "GreatMace", "DualSwords", "GreatSword",
    "Cloth", "Leather", "Back", "Feet", "Head", "Legs", "Chest", "Hands",
    "Waist", "Shoulders",
    "GearTrinket", "GearFinger", "GearNeck",
})


def is_gear(item_row: dict) -> bool:
    return (item_row.get("type") or "") in _GEAR_TYPES


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
