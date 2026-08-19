"""Gear-type chip count tests (headless Qt, offscreen platform).

The type chips under the Quick tabs carry a live count ('Back · 24') that
must mirror the gear-tab matcher exactly — class, Only, Min floor and
rating — so the number always matches how many rows clicking the chip would
show. Covers the family_counts oracle against the matcher predicate and the
full-page chip text updating as filters toggle.
"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtWidgets  # noqa: E402

from farever_companion import paths  # noqa: E402
from farever_companion.data import items as idata  # noqa: E402
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


# --- oracle: family_counts vs the gear-tab matcher -------------------------


def _gear_match(item_id: str, cls: str, only: bool, mn: str,
                rating: str) -> bool:
    """The gear-tab predicate (the class/only/floor/rating parts of
    _items_match) applied to one item, as an independent oracle."""
    if idata.is_shop_item(item_id):
        return False
    it = idata.item(item_id) or {}
    if not idata.is_gear(it):
        return False
    if idata.category(it.get("type")) == "Crafting":
        return False
    if rating and rating not in idata.gear_ratings(item_id):
        return False
    if mn and idata.category(it.get("type")) != "Accessory" \
            and support.rarity_rank(idata.item_display_rarity(item_id)) \
            < support.rarity_rank(mn):
        return False
    classes = it.get("classes") or []
    if classes:
        if cls and cls not in classes:
            return False
        if only and cls and len(classes) > 1:
            return False
    return True


@pytest.mark.parametrize("cls", ["", "Fighter", "Assassin", "Cleric",
                                 "Wizard"])
@pytest.mark.parametrize("only", [False, True])
@pytest.mark.parametrize("mn", ["", "Rare"])
def test_family_counts_mirror_gear_matcher(cls, only, mn):
    """Every armor family's chip count equals the matcher's count under the
    same class / Only / floor state (rating off)."""
    for fam in ("Back", "Chest", "Feet", "Head"):
        expected = sum(
            1 for it in idata.items()
            if support.type_family(it.get("type")) == fam
            and _gear_match(it.get("id") or "", cls, only, mn, ""))
        assert support.family_counts(cls, only, mn) \
            .get(fam, 0) == expected, (fam, cls, only, mn)


def test_family_counts_rating_mirrors_matcher():
    """A picked rating narrows the chip counts to gear rolling it."""
    for rating in idata.RATING_STATS:
        expected = sum(
            1 for it in idata.items()
            if support.type_family(it.get("type")) == "Back"
            and _gear_match(it.get("id") or "", "", False, "Rare", rating))
        got = support.family_counts("", False, "Rare", rating).get("Back", 0)
        assert got == expected, rating


# --- full-page: chip text follows the live filters -------------------------


class _Page(QtWidgets.QWidget, ItemPageMixin):
    def _page_container(self, title=None):
        page = QtWidgets.QWidget()
        return page, QtWidgets.QVBoxLayout(page)


def _page():
    p = _Page()
    page = p._page_gear()
    QtWidgets.QVBoxLayout(p).addWidget(page)
    p.show()
    return p


def _chip_counts(p):
    return {f: c.text() for f, c in p._items_type_chips.items()
            if c.isVisible()}


def _visible_by_family(p):
    out = {}
    for i in range(p._items_list.count()):
        li = p._items_list.item(i)
        if li.data(support.TYPE_HEADER_ROLE) or li.isHidden():
            continue
        fam = support.type_family(li.data(support.TYPE_ROLE))
        out[fam] = out.get(fam, 0) + 1
    return out


def _chip_number(text: str) -> int:
    return int(text.split("·")[-1].strip())


def test_chip_counts_track_class_and_only():
    """Chips start at the floor-filtered total, drop with a picked class,
    drop again with Only — and always match the visible rows."""
    p = _page()
    support.quick_pick(p, "Armor")
    assert _chip_counts(p)["Back"] == \
        f"Back · {_visible_by_family(p)['Back']}"

    # Fighter: the Back chip drops to only Fighter-capable, floor-filtered
    p._items_class_chips["Fighter"].setChecked(True)
    assert _chip_counts(p)["Back"] == \
        f"Back · {_visible_by_family(p)['Back']}"

    # Only: dual-class gear drops out of both the chip and the list
    p._items_only_chip.setChecked(True)
    assert _chip_counts(p)["Back"] == \
        f"Back · {_visible_by_family(p)['Back']}"

    # dropping Only restores the dual-class count
    p._items_only_chip.setChecked(False)
    assert _chip_counts(p)["Back"] == \
        f"Back · {_visible_by_family(p)['Back']}"


def test_chip_counts_track_min_floor_and_rating():
    """The Rare floor (default) excludes Uncommon/Common gear from the chip;
    raising the floor or picking a rating narrows it further."""
    p = _page()
    support.quick_pick(p, "Armor")
    with_floor = _chip_number(_chip_counts(p)["Back"])

    # No floor: the chip grows to the full family count
    p._items_min = ""
    p._items_refilter()
    no_floor = _chip_number(_chip_counts(p)["Back"])
    assert no_floor > with_floor
    assert _chip_counts(p)["Back"] == \
        f"Back · {_visible_by_family(p)['Back']}"

    # a rating chip narrows the count to gear rolling it
    p._items_rating_chips["Critical"].setChecked(True)
    assert _chip_counts(p)["Back"] == \
        f"Back · {_visible_by_family(p)['Back']}"
    assert _chip_number(_chip_counts(p)["Back"]) < no_floor
