"""Tests for the damage-by-type (affinity) presentation layer.

The data is the game's own per-hit damage type, so the view has to stay honest
about two things the raw buckets do not say out loud: the school vocabulary is
open (a live hit read `Raw`, `Chaos` and the mechanic tag `threshold`, none of
which any weapon sheet names), and a hit can carry no tag at all. Both are
shown as rows; neither is dropped or folded into another bucket.
"""
from __future__ import annotations

import json as _json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402

from farever_companion.core.dps_data import (  # noqa: E402
    _session_to_dict, read_history_file,
)
from farever_companion.core.dps_tracker import (  # noqa: E402
    CombatSession, PlayerParse, SkillParse,
)
from farever_companion.ui import affinity_view as av  # noqa: E402
from farever_companion.ui import theme  # noqa: E402
from farever_companion.ui.combat import (  # noqa: E402
    _CmpTypeTable, refresh_rail_compare,
)
from farever_companion.ui.combat.player_card import (  # noqa: E402
    _DamageTypeBar, _PdMdLabel, _ink_for,
)
from farever_companion.ui.pages.combat_compare import CombatCompareMixin  # noqa: E402
from farever_companion.ui.pages.combat_rail import CombatRailMixin  # noqa: E402
from farever_companion.ui import skill_row as SR  # noqa: E402
from farever_companion.ui.skill_row import SkillRow  # noqa: E402
from farever_companion.data.items import labels as item_labels  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def _qapp():
    """One offscreen QApplication for the module (SkillRow/QLabel need one)."""
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _labels(rows):
    return [r.label for r in rows]


def _app() -> QtWidgets.QApplication:
    """The module's QApplication. The ``_qapp`` fixture is autouse, so by the
    time a test body runs one already exists — and a fixture must not be
    called directly."""
    return QtWidgets.QApplication.instance()


# --- ordering and the buckets themselves -----------------------------------

def test_curated_schools_come_first_then_other_tags_then_untagged():
    """A weapon school outranks a bigger unrecognised bucket in the ORDER, but
    nothing is dropped or merged: Raw and Chaos are their own rows even though
    no item sheet names them."""
    rows = av.affinity_rows({"Chaos": 500.0, "Raw": 700.0, "Physical": 100.0},
                            total_damage=1300.0)      # fully tagged
    assert _labels(rows) == ["Physical", "Raw", "Chaos"]
    assert [r.amount for r in rows] == [100.0, 700.0, 500.0]
    assert all(r.tagged for r in rows)


def test_curated_order_is_stable_even_when_the_weapon_schools_are_small():
    rows = av.affinity_rows({"Light": 10.0, "Magic": 20.0, "Fire": 30.0}, 60.0)
    assert _labels(rows) == ["Fire", "Magic", "Light"]


def test_the_untagged_remainder_is_shown_not_normalized_away():
    """A nulled/renamed field, or a bridge older than the key, leaves buckets
    short of the total. The parts must NOT be renormalized to look complete."""
    rows = av.affinity_rows({"Physical": 600.0}, total_damage=1000.0)
    assert _labels(rows) == ["Physical", av.UNTAGGED]
    untagged = rows[-1]
    assert untagged.amount == 400.0 and untagged.pct == 40.0
    assert untagged.tagged is False
    # the tracker never buckets an untagged hit, so its count is unknown
    assert untagged.hits == 0


def test_a_fully_tagged_parse_grows_no_zero_row():
    rows = av.affinity_rows({"Physical": 400.0, "Fire": 600.0}, 1000.0)
    assert _labels(rows) == ["Physical", "Fire"]


def test_float_dust_is_not_mistaken_for_untagged_damage():
    """Summing buckets in a different order than the total leaves an epsilon;
    it must not print an 'untagged 0.0' row."""
    total = 1000.0
    rows = av.affinity_rows({"Physical": 999.9999999}, total)
    assert _labels(rows) == ["Physical"]


def test_a_capture_with_no_types_says_nothing_at_all():
    """All-untagged is not a breakdown, it is an absent feature: a permanent
    "untagged 100%" line would show on every fight for every user whose build
    (or bridge) does not carry the field."""
    assert av.affinity_rows(None, 0.0) == []
    assert av.affinity_rows({}, 0.0) == []
    assert av.affinity_rows({}, 250.0) == []
    assert av.affinity_rows(None, 250.0) == []


def test_sub_one_percent_keeps_a_decimal():
    rows = av.affinity_rows({"Fire": 2.0}, total_damage=1000.0)
    assert rows[0].pct_text() == "0.2%"
    big = av.affinity_rows({"Fire": 784.0}, total_damage=1000.0)
    assert big[0].pct_text() == "78%"


# --- per-player aggregation ------------------------------------------------

def _player() -> PlayerParse:
    p = PlayerParse(name="Me")
    a = SkillParse(skill_id="Sword_Base_Attack", name="Attack")
    a.damage, a.affinity_damage = 600.0, {"Physical": 600.0}
    a.affinity_hits = {"Physical": 4}
    b = SkillParse(skill_id="Book_Fireball_Skill1", name="Pyroball")
    b.damage, b.affinity_damage = 300.0, {"Fire": 200.0}
    b.affinity_hits = {"Fire": 1}
    c = SkillParse(skill_id="Mace_Benediction_Skill1", name="Benediction")
    c.damage = 100.0                       # untagged hit, no bucket
    p.skills = {"a": a, "b": b, "c": c}
    return p


def test_player_totals_sum_the_skills():
    damage, hits, total = av.player_affinity(_player())
    assert damage == {"Physical": 600.0, "Fire": 200.0}
    assert hits == {"Physical": 4, "Fire": 1}
    assert total == 1000.0


def test_player_rows_show_the_untagged_remainder():
    rows = av.player_rows(_player())
    assert _labels(rows) == ["Physical", "Fire", av.UNTAGGED]
    assert rows[-1].amount == 200.0


def test_player_affinity_tolerates_a_parse_without_the_fields():
    """An old archived session (or a foreign parse object) has no buckets."""
    p = PlayerParse(name="Ally")
    sp = SkillParse(skill_id="Attack", name="Attack")
    sp.damage = 50.0
    p.skills = {"x": sp}
    assert av.player_affinity(p) == ({}, {}, 50.0)
    assert av.player_affinity(object()) == ({}, {}, 0.0)
    assert av.player_rows(p) == []


# --- rendering -------------------------------------------------------------

def test_tooltip_lists_amounts_and_hits():
    rows = av.affinity_rows({"Physical": 600.0}, 1000.0, {"Physical": 4})
    html = av.affinity_tooltip_html(rows)
    assert "Damage by type:" in html
    assert "Physical: <b>600</b> (60.0%) · 4 hits" in html
    # the untagged row carries no hit count, because the tracker has none
    assert "untagged: <b>400</b> (40.0%)<br>" in html or html.endswith(
        "untagged: <b>400</b> (40.0%)")


# --- the two surfaces that render it ---------------------------------------

def test_skill_row_tooltip_carries_the_breakdown():
    row = SkillRow(density="detailed")
    sp = SkillParse(skill_id="Sword_Base_Attack", name="Attack")
    sp.damage, sp.hit_count = 1000.0, 12
    sp.affinity_damage = {"Physical": 900.0, "Fire": 100.0}
    sp.affinity_hits = {"Physical": 11, "Fire": 1}
    row.update(sp, 0.5)
    tip = row.toolTip()
    assert "Damage by type:" in tip
    assert "Physical" in tip and "Fire" in tip


def test_skill_row_tooltip_is_unchanged_without_any_types():
    """An older bridge must not grow an empty 'Damage by type' section."""
    row = SkillRow(density="detailed")
    sp = SkillParse(skill_id="Sword_Base_Attack", name="Attack")
    sp.damage, sp.hit_count = 100.0, 1
    row.update(sp, 0.5)
    assert "Damage by type" not in row.toolTip()
    assert "Total: <b>100.0</b>" in row.toolTip()


# --- the bar widget --------------------------------------------------------

# The widget has a fixed 16 px height, so the middle row is always y=8.
_BAR_W = 200
_BAR_Y = 8


