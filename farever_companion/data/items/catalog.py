"""Raw access to the bundled item_drops.json index: item lookup and search."""
from __future__ import annotations

import json
import re
from functools import lru_cache

from ... import paths
from ...rules import RARITY_ORDER
from .. import cdb
from .labels import class_label

try:
    from .. import raw_item_drops
except ImportError:
    raw_item_drops = None

@lru_cache(maxsize=1)
def _shop_ids() -> frozenset[str]:
    """Item ids a shop counter sells — the drops index's vendor rows.

    Lazily imports `sources` (which imports this module) so there is no import
    cycle; this runs at call time, well after both modules have loaded.
    """
    try:
        from . import sources as _sources
        return _sources.shop_item_ids()
    except Exception:
        return frozenset()


_SHOP_ID_RE = re.compile(r"(?i)_shop")


def is_shop_item(item_id: str) -> bool:
    """True for cash-shop cosmetic gear (no drops).

    This is the shop SOURCE, not the browse rule: the Items lists do NOT hide
    by it directly. Changing where the set comes from (`_shop_ids` /
    `sources.shop_item_ids`) changes this predicate, so read the browse
    contract first — `ui/pages/items/support.is_hidden_shop_item` is the one
    gate the lists use, and it keeps sourced EQUIPMENT in however the shop set
    widens (see `tests/test_items_shop_gate.py`)."""
    uid = item_id or ""
    return uid in _shop_ids() or bool(_SHOP_ID_RE.search(uid))


