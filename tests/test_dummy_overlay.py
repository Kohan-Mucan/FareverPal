"""Offscreen tests for the standalone Test Dummy HUD.

The window is the Top DPS meter stripped to a dummy parse, so these tests pin
what makes it its own surface: it renders the DUMMY session (whatever else is
running), it gives every dummy in a cluster its own health bar, it takes a
target from a click on one of those rows, it measures the test against the last
one, and it carries the test's own end signal. Its registry entry is pinned
here; when it EXISTS (Rule F) and that the meter keeps no bench (Rule E) is
tested through the real manager in tests/test_menu_behaviour_matrix.py.
"""
from __future__ import annotations

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6 import QtWidgets  # noqa: E402

from farever_companion.core.damage_events import DamageEvent  # noqa: E402
from farever_companion.ui.overlays.dummy_overlay import (  # noqa: E402
    DummyOverlay,
)
from tests.dps_fakes import (  # noqa: E402
    PA, ALLY, FOE, BOSS, _FakeModel as _Model, _hero, _foe, training_dummy,
)
from tests import dummy_labels as L  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def _qapp():
    """One offscreen QApplication for the module (a widget needs one)."""
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


from tests.qt_helpers import click as _click, destroy  # noqa: E402


class _Settings:
    opacity = 1.0
    geometry = {}
    dps_dummy_range = 20
    dps_top_count = 8
    dps_pin_me = True
    dps_solo_only = True
    dps_skill_cap = 8
    open_overlay_dummy = True

    def save(self):
        pass


@pytest.fixture()
def ov():
    """Fresh Test Dummy HUD on a fresh fake model/settings (shown for layout)."""
    o = DummyOverlay(_Model(names={PA: "Me", ALLY: "Brit"}), _Settings())
    o.show()
    QtWidgets.QApplication.processEvents()
    try:
        yield o
    finally:
        destroy(o)


def _tick(ov):
    """Refresh the HUD and drain Qt so visibility is truthful."""
    ov._tick()
    QtWidgets.QApplication.processEvents()


def _hit(m, addr, amount):
    m.damage.push(DamageEvent(amount=amount, skill="Sword_Base_Attack",
                              source_addr=PA, target_addr=addr, t=time.time()))


def test_hud_renders_the_dummy_parse_and_its_target(ov):
    """The bare "Dummy" unit the open-world hubs spawn (the one the recognizer
    used to miss) drives the whole HUD: total, DPS, the pinned target's own
    health bar and its row of players."""
    m = ov.model
    m._units = [_hero(PA), training_dummy(FOE, uid="Dummy", hp=100000.0)]
    _hit(m, FOE, 400.0)
    _tick(ov)

    assert ov.tracker.in_dummy_fight is True
    assert ov.dmg_lbl.text() == L.dmg(400.0)
    assert ov.dps_lbl.text().endswith("/s")
    assert ov.target_lbl.text() == L.target_label("Dummy")
    assert "100,000" in ov.target_hp_lbl.text()
    assert ov.target_bar.value() == 100
    # the total lives on dmg_lbl above; the box intentionally builds no header
    # (a hidden one is still a ghost widget), so there is no tag to repeat it
    assert ov.top_box.header is None
    assert set(ov._row_widgets) == {"Me (You)"}
    # one dummy is not a split: the target bar already says everything
    assert ov.split_box.isVisible() is False


def test_the_split_lists_every_dummy_in_the_yard(ov):
    """The split shows the SCENE's whole dummy cluster, not only the dummy the
    decoded hit named — so a yard of three is visibly three rows, with the one
    that took the damage pinned/highlighted."""
    m = ov.model
    m._units = [_hero(PA),
                training_dummy(FOE, uid="Dummy", hp=100000.0, x=2.0, y=1.0, z=1.0),
                training_dummy(BOSS, uid="Dummy", hp=100000.0, x=3.0, y=1.0, z=1.0),
                training_dummy(0xB0B, uid="Dummy", hp=100000.0, x=4.0, y=1.0, z=1.0)]
    _hit(m, FOE, 500.0)                      # only ONE dummy is hit
    _tick(ov)
    assert ov.split_box.isVisible() is True
    rows = [r for r in ov.split_box._rows if r.isVisible()]
    assert len(rows) == 3
    assert rows[0]._pinned is True           # the damage leader leads + is pinned


