"""Headless tests for the event-driven DpsTracker (core/dps_tracker.py).

Real damage events (the model's DamageSourceManager ring) are authoritative;
HP is display/segmentation only and never fabricates numbers: dropping enemy
HP alone records nothing, boss routing follows the in-boss-fight segment, and
unresolvable casters are never fabricated onto another player.
"""
from __future__ import annotations

import time
from pathlib import Path

from farever_companion.core.damage_events import DamageEvent, K_HEAL
from farever_companion.core.dps_tracker import (
    DpsTracker, CombatSession, GAME_COMBAT_END_CONFIRM_S,
    ZONE_ARM_SETTLE_S, own_row)
from farever_companion.core.dps_dummy import (DUMMY_REARM_S,
                                              DUMMY_REENGAGE_COOLDOWN_S)

from tests.dps_fakes import (
    PA, TEAM, ALLY, FOE, BOSS, PET, _FakeModel as _Model,
    _hero, _foe, training_dummy, _pet,
)

# A second hero on the scene that is NOT the local player or the party ally:
# the instance-wide scan's "another player is in this rift" slot.
STRANGER = 0x9999


def test_entering_dungeon_or_rift_auto_resets_meter():
    """Zoning into a rift/dungeon auto-resets the DPS meter: the open-world
    run is archived to fight history, all live sessions clear, and staying
    inside (or repeated updates) doesn't reset again until the next entry."""
    m = _Model()
    tr = DpsTracker(m)
    m._units = [_hero(PA), _foe(FOE)]
    tr.update()
    m.damage.push(DamageEvent(amount=300.0, skill="Sword_Base_Attack",
                              source_addr=PA, target_addr=FOE,
                              t=time.time()))
    tr.update()
    assert tr.overall_session.group_damage == 300.0
    assert tr.history == []

    # open world: repeated updates never reset
    tr.update()
    assert tr.overall_session.group_damage == 300.0

    # zone into a dungeon -> auto reset archives the run and clears the
    # meter. The edge must HOLD for the debounce window (a flickering
    # is_dungeon read must not wipe a run mid-flight), so this test pins
    # the debounce to zero and drives updates past it.
    tr._instance_edge_debounce_s = 0.0
    m.is_dungeon = True
    tr.update()
    assert tr.overall_session.group_damage == 0.0
    # an open-world run is not instance content: the reset still
    # clears the meter, but there is no trash board to archive
    assert tr.history == []

    # staying inside doesn't reset again
    tr.update()
    assert tr.overall_session.group_damage == 0.0
    assert tr.history == []

    # leave to the open world, accumulate a bit, then re-enter via a rift
    m.is_dungeon = False
    tr.update()
    m.damage.push(DamageEvent(amount=50.0, skill="Sword_Base_Attack",
                              source_addr=PA, target_addr=FOE,
                              t=time.time()))
    tr.update()
    assert tr.overall_session.group_damage == 50.0
    m.is_rift = True
    tr.update()
    assert tr.overall_session.group_damage == 0.0
    assert tr.history == []


def test_zone_in_flicker_does_not_wipe_the_meter():
    """A transient is_dungeon flicker (loading screen, in-dungeon area
    transition) must NOT archive/wipe the run: only an edge that HOLDS the
    debounce window fires the reset (live 2026-09-19: meter cleared when
    zoning into a dungeon)."""
    m = _Model()
    tr = DpsTracker(m)
    m._units = [_hero(PA), _foe(FOE)]
    tr.update()
    m.damage.push(DamageEvent(amount=300.0, skill="Sword_Base_Attack",
                              source_addr=PA, target_addr=FOE, t=time.time()))
    tr.update()
    assert tr.overall_session.group_damage == 300.0

    # Flicker ON for one tick, then back out — under the debounce window.
    m.is_dungeon = True
    tr.update()
    m.is_dungeon = False
    tr.update()
    assert tr.overall_session.group_damage == 300.0     # run survived
    assert tr.history == []

    # A REAL zone-in (edge holds, monotonic clock advanced) still resets.
    tr._instance_edge_debounce_s = 0.0
    m.is_dungeon = True
    tr.update()
    assert tr.overall_session.group_damage == 0.0
    # open-world run: the meter resets, nothing archives (instance-only)
    assert tr.history == []


def test_auto_reset_zone_toggle_off_keeps_meter_across_zone_in():
    """With the Auto-Reset Zone toggle off, zoning into a dungeon/rift must
    NOT wipe the meter (no archive, no session clear) — the open-world run
    carries into the instance."""
    m = _Model()
    tr = DpsTracker(m)
    tr.auto_reset_zone = False       # user turned the toggle off
    m._units = [_hero(PA), _foe(FOE)]
    tr.update()
    m.damage.push(DamageEvent(amount=300.0, skill="Sword_Base_Attack",
                              source_addr=PA, target_addr=FOE,
                              t=time.time()))
    tr.update()
    assert tr.overall_session.group_damage == 300.0

    # zone into a dungeon: nothing resets, nothing archives
    m.is_dungeon = True
    tr.update()
    assert tr.overall_session.group_damage == 300.0
    assert tr.history == []

    # re-enter again (open world -> rift): still no wipe
    m.is_dungeon = False
    m.is_rift = True
    tr.update()
    assert tr.overall_session.group_damage == 300.0
    assert tr.history == []


def test_hp_drops_alone_record_nothing():
    """Dropping enemy HP alone records nothing: HP never fabricates damage."""
    m = _Model()
    tr = DpsTracker(m)
    m._units = [_hero(PA), _foe(FOE, hp=5000.0)]
    tr.update()
    m._units = [_hero(PA), _foe(FOE, hp=4000.0)]
    tr.update()
    m._units = [_hero(PA), _foe(FOE, hp=0.0)]
    tr.update()
    assert tr.overall_session.group_damage == 0.0
    assert tr.overall_session.group_heals == 0.0
    assert tr.overall_session.state == "READY"


def test_silent_source_never_fabricates_damage():
    """A silent event source never fabricates damage from HP drops."""
    m = _Model()
    tr = DpsTracker(m)
    m._units = [_hero(PA), _foe(FOE, hp=5000.0)]
    tr.update()
    m._units = [_hero(PA), _foe(FOE, hp=4000.0)]
    tr.update()
    assert tr.overall_session.group_damage == 0.0


def test_real_damage_event_is_recorded_with_caster_and_skill():
    m = _Model()
    tr = DpsTracker(m)
    m._units = [_hero(PA), _foe(FOE)]
    tr.update()

    m.damage.push(DamageEvent(amount=77.0, skill="Sword_Base_Attack",
                              crit=True, source_addr=PA, target_addr=FOE,
                              t=time.time()))
    tr.update()

    s = tr.overall_session
    assert s.state == "COMBAT"
    assert abs(s.group_damage - 77.0) < 1e-6
    p = s.players.get("Me (You)")
    assert p is not None
    assert abs(p.total_damage - 77.0) < 1e-6
    assert p.hit_count == 1
    assert "Sword_Base_Attack" in p.skills
    assert s.target_addr == FOE
    hit = s.hits[0]
    assert hit.is_crit and not hit.is_heal and hit.caster_name == "Me (You)"


def test_hero_target_event_classifies_as_heal():
    m = _Model()
    tr = DpsTracker(m)
    m._units = [_hero(PA), _hero(TEAM), _foe(FOE)]
    tr.update()

    m.damage.push(DamageEvent(amount=120.0, skill="Priest_Holy_Light",
                              source_addr=PA, target_addr=TEAM, kind=K_HEAL,
                              t=time.time()))
    tr.update()

    s = tr.overall_session
    assert abs(s.group_heals - 120.0) < 1e-6
    p = s.players.get("Me (You)")
    assert p is not None and abs(p.heals - 120.0) < 1e-6
    # healing feed row targets the healed teammate
    assert s.hits[0].is_heal and s.hits[0].target_name == "Teammate"


def test_negative_amount_counts_as_heal():
    m = _Model()
    tr = DpsTracker(m)
    m._units = [_hero(PA), _foe(FOE)]
    tr.update()
    # a friendly number over me with a negative amount (heal encoding)
    m.damage.push(DamageEvent(amount=-50.0, skill="Regen",
                              source_addr=PA, target_addr=PA, t=time.time()))
    tr.update()
    assert abs(tr.overall_session.group_heals - 50.0) < 1e-6


def test_open_world_heal_never_lands_on_the_trash_segment():
    """The HEALING channel gates its trash branch on the instance flag exactly
    as the outgoing (damage) and incoming (damage-taken) paths already do.

    Without the gate, open-world sustain fed `trash_session` - a DUNGEON/RIFT
    segment - so the DPS Analysis Trash view and the instance's trash pack
    could open carrying heals from a fight that never happened inside (live
    2026-10-02). Inside an instance the same heal is still the pack's.
    """
    m = _Model()
    tr = DpsTracker(m)
    m._units = [_hero(PA), _foe(FOE)]          # open world
    tr.update()
    m.damage.push(DamageEvent(amount=-120.0, skill="Regen", kind=K_HEAL,
                              source_addr=PA, target_addr=PA, t=time.time()))
    tr.update()
    assert tr.trash_session.group_heals == 0.0     # no trash board outside...
    assert abs(tr.overall_session.group_heals - 120.0) < 1e-6  # ...overall keeps it

    # inside an instance the same heal belongs to the dungeon's trash pack
    m2 = _Model()
    m2.is_dungeon = True
    tr2 = DpsTracker(m2)
    m2._units = [_hero(PA), _foe(FOE)]
    tr2.update()
    m2.damage.push(DamageEvent(amount=-80.0, skill="Regen", kind=K_HEAL,
                               source_addr=PA, target_addr=PA, t=time.time()))
    tr2.update()
    assert abs(tr2.trash_session.group_heals - 80.0) < 1e-6
    assert abs(tr2.overall_session.group_heals - 80.0) < 1e-6


def test_dungeon_trash_heals_and_taken_stay_on_the_trash_segment():
    """A dungeon's pre-boss trash must record its own healing and damage taken.

    Live 2026-10-02, the Lost City run: the trash crab was promoted to "the
    boss", so `in_boss_fight` latched from the first pack - every heal went to
    the boss session, incoming hits to its taken channel, and the other trash
    mobs' damage to the adds session. The trash session stayed empty, so the
    page's Trash segment showed 0 for a run whose archive carried the numbers.
    The crab is trash now; this pins the routing the display depends on, and
    that the boss engage archives the pulls with those numbers intact.
    """
    from farever_companion.core.scene import Entity

    m = _Model()
    m.is_dungeon = True
    tr = DpsTracker(m)
    crab = Entity(addr=FOE, cls="ent.boss.Crabgantua", unit_id="Crab_Z1D",
                  x=5.0, y=5.0, z=5.0, hp=900.0, is_foe=True)
    m._units = [_hero(PA), crab]
    tr.update()
    assert tr.in_boss_fight is False

    now = time.time()
    m.damage.push(DamageEvent(amount=1000.0, skill="Sword_Base_Attack",
                              source_addr=PA, target_addr=FOE, t=now))
    m.damage.push(DamageEvent(amount=-500.0, skill="Regen", kind=K_HEAL,
                              source_addr=PA, target_addr=PA, t=now))
    m.damage.push(DamageEvent(amount=300.0, skill="Mob_Bite",
                              source_addr=FOE, target_addr=PA,
                              incoming=True, t=now))
    tr.update()
    assert abs(tr.trash_session.group_damage - 1000.0) < 1e-6
    assert abs(tr.trash_session.group_heals - 500.0) < 1e-6
    assert abs(tr.trash_session.group_taken - 300.0) < 1e-6
    assert tr.boss_session.group_heals == 0.0     # nothing hijacked to the boss
    assert tr.boss_session.group_taken == 0.0

    # The real boss appears: the pulls archive with their numbers, which is
    # what the page's Trash segment reads (live) and what the paired
    # "Trash pack <Boss>" file carries (after the run).
    m._units = [_hero(PA), _foe(0x41A1, uid="Nepsilon", hp=8000.0)]
    tr.update()
    assert tr.in_boss_fight is True
    trashed = [s for s in tr.history if s.kind == "trash"]
    assert len(trashed) == 1
    assert abs(trashed[0].group_heals - 500.0) < 1e-6
    assert abs(trashed[0].group_taken - 300.0) < 1e-6


def _trash_triple(tr):
    return (tr.trash_session.group_damage,
            tr.trash_session.group_heals,
            tr.trash_session.group_taken)


def _overall_triple(tr):
    return (tr.overall_session.group_damage,
            tr.overall_session.group_heals,
            tr.overall_session.group_taken)


def _attr_value(tr, path):
    """Read a dotted attribute path off the tracker (0.0 when absent)."""
    if path is None:
        return 0.0
    obj = tr
    for part in path.split("."):
        obj = getattr(obj, part)
    return obj


def test_only_an_instance_feeds_the_trash_segment():
    """EVERY event family reaches the Trash segment only while the tracker
    reports being inside an instance.

    Trash is a DUNGEON/RIFT segment, so each of its channels gates on the
    instance flag (live 2026-10-02: open-world damage, sustain and incoming
    hits each fed a trash board that belonged to no dungeon, and the next
    zone-in archived it). A dummy test and the boss-adds channel are their own
    segments and must not feed trash in the open world either. The table drives
    one REAL event of each family in both states and reads the trash channel it
    would move, so a new family that forgets the gate fails here rather than in
    a player's archive.
    """
    def _plain():
        return [_hero(PA), _foe(FOE)]

    def _with_dummy():
        return [_hero(PA), training_dummy(FOE, uid="TrainingDummy", hp=100000.0)]

    def _with_boss_and_add():
        return [_hero(PA), _foe(BOSS, uid="Nepsilon", hp=8000.0),
                _foe(FOE, uid="Trash_Pig", hp=400.0)]

    def _fire_damage(m, tr):
        m.damage.push(DamageEvent(amount=1000.0, skill="Sword_Base_Attack",
                                  source_addr=PA, target_addr=FOE, t=time.time()))

    def _fire_heal(m, tr):
        m.damage.push(DamageEvent(amount=-500.0, skill="Regen", kind=K_HEAL,
                                  source_addr=PA, target_addr=PA, t=time.time()))

    def _fire_taken(m, tr):
        m.damage.push(DamageEvent(amount=300.0, skill="Mob_Bite",
                                  source_addr=FOE, target_addr=PA,
                                  incoming=True, t=time.time()))

    def _fire_dummy_hit(m, tr):
        m.damage.push(DamageEvent(amount=700.0, skill="Sword_Base_Attack",
                                  source_addr=PA, target_addr=FOE, t=time.time()))

    def _fire_add_hit(m, tr):
        m.damage.push(DamageEvent(amount=900.0, skill="Sword_Base_Attack",
                                  source_addr=PA, target_addr=FOE, t=time.time()))

    # family, units, fire, trash-channel index, in-instance trash delta,
    # overall-channel index, overall delta, own-segment attr, own delta in/out
    # (own None = this family has no separate segment to assert)
    table = [
        ("damage", _plain, _fire_damage, 0, 1000.0, 0, 1000.0,
         None, None, None),
        ("heal", _plain, _fire_heal, 1, 500.0, 1, 500.0,
         None, None, None),
        ("damage-taken", _plain, _fire_taken, 2, 300.0, 2, 300.0,
         None, None, None),
        ("dummy", _with_dummy, _fire_dummy_hit, 0, 700.0, 0, 700.0,
         "dummy_session.group_damage", 0.0, 700.0),
        ("adds", _with_boss_and_add, _fire_add_hit, 0, 0.0, 0, 900.0,
         "boss_adds_session.group_damage", 900.0, None),
    ]

    for (family, units, fire, ch, inside, ov_ix, ov_amt,
         own_attr, own_inside, own_open) in table:
        for in_instance in (False, True):
            m = _Model()
            m.is_dungeon = bool(in_instance)
            tr = DpsTracker(m)
            m._units = units()
            tr.update()
            before = _trash_triple(tr)
            before_ov = _overall_triple(tr)
            before_own = _attr_value(tr, own_attr)

            fire(m, tr)
            tr.update()
            after = _trash_triple(tr)
            after_ov = _overall_triple(tr)
            after_own = _attr_value(tr, own_attr)

            where = f"{family} {'inside' if in_instance else 'outside'} an instance"
            # The event FIRED: it reached the overall board in both states, so
            # a green trash assertion below is a real gate, not a dropped event.
            assert after_ov[ov_ix] - before_ov[ov_ix] == ov_amt, where
            # THE invariant: outside an instance no family moves trash.
            assert after[ch] - before[ch] == (inside if in_instance else 0.0), \
                where
            # ...and the family's own segment (dummy / adds) agrees.
            if own_attr is not None:
                want = own_inside if in_instance else own_open
                if want is not None:
                    assert after_own - before_own == want, where


def _declare_boss(monkeypatch, boss_id: str) -> None:
    """Make the instance declare `boss_id` (what the scan reads per tick)."""
    import farever_companion.data.dungeons as ddata
    monkeypatch.setattr(ddata, "declared_boss_id", lambda zone: boss_id)


def _stand_at(m, x: float, y: float = 0.0, z: float = 0.0):
    """Move the PLAYER to (x, y, z).

    Every distance in the tracker scan - trash, boss, dummy, and the
    `ent.BossArea` trigger - is measured from `model.player_xyz()`, not from
    the hero entity, so walking into a room means moving that, not nudging a
    unit's coordinates. `_FakeModel` pins it to the origin.
    """
    m.player_xyz = lambda: (x, y, z)
    return m


def _boss_area(x: float = 10.0, y: float = 0.0, z: float = 0.0):
    """The `ent.BossArea` trigger volume every boss room carries.

    The declared-boss hold (core/dps_tracker_tick.py) reads this as "the
    player is actually IN the arena", so a fixture that wants the boss to
    engage has to say where the arena is. hp 0.0: it is not a fighter, and the
    scan skips it as a target.
    """
    from farever_companion.core.scene import Entity
    return Entity(addr=0xB0A, cls="ent.BossArea", unit_id="ent.BossArea",
                  x=x, y=y, z=z, hp=0.0)


def test_a_loaded_but_far_declared_boss_does_not_swallow_the_trash(monkeypatch):
    """A dungeon's DECLARED boss that is merely LOADED - present in the scene
    at full HP, far from the player - must not pre-empt the trash pull in
    front of them.

    Live 2026-10-02, Lost City of Mayda: the boss entity was in the scene from
    the start, so `in_boss_fight` latched and the whole run archived as
    "Boss Fight (Died (...))" rows with NO trash session at all - the Trash
    segment read 0 for healing and damage taken while the boss board looked
    right. The nearer trash is the fight you are actually in.

    Since 2026-10-04 "nearer trash" is necessary but NOT sufficient: between
    packs there is no trash at all, and the declared boss then engaged from
    wherever the player stood - live KoboldsMines, the boss 88.6 m away at the
    far end of the dungeon, before the boss room. That engagement armed
    `_close_trash_near_boss`, which archives the trash run and RESETS both
    the trash and the OVERALL session, so the run was wiped at the first lull
    between packs ("top dps is resetting the combat every time i go out of
    combat"). So the boss engages when it is the nearest living foe AND the
    player is in the arena (`ent.BossArea`), or on the first hit that lands on
    it - see test_hitting_a_far_declared_boss_still_engages_it for the event
    path, which is the other half of "in the room / or hitting it".
    """
    from farever_companion.core.scene import Entity

    _declare_boss(monkeypatch, "Nepsilon")
    m = _Model()
    m.is_dungeon = True
    tr = DpsTracker(m)
    trash = Entity(addr=FOE, cls="ent.Foe", unit_id="Manfish_Z1D_Caster",
                   x=5.0, y=5.0, z=5.0, hp=400.0, is_foe=True)
    far_boss = Entity(addr=BOSS, cls="ent.Foe", unit_id="Nepsilon",
                      x=200.0, y=0.0, z=0.0, hp=8000.0, is_foe=True)

    # boss loaded far away, trash right in front: the trash is the fight
    m._units = [_hero(PA), trash, far_boss]
    tr.update()
    assert tr.in_boss_fight is False

    m.damage.push(DamageEvent(amount=500.0, skill="Sword_Base_Attack",
                              source_addr=PA, target_addr=FOE, t=time.time()))
    tr.update()
    assert abs(tr.trash_session.group_damage - 500.0) < 1e-6
    assert tr.boss_session.group_damage == 0.0
    assert tr.boss_adds_session.group_damage == 0.0

    # Trash gone, so nothing is nearer than the boss. It is still 200 m away at
    # the far end of the instance and there is no arena underfoot, so the boss
    # must NOT engage - "only living foe" was never the whole rule, and reading
    # it that way is what reset the run between packs.
    m._units = [_hero(PA), far_boss]
    tr.update()
    assert tr.in_boss_fight is False, \
        "the declared boss engaged from across the instance - 2026-10-04 report"

    # Walking into the boss room is the other half of the rule: the PLAYER
    # moves to the far end of the instance, into the room's trigger volume.
    # An arena far away while the player stays put is the next room, not this
    # one - the 60 m "inside" test is measured from the player.
    _stand_at(m, 205.0)
    m._units = [_hero(PA), far_boss, _boss_area(210.0, 0.0, 0.0)]
    tr.update()
    assert tr.in_boss_fight is True


def test_hitting_a_far_declared_boss_still_engages_it(monkeypatch):
    """The hold must not deafen the meter to a boss you actually hit: the
    event path engages a boss on the first hit, wherever it is."""
    from farever_companion.core.scene import Entity

    _declare_boss(monkeypatch, "Nepsilon")
    m = _Model()
    m.is_dungeon = True
    tr = DpsTracker(m)
    trash = Entity(addr=FOE, cls="ent.Foe", unit_id="Manfish_Z1D_Caster",
                   x=5.0, y=5.0, z=5.0, hp=400.0, is_foe=True)
    far_boss = Entity(addr=BOSS, cls="ent.Foe", unit_id="Nepsilon",
                      x=200.0, y=0.0, z=0.0, hp=8000.0, is_foe=True)
    m._units = [_hero(PA), trash, far_boss]
    tr.update()
    assert tr.in_boss_fight is False

    # ...but a hit on the boss engages it immediately, despite the distance
    m.damage.push(DamageEvent(amount=300.0, skill="Sword_Base_Attack",
                              source_addr=PA, target_addr=BOSS, t=time.time()))
    tr.update()
    assert tr.in_boss_fight is True
    assert abs(tr.boss_session.group_damage - 300.0) < 1e-6


def test_a_declared_boss_engages_when_it_is_the_nearest_living_foe(monkeypatch):
    """The ordinary case is untouched: reach the boss and it engages.

    "Reach" is both halves of the hold - the boss is the nearest living foe
    AND the player is standing in the arena, so the fixture carries the room's
    `ent.BossArea` trigger. The far trash is there to keep "nearer trash beats
    the boss" in play: the boss wins only because it is both near and in
    range of the arena.
    """
    from farever_companion.core.scene import Entity

    _declare_boss(monkeypatch, "Nepsilon")
    m = _Model()
    m.is_dungeon = True
    tr = DpsTracker(m)
    near_boss = Entity(addr=BOSS, cls="ent.Foe", unit_id="Nepsilon",
                       x=5.0, y=5.0, z=5.0, hp=8000.0, is_foe=True)
    far_trash = Entity(addr=FOE, cls="ent.Foe", unit_id="Manfish_Z1D_Caster",
                       x=200.0, y=0.0, z=0.0, hp=400.0, is_foe=True)
    m._units = [_hero(PA), near_boss, far_trash, _boss_area()]
    tr.update()
    assert tr.in_boss_fight is True


def test_a_far_declared_boss_survives_a_lull_between_packs(monkeypatch):
    """The exact probe from 2026-10-04, both halves of the report at once.

    "Top DPS is resetting the combat every time I go out of combat, and it
    auto-targets the boss at the end of the dungeon - it should not target the
    boss until I am in the boss room or hitting it."

    The two halves were one bug. Between packs there is NO trash, so the
    declared boss was the nearest living foe and engaged from wherever the
    player stood (88.6 m away, far end of the instance). That armed
    `_close_trash_near_boss`, which archives the trash run and calls
    `trash_session.reset()` AND `overall_session.reset()` - so the run was
    wiped at the first lull, which is what the user saw as the reset.

    So the assertions are not just "the boss does not engage": the numbers the
    player was watching must still be there afterwards.
    """
    from farever_companion.core.scene import Entity

    _declare_boss(monkeypatch, "Reblochonk")   # live: KoboldsMines
    m = _Model()
    m.is_dungeon = True
    tr = DpsTracker(m)
    tr._instance_edge_debounce_s = 0.0
    trash = Entity(addr=FOE, cls="ent.Foe", unit_id="Kobold_Grunt",
                   x=5.0, y=0.0, z=0.0, hp=400.0, is_foe=True)
    # The 88.6 m case: loaded from the moment the zone opens, at the far end.
    far_boss = Entity(addr=BOSS, cls="ent.Foe", unit_id="Reblochonk",
                      x=88.6, y=0.0, z=0.0, hp=8000.0, is_foe=True)

    # One pack of trash, in the middle of the instance.
    m._units = [_hero(PA), trash, far_boss]
    m.damage.push(DamageEvent(amount=500.0, skill="Sword_Base_Attack",
                              source_addr=PA, target_addr=FOE, t=time.time()))
    tr.update()
    assert abs(tr.trash_session.group_damage - 500.0) < 1e-6

    # Out of combat: the pack is dead and nothing is near. Between packs this
    # is a lull, not a new encounter.
    m._units = [_hero(PA), far_boss]
    tr.update()

    # Half one: the boss at the end of the dungeon is not auto-targeted.
    assert tr.in_boss_fight is False, \
        "auto-targeted the declared boss from across the instance"
    assert tr.boss_session.group_damage == 0.0

    # Half two: the run survived the lull, on BOTH boards.
    assert abs(tr.trash_session.group_damage - 500.0) < 1e-6, \
        "the trash run was reset at the lull between packs"
    assert abs(tr.overall_session.group_damage - 500.0) < 1e-6, \
        "the overall session was reset at the lull between packs"
    assert [s for s in tr.history if s.kind == "trash"] == [], \
        "the lull archived the run as a finished encounter"

    # And a longer lull must not change that answer.
    for _ in range(5):
        tr.update()
    assert tr.in_boss_fight is False
    assert abs(tr.overall_session.group_damage - 500.0) < 1e-6

    # Entering the room still engages it - the hold must not deafen the meter.
    # The player walks the 88.6 m to the boss, into the room's trigger volume.
    _stand_at(m, 90.0)
    m._units = [_hero(PA), far_boss, _boss_area(95.0, 0.0, 0.0)]
    tr.update()
    assert tr.in_boss_fight is True
    trashed = [s for s in tr.history if s.kind == "trash"]
    assert len(trashed) == 1, "the trash run archives once, on arriving"
    assert abs(trashed[0].group_damage - 500.0) < 1e-6


def test_a_rift_boss_is_not_held_by_the_declared_boss_gate(monkeypatch):
    """A RIFT declares no boss (the two rifts share one entrance zone), so its
    engagement is unchanged: a far boss still engages when no boss is declared."""
    from farever_companion.core.scene import Entity

    _declare_boss(monkeypatch, None)      # rifts resolve to no declared boss
    m = _Model()
    m.is_rift = True
    tr = DpsTracker(m)
    trash = Entity(addr=FOE, cls="ent.Foe", unit_id="Manfish_Z1D_Caster",
                   x=5.0, y=5.0, z=5.0, hp=400.0, is_foe=True)
    far_boss = Entity(addr=BOSS, cls="ent.Foe", unit_id="Nepsilon",
                      x=200.0, y=0.0, z=0.0, hp=8000.0, is_foe=True)
    m._units = [_hero(PA), trash, far_boss]
    tr.update()
    # now the gate is scoped away: the rift boss engages as it always did
    assert tr.in_boss_fight is True


def test_incoming_damage_on_me_is_recorded_as_damage_taken():
    m = _Model()
    tr = DpsTracker(m)
    m._units = [_hero(PA), _foe(FOE)]
    tr.update()
    # mob hits me: target == me, source is NOT a hero -> my damage taken
    m.damage.push(DamageEvent(amount=30.0, skill="Mob_Bite",
                              source_addr=0x9999, target_addr=PA, t=time.time()))
    tr.update()
    s = tr.overall_session
    assert s.group_damage == 0.0                 # NOT outgoing damage
    assert s.group_heals == 0.0
    p = s.players.get("Me (You)")
    assert p is not None
    assert abs(p.damage_taken - 30.0) < 1e-6


def test_stranger_incoming_damage_does_not_mint_player_row():
    """A stranger's incoming damage (the game's generic "Player" label for
    an ungrouped rival) must not create a fake "Player" DPS row on the meter
    before solo filtering can drop it."""
    m = _Model()
    tr = DpsTracker(m)
    m._units = [_hero(PA), _foe(FOE)]
    tr.update()
    # An ungrouped stranger gets hit; target is not me and not a known hero,
    # and the game labels the target generically as "Player".
    m.damage.push(DamageEvent(amount=35.0, skill="Mob_Bite",
                              source_addr=0x9999, target_addr=0x7777,
                              incoming=True, target_name="Player",
                              t=time.time()))
    tr.update()
    assert "Player" not in tr.overall_session.players
    assert not any(p.name == "Player"
                   for p in tr.overall_session.players.values())


def test_stranger_outgoing_generic_label_never_mints_player_row():
    """Outgoing damage/heals named only by the game's generic "Player" label
    (an ungrouped rival) must never mint a phantom "Player" row either — the
    earlier guard covered incoming damage only. With no roster the unowned
    source falls back to the existing addr=0 solo-credit rule, so the hit
    lands on the local player instead of a stranger row."""
    m = _Model()
    m._units = [_hero(PA), _foe(FOE)]
    tr = DpsTracker(m)
    tr.update()

    m.damage.push(DamageEvent(amount=500.0, skill="Sword_Base_Attack",
                              source_name="Player", is_me=False,
                              source_addr=0, target_addr=FOE,
                              t=time.time()))
    m.damage.push(DamageEvent(amount=-50.0, skill="Regen",
                              source_name="Player", is_me=False,
                              source_addr=0, target_addr=PA,
                              t=time.time()))
    tr.update()

    s = tr.overall_session
    assert "Player" not in s.players
    assert not any(p.name == "Player" for p in s.players.values())
    # the unowned hits were solo-credited to the local player, not a stranger
    assert s.group_damage == 500.0
    me = next(iter(s.players.values()))
    assert me.is_me and me.total_damage == 500.0 and me.heals == 50.0


def test_in_instance_preregisters_idle_party_members():
    """Inside a dungeon/rift the tracker pre-registers every roster member
    (0 stats) onto the live meter so the overlay can auto-load the whole
    party between pulls; open world never pre-registers idle members."""
    from farever_companion.core.group_reader import GroupSnapshot, GroupMember

    m = _Model()
    m.is_dungeon = True
    m.roster = GroupSnapshot(group=0x5000, members=[
        GroupMember(name="Milo", hero=0x9999, player=0x8888),
    ])
    m._units = [_hero(PA), _foe(FOE)]
    tr = DpsTracker(m)
    tr.update()

    s = tr.overall_session
    assert "Milo" in s.players
    milo = s.players["Milo"]
    assert milo.total_damage == 0 and milo.heals == 0 and not milo.is_me
    assert any(p.is_me for p in s.players.values())   # you're in the party too
    assert "Milo" in tr.trash_session.players

    # open world (no instance): idle members are never pre-registered
    m2 = _Model()
    m2.roster = GroupSnapshot(group=0x5000, members=[
        GroupMember(name="Milo", hero=0x9999, player=0x8888),
    ])
    m2._units = [_hero(PA), _foe(FOE)]
    tr2 = DpsTracker(m2)
    tr2.update()
    assert "Milo" not in tr2.overall_session.players


def test_incoming_damage_on_teammate_records_on_them():
    m = _Model()
    tr = DpsTracker(m)
    m._units = [_hero(PA), _hero(TEAM), _foe(FOE)]
    tr.update()
    m.damage.push(DamageEvent(amount=25.0, skill="Mob_Bite",
                              source_addr=0x9999, target_addr=TEAM,
                              t=time.time()))
    tr.update()
    s = tr.overall_session
    assert s.group_damage == 0.0                 # no bogus enemy "outgoing" line
    mate = s.players.get("Teammate")
    assert mate is not None and abs(mate.damage_taken - 25.0) < 1e-6
    me = s.players.get("Me (You)")
    assert me is not None and me.damage_taken == 0.0


def test_incoming_damage_on_pet_counts_to_owner():
    m = _Model()
    tr = DpsTracker(m)
    m._units = [_hero(PA), _pet(PET, PA), _foe(FOE)]
    tr.update()
    # mob hits my pet -> counted as MY damage taken (pet merges into owner)
    m.damage.push(DamageEvent(amount=20.0, skill="Mob_Bite",
                              source_addr=0x9999, target_addr=PET,
                              t=time.time()))
    tr.update()
    s = tr.overall_session
    assert s.group_damage == 0.0
    p = s.players.get("Me (You)")
    assert p is not None and abs(p.damage_taken - 20.0) < 1e-6


def test_hero_hp_drop_records_no_taken_without_an_event():
    """Hero HP dropping with no damage event records no damage taken: only
    authoritative incoming events credit the taken channel."""
    m = _Model()
    tr = DpsTracker(m)
    m._units = [_hero(PA, hp=1000.0), _foe(FOE)]
    tr.update()                     # seeds HP
    m._units = [_hero(PA, hp=700.0), _foe(FOE)]
    tr.update()
    s = tr.overall_session
    assert s.group_damage == 0.0                  # HP is never outgoing damage
    p = s.players.get("Me (You)")
    assert p is None or p.damage_taken == 0.0


def test_taken_comes_only_from_events_across_quiet_ticks():
    """An authoritative hit credits taken once; later HP drops with the source
    quiet change nothing — there is no estimate to double-count with."""
    m = _Model()
    tr = DpsTracker(m)
    m._units = [_hero(PA, hp=1000.0), _foe(FOE)]
    tr.update()                     # seeds HP
    # same tick: event says the mob hit me for 30, and HP dropped 300
    m._units = [_hero(PA, hp=700.0), _foe(FOE)]
    m.damage.push(DamageEvent(amount=30.0, skill="Mob_Bite",
                              source_addr=0x9999, target_addr=PA, t=time.time()))
    tr.update()
    p = tr.overall_session.players.get("Me (You)")
    assert p is not None
    assert abs(p.damage_taken - 30.0) < 1e-6      # event only, HP skipped

    # next tick: events stopped, HP drops again - taken is unchanged
    m._units = [_hero(PA, hp=600.0), _foe(FOE)]
    tr.update()
    assert abs(p.damage_taken - 30.0) < 1e-6


def test_hero_death_counts_without_crediting_taken():
    """Death (hp -> 0) records the death but never damage taken, and it does
    NOT end the trash run: the run is one run across the instance, so dying
    pauses it instead of archiving and wiping it."""
    m = _Model()
    tr = DpsTracker(m)
    m.is_dungeon = True
    m._units = [_hero(PA, hp=1000.0), _foe(FOE)]
    tr.update()                     # seeds HP

    # reader alive: an event ticks through, then HP drops - taken stays 0
    m.damage.push(DamageEvent(amount=10.0, skill="Sword_Base_Attack",
                              source_addr=PA, target_addr=FOE, t=time.time()))
    m._units = [_hero(PA, hp=900.0), _foe(FOE)]
    tr.update()
    p = tr.overall_session.players.get("Me (You)")
    assert p.damage_taken == 0.0

    # more quiet HP drops change nothing
    m._units = [_hero(PA, hp=800.0), _foe(FOE)]
    tr.update()
    assert p.damage_taken == 0.0

    # death: hp drops to 0 -> death recorded, run PAUSED, nothing archived
    m._units = [_hero(PA, hp=0.0), _foe(FOE)]
    tr.update()
    assert p.damage_taken == 0.0                  # unchanged
    assert tr.overall_session.state == "PAUSED"
    assert tr.overall_session.group_damage == 10.0  # the run is intact
    assert len(tr.history) == 0                   # nothing ended the run
    assert tr.last_local_death_m > 0.0             # ...but the death was seen


def test_boss_segment_routing_and_kill_ends_fight():
    m = _Model()
    m.dungeon_boss = "Boss_Foo"
    tr = DpsTracker(m)
    m._units = [_hero(PA), _foe(BOSS, uid="Boss_Foo", hp=100000.0,
                                  is_boss=True), _foe(FOE)]
    tr.update()
    assert tr.in_boss_fight is True
    assert tr.boss_session.target_addr == BOSS

    # damage on the boss while in the boss fight
    m.damage.push(DamageEvent(amount=500.0, skill="Sword_Base_Attack",
                              source_addr=PA, target_addr=BOSS, t=time.time()))
    # damage on a nearby ADD during the boss fight lands in its own segment
    # (routed by target, not by fight phase, so boss/add skill rows never
    # merge, and pre-boss trash stays in the trash session)
    m.damage.push(DamageEvent(amount=300.0, skill="Axe_Base_Attack",
                              source_addr=PA, target_addr=FOE, t=time.time()))
    tr.update()
    assert abs(tr.boss_session.group_damage - 500.0) < 1e-6
    assert abs(tr.boss_adds_session.group_damage - 300.0) < 1e-6
    assert tr.trash_session.group_damage == 0.0
    assert abs(tr.overall_session.group_damage - 800.0) < 1e-6

    # boss leaves the scene and its killing-blow event arrives
    m._units = [_hero(PA), _foe(FOE)]
    m.damage.push(DamageEvent(amount=200.0, skill="Sword_Base_Attack",
                              kill=True, source_addr=PA, target_addr=BOSS,
                              t=time.time()))
    tr.update()
    assert tr.in_boss_fight is False
    assert len(tr.history) >= 1
    # ONE fight, ONE archive: the kill blow and the adds fold into the
    # boss's entry (500 + the 200 kill blow + the add's 300)
    boss_archives = [s for s in tr.history if s.kind == "boss"]
    assert boss_archives and abs(boss_archives[-1].group_damage - 1000.0) < 1e-6
    assert not [s for s in tr.history if s.kind == "boss_adds"]


