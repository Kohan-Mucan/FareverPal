"""The user planner: the gear-farm list + crafting queue persistence
(planner.json) + the Items page Farm tab.

The farm list and crafting queue are user state (config_dir()/planner.json)
— every test points FAREVER_MODDATA_DIR at a throwaway folder so nothing
touches real user data, and clears the planner module's parse cache
between cases.
"""
import json
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtWidgets  # noqa: E402

from farever_companion import planner as pdata  # noqa: E402
from farever_companion import paths  # noqa: E402
from farever_companion.data import items as idata  # noqa: E402
from farever_companion.ui import components as C  # noqa: E402
from farever_companion.ui.pages.items import ItemPageMixin  # noqa: E402
from farever_companion.ui.pages.items import support  # noqa: E402

# a boss-dropped shoulders piece (Assassin/Fighter, Nepsilon), a
# dual-class hauberk (Cleric/Fighter, world drop) and a crafted mantle
# (Assassin, no drop sources) — the farm-tab test fixtures
_SHOULDERS = "Shoulders_RManfish_FigAss"
_HAUBERK = "Chest_Z2U1_FigCle"
_MANTLE = "Shoulders_RDemon_Ass_Craft"


def _data_present() -> bool:
    try:
        return paths.item_drops_path().exists()
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _data_present(),
    reason="requires bundled data (assets/data/item_drops.json)")


@pytest.fixture(autouse=True)
def _moddata(tmp_path, monkeypatch):
    """A throwaway config dir + a cold planner.json cache per test."""
    monkeypatch.setenv("FAREVER_MODDATA_DIR", str(tmp_path))
    pdata._data.cache_clear()
    yield
    pdata._data.cache_clear()


@pytest.fixture(scope="module", autouse=True)
def _qapp():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


# --- persistence ---------------------------------------------------------
def test_gear_persistence_roundtrip():
    assert pdata.gear_item_ids() == []
    assert pdata.add_gear(_SHOULDERS) is True
    assert pdata.add_gear(_HAUBERK) is True
    assert pdata.gear_item_ids() == [_SHOULDERS, _HAUBERK]   # add order
    assert pdata.has_gear(_SHOULDERS)
    assert pdata.add_gear(_SHOULDERS) is False               # dup is a no-op
    assert pdata.gear_item_ids() == [_SHOULDERS, _HAUBERK]

    # the file on disk matches (crash-safe atomic write)
    path = pdata._path()
    assert json.loads(path.read_text(encoding="utf-8")) == \
        {"farm": [_SHOULDERS, _HAUBERK],
         "craft_queue": {"entries": [], "got": {}}}

    pdata.remove_gear(_SHOULDERS)
    assert pdata.gear_item_ids() == [_HAUBERK]
    pdata.clear_gear()
    assert pdata.gear_item_ids() == []
    assert json.loads(path.read_text(encoding="utf-8")) == \
        {"farm": [], "craft_queue": {"entries": [], "got": {}}}


def test_gear_corrupt_file_falls_back_and_rewrites(tmp_path, monkeypatch):
    monkeypatch.setenv("FAREVER_MODDATA_DIR", str(tmp_path))
    pdata._data.cache_clear()
    pdata._path().write_text("{not json", encoding="utf-8")
    assert pdata.gear_item_ids() == []          # falls back, doesn't crash
    pdata.add_gear(_SHOULDERS)                  # and rewrites the file
    assert json.loads(pdata._path().read_text(encoding="utf-8")) == \
        {"farm": [_SHOULDERS], "craft_queue": {"entries": [], "got": {}}}


def test_craft_queue_persistence_roundtrip():
    """The crafting queue shares planner.json with the farm list: entries
    and gathered counts round-trip, and a farm write never clobbers the
    queue (or vice versa)."""
    assert pdata.queue_entries() == []
    assert pdata.queue_got() == {}
    pdata.queue_save(
        [{"item": "SmallAlchemistCauldron", "qty": 10,
          "name": "Minor Alchemist Cauldron"}],
        {"Vial": 40})
    assert pdata.queue_entries() == [
        {"item": "SmallAlchemistCauldron", "qty": 10,
         "name": "Minor Alchemist Cauldron"}]
    assert pdata.queue_got() == {"Vial": 40}
    # the farm list survives a queue save in the same file
    pdata.add_gear(_SHOULDERS)
    assert pdata.gear_item_ids() == [_SHOULDERS]
    assert pdata.queue_entries()[0]["item"] == "SmallAlchemistCauldron"
    payload = json.loads(pdata._path().read_text(encoding="utf-8"))
    assert payload == {
        "farm": [_SHOULDERS],
        "craft_queue": {
            "entries": [{"item": "SmallAlchemistCauldron", "qty": 10,
                          "name": "Minor Alchemist Cauldron"}],
            "got": {"Vial": 40}}}
    # an empty queue persists as empty state
    pdata.queue_save([], {})
    assert json.loads(pdata._path().read_text(encoding="utf-8")) == \
        {"farm": [_SHOULDERS], "craft_queue": {"entries": [], "got": {}}}

