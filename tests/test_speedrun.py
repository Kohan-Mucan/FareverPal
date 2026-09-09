"""Speedrun timer auto-stop / cancel logic (headless).

Guards the leaderboard-integrity bug: leaving the dungeon (boss despawns at full
HP, e.g. going to the main menu) must CANCEL the run, never auto-finish + record.
"""
from farever_companion.core.speedrun import (
    AutoStarter, BossAttachGate, BossTimer, Encounter, GroupWipeWatcher,
    ModeLatch, RearmGate, ZoneEdge, WipeWatcher, kill_entry, kill_entry_with_boss,
    append_kill_log, KILL_LOG_LIMIT, should_auto_start,
    clock_pb_line, delta_text, row_key, merge_history_rows)
from farever_companion.core.speedrun import SpeedrunTimer as T
from farever_companion.data import encounters


def _running():
    t = T()
    t.start()
    return t


def test_start_accepts_overlay_auto_mode():
    t = T()
    t.start("auto")
    assert t.state == t.RUNNING


def test_leaving_dungeon_at_full_hp_is_not_a_kill():
    t = _running()
    assert t.feed_boss("MunsterChuck", True, 21000.0) == t.ALIVE   # entered, boss healthy
    # Main menu / left the dungeon: boss despawns while still at full HP and
    # the instance gate is proven outside.
    assert t.feed_boss("MunsterChuck", False, None, outside=True) == t.LEFT
    assert t.state != t.DONE          # caller cancels; timer must NOT have finished
    assert t.is_kill is False


def test_kill_when_boss_dies_in_scene():
    t = _running()
    t.feed_boss("MunsterChuck", True, 21000.0)
    t.feed_boss("MunsterChuck", True, 4000.0)
    assert t.feed_boss("MunsterChuck", True, 0.0) == t.KILL   # corpse at 0 HP
    assert t.state == t.DONE
    assert t.is_kill is True


def test_kill_when_boss_despawns_near_death():
    t = _running()
    t.feed_boss("MunsterChuck", True, 21000.0)
    t.feed_boss("MunsterChuck", True, 300.0)        # < 5% of max → death's door
    assert t.feed_boss("MunsterChuck", False, None) == t.KILL
    assert t.state == t.DONE and t.is_kill is True


def test_despawn_at_high_hp_is_left_not_kill():
    t = _running()
    t.feed_boss("Cleodora", True, 5000.0)
    t.feed_boss("Cleodora", True, 4000.0)           # still 80% — clearly alive
    assert t.feed_boss("Cleodora", False, None, outside=True) == t.LEFT
    assert t.state != t.DONE and t.is_kill is False


def test_a_healthy_boss_vanishing_inside_or_on_an_unreadable_tick_keeps_the_run(tick_overlay):
    """A healthy boss that vanishes while still inside - or while the scene
    is unreadable for a tick or two - is a scene blip (void-fall / respawn /
    arena unload), not leaving. The RUN must keep timing. Only a sustained
    healthy absence while proven outside aborts."""
    t = _running()
    t.feed_boss("MunsterChuck", True, 21000.0)
    assert t.feed_boss("MunsterChuck", False, None, outside=False) == t.ALIVE
    assert t.state == t.RUNNING
    assert t.feed_boss("MunsterChuck", False, None, outside=None) == t.ALIVE
    assert t.state == t.RUNNING
    # ...and through the overlay (ticks rather than one core call) one or two
    # outside-inside blips during a respawn leave the RUN intact.
    ov, model = tick_overlay()
    model.inside = True
    _tick(ov)
    _fight(model, 100.0)
    _tick(ov, DETECT_EVERY + 1)
    _fight(model, 80.0)
    _tick(ov)                      # BOSS armed
    assert ov.timer.state == ov.timer.RUNNING
    assert ov.boss_timer.state == ov.boss_timer.RUNNING
    run_start = ov.timer._t0
    ov.hide()
    model.inside = False           # void-fall blip
    model.states = []               # arena's boss temporarily absent
    _tick(ov, 2)
    model.inside = True
    _fight(model, 100.0)
    _tick(ov, 2)
    ov.show()
    _tick(ov)
    assert ov.timer.state == ov.timer.RUNNING
    assert ov.timer._t0 == run_start


def test_main_menu_sustained_healthy_absence_does_abort(tick_overlay):
    """Unknown scene after quitting to the main menu - sustained healthy boss
    absence still aborts (the proven-outside path stays too), while a
    transient blip does not."""
    t = _running()
    t.feed_boss("MunsterChuck", True, 21000.0)
    for _ in range(t.GONE_TICKS - 1):
        assert t.feed_boss("MunsterChuck", False, None, outside=None) == t.ALIVE
        assert t.state == t.RUNNING
    assert t.feed_boss("MunsterChuck", False, None, outside=None) == t.LEFT


def test_manual_stop_is_not_a_kill():
    t = _running()
    t.feed_boss("MunsterChuck", True, 21000.0)
    t.stop()                                         # not a kill
    assert t.state == t.DONE
    assert t.is_kill is False                        # so it won't set PB / kill log


def test_boss_never_seen_then_vanishes_does_not_finish():
    t = _running()
    # boss read fails (None) before we ever confirm it alive, then "vanishes"
    assert t.feed_boss(None, False, None) == t.ALIVE
    assert t.state == t.RUNNING


# --- AutoStarter: consistent "starts on real movement" -----------------------

def _settle(a, base=(0.0, 0.0, 0.0)):
    """Feed enough stationary ticks at `base` to establish the baseline."""
    for _ in range(AutoStarter.SETTLE_TICKS + 1):
        assert a.feed(True, base) is False


def test_autostart_never_arms_outside_dungeon():
    a = AutoStarter()
    for _ in range(20):
        assert a.feed(False, (1000.0 * _, 0.0, 0.0)) is False   # walking in town


def test_autostart_fires_only_after_real_movement_from_settled_spawn():
    a = AutoStarter()
    _settle(a, (10.0, 10.0, 0.0))
    # tiny jitter at spawn must NOT start
    assert a.feed(True, (10.2, 10.0, 0.0)) is False
    # deliberate walk away → start, exactly once
    assert a.feed(True, (13.0, 10.0, 0.0)) is True
    assert a.feed(True, (16.0, 10.0, 0.0)) is False   # already started; doesn't re-fire


def test_autostart_ignores_teleport_in_then_starts_on_walk():
    a = AutoStarter()
    # arrive: first tick at the load origin, then a big teleport jump to the spawn
    assert a.feed(True, (0.0, 0.0, 0.0)) is False
    assert a.feed(True, (500.0, 500.0, 0.0)) is False   # teleport → no baseline yet
    # settle at the real spawn, then walk
    _settle(a, (500.0, 500.0, 0.0))
    assert a.feed(True, (504.0, 500.0, 0.0)) is True


def test_autostart_does_not_fire_on_load_jitter_alone():
    a = AutoStarter()
    # continuous small drift that never settles (loading) must not baseline/start
    fired = any(a.feed(True, (float(i), 0.0, 0.0)) for i in range(1, 30))
    assert fired is False


def test_autostart_ignores_teleport_in_landing_drift():
    # Real trace: after the teleport into a dungeon the player spawns above the
    # floor and SETTLES DOWN ~2.2 units in Z over ~1s (X/Y fixed). Each tick of
    # that drop is a tiny step that the old per-tick test mistook for 'still', so
    # it baselined mid-fall and the remaining settle tripped the move threshold —
    # a false start with no real input.
    a = AutoStarter()
    a.feed(True, (2078.0, 162.0, -292.0))        # out in the world
    a.feed(True, (-98.8, 347.8, 17.4))           # teleport into the dungeon (>50)
    z = 17.4
    fired = False
    for _ in range(26):                          # the landing drop (~0.085/tick)
        z -= 0.085
        fired = fired or a.feed(True, (-98.8, 347.8, z))
    assert fired is False                        # must NOT auto-start during the landing
    # genuinely at rest now → settle, then a deliberate walk starts it
    for _ in range(AutoStarter.SETTLE_TICKS + 5):
        a.feed(True, (-98.8, 347.8, z))
    assert a.feed(True, (-95.0, 347.8, z)) is True


