"""Codex jump (Items/Craft Drops From -> codex card) tests, headless Qt.

The generalized _codex_jump_to_unit (ui/pages/codex/map_ui.py): non-boss
units — world mobs and NPC vendors like Perina Wann — land on their
region's tab and click their card like a real user, and the dungeon-list
path falls back to the card grid when the unit isn't a dungeon row. Driven
through a minimal fake page: only the attributes the mixin touches are
provided, so the tests don't need a full ControlPanel.
"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtWidgets  # noqa: E402

from farever_companion.ui.pages.codex.map_ui import CodexMapUiMixin  # noqa: E402
from farever_companion.ui.pages.codex.soulstones import SoulstoneMixin  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def _qapp():
    """One offscreen QApplication for the whole module (widgets need one)."""
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class _FakeCard(QtCore.QObject):
    selected = QtCore.Signal(str)

    def __init__(self, uid: str):
        super().__init__()
        self.uid = uid


class _FakeScroll:
    def __init__(self):
        self._body = QtWidgets.QWidget()
        self.ensured = []

    def widget(self):
        return self._body

    def ensureWidgetVisible(self, w, xmargin=0, ymargin=0):
        self.ensured.append(w)


class _FakeBtn:
    def __init__(self, checked=False):
        self._checked = checked

    def setChecked(self, on):
        self._checked = bool(on)

    def isChecked(self):
        return self._checked

    def blockSignals(self, _on):
        return False


class _FakeTabs:
    def __init__(self):
        self._text = ""

    def setCurrentText(self, t):
        self._text = t

    def currentText(self):
        return self._text

    def blockSignals(self, _on):
        return False


class _FakeSearch:
    def __init__(self, text=""):
        self._text = text

    def text(self):
        return self._text

    def setText(self, t):
        self._text = t

    def blockSignals(self, _on):
        return False


class _FakeDungeonList:
    def __init__(self, cells=None):
        self._cells = list(cells or [])

    def layout(self):
        return None


class _FakeSubBtn:
    """Collection sub-view button: records clicks (the real handler refreshes
    the grid, which the fake page's refresh already covers)."""

    def __init__(self, label):
        self.label = label
        self.clicks = 0

    def click(self):
        self.clicks += 1


class _FakeSoulstoneCell(QtCore.QObject):
    pick = QtCore.Signal(object)

    def __init__(self, poi):
        super().__init__()
        self.poi = poi
        self.picked = []
        self.pick.connect(lambda p: self.picked.append(p))


class _FakeSoulstoneList:
    def __init__(self, pois):
        self._cells = [_FakeSoulstoneCell(p) for p in pois]


class _FakeChestCell(QtCore.QObject):
    pick = QtCore.Signal(object)

    def __init__(self, cid):
        super().__init__()
        self.item = {"id": cid}
        self.picked = []
        self.pick.connect(lambda it: self.picked.append(it))


class _FakeChestList:
    def __init__(self, ids):
        self._cells = [_FakeChestCell(i) for i in ids]


class _FakePage(CodexMapUiMixin, SoulstoneMixin):
    """Minimal page: provides just the attributes _codex_jump_to_unit and
    friends touch; _refresh_codex_grid renders the stored cards."""

    def __init__(self, cards):
        self._nav = None
        self._codex_tabs = _FakeTabs()
        self._codex_scroll = _FakeScroll()
        self._cards = list(cards)
        self._codex_cards = []
        self._clicks = []
        self._map_clears = 0
        for c in self._cards:
            c.selected.connect(lambda uid: self._clicks.append(uid))
        self._dungeon_list_mode = True
        self._chest_orb_kind = None

    def _select_nav(self, key):
        self._nav = key

    def _refresh_codex_grid(self, reset_scroll=False):
        self._codex_cards = list(self._cards)

    def _clear_codex_map_selection(self):
        self._map_clears += 1


def _mk(uid: str):
    return _FakeCard(uid)


def test_vendor_jump_lands_on_others_tab_and_clicks_card():
    """Perina Wann (a vendor) jumps to the Others tab and clicks the Stable
    Master card like a real user."""
    page = _FakePage([_mk("TODO_StableMaster"), _mk("TODO_Slime_King")])
    page._codex_jump_to_unit("TODO_StableMaster", "Perina Wann")
    QtCore.QCoreApplication.processEvents()
    assert page._nav == "codex"
    assert page._codex_tabs.currentText() == "Other"
    assert page._dungeon_list_mode is False
    assert page._clicks == ["TODO_StableMaster"]
    assert page._codex_scroll.ensured \
        and page._codex_scroll.ensured[0].uid == "TODO_StableMaster"
    assert page._map_clears == 1


def test_zone_mob_jump_lands_on_its_region_tab():
    """A world mob (Krabby Jacob on Z1) lands on the Z1 zone tab and clicks
    its card — no Others/Collection sub-view juggling needed."""
    page = _FakePage([_mk("Crawler_Z1W_E")])
    page._codex_jump_to_unit("Crawler_Z1W_E", "Krabby Jacob")
    QtCore.QCoreApplication.processEvents()
    assert page._codex_tabs.currentText() == "Skover Island"  # Z1 region tab
    assert page._clicks == ["Crawler_Z1W_E"]


def test_dungeon_list_falls_back_to_card_grid():
    """A dungeon mob that isn't a dungeon-list row (no boss row matches)
    switches to the mobs card grid and clicks its card."""
    page = _FakePage([_mk("RobinHoofDog01")])
    page._codex_dungeon_list = _FakeDungeonList()   # no matching row
    page._btn_dungeon_mobs = _FakeBtn()
    page._btn_dungeon_list = _FakeBtn()
    page._dungeon_list_mode = True
    page._codex_click_unit("RobinHoofDog01", "Crimson Hunting Dog")
    QtCore.QCoreApplication.processEvents()
    assert page._dungeon_list_mode is False
    assert page._btn_dungeon_mobs.isChecked()
    assert page._btn_dungeon_list.isChecked() is False
    assert page._clicks == ["RobinHoofDog01"]


def test_mount_jump_lands_on_collection_mounts_and_clicks_card():
    """A mount item's VIEW IN CODEX link lands on the Collection tab's
    Mounts sub-view and clicks the mount's card like a real user."""
    page = _FakePage([_mk("Mount_Goat_01")])
    page._collection_sub_btns = {lbl: _FakeSubBtn(lbl)
                                 for lbl in ("Pets", "Mounts", "Gliders",
                                             "Chests", "Orbs")}
    page._codex_jump_to_unit("Mount_Goat_01", "Goat")
    QtCore.QCoreApplication.processEvents()
    assert page._nav == "codex"
    assert page._codex_tabs.currentText() == "Collection"
    assert page._collection_sub_btns["Mounts"].clicks == 1
    assert page._clicks == ["Mount_Goat_01"]
    assert page._codex_scroll.ensured \
        and page._codex_scroll.ensured[0].uid == "Mount_Goat_01"


def test_soulstone_jump_lands_on_dungeons_soulstones_and_clicks_row():
    """A soulstone demon boss (Ariana Grandemon — no codex card of her own)
    lands on the Dungeons tab's Soulstones list and clicks her summon-spot
    row like a real user."""
    from farever_companion.data import codex
    page = _FakePage([])
    page._btn_dungeon_soulstones = _FakeBtn()
    poi = next(p for p in codex.soulstone_pois()
               if p["spawn_unit"] == "FaerieDemon_Z1_Soulstone_Leg")
    page._codex_soulstone_list = _FakeSoulstoneList([poi])
    page._codex_jump_to_unit("FaerieDemon_Z1_Soulstone_Leg", "Ariana Grandemon")
    QtCore.QCoreApplication.processEvents()
    assert page._nav == "codex"
    assert page._codex_tabs.currentText() == "Dungeons"
    assert page._btn_dungeon_soulstones.isChecked()
    cell = page._codex_soulstone_list._cells[0]
    assert cell.picked and cell.picked[0]["id"] == poi["id"]
    assert page._codex_scroll.ensured \
        and page._codex_scroll.ensured[0] is cell


def test_chest_jump_lands_on_collection_chests_and_clicks_row():
    """A single-chest drop source lands on the Collection tab's Chests view
    and clicks the chest's row (tracks it + plots it like a manual click)."""
    page = _FakePage([])
    page._collection_sub_btns = {lbl: _FakeSubBtn(lbl)
                                 for lbl in ("Pets", "Mounts", "Gliders",
                                             "Chests", "Orbs")}
    page._zone_widgets = {"All": _FakeBtn(checked=True)}
    page._codex_chest_list = _FakeChestList(
        ["W1_Siagarta_WorldChest_54", "VaultChest_1"])
    page._codex_jump_to_unit("W1_Siagarta_WorldChest_54",
                             "W1_Siagarta_WorldChest_54 (x2)")
    QtCore.QCoreApplication.processEvents()
    assert page._nav == "codex"
    assert page._codex_tabs.currentText() == "Collection"
    assert page._collection_sub_btns["Chests"].clicks == 1
    cell = page._codex_chest_list._cells[0]
    assert cell.picked and cell.picked[0]["id"] == \
        "W1_Siagarta_WorldChest_54"
    assert page._codex_scroll.ensured \
        and page._codex_scroll.ensured[0].item["id"] == \
        "W1_Siagarta_WorldChest_54"


def test_jump_clears_filters_that_could_hide_the_card():
    """A stale search query, the spark toggle and source-badge filters are
    cleared before the jump so the target card can't be hidden."""
    page = _FakePage([_mk("TODO_StableMaster")])
    page._codex_search = _FakeSearch("stable")
    page._status_widgets = {"All": _FakeBtn(checked=True)}
    page._spark_only = True
    page._spark_filter_btn = _FakeBtn(checked=True)
    page._source_filters = {"npc"}
    page._source_filter_btns = {"npc": _FakeBtn(checked=True)}
    page._codex_reset_jump_filters()
    assert page._codex_search.text() == ""
    assert page._spark_only is False
    assert page._spark_filter_btn.isChecked() is False
    assert page._source_filters == set()
    assert page._source_filter_btns["npc"].isChecked() is False
