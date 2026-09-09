# -*- coding: utf-8 -*-
# GENERATED FILE - DO NOT EDIT.
#
# The app's copy of the shared game rules, bundled from `game_rules.py` at the
# repo root by `compiler.py` on every build. The rules live OUTSIDE the app
# package so the build can load them without that package being importable (the
# build is what generates it), and the app keeps importing them here.
#
# Edit `game_rules.py` and rebuild: an edit to THIS file is reverted by the next
# `python compiler.py`, and `tests/test_compiler.py` fails while the two differ.

"""Game rules shared by the build and the app — a STDLIB-ONLY leaf, OUTSIDE the app package.

This file lives at the repo root, beside `compiler.py`, and that placement is the
point: `compiler.py` is the thing that GENERATES the app's data modules, so it
must be able to load the rules it needs without the app package being importable
at all. `farever_companion/rules.py` is a *generated copy* of this file, bundled
by `compiler.py` on every build, which is how the app keeps importing the rules
as `from ..rules import ...` with no call site moved.

Which file to edit: THIS one. `farever_companion/rules.py` carries a "GENERATED —
DO NOT EDIT" banner, and `tests/test_compiler.py` fails while the two differ, so
an edit to the copy is loud rather than silently reverted by the next build.

`compiler.py` used to re-express a handful of game rules in its own words while
`farever_companion/` spelled them again (see the 2026-10-05
`ai/workspace/buffy/compiler-runtime-rule-inventory.md`), so the two could drift
with nothing to catch it. The rules that need no data access — a bitmask, a
regex, a ladder, a label map — live here once, and every current home in the app
re-exports them, so no call site moves.

THE ONE RULE OF THIS FILE: import nothing but the standard library. Importing any
`farever_companion` module here (or anything that pulls the `raw_data` shims,
`cdb`, `paths`, Qt) defeats the point — and is caught by `tests/test_rules.py`.

Deliberately NOT here (see the inventory's section 2): the divergent copies a
unification would have to *decide*, not just move — the Items page's
`(?i)_shop` id predicate (`catalog._SHOP_ID_RE`, a different question from the
premium-id pattern), and the region display names (`geo/orbs` says Z3 is
"Ramburg", the compiler's zone-row fallback says "Crimson Island" — different
tables with different keys). Reconciling either changes behaviour, so both are
left where they are until a measurement picks a winner.
"""
from __future__ import annotations

import re

# --- unit flags -------------------------------------------------------------
# Measured against the shipped `unit.json` (530 rows, 2026-10-05) by correlating
# each bit with what the sheet can show — boss-ness, instance-only ids, the
# `Spark` naming. Correlation, NOT observation: the medium/low entries are
# honest readings, not proofs.
#
#   0x10      BOSS_FLAG_BIT         19 rows, all named bosses/minibosses
#                                   (Nepsilon, Reblochonk, Gatsbee,
#                                   Crabgantua, MunsterChuck, Phrixes, the
#                                   DemonSuperElite family). High confidence.
#   0x40000   NO_CODEX_FLAG_BIT     29 rows: `Dummy`, the `_Z*D_` internal
#                                   variants, `ChaoticPortal`. The compiler
#                                   drops these rows. High confidence.
#   0x400000  SPARK_FLAG_BIT        36 rows; 16 of the 17 ids containing
#                                   "Spark". The only bit that tracks Spark
#                                   directly. No production path reads it yet —
#                                   the compiler still uses SPARK_PROXY.
#   0x40      SPARK_PROXY_FLAG_BIT  64 rows. The compiler's stand-in for "drops
#                                   Spark". Overlaps the real Spark bit without
#                                   matching it: against 0x400000 it
#                                   over-selects 42 rows and misses 18 (the
#                                   `Frog_Spark` companions, because the name
#                                   test looks for "sparkling"). Medium.
#   0x80      UNIQUE_FLAG_BIT       137 rows — a broad SUPERset of 0x40 (57 of
#                                   the 64 also carry 0x80). Holds the `_U` /
#                                   `RamPatrol` uniques but also `_FS` / `_Z*D_`
#                                   instance spawns, so "unique" is the
#                                   runtime's reading, not a tight name. The
#                                   compiler ALSO reuses this bit as an
#                                   instance-only fallback — one bit, two jobs,
#                                   recorded here rather than silently split.
#                                   Low/medium: do not tighten without the game.
#   0x90      SPECIAL_FLAG_BIT      BOSS_FLAG_BIT | UNIQUE_FLAG_BIT: the mask
#                                   the compiler keeps a Totem/Environment/
#                                   Trigger/Marker row for.
BOSS_FLAG_BIT = 0x10
SPARK_PROXY_FLAG_BIT = 0x40
UNIQUE_FLAG_BIT = 0x80
SPECIAL_FLAG_BIT = 0x90
NO_CODEX_FLAG_BIT = 0x40000
SPARK_FLAG_BIT = 0x400000


