"""User planner data storage (farm list & craft queue in planner.json)."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from ..config import config_dir
from ..runtime.persist import atomic_write_json


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
    }, compact_lists=True)
    _data.cache_clear()


def _loadout_path() -> Path:
    return config_dir() / "loadout.json"


def load_global_loadout() -> dict:
    """Read saved offline loadout data from loadout.json.

    Cached per path: the Loadout page and the gear picker ask about EVERY
    tile they draw, and one read of this file also walks `config_dir()`
    (two `Path.resolve()` + a `mkdir()`) — measured 2026-09-25, 192 tiles
    cost ~92 ms of pure filesystem syscalls and dominated the Collection
    Manager switch. `save_global_loadout` clears the cache, so the cached
    copy is only ever one write behind.
    """
    return dict(_owned_data(str(_loadout_path())))


@lru_cache(maxsize=4)
def _owned_data(path: str) -> dict:
    """Parsed loadout.json keyed by its path (see load_global_loadout)."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_global_loadout(data: dict) -> None:
    """Persist offline loadout choices to loadout.json."""
    atomic_write_json(_loadout_path(), dict(data))
    _owned_data.cache_clear()


def add_to_owned_collection(item_id: str) -> bool:
    """Add item_id to offline owned gear collection in loadout.json."""
    data = load_global_loadout()
    coll = list(data.get("collection") or [])
    if item_id in coll:
        return False
    coll.append(item_id)
    data["collection"] = coll
    save_global_loadout(data)
    return True


def remove_from_owned_collection(item_id: str) -> None:
    data = load_global_loadout()
    coll = list(data.get("collection") or [])
    if item_id in coll:
        coll.remove(item_id)
        data["collection"] = coll
        save_global_loadout(data)


def is_in_owned_collection(item_id: str) -> bool:
    """True when item_id is in the offline owned collection.

    Reads the cached parse (see load_global_loadout) — the picker calls this
    once per listed row, so an uncached read here cost a file open per row.
    """
    if not item_id:
        return False
    return str(item_id) in (_owned_data(str(_loadout_path())).get(
        "collection") or ())