def test_training_dummy_engages_own_boss_group():
    """Training dummies fight like bosses: the scene flags them dummy-like so
    dummy parses land in the dummy session (own group), never in trash."""
    m = _Model()
    tr = DpsTracker(m)
    m._units = [_hero(PA), training_dummy(BOSS, uid="TrainingDummy", hp=100000.0),
                _foe(FOE, uid="Trash_Pig", hp=500.0)]
    tr.update()
    assert tr.in_dummy_fight is True
    assert tr.dummy_session.target_addr == BOSS
    assert "Training Dummy" in tr.dummy_session.target_name

    m.damage.push(DamageEvent(amount=500.0, skill="Sword_Base_Attack",
                              source_addr=PA, target_addr=BOSS, t=time.time()))
    tr.update()
    assert abs(tr.dummy_session.group_damage - 500.0) < 1e-6
    assert tr.trash_session.group_damage == 0.0
    assert abs(tr.overall_session.group_damage - 500.0) < 1e-6


def test_dummy_near_reports_distance_and_the_nearest_one_wins():
    """The scene scan persists the nearest live training dummy and its distance
    (DpsTracker.dummy_near), and dummy_within() turns that into the meter's
    auto-show verdict — the dummy exception to the overlay manager's Rule E.
    A non-positive/None radius means no cap (0 = uncapped, as elsewhere)."""
    m = _Model()
    tr = DpsTracker(m)
    assert tr.dummy_near is None
    assert tr.dummy_within(20) is False

    # Two dummies: the NEARER one decides, not whichever the scan happens to
    # have seen first (the hero stands at 1,1,1; _foe defaults to 5,5,5).
    near = training_dummy(FOE, uid="TrainingDummy", hp=100000.0)
    far = training_dummy(BOSS, uid="PunchingBag", hp=100000.0)
    far.x = far.y = far.z = 40.0
    m._units = [_hero(PA), far, near]        # far one FIRST on purpose
    tr.update()
    assert tr.dummy_near is not None
    assert tr.dummy_near[0] == FOE           # nearest wins
    assert tr.dummy_near[1].startswith("🎯 ")   # same display form as rows
    dist = tr.dummy_near[3]
    assert 0.0 < dist < 20.0
    assert tr.dummy_within(20) is True
    assert tr.dummy_within(dist - 0.5) is False   # out of radius
    assert tr.dummy_within(0) is True             # 0 = no cap
    assert tr.dummy_within(None) is True

    # No dummy in the scan: nothing to auto-show for. A dead dummy (hp 0) is
    # not a dummy test target either.
    m._units = [_hero(PA), _foe(FOE, uid="Trash_Pig", hp=500.0)]
    tr.update()
    assert tr.dummy_near is None
    assert tr.dummy_within(20) is False


def test_a_dummy_inside_an_instance_is_never_a_dummy_test():
    """A training dummy is an OPEN-WORLD bench, so the scan stops reporting one
    the moment the player is inside a dungeon.

    The yards live in the hubs and an instance is never one, so a lookalike unit
    there (or a stale open-world read) is ordinary trash. Ungated it gave the
    HUD its auto-show reason, handed the meter's Rule E exception back inside a
    dungeon and — worse — opened a dummy fight whose board sat over the Dungeon
    HUD with nothing to hit. Stepping back outside, the very same scene is what
    the bench is for.
    """
    m = _Model()
    m.is_dungeon = True
    tr = DpsTracker(m)
    tr.auto_reset_zone = False          # the zone-in reset is a different subject
    m._units = [_hero(PA), training_dummy(FOE, uid="TrainingDummy", hp=100000.0)]
    tr.update()

    assert tr.dummy_zone_ok() is False
    assert tr.dummy_may_engage() is False        # armed, idle, still refused
    assert tr.dummy_near is None                 # the scan reports no dummy...
    assert tr.dummy_within(20) is False
    assert tr.in_dummy_fight is False            # ...so none engages

    # damage on it is ordinary instance damage, not a dummy parse — on both
    # decoded paths (the raw-id route reads _foe_raw_ids, which the scan fills
    # for every foe, dummy or not)
    m.damage.push(DamageEvent(amount=500.0, skill="Sword_Base_Attack",
                              source_addr=PA, target_addr=FOE, t=time.time()))
    m.damage.push(DamageEvent(amount=-100.0, skill="Priest_Holy_Light",
                              source_addr=PA, target_addr=FOE, kind=K_HEAL,
                              t=time.time()))
    tr.update()
    assert tr.dummy_session.group_damage == 0.0
    assert tr.dummy_session.group_heals == 0.0
    assert abs(tr.trash_session.group_damage - 500.0) < 1e-6

    # outside again: the same unit, the same hit, now a dummy test
    m.is_dungeon = False
    tr.update()
    assert tr.dummy_zone_ok() is True
    assert tr.dummy_near is not None
    assert tr.in_dummy_fight is True


def test_a_rift_is_an_instance_for_the_dummy_gate():
    """A rift counts exactly like a dungeon: one zone answer, two flags."""
    m = _Model()
    m.is_rift = True
    tr = DpsTracker(m)
    tr.auto_reset_zone = False
    m._units = [_hero(PA), training_dummy(FOE, uid="Dummy", hp=100000.0)]
    tr.update()

    assert tr.dummy_zone_ok() is False
    assert tr.dummy_near is None
    assert tr.in_dummy_fight is False


def test_dummy_cluster_records_a_per_target_split():
    """Three dummies standing together share ONE dummy session (which is what
    keeps the group total right), so an AoE has to be split per target or the
    meter can only ever show one lumped "Training Dummy" number. The buckets
    are filled by the same record_hit that feeds the players, so they add up to
    the group total."""
    m = _Model()
    tr = DpsTracker(m)
    m._units = [_hero(PA), training_dummy(FOE, uid="PunchingBag", hp=100000.0),
                training_dummy(BOSS, uid="PunchingBagArmor", hp=100000.0),
                training_dummy(PET, uid="PunchingBagMagicRes", hp=100000.0)]
    tr.update()                              # the scan engages the group

    for addr, dmg in ((FOE, 300.0), (BOSS, 200.0), (PET, 100.0)):
        m.damage.push(DamageEvent(amount=dmg, skill="Axe_Base_Attack",
                                  source_addr=PA, target_addr=addr,
                                  t=time.time()))
    tr.update()

    sess = tr.session_for("dummy")
    assert abs(sess.group_damage - 600.0) < 1e-6
    rows = sess.ranked_targets()
    assert abs(sess.target_total - 600.0) < 1e-6
    assert [round(t.damage) for t in rows] == [300, 200, 100]   # biggest first
    assert [t.hits for t in rows] == [1, 1, 1]
    assert len({t.name for t in rows}) == 3          # one bucket per dummy
    assert all(t.name.startswith("🎯 ") for t in rows)
    assert abs(rows[0].share_pct(sess.target_total) - 50.0) < 1e-6
    # the cluster never leaks into the other groups — least of all as a split:
    # the overall board took every one of those hits, and it must stay
    # split-free (a boss + adds + trash split is not a thing any surface shows)
    assert tr.boss_session.group_damage == 0.0
    assert tr.trash_session.group_damage == 0.0
    assert tr.overall_session.group_damage == 600.0
    assert tr.overall_session.targets == {}
    # ...and a reset (a fresh dummy test) drops the split with the session
    sess.reset()
    assert sess.ranked_targets() == []
    assert sess.target_total == 0.0


def test_dummy_cluster_pins_one_target_and_gives_each_row_its_own_hp():
    """A cluster must not make the board hop: the session's own target (its name
    and the single top bar) stays on ONE dummy — the nearest at engagement, not
    whichever the scan happened to meet first — and a hit on a different dummy
    may not move it. Each split row keeps its OWN live HP from the scan."""
    m = _Model()
    tr = DpsTracker(m)
    near = training_dummy(FOE, uid="PunchingBag", hp=100000.0)
    near.x, near.y, near.z = 2.0, 1.0, 1.0              # 1 m from the hero
    mid = training_dummy(BOSS, uid="PunchingBagArmor", hp=50000.0,
                         x=5.0, y=5.0, z=5.0)      # 6.9 m: the middle one
    far = training_dummy(PET, uid="PunchingBagMagicRes", hp=25000.0)
    far.x = far.y = far.z = 12.0                        # the farthest of the three
    m._units = [_hero(PA), far, mid, near]              # farthest scanned FIRST
    tr.update()

    # pinned to the NEAREST dummy, not to the first one the scan met
    assert tr.dummy_session.target_addr == FOE
    pinned_name = tr.dummy_session.target_name

    # one AoE swing landing on all three
    for addr, dmg in ((FOE, 300.0), (BOSS, 200.0), (PET, 100.0)):
        m.damage.push(DamageEvent(amount=dmg, skill="Axe_Base_Attack",
                                  source_addr=PA, target_addr=addr,
                                  t=time.time()))
    tr.update()
    # the pinned target and its label did not move with the hits
    assert tr.dummy_session.target_addr == FOE
    assert tr.dummy_session.target_name == pinned_name
    assert tr.dummy_session.target_hp == 100000.0

    rows = {r.addr: r for r in tr.session_for("dummy").ranked_targets()}
    assert set(rows) == {FOE, BOSS, PET}
    assert all(r.has_hp for r in rows.values())
    assert abs(rows[FOE].hp - 100000.0) < 1e-6
    assert abs(rows[BOSS].hp - 50000.0) < 1e-6
    assert abs(rows[PET].hp - 25000.0) < 1e-6

    # a dummy losing health moves only ITS row, and only its own percentage
    far.hp = 12500.0
    tr.update()
    rows = {r.addr: r for r in tr.session_for("dummy").ranked_targets()}
    assert abs(rows[PET].hp - 12500.0) < 1e-6
    assert abs(rows[PET].hp_pct - 50.0) < 1e-6
    assert abs(rows[FOE].hp_pct - 100.0) < 1e-6
    # ...and the pinned bar still reports the PINNED dummy's health
    assert tr.dummy_session.target_hp == 100000.0

    # Before the test has scored a single point, walking to another dummy
    # follows the player — it is only an IN-PROGRESS test that is pinned.
    tr.reset()
    m._units = [_hero(PA), far, mid, near]
    tr.update()
    assert tr.dummy_session.target_addr == FOE     # FOE is still nearest
    mid.x, mid.y, mid.z = 2.0, 1.0, 1.0            # now BOSS is the nearest
    near.x, near.y, near.z = 30.0, 30.0, 30.0
    tr.update()
    assert tr.dummy_session.target_addr == BOSS


def test_the_pinned_dummy_follows_the_damage_leader_and_a_click_beats_it():
    """Which dummy the board's header (and its one top bar) names is a RULE, not
    an accident of the scan. In order: a clicked split row wins outright, the
    damage leader wins next, and the nearest dummy is only the answer before any
    damage has landed. Only a STRICT lead takes the pin, so an AoE that credits
    a whole cluster equally cannot walk the bar around the yard."""
    m = _Model()
    tr = DpsTracker(m)
    near = training_dummy(FOE, uid="PunchingBag", hp=100000.0)
    near.x, near.y, near.z = 2.0, 1.0, 1.0            # nearest to the hero
    far = training_dummy(PET, uid="PunchingBagMagicRes", hp=90000.0)
    far.x = far.y = far.z = 9.0                       # farthest
    m._units = [_hero(PA), far, near]                 # the far one scans FIRST
    tr.update()
    assert tr.dummy_session.target_addr == FOE        # nothing landed yet

    def _swing(*pairs):
        for addr, dmg in pairs:
            m.damage.push(DamageEvent(amount=dmg, skill="Axe_Base_Attack",
                                      source_addr=PA, target_addr=addr,
                                      t=time.time()))
        tr.update()

    # the bigger number on the FAR dummy takes the header, not the nearest one
    _swing((PET, 500.0), (FOE, 100.0))
    assert tr.dummy_session.target_addr == PET
    assert tr.dummy_session.target_name == "🎯 Magic Res"
    # ...and the header bar now reads THAT dummy's live health, not the near one's
    assert tr.dummy_session.target_hp == 90000.0

    # an equal AoE over the cluster leaves the pin exactly where it is
    for _ in range(3):
        _swing((FOE, 100.0), (PET, 100.0))
        assert tr.dummy_session.target_addr == PET

    # a click pins the row the player chose, and the leader cannot take it back
    assert tr.select_dummy_target(FOE) is True
    assert tr.dummy_session.target_locked is True
    assert tr.dummy_session.target_addr == FOE
    assert tr.dummy_session.target_hp == 100000.0
    _swing((PET, 5000.0),)
    assert tr.dummy_session.target_addr == FOE

    # clicking the pinned row again releases the lock and the rule takes over
    assert tr.select_dummy_target(FOE) is True
    assert tr.dummy_session.target_locked is False
    tr.update()
    assert tr.dummy_session.target_addr == PET         # back to the leader

    # a fresh test starts unlocked (and the lock does not outlive it)
    assert tr.select_dummy_target(FOE) is True
    tr.reset()
    assert tr.dummy_session.target_locked is False
    assert tr.dummy_session.pinned_bucket() is None


def test_a_finished_dummy_test_becomes_the_next_tests_reference():
    """A training yard is the only place in the game where the same rotation can
    be measured twice against something that never moves, which is what turns a
    gear swap into a number. So a finished test is kept as the reference the next
    one is measured against, per skill, and handed to the UI EXACTLY ONCE (a 1 Hz
    drain must not write the same reference twice). A test too short to divide by
    is not a reference at all."""
    m = _Model()
    tr = DpsTracker(m)
    m._units = [_hero(PA), training_dummy(FOE, uid="Dummy", hp=1000000.0)]

    def _hit(amount, skill):
        m.damage.push(DamageEvent(amount=amount, skill=skill,
                                  source_addr=PA, target_addr=FOE,
                                  t=time.time()))
        tr.update()

    # --- test one: two skills over five seconds ---------------------------
    _hit(500.0, "Sword_Base_Attack")
    _hit(500.0, "Axe_Base_Attack")
    # Five seconds OF FIGHT, anchored to the last hit's own timestamp — which
    # is what a fight's span now measures (CombatSession.duration is event-
    # anchored; `time.time() - 5.0` here leaves a sub-millisecond sliver
    # between the last hit and this line, and 1000/4.9999 reads 200.01).
    tr.dummy_session.start_time = tr.dummy_session.last_event_time - 5.0
    tr.stop_dummy_test()
    snapshot = tr.take_dummy_baseline_pending()
    assert snapshot is not None
    assert set(snapshot["skills"]) == {"Sword_Base_Attack", "Axe_Base_Attack"}
    assert all(entry["dps"] > 0.0 for entry in snapshot["skills"].values())
    assert abs(snapshot["dps"] - 200.0) < 1e-6        # 1000 over 5 s
    assert tr.take_dummy_baseline_pending() is None  # exactly once

    # a swing or two is not a measurement
    tr.start_dummy_test()
    tr.update()
    _hit(10.0, "Sword_Base_Attack")
    tr.stop_dummy_test()
    assert tr.take_dummy_baseline_pending() is None

    # --- test two: the same yard, a different rotation -------------------
    tr.set_dummy_baseline(snapshot)
    tr.start_dummy_test()
    tr.update()
    _hit(900.0, "Sword_Base_Attack")     # faster
    _hit(100.0, "Axe_Base_Attack")       # slower
    _hit(700.0, "Axe_Whirlwind")         # a skill the reference never used
    tr.dummy_session.start_time = time.time() - 5.0
    cmp = tr.dummy_baseline_deltas()
    by_id = {r.skill_id: r for r in cmp.rows}
    assert set(by_id) == {"Sword_Base_Attack", "Axe_Base_Attack", "Axe_Whirlwind"}
    assert by_id["Sword_Base_Attack"].delta > 0.0
    assert by_id["Axe_Base_Attack"].delta < 0.0
    assert by_id["Axe_Whirlwind"].base_dps == 0.0
    assert by_id["Axe_Whirlwind"].delta_pct is None   # no honest % against zero
    assert cmp.live_dps > 0.0 and cmp.base_dps > 0.0
    assert cmp.delta == cmp.live_dps - cmp.base_dps

    # nothing to compare means an EMPTY result, never a table of zeros
    tr.set_dummy_baseline(None)
    assert tr.dummy_baseline_deltas().rows == []
    # ...and a session that is not the dummy test has no reference at all
    tr.set_dummy_baseline(snapshot)
    assert tr.dummy_baseline_deltas(tr.trash_session).rows == []
    # a corrupt or half-written stored snapshot leaves the feature off, not raising
    tr.set_dummy_baseline({"dps": 100.0})
    assert tr.dummy_baseline is None


def test_the_event_path_counters_say_where_events_stop():
    """A meter that reads zero has at least three causes — nothing arrived,
    nothing decoded, attribution threw it away — and the engine status cannot
    tell them apart. These counters are the answer, and the arithmetic that
    matters is that a DELIBERATE discard (the solo filter, the post-wipe hold)
    is counted apart from a real drop: folding the two together makes a healthy
    solo session look lossy."""
    m = _Model()
    tr = DpsTracker(m)
    m._units = [_hero(PA), _hero(TEAM), _foe(FOE, uid="Trash_Pig")]

    def _hit(amount=50.0, **kw):
        m.damage.push(DamageEvent(amount=amount, skill="Axe_Base_Attack",
                                  t=time.time(), **kw))
        tr.update()
        return m.damage.stats.snapshot()

    assert _hit(source_addr=PA, target_addr=FOE)["attributed"] == 1
    # a bridge hit naming you "Player" that resolves to no hero: the capture is
    # fine, the reading of WHO is not
    s = _hit(source_addr=0, target_addr=FOE, source_name="Player",
             capture_mode="proxy")
    assert s["attributed"] == 1
    assert s["dropped"] == 1
    assert s["dropped_unresolved"] == 1
    assert s["pulled"] == 2 and s["decoded"] == 2
    # ...and the drop is DERIVED, so kept + filtered + dropped always == pulled
    assert s["attributed"] + s["filtered"] + s["dropped"] == s["pulled"]
    # a zero-amount kill ping is not an event: it confirms a boss and is never
    # recorded, which is a "filtered", never a "dropped"
    s = _hit(source_addr=PA, target_addr=FOE, amount=0.0, kill=True)
    assert s["filtered"] == 1 and s["dropped"] == 1
    assert s["attributed"] + s["filtered"] + s["dropped"] == s["pulled"]


def test_the_coordinator_measures_the_bridge_payload_without_touching_the_bridge():
    """Bytes off the wire are only knowable inside the bridge, and the bridge is
    a file with its own owners — so the coordinator counts at the bridge's one
    ingest seam rather than editing the engine. It must be transparent: the
    bridge still decodes what it is handed, and the count degrades to "n/a"
    rather than breaking the listener."""
    import json

    from farever_companion.core.dps_source import DamageSourceManager

    dm = DamageSourceManager(None)
    payload = json.dumps({"t": "heartbeat"})
    assert dm.stats.snapshot()["payload_counted"] is False   # no stream yet
    dm.bridge._ingest(payload, time.time())
    s = dm.stats.snapshot()
    assert s["payload_counted"] is True
    assert s["payload_bytes"] == len(payload)
    assert s["payload_packets"] == 1
    # the real decode still happened: a heartbeat marks the DLL loaded
    assert dm.bridge.is_loaded_flag is True
    assert s["decoded"] == 0          # a heartbeat is not a combat event


def test_the_games_bare_Dummy_unit_is_a_training_dummy():
    """The open-world hubs spawn a unit whose id AND display name are just
    "Dummy". It was the one member of the family the recognizer missed: every
    variant matched (Test Dummy / Training Dummy / PunchingBag*), the commonest
    one did not — so a parse at it never engaged at all (no dummy session, no
    per-target split, no dummy auto-show). The manifest's other Dummy_* rows are
    props, not targets, and must stay out."""
    from farever_companion.data import units as udata

    assert udata.is_training_dummy("Dummy") is True
    assert udata.is_training_dummy("dummy") is True
    assert udata.is_training_dummy("🎯 Dummy") is True     # emblemed scan form
    for prop in ("Dummy_Runner", "Fleeing Bag", "Dummy_Support",
                 "Ally dummy", "Dummy_FX", "FXTest"):
        assert udata.is_training_dummy(prop) is False, prop
    # ...and the other classifiers must NOT grow a dummy: the codex tab and the
    # boss-marked drop rows each key off one of these, and a parse bench is
    # neither a boss nor a codex entry
    assert udata.is_boss("Dummy") is False
    assert udata.is_codex_unit("Dummy") is False

    # ...and the tracker engages it from the scene alone, end to end
    m = _Model()
    tr = DpsTracker(m)
    m._units = [_hero(PA), training_dummy(FOE, uid="Dummy", hp=100000.0)]
    tr.update()
    assert tr.in_dummy_fight is True
    assert tr.dummy_near is not None and tr.dummy_near[0] == FOE
    m.damage.push(DamageEvent(amount=250.0, skill="Sword_Base_Attack",
                              source_addr=PA, target_addr=FOE, t=time.time()))
    tr.update()
    assert abs(tr.session_for("dummy").group_damage - 250.0) < 1e-6
    assert tr.trash_session.group_damage == 0.0


def test_stopping_a_dummy_test_ends_it_and_holds_off_re_engagement():
    """A dummy has no end signal in the game at all: no death to read, no
    despawn, and the lull path deliberately skips boss/dummy fights — so the
    session used to stay in COMBAT and its clock ran for the rest of the
    evening, the test's DPS decaying to nothing while the player stood idle.
    stop_dummy_test() is that missing end, and `dummy_test_armed` is what keeps
    the very next tick — scan OR damage event — from silently re-opening the
    test on the same dummy."""
    m = _Model()
    tr = DpsTracker(m)
    m._units = [_hero(PA), training_dummy(FOE, uid="Dummy", hp=100000.0)]
    tr.update()
    m.damage.push(DamageEvent(amount=400.0, skill="Sword_Base_Attack",
                              source_addr=PA, target_addr=FOE, t=time.time()))
    tr.update()
    assert tr.in_dummy_fight is True
    assert tr.dummy_test_armed is True

    tr.stop_dummy_test()
    assert tr.dummy_test_armed is False
    assert tr.in_dummy_fight is False
    assert tr.dummy_session.state == "PAUSED"      # the finished parse stays up
    assert abs(tr.dummy_session.group_damage - 400.0) < 1e-6

    # the same dummy is still standing right there: the next scan must NOT
    # re-open the test the user just closed
    tr.update()
    assert tr.in_dummy_fight is False
    # ...and neither must a hit on it — the event path engages dummies too
    m.damage.push(DamageEvent(amount=400.0, skill="Sword_Base_Attack",
                              source_addr=PA, target_addr=FOE, t=time.time()))
    tr.update()
    assert tr.in_dummy_fight is False
    assert abs(tr.dummy_session.group_damage - 400.0) < 1e-6      # frozen
    # the hit is not lost, it is just not a dummy test: with none live it is
    # ordinary open-world damage — no instance is up, so it stays on the
    # overall board (the trash board is dungeon/rift content only)
    assert abs(tr.overall_session.group_damage - 800.0) < 1e-6
    assert tr.trash_session.group_damage == 0.0
    # the dummy is still in range, which is what keeps Start Test reachable
    assert tr.dummy_near is not None

    # Start re-arms: the next scan opens a FRESH test, not a resumed one
    tr.start_dummy_test()
    tr.update()
    assert tr.in_dummy_fight is True
    assert tr.dummy_session.group_damage == 0.0

    # ...and Reset is a fresh meter, so it re-arms that too
    tr.stop_dummy_test()
    assert tr.dummy_test_armed is False
    tr.reset()
    assert tr.dummy_test_armed is True



def _expire_reengage_cooldown(tr) -> None:
    """Age an auto end's re-engage cooldown past its window, in place.

    The 2026-10-03 cooldown gates the hit that re-arms an auto-ended test;
    tests that exercise the re-arm itself (not the gate) set the finish
    instant back so the cooldown has already elapsed.
    """
    tr._dummy_finished_at = (time.monotonic()
                             - DUMMY_REENGAGE_COOLDOWN_S - 0.1)


def test_hitting_the_dummy_again_starts_the_next_test():
    """A finished test re-arms on the next swing at the SAME dummy.

    Every way a dummy test ends latches the engagement off, and the only thing
    that ever cleared it was the Start Test button — so a player who finished a
    run and kept attacking watched the OLD numbers sit there with no new test
    starting (live 2026-10-02). The re-arm is the fix; the hit that ENDED the
    run must not also be the hit that starts the next one."""
    m = _Model()
    tr = DpsTracker(m)
    tr.dummy_target_s = 0.0                 # end it by hand, not by time
    m._units = [_hero(PA), training_dummy(FOE, uid="Dummy", hp=100000.0)]
    tr.update()
    m.damage.push(DamageEvent(amount=500.0, skill="Sword_Base_Attack",
                              source_addr=PA, target_addr=FOE, t=time.time()))
    tr.update()
    assert tr.in_dummy_fight is True

    tr.dummy_session.start_time = time.time() - 5.0
    tr.stop_dummy_test()                    # the player's own end signal
    assert tr.dummy_test_armed is False
    assert abs(tr.dummy_session.group_damage - 500.0) < 1e-6

    # Stop Test is NOT undone by a hit: only Start Test re-arms that one.
    time.sleep(0.01)
    m.damage.push(DamageEvent(amount=700.0, skill="Sword_Base_Attack",
                              source_addr=PA, target_addr=FOE, t=time.time()))
    tr.update()
    assert tr.in_dummy_fight is False
    assert abs(tr.dummy_session.group_damage - 500.0) < 1e-6     # frozen

    # An AUTO end (target length) is the opposite: the next swing starts a
    # fresh test rather than piling onto the finished one.
    tr.start_dummy_test()
    tr.update()
    assert tr.in_dummy_fight is True
    tr.dummy_session.start_time = time.time() - 5.0
    tr.dummy_target_s = 0.001               # ends on the very next tick
    time.sleep(0.01)
    tr.update()
    assert tr.in_dummy_fight is False
    assert tr.dummy_test_armed is False     # held off, as Stop does
    assert abs(tr.dummy_session.group_damage - 0.0) < 1e-6

    # The tracker's end holds the door for the re-engage cooldown (2026-10-03):
    # the next hit only starts a test AFTER it. Age it, then swing.
    _expire_reengage_cooldown(tr)
    m.damage.push(DamageEvent(amount=300.0, skill="Sword_Base_Attack",
                              source_addr=PA, target_addr=FOE, t=time.time()))
    tr.update()
    assert tr.in_dummy_fight is True        # auto re-armed by the hit
    assert tr.dummy_test_armed is True
    # ...and it is a FRESH test: the hit landed in the new session, not on top
    # of the finished run's numbers.
    assert abs(tr.dummy_session.group_damage - 300.0) < 1e-6


def test_walking_away_and_returning_re_arms_on_the_next_hit():
    """A test abandoned by walking out of the radius ends the same way a timed
    one does, so coming back and swinging must start a new test — the end
    signal is not the player's Stop, so the latch is not theirs to undo."""
    m = _Model()
    tr = DpsTracker(m)
    tr.dummy_target_s = 0.0
    tr._dummy_gone_grace_s = 0.0
    m._units = [_hero(PA), training_dummy(FOE, uid="Dummy", hp=100000.0)]
    tr.update()
    m.damage.push(DamageEvent(amount=400.0, skill="Sword_Base_Attack",
                              source_addr=PA, target_addr=FOE, t=time.time()))
    tr.update()
    assert tr.in_dummy_fight is True

    m._units = [_hero(PA)]                  # the dummy leaves the scan
    tr.update()
    assert tr.in_dummy_fight is False
    # the self-concluded end resets the surface (the run is archived)
    assert tr.dummy_session.group_damage == 0.0

    m._units = [_hero(PA), training_dummy(FOE, uid="Dummy", hp=100000.0)]
    tr.update()
    # the walk-away end was the tracker's: the return swing waits out the
    # re-engage cooldown (2026-10-03)
    _expire_reengage_cooldown(tr)
    m.damage.push(DamageEvent(amount=250.0, skill="Sword_Base_Attack",
                              source_addr=PA, target_addr=FOE, t=time.time()))
    tr.update()
    assert tr.in_dummy_fight is True
    assert abs(tr.dummy_session.group_damage - 250.0) < 1e-6     # a fresh run


def test_a_hit_on_a_different_dummy_never_re_arms_a_finished_test():
    """The re-arm is keyed on the dummy the finished test belonged to: a swing
    at some OTHER dummy is not "hitting the dummy again" and must not quietly
    start a test the player did not ask for."""
    m = _Model()
    tr = DpsTracker(m)
    tr.dummy_target_s = 0.0
    m._units = [_hero(PA), training_dummy(FOE, uid="Dummy", hp=100000.0)]
    tr.update()
    m.damage.push(DamageEvent(amount=500.0, skill="Sword_Base_Attack",
                              source_addr=PA, target_addr=FOE, t=time.time()))
    tr.update()
    tr.dummy_session.start_time = time.time() - 5.0
    tr.stop_dummy_test()
    tr._dummy_stopped_by_hand = False       # pretend an AUTO end, worst case

    time.sleep(0.01)
    m.damage.push(DamageEvent(amount=900.0, skill="Sword_Base_Attack",
                              source_addr=PA, target_addr=BOSS, t=time.time()))
    tr.update()
    assert tr.in_dummy_fight is False
    assert abs(tr.dummy_session.group_damage - 500.0) < 1e-6


def test_a_dummy_the_player_is_not_at_cannot_own_the_live_board():
    """Live 2026-09-29: "top dps has no damage" while the player fought real
    mobs. The scan reads a training dummy through walls and across the zone, and
    presence alone engaged the dummy fight — and because a dummy fight has no
    end signal of its own, that EMPTY dummy session then owned
    session_for("auto") for the rest of the session: the meter showed zeroes
    through a whole fight while the damage piled up in trash. Engagement is now
    gated on the Dummy Range setting's radius (the same number the meter's
    auto-show and the Test Dummy HUD use), and the live board only follows a
    dummy the player is actually at."""
    m = _Model()
    tr = DpsTracker(m)
    far = training_dummy(FOE, uid="Dummy", hp=100000.0)
    far.x, far.y, far.z = 60.0, 60.0, 60.0            # ~103 m away, past the 20 m
    m._units = [_hero(PA), far, _foe(BOSS, uid="Trash_Pig", hp=500.0)]
    tr.update()
    # the scan still reports it — that reading is what feeds the radius test
    assert tr.dummy_near is not None and tr.dummy_near[3] > 100.0
    assert tr.dummy_in_range() is False
    # ...but it neither engages nor owns the board
    assert tr.in_dummy_fight is False
    assert tr.session_for("auto") is not tr.dummy_session

    m.damage.push(DamageEvent(amount=900.0, skill="Sword_Base_Attack",
                              source_addr=PA, target_addr=BOSS, t=time.time()))
    tr.update()
    # open world, no dummy test in range: the hit is ordinary overall
    # damage, not trash-board content
    assert abs(tr.overall_session.group_damage - 900.0) < 1e-6
    assert tr.trash_session.group_damage == 0.0
    assert tr.session_for("auto") is tr.overall_session
    assert tr.dummy_session.group_damage == 0.0

    # the radius is the one number that decides it: 0 is the documented "no
    # cap", and a cap the dummy is inside engages as it always did
    tr.reset()
    tr.dummy_range = 0.0
    tr.update()
    assert tr.in_dummy_fight is True


def test_a_dummy_test_ends_itself_after_a_lull_and_hands_the_board_back():
    """A dummy has no game end signal, and standing at one keeps the client
    in combat until the player is ~30 m out — so a run that simply stopped
    landing hits ran on, the meter reading IN COMBAT with the finished numbers
    under it (live 2026-10-03). A damage lull concludes it the way a trash pull
    concludes: the run is archived, the engagement holds off, and the live
    board goes back to READY. The next swing re-arms."""
    m = _Model()
    tr = DpsTracker(m)
    tr.dummy_target_s = 0.0                 # the lull is the end under test
    m._units = [_hero(PA), training_dummy(FOE, uid="Dummy", hp=100000.0)]
    tr.update()
    m.damage.push(DamageEvent(amount=500.0, skill="Sword_Base_Attack",
                              source_addr=PA, target_addr=FOE, t=time.time()))
    tr.update()
    assert tr.in_dummy_fight is True
    assert tr.session_for("auto") is tr.dummy_session

    # no hit for longer than the lull: the test concludes on its own
    tr.dummy_session.last_event_time = time.time() - (DUMMY_REARM_S + 1.0)
    tr.update()
    assert tr.in_dummy_fight is False
    # The finished run STAYS on the dummy surface. The player is still standing
    # at the dummy; clearing it left the Test Dummy HUD blank for the whole idle
    # stretch, with the run archived and visible nowhere (live 2026-10-03: "the
    # auto restart did not work", three minutes out of combat). The next hit
    # re-arms and opens a fresh session, so nothing stale survives.
    assert tr.dummy_session.state == "PAUSED"
    assert abs(tr.dummy_session.group_damage - 500.0) < 1e-6
    assert tr.dummy_test_armed is False      # held off, as Stop does

    # ...and the live board is handed straight back, not left on the finished
    # run's aggregate
    live = tr.session_for("auto")
    assert live is not tr.dummy_session
    assert live.state == "READY"

    # the next swing AFTER the re-engage cooldown starts a fresh test
    # (2026-10-03: the tracker's own end holds the door briefly)
    _expire_reengage_cooldown(tr)
    m.damage.push(DamageEvent(amount=250.0, skill="Sword_Base_Attack",
                              source_addr=PA, target_addr=FOE, t=time.time()))
    tr.update()
    assert tr.in_dummy_fight is True
    assert abs(tr.dummy_session.group_damage - 250.0) < 1e-6


def test_a_long_idle_keeps_the_finished_run_on_the_dummy_surface():
    """Three minutes out of combat must not leave the board blank.

    The reported symptom: the auto-end fired (the run was archived, the
    engagement held off) and the Test Dummy HUD went to its empty READY hint
    anyway, because releasing the board also reset the dummy session. Nothing
    was wrong with the rule — the run simply had nowhere left to be seen, for
    as long as the player stood there waiting.

    So the idle end releases the OPEN-WORLD board (the aggregate that reads IN
    COMBAT anywhere in a yard) and keeps the dummy session. Walking out of the
    radius is the opposite case and still clears it: the player left.
    """
    m = _Model()
    tr = DpsTracker(m)
    tr.dummy_target_s = 0.0                 # the idle end is the end under test
    m._units = [_hero(PA), training_dummy(FOE, uid="Dummy", hp=100000.0)]
    tr.update()
    m.damage.push(DamageEvent(amount=500.0, skill="Sword_Base_Attack",
                              source_addr=PA, target_addr=FOE, t=time.time()))
    tr.update()
    assert tr.in_dummy_fight is True

    tr.dummy_session.last_event_time = time.time() - 180.0
    tr.update()
    assert tr.in_dummy_fight is False
    # the run is still there to read, at full length, minutes later
    assert tr.dummy_session.state == "PAUSED"
    assert abs(tr.dummy_session.group_damage - 500.0) < 1e-6
    # ...and it was remembered, so the next test has something to compare to
    assert any(abs(h.group_damage - 500.0) < 1e-6 for h in tr.history)

    # the open-world board is still released: no finished aggregate reading
    # IN COMBAT while standing in the yard
    live = tr.session_for("auto")
    assert live is not tr.dummy_session
    assert live.state == "READY"

    # walking away is different: there the surface goes back to its hint.
    # The swing still waits out the re-engage cooldown (the end was the
    # tracker's, not the player's) — aged here, then hit.
    tr._dummy_gone_grace_s = 0.0
    _expire_reengage_cooldown(tr)
    m.damage.push(DamageEvent(amount=250.0, skill="Sword_Base_Attack",
                              source_addr=PA, target_addr=FOE, t=time.time()))
    tr.update()
    assert tr.in_dummy_fight is True, "the next swing re-arms a fresh test"
    m._units = [_hero(PA)]                  # the dummy leaves the scan
    tr.update()
    assert tr.in_dummy_fight is False
    assert tr.dummy_session.state == "READY"
    assert tr.dummy_session.group_damage == 0.0


def test_the_auto_rearm_wait_is_the_trackers_own_number():
    """The idle re-arm is a setting, so the rule reads the tracker's number and
    not the module constant: a custom wait ends the test sooner, and 0 turns the
    auto end off entirely (Stop Test / target length only)."""
    m = _Model()
    tr = DpsTracker(m)
    tr.dummy_target_s = 0.0
    tr.dummy_rearm_s = 2.0
    m._units = [_hero(PA), training_dummy(FOE, uid="Dummy", hp=100000.0)]
    tr.update()
    m.damage.push(DamageEvent(amount=500.0, skill="Sword_Base_Attack",
                              source_addr=PA, target_addr=FOE, t=time.time()))
    tr.update()
    assert tr.in_dummy_fight is True

    # idle for less than the custom wait: still running
    tr.dummy_session.last_event_time = time.time() - 1.0
    tr.update()
    assert tr.in_dummy_fight is True
    # ...past it: the test ends itself
    tr.dummy_session.last_event_time = time.time() - 3.0
    tr.update()
    assert tr.in_dummy_fight is False

    # 0 disables the auto end: an idle test just keeps running
    tr.dummy_rearm_s = 0.0
    tr.start_dummy_test()
    tr.update()
    assert tr.in_dummy_fight is True
    tr.dummy_session.last_event_time = time.time() - 60.0
    tr.update()
    assert tr.in_dummy_fight is True


