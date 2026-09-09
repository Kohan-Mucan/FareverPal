"""Gear stat math: computed stats, rarity tiers, upgrade ladders, stat search.

Port of the Item Lookup viewer's computeStats / gear_scaling (the game's
per-aptitude exponential curves x the item's atbRatio budget split, gated by
rarity/faction conditions) plus the upgrade ladder — per-rarity bases, what
each +1 adds, and the material costs — and the stat-name index that powers
the item page's stat search.
"""
from __future__ import annotations

import math
import re
from functools import lru_cache

from ...rules import (RARITY_RANK_DESC, RARITY_RANK_LOWER,
                      UPGRADABLE_RARITIES)
from .. import cdb
from .catalog import _data, item, items
from .craft import is_craftable
from .labels import category, is_gear, is_two_handed, own_stats

# rarity -> which GearUpgrades material row. Legendary reuses the Epic crystal
# (there is no UpgradeLegendary item in the sheets) — this mapping is game
# logic, so it lives here rather than in the constant sheet. Uncommon uses
# the UpgradeAll (Spark Dust) row: the sheet lists it, but the LIVE game
# cannot upgrade Uncommon gear (see _LIVE_UPGRADE_CAPS), so the row only
# names the material the piece would take.
_UPGRADE_MATERIAL_ROW = {
    "Uncommon": "UpgradeAll",
    "Rare": "UpgradeRare",
    "Epic": "UpgradeEpic",
    "Legendary": "UpgradeEpic",
}

# The ONE live-verified deviation from the rarity sheet's own numbers: the
# sheet stamps Uncommon `gearUpgrades: 2`, but the live game only upgrades Rare
# and up, so Uncommon and Common gear is base-only. Every other rarity's cap
# is READ from `rarity.json` `props.gearUpgrades` (Rare 3 / Epic 4 /
# Legendary 5 today) — never restated here, so a game patch that changes them
# changes the app. Every iLevel / ladder / cost caller goes through
# upgrade_cap() so the BY STEP and BY RARITY tables can't disagree.
_NO_UPGRADE_RARITIES = frozenset({"Common", "Uncommon"})


@lru_cache(maxsize=1)
def _rarity_upgrade_caps() -> dict[str, int]:
    """Per-rarity upgrade counts from the rarity sheet's
    `props.gearUpgrades`, with the live-verified no-upgrade rarities pinned to
    0 (see _NO_UPGRADE_RARITIES). Missing/unbundled sheet -> {}."""
    caps: dict[str, int] = {}
    try:
        rows = cdb.lines("rarity")
    except Exception:
        rows = []
    for r in rows:
        rid = (r.get("id") or "").title()
        n = (r.get("props") or {}).get("gearUpgrades")
        if rid and isinstance(n, int) and rid not in _NO_UPGRADE_RARITIES:
            caps[rid] = n
    for rid in _NO_UPGRADE_RARITIES:
        caps[rid] = 0
    return caps

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