@lru_cache(maxsize=1)
def _data() -> dict:
    """Parsed item_drops.json payload from shim or loose JSON.

    InfusionPattern rows get their display name normalized at load
    ('InfusionPattern_Bee_Support' -> 'Bee Support'): the manifest ships
    the raw id as the name (and one unresolved 'Infusion: ::ref_skill::'
    token), and every surface — the Items list, the detail card, the
    Infusions tab, the dungeon-tab tiles — renders the row's `name`, so
    the cleanup happens once here instead of at each call site. The
    'Infusion Pattern' context already rides the surrounding labels."""
    if raw_item_drops is not None:
        data = getattr(raw_item_drops, "DATA", None)
        if data and data.get("items"):
            _normalize_infusion_pattern_names(data)
            return data
    try:
        data = json.loads(paths.item_drops_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    _normalize_infusion_pattern_names(data)
    return data


def _normalize_infusion_pattern_names(data: dict) -> None:
    """In-place display-name cleanup for the InfusionPattern rows
    (see _data) — id suffixes are 'Bee_DPS'-shaped, so the name is the
    two words the game's own UI uses for the variant."""
    for iid, it in (data.get("items") or {}).items():
        if not str(iid).startswith("InfusionPattern_"):
            continue
        parts = str(iid).split("_")
        if len(parts) >= 3:
            it["name"] = f"{parts[1]} {parts[2]}"


@lru_cache(maxsize=1)
def _sources() -> list[dict]:
    return _data().get("_sources", [])


@lru_cache(maxsize=1)
def _locs() -> list[str]:
    return _data().get("_locs", [])


@lru_cache(maxsize=1)
def _tables() -> list[str]:
    return _data().get("_tables", [])


def available() -> bool:
    """True when the bundled item database is reachable."""
    return bool(_data().get("items"))


@lru_cache(maxsize=1)
def items() -> list[dict]:
    """Every item flattened and sorted by display name. The names are the
    sheets' own, verbatim: a row whose name IS its id ('Back_RBee_Wiz') is
    the test-server data saying it has no name yet, and the UI shows that id
    rather than inventing one — a made-up name reads like real data and hides
    the gap."""
    out = [{"id": iid, **it} for iid, it in _data().get("items", {}).items()]
    out.sort(key=lambda r: (r.get("name") or r["id"]).lower())
    return out


def item(item_id: str) -> dict | None:
    row = _data().get("items", {}).get(item_id)
    return {"id": item_id, **row} if row else None


@lru_cache(maxsize=1)
def _id_by_name() -> dict[str, str]:
    """Lowercased display name -> item id (first row wins on duplicates)."""
    out: dict[str, str] = {}
    for it in items():
        nm = it.get("name")
        if nm:
            out.setdefault(nm.lower(), it["id"])
    return out


def item_id_by_name(name: str) -> str | None:
    """Item id whose display name matches `name` (case-insensitive), or None.

    Live-placed food resolves its display name off an st.skill.Skill and never
    carries a string id in memory; this reverse lookup maps that name back to
    the stable item id (e.g. 'Plainswalker Feast' -> 'Feast')."""
    return _id_by_name().get((name or "").strip().lower())


def resolve_food_info(name: str | None) -> tuple[str, str]:
    """Resolve raw food station name / skill / item string into (display_name, item_id).

    Robustly maps item IDs ('Feast', 'Cook_1', 'SmallAlchemistCauldron'),
    display names ('Plainswalker Feast', 'Minor Alchemist Cauldron'),
    skill identifiers ('PrepareWorldConsumable'), camelCase strings,
    and missing/None values so food rows always have valid icons and names.
    """
    if not name:
        return ("Plainswalker Feast", "Feast")
    raw = str(name).strip()
    if not raw or raw.lower() in ("food", "prepareworldconsumable", "worldconsumable"):
        return ("Plainswalker Feast", "Feast")

    # 1. Direct ID match in catalog
    it = item(raw)
    if it:
        return (it.get("name") or raw, raw)

    # 2. Case-insensitive item_id match
    for iid, row in _data().get("items", {}).items():
        if iid.lower() == raw.lower():
            return (row.get("name") or iid, iid)

    # 3. Direct display name lookup
    iid = item_id_by_name(raw)
    if iid:
        it = item(iid)
        return ((it.get("name") if it else raw) or raw, iid)

    # 4. Try stripping prefixes
    clean = re.sub(r'^(Food_|Skill_|Skill_Food_|Items_Loot_Cook_|Items_Loot_)', '', raw, flags=re.IGNORECASE)
    if clean != raw:
        it = item(clean)
        if it:
            return (it.get("name") or clean, clean)
        iid = item_id_by_name(clean)
        if iid:
            it = item(iid)
            return ((it.get("name") if it else clean) or clean, iid)

    # 5. Try humanizing camelCase (e.g. PlainswalkerFeast -> Plainswalker Feast)
    hum = re.sub(r'([a-z])([A-Z])', r'\1 \2', clean).replace('_', ' ').strip()
    iid = item_id_by_name(hum)
    if iid:
        it = item(iid)
        return ((it.get("name") if it else hum) or hum, iid)

    return (hum or raw, "Feast")


def search(query: str = "", item_type: str = "", rarity: str = "") -> list[dict]:
    """Items matching query (name/id/type) plus optional filters."""
    q = (query or "").strip().lower()
    out = []
    for it in items():
        if rarity and it.get("rarity") != rarity:
            continue
        if item_type and it.get("type") != item_type:
            continue
        if q:
            hay = f"{it.get('name') or ''} {it['id']} {it.get('type') or ''}".lower()
            if q not in hay and not matches_skill(it["id"], q) and not matches_class(it["id"], q):
                continue
        out.append(it)
    return out


@lru_cache(maxsize=1)
def types() -> list[str]:
    """All sorted item type ids."""
    return sorted({it.get("type") for it in items() if it.get("type")})


#: The game's rarity ladder, common -> rarest (the order of the game's own
#: `rarity.json`). Public because a rarity is only half a fact wherever a box
#: or a craft ROLLS it: "at least Epic" means the rungs from Epic to the top,
#: and the top is this tuple's last entry, not a number restated per call site.
#: Re-exported from the shared `rules` module so this name keeps resolving here
#: (no call site moves) while the compiler reads the same tuple.


@lru_cache(maxsize=1)
def rarities() -> list[str]:
    have = {it.get("rarity") for it in items() if it.get("rarity")}
    return [r for r in RARITY_ORDER if r in have]


@lru_cache(maxsize=1)
def _raw_item_lines() -> list[dict]:
    lines = cdb.lines("item")
    if lines:
        return lines
    try:
        data = json.loads(paths.sheets_dir().joinpath("item.json").read_text(encoding="utf-8"))
        return data.get("lines", [])
    except (OSError, ValueError):
        return []


@lru_cache(maxsize=1)
def _raw_item_skills() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for it in items():
        iid = it.get("id")
        sk = it.get("skills")
        if iid and sk:
            out[iid] = [s.get("skill") if isinstance(s, dict) else s for s in sk if s]
    if out:
        return out
    for r in _raw_item_lines():
        iid = r.get("id")
        if not iid:
            continue
        sk = [s.get("skill") if isinstance(s, dict) else s for s in (r.get("skills") or [])]
        sk = [s for s in sk if s]
        if sk:
            out[iid] = sk
    return out


@lru_cache(maxsize=1)
def _raw_item_sheet_lines() -> list[dict]:
    """The SOURCE item sheet (assets/data/item.json), full `props` intact.

    `_raw_item_lines()` returns the COMPILED sheet, which the compiler prunes
    to the keys the runtime reads — dropping `props`, and with it every
    consumable's effect duration. Anything that needs `props` has to read the
    source sheet; the compiled rows stay the fallback for a build that ships
    only the shim.
    """
    try:
        data = json.loads(paths.sheets_dir().joinpath("item.json")
                          .read_text(encoding="utf-8"))
        return data.get("lines", []) or []
    except (OSError, ValueError):
        return []


@lru_cache(maxsize=1)
def _item_effect_durations() -> dict[str, int]:
    """item id -> its consumable status-effect duration, in seconds.

    Reads the SOURCE sheet first (see `_raw_item_sheet_lines`): the compiler
    stamps only `statusRefs`, so the durations the Food/Elixir cards show per
    row (the Feast's 1H against the 15M norm) are unreachable from the
    compiled sheet alone.
    """
    out: dict[str, int] = {}
    for source in (_raw_item_sheet_lines(), _raw_item_lines()):
        for r in source:
            iid = r.get("id")
            if not iid or iid in out:
                continue
            try:
                dur = r["props"]["effects"][0]["status"][0]["duration"]
                if dur is not None:
                    out[iid] = dur
            except (KeyError, IndexError, TypeError):
                continue
    return out


def item_effect_duration(item_id: str) -> int | None:
    """Consumable status-effect duration in seconds."""
    return _item_effect_durations().get(item_id)


@lru_cache(maxsize=1024)
def _weapon_skill_ids(item_id: str) -> list[str]:
    it = _data().get("items", {}).get(item_id) or {}
    ids = it.get("skills")
    if ids is None:
        ids = _raw_item_skills().get(item_id)
    return [s.get("skill") if isinstance(s, dict) else str(s) for s in (ids or []) if s]


@lru_cache(maxsize=None)
def weapons_for_skill(skill_id: str) -> tuple[dict, ...]:
    """The weapons whose skill list contains `skill_id`, as
    {"id", "name", "type"} rows (catalog order, deduped by id). This is
    the reverse of ``weapon_skills``: given a skill the DPS meter tracked,
    which weapons actually grant it. Empty for pure-cast skills (Sunlight,
    boss AoE) that no weapon carries — callers skip the "from weapon" line.
    """
    q = (skill_id or "").strip().lower()
    if not q:
        return ()
    out: list[dict] = []
    seen: set[str] = set()
    for it in items():
        if q in {s.lower() for s in _weapon_skill_ids(it["id"])}:
            if it["id"] in seen:
                continue
            seen.add(it["id"])
            out.append({"id": it["id"],
                        "name": it.get("name") or it["id"],
                        "type": it.get("type") or ""})
    return tuple(out)


@lru_cache(maxsize=None)
def _skill_forms(item_id: str) -> tuple[str, ...]:
    out: list[str] = []
    for s in weapon_skills(item_id):
        if s.get("name"):
            out.append(s["name"].lower())
        if s.get("id"):
            out.append(s["id"].lower())
        d = s.get("description")
        if d:
            out.append(d.lower())
    return tuple(dict.fromkeys(f for f in out if f))


@lru_cache(maxsize=None)
def matches_skill(item_id: str, q: str) -> bool:
    """True when query matches item's weapon skill name, id, or description."""
    q = (q or "").strip().lower()
    return bool(q and any(q in t for t in _skill_forms(item_id)))


@lru_cache(maxsize=1024)
def matches_class(item_id: str, q: str) -> bool:
    """True when query matches one of the item's classes or aptitudes."""
    q = (q or "").strip().lower()
    if not q:
        return False
    it = _data().get("items", {}).get(item_id) or {}
    for c in it.get("classes") or []:
        if q in class_label(c).lower() or q in str(c).lower():
            return True
    return False


@lru_cache(maxsize=None)
def matched_skill_labels(item_id: str, q: str) -> tuple[str, ...]:
    """Skill names matching query for tag display."""
    q = (q or "").strip().lower()
    if not q:
        return ()
    out = []
    for s in weapon_skills(item_id):
        nm = s.get("name") or ""
        if not nm:
            continue
        if q in nm.lower() or q in (s.get("id") or "").lower() or q in (s.get("description") or "").lower():
            out.append(nm)
    return tuple(dict.fromkeys(out))


@lru_cache(maxsize=1024)
def weapon_skills(item_id: str) -> list[dict]:
    """Resolved weapon skills: [{id, name, type, description}]."""
    ids = _weapon_skill_ids(item_id)
    if not ids:
        return []
    from .. import names, skills
    out = []
    for sid in ids:
        row = skills.skill_row(sid) or {}
        texts = row.get("texts") or {}
        out.append({
            "id": sid,
            "name": texts.get("name") or names.skill_name(sid) or sid,
            "type": skills.skill_type(sid),
            "description": skills.skill_description(sid),
        })
    return out


@lru_cache(maxsize=1024)
def item_special_traits(item_id: str) -> list[dict]:
    """Special item affixes and passive traits ONLY (actives & combos stay
    in SkillBar): [{title, text, type, skill}].

    `skill` carries the skill id on the entries that came from the item's own
    weapon skills (Passive / Status) and is empty on the authored
    effect/text/trait/affix/proc_text ones. Surfaces that render the SkillBar
    next to this list can use it to leave the weapon's own passives to the
    bar's tiles — the two read the same `weapon_skills()` rows, so re-listing
    them put the same sentence on screen twice."""
    it = _data().get("items", {}).get(item_id) or {}
    out: list[dict] = []

    # 1. Explicit item text/trait/effect/affix fields
    for key in ("effect", "text", "trait", "affix", "proc_text"):
        val = it.get(key)
        if isinstance(val, str) and val.strip():
            txt = val.strip()
            if not any(t["text"] == txt for t in out):
                out.append({
                    "title": key.capitalize(),
                    "text": txt,
                    "type": "Special Affix",
                    "skill": "",
                })

    # 2. ONLY Passive or Status skills attached to the item
    for s in weapon_skills(item_id):
        stype = s.get("type") or ""
        if stype in ("Passive", "Status"):
            desc = (s.get("description") or "").strip()
            txt = desc or s.get("name") or ""
            if txt and not any(t["text"] == txt for t in out):
                out.append({
                    "title": s.get("name") or "Special Trait",
                    "text": txt,
                    "type": "Passive",
                    "skill": s.get("id") or "",
                })

    return out


# The gear-upgrade system grants every weapon the `<Type>_Upgrade` skill
# ("Weapon Upgraded") at each upgrade rank, and THAT skill is where the
# per-rank bonus lives — no item row references it, so nothing else in the
# app can reach it. All 20 weapon types have it, stated two ways:
#
#   attribute — a per-rank `affixes` ladder keyed to the skill's own rank
#     ({minRank: 3, maxRank: 3} -> rank 3): Axe / DualAxes / GreatAxe
#     CritChance +1..+5%, Fists / Book Physical / Magic Mastery +4..+8%,
#     Spear / Scepter Armor / Spell Penetration +4..+8%, Staff Cooldown
#     Reduction +2..+6%.
#   effect — no affix at all: the bonus is a script effect whose number
#     lives in the row's `vars` and grows through `props.rankOverride`
#     entries (minRank -> vars, cumulative): Sword / GreatSword / DualSwords
#     4..8% double-attack chance, Mace / DualMaces / GreatMace 4..8% stun
#     chance, Daggers / Bow / Crescent 8..16% damage, Halos 4..8% cooldown,
#     Thrown 4..8% crit, Shield 5..13% damage reduction.
#
# The two paths are STATEMENT SHAPES, not two ladders: audited 2026-09-15 over
# all 20 types, both are value(rank) for ranks 1..5 and agree rank for rank.
# The affix path keys each value to ONE rank (its first entry carries only
# `maxRank: 1`, i.e. "rank 1 and below"); the effect path's base `vars` is
# rank 1 with every rankOverride entry at minRank 2..5, cumulative. So one
# rank rule serves both and nothing here may branch on `kind`.
#
# The granted skill's rank is NOT the gear's upgrade level: the live game
# shifts it by the piece's RARITY. In game 2026-09-15 a Cheese Moon tooltip at
# upgrade +3 reads Critical Chance 2% (Rare) / 3% (Epic) / 4% (Legendary) —
# the sheet ladder's ranks 2 / 3 / 4 with all three pieces at +3. So rarity
# again decides: it caps how many ranks exist (Rare 3 / Epic 4 / Legendary 5,
# from rarity.json) AND shifts which rank a given +N receives — see
# stats.granted_rank, the one place that mapping lives. Values are never
# restated here; only the rank index moves.
#
# One candidate for that shift is measured, not guessed: constant.json's
# `GearUpgrades > SkillUnlockLevel` is 3 — the only rank-ish value in the
# gear-upgrade data (WeaponSkill_MaxRank 3 and WeaponKills_PerSkillRankPoint
# 20 belong to the weapon-SKILL ladder, a different system). Read as "the
# granted passive unlocks at upgrade +3", it fits the live reading: every
# rarity column the card draws sits at that rarity's cap (Rare 3 / Epic 4 /
# Legendary 5), so all three are at or past +3 and the gate never hides a
# value we show. It does NOT say which rank +3 receives — the sheets contain
# no per-rarity rank map at all — so granted_rank's cap-minus-one stays
# calibrated from the game and the -1 lives in that one function.
# ai/workspace/buffy/weapon_upgrade_probe.py measures it live (it reads the
# hero's CritChance attribute per upgrade step) if a patch ever moves it.

_PERCENT_RE = re.compile(r"(\d+(?:\.\d+)?)%")


def _first_percent(text: str) -> float | None:
    """The percentage a resolved sheet sentence states, or None. Only a
    FALLBACK: both ladders are read numerically (see _effect_ladder_var), and
    the rendered sentence is used only for a shape we have not seen — never a
    figure we invent."""
    m = _PERCENT_RE.search(text or "")
    return float(m.group(1)) if m else None


def _effect_ladder_var(row: dict) -> str:
    """The single var a row's props.rankOverride scales — the key its ladder
    is written in (Sword's `chance`, Bow's `damage`, Shield's `damage`). ""
    when the overrides scale nothing, more than one var, or a non-number.
    That key is what makes the effect path read like the affix one: both are
    value(rank) for ranks 1..5, so no caller needs to know which it is."""
    keys: set[str] = set()
    for o in (row.get("props") or {}).get("rankOverride") or []:
        keys.update((o.get("vars") or {}).keys())
    if len(keys) != 1:
        return ""
    key = next(iter(keys))
    base = (row.get("vars") or {}).get(key)
    return key if isinstance(base, (int, float)) else ""


def _effect_value_at(row: dict, key: str, rank: int) -> float | None:
    """`key`'s value at `rank`: the base `vars` entry (which is the ladder's
    rank 1 — every override starts at minRank 2) with each rankOverride entry
    whose minRank <= rank applied over it, cumulatively — the same promotion
    skills._row_at_rank models for the sheet's prose."""
    val = (row.get("vars") or {}).get(key)
    for o in (row.get("props") or {}).get("rankOverride") or []:
        if int(o.get("minRank") or 1) > rank:
            continue
        v = (o.get("vars") or {}).get(key)
        if isinstance(v, (int, float)):
            val = v
    return float(val) if isinstance(val, (int, float)) else None


@lru_cache(maxsize=1024)
def weapon_upgrade_passive(item_id: str) -> dict | None:
    """The weapon's "Weapon Upgraded" passive as a per-rank ladder:
    {id, name, description, kind, attr, values, per_rank, max_rank} — None
    when the piece has no upgrade passive at all (armor, jewelry, non-gear).

    `kind` is "attribute" for the affix ladder and "effect" for the
    rankOverride one. `values` holds the raw per-rank number on the attribute
    path ({1: 1 ... 5: 5}) and stays empty on the effect path, where the row
    states the same thing as a base `vars` number plus cumulative
    props.rankOverride entries (see _effect_value_at) — read numerically, so
    neither path's value depends on how the sheet's prose is worded.
    `per_rank[r]` is the short value text both paths share ("+3%" / "12%") —
    what the trait line groups per rarity.
    """
    it = item(item_id) or {}
    typ = it.get("type") or ""
    if not typ:
        return None
    from .. import skills
    sid = f"{typ}_Upgrade"
    row = skills.skill_row(sid)
    if not row:
        return None
    attr = ""
    values: dict[int, float] = {}
    for a in row.get("affixes") or []:
        target_attr = (a.get("target") or {}).get("attribute") or ""
        conds = a.get("conds") or {}
        rank = conds.get("minRank") or conds.get("maxRank")
        val = a.get("val")
        if not target_attr or rank is None or val is None:
            continue
        if attr and target_attr != attr:
            continue        # a mixed ladder: keep the first attribute only
        attr = target_attr
        values[int(rank)] = val
    if values:
        kind = "attribute"
        per_rank = {r: f"+{v:g}%" for r, v in values.items()}
    else:
        kind = "effect"
        # Read the ladder's NUMBERS off the row, exactly as the affix path
        # does, rather than scraping the first "%" out of the rendered
        # sentence: the sentence carries other figures too (a range, a
        # duration, a required enemy count) and only one of them is the
        # ladder. Both paths therefore arrive as value(rank) for ranks 1..5.
        per_rank = {}
        key = _effect_ladder_var(row)
        if key:
            for r in range(1, 6):
                val = _effect_value_at(row, key, r)
                if val is not None:
                    per_rank[r] = f"{val * 100:g}%"
        if not per_rank:
            # an unseen shape: fall back to the sheet's own sentence, resolved
            # AT each rank, so the number is still the sheet's, never ours
            for r in range(1, 6):
                pct = _first_percent(skills.skill_description(sid, rank=r) or "")
                if pct is not None:
                    per_rank[r] = f"{pct:g}%"
        if not per_rank:
            return None
    return {
        "id": sid,
        "name": (row.get("texts") or {}).get("name") or "Weapon Upgraded",
        "description": skills.skill_description(sid),
        "kind": kind,
        "attr": attr,
        "values": values,
        "per_rank": per_rank,
        "max_rank": max(per_rank),
    }


# An EFFECT ladder's sentence states the value the engine grants THIS piece,
# UNLABELLED — settled 2026-09-15 by comparing every candidate against the
# game's OWN text for the same weapon: resolving the sheet's sentence at the
# rank the piece is granted reproduces the game's sentence VERBATIM for all 12
# effect types (ai/workspace/buffy/compare_trait_vs_game.py prints 12/12).
# A `(Rare)` label inserted a word the game never prints in any of these
# sentences — the game's strings carry no rarity token at all — and the earlier
# `X%` placeholder hid a number the game always states. Rarity is conveyed by
# COLOUR, in the app exactly as in the game. The affix path is unaffected: its
# prose carries no number of its own.


def _sentence_value(text: str, val: str | None) -> list[tuple[str, str]]:
    """The effect sentence as [(text, rarity)] parts with the piece's own value
    left in place, exactly where the sheet put it — so the prose is the game's
    own sentence for this weapon.

    The value carries no `(Rarity)` label and no colour tag of its own: the
    game's sentence names the number and never a rarity (see the note above), so
    a label would both add a word the game does not print and paint the same
    rarity's value twice — the run below already names that value, in colour. A
    sentence that renders its value some other way is left as the sheet wrote
    it rather than guessed at."""
    idx = text.find(val) if val else -1
    if idx < 0:
        return [(text, "")]
    return [(text[:idx], ""), (val, ""), (text[idx + len(val):], "")]


@lru_cache(maxsize=1024)
def weapon_upgrade_trait(item_id: str) -> dict | None:
    """The weapon's "Weapon Upgraded" passive as a trait line for the item
    card's ⚡ block: {title, text, type, skill} — None when the piece has no
    upgrade passive (see weapon_upgrade_passive).

    This passive has no SkillBar tile: the engine grants it per upgrade rank
    instead of the item authoring it as one of its skills, so the line spells
    the ladder out once. It names ONE VALUE PER RARITY, each labelled with that
    rarity — the value being what the engine GRANTS that rarity (the ladder's
    rank to which that rarity maps, e.g. for an axe Rare +2% / Epic +3% /
    Legendary +4%). A single merged run ("…+1/+2/+3/+4/+5%") read as if a Rare
    piece had five ranks; one value per rarity says which number belongs to
    which rarity. Each rarity bit is tagged in `parts` so the card can colour
    it with the same colour its ladder column uses.

    An EFFECT ladder's prose states its number inline, and that number is a
    function of which rarity GRANTS the rank — so the prose states the value
    THIS piece is granted, UNLABELLED (the game's own sentence for the same
    weapon, verbatim), and the run that follows names every reachable rarity in
    order, its own first:

        … increased by 10% — Rare 10% · Epic 12% · Legendary 14%.

    Returns {title, text, parts, type, skill}, where `parts` is [(text,
    rarity)] whose texts concatenate to exactly `text` (rarity "" for plain
    prose, so a caller that ignores `parts` still renders the sentence).
    """
    pas = weapon_upgrade_passive(item_id)
    if not pas:
        return None
    from .. import skills
    from .stats import granted_rank, upgrade_ladder
    # one (rarity, granted rank, value) per upgradable column of the ladder
    cols: list[tuple[str, int, str]] = []
    for col in upgrade_ladder(item(item_id)) or []:
        rar = col.get("rarity") or ""
        rank = granted_rank(rar, pas["max_rank"])
        val = pas["per_rank"].get(rank) if rank else None
        if rar and val:
            cols.append((rar, rank, val))
    if not cols:
        # nothing can be bought at any of this piece's rarities: the engine
        # grants this passive BY upgrade rank, so a piece that can't be
        # upgraded (Common/Uncommon gear, rows with no scaling curve) never
        # receives it — stating the sheet's ladder here would be a phantom
        return None
    if pas["kind"] == "attribute":
        parts: list[tuple[str, str]] = [
            (f"{pas['name']} — {skills.stat_label(pas['attr'])}: ", "")]
        bits = cols
    else:
        # The sheet's own sentence, rendered at the rank this piece's rarity is
        # GRANTED (cols[0]), so any number left in the prose is this piece's
        # real value. Never the sentence at rank 1 (the ladder's base): that is
        # the form that put an unreachable number in front of the reader.
        # ...and if that rank has no text at all, fall back to the BASE
        # sentence (rank 0): cols[0][2] is the VALUE text ("10%"), which as a
        # rank argument would raise a TypeError rather than fall back
        own = (skills.skill_description(pas["id"], rank=cols[0][1])
               or skills.skill_description(pas["id"])
               or pas["name"]).rstrip(".")
        parts = [(f"{pas['name']} — ", "")]
        parts.extend(_sentence_value(own, cols[0][2]))
        bits = cols
        if bits:
            parts.append((" — ", ""))
    for i, (rar, _rank, val) in enumerate(bits):
        if i:
            parts.append((" · ", ""))
        parts.append((f"{rar} {val}", rar))
    parts.append((".", ""))
    return {
        "title": pas["name"],
        "text": "".join(t for t, _ in parts),
        "parts": parts,
        "type": "Passive",
        "skill": pas["id"],
    }



