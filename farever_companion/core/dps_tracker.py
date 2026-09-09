"""DPS tracking engine: multi-segment combat tracking fed by real damage events.

HP is polled for display/segmentation only, never to fabricate numbers.

This module is the tracker's state and facade: consumers hold a ``DpsTracker``
and the per-concern implementations live beside it -- ``dps_tracker_tick.py``
(the per-tick update loop), ``dps_tracker_events.py`` (event application),
``dps_tracker_lifecycle.py`` (fight end / wipe / death),
``dps_tracker_attribution.py`` / ``dps_tracker_history.py``, and
``dps_dummy.py`` (everything the training-dummy test needs). The names other
modules consume through this facade are re-exported here -- ``__all__`` is that
list, and it is what keeps the re-exports from reading as unused imports.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

from ..data import names
from .damage_events import clean_wire_name
from .dps_dummy import DpsTrackerDummy
from .dps_tracker_tick import DpsTrackerTick
from .dps_tracker_attribution import (
    _GENERIC_PLAYER_LABELS,
    _in_instance,
    _read_roster,
    same_player_name,
    DpsTrackerAttribution,
    own_row,
    solo_only_view,
    solo_status,
    view_totals,
)
from .dps_tracker_history import (
    DpsTrackerHistory,
    HISTORY_KIND_TOKENS,
)
from .dps_tracker_lifecycle import DpsTrackerLifecycle
from .dps_tracker_events import (
    EVENT_DROPPED,
    EVENT_UNRESOLVED,
    DpsTrackerEvents,
)
from .dps_data import (
    BOSS_IDLE_AUX_S,
    GAME_COMBAT_END_CONFIRM_S,
    GAME_COMBAT_POLL_S,
    PARTY_RETRY_S,
    _roster_change_key,
    _roster_line,
    CombatSession,
    DamageEvent,
    HitEvent,
    _PET_SUFFIX,
    PackSplit,
    PlayerParse,
    SkillParse,
    TargetParse,
    read_history_file,
    top_skill_weapon,
    weapon_families_used,
    weapon_family_of,
    weapon_for_skill,
    weapons_used,
    hero_cls_to_class,
    infer_class_from_skill,
    log,
)

# The facade's re-export list. pyflakes honours `__all__`, so an entry here is
# what marks a deliberate re-export as used rather than accusing it; the
# hygiene suite resolves every entry back to this module. A name belongs here
# only when something consumes it through dps_tracker -- everything else is
# importable from the module that defines it (dps_data, attribution, ...).
__all__ = [
    "DpsTracker",
    "solo_status",
    "CombatSession",
    "PlayerParse",
    "SkillParse",
    "HitEvent",
    "DamageEvent",
    "PackSplit",
    "TargetParse",
    "top_skill_weapon",
    "weapon_families_used",
    "weapon_family_of",
    "weapon_for_skill",
    "weapons_used",
    "read_history_file",
    "infer_class_from_skill",
    "hero_cls_to_class",
    "GAME_COMBAT_END_CONFIRM_S",
    "BOSS_IDLE_AUX_S",
    "HISTORY_KIND_TOKENS",
    "own_row",
    "solo_only_view",
    "view_totals",
    "game_combat_settled",
]


CO_COMBAT_WINDOW_S = 10.0   # a player this fresh counts as fighting alongside you


def game_combat_settled(st) -> bool:
    """True when the game has recorded that the encounter it was tracking is
    CLOSED, whether or not ``isInCombat`` has caught up yet.

    ``isInCombat`` is a boolean the client clears on its own schedule, not a
    fight boundary. A rift boss kill, a wipe, a zone change or a disconnect
    can all leave it set for a while after ``combatEndTime`` was stamped, and
    anything that runs a clock off that boolean as a LEVEL - the meter's
    armed clock, the IN COMBAT badge - then keeps counting a fight that ended
    minutes ago, with nothing behind the number.

    ``combat_end > combat_start > 0`` is the game saying "this encounter is
    closed". It cannot be true while a fight is live (the field reads 0.0
    then), so it is a safe end-confirm and not a timeout: a slow first decode
    mid-fight is unaffected. Read defensively - a reader that cannot resolve
    the field reports 0.0, which is the open case.
    """
    if not st:
        return False
    try:
        start = float(st.get("combat_start") or 0.0)
        end = float(st.get("combat_end") or 0.0)
    except (AttributeError, TypeError, ValueError):
        return False
    return start > 0.0 and end > start


# After a zone enter/exit, ignore the game's combat window this long: a
# transition is not a fight, so the timer must not start on zone-in (see
# DpsTrackerTick._watch_zone_edge). The other tick constants live in dps_data
# with the rest of the engine's tunables.
ZONE_ARM_SETTLE_S = 3.0


class DpsTracker(DpsTrackerDummy, DpsTrackerTick):
    """Multi-segment combat tracking fed by real damage events.

    HP is display/segmentation only. With no reader nothing is recorded;
    the meter shows its empty state rather than fabricated numbers.
    """

    def __init__(self, model):
        self.model = model
        # Domain coordinators preserve the long-standing DpsTracker facade
        # while the large implementation is migrated out in safe, testable
        # slices. They never touch capture or native DLL code.
        self._history_component = DpsTrackerHistory(self)
        self.lifecycle = DpsTrackerLifecycle(self)
        self.events = DpsTrackerEvents(self)
        self.attribution = DpsTrackerAttribution(self)

        self.overall_session = CombatSession("Overall Run", kind="overall")
        self.trash_session = CombatSession("Trash Mobs", kind="trash")
        self.boss_session = CombatSession("Boss Fight", kind="boss")
        # Adds hit while the boss is up (own segment beside boss/trash).
        self.boss_adds_session = CombatSession("Boss Adds", kind="boss_adds")
        # Training Dummy has its own group like a boss fight.
        self.dummy_session = CombatSession("Training Dummy", kind="dummy")

        self.segment_view: str = "auto"  # "auto", "boss", "boss_adds", "trash", "dummy", "overall"
        self.in_boss_fight: bool = False
        self.in_dummy_fight: bool = False
        # Whether a training dummy may be engaged at all — the meter's Stop Test
        # latch. A dummy is the one fight with no natural end, so its end signal
        # has to be the user's; core/dps_dummy.py owns that rule (and every other
        # dummy-specific one) and this is the state it works on.
        self.dummy_test_armed: bool = True

        # Encounter Fight History (archived finished sessions)
        self.history: list[CombatSession] = []
        self._history_path: Path | None = None   # explicit file (tests/tools)
        # Dedicated moddata/dps/ folder — OFF by default so headless runs and
        # probes never touch disk. The app opts in when it attaches to the
        # game (game_attach -> set_history_dir(dps_dir())).
        self._history_dir: Path | None = None
        self._active_profile: str | None = None  # profile the loaded file is for
        self._profile_ts: float = 0.0

        self._started_at: float = time.time()

        self._hero_name_cache: dict[int, str] = {}
        # Authoritative class per hero; _sync_known_classes() writes it onto rows.
        self._hero_classes: dict[int, str] = {}
        self._hero_pointers: dict[int, tuple[str, bool]] = {}
        self._foe_names: dict[int, str] = {}
        self._foe_raw_ids: dict[int, str] = {}   # addr -> raw unit id (kill-uid matching)
        self._src_cursor: int = 0
        self._boss_kill_pending: float = 0.0        # wall clock of a "killed" flag
        self._boss_last_seen: float = 0.0
        self._boss_missing_at: float = 0.0   # wall clock the pinned boss went absent
        # Boss-corpse suppression: addr + deadline (monotonic) of the boss that
        # JUST ended, so the despawning corpse can't instantly re-engage
        # (timer pinning at 00:00.0 on an empty board).
        self._ended_boss_addr: int | None = None
        self._ended_boss_deadline: float = 0.0
        # The boss the current instance DECLARES (data/dungeons.py), refreshed
        # each scene scan and read by the event router so a hit on it is
        # recognised as boss damage even when `units.is_boss` has never heard
        # of the id. None outside a resolvable instance.
        self._declared_boss: str | None = None
        # Whether the currently seen "boss" is only the ENGINE's word for it
        # (see `_scan_foes`). Uncertain claims do not split the fight.
        self._boss_claim_uncertain: bool = False
        # Wall time of the last zone enter/exit: the fight clock ignores the
        # game's combat window for ZONE_ARM_SETTLE_S after it (a transition is
        # not a fight -- see DpsTrackerTick._watch_zone_edge).
        self._zone_edge_at: float = 0.0
        # The zone-in auto-reset's debounce HOLD: the monotonic stamp of the
        # enter edge, kept across ticks until the window elapses (or the zone
        # read leaves the instance). See DpsTrackerTick._watch_zone_edge.
        self._instance_edge_at: float = 0.0
        # Start of the current OUTSIDE streak (monotonic, 0 = inside). The
        # debounce has to hold on this side too, or one tick that reads
        # "outside" mid-dungeon (activity slot cleared between packs, an area
        # transition, a failed read) is indistinguishable from a real exit and
        # re-arms the zone-in reset, which then wipes the run's own damage.
        self._outside_since: float = 0.0
        # Start of the current INSIDE->OUTSIDE streak (monotonic, 0 = not
        # leaving). The fight-clock drop on a zone EXIT is debounced the same
        # way the zone-in meter reset is: stamped only on the transition
        # itself, held across ticks, and the clock drops once the window has
        # elapsed -- so a one-tick "outside" flicker mid-dungeon cannot reset
        # the timer mid-fight (live 2026-10-02). See DpsTrackerTick.
        self._exit_since: float = 0.0

        # Game combat state (auxiliary to idle heuristics; see _poll_combat_state).
        self.combat_state: dict | None = None
        self._combat_id: int | None = None
        self._combat_was_in: bool | None = None
        self._game_combat_end_at: float = 0.0
        self._combat_poll_at: float = 0.0
        # Live encounter timer: anchor wall time when combatStartTime moves (see _poll_combat_state).
        self._combat_start: float = 0.0      # last seen combatStartTime (engine s)
        self._combat_wall_start_at: float = 0.0  # wall clock at that fight start

        # Pets report the pet as serverSource; map pet addr -> owning hero addr (Details-style merge).
        self._pet_owners: dict[int, int] = {}
        self._pet_last_seen: dict[int, float] = {}
        # Source addrs learned to be the local hero's item/summon proc.
        self._owned_proc_sources: dict[int, int] = {}
        # addr -> Party_XXXX row; folds into the owner once the owner is learned.
        self._fallback_rows: dict[int, str] = {}
        # Proxy attribution warnings are transition-only: one per unresolved
        # source/target until it resolves, not one per hit.
        self._proxy_unresolved_logged: set[tuple[str, str, str]] = set()
        self._pet_probe_at: dict[int, float] = {}    # last probe time per addr
        self._party_retry_at: dict[int, float] = {}  # last name retry per addr
        self._roster_ts: float = 0.0                 # last st.Group roster seed
                                                     # (see _seed_group_roster)
        # Roster change-log state (log_line sink wired to the Activity Log).
        self._last_roster_key: tuple | None = None
        # Last decoded st.Group snapshot (the tracker seeds it every 2s);
        # the display path reads this cache instead of polling the game.
        self.last_roster = None
        self.log_line = None                         # Callable[[str], None] | None
        # Zone edge-detect: reset the meter when zoning into a dungeon/rift.
        # None until the first update so attaching while already inside an
        # instance doesn't spuriously wipe an already-empty tracker.
        self._in_instance_prev: bool | None = None
        # This tick's instance answer, kept for the dummy gate: a training
        # dummy is an OPEN-WORLD bench (core/dps_dummy.dummy_zone_ok), and the
        # scene scan and the event drain have to read the same per-tick fact or
        # one of them would report a dummy the other refuses. False until the
        # first tick, which is the open-world answer the rest of the dummy
        # feature already assumes.
        self._instance_now = False
        # Open-world solo combat-flag edge (True -> False pauses; None until
        # the first read so a stale flag can never pause).
        self._ow_in_combat_prev: bool | None = None
        # Live per-hero HP from the last unit scan, keyed by the display name
        # used on rows (so the overlay can flag dead players). Empty until the
        # first scene scan. Value is (hp, is_me).
        self.last_hero_status: dict[str, tuple[float, bool]] = {}
        # Nearest live training dummy (addr, name, hp, distance in metres) from
        # the last scene scan, None when the scan held none. The Top DPS
        # overlay's visibility gate reads this between scans (a dummy in range
        # keeps the meter on screen even in the open world) and so does the
        # meter's own empty state — see dummy_within().
        self.dummy_near: tuple[int, str, float, float] | None = None
        # Every live dummy inside the radius, nearest first, from the same scan
        # pass. The split lists the whole yard (core/dps_dummy.py,
        # `dummy_split_targets`) so a cluster shows every dummy, not just the
        # ones the decoded damage happened to name.
        self.dummy_cluster: list[tuple[int, str, float, float]] = []
        # Split diagnostics (Activity Log): the distinct decoded hit-target
        # addresses seen in the live test, and the last cluster address set
        # logged. `_dummy_diag_addrs` is cleared per test (core/dps_dummy.py,
        # `_engage_dummy`); both are transition-only so a run logs a handful of
        # lines rather than one per hit / per scan.
        self._dummy_diag_addrs: set[int] = set()
        self._dummy_diag_cluster_key: tuple | None = None
        # The dummy test's own radius in metres (0 = no cap), mirroring the
        # `dps_dummy_range` setting. The 1 Hz drain pushes the live setting onto
        # the tracker; everything dummy-side reads it through
        # `dummy_in_range()` (core/dps_dummy.py) so the engagement gate, the
        # auto-show rule and the auto-end can never disagree about which dummies
        # count. The default matches the setting's default, so a tracker that
        # has never seen a settings push still behaves.
        self.dummy_range: float = 5.0
        # How close the player must get to the boss before the accumulated
        # trash run is closed out, in metres (0 = never). Mirrors
        # `dps_boss_clear_range` and is pushed by the same 1 Hz drain. The
        # default matches the setting's default, so a tracker that has never
        # seen a settings push still behaves.
        self.boss_clear_range: float = 30.0
        # FREE TEST: a dummy run that leaves no record. Not archived to Fight
        # History, not written to the day sheet, and - the part that actually
        # matters - not promoted to the personal best or queued as the next
        # run's reference, so a lucky practice run cannot quietly become the
        # number every later comparison is measured against.
        self.dummy_free: bool = False
        # Distance from the player to the boss the scan picked, and 0.0 when
        # no live boss is in the snapshot. Written by the scan beside
        # `dummy_near` so the close-out rule never has to walk the scene again.
        self.boss_near_d: float = 0.0
        # The test's target length in seconds (0 = free-running), mirroring the
        # `dps_dummy_target` setting and pushed by the same 1 Hz drain. A dummy
        # fight is the one encounter nothing in the scene ever concludes, so a
        # measured test needs an end it can reach on its own — otherwise every
        # run is a different length and the DPS figures are not comparable. See
        # `dummy_clock` / `_dummy_target_due` in core/dps_dummy.py; the default
        # matches the setting's, so a tracker that never sees a settings push
        # still behaves.
        self.dummy_target_s: float = 60.0
        # How long a RUNNING test may go with no hit before the tracker ends it
        # itself and re-arms, in seconds (0 = off), mirroring `dps_dummy_rearm`
        # and pushed by the same drain. A dummy keeps the client "in combat" at
        # any range inside its yard, so without this a test ran until the player
        # walked ~30 m out. See core/dps_dummy.py, `_track_dummy_target`.
        self.dummy_rearm_s: float = 5.0
        # Incoming damage (as a % of the test's own) above which a run is
        # flagged as not a clean measurement, mirroring the
        # `dps_dummy_taken_pct` setting the 1 Hz drain pushes. The default
        # matches the setting's, so a tracker that never sees a settings push
        # still behaves — see DUMMY_TAKEN_WARN_PCT in core/dps_dummy.py.
        self.dummy_taken_limit_pct: float = 5.0
        # Latched when a test ends BECAUSE it spent its target length, and
        # cleared by the next engagement (and by reset). The finished session's
        # own duration is stamped from its last event, so it can read a few
        # hundredths under the target that ended it — see DummyClock.reached.
        self.dummy_target_reached: bool = False
        # The dummy a FINISHED test belongs to, plus the two facts a later hit
        # needs to start the next one (see dps_dummy._watch_dummy_reengage):
        # when the run ended, and whether the player ended it BY HAND (Stop
        # Test — never auto-re-armed, only Start Test undoes that one).
        self._dummy_finished_addr: int = 0
        self._dummy_finished_at: float = 0.0
        self._dummy_stopped_by_hand: bool = False
        # Set when a dummy test ends ITSELF (a lull, or the dummy leaving the
        # radius) rather than by the player's Stop: the finished run's board is
        # then handed back to the open-world view instead of holding it
        # (core/dps_dummy.py, `_release_dummy_board`).
        self._dummy_board_released: bool = False
        # A finished dummy test holds the client's own IN COMBAT down until the
        # game drops the flag or a real swing lands (see `suppress_game_combat`):
        # a training yard keeps isInCombat set at any range inside it.
        self._dummy_combat_suppressed: bool = False
        # When the last scan first stopped seeing a dummy inside that radius
        # (monotonic, 0 = a dummy is in range right now). The grace is what ends
        # an abandoned test — see dps_dummy._track_dummy_target.
        self._dummy_missing_at: float = 0.0
        # The remembered test the live one is measured against, and the finished
        # test waiting to be written to disk. Both live here (not in the
        # session) so a new test compares against the PREVIOUS one instead of
        # against its own first second. The settings side is carried by the 1 Hz
        # drain — see core/dps_dummy.py's baseline section.
        self.dummy_baseline: dict | None = None
        self.dummy_baseline_pending: dict | None = None
        # The profile's best-ever dummy test, and the new record waiting to be
        # written. Both follow the baseline's pattern (core owns the rule, the
        # 1 Hz drain carries the value) but with the opposite lifetime: the
        # reference is replaced by every run, this is only ever beaten — see
        # `capture_dummy_reference` in core/dps_dummy.py.
        self.dummy_best: dict | None = None
        self.dummy_best_pending: dict | None = None

        # Foe max-HP snapshots for the real-event path (target_max on rows).
        # Cleared at fight/mode boundaries (reset_foe_max_hp): a recycled foe
        # addr must not inherit the previous occupant's max.
        self._foe_max_hp: dict[int, float] = {}
        # Non-blocking guard serializing update() across the drain worker and
        # the overlay's timer thread (see update()).
        self._update_lock = threading.Lock()
        self._death_prev_hp: dict[int, float] = {}
        # Wipe marker: monotonic timestamp of the last LOCAL death, set by
        # on_local_death BEFORE any early return, and deliberately never
        # cleared by reset() (which on_local_death itself calls). The speedrun
        # overlay baselines this at run start: an advance means the player
        # died mid-run. A direct HP poll can't do this job — on death the
        # hero's attributes object can go unreadable (None reads forever),
        # which is exactly how a wipe used to slip past the timer.
        self.last_local_death_m: float = 0.0
        # After a wipe, suppress stale boss/bridge events until the game has
        # reported a live respawn outside combat. Otherwise the corpse and the
        # carried-over combat flag immediately re-engage the same boss and the
        # HUD timer starts running again after reset().
        self._death_rearm_wait: bool = False
        self._pa_absent_since: float = 0.0   # monotonic start of the current
                                             # local-hero absence streak (0 = present)
        self._pa_last_alive_hp: float = 0.0  # last seen-alive HP (the vanish
                                             # edge's own baseline — the HP
                                             # watcher below overwrites its
                                             # prev map every tick, so vanish
                                             # keeps a private one)
        self._last_events_ts: float = 0.0
        # Wall time of the last LOGGED capture failure, keyed by operation.
        # The drain runs every tick, so a broken reader must not write a log
        # line per tick (see DpsTrackerTick._log_capture_failure).
        self._capture_fail_at: dict[str, float] = {}
        self.auto_reset_zone: bool = True    # user toggle: auto-reset meter on rift/dungeon entry

        # Character change detection state
        self._last_local_hero_addr: int | None = None
        self._last_local_hero_name: str | None = None
        self._hero_was_absent: bool = False

    # --- damage source (model-owned manager) ------------------------------#
    @property
    def source(self):
        """The model's DamageSourceManager (or None when unattached/tests)."""
        m = self.model
        return getattr(m, "damage", None) if m is not None else None

    def session_for(self, segment: str = "auto") -> CombatSession:
        """The session a display surface should show for ``segment``.

        Segment selection belongs to the CONSUMER, not to the tracker: the
        Combat & DPS page's segment picker and the Top DPS overlay's own view
        must not move each other's numbers. The page therefore asks for its
        segment through this method instead of writing ``segment_view``.

        ``segment_view`` stays the tracker's shared default (what the overlay
        shows): "auto" picks the fight that is actually running.
        """
        if segment == "boss":
            return self.boss_session
        if segment == "boss_adds":
            return self.boss_adds_session
        if segment == "trash":
            return self.trash_session
        if segment == "dummy":
            return self.dummy_session
        if segment == "overall":
            return self.overall_session

        # "auto" - and anything unrecognised, including the page's
        # "past_fights" (which is a list view, not a segment).
        # Boss must be checked BEFORE dummy: stale dummy_session.group_damage
        # from a previous fight would shadow the live boss otherwise.
        if self.in_boss_fight:
            # The fight is live: show the boss board — unless every hit so
            # far landed on adds. A latched boss flag with an empty boss
            # session (trash wave before the pull) must not blank BOTH
            # surfaces; live 2026-09-19 dungeon run archived 6341 damage
            # under Boss Adds while the meter pinned the empty boss view
            # and Top DPS + the Combat page showed nothing until a reset.
            if self.boss_session.group_damage > 0:
                return self.boss_session
            if self.boss_adds_session.group_damage > 0:
                return self.boss_adds_session
            return self.boss_session
        if self.dummy_owns_board():
            return self.dummy_session
        if self.boss_session.group_damage > 0:
            return self.boss_session
        if self.trash_session.group_damage > 0:
            return self.trash_session
        return self.overall_session

    @property
    def session(self) -> CombatSession:
        """The shared default view. Consumers with their own picker use
        :meth:`session_for` so they can't retarget this one."""
        return self.session_for(self.segment_view)


    # --- fight-history persistence (opt-in) -------------------------------#
    def archive_current_encounter(self, reason: str = "Encounter Finished",
                                     boss_name: str = ""):
        return self._history_component.archive_current_encounter(reason, boss_name)

    def set_history_path(self, path: str | Path | None) -> None:
        return self._history_component.set_path(path)

    def set_history_dir(self, path: str | Path | None) -> None:
        return self._history_component.set_dir(path)

    def load_history(self) -> None:
        return self._history_component.load()




    # --- day/kind fight-history files ------------------------------------#






    # History implementation lives in DpsTrackerHistory. These compatibility
    # names intentionally remain on the facade for existing callers.



    def _resolve_history_file(self) -> Path | None:
        return self._history_component.resolve_file()



    def _persist_history(self) -> None:
        return self._history_component.persist()

    def prune_history_older_than(self, days: float,
                                 directory=None) -> dict:
        """Delete archived fights older than `days` from the log dir.

        The user-triggered "Clear Old Logs" (Settings > DPS keeps the window in
        `dps_log_keep_days`, 0 = keep everything). Lives here so the UI has one
        call and the rule stays with the layout it deletes.
        """
        return self._history_component.prune_older_than(days, directory)

    def _history_load(self) -> None:
        return self._history_component.load()

    def on_local_death(self, name: str = "You") -> None:
        return self.lifecycle.on_local_death(name)

    def _watch_hero_deaths(self, pa, heroes, hero_hp_now):
        return self.lifecycle.watch_hero_deaths(pa, heroes, hero_hp_now)

    def _watch_local_vanish(self, pa, hero_hp_now, in_instance_now, now_wall):
        return self.lifecycle.watch_local_vanish(pa, hero_hp_now, in_instance_now, now_wall)

    def _end_boss_fight(self, reason: str):
        return self.lifecycle.end_boss_fight(reason)

    def _end_dummy_fight(self, reason: str):
        return self.lifecycle.end_dummy_fight(reason)

    def _fold_party_row(self, old_name: str, owner_name: str, pet_skills: bool = False):
        return self.attribution._fold_party_row(old_name, owner_name, pet_skills)

    def _map_pet_source(self, src, pa, heroes, now):
        return self.attribution._map_pet_source(src, pa, heroes, now)

    def _own_proc_source(self, src, skill_raw, pa):
        return self.attribution._own_proc_source(src, skill_raw, pa)

    def reset(self, reason: str = "Manual Reset"):
        # Archive before wipe if there was data
        if self.overall_session.group_damage > 0:
            self.archive_current_encounter(reason)

        self.overall_session.reset()
        self.trash_session.reset()
        self.boss_session.reset()
        self.boss_adds_session.reset()
        # A test the player Reset away from was still a test: keep it as the next
        # one's reference, the same way Stop Test does. Captured before the wipe
        # below, and the drain writes it (core/dps_dummy.py owns the rule).
        if self.in_dummy_fight and self.dummy_session.group_damage > 0.0:
            self.capture_dummy_reference()
        self.dummy_session.reset()
        self.in_boss_fight = False
        self.in_dummy_fight = False
        # A fresh meter is an armed meter: Reset means "start over", so a test
        # stopped with the meter's Stop Test button starts again here rather than
        # leaving the tracker permanently deaf to the dummy in front of it
        # (core/dps_dummy.py owns the latch and its rules).
        self.dummy_test_armed = True
        self._hero_name_cache.clear()
        self._hero_pointers.clear()
        self._foe_names.clear()
        self._foe_raw_ids.clear()
        self.dummy_near = None
        self.dummy_cluster = []
        self._dummy_diag_addrs = set()
        self._dummy_diag_cluster_key = None
        self._dummy_missing_at = 0.0
        self.dummy_target_reached = False      # reset clears the finished run's latch
        self._dummy_finished_addr = 0
        self._dummy_finished_at = 0.0
        self._dummy_stopped_by_hand = False
        self._dummy_board_released = False
        self._dummy_combat_suppressed = False
        self._boss_kill_pending = 0.0
        self._boss_last_seen = 0.0
        self._boss_missing_at = 0.0
        self._ended_boss_addr = None
        self._ended_boss_deadline = 0.0
        self._declared_boss = None
        self.combat_state = None
        self._combat_id = None
        self._combat_was_in = None
        self._game_combat_end_at = 0.0
        self._combat_poll_at = 0.0
        self._combat_start = 0.0
        self._combat_wall_start_at = 0.0
        self._pet_owners.clear()
        self._pet_last_seen.clear()
        self._owned_proc_sources.clear()
        self._fallback_rows.clear()
        self._proxy_unresolved_logged.clear()
        self._pet_probe_at.clear()
        self._party_retry_at.clear()
        self._roster_ts = 0.0    # re-seed the st.Group roster on the next tick
        self._last_roster_key = None   # ...and log the new group fresh
        self.last_roster = None
        self._hero_classes.clear()
        self._foe_max_hp.clear()
        self._death_prev_hp = {}
        self._pa_absent_since = 0.0
        self._pa_last_alive_hp = 0.0
        self._ow_in_combat_prev = None
        self._last_events_ts = 0.0
        self.last_hero_status = {}

    def reset_foe_max_hp(self, reason: str = "fight boundary") -> None:
        """Drop cached foe max-HP snapshots (fight/mode boundary).

        Called when an encounter ends (boss defeated, trash lull, boss/dummy
        engage), on capture-mode switch and on attach: snapshots recorded
        under the old fight/source are stale for the next one.
        """
        self._foe_max_hp.clear()
        if callable(self.log_line):
            try:
                self.log_line(f"Top DPS: foe max-HP reset ({reason})")
            except Exception:
                pass

    def _reader_rehunt(self, why: str) -> None:
        """Ask the capture source to re-derive its scan cluster immediately.

        Zone changes relocate the display pool; without the forced re-hunt the
        memory reader can spend up to its wide-sweep rate gate (120 s) polling
        pages that no longer hold displays. No-op for bridge modes and fakes
        that don't implement it.
        """
        src = self.source
        if src is None:
            return
        fn = getattr(src, "force_wide_rehunt", None)
        if callable(fn):
            try:
                fn(why)
            except Exception:
                pass
        try:
            src.recalibrate()
        except Exception:
            pass

    # --- name resolution --------------------------------------------------#
    def _match_me(self, name: str, pa: int) -> str | None:
        """Resolved ``X (You)`` when `name` is the local hero's own name.

        The bridge resolves real caster names but its isMe record can be
        stale (player offsets drift), so your own hits arrive flagged
        is_me=False under your bare name — and the solo filter then hides
        the whole parse. Names are unique per server, so an exact match
        (with or without the " (You)" suffix) is unambiguously you.
        """
        if not name or not pa:
            return None
        try:
            me = self._resolve_hero_name(pa, True)
        except Exception:
            return None
        if not me:
            return None
        if same_player_name(name, me):
            return me
        return None

    def _name_is_known_other(self, name: str, pa: int) -> bool:
        """True when `name` is a player this tracker has POSITIVELY
        identified as somebody other than the local player.

        The evidence is whatever the companion already read off the game: the
        live/stale ``st.Group`` roster, every hero a real name was put on
        (``_hero_pointers`` / ``_hero_name_cache``), and any row already
        recorded under that name that is not a "me" row. Deliberately never a
        guess: an unknown or placeholder name answers False, so callers can
        treat True as proof and keep the old behaviour everywhere else."""
        bare = (name or "").strip()
        if not bare or bare in _GENERIC_PLAYER_LABELS or bare.startswith("Party_"):
            return False
        if not pa:
            return False
        try:
            if self._match_me(bare, pa) is not None:
                return False          # that IS us (any spelling)
        except Exception:
            return False
        roster = None
        try:
            roster = _read_roster(self.model)
        except Exception:
            roster = None
        if roster is None:
            # group_roster() answers None while its rate-limited live decode
            # refreshes; _seed_group_roster cached the last real one.
            roster = getattr(self, "last_roster", None)
        for mem in list(getattr(roster, "members", []) or []):
            if getattr(mem, "is_me", False):
                continue
            if same_player_name(getattr(mem, "name", "") or "", bare):
                return True
        for entry in self._hero_pointers.values():
            if not entry:
                continue
            n, me = entry[0], entry[1]
            if me or not n or str(n).startswith("Party_"):
                continue
            if same_player_name(n, bare):
                return True
        for n in self._hero_name_cache.values():
            if n and not str(n).startswith("Party_") and same_player_name(n, bare):
                return True
        for sess in (self.overall_session, self.trash_session, self.boss_session,
                     self.boss_adds_session, self.dummy_session):
            try:
                row = (getattr(sess, "players", None) or {}).get(bare)
            except Exception:
                row = None
            if row is not None and not getattr(row, "is_me", False):
                return True
        return False

    def _flag_loses_to_a_known_ally(self, ev, raw_caster: str, pa: int) -> bool:
        """True when an event's `me` flag must not decide the caster: the wire
        carries the real name of a player we know is NOT us.

        The bridge answers "is this caster the local player?" from one byte it
        reads off a pointer chain, and that record has drifted before -- the
        night it did, the injector flagged EVERY hero as isMe and the meter,
        the page, the rail and the roster all agreed on the same stranger
        (`dps_data.own_row`). A character name carries no such risk: names are
        unique per server and the party is known from the st.Group roster, so
        a name identified as another player is stronger evidence than the
        byte. (Live 2026-10-01, a rift party: the meter showed every pull as
        the local row alone; the archive that stays behind holds the other
        shape of the same leak -- `Sirux is_me=True` beside `Kohan (You)`.)

        Never fires for your own name in any spelling, and never for a name we
        cannot place: an unflagged stranger stays the flag's to decide."""
        if not getattr(ev, "is_me", False):
            return False
        return self._name_is_known_other(raw_caster, pa)

    def _resolve_hero_name(self, hero_addr: int, is_me: bool) -> str:
        if is_me:
            try:
                name = str(self.model.player_name(hero_addr) or "").strip()
                if name:
                    return (name if same_player_name(name, "You")
                            else f"{name} (You)")
            except Exception:
                pass
            # The live name read came back empty for this tick. Returning
            # the bare "You" here minted a SECOND local row whenever the
            # pointer read flaked - the scan registers the row it is given
            # (`_scan_heroes`), so a flaky read mid-run left "Me (You)" and
            # "You" both flagged `is_me`. `own_row` picks whichever was
            # inserted first, so the player's own hits kept landing on the
            # row the meter does NOT highlight while the named party kept
            # ticking (live 2026-10-03, a rift's trash pull: "my dmg stopped
            # being recorded, every other player was still working"). Reuse
            # the last name the read DID succeed with - the same one the
            # scene scan already registered the row under.
            last = getattr(self, "_last_local_hero_name", None)
            if last:
                return (last if same_player_name(last, "You")
                        else f"{last} (You)")
            return "You"

        cached = self._hero_name_cache.get(hero_addr)
        if cached and not cached.startswith("Party_"):
            return cached

        name = None
        try:
            name = self.model.player_name(hero_addr)
            if not name:
                p = self.model._hero_player_ptr(hero_addr)
                if p:
                    name = self.model._player_name_at(p)
        except Exception:
            pass

        if not name:
            name = cached or f"Party_{hero_addr & 0xFFFF:04X}"
        elif cached and cached.startswith("Party_") and name != cached:
            # Fold the Party_XXXX row into the real name (merge, not clobber).
            self._fold_party_row(cached, name)
            self._fallback_rows.pop(hero_addr, None)

        self._hero_name_cache[hero_addr] = name
        return name

    def _is_resolved_player(self, addr: int) -> bool:
        """True when `addr` already maps to a real (non-placeholder) name.

        Used to keep the owned-proc re-credit off a source we have positively
        identified as a player: a placeholder (``Party_XXXX``) means
        unidentified, a real name means the source is not ours to claim. Scene
        heroes land in `_hero_pointers` on every scene scan, so a scanned ally
        is covered here.
        """
        if not addr:
            return False
        pointers = self._hero_pointers.get(addr)
        if pointers and pointers[0] and not str(pointers[0]).startswith("Party_"):
            return True
        cached = self._hero_name_cache.get(addr)
        return bool(cached) and not str(cached).startswith("Party_")

    def _no_known_rivals(self) -> bool:
        """No real (non-Party_) player row has ever resolved this session."""
        for _addr, (n, me) in self._hero_pointers.items():
            if me or not n or n.startswith("Party_"):
                continue
            return False
        return True

    def _no_other_player(self, pa: int,
                         heroes: dict[int, tuple[str, bool, tuple[float, float, float], str]]
                         ) -> bool:
        """True when the evidence says no OTHER real player is involved, so an
        unresolvable source can only be the local player's own proc/status
        damage (the floaty's serverSource is the proc object, not a hero).

        Deliberately NOT ``solo_status()``: that hardwires False inside a
        dungeon/rift (the "always show everyone" DISPLAY rule) without ever
        reading the roster, which left the mint guard dead in instances —
        live 2026-09-18, solo against King Ratsar, the user's own hits minted
        Party_3258/9280/A0B8 rows into the fight archive. Two solo evidences:

        - the game DECODED an empty roster (ungrouped, anywhere), or
        - inside an instance with an unreadable roster: the scene scan is
          instance-wide there, so no other hero on scan + no real player ever
          resolved means nobody else is in this dungeon. Off-instance an
          unreadable roster stays False — an out-of-range ally may exist and
          must keep a fold-able Party_ row (the roster-shredding hazard).

        The scene scan (someone else's hero on scene now) and the cumulative
        check (a real player name resolved earlier) are backstops in every
        branch: a genuine party member always leaves one of those behind; a
        proc object leaves none."""
        m = self.model
        roster = _read_roster(m) if m is not None else None
        # group_roster() can briefly return None while its rate-limited live
        # read refreshes, even though _seed_group_roster already recorded the
        # authoritative solo snapshot for this tick/session.
        if roster is None:
            roster = getattr(self, "last_roster", None)
        if roster is not None:
            members = list(getattr(roster, "members", []) or [])
            if [mm for mm in members if not getattr(mm, "is_me", False)]:
                return False
            # An explicitly decoded empty st.Group is authoritative. Scene
            # scans can contain non-player Hero-like objects and stale cached
            # pointers; treating those as party evidence is what remints
            # Party_XXXX rows for a solo player's proc/status damage.
            return True
        # A scene Hero is rival evidence only after it resolves to a real
        # player. Placeholder Hero-like objects must not turn a proxy's generic
        # caster into a fabricated Party_XXXX row for a solo player.
        if pa and any(
                a != pa and n and not str(n).startswith("Party_")
                for a, (n, me, *_rest) in heroes.items() if not me):
            return False
        if not _in_instance(m):
            return False
        return self._no_known_rivals()

    def _credit_nameless_self(self, pa: int, flagged_me: bool) -> str | None:
        """The local row's name for a hit the bridge flagged as ours but could
        not name — or None when the flag is all there is to go on and the
        evidence contradicts it.

        The asymmetry behind live 2026-10-03 (a rift's trash pull: "my dmg
        stopped being recorded, every other player was still working"): an
        ally's hit arrives NAMED, so it resolves by name no matter what else
        is broken, while the local player's own hits arrive flagged `is_me`
        with the bridge's placeholder name and often no source object either.
        That path ran into `_resolve_caster`'s no-address rule, which refuses
        to credit the local row unless the scene proves you are alone, and
        then into the drop — so your parse vanished while the party's did not.

        `_resolve_caster` must keep refusing that case for OTHER players: the
        2026-10-01 leak had the injector flag every hero as isMe and folding
        on the flag alone put the whole party's parse on the local row. That
        leak is excluded here by construction, twice over: the caller clears
        `flagged_me` when the wire name is a known rival
        (`_flag_loses_to_a_known_ally`), and a named caster never reaches
        this helper at all. What is left is a hit nothing on the wire
        contradicts. Dropping it loses real damage and explains nothing;
        crediting the local row is the only answer that can be right.
        """
        if not flagged_me:
            return None
        try:
            name = self._resolve_hero_name(pa, True) if pa else ""
        except Exception:
            name = ""
        if name and name != "You":
            return name
        # The live name read came back empty for this tick - the same flaky
        # pointer read that made the event nameless in the first place. The
        # scene scan names the local row from the last name it DID read
        # (`_last_local_hero_name`), so falling back to the bare "You" here
        # mints a SECOND `is_me` row beside the scan's real one. Every surface
        # that answers "which row is mine" (`dps_data.own_row`) then picks
        # whichever row was inserted first, and the hit the player just landed
        # vanishes from the row the meter highlights while the named party
        # keeps ticking - the exact "my damage stopped being recorded" shape.
        # Reuse the scan's own name so there is only ever one local row.
        last = getattr(self, "_last_local_hero_name", None)
        if last:
            return last if same_player_name(last, "You") else f"{last} (You)"
        return name or "You"

    def _roster_decoded_solo(self) -> bool:
        """True when the game DECODED a roster and it holds nobody but you.

        Different from ``_no_other_player``, which also answers True for the
        weaker "unreadable roster, but nothing here proves anyone else exists"
        case. This is the strong form only: a roster object actually arrived
        and it named no other member, which is the game's own statement that
        you are ungrouped.

        The distinction is load-bearing in an INSTANCE. A rift's scan is
        instance-wide, so other players' heroes resolve to real names and land
        in ``_hero_pointers`` — they are in the same rift, not in your group.
        Treating those strangers as "a resolved ally" declined solo credit for
        the player's own nameless hits, which is the live 2026-10-05 rift
        report (solo, ``[group] 1 member: Bee (you)``, while the bridge log
        named the other rift players): your own procs went to the drop and the
        named strangers kept resolving — "my damage stopped recording, everyone
        else is fine". A decoded solo roster outranks the wide scan; the wide
        scan only outranks the UNREADABLE case (see `_resolve_caster`).
        """
        roster = _read_roster(self.model)
        if roster is None:
            roster = getattr(self, "last_roster", None)
        if roster is None:
            return False
        members = list(getattr(roster, "members", []) or [])
        return not any(not getattr(mem, "is_me", False) for mem in members)

    def _resolve_caster(self, src_addr: int, pa: int,
                        heroes: dict[int, tuple[str, bool, tuple[float, float, float], str]]
                        ) -> tuple[str | None, bool]:
        """(caster_name, is_me) for an event's authoritative source object."""
        if not src_addr:
            # Solo-credit guard: resolve the whole scene, not merely its length.
            # A loading/attach tick can be empty, while a live instance can also
            # contain unresolved Hero-like objects beside the player. Neither is
            # a real rival; a resolved ally still declines solo attribution.
            _alone = (len(heroes) == 1 and pa in heroes)
            _solo_scene = bool(pa and self._no_other_player(pa, heroes))
            if _alone or _solo_scene:
                # The ``known_others`` backstop exists for an UNREADABLE roster,
                # where an off-scene ally may exist and must keep a fold-able
                # Party_ row. It must NOT override a roster that decoded as
                # solo: an instance-wide scan resolves other players' heroes by
                # name (they are in the rift, not in your group), and letting
                # those strangers decline solo credit dropped the player's own
                # nameless hits while the named strangers kept resolving (live
                # 2026-10-05). A decoded solo roster is the game's own statement
                # about group membership, so it wins here.
                known_others = [
                    n for a, (n, _me) in self._hero_pointers.items()
                    if a != pa and n and not n.startswith("Party_")
                ]
                if not known_others or self._roster_decoded_solo():
                    if pa in heroes:
                        return heroes[pa][0], True
                    try:
                        me_name = self._resolve_hero_name(pa, True)
                    except Exception:
                        me_name = ""
                    if me_name:
                        return me_name, True
            return None, False

        # 1. Direct hero lookup
        if src_addr in heroes:
            name, is_me, *_ = heroes[src_addr]
            return name, is_me

        # 2. Local hero check
        if src_addr == pa:
            return self._resolve_hero_name(pa, True), True

        # 3. Dual-pointer cache (skips stale Party_XXXX so retry below can fold it)
        hp = self._hero_pointers.get(src_addr)
        if hp and not hp[0].startswith("Party_"):
            return hp

        # 4. Name cache; stale Party_XXXX retries live resolution on cadence and folds on success.
        cached = self._hero_name_cache.get(src_addr)
        if cached and not cached.startswith("Party_"):
            return cached, False
        if cached is not None:
            if time.time() - self._party_retry_at.get(src_addr, 0.0) < PARTY_RETRY_S:
                return cached, False
        self._party_retry_at[src_addr] = time.time()

        # 5. Live resolution (ent.Hero, st.Player, or unit)
        try:
            name = self.model.player_name(src_addr)
            if name:
                is_me = (src_addr == pa or "(You)" in name)
                party = self._fallback_rows.get(src_addr)
                if party and party != name:
                    self._fold_party_row(party, name)
                    self._fallback_rows.pop(src_addr, None)
                self._hero_name_cache[src_addr] = name
                self._hero_pointers[src_addr] = (name, is_me)
                return name, is_me
        except Exception:
            pass

        # 6. Fallback party tag (remembered so a later fold can find the row)
        # — but NEVER when no other real player is involved: an unresolvable
        # source is the local player's own proc/status damage (shield orbs,
        # weapon bleeds — the floaty's serverSource is the proc object, not
        # the hero), so minting `Party_XXXX` fabricated party members out of
        # the user's own hits (live 2026-09-16: solo at a dummy, overlay
        # showed Party_77F8 + Party_8568 doing the user's DPS; again
        # 2026-09-18 inside King Ratsar as Party_3258/9280/A0B8 — the old
        # `solo_status() is True` guard was dead IN INSTANCES because
        # solo_status short-circuits False there before reading the roster).
        # Same rationale as the src == 0 guard above: nobody else in the
        # fight, the hit is yours.
        if self._no_other_player(pa, heroes):
            me_name = None
            if pa:
                try:
                    me_name = self._resolve_hero_name(pa, True)
                except Exception:
                    me_name = None
            if me_name:
                return me_name, True
        fallback_name = cached or f"Party_{src_addr & 0xFFFF:04X}"
        self._hero_name_cache[src_addr] = fallback_name
        self._hero_pointers[src_addr] = (fallback_name, False)
        self._fallback_rows[src_addr] = fallback_name
        return fallback_name, False

    def _attribute_caster(self, ev, src: int, pa: int,
                          heroes: dict[int, tuple[str, bool, tuple[float, float, float], str]]):
        """Resolve who cast `ev`, and log whatever the proxy could not place.

        The bridge's own flag, the generic-name guard, the address/name
        resolution and the proxy's unresolved-caster bookkeeping all answer one
        question - which row this event is credited to - so they live here
        rather than in both `_events_apply_damage` and `_events_apply_heal`,
        which rebind `self` to this tracker and differ only in how they name a
        target the event never named.

        Returns `(caster, is_me, event)`: `event` is None when the caster
        resolved, else the `EVENT_UNRESOLVED` / `EVENT_DROPPED` sentinel the
        caller returns unchanged (the once-per-target Activity Log line for
        each case is emitted here).
        """
        raw_caster = clean_wire_name(getattr(ev, "source_name", ""))
        # The game labels ungrouped rivals with generic names ("Player",
        # "Hero"); never mint a row under one — fall back to address
        # resolution (real party names, Party_XXXX that folds later, or a
        # drop) exactly like the no-name path. "You"/"Player" flagged is_me
        # still resolves to the local hero's real name below.
        is_me = bool(getattr(ev, "is_me", False))
        if is_me and self._flag_loses_to_a_known_ally(ev, raw_caster, pa):
            # The flag and the name disagree and the name is a player we have
            # identified as somebody else, so the name wins (`DpsTracker.
            # _flag_loses_to_a_known_ally` has the live story). This is the
            # shape that reads as "everyone's damage is me": the injector's
            # isMe record leaks onto allies while the names on the wire stay
            # correct, and folding on the flag alone puts the whole party's
            # parse on the local row.
            is_me = False
        # The bridge's own flag, kept across the resolution below: that call
        # answers (None, False) for a caster it cannot place, which would
        # otherwise erase the one piece of evidence this event DID carry.
        flagged_me = bool(is_me)
        if is_me and (not raw_caster or raw_caster in _GENERIC_PLAYER_LABELS):
            # "me" with no identity on the wire. The flag is a claim, not a
            # name, and this used to mean "credit the local player" - true
            # only when the evidence says nobody else is in the fight. Route
            # it through the same path every other nameless cast takes:
            # `_resolve_caster` credits the local row when that evidence holds
            # (a solo player's own proc damage still lands on them) and the
            # generic-name guard below counts a drop in the Activity Log
            # otherwise, which is the honest answer for a caster the bridge
            # could neither name nor place.
            caster, is_me = self._resolve_caster(src, pa, heroes)
        elif is_me:
            caster = self._resolve_hero_name(pa, True) if pa else None
            if not caster:
                caster = raw_caster if raw_caster and raw_caster not in _GENERIC_PLAYER_LABELS else "You"
        elif raw_caster and raw_caster not in _GENERIC_PLAYER_LABELS:
            # Bridge names you correctly while its isMe record lies (drifting
            # player offsets): claim your own bare name as you, or the solo
            # filter buries your whole parse under a stranger row.
            me_match = self._match_me(raw_caster, pa)
            if me_match is not None:
                caster, is_me = me_match, True
            else:
                caster = raw_caster
        else:
            caster, is_me = self._resolve_caster(src, pa, heroes)
        if raw_caster in _GENERIC_PLAYER_LABELS or not raw_caster:
            if caster is None:
                # A hit the bridge flagged as OURS, with nothing on the wire to
                # contradict it, is credited to the local row rather than
                # dropped (live 2026-10-03: the local player's trash damage
                # vanished while the named party kept resolving). A named rival
                # never reaches here - `_flag_loses_to_a_known_ally` cleared the
                # flag and the name branch above took the caster.
                self_credit = self._credit_nameless_self(pa, flagged_me)
                if self_credit:
                    caster, is_me = self_credit, True
                    self.events._warn_proxy_unresolved(
                        ev, raw_caster, "credited to the local row")
                else:
                    self.events._warn_proxy_unresolved(
                        ev, raw_caster, "event dropped")
                    return None, False, EVENT_UNRESOLVED
            if caster.startswith("Party_"):
                self.events._warn_proxy_unresolved(
                    ev, raw_caster, f"using temporary {caster}")
            else:
                self.events._clear_proxy_unresolved(ev, raw_caster)
        if caster is None:
            return None, False, EVENT_DROPPED
        return caster, is_me, None

    def _hero_name(self, addr: int, pa: int,
                   heroes: dict[int, tuple[str, bool, tuple[float, float, float], str]]
                   ) -> str | None:
        if addr == pa:
            return self._resolve_hero_name(pa, True)
        h = heroes.get(addr)
        if h:
            return h[0]
        cached = self._hero_name_cache.get(addr)
        if cached:
            return cached
        # Off-scene roster heroes live in _hero_pointers before the name cache.
        hp = self._hero_pointers.get(addr)
        if hp is not None and not hp[0].startswith("Party_"):
            return hp[0]
        return None

    def _foe_name(self, addr: int,
                  foes: dict[int, tuple[float, str, bool, float, float, float]]) -> str | None:
        f = foes.get(addr)
        if f:
            return f[1]
        return self._foe_names.get(addr)

    # --- group roster (st.Group) name seeding ----------------------------#
    def _seed_group_roster(self, pa: int) -> None:
        """Pre-seed party names from the game's st.Group roster; Party_XXXX rows fold into real names."""
        m = self.model
        if m is None:
            return
        try:
            roster = m.group_roster()
        except Exception:
            return
        # Cache for the display path (group line + solo check): the UI never
        # polls the game's group object itself.
        self.last_roster = roster

        # Roster-change log: one line per join/leave/leader/solo transition.
        key = _roster_change_key(roster)
        if key != self._last_roster_key:
            self._last_roster_key = key
            line = _roster_line(roster)
            log.info("[group-roster] %s", line)
            if self.log_line is not None:
                try:
                    self.log_line(f"[group] {line}")
                except Exception:
                    pass

        if roster is None or not roster.members:
            return
        me_player = 0
        try:
            me_player = m._hero_player_ptr(pa) or 0
        except Exception:
            pass
        me_name = self._resolve_hero_name(pa, True) if pa else ""
        party_names: list[str] = []
        for mem in roster.members:
            name = (mem.name or "").strip()
            if not name:
                continue
            # The group reader's explicit local flag is authoritative. Some
            # game builds also drift the hero/player pointers, so also match
            # the unique character name; otherwise a stale local roster entry
            # pre-registers bare "Name" beside the real "Name (You)" row.
            if (mem.is_me or mem.hero == pa
                    or (me_player and mem.player == me_player)
                    or same_player_name(name, me_name)):
                continue
            party_names.append(name)
            for addr in (mem.hero, mem.player):
                if not addr:
                    continue
                cached = self._hero_pointers.get(addr)
                if cached is not None and not cached[0].startswith("Party_"):
                    continue
                # Fold this addr's own fallback row into the real name.
                fallback = self._fallback_rows.get(addr)
                if fallback and fallback != name:
                    self._fold_party_row(fallback, name)
                    self._fallback_rows.pop(addr, None)
                elif fallback is None:
                    oldc = self._hero_name_cache.get(addr)
                    if oldc and oldc.startswith("Party_") and oldc != name:
                        self._fold_party_row(oldc, name)
                self._hero_name_cache[addr] = name
                self._hero_pointers[addr] = (name, False)

        # Class the party and self from equipped skill slots / scene entity
        if pa and pa not in self._hero_classes:
            try:
                cls = m.hero_class_of(pa) or m.player_class()
            except Exception:
                cls = None
            if cls:
                self._hero_classes[pa] = cls

        for mem in roster.members:
            addr = getattr(mem, "hero", 0) or 0
            if not addr or addr in self._hero_classes:
                continue
            try:
                cls = m.hero_class_of(addr)
            except Exception:
                cls = None
            if cls:
                self._hero_classes[addr] = cls

        # Dungeon/rift: pre-register the whole party onto the live meter
        # (trash + overall, 0 stats) so the overlay can auto-load every
        # member between pulls instead of only active participants. Idle
        # rows stay invisible in open-world/combat display because the
        # meter's activity filter drops 0-stat players unless asked.
        if _in_instance(m) and party_names:
            names = list(party_names)
            if me_name and me_name not in ("", "You") and me_name not in names:
                names.insert(0, me_name)
            for pname in names:
                is_me = (pname == me_name)
                self.trash_session.register_player(pname, is_me=is_me)
                self.overall_session.register_player(pname, is_me=is_me)

    def _sync_known_classes(self) -> None:
        """Write game-known classes onto session rows (truth replaces skill-guess)."""
        if not self._hero_classes:
            return
        sessions = (self.overall_session, self.trash_session,
                    self.boss_session, self.boss_adds_session)
        pa = getattr(self.model, "player_addr", 0) if self.model else 0
        for addr, cls in list(self._hero_classes.items()):
            hp = self._hero_pointers.get(addr)
            is_me = (hp[1] if hp else False) or (addr == pa)
            name = hp[0] if hp else None
            if not name or name.startswith("Party_"):
                name = self._hero_name_cache.get(addr)
            for sess in sessions:
                if name and not name.startswith("Party_"):
                    p = sess.players.get(name)
                    if p is not None and p.hero_class != cls:
                        p.hero_class = cls
                # If this is the local player, also update any row flagged is_me
                if is_me:
                    for p_cand in sess.players.values():
                        if p_cand.is_me and p_cand.hero_class != cls:
                            p_cand.hero_class = cls

    # --- game combat signals (auxiliary) ---------------------------------#
    def _in_zone_settle(self) -> bool:
        """True for a short window after a zone enter/exit.

        The game keeps reporting isInCombat across a loading screen, and
        combatStartTime can be regenerated on arrival, so neither the fight
        clock nor the armed display may trust the combat fields until the new
        zone settles (live 2026-09-19: the timer started the moment the user
        zoned in).
        """
        if self._zone_edge_at <= 0.0:
            return False
        return (time.time() - self._zone_edge_at) < ZONE_ARM_SETTLE_S

    def armed_fight(self) -> tuple[float, str] | None:
        """Display clock for a fight whose first hit has NOT decoded yet.

        The meter must read as running for the WHOLE fight, so the timer ticks
        from COMBAT ENTRY rather than from the first decoded hit: with Option 3
        the capture can still be blind for the first seconds of a pull (a blind
        rescue is in flight), and the header then sat at 00:00.0 over a fight
        that was already happening. Returns ``(elapsed, name)`` while the game
        reports combat and no session owns the clock, else None.

        No jump when the first hit lands: the session starts from this same
        `_combat_wall_start_at` anchor (see CombatSession.clock_anchor), so the
        number the timer already showed is the number it keeps.
        """
        if self._death_rearm_wait:
            return None
        if self._combat_wall_start_at <= 0.0:
            return None
        if self._in_zone_settle():
            return None                 # zone-in is not a fight
        st = self.combat_state
        if not st or not st.get("in_combat"):
            return None
        if game_combat_settled(st):
            return None         # the game stamped combat_end: it is over
        sess = self.session
        if sess.state in ("COMBAT", "PAUSED") or sess.group_damage > 0:
            return None                 # a session owns the clock now
        return max(0.0, time.time() - self._combat_wall_start_at), \
            (sess.name or "Fight")

    @property
    def game_encounter_elapsed(self) -> float | None:
        """Wall seconds since combatStartTime moved (live encounter timer; None when idle)."""
        st = self.combat_state
        if not st or not st.get("in_combat") or self._combat_wall_start_at <= 0:
            return None
        return max(0.0, time.time() - self._combat_wall_start_at)

    def _poll_combat_state(self, pa: int) -> None:
        """Sample Hero combat fields; AUXILIARY end-confirm only, never starts a fight."""
        now = time.time()
        if now - self._combat_poll_at < GAME_COMBAT_POLL_S:
            return
        self._combat_poll_at = now

        st = None
        m = self.model
        if m is not None and pa:
            try:
                st = m.combat_state()
            except Exception:
                st = None
        self.combat_state = st
        if not st:
            return

        in_combat = bool(st.get("in_combat"))
        cid = st.get("combat_id")
        start = float(st.get("combat_start") or 0.0)

        # Anchor wall time when combatStartTime moves (the game's "fight began" tick).
        if in_combat:
            if start > 0 and (self._combat_start <= 0
                              or abs(start - self._combat_start) > 1e-6):
                self._combat_wall_start_at = now
            if start <= 0:
                self._combat_wall_start_at = 0.0
        else:
            self._combat_wall_start_at = 0.0
        self._combat_start = start

        # Hand the fight clock to the sessions. The game's combat entry is the
        # ONLY signal that predates the first DECODED hit, so it is what keeps
        # capture latency (a blind re-anchor, a throttled tick) out of the
        # timer: the meter starts counting when combat starts, not when the
        # damage finally decodes. Cleared the moment the game says combat is
        # over, so a stale anchor can never seed a later fight.
        anchor = self._combat_wall_start_at if in_combat else 0.0
        if anchor > 0.0 and self._in_zone_settle():
            # A carried-over combat window right after a zone change would
            # backdate the first pull's clock to the zone-in moment.
            anchor = 0.0
        for sess in (self.overall_session, self.trash_session,
                     self.boss_session, self.boss_adds_session,
                     self.dummy_session):
            sess.clock_anchor = anchor

        if self._combat_was_in is True and not in_combat:
            self._game_combat_end_at = now
            log.info("[combat] game declares combat over (last id=%s)",
                     self._combat_id)
        if in_combat:
            if (cid is not None and self._combat_id is not None
                    and cid != self._combat_id):
                # combatId rotated: previous fight ended.
                self._game_combat_end_at = now
                log.info("[combat] combat id rotated %s -> %s "
                         "(previous fight ended)", self._combat_id, cid)
            else:
                # In (a) fight right now; no pending end signal.
                self._game_combat_end_at = 0.0
        self._combat_was_in = in_combat
        if cid is not None:
            self._combat_id = cid

        # A dummy test's own end is the honest combat boundary (see
        # `suppress_game_combat`): a training yard keeps isInCombat set at any
        # range inside it, so the meter read IN COMBAT off a finished test.
        # Hold that down until the game drops the flag or a new test starts.
        if self._dummy_combat_suppressed:
            if not in_combat or self.in_dummy_fight:
                self._dummy_combat_suppressed = False
            else:
                self.combat_state = dict(st, in_combat=False)

    def suppress_game_combat(self) -> None:
        """Report combat over the moment a dummy test ends by itself.

        The client keeps ``isInCombat`` set anywhere inside a training yard (the
        dummy holds the flag until the player is ~30 m out), so the moment the
        tracker concluded the test the meter still read IN COMBAT with the
        finished run under it — the badge and the armed clock both. The test's
        own end is the honest boundary, so both read out-of-combat until the
        game itself drops the flag or a real (non-dummy) swing lands:
        ``_poll_combat_state`` keeps it applied on every poll and
        ``DpsTrackerEvents._events_apply_damage`` clears it.
        """
        self._dummy_combat_suppressed = True
        self._combat_wall_start_at = 0.0
        st = self.combat_state
        if st and st.get("in_combat"):
            self.combat_state = dict(st, in_combat=False)

    # --- capture health ---------------------------------------------------#
    def capture_line(self) -> str:
        """One line saying whether combat events are actually arriving.

        A fight can start with the capture dead - a stale cluster after a zone
        change, an unarmed DLL, a scanner still calibrating - and every surface
        then just shows zeros, silently. This names the mode, the engine's own
        status, its per-poll diagnostics, the event count and the bridge's issue
        (the HLBOOT verdict, when that is why it is not arming), so ONE dungeon
        pull is enough to tell a moved pool from an unarmed bridge.
        """
        src = self.source
        if src is None:
            return "Top DPS: capture unknown (no event source attached)."
        parts = [f"mode={getattr(src, 'mode', '?')}"]
        for label, get in (("status", getattr(src, "status", None)),
                           ("source", getattr(src, "live_source_name", None))):
            if not callable(get):
                continue
            try:
                val = get()
            except Exception:
                continue
            if val:
                parts.append(f"{label}={val}")
        try:
            d = src.diagnostics() or {}
        except Exception:
            d = {}
        if d:
            keys = ("displays", "ok", "no_res", "bad_hdr", "bad_amount")
            parts.append("poll " + " ".join(f"{k}={d.get(k, '-')}" for k in keys))
        try:
            n, _other = src.counts()
            parts.append(f"events={n}")
        except Exception:
            pass
        if self._last_events_ts:
            parts.append(f"last event {time.time() - self._last_events_ts:.0f}s ago")
        else:
            parts.append("no event yet this run")
        try:
            issue = src.bridge_issue()
        except Exception:
            issue = ""
        if issue:
            parts.append(issue)
        return "Top DPS: capture " + " · ".join(parts)

    def _log_capture_if_deaf(self, why: str) -> None:
        """Log the capture state once, at a fight's start, only when no event
        has ever been decoded (a working capture needs no commentary)."""
        if self._last_events_ts or not callable(self.log_line):
            return
        self._log_activity(f"{why} — {self.capture_line()}")










    # --- pet/summon owner attribution ------------------------------------#



    # --- per-event application --------------------------------------------#
    def _confirm_boss_kill(self, ev, now):
        return self.events.confirm_boss_kill(ev, now)

    def _apply_event(self, ev, pa, heroes, foes, now):
        return self.events.apply_event(ev, pa, heroes, foes, now)

    def _apply_damage_taken(self, tgt, amount, pa, heroes, now, **kwargs):
        return self.events.apply_damage_taken(tgt, amount, pa, heroes, now, **kwargs)

    def _apply_heal(self, ev, amount, src, tgt, pa, heroes, now, **kwargs):
        return self.events.apply_heal(ev, amount, src, tgt, pa, heroes, now, **kwargs)

    def _apply_damage(self, ev, amount, src, tgt, pa, heroes, foes, now, **kwargs):
        return self.events.apply_damage(ev, amount, src, tgt, pa, heroes, foes, now, **kwargs)






    @staticmethod
    def _skill_labels(ev: DamageEvent, fallback: tuple[str, str]) -> tuple[str, str, bool]:
        """The hit's skill as (id, display name, tagged-as-pet).

        The id comes back CANONICAL: any `(pet)` the wire appended is stripped,
        and reported separately as the flag. That is the whole point of this
        function.

        Two taggers write "(pet)" into a skill string: the bridge reader
        (`dps_bridge`, onto the id) and the tracker's own pet-owner map
        (`_events_apply_damage`/`_apply_heal`, onto the name). The bridge's tag
        is decided per hit from a live memory read (`bridge_core.h`: is
        `dealer + OFF_FOE_OWNER` readable and a hero instance right now?), so
        the SAME pet swing arrives tagged on one hit and untagged on the next.

        Nothing reconciled them, and every skill bucket is keyed by id. So one
        pet skill minted two rows in the HUD, and `update_stats` hides every row
        whose id is not in this tick's `active_skills` — the pair took turns
        appearing and disappearing at the pet's cadence (reported live
        2026-10-03: "auto expanding skills keep showing then hiding"). Worse, a
        hit that BOTH taggers marked read `... (pet) (pet)`.

        Pet-ness is a property of the skill, not of one hit's read, so it is
        decided once here from both signals and stamped on exactly one field.
        """
        raw = (ev.skill or "").strip()
        tagged = raw.endswith(_PET_SUFFIX)
        sid = raw[:-len(_PET_SUFFIX)].strip() if tagged else raw
        if not sid or sid == "?":
            # Unread skills stay "Unknown", never masquerade as basic attacks.
            return "?", "Unknown", tagged
        name = names.skill_name(sid) or sid
        if name.endswith(_PET_SUFFIX):
            name = name[:-len(_PET_SUFFIX)].strip()
        return sid, name, tagged