def test_autostart_disarms_when_leaving_dungeon():
    a = AutoStarter()
    _settle(a, (10.0, 10.0, 0.0))
    assert a.feed(False, (10.0, 10.0, 0.0)) is False   # left dungeon → reset
    # back in a new dungeon: must re-settle before it can start again
    assert a.feed(True, (10.0, 10.0, 0.0)) is False


# --- ModeLatch: difficulty stays correct through the boss dying --------------

def test_modelatch_prefers_fresh_auto():
    m = ModeLatch()
    m.observe("hard", "auto")
    assert m.resolve("normal", "auto") == ("normal", "auto")   # fresh auto wins


def test_modelatch_falls_back_to_latched_auto_when_finish_read_fails():
    m = ModeLatch()
    m.observe("hard", "auto")          # read reliably mid-fight
    # at finish the boss is dead → fresh read is the manual fallback
    assert m.resolve("normal", "manual") == ("hard", "auto")


def test_modelatch_uses_manual_when_never_auto_detected():
    m = ModeLatch()
    assert m.resolve("normal", "manual") == ("normal", "manual")


def test_modelatch_does_not_latch_manual_or_unknown():
    m = ModeLatch()
    m.observe("hard", "manual")        # manual reads must not latch
    m.observe(None, "auto")            # unreadable
    assert m.resolve("normal", "manual") == ("normal", "manual")


def test_modelatch_reset_clears_previous_run():
    m = ModeLatch()
    m.observe("hard", "auto")
    m.reset()                          # new run
    assert m.resolve("normal", "manual") == ("normal", "manual")


# --- the run has no manual start/stop any more ------------------------------

def test_the_timer_has_no_manual_start_or_stop():
    """The app binds no keys and the HUD has no start control: both edges of a run
    come from the dungeon (first boss damage, then the kill). This pins that no
    hand-driven entry point creeps back without the reasoning being revisited —
    every run must have a trusted auto-detected context to be recorded."""
    t = T()
    assert not hasattr(t, "toggle")
    assert not hasattr(t, "source")


# --- BossAttachGate: attach-only BOSS needs a trustworthy baseline ----------

def test_boss_attach_gate_accepts_verified_full_health():
    gate = BossAttachGate()
    assert gate.feed(100.0, 100.0) is True
    assert gate.eligible is True

    gate.reset()
    assert gate.feed(90.0, 100.0) is True
    assert gate.state == gate.DAMAGED
    assert gate.eligible is False
    # A damaged verdict is latched; later lower readings cannot re-allow it.
    assert gate.feed(100.0, 100.0) is False
    assert gate.eligible is False


def test_boss_attach_gate_fails_closed_when_max_health_is_unknown():
    gate = BossAttachGate()
    assert gate.feed(100.0, None) is True
    assert gate.state == gate.UNKNOWN
    assert gate.eligible is False
    # A later reflected max can resolve an earlier unreadable tick.
    assert gate.feed(100.0, 100.0) is True
    assert gate.eligible is True


def test_boss_attach_gate_accepts_explicit_precombat_without_max_health():
    gate = BossAttachGate()
    assert gate.feed(23322.0, None, in_combat=False) is True
    assert gate.state == gate.PRECOMBAT
    assert gate.eligible is True
    # The pre-combat decision is latched; a later damage sample cannot
    # silently turn this into a second interpretation of the attach point.
    assert gate.feed(23000.0, None, in_combat=True) is False
    assert gate.eligible is True


def test_boss_attach_gate_rejects_active_combat_without_max_health():
    gate = BossAttachGate()
    assert gate.feed(23322.0, None, in_combat=True) is True
    assert gate.state == gate.ACTIVE
    assert gate.eligible is False
    assert gate.feed(23322.0, None, in_combat=False) is False
    assert gate.eligible is False


# --- BossTimer: the boss-only split ------------------------------------------

def test_bosstimer_arm_then_stop_freezes():
    b = BossTimer()
    assert b.state == b.READY and b.elapsed() == 0.0
    b.arm()
    assert b.state == b.RUNNING
    b.arm()                            # idempotent: already running
    assert b.state == b.RUNNING
    b.stop()
    assert b.state == b.DONE and b.last is not None and b.last >= 0.0
    assert b.elapsed() == b.last       # frozen after stop


def test_bosstimer_reset_clears():
    b = BossTimer()
    b.arm()
    b.stop()
    b.reset()
    assert b.state == b.READY and b.last is None and b.elapsed() == 0.0


# --- Encounter: first-hit engagement + kill-boss proxy -----------------------

def test_encounter_engages_on_first_hit():
    e = Encounter({"MunsterChuck"}, "MunsterChuck")
    assert e.feed([("MunsterChuck", True, 21000.0)]) == ("MunsterChuck", True, 21000.0)
    assert e.engaged is False                      # at full HP, no hit yet
    e.feed([("MunsterChuck", True, 20000.0)])      # took damage
    assert e.engaged is True


def test_encounter_absent_boss_reports_not_present():
    e = Encounter({"MunsterChuck"}, "MunsterChuck")
    assert e.feed([]) == ("MunsterChuck", False, None)
    assert e.engaged is False


def test_encounter_confirms_corpse_and_near_death_despawn():
    e = Encounter({"MunsterChuck"}, "MunsterChuck")
    e.feed([("MunsterChuck", True, 100.0)])
    e.feed([("MunsterChuck", True, 0.0)])
    assert e.kill_confirmed is True

    e = Encounter({"MunsterChuck"}, "MunsterChuck")
    e.feed([("MunsterChuck", True, 100.0)])
    e.feed([("MunsterChuck", True, 4.0)])
    e.feed([])
    assert e.kill_confirmed is True


def test_encounter_any_member_hit_engages():
    e = Encounter({"Add", "Cleodora"}, "Cleodora")
    e.feed([("Add", True, 500.0), ("Cleodora", True, 9000.0)])
    assert e.engaged is False
    e.feed([("Add", True, 450.0), ("Cleodora", True, 9000.0)])   # an add took damage
    assert e.engaged is True


def test_encounter_engage_any_arms_on_any_enemy():
    # Trashless room: members lists only the kill boss, but engage_any means a hit
    # to ANY fed enemy (a worker bee) starts the split.
    e = Encounter({"Cleodora"}, "Cleodora", engage_any=True)
    assert e.feed([("Worker", True, 100.0), ("Cleodora", True, 9000.0)]) == ("Cleodora", True, 9000.0)
    assert e.engaged is False
    e.feed([("Worker", True, 80.0), ("Cleodora", True, 9000.0)])   # hit a worker, not the boss
    assert e.engaged is True


def test_encounter_no_engage_any_ignores_nonmembers():
    # Default (boss + trash): hitting a trash mob must NOT start the boss split.
    e = Encounter({"Boss"}, "Boss")
    e.feed([("Trash", True, 100.0), ("Boss", True, 9000.0)])
    e.feed([("Trash", True, 10.0), ("Boss", True, 9000.0)])        # trash took damage
    assert e.engaged is False


def test_encounter_reset_clears_engagement():
    e = Encounter({"MunsterChuck"}, "MunsterChuck")
    e.feed([("MunsterChuck", True, 100.0)])
    e.feed([("MunsterChuck", True, 10.0)])
    assert e.engaged is True
    e.reset()
    assert e.engaged is False


# --- encounter definitions: default single-boss, multi-boss stubbed ----------

def test_resolve_single_boss_default():
    assert encounters.resolve("MunsterChuck") == ({"MunsterChuck"}, "MunsterChuck", False)


def test_resolve_none_is_empty():
    assert encounters.resolve(None) == (set(), None, False)


def test_resolve_cleodora_is_engage_any():
    # The trashless Honeyzabeth room: kill on Cleodora, fight arms on any first hit.
    members, kill_id, engage_any = encounters.resolve("Cleodora")
    assert kill_id == "Cleodora"
    assert members == {"Cleodora"}
    assert engage_any is True


