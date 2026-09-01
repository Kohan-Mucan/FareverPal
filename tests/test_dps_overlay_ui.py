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
from farever_companion.ui.overlays.dps_overlay import (  # noqa: E402
    DpsOverlay, GLOBAL_SKILL_CAP, _PlayerRowWidget,
)
from tests.dps_fakes import (  # noqa: E402
    PA, ALLY, FOE, BOSS, _FakeSource, _FakeModel as _Model,
    _hero, _foe, _qapp,
)


class _Settings:
    opacity = 1.0
    geometry = {}
    dps_max_dist = 400.0
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
        o.close()


def _tick(ov):
    """Refresh the overlay and drain Qt so rows report visibility truthfully."""
    ov._tick()
    QtWidgets.QApplication.processEvents()


# --- cleaned-up controls ---------------------------------------------------

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
    m._units = [_hero(PA), _foe(BOSS, uid="Boss_Foo", hp=100000.0), _foe(FOE)]
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
    # chips report your own totals, not the stray rival damage
    assert ov.chip_dmg.val_lbl.text() == "5,000"
    assert ov.chip_taken.val_lbl.text() == "0"

    # grouped again -> the rival row returns with full group totals
    ov.model.roster = GroupSnapshot(group=0x5000, members=[
        GroupMember(player=0, hero=ALLY, name="Alice"),
        GroupMember(player=0, hero=PA, name="Me", is_me=True),
    ])
    _tick(ov)
    assert set(ov._row_widgets) == {"Me (You)", "Brit"}
    assert ov.chip_dmg.val_lbl.text() == "14,000"

    # roster read fails / unattached -> rows keep showing (unknown state)
    ov.model.roster = None
    ov.model.group_roster = lambda: None
    _tick(ov)
    assert set(ov._row_widgets) == {"Me (You)", "Brit"}
    assert ov.chip_dmg.val_lbl.text() == "14,000"

    # inside a dungeon/rift party members always show, even when the game
    # reports a solo roster (dungeons group everyone in the instance)
    ov.model.group_roster = lambda: ov.model.roster
    ov.model.roster = GroupSnapshot(group=0x5000, members=[], solo=True)
    ov.model.is_dungeon = True
    _tick(ov)
    assert set(ov._row_widgets) == {"Me (You)", "Brit"}
    assert ov.chip_dmg.val_lbl.text() == "14,000"
    ov.model.roster = None
    ov.model.group_roster = lambda: None
    ov.model.is_dungeon = False


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
    assert ov.chip_dmg.val_lbl.text() == "14,000"


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

def test_game_combat_badge_shows_game_state(ov):
    """The GAME badge mirrors game combat state, duration, and reset."""
    ov._update_game_combat_lbl()
    assert ov.game_lbl._full == ""            # no snapshot yet

    ov.tracker.combat_state = {"in_combat": True, "combat_id": 7,
                               "combat_start": 100.0, "combat_end": 0.0}
    ov._update_game_combat_lbl()
    assert "IN COMBAT" in ov.game_lbl._full
    assert "7" in ov.game_lbl.toolTip()

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


def test_other_players_auto_collapsed_and_global_cap_shared(ov):
    """Ally rows start collapsed; the global skill cap is shared."""
    m = ov.model
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
    me_row = ov._row_widgets["Me (You)"]
    ally_row = ov._row_widgets["Brit"]
    me_row.show()
    ally_row.show()
    QtWidgets.QApplication.processEvents()

    # only the local player is expanded by default; allies auto-collapse
    assert me_row.expanded is True
    assert ally_row.expanded is False
    assert not ally_row.skills_box.isVisible()

    # me uses 6 of the global 8 (no toggle: 6 <= cap)
    shown = [sw for sw in me_row._skill_row_widgets.values() if sw.isVisible()]
    assert len(shown) == 6
    assert not me_row._more_btn.isVisible()

    # opening the ally gives it the leftover budget: top 2 of its 6 skills
    _click_chevron(ally_row)
    _tick(ov)
    QtWidgets.QApplication.processEvents()
    assert ally_row.expanded is True
    shown = [sw for sw in ally_row._skill_row_widgets.values()
             if sw.isVisible()]
    assert len(shown) == 2
    assert "4 more" in ally_row._more_btn.text()

    # expanding the ally's list overrides the global cap -> all 6 appear
    ally_row._more_btn.click()
    _tick(ov)
    QtWidgets.QApplication.processEvents()
    shown = [sw for sw in ally_row._skill_row_widgets.values()
             if sw.isVisible()]
    assert len(shown) == 6
    assert "fewer" in ally_row._more_btn.text()
