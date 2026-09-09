"""Dungeon HUD per-section toggles — titlebar buttons + settings gates.

2026-09-18: the Dungeon HUD only had three ad-hoc titlebar buttons (orbs /
players / loot) and four sections (FOOD / RIFT schedule / BOSS / ENEMIES)
had no toggle at all — unlike the Entity HUD where every section is
toggleable. Now all seven sections have a titlebar button and a Settings
attr, and the settings Dungeon HUD card mirrors the same toggles.

These tests exercise the render-side gates directly (fake section boxes)
and the button set through a __new__-built overlay (no live timer).
"""
import pytest

pytest.importorskip("PySide6")

from PySide6 import QtWidgets

from farever_companion.ui.overlays.dungeon_overlay import (
    DungeonOverlay, _SECTION_TOGGLES, _SECTION_TOGGLES_BY_KEY,
)
from farever_companion.ui.overlays.dungeon_render import DungeonRenderMixin


# --- render-side fakes (same pattern as test_dungeon_players.py) ----------

class _Header:
    def __init__(self):
        self.text = ""

    def set_text(self, t):
        self.text = t

    def set_tag(self, t):
        pass


class _Box:
    def __init__(self):
        self.header = _Header()
        self.specs = []
        self.visible = False

    def fill(self, specs, _isz):
        self.specs = list(specs)

    def show(self):
        self.visible = True

    def hide(self):
        self.visible = False


class _S:
    """Settings surface the renderers read; every toggle defaults like
    a fresh Settings() (all sections visible except nothing)."""
    dungeon_show_food = True
    dungeon_show_rift = True
    dungeon_show_boss = True
    dungeon_show_enemies = True
    dungeon_show_players = True
    dungeon_show_loots = True
    dungeon_show_orbs = True
    show_rift_timer = "Off"      # keep the rift card out of the food test
    hud_accent = "#38bdf8"
    icon_size = 20


class _Model:
    is_in_dungeon = staticmethod(lambda: True)
    is_in_rift = staticmethod(lambda: False)
    player_addr = 0

    def foe_target_line(self, e):
        return ""


class _Unit:
    def __init__(self, uid):
        self.unit_id = uid
        self.hp = 100.0
        self.addr = 0x501

    def dist2d(self, x, y):
        return 5.0


class _Host(DungeonRenderMixin):
    """Food + enemies + boss + rift renderers over fake boxes."""

    def __init__(self, s=None):
        self.s = s or _S()
        self._scale = 1.0
        self.model = _Model()
        self.dg_food_box = _Box()
        self.dg_rift_box = _Box()
        self.dg_boss_box = _Box()
        self.dg_enemy_box = _Box()
        self.dg_loot_box = _Box()
        self.dg_player_box = _Box()
        self.dg_orb_box = _Box()
        self._dg_food = [_Food()]
        self._dg_enemies = [(_Unit("Rat_E"), 5.0, "boss")]
        self._dg_xyz = (0.0, 0.0, 0.0)
        self._dg_orbs = []
        self._dg_players = []

    def _dg_display_name(self, uid):  # pragma: no cover - trivial
        return uid

    def food_specs(self, isz):
        """Stub: the real one lives on DungeonOverlay (catalog lookup)."""
        from farever_companion.ui.overlays.entity_rows import RowSpec
        if not self._dg_food:
            return None
        return [RowSpec(None, None, "#FB923C", f.name, "#FDBA74",
                        value="   3m", key=("food", f.addr))
                for f in self._dg_food]

    def loot_specs(self, isz):
        return None          # no loot rows in this harness

    def _is_tracked(self, kind, key):
        return False


class _Food:
    name = "Feast"
    addr = 0x900

    def dist2d(self, x, y):
        return 3.0


# --- 1. every section is in the toggle table ------------------------------

def test_section_table_covers_all_seven_sections():
    assert [k for k, *_ in _SECTION_TOGGLES] == [
        "food", "rift", "boss", "enemies", "players", "loots", "orbs"]
    # each maps to a distinct Settings attr
    attrs = [spec[0] for spec in _SECTION_TOGGLES_BY_KEY.values()]
    assert len(set(attrs)) == len(attrs)


def test_new_section_settings_exist_on_config():
    """The four formerly untoggleable sections now have Settings bools."""
    from farever_companion.config import Settings
    s = Settings()
    assert s.dungeon_show_food is True
    assert s.dungeon_show_rift is True
    assert s.dungeon_show_boss is True
    assert s.dungeon_show_enemies is True


# --- 2. render-side gates (the actual hiding) -----------------------------

def test_food_toggle_hides_food_section():
    host = _Host()
    host._render_dg_sections(None)
    assert host.dg_food_box.visible is True
    host.s.dungeon_show_food = False
    host._render_dg_sections(None)
    assert host.dg_food_box.visible is False


def test_boss_toggle_hides_boss_section():
    host = _Host()
    host._render_dg_sections(None)
    assert host.dg_boss_box.visible is True
    host.s.dungeon_show_boss = False
    host._render_dg_sections(None)
    assert host.dg_boss_box.visible is False


