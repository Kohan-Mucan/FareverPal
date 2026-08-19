"""Dungeons-tab Soulstones list view tests (headless Qt).

The Soulstones view lists the 8 soulstone demon-boss summon spots the way
the Dungeon List lists dungeons: boss icon + name + zone, zone-filtered,
click-to-track + plot the labeled pin on the codex map.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6 import QtCore, QtWidgets  # noqa: E402

from farever_companion.data import codex  # noqa: E402
from farever_companion.ui.pages.codex.map_ui import CodexMapUiMixin  # noqa: E402
from farever_companion.ui.pages.codex.soulstones import (  # noqa: E402
    SoulstoneListView, SoulstoneMixin)


@pytest.fixture(scope="module", autouse=True)
def _qapp():
    """One offscreen QApplication for the whole module (widgets need one)."""
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class _Tracker(QtCore.QObject):
    changed = QtCore.Signal()

    def __init__(self, tracked=()):
        super().__init__()
        self._tracked = set(tracked)
        self.toggled = []

    def is_tracked(self, kind, key):
        return (kind, key) in self._tracked

    def toggle(self, kind, key):
        self.toggled.append((kind, key))


class _ToggleTracker(QtCore.QObject):
    """Tracker that really toggles: first click tracks, second untracks."""

    changed = QtCore.Signal()

    def __init__(self):
        super().__init__()
        self._target = None

    def is_tracked(self, kind, key):
        return self._target == (kind, key)

    def toggle(self, kind, key):
        if self.is_tracked(kind, key):
            self._target = None
        else:
            self._target = (kind, key)
        self.changed.emit()


class _FakeMapWidget:
    """Minimal stand-in for CodexZoneMapCanvas: records the plotted pins."""

    def __init__(self):
        self.pins = []
        self.selected = ""

    def set_multi_pins(self, label, pins):
        self.pins = list(pins or [])
        self.selected = label

    def set_selected_mob(self, name, coords, item=None):
        self.pins = list(coords or [])
        self.selected = name


class _Label:
    def __init__(self):
        self.text = ""

    def setText(self, t):
        self.text = t


class _UntrackPage(SoulstoneMixin, CodexMapUiMixin):
    """Page with the real track handlers: toggles the tracker and plots /
    clears the map pin."""

    def __init__(self, tracker):
        self.overlay_mgr = _Ov(tracker)
        self.s = _Settings()
        self.model = None
        self._codex_map_widget = _FakeMapWidget()
        self._map_info_lbl = _Label()
        self._last_codex_item = None
        self._zone_pins_active = False
        self.clears = 0

    def _clear_codex_map_selection(self):
        self.clears += 1
        self._codex_map_widget.pins = []


class _Ov:
    def __init__(self, tracker):
        self.tracker = tracker


class _Settings:
    hud_accent = "#38bdf8"
    track_kind = ""
    track_id = ""


class _FakePage:
    """Minimal page: the attributes SoulstoneListView touches plus the track
    handlers it wires the cells to."""

    def __init__(self, zone="All", tracker=None):
        self._dungeon_zone = zone
        self.s = _Settings()
        self.overlay_mgr = _Ov(tracker or _Tracker())
        self.tracked = []

    def _is_soulstone_tracked(self, poi):
        tr = self.overlay_mgr.tracker
        from farever_companion.ui.pages.codex.soulstones import SoulstoneMixin
        key = SoulstoneMixin._soulstone_track_key(self, poi)
        return tr.is_tracked("soulstone", key)

    def _track_soulstone(self, poi):
        self.tracked.append(poi)


def test_list_renders_all_eight_summon_spots():
    """All 8 soulstone bosses render as rows with their boss name and summon
    zone (the dungeon-list shape: name + sub-line)."""
    page = _FakePage()
    view = SoulstoneListView(page)
    assert view.row_count == 8
    names = [c.poi["name"] for c in view._cells]
    assert "Ariana Grandemon" in names and "Baphometal" in names
    assert len(set(names)) == 8
    # sub-line shows the summon zone, not the raw zone id
    sub = view._cells[0]._sub_lbl.text()
    assert sub and "Z1_" not in sub and "Z2_" not in sub


def test_cell_has_no_tooltip():
    """Flat rows with a subtle hover: the Soulstones list deliberately shows
    no tooltips (the summon cost used to be the hover text, but the rows are
    plain text rows now), so every cell hovers empty."""
    view = SoulstoneListView(_FakePage())
    for cell in view._cells:
        assert cell.toolTip() == "", cell.toolTip()


def test_zone_filter_slices_the_list():
    """The Dungeons-tab zone keys (All / Z1 / Z2) filter the summon spots by
    zone — Z1 has 4, Z2 has 4."""
    z1 = SoulstoneListView(_FakePage(zone="Z1"))
    z2 = SoulstoneListView(_FakePage(zone="Z2"))
    assert z1.row_count == 4
    assert z2.row_count == 4
    from farever_companion.data import dungeons
    for c in z1._cells:
        assert dungeons.zone_tag_from_zone_id(c.poi.get("zone") or "") == "Z1"
    for c in z2._cells:
        assert dungeons.zone_tag_from_zone_id(c.poi.get("zone") or "") == "Z2"


def test_cell_click_tracks_and_plots_the_summon_spot():
    """Clicking a row fires the page's _track_soulstone with that spot, so the
    real page toggles the minimap-style tracker and plots the labeled pin."""
    page = _FakePage()
    view = SoulstoneListView(page)
    cell = view._cells[0]
    cell.pick.emit(cell.poi)
    assert page.tracked and page.tracked[0]["id"] == cell.poi["id"]


def test_tracked_row_shows_tracking_label():
    """A spot the tracker already targets renders its '◈ TRACKING' row, like
    the dungeon list's tracked cells."""
    key = SoulstoneMixin._soulstone_track_key(_FakePage(), codex.soulstone_pois()[0])
    page = _FakePage(tracker=_Tracker(tracked=[("soulstone", key)]))
    view = SoulstoneListView(page)
    # isHidden(), not isVisible(): the list is never shown in the test, so a
    # widget only reports its own explicit visibility state.
    assert not view._cells[0]._track_lbl.isHidden()
    assert view._cells[1]._track_lbl.isHidden()


