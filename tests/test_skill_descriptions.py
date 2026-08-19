"""Skill description resolver (headless, no game): skill.json template
tokens (::var1%::, ::ref_name::, ...) and [X] bracket references resolved
into readable text, plus the nature -> type label mapping."""
import pytest

from farever_companion import paths
from farever_companion.data import skills


def _data_present() -> bool:
    try:
        return (paths.sheets_dir() / "skill.json").exists()
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _data_present(),
    reason="requires bundled data (assets/data/skill.json)")


def test_var_tokens_and_ref_duration():
    # Bonethrow: vars.var1 = 0.4 -> 40%, vars.var3 = 2 -> '2', and the
    # bleed's duration comes from the referenced status row (8s). ::dmg::
    # has no numeric source -> the honest 'damage' fallback.
    d = skills.skill_description("Axe_Boomerang_Skill1")
    assert d == ("Throws a sharpen bone at an enemy, dealing damage and "
                 "causing them to bleed for 40% of the damage dealt over "
                 "8s. The bone then jumps to 2 enemies.")


def test_ref_name_and_placeholders_never_invent_numbers():
    # Mace's light burst consumes ::ref_name:: (the 'Benediction' status)
    # and ::ref_stacks:: (no value anywhere) — the unresolvable slots stay
    # neutral 'X stacks' / 'X%' instead of a fabricated number.
    d = skills.skill_description("Mace_Benediction_Skill1")
    assert "consuming all Benediction stacks" in d
    assert "If X stacks are consumed" in d
    assert "your Critical Chance and Fervor by X% for 15s" in d


def test_bracket_stat_references():
    # [CritChance] -> 'Critical Chance'; ranges carry the sheet-implicit
    # unit (40m); small vars render as percents (0.03 -> 3%).
    d = skills.skill_description("Axe_Boomerang_Skill_Passive")
    assert d == ("Increases the Critical Chance of all allies within 40m "
                 "by 3%.")
    # [Attack] / [ComboAttack] resolve too
    d = skills.skill_description("PhysicalBlock")
    assert "Blocks incoming Attacks" in d
    assert "your next Attack by 30%" in d      # ref_damage% -> the status row


def test_missing_values_stay_honest():
    # ::val1%:: has no source in PhysicalBlock's row or its ref -> 'X%',
    # never a made-up percentage. The resolved 15% comes from a REAL var.
    d = skills.skill_description("PhysicalBlock")
    assert "reducing their damage by X%" in d
    assert "by an additional 15%" in d


def test_basic_attack_without_vars():
    # no vars -> 'damage'; no leftover template tokens
    d = skills.skill_description("Axe_Base_Attack")
    assert d == "Deals damage to enemies in a small cone."
    assert "::" not in d


def test_ref_slot_with_no_ref_target():
    # GS_Nova_Ultimate's desc references ::ref2_dmg:: but carries no refs —
    # the noun fallback keeps the sentence readable.
    d = skills.skill_description("GS_Nova_Ultimate")
    assert d == ("Smashes the ground and releases a massive nova dealing "
                 "damage to all enemies and knocking them back.")


def test_no_leftover_template_tokens_anywhere():
    """Property: every description in the sheet resolves without a single
    '::' or '[' left behind (520 rows) — and nothing crashes on ref cycles."""
    rows = skills._rows()
    assert rows, "skill sheet should load"
    n = 0
    for sid, row in rows.items():
        raw = (row.get("texts") or {}).get("desc")
        if not raw:
            continue
        out = skills.skill_description(sid)
        assert out, sid
        assert "::" not in out, sid
        assert "[" not in out, sid
        n += 1
    assert n > 400          # the vast majority of rows carry descriptions