def _row(label, pct, color, amount=0.0, hits=0, tagged=True) -> av.TypeRow:
    return av.TypeRow(label=label, amount=amount, pct=pct, hits=hits,
                      color=color, tagged=tagged)


def _bar_image(rows, width=_BAR_W, height=_BAR_Y * 2):
    """Render the bar offscreen and hand back the widget and its pixels.

    Painting is asserted through `grab()`, not by re-reading `rows`: the two
    things that can actually be wrong are the widths and the text, and those
    only exist in the painted result.
    """
    bar = _DamageTypeBar()
    bar.resize(width, height)
    bar.set_rows(rows)
    return bar, bar.grab().toImage()


def _at(image, x) -> str:
    """Colour at the middle of the bar, sampled away from segment edges so
    antialiasing cannot decide the answer."""
    return image.pixelColor(x, _BAR_Y).name().lower()


def test_bar_segments_are_proportional_to_the_share():
    from farever_companion.data.items import labels as item_labels

    rows = av.affinity_rows({"Physical": 250.0, "Fire": 750.0}, 1000.0)
    _, image = _bar_image(rows)
    # 25% of 200 px is 50 px, so x=40 and x=60 straddle the boundary
    assert _at(image, 40) == item_labels.affinity_color("Physical").lower()
    assert _at(image, 60) == item_labels.affinity_color("Fire").lower()
    # and the run reaches the right edge instead of stopping short of it
    assert _at(image, _BAR_W - 1) == item_labels.affinity_color("Fire").lower()


def test_bar_keeps_a_hairline_for_a_bucket_too_small_to_see():
    """0.1% of a 200 px bar is 0.2 px: without MIN_SEG_W a real bucket would
    paint nothing at all, and a type that paints nothing reads as absent."""
    _, image = _bar_image([_row("Raw", 0.1, "#ff00ff"),
                           _row("Physical", 99.9, "#00ff00")])
    assert _at(image, 0) == "#ff00ff"


def test_bar_labels_a_wide_segment():
    """The label inside its own segment is what makes the bar readable without
    a legend, and the pen is chosen for contrast rather than hardcoded."""
    from farever_companion.data.items import labels as item_labels

    gold = item_labels.affinity_color("Physical")
    _, image = _bar_image(av.affinity_rows({"Physical": 100.0}, 100.0))
    painted = {image.pixelColor(x, _BAR_Y).name().lower()
               for x in range(_BAR_W)}
    assert _ink_for(QtGui.QColor(gold)).lower() in painted


def test_bar_never_squeezes_a_label_into_a_sliver():
    """A 1% bucket is ~2 px wide: drawing "Fire 1%" there would be unreadable
    mush over its neighbour, so the label is dropped and hover carries it."""
    from farever_companion.data.items import labels as item_labels

    fire = item_labels.affinity_color("Fire").lower()
    _, image = _bar_image(av.affinity_rows({"Physical": 990.0, "Fire": 10.0},
                                           1000.0))
    # the sliver keeps its own colour (it IS a bucket)...
    assert _at(image, _BAR_W - 1) == fire
    # ...but carries no ink of any kind
    assert {_at(image, x) for x in (_BAR_W - 1,)} == {fire}


def test_segment_ink_flips_with_the_colour_it_sits_on():
    """The palette is open (an unknown school falls back to the theme accent),
    so a light fill must take the dark ink and a dark fill the light one."""
    assert _ink_for(QtGui.QColor("#e8b45a")) == theme.BG       # light gold
    assert _ink_for(QtGui.QColor("#101418")) == theme.TEXT     # near-black


def test_bar_drops_zero_share_rows_and_builds_the_hover_table():
    bar = _DamageTypeBar()
    bar.set_rows(av.affinity_rows({"Physical": 600.0}, 1000.0, {"Physical": 4})
                 + [_row("Fire", 0.0, "#ff0000")])
    assert [r.label for r in bar.rows] == ["Physical", av.UNTAGGED]
    assert "Damage by type:" in bar.toolTip()
    assert "Physical: <b>600</b> (60.0%) · 4 hits" in bar.toolTip()


def test_clearing_the_bar_drops_the_segments_and_the_tooltip():
    bar = _DamageTypeBar()
    bar.set_rows(av.affinity_rows({"Physical": 100.0}, 100.0))
    assert bar.rows and bar.toolTip()
    bar.clear()
    assert bar.rows == []
    assert bar.toolTip() == ""
    # a capture that decodes no types must not leave the last one on hover
    bar.set_rows(av.affinity_rows({}, 250.0))
    assert bar.toolTip() == ""


def test_the_bar_never_hides_itself():
    """Visibility is the rail's: it hides the whole caption+bar row. A child
    that hides itself stays hidden when its container is shown again - the trap
    `components._badge` shipped once - so the bar must carry no hide flag of
    its own and the rail tells it to clear() instead.

    Asserted on the CHILD, not on a parentless bar: Qt makes a widget with no
    parent a top-level window, and a top-level widget is hidden until it is
    shown, so `_DamageTypeBar().isHidden()` is True either way.
    """
    row = QtWidgets.QWidget()
    bar = _DamageTypeBar(row)
    row.setVisible(False)
    assert bar.isHidden() is False      # nothing of its own to undo
    assert bar.isVisible() is False     # ...but the hidden row still wins


# --- the PD / MD merged pair (the rail's text labels) -----------------------

def test_pd_md_text_merges_to_two_numbers_plus_untagged():
    """PD = Physical + Raw, MD = every other tagged school; the untagged
    remainder rides along — the same merge the Test Dummy HUD's line draws."""
    html = av.pd_md_text({"Physical": 600.0, "Raw": 100.0, "Fire": 250.0}, 1000.0)
    assert "PD 700" in html and "MD 250" in html and "untagged 50" in html


def test_pd_md_text_carries_percentages_and_school_colors():
    html = av.pd_md_text({"Physical": 600.0, "Fire": 400.0}, 1000.0)
    assert "PD 600 (60%)" in html
    assert "MD 400 (40%)" in html
    pd_c = item_labels.affinity_color("Physical")
    md_c = item_labels.affinity_color("Magic")
    assert pd_c in html and md_c in html


def test_pd_md_text_is_empty_when_nothing_is_classified():
    """An untagged capture grows no label — the same rule as the bar."""
    assert av.pd_md_text({}, 1000.0) == ""
    assert av.pd_md_text(None, 0.0) == ""


def test_the_rail_label_reads_one_player_and_clears_on_their_absence():
    """`set_player` is the label's whole contract: one player in, their merged
    pair in the text; a parse with no buckets clears it."""
    from farever_companion.ui.combat.player_card import _PdMdLabel

    lbl = _PdMdLabel()
    lbl.set_player(_player())
    assert "PD 600" in lbl.text()          # Physical only -> all PD
    assert "MD 200" in lbl.text()          # Fire only -> all MD
    assert "untagged 200" in lbl.text()
    bare = PlayerParse(name="Ally")
    sp = SkillParse(skill_id="Attack", name="Attack")
    sp.damage = 500.0
    bare.skills = {"x": sp}
    lbl.set_player(bare)
    assert lbl.text() == ""


# --- the rail surface ------------------------------------------------------

class _RailStub(CombatRailMixin):
    """Drives the real rail mixin; its only collaborators are the bar, the
    PD/MD labels and the row that carries them.

    Inheriting (rather than standing in for) the mixin keeps every `self.*`
    helper it calls resolvable, so the test exercises the shipped method.
    """

    def __init__(self):
        self.cp_dmg_type_bar = _DamageTypeBar()
        self.cp_dmg_type_pdmd = _PdMdLabel()
        self.cp_dmg_type_row = QtWidgets.QWidget()
        self.cp_dmg_type_row.setVisible(False)


# `isHidden()` (the explicit flag), never `isVisible()`: visibility is False
# for any child of an unshown page, which is how a guard on isVisible() for
# off-screen cards shipped a real bug in components._badge once already.

