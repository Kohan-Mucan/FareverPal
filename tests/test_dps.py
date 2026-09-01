"""Headless tests for the event-driven DpsTracker (core/dps_tracker.py).

Real damage events (the model's DamageSourceManager ring) are authoritative;
HP-delta metering is an opt-in fallback: with it disabled (``hp_est_allowed``
False, the default) dropping enemy HP alone records nothing, boss routing
follows the in-boss-fight segment, and unresolvable casters are never
fabricated onto another player.
"""
from __future__ import annotations

import json
import time

from farever_companion.core.damage_events import DamageEvent, K_HEAL
from farever_companion.core.dps_tracker import (
    DpsTracker, GAME_COMBAT_END_CONFIRM_S, BOSS_IDLE_AUX_S)

from tests.dps_fakes import (
    PA, TEAM, FOE, BOSS, PET, _FakeSource, _FakeModel as _Model,
    _hero, _foe, _pet,
)


def test_hp_drops_alone_record_nothing_when_fallback_disabled():
    """With the HP-diff fallback off, dropping enemy HP alone records nothing."""
    m = _Model()
    tr = DpsTracker(m)
    tr.hp_est_allowed = False        # user turned the fallback off
    m._units = [_hero(PA), _foe(FOE, hp=5000.0)]
    tr.update()
    m._units = [_hero(PA), _foe(FOE, hp=4000.0)]
    tr.update()
    m._units = [_hero(PA), _foe(FOE, hp=0.0)]
    tr.update()
    assert tr.overall_session.group_damage == 0.0
    assert tr.overall_session.group_heals == 0.0
    assert tr.overall_session.state == "READY"


def test_hp_est_fallback_is_off_by_default():
    """The HP-diff fallback is off by default: a silent source never fabricates
    damage from HP drops."""
    m = _Model()
    tr = DpsTracker(m)
    assert tr.hp_est_allowed is False
    m._units = [_hero(PA), _foe(FOE, hp=5000.0)]
    tr.update()
    m._units = [_hero(PA), _foe(FOE, hp=4000.0)]
    tr.update()
    assert tr.overall_session.group_damage == 0.0


def test_hp_est_fallback_when_enabled_records_hp_drops():
    """With the fallback enabled, enemy HP drops become estimated self damage
    ("HP diff (est.)") when the event source is silent."""
    m = _Model()
    tr = DpsTracker(m)
    tr.hp_est_allowed = True
    m._units = [_hero(PA), _foe(FOE, hp=5000.0)]
    tr.update()
    m._units = [_hero(PA), _foe(FOE, hp=4000.0)]
    tr.update()
    assert abs(tr.overall_session.group_damage - 1000.0) < 1e-6
    # ...and the fallback shows up labeled, not as a real skill
    me = tr.overall_session.players.get("Me (You)")
    assert me is not None and "(HP)" in me.skills


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
    assert p.damage_taken_est == 0.0             # authoritative event, not est


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


def test_hero_hp_drop_is_labeled_estimate_fallback():
    """Hero HP is only a fallback estimate during reader lulls, never outgoing damage."""
    m = _Model()
    tr = DpsTracker(m)
    m._units = [_hero(PA, hp=1000.0), _foe(FOE)]
    tr.update()                     # seeds HP
    m._units = [_hero(PA, hp=700.0), _foe(FOE)]
    tr.update()
    s = tr.overall_session
    assert s.group_damage == 0.0                  # HP is never outgoing damage
    p = s.players.get("Me (You)")
    assert p is not None
    assert p.damage_taken == 0.0                  # estimate does not inflate
    assert abs(p.damage_taken_est - 300.0) < 1e-6  # fully an estimate


def test_hp_fallback_never_double_counts_with_events():
    """Events gate off HP estimates entirely so the two can never double-count."""
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
    assert p.damage_taken_est == 0.0

    # next tick: events stopped, HP drops again - the 3s lull is NOT over yet
    m._units = [_hero(PA, hp=600.0), _foe(FOE)]
    tr.update()
    assert abs(p.damage_taken - 30.0) < 1e-6      # still no estimate
    assert p.damage_taken_est == 0.0


def test_hp_est_engages_after_lull_and_skips_death():
    """The estimate engages only after the lull, and death (hp -> 0) never counts."""
    m = _Model()
    tr = DpsTracker(m)
    tr.hp_est_allowed = True
    m._units = [_hero(PA, hp=1000.0), _foe(FOE)]
    tr.update()                     # seeds HP

    # reader alive: an event ticks through, then HP drops - still gated
    m.damage.push(DamageEvent(amount=10.0, skill="Sword_Base_Attack",
                              source_addr=PA, target_addr=FOE, t=time.time()))
    m._units = [_hero(PA, hp=900.0), _foe(FOE)]
    tr.update()
    p = tr.overall_session.players.get("Me (You)")
    assert p.damage_taken == 0.0 and p.damage_taken_est == 0.0

    # simulate the lull passing, then a real HP drop -> estimate appears
    tr._last_events_ts = time.time() - 10.0
    m._units = [_hero(PA, hp=800.0), _foe(FOE)]
    tr.update()
    assert p.damage_taken == 0.0                  # estimate-only, never main
    assert abs(p.damage_taken_est - 100.0) < 1e-6

    # death: hp drops to 0 -> NOT damage taken
    m._units = [_hero(PA, hp=0.0), _foe(FOE)]
    tr.update()
    assert p.damage_taken == 0.0                  # unchanged
    assert abs(p.damage_taken_est - 100.0) < 1e-6


