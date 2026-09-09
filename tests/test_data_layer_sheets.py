"""The data layer's sheet set stays honest.

`assets/data/` is a grab bag accumulated over many scan generations: the game's
own CDB dumps, the item/loot/achievement sheets the app actually reads, and a
long tail of scanner-era summaries that duplicate one of those. The shop-sheet
sweep and this one removed the duplicates that nothing reads - but "nothing
reads it" is not a property any single file can express, so it is pinned here.

Four things are guarded, all of them previously wrong or drift-prone:

* `compiler.prune_unused_jsons` must keep every compile input the compiler
  names. It used to omit craft.json / job.json / item_drops.json / codex.json,
  so "Compile and Prune" deleted inputs the very next build step required, and
  it listed codex_completion.json, which nothing has read in years.
* `verify_assets.logic_sheets` must name only sheets the app reads - it carried
  the loot_tables / loot_table_contents / items_manifest summaries, none of
  which the app opens.
* No retired sheet may reappear as a read. A reader re-added under one of these
  names means a summary snuck back into the load path.
* The pruner must actually DO that, not just list it: run over a temporary copy
  of `assets/data`, it has to retire exactly the RETIRED_SHEETS and keep every
  live sheet. The checks above read the lists; this one executes them.

AST is used rather than a text search so comments and docstrings (which name
several of these files on purpose) neither satisfy nor trip the checks.
"""
from __future__ import annotations

import ast
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPILER = ROOT / "compiler.py"
VERIFY = ROOT / "build_tools" / "verify_assets.py"
BUILD_TOOLS = ROOT / "build_tools"
PKG = ROOT / "farever_companion"

# A real sheet filename literal - excludes the glob patterns compiler.py legs
# through (`*.json`, `atlas_*.json`) and the bare `".json"` suffix fragment.
_SHEET_LITERAL = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\.json$")

# Sheets retired because the drops index / a source sheet already subsumes them
# and nothing in the app read them. Keep this list authoritative: it is the
# record of what the sweeps removed.
RETIRED_SHEETS = (
    "items_manifest",
    "loot_tables",
    "loot_table_contents",
    "collection_catalog",
    "unitGroup",
    "crafting",
    "crafting_clean",
    "achievements",
    "achievements_clean",
    "map",
    "map_clean",
    "codex_clean",
    "collection_clean",
    "codex_completion",
)


def _literal_json_inputs(source: Path) -> set[str]:
    """`*.json` sheet basenames written as string literals in a module."""
    tree = ast.parse(source.read_text(encoding="utf-8"))
    return {node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
            and _SHEET_LITERAL.match(node.value)}


def _prune_required() -> set[str]:
    """The literal set bound to `required` inside `prune_unused_jsons`."""
    tree = ast.parse(COMPILER.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "prune_unused_jsons":
            for stmt in node.body:
                if (isinstance(stmt, ast.Assign)
                        and any(isinstance(t, ast.Name) and t.id == "required"
                                for t in stmt.targets)):
                    return {e.value for e in stmt.value.elts
                            if isinstance(e, ast.Constant)
                            and isinstance(e.value, str)}
    raise AssertionError("prune_unused_jsons no longer binds a literal `required` set")


def _verify_assets_names(name: str) -> list[str]:
    """A module-level list bound in verify_assets.py, read without importing it.

    verify_assets.py is a build SCRIPT: importing it runs the whole asset
    verification (and sys.exit()s on failure), so it must be parsed, not
    imported.
    """
    tree = ast.parse(VERIFY.read_text(encoding="utf-8"))
    for node in tree.body:
        if (isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == name
                        for t in node.targets)
                and isinstance(node.value, ast.List)):
            return [e.value for e in node.value.elts
                    if isinstance(e, ast.Constant) and isinstance(e.value, str)]
    raise AssertionError(f"verify_assets.py no longer binds a literal `{name}` list")


def _verifier_required_sheets() -> set[str]:
    """Every sheet verify_assets.py requires, normalized to ``X.json`` names.

    `logic_sheets` are bare stems (``items``); the location and page lists
    already carry the suffix. Both are normalized so the comparison against the
    pruner's `required` is apples to apples.
    """
    names = (_verify_assets_names("logic_sheets")
             + _verify_assets_names("essential_location_data")
             + _verify_assets_names("page_sheets"))
    return {n if n.endswith(".json") else f"{n}.json" for n in names}


def test_prune_keeps_every_literal_compile_input():
    """Every `*.json` the compiler names must survive a prune.

    The compiler reads its inputs from both the fresh source dir and the app's
    assets/data; `prune_unused_jsons` is what deletes the leftovers. A compile
    input missing from `required` is deleted the moment a prune runs, and the
    failure surfaces one step later ("verify_assets: craft.json MISSING"), far
    from the cause.
    """
    required = _prune_required()
    literals = _literal_json_inputs(COMPILER)
    missing = sorted(literals - required)
    assert not missing, (
        "compiler.py reads these sheets but prune_unused_jsons' required set "
        f"would delete them: {missing}")
    assert literals, "expected the compiler to name at least one .json input"