# --- RearmGate: the finished result must stay readable after the kill ---------

def test_rearm_holds_through_boss_despawn():
    # The bug: the boss despawns on death; that must NOT re-arm (wipe) the result.
    # Standing still in the cleared dungeon keeps the times on screen.
    g = RearmGate()
    for _ in range(200):                                  # ~10s of looting
        assert g.feed((10.0, 10.0, 0.0)) is False


def test_rearm_small_movement_keeps_result():
    g = RearmGate()
    for i in range(100):                                  # walking around the room
        assert g.feed((10.0 + i * 0.4, 10.0, 0.0)) is False


def test_rearm_on_exit_teleport():
    g = RearmGate()
    assert g.feed((10.0, 10.0, 0.0)) is False
    assert g.feed((900.0, 900.0, 0.0)) is True            # ported out -> re-arm now


def test_rearm_on_load_screen():
    g = RearmGate()
    g.feed((10.0, 10.0, 0.0))
    for _ in range(RearmGate.NONE_TICKS - 1):             # position unreadable…
        assert g.feed(None) is False
    assert g.feed(None) is True                           # …long enough = left


def test_rearm_transient_read_failure_is_not_a_leave():
    g = RearmGate()
    g.feed((10.0, 10.0, 0.0))
    for _ in range(5):                                    # brief read hiccup
        assert g.feed(None) is False
    assert g.feed((10.0, 10.0, 0.0)) is False             # recovered -> still held


def test_rearm_grace_expiry():
    g = RearmGate()
    fired = [g.feed((10.0, 10.0, 0.0)) for _ in range(RearmGate.GRACE_TICKS)]
    assert fired[-1] is True and not any(fired[:-1])      # exactly at the grace


# --- ZoneEdge: the full run starts on zone-in -------------------------------

def test_zone_edge_fires_once_on_enter():
    z = ZoneEdge()
    assert z.feed(False) is False
    assert z.feed(False) is False
    assert z.feed(True) is True     # zoned in -> start, exactly once
    assert z.feed(True) is False
    assert z.feed(False) is False   # left
    assert z.feed(True) is True     # next dungeon -> start again


def test_zone_edge_arms_without_firing_when_already_inside():
    # Attaching inside only arms the edge; a real exit/re-entry starts RUN.
    z = ZoneEdge()
    assert z.feed(True) is False
    assert z.feed(True) is False


def test_zone_edge_prime_inside_waits_for_a_real_reentry():
    z = ZoneEdge()
    z.feed(False)
    assert z.feed(True) is True
    z.prime_inside()
    assert z.feed(True) is False
    assert z.feed(False) is False
    assert z.feed(True) is True


# --- WipeWatcher: local death resets the run --------------------------------

def test_wipe_fires_on_downward_hp_edge():
    w = WipeWatcher()
    assert w.feed(21000.0) is False  # baseline, no fire
    assert w.feed(15000.0) is False  # took damage, alive
    assert w.feed(0.0) is True       # died -> reset once
    assert w.feed(0.0) is False      # corpse ticks never re-fire
    assert w.feed(21000.0) is False  # respawned
    assert w.feed(0.0) is True       # died again -> fires again


def test_wipe_ignores_unreadable_ticks():
    w = WipeWatcher()
    assert w.feed(None) is False
    assert w.feed(None) is False
    w.feed(5000.0)
    assert w.feed(None) is False    # load-screen gap: no fire, baseline kept
    assert w.feed(4000.0) is False
    assert w.feed(None) is False


def test_wipe_needs_a_positive_baseline():
    w = WipeWatcher()
    assert w.feed(0.0) is False     # first read already dead: no edge


def test_wipe_reset_clears_baseline():
    w = WipeWatcher()
    w.feed(5000.0)
    w.reset()
    assert w.feed(0.0) is False


def test_group_wipe_waits_until_every_seen_member_is_down():
    g = GroupWipeWatcher()
    assert g.feed(True, {1, 2}, {1, 2}) is False
    assert g.feed(True, {2}, {2}) is False       # one survivor: not a wipe
    assert g.feed(True, set(), {1, 2}) is False  # corpses still readable
    assert g.feed(True, set(), set()) is False   # first despawn tick
    assert g.feed(True, set(), set()) is True    # whole group gone
    assert g.feed(True, set(), set()) is False   # fires once, then re-arms


# --- kill log: confirmed kills only, capped ----------------------------------

def test_kill_entry_bits_per_second_row_has_no_boss_ms():
    """A bits-per-second row (no boss split) carries no boss_ms so it is skipped
    when a per-dungeon boss PB is wanted."""
    row = kill_entry("MunsterChuck", 235400, "hard", 1726800000.0)
    assert row == {"boss": "MunsterChuck", "full_ms": 235400,
                   "mode": "hard", "at": 1726800000.0}
    assert "boss_ms" not in row


def test_kill_entry_with_boss_includes_boss_split():
    row = kill_entry_with_boss("MunsterChuck", 235400, 102180, "hard", 1726800000.0)
    assert row == {"boss": "MunsterChuck", "full_ms": 235400,
                   "boss_ms": 102180, "mode": "hard", "at": 1726800000.0}


def test_append_kill_log_caps_to_newest():
    rows: list = []
    for i in range(KILL_LOG_LIMIT + 5):
        rows = append_kill_log(rows, kill_entry("B", i, "hard", float(i)))
    assert len(rows) == KILL_LOG_LIMIT
    assert rows[-1]["full_ms"] == KILL_LOG_LIMIT + 4   # newest kept
    assert rows[0]["full_ms"] == 5                     # oldest dropped


def test_append_kill_log_tolerates_none():
    rows = append_kill_log(None, kill_entry("B", 1, "hard", 0.0))
    assert len(rows) == 1


def test_merge_history_rows_dedupes_sorts_and_caps():
    """THE shared-history merge: one identity contract (row_key) for the
    config writer, the migration and the backup restore. Same (char,boss,at,
    full_ms) must never double-count; output is newest-last and capped."""
    a = {"char": "K", "boss": "A", "full_ms": 10, "mode": "hard", "at": 1.0}
    a_dup = dict(a)                                # identical row -> deduped
    b = {"char": "K", "boss": "B", "full_ms": 20, "mode": "hard", "at": 2.0}
    out = merge_history_rows([b, a], [a_dup], limit=2)
    assert [r["boss"] for r in out] == ["A", "B"]
    assert len(out) == 2
    assert row_key(a) == row_key(a_dup)
    # Cap keeps the newest rows.
    many = [{"char": "K", "boss": f"B{i}", "full_ms": i,
             "mode": "hard", "at": float(i)} for i in range(10)]
    assert len(merge_history_rows(many, limit=10)) == 10


# --- should_auto_start: the speed-kill trigger -------------------------------

def test_auto_start_fires_on_boss_damage_only():
    """The run begins on the first boss-fight damage while inside — never on
    zone entry alone, never outside, never twice."""
    assert should_auto_start("ready", True, True) is True
    assert should_auto_start("ready", True, False) is False   # walking, no hit yet
    assert should_auto_start("ready", False, True) is False   # engaged but left
    assert should_auto_start("running", True, True) is False  # already going
    assert should_auto_start("done", True, True) is False     # finished


# --- tile sub-lines: PB + signed pace per clock (the tiles replaced the old
# --- headline record row; clock_pb_line is the pure half of that render) ------

def test_clock_pb_line_no_pb_says_so_once_there_is_a_time():
    text, tone = clock_pb_line(None, 102.18)
    assert text == "no PB yet" and tone == "none"


def test_clock_pb_line_shows_pb_and_signed_pace():
    text, tone = clock_pb_line(99.02, 102.18)
    assert text == "PB 01:39.02   Δ +3.16" and tone == "behind"
    text, tone = clock_pb_line(99.02, 95.00)
    assert "Δ −4.02" in text and tone == "ahead"


def test_clock_pb_line_new_best_star_and_pace():
    text, tone = clock_pb_line(95.00, 95.00, is_new_best=True)
    assert text.startswith("PB 01:35.00   ★ NEW BEST") and tone == "new_best"


