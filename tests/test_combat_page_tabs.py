"""Offscreen tests that the Combat & DPS page's breakdown tabs
(Damage / Healing / Skill Performance) each render genuinely different
content — not just a bar color swap.

Since the v10 UI redesign the page splits this work across two widgets:

* ``_PlayerCardWidget`` — the compact class-board card. ``update_stats()``
  picks the metric (damage / heals / combined), the live rate, the share
  bar, and the top-skill chip per tab.
* ``SkillRow`` — the shared per-skill row used by the skills rail (detailed
  density) and the overlay (compact density). Its ``update(sp, share_pct,
  mode)`` renders the damage / heal / both channels with aligned columns.

These drive both components directly with crafted PlayerParse objects: a
melee DPS hero, a healer, and a mixed one. Each tab must change the player's
stat line, the channel basis, and the skill ordering.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6 import QtCore, QtTest, QtWidgets  # noqa: E402

from farever_companion.core.dps_tracker import PlayerParse, SkillParse  # noqa: E402
from farever_companion.ui.pages.combat_page import _PlayerCardWidget  # noqa: E402
from farever_companion.ui.combat import _ranked_skill_total  # noqa: E402
from farever_companion.ui.skill_row import SkillRow  # noqa: E402
from farever_companion.ui.combat.timeline_plotter import _CombatTimelinePlotter  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def _qapp():
    """One offscreen QApplication for the whole module (widgets need one).

    Without this the module hard-aborts the interpreter (0xC0000409) when run
    on its own: building a QWidget with no QApplication kills the process
    rather than failing a test. A full-suite run only survives it because an
    earlier module happens to leave a QApplication alive.
    """
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _mk_player(name: str, dmg: float, hits: int, heals: float,
               skills: list[tuple[str, str, float, int]],
               heal_skills: list[tuple[str, str, float, int]] | None = None) -> PlayerParse:
    """Build a PlayerParse with damage-channel and heal-channel rows."""
    p = PlayerParse(name=name, is_me=False)
    p.total_damage = dmg
    p.hit_count = hits
    p.heals = heals
    p.heal_count = sum(c for _, _, _, c in (heal_skills or []))
    for sid, sname, amt, shits in skills:
        sp = SkillParse(skill_id=sid, name=sname)
        sp.damage = amt
        sp.hit_count = shits
        sp.max_hit = amt
        sp.min_hit = amt
        p.skills[sid] = sp
    for sid, sname, amt, shits in (heal_skills or []):
        if sid not in p.skills:
            p.skills[sid] = SkillParse(skill_id=sid, name=sname)
        sp = p.skills[sid]
        sp.heals = amt
        sp.heal_count = shits
        sp.max_heal = amt
        sp.min_heal = amt
    return p


def _card(name: str = "Me") -> _PlayerCardWidget:
    c = _PlayerCardWidget(name, is_me=False)
    c.show()
    QtWidgets.QApplication.processEvents()
    return c


def _detailed_row(skill_id: str, name: str) -> SkillRow:
    """A skills-rail-density row ready to render one skill."""
    row = SkillRow(density="detailed")
    row.set_skill(skill_id, name)
    return row


# A DPS warrior: big melee damage, no heals, one dominant skill.
def _dps() -> PlayerParse:
    return _mk_player("War", 9000.0, 60, 0.0,
                      [("Sword_Spin", "Whirlwind", 5000.0, 20),
                       ("Axe_Base_Attack", "Axe Base Attack", 4000.0, 40)])


# A healer: no damage, big heals through one channeled skill (stored in the
# HEAL channel - the old bug stuffed this into .damage, which is what made
# the healing-tab rows nonsense).
def _healer() -> PlayerParse:
    return _mk_player("Priest", 0.0, 0, 6000.0, [],
                      heal_skills=[("Holy_Beam", "Holy Beam", 6000.0, 24)])


# A hybrid: Judgment deals damage to the mob AND heals the ally - ONE skill_id
# carrying both channels, exactly like the real game emits it.
def _hybrid() -> PlayerParse:
    return _mk_player("Paladin", 500.0, 4, 300.0,
                      [("Judgment", "Judgment", 500.0, 4)],
                      heal_skills=[("Judgment", "Judgment", 300.0, 3)])


# --- card tabs: metric, rate, share, top-skill chip --------------------------

def test_damage_tab_emphasizes_damage_stat_and_sorts_by_damage():
    c = _card()
    c.update_stats(0, _dps(), 60.0, group_dmg=9000.0, group_heals=0.0,
                   tab="damage")
    assert c.total_lbl.text().startswith("9,000")  # TOTAL column = damage
    assert c.rate_lbl.text().endswith("/s")
    assert c.bar.value() == 100              # 9000/9000 share bar
    assert "Heal" not in c.total_lbl.text()  # nothing to heal -> not shown
    assert c.top_skill_lbl.text() == "✦ Whirlwind"  # top damage skill chip


def test_healing_tab_emphasizes_heals_not_damage():
    c = _card()
    # healer card on the healing tab: heals front and center
    c.update_stats(0, _healer(), 60.0, group_dmg=0.0, group_heals=6000.0,
                   tab="healing")
    assert c.total_lbl.text().startswith("6,000")  # TOTAL column = heals
    assert c.rate_lbl.text().endswith("/s")
    assert "Dmg" not in c.total_lbl.text()   # zero damage -> omitted
    assert "Damage" not in c.total_lbl.text()

    # a DPS player on the healing tab still ranks under healing, but its
    # damage skill stays visible as the top-skill chip
    c2 = _card()
    c2.update_stats(1, _dps(), 60.0, group_dmg=9000.0, group_heals=6000.0,
                    tab="healing")
    assert c2.total_lbl.text().startswith("0")   # 0 heals -> leading 0
    assert c2.top_skill_lbl.text() == "✦ Whirlwind"


def test_skills_tab_shows_top_skill_and_total():
    c = _card()
    c.update_stats(0, _dps(), 60.0, group_dmg=9000.0, group_heals=0.0,
                   tab="skills")
    assert c.total_lbl.text().startswith("9,000")  # combined D+H total
    assert c.rate_lbl.text().endswith("/s")
    assert c.top_skill_lbl.text() == "✦ Whirlwind"


def test_damage_and_skills_tabs_differ_in_total():
    """Damage and skills tabs render different totals for one player."""
    c_dmg = _card()
    c_sk = _card()
    p = _hybrid()
    c_dmg.update_stats(0, p, 60.0, 500.0, 300.0, tab="damage")
    c_sk.update_stats(0, p, 60.0, 500.0, 300.0, tab="skills")
    # damage tab counts the damage channel only; skills tab combines D+H
    assert c_sk.total_lbl.text().startswith("800")
    assert c_dmg.total_lbl.text().startswith("500")
    # both tabs lead with the same top skill chip
    assert c_dmg.top_skill_lbl.text() == c_sk.top_skill_lbl.text() == "✦ Judgment"


def test_player_card_uses_aligned_total_and_rate_columns():
    """The player header uses TOTAL and RATE columns with a share bar."""
    c = _card()
    p = _mk_player("War", 9000.0, 60, 2000.0,
                   [("Sword_Spin", "Whirlwind", 5000.0, 20)],
                   heal_skills=[("Holy_Beam", "Holy Beam", 2000.0, 8)])
    p.hero_class = "warrior"
    c.update_stats(0, p, 60.0, 9000.0, 2000.0, tab="damage")
    assert c.r_lbl.text() == "#1"
    assert c.total_lbl.text() == "9,000 (100.0%)"  # TOTAL + share columns
    assert c.rate_lbl.text().endswith("/s")
    assert "warrior" in c.cls_rank_lbl.text().lower()  # class chip next to name
    assert c.bar.value() == 100                   # share lives in the bar


# --- shared SkillRow: aligned columns + channel basis ------------------------

def test_skill_row_uses_aligned_columns_not_prose():
    """Skill rows use aligned columns plus a share bar, not prose."""
    row = _detailed_row("Sword_Spin", "Whirlwind")
    sp = _dps().skills["Sword_Spin"]
    row.update(sp, share_pct=5000 / 9000 * 100, mode="damage")
    st = row.findChild(QtWidgets.QLabel, "SkillTotal")
    sh = row.findChild(QtWidgets.QLabel, "SkillHits")
    sc = row.findChild(QtWidgets.QLabel, "SkillCrit")
    sa = row.findChild(QtWidgets.QLabel, "SkillAvg")
    share = row.findChild(QtWidgets.QLabel, "SkillShare")
    assert st is not None and st.text() == "5,000"
    assert sh is not None and sh.text() == "20"
    assert sc is not None and sc.text() == "—"          # no crits recorded
    assert sa is not None and "250" in sa.text()          # avg [min–max]
    assert share is not None and share.text() == "56%"    # 5000 of 9000
    # prose artifacts from the old single-string row are gone
    assert "(55%) · 20 hits" not in st.text()


def test_a_sub_one_percent_skill_does_not_report_a_full_share():
    """``share_pct`` is a PERCENTAGE. ``update()`` used to guess that a value
    ``<= 1.0`` was a ratio instead, which cannot tell a 0.99% share from a 0.99
    ratio: a skill doing 0.99% of a player's damage drew its bar at 99% wide.
    Archived fights are where it showed, since they keep every proc and status
    and the rail fills with sub-1% skills that should be slivers.
    """
    total = 5745.0
    row = _detailed_row("Sword_Proc", "Flurry Proc")
    for damage, want in ((5745.0, "100%"), (2872.0, "50%"), (287.0, "5%"),
                         (57.0, "1%"), (5.0, "0%"), (2.0, "0%")):
        sp = SkillParse(skill_id="Sword_Proc", name="Flurry Proc")
        sp.damage, sp.hit_count = damage, 1
        row.update(sp, share_pct=sp.share_pct(total), mode="damage")
        assert row._share_lbl.text() == want, (damage, row._share_lbl.text())


def test_the_share_column_states_the_number_not_a_bar():
    """A filled bar with empty groove beside it reads as two segments, and next
    to a TOTAL column the number is the consistent thing. The compact overlay
    row keeps its bar — a proportion to glance at in a narrow window."""
    detailed = _detailed_row("X", "X")
    assert detailed._share_lbl is not None and detailed._bar is None
    compact = SkillRow(density="compact")
    assert compact._bar is not None and compact._share_lbl is None


def test_the_share_text_and_the_tooltip_never_disagree():
    """The bug showed as a bar at 99% next to a text saying 1%: the text used
    the percentage and the bar used the misread ratio. They read the same
    number now."""
    row = _detailed_row("Sword_Proc", "Flurry Proc")
    sp = SkillParse(skill_id="Sword_Proc", name="Flurry Proc")
    sp.damage, sp.hit_count = 57.0, 1
    row.update(sp, share_pct=sp.share_pct(5745.0), mode="damage")
    assert row._share_lbl.text() == "1%"
    assert f"({sp.share_pct(5745.0):.1f}%)" in row.toolTip()


def test_skill_row_healing_channel_amounts_and_avg():
    """Healing rows fill the same columns from the heal channel."""
    row = _detailed_row("Holy_Beam", "Holy Beam")
    sp = _healer().skills["Holy_Beam"]
    row.update(sp, share_pct=100.0, mode="healing")
    st = row.findChild(QtWidgets.QLabel, "SkillTotal")
    sh = row.findChild(QtWidgets.QLabel, "SkillHits")
    sc = row.findChild(QtWidgets.QLabel, "SkillCrit")
    sa = row.findChild(QtWidgets.QLabel, "SkillAvg")
    assert st.text() == "6,000" and sh.text() == "24"
    assert sc.text() == "—"
    assert "250" in sa.text()                 # 6000/24 avg


def test_healing_tab_lists_only_real_heal_skills():
    """The healing view draws from the heal channel only."""
    # a player who did damage AND healing: only the heal channel ranks here
    p = _mk_player("WarPriest", 8000.0, 40, 2000.0,
                   [("Sword_Spin", "Whirlwind", 8000.0, 40)],
                   heal_skills=[("Holy_Beam", "Holy Beam", 2000.0, 8)])
    assert [s.skill_id for s in p.ranked_heal_skills()] == ["Holy_Beam"]
    assert [s.skill_id for s in p.ranked_skills()] == ["Sword_Spin"]


def test_damage_tab_excludes_heal_only_skills():
    """A heal-only player ranks zero skills on the damage view."""
    p = _healer()
    assert p.ranked_skills() == []      # heal-only -> nothing on damage tab
    assert [s.skill_id for s in p.ranked_heal_skills()] == ["Holy_Beam"]

    # and a heal-only skill renders as zero in damage mode, never merged
    row = _detailed_row("Holy_Beam", "Holy Beam")
    row.update(p.skills["Holy_Beam"], share_pct=0.0, mode="damage")
    st = row.findChild(QtWidgets.QLabel, "SkillTotal")
    assert st is not None and st.text() == "0"


def test_hybrid_skill_shows_damage_on_damage_tab_and_heal_on_healing_tab():
    """A hybrid skill shows damage on damage mode, heals on healing mode."""
    sp = _hybrid().skills["Judgment"]

    # damage mode: only the damage channel, with the damage total
    row_d = _detailed_row("Judgment", "Judgment")
    row_d.update(sp, share_pct=100.0, mode="damage")
    st = row_d.findChild(QtWidgets.QLabel, "SkillTotal")
    sh = row_d.findChild(QtWidgets.QLabel, "SkillHits")
    assert st.text() == "500" and sh.text() == "4"
    assert "H 300" not in st.text()

    # healing mode: same skill_id, heal channel shown instead
    row_h = _detailed_row("Judgment", "Judgment")
    row_h.update(sp, share_pct=100.0, mode="healing")
    st2 = row_h.findChild(QtWidgets.QLabel, "SkillTotal")
    sh2 = row_h.findChild(QtWidgets.QLabel, "SkillHits")
    assert st2.text() == "300" and sh2.text() == "3"

    # skills mode: hybrid shows BOTH channels on the one row
    row_b = _detailed_row("Judgment", "Judgment")
    row_b.update(sp, share_pct=100.0, mode="both")
    st3 = row_b.findChild(QtWidgets.QLabel, "SkillTotal")
    assert "D 500" in st3.text() and "H 300" in st3.text()


def test_skill_row_shares_against_parent_total():
    """The share is stated against the parent (tab) total."""
    row = _detailed_row("Sword_Spin", "Whirlwind")
    sp = _dps().skills["Sword_Spin"]
    row.update(sp, share_pct=5000 / 9000 * 100, mode="damage")
    share = row.findChild(QtWidgets.QLabel, "SkillShare")
    # Whirlwind = 5000 of 9000 total damage
    assert share is not None and share.text() == "56%"


def test_ranked_skills_resort_when_rankings_flip():
    """Skill rankings re-sort live when rankings flip mid-fight."""
    # first update: Sword_Spin (5000) ranks above Axe (4000)
    p1 = _mk_player("War", 9000.0, 60, 0.0,
                    [("Sword_Spin", "Whirlwind", 5000.0, 20),
                     ("Axe_Base_Attack", "Axe Base Attack", 4000.0, 40)])
    assert [s.skill_id for s in _ranked_skill_total(p1)][:2] == \
        ["Sword_Spin", "Axe_Base_Attack"]

    # same player, next tick: Axe (9000) now beats Sword (1000) -> reorders
    p2 = _mk_player("War", 10000.0, 60, 0.0,
                    [("Sword_Spin", "Whirlwind", 1000.0, 20),
                     ("Axe_Base_Attack", "Axe Base Attack", 9000.0, 40)])
    assert [s.skill_id for s in _ranked_skill_total(p2)][:2] == \
        ["Axe_Base_Attack", "Sword_Spin"]

    # and the re-rendered row reflects the new values
    row = _detailed_row("Axe_Base_Attack", "Axe Base Attack")
    row.update(p2.skills["Axe_Base_Attack"], share_pct=9000 / 10000 * 100,
               mode="damage")
    st = row.findChild(QtWidgets.QLabel, "SkillTotal")
    assert st is not None and st.text() == "9,000"


# --- weapon family linking ------------------------------------------------

def test_weapon_family_of_maps_skill_tokens():
    """Skill ids resolve to their weapon family; cast skills get none."""
    from farever_companion.core.dps_tracker import weapon_family_of
    assert weapon_family_of("Axe_Base_Attack") == "Axe"
    assert weapon_family_of("Sword_Spin") == "Sword"
    assert weapon_family_of("Shield_OrbitWater_S1_OrbStatus") == "Shield"
    assert weapon_family_of("DualAxes_Boomerang_Skill1") == "Dual Axes"
    assert weapon_family_of("Sunlight") is None      # pure cast -> no weapon
    assert weapon_family_of("") is None


def test_weapons_for_skill_reverse_lookup():
    """The catalog reverse lookup maps a skill id back to the weapons that
    grant it; pure-cast skills resolve to nothing."""
    from farever_companion.data.items import weapons_for_skill
    ws = weapons_for_skill("Sword_Swarm_Skill1")
    assert [w["id"] for w in ws] == ["Sword_Swarm"]
    assert ws[0]["name"] == "Beefury, Blessed Blade of the Farseeker"
    assert ws[0]["type"] == "Sword"
    assert weapons_for_skill("Sunlight") == ()   # pure cast -> no weapon
    assert weapons_for_skill("") == ()


def test_weapons_used_resolves_weapon_names():
    """The loadout shows real weapon NAMES (not family types) for the local
    player, and falls back to the family only when the catalog has no match."""
    from farever_companion.core.dps_tracker import (CombatSession,
                                                    weapons_used)
    s = CombatSession("Test", kind="overall")
    me = _mk_player("War (You)", 9000.0, 60, 0.0,
                    [("Sword_Swarm_Skill1", "Swarm Cleave", 5000.0, 20),
                     ("Sunlight", "Sunlight", 4000.0, 40)])
    me.is_me = True
    ally = _mk_player("Brit", 500.0, 5, 0.0,
                      [("Bow_Base_Attack", "Bow Base Attack", 500.0, 5)])
    s.players[me.name] = me
    s.players[ally.name] = ally
    # ally's bow must NOT appear - the loadout is the local player's weapons
    assert weapons_used(s) == [
        {"name": "Beefury, Blessed Blade of the Farseeker", "type": "Sword"}]

    # no catalog match -> family name falls back so the line never goes empty
    s2 = CombatSession("Test", kind="overall")
    m2 = _mk_player("War (You)", 100.0, 2, 0.0,
                    [("Sword_Spin", "Sword Spin", 100.0, 2)])
    m2.is_me = True
    s2.players[m2.name] = m2
    assert weapons_used(s2) == [{"name": "Sword", "type": "Sword"}]


def test_weapons_used_collapses_ambiguous_shared_skill():
    """A generic skill shared by many catalog weapons (e.g. a family's base
    attack) must NOT list every weapon — it shows the family once. Only a
    signature skill that maps to exactly one weapon shows that weapon's name."""
    from farever_companion.core.dps_tracker import (CombatSession,
                                                    weapons_used)
    # "Sword_Base_Attack" is shared by Beefury / Glory / Light Practice Sword
    # in the catalog, so it must resolve to the "Sword" family, not all three.
    s = CombatSession("Test", kind="overall")
    me = _mk_player("War (You)", 9000.0, 60, 0.0,
                    [("Sword_Base_Attack", "Sword Base Attack", 6000.0, 30),
                     ("Sword_Swarm_Skill1", "Swarm Cleave", 3000.0, 10)])
    me.is_me = True
    s.players[me.name] = me
    got = weapons_used(s)
    names = [w["name"] for w in got]
    assert sorted(names) == sorted(
        ["Sword", "Beefury, Blessed Blade of the Farseeker"])
    assert len(got) == 2


