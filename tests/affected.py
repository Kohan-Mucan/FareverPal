"""Pick the tests a working-tree change can actually break.

Why
---
``pytest`` with no arguments collects all 44 test modules (~6.5 minutes). Most
edits touch one area, and the docs already say to verify narrowly (``AGENTS.md``,
"Verify"). This module derives that narrow set mechanically instead of from a
hand-kept table:

* every test module's transitive import closure over this repo's own modules, so
  a change to a module a test imports - directly or through a chain - keeps it;
* the tests whose source names the changed file, which is how data sheets and
  path-reading guards are reached;
* the tree-wide contract tests a change to ANY tracked file can invalidate
  (``_ALWAYS_TESTS``), plus a small prefix table for whole-area scans
  (``_AREA_GUARDS``).

``tests/conftest.py`` wires it into collection: ``pytest_ignore_collect`` keeps
only the selected modules, so the rest are never imported or run. It is
deliberately conservative - a source module no test reaches, a change to a
conftest, or anything it cannot read falls back to the FULL suite - and it never
fires under CI or when the user names a path on the command line.

Escape hatches: ``FAREVER_TEST_SCOPE=all`` (or ``pytest --scope=all``) forces the
full suite; ``FAREVER_TEST_SCOPE=changed`` is accepted as a synonym for the
default ``auto``.

A narrowed run can only be wrong by OMISSION - it skips a module the change did
break, and nothing in that run can see it, because the skipped module never
runs. So each narrowed plan is remembered, and when a later FULL run goes red in
a module the plan skipped, the pair (red test file, the changed paths that should
have selected it) is recorded to a ledger and reported. That is the only signal
that the mapping is missing an edge; see ``record_gaps``.
"""
from __future__ import annotations

import ast
import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Env values that mean "yes" (same set tests/test_repo_hygiene.py uses).
_TRUTHY = {"1", "true", "yes", "on"}

# Directories that never hold project source we map against. `ai/` is the
# agent scratch tree (git-ignored); the rest are build output or caches.
_SKIP_DIRS = frozenset({
    "__pycache__", ".git", ".venv", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", "ai", "build", "dist", "node_modules",
})

# Tree-wide contract modules: a change to ANY tracked file can convict them
# (they census every tracked source), so a narrowed run always keeps them.
_ALWAYS_TESTS = ("test_repo_hygiene.py",)

# Extra guards keyed by the changed path's prefix. `test_ui_utils.py` scans every
# file under `ui/` (line budget, HUD ownership, overlay persistence), so a ui
# change can break it without importing the module that changed.
_AREA_GUARDS = (
    ("farever_companion/ui/", ("test_ui_utils.py",)),
)

# A change here changes how EVERY test is collected, so narrowing is unsafe.
_FULL_SUITE_TRIGGERS = frozenset({"conftest.py", "tests/conftest.py"})


class Selection:
    """One narrowed run: which test files to keep, and why.

    ``keep`` holds repo-relative posix paths (``tests/test_x.py``). ``unmapped``
    records changed paths no test reaches; when any of those is a Python source
    the caller falls back to the full suite rather than quietly testing less.
    ``full`` short-circuits the whole thing.
    """

    __slots__ = ("keep", "changed", "unmapped", "full")

    def __init__(self, keep, changed, unmapped=(), full=False):
        self.keep = set(keep)
        self.changed = list(changed)
        self.unmapped = list(unmapped)
        self.full = bool(full)


def _env_truthy(name):
    return os.environ.get(name, "").strip().lower() in _TRUTHY


def changed_paths(root=ROOT):
    """Every path git reports as changed (staged, unstaged, untracked).

    Returns repo-relative posix paths, or None when git cannot be read at all -
    the caller treats that as "do not narrow". A rename contributes only its new
    path; ``-z`` NUL-separates fields, and a rename record's source follows it as
    the next field.
    """
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain", "-z",
             "--untracked-files=all"],
            capture_output=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return None
    return _parse_status(proc.stdout.decode("utf-8", "replace"))


def _parse_status(text):
    """The paths in a ``git status --porcelain -z`` payload.

    Each record is ``XY <path>``; a rename/copy record is followed by its source
    path as the NEXT NUL field (git puts the new name first), so that field is
    skipped rather than read as a record of its own.
    """
    fields = text.split("\0")
    paths = []
    i = 0
    while i < len(fields):
        entry = fields[i]
        i += 1
        if len(entry) < 4:
            continue
        status, raw = entry[:2], entry[3:]
        if status[0] in "RC":            # rename/copy: skip the source field
            i += 1
        raw = raw.strip('"').replace("\\", "/")
        if raw:
            paths.append(raw)
    return paths


