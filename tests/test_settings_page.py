"""Settings page: layout, DPS deep links, and persistence (headless Qt)."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402

from farever_companion.config import Settings  # noqa: E402
from farever_companion.ui.pages.settings import (  # noqa: E402
    SettingsPageMixin, _LOOT_OPTS,
)
from tests.qt_helpers import destroy  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def _qapp():
    """One offscreen QApplication for the whole module (widgets need one)."""
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture(scope="module", autouse=True)
def _moddata(tmp_path_factory):
    """Every test here runs against a throwaway moddata folder.

    `_FakeHost.__init__` builds a real `Settings()` and the page's own `_set()`
    calls `save()`, so a test in this module that does not redirect the folder
    writes the user's LIVE settings.json — the 2026-09-17 reset, triggered by
    `test_settings_tabs_build_lazily` (which takes no fixtures, so no
    function-scoped `monkeypatch.setenv` of its own). tests/conftest.py sets
    the same thing session-wide; this is the module-local pin so the module
    stays safe if it is ever run on its own.
    """
    mp = pytest.MonkeyPatch()
    mp.setenv("FAREVER_MODDATA_DIR", str(tmp_path_factory.mktemp("settings_moddata")))
    yield
    mp.undo()


class _FakeMgr:
    """The manager bits the settings page reaches for: the live-apply hooks
    (the real one pushes a setting onto the core read and repaints the window),
    plus an empty overlay table so the page's other lookups stay no-ops."""

    def __init__(self):
        self.applied: list[tuple[str, object]] = []
        self.overlays: dict[str, object] = {}

    def apply_dummy_range(self, value) -> None:
        self.applied.append(("dps_dummy_range", value))


class _FakeHost(SettingsPageMixin):
    """Minimal stand-in for ControlPanel: settings + the setters the page
    calls, recording every write so tests can assert on them."""

    def __init__(self):
        self.s = Settings()
        self.writes: list[tuple[str, object]] = []
        self.logs: list[str] = []
        self.overlays = {}
        self.overlay_mgr = _FakeMgr()

    def log(self, msg: str) -> None:
        """The real host is ControlPanel, which owns the Activity Log; the
        settings mixins call ``self.log(...)`` freely, so the stand-in records
        it instead of exploding mid-toggle."""
        self.logs.append(msg)

    def _page_container(self, title=None):
        page = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(page)
        v.setContentsMargins(22, 20, 22, 20)
        v.setSpacing(16)
        return page, v

    def _set(self, attr, value):
        setattr(self.s, attr, value)
        self.s.save()
        self.writes.append((attr, value))

    def _set_minimap_layer(self, attr, on):
        self._set(attr, on)


@pytest.fixture(scope="module", autouse=True)
def _themed_app():
    """Apply the theme ONCE for the module, the way the app always does.

    Without this the file inherits whatever font state the process happens
    to be in, and the width assertions move: test_party_inspect calls
    theme.apply() — which permanently registers the app fonts in the font
    database, they cannot be unloaded — then restores the font and stylesheet
    but not that database. So this file measured a fallback font when it ran
    first and the real one when it ran after, and the DPS tab's reflow
    threshold sat between the two.

    Module scope, not per-test: theme.apply() re-registers the fonts and
    re-sets the whole app stylesheet, and doing that for each of this file's
    50+ tests made the file ten times slower (6s -> 62s) for no gain.
    """
    from farever_companion.ui import theme
    app = QtWidgets.QApplication.instance()
    if app is not None:
        theme.apply(app)
    return app


@pytest.fixture()
def host(tmp_path, monkeypatch, _themed_app):
    monkeypatch.setenv("FAREVER_MODDATA_DIR", str(tmp_path))
    h = _FakeHost()
    h._page = h._page_settings()   # keep the widget tree alive for the test
    # Sub-tabs now build lazily; these tests assume the full page is built,
    # so materialize every tab through the same lazy path used at runtime.
    for key in h._TAB_ORDER:
        h._ensure_settings_tab(key)
    return h


def _drain_diag_worker(h, timeout_s: float = 10.0) -> None:
    """Pump the event loop until h's background diagnostics scan lands.

    The scan runs on a CallWorker thread, so tests must pump (never
    worker.wait(): that would block the loop the done signal needs).
    """
    import time
    app = QtWidgets.QApplication.instance()
    end = time.monotonic() + timeout_s
    while time.monotonic() < end:
        app.processEvents()
        w = getattr(h, "_dps_diag_worker", None)
        if w is None or not w.isRunning():
            break
        time.sleep(0.005)
    app.processEvents()
    app.processEvents()


def _by_attr(host):
    return {a: t for a, t in host._settings_toggles}


def _empty_cells(grid):
    """Cells inside a grid's used rectangle that hold no item.

    A tile grid filled two-per-row leaves one of these whenever the tile
    count is odd, and the hole reads as a layout bug rather than as "the
    list ended here" — which is how the Settings grids shipped with a gap.
    """
    occupied = set()
    for i in range(grid.count()):
        r, c, rowspan, colspan = grid.getItemPosition(i)
        for rr in range(r, r + rowspan):
            for cc in range(c, c + colspan):
                occupied.add((rr, cc))
    if not occupied:
        return []
    rows = max(r for r, _ in occupied) + 1
    cols = max(c for _, c in occupied) + 1
    return [(r, c) for r in range(rows) for c in range(cols)
            if (r, c) not in occupied]


def test_settings_tile_grids_leave_no_empty_cells(host):
    """Every tile grid in Settings closes its own trailing row.

    Both Pages sections and the Layers matrix fill two tiles per row, so any
    odd count used to leave the last row half empty. The shipped case was
    Sidebar Elements: the dev-only Dev Icons tile is absent from a frozen
    build, dropping it to three and opening the gap. An odd group in the
    middle of the Layers matrix (it is ONE grid with a row range per group)
    has to be closed too, not just the last one.
    """
    for widget in (host._settings_tab_pages["Pages"],
                   host._settings_tab_pages["Layers"]):
        for grid in widget.findChildren(QtWidgets.QGridLayout):
            assert _empty_cells(grid) == [], "tile grid has a hole in it"


def test_pages_grids_close_up_without_the_dev_only_tile(tmp_path, monkeypatch,
                                                        _themed_app):
    """The frozen-build shape (3 Sidebar Elements, no Dev Icons) is the one
    that regressed, so build it explicitly rather than relying on which
    checkout the suite runs in."""
    from farever_companion.ui import control_panel

    monkeypatch.setenv("FAREVER_MODDATA_DIR", str(tmp_path))
    monkeypatch.setattr(control_panel, "dev_icons_enabled", lambda: False)
    h = _FakeHost()
    h._page = h._tab_pages()
    grids = h._page.findChildren(QtWidgets.QGridLayout)
    assert grids, "the Pages tab built no tile grid at all"
    for grid in grids:
        assert _empty_cells(grid) == []
    assert len(h._sidebar_element_toggles) == 3


def test_layers_matrix_closes_an_odd_group_mid_grid(tmp_path, monkeypatch,
                                                    _themed_app):
    """A group with an odd number of layers is closed in place, so the hole
    cannot reappear in the middle of the matrix when a layer is added or
    removed from a group."""
    from farever_companion.ui.pages.settings import layers as layers_mod
    from farever_companion.ui.pages.settings import _shared

    monkeypatch.setenv("FAREVER_MODDATA_DIR", str(tmp_path))
    real = _shared._LAYER_GROUPS
    # 5 layers in the first group -> its last row would be half empty, and it
    # is followed by a second group, so a "last row of the grid" fix misses it.
    monkeypatch.setattr(_shared, "_LAYER_GROUPS",
                        [("HUD & MINIMAP", real[0][1][:5]), real[1]])
    monkeypatch.setattr(layers_mod, "_LAYER_GROUPS", _shared._LAYER_GROUPS)

    h = _FakeHost()
    holder = QtWidgets.QWidget()
    h._holder = holder                       # keep the tree alive
    h._build_layer_matrix(QtWidgets.QVBoxLayout(holder))

    grid = holder.findChildren(QtWidgets.QGridLayout)[0]
    assert _empty_cells(grid) == []
    # the odd one out is the tile that spans the closed row
    spanning = [grid.itemAt(i).widget() for i in range(grid.count())
                if grid.getItemPosition(i)[3] == 2]
    assert spanning, "nothing re-spanned the half-empty row"


def test_first_open_lands_on_the_active_tab():
    """Opening Settings must SHOW the tab the strip is on.

    QStackedWidget tracks a POSITION, not a widget. Inserting the freshly
    built tab at its own index slid the placeholder along, and removing that
    placeholder then left currentIndex pointing at whatever had moved into
    the slot. Opening Settings therefore showed a bare placeholder — laid out
    at Qt's default 630x480, so every card inside it was sized for the wrong
    width — under a tab strip reading "Layers", and the background pre-warm
    painted a different tab's content into that same slot afterwards. That is
    the "gap on first open that a tab switch fixes" (2026-09-27).
    """
    h = _FakeHost()
    h._page = h._page_settings()      # keep the widget tree alive
    assert h._settings_stack.currentIndex() == 0
    shown = h._settings_stack.currentWidget()
    assert shown is h._settings_tab_pages["Layers"]
    assert isinstance(shown, QtWidgets.QScrollArea)
    # the real content is in that slot, not an empty placeholder
    assert shown.widget().findChildren(QtWidgets.QFrame)


def test_background_prewarm_never_switches_the_visible_tab():
    """Building a tab off-screen (what the pre-warm timer does) must never
    change what the user is looking at — including when the tab being built
    lands BEFORE the current one and slides every later slot along.

    This one guards the invariant rather than reproducing the 2026-09-27
    first-open bug: Qt's own index bookkeeping already covers the
    insert-before/remove-before case, and the case that actually broke is
    the placeholder being replaced WHILE it is the visible widget, which
    `test_first_open_lands_on_the_active_tab` pins."""
    h = _FakeHost()
    h._page = h._page_settings()
    h._settings_tabs._pick("Rift")            # the user is on Rift, index 3
    shown = h._settings_stack.currentWidget()
    assert shown is h._settings_tab_pages["Rift"]
    for key in ("Pages", "HUD's", "DPS", "Layers"):
        h._ensure_settings_tab(key)
        assert h._settings_stack.currentWidget() is shown, \
            f"pre-warming {key!r} swapped the visible tab"
        assert h._settings_tabs.currentText() == "Rift"


