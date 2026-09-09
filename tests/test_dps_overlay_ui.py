"""Offscreen UI tests for the Top DPS overlay (mockup layout + tracker).

Builds the real DpsOverlay on a fake model/settings and verifies:
(a) the header is the mockup control row - a green-dot TOP DPS title with a
    DMG / HEAL / BOTH segmented pill in the title bar (the old METER / VIEW
    dropdowns are gone);
(b) the pill drives the meter mode and writes settings; segment view is
    driven by the tracker (Combat page tabs);
(c) real damage/incoming events flow through _tick() into ranked player rows
    (per-row stats; damage-taken totals are tracked and shown per-player on
    the Combat page, not duplicated on overlay rows), and pause/reset behave.
"""
from __future__ import annotations

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402

from farever_companion.core.damage_events import DamageEvent  # noqa: E402
from farever_companion.core.dps_tracker import PlayerParse  # noqa: E402
from farever_companion.core.group_reader import (  # noqa: E402
    GroupSnapshot, GroupMember,
)
from farever_companion.core.scene import Entity  # noqa: E402
from farever_companion.ui.overlays.dps import (  # noqa: E402
    DpsOverlay, GLOBAL_SKILL_CAP, _PlayerRowWidget,
)
from tests.dps_fakes import (  # noqa: E402
    PA, ALLY, FOE, BOSS, PET, _FakeModel as _Model,
    _hero, _foe, training_dummy,
)


@pytest.fixture(scope="module", autouse=True)
def _qapp():
    """One offscreen QApplication for the whole module (widgets need one).

    Without this the module hard-aborts the interpreter (0xC0000409) when run
    on its own: building a QWidget with no QApplication kills the process
    rather than failing a test. A full-suite run only survives it because an
    earlier module happens to leave a QApplication alive.
    """
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests.qt_helpers import click, destroy  # noqa: E402


class _Settings:
    opacity = 1.0
    geometry = {}
    dps_view = "damage"
    dps_top_count = 8
    heals_top_count = 2

    def save(self):
        pass


@pytest.fixture()
def ov():
    """Fresh overlay on a fresh fake model/settings (shown for layout state)."""
    o = DpsOverlay(_Model(names={PA: "Me", ALLY: "Brit"}), _Settings())
    o.show()
    QtWidgets.QApplication.processEvents()   # offscreen: visible only after the queue drains
    try:
        yield o
    finally:
        destroy(o)


def _tick(ov):
    """Refresh the overlay and drain Qt so rows report visibility truthfully."""
    ov._tick()
    QtWidgets.QApplication.processEvents()


# --- cleaned-up controls ---------------------------------------------------

def test_the_meter_renders_no_dummy_surface(ov):
    """The dummy TEST lives in the Test Dummy HUD and nowhere else.

    The meter used to carry a second copy of the whole test surface — Stop /
    Start, SPLIT BY TARGET, the dirty-run strip, VS LAST TEST — built whenever
    the Test Dummy HUD was off, so every dummy feature existed twice and the two
    boards could stack over one dummy. The meter is dummy-blind now: with
    dummies in front of it the tracker still runs the test, but the widgets are
    the plain ones a boss fight gets.

    `tests/test_dummy_overlay.py` owns the surfaces themselves; this is the
    guard for them having exactly one home.
    """
    m = ov.model
    m._units = [_hero(PA), training_dummy(FOE, uid="PunchingBag", hp=100000.0),
                training_dummy(BOSS, uid="TrainingDummy", hp=100000.0)]
    m.damage.push(DamageEvent(amount=500.0, skill="Axe_Whirlwind",
                              source_addr=PA, target_addr=FOE, t=time.time()))
    _tick(ov)

    # the dummy test is still RUNNING — it is the rendering that moved
    assert ov.tracker.in_dummy_fight is True
    assert abs(ov.tracker.dummy_session.group_damage - 500.0) < 1e-6
    for gone in ("split_box", "dirty_strip", "baseline_box", "dummy_btn"):
        assert not hasattr(ov, gone), f"the meter still builds {gone}"

    # ...and standing at a dummy changes nothing about its empty state: the
    # prompt to go hit the dummy is the Test Dummy HUD's line
    ov.model.player_addr = None
    ov.tracker.reset()
    _tick(ov)
    assert ov.empty_lbl.isVisible()
    assert ov.empty_lbl.text() == "Hit an enemy or heal party to start."


def test_header_is_mockup_control_row(ov):
    # removed: range cycle button, mode tabs, segment toggle buttons AND the
    # METER / VIEW dropdowns - replaced by the DMG / HEAL / BOTH segmented pill
    assert not hasattr(ov, "range_btn")
    assert not hasattr(ov, "_btn_dmg") and not hasattr(ov, "_btn_both")
    assert not hasattr(ov, "_btn_seg_auto") and not hasattr(ov, "_btn_seg_overall")


def test_status_row_shows_live_party_roster(ov):
    """The status row renders the live party roster, solo, or blank."""
    # no group_roster on the stock fake model -> line stays blank
    _tick(ov)
    assert ov.group_lbl._full == ""

    # roster of two -> real names with role tags
    ov.model.roster = GroupSnapshot(group=0x5000, members=[
        GroupMember(player=0, hero=ALLY, name="Alice", is_leader=True),
        GroupMember(player=0, hero=PA, name="Me", is_me=True),
    ])
    ov.model.group_roster = lambda: ov.model.roster
    _tick(ov)
    assert "Group: 2 — Alice (leader), Me (you)" in ov.group_lbl._full

    # a decoded-but-empty group reads as solo
    ov.model.roster = GroupSnapshot(group=0x5000, members=[], solo=True)
    _tick(ov)
    assert ov.group_lbl._full == "Group: solo"

    # no decoded group -> blank (no misleading 'solo' while unattached)
    ov.model.roster = None
    _tick(ov)
    assert ov.group_lbl._full == ""
    assert not hasattr(ov, "mode_combo") and not hasattr(ov, "seg_combo")
    assert set(ov.mode_pill._btns) == {"DMG", "HEAL", "BOTH"}
    assert ov.mode_pill.currentText() == "DMG"
    # green-dot TOP DPS title + bottom Reset / Pause / Recent-events buttons
    assert "TOP DPS" in ov.titlebar.title.text()
    assert hasattr(ov, "pause_btn") and ov.pause_btn is not None
    assert hasattr(ov, "reset_btn") and ov.reset_btn is not None


# --- pill drives the meter; tracker drives the segment ----------------------

def test_tracker_segment_view_drives_displayed_session(ov):
    # auto picks the boss segment while a boss fight is live
    m = ov.model
    m.dungeon_boss = "Boss_Foo"
    m._units = [_hero(PA), _foe(BOSS, uid="Boss_Foo", hp=100000.0,
                                  is_boss=True), _foe(FOE)]
    ov._tick()
    assert ov.tracker.in_boss_fight is True
    assert ov.tracker.session is ov.tracker.boss_session

    # the tracker's segment view (set from the Combat page tabs) shows the
    # trash segment instead - no in-overlay dropdown needed
    ov.tracker.segment_view = "trash"
    ov._tick()
    assert ov.tracker.session is ov.tracker.trash_session
    ov.tracker.segment_view = "auto"


def test_mode_pill_drives_view_mode_and_settings(ov):
    assert ov._view_mode == "damage" and ov.s.dps_view == "damage"
    ov.mode_pill._btns["HEAL"].click()
    assert ov._view_mode == "healing"
    assert ov.s.dps_view == "healing"
    assert ov.mode_pill.currentText() == "HEAL"
    ov.mode_pill._btns["BOTH"].click()
    assert ov._view_mode == "both" and ov.s.dps_view == "both"
    assert ov.mode_pill.currentText() == "BOTH"


def test_mode_pill_click_highlights_the_checked_chip(ov):
    """Clicking a DMG/HEAL/BOTH chip must re-style it as active (checked
    fill), not just flip the button-group state — previously the clicked
    chip stayed in its unchecked transparent style, so nothing looked
    highlighted."""
    ov.mode_pill._btns["HEAL"].click()
    heal = ov.mode_pill._btns["HEAL"]
    dmg = ov.mode_pill._btns["DMG"]
    both = ov.mode_pill._btns["BOTH"]
    assert heal.isChecked() is True
    assert ov.mode_pill.currentText() == "HEAL"
    # clicked chip shows the accent fill; the others stay transparent
    assert "transparent" not in heal.styleSheet()
    assert "transparent" in dmg.styleSheet()
    assert "transparent" in both.styleSheet()


# --- real events flow through the tracker into the rows --------------------

def test_damage_event_reaches_row_stat_and_taken_is_tracked(ov):
    m = ov.model
    m._units = [_hero(PA), _foe(FOE)]
    _tick(ov)

    m.damage.push(DamageEvent(amount=77.0, skill="Sword_Base_Attack", crit=True,
                              source_addr=PA, target_addr=FOE, t=time.time()))
    # a mob hits me on the same tick -> damage taken
    m.damage.push(DamageEvent(amount=30.0, skill="Mob_Bite",
                              source_addr=0x9999, target_addr=PA, t=time.time()))
    _tick(ov)

    s = ov.tracker.overall_session
    assert s.state == "COMBAT"
    assert abs(s.group_damage - 77.0) < 1e-6
    assert abs(s.group_heals) < 1e-6
    # damage taken is tracked in the session (shown per-player on the Combat
    # page; rows intentionally stay damage-only in the cleaned-up overlay)
    assert abs(s.group_taken - 30.0) < 1e-6

    # exactly one self row - the raw/suffixed duplicate pair must not render
    row = ov._row_widgets.get("Me (You)")
    assert set(ov._row_widgets) == {"Me (You)"}
    assert row is not None and row.isVisible()
    assert "77" in row.total_lbl.text()         # damage shown in the row


def test_class_chip_shows_scene_class_not_first_skill(ov):
    """The row class chip shows the scene class, not a skill guess."""
    m = ov.model
    priest = Entity(addr=PA, cls="ent.hero.Priest", unit_id=None,
                    x=1.0, y=1.0, z=1.0, hp=1000.0, is_hero=True)
    m._units = [priest, _foe(FOE)]
    _tick(ov)

    m.damage.push(DamageEvent(amount=77.0, skill="Sword_Base_Attack",
                              source_addr=PA, target_addr=FOE,
                              t=time.time()))
    _tick(ov)

    row = ov._row_widgets.get("Me (You)")
    assert row is not None and row.isVisible()
    assert row.cls_lbl.text() == "PRIEST"
    assert row.cls_lbl.isVisible()


