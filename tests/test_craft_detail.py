"""Recipe card rendering (headless Qt, offscreen platform).

_craft_render_recipe (ui/pages/craft/detail.py) builds the recipe card used
both on the Craft page and inside the Items page's In Recipes drawer. The
CRAFTED material tags and the OPEN IN GEAR / OPEN IN ENCHANTS button carry
per-widget
stylesheets — those must stay brace-balanced, or Qt logs 'Could not parse
stylesheet of object QPushButton(...)' for every button on every drawer
open (Elixir of Faith -> Minor Alchemist Cauldron, which has five crafted
materials, spammed five warnings).
"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtWidgets  # noqa: E402

from farever_companion import paths  # noqa: E402
from farever_companion import planner as pdata  # noqa: E402
from farever_companion.ui import theme  # noqa: E402
from farever_companion.data import items as idata  # noqa: E402
from farever_companion.ui.pages.craft import detail as cdetail  # noqa: E402
from farever_companion.ui.pages.craft.detail import CraftDetailMixin  # noqa: E402


def _data_present() -> bool:
    try:
        return paths.craft_path().exists()
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _data_present(),
    reason="requires bundled data (assets/data/craft.json)")


@pytest.fixture(scope="module", autouse=True)
def _qapp():
    """One offscreen QApplication for the whole module (widgets need one)."""
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture(autouse=True)
def _moddata(tmp_path, monkeypatch):
    """Queue mutations now persist (planner.json) — point FAREVER_MODDATA_DIR
    at a throwaway folder per test so nothing touches real user data, with
    a cold planner parse cache."""
    monkeypatch.setenv("FAREVER_MODDATA_DIR", str(tmp_path))
    pdata._data.cache_clear()
    yield
    pdata._data.cache_clear()


def _render_recipe(recipe_item: str):
    """Render the recipe card for `recipe_item` into a hosted widget and
    return (host, buttons) — every QPushButton with a per-widget
    stylesheet. The host must stay referenced so its children aren't
    garbage-collected mid-assert."""
    class _Dummy(CraftDetailMixin):
        _codex_jump_to_unit = staticmethod(lambda *a: None)
        _craft_refresh_scroll = staticmethod(lambda: None)

    r = idata.recipe(recipe_item)
    assert r is not None, recipe_item
    host = QtWidgets.QWidget()
    lay = QtWidgets.QVBoxLayout(host)
    _Dummy()._craft_render_recipe(lay, r,
                                  jump=lambda i: None,
                                  opener=lambda i: None)
    return host, [b for b in host.findChildren(QtWidgets.QPushButton)
                  if b.styleSheet()]


def _row_checkbox(lbl):
    """The QCheckBox in the same Total Needed row as `lbl` — each row is one
    QHBoxLayout inside the bill body's QVBoxLayout, so walk the parent's
    layout until the row holding the label is found."""
    lay = lbl.parentWidget().layout()
    for i in range(lay.count()):
        row = lay.itemAt(i).layout()
        if row is None or not any(row.itemAt(j).widget() is lbl
                                  for j in range(row.count())):
            continue
        for j in range(row.count()):
            w = row.itemAt(j).widget()
            if isinstance(w, QtWidgets.QCheckBox):
                return w
    return None


def test_recipe_card_button_stylesheets_are_balanced():
    """Every QPushButton stylesheet in a rendered recipe card has balanced
    braces — a stray '}}' (plain string segment) makes the whole sheet fail
    to parse and Qt spams 'Could not parse stylesheet' once per button."""
    for r in idata.recipes():
        host, btns = _render_recipe(r["item"])
        for b in btns:
            ss = b.styleSheet()
            assert ss.count("{") == ss.count("}"), \
                (r["item"], b.text(), ss)
        host.deleteLater()


def test_heavy_raw_material_gets_collapsible_groups():
    """A raw material with many drop sources (Strange Spores in Vessel
    Potion, 27 sources) renders the same one-line-vs-collapsible kind
    groups as the items Drops From — but farmability-first: CHEST sources
    are dropped from the craft hints (one-time RNG opens aren't a
    repeatable farm), so only the 'GATHER · 19 ▾' block remains, collapsed
    by default so the card stays compact; short-list materials (Vial,
    Veiled Wing) keep the compact '← source · spots' line instead."""
    r = idata.recipe("VesselPotion")
    host = QtWidgets.QWidget()
    lay = QtWidgets.QVBoxLayout(host)
    class _Dummy(CraftDetailMixin):
        _codex_jump_to_unit = staticmethod(lambda *a: None)
        _craft_refresh_scroll = staticmethod(lambda: None)
    _Dummy()._craft_render_recipe(lay, r,
                                  jump=lambda i: None,
                                  opener=lambda i: None)
    host.show()
    try:
        lbls = [l.text() for l in host.findChildren(QtWidgets.QLabel)
                if l.text()]
        btns = [b.text() for b in host.findChildren(QtWidgets.QPushButton)
                if b.text()]
        # the heavy material's sources are a collapsible GATHER group —
        # chests are gone from the hints
        assert not any(b.startswith("CHESTS") for b in btns), btns
        assert any(b.startswith("GATHER · 19") and b.endswith("▾")
                   for b in btns), btns
        # the short-list material (Vial) keeps the compact summary line
        inline = [t for t in lbls if t.startswith("← ")]
        assert any("Guild Merchant" in t for t in inline), inline
        # Veiled Wing is no longer boss-only: its farmable mobs render in
        # a collapsible MOBS · 7 group (the faction groups + the boss are
        # all in there, hidden until the header is clicked)
        assert any(b.startswith("MOBS · 7") and b.endswith("▾")
                   for b in btns), btns
        assert any("Nightqueen Shaarlize" in t for t in lbls), lbls
        # the collapsible group starts collapsed (names hidden) and a
        # header click reveals it
        head = next(b for b in host.findChildren(QtWidgets.QPushButton)
                    if b.text().startswith("GATHER · 19"))
        flow = head.parentWidget().layout().itemAt(1).widget()
        assert not flow.isVisible()
        head.click()
        assert flow.isVisible()
    finally:
        host.deleteLater()


def test_elixir_of_faith_drawer_buttons():
    """Elixir of Faith's In Recipes drawer (Minor Alchemist Cauldron) has
    five crafted-material CRAFTED tags — the exact set that previously
    spammed five parse warnings — all balanced. The output is a non-gear
    craft item whose full page IS this card (craft items live on the
    Craft page's flat list), so it carries no OPEN IN GEAR button;
    crafted GEAR keeps it (its full page is the Gear tab)."""
    uses = idata.recipes_using("ElixirOfFaith_Z2")
    assert [u["item"] for u in uses] == ["SmallAlchemistCauldron"]
    _host, btns = _render_recipe("SmallAlchemistCauldron")
    crafted = [b for b in btns if b.text() == "CRAFTED"]
    assert len(crafted) == 5          # Simple Cauldron is the only raw input
    assert not any(b.text().startswith("OPEN IN") for b in btns)
    assert all(b.styleSheet().count("{") == b.styleSheet().count("}")
               for b in btns)
    # crafted gear keeps the jump to its full page — the button names the
    # tab it opens (Gear), not the removed Items tab
    gear_r = next(r for r in idata.recipes() if r.get("gear"))
    _ghost, gbtns = _render_recipe(gear_r["item"])
    assert any(b.text().startswith("OPEN IN GEAR") for b in gbtns)
    # enchant outputs (scrolls / gems) label the Enchants tab instead
    er = idata.recipe("AttunedCutBeryl")
    assert er is not None
    _ehost, ebtns = _render_recipe("AttunedCutBeryl")
    assert any(b.text().startswith("OPEN IN ENCHANTS") for b in ebtns)


def test_recipe_card_quantity_stepper_scales_batch():
    """Craft-page card (qty_selector=True): the CRAFT × stepper scales the
    materials in place and keeps the Total Needed shopping list — updating
    in place, never rebuilding the card, so the stepper survives between
    clicks."""
    from farever_companion.ui import components as C

    class _Dummy(CraftDetailMixin):
        _codex_jump_to_unit = staticmethod(lambda *a: None)
        _craft_refresh_scroll = staticmethod(lambda: None)

    def labels(host):
        return [l.text() for l in host.findChildren(QtWidgets.QLabel)
                if l.text()]

    r = idata.recipe("SmallAlchemistCauldron")
    assert r is not None
    dummy = _Dummy()                 # keep referenced — the stepper's
    host = QtWidgets.QWidget()       # valueChanged bound method holds it
    lay = QtWidgets.QVBoxLayout(host)
    dummy._craft_render_recipe(lay, r,
                               jump=lambda i: None,
                               opener=lambda i: None,
                               qty_selector=True, qty=1)
    host.show()
    try:
        steps = host.findChildren(C.Stepper)
        assert len(steps) == 1
        lbls = labels(host)
        assert any("CRAFT ×" in t for t in lbls)
        assert any("TOTAL NEEDED" in t for t in lbls)
        # per-craft counts at qty=1: 1 Simple Cauldron, 4 Vials deduped in
        # the shopping list (one per elixir)
        assert any(t == "1×  Simple Cauldron" for t in lbls)
        assert any(t == "4×  Vial" for t in lbls)
        assert any(t == "BATCH · 50 GOLD" for t in lbls)
        # bump the batch: everything scales in place
        steps[0].setValue(10)
        lbls = labels(host)
        assert any(t == "10×  Simple Cauldron" for t in lbls)
        assert any(t == "40×  Vial" for t in lbls)
        assert any(t == "BATCH · 500 GOLD" for t in lbls)
        assert any(t == "18 ITEMS · 590 RAW" for t in lbls)
        assert any(t == "6 ITEMS · ×10" for t in lbls)   # materials header
    finally:
        host.deleteLater()


def test_queue_rail_sums_across_recipes():
    """The docked queue rail (mockup C): each added recipe renders as a
    line item above the summed Total Needed bill; qty edits, removals and
    clears rebuild the rail, and clearing hides it."""
    from farever_companion.ui import components as C

    class _Dummy(CraftDetailMixin):
        _codex_jump_to_unit = staticmethod(lambda *a: None)
        _craft_refresh_scroll = staticmethod(lambda: None)

    def labels(host):
        return [l.text() for l in host.findChildren(QtWidgets.QLabel)
                if l.text()]

    dummy = _Dummy()
    host = QtWidgets.QWidget()
    dummy._craft_rail = QtWidgets.QFrame()
    dummy._craft_rail_lay = QtWidgets.QVBoxLayout(host)
    dummy._craft_queue = [
        {"item": "SmallAlchemistCauldron", "qty": 10,
         "name": "Minor Alchemist Cauldron"},
        {"item": "BronzeIngot", "qty": 5, "name": "Bronze Ingot"},
    ]
    dummy._craft_rail_build()
    try:
        lbls = labels(host)
        # no "Craft List · N RECIPES" header — the count lives on the tab
        assert not any(t.endswith("RECIPES") for t in lbls)
        assert any("TOTAL NEEDED" in t for t in lbls)
        assert any(t == "40×  Vial" for t in lbls)          # cauldron only
        assert any(t == "25×  Tin Ore" for t in lbls)       # bronze only
        assert any(t == "60×  Spark Sample" for t in lbls)  # summed
        assert any(t == "TOTAL GOLD · 500" for t in lbls)
        assert len(host.findChildren(C.Stepper)) == 2        # one per entry
        # each row's remove control is a trash-bin icon (not a ✕ glyph)
        rms = [b for b in host.findChildren(QtWidgets.QPushButton)
               if b.objectName() == "TrashButton"]
        assert len(rms) == 2
        assert all(not b.icon().isNull() for b in rms)
        # CLEAR carries the same trash-bin icon
        clear = next(b for b in host.findChildren(QtWidgets.QPushButton)
                     if b.text() == "CLEAR")
        assert not clear.icon().isNull()
        # a qty edit rescales the summed bill in place
        dummy._craft_queue_set_qty(0, 15)
        lbls = labels(host)
        assert any(t == "60×  Vial" for t in lbls)          # 15 × 4 vials
        assert any(t == "TOTAL GOLD · 750" for t in lbls)   # 15 × 50g
        # removing an entry drops its materials from the sum
        dummy._craft_queue_remove(1)
        lbls = labels(host)
        assert not any(t.endswith("RECIPE") for t in lbls)
        # the bronze-only bill row is gone (its farmable hints may still
        # name Tin Ore nodes under OTHER rows' GATHER groups, so match the
        # row label itself)
        assert not any(t.startswith("25×  Tin Ore") for t in lbls)
        # clearing empties the queue and hides the rail
        dummy._craft_queue_clear()
        assert not dummy._craft_rail.isVisible()
    finally:
        host.deleteLater()


def test_add_to_queue_button_merges_quantities():
    """The recipe card's ADD TO QUEUE pushes the current CRAFT × batch into
    the queue; re-adding the same recipe merges (sums) quantities, and the
    header badge counts entries."""
    from farever_companion.ui import components as C

    class _Dummy(CraftDetailMixin):
        _codex_jump_to_unit = staticmethod(lambda *a: None)
        _craft_refresh_scroll = staticmethod(lambda: None)

    dummy = _Dummy()
    host = QtWidgets.QWidget()
    dummy._craft_detail_lay = QtWidgets.QVBoxLayout(host)  # mutations re-render
    dummy._craft_queue = []
    dummy._craft_tabs = C.SegmentedControl(["Recipes", "Craft List"])
    dummy._craft_queue_badge()
    dummy._craft_qty = 10
    dummy._craft_queue_add("SmallAlchemistCauldron")
    dummy._craft_queue_add("SmallAlchemistCauldron")
    dummy._craft_qty = 5               # each add uses the current CRAFT × value
    dummy._craft_queue_add("BronzeIngot")
    assert [(e["item"], e["qty"]) for e in dummy._craft_queue] == \
        [("SmallAlchemistCauldron", 20), ("BronzeIngot", 5)]
    assert dummy._craft_queue[0]["name"] == "Minor Alchemist Cauldron"
    # the Craft List tab carries the queue count in the tab's own color
    tab_text = dummy._craft_tabs._btns["Craft List"].text()
    assert "· 2" in tab_text
    dummy._craft_queue_clear()
    dummy._craft_queue_badge()
    # an empty queue hides the count — the tab reads plain Craft List
    assert dummy._craft_tabs._btns["Craft List"].text() == "Craft List"


def test_drawer_uses_quantity_card():
    """The items-page In Recipes drawer (qty_selector=True) gets the same
    queue card as the craft page: CRAFT × stepper, batch-scaled
    materials and the Total Needed bill (no Supply Chain tree) —    batch math works wherever a recipe appears. The stepper updates in place and the
    ADD TO QUEUE button pushes into the shared queue."""
    from farever_companion.ui import components as C

    class _Dummy(CraftDetailMixin):
        _codex_jump_to_unit = staticmethod(lambda *a: None)
        _craft_refresh_scroll = staticmethod(lambda: None)

    def labels(host):
        return [l.text() for l in host.findChildren(QtWidgets.QLabel)
                if l.text()]

    r = idata.recipe("SmallAlchemistCauldron")
    assert r is not None
    dummy = _Dummy()
    dummy._craft_queue = []
    dummy._craft_qty = 10
    host = QtWidgets.QWidget()
    lay = QtWidgets.QVBoxLayout(host)
    dummy._craft_render_recipe(lay, r, jump=lambda i: None,
                               opener=lambda i: None,
                               qty_selector=True, qty=10)
    host.show()
    try:
        assert len(host.findChildren(C.Stepper)) == 1
        lbls = labels(host)
        assert any("TOTAL NEEDED" in t for t in lbls)
        assert any("CRAFT ×" in t for t in lbls)
        assert not any("SUPPLY CHAIN" in t for t in lbls)
        # the shared batch count is what the drawer starts from
        assert dummy._craft_qty == 10
        # ADD TO QUEUE on the drawer card pushes into the same queue
        adds = [b for b in host.findChildren(QtWidgets.QPushButton)
                if "QUEUE" in b.text().upper()]
        assert adds, "drawer card has an ADD TO QUEUE button"
        adds[0].click()
        assert dummy._craft_queue == [
            {"item": "SmallAlchemistCauldron", "qty": 10,
             "name": "Minor Alchemist Cauldron"}]
        # the stepper updates this card's own bill in place
        steps = host.findChildren(C.Stepper)
        steps[0].setValue(2)
        lbls = labels(host)
        assert any(t == "8×  Vial" for t in lbls)          # 2 × 4 vials
        assert any(t == "BATCH · 100 GOLD" for t in lbls)  # 2 × 50g
        # the drawer's Total Needed header carries the COPY export button
        assert any(b.text() == "COPY"
                   for b in host.findChildren(QtWidgets.QPushButton))
    finally:
        host.deleteLater()


def test_queue_total_needed_check_off():
    """Queue rail Total Needed rows are checkable: ticking a material
    strikes it through (rich-text line-through) and switches the section
    tag to the still-missing summary ('K DONE · R RAW LEFT'); unticking
    restores it, and the gathered state survives rail rebuilds (qty edits
    re-render, re-deriving each checkbox from the shared got-state)."""
    from farever_companion.ui import components as C

    class _Dummy(CraftDetailMixin):
        _codex_jump_to_unit = staticmethod(lambda *a: None)
        _craft_refresh_scroll = staticmethod(lambda: None)

    def labels(host):
        # skip hidden labels: the ✓ badge is hidden (not text-cleared)
        # once a row un-gathers
        return [l.text() for l in host.findChildren(QtWidgets.QLabel)
                if l.text() and not l.isHidden()]

    dummy = _Dummy()
    host = QtWidgets.QWidget()
    dummy._craft_rail = QtWidgets.QFrame()
    dummy._craft_rail_lay = QtWidgets.QVBoxLayout(host)
    dummy._craft_queue = [
        {"item": "SmallAlchemistCauldron", "qty": 10,
         "name": "Minor Alchemist Cauldron"},
    ]
    dummy._craft_rail_build()
    host.show()
    try:
        # one checkbox per Total Needed row (18 materials for 10 cauldrons)
        boxes = host.findChildren(QtWidgets.QCheckBox)
        assert len(boxes) == 18, len(boxes)
        assert any(t == "18 ITEMS · 590 RAW" for t in labels(host))
        # tick Vial (the 40-count gathered material): struck through + tag
        vial = next(l for l in host.findChildren(QtWidgets.QLabel)
                    if l.text() == "40×  Vial")
        vbox = _row_checkbox(vial)
        assert vbox is not None
        vbox.click()      # click(), not setChecked(): the flip is wired to
                          # the clicked signal (setChecked only toggles)
        lbls = labels(host)
        assert any("line-through" in t and "40×  Vial" in t
                   for t in lbls), lbls
        assert any(t == "1 DONE · 550 RAW LEFT" for t in lbls), lbls
        assert dummy._craft_got.get("Vial") == 40
        # the gathered material also carries the explicit green ✓ badge
        assert any(t == "✓" for t in lbls), lbls
        # unticking restores the full-raw tag and drops the ✓ badge
        vbox.click()
        assert any(t == "18 ITEMS · 590 RAW" for t in labels(host))
        assert not any(t == "✓" for t in labels(host))
        # gathered state survives a rebuild: tick again, then a qty edit
        vbox.click()
        dummy._craft_queue_set_qty(0, 15)
        boxes = host.findChildren(QtWidgets.QCheckBox)
        assert len(boxes) == 18, len(boxes)
        # rows show what's still needed once gathering starts: Vial reads
        # 20× (60 needed − 40 gathered)
        vial = next(l for l in host.findChildren(QtWidgets.QLabel)
                    if l.text().startswith("20×") and "Vial" in l.text())
        # 40 gathered < 60 now needed, so the row came back unchecked
        assert not _row_checkbox(vial).isChecked()
        assert any(t == "18 ITEMS · 885 RAW" for t in labels(host))
        _row_checkbox(vial).click()
        assert any(t == "1 DONE · 825 RAW LEFT" for t in labels(host))
    finally:
        host.deleteLater()


def test_queue_bill_rows_show_farmable_source_hints():
    """The Craft List queue's Total Needed bill rows carry the same
    farmable drop hints as the recipe card's Materials list — CHEST sources
    are dropped (one-time RNG opens aren't a repeatable farm), GATHER
    groups lead, then MOBS; short-list materials keep the compact
    '← source · spots' line; crafted intermediates get no hint (their
    CRAFTED tag is the drill-down)."""
    class _Dummy(CraftDetailMixin):
        _codex_jump_to_unit = staticmethod(lambda *a: None)
        _craft_refresh_scroll = staticmethod(lambda: None)

    dummy = _Dummy()
    host = QtWidgets.QWidget()
    bl = QtWidgets.QVBoxLayout(host)
    bill = idata.craft_bill("BronzeIngot", 1)
    dummy._craft_fill_bill(bl, bill, lambda i: None,
                           checkable=True, source_hints=True)
    host.show()
    try:
        for _ in range(4):
            QtWidgets.QApplication.processEvents()
        btns = [b.text() for b in host.findChildren(QtWidgets.QPushButton)
                if b.text()]
        # no chest sources anywhere in the hints
        assert not any(b.startswith("CHESTS") for b in btns), btns
        # collapsible kind groups lead with GATHER (SparkSample's 19
        # gatherables render first)
        groups = [b for b in btns if b.endswith("▾")]
        assert groups and groups[0].startswith("GATHER"), groups
        # both TinOre kind groups are collapsed toggle headers — GATHER
        # and MOBS names stay hidden until clicked; only the compact
        # single-source '← source · spots' lines read inline
        lbls = [l.text() for l in host.findChildren(QtWidgets.QLabel)
                if l.text()]
        assert not any(t.startswith("GATHER · 2") for t in lbls), lbls
        assert not any(t.startswith("MOBS · 6") for t in lbls), lbls
        gather = next(b for b in btns if b.startswith("GATHER · 2"))
        mobs = next(b for b in btns if b.startswith("MOBS · 6"))
        assert gather.endswith("▾") and mobs.endswith("▾"), (gather, mobs)
        # the kind headers sit on the SAME line — 'GATHER · 2 ▾  MOBS · 6 ▾'
        # flows as one compact hint line instead of one line each
        gw = next(b for b in host.findChildren(QtWidgets.QPushButton)
                  if b.text().startswith("GATHER · 2"))
        mw = next(b for b in host.findChildren(QtWidgets.QPushButton)
                  if b.text().startswith("MOBS · 6"))
        gpos = gw.parentWidget().pos()
        mpos = mw.parentWidget().pos()
        assert gpos.y() == mpos.y(), (gpos.y(), mpos.y())
        assert gpos.x() < mpos.x(), (gpos.x(), mpos.x())
        # GATHER still leads MOBS within the hint (widget creation order)
        ws = host.findChildren(QtWidgets.QWidget)
        gi = next(i for i, w in enumerate(ws)
                  if isinstance(w, QtWidgets.QPushButton)
                  and w.text().startswith("GATHER · 2"))
        mi = next(i for i, w in enumerate(ws)
                  if isinstance(w, QtWidgets.QPushButton)
                  and w.text().startswith("MOBS · 6"))
        assert gi < mi, (gi, mi)
        # the crafted intermediate (CopperIngot) gets the CRAFTED pill,
        # not a source hint
        assert any(b == "CRAFTED" for b in btns), btns
    finally:
        host.deleteLater()

    # a short-list raw material keeps the compact '← source · spots' line
    host2 = QtWidgets.QWidget()
    bl2 = QtWidgets.QVBoxLayout(host2)
    dummy._craft_fill_bill(bl2, idata.craft_bill("VesselPotion", 1),
                           lambda i: None, checkable=True,
                           source_hints=True)
    host2.show()
    try:
        lbls = [l.text() for l in host2.findChildren(QtWidgets.QLabel)
                if l.text()]
        inline = [t for t in lbls if t.startswith("← ")]
        assert any("Guild Merchant" in t for t in inline), inline
        # Veiled Wing's hint is the full farmable list now — the boss is
        # no longer the only row
        assert any("Nightqueen Shaarlize" in t for t in lbls), lbls
        assert any("Demon mobs" in t for t in lbls), lbls
        btns = [b.text() for b in host2.findChildren(QtWidgets.QPushButton)
                if b.text()]
        assert not any(b.startswith("CHESTS") for b in btns), btns
    finally:
        host2.deleteLater()


def test_copy_button_exports_total_needed():
    """The recipe card's Total Needed header has a COPY button that puts
    the plain-text export on the clipboard — the batch title, the item/raw
    tag, one `N×  name` line per material, and the total gold. The export
    reads the LIVE batch state, so a later stepper change is reflected in
    a fresh copy, and the button flashes ✓ COPIED."""
    from farever_companion.ui import components as C

    class _Dummy(CraftDetailMixin):
        _codex_jump_to_unit = staticmethod(lambda *a: None)
        _craft_refresh_scroll = staticmethod(lambda: None)

    dummy = _Dummy()
    dummy._craft_qty = 10
    host = QtWidgets.QWidget()
    lay = QtWidgets.QVBoxLayout(host)
    r = idata.recipe("SmallAlchemistCauldron")
    assert r is not None
    dummy._craft_render_recipe(lay, r, jump=lambda i: None,
                               opener=lambda i: None,
                               qty_selector=True, qty=10)
    host.show()
    try:
        copy = next(b for b in host.findChildren(QtWidgets.QPushButton)
                    if b.text() == "COPY")
        copy.click()
        clip = QtWidgets.QApplication.clipboard().text()
        assert "**10×  Minor Alchemist Cauldron**" in clip   # bold header
        assert "18 ITEMS · 590 RAW" in clip
        assert "- 40×  Vial" in clip                        # bullet line
        assert "90×  Strange Spores" in clip
        assert "TOTAL GOLD · 500" in clip
        non_empty = [ln for ln in clip.splitlines() if ln]
        assert non_empty[-1].startswith("Exported ")       # timestamp
        assert copy.text() == "✓ COPIED"
        # a later stepper change shows up in a fresh copy (live state)
        host.findChildren(C.Stepper)[0].setValue(15)
        copy.click()
        clip = QtWidgets.QApplication.clipboard().text()
        assert "15×  Minor Alchemist Cauldron" in clip
        assert "60×  Vial" in clip
        assert "885 RAW" in clip
    finally:
        host.deleteLater()


def test_tile_grid_badges_queued_recipes():
    """Tiles for recipes already in the queue carry the gold QUEUED badge;
    the badge follows the queue live — adding a recipe badges its tile,
    removing it (or clearing) clears it in place."""
    from farever_companion.ui.pages.craft import CraftPageMixin

    class _Dummy(CraftPageMixin):
        def _page_container(self, title=None):
            page = QtWidgets.QWidget()
            v = QtWidgets.QVBoxLayout(page)
            v.setContentsMargins(22, 20, 22, 20)
            v.setSpacing(16)
            return page, v

    d = _Dummy()
    page = d._page_craft()
    win = QtWidgets.QMainWindow()
    win.setCentralWidget(page)
    win.resize(1100, 700)
    win.show()
    try:
        for _ in range(6):
            QtWidgets.QApplication.processEvents()
        rows = [d._craft_list.item(i) for i in range(d._craft_list.count())]

        def tile(item_id):
            return next(li for li in rows
                        if li.data(cdetail.ID_ROLE) == item_id)

        # nothing queued yet — no badges on any tile
        assert not any(li.data(cdetail.QUEUED_ROLE)
                       for li in rows if li.data(cdetail.DATA_ROLE))
        # adding recipes badges their tiles; the rest stay clean
        d._craft_qty = 1
        d._craft_queue_add("SmallAlchemistCauldron")
        d._craft_queue_add("BronzeIngot")
        for _ in range(6):
            QtWidgets.QApplication.processEvents()
        assert tile("SmallAlchemistCauldron").data(cdetail.QUEUED_ROLE)
        assert tile("BronzeIngot").data(cdetail.QUEUED_ROLE)
        assert not tile("Cook_13").data(cdetail.QUEUED_ROLE)
        # no hover text on the craft page — tiles carry no tooltip either
        assert tile("BronzeIngot").toolTip() == ""
        # removing one entry clears only its tile's badge
        d._craft_queue_remove(0)          # SmallAlchemistCauldron is first
        for _ in range(6):
            QtWidgets.QApplication.processEvents()
        assert not tile("SmallAlchemistCauldron").data(cdetail.QUEUED_ROLE)
        assert tile("BronzeIngot").data(cdetail.QUEUED_ROLE)
        # clearing clears every badge
        d._craft_queue_clear()
        for _ in range(6):
            QtWidgets.QApplication.processEvents()
        assert not any(li.data(cdetail.QUEUED_ROLE)
                       for li in rows if li.data(cdetail.DATA_ROLE))
    finally:
        win.close()


def test_queue_copy_button_exports_merged_bill():
    """The queue rail's Total Needed header COPY exports the merged bill —
    a CRAFTING QUEUE header over one line per queue entry, then the deduped
    material list (shared materials summed) and the total gold across
    every recipe."""
    class _Dummy(CraftDetailMixin):
        _codex_jump_to_unit = staticmethod(lambda *a: None)
        _craft_refresh_scroll = staticmethod(lambda: None)

    dummy = _Dummy()
    host = QtWidgets.QWidget()
    dummy._craft_rail = QtWidgets.QFrame()
    dummy._craft_rail_lay = QtWidgets.QVBoxLayout(host)
    dummy._craft_queue = [
        {"item": "SmallAlchemistCauldron", "qty": 10,
         "name": "Minor Alchemist Cauldron"},
        {"item": "BronzeIngot", "qty": 5, "name": "Bronze Ingot"},
    ]
    dummy._craft_rail_build()
    host.show()
    try:
        copy = next(b for b in host.findChildren(QtWidgets.QPushButton)
                    if b.text() == "COPY")
        copy.click()
        clip = QtWidgets.QApplication.clipboard().text()
        lines = clip.splitlines()
        assert lines[0] == "**CRAFTING QUEUE**"      # bold markdown header
        assert lines[1] == ""
        assert "- 10×  Minor Alchemist Cauldron" in clip
        assert "- 5×  Bronze Ingot" in clip
        assert "- 40×  Vial" in clip           # cauldron-only material
        assert "- 60×  Spark Sample" in clip   # merged across both recipes
        assert "TOTAL GOLD · 500" in clip
        non_empty = [ln for ln in clip.splitlines() if ln]
        assert non_empty[-1].startswith("Exported ")   # timestamp
    finally:
        host.deleteLater()


def test_recipe_card_total_needed_check_off_shares_state():
    """The recipe card's Total Needed list is checkable too, and its ticks
    share the queue's gathered state — checking Vial on the card marks it
    gathered in the queue's still-missing tally."""
    from farever_companion.ui import components as C

    class _Dummy(CraftDetailMixin):
        _codex_jump_to_unit = staticmethod(lambda *a: None)
        _craft_refresh_scroll = staticmethod(lambda: None)

    dummy = _Dummy()
    host = QtWidgets.QWidget()
    lay = QtWidgets.QVBoxLayout(host)
    r = idata.recipe("SmallAlchemistCauldron")
    assert r is not None
    dummy._craft_render_recipe(lay, r, jump=lambda i: None,
                               opener=lambda i: None,
                               qty_selector=True, qty=10)
    host.show()
    try:
        boxes = host.findChildren(QtWidgets.QCheckBox)
        assert len(boxes) == 18, len(boxes)   # the card's Total Needed too
        vial = next(l for l in host.findChildren(QtWidgets.QLabel)
                    if l.text() == "40×  Vial")
        _row_checkbox(vial).click()
        assert dummy._craft_got.get("Vial") == 40
        lbls = [l.text() for l in host.findChildren(QtWidgets.QLabel)
                if l.text()]
        assert any(t == "1 DONE · 550 RAW LEFT" for t in lbls), lbls
        # the batch stepper refills the bill and keeps the gathered row
        # (gathered rows are rich text, so match the label by substring)
        step = host.findChildren(C.Stepper)[0]
        step.setValue(10)                     # same qty: still gathered
        vial = next(l for l in host.findChildren(QtWidgets.QLabel)
                    if "40×  Vial" in l.text())
        assert _row_checkbox(vial).isChecked()
        step.setValue(15)                     # need 60 > gathered 40
        # bill rows show what's STILL needed once gathering starts, so the
        # Vial row reads 20× (60 needed − 40 gathered), unchecked
        vial = next(l for l in host.findChildren(QtWidgets.QLabel)
                    if l.text().startswith("20×") and "Vial" in l.text())
        assert not _row_checkbox(vial).isChecked()
    finally:
        host.deleteLater()


def test_recipe_card_labels_never_squash_wrapped_text():
    """Recipe cards use WrapLabel for the wrapping text (output name, meta,
    material names, drop lines, supply chain, unlock/loot lines), so on a
    narrow pane no label ends up shorter than its own wrapped height — the
    same squeeze that hit the Items page Drops From rows."""
    from PySide6 import QtCore

    class _Dummy(CraftDetailMixin):
        _codex_jump_to_unit = staticmethod(lambda *a: None)
        _craft_refresh_scroll = staticmethod(lambda: None)

    def clipped(viewport_w: int, recipe_item: str) -> list[str]:
        r = idata.recipe(recipe_item)
        assert r is not None, recipe_item
        host = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(host)
        _Dummy()._craft_render_recipe(lay, r,
                                      jump=lambda i: None,
                                      opener=lambda i: None)
        host.resize(viewport_w, 700)
        host.show()
        try:
            for _ in range(12):
                QtCore.QCoreApplication.processEvents()
            out = []
            for lbl in host.findChildren(QtWidgets.QLabel):
                if lbl.isHidden() or not lbl.text():
                    continue
                need = lbl.heightForWidth(max(1, lbl.width()))
                if lbl.height() < need:
                    out.append(lbl.text())
            return out
        finally:
            host.close()

    # the Elixir-of-Faith drawer recipe (6 materials, 5 crafted), a deep
    # supply chain, a scroll-unlocked plate, and a dungeon-set piece — all
    # with long names/locations that wrap on narrow panes
    for item in ("SmallAlchemistCauldron", "ReinforcedCopperPlate",
                 "HonedCopperPlate", "BronzeIngot"):
        for w in (360, 420, 520):
            assert clipped(w, item) == [], (item, w)


def test_queue_page_splits_into_two_columns():
    """The Craft List tab renders TWO side-by-side columns: the line items
    (steppers) on the LEFT, the summed checkable Total Needed bill rows on
    the RIGHT — every checkbox sits to the right of every stepper, with
    the total gold + CLEAR under the entries and the trash controls
    present."""
    from PySide6 import QtCore
    from farever_companion.ui import components as C

    class _Dummy(CraftDetailMixin):
        _codex_jump_to_unit = staticmethod(lambda *a: None)
        _craft_refresh_scroll = staticmethod(lambda: None)

    dummy = _Dummy()
    host = QtWidgets.QWidget()
    dummy._craft_rail = QtWidgets.QFrame()
    dummy._craft_rail_lay = QtWidgets.QVBoxLayout(host)
    dummy._craft_tabs = C.SegmentedControl(["Recipes", "Craft List"])
    dummy._craft_queue = [
        {"item": "SmallAlchemistCauldron", "qty": 10,
         "name": "Minor Alchemist Cauldron"},
        {"item": "BronzeIngot", "qty": 5, "name": "Bronze Ingot"},
    ]
    dummy._craft_rail_build()
    host.resize(900, 700)
    host.show()
    try:
        for _ in range(8):
            QtCore.QCoreApplication.processEvents()
        steps = host.findChildren(C.Stepper)
        boxes = host.findChildren(QtWidgets.QCheckBox)
        assert steps and boxes
        # two columns: every bill checkbox sits RIGHT of every stepper
        xs = [s.mapTo(host, QtCore.QPoint(0, 0)).x() for s in steps]
        bxs = [b.mapTo(host, QtCore.QPoint(0, 0)).x() for b in boxes]
        assert min(bxs) > max(xs), (max(xs), min(bxs))
        # each entry row: item icon on the LEFT, then the name, then the
        # stepper (the stepper is right of its row's name, not leading it)
        icons = [l for l in host.findChildren(QtWidgets.QLabel)
                 if l.pixmap() and not l.pixmap().isNull()]
        assert len(icons) == 2, len(icons)     # one icon tile per entry
        ix = [i.mapTo(host, QtCore.QPoint(0, 0)).x() for i in icons]
        assert max(ix) < min(xs), (ix, xs)     # icons left of every stepper
        # one trash control per entry + the CLEAR action with the same icon
        rms = [b for b in host.findChildren(QtWidgets.QPushButton)
               if b.objectName() == "TrashButton"]
        assert len(rms) == 2
        clear = next(b for b in host.findChildren(QtWidgets.QPushButton)
                     if b.text() == "CLEAR")
        assert not clear.icon().isNull()
        # no header count tag in the rail; the summed bill is present and
        # the count lives on the Craft List tab
        texts = [l.text() for l in host.findChildren(QtWidgets.QLabel)]
        assert any("TOTAL GOLD" in t for t in texts), texts
        assert not any(t.strip().endswith("RECIPES") for t in texts)
        assert "· 2" in dummy._craft_tabs._btns["Craft List"].text()
    finally:
        host.close()


def test_queue_is_own_tab_and_job_chips_filter():
    """Craft v2: two tabs — Recipes (the browse) and Craft List (the
    queue's own full-width page, not a cramped docked rail). The job-chip
    row filters the grid (the five crafting professions in the user's
    order, no ALL chip — the un-checked state is every recipe, and
    clicking the active chip returns to ALL); the header CRAFT LIST · N
    pill jumps to the Craft List tab; adding a recipe fills the queue
    page, clearing empties it back to the empty-state card."""
    from farever_companion.ui.pages.craft import CraftPageMixin

    class _Dummy(CraftPageMixin):
        def _page_container(self, title=None):
            page = QtWidgets.QWidget()
            v = QtWidgets.QVBoxLayout(page)
            v.setContentsMargins(22, 20, 22, 20)
            v.setSpacing(16)
            return page, v

    d = _Dummy()
    page = d._page_craft()
    win = QtWidgets.QMainWindow()
    win.setCentralWidget(page)
    win.resize(1100, 700)
    win.show()
    try:
        for _ in range(6):
            QtWidgets.QApplication.processEvents()
        # the chip row: the five crafting professions in the user's order —
        # no ALL chip (the un-checked state IS every recipe) and no
        # Enchanter (enchanting lives on the Items page's Enchants tab);
        # the gathering professions (Mining, Herbalism, Fishing, Archeology)
        # are open skills / not in the game yet, so they're hidden too
        assert list(d._craft_chip_btns) == \
            ["Blacksmith", "Outfitter", "Jeweller", "Alchemist", "Cook"]
        assert not (set(d._craft_chip_btns) & {"Mining", "Herbalism",
                                               "Fishing", "Archeology"})
        # Blacksmith is the default filter on open (the user's first chip)
        assert d._craft_chip_btns["Blacksmith"].isChecked()
        assert d._craft_job == "Blacksmith"
        # every recipe tile is in the list; the browse view is flat — the
        # per-job group bars exist but stay hidden until a search runs
        rows = [d._craft_list.item(i) for i in range(d._craft_list.count())]
        assert sum(1 for li in rows if li.data(cdetail.DATA_ROLE)) == 190
        assert any(li.data(cdetail.HEADER_ROLE) for li in rows)
        # only the default job's recipes are visible
        vis = [li for li in rows if not li.isHidden()
               and li.data(cdetail.DATA_ROLE)]
        assert len(vis) == 22
        assert all(li.data(cdetail.DATA_ROLE)["job"] == "Blacksmith"
                   for li in vis)
        vis_headers = [li for li in rows
                       if li.data(cdetail.HEADER_ROLE) and not li.isHidden()]
        assert vis_headers == []
        # clicking the active chip returns to ALL (no ALL chip)
        d._craft_chip_btns["Blacksmith"].click()
        for _ in range(6):
            QtWidgets.QApplication.processEvents()
        vis = [li for li in rows if not li.isHidden()
               and li.data(cdetail.DATA_ROLE)]
        assert len(vis) == 190
        assert d._craft_job == ""
        assert not any(c.isChecked() for c in d._craft_chip_btns.values())
        # clicking the chip again re-applies the Blacksmith filter
        d._craft_chip_btns["Blacksmith"].click()
        for _ in range(6):
            QtWidgets.QApplication.processEvents()
        vis = [li for li in rows if not li.isHidden()
               and li.data(cdetail.DATA_ROLE)]
        assert len(vis) == 22
        assert d._craft_job == "Blacksmith"
        # back to ALL for the per-job sort + header-order checks below
        d._craft_chip_btns["Blacksmith"].click()
        for _ in range(6):
            QtWidgets.QApplication.processEvents()
        vis = [li for li in rows if not li.isHidden()
               and li.data(cdetail.DATA_ROLE)]
        assert len(vis) == 190
        # no level chips — the grid auto-sorts highest level first within
        # each job (the data layer sorts), so a job filter keeps that order
        assert not hasattr(d, "_craft_level_btns")
        # the every-recipe view is still highest level first per job, with
        # the grid sections in the chip order (Enchanter trailing)
        per_job = {}
        for li in vis:
            r = li.data(cdetail.DATA_ROLE)
            per_job.setdefault(r["job"], []).append(r["level"])
        for job, lvls in per_job.items():
            assert lvls == sorted(lvls, reverse=True), job
        headers = [li for li in rows if li.data(cdetail.HEADER_ROLE)]
        assert [li.data(cdetail.NAME_ROLE) for li in headers] == \
            ["Blacksmith", "Outfitter", "Jeweler", "Alchemist",
             "Cook", "Enchanter"]
        # the queue is its own tab: Recipes by default; the Craft List tab
        # shows the empty-state card while nothing is queued (explicit hide
        # state — the rail/empty live on the non-current stack page, so
        # isVisible() is False regardless; isHidden() is the real signal)
        assert d._craft_stack.currentIndex() == 0
        assert not d._craft_rail_empty.isHidden()
        assert d._craft_rail.isHidden()
        # adding a recipe fills the queue page; the tab carries the count
        d._craft_qty = 1
        d._craft_queue_add("SmallAlchemistCauldron")
        assert not d._craft_rail.isHidden()
        assert d._craft_rail_empty.isHidden()
        assert "· 1" in d._craft_tabs._btns["Craft List"].text()
        d._craft_tabs.setCurrentText("Craft List")
        d._craft_tab_changed("Craft List")
        assert d._craft_stack.currentIndex() == 1
        # the tab bar returns to the browse; the grid + selection survive
        d._craft_tabs.setCurrentText("Recipes")
        d._craft_tab_changed("Recipes")
        assert d._craft_stack.currentIndex() == 0
        assert d._craft_list.isVisible()
        # clearing empties the queue back to the empty-state card
        d._craft_queue_clear()
        assert d._craft_rail.isHidden()
        assert not d._craft_rail_empty.isHidden()
    finally:
        win.close()


def test_recipe_list_is_the_default_view():
    """The Recipes browse is a compact full-width flat list — every
    recipe in a single stream, small icons, no section bars (the per-job
    group split appears only during a search). The grid view and its
    toggle buttons are gone (the list was made the default, then the grid
    was removed), so the job filter is the only browse control."""
    from farever_companion.ui.pages.craft import CraftPageMixin

    class _Dummy(CraftPageMixin):
        def _page_container(self, title=None):
            page = QtWidgets.QWidget()
            v = QtWidgets.QVBoxLayout(page)
            v.setContentsMargins(22, 20, 22, 20)
            v.setSpacing(16)
            return page, v

    d = _Dummy()
    page = d._page_craft()
    win = QtWidgets.QMainWindow()
    win.setCentralWidget(page)
    win.resize(1100, 700)
    win.show()
    try:
        for _ in range(6):
            QtWidgets.QApplication.processEvents()
        assert d._craft_list.viewMode() == QtWidgets.QListView.ListMode
        assert not d._craft_list.isWrapping()
        assert d._craft_list.iconSize().width() == cdetail.LIST_ICON
        assert not hasattr(d, "_btn_craft_grid")
        assert not hasattr(d, "_btn_craft_list")
        rows = [d._craft_list.item(i) for i in range(d._craft_list.count())]
        assert sum(1 for li in rows if li.data(cdetail.DATA_ROLE)) == 190
        assert any(li.data(cdetail.HEADER_ROLE) for li in rows)
        assert all(li.isHidden() for li in rows
                   if li.data(cdetail.HEADER_ROLE))
        nonheader = [li for li in rows if li.data(cdetail.DATA_ROLE)]
        assert nonheader[0].sizeHint().height() == \
            cdetail._craft_row_height(d._craft_list.font())
        # the page opens on the Blacksmith filter by default (the first
        # job chip), and the chip toggles back to ALL
        vis = [li for li in rows if not li.isHidden()
               and li.data(cdetail.DATA_ROLE)]
        assert len(vis) == 22
        assert all(li.data(cdetail.DATA_ROLE)["job"] == "Blacksmith"
                   for li in vis)
        d._craft_chip_btns["Blacksmith"].click()
        for _ in range(6):
            QtWidgets.QApplication.processEvents()
        vis = [li for li in rows if not li.isHidden()
               and li.data(cdetail.DATA_ROLE)]
        assert len(vis) == 190
    finally:
        win.close()


def test_search_shows_job_group_headers():
    """The per-job section bars appear only during a search — the group
    split that reads results from different jobs at a glance, with the
    selected job's group leading. The browse view is flat: a chip filter
    already says the job (and ALL shows every recipe), so the bars hide
    again once the search clears."""
    from farever_companion.ui.pages.craft import CraftPageMixin

    class _Dummy(CraftPageMixin):
        _codex_jump_to_unit = staticmethod(lambda *a: None)

        def _page_container(self, title=None):
            page = QtWidgets.QWidget()
            v = QtWidgets.QVBoxLayout(page)
            v.setContentsMargins(22, 20, 22, 20)
            v.setSpacing(16)
            return page, v

    d = _Dummy()
    page = d._page_craft()
    win = QtWidgets.QMainWindow()
    win.setCentralWidget(page)
    win.resize(1100, 700)
    win.show()
    try:
        for _ in range(6):
            QtWidgets.QApplication.processEvents()
        rows = [d._craft_list.item(i) for i in range(d._craft_list.count())]
        # the default Blacksmith filter is a flat list — no visible bars
        assert all(li.isHidden() for li in rows
                   if li.data(cdetail.HEADER_ROLE))
        vis = [li for li in rows if not li.isHidden()
               and li.data(cdetail.DATA_ROLE)]
        assert len(vis) == 22
        # searching brings the group split back, the selected job leading
        d._craft_search.setText("copper")
        for _ in range(6):
            QtWidgets.QApplication.processEvents()
        vis_headers = [li for li in rows
                       if li.data(cdetail.HEADER_ROLE) and not li.isHidden()]
        assert [li.data(cdetail.CAT_ROLE) for li in vis_headers] == \
            ["Blacksmith", "Jeweller"]
        assert vis_headers[0].text().endswith("·  8")   # BLACKSMITH · 8
        assert vis_headers[1].text().endswith("·  2")   # JEWELER · 2
        assert d._craft_chip_btns["Blacksmith"].isChecked()
        # clicking a group bar is inert (keeps the current detail)
        d._craft_list.itemClicked.emit(vis_headers[0])
        for _ in range(6):
            QtWidgets.QApplication.processEvents()
        assert d._craft_list.currentItem() is None or \
            d._craft_list.currentItem().data(cdetail.DATA_ROLE)
        # clearing the search hides the bars and restores the chip filter
        d._craft_search.setText("")
        for _ in range(6):
            QtWidgets.QApplication.processEvents()
        assert all(li.isHidden() for li in rows
                   if li.data(cdetail.HEADER_ROLE))
        vis = [li for li in rows if not li.isHidden()
               and li.data(cdetail.DATA_ROLE)]
        assert len(vis) == 22
    finally:
        win.close()


def test_search_bypasses_job_filter_and_leads_selected_group():
    """Typing a search suspends the job chips (the query covers ALL jobs —
    the chip stays checked, not cleared), but the selected job's section
    leads the results with its group header on top; clearing the search
    restores the chip filter and the default section order. There's no
    ALL/clear button — the search is the show-all."""
    from farever_companion.ui.pages.craft import CraftPageMixin

    class _Dummy(CraftPageMixin):
        def _page_container(self, title=None):
            page = QtWidgets.QWidget()
            v = QtWidgets.QVBoxLayout(page)
            v.setContentsMargins(22, 20, 22, 20)
            v.setSpacing(16)
            return page, v

    d = _Dummy()
    page = d._page_craft()
    win = QtWidgets.QMainWindow()
    win.setCentralWidget(page)
    win.resize(1100, 700)
    win.show()
    try:
        for _ in range(6):
            QtWidgets.QApplication.processEvents()
        # select Jeweller, then search 'copper' — Blacksmith (8) +
        # Jeweller (2): the query escapes the chip and finds both jobs
        d._craft_chip_btns["Jeweller"].click()
        d._craft_search.setText("copper")
        for _ in range(6):
            QtWidgets.QApplication.processEvents()
        rows = [d._craft_list.item(i) for i in range(d._craft_list.count())]
        vis = [li for li in rows if not li.isHidden()
               and li.data(cdetail.DATA_ROLE)]
        assert len(vis) == 10
        assert {li.data(cdetail.DATA_ROLE)["job"] for li in vis} == \
            {"Blacksmith", "Jeweller"}
        # the selected job's section leads, with the group headers shown
        vis_headers = [li for li in rows
                       if li.data(cdetail.HEADER_ROLE) and not li.isHidden()]
        assert [li.data(cdetail.CAT_ROLE) for li in vis_headers] == \
            ["Jeweller", "Blacksmith"]
        assert vis_headers[0].text().endswith("·  2")   # JEWELER · 2
        assert vis_headers[1].text().endswith("·  8")   # BLACKSMITH · 8
        # the chip stays checked while searching (suspended, not cleared)
        assert d._craft_chip_btns["Jeweller"].isChecked()
        # clearing the search restores the chip filter + default order
        d._craft_search.setText("")
        for _ in range(6):
            QtWidgets.QApplication.processEvents()
        rows = [d._craft_list.item(i) for i in range(d._craft_list.count())]
        vis = [li for li in rows if not li.isHidden()
               and li.data(cdetail.DATA_ROLE)]
        assert len(vis) == 40        # the full Jeweller job, filter back
        assert all(li.data(cdetail.DATA_ROLE)["job"] == "Jeweller"
                   for li in vis)
        headers = [li for li in rows if li.data(cdetail.HEADER_ROLE)]
        assert [li.data(cdetail.NAME_ROLE) for li in headers] == \
            ["Blacksmith", "Outfitter", "Jeweler", "Alchemist",
             "Cook", "Enchanter"]
        # clicking the chip again returns to ALL
        d._craft_chip_btns["Jeweller"].click()
        for _ in range(6):
            QtWidgets.QApplication.processEvents()
        vis = [li for li in rows if not li.isHidden()
               and li.data(cdetail.DATA_ROLE)]
        assert len(vis) == 190
    finally:
        win.close()


def test_material_link_opens_material_card():
    """Craft v2 merges the Craftables view into the recipe card: a raw
    material's name is a link that opens the material's card (Drops From +
    Used In rows that jump back to their recipes) in the detail pane, so
    the whole crafting loop stays browsable from the one flat list. The
    card is the non-gear item's full page — no OPEN IN GEAR button."""
    from PySide6 import QtCore
    from farever_companion.ui import components as C
    from farever_companion.ui.pages.craft import CraftPageMixin

    class _Dummy(CraftPageMixin):
        _codex_jump_to_unit = staticmethod(lambda *a: None)

        def _page_container(self, title=None):
            page = QtWidgets.QWidget()
            v = QtWidgets.QVBoxLayout(page)
            v.setContentsMargins(22, 20, 22, 20)
            v.setSpacing(16)
            return page, v

    d = _Dummy()
    page = d._page_craft()
    win = QtWidgets.QMainWindow()
    win.setCentralWidget(page)
    win.resize(1100, 700)
    win.show()
    try:
        for _ in range(6):
            QtWidgets.QApplication.processEvents()
        # select a recipe whose raw material is Simple Cauldron
        rows = [d._craft_list.item(i) for i in range(d._craft_list.count())]
        target = next(li for li in rows
                      if li.data(cdetail.ID_ROLE) == "SmallAlchemistCauldron")
        d._craft_list.setCurrentRow(d._craft_list.row(target))
        for _ in range(6):
            QtWidgets.QApplication.processEvents()
        lbls = [l.text() for l in d._craft_detail.findChildren(
            QtWidgets.QLabel) if l.text()]
        assert any("MATERIALS" in t for t in lbls), lbls
        assert d._craft_detail.findChildren(C.Stepper), "CRAFT × stepper"
        # crafted materials are CRAFTED pills (5 in the material rows, more
        # in the Total Needed bill); the raw one is a link instead
        btns = [b.text() for b in d._craft_detail.findChildren(
            QtWidgets.QPushButton) if b.text()]
        assert btns.count("CRAFTED") >= 5
        raw = next(l for l in d._craft_detail.findChildren(
            QtWidgets.QLabel)
            if "Simple Cauldron" in l.text()
            and l.text().startswith("<a href"))
        assert "1×  Simple Cauldron" in raw.text()
        # clicking it swaps the detail to the material card
        raw.linkActivated.emit("open")
        for _ in range(6):
            QtWidgets.QApplication.processEvents()
        lbls = [l.text() for l in d._craft_detail.findChildren(
            QtWidgets.QLabel) if l.text()]
        assert any("DROPS FROM" in t for t in lbls), lbls
        assert any("USED IN" in t for t in lbls), lbls
        # its Used In rows jump back to the consuming recipes
        used = [l for l in d._craft_detail.findChildren(QtWidgets.QLabel)
                if "Cauldron" in l.text() and l.text().startswith("<a href")]
        assert used, "material card has clickable Used In rows"
        used[0].linkActivated.emit("open")
        for _ in range(6):
            QtWidgets.QApplication.processEvents()
        lbls = [l.text() for l in d._craft_detail.findChildren(
            QtWidgets.QLabel) if l.text()]
        assert any("MATERIALS" in t for t in lbls), lbls   # recipe card back
        # the search narrows the flat list
        d._craft_search.setText("coccyx")
        for _ in range(6):
            QtWidgets.QApplication.processEvents()
        vis = [li for li in rows if not li.isHidden()
               and li.data(cdetail.DATA_ROLE)]
        assert [li.data(cdetail.ID_ROLE) for li in vis] == \
            ["Waist_RManfish_Fig"]
    finally:
        win.close()


def test_queue_survives_page_rebuild():
    """Adding recipes and ticking gathered materials persists to
    planner.json, and a fresh Craft page instance loads the saved queue +
    gathered state — the queue rides through app restarts. Clearing the
    queue persists the empty state."""
    from farever_companion.ui.pages.craft import CraftPageMixin

    class _Dummy(CraftPageMixin):
        def _page_container(self, title=None):
            page = QtWidgets.QWidget()
            v = QtWidgets.QVBoxLayout(page)
            v.setContentsMargins(22, 20, 22, 20)
            v.setSpacing(16)
            return page, v

    pages = []

    def new_page():
        d = _Dummy()
        pages.append(d._page_craft())
        return d

    d1 = new_page()
    d1._craft_qty = 10
    d1._craft_queue_add("SmallAlchemistCauldron")
    d1._craft_qty = 5
    d1._craft_queue_add("BronzeIngot")
    d1._craft_got_get()["Vial"] = 40
    d1._craft_queue_save()

    # a brand-new page instance loads the saved queue + gathered counts
    d2 = new_page()
    assert d2._craft_queue == [
        {"item": "SmallAlchemistCauldron", "qty": 10,
         "name": "Minor Alchemist Cauldron"},
        {"item": "BronzeIngot", "qty": 5, "name": "Bronze Ingot"}]
    assert d2._craft_got == {"Vial": 40}
    # removing then clearing persists the empty state
    d2._craft_queue_remove(0)
    d2._craft_queue_clear()
    d3 = new_page()
    assert d3._craft_queue == []
    assert d3._craft_got == {}
    for p in pages:
        p.deleteLater()


def test_stepper_value_field_is_editable():
    """The Stepper's number is a real input: clicking in it and typing a
    value commits on Enter/focus-out — clamped to lo..hi, junk input
    reverts, and the +/- buttons keep working."""
    from farever_companion.ui import components as C

    s = C.Stepper(value=10, lo=1, hi=999)
    assert isinstance(s._val, QtWidgets.QLineEdit)
    assert s._val.text() == "10"

    got: list[int] = []
    s.valueChanged.connect(got.append)
    s._val.setText("42")
    s._val.editingFinished.emit()
    assert s.value() == 42 and got == [42]

    s._val.setText("0")       # clamps to the floor
    s._val.editingFinished.emit()
    assert s.value() == 1
    s._val.setText("5000")    # clamps to the ceiling
    s._val.editingFinished.emit()
    assert s.value() == 999
    s._val.setText("abc")     # junk reverts
    s._val.editingFinished.emit()
    assert s.value() == 999
    s._val.setText("")        # empty reverts too
    s._val.editingFinished.emit()
    assert s.value() == 999

    s.setValue(50)
    s._minus.click()           # buttons still bump after an edit
    assert s.value() == 49
    s._plus.click()
    assert s.value() == 50


def test_stepper_glyphs_are_dead_centered():
    """The − and + stepper glyphs are PAINTED as line segments centered on
    the button's exact center. drawText centers by the font's em box, and
    the 1px-tall minus sign sits off-center inside it in most UI fonts
    (Inter included), so the glyph is drawn directly — the midpoint of
    every segment must be the button center."""
    from PySide6 import QtCore
    from farever_companion.ui.widgets import GlyphButton

    r = QtCore.QRect(0, 0, 26, 38)
    c = r.center()
    minus = GlyphButton._glyph_lines("−", r)
    assert minus == [(c.x() - 4, c.y(), c.x() + 4, c.y())]
    for x1, y1, x2, y2 in minus:
        assert ((x1 + x2) / 2, (y1 + y2) / 2) == (c.x(), c.y())
    plus = GlyphButton._glyph_lines("+", r)
    assert plus is not None and len(plus) == 2
    for x1, y1, x2, y2 in plus:
        assert ((x1 + x2) / 2, (y1 + y2) / 2) == (c.x(), c.y())
    # anything else falls back to text drawing (no lines)
    assert GlyphButton._glyph_lines("x", r) is None