def test_pages_tab_headers_do_not_absorb_the_leftover_height(tmp_path,
                                                              monkeypatch,
                                                              _themed_app):
    """The Pages tab is the only Settings tab whose content is shorter than
    the viewport, so it is the only one that needs the trailing stretch the
    other builders already end with. Without it QVBoxLayout had nowhere to put
    the leftover height and handed it to the SectionHeaders, which measured
    186px tall for a one-line title — the dead band under both section titles
    that stayed broken no matter how many times you switched tabs (2026-09-27).
    """
    from farever_companion.ui import components as C

    monkeypatch.setenv("FAREVER_MODDATA_DIR", str(tmp_path))
    h = _FakeHost()
    h._page = h._tab_pages()
    h._page.resize(1000, 700)
    h._page.show()
    app = QtWidgets.QApplication.instance()
    for _ in range(4):
        app.processEvents()
    try:
        v = h._page.layout()
        last = v.itemAt(v.count() - 1)
        assert last.widget() is None and last.layout() is None, \
            "the Pages tab must end with a stretch item, not content"
        headers = h._page.findChildren(C.SectionHeader)
        assert len(headers) == 2
        for hdr in headers:
            assert hdr.height() <= hdr.sizeHint().height() + 2, \
                f"section header is {hdr.height()}px tall for a one-line title"
    finally:
        destroy(h._page)


def test_matrix_builds_all_toggles_and_links(host):
    toggles = _by_attr(host)
    # 6 common x 2 + orbs dungeon + 4 map-only + players/loot (hud+dungeon)
    assert len(toggles) == 22
    linkable = [r for r in host._matrix_rows.values() if r["linkable"]]
    assert len(linkable) == 7
    for r in linkable:
        assert r["link"] is not None
    # The MASTER LINK bar is gone (2026-09-19) — rows carry their own LINK
    # buttons only, and no bulk switch is built or reachable.
    assert not hasattr(host, "_master_link")
    assert not hasattr(host, "_set_master_link")


def test_players_and_loot_layers_sit_in_the_top_group(host):
    """Regression (2026-09-19): Players / Dropped loot lived in their own
    "DUNGEON HUD" group at the very bottom of the matrix, below the fold —
    so the switches read as missing. They are HUD layers: keep them in the
    first group, and keep any DUNGEON-only group from coming back."""
    from farever_companion.ui.pages.settings._shared import _LAYER_GROUPS

    assert _LAYER_GROUPS[0][0] == "HUD & MINIMAP"
    top = [row[0] for row in _LAYER_GROUPS[0][1]]
    assert "players" in top and "loot" in top
    assert not any("DUNGEON HUD" in g for g, _rows in _LAYER_GROUPS)
    # and the built matrix still carries both rows (with their surfaces)
    assert host._matrix_rows["players"]
    assert host._matrix_rows["loot"]


def test_matrix_rows_use_atlas_sprites_with_dot_fallback(host):
    """Rows with a minimap-atlas sprite show a 16px marker pixmap; rows
    without one still get a painted color dot (never an empty cell)."""
    with_sprite = {"chests", "gatherables", "orbs", "obelisks",
                   "dungeons", "vendors", "soulstones", "spark_mobs"}
    no_sprite = {"enemies", "companions", "players", "loot"}
    for key, row in host._matrix_rows.items():
        pm = row["icon"].pixmap()
        assert pm is not None and not pm.isNull(), key
        assert (pm.width(), pm.height()) == (16, 16), key
        # sprites have visible non-transparent pixels, dots are centered
        img = pm.toImage().convertToFormat(
            QtGui.QImage.Format_ARGB32)
        opaque = 0
        for y in range(img.height()):
            rowv = memoryview(img.constScanLine(y)).cast("B")
            for x in range(img.width()):
                if rowv[4 * x + 3] > 8:
                    opaque += 1
        assert opaque > 0, key
    assert set(host._matrix_rows) >= with_sprite | no_sprite


def test_linked_rows_mirror_hud_to_map_both_directions(host):
    toggles = _by_attr(host)
    # Link every linkable row through the per-row buttons (what the removed
    # MASTER switch used to do in one click).
    for r in host._matrix_rows.values():
        if r["linkable"]:
            r["link"].setChecked(True)
    assert len(host.s.layer_links) == 7

    assert host.s.show_enemies is False         # default: HUD enemies off
    toggles["show_enemies"].setChecked(True)    # HUD enemies -> on
    # mirrored to the minimap setting + its switch widget
    assert host.s.minimap_enemies is True
    assert toggles["minimap_enemies"].isChecked() is True
    assert ("minimap_enemies", True) in host.writes

    # reverse direction: Map -> HUD also mirrors
    toggles["minimap_chests"].setChecked(False)
    assert host.s.show_chests is False
    assert toggles["show_chests"].isChecked() is False


def test_stale_master_link_setting_folds_into_row_links(host):
    """A settings.json from before the MASTER LINK bar was removed keeps its
    intent: the flag is folded into layer_links and cleared, so nothing is
    left linked-but-uneditable."""
    host._page = None                       # drop the built page
    host.s.layer_link_master = True
    host.s.layer_links = []
    host._page = host._page_settings()      # rebuild the matrix
    for key in host._TAB_ORDER:
        host._ensure_settings_tab(key)
    assert host.s.layer_link_master is False
    assert len(host.s.layer_links) == 7
    assert host._matrix_rows["chests"]["link"].isChecked() is True
    assert host._matrix_rows["chests"]["link"].isEnabled() is True


def test_per_row_link_mirrors_only_that_row(host):
    toggles = _by_attr(host)
    row = host._matrix_rows["chests"]
    row["link"].setChecked(True)                # link only chests
    assert host.s.layer_links == ["chests"]
    assert host.s.layer_link_master is False     # no bulk switch exists

    toggles["show_chests"].setChecked(False)
    assert host.s.minimap_chests is False        # linked row mirrors
    assert toggles["minimap_chests"].isChecked() is False

    untouched = host.s.minimap_enemies            # default False; must not move
    toggles["show_enemies"].setChecked(False)    # unlinked row does NOT
    assert host.s.minimap_enemies == untouched
    assert toggles["minimap_enemies"].isChecked() == untouched


def test_unlinked_row_stops_mirroring(host):
    toggles = _by_attr(host)
    row = host._matrix_rows["orbs"]
    row["link"].setChecked(True)
    assert host.s.layer_links == ["orbs"]
    row["link"].setChecked(False)
    assert host.s.layer_links == []

    toggles["show_orbs"].setChecked(False)
    assert host.s.minimap_orbs is True           # no longer mirrored
    assert toggles["minimap_orbs"].isChecked() is True


def test_steppers_write_settings(host):
    by_attr = dict(host._settings_steppers)
    assert len(by_attr) == 16          # 10 HUD + 2 minimap + 4 DPS meter
    by_attr["enemy_count"].setValue(9)
    assert ("enemy_count", 9) in host.writes
    by_attr["max_dist"].setValue(400)
    assert host.s.max_dist == 400
    by_attr["minimap_size"].setValue(500)
    assert host.s.minimap_size == 500
    by_attr["minimap_icon_size"].setValue(30)
    assert host.s.minimap_icon_size == 30
    assert ("minimap_icon_size", 30) in host.writes

    # Combat DPS Meter card steppers persist too
    by_attr["dps_top_count"].setValue(10)
    assert host.s.dps_top_count == 10
    assert ("dps_top_count", 10) in host.writes
    by_attr["heals_top_count"].setValue(4)
    assert host.s.heals_top_count == 4
    by_attr["dps_log_keep_days"].setValue(30)
    assert host.s.dps_log_keep_days == 30
    by_attr["dps_fights_recent_days"].setValue(14)
    assert host.s.dps_fights_recent_days == 14
    assert ("heals_top_count", 4) in host.writes


def test_zoom_slider_writes_float(host):
    host._settings_zoom.setValue(32)
    assert host.s.minimap_zoom == 32.0
    assert ("minimap_zoom", 32.0) in host.writes


def test_loot_filters_have_all_option_and_write(host):
    assert _LOOT_OPTS == ["Off", "All", "Uncommon+", "Rare+", "Epic+",
                          "Legendary"]
    loot_segs = host._loot_segs
    assert len(loot_segs) == 2              # Entity + Dungeon
    for seg in loot_segs:
        opts = [b.text() for b in seg._btns.values()]
        assert opts == _LOOT_OPTS

    # click "All" on the entity filter -> setting saved (SegmentedControl
    # only emits currentChanged on real clicks)
    loot_segs[0]._btns["All"].click()
    assert host.s.loot_filter == "All"
    assert ("loot_filter", "All") in host.writes
    loot_segs[1]._btns["Legendary"].click()
    assert host.s.dungeon_loot_filter == "Legendary"


def test_tabs_built_and_switch(host):
    assert [t for t in host._settings_tabs._btns] == \
        ["Layers", "HUD's", "DPS", "Rift", "Pages", "Dev"]
    assert host._settings_stack.count() == 6
    host._settings_tabs._pick("DPS")
    assert host._settings_stack.currentIndex() == 2
    host._settings_tabs._pick("Rift")
    assert host._settings_stack.currentIndex() == 3
    host._settings_tabs._pick("Layers")
    assert host._settings_stack.currentIndex() == 0
    # Dev is a developer-run tab: present in the strip and the stack (indices
    # stay stable for the lazy builders), but its button is hidden in a normal
    # run. isHidden() is the explicit-hide check; isVisible() would also be
    # False for every tab of an unshown parent.
    assert host._settings_tabs._btns["Dev"].isHidden() is True


def test_settings_tabs_build_lazily():
    """Opening Settings only builds the active (Layers) tab; the rest are
    built on first use, and placeholder slots keep the stack indices stable.
    """
    h = _FakeHost()
    h._page = h._page_settings()   # keep the widget tree alive
    assert set(h._settings_tab_pages) == {"Layers"}
    assert h._settings_stack.count() == 6     # placeholders hold the slots

    # first open of a tab builds it on demand and lands on the right index
    h._settings_tabs._pick("HUD's")
    assert "HUD's" in h._settings_tab_pages
    assert h._settings_stack.currentIndex() == 1

    # picking the same tab again never rebuilds
    built = dict(h._settings_tab_pages)
    h._settings_tabs._pick("HUD's")
    assert h._settings_tab_pages == built

    h._settings_tabs._pick("Pages")
    assert h._settings_stack.currentIndex() == 4
    assert set(h._settings_tab_pages) == {"Layers", "HUD's", "Pages"}

    # the hidden Dev tab still builds through the same lazy path
    h._settings_tabs._pick("Dev")
    assert h._settings_stack.currentIndex() == 5
    assert set(h._settings_tab_pages) == {"Layers", "HUD's", "Pages", "Dev"}


def test_the_dev_tab_shows_in_a_dev_run_and_stays_hidden_otherwise(
        monkeypatch, tmp_path):
    """The Dev tab is a run-log.bat tab (FAREVER_DEV_CRASH_LOG). A normal run
    hides its button; a dev run shows it and it builds the bridge dev-log row."""
    monkeypatch.delenv("FAREVER_DEV_CRASH_LOG", raising=False)
    normal = _FakeHost()
    normal._page = normal._page_settings()
    assert normal._settings_tabs._btns["Dev"].isHidden() is True

    monkeypatch.setenv("FAREVER_DEV_CRASH_LOG", str(tmp_path / "run.log"))
    dev = _FakeHost()
    dev._page = dev._page_settings()
    assert dev._settings_tabs._btns["Dev"].isHidden() is False
    dev._settings_tabs._pick("Dev")
    assert dev._settings_stack.currentIndex() == 5
    assert getattr(dev, "_dev_flag_toggle", None) is not None
    # ...and the run rows say the two things this run already has on
    rows = dict(dev._dev_run_rows())
    assert "Verbose" in rows["Activity Log"]
    assert str(tmp_path / "run.log") in rows["Crash log"]


