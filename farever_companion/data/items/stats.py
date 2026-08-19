"""Gear stat math: computed stats, rarity tiers, upgrade ladders, stat search.

Port of the Item Lookup viewer's computeStats / gear_scaling (the game's
per-aptitude exponential curves x the item's atbRatio budget split, gated by
rarity/faction conditions) plus the upgrade ladder — per-rarity bases, what
each +1 adds, and the material costs — and the stat-name index that powers
the item page's stat search.
"""
from __future__ import annotations

import re
from functools import lru_cache

from .. import cdb
from .catalog import _data, items
from .craft import is_craftable
from .labels import category, is_gear, own_stats

# rarity -> which GearUpgrades material row. Legendary reuses the Epic crystal
# (there is no UpgradeLegendary item in the sheets) — this mapping is game
# logic, so it lives here rather than in the constant sheet.
_UPGRADE_MATERIAL_ROW = {
    "Uncommon": "UpgradeAll",
    "Rare": "UpgradeRare",
    "Epic": "UpgradeEpic",
    "Legendary": "UpgradeEpic",
}

# Live-game single-class armor multiplier (per class, fitted from in-game
# reports + MetaForge tooltips). Every SINGLE-class armor piece reads well
# above the sheet's curve x atbRatio math — ~2.25x (Fighter: Crimson Wings
# 100 vs 45), ~1.85x (Wizard/Cleric), ~1.80x (Assassin: Nightling Banner 64
# vs 36, in-game report) — while MULTI-class pieces match the sheet sum
# exactly (Ram Faceshield 225, Brie von de Cape 72). The game's runtime
# armor thus differs from its own itemType.json/aptitude.json for
# single-class gear; gear_stats applies the factor only when the item has
# ONE aptitude. Values are tunable if an in-game check disagrees.
_SINGLE_CLASS_ARMOR = {
    "Fighter": 2.25,
    "Assassin": 1.80,
    "Cleric": 1.85,
    "Wizard": 1.85,
}

# Per-item-TYPE stat budgets (itemType.json `atbRatio`, resolved through the
# inherit chain — every weapon type inherits OHWeapon's split). Generated-stats
# gear rolls these ratios into its stat values; the scan only stamped `atb` on
# a subset of items (the Rift Demon set etc.), but the budget is identical for
# every item of a type, so a missing stamp falls back to the type's budget.
# Authored gear (starter weapons — fixed affixes, no curves) is handled
# separately by _atb_budget and never uses this table.
_SLOT_ATB = {
    "Sword": {"primary": 0.28, "ratings": 0.175, "vitality": 0.26},
    "GreatSword": {"primary": 0.28, "ratings": 0.175, "vitality": 0.26},
    "DualSwords": {"primary": 0.28, "ratings": 0.175, "vitality": 0.26},
    "Axe": {"primary": 0.28, "ratings": 0.175, "vitality": 0.26},
    "GreatAxe": {"primary": 0.28, "ratings": 0.175, "vitality": 0.26},
    "DualAxes": {"primary": 0.28, "ratings": 0.175, "vitality": 0.26},
    "Mace": {"primary": 0.28, "ratings": 0.175, "vitality": 0.26},
    "GreatMace": {"primary": 0.28, "ratings": 0.175, "vitality": 0.26},
    "DualMaces": {"primary": 0.28, "ratings": 0.175, "vitality": 0.26},
    "Spear": {"primary": 0.28, "ratings": 0.175, "vitality": 0.26},
    "Daggers": {"primary": 0.28, "ratings": 0.175, "vitality": 0.26},
    "Fists": {"primary": 0.28, "ratings": 0.175, "vitality": 0.26},
    "Bow": {"primary": 0.28, "ratings": 0.175, "vitality": 0.26},
    "Staff": {"primary": 0.28, "ratings": 0.175, "vitality": 0.26},
    "Book": {"primary": 0.28, "ratings": 0.175, "vitality": 0.26},
    "Scepter": {"primary": 0.28, "ratings": 0.175, "vitality": 0.26},
    "Thrown": {"primary": 0.28, "ratings": 0.175, "vitality": 0.26},
    "Crescent": {"primary": 0.28, "ratings": 0.175, "vitality": 0.26},
    "Halos": {"primary": 0.28, "ratings": 0.175, "vitality": 0.26},
    "Chest": {"armor": 0.18, "primary": 0.11, "ratings": 0.075,
               "vitality": 0.09},
    "Head": {"armor": 0.14, "primary": 0.11, "ratings": 0.075,
              "vitality": 0.09},
    "Legs": {"armor": 0.16, "primary": 0.11, "ratings": 0.075,
              "vitality": 0.09},
    "Shoulders": {"armor": 0.13, "primary": 0.08, "ratings": 0.055,
                   "vitality": 0.07},
    "Hands": {"armor": 0.11, "primary": 0.08, "ratings": 0.055,
               "vitality": 0.065},
    "Feet": {"armor": 0.12, "primary": 0.08, "ratings": 0.055,
              "vitality": 0.07},
    "Waist": {"armor": 0.11, "primary": 0.08, "ratings": 0.055,
               "vitality": 0.065},
    "Back": {"armor": 0.05, "primary": 0.07,
              "ratings": 0.04, "vitality": 0.05},
    "Shield": {"armor": 0.337},
    "GearFinger": {"ratings": 0.1, "vitality": 0.05},
    "GearNeck": {"ratings": 0.14, "vitality": 0.05},
    "GearTrinket": {"ratings": 0.08},
}


def _atb_budget(item_row: dict) -> dict:
    """The item's atb ratio budget: its own stamped value, else its TYPE's
    budget (itemType.json — every generated-stats item of a type shares the
    same split). Authored gear (fixed affixes, no curves) has none — except
    Guild Merchant gear, which is SHOP gear: the town vendors sell the
    starter weapons scaled to each town (Rare at L20/L25), never as the
    fixed character-creation affixes, so it scales via its type's budget
    even when the handout row carries authored stats."""
    atb = item_row.get("atb") or {}
    if atb:
        pass
    elif _has_guild_merchant(item_row.get("id") or ""):
        atb = _SLOT_ATB.get(item_row.get("type") or "") or {}
    elif item_row.get("stats"):
        return {}                       # authored gear: fixed affixes
    else:
        atb = _SLOT_ATB.get(item_row.get("type") or "") or {}
    return atb