def test_walking_out_of_the_radius_clears_the_meter_to_ready():
    """Walking away ends the test, and the finished run must not sit on the
    live board as the overall aggregate — a dummy keeps the client IN COMBAT,
    so that board read as a fight that never ended. The self-concluded end
    resets the test's own board too, so the next swing starts clean; the run is
    archived, not lost."""
    m = _Model()
    tr = DpsTracker(m)
    tr.dummy_target_s = 0.0
    tr._dummy_gone_grace_s = 0.0
    m._units = [_hero(PA), training_dummy(FOE, uid="Dummy", hp=100000.0)]
    tr.update()
    m.damage.push(DamageEvent(amount=500.0, skill="Sword_Base_Attack",
                              source_addr=PA, target_addr=FOE, t=time.time()))
    tr.update()
    assert tr.in_dummy_fight is True

    m._units = [_hero(PA)]                  # the dummy leaves the scan
    tr.update()
    assert tr.in_dummy_fight is False
    assert tr.session_for("auto").state == "READY"
    assert tr.session_for("auto").group_damage == 0.0
    # the test's own surface reset itself; the run is archived in history
    assert tr.session_for("dummy").group_damage == 0.0
    assert any(abs(h.group_damage - 500.0) < 1e-6 for h in tr.history)


def test_the_game_in_combat_badge_is_held_down_when_a_dummy_test_ends():
    """The client keeps isInCombat set anywhere inside a training yard, so a
    finished test left the badge and the armed clock reading IN COMBAT off a
    fight that was over. The test's own end suppresses it until the game
    catches up or a real swing lands."""
    m = _Model()
    m.combat_state_value = {"in_combat": True, "combat_start": 1.0,
                            "combat_id": 7}
    tr = DpsTracker(m)
    tr.dummy_target_s = 0.0
    tr._dummy_gone_grace_s = 0.0
    m._units = [_hero(PA), training_dummy(FOE, uid="Dummy", hp=100000.0)]
    tr.update()
    assert tr.combat_state is not None and tr.combat_state.get("in_combat") is True
    m.damage.push(DamageEvent(amount=500.0, skill="Sword_Base_Attack",
                              source_addr=PA, target_addr=FOE, t=time.time()))
    tr.update()

    m._units = [_hero(PA)]                  # end the test by walking away
    tr.update()
    assert tr.in_dummy_fight is False
    assert tr.combat_state.get("in_combat") is False
    assert tr.armed_fight() is None

    # a real swing clears the hold: combat shows again
    m._units = [_hero(PA), _foe(BOSS, uid="Trash_Pig", hp=500.0)]
    time.sleep(0.01)
    m.damage.push(DamageEvent(amount=100.0, skill="Sword_Base_Attack",
                              source_addr=PA, target_addr=BOSS, t=time.time()))
    tr.update()
    assert tr._dummy_combat_suppressed is False


def test_walking_out_of_the_dummy_radius_ends_the_test():
    """The dummy has no end signal in the game, so the one the tracker CAN
    compute — the dummy no longer inside the radius — ends an abandoned test.
    Without it the fight stayed in COMBAT (feeding capture a combat cadence from
    an empty yard) and kept the finished test pinned over whatever the player
    did next. The grace is what keeps a single unreadable scan pass from ending
    a live test."""
    m = _Model()
    tr = DpsTracker(m)
    dummy = training_dummy(FOE, uid="Dummy", hp=100000.0)
    m._units = [_hero(PA), dummy]
    tr.update()
    m.damage.push(DamageEvent(amount=500.0, skill="Sword_Base_Attack",
                              source_addr=PA, target_addr=FOE, t=time.time()))
    tr.update()
    assert tr.in_dummy_fight is True
    assert tr.session_for("auto") is tr.dummy_session

    dummy.x, dummy.y, dummy.z = 80.0, 80.0, 80.0      # walked away, or it went
    tr._dummy_gone_grace_s = 60.0
    tr.update()
    assert tr.in_dummy_fight is True                    # one pass is not proof
    assert tr.session_for("auto") is tr.dummy_session   # a live test still shows
    tr._dummy_gone_grace_s = 0.0                        # out of range long enough
    tr.update()
    assert tr.in_dummy_fight is False
    assert tr.session_for("auto") is not tr.dummy_session   # board handed back
    # the self-concluded end resets the surface too: the run is archived, not
    # left on the board for the next swing to pile onto
    assert tr.dummy_session.state == "READY"
    assert tr.dummy_session.group_damage == 0.0
    assert any(abs(h.group_damage - 500.0) < 1e-6 for h in tr.history)
    # walking back inside the radius is a fresh test, not a resumed one.
    # Every tracker-concluded end now disarms and holds the re-engage
    # cooldown (2026-10-03), so the new test starts on the first HIT after
    # the player returns — presence alone no longer re-engages.
    _expire_reengage_cooldown(tr)
    dummy.x, dummy.y, dummy.z = 2.0, 1.0, 1.0
    m.damage.push(DamageEvent(amount=150.0, skill="Sword_Base_Attack",
                              source_addr=PA, target_addr=FOE, t=time.time()))
    tr.update()
    assert tr.in_dummy_fight is True
    assert tr.dummy_session.group_damage == 150.0


def test_a_dummy_test_ends_itself_when_the_target_length_is_spent():
    """A dummy fight is the one encounter nothing in the scene concludes, and
    two runs of different lengths are not comparable — so the target length is
    the end signal a MEASURED test gets. It ends through the same
    _finish_dummy_test every other route uses, so the run is archived and
    remembered like one the player stopped by hand.

    The clock is the session's own, which only starts on the first hit: standing
    at an idle dummy must not spend the target.
    """
    m = _Model()
    tr = DpsTracker(m)
    m._units = [_hero(PA), training_dummy(FOE, uid="Dummy", hp=100000.0)]
    tr.update()
    clock = tr.dummy_clock()
    assert clock.enabled is True and clock.target_s == 60.0
    assert clock.started is False                 # no hit yet
    assert clock.reached is False
    assert clock.remaining_s == 60.0
    tr.update()
    assert tr.in_dummy_fight is True
    assert tr.dummy_clock().started is False       # still idle, still 60.0 left

    m.damage.push(DamageEvent(amount=500.0, skill="Sword_Base_Attack",
                              source_addr=PA, target_addr=FOE, t=time.time()))
    tr.update()
    assert tr.in_dummy_fight is True
    tr.dummy_session.start_time = time.time() - 59.0
    tr.update()
    assert tr.in_dummy_fight is True              # one second to go
    assert tr.dummy_clock().reached is False
    assert abs(tr.dummy_clock().remaining_s - 1.0) < 0.2

    tr.dummy_session.start_time = time.time() - 60.0
    tr.update()
    assert tr.in_dummy_fight is False             # the target spent it
    assert tr.dummy_session.state == "PAUSED"
    assert abs(tr.dummy_session.group_damage - 500.0) < 1e-6
    # ...and it was remembered, because a run that ended on its own is exactly
    # the run worth comparing the next one against.
    assert tr.take_dummy_baseline_pending() is not None
    # Ending on the target is ending like any other: the finished run keeps the
    # board while the player is still standing at the dummy, so the numbers are
    # still on screen to read when it stops. Engagement is held off (as Stop
    # does) so the next scan cannot wipe them, and the latch that keeps a
    # finished run reading as "reached" is cleared by the next test.
    assert tr.session_for("auto") is tr.dummy_session
    assert tr.dummy_test_armed is False
    assert tr.dummy_target_reached is True
    tr.update()
    assert tr.in_dummy_fight is False             # no silent second test
    tr.start_dummy_test()                        # the player's call, not the scan's
    tr.update()
    assert tr.in_dummy_fight is True
    assert tr.dummy_target_reached is False       # a fresh test starts over
    assert tr.dummy_clock().remaining_s == 60.0


def test_a_pet_hit_re_arming_a_finished_test_clears_the_target_reached_latch():
    """A pet hitting the dummy must start a NEW test, not end one instantly.

    The loop this pins (live 2026-10-03, 25 archived runs in 8 seconds, every
    one 0.0s and named "Target Time Reached"): a test spends its target length,
    `dummy_target_reached` latches so the FINISHED run's countdown reads
    complete, and then a pet hit re-arms it. The re-arm reaches the tracker
    through the EVENT path (dps_tracker_events), which can see the dummy before
    the next scene scan — and that path used to repeat the engage bookkeeping
    without clearing the latch. The next tick therefore read the finished
    test's own latch as the brand-new session's end signal and archived a
    zero-length run, over and over, for as long as the pet kept swinging.

    Every engage now goes through `DpsTrackerDummy._begin_dummy_test`, so the
    three callers cannot drift apart again.
    """
    m = _Model()
    tr = DpsTracker(m)
    m._units = [_hero(PA), _pet(PET, PA), training_dummy(FOE, uid="Dummy",
                                                         hp=100000.0)]
    tr.dummy_target_s = 60.0
    tr.update()
    assert tr.in_dummy_fight is True

    # Spend the target length: the finished run latches "reached".
    tr.dummy_session.start_time = time.time() - 60.0
    tr.update()
    assert tr.in_dummy_fight is False
    assert tr.dummy_target_reached is True

    # The pet keeps hitting the same dummy, so the test re-arms — after the
    # re-engage cooldown the tracker's own end started (2026-10-03).
    _expire_reengage_cooldown(tr)
    m.damage.push(DamageEvent(amount=81.0, skill="Staff_SummonDemon_PetHit",
                              source_addr=PET, target_addr=FOE,
                              t=time.time()))
    tr.update()
    assert tr.in_dummy_fight is True, "the pet's hit must start the next test"
    assert tr.dummy_target_reached is False, "a fresh test starts over"
    clock = tr.dummy_clock()
    assert clock.reached is False
    assert clock.remaining_s > 59.0, "the new run has its whole target ahead"


def _finished_test(tr, amount: float, seconds: float, skill="Sword_Base_Attack"):
    """Run one complete dummy test of `seconds` at `amount` damage, and end it.

    The helper exists because the BEST rule is only interesting across runs:
    a test of one shape proves nothing about whether a worse run can lower the
    record. Damage is sized so DPS = amount / seconds exactly, which is what
    makes "beat it by one point" expressible without rounding surprises.
    """
    _run_at(tr, amount, seconds, skill=skill)
    tr.stop_dummy_test()
    return tr.take_dummy_baseline_pending()


def test_the_personal_best_is_only_ever_beaten():
    """A reference that only remembers the last run quietly lowers a player's own
    ceiling every time they have a bad evening. The best is a different
    lifetime from the reference: a strictly better run replaces it, a worse one
    does not, and a TIE does not either (a record that flickered between two
    equal runs, resetting its age each time, is a record nobody can read).
    """
    m = _Model()
    tr = DpsTracker(m)
    tr.dummy_target_s = 0.0

    first = _finished_test(tr, 3000.0, 30.0)        # 100/s
    assert first and abs(first["dps"] - 100.0) < 0.5      # clock drift
    assert tr.dummy_best is not None
    assert abs(tr.dummy_best["dps"] - 100.0) < 0.5
    pending = tr.take_dummy_best_pending()
    assert pending is not None and abs(pending["dps"] - 100.0) < 0.5
    assert tr.take_dummy_best_pending() is None      # handed over exactly once

    # a worse run: the reference moves, the record does not
    _finished_test(tr, 1500.0, 30.0)                # 50/s
    assert abs(tr.dummy_best["dps"] - 100.0) < 0.5
    assert tr.take_dummy_best_pending() is None     # nothing new to write

    # a tie is not a record — checked on the RULE, not on two live runs: two
    # nominally identical 30 s runs differ by however long the machine took
    # between the two reads, and a "tie" that drifts is a flaky test rather
    # than a property of anything.
    best_dps = tr.dummy_best["dps"]
    skills = {"Sword_Base_Attack": {"name": "Sword", "dps": best_dps}}
    assert tr._is_dummy_best({"dps": best_dps, "skills": skills}) is False
    assert tr._is_dummy_best({"dps": best_dps + 0.01, "skills": skills}) is True
    assert tr._is_dummy_best({"dps": best_dps, "skills": {}}) is False   # no skills
    assert tr._is_dummy_best({}) is False

    # a strictly better run: the record, and the write, move together
    _finished_test(tr, 3300.0, 30.0)                # 110/s
    assert abs(tr.dummy_best["dps"] - 110.0) < 0.5
    assert abs(tr.take_dummy_best_pending()["dps"] - 110.0) < 0.5
    assert tr.dummy_best["skills"], "the best keeps its per-skill split"


def test_the_best_is_remembered_per_profile_and_deltas_say_so():
    """The live test is measured against BOTH records at once, because the two
    answer different questions: "did that change help" is about the run before
    this one, "is this gear better" is about everything. A run that is worse
    than the best but better than last time is a normal Tuesday and must read
    as both.
    """
    m = _Model()
    tr = DpsTracker(m)
    tr.dummy_target_s = 0.0
    _finished_test(tr, 3000.0, 30.0)                # best 100/s, also reference
    tr.set_dummy_baseline(tr.take_dummy_baseline_pending())

    # a run at 80/s: -20% vs the last test, -20% vs the best — no NEW BEST
    _run_at(tr, 2400.0, 30.0)
    best = tr.dummy_best_deltas(tr.dummy_session)
    assert best.has_best is True
    assert best.is_best is False
    assert abs(best.best_dps - 100.0) < 0.5
    assert abs(best.live_dps - 80.0) < 0.5      # clock drift, not rounding
    assert abs(best.gap_pct - (-20.0)) < 0.5
    assert best.best_duration_s == 30.0

    # a record-breaking run says so and carries no percentage: there is nothing
    # left to be measured against
    _run_at(tr, 4500.0, 30.0)                       # 150/s
    beat = tr.dummy_best_deltas(tr.dummy_session)
    assert beat.is_best is True
    assert beat.gap_pct is None

    # nothing to say: a non-dummy session, or a test that has landed no hits
    assert tr.dummy_best_deltas(tr.trash_session).has_best is False
    fresh = CombatSession("Training Dummy", kind="dummy")
    assert tr.dummy_best_deltas(fresh).has_best is False


def _run_at(tr, amount: float, seconds: float, skill="Sword_Base_Attack"):
    """Start a dummy test, land `amount`, and leave it RUNNING (not finished).

    Re-arms first: a finished test latches the engagement off (so the next scan
    cannot open a second test on the same dummy and wipe the numbers), which
    means every run after the first one has to be asked for the way a player
    asks — Start Test.
    """
    m = tr.model
    m._units = [_hero(PA), training_dummy(FOE, uid="Dummy", hp=1000000.0)]
    tr.start_dummy_test()
    tr.update()
    m.damage.push(DamageEvent(amount=amount, skill=skill, source_addr=PA,
                              target_addr=FOE, t=time.time()))
    tr.update()
    tr.dummy_session.start_time = time.time() - seconds


def test_a_test_that_took_damage_is_flagged_as_not_a_clean_measurement():
    """A dummy does not fight back, so damage taken during a test is the
    rotation (a dot, a reflect) or the world walking in — either way the DPS on
    screen is not the DPS of the build, and a player about to compare runs needs
    to be told BEFORE they trust the number.

    The measure is a SHARE of the test's own damage, not an absolute, because
    the two runs being compared can be different lengths: 2,000 damage taken is
    a disaster in a 10 s test and a rounding error in a 3-minute one.
    """
    m = _Model()
    tr = DpsTracker(m)
    tr.dummy_target_s = 0.0
    tr.dummy_taken_limit_pct = 5.0

    # a clean run: no damage in, nothing to say
    _run_at(tr, 10000.0, 20.0)
    share = tr.dummy_taken_share()
    assert share.dirty is False and share.pct == 0.0
    assert share.dealt == 10000.0

    # 1% taken: real damage, still under the limit — measured, not flagged
    _take(m, 100.0)
    tr.update()
    share = tr.dummy_taken_share()
    assert abs(share.pct - 1.0) < 0.01
    assert share.dirty is False

    # over the limit: the flag
    _take(m, 800.0)
    tr.update()
    share = tr.dummy_taken_share()
    assert abs(share.pct - 9.0) < 0.01
    assert share.dirty is True
    assert share.limit_pct == 5.0

    # a limit of 0 means "flag ANY damage taken"
    tr.dummy_taken_limit_pct = 0.0
    assert tr.dummy_taken_share().dirty is True

    # and the flag is measured, not refused: the run is still recorded, with
    # its own taken share on the record so a LATER comparison can see that the
    # run it measures against was messy too
    tr.dummy_taken_limit_pct = 5.0
    tr.stop_dummy_test()
    ref = tr.take_dummy_baseline_pending()
    assert ref["dps"] > 0.0
    assert ref["taken"] == 900.0
    assert abs(ref["taken_pct"] - 9.0) < 0.01
    tr.set_dummy_baseline(ref)          # it is the next run's reference
    cmp_ = tr.dummy_baseline_deltas(tr.dummy_session)
    assert cmp_.taken_pct == ref["taken_pct"]
    assert tr.dummy_best["taken_pct"] > 5.0
    assert tr.dummy_best_deltas().best_taken_pct > 5.0


def test_a_record_saved_before_the_taken_flag_reads_as_clean():
    """An old reference (or a corrupt one) has no `taken_pct` key, and must
    read as 0 — nothing known is not a warning. Reading it as dirty instead
    would have every pre-existing record claim to be suspect."""
    from farever_companion.core.dps_dummy import _snapshot_taken_pct

    assert _snapshot_taken_pct({}) == 0.0
    assert _snapshot_taken_pct({"dps": 100.0, "skills": {}}) == 0.0
    assert _snapshot_taken_pct(None) == 0.0
    assert _snapshot_taken_pct({"taken_pct": "bad"}) == 0.0
    assert _snapshot_taken_pct({"taken_pct": 12.5}) == 12.5


def _take(m, amount: float):
    """Something in the world hits the player mid-test."""
    m.damage.push(DamageEvent(amount=amount, skill="Mob_Bite",
                              source_addr=0x9999, target_addr=PA,
                              t=time.time()))


def test_damage_taken_during_a_dummy_test_lands_on_the_test():
    """Both channels of a test belong to the same board. The outgoing path
    routes dummy damage to the dummy session, and the TAKEN path had no dummy
    branch at all — so anything that hit you while you were on the dummy went to
    trash and the test's taken read 0.0 no matter what actually landed, which is
    the one number a "clean run" claim is made of."""
    m = _Model()
    tr = DpsTracker(m)
    m._units = [_hero(PA), training_dummy(FOE, uid="Dummy", hp=100000.0)]
    tr.update()
    m.damage.push(DamageEvent(amount=500.0, skill="Sword_Base_Attack",
                              source_addr=PA, target_addr=FOE, t=time.time()))
    tr.update()
    m.damage.push(DamageEvent(amount=340.0, skill="Mob_Bite", source_addr=0x9999,
                              target_addr=PA, t=time.time()))
    tr.update()
    me = tr.dummy_session.players.get("Me (You)")
    assert me is not None
    assert abs(me.damage_taken - 340.0) < 1e-6
    assert abs(tr.dummy_session.group_taken - 340.0) < 1e-6
    assert tr.trash_session.group_taken == 0.0        # not the trash board's
    assert abs(tr.overall_session.group_taken - 340.0) < 1e-6


def test_a_target_length_of_zero_leaves_the_test_free_running():
    """0 = no target, the codebase's usual uncapped convention: the test runs
    until the player stops it or walks out of the radius, and the HUD has nothing
    to count down to."""
    m = _Model()
    tr = DpsTracker(m)
    tr.dummy_target_s = 0.0
    m._units = [_hero(PA), training_dummy(FOE, uid="Dummy", hp=100000.0)]
    tr.update()
    m.damage.push(DamageEvent(amount=500.0, skill="Sword_Base_Attack",
                              source_addr=PA, target_addr=FOE, t=time.time()))
    tr.update()
    tr.dummy_session.start_time = time.time() - 600.0
    tr.update()
    assert tr.in_dummy_fight is True              # ten minutes later, still going
    clock = tr.dummy_clock()
    assert clock.enabled is False and clock.reached is False
    assert clock.fraction == 0.0 and clock.over_s == 0.0


def test_two_dummies_with_the_same_name_stay_apart_in_the_split():
    """The split is keyed by target ADDRESS, not by name: dummies that share a
    display name — the common shape in a training yard — must not be folded
    into one bucket (the HUD numbers the rows instead)."""
    m = _Model()
    tr = DpsTracker(m)
    m._units = [_hero(PA), training_dummy(FOE, uid="TrainingDummy", hp=100000.0),
                training_dummy(BOSS, uid="TrainingDummy", hp=100000.0)]
    tr.update()
    for addr in (FOE, BOSS):
        m.damage.push(DamageEvent(amount=150.0, skill="Axe_Base_Attack",
                                  source_addr=PA, target_addr=addr,
                                  t=time.time()))
    tr.update()
    rows = tr.session_for("dummy").ranked_targets()
    assert len(rows) == 2
    assert abs(sum(t.damage for t in rows) - 300.0) < 1e-6
    assert rows[0].name == rows[1].name              # identical labels...
    assert rows[0].addr != rows[1].addr              # ...but two dummies


def test_an_address_less_yard_falls_back_to_the_scene_cluster():
    """No per-dummy addresses: the yard still reads as its three dummies.

    When the decoded hits carry no target address, every hit lands in ONE
    name-keyed bucket, and appending that bucket beside the scene's three rows
    made a three-dummy yard read as four - one of them not a dummy at all. The
    split now falls back to the cluster: the rows are the yard's, and the
    address-less damage folds onto the dummy the tracker believes is the target
    (the nearest at engagement / the clicked row). That ONE row is reported as
    estimated — `dummy_split_estimated_keys` — so the HUD can mark it without
    telling the player the two measured dummies are guesses too.
    """
    m = _Model()
    tr = DpsTracker(m)
    m._units = [_hero(PA),
                training_dummy(FOE, uid="Dummy", hp=100000.0, x=2.0, y=0.0, z=0.0),
                training_dummy(BOSS, uid="Dummy", hp=100000.0, x=3.0, y=0.0, z=0.0),
                training_dummy(0xB0B, uid="Dummy", hp=100000.0, x=4.0, y=0.0, z=0.0)]
    tr.update()
    assert len(tr.dummy_cluster) == 3
    assert tr.dummy_session.target_addr == FOE          # nearest is the pin

    # an old-bridge hit: a dummy name, no address at all
    m.damage.push(DamageEvent(amount=700.0, skill="Sword_Base_Attack",
                              source_addr=PA, target_addr=0,
                              target_name="Dummy", t=time.time()))
    tr.update()

    sess = tr.session_for("dummy")
    rows = tr.dummy_split_targets(sess)
    assert len(rows) == 3                                # the yard, not four
    assert tr.dummy_split_is_estimated() is True
    # ...and it is the PINNED row that is the estimate, not the whole split
    assert tr.dummy_split_estimated_keys() == {FOE}
    by_addr = {r.addr: r for r in rows}
    assert abs(by_addr[FOE].damage - 700.0) < 1e-6       # on the pinned dummy
    assert by_addr[BOSS].damage == 0.0
    assert by_addr[0xB0B].damage == 0.0
    # the rows the fold did NOT touch are measurements, so they are not marked
    assert BOSS not in tr.dummy_split_estimated_keys()
    assert 0xB0B not in tr.dummy_split_estimated_keys()
    # the rows still add up to the total the header prints
    assert abs(sum(r.damage for r in rows) - sess.target_total) < 1e-6
    assert abs(sess.target_total - 700.0) < 1e-6


def test_an_addressed_leftover_row_is_kept_and_is_not_an_estimate():
    """A bucket keyed by a REAL address the cluster no longer lists is a target
    of its own, not an address-less guess: it keeps its own row and the split
    stays a measurement. Only the label-keyed (address-less) case folds.
    """
    m = _Model()
    tr = DpsTracker(m)
    m._units = [_hero(PA),
                training_dummy(FOE, uid="Dummy", hp=100000.0, x=2.0, y=0.0, z=0.0),
                training_dummy(BOSS, uid="Dummy", hp=100000.0, x=3.0, y=0.0, z=0.0)]
    tr.update()

    for addr, amt in ((FOE, 500.0), (0xDEAD, 250.0)):
        m.damage.push(DamageEvent(amount=amt, skill="Sword_Base_Attack",
                                  source_addr=PA, target_addr=addr,
                                  target_name="Dummy", t=time.time()))
    tr.update()

    rows = tr.dummy_split_targets(tr.session_for("dummy"))
    assert tr.dummy_split_is_estimated() is False
    assert tr.dummy_split_estimated_keys() == set()
    # the two cluster dummies, plus one row for the dummy that left the scan
    assert sorted(r.addr for r in rows) == sorted([FOE, BOSS, 0xDEAD])
    by_addr = {r.addr: r for r in rows}
    assert abs(by_addr[FOE].damage - 500.0) < 1e-6
    assert abs(by_addr[0xDEAD].damage - 250.0) < 1e-6


def test_the_dummy_split_lists_the_whole_yard_not_just_the_hits():
    """A three-dummy yard used to collapse the split to whatever address the
    decoded hits happened to carry (live 2026-10-03: one `🎯 Dummy` row for a
    whole cluster). The split now lists every dummy the scan sees — nearest
    first — each carrying the damage the events attributed to its own address,
    so a yard reads as three rows and the leader is the one that took the
    damage."""
    m = _Model()
    tr = DpsTracker(m)
    tr.dummy_target_s = 0.0
    m._units = [_hero(PA),
                training_dummy(FOE, uid="Dummy", hp=100000.0, x=2.0, y=1.0, z=1.0),
                training_dummy(BOSS, uid="Dummy", hp=100000.0, x=3.0, y=1.0, z=1.0),
                training_dummy(0xB0B, uid="Dummy", hp=100000.0, x=4.0, y=1.0, z=1.0)]
    tr.update()
    assert tr.in_dummy_fight is True
    assert [a for a, *_ in tr.dummy_cluster] == [FOE, BOSS, 0xB0B]

    # a hit that names an address lands on THAT dummy only
    m.damage.push(DamageEvent(amount=700.0, skill="Sword_Base_Attack",
                              source_addr=PA, target_addr=BOSS, t=time.time()))
    tr.update()
    rows = tr.dummy_split_targets(tr.dummy_session)
    assert [r.addr for r in rows] == [BOSS, FOE, 0xB0B]   # damage first, then distance
    by_addr = {r.addr: r for r in rows}
    assert abs(by_addr[BOSS].damage - 700.0) < 1e-6
    assert by_addr[FOE].damage == 0.0
    assert by_addr[0xB0B].damage == 0.0


def test_a_bridge_yard_splits_per_dummy_address():
    """A real bridge line now names the target's ADDRESS, so a live yard reads
    as one row per dummy instead of one lumped `Dummy` row.

    The wire used to carry the target's NAME only, and two training dummies
    share a name - so every hit landed on one name-keyed bucket (live
    2026-10-03). `parse_hook_event` reads `taddr` onto the event, and that one
    address is what the tracker keys the split by.
    """
    from farever_companion.core.dps_bridge import parse_hook_event

    m = _Model(names={PA: "MyChar"})
    m._units = [_hero(PA),
                training_dummy(FOE, uid="Dummy", hp=100000.0, x=2.0, y=0.0, z=0.0),
                training_dummy(BOSS, uid="Dummy", hp=100000.0, x=3.0, y=0.0, z=0.0)]
    tr = DpsTracker(m)
    tr.update()
    assert tr.in_dummy_fight is True

    for addr, amt in ((FOE, 700.0), (BOSS, 300.0)):
        ev = parse_hook_event(
            '{"t":"hit","amt":%s,"sk":"Sword_Base_Attack",'
            '"src":"MyChar","tgt":"Dummy","taddr":%d}' % (amt, addr))
        ev.capture_mode = "proxy"
        m.damage.push(ev)
    tr.update()

    rows = {r.addr: r for r in tr.session_for("dummy").ranked_targets()}
    assert set(rows) == {FOE, BOSS}          # one bucket per dummy, not one row
    assert abs(rows[FOE].damage - 700.0) < 1e-6
    assert abs(rows[BOSS].damage - 300.0) < 1e-6


def test_a_bridge_yard_without_addresses_still_collapses_to_one_row():
    """The contrast that says why `taddr` exists: the SAME two hits with no
    address (an older bridge) fold onto one name-keyed row. The address is the
    whole difference, so a regression that drops it is visible here."""
    from farever_companion.core.dps_bridge import parse_hook_event

    m = _Model(names={PA: "MyChar"})
    m._units = [_hero(PA),
                training_dummy(FOE, uid="Dummy", hp=100000.0, x=2.0, y=0.0, z=0.0),
                training_dummy(BOSS, uid="Dummy", hp=100000.0, x=3.0, y=0.0, z=0.0)]
    tr = DpsTracker(m)
    tr.update()

    for amt in (700.0, 300.0):
        ev = parse_hook_event(
            '{"t":"hit","amt":%s,"sk":"Sword_Base_Attack",'
            '"src":"MyChar","tgt":"Dummy"}' % amt)
        ev.capture_mode = "proxy"
        assert ev.target_addr == 0
        m.damage.push(ev)
    tr.update()

    rows = tr.session_for("dummy").ranked_targets()
    assert len(rows) == 1                      # collapsed: name cannot split it
    assert rows[0].addr == 0
    assert abs(rows[0].damage - 1000.0) < 1e-6


def test_the_dummy_split_diagnostic_logs_hit_addresses_against_the_scene():
    """The split's open question — can the bridge name each dummy? — is
    answerable from ONE test run's Activity Log. The scene cluster's addresses
    are logged as the reference, each DISTINCT decoded hit address is logged
    once, and the test end prints the spread against the cluster. One address
    for a three-dummy yard is the verdict that the split must collapse."""
    m = _Model()
    tr = DpsTracker(m)
    tr.dummy_target_s = 0.0
    lines: list[str] = []
    tr.log_line = lines.append
    m._units = [_hero(PA),
                training_dummy(FOE, uid="Dummy", hp=100000.0, x=2.0, y=0.0, z=0.0),
                training_dummy(BOSS, uid="Dummy", hp=100000.0, x=3.0, y=0.0, z=0.0),
                training_dummy(0xB0B, uid="Dummy", hp=100000.0, x=4.0, y=0.0, z=0.0)]
    tr.update()

    cluster = [l for l in lines if l.startswith("[dummy] scene cluster")]
    assert len(cluster) == 1 and "scene cluster (3):" in cluster[0]
    assert f"{FOE:#x}" in cluster[0] and f"{BOSS:#x}" in cluster[0]

    # two hits on the SAME dummy log one address line, not two
    m.damage.push(DamageEvent(amount=700.0, skill="Sword_Base_Attack",
                              source_addr=PA, target_addr=BOSS, t=time.time()))
    m.damage.push(DamageEvent(amount=300.0, skill="Sword_Base_Attack",
                              source_addr=PA, target_addr=BOSS, t=time.time()))
    tr.update()
    hits = [l for l in lines if l.startswith("[dummy] hit target")]
    assert len(hits) == 1 and f"{BOSS:#x}" in hits[0]

    # the end-of-test summary answers the question: 1 addr over a 3-dummy yard
    tr.stop_dummy_test()
    summary = [l for l in lines if "test end" in l]
    assert summary, lines
    assert "1 distinct target address" in summary[-1]
    assert "scene cluster had 3" in summary[-1]

    # a fresh test starts its own address set: the same dummy logs again
    count_before = len([l for l in lines if l.startswith("[dummy] hit target")])
    tr.start_dummy_test()
    tr.update()
    m.damage.push(DamageEvent(amount=100.0, skill="Sword_Base_Attack",
                              source_addr=PA, target_addr=BOSS, t=time.time()))
    tr.update()
    assert len([l for l in lines if l.startswith("[dummy] hit target")]) \
        == count_before + 1


def test_test_dummy_hit_without_scene_engages_dummy_group():
    """A 'Test Dummy' damage event with no scene foe (bridge/test injection)
    still opens the dummy group via the dummy display name."""
    m = _Model()
    tr = DpsTracker(m)
    m._units = [_hero(PA)]
    m.damage.push(DamageEvent(amount=9999.0, skill="TestHit",
                              source_addr=PA, target_addr=0,
                              target_name="Test Dummy", t=time.time()))
    tr.update()
    assert tr.in_dummy_fight is True
    assert abs(tr.dummy_session.group_damage - 9999.0) < 1e-6
    assert tr.trash_session.group_damage == 0.0


def test_dummy_variant_short_name_engages_boss_group():
    """Punching-bag variants show short display names ("Invu", "Armor", ...)
    that carry no family marker — they still open the boss group, by name
    reverse-resolution (and by scene-learned raw id)."""
    from farever_companion.data import units as udata
    assert udata.is_training_dummy("Invu")
    assert udata.is_training_dummy("Armor")
    assert udata.is_training_dummy("Shielded")
    assert udata.is_training_dummy("MagicRes")
    assert not udata.is_training_dummy("Fleeing Bag")

    m = _Model()
    tr = DpsTracker(m)
    m._units = [_hero(PA)]
    m.damage.push(DamageEvent(amount=250.0, skill="Sword_Base_Attack",
                              source_addr=PA, target_addr=0,
                              target_name="Invu", t=time.time()))
    tr.update()
    assert tr.in_dummy_fight is True
    assert abs(tr.dummy_session.group_damage - 250.0) < 1e-6
    assert tr.trash_session.group_damage == 0.0


def test_pure_kill_notification_confirms_boss_defeat_on_despawn():
    """A zero-amount kill line (uid only: the bridge's death hook reports no
    killer and no amount) matching the active boss arms the kill grace, so a
    boss that dies and despawns before its HP ever reads 0 ends as 'Boss
    Defeated' — not the fallback 'Boss Fight'."""
    m = _Model()
    m.dungeon_boss = "Boss_Foo"
    tr = DpsTracker(m)
    logs = []
    tr.log_line = logs.append
    m._units = [_hero(PA), _foe(BOSS, uid="Boss_Foo", hp=100000.0,
                                  is_boss=True)]
    tr.update()
    assert tr.in_boss_fight is True
    m.damage.push(DamageEvent(amount=500.0, skill="Meteor", source_addr=PA,
                              target_addr=BOSS, t=time.time()))
    tr.update()
    assert tr.boss_session.group_damage > 0.0

    # Boss vanishes in the same tick it dies: only the kill notification
    # (no damage, no DamageResult — just the unit id) says it died.
    m._units = [_hero(PA)]
    m.damage.push(DamageEvent(amount=0.0, kill=True,
                              target_name="Boss_Foo", t=time.time()))
    tr.update()
    assert tr.in_boss_fight is False
    bosses = [s for s in tr.history if s.kind == "boss"]
    assert bosses and "Defeated" in bosses[-1].name
    assert any("boss defeat confirmed" in line.lower() for line in logs)


def test_stray_kill_notification_does_not_end_boss_fight_early():
    """A kill notification for a trash mob (different uid) never touches the
    active boss's kill grace."""
    m = _Model()
    m.dungeon_boss = "Boss_Foo"
    tr = DpsTracker(m)
    m._units = [_hero(PA),
                _foe(BOSS, uid="Boss_Foo", hp=100000.0, is_boss=True),
                _foe(FOE, uid="Trash_Pig", hp=500.0)]
    tr.update()
    assert tr.in_boss_fight is True
    m.damage.push(DamageEvent(amount=500.0, skill="Meteor", source_addr=PA,
                              target_addr=BOSS, t=time.time()))
    tr.update()
    assert tr._boss_kill_pending == 0.0

    m.damage.push(DamageEvent(amount=0.0, kill=True,
                              target_name="Trash_Pig", t=time.time()))
    tr.update()
    assert tr.in_boss_fight is True          # the boss fight is still on
    assert tr._boss_kill_pending == 0.0


def test_one_canonical_id_rule_for_the_scene_and_the_bridge_kill_uid():
    """The scene walk and the bridge's kill notification read the SAME field -
    the unit's own `unitId` hl String - and the two must reduce it identically,
    because the kill path compares one against the other.

    The bridge puts the raw value on the wire: a prefab spawn carries its asset
    path (`Units/Enemies/Wolf/Wolf_Z1W.prefab`), a pooled instance its engine
    suffix (`Boss_Foo(Clone)`). The static CDB ids every lookup here is keyed by
    are neither, so one rule reduces the shape - and it is IDEMPOTENT, which is
    what lets the comparison canonicalize both sides without caring which one
    came off the wire.
    """
    from farever_companion.data import units as udata

    table = (
        ("Boss_Foo", "Boss_Foo"),                       # already canonical
        ("  Boss_Foo  ", "Boss_Foo"),
        ("Boss_Foo(Clone)", "Boss_Foo"),                # pooled instance
        ("Boss_Foo (Instance)", "Boss_Foo"),
        ("Units/Enemies/Boss_Foo.prefab", "Boss_Foo"),  # prefab path
        ("Units\\Enemies\\Boss_Foo.prefab", "Boss_Foo"),
        ("Units/Enemies/Patrol/Patrol_Wolf.prefab", "Patrol_Wolf"),
        ("Units/Enemies/Patrol/Patrol_Wolf_Elite.prefab", "Patrol_Wolf"),
        ("Patrol_Wolf_3", "Patrol_Wolf"),               # patrol spawn variant
        ("Boss_Foo_2", "Boss_Foo_2"),                   # not a patrol: tail stays
        (None, None),
        ("", ""),
    )
    for raw, want in table:
        got = udata.canonical_unit_id(raw)
        assert got == want, (raw, got, want)
        assert udata.canonical_unit_id(got) == want, f"not idempotent: {raw!r}"


def test_a_raw_wire_id_confirms_the_boss_kill_by_id():
    """A boss whose id arrives raw still confirms by ID.

    The wire side is the case live play produced: a path-like prefab spawn or
    an engine-pooled clone. Compared verbatim against the canonical stored id,
    neither could ever match - the id route failed on exactly the deaths the
    notification exists for, and the kill only landed when the display name
    happened to be the uid too. The boss vanishes before its HP reads 0 here,
    so the kill notification is the only thing that can close the fight, and
    the archive name is the proof it did.
    """
    for stored, wire in (
        ("Boss_Foo", "Units/Enemies/Boss_Foo.prefab"),
        ("Boss_Foo", "Boss_Foo(Clone)"),
        ("Patrol_Wolf", "Units/Enemies/Patrol/Patrol_Wolf_Elite.prefab"),
    ):
        m = _Model()
        m.dungeon_boss = stored
        tr = DpsTracker(m)
        m._units = [_hero(PA), _foe(BOSS, uid=stored, hp=100000.0, is_boss=True)]
        tr.update()
        assert tr.in_boss_fight is True, (stored, wire)
        m.damage.push(DamageEvent(amount=500.0, skill="Meteor", source_addr=PA,
                                  target_addr=BOSS, t=time.time()))
        tr.update()
        assert tr.boss_session.group_damage > 0.0, (stored, wire)

        m._units = [_hero(PA)]          # gone before its HP ever reads 0
        m.damage.push(DamageEvent(amount=0.0, kill=True,
                                  target_name=wire, t=time.time()))
        tr.update()
        assert tr.in_boss_fight is False, (stored, wire)
        bosses = [s for s in tr.history if s.kind == "boss"]
        assert bosses and "Defeated" in bosses[-1].name, (stored, wire)