def test_the_dev_tab_toggle_writes_and_removes_the_bridge_flag(
        monkeypatch, tmp_path):
    """The bridge-log control: the flag file IS the state (no settings field),
    and it is written/removed through the same bridge_log helpers the launcher
    uses, so a dev can start and stop the DLL's own log mid-session."""
    from farever_companion.core import bridge_log
    game = tmp_path / "game"
    game.mkdir()
    monkeypatch.setenv("FAREVER_DEV_CRASH_LOG", str(tmp_path / "run.log"))
    monkeypatch.setattr(_FakeHost, "_find_farever_game_dir",
                        lambda self: str(game))

    h = _FakeHost()
    h._page = h._page_settings()
    h._settings_tabs._pick("Dev")
    flag = bridge_log.dev_flag_path(str(game))
    assert not os.path.isfile(flag)
    before = list(h.writes)          # building the page may write; the toggle must not

    h._dev_flag_toggle.setChecked(True)
    assert os.path.isfile(flag)
    named = open(flag, encoding="utf-8").read().strip()
    assert named.endswith("bridge_dev.log")          # the log it points at
    assert any("bridge dev log enabled" in msg for msg in h.logs)

    h._dev_flag_toggle.setChecked(False)
    assert not os.path.isfile(flag)
    assert h.writes == before        # the flag file IS the state: no Settings field
    assert any("bridge dev log disabled" in msg for msg in h.logs)


def test_the_dev_update_check_picker_persists_and_applies(monkeypatch, tmp_path):
    """The startup update check is a three-state Settings field, so a source run
    can reach the update pill without a build and a packaged build can turn it
    off. The Dev picker writes `check_updates`, and a mode that polls starts this
    session's check immediately (the startup gate is read once, so a restart
    would otherwise be needed). The host's `_start_update_check` is stubbed, so
    nothing here touches the network."""
    monkeypatch.setenv("FAREVER_DEV_CRASH_LOG", str(tmp_path / "run.log"))
    h = _FakeHost()
    started: list[bool] = []
    h._start_update_check = lambda: started.append(True)
    h._page = h._page_settings()
    h._settings_tabs._pick("Dev")

    seg = h._dev_update_seg
    assert seg.currentText() == "Packaged only"     # the default, and conservative
    assert ("check_updates", "Packaged only") not in h.writes

    seg._btns["Always"].click()                     # a real click on the segment
    assert h.s.check_updates == "Always"
    assert ("check_updates", "Always") in h.writes
    assert started == [True]                        # checking now, no restart
    assert any("update check: Always" in msg for msg in h.logs)

    # `Never` persists and starts nothing - it does not poll this run either
    seg._btns["Never"].click()
    assert h.s.check_updates == "Never"
    assert ("check_updates", "Never") in h.writes
    assert started == [True], "Never started a check"
    assert any("update check: Never" in msg for msg in h.logs)
    assert seg.currentText() == "Never"


def test_the_update_check_mode_survives_a_save_and_a_load(monkeypatch, tmp_path):
    """The mode is what the file carries, so a restart keeps the opt-out.

    The whole point of `Never` is that it outlives the session that chose it; a
    save that dropped the field (or wrote it back as the default) would re-arm
    the poll on the next start with nothing on screen to say so.
    """
    monkeypatch.setenv("FAREVER_MODDATA_DIR", str(tmp_path))
    for mode in ("Never", "Always"):
        Settings(check_updates=mode).save()
        assert Settings.load().check_updates == mode, mode

    # Choosing the DEFAULT again has to be tested from a LOADED instance, which
    # is the shape the app has: `_preserve_stored_values` lets the file's
    # non-default value win over a BARE instance still sitting at its default
    # (the 2026-09-17 wipe guard), so a bare `Settings()` writing the default
    # back is deliberately a no-op - and would make this assertion pass for the
    # wrong reason. The live instance owns the file, so its choice sticks.
    live = Settings.load()                      # "Always" is on disk by now
    assert live.check_updates == "Always"
    live.check_updates = "Packaged only"
    live.save()
    assert Settings.load().check_updates == "Packaged only"


def test_a_boolean_era_settings_file_loads_into_an_update_check_mode(
        monkeypatch, tmp_path):
    """A stored `true`/`false` is migrated on LOAD, not left in the field.

    `Settings.load()` takes whatever JSON is on disk, so the old boolean would
    otherwise sit in `check_updates` where every reader has to defend against it
    (and the Dev picker would show no segment selected at all, since "True" is
    not one of the modes).
    """
    import json

    from farever_companion.config import store

    monkeypatch.setenv("FAREVER_MODDATA_DIR", str(tmp_path))
    path = store._settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    for stored, mode in ((True, "Always"), (False, "Packaged only"),
                         ("nonsense", "Packaged only")):
        path.write_text(json.dumps({"check_updates": stored}), encoding="utf-8")
        assert Settings.load().check_updates == mode, stored


def test_dps_tab_renders_all_sections_together(host):
    """Opening Settings > DPS shows the whole DPS page at once: the Combat
    DPS Meter card (range / view / row steppers; the solo
    toggle moved to the DPS tab Top DPS card), all three capture-engine
    option cards, and the live diagnostics card — with the meter and
    diagnostics sitting side by side, never full-width.
    """
    win = QtWidgets.QMainWindow()
    win.resize(1720, 900)   # wide enough for all three engine cards on one row
    inner = QtWidgets.QWidget()
    lay = QtWidgets.QVBoxLayout(inner)
    lay.addWidget(host._page)   # re-home the page into a real window
    win.setCentralWidget(inner)
    win.show()
    QtWidgets.QApplication.instance().processEvents()

    host._settings_tabs._pick("DPS")
    QtWidgets.QApplication.instance().processEvents()
    assert host._settings_tabs.currentText() == "DPS"
    assert host._settings_stack.currentIndex() == 2

    # One marker widget per section — all must be visible together on DPS.
    assert set(host._dps_mode_cards) == {"proxy", "injector", "memory"}
    sections = {
        "meter default view": host._dps_view_seg,
        "engine option card (proxy)": host._dps_mode_cards["proxy"],
        "engine option card (injector)": host._dps_mode_cards["injector"],
        "engine option card (memory)": host._dps_mode_cards["memory"],
        "diagnostics status": host._dps_diag_status_lbl,
        "diagnostics source": host._dps_diag_source_lbl,
        "diagnostics PID": host._dps_diag_pid_lbl,
    }
    for label, w in sections.items():
        assert w is not None and w.isVisible(), \
            f"{label} not visible on the DPS tab"

    # Meter + diagnostics share one row, side by side (never full-width).
    m, d = host._dps_card, host._dps_diag_card
    assert m is not None and d is not None
    assert m.isVisible() and d.isVisible()
    assert m is not d
    assert abs(m.y() - d.y()) < 4, "meter + diagnostics should share a row"
    assert m.x() != d.x(), "meter + diagnostics should be side by side"

    # The three engine option cards also sit side by side on grid row 0.
    xs = [host._dps_mode_cards[k].x() for k in ("proxy", "injector", "memory")]
    ys = [host._dps_mode_cards[k].y() for k in ("proxy", "injector", "memory")]
    assert xs == sorted(xs) and len(set(xs)) == 3
    assert max(ys) - min(ys) < 4

    # Everything belongs to the DPS tab: leaving it hides all sections.
    host._settings_tabs._pick("Layers")
    QtWidgets.QApplication.instance().processEvents()
    for label, w in sections.items():
        assert not w.isVisible(), f"{label} still visible after leaving DPS"
    destroy(win)


def _dps_tab_content_widths(host):
    """(content, viewport, edges) of the DPS tab after a resize + relayout."""
    # Drain in a loop, not once: a resize is a queued resize -> layout
    # request -> polish -> relayout chain, and ONE processEvents() is only
    # enough to get through it when the queue is otherwise empty. In a full
    # run the queue is not, so the cards were still measured on their
    # pre-resize geometry and the narrow case looked like it had not reflowed
    # (all three engine cards still on y=36). Same idiom the other UI tests
    # use.
    app = QtWidgets.QApplication.instance()
    for _ in range(40):
        app.processEvents()
    scroll = host._settings_tab_pages["DPS"]
    content = scroll.widget()
    # The old engine segmented control was replaced by the three option cards,
    # so the wide elements are: the meter, the diagnostics card and the cards.
    widgets = [host._dps_card, host._dps_diag_card,
               *host._dps_mode_cards.values()]
    edges = [w.mapTo(content, QtCore.QPoint(0, 0)).x() + w.width()
             for w in widgets]
    return content, scroll.viewport(), edges


def test_dps_tab_never_overflows_horizontally(host):
    """The DPS tab's wide elements (engine segmented control, option cards,
    meter + diagnostics) never push content past the viewport: wide windows
    keep three engine cards in a row, narrow windows flow them onto extra
    rows instead of clipping horizontally."""
    win = QtWidgets.QMainWindow()
    inner = QtWidgets.QWidget()
    lay = QtWidgets.QVBoxLayout(inner)
    lay.addWidget(host._page)
    win.setCentralWidget(inner)
    win.show()
    host._settings_tabs._pick("DPS")

    # Wide: everything fits on its original single rows.
    win.resize(1720, 900)
    content, vp, edges = _dps_tab_content_widths(host)
    assert content.width() <= vp.width() + 1
    for e in edges:
        assert e <= content.width() + 2, f"edge {e} past {content.width()}"
    ys = [c.y() for c in host._dps_mode_cards.values()]
    assert max(ys) - min(ys) < 4, "wide window keeps the 3 engine cards on one row"
    assert abs(host._dps_card.y() - host._dps_diag_card.y()) < 4

    # Narrow: the same page re-flows instead of overflowing. Measured under
    # the theme, the three engine cards keep one row down to a ~728 px
    # viewport and only need extra rows below that; meter + diagnostics hold
    # one row until ~528 px. The old 1000 px case asserted a wrap there and
    # only ever passed because the harness measured a FALLBACK font (wider
    # than the real one), which pushed the threshold up — it was the order of
    # the files, not the layout, that decided it. So: no width may overflow,
    # and the two widths that genuinely need a reflow must show one.
    for w in (1200, 1000, 900, 800, 700, 600):
        win.resize(w, 900)
        content, vp, edges = _dps_tab_content_widths(host)
        assert content.width() <= vp.width() + 1, (
            f"content {content.width()} wider than viewport {vp.width()}"
            f" at window {w}")
        for e in edges:
            assert e <= content.width() + 2, (
                f"edge {e} past {content.width()} at window {w}")

    # 700 px window: the three engine cards must flow onto extra rows
    win.resize(700, 900)
    _dps_tab_content_widths(host)
    ys = [c.y() for c in host._dps_mode_cards.values()]
    assert max(ys) - min(ys) >= 4, \
        "narrow window must wrap engine cards onto extra rows"
    # ...and at 600 px even the meter/diagnostics pair stacks
    win.resize(600, 900)
    _dps_tab_content_widths(host)
    assert abs(host._dps_card.y() - host._dps_diag_card.y()) >= 4, \
        "narrow window must wrap meter + diagnostics onto extra rows"
    destroy(win)


