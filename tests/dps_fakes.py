"""Shared DPS-test scaffolding: fake damage source/model, entities, addrs.

Superset of the former per-file copies in test_dps.py and
test_dps_overlay_ui.py — every behavior a test relies on lives here once:
the event-ring source (status, counts, stage timings, stale/decoded flags),
the model (name map, combat state, profile, pet owners, roster), the
hero/foe/pet entity builders, the address constants, and the offscreen Qt
platform default. Each test module brings up its own `_qapp` fixture.
tests/fakemem.py is untouched (memory fakes).
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from farever_companion.core.damage_events import DamageEvent
from farever_companion.core.dps_source import DpsEventStats
from farever_companion.core.scene import Entity

PA = 0x1111      # local hero
TEAM = 0x2222    # party member (test_dps name)
ALLY = 0x2222    # nearby group member (overlay name; same slot as TEAM)
FOE = 0x3333     # a trash mob
BOSS = 0x4444    # the boss
PET = 0x5555     # a player-owned summon


class _FakeSource:
    """DamageSourceManager stand-in: event ring + live status only."""

    def __init__(self):
        self._evs: list[DamageEvent] = []
        self._calib_s: float = 0.0
        self._map_s: float = 0.0
        self._first_event_s: float = 0.0
        self._status = "live"
        self.damage_stale = False
        self.has_decoded = False
        self.rehunt_calls = 0
        self.recalibrate_calls = 0
        # Real counter set, not a stub: the capture diagnostic reads these.
        self.stats = DpsEventStats()
        # One bool per update() tick, as the real manager records it.
        self.combat_hints: list[bool] = []

    def ensure_started(self):
        pass

    def note_combat(self, in_fight: bool) -> None:
        """The tracker's per-tick fight hint (a bool write in the real one)."""
        self.combat_hints.append(bool(in_fight))

    def note_watched(self, in_watched: bool) -> None:
        """The tracker's per-tick capture-watch flag (a bool write)."""
        self.watched = bool(in_watched)

    def force_wide_rehunt(self, why: str = ""):
        self.rehunt_calls += 1

    def recalibrate(self):
        self.recalibrate_calls += 1

    def events_after(self, cursor: int):
        evs = self._evs[cursor:]
        return len(self._evs), evs

    def push(self, ev: DamageEvent):
        self._evs.append(ev)
        # The real manager's `_push` counts what it decodes; the fake stands in
        # for the engine here, so the capture diagnostic's numbers mean the same
        # thing in a test as they do live.
        self.stats.note_decoded(1)

    def timing_text(self):
        parts = []
        if self._calib_s > 0.0:
            parts.append(f"calib {self._calib_s:.1f}s")
        if self._map_s > 0.0:
            parts.append(f"map {self._map_s:.1f}s")
        if self._first_event_s > 0.0:
            parts.append(f"1st event {self._first_event_s:.1f}s")
        return " · ".join(parts)

    def status(self):
        return self._status

    def counts(self):
        return 1, 0

    def live_source_name(self):
        return "damage numbers"

    def diagnostics(self):
        return {}

    def event_path_counts(self):
        return self.stats.snapshot()


# Sentinel for "leave the default alone" - None is a legal combat_state value
# (a model that reports no combat), so it cannot double as the marker.
_UNSET = object()


class _FakeModel:
    """LiveModel stand-in: units, names, combat state, pets, roster.

    Every extra surface is an optional keyword, so a caller that needs a
    party, a live unit scan or an already-seeded event ring configures the
    same class instead of copying it: the dev-only HUD previews
    (build_tools/dev/devicons.py) are the other consumers.
    """

    is_dungeon = False
    is_rift = False
    dungeon_boss: str | None = None

    def __init__(self, names: dict[int, str] | None = None, *,
                 damage=None, roster=None,
                 units: list[Entity] | None = None,
                 combat_state=_UNSET):
        self.player_addr = PA
        self.damage = damage if damage is not None else _FakeSource()
        self.roster = roster
        self._units: list[Entity] = list(units) if units else []
        self.player_profile_value: str | None = None
        self.pet_owner_map: dict[int, int] = {}
        self.combat_state_value: dict | None = (
            {"in_combat": True} if combat_state is _UNSET else combat_state)
        self.names: dict[int, str] = (
            dict(names) if names is not None
            else {PA: "Me", TEAM: "Teammate"}
        )

    def player_xyz(self):
        return (0.0, 0.0, 0.0)

    def units(self):
        return list(self._units)

    def combat_state(self):
        return self.combat_state_value

    def player_name(self, addr):
        return self.names.get(addr)

    def player_profile(self):
        return self.player_profile_value

    def resolve_pet_owner(self, addr):
        """Live owner-slot read stand-in: empty map means unowned."""
        return self.pet_owner_map.get(addr, 0)

    def group_roster(self):
        return self.roster

    def _hero_player_ptr(self, addr):
        return None

    def _player_name_at(self, ptr):
        return None


def _hero(addr: int = PA, cls: str = "ent.hero.Warrior",
          name: str | None = None, hp: float = 1000.0) -> Entity:
    return Entity(addr=addr, cls=cls, unit_id=None, x=1.0, y=1.0, z=1.0,
                  hp=hp, is_hero=True)


def _foe(addr: int = FOE, uid: str = "Trash_Pig",
         hp: float = 100.0, is_boss: bool = False) -> Entity:
    e = Entity(addr=addr, cls="ent.Foe", unit_id=uid, x=5.0, y=5.0, z=5.0,
               hp=hp, is_foe=True)
    if is_boss:
        # scene.Entity carries no is_boss field; the tracker's scan reads
        # one off the unit (getattr) for bosses the data table has not
        # named — the test-side stand-in for the calibrated flag bit.
        e.is_boss = True
    return e


def training_dummy(addr: int = FOE, uid: str = "Dummy",
                   hp: float = 100000.0, x: float = 2.0, y: float = 1.0,
                   z: float = 1.0) -> Entity:
    """A training dummy STANDING AT the player (1 m), as one is in a real yard.

    Inside the default 5 m Dummy Range on purpose: `_foe`'s default 5,5,5 sits
    ~6.9 m away, OUTSIDE it, so a dummy fixture at that spot would make every
    dummy test depend on the default radius it is not testing — the one thing a
    test should never be coupled to. `x/y/z` are explicit for the tests that
    need a second dummy somewhere else.
    """
    return Entity(addr=addr, cls="ent.Foe", unit_id=uid, x=x, y=y, z=z,
                  hp=hp, is_foe=True)


def _pet(addr: int, owner: int, cls: str = "ent.foe.Companion",
         owner_cls: str = "ent.Hero") -> Entity:
    """A player-owned summon: an ent.Foe unit whose owner is a hero."""
    return Entity(addr=addr, cls=cls, unit_id="Pet_Skeleton",
                  x=1.0, y=1.0, z=1.0, hp=500.0, is_foe=True,
                  owner_addr=owner, owner_cls=owner_cls)