def test_the_pruner_no_longer_keeps_the_retired_summaries():
    """Not one retired name may sit in `required` - that keeps it forever."""
    required = _prune_required()
    stale = sorted(f"{name}.json" for name in RETIRED_SHEETS if f"{name}.json" in required)
    assert not stale, (
        "prune_unused_jsons still lists retired sheets as required, so a prune "
        f"can never retire them: {stale}")


def test_a_prune_over_a_temp_copy_retires_exactly_the_retired_sheets(tmp_path):
    """Run the real pruner, not just its lists.

    The AST checks above read `required` and RETIRED_SHEETS; this one exercises
    the behaviour they describe. `assets/data/` is gitignored, so a clean
    checkout has no copy to make - the required sheets are then seeded from
    `required` itself, which keeps the "every live sheet survives" half
    meaningful in CI as well as locally.
    """
    import compiler          # stdlib-only at module scope; safe to import

    required = _prune_required()
    retired = {f"{name}.json" for name in RETIRED_SHEETS}

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    live = ROOT / "assets" / "data"
    for source in (sorted(live.glob("*.json")) if live.is_dir() else []):
        shutil.copyfile(source, data_dir / source.name)

    seeded = {p.name for p in data_dir.glob("*.json")}
    for name in sorted(required | retired):
        path = data_dir / name
        if not path.exists():        # leave a real copy's content alone
            path.write_text("{}", encoding="utf-8")
        seeded.add(name)

    compiler.prune_unused_jsons(data_dir)

    survivors = {p.name for p in data_dir.glob("*.json")}
    removed = seeded - survivors
    assert removed == retired, (
        "a prune must retire exactly the scanner-era sheets: "
        f"unexpectedly removed {sorted(removed - retired)}, "
        f"retired but surviving {sorted(retired - removed)}")
    assert survivors == required, (
        "every live compile input must survive a prune: "
        f"missing {sorted(required - survivors)}, "
        f"unexpected {sorted(survivors - required)}")


def test_the_pruner_can_never_delete_a_sheet_the_verifier_requires():
    """`--prune` must keep every sheet verify_assets.py requires to exist.

    Update_Raw_Data.bat option [2] compiles and then runs `compiler.py --prune`,
    which deletes every assets/data/*.json NOT named in prune_unused_jsons'
    `required` set. The deletion is permanent - the sheet only comes back from a
    fresh scan - and verify_assets.py REQUIRES several of those sheets to exist
    (the item / craft page sheets and the location sheets), so a prune that
    drops one turns the next build's verify step red with the file already
    gone. `required` must therefore be a superset of what the verifier needs.
    """
    required = _prune_required()
    needed = _verifier_required_sheets()
    assert all(n.endswith(".json") for n in needed), (
        f"verifier-required names must be normalized to X.json first: {sorted(needed)}")
    missing = sorted(needed - required)
    assert not missing, (
        "verify_assets.py requires these sheets but prune_unused_jsons would "
        f"delete them: {missing}")
    for sheet in ("item_drops.json", "craft.json", "job.json"):
        assert sheet in needed, f"{sheet} is no longer a verifier-required sheet"
        assert sheet in required, f"a prune would delete verifier-required {sheet}"


def test_verify_assets_requires_only_sheets_the_app_reads():
    """`logic_sheets` must be exactly the app-read core sheets.

    Pinned rather than derived: the point is that adding a sheet to this list
    is a deliberate statement that the app reads it, not an accident.
    """
    logic = _verify_assets_names("logic_sheets")
    assert logic == ["items", "enemies", "skills", "lootTable", "dungeons"], (
        "verify_assets.logic_sheets changed - the listed sheets are required in "
        "raw_data-less fallback mode, so each must be one the app truly reads")
    for retired in RETIRED_SHEETS:
        assert retired not in logic, (
            f"{retired} is a retired scanner-era sheet and must not be required")
    assert _verify_assets_names("page_sheets") == ["craft.json", "job.json", "item_drops.json"]


def _python_sources(*roots: Path):
    for root in roots:
        if root.is_file():
            yield root
        else:
            for path in sorted(root.rglob("*.py")):
                if "__pycache__" not in path.parts:
                    yield path


def test_no_retired_sheet_is_read_by_the_data_layer():
    """A retired sheet name must not come back as a read.

    Equality against the whole literal (not a substring) is deliberate: a
    docstring in `data/collections.py` and a comment in `compiler.py` both
    spell `collection_catalog.json` on purpose, and neither is a read. Only an
    exact `"<name>.json"` literal - the shape every real reader uses (a path
    join or a cdb lookup) - counts.
    """
    retired_files = {f"{name}.json" for name in RETIRED_SHEETS}
    offenders: dict[str, list[str]] = {}
    for path in _python_sources(PKG, BUILD_TOOLS, COMPILER):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Constant)
                    and isinstance(node.value, str)
                    and node.value in retired_files):
                offenders.setdefault(node.value, []).append(
                    str(path.relative_to(ROOT)))
    assert not offenders, (
        "retired scanner-era sheets are read again - the drops index already "
        f"carries these: {offenders}")
