"""Chest loot-table resolution and static/live merge (pure policy, no memory reads).

Owns the per-chest table cache, boss re-attribution, and the merge of the static
overworld chest index with the live scene's chests, so LiveModel stays thin.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from ..data import units as udata
from ..geo import chests as chestdb

log = logging.getLogger(__name__)

# Re-export so minimap/entity code can share the proximity threshold without
# importing geo directly.
ACTIVITY_BASE_MATCH_DIST = chestdb.ACTIVITY_BASE_MATCH_DIST


def is_event_orb_id(chest_id: str | None) -> bool:
    """True for the *orbs an activity spawns* (ChestOrb / TimerCollectRun
    collectible orbs and start points, which carry an orb name segment like
    ``_Orb_`` / ``_StartOrb_`` / ``_Start_``) — these render as goldorbs,
    never as chest boxes.

    The bare base activity id (e.g. ``..._ChestOrb_3``) is the *main chest*
    that spawns those orbs and must render as a chest box, so it does NOT
    match.  End-chests like ``..._ChestOrb_3_Chest_7`` are real loot chests
    (the reward that spawns when an activity completes) and must NOT match
    either.  The ``_Chest`` *segment* check (followed by a number, then end of
    id or another separator) keeps the ``..._chestorb_...`` substring from
    triggering it.

    FightStone ids (e.g. ``FightStone``, ``..._FightStone_16``) are NOT
    goldorbs either: they are the fight-spot chests and render as chest
    boxes.

    ⛔ ORB-CHEST ACTIVITY LOGIC — DO NOT EDIT.  This classifier is the single
    source of truth for the minimap's chest-vs-goldorb decision and was
    verified live against the game's element ids (see docs/CHEST_ORB_LOGIC.md).
    Do not change it unless explicitly asked because a GAME UPDATE broke it."""
    if not chest_id:
        return False
    cid_l = chest_id.lower()
    if "chestorb" not in cid_l and "chest_orb" not in cid_l and "timercollectrun" not in cid_l:
        return False
    if re.search(r"_chest(?!orb)(?:_\d+)?(?:$|_)", cid_l):
        return False
    # Only ids carrying an orb/start name segment are goldorbs; the bare base
    # id (main chest) and end-chests fall through to False above.  Both the
    # numbered (…_Orb_10, …_StartOrb_1) and bare (…_Orb, …_Start) forms match.
    return bool(re.search(r"_(?:startorb|start|orb)_", cid_l)) or cid_l.endswith(("_orb", "_start", "_startorb"))


_END_CHEST_RE = re.compile(r"^(.*)_chest(?!orb)(?:_\d+)?(?:$|_)")


def activity_base_id(chest_id: str | None) -> str | None:
    """Extract the activity base id from a real end-chest id.

    ``..._ChestOrb_3_Chest_7`` -> ``..._ChestOrb_3`` and ``..._ChestOrb_2_Chest``
    -> ``..._ChestOrb_2``.  Returns None for ids without a real ``_Chest``
    suffix — including bare activity bases like ``..._ChestOrb_3``, where the
    only ``_chest`` occurrence is the substring inside ``chestorb``.  A naive
    ``rsplit("_chest")`` on those would produce a garbage zone prefix (e.g.
    ``z1_world_greenlands``) that accidentally matches every activity base in
    the zone and hides them all.

    ⛔ ORB-CHEST ACTIVITY LOGIC — same classifier family as is_event_orb_id();
    see docs/CHEST_ORB_LOGIC.md.
    """
    if not chest_id:
        return None
    m = _END_CHEST_RE.match(chest_id.lower())
    return m.group(1) if m else None


@dataclass
class ChestRow:
    chest_id: str
    dist: float
    loot_table: str | None
    level: int | None
    state: str | None
    live: bool
    x: float
    y: float
    z: float
    anomaly: bool = False


class ChestResolver:
    def __init__(self):
        try:
            self.chests = chestdb.load_chests()
        except Exception as e:
            log.warning("chest geo load failed (overworld POIs disabled): %s", e)
            self.chests = []
        self._static_ids = {c.chest_id for c in self.chests}
        self._table_cache: dict[str, str | None] = {}

    def chest_table(self, chest_id: str, dungeon_boss: str | None,
                    default_table: str | None = None) -> str | None:
        """Resolve a chest's loot table. Non-boss chests are cached once so they
        can't flicker; boss chests are never cached, since they re-attribute to
        the live dungeon boss each frame."""
        boss_chest = chest_id.startswith("BossChest")
        if not boss_chest and chest_id in self._table_cache:
            return self._table_cache[chest_id]
            
        tbl = default_table or chestdb.loot_table_for(chest_id)
        
        # Fallback for generic world chests that missing a table assignment
        if not tbl:
            eid_l = chest_id.lower()
            if "chest" in eid_l or "crate" in eid_l:
                tbl = "WorldCrate"

        if boss_chest and dungeon_boss and (not tbl or not udata.is_boss(tbl)):
            tbl = udata.boss_loot_table(dungeon_boss) or tbl
        if tbl and not boss_chest:
            self._table_cache[chest_id] = tbl
        return tbl

    def nearest_chests_merged(self, xyz, n: int, dungeon_boss: str | None,
                              live_chests, max_dist: float = 0.0,
                              player_zone: str | None = None,
                              use_2d: bool = False) -> list[ChestRow]:
        rows: dict[str, ChestRow] = {}
        px, py, pz = xyz
        
        static_ranks = chestdb.nearest2d(self.chests, px, py, n=10 ** 6) if use_2d \
                       else chestdb.nearest(self.chests, *xyz, n=10 ** 6)
        
        for c, d in static_ranks:
            # Exclude BossChests, Camps
            if (c.chest_id.startswith("BossChest") or
                "camp" in c.chest_id.lower() or
                "levelup" in c.chest_id.lower()):
                continue
            if player_zone:
                from ..geo import zones as geo_zones
                czone = geo_zones.chest_zone(c.chest_id)
                if czone and geo_zones.get_area_id(czone) != geo_zones.get_area_id(player_zone):
                    continue
            tbl = self.chest_table(c.chest_id, dungeon_boss, c.loot_table)
            # drop a static boss chest belonging to a different boss
            if (tbl and dungeon_boss and tbl != dungeon_boss
                    and udata.is_boss(tbl)):
                continue
            rows[c.chest_id] = ChestRow(c.chest_id, d, tbl, c.level, None, False, c.x, c.y, c.z)
        for e in live_chests:
            if not e.elem_id:
                continue
            # Exclude BossChests and checkpoints from live scan
            elem_id_lower = e.elem_id.lower()
            if "checkpoint" in elem_id_lower or "levelup" in elem_id_lower or e.elem_id.startswith("BossChest"):
                continue

            # Merge with static data if possible by finding the best ID
            best_id = e.elem_id
            if "_chest" in elem_id_lower or is_event_orb_id(e.elem_id):
                # End-chests (…_ChestOrb_3_Chest_7) are their own real chests,
                # and spawned activity orbs (…_ChestOrb_3_Orb_11) are goldorbs:
                # never collapse either into the base activity id, otherwise
                # the minimap can't render them as chest boxes / goldorbs.
                pass
            elif e.elem_id not in self._static_ids:
                # Try stripping common suffixes
                for sep in ("_StartOrb_", "_startorb_", "_Orb_", "_orb_", "_Start_", "_start_"):
                    if sep in e.elem_id:
                        cand = e.elem_id.split(sep)[0]
                        if cand in self._static_ids:
                            best_id = cand
                            break
                if best_id == e.elem_id:
                    # Fallback to longest prefix match for unique instances
                    cands = [sid for sid in self._static_ids if e.elem_id.startswith(sid)]
                    if cands:
                        best_id = max(cands, key=len)

            d = e.dist2d(px, py) if use_2d else e.dist(*xyz)
            # If we merged with a static ID, it will overwrite the static placeholder
            rows[best_id] = ChestRow(
                best_id, d, self.chest_table(best_id, dungeon_boss),
                None, e.state, True, e.x, e.y, e.z,
                anomaly=(best_id not in self._static_ids))
        def chest_priority(r: ChestRow) -> int:
            # Anomalies are prioritized so they aren't crowded out by generic crates;
            # recipes are now sorted by distance like normal chests.
            return 0 if r.anomaly else 1

        out = sorted(rows.values(), key=lambda r: (chest_priority(r), r.dist))
        if max_dist > 0:
            out = [r for r in out if r.dist <= max_dist]
        return out[:n]