def _is_test_file(rel):
    name = rel.rsplit("/", 1)[-1]
    return name.startswith("test_") and name.endswith(".py")


def rel_test_path(path):
    """`path` as a repo-relative test-module path, or None if it is not one.

    This is what `pytest_ignore_collect` asks, so it must be cheap and never
    raise: a path outside the repo, a directory, or any non-``test_*.py`` file
    all answer None.
    """
    try:
        rel = Path(str(path)).resolve().relative_to(ROOT).as_posix()
    except (ValueError, OSError):
        return None
    if not rel.startswith("tests/") or not _is_test_file(rel):
        return None
    return rel


def _module_name(rel):
    """Dotted module name for a repo-relative .py path (`__init__` folds into
    its package, as Python does)."""
    stem = rel[:-3]
    if stem.endswith("/__init__"):
        stem = stem[: -len("/__init__")]
    return stem.replace("/", ".")


def _package_of(module, is_package):
    if is_package:
        return module
    if "." not in module:
        return ""
    return module.rsplit(".", 1)[0]


def _is_reexport(node, lines):
    """True for an import the file re-exports instead of using.

    `# noqa: F401` is how this repo marks a deliberate re-export - the split
    convention keeps `from .moved_module import Name  # noqa: F401` so no call
    site moves (see `ui/pages/combat_rail.py`). A wildcard namespace pull marks
    `F401,F403` and is NOT a re-export: there the imported namespace IS the
    use, so it stays an edge.
    """
    end = getattr(node, "end_lineno", None) or node.lineno
    segment = "\n".join(lines[node.lineno - 1:end])
    return "noqa" in segment and "F401" in segment and "F403" not in segment


def _project_sources(root=ROOT):
    """Read every project .py file once: {repo-relative posix path: text}."""
    texts = {}
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        if any(part in _SKIP_DIRS for part in rel.split("/")[:-1]):
            continue
        try:
            texts[rel] = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
    return texts


def _dependencies(text, module, is_package, known, skip_reexports=False):
    """Project modules ``text`` imports, resolved enough for a closure.

    Every import spelling is walked, not just the module level, so a deferred
    import inside a function still counts. A dotted import also depends on each
    package above it (importing ``a.b.c`` runs ``a/__init__`` and ``a/b/__init__``),
    which is why the prefix chain is added too.

    ``skip_reexports`` leaves out an import the file only re-exports (see
    ``_is_reexport``). It defaults off, so the narrowed test run keeps every
    edge it has always counted.
    """
    package = _package_of(module, is_package)
    deps = set()

    def add(name):
        parts = name.split(".")
        for i in range(1, len(parts) + 1):
            candidate = ".".join(parts[:i])
            if candidate in known and candidate != module:
                deps.add(candidate)

    try:
        tree = ast.parse(text)
    except SyntaxError:
        return deps
    lines = text.splitlines()
    for node in ast.walk(tree):
        if (skip_reexports and isinstance(node, (ast.Import, ast.ImportFrom))
                and _is_reexport(node, lines)):
            continue
        if isinstance(node, ast.Import):
            for alias in node.names:
                add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            base = package
            if node.level:
                for _ in range(node.level - 1):
                    base = base.rsplit(".", 1)[0] if "." in base else ""
                target = f"{base}.{node.module}" if node.module else base
            else:
                target = node.module or ""
            if target:
                add(target)
            for alias in node.names:
                if alias.name != "*" and target:
                    add(f"{target}.{alias.name}")
    return deps


def _known_modules(texts):
    """(every dotted module, {module: is-package}) for a source mapping."""
    known = set()
    is_package = {}
    for rel in texts:
        name = _module_name(rel)
        known.add(name)
        is_package[name] = rel.endswith("/__init__.py")
    return known, is_package


def _module_graph(texts):
    """{dotted module: set of project modules it imports} for every source."""
    known, is_package = _known_modules(texts)
    graph = {}
    for rel, text in texts.items():
        name = _module_name(rel)
        graph[name] = _dependencies(text, name, is_package[name], known)
    return graph