def test_the_hud_counts_down_to_the_tests_auto_rearm(ov):
    """A dummy keeps the client "in combat" at any range inside the yard, so a
    test that stops landing hits ends itself after the Auto Re-arm setting — and
    the board says how long, rather than looking frozen, until it does."""
    m = ov.model
    m._units = [_hero(PA), training_dummy(FOE, uid="Dummy", hp=100000.0)]
    _hit(m, FOE, 500.0)
    _tick(ov)
    assert ov.tracker.in_dummy_fight is True
    assert ov.rearm_lbl.isVisible() is False       # actively fighting

    # idle for a few seconds: the countdown appears
    ov.tracker.dummy_session.last_event_time = time.time() - 4.0
    _tick(ov)
    assert ov.rearm_lbl.isVisible() is True
    assert L.REARM_WORD in ov.rearm_lbl.text()

    # ...and a fresh swing takes it away again
    _hit(m, FOE, 100.0)
    _tick(ov)
    assert ov.rearm_lbl.isVisible() is False
    assert ov.tracker.in_dummy_fight is True


def test_the_hud_replays_a_finished_test_and_names_its_worst_gap(ov):
    """A finished test is played back on the clock it was measured on: one bar
    per second of damage, the run's own average, and the worst stretch that
    landed NOTHING named in seconds — the half of "why is my DPS not higher"
    the finished total cannot say. A LIVE test is not replayed: its timeline is
    still growing, so a strip of it would only be a spikier copy of the DPS two
    lines above it."""
    m = ov.model
    m._units = [_hero(PA), training_dummy(FOE, uid="Dummy", hp=100000.0)]
    t0 = time.time()
    for off, amount in ((0.0, 500.0), (1.0, 600.0), (3.0, 700.0), (4.0, 800.0)):
        m.damage.push(DamageEvent(amount=amount, skill="Sword_Base_Attack",
                                  source_addr=PA, target_addr=FOE, t=t0 + off))
    _tick(ov)
    assert ov.tracker.in_dummy_fight is True
    assert ov.replay_box.isVisible() is False     # live: nothing to replay

    ov.tracker.stop_dummy_test()
    _tick(ov)
    assert ov.replay_box.isVisible() is True
    # one bar per second, the empty second (2) included so the gap shows
    assert ov.replay_box._bars == [500.0, 600.0, 0.0, 700.0, 800.0]
    assert ov.replay_box.gap_lbl.text() == L.replay_gap(1, 2.0)
    assert ov.replay_box._playing is True         # a new finished run plays

    # scrubbed to the end: the banked damage and DPS are the run's own numbers.
    # `_bars` (above) is the raw series, so the expected readout is COMPUTED
    # from it through the shared grammar rather than respelled - the format
    # itself belongs to the pinning test, not to this one.
    ov.replay_box.seek(ov.replay_box._dur)
    assert ov.replay_box.read_lbl.text() == L.replay_read(
        5.0, 5.0, sum(ov.replay_box._bars), 2600.0 / 5.0)

    # Reset drops the replay with the parse it describes
    ov.reset()
    _tick(ov)
    assert ov.replay_box.isVisible() is False


def test_an_address_less_yard_still_shows_the_whole_yard(ov):
    """A capture with no per-dummy addresses still reads as the YARD.

    The wire carries the target's address now, but an older bridge (or a
    context the pointer cannot be read from) sends the name only. Every hit
    then lands in one bucket, and appending that beside the scene's rows made a
    three-dummy yard render FOUR rows — one of them not a dummy at all. The
    split falls back to the cluster, so the player sees three dummies.

    The estimate is marked on the ONE row that absorbed those hits, not on the
    split: the other two rows were measured, and a header tag made all three
    look like guesses.
    """
    from farever_companion.ui import theme
    m = ov.model
    m._units = [_hero(PA),
                training_dummy(FOE, uid="Dummy", hp=100000.0, x=2.0, y=1.0, z=1.0),
                training_dummy(BOSS, uid="Dummy", hp=100000.0, x=3.0, y=1.0, z=1.0),
                training_dummy(0xB0B, uid="Dummy", hp=100000.0, x=4.0, y=1.0, z=1.0)]
    m.damage.push(DamageEvent(amount=700.0, skill="Sword_Base_Attack",
                              source_addr=PA, target_addr=0,
                              target_name="Dummy", t=time.time()))
    _tick(ov)

    assert ov.split_box.isVisible() is True
    rows = [r for r in ov.split_box._rows if r.isVisible()]
    assert len(rows) == 3                       # three dummies, not four rows
    # the SPLIT carries the plain total; the header only explains the mark
    assert ov.split_box.box.header._tag.text() == L.dummy_split_head_tag(700.0)
    assert ov.split_box.box.header._label.text() == L.dummy_split_head_text(3)
    assert "no target address" in ov.split_box.box.header.toolTip()
    # ...and exactly ONE row is marked as the guess
    marked = [r for r in rows if r.total_lbl.text().startswith(L.ESTIMATE_MARK)]
    assert len(marked) == 1
    assert marked[0].total_lbl.text() == L.split_row_total(700.0, True)
    assert "no target address" in marked[0].toolTip()
    assert theme.ORANGE in marked[0].total_lbl.styleSheet()   # a guess, not a read
    for r in rows:
        if r is not marked[0]:
            assert r.total_lbl.text() == L.split_row_total(0.0)
            assert "no target address" not in r.toolTip()
            assert theme.ACCENT_LIGHT in r.total_lbl.styleSheet()
    # the damage is on ONE yard row, and the rows still sum to the total shown
    assert abs(sum(float(r.total_lbl.text().lstrip(L.ESTIMATE_MARK)
                         .replace(",", "")) for r in rows) - 700.0) < 1e-6


