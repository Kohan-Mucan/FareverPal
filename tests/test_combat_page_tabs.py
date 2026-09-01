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
from tests.dps_fakes import _qapp  # noqa: E402,F401 (shared offscreen fixture)


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
    row.update(sp, share_pct=5000 / 9000, mode="damage")
    st = row.findChild(QtWidgets.QLabel, "SkillTotal")
    sh = row.findChild(QtWidgets.QLabel, "SkillHits")
    sc = row.findChild(QtWidgets.QLabel, "SkillCrit")
    sa = row.findChild(QtWidgets.QLabel, "SkillAvg")
    bar = row.findChild(QtWidgets.QProgressBar, "SkillBar")
    assert st is not None and st.text() == "5,000"
    assert sh is not None and sh.text() == "20"
    assert sc is not None and sc.text() == "—"          # no crits recorded
    assert sa is not None and "250" in sa.text()          # avg [min–max]
    assert bar is not None and bar.value() == 55          # 5000 of 9000
    # prose artifacts from the old single-string row are gone
    assert "(55%) · 20 hits" not in st.text()


def test_skill_row_healing_channel_amounts_and_avg():
    """Healing rows fill the same columns from the heal channel."""
    row = _detailed_row("Holy_Beam", "Holy Beam")
    sp = _healer().skills["Holy_Beam"]
    row.update(sp, share_pct=1.0, mode="healing")
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
    row_d.update(sp, share_pct=500 / 500, mode="damage")
    st = row_d.findChild(QtWidgets.QLabel, "SkillTotal")
    sh = row_d.findChild(QtWidgets.QLabel, "SkillHits")
    assert st.text() == "500" and sh.text() == "4"
    assert "H 300" not in st.text()

    # healing mode: same skill_id, heal channel shown instead
    row_h = _detailed_row("Judgment", "Judgment")
    row_h.update(sp, share_pct=300 / 300, mode="healing")
    st2 = row_h.findChild(QtWidgets.QLabel, "SkillTotal")
    sh2 = row_h.findChild(QtWidgets.QLabel, "SkillHits")
    assert st2.text() == "300" and sh2.text() == "3"

    # skills mode: hybrid shows BOTH channels on the one row
    row_b = _detailed_row("Judgment", "Judgment")
    row_b.update(sp, share_pct=800 / 800, mode="both")
    st3 = row_b.findChild(QtWidgets.QLabel, "SkillTotal")
    assert "D 500" in st3.text() and "H 300" in st3.text()


def test_skill_row_shares_against_parent_total():
    """The share bar draws against the parent (tab) total."""
    row = _detailed_row("Sword_Spin", "Whirlwind")
    sp = _dps().skills["Sword_Spin"]
    row.update(sp, share_pct=5000 / 9000, mode="damage")
    bar = row.findChild(QtWidgets.QProgressBar, "SkillBar")
    # Whirlwind = 5000 of 9000 total damage
    assert bar is not None and bar.value() == 55


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
    row.update(p2.skills["Axe_Base_Attack"], share_pct=9000 / 10000,
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