def test_row_total_amount_is_visible_at_default_width(ov):
    """The row damage amount stays visible at the default 320px width."""
    m = ov.model
    m._units = [_hero(PA), _foe(FOE)]
    _tick(ov)
    m.damage.push(DamageEvent(amount=6000.0, skill="Sword_Base_Attack",
                              source_addr=PA, target_addr=FOE,
                              t=time.time()))
    m.damage.push(DamageEvent(amount=4000.0, skill="Warrior_Whirlwind",
                              source_addr=PA, target_addr=FOE,
                              t=time.time()))
    _tick(ov)
    ov.resize(320, 420)          # default overlay size (tightest common case)
    _tick(ov)

    row = ov._row_widgets.get("Me (You)")
    assert row is not None
    # the primary column shows the live DPS rate (10,000 over <1s), never prose
    assert "10,000" in row.total_lbl.text()
    assert row.total_lbl.text().endswith("DPS")
    assert row.total_lbl.width() >= 40          # amount has real room
    assert row.rate_lbl.width() >= 40           # share column intact too
    assert row.name_lbl.width() < 150           # name elided to fit

    # Per-skill rows had the same bug: the "amount · hits" stat was laid out
    # at 0px (Ignored policy) while the % column stayed visible - so skills
    # showed a share % with no damage amount.
    for sid, sw in row._skill_row_widgets.items():
        stat = sw.findChild(QtWidgets.QLabel, "SkillTotal")
        pct = sw.findChild(QtWidgets.QLabel, "SkillPct")
        assert stat is not None and stat.width() >= 54
        assert stat.text() and any(ch.isdigit() for ch in stat.text())
        assert pct is not None and pct.width() == 30
        assert pct.text().endswith("%")


def test_empty_state_shows_only_with_no_player(ov):
    # attached player with an empty scene: a single clean zeroed self row -
    # never fabricated numbers, never a raw/suffixed duplicate pair
    ov.model._units = []
    _tick(ov)
    assert set(ov._row_widgets) == {"Me (You)"}
    assert ov._row_widgets["Me (You)"].isVisible()
    assert ov._row_widgets["Me (You)"].total_lbl.text().startswith("0")
    assert not ov.empty_lbl.isVisible()

    # hero present, no events: still exactly one zeroed self row
    ov.model._units = [_hero(PA), _foe(FOE)]
    _tick(ov)
    assert set(ov._row_widgets) == {"Me (You)"}
    assert not ov.empty_lbl.isVisible()
    assert ov.tracker.overall_session.group_damage == 0.0
    assert ov.tracker.overall_session.group_taken == 0.0
    assert ov.tracker.overall_session.group_heals == 0.0

    # no player attached at all -> the honest empty hint (no fabrication);
    # reset first so the earlier self-row registration is not carried over
    ov.model.player_addr = None
    ov.tracker.reset()
    _tick(ov)
    assert ov.empty_lbl.isVisible()
    assert "Hit an enemy or heal party to start." == ov.empty_lbl.text()


def test_pause_resume_and_reset_drive_session_state(ov):
    m = ov.model
    m._units = [_hero(PA), _foe(FOE)]
    m.damage.push(DamageEvent(amount=50.0, skill="Sword_Base_Attack",
                              source_addr=PA, target_addr=FOE, t=time.time()))
    _tick(ov)
    sess = ov.tracker.session            # auto segment (trash here)
    assert sess.state == "COMBAT"

    ov.toggle_pause()
    assert sess.state == "PAUSED"
    assert ov.tracker.session.state == "PAUSED"
    ov.toggle_pause()
    assert sess.state == "COMBAT"

    ov.reset()
    assert ov.tracker.overall_session.group_damage == 0.0
    assert ov.tracker.overall_session.state == "READY"
    assert ov.empty_lbl.isVisible()
    assert ov.time_lbl.text() == "00:00.0"


# --- NEARBY ALLIES card removed -------------------------------------------

def test_nearby_allies_card_removed(ov):
    """The NEARBY ALLIES card is gone; ticks with ally events must not raise."""
    assert not hasattr(ov, "nearby_lbl")
    assert not hasattr(ov, "nearby_live_lbl")

    m = ov.model
    m._units = [_hero(PA), _hero(ALLY), _foe(FOE)]
    m.damage.push(DamageEvent(amount=227.0, skill="Sunlight",
                              source_addr=ALLY, target_addr=FOE, t=time.time()))
    _tick(ov)                       # must not raise with the card gone
    assert not hasattr(ov, "_update_nearby")


def test_solo_meter_shows_only_own_dps(ov):
    """Ungrouped: only the local player's row renders and chips report
    your own totals; grouping brings the rivals back."""
    m = ov.model
    m.roster = GroupSnapshot(group=0x5000, members=[], solo=True)
    m._units = [_hero(PA), _hero(ALLY), _foe(FOE)]
    t0 = time.time()
    m.damage.push(DamageEvent(amount=5000.0, skill="Sword_Spin",
                              source_addr=PA, target_addr=FOE, t=t0))
    m.damage.push(DamageEvent(amount=9000.0, skill="Ally_BigHit",
                              source_addr=ALLY, target_addr=FOE, t=t0 + 0.01))
    _tick(ov)

    # solo: the ally's 9,000 never renders — only your own parse shows
    assert set(ov._row_widgets) == {"Me (You)"}
    assert "5,000" in ov._row_widgets["Me (You)"].total_lbl.text()
    assert ov._row_widgets["Me (You)"].rate_lbl.text() == "100.0%"
    # chips report your own totals, not the stray rival damage. The TOTAL now
    # rides the chip's sub-line: the /s rate is the focus number (2026-10-03).
    assert ov.chip_dmg.sub_lbl.text() == "5,000 total"
    assert ov.chip_dmg.val_lbl.text().endswith("/s")
    assert ov.chip_taken.sub_lbl.text() == "0 total"

    # grouped again -> the rival row returns with full group totals
    ov.model.roster = GroupSnapshot(group=0x5000, members=[
        GroupMember(player=0, hero=ALLY, name="Alice"),
        GroupMember(player=0, hero=PA, name="Me", is_me=True),
    ])
    _tick(ov)
    assert set(ov._row_widgets) == {"Me (You)", "Brit"}
    assert ov.chip_dmg.sub_lbl.text() == "14,000 total"

    # roster read fails / unattached -> rows keep showing (unknown state)
    ov.model.roster = None
    ov.model.group_roster = lambda: None
    _tick(ov)
    assert set(ov._row_widgets) == {"Me (You)", "Brit"}
    assert ov.chip_dmg.sub_lbl.text() == "14,000 total"

    # inside a dungeon/rift party members always show, even when the game
    # reports a solo roster (dungeons group everyone in the instance)
    ov.model.group_roster = lambda: ov.model.roster
    ov.model.roster = GroupSnapshot(group=0x5000, members=[], solo=True)
    # Zoning in has its own feature since v0.3.6 - auto-reset archives the
    # open-world run and starts a fresh meter. This test is about the SOLO
    # filter, so hold that unrelated reset off and assert the parse survives.
    ov.tracker.auto_reset_zone = False
    ov.model.is_dungeon = True
    _tick(ov)
    assert set(ov._row_widgets) == {"Me (You)", "Brit"}
    assert ov.chip_dmg.sub_lbl.text() == "14,000 total"
    ov.model.roster = None
    ov.model.group_roster = lambda: None
    ov.model.is_dungeon = False


def test_instance_idle_auto_loads_all_roster_members(ov):
    """Inside a dungeon/rift while idle (between pulls) the whole
    pre-registered party shows on the meter — a 0-stat member who never
    acted appears too (auto-loaded from the roster). Once a hit lands
    (combat), the activity filter drops the idle members again."""
    m = ov.model
    m.is_dungeon = True
    m.roster = GroupSnapshot(group=0x5000, members=[
        GroupMember(name="Milo", hero=0x9999, player=0x8888),
    ])
    m._units = [_hero(PA), _foe(FOE)]   # Milo is NOT in the scene
    _tick(ov)

    assert set(ov._row_widgets) == {"Me (You)", "Milo"}
    assert ov._row_widgets["Milo"].total_lbl.text() == "0 DPS"
    assert not ov._row_widgets["Milo"].isHidden()

    # a hit lands -> combat -> idle members drop off the meter
    m.damage.push(DamageEvent(amount=500.0, skill="Sword_Base_Attack",
                              source_addr=PA, target_addr=FOE,
                              t=time.time()))
    _tick(ov)
    assert ov._row_widgets["Milo"].isHidden()
    assert not ov._row_widgets["Me (You)"].isHidden()

    # open world (no instance): idle members are never auto-loaded
    ov.model.is_dungeon = False
    _tick(ov)
    assert ov._row_widgets["Milo"].isHidden()


def test_solo_status_helper_rules():
    """The shared solo detector: unknown / solo / grouped / instance."""
    from farever_companion.core.dps_tracker import solo_status

    m = _Model()
    assert solo_status(m) is None            # no roster -> unknown
    assert solo_status(None) is None

    m.roster = GroupSnapshot(group=0x5000, members=[], solo=True)
    assert solo_status(m) is True            # the game says solo

    m.roster = GroupSnapshot(group=0x5000, members=[
        GroupMember(player=0, hero=ALLY, name="Alice"),
        GroupMember(player=0, hero=PA, name="Me", is_me=True),
    ])
    assert solo_status(m) is False           # grouped

    m.roster = None
    m.is_dungeon = True
    assert solo_status(m) is False           # instances always show everyone
    m.is_dungeon = False
    m.is_rift = True
    assert solo_status(m) is False


def test_solo_only_toggle_off_shows_ally_rows(ov):
    """dps_solo_only=False: the meter keeps showing ungrouped rivals."""
    m = ov.model
    m.roster = GroupSnapshot(group=0x5000, members=[], solo=True)
    m._units = [_hero(PA), _hero(ALLY), _foe(FOE)]
    t0 = time.time()
    m.damage.push(DamageEvent(amount=5000.0, skill="Sword_Spin",
                              source_addr=PA, target_addr=FOE, t=t0))
    m.damage.push(DamageEvent(amount=9000.0, skill="Ally_BigHit",
                              source_addr=ALLY, target_addr=FOE, t=t0 + 0.01))

    # default (on): solo filtering applies
    _tick(ov)
    assert set(ov._row_widgets) == {"Me (You)"}

    # toggle off -> ally row and group totals come back
    ov.s.dps_solo_only = False
    _tick(ov)
    assert set(ov._row_widgets) == {"Me (You)", "Brit"}
    assert ov.chip_dmg.sub_lbl.text() == "14,000 total"


def test_solo_button_on_overlay_syncs_with_settings(ov):
    """The Solo button lives on the overlay control bar, mirrors
    dps_solo_only, and flipping either side keeps both in sync."""
    assert ov.solo_btn is not None
    assert ov.solo_btn.isChecked() is True          # default: solo on

    # overlay button -> settings persist
    ov.solo_btn.setChecked(False)
    ov._toggle_solo()
    assert ov.s.dps_solo_only is False

    # external settings change -> button re-syncs on the next tick
    ov.s.dps_solo_only = True
    _tick(ov)
    assert ov.solo_btn.isChecked() is True


def test_solo_button_auto_disables_inside_dungeon_and_rearms_outside(ov):
    """Solo only applies in the open world: inside a dungeon/rift the button
    greys out and unchecks (the saved preference is untouched), and re-arms
    from the setting once back outside."""
    m = ov.model
    assert ov.solo_btn.isEnabled() is True

    m.is_dungeon = True
    _tick(ov)
    assert ov.solo_btn.isEnabled() is False
    assert ov.solo_btn.isChecked() is False
    assert getattr(ov.s, "dps_solo_only", True) is True   # preference kept

    # back in the open world, ungrouped -> Solo re-arms from the setting
    m.is_dungeon = False
    m.roster = GroupSnapshot(group=0x5000, members=[], solo=True)
    _tick(ov)
    assert ov.solo_btn.isEnabled() is True
    assert ov.solo_btn.isChecked() is True

    # user turns it off -> stays off outside instances
    ov.s.dps_solo_only = False
    _tick(ov)
    assert ov.solo_btn.isEnabled() is True
    assert ov.solo_btn.isChecked() is False

    # inside a rift the button is inert again, even with the setting on
    ov.s.dps_solo_only = True
    m.is_rift = True
    _tick(ov)
    assert ov.solo_btn.isEnabled() is False
    assert ov.solo_btn.isChecked() is False