def module_importers(texts=None, skip_reexports=True):
    """{dotted module: every project module that imports it}.

    The graph the narrowed run is built from, inverted, so "who imports this"
    has ONE definition: `tests/test_ui_utils.py` censuses the small
    single-consumer UI modules with it instead of growing a second resolver
    that could drift from this one. Test modules count as importers - a test
    that imports a module directly is itself evidence that the module is a unit
    worth naming.

    `skip_reexports` drops an import the file only re-exports rather than uses
    (`_is_reexport`), because a DEAD re-export otherwise makes a fragment look
    shared - which is how the 19-line `ui/pages/items/family_glyphs.py` held a
    second "consumer" that never referenced it.
    """
    texts = _project_sources() if texts is None else dict(texts)
    known, is_package = _known_modules(texts)
    importers = {}
    for rel, text in texts.items():
        name = _module_name(rel)
        for dep in _dependencies(text, name, is_package[name], known,
                                 skip_reexports=skip_reexports):
            importers.setdefault(dep, set()).add(name)
    return importers


def _closure(start, graph):
    """Every module reachable from ``start`` (its import closure, exclusive)."""
    seen = set()
    stack = [start]
    while stack:
        for dep in graph.get(stack.pop(), ()):
            if dep not in seen:
                seen.add(dep)
                stack.append(dep)
    return seen


def select_tests(changed, texts=None):
    """The tests ``changed`` can affect, plus the paths none of them reach.

    ``texts`` defaults to this repo on disk; tests inject a synthetic tree.
    """
    texts = _project_sources() if texts is None else dict(texts)
    graph = _module_graph(texts)
    test_rels = sorted(r for r in texts
                       if r.startswith("tests/") and _is_test_file(r))
    closures = {rel: _closure(_module_name(rel), graph) for rel in test_rels}

    keep = set()
    unmapped = []
    for raw in changed:
        rel = raw.replace("\\", "/")
        if rel in test_rels:            # an edited test runs itself
            keep.add(rel)
            continue
        module = _module_name(rel) if rel.endswith(".py") else None
        base = rel.rsplit("/", 1)[-1]
        hit = False
        if module is not None:
            for test in test_rels:
                if module in closures[test]:
                    keep.add(test)
                    hit = True
        for test in test_rels:          # a data sheet or spec named in the test
            if (base and base in texts[test]) or rel in texts[test]:
                keep.add(test)
                hit = True
        if not hit:
            unmapped.append(rel)

    for name in _ALWAYS_TESTS:
        rel = f"tests/{name}"
        if rel in test_rels:
            keep.add(rel)
    for prefix, guards in _AREA_GUARDS:
        if any(c.replace("\\", "/").startswith(prefix) for c in changed):
            for guard in guards:
                rel = f"tests/{guard}"
                if rel in test_rels:
                    keep.add(rel)
    return Selection(keep, changed, unmapped)


def _scope_choice(config):
    """`all` to force the full suite, else `auto` (narrow when possible)."""
    raw = config.getoption("--scope") or os.environ.get("FAREVER_TEST_SCOPE", "")
    raw = str(raw).strip().lower()
    if raw in ("all", "full", "off", "none"):
        return "all"
    if _env_truthy("CI"):
        return "all"
    return "auto"


def _explicit_selection(config):
    """True when the user already scoped the run - never narrow over them.

    A path/nodeid on the command line, ``-k``/``-m``/``--deselect``, or a
    last-failed/stepwise run all mean "run what I asked for".
    """
    option = config.option
    if option.keyword or option.markexpr or option.deselect:
        return True
    if getattr(option, "last_failed", False) or getattr(option, "stepwise", False):
        return True
    root = str(config.rootdir)
    return any(arg != root for arg in config.args)


def plan_run(config):
    """Decide the run scope, or None to leave pytest untouched.

    None means "collect normally" and is the answer for CI, an explicit
    selection, a clean tree, an unreadable git, and every fail-open case.
    """
    if _scope_choice(config) == "all":
        return None
    if _explicit_selection(config):
        return None
    changed = changed_paths()
    if not changed:
        return None
    normalized = [c.replace("\\", "/") for c in changed]
    if any(c in _FULL_SUITE_TRIGGERS for c in normalized):
        return Selection((), normalized, full=True)
    selection = select_tests(normalized)
    if not selection.keep:
        selection.full = True
    elif any(u.endswith(".py") for u in selection.unmapped):
        selection.full = True
    return selection