def test_a_raw_wire_id_for_another_unit_never_confirms_the_boss_kill():
    """Canonicalizing must not over-match.

    A different unit's raw id - prefab path, clone suffix and all - never arms
    the active boss's kill grace, and a tail the rule does not fold stays part
    of the id, so `Boss_Foo_2` is a different boss from `Boss_Foo`.
    """
    m = _Model()
    m.dungeon_boss = "Boss_Foo"
    tr = DpsTracker(m)
    m._units = [_hero(PA), _foe(BOSS, uid="Boss_Foo", hp=100000.0,
                                  is_boss=True)]
    tr.update()
    m.damage.push(DamageEvent(amount=500.0, skill="Meteor", source_addr=PA,
                              target_addr=BOSS, t=time.time()))
    tr.update()
    assert tr._boss_kill_pending == 0.0

    for wire in ("Units/Enemies/Trash_Pig.prefab",
                 "Trash_Pig(Clone)",
                 "Units/Enemies/Boss_Foo_2.prefab"):
        m.damage.push(DamageEvent(amount=0.0, kill=True,
                                  target_name=wire, t=time.time()))
        tr.update()
        assert tr.in_boss_fight is True, wire
        assert tr._boss_kill_pending == 0.0, wire


def test_boss_adds_segment_is_selectable_and_archives_with_boss():
    """The boss-adds segment is selectable and archives separately with the boss."""
    m = _Model()
    m.dungeon_boss = "Boss_Foo"
    tr = DpsTracker(m)
    m._units = [_hero(PA), _foe(BOSS, uid="Boss_Foo", hp=100000.0,
                                  is_boss=True), _foe(FOE)]
    tr.update()
    m.damage.push(DamageEvent(amount=400.0, skill="Sword_Base_Attack",
                              source_addr=PA, target_addr=BOSS, t=time.time()))
    m.damage.push(DamageEvent(amount=250.0, skill="Axe_Base_Attack",
                              source_addr=PA, target_addr=FOE, t=time.time()))
    tr.update()

    # the adds segment is selectable and shows only the add damage
    tr.segment_view = "boss_adds"
    assert tr.session is tr.boss_adds_session
    assert abs(tr.session.group_damage - 250.0) < 1e-6
    assert "Axe_Base_Attack" in tr.session.players["Me (You)"].skills

    # boss segment stays boss-only
    tr.segment_view = "boss"
    assert abs(tr.session.group_damage - 400.0) < 1e-6

    # auto picks the boss fight while it is running
    tr.segment_view = "auto"
    assert tr.session is tr.boss_session


def test_unresolvable_caster_only_claimed_when_alone():
    m = _Model()
    tr = DpsTracker(m)
    m._units = [_hero(PA), _hero(TEAM), _foe(FOE)]
    tr.update()
    # two heroes present, caster unknown -> the hit is NOT credited to You
    m.damage.push(DamageEvent(amount=55.0, skill="Axe_Base_Attack",
                              source_addr=0, target_addr=FOE, t=time.time()))
    tr.update()
    assert tr.overall_session.group_damage == 0.0

    # solo: the local player is the only possible caster -> credited
    m._units = [_hero(PA), _foe(FOE)]
    tr.reset()
    tr.update()
    m.damage.push(DamageEvent(amount=55.0, skill="Axe_Base_Attack",
                              source_addr=0, target_addr=FOE, t=time.time()))
    tr.update()
    assert abs(tr.overall_session.group_damage - 55.0) < 1e-6


# --- pet / summon attribution ---------------------------------------------
# The game reports pets as ent.Foe units owned by a hero, and their damage
# events carry the PET as serverSource. The tracker must re-credit those hits
# to the owning hero (Details-style) instead of a synthetic "Party_XXXX" line.


def test_pet_damage_merges_into_owner_solo():
    m = _Model()
    tr = DpsTracker(m)
    m._units = [_hero(PA), _pet(PET, PA), _foe(FOE)]
    tr.update()
    assert tr._pet_owners.get(PET) == PA

    m.damage.push(DamageEvent(amount=40.0, skill="Pet_Bite",
                              source_addr=PET, target_addr=FOE, t=time.time()))
    m.damage.push(DamageEvent(amount=60.0, skill="Sword_Base_Attack",
                              source_addr=PA, target_addr=FOE, t=time.time()))
    tr.update()

    s = tr.overall_session
    assert abs(s.group_damage - 100.0) < 1e-6          # pet + owner combined
    p = s.players.get("Me (You)")
    assert p is not None
    assert abs(p.total_damage - 100.0) < 1e-6          # pet merged INTO me
    assert p.hit_count == 2
    assert all("5555" not in name for name in s.players)   # no pet-only line
    # the pet's skill stays distinguishable inside my parse
    assert "Pet_Bite" in p.skills
    assert p.skills["Pet_Bite"].name.endswith("(pet)")
    assert "Sword_Base_Attack" in p.skills
    assert not p.skills["Sword_Base_Attack"].name.endswith("(pet)")


def test_one_pet_skill_is_one_bucket_however_the_wire_tags_it():
    """The same pet swing, tagged and untagged, must be ONE skill row.

    Two taggers write "(pet)": the bridge reader onto the skill ID, and the
    tracker's pet-owner map onto the display name. The bridge decides its tag
    per hit from a live memory read (`bridge_core.h`: is the dealer's owner
    field readable and a hero right now?), so one swing arrives tagged and the
    next untagged.

    Nothing reconciled them, and every bucket is keyed by id — so the HUD held
    two rows for one pet skill, and `update_stats` hides each row on any tick
    where its id is absent. They took turns appearing and disappearing at the
    pet's cadence (live 2026-10-03: the auto-expanded skills "keep showing then
    hiding"). A doubly-tagged hit also read `(pet) (pet)`.
    """
    m = _Model()
    tr = DpsTracker(m)
    m._units = [_hero(PA), _pet(PET, PA), _foe(FOE)]
    tr.update()

    for skill in ("Pet_Bite (pet)", "Pet_Bite", "Pet_Bite (pet)", "Pet_Bite"):
        m.damage.push(DamageEvent(amount=40.0, skill=skill, source_addr=PET,
                                  target_addr=FOE, t=time.time()))
    tr.update()

    p = tr.overall_session.players["Me (You)"]
    assert list(p.skills) == ["Pet_Bite"], "one skill, one bucket"
    assert p.hit_count == 4
    # Still distinguishable from my own rotation...
    assert p.skills["Pet_Bite"].name.endswith("(pet)")
    # ...and tagged exactly once.
    assert "(pet) (pet)" not in p.skills["Pet_Bite"].name


def test_my_own_skill_is_never_tagged_as_a_pet():
    """The canonical id must not turn MY skills into pet skills.

    The strip is on the id every hit shares, so a skill id that genuinely ends
    in the marker is the one case worth guarding: the flag has to come from the
    wire tag alone, never from the resolved display name, or a sheet name that
    happens to end in "(pet)" would relabel the bucket.
    """
    tr = DpsTracker(_Model())
    ev = DamageEvent(amount=1.0, skill="Staff_SummonDemon_PetHit",
                     target_addr=FOE, t=0.0)
    sid, sname, tagged = tr._skill_labels(ev, fallback=("?", "?"))
    assert (sid, tagged) == ("Staff_SummonDemon_PetHit", False)
    assert not sname.endswith("(pet)")


def test_party_member_pet_merges_into_owner():
    m = _Model()
    tr = DpsTracker(m)
    m._units = [_hero(PA), _hero(TEAM), _pet(PET, TEAM), _foe(FOE)]
    tr.update()

    m.damage.push(DamageEvent(amount=25.0, skill="Pet_Bite",
                              source_addr=PET, target_addr=FOE, t=time.time()))
    tr.update()

    s = tr.overall_session
    me = s.players.get("Me (You)")
    mate = s.players.get("Teammate")
    assert mate is not None and abs(mate.total_damage - 25.0) < 1e-6
    assert abs(s.group_damage - 25.0) < 1e-6
    assert me is None or me.total_damage == 0.0        # NOT credited to me
    assert "Pet_Bite" in mate.skills


def test_pet_heal_merges_into_owner():
    m = _Model()
    tr = DpsTracker(m)
    m._units = [_hero(PA), _pet(PET, PA), _foe(FOE)]
    tr.update()

    m.damage.push(DamageEvent(amount=-30.0, skill="Pet_Regen", kind=K_HEAL,
                              source_addr=PET, target_addr=PA, t=time.time()))
    tr.update()

    s = tr.overall_session
    assert abs(s.group_heals - 30.0) < 1e-6
    p = s.players.get("Me (You)")
    assert p is not None and abs(p.heals - 30.0) < 1e-6
    assert p.skills["Pet_Regen"].name.endswith("(pet)")


def test_hybrid_skill_splits_into_damage_and_heal_channels():
    """A hybrid skill's damage and heal events land in separate channels."""
    m = _Model()
    tr = DpsTracker(m)
    m._units = [_hero(PA), _hero(TEAM), _foe(FOE)]
    tr.update()

    # damage component lands on the mob
    m.damage.push(DamageEvent(amount=500.0, skill="Judgment",
                              source_addr=PA, target_addr=FOE, t=time.time()))
    # heal component lands on the ally
    m.damage.push(DamageEvent(amount=-300.0, skill="Judgment", kind=K_HEAL,
                              source_addr=PA, target_addr=TEAM, t=time.time()))
    tr.update()

    s = tr.overall_session
    p = s.players.get("Me (You)")
    assert p is not None
    assert abs(p.total_damage - 500.0) < 1e-6     # damage only
    assert abs(p.heals - 300.0) < 1e-6            # heals only

    sp = p.skills["Judgment"]
    assert sp is not None
    assert abs(sp.damage - 500.0) < 1e-6          # damage channel
    assert sp.hit_count == 1
    assert abs(sp.heals - 300.0) < 1e-6           # heal channel, NOT merged
    assert sp.heal_count == 1
    assert abs(sp.total - 800.0) < 1e-6           # combined for Skill view
    # and the damage totals never swallowed the heals
    assert abs(s.group_damage - 500.0) < 1e-6
    assert abs(s.group_heals - 300.0) < 1e-6


def test_enemies_are_never_merged_as_pets():
    m = _Model()
    tr = DpsTracker(m)
    # plain foes have no hero owner -> never mapped to a hero
    m._units = [_hero(PA), _foe(FOE)]
    tr.update()
    assert tr._pet_owners == {}

    # and a foe's owner is NOT a hero class even when a pointer exists
    odd = _foe(0x6666)
    odd.owner_addr = 0x7777
    odd.owner_cls = "ent.Foe"   # an enemy owning another enemy
    m._units = [_hero(PA), odd]
    tr.update()
    assert tr._pet_owners == {}


def test_despawned_pet_events_still_merge_while_pruning_bounds_cache():
    m = _Model()
    tr = DpsTracker(m)
    m._units = [_hero(PA), _pet(PET, PA), _foe(FOE)]
    tr.update()

    # pet despawns (owner dismissed it), but the last in-flight hits arrive
    m._units = [_hero(PA), _foe(FOE)]
    tr.update()
    assert PET in tr._pet_owners            # kept (grace period)

    m.damage.push(DamageEvent(amount=12.0, skill="Pet_Bite",
                              source_addr=PET, target_addr=FOE, t=time.time()))
    tr.update()
    p = tr.overall_session.players.get("Me (You)")
    assert p is not None and abs(p.total_damage - 12.0) < 1e-6

    # entries older than the 60s window are pruned
    tr._pet_last_seen[PET] = time.time() - 120.0
    tr.update()
    assert PET not in tr._pet_owners


def test_unscanned_pet_owner_read_at_event_time():
    """A never-scanned summon still merges via the live owner-slot read."""
    m = _Model()
    m.pet_owner_map = {PET: TEAM}          # live game: PET is Teammate's summon
    tr = DpsTracker(m)
    m._units = [_hero(PA), _hero(TEAM), _foe(FOE)]
    tr.update()
    # PET is NOT among the scene units - only the owner read can identify it
    m.damage.push(DamageEvent(amount=40.0, skill="Pet_Burn",
                              source_addr=PET, target_addr=FOE, t=time.time()))
    m.damage.push(DamageEvent(amount=60.0, skill="Pet_Burn",
                              source_addr=PET, target_addr=FOE, t=time.time()))
    tr.update()

    s = tr.overall_session
    assert abs(s.group_damage - 100.0) < 1e-6
    mate = s.players.get("Teammate")
    assert mate is not None and abs(mate.total_damage - 100.0) < 1e-6
    assert mate.skills["Pet_Burn"].name.endswith("(pet)")
    assert not any(n.startswith("Party_") for n in s.players)


def test_unscanned_own_pet_merges_into_me():
    m = _Model()
    m.pet_owner_map = {PET: PA}
    tr = DpsTracker(m)
    m._units = [_hero(PA), _foe(FOE)]
    tr.update()
    m.damage.push(DamageEvent(amount=33.0, skill="Pet_Claw",
                              source_addr=PET, target_addr=FOE, t=time.time()))
    tr.update()
    s = tr.overall_session
    p = s.players.get("Me (You)")
    assert p is not None and abs(p.total_damage - 33.0) < 1e-6
    assert p.skills["Pet_Claw"].name.endswith("(pet)")
    assert not any(n.startswith("Party_") for n in s.players)


def test_unscanned_pet_heal_merges_into_owner():
    m = _Model()
    m.pet_owner_map = {PET: PA}
    tr = DpsTracker(m)
    m._units = [_hero(PA), _foe(FOE)]
    tr.update()
    m.damage.push(DamageEvent(amount=-25.0, skill="Pet_Regen", kind=K_HEAL,
                              source_addr=PET, target_addr=PA, t=time.time()))
    tr.update()
    s = tr.overall_session
    assert abs(s.group_heals - 25.0) < 1e-6
    p = s.players.get("Me (You)")
    assert p is not None and abs(p.heals - 25.0) < 1e-6
    assert not any(n.startswith("Party_") for n in s.players)


def test_late_scene_scan_folds_existing_uid_row():
    """A uid Party_XXXX row folds into the owner once the scan learns it."""
    m = _Model()
    tr = DpsTracker(m)
    # phase 1: summon not yet visible and unreadable -> uid row accrues
    m._units = [_hero(PA), _hero(TEAM), _foe(FOE)]
    tr.update()
    m.damage.push(DamageEvent(amount=20.0, skill="Pet_Burn",
                              source_addr=PET, target_addr=FOE, t=time.time()))
    m.damage.push(DamageEvent(amount=30.0, skill="Pet_Burn",
                              source_addr=PET, target_addr=FOE, t=time.time()))
    tr.update()
    s = tr.overall_session
    assert len([n for n in s.players if n.startswith("Party_")]) == 1
    assert abs(s.group_damage - 50.0) < 1e-6

    # phase 2: summon + owner become visible -> scan learns the mapping
    m._units = [_hero(PA), _hero(TEAM), _pet(PET, TEAM), _foe(FOE)]
    tr.update()
    assert tr._pet_owners.get(PET) == TEAM
    s = tr.overall_session
    assert not any(n.startswith("Party_") for n in s.players)   # row folded
    mate = s.players.get("Teammate")
    assert mate is not None and abs(mate.total_damage - 50.0) < 1e-6
    assert abs(s.group_damage - 50.0) < 1e-6     # total unchanged: no double count
    assert mate.skills["Pet_Burn"].name.endswith("(pet)")


def test_uid_row_folds_when_identity_resolves_late():
    """An unknown caster's uid row folds into the real name once readable."""
    m = _Model()
    tr = DpsTracker(m)
    m._units = [_hero(PA), _foe(FOE)]
    tr.update()
    src = 0x8888
    state = {"known": False}

    def pname(addr):
        if addr == src:
            return "LateAlly" if state["known"] else None
        return {PA: "Me", TEAM: "Teammate"}.get(addr)

    m.player_name = pname
    m.damage.push(DamageEvent(amount=15.0, skill="Axe_Base_Attack",
                              source_addr=src, target_addr=FOE, t=time.time()))
    tr.update()
    s = tr.overall_session
    assert any(n.startswith("Party_") for n in s.players)

    # identity becomes readable later; the next event retries past the cadence
    state["known"] = True
    tr._party_retry_at[src] = 0.0
    m.damage.push(DamageEvent(amount=25.0, skill="Axe_Base_Attack",
                              source_addr=src, target_addr=FOE, t=time.time()))
    tr.update()
    s = tr.overall_session
    assert not any(n.startswith("Party_") for n in s.players)
    ally = s.players.get("LateAlly")
    assert ally is not None and abs(ally.total_damage - 40.0) < 1e-6


def test_foe_with_no_hero_owner_is_never_mapped():
    """Enemy casters with no hero owner are never re-credited to players."""
    m = _Model()
    m.pet_owner_map = {}                     # nothing is player-owned
    tr = DpsTracker(m)
    m._units = [_hero(PA), _hero(TEAM), _foe(FOE)]
    tr.update()
    m.damage.push(DamageEvent(amount=90.0, skill="Mob_Burn",
                              source_addr=FOE, target_addr=0x7777,
                              t=time.time()))
    tr.update()
    assert tr._pet_owners == {}
    s = tr.overall_session
    mate = s.players.get("Teammate")
    me = s.players.get("Me (You)")
    assert mate is None or mate.total_damage == 0.0
    assert me is None or me.total_damage == 0.0


# --- fight-history persistence ---------------------------------------------


def test_archived_fight_round_trips_through_disk(tmp_path):
    """A finished fight archives to disk and reloads with identical totals."""
    import json

    m = _Model()
    tr = DpsTracker(m)
    path = tmp_path / "dps_history.json"
    tr.set_history_path(path)

    # real boss fight: damage + a heal, with per-second timeline buckets
    m._units = [_hero(PA), _foe(BOSS, uid="Boss_Foo", hp=100000.0,
                                  is_boss=True), _foe(FOE)]
    tr.update()
    m.damage.push(DamageEvent(amount=700.0, skill="Sword_Base_Attack",
                              source_addr=PA, target_addr=BOSS, t=time.time()))
    m.damage.push(DamageEvent(amount=-300.0, skill="Regen", kind=K_HEAL,
                              source_addr=PA, target_addr=PA, t=time.time()))
    tr.update()
    m._units = [_hero(PA), _foe(FOE)]
    m.damage.push(DamageEvent(amount=100.0, skill="Sword_Base_Attack",
                              kill=True, source_addr=PA, target_addr=BOSS,
                              t=time.time()))
    tr.update()

    assert len(tr.history) == 1
    assert path.exists()                       # archive persisted automatically
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["history"][0]["players"][0]["name"] == "Me (You)"

    # a FRESH tracker on the same path reloads the fight
    tr2 = DpsTracker(_Model())
    assert tr2.history == []
    tr2.set_history_path(path)
    assert len(tr2.history) == 1
    s = tr2.history[0]
    assert s.state == "PAUSED"
    assert abs(s.group_damage - 800.0) < 1e-6          # 700 + 100 kill
    assert abs(s.group_heals - 300.0) < 1e-6
    me = s.players.get("Me (You)")
    assert me is not None and abs(me.total_damage - 800.0) < 1e-6
    assert "Sword_Base_Attack" in me.skills
    assert "Regen" in me.skills
    # heal channel survives disk too: Regen's 300 heal is NOT in damage
    assert abs(me.skills["Regen"].heals - 300.0) < 1e-6
    assert abs(me.skills["Regen"].damage) < 1e-6
    assert me.skills["Regen"].heal_count == 1
    assert max(s.timeline.values()) >= 800.0           # timeline buckets intact


# --- per-hit damage type (affinity) ------------------------------------------

def test_damage_type_is_bucketed_beside_the_skill_total():
    """Per-hit damage type accumulates per skill; untagged hits stay out of the
    buckets, so a breakdown can sum to less than the skill's damage."""
    m = _Model()
    tr = DpsTracker(m)
    m._units = [_hero(PA), _foe(FOE)]
    tr.update()
    for amount, aff in ((600.0, "Physical"), (300.0, "Fire"), (100.0, "")):
        m.damage.push(DamageEvent(amount=amount, skill="Sword_Base_Attack",
                                  affinity=aff, source_addr=PA,
                                  target_addr=FOE, t=time.time()))
    tr.update()

    me = tr.overall_session.players["Me (You)"]
    sp = me.skills["Sword_Base_Attack"]
    assert abs(sp.damage - 1000.0) < 1e-6
    assert sp.hit_count == 3
    assert sp.affinity_damage == {"Physical": 600.0, "Fire": 300.0}
    assert sp.affinity_hits == {"Physical": 1, "Fire": 1}
    # the untagged hit is counted in the skill, just not attributed a school
    assert abs(sum(sp.affinity_damage.values()) - 900.0) < 1e-6


def test_junk_affinity_on_a_hand_built_event_is_ignored():
    """A recycled pool slot (or a non-conforming third-party producer) must not
    mint a bucket key; the hit still counts."""
    m = _Model()
    tr = DpsTracker(m)
    m._units = [_hero(PA), _foe(FOE)]
    tr.update()
    m.damage.push(DamageEvent(amount=250.0, skill="Sword_Base_Attack",
                              affinity="instStretch:y", source_addr=PA,
                              target_addr=FOE, t=time.time()))
    tr.update()

    sp = tr.overall_session.players["Me (You)"].skills["Sword_Base_Attack"]
    assert abs(sp.damage - 250.0) < 1e-6
    assert sp.affinity_damage == {} and sp.affinity_hits == {}


def test_folding_a_placeholder_keeps_its_damage_by_type():
    """Folding a Party_XXXX row into its owner must merge the affinity buckets
    too, or resolving the identity silently drops that player's breakdown."""
    m = _Model()
    tr = DpsTracker(m)
    # phase 1: summon not yet visible -> uid row accrues a typed hit
    m._units = [_hero(PA), _hero(TEAM), _foe(FOE)]
    tr.update()
    m.damage.push(DamageEvent(amount=40.0, skill="Pet_Burn", affinity="Fire",
                              source_addr=PET, target_addr=FOE, t=time.time()))
    tr.update()
    assert len([n for n in tr.overall_session.players
                if n.startswith("Party_")]) == 1

    # phase 2: the scan learns the owner -> the row folds
    m._units = [_hero(PA), _hero(TEAM), _pet(PET, TEAM), _foe(FOE)]
    tr.update()
    s = tr.overall_session
    assert not any(n.startswith("Party_") for n in s.players)
    sp = s.players["Teammate"].skills["Pet_Burn"]
    assert abs(sp.damage - 40.0) < 1e-6
    assert sp.affinity_damage == {"Fire": 40.0}
    assert sp.affinity_hits == {"Fire": 1}


def test_damage_type_survives_the_archive(tmp_path):
    """Archived fights must keep the breakdown, or past-fight inspection loses
    what the live meter showed."""
    m = _Model()
    tr = DpsTracker(m)
    path = tmp_path / "dps_history.json"
    tr.set_history_path(path)
    m._units = [_hero(PA), _foe(BOSS, uid="Boss_Foo", hp=100000.0,
                                  is_boss=True)]
    tr.update()
    m.damage.push(DamageEvent(amount=700.0, skill="Sword_Base_Attack",
                              affinity="Physical", source_addr=PA,
                              target_addr=BOSS, t=time.time()))
    m.damage.push(DamageEvent(amount=200.0, skill="Book_Fireball_Skill1",
                              affinity="Fire", source_addr=PA,
                              target_addr=BOSS, t=time.time()))
    tr.update()
    m._units = [_hero(PA), _foe(FOE)]      # boss despawns -> archive
    m.damage.push(DamageEvent(amount=100.0, skill="Sword_Base_Attack",
                              affinity="Physical", kill=True, source_addr=PA,
                              target_addr=BOSS, t=time.time()))
    tr.update()
    assert len(tr.history) == 1

    tr2 = DpsTracker(_Model())
    tr2.set_history_path(path)
    me = tr2.history[0].players["Me (You)"]
    assert me.skills["Sword_Base_Attack"].affinity_damage == {"Physical": 800.0}
    assert me.skills["Sword_Base_Attack"].affinity_hits == {"Physical": 2}
    assert me.skills["Book_Fireball_Skill1"].affinity_damage == {"Fire": 200.0}


def test_history_persistence_disabled_by_default():
    """No path set -> archives never touch disk (headless tools/probes)."""
    m = _Model()
    tr = DpsTracker(m)
    assert tr._history_path is None and tr._history_dir is None
    m.is_dungeon = True             # instance trash: reset() archives it
    m._units = [_hero(PA), _foe(FOE)]
    tr.update()
    m.damage.push(DamageEvent(amount=50.0, skill="Sword_Base_Attack",
                              source_addr=PA, target_addr=FOE, t=time.time()))
    tr.update()
    tr.reset()
    assert len(tr.history) >= 1               # archived in memory
    assert tr._history_path is None and tr._history_dir is None
    assert tr._resolve_history_file() is None  # never written anywhere


# --- per-profile fight-history files ---------------------------------------

def _log_dir(base, when=None):
    """The <YYYY-MM>/<DD> folder a session's time lands in (the live layout)."""
    lt = time.localtime(when if when is not None else time.time())
    return Path(base) / time.strftime("%Y-%m", lt) / time.strftime("%d", lt)



def test_history_archives_into_profile_scoped_file(tmp_path):
    """set_history_dir() writes per-day/<kind>_<profile>.json files."""
    m = _Model()
    m.player_profile_value = "Kartik_Warrior_a1b2"
    tr = DpsTracker(m)
    tr.set_history_dir(tmp_path)
    m.is_dungeon = True             # instance trash: reset() archives it

    m._units = [_hero(PA), _foe(FOE)]
    tr.update()
    m.damage.push(DamageEvent(amount=50.0, skill="Sword_Base_Attack",
                              source_addr=PA, target_addr=FOE, t=time.time()))
    tr.update()
    tr.reset()

    day = _log_dir(tmp_path)
    prof_file = day / "trash_Kartik_Warrior_a1b2.json"
    assert prof_file.exists()                 # per-profile day/kind file
    assert not (tmp_path / "dps_history.json").exists()

    import json
    saved = json.loads(prof_file.read_text(encoding="utf-8"))
    assert len(saved["history"]) == 1
    assert saved["history"][0]["kind"] == "trash"


def test_a_trash_log_is_named_boss_first(tmp_path):
    """The pulls file rides the BOSS's name with `trash` after it.

    `<Dungeon> - <Boss> trash_<slug>.json` sorts right beside that boss's own
    fight file in the day folder; the old `Trash pack <Boss>` prefix buried it
    under T (field 2026-10-04: "trash info should be after the boss name").
    """
    m = _Model()
    m.player_profile_value = "Kartik_Warrior_a1b2"
    tr = DpsTracker(m)
    tr.set_history_dir(tmp_path)
    tr._active_profile = "Kartik_Warrior_a1b2"      # set_dir() clears it
    when = 1_752_000_000.0                           # a fixed day folder

    boss_file = tr._history_component.file_for(
        "boss", when, boss_label="Reblochonk", dungeon="Kobolds Mines",
        outcome="PASS")
    trash_file = tr._history_component.file_for(
        "trash", when, boss_label="Reblochonk", dungeon="Kobolds Mines")

    assert boss_file.name == \
        "Kobolds Mines - Reblochonk_Kartik_Warrior_a1b2_PASS.json"
    assert trash_file.name == \
        "Kobolds Mines - Reblochonk trash_Kartik_Warrior_a1b2.json"
    assert trash_file.parent == boss_file.parent


def test_a_finished_dummy_test_lands_in_the_days_csv(tmp_path):
    """A training yard is for comparing runs, and a HUD can only show you the
    last two — the sheet is what shows you the twenty. It is written where the
    fight history already goes (moddata/dps, per day, per profile) and by the
    same call that archives the fight, so every route that ends a test lands
    here: the target length, Stop Test, walking away, Reset."""
    m = _Model()
    m.player_profile_value = "Kartik_Warrior_a1b2"
    tr = DpsTracker(m)
    tr.set_history_dir(tmp_path)
    tr.dummy_target_s = 0.0                       # end it by hand, not by time

    m._units = [_hero(PA), training_dummy(FOE, uid="Dummy", hp=100000.0)]
    tr.update()
    m.damage.push(DamageEvent(amount=1000.0, skill="Sword_Base_Attack",
                              source_addr=PA, target_addr=FOE, t=time.time()))
    tr.update()
    tr.dummy_session.start_time = time.time() - 20.0
    tr.stop_dummy_test()

    day = _log_dir(tmp_path)
    sheet = day / "dummy_Kartik_Warrior_a1b2.csv"
    assert sheet.exists()
    text = sheet.read_text(encoding="utf-8-sig")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    header = lines[0].split(",")
    assert header[0] == "row_kind" and "dps" in header and "skill_id" in header

    import csv as _csv
    rows = list(_csv.DictReader(text.splitlines()))
    kinds = {r["row_kind"] for r in rows}
    assert kinds == {"test", "target", "skill"}
    test_row = next(r for r in rows if r["row_kind"] == "test")
    assert abs(float(test_row["damage"]) - 1000.0) < 1e-6
    assert float(test_row["duration_s"]) >= 20.0
    assert test_row["reason"] == "Test Stopped"
    assert test_row["profile"] == "Kartik_Warrior_a1b2"
    assert test_row["test_start"] and test_row["test_end"]
    skill_row = next(r for r in rows if r["row_kind"] == "skill")
    assert skill_row["skill"] == "Sword Base Attack"
    assert abs(float(skill_row["dps"]) - 50.0) < 1.0     # 1000 over ~20 s
    assert abs(float(skill_row["share_pct"]) - 100.0) < 0.01

    # a second test appends rows; it must never append a second header
    tr.start_dummy_test()
    tr.update()
    m.damage.push(DamageEvent(amount=500.0, skill="Axe_Base_Attack",
                              source_addr=PA, target_addr=FOE, t=time.time()))
    tr.update()
    tr.dummy_session.start_time = time.time() - 10.0
    tr.stop_dummy_test()
    text2 = sheet.read_text(encoding="utf-8-sig")
    assert text2.count("row_kind") == 1
    rows2 = list(_csv.DictReader(text2.splitlines()))
    assert len([r for r in rows2 if r["row_kind"] == "test"]) == 2
    assert any(r["skill"] == "Axe Base Attack" for r in rows2
               if r["row_kind"] == "skill")


def test_the_dummy_sheet_is_pruned_with_the_day_folder(tmp_path):
    """The CSV rides the same per-day, per-profile folders as the history JSON,
    so the pruner that empties them must take the sheet with it: a character
    switch would otherwise leave the previous character's tests in moddata
    forever, and the day folder could never be removed."""
    m = _Model()
    m.player_profile_value = "Kartik_Warrior_a1b2"
    tr = DpsTracker(m)
    tr.set_history_dir(tmp_path)
    tr.dummy_target_s = 0.0

    def _run_one_test():
        m._units = [_hero(PA), training_dummy(FOE, uid="Dummy", hp=100000.0)]
        tr.update()
        m.damage.push(DamageEvent(amount=100.0, skill="Sword_Base_Attack",
                                  source_addr=PA, target_addr=FOE, t=time.time()))
        tr.update()
        tr.dummy_session.start_time = time.time() - 5.0
        tr.stop_dummy_test()
        tr.start_dummy_test()

    _run_one_test()
    day = _log_dir(tmp_path)
    kartik = day / "dummy_Kartik_Warrior_a1b2.csv"
    assert kartik.exists()

    # switch character: the next persist under the new profile prunes the old
    m.player_profile_value = "Maru_Rogue_c9d0"
    tr._profile_ts = 0.0
    tr.update()
    tr._persist_history()
    assert not kartik.exists()


def test_dummy_csv_text_escapes_and_rejects_a_non_test():
    """A skill name with a comma in it is a normal thing to find in this game's
    data, and a hand-rolled join would shift every column after it. A session
    that is not a dummy test writes nothing at all rather than a sheet of
    zeroes."""
    from farever_companion.core.dps_dummy import DUMMY_CSV_COLUMNS, dummy_test_csv

    m = _Model()
    tr = DpsTracker(m)
    m._units = [_hero(PA), training_dummy(FOE, uid="Dummy", hp=100000.0)]
    tr.update()
    m.damage.push(DamageEvent(amount=400.0, skill="Sword_Base_Attack",
                              source_addr=PA, target_addr=FOE, t=time.time()))
    tr.update()
    tr.dummy_session.start_time = time.time() - 8.0

    text = dummy_test_csv(tr.dummy_session, profile="Me", reason="Test Stopped")
    import csv as _csv
    rows = list(_csv.reader(text.splitlines()))
    assert rows[0] == list(DUMMY_CSV_COLUMNS)
    for row in rows[1:]:
        assert len(row) == len(DUMMY_CSV_COLUMNS)

    tr.trash_session.target_name = "Trash, Pigs"
    m.damage.push(DamageEvent(amount=10.0, skill="Sword_Base_Attack",
                              source_addr=PA, target_addr=FOE, t=time.time()))
    tr.update()
    assert dummy_test_csv(tr.trash_session) == ""


def test_profile_swap_reloads_other_characters_history(tmp_path):
    """Switching characters swaps to the new profile's history file."""
    m = _Model()
    m.player_profile_value = "Kartik_Warrior_a1b2"
    tr = DpsTracker(m)
    tr.set_history_dir(tmp_path)
    m.is_dungeon = True             # instance trash: the pulls archive

    # fight + archive as Kartik
    m._units = [_hero(PA), _foe(FOE)]
    tr.update()
    m.damage.push(DamageEvent(amount=50.0, skill="Sword_Base_Attack",
                              source_addr=PA, target_addr=FOE, t=time.time()))
    tr.update()
    tr.reset()
    assert len(tr.history) == 1

    # switch character: different profile resolves on the same model
    m.player_profile_value = "Maru_Rogue_c9d0"
    # the tracker samples the profile on its 2s cadence during update()
    tr._profile_ts = 0.0
    tr.update()

    day = _log_dir(tmp_path)
    assert tr._active_profile == "Maru_Rogue_c9d0"
    assert tr.history == []                   # Kartik's fights not in memory
    assert (day / "trash_Kartik_Warrior_a1b2.json").exists()
    assert not (day / "trash_Maru_Rogue_c9d0.json").exists()

    # Maru fights and archives -> lands in Maru's own day/kind file
    m.damage.push(DamageEvent(amount=30.0, skill="Bow_Base_Attack",
                              source_addr=PA, target_addr=FOE, t=time.time()))
    tr.update()
    tr.reset()
    assert len(tr.history) == 1
    assert (day / "trash_Maru_Rogue_c9d0.json").exists()
    import json
    maru_data = json.loads((day / "trash_Maru_Rogue_c9d0.json")
                           .read_text(encoding="utf-8"))
    assert len(maru_data["history"]) == 1

    # switching BACK to Kartik restores his past fights from his file
    m.player_profile_value = "Kartik_Warrior_a1b2"
    tr._profile_ts = 0.0
    tr.update()
    assert tr._active_profile == "Kartik_Warrior_a1b2"
    assert len(tr.history) == 1


def test_unresolvable_profile_falls_back_to_shared_file(tmp_path):
    """Unresolved profiles use the shared per-day/kind files (no slug)."""
    m = _Model()
    m.player_profile_value = None
    tr = DpsTracker(m)
    tr.set_history_dir(tmp_path)
    m.is_dungeon = True             # instance trash: reset() archives it

    m._units = [_hero(PA), _foe(FOE)]
    tr.update()
    m.damage.push(DamageEvent(amount=25.0, skill="Sword_Base_Attack",
                              source_addr=PA, target_addr=FOE, t=time.time()))
    tr.update()
    tr.reset()
    day = _log_dir(tmp_path)
    assert (day / "trash.json").exists()   # shared day/kind file used
    assert not (tmp_path / "dps_history.json").exists()


def test_history_splits_kind_into_separate_files(tmp_path):
    """Trash, boss and dummy archives each land in their own per-day file,
    never mixed into one history blob."""
    m = _Model()
    m.player_profile_value = "Kartik_Warrior_a1b2"
    tr = DpsTracker(m)
    tr.set_history_dir(tmp_path)
    day = _log_dir(tmp_path)
    m.is_dungeon = True             # instance trash: the fights archive

    # 1) trash fight
    m._units = [_hero(PA), _foe(FOE)]
    tr.update()
    m.damage.push(DamageEvent(amount=50.0, skill="Sword_Base_Attack",
                              source_addr=PA, target_addr=FOE, t=time.time()))
    tr.update()
    tr.reset()
    trash_f = day / "trash_Kartik_Warrior_a1b2.json"
    assert trash_f.exists()

    # 2) boss fight — a fight against a NAMED boss files beside that
    # boss's other runs (<Boss>_<profile>.json), not the day-wide
    # boss_ file: that pairing is what the folder is built on
    m.dungeon_boss = "Boss_Foo"
    m._units = [_hero(PA), _foe(BOSS, uid="Boss_Foo", hp=100000.0,
                                  is_boss=True)]
    tr.update()
    assert tr.in_boss_fight is True
    m.damage.push(DamageEvent(amount=900.0, skill="Sword_Base_Attack",
                              source_addr=PA, target_addr=BOSS, t=time.time()))
    tr.update()
    tr.reset()
    # The per-boss name now carries the kill outcome (2026-10-03): this pull
    # ended in a reset without a kill, so the file reads FAILED.
    boss_files = list(day.glob("Boss Foo_*.json"))
    assert len(boss_files) == 1
    boss_f = boss_files[0]
    assert "_FAILED" in boss_f.stem

    # 3) training dummy parse — dummy yards are OPEN-WORLD benches
    # (dummy_zone_ok refuses a dummy reported inside an instance), so
    # the scene flips back out of the dungeon for this leg
    m.is_dungeon = False
    m.dungeon_boss = None
    m._units = [_hero(PA), training_dummy(BOSS, uid="TrainingDummy", hp=100000.0)]
    tr.update()
    assert tr.in_dummy_fight is True
    m.damage.push(DamageEvent(amount=400.0, skill="Sword_Base_Attack",
                              source_addr=PA, target_addr=BOSS, t=time.time()))
    tr.update()
    tr.reset()
    dummy_f = day / "dummy_Kartik_Warrior_a1b2.json"
    assert dummy_f.exists()

    import json
    kinds = {}
    for p in (trash_f, boss_f, dummy_f):
        data = json.loads(p.read_text(encoding="utf-8"))
        assert len(data["history"]) == 1     # one fight per kind file
        for d in data["history"]:
            kinds.setdefault(d["kind"], []).append(p.name)
    assert kinds["trash"] == ["trash_Kartik_Warrior_a1b2.json"]
    assert kinds["boss"] == [boss_f.name]
    assert kinds["dummy"] == ["dummy_Kartik_Warrior_a1b2.json"]