def test_enemies_toggle_hides_enemy_rows_but_keeps_bosses():
    """Enemies off hides the mob list only — the boss card has its own
    toggle (a boss you are fighting must not vanish with the trash)."""
    host = _Host()
    host.s.dungeon_show_enemies = False
    host._render_dg_sections(None)
    assert host.dg_enemy_box.visible is False
    assert host.dg_boss_box.visible is True   # independent toggle


def test_rift_schedule_toggle_hides_rift_card():
    host = _Host()

    def _fake_rift_render(isz):
        # bypass the game-state reads: emulate the gate directly
        if not getattr(host.s, "dungeon_show_rift", True):
            host.dg_rift_box.hide()
            return
        host.dg_rift_box.show()

    _fake_rift_render(16)
    assert host.dg_rift_box.visible is True
    host.s.dungeon_show_rift = False
    _fake_rift_render(16)
    assert host.dg_rift_box.visible is False


def test_rift_render_gate_short_circuits_before_game_state():
    """The real renderer's gate runs before any rift-status read, so a
    hidden card stays hidden even when the model would answer."""
    host = _Host()
    host.s.show_rift_timer = "Always"
    host.s.dungeon_show_rift = False
    # No rift state on the fake model at all — the gate fires first.
    host._render_dg_rift(16)
    assert host.dg_rift_box.visible is False


# --- 3. the overlay's titlebar buttons ------------------------------------

class _SaveSpy:
    """Records save() calls instead of touching disk; writes and reads
    pass through to the wrapped settings object."""

    def __init__(self, inner):
        object.__setattr__(self, "_inner", inner)
        object.__setattr__(self, "saved", 0)

    def save(self):
        object.__setattr__(self, "saved", self.saved + 1)

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def __setattr__(self, name, value):
        setattr(self._inner, name, value)


def _overlay_host(s):
    """__new__-built DungeonOverlay with just the section-button surface."""
    ov = DungeonOverlay.__new__(DungeonOverlay)
    ov.s = _SaveSpy(s)
    ov._section_btns = {}
    ov._tick = lambda: None
    return ov


def test_set_show_section_writes_settings_and_retitls_button():
    s = _S()
    ov = _overlay_host(s)
    ov.set_show_section("food", False)
    assert s.dungeon_show_food is False
    assert ov.s.saved == 1                 # persisted
    ov.set_show_section("boss", False)
    assert s.dungeon_show_boss is False
    # unknown key is a no-op, not a crash
    ov.set_show_section("nope", False)


def test_backcompat_setters_route_through_set_show_section():
    s = _S()
    ov = _overlay_host(s)
    ov.set_show_orbs(False)
    assert s.dungeon_show_orbs is False
    assert ov.s.saved == 1
    ov.set_show_players(True)
    assert s.dungeon_show_players is True
    ov.set_show_loots(False)
    assert s.dungeon_show_loots is False


# --- constructor smoke: the REAL overlay must build -----------------------

def test_dungeon_overlay_constructs_with_all_section_buttons(tmp_path, monkeypatch):
    """Regression (2026-09-19): the titlebar loop unpacked each _SECTION_TOGGLES
    tuple as (key, icon, tip) while the tuples carry five fields, so the real
    DungeonOverlay died in __init__ with 'too many values to unpack' and the
    whole HUD never appeared. __new__-built tests never run __init__, which is
    how this shipped; this test builds the real window."""
    monkeypatch.setenv("FAREVER_MODDATA_DIR", str(tmp_path))
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    from farever_companion.config import Settings

    s = Settings()
    ov = DungeonOverlay(model=None, settings=s)
    try:
        assert sorted(ov._section_btns) == [
            "boss", "enemies", "food", "loots", "orbs", "players", "rift"]
        # The real settings surface drives the button tint both ways.
        ov.set_show_section("food", False)
        assert s.dungeon_show_food is False
        assert ov._section_btns["food"].toolTip() == "Show food"
        ov.set_show_section("food", True)
        assert s.dungeon_show_food is True
    finally:
        ov._timer.stop()
        ov.close()


def test_dungeon_hud_reports_the_size_it_takes_in_game(tmp_path, monkeypatch):
    """`ingame_size` is what the 1:1 preview pins: the scaled base width, and
    the body + chrome height `_do_auto_height` grows the live window to. One
    rule shared by the resize and the preview, so the preview cannot drift
    from the HUD it previews."""
    monkeypatch.setenv("FAREVER_MODDATA_DIR", str(tmp_path))
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    from farever_companion.config import Settings

    ov = DungeonOverlay(model=None, settings=Settings())
    try:
        assert ov.ingame_size().width() == round(ov._base_w * ov._scale)
        assert ov.ingame_size().width() >= ov.minimumWidth()
        assert ov.ingame_size().height() == ov._ingame_height()
        ov._do_auto_height()
        assert ov.height() == ov._ingame_height()
        assert ov.ingame_size().height() == ov.height()
    finally:
        ov._timer.stop()
        ov.close()