def test_resolves_from_compiled_shim_without_loose_sheet():
    """Frozen-build path: the loose skill.json is a compile input only, so
    the compiler carries the resolver fields (texts/vars/cooldown/duration/
    steps) in the raw_skills shim. With the loose sheet hidden, descriptions
    must still resolve from the shim alone — the item page's weapon-skills
    section depends on this in the frozen app."""
    from pathlib import Path

    class NoSheets:
        def sheets_dir(self):
            return Path(__file__).resolve().parents[1] / "tmp_preview"

    orig = skills.paths
    try:
        skills.paths = NoSheets()
        skills._rows.cache_clear()
        assert skills.skill_description("Axe_Boomerang_Skill1") == (
            "Throws a sharpen bone at an enemy, dealing damage and causing "
            "them to bleed for 40% of the damage dealt over 8s. The bone "
            "then jumps to 2 enemies.")
        # rank text rides the shim too — rank 3 falls back to the base
        # description with the rankOverride value applied (3 enemies)
        assert "jumps to 3 enemies" in skills.skill_description(
            "Axe_Boomerang_Skill1", rank=3)
        assert skills.skill_type("Axe_Boomerang_Skill_Passive") == "Passive"
        # the lunge step rides the shim too (compiler keeps range +
        # duration) — the base-chain wind-up / reach lines work frozen
        assert skills.skill_moves("Axe_Base_Attack3") == \
            {"duration": 0.38, "range": 1.3}
        assert skills.skill_moves("Staff_Base_Attack") is None
        # the meta line rides the shim too (cooldown scalar + step ranges)
        assert skills.skill_meta("Axe_Boomerang_Skill1") == \
            {"cooldown": 15, "range": 40}
        assert skills.skill_meta("Axe_Boomerang_Skill_Passive") == \
            {"range": 40}
        # the whole sheet still resolves token-free from the shim
        for sid, row in skills._rows().items():
            raw = (row.get("texts") or {}).get("desc")
            if not raw:
                continue
            out = skills.skill_description(sid)
            assert out, sid
            assert "::" not in out, sid
            assert "[" not in out, sid
    finally:
        skills.paths = orig
        skills._rows.cache_clear()


def test_rank_description_uses_rank_values():
    # rank 0 = the base description with base values (unchanged default).
    # rank N = the per-rank description (rankDescs[N-1]); ranks without a
    # per-rank line fall back to the base description — but always with
    # props.rankOverride applied (minRank <= rank), so the base text
    # reflects the rank: Bonethrow jumps to 3 enemies at rank 3 (var3
    # overridden from 2), and its rank-2 line resolves 20% (var2 = 0.2).
    d = skills.skill_description("Axe_Boomerang_Skill1")
    assert "jumps to 2 enemies" in d
    assert skills.skill_description("Axe_Boomerang_Skill1", rank=1) == \
        "Can jump to 2 enemies."
    assert skills.skill_description("Axe_Boomerang_Skill1", rank=2) == \
        "Deals 20% increased critical damage."
    d3 = skills.skill_description("Axe_Boomerang_Skill1", rank=3)
    assert d3 == d.replace("jumps to 2 enemies", "jumps to 3 enemies")
    # rank 1 applies no overrides (minRank 2+); the passive's base 3%
    # becomes 5% from rank 2 on
    assert skills.skill_description(
        "Axe_Boomerang_Skill_Passive", rank=1) == \
        "Critical Chance bonus increased to 3%."
    assert "by 5%" in skills.skill_description(
        "Axe_Boomerang_Skill_Passive", rank=3)


def test_rank_descriptions_resolved_lines():
    # skill_rank_descriptions returns every rank line resolved at its own
    # rank, in order; rows without rankDescs return []
    assert skills.skill_rank_descriptions("Axe_Boomerang_Skill_Passive") == [
        "Critical Chance bonus increased to 3%.",
        "When you perform a Physical critical strike, heal yourself for "
        "10% of your Max Health (6s cooldown).",
    ]
    assert skills.skill_rank_descriptions("Axe_Base_Attack") == []
    assert skills.skill_rank_descriptions("NoSuchSkill") == []


def test_rank_props_override_and_placeholder_honesty():
    # rank lines resolve like the base: Daggers_Start_Skill1's rank-2 line
    # carries ::dmg:: (no source -> the honest 'damage' noun, never an
    # invented number) and ::var1:: from the row's vars; its rank-1 line
    # resolves ::chance:: (0.15 -> 15%)
    assert skills.skill_description(
        "Daggers_Start_Skill1", rank=2) == "Now deals damage 2 times."
    d1 = skills.skill_description("Daggers_Start_Skill1", rank=1)
    assert "15% additional chance to critically strike" in d1
    assert "::" not in d1 and "[" not in d1
    # unresolvable slots in rank lines stay neutral (never invented numbers)
    assert "X% Critical Chance" in skills.skill_description(
        "Axe_Boomerang_Combo", rank=1)