def _is_authored_affixes(item_row: dict) -> bool:
    """True for gear whose stats are FIXED authored affixes (no scaling
    curves): crafted pieces and non-vendor fixed-stat gear. Guild Merchant
    gear is excluded — the town vendors sell the starter weapons scaled to
    each town (Rare at L20/L25), so they scale like shop gear even though
    the handout row carries fixed starter affixes."""
    return bool(item_row.get("stats")) \
        and not _has_guild_merchant(item_row.get("id") or "")


def _has_guild_merchant(item_id: str) -> bool:
    """True when the item is sold by a Guild Merchant (the town shops). The
    town weapons carry the `_Craft` token but are shop gear sold scaled to
    each town — item_fixed_level must not pin them."""
    from .sources import has_guild_merchant
    return has_guild_merchant(item_id)


def item_fixed_level(item_row: dict) -> int | None:
    """The item's FIXED level when it's crafted gear — the level the piece
    is made at in-game and never changes, so the item page shows its stats
    there with NO level scaling (no slider). None for dropped / generated /
    shop-sold gear, whose stats roll from the source's level.

    Signal: the sheet's `level` stamp (level > 1) on gear the game crafts —
    the `_Craft` / `RCraft` id token (the crafted dungeon / Rift-set pieces
    like the Rift Demon set's crafted variants, made at their sheet level)
    or a producing recipe (Aura of the Honeycomb, the Alchemist stones —
    any recipe-bearing GEAR is crafted). The town weapons (Credence,
    Glory, Radiance, Judgement, ...) are `_Craft` ids too but are SHOP
    gear — the Guild Merchants sell them scaled to each town (Tyrna L20 /
    Lower Ramburg L25), so a Guild Merchant source overrides the token and
    they stay scalable. Recipes only mark GEAR as crafted: consumables and
    materials are craftable too but still drop normally. Drop templates
    (start gear, the Rift Demon set) are stamped level 1 and stay
    scalable.

    The zone gear (Reinforced Hauberk of the Exile, Blessed Galligaskins
    ...) is deliberately NOT crafted: those pieces carry the sheet's
    `WorldLoot` flag — the game generates them from the WorldLoot token
    (zone mobs / crates), so they are Uncommon mob drops with an authored
    zone-tier level, not recipes. (An old rule treating `iLevel == 10 x
    level` as a craft signal caught them — that identity just means
    "Uncommon gear at base iLevel" and was dropped.)
    """
    lvl = item_row.get("level")
    if not isinstance(lvl, int) or lvl <= 1:
        return None
    iid = item_row.get("id") or ""
    crafted = (("_Craft" in iid or "RCraft" in iid)
               or (is_craftable(iid) and is_gear(item_row)))
    if crafted and not _has_guild_merchant(iid):
        return lvl
    return None


def _table_level(item_row: dict, level: int | None) -> int:
    """The level the stat tables (upgrade ladder, rarity tiers) use when the
    caller doesn't pass one: the item's FIXED craft level for crafted gear
    (its stats never scale), else the item's real source max (a mob/boss
    drop level — Judgement from Munster Chuck L19), else the bundled max
    level. Keeps the BY STEP and BY RARITY tables agreeing everywhere."""
    if level is not None:
        return level
    fixed = item_fixed_level(item_row)
    if fixed:
        return fixed
    # the item's scale top — Guild Merchant gear opens at the vendor's top
    # sale (L25), not the authored level or a lower boss drop
    from .sources import item_scale_max_level
    return item_scale_max_level(item_row.get("id") or "")


@lru_cache(maxsize=1)
def _gear_upgrades() -> dict[str, dict]:
    """Gear-upgrade material rows from the bundled constant sheet.

    constant.json `GearUpgrades.Materials` lists per-material
    `{item, levelExponent, base: [{v}, ...]}` — the per-rank counts are
    `cost = base[rank] x level ^ levelExponent`, rounded to 2dp (matches the
    reference gear_scaling.py). Returns {} if the sheet isn't bundled.
    """
    rows: dict[str, dict] = {}
    for r in cdb.lines("constant"):
        if r.get("id") != "GearUpgrades":
            continue
        for g in (r.get("v") or {}).get("group", []):
            if g.get("id") != "Materials":
                continue
            for row in (g.get("v") or {}).get("other", {}).get("gearUpgrades", []):
                rows[row["item"]] = {
                    "exp": row.get("levelExponent", 0.0),
                    "base": tuple(x.get("v", 0) for x in row.get("base", [])),
                }
    return rows


@lru_cache(maxsize=1)
def _upgrade_material_names() -> dict[str, str]:
    """Upgrade material item id -> display name, from the bundled items sheet."""
    return {r.get("id"): r.get("name") for r in cdb.lines("items") if r.get("id")}


def gear_scaling() -> dict:
    """The `scaling` block: max_level, rarity_ilevel, rarity_upgrades,
    flawless_ilevel, upgrade_ilevel, aptitudes."""
    return _data().get("scaling", {})


# Shorthand stat names the search box accepts ('mpen' for Magic Penetration
# etc.) — the same short forms the upgrade chips use.
_STAT_ALIASES = {
    "Magic Penetration": ("mpen",),
    "Armor Penetration": ("arpen",),
    "Critical": ("crit",),
    "Health": ("hp",),
}


# Combat-rating stats — the four ratings gear rolls from its aptitude
# curves (gated by faction; see scaling.aptitudes). The filter row on the
# item page lists these four.
RATING_STATS = ("Critical", "Fervor", "Magic Penetration",
                "Armor Penetration")
_RATING_LABELS = {
    "Armor Penetration": "ArPen",
    "Critical": "Crit",
    "Magic Penetration": "MaPen",
    "Fervor": "Fervor",
}


@lru_cache(maxsize=None)
def gear_ratings(item_id: str) -> tuple[str, ...]:
    """The combat ratings a gear item can roll — ArPen / Crit / MaPen /
    Fervor — from its aptitude curves at Rare+ (the rating curves are gated
    `minRarity: Rare`, so the item's own rarity doesn't matter). Per-aptitude
    `splits` count too: multi-class gear shows each aptitude's rating as its
    own line (Ram Faceshield rolls Critical AND Fervor), so a split label
    missing from the aggregate row is still a rollable rating. Zone gear
    with no faction has no rating curves and rolls none. Returns () for
    non-gear / authored gear without an atb ratio."""
    it = _data().get("items", {}).get(item_id) or {}
    if not is_gear(it) or not _atb_budget(it):
        return ()
    labels: set[str] = set()
    for rar in ("Rare", "Epic", "Legendary"):
        gs = gear_stats(it, level=gear_scaling().get("max_level") or 25,
                        rarity=rar)
        if gs:
            for s in gs["stats"]:
                if s.get("label"):
                    labels.add(s["label"])
                for sp in s.get("splits") or []:
                    if sp.get("label"):
                        labels.add(sp["label"])
    return tuple(r for r in RATING_STATS if r in labels)


