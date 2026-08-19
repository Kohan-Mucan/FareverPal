"""Items-page tile hit-testing (headless Qt, offscreen platform).

The icon-mode tile grid mixes full-width type-section headers with
variable-height tiles, and refiltering hides whole sections. Qt's icon
flow then desyncs its hit-test tree from the painted rects — the FIRST
visible tile is drawn at the top of the list but indexAt misses it, so
it can't be clicked (and right-click there only finds the bare viewport,
so the copy menu offers no text). The page forces a synchronous re-flow
after the visibility pass and when the icon fill completes; these tests
pin that every visible tile stays clickable and copy-able.
"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtWidgets  # noqa: E402

from farever_companion import paths  # noqa: E402
from farever_companion.ui import copy_menu  # noqa: E402
from farever_companion.ui.pages.items import ItemPageMixin, support  # noqa: E402


def _data_present() -> bool:
    try:
        return paths.item_drops_path().exists()
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _data_present(),
    reason="requires bundled data (assets/data/item_drops.json)")


@pytest.fixture(scope="module", autouse=True)
def _qapp():
    """One offscreen QApplication for the whole module (widgets need one)."""
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class _Page(QtWidgets.QWidget, ItemPageMixin):
    def _page_container(self, title=None):
        page = QtWidgets.QWidget()
        return page, QtWidgets.QVBoxLayout(page)

    def _codex_jump_to_unit(self, *args, **kwargs):
        return None


def _page():
    p = _Page()
    page = p._page_gear()
    QtWidgets.QVBoxLayout(p).addWidget(page)
    p.resize(1200, 820)
    p.show()
    # drain the batched icon-fill timer (0-ms ticks) — the page's flow
    # rebuild happens when the fill completes
    app = QtWidgets.QApplication.instance()
    for _ in range(40):
        app.processEvents()
    return p


def _visible_tiles(lst):
    """(index, item) for every visible non-header row, in model order."""
    out = []
    for i in range(lst.count()):
        li = lst.item(i)
        if li is not None and not li.isHidden() \
                and not li.data(support.TYPE_HEADER_ROLE):
            out.append((i, li))
    return out


def _tile_hits(lst):
    """[(index, id) of visible tiles whose own center indexAt misses]."""
    bad = []
    for i, li in _visible_tiles(lst):
        vr = lst.visualRect(lst.indexFromItem(li))
        if vr.isNull() or vr.width() <= 0 or vr.height() <= 0:
            continue
        if not lst.indexAt(vr.center()).isValid():
            bad.append((i, li.data(support.ID_ROLE)))
    return bad


def test_first_visible_tile_is_clickable():
    """The first tile of the default Gear list (the one right under the
    first type header) must hit-test to itself — it was painted but dead
    to clicks when hidden header rows desynced Qt's icon-mode layout."""
    p = _page()
    lst = p._items_list
    assert _visible_tiles(lst), "list should have visible tiles"
    i, li = _visible_tiles(lst)[0]
    vr = lst.visualRect(lst.indexFromItem(li))
    assert vr.isValid() and vr.width() > 0 and vr.height() > 0
    hit = lst.indexAt(vr.center())
    assert hit.isValid(), \
        f"first tile {li.data(support.ID_ROLE)!r} is not clickable " \
        f"(rect {vr})"
    assert hit.row() == i


def test_every_visible_tile_is_clickable():
    """All visible tiles of the default Gear list hit-test to themselves
    — a refilter-triggered re-flow must not break tiles mid-list."""
    p = _page()
    bad = _tile_hits(p._items_list)
    assert not bad, f"tiles not clickable: {bad[:6]}"


def test_tiles_clickable_after_refilter():
    """Filtering (a search query) reflows the grid; the tiles must stay
    clickable right after the refilter, not only after the deferred
    layout eventually runs."""
    p = _page()
    p._items_search.setText("axe")
    for _ in range(40):
        QtWidgets.QApplication.instance().processEvents()
    bad = _tile_hits(p._items_list)
    assert not bad, f"tiles not clickable after refilter: {bad[:6]}"


def test_first_tile_copy_menu_finds_text():
    """Right-click on the first tile must surface its text in the copy
    menu (the app-wide copy filter reads indexAt through the viewport,
    which was the exact spot that returned 'no item' before the fix)."""
    p = _page()
    lst = p._items_list
    i, li = _visible_tiles(lst)[0]
    vr = lst.visualRect(lst.indexFromItem(li))
    gpos = lst.viewport().mapToGlobal(vr.center())
    text = copy_menu._item_text_at(lst, gpos)
    assert text == li.text()