def test_the_dummy_sheet_shares_the_fight_file_stem(tmp_path):
    """`dummy_Mage.json` (the archived test) sits beside `dummy_Mage.csv` (the
    day's runs as rows): one stem, one vocabulary, and the extension is what
    tells them apart. The fight analysis only opens `*.json` and only accepts
    the kind stems in HISTORY_KIND_STEMS, so the sheet is invisible to it by
    construction — a spreadsheet is not a fight, and a new KIND for it would
    invite exactly the confusion this avoids.

    The pre-release `dummy_tests_*.csv` name is still understood, because the
    pruner reads the character slug out of the file name: mistaking `tests` for
    a character would have it delete a player's own sheet as a stranger's.
    """
    m = _Model()
    m.player_profile_value = "Mage"
    tr = DpsTracker(m)
    tr.set_history_dir(tmp_path)
    tr.dummy_target_s = 0.0
    m._units = [_hero(PA), training_dummy(FOE, uid="Dummy", hp=100000.0)]
    tr.update()
    m.damage.push(DamageEvent(amount=100.0, skill="Sword_Base_Attack",
                              source_addr=PA, target_addr=FOE, t=time.time()))
    tr.update()
    tr.stop_dummy_test()
    day = _log_dir(tmp_path)
    assert (day / "dummy_Mage.json").exists()      # the fight
    assert (day / "dummy_Mage.csv").exists()       # the sheet, same stem
    from farever_companion.ui.combat.history_index import scan_history_dir
    rows = scan_history_dir(tmp_path)
    assert len(rows) == 1 and rows[0]["sessions"][0].kind == "dummy"
    assert all(r["path"].suffix == ".json" for r in rows)   # the sheet is not a fight

    # the legacy name resolves to the same character, and is renamed on load
    from farever_companion.core.dps_tracker_history import DpsTrackerHistory
    legacy = day / "dummy_tests_Mage.csv"
    legacy.write_text("row_kind\nlegacy\n", encoding="utf-8")
    assert DpsTrackerHistory.dummy_csv_slug(legacy) == "Mage"
    assert DpsTrackerHistory.dummy_csv_slug(day / "dummy.csv") == ""
    tr._history_load()
    assert not legacy.exists()
    assert (day / "dummy_Mage.csv").exists()
    assert (day / "dummy_Mage.csv").read_text(encoding="utf-8").endswith("legacy\n")

    # and a legacy file is never deleted as another character's on a prune
    (day / "dummy_tests_Mage.csv").write_text("row_kind\nlegacy\n",
                                              encoding="utf-8")
    tr._persist_history()
    assert (day / "dummy_tests_Mage.csv").exists()
    # on the next load its rows are FOLDED into the current sheet, not dropped
    tr._history_load()
    assert not (day / "dummy_tests_Mage.csv").exists()
    folded = (day / "dummy_Mage.csv").read_text(encoding="utf-8-sig")
    assert folded.count("row_kind") == 1            # one header, not two
    assert folded.endswith("legacy\n")


def test_clearing_old_logs_removes_whole_day_folders_and_leaves_the_rest(tmp_path):
    """The Combat page's "Clear Old Logs" is the only bulk delete in the app,
    so it has to be exact: everything older than the window goes, nothing
    inside it does, and the month folder goes only once it is empty. A window
    of 0 deletes nothing at all — "keep everything" is a real setting, and
    reading it as "delete everything" would be the worst possible surprise."""
    from farever_companion.core.dps_tracker_history import prune_history_older_than

    now = time.time()
    old_day = _log_dir(tmp_path, now - 40 * 86400)
    recent_day = _log_dir(tmp_path, now - 2 * 86400)
    for folder, name in ((old_day, "boss.json"), (recent_day, "trash.json")):
        folder.mkdir(parents=True, exist_ok=True)
        (folder / name).write_text('{"history": []}', encoding="utf-8")
    (recent_day / "dummy_Maru.csv").write_text("row_kind\n", encoding="utf-8")
    empty_month = old_day.parent

    out = prune_history_older_than(tmp_path, 30, now=now)
    assert out["folders"] == 1 and out["files"] == 1
    assert out["cutoff"]
    assert not old_day.exists()
    assert not empty_month.exists()             # the husk went with it
    assert (recent_day / "trash.json").exists()  # inside the window: untouched
    assert (recent_day / "dummy_Maru.csv").exists()

    assert prune_history_older_than(tmp_path, 0, now=now)["folders"] == 0
    assert prune_history_older_than(tmp_path, -5, now=now)["folders"] == 0
    assert (recent_day / "trash.json").exists()
    # a directory that is not there is not an error
    assert prune_history_older_than(tmp_path / "nope", 30, now=now)["folders"] == 0


def test_clearing_old_logs_drops_the_same_fights_from_memory(tmp_path):
    """The disk delete is the easy half. `persist()` writes the tracker's
    in-memory history back out, so a cleanup that only touched the files would
    have the very next archive re-create every folder it just removed."""
    m = _Model()
    m.player_profile_value = "Kartik_Warrior_a1b2"
    tr = DpsTracker(m)
    tr.set_history_dir(tmp_path)
    now = time.time()
    old = CombatSession("Trash Mobs", kind="trash")
    old.start_time = now - 40 * 86400
    fresh = CombatSession("Boss Fight", kind="boss")
    fresh.start_time = now
    tr.history = [old, fresh]
    tr._persist_history()
    assert _log_dir(tmp_path, now - 40 * 86400).exists()

    out = tr.prune_history_older_than(30, directory=tmp_path)
    assert out["folders"] == 1
    assert [s.kind for s in tr.history] == ["boss"]
    assert not _log_dir(tmp_path, now - 40 * 86400).exists()

    # and the next archive does not bring it back
    tr._persist_history()
    assert not _log_dir(tmp_path, now - 40 * 86400).exists()
    assert _log_dir(tmp_path).exists()


def test_history_day_bucketing_and_prune(tmp_path):
    """Sessions persist into their own day's folder; dropping a session from
    memory prunes its now-stale day/kind file so disk mirrors memory."""
    m = _Model()
    tr = DpsTracker(m)
    tr.set_history_dir(tmp_path)

    now = time.time()
    old_day = _log_dir(tmp_path, now - 3 * 86400)
    today = _log_dir(tmp_path, now)
    old = CombatSession("Trash Mobs", kind="trash")
    old.start_time = now - 3 * 86400
    fresh = CombatSession("Boss Fight", kind="boss")
    fresh.start_time = now
    tr.history = [old, fresh]
    tr._persist_history()
    assert (old_day / "trash.json").exists()
    assert (today / "boss.json").exists()

    # drop the old session: re-persisting prunes yesterday's file
    tr.history = [fresh]
    tr._persist_history()
    assert not (old_day / "trash.json").exists()
    assert (today / "boss.json").exists()

    # a fresh tracker still loads today's fight from the day/kind files
    tr2 = DpsTracker(_Model())
    tr2.set_history_dir(tmp_path)
    assert [s.kind for s in tr2.history] == ["boss"]


def test_a_named_difficulty_is_written_into_the_log_file_name(tmp_path):
    """The instance's difficulty is recorded WITH the run - in the session (so
    loading the fight can badge it) and in the log's NAME (so the Fights list
    can show it without opening anything)."""
    from farever_companion.core.dps_tracker_history import read_history_file

    m = _Model()
    tr = DpsTracker(m)
    tr.set_history_dir(tmp_path)

    now = time.time()
    s = CombatSession("Boss Fight", kind="boss")
    s.start_time = now
    s.mode = "hard"                         # what archiving stamps on it
    tr.history = [s]
    tr._persist_history()

    path = _log_dir(tmp_path, now) / "boss_HARD.json"
    assert path.exists()
    assert read_history_file(path)[0].mode == "hard"

    # a fresh tracker reads the fight AND its difficulty back
    tr2 = DpsTracker(_Model())
    tr2.set_history_dir(tmp_path)
    assert tr2.history[0].mode == "hard"


def test_an_unreadable_difficulty_leaves_the_log_name_alone(tmp_path):
    """A GUESS must never be baked into a file name: with nothing read from
    the game the log keeps the plain name and the fight records no mode, so an
    unknown difficulty can never be mistaken for a measured one."""
    from farever_companion.core.dps_tracker_history import read_history_file

    m = _Model()                            # reports no difficulty at all
    tr = DpsTracker(m)
    tr.set_history_dir(tmp_path)

    now = time.time()
    s = CombatSession("Trash Mobs", kind="trash")
    s.start_time = now
    tr.history = [s]
    tr._persist_history()

    day = _log_dir(tmp_path, now)
    plain = day / "trash.json"
    assert plain.exists()
    assert list(day.glob("*_HARD.json")) == []
    assert list(day.glob("*_NORMAL.json")) == []
    assert list(day.glob("*_HEROIC.json")) == []
    assert read_history_file(plain)[0].mode == ""


def test_archiving_a_fight_stamps_the_difficulty_the_game_reported(tmp_path):
    """End to end through the live path: a dungeon trash run is archived with
    the difficulty the game named, and the file lands under that token."""
    m = _Model()
    m.is_dungeon = True                     # inside an instance
    m.detected_mode = lambda: "HEROIC"      # normalised, not trusted verbatim
    tr = DpsTracker(m)
    tr.set_history_dir(tmp_path)
    m._units = [_hero(PA), _foe(FOE)]
    tr.update()
    m.damage.push(DamageEvent(amount=50.0, skill="Sword_Base_Attack",
                              source_addr=PA, target_addr=FOE, t=time.time()))
    tr.update()
    tr.reset()

    day = _log_dir(tmp_path)
    assert (day / "trash_HEROIC.json").exists()
    assert tr.history[-1].mode == "heroic"


def test_history_migrates_legacy_flat_file(tmp_path):
    """Legacy dps_history.json files fold into per-day/kind files on load,
    then the flat file is removed."""
    import json
    # Dated NOW, the way a real migrating file looks: the migration still
    # folds it into its own day folder, and today-only load() then shows it.
    t0 = time.time()
    raw = {"history": [{
        "name": "Boss Fight (Boss Fight)", "kind": "boss",
        "start_time": t0, "end_time": t0 + 100.0, "state": "PAUSED",
        "target": "👑 Old Nightking", "players": [], "timeline": {},
        "deaths": [], "burst_peak": 0.0, "burst_peak_time": 0.0,
    }]}
    (tmp_path / "dps_history.json").write_text(json.dumps(raw), encoding="utf-8")

    tr = DpsTracker(_Model())
    tr.set_history_dir(tmp_path)
    day = _log_dir(tmp_path, t0)
    migrated = day / "boss.json"
    assert migrated.exists()
    assert not (tmp_path / "dps_history.json").exists()
    assert len(tr.history) == 1
    assert tr.history[0].kind == "boss"
    assert tr.history[0].target_name == "👑 Old Nightking"


def test_history_scan_reads_month_day_layout(tmp_path):
    """scan_history_dir() finds the <YYYY-MM>/<DD>/<kind>_<profile> files the
    writer lays down and labels the character from the file name. The scanner
    imports the folder patterns from the writer, so a layout change cannot
    leave the Combat page quietly showing an empty history."""
    from farever_companion.ui.combat.history_index import scan_history_dir

    m = _Model()
    m.player_profile_value = "Kartik_Warrior_a1b2"
    tr = DpsTracker(m)
    tr.set_history_dir(tmp_path)
    m.is_dungeon = True             # instance trash: reset() archives it
    m._units = [_hero(PA), _foe(FOE)]
    tr.update()
    m.damage.push(DamageEvent(amount=50.0, skill="Sword_Base_Attack",
                              source_addr=PA, target_addr=FOE, t=time.time()))
    tr.update()
    tr.reset()

    rows = scan_history_dir(tmp_path)
    assert len(rows) == 1
    row = rows[0]
    assert row["char"] == "Kartik Warrior A1b2"
    assert len(row["sessions"]) == 1
    assert row["sessions"][0].kind == "trash"
    assert row["path"].name == "trash_Kartik_Warrior_a1b2.json"
    lt = time.localtime()
    assert row["path"].parent.name == time.strftime("%d", lt)
    assert row["path"].parent.parent.name == time.strftime("%Y-%m", lt)

    # a <DD> folder sitting at the root (an interrupted migration, or a month
    # folder that never got written) must still be read, not silently skipped
    stray = tmp_path / time.strftime("%d", lt)
    stray.mkdir(parents=True, exist_ok=True)
    (stray / "boss_Kartik_Warrior_a1b2.json").write_text(
        '{"history": [{"name": "Boss", "kind": "boss", "start_time": 1000.0,'
        ' "end_time": 1100.0, "state": "PAUSED", "target": "👑 Trashy",'
        ' "players": [], "timeline": {}, "deaths": [], "burst_peak": 0.0,'
        ' "burst_peak_time": 0.0}]}', encoding="utf-8")
    rows2 = scan_history_dir(tmp_path)
    assert len(rows2) == 2
    assert any(r["sessions"][0].kind == "boss" for r in rows2)


def test_history_scan_labels_per_boss_files_with_the_character(tmp_path):
    """A per-boss file is filed under the BOSS, but every surface that
    reads it tags the CHARACTER — and the tag comes from the FILE NAME
    alone. The profile slug is everything after the first underscore
    (the same boundary `is_boss_file` reads), so the label never opens
    the log: contents that name another character, or no boss label at
    all, change nothing."""
    import json
    from farever_companion.ui.combat.history_index import scan_history_dir

    def _fight(kind, boss_label="King Ratsar"):
        return {
            "name": "King Ratsar", "kind": kind,
            "boss_label": boss_label,
            "start_time": 1000.0, "end_time": 1100.0, "state": "PAUSED",
            "target_name": "👑 King Ratsar",
            "players": [{"name": "Kartik", "is_me": True,
                         "hero_class": "Warrior", "total_damage": 500.0}],
            "timeline": {}, "deaths": [], "burst_peak": 0.0,
            "burst_peak_time": 0.0,
        }

    day = _log_dir(tmp_path)
    day.mkdir(parents=True, exist_ok=True)
    (day / "King Ratsar_Kartik_Warrior_a1b2.json").write_text(
        json.dumps({"history": [_fight("boss")]}),
        encoding="utf-8")
    rows = scan_history_dir(tmp_path)
    assert len(rows) == 1
    assert rows[0]["char"] == "Kartik Warrior A1b2"
    assert rows[0]["sessions"][0].kind == "boss"

    # the trash half of the same run: same boss label, prefixed name
    (day / "Trash pack King Ratsar_Kartik_Warrior_a1b2.json").write_text(
        json.dumps({"history": [_fight("trash")]}), encoding="utf-8")
    rows2 = scan_history_dir(tmp_path)
    assert len(rows2) == 2
    assert {r["char"] for r in rows2} == {"Kartik Warrior A1b2"}

    # the label is the NAME, not the contents: this log names Bee Mage
    # and records no boss label, yet the file says Kartik Warrior
    (day / "Nameless Kills_Kartik_Warrior_a1b2.json").write_text(
        json.dumps({"history": [{
            "name": "Nameless Kills", "kind": "boss", "boss_label": None,
            "start_time": 1000.0, "end_time": 1100.0, "state": "PAUSED",
            "target_name": None,
            "players": [{"name": "Bee", "is_me": True,
                         "hero_class": "Mage", "total_damage": 5.0}],
            "timeline": {}, "deaths": [], "burst_peak": 0.0,
            "burst_peak_time": 0.0}]}),
        encoding="utf-8")
    rows3 = scan_history_dir(tmp_path)
    assert len(rows3) == 3
    assert {r["char"] for r in rows3} == {"Kartik Warrior A1b2"}

    # a single-word slug is still a character name, and a file that
    # names no profile at all says so honestly
    (day / "Mystery_Mystery.json").write_text(
        json.dumps({"history": [_fight("boss", boss_label=None)]}),
        encoding="utf-8")
    (day / "boss.json").write_text(
        json.dumps({"history": [_fight("boss", boss_label=None)]}),
        encoding="utf-8")
    rows4 = scan_history_dir(tmp_path)
    assert len(rows4) == 5
    assert {r["char"] for r in rows4} == {"Kartik Warrior A1b2",
                                          "Mystery", "Current Character"}


# --- weapon loadout (derived from used skill ids) --------------------------


def test_weapon_families_derived_from_used_skills():
    """Used skill ids reveal the weapon family; casts and class prefixes add nothing."""
    from farever_companion.core.dps_tracker import weapon_families_used
    m = _Model()
    tr = DpsTracker(m)
    m._units = [_hero(PA), _foe(FOE)]
    tr.update()
    for sid, amt in (("Axe_Base_Attack", 50.0), ("Axe_Base_Attack2", 40.0),
                     ("Shield_OrbitWater_S1_OrbStatus", 1.0),
                     ("Sunlight", 30.0)):
        m.damage.push(DamageEvent(amount=amt, skill=sid,
                                  source_addr=PA, target_addr=FOE,
                                  t=time.time()))
    tr.update()

    assert weapon_families_used(tr.overall_session) == ["Axe", "Shield"]


def test_weapon_families_ignore_class_prefix_and_empty():
    from farever_companion.core.dps_tracker import weapon_families_used
    # only a class-prefixed status skill -> no weapon family (can't tell)
    m = _Model()
    tr = DpsTracker(m)
    m._units = [_hero(PA), _foe(FOE)]
    tr.update()
    m.damage.push(DamageEvent(amount=9.0, skill="Warrior_Hemorrhage_Status",
                              source_addr=PA, target_addr=FOE, t=time.time()))
    tr.update()
    assert weapon_families_used(tr.overall_session) == []




def test_owned_item_proc_ticks_merge_into_me():
    """Unresolved owned-item proc ticks re-credit to the local player."""
    m = _Model()
    tr = DpsTracker(m)
    m._units = [_hero(PA), _foe(FOE)]
    tr.update()

    proc_src = 0x9ABC   # the item's proc entity (never a scene hero)
    t0 = time.time()
    m.damage.push(DamageEvent(amount=35.0,
                              skill="Finger of Sukuna'Maatata",
                              source_addr=proc_src, target_addr=FOE,
                              t=t0))
    m.damage.push(DamageEvent(amount=41.0,
                              skill="Finger of Sukuna'Maatata",
                              source_addr=proc_src, target_addr=FOE,
                              t=t0 + 0.02))
    tr.update()

    s = tr.overall_session
    assert abs(s.group_damage - 76.0) < 1e-6
    me = s.players.get("Me (You)")
    assert me is not None and abs(me.total_damage - 76.0) < 1e-6
    assert "Finger of Sukuna'Maatata" in me.skills
    assert not any(k.startswith("Party_") for k in s.players)

    # a second burst from the SAME proc source is remapped instantly
    m.damage.push(DamageEvent(amount=37.0,
                              skill="Finger of Sukuna'Maatata",
                              source_addr=proc_src, target_addr=FOE,
                              t=t0 + 0.04))
    tr.update()
    assert abs(s.players["Me (You)"].total_damage - 113.0) < 1e-6


def test_unowned_unresolved_source_stays_party_row():
    """Unresolved sources with other skills stay under their Party_XXXX row."""
    m = _Model()
    tr = DpsTracker(m)
    m._units = [_hero(PA), _foe(FOE)]
    tr.update()

    m.damage.push(DamageEvent(amount=12.0, skill="Mob_Bite",
                              source_addr=0x7777, target_addr=FOE,
                              t=time.time()))
    tr.update()

    s = tr.overall_session
    assert abs(s.group_damage - 12.0) < 1e-6
    assert s.players.get("Me (You)") is None or s.players["Me (You)"].total_damage == 0.0
    party = [k for k in s.players if k.startswith("Party_")]
    assert party and abs(s.players[party[0]].total_damage - 12.0) < 1e-6


def test_history_legacy_target_key_restored(tmp_path):
    """Archives with the legacy 'target' key restore their boss name."""
    import json
    from farever_companion.core.dps_tracker import read_history_file

    raw = {"history": [{
        "name": "Boss Fight (Boss Fight)", "kind": "boss",
        "start_time": 1000.0, "end_time": 1100.0, "state": "PAUSED",
        "target": "👑 Old Nightking", "players": [], "timeline": {},
        "deaths": [], "burst_peak": 0.0, "burst_peak_time": 0.0,
    }]}
    p = tmp_path / "dps_history_Legacy.json"
    p.write_text(json.dumps(raw), encoding="utf-8")

    s = read_history_file(p)[0]
    assert s.target_name == "👑 Old Nightking"


# --- class chip: game truth beats first-skill guessing ----------------------


def test_row_class_comes_from_scene_not_first_skill():
    """The class chip shows the scene's real class, never a skill guess."""
    m = _Model()
    tr = DpsTracker(m)
    # Local hero IS a Priest; the scene knows it before any damage lands.
    m._units = [_hero(PA, cls="ent.hero.Priest"), _foe(FOE)]
    tr.update()
    me = tr.overall_session.players.get("Me (You)")
    assert me is not None and me.hero_class == "Priest"

    # First damage uses a shared weapon id that keyword-guesses Warrior -
    # the row must keep the scene-known class instead.
    m.damage.push(DamageEvent(amount=40.0, skill="Sword_Base_Attack",
                              source_addr=PA, target_addr=FOE,
                              t=time.time()))
    tr.update()
    assert tr.overall_session.players["Me (You)"].hero_class == "Priest"


def test_scene_ally_class_overrides_stale_inference():
    """A teammate's scene class replaces the event-inferred class on sight."""
    m = _Model()
    tr = DpsTracker(m)
    m._units = [_hero(PA), _foe(FOE)]
    tr.update()

    # Teammate far away: only their damage events arrive (shared axe id).
    m.damage.push(DamageEvent(amount=25.0, skill="Axe_Base_Attack",
                              source_addr=TEAM, target_addr=FOE,
                              t=time.time()))
    tr.update()
    ally = tr.overall_session.players.get("Teammate")
    assert ally is not None and ally.hero_class == "Warrior"  # inference only

    # Teammate walks into the scene as a Mage -> scene class wins.
    m._units = [_hero(PA), _hero(TEAM, cls="ent.hero.Mage"), _foe(FOE)]
    tr.update()
    assert tr.overall_session.players["Teammate"].hero_class == "Mage"


def test_infer_class_prefers_game_class_prefix():
    """Class-prefixed skill ids beat keyword guessing for inference."""
    from farever_companion.core.dps_tracker import (
        infer_class_from_skill, hero_cls_to_class,
    )
    assert infer_class_from_skill("Priest_Smite") == "Priest"
    assert infer_class_from_skill("Warrior_Whirlwind") == "Warrior"
    assert infer_class_from_skill("Rogue_Backstab") == "Rogue"
    assert infer_class_from_skill("Mage_Fireball") == "Mage"
    assert infer_class_from_skill("Axe_Base_Attack") == "Warrior"  # keyword
    assert infer_class_from_skill("Regen") == "Priest"             # keyword
    assert hero_cls_to_class("ent.hero.Priest") == "Priest"
    assert hero_cls_to_class("ent.hero.Warrior") == "Warrior"
    assert hero_cls_to_class("ent.hero.Mage") == "Mage"
    assert hero_cls_to_class("ent.hero.Rogue") == "Rogue"
    assert hero_cls_to_class("ent.Hero") == ""
    assert hero_cls_to_class("ent.hero.Druid") == ""
    assert hero_cls_to_class("") == ""


# --- game-authoritative combat signals (auxiliary to idle heuristics) ------
def test_combat_poll_tracks_game_edges():
    """The combat poll records game-declared edges and clears on stable reads."""
    m = _Model()
    tr = DpsTracker(m)

    m.combat_state_value = {"in_combat": True, "combat_id": 7,
                            "combat_start": 100.0, "combat_end": 0.0}
    tr._combat_poll_at = 0.0
    tr._poll_combat_state(PA)
    assert tr.combat_state is m.combat_state_value
    assert tr._combat_id == 7
    assert tr._combat_was_in is True
    assert tr._game_combat_end_at == 0.0

    # game says the fight is over (isInCombat false edge)
    m.combat_state_value = {"in_combat": False, "combat_id": 7,
                            "combat_start": 0.0, "combat_end": 130.0}
    tr._combat_poll_at = 0.0
    tr._poll_combat_state(PA)
    assert tr._game_combat_end_at > 0.0
    assert tr._combat_was_in is False

    # re-entry with a NEW combat id (rotation) also marks the previous
    # encounter as ended
    m.combat_state_value = {"in_combat": True, "combat_id": 8,
                            "combat_start": 140.0, "combat_end": 0.0}
    tr._combat_poll_at = 0.0
    tr._poll_combat_state(PA)
    assert tr._game_combat_end_at > 0.0
    assert tr._combat_id == 8

    # stable id while in combat clears the pending end signal
    m.combat_state_value = {"in_combat": True, "combat_id": 8,
                            "combat_start": 150.0, "combat_end": 0.0}
    tr._combat_poll_at = 0.0
    tr._poll_combat_state(PA)
    assert tr._game_combat_end_at == 0.0


def test_game_encounter_elapsed_anchors_on_start_change():
    """game_encounter_elapsed anchors on start changes and clears out of combat."""
    m = _Model()
    tr = DpsTracker(m)

    # no snapshot / out of combat -> None
    assert tr.game_encounter_elapsed is None
    m.combat_state_value = {"in_combat": False, "combat_id": 5,
                            "combat_start": 0.0, "combat_end": 0.0}
    tr._combat_poll_at = 0.0
    tr._poll_combat_state(PA)
    assert tr.game_encounter_elapsed is None

    # fight starts: combatStartTime appears -> anchored (~0 s now)
    m.combat_state_value = {"in_combat": True, "combat_id": 6,
                            "combat_start": 900.0, "combat_end": 0.0}
    tr._combat_poll_at = 0.0
    tr._poll_combat_state(PA)
    el = tr.game_encounter_elapsed
    assert el is not None and 0.0 <= el < 2.0

    # a later, stable read does NOT re-anchor (timer keeps running)
    time.sleep(0.01)
    m.combat_state_value = {"in_combat": True, "combat_id": 6,
                            "combat_start": 900.0, "combat_end": 0.0}
    tr._combat_poll_at = 0.0
    tr._poll_combat_state(PA)
    assert tr.game_encounter_elapsed >= el

    # combatStartTime moving to a NEW fight start re-anchors the timer
    m.combat_state_value = {"in_combat": True, "combat_id": 7,
                            "combat_start": 940.0, "combat_end": 0.0}
    tr._combat_poll_at = 0.0
    tr._poll_combat_state(PA)
    el2 = tr.game_encounter_elapsed
    assert el2 is not None and el2 < el + 0.5

    # combat over -> no live timer (the sealed fight shows its own duration)
    m.combat_state_value = {"in_combat": False, "combat_id": 7,
                            "combat_start": 0.0, "combat_end": 955.0}
    tr._combat_poll_at = 0.0
    tr._poll_combat_state(PA)
    assert tr.game_encounter_elapsed is None


def test_game_combat_signal_confirms_idle_boss_end():
    """A game-declared end plus damage idle seals the boss fight early."""
    m = _Model()
    m.dungeon_boss = "Boss_Foo"
    tr = DpsTracker(m)
    m._units = [_hero(PA), _foe(BOSS, uid="Boss_Foo", hp=100000.0,
                                  is_boss=True)]
    tr.update()
    assert tr.in_boss_fight is True

    m.damage.push(DamageEvent(amount=500.0, skill="Sword_Base_Attack",
                              source_addr=PA, target_addr=BOSS,
                              t=time.time()))
    tr.update()
    assert abs(tr.boss_session.group_damage - 500.0) < 1e-6

    # Boss leaves the scene; the game declared the fight over a while ago
    # and the fight has been damage-idle well past BOSS_IDLE_AUX_S.
    m._units = [_hero(PA)]
    tr._game_combat_end_at = time.time() - (GAME_COMBAT_END_CONFIRM_S + 1.0)
    tr.boss_session.last_event_time = time.time() - 10.0
    tr._boss_last_seen = time.time() - 10.0
    tr.update()

    assert tr.in_boss_fight is False
    boss_archives = [s for s in tr.history if s.kind == "boss"]
    assert boss_archives and abs(boss_archives[-1].group_damage - 500.0) < 1e-6


def test_game_signal_alone_does_not_end_live_boss_fight():
    """The game signal alone never seals a live (recent-damage) fight."""
    m = _Model()
    m.dungeon_boss = "Boss_Foo"
    tr = DpsTracker(m)
    m._units = [_hero(PA), _foe(BOSS, uid="Boss_Foo", hp=100000.0,
                                  is_boss=True)]
    tr.update()
    m.damage.push(DamageEvent(amount=500.0, skill="Sword_Base_Attack",
                              source_addr=PA, target_addr=BOSS,
                              t=time.time()))
    tr.update()
    assert tr.in_boss_fight is True

    # game signal set (even stale), boss gone from the scene, but damage
    # just landed -> idle ~0 < BOSS_IDLE_AUX_S -> fight must keep going
    m._units = [_hero(PA)]
    tr._game_combat_end_at = time.time() - 10.0
    tr.update()
    assert tr.in_boss_fight is True


def test_boss_start_clears_stale_game_end_signal():
    """A new boss engagement clears any stale game-end signal."""
    m = _Model()
    m.dungeon_boss = "Boss_Foo"
    tr = DpsTracker(m)
    # stale signal from a previous encounter (e.g. the game never flipped
    # isInCombat back on in a reliable way)
    tr._game_combat_end_at = time.time() - 60.0

    m._units = [_hero(PA), _foe(BOSS, uid="Boss_Foo", hp=100000.0,
                                  is_boss=True)]
    tr.update()
    assert tr.in_boss_fight is True
    assert tr._game_combat_end_at == 0.0


def test_boss_target_pinned_across_single_tick_scan_flicker():
    """A rift can scan two boss-tagged entities, and the engaged boss can drop
    out of the unit scan for a single poll. The meter must NOT hop to the
    other boss on that one miss (that made the label alternate between the
    two every tick) — it keeps the engaged boss until it has been gone past
    the relatch grace."""
    from farever_companion.core.scene import Entity

    def _demon(addr, x):
        return Entity(addr=addr, cls="ent.Foe", unit_id="DemonSuperElite",
                      x=x, y=1.0, z=1.0, hp=100000.0, is_foe=True)

    A = 0x41A1
    B = 0x41B2
    m = _Model()
    tr = DpsTracker(m)
    m._units = [_hero(PA), _demon(A, 5.0), _demon(B, 60.0)]
    tr.update()
    assert tr.in_boss_fight is True
    assert tr.boss_session.target_addr == A      # nearest boss engaged
    name_a = tr.boss_session.target_name

    # A flickers out of the scan for one tick while B is still present
    m._units = [_hero(PA), _demon(B, 60.0)]
    tr.update()
    assert tr.boss_session.target_addr == A      # no hop on a single miss
    assert tr.boss_session.target_name == name_a

    # A is back; the latch is unchanged
    m._units = [_hero(PA), _demon(A, 5.0), _demon(B, 60.0)]
    tr.update()
    assert tr.boss_session.target_addr == A
    assert tr.boss_session.target_name == name_a

    # ...and repeated single-tick flickers never flip it either
    for i in range(5):
        m._units = [_hero(PA), (_demon(B, 60.0) if i % 2 == 0
                                else _demon(A, 5.0))]
        tr.update()
    assert tr.boss_session.target_addr == A


def test_boss_target_relatches_after_sustained_absence():
    """Once the engaged boss is genuinely gone (past the grace window) the
    tracker re-targets — preferring the same boss on a fresh address — so a
    legitimate despawn/respawn (new address) keeps one continuous fight
    instead of a dead lock on the old address."""
    from farever_companion.core.scene import Entity

    def _demon(addr, x):
        return Entity(addr=addr, cls="ent.Foe", unit_id="DemonSuperElite",
                      x=x, y=1.0, z=1.0, hp=100000.0, is_foe=True)

    A = 0x41A1
    A2 = 0x41C3          # same boss, fresh address after respawn
    B = 0x41B2
    m = _Model()
    tr = DpsTracker(m)
    tr._boss_relatch_grace_s = 0.0              # make the grace window instant
    m._units = [_hero(PA), _demon(A, 5.0), _demon(B, 60.0)]
    tr.update()
    assert tr.in_boss_fight is True
    assert tr.boss_session.target_addr == A
    name_a = tr.boss_session.target_name

    # A is gone for good. The FAR boss iterates first in the scan, but the
    # respawn at the arena (A2, nearer to the player) must win over it.
    m._units = [_hero(PA), _demon(B, 60.0), _demon(A2, 5.0)]
    tr.update()
    assert tr.boss_session.target_addr == A2    # nearest boss re-engaged
    assert tr.boss_session.target_name == name_a


def test_boss_engagement_picks_nearest_boss_when_two_present():
    """Rift scans are instance-wide: with two boss-tagged units alive at
    engagement (a staged/second one somewhere in the same instance) the meter
    names the boss being fought — the nearest — not whichever unit the scan
    happens to iterate first."""
    from farever_companion.core.scene import Entity

    def _demon(addr, x):
        return Entity(addr=addr, cls="ent.Foe", unit_id="DemonSuperElite",
                      x=x, y=1.0, z=1.0, hp=100000.0, is_foe=True)

    A = 0x41A1
    B = 0x41B2
    m = _Model()
    tr = DpsTracker(m)
    # the FAR boss iterates first in the scan, the near one second
    m._units = [_hero(PA), _demon(B, 60.0), _demon(A, 5.0)]
    tr.update()
    assert tr.in_boss_fight is True
    assert tr.boss_session.target_addr == A      # nearest, not first-listed


def test_a_boss_classed_dungeon_trash_never_engages_as_the_boss():
    """The engine's boss CLASS is a fallback for units the data layer has never
    heard of - not a second opinion about one it knows.

    Lost City of Mayda's trash crab is spawned from the `ent.boss.Crabgantua`
    prefab, so the class alone promoted it to "the boss": the meter opened a
    boss fight on the crab and swallowed the pre-boss pulls into it, and the
    run never split (live 2026-10-02: "top dps in dungeon did not split the
    fight before the boss fight like the rift does").
    """
    from farever_companion.core.scene import Entity
    from farever_companion.data import units as udata

    assert udata.is_known_unit("Crab_Z1D")      # the CDB has the row...
    assert not udata.is_boss("Crab_Z1D")        # ...and it says trash

    m = _Model()
    tr = DpsTracker(m)
    m.is_dungeon = True
    tr._instance_edge_debounce_s = 0.0
    crab = Entity(addr=FOE, cls="ent.boss.Crabgantua", unit_id="Crab_Z1D",
                  x=5.0, y=5.0, z=5.0, hp=900.0, is_foe=True)
    m._units = [_hero(PA), crab]
    tr.update()
    assert tr.in_boss_fight is False

    # the crab is ordinary dungeon trash
    m.damage.push(DamageEvent(amount=300.0, skill="Sword_Base_Attack",
                              source_addr=PA, target_addr=FOE, t=time.time()))
    tr.update()
    assert tr.boss_session.group_damage == 0.0
    assert abs(tr.trash_session.group_damage - 300.0) < 1e-6

    # the instance's real boss appears and the pulls archive as the paired
    # trash pack - the split the rift already gets
    boss_addr = 0x41A1
    m._units = [_hero(PA), _foe(boss_addr, uid="Nepsilon", hp=7872.0)]
    tr.update()
    assert tr.in_boss_fight is True
    trashed = [s for s in tr.history if s.kind == "trash"]
    assert len(trashed) == 1
    assert abs(trashed[0].group_damage - 300.0) < 1e-6
    assert trashed[0].boss_label == "Nepsilon"


def test_an_add_cannot_hijack_the_engaged_boss_or_confirm_its_kill():
    """The boss session is pinned by the ENGAGE, not by the last unit hit.

    An encounter names its adds after the boss they belong to
    (`Nepsilon_Totem` is a Nepsilon pull's totem), and the name-containment
    route plus a hit-driven re-pin used to hand the session to the add: the
    add's death then confirmed a boss kill, the fight ended early as a run of
    short "Boss Fight" rows and the archive was named after the add (live
    2026-10-02: `Tidal Totem_<profile>.json` holding a Nepsilon pull).
    """
    m = _Model()
    tr = DpsTracker(m)
    boss_addr, totem_addr = 0x41A1, 0x41B2
    m._units = [_hero(PA), _foe(boss_addr, uid="Nepsilon", hp=7872.0),
                _foe(totem_addr, uid="Nepsilon_Totem", hp=2280.0)]
    tr.update()
    assert tr.in_boss_fight is True
    boss_name = tr.boss_session.target_name

    m.damage.push(DamageEvent(amount=500.0, skill="Sword_Base_Attack",
                              source_addr=PA, target_addr=boss_addr,
                              t=time.time()))
    # the add's hit, named on the wire by its raw id
    m.damage.push(DamageEvent(amount=250.0, skill="Axe_Base_Attack",
                              source_addr=PA, target_addr=totem_addr,
                              target_name="Nepsilon_Totem", t=time.time()))
    tr.update()
    assert tr.boss_session.target_addr == boss_addr
    assert tr.boss_session.target_name == boss_name
    assert abs(tr.boss_session.group_damage - 500.0) < 1e-6
    assert abs(tr.boss_adds_session.group_damage - 250.0) < 1e-6

    # ...and the add dying is not the boss dying
    m._units = [_hero(PA), _foe(boss_addr, uid="Nepsilon", hp=7000.0)]
    m.damage.push(DamageEvent(amount=0.0, kill=True,
                              target_name="Nepsilon_Totem", t=time.time()))
    tr.update()
    assert tr.in_boss_fight is True
    assert tr._boss_kill_pending == 0.0