def test_rail_bar_shows_for_damage_tabs_only():
    for metric in ("damage", "skills"):
        stub = _RailStub()
        stub._refresh_damage_type(_player(), metric)
        assert not stub.cp_dmg_type_row.isHidden(), metric
        assert [r.label for r in stub.cp_dmg_type_bar.rows] == [
            "Physical", "Fire", av.UNTAGGED]
        # the merged pair rides the same row as the bar
        assert "PD 600" in stub.cp_dmg_type_pdmd.text(), metric
        assert "MD 200" in stub.cp_dmg_type_pdmd.text(), metric
        # the hover detail is the full table
        assert "Damage by type:" in stub.cp_dmg_type_bar.toolTip()
    for metric in ("healing", "taken", "damage_taken"):
        stub = _RailStub()
        stub._refresh_damage_type(_player(), metric)
        assert stub.cp_dmg_type_row.isHidden(), metric
        assert stub.cp_dmg_type_pdmd.text() == "", metric


def test_rail_bar_hides_when_the_capture_has_no_types():
    stub = _RailStub()
    p = PlayerParse(name="Ally")
    sp = SkillParse(skill_id="Attack", name="Attack")
    sp.damage = 500.0
    p.skills = {"x": sp}
    stub._refresh_damage_type(p, "damage")
    assert stub.cp_dmg_type_row.isHidden()
    assert stub.cp_dmg_type_bar.rows == []
    assert stub.cp_dmg_type_bar.toolTip() == ""
    assert stub.cp_dmg_type_pdmd.text() == ""


def test_hiding_the_rail_bar_clears_a_stale_breakdown():
    """Switching to a fight/capture with no types must not leave the previous
    fight's segments behind the hide, where only a reader of the widget's own
    state sees them (the stale-state trap this repo has hit with hidden
    widgets). Showing the row again would otherwise repaint them."""
    stub = _RailStub()
    stub._refresh_damage_type(_player(), "damage")
    assert not stub.cp_dmg_type_row.isHidden()
    assert stub.cp_dmg_type_bar.rows
    stub._refresh_damage_type(_player(), "healing")
    assert stub.cp_dmg_type_row.isHidden()
    assert stub.cp_dmg_type_bar.rows == []
    assert stub.cp_dmg_type_bar.toolTip() == ""


def test_clearing_the_rail_bar_drops_the_previous_players_split():
    """This is what a rail reset runs, so it has to actually empty the bar: a
    reset that only hid it would repaint the old player on the next show."""
    stub = _RailStub()
    stub._refresh_damage_type(_player(), "damage")
    assert stub.cp_dmg_type_bar.rows
    assert stub.cp_dmg_type_pdmd.text()
    stub._clear_damage_type()
    assert stub.cp_dmg_type_row.isHidden()
    assert stub.cp_dmg_type_bar.rows == []
    assert stub.cp_dmg_type_pdmd.text() == ""


def test_the_rail_refreshes_the_bar_on_every_skills_refresh():
    """Pinned at the source on purpose: the rail's refresh path needs a whole
    built page (every label and the skills table), and the point here is only
    that the bar is refreshed with the rest of the rail rather than once at
    build time, so a later fight cannot leave a stale split on screen."""
    from pathlib import Path

    import farever_companion.ui.pages.combat_rail as rail

    src = Path(rail.__file__).read_text(encoding="utf-8")
    assert "self._refresh_damage_type(p, metric)" in src
    # the bar is created by the rail itself, not by a caller, and the reset
    # path clears it with the rest of the hero-box state
    assert "self.cp_dmg_type_bar = _DamageTypeBar()" in src
    assert "self._clear_damage_type()" in src
    # the old skill-bar wiring is gone for good: it named attributes no code
    # ever created, which is why the widget never mounted in the first place
    assert "cp_stacked_bar" not in src
    assert "cp_hero_skill_count" not in src


# --- head-to-head: the Compare view's table --------------------------------

def _typed_player(name: str, buckets: dict, total: float) -> PlayerParse:
    """A player whose single skill deals ``total``, split into ``buckets``."""
    p = PlayerParse(name=name)
    sp = SkillParse(skill_id="Sword_Base_Attack", name="Attack")
    sp.damage = total
    sp.affinity_damage = dict(buckets)
    sp.affinity_hits = {k: 1 for k in buckets}
    p.skills = {"a": sp}
    # Kept in step with the skills: the compare view ranks players by this,
    # and a mismatch would rank them by a number the table never shows.
    p.total_damage = total
    p.hit_count = len(buckets)
    return p


def _untyped_player(name: str) -> PlayerParse:
    p = PlayerParse(name=name)
    sp = SkillParse(skill_id="Attack", name="Attack")
    sp.damage = 400.0                       # dealt damage, none of it tagged
    p.skills = {"a": sp}
    p.total_damage = 400.0
    return p


def test_compare_rows_take_the_union_not_the_intersection():
    """A school only ONE of them dealt keeps its row. Dropping it would leave
    the other side's number with nothing to compare against, which is the whole
    job of this table."""
    rows = av.compare_type_rows(
        _typed_player("War", {"Physical": 900.0}, 900.0),
        _typed_player("Mage", {"Fire": 900.0}, 900.0))
    assert [r.label for r in rows] == ["Physical", "Fire"]
    by = {r.label: r for r in rows}
    assert (by["Physical"].a_amount, by["Physical"].b_amount) == (900.0, 0.0)
    assert (by["Fire"].a_amount, by["Fire"].b_amount) == (0.0, 900.0)
    assert by["Physical"].delta() == 900.0
    assert by["Fire"].delta() == -900.0
    # a row only one side has still carries the school's colour
    assert by["Fire"].color.startswith("#")


def test_each_side_percentage_stays_against_its_own_total():
    """60/40 of a big parse and 30/70 of a small one must read as 60/40 and
    30/70. Renormalizing both sides to their TAGGED damage would hide the
    untagged remainder - the one number that says the capture was incomplete."""
    a = _typed_player("War", {"Physical": 600.0}, 1000.0)
    b = _typed_player("Mage", {"Physical": 30.0}, 100.0)
    by = {r.label: r for r in av.compare_type_rows(a, b)}
    assert by["Physical"].a_pct == 60.0 and by["Physical"].b_pct == 30.0
    assert by[av.UNTAGGED].a_pct == 40.0 and by[av.UNTAGGED].b_pct == 70.0
    assert by[av.UNTAGGED].tagged is False


def test_compare_row_order_matches_the_single_player_breakdown():
    """Same fight, same order: a type must not jump rows between the rail's bar
    and this table. Curated schools, then other tags by size, untagged last."""
    a = _typed_player("War", {"Chaos": 50.0, "Raw": 300.0, "Physical": 100.0},
                      450.0)
    b = _typed_player("Mage", {"Light": 10.0, "Magic": 20.0, "Fire": 30.0}, 60.0)
    assert [r.label for r in av.compare_type_rows(a, b)] == [
        "Physical", "Fire", "Magic", "Light", "Raw", "Chaos"]


def test_compare_rows_are_empty_when_neither_side_has_any_types():
    """Same rule as the bar: an all-untagged capture grows no table, rather
    than an all-zero one that says nothing and shows on every fight."""
    assert av.compare_type_rows(None, None) == []
    assert av.compare_type_rows(_untyped_player("A"), _untyped_player("B")) == []


def test_one_typed_side_is_still_a_table():
    """A side that dealt damage but tagged none of it (a pet row, an older
    bridge) keeps its column with zeros instead of removing the row."""
    rows = av.compare_type_rows(_typed_player("War", {"Physical": 500.0}, 500.0),
                                _untyped_player("Fresh"))
    assert [r.label for r in rows] == ["Physical"]
    assert rows[0].a_amount == 500.0 and rows[0].b_amount == 0.0


def test_compare_rows_tolerate_a_foreign_parse_object():
    assert av.compare_type_rows(object(), object()) == []
    assert av.compare_type_rows(object(), _typed_player("W", {"Fire": 1.0}, 1.0))


def test_compare_tooltip_names_both_sides():
    rows = av.compare_type_rows(_typed_player("War", {"Physical": 600.0}, 1000.0),
                                _typed_player("Mage", {"Fire": 100.0}, 100.0))
    tip = av.compare_type_tooltip(rows[0], "War", "Mage")
    assert "War:" in tip and "Mage:" in tip
    assert "1 hits" in tip
    assert "\u0394" in tip                      # the signed gap, spelled out