def _has(flags, bit: int) -> bool:
    return isinstance(flags, int) and bool(flags & bit)


def is_no_codex(flags) -> bool:
    return _has(flags, NO_CODEX_FLAG_BIT)


def is_boss_flag(flags) -> bool:
    return _has(flags, BOSS_FLAG_BIT)


def is_unique_flag(flags) -> bool:
    return _has(flags, UNIQUE_FLAG_BIT)


def is_special_flag(flags) -> bool:
    return _has(flags, SPECIAL_FLAG_BIT)


# --- premium / shop ids -----------------------------------------------------
# The id pattern the Items/Codex premium half applies, on top of the drops
# index's vendor-derived shop set (`sources.shop_item_ids`). The compiler and
# `data/codex.py` carried identical copies; `catalog._SHOP_ID_RE` is a DIFFERENT
# predicate (`(?i)_shop`) and is intentionally not folded in here.
PREMIUM_SHOP_ID_RE = re.compile(r"(?i)(_ea_|earlyaccess|^spark)")


def is_premium_shop_id(item_id: str) -> bool:
    return bool(PREMIUM_SHOP_ID_RE.search(item_id or ""))


# --- codex kinds ------------------------------------------------------------
# `unreleased` is the codex's "no source known" flag; the runtime has always
# treated `todo` as the same thing (`codex._NO_LOC_KINDS`), while the compiler's
# obtainable test only checked `unreleased` — a latent divergence that is zero
# rows wide on today's data (0 `todo`, 46 `unreleased`), so tying them together
# changes no output.
UNRELEASED_KINDS = ("unreleased", "todo")
NO_LOC_KINDS = ("todo", "unreleased", "achievement")


def is_unreleased_kind(kind) -> bool:
    return (kind or "").lower() in UNRELEASED_KINDS


# --- guild merchants --------------------------------------------------------
GUILD_MERCHANT_RE = re.compile(r"^WanderingMerchant", re.IGNORECASE)


def is_guild_merchant_source(source_id) -> bool:
    return bool(GUILD_MERCHANT_RE.match(source_id or ""))


# --- rarity -----------------------------------------------------------------
#: The game's rarity ladder, common -> rarest (the order of `rarity.json`).
RARITY_ORDER = ("Common", "Uncommon", "Rare", "Epic", "Legendary")
#: Ascending rank, name -> index (lower rarity = lower number).
RARITY_RANK = {r: i for i, r in enumerate(RARITY_ORDER)}
#: The same ranks keyed by the lowercase spellings the item sheet uses.
RARITY_RANK_LOWER = {r.lower(): i for i, r in enumerate(RARITY_ORDER)}
#: Descending rank for "higher wins" tie-breaks (Legendary 0 ... Common 4).
RARITY_RANK_DESC = {r: i for i, r in enumerate(reversed(RARITY_ORDER))}
#: The bands a worn piece can be raised through, cheapest first — the subset of
#: the ladder a gear upgrade can reach, NOT the whole ladder.
UPGRADABLE_RARITIES = ("Rare", "Epic", "Legendary")

# --- rating attribute labels ------------------------------------------------
# The item sheet's rating attributes -> the labels the app speaks. The compiler
# stamps these onto item rows; `stats._STAT_LABELS` is a broader map (it also
# carries non-rating stats) and is left to grow separately.
RATING_ATTR_LABELS = {
    "CritChanceRating": "Critical",
    "FervorRating": "Fervor",
    "ArmorPenetrationRating": "Armor Penetration",
    "SpellPenetrationRating": "Magic Penetration",
}

# --- gear types -------------------------------------------------------------
# The item types that count as GEAR (weapons + armor + jewelry). `labels` used
# to own this table; it lives here so the payload-pure crafted-item rule below
# stays stdlib-only and the compiler can reach it without the app's data layer.
GEAR_TYPES = frozenset({
    "GreatAxe", "Daggers", "Halos", "Staff", "DualAxes", "Scepter", "Axe",
    "Crescent", "Spear", "Shield", "Fists", "DualMaces", "Thrown", "Sword",
    "Book", "Bow", "Mace", "GreatMace", "DualSwords", "GreatSword",
    "Cloth", "Leather", "Back", "Feet", "Head", "Legs", "Chest", "Hands",
    "Waist", "Shoulders",
    "GearTrinket", "GearFinger", "GearNeck",
})


def is_gear(item_row: dict) -> bool:
    return (item_row.get("type") or "") in GEAR_TYPES