def test_hud_gives_every_dummy_in_a_cluster_its_own_bar(ov):
    """A training yard is several dummies in ONE session, so the split is the
    only place that can show which of them is hurt."""
    m = ov.model
    m._units = [_hero(PA), training_dummy(FOE, uid="Dummy", hp=100000.0),
                training_dummy(BOSS, uid="TrainingDummy", hp=50000.0)]
    _hit(m, FOE, 300.0)
    _hit(m, BOSS, 200.0)
    _tick(ov)

    assert ov.split_box.isVisible() is True
    rows = [r for r in ov.split_box._rows if r.isVisible()]
    assert len(rows) == 2
    assert sorted(r.bar.value() for r in rows) == [100, 100]
    # the split rows' own numbers, in the shared grammar
    assert [r.hits_lbl.text() for r in rows] == [L.hits_label(1)] * 2
    assert sorted(r.share_lbl.text() for r in rows) == \
        [L.share_pct(40.0), L.share_pct(60.0)]

    # one dummy losing health moves only ITS row
    m._units = [_hero(PA), training_dummy(FOE, uid="Dummy", hp=40000.0),
                training_dummy(BOSS, uid="TrainingDummy", hp=50000.0)]
    _tick(ov)
    rows = [r for r in ov.split_box._rows if r.isVisible()]
    assert sorted(r.bar.value() for r in rows) == [40, 100]


def test_clicking_a_split_row_pins_that_dummy_as_the_target(ov):
    """The split rows are the target picker. The header follows the damage
    leader on its own, and a click pins the row the player picked — which is the
    point of a training yard: watch one dummy while you go on hitting the other.
    Clicking the pinned row hands the pin back to the rule."""
    m = ov.model
    m._units = [_hero(PA), training_dummy(FOE, uid="Dummy", hp=100000.0),
                training_dummy(BOSS, uid="TrainingDummy", hp=50000.0)]
    _hit(m, FOE, 100.0)
    _hit(m, BOSS, 400.0)
    _tick(ov)

    # the leader owns the header before anyone clicks anything
    assert ov.target_lbl.text() == L.target_label("Training Dummy")
    assert "50,000" in ov.target_hp_lbl.text()
    rows = [r for r in ov.split_box._rows if r.isVisible()]
    assert [r._pinned for r in rows] == [True, False]      # damage-ranked

    _click(rows[1])
    _tick(ov)
    assert ov.tracker.dummy_session.target_addr == FOE
    assert ov.target_lbl.text() == L.target_label("Dummy")
    assert "100,000" in ov.target_hp_lbl.text()            # the clicked dummy's HP
    rows = [r for r in ov.split_box._rows if r.isVisible()]
    assert [r._pinned for r in rows] == [False, True]      # the highlight followed

    # ...and the leader can no longer steal the header back
    _hit(m, BOSS, 9000.0)
    _tick(ov)
    assert ov.target_lbl.text() == L.target_label("Dummy")
    assert ov.tracker.dummy_session.target_addr == FOE

    # a second click on the same row releases the pin
    _click([r for r in ov.split_box._rows if r.isVisible()][1])
    _tick(ov)
    assert ov.tracker.dummy_session.target_locked is False
    assert ov.target_lbl.text() == L.target_label("Training Dummy")