def test_untrack_soulstone_clears_pin():
    """Clicking a tracked soulstone row again stops tracking AND clears the
    labeled pin from the codex map — the second click leaves nothing."""
    tracker = _ToggleTracker()
    page = _UntrackPage(tracker)
    poi = codex.soulstone_pois()[0]
    key = SoulstoneMixin._soulstone_track_key(page, poi)

    page._track_soulstone(poi)
    assert tracker.is_tracked("soulstone", key)
    assert len(page._codex_map_widget.pins) == 1
    assert page._codex_map_widget.pins[0]["name"] == poi["name"]

    page._track_soulstone(poi)
    assert not tracker.is_tracked("soulstone", key)
    assert page.clears >= 1
    assert page._codex_map_widget.pins == []


def test_dungeon_cell_tooltip_shows_only_extra_info():
    """Dungeon rows already show boss + level · name on the row, so the
    hover tooltip keeps only the extra info: the rift tracking note, the
    missing-entrance explanation, or nothing at all."""
    from farever_companion.data import dungeons
    from farever_companion.ui.pages.codex.dungeon_list import (
        DungeonListCell, DungeonListView)
    page = _UntrackPage(_ToggleTracker())
    view = DungeonListView(page)
    # normal dungeons: everything is already on the row -> no tooltip
    plain = [c for c in view._cells
             if c.dungeon.get("entrance_zone") != "Rifts" and c.key]
    assert plain
    for c in plain:
        assert c.toolTip() == "", c.toolTip()
    # rifts: the row can't show that it tracks the live/next-due rift
    rifts = [c for c in view._cells if c.dungeon.get("entrance_zone") == "Rifts"]
    assert rifts
    for c in rifts:
        tip = c.toolTip()
        assert "tracks the active rift" in tip.lower(), tip
        assert c._sub_lbl.text() not in tip, tip
    # a row with no resolvable entrance explains why it's dimmed
    d = dungeons.load_dungeons()[0]
    cell = DungeonListCell(page, d, None)
    assert "no entrance location found" in cell.toolTip().lower()


def test_untrack_dungeon_clears_pin():
    """Same toggle behavior on the Dungeon List: the second click on a
    tracked dungeon row untracks it and clears its entrance pin."""
    from farever_companion.data import dungeons
    tracker = _ToggleTracker()
    page = _UntrackPage(tracker)
    d = next((dd for dd in dungeons.load_dungeons()
              if dd.get("entrance_zone") != "Rifts"
              and page._dungeon_track_key(dd) is not None), None)
    assert d is not None
    kind, key = page._dungeon_track_key(d)

    page._track_dungeon(d)
    assert tracker.is_tracked(kind, key)
    assert len(page._codex_map_widget.pins) == 1

    page._track_dungeon(d)
    assert not tracker.is_tracked(kind, key)
    assert page.clears >= 1
    assert page._codex_map_widget.pins == []