def test_tile_sub_lines_pin_the_pure_helpers():
    """The overlay's tile sub-lines must be built from clock_pb_line + delta_text,
    not from a parallel string format: the pins here and the HUD's render share
    one source, so the wording cannot drift between test and screen."""
    assert delta_text(102.18, 99.02) == "Δ +3.16"
    assert delta_text(95.00, 99.02) == "Δ −4.02"
    assert delta_text(None, 99.02) is None


def test_speedrun_view_mode_is_gone():
    """Both clocks are permanently on screen in the tile layout, so there is no
    record line left for a view mode to choose. Pinned so it cannot creep back."""
    from farever_companion.config import Settings
    from farever_companion.core import speedrun as core
    assert not hasattr(Settings(), "speedrun_view_mode")
    assert not hasattr(core, "record_lines")
    # ...and the Speedrun page (deleted) must not come back with it.
    assert not __import__("pathlib").Path(
        "farever_companion/ui/pages/speedrun_page.py").exists()


def test_the_auto_detect_switch_is_gone_and_cannot_creep_back():
    """"Auto Detect Boss Run" was removed 2026-09-21: as an opt-in default it read
    as "the timer is broken" rather than "the feature is off". Detection in a
    dungeon or rift is now unconditional, so the flag must not come back without
    the reasoning (and these pins) being revisited."""
    from farever_companion import config
    from farever_companion.config import Settings
    assert "speedrun_auto" not in config._PROP_MAP
    assert not hasattr(Settings(), "speedrun_auto")
    assert "auto" not in Settings().speedrun_options


def test_a_stale_auto_key_is_stripped_on_load(tmp_path, monkeypatch):
    """An install saved while the switches existed still carries "auto" /
    "auto_rearm" in its options list. Left alone they would read as if detection
    or re-arm could be off — both are unconditional now — so load() drops them."""
    import json
    from farever_companion.config import Settings
    monkeypatch.setenv("FAREVER_MODDATA_DIR", str(tmp_path))
    (tmp_path / "settings.json").write_text(
        json.dumps({"speedrun_options": ["auto", "auto_rearm"]}), encoding="utf-8")
    assert Settings.load().speedrun_options == []


def test_the_rearm_toggle_is_gone_and_cannot_creep_back():
    """Re-arm after a finished run is always on (the overlay keys on DONE), so
    the Re-arm chip and its config flag must not come back without revisiting
    the reasoning (and these pins)."""
    from farever_companion import config
    from farever_companion.config import Settings
    assert "speedrun_auto_rearm" not in config._PROP_MAP
    assert not hasattr(Settings(), "speedrun_auto_rearm")
    assert "auto_rearm" not in Settings().speedrun_options


# --- HUD widget tests (merged from test_speedrun_overlay_ui.py) ---
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6 import QtWidgets  # noqa: E402

from farever_companion.ui.overlays.speedrun_overlay import (  # noqa: E402
    DETECT_EVERY,
    SpeedrunOverlay,
)


class _Settings:
    opacity = 1.0
    geometry = {}
    hud_accent = "#38bdf8"
    speedrun_bare = False
    speedrun_transparent = False

    def save(self):
        pass

    def get_speedrun_best(self, profile):
        return {}

    def get_speedrun_boss_best(self, profile):
        return {}

    def save_speedrun_best(self, profile, best):
        pass

    def save_speedrun_boss_best(self, profile, best):
        pass


class _Model:
    player_addr = 0

    def dungeon_mode(self):
        return None

    def player_profile(self):
        return "Me"

    def player_name(self, addr):
        return "MunsterChuck"

    def player_class(self):
        return "Rogue"


@pytest.fixture()
def qapp():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture()
def ov(qapp):
    o = SpeedrunOverlay(_Model(), _Settings())
    o.show()
    QtWidgets.QApplication.processEvents()
    o._render()
    QtWidgets.QApplication.processEvents()
    yield o
    # Destroy, not just close: overlays are parentless top-level windows, so a
    # closed one keeps its whole widget tree alive in the process and drifts
    # unrelated layout tests that run after this file (tests/qt_helpers.destroy).
    from tests.qt_helpers import destroy
    destroy(o)


def test_run_tile_caption_is_run(ov):
    """The whole-run clock's tile says what it times, and never the old 'FULL'."""
    cap = ov._run_tile.findChildren(QtWidgets.QLabel)[0]
    assert cap.text() == "RUN"
    assert ov.time_lbl.text() == "00:00.00"


@pytest.mark.parametrize("source", ["auto", "manual"])
def test_title_shows_title_case_difficulty_without_provenance(ov, source):
    """The HUD says 'Hard Mode', without the internal auto/manual source."""
    ov._in_dungeon = True
    ov._live_mode = "hard"
    ov._live_mode_src = source
    ov._render()

    title = ov.titlebar.title.text()
    assert title == "Run Timer · Hard Mode"
    style = ov.titlebar.title.styleSheet()
    assert "font-size:13px" in style
    assert "font-size:8px" not in style
    assert "font-size:2px" not in style
    assert "detected" not in title.lower()
    assert "manual" not in title.lower()


def test_boss_tile_is_labelled_boss_when_unarmed(ov):
    """Idle (no fight seen yet) the split still names itself, dash and all.

    "BOSS", not the old "SPEEDKILL": the label is a noun naming what the clock
    times (the code calls it the boss split everywhere else), and both clocks
    stop on the same kill, so a RUN/BOSS pair claims no nesting.
    """
    assert ov.boss_timer.state == ov.boss_timer.READY
    cap = ov._kill_tile.findChildren(QtWidgets.QLabel)[0]
    assert cap.text() == "BOSS"
    assert ov.sub_lbl.text() == "--:--.---"
    assert ov.sub_sub.text() == "no split yet"


def test_boss_tile_shows_live_split_when_running(ov):
    """Once the fight is armed the placeholder is replaced by the live split."""
    ov.boss_timer.arm()
    ov._render()
    assert "--:--" not in ov.sub_lbl.text()
    assert ov.sub_lbl.text() != "--:--.---"


def test_tile_sub_lines_carry_pb_pace_and_last(ov):
    """The tile sub-line is the record data: PB, signed pace, LAST — one per
    tile instead of a headline row across the window."""
    ov.s.get_speedrun_best = lambda profile: {"_": 99.02}
    ov.timer.last = 102.18
    ov._render()
    text = ov.time_sub.text()
    assert "PB 01:39.02" in text
    assert "Δ +3.16" in text
    assert "LAST 01:42.18" in text


# A tile is ~131px wide inside the pinned 330px HUD. The record row draws a
# "\u2605" for a new best, and U+2605 has no glyph in the bundled Roboto Condensed
# (checked against the TTF cmap), so the OS supplies it and its advance is ours
# to guess at. A symbol font typically draws it wider than the fallback does, so
# the row has to keep this much of the tile in hand rather than end up clipping
# the LAST time off the right edge.
RECORD_ROW_HEADROOM = 12


def _widest_record_rows(ov):
    """Drive the overlay to its longest record rows: PB + ★ + LAST on both tiles."""
    ov.s.get_speedrun_best = lambda profile: {"_": 99.02}
    ov.s.get_speedrun_boss_best = lambda profile: {"_": 45.20}
    ov.timer.state = ov.timer.DONE
    ov.timer.is_new_best = True
    ov.timer.last = 79.1
    ov.boss_timer.arm()
    ov.boss_timer.state = ov.boss_timer.DONE
    ov.boss_is_new_best = True
    ov.boss_timer.last = 45.2
    ov._render()
    QtWidgets.QApplication.processEvents()
    return ov.time_sub, ov.sub_sub


