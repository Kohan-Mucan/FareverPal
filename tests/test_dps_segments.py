"""Segment selection is per-surface.

The Top DPS overlay and the Combat & DPS Analysis page share ONE tracker
(``model.dps``). The page has its own segment picker (Boss / Trash / Overall …),
so if it wrote ``tracker.segment_view`` it would silently retarget the overlay's
numbers, timer, ranks and segment label - the two surfaces would "disagree"
about the very same fight. The page therefore reads its segment back through
``DpsTracker.session_for`` and the shared default stays put.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from farever_companion.core.dps_tracker import CombatSession, DpsTracker
from farever_companion.ui.dps_source_text import empty_hint
from farever_companion.ui.pages.combat_page import (
    CombatPageMixin, _CP_SEGMENTS, _CP_SEGMENT_ORDER, _cp_empty_hint,
)
from tests.dps_fakes import _FakeModel  # noqa: F401


@pytest.fixture(scope="module", autouse=True)
def _qapp():
    """One offscreen QApplication for the whole module (widgets need one).

    Without this the module hard-aborts the interpreter (0xC0000409) when run
    on its own: building a QWidget with no QApplication kills the process
    rather than failing a test. A full-suite run only survives it because an
    earlier module happens to leave a QApplication alive.
    """
    from PySide6 import QtWidgets
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])



@pytest.fixture
def tracker():
    return DpsTracker(_FakeModel())


# --- the tracker's contract ----------------------------------------------
def test_session_for_picks_the_requested_segment_without_moving_the_default(tracker):
    assert tracker.session_for("trash") is tracker.trash_session
    assert tracker.session_for("boss") is tracker.boss_session
    assert tracker.session_for("boss_adds") is tracker.boss_adds_session
    assert tracker.session_for("dummy") is tracker.dummy_session
    assert tracker.session_for("overall") is tracker.overall_session
    # ...and none of that moved the shared default the overlay reads
    assert tracker.segment_view == "auto"
    assert tracker.session is tracker.overall_session


def test_auto_ignores_the_shared_default(tracker):
    """`session_for("auto")` is the live view, whatever segment_view says."""
    tracker.segment_view = "trash"
    assert tracker.session is tracker.trash_session
    assert tracker.session_for("auto") is tracker.overall_session


def test_auto_follows_the_running_fight(tracker):
    """Auto = the fight that is actually running, dummy > boss > trash."""
    assert tracker.session_for("auto") is tracker.overall_session

    # A dummy test needs a dummy in front of the player, not just the flag:
    # "in a dummy fight" alone used to be enough, and an empty dummy session
    # engaged by a dummy the scan saw across the zone then owned the live board
    # for the rest of the session (see dummy_owns_board).
    tracker.dummy_near = (0x9, "Dummy", 500.0, 6.9)
    tracker.in_dummy_fight = True
    assert tracker.session_for("auto") is tracker.dummy_session

    tracker.in_dummy_fight = False
    tracker.in_boss_fight = True
    assert tracker.session_for("auto") is tracker.boss_session


def test_auto_follows_adds_while_the_boss_board_is_empty(tracker):
    """A latched boss flag with zero boss damage must not blank both surfaces.

    Live 2026-09-19: a dungeon wave before the boss pull archived 6341 damage
    under Boss Adds while `auto` pinned the empty boss session — the Top DPS
    meter AND the Combat page showed nothing until a manual reset. When the
    fight is live and every hit so far landed on adds, auto shows the adds
    board; the moment the boss board has damage, it wins again.
    """
    tracker.in_boss_fight = True
    # Adds took damage, boss session still empty -> adds board is the live view.
    tracker.boss_adds_session.record_hit(
        target_name="add", target_addr=1, target_hp=10.0, target_max_hp=100.0,
        damage=25.0, caster_name="You", is_me=True, skill_id="s1",
        skill_name="Skill", is_crit=False)
    assert tracker.session_for("auto") is tracker.boss_adds_session
    assert tracker.session is tracker.boss_adds_session
    # Boss damage lands -> back to the boss board.
    tracker.boss_session.record_hit(
        target_name="boss", target_addr=2, target_hp=10.0, target_max_hp=100.0,
        damage=50.0, caster_name="You", is_me=True, skill_id="s2",
        skill_name="Skill", is_crit=False)
    assert tracker.session_for("auto") is tracker.boss_session


def test_unknown_segment_falls_back_to_auto(tracker):
    """The page's "past_fights" is a list view, not a segment."""
    assert tracker.session_for("past_fights") is tracker.session_for("auto")
    tracker.segment_view = "past_fights"
    assert tracker.session is tracker.session_for("auto")


