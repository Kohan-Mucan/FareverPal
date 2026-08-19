"""Raw access to the bundled item_drops.json index: item lookup and search."""
from __future__ import annotations

import json
import re
from functools import lru_cache

from ... import paths
from .labels import class_label

try:
    from .. import raw_item_drops
except ImportError:
    raw_item_drops = None

try:
    from .. import raw_shop
except ImportError:
    raw_shop = None


@lru_cache(maxsize=1)
def _shop_ids() -> frozenset[str]:
    """Cash-shop item ids from compiled shop.json."""
    try:
        rows = getattr(raw_shop, "DATA", None)
        if rows:
            if isinstance(rows, dict):
                rows = rows.get("shop") or rows.get("items") or []
            if isinstance(rows, list):
                ids = {str(r.get("id")) for r in rows
                       if isinstance(r, dict) and r.get("id")}
                if ids:
                    return frozenset(ids)
    except Exception:
        pass
    return frozenset()


_SHOP_ID_RE = re.compile(r"(?i)_shop")


def is_shop_item(item_id: str) -> bool:
    """True for cash-shop cosmetic gear (no drops)."""
    uid = item_id or ""
    return uid in _shop_ids() or bool(_SHOP_ID_RE.search(uid))


@lru_cache(maxsize=1)
def _data() -> dict:
    """Parsed item_drops.json payload from shim or loose JSON."""
    if raw_item_drops is not None:
        data = getattr(raw_item_drops, "DATA", None)
        if data and data.get("items"):
            return data
    try:
        return json.loads(paths.item_drops_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


@lru_cache(maxsize=1)
def _sources() -> list[dict]:
    return _data().get("_sources", [])


@lru_cache(maxsize=1)
def _locs() -> list[str]:
    return _data().get("_locs", [])


@lru_cache(maxsize=1)
def _tables() -> list[str]:
    return _data().get("_tables", [])


def available() -> bool:
    """True when the bundled item database is reachable."""
    return bool(_data().get("items"))


@lru_cache(maxsize=1)
def items() -> list[dict]:
    """Every item flattened and sorted by display name."""
    out = [{"id": iid, **it} for iid, it in _data().get("items", {}).items()]
    out.sort(key=lambda r: (r.get("name") or r["id"]).lower())
    return out


def item(item_id: str) -> dict | None:
    row = _data().get("items", {}).get(item_id)
    return {"id": item_id, **row} if row else None


def search(query: str = "", item_type: str = "", rarity: str = "") -> list[dict]:
    """Items matching query (name/id/type) plus optional filters."""
    q = (query or "").strip().lower()
    out = []
    for it in items():
        if rarity and it.get("rarity") != rarity:
            continue
        if item_type and it.get("type") != item_type:
            continue
        if q:
            hay = f"{it.get('name') or ''} {it['id']} {it.get('type') or ''}".lower()
            if q not in hay and not matches_skill(it["id"], q) and not matches_class(it["id"], q):
                continue
        out.append(it)
    return out


@lru_cache(maxsize=1)
def types() -> list[str]:
    """All sorted item type ids."""
    return sorted({it.get("type") for it in items() if it.get("type")})


@lru_cache(maxsize=1)
def rarities() -> list[str]:
    order = ["Common", "Uncommon", "Rare", "Epic", "Legendary"]
    have = {it.get("rarity") for it in items() if it.get("rarity")}
    return [r for r in order if r in have]


@lru_cache(maxsize=1)
def _raw_item_lines() -> list[dict]:
    try:
        data = json.loads(paths.sheets_dir().joinpath("item.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return data.get("lines", [])


@lru_cache(maxsize=1)
def _raw_item_skills() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for r in _raw_item_lines():
        iid = r.get("id")
        if not iid:
            continue
        sk = [s.get("skill") if isinstance(s, dict) else s for s in (r.get("skills") or [])]
        sk = [s for s in sk if s]
        if sk:
            out[iid] = sk
    return out


@lru_cache(maxsize=1)
def _item_effect_durations() -> dict[str, int]:
    out: dict[str, int] = {}
    for r in _raw_item_lines():
        iid = r.get("id")
        if not iid:
            continue
        try:
            dur = r["props"]["effects"][0]["status"][0]["duration"]
            if dur is not None:
                out[iid] = dur
        except (KeyError, IndexError, TypeError):
            continue
    return out


def item_effect_duration(item_id: str) -> int | None:
    """Consumable status-effect duration in seconds."""
    return _item_effect_durations().get(item_id)


@lru_cache(maxsize=1024)
def _weapon_skill_ids(item_id: str) -> list[str]:
    it = _data().get("items", {}).get(item_id) or {}
    ids = it.get("skills")
    if ids is None:
        ids = _raw_item_skills().get(item_id)
    return [s.get("skill") if isinstance(s, dict) else str(s) for s in (ids or []) if s]


@lru_cache(maxsize=None)
def _skill_forms(item_id: str) -> tuple[str, ...]:
    out: list[str] = []
    for s in weapon_skills(item_id):
        if s.get("name"):
            out.append(s["name"].lower())
        if s.get("id"):
            out.append(s["id"].lower())
        d = s.get("description")
        if d:
            out.append(d.lower())
    return tuple(dict.fromkeys(f for f in out if f))


@lru_cache(maxsize=None)
def matches_skill(item_id: str, q: str) -> bool:
    """True when query matches item's weapon skill name, id, or description."""
    q = (q or "").strip().lower()
    return bool(q and any(q in t for t in _skill_forms(item_id)))


@lru_cache(maxsize=1024)
def matches_class(item_id: str, q: str) -> bool:
    """True when query matches one of the item's classes or aptitudes."""
    q = (q or "").strip().lower()
    if not q:
        return False
    it = _data().get("items", {}).get(item_id) or {}
    for c in it.get("classes") or []:
        if q in class_label(c).lower() or q in str(c).lower():
            return True
    return False


@lru_cache(maxsize=None)
def matched_skill_labels(item_id: str, q: str) -> tuple[str, ...]:
    """Skill names matching query for tag display."""
    q = (q or "").strip().lower()
    if not q:
        return ()
    out = []
    for s in weapon_skills(item_id):
        nm = s.get("name") or ""
        if not nm:
            continue
        if q in nm.lower() or q in (s.get("id") or "").lower() or q in (s.get("description") or "").lower():
            out.append(nm)
    return tuple(dict.fromkeys(out))


@lru_cache(maxsize=1024)
def weapon_skills(item_id: str) -> list[dict]:
    """Resolved weapon skills: [{id, name, type, description}]."""
    ids = _weapon_skill_ids(item_id)
    if not ids:
        return []
    from .. import names, skills
    out = []
    for sid in ids:
        row = skills.skill_row(sid) or {}
        texts = row.get("texts") or {}
        out.append({
            "id": sid,
            "name": texts.get("name") or names.skill_name(sid) or sid,
            "type": skills.skill_type(sid),
            "description": skills.skill_description(sid),
        })
    return out