def test_the_record_row_fits_its_tile_under_the_app_stylesheet(ov):
    """The record row keeps real headroom beside a fallback-drawn \u2605.

    The app QSS gives QLabel#Mono a 0.5px letter-spacing, which on this row's
    ~24 characters is 12px of a 131px tile \u2014 leaving almost nothing once the
    star's borrowed advance lands. The row is prose, not tabular digits, so it
    drops the spacing; this pins the headroom that buys, and would have caught
    the row being clipped mid-glyph in the shipped HUD.
    """
    from PySide6 import QtGui
    from farever_companion import paths
    from farever_companion.ui import theme
    # The offscreen platform exposes NO font families at all, and Qt's substitute
    # is ~2.3x wider than the real face (measured: 264px vs 113px for this row),
    # so the bundled font has to be registered before anything is measured.
    # Registered and removed here rather than via theme.load_fonts(): that one is
    # process-global AND sticky, and the stock metrics are what the layout tests
    # in other files measure against (loading it there moved a settings-tab
    # overflow assertion). Measuring with a face should not re-face the suite.
    fid = QtGui.QFontDatabase.addApplicationFont(
        str(paths.assets_dir() / "fonts" / "RobotoCondensed-Variable.ttf"))
    try:
        ov.setStyleSheet(theme.QSS)      # the real app sheet, scoped to this window
        ov.ensurePolished()
        QtWidgets.QApplication.processEvents()
        for lab in _widest_record_rows(ov):
            assert "letter-spacing:0" in lab.styleSheet()
            fm = lab.fontMetrics()
            need = max(fm.horizontalAdvance(line) for line in lab.text().split("\n"))
            spare = lab.width() - need
            assert spare >= RECORD_ROW_HEADROOM, (
                f"{lab.text()!r} needs {need}px in {lab.width()}px, leaving only "
                f"{spare}px \u2014 the \u2605 is drawn from an OS fallback, so the row "
                "clips the moment that glyph is a little wider than the bundled "
                "font's")
    finally:
        QtGui.QFontDatabase.removeApplicationFont(fid)


def test_copy_buttons_land_a_full_result_line(ov, qapp):
    """Each tile has a copy-to-clipboard button that lands a complete result
    line — player, class, boss, dungeon, mode, time — one per clock. Buttons
    are live exactly when their clock has a finished time to copy.

    The line spells the clock out ("full run", not the tile's "RUN"): pasted
    into chat it has to identify itself, and the caption is only unambiguous
    sitting next to its own digits."""
    # Idle: nothing finished -> nothing to copy.
    assert not ov._run_copy.isEnabled()
    assert not ov._kill_copy.isEnabled()

    ov.timer.state = ov.timer.DONE
    ov.timer.last = 79.1
    ov.timer.boss_id = "boss_munster"
    ov.timer.is_kill = True
    ov.boss_timer.state = ov.boss_timer.DONE
    ov.boss_timer.last = 45.2
    ov._run_mode = "hard"
    ov._run_mode_src = "auto"
    ov._render()
    assert ov._run_copy.isEnabled() and ov._kill_copy.isEnabled()

    ov._run_copy.click()
    full = QtWidgets.QApplication.clipboard().text()
    assert "MunsterChuck (Rogue)" in full      # player + class
    assert "Munster" in full                   # boss, by name
    assert "(Hard)" in full                    # dungeon mode
    assert "full run 01:19.10" in full         # clock spelled out + time
    assert "RUN" not in full                   # never the bare tile caption

    ov._kill_copy.click()
    boss = QtWidgets.QApplication.clipboard().text()
    assert "boss split 00:45.20" in boss
    # Same context line apart from the clock itself.
    assert boss.replace("boss split 00:45.20", "") == \
        full.replace("full run 01:19.10", "")


# --- trigger wiring, end to end ----------------------------------------------
# Both triggers are wired in _tick and nowhere else: the FULL run starts only on
# ZoneEdge.feed() (the instance zone-in edge), and the BOSS split arms only
# inside `if running:`, where `running` was read at the TOP of the tick -- before
# any start that tick. That ordering IS the contract: the tick that starts a run
# cannot arm the split, a fight already under way can never start the clock, and
# a finish cannot be re-armed by the corpse still lying in the scene. These tests
# drive the widget's own _tick against a duck-typed model, so reordering _tick
# (or moving either trigger) fails here rather than in the game.


class _TickModel:
    """The model double the tick needs: an instance flag and one scene feed."""

    def __init__(self):
        self.inside = False
        self.instance_known = True
        self.members = ()
        self.kill_id = None
        self.engage_any = False
        self.states = []
        self.boss_max_hp = None
        self.combat_state_value = None
        self.party_combat_state_value = None
        self.player_addr = 0        # no hero address -> no direct HP fallback
        self.dps = type("_Dps", (), {"last_local_death_m": 0.0})()
        self.party_state = (False, {1}, {1})

    # game_state.in_instance() asks a duck-typed model for this one
    def is_in_dungeon_or_rift(self):
        return self.inside

    def instance_state(self):
        return self.inside, False, self.instance_known

    # ...and the overlay asks for these every tick
    def encounter_state(self):
        return (self.members, self.kill_id, list(self.states), self.engage_any)

    def boss_max_health(self):
        return self.boss_max_hp

    def combat_state(self):
        return self.combat_state_value

    def party_combat_state(self):
        return self.party_combat_state_value

    def dungeon_mode(self):
        return None

    def detected_mode(self):
        return "hard"

    def party_health_state(self):
        return self.party_state

    def player_profile(self):
        return "Me"

    def player_xyz(self):
        return (0.0, 0.0, 0.0)      # standing still, so the re-arm gate stays shut


@pytest.fixture()
def tick_overlay(qapp):
    """Factory for an overlay that only ticks when the test says so.

    ``town=True`` ticks once outside the instance first, which is what ARMS the
    zone edge (ZoneEdge fires on a False -> True transition, so the first
    observation being False is what makes the next True a zone-in). In the game
    that observation comes from a HIDDEN tick -- Rule D keeps this HUD off
    screen outside an instance -- which is what test_a_hidden_hud_arms_* pins.
    ``town=False`` attaches with no baseline at all, i.e. the mid-dungeon attach.

    Every overlay built is destroyed at teardown: those are parentless top-level
    windows, and leaving them alive is what drifts unrelated layout tests later
    in the same process (tests/qt_helpers.destroy).
    """
    from tests.qt_helpers import destroy

    made = []

    def make(town: bool = True):
        model = _TickModel()
        ov = SpeedrunOverlay(model, _Settings())
        ov._poll.stop()             # its own 50ms timer would tick underneath
        ov.show()
        if town:
            _tick(ov)
        made.append(ov)
        return ov, model

    yield make
    for ov in made:
        destroy(ov)


def _tick(ov, n: int = 1):
    """`n` real overlay ticks. Beyond DETECT_EVERY the scene read also happens."""
    for _ in range(n):
        ov._tick()


def _starts(ov):
    """Count SpeedrunTimer.start() calls: one per zone-in, and only per zone-in."""
    seen = []
    real = ov.timer.start

    def counting(*a, **k):
        seen.append(a)
        return real(*a, **k)

    ov.timer.start = counting
    return seen


def _fight(model, hp: float, max_hp: float | None = None):
    """The scene's answer: kill boss present, with optional reflected max HP."""
    model.members = ("boss_munster",)
    model.kill_id = "boss_munster"
    model.engage_any = False
    model.states = [("boss_munster", True, hp)]
    model.boss_max_hp = max_hp


def test_the_run_starts_only_on_the_instance_zone_edge(tick_overlay):
    """A boss fight under way in town never starts the clock: only zone-in does."""
    ov, model = tick_overlay()
    starts = _starts(ov)
    _fight(model, 100.0)
    _tick(ov, DETECT_EVERY + 1)             # in town, the fight is up
    _fight(model, 25.0)
    _tick(ov, DETECT_EVERY + 1)             # heavy damage, still in town
    assert ov.timer.state == ov.timer.READY
    assert ov.boss_timer.state == ov.boss_timer.READY
    assert starts == []

    model.inside = True                     # the zone-in edge
    _tick(ov)
    assert ov.timer.state == ov.timer.RUNNING
    assert len(starts) == 1