def test_compare_rail_accepts_boss_session_group_damage():
    """refresh_rail_compare must read a session's GROUP damage (sum of the
    players' total_damage), not a per-player total_damage — accessing
    boss_session.total_damage used to raise AttributeError every refresh."""
    from farever_companion.core.dps_tracker import CombatSession
    from farever_companion.ui.combat.compare_view import refresh_rail_compare

    class _Tracker:
        boss_session = CombatSession("Boss", kind="boss")

    class _Page:
        def _get_active_tracker(self):
            return _Tracker()

        cp_cmp_insight = QtWidgets.QLabel("")
        cp_plotter = None
        # Combo boxes the rail syncs (defaults only resolve if present).
        cp_compare_combo_a = QtWidgets.QComboBox()
        cp_compare_combo_b = QtWidgets.QComboBox()
        _cp_compare_a = ""
        _cp_compare_b = ""

    # A boss session with players + damage triggers the boss-session branch.
    # With fewer than 2 players it bails to the insight label — crucially
    # WITHOUT raising AttributeError on a missing total_damage.
    bs = _Tracker.boss_session
    bs.players["War (You)"] = _mk_player("War (You)", 5000.0, 30, 0.0,
                                          [("Sword_Base_Attack",
                                            "Sword Base Attack",
                                            5000.0, 30)])
    p = _Page()
    # Old code crashed here: boss_session has no total_damage attribute.
    refresh_rail_compare(p, CombatSession("Overall"), 5.0, "damage")
    assert "Need at least 2 players" in p.cp_cmp_insight.text()