def test_the_hud_measures_the_test_against_the_last_one(ov):
    """The reason a training yard exists: the same rotation, twice, against
    something that never moves. The section shows each skill's live DPS next to
    the remembered test's — and stays hidden until there IS a reference, because
    a "vs last test" with nothing behind it teaches the player to distrust the
    HUD."""
    m = ov.model
    m._units = [_hero(PA), training_dummy(FOE, uid="Dummy", hp=1000000.0)]
    _hit(m, FOE, 1000.0)
    _tick(ov)
    assert ov.baseline_box.isVisible() is False        # no reference yet

    # finish that test; it becomes the reference. Anchor the fixture to the
    # session's OWN last event: `duration` is event-anchored now (see
    # CombatSession.duration), so `time.time() - 5.0` measures a span that is
    # short by however long the tick above took, and the deltas below read a
    # few percent off (live-2026-10-03's 600-vs-637 bug, in test form).
    ov.tracker.dummy_session.start_time = ov.tracker.dummy_session.last_event_time - 5.0
    ov.tracker.stop_dummy_test()
    _tick(ov)
    ref = ov.tracker.take_dummy_baseline_pending()
    assert ref and ref["dps"] > 0.0
    ov.tracker.set_dummy_baseline(ref)

    # a slower second test: the section says so, per skill and in the header
    ov.tracker.start_dummy_test()
    _tick(ov)
    _hit(m, FOE, 100.0)
    _tick(ov)
    # Anchor to the hit's own stamp (drained by the tick above): `duration` is
    # event-anchored, so this is the only way the fixture measures the 5 s it
    # says it does. Setting it before the drain would read a 0.0 stamp.
    ov.tracker.dummy_session.start_time = ov.tracker.dummy_session.last_event_time - 5.0
    _tick(ov)
    assert ov.baseline_box.isVisible() is True
    rows = [r for r in ov.baseline_box._rows if r.isVisible()]
    assert len(rows) == 1
    assert rows[0].delta_lbl.text().startswith("-")     # 20/s now vs 200/s
    assert rows[0].delta_lbl.text() == L.delta_line(-180.0, -90.0)
    assert rows[0].live_lbl.text() == L.rate(20.0, places=0)
    assert ov.baseline_box.box.header._tag.text().startswith("-")
    assert "%" in ov.baseline_box.box.header._tag.text()

    # Reset clears the section with the session it measured
    ov.reset()
    _tick(ov)
    assert ov.baseline_box.isVisible() is False


def test_the_hud_counts_down_to_the_target_and_shows_what_the_test_took(ov):
    """Two things a measured test needs and a dummy cannot report itself: how
    long is left of the target length (the test ends itself, so every run is the
    same length and the numbers are comparable), and how much damage landed on
    the player while it ran — a "clean 30 s" is only clean if that stayed at
    zero.

    The countdown counts the SESSION clock, which starts on the first hit, so
    walking up to an idle dummy must leave the whole target ahead of the player
    rather than burning it. And a target of 0 hides both, because a bar stuck at
    zero would be a claim the HUD cannot back up.
    """
    from farever_companion.ui import theme

    m = ov.model
    m._units = [_hero(PA), training_dummy(FOE, uid="Dummy", hp=1000000.0)]
    _tick(ov)
    assert ov.clock_lbl.isVisible() is True
    assert ov.clock_lbl.text() == L.clock_ready(60.0)   # idle: nothing spent
    assert ov.time_bar.isVisible() is True
    assert ov.time_bar.value() == 0
    assert ov.chip_taken.title_lbl.text() == L.CHIP_TAKEN
    assert ov.chip_taken.val_lbl.text() == "0"

    _hit(m, FOE, 500.0)
    _tick(ov)
    assert ov.clock_lbl.text() == L.mmss(60.0)    # started, nothing spent yet
    assert ov.chip_taken.val_lbl.text() == "0"

    # 12 s in, and something in the world hit the player back
    ov.tracker.dummy_session.start_time = time.time() - 12.0
    m.damage.push(DamageEvent(amount=340.0, skill="Mob_Bite", source_addr=FOE,
                              target_addr=PA, incoming=True, t=time.time()))
    _tick(ov)
    assert ov.clock_lbl.text() == L.mmss(48.0)
    assert ov.time_bar.value() == 20
    assert theme.ACCENT_LIGHT in ov.clock_lbl.styleSheet()   # 48 s left: no warning
    # ...and the elapsed readout below the clock reads the SAME way: whole
    # seconds, M:SS, never the padded tenths ("00:12.3") it used to show.
    assert ov.sub_lbl.text() == L.elapsed_line(12.0, 1)
    assert "340" in ov.chip_taken.val_lbl.text()
    assert "/s" in ov.chip_taken.sub_lbl.text()

    # The warning opens 20 s out - early enough to still close a rotation - and
    # turns red at 10 s. Both readings sit well clear of the boundaries, so a
    # slow tick cannot land them in the other tier.
    ov.tracker.dummy_session.start_time = time.time() - 42.0
    _tick(ov)
    assert ov.clock_lbl.text() == L.mmss(18.0)
    assert theme.ORANGE in ov.clock_lbl.styleSheet()
    ov.tracker.dummy_session.start_time = time.time() - 52.0
    _tick(ov)
    assert ov.clock_lbl.text() == L.mmss(8.0)
    assert theme.DANGER in ov.clock_lbl.styleSheet()

    # the last five seconds are the ones that get read
    ov.tracker.dummy_session.start_time = time.time() - 57.0
    _tick(ov)
    assert ov.clock_lbl.text() == L.mmss(3.0)
    assert theme.DANGER in ov.clock_lbl.styleSheet()

    # the target spent it: the test ends itself and the run stays on screen
    ov.tracker.dummy_session.start_time = time.time() - 60.0
    _tick(ov)
    assert ov.tracker.in_dummy_fight is False
    assert ov.clock_lbl.text() == L.clock_done(60.0)
    assert ov.dmg_lbl.text() == L.dmg(500.0)      # the finished run is readable
    # ...and it is not immediately replaced: the button now offers the next run
    assert "Start" in ov.dummy_btn.text()
    _tick(ov)
    assert ov.tracker.in_dummy_fight is False     # no silent second test

    # a target of 0 = free-running: nothing to count down to, so nothing drawn
    ov.tracker.dummy_target_s = 0.0
    _tick(ov)
    assert ov.clock_lbl.isVisible() is False
    assert ov.time_bar.isVisible() is False


