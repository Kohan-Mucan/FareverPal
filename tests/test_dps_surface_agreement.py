"""The Combat & DPS Analysis page and the Top DPS overlay must agree.

Both surfaces read ONE tracker (``model.dps``) and share one totals function,
so a disagreement is a display-side divergence. There is exactly ONE that is
deliberate: the Solo Only filter belongs to the live overlay - a self-view HUD
- while the Combat & DPS Analysis page is a whole-group, post-fight surface and
is ALL-PLAYER by design (``_cp_solo_filter_on`` returns False always, pinned by
``test_cp_solo_toggle_never_filters_combat_analysis`` in
test_combat_page_tabs.py). A solo roster therefore makes the two surfaces
diverge on purpose, and that divergence is asserted below rather than treated
as a bug.

The mixed/group case must agree exactly, and the two self-only totals must
come from ONE formula: ``view_totals`` reports real-event totals for both
surfaces. These tests pin the shared predicates - ``solo_only_view`` and
``view_totals`` - and cross-check the REAL overlay's rendered numbers against
the formulas the page renders from.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import time

import pytest
from PySide6 import QtWidgets

from farever_companion.core.damage_events import DamageEvent
from farever_companion.core.dps_tracker import (
    CombatSession, DpsTracker, PlayerParse, solo_only_view, solo_status,
    view_totals,
)
from farever_companion.core.group_reader import GroupMember, GroupSnapshot
from farever_companion.ui.overlays.dps import DpsOverlay
from farever_companion.ui.pages.combat_page import CombatPageMixin
from tests.dps_fakes import (  # noqa: F401
    ALLY, FOE, PA, _FakeModel, _foe, _hero,
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



class _Settings:
    """The settings surface both the overlay and the page read."""

    opacity = 1.0
    geometry = {}
    dps_view = "damage"
    dps_top_count = 8
    heals_top_count = 2
    dps_solo_only = True

    def save(self):
        pass


class _PageStub(CombatPageMixin):
    """CombatPageMixin with just what the solo decision touches."""

    def __init__(self, model, tracker, settings):
        self.model = model
        self.s = settings
        self._tracker = tracker
        self._cp_segment = "auto"
        self._selected_history_idx = 0
        self._browse_sessions = None

    def _get_active_tracker(self):
        return self._tracker


def _solo_roster() -> GroupSnapshot:
    return GroupSnapshot(group=0x5000, members=[], solo=True)


def _group_roster() -> GroupSnapshot:
    return GroupSnapshot(group=0x5000, members=[
        GroupMember(player=0, hero=ALLY, name="Alice"),
        GroupMember(player=0, hero=PA, name="Me", is_me=True),
    ])


# --- the shared predicates ------------------------------------------------
def test_solo_only_view_needs_a_decoded_solo_roster():
    m = _FakeModel()
    m.roster = None
    # No roster at all is UNKNOWN, not solo: a failed group read must never
    # hide a real party (or flip either surface into self-only totals).
    assert solo_status(m) is None
    assert solo_only_view(m, _Settings()) is False

    m.roster = _solo_roster()
    assert solo_only_view(m, _Settings()) is True
    assert solo_only_view(m, _Settings()) is True

    m.roster = _group_roster()
    assert solo_only_view(m, _Settings()) is False

    # the user's toggle still overrides a genuine solo
    m.roster = _solo_roster()
    s = _Settings()
    s.dps_solo_only = False
    assert solo_only_view(m, s) is False

    # inside an instance everyone always shows, whatever the roster says
    m.is_dungeon = True
    assert solo_status(m) is False
    assert solo_only_view(m, _Settings()) is False


def test_view_totals_switches_to_the_local_parse_only_when_solo():
    session = CombatSession("Fight")
    me = PlayerParse(name="Me (You)", is_me=True)
    me.total_damage, me.heals = 5000.0, 200.0
    me.damage_taken = 300.0
    rival = PlayerParse(name="Brit", is_me=False)
    rival.total_damage, rival.heals = 9000.0, 0.0
    session.players = {"Me (You)": me, "Brit": rival}

    # (damage, heals, taken) — every number from real damage events.
    assert view_totals(session, solo_only=False) == (14000.0, 200.0, 300.0)
    assert view_totals(session, solo_only=True) == (5000.0, 200.0, 300.0)


# --- rendered numbers, cross-checked --------------------------------------
@pytest.fixture()
def overlay():
    """A real overlay on a real tracker over the fake model."""
    o = DpsOverlay(_FakeModel(names={PA: "Me", ALLY: "Brit"}), _Settings())
    o.show()
    QtWidgets.QApplication.processEvents()
    try:
        yield o
    finally:
        from tests.qt_helpers import destroy
        destroy(o)


def _push_a_solo_and_a_rival_hit(m):
    t0 = time.time()
    m.damage.push(DamageEvent(amount=5000.0, skill="Sword_Spin",
                             source_addr=PA, target_addr=FOE, t=t0))
    m.damage.push(DamageEvent(amount=9000.0, skill="Ally_BigHit",
                             source_addr=ALLY, target_addr=FOE, t=t0 + 0.01))


def _tick(ov):
    ov._tick()
    QtWidgets.QApplication.processEvents()


def _render_page_totals(page, session):
    """Exactly what the page's stat cards and row shares are built from."""
    return view_totals(session, page._cp_solo_filter_on())