# --- payload-pure item derivations ------------------------------------------
# These read a raw `item_drops` / craft payload instead of the live model, so
# BOTH the compiler (on the JSON it just loaded) and the runtime (on the shims)
# derive the same sets from the same single implementation. The compiler used to
# import `data.items.sources` to reach these; they live here now, and `sources`
# re-exports the two public names so no call site moved.
def recipe_output_ids_from_payload(rows) -> frozenset:
    """Ids any recipe produces, from a craft sheet's row list."""
    return frozenset(r.get("item") for r in (rows or []) if r.get("item"))


def payload_has_guild_merchant(srcs: list, it: dict) -> bool:
    """True when any of the row's drop sources is a Guild Merchant (a
    WanderingMerchant NPC) — the payload-pure twin of `has_guild_merchant`."""
    for dr in it.get("drops") or []:
        si = dr.get("s", -1)
        s = srcs[si] if isinstance(si, int) and 0 <= si < len(srcs) else {}
        if GUILD_MERCHANT_RE.match(s.get("id") or ""):
            return True
    return False


def row_is_crafted(srcs: list, iid: str, it: dict,
                   recipe_outputs=frozenset()) -> bool:
    """The crafted-gear verdict for one item row, read purely from the drops
    payload (plus the recipe output ids)."""
    lvl = it.get("level")
    if not isinstance(lvl, int) or lvl <= 1:
        return False
    crafted = ("_Craft" in iid or "RCraft" in iid) \
        or (iid in recipe_outputs and is_gear({"id": iid, **it}))
    if not crafted:
        return False
    return not payload_has_guild_merchant(srcs, it)


def crafted_item_ids(data: dict, recipe_outputs=frozenset()) -> frozenset:
    """Every item id `row_is_crafted` flags, from an item_drops payload."""
    srcs = data.get("_sources") or []
    out = set()
    for iid, it in (data.get("items") or {}).items():
        if row_is_crafted(srcs, iid, it, recipe_outputs):
            out.add(iid)
    return frozenset(out)


def shop_item_ids_from_payload(data: dict,
                               crafted=frozenset()) -> frozenset:
    """Shop-ONLY item ids, derived from a raw item_drops payload.

    One resolved row per (source, location) — the same grouping `resolve_drops`
    uses, keeping the FIRST row's kind/table — then: at least one npc (vendor)
    row, crafted gear keeps only its npc rows, the training-dummy rows are
    dropped, and EVERY remaining row must be an `npc`/`Shop` offer.
    """
    srcs = data.get("_sources") or []
    tbls = data.get("_tables") or []
    out = set()
    for iid, it in (data.get("items") or {}).items():
        raw = it.get("drops") or []
        if not raw:
            continue
        has_npc = False
        groups: dict[tuple[str, int], dict] = {}
        for dr in raw:
            si = dr.get("s", -1)
            s = srcs[si] if isinstance(si, int) and 0 <= si < len(srcs) else {}
            kind = s.get("kind", "")
            if kind == "npc":
                has_npc = True
            ti = dr.get("t", -1)
            table = tbls[ti] if isinstance(ti, int) and 0 <= ti < len(tbls) else ""
            sid = s.get("id", "?")
            li = dr.get("l")
            groups.setdefault((sid, li if li is not None else -1),
                              {"kind": kind, "table": table, "sid": sid})
        if not has_npc:
            continue
        rows = list(groups.values())
        if iid in crafted:
            rows = [r for r in rows if r["kind"] == "npc"]
        rows = [r for r in rows
                if not (r["kind"] == "unit"
                        and (r["sid"] or "").startswith("PunchingBag"))]
        if rows and all(r["kind"] == "npc" and r["table"] == "Shop"
                        for r in rows):
            out.add(iid)
    return frozenset(out)


def heroic_boss_items(loot_rows) -> dict[str, tuple[str, ...]]:
    """`<BossId>_HM` loot table id -> the items that table rolls.

    Each heroic (Hardmode) boss guarantees a fixed block of its family's Epic
    set on the loot table NAMED AFTER IT (`Cleodora_HM`, `RobinHoof_HM`, ...);
    twelve of the fourteen bosses have one. The `_HM` convention and the item
    list are the sheet's, and this is the ONE place both the build and the app
    read them: the compiler stamps each item's boss edge onto the compiled
    drops index from here, and the runtime's Dungeons page reads the same
    tables. Empty when no row carries an `_HM` table.
    """
    out: dict[str, tuple[str, ...]] = {}
    for r in loot_rows or ():
        tid = r.get("id") or ""
        if not tid.endswith("_HM"):
            continue
        items = tuple(e.get("item") for e in (r.get("loot") or [])
                      if e.get("item"))
        if items:
            out[tid] = items
    return out
