"""Drops From body tests (headless Qt, offscreen platform).

build_drops_body (ui/pages/items/drops.py): short lists render one 2-column
grid with no expander; long lists preview the first _PREVIEW_DROPS rows and
hide the rest behind a '+N more sources' expander whose revealed rows group
by drop kind ('MOBS · 8', 'CHESTS · 6', 'GATHER · 13') with readable names
(gathering-node ids humanized, size variants collapsed).
"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtWidgets  # noqa: E402

from farever_companion import paths  # noqa: E402
from farever_companion.data import items as idata  # noqa: E402
from farever_companion.ui.pages.items import drops  # noqa: E402


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


def _widgets(body):
    return [body.itemAt(i).widget() for i in range(body.count())
            if body.itemAt(i).widget() is not None]


def _ordered_texts(widget):
    """A widget's label/button texts in layout order — the group block's
    header button ('CHESTS · 17 ▾') comes before its names flow, and the
    flow's dot separators interleave with the names."""
    texts = []
    lay = widget.layout()
    if lay is None:
        if isinstance(widget, (QtWidgets.QLabel, QtWidgets.QPushButton)) \
                and widget.text():
            texts.append(widget.text())
        return texts
    for i in range(lay.count()):
        item = lay.itemAt(i)
        w = item.widget()
        if w is not None:
            texts.extend(_ordered_texts(w))
    return texts


def _expanded_labels(body):
    """Host the layout so visibility is real, click the expander, and return
    each revealed group block's rendered line ('MOBS · 8 ▾ Crimson mobs ·
    Slime King …' — the header first, names in order)."""
    host = QtWidgets.QWidget()
    host.setLayout(body)
    host.show()
    try:
        ws = _widgets(body)
        btn = next(w for w in ws if isinstance(w, QtWidgets.QPushButton))
        more = next(w for w in ws if w is not btn and w.isHidden())
        btn.click()
        assert not more.isHidden()
        out = []
        for i in range(more.layout().count()):
            row = more.layout().itemAt(i).widget()
            out.append(" ".join(_ordered_texts(row)))
        return out
    finally:
        host.close()


def test_short_list_has_no_expander():
    body = drops.build_drops_body(idata.shown_drops("Sword_Start"))
    assert not any(isinstance(w, QtWidgets.QPushButton)
                   for w in _widgets(body))


def test_friendly_node_names():
    """Gathering-node ids read as words with the size suffix stripped and
    the leading zone code dropped; named sources keep their labels."""
    assert drops._friendly_source_name(
        {"source": "CopperOre_Large"}) == "Copper Ore"
    assert drops._friendly_source_name(
        {"source": "R2Plant2_Small"}) == "Plant 2"
    assert drops._friendly_source_name(
        {"source": "R2PlantRare_Large"}) == "Plant Rare"
    assert drops._friendly_source_name(
        {"source": "Zealotus"}) == "Zealotus"
    assert drops._friendly_source_name(
        {"source": "Crimson mobs"}) == "Crimson mobs"
    assert drops._friendly_source_name(
        {"source": "Bee Hive Crates (x20)"}) == "Bee Hive Crates (x20)"


def test_node_type_label():
    """The base node type collapses size suffixes, zone codes and the
    numbered/rare instances — the same-type merge key ('R2Plant2_Small' +
    'R2Plant3_Small' + 'R2PlantRare' all read as 'Plant')."""
    assert drops._node_type_label(
        {"source": "R2Plant2_Small"}) == "Plant"
    assert drops._node_type_label(
        {"source": "R2Plant3_Small"}) == "Plant"
    assert drops._node_type_label(
        {"source": "R2PlantRare_Large"}) == "Plant"
    assert drops._node_type_label(
        {"source": "CopperOre_Large"}) == "Copper Ore"
    assert drops._node_type_label(
        {"source": "CopperOre_Small"}) == "Copper Ore"
    assert drops._node_type_label(
        {"source": "Zealotus"}) == "Zealotus"