def test_the_hud_shows_the_personal_best_beside_the_last_test(ov):
    """Two records, two questions, both on screen at once. "Did that change
    help" is about the run before this one (the header); "is this gear actually
    better" is about everything (the best line). A run that is worse than the
    best but better than last time is a normal Tuesday and must read as both —
    which is the whole reason the best is a separate number and not a rewrite
    of the reference.
    """
    m = ov.model
    m._units = [_hero(PA), training_dummy(FOE, uid="Dummy", hp=1000000.0)]

    def run(amount: float, seconds: float = 20.0):
        """One test: land `amount`, rewind the session clock, render.

        The clock is set AFTER the tick that engages the session, because
        engaging resets it — the same order the other tests in this file use.
        Anchored to the hit's own stamp rather than the wall clock: `duration`
        is event-anchored (see CombatSession.duration), so `time.time()` would
        measure a span short by however long the tick above took and the DPS
        figures below would drift with machine load.
        """
        _hit(m, FOE, amount)
        _tick(ov)
        ov.tracker.dummy_session.start_time = ov.tracker.dummy_session.last_event_time - seconds
        _tick(ov)

    # a first test at 1000/20 = 50/s: with no best yet, the line stays hidden
    run(1000.0)
    ov.tracker.stop_dummy_test()
    _tick(ov)
    ref = ov.tracker.take_dummy_baseline_pending()
    ov.tracker.set_dummy_baseline(ref)
    assert abs(ov.tracker.dummy_best["dps"] - 50.0) < 0.5   # clock drift

    ov.tracker.start_dummy_test()
    # 900/20 = 45/s: CLEARLY worse than the 50/s record, not a replay of it. A
    # repeat of the same run is a tie, and a tie is deliberately not a new
    # record — but that half is checked on the rule in test_dps.py, not here:
    # two nominally identical live runs differ by however long the machine took
    # between the two reads, so a hair over 50/s would read as a record and the
    # assertion below would flip with machine load. What the HUD owes this case
    # is the gap to the ceiling, so the run is made clearly worse on purpose.
    run(900.0)
    assert "NEW BEST" not in ov.baseline_box.best_lbl.text()
    assert L.best_headline(50.0) in ov.baseline_box.best_lbl.text()
    assert ov.baseline_box.best_lbl.isVisible() is True
    assert ov.baseline_box.isVisible() is True

    # a worse run shows the gap to the ceiling, not just to yesterday
    ov.tracker.stop_dummy_test()
    ov.tracker.set_dummy_baseline(ov.tracker.take_dummy_baseline_pending())
    ov.tracker.start_dummy_test()
    run(500.0)
    assert L.best_headline(50.0) in ov.baseline_box.best_lbl.text()
    assert "-50" in ov.baseline_box.best_lbl.text()   # half the record
    assert L.best_tooltip_this_run(25.0) in ov.baseline_box.best_lbl.toolTip()

    # a record-breaking run says so in gold and carries no percentage
    ov.tracker.stop_dummy_test()
    ov.tracker.set_dummy_baseline(ov.tracker.take_dummy_baseline_pending())
    ov.tracker.start_dummy_test()
    run(2000.0)
    assert L.new_best(100.0) in ov.baseline_box.best_lbl.text()
    assert "%" not in ov.baseline_box.best_lbl.text()
    # The line reads the LIVE run being ahead of the record, but the record
    # itself only moves when the test is finished — a run that stops early
    # banks the DPS it actually finished at, not its best moment.
    assert ov.tracker.dummy_best["dps"] < 60.0     # still the first run's record
    ov.tracker.stop_dummy_test()
    assert abs(ov.tracker.dummy_best["dps"] - 100.0) < 0.05   # clock drift

    # Reset clears the section (and the best line) with the session it measured
    ov.reset()
    _tick(ov)
    assert ov.baseline_box.best_lbl.isVisible() is False