def test_dummy_fight_never_matches_trash_filter():
    """A training-dummy fight archived with kind=='trash' (dummy engagement
    missed at event time) must group as Test Dummy and be excluded from the
    Trash filter, matching the rule the fights list uses."""
    from farever_companion.core.dps_tracker import CombatSession
    from farever_companion.data import units as udata

    # The exact shape that used to leak: a trash-kind session whose target is
    # a training dummy. Filter rule: kind==dummy OR target is a dummy.
    sess = CombatSession("Training Dummy", kind="trash")
    sess.target_name = "🎯 Invu"
    is_dummy = (sess.kind == "dummy" or udata.is_training_dummy(sess.target_name))
    assert is_dummy is True

    # kind reads trash, but the dummy check must gate it out of Trash:
    # Trash shows kind==trash AND NOT dummy.
    shows_in_trash = (sess.kind == "trash") and not is_dummy
    assert shows_in_trash is False


def test_combat_rail_does_not_double_emblem_on_dummy_fight():
    """A loaded dummy fight has target_name starting with '🎯 ' — the detail
    rail must not prepend a second emblem (used to show '🎯 🎯 Invu')."""
    from farever_companion.core.dps_tracker import CombatSession
    import farever_companion.ui.pages.combat_page as cp_page

    sess = CombatSession("Training Dummy", kind="dummy")
    sess.target_name = "🎯 Invu"
    me = _mk_player("War (You)", 5000.0, 30, 0.0,
                    [("Sword_Base_Attack", "Sword Base Attack", 5000.0, 30)])
    me.is_me = True
    sess.players[me.name] = me

    host = cp_page.CombatPageMixin.__new__(cp_page.CombatPageMixin)
    host.cp_rail_boss_box = QtWidgets.QFrame()
    host.cp_rail_boss_nm = QtWidgets.QLabel("")
    host.cp_rail_boss_hp = QtWidgets.QLabel("")
    host.cp_rail_boss_bar = QtWidgets.QProgressBar()
    host.cp_rail_burst_lbl = QtWidgets.QLabel("")
    host.cp_rail_deaths_lbl = QtWidgets.QLabel("")
    host.cp_rail_top_lbl = QtWidgets.QLabel("")
    host.cp_rail_top_sub = QtWidgets.QLabel("")
    host.cp_rail_ally_lbl = QtWidgets.QLabel("")

    host._update_combat_rail(sess)
    assert host.cp_rail_boss_nm.text() == "🎯 Invu"
    assert "🎯 🎯" not in host.cp_rail_boss_nm.text()


