"""test_core_data - consolidated subsystem tests.

Merged without changing any test, from:
  * tests/test_fakemem.py
  * tests/test_game_state.py
  * tests/test_tracker.py
  * tests/test_announcement_reader.py
  * tests/test_name_labels.py
  * tests/test_group_member_zones.py
  * tests/test_group_reader_remap.py

Imports are deduped and the shared `_qapp` fixture kept once;
where two source files bound one name differently, the later
was renamed `<name>__<file>` and only its own file's references
follow. See AGENTS.md: the suite is the spec.
"""
from __future__ import annotations


# --- shared imports ---
import struct
import pytest
from tests import fakemem
from tests.fakemem import FakeProc
from farever_companion.core.game_state import (GameState, Held, TickReads,
                                               boss, in_dungeon, in_instance,
                                               in_rift, published_state,
                                               read_game_state, read_instance,
                                               rift, zone)
import os
from PySide6 import QtWidgets  # noqa: E402
from farever_companion.data import codex  # noqa: E402
from farever_companion.ui.tracker import TrackController  # noqa: E402
from farever_companion.core.announcement_reader import AnnouncementReader
import ast
from pathlib import Path
from farever_companion.constants import OFF_POS
from farever_companion.core.group_reader import GroupMember, GroupReader
from farever_companion.core.model import LiveModel
from farever_companion.core.group_reader import (REMAP_EMPTY_MAX_S,
                                                 REMAP_EMPTY_S, GroupReader,
                                                 GroupSnapshot)

# ========================================================================
# from tests/test_fakemem.py
# ========================================================================

"""The fake memory reader's own semantics.

`tests/fakemem.py` is a test HARNESS, so nothing else in the suite tests it —
until 2026-09-26, when its byte-search was rewritten from "ask `_get` for
`step` bytes at every aligned address" to "read 1 MiB chunks and let
`bytes.find` do it" (the old form cost `test_damage.py` 2m43s on its own). A
harness that is wrong makes every test that leans on it quietly wrong too, and
the two rules that rewrite could plausibly have broken are the ones pinned
here: ALIGNMENT, and matches that straddle a chunk boundary.
"""
@pytest.fixture
def tiny_chunks(monkeypatch):
    """Force the scanner down to small chunks so the boundary logic in `_scan`
    is actually exercised — at the real 1 MiB a test would need a megabyte of
    fixture to reach the same seam."""
    monkeypatch.setattr(fakemem, "_SCAN_CHUNK", 16)
def test_only_aligned_hits_are_reported(tiny_chunks):
    """`find_qword_in` reports 8-byte-aligned addresses, and still does when
    the same needle is also present UNALIGNED — the old per-step walk never
    looked at unaligned addresses at all, so a regression here is silent."""
    p = FakeProc()
    p.add_region(0x1000, 0x1000, writable=True)
    needle = struct.pack("<Q", 0xDEAD)
    p.write(0x1008, needle)          # aligned (0x1008 % 8 == 0)
    p.write(0x1039, needle)          # unaligned, and clear of the aligned copy
    assert 0x1039 % 8 != 0
    hits = p.find_qword_in(0xDEAD, [(0x1000, 0x1000)])
    assert hits == [0x1008]
def test_a_needle_straddling_a_chunk_boundary_is_found_once(monkeypatch):
    """A hit that begins in one chunk and finishes in the next is found, and
    found ONCE.

    The chunk size here (20) is deliberately not a multiple of the 8-byte
    alignment, which is the only way an 8-aligned hit can straddle a seam: with
    a chunk size that IS a multiple of 8, every aligned hit lands wholly
    inside some chunk and the overlap is never exercised.
    """
    monkeypatch.setattr(fakemem, "_SCAN_CHUNK", 20)
    p = FakeProc()
    p.add_region(0x1000, 0x1000, writable=True)
    p.write(0x1010, struct.pack("<Q", 0xBEEF))   # 0x1010..0x1017, seam at 0x1014
    hits = p.find_qword_in(0xBEEF, [(0x1000, 0x1000)])
    assert hits == [0x1010]
    assert len(hits) == len(set(hits))
def test_a_needle_at_the_very_end_of_a_range_is_found(tiny_chunks):
    """The last legal hit begins at `end - L`; one byte later is out of range
    and must not be reported."""
    p = FakeProc()
    p.add_region(0x2000, 0x100, writable=True)
    end = 0x2000 + 0x100
    p.write(end - 8, struct.pack("<Q", 0xF00D))       # last aligned hit
    hits = p.find_qword_in(0xF00D, [(0x2000, 0x100)])
    assert hits == [end - 8]
def test_max_hits_still_caps_the_result(tiny_chunks):
    p = FakeProc()
    p.add_region(0x3000, 0x400, writable=True)
    for i in range(20):
        p.write(0x3000 + i * 8, struct.pack("<Q", 0x77))
    assert len(p.find_qword_in(0x77, [(0x3000, 0x400)], max_hits=5)) == 5
def test_a_read_across_the_gap_between_two_regions_fails():
    """The reason the byte images are kept PER REGION rather than as one flat
    span over the whole address range: the real reader returns None for a read
    that leaves committed memory, and a flat buffer would quietly return zeros
    across the hole instead."""
    p = FakeProc()
    p.add_region(0x4000, 0x100, writable=True)
    p.add_region(0x9000, 0x100, writable=True)
    p.write(0x4080, b"\xAB" * 16)
    assert p.try_read(0x4080, 16) == b"\xAB" * 16        # inside
    assert p.try_read(0x40F0, 32) is None                 # straddles the gap