def test_rift_tab_writes_and_predictor(host):
    host._settings_tabs._pick("Rift")
    host._rift_seg._btns["Active"].click()
    assert host.s.show_rift_timer == "Active"
    assert ("show_rift_timer", "Active") in host.writes
    # predictor card filled at build (pure clock, no game)
    assert host._rift_next_lbl.text() != "Next Rift: --"


def test_hud_display_config_writes(host):
    by_attr = dict(host._hud_toggles)
    assert set(by_attr) == {
        "entity_bare", "entity_transparent", "show_compass",
        "auto_select_next_collectible", "limit_by_zone",
        "entity_hide_collected", "PlayerNames",
        # per-section switches (2026-09-19): the HUD's titlebar buttons
        "entity_show_rift", "show_loot",
    }
    by_attr["entity_bare"].setChecked(True)
    assert host.s.entity_bare is True
    assert ("entity_bare", True) in host.writes

    by_attr["entity_transparent"].setChecked(True)
    assert host.s.entity_transparent is True
    assert ("entity_transparent", True) in host.writes

    by_attr["show_compass"].setChecked(True)
    assert host.s.show_compass is True
    assert ("show_compass", True) in host.writes

    by_attr["auto_select_next_collectible"].setChecked(False)
    assert host.s.auto_select_next_collectible is False
    assert ("auto_select_next_collectible", False) in host.writes


def test_minimap_section_writes(host):
    by_attr = dict(host._minimap_toggles)
    assert set(by_attr) == {
        "minimap_bare", "minimap_transparent", "minimap_rotate",
        "minimap_texture", "minimap_icons",
        "minimap_hide_collected", "minimap_limit_by_zone",
        "minimap_compass_labels",
    }
    # N/E/S/W labels default ON (own toggle, independent of the needle)
    assert host.s.minimap_compass_labels is True
    by_attr["minimap_compass_labels"].setChecked(False)
    assert host.s.minimap_compass_labels is False
    assert ("minimap_compass_labels", False) in host.writes
    by_attr["minimap_transparent"].setChecked(True)      # default False
    assert host.s.minimap_transparent is True
    assert ("minimap_transparent", True) in host.writes
    by_attr["minimap_rotate"].setChecked(True)          # default False
    assert host.s.minimap_rotate is True
    assert ("minimap_rotate", True) in host.writes
    by_attr["minimap_texture"].setChecked(False)         # default True
    assert host.s.minimap_texture is False
    by_attr["minimap_icons"].setChecked(False)           # default True
    assert host.s.minimap_icons is False
    assert ("minimap_icons", False) in host.writes
    by_attr["minimap_hide_collected"].setChecked(True)   # default False
    assert host.s.minimap_hide_collected is True
    by_attr["minimap_limit_by_zone"].setChecked(True)    # default False
    assert host.s.minimap_limit_by_zone is True

    # shape segmented -> writes on click (currentChanged fires on clicks)
    assert host._minimap_shape.currentText() == "Square"  # default
    host._minimap_shape._btns["Circle"].click()
    assert host.s.minimap_shape == "Circle"
    assert ("minimap_shape", "Circle") in host.writes


def test_dungeon_overlay_section_writes(host):
    by_attr = dict(host._dungeon_toggles)
    assert set(by_attr) == {"dungeon_bare", "dungeon_transparent",
                            "dungeon_hide_collected", "rift_clone_track",
                            "dungeon_show_food", "dungeon_show_rift",
                            "dungeon_show_boss", "dungeon_show_enemies",
                            "dungeon_show_players", "dungeon_show_loots",
                            "dungeon_show_orbs"}
    # defaults: bare off, transparent off, hide-collected on, boss auto-track
    # on; every section visible (players/loots/orbs ride dungeon_layers, the
    # four new bools default True)
    assert host.s.dungeon_bare is False
    assert host.s.rift_clone_track is True
    assert host.s.dungeon_show_food is True
    assert host.s.dungeon_show_rift is True
    assert host.s.dungeon_show_boss is True
    assert host.s.dungeon_show_enemies is True
    assert host.s.dungeon_show_players is True
    assert host.s.dungeon_show_loots is True
    assert host.s.dungeon_show_orbs is True

    by_attr["dungeon_bare"].setChecked(True)          # default False
    assert host.s.dungeon_bare is True
    assert ("dungeon_bare", True) in host.writes
    by_attr["dungeon_transparent"].setChecked(True)   # default False
    assert host.s.dungeon_transparent is True
    assert ("dungeon_transparent", True) in host.writes
    by_attr["dungeon_hide_collected"].setChecked(False)  # default True
    assert host.s.dungeon_hide_collected is False
    assert ("dungeon_hide_collected", False) in host.writes
    by_attr["rift_clone_track"].setChecked(False)  # default True
    assert host.s.rift_clone_track is False
    assert ("rift_clone_track", False) in host.writes
    # 2026-09-18 per-section toggles write through
    by_attr["dungeon_show_food"].setChecked(False)     # default True
    assert host.s.dungeon_show_food is False
    assert ("dungeon_show_food", False) in host.writes
    by_attr["dungeon_show_enemies"].setChecked(False)  # default True
    assert host.s.dungeon_show_enemies is False
    assert ("dungeon_show_enemies", False) in host.writes
    by_attr["dungeon_show_rift"].setChecked(False)     # default True
    assert host.s.dungeon_show_rift is False
    by_attr["dungeon_show_boss"].setChecked(False)     # default True
    assert host.s.dungeon_show_boss is False
    by_attr["dungeon_show_players"].setChecked(False)  # default True
    assert host.s.dungeon_show_players is False
    assert ("dungeon_show_players", False) in host.writes
    by_attr["dungeon_show_loots"].setChecked(False)    # default True
    assert host.s.dungeon_show_loots is False
    by_attr["dungeon_show_orbs"].setChecked(False)     # default True
    assert host.s.dungeon_show_orbs is False


def test_top_dps_overlay_section_writes(host):
    """DPS tab Top DPS card: borderless / transparent / solo-only write to
    the same dps_* settings the DPS tab and overlay use."""
    by_attr = dict(host._top_dps_toggles)
    assert set(by_attr) == {"dps_bare", "dps_transparent", "dps_solo_only",
                            "dps_pin_me", "dps_instances_only",
                            "dps_auto_reset_zone"}
    # defaults: bare off, transparent off, solo-only on, pin-me on,
    # instances-only ON (dungeon-only out of the box), auto-reset-zone on
    assert host.s.dps_bare is False
    assert host.s.dps_transparent is False
    assert host.s.dps_solo_only is True
    assert host.s.dps_pin_me is True
    assert host.s.dps_instances_only is True
    assert host.s.dps_auto_reset_zone is True

    by_attr["dps_bare"].setChecked(True)          # default False
    assert host.s.dps_bare is True
    assert ("dps_bare", True) in host.writes
    by_attr["dps_transparent"].setChecked(True)   # default False
    assert host.s.dps_transparent is True
    assert ("dps_transparent", True) in host.writes
    by_attr["dps_solo_only"].setChecked(False)    # default True
    assert host.s.dps_solo_only is False
    assert ("dps_solo_only", False) in host.writes
    by_attr["dps_pin_me"].setChecked(False)       # default True
    assert host.s.dps_pin_me is False
    assert ("dps_pin_me", False) in host.writes
    by_attr["dps_instances_only"].setChecked(False)  # default True
    assert host.s.dps_instances_only is False
    assert ("dps_instances_only", False) in host.writes
    by_attr["dps_auto_reset_zone"].setChecked(False)  # default True
    assert host.s.dps_auto_reset_zone is False
    assert ("dps_auto_reset_zone", False) in host.writes

    # Boss Clear Range lives on THIS card, beside Auto-Reset Zone: both are
    # fight-boundary rules ("when does one fight end and the next begin"),
    # not meter chrome. The setting, its 1 Hz drain and the core rule all
    # existed with no UI at all, so the feature was unreachable.
    bcr = host._boss_clear_range
    assert bcr.value() == 30                    # the shipped default
    bcr.setValue(60)
    assert host.s.dps_boss_clear_range == 60
    assert ("dps_boss_clear_range", 60) in host.writes
    bcr.setValue(0)                             # 0 = proximity never closes a run
    assert host.s.dps_boss_clear_range == 0
    assert ("dps_boss_clear_range", 0) in host.writes


def test_the_dummy_card_says_the_hud_is_open_world_only(host):
    """The HUD never appears inside a dungeon or a rift, and no number in metres
    can say that: the rule is placement, so the card that owns the radius states
    it in words (the same promise the Overlays page card makes about the same
    window)."""
    text = " ".join(lbl.text()
                   for lbl in host._page.findChildren(QtWidgets.QLabel))
    assert "never appears inside a dungeon or a rift" in text


def test_the_dummy_card_owns_the_dummy_settings(host):
    """The Test Dummy card is the dummy feature's own home, so the radius lives
    there and NOT on the Top DPS card: one number decides when the HUD surfaces
    at a dummy, when a test engages and when one ends, and reading like a meter's
    option was what made it wrong where it was."""
    assert not hasattr(host, "_top_dps_range")   # gone from the meter card
    st = host._dummy_range
    assert st.value() == 5                      # default radius (metres)
    st.setValue(35)
    assert host.s.dps_dummy_range == 35
    assert ("dps_dummy_range", 35) in host.writes
    # the number is the CORE read's gate, so it is also handed over live: the
    # 1 Hz drain that usually carries it would leave the HUD painting (and a
    # test engaging on) the radius it had before the edit
    assert host.overlay_mgr.applied == [("dps_dummy_range", 35)]
    st.setValue(0)                              # 0 = no distance cap
    assert host.s.dps_dummy_range == 0
    assert ("dps_dummy_range", 0) in host.writes
    assert host.overlay_mgr.applied[-1] == ("dps_dummy_range", 0)

    # Auto On is on by default and writes the setting Rule F reads
    toggles = dict(host._dummy_toggles)
    assert set(toggles) == {"dps_dummy_auto", "dummy_bare", "dummy_transparent"}
    assert host.s.dps_dummy_auto is True
    host._set_dummy_auto(False)
    assert host.s.dps_dummy_auto is False
    assert ("dps_dummy_auto", False) in host.writes

    # The target length is the test's other end signal, and it is a setting for
    # the same reason the radius is: the rules live in core, this is the user's
    # number. 0 is the free-running escape hatch, so it must be reachable.
    tgt = host._dummy_target
    assert tgt.value() == 60                     # default length (seconds)
    tgt.setValue(45)
    assert host.s.dps_dummy_target == 45
    assert ("dps_dummy_target", 45) in host.writes
    tgt.setValue(0)                              # 0 = no target, run free
    assert host.s.dps_dummy_target == 0
    assert ("dps_dummy_target", 0) in host.writes

    # The clean-run limit: how much incoming damage (as a share of the test's
    # own) still counts as a measurement. 0 flags any damage at all.
    lim = host._dummy_taken_limit
    assert lim.value() == 5
    lim.setValue(15)
    assert host.s.dps_dummy_taken_pct == 15
    assert ("dps_dummy_taken_pct", 15) in host.writes
    lim.setValue(0)
    assert host.s.dps_dummy_taken_pct == 0

    # Auto Re-arm: the idle wait before a test the player has finished ends
    # itself and resets — a dummy keeps the game "in combat" at any range
    # inside its yard, so this is what replaces "walk ~30 m away". 0 = off.
    rearm = host._dummy_rearm
    assert rearm.value() == 5                     # default wait (seconds)
    rearm.setValue(12)
    assert host.s.dps_dummy_rearm == 12
    assert ("dps_dummy_rearm", 12) in host.writes
    rearm.setValue(0)                             # 0 = never auto-end
    assert host.s.dps_dummy_rearm == 0
    assert ("dps_dummy_rearm", 0) in host.writes

    # Free Test: a practice run that records nothing. The setting, the 1 Hz
    # drain and the tracker's gate all existed; the TOGGLE did not, so the
    # feature was unreachable from the UI and nothing caught it.
    free = host._dummy_free
    assert free.isChecked() is False            # off by default
    free.setChecked(True)
    assert host.s.dps_dummy_free is True
    assert ("dps_dummy_free", True) in host.writes
    free.setChecked(False)
    assert host.s.dps_dummy_free is False
    assert ("dps_dummy_free", False) in host.writes