# --- the page's picker must not retarget the overlay ----------------------
class _Chip:
    """Stand-in for a checkable segment chip."""

    def __init__(self, label: str = ""):
        self.label = label
        self.checked = False
        # The chips also carry their own inertness, since they only slice the
        # live run (see CombatPageMixin._sync_segment_chips).
        self.enabled = True
        self.tooltip = ""

    def setChecked(self, on):        # noqa: N802 - Qt API
        self.checked = bool(on)

    def isChecked(self):             # noqa: N802 - Qt API
        return self.checked

    def setEnabled(self, on):        # noqa: N802 - Qt API
        self.enabled = bool(on)

    def setToolTip(self, text):      # noqa: N802 - Qt API
        self.tooltip = text


class _PageStub(CombatPageMixin):
    """CombatPageMixin with just the collaborators the segment path touches."""

    def __init__(self, tracker):
        self._cp_segment = "auto"
        self._cp_metric = "damage"
        self._selected_history_idx = 0
        self._tracker = tracker
        self.rendered = 0
        self.cp_segment_chips = {seg: _Chip(lbl) for seg, lbl, _ in _CP_SEGMENTS}
        self.cp_metric_chips: dict = {}
        # the real build syncs the chips right after creating them
        self._sync_segment_chips()

    def _get_active_tracker(self):
        return self._tracker

    def _render_combat_breakdown(self):
        self.rendered += 1

    def _selected_segment(self):
        return {s for s, c in self.cp_segment_chips.items() if c.isChecked()}


def test_page_segment_picker_never_writes_the_shared_default(tracker):
    page = _PageStub(tracker)
    page._set_combat_page_segment("trash")

    # the overlay's view is untouched...
    assert tracker.segment_view == "auto"
    # ...while the page shows what its own picker asked for
    assert page._cp_segment == "trash"
    assert page._get_displayed_session() is tracker.trash_session
    assert page.rendered == 1

    page._set_combat_page_segment("boss")
    assert tracker.segment_view == "auto"
    assert page._get_displayed_session() is tracker.boss_session


def test_overlay_view_survives_a_page_segment_change(tracker):
    """The reported symptom: pick a segment on the page, the overlay must keep
    showing the live fight."""
    page = _PageStub(tracker)
    overlay_session = tracker.session
    page._set_combat_page_segment("trash")
    assert tracker.session is overlay_session


# --- the rebuilt segment selector -----------------------------------------
def test_the_selector_offers_every_segment_session_for_understands(tracker):
    """Boss / Trash / Adds / Overall live on the page, and Auto keeps it
    following the fight that is actually running."""
    assert _CP_SEGMENT_ORDER == ("auto", "boss", "trash", "boss_adds", "overall")
    assert set(_CP_SEGMENT_ORDER) >= {"boss", "trash", "boss_adds", "overall"}
    # the four explicit segments are four distinct parses - no two alias
    explicit = ["boss", "trash", "boss_adds", "overall"]
    assert len({id(tracker.session_for(s)) for s in explicit}) == len(explicit)
    assert tracker.session_for("boss") is tracker.boss_session
    assert tracker.session_for("boss_adds") is tracker.boss_adds_session
    assert tracker.session_for("trash") is tracker.trash_session
    assert tracker.session_for("overall") is tracker.overall_session
    # Auto is the LIVE fight: Overall until something is running, then the
    # segment that actually is.
    assert tracker.session_for("auto") is tracker.overall_session
    tracker.in_boss_fight = True
    assert tracker.session_for("auto") is tracker.boss_session


@pytest.mark.parametrize("segment", ["auto", "boss", "trash", "boss_adds",
                                     "overall"])
def test_every_chip_selects_exactly_that_segment(tracker, segment):
    page = _PageStub(tracker)
    page._set_combat_page_segment(segment)
    assert page._cp_segment == segment
    assert page._get_displayed_session() is tracker.session_for(segment)
    assert page._selected_segment() == {segment}
    # ...and the shared default the overlay reads never moved
    assert tracker.segment_view == "auto"


def test_the_selector_starts_on_auto(tracker):
    page = _PageStub(tracker)
    assert page._cp_segment == "auto"
    assert page._selected_segment() == {"auto"}


def test_an_unknown_segment_falls_back_to_auto(tracker):
    page = _PageStub(tracker)
    page._set_combat_page_segment("boss")
    page._set_combat_page_segment("nonsense")
    assert page._cp_segment == "auto"
    assert page._selected_segment() == {"auto"}


def test_picking_a_segment_on_compare_leaves_the_whole_encounter_view(tracker):
    """A segment is invisible on the Compare matrix, so a click there has to
    bring the breakdown board back rather than light a chip and change nothing
    on screen.

    The Fights LIST used to be lumped in with this, and that was the bug: a
    segment chip there either closed the list or was swallowed, so the row was
    meaningless exactly where the player was using it (live 2026-10-04). It now
    filters that list instead (see
    `test_the_segment_row_filters_the_fights_list`); Compare has no kind to
    filter, so it keeps the old behaviour.
    """
    page = _PageStub(tracker)
    page._set_combat_metric("compare")
    page.rendered = 0
    page._set_combat_page_segment("trash")
    assert page._cp_metric == "damage"
    assert page._cp_segment == "trash"
    assert page.rendered == 1
    assert tracker.segment_view == "auto"