def test_unwritten_bytes_read_as_zero_inside_a_region():
    """A committed page is zero-filled until written — the property the heap
    builder relies on when it allocates without materialising the memory."""
    p = FakeProc()
    p.add_region(0x5000, 0x100, writable=True)
    assert p.try_read(0x5000, 8) == b"\x00" * 8
    p.put_u64(0x5000, 0x1234)
    assert p.try_read(0x5000, 8) == struct.pack("<Q", 0x1234)
    assert p.try_read(0x5008, 8) == b"\x00" * 8
def test_a_write_after_a_read_is_visible(tiny_chunks):
    """`write` writes THROUGH the cached byte images rather than invalidating
    them, so a scan that already ran cannot go stale."""
    p = FakeProc()
    p.add_region(0x6000, 0x100, writable=True)
    assert p.find_qword_in(0xCAFE, [(0x6000, 0x100)]) == []
    p.put_u64(0x6040, 0xCAFE)
    assert p.find_qword_in(0xCAFE, [(0x6000, 0x100)]) == [0x6040]
def _slow_scan(proc, needle, ranges, step, max_hits):
    """The pre-2026-09-26 scanner, kept here as the ORACLE: ask `_get` for
    `step` bytes at every aligned address. Far too slow to run over a 48 MiB
    region, which is why it was replaced — but over a few KiB it is obviously
    correct, so it can referee the fast one."""
    hits = []
    L = len(needle)
    for base, size in ranges:
        a = base
        if a % step:
            a += step - (a % step)
        end = base + size
        while a + L <= end:
            if proc._get(a, L) == needle:      # noqa: SLF001 (the oracle)
                hits.append(a)
                if len(hits) >= max_hits:
                    return hits
            a += step
    return hits
def test_the_fast_scan_agrees_with_the_old_one_byte_for_byte(monkeypatch):
    """The real evidence that the chunked rewrite is safe: same hits, same
    order, same cap, as the per-step walk it replaced.

    The needles are PLANTED at every `len(needle)`-th offset of an 8-ALIGNED
    region, so hits land on and around chunk seams at every alignment; a
    second, deliberately unaligned region is searched with `step=1` only.

    (Three earlier attempts at this fixture were worse than useless, and each
    passed against a real overlap bug. A pseudo-random blob almost never
    contains an 8-byte pattern, so both scanners returned `[]` and agreed.
    Planting at EVERY offset is worse in a subtle way: each plant overwrites
    the last, leaving a diagonal ramp of `needle[0]` in which the needle never
    occurs. Pairing an UNALIGNED base with an 8-aligned search is worse still
    — every plant then lands on the same residue and nothing can ever match.
    The `assert want` below is what caught that last one, and it is the only
    reason it did.)
    """
    import random
    rng = random.Random(20260926)
    size = 0x1000
    combos = ((b"\xff\xff\xff\xff", 8, 4096),
              (b"\xde\xad\xbe\xef\xde\xad\xbe\xef", 8, 4096),
              (struct.pack("<Q", 0x0102030405060708), 8, 4096),
              (b"\x5a" * 3, 1, 4096),
              (b"\x5a" * 3, 4, 5))
    for base, steps in ((0x8000, (8, 1, 4)), (0x8003, (1,))):
        region = (base, size)
        for chunk in (7, 13, 16, 20, 64, 512, 4096):
            monkeypatch.setattr(fakemem, "_SCAN_CHUNK", chunk)
            for needle, step, cap in combos:
                if step not in steps:
                    continue
                blob = bytearray(rng.randrange(1, 255) for _ in range(size))
                for off in range(0, size - len(needle), len(needle)):
                    blob[off:off + len(needle)] = needle   # non-overlapping
                p = FakeProc()
                p.add_region(base, size, writable=True)
                p.write(base, bytes(blob))
                want = _slow_scan(p, needle, [region], step, cap)
                assert want, "fixture planted nothing — the test is vacuous"
                got = p.find_bytes_in(needle, [region], step, cap)
                assert got == want, (f"base={base:#x} chunk={chunk} "
                                     f"step={step} len={len(needle)}: "
                                     f"{len(got)} vs {len(want)}")

# ========================================================================
# from tests/test_game_state.py
# ========================================================================

"""The per-tick game-state snapshot: read once, answered the same for everyone.

Overlays used to ask the same questions independently - is an instance up, is it
a rift, which boss is this, is a menu covering the HUD - and every one of those
is a walk over game memory. That cost the sum of the walks per frame and let two
rules disagree *inside one tick*, which is how the overlay rules and the cursor
gate drifted apart. Now `TickReads` answers them once per `_combat_tick`,
publishes the result on the model, and every reader goes through this module.

These tests pin the contract: one walk per tick no matter how many readers, the
SAME object handed to all of them, unknowns held at their last proven value, and
a live-read fallback for models that carry no snapshot (duck-typed doubles, or a
read with no tick behind it).
"""
class _Walk:
    """Duck-typed model whose game reads are counted (no live game)."""

    def __init__(self, instance=(True, False), boss=None, rift_value=None,
                 player=0x1000):
        self.walks = 0
        self.rift_calls = 0
        self.player_addr = player
        self.dungeon_boss = boss
        self._instance = instance
        self._rift = rift_value
        self._published = None

    def instance_state(self):
        self.walks += 1
        return self._instance

    def rift_status(self):
        self.rift_calls += 1
        return self._rift

    def publish_state(self, state):
        self._published = state

    @property
    def published_state(self):
        return self._published
class _Legacy:
    """A model with only the boolean pair (no tri-state, no snapshot)."""

    def __init__(self, dungeon=False, wrift=False, raises=False):
        self._d, self._r, self._raises = dungeon, wrift, raises

    def is_in_dungeon(self):
        return self._d

    def is_in_rift(self):
        if self._raises:
            raise RuntimeError("stale scene")
        return self._r