# --- the Farm tab (composed Items page) ----------------------------------
def _make_page():
    class Dummy(ItemPageMixin):
        def _page_container(self, title=None):
            page = QtWidgets.QWidget()
            v = QtWidgets.QVBoxLayout(page)
            v.setContentsMargins(22, 20, 22, 20)
            v.setSpacing(16)
            return page, v

        _codex_jump_to_unit = staticmethod(lambda *a: None)

    d = Dummy()
    page = d._page_gear()
    win = QtWidgets.QMainWindow()
    win.setCentralWidget(page)
    win.resize(1150, 760)
    win.show()
    return d, win


def _farm_body(d):
    return d._farm_lay.parentWidget()


def _farm_labels(d):
    return [l.text() for l in _farm_body(d).findChildren(QtWidgets.QLabel)
            if l.text()]


def test_farm_tab_renders_rows_with_class_stats_boss():
    pdata.add_gear(_SHOULDERS)
    pdata.add_gear(_HAUBERK)
    idata.set_stat_display_mode("both")
    d, win = _make_page()
    try:
        # the count is set at page build — it reads before opening the tab
        assert "· 2 Items" in d._items_tabs._btns["Farm"].text()
        d._items_set_mode("Farm")
        for _ in range(6):
            QtWidgets.QApplication.processEvents()
        lbls = _farm_labels(d)
        assert any("GEAR FARM" in t for t in lbls)
        # the count lives on the Farm tab, not in the header
        assert not any(t == "2 ITEMS" for t in lbls)
        tab_text = d._items_tabs._btns["Farm"].text()
        assert "· 2 Items" in tab_text
        # class tag -> item name -> base stats, boss on the right (each
        # class renders as its own colored pill)
        sh_row = next(w for w in _farm_body(d).findChildren(
            QtWidgets.QFrame) if w.objectName() == "Cell")
        sh_lbls = [l.text() for l in sh_row.findChildren(
            QtWidgets.QLabel) if l.text()]
        assert "Rogue" in sh_lbls and "Warrior" in sh_lbls, sh_lbls
        assert any(t == "Abyssal Shoulderplates" for t in lbls)
        # the row shows the rounded value with the TRUE computed float in
        # parens (e.g. 'Armor 208 (207.x…)') — not just the lossy integer.
        # L25: dungeon-scaled gear previews at char max (hard mode drops it
        # there), not the per-family dungeon level.
        assert any("Armor 208" in t and "Armor Penetration 11" in t
                   and "(" in t for t in lbls)
        assert any("Nepsilon" in t for t in lbls)          # the bosses:
        assert any("Crabgantua" in t for t in lbls)        # one stack per
        assert any("Sponge Blob" in t for t in lbls)       # family boss
        assert any("Faith 2" in t and "Armor 144" in t for t in lbls)
        # each row shows its game icon (a real rendered tile) and the class
        tiles = _farm_body(d).findChildren(C.IconTile)
        assert len(tiles) == 2
        assert all(not t.pixmap().isNull() for t in tiles)
        # the column header strip (ICON | ITEM · CLASS | DROPS FROM) sits
        # above the rows, with the icon column aligned over the tiles and
        # DROPS FROM pinned over the boss column
        body = _farm_body(d)
        head_lbls = {l.text(): l for l in body.findChildren(QtWidgets.QLabel)
                     if l.text() in ("ICON", "ITEM · CLASS", "DROPS FROM")}
        assert set(head_lbls) == {"ICON", "ITEM · CLASS", "DROPS FROM"}
        assert all(t.x() == head_lbls["ICON"].x() for t in tiles)
        row = next(w for w in body.findChildren(QtWidgets.QFrame)
                   if w.objectName() == "Cell")
        boss = next(l for l in row.findChildren(QtWidgets.QLabel)
                    if l.text() == "Sponge Blob")
        hdr_right = head_lbls["DROPS FROM"].x() + \
            head_lbls["DROPS FROM"].width()
        # the boss label lives inside the row's source cell, so its right
        # edge is mapped into row coordinates before comparing to the
        # header (both share the row's right 50px: trash 26 + spacing 12)
        boss_right = boss.mapTo(row, QtCore.QPoint(0, 0)).x() \
            + boss.width()
        assert abs(hdr_right - boss_right) <= 4
        # one trash button per row
        trash = [b for b in _farm_body(d).findChildren(QtWidgets.QPushButton)
                 if b.objectName() == "FarmRemove"]
        assert len(trash) == 2
    finally:
        win.close()
        idata.set_stat_display_mode("rounding")


