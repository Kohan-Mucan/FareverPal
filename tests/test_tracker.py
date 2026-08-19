"""TrackController waypoint tracking tests (headless Qt).

Regression: soulstone summon spots are fixed waypoints (like dungeons, rifts
and obelisks), so with a model attached the compass-needle tick must resolve
them instead of clearing the freshly-set target. Before the fix the
`_target()` waypoint-kind list omitted 'soulstone', so an attached tick
returned None and wiped the tracking the moment it was set — while detached
(no model, no tick) it appeared to work.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6 import QtWidgets  # noqa: E402

from farever_companion.data import codex  # noqa: E402
from farever_companion.ui.tracker import TrackController  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def _qapp():
    """One offscreen QApplication for the whole module (needle widgets)."""
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class _Settings:
    show_compass = True
    opacity = 1.0
    track_kind = ""
    track_id = ""

    def save(self):
        pass


class _Proc:
    pid = os.getpid()


class _Model:
    """Minimal attached LiveModel stand-in: a player in the scene, no camera
    matrix, no units — enough for the static-waypoint tick path."""

    player_addr = 0x1234
    proc = _Proc()

    def player_xyz(self):
        return (0.0, 0.0, 0.0)

    def view_matrix(self):
        return None

    def camera_yaw(self):
        return 0.0

    def camera_pitch(self):
        return 0.0

    def player_heading(self):
        return 0.0

    def player_profile(self):
        return None

    def units(self):
        return []


def _soulstone_key(poi: dict) -> str:
    """The exact tracker key SoulstoneMixin._soulstone_track_key builds —
    the same shape a minimap click on the soulstone marker passes in."""
    wp = poi.get("world_pos") or {}
    return (f"{float(wp.get('x', 0)):.1f},{float(wp.get('y', 0)):.1f},"
            f"{float(poi.get('z', 0)):.1f}|{poi.get('name') or poi.get('id')}|"
            f"{poi.get('id')}")


def test_attached_soulstone_track_survives_the_tick():
    """Attached + compass on: the synchronous needle tick must resolve the
    soulstone waypoint, so the freshly-set target stays tracked (this is the
    exact path the Codex Soulstones rows and minimap soulstone pins use)."""
    poi = codex.soulstone_pois()[0]
    key = _soulstone_key(poi)
    tr = TrackController(_Settings())
    tr.set_model(_Model())
    try:
        tr.toggle("soulstone", key)
        assert tr.is_tracked("soulstone", key)
        # and the needle has a real target, not the clear-on-missing path
        tgt = tr._target()
        assert tgt is not None and tgt[0] is not None
    finally:
        tr.shutdown()


def test_detached_soulstone_track_persists():
    """Detached (no model): tracking still sticks — the reported 'works when
    the game isn't running' behavior stays intact."""
    poi = codex.soulstone_pois()[0]
    key = _soulstone_key(poi)
    tr = TrackController(_Settings())
    try:
        tr.toggle("soulstone", key)
        assert tr.is_tracked("soulstone", key)
    finally:
        tr.shutdown()


def test_toggle_again_untracks_soulstone():
    """Second click on the same spot clears the target — the Soulstones
    list's track/untrack toggle still behaves in attached mode."""
    poi = codex.soulstone_pois()[0]
    key = _soulstone_key(poi)
    tr = TrackController(_Settings())
    tr.set_model(_Model())
    try:
        tr.toggle("soulstone", key)
        assert tr.is_tracked("soulstone", key)
        tr.toggle("soulstone", key)
        assert not tr.is_tracked("soulstone", key)
    finally:
        tr.shutdown()


def test_dungeon_waypoint_clears_within_15m():
    """A tracked dungeon entrance auto-clears once the player is within 15m
    of it — the needle has delivered you, so the waypoint drops (the player
    model sits at the origin; the tracked spot is 10m away)."""
    key = "10.0,0.0,0.0|Crimson Barracks|dun_1"
    tr = TrackController(_Settings())
    tr.set_model(_Model())
    try:
        tr.track("dungeon", key)
        assert not tr.is_tracked("dungeon", key)
    finally:
        tr.shutdown()


def test_dungeon_waypoint_persists_beyond_15m():
    """Further than 15m the waypoint stays tracked and resolves a target."""
    key = "100.0,0.0,0.0|Crimson Barracks|dun_1"
    tr = TrackController(_Settings())
    tr.set_model(_Model())
    try:
        tr.track("dungeon", key)
        assert tr.is_tracked("dungeon", key)
        tgt = tr._target()
        assert tgt is not None and tgt[0] == 100.0
    finally:
        tr.shutdown()


def test_soulstone_waypoint_clears_within_15m():
    """Same arrival behavior for tracked soulstone summon spots."""
    key = "10.0,5.0,0.0|Mortalkombaal|Soulstone_Demon_7"
    tr = TrackController(_Settings())
    tr.set_model(_Model())
    try:
        tr.track("soulstone", key)
        assert not tr.is_tracked("soulstone", key)
    finally:
        tr.shutdown()


def test_waypoint_boundary_exactly_15m_clears():
    """15m is inclusive: standing exactly at the boundary arrives."""
    key = "15.0,0.0,0.0|Crimson Barracks|dun_1"
    tr = TrackController(_Settings())
    tr.set_model(_Model())
    try:
        tr.track("dungeon", key)
        assert not tr.is_tracked("dungeon", key)
    finally:
        tr.shutdown()


def test_rift_waypoint_clears_within_15m():
    """Rift waypoints (the active/next-due rift spot) clear on arrival too."""
    key = "12.0,0.0,0.0|Rift Devourer|s12.0,0.0"
    tr = TrackController(_Settings())
    tr.set_model(_Model())
    try:
        tr.track("rift", key)
        assert not tr.is_tracked("rift", key)
    finally:
        tr.shutdown()


def test_obelisk_waypoint_clears_within_15m():
    """Obelisk waypoints clear on arrival like the other static POIs."""
    key = "8.0,8.0,0.0|Obelisk of Yore|ob_1"
    tr = TrackController(_Settings())
    tr.set_model(_Model())
    try:
        tr.track("obelisk", key)
        assert not tr.is_tracked("obelisk", key)
    finally:
        tr.shutdown()


def test_obelisk_waypoint_persists_beyond_15m():
    """Further than 15m an obelisk waypoint stays tracked."""
    key = "40.0,0.0,0.0|Obelisk of Yore|ob_1"
    tr = TrackController(_Settings())
    tr.set_model(_Model())
    try:
        tr.track("obelisk", key)
        assert tr.is_tracked("obelisk", key)
    finally:
        tr.shutdown()