def rating_short(label: str) -> str:
    """Compact chip label for a combat rating ('Armor Penetration' ->
    'ArPen')."""
    return _RATING_LABELS.get(label, label)


@lru_cache(maxsize=None)
def _stat_labels(item_id: str) -> tuple[str, ...]:
    """Distinct stat names an item shows or rolls — computed gear stats
    (with per-class splits) plus authored affixes, deduplicated."""
    it = _data().get("items", {}).get(item_id) or {}
    labels: list[str] = [s.get("n") for s in (it.get("stats") or []) if s.get("n")]
    gs = gear_stats(it)
    if gs:
        for s in gs["stats"]:
            if s.get("label"):
                labels.append(s["label"])
            for sp in s.get("splits") or []:
                if sp.get("label"):
                    labels.append(sp["label"])
    seen: set[str] = set()
    out = [l for l in labels if not (l in seen or seen.add(l))]
    return tuple(out)


def _label_tokens(lbl: str) -> tuple[str, ...]:
    """All search forms of one stat label: the full name, its de-spaced
    compact form ('magicpenetration') and any shorthand alias ('mpen')."""
    t = re.sub(r"[^a-z0-9]", "", lbl.lower())
    toks = [lbl.lower()]
    if t:
        toks.append(t)
    toks.extend(_STAT_ALIASES.get(lbl, ()))
    return tuple(toks)


@lru_cache(maxsize=None)
def matched_stat_labels(item_id: str, q: str) -> tuple[str, ...]:
    """Human stat names the query matches on this item — 'dex' returns
    ('Dexterity',), 'pen' returns both penetration labels. Drives the
    per-row matched-stat tag in the search results."""
    q = (q or "").strip().lower()
    if not q:
        return ()
    return tuple(lbl for lbl in _stat_labels(item_id)
                 if any(q in t for t in _label_tokens(lbl)))


def matches_stat(item_id: str, q: str) -> bool:
    """True when the query matches one of the item's stat names — 'dex'
    matches Dexterity, 'mpen' matches Magic Penetration, 'armor' matches
    Armor. Used by the item page's search box."""
    return bool(matched_stat_labels(item_id, q))


def ilevel_tiers(item_row: dict, level: int | None = None) -> list[dict] | None:
    """Gear iLevel tiers for a gear item, from the scaling block.

    `final iLevel = 10 x level + rarity bonus + 10 x upgrades`, capped per
    rarity (see GEAR_SCALING_README.md). Defaults to the bundled max level
    (25). Returns None for non-gear items. Each tier:
    {rarity, base, max, upgrades}.
    """
    if not is_gear(item_row):
        return None
    sc = gear_scaling()
    lvl = level if level is not None else (sc.get("max_level") or 25)
    step = sc.get("upgrade_ilevel", 10)
    base = 10 * lvl
    bonuses = sc.get("rarity_ilevel", {})
    caps = sc.get("rarity_upgrades", {})
    out = []
    for rar in ("Uncommon", "Rare", "Epic", "Legendary"):
        bonus = bonuses.get(rar, 0)
        ups = caps.get(rar, 0)
        out.append({"rarity": rar, "base": base + bonus,
                    "max": base + bonus + step * ups, "upgrades": ups})
    return out


def upgrade_material(rarity: str) -> str | None:
    """The gear-upgrade material item for a rarity (Spark Dust / Shard /
    Crystal), or None for rarities with no upgrades."""
    row_id = _UPGRADE_MATERIAL_ROW.get(rarity or "", "")
    if row_id not in _gear_upgrades():
        return None
    return _upgrade_material_names().get(row_id)


def upgrade_costs(item_row: dict, level: int | None = None) -> list[dict] | None:
    """Per-rank material counts for the item's OWN rarity at `level` (default
    the bundled max level, 25). Each entry: {upgrade, count, material}.
    ``count = base[rank] x level ^ levelExponent`` (rounded to 2dp)."""
    if not is_gear(item_row):
        return None
    rar = item_row.get("rarity") or ""
    key = _UPGRADE_MATERIAL_ROW.get(rar)
    row = _gear_upgrades().get(key or "")
    if not row:
        return None
    sc = gear_scaling()
    lvl = level if level is not None else (sc.get("max_level") or 25)
    cap = sc.get("rarity_upgrades", {}).get(rar, 0)
    base, exp = row["base"], row["exp"]
    material = _upgrade_material_names().get(key)
    return [{"upgrade": i + 1, "count": round(base[i] * (lvl ** exp), 2),
             "material": material} for i in range(min(cap, len(base)))]


def upgrade_path(item_row: dict, level: int | None = None,
                 rarity: str | None = None) -> list[int] | None:
    """iLevel at every upgrade step (+0 .. +cap) for the item's OWN rarity.

    Each upgrade adds `upgrade_ilevel` (10) to the base + rarity bonus at
    `level` (default the bundled max level); the cap is that rarity's
    `rarity_upgrades`. `rarity` overrides the authored rarity — the items
    page passes the item's DISPLAY rarity, so Guild Merchant gear (the
    starter weapons, sold as Rare by the town vendors) sorts and badges at
    its shop quality. None for non-gear.
    """
    if not is_gear(item_row):
        return None
    sc = gear_scaling()
    lvl = level if level is not None else (sc.get("max_level") or 25)
    step = sc.get("upgrade_ilevel", 10)
    rar = rarity if rarity is not None else (item_row.get("rarity") or "")
    bonus = sc.get("rarity_ilevel", {}).get(rar, 0)
    cap = sc.get("rarity_upgrades", {}).get(rar, 0)
    base = 10 * lvl + bonus
    return [base + step * i for i in range(cap + 1)]


def upgrade_gains(item_row: dict, level: int | None = None) -> list[dict] | None:
    """Per-rank stat gain from gear upgrades — what each +1 actually adds.

    Each upgrade adds `upgrade_ilevel` (10) to the item's iLevel, which moves
    the stat values along the aptitude curves, so the stats at rank i equal
    gear_stats(level + i). Returns [{upgrade, gains: [{label, value}]}] for
    the item's OWN rarity at `level` (default the bundled max level), or None
    for non-gear / authored gear without an atb ratio / rarities with no
    upgrade cap.
    """
    if not is_gear(item_row):
        return None
    sc = gear_scaling()
    lvl = level if level is not None else (sc.get("max_level") or 25)
    cap = sc.get("rarity_upgrades", {}).get(item_row.get("rarity") or "", 0)
    if cap <= 0:
        return None

    def values(l: int) -> dict | None:
        gs = gear_stats(item_row, level=l)
        if not gs:
            return None
        return {s["key"]: s["value"] for s in gs["stats"] if s["value"]}

    prev = values(lvl)
    if prev is None:
        return None
    base = gear_stats(item_row, level=lvl)
    labels = {s["key"]: s["label"] for s in base["stats"]}
    out = []
    for i in range(1, cap + 1):
        cur = values(lvl + i)
        if cur is None:
            break
        gains = [{"label": labels.get(k, k), "value": cur[k] - prev[k]}
                 for k in prev if cur.get(k, 0) > prev[k]]
        out.append({"upgrade": i, "gains": gains})
        prev = cur
    return out


