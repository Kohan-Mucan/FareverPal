"""LiveModel: one immutable per-tick snapshot of the game, shared by every view."""
from __future__ import annotations

import logging
import time

log = logging.getLogger(__name__)

from .proc import Proc, ProcError
from .hl import Hl, is_ptr as _is_ptr
from .scene import Scene, Entity, Element
from .player import PlayerLocator
from .announcement_reader import AnnouncementReader
from .camera import ViewCamera
from .chest_resolver import ChestResolver, ChestRow, is_event_orb_id
from .rift_tracker import RiftTracker, RiftStatus
from .dps_source import DamageSourceManager
from .dps_tracker import DpsTracker
from .group_reader import GroupReader
from .game_state import GameState, read_game_state
from . import attributes, game_state
from ..constants import (OFF_HERO_OWNERPLAYER, OFF_FOE_OWNER, OFF_FOE_TARGET_OBJ,
                         OFF_FOE_TARGET_HERO, OFF_FOE_TARGET_ALT,
                         OFF_FOE_TARGET_DIRECT, OFF_FOE_RECENT_HATE,
                         OFF_FOE_HATE_LIST, AGGRO_SWEEP_BYTES,
                         FOE_TARGET_HOLD_S, OFF_OWNER, OFF_PLAYER_NAME,
                         OFF_UI_WINDOWS)
from ..data import units as udata, encounters as encdata

XYZ = tuple[float, float, float]

# Sanity bound on the open-window list: the game never has more than a handful
# of windows up, so a bigger count means the pointer we followed isn't a window
# array (a stale/freed GameUI reads as garbage) and the read must not be trusted.
MAX_UI_WINDOWS = 256