def test_solo_auto_off_inside_dungeon_shows_party_rows(ov):
    """Even with solo-only ON, entering a dungeon shows everyone: the solo
    filter never hides party members inside an instance."""
    m = ov.model
    m.is_dungeon = True
    m.roster = GroupSnapshot(group=0x5000, members=[], solo=True)
    m._units = [_hero(PA), _hero(ALLY), _foe(FOE)]
    t0 = time.time()
    m.damage.push(DamageEvent(amount=5000.0, skill="Sword_Spin",
                              source_addr=PA, target_addr=FOE, t=t0))
    m.damage.push(DamageEvent(amount=9000.0, skill="Ally_BigHit",
                              source_addr=ALLY, target_addr=FOE, t=t0 + 0.01))
    _tick(ov)
    assert set(ov._row_widgets) == {"Me (You)", "Brit"}
    assert ov._row_widgets["Brit"].isVisible()
    assert ov.solo_btn.isChecked() is False

    # leaving the dungeon (still ungrouped) applies solo again: only me shows
    m.is_dungeon = False
    _tick(ov)
    assert ov._row_widgets["Me (You)"].isVisible()
    brit = ov._row_widgets.get("Brit")
    assert brit is None or not brit.isVisible()
    assert ov.solo_btn.isChecked() is True


def test_dead_player_shows_skull(ov):
    """A player at 0 HP in the scene scan gets a skull on their DPS row;
    it clears once they're alive again."""
    m = ov.model
    m.roster = GroupSnapshot(group=0x5000, members=[
        GroupMember(player=0, hero=ALLY, name="Brit"),
        GroupMember(player=0, hero=PA, name="Me", is_me=True),
    ])
    m._units = [_hero(PA), _hero(ALLY), _foe(FOE)]
    t0 = time.time()
    m.damage.push(DamageEvent(amount=5000.0, skill="Sword_Spin",
                              source_addr=PA, target_addr=FOE, t=t0))
    m.damage.push(DamageEvent(amount=9000.0, skill="Ally_BigHit",
                              source_addr=ALLY, target_addr=FOE, t=t0 + 0.01))
    _tick(ov)
    assert "Brit" in ov._row_widgets
    assert not ov._row_widgets["Brit"].dead_lbl.isVisible()
    assert not ov._row_widgets["Me (You)"].dead_lbl.isVisible()

    # ally goes down -> skull appears (only on the dead row)
    m._units = [_hero(PA), _hero(ALLY, hp=0.0), _foe(FOE)]
    _tick(ov)
    assert ov._row_widgets["Brit"].dead_lbl.isVisible()
    assert not ov._row_widgets["Me (You)"].dead_lbl.isVisible()

    # respawn -> skull clears
    m._units = [_hero(PA), _hero(ALLY, hp=1000.0), _foe(FOE)]
    _tick(ov)
    assert not ov._row_widgets["Brit"].dead_lbl.isVisible()


def test_pin_me_puts_local_player_top_when_enabled(ov):
    """The local player's row pins to the TOP OF THE LIST when dps_pin_me is
    on (default), but the rank number still shows the player's real DPS
    position — Brit out-damages me, so I'm #2 even while my row sits first.
    Turning the toggle off restores true rank order in the layout too."""
    m = ov.model
    m.roster = GroupSnapshot(group=0x5000, members=[
        GroupMember(player=0, hero=ALLY, name="Brit"),
        GroupMember(player=0, hero=PA, name="Me", is_me=True),
    ])
    m._units = [_hero(PA), _hero(ALLY), _foe(FOE)]
    t0 = time.time()
    m.damage.push(DamageEvent(amount=5000.0, skill="Sword_Spin",
                              source_addr=PA, target_addr=FOE, t=t0))
    m.damage.push(DamageEvent(amount=9000.0, skill="Ally_BigHit",
                              source_addr=ALLY, target_addr=FOE, t=t0 + 0.01))
    rows_lay = ov.dps_top_box.rows

    # default on: my row is pinned first in the layout, but the number shows
    # my real rank (2 — Brit out-DPS'd me), never a fake "1"
    _tick(ov)
    assert ov._row_widgets["Me (You)"].name_lbl._full.startswith("2 · Me (You)")
    assert ov._row_widgets["Brit"].name_lbl._full.startswith("1 · Brit")
    assert (rows_lay.indexOf(ov._row_widgets["Me (You)"])
            < rows_lay.indexOf(ov._row_widgets["Brit"]))

    # off: true rank order in the layout too (Brit #1 on top)
    ov.s.dps_pin_me = False
    _tick(ov)
    assert ov._row_widgets["Brit"].name_lbl._full.startswith("1 · Brit")
    assert ov._row_widgets["Me (You)"].name_lbl._full.startswith("2 · Me (You)")
    assert (rows_lay.indexOf(ov._row_widgets["Brit"])
            < rows_lay.indexOf(ov._row_widgets["Me (You)"]))


# --- per-skill heal/damage channels in the overlay rows ---------------------

def _push_heal(m, skill: str, amount: float, t: float):
    m.damage.push(DamageEvent(amount=-amount, skill=skill, kind="heal",
                              source_addr=PA, target_addr=PA, t=t))


def test_overlay_row_damage_mode_shows_damage_channel_only(ov):
    """Damage mode shows only the damage channel of a hybrid skill."""
    m = ov.model
    m._units = [_hero(PA), _foe(FOE)]
    t0 = time.time()
    m.damage.push(DamageEvent(amount=500.0, skill="Judgment",
                              source_addr=PA, target_addr=FOE, t=t0))
    _push_heal(m, "Judgment", 300.0, t0 + 0.01)
    _tick(ov)

    row = ov._row_widgets["Me (You)"]
    row.show()
    QtWidgets.QApplication.processEvents()
    stat = row._skill_row_widgets["Judgment"].findChild(
        QtWidgets.QLabel, "SkillTotal")
    assert stat is not None
    assert stat.text().startswith("500")
    assert "300" not in stat.text()           # heal NOT merged into damage


def test_overlay_row_skill_rows_resort_live_when_rankings_flip(ov):
    """Skill rows re-sort live when rankings flip mid-fight."""
    m = ov.model
    m._units = [_hero(PA), _foe(FOE)]
    t0 = time.time()

    def _layout_order(row) -> list[str]:
        return [row.skills_lay.itemAt(i).widget().objectName()
                for i in range(row.skills_lay.count())
                if row.skills_lay.itemAt(i).widget() is not None]

    # first tick: Sword_Spin (5000) ranks above Axe (4000)
    m.damage.push(DamageEvent(amount=5000.0, skill="Sword_Spin",
                              source_addr=PA, target_addr=FOE, t=t0))
    m.damage.push(DamageEvent(amount=4000.0, skill="Axe_Base_Attack",
                              source_addr=PA, target_addr=FOE, t=t0 + 0.01))
    _tick(ov)
    row = ov._row_widgets["Me (You)"]
    row.show()
    QtWidgets.QApplication.processEvents()
    row._skill_row_widgets["Sword_Spin"].setObjectName("row_sword")
    row._skill_row_widgets["Axe_Base_Attack"].setObjectName("row_axe")
    assert _layout_order(row)[:2] == ["row_sword", "row_axe"]

    # next tick: Axe (9000) now beats Sword (1000) -> must reorder
    m.damage.push(DamageEvent(amount=8000.0, skill="Axe_Base_Attack",
                              source_addr=PA, target_addr=FOE, t=t0 + 0.02))
    _tick(ov)
    assert _layout_order(row)[:2] == ["row_axe", "row_sword"]

    # and the numbers on each row reflect the new totals
    stat_axe = row._skill_row_widgets["Axe_Base_Attack"].findChild(
        QtWidgets.QLabel, "SkillTotal")
    assert stat_axe is not None and "12,000" in stat_axe.text()


def test_overlay_row_healing_mode_shows_heal_channel_only(ov):
    """Healing mode shows only the heal channel of a hybrid skill."""
    m = ov.model
    m._units = [_hero(PA), _foe(FOE)]
    t0 = time.time()
    m.damage.push(DamageEvent(amount=500.0, skill="Judgment",
                              source_addr=PA, target_addr=FOE, t=t0))
    _push_heal(m, "Judgment", 300.0, t0 + 0.01)
    _tick(ov)

    ov.mode_pill._btns["HEAL"].click()
    _tick(ov)
    row = ov._row_widgets["Me (You)"]
    row.show()
    QtWidgets.QApplication.processEvents()
    stat = row._skill_row_widgets["Judgment"].findChild(
        QtWidgets.QLabel, "SkillTotal")
    assert stat is not None
    assert stat.text().startswith("300")
    assert "heals" in stat.text()


def test_overlay_skill_row_has_pct_column_and_fixed_name(ov):
    """Skill rows use fixed columns (name/bar/amount/%), never prose."""
    m = ov.model
    m._units = [_hero(PA), _foe(FOE)]
    t0 = time.time()
    m.damage.push(DamageEvent(amount=5000.0, skill="Sword_Spin",
                              source_addr=PA, target_addr=FOE, t=t0))
    m.damage.push(DamageEvent(amount=4000.0, skill="Axe_Base_Attack",
                              source_addr=PA, target_addr=FOE, t=t0 + 0.01))
    _tick(ov)
    row = ov._row_widgets["Me (You)"]
    row.show()
    QtWidgets.QApplication.processEvents()
    sw = row._skill_row_widgets["Sword_Spin"]
    pct = sw.findChild(QtWidgets.QLabel, "SkillPct")
    stat = sw.findChild(QtWidgets.QLabel, "SkillTotal")
    bar = sw.findChild(QtWidgets.QProgressBar, "SkillBar")
    assert pct is not None and pct.text() == "56%"   # 5000 of 9000
    assert stat is not None and stat.text() == "5,000 · 1"
    assert bar is not None and bar.value() == 55
    # the name is a fixed-width eliding label: row geometry is never
    # text-driven, so skill re-sorts can't swash the overlay
    capped = [w for w in sw.findChildren(QtWidgets.QLabel)
              if w.maximumWidth() == 118]
    assert capped, "skill name must be a fixed-width eliding label"


