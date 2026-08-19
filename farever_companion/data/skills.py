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
    """Skill id -> row. The FULL raw sheet first (texts/vars/steps — what
    description resolution needs), else the compiled shim (id/type/nature/
    name only, so types still resolve without the raw sheet)."""
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
    if out:
        return out
    return cdb.by_id("skill")


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
