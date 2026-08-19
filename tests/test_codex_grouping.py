"""Codex Collection species-grouping tests (headless Qt).

Pets already group by species (sub-headers + the group sub-menu); this
covers the same treatment for the Collection tab's Mounts/Gliders views:
Z0 rows carry their compiled `group` family, the grid sorts + renders one
sub-header per family, and the group sub-menu options match the families
actually rendered.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6 import QtCore, QtWidgets  # noqa: E402

from farever_companion.data import codex  # noqa: E402
from farever_companion.ui.pages.codex.grid import CodexGridMixin  # noqa: E402
from farever_companion.ui.pages.codex.grouping import CodexGroupingMixin  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def _qapp():
    """One offscreen QApplication for the whole module (widgets need one)."""
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class _FakeSettings:
    """Settings stub: hidden companion set drives the Visible/Hidden counts."""

    def __init__(self, hidden_comps=()):
        self.codex_compact = False
        self.codex_pets_compact = True
        self._hidden_comps = set(hidden_comps)

    def get_companion_hidden_units(self, profile):
        return self._hidden_comps

    def get_entity_hidden_units(self, profile):
        return set()


class _FakeGridPage(QtCore.QObject, CodexGridMixin, CodexGroupingMixin):
    """Minimal page: just enough of the grid pipeline for the grouping tests."""

    def __init__(self):
        super().__init__()
        self.s = _FakeSettings()
        self.model = None
        self._body = QtWidgets.QWidget()
        self._codex_grid = QtWidgets.QGridLayout(self._body)
        self._codex_cards = []

    def _set_companion_hidden(self, *_a, **_k):
        pass

    def _set_unit_hidden(self, *_a, **_k):
        pass

    def _on_codex_mob_selected(self, *_a, **_k):
        pass

    def _codex_is_collection_view(self, current_rid, tab_label):
        return current_rid in ("Pets", "Z0", "Mounts", "Gliders") \
            or tab_label in ("Others", "Other", "Pets", "Collection",
                             "Mounts", "Gliders")

    def _codex_current_region(self):
        return getattr(self, "_cur_rid", "Z1"), getattr(self, "_cur_tab", "Skover Island")


def _sub_view_items(page, sub_tag):
    """Z0 rows the Collection sub-view actually renders (unreleased excluded)."""
    return [it for it in codex.units_by_region("Z0", ingame_order=True)
            if page._codex_collection_ok(it, sub_tag)]


def _group_key(it: dict, sub_tag: str) -> str:
    """The grouping key the page actually renders for `it`: the species
    family for pets / the compiled `group` for mounts & gliders — except
    cash-shop / early-access items, which group under the general 'Shop'
    family instead of masquerading as a species (SparkHorse_01 -> 'Shop',
    not 'Spark Horse'). Mirrors _codex_sub_group / _codex_group_counts."""
    if codex.is_shop_item(it):
        return "Shop"
    return (it.get("type") if sub_tag == "pets" else it.get("group") or "").strip()


def _rendered_header_labels(page):
    """Top-level QLabel widgets in the grid = the sub-header rows."""
    labels = []
    for i in range(page._codex_grid.count()):
        w = page._codex_grid.itemAt(i).widget()
        if isinstance(w, QtWidgets.QLabel):
            labels.append(w.text())
    return labels


def test_sub_group_key_family_vs_type_bucket():
    """The shared grouping key: species family on Collection mounts/gliders,
    raw type everywhere else (Pets species, Bosses dungeon, Z0 Others)."""
    page = _FakeGridPage()
    assert page._codex_sub_group({"type": "Mount", "group": "Aries"}, "Z0", True) == "Aries"
    assert page._codex_sub_group({"type": "Mount"}, "Z0", True) == "Mount"   # group-less fallback
    assert page._codex_sub_group({"type": "Mount", "group": "Aries"}, "Z0", False) == "Mount"
    assert page._codex_sub_group({"type": "Rabbit"}, "Pets", False) == "Rabbit"
    assert page._codex_sub_group({"type": "Crimson Sacristy"}, "Bosses", False) == "Crimson Sacristy"


def test_group_label_pretty_prints_camelcase():
    page = _FakeGridPage()
    assert page._codex_group_label("Aries") == "Aries"
    assert page._codex_group_label("FlyingFish") == "Flying Fish"
    assert page._codex_group_label("SparkHorse") == "Spark Horse"


@pytest.mark.parametrize("sub_tag", ["mounts", "gliders"])
def test_collection_sort_and_render_group_by_family(sub_tag):
    """Mounts/Gliders sort by species family and render one sub-header per
    family (mirroring Pets' species headers) on the Collection views."""
    page = _FakeGridPage()
    items = _sub_view_items(page, sub_tag)
    assert items
    matches = [(it["id"], it, True, True, 0) for it in items]
    page._sort_codex_matches(matches, "Z0", "ALL", "", collection_tab=True)

    groups = [_group_key(m[1], sub_tag) for m in matches]
    assert groups == sorted(groups)                      # families stay contiguous
    assert len(set(groups)) >= 5, groups

    page._render_codex_region("Z0", sub_tag.capitalize(), matches, 0,
                              False, "ALL", "Pets", True)
    labels = _rendered_header_labels(page)
    assert labels, "no sub-headers rendered"
    assert sorted(set(labels)) == sorted(labels)         # one header per family
    assert set(labels) == {page._codex_group_label(g).upper() for g in groups}
    # camelCase families read as spaced words ('FlyingFish' -> 'FLYING FISH')
    if "FlyingFish" in groups:
        assert "FLYING FISH" in labels
    # cash-shop items render under one general SHOP header, never a species
    # family ('Spark Horse') that makes a shop mount look farmable
    if "Shop" in groups:
        assert "SHOP" in labels


def test_pets_headers_use_spaced_species_labels():
    """Pets species sub-headers read as spaced words ('DEMON DOG',
    'STINK BUG') — the same labels as the group sub-menu, matching the
    Mounts/Gliders family headers."""
    page = _FakeGridPage()
    items = codex.units_by_region("Pets")
    matches = [(it["id"], it, True, True, 0) for it in items]
    page._sort_codex_matches(matches, "Pets", "ALL", "", collection_tab=True)
    page._render_codex_region("Pets", "Pets", matches, 0, False, "ALL", "Pets", True)
    labels = _rendered_header_labels(page)
    species = list(dict.fromkeys(_group_key(it, "pets") for it in items))
    assert set(labels) == {page._codex_group_label(s).upper() for s in species}
    if "DemonDog" in species:
        assert "DEMON DOG" in labels
    if "StinkBug" in species:
        assert "STINK BUG" in labels


def test_others_z0_still_buckets_by_type():
    """The Others tab keeps its Mounts/Gliders/Misc type buckets — species
    families only group the Collection Mounts/Gliders views."""
    page = _FakeGridPage()
    items = [it for it in codex.units_by_region("Z0", ingame_order=True)
             if codex.belongs_in_others(it)]
    matches = [(it["id"], it, True, False, 0) for it in items]
    page._sort_codex_matches(matches, "Z0", "ALL", "", collection_tab=False)
    types = [page._codex_type_header("Z0", m[1].get("type")) for m in matches]
    assert types == sorted(types)
    page._render_codex_region("Z0", "Other", matches, 0, False, "ALL", "Z0", False)
    labels = _rendered_header_labels(page)
    assert labels
    assert all(l in ("MOUNTS", "GLIDERS", "MISC") for l in labels)


def test_group_counts_match_rendered_families():
    """The group sub-menu's All + family buttons cover exactly the families
    the current sub-view renders (order = in-game order), with counts equal
    to the cards each family actually renders (summing to the ZONE TOTAL)."""
    page = _FakeGridPage()
    for sub_tag in ("pets", "mounts", "gliders"):
        counts = page._codex_group_counts(sub_tag)
        assert counts, sub_tag
        keys = [k for k, _c in counts]
        assert "All" not in keys
        items = (codex.units_by_region("Pets") if sub_tag == "pets"
                 else _sub_view_items(page, sub_tag))
        rendered = [_group_key(it, sub_tag) for it in items]
        assert set(keys) == set(rendered), sub_tag
        for key, count in counts:
            assert count == rendered.count(key), (sub_tag, key)
        assert sum(c for _k, c in counts) == len(items)   # sums to the total


def test_group_bar_buttons_show_family_counts():
    """The rebuilt sub-menu labels read 'Family (count)' — e.g. 'Wolf (6)' —
    and 'All (total)', keyed by the raw family so filtering still works."""
    page = _FakeGridPage()
    page._build_codex_group_sub_bar()
    page._rebuild_codex_group_bar("mounts")
    counts = dict(page._codex_group_counts("mounts"))
    assert page._group_sub_btns["All"].text() == f"All ({sum(counts.values())})"
    wolf = page._group_sub_btns.get("Wolf")
    assert wolf is not None
    assert wolf.text() == f"Wolf ({counts['Wolf']})"
    assert wolf.property("groupKey") == "Wolf"
    # cash-shop mounts use one general Shop chip instead of a species family
    # ('Spark Horse (1)' is gone — the shop horse reads as a shop item)
    assert "SparkHorse" not in page._group_sub_btns
    shop = page._group_sub_btns.get("Shop")
    assert shop is not None
    assert shop.text() == f"Shop ({counts['Shop']})"
    assert shop.property("groupKey") == "Shop"


def test_group_counts_follow_visible_hidden_status():
    """The chip counts respect the Visible/Hidden status filter: VISIBLE
    counts active cards per family, HIDDEN counts hidden ones, ALL counts
    everything. Zero-count families stay listed (chips don't reflow), and
    Nearby keeps full family sizes."""
    page = _FakeGridPage()
    all_counts = dict(page._codex_group_counts("mounts", "ALL"))
    wolf = next(it for it in _sub_view_items(page, "mounts")
                if it.get("group") == "Wolf")
    page.s = _FakeSettings(hidden_comps={wolf["id"]})

    visible = dict(page._codex_group_counts("mounts", "VISIBLE"))
    hidden = dict(page._codex_group_counts("mounts", "HIDDEN"))
    assert visible["Wolf"] == all_counts["Wolf"] - 1
    assert hidden["Wolf"] == 1
    assert visible["Aries"] == all_counts["Aries"]      # untouched family
    assert set(hidden) == set(all_counts)                  # zero-count chips stay
    assert sum(hidden.values()) == 1
    # Nearby treats the counts as ALL
    assert dict(page._codex_group_counts("mounts", "NEARBY")) == all_counts
    # pets hide through the same companion namespace
    pet = codex.units_by_region("Pets")[0]
    page.s = _FakeSettings(hidden_comps={pet["id"]})
    pets_all = dict(page._codex_group_counts("pets", "ALL"))
    pets_vis = dict(page._codex_group_counts("pets", "VISIBLE"))
    assert pets_vis[pet["type"]] == pets_all[pet["type"]] - 1


def test_group_bar_rebuilds_chip_counts_on_status_change():
    """Switching the Visible/Hidden status filter re-renders the chip counts
    (and the All total) without losing the active family selection."""
    page = _FakeGridPage()
    page._build_codex_group_sub_bar()
    page._collection_sub = "mounts"
    wolf = next(it for it in _sub_view_items(page, "mounts")
                if it.get("group") == "Wolf")
    page.s = _FakeSettings(hidden_comps={wolf["id"]})

    all_counts = dict(page._codex_group_counts("mounts", "ALL"))
    page._sync_codex_group_bar("Pets", "Collection", "ALL")
    assert page._group_sub_btns["All"].text() == f"All ({sum(all_counts.values())})"
    assert page._group_sub_btns["Wolf"].text() == f"Wolf ({all_counts['Wolf']})"

    page._sync_codex_group_bar("Pets", "Collection", "HIDDEN")
    assert page._group_sub_btns["Wolf"].text() == "Wolf (1)"
    assert page._group_sub_btns["All"].text() == "All (1)"

    # selecting a family survives the status switch
    page._codex_group_filter = "Wolf"
    page._sync_codex_group_bar("Pets", "Collection", "VISIBLE")
    assert page._codex_group_filter == "Wolf"
    assert page._group_sub_btns["Wolf"].isChecked()
    assert page._group_sub_btns["Wolf"].text() \
        == f"Wolf ({all_counts['Wolf'] - 1})"


def test_group_chip_toggles_off_when_reclicked():
    """Clicking an already-active family chip unselects it (back to All),
    so a filter can be cleared without clicking the All chip itself."""
    page = _FakeGridPage()
    page._build_codex_group_sub_bar()
    page._collection_sub = "mounts"
    page._sync_codex_group_bar("Pets", "Collection", "ALL")
    page._refresh_codex_grid = lambda **kw: None   # just record the filter
    wolf = page._group_sub_btns["Wolf"]

    wolf.click()                                    # select the family
    assert page._codex_group_filter == "Wolf"
    assert wolf.isChecked()
    assert not page._group_sub_btns["All"].isChecked()

    wolf.click()                                    # click it again: toggle off
    assert page._codex_group_filter == "All"
    assert page._group_sub_btns["All"].isChecked()
    assert not wolf.isChecked()

    # switching straight to another family still works
    page._group_sub_btns["Aries"].click()
    assert page._codex_group_filter == "Aries"
    page._group_sub_btns["All"].click()
    assert page._codex_group_filter == "All"


def _fake_status_widgets(page):
    """Minimal shared status chips (All/Nearby/Visible/Hidden), keyed like
    the real control bar (labels carry a statusKey property, not the key)."""
    page._status_btns = QtWidgets.QButtonGroup(page)
    page._status_btns.setExclusive(True)
    page._status_widgets = {}
    for lbl in ("All", "Nearby", "Visible", "Hidden"):
        btn = QtWidgets.QPushButton(lbl)
        btn.setCheckable(True)
        btn.setProperty("statusKey", lbl)
        page._status_btns.addButton(btn)
        page._status_widgets[lbl] = btn
    return page


def test_status_counts_reconcile_with_hidden_set():
    """(all, visible, hidden) totals reconcile: all == visible + hidden, and
    hiding a card moves it from visible to hidden across sub-views."""
    page = _FakeGridPage()
    total, visible, hidden = page._codex_status_counts("mounts")
    assert total == visible + hidden
    assert hidden == 0
    wolf = next(it for it in _sub_view_items(page, "mounts")
                if it.get("group") == "Wolf")
    page.s = _FakeSettings(hidden_comps={wolf["id"]})
    total2, vis2, hid2 = page._codex_status_counts("mounts")
    assert total2 == total
    assert vis2 == visible - 1
    assert hid2 == 1
    # 'all' merges the three sub-views
    merged = page._codex_status_counts("all")
    singles = [page._codex_status_counts(t) for t in ("pets", "mounts", "gliders")]
    assert merged[0] == sum(s[0] for s in singles)
    assert merged[1] == sum(s[1] for s in singles)


def test_status_chips_show_visible_hidden_counts():
    """On the Collection card-grid views the shared status chips read
    'All (total)' / 'Visible (n)' / 'Hidden (m)' (Nearby stays plain); the
    filter key still resolves through the statusKey property despite the
    count labels, and other tabs restore the plain labels."""
    page = _fake_status_widgets(_FakeGridPage())
    page._collection_sub = "mounts"
    page._build_codex_group_sub_bar()

    page._update_codex_status_chips("Pets", "Collection")
    total, visible, hidden = page._codex_status_counts("mounts")
    assert page._status_widgets["All"].text() == f"All ({total})"
    assert page._status_widgets["Visible"].text() == f"Visible ({visible})"
    assert page._status_widgets["Hidden"].text() == f"Hidden ({hidden})"
    assert page._status_widgets["Nearby"].text() == "Nearby"

    # the count-styled labels never become the filter key
    page._status_widgets["Hidden"].setChecked(True)
    _q, status = page._codex_filter_state()
    assert status == "HIDDEN"
    page._status_widgets["All"].setChecked(True)
    _q, status = page._codex_filter_state()
    assert status == "ALL"

    # hiding a card moves one unit from Visible to Hidden on the chips
    wolf = next(it for it in _sub_view_items(page, "mounts")
                if it.get("group") == "Wolf")
    page.s = _FakeSettings(hidden_comps={wolf["id"]})
    page._update_codex_status_chips("Pets", "Collection")
    assert page._status_widgets["Visible"].text() == f"Visible ({visible - 1})"
    assert page._status_widgets["Hidden"].text() == f"Hidden ({hidden + 1})"

    # Chests/Orbs lists restore the plain labels
    page._chest_orb_kind = "chests"
    page._update_codex_status_chips("Pets", "Collection")
    assert page._status_widgets["All"].text() == "All"
    assert page._status_widgets["Visible"].text() == "Visible"
    page._chest_orb_kind = None


def test_status_chips_show_counts_on_mobs_and_others_tabs():
    """The shared status chips also carry All/Visible/Hidden totals on the
    open-world zone tabs and the Others tab (counts match the region's
    rendered cards), while the Dungeons tab — where the status block is
    replaced by zone filters — restores the plain labels."""
    page = _fake_status_widgets(_FakeGridPage())
    page._cur_rid = "Z1"
    page._cur_tab = "Skover Island"

    # zone/Mobs tab: counts cover the region's cards exactly
    page._update_codex_status_chips("Z1", "Skover Island")
    total, visible, hidden = page._codex_tab_status_counts("Z1")
    assert total == len(codex.units_by_region("Z1"))
    assert page._status_widgets["All"].text() == f"All ({total})"
    assert page._status_widgets["Visible"].text() == f"Visible ({visible})"
    assert page._status_widgets["Hidden"].text() == f"Hidden ({hidden})"
    assert page._status_widgets["Nearby"].text() == "Nearby"
    assert total == visible + hidden

    # hiding a mob moves one card from Visible to Hidden on the chips
    mob = codex.units_by_region("Z1")[0]
    page.s = _FakeSettings()
    page.s.get_entity_hidden_units = lambda profile: {mob["id"]}
    page._update_codex_status_chips("Z1", "Skover Island")
    assert page._status_widgets["Visible"].text() == f"Visible ({visible - 1})"
    assert page._status_widgets["Hidden"].text() == f"Hidden ({hidden + 1})"

    # Others tab counts its own rows
    page._update_codex_status_chips("Z0", "Other")
    ot, ov, oh = page._codex_tab_status_counts("Z0")
    assert page._status_widgets["All"].text() == f"All ({ot})"
    assert page._status_widgets["Hidden"].text() == f"Hidden ({oh})"
    assert ot == ov + oh

    # Dungeons swaps in the zone filters -> plain labels restored
    page._update_codex_status_chips("Bosses", "Dungeons")
    assert page._status_widgets["All"].text() == "All"
    assert page._status_widgets["Visible"].text() == "Visible"
    assert page._status_widgets["Hidden"].text() == "Hidden"