def test_the_hud_flags_a_test_that_took_damage(ov):
    """The flag is the whole point of the taken readout: a dummy does not fight
    back, so damage taken during a test is the rotation or the world walking
    in, and either way the DPS above is not the DPS of the build. It has to
    appear WHILE the run is live — before the player reads the comparison
    section and believes it.
    """
    m = ov.model
    m._units = [_hero(PA), training_dummy(FOE, uid="Dummy", hp=1000000.0)]
    _hit(m, FOE, 10000.0)
    _tick(ov)
    ov.tracker.dummy_session.start_time = time.time() - 20.0
    _tick(ov)
    assert ov.dirty_strip.isVisible() is False       # nothing taken yet
    assert ov.chip_taken.val_lbl.text() == "0"

    # 1% of the test's own damage: real damage, under the 5% limit
    m.damage.push(DamageEvent(amount=100.0, skill="Mob_Bite", source_addr=0x9999,
                              target_addr=PA, t=time.time()))
    _tick(ov)
    assert "100" in ov.chip_taken.val_lbl.text()
    assert ov.dirty_strip.isVisible() is False       # measured, not flagged

    # over the limit: the warning appears, and the number stays visible
    m.damage.push(DamageEvent(amount=800.0, skill="Mob_Bite", source_addr=0x9999,
                              target_addr=PA, t=time.time()))
    _tick(ov)
    assert ov.dirty_strip.isVisible() is True
    assert ov.dirty_strip.lbl.text() == L.dirty_warning(9.0, 5.0)
    assert ov.chip_taken.val_lbl.text() == L.chip_value(900.0)   # not hidden

    # a limit of 0 flags ANY damage taken, not just a lot of it
    ov.reset()
    _hit(m, FOE, 10000.0)
    _tick(ov)
    ov.tracker.dummy_session.start_time = time.time() - 20.0
    ov.tracker.dummy_taken_limit_pct = 0.0
    _tick(ov)
    assert ov.dirty_strip.isVisible() is False       # still clean
    m.damage.push(DamageEvent(amount=10.0, skill="Mob_Bite", source_addr=0x9999,
                              target_addr=PA, t=time.time()))
    _tick(ov)
    assert ov.dirty_strip.isVisible() is True
    assert ov.dirty_strip.lbl.text() == L.dirty_warning(0.1, 0.0)

    # Reset clears it with the parse it was describing
    ov.reset()
    _tick(ov)
    assert ov.dirty_strip.isVisible() is False


def test_the_hud_carries_the_tests_end_signal(ov):
    """A dummy fight has no end signal in the game at all, so the HUD owns one:
    Stop archives the parse and holds it closed, and the window keeps showing
    the finished test until Reset / Start Test."""
    m = ov.model
    m._units = [_hero(PA), training_dummy(FOE, uid="Dummy", hp=100000.0)]
    _hit(m, FOE, 500.0)
    _tick(ov)
    assert ov.dummy_btn.isVisible() is True
    assert "Stop" in ov.dummy_btn.text()

    ov.dummy_btn.click()
    QtWidgets.QApplication.processEvents()
    assert ov.tracker.dummy_test_armed is False
    assert ov.tracker.in_dummy_fight is False
    _tick(ov)
    assert "Start" in ov.dummy_btn.text()
    assert ov.dmg_lbl.text() == L.dmg(500.0)  # the finished test stays on screen

    # Reset is a fresh meter: it clears the parse and re-arms the test
    ov.reset()
    assert ov.dmg_lbl.text() == L.dmg(0.0)
    assert ov.target_lbl.text() == L.TARGET_NONE
    assert ov.clock_lbl.text() == L.clock_ready(60.0)   # the whole target is back
    assert ov.tracker.dummy_test_armed is True
    _tick(ov)
    assert ov.tracker.in_dummy_fight is True
    assert ov.tracker.dummy_session.group_damage == 0.0
    assert ov.split_box.isVisible() is False


def test_the_dummy_range_gate_decides_the_boards_own_affordances(ov):
    """Dummy Range is the number the CORE dummy read is gated by, so the board
    it is set from must not offer a test the core would refuse: a dummy the scan
    can see 40 m away is in `dummy_near` either way, but outside a 5 m radius
    there is no Start Test and the empty state says why. Widening the radius
    flips both — in the same repaint, with no tick in between."""
    m = ov.model
    m._units = [_hero(PA),
                training_dummy(FOE, uid="Dummy", x=40.0, y=0.0, z=0.0)]
    # A stopped test, so nothing engages itself: what is under test here is what
    # the board OFFERS at a dummy, not the test the scan would open.
    ov.tracker.dummy_test_armed = False

    _tick(ov)                                    # 5 m: the core default radius
    assert ov.tracker.dummy_near is not None     # the scan reports it...
    assert ov.tracker.dummy_in_range() is False  # ...but it is out of range
    assert ov.dummy_btn.isVisible() is False     # so there is nothing to start
    assert "No training dummy in range" in ov.empty_lbl.text()

    # the setting moves: the new number reaches the core read and the window
    # repaints from it, with no tick between the edit and the picture
    ov.s.dps_dummy_range = 50
    ov.tracker.dummy_range = 50.0
    ov.refresh_now()
    QtWidgets.QApplication.processEvents()
    assert ov.tracker.dummy_in_range() is True
    assert ov.dummy_btn.isVisible() is True
    assert "Test stopped — press Start Test" in ov.empty_lbl.text()

    # ...and narrowing it again takes the offer back away
    ov.s.dps_dummy_range = 5
    ov.tracker.dummy_range = 5.0
    ov.refresh_now()
    QtWidgets.QApplication.processEvents()
    assert ov.dummy_btn.isVisible() is False
    assert "No training dummy in range" in ov.empty_lbl.text()