def test_show_compass_chip_live_applies_needle(host):
    """Flipping the HUD's Compass Needle chip must show/hide the 3D needle
    live — show_compass is the single shared setting behind all compass
    surfaces (Overlays-page card, this chip, minimap rose, Entity HUD row
    clicks): on starts the tracker when a target is tracked, off clears it.
    Hosts without an overlay manager (tests/stubs) still write the setting."""
    calls = {"start": 0, "clear": 0}

    class _Tracker:
        class _S:
            track_kind = "orb"
            track_id = 7
        s = _S()
        model = object()

        def _start(self):
            calls["start"] += 1

        def clear(self):
            calls["clear"] += 1

    host.overlay_mgr = type("Mgr", (), {"tracker": _Tracker()})()

    # on, with an active target -> starts the needle immediately
    host._set_show_compass(True)
    assert calls == {"start": 1, "clear": 0}
    assert host.s.show_compass is True
    assert ("show_compass", True) in host.writes

    # off -> clears the needle right away, not on the next selection
    host._set_show_compass(False)
    assert calls == {"start": 1, "clear": 1}
    assert host.s.show_compass is False

    # stub hosts without overlay_mgr still persist the setting
    del host.overlay_mgr
    host._set_show_compass(True)
    assert host.s.show_compass is True
    assert calls == {"start": 1, "clear": 1}   # no needle apply attempted


def test_loot_floor_map_accepts_all(host):
    from farever_companion.ui.overlays import loot_rows

    class _Drop:
        item_id = "?"          # unresolvable -> Common rank (0), no catalog
        count = 1
        x = y = z = 0.0

        def dist2d(self, _x, _y):
            return 0.0

    def _n(floor):
        specs = loot_rows.build_loot_specs(
            [_Drop()], (0.0, 0.0, 0.0), floor,
            is_tracked=lambda *a: False, track=lambda *a: None)
        return len(specs)

    assert _n("Off") == 1          # no floor: the Common row survives
    assert _n("All") == 1          # explicit show-everything
    assert _n("Uncommon+") == 0    # Common (rank 0) is below the floor


def test_refresh_resyncs_toggles_and_links(host):
    toggles = _by_attr(host)
    host._matrix_rows["chests"]["link"].setChecked(True)
    # external changes (e.g. the old Entity/Map pages) while away:
    host.s.show_enemies = True
    host.s.minimap_enemies = False
    host.s.layer_links = ["gatherables"]
    host._refresh_settings_page()

    assert toggles["show_enemies"].isChecked() is True
    assert toggles["minimap_enemies"].isChecked() is False
    # the row LINK buttons follow the stored layer_links
    assert host._matrix_rows["gatherables"]["link"].isChecked() is True
    assert host._matrix_rows["chests"]["link"].isChecked() is False


def test_scan_folder_points_check_at_custom_game_dir(host, tmp_path, monkeypatch):
    """The Scan Folder button lets the user point the DLL check at a custom
    install path: the pick is persisted to farever_game_dir and the
    diagnostics refresh against it. The Game Folder line is gone from the
    diagnostics card (too noisy)."""
    assert host._dps_diag_scan_btn is not None
    assert not hasattr(host, "_dps_diag_folder_lbl")
    target = os.path.normpath(str(tmp_path))
    monkeypatch.setattr(QtWidgets.QFileDialog, "getExistingDirectory",
                        staticmethod(lambda *a, **k: str(tmp_path)))

    host._scan_farever_game_dir()

    assert host.s.farever_game_dir == target
    assert ("farever_game_dir", target) in host.writes


def _diagnostics_button_row(card):
    """Return the set of button texts in the diagnostics card's single
    horizontal action row (the only QHBoxLayout directly in its layout)."""
    lay = card.layout()
    for i in range(lay.count()):
        item = lay.itemAt(i)
        if isinstance(item, QtWidgets.QHBoxLayout):
            return {item.itemAt(j).widget().text() for j in range(item.count())}
    return set()


def test_dps_diag_buttons_share_one_row_and_test_hit_is_dev_only(host, monkeypatch):
    """Live Diagnostics action buttons sit in one horizontal row; the
    Test Hit button only appears in dev (source/run.bat) mode, never in the
    frozen exe."""
    # Dev mode (default under pytest): Scan / Copy / Test Hit, one shared row.
    card = host._build_dps_diagnostics_card()
    btns = {b.text(): b for b in card.findChildren(QtWidgets.QPushButton)}
    assert "🔍 Scan Folder" in btns and "📋 Copy Diagnostics" in btns
    assert "🧪 Test Hit" in btns
    assert _diagnostics_button_row(card) == {"🔍 Scan Folder", "📋 Copy Diagnostics", "🧪 Test Hit"}

    # Frozen exe: all diagnostics actions are hidden. The card builder now
    # lives in the settings package's dps_engine module, so the global it
    # reads is patched there.
    monkeypatch.setattr(
        "farever_companion.ui.pages.settings.dps_engine.is_frozen",
        lambda: True)
    card2 = host._build_dps_diagnostics_card()
    btns2 = {b.text(): b for b in card2.findChildren(QtWidgets.QPushButton)}
    assert "🔍 Scan Folder" not in btns2
    assert "📋 Copy Diagnostics" not in btns2
    assert "🧪 Test Hit" not in btns2
    assert _diagnostics_button_row(card2) == set()


def test_scan_folder_cancel_keeps_existing_setting(host, monkeypatch):
    """Cancelling the picker leaves the saved game folder untouched."""
    monkeypatch.setattr(QtWidgets.QFileDialog, "getExistingDirectory",
                        staticmethod(lambda *a, **k: ""))
    before = host.s.farever_game_dir
    n_writes = len(host.writes)

    host._scan_farever_game_dir()

    assert host.s.farever_game_dir == before
    assert len(host.writes) == n_writes   # no new write on cancel


def test_dps_conflict_notice_is_single_header_line(host, tmp_path):
    """The 3rd-party DLL notice lives in ONE compact line in the DPS header —
    the Option 1 card and Live Diagnostics no longer show their own copies."""
    assert host._dps_conflict_line is not None
    # the old duplicate widgets are gone entirely
    assert getattr(host, "_dps_proxy_conflict_lbl", None) is None
    assert getattr(host, "_dps_diag_conflict_banner", None) is None

    # clean folder -> no notice (isHidden() mirrors the explicit setVisible)
    host._cached_farever_game_dir = str(tmp_path)
    host.s.farever_game_dir = str(tmp_path)
    host._refresh_dps_diagnostics()
    _drain_diag_worker(host)
    assert host._dps_conflict_line.isHidden() is True

    # foreign dinput8.dll + conflicting proxy preference -> one compact line
    (tmp_path / "dinput8.dll").write_bytes(b"MZ" + b"\x00" * 100)
    host.s.dps_proxy_dll = "dinput8.dll"
    host._refresh_dps_diagnostics()
    _drain_diag_worker(host)
    assert host._dps_conflict_line.isHidden() is False
    assert "dinput8.dll" in host._dps_conflict_line.text()
    # version.dll stopped shipping (2026-09-24): userenv.dll is the alt the
    # card can still suggest for an occupied dinput8.
    assert "switch the Proxy DLL to userenv.dll" in host._dps_conflict_line.text()


# ======================================================================
# test_settings_deeplinks
# ======================================================================

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtWidgets  # noqa: E402

from farever_companion.config import Settings  # noqa: E402
from farever_companion.ui.control_panel import ControlPanel  # noqa: E402
from tests.qt_helpers import destroy, destroy_panel  # noqa: E402


@pytest.fixture(scope="module")
def panel(tmp_path_factory):
    """One real ControlPanel for the whole module (built once, like the
    page-eviction tests). Settings data is isolated to a temp dir by the
    module-scoped `_moddata` fixture above."""
    cp = ControlPanel(Settings())
    yield cp
    destroy_panel(cp)


def _assert_on_dps_tab(cp) -> None:
    """The Settings page is showing with the DPS sub-tab active."""
    assert cp._settings_tabs.currentText() == "DPS"
    assert cp._settings_stack.currentIndex() == 2   # Layers, HUD's, DPS, ...


def _external_writers(cp, view: str, mode: str) -> None:
    """Simulate another surface (overlay chips / engine picker) writing
    settings while the DPS tab is not on screen."""
    cp.s.dps_view = view
    cp.s.dps_mode = mode


@pytest.mark.parametrize("target", ["settings:dps", "settings:combat",
                                    "settings:meter", "settings:damage"])
def test_deep_link_lands_on_dps_tab_and_resyncs(panel, target):
    # Leave whatever tab the previous test ended on.
    panel._select_nav("overlays")

    # First open: the Settings page builds fresh from these external writes,
    # and the deep link still routes to the DPS sub-tab.
    _external_writers(panel, view="healing", mode="memory")
    panel._select_nav(target)
    _assert_on_dps_tab(panel)
    assert panel._dps_view_seg.currentText().lower() == "healing"
    # The capture engine is picked by the option cards now (the old segmented
    # control is gone); the resync must surface the external mode write.
    assert panel.s.dps_mode == "memory"
    assert panel._dps_mode_cards["memory"] is not None

    # Leave, change settings behind the page's back, then deep-link again.
    # The page is still cached (no rebuild), so the resync is what brings the
    # widgets up to date.
    panel._select_nav("overlays")
    _external_writers(panel, view="both", mode="injector")
    panel._select_nav(target)
    _assert_on_dps_tab(panel)
    assert panel._dps_view_seg.currentText().lower() == "both"
    assert panel.s.dps_mode == "injector"
    assert panel._dps_mode_cards["injector"] is not None


