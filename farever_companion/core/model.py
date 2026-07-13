"""LiveModel: one immutable per-tick snapshot of the game, shared by every view."""
from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass

log = logging.getLogger(__name__)

from .proc import Proc, ProcError
from .hl import Hl
from .scene import Scene, Entity, Element
from .player import PlayerLocator
from .camera import ViewCamera
from .chest_resolver import ChestResolver, ChestRow
from .damage_source import DamageSourceManager
from . import attributes
from ..combat.dps import DpsMeter
from ..constants import OFF_HERO_OWNERPLAYER
from ..data import loot, units as udata, rarity as rarity_mod, encounters as encdata

XYZ = tuple[float, float, float]


@dataclass
class Nearest:
    kind: str               # 'enemy' | 'chest'
    label: str
    dist: float
    loot_table: str | None
    level: int
    note: str = ""


class LiveModel:
    UNITS_TTL = 0.10
    CHEST_DIST_BIAS = 0.5      # chests win over a slightly-closer enemy

    def __init__(self, proc: Proc):
        self.proc = proc
        self.hl = Hl(proc)
        self.scene = Scene(proc, self.hl)
        self.locator = PlayerLocator(proc, self.hl)
        self.view = ViewCamera(proc, self.hl, self.locator.app)
        self.chests_resolver = ChestResolver()
        self._units_cache: list[Entity] = []
        self._units_at = 0.0
        self.dungeon_boss: str | None = None
        self.dps = DpsMeter()
        self.damage = DamageSourceManager(proc)
        self._combat_at = 0.0
        self.player_hp_log: deque[tuple[float, float]] = deque(maxlen=240)
        self.player_max_hp: float = 0.0
        self.deaths: int = 0
        self._was_alive: bool = False
        self.units_ok: bool = True   # False while the units read fails (zone swap)
        self._last_profile: str | None = None

    # --- lifecycle -------------------------------------------------------
    def locate_player(self) -> int | None:
        addr = self.locator.locate()
        if addr:
            self.damage.warmup(addr)
        return addr

    @property
    def dps_events(self) -> DpsMeter:
        return self.damage.dps_events

    @property
    def per_skill_enabled(self) -> bool:
        return self.damage.per_skill_enabled

    def per_skill_status(self) -> str:
        return self.damage.status()

    def per_skill_progress(self) -> tuple[str, float]:
        return self.damage.progress()

    def recalibrate_skills(self) -> None:
        self.damage.recalibrate()

    def camera_yaw(self) -> float | None:
        """The gameplay camera's orbit yaw (radians). Unlike the body heading
        (ent.Entity.rotationZ at OFF_HEADING, which only turns while moving), the
        camera follows the mouse even while the player stands still - so the
        minimap rotates with where you're looking, not just where you last moved.

        Read from GameApp.camera (client.BaseCamera).curDirection, the smoothed
        current orbit angle, with the field offset resolved by name via HL
        reflection (no hardcoded layout). None until the camera + field resolve,
        in which case the minimap falls back to the body heading. The angle's
        zero/sign convention differs from the world heading, so the minimap aligns
        it with CAM_YAW_SIGN / CAM_YAW_OFFSET (tune live)."""
        return self._camera_f64("curDirection")

    def camera_pitch(self) -> float | None:
        """The camera's tilt (client.BaseCamera.curPitch, radians): 0 = level
        with the horizon, negative = looking down (validated live: the default
        gameplay camera reads ~-0.94). Drives the compass needle's ground-plane
        foreshortening."""
        return self._camera_f64("curPitch")

    def view_matrix(self) -> list[float] | None:
        """The engine camera's world->screen view-proj matrix (16 floats, row
        major), or None while unresolved. The exact projection the game draws
        with - see core/camera.py."""
        return self.view.matrix()

    def _camera_f64(self, field: str) -> float | None:
        cam = self.locator.app.camera()
        if cam is None:
            return None
        tp = self.hl.ptr(cam)
        if tp is None:
            return None
        off = self.hl.field_offset(tp, field)
        if off is None:
            return None
        try:
            return self.hl.f64(cam + off)
        except ProcError:
            return None

    def shutdown(self) -> None:
        self.damage.shutdown()

    @property
    def player_addr(self) -> int | None:
        return self.locator.live_address()

    def player_xyz(self) -> XYZ | None:
        try:
            return self.locator.read_xyz()
        except ProcError:
            return None

    def player_heading(self) -> float | None:
        fn = getattr(self.locator, "read_heading", None)
        if fn is None:
            return None
        try:
            return fn()
        except ProcError:
            return None

    @property
    def chests(self):
        return self.chests_resolver.chests

    # --- scene -----------------------------------------------------------
    def units(self) -> list[Entity]:
        now = time.monotonic()
        if now - self._units_at < self.UNITS_TTL and self._units_cache:
            return self._units_cache
        try:
            self._units_cache = self.scene.units(self.player_addr)
            self.units_ok = True
        except ProcError:
            self._units_cache = []
            self.units_ok = False
        self._units_at = now
        # boss of the current instance, cleared when none present so a prior
        # dungeon's boss can't linger
        self.dungeon_boss = next(
            (e.unit_id for e in self._units_cache
             if e.unit_id and udata.is_boss(e.unit_id)), None)
        return self._units_cache

    def enemies(self) -> list[Entity]:
        return [e for e in self.units() if e.is_enemy]

    @staticmethod
    def _ranked(pool: list[Entity], xyz: XYZ, n: int, max_dist: float, use_2d: bool = False):
        if use_2d:
            ranked = sorted(((e, e.dist2d(xyz[0], xyz[1])) for e in pool), key=lambda t: t[1])
        else:
            ranked = sorted(((e, e.dist(*xyz)) for e in pool), key=lambda t: t[1])
            
        if max_dist > 0:
            ranked = [(e, d) for e, d in ranked if d <= max_dist]
        return ranked[:n]

    def nearest_enemies(self, xyz: XYZ, n: int, max_dist: float = 0.0,
                        enemies_only: bool = False,
                        hide_types: set[str] | None = None,
                        hide_units: set[str] | None = None,
                        player_zone: str | None = None,
                        use_2d: bool = False):
        # wild companions (critters) are ent.Foe but not enemies - they get
        # their own list (nearest_companions)
        pool = []
        p_addr = self.player_addr
        p_player_addr = self.hl.ptr(p_addr + OFF_HERO_OWNERPLAYER) if p_addr else None

        for e in self.units():
            # If it's owned by the player, it's not an enemy
            if p_addr and (e.owner_addr == p_addr or (p_player_addr and e.owner_addr == p_player_addr)):
                continue
            if e.is_player_owned:
                continue
                
            if not (e.is_enemy or (enemies_only and e.is_hero)):
                continue
            if udata.is_companion(e.unit_id):
                continue
            if e.addr == p_addr:
                continue
            
            # Filter out internal engine objects (Spawners, Patrol paths, triggers)
            uid_l = (e.unit_id or "").lower()
            found_internal = False
            for s in ("patrol", "spawn", "trigger", "marker", "point", "target", "area"):
                if s in uid_l:
                    found_internal = True
                    break
            if found_internal:
                continue
                
            pool.append(e)
        if hide_types:
            pool = [e for e in pool if udata.unit_type(e.unit_id) not in hide_types]
        if hide_units:
            pool = [e for e in pool if e.unit_id not in hide_units]
        if player_zone:
            from ..geo import zones as geo_zones
            pool = [e for e in pool if geo_zones.resolve_zone(e.x, e.y, e.z) == player_zone]
        return self._ranked(pool, xyz, n, max_dist, use_2d=use_2d)

    def nearest_companions(self, xyz: XYZ, n: int, max_dist: float = 0.0,
                           hide_units: set[str] | None = None,
                           player_zone: str | None = None,
                           use_2d: bool = False):
        """Wild catchable companions (critters) near the player. Player-owned
        ones (equipped pets, own or other players') are excluded. Deliberately
        ignores the max-distance cap by default (if max_dist=0): critters are
        sparse and collectors want them visible from anywhere in the loaded scene.
        Includes only active spawned entities."""
        pool = []
        p_addr = self.player_addr
        p_player_addr = self.hl.ptr(p_addr + OFF_HERO_OWNERPLAYER) if p_addr else None

        for e in self.units():
            if e.is_foe and udata.is_companion(e.unit_id):
                # Exclude pets owned by the player or other players
                if p_addr and (e.owner_addr == p_addr or (p_player_addr and e.owner_addr == p_player_addr)):
                    continue
                if e.is_player_owned:
                    continue
                if hide_units and e.unit_id in hide_units:
                    continue
                pool.append(e)

        if player_zone:
            from ..geo import zones as geo_zones
            pool = [e for e in pool if geo_zones.resolve_zone(e.x, e.y, e.z) == player_zone]
        return self._ranked(pool, xyz, n, max_dist, use_2d=use_2d)

    def player_name(self, hero_addr: int) -> str | None:
        try:
            hero_type = self.hl.ptr(hero_addr)
            owner_off = self.hl.field_offset(hero_type, "ownerPlayer") if hero_type else None
            if owner_off is None:
                owner_off = 0x10
            player_ptr = self.hl.ptr(hero_addr + owner_off)
            if not player_ptr:
                return None
            player_type = self.hl.ptr(player_ptr)
            name_off = self.hl.field_offset(player_type, "name") if player_type else None
            if name_off is None:
                name_off = 0xa8
            name_strobj = self.hl.ptr(player_ptr + name_off)
            if name_strobj:
                return self.hl.hl_string(name_strobj)
        except Exception:
            pass
        return None

    def are_in_same_group(self, other_hero_addr: int) -> bool:
        try:
            local_hero = self.player_addr
            if not local_hero or other_hero_addr == local_hero:
                return False
            local_hero_type = self.hl.ptr(local_hero)
            owner_off = self.hl.field_offset(local_hero_type, "ownerPlayer") if local_hero_type else None
            if owner_off is None:
                owner_off = 0x10
            local_player = self.hl.ptr(local_hero + owner_off)
            if not local_player:
                return False
            local_player_type = self.hl.ptr(local_player)
            group_off = self.hl.field_offset(local_player_type, "group") if local_player_type else None
            if group_off is None:
                group_off = 0xe8
            local_group = self.hl.ptr(local_player + group_off)
            if not local_group:
                return False
            other_hero_type = self.hl.ptr(other_hero_addr)
            other_owner_off = self.hl.field_offset(other_hero_type, "ownerPlayer") if other_hero_type else None
            if other_owner_off is None:
                other_owner_off = 0x10
            other_player = self.hl.ptr(other_hero_addr + other_owner_off)
            if not other_player:
                return False
            other_player_type = self.hl.ptr(other_player)
            other_group_off = self.hl.field_offset(other_player_type, "group") if other_player_type else None
            if other_group_off is None:
                other_group_off = 0xe8
            other_group = self.hl.ptr(other_player + other_group_off)
            return other_group == local_group and other_group != 0
        except Exception:
            pass
        return False

    def hero_display_name(self, hero: Entity) -> str:
        try:
            from ..config import Settings
            s = Settings.load()
            show_all_names = getattr(s, "PlayerNames", False)
        except Exception:
            show_all_names = False
        in_group = self.are_in_same_group(hero.addr)
        if in_group or show_all_names:
            p_name = self.player_name(hero.addr)
            if p_name:
                return p_name
        cls_name = hero.cls or ""
        if cls_name.startswith("ent.hero."):
            return cls_name.replace("ent.hero.", "")
        return hero.unit_id or "Hero"

    def nearest_group_members(self, xyz: XYZ, n: int, max_dist: float = 0.0,
                              player_zone: str | None = None,
                              use_2d: bool = False):
        pool = [e for e in self.units()
                if e.is_hero and e.addr != self.player_addr]
        if player_zone:
            from ..geo import zones as geo_zones
            pool = [e for e in pool if geo_zones.resolve_zone(e.x, e.y, e.z) == player_zone]
        return self._ranked(pool, xyz, n, max_dist, use_2d=use_2d)

    def live_chests(self, player_zone: str | None = None, max_dist: float = 0.0, use_2d: bool = False) -> list[Element]:
        try:
            import math
            from dataclasses import replace
            from ..geo import zones as geo_zones
            pxyz = self.player_xyz()
            out = []
            for e in self.scene.elements(self.player_addr):
                if e.is_chest and e.elem_id and not (
                    "activity" in e.elem_id.lower() or
                    "checkpoint" in e.elem_id.lower() or
                    e.elem_id.startswith("BossChest")
                ):
                    if max_dist > 0 and pxyz:
                        dist = e.dist2d(pxyz[0], pxyz[1]) if use_2d else e.dist(*pxyz)
                        if dist > max_dist:
                            continue
                    
                    if player_zone:
                        ezone = geo_zones.resolve_zone(e.x, e.y, e.z)
                        if ezone != player_zone:
                            continue
                    
                    elem_id = e.elem_id
                    if "fightstone" in elem_id.lower():
                        fs_chests = [c for c in self.chests if "fightstone" in c.chest_id.lower()]
                        if fs_chests:
                            closest_static = min(fs_chests, key=lambda c: math.hypot(c.x - e.x, c.y - e.y))
                            if math.hypot(closest_static.x - e.x, closest_static.y - e.y) < 5.0:
                                elem_id = closest_static.chest_id
                                
                    if elem_id != e.elem_id:
                        e = replace(e, elem_id=elem_id)
                    out.append(e)
            return out
        except ProcError:
            return []

    def gatherables(self, player_zone: str | None = None, max_dist: float = 0.0, use_2d: bool = False) -> list[Element]:
        try:
            from ..geo import zones as geo_zones
            pxyz = self.player_xyz()
            out = []
            for e in self.scene.elements(self.player_addr):
                if e.is_gatherable:
                    if max_dist > 0 and pxyz:
                        dist = e.dist2d(pxyz[0], pxyz[1]) if use_2d else e.dist(*pxyz)
                        if dist > max_dist:
                            continue
                    if player_zone:
                        ezone = geo_zones.resolve_zone(e.x, e.y, e.z)
                        if ezone != player_zone:
                            continue
                    out.append(e)
            return out
        except ProcError:
            return []

    def obelisks(self, player_zone: str | None = None, max_dist: float = 0.0, use_2d: bool = False) -> list[Element]:
        try:
            from ..geo import zones as geo_zones
            pxyz = self.player_xyz()
            out = []
            for e in self.scene.elements(self.player_addr):
                if e.is_obelisk:
                    if max_dist > 0 and pxyz:
                        dist = e.dist2d(pxyz[0], pxyz[1]) if use_2d else e.dist(*pxyz)
                        if dist > max_dist:
                            continue
                    if player_zone:
                        ezone = geo_zones.resolve_zone(e.x, e.y, e.z)
                        if ezone != player_zone:
                            continue
                    out.append(e)
            return out
        except ProcError:
            return []

    def live_orbs(self, player_zone: str | None = None, max_dist: float = 0.0, use_2d: bool = False) -> list[Element]:
        """Dungeon secret orbs (InstanceOrb) in the loaded scene."""
        try:
            from ..geo import zones as geo_zones
            pxyz = self.player_xyz()
            out = []
            for e in self.scene.elements(self.player_addr):
                if e.is_orb and e.elem_id and (
                    "redorb" in e.elem_id.lower() or
                    "secretorb" in e.elem_id.lower()
                ):
                    if max_dist > 0 and pxyz:
                        dist = e.dist2d(pxyz[0], pxyz[1]) if use_2d else e.dist(*pxyz)
                        if dist > max_dist:
                            continue
                    if player_zone:
                        ezone = geo_zones.resolve_zone(e.x, e.y, e.z)
                        if ezone != player_zone:
                            continue
                    out.append(e)
            return out
        except ProcError:
            return []

    def live_chest_orbs(self) -> list[Element]:
        """Chest orbs (ent.Element) and TimerCollectRun orbs in the loaded scene."""
        try:
            out = []
            for e in self.scene.elements(self.player_addr):
                if not e.elem_id:
                    continue
                eid_l = e.elem_id.lower()
                if ("chestorb" in eid_l and "_orb_" in eid_l) or "timercollectrun" in eid_l:
                    out.append(e)
            return out
        except ProcError:
            return []

    def teleporters(self) -> list[Element]:
        """Dungeon entrances / teleporters in the loaded scene."""
        try:
            out = []
            for e in self.scene.elements(self.player_addr):
                if e.is_teleporter:
                    out.append(e)
                elif e.is_orb and e.elem_id and not (
                    "redorb" in e.elem_id.lower() or
                    "secretorb" in e.elem_id.lower() or
                    "chestorb" in e.elem_id.lower() or
                    "timercollectrun" in e.elem_id.lower()
                ):
                    out.append(e)
            return out
        except ProcError:
            return []

    def elements(self) -> list[Element]:
        """All loaded interactible elements (any class)."""
        try:
            return self.scene.elements(self.player_addr)
        except ProcError:
            return []

    _FX_OFF_CACHE: dict[int, int | None] = {}

    def world_orb_fx(self) -> list[tuple[str, bool]]:
        """(orb_id, glow-fx present) for loaded world secret orbs. The fx
        pointer is the reliable collected signal (collected = no fx)."""
        from ..constants import OFF_ELEM_FX
        out = []
        try:
            for e in self.scene.elements(self.player_addr):
                if not (e.elem_id and e.elem_id.startswith("RedOrb_World")):
                    continue
                out.append((e.elem_id, bool(self.hl.u64(e.addr + OFF_ELEM_FX))))
        except ProcError:
            return []
        return out

    def boss_state(self) -> tuple[str | None, bool, float | None]:
        scene = self.units()        # populates dungeon_boss; must run first
        bid = self.dungeon_boss
        if not bid:
            return (None, False, None)
        for e in scene:
            if e.unit_id == bid:
                try:
                    return (bid, True, attributes.health(self.hl, e.addr))
                except ProcError:
                    return (bid, True, None)
        return (bid, False, None)

    def encounter_state(self):
        """The boss-only split's per-tick view: `(members, kill_id, states, engage_any)`
        where states = `[(unit_id, present, hp)]` for each encounter unit in scene.
        Single-boss dungeons (the default) collapse to just the dungeon boss, so this
        is a superset of boss_state(); a trashless `engage_any` room feeds every enemy
        so the split can arm on the first hit to anything. `([], None, [], False)`
        outside an instance.
        """
        scene = self.units()        # populates dungeon_boss; must run first
        bid = self.dungeon_boss
        if not bid:
            return ([], None, [], False)
        members, kill_id, engage_any = encdata.resolve(bid)
        states: list[tuple[str, bool, float | None]] = []
        seen: set[str] = set()
        for e in scene:
            if engage_any:
                if not e.is_enemy:
                    continue
            elif e.unit_id not in members:
                continue
            try:
                hp = attributes.health(self.hl, e.addr)
            except ProcError:
                hp = None
            states.append((e.unit_id, True, hp))
            seen.add(e.unit_id)
        # Listed members not in scene -> reported absent so a kill-boss despawn still
        # registers (the existing kill/left detection needs the boss's liveness).
        for uid in members:
            if uid not in seen:
                states.append((uid, False, None))
        return (members, kill_id, states, engage_any)

    HARD_LEVEL = 25     # Hard mode scales every dungeon to this level (cap)

    def boss_level(self) -> int | None:
        bid = self.dungeon_boss
        if not bid:
            return None
        for e in self.units():
            if e.unit_id == bid:
                try:
                    return attributes.level(self.hl, e.addr)
                except ProcError:
                    return None
        return None

    def dungeon_difficulty(self) -> int | None:
        """0=Normal, 1=Hard from GameLayer.config, or None outside an instance.
        Primary difficulty source; independent of enemy levels."""
        try:
            return self.scene.difficulty(self.player_addr)
        except ProcError:
            return None

    def is_in_dungeon(self) -> bool:
        """True when the player is inside a dungeon instance.

        Uses GameLayer.mainActivity class name (st.activity.Dungeon) — the game's
        own activity system — so it works for every dungeon type without relying
        on boss presence, InstanceOrb elements, or mapId strings.
        """
        try:
            return self.scene.in_dungeon(self.player_addr)
        except ProcError:
            return False


    def detected_mode(self) -> str | None:
        diff = self.dungeon_difficulty()
        if diff is not None:
            return "hard" if diff == 1 else "normal"
        # fallback: enemy-level heuristic (needs a recognized, level-tagged boss)
        bid = self.dungeon_boss
        if not bid:
            return None
        live = self.boss_level()
        if live is None:
            return None
        info = udata.unit_info(bid)
        normal = info.get("lvl") if info else None
        if isinstance(normal, int):
            return "hard" if live > normal else "normal"
        return "hard" if live >= self.HARD_LEVEL else "normal"

    # --- loot resolution (delegated to ChestResolver) --------------------
    def chest_table(self, chest_id: str, default_table: str | None = None) -> str | None:
        return self.chests_resolver.chest_table(chest_id, self.dungeon_boss,
                                                default_table)

    def nearest_chests_merged(self, xyz: XYZ, n: int, max_dist: float = 0.0,
                              player_zone: str | None = None,
                              use_2d: bool = False) -> list[ChestRow]:
        return self.chests_resolver.nearest_chests_merged(
            xyz, n, self.dungeon_boss, self.live_chests(player_zone, max_dist, use_2d=use_2d),
            max_dist, player_zone, use_2d=use_2d)

    def player_profile(self) -> str | None:
        """The character profile string, cached so load boundaries don't cause a None fallback."""
        try:
            prof = self.locator.player_profile()
            if prof:
                self._last_profile = prof
                return prof
        except Exception:
            pass
        return self._last_profile

    def player_class(self) -> str | None:
        """The auto-detected player class (e.g. Rogue), parsed from equipped skills."""
        try:
            return self.locator.player_class()
        except Exception:
            return None

    def enemy_drop_source(self, entity, dist: float, default_level: int) -> Nearest | None:
        uid = getattr(entity, "unit_id", None)
        if not uid:
            return None
        tbl = udata.loot_table_for_unit(uid)
        if not tbl:
            return None
        info = udata.unit_info(uid)
        lvl = (info.get("lvl") if info else None) or default_level
        return Nearest("enemy", uid, dist, tbl, lvl,
                       note=(info.get("type") if info else "") or "")

    def chest_drop_source(self, chestrow, default_level: int) -> Nearest | None:
        if not getattr(chestrow, "loot_table", None):
            return None
        return Nearest("chest", chestrow.chest_id, chestrow.dist, chestrow.loot_table,
                       chestrow.level or default_level, note=chestrow.state or "")

    def drop_table(self, near: Nearest | None):
        if not near or not near.loot_table:
            return []
        try:
            return loot.predict_sorted(near.loot_table, near.level)
        except Exception as e:
            log.warning("loot predict failed for table %r: %s", near.loot_table, e)
            return []

    def drop_table_effective(self, near: Nearest | None):
        out = []
        for item, prob, rar, typ in self.drop_table(near):
            if rarity_mod.should_promote(item, typ, rar):
                for tier, ch in rarity_mod.promote_distribution(rar, near.level).items():
                    if ch > 0:
                        out.append((item, prob * ch, tier, "rolled"))
            else:
                out.append((item, prob, rar, typ))
        return out

    # --- combat ----------------------------------------------------------
    def sample_combat(self, radius: float = 30.0) -> DpsMeter:
        now = time.monotonic()
        if now - self._combat_at < 0.1:
            return self.dps
        self._combat_at = now

        xyz = self.player_xyz()
        snap = []
        for e in self.units():
            if not e.is_enemy:
                continue
            boss = bool(e.unit_id and udata.is_boss(e.unit_id))
            if radius and xyz and not boss and e.dist(*xyz) > radius:
                continue    # bosses are tracked regardless of radius
            snap.append((e.addr, e.unit_id or "?", attributes.health(self.hl, e.addr)))
        self.dps.update(snap)
        self._sample_player_hp(now)

        self.damage.sample(self.player_addr, self.dps.in_combat)
        return self.dps

    # --- survivability ---------------------------------------------------
    def _sample_player_hp(self, now: float) -> None:
        pa = self.player_addr
        if not pa:
            return
        try:
            hp = attributes.health(self.hl, pa)
        except ProcError:
            return
        if hp is None:
            return
        self.player_max_hp = max(self.player_max_hp, hp)
        if self.player_hp_log:
            prev = self.player_hp_log[-1][1]
            drop = prev - hp
            # guard against respawn/heal so a rebaseline isn't counted as damage
            if 0 < drop < (self.player_max_hp or drop) * 1.5:
                self.dps.add_taken(drop, now)
        self.player_hp_log.append((now, hp))
        if self._was_alive and hp <= 0:
            self.deaths += 1
        self._was_alive = hp > 0

    def player_hp_series(self) -> list[float]:
        return [hp for _t, hp in self.player_hp_log]

    def player_hp_frac(self) -> float | None:
        if not self.player_hp_log or self.player_max_hp <= 0:
            return None
        return max(0.0, min(1.0, self.player_hp_log[-1][1] / self.player_max_hp))

    def reset_combat(self) -> None:
        self.dps.reset()
        self.damage.reset()
        self.player_hp_log.clear()
        self.player_max_hp = 0.0
        self.deaths = 0
        self._was_alive = False

    def is_game_menu_open(self, include_escape: bool = True) -> bool:
        if self.player_addr is None:
            return True
        try:
            ui_addr = self.locator.app.ui()
            if not ui_addr:
                return False
            arr_ptr = self.hl.ptr(ui_addr + 0x90)
            if not arr_ptr:
                return False
            arr_len = self.hl.i32(arr_ptr + 8)
            native_arr = self.hl.ptr(arr_ptr + 0x10)
            if not native_arr or arr_len <= 0:
                return False
            for i in range(arr_len):
                item_ptr = self.hl.ptr(native_arr + 0x18 + i * 8)
                if item_ptr:
                    if not include_escape and self.hl.class_of(item_ptr) == "ui.win.EscapeMenu":
                        continue
                    if self.hl.is_a(item_ptr, "ui.win.BaseWindow"):
                        return True
        except Exception:
            pass
        return False


