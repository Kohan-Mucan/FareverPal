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
from ..constants import (   # offsets live in one place; re-exported for callers
    OFF_GAMELAYER, OFF_UNITS_ARR, OFF_ELEMS_ARR, OFF_OWNER, OFF_POS,
    OFF_UNITID, OFF_ELEMID, OFF_ELEMSTATE, UNIT_BLOCK,
    OFF_BOX_VALUE, CONFIG_SCAN_BYTES,
    OFF_MAIN_ACTIVITY, OFF_UATTR, OFF_LEVEL_UNIT, OFF_HEALTH,
    OFF_HERO_OWNERPLAYER, OFF_FOE_OWNER, OFF_RIFT_BOOL, OFF_WORLD_MAPID, OFF_WORLDEVENTS_ARR,
    OFF_CONFIG_CANDIDATES, OFF_CONFIG_MAPID_CANDIDATES, OFF_CONFIG_DIFF_CANDIDATES
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
PLAYER_OWNER_CLASSES = {"ent.Hero", "ent.hero.Warrior", "ent.hero.Rogue", "ent.hero.Mage", "ent.hero.Priest"}


@dataclass
class Entity:
    addr: int
    cls: str | None          # leaf class, e.g. "ent.Foe", "ent.foe.Boss"
    unit_id: str | None
    x: float
    y: float
    z: float
    level: int = 0
    hp: float = 0.0
    owner_addr: int = 0
    owner_cls: str | None = None
    is_foe: bool = False     # descends from ent.Foe (super-chain)
    is_hero: bool = False    # descends from ent.Hero

    @property
    def is_player_owned(self) -> bool:
        if not self.owner_cls:
            return False
        if self.owner_cls == "st.Player":
            return True
        # Match any Hero class or generic ent.Hero
        return self.owner_cls == "ent.Hero" or self.owner_cls.startswith("ent.hero.")

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

    def dist2d(self, x: float, y: float) -> float:
        return math.hypot(self.x - x, self.y - y)


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
        k = _ELEM_KINDS.get(self.cls or "", (self.cls or "")
                               .replace("ent.interactible.", "").replace("ent.", ""))
        if self.elem_id:
            eid_l = self.elem_id.lower()
            if self.is_chest:
                return "chest"
            if self.is_orb:
                return "orb"
            if self.is_obelisk:
                if "checkpoint" in eid_l or "finish_" in eid_l or "start_" in eid_l:
                    if self.state and self.state.lower() == "completed":
                        return "chest_orb"
                if "respawn" in eid_l:
                    return "respawn"
                return "obelisk"
            if self.is_gatherable:
                return "gatherable"
            if self.is_teleporter:
                return "dungeon"
        return k

    @property
    def is_gatherable(self) -> bool:
        if self.cls == "ent.interactible.Gatherable":
            return True
        if self.cls == "ent.Element" and self.elem_id:
            eid_l = self.elem_id.lower()
            return "gather" in eid_l or "ore" in eid_l or "flower" in eid_l
        return False

    @property
    def is_chest(self) -> bool:
        if self.cls == "ent.interactible.Chest":
            return True
        if self.cls == "ent.Element" and self.elem_id:
            eid_l = self.elem_id.lower()
            return ("chest" in eid_l or "crate" in eid_l or "recipe" in eid_l) and "orb" not in eid_l and "checkpoint" not in eid_l and "levelup" not in eid_l
        return False

    @property
    def is_obelisk(self) -> bool:
        if self.cls in ("ent.interactible.Obelisk", "ent.interactible.RespawnPoint"):
            return True
        if self.cls == "ent.Element" and self.elem_id:
            eid_l = self.elem_id.lower()
            return "obelisk" in eid_l or "respawn" in eid_l or "checkpoint" in eid_l or "start_" in eid_l or "finish_" in eid_l
        return False

    @property
    def is_orb(self) -> bool:
        if self.cls == "ent.interactible.InstanceOrb":
            return True
        if self.cls == "ent.Element" and self.elem_id:
            eid_l = self.elem_id.lower()
            return ("orb" in eid_l or "secretorb" in eid_l or "timercollectrun" in eid_l) and "instanceorb" not in eid_l
        return False

    @property
    def is_teleporter(self) -> bool:
        if not self.cls:
            return False
        cls_lower = self.cls.lower()
        if "teleporter" in cls_lower or "portal" in cls_lower or "dungeon" in cls_lower:
            return True
        if self.cls == "ent.Element" and self.elem_id:
            eid_l = self.elem_id.lower()
            return "teleporter" in eid_l or "portal" in eid_l or "dungeon" in eid_l or "instanceorb" in eid_l
        return False

    def dist(self, x: float, y: float, z: float) -> float:
        return math.dist((self.x, self.y, self.z), (x, y, z))

    def dist2d(self, x: float, y: float) -> float:
        return math.hypot(self.x - x, self.y - y)


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
        outside an instance. Independent of enemy levels."""
        gl = self.gamelayer(pbase)
        if gl is None:
            return None
            
        # 1. Try candidates from constants.py
        for off in OFF_CONFIG_CANDIDATES:
            try:
                cfg = self.hl.ptr(gl + off)
                if cfg and self._is_config_struct(cfg):
                    self._cfg_off = off
                    return self._difficulty_at(cfg)
            except: pass

        if self._cfg_off is not None:
            cfg = self.hl.ptr(gl + self._cfg_off)
            if cfg and self._is_config_struct(cfg):
                return self._difficulty_at(cfg)
            self._cfg_off = None
            
        # 2. Fallback: Scan GameLayer for the config struct
        now = time.monotonic()
        if now - self._cfg_scan_at < self.CONFIG_RESCAN_TTL:
            return None
        self._cfg_scan_at = now
        blk = self.proc.try_read(gl, CONFIG_SCAN_BYTES)
        if blk is None:
            return None
        
        # Using a while loop to bypass environment linter issues with 'range'
        i = 0
        while i < (len(blk) - 7):
            p = struct.unpack_from("<Q", blk, i)[0]
            if is_ptr(p) and self._is_config_struct(p):
                self._cfg_off = i
                return self._difficulty_at(p)
            i += 8
        return None

    def _is_config_struct(self, cfg: int) -> bool:
        """Validates that cfg points to a st.Config-like struct."""
        # Try mapId candidates from constants.py
        for off in OFF_CONFIG_MAPID_CANDIDATES:
            try:
                mp = self.hl.ptr(cfg + off)
                if is_ptr(mp) and self.hl.class_of(mp) == "String":
                    s = self.hl.hl_string(mp)
                    if s and ("POI" in s or "World" in s or "Dungeon" in s or "Z1" in s):
                        return True
            except:
                pass
        return False

    def _difficulty_at(self, cfg: int | None) -> int | None:
        """Read difficulty from a candidate config struct."""
        if not cfg:
            return None
            
        # Try difficulty candidates from constants.py
        for off in OFF_CONFIG_DIFF_CANDIDATES:
            # 1. Try as boxed Null<Int>
            box = self.hl.ptr(cfg + off)
            if is_ptr(box):
                try:
                    raw = self.proc.try_read(box + OFF_BOX_VALUE, 4)
                    if raw:
                        v = struct.unpack("<i", raw)[0]
                        if v in (0, 1): return v
                except: pass
            
            # 2. Try as raw i32
            try:
                raw = self.proc.try_read(cfg + off, 4)
                if raw:
                    v = struct.unpack("<i", raw)[0]
                    if v in (0, 1): return v
            except: pass
            
        return None

    def map_id(self, pbase: int | None) -> str | None:
        """The internal map/zone ID (e.g. 'POI_Forest_Z1' or 'World_Z1')."""
        gl = self.gamelayer(pbase)
        if not gl: return None
        
        # 1. Try Config.mapId
        self.difficulty(pbase)
        if self._cfg_off:
            cfg = self.hl.ptr(gl + self._cfg_off)
            if cfg:
                # Use the first valid mapId candidate
                for off in OFF_CONFIG_MAPID_CANDIDATES:
                    mid_ptr = self.hl.ptr(cfg + off)
                    if mid_ptr:
                        s = self.hl.hl_string(mid_ptr)
                        if s and len(s) > 1:
                            return s

        # 2. Try MainActivity.activityId (Common in dungeons)
        act = self.hl.ptr(gl + OFF_MAIN_ACTIVITY)
        if act:
            tp_act = self.hl.ptr(act)
            if tp_act:
                # Try many common field names for IDs in activity objects
                for fn in ("activityId", "mapId", "id", "name", "type"):
                    off = self.hl.field_offset(tp_act, fn)
                    if off:
                        sptr = self.hl.ptr(act + off)
                        if is_ptr(sptr) and self.hl.class_of(sptr) == "String":
                            s = self.hl.hl_string(sptr)
                            if s and len(s) > 1: return s

        # 3. Try World object (Common in open world)
        world = self.hl.ptr(gl + OFF_WORLD_MAPID)
        if world:
            tp_world = self.hl.ptr(world)
            if tp_world:
                for fn in ("mapId", "id", "name", "zoneId"):
                    off = self.hl.field_offset(tp_world, fn)
                    if off:
                        sptr = self.hl.ptr(world + off)
                        if is_ptr(sptr) and self.hl.class_of(sptr) == "String":
                            s = self.hl.hl_string(sptr)
                            if s and len(s) > 1: return s
        
        # 4. Try scanning Config for ANY string that looks like a Map ID
        if self._cfg_off:
            cfg = self.hl.ptr(gl + self._cfg_off)
            if cfg:
                # Scan first 0x40 bytes for a String pointer
                for scan_off in range(0, 0x40, 8):
                    sptr = self.hl.ptr(cfg + scan_off)
                    if is_ptr(sptr) and self.hl.class_of(sptr) == "String":
                        s = self.hl.hl_string(sptr)
                        if s and ("POI" in s or "World" in s or "Z1" in s or "Z2" in s or "Dungeon" in s):
                            return s
        return None

    def activity_id(self, pbase: int | None) -> str | None:
        """The current activity ID (e.g. 'Dungeon_Wolf_Hard')."""
        gl = self.gamelayer(pbase)
        if not gl: return None
        
        # 1. Try Config object
        self.difficulty(pbase)
        if self._cfg_off:
            cfg = self.hl.ptr(gl + self._cfg_off)
            if cfg:
                # activityID is the first field (offset 0) or offset 0x08 in the config struct
                for off in (0, 8, 16):
                    sptr = self.hl.ptr(cfg + off)
                    if is_ptr(sptr) and self.hl.class_of(sptr) == "String":
                        s = self.hl.hl_string(sptr)
                        if s and len(s) > 1 and "shard" not in s.lower(): return s
        
        # 2. Try MainActivity directly
        act = self.hl.ptr(gl + OFF_MAIN_ACTIVITY)
        if act:
            tp_act = self.hl.ptr(act)
            if tp_act:
                for fn in ("activityId", "id", "name", "activity_id", "type"):
                    off = self.hl.field_offset(tp_act, fn)
                    if off:
                        sptr = self.hl.ptr(act + off)
                        if is_ptr(sptr) and self.hl.class_of(sptr) == "String":
                            s = self.hl.hl_string(sptr)
                            if s and len(s) > 1: return s
        return None

    def shard_id(self, pbase: int | None) -> str | int | None:
        """The current world shard/instance ID or name."""
        gl = self.gamelayer(pbase)
        if not gl: return None
        
        # 1. Try GameLayer.serverName (Found in your debug dump)
        tp_gl = self.hl.ptr(gl)
        if tp_gl:
            off_sn = self.hl.field_offset(tp_gl, "serverName")
            if off_sn:
                sptr = self.hl.ptr(gl + off_sn)
                if sptr:
                    return self.hl.hl_string(sptr) if sptr else None

    def get_live_worldevent_rift_zone(self, pbase: int | None) -> str | None:
        """Reads active Rift zone from GameLayer.worldEvents -> currentEvents -> activeRift."""
        gl = self.gamelayer(pbase)
        if not gl:
            return None
        try:
            we = self.hl.ptr(gl + OFF_WORLDEVENTS_ARR)
            if not is_ptr(we):
                return None
            
            # st.event.WorldEvents.currentEvents (+0x0098)
            ce_proxy = self.hl.ptr(we + 0x0098)
            if not is_ptr(ce_proxy):
                return None
            
            # hxbit.ArrayProxyData.array (+0x0028) -> ArrayDyn
            arr_dyn = self.hl.ptr(ce_proxy + 0x0028)
            if not is_ptr(arr_dyn):
                return None
            
            # ArrayDyn.array (+0x0008) -> ArrayObj
            arr_obj = self.hl.ptr(arr_dyn + 0x0008)
            if not is_ptr(arr_obj):
                return None
            
            # ArrayObj length (+0x08) & array pointer (+0x10) -> NativeArray
            length = self.proc.read_i32(arr_obj + 0x08)
            if length <= 0 or length > 100:
                return None
            
            na_ptr = self.hl.ptr(arr_obj + 0x10) or self.hl.ptr(arr_obj + 0x0C)
            if not is_ptr(na_ptr):
                return None
            
            # NativeArray elements start at +0x18
            for i in range(min(length, 10)):
                evt_ptr = self.hl.ptr(na_ptr + 0x18 + (i * 8))
                if is_ptr(evt_ptr):
                    cls_name = self.hl.class_of(evt_ptr) or ""
                    if "Rift" in cls_name or "WorldEvent" in cls_name:
                        for str_off in (0x0100, 0x00F4, 0x00EC, 0x00B0):
                            active_rift_ptr = self.hl.ptr(evt_ptr + str_off)
                            if is_ptr(active_rift_ptr):
                                s = self.hl.hl_string(active_rift_ptr)
                                if s and ("POI" in s or "Rift" in s or "Z1" in s or "Z2" in s):
                                    return s
        except Exception:
            pass
        return None

        # 2. Try Config.shardId / lobbyId
        self.difficulty(pbase)
        if self._cfg_off:
            cfg = self.hl.ptr(gl + self._cfg_off)
            if cfg:
                tp_cfg = self.hl.ptr(cfg)
                if tp_cfg:
                    for fn in ("shardId", "lobbyId", "instanceId", "channel"):
                        off_cfg = self.hl.field_offset(tp_cfg, fn)
                        if off_cfg:
                            # Try as string first, then int
                            val = self.hl.ptr(cfg + off_cfg)
                            if val and self.hl.class_of(val) == "String":
                                return self.hl.hl_string(val)
                            return self.hl.i32(cfg + off_cfg)
        return None

    def in_dungeon(self, pbase: int | None) -> bool:
        """True when the player is inside a dungeon instance."""
        gl = self.gamelayer(pbase)
        if gl is None:
            return False
            
        # 1. Primary: Activity class check (fastest)
        act = self.hl.ptr(gl + OFF_MAIN_ACTIVITY)
        if act and self.hl.is_a(act, "st.activity.Dungeon"):
            return True
            
        # 2. Robust Fallback: Map ID check via Config (covers drift in Activity offsets)
        mid = self.map_id(pbase)
        if mid and ("POI_" in mid or "Dungeon_" in mid):
            return True
            
        return False

    def is_rift(self, pbase: int | None) -> bool:
        """True when the current layer is a Rift."""
        gl = self.gamelayer(pbase)
        if gl is None:
            return False
        # Try known offset for isRift boolean
        raw = self.proc.try_read(gl + OFF_RIFT_BOOL, 1)
        if raw and struct.unpack("<?", raw)[0]:
            return True
        # Fallback: check activity name
        aid = self.activity_id(pbase)
        return aid is not None and "Rift" in aid

    def _clean_id(self, raw_id: str | None) -> str | None:
        """Handle path-like IDs and Clone suffixes returned by some engine versions."""
        if not raw_id:
            return raw_id
        # 1. Strip (Clone), (Instance), etc.
        clean = raw_id.split("(")[0].strip()
        # 2. Handle path-like IDs (e.g. 'Units/Enemies/Wolf/Wolf_Z1W.prefab' -> 'Wolf_Z1W')
        if "/" in clean or "\\" in clean:
            import os
            clean = os.path.splitext(os.path.basename(clean))[0]
            
        # 3. Handle trailing variants for patrol/unique units (e.g. _Elite, _1)
        # to ensure they match their static CDB definitions.
        if "Patrol" in clean:
            for suffix in ("_Elite", "_1", "_2", "_3", "_4", "_5"):
                if clean.endswith(suffix):
                    clean = clean[:-len(suffix)]
                    break

        return clean

    def units(self, pbase: int | None) -> list[Entity]:
        gl = self.gamelayer(pbase)
        if gl is None:
            return []
        ptrs = [p for p in self.hl.array(self.hl.ptr(gl + OFF_UNITS_ARR)) if is_ptr(p)]
        if not ptrs:
            return []
        blocks = self.proc.read_many(ptrs, UNIT_BLOCK)
        
        # Collect attribute pointers for batch reading HP
        attr_ptrs = []
        for blk in blocks:
            if blk and len(blk) >= OFF_UATTR + 8:
                ap = struct.unpack_from("<Q", blk, OFF_UATTR)[0]
                attr_ptrs.append(ap if is_ptr(ap) else 0)
            else:
                attr_ptrs.append(0)
        
        out: list[Entity] = []
        for ptr, blk, attr_ptr in zip(ptrs, blocks, attr_ptrs):
            if blk is None or len(blk) < UNIT_BLOCK:
                continue
            type_ptr = struct.unpack_from("<Q", blk, 0)[0]
            owner_ptr = struct.unpack_from("<Q", blk, OFF_OWNER)[0]
            # Try Hero-to-Player owner at 0x498 (OFF_HERO_OWNERPLAYER)
            hero_owner_ptr = 0
            if len(blk) >= OFF_HERO_OWNERPLAYER + 8:
                hero_owner_ptr = struct.unpack_from("<Q", blk, OFF_HERO_OWNERPLAYER)[0]
            
            x, y, z = struct.unpack_from("<ddd", blk, OFF_POS)
            uid_ptr = struct.unpack_from("<Q", blk, OFF_UNITID)[0]
            lvl = struct.unpack_from("<i", blk, OFF_LEVEL_UNIT)[0]
            
            # Read HP if we have an attribute pointer
            hp = 0.0
            if attr_ptr:
                hp_raw = self.proc.try_read(attr_ptr + OFF_HEALTH, 8)
                if hp_raw:
                    hp = struct.unpack("<d", hp_raw)[0]

            anc = self.hl.ancestors(type_ptr)
            
            unit_id = self.hl.hl_string(uid_ptr) if is_ptr(uid_ptr) else None
            unit_id = self._clean_id(unit_id)
            
            # Determine the owner:
            # o2 = OFF_HERO_OWNERPLAYER (0x498): Hero's ownerPlayer -> st.Player (highest authority)
            # o1 = OFF_FOE_OWNER (0x78):  Foe/pet's player field -> ent.Hero owner (confirmed via dump_elements)
            o1 = struct.unpack_from("<Q", blk, OFF_FOE_OWNER)[0]       # Foe/pet -> Hero owner
            o2 = struct.unpack_from("<Q", blk, OFF_HERO_OWNERPLAYER)[0] if len(blk) > OFF_HERO_OWNERPLAYER else 0  # Hero -> st.Player
            
            final_owner_ptr = 0
            final_owner_cls = None
            
            # 1. Try to find a st.Player directly (highest authority)
            for cand in (o2, o1):
                if is_ptr(cand):
                    cls = self.hl.class_of(cand)
                    if cls == "st.Player":
                        final_owner_ptr, final_owner_cls = cand, cls
                        break
            
            # 2. If no Player found, look for an ent.Hero subclass (typical for pets)
            if not final_owner_ptr:
                for cand in (o2, o1):
                    if is_ptr(cand):
                        cls = self.hl.class_of(cand)
                        if cls == "ent.Hero" or (cls and cls.startswith("ent.hero.")):
                            final_owner_ptr, final_owner_cls = cand, cls
                            break

            # 3. Last resort: any valid pointer (for engine-owned objects)
            if not final_owner_ptr:
                for cand in (o1, o2): # Prefer o1 (GameObject.owner) for non-hero units
                    if is_ptr(cand):
                        final_owner_ptr = cand
                        final_owner_cls = self.hl.class_of(cand)
                        break

            out.append(Entity(
                addr=ptr,
                cls=self.hl.type_name(type_ptr),
                unit_id=unit_id,
                owner_addr=final_owner_ptr,
                owner_cls=final_owner_cls,
                x=x, y=y, z=z,
                level=lvl,
                hp=hp,
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
            
            elem_id = self.hl.hl_string(eid_ptr) if is_ptr(eid_ptr) else None
            elem_id = self._clean_id(elem_id)
            
            out.append(Element(
                addr=ptr,
                cls=self.hl.type_name(type_ptr),
                elem_id=elem_id,
                state=self.hl.hl_string(state_ptr) if is_ptr(state_ptr) else None,
                x=x, y=y, z=z,
            ))
        return out
