"""Live scene reader (read-only, batched).

One batched read_many pulls a header block for every unit at once. An enemy is
anything whose type descends from ent.Foe (HL super-chain) and is not hero-owned.

Verified scene path (Farever EA, 2026-05-24):

    pbase (ent.Hero) +0x58  -> st.GameLayer
    st.GameLayer     +0x128 -> ArrayObj  (units: ent.Hero + ent.Foe subclasses)
    st.GameLayer     +0x120 -> ArrayObj  (elements: interactibles, no units)
    per unit: +0x60 owner, +0x98/A0/A8 xyz (f64), +0x250 unit-id, +0x3D0 attributes
"""
from __future__ import annotations

import math
import struct
import time
from dataclasses import dataclass

from .hl import Hl, is_ptr
from .proc import Proc
from .constants import (   # offsets live in one place; re-exported for callers
    OFF_GAMELAYER, OFF_UNITS_ARR, OFF_ELEMS_ARR, OFF_OWNER, OFF_POS,
    OFF_UNITID, OFF_ELEMID, OFF_ELEMSTATE, UNIT_BLOCK,
    OFF_CONFIG_DIFFICULTY, OFF_CONFIG_MAPID, OFF_BOX_VALUE, CONFIG_SCAN_BYTES,
    OFF_MAIN_ACTIVITY,
)

_ELEM_KINDS = {
    "ent.interactible.Gatherable": "gatherable",
    "ent.interactible.Chest": "chest",
    "ent.interactible.Obelisk": "obelisk",
    "ent.interactible.Npc": "npc",
    "ent.interactible.Refresher": "refresher",
    "ent.interactible.Bumper": "bumper",
    "ent.interactible.MobilePlatform": "platform",
    "ent.interactible.RespawnPoint": "respawn",
    "ent.interactible.InstanceOrb": "orb",
    "ent.interactible.Teleporter": "dungeon",   # dungeon entrances / teleports
}
PLAYER_OWNER_CLASSES = {"ent.Hero"}


@dataclass
class Entity:
    addr: int
    cls: str | None          # leaf class, e.g. "ent.Foe", "ent.foe.Boss"
    unit_id: str | None
    owner_cls: str | None
    x: float
    y: float
    z: float
    is_foe: bool = False     # descends from ent.Foe (super-chain)
    is_hero: bool = False    # descends from ent.Hero

    @property
    def is_player_owned(self) -> bool:
        return self.owner_cls in PLAYER_OWNER_CLASSES

    @property
    def is_enemy(self) -> bool:
        return self.is_foe and not self.is_player_owned

    @property
    def kind(self) -> str:
        if self.is_hero:
            return "hero"
        if self.is_foe:
            return "companion" if self.is_player_owned else "enemy"
        return self.cls or "?"

    def dist(self, x: float, y: float, z: float) -> float:
        return math.dist((self.x, self.y, self.z), (x, y, z))


@dataclass
class Element:
    addr: int
    cls: str | None
    elem_id: str | None
    state: str | None
    x: float
    y: float
    z: float

    @property
    def kind(self) -> str:
        return _ELEM_KINDS.get(self.cls or "", (self.cls or "")
                               .replace("ent.interactible.", "").replace("ent.", ""))

    @property
    def is_gatherable(self) -> bool:
        return self.cls == "ent.interactible.Gatherable"

    @property
    def is_chest(self) -> bool:
        return self.cls == "ent.interactible.Chest"

    @property
    def is_obelisk(self) -> bool:
        return self.cls in ("ent.interactible.Obelisk", "ent.interactible.RespawnPoint")

    @property
    def is_orb(self) -> bool:
        return self.cls == "ent.interactible.InstanceOrb"

    @property
    def is_teleporter(self) -> bool:
        if not self.cls:
            return False
        cls_lower = self.cls.lower()
        return "teleporter" in cls_lower or "portal" in cls_lower or "dungeon" in cls_lower

    def dist(self, x: float, y: float, z: float) -> float:
        return math.dist((self.x, self.y, self.z), (x, y, z))