def test_held_keeps_the_last_proven_value_and_can_be_reset():
    h = Held()
    assert h.update(None) is False          # before the first read
    assert h.update(True) is True
    assert h.update(None) is True           # a hiccup doesn't flip it
    h.reset()
    assert h.value is False                 # a new session starts clean
def test_read_instance_prefers_the_models_tri_state():
    assert read_instance(_Walk(instance=(True, False))) == (True, False, True)
    assert read_instance(_Walk(instance=None)) == (False, False, False)
def test_read_instance_accepts_the_boolean_pair():
    assert read_instance(_Legacy(dungeon=True, wrift=False)) == (True, False, True)
    assert read_instance(_Legacy(wrift=True)) == (False, True, True)
def test_read_instance_reports_unknown_when_the_scene_read_fails():
    assert read_instance(_Legacy(raises=True)) == (False, False, False)
    assert read_instance(object()) == (False, False, False)   # no readers at all
def test_game_state_in_instance_is_either_flag():
    assert GameState(in_dungeon=True).in_instance is True
    assert GameState(in_rift=True).in_instance is True
    assert GameState().in_instance is False
def test_a_tick_publishes_the_same_object_to_the_model():
    m = _Walk()
    state = TickReads().read(m, menu=False, menu_gameplay=False)
    assert state is m.published_state
    assert published_state(m) is state
    assert state.tick == 1
    assert state.in_dungeon is True and state.in_rift is False
def test_the_tick_counter_advances_and_the_snapshot_changes():
    m = _Walk()
    reads = TickReads()
    first = reads.read(m, menu=False, menu_gameplay=False)
    second = reads.read(m, menu=False, menu_gameplay=False)
    assert (first.tick, second.tick) == (1, 2)
    assert second is not first
    assert published_state(m) is second
def test_unknown_flags_are_held_across_a_failed_tick():
    m = _Walk(instance=(True, False))
    reads = TickReads()
    reads.read(m, menu=False, menu_gameplay=False)
    m._instance = None                           # the scene read fails
    state = reads.read(m, menu=True, menu_gameplay=True)
    assert state.instance_known is False
    assert state.in_dungeon is True              # held, not flipped
    assert state.in_rift is False
    # and the menu gate is held by the same rule: this tick says a menu is up
    assert state.held_menu is True and state.held_menu_gameplay is True
def test_an_unreadable_menu_is_held_rather_than_taken_as_clear():
    m = _Walk()
    reads = TickReads()
    reads.read(m, menu=True, menu_gameplay=True)
    state = reads.read(m, menu=None, menu_gameplay=None)     # couldn't read it
    assert state.menu is None                                # the raw read is honest
    assert state.held_menu is True and state.held_menu_gameplay is True
def test_the_snapshot_is_read_only():
    state = GameState(in_dungeon=True)
    with pytest.raises(Exception):
        state.in_dungeon = False          # frozen: no overlay can rewrite a tick
def test_every_reader_shares_one_instance_read_per_tick():
    m = _Walk(instance=(False, False))
    reads = TickReads()
    reads.read(m, menu=False, menu_gameplay=False)
    assert m.walks == 1

    # a whole tick's worth of overlays and rules, all asking the same question
    for _ in range(6):
        assert in_instance(m) is False
        assert in_dungeon(m) is False
        assert in_rift(m) is False
    assert m.walks == 1                      # ...and none of them re-derived it

    reads.read(m, menu=False, menu_gameplay=False)
    assert m.walks == 2                      # the next tick re-reads exactly once
def test_the_rift_schedule_is_read_once_per_tick():
    """`rift_status()` is the most expensive read of the set (rift state machine
    plus the announcements scan) and every overlay wants it."""
    m = _Walk(rift_value="RIFT")
    reads = TickReads()
    reads.read(m, menu=False, menu_gameplay=False)
    for _ in range(5):
        assert rift(m) == "RIFT"
    assert m.rift_calls == 1
    reads.read(m, menu=False, menu_gameplay=False)
    assert m.rift_calls == 2
def test_readers_answer_from_the_snapshot_even_if_the_live_read_dies():
    m = _Walk(instance=(True, True), boss="Soulstone_Z1_1")
    TickReads().read(m, menu=False, menu_gameplay=False)

    def _boom():
        raise RuntimeError("stale scene")

    m.instance_state = _boom
    assert in_instance(m) is True
    assert in_dungeon(m) is True
    assert in_rift(m) is True
    assert boss(m) == "Soulstone_Z1_1"
    assert m.walks == 1
def test_boss_and_zone_come_from_the_tick():
    m = _Walk(boss="DemonSuperElite")
    state = TickReads().read(m, menu=False, menu_gameplay=False,
                             map_id="POI_Test_1")
    assert (boss(m), zone(m)) == ("DemonSuperElite", "POI_Test_1")
    assert state.map_id == "POI_Test_1"
def test_without_a_snapshot_the_accessors_use_the_models_own_read():
    assert in_instance(_Legacy(wrift=True)) is True
    assert in_dungeon(_Legacy(dungeon=True)) is True
    assert in_rift(_Legacy(wrift=True)) is True
    assert in_instance(_Legacy()) is False
    assert in_dungeon(object()) is False          # nothing to read: not a crash
def test_plain_flag_doubles_still_answer():
    """Some overlay test doubles set bare `is_dungeon`/`is_rift` attributes."""
    class _Flags:
        is_dungeon = False
        is_rift = True

    assert in_instance(_Flags()) is True
def test_accessors_tolerate_a_missing_model():
    assert in_instance(None) is False
    assert in_dungeon(None) is False
    assert in_rift(None) is False
    assert boss(None) is None
    assert zone(None) is None
    assert rift(None) is None
    assert published_state(None) is None