def test_a_solo_parse_solo_filters_the_overlay_and_never_the_page(overlay):
    """The one deliberate divergence: with Solo Only on and a solo roster the
    overlay shows your own parse, while the analysis page still reports the
    whole group. Both are reading the same tracker and the same totals
    function - only the filter differs, and only on the page-less side."""
    m = overlay.model
    m.roster = _solo_roster()
    m._units = [_hero(PA), _hero(ALLY), _foe(FOE)]
    _push_a_solo_and_a_rival_hit(m)
    _tick(overlay)

    session = overlay.tracker.session
    page = _PageStub(m, overlay.tracker, overlay.s)
    me = session.players["Me (You)"]
    dur = session.duration

    # the OVERLAY is self-only, rows and chips alike, on self-share
    assert overlay._row_widgets.keys() == {"Me (You)"}
    assert overlay.chip_dmg.sub_lbl.text() == "5,000 total"
    assert overlay._row_widgets["Me (You)"].total_lbl.text() == f"{me.dps(dur):,.0f} DPS"
    assert overlay._row_widgets["Me (You)"].rate_lbl.text() == "100.0%"

    # the PAGE is not solo-filtered, even though dps_solo_only is True
    assert page._cp_solo_filter_on() is False
    dmg, _, _ = _render_page_totals(page, session)
    assert dmg == 14000.0                      # the full group, not 5,000
    assert dmg == pytest.approx(
        sum(p.total_damage for p in session.players.values()))


def test_overlay_and_page_agree_on_a_group_parse(overlay):
    m = overlay.model
    m.roster = _group_roster()
    m._units = [_hero(PA), _hero(ALLY), _foe(FOE)]
    _push_a_solo_and_a_rival_hit(m)
    _tick(overlay)

    session = overlay.tracker.session
    page = _PageStub(m, overlay.tracker, overlay.s)

    assert page._cp_solo_filter_on() is False
    dmg, _, taken = _render_page_totals(page, session)
    assert dmg == 14000.0
    assert overlay.chip_dmg.sub_lbl.text() == f"{dmg:,.0f} total" == "14,000 total"
    # both players are on the meter, under the ROSTER's names (the group
    # reader's name wins over the bare scene name), and both surfaces see them
    assert set(overlay._row_widgets) == {"Me (You)", "Alice"}
    assert set(session.players) == {"Me (You)", "Alice"}


def test_an_unreadable_roster_keeps_both_surfaces_group_wide(overlay):
    """A failed group read is unknown: neither surface may flip to self-only."""
    m = overlay.model
    m.roster = _group_roster()
    m._units = [_hero(PA), _hero(ALLY), _foe(FOE)]
    _push_a_solo_and_a_rival_hit(m)
    _tick(overlay)

    m.group_roster = lambda: None
    m.roster = None
    _tick(overlay)

    page = _PageStub(m, overlay.tracker, overlay.s)
    assert solo_status(m) is None
    assert page._cp_solo_filter_on() is False
    dmg, _, _ = _render_page_totals(page, overlay.tracker.session)
    assert dmg == 14000.0
    assert overlay.chip_dmg.sub_lbl.text() == "14,000 total"
    assert set(overlay._row_widgets) == {"Me (You)", "Alice"}


def test_the_page_is_never_solo_filtered_in_any_view():
    """The analysis page never applies Solo Only - live, archived, browsing or
    detached. A past group run especially must not be hidden because you are
    solo NOW."""
    m = _FakeModel()
    m.roster = _solo_roster()
    page = _PageStub(m, DpsTracker(m), _Settings())   # dps_solo_only = True

    assert page._cp_solo_filter_on() is False     # the live view too
    page._selected_history_idx = 2               # an archived fight
    assert page._cp_solo_filter_on() is False
    page._selected_history_idx = 0
    page._browse_sessions = []
    assert page._cp_solo_filter_on() is False

    # no live model (detached/settings preview) must not filter either
    detached = _PageStub(None, None, _Settings())
    assert detached._cp_solo_filter_on() is False