def test_type_table_hides_itself_when_the_rows_go_away():
    """Explicit in both directions: the box is shown again by a later refresh,
    so a table left visible with no rows would keep a stale pair on screen."""
    table = _CmpTypeTable()
    table.set_rows(av.compare_type_rows(_typed_player("A", {"Fire": 1.0}, 1.0),
                                        _typed_player("B", {"Fire": 1.0}, 1.0)))
    assert not table.isHidden()
    assert [r.label for r in table.rows()] == ["Fire"]
    table.set_rows([])
    assert table.isHidden()
    assert table.rows() == []


# --- the compare surface, driven through the real refresh -------------------

class _CmpPage:
    """Just enough of the Combat page for the real ``refresh_rail_compare``.

    Built fresh per test: the refresh mutates every widget below, and the
    class-attribute stub style would carry that state between tests.
    """

    def __init__(self):
        self.cp_cmp_insight = QtWidgets.QLabel("")
        self.cp_plotter = None
        self.cp_compare_combo_a = QtWidgets.QComboBox()
        self.cp_compare_combo_b = QtWidgets.QComboBox()
        self._cp_selected_player = ""
        self._cp_compare_a = ""
        self._cp_compare_b = ""
        for attr in ("cp_cmp_class_match", "cp_cmp_winner", "cp_cmp_dps_delta",
                     "cp_cmp_pct_delta", "cp_cmp_crit_gap", "cp_cmp_hit_rate",
                     "cp_cmp_top_gap"):
            setattr(self, attr, QtWidgets.QLabel(""))
        self.cp_cmp_rows_lay = QtWidgets.QGridLayout()
        self._cp_cmp_row_widgets: list = []
        # Wired exactly as combat_compare._build_compare_container wires it.
        self.cp_cmp_type_box = QtWidgets.QWidget()
        self.cp_cmp_type_table = _CmpTypeTable()
        self.cp_cmp_type_box.hide()

    def _get_active_tracker(self):
        return None


def _session(pairs=(), duration: float = 10.0) -> CombatSession:
    """A finished fight, pinned the way an archive pins one.

    ``CombatSession.duration`` is derived, not stored, so a fixed length means
    a PAUSED session with both ends set - which is also what `read_history_file`
    produces, so these sessions behave like restored ones.
    """
    s = CombatSession("Fight", kind="boss")
    s.state = "PAUSED"
    s.start_time = 1_700_000_000.0
    s.end_time = s.start_time + duration
    for p in pairs:
        s.players[p.name] = p
    return s


def test_the_compare_skill_list_tags_procs_and_passives():
    """The main skill list carries the tag, not just the hover popup.

    Reported live 2026-10-03: on the Past Fights / DPS Analysis compare table
    "Purified Heart", "Bonethrow · Status" and "Shattered Rage" all read as
    plain names, while the meter tagged the same skills. This table aggregates
    its own rows by name, so it never went through SkillRow - the tag has to
    be applied here, and the aggregation KEY must stay the untagged name so
    two players' "Purified Heart" still merge into one row.
    """
    def _player(nm, skills):
        p = PlayerParse(name=nm)
        for i, (sid, sname, dmg) in enumerate(skills):
            sp = SkillParse(skill_id=sid, name=sname)
            sp.damage = dmg
            p.skills[f"{nm}{i}"] = sp
        p.total_damage = sum(s[2] for s in skills)
        return p

    PROC = "PurifiedHeart_Proc"
    TREE = "Warrior_Talent_ShatteredRage"
    TICK = "Axe_Boomerang_Skill1_Status"
    pa = _player("War", [(PROC, "Purified Heart", 900.0),
                         (TICK, "Bonethrow · Status", 800.0)])
    pb = _player("Mage", [(TREE, "Shattered Rage", 700.0),
                          (PROC, "Purified Heart", 650.0)])
    page = _CmpPage()
    page._cp_compare_a, page._cp_compare_b = "War", "Mage"
    refresh_rail_compare(page, _session((pa, pb)), 10.0, "compare")

    labels = [cells[1].text() for cells in page._cp_cmp_row_widgets]
    assert "Purified Heart (Item proc)" in labels
    assert "Shattered Rage (Passive)" in labels
    # A combat skill the player pressed is not tagged.
    assert "Bonethrow · Status" in labels
    # Same tag on both the label and the tooltip, and the tag is not elided
    # away by the 26-char ceiling.
    tooltips = [cells[1].toolTip() for cells in page._cp_cmp_row_widgets]
    assert any("Purified Heart (Item proc) (ID: PurifiedHeart_Proc)" in t
               for t in tooltips)


def test_compare_refresh_fills_the_type_table_for_the_chosen_pair():
    pa = _typed_player("War", {"Physical": 600.0}, 1000.0)
    pb = _typed_player("Mage", {"Fire": 100.0}, 1000.0)
    page = _CmpPage()
    page._cp_compare_a, page._cp_compare_b = "War", "Mage"
    refresh_rail_compare(page, _session((pa, pb)), 10.0, "compare")

    assert not page.cp_cmp_type_box.isHidden()
    by = {r.label: r for r in page.cp_cmp_type_table.rows()}
    assert set(by) == {"Physical", "Fire", av.UNTAGGED}
    assert by["Physical"].a_pct == 60.0 and by["Physical"].b_pct == 0.0
    assert by["Fire"].b_pct == 10.0
    # the untagged remainder is the same remainder the rail's bar shows
    assert by[av.UNTAGGED].a_amount == 400.0 and by[av.UNTAGGED].b_amount == 900.0


def test_compare_refresh_hides_the_table_with_nothing_to_compare():
    """One player is not a comparison, and the previous pair's rows must not
    survive behind the early return that says so."""
    pa = _typed_player("War", {"Physical": 600.0}, 1000.0)
    pb = _typed_player("Mage", {"Fire": 100.0}, 1000.0)
    page = _CmpPage()
    refresh_rail_compare(page, _session((pa, pb)), 10.0, "compare")
    assert not page.cp_cmp_type_box.isHidden()

    refresh_rail_compare(page, _session((pa,)), 10.0, "compare")
    assert page.cp_cmp_type_box.isHidden()
    assert page.cp_cmp_type_table.rows() == []
    assert "Need at least 2 players" in page.cp_cmp_insight.text()


def test_compare_refresh_hides_the_table_when_the_capture_tags_nothing():
    page = _CmpPage()
    refresh_rail_compare(page, _session((_untyped_player("A"),
                                        _untyped_player("B"))), 10.0, "compare")
    assert page.cp_cmp_type_box.isHidden()


def test_compare_refresh_tolerates_a_page_without_the_table():
    """`getattr`-guarded, so an older page object (or another stub) cannot make
    the whole compare refresh raise over a section it does not have."""
    pa = _typed_player("War", {"Physical": 600.0}, 1000.0)
    pb = _typed_player("Mage", {"Fire": 100.0}, 1000.0)
    page = _CmpPage()
    del page.cp_cmp_type_box
    del page.cp_cmp_type_table
    page._cp_compare_a, page._cp_compare_b = "War", "Mage"
    refresh_rail_compare(page, _session((pa, pb)), 10.0, "compare")


# --- the same split, from an ARCHIVED fight --------------------------------

def test_an_archived_fight_drives_both_surfaces(tmp_path):
    """The old-session path, end to end.

    A fight goes out through the archive WRITER, to a real history file, and
    comes back through the archive READER - the same pair `past_fights` uses.
    What comes back is an ordinary CombatSession, so the rail's bar and the
    Compare table both show its split with no live capture involved. Before
    this, the buckets existed but nothing asserted that an old fight reached
    either surface.
    """
    fight = _session((_typed_player("War (You)", {"Physical": 800.0}, 1000.0),
                      _typed_player("Mage", {"Fire": 750.0}, 1000.0)),
                     duration=12.0)
    path = tmp_path / "boss_Kartik_Warrior.json"
    path.write_text(_json.dumps({"history": [_session_to_dict(fight)]}),
                    encoding="utf-8")

    back = read_history_file(path)
    assert len(back) == 1
    old = back[0]

    # the rail's surface
    assert [r.label for r in av.player_rows(old.players["War (You)"])] == [
        "Physical", av.UNTAGGED]

    # the compare surface, from the same archived parse
    page = _CmpPage()
    page._cp_compare_a, page._cp_compare_b = "War (You)", "Mage"
    refresh_rail_compare(page, old, 12.0, "compare")
    assert not page.cp_cmp_type_box.isHidden()
    by = {r.label: r for r in page.cp_cmp_type_table.rows()}
    assert by["Physical"].a_pct == 80.0 and by["Fire"].b_pct == 75.0


