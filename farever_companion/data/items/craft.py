"""Crafting recipes + jobs (craft.json / job.json, offline).

Enriches recipes with readable names, job metadata, material chains,
and crafting queue costs.
"""
from __future__ import annotations

import json
from functools import lru_cache

from ... import paths
from .. import cdb, names
from .catalog import _data, item
from .labels import is_gear

try:
    from .. import raw_craft
except ImportError:
    raw_craft = None


def _sheet_lines(kind: str, json_path) -> list[dict]:
    """Raw sheet rows from compiled raw_craft shim or loose JSON."""
    if raw_craft is not None:
        data = getattr(raw_craft, "DATA", None)
        if data and kind in data:
            return data[kind]
    try:
        return json.loads(json_path().read_text(encoding="utf-8")).get("lines", [])
    except (OSError, ValueError):
        return []


@lru_cache(maxsize=1)
def _recipes_raw() -> list[dict]:
    return _sheet_lines("recipes", paths.craft_path)


@lru_cache(maxsize=1)
def _jobs_raw() -> list[dict]:
    return _sheet_lines("jobs", paths.job_path)


@lru_cache(maxsize=1)
def _sheet_names() -> dict[str, str]:
    """Item id -> display name from the items sheet."""
    return {r.get("id"): r.get("name") for r in cdb.lines("items") if r.get("id")}


@lru_cache(maxsize=1)
def _recipe_index() -> dict[str, dict]:
    """Item id -> raw recipe row."""
    return {r.get("item"): r for r in _recipes_raw() if r.get("item")}


def _item_name(item_id: str) -> str:
    """Display name for item id: DB name -> sheet name -> humanized id."""
    it = _data().get("items", {}).get(item_id)
    if it and it.get("name"):
        return it["name"]
    sheet = _sheet_names().get(item_id)
    return sheet if sheet else (names.humanize(item_id) or item_id)


_LOOT_NAMES = {
    "SmallCP1": "Small Craft Point node",
    "BigCP_Z1": "Big Craft Point node",
}


def _loot_name(table_id: str) -> str | None:
    if not table_id:
        return None
    return _LOOT_NAMES.get(table_id) or (names.humanize(table_id) or table_id)


@lru_cache(maxsize=1)
def _job_names() -> dict[str, str]:
    return {j.get("id"): (j.get("texts", {}).get("name", {}).get("v") or j.get("id"))
            for j in _jobs_raw() if j.get("id")}


def _enrich(r: dict) -> dict:
    """Recipe row with readable names + cross-links resolved."""
    jid = r.get("job") or ""
    jname = _job_names().get(jid, jid)
    it_row = item(r.get("item") or "")
    return {
        "item": r.get("item"),
        "name": _item_name(r.get("item") or ""),
        "count": r.get("count") or 1,
        "level": r.get("level") or 1,
        "job": jid,
        "job_name": jname,
        "materials": [{"item": m.get("item"),
                       "name": _item_name(m.get("item") or ""),
                       "count": m.get("count") or 1,
                       "crafted": m.get("item") in _recipe_index()}
                      for m in r.get("input") or []],
        "cost": r.get("cost"),
        "xp": first_craft_xp(r.get("level") or 1),
        "loot": r.get("loot"),
        "loot_name": _loot_name(r.get("loot")),
        "unlock": r.get("unlockSource"),
        "unlock_name": (_item_name(r["unlockSource"])
                        if r.get("unlockSource") else None),
        "gear": bool(it_row) and is_gear(it_row),
        "type": (it_row or {}).get("type"),
        "rarity": (it_row or {}).get("rarity"),
        "classes": (it_row or {}).get("classes") or [],
    }


def jobs() -> list[dict]:
    """Every job: {id, name, desc, tool, recipes} in sheet order."""
    counts: dict[str, int] = {}
    for r in _recipes_raw():
        j = r.get("job")
        counts[j] = counts.get(j, 0) + 1
    out = []
    for j in _jobs_raw():
        t = j.get("texts") or {}
        jid = j.get("id")
        out.append({
            "id": jid,
            "name": (t.get("name") or {}).get("v") or jid,
            "plural": (t.get("name") or {}).get("plural"),
            "desc": t.get("desc", ""),
            "tool": names.humanize(j.get("toolType")) or "",
            "recipes": counts.get(jid, 0),
        })
    return out


def first_craft_xp(level: int) -> int:
    """Job XP granted for first-crafting a recipe of given level."""
    x = max(1, int(level))
    return 120 * x + 30 * x * x


@lru_cache(maxsize=1)
def craft_jobs() -> list[str]:
    """Job ids that have recipes, in sheet order."""
    return [j["id"] for j in jobs() if j["recipes"]]


@lru_cache(maxsize=1)
def _craft_job_order() -> dict[str, int]:
    return {j: i for i, j in enumerate(craft_jobs())}


@lru_cache(maxsize=1)
def craft_levels() -> list[int]:
    """Recipe level filter choices, sorted."""
    return sorted({r.get("level", 1) for r in _recipes_raw()})


def recipes(job: str = "", level: int = 0, query: str = "") -> list[dict]:
    """Enriched recipes filtered by job, level, and text query."""
    q = (query or "").strip().lower()
    order = _craft_job_order()
    out = []
    for r in _recipes_raw():
        if job and r.get("job") != job:
            continue
        if level and r.get("level") != level:
            continue
        if q:
            hay = f"{_item_name(r.get('item') or '')} {r.get('item') or ''} {r.get('job') or ''}".lower()
            if q not in hay:
                continue
        out.append(_enrich(r))
    out.sort(key=lambda r: (order.get(r["job"], 99), -r["level"], r["name"].lower()))
    return out