def test_the_rail_groups_a_folded_sub_skill_exactly_as_the_meter_does():
    """The Combat page must show the same grouping the DPS meter does.

    The fold lives in the data layer, so the rail already RECEIVES folded
    skills - but it rendered only the parent, leaving a row on this page whose
    number is larger than anything the meter explains. Both surfaces now build
    their nested rows through the same helper (`sync_subskill_rows`), which is
    the only thing keeping them from drifting apart again.
    """
    import time as _time
    from farever_companion.core.dps_tracker import CombatSession
    from farever_companion.ui.skill_row import sub_row_key
    import farever_companion.ui.pages.combat_page as cp_page

    THROW = "Axe_Boomerang_Skill1"
    BLEED = f"{THROW}_Status"

    sess = CombatSession("Test", kind="trash")
    t0 = _time.time()
    sess.record_hit("Trash", 0x900, 100.0, 100.0, 1000.0, skill_id=THROW,
                    skill_name="Bonethrow", when=t0)
    sess.record_hit("Trash", 0x900, 100.0, 100.0, 250.0, skill_id=BLEED,
                    skill_name="Bonethrow", when=t0 + 1)
    me = sess.players["You"]
    me.name = "War (You)"

    host = cp_page.CombatPageMixin.__new__(cp_page.CombatPageMixin)
    for attr in ("cp_hero_name", "cp_hero_badge", "cp_hero_dps", "cp_hero_tot",
                 "cp_hero_shr", "cp_hero_crt", "cp_hero_compare_btn",):
        setattr(host, attr, QtWidgets.QLabel(""))
    host.cp_skills_container = QtWidgets.QWidget()
    host.cp_skills_rows_lay = QtWidgets.QVBoxLayout(host.cp_skills_container)
    host._cp_rail_skill_widgets = {}
    host.model = None
    host._cp_rail_type_shown = None

    host._refresh_rail_skills(sess, 10.0, "damage")

    key = sub_row_key(THROW, BLEED)
    assert key in host._cp_rail_skill_widgets, "the bleed is a row here too"
    child = host._cp_rail_skill_widgets[key]
    parent = host._cp_rail_skill_widgets[THROW]
    assert child._layout.contentsMargins().left() > 0
    assert parent._layout.contentsMargins().left() == 0
    # Directly beneath its parent - the rail orders by insertion, so a nested
    # row placed by the wrong rule would drift to the top of the list.
    assert (host.cp_skills_rows_lay.indexOf(child)
            == host.cp_skills_rows_lay.indexOf(parent) + 1)
    assert child._name_lbl._full != parent._name_lbl._full

    # And the parent's total is still the whole truth.
    assert parent.findChild(QtWidgets.QLabel, "SkillTotal").text().startswith(
        "1,250")

    # Same rule on the heal board: `subskills` is the damage channel, so no
    # nested damage number appears under a healing list.
    me.heals = 900.0
    for sp in me.skills.values():
        sp.heals, sp.heal_count = 900.0, 2
        sp.min_heal, sp.max_heal = 400.0, 500.0
    host._cp_rail_skill_widgets.clear()
    host._refresh_rail_skills(sess, 10.0, "healing")
    assert key not in host._cp_rail_skill_widgets
    assert THROW in host._cp_rail_skill_widgets