# ======================================================================
# test_settings_persistence
# ======================================================================

import json
import os
import time
from pathlib import Path

from farever_companion.runtime.persist import atomic_write_json


def _mtime(p: Path) -> float:
    return p.stat().st_mtime


def test_unchanged_save_does_not_rewrite_file(monkeypatch, tmp_path):
    monkeypatch.setenv("FAREVER_MODDATA_DIR", str(tmp_path))
    atomic_write_json(tmp_path / "settings.json",
                      {"ui_scale": 1.0, "opacity": 0.9}, compact_lists=True)
    from farever_companion.config import Settings
    s = Settings.load()
    before = _mtime(tmp_path / "settings.json")
    time.sleep(0.02)
    s.save()                                # nothing changed
    assert _mtime(tmp_path / "settings.json") == before


def test_changed_save_rewrites_file(monkeypatch, tmp_path):
    monkeypatch.setenv("FAREVER_MODDATA_DIR", str(tmp_path))
    atomic_write_json(tmp_path / "settings.json",
                      {"ui_scale": 1.0}, compact_lists=True)
    from farever_companion.config import Settings
    s = Settings.load()
    before = _mtime(tmp_path / "settings.json")
    time.sleep(0.02)
    s.opacity = 0.5
    s.save()
    assert _mtime(tmp_path / "settings.json") != before
    data = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert data.get("opacity") == 0.5


def test_in_place_dict_mutation_is_detected(monkeypatch, tmp_path):
    # Overlay geometry is stored as settings.geometry[key] = value (in-place),
    # then save() is called — the fingerprint must catch it (an attribute-set
    # dirty flag would miss this and silently drop overlay positions).
    monkeypatch.setenv("FAREVER_MODDATA_DIR", str(tmp_path))
    atomic_write_json(tmp_path / "settings.json", {}, compact_lists=True)
    from farever_companion.config import Settings
    s = Settings.load()
    if not s.geometry:
        s.geometry = {}
    s.geometry["overlay"] = "10,20"
    before = _mtime(tmp_path / "settings.json")
    time.sleep(0.02)
    s.save()
    assert _mtime(tmp_path / "settings.json") != before
    data = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert data.get("geometry", {}).get("overlay") == "10,20"


def test_fresh_load_unchanged_save_creates_nothing(monkeypatch, tmp_path):
    # No settings.json on disk and no changes -> save() must not create one.
    monkeypatch.setenv("FAREVER_MODDATA_DIR", str(tmp_path))
    from farever_companion.config import Settings
    s = Settings.load()
    s.save()
    assert not (tmp_path / "settings.json").exists()
    s.ui_scale = 1.5                        # real change persists
    s.save()
    assert (tmp_path / "settings.json").exists()


def test_dmg_type_pointers_roundtrip_and_empty_omitted(monkeypatch, tmp_path):
    """The cached DamageDisplay/DamageResult class pointers survive a reload
    (so an app restart skips the type scan) and are omitted from settings.json
    while unset (clean file, no empty-string noise)."""
    monkeypatch.setenv("FAREVER_MODDATA_DIR", str(tmp_path))
    from farever_companion.config import Settings
    s = Settings.load()
    s.dmg_display_type = "0x7ffc1234"
    s.dmg_result_type = "0x7ffc5678"
    s.save()
    data = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert data.get("dmg_display_type") == "0x7ffc1234"
    assert data.get("dmg_result_type") == "0x7ffc5678"

    s2 = Settings.load()
    assert s2.dmg_display_type == "0x7ffc1234"
    assert s2.dmg_result_type == "0x7ffc5678"

    # Clearing them removes the keys from disk on the next save.
    s2.dmg_display_type = ""
    s2.dmg_result_type = ""
    s2.save()
    data = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert "dmg_display_type" not in data
    assert "dmg_result_type" not in data


def test_collected_id_lookup_is_case_insensitive(monkeypatch, tmp_path):
    """HUD/live IDs can differ in casing from the canonical profile ID."""
    from farever_companion.config import Settings
    from farever_companion.runtime.persist import atomic_write_json
    monkeypatch.setenv("FAREVER_MODDATA_DIR", str(tmp_path))
    atomic_write_json(tmp_path / "progress_Mage.json", {
        "poi_done": ["RedOrb_World_37", "Z1_World_Greenlands_WorldChest_10"],
    }, compact_lists=True)
    s = Settings.load()
    assert s.is_done("redorb_world_37", "Mage")
    assert s.is_done("z1_world_greenlands_worldchest_10", "Mage")
    assert not s.is_done("RedOrb_World_38", "Mage")


def test_poi_done_authoritative_reads_disk_fresh(monkeypatch, tmp_path):
    """The sync writers base on the DISK file, not on possibly-stale/empty
    memory: a second instance (or fresh Settings) that never loaded the
    profile must not wipe a populated file. Reading authoritative also
    refreshes the whole cached dict so a save keeps orbs/hidden."""
    monkeypatch.setenv("FAREVER_MODDATA_DIR", str(tmp_path))
    atomic_write_json(tmp_path / "progress_Mage.json", {
        "poi_done": [f"poi_{i}" for i in range(448)],
        "dungeon_orb_done": ["orb_1"],
        "entity_hidden_units": ["u_1"],
    }, compact_lists=True)
    from farever_companion.config import Settings
    s = Settings.load()
    # simulate a second instance whose memory never loaded the profile
    s._profile_progress["Mage"] = {"poi_done": []}
    got = s.get_poi_done_authoritative("Mage")
    assert len(got) == 448
    # memory refreshed wholesale -> a save right after keeps the other lists
    assert s._profile_progress["Mage"].get("dungeon_orb_done") == ["orb_1"]
    assert s._profile_progress["Mage"].get("entity_hidden_units") == ["u_1"]


def test_sync_done_list_safe_refuses_empty_base_over_populated_file(
        monkeypatch, tmp_path):
    """The wipe signature: in-memory base EMPTY while the disk file holds
    done data. sync_done_list_safe returns False there (the sync refuses),
    and True for normal writes, missing files and empty files."""
    monkeypatch.setenv("FAREVER_MODDATA_DIR", str(tmp_path))
    atomic_write_json(tmp_path / "progress_Mage.json",
                      {"poi_done": [f"poi_{i}" for i in range(448)]},
                      compact_lists=True)
    from farever_companion.config import Settings
    s = Settings.load()
    assert s.sync_done_list_safe("Mage", []) is False   # the wipe signature
    assert s.sync_done_list_safe("Mage", ["poi_1"]) is True   # normal pass
    assert s.sync_done_list_safe("Nobody", []) is True  # missing file: ok
    atomic_write_json(tmp_path / "progress_Empty.json", {}, compact_lists=True)
    assert s.sync_done_list_safe("Empty", []) is True   # empty file: ok


def _write_prof(tmp_path, name="Mage", pois=448, orbs=0, hidden=0):
    atomic_write_json(tmp_path / f"progress_{name}.json", {
        "poi_done": [f"poi_{i}" for i in range(pois)],
        **({"dungeon_orb_done": [f"orb_{i}" for i in range(orbs)]} if orbs else {}),
        **({"entity_hidden_units": [f"u_{i}" for i in range(hidden)]} if hidden else {}),
    }, compact_lists=True)


def test_save_profile_data_seeds_disk_when_memory_empty(monkeypatch, tmp_path):
    """A writer with an EMPTY in-memory reference (failed load / fresh second
    instance) must never clobber a populated file: save_profile_data seeds the
    base from the current file before writing."""
    monkeypatch.setenv("FAREVER_MODDATA_DIR", str(tmp_path))
    _write_prof(tmp_path, pois=448, hidden=220)
    from farever_companion.config import Settings
    s = Settings.load()
    s._profile_progress["Mage"] = {}          # as if the load failed
    s.save_profile_data("Mage")               # would previously write {}
    d = json.loads((tmp_path / "progress_Mage.json").read_text(encoding="utf-8"))
    assert len(d["poi_done"]) == 448          # not wiped
    assert len(d["entity_hidden_units"]) == 220


def test_toggle_done_is_disk_authoritative(monkeypatch, tmp_path):
    """The stale-cache two-instance case: instance A cached 4 POIs, instance B
    adds poi_e to disk. A then toggles poi_b off - the toggle must apply to the
    CURRENT disk list so B's poi_e survives instead of being clobbered."""
    monkeypatch.setenv("FAREVER_MODDATA_DIR", str(tmp_path))
    _write_prof(tmp_path, pois=4)              # poi_0..poi_3
    from farever_companion.config import Settings
    a = Settings.load()
    assert a.get_poi_done("Mage") == ["poi_0", "poi_1", "poi_2", "poi_3"]
    # instance B adds poi_e on disk after A cached its copy
    atomic_write_json(tmp_path / "progress_Mage.json", {
        "poi_done": [f"poi_{i}" for i in range(4)] + ["poi_e"]}, compact_lists=True)

    done = a.toggle_done("poi_1", "Mage")    # unmark poi_1 in A's UI
    assert done is False
    d = json.loads((tmp_path / "progress_Mage.json").read_text(encoding="utf-8"))
    assert "poi_e" in d["poi_done"]           # B's addition survived
    assert "poi_1" not in d["poi_done"]       # A's toggle applied
    assert len(d["poi_done"]) == 4


def test_hidden_and_best_saves_are_disk_authoritative(monkeypatch, tmp_path):
    """toggle_unit_hidden / save_dps_best must not drag a stale cache over
    newer disk state (hidden list and other keys survive)."""
    monkeypatch.setenv("FAREVER_MODDATA_DIR", str(tmp_path))
    _write_prof(tmp_path, pois=10, hidden=3)   # u_0..u_2
    from farever_companion.config import Settings
    a = Settings.load()
    assert len(a.get_entity_hidden_units("Mage")) == 3
    # instance B hides u_new and collects poi_9 on disk afterwards
    atomic_write_json(tmp_path / "progress_Mage.json", {
        "poi_done": [f"poi_{i}" for i in range(10)],
        "entity_hidden_units": ["u_0", "u_1", "u_2", "u_new"]}, compact_lists=True)

    a.toggle_unit_hidden("u_1", False, "Mage")    # A un-hides u_1
    d = json.loads((tmp_path / "progress_Mage.json").read_text(encoding="utf-8"))
    assert "u_new" in d["entity_hidden_units"]     # B's hide survived
    assert "u_1" not in d["entity_hidden_units"]   # A's toggle applied

    a.save_dps_best("Mage", {"best": 12345})
    d = json.loads((tmp_path / "progress_Mage.json").read_text(encoding="utf-8"))
    assert d.get("dps_best") == {"best": 12345}
    assert "u_new" in d["entity_hidden_units"]     # still not clobbered
    assert len(d["poi_done"]) == 10