class _CompareStub(CombatCompareMixin):
    """Drives the real compare-page builder, which has no collaborators.

    Inheriting the mixin (rather than standing in for it) exercises the shipped
    method - the same reason the rail's stub below inherits its mixin.
    """


def test_the_compare_builder_mounts_the_type_section():
    """The builder really puts the section IN the compare tree.

    A widget that is constructed but never added to a layout is invisible, and
    this repo has shipped that exact bug (`_StackedSkillBar`, dead because
    nothing ever mounted it), so this asserts the built tree and the ORDER:
    damage by type sits between the gap summary and the skill matrix.
    """
    page = _CompareStub()
    container = page._build_compare_container()

    assert any(page.cp_cmp_type_box is w
               for w in container.findChildren(QtWidgets.QWidget)), \
        "the type box is not in the compare container"
    assert page.cp_cmp_type_box.isHidden(), "it must start hidden"

    lay = container.widget().layout()
    order = [lay.itemAt(i).widget() for i in range(lay.count())]
    assert order.index(page.cp_cmp_type_box) == order.index(page.cp_cmp_summary_box) + 1
    assert order.index(page.cp_cmp_table_box) == order.index(page.cp_cmp_type_box) + 1


def test_an_archive_written_before_the_field_existed_shows_no_table(tmp_path):
    """The other half of the same path: an archived fight with no buckets is
    not an error, it simply has nothing to show - and must not raise on the way
    through the reader."""
    fight = _session((_untyped_player("War (You)"), _untyped_player("Mage")),
                     duration=12.0)
    payload = _session_to_dict(fight)
    for _p in payload["players"]:                 # strip what an old write left
        for _sk in _p["skills"]:
            _sk.pop("affinity_damage", None)
            _sk.pop("affinity_hits", None)
    path = tmp_path / "boss_old.json"
    path.write_text(_json.dumps({"history": [payload]}), encoding="utf-8")

    old = read_history_file(path)[0]
    assert av.player_rows(old.players["War (You)"]) == []

    page = _CmpPage()
    refresh_rail_compare(page, old, 12.0, "compare")
    assert page.cp_cmp_type_box.isHidden()


# --- the skills table's single TYPE column ----------------------------------#
#
# One column that NAMES the school a skill deals, not one column per school: a
# weapon deals one school, the table already has a TOTAL for the number, and
# four numeric columns do not fit the 620px rail beside the skill name.


def test_the_table_names_one_type_in_a_column_of_a_workable_width():
    assert av.TYPE_HEADER == "TYPE"
    # The width is the load-bearing number here. It is what keeps the skill
    # NAME readable, so it is pinned by the geometry test further down rather
    # than trusted to stay small.
    assert 0 < av.TYPE_COLUMN_WIDTH <= 100


def test_a_single_school_is_named_and_nothing_else():
    named = av.dominant_type(
        av.affinity_rows({"Fire": 800.0}, total_damage=800.0))
    assert named.label == "Fire" and named.others == 0
    assert named.caption() == "Fire"
    assert named.color == item_labels.affinity_color("Fire")


def test_the_largest_school_wins_not_the_first_curated_one():
    """Curated order is for the BAR's rows; a single type column reports what
    the skill actually dealt, so Magic beats a bigger-but-absent Physical."""
    named = av.dominant_type(av.affinity_rows(
        {"Physical": 4.0, "Magic": 996.0}, total_damage=1000.0))
    assert named.label == "Magic" and named.others == 0


def test_a_skill_that_really_splits_is_not_reported_as_pure():
    """The whole reason the count exists: naming the top school alone would
    claim a 60/40 fire/physical skill is pure fire."""
    named = av.dominant_type(av.affinity_rows(
        {"Fire": 600.0, "Physical": 400.0}, total_damage=1000.0))
    assert named.label == "Fire" and named.others == 1
    assert named.caption() == "Fire +1"


def test_proc_noise_below_the_threshold_is_not_called_a_split():
    """A 1% off-type proc on an otherwise pure skill is not a split worth
    writing '+1' over — the column would cry wolf on every weapon."""
    named = av.dominant_type(av.affinity_rows(
        {"Physical": 990.0, "Fire": 10.0}, total_damage=1000.0))
    assert named.label == "Physical" and named.others == 0
    assert named.caption() == "Physical"


def test_a_three_way_skill_counts_both_others():
    named = av.dominant_type(av.affinity_rows(
        {"Physical": 500.0, "Fire": 300.0, "Light": 200.0},
        total_damage=1000.0))
    assert named.caption() == "Physical +2"


def test_the_untagged_remainder_is_never_named_as_a_type():
    """It says the field was missing, not that the skill dealt 'untagged'
    damage — so a half-tagged skill still names the school it did read."""
    named = av.dominant_type(av.affinity_rows(
        {"Physical": 600.0}, total_damage=1000.0))
    assert named.label == "Physical" and named.others == 0


def test_nothing_classified_names_nothing():
    """An older bridge, or a renamed field: the column must stay blank rather
    than assert a type it did not read."""
    assert av.dominant_type([]) is None
    assert av.dominant_type(av.affinity_rows({}, total_damage=900.0)) is None


# --- the per-skill cell -----------------------------------------------------#

def _typed_skill(**buckets) -> SkillParse:
    sp = SkillParse(skill_id="Book_Fireball_Skill1", name="Pyroball")
    total = float(sum(buckets.values()))
    sp.damage, sp.hit_count = total or 1000.0, 12
    sp.affinity_damage = dict(buckets)
    sp.affinity_hits = {k: 1 for k in buckets}
    return sp


def test_a_detailed_row_names_the_type_its_skill_deals():
    row = SkillRow(density="detailed")
    row.update(_typed_skill(Physical=900.0, Fire=4.0), 0.5)
    assert row.type_caption() == "Physical"
    assert row._type_lbl.text() == "Physical"


def test_the_type_column_sits_between_the_name_and_the_total():
    """Reading order: skill, what it deals, what it came to."""
    row = SkillRow(density="detailed")
    order = [row._layout.itemAt(i).widget() for i in range(row._layout.count())]
    assert order.index(row._name_lbl) < order.index(row._type_lbl)
    assert order.index(row._type_lbl) < order.index(row._total_lbl)
    assert order.index(row._total_lbl) < order.index(row._share_lbl)


def test_a_split_skill_shows_the_count_not_just_the_top_school():
    row = SkillRow(density="detailed")
    row.update(_typed_skill(Fire=600.0, Physical=400.0), 0.5)
    assert row.type_caption() == "Fire +1"
    # the dim "+1" is real markup, not a stray character in the school name
    assert "<span" in row._type_lbl.text()


def test_a_capture_with_no_types_leaves_the_column_blank():
    row = SkillRow(density="detailed")
    sp = SkillParse(skill_id="Sword_Base_Attack", name="Attack")
    sp.damage, sp.hit_count = 100.0, 1
    row.update(sp, 0.5)
    assert row._type_lbl.text() == ""
    assert row.type_caption() == ""


def test_a_heal_row_never_shows_a_damage_type():
    """The column is a damage-channel reading; a heal row has none."""
    sp = SkillParse(skill_id="Heal_Priest_Skill1", name="Heal")
    sp.heals, sp.heal_count = 500.0, 2
    sp.damage, sp.affinity_damage = 300.0, {"Physical": 300.0}
    row = SkillRow(density="detailed")
    row.update(sp, 0.5, mode="healing")
    assert row._type_lbl.text() == ""


def test_a_combined_row_leaves_it_blank_too():
    """A combined row shows a damage figure AND a heal figure, so a named type
    would look like it explained the whole line."""
    sp = SkillParse(skill_id="Sword_Base_Attack", name="Attack")
    sp.damage, sp.heals = 300.0, 200.0
    sp.affinity_damage = {"Physical": 300.0}
    row = SkillRow(density="detailed")
    row.update(sp, 0.5, mode="both")
    assert row._type_lbl.text() == ""