def test_read_game_state_falls_back_to_its_own_reads():
    """No tick to hand in (a diagnostic, or a read right after attach): the
    builder answers with the model's live reads instead of inventing a tick."""
    m = _Walk(instance=(False, True), rift_value="RIFT")
    state = read_game_state(m)
    assert (state.tick, state.in_rift, state.rift) == (0, True, "RIFT")
    assert state.instance_known is True

# ========================================================================
# from tests/test_tracker.py
# ========================================================================

"""TrackController waypoint tracking tests (headless Qt).

Regression: soulstone summon spots are fixed waypoints (like dungeons, rifts
and obelisks), so with a model attached the compass-needle tick must resolve
them instead of clearing the freshly-set target. Before the fix the
`_target()` waypoint-kind list omitted 'soulstone', so an attached tick
returned None and wiped the tracking the moment it was set — while detached
(no model, no tick) it appeared to work.
"""
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
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

# ========================================================================
# from tests/test_announcement_reader.py
# ========================================================================

"""The chat reader walks the history array by NAME, in two hops (2026-10-01).

`announcement_reader.py` reads in-game rift announcements out of the chat
subsystem's message history. For most of its life it read them off
`st.player.ChatClient.history`, using a calibrated offset as a fallback when
reflection could not resolve the field.

That fallback is the whole problem. The 2026-10-01 live scan found
`st.player.ChatClient` is exactly {player, chat} - the slot the calibrated
constant names is `chat`, and no `history` field exists on that class at all.
So the reader dereferenced the chat subsystem and walked it as if it were a
message array: a pointer to the wrong KIND of thing, which returns plausible
nonsense rather than an error, and announcements went dead without a word in
the log.

The messages are one object down, on the chat subsystem's own class. Both hops
are resolved by name here, so there is no offset left to drift.
"""
class FakeHl:
    """Just enough HL reflection for the reader's two hops."""

    def __init__(self, layout, objects, arrays=None):
        # instance address -> (class name, {field: offset})
        self.layout = layout
        self.objects = objects          # address -> the value ptr() should return
        self.arrays = arrays or {}      # array object address -> [msg ptr, ...]
        self.asked: list[tuple[str, str]] = []

    def ptr(self, addr):
        return self.objects.get(addr)

    def class_of(self, addr):
        cname, _ = self.layout.get(addr, (None, {}))
        return cname

    def field_offset(self, type_ptr, name):
        for _addr, (_cname, fields) in self.layout.items():
            if fields.get("__type__") == type_ptr and name in fields:
                self.asked.append((f"type@{type_ptr:#x}", name))
                return fields[name]
        return None

    def array(self, arrobj, max_elems=1024):
        return self.arrays.get(arrobj, [])

    def hl_string(self, strobj):
        return None
TYPE_CLIENT = 0x10
TYPE_CHAT = 0x20
TYPE_PLAYER = 0x30
TYPE_HERO = 0x40
HERO, PLAYER, CLIENT, CHAT, HISTORY, MSG = (
    0x1000, 0x2000, 0x3000, 0x4000, 0x5000, 0x6000)
LAYOUT = {
    HERO:   ("ent.Hero", {"__type__": TYPE_HERO, "ownerPlayer": 0x4E0}),
    PLAYER: ("st.Player", {"__type__": TYPE_PLAYER, "chatClient": 0x168}),
    # The 2026-10-01 finding: {player, chat}, and NO history.
    CLIENT: ("st.player.ChatClient", {"__type__": TYPE_CLIENT,
                                     "player": 0xB0, "chat": 0xB8}),
    CHAT:   ("st.player.$ChatClient", {"__type__": TYPE_CHAT, "history": 0x40}),
}
class FakeProc__announcement_reader:
    """A process whose every slot reads 0 - i.e. no pointers to follow.

    Real, because the message scan is wrapped in a bare `except: pass`: a
    proc object without `u64` would raise on the first slot and the message
    would still be recorded as seen, so the test would pass without the scan
    ever having run.
    """

    def u64(self, addr):
        return 0
def _hl(chat_up=True):
    objects = {
        HERO: TYPE_HERO,
        HERO + 0x4E0: PLAYER,
        PLAYER: TYPE_PLAYER,
        PLAYER + 0x168: CLIENT,
        CLIENT: TYPE_CLIENT,
    }
    if chat_up:
        objects[CLIENT + 0xB8] = CHAT
        objects[CHAT] = TYPE_CHAT
        objects[CHAT + 0x40] = HISTORY
    hl = FakeHl(LAYOUT, objects, arrays={HISTORY: [MSG]})
    reader = AnnouncementReader(proc=FakeProc__announcement_reader(), hl=hl, locator=object())
    reader.locator = type("L", (), {"locate": staticmethod(lambda: HERO)})()
    return reader, hl
def test_history_is_read_off_the_chat_object_not_the_client():
    """The regression: `.chat` and `.history` are different objects.

    Reading the history off the client resolved nothing and fell through to a
    calibrated constant that now points at `chat` - so the reader walked the
    chat subsystem as a message array. Both hops have to be asked for BY NAME,
    on the object each one actually belongs to.
    """
    reader, hl = _hl()
    reader.update()

    asked = [name for _tp, name in hl.asked]
    assert "history" in asked, "the history field was never resolved by name"
    # `history` must be asked of the CHAT object's class, never the client's.
    types_asked = [tp for tp, name in hl.asked if name == "history"]
    assert types_asked == [f"type@{TYPE_CHAT:#x}"], (
        f"history was resolved on the wrong class: {types_asked} "
        f"(client={f'type@{TYPE_CLIENT:#x}'}) - st.player.ChatClient has no "
        f"history field, which is the whole finding")
