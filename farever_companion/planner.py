"""User planner data storage (farm list & craft queue in planner.json)."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from .config import config_dir
from .persist import atomic_write_json


def _path() -> Path:
    return config_dir() / "planner.json"


@lru_cache(maxsize=1)
def _data() -> dict:
    try:
        return json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _farm_items() -> list[str]:
    return list(_data().get("farm") or [])


def gear_item_ids() -> list[str]:
    """Saved farm list item ids in add order."""
    return _farm_items()


def has_gear(item_id: str) -> bool:
    return item_id in _farm_items()


def add_gear(item_id: str) -> bool:
    """Add item to farm list (no-op if present). Returns True if newly added."""
    items = _farm_items()
    if item_id in items:
        return False
    items.append(item_id)
    _save(items, queue_entries(), queue_got())
    return True


def remove_gear(item_id: str) -> None:
    items = _farm_items()
    if item_id in items:
        items.remove(item_id)
        _save(items, queue_entries(), queue_got())


def clear_gear() -> None:
    _save([], queue_entries(), queue_got())


def queue_entries() -> list[dict]:
    """Saved crafting queue entries [{item, qty, name}]."""
    return [dict(e) for e in _data().get("craft_queue", {}).get("entries", [])]


def queue_got() -> dict[str, int]:
    """Gather progress state {material: count}."""
    return dict(_data().get("craft_queue", {}).get("got", {}))


def queue_save(entries: list[dict], got: dict[str, int]) -> None:
    """Persist crafting queue and gather progress."""
    _save(_farm_items(), [dict(e) for e in entries], dict(got))


def _save(farm: list[str], entries: list[dict], got: dict[str, int]) -> None:
    atomic_write_json(_path(), {
        "farm": farm,
        "craft_queue": {"entries": entries, "got": got},
    })
    _data.cache_clear()