def test_the_overlay_row_has_no_type_column():
    """Compact rows have no header to line up with, so a column there would
    only narrow the name — the tooltip still carries the split."""
    row = SkillRow(density="compact")
    assert row._type_lbl is None
    row.update(_typed_skill(Physical=900.0, Fire=4.0), 0.5)
    assert "Damage by type" in row.toolTip()


# --- the column has to leave the skill NAME readable ------------------------#

class _RailWidgetStub(CombatRailMixin):
    """The real mixin, built for real. Only the two signal targets the build
    wires up are stubbed — the widgets under test are the shipped ones."""

    def __init__(self):
        self._cp_selected_player = ""
        self._cp_rail_skill_widgets = {}
        self.model = None

    def _cp_solo_filter_on(self):
        return False

    def _copy_selected_player_parse(self):
        pass

    def _on_hero_compare_clicked(self):
        pass


def _rail_minimum_width() -> int:
    """The width the rail will not go below.

    Read off the built rail rather than repeated here, so a test that pins a
    width is pinning a WIDTH and not a copy of a number."""
    rail = _RailWidgetStub()
    container = rail._build_combat_rail()
    try:
        return rail.cp_rail_box.minimumWidth()
    finally:
        _close(container)


# The widest skill name in this player's own archives ONCE CAPPED at
# SR.NAME_MAX_CHARS — "Resilience of the Unkillable…" — at 10pt Segoe UI.
# PINNED, not measured: this headless Qt reports zero font families, so a
# fontMetrics() call in a unit test measures a fallback face. Re-derive with
# ai/workspace/dmgtype/probe_name_budget.py, which loads the real fonts.
WIDEST_REAL_SKILL_NAME_PX = 202


def _rail_at(rail_width: int):
    """Build the real rail, load a fight, and lay it out at one rail width.

    ``setFixedWidth`` rather than a container resize: the rail is a splitter
    pane, so pinning the pane is the only way to ask a specific width a
    question. Every layout is activated explicitly — reading a widget position
    without that returns the PREVIOUS pass, which is how a 6px name column
    nearly shipped as a 168px one.
    """
    rail = _RailWidgetStub()
    container = rail._build_combat_rail()
    container.resize(rail_width + 340, 620)
    container.show()
    _app().processEvents()
    rail._refresh_rail_skills(_session((_player(),), duration=12.0), 12.0,
                              "damage")
    rail.cp_rail_box.setFixedWidth(rail_width)
    for w in (container, rail.cp_rail_box, rail.cp_skills_box):
        w.layout().activate()
    _app().processEvents()
    _app().processEvents()
    return rail, container


def _close(container) -> None:
    container.close()
    container.deleteLater()
    _app().processEvents()


def test_the_type_column_leaves_the_skill_name_readable_at_every_rail_width():
    """The guard that was missing when four 56px columns shipped: whatever
    width the rail is given, the name must keep enough room to read. This is
    the test that would have caught it — the name was 6px.

    The widths start at the rail's own minimum, because that is the narrowest
    the rail can be in normal use. Below it the column is expected to drop
    (see the next test) and the name is expected to starve, which is the
    point of the minimum rather than a thing to assert away."""
    rail_min = _rail_minimum_width()
    for rail_w in (rail_min, 800, 860):
        rail, container = _rail_at(rail_w)
        try:
            row = rail._cp_rail_skill_widgets["Sword_Base_Attack"]
            assert not row._type_lbl.isHidden(), f"column hidden at {rail_w}"
            assert row._name_lbl.width() >= 120, (rail_w, row._name_lbl.width())
            # and the row still fits without a horizontal scrollbar
            assert row._layout.minimumSize().width() <= \
                rail.cp_skills_scroll.viewport().width(), rail_w
        finally:
            _close(container)


def test_the_column_survives_the_rails_own_minimum_width():
    """The rail's minimum is set to the width the table needs for BOTH the
    column and a readable name, so the column cannot disappear in normal use.
    It did once: a 470px minimum left the name 18px, the column was dropped as
    'too narrow', and the fix people reached for was to shrink SHARE instead
    of buying the space."""
    rail_min = _rail_minimum_width()
    rail, container = _rail_at(rail_min)
    try:
        assert rail.cp_rail_box.minimumWidth() == rail_min
        row = rail._cp_rail_skill_widgets["Sword_Base_Attack"]
        assert not row._type_lbl.isHidden(), "the column vanished at the minimum"
        assert row._name_lbl.width() >= 120
    finally:
        _close(container)


def test_the_rail_minimum_is_what_the_real_skill_names_need():
    """The number the minimum was set to, and why.

    The rail is pinned to its minimum on any window up to about 1400px wide,
    so the minimum IS the name column's width in normal use. At the old 570 it
    left the name 126px, which rendered "Resilience of the Unkillable Demon
    King" as "Resilience of the…". The floor below is the width of the widest
    of the 164 real skill names in this player's own archives, capped at
    NAME_MAX_CHARS; the rail minimum has to be at least that plus the fixed
    cells and their gaps.

    The name is capped in CHARACTERS before it is ever elided in pixels,
    because one 39-character passive name should not set the width of the
    whole rail — the roster has to live in the rest of the page, and every
    pixel the longest name claims is a pixel the player list does not get.

    The width itself is measured by the probe, not here — this headless Qt
    reports zero font families, so a fontMetrics() call in a unit test measures
    a fallback face."""
    rail_min = _rail_minimum_width()
    fixed = (20                      # the icon cell
             + av.TYPE_COLUMN_WIDTH + 80 + 36 + 52 + 38 + SR.AVG_WIDTH
             + 6 * 6)               # the gaps between seven cells
    assert rail_min - 12 - fixed >= WIDEST_REAL_SKILL_NAME_PX, (
        f"a {rail_min}px rail leaves the name {rail_min - 12 - fixed}px, "
        f"under the {WIDEST_REAL_SKILL_NAME_PX}px the longest real skill name "
        "needs")


def _row_with_type(affinity, hits=None, skill_id="Sword_Base_Attack",
                   name="Attack"):
    row = SkillRow(density="detailed")
    row.set_skill(skill_id, name)
    sp = SkillParse(skill_id=skill_id, name=name)
    sp.damage, sp.hit_count = 1_284_500.0, 412
    sp.min_hit, sp.max_hit = 2100.0, 9800.0
    sp.affinity_damage = affinity
    sp.affinity_hits = hits
    row.update(sp, share_pct=55.0)
    return row


def _qss_color(widget) -> str:
    """The colour in a widget's stylesheet, as #rrggbb.

    Read out of the stylesheet rather than a property because the row paints
    these cells with a per-widget stylesheet, and QSS is the only record of
    what was actually asked for."""
    for part in widget.styleSheet().split(";"):
        if "color:" in part:
            return part.split("color:")[-1].strip()
    return ""


def test_the_share_number_is_coloured_by_the_damage_type():
    """The % wears its school's colour, not the skill's.

    It used to be `theme.skill_color(skill_id)`, which put a third colour on
    the row and made the number look like it belonged to the skill's hue rather
    than to the TYPE cell sitting two columns away saying what kind of damage
    the percentage is a percentage OF.

    The rail used to set that skill colour immediately after `update()`, so the
    fix is only real if the row owns the decision — hence asserting the colour
    survives a plain `update()` with nothing set afterwards."""
    row = _row_with_type({"Fire": 600.0, "Physical": 400.0},
                         {"Fire": 180, "Physical": 120})
    assert _qss_color(row._share_lbl) == _qss_color(row._type_lbl)
    assert _qss_color(row._share_lbl) == item_labels.affinity_color("Fire")


def test_an_untagged_skill_keeps_the_skill_hue_on_its_share():
    """No type was read, so there is nothing to colour by.

    Falling back to the dim grey the TYPE cell uses would make every untagged
    row look like a broken one; falling back to the skill's own hue keeps the
    column readable and is the honest answer — the number is real, the type is
    simply unknown."""
    row = _row_with_type({}, {})
    assert row._type_lbl.text() == ""
    assert _qss_color(row._share_lbl) == theme.skill_color("Sword_Base_Attack")
    assert _qss_color(row._share_lbl) != _qss_color(row._type_lbl)


