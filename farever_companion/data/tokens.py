"""Procedural loot-token handling.

A few loot-table "items" are generator tokens the game resolves at drop time
(WorldLoot, WorldLootWithAffinity, WorldRecipeWithJob). We never fabricate a
concrete pool for these; the UI shows an honest "random X" row. The boss-filtered
`expand` helper backs only an optional estimated-pool view, never a drop claim.
"""
from __future__ import annotations

from functools import lru_cache

from . import cdb, loot, units

EXPANDABLE = frozenset({"WorldLoot", "WorldLootWithAffinity"})

LABELS = {
    "WorldLoot": "Random world gear",
    "WorldLootWithAffinity": "Random affinity weapon",
    "WorldRecipeWithJob": "Random recipe (your job)",
}


def is_token(item_id: str | None) -> bool:
    return bool(item_id) and (item_id in EXPANDABLE or item_id in LABELS)


def label(item_id: str | None) -> str | None:
    return LABELS.get(item_id) if item_id else None


@lru_cache(maxsize=1)
def _boss_table_items() -> frozenset[str]:
    """Every item reachable from a named-boss signature table, these are boss
    drops, excluded from world-pool estimates."""
    tables = loot._tables()

    def leaves(tid: str, seen: frozenset[str]) -> set[str]:
        if tid in seen or tid not in tables:
            return set()
        seen = seen | {tid}
        out: set[str] = set()
        for e in tables[tid].get("loot", []):
            if "item" in e:
                out.add(e["item"])
            elif "lootTable" in e:
                out |= leaves(e["lootTable"], seen)
        return out

    items: set[str] = set()
    for boss in units.named_bosses():
        items |= leaves(boss, frozenset())
    return frozenset(items)


@lru_cache(maxsize=1)
def _pools() -> dict[str, list[tuple[str, float, str, str]]]:
    """token -> [(item_id, share, rarity, type)] from loot tables,
    boss-exclusive weapons filtered out. Estimate only."""
    by_id = {it["id"]: it for it in cdb.display_data("items")}
    boss_items = _boss_table_items()
    
    # Reconstruct the pool by scanning which items are in which loot tables
    # especially those mentioned as tokens.
    rev_map = loot.reverse_loot_map()
    
    acc: dict[str, set[str]] = {}
    for iid, tables in rev_map.items():
        if iid in boss_items:
            continue
        for tid in tables:
            if tid in EXPANDABLE:
                acc.setdefault(tid, set()).add(iid)

    out: dict[str, list[tuple[str, float, str, str]]] = {}
    for tok, items in acc.items():
        # Without pool_share from the display data, we assume equal weights
        n = len(items)
        share = 1.0 / n if n > 0 else 1.0
        rows = []
        for iid in items:
            m = by_id.get(iid, {})
            rows.append((iid, share, m.get("rarity") or "Common", m.get("type") or ""))
        rows.sort(key=lambda r: r[0]) # Sort by ID if shares are equal
        out[tok] = rows
    return out


def expand(item_id: str | None) -> list[tuple[str, float, str, str]]:
    """Estimated pool for a generator token, or []. NOT a confirmed claim."""
    if not item_id:
        return []
    return _pools().get(item_id, [])
