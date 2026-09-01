"""Shared DPS-test scaffolding: fake damage source/model, entities, addrs.

Superset of the former per-file copies in test_dps.py and
test_dps_overlay_ui.py — every behavior a test relies on lives here once:
the event-ring source (status, counts, stage timings, stale/decoded flags),
the model (name map, combat state, profile, pet owners, roster), the
hero/foe/pet entity builders, the address constants, and the offscreen
QApplication fixture. tests/fakemem.py is untouched (memory fakes).
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from farever_companion.core.damage_events import DamageEvent
from farever_companion.core.scene import Entity

PA = 0x1111      # local hero
TEAM = 0x2222    # party member (test_dps name)
ALLY = 0x2222    # nearby group member (overlay name; same slot as TEAM)
FOE = 0x3333     # a trash mob
BOSS = 0x4444    # the boss
PET = 0x5555     # a player-owned summon


@pytest.fixture(scope="module", autouse=True)
def _qapp():
    """One offscreen QApplication for the whole module."""
    from PySide6 import QtWidgets
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


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

    def ensure_started(self):
        pass

    def events_after(self, cursor: int):
        evs = self._evs[cursor:]
        return len(self._evs), evs

    def push(self, ev: DamageEvent):
        self._evs.append(ev)

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


class _FakeModel:
    """LiveModel stand-in: units, names, combat state, pets, roster."""

    is_dungeon = False
    is_rift = False
    dungeon_boss: str | None = None

    def __init__(self, names: dict[int, str] | None = None):
        self.player_addr = PA
        self.damage = _FakeSource()
        self._units: list[Entity] = []
        self.player_profile_value: str | None = None
        self.pet_owner_map: dict[int, int] = {}
        self.combat_state_value: dict | None = None
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
        return getattr(self, "roster", None)

    def _hero_player_ptr(self, addr):
        return None

    def _player_name_at(self, ptr):
        return None


def _hero(addr: int = PA, cls: str = "ent.hero.Warrior",
          name: str | None = None, hp: float = 1000.0) -> Entity:
    return Entity(addr=addr, cls=cls, unit_id=None, x=1.0, y=1.0, z=1.0,
                  hp=hp, is_hero=True)


def _foe(addr: int = FOE, uid: str = "Trash_Pig",
         hp: float = 100.0) -> Entity:
    return Entity(addr=addr, cls="ent.Foe", unit_id=uid, x=5.0, y=5.0, z=5.0,
                  hp=hp, is_foe=True)


def _pet(addr: int, owner: int, cls: str = "ent.foe.Companion",
         owner_cls: str = "ent.Hero") -> Entity:
    """A player-owned summon: an ent.Foe unit whose owner is a hero."""
    return Entity(addr=addr, cls=cls, unit_id="Pet_Skeleton",
                  x=1.0, y=1.0, z=1.0, hp=500.0, is_foe=True,
                  owner_addr=owner, owner_cls=owner_cls)
