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

CLASS_APTITUDE = {
    "Warrior": "Fighter",
    "Rogue": "Assassin",
    "Mage": "Wizard",
    "Priest": "Cleric",
}
CLASSES = tuple(CLASS_APTITUDE)


@lru_cache(maxsize=1)
def _item_aptitudes() -> dict[str, frozenset[str]]:
    """item id -> the aptitudes that may wield it.

    Read from the item_drops index, NOT `cdb.display_data("items")`: that is
    the compiled `raw_items` sheet, whose per-sheet field list in
    compiler.py keeps only id/rarity/type (+baked name) — `aptitudes` is
    dropped there, so the old read returned an empty map for every item and
    `is_for_class` was silently always False. The drops index is built from
    the same sheet (compiler.py stamps `aptitudes` onto its rows) and is the
    payload `data/items/stats.py` already reads classes from.

    build_tools/check_compiled_fields.py guards this: it fails the build if a
    field read off a compiled shim survives on no row of that payload while
    the compile input still carries it.
    """
    from .items import catalog
    out: dict[str, frozenset[str]] = {}
    for it in catalog.items():
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