def test_no_leftover_rank_tokens_anywhere():
    """Property: every rank line in the sheet resolves without a single
    '::' or '[' left behind (256 lines across 128 rows)."""
    rows = skills._rows()
    n = 0
    for sid, row in rows.items():
        for i, rd in enumerate((row.get("texts") or {}).get("rankDescs")
                               or []):
            if not rd.get("desc"):
                continue
            out = skills.skill_description(sid, rank=i + 1)
            assert out, sid
            assert "::" not in out, (sid, i)
            assert "[" not in out, (sid, i)
            n += 1
    assert n == 256


def test_skill_moves_real_step_data():
    """Base-attack hits carry their lunge step's real numbers: the move
    step's duration (wind-up) and range (reach) — meters/seconds, the same
    units the resolver appends to ranges and durations. Skills without a
    move step (projectiles, ranged casts) return None."""
    assert skills.skill_moves("Axe_Base_Attack") == \
        {"duration": 0.11, "range": 0.9}
    assert skills.skill_moves("Axe_Base_Attack2") == \
        {"duration": 0.14, "range": 0.8}
    assert skills.skill_moves("Axe_Base_Attack3") == \
        {"duration": 0.38, "range": 1.3}
    # ranged cast (staff) and projectile (Bonethrow): no lunge to show
    assert skills.skill_moves("Staff_Base_Attack") is None
    assert skills.skill_moves("Axe_Boomerang_Skill1") is None
    assert skills.skill_moves("NoSuchSkill") is None


def test_skill_meta_real_stats():
    """Non-base skills expose their cooldown / range where the sheet has
    them — the SAME values the ::cooldown:: / ::range:: slots resolve to,
    so the meta line never disagrees with the description."""
    # Bonethrow: 15s cooldown (real sheet field) + the bouncer's 40m range
    assert skills.skill_meta("Axe_Boomerang_Skill1") == \
        {"cooldown": 15, "range": 40}
    # Bloodrage's 40m comes from vars.range (matches its description text)
    assert skills.skill_meta("Axe_Boomerang_Skill_Passive") == \
        {"range": 40}
    assert skills.skill_meta("Axe_Boomerang_Combo") == {"range": 6}
    assert skills.skill_meta("ShieldBlock") == \
        {"cooldown": 0.1, "range": 0.2}
    # step ranges can be template strings — never surfaced as numbers
    assert skills.skill_meta("NoSuchSkill") == {}


def test_unit_slots_never_percent_formatted():
    """Sub-1 values in unit-bearing slots are QUANTITIES with their
    unit, never the percent heuristic: ::time2:: = 0.25 reads '0.25s',
    not '25%s'. Ratio slots (::chance::, ::dmg::) keep percent
    formatting."""
    assert "every 0.25s" in skills.skill_description("GS_Nova_Skill1")
    assert "reduces the cooldown by 0.25s" in skills.skill_description(
        "GS_Nova_Skill1", rank=2)
    assert "by 0.5s" in skills.skill_description(
        "Sword_Start_Combo", rank=2)
    assert "by 0.35s" in skills.skill_description(
        "Spear_Eruption_Skill1", rank=2)
    # ratio slots still read as percents
    assert "15% additional chance" in skills.skill_description(
        "Daggers_Start_Skill1", rank=1)
    assert "40% of the damage dealt" in skills.skill_description(
        "Axe_Boomerang_Skill1")
    # property: no '%s' / '%m' unit artifact anywhere in the sheet
    import re
    for sid, row in skills._rows().items():
        t = (row.get("texts") or {})
        if t.get("desc"):
            assert not re.search(r"%[sm]\b",
                                 skills.skill_description(sid) or ""), sid
        for i, rd in enumerate(t.get("rankDescs") or []):
            if rd.get("desc"):
                assert not re.search(
                    r"%[sm]\b",
                    skills.skill_description(sid, rank=i + 1) or ""), (sid, i)


def test_unknown_skill():
    assert skills.skill_description("NoSuchSkill") is None
    assert skills.skill_type("NoSuchSkill") is None
    assert skills.skill_description("NoSuchSkill", rank=2) is None


def test_skill_type_labels():
    # nature -> the item page's type chip labels
    assert skills.skill_type("Axe_Base_Attack") == "Base Attack"
    assert skills.skill_type("Axe_Boomerang_Combo") == "Combo"
    assert skills.skill_type("Axe_Boomerang_Skill1") == "Active"
    assert skills.skill_type("Axe_Boomerang_Skill_Passive") == "Passive"
    # boss AoE hit-zone rows (nature 6) are never player skills
    assert skills.skill_type("MChuck_CheeseCatapultArea") is None
