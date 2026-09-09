"""Skill description resolver: skill.json template tokens -> readable text.

skill.json descriptions are authoring templates, not final copy. The raw
sheets put values in named slots instead of inlining them:

    'Deals ::dmg:: to enemies in a small cone.'
    '...resets the cooldown of ::ref_name:: if this critically strikes.'
    'Increases the [CritChance] of all allies within ::range:: by ::var1%::.'

The values live on the row itself — its `vars` map (var1/var2/var3,
chance, damage, time, dur1, range, ...), scalar fields (cooldown,
duration) and the first step's `range` — or, for the `::ref_*::` family,
on the row `texts.refs` points at (a status row, e.g. Bonethrow's bleed).
`::ref2_*::` resolves through the `ref2` target. `[X]` bracket refs are
stat names ([CritChance] -> 'Critical Chance') or cross-references to
other skills ([GS_Nova_Ultimate] -> that skill's name).

Skills also have RANKS. `texts.rankDescs` holds the per-rank (per-upgrade)
descriptions — what each upgrade grants — and `props.rankOverride`
overrides named vars/props from a minRank on (cumulatively), so the base
'jumps to ::var3:: enemies' reads 3 at rank 2 instead of 2.
`skill_description(sid, rank=N)` returns rank N's text (the per-rank line
when the sheet has one, else the base description) resolved with rank N's
values; `skill_rank_descriptions(sid)` returns every rank line resolved at
its own rank.

This module resolves every token to what the data can support and replaces
the rest with a readable neutral placeholder — 'X%', 'damage', 'a duration'
— never a fabricated number (same read-only-honest policy as the item
page's damage line: the app shows the real model, not a fake value). Some
template slots (::val1%::) have no numeric source anywhere in the sheet;
those stay placeholders rather than guesses.

`skill_row` / `skill_type` read the FULL raw sheet (assets/data/skill.json,
bundled with the frozen app like the other game data) and fall back to the
compiled raw_skills shim (id/type/nature/name only) when it's missing, so
names and types still resolve everywhere.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache

from .. import paths
from . import cdb, names

# A template slot: '::var1%::', '::ref2_duration::', '::dmg#::', ...
_TOKEN_RE = re.compile(r"::([a-zA-Z0-9_%#]+)::")
# A bracketed reference: [Attack], [CritChance], [GS_Nova_Ultimate], ...
_BRACKET_RE = re.compile(r"\[([^\]]+)\]")
# A reference token split into its ref prefix and the inner slot name:
# 'ref2_duration' -> ('ref2', 'duration')
_REF_RE = re.compile(r"(ref\d*)_(.+)")

# skill.nature -> the label the item page's type chip shows. 3 = the
# spell/signature actives (Pyroball, Ram Veil), 6 = boss AoE hit-zones
# (never player skills — no label).
_NATURE_LABELS = {
    0: "Base Attack",
    1: "Combo",
    2: "Active",
    3: "Power",
    4: "Status",
    5: "Passive",
}

# [X] stat references -> readable labels. Skill ids ([GS_Nova_Ultimate])
# are NOT here — they resolve through names.skill_name instead.
_STAT_REFS = {
    "Attack": "Attack", "WeaponSkill": "Weapon Skill",
    "ComboAttack": "Combo Attack", "BasicAttack": "Basic Attack",
    "SignatureSkill": "Signature Skill", "Skill": "Skill",
    "CritChance": "Critical Chance", "CritChanceRating": "Critical Chance",
    "CritDamage": "Critical Damage", "CriticalStrike": "Critical Strike",
    "Armor": "Armor", "MagicArmor": "Magic Armor", "Magic": "Magic",
    "MagicDamage": "Magic Damage", "MagicReduction": "Magic Reduction",
    "MaxHealth": "Max Health", "Health": "Health",
    "MoveSpeedFactor": "Move Speed", "CooldownReduction": "Cooldown Reduction",
    "PhysicalMastery": "Physical Mastery", "MagicMastery": "Magic Mastery",
    "ArmorPenetration": "Armor Penetration",
    "SpellPenetration": "Spell Penetration",
    "Fervor": "Fervor", "FervorRating": "Fervor",
    "Strength": "Strength", "Dexterity": "Dexterity",
    "Intellect": "Intellect", "Faith": "Faith", "Vitality": "Vitality",
    "Shield": "Shield", "ComboPoint": "Combo Point", "Rage": "Rage",
    "Prayer": "Prayer", "Conduit": "Conduit", "Totem": "Totem",
    "Spark": "Spark",
    "Bleeding": "Bleed", "Bleed": "Bleed", "Burn": "Burn",
    "Poison": "Poison", "Slowed": "Slow", "AttackBlock": "Block",
    "DodgeChance": "Dodge Chance", "RefillableFlask": "Refillable Flask",
    "Talent": "Talent", "Signature": "Signature", "Physical": "Physical",
}

# Units the game leaves implicit in template prose: skill ranges are
# meters, durations / cooldowns are seconds. Appended to resolved numbers.
_UNITS = {
    "range": "m", "duration": "s", "dur": "s", "dur1": "s",
    "dur2": "s", "dur3": "s", "time": "s", "time2": "s",
    "cooldown": "s",
}

# Neutral fallback for an unresolvable slot — readable, never invented
# numbers. '%' slots default to 'X%', unknown slots to 'X'.
_FALLBACKS = {
    "dmg": "damage", "dmgs": "damage", "dmg2": "damage", "dmg3": "damage",
    "heal": "health", "shield": "shield", "chance": "chance",
    "time": "a short time", "time2": "a short time",
    "duration": "a duration", "dur": "a duration",
    "dur1": "a duration", "dur2": "a duration", "dur3": "a duration",
    "stacks": "stacks", "range": "range", "cooldown": "a cooldown",
    "speed": "speed", "threshold": "a threshold", "atbgain": "ATB gain",
}


@lru_cache(maxsize=1)
def _rows() -> dict[str, dict]:
    """Skill id -> row. The compiled raw_skills shim first (with full
    vars/texts/steps), then cdb.by_id("skill"), falling back to loose skill.json."""
    try:
        from . import raw_skills
        if raw_skills is not None:
            full = raw_skills.DATA.get("skill_rows") or raw_skills.DATA.get("skills")
            if isinstance(full, list):
                res = {r["id"]: r for r in full if isinstance(r, dict) and r.get("id")}
                if res:
                    return res
            elif isinstance(full, dict) and full:
                return full
    except Exception:
        pass
    by_id = cdb.by_id("skill")
    if by_id:
        return by_id
    out: dict[str, dict] = {}
    raw = None
    try:
        raw = json.loads(paths.sheets_dir().joinpath("skill.json")
                         .read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    if isinstance(raw, dict):
        rows = raw.get("lines")
        if rows is None:
            for v in raw.values():
                if isinstance(v, list):
                    rows = v
                    break
    else:
        rows = raw
    if isinstance(rows, list):
        for r in rows:
            if isinstance(r, dict) and r.get("id"):
                out[r["id"]] = r
    return out or by_id


def skill_row(skill_id: str) -> dict | None:
    """The full skill row (vars/texts/steps) for `skill_id`, else None."""
    return _rows().get(skill_id)


def skill_type(skill_id: str) -> str | None:
    """Display type of a skill from its `nature`: Base Attack / Combo /
    Active / Power / Status / Passive. None for unknown ids (and boss AoE
    rows, which are never player skills)."""
    row = skill_row(skill_id)
    if not row:
        return None
    return _NATURE_LABELS.get(row.get("nature"))


def skill_moves(skill_id: str) -> dict | None:
    """The base-attack hit's lunge step: {duration, range} from the row's
    first move step (the forward dash before the strike — real step data,
    not prose). Seconds / meters, the same units the resolver appends to
    ranges and durations. None for skills with no move step (projectiles,
    ranged casts like staff attacks) — callers just skip the line."""
    row = skill_row(skill_id)
    if not row:
        return None
    for st in row.get("steps") or []:
        if (st.get("duration") is not None
                and st.get("range") is not None):
            return {"duration": st["duration"], "range": st["range"]}
    return None


# --- item procs vs rotation passives ----------------------------------------

@lru_cache(maxsize=1)
def _item_granted_skill_ids() -> frozenset[str]:
    """Every skill id at least one catalog item grants, from the ITEM side.

    Built by walking the items rather than the reverse lookup
    (`items.weapons_for_skill`) because this has to be a whole-set answer: the
    question is "does gear grant this at all", not "which weapon".

    Imported lazily because `items.catalog` imports this module.
    """
    try:
        from .items import catalog
    except Exception:
        return frozenset()
    out: set[str] = set()
    for it in catalog.items():
        out.update(catalog._weapon_skill_ids(it["id"]))
    return frozenset(out)


# The gear system grants every weapon a `<Type>_Upgrade` passive ("Weapon
# Upgraded") at each upgrade rank, and NO item row references it — so it is a
# genuine item proc that the reverse lookup can never find. Matched on the
# sheet's own name, the same signal `items.weapon_upgrade_passive` uses to
# build `<Type>_Upgrade`, so the two cannot drift.
_WEAPON_UPGRADED_NAME = "Weapon Upgraded"


@lru_cache(maxsize=8192)
def item_proc_source(skill_id: str) -> dict | None:
    """The item that grants `skill_id` as a PROC, or None.

    An item proc is damage you get from GEAR, and it reads differently from a
    passive in your rotation: Bloodrage Aura is not something you press, and
    a meter that lists both as "(Passive)" cannot say which is which. This
    returns `{"name", "type", "id"}` for the granting item so the row can say
    where the number came from.

    Three deliberate exclusions, each because the naive rule is FALSE here
    rather than merely imprecise (measured over the shipped sheets by
    ai/workspace/buffy/probe_item_procs3.py):

    - **Base attacks / actives / combos / powers.** Gear lists a weapon's
      whole rotation, so "an item grants this" is true of 230 skills — 65 of
      them the base attacks you swing every fight. Only a Passive-nature row
      can be a proc.
    - **Class talents.** 11 of them are listed by the augment sigils
      (11 same-named "Sigil of Bet'Hatesht" rows, one talent each). A talent
      in your TREE is a rotation passive whether or not a sigil also names it,
      so tagging it would misreport the tree.
    - **The name check.** Resolved through the ANCESTOR, because a proc's own
      ticks arrive on a derived row (`..._Passive_Status`) that no item lists;
      without that walk a proc shows untagged whenever the parent never cast.
    """
    sid = skill_id or ""
    # Walk up to the row that actually carries the passive. A derived row's
    # own nature is Status (see _NATURE_LABELS), so checking the row itself
    # would reject every tick a proc produces.
    candidates = [sid]
    parent = names.parent_skill_id(sid)
    if parent and parent != sid:
        candidates.append(parent)

    for cand in candidates:
        if "Talent" in cand:
            continue
        row = skill_row(cand)
        if not row:
            continue
        texts = row.get("texts") or {}
        if texts.get("name") == _WEAPON_UPGRADED_NAME:
            # No item row references it; name the weapon FAMILY, which is what
            # the upgrade ladder is keyed to.
            fam = cand[: -len("_Upgrade")] if cand.endswith("_Upgrade") else ""
            return {"id": "", "name": "Weapon Upgraded", "type": fam}
        if cand not in _item_granted_skill_ids():
            continue
        if _NATURE_LABELS.get(row.get("nature")) != "Passive":
            continue
        from .items import weapons_for_skill
        for w in weapons_for_skill(cand):
            return {"id": w.get("id", ""), "name": w.get("name", ""),
                    "type": w.get("type", "")}
    return None


# --- summons ---------------------------------------------------------------

# Leading skill-id tokens that name a WEAPON family. Copied from core/dps_data's
# `_WEAPON_TOKENS` (the keys) rather than imported: `data/` must not reach up
# into `core/`. Same list, same order-of-magnitude, kept beside the rule that
# uses it so the two cannot drift far.
_WEAPON_TOKENS = frozenset({
    "sword", "greatsword", "dualswords", "shield", "axe", "greataxe",
    "dualaxes", "mace", "greatmace", "dualmaces", "dagger", "daggers",
    "fists", "spear", "staff", "scepter", "bow", "thrown", "crescent",
    "halos", "book",
})
_CLASS_TOKENS = ("warrior", "rogue", "mage", "priest")

# Two summon families whose WEAPON is not in the shipped item catalog, so the
# family token above cannot recognise them. Named here rather than guessed:
# `SummonBee_*` is the bee harvester's bolt and `Summon_Imp_Auto` the imp's,
# both weapon-summon rotations (live report 2026-10-03: "i only want weapon
# summons").
_CATALOG_BLIND_WEAPON_SUMMONS = ("summonbee", "summon")


def _family_token(skill_id: str) -> str:
    """Leading skill-id token, cleaned for the weapon-family test."""
    parts = (skill_id or "").strip().split("_")
    tok = parts[0].lower()
    if tok in _CLASS_TOKENS and len(parts) > 1:
        tok = parts[1].lower()
    return re.sub(r"\d+$", "", tok)


@lru_cache(maxsize=8192)
def is_summon(skill_id: str) -> bool:
    """True when `skill_id` belongs to a WEAPON's summon family.

    Scoped to weapon summons deliberately (reported live 2026-10-03: "why is it
    talking about pet, i only want weapon summons"). A bare "summon" substring
    also matches the BOSS encounters' own scripts — `Ulserous_Summon`,
    `Phrixes_Demon_Summon`, `Faerie_Demon_Summon`, `Cleodora_ChampionSummon` —
    and tagging those "summon" told the player their own summon did damage the
    boss, which is false: they are the fight's spawn.

    So the family must ALSO be a weapon: the id's leading token is a weapon
    family (`Staff_SummonDemon_*` is the Silhouette of Almaz staff), or it is
    one of the two catalog-blind weapon summons above. Matched on the id and
    the sheet's name, because the families spell it differently, and derived
    rows are covered by walking to `parent_skill_id` — the same ancestor walk
    `item_proc_source` uses, because a summon attack often arrives on a child.
    """
    sid = skill_id or ""
    for cand in (sid, names.parent_skill_id(sid) or ""):
        if not cand:
            continue
        token = _family_token(cand)
        if token not in _WEAPON_TOKENS and token not in _CATALOG_BLIND_WEAPON_SUMMONS:
            continue
        if re.search("summon", cand, re.I):
            return True
        row = skill_row(cand)
        texts = (row or {}).get("texts") or {}
        name = texts.get("name") or ""
        if name and re.search("summon", name, re.I):
            return True
    return False


def skill_meta(skill_id: str) -> dict:
    """The skill's numeric stat line: {cooldown, range} where the sheet
    has them — the SAME values the ::cooldown:: / ::range:: template
    slots resolve to (seconds / meters), so the meta line never disagrees
    with the description text. Keys absent when the sheet has no value.
    Step ranges may be template strings ('range' -> a var), so only
    numbers count."""
    row = skill_row(skill_id)
    if not row:
        return {}
    out: dict = {}
    cd = _value("cooldown", row)
    if isinstance(cd, (int, float)):
        out["cooldown"] = cd
    rng = _value("range", row)
    if isinstance(rng, (int, float)):
        out["range"] = rng
    return out


def skill_description(skill_id: str, rank: int = 0) -> str | None:
    """The skill's description at `rank`, with every template slot resolved
    to a readable value (or a neutral placeholder). rank 0 (the default,
    unchanged) is the base skill: its own description with the base values.
    rank N >= 1 returns the per-rank description (texts.rankDescs[N-1] —
    what that upgrade grants) when the sheet has one, else the base
    description — and always resolves the numbers with the rank's values:
    props.rankOverride entries whose minRank <= N override their vars /
    props from that rank on (cumulatively), so 'The bone then jumps to
    ::var3:: enemies.' reads 3 at rank 2 instead of 2. None when the skill
    or its description is missing — callers show the name only."""
    row = skill_row(skill_id)
    if not row:
        return None
    row = _row_at_rank(row, rank)
    desc = None
    if rank > 0:
        descs = (row.get("texts") or {}).get("rankDescs") or []
        idx = rank - 1
        if 0 <= idx < len(descs):
            desc = descs[idx].get("desc")
    if not desc:
        desc = (row.get("texts") or {}).get("desc")
    if not desc:
        return None
    return _resolve(desc, row, frozenset(), rank)


def skill_rank_descriptions(skill_id: str) -> list[str]:
    """The skill's per-rank descriptions (texts.rankDescs) in order, each
    resolved at its own rank (rank N's line uses the values in effect at
    rank N, so a line that reads 'increased to ::var1%::' shows the
    rank-scaled number). [] when the sheet has no rank descriptions — the
    base description then serves every rank."""
    row = skill_row(skill_id)
    if not row:
        return []
    out = []
    for i, rd in enumerate((row.get("texts") or {}).get("rankDescs") or []):
        desc = rd.get("desc")
        if not desc:
            continue
        rrow = _row_at_rank(row, i + 1)
        out.append(_resolve(desc, rrow, frozenset(), i + 1))
    return out


# The infusion skill affixes' attribute ids -> display names (the gear
# stat vocabulary). 'Mastery' attributes are the physical/magic ratings.
_AFFIX_ATTR_LABELS = {
    "CritChance": "Critical Chance",
    "Armor": "Armor",
    "Vitality": "Vitality",
    "Fervor": "Fervor",
    "MagicMastery": "Magic Mastery",
    "PhysicalMastery": "Physical Mastery",
    "ArmorPenetration": "Armor Penetration",
    "SpellPenetration": "Spell Penetration",
}


def skill_set_bonus_affixes(skill_id: str) -> list[str]:
    """The skill's set-bonus stat lines, from its `affixes` rows (the
    infusion skills' (4) Set bonus: each affix grants an attribute when
    its minRank threshold is met — rank 2 = the (6)-piece set tier in the
    crucible UI). Flat affixes are points ('+2.5% Critical Chance'),
    ARatio affixes are fractions of the base ('+2.5% Armor'); both render
    as the percentage the game shows. [] when the skill has no
    threshold-gated affixes."""
    row = skill_row(skill_id)
    if not row:
        return []
    out = []
    for a in row.get("affixes") or []:
        if (a.get("conds") or {}).get("minRank") != 2:
            continue
        attr = (a.get("target") or {}).get("attribute") or ""
        label = _AFFIX_ATTR_LABELS.get(attr, attr)
        if not label:
            continue
        ref = a.get("ref") or ""
        val = a.get("val") or 0
        if ref == "TAttribute_ARatio":
            out.append(f"+{val * 100:g}% {label}")
        else:
            out.append(f"+{val:g}% {label}")
    return out


def _row_at_rank(row: dict, rank: int) -> dict:
    """A shallow copy of a skill row with the rank's values applied: base
    vars plus every props.rankOverride entry whose minRank <= rank (the
    entries are cumulative — each overrides its named vars and scalar
    props from that rank on). rank 0 (the base skill) returns the row
    unchanged, so the base description never sees overrides."""
    if rank <= 0 or not row:
        return row
    out = dict(row)
    vars_ = dict(row.get("vars") or {})
    scalars: dict = {}
    for o in (row.get("props") or {}).get("rankOverride") or []:
        if (o.get("minRank") or 1) > rank:
            continue
        if isinstance(o.get("vars"), dict):
            vars_.update(o["vars"])
        if isinstance(o.get("props"), dict):
            scalars.update({k: v for k, v in o["props"].items()
                            if isinstance(v, (int, float))})
    if vars_ != (row.get("vars") or {}):
        out["vars"] = vars_
    if scalars:
        out.update(scalars)     # duration / cooldown / charges promoted
    return out


def _resolve(text: str, row: dict, seen: frozenset, rank: int = 0) -> str:
    text = _BRACKET_RE.sub(lambda m: _bracket_label(m.group(1)), text)
    return _TOKEN_RE.sub(lambda m: _token(m.group(1), row, seen, rank), text)


def _token(tok: str, row: dict, seen: frozenset, rank: int = 0) -> str:
    """Resolve one '::...::' slot to text. `seen` holds the ref chain so a
    self-referencing row falls back instead of recursing forever; `rank` is
    the rank whose values apply (ref targets get the same rank)."""
    base = tok.rstrip("#")                 # 'dmg#' -> 'dmg', 'var1%#' -> 'var1%'
    pct = base.endswith("%")
    if pct:
        base = base[:-1]
    # ::name:: -> the row's own display name
    if base == "name":
        nm = (row.get("texts") or {}).get("name")
        return nm or names.skill_name(row.get("id") or "") or "the skill"
    # ::ref_*:: / ::ref1_*:: / ::ref2_*:: / ::ref3_*:: resolve the inner
    # slot on the row texts.refs points at (cycle-guarded)
    m = _REF_RE.fullmatch(base)
    if m:
        prefix, sub = m.group(1), m.group(2)
        sub_base = sub.rstrip("#")
        sub_pct = sub_base.endswith("%")
        if sub_pct:
            sub_base = sub_base[:-1]
        refs = (row.get("texts") or {}).get("refs") or {}
        key = "ref" if prefix in ("ref", "ref1") else prefix
        rid = refs.get(key) or refs.get("ref")
        if rid and isinstance(rid, str) and rid not in seen:
            rrow = _row_at_rank(_rows().get(rid), rank)
            if rrow:
                if sub == "name":
                    nm = (rrow.get("texts") or {}).get("name")
                    return nm or names.skill_name(rid) or "the skill"
                val = _value(sub_base, rrow)
                if val is not None:
                    return _fmt_value(val, sub_pct or pct, sub_base)
        # no value on the ref row: neutral placeholder beats a made-up
        # number — 'X stacks' reads better than the noun doubling the
        # prose ('stacks stacks'); damage/heal/shield keep their noun
        # ('dealing damage')
        if sub_base in ("dmg", "dmgs", "dmg2", "dmg3", "heal", "shield"):
            return _FALLBACKS[sub_base]
        return "X%" if (sub_pct or pct) else "X"
    # plain value slot
    val = _value(base, row)
    if val is not None:
        return _fmt_value(val, pct, base)
    return _fallback(base, pct)


def _value(name: str, row: dict):
    """The numeric/string value behind a slot name, from the row's own
    data: its `vars` map, scalar fields, the first step's range, or scalar
    props (::charges::, ::duration:: read props.rankOverride-promoted
    values)."""
    vars_ = row.get("vars") or {}
    if name in vars_:
        return vars_[name]
    if name in ("dmg", "dmgs") and "damage" in vars_:
        return vars_["damage"]
    if name in ("duration", "dur") and row.get("duration") is not None:
        return row["duration"]
    if name == "cooldown" and row.get("cooldown") is not None:
        return row["cooldown"]
    if name == "range":
        for step in row.get("steps") or []:
            if step.get("range") is not None:
                return step["range"]
    props = row.get("props") or {}
    if name in props and isinstance(props[name], (int, float)):
        return props[name]
    return None


def _fmt(val, pct: bool) -> str:
    """Format a raw value. '%' slots are percents (0.4 -> '40%'); whole
    numbers stay integers ('2'); small ratios (< 1) in non-unit slots
    read as percents even without the '%' suffix ('0.15' would be
    unreadable next to 'dealing' — ::chance::, ::dmg::). Unit-bearing
    slots (range / duration / time / cooldown) never reach this path:
    _fmt_value renders them as plain quantities with their unit."""
    if isinstance(val, str):
        return val
    if pct:
        return f"{round(val * 100):g}%"
    if isinstance(val, float) and val.is_integer():
        return str(int(val))
    if isinstance(val, int):
        return str(val)
    if 0 < val < 1:
        return f"{round(val * 100):g}%"
    return f"{val:g}"


def _fmt_value(val, pct: bool, base: str) -> str:
    """Format a resolved value and append the unit the sheet leaves
    implicit: ranges are meters, durations/cooldowns are seconds
    ('within 40m', 'over 8s')."""
    if isinstance(val, str):
        return val
    if not pct and base in _UNITS:
        # unit-bearing slots are QUANTITIES with their unit, never the
        # percent heuristic: ::time2:: = 0.25 reads '0.25s', not '25%s'.
        # Percent formatting stays for ratio slots (::chance::, ::dmg::).
        num = int(val) if isinstance(val, float) and val.is_integer() \
            else f"{val:g}"
        return f"{num}{_UNITS[base]}"
    return _fmt(val, pct)


def _fallback(base: str, pct: bool) -> str:
    if base in _FALLBACKS:
        return _FALLBACKS[base]
    return "X%" if pct else "X"


def stat_label(attr: str) -> str:
    """A raw sheet attribute name ('CritChance', 'PhysicalMastery') -> the
    readable label the skill prose uses ('Critical Chance', 'Physical
    Mastery'), humanized when the sheet has no entry."""
    return _STAT_REFS.get(attr) or names.humanize(attr) or attr


def _bracket_label(x: str) -> str:
    """One [X] reference -> readable text: known stat name, a referenced
    skill's name, a bare literal ([1.5]), else a humanized token."""
    lbl = _STAT_REFS.get(x)
    if lbl:
        return lbl
    if "_" in x and re.search(r"[A-Za-z]", x):
        nm = names.skill_name(x)
        if nm and nm != x:
            return nm
    if re.fullmatch(r"[.\d]+", x):
        return x
    return names.humanize(x) or x
