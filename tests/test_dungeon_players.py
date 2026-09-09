"""Dungeon HUD PLAYERS section: named rows in dungeons, class rows in rifts.

2026-09-18: a dungeon party is at most five, so the Dungeon HUD should show
each player's real name there; rifts (rival parties, dozens of heroes) keep
the class-grouped rendering.
"""
import pytest

pytest.importorskip("PySide6")

from PySide6 import QtWidgets

from farever_companion.ui.overlays.dungeon_render import DungeonRenderMixin
from farever_companion.ui.overlays.entity_rows import RowSpec


class _Hero:
    """Just what the PLAYERS renderers touch on an Entity."""

    def __init__(self, addr, cls="ent.Hero", unit_id="Hero_Warrior",
                 hero_class=None):
        self.addr = addr
        self.cls = cls
        self.unit_id = unit_id
        self.hero_class = hero_class


class _Header:
    def __init__(self):
        self.text = ""

    def set_text(self, t):
        self.text = t


class _Box:
    """Records fill(specs) so tests can assert on the RowSpecs."""

    def __init__(self):
        self.header = _Header()
        self.specs: list[RowSpec] = []
        self.visible = False

    def fill(self, specs, _isz):
        self.specs = list(specs)

    def show(self):
        self.visible = True

    def hide(self):
        self.visible = False


class _S:
    dungeon_show_players = True


class _Model:
    """Fake LiveModel: hero names for the party, in_dungeon/in_rift flags."""

    def __init__(self, names, rift=False):
        self._names = names
        self.player_addr = next(iter(names))
        self._rift = rift

    def is_in_dungeon(self):
        return not self._rift

    def is_in_rift(self):
        return self._rift

    def player_name(self, addr):
        return self._names.get(addr)

    def hero_display_name(self, e):
        n = self._names.get(getattr(e, "addr", None))
        return n or (e.cls or "").split(".")[-1]


class _Host(DungeonRenderMixin):
    """Just the players branch: state attrs + the two section widgets."""

    def __init__(self, players, model, s=None, deaths=None):
        self._dg_players = players
        self._dg_death_counts = deaths or {}
        self.dg_player_box = _Box()
        self.model = model
        self.s = s if s is not None else _S()

    def _dg_select(self, key, unit_id, addr=None):  # pragma: no cover
        pass


def test_dungeon_players_show_real_names(qapp):
    model = _Model({0x111: "Kohan", 0x222: "Mira", 0x333: "Tyrna"})
    # _dg_players arrives distance-sorted with me (0.0m) first — the
    # overlay's gather inserts me at the front (dungeon_overlay.py).
    host = _Host(
        [(_Hero(0x111), 0.0), (_Hero(0x222), 12.0), (_Hero(0x333), 30.0)],
        model)
    host._render_dg_players(isz=16)
    box = host.dg_player_box
    assert box.visible is True
    names = [sp.name for sp in box.specs]
    assert names == ["Kohan (You)", "Mira", "Tyrna"]      # me first (d=0)
    assert box.header.text == "PLAYERS · 3"
    # one row per player, class-colored, distance on the right
    assert all(sp.value.strip().endswith("m") for sp in box.specs)
    assert all(sp.ui_icon == "player" for sp in box.specs)


def test_dungeon_players_death_marker(qapp):
    model = _Model({0x111: "Kohan", 0x222: "Mira"})
    host = _Host([(_Hero(0x111), 0.0), (_Hero(0x222), 9.0)], model,
                 deaths={0x222: 2})
    host._render_dg_players(isz=16)
    assert [sp.name for sp in host.dg_player_box.specs] == \
        ["Kohan (You)", "Mira ☠2"]
    assert host.dg_player_box.header.text == "PLAYERS · 2 · ☠2"


def test_rift_players_stay_class_grouped(qapp):
    model = _Model({0x111: "Kohan", 0x222: "Mira"}, rift=True)
    host = _Host(
        [(_Hero(0x222, hero_class="Priest"), 12.0),
         (_Hero(0x333, hero_class="Priest"), 20.0),
         (_Hero(0x111), 0.0)],
        model)
    host._render_dg_players(isz=16)
    names = [sp.name for sp in host.dg_player_box.specs]
    # class-grouped, nearest class first: me (Warrior, 0m), then the pair
    assert names == ["Warrior", "Priest (x2)"]


def test_toggle_off_hides_section(qapp):
    model = _Model({0x111: "Kohan"})
    s = _S()
    s.dungeon_show_players = False
    host = _Host([(_Hero(0x111), 0.0)], model, s=s)
    host._render_dg_players(isz=16)
    assert host.dg_player_box.visible is False


@pytest.fixture(scope="module")
def qapp():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield app