def test_boss_crown_never_alternates_between_raw_id_and_display_name():
    """The Top DPS crown label is ``session.target_name``. Bridge/DLL damage
    events can name the engaged rift boss by its RAW internal id
    ("DemonSuperElite") while the unit scan resolves the display name
    ("Nightking Maat Demon"); both spellings land on the session every tick
    and must converge on the display name instead of flipping the label."""
    from farever_companion.core.scene import Entity
    from farever_companion.data import names as data_names

    m = _Model()
    tr = DpsTracker(m)
    disp = "👑 " + (data_names.unit_name("DemonSuperElite")
                    or "DemonSuperElite")
    raw = "👑 DemonSuperElite"

    def _boss(hp=100000.0):
        return Entity(addr=BOSS, cls="ent.Foe", unit_id="DemonSuperElite",
                      x=5.0, y=5.0, z=5.0, hp=hp, is_foe=True)

    m._units = [_hero(PA), _boss()]
    tr.update()                          # scene scan engages the rift boss
    assert tr.in_boss_fight is True
    assert tr.boss_session.target_name == disp
    assert disp != raw                   # data resolves a real display name

    # raw-id events keep landing on the boss; scan ticks keep refreshing the
    # resolved name. The crown must never flip to the raw spelling.
    for i in range(6):
        m.damage.push(DamageEvent(amount=10.0, skill="Sword_Base_Attack",
                                  source_addr=PA, target_addr=BOSS,
                                  target_name="DemonSuperElite",
                                  t=time.time()))
        m._units = [_hero(PA), _boss(hp=100000.0 - (i + 1) * 10.0)]
        tr.update()
        assert tr.boss_session.target_name == disp, \
            f"tick {i}: {tr.boss_session.target_name!r} != {disp!r}"

    # event-only ticks (boss out of the scan, hits still arriving) stay
    # display-named too — this is the bridge-heavy path that flipped it
    m.damage.push(DamageEvent(amount=15.0, skill="Sword_Base_Attack",
                              source_addr=PA, target_addr=BOSS,
                              target_name="DemonSuperElite",
                              t=time.time()))
    m._units = [_hero(PA)]
    tr.update()
    assert tr.boss_session.target_name == disp
    assert tr.boss_session.target_addr == BOSS   # latch held
    assert tr.boss_session.group_damage == 75.0

    # a damage-driven re-engage (scene missed the boss) also crowns display
    tr.reset()
    m._units = [_hero(PA)]
    m.damage.push(DamageEvent(amount=20.0, skill="Sword_Base_Attack",
                              source_addr=PA, target_addr=BOSS,
                              target_name="DemonSuperElite",
                              t=time.time()))
    tr.update()
    assert tr.in_boss_fight is True
    assert tr.boss_session.target_name == disp


def test_dummy_crown_keeps_display_name_and_emblem_under_raw_id_events():
    """Dummies get the same treatment: events can carry the raw unit id
    ("TrainingDummy") while the scan stores "🎯 Training Dummy", and the
    session label must keep ONE spelling AND its 🎯 emblem — record_hit used
    to drop the 🎯 on the first hit, flapping it back on the next scan."""
    from farever_companion.core.scene import Entity
    from farever_companion.data import names as data_names

    m = _Model()
    tr = DpsTracker(m)
    DUMMY = 0x6666
    disp = "🎯 " + (data_names.unit_name("TrainingDummy")
                    or "TrainingDummy")
    assert disp != "🎯 TrainingDummy"     # resolves to a spaced display name

    def _dummy(hp=100000.0):
        # The shared fixture puts the dummy AT the player, inside the default
        # Dummy Range — a hand-built entity at 5,5,5 would sit outside it and
        # make this test about the radius instead of the emblem.
        return training_dummy(DUMMY, uid="TrainingDummy", hp=hp)

    m._units = [_hero(PA), _dummy()]
    tr.update()                          # scene scan engages the dummy
    assert tr.in_dummy_fight is True
    assert tr.dummy_session.target_name == disp

    # raw-id hits keep landing; scan ticks keep refreshing — label stable
    for i in range(4):
        m.damage.push(DamageEvent(amount=25.0, skill="Sword_Base_Attack",
                                  source_addr=PA, target_addr=DUMMY,
                                  target_name="TrainingDummy",
                                  t=time.time()))
        m._units = [_hero(PA), _dummy(hp=100000.0 - (i + 1) * 25.0)]
        tr.update()
        assert tr.dummy_session.target_name == disp, \
            f"tick {i}: {tr.dummy_session.target_name!r} != {disp!r}"

    # event-only tick (dummy out of the scan) keeps spelling AND emblem
    m.damage.push(DamageEvent(amount=25.0, skill="Sword_Base_Attack",
                              source_addr=PA, target_addr=DUMMY,
                              target_name="TrainingDummy", t=time.time()))
    m._units = [_hero(PA)]
    tr.update()
    assert tr.dummy_session.target_name == disp
    assert tr.dummy_session.target_addr == DUMMY
    assert abs(tr.dummy_session.group_damage - 125.0) < 1e-6

    # heal-path engage: a raw-id heal event starts the dummy fight and must
    # label it the same display way the scan would
    tr.reset()
    m._units = [_hero(PA)]
    m.damage.push(DamageEvent(kind=K_HEAL, amount=40.0, skill="Regen",
                              source_addr=PA, target_addr=DUMMY,
                              target_name="TrainingDummy", t=time.time()))
    tr.update()
    assert tr.in_dummy_fight is True
    assert tr.dummy_session.target_name == disp
    assert abs(tr.dummy_session.group_heals - 40.0) < 1e-6


def test_dummy_heal_engage_never_doubles_emblem():
    """A heal event naming the dummy in its already-emblemed scan form must
    not store a doubled '🎯 🎯 ...' session label (which the overlay then
    showed verbatim on open)."""
    from farever_companion.data import names as data_names

    m = _Model()
    tr = DpsTracker(m)
    DUMMY = 0x6666
    disp = "🎯 " + (data_names.unit_name("TrainingDummy")
                    or "TrainingDummy")
    m._units = [_hero(PA)]
    m.damage.push(DamageEvent(kind=K_HEAL, amount=40.0, skill="Regen",
                              source_addr=PA, target_addr=DUMMY,
                              target_name=disp, t=time.time()))
    tr.update()
    assert tr.in_dummy_fight is True
    assert tr.dummy_session.target_name == disp
    assert "🎯 🎯" not in tr.dummy_session.target_name


def test_solo_unresolvable_source_credits_me_not_party_xxxx():
    """Solo at a dummy, a proc/status hit whose serverSource is the proc
    object (not the hero) failed every name resolution and minted a fake
    `Party_XXXX` party member out of the user's own DPS (live 2026-09-16:
    Party_77F8 + Party_8568 on the overlay with nobody else in the world).
    When the roster says SOLO, an unresolvable source must credit to the
    local player — nobody else could have dealt it."""
    from farever_companion.core.group_reader import GroupSnapshot, GroupMember
    m = _Model()
    m._units = [_hero(PA), _foe(FOE)]
    m.roster = GroupSnapshot(group=0x6000, members=[])   # the game's solo roster
    tr = DpsTracker(m)
    tr.update()
    proc_src = 0x77F8                    # a proc/status object address
    m.damage.push(DamageEvent(amount=30.0, skill="Axe_Boomerang_Skill1_Status",
                              source_addr=proc_src, target_addr=FOE, t=time.time()))
    tr.update()
    s = tr.overall_session
    assert not any(n.startswith("Party_") for n in s.players)
    me = next((p for p in s.players.values() if p.is_me), None)
    assert me is not None and abs(me.total_damage - 30.0) < 1e-6

    # grouped: the same unresolvable source keeps the fold-able Party_ row
    m2 = _Model()
    m2._units = [_hero(PA), _foe(FOE)]
    m2.roster = GroupSnapshot(group=0x6001, members=[
        GroupMember(name="Me", player=0, hero=PA, is_me=True),
        GroupMember(name="Ally", player=0, hero=TEAM),
    ])
    tr2 = DpsTracker(m2)
    tr2.update()
    m2.damage.push(DamageEvent(amount=12.0, skill="Axe_Boomerang_Skill1_Status",
                               source_addr=proc_src, target_addr=FOE, t=time.time()))
    tr2.update()
    s2 = tr2.overall_session
    assert any(n.startswith("Party_") for n in s2.players), \
        "grouped: an unresolved source may belong to an off-scene ally — keep the fold-able row"


def test_instance_solo_unresolvable_source_credits_me_not_party_xxxx():
    """The 2026-09-16 guard was keyed on solo_status(), which hardwires False
    inside a dungeon/rift before reading the roster — so the guard was dead
    exactly where procs mint ghosts most (live 2026-09-18: solo against King
    Ratsar, Party_3258/9280/A0B8 minted out of the user's own proc hits).
    INSIDE an instance the roster read decides: empty roster = no minting.
    (Off-instance the empty-roster solo case is already covered by
    test_solo_unresolvable_source_credits_me_not_party_xxxx.)"""
    from farever_companion.core.group_reader import GroupSnapshot
    m = _Model()
    m._units = [_hero(PA), _foe(FOE)]
    m.is_dungeon = True                 # instance: solo_status() would say False
    m.roster = GroupSnapshot(group=0x6002, members=[])   # the game's solo roster
    tr = DpsTracker(m)
    tr.update()
    proc_src = 0x66F0                   # a proc/status object address
    m.damage.push(DamageEvent(amount=30.0, skill="Axe_Boomerang_Skill1_Status",
                              source_addr=proc_src, target_addr=FOE, t=time.time()))
    tr.update()
    s = tr.overall_session
    assert not any(n.startswith("Party_") for n in s.players)
    me = next((p for p in s.players.values() if p.is_me), None)
    assert me is not None and abs(me.total_damage - 30.0) < 1e-6


def test_proxy_roster_local_name_with_drifted_pointer_does_not_duplicate_me():
    """A drifted local roster pointer must not pre-register bare ``Name`` beside
    the proxy's correctly attributed ``Name (You)`` row."""
    from farever_companion.core.dps_bridge import parse_hook_event
    from farever_companion.core.group_reader import GroupMember, GroupSnapshot

    m = _Model(names={PA: "MyChar", TEAM: "Ally"})
    m._units = [_hero(PA), _hero(TEAM), _foe(FOE)]
    m.is_dungeon = True
    m.roster = GroupSnapshot(group=1, members=[
        # Same local character, but the decoded hero/player pointers drifted
        # and is_me was not recovered from those pointers.
        GroupMember(name="MyChar", hero=0x7777, player=0x8888, is_me=False),
        GroupMember(name="Ally", hero=TEAM),
    ])

    tr = DpsTracker(m)
    tr.update()
    event = parse_hook_event(
        '{"t":"hit","amt":30,"sk":"Axe_Base_Attack",'
        '"src":"MyChar","tgt":"KingRatsar"}')
    event.capture_mode = "proxy"
    m.damage.push(event)
    tr.update()

    assert set(tr.overall_session.players) == {"MyChar (You)", "Ally"}
    assert "MyChar" not in tr.overall_session.players
    me = tr.overall_session.players["MyChar (You)"]
    assert me.is_me and abs(me.total_damage - 30.0) < 1e-6


def test_instance_solo_proxy_caster_ignores_unresolved_hero_like_scene_objects():
    """Option 1 sends a missing caster name as generic ``Player``. In a solo
    instance, an unresolved Hero-like scene object is not a party member and
    must not make the proxy event mint ``Party_XXXX`` rows for the player's
    own damage."""
    from farever_companion.core.dps_bridge import parse_hook_event

    m = _Model()
    m._units = [_hero(PA), _hero(0x9999), _foe(FOE)]
    m.is_dungeon = True
    m.roster = None                     # no trustworthy roster decode
    logs = []
    tr = DpsTracker(m)
    tr.log_line = logs.append
    tr.update()

    # No src/me fields: the real proxy parser emits source_name="Player" with
    # no address. The fake scene Hero remains an unresolved Party_XXXX pointer.
    event = parse_hook_event(
        '{"t":"hit","amt":30,"sk":"Axe_Base_Attack","tgt":"KingRatsar"}')
    event.capture_mode = "proxy"
    m.damage.push(event)
    tr.update()

    s = tr.overall_session
    assert not any(n.startswith("Party_") for n in s.players)
    me = next((p for p in s.players.values() if p.is_me), None)
    assert me is not None and abs(me.total_damage - 30.0) < 1e-6
    assert not [line for line in logs if "attribution unresolved" in line]


def test_proxy_unresolved_caster_logs_once_per_target():
    from farever_companion.core.dps_bridge import parse_hook_event
    from farever_companion.core.group_reader import GroupMember, GroupSnapshot

    m = _Model()
    m._units = [_hero(PA), _hero(TEAM), _foe(FOE)]
    m.roster = GroupSnapshot(group=1, members=[
        GroupMember(name="Me", hero=PA, is_me=True),
        GroupMember(name="Ally", hero=TEAM),
    ])
    logs = []
    tr = DpsTracker(m)
    tr.log_line = logs.append
    tr.update()

    for amount in (10.0, 20.0):
        event = parse_hook_event(
            '{"t":"hit","amt":%s,"sk":"Axe_Base_Attack",'
            '"tgt":"KingRatsar"}' % amount)
        event.capture_mode = "proxy"
        m.damage.push(event)
    tr.update()

    warnings = [line for line in logs if "proxy attribution unresolved" in line]
    assert len(warnings) == 1, logs
    assert "Player on KingRatsar" in warnings[0]
    assert "event dropped" in warnings[0]


def test_solo_in_a_rift_credits_own_nameless_hit_despite_named_strangers_on_scan():
    """Solo in a rift, your own nameless hit must not be dropped just because
    the instance-wide scan carries OTHER players.

    Live 2026-10-05, a rift: "my damage stopped recording, everyone else is
    fine". The Activity Log is the proof — the roster DECODED as solo
    (``[group] 1 member: Bee (you)``) while the same session's bridge log named
    the other players in the rift (Ldivad / Karaoke / Smoking fern), and the
    drops fired against a nameless caster:

        Top DPS: injector bridge attribution unresolved — Player on Enemy
        (Regen); event dropped

    The asymmetry is the tell. An ally's hit arrives NAMED, so it resolves by
    name whatever else is broken; the local player's own procs arrive flagged
    or unnamed with no source object. `_resolve_caster`'s solo-credit guard
    refused that case whenever ``known_others`` was non-empty — and a rift's
    scan is INSTANCE-WIDE, so strangers on it are not a party. The backstop is
    right for an UNREADABLE roster (an off-scene ally must keep a fold-able
    Party_ row) and wrong as an override of a roster that decoded as solo.
    """
    from farever_companion.core.group_reader import GroupSnapshot, GroupMember

    m = _Model(names={PA: "Bee", STRANGER: "Ldivad"})
    m._units = [_hero(PA), _hero(STRANGER), _foe(FOE, uid="Trash_Pig")]
    m.is_dungeon = True
    m.roster = GroupSnapshot(group=0x5000, members=[
        GroupMember(player=0, hero=PA, name="Bee", is_me=True),
    ])
    logs = []
    tr = DpsTracker(m)
    tr.log_line = logs.append
    tr.update()
    assert "Ldivad" in [n for n, _me in tr._hero_pointers.values()], \
        "the fixture must put a named stranger on the instance-wide scan"

    # The nameless, un-addressed shape the bridge sends for a proc/status hit.
    m.damage.push(DamageEvent(amount=1000.0, skill="Halos_Totem_Passive_Aoe",
                              source_addr=0, target_addr=FOE,
                              source_name="Player", is_me=False,
                              capture_mode="injector", t=time.time()))
    tr.update()

    st = m.damage.stats.snapshot()
    assert st["dropped"] == 0 and st["attributed"] == 1, logs
    assert not any("event dropped" in ln for ln in logs), logs
    r = own_row(tr.trash_session)
    assert r is not None and r.total_damage >= 1000.0, (
        "the hit must land on the LOCAL row: "
        f"{[(n, p.total_damage) for n, p in tr.trash_session.players.items()]}")

    # The guard the backstop exists for is untouched: with an UNREADABLE roster
    # a named stranger on the scan still declines solo credit.
    m2 = _Model(names={PA: "Bee", STRANGER: "Ldivad"})
    m2._units = [_hero(PA), _hero(STRANGER), _foe(FOE, uid="Trash_Pig")]
    m2.is_dungeon = True
    m2.roster = None                     # undecodable: no solo claim
    tr2 = DpsTracker(m2)
    tr2.update()
    m2.damage.push(DamageEvent(amount=1000.0, skill="Halos_Totem_Passive_Aoe",
                               source_addr=0, target_addr=FOE,
                               source_name="Player", is_me=False,
                               capture_mode="injector", t=time.time()))
    tr2.update()
    assert m2.damage.stats.snapshot()["dropped"] == 1, \
        "an unreadable roster must keep the Party_ row fold-able"

    # ...and a real PARTY member is never folded onto the local row.
    m3 = _Model(names={PA: "Bee", STRANGER: "Ldivad"})
    m3._units = [_hero(PA), _hero(STRANGER), _foe(FOE, uid="Trash_Pig")]
    m3.is_dungeon = True
    m3.roster = GroupSnapshot(group=0x5000, members=[
        GroupMember(player=0, hero=PA, name="Bee", is_me=True),
        GroupMember(player=0, hero=STRANGER, name="Ldivad"),
    ])
    tr3 = DpsTracker(m3)
    tr3.update()
    m3.damage.push(DamageEvent(amount=1000.0, skill="Halos_Totem_Passive_Aoe",
                               source_addr=0, target_addr=FOE,
                               source_name="Ldivad", is_me=False,
                               capture_mode="injector", t=time.time()))
    tr3.update()
    r3 = own_row(tr3.trash_session)
    assert r3 is not None and r3.total_damage == 0.0, \
        "a named party member's hit must never be folded onto the local row"
    assert tr3.trash_session.players["Ldivad"].total_damage == 1000.0


def test_instance_unreadable_roster_with_seen_ally_keeps_party_row():
    """Inside an instance with an UNREADABLE roster, a real ally already seen
    (scene scan or a resolved name) must keep the fold-able Party_ row for
    their off-scene hits — the instance-wide scan is not proof of solitude."""
    m = _Model()
    m._units = [_hero(PA), _foe(FOE)]
    m.is_dungeon = True
    m.roster = None                     # undecodable
    tr = DpsTracker(m)
    tr._hero_pointers[TEAM] = ("Ally", False)   # a real ally resolved earlier
    tr.update()
    m.damage.push(DamageEvent(amount=9.0, skill="Axe_Boomerang_Skill1_Status",
                              source_addr=0x5555, target_addr=FOE, t=time.time()))
    tr.update()
    s = tr.overall_session
    assert any(n.startswith("Party_") for n in s.players), \
        "an ally seen this session keeps unresolved hits fold-able, even in an instance"


# --- capture health line ----------------------------------------------------
# A fight can start with the capture dead (stale cluster after a zone change, an
# unarmed DLL, a scanner still calibrating) and every surface then shows zeros
# with nothing saying why. One line at engagement time names the cause - the
# 2026-09-19 dungeon report ("nothing at all, during or after, nothing saved")
# was exactly this: silence with no evidence.

def test_capture_line_reports_why_nothing_is_arriving():
    m = _Model()
    tr = DpsTracker(m)
    m.damage._status = "silent"
    m.damage.diagnostics = lambda: {"displays": 23, "ok": 0, "no_res": 11}
    line = tr.capture_line()
    assert "mode=" in line and "status=silent" in line
    assert "displays=23" in line and "ok=0" in line
    assert "no event yet this run" in line


def test_capture_line_names_the_bridge_issue():
    m = _Model()
    tr = DpsTracker(m)
    m.damage.bridge_issue = lambda: "HLBOOT MISMATCH - refusing to arm"
    assert "HLBOOT" in tr.capture_line()


def test_deaf_capture_is_logged_at_engagement_but_not_when_events_flow():
    m = _Model()
    m.dungeon_boss = "Boss_Foo"
    tr = DpsTracker(m)
    logs = []
    tr.log_line = logs.append
    m._units = [_hero(PA), _foe(BOSS, uid="Boss_Foo", hp=900.0, is_boss=True)]
    tr.update()
    assert tr.in_boss_fight is True
    assert any("no damage being read" in x for x in logs)
    assert any("capture" in x for x in logs)

    tr._log_capture_if_deaf("Second call")
    tr._last_events_ts = time.time()      # a working capture needs no comment
    before = len(logs)
    tr._log_capture_if_deaf("Third call")
    assert len(logs) == before
    assert not any("Third call" in x for x in logs)


def test_zone_transition_forces_a_reader_rehunt_even_with_autoreset_off():
    """Zoning in/out must re-anchor the Option 3 reader immediately.

    The display pool moves with the zone; the reader's wide-sweep rate gate
    (120 s) can be armed by a startup derive right before the entry, which
    live 2026-09-19 blinded the first pull for ~1 min. The re-hunt is also
    deliberately independent of the Auto-Reset Zone toggle: re-anchoring the
    reader is not the meter reset.
    """
    m = _Model()
    tr = DpsTracker(m)
    tr.auto_reset_zone = False   # re-anchoring is NOT the meter reset
    m._units = [_hero(PA), _foe(FOE)]
    tr.update()

    m.is_dungeon = True
    tr.update()
    assert m.damage.rehunt_calls == 1          # enter
    m.is_dungeon = False
    tr.update()
    assert m.damage.rehunt_calls == 2          # exit

    # no double-fire while the zone state is unchanged
    tr.update()
    tr.update()
    assert m.damage.rehunt_calls == 2


# --- delayed-drain fidelity: capture timestamps rule the session clock ------
def test_backlog_drained_late_keeps_capture_clock():
    """A fight whose events drain in one gulp (UI timer stall, backgrounded
    window) must still archive with its capture-time span, not compress into
    a 0.0 s burst at drain time. Live 2026-09-19: a boss pull drained 124 hits
    in one 28 ms pass ~30 s after the fight and archived start==end."""
    m = _Model()
    tr = DpsTracker(m)
    m._units = [_hero(PA), _foe(FOE)]
    tr.update()
    t0 = time.time() - 30.0
    for i in range(5):
        m.damage.push(DamageEvent(amount=100.0, skill="Sword_Base_Attack",
                                  source_addr=PA, target_addr=FOE,
                                  t=t0 + i * 6.0))   # hits spread over ~24 s
    tr.update()   # all 5 drain NOW, in one pass (the gulp)

    sess = tr.overall_session
    assert sess.group_damage == 500.0
    assert 18.0 <= sess.duration <= 32.0            # capture span, not ~0
    assert len(sess.timeline) >= 4                  # buckets spread, not 1


def test_session_recorder_uses_capture_timestamp_when_given():
    from farever_companion.core.dps_data import CombatSession
    t0 = time.time() - 12.0
    s = CombatSession("T", "boss")
    s.record_hit("Foe", 1, 100.0, 100.0, 50.0, when=t0)
    s.record_hit("Foe", 1, 50.0, 100.0, 50.0, when=t0 + 12.0)
    assert s.start_time == t0
    assert 10.0 <= s.duration <= 14.0
    assert set(s.timeline) == {0, 12}

    s2 = CombatSession("T", "boss")
    s2.record_hit("Foe", 1, 100.0, 100.0, 10.0)     # no `when`: wall clock
    assert abs(s2.start_time - time.time()) < 5.0


def test_taken_and_heal_recorders_honor_capture_timestamp():
    from farever_companion.core.dps_data import CombatSession
    s = CombatSession("T", "boss")
    t0 = time.time() - 8.0
    s.record_damage_taken("You", 30.0, when=t0)
    s.record_heal("You", 15.0, when=t0 + 8.0)
    assert s.start_time == t0
    assert 6.0 <= s.duration <= 12.0


def test_a_dummy_test_drops_a_chip_damage_only_bystander():
    """A dummy does not fight back, so damage TAKEN alone is not a parse row.

    Live 2026-10-02: a dummy session listed four idle party members, each with
    only chips of damage taken and no hits of their own, on the DPS Analysis
    page and in the saved archive. The dummy keeps only players who earned a
    row; a rift/dungeon session keeps the taken-inclusive rule, because an
    instance pre-registers the party between pulls on purpose.
    """
    from farever_companion.core.dps_data import (CombatSession, PlayerParse,
                                                 _session_to_dict)

    dummy = CombatSession("Dummy", "dummy")
    dummy.players["Me (You)"] = PlayerParse(name="Me (You)", is_me=True,
                                            total_damage=500.0)
    dummy.players["Ladybug"] = PlayerParse(name="Ladybug", is_me=False,
                                           damage_taken=60.0)
    dummy.players["Esme"] = PlayerParse(name="Esme", is_me=False,
                                        damage_taken=214.0, heals=40.0)

    # the local player and anyone who dealt damage / healed is kept; the
    # chip-damage-only bystander is not (page, overlay and archive all read
    # this one rule).
    assert [p.name for p in dummy.ranked_players()] == ["Me (You)", "Esme"]
    assert [p["name"] for p in _session_to_dict(dummy)["players"]] == \
        ["Me (You)", "Esme"]
    assert dummy.is_active_player(dummy.players["Ladybug"]) is False

    # a rift/dungeon (any non-dummy) session keeps the taken-inclusive rule
    rift = CombatSession("Dungeon", "trash")
    rift.players["Ladybug"] = PlayerParse(name="Ladybug", is_me=False,
                                          damage_taken=60.0)
    assert [p.name for p in rift.ranked_players()] == ["Ladybug"]
    assert [p["name"] for p in _session_to_dict(rift)["players"]] == \
        ["Ladybug"]


# --- local death ends the fight: archive + fresh meter (2026-09-19) ---

def test_local_death_pauses_the_meter_without_losing_the_run():
    """Dying no longer ends the trash run.

    It used to: `on_local_death` called `reset()`, archiving the pull and
    wiping every session. A trash run is ONE run across the whole instance, so
    dying three packs in threw away the first two — losing the measurement
    rather than ending it (reported live 2026-10-03: "it should be 1 run for
    trash even if i die").

    So the run is paused, not reset: `record_hit` resumes a paused session, so
    the next hit picks the clock up where it stopped and the totals carry on.
    """
    m = _Model()
    tr = DpsTracker(m)
    lines: list[str] = []
    tr.log_line = lines.append
    m.is_dungeon = True
    m._units = [_hero(PA), _foe(FOE)]
    m.damage.push(DamageEvent(amount=300.0, skill="Sword_Base_Attack",
                              source_addr=PA, target_addr=FOE, t=time.time()))
    tr.update()
    assert tr.overall_session.group_damage == 300.0

    # Local hero dies: HP edge 1000 -> 0 on the *local* address.
    m._units = [_hero(PA, hp=0.0), _foe(FOE)]
    tr.update()

    assert tr.overall_session.state == "PAUSED"        # clock stopped...
    assert tr.overall_session.group_damage == 300.0   # ...numbers kept
    assert len(tr.history) == 0                       # nothing was archived
    assert tr.last_local_death_m > 0.0                # the death still counted
    assert any("died" in ln.lower() for ln in lines)

    # Respawn and keep pulling: the SAME run continues, it does not restart.
    m._units = [_hero(PA, hp=1000.0), _foe(FOE)]
    tr.update()
    m.damage.push(DamageEvent(amount=70.0, skill="Sword_Base_Attack",
                              source_addr=PA, target_addr=FOE, t=time.time()))
    tr.update()
    assert tr.overall_session.group_damage == 370.0, "the run carried on"


def test_local_death_does_not_rearm_timer_on_respawn_tick():
    """A stale boss scene/combat flag cannot restart the HUD clock on the
    death tick or immediately when the player respawns."""
    m = _Model()
    tr = DpsTracker(m)
    m._units = [_hero(PA), _foe(BOSS, uid="Boss_Foo", hp=100000.0,
                                  is_boss=True)]
    tr.update()
    m.damage.push(DamageEvent(amount=100.0, skill="Sword_Base_Attack",
                              source_addr=PA, target_addr=BOSS, t=time.time()))
    tr.update()

    m._units = [_hero(PA, hp=0.0), _foe(BOSS, uid="Boss_Foo", hp=100000.0,
                                       is_boss=True)]
    tr.update()
    assert tr.armed_fight() is None
    assert tr.in_boss_fight is False

    m._units = [_hero(PA, hp=1000.0), _foe(BOSS, uid="Boss_Foo", hp=100000.0,
                                         is_boss=True)]
    tr.update()
    assert tr.armed_fight() is None
    assert tr.in_boss_fight is False


def test_local_death_noop_when_nothing_was_fought():
    """Dying with an empty meter (wiped before landing a hit) must not archive
    an empty fight or spam a reset line."""
    m = _Model()
    tr = DpsTracker(m)
    lines: list[str] = []
    tr.log_line = lines.append
    m._units = [_hero(PA), _foe(FOE)]
    tr.update()                                  # seed HP, no damage
    m._units = [_hero(PA, hp=0.0), _foe(FOE)]
    tr.update()

    assert tr.overall_session.group_damage == 0.0
    assert tr.history == []                      # nothing archived
    assert not [ln for ln in lines if "died" in ln.lower()]


def test_remote_player_death_does_not_reset_the_meter():
    """Only the LOCAL player's death ends the meter: a teammate dying mid-pull
    must not archive a fight the rest of the party is still in."""
    m = _Model()
    tr = DpsTracker(m)
    mate = 0x2222
    m._units = [_hero(PA), _hero(mate, name="Mate"), _foe(FOE)]
    tr.update()                                  # seeds both HPs
    m.damage.push(DamageEvent(amount=120.0, skill="Sword_Base_Attack",
                              source_addr=PA, target_addr=FOE, t=time.time()))
    tr.update()
    assert tr.overall_session.group_damage == 120.0

    # Teammate dies; my HP untouched.
    m._units = [_hero(PA), _hero(mate, name="Mate", hp=0.0), _foe(FOE)]
    tr.update()

    assert tr.overall_session.group_damage == 120.0     # meter kept
    assert tr.history == []


def test_on_local_death_edge_triggered_no_repeat_resets():
    """The death watcher fires on the prev>0 -> hp<=0 edge only: corpse ticks at
    hp 0 never re-fire it, so the run is paused once and stays paused."""
    m = _Model()
    tr = DpsTracker(m)
    m.is_dungeon = True
    m._units = [_hero(PA), _foe(FOE)]
    m.damage.push(DamageEvent(amount=90.0, skill="Sword_Base_Attack",
                              source_addr=PA, target_addr=FOE, t=time.time()))
    tr.update()
    m._units = [_hero(PA, hp=0.0), _foe(FOE)]
    tr.update()                                  # death -> pause
    assert tr.overall_session.state == "PAUSED"
    marked = tr.last_local_death_m
    for _ in range(4):
        tr.update()                              # corpse ticks
    assert tr.overall_session.state == "PAUSED"  # still just the one pause
    assert tr.last_local_death_m == marked       # the marker did not move
    assert tr.overall_session.group_damage == 90.0


def test_local_death_stamps_wipe_marker_for_speedrun():
    """on_local_death stamps last_local_death_m even with nothing fought (a
    death with zero damage is still a wipe for the speedrun timer), and
    reset() never clears it (on_local_death itself calls reset)."""
    m = _Model()
    tr = DpsTracker(m)
    assert tr.last_local_death_m == 0.0
    tr.on_local_death("Me (You)")                # nothing fought: no archive...
    assert tr.last_local_death_m > 0.0           # ...but the marker lands anyway
    assert len(tr.history) == 0
    marked = tr.last_local_death_m
    tr.reset("test")                             # zone/manual reset keeps it
    assert tr.last_local_death_m == marked

    # full cycle with damage: archives AND keeps the marker past the reset
    m.is_dungeon = True             # instance fight: death archives it
    m._units = [_hero(PA), _foe(FOE)]
    m.damage.push(DamageEvent(amount=90.0, skill="Sword_Base_Attack",
                              source_addr=PA, target_addr=FOE, t=time.time()))
    tr.update()
    m._units = [_hero(PA, hp=0.0), _foe(FOE)]
    tr.update()                                  # death -> the run is paused
    assert len(tr.history) == 0                  # a trash death archives nothing
    assert tr.overall_session.group_damage == 90.0
    assert tr.last_local_death_m >= marked


def test_vanished_hero_mid_fight_counts_as_wipe():
    """Death can remove the hero entity instead of leaving a 0-HP corpse: the
    HP edge then never fires. A persisting absence mid-fight (stable
    instance) routes through on_local_death too — archive + wipe marker."""
    m = _Model()
    m.is_dungeon = True
    tr = DpsTracker(m)
    tr._vanish_grace_s = 0.0
    m._units = [_hero(PA), _foe(FOE)]
    tr.update()                                  # seeds the alive baseline
    m.damage.push(DamageEvent(amount=90.0, skill="Sword_Base_Attack",
                              source_addr=PA, target_addr=FOE, t=time.time()))
    tr.update()                                  # fight is live now
    m._units = [_foe(FOE)]                       # hero gone, same dungeon
    tr.update()                                  # grace clock starts, no fire yet
    assert tr.last_local_death_m == 0.0
    assert len(tr.history) == 0
    tr.update()                                  # still gone past grace -> wipe
    assert tr.last_local_death_m > 0.0
    assert tr.overall_session.state == "PAUSED"  # paused: the run continues
    assert tr.overall_session.group_damage == 90.0
    assert len(tr.history) == 0


def test_vanish_while_idle_never_wipes():
    """Nothing fought means nobody wiped: an idle vanish (loads, menu) must
    not archive or stamp the marker."""
    m = _Model()
    m.is_dungeon = True
    tr = DpsTracker(m)
    tr._vanish_grace_s = 0.0
    m._units = [_hero(PA), _foe(FOE)]
    tr.update()
    m._units = [_foe(FOE)]
    for _ in range(4):
        tr.update()
    assert tr.last_local_death_m == 0.0
    assert len(tr.history) == 0


def test_death_registers_when_locator_lost_the_player():
    """Death can clear player_addr (locator can't resolve a gone hero): the
    corpse at 0 HP must still match the last-known hero and wipe."""
    m = _Model()
    tr = DpsTracker(m)
    m.is_dungeon = True             # instance fight: death archives it
    m._units = [_hero(PA), _foe(FOE)]
    tr.update()                                  # seeds alive baseline
    m.damage.push(DamageEvent(amount=90.0, skill="Sword_Base_Attack",
                              source_addr=PA, target_addr=FOE, t=time.time()))
    tr.update()
    m.player_addr = None                         # locator lost the player
    m._units = [_hero(PA, hp=0.0), _foe(FOE)]    # ...but the corpse scans
    tr.update()
    assert tr.last_local_death_m > 0.0
    assert tr.overall_session.state == "PAUSED"  # the run pauses, not resets
    assert len(tr.history) == 0


def test_vanish_counts_as_wipe_when_locator_lost_the_player():
    """The full live wipe: entity gone AND pa cleared mid-fight."""
    m = _Model()
    m.is_dungeon = True
    tr = DpsTracker(m)
    tr._vanish_grace_s = 0.0
    m._units = [_hero(PA), _foe(FOE)]
    tr.update()
    m.damage.push(DamageEvent(amount=90.0, skill="Sword_Base_Attack",
                              source_addr=PA, target_addr=FOE, t=time.time()))
    tr.update()
    m.player_addr = None
    m._units = [_foe(FOE)]
    tr.update()                                  # grace clock starts
    assert tr.last_local_death_m == 0.0
    tr.update()                                  # still gone -> wipe
    assert tr.last_local_death_m > 0.0
    assert len(tr.history) == 0                  # a trash death archives nothing


def test_game_combat_flag_feeds_capture_hint():
    """The game's own combat flag reaches the capture engine even with no
    boss/dummy engaged: a trash pull with zero decoded events is still a
    fight, and without the flag the memory scanner sat on its 45 s LULL
    cadence while the first hits landed (live: skills ~30 s late)."""
    m = _Model()
    tr = DpsTracker(m)
    seen: list[bool] = []
    m.damage.note_combat = seen.append
    tr._combat_poll_at = 0.0
    m.combat_state_value = {"in_combat": True, "combat_id": 7,
                            "combat_start": 1.0, "combat_end": 0.0}
    m._units = [_hero(PA), _foe(FOE)]
    tr.update()
    assert seen and seen[-1] is True

    # flag off, nothing engaged -> hint off
    tr._combat_poll_at = 0.0
    m.combat_state_value = {"in_combat": False, "combat_id": 7,
                            "combat_start": 0.0, "combat_end": 2.0}
    tr.update()
    assert seen[-1] is False

    # carried-over combat across a zone load must not force combat cadence
    tr._combat_poll_at = 0.0
    m.combat_state_value = {"in_combat": True, "combat_id": 7,
                            "combat_start": 1.0, "combat_end": 0.0}
    tr._zone_edge_at = time.time()
    tr.update()
    assert seen[-1] is False