class LiveModel:
    UNITS_TTL = 0.10
    CHEST_DIST_BIAS = 0.5      # chests win over a slightly-closer enemy

    def __init__(self, proc: Proc):
        self.proc = proc
        self.hl = Hl(proc)
        self.scene = Scene(proc, self.hl)
        self.locator = PlayerLocator(proc, self.hl)
        self.announcements = AnnouncementReader(proc, self.hl, self.locator)
        # Party roster reader (replicated st.Group net object): authoritative
        # member names/leader at any distance - the read side of the game's
        # invite-to-group flow. Probe-first + scan-free via the local
        # player's own st.Player.group slot (see core/group_reader.py).
        self._group_reader = GroupReader(proc, self.hl)
        self.view = ViewCamera(proc, self.hl, self.locator.app)
        self.chests_resolver = ChestResolver()
        self.rift_tracker = RiftTracker(self)
        # Real combat-event source (DamageDisplay numbers / HUD group meter).
        # Lazy: its background thread starts on the first DPS tracker update,
        # and shutdown() below stops it (previously referenced but never set).
        self.damage = DamageSourceManager(proc)
        self.dps = DpsTracker(self)
        self._units_cache: list[Entity] = []
        self._units_at = 0.0
        self._units_key: int | None = None
        self.dungeon_boss: str | None = None
        self.units_ok: bool = True   # False while the units read fails (zone swap)
        self._last_profile: str | None = None
        # Per-tick snapshot (core/game_state.py): published once by the overlay
        # manager's tick, then read by every overlay and rule instead of each
        # re-deriving the same answers from memory.
        self._state: GameState | None = None

    # --- the per-tick snapshot -------------------------------------------
    @property
    def published_state(self) -> GameState | None:
        """This tick's shared snapshot, or None when no tick has published one.
        Never builds one - readers that need an answer either way use the
        accessors in core/game_state.py, which fall back to a live read."""
        return self._state

    @property
    def state(self) -> GameState:
        """The shared snapshot; built on the spot if no tick published one.

        Published snapshots are reused for the rest of the tick, so every
        overlay and rule in a tick sees the SAME object (and the same answers).
        The fallback build is for reads with no tick behind them (detached, one
        -off diagnostics) - it isn't cached, so it can't go stale.
        """
        st = self._state
        return st if st is not None else read_game_state(self)

    def publish_state(self, state: GameState) -> None:
        """Adopt a tick's snapshot (called by OverlayManager's tick)."""
        self._state = state

    def clear_state(self) -> None:
        """Forget the snapshot - a detach must not leave one for the next
        session's overlays to read."""
        self._state = None

    # --- lifecycle -------------------------------------------------------
    def locate_player(self) -> int | None:
        return self.locator.locate()

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
        try:
            self.damage.shutdown()
        except Exception:
            pass

    @property
    def player_addr(self) -> int | None:
        return self.locator.live_address()

    def player_xyz(self) -> XYZ | None:
        try:
            return self.locator.read_xyz()
        except ProcError:
            return None

    def _locator_probe(self, name: str):
        """`self.locator.<name>()`, or None when it has no such method or the
        read fails.

        The locator's optional readers (`read_heading`, `combat_state`) are
        absent on older captures and on the test fakes, so a missing method is
        a plain "not available" answer rather than an error - the fail-closed
        guard both auxiliary signals need."""
        fn = getattr(self.locator, name, None)
        if fn is None:
            return None
        try:
            return fn()
        except ProcError:
            return None

    def player_heading(self) -> float | None:
        return self._locator_probe("read_heading")

    def combat_state(self) -> dict | None:
        """Game-authoritative Hero combat fields (isInCombat, combatId,
        combatStartTime, combatEndTime) or None when the player isn't
        located. Used by the DPS tracker as an auxiliary encounter signal
        (see PlayerLocator.combat_state)."""
        return self._locator_probe("combat_state")

    def party_combat_state(self) -> bool | None:
        """Return whether any current group member is in combat.

        ``True`` means at least one member is active, ``False`` means every
        connected member explicitly reported out of combat, and ``None`` means
        the group or one of its member combat fields could not be read. The
        attach-time Run Timer gate must fail closed on ``None``.
        """
        roster = self.cached_group_roster()
        if roster is None:
            return None
        members = [m for m in (getattr(roster, "members", ()) or ())
                   if getattr(m, "connected", True)]
        if not members:
            state = self.combat_state()
            value = state.get("in_combat") if isinstance(state, dict) else None
            return bool(value) if isinstance(value, bool) else None

        active = False
        for member in members:
            hero = int(getattr(member, "hero", 0) or 0)
            if not hero and getattr(member, "is_me", False):
                hero = int(self.player_addr or 0)
            if not hero:
                return None
            state = self.locator.combat_state(hero)
            value = state.get("in_combat") if isinstance(state, dict) else None
            if not isinstance(value, bool):
                return None
            active = active or value
        return active

    @property
    def chests(self):
        return self.chests_resolver.chests

    # --- scene -----------------------------------------------------------
    def units(self) -> list[Entity]:
        now = time.monotonic()
        # Keyed on the player too: a zone swap changes the scene the address
        # resolves in, and a time-only key would hand the previous zone's units
        # to the first reader after the swap.
        if (now - self._units_at < self.UNITS_TTL and self._units_cache
                and self._units_key == self.player_addr):
            return self._units_cache
        try:
            self._units_cache = self.scene.units(self.player_addr)
            self.units_ok = True
        except ProcError:
            self._units_cache = []
            self.units_ok = False
        self._units_at = now
        self._units_key = self.player_addr
        # The boss of the current instance, cleared when none is present so a
        # prior dungeon's boss cannot linger into the next one.
        #
        # Three signals, in this order, and the order is the whole point.
        # `dungeon_boss` is not only "the boss of this instance" for the codex
        # and the chest resolver - it is the unit whose DEATH ENDS THE RUN
        # TIMER (`encounter_state` -> `encdata.resolve` -> `Encounter.kill_id`).
        # So picking the wrong one ends a healthy run.
        #
        # 1. DECLARED - the game's own activity table names this instance's
        #    boss (`dungeons.declared_boss_id`). It is a statement, not an
        #    inference, and it is the one signal that can say "this unit is THE
        #    boss" when the unit walk only says "this unit looks like one".
        # 2. STICKY - keep the boss we already had while it is still in the
        #    scene, so a second boss-tagged unit cannot steal the fight
        #    mid-encounter.
        # 3. NEAREST - the fallback, and what this used to do unconditionally.
        #
        # Why 1 and 2 exist: `units.is_boss` counts a rift's TRUE CLONE as a
        # boss, which is right for DAMAGE (killing the clone is damage worth
        # recording) and wrong for the kill target - it is a phase, not the
        # fight. Live 2026-10-03, a rift: the Run Timer finished the moment the
        # clone died with the boss still standing. Distance alone picked
        # whichever clone was closer to the player.
        #
        # This is deliberately the SAME precedence the DPS meter's scan already
        # uses (`dps_tracker_tick._scan_foes`: declared outranks distance, plus
        # its own stickiness in `_track_boss_target`). Two rules for one
        # question is how the meter and the timer came to disagree about which
        # fight they were in.
        bosses = [e for e in self._units_cache
                  if e.unit_id and udata.is_boss(e.unit_id)]
        if not bosses:
            self.dungeon_boss = None
            return self._units_cache

        declared = None
        try:
            from ..data import dungeons as ddata
            declared = ddata.declared_boss_id(game_state.zone(self))
        except Exception:
            declared = None

        pick = next((e for e in bosses if e.unit_id == declared), None)
        if pick is None and self.dungeon_boss:
            pick = next((e for e in bosses if e.unit_id == self.dungeon_boss),
                        None)
        if pick is None:
            me = next((e for e in self._units_cache
                       if e.addr and e.addr == self.player_addr), None)
            pick = bosses[0]
            if me is not None:
                pick_d = pick.dist(me.x, me.y, me.z)
                for e in bosses[1:]:
                    d = e.dist(me.x, me.y, me.z)
                    if d < pick_d:
                        pick, pick_d = e, d
        self.dungeon_boss = pick.unit_id
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
            if e.addr == p_addr:
                continue
            
            # Use unified codex unit filtering to exclude internal engine objects, templates,
            # and non-combat types like Mounts or Critters (companions).
            if not udata.is_codex_unit(e.unit_id):
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
                if hide_units:
                    if e.unit_id in hide_units:
                        continue
                pool.append(e)

        if player_zone:
            from ..geo import zones as geo_zones
            pool = [e for e in pool if geo_zones.resolve_zone(e.x, e.y, e.z) == player_zone]
        return self._ranked(pool, xyz, n, max_dist, use_2d=use_2d)

    def player_name(self, hero_addr: int) -> str | None:
        if not hero_addr:
            return None
        try:
            # 1. If hero_addr is already an st.Player, resolve its name directly
            cls_name = self.hl.class_of(hero_addr)
            if cls_name == "st.Player":
                return self._player_name_at(hero_addr)

            # 2. Hero -> Player pointer resolution
            p = self._hero_player_ptr(hero_addr)
            if p:
                name = self._player_name_at(p)
                if name:
                    return name

            hero_type = self.hl.ptr(hero_addr)
            owner_off = self.hl.field_offset(hero_type, "ownerPlayer") if hero_type else None
            if owner_off is None:
                owner_off = 0x10
            player_ptr = self.hl.ptr(hero_addr + owner_off)
            if player_ptr:
                return self._player_name_at(player_ptr)
        except Exception:
            pass
        return None

    def _hero_player_ptr(self, hero_addr: int) -> int:
        """ent.Hero -> st.Player, verified by runtime class. The owner slot
        drifts between builds (0x498 historically; the 2026-08-24 diag shows
        refl(ownerPlayer)=0x10 and refl(player)=0x4C0 both holding a live
        st.Player while 0x498 holds garbage), so try every candidate and
        trust only one whose class checks out."""
        try:
            tp = self.hl.ptr(hero_addr)
        except Exception:
            return 0
        offs: list[int] = []
        if tp:
            for fname in ("ownerPlayer", "player"):
                try:
                    off = self.hl.field_offset(tp, fname)
                    if off:
                        offs.append(off)
                except Exception:
                    pass
        offs += [OFF_HERO_OWNERPLAYER, OFF_OWNER, 0x10, 0x4C0]
        seen: set[int] = set()
        for off in offs:
            if off in seen:
                continue
            seen.add(off)
            try:
                p = self.hl.ptr(hero_addr + off)
                if p and self.hl.class_of(p) == "st.Player":
                    return p
            except Exception:
                continue
        return 0

    def resolve_pet_owner(self, unit_addr: int) -> int:
        """The hero entity (or st.Player) owning a NON-hero unit, verified
        live. Used when a damage floaty's caster (serverSource) is a
        pet/summon that never showed up in the units scan: read the unit's
        owner slot the same way the scene reader does and trust it only when
        the owner's runtime class IS a hero or st.Player. Enemies have no
        hero owner, so their events never resolve to a player (nothing is
        fabricated from an empty/garbage slot)."""
        if not unit_addr:
            return 0
        # OFF_FOE_OWNER (0x78) is the foe/pet -> hero slot (scene.py); the
        # generic GameObject owner and the hero ownerPlayer slot are checked
        # as fallbacks for builds that park a summon's owner elsewhere.
        for off in (OFF_FOE_OWNER, OFF_OWNER, OFF_HERO_OWNERPLAYER):
            try:
                cand = self.hl.ptr(unit_addr + off)
            except ProcError:
                continue
            if not cand:
                continue
            try:
                cls = self.hl.class_of(cand)
            except ProcError:
                continue
            if cls == "st.Player":
                return cand
            if cls == "ent.Hero" or (cls and cls.startswith("ent.hero.")):
                return cand
        return 0

    def _player_name_at(self, player_ptr: int) -> str | None:
        """Character name off a verified st.Player: reflection 'name' field
        first, then confirmed 0xc0, then legacy 0xa8, then OFF_PLAYER_NAME."""
        try:
            ptype = self.hl.ptr(player_ptr)
            refl = self.hl.field_offset(ptype, "name") if ptype else None
            for off in (refl, 0xc0, 0xa8, OFF_PLAYER_NAME):
                if not off:
                    continue
                strobj = self.hl.ptr(player_ptr + off)
                if not strobj:
                    continue
                s = self.hl.hl_string(strobj)
                if s:
                    return s
        except Exception:
            pass
        return None

    def foe_target_line(self, el) -> str | None:
        """Combined row text: '◎ Tank · ⚡ Hater' — target glyph follows the
        boss's live target (never-blank fallback only), lightning marks the
        top hate-list entry when it's someone else."""
        disp, hate = self._foe_target_state(el)
        parts = []
        if disp:
            parts.append(f"◎ {disp}")
        if hate and hate != disp:
            parts.append(f"⚡{hate}")
        line = " · ".join(parts)
        return line or None

    def _foe_target_state(self, el):
        """Full aggro read: (display_name, hate_name).

        FOLLOWS LIVE: the row shows the boss's real current target every tick
        (no debounce, no stale hold) so genuine retargets are visible at once.
        The only smoothing is a never-blank fallback — if the live read comes
        up empty on a tick (MAIN nulled between swings, transient read miss),
        it keeps the last confirmed name for at most FOE_TARGET_HOLD_S before
        clearing."""
        import struct as _struct
        try:
            units = self.units()
            heroes = {u.addr: u for u in units
                      if getattr(u, "is_hero", False)}
            if not heroes:
                return None, None

            seen: dict[int, int] = {}   # hero addr -> highest hate slot

            # memoized per tick: the same hero can resolve twice (MAIN target
            # and the top hate entry) without re-reading names from memory
            names: dict[int, str | None] = {}

            def resolve(hero) -> str | None:
                addr = hero.addr
                if addr in names:
                    return names[addr]
                # same proven resolution as the PLAYERS rows (player_name)
                name = self.player_name(addr)
                if not name:
                    p = self._hero_player_ptr(addr)
                    if p:
                        name = self._player_name_at(p)
                name = name or hero.unit_id or None
                names[addr] = name
                return name

            def sweep(obj) -> None:
                blk = self.proc.read(obj, AGGRO_SWEEP_BYTES)
                for o2 in range(8, len(blk) - 7, 8):
                    v = _struct.unpack_from("<Q", blk, o2)[0]
                    if v in heroes and o2 > seen.get(v, -1):
                        seen[v] = o2

            # 1. MAIN target slot (+ BIG-BOSS direct slot fallback)
            target_hero = None
            try:
                obj = self.hl.ptr(el.addr + OFF_FOE_TARGET_OBJ)
                if obj:
                    try:
                        v = self.hl.ptr(obj + OFF_FOE_TARGET_HERO)
                        if v in heroes:
                            target_hero = heroes[v]
                    except Exception:
                        target_hero = None
                    sweep(obj)
            except Exception:
                pass
            if target_hero is None:
                try:
                    v = self.hl.ptr(el.addr + OFF_FOE_TARGET_DIRECT)
                    if v in heroes:
                        target_hero = heroes[v]
                except Exception:
                    target_hero = None

            # 2. hate-list sweeps (also feed the hate_name column)
            if not seen:
                for base in (OFF_FOE_RECENT_HATE, OFF_FOE_HATE_LIST,
                             OFF_FOE_TARGET_ALT):
                    try:
                        obj = self.hl.ptr(el.addr + base)
                        if obj:
                            sweep(obj)
                    except Exception:
                        continue

            # 3. LIVE target this tick: the boss's real aggro.  Falls back to
            #    the top hate-list entry when MAIN is empty.
            now = time.time()
            top_hate = None
            if seen:
                top_hate = heroes[max(seen.items(), key=lambda kv: kv[1])[0]]
            cand = target_hero if target_hero is not None else top_hate
            live_name = resolve(cand) if cand else None

            # never-blank fallback: keep the last confirmed name for a short
            # while if this tick read nothing (transient null between swings)
            last = getattr(self, "_tgt_last", None)
            if last is None:
                last = self._tgt_last = {}
            if live_name:
                last[el.addr] = (now, live_name)
            held = last.get(el.addr)
            disp_name = live_name
            if not disp_name and held \
                    and now - held[0] < FOE_TARGET_HOLD_S:
                disp_name = held[1]

            # forget cache of foes that left the scene
            live_addrs = {u.addr for u in units}
            for k in [k for k in last if k not in live_addrs]:
                last.pop(k, None)

            # top hate entry that differs from the displayed target (⚡), only
            # when the display is the live read — never a stale hold
            hate_name = None
            if top_hate and live_name and live_name == disp_name:
                hn = resolve(top_hate)
                if hn and hn != disp_name:
                    hate_name = hn
            return disp_name, hate_name
        except Exception:
            return None, None

    def group_roster(self):
        """The local player's party roster, read off the game's own
        net-synced ``st.Group`` object (the object ``invite player to group``
        mutates): real member names + leader + solo flag, at any distance -
        unlike the local scene scan which only sees nearby units.

        Rate-limited ~1 Hz inside the reader; None unattached / at the menu /
        when no readable group decodes (never fabricated). Consumers duck-type
        ``GroupSnapshot`` (members with ``name``/``hero``/``player``/``uid``/
        ``is_me``/``is_leader``), so the DPS tracker can seed far-away
        members' names without importing the module."""
        try:
            pa = self.player_addr
            me = self._hero_player_ptr(pa) if pa else 0
            return self._group_reader.snapshot(local_player=me, local_hero=pa)
        except Exception:
            return None

    def group_member_zones(self, roster=None) -> dict[int, str]:
        """{hero_addr: readable zone name} for every roster member with a
        usable position.

        Wraps the roster's own hero positions (GroupMember.xyz) in the same
        zone resolution the map uses: world coords -> zone id -> readable
        name. Best-effort throughout - a member with no readable position, or
        one whose zone id the sheet does not know, is simply absent from the
        result rather than guessed at.
        """
        if roster is None:
            roster = self.cached_group_roster()
        members = list(getattr(roster, "members", None) or []) if roster else []
        if not members:
            return {}
        from ..data import names as gnames
        from ..geo import zones as geo_zones
        out: dict[int, str] = {}
        for m in members:
            hero = getattr(m, "hero", 0) or 0
            xyz = getattr(m, "xyz", None)
            if not hero or not xyz or not any(xyz):
                continue
            try:
                zid = geo_zones.resolve_zone(*xyz)
            except Exception:
                continue
            if not zid:
                continue
            # zone_display, NOT zone_name: the latter humanizes an id the
            # sheet doesn't know into "Z 9 Unknownlands", which would put a
            # raw technical id in front of the user.
            zname = gnames.zone_display(zid)
            if not zname:
                continue
            out[hero] = zname
        return out

    def cached_group_roster(self):
        """The last decoded roster, WITHOUT triggering a decode — for UI
        threads. See GroupReader.last: a live decode is a heap sweep measured in
        seconds, so a page that only wants the party list should read the cache
        and let a background thread call ``group_roster()``."""
        try:
            return self._group_reader.last()
        except Exception:
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
        # For group members, we ignore player_zone filtering if they are close enough (within max_dist)
        # as zone boundaries shouldn't hide teammates.
        # Group members ignore player_zone filtering when close enough (within
        # max_dist): zone boundaries shouldn't hide teammates, and _ranked
        # applies the distance limit anyway.
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
                        # Live fight-spot chests use the GENERIC id "FightStone"
                        # — the same for every fight chest.  Resolve it to the
                        # nearest static anchor (…_FightStone_N) so done-marking
                        # is per-chest and the label is meaningful.  Anchors are
                        # the fight spots themselves, so a live chest always
                        # spawns right on one; 60 m is a generous tolerance.
                        # ⛔ ORB-CHEST ACTIVITY LOGIC — DO NOT EDIT (see
                        # docs/CHEST_ORB_LOGIC.md).
                        fs_chests = [c for c in self.chests if "fightstone" in c.chest_id.lower()]
                        if fs_chests:
                            closest_static = min(fs_chests, key=lambda c: math.hypot(c.x - e.x, c.y - e.y))
                            if math.hypot(closest_static.x - e.x, closest_static.y - e.y) < 60.0:
                                elem_id = closest_static.chest_id

                    if elem_id != e.elem_id:
                        e = replace(e, elem_id=elem_id)
                    out.append(e)
            return out
        except ProcError:
            return []

    def _window(self, source, player_zone: str | None = None,
                max_dist: float = 0.0, use_2d: bool = False) -> list:
        """Apply the shared distance + zone window to a stream of elements.

        Every `*_scene` query below asks the same two questions of what it
        found - is it inside `max_dist` of the player, and is it in the
        player's zone - so the rule lives here once instead of in each query.
        `source` is any iterable of elements with x/y/z and dist/dist2d; which
        elements are in it stays the caller's business."""
        pxyz = self.player_xyz()
        zone_mod = None
        if player_zone:
            from ..geo import zones as zone_mod
        out = []
        for e in source:
            if max_dist > 0 and pxyz:
                dist = e.dist2d(pxyz[0], pxyz[1]) if use_2d else e.dist(*pxyz)
                if dist > max_dist:
                    continue
            if player_zone:
                if zone_mod.resolve_zone(e.x, e.y, e.z) != player_zone:
                    continue
            out.append(e)
        return out

    def gatherables(self, player_zone: str | None = None, max_dist: float = 0.0, use_2d: bool = False) -> list[Element]:
        try:
            return self._window(
                (e for e in self.scene.elements(self.player_addr) if e.is_gatherable),
                player_zone, max_dist, use_2d)
        except ProcError:
            return []

    def obelisks(self, player_zone: str | None = None, max_dist: float = 0.0, use_2d: bool = False) -> list[Element]:
        try:
            return self._window(
                (e for e in self.scene.elements(self.player_addr) if e.is_obelisk),
                player_zone, max_dist, use_2d)
        except ProcError:
            return []

    def loot_drops(self, max_dist: float = 0.0, use_2d: bool = False) -> list:
        """Dropped loot (ent.interactible.LootDrop) in the loaded scene,
        item ids resolved via st.Item.kind."""
        try:
            return self._window(self.scene.loot_drops(self.player_addr),
                                max_dist=max_dist, use_2d=use_2d)
        except ProcError:
            return []

    def food_stations(self, max_dist: float = 0.0, use_2d: bool = False) -> list:
        """Player-placed food/consumables (WorldConsumable) in the scene,
        display names resolved off their st.skill.Skill."""
        try:
            return self._window(self.scene.food_stations(self.player_addr),
                                max_dist=max_dist, use_2d=use_2d)
        except ProcError:
            return []

    def live_orbs(self, player_zone: str | None = None, max_dist: float = 0.0, use_2d: bool = False) -> list[Element]:
        """Dungeon secret orbs (InstanceOrb) in the loaded scene."""
        try:
            return self._window(
                (e for e in self.scene.elements(self.player_addr)
                 if e.is_orb and e.elem_id and (
                     "redorb" in e.elem_id.lower() or
                     "secretorb" in e.elem_id.lower())),
                player_zone, max_dist, use_2d)
        except ProcError:
            return []

    def live_chest_orbs(self, player_zone: str | None = None, max_dist: float = 0.0, use_2d: bool = False) -> list[Element]:
        """Chest orbs (ent.Element) and TimerCollectRun orbs in the loaded scene.

        ⛔ ORB-CHEST ACTIVITY LOGIC — DO NOT EDIT.  Filtered exclusively by
        chest_resolver.is_event_orb_id(); see docs/CHEST_ORB_LOGIC.md.  Do not
        change unless explicitly asked because a GAME UPDATE broke it."""
        try:
            from ..geo import zones as geo_zones
            pxyz = self.player_xyz()
            out = []
            for e in self.scene.elements(self.player_addr):
                if not e.elem_id:
                    continue
                if not is_event_orb_id(e.elem_id):
                    continue
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

    def world_orb_fx(self) -> list[tuple[str, bool, float, float]]:
        """(elem_id, glow-fx present, x, y) for loaded world secret orbs. The
        fx pointer is the reliable collected signal (collected = no fx); the
        position lets callers resolve the live element to the STATIC placement
        id (instance ids don't correspond to prefab ids) done lists key on.

        Untrusted reads are DROPPED, never coerced to False: the field offset
        comes from reflection (currentFx, super-chain). Before the reflection
        hardening a garbage read at the hardcoded slot read as fx-present for
        every orb ( junk-pointer-in-range) or fx-less after a patch shifted
        layouts - either lie then walks the done list (the 2026-09-18
        hide-collected regression: marked dungeon orbs reappearing red)."""
        out: list[tuple[str, bool, float, float]] = []
        try:
            for e in self.scene.elements(self.player_addr):
                if not (e.elem_id and e.elem_id.startswith("RedOrb_World")):
                    continue
                fx = self.orb_fx_present(e)
                if fx is None:
                    continue
                out.append((e.elem_id, fx, e.x, e.y))
        except ProcError:
            return []
        return out

    def orb_fx_present(self, e: Element) -> bool | None:
        """Whether element `e` currently carries its glow-fx pointer, or None
        when the read can't be trusted. The offset is reflected per element
        type (field 'currentFx', cached by hl.field_offset) — never a
        hardcoded slot (same rule as the ownerPlayer field: a drifted layout
        must degrade to "no opinion", not to a confident lie).

        Trust rules: null at the reflected slot = genuinely dark (collected);
        a plausible heap pointer = glowing; anything else (junk value, no
        runtime layout, unreadable memory) = None, i.e. "no opinion" —
        callers must treat that as unknown, NEVER as 'no fx' (a wrong-slot
        read would mark every orb collected / un-done every persisted
        mark — the 2026-09-18 hide-collected regression)."""
        try:
            tp = e.type_ptr
            if not tp:
                return None
            off = self.hl.field_offset(tp, "currentFx")
            if off is None:
                return None
            raw = self.hl.u64(e.addr + off)
        except ProcError:
            return None
        except Exception:
            return None
        if raw == 0:
            return False                # genuinely null fx -> collected
        return True if _is_ptr(raw) else None   # junk value -> no opinion

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

    def boss_max_health(self) -> float | None:
        """True max HP for the current dungeon boss, or None if not exposed.

        This is intentionally separate from current HP: an attach-time BOSS
        split is valid only when current/max proves the boss was still pristine.
        """
        scene = self.units()        # populates dungeon_boss; cached by LiveModel
        bid = self.dungeon_boss
        if not bid:
            return None
        for e in scene:
            if e.unit_id == bid:
                try:
                    return attributes.max_health(self.hl, e.addr)
                except ProcError:
                    return None
        return None

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

    def party_health_state(self) -> tuple[bool, set[int], set[int]] | None:
        """Return ``(grouped, alive_hero_addrs, present_hero_addrs)`` for the
        current scene. ``None`` means the scene/group read is not trustworthy.

        The cached ``st.Group`` roster decides whether this is a party. When it
        is unavailable, multiple live hero entities in the loaded instance are
        the conservative fallback. Only members seen in this dungeon latch into
        the Run Timer's group-wipe watcher, so someone remaining in town does
        not prevent a local dungeon wipe from resetting the boss split.
        """
        try:
            scene = self.units()
            heroes = {e.addr: e for e in scene if getattr(e, "is_hero", False)}
            roster = self.cached_group_roster()
            roster_heroes: set[int] = set()
            if roster is not None:
                for member in getattr(roster, "members", ()) or ():
                    if not getattr(member, "connected", True):
                        continue
                    hero = int(getattr(member, "hero", 0) or 0)
                    if not hero and getattr(member, "is_me", False):
                        hero = int(self.player_addr or 0)
                    if hero:
                        roster_heroes.add(hero)
                grouped = len(roster_heroes) >= 2
                watched = roster_heroes
                if not grouped:
                    # Some roster rows can have an unreadable hero pointer.
                    # Fall back to the heroes actually loaded in this instance
                    # rather than misclassifying a visible party as solo.
                    watched = set(heroes)
                    grouped = len(watched) >= 2
            else:
                watched = set(heroes)
                grouped = len(watched) >= 2

            if not grouped:
                watched = {int(self.player_addr or 0)} & set(heroes)
            alive: set[int] = set()
            present: set[int] = set()
            for addr in watched & set(heroes):
                try:
                    hp = attributes.health(self.hl, addr)
                except ProcError:
                    continue
                if hp is None:
                    continue
                present.add(addr)
                if hp > 0:
                    alive.add(addr)
            return grouped, alive, present
        except Exception:
            return None

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
        """0=Normal, 1=Hard, 2=Heroic from GameLayer.config, or None outside
        an instance. Primary difficulty source; independent of enemy levels."""
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

    def is_in_rift(self) -> bool:
        """True when the player is inside a Rift instance."""
        try:
            return self.scene.is_rift(self.player_addr)
        except ProcError:
            return False

    def is_in_dungeon_or_rift(self) -> bool:
        """True inside any dungeon or rift instance (dungeon overlay gate)."""
        return self.is_in_dungeon() or self.is_in_rift()

    def instance_state(self) -> tuple[bool, bool] | None:
        """`(in_dungeon, in_rift)`, or None when the scene can't be read.

        `is_in_dungeon()` / `is_in_rift()` collapse a failed scene read to
        False, which is the wrong bias for the overlay visibility loop: a
        transient failure would un-hide the minimap and entity overlays inside
        a dungeon and hide the Dungeon HUD. A player who is not located is not
        in an instance, though - that is knowledge, not a missing read - so it
        reports (False, False) and only a real read failure is 'unknown'.
        """
        try:
            if self.player_addr is None:
                return (False, False)
            if self.scene.gamelayer(self.player_addr) is None:
                return None
            return (self.is_in_dungeon(), self.is_in_rift())
        except Exception:
            return None

    def rift_status(self) -> RiftStatus:
        return self.rift_tracker.get_status()


    def dungeon_heroic(self) -> bool:
        """True when the current dungeon is a HEROIC run (the GameLayer heroic
        flag — heroic is a layer flag, not a difficulty value)."""
        try:
            gl = self.scene.gamelayer(self.player_addr)
            return bool(gl and self.scene._heroic_flag(gl))
        except (ProcError, AttributeError):
            return False

    def dungeon_mode(self) -> str | None:
        """'normal' | 'hard' | 'heroic' for the current instance, or None
        outside one / unreadable. Heroic wins over the plain difficulty int:
        the layer flag (OFF_LAST_HEROIC) first, then difficulty==2 (the boxed
        config value read 2 on a live heroic run, 2026-09-20) as the fallback
        when the flag can't be read."""
        if self.dungeon_heroic():
            return "heroic"
        diff = self.dungeon_difficulty()
        if diff is not None:
            if diff == 1:
                return "hard"
            if diff == 2:
                return "heroic"
            return "normal"
        return None

    def detected_mode(self) -> str | None:
        mode = self.dungeon_mode()
        if mode is not None:
            return mode
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

    # --- chest resolution (delegated to ChestResolver) -------------------
    def chest_table(self, chest_id: str, default_table: str | None = None) -> str | None:
        return self.chests_resolver.chest_table(chest_id, self.dungeon_boss,
                                                default_table)

    def nearest_chests_merged(self, xyz: XYZ, n: int, max_dist: float = 0.0,
                              player_zone: str | None = None,
                              use_2d: bool = False) -> list[ChestRow]:
        live = self.live_chests(player_zone, max_dist, use_2d=use_2d)
        live += self.live_chest_orbs(player_zone, max_dist, use_2d=use_2d)
        return self.chests_resolver.nearest_chests_merged(
            xyz, n, self.dungeon_boss, live,
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
            cls = self.locator.player_class()
            if cls:
                return cls
        except Exception:
            pass
        if self.player_addr:
            return self.hero_class_of(self.player_addr)
        return None

    def hero_class_of(self, hero_addr: int | None) -> str | None:
        """Playable class of ANY hero object, read from its equipped skill
        slots or scene entity (same authoritative source as the HUD)."""
        if not hero_addr:
            return None
        try:
            cls = self.locator.detect_class(hero_addr)
            if cls:
                return cls
        except Exception:
            pass
        # Fallback to scene entity (same as Entity HUD)
        try:
            from ..data.units import resolve_hero_class
            for u in self.units() or []:
                if getattr(u, "addr", 0) == hero_addr:
                    return getattr(u, "hero_class", None) or resolve_hero_class(getattr(u, "cls", None), getattr(u, "unit_id", None)) or None
        except Exception:
            pass
        return None

    def menu_windows(self) -> tuple[bool, bool] | None:
        """Both menu answers from ONE walk of `ui.GameUI.windows`.

        Returns `(any_open, gameplay_open)`: `any_open` is any open
        `ui.win.BaseWindow` (the escape menu included) and `gameplay_open` is
        the same list with `ui.win.EscapeMenu` excluded. Those are the two
        questions the overlay tick asks per frame - Rule A (escape menu) and
        Rule B (auto-hide_menus) - and answering them from a single walk means
        they cannot disagree about the same tick, nor cost two passes over the
        window list (plus two reflection probes) every 500 ms.

        Returns None when the gate could not be evaluated: the game isn't
        attached yet, the UI pointer is stale, a read didn't come back (zone
        swap, game relaunching, freed GameUI), or the list itself doesn't add
        up - a freed/zeroed array, an entry that isn't a window, an unreadable
        slot. None of those can support "nothing is open", so none of them
        answer with it. Callers that need one answer use `menu_state`, a thin
        wrapper over this.
        """
        if self.player_addr is None:
            return (True, True)   # character screen: a menu for hiding purposes
        try:
            ui_addr = self.locator.app.ui()
        except Exception:
            return None
        if not ui_addr:
            return None
        # ui.GameUI.windows resolved BY NAME: the 2026-09-11 patch inserted
        # `stutters` after `fpsGraph`, which pushed `additionalTips` 0x88 ->
        # 0x90 and `windows` 0x90 -> 0x98. The old hardcoded 0x90 then read the
        # (normally empty) tips array, so no menu ever registered and the
        # overlays stayed visible over open menus. OFF_UI_WINDOWS is only the
        # fallback (0x90 on the Aug-4 build, 0x98 on the Sep-11 build), so a
        # failed reflection probe alone must not poison the whole read.
        win_off = None
        try:
            ui_tp = self.hl.ptr(ui_addr)
            if ui_tp:
                win_off = self.hl.field_offset(ui_tp, "windows")
        except Exception:
            win_off = None
        if win_off is None:
            win_off = OFF_UI_WINDOWS
        try:
            arr_ptr = self.hl.ptr(ui_addr + win_off)
            if not arr_ptr:
                # A live GameUI always owns its windows array (it's built with
                # the UI), so a null one means we're following a stale pointer -
                # not that nothing is open.
                return None
            if self.hl.ptr(arr_ptr) is None:
                # Every live HL object carries its hl_type at +0, and so does
                # this array. A readable-but-null type pointer means the object
                # was freed and the page zeroed/reused - and a zeroed array
                # reads length 0, which would otherwise look exactly like a
                # confidently empty window list.
                return None
            arr_len = self.hl.i32(arr_ptr + 8)
            if not 0 <= arr_len <= MAX_UI_WINDOWS:
                return None
            if arr_len == 0:
                return (False, False)
            native_arr = self.hl.ptr(arr_ptr + 0x10)
            if not native_arr:
                # Non-empty list with no backing store: inconsistent, so don't
                # guess either way.
                return None
            any_open = False
            gameplay_open = False
            for i in range(arr_len):
                item_ptr = self.hl.ptr(native_arr + 0x18 + i * 8)
                if not item_ptr:
                    continue
                if not self.hl.is_a(item_ptr, "ui.win.BaseWindow"):
                    continue
                any_open = True
                if self.hl.class_of(item_ptr) != "ui.win.EscapeMenu":
                    gameplay_open = True
                    break          # both answers are settled
            if not any_open:
                # A NON-empty list that yielded no window: an entry that isn't
                # a `ui.win.BaseWindow`, an unreadable slot, a garbage pointer,
                # or a backing store that isn't mapped. Any of those means we
                # are looking at the wrong array (the 0x90-style drift) or at
                # malformed memory - and then "nothing is open" is a conclusion
                # this walk can't back, so report unknown instead of a
                # confident no. (An empty list is the ordinary, trusted case
                # and was answered above.)
                return None
            return (any_open, gameplay_open)
        except Exception:
            return None

    def menu_state(self, include_escape: bool = True) -> bool | None:
        """Tri-state menu gate: is a game menu covering the HUD?

        True  - a `ui.win.BaseWindow` is open (`ui.win.EscapeMenu` included
                unless `include_escape` is False)
        False - the UI's window list was read and is empty: we can PROVE the
                gameplay screen is clear
        None  - the gate could not be evaluated: the game isn't attached yet,
                the UI pointer is stale, a read didn't come back (zone swap,
                game relaunching, freed GameUI), or the window list doesn't add
                up (see `menu_windows`).

        This is deliberately the ONLY menu API - there is no boolean alias,
        because None has to reach the caller to be handled. Overlays HOLD their
        last proven state on None (core.game_state.TickReads) and the mouse
        recenter requires a definite False, so a boolean that folded None into
        False would quietly bring back the one bias this tri-state exists to
        expose (and its mirror image, a menu missed entirely). Ask
        `menu_windows` when both answers are needed: that walks the list once.
        """
        pair = self.menu_windows()
        if pair is None:
            return None
        return pair[0] if include_escape else pair[1]