def test_preview_cells_use_friendly_names():
    """The preview grid rows drop the raw node ids too — the first layout
    holds the plain cells, and no '_Large'/'_Small' suffix leaks. Each node
    type appears once: 'Copper Ore' once (not Large+Small), and the plant
    variants merge into one 'Plant' row (never 'Plant 2' / 'Plant 3')."""
    body = drops.build_drops_body(idata.shown_drops("StrangeSpores"))
    grid = body.itemAt(0).layout()          # the preview grid
    texts = []
    for i in range(grid.count()):
        w = grid.itemAt(i).widget()
        if w is not None:
            texts.extend(lbl.text() for lbl in w.findChildren(QtWidgets.QLabel))
    joined = " | ".join(texts)
    assert joined.count("Copper Ore") == 1, joined
    assert joined.count("Plant") == 1, joined
    assert "Plant 2" not in joined and "Plant 3" not in joined, joined
    assert "_" not in joined, joined


def test_node_type_variants_merge():
    """The dedupe merges gathering nodes by base node type (size, zone code
    and numbered/rare instances), keeps first-seen order and folds every
    merged row's locations into the survivor; boss/vendor rows are never
    merged."""
    rows = [
        {"kind": "gatherable", "source": "CopperOre_Large"},
        {"kind": "gatherable", "source": "CopperOre_Small"},
        {"kind": "gatherable", "source": "Zealotus"},
        {"kind": "npc", "source": "Guild Merchant"},
        {"kind": "gatherable", "source": "CopperOre_Medium"},
        {"kind": "gatherable", "source": "R2Plant2_Small",
         "locs": ["Node A"], "n_locs": 1},
        {"kind": "gatherable", "source": "R2Plant3_Small",
         "locs": ["Node B"], "n_locs": 1},
        {"kind": "gatherable", "source": "R2PlantRare_Large",
         "locs": ["Node C"], "n_locs": 1},
    ]
    out = drops._deduped_drops(rows)
    assert [d["source"] for d in out] == \
        ["CopperOre_Large", "Zealotus", "Guild Merchant",
         "R2Plant2_Small"]
    # the merged Plant row keeps every variant's location
    plant = out[-1]
    assert plant["locs"] == ["Node A", "Node B", "Node C"]
    assert plant["n_locs"] == 3


def test_long_list_groups_by_kind():
    """Strange Spores (27 raw sources, 17 distinct after the node-type
    merge — the plant variants collapse): 8 preview rows, a '+9 more
    sources' expander, and the revealed rows grouped as 'CHESTS · 6' +
    'GATHER · 3' with readable node-type names."""
    body = drops.build_drops_body(idata.shown_drops("StrangeSpores"))
    btn = next(w for w in _widgets(body)
               if isinstance(w, QtWidgets.QPushButton))
    assert "+9 more sources" in btn.text()
    labels = _expanded_labels(body)
    assert any(l.startswith("CHESTS · 6") for l in labels), labels
    gather = next(l for l in labels if l.startswith("GATHER · 3"))
    # node ids are readable and no raw '_Large'/'_Small' suffixes leak;
    # the plant variants merged into the preview's single 'Plant' row, so
    # the numbered 'Plant 2'/'Plant 3' forms are gone everywhere
    assert "Tin Ore" in gather and "Zealotus" in gather, gather
    assert "Plant 2" not in gather and "Plant 3" not in gather, gather
    assert "_" not in gather, gather
    # named sources (crates) drop the scan's '(xN)' roll count
    chests = next(l for l in labels if l.startswith("CHESTS"))
    assert "World Crates" in chests and "(x" not in chests, chests


def test_mob_rows_group_with_names():
    """SmallPouch: the expanded mob rows read as one 'MOBS · 8' line keeping
    the authored mob names (each name widget's tooltip carries its own locs)."""
    body = drops.build_drops_body(idata.shown_drops("SmallPouch"))
    labels = _expanded_labels(body)
    mobs = next(l for l in labels if l.startswith("MOBS · 8"))
    assert "Manfish mobs" in mobs and "Slime King" in mobs, mobs
    # the vendor rows stay full-width and visible (never in the expander)
    lbl = next(l for l in labels if l.startswith("MOBS"))
    assert lbl