def test_the_dummy_baseline_is_saved_per_profile(monkeypatch, tmp_path):
    """A dummy baseline is a statement about THIS character's gear and rotation,
    so it lives in the per-character file — and, like dps_best, writing that one
    key must not drag the file's other keys back to an older state. The shared
    settings.json must NOT carry it (see the _serialized_settings pop)."""
    monkeypatch.setenv("FAREVER_MODDATA_DIR", str(tmp_path))
    _write_prof(tmp_path, pois=10, hidden=3)
    from farever_companion.config import Settings
    a = Settings.load()
    snap = {"ts": 1.0, "duration": 30.0, "dps": 1234.0,
            "skills": {"Sword_Base_Attack": {"name": "Sword", "dps": 900.0}}}

    a.save_dps_baseline("Mage", snap)
    d = json.loads((tmp_path / "progress_Mage.json").read_text(encoding="utf-8"))
    assert d.get("dps_baseline") == snap
    assert len(d["poi_done"]) == 10                  # other keys intact
    assert "dps_baseline" not in a._serialized_settings()   # never in settings.json

    # it reads back for that character, and another character has no reference
    b = Settings.load()
    assert b.get_dps_baseline("Mage") == snap
    assert b.get_dps_baseline("Rogue") != snap


def test_the_dummy_personal_best_is_saved_per_profile(monkeypatch, tmp_path):
    """The best-ever test is a second per-character record with the opposite
    lifetime to the reference: it is never replaced by a worse run, so it needs
    its own key rather than sharing the baseline's. Same guarantees — a
    single-key write that does not drag the file's other keys back, and never
    in the shared settings.json."""
    monkeypatch.setenv("FAREVER_MODDATA_DIR", str(tmp_path))
    _write_prof(tmp_path, pois=10, hidden=3)
    from farever_companion.config import Settings
    a = Settings.load()
    best = {"ts": 2.0, "duration": 30.0, "dps": 4321.0,
            "skills": {"Axe_Base_Attack": {"name": "Axe", "dps": 4000.0}}}

    a.save_dps_best_dummy("Mage", best)
    d = json.loads((tmp_path / "progress_Mage.json").read_text(encoding="utf-8"))
    assert d.get("dps_best_dummy") == best
    assert len(d["poi_done"]) == 10                 # other keys intact
    assert "dps_best_dummy" not in a._serialized_settings()
    assert "dps_baseline" not in d                  # the two records are separate

    b = Settings.load()
    assert b.get_dps_best_dummy("Mage") == best
    assert b.get_dps_best_dummy("Rogue") != best


def test_save_profile_progress_preserves_other_keys_from_disk(monkeypatch, tmp_path):
    """save_profile_progress writes onto the CURRENT file (not the stale
    cache), so orbs/hidden added by another instance survive a poi save."""
    monkeypatch.setenv("FAREVER_MODDATA_DIR", str(tmp_path))
    _write_prof(tmp_path, pois=10, orbs=2)     # orb_0..orb_1
    from farever_companion.config import Settings
    a = Settings.load()
    assert len(a.get_poi_done("Mage")) == 10
    # instance B collects orb_9 after A cached its copy
    atomic_write_json(tmp_path / "progress_Mage.json", {
        "poi_done": [f"poi_{i}" for i in range(10)],
        "dungeon_orb_done": ["orb_0", "orb_1", "orb_9"]}, compact_lists=True)

    a.save_profile_progress("Mage", [f"poi_{i}" for i in range(11)])  # A adds poi_10
    d = json.loads((tmp_path / "progress_Mage.json").read_text(encoding="utf-8"))
    assert "orb_9" in d["dungeon_orb_done"]    # B's orb survived
    assert "poi_10" in d["poi_done"]


# --- settings.json may only be rewritten by an instance that owns it -------
# Regression for the 2026-09-17 reset: a test built a real
# `ControlPanel(Settings())` (fixture-less, so no moddata redirection) and the
# Settings page's own `_set()` saved it. The user's live settings.json was left
# differing from fresh defaults in exactly one key (`farever_game_dir`) - every
# toggle they had set was gone, and it happened again on every suite run.

def _stored(tmp_path: Path) -> dict:
    return json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))


_USER_SETTINGS = {
    "ui_scale": 1.35,
    "opacity": 0.75,
    "geometry": {"entity": "40,40,375,471", "minimap": "2120,40"},
    "sidebar_elements": ["launch_button", "log", "rift_box"],
    "dps_solo_only": True,
    "loot_filter": "Rare+",
}


def test_a_defaults_instance_cannot_erase_stored_settings(monkeypatch, tmp_path):
    """A never-loaded `Settings()` must not replace the user's file with
    defaults. Values the bare instance actually set do get written; every
    field it left at its default keeps the stored value."""
    monkeypatch.setenv("FAREVER_MODDATA_DIR", str(tmp_path))
    atomic_write_json(tmp_path / "settings.json", dict(_USER_SETTINGS),
                      compact_lists=True)
    from farever_companion.config import Settings

    s = Settings()                  # bare - exactly what _FakeHost builds
    s.farever_game_dir = r"D:\Steam\steamapps\common\Farever"
    s.save()

    data = _stored(tmp_path)
    assert data.get("ui_scale") == 1.35
    assert data.get("opacity") == 0.75
    assert data.get("geometry") == {"entity": "40,40,375,471",
                                    "minimap": "2120,40"}
    assert data.get("sidebar_elements") == ["launch_button", "log", "rift_box"]
    assert data.get("dps_solo_only") is True
    assert data.get("loot_filter") == "Rare+"
    # the change the instance did make is persisted
    assert data.get("farever_game_dir") == r"D:\Steam\steamapps\common\Farever"


def test_a_defaults_instance_still_creates_a_missing_file(monkeypatch, tmp_path):
    """Nothing on disk means nothing to preserve: a fresh install (and every
    test that starts from an empty folder) must still be able to save."""
    monkeypatch.setenv("FAREVER_MODDATA_DIR", str(tmp_path))
    from farever_companion.config import Settings

    s = Settings()
    s.dps_solo_only = True
    s.save()

    assert _stored(tmp_path).get("dps_solo_only") is True


def test_a_save_with_no_geometry_keeps_stored_overlay_positions(monkeypatch, tmp_path):
    """Overlay positions are populated only once an overlay restores or is
    dragged, and the first save of a session fires from the startup tracking
    clear - so a session that has placed nothing yet must not delete the
    stored positions (the "overlays jump back to the default corner" bug)."""
    monkeypatch.setenv("FAREVER_MODDATA_DIR", str(tmp_path))
    atomic_write_json(tmp_path / "settings.json", dict(_USER_SETTINGS),
                      compact_lists=True)
    from farever_companion.config import Settings

    s = Settings.load()
    assert s.geometry, "load() must read the stored geometry"
    s.geometry = {}                          # not placed this session yet
    s.opacity = 0.6                          # a real change, so a save happens
    s.save()

    assert _stored(tmp_path).get("geometry") == _USER_SETTINGS["geometry"]
    assert _stored(tmp_path).get("opacity") == 0.6

def test_the_speedrun_card_lives_on_the_dps_tab(panel):
    """The sidebar Speedrun page is gone; its controls survive as a card on
    Settings ▸ DPS (bottom row, beside the Combat DPS Meter card), in the same
    OverlayCardFrame pattern as the other HUD cards — and the sidebar page must
    stay gone (no second surface to drift from)."""
    from farever_companion.ui.nav_registry import NAV
    from farever_companion.ui.components import OverlayCard

    assert all(k != "speedrun" for k, _, _ in NAV)
    assert not hasattr(panel, "_page_speedrun")
    panel._select_nav("overlays")
    overlay_cards = panel.overlay_mgr.cards.get("speedrun")
    assert overlay_cards                         # open/close stays there
    overlay_card = overlay_cards[0]
    overlay_text = " | ".join(lbl.text() for lbl in
                              overlay_card.findChildren(QtWidgets.QLabel))
    assert "Run Timer" in overlay_text
    assert "Speedrun" not in overlay_text

    panel._select_nav("settings:DPS")
    assert panel._speedrun_card is not None
    settings_widgets = (panel._speedrun_card.findChildren(QtWidgets.QLabel)
                        + panel._speedrun_card.findChildren(QtWidgets.QPushButton))
    settings_text = " | ".join(widget.text() for widget in settings_widgets)
    assert "run timer" in settings_text.lower()
    assert "DUNGEON TIMER" in settings_text
    assert "speedrun" not in settings_text.lower()
    grid_texts = " | ".join(b.text() for b, in
                            [(t[1],) for t in panel._speedrun_toggles])
    for want in ("Borderless", "Transparent"):
        assert want in grid_texts, f"missing chip: {want}"
    assert "Re-arm" not in grid_texts  # re-arm is always on — toggle removed
    # No stray OverlayCard duplicate and no scale slider (the setting is gone).
    assert panel._speedrun_card.findChildren(OverlayCard) == []
    assert panel._speedrun_card.findChildren(QtWidgets.QSlider) == []


def test_the_run_timer_cards_chips_hug_their_content(host):
    """The Run Timer card is the shortest card in the DPS tab's bottom
    `StretchFlow` row (it shares that row with the Top DPS card, among
    others), and the row hands EVERY card in it the same width and the same
    height - its neighbour's.

    The chip grid used to absorb all of that slack on both axes, because it
    was the only child free to grow: the Borderless/Transparent cell rendered
    ~480x80 for a 3+32+3 = 38 px row of chips, so the two labels floated in a
    sea of air and each chip was blown out to ~230 px for a 10-character
    label. Pinned here: the cell and the chips stay at their own size hints
    and the slack collects below them instead of inside the cell.
    """
    win = QtWidgets.QMainWindow()
    inner = QtWidgets.QWidget()
    lay = QtWidgets.QVBoxLayout(inner)
    lay.addWidget(host._page)
    win.setCentralWidget(inner)
    # Wide enough that ALL FIVE cards of that row share ONE row: below ~2600 px
    # the Run Timer card takes a row of its own (the Test Dummy card joined the
    # row after this test was written), where its own hint already wins and the
    # slack question does not arise.
    win.resize(2800, 900)
    win.show()
    host._settings_tabs._pick("DPS")
    _dps_tab_content_widths(host)   # drains the resize -> relayout chain
    try:
        card = host._speedrun_card
        assert card.isVisible(), "the Run Timer card is not on screen"
        assert abs(card.y() - host._top_dps_card.y()) <= 4, (
            "precondition: the Run Timer card shares its row with Top DPS; "
            "widen the window if the row packing changed")
        assert card.width() > card.sizeHint().width() * 1.5, (
            "precondition: the row stretched this card well past its own "
            "hint, which is the situation the chip cell used to absorb")
        cells = [f for f in card.findChildren(QtWidgets.QFrame)
                 if f.objectName() == "Cell"]
        assert len(cells) == 1, "expected exactly one chip cell on the card"
        cell = cells[0]

        # The cell is its content, not the row's leftover space: a row of the
        # two 32 px chips (plus the cell's 3 px inset) is ~38 px tall, and this
        # is the assertion that fails if the grid starts growing again.
        assert cell.height() <= cell.sizeHint().height() + 2, (
            f"chip cell stretched to {cell.height()}px for a {cell.sizeHint().height()}px"
            " row of chips")
        assert cell.width() <= cell.sizeHint().width() + 2, (
            f"chip cell stretched to {cell.width()}px for {cell.sizeHint().width()}px"
            " of chips")

        # ...while the CARD keeps the row's shared width (the point is not to
        # shrink the card, it is to stop the chips filling it).
        assert card.width() > cell.width() * 1.5, (
            "the card should still be much wider than its two chips")

        # The slack goes BELOW the cell: the header sits at the top of the card
        # and the cell right under it, instead of the header row ballooning to
        # push the cell into the middle of the card.
        cell_top = cell.mapTo(card, QtCore.QPoint(0, 0)).y()
        assert cell_top < card.height() // 2, (
            f"chip cell sits at y={cell_top} in a {card.height()}px card - the"
            " row's slack is being spent above it")
        assert cell_top + cell.height() < card.height(), (
            "the chip cell is clipped by the card")

        # And each chip is its own size, not an equal share of the card.
        assert len(host._speedrun_toggles) == 2
        for key, btn in host._speedrun_toggles:
            assert btn.isVisible(), f"the {key} chip is not on screen"
            assert btn.width() <= btn.sizeHint().width() + 2, (
                f"the {key} chip stretched to {btn.width()}px for a"
                f" {btn.sizeHint().width()}px label")
    finally:
        destroy(win)


