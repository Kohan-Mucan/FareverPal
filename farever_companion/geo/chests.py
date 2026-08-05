"""Static chest index: id -> world position + loot table.

Reads from scan-generated chest and POI manifests in assets/data/ that
contain full world positions and loot table assignments.

Coordinates are global world XYZ, 1:1 with the runtime player struct.
Pure data, no attached process.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from functools import lru_cache

from ..data import cdb


@dataclass(frozen=True)
class Chest:
    chest_id: str
    loot_table: str | None
    x: float
    y: float
    z: float
    faction: str | None = None
    level: int | None = None
    name: str | None = None


    def dist(self, x: float, y: float, z: float) -> float:
        return math.dist((self.x, self.y, self.z), (x, y, z))

    def dist2d(self, x: float, y: float) -> float:
        return math.hypot(self.x - x, self.y - y)


@lru_cache(maxsize=1)
def _chest_markers() -> list[dict]:
    """Raw chest entries from compiled raw data."""
    from ..data import cdb
    return cdb.lines("chest_locs")


@lru_cache(maxsize=1)
def _poi_markers() -> list[dict]:
    """Raw POI entries from compiled raw data."""
    from ..data import cdb
    return cdb.lines("poi_locs")


_COMPANION_RE = re.compile(r"^(.*)_Chest_\d+$")

# How close a reward-chest companion must be to an activity base to be the
# same physical chest.  Shared by geo/chests.py, minimap.py and
# entity_overlay.py (see chest_resolver.ACTIVITY_BASE_MATCH_DIST).
ACTIVITY_BASE_MATCH_DIST = 40.0

# Activity kinds whose bases render a marker of their own and pair with a
# `_Chest_<n>` reward chest (see _dedupe_activity_pairs).
_ORB_ACTIVITY_KINDS = ("ChestOrb", "Chest_Orb", "TimerCollectRun")


def _dedupe_activity_pairs(markers: list[dict]) -> list[dict]:
    """Drop duplicated activity markers so one physical event chest shows up once.

    The scan emits two entries per event chest: the activity *base* (e.g.
    `Z1_World_Greenlands_FightStone_11` / `..._ChestOrb_20`) and a *companion*
    (`FightStone` or `..._ChestOrb_20_Chest_1`) at (nearly) the same spot.  We
    keep the base — it drives the activity lifecycle on the map — and drop the
    companion when it actually sits near a base.

    Matching is by POSITION, never by id prefix: the game names reward chests
    `..._ChestOrb_<n>_Chest_<m>` where `<n>` does not reliably name the
    activity (e.g. `..._ChestOrb_10_Chest_2` sits on the `..._ChestOrb_12`
    base, and `..._ChestOrb_3_Chest_7` on `..._ChestOrb_16`), so an id-based
    "base exists" check would eat real chests that nothing else covers.  Only
    orb-activity companions are collapsed this way — a `..._Camp_N_Chest_1`
    near a ChestOrb base is a distinct chest and keeps its own marker.
    """
    orb_bases = [
        m for m in markers
        if (m.get("sub_kind") or "") == "activity"
        and any(t in (m.get("chest_id") or m.get("id") or "") for t in _ORB_ACTIVITY_KINDS)
    ]
    fs_instances = [
        m for m in markers
        if (m.get("chest_id") or m.get("id") or "") != "FightStone"
        and "FightStone" in (m.get("chest_id") or m.get("id") or "")
    ]

    def _near(m: dict, pool: list[dict], limit: float) -> bool:
        """True when some entry in `pool` sits within `limit` units of `m`."""
        wp = m.get("world_pos") or {}
        x, y = wp.get("x"), wp.get("y")
        if x is None or y is None:
            return False
        for inst in pool:
            iw = inst.get("world_pos") or {}
            ix, iy = iw.get("x"), iw.get("y")
            if ix is None or iy is None:
                continue
            if math.hypot(x - ix, y - iy) <= limit:
                return True
        return False

    out = []
    for m in markers:
        cid = m.get("chest_id") or m.get("id") or ""
        # bare `FightStone` mirrors a *_FightStone_<n> instance near the same spot
        if cid == "FightStone" and _near(m, fs_instances, 30.0):
            continue
        # `..._ChestOrb_N_Chest_<m>` companion that duplicates a nearby
        # orb-activity base
        if (_COMPANION_RE.match(cid)
                and any(t in cid for t in _ORB_ACTIVITY_KINDS)
                and _near(m, orb_bases, ACTIVITY_BASE_MATCH_DIST)):
            continue
        out.append(m)
    return out


@lru_cache(maxsize=1)
def load_chests() -> list[Chest]:
    """All chests from chest_locs.json that have a world position."""
    out = []
    for m in _dedupe_activity_pairs(_chest_markers()):
        # Skip generic activity markers (trigger points) that aren't chests/orbs
        if (m.get("sub_kind") or "") == "activity":
            cid = m.get("chest_id") or m.get("id", "")
            if "ChestOrb" not in cid and "FightStone" not in cid:
                continue
        wp = m.get("world_pos") or {}
        x = wp.get("x")
        y = wp.get("y")
        if x is None or y is None:
            continue
        
        lt = m.get("lootTable")
        # Fix missing data: WorldChest is used in scan but missing from tables; use WorldCrate
        if lt == "WorldChest":
            lt = "WorldCrate"
            
        out.append(Chest(
            chest_id=m.get("chest_id") or m.get("id", ""),
            loot_table=lt,
            x=float(x),
            y=float(y),
            z=float(m.get("z", 0.0)),
        ))
    return out


@lru_cache(maxsize=1)
def _index() -> dict[str, str]:
    """{chest_id -> lootTable} built from chest_locs + poi_locs."""
    out: dict[str, str] = {}
    # No _dedupe_activity_pairs() here on purpose: the dedupe drops the
    # `_Chest_<n>` / bare `FightStone` COMPANIONS, which are exactly the rows
    # that carry the real loot (WorldChest).  Map markers collapse to one per
    # spot, but the loot index must keep every id so `..._ChestOrb_10_Chest_2`
    # and `..._FightStone_11` still resolve to a real loot table.
    for m in _chest_markers():
        # Skip generic activity markers so they don't resolve loot tables
        # unless they are ChestOrbs or FightStones.
        if (m.get("sub_kind") or "") == "activity":
            cid = m.get("chest_id") or m.get("id") or ""
            if "ChestOrb" not in cid and "FightStone" not in cid:
                continue
        lt = m.get("lootTable")
        if not lt:
            continue
        
        # Fix missing data: WorldChest is used in scan but missing from tables; use WorldCrate
        if lt == "WorldChest":
            lt = "WorldCrate"

        cid = m.get("chest_id") or m.get("id")
        if cid:
            out[cid] = lt
    # POI entries carry chest_ids lists with boss loot tables
    for m in _poi_markers():
        lt = m.get("lootTable")
        if not lt:
            continue
        for cid in m.get("chest_ids") or []:
            if cid and cid not in out:
                out[cid] = lt
    return out


@lru_cache(maxsize=1)
def _table_ids() -> frozenset[str]:
    return frozenset(r["id"] for r in cdb.lines("lootTable"))


@lru_cache(maxsize=4096)
def loot_table_for(chest_id: str | None) -> str | None:
    """Resolve a chest id to its loot table, deterministically:
    1. exact match in the index;
    2. the id IS itself a loot-table id;
    3. strip trailing _<n> instance suffixes and retry 1+2;
    4. longest template-prefix match in the index.
    Pure + cached, so a given id always resolves identically (no flicker)."""
    if not chest_id:
        return None
    idx = _index()
    tables = _table_ids()
    if chest_id in idx:
        return idx[chest_id]
    if chest_id in tables:
        return chest_id
    base = chest_id
    while True:
        stripped = re.sub(r"_\d+$", "", base)
        if stripped == base:
            break
        base = stripped
        if base in idx:
            return idx[base]
        if base in tables:
            return base
    cands = [k for k in idx if chest_id.startswith(k)]
    if cands:
        return idx[max(cands, key=len)]
    return None


def nearest(chests, x: float, y: float, z: float, n: int = 10):
    ranked = sorted(((c, c.dist(x, y, z)) for c in chests), key=lambda t: t[1])
    return ranked[:n]


def nearest2d(chests, x: float, y: float, n: int = 10):
    ranked = sorted(((c, c.dist2d(x, y)) for c in chests), key=lambda t: t[1])
    return ranked[:n]