def test_vanish_across_zone_change_never_wipes():
    """A zone transition blanks the scene: vanish + instance edge together is
    a load, not a death."""
    m = _Model()
    m.is_dungeon = True
    tr = DpsTracker(m)
    tr._instance_edge_debounce_s = 0.0
    tr._vanish_grace_s = 0.0
    m._units = [_hero(PA), _foe(FOE)]
    tr.update()
    m.damage.push(DamageEvent(amount=90.0, skill="Sword_Base_Attack",
                              source_addr=PA, target_addr=FOE, t=time.time()))
    tr.update()
    m.is_dungeon = False                         # zoned out + hero gone
    m._units = []
    for _ in range(3):
        tr.update()
    assert tr.last_local_death_m == 0.0


# --- update() reentrancy guard: worker thread + overlay timer (2026-09-19) ---

def test_update_is_nonreentrant_second_call_skips_not_blocks():
    """A concurrent update() (the drain worker arriving while the overlay's
    main-thread tick owns the pass) must SKIP, never block: a blocking lock
    would drag the GUI thread behind a slow scene read."""
    m = _Model()
    tr = DpsTracker(m)
    m._units = [_hero(PA), _foe(FOE)]

    with tr._update_lock:                     # someone else owns the pass
        started = time.monotonic()
        tr.update()                           # must return immediately
        elapsed = time.monotonic() - started
    assert elapsed < 0.5, "update() blocked behind a held guard"


def test_update_guard_released_after_pass():
    m = _Model()
    tr = DpsTracker(m)
    m._units = [_hero(PA), _foe(FOE)]
    tr.update()
    assert tr._update_lock.acquire(blocking=False)
    tr._update_lock.release()

    # A raise inside the pass must still release the guard (finally).
    m._units = [object()]                     # garbage unit -> likely raise
    try:
        tr.update()
    except Exception:
        pass
    assert tr._update_lock.acquire(blocking=False)
    tr._update_lock.release()


