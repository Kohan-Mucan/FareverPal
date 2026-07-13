"""Loot predictor: recursive expansion of a loot table to expected per-item
drop probability. Pure data, no attached process.

Two entry kinds, selected by the table's flags (lootTable.flags,
TFlags{Weights=1, WithAffinity=2}):

* Independent-roll (Weights clear): each entry drops on its own with `proba`,
  accumulating additively; a table can yield several or zero items. Default for
  trash, crates and currency.
* Weighted pick-one (Weights set): one normalized weighted pick over the
  eligible entries (`proba` is a relative weight), always exactly one outcome.
  Every boss bossLootTable is a Weights table. See findings/formulas.md.
"""
from __future__ import annotations

from functools import lru_cache

from . import cdb

MAX_DEPTH = 8
WEIGHTS_FLAG = 1   # lootTable.flags bit 0 (TFlags{Weights, WithAffinity})


@lru_cache(maxsize=1)
def _tables() -> dict[str, dict]:
    return {r["id"]: r for r in cdb.lines("lootTable")}


@lru_cache(maxsize=1)
def _items() -> dict[str, dict]:
    return {r["id"]: r for r in cdb.lines("item")}


def predict(
    tid: str,
    level: int,
    conds: int | None = None,
    _depth: int = 0,
    _visited: frozenset[str] | None = None,
) -> dict[str, float]:
    """Expected per-item drop probability for loot table `tid` at `level`.

    `conds`: optional TFlags bitmask {BasicFoe=1, SpecialFoe=2, DungeonFoe=4};
    an entry's conds bits must all be set in `conds` to be eligible. None
    (default) applies no condition filter (the in-game default).
    """
    if _depth > MAX_DEPTH:
        return {}
    _visited = _visited or frozenset()
    if tid in _visited:
        return {}
    _visited = _visited | {tid}

    t = _tables().get(tid)
    if t is None:
        return {f"<missing: {tid}>": 1.0}

    # Entries surviving the level/conds gate are the candidates the engine
    # actually sees; for a Weights table the normalization sum is over these.
    eligible = [e for e in t.get("loot", []) if _entry_ok(e, level, conds)]

    weighted = bool((t.get("flags") or 0) & WEIGHTS_FLAG)
    if weighted:
        total = sum(e.get("proba", 0) for e in eligible)
        if total <= 0:
            return {}

    out: dict[str, float] = {}
    for e in eligible:
        # Weighted pick-one: the entry's share of the single guaranteed pick.
        # Independent roll: the entry's own drop probability.
        p = (e.get("proba", 0) / total) if weighted else e.get("proba", 0)
        if "item" in e:
            out[e["item"]] = out.get(e["item"], 0.0) + p
        elif "lootTable" in e:
            sub = predict(e["lootTable"], level, conds, _depth + 1, _visited)
            for k, v in sub.items():
                out[k] = out.get(k, 0.0) + p * v
    return out


def _entry_ok(e: dict, level: int, conds: int | None) -> bool:
    """Level- and conds-gate for a single loot entry (in-game default: no
    conds filter). `conds` bits required by the entry must all be set."""
    if "minLvl" in e and level < e["minLvl"]:
        return False
    if "maxLvl" in e and level > e["maxLvl"]:
        return False
    if conds is not None and "conds" in e and (e["conds"] & conds) != e["conds"]:
        return False
    return True


def predict_sorted(tid: str, level: int, conds: int | None = None
                   ) -> list[tuple[str, float, str, str]]:
    """Rows (item, prob, rarity, type) sorted by descending probability."""
    items = _items()
    rows = []
    for item, prob in predict(tid, level, conds).items():
        meta = items.get(item, {})
        rows.append((item, prob, meta.get("rarity", ""), meta.get("type", "")))
    rows.sort(key=lambda r: r[1], reverse=True)
    return rows


def table_ids() -> list[str]:
    return sorted(_tables().keys())


def table_exists(tid: str | None) -> bool:
    return bool(tid) and tid in _tables()


@lru_cache(maxsize=1)
def reverse_loot_map() -> dict[str, list[str]]:
    """Map of item_id -> list of loot_table_ids that contain it."""
    out: dict[str, set[str]] = {}
    tables = _tables()

    def walk(tid: str, root_tid: str, visited: set[str]):
        if tid in visited:
            return
        visited.add(tid)
        t = tables.get(tid)
        if not t:
            return
        for e in t.get("loot", []):
            if "item" in e:
                iid = e["item"]
                if iid not in out:
                    out[iid] = set()
                out[iid].add(root_tid)
            elif "lootTable" in e:
                walk(e["lootTable"], root_tid, visited)

    for tid in tables:
        walk(tid, tid, set())
    return {k: sorted(list(v)) for k, v in out.items()}