def test_an_archived_fight_still_shows_the_fold_and_the_tags():
    """DPS Analysis and Past Fights read ARCHIVED sessions; the live meter
    must not explain a row the archive cannot.

    This was a real hole, not a hypothetical one. `subskills` was never
    serialized, so the moment a fight was written to disk the fold vanished:
    the parent's number stayed whole (1,250) while the breakdown explaining
    250 of it disappeared, and the same fight read differently in the meter
    than in DPS Analysis a minute later.

    Asserted through a real round-trip, because the failure mode was in the
    SERIALIZER - a unit test on the rail alone would have passed throughout.
    """
    import json
    import time as _time
    from farever_companion.core.dps_data import (
        CombatSession, _session_from_dict, _session_to_dict,
    )
    from farever_companion.ui.skill_row import sub_row_key
    import farever_companion.ui.pages.combat_page as cp_page

    THROW = "Axe_Boomerang_Skill1"
    BLEED = f"{THROW}_Status"
    PROC = "Axe_Boomerang_Skill_Passive"      # Bloodrage Aura: an item proc

    sess = CombatSession("Test", kind="trash")
    t0 = _time.time()
    sess.record_hit("Trash", 0x900, 100.0, 100.0, 1000.0, skill_id=THROW,
                    skill_name="Bonethrow", when=t0)
    sess.record_hit("Trash", 0x900, 100.0, 100.0, 250.0, skill_id=BLEED,
                    skill_name="Bonethrow", when=t0 + 1)
    sess.record_hit("Trash", 0x900, 100.0, 100.0, 400.0, skill_id=PROC,
                    skill_name="Bloodrage Aura", when=t0 + 2)

    # Through JSON, not just the dicts - the archive is files on disk.
    back = _session_from_dict(json.loads(json.dumps(_session_to_dict(sess))))
    me = back.players["You"]
    me.name = "War (You)"

    host = cp_page.CombatPageMixin.__new__(cp_page.CombatPageMixin)
    for attr in ("cp_hero_name", "cp_hero_badge", "cp_hero_dps", "cp_hero_tot",
                 "cp_hero_shr", "cp_hero_crt", "cp_hero_compare_btn",):
        setattr(host, attr, QtWidgets.QLabel(""))
    host.cp_skills_container = QtWidgets.QWidget()
    host.cp_skills_rows_lay = QtWidgets.QVBoxLayout(host.cp_skills_container)
    host._cp_rail_skill_widgets = {}
    host.model = None
    host._cp_rail_type_shown = None

    host._refresh_rail_skills(back, 10.0, "damage")

    # The fold survived the archive: a nested row, still indented under its
    # parent, with its OWN hits - not just a number to add up.
    key = sub_row_key(THROW, BLEED)
    assert key in host._cp_rail_skill_widgets, "archive lost the fold"
    child = host._cp_rail_skill_widgets[key]
    assert child._layout.contentsMargins().left() > 0
    assert child.findChild(QtWidgets.QLabel, "SkillTotal").text().startswith(
        "250")

    # And the tag is applied from the archived data, not from a live-only hint.
    # It is a badge beside the name (2026-10-03), not a suffix inside it.
    assert "proc" in host._cp_rail_skill_widgets[PROC]._tag_lbl.text()
    assert host._cp_rail_skill_widgets[PROC]._name_lbl._full == "Bloodrage Aura"
    assert host._cp_rail_skill_widgets[THROW]._name_lbl._full == "Bonethrow"
    assert host._cp_rail_skill_widgets[THROW]._tag_lbl.isHidden()


def test_the_timeline_plotter_has_no_skill_axis_to_disagree_about():
    """The fold reaches the plotter as TOTALS, not as skills - verify that.

    The plotter draws DPS-over-time, or per-player 10s bins in compare mode.
    It never names a skill, so it has no list to group and cannot disagree with
    the meter about how a sub-skill is attributed. What it must still get right
    is the total: the bleed folded into Bonethrow has to stay in the curve, or
    the timeline would silently under-report the fight.

    This pins that, plus the mock-data fallback path (the one place the
    plotter DOES walk a skill list) - it must walk the folded parent, whose
    damage already includes the child, or the synthesised flow loses it.
    """
    import time as _time
    from PySide6 import QtGui
    from farever_companion.core.dps_data import CombatSession
    from farever_companion.ui.combat.timeline_plotter import (
        _CombatTimelinePlotter, _get_player_timeline_flow,
    )

    THROW = "Axe_Boomerang_Skill1"
    BLEED = f"{THROW}_Status"
    s = CombatSession("Test", kind="trash")
    t0 = _time.time()
    s.record_hit("Trash", 0x900, 100.0, 100.0, 1000.0, skill_id=THROW,
                 skill_name="Bonethrow", when=t0)
    s.record_hit("Trash", 0x900, 100.0, 100.0, 250.0, skill_id=BLEED,
                 skill_name="Bonethrow", when=t0 + 1)
    s.start_time = t0

    # The real path: the curve is built from the hit log, so the fold cannot
    # hide the bleed from it.
    flow = _get_player_timeline_flow(s, "You")
    assert abs(sum(flow.values()) - 1250.0) < 1e-6

    # The fallback path walks ranked_skills(). With the child popped into its
    # parent that list is one entry of 1250, not two entries summing to 1250 -
    # and the synthesised flow is still the whole truth.
    s.hits.clear()
    s.player_timeline.clear()
    fallback = _get_player_timeline_flow(s, "You")
    assert abs(sum(fallback.values()) - 1250.0) < 1e-6, "fallback loses the fold"

    # It really is skill-less: painting one names players and times, never a
    # skill, so there is nothing here that could drift out of step.
    plotter = _CombatTimelinePlotter()
    plotter.set_session(s)
    plotter.resize(400, 200)
    img = QtGui.QImage(400, 200, QtGui.QImage.Format_ARGB32)
    img.fill(QtGui.QColor("#000000"))
    plotter.render(img)
    plotter.deleteLater()


def test_timeline_plotter_clear_is_safe_after_combat_reset():
    """Manual combat reset clears the plotter without requiring a legacy API."""
    plotter = _CombatTimelinePlotter()
    plotter.compare_players = ["Me"]
    plotter.hover_x = 12.0
    plotter.clear()
    assert plotter.session is None
    assert plotter.compare_players == []
    assert plotter.hover_x is None
    plotter.deleteLater()