def class_primary_stat(cls: str) -> str | None:
    """The primary stat of a class — the endAtb of its aptitude group-0
    curve: Strength (Fighter), Dexterity (Assassin), Intellect (Wizard),
    Faith (Cleric). None for aptitudes without a primary curve (Crit,
    ArPen, MaPen, Fervor, Vita)."""
    g0 = (((gear_scaling().get("aptitudes") or {}).get(cls) or {})
          .get("groups") or {}).get("0") or []
    for c in g0:
        if c.get("endAtb"):
            return c["endAtb"]
    return None


def _carried_rarity_stats(item_row: dict,
                          order: tuple[str, ...]) -> dict[str, list]:
    """Per-rarity stat values the item's compiled row carries from the
    game — the `rarity_stats` map ({rarity: [stat rows]}) the compiler
    stamps when the sheet ships gear stats at each rarity. The current
    game data has every item as a single-rarity template, so this is empty
    for every real item and callers fall back to the computed curves; a
    later game update that adds per-rarity stats overrides the computed
    columns automatically — no category hard-coding."""
    rs = item_row.get("rarity_stats")
    if not isinstance(rs, dict) or not rs:
        return {}
    own = (item_row.get("rarity") or "").title()
    if own not in order:
        own = order[0]
    return {r: rs[r] for r in order[order.index(own):] if rs.get(r)}


def _stat_rows_from(raw) -> list[dict]:
    """Normalize carried per-rarity stat rows to {key, label, value} —
    accepts the game's authored shape ({n, v}) and the computed shape
    ({key, label, value}), skipping rows without a usable value."""
    rows = []
    for s in raw or []:
        if not isinstance(s, dict):
            continue
        key = s.get("key") or s.get("n") or s.get("k")
        label = s.get("label") or s.get("n")
        value = s.get("value") or s.get("v")
        if key is None or label is None or not value:
            continue
        rows.append({"key": key, "label": label, "value": value})
    return rows


# Gear-stat display mode for stat_text — which form the value lines show:
# "rounding" (default — the clean game-tooltip integer), "true" (the exact
# computed float, 3 decimals) or "both" ('4 (3.985)'). Toggled from the
# Items search header; it's a session-level UI preference, so it lives here
# where both display surfaces (farm row, upgrade matrix) can read it without
# threading state through every render call.
_STAT_DISPLAY_MODE = "rounding"


def stat_display_mode() -> str:
    """The active gear-stat display mode: 'rounding' | 'true' | 'both'."""
    return _STAT_DISPLAY_MODE


def set_stat_display_mode(mode: str) -> None:
    """Set the gear-stat display mode — 'rounding' (game-tooltip integers,
    the default), 'true' (exact computed floats, 3 decimals) or 'both'
    ('4 (3.985)'). Unknown values are ignored."""
    global _STAT_DISPLAY_MODE
    if mode in ("rounding", "true", "both"):
        _STAT_DISPLAY_MODE = mode


def stat_text(value, raw=None) -> str:
    """The display form of a stat value per stat_display_mode(): the rounded
    integer the game's tooltip shows ('rounding', the default), the TRUE
    computed float to 3 decimals with trailing zeros stripped ('true'), or
    both — '4 (3.985)' ('both'). Stats without a computed raw
    (authored/shipped values, exact by definition) always render plainly.
    """
    if raw is None:
        return f"{value:g}"
    mode = _STAT_DISPLAY_MODE
    if mode == "rounding":
        return f"{value:g}"
    r = f"{raw:.3f}".rstrip("0").rstrip(".")
    if mode == "true":
        return r
    if abs(raw - value) < 0.0005:
        return f"{value:g}"
    return f"{value:g} ({r})"


def stat_rows(gs_stats) -> list[dict]:
    """The stat lines an item displays, in game order: computed gear_stats
    rows expanded so a stat carrying per-aptitude `splits` (Strength +
    Dexterity, Critical + Fervor, ...) becomes one row per split instead of
    a single aggregate row labeled with the first split — the game shows
    each aptitude's contribution as its own line (Ram Faceshield reads
    Critical + Fervor separately, not one aggregated Critical). Rows
    without splits pass through unchanged; zero/blank rows are dropped."""
    rows = []
    for s in gs_stats or []:
        if s.get("splits"):
            for sp in s["splits"]:
                if sp.get("label") and sp["value"]:
                    rows.append({"label": sp["label"], "value": sp["value"],
                                 "raw": sp.get("raw")})
        elif s.get("label") and s["value"]:
            rows.append({"label": s["label"], "value": s["value"],
                         "raw": s.get("raw")})
    return rows