def test_a_hidden_hud_arms_the_zone_edge_for_the_next_run(tick_overlay):
    """Outside an instance Rule D hides this HUD, so the only tick that can see
    the FALSE half of the zone-in edge is a HIDDEN one. Without it the edge ever
    only armed without firing, and the RUN clock never started in the game."""
    ov, model = tick_overlay(town=False)    # created, never ticked in town
    starts = _starts(ov)
    ov.hide()
    _tick(ov, 3)                            # in town, HUD hidden
    assert ov.timer.state == ov.timer.READY
    assert starts == []
    model.inside = True                     # the manager shows the HUD on zone-in
    ov.show()
    _tick(ov)
    assert ov.timer.state == ov.timer.RUNNING
    assert len(starts) == 1


def test_a_zone_in_reached_while_hidden_still_starts_when_shown(tick_overlay):
    """Alt-tabbed (or the companion unfocused) at the moment of the zone-in: the
    hidden tick must not eat the edge, so the run starts when the HUD is back."""
    ov, model = tick_overlay(town=False)
    starts = _starts(ov)
    ov.hide()
    _tick(ov, 2)                            # in town, hidden: the edge is armed
    model.inside = True
    _tick(ov, 3)                            # zoned in while still hidden
    assert starts == []
    ov.show()
    _tick(ov)
    assert ov.timer.state == ov.timer.RUNNING
    assert len(starts) == 1


def test_leaving_the_instance_hidden_lets_the_next_zone_in_start(tick_overlay):
    """The exit must be seen while the HUD is hidden too, or the finished run
    holds the clock and the next dungeon never gets one."""
    ov, model = tick_overlay()
    _fight(model, 100.0)
    model.inside = True
    _tick(ov)
    _tick(ov)
    _fight(model, 0.0)                      # the kill
    _tick(ov)
    assert ov.timer.state == ov.timer.DONE
    ov.hide()
    model.inside = False                    # walked out while hidden
    _tick(ov, 2)
    model.inside = True                     # the next zone-in
    ov.show()
    _tick(ov)
    assert ov.timer.state == ov.timer.RUNNING


def test_a_run_live_when_the_player_leaves_is_cancelled_even_hidden(tick_overlay):
    """Left the instance mid-run with the HUD hidden: the clock is cancelled,
    not left counting, so the next zone-in can start a fresh run.

    A single-tick void-fall blip must not cancel; the gate requires a
    sustained proven-outside streak (mirroring the DPS tracker's
    INSTANCE_EDGE_DEBOUNCE_S)."""
    from farever_companion.ui.overlays.speedrun_overlay import OUTSIDE_CONFIRM_TICKS
    ov, model = tick_overlay()
    model.inside = True
    _tick(ov)
    assert ov.timer.state == ov.timer.RUNNING
    # A brief blip outside stays alive.
    ov.hide()
    model.inside = False
    _tick(ov, OUTSIDE_CONFIRM_TICKS - 1)
    assert ov.timer.state == ov.timer.RUNNING
    model.inside = True
    _tick(ov)
    assert ov.timer.state == ov.timer.RUNNING
    # Sustained outside genuinely cancels.
    ov.hide()
    model.inside = False
    _tick(ov, OUTSIDE_CONFIRM_TICKS + 1)
    assert ov.timer.state == ov.timer.READY
    model.inside = True
    ov.show()
    _tick(ov)
    assert ov.timer.state == ov.timer.RUNNING


def test_attaching_mid_dungeon_keeps_run_ready_but_boss_can_arm_from_pristine_baseline(tick_overlay):
    """Attach inside holds RUN, but a not-yet-hit boss may start BOSS."""
    ov, model = tick_overlay(town=False)
    starts = _starts(ov)
    _fight(model, 100.0, 100.0)
    model.inside = True
    _tick(ov, DETECT_EVERY + 1)             # first boss observation is pristine
    assert ov.timer.state == ov.timer.READY
    _fight(model, 40.0)
    _tick(ov, 3 * DETECT_EVERY)             # first fresh hit after attach

    assert starts == []
    assert ov.timer.state == ov.timer.READY
    assert ov.boss_timer.state == ov.boss_timer.RUNNING

    model.inside = False
    _tick(ov)
    model.inside = True
    _tick(ov)
    assert ov.timer.state == ov.timer.RUNNING
    assert len(starts) == 1


def test_attaching_to_a_damaged_boss_never_starts_boss(tick_overlay):
    ov, model = tick_overlay(town=False)
    _fight(model, 90.0, 100.0)
    model.inside = True
    lines = []
    ov.log.connect(lines.append)
    _tick(ov, DETECT_EVERY + 1)
    _fight(model, 80.0, 100.0)
    _tick(ov, 3 * DETECT_EVERY)

    assert ov.timer.state == ov.timer.READY
    assert ov.boss_timer.state == ov.boss_timer.READY
    assert "fight already started" in ov.upload_lbl.text()
    assert any("BOSS SKIP" in line for line in lines)


def test_attaching_without_max_health_fails_closed(tick_overlay):
    ov, model = tick_overlay(town=False)
    _fight(model, 100.0, None)
    model.inside = True
    _tick(ov, DETECT_EVERY + 1)
    _fight(model, 80.0, None)
    _tick(ov, 3 * DETECT_EVERY)

    assert ov.timer.state == ov.timer.READY
    assert ov.boss_timer.state == ov.boss_timer.READY
    assert "Open FareverPal before zoning in" in ov.upload_lbl.text()


def test_attaching_at_full_health_uses_explicit_precombat_baseline(tick_overlay):
    """Builds without reflected max HP still track a boss first seen before combat."""
    ov, model = tick_overlay(town=False)
    model.combat_state_value = {"in_combat": False}
    model.party_combat_state_value = False
    _fight(model, 23322.0, None)
    model.inside = True
    _tick(ov, DETECT_EVERY + 1)
    assert ov.timer.state == ov.timer.READY
    assert "pre-combat baseline" in ov.upload_lbl.text()

    _fight(model, 23000.0, None)
    _tick(ov, 3 * DETECT_EVERY)
    assert ov.timer.state == ov.timer.READY
    assert ov.boss_timer.state == ov.boss_timer.RUNNING


def test_attaching_during_combat_without_max_health_stays_held(tick_overlay):
    ov, model = tick_overlay(town=False)
    model.combat_state_value = {"in_combat": True}
    model.party_combat_state_value = True
    _fight(model, 23322.0, None)
    model.inside = True
    _tick(ov, DETECT_EVERY + 1)
    _fight(model, 23000.0, None)
    _tick(ov, 3 * DETECT_EVERY)

    assert ov.timer.state == ov.timer.READY
    assert ov.boss_timer.state == ov.boss_timer.READY
    assert "fight already started" in ov.upload_lbl.text()


def test_attaching_with_one_group_member_in_combat_stays_held(tick_overlay):
    ov, model = tick_overlay(town=False)
    model.combat_state_value = {"in_combat": False}
    model.party_combat_state_value = True   # ally is in combat
    _fight(model, 23322.0, None)
    model.inside = True
    _tick(ov, DETECT_EVERY + 1)
    _fight(model, 23000.0, None)
    _tick(ov, 3 * DETECT_EVERY)

    assert ov.boss_timer.state == ov.boss_timer.READY
    assert "fight already started" in ov.upload_lbl.text()


def test_attaching_with_unreadable_group_combat_stays_held(tick_overlay):
    ov, model = tick_overlay(town=False)
    model.combat_state_value = {"in_combat": False}
    model.party_combat_state_value = None   # one member's state is unknown
    _fight(model, 23322.0, None)
    model.inside = True
    _tick(ov, DETECT_EVERY + 1)
    _fight(model, 23000.0, None)
    _tick(ov, 3 * DETECT_EVERY)

    assert ov.boss_timer.state == ov.boss_timer.READY
    assert "Open FareverPal before zoning in" in ov.upload_lbl.text()


def test_normal_zone_in_still_arms_boss_without_an_attach_pristine_proof(tick_overlay):
    ov, model = tick_overlay()
    model.inside = True
    _tick(ov)
    _fight(model, 100.0, None)
    _tick(ov, DETECT_EVERY + 1)
    _fight(model, 80.0, None)
    _tick(ov)

    assert ov.timer.state == ov.timer.RUNNING
    assert ov.boss_timer.state == ov.boss_timer.RUNNING