def test_a_null_chat_object_is_quiet_rather_than_a_drift_warning():
    """Chat not being up is the common case, not a fault.

    It used to log "FALLBACK constant - slot may have drifted" - a warning
    that was permanently true once the field moved, which is precisely how a
    real, readable announcement would have gone unnoticed.
    """
    reader, hl = _hl(chat_up=False)
    assert reader.update() is None
    assert reader.last_announcement is None
def test_the_announcement_chain_is_walked_end_to_end():
    """A history array with one message reaches the brute-force scan.

    The parse of the message itself is unchanged by this fix; what has to hold
    is that a populated history still arrives at it.
    """
    reader, hl = _hl()
    reader.update()
    # The reader reached HISTORY, asked it for its array, and handed the one
    # message to the brute-force scan - which records it as seen. It is not an
    # announcement (no rift string in an all-zero message), so nothing is
    # claimed: the point is that a populated history still ARRIVES.
    assert HISTORY in hl.arrays
    assert MSG in reader.seen_ptrs
    assert reader.last_announcement is None

# ========================================================================
# from tests/test_name_labels.py
# ========================================================================

"""Label contract: an id the sheets can name is never printed as words, and
`humanize` is only called where no resolver has anything to say.

The bug this pins was reported twice, from two directions. First as a wrong
name: the party page listed a passive's buff as "Halos Totem Passive Aoe Status"
while the damage log called the same skill "Power of the Tides". Then as a raw
id: a derived row rendered as `DM_Multispin`. Both are the same mistake — a
surface spelling an id out for itself instead of asking the resolver — and both
are invisible until someone reads that exact row on that exact page, because
`humanize` always returns *something* plausible ("Elixir Of Dexterity Z 2" for
an id the sheet calls "Elixir of Dexterity").

So this file holds the audit still rather than repeating it:

* `_HUMANIZE_SITES` is the complete list of `humanize` calls in the shipped
  package, each with the reason the id domain under it has no better name. The
  guard fails BOTH ways — a new call site that is not classified here, and a
  classified site that stopped humanizing — so the list cannot rot into a
  comment. A new site is the moment to ask whether a resolver should answer
  instead; the reasons below record where the answer was "no, and why".
* The resolver tests assert the invariant that makes a humanizing label site
  wrong in the first place: no resolver hands back an id that `humanize` could
  have spelled out. A resolver answer equal to the raw id is only allowed when
  the id is *already* readable ("Attack"), which is exactly when humanizing
  would be a no-op.
"""
ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "farever_companion"
_HUMANIZE_SITES = {
    # A zone id the zone sheet does not carry. `zone_name` already humanizes
    # its own miss, so these two are the redundant-but-harmless shape; the one
    # in `resolve_id` is the genuine last resort after three zone_name tries.
    "core/rift_tracker.py::RiftTracker._resolve_rift_info.resolve_id":
        "zone id, after zone_name missed a prefix-stripped try",
    "core/rift_tracker.py::rift_summary":
        "zone id; zone_name already answered or humanized",
    "core/rift_tracker.py::upcoming_rifts":
        "zone id; zone_name already answered or humanized",
    # Craft-side naming walks its own data (compiled DB -> raw items sheet),
    # not the names manifest, and humanizes only what neither names.
    "data/items/craft.py::_item_name":
        "item id; compiled DB then raw sheet first, humanize last",
    "data/items/craft.py::_loot_name":
        "loot-table id; a hand map then humanize, no sheet names these",
    "data/items/craft.py::jobs":
        "job toolType token ('Blacksmith'), not an id any sheet names",
    # Infusion-pattern ids: the row's own name is preferred, and the humanized
    # id is only what an unnamed pattern falls back to.
    "data/items/sources.py::heroic_infusions":
        "InfusionPattern id; the pattern's own name first",
    # Skill prose: sheet tokens and [bracket] references, which try the stat
    # table and (for namespaced ids) `skill_name` before humanizing.
    "data/skills.py::stat_label":
        "sheet attribute token ('CritChance'), not an id any resolver covers",
    "data/skills.py::_bracket_label":
        "prose token; tries the stat table then skill_name first",
    # Gathering-node ids. Not items, not units: the sheets carry no name for
    # them, which is why the source row must be humanized.
    "ui/pages/items/drops.py::_friendly_source_name":
        "gathering-node id ('R2Plant2_Small'); no sheet names a node",
    "ui/pages/items/drops.py::_node_type_label":
        "gathering-node id collapsed to its base type; no sheet name",
    # Item type tokens: the three slots that need a friendlier word are mapped,
    # and the rest read acceptably as words.
    "ui/pages/items/support.py::slot_label":
        "item type token ('GearFinger'); _SLOT_LABELS first",
    # The LAST branch of the buff chain, after status_name, skill_name and
    # item_name have each declined. Unreachable while status_name answers, and
    # kept as the honest floor if that ever changes.
    "ui/pages/party_rows.py::_buff_label":
        "buff id; the last branch after status_name/skill_name/item_name",
}
def _humanize_sites() -> dict[str, int]:
    """Every `humanize(...)` call in the package -> its line number.

    Keyed by `path::qualname` (relative to the package, POSIX separators) so
    the allowlist above reads like a location. `data/names.py` is skipped: it
    defines humanize, so its own calls are the resolver deciding.
    """
    out: dict[str, int] = {}
    for path in sorted(PKG.rglob("*.py")):
        rel = path.relative_to(PKG).as_posix()
        if rel == "data/names.py" or "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        stack: list[str] = []

        class Visitor(ast.NodeVisitor):
            def generic_visit(self, node: ast.AST) -> None:
                named = isinstance(node, (ast.FunctionDef,
                                          ast.AsyncFunctionDef, ast.ClassDef))
                if named:
                    stack.append(node.name)          # type: ignore[attr-defined]
                super().generic_visit(node)
                if named:
                    stack.pop()

            def visit_Call(self, node: ast.Call) -> None:
                fn = node.func
                name = getattr(fn, "attr", None) or getattr(fn, "id", None)
                if name == "humanize":
                    qual = ".".join(stack) or "<module>"
                    out[f"{rel}::{qual}"] = node.lineno
                self.generic_visit(node)

        Visitor().visit(tree)
    return out