def upgrade_ladder(item_row: dict, level: int | None = None) -> list[dict] | None:
    """Per-rarity upgrade ladders for a gear item at `level` (default the
    item's scale top — its fixed craft level for crafted gear, else the
    item's max scale level): every rarity from the item's OWN (display)
    rarity up, each as
    {rarity, upgrades (max rank), material, base: [{label, value}], steps:
    [{upgrade, gains: [{label, value}], count}]}. The own-rarity column is
    the item's DISPLAY rarity, so Guild Merchant gear (the starter weapons,
    sold as Rare by the town vendors) shows its shop quality column.

    `base` is the stat values at that rarity; each step's `gains` is what
    that +N adds (each upgrade = +10 iLevel, the gear-stats delta) and
    `count` the material cost for the rank. Authored gear (no atb ratio) has
    fixed affixes instead of curves — one column with its own rarity, its
    own stats as base, and material-only steps. Weapons expand to every
    rarity from their own up; armor and jewelry carry no per-rarity stats
    in-game yet (a later update may add them), so they show their own
    rarity's single column — WITH the same per-rank gains and costs as
    weapons (each upgrade is +10 iLevel for every gear category). Until
    shipped `rarity_stats` data arrives, which overrides the columns for
    all three automatically. None for non-gear.
    """
    if not is_gear(item_row):
        return None
    sc = gear_scaling()
    lvl = _table_level(item_row, level)
    # the ladder's own-rarity column is the item's DISPLAY rarity, not the
    # authored one: Guild Merchant gear (the starter weapons, sold as Rare
    # by the town vendors) shows its shop quality column, so the matrix
    # agrees with the tiles/header/badge instead of a green Uncommon
    # starter column. Non-vendor gear is unaffected (display = authored).
    from .sources import item_display_rarity
    own = (item_display_rarity(item_row.get("id") or "")
           or item_row.get("rarity") or "").title()
    order = ("Uncommon", "Rare", "Epic", "Legendary")
    if own not in order:
        own = order[0]
    caps = sc.get("rarity_upgrades", {})
    if not _atb_budget(item_row) or _is_authored_affixes(item_row):
        # authored gear (crafted / non-vendor fixed-stat items): fixed
        # affixes, no scaling curves — one column with the item's own stats
        # as base and material-only steps (the affixes never move with
        # level). Guild Merchant gear is NOT authored: the vendor sells it
        # scaled to each town, so it takes the curve path below
        cap = caps.get(own, 0)
        row_id = _UPGRADE_MATERIAL_ROW.get(own, "")
        row = _gear_upgrades().get(row_id)
        material = _upgrade_material_names().get(row_id)
        steps = []
        if row:
            bc, exp = row["base"], row["exp"]
            for i in range(1, cap + 1):
                steps.append({"upgrade": i, "gains": [],
                              "count": round(bc[min(i - 1, len(bc) - 1)]
                                              * (lvl ** exp), 2)})
        base = [{"label": s.get("n") or "?", "value": s.get("v", 0)}
                for s in (item_row.get("stats") or []) if s.get("n") and s.get("v") != 0]
        if not base:
            return None
        return [{"rarity": own, "upgrades": cap, "material": material,
                 "base": base, "steps": steps}]

    # Rarity columns: weapons carry per-rarity stats in-game, so they
    # expand from the item's own rarity up. Armor and jewelry do NOT use
    # per-rarity stats yet (a later update may add them) — their expansion
    # is commented out and they show the single base column. When the game
    # ships per-rarity data (`rarity_stats`), it overrides every column
    # automatically for all three.
    carried = _carried_rarity_stats(item_row, order)
    is_weapon = (category(item_row.get("type")) == "Weapons")
    if carried:
        rarities = order[order.index(own):]
    elif is_weapon:
        rarities = order[order.index(own):]
    else:
        # armor + jewelry: per-rarity stats not in the game yet — single base column only
        rarities = (own,)

    out = []
    for rar in rarities:
        cap = caps.get(rar, 0)
        gs = gear_stats(item_row, level=lvl, rarity=rar)
        if not gs or not any(s.get("value") for s in gs["stats"]):
            continue
        def values(l: int) -> dict:
            g = gear_stats(item_row, level=l, rarity=rar)
            return {s["label"]: (s["value"], s.get("raw", float(s["value"])))
                    for s in stat_rows((g or {}).get("stats", []))}

        # every gear category uses the same upgrade system: each +1 = +10
        # iLevel, the gear-stats delta (armor and jewelry keep their single
        # own-rarity column but still show the per-rank gains + costs)
        steps = []
        prev = values(lvl)
        for i in range(1, cap + 1):
            cur = values(lvl + i)
            if not cur:
                break
            # gains carry the TRUE float delta too, so displays can show the
            # exact per-step value (the rounded delta stays the headline)
            gains = [{"label": k, "value": cur[k][0] - prev[k][0],
                      "raw": cur[k][1] - prev[k][1]}
                     for k in prev
                     if cur.get(k) and cur[k][0] > prev[k][0]]
            gains.sort(key=lambda x: -x["value"])
            steps.append({"upgrade": i, "gains": gains})
            prev = cur
        row_id = _UPGRADE_MATERIAL_ROW.get(rar, "")
        row = _gear_upgrades().get(row_id)
        material = _upgrade_material_names().get(row_id)
        if row:
            bc, exp = row["base"], row["exp"]
            for s in steps:
                s["count"] = round(bc[min(s["upgrade"] - 1, len(bc) - 1)]
                                    * (lvl ** exp), 2)
        raw_base = carried.get(rar)
        if raw_base:
            base = [{"label": r["label"], "value": r["value"]}
                    for r in _stat_rows_from(raw_base)]
        elif _is_authored_affixes(item_row):
            # genuinely fixed-affix gear (crafted / non-vendor): the authored
            # stats are the base at every level. Guild Merchant gear falls
            # through — the vendor sells it scaled, so the computed base wins
            base = [{"label": s.get("n") or "?", "value": s.get("v", 0)}
                    for s in item_row["stats"] if s.get("n") and s.get("v")]
        else:
            base = stat_rows(gs["stats"])
        out.append({"rarity": rar, "upgrades": cap, "material": material,
                    "base": base, "steps": steps})
    return out or None


@lru_cache(maxsize=1)
def enchant_conversions() -> list[dict]:
    """The demon-gear augment enchants (DemonGearUpgrade* items): every stat
    conversion, each with its Rare and Epic versions.

    Each augment carries two authored stats — the stat it ADDS (+20 Rare /
    +40 Epic, `target`) and the one it replaces (−20 / −40, `source`), so
    the item page can list, per stat on a piece, the conversions that trade
    it for another rating. Returns [{target, source, rare: {value, item},
    epic: {value, item}}] sorted by target then source. `value` is the +N
    (always 20 for Rare, 40 for Epic in the current scan)."""
    out: dict[tuple[str, str], dict] = {}
    for it in items():
        iid = it.get("id") or ""
        if not iid.startswith("DemonGearUpgrade"):
            continue
        own = own_stats(it) or []
        pos = next((s for s in own if s["v"] > 0), None)
        neg = next((s for s in own if s["v"] < 0), None)
        if not pos or not neg:
            continue
        row = out.setdefault((pos["n"], neg["n"]),
                             {"target": pos["n"], "source": neg["n"]})
        row["epic" if it.get("rarity") == "Epic" else "rare"] = {
            "value": pos["v"], "item": iid}
    return sorted(out.values(), key=lambda r: (r["target"], r["source"]))


