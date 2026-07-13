"""Static companion/critter index to resolve spawner elements to unit IDs."""
import json
from functools import lru_cache
from .. import paths

@lru_cache(maxsize=1)
def _critter_spawners() -> dict[str, str]:
    """Map spawner ID (e.g. 'Critters_Patrol_World_Rabbit_2') to unit ID (e.g. 'Rabbit')."""
    try:
        path = paths.critter_locs_path()
        if not path.exists():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        mapping = {}
        for c in data.get("critters", []):
            cid = c.get("id")
            if cid:
                units = c.get("units") or []
                unit = units[0] if units else (c.get("unit") or cid)
                mapping[cid] = unit
        return mapping
    except Exception:
        return {}

def resolve_spawner_unit(spawner_id: str | None) -> str | None:
    if not spawner_id:
        return None
    # Check exact match
    spawners = _critter_spawners()
    if spawner_id in spawners:
        return spawners[spawner_id]
    
    # Fallback/pattern match: extract unit name from string if possible (e.g. "Critters_Patrol_World_Rabbit_2" -> "Rabbit")
    # Companion IDs are commonly found in the spawner ID
    lower_id = spawner_id.lower()
    for sid, uid in spawners.items():
        if uid.lower() in lower_id:
            return uid
            
    # Try common companion names in the ID
    for name in ("rabbit", "frog", "lizard", "ladybug", "sheep", "goat", "demon", "squid", "crab", "bird", "owl", "penguin", "fox", "cat", "dog", "pig", "cow", "chicken", "duck", "squirrel", "turtle", "tortorock", "stinkbug"):
        if name in lower_id:
            # Capitalize first letter as is typical for unit IDs
            return name.capitalize()
            
    return None