def recipe(item_id: str) -> dict | None:
    """Enriched recipe that crafts `item_id`, or None."""
    r = _recipe_index().get(item_id)
    return _enrich(r) if r else None


def is_craftable(item_id: str) -> bool:
    """True when a recipe produces item_id."""
    return item_id in _recipe_index()


@lru_cache(maxsize=1)
def _material_ids() -> frozenset[str]:
    return frozenset(m.get("item") for r in _recipes_raw() for m in r.get("input") or [])


def is_craft_item(item_id: str) -> bool:
    """True when item is a recipe output or consumed material."""
    return is_craftable(item_id) or item_id in _material_ids()


@lru_cache(maxsize=1)
def _unlock_index() -> dict[str, dict]:
    return {r.get("unlockSource"): r for r in _recipes_raw() if r.get("unlockSource")}


def recipe_unlocked_by(recipe_item_id: str) -> dict | None:
    """The enriched recipe unlocked by a recipe scroll item, or None."""
    if not recipe_item_id:
        return None
    r = _unlock_index().get(recipe_item_id)
    return _enrich(r) if r else None


@lru_cache(maxsize=1)
def _recipes_using_index() -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for r in _recipes_raw():
        for m in r.get("input") or []:
            iid = m.get("item")
            if iid:
                out.setdefault(iid, []).append(r)
    return out


def recipes_using(item_id: str) -> list[dict]:
    """Recipes consuming `item_id` as input, sorted by job and level."""
    order = _craft_job_order()
    raw = _recipes_using_index().get(item_id, [])
    out = [_enrich(r) for r in raw]
    out.sort(key=lambda r: (order.get(r.get("job"), 99), -r.get("level", 1), r.get("name", "").lower()))
    return out


def craft_chain(item_id: str) -> list[dict] | None:
    """Full recursive supply chain for a crafted item down to raw inputs."""
    root = _recipe_index().get(item_id)
    if not root:
        return None
    out: list[dict] = []
    on_path: set[str] = set()

    def walk(mat: str, count: int, depth: int) -> None:
        if mat in on_path:
            return
        rec = _recipe_index().get(mat)
        out.append({"item": mat, "name": _item_name(mat), "count": count,
                    "depth": depth, "crafted": rec is not None})
        if not rec:
            return
        on_path.add(mat)
        for m in rec.get("input", []):
            walk(m.get("item"), count * (m.get("count") or 1), depth + 1)
        on_path.discard(mat)

    for m in root.get("input", []):
        walk(m.get("item"), m.get("count") or 1, 1)
    return out


@lru_cache(maxsize=256)
def craft_bill(item_id: str, qty: int = 1) -> dict | None:
    """Summed bill of materials and total gold for crafting `item_id` `qty` times."""
    root = _recipe_index().get(item_id)
    if not root:
        return None
    qty = max(1, int(qty))
    agg: dict[str, dict] = {}
    depth: dict[str, int] = {}

    def add(mat: str, count: int, d: int) -> None:
        if mat not in agg:
            agg[mat] = {"item": mat, "name": _item_name(mat), "count": 0,
                        "depth": d, "crafted": mat in _recipe_index()}
            depth[mat] = d
        agg[mat]["count"] += count
        depth[mat] = min(depth[mat], d)

    on_path: set[str] = set()

    def walk(mat: str, count: int, d: int) -> None:
        if mat in on_path:
            return
        add(mat, count, d)
        rec = _recipe_index().get(mat)
        if not rec:
            return
        on_path.add(mat)
        for m in rec.get("input", []):
            walk(m.get("item"), count * (m.get("count") or 1), d + 1)
        on_path.discard(mat)

    for m in root.get("input", []):
        walk(m.get("item"), (m.get("count") or 1) * qty, 1)

    gold = 0
    for a in agg.values():
        a["depth"] = depth[a["item"]]
        if a["crafted"]:
            rec = _recipe_index()[a["item"]]
            runs = -(-a["count"] // (rec.get("count") or 1))
            gold += runs * (rec.get("cost") or 0)
    if root.get("cost"):
        gold += qty * root["cost"]

    items = sorted(agg.values(), key=lambda a: (a["depth"], a["name"].lower()))
    return {"qty": qty, "items": items, "gold": gold,
            "raw_total": sum(a["count"] for a in items if not a["crafted"])}


@lru_cache(maxsize=64)
def craft_bill_many(entries: tuple[tuple[str, int], ...]) -> dict | None:
    """Summed bill of materials across multiple (item, qty) queue items."""
    agg: dict[str, dict] = {}
    depth: dict[str, int] = {}
    gold = 0
    recipes: list[dict] = []
    for item_id, qty in entries:
        b = craft_bill(item_id, qty)
        if not b:
            continue
        for it in b["items"]:
            if it["item"] not in agg:
                agg[it["item"]] = dict(it)
                depth[it["item"]] = it["depth"]
            else:
                agg[it["item"]]["count"] += it["count"]
                depth[it["item"]] = min(depth[it["item"]], it["depth"])
        gold += b["gold"]
        rec = recipe(item_id)
        recipes.append({
            "item": item_id,
            "name": rec["name"] if rec else _item_name(item_id),
            "qty": qty,
            "gold": ((rec or {}).get("cost") or 0) * qty,
        })
    if not agg:
        return None
    for a in agg.values():
        a["depth"] = depth[a["item"]]
    items = sorted(agg.values(), key=lambda a: (a["depth"], a["name"].lower()))
    return {"items": items, "gold": gold,
            "raw_total": sum(a["count"] for a in items if not a["crafted"]),
            "recipes": recipes}