def test_humanize_is_called_only_where_a_resolver_has_nothing_to_say():
    """The audit, held still: every call site is classified, and the list has
    no stale entries.

    A new call site means someone decided an id should be spelled out by
    hand — the exact decision that produced "Halos Totem Passive Aoe Status"
    and `DM_Multispin`. Either route it through a resolver, or add it here with
    the reason its id domain has no better name.
    """
    found = _humanize_sites()
    unclassified = sorted(set(found) - set(_HUMANIZE_SITES))
    stale = sorted(set(_HUMANIZE_SITES) - set(found))

    assert not unclassified, (
        "humanize() called at a site with no recorded reason — use a resolver "
        "(skill_name/status_name/item_name/item_label/unit_name/zone_name) or "
        "classify it in _HUMANIZE_SITES with the id domain that has no name:\n"
        + "\n".join(f"  {k} (line {found[k]})" for k in unclassified))
    assert not stale, (
        "…_HUMANIZE_SITES names sites that no longer call humanize — drop "
        "them so the list keeps meaning something:\n"
        + "\n".join(f"  {k}" for k in stale))
def test_no_resolver_hands_back_an_id_humanize_could_have_spelled_out():
    """The invariant that makes a humanizing label site a bug.

    Each resolver's whole job is to do at least as well as `humanize`. An
    answer equal to the raw id is therefore only acceptable when the id is
    already readable, i.e. when humanizing it would change nothing.
    """
    from farever_companion.data import names

    resolvers = {
        "skill_name": names.skill_name,
        "status_name": names.status_name,
        "item_label": names.item_label,
        "unit_name": names.unit_name,
        "zone_name": names.zone_name,
    }
    domains = {
        "skill_name": list(names._skills()),
        "status_name": list(names._skills()),
        "item_label": list(names._items()),
        "unit_name": list(names._units()),
        "zone_name": list(names._all_zone_names()),
    }
    bad: list[str] = []
    for resolver_name, ids in domains.items():
        resolve = resolvers[resolver_name]
        for ident in ids:
            got = resolve(ident)
            if got == ident and names.humanize(ident) != ident:
                bad.append(f"{resolver_name}({ident!r}) -> {got!r} "
                           f"(humanize: {names.humanize(ident)!r})")
    assert not bad, (
        "a resolver answered with the raw id where humanize had readable "
        "words — the id is missing from the sheet the resolver reads:\n  "
        + "\n  ".join(bad[:20]))
def test_status_name_names_an_item_shaped_kind_from_the_item_sheet():
    """A buff the game reports as the granting ITEM's own id.

    `status_name` resolved `<Item>_Status` rows, sheet-named skill rows and a
    derived row's parent skill — then humanized, which is where an id that is
    an item's own id fell out as words: 'Elixir Of Dexterity Z 2' for an id the
    item sheet calls 'Elixir of Dexterity'. The item sheet is consulted before
    the humanize floor, so every route that already resolved keeps its answer.
    """
    from farever_companion.data import names

    assert names.item_name("ElixirOfDexterity_Z2") == "Elixir of Dexterity"
    assert names.status_name("ElixirOfDexterity_Z2") == "Elixir of Dexterity"
    # a weapon's own id, where the humanized form is a different word entirely
    assert names.status_name("DM_Multispin") == "Twin Pillars of Justice"
    assert names.status_name("BadgeOfGlory") == "Medal of Glory"
    # and the routes that already resolved are untouched
    assert names.status_name("Whetstone_Status") == "Whetstone"
    assert names.status_name("Halos_Totem_Passive_Aoe_Status") == \
        "Power of the Tides"
    assert names.status_name("ItemStatus", "ElixirOfDexterity_Z2") == \
        "Elixir of Dexterity"
def test_status_name_still_humanizes_an_id_nobody_names():
    """The fallback floor is a contract, not a leftover: an id the sheets and
    the skill tree both decline still reads as words rather than as an id."""
    from farever_companion.data import names

    assert "NotARealStatus_XYZ" not in names._items()
    assert names.status_name("NotARealStatus_XYZ") == "Not A Real Status XYZ"
    assert names.status_name(None) is None
def test_item_label_never_returns_a_raw_id():
    """`item_label` is the party slot's resolver: a piece the sheets do not
    carry reads as words, where `item_name` would hand the id straight back."""
    from farever_companion.data import names

    # the sheets carry it: the real name
    assert names.item_label("DM_Multispin") == "Twin Pillars of Justice"
    assert names.item_label("ElixirOfDexterity_Z2") == "Elixir of Dexterity"
    # they do not: humanized words, never the id
    assert names.item_label("CursedBlade_Z2") == "Cursed Blade Z 2"
    assert names.item_label("") == ""
    assert names.item_label(None) is None
@pytest.mark.parametrize("kind", [
    "DS_Bladeleaf_Skill2_Status",
    "Daggers_Demondash_Passive_Status",
    "Trinket_Demon_Status",
    "GA_Demon_Combo_Status",
    "Warrior_Hemorrhage_Status",
    "Axe_Boomerang_Skill_Passive_Status",
])
def test_the_buff_kinds_captured_from_the_live_client_name_themselves(kind: str):
    """The status kinds actually found in the captured live data.

    These five-plus one are the whole observed population, taken from the
    on-disk progress captures, so they are the regression that matters: whatever
    the resolver does next, these must keep reading as the game names them.
    """
    from farever_companion.data import names

    nm = names.status_name(kind)
    assert nm and nm != kind, f"{kind} resolved to itself"
    assert "_" not in nm, f"{kind} resolved to an id-shaped label {nm!r}"
    assert nm == names.status_name(kind), "resolver is not stable"