def test_reset_combat_session_never_touches_capture():
    """Reset clears the meter in place: it must not call into the capture
    engine — a stale damage.reset() call once raised AttributeError (the
    manager has no reset), aborting the click before the rail + breakdown
    re-rendered."""
    from types import SimpleNamespace
    import farever_companion.ui.pages.combat_page as cp_page
    from farever_companion.core.dps_tracker import DpsTracker
    from farever_companion.core.dps_source import DamageSourceManager

    class _Proc:
        has_scan = True

    calls: list[str] = []
    host = cp_page.CombatPageMixin.__new__(cp_page.CombatPageMixin)
    host.cp_history_combo = QtWidgets.QComboBox()
    host.cp_history_combo.addItem("Live Encounter (Active)")
    host._update_hist_banner = lambda *a: calls.append("banner")
    host._reset_combat_rail = lambda: calls.append("rail")
    host._render_combat_breakdown = lambda: calls.append("breakdown")
    dmg = DamageSourceManager(_Proc())
    host.model = SimpleNamespace(dps=DpsTracker(None), damage=dmg)

    host._reset_combat_session()                  # must not raise

    assert dmg.started is False                   # capture engine untouched
    assert calls == ["banner", "rail", "breakdown"]
    dmg.shutdown()


def test_cp_solo_toggle_never_filters_combat_analysis():
    """The Top DPS overlay's Solo toggle (dps_solo_only) must never change
    the Combat & DPS Analysis page: the solo filter stays off even when the
    flag is on, for live and archived sessions alike."""
    import farever_companion.ui.pages.combat_page as cp_page

    host = cp_page.CombatPageMixin.__new__(cp_page.CombatPageMixin)
    host._selected_history_idx = 0      # live session view
    host._browse_sessions = None
    host.s = type("S", (), {"dps_solo_only": True})()

    # even with dps_solo_only enabled the analysis page never solo-filters
    assert host._cp_solo_filter_on() is False

    # archived/browse views were already never solo-filtered
    host._selected_history_idx = 3
    assert host._cp_solo_filter_on() is False


def test_skill_row_click_opens_skill_popup():
    """Clicking a detailed skill row fires the wired callback with the skill
    id and the row (the page anchors the info popup there)."""
    row = SkillRow(density="detailed")
    row.set_skill("Sword_Swarm_Skill1", "Swarm Cleave")
    calls = []
    row.set_on_skill_click(lambda sid, r: calls.append((sid, r)))
    row.show()
    QtTest.QTest.mouseClick(row, QtCore.Qt.LeftButton)
    assert calls == [("Sword_Swarm_Skill1", row)]
    assert row.cursor().shape() == QtCore.Qt.PointingHandCursor


def test_skill_row_click_inert_in_compact_density():
    """Overlay rows are never clickable — no callback fires, no pointer."""
    row = SkillRow(density="compact")
    calls = []
    row.set_on_skill_click(lambda sid, r: calls.append(sid))
    row.show()
    QtTest.QTest.mouseClick(row, QtCore.Qt.LeftButton)
    assert calls == []
    assert row.cursor().shape() == QtCore.Qt.ArrowCursor


def test_skill_popup_shows_effect_and_source_weapon():
    """The row-click popup shows the skill's effect AND the weapon that
    grants it, by name."""
    from farever_companion.ui.pages.combat_page import _SkillWeaponPopup
    sp = SkillParse(skill_id="Sword_Swarm_Skill1", name="Swarm Cleave")
    pop = _SkillWeaponPopup(sp)
    pop.adjustSize()
    texts = [l.text() for l in pop.findChildren(QtWidgets.QLabel)]
    joined = " ".join(texts)
    assert "Swarm Cleave" in joined
    assert "ACTIVE" in joined                    # type chip
    assert "Beefury, Blessed Blade of the Farseeker" in joined
    assert any("⚔" in t for t in texts)         # weapon glyph
    pop.close()


def test_the_skill_popup_chips_an_item_proc_instead_of_its_type():
    """The row's "(Item proc)" is text on the meter; the POPUP says it as a
    chip - and says it ONCE.

    An item proc IS a Passive, so `skill_type` returns "Passive" and the TYPE
    chip printed PASSIVE right beside ITEM PROC. That is the same fact twice,
    and it is what the field reported (live 2026-10-03). The distinction is
    what matters, so the kind chip REPLACES the type chip rather than joining
    it: ITEM PROC says both the category and the distinction in two words.
    """
    from farever_companion.ui.pages.combat_page import _SkillWeaponPopup

    proc = SkillParse(skill_id="Axe_Boomerang_Skill_Passive",
                      name="Bloodrage Aura")
    pop = _SkillWeaponPopup(proc)
    pop.adjustSize()
    chips = [l.text() for l in pop.findChildren(QtWidgets.QLabel)
             if l.objectName() == "PopupChip"]
    assert "ITEM PROC" in chips
    # The regression: two chips for one fact, saying the same thing.
    assert "PASSIVE" not in chips, \
        "ITEM PROC replaces the PASSIVE type chip, it does not join it"
    # One chip per card, so nothing else snuck in either (FROM <parent> is a
    # derived row's own chip and this is a top-level skill).
    assert len(chips) == 1
    # ...and the card still names the piece, which the chip cannot.
    texts = [l.text() for l in pop.findChildren(QtWidgets.QLabel)]
    assert "Cheese Moon" in " ".join(texts)
    pop.close()

    # A rotation passive gets the TYPE chip only - no second badge.
    tree = SkillParse(skill_id="Warrior_Talent_ShatteredRage",
                      name="Shattered Rage")
    pop2 = _SkillWeaponPopup(tree)
    pop2.adjustSize()
    chips2 = [l.text() for l in pop2.findChildren(QtWidgets.QLabel)
              if l.objectName() == "PopupChip"]
    assert "ITEM PROC" not in chips2
    assert "PASSIVE" in chips2
    pop2.close()


