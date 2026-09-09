"""The data compiler's rebuild must stay a pure function of the game data.

`compiler.py` is dev-side — it never ships, and the app reads the `raw_*.py`
shims it generates (plus the shared-rules copy it bundles into the package) —
which is exactly why its 1147-line `compile_to_py` grew
without a single test: nothing but a human diffing the rebuilt shims ever
checked a change to it. So a change there has one honest acceptance test, and
this is it: rebuild from the same input and get byte-identical shims.

The property is the compiler's own stated contract (`_payload_meta`: "the
payload must be a pure function of the game data so rebuilds of unchanged data
are byte-identical — and `git status` stays clean after every build-mod"), so
any diff here means a change altered what the compiler MEANS, not how it is
written. The zlib caveat: the shim stores deflate output, and a different zlib
version can produce different (equally valid) bytes for the same input — on
such a machine this comparison is stale by construction, not the compiler.

Skipped when the extracted game data is not in the checkout: CI has no game
dump, so nothing can be rebuilt there. The fail-fast path (no sheets at all)
is pinned separately, so the skip is never silent.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from compiler import compile_to_py

ROOT = Path(__file__).resolve().parents[1]
GAME_DATA = ROOT / "assets" / "data"       # extracted game sheets (local only)
SHIMS = ROOT / "farever_companion" / "data"


def _digests(directory: Path) -> dict[str, str]:
    return {p.name: hashlib.md5(p.read_bytes()).hexdigest()
            for p in directory.glob("raw_*.py")}


def _have_game_data() -> bool:
    return GAME_DATA.is_dir() and any(GAME_DATA.glob("*.json"))


def test_a_rebuild_reproduces_the_committed_shims(tmp_path):
    """Same game data in, same shim out — byte for byte.

    Writes go only under the `output_file` the caller passes (`output_dir =
    output_file.parent`), so pointing the rebuild at tmp_path is what keeps
    this test from touching the checkout — asserted rather than assumed,
    because a compiler that wrote into the package would make the comparison
    above it meaningless.
    """
    if not _have_game_data():
        pytest.skip("no extracted game data in assets/data — cannot rebuild")

    before = _digests(SHIMS)
    assert before, "no compiled shims to compare against"

    compile_to_py(ROOT, ROOT, tmp_path / "raw_data.py")

    produced = _digests(tmp_path)
    assert produced, "the rebuild produced no shims at all"
    for name, digest in sorted(produced.items()):
        assert name in before, f"the compiler now writes a new shim: {name}"
        assert digest == before[name], (
            f"{name} changed after rebuilding unchanged game data: the "
            "compiler's output is no longer a pure function of its input")
    # Every committed shim is compiler output, and every produced shim must
    # match the committed one — so the rebuild only ever writes under the
    # output_file it was given.
    assert _digests(SHIMS) == before, (
        "the rebuild wrote into the repo's data dir — it must only write under "
        "the output_file it was given")


def test_the_default_shim_target_is_where_the_comparison_looks():
    """`SHIMS` is a hardcoded copy of where the compiler's default invocation
    writes, so this asserts the copy stays true.    The rebuild comparison above can only be as honest as its target: if the
    default target moved, `SHIMS` would keep pointing at the old directory, the
    digests there would never change, and the byte-identical check would keep
    passing forever against shims nothing writes any more. Three independent
    ways for that to happen, three halves here:

    - the runtime half: the app's `paths.data_root()` must still resolve to this
      directory. The constant is deliberately NOT shared with the compiler —
      it is an independent copy, pinned loud instead of silently equal.
    - the build half: the compiler is STANDALONE (it generates the package, so
      it may not import it), which leaves the target as arithmetic —
      `PACKAGE_DIRNAME` under the compiler's own directory. The name is checked
      against the real package directory AND against the runtime's own
      derivation, so a rename cannot move one and not the others.
    - the source half: that arithmetic is visible only in the `__main__` block,
      which no import can check (a future edit there could hardcode any path) —
      so the block is read and pinned, including that the app's path module is
      NOT back.
    """
    from farever_companion.paths import data_root
    from compiler import PACKAGE_DIRNAME

    assert data_root() == SHIMS, (
        f"paths.data_root() resolves to {data_root()} but this file compares "
        f"rebuilt shims against {SHIMS} - either the default data dir moved "
        "(follow it here) or the rebuild comparison is reading stale shims")
    assert data_root().parent.name == PACKAGE_DIRNAME, (
        f"compiler.PACKAGE_DIRNAME ({PACKAGE_DIRNAME!r}) and the package "
        f"paths.py derives ({data_root().parent.name!r}) disagree - the two are "
        "pinned separately on purpose, so a rename has to move both")
    assert ROOT / PACKAGE_DIRNAME == SHIMS.parent, (
        f"PACKAGE_DIRNAME no longer names the package holding {SHIMS}")

    main_block = (ROOT / "compiler.py").read_text(encoding="utf-8").split(
        'if __name__ == "__main__":', 1)[1]
    assert "from farever_companion.paths import data_root" not in main_block, (
        "compiler.py imports the app's path module again - it must stay "
        "standalone (see test_no_compiler_code_path_imports_the_app_tree)")
    assert "package_dir = project_root / PACKAGE_DIRNAME" in main_block, (
        "compiler.py's default target no longer derives the package directory "
        "from PACKAGE_DIRNAME - a hardcoded path there would be invisible to "
        "the runtime check and to this file's comparison")
    assert 'out = package_dir / "data" / "raw_data.py"' in main_block, (
        "compiler.py's default output file renamed or rerouted - update this "
        "pin and SHIMS together, or the rebuild comparison reads stale shims")


def test_compiling_without_game_data_fails_fast(tmp_path):
    """No sheets -> SystemExit naming the folder, not a near-empty shim.

    This is the first thing a fresh clone hits. The failure has to say which
    folder is empty and what to run, because the alternative — writing an empty
    shim — surfaces much later, as a confusing missing-data error at runtime.
    """
    (tmp_path / "manifest").mkdir()

    with pytest.raises(SystemExit) as exc:
        compile_to_py(tmp_path / "raw", tmp_path / "manifest",
                      tmp_path / "out" / "raw_data.py")

    assert "Missing game data" in str(exc.value)


def test_compiled_shop_ids_match_the_runtime_derivation():
    """The compiler's collection-catalog shop set IS the runtime's shop set.

    The `obtainable` flag on a collection row is `codex.is_shop_item`'s union:
    the drops index's shop-ONLY ids OR the premium id pattern. The compiler used
    to apply the pattern half alone — a comment claimed the union it never
    implemented — so every shop row whose id carried no premium token would read
    '[unreleased]' in the frozen catalog while the app called it obtainable.
    Both sides now derive the set from the same payload via
    `sources.shop_item_ids_from_payload`; this pins them together, so a change
    that stops the compiler reading the index (or that feeds it a different
    crafted-id set) fails here instead of silently diverging.
    """
    if not _have_game_data():
        pytest.skip("no extracted game data in assets/data — cannot derive")
    from compiler import _compiled_shop_ids
    from farever_companion.data.items import sources

    data = ROOT / "assets" / "data"
    compiled = _compiled_shop_ids(data, data)
    runtime = sources.shop_item_ids()
    assert compiled == runtime, (
        f"compiler shop set ({len(compiled)}) != runtime shop set "
        f"({len(runtime)}); symmetric difference: "
        f"{sorted(compiled ^ runtime)[:20]}")
    # a representative drops-index shop id the premium pattern does NOT match,
    # so the assertion cannot pass on the pattern alone
    from farever_companion.data import codex
    probe = "Mount_Wolf_01"
    assert probe in compiled
    assert not codex._SHOP_ID_RE.search(probe)


def test_no_compiler_code_path_imports_the_app_tree():
    """The compiler GENERATES the app package, so it has to run with that package
    unimportable. Any `farever_companion` import anywhere in it — module level or
    inside a function, a deferred branch that never runs today — puts the
    bootstrap-order edge back, which is the whole reason the shared rules moved
    out to `game_rules.py` and the package directory is a string constant.

    Two halves, because either can pass while the other fails: the AST walk sees
    every import statement in the file, and the subprocess actually imports the
    compiler with the package blocked — which is what standalone MEANS.
    """
    import ast
    import subprocess
    import sys

    tree = ast.parse((ROOT / "compiler.py").read_text(encoding="utf-8"))
    offenders = sorted(
        {a.name for n in ast.walk(tree) if isinstance(n, ast.Import)
         for a in n.names if a.name.split(".")[0] == "farever_companion"}
        | {n.module for n in ast.walk(tree)
           if isinstance(n, ast.ImportFrom) and n.module
           and n.module.split(".")[0] == "farever_companion"})
    assert not offenders, (
        f"compiler.py imports the app package: {offenders} - the shared rules "
        "live in game_rules.py beside it, and the package's directory name "
        "belongs in PACKAGE_DIRNAME as a string, not an import")

    script = "\n".join([
        "import sys",
        "class _BlockApp:",
        "    def find_spec(self, name, path=None, target=None):",
        "        if name.split('.')[0] == 'farever_companion':",
        "            raise ImportError('the app package is unavailable')",
        "        return None",
        "sys.meta_path.insert(0, _BlockApp())",
        "import compiler",
        "print('STANDALONE OK')",
    ])
    r = subprocess.run([sys.executable, "-c", script], cwd=str(ROOT),
                       capture_output=True, text=True)
    assert r.returncode == 0 and "STANDALONE OK" in r.stdout, (
        "importing compiler.py with the app package blocked failed:\n"
        f"{r.stdout}{r.stderr}")


def test_the_bundled_rules_copy_matches_its_canonical_source(tmp_path):
    """`farever_companion/rules.py` is generated output, not a second source.

    The app imports the rules there; the canonical file is `game_rules.py` at the
    repo root, which is what the compiler loads. The build bundles one into the
    other, so this pins the copy against its source: writing to the wrong file is
    a red test rather than an edit the next build silently reverts.
    """
    from compiler import _bundle_shared_rules, bundled_rules_source

    committed = (SHIMS.parent / "rules.py").read_text(encoding="utf-8")
    assert "GENERATED FILE - DO NOT EDIT." in committed[:700], (
        "the app's rules copy lost its generated banner - it is not the copy "
        "the build writes")
    assert committed == bundled_rules_source(), (
        "farever_companion/rules.py differs from game_rules.py + the banner - "
        "run `python compiler.py` so the app and the build agree again")

    # the bundler writes exactly that text, and only where it is pointed
    assert _bundle_shared_rules(tmp_path) == tmp_path / "rules.py"
    assert (tmp_path / "rules.py").read_text(encoding="utf-8") == committed


def test_heroic_boss_edges_are_stamped_onto_rowless_items():
    """The compiler stamps the `<boss>_HM` edge onto items the scan left with
    no drop row, so every consumer of the compiled index sees the boss — and
    it leaves recorded rows exactly as the scan wrote them."""
    from compiler import _stamp_heroic_boss_edges

    loot = [
        {"id": "Cleodora_HM",
         "loot": [{"item": "Back_EBee_AssCle", "proba": 0.01},
                  {"item": "Chest_EBee_Fig", "proba": 0.01}]},
        {"id": "Some_World_Table", "loot": [{"item": "Unrelated"}]},
    ]
    drops = {
        "_sources": [{"id": "Cleodora", "kind": "unit",
                      "name": "Queen Honeyzabeth"}],
        "_tables": ["HM_Items"],
        "items": {
            "Back_EBee_AssCle": {},                        # no rows -> stamped
            "Chest_EBee_Fig": {"drops": [{"l": 0, "p": 0.5, "s": 0, "t": 0}]},
            "Unrelated": {"drops": []},
        },
    }
    _stamp_heroic_boss_edges(drops, loot)
    # the row-less piece got its heroic boss + `_HM` table
    assert drops["items"]["Back_EBee_AssCle"]["drops"] == [
        {"l": -1, "p": 0.01, "s": 0, "t": 1}]
    # the recorded piece is untouched, and a non-`_HM` table stamps nothing
    assert drops["items"]["Chest_EBee_Fig"]["drops"] == [
        {"l": 0, "p": 0.5, "s": 0, "t": 0}]
    assert drops["items"]["Unrelated"]["drops"] == []
    # the boss source already existed; only the missing `_HM` table is added
    assert [s["id"] for s in drops["_sources"]] == ["Cleodora"]
    assert drops["_tables"] == ["HM_Items", "Cleodora_HM"]
    # idempotent: a second stamp adds no second table or row
    _stamp_heroic_boss_edges(drops, loot)
    assert drops["_tables"] == ["HM_Items", "Cleodora_HM"]
    assert drops["items"]["Back_EBee_AssCle"]["drops"] == [
        {"l": -1, "p": 0.01, "s": 0, "t": 1}]


def test_collection_catalog_counts_drops_index_shop_ids_as_obtainable(tmp_path):
    """A codex row flagged 'unreleased' reads obtainable when its id is in the
    drops-index shop set — the union, not just the premium pattern."""
    from compiler import _collection_catalog_from_codex

    data = tmp_path / "data"
    data.mkdir()
    rows_in = {
        "Mount_Wolf_01": {"id": "Mount_Wolf_01", "type": "Mount",
                          "kind": "unreleased"},
        "Mount_Plain_99": {"id": "Mount_Plain_99", "type": "Mount",
                           "kind": "unreleased"},
        "Glider_Fancy_EA_Spark": {"id": "Glider_Fancy_EA_Spark",
                                  "type": "Glider", "kind": "unreleased"},
    }
    (data / "codex.json").write_text(json.dumps({"codex": rows_in}),
                                      encoding="utf-8")

    catalog = _collection_catalog_from_codex(
        data, data, [], None, frozenset({"Mount_Wolf_01"}))
    by_id = {r["id"]: r for r in catalog["items"]}
    assert by_id["Mount_Wolf_01"]["obtainable"] is True    # drops-index shop id
    assert by_id["Glider_Fancy_EA_Spark"]["obtainable"] is True  # pattern half
    assert by_id["Mount_Plain_99"]["obtainable"] is False   # neither half
