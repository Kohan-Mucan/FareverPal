"""Player class <-> aptitude <-> item relevance.

Farever has 4 player classes; each maps to one combat aptitude (verified vs
aptitude.json, the aptitude's name IS the class):

    Warrior -> Fighter (Strength), Rogue -> Assassin (Dexterity),
    Mage -> Wizard (Intellect), Priest -> Cleric (Faith)

Each weapon/gear item lists the aptitudes that can wield it (items.json
`aptitudes`). An item is relevant to my class iff my class's aptitude is in
that list.
"""
from __future__ import annotations

from functools import lru_cache

from . import cdb

CLASS_APTITUDE = {
    "Warrior": "Fighter",
    "Rogue": "Assassin",
    "Mage": "Wizard",
    "Priest": "Cleric",
}
CLASSES = tuple(CLASS_APTITUDE)


@lru_cache(maxsize=1)
def _item_aptitudes() -> dict[str, frozenset[str]]:
    out: dict[str, frozenset[str]] = {}
    for it in cdb.display_data("items"):
        apts = it.get("aptitudes") or []
        if apts:
            out[it["id"]] = frozenset(apts)
    return out


def aptitude_for(cls: str | None) -> str | None:
    return CLASS_APTITUDE.get(cls) if cls else None


def item_aptitudes(item_id: str | None) -> frozenset[str]:
    if not item_id:
        return frozenset()
    return _item_aptitudes().get(item_id, frozenset())


def is_for_class(item_id: str | None, cls: str | None) -> bool:
    apt = aptitude_for(cls)
    return bool(apt and apt in item_aptitudes(item_id))