def describe(selection):
    """The `pytest_report_header` lines that say what was narrowed and how to
    widen it back."""
    if selection.full:
        return ["farever scope: full suite (change could not be mapped safely)"]
    lines = [
        f"farever scope: {len(selection.keep)} affected test file(s) "
        f"from {len(selection.changed)} changed path(s)",
    ]
    if selection.unmapped:
        lines.append("  unmapped: " + ", ".join(sorted(selection.unmapped)[:6]))
    lines.append("  full suite: pytest --scope=all  (or FAREVER_TEST_SCOPE=all)")
    return lines


# --- Mapping-gap ledger -----------------------------------------------------
#
# The plan a narrowed run used is written down, so a later full run can answer
# the one question a narrowed run cannot: did we skip something that was really
# broken? The record names the red test file and the changed paths that should
# have selected it, which is exactly what a human needs to add the missing edge.

# Inside pytest's own cache dir on purpose: git ignores `.pytest_cache/`, and
# `_SKIP_DIRS` already keeps the selector from reading it as source, so a
# generated ledger can never look like a change.
LEDGER_REL = ".pytest_cache/farever_affected_gaps.json"


def ledger_path(root=ROOT):
    """Where the mapping-gap ledger lives."""
    return Path(root) / LEDGER_REL


def _read_ledger(root=ROOT):
    """The ledger as a dict; an absent or corrupt file reads as empty."""
    try:
        data = json.loads(ledger_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_ledger(data, root=ROOT):
    """Persist the ledger. Never raises - a record must not fail a run."""
    path = ledger_path(root)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8")
    except OSError:
        return None
    return path


def remember_plan(selection, root=ROOT):
    """Write down the narrowed plan a later full run attributes failures to.

    A full or absent selection has nothing to remember, so it leaves the
    previous plan alone: the full run is the one that READS it.
    """
    if selection is None or selection.full or not selection.changed:
        return None
    data = _read_ledger(root)
    data["plan"] = {"changed": list(selection.changed),
                    "keep": sorted(selection.keep)}
    return _write_ledger(data, root)


def failing_test_rel(nodeid):
    """The test module a nodeid belongs to, or None when it is not one.

    ``tests/test_x.py::test_y[param]`` -> ``tests/test_x.py``; a collect error's
    nodeid is the file itself; anything outside ``tests/test_*.py`` is None.
    """
    rel = str(nodeid).split("::", 1)[0].replace("\\", "/")
    if rel.startswith("tests/") and _is_test_file(rel):
        return rel
    return None


def mapping_gaps(failing, plan):
    """Red test files ``plan`` skipped, each paired with the changed paths.

    Pure: ``failing`` is a collection of test-module paths and ``plan`` is a
    remembered plan dict. A file in ``plan["keep"]`` was RUN, so it is not a
    gap; whatever is left was skipped and the plan's changed paths are the ones
    that should have selected it.
    """
    keep = set(plan.get("keep", ()))
    changed = list(plan.get("changed", ()))
    return [(rel, changed) for rel in sorted(set(failing)) if rel not in keep]


def record_gaps(failing, root=ROOT):
    """Record every red test file the remembered narrowed plan skipped.

    Returns the ``(test_rel, changed_paths)`` pairs written, or an empty list
    when there is no remembered plan, nothing failed, or every red file was one
    the plan kept. Each gap counts its sightings so a recurring one stands out.

    Caveat: the plan is from the LAST narrowed run. If the working tree has
    moved on since, a later unrelated failure can be attributed to it; the
    changed paths are recorded with each gap so the record can be judged.
    """
    data = _read_ledger(root)
    plan = data.get("plan")
    if not plan or not failing:
        return []
    gaps = mapping_gaps(failing, plan)
    if not gaps:
        return []
    stored = data.setdefault("gaps", {})
    for test, changed in gaps:
        entry = stored.setdefault(test, {"seen": 0})
        entry["changed"] = changed
        entry["seen"] = int(entry.get("seen", 0)) + 1
    _write_ledger(data, root)
    return gaps


def format_gaps(gaps):
    """The warning a full run prints when it finds a mapping gap."""
    if not gaps:
        return []
    lines = [
        "",
        "farever affected-mapping GAP - a full run failed in a test file that a",
        "narrowed run over the same change SKIPPED. The mapping should connect:",
    ]
    for test, changed in gaps:
        lines.append(f"  {test}")
        paths = ", ".join(changed) if changed else "(no changed paths recorded)"
        lines.append(f"      <- changed: {paths}")
    lines.append("  Fix: teach tests/affected.py this edge, or add the test to "
                 "_ALWAYS_TESTS.")
    return lines
