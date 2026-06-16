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
    def _ranked(pool: list[Entity], xyz: XYZ, n: int, max_dist: float):
        ranked = sorted(((e, e.dist(*xyz)) for e in pool), key=lambda t: t[1])
        if max_dist > 0:
            ranked = [(e, d) for e, d in ranked if d <= max_dist]
        return ranked[:n]

    def nearest_enemies(self, xyz: XYZ, n: int, max_dist: float = 0.0,
                        enemies_only: bool = True,
                        hide_types: set[str] | None = None,
                        hide_units: set[str] | None = None):
        # wild companions (critters) are ent.Foe but not enemies - they get
        # their own list (nearest_companions)
        pool = [e for e in self.units()
                if (e.is_enemy if enemies_only else (e.is_foe or e.is_hero))
                and not udata.is_companion(e.unit_id)]
        if hide_types:
            pool = [e for e in pool if udata.unit_type(e.unit_id) not in hide_types]
        if hide_units:
            pool = [e for e in pool if e.unit_id not in hide_units]
        return self._ranked(pool, xyz, n, max_dist)

    def nearest_companions(self, xyz: XYZ, n: int):
        """Wild catchable companions (critters) near the player. Player-owned
        ones (equipped pets, own or other players') are excluded. Deliberately
        ignores the max-distance cap: critters are sparse and collectors want
        them visible from anywhere in the loaded scene."""
        pool = [e for e in self.units()
                if e.is_foe and udata.is_companion(e.unit_id)
                and not e.is_player_owned]
        return self._ranked(pool, xyz, n, 0.0)

    def live_chests(self) -> list[Element]:
        try:
            return [e for e in self.scene.elements(self.player_addr)
                    if e.is_chest and e.elem_id and not (
                        "activity" in e.elem_id.lower() or
                        e.elem_id.startswith("BossChest")
                    )]
        except ProcError:
            return []

    def gatherables(self) -> list[Element]:
        try:
            return [e for e in self.scene.elements(self.player_addr) if e.is_gatherable]
        except ProcError:
            return []

    def obelisks(self) -> list[Element]:
        try:
            return [e for e in self.scene.elements(self.player_addr) if e.is_obelisk]
        except ProcError:
            return []

    def live_orbs(self) -> list[Element]:
        """Dungeon secret orbs (InstanceOrb) in the loaded scene."""
        try:
            return [e for e in self.scene.elements(self.player_addr)
                    if e.is_orb and e.elem_id and (
                        "redorb" in e.elem_id.lower() or
                        "secretorb" in e.elem_id.lower()
                    )]
        except ProcError:
            return []

    def teleporters(self) -> list[Element]:
        """Dungeon entrances / teleporters in the loaded scene."""
        try:
            return [e for e in self.scene.elements(self.player_addr) if e.is_teleporter]
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
        pointer is the reliable collected signal (collected = no fx); offset
        resolved by name per element type and cached."""
        out = []
        try:
            for e in self.scene.elements(self.player_addr):
                if not (e.elem_id and e.elem_id.startswith("RedOrb_World")):
                    continue
                tp = self.hl.ptr(e.addr)
                if tp is None:
                    continue
                if tp not in self._FX_OFF_CACHE:
                    self._FX_OFF_CACHE[tp] = self.hl.field_offset(tp, "currentFx")
                off = self._FX_OFF_CACHE[tp]
                if off is None:
                    continue
                out.append((e.elem_id, bool(self.hl.u64(e.addr + off))))
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

    HARD_LEVEL = 20     # Hard mode scales every dungeon to this level

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

    def nearest_chests_merged(self, xyz: XYZ, n: int, max_dist: float = 0.0
                              ) -> list[ChestRow]:
        return self.chests_resolver.nearest_chests_merged(
            xyz, n, self.dungeon_boss, self.live_chests(), max_dist)

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

    def closest_loot(self, xyz: XYZ, default_level: int) -> Nearest | None:
        cands: list[tuple[float, Nearest]] = []
        for e, d in self.nearest_enemies(xyz, 6, 0.0, enemies_only=True):
            if not e.unit_id:
                continue
            tbl = udata.loot_table_for_unit(e.unit_id)
            if not tbl:
                continue
            info = udata.unit_info(e.unit_id)
            lvl = (info.get("lvl") if info else None) or default_level
            cands.append((d, Nearest("enemy", e.unit_id, d, tbl, lvl,
                                     note=(info.get("type") if info else "") or "")))
            break
        for cr in self.nearest_chests_merged(xyz, n=15):
            tbl = cr.loot_table
            if not tbl:
                continue
            cands.append((cr.dist * self.CHEST_DIST_BIAS,
                          Nearest("chest", cr.chest_id, cr.dist, tbl,
                                  cr.level or default_level, note=cr.state or "")))
            break
        if not cands:
            return None
        return min(cands, key=lambda t: t[0])[1]

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