def test_the_overlay_keeps_its_bar_and_its_skill_hue():
    """The compact row has no TYPE cell, so it must not try to have a % hue.

    The DPS overlay draws a progress bar rather than a number, and every
    overlay row would otherwise be tinted by a damage type the overlay never
    shows — a colour with no key anywhere on that window."""
    row = SkillRow(density="compact")
    row.set_skill("Sword_Base_Attack", "Attack")
    sp = SkillParse(skill_id="Sword_Base_Attack", name="Attack")
    sp.damage, sp.hit_count = 100.0, 4
    sp.affinity_damage = {"Fire": 100.0}
    row.update(sp, share_pct=50.0)
    assert row._type_lbl is None
    assert row._share_lbl is None
    assert row._bar is not None


def test_a_long_skill_name_is_capped_before_it_is_elided():
    """Characters, not pixels, and the full name stays in the tooltip.

    Pixel eliding alone is what made the rail 732px wide: the layout sized
    itself around one 39-character name. Capping first means the cap, not the
    longest name in the game, decides how wide the table has to be — and the
    reader still gets the whole name on hover."""
    long_name = "Resilience of the Unkillable Demon King"
    row = SkillRow(density="detailed")
    row.set_skill("GA_Demon_Passive", long_name)
    shown = row._name_lbl._full
    # The kind is a BADGE beside the name now, not a suffix inside it (reported
    # live 2026-10-03), which also means the cap applies to the name alone with
    # no tag to protect. The badge still says what kind of thing this is.
    assert row._tag_lbl.text(), "the row carries a kind label"
    capped = shown
    # At most the cap, not exactly: the slice is rstripped first so a name cut
    # on a space does not come back as "Unkillable …".
    assert len(capped) <= SR.NAME_MAX_CHARS, (capped, len(capped))
    assert capped.endswith("…"), capped
    assert capped not in long_name           # it really was truncated
    assert long_name.startswith(capped[:-1])  # nothing but the tail was lost
    sp = SkillParse(skill_id="GA_Demon_Passive", name=long_name)
    sp.damage, sp.hit_count = 1_284_500.0, 412
    sp.min_hit, sp.max_hit = 2100.0, 9800.0
    sp.affinity_damage = {"Physical": 1_284_500.0}
    row.update(sp, share_pct=55.0)
    assert long_name in row.toolTip(), "the full name must stay one hover away"


def test_the_type_column_drops_only_when_forced_below_the_rail_minimum():
    """QSplitter can push a pane under its child's minimum when the window
    itself is too small. The column gives way there, and the type is still in
    the row tooltip.

    What it does NOT promise is a readable name at 470px, and this test used
    to promise one. The rail's minimum is now 732, so a 470px rail is a state
    the app cannot reach; six numeric cells plus a name do not fit in 470px by
    any arrangement, and the widths that got the name there came out of cells
    that were too small for their own contents. What has to hold at EVERY
    width is the row not overflowing — that is the next test."""
    rail_min = _rail_minimum_width()
    for rail_w in (470, 520, 550):
        rail, container = _rail_at(rail_w)
        try:
            assert rail_w < rail_min, "these widths are no longer forced-narrow"
            row = rail._cp_rail_skill_widgets["Sword_Base_Attack"]
            assert row._type_lbl.isHidden(), f"column shown at {rail_w}"
            assert rail.cp_skills_type_hdr.isHidden(), f"header shown at {rail_w}"
        finally:
            _close(container)


def test_the_avg_hit_cell_stays_inside_the_table_at_every_width():
    """The bug the user reported, as a test.

    A row is a plain QHBoxLayout of FIXED-width cells. When one cell's content
    is wider than its fixed width it does not shrink — the layout overflows and
    the rightmost column, Avg Hit, is painted past the scroll viewport and cut
    off at its edge. It looked like a rendering bug; it was arithmetic.

    So this does not check that the text FITS the cell (headless Qt measures a
    fallback face, and a font-dependent assertion here is a coin flip). It
    checks the geometry that actually broke: the cell's right edge is inside
    the viewport at every width the rail can be given, narrow and wide.

    The sweep starts at the rail's own minimum because that is the narrowest
    it can be in use — the page auto-hides the rail below 1040px of page, and
    the splitter will not take a pane under its minimum. Asserting the
    invariant at 400px would be asserting a state the app cannot reach, and
    the arithmetic that keeps the minimum honest is the name-budget test."""
    rail_min = _rail_minimum_width()
    for rail_w in (rail_min, rail_min + 40, 800, 860, 900):
        rail, container = _rail_at(rail_w)
        try:
            row = rail._cp_rail_skill_widgets["Sword_Base_Attack"]
            view = rail.cp_skills_scroll.viewport()
            right = row._avg_lbl.mapTo(view, QtCore.QPoint(0, 0)).x() \
                + row._avg_lbl.width()
            assert right <= view.width(), (
                f"at {rail_w}px the Avg Hit cell ends at {right}px, past the "
                f"{view.width()}px viewport — the tail is cut off")
            assert row._layout.minimumSize().width() <= view.width(), rail_w
        finally:
            _close(container)


def test_every_type_caption_fits_its_cell():
    """QLabel does not clip: text wider than the cell paints straight over the
    neighbouring column. The split case is where that bites first — "Fire +2"
    is the longest string the column ever holds.

    The width is PINNED rather than measured here. This headless Qt reports
    zero font families (QFontDatabase.families() == []), so a fontMetrics()
    call in a unit test measures a fallback face and came out at 88px for
    "Physical" against a real 41px — a test that reports a break that is not
    one is worse than no test. 68 is the worst case measured with the shipped
    font, from ai/workspace/dmgtype/probe_split_fit.py; re-run that probe
    rather than trusting a number typed here.
    """
    WORST_CAPTION_PX = 68      # "threshold +1" at 11px/700, measured
    assert av.TYPE_COLUMN_WIDTH >= WORST_CAPTION_PX, (
        f"{av.TYPE_COLUMN_WIDTH}px cell cannot hold a {WORST_CAPTION_PX}px "
        "caption; the split case would paint over TOTAL")

    # the captions themselves are still whatever dominant_type says they are
    row = SkillRow(density="detailed")
    row.set_types(av.affinity_rows({"threshold": 600.0, "Physical": 400.0}, 1000.0))
    assert row.type_caption() == "threshold +1"
    assert row._type_lbl.maximumWidth() == av.TYPE_COLUMN_WIDTH


def test_the_type_header_and_the_type_cell_line_up():
    """Header and row lay the column out in two places; a drift would leave the
    caption over the wrong column."""
    rail, container = _rail_at(860)
    try:
        row = rail._cp_rail_skill_widgets["Sword_Base_Attack"]
        box = rail.cp_skills_box
        hdr_x = rail.cp_skills_type_hdr.mapTo(box, QtCore.QPoint(0, 0)).x()
        row_x = row._type_lbl.mapTo(box, QtCore.QPoint(0, 0)).x()
        assert hdr_x == row_x, (hdr_x, row_x)
        assert rail.cp_skills_type_hdr.maximumWidth() == av.TYPE_COLUMN_WIDTH
        assert row._type_lbl.maximumWidth() == av.TYPE_COLUMN_WIDTH
    finally:
        _close(container)


def test_the_avg_hit_cell_stays_bounded_at_every_damage_scale():
    """QLabel does not clip, so a cell whose text outgrows it loses the tail at
    the scroll area's edge rather than the row gaining a scrollbar. The range
    endpoints are abbreviated for exactly this reason.

    Check the cell's FULL text, not what it renders: this headless Qt has no
    fonts, so the elided text depends on a fallback face and asserting on it
    would test the environment rather than the code.
    """
    MAX_CHARS = 20          # ~5px per char at 11px monospace = the 120px cell
    row = SkillRow(density="detailed")
    for avg, lo, hi in ((71.0, 50.0, 84.0), (452.0, 452.0, 452.0),
                        (4520.0, 12000.0, 41000.0),
                        (45200.0, 120000.0, 410000.0),
                        (4520000.0, 1200000.0, 4100000.0)):
        sp = SkillParse(skill_id="X", name="X")
        sp.damage, sp.hit_count = avg * 4, 40
        sp.min_hit, sp.max_hit = lo, hi
        sp.affinity_damage = {"Physical": avg}
        row.update(sp, 0.5)
        full = row._avg_lbl._full
        assert len(full) <= MAX_CHARS, (full, len(full))
        assert "[" in full and "]" in full, full