def test_a_derived_rows_popup_names_the_skill_it_belongs_to():
    """The FROM chip names a derived row's parent ONLY when that adds something.

    `Axe_Boomerang_Skill1_Status` is its own sheet row with its own TYPE chip,
    so on its own it reads as a separate skill the player never cast. The FROM
    chip is what reconnects it to its parent - but ONLY where the parent is
    named differently. The sheet reuses one name across a skill and its
    derived rows, so for the majority the chip echoed the card's own title
    ("FROM BONETHROW" printed directly under "Bonethrow"), which is noise the
    eye learns to skip and the reason live 2026-10-05 dropped it.

    The mechanism is kept, not removed: where the parent really is a different
    skill ("Dart" from "Hive Assault") the chip is the only thing tying the
    two together, so it must survive.
    """
    from farever_companion.ui.pages.combat_page import _SkillWeaponPopup

    def chips(sid, name):
        pop = _SkillWeaponPopup(SkillParse(skill_id=sid, name=name))
        pop.adjustSize()
        out = [l.text() for l in pop.findChildren(QtWidgets.QLabel)
               if l.objectName() == "PopupChip"]
        pop.close()
        return out

    # The bleed: STATUS, and named after its parent - so the parent chip would
    # only echo the card's own title ("FROM BONETHROW" under "Bonethrow").
    # 191 of 266 derived rows are this shape; the chip is suppressed for them.
    bleed = chips("Axe_Boomerang_Skill1_Status", "Bonethrow")
    assert bleed == ["STATUS"]

    # The throw itself has NO parent chip: it is a top-level skill, and a
    # self-gating chip is the point — one that appeared everywhere would be
    # noise that trains the eye to skip it.
    assert chips("Axe_Boomerang_Skill1", "Bonethrow") == ["ACTIVE"]

    # A derived row whose parent is genuinely a DIFFERENT skill keeps the
    # chip - it is the only thing tying the row back to what inflicts it.
    # ("Dart" is the status of "Hive Assault"; naming the same word twice
    # would tie nothing together.)
    assert "FROM HIVE ASSAULT" in chips("Spear_Goo_Combo_Status", "Dart")
    assert "FROM SWARM LORD" in \
        chips("Sword_Swarm_Passive_Swarm", "Hive Swarm")

    # ...and a gear-granted one carries its kind chip either way.
    proc = chips("Axe_Boomerang_Skill_Passive_Status", "Bloodrage Aura")
    assert "STATUS" in proc and "ITEM PROC" in proc
    # Same name as its parent ("Bloodrage Aura"), so no FROM chip.
    assert not any(c.startswith("FROM") for c in proc), proc

    # The reported card: the parent and the status share one name, so the
    # chip is dropped rather than repeating the title verbatim.
    assert chips("DS_Bladeleaf_Skill2_Status", "Swarmstrike Accord") \
        == ["STATUS"]


def test_a_derived_rows_popup_still_names_the_weapon_it_is_from():
    """A derived row must keep the "From Weapon" section its parent earned.

    The FROM chip (above) ties a `..._Status` row back to the skill that
    inflicts it, but the WEAPON section asked the catalog about the status id
    itself - and no item lists a status, only the parent that applies it. So
    every derived row silently lost the one line answering "where does this
    come from?" (reported on Swarmstrike Accord: `DS_Bladeleaf_Skill2_Status`
    is granted by Wingsabers' `DS_Bladeleaf_Skill2`).

    Both assertions are real sheet ids, not mocks, and the second is a
    different weapon: this is the shared shape for bleeds, DoTs and buff
    bonus damage, not a one-off for the reported skill.
    """
    from farever_companion.ui.pages.combat_page import _SkillWeaponPopup

    def card(sid, name):
        pop = _SkillWeaponPopup(SkillParse(skill_id=sid, name=name))
        pop.adjustSize()
        joined = " ".join(l.text() for l in pop.findChildren(QtWidgets.QLabel))
        pop.close()
        return joined

    accord = card("DS_Bladeleaf_Skill2_Status", "Swarmstrike Accord")
    assert "Wingsabers" in accord, \
        "a status row must resolve its weapon through the parent skill"

    # ...and the same shape on another weapon, so the fix is not special-cased.
    assert "Cheese Moon" in card("Axe_Boomerang_Skill1_Status", "Bonethrow")

    # A top-level skill already resolved directly; it must keep doing so
    # (the parent walk is a fallback, never a replacement).
    assert "Beefury, Blessed Blade of the Farseeker" in \
        card("Sword_Swarm_Skill1", "Swarm Cleave")


def test_a_concatenated_status_popup_still_names_the_weapon_it_is_from():
    """The CONCATENATED derived-row shape names its weapon too.

    The test above covers `..._Skill1_Status`, where the marker is its own
    `_`-segment. The sheet also ships rows that GLUE the marker onto the last
    segment - `Daggers_DuplicatePoison_Passive` + `Status` ->
    `..._PassiveStatus` - and no `_`-split can reach that parent, so the popup
    fell through to no "From Weapon" section at all. That is the row the
    player reported: "Gash" on the Combat rail, with no weapon on the card.
    """
    from farever_companion.ui.pages.combat_page import _SkillWeaponPopup

    def card(sid, name):
        pop = _SkillWeaponPopup(SkillParse(skill_id=sid, name=name))
        pop.adjustSize()
        joined = " ".join(l.text() for l in pop.findChildren(QtWidgets.QLabel))
        pop.close()
        return joined

    # the reported row, in both the sheet's spelling and the archive's (the
    # archived parse stores the display name the game rendered)
    assert "Twin Fangs of Ratsar" in \
        card("Daggers_DuplicatePoison_PassiveStatus", "Gash")
    assert "Twin Fangs of Ratsar" in \
        card("Daggers_DuplicatePoison_PassiveStatus", "Gash (Item proc)")

    # ...and a second concatenated shape on a different weapon
    assert "Iron Fins of the Leviathan" in \
        card("DA_Water_Skill1_StatusShield", "Abyssal Fury")


# --- weapon loadout derivation ---------------------------------------------

def test_weapon_loadout_picks_me_player_skill_families():
    """Loadout reads the local player's weapon-family skill ids."""
    from farever_companion.core.dps_tracker import (CombatSession,
                                                    weapon_families_used)
    s = CombatSession("Test", kind="overall")
    me = _mk_player("War (You)", 9000.0, 60, 0.0,
                    [("Axe_Base_Attack", "Axe Base Attack", 4000.0, 20),
                     ("Axe_Boomerang_Skill1", "Axe Boomerang", 3000.0, 10),
                     ("Shield_OrbitWater_S1_OrbStatus", "Water Shield", 2000.0, 5)])
    me.is_me = True
    ally = _mk_player("Brit", 500.0, 5, 0.0,
                      [("Bow_Base_Attack", "Bow Base Attack", 500.0, 5)])
    s.players[me.name] = me
    s.players[ally.name] = ally

    # ally's bow must NOT appear - loadout is the local player's weapons
    assert weapon_families_used(s) == ["Axe", "Shield"]


def test_weapon_loadout_empty_for_spell_only_parse():
    """Pure-cast skills (no weapon token) -> empty loadout, never fabricated."""
    from farever_companion.core.dps_tracker import (CombatSession,
                                                    weapon_families_used)
    s = CombatSession("Test", kind="overall")
    me = _mk_player("Me (You)", 1000.0, 8, 0.0,
                    [("Sunlight", "Sunlight", 600.0, 4),
                     ("Eruption", "Eruption", 400.0, 4)])
    me.is_me = True
    s.players[me.name] = me
    assert weapon_families_used(s) == []