@lru_cache(maxsize=1)
def enchant_scrolls() -> dict[str, int]:
    """The +2 enchant scrolls: stat -> the flat value it adds (ScrollOf*
    items, parsed from their authored stats). Returns {'Dexterity': 2,
    'Faith': 2, 'Intellect': 2, 'Strength': 2, 'Vitality': 2}. The
    corrupted scrolls grant +4 with a Vitality penalty and live in
    corrupted_scrolls()."""
    out: dict[str, int] = {}
    for it in items():
        iid = it.get("id") or ""
        if not iid.startswith("ScrollOf"):
            continue
        own = own_stats(it) or []
        pos = next((s for s in own if s["v"] > 0), None)
        if pos:
            out[pos["n"]] = pos["v"]
    return out


# The corrupted enchant scrolls (ScrollOfCorrupted*): like the gems, the
# bundled item scan records them with no stats, so their values come from
# the fareverdb item pages (e.g. fareverdb.com/items/ScrollOfCorruptedDexterity):
# each enchants with +4 to one stat but drains 4 Vitality (Enchanter, Lv 6).
# All four trade the same penalty — the shape is the + stat and its price.
_CORRUPTED_SCROLLS = (
    {"item": "ScrollOfCorruptedDexterity", "stat": "Dexterity",
     "value": 4, "penalty": "Vitality"},
    {"item": "ScrollOfCorruptedFaith", "stat": "Faith",
     "value": 4, "penalty": "Vitality"},
    {"item": "ScrollOfCorruptedIntellect", "stat": "Intellect",
     "value": 4, "penalty": "Vitality"},
    {"item": "ScrollOfCorruptedStrength", "stat": "Strength",
     "value": 4, "penalty": "Vitality"},
)


def corrupted_scrolls() -> tuple[dict, ...]:
    """The corrupted enchant scrolls (ScrollOfCorrupted*): each
    {item, stat, value, penalty} — enchants with +value of `stat` while
    draining `value` Vitality. The plain +2 scrolls live in
    enchant_scrolls()."""
    return _CORRUPTED_SCROLLS


# Gem augment values (Jeweller-crafted): the bundled item scan records the
# Cut Beryl/Ruby/Agate/Amber tiers as items with NO stats, so their values
# come from MetaForge's Farever augment database
# (metaforge.app/farever/database/augments) — the same reference the gear
# math below is validated against. Z1 stones (Beryl/Amber, Jeweller lv 2)
# grant +4 stat pairs (+2 Vitality for Strong); Z2 stones (Ruby/Agate,
# lv 4) grant +7 pairs (+4 Vitality). Runed Cut Beryl/Ruby exist as items
# but have no documented values yet, so they are omitted.
_GEM_AUGMENTS = (
    {"item": "AttunedCutBeryl", "level": 2, "zone": "Z1",
     "stats": {"Magic Penetration": 4, "Fervor": 4}},
    {"item": "GildedCutBeryl", "level": 2, "zone": "Z1",
     "stats": {"Armor Penetration": 4, "Magic Penetration": 4}},
    {"item": "TemperedCutBeryl", "level": 2, "zone": "Z1",
     "stats": {"Armor Penetration": 4, "Fervor": 4}},
    {"item": "SurgingCutAmber", "level": 2, "zone": "Z1",
     "stats": {"Critical": 4, "Magic Penetration": 4}},
    {"item": "ResonantCutAmber", "level": 2, "zone": "Z1",
     "stats": {"Critical": 4, "Fervor": 4}},
    {"item": "SunderedCutAmber", "level": 2, "zone": "Z1",
     "stats": {"Critical": 4, "Armor Penetration": 4}},
    {"item": "StrongCutAmber", "level": 2, "zone": "Z1",
     "stats": {"Vitality": 2}},
    {"item": "AttunedCutRuby", "level": 4, "zone": "Z2",
     "stats": {"Magic Penetration": 7, "Fervor": 7}},
    {"item": "GildedCutRuby", "level": 4, "zone": "Z2",
     "stats": {"Armor Penetration": 7, "Magic Penetration": 7}},
    {"item": "TemperedCutRuby", "level": 4, "zone": "Z2",
     "stats": {"Armor Penetration": 7, "Fervor": 7}},
    {"item": "SurgingCutAgate", "level": 4, "zone": "Z2",
     "stats": {"Critical": 7, "Magic Penetration": 7}},
    {"item": "ResonantCutAgate", "level": 4, "zone": "Z2",
     "stats": {"Critical": 7, "Fervor": 7}},
    {"item": "SunderedCutAgate", "level": 4, "zone": "Z2",
     "stats": {"Critical": 7, "Armor Penetration": 7}},
    {"item": "StrongCutAgate", "level": 4, "zone": "Z2",
     "stats": {"Vitality": 4}},
    # the Cursed Eyes (Jeweller Lv 6): three +9 ratings and one −9 penalty
    # each, values from the fareverdb item pages. They're not zoned like
    # the Z1/Z2 stones, so their group header reads "Cursed · Jeweller Lv 6".
    {"item": "BrutalityCursedCutEye", "level": 6, "zone": "Cursed",
     "stats": {"Critical": 9, "Fervor": 9, "Armor Penetration": 9,
                "Magic Penetration": -9}},
    {"item": "DominationCursedCutEye", "level": 6, "zone": "Cursed",
     "stats": {"Critical": 9, "Armor Penetration": 9,
                "Magic Penetration": 9, "Fervor": -9}},
    {"item": "FanatismCursedCutEye", "level": 6, "zone": "Cursed",
     "stats": {"Fervor": 9, "Armor Penetration": 9,
                "Magic Penetration": 9, "Critical": -9}},
    {"item": "ViceCursedCutEye", "level": 6, "zone": "Cursed",
     "stats": {"Critical": 9, "Fervor": 9, "Magic Penetration": 9,
                "Armor Penetration": -9}},
)


def gem_augments() -> tuple[dict, ...]:
    """The Jeweller gem augments (Cut Beryl / Ruby / Agate / Amber tiers
    plus the Cursed Eyes): each {item, level (Jeweller station), zone
    (Z1/Z2/Cursed), stats: {stat: value}}. Z2 grants the highest plain
    values (+7 vs +4); the Lv 6 Cursed Eyes grant three +9 ratings and one
    −9 penalty each, so negative values mean a drained stat. Values are
    from MetaForge's augment database and fareverdb — the bundled scan
    records these gems without any stats."""
    return _GEM_AUGMENTS


def item_level(level: int, rarity: str = "", upgrades: int = 0) -> int:
    """iLevel = 10 x level + rarity bonus + 10 x upgrades (capped per rarity).
    Port of the Item Lookup viewer's itemLevel / gear_scaling.py."""
    sc = gear_scaling()
    base = 10 * level
    bonus = sc.get("rarity_ilevel", {}).get(rarity, 0)
    cap = sc.get("rarity_upgrades", {}).get(rarity, 0)
    step = sc.get("upgrade_ilevel", 10)
    return base + bonus + step * min(max(upgrades, 0), cap)