def test_boss_segment_routing_and_kill_ends_fight():
    m = _Model()
    m.dungeon_boss = "Boss_Foo"
    tr = DpsTracker(m)
    m._units = [_hero(PA), _foe(BOSS, uid="Boss_Foo", hp=100000.0), _foe(FOE)]
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
    # boss damage (500 + the 200 kill blow) and the add's 300 each archive as
    # their own segment under the boss fight
    boss_archives = [s for s in tr.history if s.kind == "boss"]
    add_archives = [s for s in tr.history if s.kind == "boss_adds"]
    assert boss_archives and abs(boss_archives[-1].group_damage - 700.0) < 1e-6
    assert add_archives and abs(add_archives[-1].group_damage - 300.0) < 1e-6


def test_training_dummy_engages_own_boss_group():
    """Training dummies fight like bosses: the scene flags them dummy-like so
    dummy parses land in the dummy session (own group), never in trash."""
    m = _Model()
    tr = DpsTracker(m)
    m._units = [_hero(PA), _foe(BOSS, uid="TrainingDummy", hp=100000.0),
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
    """A zero-amount kill notification (st.Player.notifyUnitKilled__impl uid)
    matching the active boss arms the kill grace, so a boss that dies and
    despawns before its HP ever reads 0 ends as 'Boss Defeated' — not the
    fallback 'Boss Fight'."""
    m = _Model()
    m.dungeon_boss = "Boss_Foo"
    tr = DpsTracker(m)
    logs = []
    tr.log_line = logs.append
    m._units = [_hero(PA), _foe(BOSS, uid="Boss_Foo", hp=100000.0)]
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
                _foe(BOSS, uid="Boss_Foo", hp=100000.0),
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


def test_boss_adds_segment_is_selectable_and_archives_with_boss():
    """The boss-adds segment is selectable and archives separately with the boss."""
    m = _Model()
    m.dungeon_boss = "Boss_Foo"
    tr = DpsTracker(m)
    m._units = [_hero(PA), _foe(BOSS, uid="Boss_Foo", hp=100000.0), _foe(FOE)]
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
    m._units = [_hero(PA), _foe(BOSS, uid="Boss_Foo", hp=100000.0), _foe(FOE)]
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


def test_history_persistence_disabled_by_default():
    """No path set -> archives never touch disk (headless tools/probes)."""
    m = _Model()
    tr = DpsTracker(m)
    assert tr._history_path is None and tr._history_dir is None
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


def test_history_archives_into_profile_scoped_file(tmp_path):
    """set_history_dir() writes one dps_history_<profile>.json per character."""
    m = _Model()
    m.player_profile_value = "Kartik_Warrior_a1b2"
    tr = DpsTracker(m)
    tr.set_history_dir(tmp_path)

    m._units = [_hero(PA), _foe(FOE)]
    tr.update()
    m.damage.push(DamageEvent(amount=50.0, skill="Sword_Base_Attack",
                              source_addr=PA, target_addr=FOE, t=time.time()))
    tr.update()
    tr.reset()

    prof_file = tmp_path / "dps_history_Kartik_Warrior_a1b2.json"
    assert prof_file.exists()                 # per-profile file, not the shared one
    assert not (tmp_path / "dps_history.json").exists()

    import json
    saved = json.loads(prof_file.read_text(encoding="utf-8"))
    assert len(saved["history"]) == 1


def test_profile_swap_reloads_other_characters_history(tmp_path):
    """Switching characters swaps to the new profile's history file."""
    m = _Model()
    m.player_profile_value = "Kartik_Warrior_a1b2"
    tr = DpsTracker(m)
    tr.set_history_dir(tmp_path)

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

    assert tr._active_profile == "Maru_Rogue_c9d0"
    assert tr.history == []                   # Kartik's fights not in memory
    assert (tmp_path / "dps_history_Kartik_Warrior_a1b2.json").exists()
    assert not (tmp_path / "dps_history_Maru_Rogue_c9d0.json").exists()

    # Maru fights and archives -> lands in Maru's own file
    m.damage.push(DamageEvent(amount=30.0, skill="Bow_Base_Attack",
                              source_addr=PA, target_addr=FOE, t=time.time()))
    tr.update()
    tr.reset()
    assert len(tr.history) == 1
    assert (tmp_path / "dps_history_Maru_Rogue_c9d0.json").exists()
    import json
    maru_data = json.loads((tmp_path / "dps_history_Maru_Rogue_c9d0.json")
                           .read_text(encoding="utf-8"))
    assert len(maru_data["history"]) == 1

    # switching BACK to Kartik restores his past fights from his file
    m.player_profile_value = "Kartik_Warrior_a1b2"
    tr._profile_ts = 0.0
    tr.update()
    assert tr._active_profile == "Kartik_Warrior_a1b2"
    assert len(tr.history) == 1


def test_unresolvable_profile_falls_back_to_shared_file(tmp_path):
    """Unresolved profiles fall back to the shared dps_history.json."""
    m = _Model()
    m.player_profile_value = None
    tr = DpsTracker(m)
    tr.set_history_dir(tmp_path)

    m._units = [_hero(PA), _foe(FOE)]
    tr.update()
    m.damage.push(DamageEvent(amount=25.0, skill="Sword_Base_Attack",
                              source_addr=PA, target_addr=FOE, t=time.time()))
    tr.update()
    tr.reset()
    assert (tmp_path / "dps_history.json").exists()   # fallback file used


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
    m._units = [_hero(PA), _foe(BOSS, uid="Boss_Foo", hp=100000.0)]
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
    m._units = [_hero(PA), _foe(BOSS, uid="Boss_Foo", hp=100000.0)]
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

    m._units = [_hero(PA), _foe(BOSS, uid="Boss_Foo", hp=100000.0)]
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
        return Entity(addr=DUMMY, cls="ent.Foe", unit_id="TrainingDummy",
                      x=5.0, y=5.0, z=5.0, hp=hp, is_foe=True)

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