def test_weapons_used_prefers_the_equipped_weapon_hint():
    """A skill id the catalog lacks (statuses, procs) used to fall back to the
    bare family ("Axe") even though the live gear walk knows exactly which axe
    is equipped. With an equipped hint the family resolves to the REAL weapon
    ("Cheese Moon"); without one the old fallback still applies."""
    from farever_companion.core.dps_tracker import (CombatSession,
                                                    weapons_used)
    s = CombatSession("Test", kind="overall")
    me = _mk_player("War (You)", 163.0, 5, 0.0,
                    [("Axe_Boomerang_Skill1_Status", "Bonethrow", 123.0, 3),
                     ("Shield_OrbitWater_S1_OrbStatus", "Depth Shield", 40.0, 2)])
    me.is_me = True
    s.players[me.name] = me

    # no hint (reader unavailable): the family fallback keeps the line alive
    assert weapons_used(s) == [{"name": "Axe", "type": "Axe"},
                               {"name": "Shield", "type": "Shield"}]

    # live gear hint: family -> the actually equipped weapons
    hint = {"Axe": {"id": "Axe_Boomerang", "name": "Cheese Moon", "type": "Axe"},
            "Shield": {"id": "Shield_OrbitWater",
                       "name": "Crabgantua's Kneecap", "type": "Shield"}}
    assert weapons_used(s, equipped=hint) == [
        {"name": "Cheese Moon", "type": "Axe"},
        {"name": "Crabgantua's Kneecap", "type": "Shield"}]

    # a signature skill the catalog DOES know stays exact with a hint present
    s2 = CombatSession("Test", kind="overall")
    me2 = _mk_player("War (You)", 500.0, 2, 0.0,
                     [("Axe_Boomerang_Skill1", "Bonethrow", 500.0, 2)])
    me2.is_me = True
    s2.players[me2.name] = me2
    assert weapons_used(s2, equipped=hint) == [
        {"name": "Cheese Moon", "type": "Axe"}]


def test_top_skill_weapon_follows_the_biggest_skill_not_slot_one():
    """The rail's weapon line names the weapon behind the TOP skill.

    Slot order is not damage order: a sword in slot 1 used to be credited
    even when an offhand/off-slot weapon's skill did nearly all the damage.
    """
    from farever_companion.core.dps_tracker import (CombatSession,
                                                    top_skill_weapon)
    hint = {"Sword": {"id": "Sword_Swarm", "name": "Beefury, Blessed Blade of the Farseeker",
                      "type": "Sword"},
            "Axe": {"id": "Axe_Boomerang", "name": "Cheese Moon", "type": "Axe"}}
    s = CombatSession("Test", kind="overall")
    me = _mk_player("War (You)", 7000.0, 30, 0.0,
                    [("Sword_Spin", "Whirlwind", 1000.0, 10),   # slot 1, small
                     ("Axe_Base_Attack", "Axe Base Attack", 6000.0, 20)])
    me.is_me = True
    s.players[me.name] = me
    # the big number is the axe's -> Cheese Moon, never Beefury
    assert top_skill_weapon(s, equipped=hint) == {"name": "Cheese Moon",
                                                   "type": "Axe"}

    # healing metric ranks by heals, so a healing caster's wand wins over its
    # own highest-damage sword skill
    s2 = CombatSession("Test", kind="overall")
    me2 = _mk_player("Pri (You)", 5000.0, 20, 9000.0,
                     [("Sword_Base_Attack", "Slash", 5000.0, 20)],
                     heal_skills=[("Mace_Light", "Holy Beam", 9000.0, 9)])
    me2.is_me = True
    s2.players[me2.name] = me2
    assert top_skill_weapon(s2, metric="healing")["type"] == "Mace"

    # a pure cast on top never claims the line - it walks down to the next
    # weapon-backed skill, here an ambiguous base attack, which reports the
    # honest family rather than picking one of the three catalog swords
    s3 = CombatSession("Test", kind="overall")
    me3 = _mk_player("War (You)", 3000.0, 10, 0.0,
                     [("Sword_Base_Attack", "Slash", 3000.0, 10),
                      ("Sunlight", "Sunlight", 2000.0, 8)])
    me3.is_me = True
    s3.players[me3.name] = me3
    assert top_skill_weapon(s3) == {"name": "Sword", "type": "Sword"}
    assert top_skill_weapon(CombatSession("Empty", kind="overall")) is None


def test_clear_old_logs_respects_the_keep_window(monkeypatch):
    """Clear Old Logs is the only bulk delete in the app, so both of its guards
    are the test's subject: a Keep Logs window of 0 (keep everything, the
    default) must not delete a single file however many times it is pressed,
    and a real window must ask first and then say what it removed."""
    import PySide6.QtWidgets as QtWidgets
    import farever_companion.ui.pages.combat_history as ch

    asked: list[str] = []
    calls: list[tuple] = []
    logs: list[str] = []
    rerenders: list[int] = []

    class _Tracker:
        def prune_history_older_than(self, days, directory=None):
            calls.append((days, directory))
            return {"folders": 3, "files": 7, "cutoff": "2026-01-01"}

    class _S:
        dps_log_keep_days = 0

    host = ch.CombatHistoryMixin.__new__(ch.CombatHistoryMixin)
    host.s = _S()
    host._get_active_tracker = lambda: _Tracker()
    host.log = logs.append
    host._sync_history_combo = lambda: None
    host._render_combat_breakdown = lambda: rerenders.append(1)

    monkeypatch.setattr(QtWidgets.QMessageBox, "information",
                        staticmethod(lambda *a, **k: asked.append("info")))
    monkeypatch.setattr(QtWidgets.QMessageBox, "question",
                        staticmethod(lambda *a, **k: asked.append("ask")
                                     or QtWidgets.QMessageBox.No))

    # 0 = keep everything: explain and stop, without touching the folder
    host._clear_old_logs()
    assert asked == ["info"]
    assert calls == []
    assert logs == []

    # a real window asks, and a "no" is a no
    host.s.dps_log_keep_days = 30
    host._clear_old_logs()
    assert asked == ["info", "ask"]
    assert calls == []
    assert logs == []

    # confirmed: prune once, with the setting's number and the log dir, then
    # re-render so the list on screen matches what is on disk
    monkeypatch.setattr(QtWidgets.QMessageBox, "question",
                        staticmethod(lambda *a, **k: QtWidgets.QMessageBox.Yes))
    host._clear_old_logs()
    assert len(calls) == 1 and calls[0][0] == 30
    assert str(calls[0][1]).replace("\\", "/").endswith("dps")
    assert rerenders == [1]
    assert host._cp_past_fights_dirty is True
    assert logs and "3 day folders" in logs[0]