def test_overlay_player_row_uses_total_and_rate_columns(ov):
    """Player rows show the live DPS/HPS rate; the share lives in the % column."""
    m = ov.model
    m._units = [_hero(PA), _foe(FOE)]
    t0 = time.time()
    m.damage.push(DamageEvent(amount=21307.0, skill="Conduit: Shard",
                              source_addr=PA, target_addr=FOE, t=t0))
    _tick(ov)
    row = ov._row_widgets["Me (You)"]
    row.show()
    QtWidgets.QApplication.processEvents()
    # the primary column shows the live rate (21,307 over <1s), never prose
    assert "21,307" in row.total_lbl.text()
    assert row.total_lbl.text().endswith("DPS")
    assert "%" not in row.total_lbl.text()     # share lives in the % column
    assert row.rate_lbl.text() == "100.0%"     # sole-owner group share
    assert row.rate_lbl.width() == 46          # fixed share column

    # healing mode fills the same columns from the heal channel
    m2 = ov.model
    t1 = time.time()
    m2.damage.push(DamageEvent(amount=-6000.0, skill="Holy_Beam", kind="heal",
                               source_addr=PA, target_addr=PA, t=t1))
    _tick(ov)
    ov.mode_pill._btns["HEAL"].click()
    _tick(ov)
    row2 = ov._row_widgets["Me (You)"]
    assert "6,000" in row2.total_lbl.text()
    assert row2.total_lbl.text().endswith("HPS")


# --- view-distance reminder removed -----------------------------------------

def test_view_distance_reminder_removed(ov):
    """The View Distance reminder is gone from all overlay labels."""
    assert not hasattr(ov, "vd_lbl")

    m = ov.model
    m._units = [_hero(PA), _hero(ALLY), _foe(FOE)]
    m.damage.push(DamageEvent(amount=50.0, skill="Sword_Base_Attack",
                              source_addr=PA, target_addr=FOE, t=time.time()))
    _tick(ov)                       # group combat tick must not raise
    assert "View Distance" not in ov.state_lbl.text()
    # footer (src_state/timing line) is gone entirely
    assert not hasattr(ov, "src_state_lbl")
    assert not hasattr(ov, "timing_lbl")

def test_top_dps_overlay_carries_no_hover_tooltips(ov):
    """The Top DPS HUD shows no mouse-over tooltips — the controls are
    self-explanatory and a hover bubble over a game overlay is just noise
    (the same rule the Items page follows). The only text left in a tooltip
    is ElideLabel's own "reveal the truncated text" mirror, so the GAME badge
    must never carry the old isInCombat/combatId debug dump."""
    m = ov.model
    m._units = [_hero(PA), _hero(ALLY), _foe(FOE)]
    m.damage.push(DamageEvent(amount=50.0, skill="Sword_Base_Attack",
                              source_addr=PA, target_addr=FOE, t=time.time()))
    ov.tracker.combat_state = {"in_combat": True, "combat_id": 7,
                               "combat_start": 100.0, "combat_end": 0.0}
    _tick(ov)

    # controls: no hover text at all
    assert ov.solo_btn.toolTip() == ""
    assert ov.pause_btn.toolTip() == ""
    assert ov.reset_btn.toolTip() == ""

    # the GAME badge: tooltip is the elide mirror only, never the engine dump
    assert ov.game_lbl.toolTip() == ov.game_lbl._full
    assert "isInCombat" not in ov.game_lbl.toolTip()
    assert "combatId" not in ov.game_lbl.toolTip()

    # per-player rows: the skull and the DPS total carry no hover text
    assert ov._row_widgets, "expected live rows to assert against"
    for row in ov._row_widgets.values():
        assert row.total_lbl.toolTip() == ""
        assert row.dead_lbl.toolTip() == ""


def test_game_combat_badge_shows_game_state(ov):
    """The GAME badge mirrors game combat state, duration, and reset."""
    ov._update_game_combat_lbl()
    assert ov.game_lbl._full == ""            # no snapshot yet

    ov.tracker.combat_state = {"in_combat": True, "combat_id": 7,
                               "combat_start": 100.0, "combat_end": 0.0}
    ov._update_game_combat_lbl()
    assert "IN COMBAT" in ov.game_lbl._full

    # with a wall anchor the badge carries a LIVE running timer (0.1 s
    # update cadence), not a static label
    ov.tracker._combat_wall_start_at = time.time() - 5.0
    ov._update_game_combat_lbl()
    assert "IN COMBAT" in ov.game_lbl._full
    assert "5." in ov.game_lbl._full        # ~5 s elapsed, ticking
    ov.tracker._combat_wall_start_at = 0.0

    ov.tracker.combat_state = {"in_combat": False, "combat_id": 7,
                               "combat_start": 100.0, "combat_end": 112.5}
    ov._update_game_combat_lbl()
    assert "12.5s" in ov.game_lbl._full       # game-recorded duration

    ov.tracker.combat_state = {"in_combat": False, "combat_id": 7,
                               "combat_start": 0.0, "combat_end": 0.0}
    ov._update_game_combat_lbl()
    assert "GAME out" in ov.game_lbl._full

    # manual reset clears the badge back to its idle state
    ov.reset()
    assert ov.game_lbl._full == ""


def test_a_dummy_lull_paints_the_meter_ready_and_the_game_badge_out(ov):
    """End to end through the REAL Top DPS overlay: a training dummy keeps the
    client "in combat" at any range inside its yard, so a test that stopped
    landing hits used to leave the header reading IN COMBAT — and the GAME badge
    agreeing — off a fight that was over. The idle re-arm ends it, and the meter
    paints READY with "GAME out" instead of a phantom combat."""
    m = ov.model
    m.combat_state_value = {"in_combat": True, "combat_id": 7,
                            "combat_start": 100.0, "combat_end": 0.0}
    m._units = [_hero(PA), training_dummy(FOE, uid="Dummy", hp=100000.0)]
    m.damage.push(DamageEvent(amount=500.0, skill="Sword_Base_Attack",
                              source_addr=PA, target_addr=FOE, t=time.time()))
    _tick(ov)
    # the test is running, and both the header and the GAME badge read combat
    assert ov.tracker.in_dummy_fight is True
    assert ov.state_lbl.text() == "IN COMBAT"
    assert "IN COMBAT" in ov.game_lbl._full

    # No hit for longer than the Auto Re-arm wait: the test ends itself. The
    # wall clock is left armed so the check below is about the suppression and
    # not merely an unanchored timer.
    ov.tracker._combat_wall_start_at = time.time() - 3.0
    ov.tracker.dummy_session.last_event_time = time.time() - 6.0
    _tick(ov)

    assert ov.tracker.in_dummy_fight is False
    assert ov.tracker.armed_fight() is None       # no phantom armed clock
    assert ov.state_lbl.text() == "READY"
    assert "GAME out" in ov.game_lbl._full


def test_skill_breakdown_caps_globally_then_expands(ov):
    """Skill rows cap globally and expand/collapse via the more toggle."""
    m = ov.model
    m._units = [_hero(PA), _foe(FOE)]
    t0 = time.time()
    for i, amt in enumerate((10000, 9000, 8000, 7000, 6000, 5000,
                             4000, 3000, 2000, 1000)):
        m.damage.push(DamageEvent(amount=amt, skill=f"Skill_{i + 1}",
                                  source_addr=PA, target_addr=FOE,
                                  t=t0 + i * 0.01))
    _tick(ov)
    row = ov._row_widgets["Me (You)"]
    row.show()
    QtWidgets.QApplication.processEvents()

    def _layout_order():
        return [row.skills_lay.itemAt(i).widget().objectName()
                for i in range(row.skills_lay.count())
                if row.skills_lay.itemAt(i).widget() is not None]

    # default view: only the top-8 widgets exist and are visible
    assert set(row._skill_row_widgets) == {f"Skill_{i}" for i in range(1, 9)}
    shown = [sw for sw in row._skill_row_widgets.values() if sw.isVisible()]
    assert len(shown) == GLOBAL_SKILL_CAP
    assert row._more_btn.isVisible()
    assert "2 more" in row._more_btn.text()
    for sid, sw in row._skill_row_widgets.items():
        sw.setObjectName(f"sk_{sid}")
    assert _layout_order()[:8] == [f"sk_Skill_{i}" for i in range(1, 9)]
    assert _layout_order()[-1] == "MoreSkills"

    # each shown row still carries its TOTAL damage in the stat column
    stat = row._skill_row_widgets["Skill_1"].findChild(
        QtWidgets.QLabel, "SkillTotal")
    assert stat is not None and stat.text().startswith("10,000")

    # expand -> the remaining skills appear, ranked below the top 8
    row._more_btn.click()
    _tick(ov)
    QtWidgets.QApplication.processEvents()
    assert len(row._skill_row_widgets) == 10
    shown = [sw for sw in row._skill_row_widgets.values() if sw.isVisible()]
    assert len(shown) == 10
    assert "fewer" in row._more_btn.text()
    stat10 = row._skill_row_widgets["Skill_10"].findChild(
        QtWidgets.QLabel, "SkillTotal")
    assert stat10 is not None and stat10.text().startswith("1,000")

    # collapse again -> back to the top 8
    row._more_btn.click()
    _tick(ov)
    QtWidgets.QApplication.processEvents()
    shown = [sw for sw in row._skill_row_widgets.values() if sw.isVisible()]
    assert len(shown) == GLOBAL_SKILL_CAP
    assert "2 more" in row._more_btn.text()


def test_skill_list_has_no_expand_toggle_below_cap(ov):
    """At most the cap, skill lists get no expand toggle."""
    m = ov.model
    m._units = [_hero(PA), _foe(FOE)]
    t0 = time.time()
    for i, amt in enumerate((5000, 4000)):
        m.damage.push(DamageEvent(amount=amt, skill=f"Axe_{i}",
                                  source_addr=PA, target_addr=FOE,
                                  t=t0 + i * 0.01))
    _tick(ov)
    row = ov._row_widgets["Me (You)"]
    row.show()
    QtWidgets.QApplication.processEvents()
    assert len(row._skill_row_widgets) == 2
    assert not row._more_btn.isVisible()
    assert row.skills_lay.indexOf(row._more_btn) == -1   # not in the layout


def _click_chevron(row):
    """Real click path toggling a row's per-player skill section."""
    ev = QtGui.QMouseEvent(QtCore.QEvent.MouseButtonPress,
                           QtCore.QPointF(1, 1), QtCore.Qt.LeftButton,
                           QtCore.Qt.LeftButton, QtCore.Qt.NoModifier)
    row.mousePressEvent(ev)


def test_skill_expand_state_survives_ticks_and_mode_switches(ov):
    """The expand choice survives re-sorts and mode switches."""
    m = ov.model
    m._units = [_hero(PA), _foe(FOE)]
    t0 = time.time()
    for i, amt in enumerate((10000, 9000, 8000, 7000, 6000, 5000,
                             4000, 3000, 2000, 1000)):
        m.damage.push(DamageEvent(amount=amt, skill=f"Skill_{i + 1}",
                                  source_addr=PA, target_addr=FOE,
                                  t=t0 + i * 0.01))
    _tick(ov)
    row = ov._row_widgets["Me (You)"]
    row.show()
    QtWidgets.QApplication.processEvents()

    row._more_btn.click()                     # expand to all 10
    _tick(ov)
    shown = [sw for sw in row._skill_row_widgets.values() if sw.isVisible()]
    assert len(shown) == 10
    assert ov._skills_show_all.get("Me (You)") is True   # mirrored out

    # a re-sort tick (more damage arriving) must not collapse the list
    m.damage.push(DamageEvent(amount=9000.0, skill="Skill_1",
                              source_addr=PA, target_addr=FOE, t=t0 + 0.5))
    _tick(ov)
    shown = [sw for sw in row._skill_row_widgets.values() if sw.isVisible()]
    assert len(shown) == 10
    assert "fewer" in row._more_btn.text()

    # DMG -> BOTH -> HEAL -> DMG keeps the expanded state alive
    ov._set_mode("BOTH")
    _tick(ov)
    assert row._show_all_skills is True
    ov._set_mode("HEAL")
    _tick(ov)
    assert row._show_all_skills is True   # remembered even with no heal rows
    ov._set_mode("DMG")
    _tick(ov)
    assert row._show_all_skills is True
    shown = [sw for sw in row._skill_row_widgets.values() if sw.isVisible()]
    assert len(shown) == 10
    assert "fewer" in row._more_btn.text()