class Scene:
    CONFIG_RESCAN_TTL = 1.0    # max once/sec to find the config when uncached

    def __init__(self, proc: Proc, hl: Hl):
        self.proc = proc
        self.hl = hl
        self._cfg_off: int | None = None   # discovered GameLayer.config offset
        self._cfg_scan_at = 0.0            # last full config scan (throttle)

    def gamelayer(self, pbase: int | None) -> int | None:
        if not pbase:
            return None
        return self.hl.ptr(pbase + OFF_GAMELAYER)

    def difficulty(self, pbase: int | None) -> int | None:
        """Instance difficulty from GameLayer.config: 0=Normal, 1=Hard, None
        outside an instance. Independent of enemy levels. The config pointer's
        offset on GameLayer drifts between builds, so it's discovered by signature
        (mapId contains 'POI') and cached; see core/constants."""
        gl = self.gamelayer(pbase)
        if gl is None:
            return None
        if self._cfg_off is not None:
            d = self._difficulty_at(self.hl.ptr(gl + self._cfg_off))
            if d is not None:
                return d
            self._cfg_off = None
        # uncached (outside an instance, or just entered): throttle the GameLayer
        # sweep so a 20 Hz caller can't hammer it. Cached reads above stay O(1).
        now = time.monotonic()
        if now - self._cfg_scan_at < self.CONFIG_RESCAN_TTL:
            return None
        self._cfg_scan_at = now
        blk = self.proc.try_read(gl, CONFIG_SCAN_BYTES)
        if blk is None:
            return None
        for off in range(0, len(blk) - 7, 8):
            p = struct.unpack_from("<Q", blk, off)[0]
            if not is_ptr(p):
                continue
            d = self._difficulty_at(p)
            if d is not None:
                self._cfg_off = off
                return d
        return None

    def in_dungeon(self, pbase: int | None) -> bool:
        """True when the player is inside a dungeon instance.

        Reads GameLayer.mainActivity (fixed offset 0xd8) and checks whether its
        class descends from st.activity.Dungeon.  This is the same signal the
        game itself uses: the field is set when the layer is a dungeon instance
        and cleared (or a different type) in the open world / town zones.  Far
        more reliable than the old POI-mapId scan which missed dungeons whose
        mapId doesn't contain 'POI'.
        """
        gl = self.gamelayer(pbase)
        if gl is None:
            return False
        act = self.hl.ptr(gl + OFF_MAIN_ACTIVITY)
        if act is None:
            return False
        cls = self.hl.class_of(act)
        return cls == "st.activity.Dungeon"

    def _difficulty_at(self, cfg: int | None) -> int | None:
        """Read difficulty from a candidate config struct, validating it's the
        real one (mapId is a 'POI' String, difficulty box holds 0/1)."""
        if not cfg:
            return None
        mp = self.hl.ptr(cfg + OFF_CONFIG_MAPID)
        s = self.hl.hl_string(mp) if mp else None
        if not s or "POI" not in s:
            return None
        box = self.hl.ptr(cfg + OFF_CONFIG_DIFFICULTY)
        if not box:
            return None
        raw = self.proc.try_read(box + OFF_BOX_VALUE, 4)
        if raw is None:
            return None
        v = struct.unpack("<i", raw)[0]
        return v if v in (0, 1) else None

    def units(self, pbase: int | None) -> list[Entity]:
        gl = self.gamelayer(pbase)
        if gl is None:
            return []
        ptrs = [p for p in self.hl.array(self.hl.ptr(gl + OFF_UNITS_ARR)) if is_ptr(p)]
        if not ptrs:
            return []
        blocks = self.proc.read_many(ptrs, UNIT_BLOCK)
        out: list[Entity] = []
        for ptr, blk in zip(ptrs, blocks):
            if blk is None or len(blk) < UNIT_BLOCK:
                continue
            type_ptr = struct.unpack_from("<Q", blk, 0)[0]
            owner_ptr = struct.unpack_from("<Q", blk, OFF_OWNER)[0]
            x, y, z = struct.unpack_from("<ddd", blk, OFF_POS)
            uid_ptr = struct.unpack_from("<Q", blk, OFF_UNITID)[0]
            anc = self.hl.ancestors(type_ptr)
            out.append(Entity(
                addr=ptr,
                cls=self.hl.type_name(type_ptr),
                unit_id=self.hl.hl_string(uid_ptr) if is_ptr(uid_ptr) else None,
                owner_cls=self.hl.class_of(owner_ptr) if is_ptr(owner_ptr) else None,
                x=x, y=y, z=z,
                is_foe="ent.Foe" in anc,
                is_hero="ent.Hero" in anc,
            ))
        return out

    def elements(self, pbase: int | None) -> list[Element]:
        gl = self.gamelayer(pbase)
        if gl is None:
            return []
        ptrs = [p for p in self.hl.array(self.hl.ptr(gl + OFF_ELEMS_ARR)) if is_ptr(p)]
        if not ptrs:
            return []
        block = OFF_ELEMSTATE + 8
        blocks = self.proc.read_many(ptrs, block)
        out: list[Element] = []
        for ptr, blk in zip(ptrs, blocks):
            if blk is None or len(blk) < block:
                continue
            type_ptr = struct.unpack_from("<Q", blk, 0)[0]
            x, y, z = struct.unpack_from("<ddd", blk, OFF_POS)
            eid_ptr = struct.unpack_from("<Q", blk, OFF_ELEMID)[0]
            state_ptr = struct.unpack_from("<Q", blk, OFF_ELEMSTATE)[0]
            out.append(Element(
                addr=ptr,
                cls=self.hl.type_name(type_ptr),
                elem_id=self.hl.hl_string(eid_ptr) if is_ptr(eid_ptr) else None,
                state=self.hl.hl_string(state_ptr) if is_ptr(state_ptr) else None,
                x=x, y=y, z=z,
            ))
        return out