def test_expanded_mob_names_link_to_codex():
    """In the expanded group rows, names with a codex card (Slime King) are
    jump buttons — clicking one calls on_codex_click with the unit id;
    faction groups ('Manfish mobs') are never links."""
    calls = []
    body = drops.build_drops_body(
        idata.shown_drops("SmallPouch"),
        on_codex_click=lambda uid, nm: calls.append((uid, nm)))
    host = QtWidgets.QWidget()
    host.setLayout(body)
    host.show()
    try:
        ws = _widgets(body)
        btn = next(w for w in ws if isinstance(w, QtWidgets.QPushButton))
        more = next(w for w in ws if w is not btn and w.isHidden())
        btn.click()
        rows = [more.layout().itemAt(i).widget()
                for i in range(more.layout().count())]
        mobs = next(r for r in rows
                    if any("MOBS" in w.text()
                           for w in (r.findChildren(QtWidgets.QLabel)
                                     + r.findChildren(QtWidgets.QPushButton))))
        slime = next((b for b in mobs.findChildren(QtWidgets.QPushButton)
                      if b.text() == "Slime King"), None)
        assert slime is not None
        assert slime.toolTip() == ""   # no mouse-over tooltips on Items
        slime.click()
        # faction groups stay plain labels
        assert not [b for b in mobs.findChildren(QtWidgets.QPushButton)
                    if b.text() == "Manfish mobs"]
    finally:
        host.close()
    assert calls == [("TODO_Slime_King", "Slime King")]


def test_large_kind_groups_start_collapsed_and_toggle():
    """A large kind group (> 6 names) starts collapsed: a 'CHESTS · 17 ▾'
    header shows the count with its name flow hidden (Gold's 17-crate list
    can't wall the reveal), and clicking the header reveals that group's
    names, flips the chevron to ▴, and fires on_expand (the detail card
    refits)."""
    refits = []
    body = drops.build_drops_body(
        idata.shown_drops("Gold"),
        on_expand=lambda: refits.append(True))
    host = QtWidgets.QWidget()
    host.setLayout(body)
    host.show()
    try:
        ws = _widgets(body)
        btn = next(w for w in ws if isinstance(w, QtWidgets.QPushButton))
        more = next(w for w in ws if w is not btn and w.isHidden())
        btn.click()
        groups = [more.layout().itemAt(i).widget()
                  for i in range(more.layout().count())]
        chests = next(g for g in groups
                      if " ".join(_ordered_texts(g)).startswith("CHESTS"))
        head = chests.layout().itemAt(0).widget()
        flow = chests.layout().itemAt(1).widget()
        assert "· 17" in head.text() and head.text().endswith("▾"), \
            head.text()
        assert head.toolTip() == ""   # no mouse-over tooltips on Items
        assert not flow.isVisible()          # collapsed by default
        refits.clear()                        # ignore the expander's own refit
        head.click()                          # reveal the names
        assert flow.isVisible() and head.text().endswith("▴")
        assert refits == [True]
        head.click()                          # and collapse again
        assert not flow.isVisible() and head.text().endswith("▾")
        assert refits == [True, True]
    finally:
        host.close()


def test_short_kind_groups_render_as_one_line():
    """Short kind groups (<= 6 names) render as ONE wrapping line — head
    label + names, no toggle header: StrangeSpores' CHESTS · 6 and
    GATHER · 3 read 'CHESTS · 6 — …' with the names already visible (a
    header line for a handful of names is more lines, not fewer)."""
    body = drops.build_drops_body(idata.shown_drops("StrangeSpores"))
    host = QtWidgets.QWidget()
    host.setLayout(body)
    host.show()
    try:
        ws = _widgets(body)
        btn = next(w for w in ws if isinstance(w, QtWidgets.QPushButton))
        more = next(w for w in ws if w is not btn and w.isHidden())
        btn.click()
        groups = [more.layout().itemAt(i).widget()
                  for i in range(more.layout().count())]
        joined = [" ".join(_ordered_texts(g)) for g in groups]
        chests = next(j for j in joined if j.startswith("CHESTS · 6"))
        assert "World Crates" in chests and "▾" not in chests, chests
        gather = next(j for j in joined if j.startswith("GATHER · 3"))
        assert "Tin Ore" in gather and "Zealotus" in gather, gather
        # short groups have no collapsible header buttons — one line each
        assert not [b for g in groups
                    for b in g.findChildren(QtWidgets.QPushButton)], \
            "short groups should not carry toggle headers"
    finally:
        host.close()