def test_skill_expand_and_chevron_state_survive_row_rebuild(ov):
    """Expand and chevron choices survive a row-widget rebuild."""
    m = ov.model
    m._units = [_hero(PA), _foe(FOE)]
    t0 = time.time()
    for i, amt in enumerate((10000, 9000, 8000, 7000, 6000, 5000,
                             4000, 3000, 2000, 1000)):
        m.damage.push(DamageEvent(amount=amt, skill=f"Skill_{i + 1}",
                                  source_addr=PA, target_addr=FOE,
                                  t=t0 + i * 0.01))
    _tick(ov)
    row = ov._row_widgets["Me (You)"]
    row.show()
    QtWidgets.QApplication.processEvents()
    row._more_btn.click()                     # expand to all 10
    _click_chevron(row)                       # ... then collapse the row
    _tick(ov)
    assert row.expanded is False and not row.skills_box.isVisible()
    assert ov._row_collapsed["Me (You)"] is True
    assert ov._skills_show_all["Me (You)"] is True

    # simulate the rows being wiped and rebuilt from the session players
    ov._update_meters([], 0.0, 0.0, 0.0)      # empty-ranked branch clears rows
    assert not ov._row_widgets
    _tick(ov)
    row2 = ov._row_widgets["Me (You)"]
    row2.show()
    QtWidgets.QApplication.processEvents()
    assert row2._show_all_skills is True      # expand restored
    assert row2.expanded is False             # chevron choice restored
    assert not row2.skills_box.isVisible()

    # re-expanding the rebuilt row renders the FULL list at once
    _click_chevron(row2)
    _tick(ov)
    shown = [sw for sw in row2._skill_row_widgets.values() if sw.isVisible()]
    assert len(shown) == 10
    assert "fewer" in row2._more_btn.text()


def test_zero_ally_autocollapse_preserves_user_chevron(ov):
    """Zero-data auto-collapse never clobbers the user's chevron choice."""
    # user left this ally row expanded (default for allies is collapsed, so
    # pass it explicitly to simulate the choice)
    row = _PlayerRowWidget("Brit", is_me=False, expanded=True)
    row.show()  # top-level: makes the skills_box visibility asserts real
    p = PlayerParse(name="Brit", is_me=False)

    # zero ally -> transient auto-collapse, user state untouched
    row.update_stats(3, p, 10.0, 1000.0, 0.0)
    assert row._auto_collapsed is True
    assert not row.skills_box.isVisible()
    assert row.expanded is True

    # ally starts decoding -> re-opens (user never collapsed it)
    p.total_damage = 800.0
    row.update_stats(3, p, 10.0, 800.0, 0.0)
    assert row._auto_collapsed is False
    assert row.skills_box.isVisible()

    # ... but a row the user collapsed STAYS collapsed after data arrives
    _click_chevron(row)                       # user collapses
    assert row.expanded is False
    p.total_damage = 1200.0
    row.update_stats(3, p, 10.0, 1200.0, 0.0)
    assert row._auto_collapsed is False
    assert row.expanded is False
    assert not row.skills_box.isVisible()     # user choice wins


def _two_session_parses(ov):
    """(me, ally) PlayerParse rows with real skill data, straight from the
    tracker session. A grouped roster is set first so the tracker records
    ally events (its solo guard drops non-me events otherwise); the tests
    then drive _update_meters directly to choose what the meter shows."""
    m = ov.model
    m.roster = GroupSnapshot(group=0x5000, members=[
        GroupMember(player=0, hero=ALLY, name="Brit"),
        GroupMember(player=0, hero=PA, name="Me", is_me=True),
    ])
    m._units = [_hero(PA), _hero(ALLY), _foe(FOE)]
    t0 = time.time()
    for i, amt in enumerate((6000, 5000, 4000, 3000, 2000, 1000)):
        m.damage.push(DamageEvent(amount=amt, skill=f"MySkill_{i + 1}",
                                  source_addr=PA, target_addr=FOE,
                                  t=t0 + i * 0.01))
    for i, amt in enumerate((4000, 3500, 3000, 2500, 2000, 1500)):
        m.damage.push(DamageEvent(amount=amt, skill=f"AllySkill_{i + 1}",
                                  source_addr=ALLY, target_addr=FOE,
                                  t=t0 + 0.1 + i * 0.01))
    _tick(ov)
    s = ov.tracker.session
    return s.players["Me (You)"], s.players["Brit"]


def _visible_skill_rows(row) -> list:
    return [sw for sw in row._skill_row_widgets.values() if sw.isVisible()]


def test_grouped_meter_collapses_every_row_and_global_cap_shared(ov):
    """Once rival rows are on the meter, every row (including yours) starts
    collapsed; user-expanded rows still share the global skill cap."""
    me_p, ally_p = _two_session_parses(ov)
    dur = 10.0
    group_dmg = me_p.total_damage + ally_p.total_damage

    # not solo (two rows on the meter) -> every row starts collapsed
    ov._update_meters([me_p, ally_p], dur, group_dmg, 0.0)
    me_row = ov._row_widgets["Me (You)"]
    ally_row = ov._row_widgets["Brit"]
    me_row.show()
    ally_row.show()
    QtWidgets.QApplication.processEvents()
    assert me_row.expanded is False
    assert ally_row.expanded is False
    assert not me_row.skills_box.isVisible()
    assert not ally_row.skills_box.isVisible()

    # opening the top-ranked row gets the full global budget (6 <= 8 cap)
    _click_chevron(me_row)
    ov._update_meters([me_p, ally_p], dur, group_dmg, 0.0)
    QtWidgets.QApplication.processEvents()
    assert me_row.expanded is True
    assert len(_visible_skill_rows(me_row)) == 6
    assert not me_row._more_btn.isVisible()

    # opening the ally too: it gets the leftover budget: top 2 of its 6 skills
    _click_chevron(ally_row)
    ov._update_meters([me_p, ally_p], dur, group_dmg, 0.0)
    QtWidgets.QApplication.processEvents()
    assert ally_row.expanded is True
    assert len(_visible_skill_rows(ally_row)) == 2
    assert "4 more" in ally_row._more_btn.text()

    # expanding the ally's list overrides the global cap -> all 6 appear
    ally_row._more_btn.click()
    ov._update_meters([me_p, ally_p], dur, group_dmg, 0.0)
    QtWidgets.QApplication.processEvents()
    assert len(_visible_skill_rows(ally_row)) == 6
    assert "fewer" in ally_row._more_btn.text()


def test_skill_sections_follow_solo_and_group_scope(ov):
    """A solo meter (a single own row) auto-expands my skill breakdown; once
    rival rows share the meter every row collapses. No chevron was clicked,
    so the scope default applies live in both directions."""
    me_p, ally_p = _two_session_parses(ov)
    dur = 10.0
    me_dmg = me_p.total_damage

    # solo: only my own row -> skills expanded by default
    ov._update_meters([me_p], dur, me_dmg, 0.0)
    me_row = ov._row_widgets["Me (You)"]
    me_row.show()
    QtWidgets.QApplication.processEvents()
    assert me_row.expanded is True
    assert me_row.skills_box.isVisible()
    assert len(_visible_skill_rows(me_row)) == 6

    # not solo: the rival shares the meter and my skills collapse with it
    ov._update_meters([me_p, ally_p], dur, me_dmg + ally_p.total_damage, 0.0)
    ally_row = ov._row_widgets["Brit"]
    ally_row.show()
    QtWidgets.QApplication.processEvents()
    assert me_row.expanded is False
    assert not me_row.skills_box.isVisible()
    assert ally_row.expanded is False

    # solo again: the single own row re-expands on its own (no saved chevron)
    ov._update_meters([me_p], dur, me_dmg, 0.0)
    QtWidgets.QApplication.processEvents()
    assert me_row.expanded is True
    assert me_row.skills_box.isVisible()
    assert len(_visible_skill_rows(me_row)) == 6


def test_solo_expand_ignores_an_inactive_second_row(ov):
    """Solo with a second row carrying no parse (phantom Party_XXXX, idle
    pre-registered ally): my skills still auto-expand — the default counts
    ACTIVE parses, not total rows."""
    me_p, ally_p = _two_session_parses(ov)
    ally_p.total_damage = 0.0                    # no parse of its own
    assert ally_p.heals == 0.0
    ov._update_meters([me_p, ally_p], 10.0, me_p.total_damage, 0.0)
    me_row = ov._row_widgets["Me (You)"]
    me_row.show()
    QtWidgets.QApplication.processEvents()
    assert me_row.expanded is True
    assert me_row.skills_box.isVisible()


# --- per-row damage-type cells ----------------------------------------------

def _tagged_hits(ov, affinity, skill="Sword_Base_Attack", amount=100.0, n=4):
    """Push a few tagged damage hits as Me and tick the overlay."""
    m = ov.model
    m._units = [_hero(PA), _foe(FOE)]
    t0 = time.time()
    for i in range(n):
        m.damage.push(DamageEvent(amount=amount, skill=skill,
                                  source_addr=PA, target_addr=FOE,
                                  t=t0 + i * 0.01, affinity=affinity))
    _tick(ov)


def test_meter_rows_name_the_school_like_the_analysis_page(ov):
    """The meter's per-skill rows carry the TYPE cell the DPS Analysis page
    has: the school this skill deals, in the school's colour."""
    _tagged_hits(ov, "Fire")
    rows = ov._row_widgets["Me (You)"]._skill_row_widgets
    cell = rows["Sword_Base_Attack"]._type_lbl
    assert cell is not None
    assert cell.text() == "Fire"


def test_meter_rows_carry_one_damage_type_cell_only(ov):
    """The school name IS the damage type column — the merged PD/MD tag that
    used to sit beside it duplicated it (Physical is PD, Fire is MD), so no
    second cell is built. An untagged skill's cell stays blank."""
    _tagged_hits(ov, "Fire")
    rows = ov._row_widgets["Me (You)"]._skill_row_widgets
    row = rows["Sword_Base_Attack"]
    assert row._type_lbl.text() == "Fire"
    assert not hasattr(row, "_pdmd_lbl")
    _tagged_hits(ov, "", skill="Skill_2")
    assert rows["Skill_2"]._type_lbl.text() == ""