# The body-armor SLOTS those reports came from, and the ONLY ones the factor
# is applied to. Every calibration sample is a worn armor piece (Back, Head,
# Chest), so the set is exactly those three — the rule is not extended to a
# slot nobody measured.
#
# This is the second half of a fix whose first half was the Shield. The
# reports justify a per-SLOT scope by saying "no sample for this one", and
# that reasoning was applied to the off-hand while five other unsampled slots
# stayed in:
#
#   Shield          armor-only, and 300 x 2.25 read 676 against the two-class
#                   shields' 487, so the boost was the ONLY thing separating
#                   one shield's score from another's and BiS recommended the
#                   starter gear. Removed.
#   Legs/Shoulders/ Hands/Feet/ the same extrapolation, and it was quietly
#   Waist           deciding real picks: on all five, a single-class piece
#                   outranked a two-class piece purely on a multiplier
#                   measured on a CHEST. Measured across every class and role,
#                   10 of the 77 build slots moved when the factor was
#                   restricted to the sampled three (all Fighter, the class
#                   with the largest factor) — pinned by
#                   `test_the_single_class_armor_boost_stays_on_sampled_slots`.
#
# These slots keep the raw sheet math until an in-game tooltip says
# otherwise. That is the conservative direction: an under-stated armor
# number is visibly low next to its neighbours, where a 2.25x guess is
# indistinguishable from a real one.
_SINGLE_CLASS_ARMOR_TYPES = frozenset({"Back", "Head", "Chest"})

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
    with no faction has no rating curves and    rolls none. Returns () for
    non-gear / authored gear without an atb ratio."""
    # `item()` (not a bare _data() hit): the drops index keys rows BY id and
    # omits an `id` field, and everything downstream (_atb_budget ->
    # _has_guild_merchant, item_fixed_level's `_Craft` test) needs the id on
    # the row. The id-less row made guild-merchant/vendor gear take the
    # authored-stats branch and lose its ratings.
    it = item(item_id) or {}
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
    it = item(item_id) or {}     # carries `id` (see gear_ratings)
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
    out = []
    for rar in ("Uncommon", "Rare", "Epic", "Legendary"):
        bonus = bonuses.get(rar, 0)
        ups = upgrade_cap(rar)
        out.append({"rarity": rar, "base": base + bonus,
                    "max": base + bonus + step * ups, "upgrades": ups})
    return out


def granted_rank(rarity: str, max_rank: int = 5) -> int:
    """The rank the engine grants the `<Type>_Upgrade` ("Weapon Upgraded")
    passive to a piece of `rarity`: ONE BELOW that rarity's upgrade cap, 0 for
    a rarity that cannot upgrade.

    CALIBRATED IN GAME (2026-09-15): a Cheese Moon (Axe_Boomerang) tooltip
    reads Critical Chance 2% (Rare) / 3% (Epic) / 4% (Legendary) — the sheet
    ladder's ranks 2 / 3 / 4 — where the sheet's own min/maxRank conditions
    would key a +3 piece to rank 3 (3%). The reading sits one rank under each
    rarity's cap (Rare 3 -> 2, Epic 4 -> 3, Legendary 5 -> 4), on every
    rarity, so the mapping is the cap minus one and the VALUES stay the
    sheet's, untouched.

    SETTLED IN GAME (2026-09-15), by an independent observable: the engine
    stores the granted rank ON the skill instance it grants, so the mapping
    can be READ rather than inferred from a tooltip. A live read of the
    hero's granted skills returned kind="GreatAxe_Upgrade" with
    internalRank=4 (and, in the same session, "GreatSword_Upgrade" with
    internalRank=4), each with its originItem (st.item.Weapon) reading
    rarity="Legendary" and upgradeLevel=5.

    Legendary's cap is 5, so the engine granted rank 4 — cap minus one — and
    the ceiling step is exactly the step that separates the candidates:
    cap-minus-one (this rule) says 4, `rank = the upgrade level` says 5, and
    `level + cap - 4` says 5. The engine says 4, so both level-driven
    readings are out. ai/workspace/buffy/upgrade_rank_probe.py re-reads the
    same triple live (kind / internalRank / originItem rarity+upgradeLevel)
    if a patch ever moves it."""
    cap = upgrade_cap(rarity)
    return max(1, min(cap - 1, max_rank)) if cap > 1 else 0


def upgrade_cap(rarity: str) -> int:
    """The LIVE upgrade cap for a rarity — the max +N a piece can reach.
    Read from the rarity sheet's own `props.gearUpgrades` (Rare +3 / Epic +4 /
    Legendary +5 in the current data), except the live-verified rule that
    Uncommon and Common gear cannot be upgraded at all, so every iLevel /
    ladder / cost caller asks this instead of the raw sheet."""
    return _rarity_upgrade_caps().get((rarity or "").title(), 0)


def upgrade_material(rarity: str) -> str | None:
    """The gear-upgrade material item for a rarity (Spark Dust / Shard /
    Crystal), or None for rarities with no upgrades."""
    row_id = _UPGRADE_MATERIAL_ROW.get(rarity or "", "")
    if row_id not in _gear_upgrades():
        return None
    return _upgrade_material_names().get(row_id)


def upgrade_materials() -> list[dict]:
    """The Enchants > Upgrades matrix rows: one per UPGRADABLE rarity, in
    progression order, each {rarity, item (the GearUpgrades material row
    id), name (its display name), cap (the live +N), base (the per-rank
    sheet counts) and exp (the sheet's level exponent, for the cost
    matrix: count = base[rank] x level ** exp)}. Uncommon is skipped — its
    material row exists (Spark Dust) but the live cap is 0, so it never
    appears as an upgrade column. Legendary reuses the Epic crystal row, so
    it carries the same base and exponent."""
    out: list[dict] = []
    for rar in ("Uncommon", "Rare", "Epic", "Legendary"):
        cap = upgrade_cap(rar)
        if cap <= 0:
            continue
        row_id = _UPGRADE_MATERIAL_ROW.get(rar, "")
        row = _gear_upgrades().get(row_id)
        if not row:
            continue
        out.append({"rarity": rar, "item": row_id,
                    "name": _upgrade_material_names().get(row_id),
                    "cap": cap, "base": list(row["base"]),
                    "exp": row["exp"]})
    return out


# --- MEASURED costs (live game, 2026-09-26) ---------------------------------
# The sheet's `base x level^levelExponent` does NOT match what the game
# charges, and no single power law reproduces the measurements either. What
# the data DOES support, hand-read off the live upgrade UI:
#
#   * CRYSTAL is EXACT and LEVEL-INDEPENDENT. It matched the sheet's base
#     array (0,0,0,3,15) to the unit at every step and every level, so
#     `base[rank - 1]` is the right indexing, the zero-cost early steps are
#     real, and the figures are per-step (not cumulative).
#   * DUST and SHARD keep the shipped per-step RATIO (measured dust:shard
#     tracks the sheet's 6.00 / 4.50 / 4.00 at every level measured), so the
#     per-step SHAPE in the sheet is right and only the scale is wrong.
#   * A step charges SEVERAL materials at once, which the sheet never
#     implied: rare +2 is dust AND shard, epic +4 adds crystal.
#   * There is a per-rarity multiplier nothing in the shipped cost path
#     applies: Epic ~1.21x Rare, Legendary ~1.34x Rare.
#   * The LEVEL term is real but is NOT a power law. The 20->25 ratio differs
#     per step (1.293 / 1.287 / 1.275 for rare +1/+2/+3), and epic +3 goes
#     12->14 by only 1.053 but 14->25 by 1.798. A single exponent cannot
#     produce that, and a separable `f(step) x g(level)` cannot either.
#
# So there is no formula here to derive - only measurements. The table is
# keyed (rarity, step, level); a level between two measured ones is
# interpolated log-linearly (held-out check: predicting epic +3 at level 14
# from levels 12 and 25 lands within 8.6%, where the shipped formula is off
# by 57%). Outside the measured range there is no evidence at all, so the
# sheet formula answers and the caller is told that is what happened.
#
# RE-MEASURING after a game update: docs/UPGRADE_COSTS.md is the how-to (how
# to read a step off the live upgrade panel, which rows to add, which numbers
# in the UI and the tests move as a result, and the validation tests that must
# stay green). Add the rows you read - every derived/extrapolated cell at that
# level collapses into a measured one on its own.
#
# Rare was measured at levels 20 and 25; epic at 12, 14 and 25; legendary only
# at 25. Rows are ((material, count), ...) in the order the game lists them,
# with zero-cost materials omitted - a step lists exactly what is needed.
_MEASURED_UPGRADES: dict[tuple[str, int, int], tuple[tuple[str, int], ...]] = {
    # --- Rare -------------------------------------------------------------
    ("Rare", 1, 20): (("Spark Dust", 41),),
    ("Rare", 1, 25): (("Spark Dust", 53),),
    ("Rare", 2, 20): (("Spark Dust", 87), ("Spark Shard", 14)),
    ("Rare", 2, 25): (("Spark Dust", 112), ("Spark Shard", 18)),
    ("Rare", 3, 20): (("Spark Dust", 138), ("Spark Shard", 30)),
    ("Rare", 3, 25): (("Spark Dust", 176), ("Spark Shard", 39)),
    # --- Epic -------------------------------------------------------------
    ("Epic", 1, 25): (("Spark Dust", 64),),
    ("Epic", 2, 12): (("Spark Dust", 72), ("Spark Shard", 12)),
    ("Epic", 3, 12): (("Spark Dust", 113), ("Spark Shard", 25)),
    ("Epic", 3, 14): (("Spark Dust", 119), ("Spark Shard", 26)),
    ("Epic", 3, 25): (("Spark Dust", 214), ("Spark Shard", 47)),
    ("Epic", 4, 12): (("Spark Dust", 159), ("Spark Shard", 39),
                      ("Spark Crystal", 3)),
    ("Epic", 4, 14): (("Spark Dust", 167), ("Spark Shard", 41),
                      ("Spark Crystal", 3)),
    ("Epic", 4, 25): (("Spark Dust", 300), ("Spark Shard", 75),
                      ("Spark Crystal", 3)),
    # --- Legendary (level 25 only) ---------------------------------------
    ("Legendary", 1, 25): (("Spark Dust", 71),),
    ("Legendary", 2, 25): (("Spark Dust", 150), ("Spark Shard", 25)),
    ("Legendary", 3, 25): (("Spark Dust", 236), ("Spark Shard", 52)),
    ("Legendary", 4, 25): (("Spark Dust", 330), ("Spark Shard", 82),
                           ("Spark Crystal", 3)),
    ("Legendary", 5, 25): (("Spark Dust", 434), ("Spark Shard", 115),
                           ("Spark Crystal", 15)),
}

#: the item levels any cost was measured at, ascending.
MEASURED_UPGRADE_LEVELS = tuple(sorted({k[2] for k in _MEASURED_UPGRADES}))

# provenance of an answer from upgrade_step_cost / measured_upgrade_cost
MEASURED = "measured"        # read off the live game at exactly this level
INTERPOLATED = "interpolated"  # between two measured levels
EXTRAPOLATED = "extrapolated"  # past the highest measured level: the same
                               # log-linear curve extended, NOT a measurement
DERIVED = "derived"          # Rare's measured cost x a validated rarity mult
UNVERIFIED = "unverified"    # no measurement anywhere near: the sheet's guess

#: Per-rarity cost multiplier over Rare, solved from the level-25 rows (the one
#: level where all three rarities were measured on the same steps) and checked
#: by prediction: applied back to Rare it reproduces 4 of the 5 measured
#: Epic/Legendary level-25 cells to the unit and the fifth to within 1 (0.5%).
#: The per-step spread of the ratio is 0.69% (Epic) / 0.12% (Legendary), which
#: is what makes a single constant defensible rather than a per-step fudge.
#:
#: This is a RARITY property, not a level one: it is constant across the steps
#: it was solved from. It is NOT applied across levels - the level term is not
#: characterised (see above), so a level with no Rare measurement still falls
#: through to the sheet. Only Rare is the anchor; Uncommon has no upgrades.
_RARITY_COST_MULT = {"Rare": 1.0, "Epic": 1.2117, "Legendary": 1.3399}

#: the rarity whose measured costs anchor the derived ones.
_COST_ANCHOR_RARITY = "Rare"


def upgrade_cost_levels() -> list[int]:
    """The item levels the cost table may be PRICED at, ascending - the level
    chips the Enchants > Upgrades tab offers.

    Only levels the ANCHOR rarity (`_COST_ANCHOR_RARITY`, Rare) was measured
    at, and only up to the game's own `max_level`. That is the honest bar: at
    a level with no Rare reading the whole table would be extrapolation (rare
    +4/+5) or a scaling of the rarity's own row, so offering the level would
    mean offering a level nothing in the table was actually read at. Rare is
    measured at 20 and 25, so those are the two chips today; measure Rare at a
    new level and the chip appears by itself, with no UI change.

    Falls back to [max_level] if the table is ever empty, so the tab always
    has something to price.
    """
    cap = int(gear_scaling().get("max_level") or 25)
    lv = sorted({k[2] for k in _MEASURED_UPGRADES
                 if k[0] == _COST_ANCHOR_RARITY and k[2] <= cap})
    return lv or [cap]


def measured_levels_for(rarity: str, step: int) -> list[int]:
    """Item levels this (rarity, step) was measured at, ascending."""
    r = (rarity or "").title()
    return sorted(k[2] for k in _MEASURED_UPGRADES
                  if k[0] == r and k[1] == int(step))


def _anchor_level_floor() -> int:
    """The lowest item level the anchor rarity was measured at — the edge of
    where the level curve has any support.

    The curve in `_level_curve_factor` is fitted on ONE span of the anchor's
    readings (20->25 today), so it is a local approximation. It is asked to
    extrapolate past the readings in both directions, and that is fine for a
    short step either way — but a long way below the lowest reading it is
    extrapolating from nothing, and produced figures far below the measured
    ones (a derived step appearing to get CHEAPER as the gear got better, with
    a level-1 Epic +1 priced at 2 Spark Dust). Every derivation stops at this
    floor and reports nothing instead.

    Returns 1 when the anchor rarity has no readings at all, so the guard
    cannot silently disable every derivation.
    """
    lv = [k[2] for k in _MEASURED_UPGRADES
          if k[0] == _COST_ANCHOR_RARITY]
    return min(lv) if lv else 1


def measured_upgrade_cost(rarity: str, step: int, level: int | None = None
                          ) -> tuple[list[dict], str] | None:
    """([{material, count}, ...], provenance) for this rarity/step/level.

    Returns None ONLY when nothing can be said at all - i.e. this rarity/step
    has no measurement AND no sheet row. A missing measurement is never
    reported as a zero cost.

    provenance is MEASURED when `level` is a level this step was read at,
    INTERPOLATED when it falls strictly between two such levels, and None
    (with the sheet fallback left to the caller) when it falls outside them —
    EXCEPT one case: past the HIGHEST measured level the same log-linear curve
    is extended (EXTRAPOLATED), so a view at a level the game grew past still
    shows costs that follow the real curve instead of the 3-5x-off sheet
    formula. Extrapolation is deliberately one-directional: it fires only above
    the measured range, never below it (low levels have their own anchors), and
    it needs two measured levels to extend.
    """
    if level is None:
        return None
    r, st, lv = (rarity or "").title(), int(step), int(level)
    exact = _MEASURED_UPGRADES.get((r, st, lv))
    if exact is not None:
        return [{"material": m, "count": c} for m, c in exact], MEASURED
    levels = measured_levels_for(r, st)
    lo = [x for x in levels if x <= lv]
    hi = [x for x in levels if x >= lv]
    if not lo or not hi:
        # outside the measured range. Above it (and only above it), extend the
        # same log-linear curve the interpolation uses: the two HIGHEST
        # measured levels are the anchors, t > 1. Below it, stay silent - a
        # low level has its own anchors or nothing honest to say.
        if not (levels and lv > levels[-1] and len(levels) >= 2):
            return None
        a, b = levels[-2], levels[-1]
        src = EXTRAPOLATED
    else:
        a, b = lo[-1], hi[0]
        if a == b:
            return None
        src = INTERPOLATED
    # log-linear in level, applied per material: the game curve is not a power
    # law, so this is a local approximation between two real measurements.
    out = []
    base_a = dict(_MEASURED_UPGRADES[(r, st, a)])
    base_b = dict(_MEASURED_UPGRADES[(r, st, b)])
    for mat in base_a:
        ca, cb = base_a[mat], base_b.get(mat)
        if cb is None or cb == 0 or ca == 0:
            out.append({"material": mat, "count": ca})
            continue
        t = ((math.log(lv) - math.log(a)) / (math.log(b) - math.log(a)))
        out.append({"material": mat,
                    "count": round(ca ** (1 - t) * cb ** t)})
    return out, src


def _derived_from_anchor(rarity: str, step: int, level: int
                         ) -> tuple[list[dict], str] | None:
    """This rarity/step/level via a measured row x the rarity mult.

    Only used when the rarity itself has no measured answer at `level`. The
    anchor is Rare's answer AT THAT LEVEL — measured, interpolated or
    extrapolated — so a level past the measurements still derives every rarity
    from the same curve Rare extends on. Every material scales together, so a
    derived step still lists the shard and crystal the real one charges rather
    than dust alone.

    When even Rare has no anchor for this STEP at `level` (Rare's ladder is
    the shortest — +3 — so its +4/+5 have no row to extend), the anchor is the
    RARITY'S OWN highest measured level for that step, grown by the SAME
    level curve Rare's extrapolation uses. The curve is a property of LEVEL,
    not rarity (rarity is the constant multiplier), so applying it to the
    rarity's own measured row is the same operation Rare gets — and it keeps
    the whole material list (Legendary +4 at 30 stays dust + shard + crystal,
    not a lone crystal from the sheet).

    That row is the RARITY'S OWN cost, already carrying its premium, so the
    rarity multiplier is NOT applied to it — doing so counted the premium
    twice and made Legendary +4 read 423 Spark Dust at level 24 against a
    MEASURED 330 at level 25, a step that got more expensive as the gear got
    better. Only the level factor applies here.

    That growth is bounded by the EVIDENCE, not by direction. The curve is
    fitted on one short span (Rare's 20->25), so stepping a little past either
    end is the same local inference the extrapolation already makes — which is
    why Legendary +4 at level 20 still answers from its own 25-row. Running it
    far below is not: level 1 is ~11 fitted spans away and came out at 2 Spark
    Dust for Epic +1 and 17 for Legendary +5, figures far CHEAPER than the
    measured level above them. So the floor is the anchor rarity's LOWEST
    measured level (20 today) — the edge of where the curve has any support at
    all. Below it there is nothing to scale from and this returns None, which
    leaves the caller to say nothing rather than guess.

    Returns None when neither anchor exists.
    """
    r = (rarity or "").title()
    mult = _RARITY_COST_MULT.get(r)
    if mult is None or r == _COST_ANCHOR_RARITY:
        return None
    anchor = measured_upgrade_cost(_COST_ANCHOR_RARITY, int(step), int(level))
    if anchor:
        return ([{"material": c["material"], "count": round(c["count"] * mult)}
                 for c in anchor[0]], DERIVED)
    # no Rare anchor for this step at this level: take the rarity's own
    # highest measured row and grow it along the level curve (see
    # _level_curve_factor — it reads the curve off the anchor rarity's
    # measured steps, whatever step has two anchors). Bounded below by where
    # the curve has any support (see the docstring).
    own = measured_levels_for(r, int(step))
    floor = _anchor_level_floor()
    if not own or int(level) < floor:
        return None
    base_lv = own[-1]
    factor = _level_curve_factor(_COST_ANCHOR_RARITY, int(level))
    if factor is None:
        return None
    row = _MEASURED_UPGRADES[(r, int(step), base_lv)]
    # the level factor ONLY: `row` is this rarity's own measured cost, so it
    # already includes the premium `mult` stands for (see the docstring).
    return ([{"material": m, "count": round(c * factor)}
             for m, c in row], DERIVED)


def _level_curve_factor(rarity: str, to_lv: int) -> float | None:
    """The level curve's growth factor per level unit, read off `rarity`'s
    measured anchors and applied from that rarity's HIGHEST measured level up
    to `to_lv`.

    The factor is a property of the LEVEL span, not of the step: held-out
    validation (test_interpolation_lands_close_on_a_held_out_level) showed the
    same curve fits every step, so the anchors can come from any step with
    two measured levels. Rare is the anchor rarity, so this is Rare's 20->25
    growth extended — the same curve `measured_upgrade_cost` extrapolates
    with, just folded into one number.

    Returns None when the rarity has no two measured levels to read a curve
    from.
    """
    r = (rarity or "").title()
    # the highest step of this rarity that was measured at TWO levels: the
    # curve is shared across steps, so any such pair answers — and the
    # highest pair is the one closest to `to_lv`
    by_step: dict[int, dict[int, tuple[tuple[str, int], ...]]] = {}
    for k, row in _MEASURED_UPGRADES.items():
        if k[0] == r:
            by_step.setdefault(k[1], {})[k[2]] = row
    best = None
    for step, rows in sorted(by_step.items(), reverse=True):
        lvs = sorted(rows)
        if len(lvs) < 2:
            continue
        a, b = lvs[-2], lvs[-1]
        mat_a = dict(rows[a])
        mat_b = dict(rows[b])
        mat = next((m for m, c in mat_a.items()
                    if c and mat_b.get(m)), None)
        if mat is None:
            continue
        best = (a, b, mat, mat_a[mat], mat_b[mat])
        break
    if best is None:
        return None
    a, b, mat, ca, cb = best
    t = ((math.log(to_lv) - math.log(a))
         / (math.log(b) - math.log(a)))
    return (ca ** (1 - t) * cb ** t) / cb


def upgrade_step_cost(rarity: str, step: int, level: int,
                      fallback_material: str | None = None
                      ) -> tuple[list[dict], str]:
    """ONE place that answers "what does this +N cost at this level".

    Returns ([{material, count}, ...], provenance) where provenance is one of
    MEASURED / INTERPOLATED / EXTRAPOLATED / DERIVED / UNVERIFIED. Both the
    item ladder and the Enchants cost matrix call THIS, so a cell and a ladder
    step can never disagree - they used to compute the cost independently and
    could drift.

    At a measured level the game charges several materials per step, so the
    list can hold more than one entry.

    The sheet's `base x level^exp` fallback answers ONLY for a step that was
    never measured for this rarity at ANY level (Epic +5, past its cap of 4,
    is the live example) — and the measurements show even that path is wrong
    by 3-5x, so the answer is UNVERIFIED. When the step WAS measured, just not
    at this level, the sheet does not get a say: a level the game has but the
    table has not read is a missing reading, not a licence for the formula the
    readings overturned. It is also arithmetically absurd at the bottom of the
    range — `base[0]` is 0 for the early steps, so a level-2 weapon's first
    upgrade priced at 0.0 Spark Dust, which reads as free. That case now
    returns no cost at all, and the caller says nothing rather than lying.
    """
    got = measured_upgrade_cost(rarity, step, level)
    if got is not None:
        return got
    got = _derived_from_anchor(rarity, step, level)
    if got is not None:
        return got
    if measured_levels_for(rarity, step):
        return [], UNVERIFIED     # measured elsewhere, not here: no answer
    key = _UPGRADE_MATERIAL_ROW.get((rarity or "").title(), "")
    row = _gear_upgrades().get(key or "")
    material = _upgrade_material_names().get(key) or fallback_material
    if not row:
        return [], UNVERIFIED
    bc, exp = row["base"], row["exp"]
    count = round(bc[min(int(step) - 1, len(bc) - 1)] * (level ** exp), 2)
    return [{"material": material, "count": count}], UNVERIFIED


def upgrade_costs(item_row: dict, level: int | None = None) -> list[dict] | None:
    """Per-rank material counts for the item's OWN rarity at `level` (default:
    the item's own table level, via `_table_level` — its fixed craft level for
    crafted gear, else its source max). Each entry: {upgrade, count, material,
    costs, source}.

    The default used to be the bundled max level, which made this disagree with
    `upgrade_ladder` — the sibling call for the SAME item resolved the level
    through `_table_level` and got a different answer, so 155 of 309 gear items
    carried two different cost tables at once (a level-20 Rare drop showed 41/
    87/138 in its ladder and 53/112/176 here). One item, one level, one cost.

    `count`/`material` are the PRIMARY (first) material, kept for existing
    callers. `costs` is the full [{material, count}] list - a measured step
    charges SEVERAL materials, which the sheet's one-material-per-rarity
    model could not express. `source` is "measured" (read off the live game
    at this exact level), "interpolated" (between two measured levels),
    "extrapolated" (past the highest measured level), "derived" (Rare's
    measured cost at the same level x this rarity's validated multiplier) or
    "unverified" (no measurement anywhere near). A step the table has no
    reading for at this level is OMITTED rather than filled in from the
    shipped formula - see upgrade_step_cost."""
    if not is_gear(item_row):
        return None
    rar = item_row.get("rarity") or ""
    key = _UPGRADE_MATERIAL_ROW.get(rar)
    row = _gear_upgrades().get(key or "")
    if not row:
        return None
    lvl = _table_level(item_row, level)
    if lvl is None:
        # no fixed craft level and no source max (an item id nothing knows):
        # there is no level to price at, and the cost path needs a real int
        # (the derivation interpolates on log(level)). No rows is the honest
        # answer — the same one a step with no reading gets.
        return []
    cap = upgrade_cap(rar)
    material = _upgrade_material_names().get(key)
    out = []
    for i in range(1, cap + 1):
        costs, source = upgrade_step_cost(rar, i, lvl, material)
        if not costs:
            continue
        # `count`/`material` stay the PRIMARY (first) material so every
        # existing caller keeps working; `costs` carries the full list,
        # because a measured step charges SEVERAL materials, and `source`
        # says whether the number was measured, interpolated, or is still the
        # shipped (unverified) formula.
        out.append({"upgrade": i, "count": costs[0]["count"],
                    "material": costs[0]["material"],
                    "costs": costs, "source": source})
    # An empty list is NOT the same answer as None: None means "this rarity has
    # no upgrade row at all" (Common), while [] means "it has one but the cap
    # is 0" (Uncommon, whose cap the game gates behind something else). Both
    # are "no cost rows" to a caller, but the distinction is load-bearing.
    return out


def upgrade_path(item_row: dict, level: int | None = None,
                 rarity: str | None = None) -> list[int] | None:
    """iLevel at every upgrade step (+0 .. +cap) for the item's OWN rarity.

    Each upgrade adds `upgrade_ilevel` (10) to the base + rarity bonus at
    `level` (default the bundled max level); the cap is that rarity's
    LIVE upgrade cap (upgrade_cap). `rarity` overrides the authored rarity — the items
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
    cap = upgrade_cap(rar)
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
    cap = upgrade_cap(item_row.get("rarity") or "")
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


# iLevel a WORN piece sits above what `upgrade_path` derives for it, by
# rarity. MEASURED against the game's own tooltips (2026-09-26), not
# authored: the compiled `rarity_ilevel` is 10 short for Legendary.
#
# `upgrade_path` puts a L25 Legendary at +5 on iLevel 370
# (10*25 + rarity_ilevel 70 + 5 * upgrade_ilevel 10). Four worn pieces were
# read off their own tooltips and every stat of every one was solved back
# through the curves:
#
#   piece                          gems  solved iLevel  rarity  exact?
#   Silhouette of Almaz  (Staff)     +5           380  Legend.  yes
#   Twin Fangs of Ratsar (Daggers)   +5           380  Legend.  yes
#   Cheese Moon           (Axe)      +5           380  Legend.  yes
#   Ghost Clams of the Low Tide      +4           340  Epic     yes
#
# The 340 is what makes this a per-rarity table and not the flat +1 step it
# first looked like: an Epic at +4 is EXACTLY right with no bonus at all
# (250 + 50 + 40 = 340), so a blanket extra step overstates it by 10. Only
# Legendary needs the +10. Every match is exact on all of a piece's stats,
# splits included — Cheese Moon's Dex 35 + Str 29 and ArPen 55 + Crit 55 are
# our model's 64 and 110 shown as the two lines the tooltip prints.
#
# SUSPECTED ROOT CAUSE, deliberately not fixed here: the game's Legendary
# rarity_ilevel looks like 80, not the 70 the compiled sheet carries. That
# is a data correction, and it moves the Items-page planner and the upgrade
# ladder for every Legendary piece in the game, not just worn ones — a
# bigger change than the worn-piece maths, and `data/raw_*.py` is being
# regenerated underneath this. Kept local and named until that is settled.
_WORN_ILEVEL_BONUS = {"Legendary": 10}

# The rarity bands a worn piece can be in, cheapest first. Used only to work
# out a FLOOR for the band (see _effective_rarity), never to raise one.
_RARITY_ORDER = UPGRADABLE_RARITIES


def _effective_rarity(rarity: str | None, upgrade: int | None) -> str | None:
    """The rarity band a worn piece is costed at.

    `core/inspect._read_item` reads a live `rarity` string off the piece and
    FALLS BACK to the sheet's AUTHORED rarity when it cannot — and every one
    of the pieces measured on 2026-09-26 is authored `Rare` while being worn
    at +5, which the game itself calls Legendary (Rare caps at +3, Epic at
    +4). So on the fallback path the band is wrong, and the iLevel with it:
    the staff costs iLevel 310 instead of 380 and reads 84 Critical where its
    own tooltip says 110.

    The upgrade rank settles it without trusting the string. A band cannot
    hold more ranks than its cap, so a +5 piece can only be Legendary and a
    +4 piece can only be Epic or better. The returned band is therefore the
    LATER of what was read and the lowest band that can hold the rank — the
    string can still promote a piece (a Legendary at +2 stays Legendary),
    it just cannot demote one past what its own gems prove.
    """
    rank = int(upgrade or 0)
    read = (rarity or "").title()
    floor = None
    for cand in _RARITY_ORDER:
        if upgrade_cap(cand) >= rank:
            floor = cand
            break
    if floor is None:
        return rarity
    if read in _RARITY_ORDER and _RARITY_ORDER.index(read) >= _RARITY_ORDER.index(floor):
        return read
    return floor


def equipped_stats(item_row: dict, level: int | None = None,
                   rarity: str | None = None,
                   upgrade: int | None = None) -> dict | None:
    """`gear_stats` for a piece as the hero actually WEARS it.

    The gear walk reads each equipped item's live `upgradeLevel` rank and
    live `rarity` string by name (core/inspect._read_item), and the selected
    piece already printed the rank as "+5" in its caption — but both the
    totals sum and the piece panel handed `gear_stats` only level+rarity, so
    every worn piece was costed as if freshly dropped at +0. A maxed staff
    read 69 Critical where its tooltip says 110: a 60% undercount on the
    largest item the hero owns, on the block the page leads with.

    `rarity` is the band to trust no further than its caps allow — see
    _effective_rarity, which is what makes this correct whether or not the
    live string came back. `upgrade` is in rank steps, not iLevel: each step
    is `upgrade_ilevel` (10) on the real item, expressed as one step of
    `level` so the existing curve maths is untouched, plus
    `_WORN_ILEVEL_BONUS` for the band. Falls back to plain `gear_stats` when
    there is no level to advance.
    """
    if not level:
        return gear_stats(item_row, None, rarity)
    band = _effective_rarity(rarity, upgrade)
    step = gear_scaling().get("upgrade_ilevel") or 10
    bonus = _WORN_ILEVEL_BONUS.get(band or "", 0)
    return gear_stats(item_row, int(level) + int(upgrade or 0)
                      + bonus // step, band)


# Critical rating -> Critical Chance (%).
#
# MEASURED against the game's own character sheet on 2026-09-26, not
# authored in any shipped table — no sheet we carry holds a divisor, and the
# live process never stores the sheet's numbers (the game computes them when
# the sheet draws; a differential scan of the hero with gear on vs off moved
# the UI's layout boxes and the character's position, and not one attribute).
#
# Three readings of one character pin it, and they cross-check:
#
#   state          sheet    implied gear Critical rating
#   naked           6.0%           0
#   weapon off     13.6%         144.4
#   fully geared   19.4%         254.6
#
# The two absolute readings differ by 110.2, and the staff that separates
# them grants exactly +110 Critical — so the divisor is not fitted to one
# delta, it is the same number three ways. The sheet rounds to one decimal,
# which bounds the divisor to [18.80, 19.13]; 19 is the only round value in
# that window (18 predicts 6.1, 20 predicts 5.5).
#
# The BASE is not zero: a completely naked level-25 character reads 6.0%, so
# the rule is additive. It is also class-independent — the Mage (Int 40 /
# Dex 34) and the Rogue (Int 34 / Dex 40) have SWAPPED primaries and both
# read 6.0% — which is what rules the primaries out of the conversion and
# leaves a single per-rating divisor.
CRIT_CHANCE_DIVISOR = 19.0

# The naked-character base chance, per level. MEASURED — 25 is the only
# level read so far, and a level that is not in here returns None rather
# than an extrapolated number. The gear walk's own `level` is the
# character's level (the tooltip prints "Level 25" beside a level-25 item),
# so this keys off the same number the sheet does.
_CRIT_BASE_BY_LEVEL: dict[int, float] = {25: 6.0}


def crit_chance_pct(rating: float, level: int | None) -> float | None:
    """Critical Chance % for a summed Critical `rating` at `level`.

    `base(level) + rating / CRIT_CHANCE_DIVISOR`. None when the level has
    no measured base — an uncalibrated level must show nothing rather than
    a number borrowed from level 25, which would read as authoritative on
    the page's main readout and be wrong for every hero not standing at it.
    """
    if not level:
        return None
    base = _CRIT_BASE_BY_LEVEL.get(int(level))
    if base is None:
        return None
    return base + float(rating) / CRIT_CHANCE_DIVISOR


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
# "rounding" (default — the clean game-tooltip integer) or "true" (the exact
# computed float, 3 decimals). Toggled from the Items search header; it's a
# session-level UI preference, so it lives here where both display surfaces
# (farm row, upgrade matrix) can read it without threading state through
# every render call. The old third form ("both", '4 (3.985)') was dropped
# from the header toggle and is refused below, so the two surfaces and the
# tests pin one contract instead of a form nothing can select.
_STAT_DISPLAY_MODE = "rounding"


def stat_display_mode() -> str:
    """The active gear-stat display mode: 'rounding' or 'true'."""
    return _STAT_DISPLAY_MODE


def set_stat_display_mode(mode: str) -> None:
    """Set the gear-stat display mode: rounded integers, or exact floats.
    Anything else — the retired 'both' included — is ignored, never latched,
    so the button's cycle and the rendered form can't disagree."""
    global _STAT_DISPLAY_MODE
    if mode in ("rounding", "true"):
        _STAT_DISPLAY_MODE = mode


def stat_text(value, raw=None) -> str:
    """The display form of a stat value per stat_display_mode(): the rounded
    integer the game's tooltip shows ('rounding', the default) or the TRUE
    computed float to 3 decimals with trailing zeros stripped ('true').
    Stats without a computed raw (authored/shipped values, exact by
    definition) always render plainly.
    """
    if raw is None:
        return f"{value:g}"
    if _STAT_DISPLAY_MODE == "rounding":
        return f"{value:g}"
    return f"{raw:.3f}".rstrip("0").rstrip(".")


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
    if not _atb_budget(item_row) or _is_authored_affixes(item_row):
        # authored gear (crafted / non-vendor fixed-stat items): fixed
        # affixes, no scaling curves — one column with the item's own stats
        # as base and material-only steps (the affixes never move with
        # level). Guild Merchant gear is NOT authored: the vendor sells it
        # scaled to each town, so it takes the curve path below
        cap = upgrade_cap(own)
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
        cap = upgrade_cap(rar)
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
        material = _upgrade_material_names().get(row_id)
        for s in steps:
            costs, source = upgrade_step_cost(rar, s["upgrade"], lvl,
                                              material)
            if costs:
                s["count"] = costs[0]["count"]
                s["costs"] = costs
                s["source"] = source
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
    cap = upgrade_cap(rarity)
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
_RANK = RARITY_RANK_LOWER
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
            # the sheet sum, while every single-class piece is 1.85-2.25x it),
            # nor to a Shield — no calibration sample is an off-hand piece
            # (see _SINGLE_CLASS_ARMOR_TYPES)
            if (len(apts) == 1 and apts[0] in _SINGLE_CLASS_ARMOR
                    and (item_row.get("type") or "")
                    in _SINGLE_CLASS_ARMOR_TYPES):
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

    Each entry: {rarity, upgrades (that rarity's max upgrade rank — the
    LIVE cap: Rare 3, Epic 4, Legendary 5; Uncommon cannot upgrade), stats:
    [{key, label, value}]}. None for non-gear / authored gear (no atb ratio).
    """
    if not is_gear(item_row):
        return None
    if not _atb_budget(item_row):
        return None
    lvl = _table_level(item_row, level)
    # the own-rarity column is the item's DISPLAY rarity, matching the
    # ladder (Guild Merchant gear shows its shop quality, Rare)
    from .sources import item_display_rarity
    own = (item_display_rarity(item_row.get("id") or "")
           or item_row.get("rarity") or "").title()
    order = ("Uncommon", "Rare", "Epic", "Legendary")
    if own not in order:
        own = order[0]   # unknown rarity -> treat as base Uncommon
    out = []

    def _append(rar: str) -> None:
        gs = gear_stats(item_row, level=lvl, rarity=rar)
        if not gs:
            return
        stats = [{"key": s["label"], "label": s["label"], "value": s["value"]}
                 for s in stat_rows(gs["stats"])]
        if stats:
            out.append({"rarity": rar, "upgrades": upgrade_cap(rar),
                        "stats": stats})

    carried = _carried_rarity_stats(item_row, order)
    if carried:
        # shipped per-rarity stats override the computed columns
        for rar in order[order.index(own):]:
            rows = _stat_rows_from(carried.get(rar)) if carried.get(rar) else None
            if rows:
                out.append({"rarity": rar, "upgrades": upgrade_cap(rar),
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


# --- Loadout planner ---------------------------------------------------------
# The Loadout Builder tab (ui/pages/items/loadout.py) asks these for the
# stat totals of an equipped set, the best-in-slot pieces, and an augment
# plan. They all read the same gear stats as the rest of the data layer —
# the aptitude curves at the piece's real level, DISPLAY rarity (Guild
# Merchant gear shows its shop quality) and upgrade rank, where each rank
# is +10 iLevel (= +1 level to gear_stats).

# Rarity order for tie-breaks: higher rarity wins when two pieces score the
# same (Legendary 0 ... Common 4).
_RARITY_RANK = RARITY_RANK_DESC

# The 11 equipment slots in the order the loadout UI shows them: display
# label -> the item type that fills it.
_LOADOUT_SLOTS = (
    ("Head", "Head"), ("Shoulders", "Shoulders"), ("Chest", "Chest"),
    ("Hands", "Hands"), ("Waist", "Waist"), ("Legs", "Legs"),
    ("Feet", "Feet"), ("Neck", "GearNeck"), ("Ring 1", "GearFinger"),
    ("Ring 2", "GearFinger"), ("Trinket", "GearTrinket"),
)
_JEWELRY_TYPES = ("GearNeck", "GearFinger", "GearTrinket")

# Build-score weights (ai/workspace/loadout_builder/README.md): the build's
# primary stat leads at 1.5x, Critical is soft-capped at 35% (0.3x above the
# cap, so a balanced set beats a crit-stacked one), then Fervor 1.1x, the
# penetration ratings 1.0x, Vitality a modest 0.5x and Armor 0.2x.
_CRIT_SOFT_CAP = 35.0
_CRIT_CAP_FACTOR = 0.3
_PRIMARY_STAT_NAMES = ("Strength", "Dexterity", "Intellect", "Faith")

# A shield's KIT, not its armor, is what separates the catalog's shields. The
# three Rare ones all roll the same 0.337 armor budget and read an identical
# 564 at their caps, and the two Cleric/Fighter shields (Dominion, Crabgantua's
# Kneecap) even carry the SAME class list — so neither armor nor class can
# pick between them, which is why the off-hand read as an alphabetical
# accident. The sheets do separate them, in the skills every shield carries:
#   Shield_Craft       Fortifying Cry — "Increases the Armor of ALL ALLIES
#                      within 40m by X% for 10s" — plus Heartsteel (the next
#                      Combo Attack deals additional damage)   -> the tank's
#   Shield_OrbitWater  Depth Shield — a bubble that "deals damage to enemies
#                      and HEALS ALLIES" — plus Shell of the Devourer (Blocks
#                      stack Water Infusion)                   -> the healer's
#   Shield_Firebreath  Furnace Roar + Cinder Coat: blocking procs AoE damage
#                                                               -> the DPSer's
#   Shield_Start       Shield Bash, a 1s stun          -> the starter, and it
#                      is the one that CANNOT win: Uncommon authored, so 348
#                      Armor at its cap against the other three's 564.
# The value is NOT a stat comparison and is deliberately larger than the
# whole armor term (564 x 0.2 = 112.8): a party armor buff or an ally heal is
# the reason to hold a shield, and it must outrank a rounding difference in a
# stat all three share.
# value: key -> (fit, why it wins) — the `why` is the annotation the off-hand
# card shows beside the contenders, so the ranking can explain itself instead
# of being a number with no reason attached.
#
# The ROLE leads, because the role is what a build is FOR and the kit is
# written for a job: Fortifying Cry is a party-armor buff, Depth Shield is an
# ally heal, Cinder Coat is damage off every block. Keying on class alone got
# two of the three right by luck (a Fighter is usually tanking, a Cleric
# usually healing) and the third wrong — a Fighter on the Damage chip was
# handed the tank's shield, which is a wrong build that reads like a right
# one. `_loadout_toggle_role` re-decides the off-hand for the same reason it
# re-decides the main hand.
_SHIELD_ROLE_FIT = {
    "tank": {"Shield_Craft": (120.0,
                              "Fortifying Cry buffs all allies' Armor")},
    "healer": {"Shield_OrbitWater": (120.0,
                                     "Depth Shield heals all allies")},
    "damage": {"Shield_Firebreath": (120.0,
                                      "Cinder Coat burns nearby enemies "
                                      "every time you block")},
}

# The class fallback, and it is load-bearing rather than legacy: a role fit
# can name a shield the class CANNOT wield. `Shield_Firebreath` is
# Fighter/Wizard, so a Cleric or a Wizard-healer asking for the damage shield
# gets nothing from the role table — and with all four shields on an
# identical 564 Armor that leaves the off-hand tie-broken ALPHABETICALLY onto
# whichever id sorts first, which is the accident this whole table exists to
# prevent. So when the role's shield is off-limits the class still gets its
# own: a Cleric on Damage falls back to Depth Shield, a Fighter on Damage to
# Fortifying Cry. A class with neither a role nor a class fit takes the plain
# armor ranking, and says so with an empty `why` on the card.
_SHIELD_CLASS_FIT = {
    "Fighter": {"Shield_Craft": (120.0,
                                 "Fortifying Cry buffs all allies' Armor")},
    "Cleric": {"Shield_OrbitWater": (120.0,
                                     "Depth Shield heals all allies")},
    # a Wizard can wield NEITHER of the two class shields (both are
    # Cleric/Fighter), so without this the Wizard's off-hand had no fit at
    # all: the role fit names a shield the Wizard cannot hold, the class
    # fallback had no Wizard, and the single remaining contender won
    # alphabetically with an EMPTY `why` — a recommendation that could not
    # explain itself on the one class that has the fewest options.
    "Wizard": {"Shield_Firebreath": (120.0,
                                      "Cinder Coat burns nearby enemies "
                                      "every time you block")},
}


def _shield_kit_fit(class_filter: str | None, role: str | None
                    ) -> dict[str, tuple[float, str]]:
    """The {shield id: (fit, why)} for a class building a role — the role's
    own fit where the class can wield that shield, the class fallback where
    it cannot. See `_SHIELD_ROLE_FIT` for why the fallback still exists.

    An illegal role resolves to the DEFAULT build first, the same guard the
    other two role-taking entry points carry, so a stale chip cannot ask a
    Rogue for a tank's shield.
    """
    if role and not class_can_role(class_filter, role):
        role = "damage"
    # The role fit REPLACES the class fit, it does not join it. Merging them
    # was the first attempt and it silently did nothing: both carry 120, the
    # Fighter's Damage build named Cinder Coat and still came away with
    # Fortifying Cry, because two shields on 564 Armor and an equal fit is an
    # alphabetical tie and `Shield_Craft` sorts first. A fallback that ties
    # with the thing it is a fallback for is not a fallback.
    usable = {}
    for iid, entry in _SHIELD_ROLE_FIT.get(role or "", {}).items():
        classes = (item(iid) or {}).get("classes") or []
        if not classes or not class_filter or class_filter in classes:
            usable[iid] = entry
    return usable or dict(_SHIELD_CLASS_FIT.get(class_filter or "", {}))

# The three things a build is FOR. A stat sheet cannot tell them apart — in
# this game healing, blocking and threat are SKILLS, not stats, so
# `_build_score` counts them at zero and a pure stat sum ranks the two best
# HEALING Cleric weapons dead last of fourteen (Flame of Argol's "healing
# health every 2s", Rehearsal Scepter's "Radiance: Heals nearby allies"),
# while handing the slot to a staff that won by 2 points. So the role is
# stated, not inferred, and the fit is read off the sheets' own skill text.
LOADOUT_ROLES = ("tank", "healer", "damage")
_ROLE_LABELS = {"tank": "Tank", "healer": "Healer", "damage": "Damage"}

# Which roles a class can actually PLAY, user-confirmed against the live
# game (2026-09-28): a Warrior or a Rogue cannot heal, a mage can; a mage or
# a Rogue cannot tank, only a Warrior can. Every class damages.
#
# This is game knowledge, NOT something the sheets can supply, and the
# catalog actively disagrees: 4 Fighter and 2 Assassin weapons carry healing
# text (GA_Demon's "heal yourself for 50% of the damage dealt", Sword_Swarm's
# "Hive Bite ... healing you and all nearby allies", Crescent's "Bloom: all
# damage you deal heals allies"). Those are self-heals and damage-to-heal
# conversions riding a damage kit — incidental, not a healer — which is
# exactly why the role has to be stated per class instead of read off the
# weapon text. `weapon_role_fit` still scans the text; this table decides
# whether the role is even on offer.
_CLASS_ROLES = {
    "Fighter": ("tank", "damage"),
    "Assassin": ("damage",),
    "Cleric": ("healer", "damage"),
    "Wizard": ("healer", "damage"),
}


def class_roles(class_filter: str | None) -> tuple[str, ...]:
    """The roles a class can play. Every role, for an unknown class or none
    — an unrecognised class must not silently lose the choice."""
    return _CLASS_ROLES.get(class_filter or "", LOADOUT_ROLES)


def class_can_role(class_filter: str | None, role: str | None) -> bool:
    """True when `role` is a role this class can play. An empty role is
    always fine (it resolves to the default damage build)."""
    return not role or role in class_roles(class_filter)


def role_label(role: str) -> str:
    """A role's button caption ("tank" -> "Tank"). The role key itself."""
    return _ROLE_LABELS.get(role, role)

# Role signature phrases, each one a literal from the catalog's skill text.
# This is a phrase list, NOT a parser: a future patch that rewords a skill
# simply stops matching, and a weapon with no signal scores 0 — which can
# never make it a wrong pick, only an unranked one.
_ROLE_KIT_SIGNALS = {
    # Shield_Start/OrbitWater/Craft Block, Withstand (Staff_Censer,
    # Staff_Craft, Staff_SummonDemon), Parry (GA_Craft, GA_Demon, Thrown)
    "tank": ("blocks incoming attacks", "shield absorbing",
             "increase your armor", "amount of threat"),
    # Radiance (Scepter_Start), Flamie (Scepter_Flamie), Tidal Totem
    # (Halos_Totem), Righteous Resolve (Sword_Craft), Hive Bite
    # (Sword_Swarm), Bloom (Crescent_FlowerSpiral), Hidden Power
    # (Staff_Censer), Water Veil (Book_WaterOrbs)
    "healer": ("healing", "heals", "heal health", "heal yourself",
               "healing allies"),
    # the scaling words a damage build actually stacks
    "damage": ("critical", "fervor", "physical mastery", "magic mastery",
               "additional damage", "increased damage"),
}
# Per matched signal, and the ceiling. Sized against the ~2-point margins the
# stat sum leaves between real candidates, and under the 120 a defining party
# buff is worth, so a role fit decides the slot without erasing the score.
_ROLE_KIT_STEP = 40.0
_ROLE_KIT_CAP = 120.0
# What a ONE-HANDED main hand is worth when the build wants a shield: not a
# free win, so a real role fit can still outweigh it, but comfortably more
# than the stat noise that used to hand a 2H staff to a Cleric and lock the
# off-hand (and with it every shield) out of its own build.
_ONE_HANDED_PREFERENCE = 80.0


def weapon_primary_stat(item_id: str | None,
                        class_filter: str | None = None) -> str | None:
    """A weapon's primary scaling stat — Strength (Fighter), Dexterity
    (Assassin), Intellect (Wizard) or Faith (Cleric). None for non-weapons
    and items with no class aptitude. The loadout UI keeps armor slots
    aligned with the equipped weapon's stat.

    `class_filter` is the class the build is FOR, and it wins when that class
    can hold the weapon. A dual-class weapon's `classes` list is in the
    sheet's own order, which says nothing about the wielder: `Axe_Boomerang`
    lists ['Assassin', 'Fighter'] and every Fighter equipping it reads
    Dexterity here, so a Fighter build aligned its armor to Dexterity and
    settled for Rare Assassin halves while better Fighter pieces it could
    wear sat unscored (wizard + `Staff_Censer` read Faith the same way).
    Without `class_filter` the weapon's own first class is the answer, as
    before.
    """
    if not item_id:
        return None
    classes = (item(item_id) or {}).get("classes") or []
    if class_filter and class_filter in classes:
        ps = class_primary_stat(class_filter)
        if ps:
            return ps
    for cls in classes:
        ps = class_primary_stat(cls)
        if ps:
            return ps
    return None


def _display_rarity(item_id: str) -> str:
    """The rarity a piece displays at (Guild Merchant gear sells as Rare)."""
    from .sources import item_display_rarity
    return item_display_rarity(item_id) or ""


def _gear_stat_rows(item_id: str, level: int, upgrades: int = 0) -> list[dict]:
    """The stat lines a piece contributes: computed curves when it scales
    (the display rarity, `level` + `upgrades` ranks), else its authored
    affixes. Jewelry is read at its real source cap — zone jewelry only
    drops at its authored zone tier, so it never reads above that.

    ARMOR IS NOT CAPPED, and the asymmetry is deliberate but UNVERIFIED, so
    it is written down rather than left to look like an oversight. The
    loadout compares the whole field at the character's level ("Rare+
    dungeon armor at L25"), while zone JEWELRY is pinned to the tier it
    drops at. If dungeon armor is really fixed at its drop level too, then
    the cap belongs on armor as well: 9 of 28 armor picks change when it is
    extended there (a level-8 Epic head reads 188 Armor at L25 and 82 at its
    real level). Nothing in the sheets settles it — `power_ratio` and
    `ilevel` are per-item and do not say whether a dungeon piece follows the
    wearer — so the honest state is that this is a measurement waiting to be
    taken, not a known bug. Zone ARMOR is unaffected either way: every zone
    armor piece is Uncommon and the `_gear_candidates` gate already drops it.
    """
    it = item(item_id) or {}
    if not it:
        return []
    lvl = max(1, int(level))
    if (it.get("type") or "") in _JEWELRY_TYPES:
        from .sources import item_scale_max_level
        lvl = min(lvl, item_scale_max_level(item_id))
    rar = _display_rarity(item_id) or None
    gs = gear_stats(it, level=lvl + max(0, int(upgrades)), rarity=rar)
    if gs:
        return stat_rows(gs["stats"])
    return [{"label": s.get("n") or "?", "value": s.get("v", 0), "raw": None}
            for s in (it.get("stats") or [])
            if s.get("n") and s.get("v")]


def _item_stats(item_id: str, level: int, upgrades: int = 0,
                factor: float = 1.0) -> dict[str, float]:
    """Label -> value totals for one piece, scaled by `factor` (0.5 for the
    Arsenal weapon's half contribution)."""
    out: dict[str, float] = {}
    for r in _gear_stat_rows(item_id, level, upgrades):
        v = r.get("value") or 0
        if v:
            out[r["label"]] = out.get(r["label"], 0.0) + v * factor
    return out


def format_item_stats_summary(item_id: str, level: int = 25,
                              upgrades: int = 0) -> str:
    """Compact one-line stat summary for the loadout list rows, e.g.
    'Strength 12 · Vitality 8 · Armor 34'. Reads the piece at its display
    rarity, `level` plus `upgrades` ranks; authored-affix gear shows its
    fixed affixes. Empty string for unknown / statless items."""
    parts = [f"{r['label']} {stat_text(r['value'], r.get('raw'))}"
             for r in _gear_stat_rows(item_id, level, upgrades) if r.get("value")]
    return " · ".join(parts)


def loadout_stat_totals(weapon1_id: str | None = None,
                        weapon2_id: str | None = None,
                        arsenal_id: str | None = None,
                        slot_items: dict | None = None,
                        level: int = 25,
                        upgrades: dict | None = None) -> dict[str, float]:
    """Combined character stats for an equipped loadout.

    Both main weapons count at 100%; the Arsenal weapon contributes 50% of
    its stats (the game's off-set rule) exactly like the loadout UI labels.
    Every filled gear slot adds its own piece at `level`, and `upgrades`
    maps item id -> rank (each rank = +10 iLevel). Returns label -> total.
    """
    upg = upgrades or {}
    out: dict[str, float] = {}

    def add(iid: str | None, factor: float) -> None:
        if not iid:
            return
        for k, v in _item_stats(iid, level, upg.get(iid, 0), factor).items():
            out[k] = round(out.get(k, 0.0) + v, 2)

    add(weapon1_id, 1.0)
    add(weapon2_id, 1.0)
    add(arsenal_id, 0.5)
    for iid in (slot_items or {}).values():
        add(iid, 1.0)
    return out


# Per-profile weight OVERRIDES on top of the damage-shaped defaults in
# `_build_score`. A stat sum cannot tell the three roles apart, because the
# defaults are all damage: Armor 0.2 and Vitality 0.5 are near-token, and
# Critical 1.2 / penetration 1.0 lead. That is a defensible choice for a
# damage build and a wrong one for the other two, and the symptom was
# literal — a tank, a healer and a damage dealer were handed the SAME
# eleven pieces, because with every stat weighted for damage nothing in the
# sheet pulls toward the other two roles.
#
# Each override is the set of stats whose weight CHANGES; anything absent
# keeps `_build_score`'s default. Weights are stated, not derived, and the
# two non-damage profiles are read off what the sheets actually build on:
#
#   Fervor is the HEALER's rating. `Infusion_Manfish_Support` ("Chain
#   Heal"), `Priest_BlessingOfFervor`, `Mage_Talent_FerventWizard` and
#   `Priest_Talent_FerventDefender` all raise it and nothing else does for
#   a healing kit. Critical and the two penetrations are the DAMAGE
#   ratings (`Infusion_Nightling_DPS`, `Mage_Talent_ConcentratedPower`,
#   `Spear_Upgrade`/`Scepter_Upgrade`), so both non-damage roles discount
#   them rather than ignore them — a healer still crits off a proc.
#
#   Armor / Vitality / Health are the TANK's, and they are already the
#   whole survivability story. But armor, primary stat and Vitality all
#   scale off the SAME aptitude curve, so raising those three to parity
#   moves ZERO armor picks on its own — verified, and pinned by
#   `test_the_tank_profile_alone_moves_no_armor_pick`. What does move them
#   is demoting the ratings: `Shoulders_EKobold` (Crit 25) and
#   `Shoulders_EDemon` (Fervor 25) are the SAME 317 Armor, so the rating
#   is the only thing left to separate them, and 15 of 28 armor slots plus
#   all 16 jewelry slots do separate once the ratings are weighted.
#
# GEAR SLOTS ONLY, and that restriction is not cosmetic: no weapon in the
# catalog rolls ANY Armor (only the four shields do), and vitality on
# weapons runs 32-40 whatever the kit — LOWEST on the tank-kit weapons
# (avg 28 vs 39 for the healers). Applying a survivability profile to
# weapons would anti-tank the weapon pick. The role chips still steer the
# weapon slots, but through the kit fit, which is the only place the
# sheets actually record what a weapon is for.
_PROFILE_WEIGHTS = {
    "tank": {"Armor": 1.0, "Vitality": 1.0, "Health": 1.0,
             "Critical": 0.3, "Armor Penetration": 0.3,
             "Magic Penetration": 0.1},
    "healer": {"Armor": 0.4, "Vitality": 1.0, "Health": 1.0,
               "Fervor": 1.5, "Critical": 0.2,
               "Armor Penetration": 0.1, "Magic Penetration": 1.0},
}


def _build_score(stats: dict, primary: str | None = None,
                 profile: str | None = None) -> float:
    """Weighted build score for a set of stat totals (see the weights
    above): the primary stat leads, Critical is soft-capped, then Fervor /
    penetration, Vitality and Armor.    `profile` = a role key ("tank" / "healer"), swapping in the role
    weights from `_PROFILE_WEIGHTS` for every stat they name; a stat the
    role does not name keeps its default. The default profile is
    deliberately a damage one (Armor 0.2, Vitality 0.5, Critical 1.2,
    penetration 1.0), which is a CHOICE, not a measurement — and the wrong
    one for the other two roles, whose gear exists to stack Fervor or Armor
    instead of Critical.
    """
    over = _PROFILE_WEIGHTS.get(profile or "", {})
    score = 0.0
    for k, v in stats.items():
        if k in over:
            score += v * over[k]
        elif primary and k == primary:
            score += v * 1.5
        elif k in _PRIMARY_STAT_NAMES:
            score += v * 1.0
        elif k in ("Vitality", "Health"):
            score += v * 0.5
        elif k == "Critical":
            c = min(v, _CRIT_SOFT_CAP) \
                + max(0.0, v - _CRIT_SOFT_CAP) * _CRIT_CAP_FACTOR
            score += c * 1.2
        elif k == "Fervor":
            score += v * 1.1
        elif k in ("Armor Penetration", "Magic Penetration"):
            score += v * 1.0
        elif k == "Armor":
            score += v * 0.2
        else:
            score += v * 0.5
    return score


def _candidate_pool_ids(candidate_pool) -> set[str] | None:
    """The owned-collection id set when a pool is given (a dict, list or
    set of item ids), else None meaning the full catalog. An EMPTY pool is
    an empty collection — no candidates at all — not the full catalog."""
    if candidate_pool is None:
        return None
    if isinstance(candidate_pool, dict):
        return {str(k) for k in candidate_pool}
    return {str(i) for i in candidate_pool}


def _gear_candidates(candidate_pool=None, item_type: str | None = None,
                     category_name: str | None = None,
                     class_filter: str | None = None,
                     primary_stat: str | None = None) -> list[dict]:
    """Gear rows eligible for auto-pick: in the owned pool when one is
    given, gear only, Common and (outside jewelry) Uncommon junk skipped,
    class-restricted pieces filtered to `class_filter`, and non-jewelry
    pieces filtered to the armor that actually rolls `primary_stat`."""
    pool = _candidate_pool_ids(candidate_pool)
    out = []
    for it in items():
        iid = it.get("id") or ""
        if pool is not None and iid not in pool:
            continue
        if not is_gear(it):
            continue
        itype = it.get("type") or ""
        if item_type is not None and itype != item_type:
            continue
        if category_name is not None and category(itype) != category_name:
            continue
        rar = _display_rarity(iid) or it.get("rarity") or ""
        if rar == "Common":
            continue
        if itype not in _JEWELRY_TYPES and rar == "Uncommon":
            continue
        # NO `level` gate here: the dungeon sets carry no level field at all
        # (the Rare shared pieces AND their `_E<Faction>` Epic twins), so a
        # non-zero-level check dropped every one of them from BiS and from
        # the owned auto-fill. Whether a piece can be scored is decided below
        # by the stats it actually resolves at the requested level.
        classes = it.get("classes") or []
        if class_filter and classes and class_filter not in classes:
            continue
        if (primary_stat and itype not in _JEWELRY_TYPES
                and not matches_stat(iid, primary_stat)):
            continue
        out.append(it)
    return out


def _score_pick(candidate_pool, item_type, category_name, class_filter,
                primary_stat, level, upgrades,
                exclude=(), factor: float = 1.0,
                kit: dict | None = None,
                profile: str | None = None) -> tuple[str | None, dict]:
    """The highest-scoring candidate's id and its stat totals (None, {} when
    nothing qualifies), skipping `exclude` (already-equipped ids). Ties
    break toward higher rarity, then item id. `factor` reads the piece at
    the share it actually contributes (the Arsenal weapon's 0.5), so the
    Critical soft cap is applied to the value the build really gets.
    `kit` adds a per-item-id score for what a piece DOES that its stats
    cannot show (see `_SHIELD_KIT_FIT`); it is added to the build score
    before the tie-break, so it is a ranking term, not a tie-break one.
    `profile` is the build weighting (see `_PROFILE_WEIGHTS`)."""
    upg = upgrades or {}
    skip = {str(i) for i in (exclude or ())}
    best_id, best_key, best_stats = None, None, {}
    for it in _gear_candidates(candidate_pool, item_type, category_name,
                               class_filter, primary_stat):
        iid = it["id"]
        if iid in skip:
            continue
        stats = _item_stats(iid, level, upg.get(iid, 0), factor)
        if not stats:
            continue
        rar = _display_rarity(iid) or it.get("rarity") or ""
        score = (_build_score(stats, primary_stat, profile)
                 + (kit or {}).get(iid, 0.0))
        key = (-score, _RARITY_RANK.get(rar, 9), iid)
        if best_key is None or key < best_key:
            best_key, best_id, best_stats = key, iid, stats
    return best_id, best_stats


def optimize_loadout_build(weapon1_id: str | None = None,
                           weapon2_id: str | None = None,
                           arsenal_id: str | None = None,
                           class_filter: str | None = None,
                           candidate_pool=None,
                           level: int = 25,
                           upgrades: dict | None = None,
                           role: str | None = None) -> dict[str, str]:
    """Best-in-slot fill for the 11 gear slots — {slot label: item id}.

    Each slot takes the highest build score among its eligible candidates
    (Rare+ armor, any-rarity jewelry, class-aligned, primary-stat aligned
    with the equipped weapon), with ring uniqueness honoured so Ring 1 and
    Ring 2 never share a piece. Slots with no qualifying piece are omitted.

    `role` picks the build WEIGHTING, not the kit: "tank" raises the
    survivability stats and demotes the damage ratings, "healer" does the
    reverse and weights Fervor (the rating every healing infusion in the
    catalog builds on) — see `_PROFILE_WEIGHTS` for the numbers and the
    sheets they were read off. Both profiles move real picks: 15 of 28
    armor slots and all 16 jewelry slots separate from a damage build. The
    weapon slots are not scored here — a role reaches those through
    `suggest_main_weapon`'s kit fit, because no weapon rolls Armor at all
    (see `_PROFILE_WEIGHTS`).
    """
    primary = weapon_primary_stat(weapon1_id, class_filter) \
        or (class_primary_stat(class_filter) if class_filter else None)
    # Same guard the two weapon pickers carry, and for the same reason: a
    # role this class cannot PLAY resolves to the DEFAULT build, not to "no
    # role". Stale page state must never be able to build a tank for a mage.
    if role and not class_can_role(class_filter, role):
        role = "damage"
    pool = _candidate_pool_ids(candidate_pool)
    out: dict[str, str] = {}
    used: set[str] = set()
    for label, itype in _LOADOUT_SLOTS:
        pick, _stats = _score_pick(pool, itype, None, class_filter,
                                   primary, level, upgrades, exclude=used,
                                   profile=role)
        if pick:
            out[label] = pick
            used.add(pick)
    return out


def weapon_role_fit(item_id: str, role: str | None) -> float:
    """How well a weapon's KIT serves a role, read off the sheets' own skill
    text (`_ROLE_KIT_SIGNALS`): 40 per distinct signature phrase, capped at
    120. 0 for an unknown role, a non-weapon, or a weapon whose text carries
    no signal — an unranked piece, never a wrong one.

    This is the term a stat sheet cannot supply. Healing, blocking and threat
    are skills here, so the build score counts them at zero: without this,
    the two most healing Cleric weapons score LAST of fourteen.
    """
    if not item_id or role not in _ROLE_KIT_SIGNALS:
        return 0.0
    hits = len(_weapon_role_hits(item_id, role))
    return min(hits * _ROLE_KIT_STEP, _ROLE_KIT_CAP)


def _weapon_role_hits(item_id: str, role: str | None
                      ) -> tuple[tuple[str, str], ...]:
    """The signature phrases in a weapon's own skill text that serve `role`,
    each paired with the NAME of the skill it was found in, in
    `_ROLE_KIT_SIGNALS` order. The one place the match is made, so the number
    `weapon_role_fit` scores and the reasons `weapon_role_reasons` shows can
    never disagree about which phrases fired.

    The skill name is not decoration. A weapon's reasons span all of its
    skills, so a bare phrase can name a sentence the card never shows —
    Cleric healer "Ghost Clams of the Low Tide" attributes the pick to
    "Healing", which lives in Tidal Totem, while the card's own trait line
    is Power of the Tides. Attributing each phrase to its skill is what lets
    a reader check the claim instead of taking it on faith.
    """
    if not item_id or role not in _ROLE_KIT_SIGNALS:
        return ()
    from .catalog import weapon_skills
    try:
        skills = weapon_skills(item_id)
    except Exception:
        return ()
    if not skills:
        return ()
    hits: list[tuple[str, str]] = []
    for sig in _ROLE_KIT_SIGNALS[role]:
        for s in skills:
            blob = f"{s.get('name') or ''} {(s.get('description') or '')}".lower()
            if sig in blob:
                hits.append((s.get("name") or "", sig))
                break
    return tuple(hits)


def weapon_role_reasons(item_id: str, role: str | None) -> tuple[str, ...]:
    """WHY this weapon suits a role, as display-ready strings grouped by the
    skill each phrase came from: `"Tidal Totem: Healing, Healing allies"`.

    Each phrase is a verbatim slice of a skill description rather than a
    paraphrase, and each is attributed to its skill (`_weapon_role_hits`),
    so a reader can find the sentence rather than take the claim on faith.
    Grouped rather than one label per phrase because a single skill routinely
    carries two — a healer wants "healing" AND "heals nearby allies" out of
    the same ability, and repeating its name for both wastes the line.

    Empty for a weapon with no signal, which is the same unranked-not-wrong
    answer `weapon_role_fit` gives as 0 — a recommendation that cannot
    explain itself should say nothing rather than invent a reason.
    """
    grouped: dict[str, list[str]] = {}
    order: list[str] = []
    for skill, sig in _weapon_role_hits(item_id, role):
        key = skill or "Kit"
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        pretty = sig[:1].upper() + sig[1:]
        if pretty not in grouped[key]:
            grouped[key].append(pretty)
    return tuple(f"{k}: {', '.join(grouped[k])}" for k in order)


def suggest_main_weapon(exclude_ids=(), class_filter: str | None = None,
                        candidate_pool=None, level: int = 25,
                        upgrades: dict | None = None,
                        role: str | None = None,
                        prefer_one_handed: bool = False) -> str | None:
    """The best main-hand weapon for a class: the highest build score among
    weapons the class can wield, excluding `exclude_ids` (the other hand).
    None when nothing qualifies (e.g. an empty owned collection).

    `role` ("tank" / "healer" / "damage") adds `weapon_role_fit`, because the
    stat score cannot see what a weapon DOES — pass None and the kit is
    invisible and the pick falls back to a stat sum that ranks the best
    healing weapons LAST. Callers with a role concept should RESOLVE one
    (the loadout page defaults an unselected role to "damage", matching the
    damage-shaped default weights) rather than pass None. `prefer_one_handed`
    is the
    off-hand's claim on this slot: a 2H main hand locks the off-hand, so when
    the build wants a shield and the scores are otherwise close, a one-hander
    wins — for a Cleric that is the difference between a build that can hold
    a shield and one structurally locked out of every one of them.
    """
    excluded = {str(i) for i in (exclude_ids or ())}
    pool = _candidate_pool_ids(candidate_pool)
    primary = class_primary_stat(class_filter) if class_filter else None
    kit: dict[str, float] = {}
    # a role the class cannot play resolves to the DEFAULT build, not to "no
    # role": stale page state must never be able to build a tank for a mage,
    # and falling back to the bare stat sum instead would quietly hand the
    # mage a different build from the one an untouched page gives them
    if role and not class_can_role(class_filter, role):
        role = "damage"
    for it in _gear_candidates(pool, None, "Weapons", class_filter, primary):
        iid = it["id"]
        fit = weapon_role_fit(iid, role)
        if prefer_one_handed and not is_two_handed(iid):
            fit += _ONE_HANDED_PREFERENCE
        if fit:
            kit[iid] = fit
    pick, _stats = _score_pick(pool, None, "Weapons", class_filter, primary,
                               level, upgrades, exclude=excluded,
                               kit=kit or None)
    return pick


def suggest_offhand_shield(weapon1_id: str | None = None,
                           class_filter: str | None = None,
                           candidate_pool=None, level: int = 25,
                           role: str | None = None) -> str | None:
    """The best SHIELD for the off-hand, or None when the class has none.

    Separate from `suggest_main_weapon` because the off-hand is not a second
    main hand: it takes shields only, and — the part that matters — it is
    scored WITHOUT the primary-stat gate. That gate keeps a weapon that
    cannot roll the build's scaling stat out of the auto-fill, and every
    shield is Armor-only, so the gate rejected all of them and the off-hand
    auto-filled empty for a class that can plainly use one. A shield
    contributes Armor, which `_build_score` already weights (0.2); it has
    no scaling stat to roll, so there is nothing for the gate to protect.

    Armor ALONE cannot rank shields: all four read an identical 564 at their
    caps. So the pick also carries `_shield_kit_fit` — the role's own shield
    where the class can wield it, the class fallback where it cannot. A
    Fighter on Tank gets Fortifying Cry, the same Fighter on Damage gets
    Cinder Coat, and a Cleric on Damage gets Depth Shield because the damage
    shield is not theirs to hold. That is what the two identically-classed
    Cleric/Fighter shields actually are FOR, which armor cannot express.

    EVERY candidate is read at its OWN ladder cap, and the caller's
    `upgrades` dict is deliberately NOT consulted. That dict is per-item page
    state which outlives a class switch, so reading it compared the equipped
    shield (maxed, 564 armor) against candidates still at +0 (487) and let
    the stale one win: a Fighter's upgraded Dominion handed a CLERIC the
    tank's shield. Since a pick always maxes the piece, comparing at the cap
    is both the honest comparison and the only class-independent one.

    `weapon1_id` is accepted for symmetry with the other suggest_* calls and
    is deliberately unused: unlike a second main hand, a shield never
    competes with what is already in the main hand for the primary stat.
    """
    del weapon1_id
    pool = _candidate_pool_ids(candidate_pool)
    kit = {iid: fit for iid, (fit, _why) in
           _shield_kit_fit(class_filter, role).items()}
    pick, _stats = _score_pick(pool, "Shield", "Weapons", class_filter, None,
                               level, _every_shield_maxed(), kit=kit)
    return pick


@lru_cache(maxsize=1)
def _every_shield_maxed() -> dict:
    """Every catalog shield at its own display-rarity cap, the state a pick
    lands in. Shared by the shield pick and the card's ranking so the two can
    never read the same shield at two different ranks."""
    out: dict[str, int] = {}
    for it in items():
        iid = it.get("id") or ""
        if it.get("type") == "Shield" and iid:
            out[iid] = upgrade_cap(_display_rarity(iid) or
                                   it.get("rarity") or "")
    return out


def rank_offhand_shields(class_filter: str | None = None,
                         candidate_pool=None, level: int = 25,
                         limit: int = 3,
                         role: str | None = None) -> list[dict]:
    """The off-hand shields RANKED, best first, so the card can show what
    beat what. Same scoring as `suggest_offhand_shield` (armor + the
    `_shield_kit_fit` role-then-class fit, same tie-break), but the whole
    field comes back instead of one id, and `role` has to be passed here too
    or the card ranks for a different build than the one it sits on.

    Each entry: {id, name, score, armor, fit, why, skills}. `why` is the
    annotation that decided the ranking for this class, or "" when the
    ranking came down to armor alone. `skills` is the shield's own kit
    (Block plus whatever it actually does), because with four shields at an
    identical 564 Armor the kit IS the answer and the numbers are not.

    Every candidate is read at its own cap (`_every_shield_maxed`) for the
    reason given in `suggest_offhand_shield`: a side-by-side that reads the
    equipped piece maxed and the rest at +0 is not a side-by-side.
    """
    pool = _candidate_pool_ids(candidate_pool)
    upg = _every_shield_maxed()
    kit: dict[str, float] = {}
    why: dict[str, str] = {}
    for iid, (fit, note) in _shield_kit_fit(class_filter, role).items():
        kit[iid] = fit
        why[iid] = note
    rows = []
    for it in _gear_candidates(pool, "Shield", "Weapons", class_filter, None):
        iid = it["id"]
        stats = _item_stats(iid, level, upg.get(iid, 0), 1.0)
        if not stats:
            continue
        rar = _display_rarity(iid) or it.get("rarity") or ""
        score = _build_score(stats, None) + kit.get(iid, 0.0)
        rows.append({
            "key": (-score, _RARITY_RANK.get(rar, 9), iid),
            "id": iid,
            "name": it.get("name") or iid,
            "score": round(score, 1),
            "armor": stats.get("Armor", 0.0),
            "fit": kit.get(iid, 0.0),
            "why": why.get(iid, ""),
        })
    rows.sort(key=lambda r: r["key"])
    from .catalog import weapon_skills
    out = []
    for row in rows[:max(1, int(limit))]:
        row.pop("key", None)
        try:
            row["skills"] = [{"name": s.get("name") or "",
                              "type": s.get("type") or "",
                              "description": (s.get("description") or "")
                              .strip().splitlines()[0]
                              if (s.get("description") or "").strip() else ""}
                             for s in weapon_skills(row["id"])
                             if s.get("type") != "Base Attack"]
        except Exception:
            row["skills"] = []
        out.append(row)
    return out


def suggest_arsenal_weapon(weapon1_id: str | None = None,
                           weapon2_id: str | None = None,
                           slot_items: dict | None = None,
                           class_filter: str | None = None,
                           candidate_pool=None, level: int = 25,
                           upgrades: dict | None = None,
                           role: str | None = None) -> str | None:
    """The best Arsenal weapon — the highest build score among weapons the
    class can wield that aren't already equipped, scored at its 50%
    contribution so it stays a secondary pick. None when nothing qualifies.

    `role` carries the same kit term as the main hand (`weapon_role_fit`): the
    Arsenal is a third of the build's weapons, so a healer whose Arsenal is a
    two-hander that cannot heal is as wrong as a bad main hand, and this slot
    is otherwise as blind to the kit as the main hand would be without a
    role. No 1H preference here — the off-hand is not this slot's claim.
    """
    excluded = {w for w in (weapon1_id, weapon2_id) if w}
    pool = _candidate_pool_ids(candidate_pool)
    primary = (weapon_primary_stat(weapon1_id, class_filter)
               if weapon1_id else
               (class_primary_stat(class_filter) if class_filter else None))
    kit: dict[str, float] = {}
    if role and not class_can_role(class_filter, role):
        role = "damage"
    for it in _gear_candidates(pool, None, "Weapons", class_filter, primary):
        fit = weapon_role_fit(it["id"], role)
        if fit:
            kit[it["id"]] = fit
    pick, _stats = _score_pick(pool, None, "Weapons", class_filter, primary,
                               level, upgrades, exclude=excluded, factor=0.5,
                               kit=kit or None)
    return pick


def suggest_augment_plan(weapon1_id: str | None = None,
                         weapon2_id: str | None = None,
                         arsenal_id: str | None = None,
                         slot_items: dict | None = None,
                         class_filter: str | None = None,
                         candidate_pool=None,
                         level: int = 25,
                         upgrades: dict | None = None) -> dict:
    """An augment plan for an equipped loadout.

    Assumes ONE socket per piece: for every equipped weapon / gear slot it
    picks the single gem augment (Jeweller stones, Enchants tab data) that
    raises the build score the most, and proposes a DemonGear conversion on
    the first rating the piece actually rolls. Returns
    {slots: {label: {name, item, grants: [(stat, value)]}},
     conversions: [{slot, source, target, value}],
     totals_with_plan: {stat: total}} — the arsenal's grants weigh 50%.
    """
    upg = upgrades or {}
    primary = weapon_primary_stat(weapon1_id, class_filter) \
        or (class_primary_stat(class_filter) if class_filter else None)
    pieces: list[tuple[str, str, float]] = []
    if weapon1_id:
        pieces.append(("W1", weapon1_id, 1.0))
    if weapon2_id:
        pieces.append(("W2", weapon2_id, 1.0))
    if arsenal_id:
        pieces.append(("Arsenal", arsenal_id, 0.5))
    for label, iid in (slot_items or {}).items():
        if iid:
            pieces.append((label, iid, 1.0))

    conv_by_source: dict[str, list[dict]] = {}
    for c in enchant_conversions():
        conv_by_source.setdefault(c["source"], []).append(c)

    slots: dict[str, dict] = {}
    conversions: list[dict] = []
    with_plan = loadout_stat_totals(weapon1_id, weapon2_id, arsenal_id,
                                    slot_items, level=level, upgrades=upg)
    for label, iid, factor in pieces:
        base = _item_stats(iid, level, upg.get(iid, 0))
        if not base:
            continue
        best_gem, best_delta = None, 0.0
        for gem in gem_augments():
            boosted = dict(base)
            for k, v in gem["stats"].items():
                boosted[k] = boosted.get(k, 0.0) + v * factor
            delta = _build_score(boosted, primary) - _build_score(base, primary)
            if delta > best_delta + 1e-9:
                best_gem, best_delta = gem, delta
        if best_gem:
            name = (item(best_gem["item"]) or {}).get("name") or best_gem["item"]
            grants = [(k, v) for k, v in best_gem["stats"].items()]
            slots[label] = {"name": name, "item": best_gem["item"],
                            "grants": grants}
            for k, v in grants:
                with_plan[k] = round(with_plan.get(k, 0.0) + v * factor, 2)
        ratings = gear_ratings(iid) or ()
        for src in ratings:
            convs = conv_by_source.get(src) or []
            if not convs:
                continue
            pick = convs[0]
            conversions.append({
                "slot": label, "source": pick["source"],
                "target": pick["target"],
                "value": (pick.get("rare") or {}).get("value", 20)})
            break
    return {"slots": slots, "conversions": conversions,
            "totals_with_plan": with_plan}