def test_map_change_reset_while_already_inside_does_not_restart_run(tick_overlay):
    """A map/id change inside the dungeon clears RUN but cannot fake outside."""
    ov, model = tick_overlay()
    starts = _starts(ov)
    model.inside = True
    _tick(ov)
    assert ov.timer.state == ov.timer.RUNNING

    ov.reset(prime_zone=False)             # manager knows this map is still inside
    assert ov.timer.state == ov.timer.READY
    _fight(model, 100.0, 100.0)
    _tick(ov, 3 * DETECT_EVERY)
    _fight(model, 40.0, 100.0)
    _tick(ov, 3 * DETECT_EVERY)

    assert len(starts) == 1                # still only the real outside->inside start
    assert ov.timer.state == ov.timer.READY
    assert ov.boss_timer.state == ov.boss_timer.RUNNING


def test_a_normal_zone_in_after_an_outside_baseline_can_record_boss(tick_overlay):
    """Only a mid-fight attach suppresses BOSS; a proven zone-in does not."""
    ov, model = tick_overlay()              # first tick observes outside
    _fight(model, 100.0)
    model.inside = True
    _tick(ov)
    _tick(ov, DETECT_EVERY + 1)
    _fight(model, 88.0)
    _tick(ov)
    assert ov.timer.state == ov.timer.RUNNING
    assert ov.boss_timer.state == ov.boss_timer.RUNNING


def test_unlocated_model_never_seeds_a_false_outside_edge(tick_overlay):
    """Pre-attach/pre-locate False is UNKNOWN, not evidence for RUN."""
    ov, model = tick_overlay(town=False)
    model.player_addr = None
    lines = []
    ov.log.connect(lines.append)
    _tick(ov)
    assert not any("instance=OUTSIDE" in line for line in lines)

    model.player_addr = 0x1234           # first real location is already inside
    model.inside = True
    _tick(ov)
    assert any("first observation is INSIDE" in line for line in lines)
    assert "Open FareverPal before zoning in" in ov.upload_lbl.text()
    assert not any("RUN START" in line for line in lines)
    assert ov.timer.state == ov.timer.READY


def test_probe_logs_start_edges_without_per_tick_spam(tick_overlay):
    ov, model = tick_overlay()
    lines = []
    ov.log.connect(lines.append)

    model.inside = True
    _tick(ov)
    assert any("instance=INSIDE" in line for line in lines)
    assert any("RUN START on proven outside -> inside" in line for line in lines)

    logged = list(lines)
    _tick(ov, 5)
    assert lines == logged                 # unchanged state logs nothing


def test_boss_can_finish_without_a_full_run_when_attached_before_first_hit(tick_overlay):
    ov, model = tick_overlay(town=False)
    _fight(model, 100.0, 100.0)
    model.inside = True
    _tick(ov, DETECT_EVERY + 1)
    _fight(model, 88.0, 100.0)
    _tick(ov)
    assert ov.timer.state == ov.timer.READY
    assert ov.boss_timer.state == ov.boss_timer.RUNNING
    assert "BOSS is recording" in ov.upload_lbl.text()

    _fight(model, 0.0)
    _tick(ov)
    assert ov.timer.state == ov.timer.READY
    assert ov.boss_timer.state == ov.boss_timer.DONE
    assert ov.boss_timer.last is not None
    assert "BOSS finished" in ov.upload_lbl.text()


def test_probe_holds_when_first_observed_inside(tick_overlay):
    ov, model = tick_overlay(town=False)
    lines = []
    ov.log.connect(lines.append)
    model.inside = True
    _tick(ov)

    assert any("first observation is INSIDE" in line for line in lines)
    assert "Open FareverPal before zoning in" in ov.upload_lbl.text()
    assert not any("RUN START" in line for line in lines)
    assert ov.timer.state == ov.timer.READY


def test_probe_logs_fresh_boss_arm_once(tick_overlay):
    ov, model = tick_overlay()
    model.inside = True
    _tick(ov)
    _fight(model, 100.0)
    _tick(ov, DETECT_EVERY + 1)            # baseline the encounter
    lines = []
    ov.log.connect(lines.append)

    _fight(model, 88.0)
    _tick(ov)
    assert sum("BOSS ARM" in line for line in lines) == 1
    _tick(ov, 3)
    assert sum("BOSS ARM" in line for line in lines) == 1


def test_probe_logs_confirmed_kill_and_boss_result(tick_overlay):
    ov, model = tick_overlay()
    _fight(model, 100.0)
    model.inside = True
    _tick(ov)
    _tick(ov)
    _fight(model, 88.0)
    _tick(ov)                              # BOSS armed
    lines = []
    ov.log.connect(lines.append)

    _fight(model, 0.0)
    _tick(ov)
    finish = [line for line in lines if "RUN FINISH" in line]
    assert len(finish) == 1
    assert "BOSS=FINISHED" in finish[0]


def test_an_unknown_scene_read_cannot_look_like_an_outside_inside_edge(tick_overlay):
    """A failed first read is not False; a later inside view only arms the edge."""
    ov, model = tick_overlay(town=False)
    starts = _starts(ov)
    _fight(model, 100.0, 100.0)
    model.inside = True
    model.instance_known = False
    _tick(ov, DETECT_EVERY + 1)
    model.instance_known = True
    _fight(model, 40.0, 100.0)
    _tick(ov, 3 * DETECT_EVERY)

    assert starts == []
    assert ov.timer.state == ov.timer.READY
    assert ov.boss_timer.state == ov.boss_timer.RUNNING


def test_a_second_zone_edge_cannot_restart_a_live_run(tick_overlay):
    """Re-entering (or any later edge) while the run is live keeps the SAME
    stopwatch: the clock a speedrun is measured on never silently restarts."""
    ov, model = tick_overlay()
    starts = _starts(ov)
    model.inside = True
    _tick(ov)
    t0 = ov.timer._t0
    model.inside = False
    _tick(ov, 2)
    model.inside = True
    _tick(ov, 2)
    assert len(starts) == 1
    assert ov.timer._t0 == t0


def test_the_boss_split_arms_only_once_the_run_is_running(tick_overlay):
    """The split cannot arm on the tick that starts the run -- `running` was read
    before the start -- and it waits for a fresh hit rather than the pre-run one."""
    ov, model = tick_overlay()
    _fight(model, 100.0)
    model.inside = True
    _tick(ov)
    assert ov.timer.state == ov.timer.RUNNING
    assert ov.boss_timer.state == ov.boss_timer.READY   # same tick, not armed

    _tick(ov)                               # baseline the (cleared) fight
    _fight(model, 88.0)                     # the first hit lands
    _tick(ov)
    assert ov.boss_timer.state == ov.boss_timer.RUNNING


def test_solo_death_keeps_full_run_and_resets_only_boss_split(tick_overlay):
    ov, model = tick_overlay()
    _fight(model, 100.0)
    model.inside = True
    _tick(ov)
    _tick(ov)                              # baseline the encounter
    _fight(model, 88.0)
    _tick(ov)
    assert ov.boss_timer.state == ov.boss_timer.RUNNING
    run_start = ov.timer._t0

    model.dps.last_local_death_m = 1.0
    _tick(ov)

    assert ov.timer.state == ov.timer.RUNNING
    assert ov.timer._t0 == run_start
    assert ov.boss_timer.state == ov.boss_timer.READY