def test_one_name_group_has_no_toggle_header():
    """A kind group with a single name renders as one plain line — head +
    name, no collapsible header (a header line for one item is more lines,
    not fewer): 'MOBS · 1 — Slime King'."""
    rows = [{"kind": "unit", "source": "Slime King",
             "source_id": "TODO_Slime_King", "boss": False}]
    out = drops._kind_group_rows(rows)
    assert len(out) == 1
    g = out[0]
    assert not g.findChildren(QtWidgets.QPushButton)
    assert " ".join(_ordered_texts(g)) == "MOBS · 1 — Slime King"


def test_single_chests_get_human_names():
    """Every chest row drops the scan's '(xN)' — it's the loot table's roll
    count, not how many chests exist ('World Crates (x158)' rolls 2x like
    every other entry). Individual chests additionally read as 'Zone · World
    Chest N' from their world position."""
    single = drops._row_label({"kind": "chest",
                               "source": "W1_Siagarta_WorldChest_54 (x2)",
                               "source_id": "W1_Siagarta_WorldChest_54"})
    assert "(x2)" not in single, single
    assert "World Chest 54" in single and "·" in single, single
    vault = drops._row_label({"kind": "chest",
                              "source": "VaultChest_1 (x2)",
                              "source_id": "VaultChest_1"})
    assert "(x2)" not in vault and "Vault Chest 1" in vault, vault
    # group crates drop the count too — same roll-number stamp
    assert drops._row_label({"kind": "chest",
                             "source": "World Crates (x158)",
                             "source_id": "WorldCrate"}) == "World Crates"
    assert drops._row_label({"kind": "chest",
                             "source": "Bee Hive Crates (x20)",
                             "source_id": "BeeCrate"}) == "Bee Hive Crates"
    assert drops._row_label({"kind": "chest", "source": "Rift Tier6",
                             "source_id": "Rift_Tier6"}) == "Rift Tier6"


def test_loc_preview_is_short():
    """A mob row's location line is capped: the scan's zone clusters
    ('Abandoned Mines · Aurock Mound') flatten into names and only the first
    two show, with a '+N' for the rest — 'Munster's Crossing · Gorgon's
    Hollow +6', not a 4+-name wall."""
    body = drops.build_drops_body(idata.shown_drops("ScrollOfStrength"))
    host = QtWidgets.QWidget()
    host.setLayout(body)
    host.show()
    try:
        locs = [l for l in host.findChildren(QtWidgets.QLabel)
                if l.text().startswith("Munster's") and " · " in l.text()]
        assert locs, "Kobold mobs location preview not found"
        loc = locs[0]
        assert loc.toolTip() == ""   # no mouse-over tooltips on Items
        assert loc.text() == "Munster's Crossing · Gorgon's Hollow +6", \
            loc.text()
    finally:
        host.close()


def test_mount_drops_come_from_the_codex_card():
    """Mounts/gliders' Drops From rows come from the codex card — the
    authoritative vendor/mob list — not the scan's loot-table inference:
    the owl is one 'Perina Wann' row with its exact sale spot; the wolf is
    one row per mob that drops it (24 authored mobs, not the scan's 9
    inferred family rows). Regular gear has no card -> no rows."""
    owl = drops.codex_drop_rows("Glider_Owl_Brown02")
    assert len(owl) == 1
    assert owl[0]["kind"] == "npc" and owl[0]["source"] == "Perina Wann"
    assert owl[0]["locs"] == ["Navelin"] and owl[0].get("codex")
    wolf = drops.codex_drop_rows("Mount_Wolf_02")
    assert wolf and all(d["kind"] == "unit" for d in wolf)
    assert wolf[0]["source"] == "Alpha Wolf"
    assert wolf[0]["source_id"] == "Wolf_Z1W_Alpha"
    assert len(wolf) > 20
    assert drops.codex_drop_rows("Sword_Start") == []