# --- the empty board explains itself --------------------------------------
def _empty_session() -> CombatSession:
    return CombatSession("Boss Fight", kind="boss")


def test_an_empty_segment_names_itself():
    """An empty Boss board on a trash-only run is not a dead reader: the run
    has numbers, that slice does not."""
    hint = _cp_empty_hint(None, _empty_session(), "boss")
    assert "No boss fight recorded" in hint
    assert "Pick Auto" in hint


@pytest.mark.parametrize("segment", ["auto", "overall", "past_fights"])
def test_auto_and_overall_keep_the_generic_empty_hint(segment):
    default = "No combat data recorded yet. Attack enemies or dummy in-game."
    hint = _cp_empty_hint(None, _empty_session(), segment)
    assert hint == empty_hint(None, default)
    assert "Pick Auto" not in hint


# --- the picker as BUILT ---------------------------------------------------
def test_the_built_page_has_wired_segment_chips(tmp_path, monkeypatch):
    """The regression this rebuild fixes: the handler existed but nothing
    called it, and its handler chain crashed on an attribute the build never
    set. Exercise the real page, not a stub."""
    monkeypatch.setenv("FAREVER_MODDATA_DIR", str(tmp_path / "moddata"))
    from farever_companion.config import Settings
    from farever_companion.ui.control_panel import ControlPanel

    cp = ControlPanel(Settings())
    try:
        cp._page_combat()
        chips = getattr(cp, "cp_segment_chips", None)
        assert chips is not None, "the segment row was never built"
        assert set(chips) == set(_CP_SEGMENT_ORDER)
        assert chips["auto"].isChecked()

        cp._set_combat_page_segment("boss")
        assert cp._cp_segment == "boss"
        assert chips["boss"].isChecked()
        assert not chips["auto"].isChecked()

        tracker = cp._get_active_tracker()
        if tracker is not None:
            assert tracker.segment_view == "auto"
    finally:
        from tests.qt_helpers import destroy_panel
        destroy_panel(cp)


def test_a_chip_clicked_on_an_archived_fight_leaves_the_fight_picked(tracker):
    """Clicking a SEGMENT must not move the fight selector.

    Live 2026-10-03: "in DPS Analysis clicking a SEGMENT changes the drop down
    to live". The old code did exactly that — it snapped the selector back to
    the Live row to escape the fact that segments slice only the live run, and
    threw away the fight the user had just picked. Segments genuinely cannot
    apply to a frozen archived fight (it has no boss/trash/adds slices), but the
    selector is the user's choice: the chips now report their own inertness
    instead of silently changing what is selected.
    """
    page = _PageStub(tracker)
    archived = CombatSession("Old King Ratsar", kind="boss")
    page._combo_sessions = [None, archived]
    page._selected_history_idx = 1

    page._set_combat_page_segment("boss")

    assert page._selected_history_idx == 1, "the chip moved the fight selector"
    assert page._cp_segment != "boss" or not page._segments_apply_to_shown_session()
    assert tracker.segment_view == "auto"     # the shared default never moved


def test_the_segment_chips_are_never_disabled_and_say_why_on_hover(tracker):
    """The chips stay pressable everywhere; the limitation is a hint, not a lock.

    Disabling them was the wrong instinct and failed twice in the field - once
    locking the whole row on the LIVE run. So the contract is now: never
    disabled, and on a past fight the hover states that segments split the live
    run. A click there is a no-op that leaves the fight picked.
    """
    page = _PageStub(tracker)
    page._combo_sessions = [None, CombatSession("Old King Ratsar", kind="boss")]
    page.cp_segment_chips = {seg: _Chip() for seg in _CP_SEGMENT_ORDER}
    page._selected_history_idx = 0
    page._sync_segment_chips()

    assert all(c.enabled for c in page.cp_segment_chips.values())
    assert page.cp_segment_chips["boss"].tooltip == ""

    # A past fight: still pressable, but it says what it cannot do.
    page._selected_history_idx = 1
    page._sync_segment_chips()
    assert all(c.enabled for c in page.cp_segment_chips.values())
    assert "Live" in page.cp_segment_chips["boss"].tooltip

    page._set_combat_page_segment("trash")
    assert page._selected_history_idx == 1, "the click moved the fight selector"
    assert page._cp_segment != "trash"