# ========================================================================
# from tests/test_group_member_zones.py
# ========================================================================

"""Where each party member is, read off their hero's world position.

The Entity HUD's PLAYERS section could only ever list members present in the
LOCAL scene (`nearest_group_members` filters `model.units()`), so a member
standing in another zone was invisible in the HUD entirely — the section just
showed fewer rows, with nothing to say why. The party roster
(`core/group_reader.py`) lists members at any distance, and each member's
`ent.Hero` carries a readable world position, so their zone resolves with the
same anchor lookup the map already uses.

What is pinned here:
  - `GroupReader._read_pos` reads OFF_POS (f64[3]) and rejects the garbage a
    freed/never-initialised object returns, so a bad read can never be
    mistaken for a real location;
  - `LiveModel.group_member_zones` names those positions, drops members with
    no usable position, and drops names that are still raw technical ids
    ("Z1_Enripit_Falls") — the HUD must never show one;
  - it reads the roster CACHE, never a live decode, because the decode is a
    heap sweep measured in seconds and the HUD runs on a timer.
"""
class _PosProc(FakeProc):
    """A FakeProc with one hero object whose OFF_POS slot is `xyz`."""

    def __init__(self, hero: int, xyz):
        super().__init__()
        self.hero = hero
        self.xyz = xyz

    def try_read(self, addr, n):                        # noqa: D102
        if addr == self.hero + OFF_POS and n >= 24:
            return struct.pack("<ddd", *self.xyz)
        return super().try_read(addr, n)
def test_read_pos_takes_the_games_off_pos_slot():
    """The read is the ent.GameObject position the scene scan uses."""
    hero = 0x4000
    r = GroupReader(_PosProc(hero, (100.0, 200.0, 30.0)), None)
    assert r._read_pos(hero) == (100.0, 200.0, 30.0)
def test_read_pos_asks_for_exactly_the_three_doubles():
    """OFF_POS is f64[3] - a short read would silently misalign the fields."""
    seen = {}

    class _P(FakeProc):
        def try_read(self, addr, n):
            seen["n"] = n
            return struct.pack("<ddd", 1.0, 2.0, 3.0)

    assert GroupReader(_P(), None)._read_pos(0x4000) == (1.0, 2.0, 3.0)
    assert seen["n"] == 24
def test_read_pos_rejects_a_freed_object_instead_of_reporting_junk():
    """A wild value must not become somebody's location."""
    r = GroupReader(_PosProc(0x4000, (1e12, 0.0, 0.0)), None)
    assert r._read_pos(0x4000) == (0.0, 0.0, 0.0)
    r = GroupReader(_PosProc(0x4000, (float("nan"), 1.0, 1.0)), None)
    assert r._read_pos(0x4000) == (0.0, 0.0, 0.0)
def test_read_pos_of_nothing_is_the_origin():
    assert GroupReader(FakeProc(), None)._read_pos(0) == (0.0, 0.0, 0.0)
def test_has_pos_separates_a_real_position_from_an_unread_one():
    """`xyz` alone cannot: (0,0,0) is both a real origin and a failed read."""
    assert GroupMember(hero=0x10, xyz=(5.0, 0.0, 0.0)).has_pos
    assert not GroupMember(hero=0x10, xyz=(0.0, 0.0, 0.0)).has_pos
    assert not GroupMember(hero=0, xyz=(5.0, 5.0, 5.0)).has_pos
class _Model__group_member_zones(LiveModel):
    """A LiveModel with no process: only the zone naming is under test."""

    def __init__(self, roster):                          # noqa: D107
        self._roster = roster
        self.decode_calls = 0

    def cached_group_roster(self):
        return self._roster

    def group_roster(self):
        self.decode_calls += 1
        return self._roster
def _roster(*members):
    class _Snap:
        pass
    s = _Snap()
    s.members = list(members)
    return s
def test_group_member_zones_names_each_member(monkeypatch):
    monkeypatch.setattr("farever_companion.geo.zones.resolve_zone",
                        lambda x, y, z: "Z1_Talitha" if x > 0 else "Z2_Krisomal")
    monkeypatch.setattr("farever_companion.data.names.zone_display",
                        lambda zid: {"Z1_Talitha": "Talitha Falls",
                                     "Z2_Krisomal": "Krisomal Vale"}[zid])
    m = _Model__group_member_zones(_roster(
        GroupMember(hero=0x10, name="Ann", xyz=(5.0, 0.0, 0.0)),
        GroupMember(hero=0x20, name="Bo", xyz=(-5.0, 0.0, 0.0)),
    ))
    assert m.group_member_zones() == {0x10: "Talitha Falls",
                                      0x20: "Krisomal Vale"}
def test_group_member_zones_drops_an_id_the_zone_sheet_does_not_know(monkeypatch):
    """A zone id with no real name must not surface as a technical id.

    This is why the consumer uses `names.zone_display` and not `zone_name`:
    the latter humanizes an unknown id into "Z 9 Unknownlands" (humanize
    splits the letter/digit boundary), so a "Z\\d+" guard on the OUTPUT
    silently never matches.
    """
    monkeypatch.setattr("farever_companion.geo.zones.resolve_zone",
                        lambda x, y, z: "Z9_Unknownlands")
    monkeypatch.setattr("farever_companion.data.names.zone_display",
                        lambda zid: None)
    m = _Model__group_member_zones(_roster(GroupMember(hero=0x10, name="Ann", xyz=(5.0, 0.0, 0.0))))
    assert m.group_member_zones() == {}