# --- capture-source badge on the title bar ----------------------------------
def test_target_label_never_doubles_emblem(ov):
    """A stored dummy target already carries 🎯 — the header must not prepend
    a second one (used to show '🎯 🎯 Training Dummy' on open)."""
    ov.tracker.session.target_name = "🎯 Training Dummy"
    ov.tracker.session.target_hp = 50.0
    ov.tracker.session.target_max_hp = 100.0
    _tick(ov)
    assert ov.target_lbl.text() == "🎯 Training Dummy"

    ov.tracker.session.target_name = "👑 Nightking"
    _tick(ov)
    assert ov.target_lbl.text() == "👑 Nightking"


def test_a_passive_skill_is_tagged_on_its_row():
    """A passive's damage arrives looking like a spell you cast.

    "Shattered Rage" sat on the meter as just another rotation entry, because
    nothing on the row said its ticks came from a passive (reported live
    2026-10-03). The sheet already knows - a passive is `nature` 5 - so the tag
    is a lookup, not a guess.
    """
    from farever_companion.ui.skill_row import NAME_MAX_CHARS, tagged_skill_name

    PASSIVE = "Warrior_Talent_ShatteredRage"
    assert tagged_skill_name(PASSIVE, "Shattered Rage") == "Shattered Rage (Passive)"

    # An ACTIVE skill is not tagged: Bonethrow is `nature` 2.
    assert tagged_skill_name("Axe_Boomerang_Skill1", "Bonethrow") == "Bonethrow"

    # Summons already carry their tag from attribution; both can be present and
    # neither may be doubled.
    assert tagged_skill_name("Staff_SummonDemon_PetHit (pet)",
                             "Summon Demon (pet)") == "Summon Demon (pet)"
    assert tagged_skill_name(PASSIVE, "Shattered Rage (Passive)") \
        == "Shattered Rage (Passive)"

    # A long name is capped, but the TAG is what survives: truncating after
    # tagging would silently eat it on exactly the names that need it.
    long_name = tagged_skill_name(PASSIVE, "An Extremely Long Passive Name Here",
                                  NAME_MAX_CHARS)
    assert len(long_name) <= NAME_MAX_CHARS
    assert long_name.endswith("(Passive)")

    # An id the sheet has never heard of is left exactly as it arrived.
    assert tagged_skill_name("No_Such_Skill", "Mystery") == "Mystery"


def test_an_item_proc_reads_differently_from_a_rotation_passive():
    """Both are passives, and the meter must not flatten them into one kind.

    Bloodrage Aura and Shattered Rage both arrive as ticks nobody pressed, and
    before this they both read "(Passive)". They are not the same thing: one
    comes from the axe in your hands, the other from your build. The tag names
    which, because that is the question a player asks when their damage jumps
    after a gear change.

    Every exclusion here is a case where the naive rule - "an item grants this
    skill" - is FALSE, not merely imprecise (measured over the shipped sheets
    by ai/workspace/buffy/probe_item_procs3.py), so each one is pinned by real
    sheet ids rather than by a mock.
    """
    from farever_companion.data import names, skills
    from farever_companion.ui.skill_row import skill_row_tag, tagged_skill_name

    # An item proc: a weapon passive, tagged as gear rather than as rotation.
    PROC = "Axe_Boomerang_Skill_Passive"
    assert names.skill_name(PROC) == "Bloodrage Aura"
    assert skills.item_proc_source(PROC)["name"] == "Cheese Moon"
    assert skill_row_tag(PROC) == "item proc"
    assert tagged_skill_name(PROC, "Bloodrage Aura") == "Bloodrage Aura (Item proc)"

    # A rotation passive: still "(Passive)", NOT "(Item proc)".
    TREE = "Warrior_Talent_ShatteredRage"
    assert skills.item_proc_source(TREE) is None
    assert tagged_skill_name(TREE, "Shattered Rage") == "Shattered Rage (Passive)"

    # EXCLUSION 1 - base attacks. Gear lists a weapon's whole rotation, so 63
    # base attacks are "granted by an item". Tagging them would put (Item proc)
    # on every swing the player makes.
    assert skills.item_proc_source("Sword_Base_Attack") is None
    assert tagged_skill_name("Sword_Base_Attack", "Sword Attack") \
        == "Sword Attack"

    # EXCLUSION 2 - actives/combos/powers. Same reason; only a Passive-nature
    # row can be a proc.
    assert skills.item_proc_source("Axe_Boomerang_Skill1") is None   # Bonethrow

    # EXCLUSION 3 - class talents that an augment sigil ALSO lists. Eleven
    # same-named "Sigil of Bet'Hatesht" rows grant one talent each, so the
    # reverse lookup finds them - but a talent in your TREE is a rotation
    # passive whether or not a sigil names it too.
    for T in ("Mage_Talent_HighVoltage", "Rogue_Talent_CheatDeath",
              "Warrior_Talent_SurgeOfViolence"):
        assert skills.item_proc_source(T) is None, T
        assert "(Item proc)" not in tagged_skill_name(T, "Some Talent")

    # EXCLUSION 4 - the <Type>_Upgrade passives are granted by the GEAR SYSTEM
    # and referenced by no item row, so the reverse lookup cannot find them.
    # They are still gear, and the resolver reaches them by the sheet's name.
    up = skills.item_proc_source("Axe_Upgrade")
    assert up and up["name"] == "Weapon Upgraded" and up["type"] == "Axe"

    # A proc's own TICKS arrive on a derived row no item lists, and must
    # inherit the tag - otherwise the proc reads untagged whenever the parent
    # never cast.
    TICK = "Axe_Boomerang_Skill_Passive_Status"
    assert skills.skill_type(TICK) == "Status", "the tick is not itself passive"
    assert skills.item_proc_source(TICK) is not None
    assert tagged_skill_name(TICK, "Bloodrage Aura") \
        == "Bloodrage Aura (Item proc)"

    # Idempotent: the label is rebuilt on every identity change, so a second
    # pass must not produce "(Item proc) (Item proc)".
    assert tagged_skill_name(PROC, "Bloodrage Aura (Item proc)") \
        == "Bloodrage Aura (Item proc)"


def test_summon_family_rows_are_tagged_as_summons():
    """Summon Bee Honey Bolt and its kin read as summons, not as your swings.

    Reported live 2026-10-03: SummonBee_HoneyBolt sits in the meter looking
    like an ordinary base attack the player pressed. Both spellings of the
    family have to match, because the shipped sheets use both - and the id
    alone misses the bee.
    """
    from farever_companion.data import names, skills
    from farever_companion.ui.skill_row import skill_row_tag, tagged_skill_name

    BEE = "SummonBee_HoneyBolt"
    assert names.skill_name(BEE) == "Summon Bee Honey Bolt"
    # The word is in BOTH here, so this row cannot tell the two rules apart.
    assert "ummon" in BEE and "ummon" in names.skill_name(BEE)
    assert skill_row_tag(BEE) == "summon"
    assert tagged_skill_name(BEE, "Summon Bee Honey Bolt") \
        == "Summon Bee Honey Bolt (Summon)"

    # Id-side only: the sheet names it "Demonic Discharge" / "Inner Demon".
    for sid in ("Staff_SummonDemon_Combo", "Staff_SummonDemon_Skill2"):
        assert skill_row_tag(sid) == "summon", sid

    # A summon passive keeps ONE tag - the item proc that outranks the summon
    # kind - so the row never reads "(Item proc) (Summon)".
    INNER = tagged_skill_name("Staff_SummonDemon_Passive", "Inner Demon")
    assert INNER == "Inner Demon (Item proc)"

    # WEAPON summons only (reported live 2026-10-03: "i only want weapon
    # summons"). A bare "summon" substring also matches BOSS encounters' own
    # scripts - Ulserous, Phrixes, Faerie, Cleodora - and calling those "your
    # summon" claimed your summon did damage the boss.
    for boss_row in ("Ulserous_Summon", "Phrixes_Demon_Summon",
                     "Faerie_Demon_Summon", "Cleodora_ChampionSummon"):
        assert not skills.is_summon(boss_row), boss_row
    assert skills.is_summon("Staff_SummonDemon_Combo")

    # "(pet)" is the STRONGER claim (the bridge saw the owner), so a pet row
    # keeps it and does not also get "(Summon)".
    assert tagged_skill_name("Staff_SummonDemon_Combo (pet)",
                             "Chaos Bolts (pet)") == "Chaos Bolts (pet)"
    assert tagged_skill_name("Staff_SummonDemon_Combo", "Chaos Bolts (Summon)") \
        == "Chaos Bolts (Summon)"      # idempotent

    # A derived row inherits through the ancestor walk, the same way an item
    # proc's ticks do.
    assert skill_row_tag("Staff_SummonDemon_Skill1_Status") == "summon"

    # Nothing that is not a summon gets the tag.
    assert skill_row_tag("Sword_Base_Attack") == ""
    assert skill_row_tag("No_Such_Skill") == ""


def test_an_iconless_skill_still_draws_a_glyph():
    """No atlas art must not mean no icon.

    Six of the twenty summon-family rows ship no art, SummonBee_HoneyBolt
    among them - and no catalog item grants that skill, so the weapon-icon
    fallback has nothing to resolve. The row previously fell to a "zap" glyph
    that resolves to a NULL pixmap, drawing nothing at all.
    """
    from farever_companion.data import icons
    from farever_companion.ui.skill_row import _skill_icon

    for sid in ("SummonBee_HoneyBolt", "No_Such_Skill_At_All"):
        pm = _skill_icon(sid).pixmap()
        assert not pm.isNull(), sid
        assert not icons.has_icon("skills", sid) and not icons.has_icon("items", sid)


def _bonethrow_parse():
    """A real parse of Bonethrow plus its own bleed, folded (see dps_data)."""
    from farever_companion.core.dps_data import CombatSession
    from farever_companion.data import names

    THROW = "Axe_Boomerang_Skill1"
    BLEED = f"{THROW}_Status"
    assert names.skill_name(THROW) == names.skill_name(BLEED) == "Bonethrow"
    s = CombatSession("Test", "trash")
    t0 = time.time()
    s.record_hit("Trash", FOE, 100.0, 100.0, 1000.0, skill_id=THROW,
                 skill_name="Bonethrow", when=t0)
    s.record_hit("Trash", FOE, 100.0, 100.0, 250.0, skill_id=BLEED,
                 skill_name="Bonethrow", when=t0 + 1)
    return s.players["You"], THROW, BLEED