def test_concurrent_updates_neither_corrupt_nor_double_drain():
    """Hammer update() from two threads (worker + timer simulation): every
    event is consumed exactly once, totals stay exact, no state races."""
    m = _Model()
    tr = DpsTracker(m)
    m._units = [_hero(PA), _foe(FOE)]
    tr.update()                               # seed HP

    n = 40
    for i in range(n):
        m.damage.push(DamageEvent(amount=10.0, skill="Sword_Base_Attack",
                                  source_addr=PA, target_addr=FOE, t=time.time()))
    import threading as _th
    errors: list[Exception] = []

    def run():
        try:
            for _ in range(25):
                tr.update()
        except Exception as e:                # pragma: no cover
            errors.append(e)

    threads = [_th.Thread(target=run) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert tr.overall_session.group_damage == n * 10.0


# --- boss-corpse suppression: no re-engage after the fight ends (2026-09-19) ---

def test_boss_corpse_does_not_reengage_and_pin_timer_at_zero():
    """After the boss dies, the despawning corpse still matches the
    engagement scan — the next tick re-armed boss_session (archive + reset),
    pinning the timer at 00:00.0 on an empty board. The ended boss must stay
    suppressed for the corpse window (live: 'timer showing 0 after fight
    ended' + 'not stopping when boss died')."""
    m = _Model()
    tr = DpsTracker(m)
    # Engage a REAL boss (uid matches the boss scan), land damage, then kill.
    m._units = [_hero(PA), _foe(BOSS, uid="Boss_Foo", hp=10000.0,
                                  is_boss=True)]
    tr.update()
    assert tr.in_boss_fight is True
    m.damage.push(DamageEvent(amount=500.0, skill="Sword_Base_Attack",
                              source_addr=PA, target_addr=BOSS, t=time.time()))
    tr.update()
    m._units = [_hero(PA), _foe(BOSS, uid="Boss_Foo", hp=0.0)]   # boss corpse
    tr.update()                                     # sees hp<=0 -> fight ends
    assert tr.in_boss_fight is False
    assert len(tr.history) == 1
    end_state = tr.boss_session.state               # PAUSED, not re-armed

    # Corpse still in the scene for the next few ticks (despawn animation).
    for _ in range(6):
        m._units = [_hero(PA), _foe(BOSS, uid="Boss_Foo", hp=0.0)]
        tr.update()
    assert tr.in_boss_fight is False                # never re-engaged
    assert tr.boss_session.state == end_state
    # The live board keeps the ended fight's numbers (the overlay shows the
    # final state); the NEXT engagement resets it, not the corpse.
    assert tr.boss_session.group_damage == 500.0
    assert tr.history[-1].group_damage == 500.0     # archive intact
    # The timer the overlay reads is the session duration — must stay at the
    # ended fight's span, not restart from 0 as a fresh COMBAT session.
    assert tr.session.duration >= 0.0
    assert tr.session.state != "COMBAT"


def test_new_boss_after_corpse_window_reengages():
    """Suppression is a WINDOW, not forever: a genuinely new boss (different
    addr) after the window re-engages normally."""
    m = _Model()
    tr = DpsTracker(m)
    m._units = [_hero(PA), _foe(BOSS, uid="Boss_Foo", hp=10000.0,
                                  is_boss=True)]
    tr.update()
    assert tr.in_boss_fight is True
    m.damage.push(DamageEvent(amount=500.0, skill="Sword_Base_Attack",
                              source_addr=PA, target_addr=BOSS, t=time.time()))
    m._units = [_hero(PA), _foe(BOSS, uid="Boss_Foo", hp=0.0)]
    tr.update()
    assert tr.in_boss_fight is False

    # Shrink the corpse window to zero and present a NEW boss.
    tr._ended_boss_deadline = 0.0
    new_boss = 0x5555
    m._units = [_hero(PA), _foe(new_boss, uid="Boss_Bar", hp=8000.0, is_boss=True)]
    tr.update()
    assert tr.in_boss_fight is True
    assert tr.boss_session.target_addr == new_boss


# --- fight clock: capture time rules, never the wall clock (2026-09-19) -----

def test_a_late_delivered_event_cannot_rewrite_the_fight_clock():
    """resume() must shift a paused fight's clock by the EVENT's capture time,
    never by the wall clock. A backlog processed late pushed start_time forward
    by the whole wall-clock gap on every pause/resume cycle until the duration
    collapsed. Live 2026-09-19: a 6,342-damage boss kill archived as
    start=...624.83 end=...624.84 — a 0.01 s fight, which the Top DPS header
    renders as a timer that starts and then shows 00:00.0."""
    from farever_companion.core.dps_data import CombatSession
    t0 = time.time() - 90.0
    s = CombatSession("Boss Fight", "boss")
    s.record_hit("Boss", 1, 100.0, 100.0, 60.0, when=t0)
    s.record_hit("Boss", 1, 40.0, 100.0, 60.0, when=t0 + 60.0)   # 60 s fought
    s.pause()
    assert s.state == "PAUSED"
    start_before = s.start_time
    assert 58.0 <= s.duration <= 62.0

    # An event captured 20 s BEFORE the pause (a late/re-ordered delivery):
    # it is recorded, but the clock must not move an inch.
    s.record_hit("Boss", 1, 20.0, 100.0, 5.0, when=t0 + 40.0)
    assert s.start_time == start_before, "a late delivery rewrote the fight clock"

    # A genuinely NEW hit after the pause still shifts the clock by the real
    # gap, so the paused interval is not counted as fight time.
    s2 = CombatSession("Boss Fight", "boss")
    s2.record_hit("Boss", 1, 100.0, 100.0, 60.0, when=t0)
    s2.record_hit("Boss", 1, 40.0, 100.0, 60.0, when=t0 + 60.0)
    s2.pause()
    s2.record_hit("Boss", 1, 40.0, 100.0, 10.0, when=t0 + 80.0)  # 20 s later
    assert s2.start_time == t0 + 20.0
    # 60 s of actual fighting: t0 -> t0+60, then a 20 s pause that start_time's
    # shift removes. The assertion used to be ">= 65.0", which silently counted
    # the wall-clock time since that last hit - real seconds in which no
    # damage landed. `duration` is the rate denominator and now measures the
    # same span the archive will (see CombatSession.duration), so 60 is the
    # honest answer and the meter no longer disagrees with DPS Analysis.
    assert s2.duration == 60.0


def test_the_same_encounter_is_archived_once_even_with_an_adds_segment():
    """A death archives through reset() and a boss kill through its own end.
    The boss and its adds fold into ONE "Boss Fight" archive — the kill
    ends the fight, so the adds segment is absorbed into it. Comparing
    only history[-1] once let a second copy of the boss sit behind the
    adds, so every death was saved twice under two different reasons
    (live 2026-09-19: the same 65.9 s pull as both "Died (Kohan (You))"
    and "Local player death")."""
    m = _Model()
    tr = DpsTracker(m)
    tr.boss_session.reset()
    tr.boss_session.name = "Boss Fight"
    tr.boss_session.record_hit("Boss", FOE, 50.0, 100.0, 500.0)
    tr.boss_adds_session.reset()
    tr.boss_adds_session.name = "Boss Adds"
    tr.boss_adds_session.record_hit("Trash", FOE, 10.0, 10.0, 100.0)

    tr.archive_current_encounter("Died (Kohan (You))")
    tr.archive_current_encounter("Local player death")

    names = [h.name for h in tr.history]
    assert len(names) == 1, names                    # one archive: the fight
    assert sum(1 for n in names if n.startswith("Boss Fight")) == 1
    assert not [n for n in names if n.startswith("Boss Adds")]


def test_a_death_archives_the_encounter_once_named_for_the_death():
    """A BOSS wipe is a real fight end, so it archives — exactly once, and
    named for the death.

    The trash run is not: it is one run across the instance and a death only
    pauses it (`test_local_death_pauses_the_meter_without_losing_the_run`). The
    boss pull keeps its own end signals, so the "one archive, one reason" rule
    this test exists for still has a home.
    """
    m = _Model()
    tr = DpsTracker(m)
    m.is_dungeon = True
    m._units = [_hero(PA), _foe(BOSS, uid="Boss_Foo", hp=100000.0,
                                is_boss=True)]
    m.damage.push(DamageEvent(amount=250.0, skill="Sword_Base_Attack",
                              source_addr=PA, target_addr=BOSS, t=time.time()))
    tr.update()
    assert tr.in_boss_fight is True
    m._units = [_hero(PA, hp=0.0), _foe(BOSS, uid="Boss_Foo", hp=100000.0,
                                       is_boss=True)]
    tr.update()

    names = [h.name for h in tr.history]
    assert len(names) == 1, names
    assert "Died" in names[0]
    assert "Local player death" not in names[0]      # the redundant second copy
    assert tr.in_boss_fight is False


# --- the meter starts counting when COMBAT starts (2026-09-19) --------------

def test_the_fight_clock_starts_at_combat_entry_not_at_the_first_decoded_hit():
    """As soon as you go in combat the timer should start — not when the damage
    finally decodes. Option 3 can be blind for the first seconds of a pull, and
    without the game anchor that lag showed up as a timer that started late."""
    from farever_companion.core.dps_data import CombatSession
    now = time.time()
    s = CombatSession("Boss Fight", "boss")
    s.clock_anchor = now - 6.0            # the game says the fight began 6 s ago
    s.record_hit("Boss", 1, 100.0, 100.0, 500.0, when=now)   # first hit NOW
    assert s.start_time == now - 6.0
    assert 5.0 <= s.duration <= 8.0       # the timer shows the fight, not 0

    # No anchor at all (game fields unreadable): the event time stands.
    s2 = CombatSession("Boss Fight", "boss")
    s2.record_hit("Boss", 1, 100.0, 100.0, 500.0, when=now)
    assert s2.start_time == now


def test_an_implausible_combat_anchor_loses_to_the_event_time():
    from farever_companion.core.dps_data import CombatSession
    now = time.time()
    for bad in (now + 5.0, now - 600.0):      # in the future / an earlier fight
        s = CombatSession("T", "boss")
        s.clock_anchor = bad
        s.record_hit("Boss", 1, 1.0, 1.0, 10.0, when=now)
        assert s.start_time == now, f"anchor {bad - now:+.0f}s was trusted"


def test_the_tracker_hands_the_game_combat_start_to_the_sessions():
    m = _Model()
    tr = DpsTracker(m)
    m.combat_state_value = {"in_combat": True, "combat_id": 3,
                            "combat_start": 42.0, "combat_end": 0.0}
    tr._combat_poll_at = 0.0
    tr._poll_combat_state(PA)
    anchor = tr._combat_wall_start_at
    assert anchor > 0.0
    for sess in (tr.overall_session, tr.trash_session, tr.boss_session,
                 tr.boss_adds_session, tr.dummy_session):
        assert sess.clock_anchor == anchor

    # Combat is over: every anchor clears, so a stale one can't seed the next.
    m.combat_state_value = {"in_combat": False, "combat_id": 3,
                            "combat_start": 0.0, "combat_end": 60.0}
    tr._combat_poll_at = 0.0
    tr._poll_combat_state(PA)
    for sess in (tr.overall_session, tr.trash_session, tr.boss_session,
                 tr.boss_adds_session, tr.dummy_session):
        assert sess.clock_anchor == 0.0


def test_engaging_a_boss_keeps_the_game_anchor_so_the_first_hit_isnt_late():
    """The combat poll runs BEFORE engagement inside one tick, and engagement
    resets the boss session. If reset() dropped the anchor, the first hit of
    that very same tick would start the clock late again."""
    m = _Model()
    tr = DpsTracker(m)
    m.combat_state_value = {"in_combat": True, "combat_id": 4,
                            "combat_start": 10.0, "combat_end": 0.0}
    tr._combat_poll_at = 0.0
    tr._poll_combat_state(PA)
    anchor = tr._combat_wall_start_at
    assert anchor > 0.0

    tr.boss_session.reset()                   # exactly what engagement does
    assert tr.boss_session.clock_anchor == anchor


def test_walking_up_to_the_boss_closes_and_archives_the_trash_run():
    """One trash run across the instance, ended by walking into the boss.

    The run is deliberately long-lived now (`_auto_pause_lull`), which is right
    for "how did this whole pull go" and wrong the moment the boss is in front
    of you: pre-boss trash and the boss pull are two different measurements and
    averaging them says neither. So the approach closes the run — ARCHIVED, so
    it is still there to compare — rather than leaving it to bleed into the
    boss fight.
    """
    m = _Model()
    m.is_dungeon = True
    tr = DpsTracker(m)
    tr.boss_clear_range = 30.0
    tr.dummy_target_s = 0.0
    m._units = [_hero(PA), _foe(FOE)]
    m.damage.push(DamageEvent(amount=400.0, skill="Sword_Base_Attack",
                              source_addr=PA, target_addr=FOE, t=time.time()))
    tr.update()
    assert tr.trash_session.group_damage == 400.0

    # The boss is loaded across the instance: far away, so the run keeps going.
    # A boss the DATA layer knows (a real unit id), so this is a real
    # engagement and the run must still close - the uncertain-claim carve-out
    # below is only for units nothing in the sheets has ever heard of.
    boss = _foe(BOSS, uid="DemonSuperElite", hp=100000.0, is_boss=True)
    boss.x, boss.y, boss.z = 900.0, 900.0, 900.0
    m._units = [_hero(PA), _foe(FOE), boss]
    tr.update()
    assert tr.trash_session.group_damage == 400.0, "far away: run continues"
    assert len(tr.history) == 0

    # Walk into its area: the run closes, archived, and the board is clear.
    boss.x, boss.y, boss.z = 6.0, 5.0, 5.0          # ~1 m from the player
    tr.update()
    assert tr.trash_session.group_damage == 0.0, "board cleared for the boss"
    assert tr.overall_session.group_damage == 0.0
    assert len(tr.history) == 1, "archived, not discarded"
    assert "Maat" in tr.history[-1].name
    assert abs(tr.history[-1].group_damage - 400.0) < 1e-6

    # It cannot re-fire on the next tick: there is no run left to close.
    tr.update()
    assert len(tr.history) == 1

    # 0 turns the rule off entirely. Checked on a fresh tracker so the boss is
    # not already engaged — with a boss fight live, trash damage is routed to
    # the adds segment and would prove nothing about this rule.
    m2 = _Model()
    m2.is_dungeon = True
    tr2 = DpsTracker(m2)
    tr2.boss_clear_range = 0.0
    near = _foe(BOSS, uid="DemonSuperElite", hp=100000.0, is_boss=True)
    near.x, near.y, near.z = 6.0, 5.0, 5.0
    m2._units = [_hero(PA), _foe(FOE)]
    m2.damage.push(DamageEvent(amount=90.0, skill="Sword_Base_Attack",
                               source_addr=PA, target_addr=FOE, t=time.time()))
    tr2.update()
    assert tr2.trash_session.group_damage == 90.0
    m2._units = [_hero(PA), _foe(FOE), near]        # boss arrives, right next to us
    tr2.update()
    tr2.update()
    # Range 0 is the OLD rule: the appearance archives a snapshot, but the run
    # itself is never cleared by proximity.
    assert tr2.trash_session.group_damage == 90.0, "range 0 = never close"
    assert len(tr2.history) == 1


def test_an_engine_only_boss_claim_does_not_split_the_fight(monkeypatch):
    """A hard/heroic champion mob must not archive the run once per pack.

    Live 2026-10-03, heroic dungeon: the meter reset after every trash mob, and
    the 👑 target line showed a trash mob. The instance names no boss (Kobolds
    Mines is not in the shipped data), the unit id is unknown to the data layer,
    and the ENGINE says it is a boss - so the claim is recorded as uncertain and
    the trash run is left open. A real boss - known to the data, or the one the
    instance declares - still splits the fight exactly as before
    (test_walking_up_to_the_boss_closes_and_archives_the_trash_run).
    """
    _declare_boss(monkeypatch, None)     # Kobolds Mines: no declaration
    from farever_companion.core.scene import Entity

    CHAMPION = "Kobold_HeroicChampion"   # unknown id, boss class + flag
    m = _Model()
    m.is_dungeon = True
    tr = DpsTracker(m)
    tr._instance_edge_debounce_s = 0.0
    tr.boss_clear_range = 30.0
    champ = Entity(addr=FOE, cls="ent.boss.KoboldChampion", unit_id=CHAMPION,
                   x=6.0, y=5.0, z=5.0, hp=600.0, is_foe=True)
    champ.is_boss = True      # the calibrated flag bit, as the game sets it
    m._units = [_hero(PA), champ]
    m.damage.push(DamageEvent(amount=400.0, skill="Sword_Base_Attack",
                              source_addr=PA, target_addr=FOE, t=time.time()))
    tr.update()
    # The board still records what you hit...
    assert tr.in_boss_fight is True
    # ...but the target line does NOT crown it: the 👑 on a trash mob was the
    # visible symptom (live 2026-10-03, "top dps did show the boss while i was
    # killing trash"), and it is the one thing an engine-only claim does not get.
    name = tr._foe_names.get(FOE, "")
    assert "👑" not in name, name

    # ...and the trash run was NOT archived: no split, no reset, no extra row.
    assert [s for s in tr.history if s.kind == "trash"] == [],         "an engine-only boss claim must not archive the trash run"

    # A second pack behaves the same way, rather than producing a second row.
    m.damage.push(DamageEvent(amount=350.0, skill="Sword_Base_Attack",
                              source_addr=PA, target_addr=FOE, t=time.time()))
    tr.update()
    assert [s for s in tr.history if s.kind == "trash"] == []


def test_a_free_test_records_nothing_and_never_becomes_the_best():
    """A practice run is not a result.

    Two things it must not do, and the second is the one that matters:

      * leave a record - not Fight History, and not the day sheet, which is
        appended from inside the archive so one gate covers both;
      * become the personal best or the reference the NEXT run is measured
        against. A free test that quietly promoted itself would be worse than
        one that recorded itself: it would move the goalposts for every later
        comparison while looking like nothing happened.
    """
    def run(tr, amount):
        m = tr.model
        # Stop Test latches the engagement off by design, so a second run in
        # one test needs the player's own Start Test - not just another swing.
        tr.start_dummy_test()
        m._units = [_hero(PA), training_dummy(FOE, uid="Dummy", hp=100000.0)]
        m.damage.push(DamageEvent(amount=amount, skill="Sword_Base_Attack",
                                  source_addr=PA, target_addr=FOE, t=time.time()))
        tr.update()
        # A run long enough to BE a reference: `dummy_baseline_snapshot`
        # refuses anything under BASELINE_MIN_S, so an instant test would
        # prove nothing about whether the best was touched.
        tr.dummy_session.start_time = time.time() - 30.0
        tr.stop_dummy_test("Test Stopped")
        tr.update()

    m = _Model()
    tr = DpsTracker(m)
    tr.dummy_target_s = 0.0

    # A FREE run, first and big, so anything it wrongly recorded would show.
    tr.dummy_free = True
    run(tr, 5000.0)
    assert tr.history == [], "no Fight History entry"
    assert not tr.dummy_best, "no personal best"
    assert tr.dummy_baseline_pending is None, "no reference for the next run"
    assert tr.take_dummy_best_pending() is None

    # A recorded one, on the same tracker: it must behave normally.
    tr.dummy_free = False
    run(tr, 1000.0)
    assert len(tr.history) == 1
    assert tr.history[-1].group_damage == 1000.0
    assert tr.dummy_best, "a recorded run still sets the best"

    # A free run afterwards must not disturb the record it just set.
    best_after = dict(tr.dummy_best)
    tr.dummy_free = True
    run(tr, 9000.0)
    assert len(tr.history) == 1, "still just the one recorded run"
    assert dict(tr.dummy_best) == best_after, "the best is untouched"


def test_a_skills_own_derived_row_folds_into_it_rather_than_a_second_row():
    """Bonethrow's bleed is Bonethrow.

    `Axe_Boomerang_Skill1` (the throw) and `Axe_Boomerang_Skill1_Status` (the
    bleed it applies) are BOTH named "Bonethrow" in the sheet, so DPS Analysis
    showed two identical rows and the passive damage read as a second skill
    (reported live 2026-10-03). The bleed IS the skill's damage, so it belongs
    in that row - and the row's total must still be the whole truth.
    """
    from farever_companion.data import names
    from farever_companion.core.dps_data import CombatSession

    THROW = "Axe_Boomerang_Skill1"
    BLEED = f"{THROW}_Status"
    # Real ids, real names: this only means anything against the shipped sheet.
    assert names.skill_name(THROW) == "Bonethrow"
    assert names.skill_name(BLEED) == "Bonethrow"
    assert names.parent_skill_id(BLEED) == THROW

    s = CombatSession("Test", "trash")
    s.record_hit("Trash Pig", FOE, 100.0, 100.0, 300.0, skill_id=THROW,
                 skill_name="Bonethrow", when=time.time())
    s.record_hit("Trash Pig", FOE, 100.0, 100.0, 70.0, skill_id=BLEED,
                 skill_name="Bonethrow", when=time.time() + 1)

    p = s.players["You"]
    assert list(p.skills) == [THROW], "one skill, one row"
    assert abs(p.total_damage - 370.0) < 1e-6, "nothing lost or double-counted"
    sp = p.skills[THROW]
    assert abs(sp.damage - 370.0) < 1e-6
    # ...and the fold is inspectable rather than silent: the child keeps its
    # OWN parse, so the nested row has hits and crits, not just a total.
    assert list(sp.subskills) == [BLEED]
    assert abs(sp.subskills[BLEED].damage - 70.0) < 1e-6
    assert sp.subskills[BLEED].hit_count == 1

    # A status from a skill the player never cast keeps its own row: the fold
    # is gated on the parent already being in the parse, so nothing is
    # attributed to a skill that merely shares its prefix.
    s2 = CombatSession("Test", "trash")
    s2.record_hit("Trash Pig", FOE, 100.0, 100.0, 70.0, skill_id=BLEED,
                  skill_name="Bonethrow", when=time.time())
    assert list(s2.players["You"].skills) == [BLEED]


def test_the_games_own_boss_area_trigger_closes_the_trash_run():
    """`ent.BossArea` is the room's real boundary, and beats the radius.

    Every boss room carries the trigger volume the encounter itself uses, so
    it knows where the arena starts even for a boss the data layer has never
    heard of. It is also not a fighter: it has no HP and must never be counted
    as a target, which is why the scan records it and skips it rather than
    letting it fall through the foe filters.
    """
    from farever_companion.core.scene import Entity
    m = _Model()
    m.is_dungeon = True
    tr = DpsTracker(m)
    tr.boss_clear_range = 0.0          # radius OFF: only the trigger can fire
    tr.dummy_target_s = 0.0
    m._units = [_hero(PA), _foe(FOE)]
    m.damage.push(DamageEvent(amount=250.0, skill="Sword_Base_Attack",
                              source_addr=PA, target_addr=FOE, t=time.time()))
    tr.update()
    assert tr.trash_session.group_damage == 250.0

    # The trigger is loaded with the zone, far away: nothing yet.
    area = Entity(addr=0xB0A, cls="ent.BossArea", unit_id="ent.BossArea",
                  x=800.0, y=800.0, z=800.0, hp=0.0)
    m._units = [_hero(PA), _foe(FOE), area]
    tr.update()
    assert tr.trash_session.group_damage == 250.0, "trigger seen, not entered"
    assert tr._in_boss_area is False
    # and it never became a target
    assert all(t.name != "ent.BossArea" for t in tr.trash_session.targets.values())

    # Walk into the room.
    area.x, area.y, area.z = 10.0, 0.0, 0.0
    tr.update()
    assert tr._in_boss_area is True
    assert tr.trash_session.group_damage == 0.0, "closed by the trigger alone"
    assert len(tr.history) == 1


def test_the_game_combat_flag_splits_packs_but_not_the_run():
    """The game's isInCombat is the authoritative "out of combat" in EVERY
    zone — but it no longer ends the meter.

    It used to be open-world-solo only, then it became a fight-ender. Both were
    wrong for the same reason: pulling trash one add at a time means being out
    of combat between every one, so each add started a new run and the pull
    never added up (reported live 2026-10-03: "top dps is resetting on
    different adds / out of combat it should be 1 run for trash even if i
    die").

    The flag still earns its keep as the PACK boundary: that is a display
    concern, and cutting a measurement at a display boundary is exactly what
    was wrong.
    """
    m = _Model()
    m.is_dungeon = True
    m._units = [_hero(PA), _foe(FOE)]
    tr = DpsTracker(m)

    m.combat_state_value = {"in_combat": True, "combat_id": 9,
                            "combat_start": 5.0, "combat_end": 0.0}
    tr._combat_poll_at = 0.0
    m.damage.push(DamageEvent(amount=100.0, skill="Sword_Base_Attack",
                              source_addr=PA, target_addr=FOE, t=time.time()))
    tr.update()
    assert tr.trash_session.state == "COMBAT"
    assert tr.overall_session.state == "COMBAT"
    assert tr.trash_session.group_damage == 100.0

    # The game declares the fight over: the meter stops NOW, no 6 s wait.
    m.combat_state_value = {"in_combat": False, "combat_id": 9,
                            "combat_start": 0.0, "combat_end": 40.0}
    tr._combat_poll_at = 0.0
    tr.update()
    # The game declaring the fight over no longer ENDS the run: it is out of
    # combat between adds for a moment at a time, and a trash run is one run
    # across the instance. What the flag still does is close the PACK, so the
    # breakdown knows where this pull ended.
    assert tr.trash_session.state == "COMBAT"
    assert tr.overall_session.state == "COMBAT"
    assert tr.trash_session.group_damage == 100.0     # totals kept, not wiped
    assert len(tr.trash_session.packs) == 1, "this pull is one closed pack"
    # ...and the boundary is real: the next pull opens a pack of its own.
    m.combat_state_value = {"in_combat": True, "combat_id": 10,
                            "combat_start": 0.0, "combat_end": 0.0}
    tr._combat_poll_at = 0.0
    tr.update()
    m.damage.push(DamageEvent(amount=40.0, skill="Sword_Base_Attack",
                              source_addr=PA, target_addr=FOE, t=time.time()))
    tr.update()
    assert len(tr.trash_session.packs) == 2, "a new pack, inside the same run"
    assert tr.trash_session.group_damage == 140.0, "one run across both packs"


def test_armed_fight_ticks_from_combat_entry_before_the_first_hit():
    """The meter must read as running for the WHOLE fight: while the game says
    combat is live and nothing has decoded yet, the armed clock owns the
    display, and it steps aside the moment a session does."""
    m = _Model()
    tr = DpsTracker(m)
    m.combat_state_value = {"in_combat": True, "combat_id": 11,
                            "combat_start": 3.0, "combat_end": 0.0}
    tr._combat_poll_at = 0.0
    tr._poll_combat_state(PA)
    tr._combat_wall_start_at = time.time() - 4.5      # fight began 4.5 s ago

    armed = tr.armed_fight()
    assert armed is not None
    elapsed, name = armed
    assert 4.0 <= elapsed <= 6.0
    assert name == tr.session.name

    # A hit lands: the session owns the clock now, from the SAME anchor.
    sess = tr.session
    sess.clock_anchor = tr._combat_wall_start_at
    sess.record_hit("Foe", FOE, 10.0, 10.0, 100.0, when=time.time())
    assert tr.armed_fight() is None
    assert tr.session.state == "COMBAT"
    assert 4.0 <= tr.session.duration <= 6.0          # no jump backwards

    # Out of combat: there is no armed clock at all.
    m.combat_state_value = {"in_combat": False, "combat_id": 11,
                            "combat_start": 0.0, "combat_end": 30.0}
    tr._combat_poll_at = 0.0
    tr._poll_combat_state(PA)
    tr.reset()
    assert tr.armed_fight() is None


def test_armed_fight_needs_the_game_anchor_and_a_live_combat_flag():
    m = _Model()
    tr = DpsTracker(m)
    assert tr.armed_fight() is None                   # nothing known yet

    tr._combat_wall_start_at = time.time() - 3.0
    assert tr.armed_fight() is None                   # anchor but no flag read

    tr.combat_state = {"in_combat": False, "combat_start": 0.0, "combat_end": 9.0}
    assert tr.armed_fight() is None                   # flag says out of combat


def test_zoning_in_does_not_start_the_fight_clock():
    """A zone transition is not a fight. The game keeps reporting isInCombat
    across a loading screen (and can regenerate combatStartTime on arrival), so
    neither the timer nor the session clock may trust the combat fields until
    the new zone settles — live 2026-09-19: the timer started the instant the
    user zoned in."""
    m = _Model()
    m._units = [_hero(PA), _foe(FOE)]
    tr = DpsTracker(m)
    m.combat_state_value = {"in_combat": True, "combat_id": 12,
                            "combat_start": 7.0, "combat_end": 0.0}
    tr._combat_poll_at = 0.0
    tr.update()
    tr._combat_wall_start_at = time.time() - 2.0
    assert tr.armed_fight() is not None          # a real fight is ticking

    # Zone in: the edge stamps the settle window AND drops the cached read.
    m.is_dungeon = True
    tr._combat_poll_at = 0.0
    tr.update()
    assert tr._zone_edge_at > 0.0
    assert tr.armed_fight() is None, "the timer started on zone-in"
    for sess in (tr.overall_session, tr.trash_session, tr.boss_session,
                 tr.boss_adds_session, tr.dummy_session):
        assert sess.clock_anchor == 0.0, \
            "a carried-over combat window backdated the fight clock"

    # Once the new zone settles, a live fight clock ticks again.
    tr._zone_edge_at = time.time() - (ZONE_ARM_SETTLE_S + 0.5)
    assert tr.armed_fight() is not None


# --- placeholder rows are never party members (live 2026-09-19) -----------#
# Zoning in showed the meter listing placeholder "players" (Party_XXXX). Two
# mints fed that list: the damage-taken path recorded a placeholder name for
# any hero it failed to identify, and the owned-proc re-credit claimed sources
# it had already identified as players (an ally holding the same item).

def test_owned_proc_in_instance_credits_me_not_a_placeholder_row():
    """An owned-item proc inside a dungeon lands on me, not on Party_XXXX.

    A pass-through pin with the odds stacked against it: a resolved party
    member exists, so `_no_other_player` is False and the source is free to
    mint, and the unowned/unresolved case in the same zone keeps its
    placeholder (see `test_unresolved_non_proc_source_in_instance_keeps_
    foldable_row`). Only the owned-proc skill may claim it.
    """
    from farever_companion.core.group_reader import GroupSnapshot, GroupMember

    m = _Model()
    m.is_dungeon = True
    m.roster = GroupSnapshot(group=0x5000, members=[
        GroupMember(name="Milo", hero=0x9999, player=0x8888),
    ])
    m._units = [_hero(PA), _foe(FOE)]
    tr = DpsTracker(m)
    tr.update()
    assert "Milo" in tr.overall_session.players   # a real player IS known

    proc_src = 0x9ABC   # the item's proc entity (never a scene hero)
    m.damage.push(DamageEvent(amount=50.0,
                              skill="Finger of Sukuna'Maatata",
                              source_addr=proc_src, target_addr=FOE,
                              t=time.time()))
    tr.update()

    s = tr.overall_session
    assert abs(s.players["Me (You)"].total_damage - 50.0) < 1e-6
    assert abs(s.group_damage - 50.0) < 1e-6
    assert not any(k.startswith("Party_") for k in s.players)


def test_owned_proc_from_a_resolved_ally_is_not_stolen():
    """The re-credit must not claim a source we already identified as a player.

    A known hero's proc ticks stay on their row even though the skill is one
    of the local player's owned-item procs (an ally can hold the same item).
    """
    m = _Model(names={PA: "Me", TEAM: "Milo"})
    m._units = [_hero(PA), _hero(TEAM), _foe(FOE)]
    tr = DpsTracker(m)
    tr.update()

    m.damage.push(DamageEvent(amount=30.0, skill="Finger of Sukuna'Maatata",
                              source_addr=TEAM, target_addr=FOE,
                              t=time.time()))
    tr.update()

    s = tr.overall_session
    assert abs(s.players["Milo"].total_damage - 30.0) < 1e-6
    assert s.players["Me (You)"].total_damage == 0.0


def test_unresolved_non_proc_source_in_instance_keeps_foldable_row():
    """Scope: only OWNED-proc skills are re-credited. An unexplained source
    with an ordinary skill still keeps its fold-able Party_XXXX row."""
    from farever_companion.core.group_reader import GroupSnapshot, GroupMember

    m = _Model()
    m.is_dungeon = True
    m.roster = GroupSnapshot(group=0x5000, members=[
        GroupMember(name="Milo", hero=0x9999, player=0x8888),
    ])
    m._units = [_hero(PA), _foe(FOE)]
    tr = DpsTracker(m)
    tr.update()

    m.damage.push(DamageEvent(amount=12.0, skill="Mob_Bite",
                              source_addr=0x7777, target_addr=FOE,
                              t=time.time()))
    tr.update()

    s = tr.overall_session
    party = [k for k in s.players if k.startswith("Party_")]
    assert party and abs(s.players[party[0]].total_damage - 12.0) < 1e-6


def test_placeholder_name_never_mints_damage_taken_row():
    """An unidentified hero's taken damage must not invent a party member.

    `Party_XXXX` means "we failed to resolve this address", not a name —
    recording it as a player row is how zone-in listed leftover placeholder
    rows carrying damage taken.
    """
    m = _Model()
    tr = DpsTracker(m)
    m._units = [_hero(PA), _foe(FOE)]
    tr.update()

    tr._hero_name_cache[0xBEEF] = "Party_BEEF"
    tr._hero_pointers[0xBEEF] = ("Party_BEEF", False)
    tr._apply_damage_taken(0xBEEF, 120.0, PA, {}, time.time())

    s = tr.overall_session
    assert not any(k.startswith("Party_") for k in s.players)
    assert s.group_damage == 0.0
    assert s.players.get("Party_BEEF") is None


def test_placeholder_taken_damage_still_resolves_when_it_is_me():
    """The placeholder refusal is not a blanket drop: if the target address is
    the local player, the row resolves to their real name as before."""
    m = _Model()
    tr = DpsTracker(m)
    m._units = [_hero(PA), _foe(FOE)]
    tr.update()

    tr._hero_name_cache[PA] = "Party_1111"    # stale placeholder for ME
    tr._apply_damage_taken(PA, 40.0, PA, {}, time.time())

    s = tr.overall_session
    assert abs(s.players["Me (You)"].total_damage_taken - 40.0) < 1e-6


# --- capture failures: per-event isolation + one log line per window ---

def test_a_poisoned_event_does_not_drop_the_rest_of_the_batch():
    """One event that cannot be applied must not take the batch with it.

    The drain moves the cursor BEFORE applying what it drained (the ring is a
    live stream - a cursor left behind a bad event would replay it forever), so
    a raise inside the apply loop used to abandon every event behind it,
    permanently and with no trace anywhere. The visible symptom is a meter that
    quietly under-counts, which is why the batch is applied one event at a
    time.
    """
    m = _Model()
    tr = DpsTracker(m)
    m._units = [_hero(PA), _foe(FOE)]
    tr.update()

    lines: list[str] = []
    tr.log_line = lines.append

    def _hit(amount: float) -> DamageEvent:
        return DamageEvent(amount=amount, skill="Sword_Base_Attack",
                           source_addr=PA, target_addr=FOE, t=time.time())

    poisoned = DamageEvent(amount=5.0, skill="Sword_Base_Attack",
                           source_addr=PA, target_addr=FOE, t=time.time())
    m.damage.push(_hit(100.0))
    m.damage.push(poisoned)
    m.damage.push(_hit(200.0))

    real_apply = tr._apply_event

    def apply(ev, pa, heroes, foes, now):
        if ev is poisoned:
            raise ValueError("poisoned event")
        return real_apply(ev, pa, heroes, foes, now)

    tr._apply_event = apply
    tr.update()

    # Only the poisoned event is lost: the hit AFTER it still counts.
    assert tr.overall_session.group_damage == 300.0
    assert any("apply_event" in ln and "poisoned event" in ln for ln in lines), (
        f"the dropped event must be reported; got {lines}")

    # ...and the cursor moved past it, so it is never replayed.
    tr.update()
    assert tr.overall_session.group_damage == 300.0


def test_capture_failures_are_logged_once_per_window_each():
    """A capture operation that starts failing speaks up, once per window.

    ``events_after`` refusing is the difference between "the meter is empty
    because nothing has been hit yet" and "the meter is empty because capture
    broke", and the old drain swallowed it whole. The report has to be
    throttled, though: the drain runs every tick (~10 Hz, from two threads), so
    a reader that is simply gone would otherwise write a line per tick forever
    and bury the log it exists to be findable in. Throttling is per operation,
    so a second broken one still gets to speak.
    """
    m = _Model()
    tr = DpsTracker(m)
    lines: list[str] = []
    tr.log_line = lines.append

    attempts = {"n": 0}

    def gone(cursor):
        attempts["n"] += 1
        raise RuntimeError("reader gone")

    m.damage.events_after = gone
    tr.update()
    tr.update()
    tr.update()

    assert attempts["n"] == 3, "the drain must still try on every tick"
    # The tracker's sink carries other Activity-Log lines too (the roster
    # change line lands in here), so count the capture reports only.
    fails = [ln for ln in lines if "capture" in ln]
    assert len(fails) == 1, f"one line per window, not one per tick: {fails}"
    assert "events_after" in fails[0] and "reader gone" in fails[0]

    def refused(in_fight):
        raise RuntimeError("hint refused")

    m.damage.note_combat = refused
    tr.update()

    fails = [ln for ln in lines if "capture" in ln]
    assert len(fails) == 2, (
        f"a second failing operation reports on its own timer: {fails}")
    assert "note_combat" in fails[1] and "hint refused" in fails[1]


def test_a_hard_mode_champion_mob_never_becomes_the_boss(monkeypatch):
    """An unknown mob the ENGINE calls a boss is still not the boss.

    Live 2026-10-03, hard mode only: "top dps is pausing then resetting after
    every trash pack", and three archived rows for ONE 5:30 fight. The cause is
    the engine-fallback in `_scan_foes`: a hard-mode champion mob is a unit id
    the shipped sheets predate (the CDB holds 530 units and none match
    champion/elite/hard) and it arrives with the runtime boss flag and a boss
    class, so the fallback promoted ordinary TRASH to "the boss". Each promotion
    ran `_engage_boss`, which archives the trash run as "Trash Mobs Cleared" -
    hence one row per pack. Normal mode never showed it because those mobs ARE
    in the CDB, so the fallback never got a vote.

    The fix: when the instance DECLARES its boss (`dungeons.declared_boss_id`),
    that declaration is the authority and an unknown unit's runtime claim
    cannot overrule it. Rifts declare nothing, so they are untouched - see
    test_a_rift_boss_is_not_held_by_the_declared_boss_gate.
    """
    from farever_companion.core.scene import Entity
    from farever_companion.data import units as udata

    CHAMPION = "Kobold_Champion"          # hard-mode only: not in the CDB
    assert not udata.is_known_unit(CHAMPION)
    assert not udata.is_boss(CHAMPION)

    _declare_boss(monkeypatch, "Nepsilon")      # the instance's real boss
    m = _Model()
    m.is_dungeon = True
    tr = DpsTracker(m)
    tr._instance_edge_debounce_s = 0.0
    champ = Entity(addr=FOE, cls="ent.boss.KoboldChampion", unit_id=CHAMPION,
                   x=5.0, y=5.0, z=5.0, hp=600.0, is_foe=True)
    m._units = [_hero(PA), champ]

    tr.update()
    assert tr.in_boss_fight is False, "a hard-mode trash mob engaged as the boss"

    # Two packs, both trash: one run, and NO archive per pack.
    for i, amount in enumerate((400.0, 350.0), start=1):
        m.damage.push(DamageEvent(amount=amount, skill="Sword_Base_Attack",
                                  source_addr=PA, target_addr=FOE,
                                  t=time.time()))
        tr.update()
        assert tr.boss_session.group_damage == 0.0, f"pack {i} hit the boss board"
    assert abs(tr.trash_session.group_damage - 750.0) < 1e-6
    assert [s for s in tr.history if s.kind == "trash"] == [], \
        "each pack archived separately - the symptom reported live"

    # The REAL declared boss still engages - the narrowing must not deafen us.
    # The arena trigger is present because this clause is about the champion
    # narrowing, not the declared-boss hold: the player is at the boss (5 m)
    # and the room is where they are standing.
    boss_addr = 0x41A1
    m._units = [_hero(PA), Entity(addr=boss_addr, cls="ent.Foe",
                                 unit_id="Nepsilon", x=5.0, y=5.0, z=5.0,
                                 hp=8000.0, is_foe=True), _boss_area()]
    tr.update()
    assert tr.in_boss_fight is True
    trashed = [s for s in tr.history if s.kind == "trash"]
    assert len(trashed) == 1, "the whole trash run archives once, at the boss"
    assert abs(trashed[0].group_damage - 750.0) < 1e-6


def test_a_paused_boss_fight_reports_the_same_dps_live_as_after_archiving():
    """The live meter and DPS Analysis must not disagree about one fight.

    Live 2026-10-03: a rift boss read 600 DPS on the Top DPS overlay and 637
    in DPS Analysis - a 6% gap that only appeared on fights containing a
    pause.

    Two clocks meet here. `pause()` stamps `end_time` from the LAST EVENT, so
    it is already up to one lull timeout (6 s) behind the wall clock;
    `resume()` then shifts `start_time` by the gap between two EVENT times.
    Both halves are correct for the archive, which measures entirely in
    event time - but live `duration` is `time.time() - start_time`, on the
    WALL clock. The un-tracked slice between the last event and the wall
    clock therefore stays in the live span and vanishes on archive: the live
    span is longer, so live DPS reads low, and the number jumps UP the moment
    the fight is archived.

    Fix: measure the live span in the same event time the archive uses -
    anchor the "now" of a live session to its last event, and let the ticking
    timer keep its own wall clock so it still moves between hits.
    """
    from farever_companion.core.dps_data import CombatSession
    t0 = time.time() - 120.0
    s = CombatSession("Boss Fight", "boss")
    s.record_hit("Boss", 1, 1000.0, 100.0, 63.7, when=t0)
    # Fight on for 60 s, then a lull: the pause is stamped from the LAST
    # EVENT, 6 s before the wall clock reaches the lull timeout.
    s.record_hit("Boss", 1, 4000.0, 60.0, 63.7, when=t0 + 60.0)
    s.check_combat_lull(t0 + 66.0, timeout_s=6.0)
    assert s.state == "PAUSED"
    # New damage 20 s of wall time after the lull.
    s.record_hit("Boss", 1, 500.0, 50.0, 63.7, when=t0 + 86.0)
    assert s.state == "COMBAT"

    live = s.duration
    s.pause()
    archived = s.duration
    assert abs(live - archived) < 0.5, (
        f"live span {live:.1f}s vs archived {archived:.1f}s - the meter and "
        "DPS Analysis compute DPS over different denominators, so the "
        "number changes when the fight is archived")


def test_a_resumed_fight_never_leaks_the_drain_delay_into_its_rates():
    """A resumed fight's rate denominator must stay on the EVENT clock.

    `resume()` shifts `start_time` forward to exactly the resuming event's
    stamp, and `record_hit` then stores that same stamp as `last_event_time` -
    so the two are EQUAL, and `duration`'s guard used to be a strict `>`.
    That equality fell through to the WALL clock, read at DRAIN time, so a
    backlog delivered late leaked the whole drain delay into the denominator.

    The live Activity Log shows exactly that shape - "DPS drain stalled 20s
    (worker thread starved?) — events drained late" - and this is why the
    module docstring for `resume` calls out the late-drain case at all. A
    fight resumed 5 s in and drained 25 s later reported a 25.0 s `duration`
    instead of 0.0 s, dividing every rate on that board by ~26x the truth.
    """
    from farever_companion.core.dps_data import CombatSession
    t_open = time.time() - 30.0
    s = CombatSession("Trash Mobs", "trash")
    s.record_hit("Mob", 1, 100.0, 100.0, 500.0, when=t_open)
    s.pause()
    # The resuming hit arrives 5 s after the pause, but is drained much later.
    s.record_hit("Mob", 1, 100.0, 100.0, 500.0, when=t_open + 5.0)
    assert s.last_event_time == s.start_time, (
        "fixture must reproduce the equal-stamp resume")
    assert s.duration == 0.0, (
        f"the drain delay leaked into the rate denominator: {s.duration:.1f}s "
        "for a fight that has only just re-opened")
    # The ticking timer is a separate measurement and still moves.
    assert s.wall_duration > 0.0

    # The event-anchored span still advances normally once real time passes.
    s.record_hit("Mob", 1, 100.0, 100.0, 500.0, when=t_open + 45.0)
    assert abs(s.duration - 40.0) < 0.01


def test_the_fight_timer_still_ticks_between_hits():
    """The event-clock fix must not freeze the displayed timer.

    `duration` now anchors to the last event so that live and archived DPS
    agree, which means it no longer advances on its own. The header timer is
    a separate, wall-clock measurement and must keep moving - a run timer
    that only changes on a hit is worse than the 6% it replaced.
    """
    from farever_companion.core.dps_data import CombatSession
    t0 = time.time() - 30.0
    s = CombatSession("Boss Fight", "boss")
    s.record_hit("Boss", 1, 1000.0, 100.0, 63.7, when=t0)
    first = s.wall_duration
    time.sleep(0.05)
    assert s.wall_duration > first, "the ticking timer stopped"


def test_history_load_stops_reading_once_it_has_the_recent_fights(tmp_path, monkeypatch):
    """Attaching must not parse a player's whole log tree on the GUI thread.

    Live 2026-10-03: the window went "Not Responding" and stayed there while
    DPS Analysis was open. `load()` walked EVERY month/day folder and fully
    parsed every JSON on the way, only to keep the newest 25 sessions - so a
    veteran player's attach re-read months of archives synchronously, and the
    GUI thread starved while it ran. The reader already keeps only the last
    25; walking NEWEST FIRST and stopping at a day boundary once 25 sessions
    are in hand returns the same list while touching a bounded number of
    files. The user had been asking for the info to live in the file NAME so
    logs would not need opening at all - this is the same idea applied to the
    reader that never needed most of what it opened.
    """
    import json
    from farever_companion.core import dps_tracker_history as hist_mod
    from farever_companion.core.dps_data import (_session_to_dict,
                                                 CombatSession)
    from farever_companion.core.dps_tracker import DpsTracker

    m = _Model()
    m.player_profile_value = "Mage"
    tr = DpsTracker(m)

    def _file(day: str, name: str, n: int, t_start: float) -> None:
        d = tmp_path / day
        d.mkdir(parents=True, exist_ok=True)
        s = CombatSession("Boss Fight", kind="boss")
        s.start_time = t_start
        s.end_time = t_start + 10.0
        s.record_hit("Boss", 1, 100.0, 100.0, 50.0, when=t_start + 5.0)
        (d / name).write_text(
            json.dumps({"history": [_session_to_dict(s) for _ in range(n)]}),
            encoding="utf-8")

    # Yesterday's archive and today's fights. Only today's may be read.
    _file("2026-09/09", "trash_Mage.json", 30, 1000.0)
    today = time.strftime("%Y-%m/%d")
    _file(today, "boss_Mage.json", 30, 2000.0)

    reads: list[str] = []
    real = hist_mod.read_history_file
    monkeypatch.setattr(
        hist_mod, "read_history_file",
        lambda p: (reads.append(str(p)), real(p))[1])

    tr.set_history_dir(tmp_path)   # triggers a first load (no profile yet)
    tr._active_profile = "Mage"    # the attach flow sets the hero before loading
    reads.clear()
    tr._history_load()

    # Only today's folder was opened - the field said it outright: "it should
    # just load todays list". Older days stay on disk for Past Fights.
    assert not any("2026-09" in r for r in reads), (
        f"load() read files it did not need: {reads}")
    assert reads and all(today.replace("/", "\\") in r or today in r
                         for r in reads)
    assert len(tr.history) == 25
    assert all(s.start_time >= 2000.0 for s in tr.history)
    starts = [s.start_time for s in tr.history]
    assert starts == sorted(starts), "history must stay sorted by fight start"

    # And a fresh day with no log yet loads nothing rather than reaching back.
    reads.clear()
    tr._history_dir = tmp_path / "fresh"
    tr._history_load()
    assert reads == []
    assert tr.history == [] or all(
        s.start_time >= 2000.0 for s in tr.history)


def test_the_boss_log_name_carries_dungeon_player_class_and_outcome(tmp_path):
    """A dir list must say everything about a boss log without opening it.

    Live 2026-10-03: after the whole-tree load freeze the field spelled out
    what the NAME should carry — "dungeon", "Boss", "playername", "class",
    "failed/passed" — so the app can list a folder and know each log's story
    from the names alone. The date was already the folder (`<YYYY-MM>/<DD>/`);
    the player was already the profile slug; this pins the three new tokens
    and the reader that parses them back out.
    """
    import json
    from farever_companion.core.dps_data import CombatSession
    from farever_companion.core.dps_tracker import DpsTracker
    from farever_companion.core.dps_tracker_history import DpsTrackerHistory
    from farever_companion.ui.combat.history_index import _file_descriptor

    m = _Model()
    m.player_profile_value = "Kartik_Priest_a1b2"
    tr = DpsTracker(m)
    tr.set_history_dir(tmp_path)
    tr._active_profile = "Kartik_Priest_a1b2"

    # One failed pull, then the kill. Same boss label -> the SAME file, and
    # the field's rule decides its name: "if the group killed it, it's not
    # a fail".
    def _boss_session(killed: bool, t0: float) -> CombatSession:
        s = CombatSession("Boss Fight", kind="boss")
        s.start_time = t0
        s.end_time = t0 + 30.0
        s.target_name = "Reblochonk"
        s.boss_label = "Reblochonk"
        s.boss_killed = killed
        from farever_companion.core.dps_data import PlayerParse
        me = PlayerParse(name="Kartik", is_me=True, total_damage=5000.0,
                         hero_class="Priest")
        s.players["Kartik"] = me
        return s

    tr.history = [_boss_session(False, time.time() - 60.0)]
    tr._persist_history()
    failed = list(_log_dir(tmp_path).glob("Reblochonk_*FAILED*.json"))
    assert len(failed) == 1, "a wipe archives under a FAILED name"

    tr.history.append(_boss_session(True, time.time()))
    tr._persist_history()
    files = list(_log_dir(tmp_path).glob("Reblochonk_*.json"))
    assert len(files) == 1 and files[0].stem.endswith("_PASS"), (
        f"the kill must upgrade the file to PASS, got "
        f"{[f.name for f in files]}")
    # The wipe's fights were folded into the PASS file, not lost with it.
    body = json.loads(files[0].read_text(encoding="utf-8"))
    assert len(body["history"]) == 2

    # The class and outcome tokens are legible from the name alone — the
    # descriptor is what a dir-list UI reads, and it opens nothing.
    row = _file_descriptor(files[0])
    assert row["outcome"] == "pass"
    assert "Priest" in row["char"] or "Priest" in files[0].stem
    assert row["kind"] == "boss"

    # The helper pieces agree: OUTCOME_TOKENS is what file_for writes and
    # what the descriptor reads, so they cannot drift apart.
    from farever_companion.core import dps_tracker_history as _hist
    assert _hist.OUTCOME_TOKENS == {"PASS", "FAILED"}


def test_an_auto_ended_dummy_test_waits_before_the_next_hit_restarts_it():
    """A tracker-concluded end must hold the door briefly and SAY so.

    Live 2026-10-03: the idle/out-of-range end fired mid-combat (the split
    showed "Dummy #1" and "Dummy #2"), the very next hit re-armed a fresh
    test instantly, and the heals between the two landed in neither test.
    The field asked for exactly this shape: "needs to be a wait 5-10s with a
    msg test ended starting new one / ready then reset it when dmg has
    started again". So: an auto end logs that the test is over and that a
    new one starts on the next damage after a cooldown; hits inside the
    cooldown re-arm nothing; the first hit after it does.
    """
    from farever_companion.core.dps_data import CombatSession
    m = _Model()
    tr = DpsTracker(m)
    lines: list[str] = []
    tr.log_line = lines.append

    def _armed_session(t_last: float) -> CombatSession:
        s = CombatSession("Dummy", "dummy")
        s.state = "COMBAT"
        s.start_time = t_last - 10.0
        s.end_time = None
        s.last_event_time = t_last
        s.target_addr = 0x1234      # the re-engage rule matches on this
        return s

    # A running test whose last hit just aged past the idle window.
    t_last = time.time() - 7.0
    tr.dummy_session = _armed_session(t_last)
    tr.in_dummy_fight = True
    tr.dummy_test_armed = True
    tr._dummy_finished_at = 0.0
    tr.dummy_rearm_s = 5.0

    class _Tick:
        now = t_last + 6.0
        current_foes = {}

    tr.finish_dummy_test("Dummy Idle")      # the idle end fires
    assert tr.in_dummy_fight is False
    assert tr.dummy_test_armed is False
    # The message exists and names both halves: ended + when a new one starts.
    assert any("test ended" in l.lower() and ("ready" in l.lower()
                                              or "next" in l.lower())
               for l in lines), lines

    finished_addr = tr._dummy_finished_addr
    # The re-engage gate only listens while a dummy is actually in range —
    # the same scene fact the engagement path reads.
    tr.dummy_near = (finished_addr, "Dummy", 100.0, 5.0)   # addr, name, hp, metres
    # A hit half a second later (a heal, a stray tick, the swing that was in
    # flight) must NOT restart the test inside the cooldown. Hit times are
    # capture timestamps, the cooldown is wall time since the end.
    tr._watch_dummy_reengage(t_last + 60.0, finished_addr)
    time.sleep(0.05)
    assert tr.dummy_test_armed is False, \
        "a hit inside the cooldown restarted the test"

    # After the cooldown, the next hit on the same dummy re-arms normally.
    from farever_companion.core.dps_dummy import DUMMY_REENGAGE_COOLDOWN_S
    time.sleep(DUMMY_REENGAGE_COOLDOWN_S + 0.1)
    tr._watch_dummy_reengage(t_last + 120.0, finished_addr)
    assert tr.dummy_test_armed is True, \
        "the cooldown never lifted: the next swing cannot start a test"

    # A hand Stop is untouched by any of this: the latch stays until the
    # player presses Start Test, exactly as before.
    tr.dummy_test_armed = False
    tr._dummy_stopped_by_hand = True
    tr._watch_dummy_reengage(t_last + 180.0, finished_addr)
    assert tr.dummy_test_armed is False


def test_your_own_hit_is_not_dropped_just_because_the_wire_left_it_nameless():
    """Your damage must survive an event that cannot name its source.

    Live 2026-10-03, a rift's trash pull: "my dmg stopped being recorded,
    every other player was still working". The asymmetry is the tell. A party
    member's hits arrive NAMED, so they resolve by name whatever else is
    broken; the local player's hits arrive flagged `is_me` with the bridge's
    placeholder name and often no source object at all, and that path ended
    in `_resolve_caster` -> (None, False) -> EVENT_UNRESOLVED -> dropped.

    `_resolve_caster` deliberately refuses the no-address case unless the
    scene proves you are alone, and it must keep refusing it for OTHER
    players - the 2026-10-01 leak credited everyone's damage to the local
    row. But a hit the bridge POSITIVELY flagged as ours, with nothing on the
    wire to contradict it (no name, no rival match), has no competing
    evidence. Dropping it loses your parse and tells you nothing; crediting
    the local row is the only answer that can be right.
    """
    from farever_companion.core.group_reader import GroupSnapshot, GroupMember

    m = _Model()
    tr = DpsTracker(m)
    m._units = [_hero(PA), _hero(ALLY), _foe(FOE)]
    m.roster = GroupSnapshot(group=0x5000, members=[
        GroupMember(player=0, hero=ALLY, name="Brit"),
        GroupMember(player=0, hero=PA, name="Me", is_me=True),
    ])
    tr.update()

    # The un-named, un-addressed shape: flagged as us, source object unknown.
    m.damage.push(DamageEvent(amount=1000.0, skill="Sword_Spin",
                              source_addr=0, target_addr=FOE,
                              source_name="Player", is_me=True,
                              capture_mode="injector", t=time.time()))
    tr.update()

    sess = tr.session
    assert sess.group_damage >= 1000.0, (
        "your own flagged hit was dropped: "
        f"{ {n: p.total_damage for n, p in sess.players.items()} }")
    me = [p for p in sess.players.values() if getattr(p, "is_me", False)]
    assert me and me[0].total_damage >= 1000.0, (
        "the hit must land on the LOCAL row, not a stranger's")

    # The guard that matters is untouched: a NAMED ally the scene places is
    # still an ally, however the flag is set (the 2026-10-01 leak).
    m.damage.push(DamageEvent(amount=500.0, skill="Ally_BigHit",
                              source_addr=ALLY, target_addr=FOE,
                              source_name="Brit", is_me=True,
                              capture_mode="injector", t=time.time()))
    tr.update()
    brit = sess.players.get("Brit")
    assert brit is not None and brit.total_damage >= 500.0
    assert brit.is_me is False, "a named ally must not become the local row"


def test_a_flaky_name_read_never_mints_a_second_local_row():
    """One local row, even when the live name read misses.

    The scene scan registers the row it is handed, and `_resolve_hero_name`
    returned the bare "You" whenever the pointer read came back empty. So a
    name read that flaked mid-run (loading, a stale pointer) left `Me (You)`
    and `You` BOTH flagged `is_me`; `own_row` then picks whichever was
    inserted first, and the player's own hits kept landing on the row the
    meter does NOT highlight while the named party kept ticking - the exact
    "my damage stopped being recorded" shape reported live 2026-10-03 from a
    rift's trash pull. Reusing the last name the read succeeded with keeps
    the row single.
    """
    from farever_companion.core.group_reader import GroupSnapshot, GroupMember

    m = _Model()
    tr = DpsTracker(m)
    m._units = [_hero(PA), _hero(ALLY), _foe(FOE)]
    m.roster = GroupSnapshot(group=0x5000, members=[
        GroupMember(player=0, hero=ALLY, name="Brit"),
        GroupMember(player=0, hero=PA, name="Me", is_me=True),
    ])
    tr.update()

    # The read flakes on the pull's first tick, but the hit is ours.
    m.names = {}
    m.damage.push(DamageEvent(amount=1000.0, skill="Sword_Spin",
                              source_addr=0, target_addr=FOE,
                              source_name="Player", is_me=True,
                              capture_mode="injector", t=time.time()))
    tr.update()

    for sess in (tr.trash_session, tr.overall_session):
        me_rows = [n for n, p in sess.players.items() if p.is_me]
        assert len(me_rows) == 1, (
            f"duplicate local rows in {sess.kind}: "
            f"{[(n, p.total_damage) for n, p in sess.players.items()]}")
    r = own_row(tr.overall_session)
    assert r is not None and r.total_damage >= 1000.0, (
        "the hit landed on a row the meter does not highlight: "
        f"{[(n, p.total_damage) for n, p in tr.overall_session.players.items()]}")


# --- the per-log sidecar: a read-free summary of an archived log -------------

def _boss_kill(name="Reblochonk", damage=4242.0, when=None, killed=True):
    """One archived boss session, complete enough to summarise."""
    from farever_companion.core.dps_data import CombatSession
    s = CombatSession("Boss Fight", "boss")
    s.boss_label = name
    s.target_name = f"\U0001f451 {name}"
    s.record_hit(name, FOE, 100.0, 100.0, damage,
                 when=when if when is not None else time.time())
    s.boss_killed = killed
    s.pause()
    # a single hit leaves a zero-length span; give the fight a real one so the
    # summary's duration/best-time fields have something to carry.
    s.end_time = (s.start_time or 0.0) + 42.0
    return s


def test_archiving_writes_a_read_free_summary_beside_the_log(tmp_path):
    """Every archived log gets an advisory `<log>.json.summary`.

    It holds what the filename cannot carry — fight count, best damage, the
    best-damage fight's duration and whether any boss pull failed — so the
    Fights list and the records strip can describe a log WITHOUT parsing it.
    """
    m = _Model()
    m.player_profile_value = "Kartik_Warrior_a1b2"
    tr = DpsTracker(m)
    tr.set_history_dir(tmp_path)
    tr._active_profile = "Kartik_Warrior_a1b2"

    now = time.time()
    tr.history = [_boss_kill(damage=4242.0, when=now),
                  _boss_kill(damage=900.0, when=now, killed=False)]
    tr._persist_history()

    day = _log_dir(tmp_path)
    logs = list(day.glob("Reblochonk*.json"))
    assert len(logs) == 1, [p.name for p in day.iterdir()]
    log = logs[0]
    from farever_companion.core.dps_tracker_history import summary_path_for
    side = summary_path_for(log)
    assert side.exists() and side.suffix == ".summary"

    from farever_companion.ui.combat.history_index import read_log_summary
    st = log.stat()
    summary = read_log_summary(log, st.st_mtime, st.st_size)
    assert summary is not None, "the sidecar was written but did not read back"
    assert summary["fights"] == 2
    assert summary["best_damage"] == 4242.0
    assert summary["best_duration"] > 0.0
    assert summary["has_failure"] is True
    assert summary["bosses"]["Reblochonk"]["attempts"] == 2
    assert summary["bosses"]["Reblochonk"]["kills"] == 1

    # the sidecar is not a fight file: the scan finds the LOG, not the sidecar
    from farever_companion.ui.combat.history_index import scan_history_dir
    rows = scan_history_dir(tmp_path)
    assert [r["path"] for r in rows] == [log]
    assert rows[0]["summary"]["best_damage"] == 4242.0


def test_a_sidecar_is_ignored_once_its_log_changes(tmp_path):
    """The sidecar is advisory: the `.json` always wins.

    The writer stamps the log's own (mtime, size) into the sidecar, so a log
    rewritten since it was summarised no longer matches and the reader returns
    None — the caller falls back to parsing rather than serving a stale number.
    """
    from farever_companion.ui.combat.history_index import (
        read_log_summary, write_log_summary)

    p = tmp_path / "log.json"
    p.write_text('{"history": []}', encoding="utf-8")
    assert write_log_summary(p, [_boss_kill()]) is not None
    st = p.stat()
    assert read_log_summary(p, st.st_mtime, st.st_size) is not None

    # a changed log: a different size/mtime -> the fingerprint no longer matches
    p.write_text('{"history": [{"name": "changed"}]}', encoding="utf-8")
    st2 = p.stat()
    assert read_log_summary(p, st2.st_mtime, st2.st_size) is None

    # a missing sidecar reads as None too, never an error
    p.unlink()
    assert read_log_summary(p, 0.0, 0) is None


def test_the_sidecar_rollup_reproduces_the_parsed_records():
    """A record built from sidecars equals one built from parsed sessions.

    This is the load-bearing claim of the whole sidecar: if `summarize` and
    `boss_records` can disagree, the strip would show a different personal best
    depending on whether a log had been converted yet.
    """
    import pytest
    from farever_companion.core.dps_records import (
        boss_records, merge_rollup, records_from_rollups, summarize)

    now = time.time()
    log_a = [_boss_kill("Reblochonk", damage=4242.0, when=now),
             _boss_kill("Reblochonk", damage=900.0, when=now, killed=False)]
    log_b = [_boss_kill("Reblochonk", damage=5000.0, when=now + 100.0),
             _boss_kill("Zul'farak", damage=777.0, when=now)]

    parsed = boss_records(log_a + log_b)
    rollups: dict = {}
    for log in (log_a, log_b):
        for boss, roll in summarize(log)["bosses"].items():
            merge_rollup(rollups.setdefault(boss, {}), roll)
    sidecar = records_from_rollups(rollups)

    assert [(r.boss, r.kills, r.attempts) for r in parsed] == \
        [(r.boss, r.kills, r.attempts) for r in sidecar]
    for a, b in zip(parsed, sidecar):
        assert a.best_time == pytest.approx(b.best_time)
        assert a.avg_time == pytest.approx(b.avg_time)
        assert a.best_dps == pytest.approx(b.best_dps)
        assert a.total_damage == pytest.approx(b.total_damage)


def test_a_per_boss_name_is_matched_by_its_full_profile_slug():
    """The pruner's rule, as a pure function.

    It used to compare the LAST underscore segment (`PASS`, or the `a1b2` hash)
    against the full active slug, so no per-boss file ever matched its own
    character and a stranger's boss logs were never swept. It must keep our own
    (including the class `file_for` may append) and reject everyone else's,
    without over-stripping a slug that legitimately ends in a class word.
    """
    from farever_companion.core.dps_tracker_history import boss_file_is_mine

    slug = "Kartik_Warrior_a1b2"
    assert boss_file_is_mine("Reblochonk_Kartik_Warrior_a1b2_PASS", slug)
    assert boss_file_is_mine("Reblochonk_Kartik_Warrior_a1b2_FAILED_HARD", slug)
    assert boss_file_is_mine("Reblochonk_Kartik_Warrior_a1b2", slug)
    # the trash half carries the same slug, with no outcome token
    assert boss_file_is_mine(
        "Kobolds Mines - Reblochonk trash_Kartik_Warrior_a1b2", slug)
    # ... and a stranger's is nobody else's
    assert not boss_file_is_mine("Reblochonk_Alice_Rogue_c9d0_PASS", slug)
    assert not boss_file_is_mine("Reblochonk_Alice_Rogue_c9d0", slug)

    # a slug the writer extends with the class word is still ours ...
    assert boss_file_is_mine("Reblochonk_Kartik_a1b2_Warrior_PASS", "Kartik_a1b2")
    # ... a slug that ALREADY ends in its class word is not over-stripped ...
    assert boss_file_is_mine("Reblochonk_Bob_Warrior_PASS", "Bob_Warrior")
    # ... and the ambiguous pair resolves toward KEEPING, never deleting
    assert boss_file_is_mine("Reblochonk_Bob_Warrior_PASS", "Bob")
    assert not boss_file_is_mine("Reblochonk_Alice_Rogue_c9d0_PASS", "Bob")

    # no active profile: the unprofiled logs are ours, profiled ones are not
    assert boss_file_is_mine("Reblochonk_PASS", "")
    assert boss_file_is_mine("Reblochonk_Warrior_PASS", "")
    assert not boss_file_is_mine("Reblochonk_Kartik_Warrior_a1b2_PASS", "")


def test_a_character_switch_sweeps_the_previous_characters_per_boss_logs(tmp_path):
    """On disk, the fixed rule actually clears another character's boss logs.

    `Alice`'s per-boss log — and the sidecar beside it — must go the moment the
    active profile is `Kartik`; before the fix the pruner read `PASS` as the
    profile and kept it forever. Our own freshly written boss log stays.
    """
    day = _log_dir(tmp_path)
    day.mkdir(parents=True, exist_ok=True)
    stranger = day / "Reblochonk_Alice_Rogue_c9d0_PASS.json"
    stranger.write_text('{"history": []}', encoding="utf-8")
    stranger_side = Path(str(stranger) + ".summary")
    stranger_side.write_text("{}", encoding="utf-8")

    m = _Model()
    m.player_profile_value = "Kartik_Warrior_a1b2"
    tr = DpsTracker(m)
    tr.set_history_dir(tmp_path)
    tr._active_profile = "Kartik_Warrior_a1b2"
    tr.history = [_boss_kill(damage=1000.0)]
    tr._persist_history()

    assert not stranger.exists(), \
        "another character's per-boss log survived the prune"
    assert not stranger_side.exists(), "its sidecar was left orphaned"
    assert [p.name for p in day.glob("Reblochonk_Kartik*")], \
        [p.name for p in day.iterdir()]


def test_a_skill_row_does_not_write_the_fields_it_does_not_use():
    """A pure-damage skill used to write nine zero fields on EVERY hit's row.

    Live 2026-10-04: "my dps logs is now in mbs ... we dont need rows where the
    value is 0". A skill row is the bulk of an archive and typically uses half
    its keys, so omitting the unused ones is where the size comes from — and
    every reader already defaults the key it does not find, so the fight the
    file describes does not change.
    """
    from farever_companion.core.dps_data import (
        CombatSession, SkillParse, _session_from_dict, _session_to_dict,
        _skill_to_dict)

    sp = SkillParse(skill_id="Sword_Swarm", name="Hive Swarm")
    sp.damage, sp.hit_count, sp.min_hit, sp.max_hit = 1028.0, 34, 25.0, 42.0
    out = _skill_to_dict(sp)
    for unused in ("heals", "self_heals", "group_heals", "heal_count",
                   "min_heal", "max_heal", "damage_taken", "taken_count",
                   "affinity_hits", "subskills", "crit_count"):
        assert unused not in out, f"{unused} is a zero the row does not use"
    assert out["damage"] == 1028.0 and out["hit_count"] == 34
    assert out["min_hit"] == 25.0 and out["max_hit"] == 42.0

    # a field that IS used is never dropped
    sp.heals, sp.heal_count, sp.self_heals = 12.0, 1, 12.0
    out2 = _skill_to_dict(sp)
    assert out2["heals"] == 12.0 and out2["heal_count"] == 1
    assert out2["self_heals"] == 12.0

    # and the archive still round-trips to the same fight
    s = CombatSession("Boss Fight", "boss")
    s.record_hit("Boss", 1, 100.0, 100.0, 1028.0, skill_id="Sword_Swarm",
                 skill_name="Hive Swarm", when=1000.0)
    s.pause()
    d = _session_to_dict(s)
    assert "deaths" not in d and "burst_peak_time" not in d
    assert _session_to_dict(_session_from_dict(d)) == d


def test_a_session_does_not_write_the_totals_it_can_recompute():
    """`state`, `duration` and the four `group_*` totals are write-only keys.

    Every archived session loads as PAUSED and exposes all six as computed
    properties (`duration` from the stored span, the totals from the player
    rows), so `_session_from_dict` never reads any of them back. Storing them
    therefore only inflated every fight on disk. Live 2026-10-04: "my dps
    logs is now in mbs" — this is one of the shaves on top of the row trim.
    """
    from farever_companion.core.dps_data import (
        CombatSession, _session_from_dict, _session_to_dict)

    s = CombatSession("Boss Fight", "boss")
    s.record_hit("Boss", FOE, 100.0, 100.0, 1200.0,
                 skill_id="Sword_Swarm", skill_name="Hive Swarm", when=1000.0)
    s.record_heal("Boss", 300.0, caster_name="Kartik", when=1010.0)
    s.pause()
    # a single hit leaves a zero-length span; give the fight a real one.
    s.end_time = (s.start_time or 0.0) + 30.0

    d = _session_to_dict(s)
    for unread in ("state", "duration", "group_damage", "group_heals",
                   "group_dps", "group_hps"):
        assert unread not in d, f"{unread} is written but never read back"

    # the six it dropped are recomputed on load, from rows the file keeps
    again = _session_from_dict(d)
    assert again.state == "PAUSED"
    assert again.duration == 30.0
    assert again.group_damage == 1200.0
    assert again.group_heals == 300.0
    assert again.group_dps == 1200.0 / 30.0
    assert again.group_hps == 300.0 / 30.0

    # and the write is idempotent, so nothing was lost that a re-read needed
    assert _session_to_dict(again) == d


def test_the_board_trace_explains_a_meter_clear(tmp_path, monkeypatch):
    """A cleared meter with no reset line is explained by the board trace.

    Live 2026-10-04, in a dungeon: "top dps is still resetting after every time
    i leave combat ... no reset line — it just clears". The reset reasons are
    logged; the fact that the AUTO board switched to a different session (or its
    total fell) is not, so the clear had no explanation. This is the opt-in
    diagnostic that names it, one line per board change.
    """
    import json
    from pathlib import Path

    monkeypatch.setenv("FAREVER_DPS_TRACE", "1")
    monkeypatch.setenv("FAREVER_MODDATA_DIR", str(tmp_path))

    m = _Model()
    tr = DpsTracker(m)
    lines: list[str] = []
    tr.log_line = lines.append

    # seed on the boss board (the auto pick while a boss has damage)
    tr.boss_session.record_hit("Boss", FOE, 100.0, 100.0, 900.0, when=1000.0)
    tr._trace_board_state()
    assert not any(l.startswith("Top DPS: board") for l in lines), lines

    # boss wiped, trash now carries damage: the auto board SWITCHES
    tr.boss_session.reset()
    tr.trash_session.record_hit("Mob", FOE, 50.0, 50.0, 300.0, when=1001.0)
    tr._trace_board_state()
    assert any("board switch" in l for l in lines), lines

    # the OVERALL board is shown and then drops in place: a CLEAR, same session
    tr.trash_session.reset()
    tr.overall_session.record_hit("Mob", FOE, 50.0, 50.0, 300.0, when=1002.0)
    tr._trace_board_state()          # boss/trash empty -> auto picks overall
    tr.overall_session.reset()
    tr._trace_board_state()
    cleared = [l for l in lines if "board cleared" in l]
    assert cleared, lines
    assert "boss=" in cleared[0] and "inst=" in cleared[0] and "combat=" in cleared[0]

    # the file carries the same event with the decision state beside it
    trace = Path(tmp_path) / "dps" / "dps_trace.jsonl"
    assert trace.exists()
    last = json.loads(trace.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert last["event"] == "cleared"
    for key in ("board", "dmg", "prev_dmg", "in_boss_fight", "instance_now",
                "boss", "trash", "overall", "in_combat"):
        assert key in last, key


def test_the_board_trace_is_off_unless_asked(tmp_path, monkeypatch):
    """The diagnostic is opt-in: no env switch, no file, no log line."""
    from pathlib import Path

    monkeypatch.delenv("FAREVER_DPS_TRACE", raising=False)
    monkeypatch.setenv("FAREVER_MODDATA_DIR", str(tmp_path))

    m = _Model()
    tr = DpsTracker(m)
    lines: list[str] = []
    tr.log_line = lines.append
    tr.boss_session.record_hit("Boss", FOE, 100.0, 100.0, 900.0, when=1000.0)
    tr._trace_board_state()
    tr.boss_session.reset()
    tr.overall_session.record_hit("Mob", FOE, 50.0, 50.0, 300.0, when=1001.0)
    tr._trace_board_state()

    assert not any(l.startswith("Top DPS: board") for l in lines), lines
    assert not (Path(tmp_path) / "dps" / "dps_trace.jsonl").exists()