def test_group_member_zones_ignores_the_humanize_fallback(monkeypatch):
    """The whole point: an unresolved id must not reach the HUD even though
    `zone_name` would happily invent a label for it."""
    monkeypatch.setattr("farever_companion.geo.zones.resolve_zone",
                        lambda x, y, z: "Z9_Unknownlands")
    from farever_companion.data import names as gnames
    # zone_name WOULD return a string here...
    assert gnames.zone_name("Z9_Unknownlands")
    # ...and zone_display correctly reports that it resolved to nothing.
    assert gnames.zone_display("Z9_Unknownlands") is None
def test_group_member_zones_skips_a_member_with_no_position(monkeypatch):
    """An unread hero is absent, not guessed at — the row just doesn't show."""
    monkeypatch.setattr("farever_companion.geo.zones.resolve_zone",
                        lambda x, y, z: "Z1_Talitha")
    monkeypatch.setattr("farever_companion.data.names.zone_display",
                        lambda zid: "Talitha Falls")
    m = _Model__group_member_zones(_roster(
        GroupMember(hero=0x10, name="Ann", xyz=(5.0, 0.0, 0.0)),
        GroupMember(hero=0, name="Ghost", xyz=(5.0, 0.0, 0.0)),
        GroupMember(hero=0x30, name="Unread", xyz=(0.0, 0.0, 0.0)),
    ))
    assert set(m.group_member_zones()) == {0x10}
def test_group_member_zones_of_no_roster_is_empty():
    assert _Model__group_member_zones(None).group_member_zones() == {}
    assert _Model__group_member_zones(_roster()).group_member_zones() == {}
def test_group_member_zones_never_triggers_a_live_decode():
    """The decode is a heap sweep measured in SECONDS; the HUD runs on a
    timer, so this must read the cache only (same rule as the Party page)."""
    m = _Model__group_member_zones(_roster(GroupMember(hero=0x10, name="Ann", xyz=(1.0, 1.0, 1.0))))
    m.group_member_zones()
    assert m.decode_calls == 0
def test_group_member_zones_survives_a_raising_zone_resolver(monkeypatch):
    def _boom(x, y, z):
        raise ValueError("zone sheet not loaded")
    monkeypatch.setattr("farever_companion.geo.zones.resolve_zone", _boom)
    m = _Model__group_member_zones(_roster(GroupMember(hero=0x10, name="Ann", xyz=(1.0, 1.0, 1.0))))
    assert m.group_member_zones() == {}
if __name__ == "__main__":       # pragma: no cover
    pytest.main([__file__])

# ========================================================================
# from tests/test_group_reader_remap.py
# ========================================================================

"""How often the party-roster reader re-sweeps the heap while nothing decodes.

`GroupReader._scan_snapshots` remaps (a bounded but slow heap sweep, measured
~2.4 s live) when a scan finds no group at all — which is the normal state while
you are SOLO, so the remap can never succeed and repeating it every
REMAP_EMPTY_S was pure cost for every poller (the Party page's 2 s tick, the DPS
meter's roster line, the tracker's seed).

The rule pinned here: a remap that decodes nothing doubles the wait before the
next one, up to REMAP_EMPTY_MAX_S, and any decode at all resets it. Measured
live 2026-09-15 in a solo session: 6.3 s for the cold decode, then 2.4 s per
remap, every 6 s.
"""
def _reader():
    """A reader whose scan is stubbed: no memory, just the remap bookkeeping.

    `_group_type` / `_scan_ranges` are set so `_scan_snapshots` goes straight to
    the scan, and `refresh_ranges` is replaced by a counter — the sweep itself is
    covered by the live reads, this file is about WHEN it runs.
    """
    r = GroupReader(FakeProc(), None)
    r._group_type = 0x1000
    r._scan_ranges = [(0x2000, 0x3000)]
    remaps: list[int] = []
    r.refresh_ranges = lambda: remaps.append(1)
    r.decode = lambda addr: GroupSnapshot(group=addr)
    return r, remaps
def _force_due(r: GroupReader) -> None:
    """Make the remap gate open (it is a wall-clock interval)."""
    r._last_remap = 0.0
def test_an_empty_scan_backs_off_instead_of_rescanning_forever():
    r, remaps = _reader()
    r._instances = lambda: []
    assert r._remap_wait == REMAP_EMPTY_S

    waits = []
    for _ in range(5):
        _force_due(r)                       # as if the interval had elapsed
        assert r._scan_snapshots() == []
        waits.append(r._remap_wait)

    # every empty remap doubled the wait: 12, 24, 48, 96, capped at 120
    assert waits == [12.0, 24.0, 48.0, 96.0, REMAP_EMPTY_MAX_S]
    assert len(remaps) == 5                 # …and each one did sweep
def test_the_backoff_holds_the_gate_shut_until_it_elapses():
    """With the wait grown, a scan that happens sooner must not sweep at all."""
    r, remaps = _reader()
    r._instances = lambda: []
    _force_due(r)
    r._scan_snapshots()
    assert r._remap_wait == 12.0
    r._last_remap = __import__("time").monotonic()   # just swept
    assert r._scan_snapshots() == []
    assert len(remaps) == 1                 # no second sweep
def test_a_decoded_group_resets_the_backoff_and_never_sweeps():
    """The moment something decodes, the interval is back to its minimum — and
    a scan that already found a group doesn't remap at all."""
    r, remaps = _reader()
    r._instances = lambda: []
    for _ in range(3):
        _force_due(r)
        r._scan_snapshots()
    assert r._remap_wait == 48.0

    r._instances = lambda: [0x4000]
    _force_due(r)
    out = r._scan_snapshots()
    assert [s.group for s in out] == [0x4000]
    assert r._remap_wait == REMAP_EMPTY_S   # reset
    assert len(remaps) == 3                 # …and no sweep for a hit