def test_achievement_mounts_show_the_codex_reward():
    """Mounts/gliders carrying an achievement reward in the codex get an
    'achievement' drop row: the bat shows BOTH its mob drop and 'Collect
    Gliders 25' (like the card's label), and achievement-only mounts
    (Crimson Goat) show 'Savior of Skover' instead of a blank Drops From.
    The row renders with the ACHIEVEMENT tag and the category/points
    read inline after the name."""
    bat = drops.codex_drop_rows("Glider_Bat_Burning")
    ach = next(r for r in bat if r["kind"] == "achievement")
    assert ach["source"] == "Collect Gliders 25"
    assert ach["category"] == "Collection" and ach["points"] == 5
    assert any(r["kind"] == "unit" for r in bat)   # mob drop stays too
    goat = drops.codex_drop_rows("Mount_Goat_04")
    assert [r["source"] for r in goat] == ["Savior of Skover"]
    body = drops.build_drops_body(goat)
    host = QtWidgets.QWidget()
    host.setLayout(body)
    host.show()
    try:
        texts = ([l.text() for l in host.findChildren(QtWidgets.QLabel)]
                 + [b.text() for b in host.findChildren(QtWidgets.QPushButton)])
        assert any("Savior of Skover" in t for t in texts), texts
        assert "ACHIEVEMENT" in texts, texts
        tip = next(l.text() for l in host.findChildren(QtWidgets.QLabel)
                   if "10 pts" in l.text())
        assert "Combat" in tip and "10 pts" in tip, tip
    finally:
        host.close()


def test_codex_mount_vendor_row_skips_rare_tag():
    """A codex-sourced mount's vendor row renders as a full-width vendor
    cell with its sale spot and no RARE tag (Perina Wann sells for gold,
    not at Rare quality), and it jumps to the mount's own codex card."""
    calls = []
    body = drops.build_drops_body(
        drops.codex_drop_rows("Glider_Owl_Brown02"),
        on_codex_click=lambda uid, nm: calls.append((uid, nm)),
        item_id="Glider_Owl_Brown02")
    host = QtWidgets.QWidget()
    host.setLayout(body)
    host.show()
    try:
        texts = ([l.text() for l in host.findChildren(QtWidgets.QLabel)]
                 + [b.text() for b in host.findChildren(QtWidgets.QPushButton)])
        assert "Perina Wann" in texts and "Navelin" in texts, texts
        assert not any("RARE" in t for t in texts), texts
        btn = next(b for b in host.findChildren(QtWidgets.QPushButton)
                   if b.text() == "Perina Wann")
        btn.click()
        assert calls == [("Glider_Owl_Brown02", "Perina Wann")]
    finally:
        host.close()


def test_codex_target_resolution():
    """Which drop rows can jump to a codex card: NPC vendors with a codex
    unit (Perina Wann -> the Stable Master), named mobs with a card (Slime
    King), and single chests (WorldChest_54 -> its Chests-list row); faction
    groups, group crate/activity tokens and sprite-less vendors never link."""
    perina = {"kind": "npc", "source": "Perina Wann",
              "source_id": "MountTamer_NPC_1", "boss": False}
    assert drops._codex_target(perina) == "TODO_StableMaster"
    # when the row belongs to an item that has its own card (a mount/glider
    # sold by the vendor), that card wins — it pins the item's exact sale
    # spot instead of the vendor's aggregated shop locations
    assert drops._codex_target(perina, item_id="Mount_Goat_01") == \
        "Mount_Goat_01"
    assert drops._codex_target(perina, item_id="Glider_Owl_Brown02") == \
        "Glider_Owl_Brown02"
    # an item without a codex card keeps the vendor-card target
    assert drops._codex_target(perina, item_id="Sword_Start") == \
        "TODO_StableMaster"
    slime = {"kind": "unit", "source": "Slime King",
             "source_id": "TODO_Slime_King", "boss": False}
    assert drops._codex_target(slime) == "TODO_Slime_King"
    group = {"kind": "unit", "source": "Manfish mobs",
             "source_id": "Manfish", "boss": False}
    assert drops._codex_target(group) is None
    crates = {"kind": "chest", "source": "World Crates (x158)",
              "source_id": "WorldCrate", "boss": False}
    assert drops._codex_target(crates) is None
    single = {"kind": "chest",
              "source": "W1_Siagarta_WorldChest_54 (x2)",
              "source_id": "W1_Siagarta_WorldChest_54", "boss": False}
    assert drops._codex_target(single) == "W1_Siagarta_WorldChest_54"
    mira = {"kind": "npc", "source": "Mira the Demon Huntress",
            "source_id": "DemonHuntMira_NPC_1", "boss": False}
    assert drops._codex_target(mira) is None
    boss = {"kind": "unit", "source": "Reblochonk",
            "source_id": "TODO_Slime_King", "boss": True}
    assert drops._codex_target(boss) is None   # boss rows use their own link