def test_the_avg_hit_range_is_abbreviated_only_where_it_needs_to_be():
    """Small hits stay exact; a five-figure range becomes 12k-41k so the cell
    keeps fitting. Tested on the FORMATTER, not the rendered label, because
    the rendered label elides to the font this environment happens to have.
    The exact values stay in the row tooltip either way."""
    span = SkillRow._fmt_span
    assert span(50.0) == "50"
    assert span(8400.0) == "8,400"
    assert span(9999.0) == "9,999"
    assert span(12000.0) == "12k"
    assert span(410000.0) == "410k"
    assert span(1200000.0) == "1.2m"
    assert span(4100000.0) == "4.1m"
    assert span(float("inf")) == "—"
    # The billions tier and the ".0" trim. Both exist for the same reason: an
    # abbreviation wider than the digits it replaced is the failure this whole
    # helper was written to prevent, and "1000.0m" / "88.0m" are exactly that.
    assert span(12000000.0) == "12m"
    assert span(120000000.0) == "120m"
    assert span(1500000.0) == "1.5m"
    assert span(1200000000.0) == "1.2b"
    assert span(12000000000.0) == "12b"

    row = SkillRow(density="detailed")
    sp = SkillParse(skill_id="X", name="X")
    sp.damage, sp.hit_count = 452000.0, 40
    sp.min_hit, sp.max_hit = 120000.0, 4100000.0
    sp.affinity_damage = {"Physical": 452000.0}
    row.update(sp, 0.5)
    assert row._avg_lbl._full == "11,300 [120k–4.1m]"
    # the exact range is still one hover away
    assert "Min: <b>120,000</b>" in row.toolTip()
    assert "Max: <b>4,100,000</b>" in row.toolTip()


def test_the_share_header_and_cell_are_the_same_width():
    """SHARE is the one cell whose width is easy to change in one place and
    forget in the other, which slides every column after it out of alignment."""
    rail, container = _rail_at(860)
    try:
        box = rail.cp_skills_box
        # The header row by CONTENT, not index: the skills box once carried a
        # "Weapon: ..." label at index 0, and an index-based lookup slid every
        # column check one slot when it was removed (2026-10-03).
        def _header_row():
            lay = box.layout()
            for i in range(lay.count()):
                sub = lay.itemAt(i).layout()
                if sub is None:
                    continue
                for j in range(sub.count()):
                    w = sub.itemAt(j).widget()
                    if w is not None and w.text() == "%":
                        return sub
            return None
        hdr = _header_row()
        assert hdr is not None, "the % header row vanished from the skills box"
        share_hdr = [hdr.itemAt(i).widget() for i in range(hdr.count())
                     if hdr.itemAt(i).widget() is not None
                     and hdr.itemAt(i).widget().text() == "%"]
        assert len(share_hdr) == 1
        row = rail._cp_rail_skill_widgets["Sword_Base_Attack"]
        assert share_hdr[0].maximumWidth() == row._share_lbl.maximumWidth()
        hdr_x = share_hdr[0].mapTo(box, QtCore.QPoint(0, 0)).x()
        row_x = row._share_lbl.mapTo(box, QtCore.QPoint(0, 0)).x()
        assert hdr_x == row_x, (hdr_x, row_x)
    finally:
        _close(container)


def test_the_avg_hit_header_and_cell_line_up():
    """The header says "Avg Hit" now, and it has to sit over the cell that
    carries the range — a rename is how a column silently drifts."""
    rail, container = _rail_at(860)
    try:
        box = rail.cp_skills_box
        # By content, not index - see the SHARE-width test's note.
        def _header_row():
            lay = box.layout()
            for i in range(lay.count()):
                sub = lay.itemAt(i).layout()
                if sub is None:
                    continue
                for j in range(sub.count()):
                    w = sub.itemAt(j).widget()
                    if w is not None and w.text() == "Avg Hit":
                        return sub
            return None
        hdr = _header_row()
        assert hdr is not None, "the header no longer names the column"
        heads = [hdr.itemAt(i).widget() for i in range(hdr.count())]
        avg_hdr = [w for w in heads if w is not None and w.text() == "Avg Hit"]
        row = rail._cp_rail_skill_widgets["Sword_Base_Attack"]
        assert avg_hdr[0].maximumWidth() == row._avg_lbl.maximumWidth()
        assert (avg_hdr[0].mapTo(box, QtCore.QPoint(0, 0)).x()
                == row._avg_lbl.mapTo(box, QtCore.QPoint(0, 0)).x())
    finally:
        _close(container)


# --- PD / MD merge (the Test Dummy HUD split) ------------------------------------

def test_pd_md_split_merges_schools():
    pd, md, untagged = av.pd_md_split(
        {"Physical": 600.0, "Raw": 100.0, "Fire": 200.0, "Magic": 50.0, "Light": 25.0},
        975.0)
    assert (pd, md, untagged) == (700.0, 275.0, 0.0)


def test_pd_md_split_folds_unknown_tags_into_md():
    pd, md, untagged = av.pd_md_split(
        {"Physical": 100.0, "Chaos": 300.0, "threshold": 50.0}, 450.0)
    assert (pd, md, untagged) == (100.0, 350.0, 0.0)


def test_pd_md_split_shows_the_untagged_remainder():
    pd, md, untagged = av.pd_md_split({"Physical": 600.0, "Fire": 300.0}, 1000.0)
    assert (pd, md, untagged) == (600.0, 300.0, 100.0)


def test_pd_md_split_empty_when_nothing_classified():
    assert av.pd_md_split({}, 0.0) == (0.0, 0.0, 0.0)
    assert av.pd_md_split(None, 250.0) == (0.0, 0.0, 0.0)
    assert av.pd_md_split({"Physical": 600.0}, 600.0) == (600.0, 0.0, 0.0)# --- the compact TYPE cell (the HUD rows' per-skill damage type) ------------

def _pdmd_skill(**buckets) -> SkillParse:
    sp = SkillParse(skill_id="Sword_Base_Attack", name="Attack")
    sp.damage, sp.hit_count = 1000.0, 12
    sp.affinity_damage = dict(buckets)
    sp.affinity_hits = {k: 1 for k in buckets}
    return sp



def test_compact_row_grows_no_type_cell_by_default():
    """No opt-in, no cell: a compact row built without show_type has no
    damage-type column at all."""
    row = SkillRow(density="compact")
    assert row._type_lbl is None
    row.update(_pdmd_skill(Physical=900.0, Fire=100.0), 90.0)
    assert row._type_lbl is None


def test_compact_rows_have_exactly_one_damage_type_cell():
    """One column per row, by design: the school name already names the
    merged side (Physical is PD, Fire/Magic is MD), so the old second PD/MD
    tag beside it said the same thing twice for 26px of a HUD row."""
    row = SkillRow(density="compact", show_type=True)
    # 20/920 is under SPLIT_MIN_PCT, so this is one school, named outright.
    row.update(_pdmd_skill(Physical=900.0, Fire=20.0), 90.0)
    assert not hasattr(row, "_pdmd_lbl")
    assert row.type_caption() == "Physical"

    row.update(_pdmd_skill(Fire=800.0, Physical=20.0), 90.0)
    assert row.type_caption() == "Fire"


def test_compact_type_cell_names_a_genuine_split_rather_than_the_top_school():
    """Same rule the rail's TYPE column keeps: a skill carrying a second
    school reads "Physical +1" instead of claiming to be pure physical."""
    row = SkillRow(density="compact", show_type=True)
    row.update(_pdmd_skill(Physical=900.0, Fire=100.0), 90.0)
    assert row.type_caption() == "Physical +1"


def test_compact_type_cell_stays_blank_until_classified():
    row = SkillRow(density="compact", show_type=True)
    row.update(_pdmd_skill(), 90.0)
    assert row._type_lbl.text() == ""

