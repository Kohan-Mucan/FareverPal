"""The shared game rules have ONE canonical home, outside the app package.

`game_rules.py` at the repo root is what `compiler.py` loads — it has to live
outside the package, because the compiler is what GENERATES that package and so
must run with it unimportable. The app reads a generated copy of the same file,
bundled into `farever_companion/rules.py` by `compiler._bundle_shared_rules`.

This module pins the half that lives here: the canonical leaf stays stdlib-only.
`tests/test_compiler.py` pins the bundle (the copy is the canonical source plus
its banner, byte for byte) and that no compiler path imports the app tree. And
every rule it holds was MOVED out of a current app home that still re-exports the
name, so no call site moved — the identity checks below keep those two spellings
from drifting apart again.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

from farever_companion import rules

ROOT = Path(__file__).resolve().parents[1]
#: The canonical leaf — the file the build loads, at the repo root.
RULES_PY = ROOT / "game_rules.py"
#: The app's generated copy of it (compiler.py writes this from the above).
BUNDLED_PY = ROOT / "farever_companion" / "rules.py"


def test_rules_imports_only_the_standard_library():
    """This is the file the build loads with the app package unimportable;
    dragging the app (shims, cdb, paths, Qt) in would defeat the point."""
    assert BUNDLED_PY.exists(), (
        "the app's generated rules copy is missing — run `python compiler.py`")
    tree = ast.parse(RULES_PY.read_text(encoding="utf-8"))
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, (
                f"rules.py uses a relative import (level {node.level}) — it must "
                "not reach into farever_companion")
            if node.module:
                mods.add(node.module.split(".")[0])
    assert mods, "rules.py imports nothing — did the parse read the wrong file?"
    non_stdlib = mods - set(sys.stdlib_module_names)
    assert not non_stdlib, f"rules.py imports non-stdlib modules: {sorted(non_stdlib)}"


def test_rarity_homes_re_export_the_shared_tables():
    """`catalog`/`stats`/the Items-page support module keep their public names
    but resolve the SAME objects the shared module holds."""
    from farever_companion.data.items import catalog, stats

    assert catalog.RARITY_ORDER is rules.RARITY_ORDER
    assert stats._RARITY_ORDER is rules.UPGRADABLE_RARITIES
    assert stats._RANK is rules.RARITY_RANK_LOWER
    assert stats._RARITY_RANK is rules.RARITY_RANK_DESC

    # the descending map really is the ladder reversed, not a second copy
    assert rules.RARITY_RANK_DESC == {r: i for i, r in
                                      enumerate(reversed(rules.RARITY_ORDER))}
    assert rules.RARITY_RANK_LOWER == {r.lower(): i for i, r
                                       in enumerate(rules.RARITY_ORDER)}


def test_item_page_support_re_uses_the_ascending_rarity_rank():
    from farever_companion.ui.pages.items import support

    assert support._RARITY_RANK is rules.RARITY_RANK


def test_codex_and_sources_re_export_the_shared_predicates():
    from farever_companion.data import codex
    from farever_companion.data.items import sources

    assert codex._SHOP_ID_RE is rules.PREMIUM_SHOP_ID_RE
    assert codex._NO_LOC_KINDS is rules.NO_LOC_KINDS
    assert sources._GUILD_MERCHANT_RE is rules.GUILD_MERCHANT_RE


def test_flag_homes_re_export_the_shared_bits():
    from farever_companion import unit_flags
    from farever_companion.data import units

    assert units.BOSS_FLAG_BIT == rules.BOSS_FLAG_BIT
    assert units.UNIQUE_FLAG_BIT == rules.UNIQUE_FLAG_BIT
    assert unit_flags.NO_CODEX_FLAG_BIT == rules.NO_CODEX_FLAG_BIT
    assert unit_flags.SPARK_PROXY_FLAG_BIT == rules.SPARK_PROXY_FLAG_BIT


def test_the_payload_derivations_are_shared_not_copied():
    """The shop / crafted / gear derivations have ONE implementation, and the
    runtime homes re-export it rather than keeping a copy."""
    from farever_companion.data.items import labels, sources

    assert sources.shop_item_ids_from_payload is rules.shop_item_ids_from_payload
    assert sources.crafted_item_ids is rules.crafted_item_ids
    assert labels.is_gear is rules.is_gear
    assert labels._GEAR_TYPES is rules.GEAR_TYPES

    # the runtime wrapper is exactly the shared function over the live payload,
    # which is the whole point: one rule, two callers
    assert sources.shop_item_ids() == rules.shop_item_ids_from_payload(
        sources._data(), sources._crafted_ids())


def test_the_heroic_table_derivation_is_shared_not_copied():
    """The `<boss>_HM` read has ONE implementation: the Dungeons page's
    `_heroic_tables` is exactly `rules.heroic_boss_items` over the live
    lootTable, which is the same call the compiler makes to stamp the edge
    onto the compiled drops index."""
    from farever_companion.data import cdb
    from farever_companion.data.items import sources

    assert sources._heroic_tables() == rules.heroic_boss_items(
        cdb.lines("lootTable"))


def test_the_build_does_not_import_the_app_data_layer():
    """The compiler reaches the shared rules through the stdlib-only leaf,
    never through `farever_companion.data.*` — the layer whose shims it builds.
    That bootstrap-order edge is what moving the derivations onto `rules`
    removed."""
    src = (ROOT / "compiler.py").read_text(encoding="utf-8")
    assert "farever_companion.data" not in src
    assert "import sources" not in src
    for name in ("shop_item_ids_from_payload", "crafted_item_ids",
                 "recipe_output_ids_from_payload"):
        assert name in src, name


def test_the_predicates_apply_the_shared_regexes():
    assert rules.is_premium_shop_id("Mount_Wolf_EA_Spark")
    assert rules.is_premium_shop_id("Sparkle_Sword")
    assert not rules.is_premium_shop_id("Plain_Rusty_Sword")
    assert rules.is_guild_merchant_source("WanderingMerchant_Npc_Crimson")
    assert not rules.is_guild_merchant_source("Some_Other_Vendor")
    assert rules.is_unreleased_kind("Todo") and rules.is_unreleased_kind("UNRELEASED")
    assert not rules.is_unreleased_kind("achievement")


def test_the_flag_predicates_read_the_named_bits():
    assert rules.is_no_codex(rules.NO_CODEX_FLAG_BIT | 0x1)
    assert not rules.is_no_codex(0x1)
    assert rules.is_special_flag(rules.SPECIAL_FLAG_BIT)
    assert rules.is_special_flag(rules.BOSS_FLAG_BIT | rules.UNIQUE_FLAG_BIT)
    assert not rules.is_special_flag(0x0)
    # the guard every caller relies on: a missing/None flags value is False
    for fn in (rules.is_no_codex, rules.is_boss_flag, rules.is_unique_flag,
               rules.is_special_flag):
        assert fn(None) is False and fn("x") is False