def test_refresh_now_repaints_in_place_and_leaves_a_hidden_window_alone(ov):
    """`refresh_now` is the settings-change repaint: the board lands on screen in
    the same frame as the edit rather than at the next 100 ms tick. A HIDDEN
    window does nothing at all — there is no board to correct, and draining the
    tracker for one would be work nobody asked for."""
    m = ov.model
    m._units = [_hero(PA), training_dummy(FOE, uid="Dummy")]
    _hit(m, FOE, 250.0)
    ov.refresh_now()                            # no `_tick` call in between
    assert ov.dmg_lbl.text() == L.dmg(250.0)

    ov.hide()
    _hit(m, FOE, 100.0)
    ov.refresh_now()
    assert ov.dmg_lbl.text() == L.dmg(250.0)    # nothing painted while hidden
    ov.show()
    _tick(ov)
    assert ov.dmg_lbl.text() == L.dmg(350.0)    # the hit was never lost


def test_the_hud_label_formats_are_the_shared_contract(ov):
    """One test owns the HUD's label GRAMMAR; the rest of the file reads it.

    Every format below mirrors a real call site:

      * `dummy_overlay.py`  - dmg_lbl, clock_lbl, sub_lbl, rearm_lbl,
                              target_lbl, chip_taken.title_lbl, dummy_btn
      * `dps/dummy_ui.py`   - hits_lbl, share_pct, live_lbl, best_lbl,
                              best_lbl tooltip, DirtyRunStrip.lbl, the split
                              section's head text/tag, and the `~` on the one
                              row that absorbed address-less hits
      * `dps/dummy_replay.py` - read_lbl, gap_lbl

    The explicit literals on the left are the point: without them this would
    only prove the helper agrees with itself. If a label changes shape, THIS is
    the single test that goes red, and the assertions elsewhere in the file
    follow one edit here (`tests/dummy_labels.py`) instead of five copies.
    """
    # The formatters themselves, spelled out.
    assert L.mmss(60.0) == "1:00"
    assert L.mmss(93.0) == "1:33"
    assert L.mmss(27.4) == "0:27"          # whole seconds, rounded
    assert L.clock_ready(60.0) == "1:00 ready"
    assert L.clock_done(60.0) == "DONE 1:00"
    assert L.elapsed_line(12.0, 1) == "0:12 · 1 hits"
    assert L.rearm(12.4) == "⏳ auto re-arm in 12s"
    assert L.dmg(400.0) == "400 dmg"
    assert L.dmg(12345.0) == "12,345 dmg"
    assert L.rate(39.9) == "39.9/s"       # the headline rate keeps a decimal
    assert L.rate(520.0, places=0) == "520/s"
    assert L.delta_rate(-12.4) == "-12/s"
    assert L.delta_line(-180.0, -90.0) == "-180/s  -90.0%"
    assert L.delta_line(12.0, None) == "+12/s"
    assert L.chip_value(900.0) == "900"    # a chip's number carries no unit
    assert L.hits_label(1) == "1 hit"
    assert L.hits_label(3) == "3 hits"
    assert L.share_pct(48.8) == "48.8%"
    assert L.dummy_split_head_text(3) == "SPLIT BY TARGET · 3"
    assert L.dummy_split_head_tag(700.0) == "700 total"   # no `est.` variant
    assert L.split_row_total(700.0, estimated=True) == "~700"
    assert L.split_row_total(1234.0) == "1,234"
    assert L.ESTIMATE_MARK == "~"          # the ONE guessed row's marker
    assert L.replay_read(5.0, 5.0, 2600.0, 520.0) == \
        "0:05 / 0:05   2,600 dmg   520/s"
    assert L.clock_ready(0.0) == "0:00 ready"
    assert L.replay_gap(1, 2.0) == "worst gap 1s @ 0:02"
    assert L.replay_gap(0, 0.0) == "no dead seconds"
    assert L.best_headline(50.0) == "BEST 50/s"
    assert L.new_best(100.0) == "★ NEW BEST 100/s"
    assert L.best_line(50.0, "just now", -50.0) == "BEST 50/s · just now · -50.0%"
    assert L.best_line(50.0, "just now", None) == "BEST 50/s · just now · —"
    assert L.best_tooltip_this_run(25.0) == "This run: 25/s."
    assert L.dirty_warning(9.0, 5.0) == (
        "⚠ 9.0% of this test's damage came back at you (over 5%) — not a "
        "clean measurement")
    assert L.target_label("Dummy") == "🎯 Dummy"
    assert L.TARGET_NONE == "Target: —"
    assert L.BTN_STOP == "🎯 Stop Test" and L.BTN_START == "▶ Start Test"
    assert L.HINT_STOPPED == "🎯 Test stopped — press Start Test."
    assert L.HINT_ATTACK == "🎯 Attack the dummy to start."
    assert L.CHIP_TAKEN == "TAKEN" and L.CHIP_HEALING == "HEALING"
    assert L.REPLAY_TITLE == "REPLAY"
    assert (L.PLAY_GLYPH, L.PAUSE_GLYPH) == ("▶", "❚❚")

    # ...and the HUD really renders them (a helper that drifted from the widget
    # is the failure mode this half exists for).
    m = ov.model
    m._units = [_hero(PA), training_dummy(FOE, uid="Dummy", hp=1000000.0)]
    _hit(m, FOE, 400.0)
    _tick(ov)
    assert ov.dmg_lbl.text() == L.dmg(400.0)
    assert ov.dps_lbl.text() == L.rate(400.0 / max(1.0, ov.tracker.dummy_clock().elapsed_s))
    assert ov.clock_lbl.text() == L.mmss(60.0)
    assert ov.chip_taken.title_lbl.text() == L.CHIP_TAKEN
    assert ov.chip_heal.title_lbl.text() == L.CHIP_HEALING
    assert ov.dummy_btn.text() == L.BTN_STOP
    assert ov.target_lbl.text() == L.target_label("Dummy")
    assert ov.replay_box.title_lbl.text() == L.REPLAY_TITLE
    assert ov.replay_box.play_btn.text() in (L.PLAY_GLYPH, L.PAUSE_GLYPH)