# --- computed gear stats (port of the Item Lookup viewer's computeStats) -----
# The viewer implements the game's gear math — per-aptitude exponential curves
# (aptitude.json atbScaling, emitted as scaling.aptitudes) x the item type's
# atbRatio budget split, gated by rarity/faction conditions, times the
# powerRatio ramp (constant.json) — validated against fareverdb / questlog /
# metaforge. This is the same math, in Python, so the item page can show REAL
# stat values at any level.
_STAT_GROUPS = ("primary", "armor", "ratings", "vitality")
_STAT_LABELS = {
    "ArmorPenetrationRating": "Armor Penetration",
    "SpellPenetrationRating": "Magic Penetration",
    "CritChanceRating": "Critical",
    "FervorRating": "Fervor",
    "MaxHealth": "Health",
}
_GROUP_LABELS = {"primary": "Primary Stat", "ratings": "Combat Ratings",
                  "armor": "Armor", "vitality": "Health"}
_RANK = {"common": 0, "uncommon": 1, "rare": 2, "epic": 3, "legendary": 4}
_MODE = {"primary": "0", "vitality": "1", "armor": "2", "ratings": "3"}


def _cond_ok(conds: dict | None, ctx: dict) -> bool:
    if not conds:
        return True
    mr = conds.get("minRarity")
    if isinstance(mr, str):
        r = (ctx.get("rarity") or "").lower()
        if r in _RANK and _RANK[r] < _RANK.get(mr.lower(), 0):
            return False
    facs = conds.get("factions")
    if isinstance(facs, list) and facs:
        if not ctx.get("faction"):
            return False
        f = ctx["faction"]
        if not any((x if isinstance(x, str) else (x or {}).get("ref")) == f for x in facs):
            return False
    return True


def _curve(start: float, end: float, x: float, max_l: float) -> float:
    """Exponential ramp start * (end/start)^((x-1)/(max-1)), x clamped to [1, max]."""
    if start <= 0:
        return 0.0
    if end == start or max_l <= 1:
        return start
    xc = min(max(1.0, x), max_l)
    return start * (end / start) ** ((xc - 1) / (max_l - 1))


def _apt_curve(apt: str, group: str, scaling: dict, ctx: dict):
    g = ((scaling.get("aptitudes") or {}).get(apt) or {}).get("groups") or {}
    rows = g.get(group) or []
    for c in rows:
        if _cond_ok(c.get("conds"), ctx):
            return c
    return None


def _any_curve(apts: list, group: str, scaling: dict, ctx: dict):
    for apt in apts:
        c = _apt_curve(apt, group, scaling, ctx)
        if c:
            return c
    return None


def gear_stats(item_row: dict, level: int | None = None,
               rarity: str | None = None, variance: float = 0.3):
    """Computed stat values for a gear item at `level` (default the bundled max
    level) for `rarity` (default the item's own rarity).

    Port of the Item Lookup viewer's computeStats: uses the aptitude curves +
    the item's atbRatio budget split. Returns {"ilevel", "stats": [{key,
    label, value, min, max, splits?}]} or None for non-gear / items without an
    atb ratio (authored-stats gear like the Rift Demon set rolls these).
    """
    if not is_gear(item_row):
        return None
    atb = _atb_budget(item_row)
    if not atb:
        return None
    sc = gear_scaling()
    lvl = level if level is not None else (sc.get("max_level") or 25)
    rar = rarity or item_row.get("rarity") or "Common"
    ctx = {"faction": item_row.get("faction") or None, "rarity": rar}

    y = max(0.0, variance)
    # the game rounds stat values with math_round (not floor/truncate) —
    # confirmed in $HItem.generateItemAffixes; w_ applies it to the value
    # and its ±variance roll range, and keeps the TRUE float (`raw`) so
    # displays can show the exact computed number (3.717) next to the
    # rounded integer the game's tooltip shows (4)
    w_ = lambda v: (round(v), round(v * (1 - y)), round(v * (1 + y)), v)
    bonus = sc.get("rarity_ilevel", {}).get(rar, 0)
    m = lvl + bonus / 10.0                        # baseILevel incl. rarity
    l1, l50 = sc.get("bounds", [0.5, 0.9])[:2]
    em = sc.get("early_max_level") or 50.0
    # power ratio: the game scales the stat budget along the SAME exponential
    # curve it uses everywhere else (getAtbLevelScaling: start * (end/start)^
    # ((x-1)/(max-1))) — the reference viewer's linear ramp was an inference
    # from the bounds description and drifts ~5% high by max level (Brie's
    # Intellect and Nightling's Dexterity both read one lower in-game)
    c = l1 if em <= 1 else l1 * (l50 / l1) ** ((min(max(1.0, m), em) - 1) / (em - 1))
    p = em
    # The aptitude set that rolls the item's stats: its class list when it
    # has one (weapons/armor), else its own aptitudes from the raw sheet
    # (jewelry — rings/necks/trinkets carry stat aptitudes like Crit, Fervor,
    # ArPen, MaPen, Vita, not classes, so `classes` is empty there).
    apts = item_row.get("classes") or item_row.get("aptitudes") or []
    e = max(1, len(apts))
    keys = [k for k in _STAT_GROUPS if k in atb] + \
           [k for k in atb if k not in _STAT_GROUPS]
    out_stats: list[dict] = []
    for key in keys:
        t = atb.get(key) or 0.0
        mode = _MODE.get(key)
        if mode is None:      # custom atb key (rare) — raw stat
            a = _any_curve(apts, "0", sc, ctx)
            raw = (_curve(a["start"], a["end"], m, p) if a else 0.0) * t * c
            v, mn, mx, r = w_(raw)
            out_stats.append({"key": key, "label": key, "value": v,
                              "min": mn, "max": mx, "raw": r})
            continue
        if mode in ("0", "3"):
            out: list[dict] = []
            tv = tn = tx = tr = 0.0
            for apt in apts:
                x = _apt_curve(apt, mode, sc, ctx)
                if not x:
                    continue
                cv = _curve(x["start"], x["end"], m, p)
                if x.get("gearOnly"):
                    v, mn, mx, r = w_(cv * t / e)
                else:
                    # the game keeps the curve x budget product as a float
                    # and applies math_round once at the end (confirmed in
                    # $HItem.generateItemAffixes bytecode) — the reference
                    # viewer's int(cv*t) truncation lost precision (Brie's
                    # Intellect 3.87 -> 3 vs live 4)
                    v, mn, mx, r = w_(cv * t * c / e)
                end_atb = x.get("endAtb")
                h = _STAT_LABELS.get(end_atb) or end_atb \
                    or _GROUP_LABELS.get(key, key)
                row = next((o for o in out if o["label"] == h), None)
                if row is not None:
                    # the same stat from a second aptitude (Fig+Cle Crimson
                    # both roll Fervor): the game SUMS the classes' shares into
                    # one line (Breastplate of Recklessness reads Fervor ~27,
                    # not one class's 15) — add into the existing row
                    row["value"] += v
                    row["min"] += mn
                    row["max"] += mx
                    row["raw"] += r
                    tv += v
                    tn += mn
                    tx += mx
                    tr += r
                    continue
                out.append({"label": h, "value": v, "min": mn,
                            "max": mx, "raw": r})
                tv += v
                tn += mn
                tx += mx
                tr += r
            if len(out) == 1:
                o = out[0]
                out_stats.append({"key": key, "label": o["label"],
                                  "value": o["value"], "min": o["min"],
                                  "max": o["max"], "raw": o["raw"]})
            elif not out:
                out_stats.append({"key": key, "label": _GROUP_LABELS.get(key, key),
                                  "value": 0, "min": 0, "max": 0, "raw": 0.0})
            else:
                out_stats.append({"key": key, "label": out[0]["label"],
                                  "value": tv, "min": tn, "max": tx,
                                  "raw": tr, "splits": out})
            continue
        if mode == "1":      # vitality -> health (divides by aptitudes x 3)
            tv = tn = tx = tr = 0.0
            found = False
            for apt in apts:
                x = _apt_curve(apt, "1", sc, ctx)
                if not x:
                    continue
                found = True
                v, mn, mx, r = w_(_curve(x["start"], x["end"], m, p) * t * c / e / 3)
                tv += v
                tn += mn
                tx += mx
                tr += r
            out_stats.append({"key": key, "label": "Vitality" if found else None,
                              "value": tv, "min": tn, "max": tx, "raw": tr})
            continue
        if mode == "2":      # armor: summed over unique aptitudes, no powerRatio
            acc = 0.0
            end_atb = None
            seen: set = set()
            for apt in apts:
                if apt in seen:
                    continue
                seen.add(apt)
                x = _apt_curve(apt, "2", sc, ctx)
                if x:
                    acc += _curve(x["start"], x["end"], m, p) * t
                    end_atb = end_atb or x.get("endAtb")
            # single-class gear reads a higher armor budget in the live game
            # than the sheet's atbRatio (see _SINGLE_CLASS_ARMOR); the boost
            # is per class and does NOT apply to multi-class gear (verified
            # against in-game + MetaForge: the 2-class Ram Faceshield matches
            # the sheet sum, while every single-class piece is 1.85-2.25x it)
            if len(apts) == 1 and apts[0] in _SINGLE_CLASS_ARMOR:
                acc *= _SINGLE_CLASS_ARMOR[apts[0]]
            v, mn, mx, r = w_(acc)
            out_stats.append({"key": key,
                              "label": _STAT_LABELS.get(end_atb) or end_atb
                              or "Armor",
                              "value": v, "min": mn, "max": mx, "raw": r})
            continue
        a = _any_curve(apts, mode, sc, ctx)
        if not a:
            out_stats.append({"key": key, "label": None, "value": 0,
                              "min": 0, "max": 0, "raw": 0.0})
            continue
        v, mn, mx, r = w_(_curve(a["start"], a["end"], m, p) * t * c)
        a_end = a.get("endAtb")
        out_stats.append({"key": key,
                          "label": _STAT_LABELS.get(a_end) or a_end or key,
                          "value": v, "min": mn, "max": mx, "raw": r})
    return {"ilevel": item_level(lvl, rar),
            "base_ilevel": m, "power_ratio": c, "stats": out_stats}


