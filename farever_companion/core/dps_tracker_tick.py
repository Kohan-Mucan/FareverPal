"""The DPS tracker's per-tick update loop.

Split out of ``dps_tracker.py`` (which keeps the engine's state and its public
facade) so the tick can be read one phase at a time. The phases are exactly the
ones the banners in the old single method named, in the order they run:

    _watch_zone_edge ....... zone enter/exit: re-anchor the reader, the
                             debounced auto-reset, disarm a carried-over
                             game combat window
    _scan_scene ............ ONE scene read -> TickScene (heroes, foes, pets)
    _resolve_engagement .... respawn re-arm, boss/dummy engagement, re-target
    _consume_events ........ forward the fight hint, drain the capture ring
    _watch_deaths .......... the HP / vanish death edges
    _end_boss_if_over ...... kill flags + boss HP/despawn end signals
    _auto_pause_lull ....... the game's combat flag: seals the pack, never ends
                             the run (see the method)

Three rules hold the loop together, and each of them is load-bearing:

* one wall stamp before the scan for the cadence windows (roster TTL, profile
  swap, pet last-seen, the zone edge) and a second one after it for everything  that times the fight itself (event arrival, the boss re-target grace,
  the pack boundary). The scan can block for milliseconds on a wide dungeon read, and a hit
  must not be stamped with a time taken before it was decoded;
* ``time.monotonic`` stays monotonic where it already was -- the instance-edge
  debounce and the corpse-suppression deadline measure elapsed time and must
  not move when the wall clock does;
* ``m.units()`` is read exactly once per tick, by ``_scan_scene``, so no phase
  can act on a scene another phase did not see.

This module decides what a tick MEANS for the meter. It reads no memory
itself, touches no offsets, and never writes to the game.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

from . import game_state

from ..data import names, units as udata
from ..data import dungeons as ddata
from .dps_data import (
    BOSS_DAMAGE_LULL_S,
    BOSS_GONE_IDLE_S,
    BOSS_IDLE_AUX_S,
    BOSS_KILL_GRACE_S,
    BOSS_RELATCH_GRACE_S,
    GAME_COMBAT_END_CONFIRM_S,
    GROUP_ROSTER_TTL_S,
    INSTANCE_EDGE_DEBOUNCE_S,
    CombatSession,
    hero_cls_to_class,
)
from .dps_tracker_attribution import _in_instance
from .dps_tracker_events import (EVENT_ATTRIBUTED, EVENT_DROPPED,
                                 EVENT_FILTERED, EVENT_UNRESOLVED)

# One Activity-Log line per failing capture operation per window (see
# _log_capture_failure). The drain runs every tick -- ~10 Hz, from two threads --
# so an unthrottled report of a permanently broken reader would bury the log it
# exists to be findable in.
CAPTURE_FAIL_LOG_S = 30.0


@dataclass
class TickScene:
    """One tick's scene reads, handed to every phase that reacts to them.

    A dataclass rather than a dozen locals because the phases are sequential
    reactions to the SAME scan: the hero list feeds the pet pass and the
    engagement rules, and the foe snapshot feeds both the engagement and the
    end-of-fight checks. Mutable on purpose -- the respawn re-arm clears the
    engagement candidates mid-tick (see `_watch_respawn_rearm`).
    """

    pa: int | None = None
    # Wall time of this tick's scene read (see the module docstring).
    now: float = 0.0
    in_instance_now: bool = False
    pxyz: tuple[float, float, float] | None = None
    heroes: dict[int, tuple[str, bool, tuple[float, float, float], str]] = field(
        default_factory=dict)
    hero_hp_now: dict[int, float] = field(default_factory=dict)
    current_foes: dict[int, tuple[float, str, bool, bool, float, float, float]] = \
        field(default_factory=dict)
    boss_present: tuple[int, str, float] | None = None
    # The boss this instance declares, resolved once per tick from the zone
    # (see `dungeons.declared_boss_id`). None outside a resolvable instance.
    declared_boss: str | None = None
    dummy_present: tuple[int, str, float] | None = None


class DpsTrackerTick:
    """The per-tick update loop, mixed into ``DpsTracker``.

    A mixin rather than a coordinator service (the shape the other
    ``DpsTracker*`` modules use) because these phases read and write dozens of
    tracker fields -- sessions, cursors, engagement state -- and routing every
    one of those through ``self.tracker`` would add an indirection per field
    without making anything clearer.
    """

    # --- event intake -----------------------------------------------------#
    def update(self):
        # Non-blocking reentrancy guard: the drain worker (plain thread) and
        # the Top DPS overlay's timer (main thread) both drive update().
        # Whichever arrives first owns the pass; the other skips it. A blocking
        # lock would risk dragging the GUI thread behind a slow scene read —
        # skipping a tick is always safe (capture cursors make a double-drain
        # a no-op anyway; the next pass drains whatever accumulated).
        if not self._update_lock.acquire(blocking=False):
            return
        try:
            self._update_impl()
        finally:
            self._update_lock.release()

    def _update_impl(self):
        """One tick, phase by phase (the module docstring lists them).

        The ORDER is load-bearing: the zone edge re-anchors the reader before
        anything reads combat state, the scan is read ONCE for every phase, the
        fight hint reaches the capture engine before the drain (its re-derive
        cadence depends on it), events are applied before the death watches and
        the kill-flag check so a killing blow is counted before the fight it
        ends is archived, and the pack boundary runs last, on the freshest state.
        """
        m = self.model
        if m is None:
            return

        src = self.source
        if src is not None:
            self._start_capture(src)

        pa = getattr(m, "player_addr", None)
        now = time.time()
        in_instance_now = self._watch_zone_edge(m, pa, now)
        tick = self._scan_scene(m, pa, src, in_instance_now, now)

        # A second stamp for the react half: the scan above can block for
        # milliseconds on a wide dungeon read, and nothing below -- the events
        # it delivers, the boss re-target grace, the pack boundary -- should be
        # backdated by it (see the module docstring).
        now = time.time()
        tick.now = now
        self._resolve_engagement(tick, now)
        self._consume_events(src, tick, now)
        self._watch_deaths(pa, tick, in_instance_now, now)
        # Authoritative classes win over record-time skill guesses.
        self._sync_known_classes()
        self._end_boss_if_over(tick, now)
        self._auto_pause_lull(tick, now)
        self._trace_board_state()

    def _start_capture(self, src) -> None:
        """Bring the selected engine up, if it isn't already.

        Best-effort by design (an engine that cannot start this tick usually
        starts on the next one) -- but not silent: a source that never starts is
        why a meter sits empty, and the log line is the only trace of it.
        """
        try:
            src.ensure_started()
        except Exception as exc:
            self._log_capture_failure("ensure_started", exc)

    def _log_capture_failure(self, where: str, exc: BaseException) -> None:
        """One Activity-Log line per failing capture operation, per window.

        Throttled per operation (``CAPTURE_FAIL_LOG_S``), not per tick: this is
        called from the drain, so a reader that is simply gone would otherwise
        write a line per tick forever. Per operation rather than one shared
        timer so a broken ``events_after`` cannot hide a failing ``apply_event``.
        """
        cb = self.log_line
        if not callable(cb):
            return
        now = time.monotonic()
        if now - self._capture_fail_at.get(where, 0.0) < CAPTURE_FAIL_LOG_S:
            return
        self._capture_fail_at[where] = now
        self._log_activity(f"Top DPS: capture {where} failed "
                           f"({type(exc).__name__}: {exc}) — {self.capture_line()}")

    def _log_activity(self, msg: str) -> None:
        """One Activity-Log line, when a sink is wired. Never raises."""
        cb = self.log_line
        if not callable(cb):
            return
        try:
            cb(msg)
        except Exception:
            pass

    # --- zone edges --------------------------------------------------------#
    def _watch_zone_edge(self, m, pa, now) -> bool:
        """Note this tick's zone, re-anchoring on a rift/dungeon edge.

        Returns ``in_instance_now`` for the rest of the tick. Three independent
        reactions live here:

        * the reader re-anchor (both directions) — the display pool moves with
          the zone, so the Option 3 reader is re-hunted immediately;
        * the debounced auto-reset — a fresh meter on entering an instance:
          the edge arms a hold that must survive the debounce window (and a
          loading screen without a locatable player), then fires once;
        * the debounced clock drop — the fight clock ignores the game's combat
          window across a REAL zone change only. A one-tick is_dungeon flicker
          mid-dungeon (the activity slot clears between trash packs, an area
          transition unloads the arena, a read fails for a tick) is NOT a zone
          change, and used to drop the clock mid-fight (live 2026-10-02: the
          rift timer/segment reset ~3x mid-run).

        Auto-reset on zoning into a rift/dungeon: entering an instance starts
        a fresh meter (the open-world run is archived to fight history first).
        Skipped on the first tick while already inside (prev is None) so
        attaching mid-instance doesn't spuriously reset an empty tracker.
        """
        in_instance_now = _in_instance(m)
        if (pa is not None and self._in_instance_prev is not None
                and bool(in_instance_now) != self._in_instance_prev):
            # The display pool moves with the zone (both directions). Re-anchor
            # the Option 3 reader NOW — its wide-sweep rate gate (120 s) may
            # have just been armed by a startup/maintenance derive, which live
            # 2026-09-19 blinded the whole first pull (~1 min of dead meter).
            # Deliberately outside the auto_reset_zone gate: re-anchoring the
            # reader is not the meter reset.
            self._reader_rehunt("zone "
                                + ("enter" if in_instance_now else "exit"))
            # Edge stamp for _in_zone_settle (both directions, flicker or not):
            # the fight clock and the armed display ignore the game's combat
            # fields for ZONE_ARM_SETTLE_S after ANY edge.
            self._zone_edge_at = now
        # Auto-reset on zoning into a rift/dungeon: entering an instance starts
        # a fresh meter (the open-world run is archived to fight history first;
        # the reader re-anchor above is NOT debounced - re-anchoring is
        # harmless). The enter edge ARMS A HOLD and the reset fires once the
        # hold has elapsed, so a flickering is_dungeon read (loading screens,
        # in-dungeon area transitions) still cannot wipe a run mid-flight.
        #
        # The hold is a monotonic stamp on the tracker, not a per-tick "prev
        # was False" test, and that is the whole point: `_in_instance_prev` is
        # updated at the end of every tick, so the tick AFTER the edge reads
        # prev True. The old shape armed the stamp on the edge tick and cleared
        # it on the next one, which meant the debounce window could never
        # elapse: with the shipped 2.0 s debounce the reset NEVER fired (live
        # 2026-10-01 - a dungeon run opened on the previous zone's rows,
        # strangers included, and stayed that way until the user Reset it by
        # hand). Keeping the stamp while inside also lets the reset survive the
        # loading screen that usually coincides with the edge: the player
        # locator is gone for those ticks, so `pa` arrives a second later,
        # still inside, and the pending edge fires then instead of being lost.
        now_edge = time.monotonic()      # the debounce is elapsed time
        # Tests pin the window via _instance_edge_debounce_s (0.0 fires on the
        # edge tick itself, as it always has).
        debounce = getattr(self, "_instance_edge_debounce_s",
                           INSTANCE_EDGE_DEBOUNCE_S)
        # The window guards BOTH edges. A tick that reads "outside" in the
        # MIDDLE of a dungeon is not a zone change: the activity slot clears
        # between trash packs, an area transition unloads the arena, a read
        # fails for one tick. `_in_instance_prev` is written from that same
        # single read, so one such tick looked exactly like a real exit -
        # it re-armed the hold below, and the window later expiring wiped the
        # accumulated Trash Mobs mid-run, so every pull rendered as its own
        # fight (live 2026-10-01). Only an outside reading that HOLDS the
        # window is a proven exit and may arm the reset.
        if in_instance_now:
            outside_proven = (self._outside_since > 0.0
                              and now_edge - self._outside_since >= debounce)
            self._outside_since = 0.0
            self._exit_since = 0.0    # back inside: any exit streak is over
        else:
            if self._outside_since <= 0.0:
                self._outside_since = now_edge
            # Exit streak: stamped ONLY on the inside->outside transition (a
            # startup tick that reads outside never arms it), and it must hold
            # the debounce window before the clock drop below treats the exit
            # as real -- the same flicker guard as the enter hold above.
            if self._in_instance_prev is True and self._exit_since <= 0.0:
                self._exit_since = now_edge
            outside_proven = False
        # The enter edge is proven exactly when the meter arm is: the player
        # held the outside reading for the debounce window BEFORE crossing in.
        enter_proven = (self._in_instance_prev is False and in_instance_now
                        and outside_proven)
        if enter_proven:
            self._instance_edge_at = now_edge
        elif not in_instance_now or not self.auto_reset_zone:
            self._instance_edge_at = 0.0
        if (self.auto_reset_zone and pa is not None and in_instance_now
                and self._instance_edge_at > 0.0):
            if now_edge - self._instance_edge_at >= debounce:
                self._instance_edge_at = 0.0
                self._reset_on_zone_in()
        # A zone transition is NOT a fight. The game keeps reporting
        # isInCombat across a loading screen (and its combatStartTime can be
        # regenerated on arrival), so the fight clock must ignore the game's
        # combat window until the new zone settles -- otherwise the timer
        # starts the instant you zone in (live 2026-09-19). Drop the cached
        # read as well: a carried-over state must not be treated as this
        # zone's combat. But only on a PROVEN edge, symmetric with the meter
        # arm: the same one-tick flicker that must not wipe the meter
        # (live 2026-10-01) must not reset the timer either (live
        # 2026-10-02). The enter drop is covered by _in_zone_settle until the
        # debounced meter reset lands (ZONE_ARM_SETTLE_S >=
        # INSTANCE_EDGE_DEBOUNCE_S), so the clock cannot backdate across the
        # gap between the two.
        exit_proven = (self._exit_since > 0.0
                       and now_edge - self._exit_since >= debounce)
        if enter_proven or exit_proven:
            self.combat_state = None
            self._combat_wall_start_at = 0.0
            self._combat_was_in = None
            self._combat_id = None
            self._combat_start = 0.0
            self._game_combat_end_at = 0.0
            self._combat_poll_at = 0.0    # re-read the fields on the next tick
            self._exit_since = 0.0        # fired once per exit edge
        self._in_instance_prev = bool(in_instance_now)
        # Published for the rest of the tick AND for the event drain that runs
        # later in it: the dummy gate (dummy_zone_ok) asks whether this is a
        # zone a training-dummy test can exist in at all, and a single answer
        # per tick is what keeps the scan and a decoded hit from disagreeing.
        self._instance_now = bool(in_instance_now)
        return bool(in_instance_now)

    def _reset_on_zone_in(self) -> None:
        """The debounced auto-reset itself: wipe the meter and say so.

        Trash met in the zone transition itself rides through. The
        instance flag reads True the moment the enter edge is proven,
        so hits from the first seconds after zoning already sit on the
        trash meter when this fires; they used to archive as their own
        1-mob "Trash Mobs (Entered Dungeon/Rift)" entry in the day-wide
        trash file while the instance's real trash archived later as
        "<Boss> trash.json" — three files for one run (live
        2026-10-02: the 2nd rift showed one early mob split off from
        the pack it belonged to). The keep is told apart by HIT time,
        not start time: `_fight_start` can anchor to the game's
        combatStartTime, which pre-dates the edge across a loading
        screen, while `last_event_time` is the wall clock of the last
        hit actually recorded. A session whose last hit landed at or
        after the enter edge is this transition's trash and is carried
        into the fresh meter (it merges with the instance's trash and
        archives once, at boss engage); a leftover from the previous
        instance — its last hit older than the edge — still archives
        as before.
        """
        keep = None
        trash = self.trash_session
        if (trash.group_damage > 0 and trash.last_event_time > 0.0
                and self._zone_edge_at > 0.0
                and trash.last_event_time >= self._zone_edge_at):
            keep = trash
            # A blank meter for the wipe: reset()'s archive pass now
            # sees no trash (the overall copy of these hits clears with
            # the rest, as any reset's does), so the transition itself
            # writes nothing. The kept session is restored below, and
            # the instance's next hit lands in it.
            self.trash_session = CombatSession("Trash Mobs",
                                               kind="trash")
        self.reset("Entered Dungeon/Rift")
        if keep is not None:
            self.trash_session = keep
        self._log_activity("Top DPS: reset (Entered Dungeon/Rift)")

    # --- scene scan --------------------------------------------------------#
    def _scan_scene(self, m, pa, src, in_instance_now, now) -> TickScene:
        """Read the scene ONCE this tick; every later phase works from this.

        ``m.units()`` is read exactly once below: the hero pass registers
        names/classes/pointers, the pet pass resolves owners and the foe pass
        names and segments -- three reads of the same list would cost three
        scene walks and, worse, could disagree inside one tick.
        """
        tick = TickScene(pa=pa, now=now, in_instance_now=in_instance_now)
        if pa is None:
            self._scan_detached(m, tick)
            return tick
        curr_name = self._scan_identity(m, pa, src, now)
        tick.pxyz = m.player_xyz()
        # One zone read for the whole tick: it names the instance, and the
        # activity table names that instance's boss. Pure dict lookups after
        # this, so it costs no extra memory walk.
        try:
            tick.declared_boss = ddata.declared_boss_id(game_state.zone(m))
        except Exception:
            tick.declared_boss = None
        units = self._read_units(m)
        self._scan_heroes(m, units, pa, curr_name, tick)
        self._scan_pets(units, pa, tick)
        self._scan_foes(units, tick)
        return tick

    def _read_units(self, m) -> list:
        """The tick's unit list. A failed scene read is an empty scene, not a
        crash: the tick still runs its combat-state and clock work."""
        try:
            return m.units() or []
        except Exception:
            return []

    def _scan_detached(self, m, tick) -> None:
        """The no-player branch of the scan (menu, loading, or a lost locator)."""
        self.last_roster = None    # detached/menu: the group line goes blank
        self._hero_was_absent = True
        # Still scan heroes: death can clear player_addr (the locator
        # can't resolve a gone hero), and the method-level death watches
        # below match against the last-known addr — but they can only see
        # heroes this scan reports. Without this, a death that clears pa
        # blinds the HP edge, the vanish edge AND the wipe marker at once
        # (live: timer + meter never reset on death). Attribution still
        # needs a live pa and stays gated; this only feeds HP presence.
        hero_hp_now: dict[int, float] = {}
        for u in self._read_units(m):
            if getattr(u, "is_hero", False) and getattr(u, "addr", 0):
                hero_hp_now[u.addr] = float(getattr(u, "hp", 0.0) or 0.0)
        tick.hero_hp_now = hero_hp_now

    def _scan_identity(self, m, pa, src, now) -> str | None:
        """Who we are attached to this tick: character/profile edge, the game's
        combat sample, the roster seed.

        Returns the current character name (None when unreadable), which the
        hero pass needs to register the local row when the hero entity itself is
        missing from the scan.
        """
        curr_name = None
        try:
            curr_name = m.player_name(pa)
        except Exception:
            curr_name = None

        prof = None
        try:
            prof = m.player_profile()
        except Exception:
            prof = None

        char_changed = False
        if self._last_local_hero_name is not None and curr_name and curr_name != self._last_local_hero_name:
            char_changed = True
        elif self._active_profile is not None and prof and prof != self._active_profile:
            char_changed = True
        elif self._hero_was_absent and self._last_local_hero_addr is not None and pa != self._last_local_hero_addr:
            char_changed = True

        if char_changed:
            self.reset(f"Character Change: {curr_name or prof or 'New Character'}")
            if src is not None:
                try:
                    new_cur, _ = src.events_after(0)
                    self._src_cursor = new_cur
                except Exception as exc:
                    self._log_capture_failure("events_after", exc)
            if prof:
                self._active_profile = prof
            if self._history_dir is not None:
                self.history = []
                try:
                    self.load_history()
                except Exception:
                    pass

        self._last_local_hero_addr = pa
        if curr_name:
            self._last_local_hero_name = curr_name
        if prof:
            self._active_profile = prof
        self._hero_was_absent = False

        # Game-authoritative combat signals (auxiliary; slow cadence).
        self._poll_combat_state(pa)

        # Per-profile history: swap to the new profile's file (reset already archived the old one).
        if self._history_dir is not None:
            if now - self._profile_ts >= 2.0:
                self._profile_ts = now
                try:
                    prof = m.player_profile()
                except Exception:
                    prof = None
                if prof and prof != self._active_profile:
                    self._active_profile = prof
                    self.history = []   # old file's fights already persisted
                    try:
                        self.load_history()
                    except Exception:
                        pass

        # Seed roster names so off-scene members resolve instead of minting Party_XXXX rows.
        if now - self._roster_ts >= GROUP_ROSTER_TTL_S:
            self._roster_ts = now
            self._seed_group_roster(pa)

        return curr_name

    def _scan_heroes(self, m, units, pa, curr_name, tick) -> None:
        """Fill this tick's hero roster: display names, classes, HP and the
        dual ent.Hero/st.Player pointer map."""
        heroes: dict[int, tuple[str, bool, tuple[float, float, float], str]] = {}
        hero_hp_now: dict[int, float] = {}
        me_in_scene = False
        for u in units:
            addr = getattr(u, "addr", 0)
            if not addr:
                continue
            if not getattr(u, "is_hero", False):
                continue
            is_me = (addr == pa)
            if is_me:
                me_in_scene = True
            h_name = self._resolve_hero_name(addr, is_me)
            xyz = (getattr(u, "x", 0.0), getattr(u, "y", 0.0), getattr(u, "z", 0.0))
            h_cls = getattr(u, "cls", "ent.Hero") or "ent.Hero"
            u_id = getattr(u, "unit_id", "") or ""
            heroes[addr] = (h_name, is_me, xyz, h_cls)
            hero_hp_now[addr] = float(getattr(u, "hp", 0.0) or 0.0)

            # Authoritative entity class (exact same as Entity HUD)
            known_cls = getattr(u, "hero_class", "") or hero_cls_to_class(h_cls, u_id)
            if known_cls:
                self._hero_classes[addr] = known_cls

            # Dual-pointer registration: map ent.Hero and st.Player
            self._hero_pointers[addr] = (h_name, is_me)
            if m is not None:
                p_ptr = m._hero_player_ptr(addr)
                if p_ptr:
                    self._hero_pointers[p_ptr] = (h_name, is_me)
                    if known_cls:
                        self._hero_classes[p_ptr] = known_cls

            # Only pre-register the local player so the meter isn't blank;
            # allies are registered dynamically when combat events arrive for them.
            if is_me:
                me_in_scene = True
                self.overall_session.register_player(h_name, is_me=True, hero_class=known_cls)
                self.trash_session.register_player(h_name, is_me=True, hero_class=known_cls)
                self.boss_session.register_player(h_name, is_me=True, hero_class=known_cls)

        if curr_name and not me_in_scene:
            # Missing self entity (loads); register under the same display name so the row never duplicates.
            me_key = self._resolve_hero_name(pa, True)
            my_cls = self._hero_classes.get(pa, "")
            self.overall_session.register_player(me_key, is_me=True, hero_class=my_cls)
            self.trash_session.register_player(me_key, is_me=True, hero_class=my_cls)
            self.boss_session.register_player(me_key, is_me=True, hero_class=my_cls)

        # Live per-hero HP for the death indicator (display only): keyed
        # by the same display names the rows use. Heroes absent from the
        # scene aren't flagged dead (their HP is unknown).
        self.last_hero_status = {
            h_name: (hero_hp_now.get(addr, 0.0), is_me)
            for addr, (h_name, is_me, *_rest) in heroes.items()
        }
        tick.heroes = heroes
        tick.hero_hp_now = hero_hp_now

    def _scan_pets(self, units, pa, tick) -> None:
        """Learn pet -> owner mappings from this tick's units.

        Pets: non-hero owner resolving to a live hero; despawned pets linger
        briefly for in-flight events. Uses the tick's stamp for the 60 s
        last-seen cut -- it is a seconds-scale window, and the scan itself is
        the thing that just happened.
        """
        now_pet_scan = tick.now
        heroes = tick.heroes
        learned: list[tuple[int, int]] = []
        for u in units:
            addr = getattr(u, "addr", 0)
            if not addr or getattr(u, "is_hero", False):
                continue
            if not (getattr(u, "is_foe", False)
                    and getattr(u, "is_player_owned", False)):
                continue
            own = getattr(u, "owner_addr", 0)
            own_key = own
            if own and own not in heroes and own != pa:
                # Owner may be the st.Player pointer; translate to its hero entity.
                want = self._hero_pointers.get(own)
                if want:
                    for h_addr, (h_name, is_me, *_rest) in heroes.items():
                        if self._hero_pointers.get(h_addr) == want:
                            own_key = h_addr
                            break
            if own_key in heroes or own_key == pa:
                if addr not in self._pet_owners:
                    learned.append((addr, own_key))
                self._pet_owners[addr] = own_key
                self._pet_last_seen[addr] = now_pet_scan
        # Late-learned mapping folds the uid fallback row into the owner.
        for addr, own_key in learned:
            party = self._fallback_rows.get(addr)
            if not party:
                continue
            if own_key == pa:
                owner_name = self._resolve_hero_name(pa, True)
            elif own_key in heroes:
                owner_name = heroes[own_key][0]
            else:
                continue
            self._fold_party_row(party, owner_name, pet_skills=True)
            self._fallback_rows.pop(addr, None)
        cut = now_pet_scan - 60.0
        for a in [a for a, ts in self._pet_last_seen.items() if ts < cut]:
            self._pet_owners.pop(a, None)
            self._pet_last_seen.pop(a, None)
            self._fallback_rows.pop(a, None)

    def _scan_foes(self, units, tick) -> None:
        """Fill this tick's foe snapshot and pick the engagement candidates.

        Foes snapshot: naming/boss display/segmentation only; never diff HP into
        damage. Every unit the game reports counts — there is no distance cap,
        in instances or in the open world. The old one was 400m outside
        instances and stayed on inside them too (an is_dungeon read of
        attributes the model never had), so dungeon/rift trash beyond it was
        never recorded at all; distance never changed a damage number, only
        whether a far unit could be named/segmented as a boss.
        """
        foes: dict[int, tuple[float, str, bool, bool, float, float, float]] = {}
        boss_present = None
        boss_present_d = math.inf   # nearest boss-candidate distance
        boss_present_declared = False   # the instance DECLARES this one
        dummy_present = None
        dummy_near = None
        dummy_near_d = math.inf   # nearest live dummy distance
        # EVERY live dummy inside the radius, not just the nearest: the split
        # lists the whole yard so a cluster shows even when the decoded hits
        # carry no per-dummy address (see `DpsTracker.dummy_cluster`).
        dummy_cluster: list[tuple[int, str, float, float]] = []
        nearest_trash_d = math.inf   # nearest live non-boss foe (see the hold)
        boss_area_d = math.inf   # nearest ent.BossArea trigger volume
        # "Inside" the boss area: the trigger volume is anchored ON the arena,
        # so anything within this distance of it is standing in the room. Wide
        # enough to cover a spawn at the far lip, narrow enough that a trigger
        # in the next room down does not count.
        _BOSS_AREA_INSIDE_M = 60.0
        pxyz = tick.pxyz
        # The boss this instance DECLARES (data/dungeons.py, compiled from the
        # game's activity table) - the one boss signal that is not an
        # inference about the unit in front of us. None outside a resolvable
        # instance, and None in the two rifts, which share one entrance zone
        # and so cannot say which of their bosses you are in.
        declared_boss = tick.declared_boss
        # Also on the tracker, for the event router (core/dps_tracker_events.py):
        # a decoded hit names its target, so routing needs this without a scan.
        self._declared_boss = declared_boss
        for u in units:
            addr = getattr(u, "addr", 0)
            if not addr or getattr(u, "is_hero", False):
                continue

            x, y, z = getattr(u, "x", 0.0), getattr(u, "y", 0.0), getattr(u, "z", 0.0)

            hp = getattr(u, "hp", 0.0) or 0.0
            raw_id = getattr(u, "unit_id", "") or "Unknown"
            readable_name = names.unit_name(raw_id) or raw_id
            # The game's OWN "you have entered the boss's area" marker: the
            # `ent.BossArea` unit every boss room carries. It is a better
            # boundary than a distance guess, because it is the trigger volume
            # the encounter actually uses - it knows where the arena starts,
            # including for a boss the data layer has never heard of.
            #
            # Checked before the foe filters below because a BossArea is a
            # trigger, not a fighter: it has no HP and must never be counted as
            # a target. Recorded, not consumed - the close-out rule reads it.
            cls = str(getattr(u, "cls", "") or "")
            if "bossarea" in raw_id.replace("_", "").replace(".", "").lower() \
                    or "bossarea" in cls.replace(".", "").lower():
                d_area = math.dist(pxyz, (x, y, z)) if pxyz else 0.0
                if d_area and (d_area < boss_area_d):
                    boss_area_d = d_area
                continue
            # A training dummy is an open-world bench: the yards live in the
            # hubs, so inside a dungeon/rift a unit that merely looks like one
            # is ordinary trash there. This is the recognition half of
            # `dummy_zone_ok` (core/dps_dummy.py) — the gate that keeps a dummy
            # out of `dummy_near`, the HUD's auto-show, and the engagement both
            # the scan and a decoded hit consult.
            is_dummy = (self.dummy_zone_ok()
                        and udata.is_training_dummy(raw_id))
            # udata.is_boss is the ONE authority for "is this a boss": named
            # loot table, the calibrated flag bit, and the Rift family (the
            # `DemonSuperElite` line, which deliberately excludes _Guardian -
            # "an add, not a boss" - plus the curated ids the shipped sheets
            # predate).
            #
            # Three things this scan used to add on top of that are gone:
            #
            #   * re-deriving the prefix rule without the _Guardian exclusion,
            #     which promoted an add while the data layer said trash;
            #   * `"boss" in raw_id.lower()`. Across all 530 shipped units it
            #     matched exactly ONE id - R1KoboldBoss_Sparkling, "Snack of
            #     Ratsar (Spark)", a Golem trash mob in the King Ratsar
            #     dungeon. A substring is not a boss test, so it is deleted
            #     rather than narrowed: a name is not evidence of anything.
            #   * `raw_id == game_state.boss(m)`, which was NOT an independent
            #     activity read - `dungeon_boss` is produced by model.units()
            #     from this very function (core/model.py, `units()`), so it
            #     could only echo a unit is_boss had already accepted.
            #
            # What replaced it IS independent: `declared_boss`, the boss the
            # game's own activity table names for this instance (see
            # `dungeons.declared_boss_id`). Two signals stay because the data
            # layer cannot have them: that declaration, and the engine's class
            # name - but the class name only for units the data layer has
            # never heard of (see `udata.is_known_unit`), which is the case it
            # was kept for. When the CDB knows the unit, the CDB's verdict is
            # the verdict: Lost City of Mayda's trash crab is spawned from the
            # `ent.boss.Crabgantua` prefab, so the class alone promoted it to
            # "the boss", swallowed the pre-boss pulls into one boss fight and
            # nothing ever split (live 2026-10-02: "top dps in dungeon did not
            # split the fight before the boss fight like the rift does").
            #
            # 2026-10-03, HARD mode only: the same trap, one level down. A
            # hard-mode champion mob is a unit id the shipped sheets predate -
            # the CDB holds 530 units and NONE of them match champion/elite/hard
            # - and it is spawned from a boss-class prefab with the runtime
            # flag set, so the fallback believed it and promoted a TRASH MOB to
            # the boss. Each promotion ran `_engage_boss`, which archives the
            # trash run as "Trash Mobs Cleared": one live fight produced three
            # archived rows at 5:30 and the meter paused/reset per pack
            # (normal mode never showed it - those mobs ARE in the CDB, so the
            # fallback never got a vote).
            #
            # So the fallback now also requires the instance to DECLARE NO
            # BOSS. `declared_boss_id` is the game's own answer to "who is the
            # boss here", read from the activity table; when it has an answer,
            # an unknown unit's runtime claim cannot overrule it. Rifts declare
            # no boss (the shared Rifts entrance declines the lookup), so rift
            # behaviour is byte-for-byte unchanged, and an instance with no
            # declaration keeps the fallback exactly as before.
            data_knows_unit = udata.is_known_unit(raw_id)
            engine_claims_boss = (getattr(u, "is_boss", False)
                                  or (getattr(u, "cls", None)
                                      and "boss" in str(u.cls).lower()))
            # An ENGINE-ONLY boss claim: a unit the data layer has never heard
            # of, in an instance that names no boss, saying it is one. That is
            # a guess, and it is wrong often enough to matter - a hard/heroic
            # champion mob carries the flag and a boss-class prefab, so the Top
            # DPS target line CROWNED A TRASH MOB while the player was killing
            # a pack, and each pack then opened a "boss fight" and archived the
            # trash run (live 2026-10-03, hard then heroic: the meter reset
            # after every mob). The engine's word alone is no longer enough to
            # be the boss: an unknown unit in an undeclared instance stays
            # ordinary trash until the data layer learns it. Its name is logged
            # once so the gap is visible and fixable rather than silent.
            self._boss_claim_uncertain = (not data_knows_unit
                                          and declared_boss is None
                                          and engine_claims_boss)
            is_boss = (udata.is_boss(raw_id)
                       or (declared_boss is not None and raw_id == declared_boss)
                       or (not data_knows_unit and declared_boss is None
                           and engine_claims_boss))
            if (is_boss and not data_knows_unit and declared_boss is None
                    and engine_claims_boss):
                # The engine's word alone. Left UNCROWNED on purpose: a 👑 on a
                # trash mob while the player was killing a pack was the visible
                # half of this bug (live 2026-10-03), and the crown is the only
                # thing that claim is not entitled to. The NAME is untouched -
                # renaming it here leaked into archived file names.
                pass
            elif is_boss and not readable_name.startswith("👑 "):
                readable_name = f"👑 {readable_name}"
            if is_dummy and not readable_name.startswith("🎯 "):
                readable_name = f"🎯 {readable_name}"

            foes[addr] = (hp, readable_name, is_boss, is_dummy, x, y, z)
            self._foe_names[addr] = readable_name
            self._foe_raw_ids[addr] = raw_id
            # Nearest live non-boss foe that is NOT a player pet: the thing the
            # player is actually on while a DECLARED boss is merely loaded.
            # Pets are excluded - a summon is not a fight.
            if (pxyz and hp > 0 and not is_boss and not is_dummy
                    and addr not in self._pet_owners):
                d_t = math.dist(pxyz, (x, y, z))
                if d_t < nearest_trash_d:
                    nearest_trash_d = d_t
            if is_boss and hp > 0:
                # Rift/dungeon scans are instance-wide, so a second
                # boss-tagged entity (staged boss, clone, Asuna alongside the
                # boss) can sit in the same snapshot. Prefer the NEAREST one —
                # the boss being fought — instead of whichever unit iterates
                # first. An add winning this pick re-targeted boss_session at
                # it and split the real boss fight into a boss row plus mob
                # rows (live 2026-10-01).
                #
                # ...except the instance's DECLARED boss, which outranks
                # distance. That is the game's own statement about who the boss
                # is, and it is the fallback for content `is_boss` has not
                # heard of - so it is the one signal here that can say "this
                # unit is the boss" when nothing else can. Stickiness for a
                # fight already under way is handled where it belongs, in
                # `_track_boss_target`.
                d_b = math.dist(pxyz, (x, y, z)) if pxyz else 0.0
                is_declared = (declared_boss is not None
                               and raw_id == declared_boss)
                if (boss_present is None
                        or (is_declared and not boss_present_declared)
                        or (is_declared == boss_present_declared
                            and d_b < boss_present_d)):
                    boss_present = (addr, readable_name, hp)
                    boss_present_d = d_b
                    boss_present_declared = is_declared
            if is_dummy and hp > 0:
                if dummy_present is None:
                    dummy_present = (addr, readable_name, hp)
                # Nearest live dummy + its distance: the Top DPS overlay's
                # auto-show radius reads this between scene scans (see
                # DpsTracker.dummy_within). Same single pass, no second walk.
                d_dummy = math.dist(pxyz, (x, y, z)) if pxyz else 0.0
                rng = getattr(self, "dummy_range", 0.0)
                if not rng or float(rng) <= 0.0 or d_dummy <= float(rng):
                    dummy_cluster.append((addr, readable_name, hp, d_dummy))
                if d_dummy < dummy_near_d:
                    dummy_near = (addr, readable_name, hp, d_dummy)
                    dummy_near_d = d_dummy

        # An instance's DECLARED boss is held back while a closer NON-boss foe
        # is still alive. The boss entity can be loaded (hp>0) from the moment
        # you enter, long before you reach it, and engaging it on mere presence
        # swallowed every pre-boss pull into one boss fight (live 2026-10-02,
        # Lost City of Mayda: the whole run archived as "Boss Fight (Died)"
        # rows with NO trash session at all, so the Trash segment read 0 for
        # healing and damage taken while the boss board looked right). Trash
        # that is nearer is the fight you are actually in; the boss engages the
        # moment it becomes the nearest thing, or on the first hit that lands
        # on it (the event path, core/dps_tracker_events.py).
        #
        # Scoped to a DECLARED boss in an instance on purpose: a rift declares
        # no boss (`dungeons.declared_boss_id` declines the shared Rifts
        # entrance), so rift engagement is byte-for-byte unchanged, and a
        # non-declared boss is left exactly as it was.
        # ...and, since 2026-10-04, until the player is actually IN the arena.
        # The nearer-trash test alone is not enough: between packs there is no
        # trash near at all, so the declared boss engaged from wherever the
        # player happened to be - live KoboldsMines, the boss 88.6 m away at the
        # far end of the dungeon, before the player had entered the room. That
        # engagement then armed `_close_trash_near_boss`, which ARCHIVES the
        # trash run and RESETS both the trash and the OVERALL session, so the
        # whole run's numbers were wiped at the first lull between packs - the
        # "top dps is resetting the combat every time i go out of combat" half
        # of the same report.
        #
        # The arena is the game's own `ent.BossArea` trigger (already read
        # above as `boss_area_d`), so this works for a boss the data layer has
        # never heard of. A hit on the boss still engages it immediately - the
        # event path is separate and untouched - so "in the room OR hitting it"
        # is exactly what this encodes. Read from the LOCAL `boss_area_d`, not
        # `self._in_boss_area`: that field is only assigned further down, so
        # using it here would read the PREVIOUS tick's answer.
        in_boss_arena = (boss_area_d != math.inf
                         and boss_area_d <= _BOSS_AREA_INSIDE_M)
        if (boss_present is not None and boss_present_declared
                and tick.in_instance_now and pxyz
                and (nearest_trash_d < boss_present_d or not in_boss_arena)):
            boss_present = None

        # Boss-corpse suppression: right after a fight ends, the despawning
        # corpse (or a lingering second boss-tagged entity — staged next
        # boss, clone) still matches the engagement scan above — the next
        # tick would immediately "re-engage" (boss_session.reset()),
        # archiving the fight and pinning the timer at 00:00.0 on an empty
        # board. Live 2026-09-19: "timer showing 0 after fight ended" and
        # "not stopping when boss died". Ignore ALL boss engagement for a
        # short window after a fight ends (dungeon bosses are sequential;
        # a legit next boss is always further away than this), then
        # re-engage freely.
        if self._ended_boss_addr is not None:
            if self._ended_boss_deadline <= time.monotonic():
                self._ended_boss_addr = None      # window over
                self._ended_boss_deadline = 0.0
            else:
                # Suppress ALL boss engagement until the deadline (covers
                # the corpse AND a staged second boss-tagged unit; a legit
                # next boss is always further away than this window).
                boss_present = None

        if len(self._foe_names) > 4096:
            self._foe_names = {a: n for a, n in self._foe_names.items()
                               if a in foes}
            self._foe_raw_ids = {a: r for a, r in self._foe_raw_ids.items()
                                 if a in foes}

        tick.current_foes = foes
        tick.boss_present = boss_present
        tick.dummy_present = dummy_present
        # Persisted on the tracker, not only in this tick: the overlay
        # visibility rules ask "is a dummy near?" between scene scans too.
        self.dummy_near = dummy_near
        # ...and the whole yard, distance-ordered, so the dummy split can list
        # every one of them (core/dps_dummy.py, `dummy_split_targets`).
        dummy_cluster.sort(key=lambda c: c[3])
        self.dummy_cluster = dummy_cluster
        # The distance to the boss the scan picked, for the trash close-out
        # rule below. 0.0 means "no live boss in this snapshot", which the
        # rule reads as "not near one" — an unreadable position must never
        # read as distance zero, which would clear the run on sight.
        self.boss_near_d = (boss_present_d if (boss_present is not None
                                               and pxyz) else 0.0)
        # The game's own boss-area trigger, distance to it, and whether we are
        # INSIDE it. `_in_boss_area` is the authoritative boundary when the
        # room carries one; the radius above is the fallback for a boss room
        # that does not.
        self.boss_area_d = (boss_area_d if pxyz else 0.0)
        self._in_boss_area = bool(boss_area_d != math.inf
                                  and boss_area_d <= _BOSS_AREA_INSIDE_M)
        self.log_dummy_cluster_diag()      # Activity Log, once per change

    # --- engagement --------------------------------------------------------#
    def _resolve_engagement(self, tick, now) -> None:
        """Fold the scan into the fight model, in the order the fights overlap.

        Runs between the scan and the drain because it decides which session
        the events that follow belong to (boss, dummy, or trash).
        """
        self._watch_respawn_rearm(tick)
        if self._death_rearm_wait:
            # The respawn gate `_watch_respawn_rearm` documents, enforced here.
            # Death used to end by calling `reset()`, which cleared enough state
            # that no stale scan could re-engage; now that a death only PAUSES
            # the trash run, the boss scene is still sitting there on the next
            # tick and would re-arm the fight (and the HUD clock) the instant
            # the player respawned. Nothing engages until that gate opens.
            return
        # BEFORE the boss engages: the close-out is guarded on no boss fight
        # being live, and it must be — the trash run is the boundary the boss
        # pull starts from, so it closes first and the boss session then opens
        # against a clean board.
        self._close_trash_near_boss(tick)
        self._engage_boss(tick)
        self._engage_dummy(tick)          # core/dps_dummy.py
        self._track_dummy_target(tick)    # core/dps_dummy.py
        self._track_boss_target(tick, now)

    def _close_trash_near_boss(self, tick) -> None:
        """Close the accumulated trash run when the player reaches the boss.

        A trash run is ONE run across the instance (see `_auto_pause_lull`),
        which is right for "how did this whole pull go" and wrong the moment
        the boss is in front of you: the pre-boss trash and the boss pull are
        two different measurements, and averaging them together says neither.

        So walking into the boss's area ends the trash run — ARCHIVED, not
        discarded, so it is still in Fight History to compare against.

        Two ways to know the player has arrived, in order of trust:

        1. the game's OWN `ent.BossArea` trigger — the room's actual boundary,
           known even for a boss the data layer has never heard of;
        2. the `dps_boss_clear_range` radius around the boss (0 = never),
           mirroring the dummy radius, for a room that carries no trigger.

        The distance comes from the scan that already picked the boss, so
        neither costs a second walk of the scene.

        Guarded so it cannot fire mid-pull: it needs a live trash run with
        damage, no DUMMY test, no boss damage yet, and a real measurement.
        "No boss damage yet" rather than "no boss fight" because the boss
        entity is loaded from the moment the zone opens — `_engage_boss` can
        have engaged it while the player was still across the instance, so a
        guard on the fight flag would refuse exactly the case this rule exists
        for. Once it fires the trash session is empty, so it cannot re-fire.
        """
        if self.in_dummy_fight:
            return
        if float(getattr(self.boss_session, "group_damage", 0.0) or 0.0) > 0.0:
            return          # the boss pull is genuinely under way
        if getattr(self, "_in_boss_area", False):
            in_area = True
        else:
            try:
                rng = float(getattr(self, "boss_clear_range", 0.0) or 0.0)
            except (TypeError, ValueError):
                return
            if rng <= 0.0:
                return
            dist = float(getattr(self, "boss_near_d", 0.0) or 0.0)
            in_area = bool(0.0 < dist <= rng)
        if not in_area:
            return
        if self.trash_session.group_damage <= 0:
            return
        boss_name = ""
        present = getattr(tick, "boss_present", None)
        if present is not None and len(present) > 1:
            boss_name = str(present[1] or "").replace("👑", "").strip()
        reason = f"Approached {boss_name}" if boss_name else "Approached the boss"
        # `archive_current_encounter` picks its session by asking which fight is
        # LIVE, and the boss entity is loaded from the moment the zone opens —
        # so `in_boss_fight` can already be true here with an empty boss board,
        # and the archive would file an empty boss entry instead of the trash
        # run this boundary is actually about. Clear the flag for the call only:
        # the trash run is the thing being closed.
        was_boss = self.in_boss_fight
        self.in_boss_fight = False
        try:
            self.archive_current_encounter(reason, boss_name)
        finally:
            self.in_boss_fight = was_boss
        self.trash_session.reset()
        self.overall_session.reset()
        self.reset_foe_max_hp()
        if callable(self.log_line):
            try:
                self.log_line(f"Top DPS: trash run closed — {reason}")
            except Exception:
                pass

    def _watch_respawn_rearm(self, tick) -> None:
        """Hold off engagement until a dead local hero is alive again.

        A death reset clears the sessions, but the scene and game combat flag
        can still describe the old boss for several ticks. Do not let either
        stale scene data or queued capture events start a new fight until the
        respawn is alive and the game is no longer in combat.
        """
        if not self._death_rearm_wait:
            return
        pa = tick.pa
        respawn_hp = tick.hero_hp_now.get(
            (pa or self._last_local_hero_addr) or 0)
        if respawn_hp is not None and respawn_hp > 0.0:
            self._death_rearm_wait = False
            # Force a fresh game-combat sample after respawn; the previous
            # combat id/anchor belongs to the dead pull and must not keep
            # the HUD clock alive.
            self.combat_state = None
            self._combat_wall_start_at = 0.0
            self._combat_start = 0.0
            self._combat_id = None
            self._combat_was_in = None
            self._combat_poll_at = 0.0
            # Do not re-engage the corpse on the same respawn tick; the
            # next real event/new scene pass starts the next encounter.
            tick.boss_present = None
            tick.dummy_present = None
        else:
            tick.boss_present = None
            tick.dummy_present = None

    def _boss_close_due(self) -> bool:
        """Whether the boss is close enough to be the trash run's boundary.

        One boundary, one archive. This used to be unconditional — the moment a
        boss appeared anywhere in the instance-wide scan, the trash run was
        archived. That was right when each add was its own run and the archive
        was the run's end; it is wrong now that the trash run spans the whole
        instance, because the boss entity is loaded from the moment the zone
        opens. Archiving then would cut the run hours before the player reaches
        the boss AND archive it a second time when they finally arrive.

        So the appearance archive waits for the same proximity the close-out
        rule uses (`dps_boss_clear_range`); with that range disabled, the old
        unconditional behaviour stands and the appearance is the boundary again.
        """
        try:
            rng = float(getattr(self, "boss_clear_range", 0.0) or 0.0)
        except (TypeError, ValueError):
            return True
        if rng <= 0.0:
            return True
        dist = float(getattr(self, "boss_near_d", 0.0) or 0.0)
        return 0.0 < dist <= rng

    def _engage_boss(self, tick) -> None:
        """Start the boss fight this tick's snapshot implies (scene-driven)."""
        if not tick.boss_present or self.in_boss_fight:
            return
        b_addr, b_name, b_hp = tick.boss_present
        # An engine-only boss claim (a hard/heroic champion mob) still gets a
        # boss board - the player is hitting SOMETHING and those numbers are
        # real - but it must not archive the trash run. Archiving is what made
        # one fight read as a dozen rows and reset the meter per pack
        # (reported live 2026-10-03, hard and heroic). The declared boss, or a
        # boss the data layer knows, still splits the fight as before.
        if (getattr(self, "_boss_claim_uncertain", False)
                and self.trash_session.group_damage > 0):
            if callable(self.log_line):
                try:
                    self.log_line(
                        f"Top DPS: '{b_name}' looks boss-flagged but the "
                        "instance names no boss - keeping the trash run open "
                        "instead of splitting the fight here")
                except Exception:
                    pass
            self.trash_session.close_pack()
        elif self.trash_session.group_damage > 0 and self._boss_close_due():
            if self.trash_session.state == "COMBAT":
                self.trash_session.pause()
            # Seal the last pack BEFORE the archive deep-copies the session,
            # so the archived trash carries every pull complete rather than one
            # left open at the moment the boss appeared (see `PackSplit`).
            self.trash_session.close_pack()
            self.archive_current_encounter("Trash Mobs Cleared",
                                            boss_name=b_name)

        self.in_boss_fight = True
        # A fresh engagement clears corpse suppression (a real new boss,
        # not the despawning one).
        self._ended_boss_addr = None
        self._ended_boss_deadline = 0.0
        self.boss_session.reset()
        self.boss_adds_session.reset()
        self.boss_session.target_addr = b_addr
        self.boss_session.target_name = b_name
        self.boss_session.target_hp = b_hp
        self.boss_session.target_max_hp = b_hp
        self._boss_kill_pending = 0.0
        self._boss_missing_at = 0.0
        # Fresh engagement clears stale game-declared end signals.
        self._game_combat_end_at = 0.0
        # New encounter: drop HP baselines so the boss fight re-baselines.
        self.reset_foe_max_hp()
        self._log_capture_if_deaf("Boss engaged with no damage being read")

        for h_addr, (h_name, is_me, *_) in tick.heroes.items():
            if is_me:
                self.boss_session.register_player(h_name, is_me=is_me)

    # `_engage_dummy` and `_track_dummy_target` live in core/dps_dummy.py with
    # the rest of the training-dummy rules (the pin, the re-pin cases, the live
    # per-dummy HP); both are reached from here through the shared tracker.

    def _track_boss_target(self, tick, now) -> None:
        """Keep the boss fight pointed at the right boss, and its HP current."""
        if not self.in_boss_fight:
            return
        foes = tick.current_foes
        foe = foes.get(self.boss_session.target_addr)
        if foe is None:
            # The pinned boss is missing from this scan. Don't hop to
            # another boss-tagged entity on a single miss — that made the
            # meter label alternate between rift bosses every tick.
            # Re-target only once the pinned boss has stayed gone past
            # BOSS_RELATCH_GRACE_S (tests can shrink it via
            # _boss_relatch_grace_s), and then pick the NEAREST remaining
            # boss (the one being fought / the phase-swapped respawn).
            # A raw-id tie can't disambiguate — every rift boss shares the
            # same raw id — so distance is the only stable signal.
            if self._boss_missing_at <= 0.0:
                self._boss_missing_at = now
            grace = getattr(self, "_boss_relatch_grace_s",
                            BOSS_RELATCH_GRACE_S)
            if now - self._boss_missing_at >= grace:
                best = None
                best_d = math.inf
                for f_addr, f_info in foes.items():
                    if not (f_info[2] and f_info[0] > 0):  # is_boss, alive
                        continue
                    d = (math.dist(tick.pxyz, (f_info[4], f_info[5], f_info[6]))
                         if tick.pxyz else 0.0)
                    if d < best_d:
                        best_d, best = d, (f_addr, f_info)
                if best is not None:
                    b_addr, b_info = best
                    self.boss_session.target_addr = b_addr
                    self.boss_session.target_hp = b_info[0]
                    self.boss_session.target_max_hp = max(
                        self.boss_session.target_max_hp, b_info[0])
                    self.boss_session.target_name = b_info[1] or \
                        self.boss_session.target_name
                    foe = b_info
                    self._boss_missing_at = 0.0
        else:
            self._boss_missing_at = 0.0
        if foe is not None:
            self.boss_session.target_hp = foe[0]
            self.boss_session.target_max_hp = max(self.boss_session.target_max_hp, foe[0])
            if foe[1]:
                self.boss_session.target_name = foe[1]
            self._boss_last_seen = now

    # --- consume REAL damage events ----------------------------------------#
    def _consume_events(self, src, tick, now) -> None:
        """Forward the fight hint, then drain the capture ring into the meter.

        Forward fight state to the capture engine BEFORE draining the ring:
        the memory scanner keeps its combat re-derive cadence while a fight
        is live even when it has gone event-deaf (its own 6 s recency window
        collapses the moment the last decoded hit ages out, which is exactly
        when a re-derive is needed — live 2026-09-18, a 44 s boss fight
        streamed in ~6 bursts and a 0.0 s "fight" held 81 hits).
        The game's own combat flag is included (not just boss/dummy
        engagement): a trash pull with no decoded event yet is still a
        fight, and without the flag the scanner sat on its 45 s LULL
        cadence while the first hits landed — live, skills took ~30 s to
        show on a cold reader. The flag is sampled at 10 Hz and the zone
        settle window keeps a carried-over combat across loads from
        forcing combat cadence.

        Failures are reported, not swallowed: the cursor moves before the
        events are applied (the ring is a live stream — a cursor left behind a
        bad event would double-count it forever), so a raise inside the apply
        loop used to drop the REST of the batch with no trace in the log. Each
        event is applied on its own now, and the first failure of a streak is
        logged (``_log_capture_failure``), so a broken reader or a poisoned
        event surfaces as an Activity-Log line instead of as a quietly wrong
        meter.
        """
        if src is not None:
            try:
                cst = self.combat_state
                game_in = bool(cst and cst.get("in_combat")) \
                    and not self._in_zone_settle()
                src.note_combat(bool(self.in_boss_fight or self.in_dummy_fight
                                     or game_in))
            except Exception as exc:
                self._log_capture_failure("note_combat", exc)
            # And whether that fight is one the log narrates — an instance run
            # (the tick's one instance read) or a dummy test. The bridge's
            # periodic health roll-up speaks only then: open-world hits still
            # feed the meter, but they stop writing a line per 50 events into
            # the Activity Log nobody is reading a story in.
            try:
                src.note_watched(bool(self.in_dummy_fight
                                      or tick.in_instance_now))
            except Exception as exc:
                self._log_capture_failure("note_watched", exc)
            # Feed the local-hero addr to the memory reader so decoded hits
            # on you flag incoming (taken channel); harmless for bridge mode.
            try:
                src.my_hero_hint = tick.pa
            except Exception as exc:
                self._log_capture_failure("my_hero_hint", exc)
        if src is None:
            return
        try:
            new_cursor, events = src.events_after(self._src_cursor)
        except Exception as exc:
            self._log_capture_failure("events_after", exc)
            return
        self._src_cursor = new_cursor
        if events:
            self._last_events_ts = now   # any event = reader is alive
        attributed = 0
        unresolved = 0
        filtered = 0
        for ev in events:
            try:
                disposition = self._apply_event(
                    ev, tick.pa, tick.heroes, tick.current_foes, now)
            except Exception as exc:
                self._log_capture_failure("apply_event", exc)
                disposition = EVENT_DROPPED
            if disposition == EVENT_ATTRIBUTED:
                attributed += 1
            elif disposition == EVENT_UNRESOLVED:
                unresolved += 1
            elif disposition == EVENT_FILTERED:
                filtered += 1
        # One call per drain, not one per event: the counter is shared with the
        # engine's own stages (core/dps_source.py DpsEventStats), and the drops
        # are derived from these numbers so they can never drift apart.
        try:
            src.stats.note_drained(len(events), attributed, unresolved,
                                   filtered)
        except Exception as exc:
            self._log_capture_failure("note_drained", exc)

    # --- deaths, fight ends, lulls -----------------------------------------#
    def _watch_deaths(self, pa, tick, in_instance_now, now) -> None:
        """The two death edges, against the last-known addr.

        Deaths are facts, not estimates: a hero HP edge (prev>0 -> hp<=0)
        records the death count, and a local death archives + resets — plus
        the vanish edge for deaths that remove the hero entity outright.
        Both watches match against the last-known hero addr when the locator
        momentarily loses the player (pa None on death/menu): a death that
        clears pa must still match the corpse / absence against the hero we
        knew. Attribution keeps using live pa only.
        """
        eff_pa = pa if pa else self._last_local_hero_addr
        self._watch_local_vanish(eff_pa, tick.hero_hp_now, in_instance_now, now)
        self._watch_hero_deaths(eff_pa, tick.heroes, tick.hero_hp_now)

    def _end_boss_if_over(self, tick, now) -> None:
        """End the boss fight on a kill flag, a dead boss, or a despawn;
        pause it when the damage stops against a boss that is still alive."""
        if not (self.in_boss_fight and self.boss_session.group_damage > 0):
            return
        foe = tick.current_foes.get(self.boss_session.target_addr)
        killed = self._boss_kill_pending and (now - self._boss_kill_pending <= BOSS_KILL_GRACE_S)
        if foe is not None and foe[0] > 0:
            # Alive and still in the scene, so neither the kill nor the
            # despawn arm below can fire - and `_auto_pause_lull` returns
            # early while `in_boss_fight`, so the trash damage lull never
            # runs either. Nothing at all could end this fight: a player who
            # stops attacking a surviving boss (walked off, died, dropped to
            # adds) left the session in COMBAT, and a COMBAT session's
            # duration is `now - start_time`, so the header counted a fight
            # that was over. Live 2026-10-01, a rift pull abandoned with the
            # boss still up: the meter read 02:48.5 and climbing, rows on
            # screen, header reading IN COMBAT.
            #
            # PAUSE, do not end. `_end_boss_fight` would archive the pull and
            # open the BOSS_CORPSE_SUPPRESS_S window, after which the
            # still-standing boss re-engages and `boss_session.reset()` pins
            # the clock at 00:00.0 again - trading a clock that runs away for
            # one that restarts on every lull. Pausing leaves the fight
            # engaged: the next hit resumes it (`record_hit` resumes a PAUSED
            # session, shifting the clock past the idle), and the kill and
            # despawn arms below still archive the encounter properly.
            last = self.boss_session.last_event_time
            if last and now - last >= BOSS_DAMAGE_LULL_S:
                self.boss_session.pause()
            return
        if foe is not None and foe[0] <= 0:
            # HP on the floor: the fight was WON. Flagged as a fact on the
            # session rather than left to be re-read out of the reason string
            # later, because a personal best that quietly counted wipes would be
            # worse than no record at all (see `boss_killed`).
            self.boss_session.boss_killed = True
            self._end_boss_fight("Boss Defeated")
        elif foe is None:
            idle = now - max(self.boss_session.last_event_time,
                             self._boss_last_seen or 0.0)
            # Game signals confirm an end only once also damage-idle for BOSS_IDLE_AUX_S.
            game_confirmed = (self._game_combat_end_at > 0
                              and (now - self._game_combat_end_at)
                              >= GAME_COMBAT_END_CONFIRM_S)
            if killed or idle > BOSS_GONE_IDLE_S \
                    or (game_confirmed and idle >= BOSS_IDLE_AUX_S):
                if killed:
                    self.boss_session.boss_killed = True
                self._end_boss_fight("Boss Defeated" if killed else "Boss Fight")

    def _auto_pause_lull(self, tick, now) -> None:
        """Split the trash run into PACKS on the game's combat flag.

        This no longer ENDS anything. A trash run is one run: it accumulates
        through adds, through the out-of-combat gap between them, and through
        a death, and only a manual Reset or leaving the instance ends it (the
        zone edge, `auto_reset_zone`). Reporting 2026-10-03: "top dps is
        resetting on different adds / out of combat it should be 1 run for
        trash even if i die".

        Two things used to end it here and no longer do — the edge-triggered
        `isInCombat` pause (briefly False between adds, so every add was a new
        run) and the 6 s damage lull (a pack you walk away from for six
        seconds ended the whole thing). Both remain useful for one purpose,
        which is why this survives: the PACK breakdown still needs to know
        where a pull ended, so the combat edge still seals the pack and resets
        the foe HP baseline. The pack boundary is a display concern; the run is
        the measurement, and it must not be cut at a display boundary.

        Boss/dummy fights are excluded above: they end on their own signals
        (boss death, the target length), with this flag only confirming.
        """
        if self.in_boss_fight or self.in_dummy_fight:
            return
        if tick.pa is None:
            return
        st = self.combat_state
        in_cb = st.get("in_combat") if st is not None else None
        was_in = self._ow_in_combat_prev
        self._ow_in_combat_prev = in_cb
        # Edge-triggered (True -> False) on purpose: a stale always-False flag
        # must never machine-gun a pack close every tick.
        if was_in is True and in_cb is False:
            self.trash_session.close_pack(now)
            self.reset_foe_max_hp()

    # --- board trace (opt-in) ---------------------------------------------#
    def _trace_board_state(self) -> None:
        """Explain a meter that CLEARS without a reset line (see core/dps_trace).

        A number dropping to zero has two very different causes: the tracker
        wiped (and said so), or the AUTO board switched to a different session
        (which nothing logged). This watches the board the overlay actually
        shows — `session_for("auto")` — and records a line the moment the
        session IDENTITY changes (a switch) or its total FALLS (a clear), with
        the whole decision state beside it. Only on change, so a steady board
        costs nothing; `enabled()` is checked first, so the default path pays a
        dict lookup and stops.
        """
        from . import dps_trace
        if not dps_trace.enabled():
            return
        try:
            session = self.session_for("auto")
        except Exception:
            return
        try:
            dmg = round(float(session.group_damage or 0.0), 1)
        except Exception:
            return
        cur = (id(session), dmg)
        prev = getattr(self, "_trace_board_prev", None)
        self._trace_board_prev = cur
        if prev is None:
            return                      # seed: the opening board is not a change
        switched = prev[0] != cur[0]
        dropped = cur[1] < prev[1] - 0.5
        if not (switched or dropped):
            return
        try:
            in_combat = (self.combat_state or {}).get("in_combat")
        except Exception:
            in_combat = None

        def _tot(s) -> float:
            try:
                return round(float(s.group_damage or 0.0), 1)
            except Exception:
                return 0.0

        state = {
            "event": "switch" if switched else "cleared",
            "board": f"{session.kind}:{session.name}",
            "dmg": cur[1], "prev_dmg": prev[1],
            "in_boss_fight": bool(self.in_boss_fight),
            "in_dummy_fight": bool(self.in_dummy_fight),
            "instance_now": bool(getattr(self, "_instance_now", False)),
            "instance_prev": bool(getattr(self, "_in_instance_prev", False)),
            "boss": _tot(self.boss_session),
            "boss_adds": _tot(self.boss_adds_session),
            "trash": _tot(self.trash_session),
            "overall": _tot(self.overall_session),
            "in_combat": in_combat,
        }
        dps_trace.record(**state)
        self._log_activity(
            f"Top DPS: board {state['event']} — {state['board']} "
            f"{prev[1]:,.0f}\u2192{cur[1]:,.0f} \u00b7 boss={state['boss']:,.0f} "
            f"adds={state['boss_adds']:,.0f} trash={state['trash']:,.0f} "
            f"overall={state['overall']:,.0f} \u00b7 in_boss={state['in_boss_fight']} "
            f"inst={state['instance_now']}/{state['instance_prev']} "
            f"combat={state['in_combat']}")