def test_farm_rows_show_drop_source_cell():
    """Every farm row's right column shows WHERE the piece comes from with
    an icon, so the DROPS FROM column is never empty: the boss's unit-sprite
    icon + gold name + dungeon sub-line for boss drops, the world-drop
    source (World Crates) for world gear, and a CRAFTED tag + the recipe
    job for crafted gear (no drop sources). The base stats split into two
    lines for pieces with 4+ stats."""
    pdata.add_gear(_SHOULDERS)     # boss Nepsilon · Manfish Ruins
    pdata.add_gear(_HAUBERK)       # world drop — World Crates
    pdata.add_gear(_MANTLE)        # crafted — no drops
    d, win = _make_page()
    try:
        d._items_set_mode("Farm")
        for _ in range(6):
            QtWidgets.QApplication.processEvents()
        body = _farm_body(d)
        rows = [w for w in body.findChildren(QtWidgets.QFrame)
                if w.objectName() == "Cell"]
        assert len(rows) == 3

        def row_labels(row):
            return [l.text() for l in row.findChildren(QtWidgets.QLabel)
                    if l.text()]

        def has_label(row, *parts):
            return any(all(p in t for p in parts)
                       for t in row_labels(row))

        # shoulders: one boss stack per family dungeon — sprite, gold name,
        # dungeon sub-line
        sh = next(r for r in rows if has_label(r, "Abyssal Shoulderplates"))
        rl = row_labels(sh)
        assert "Nepsilon" in rl and "Crabgantua" in rl and "Sponge Blob" in rl
        assert "Manfish Ruins" in rl and "Nepsid Boss" in rl \
            and "Manfish Abyss" in rl
        pics = [l for l in sh.findChildren(QtWidgets.QLabel)
                if l.pixmap() and not l.pixmap().isNull()]
        assert len(pics) == 4         # the item tile + 3 boss sprites

        # hauberk: the world-drop source with its kind icon (no boss)
        hb = next(r for r in rows if has_label(r, "Blessed Hauberk"))
        assert "World Crates" in row_labels(hb)

        # mantle: crafted — CRAFTED tag + job, and the stats split in 2
        mt = next(r for r in rows if has_label(r, "Demon Hunter"))
        rl = row_labels(mt)
        assert "CRAFTED · L25" in rl
        assert "OUTFITTER · LV 6" in rl
        st = next(l for l in mt.findChildren(QtWidgets.QLabel)
                  if "Dexterity 8" in l.text())
        assert "\n" in st.text()      # 'Dexterity 8 · Armor 166' /
        assert st.text().count("\n") == 1   #   'Critical 22 · Vitality 7'
    finally:
        win.close()


def test_farm_class_filter_is_single_select_with_all():
    """The farm class chips are single-select over an All chip: picking a
    class shows only gear for it (replacing any previous pick), unticking
    or picking All restores every saved piece."""
    pdata.add_gear(_SHOULDERS)     # Assassin + Fighter
    pdata.add_gear(_HAUBERK)       # Cleric + Fighter
    d, win = _make_page()
    try:
        d._items_set_mode("Farm")
        for _ in range(6):
            QtWidgets.QApplication.processEvents()

        def chips():
            return {c.text(): c for c in
                    _farm_body(d).findChildren(C.FilterChip)}

        ch = chips()
        assert "All" in ch and ch["All"].isChecked()   # default = all

        # picking Priest narrows to the hauberk
        ch["Priest"].setChecked(True)
        for _ in range(6):
            QtWidgets.QApplication.processEvents()
        lbls = _farm_labels(d)
        assert any(t == "Blessed Hauberk of the Adventurer" for t in lbls)
        assert not any(t == "Abyssal Shoulderplates" for t in lbls)
        ch = chips()
        assert ch["Priest"].isChecked() and not ch["All"].isChecked()

        # picking Rogue REPLACES the pick (single-select): shoulders only
        ch["Rogue"].setChecked(True)
        for _ in range(6):
            QtWidgets.QApplication.processEvents()
        lbls = _farm_labels(d)
        assert any(t == "Abyssal Shoulderplates" for t in lbls)
        assert not any(t == "Blessed Hauberk of the Adventurer"
                       for t in lbls)
        ch = chips()
        assert ch["Rogue"].isChecked() and not ch["Priest"].isChecked()

        # picking All (or unticking the class) restores everything
        ch["All"].setChecked(True)
        for _ in range(6):
            QtWidgets.QApplication.processEvents()
        lbls = _farm_labels(d)
        assert any(t == "Abyssal Shoulderplates" for t in lbls)
        assert any(t == "Blessed Hauberk of the Adventurer" for t in lbls)
    finally:
        win.close()