def gear_rarity_tiers(item_row: dict, level: int | None = None) -> list[dict] | None:
    """Per-rarity stat values for a gear item at `level` (default the item's
    fixed craft level for crafted gear, else the bundled max level, 25).
    The base column is the item's own rarity at that
    rarity's max upgrade rank (+N). Weapons expand to every rarity above
    their own (computed from the curves — they carry per-rarity stats
    in-game); armor and jewelry do not use per-rarity stats yet (a later
    update may add them), so their expansion is commented out and they
    show the single base column. When the item's data carries shipped
    per-rarity stats (`rarity_stats`, a later game update), those values
    override the columns for every category automatically.

    Each entry: {rarity, upgrades (that rarity's max upgrade rank — from
    rarity.json gearUpgrades: Uncommon 2, Rare 3, Epic 4, Legendary 5), stats:
    [{key, label, value}]}. None for non-gear / authored gear (no atb ratio).
    """
    if not is_gear(item_row):
        return None
    if not _atb_budget(item_row):
        return None
    sc = gear_scaling()
    lvl = _table_level(item_row, level)
    # the own-rarity column is the item's DISPLAY rarity, matching the
    # ladder (Guild Merchant gear shows its shop quality, Rare)
    from .sources import item_display_rarity
    own = (item_display_rarity(item_row.get("id") or "")
           or item_row.get("rarity") or "").title()
    order = ("Uncommon", "Rare", "Epic", "Legendary")
    if own not in order:
        own = order[0]   # unknown rarity -> treat as base Uncommon
    caps = sc.get("rarity_upgrades", {})
    out = []

    def _append(rar: str) -> None:
        gs = gear_stats(item_row, level=lvl, rarity=rar)
        if not gs:
            return
        stats = [{"key": s["label"], "label": s["label"], "value": s["value"]}
                 for s in stat_rows(gs["stats"])]
        if stats:
            out.append({"rarity": rar, "upgrades": caps.get(rar, 0),
                        "stats": stats})

    carried = _carried_rarity_stats(item_row, order)
    if carried:
        # shipped per-rarity stats override the computed columns
        for rar in order[order.index(own):]:
            rows = _stat_rows_from(carried.get(rar)) if carried.get(rar) else None
            if rows:
                out.append({"rarity": rar, "upgrades": caps.get(rar, 0),
                            "stats": rows})
            else:
                _append(rar)
    elif category(item_row.get("type")) == "Weapons":
        # weapons use per-rarity stats in-game — full computed expansion
        for rar in order[order.index(own):]:
            _append(rar)
    else:
        # armor + jewelry: per-rarity stats are not in the game yet (may
        # come in a later update) — COMMENTED OUT; the base column only
        _append(own)
        # for rar in order[order.index(own):]:
        #     _append(rar)
    return out or None