def test_an_item_proc_row_names_the_piece_it_came_from():
    """The tag says WHICH gear, because that is the question the row can't
    otherwise answer.

    When damage moves, the player wants to know whether it was a gear change.
    A bare "(Item proc)" tells them the category; naming Cheese Moon tells them
    what to look at.
    """
    from farever_companion.core.dps_data import SkillParse
    from farever_companion.ui.skill_row import SkillRow

    PROC = "Axe_Boomerang_Skill_Passive"
    row = SkillRow(density="compact")
    row.set_skill(PROC, "Bloodrage Aura")
    sp = SkillParse(skill_id=PROC, name="Bloodrage Aura")
    sp.damage, sp.hit_count = 5000.0, 12
    sp.min_hit, sp.max_hit = 200.0, 900.0
    row.update(sp, share_pct=12.0)
    # 2026-10-03: the tooltip's "From your gear: <item>" line was REMOVED at
    # the user's request - it restated the row's own label inside a hover the
    # player opens for numbers. The item is still named in the skill popup's
    # gear section, which is where a player goes looking for it.
    assert "Cheese Moon" not in row.toolTip()
    assert "From your gear" not in row.toolTip()
    # The row still says what kind of thing this is, as a plain label.
    assert row._name_lbl._full == "Bloodrage Aura"
    assert "proc" in row._tag_lbl.text() and not row._tag_lbl.isHidden()

    # A rotation passive gets no gear line - there is no piece to name, and
    # inventing one would be worse than silence.
    tree = SkillRow(density="compact")
    tree.set_skill("Warrior_Talent_ShatteredRage", "Shattered Rage")
    tsp = SkillParse(skill_id="Warrior_Talent_ShatteredRage",
                     name="Shattered Rage")
    tsp.damage, tsp.hit_count = 5000.0, 12
    tsp.min_hit, tsp.max_hit = 200.0, 900.0
    tree.update(tsp, share_pct=12.0)
    assert "From your gear" not in tree.toolTip()
    row.deleteLater()
    tree.deleteLater()


def test_a_folded_sub_skill_is_a_nested_row_under_its_parent(ov):
    """Bonethrow's bleed gets its own row, NESTED - not just in the tooltip.

    The fold was invisible except on hover: the meter showed one Bonethrow
    and the 250 damage it had actually caused was only findable by hovering
    (reported live 2026-10-03). A breakdown nobody can see is not a
    breakdown, so the child is drawn as a real indented row.
    """
    me_p, THROW, BLEED = _bonethrow_parse()
    assert list(me_p.skills) == [THROW], "the child has no row of its own"

    ov._update_meters([me_p], 10.0, me_p.total_damage, 0.0)
    row = ov._row_widgets["You"]
    row.show()
    QtWidgets.QApplication.processEvents()

    key = f"{THROW}\x00{BLEED}"
    assert key in row._skill_row_widgets, "the bleed is a row of its own"
    child = row._skill_row_widgets[key]
    assert child.isVisible()
    # Nested: indented, directly under its parent, and the two rows are told
    # apart by name (they share the sheet's "Bonethrow").
    assert child._layout.contentsMargins().left() > 0
    assert row._skill_row_widgets[THROW]._layout.contentsMargins().left() == 0
    assert row.skills_lay.indexOf(child) == row.skills_lay.indexOf(
        row._skill_row_widgets[THROW]) + 1
    assert "Bonethrow" in child._name_lbl._full
    assert child._name_lbl._full != row._skill_row_widgets[THROW]._name_lbl._full
    # Its numbers are its OWN (hits and all), not just a slice of the parent.
    stat = child.findChild(QtWidgets.QLabel, "SkillTotal")
    assert stat is not None and stat.text().startswith("250")

    # The parent still reads as the whole truth - the nested row breaks the
    # skill down, it does not add to it.
    pstat = row._skill_row_widgets[THROW].findChild(
        QtWidgets.QLabel, "SkillTotal")
    assert pstat.text().startswith("1,250")


def test_a_folded_sub_skill_row_is_not_drawn_on_the_heal_board(ov):
    """`subskills` is the damage channel only.

    Nested under a healing row a child would show 250 damage ranked against a
    heal total - a number from the wrong channel, under a skill that may not
    even heal.
    """
    me_p, THROW, BLEED = _bonethrow_parse()
    me_p.heals = 900.0
    for sp in me_p.skills.values():
        sp.heals = 900.0
        sp.heal_count = 2
        sp.min_heal, sp.max_heal = 400.0, 500.0

    ov._set_mode("HEAL")
    ov._update_meters([me_p], 10.0, me_p.total_damage, me_p.heals)
    row = ov._row_widgets["You"]
    row.show()
    QtWidgets.QApplication.processEvents()

    assert f"{THROW}\x00{BLEED}" not in row._skill_row_widgets
    assert THROW in row._skill_row_widgets


def test_the_target_line_says_boss_and_trash_when_both_are_taking_damage(ov):
    """Dropping to adds during a boss fight must not read as tracking the boss.

    The boss board wins the session choice while a boss is engaged (see
    `DpsTracker.session_for`), so the line named the boss even while the player
    was hitting its adds — reported live 2026-10-03 as "it is showing the boss
    as the target".

    Naming it honestly beats flipping the whole board: the boss is still the
    session, so its HP bar still tracks the boss, but the label says the boss
    AND its trash are both being hit. Flashing the name on every damage tick
    would be worse than either answer.
    """
    tr = ov.tracker
    ov.model.is_dungeon = True
    # A boss the DATA layer knows: an unknown id in an undeclared instance is
    # an engine-only claim, and those are deliberately left UNCROWNED now
    # (live 2026-10-03 - a 👑 on a hard/heroic trash mob), so this test uses a
    # real unit id to exercise the real thing it is about.
    tr.model._units = [_hero(PA), _foe(BOSS, uid="DemonSuperElite", hp=100000.0,
                                       is_boss=True)]
    tr.update()
    tr.model.damage.push(DamageEvent(amount=500.0, skill="Sword_Base_Attack",
                                     source_addr=PA, target_addr=BOSS,
                                     t=time.time()))
    tr.update()
    _tick(ov)
    assert ov.target_lbl.text() == "👑 Nightking Maat Demon", "boss only"

    # adds take damage too -> the label says so, and the bar still reads boss
    tr.model.damage.push(DamageEvent(amount=250.0, skill="Sword_Base_Attack",
                                     source_addr=PA, target_addr=FOE,
                                     t=time.time()))
    tr.update()
    assert tr.boss_adds_session.group_damage == 250.0, "adds took damage"
    _tick(ov)
    assert ov.target_lbl.text() == "👑 Nightking Maat Demon + trash"
    assert "100,000" in ov.target_hp_lbl.text()

    # not a boss fight -> never decorated
    tr.in_boss_fight = False
    _tick(ov)
    assert "+ trash" not in ov.target_lbl.text()


def test_the_pre_boss_trash_names_the_boss_that_is_coming(ov):
    """The instance DECLARES its boss, and that boss is loaded from the start,
    so the meter holds the engagement back until the trash is cleared (see
    DpsTrackerTick._scan_foes). Without a label the target line read
    "Target: None" through every pre-boss pull even though the instance had
    already told us exactly which boss was waiting."""
    tr = ov.tracker
    ov.model.is_dungeon = True
    tr.session.kind = "trash"
    # The tick's own scan re-derives `_declared_boss` from the scene every
    # frame (and a fake scene declares nothing), so drive the label directly.
    tr._declared_boss = "Nepsilon"
    ov._update_target_lbl(tr.session)
    assert ov.target_lbl.text() == "👑 Nepsilon · next"
    assert ov.target_bar.value() == 0
    assert ov.target_hp_lbl.text() == ""

    # Once the real fight starts the live target owns the line again.
    tr.in_boss_fight = True
    ov._update_target_lbl(tr.session)
    assert ov.target_lbl.text() == "Target: None"

    # Outside an instance there is no declared boss to announce.
    tr.in_boss_fight = False
    ov.model.is_dungeon = False
    ov._update_target_lbl(tr.session)
    assert ov.target_lbl.text() == "Target: None"

    # A boss fight / dummy test never announces an "upcoming" boss either.
    ov.model.is_dungeon = True
    tr.session.kind = "boss"
    ov._update_target_lbl(tr.session)
    assert ov.target_lbl.text() == "Target: None"


def test_stale_badge_click_rescans(ov):
    """The badge is the capture diagnostic AND the unstick button.

    Clicking it while stuck must force a real rescan (and never raise); clicking
    a HEALTHY badge must write the report and nothing else — a rescan on a live
    stream would cost the reader a re-derive for no reason, which is exactly
    what the old "clickable only while stuck" rule protected.
    """
    ov.model.damage._status = "silent"
    ov.model.damage.damage_stale = True    # stale pool: badge reads ⚠
    _tick(ov)
    assert ov.cap_badge.text() == "⚠"
    assert callable(ov.cap_badge.on_click)
    before = ov.model.damage.rehunt_calls
    ov.cap_badge.on_click()
    assert ov.model.damage.rehunt_calls == before + 1     # it really rescanned

    ov.model.damage._status = "live"       # healthy stream: report only
    ov.model.damage.damage_stale = False
    _tick(ov)
    assert ov.cap_badge.text() == "LIVE"
    assert callable(ov.cap_badge.on_click)
    before = ov.model.damage.rehunt_calls
    ov.cap_badge.on_click()                 # must not raise
    assert ov.model.damage.rehunt_calls == before


def test_the_capture_report_names_the_stage_the_events_stop_at(ov):
    """A board that reads zero has at least three causes — nothing arrived,
    nothing decoded, attribution dropped it — and the engine status cannot tell
    them apart. The report walks the path in order, so the first zero IS the
    diagnosis, and says so in words as well as numbers."""
    from farever_companion.ui.dps_source_text import event_path_report

    m = ov.model
    # An ally in the roster keeps the Solo Open-World display filter out of the
    # way: it would (correctly) discard a stranger's hit before attribution,
    # which is the "filtered" bucket, not the drop this test is about.
    m._units = [_hero(PA), _hero(ALLY), _foe(FOE, uid="Trash_Pig")]
    report = "\n".join(event_path_report(m))
    assert "NOTHING ARRIVED" in report          # nothing has been pushed yet
    for stage in ("arrived", "decoded", "pulled", "kept", "dropped", "verdict"):
        assert stage in report

    # one real hit, correctly attributed: the path is working
    m.damage.push(DamageEvent(amount=100.0, skill="Axe_Base_Attack",
                              source_addr=PA, target_addr=FOE, t=time.time()))
    _tick(ov)
    report = "\n".join(event_path_report(m))
    assert "ARRIVING" in report
    assert "attributed to a session" in report

    # a hit whose caster cannot be identified — the live bridge case: the DLL
    # names you "Player" and the tracker cannot resolve a hero for it. The
    # capture is fine, the READING is not, and the good hit is still attributed,
    # so this is the PARTIAL case a binary verdict would call healthy.
    m.damage.push(DamageEvent(amount=50.0, skill="Axe_Base_Attack",
                              source_addr=0, target_addr=FOE,
                              source_name="Player", capture_mode="proxy",
                              t=time.time()))
    _tick(ov)
    report = "\n".join(event_path_report(m))
    assert "PARTIAL" in report
    assert "unidentifiable caster" in report
    assert "1 of 2 events reached a session" in report

    # only drops, no keeps: that is the loud version of the same failure
    from farever_companion.ui.dps_source_text import event_path_verdict
    verdict = event_path_verdict({"decoded": 4, "pulled": 4, "attributed": 0,
                                  "dropped": 4, "dropped_unresolved": 4,
                                  "payload_packets": 4, "payload_counted": True})
    assert verdict.startswith("DROPPED BEFORE ATTRIBUTION")

    # a model with no source at all says so instead of raising
    assert "no capture source attached" in "\n".join(event_path_report(None))


