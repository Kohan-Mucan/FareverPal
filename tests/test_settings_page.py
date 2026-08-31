"""Settings page layer matrix — linking behavior (headless Qt, offscreen).

Builds the real SettingsPageMixin page on a minimal fake host (Settings +
the two setters it calls) and verifies the HUD <-> MAP link feature: the
MASTER bar links every row with both switches and mirrors HUD -> Map, while
per-row chain buttons link just that row.
"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtGui, QtWidgets  # noqa: E402

from farever_companion.config import Settings  # noqa: E402
from farever_companion.ui.pages.settings import (  # noqa: E402
    SettingsPageMixin, _LOOT_OPTS,
)


@pytest.fixture(scope="module", autouse=True)
def _qapp():
    """One offscreen QApplication for the whole module (widgets need one)."""
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class _FakeHost(SettingsPageMixin):
    """Minimal stand-in for ControlPanel: settings + the setters the page
    calls, recording every write so tests can assert on them."""

    def __init__(self):
        self.s = Settings()
        self.writes: list[tuple[str, object]] = []
        self.overlays = {}

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


@pytest.fixture()
def host(tmp_path, monkeypatch):
    monkeypatch.setenv("FAREVER_MODDATA_DIR", str(tmp_path))
    h = _FakeHost()
    h._page = h._page_settings()   # keep the widget tree alive for the test
    return h


def _by_attr(host):
    return {a: t for a, t in host._settings_toggles}


def test_matrix_builds_all_toggles_and_links(host):
    toggles = _by_attr(host)
    # 6 common x 2 + orbs dungeon + 4 map-only + players/loot (hud+dungeon)
    assert len(toggles) == 22
    linkable = [r for r in host._matrix_rows.values() if r["linkable"]]
    assert len(linkable) == 7
    for r in linkable:
        assert r["link"] is not None
    assert host._master_link is not None


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


def test_master_link_links_every_row_and_mirrors_hud_to_map(host):
    toggles = _by_attr(host)
    host._master_link.setChecked(True)          # flips the master toggle
    assert host.s.layer_link_master is True
    assert len(host.s.layer_links) == 7         # all linkable rows linked

    toggles["show_enemies"].setChecked(False)   # HUD enemies -> off
    # mirrored to the minimap setting + its switch widget
    assert host.s.minimap_enemies is False
    assert toggles["minimap_enemies"].isChecked() is False
    assert ("minimap_enemies", False) in host.writes

    # reverse direction: Map -> HUD also mirrors
    toggles["minimap_chests"].setChecked(False)
    assert host.s.show_chests is False
    assert toggles["show_chests"].isChecked() is False


def test_master_off_unlinks_all(host):
    host._master_link.setChecked(True)
    host._master_link.setChecked(False)
    assert host.s.layer_link_master is False
    assert host.s.layer_links == []


def test_per_row_link_mirrors_only_that_row(host):
    toggles = _by_attr(host)
    row = host._matrix_rows["chests"]
    row["link"].setChecked(True)                # link only chests
    assert host.s.layer_links == ["chests"]
    assert host._master_link.isChecked() is False

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
    assert len(by_attr) == 12          # 10 HUD + 2 minimap
    by_attr["enemy_count"].setValue(9)
    assert ("enemy_count", 9) in host.writes
    by_attr["max_dist"].setValue(400)
    assert host.s.max_dist == 400
    by_attr["minimap_size"].setValue(500)
    assert host.s.minimap_size == 500
    by_attr["minimap_icon_size"].setValue(30)
    assert host.s.minimap_icon_size == 30
    assert ("minimap_icon_size", 30) in host.writes


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
        ["Layers", "HUD's", "Rift", "Pages"]
    assert host._settings_stack.count() == 4
    host._settings_tabs._pick("Rift")
    assert host._settings_stack.currentIndex() == 2
    host._settings_tabs._pick("Layers")
    assert host._settings_stack.currentIndex() == 0


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
        "entity_bare", "show_compass", "auto_select_next_collectible",
        "limit_by_zone", "entity_hide_collected", "PlayerNames",
    }
    by_attr["entity_bare"].setChecked(True)
    assert host.s.entity_bare is True
    assert ("entity_bare", True) in host.writes

    by_attr["show_compass"].setChecked(True)
    assert host.s.show_compass is True
    assert ("show_compass", True) in host.writes

    by_attr["auto_select_next_collectible"].setChecked(False)
    assert host.s.auto_select_next_collectible is False
    assert ("auto_select_next_collectible", False) in host.writes


def test_minimap_section_writes(host):
    by_attr = dict(host._minimap_toggles)
    assert set(by_attr) == {
        "minimap_bare", "minimap_rotate", "minimap_texture", "minimap_icons",
        "minimap_hide_collected", "minimap_limit_by_zone",
    }
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
    assert set(by_attr) == {"dungeon_bare", "rift_clone_track"}
    # defaults: bare off, boss auto-track on
    assert host.s.dungeon_bare is False
    assert host.s.rift_clone_track is True

    by_attr["dungeon_bare"].setChecked(True)          # default False
    assert host.s.dungeon_bare is True
    assert ("dungeon_bare", True) in host.writes
    by_attr["rift_clone_track"].setChecked(False)  # default True
    assert host.s.rift_clone_track is False
    assert ("rift_clone_track", False) in host.writes


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
    host._master_link.setChecked(True)
    # external changes (e.g. the old Entity/Map pages) while away:
    host.s.show_enemies = True
    host.s.minimap_enemies = False
    host.s.layer_link_master = False
    host.s.layer_links = ["gatherables"]
    host._refresh_settings_page()

    assert toggles["show_enemies"].isChecked() is True
    assert toggles["minimap_enemies"].isChecked() is False
    assert host._master_link.isChecked() is False
    assert host._matrix_rows["gatherables"]["link"].isChecked() is True
    assert host._matrix_rows["chests"]["link"].isChecked() is False