def test_a_void_fall_death_keeps_the_run_and_only_resets_the_boss_split(tick_overlay):
    """The report: fell off a ledge (boss still healthy), the whole RUN reset.
    Death must only reset the BOSS split; a healthy boss that vanishes while
    still inside, or while the scene is unreadable for a couple of ticks,
    is not an abort, and a transient outside/unload during the respawn must
    be held, not counted, so the run keeps its start time."""
    ov, model = tick_overlay()
    model.inside = True
    _tick(ov)
    _fight(model, 100.0)
    _tick(ov, DETECT_EVERY + 1)         # baseline the boss peak
    _fight(model, 82.0)
    _tick(ov)
    assert ov.timer.state == ov.timer.RUNNING
    assert ov.boss_timer.state == ov.boss_timer.RUNNING
    t0 = ov.timer._t0
    # Die by falling: the tracker marks a wipe, the scene reloads, the boss's
    # arena unloads for a tick or two while the instance gate blips outside.
    model.dps.last_local_death_m = 1.0
    model.inside = False
    model.states = []                   # boss not in scene this tick
    _tick(ov, 2)
    model.inside = True
    _fight(model, 100.0)
    _tick(ov, 2)
    assert ov.timer.state == ov.timer.RUNNING
    assert ov.timer._t0 == t0           # full run was NOT reset
    assert ov.boss_timer.state == ov.boss_timer.READY  # boss was correctly reset


def test_group_death_resets_boss_only_after_last_member_is_down(tick_overlay):
    ov, model = tick_overlay()
    model.party_state = (True, {1, 2}, {1, 2})
    _fight(model, 100.0)
    model.inside = True
    _tick(ov)
    _tick(ov)
    _fight(model, 88.0)
    _tick(ov)
    assert ov.boss_timer.state == ov.boss_timer.RUNNING
    run_start = ov.timer._t0

    model.dps.last_local_death_m = 1.0
    model.party_state = (True, {2}, {2})
    _tick(ov)
    assert ov.boss_timer.state == ov.boss_timer.RUNNING  # teammate remains

    model.party_state = (True, set(), set())
    _tick(ov, 2)
    assert ov.boss_timer.state == ov.boss_timer.RUNNING
    _tick(ov)
    assert ov.timer.state == ov.timer.RUNNING
    assert ov.timer._t0 == run_start
    assert ov.boss_timer.state == ov.boss_timer.READY


def test_damage_to_a_non_member_does_not_arm_the_split(tick_overlay):
    """Only the fight's own units arm the split (engage_any off): a trash mob
    dying beside the pull is not the dungeon's first boss damage."""
    ov, model = tick_overlay()
    model.members = ("boss_munster",)
    model.kill_id = "boss_munster"
    model.engage_any = False
    model.inside = True
    _tick(ov)
    assert ov.timer.state == ov.timer.RUNNING
    model.states = [("boss_munster", True, 100.0), ("trash_7", True, 500.0)]
    _tick(ov)
    model.states = [("boss_munster", True, 100.0), ("trash_7", True, 20.0)]
    _tick(ov, 3)
    assert ov.boss_timer.state == ov.boss_timer.READY
    model.states = [("boss_munster", True, 91.0), ("trash_7", True, 20.0)]
    _tick(ov)
    assert ov.boss_timer.state == ov.boss_timer.RUNNING


def test_a_finished_run_is_not_re_armed_by_the_fight_left_in_the_scene(tick_overlay):
    """After the kill the result is held: the corpse (and any further damage)
    cannot reset or restart either clock while the player stands in the dungeon."""
    ov, model = tick_overlay()
    _fight(model, 100.0)
    model.inside = True
    _tick(ov)
    _tick(ov)
    _fight(model, 80.0)
    _tick(ov)
    assert ov.boss_timer.state == ov.boss_timer.RUNNING
    _fight(model, 0.0)                      # the kill
    _tick(ov)
    assert ov.timer.state == ov.timer.DONE
    assert ov.boss_timer.state == ov.boss_timer.DONE
    frozen = ov.boss_timer.last
    for _ in range(30):                     # still standing there, looting
        _tick(ov)
    assert ov.timer.state == ov.timer.DONE
    assert ov.boss_timer.state == ov.boss_timer.DONE
    assert ov.boss_timer.last == frozen


def test_boss_heal_or_leash_resets_boss_split_and_lets_next_hit_restart(tick_overlay):
    """Boss heals back to full (wipe/leash): BOSS split resets and the next hit restarts it, RUN keeps timing.

    Only a heal that brings the boss back to ~100% WHILE OUT OF COMBAT trips it —
    a mid-fight self-heal to full stays in combat and must not reset the split.
    """
    ov, model = tick_overlay()
    model.inside = True
    _tick(ov)
    _fight(model, 100.0)
    _tick(ov, DETECT_EVERY + 1)
    _fight(model, 82.0)
    _tick(ov)
    assert ov.timer.state == ov.timer.RUNNING
    assert ov.boss_timer.state == ov.boss_timer.RUNNING
    t0_boss = ov.boss_timer._t0
    t0_run = ov.timer._t0
    model.party_combat_state_value = False  # proven wipe / out of combat
    _fight(model, 100.0)  # heal to full while wiped
    _tick(ov)
    assert ov.timer.state == ov.timer.RUNNING
    assert ov.timer._t0 == t0_run
    assert ov.boss_timer.state == ov.boss_timer.READY
    # before next hit: stays ready (heal tick established a fresh peak at 100)
    _fight(model, 90.0)
    _tick(ov)
    assert ov.boss_timer.state == ov.boss_timer.READY
    _fight(model, 80.0)  # drop below fresh peak -> engage re-arms
    _tick(ov)
    assert ov.boss_timer.state == ov.boss_timer.RUNNING
    assert ov.boss_timer._t0 != t0_boss


def test_boss_reset_does_not_hamper_a_following_group_wipe_sequence(tick_overlay):
    ov, model = tick_overlay()
    model.inside = True
    _tick(ov)
    _fight(model, 100.0)
    _tick(ov, DETECT_EVERY + 1)
    _fight(model, 70.0)
    _tick(ov)
    assert ov.boss_timer.state == ov.boss_timer.RUNNING
    model.party_combat_state_value = False
    _fight(model, 100.0)  # wipe heal while out of combat
    _tick(ov)
    assert ov.boss_timer.state == ov.boss_timer.READY
    _fight(model, 90.0)
    _tick(ov)
    assert ov.boss_timer.state == ov.boss_timer.READY
    _fight(model, 80.0)
    _tick(ov)
    assert ov.boss_timer.state == ov.boss_timer.RUNNING
    # still in combat self-heals must NOT reset the split
    model.party_combat_state_value = True
    _fight(model, 100.0)  # heal to full while still fighting
    _tick(ov)
    assert ov.boss_timer.state == ov.boss_timer.RUNNING
    model.party_combat_state_value = False
    _fight(model, 95.0)
    _tick(ov)
    assert ov.boss_timer.state == ov.boss_timer.RUNNING  # 95% is not full
    model.party_combat_state_value = True
    _fight(model, 100.0)  # full again, still in combat
    _tick(ov)
    assert ov.boss_timer.state == ov.boss_timer.RUNNING
    # group wipe still respected after heal sequence
    model.party_state = (True, {1, 2}, {1, 2})
    model.dps.last_local_death_m = 1.0
    _tick(ov)
    model.party_state = (True, set(), set())
    _tick(ov, 3)
    assert ov.timer.state == ov.timer.RUNNING
    assert ov.boss_timer.state == ov.boss_timer.READY


# --- the copied line's dungeon name (regression, 2026-09-27) ---------------

def test_copied_line_names_the_zone_the_player_is_standing_in(monkeypatch):
    """`from ...geo import geo_zones` named a module that does not exist, and
    the bare except around it swallowed the ImportError: every copied line
    said "Dungeon --" and every logged kill lost its dungeon, for as long as
    that line was there. Pinned through the zone resolver's own attributes
    rather than the module path, so the broken import still fails this test.
    """
    from farever_companion.data import names
    from farever_companion.geo import zones
    from farever_companion.ui.overlays import speedrun_overlay as so

    monkeypatch.setattr(zones, "resolve_zone", lambda x, y, z: "Z1")
    monkeypatch.setattr(names, "zone_name", lambda zid: "Skover Island")

    class Standing:
        def player_xyz(self):
            return (100.0, 200.0, 0.0)

    class NoPosition:
        def player_xyz(self):
            return None

    assert so._dungeon_display(Standing()) == "Skover Island"
    assert so._dungeon_display(NoPosition()) == "--"