def test_ready_label_colors_capture_state(ov):
    """READY is green when capture is ready, gold while still loading, red
    when the build cannot capture at all."""
    from farever_companion.ui import theme

    ov.model.damage._status = "silent"     # calibrated, quiet: ready
    _tick(ov)
    assert ov.state_lbl.text() == "READY"
    assert theme.GOOD in ov.state_lbl.styleSheet()

    ov.model.damage._status = "locating"   # still loading
    _tick(ov)
    assert ov.state_lbl.text() == "READY"
    assert theme.GOLD in ov.state_lbl.styleSheet()

    ov.model.damage._status = "noscan"     # cannot capture
    _tick(ov)
    assert theme.DANGER in ov.state_lbl.styleSheet()


def test_capture_badge_reflects_source_status(ov):
    """The title-bar badge tracks the damage source: LIVE while events flow,
    IDLE when quiet, hidden when there is no source to talk about."""
    from farever_companion.ui import theme

    _tick(ov)                                    # fake source reports "live"
    assert ov.cap_badge.isVisible()
    assert ov.cap_badge.text() == "LIVE"
    assert theme.GOOD in ov.cap_badge.styleSheet()

    ov.model.damage._status = "silent"           # calibrated but quiet
    _tick(ov)
    assert ov.cap_badge.text() == "IDLE"
    assert theme.DIM in ov.cap_badge.styleSheet()

    ov.model.damage = None                       # no source: nothing to say
    _tick(ov)
    assert not ov.cap_badge.isVisible()


def test_capture_badge_starts_hidden_before_first_tick():
    o = DpsOverlay(_Model(names={PA: "Me"}), _Settings())
    try:
        assert not o.cap_badge.isVisible()       # no status read yet
        assert o.cap_badge.text() == ""
    finally:
        destroy(o)


def test_the_overlay_timer_ticks_from_combat_entry(ov):
    """The header must read as running for the whole fight: with the game
    reporting combat and nothing decoded yet, the timer ticks from combat
    entry instead of sitting at 00:00.0, and the first hit takes the clock
    over without the number jumping."""
    ov.model.combat_state_value = {"in_combat": True, "combat_id": 5,
                                   "combat_start": 20.0, "combat_end": 0.0}
    _tick(ov)                                   # the combat poll reads the flag
    assert ov.tracker.combat_state["in_combat"] is True

    # The fight began a few seconds before the poll noticed it (and _tick's
    # tracker.update() re-polls + re-propagates the anchor, like production).
    ov.tracker._combat_wall_start_at = time.time() - 4.0
    ov.tracker._combat_poll_at = 0.0
    _tick(ov)

    assert ov.state_lbl.text() == "IN COMBAT"
    assert ov.time_lbl.text() != "00:00.0"
    secs = float(ov.time_lbl.text().split(":")[1])
    assert 3.5 <= secs <= 6.0, ov.time_lbl.text()

    # The first hit takes over from the SAME anchor: no jump.
    ov.tracker.session.record_hit("Foe", 0x1, 10.0, 10.0, 250.0,
                                  when=time.time())
    _tick(ov)
    assert ov.state_lbl.text() == "IN COMBAT"
    secs2 = float(ov.time_lbl.text().split(":")[1])
    assert 3.5 <= secs2 <= 6.0, ov.time_lbl.text()


def test_the_tag_is_a_plain_label_beside_the_name():
    """Reported live 2026-10-03: "Envenom (Passive) ... thats just text not a
    icon looking tag button".

    So the kind moved out of the skill name and into a pill: the name reads
    `Envenom` and a coloured GEAR / PASSIVE / PET badge sits beside it. The
    badge also gives the name its characters back - the suffix was eating the
    name's own 30-character budget.
    """
    from farever_companion.ui import theme
    from farever_companion.ui.skill_row import SkillRow, skill_row_tag

    # GLYPH ONLY - reported live 2026-10-03: "instead u put a text lable next
    # to the skill". The word is on the hover, where it costs no width.
    # A PLAIN LABEL, not a round pill - reported live 2026-10-03: "i did not
    # want some round pills, i wanted a lable tag like the tool tips have".
    cases = [
        ("PurifiedHeart_Proc", "Purified Heart", "proc"),
        ("Warrior_Talent_ShatteredRage", "Shattered Rage", "passive"),
        ("SummonBee_HoneyBolt", "Summon Bee Honey Bolt", "summon"),
    ]
    for sid, name, word in cases:
        row = SkillRow()
        row.set_skill(sid, name)
        assert word in row._tag_lbl.text(), (sid, row._tag_lbl.text())
        # Flat: no pill. A filled chip is what was rejected twice; the label
        # keeps only its own colour and a transparent background.
        style = row._tag_lbl.styleSheet()
        assert "background: transparent" in style, style
        assert theme.PANEL_HI not in style, style
        assert not row._tag_lbl.isHidden(), sid        # shown, not merely set
        assert row._tag_lbl.toolTip(), "the word lives on the hover"
        # the name is clean: no "(Passive)" suffix inside it any more
        full = row._name_lbl._full
        assert full == name, (sid, full)
        assert "(" not in full

    # A name that reached an ARCHIVE can still carry the old text tag; the row
    # strips it rather than showing the fact twice (badge AND suffix).
    archived = SkillRow()
    archived.set_skill("PurifiedHeart_Proc", "Flaming Weapon (Item proc)")
    assert archived._name_lbl._full == "Flaming Weapon"

    # A skill you pressed carries no badge at all.
    plain = SkillRow()
    plain.set_skill("Sword_Base_Attack", "Sword Attack")
    assert plain._tag_lbl.text() == "" and plain._tag_lbl.isHidden()

    # "(pet)" is the stronger, bridge-observed claim: one badge, not two.
    pet = SkillRow()
    pet.set_skill("Staff_SummonDemon_Combo (pet)", "Chaos Bolts (pet)")
    assert pet._tag_lbl.text() == "", pet._tag_lbl.text()
    assert skill_row_tag("Staff_SummonDemon_Combo (pet)") == "summon"


def test_the_meter_chips_lead_with_the_per_second_rates(ov):
    """The /s numbers are the meter's focus line, totals sit beneath them.

    Live 2026-10-03: "in the top dps hud the dp/s h/s damage taken /s should
    be the main focus numbers". The chips led with the running TOTAL and put
    the rate in the small sub-line — backwards for a number a player watches
    mid-fight — and the TAKEN chip had no /s at all, so the one figure that
    says how fast you are being hurt existed nowhere on this HUD.

    `set_rate` is the shape: rate in the big bold line, total one line down.
    The dummy HUD still uses `set_value` (total first), because a finished
    test is read as a receipt rather than watched.
    """
    m = ov.model
    m._units = [_hero(PA), _foe(FOE)]
    t0 = time.time()
    m.damage.push(DamageEvent(amount=6000.0, skill="Sword_Spin",
                              source_addr=PA, target_addr=FOE, t=t0))
    # Incoming (taken) and a heal, both through the one damage queue the
    # fake model exposes; `incoming` is what routes it to TAKEN.
    m.damage.push(DamageEvent(amount=1200.0, skill="Enemy_Swipe",
                              source_addr=FOE, target_addr=PA, t=t0 + 0.01,
                              incoming=True))
    from farever_companion.core.damage_events import K_HEAL
    m.damage.push(DamageEvent(amount=900.0, skill="Holy_Heal", kind=K_HEAL,
                              source_addr=PA, target_addr=PA, t=t0 + 0.02))
    # A known span, so the expected rates are arithmetic and not a guess.
    ov.tracker.overall_session.start_time = time.time() - 10.0
    ov.tracker.overall_session.last_event_time = time.time()
    _tick(ov)

    for chip in (ov.chip_dmg, ov.chip_heal, ov.chip_taken):
        assert chip.val_lbl.text().endswith("/s"), (
            f"{chip.title_lbl.text()} must lead with its /s rate, "
            f"got {chip.val_lbl.text()!r}")
        assert chip.sub_lbl.text().endswith("total"), (
            f"{chip.title_lbl.text()} must keep its total one line down, "
            f"got {chip.sub_lbl.text()!r}")
        # The focus line is the heavy one: 15px beating 9px is the whole
        # point of the layout, so pin it rather than trusting the stylesheet.
        focus_px = int(chip.val_lbl.styleSheet().split("font-size:")[1]
                       .split("px")[0])
        sub_px = int(chip.sub_lbl.styleSheet().split("font-size:")[1]
                     .split("px")[0])
        assert focus_px > sub_px, "the rate must be the larger number"

    # The taken rate is a real figure, not a placeholder: 1,200 over ~10s.
    assert ov.chip_taken.val_lbl.text() != "0.0/s"
    assert "1,200" in ov.chip_taken.sub_lbl.text()
    assert "6,000" in ov.chip_dmg.sub_lbl.text()

    # A reset returns every chip to the same shape it reports live.
    ov.model.player_addr = None
    ov.tracker.reset()
    _tick(ov)
    assert ov.chip_dmg.val_lbl.text() == "0.0/s"
    assert ov.chip_dmg.sub_lbl.text() == "0 total"
    assert ov.chip_taken.val_lbl.text() == "0.0/s"


def test_the_meter_timer_ticks_on_the_wall_clock_but_rates_do_not(ov):
    """The time label and the rate denominator are DIFFERENT clocks.

    Two facts pulled in opposite directions on 2026-10-03. The fight timer
    must keep advancing between hits, so it reads the wall-clock span
    (`wall_duration`) - `duration` is anchored to the last event and would
    stand still. The RATES must divide by the event span, which is what
    DPS Analysis uses and what the archive will store; feeding the wall span
    into them is the same 600-vs-637 split `CombatSession.duration` was fixed
    for this session, and it survived inside this window because one local
    variable served both jobs.

    A pause is what separates the two: the wall span keeps running, the event
    span does not. With a 100s wall span over a 60s event span, the timer must
    show ~01:40 while the chip rate divides by 60.
    """
    m = ov.model
    m._units = [_hero(PA), _foe(FOE)]
    sess = ov.tracker.session
    now = time.time()
    t0 = now - 60.0
    m.damage.push(DamageEvent(amount=6000.0, skill="Sword_Spin",
                              source_addr=PA, target_addr=FOE, t=t0))
    _tick(ov)

    # A 40s pause the fight's clock absorbed: start_time sits 100s back on the
    # wall clock, while the last EVENT is only 60s back. The gap between them
    # (now - last_event) is exactly what a rate must not divide by.
    sess.start_time = now - 100.0
    sess.last_event_time = now - 40.0
    sess.end_time = None
    _tick(ov)

    assert abs(sess.duration - 60.0) < 2.0, "event span, for the rates"
    assert abs(sess.wall_duration - 100.0) < 2.0, "wall span, for the timer"
    # The timer reads the wall span...
    assert ov.time_lbl.text().startswith("01:4"), ov.time_lbl.text()
    # ...while the DPS chip divides by the EVENT span: 6,000 / 60 = 100/s.
    # Dividing by the wall span would read 60.0/s.
    assert ov.chip_dmg.val_lbl.text().startswith("100"), (
        f"the rate must use the event span, got {ov.chip_dmg.val_lbl.text()!r}")
    # Every player row divides by the same span as the chips: the row's DPS
    # figure is the rate, so it must read 100 too (60 would be the wall span).
    row = ov._row_widgets["Me (You)"]
    assert row.total_lbl.text().startswith("100"), row.total_lbl.text()
    assert "DPS" in row.total_lbl.text()