def test_farm_copy_button_exports_list_with_bosses():
    """The Farm tab's COPY button puts a markdown-flavored run list on the
    clipboard — '**GEAR FARM**' over one `- Name — Boss (Dungeon)` bullet
    per saved piece (world drops without a boss keep just the name),
    respecting the active class filter — and flashes ✓ COPIED."""
    pdata.add_gear(_SHOULDERS)     # boss: Nepsilon · Manfish Ruins
    pdata.add_gear(_HAUBERK)       # world drop, no boss
    d, win = _make_page()
    try:
        d._items_set_mode("Farm")
        for _ in range(6):
            QtWidgets.QApplication.processEvents()
        copy = next(b for b in _farm_body(d).findChildren(
            QtWidgets.QPushButton) if b.text() == "COPY")
        copy.click()
        clip = QtWidgets.QApplication.clipboard().text()
        lines = clip.splitlines()
        assert lines[0] == "**GEAR FARM**"
        assert ("- Abyssal Shoulderplates (Rogue · Warrior) — "
                "Crabgantua (Nepsid Boss) · Nepsilon (Manfish Ruins) · "
                "Sponge Blob (Manfish Abyss)") in clip
        assert "- Blessed Hauberk of the Adventurer (Priest · Warrior)" in clip
        non_empty = [ln for ln in clip.splitlines() if ln]
        assert non_empty[-1].startswith("Exported ")   # timestamp
        assert copy.text() == "✓ COPIED"

        # the class filter narrows the export too (Priest -> hauberk only)
        priest = next(c for c in _farm_body(d).findChildren(C.FilterChip)
                      if c.text() == "Priest")
        priest.setChecked(True)
        for _ in range(6):
            QtWidgets.QApplication.processEvents()
        copy.click()
        clip = QtWidgets.QApplication.clipboard().text()
        assert "- Blessed Hauberk of the Adventurer (Priest · Warrior)" in clip
        assert "Abyssal Shoulderplates" not in clip
    finally:
        win.close()


def test_gear_detail_card_adds_to_farm_and_trash_removes():
    d, win = _make_page()
    try:
        # open the shoulders piece in the detail pane
        for i in range(d._items_list.count()):
            li = d._items_list.item(i)
            if li.data(support.ID_ROLE) == _SHOULDERS:
                d._items_list.setCurrentRow(i)
                break
        else:
            pytest.fail("shoulders piece not in the item list")
        for _ in range(6):
            QtWidgets.QApplication.processEvents()
        add = next(b for b in win.centralWidget().findChildren(
            QtWidgets.QPushButton) if b.text() == "ADD TO FARM")
        assert pdata.gear_item_ids() == []
        add.click()
        assert pdata.gear_item_ids() == [_SHOULDERS]
        assert add.text() == "✓ FARMED"     # flash state
        add.click()                          # re-add is a no-op
        assert pdata.gear_item_ids() == [_SHOULDERS]

        # the Farm tab shows it and the trash button removes + persists
        d._items_set_mode("Farm")
        for _ in range(6):
            QtWidgets.QApplication.processEvents()
        trash = next(b for b in _farm_body(d).findChildren(
            QtWidgets.QPushButton) if b.objectName() == "FarmRemove")
        trash.click()
        for _ in range(6):
            QtWidgets.QApplication.processEvents()
        assert pdata.gear_item_ids() == []
        assert any("Farm is empty" in t for t in _farm_labels(d))
    finally:
        win.close()


def test_stat_display_mode_toggle_in_search_header():
    """The search header carries the gear-stat display-mode toggle (default
    Rounded) and the result count sits right after the SEARCH label, with
    the toggle at the far right. Clicking the toggle cycles
    Rounded -> Exact -> Both -> Rounded and updates the button's live
    sample."""
    assert idata.stat_display_mode() == "rounding"
    d, win = _make_page()
    try:
        hdr = d._items_search_header
        btn = d._items_stat_mode_btn
        assert btn.parentWidget() is hdr
        # the count reads next to the title; the toggle is the far-right item
        lay = hdr.layout()
        assert lay.indexOf(hdr._tag) == lay.indexOf(hdr._label) + 1
        assert lay.indexOf(btn) == lay.count() - 1
        # default sample is the clean rounded form
        assert btn.text() == "16"
        btn.click()
        for _ in range(6):
            QtWidgets.QApplication.processEvents()
        assert idata.stat_display_mode() == "true"
        assert btn.text() == "15.987"
        btn.click()
        for _ in range(6):
            QtWidgets.QApplication.processEvents()
        assert idata.stat_display_mode() == "both"
        assert btn.text() == "16 (15.987)"
        btn.click()
        for _ in range(6):
            QtWidgets.QApplication.processEvents()
        assert idata.stat_display_mode() == "rounding"
        assert btn.text() == "16"
    finally:
        win.close()
        idata.set_stat_display_mode("rounding")