def test_drops_rows_never_squash_wrapped_text():
    """The detail card (CardScroll._fit) sizes from the layout's
    heightForWidth and the drops labels are WrapLabels that lock their
    wrapped height at the assigned width — so on narrow panes no Drops
    From label ends up shorter than its own wrapped text (the old
    sizeHint-based fit compressed the labels and squashed/overlapped
    their lines on dense items like Strange Spores / Gold)."""
    from PySide6 import QtCore
    from farever_companion.ui.pages.items import support

    def clipped(viewport_w: int, item_id: str) -> list[str]:
        card = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(card)
        lay.addLayout(drops.build_drops_body(idata.shown_drops(item_id)))
        lay.addStretch(1)          # the real detail pane ends with a stretch
        sc = support.CardScroll(card)
        host = QtWidgets.QWidget()
        hl = QtWidgets.QVBoxLayout(host)
        hl.addWidget(sc)
        host.resize(viewport_w, 600)
        host.show()
        try:
            for _ in range(14):
                QtCore.QCoreApplication.processEvents()
            out = []
            for lbl in card.findChildren(QtWidgets.QLabel):
                if lbl.isHidden() or not lbl.text():
                    continue
                need = lbl.heightForWidth(max(1, lbl.width()))
                if lbl.height() < need:
                    out.append(lbl.text())
            return out
        finally:
            host.close()

    # dense multi-line sources at narrow pane widths — the old fit squashed
    # every one of these; mounts (single source) never wrapped so stay clean
    for item_id in ("StrangeSpores", "Gold", "SmallPouch", "SoftFur",
                    "ScrollOfVitality"):
        for w in (420, 480, 540):
            assert clipped(w, item_id) == [], (item_id, w)


def test_perina_row_is_a_codex_link():
    """A vendor row with a codex card renders as a clickable source name:
    Mount_Goat_01's 'Perina Wann' row jumps to the MOUNT's own card (the
    exact sale spot) when the page passes item_id; without it (the craft
    page) the Stable Master card is the fallback."""
    calls = []
    body = drops.build_drops_body(
        idata.shown_drops("Mount_Goat_01"),
        on_codex_click=lambda uid, nm: calls.append((uid, nm)),
        item_id="Mount_Goat_01")
    ws = _widgets(body)
    btn = next((b for w in ws
                for b in w.findChildren(QtWidgets.QPushButton)
                if b.text() == "Perina Wann"), None)
    assert btn is not None
    assert btn.toolTip() == ""   # no mouse-over tooltips on Items
    btn.click()
    assert calls == [("Mount_Goat_01", "Perina Wann")]
    # same row without item_id still falls back to the vendor's card
    calls2 = []
    body2 = drops.build_drops_body(
        idata.shown_drops("Mount_Goat_01"),
        on_codex_click=lambda uid, nm: calls2.append((uid, nm)))
    ws2 = _widgets(body2)
    btn2 = next((b for w in ws2
                 for b in w.findChildren(QtWidgets.QPushButton)
                 if b.text() == "Perina Wann"), None)
    btn2.click()
    assert calls2 == [("TODO_StableMaster", "Perina Wann")]