def test_the_speedrun_upload_control_is_gone(panel):
    """Speedrun has no upload path (local PBs + kill log only), so the
    Settings UPLOAD segment and overlay upload methods must not come back."""
    panel._select_nav("settings:DPS")
    assert not hasattr(panel, "_speedrun_upload_seg")
    assert not hasattr(panel, "_set_speedrun_upload_mode")
    from farever_companion.ui.overlays.speedrun_overlay import SpeedrunOverlay
    assert not hasattr(SpeedrunOverlay, "_maybe_upload")
    assert not hasattr(SpeedrunOverlay, "_do_upload")
    assert not hasattr(SpeedrunOverlay, "_record_completion")


def test_the_dead_speedrun_page_settings_are_gone(panel):
    """With the page deleted, the settings only it wrote die with it: scale,
    build override and co-runners must not come back without the surface
    that justified them."""
    from farever_companion.config import Settings
    for dead in ("speedrun_scale", "speedrun_build_override",
                 "speedrun_build_override_on", "speedrun_corunners"):
        assert not hasattr(Settings(), dead)
    src = open("farever_companion/ui/overlays/speedrun_overlay.py",
               encoding="utf-8").read()
    assert "co_runners = list" not in src and "build_code" not in src

def test_dps_background_tick_reports_timer_stall(qapp):
    """The drain worker's canary must surface multi-second loop gaps (thread
    starvation / a stuck pass) instead of silently backing up the event ring.
    The drain itself runs on DpsDrainWorker (a plain thread Windows cannot
    throttle); the canary remains as the detector."""
    from farever_companion.ui.dps_background_tick import DpsDrainWorker
    import time as _time

    lines: list[str] = []
    w = DpsDrainWorker(lambda: None, log_emit=lines.append)
    w._last = _time.monotonic() - 8.0      # simulate an 8 s loop gap
    w._check_gap()
    assert any("stalled" in ln for ln in lines)
    assert lines[-1].startswith("DPS drain stalled 8s")

    lines.clear()
    w._last = _time.monotonic()            # healthy gap
    w._check_gap()
    assert lines == []


# --- shared speedrun history (moddata/speedrun_history.json) -----------------

def _reset_speedrun_migration():
    import farever_companion.config as cfg
    cfg._speedrun_migrated_dirs.clear()


def test_shared_speedrun_history_roundtrip(monkeypatch, tmp_path):
    """One cross-character store: write/read is profile-agnostic, the file is
    a bare JSON list, and get/save_speedrun_history take no profile arg."""
    import json
    monkeypatch.setenv("FAREVER_MODDATA_DIR", str(tmp_path))
    _reset_speedrun_migration()
    from farever_companion.config import (
        Settings, load_speedrun_history, write_speedrun_history)

    assert load_speedrun_history() == []
    row = {"boss": "MunsterChuck", "full_ms": 1234, "mode": "hard",
           "at": 100.0, "char": "Kohan_Rogue_S8c4ba8"}
    write_speedrun_history([row])
    assert load_speedrun_history() == [row]

    s = Settings.load()
    assert s.get_speedrun_history() == [row]
    s.save_speedrun_history([dict(row, at=200.0)])
    got = load_speedrun_history()
    assert len(got) == 1 and got[0]["at"] == 200.0
    raw = json.loads((tmp_path / "speedrun_history.json").read_text(encoding="utf-8"))
    assert isinstance(raw, list) and raw[0]["boss"] == "MunsterChuck"
    # No per-profile key is written back.
    assert not any("speedrun_history" in json.loads(p.read_text(encoding="utf-8"))
                   for p in tmp_path.glob("progress_*.json"))


def test_speedrun_history_migrates_profiles_and_strips_keys(monkeypatch, tmp_path):
    """Legacy per-profile kill logs fold into the shared file (char stamped
    from the filename), other profile keys survive, and the old key is gone."""
    import json
    monkeypatch.setenv("FAREVER_MODDATA_DIR", str(tmp_path))
    _reset_speedrun_migration()
    from farever_companion.config import Settings, load_speedrun_history

    # full_ms must clear MIN_PLAUSIBLE_SPLIT_S (1.0 s): the sub-second purge
    # (_purge_nonsense_pbs) runs right after the migration in Settings.load and
    # deletes anything shorter, so a 10-30 ms fixture would test the purge, not
    # the fold. See core/speedrun.py:MIN_PLAUSIBLE_SPLIT_S.
    atomic_write_json(tmp_path / "progress_Kohan_Rogue_S8c4ba8.json",
                      {"speedrun_history": [
                          {"boss": "A", "full_ms": 12_000, "mode": "hard", "at": 1.0},
                          {"boss": "B", "full_ms": 20_000, "mode": "hard", "at": 2.0},
                      ], "poi_done": ["x"]}, compact_lists=True)
    atomic_write_json(tmp_path / "progress_Bee_Mage_S8c4ba8.json",
                      {"speedrun_history": [
                          {"boss": "C", "full_ms": 30_000, "mode": "normal", "at": 3.0},
                      ]}, compact_lists=True)

    s = Settings.load()
    rows = load_speedrun_history()
    assert [r["boss"] for r in rows] == ["A", "B", "C"]      # newest last
    by_boss = {r["boss"]: r for r in rows}
    assert by_boss["A"]["char"] == "Kohan_Rogue_S8c4ba8"
    assert by_boss["C"]["char"] == "Bee_Mage_S8c4ba8"
    assert s.get_speedrun_history() == rows

    kohan = json.loads(
        (tmp_path / "progress_Kohan_Rogue_S8c4ba8.json").read_text(encoding="utf-8"))
    bee = json.loads(
        (tmp_path / "progress_Bee_Mage_S8c4ba8.json").read_text(encoding="utf-8"))
    assert "speedrun_history" not in kohan and kohan.get("poi_done") == ["x"]
    assert "speedrun_history" not in bee


def test_speedrun_history_migration_is_idempotent_after_crash(monkeypatch, tmp_path):
    """A crash between the shared write and the profile strip re-runs a merge
    that dedupes on row identity — never a double-counted kill."""
    import json
    monkeypatch.setenv("FAREVER_MODDATA_DIR", str(tmp_path))
    _reset_speedrun_migration()
    from farever_companion.config import Settings, load_speedrun_history

    row = {"boss": "A", "full_ms": 12_000, "mode": "hard", "at": 1.0}
    atomic_write_json(tmp_path / "progress_Kohan_Rogue_S8c4ba8.json",
                      {"speedrun_history": [row]}, compact_lists=True)

    Settings.load()
    assert len(load_speedrun_history()) == 1

    # Simulate crash-before-strip: profile still carries the key; shared file
    # already has the stamped row from the first attempt.
    atomic_write_json(tmp_path / "progress_Kohan_Rogue_S8c4ba8.json",
                      {"speedrun_history": [row]}, compact_lists=True)
    _reset_speedrun_migration()
    Settings.load()
    rows = load_speedrun_history()
    assert len(rows) == 1
    assert rows[0]["char"] == "Kohan_Rogue_S8c4ba8"
    left = json.loads(
        (tmp_path / "progress_Kohan_Rogue_S8c4ba8.json").read_text(encoding="utf-8"))
    assert "speedrun_history" not in left


def test_speedrun_history_migration_clears_legacy_settings_field(monkeypatch, tmp_path):
    """The old settings.json `speedrun_history` field is folded in and the
    key is stripped from disk (one source of truth)."""
    import json
    monkeypatch.setenv("FAREVER_MODDATA_DIR", str(tmp_path))
    _reset_speedrun_migration()
    from farever_companion.config import Settings, load_speedrun_history

    row = {"boss": "Z", "full_ms": 11_000, "mode": "hard", "at": 5.0}
    atomic_write_json(tmp_path / "settings.json",
                      {"speedrun_history": [row], "ui_scale": 1.2},
                      compact_lists=True)

    s = Settings.load()
    assert load_speedrun_history() == [row]
    assert s.speedrun_history == []
    on_disk = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert "speedrun_history" not in on_disk
    assert on_disk.get("ui_scale") == 1.2


def test_pb_history_is_stack_only_not_sidebar_or_pages_tile(panel):
    """PB History lives in NAV (so _select_nav accepts it and the stack has a
    slot) but is filtered out of the sidebar and skipped by Settings → Pages —
    reached only via the DPS Analysis header link."""
    from farever_companion.config import Settings
    from farever_companion.ui.nav_registry import NAV, filtered_nav
    from farever_companion.ui.pages.settings import SettingsPageMixin

    keys = [k for k, _, _ in NAV]
    assert "pb" in keys
    assert "pb" not in [k for k, _, _ in filtered_nav(Settings())]
    assert "pb" in SettingsPageMixin._PAGES_ALWAYS_ON

    # Header link on DPS Analysis opens it; sidebar has no PB item.
    panel._select_nav("combat")
    panel._select_nav("pb")
    assert panel._pages_built.get("pb")
    assert "pb" not in panel._nav_items
    stack_w = panel.stack.widget(keys.index("pb"))
    assert panel._widget_alive(stack_w)

    # Back returns to DPS Analysis. The page is scroll-wrapped, so
    # consume_pane_back lives on the inner widget (mouse-back walks up to it).
    inner = next((w for w in stack_w.findChildren(QtWidgets.QWidget)
                  if getattr(w, "consume_pane_back", None)), None)
    assert inner is not None
    assert inner.consume_pane_back() is True
    assert panel._current_page() == "combat"