def test_the_dummy_hud_is_registered_as_a_real_hud_window():
    """The manager resolves it by runtime string, so the source table and the
    frozen-build hiddenimport must both name it (the hygiene suite checks the
    pair; this pins the key itself)."""
    from farever_companion.ui import overlay_manager as om

    assert "dummy" in om.HUD_OVERLAYS
    assert om._OVERLAY_SOURCES["dummy"] == ("dummy_overlay", "DummyOverlay")
    assert om._overlay_class("dummy") is DummyOverlay


def test_pd_md_line_merges_tagged_hits_and_hides_when_untagged(ov):
    """Physical + Raw read as PD, every other tagged school as MD; an
    untagged capture grows no line instead of a zero one."""
    m = ov.model
    m._units = [_hero(PA), training_dummy(FOE, uid="Dummy", hp=100000.0)]
    for amount, aff in ((600.0, "Physical"), (100.0, "Raw"),
                        (200.0, "Fire"), (50.0, "Chaos"), (50.0, "")):
        m.damage.push(DamageEvent(amount=amount, skill="Sword_Base_Attack",
                                  affinity=aff, source_addr=PA,
                                  target_addr=FOE, t=time.time()))
    _tick(ov)
    assert ov.pdmd_lbl.isVisible() is True
    assert "PD 700" in ov.pdmd_lbl.text()
    assert "MD 250" in ov.pdmd_lbl.text()
    assert f"untagged 50" in ov.pdmd_lbl.text()

    ov.reset()
    QtWidgets.QApplication.processEvents()
    assert ov.pdmd_lbl.isVisible() is False


def test_skill_rows_name_the_damage_type_they_deal(ov):
    """Each skill row names the school it deals (the TYPE cell, as the DPS
    Analysis page does) — blank until the capture tags the hits. The merged
    PD/MD NUMBERS are on the board's own line above, not on every row."""
    m = ov.model
    m._units = [_hero(PA), training_dummy(FOE, uid="Dummy", hp=100000.0)]
    m.damage.push(DamageEvent(amount=400.0, skill="Sword_Base_Attack",
                              affinity="Fire", source_addr=PA,
                              target_addr=FOE, t=time.time()))
    _tick(ov)
    rows = ov._row_widgets["Me (You)"]._skill_row_widgets
    row = rows["Sword_Base_Attack"]
    assert row._type_lbl.text() == "Fire"
    assert not hasattr(row, "_pdmd_lbl")   # one damage-type cell, not two