def test_the_segment_chips_stay_clickable_on_live_despite_stale_bookkeeping(tracker):
    """Regression: every chip came up DISABLED on the live run.

    Reported live 2026-10-03: "now i cant click any SEGMENT there are disabled",
    introduced by the change that stopped a chip click from snapping the fight
    selector back to Live. That version read the enabled state from
    `_selected_history_idx` AND `_browse_sessions`; while the LIVE run was on
    screen but a browse holder was still set, the two disagreed and the whole
    row locked - on the one screen where the chips do work.

    So the chips ask the question they actually mean: is the board showing the
    live run? Anything else (an exception, a missing tracker) leaves them
    enabled, because locking the player out is the worse failure.
    """
    page = _PageStub(tracker)
    page._combo_sessions = [None, CombatSession("Old King Ratsar", kind="boss")]
    page._selected_history_idx = 0
    # A browse holder left over from an earlier past-fight view.
    page._browse_sessions = ["Old King Ratsar"]

    page.cp_segment_chips = {seg: _Chip() for seg in _CP_SEGMENT_ORDER}
    page._sync_segment_chips()

    assert all(c.enabled for c in page.cp_segment_chips.values()), \
        "live run, so the chips must be clickable"
    page._set_combat_page_segment("trash")
    assert page._cp_segment == "trash"

    # A past fight must not lock them either - the same rule as live.
    page._selected_history_idx = 1
    page._sync_segment_chips()
    assert all(c.enabled for c in page.cp_segment_chips.values())


def test_the_segment_row_filters_the_fights_list(tmp_path, monkeypatch):
    """On the Fights list the SEGMENT chips pick the list's kind, and the two
    filter controls stay in step.

    Reported live 2026-10-04: "SEGMENT is still buggy with the filters ...
    they dont do anything when i click them". On that view a segment chip either
    closed the list (metric "fights" was a whole-encounter view) or, with a past
    fight picked, was silently swallowed — the row did nothing exactly where the
    player was using it. Now Boss/Trash pick the kind, Auto/Overall/Adds show
    every kind, and the frozen bar's chips move the SEGMENT highlight back.
    """
    import json
    import time

    monkeypatch.setenv("FAREVER_MODDATA_DIR", str(tmp_path / "moddata"))
    from farever_companion.config import Settings, dps_dir
    from farever_companion.ui.control_panel import ControlPanel
    from tests.qt_helpers import destroy_panel

    cp = ControlPanel(Settings())
    try:
        cp._page_combat()
        lt = time.localtime()
        day = dps_dir() / time.strftime("%Y-%m", lt) / time.strftime("%d", lt)
        day.mkdir(parents=True, exist_ok=True)
        for name in ("Boss Fights_Kartik_Warrior_a1b2.json",
                     "trash_Kartik_Warrior_a1b2.json"):
            (day / name).write_text(json.dumps({"history": []}),
                                    encoding="utf-8")

        cp._set_combat_metric("fights")
        assert cp._cp_metric == "fights"

        cp._set_combat_page_segment("boss")
        assert cp._cp_metric == "fights", "a SEGMENT chip closed the Fights list"
        assert cp._cp_fights_filter == "boss"
        assert cp.cp_segment_chips["boss"].isChecked()
        assert not cp.cp_segment_chips["auto"].isChecked()

        cp._set_combat_page_segment("trash")
        assert cp._cp_fights_filter == "trash"
        assert cp.cp_segment_chips["trash"].isChecked()

        # Auto / Overall / Adds all mean "every kind"
        cp._set_combat_page_segment("overall")
        assert cp._cp_fights_filter == "all"
        assert cp.cp_segment_chips["auto"].isChecked()
        assert not cp.cp_segment_chips["boss"].isChecked()

        # the frozen bar's chips move the SEGMENT row the other way ...
        cp._set_fights_filter("boss")
        assert cp.cp_segment_chips["boss"].isChecked()
        cp._set_fights_filter("all")
        assert cp.cp_segment_chips["auto"].isChecked()
        # ... and a kind the row cannot name (Failed) lights nothing rather
        # than claiming a kind it is not filtering to
        cp._set_fights_filter("failed")
        assert not any(b.isChecked() for b in cp.cp_segment_chips.values())
    finally:
        destroy_panel(cp)

    # leaving the Fights list hands the row back to the live-run slices: the
    # click that filtered the list must NOT have re-sliced what the board shows
    cp2 = ControlPanel(Settings())
    try:
        cp2._page_combat()
        cp2._set_combat_metric("fights")
        cp2._set_combat_page_segment("boss")
        assert cp2._cp_segment == "auto"
        cp2._set_combat_metric("damage")
        assert cp2.cp_segment_chips["auto"].isChecked()
        cp2._set_combat_page_segment("trash")      # now it slices the live run
        assert cp2._cp_segment == "trash"
        assert cp2._cp_metric == "damage"
    finally:
        destroy_panel(cp2)
