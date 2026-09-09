"""Repo hygiene: stray files in the shipped package, repo-root scratch, and
live-data isolation.

A notepad save dropped under `farever_companion/` is invisible to every other
guard in this suite: the line-budget test globs `*.py`, the import audits only
look at modules, and the packaging spec never names it — so it just sits in the
source tree as dead weight. Live example: `ui/entity - pets.text` (added in
b280384, removed 2026-09-17) survived every check in here for months.

Repo-root scratch has the same problem one level up: tools that render a mockup,
probe or screenshot write a folder like `tmp_preview/` at the repo root, and
nothing tells anyone it is still there. `ai/workspace/<tool>/` is where session
scratch belongs (AGENTS.md), so anything at the root that git does not track —
and that is not a generated build artifact — is reported too.

Both guards REPORT locally and FAIL under CI. Locally each stray is warned about
as DEAD/UNUSED FILE, so it shows up in pytest's warnings summary without turning
a hygiene slip into a red baseline mid-work — the point is to be noticed, not to
block a work-in-progress run. Under CI (`CI` is set by every major provider,
GitHub Actions included) the same findings become an assertion failure, so a
stray cannot be merged. `FAREVER_HYGIENE_STRICT=1` / `=0` forces either
behaviour locally — see `_strict()`.

The last test here is a different kind of hygiene: it refuses to let the suite
run against the user's live moddata folder, which is how settings.json got reset
to defaults on 2026-09-17.

A third kind is dead CODE rather than dead files: a class nothing in the repo
references. Pyflakes cannot see this at all (it reports unused imports, never
unused classes), which is how `components.ClickableCard` - zero consumers in the
app, the tests and the dev tools - and `widgets.ColorButton` survived a full
unused-import sweep. The guard below counts each top-level class name across
every tracked file and reports the ones whose definition is the only mention of
that name anywhere; the dead-class census behind it found 3 in 371 (Sparkline,
_LinkButton, _HudReplica), all since deleted.

The census now covers top-level FUNCTIONS and fixtures too - pyflakes cannot
see those either, which is how `_qapp` (a module fixture whose last three users
were each given a local copy of it) sat in tests/dps_fakes.py with nothing
referencing it. It reads module-level CONSTANTS too - pyflakes reports unused
imports and unused locals, never an unread tunable, so `theme.LIME` and the
`constants.HL_ANCHOR_*` tables waited for someone to work out whether their
reader had been deleted or their feature was never wired. A fourth guard resolves
every literal `__all__` entry against its
module: `__all__` is what makes a re-export hub a contract instead of a comment,
and it is also the only thing that silences the linter for a name a module does
not define, so a stale entry is completely silent until something checks it.
Its twin reads the same lists the other way round - an import in a module that
declares `__all__` must be exported or used - so a re-export nobody declared
cannot sit there as an "unused import" waiting to be deleted by the next sweep.

One more guard resolves relative imports against the tree. Pyflakes checks
NEITHER half of a `from ... import`: not a module path that does not exist (it
is silent on `from .missing import Thing`) and not a name the target module
does not bind (silent on `from .mod import Nope`), so a broken relative import
is an error only when it runs - and inside a deferred `try/except` not even
then: it degrades its feature quietly. The config split broke five imports that
way (the shared speedrun history stopped merging, companion categories fell
back to `pets`), and a sixth in `core/dps_data.py` had been broken since the
day it was written. The module path is resolved exactly - the dots plus the path
name one target, so that half needs no baseline - and the imported NAMES are
read off the target's own module-level bindings (a `try`/`if` block still
counts, a def/class local does not). A name that is a submodule of the target
passes too, as does every name of a target whose names cannot be enumerated (a
star import, a module `__getattr__`, `globals`/`exec`). It can miss a real one
and cannot convict a live import, the same trade the censuses make.

The newest guard censuses the KEYS the compiler bakes into the `raw_*` data
shims - a baked field is a constant by another name (named once in
compiler.py, shipped to every user), and pyflakes cannot see any of it because
the keys live inside a compressed JSON literal. That is how 482 `atlas_x`/
`atlas_y` row keys shipped unread, and how the rarity sheet still rides with a
`props` subtree whose own compiler comment cites a consumer ("the loot
predictor") that does not exist. A key counts as read when any tracked file
outside its shim and the compiler mentions it by name - a writer, a test or a
dev tool all count - so it can only under-report, never convict. Containers
whose children are legitimately nameless get explicit escapes: the atlas blobs
are id-keyed and read by lookup, raw_skills' `skill_rows` are baked VERBATIM
as a test-pinned contract while its trimmed mirror feeds a resolver that reads
`props`/`vars`/`texts.refs` by description-token name, and `__meta__` is the
provenance stamp a human reads. The zero-reader keys found the day it was
added are ratcheted in a baseline like every other census here.

One guard here is about WHERE a HUD surface lives rather than whether it exists.
A surface built by two overlay windows is the same feature kept in two places:
the Top DPS meter and the Test Dummy HUD each mounted the whole dummy test
(Stop / Start, the per-target split, the vs-last-test baseline, the dirty-run
strip) from one shared mixin, and the meter's copy surfaced whenever the dummy
HUD was off - so every dummy change had two readers, the two boards could stack
over one dummy, and nothing could see it. The widget is instantiated inside the
MIXIN's file, so a check of the window classes alone sees one builder, and the
dead-class census sees a class with two live callers. This one walks each
window's bases, because a mixin's file IS part of the window's surface, and
census what each window builds - the overlay-package classes AND the top-level
builders beside them, since this package draws most of its sections in
dungeon_render/entity_render functions rather than widgets. A name built by two
windows fails until someone declares it shared furniture on purpose, and the
allowlist of those declarations fails in turn when one goes stale.
"""
from __future__ import annotations

import ast
import io
import os
import re
import subprocess
import sys
import tokenize
import warnings
from collections import Counter
from pathlib import Path

import pytest

from tests import source_guard

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "farever_companion"

# The one allowed session-scratch location, relative to the repo root.
SCRATCH_DIR = "ai/workspace"

# Top-level entries that are build/tooling output rather than source or scratch:
# regenerated at will, never hand-edited, so they are not strays.
GENERATED_ROOT_NAMES = {
    "build",            # PyInstaller work dir (build-mod.bat)
    "dist",             # frozen app + moddata
    "native",           # cargo build tree (native/target)
    "__pycache__",      # bytecode cache
}

# Strays warn locally and fail under CI (see the module docstring). These are
# the values `CI`/`FAREVER_HYGIENE_STRICT` are tested against.
_TRUTHY = {"1", "true", "yes", "on"}

# A Python package may legitimately ship only these beside its modules. This
# package bundles NO asset files — its data is compiled into raw_* modules by
# compiler.py — so anything else under here is dead weight.
ALLOWED_SUFFIXES = {".py", ".pyi", ".pyc", ".pyo", ".pyd", ".so"}
ALLOWED_NAMES = {"py.typed"}

SKIP_DIRS = {"__pycache__", ".git", ".mypy_cache", ".pytest_cache", ".ruff_cache"}


class StrayFileWarning(UserWarning):
    """A file sitting where it does not belong: inside the shipped package, or
at the repo root outside the allowed scratch folder."""


class DeadCodeWarning(UserWarning):
    """A tracked definition (class, function, fixture) that nothing in the repo
references, or an `__all__` entry that does not exist."""


def _strict() -> bool:
    """True when stray files should fail the run rather than warn.

    CI is where the tree is expected to be clean and a stray must not be
    mergeable, so `CI` being set turns both guards into hard failures. Locally
    the same finding stays a warning, which is what keeps a hygiene nit from
    reddening a work-in-progress baseline.

    `FAREVER_HYGIENE_STRICT` overrides the detection in either direction: set it
    to 1/true/yes/on to fail locally too, or to 0/false/no/off to keep CI quiet.
    """
    override = os.environ.get("FAREVER_HYGIENE_STRICT", "").strip().lower()
    if override:
        return override in _TRUTHY
    return os.environ.get("CI", "").strip().lower() in _TRUTHY


def _report(problems: list[str], label: str = "stray file guard",
            warning: type[UserWarning] = StrayFileWarning,
            tail: str = f"Delete each file, or move scratch into {SCRATCH_DIR}/<tool>/. ") -> None:
    """Fail the run under CI, otherwise warn once per problem.

    Locally: one warning per stray, so a summary with several of them stays
    readable. Under CI: one assertion naming every stray, so the first failure
    reports the whole tree instead of making you re-run for the next one.

    `label`/`warning` let a second guard reuse the same policy with wording and
    warning class of its own; `tail` is the advice its failure message ends on.
    """
    if not problems:
        return
    if _strict():
        joined = "\n".join(            f"  - {p}" for p in problems)
        raise AssertionError(
            f"{label}: {len(problems)} problem(s) under CI.\n{joined}\n"
            f"{tail}"
            f"Local runs only warn; FAREVER_HYGIENE_STRICT=0 downgrades this "
            f"run the same way.")
    for p in problems:
        warnings.warn(p, warning, stacklevel=1)


def _stray_files(pkg: Path) -> list[Path]:
    """Non-code files under `pkg`, sorted so the report is stable."""
    out: list[Path] = []
    for p in sorted(pkg.rglob("*")):
        if not p.is_file():
            continue
        if SKIP_DIRS & set(p.relative_to(pkg).parts):
            continue
        if p.suffix.lower() in ALLOWED_SUFFIXES or p.name in ALLOWED_NAMES:
            continue
        out.append(p)
    return out


def _tracked_paths(root: Path) -> set[str] | None:
    """Every git-tracked path under `root`, as posix strings relative to it.

    Returns None when git is unavailable or `root` is not a checkout — callers
    then cannot tell committed source from scratch and must skip rather than
    guess (a guess here would either flag the whole repo or nothing at all).
    """
    try:
        r = subprocess.run(["git", "-C", str(root), "ls-files", "-z"],
                           capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    return {p for p in r.stdout.split("\0") if p}


def _dead_classes(texts: dict[str, str]) -> list[tuple[str, int, str]]:
    """Top-level classes whose name appears exactly once across every source
    given: the `class` line itself, with no other mention in the repo.

    The counting is deliberately blunt - every word of every file, one pass, no
    attempt to understand imports - because the bar is accusation, not proof: a
    name mentioned in a comment, docstring or decorator counts as alive, as does
    a class used inside its own file, and so does a name two files define
    between them (which cannot be attributed by name alone). So this can
    under-report a dead class but never convicts a live one.

    Returns [(relative path, lineno, class name)], sorted.
    """
    token_counts: Counter[str] = Counter()
    for src in texts.values():
        token_counts.update(re.findall(r"\w+", src))

    dead: list[tuple[str, int, str]] = []
    for rel, src in sorted(texts.items()):
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue        # a file Python itself will not parse is the
                            # import guards' business, not this one's
        for node in tree.body:            # top level only: a class inside a
            if isinstance(node, ast.ClassDef) and token_counts[node.name] <= 1:
                dead.append((rel, node.lineno, node.name))
    return dead


def _tracked_sources(tracked: set[str]) -> dict[str, str]:
    """Every tracked .py file's text, keyed by its posix path.

    One loader for every census in here: they must see exactly the same tree,
    or one convicts on a name another counted alive somewhere else.
    """
    texts: dict[str, str] = {}
    for rel in sorted(tracked):
        if not rel.endswith(".py"):
            continue
        try:
            texts[rel] = source_guard.read_text(ROOT / rel, encoding="utf-8",
                                               errors="replace")
        except OSError:
            continue
    return texts


# The orphans the function census found the day it was added (2026-09-27),
# ratcheted here instead of warned about: 30 names that were already dead would
# have put a permanent block of warnings in every local summary, and a warning
# that is always there is one nobody reads. Delete an entry when its function is
# wired up or removed; this list is debt, not an exemption. `brand_qicon` reads
# its removal from the account-feature deletion (2026-09-27) - its pixmap twin
# `brand_icon` stays for future sidebar branding, so the ratchet documents rather
# than deletes. Note the `paths.*`
# group: the census reads THIS repo's tracked files only, so a helper consumed
# by the sibling GameFiles/htdocs tools looks dead from in here. The test that
# owns this list re-runs the census for every entry, so one that has been wired
# up (or whose definition is gone) fails the suite until it is deleted here.
_ORPHAN_DEF_BASELINE = {
    "_region_ranges",
    "_sweep_string", "marker_qicon", "_placeholder_glyph",
    "_worldloot_chance", "any_name", "static_spawns",
    "brand_qicon",
    "region_progress", "images_dir", "map_icons_dir", "notes_dir",
    "chest_locs_path", "orb_positions_path", "critter_locs_path",
    "gatherable_locs_path", "cache_dir",    "_get_combo_player_name",
    "_calculate_dps_timeline", "hbox", "_food_item_id", "_cost_label",
    "vendor_upper",
    "_fake_party", "_layout_text",
}

# The dead constants the census found the day it was added (2026-09-27), on the
# same ratchet as the orphans above: 42 names nothing reads would have been a
# permanent block of warnings, and a warning that is always there is one nobody
# reads. Delete an entry when its constant is read or removed. Every one was
# checked across the whole tracked tree, .bat/.spec/.md included - none is
# mentioned a second time - but note the `constants.py` group: that file is kept
# in lockstep with the out-of-repo calibrator (AGENTS.md), so its reflection
# indexes and offset slices are debt to review with that tool in hand rather
# than delete from in here. Same staleness check as the orphans above: an entry
# the constant census no longer finds dead fails the suite until it is deleted.
_DEAD_CONSTANT_BASELINE = {
    # constants.py - HashLink reflection indexes, scan anchors, offset slices
    # (the three armed HL_FINDEX_* names left this list when HL_FINDEX_NAMES
    # started reading them; NOTIFY_KILLED is still the documented-but-unused one)
    "APP_NAME", "HL_FINDEX_NOTIFY_KILLED", "HL_ANCHOR_JPG_DECODE",
    "HL_ANCHOR_PNG_DECODE", "HL_ANCHOR_INFLATE_INIT", "HL_ANCHOR_INFLATE_BUF",
    "OFF_CONSUMABLE_UID", "OFF_SYSTEM_MESSAGE_SENDER_SLOTS", "FOG_MARGIN",
    # The native bridge layout block (st.skill.DamageResult / BaseSkill /
    # HitData, ent.UnitAttributes health, the HashLink String shape) plus the
    # legacy hero->player pointer. These have no PYTHON reader on purpose: the
    # census reads this repo's tracked files only, and their consumer is the
    # sibling GameFiles tree - `hook/gen_bridge_offsets.py` generates
    # `hook/bridge_offsets.h` from them, so the DLL compiles one number per
    # field rather than a C copy that goes stale (see
    # tests/test_generated_bridge_offsets.py). Deleting one silently drops it
    # from the calibrator's view, which is the bug that test was written to
    # stop; kept in lockstep with GameFiles/Farever/Offsets/constants.py, so
    # review with that tool in hand rather than from in here.
    "OFF_HERO_PLAYER_LEGACY",
    "OFF_DR_BASESKILL", "OFF_DR_TARGET", "OFF_DR_AMOUNT", "OFF_DR_KILL",
    "OFF_DR_CRITICAL",
    "OFF_BASESKILL_OWNER_PL", "OFF_BASESKILL_KIND", "OFF_BASESKILL_OWNER",
    "OFF_HITDATA_BASESKILL", "OFF_UA_HEALTH",
    "OFF_STRING_BYTES", "OFF_STRING_LENGTH",
    # core/ - read tables and tunables whose reader has gone
    "MAX_ANCHORS", "COUNT_WINDOW_S", "DERIVE_SILENCE_S", "CO_COMBAT_WINDOW_S",
    "_HERO_STATUS_FIELDS", "_HERO_LOADOUT_FIELDS", "_LOADOUT_EQUIP_FIELDS",
    "_EQUIP_CONTENT_FIELDS", "PLAYER_OWNER_CLASSES", "ATTR_HEALTH_FIELDS",
    # ui/ - palette entries, row metrics and a Windows power-flag table
    "LIME", "CLASS_COLOR", "CAT_LABEL", "_CLASS_FONT_PX",
    "LIST_ROW_H", "R_DIAL", "_CHIP_COLS", "_LAYER_EXTRA",
    "_POWER_REQUEST_CONTEXT_FLAGS_DETAILED", "_POWER_REQUEST_CONTEXT_INVALID",
    "_POWER_REQUEST_CONTEXT_MAX_SIMPLE", "_POWER_REQUEST_CONTEXT_MAX_DETAILED",
    # data/, geo/, backup/master.py and the dev-only icon tool
    "LIVE_PREFIX", "_SETTINGS_INTENT_KEYS",
    "_C_NAME", "_C_GATH", "_C_LOOT", "_C_ACT",
}

# Modules the two ratchets above already convict *as a whole file*: every way
# into them is a name one of those lists records as dead. Same ratchet, one
# level up: the name censuses can only ever report names, so a file whose sole
# purpose is to hold one survives every one of them. `geo/critters.py` was the
# case - `_ORPHAN_DEF_BASELINE` already called its one public function dead and
# its private helper was read by that dead function and nothing else, so
# neither census could see that the file went with the name - and it is deleted
# now, which is why this is empty. It stays as the mechanism for the next one.
# Entries are dotted module paths, and are deleted with the file (debt, not an
# exemption).
_DEAD_FILE_BASELINE = set()


def _framework_name(name: str) -> bool:
    """Names a framework reaches without ever spelling them out, so "mentioned
    nowhere" does not mean "used nowhere". Scoped to tests/ by the caller: a
    `test_*` function in the app itself really is dead."""
    return (name.startswith("test_") or name.startswith("pytest_")
            or name.startswith("setup_") or name.startswith("teardown_")
            or name.startswith("__"))


def _is_autouse_fixture(node: ast.AST) -> bool:
    """True for a fixture pytest runs by itself, with no request anywhere."""
    for dec in getattr(node, "decorator_list", ()):
        try:
            src = ast.unparse(dec)
        except Exception:
            continue
        if "fixture" in src and "autouse" in src:
            return True
    return False


def _dead_functions(texts: dict[str, str], baseline: set[str] = frozenset(),
                    ) -> list[tuple[str, int, str]]:
    """Top-level functions whose name appears exactly once across every source
    given: the `def` line itself, with no other mention in the repo.

    The class census's twin, on the same deliberately blunt bar (see
    `_dead_classes`). Everything is a mention: an import in another file, a
    call up the same file, a docstring, a string table, an `__all__` entry, a
    pytest signature, `request.getfixturevalue("...")` - so this can
    under-report a dead function but never convicts a live one.

    A fixture is therefore checked the same way as any other function: a plain
    fixture must be requested BY NAME somewhere, which is what makes a dead one
    visible. Inside tests/ the names pytest and the runtime claim by convention
    (`test_*`, hooks, autouse fixtures) are skipped.
    """
    token_counts: Counter[str] = Counter()
    for src in texts.values():
        token_counts.update(re.findall(r"\w+", src))

    dead: list[tuple[str, int, str]] = []
    for rel, src in sorted(texts.items()):
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for node in tree.body:            # top level only: a def inside a
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            name = node.name
            if name in baseline:
                continue        # known debt, see the list above
            if rel.startswith("tests/") and (_framework_name(name)
                                             or _is_autouse_fixture(node)):
                continue
            if token_counts[name] <= 1:
                dead.append((rel, node.lineno, name))
    return dead


def _is_constant_name(name: str) -> bool:
    """True for a module-level assignment that follows the repo's constant
    convention: ALL_CAPS, underscores allowed, never a dunder.

    The convention is what keeps this census off module state that is merely
    assigned once and reached some other way - `log`, a `_cache` dict, a Qt
    object held for the app's lifetime. In this codebase an ALL_CAPS module name
    means "a tunable or a table the module reads", so one that nothing reads is
    dead rather than incidental.
    """
    return not name.startswith("__") and name.isupper()


def _dead_constants(texts: dict[str, str], baseline: set[str] = frozenset(),
                    ) -> list[tuple[str, int, str]]:
    """Top-level ALL_CAPS assignments whose name appears exactly once across
    every source given: the assignment itself, with no other mention anywhere.

    The third of the family (`_dead_classes`, `_dead_functions`), on the same
    deliberately blunt bar: every word of every file is a mention, so a name a
    sibling module reads, a table the same file indexes, a docstring reference,
    a `"NAME"` string lookup, an `__all__` entry or a test assertion all count
    as alive - and a name two files define between them cannot be attributed by
    name alone, so it is left alone. This under-reports a dead tunable, and
    never convicts a live one.

    Scope is what `_is_constant_name` allows and only `tree.body`: a dunder, a
    lowercase name, and an assignment inside a function or an `if` block are all
    outside it.
    """
    token_counts: Counter[str] = Counter()
    for src in texts.values():
        token_counts.update(re.findall(r"\w+", src))

    dead: list[tuple[str, int, str]] = []
    for rel, src in sorted(texts.items()):
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for node in tree.body:            # top level only: a tunable built in an
            if isinstance(node, ast.Assign):
                targets = [t for t in node.targets if isinstance(t, ast.Name)]
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                targets = [node.target]
            else:
                continue
            for target in targets:
                name = target.id
                if name in baseline or not _is_constant_name(name):
                    continue
                if token_counts[name] <= 1:
                    dead.append((rel, node.lineno, name))
    return dead


# This file's own path, for the ratchet staleness check below: the baselines
# are declared here, so this is the one file whose mentions of a listed name
# are records rather than readers.
_GUARD_SELF = Path(__file__).resolve().relative_to(ROOT).as_posix()


def _without_ratchet_mentions(texts: dict[str, str],
                              baseline: set[str]) -> dict[str, str]:
    """`texts` with THIS file's mentions of every ratcheted name removed.

    A ratchet entry is a record, not a reader. The name is written in the
    declaration, often again in the comment beside it, and a test in this file
    may assert it - and the censuses count every word of every file, so all
    three of those would each keep the entry looking alive. The staleness
    question is therefore asked against a copy of the tree with this file's
    mentions of those names taken out; a mention anywhere else is a real reader
    and still clears the entry.
    """
    here = texts.get(_GUARD_SELF)
    if here is None:
        return texts
    for name in sorted(baseline):
        here = re.sub(r"\b%s\b" % re.escape(name), "", here)
    out = dict(texts)
    out[_GUARD_SELF] = here
    return out


def _stale_ratchet_entries(texts: dict[str, str], baseline: set[str],
                           census) -> list[str]:
    """Ratcheted names `census` no longer finds dead, sorted.

    The ratchet is re-checked with the very census that owns it - the rule the
    doc-only ratchet already uses - so the day a listed name is wired up or
    deleted, its entry is a red test instead of a line that quietly outlives
    its reason. `census` is `_dead_functions` or `_dead_constants`.
    """
    detected = {name for _rel, _line, name
                in census(_without_ratchet_mentions(texts, baseline))}
    return sorted(baseline - detected)


# The doc-only ratchet. The three censuses above count every word of every .py
# file, so a COMMENT or a DOCSTRING keeping a definition's name alive is
# indistinguishable from code reading it: a name whose only surviving mentions
# are prose is invisible to all three. That is how `constants.CALIBRATION_MAP`
# (named only in the two comments around it) and
# `dps_dummy.DUMMY_TARGET_DEFAULT_S` (named only in its own module header) sat
# unreported with nothing reading either.
#
# Debt, not an exemption, on the same ratchet as the lists above: delete an
# entry when its name is wired up or removed.
_PROSE_ONLY_DEF_BASELINE = {
    # The calibrator's Haxe class/field table. Its reader is the Calibrator GUI
    # in the sibling GameFiles tree (Offsets/), which this repo's tracked files
    # cannot see - the same position as the bridge OFF_* names in
    # _DEAD_CONSTANT_BASELINE. Review with that tool in hand, not from in here.
    "CALIBRATION_MAP",
    # The live default is config.model.dps_dummy_target (60); this constant
    # documents the same number and reads as its source, but DummyClock is only
    # ever handed a `target_s`, so nothing loads it.
    "DUMMY_TARGET_DEFAULT_S",
    # Without a caller since the aptitude index landed: the drops predicate is
    # spelled out at its use sites instead. Wire it up or delete it.
    "is_for_class",
    # Moved here from _ORPHAN_DEF_BASELINE (2026-10-07), which the staleness
    # check retired it from: its only mention outside `core/inspect.py` is the
    # comment in `core/group_reader.py` naming it beside the "one more hop"
    # branch, so the function census stopped finding it dead while nothing in
    # code reads it. Wire it up or delete it.
    "_array_entries",
}


# Tracked non-Python text: the launchers, the spec, the manifests and the eight
# tracked .md files. NOT the docs/*.md corpus - that tree is git-ignored
# (`.gitignore: docs/*`), so it is absent from every other checkout and no guard
# may read it.
DOC_SUFFIXES = (".md", ".rst", ".txt", ".bat", ".spec", ".json", ".yml",
                ".yaml", ".toml", ".cfg", ".ps1")


def _tracked_doc_texts(tracked: set[str]) -> dict[str, str]:
    """Every tracked non-Python text file's text, keyed by its posix path."""
    out: dict[str, str] = {}
    for rel in sorted(tracked):
        if rel.endswith(".py") or not rel.endswith(DOC_SUFFIXES):
            continue
        try:
            out[rel] = source_guard.read_text(ROOT / rel, encoding="utf-8",
                                              errors="replace")
        except OSError:
            continue
    return out


def _docstring_ids(tree: ast.AST) -> set[int]:
    """`id()` of every docstring node.

    What separates a string the module READS - an `__all__` entry, a registry
    key, a `getattr` target - from one that merely documents a name.
    """
    ids: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)):
            ids.add(id(first.value))
    return ids


def _ratchet_literal_ids(tree: ast.AST) -> set[int]:
    """`id()` of every string inside a `_PROSE_ONLY_DEF_BASELINE` declaration.

    The ratchet names the definitions it covers, in this very file, and a
    non-docstring string counts as a CODE mention - so counting the declaration
    too would let every entry exonerate itself: the one name the census is most
    obliged to keep an eye on, the debt it has already recorded, would be the
    one name it could never report again, and the ratchet could never be
    checked for staleness either. So the declaration is read as the record it
    is, not as a reader. Nothing else in the file is exempt: a real use of the
    name anywhere, this file included, still counts.
    """
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets = [t for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            targets = [node.target]
        else:
            continue
        if not any(t.id == "_PROSE_ONLY_DEF_BASELINE" for t in targets):
            continue
        for elt in ast.walk(node.value):
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                ids.add(id(elt))
    return ids


def _doc_mentions(texts: dict[str, str], doc_texts: dict[str, str],
                  ) -> tuple[Counter[str], Counter[str]]:
    """(code, prose) name counters over the two corpora.

    CODE is every way a definition is reached that is not prose: an identifier,
    an attribute, an import (including `X as Y`), a keyword or parameter name,
    and any string literal that is not a docstring - which is what keeps
    `__all__` entries, registry keys and `getattr("name")` lookups counted, and
    what `_ratchet_literal_ids` takes back out for this file's own ratchet,
    whose entries name the debt rather than read it.
    PROSE is a comment, a docstring, or the whole text of a tracked doc file.
    """
    code: Counter[str] = Counter()
    prose: Counter[str] = Counter()
    for src in texts.values():
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        docstrings = _docstring_ids(tree)
        metadata = _ratchet_literal_ids(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                code[node.id] += 1
            elif isinstance(node, ast.Attribute):
                code[node.attr] += 1
            elif isinstance(node, ast.keyword) and node.arg:
                code[node.arg] += 1
            elif isinstance(node, ast.alias):
                code[node.name] += 1        # `from m import NAME`
                if node.asname:
                    code[node.asname] += 1
            elif isinstance(node, ast.arg):
                code[node.arg] += 1
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                if id(node) in metadata:
                    continue        # a ratchet records a name, it never reads one
                (prose if id(node) in docstrings else code).update(
                    re.findall(r"\w+", node.value))
        try:
            for tok in tokenize.generate_tokens(io.StringIO(src).readline):
                if tok.type == tokenize.COMMENT:
                    prose.update(re.findall(r"\w+", tok.string))
        except tokenize.TokenError:
            continue
    for src in doc_texts.values():
        prose.update(re.findall(r"\w+", src))
    return code, prose


def _top_level_definitions(texts: dict[str, str]):
    """Yield `(relative path, lineno, name, is_constant)` for every top-level
    definition the censuses above have in scope.

    One walk, shared by the census below and by its ratchet's staleness check:
    top level only, ALL_CAPS required for an assignment to count, and inside
    tests/ the names pytest reaches without ever spelling them out (`test_*`,
    hooks, autouse fixtures) left out.
    """
    for rel, src in sorted(texts.items()):
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue        # a file Python will not parse is the import
                            # guards' business, not this one's
        in_tests = rel.startswith("tests/")
        for node in tree.body:
            candidates: list[tuple[str, bool]] = []
            if isinstance(node, ast.ClassDef):
                candidates.append((node.name, False))
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if in_tests and (_framework_name(node.name)
                                 or _is_autouse_fixture(node)):
                    continue            # pytest runs these without naming them
                candidates.append((node.name, False))
            elif isinstance(node, ast.Assign):
                candidates.extend((t.id, True) for t in node.targets
                                  if isinstance(t, ast.Name))
            elif (isinstance(node, ast.AnnAssign)
                  and isinstance(node.target, ast.Name)):
                candidates.append((node.target.id, True))
            for name, is_constant in candidates:
                if is_constant and not _is_constant_name(name):
                    continue
                yield rel, node.lineno, name, is_constant


def _prose_only_definitions(texts: dict[str, str], doc_texts: dict[str, str],
                            baseline: set[str] = frozenset(),
                            ) -> list[tuple[str, int, str]]:
    """Top-level definitions whose every mention outside their own `class`/`def`
    /assignment line is prose: a comment, a docstring or a tracked doc.

    Same scope as the censuses above (see `_top_level_definitions`) and the same
    fail-safe direction: a name reached any other way at all is left alone, so
    this can under-report a documented-but-dead definition but never convicts a
    live one.

    Returns [(relative path, lineno, name)], sorted.
    """
    code, prose = _doc_mentions(texts, doc_texts)
    found: list[tuple[str, int, str]] = []
    for rel, lineno, name, is_constant in _top_level_definitions(texts):
        if name in baseline:
            continue
        # A constant's own assignment is one code mention of itself; a class or
        # function definition contributes none.
        if code[name] > (1 if is_constant else 0):
            continue
        if prose[name] < 1:
            continue
        found.append((rel, lineno, name))
    return sorted(found)


# The shipped package: the only tree this census accuses. Everything else at the
# repo root is reached by PATH rather than by name (see _fully_dead_files).
SHIPPED_PREFIX = "farever_companion/"


def _owned_bindings(tree: ast.Module) -> list[str]:
    """Every top-level name the three censuses above own in one file: classes,
    functions, and the ALL_CAPS assignments `_is_constant_name` accepts.

    Deliberately the union of their scopes and nothing more. Module state they
    never look at - a dunder, a lowercase name, an assignment inside a function
    - cannot make a file dead, so counting it here would only ever spare a file
    that is in fact dead.
    """
    names: list[str] = []
    for node in tree.body:            # top level only, as in every census
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            names.append(node.name)
            continue
        if isinstance(node, ast.Assign):
            targets = [t for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            targets = [node.target]
        else:
            continue
        names += [t.id for t in targets if _is_constant_name(t.id)]
    return names


def _fully_dead_files(texts: dict[str, str],
                      orphan_baseline: set[str] = frozenset(),
                      constant_baseline: set[str] = frozenset(),
                      file_baseline: set[str] = frozenset(),
                      ) -> list[tuple[str, list[str]]]:
    """Modules a ratchet is the only thing keeping alive.

    The two lists above record names, and nothing reads a list - so a file
    whose every way in is one of those names outlives both censuses: the
    ``def`` lines are the only mention of themselves (the baseline entry counts
    as the second), and whatever private helpers they call are read by nothing
    else. ``geo/critters.py`` was the worked example - ``resolve_spawner_unit``
    was ratcheted and ``_critter_spawners`` was called by that dead function and
    by nobody else - and the whole file went when those two did.

    The rule, in the same blunt spirit as the censuses above - a mention is any
    occurrence of the name anywhere, comments and docstrings included:

    * it is a module inside the shipped package. ``tests/``, the root scripts
      and ``build_tools/`` are reached by PATH (pytest collects ``test_*.py``,
      the ``.bat`` files run ``run.py`` and ``compiler.py``), and this census
      only understands names, so it has no business accusing them.
    * it is not ``__init__.py``: that runs for any import of its package.
    * it owns at least one name, and at least one of those is ratcheted (in
      either baseline). Without a ratcheted name there is nothing to start
      from, so the name censuses above are the right place for the verdict.
    * every other name it owns is mentioned nowhere outside the file - those
      are the helpers only the dead names reach.
    * nothing names the file itself, by basename or by dotted path. A file can
      be reached without its names ever appearing, which is exactly how the
      dev icon page and the runtime-string overlays pull their modules in.

    Returns [(relative path, ratcheted names)], sorted. It can under-report a
    dead file (a name two files define between them cannot be attributed by
    name alone) but never convicts a live one.
    """
    # This file is left out of BOTH halves of the read test, and it has to be.
    # It stores the ratchet entries - a dotted `_DEAD_FILE_BASELINE` entry is
    # itself a mention of the path it names, which would make every ratcheted
    # file look reachable-by-path and the ratchet self-defeating - and it
    # discusses these modules in prose: this very docstring names the private
    # helper the deleted `geo/critters.py` was dead for, and that one sentence
    # was enough to keep the file alive in the first draft of this census. It
    # cannot import a shipped module at runtime either, so neither half is a
    # reader.
    self_rel = Path(__file__).resolve().relative_to(ROOT).as_posix()

    total: Counter[str] = Counter()
    per_file: dict[str, Counter[str]] = {}
    for rel, src in texts.items():
        counts = Counter(re.findall(r"\w+", src))
        per_file[rel] = counts
        if rel != self_rel:
            total.update(counts)

    dead: list[tuple[str, list[str]]] = []
    for rel, src in sorted(texts.items()):
        if not rel.startswith(SHIPPED_PREFIX):
            continue
        if rel.rsplit("/", 1)[-1] == "__init__.py":
            continue
        dotted = rel[:-3].replace("/", ".")
        if dotted in file_baseline:
            continue        # known debt, see the list above
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        owned = _owned_bindings(tree)
        entrance = sorted(n for n in owned
                          if n in orphan_baseline or n in constant_baseline)
        if not entrance or not owned:
            continue
        counts = per_file[rel]
        if any((total[n] - counts[n]) > 0 for n in owned if n not in entrance):
            continue        # a name here is read from another file: this file lives
        leaf = rel.rsplit("/", 1)[-1]
        if any(leaf in text or dotted in text
               for other, text in texts.items()
               if other != rel and other != self_rel):
            continue        # reached by path, whatever its names say
        dead.append((rel, entrance))
    return dead


def _literal_all(tree: ast.Module) -> tuple[int, list[str]] | None:
    """`(line, names)` of a module's literal `__all__`, or None when it
    declares none (or computes one).

    None and `[]` are different answers, which is why this returns the line as
    well as the names: a computed `__all__` (`settings/_shared.py` builds it
    from `globals()`) has no list to read at all, while `__all__ = []` is a hub
    declaring that it exports nothing. Both `__all__` guards below wait for a
    non-None answer before judging a module.
    """
    for node in tree.body:
        if not isinstance(node, ast.Assign) or not any(
                isinstance(t, ast.Name) and t.id == "__all__"
                for t in node.targets):
            continue
        try:
            values = ast.literal_eval(node.value)
        except (ValueError, SyntaxError, TypeError):
            return None             # computed, not a list of names
        if isinstance(values, (list, tuple)):
            return node.lineno, [str(v) for v in values]
        return None
    return None


def _unresolved_all_names(text: str) -> list[tuple[int, str]]:
    """`[(line, name)]` for every entry of a literal `__all__` that the module
    never binds at top level (as an import, def, class or assignment).

    Resolved statically on purpose: importing every module that exports names
    would drag Qt, config paths and import side effects into a hygiene guard.
    A computed `__all__` (`settings/_shared.py` builds it from `globals()`) has
    no list to check and is left alone; a module with no `__all__` at all is
    not this guard's business either.
    """
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    declared = _literal_all(tree)
    if declared is None:
        return []
    line, names = declared

    bound: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            bound.add(node.name)
        elif isinstance(node, ast.Import):
            bound.update(a.asname or a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            bound.update(a.asname or a.name for a in node.names)
        elif isinstance(node, ast.Assign):
            bound.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            bound.add(node.target.id)
    return [(line, name) for name in names if name not in bound]


def _undeclared_all_imports(text: str) -> list[tuple[int, str]]:
    """`[(line, name)]` for every top-level import of a module that declares a
    literal `__all__`, where the name is neither exported nor used anywhere in
    the module.

    The mirror of `_unresolved_all_names`, and the reason a hub stays honest:
    pyflakes honours `__all__`, so a name the repo reaches THROUGH a hub but
    that the hub never declares reads as an unused import - true enough to be
    deleted by the next sweep, one `git rm` away from breaking a live consumer.
    Declaring every re-export is therefore not bookkeeping; it is what makes
    "unused import" mean something again in exactly the files that are full of
    deliberate ones.

    Blunt on purpose, like the censuses above: a name mentioned anywhere else in
    the module - a call, an annotation, a comment, a docstring - counts as used,
    so this under-reports rather than convicting a live name, and a name bound
    twice for one use is in the same category. `from .x import *` binds nothing
    by name, an import whose statement carries `# noqa` was declared
    intentional by its author, `from __future__ import ...` binds no name at
    all, and a computed `__all__` has no list to ask whether a name is in - all
    four are skipped.
    """
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    declared = _literal_all(tree)
    if declared is None:
        return []
    exported = set(declared[1])

    bindings: list[tuple[int, str]] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            names = [a.asname or a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.module == "__future__":
                continue
            names = [a.asname or a.name for a in node.names]
        else:
            continue
        if "noqa" in (ast.get_source_segment(text, node) or ""):
            continue
        bindings += [(node.lineno, n) for n in names if n != "*"]

    counts = Counter(re.findall(r"\w+", text))
    return [(line, name) for line, name in bindings
            if name not in exported and counts[name] <= 1]


def _relative_package(rel: str, level: int, pkg_name: str) -> list[str]:
    """The package parts a relative import's leading dots resolve against.

    One dot means the directory the file lives in - the same thing whether the
    file is `core/dps_data.py` or `core/__init__.py`, since `__init__` is a
    file like any other here - and every extra dot walks one package up.

    Sources are keyed by repo path, so the package name comes off first (the
    same key `_tracked_sources` handed in), and the caller passes the name
    `pkg` carries rather than a second constant that could drift.
    """
    parts = rel.split("/")[:-1]                # the file's own directory
    if parts and parts[0] == pkg_name:
        parts = parts[1:]
    return parts[:max(0, len(parts) - (level - 1))]


def _module_exists(pkg: Path, parts: list[str]) -> bool:
    """A module is `<name>.py`, a package is `<name>/__init__.py`."""
    if not parts:
        return True
    p = pkg.joinpath(*parts)
    return p.with_suffix(".py").is_file() or (p / "__init__.py").is_file()


def _module_source(sources: dict[str, str], parts: list[str],
                   pkg_name: str) -> str | None:
    """The source of the module `parts` names - `X.py` or `X/__init__.py` - or
    None when the caller holds no source for it (an untracked module), in which
    case its names cannot be checked at all.

    `sources` is keyed by repo path (`farever_companion/...`), while `parts` are
    package-relative, so the package name goes back on for the lookup. It is a
    parameter rather than the literal because that is the whole convention this
    guard enforces elsewhere.
    """
    base = "/".join([pkg_name, *parts])
    for candidate in (f"{base}.py", f"{base}/__init__.py"):
        if candidate in sources:
            return sources[candidate]
    return None


def _module_names_unenumerable(text: str) -> bool:
    """True when the module's bound names cannot be read off its source.

    A star import brings in whatever the module it stars names (the settings
    pages' `from ._shared import *`), a module-level `__getattr__` can answer
    for any name at all, and `globals()`/`exec()` can bind one outright - in
    every case an unresolved name may still work at run time, so the caller
    must stay quiet. A module-level `match` counts too: its capture patterns
    bind names this collector does not walk. Blunt regexes on purpose, like the
    censuses: a match inside a comment or a function trips them, which is an
    under-report rather than a wrong accusation.
    """
    return (re.search(r"^\s*from\s+[\w.]+\s+import\s+\*", text, re.M) is not None
            or re.search(r"^def __getattr__\(", text, re.M) is not None
            or re.search(r"^[ \t]*match\s+.*:\s*$", text, re.M) is not None
            or re.search(r"\b(globals|exec)\s*\(", text) is not None)


def _collect_bindings(stmts, out: set[str]) -> None:
    """Add every name `stmts` binds at module level to `out`.

    Descends into the conditional wrappers that still execute at import time
    (if/try/for/with/while), because that is how this codebase guards an
    optional import or an `if TYPE_CHECKING` one, while a def/class body holds
    LOCALS and is deliberately not descended into - a function-local name is
    exactly what a mention-anywhere check would wrongly accept.
    """
    for node in stmts:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                out.update(n.id for n in ast.walk(target) if isinstance(n, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            out.add(node.target.id)
        elif isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name):
            out.add(node.target.id)
        elif isinstance(node, ast.Import):
            out.update(a.asname or a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            out.update(a.asname or a.name for a in node.names if a.name != "*")
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            out.update(n.id for n in ast.walk(node.target)
                       if isinstance(n, ast.Name))
            _collect_bindings(node.body, out)
            _collect_bindings(node.orelse, out)
        elif isinstance(node, ast.With):
            for item in node.items:
                if item.optional_vars is not None:
                    out.update(n.id for n in ast.walk(item.optional_vars)
                               if isinstance(n, ast.Name))
            _collect_bindings(node.body, out)
        elif isinstance(node, ast.Try):
            for block in (node.body, node.orelse, node.finalbody,
                          *[h.body for h in node.handlers]):
                _collect_bindings(block, out)
        elif isinstance(node, ast.If):
            _collect_bindings(node.body, out)
            _collect_bindings(node.orelse, out)
        elif isinstance(node, ast.While):
            _collect_bindings(node.body, out)
            _collect_bindings(node.orelse, out)


def _module_bindings(text: str) -> set[str] | None:
    """The module-level names `text` binds, or None when they cannot be
    enumerated (see `_module_names_unenumerable`)."""
    if _module_names_unenumerable(text):
        return None
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return None
    names: set[str] = set()
    _collect_bindings(tree.body, names)
    return names


def _unresolved_relative_imports(
        pkg: Path, sources: dict[str, str]) -> list[tuple[str, int, str, str]]:
    """`[(rel, lineno, spelling, why)]` for every relative import in `sources`
    that the package cannot satisfy - `why` being `"module"` (the target is
    not a module the package has) or `"name"` (it is, but does not mention the
    name asked of it).

    One entry per statement, so `from . import a, b` with both names missing
    reads as one problem, not two. The name half accepts a name the target
    binds at module level, a name that is a submodule of the target, and every
    name of a target whose own names cannot be enumerated (`from ._shared import
    *`, a module `__getattr__`, `globals`/`exec`, or a target the caller holds
    no source for). It is blunt in the same direction as the censuses: a name
    mentioned only by a comment or a docstring is NOT accepted any more (that
    is what let a moved module hide behind its own package docstring), but a
    construct the collector does not know is, so it can miss a real one and
    cannot convict a live name.
    """
    problems: list[tuple[str, int, str, str]] = []
    for rel, text in sorted(sources.items()):
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue        # a file Python will not parse is another guard's job
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or not node.level:
                continue
            parts = _relative_package(rel, node.level, pkg.name)
            spelling = ("from " + "." * node.level + (node.module or "")
                        + " import " + ", ".join(a.name for a in node.names))
            target = parts + (node.module.split(".") if node.module else [])
            if not _module_exists(pkg, target):
                problems.append((rel, node.lineno, spelling, "module"))
                continue
            target_src = _module_source(sources, target, pkg.name)
            if target_src is None:
                continue
            bindings = _module_bindings(target_src)
            if bindings is None:
                continue
            unresolved = [
                a.name for a in node.names
                if a.name != "*"
                and not _module_exists(pkg, target + a.name.split("."))
                and a.name not in bindings
            ]
            if unresolved:
                problems.append((rel, node.lineno, spelling, "name"))
    return problems


def _relative_import_count(sources: dict[str, str]) -> int:
    """How many relative imports the resolver above is handed to look at.

    The guard asserts a floor on this: 883 across the package when it was
    added, 235 of them deferred inside a function or a try/except - which is
    exactly the shape that fails quietly - so a walk that stopped matching
    statements would otherwise pass the guard vacuously.
    """
    total = 0
    for text in sources.values():
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        total += sum(1 for node in ast.walk(tree)
                     if isinstance(node, ast.ImportFrom) and node.level)
    return total


# --- baked shim keys -------------------------------------------------------
# The compiler embeds a compressed JSON payload in each raw_*.py shim. Keys
# inside that literal are constants by another name - named once in
# compiler.py, shipped to every user - and NO linter can see them, which is
# how 482 `atlas_x`/`atlas_y` row keys shipped unread. This census is the
# key-shaped twin of the dead-constant one above: a key counts as read when
# any tracked file outside its own shim and the compiler mentions it by name
# (a writer, a test, a dev tool, even a comment all count), so it can only
# under-report, never convict. What it cannot see is scoped explicitly:
_SHIM_STEMS = ("raw_codex", "raw_units", "raw_items", "raw_skills", "raw_data")

# Children of these containers are read by ID LOOKUP, not by name - the same
# reason the import guard stays quiet on a star import. `atlas` maps a sprite
# id to its rectangle (data/atlas.py), and no key census can enumerate ids.
_ATLAS_BLOBS = {"atlas", "ATLAS_DATA", "ICONS", "aliases"}

# The provenance stamp a human reads (compiler.py:_payload_meta), not code.
_META_KEYS = {"__meta__", "compiler", "data_version"}

# raw_skills carries TWO sections and both are contracts the frozen build
# depends on: `skill_rows` is baked VERBATIM from the loose skill.json (the
# app has no loose sheet to fall back on - compiler.py says so, and
# tests/test_item_drops.py::test_weapon_upgrade_ladders_survive_the_frozen_build
# fails if either side trims), while the trimmed `skills` mirror exists only
# to feed the ::token:: description resolver, which reads `props[name]`,
# `vars`, and `texts.refs` BY NAME derived from description text
# (data/skills.py) - unenumerable, like the atlas blobs.
_SKILLS_CONTRACT = {"skills", "skill_rows"}

# The zero-reader keys the census found the day it was added (2026-09-27), on
# the same ratchet as the orphans and constants above: 23 names nothing reads
# would be a permanent warning block nobody reads. Every entry carries its
# finding so the next session can act instead of re-derive.
_BAKED_KEY_BASELINE = {
    # lootTable.loot[] entries: the compiler deliberately projects each loot
    # entry to item/lootTable/minLvl/maxLvl/conds, but nothing ever reads the
    # level gate - `minLvl` (and `maxLvl`, ratcheted with it if the reader
    # never appears) is selection without a consumer.
    "minLvl",
    # rarity sheet `props` subkeys: the sheet's only consumer reads
    # props.gearUpgrades (data/items/stats.py::_rarity_upgrade_caps). The
    # compiler comment claims props.generationChance "feeds the loot
    # predictor's rarity roll odds" - no loot predictor exists in the tree;
    # the comment describes a feature that was never written.
    "iLevelBonus", "generationChance", "minLevel", "maxLevel",
    "sellPriceFactor",
    # stamped on EVERY standard-sheet row (compiler.py resolves the atlas
    # fallback), but the strip pass `_prune_entries` lists only
    # units/items/skills/lootTable - so the `constant` sheet's 627 rows ride
    # with `gfx: None` forever. Fix is one word in that list, or stop
    # stamping sheets the atlas never covers.
    "gfx",
    # HashLink encoded-value shapes under constant.v: rows are read only via
    # GearUpgrades.Materials and the scalar `v.float` constants
    # (data/items/stats.py); these type-tag keys have no named reader.
    "__enum__", "__value", "_hx_index", "bool", "curve", "enchant",
    "float", "floatMap", "floats", "itemTiers", "proportion", "quantity",
    "repartition", "resources", "scrapAptitudes", "scrapQuantity",
    # constant.json's HeroRefillCharges row (arrived with the 2026-10
    # sheet sync): a per-item map of refill charges (RefillableFlask=3,
    # SparkCube=1) with no consumer - the app tracks no flask-refill
    # feature, so nothing ever asks how many charges an item holds.
    # Ratcheted with the sync that brought the row in; wire a reader
    # (data/soulwell.py's neighbours) if that feature lands.
    "charges", "heroRefillCharges",
}


def _baked_key_index(sources: dict[str, str]) -> dict[str, set[str]]:
    """Quoted-string contents -> files mentioning them, for every source
    EXCEPT the compiler (the writer never counts as a reader) and the shims
    themselves (a key does not read itself)."""
    shim_paths = {f"farever_companion/data/{s}.py" for s in _SHIM_STEMS}
    index: dict[str, set[str]] = {}
    for rel, text in sources.items():
        if rel == "compiler.py" or rel in shim_paths:
            continue
        for tok in set(re.findall(r"[\"']([^\"'\n]{1,80})[\"']", text)):
            index.setdefault(tok, set()).add(rel)
    return index


def _baked_keys(shim: str, text: str, compiler_literals: set[str]) -> dict[str, set]:
    """`{key: {container chains}}` for one shim payload.

    A key is in the census domain when it occurs as a dict key transitively
    INSIDE a list (row fields and their nested children) or when the compiler
    names it as a literal at any depth (section names, sheet projections).
    The chain records the containers above the key, which is what the escape
    hatches test. Exec of the shim is how the census sees inside the compressed
    payload; a shim that will not decode shrinks the domain, and the caller's
    floor assertion catches that rather than passing vacuously.
    """
    ns: dict = {}
    exec(compile(text, f"<shim:{shim}>", "exec"), ns)
    data = ns.get("DATA")
    found: dict[str, set] = {}

    def walk(node, chain, in_list):
        if isinstance(node, dict):
            for k, v in node.items():
                if isinstance(k, str) and k:
                    if in_list or k in compiler_literals:
                        found.setdefault(k, set()).add(chain)
                    walk(v, chain + (k,), in_list)
                else:
                    walk(v, chain, in_list)
        elif isinstance(node, list):
            for v in node:
                walk(v, chain, True)

    walk(data, (), False)
    return found


def _unresolved_baked_keys(
        sources: dict[str, str]) -> list[tuple[str, str, str]]:
    """`[(shim, key, where)]` for baked keys nothing reads and no exemption
    covers, with `where` a sample container chain for the report."""
    index = _baked_key_index(sources)
    compiler_text = sources.get("compiler.py", "")
    compiler_literals = set(re.findall(r"[\"']([^\"'\n]{1,80})[\"']", compiler_text))
    problems: list[tuple[str, str, str]] = []
    for stem in _SHIM_STEMS:
        rel = f"farever_companion/data/{stem}.py"
        text = sources.get(rel)
        if text is None:
            continue
        found = _baked_keys(stem, text, compiler_literals)
        for key, chains in sorted(found.items()):
            if key in _META_KEYS or key in _BAKED_KEY_BASELINE:
                continue
            live = [c for c in chains
                    if (c and c[-1] not in _ATLAS_BLOBS
                        and not (stem == "raw_skills"
                                 and c[0] in _SKILLS_CONTRACT))]
            if live and not (index.get(key) or set()) - {rel}:
                where = ".".join(str(p) for p in live[0]) or "<top level>"
                problems.append((stem, key, where))
    return problems


def _baked_key_domain_size(sources: dict[str, str]) -> int:
    """How many baked keys the census actually walked, for the vacuity floor."""
    compiler_literals = set(re.findall(r"[\"']([^\"'\n]{1,80})[\"']",
                                       sources.get("compiler.py", "")))
    total = 0
    for stem in _SHIM_STEMS:
        text = sources.get(f"farever_companion/data/{stem}.py")
        if text is not None:
            total += len(_baked_keys(stem, text, compiler_literals))
    return total


def _ignored_root_files(root: Path) -> set[str]:
    """Names of repo-root FILES git already ignores for this checkout.

    `*.log` is in .gitignore and the developer launcher (run-log.bat) writes
    run.log at the root on every run, so without this the guard reports the
    log file it just asked for, in every single run - which is how a warning
    turns into wallpaper. An ignored name is only a licence to be output: the
    caller still checks tracked-ness, and a stray FOLDER is never exempted.

    Quiet on any git trouble (missing git, not a checkout, no ignore rules):
    the guard reports more, never fewer, strays when it cannot ask git.
    """
    names = [p.name for p in root.iterdir() if p.is_file()]
    if not names:
        return set()
    try:
        r = subprocess.run(
            ["git", "-C", str(root), "check-ignore", "-z", "--stdin"],
            input="\0".join(names) + "\0", capture_output=True, text=True,
            timeout=60)
    except (OSError, subprocess.SubprocessError):
        return set()
    if r.returncode not in (0, 1):      # 1 = "nothing ignored", 128 = no repo
        return set()
    return {p for p in r.stdout.split("\0") if p}


def _fs_key(rel: str) -> str:
    """`rel` folded the way the filesystem compares it.

    Windows (and a default macOS) resolve `Run.bat` and `run.bat` to ONE file,
    so a tracked path and an on-disk entry must be compared through
    `os.path.normcase` - identity on a case-sensitive filesystem - rather than
    as raw strings. Raw, the committed developer launcher `Run.bat` came back as
    a stray named `run.bat` in every single summary the moment a tool wrote the
    file out lowercase, which is precisely the wallpaper this guard exists to
    avoid.
    """
    return os.path.normcase(rel.replace("/", os.sep))


def _root_strays(root: Path, tracked: set[str]) -> list[Path]:
    """Repo-root entries that are neither committed content, build output, nor
    allowed session scratch.

    The discriminator is git: an entry is legitimate if it *is* tracked, if any
    tracked path lives under it (`farever_companion/`, `tests/`, ...), or if it
    is regenerated build output. That is exactly the question a stray raises -
    "is this a thing this repo owns?" - and it needs no hand-maintained list of
    source folders that would rot as the tree grows.

    "Is this tracked?" is asked through `_fs_key`, because the filesystem may
    answer a different question than the index spelling of a path does (see
    there).

    Names starting with "." are tool/IDE config (`.idea`, `.venv`, `.github`);
    they are never scratch output, so they are out of scope, and so is any file
    git already ignores (`_ignored_root_files`).

    An untracked folder is reported as the FOLDER (one line naming what to
    move), not as each file inside it - a `tmp_preview/` of 40 screenshots is
    one piece of scratch, and 40 warnings would bury the rest of the summary.
    The one exception is a folder that merely *contains* the allowed scratch
    location: `ai/` is walked one level so its loose files get named
    individually, because `ai/workspace/` inside it is allowed by design.
    """
    scratch = root / SCRATCH_DIR
    out: list[Path] = []
    ignored = {_fs_key(n) for n in _ignored_root_files(root)}
    folded = {_fs_key(t) for t in tracked}

    def visit(entry: Path) -> None:
        if entry == scratch or scratch in entry.parents:
            return                          # allowed session scratch
        key = _fs_key(entry.relative_to(root).as_posix())
        if key in folded or any(t.startswith(key + os.sep) for t in folded):
            return                          # committed source, or a source dir
        if entry.is_file():
            if _fs_key(entry.name) not in ignored:
                out.append(entry)
            return
        inside_scratch_parent = scratch in entry.rglob("*") or entry in scratch.parents
        if not inside_scratch_parent:
            out.append(entry)               # a whole untracked folder = one stray
            return
        try:
            for sub in sorted(entry.iterdir()):
                if sub.name.startswith(".") or sub.name in GENERATED_ROOT_NAMES:
                    continue
                visit(sub)
        except OSError:
            out.append(entry)

    for entry in sorted(root.iterdir()):
        if entry.name.startswith(".") or entry.name in GENERATED_ROOT_NAMES:
            continue
        visit(entry)
    return out


def _stray_size(stray: Path) -> tuple[int, int]:
    """(file count, total bytes) for a stray entry - a file counts as itself.

    Skips cache/build noise inside the entry so the reported size reflects what
    a person would actually move.
    """
    if stray.is_file():
        try:
            return 1, stray.stat().st_size
        except OSError:
            return 1, 0
    files = 0
    total = 0
    try:
        for sub in stray.rglob("*"):
            if not sub.is_file() or SKIP_DIRS & set(sub.relative_to(stray).parts):
                continue
            files += 1
            try:
                total += sub.stat().st_size
            except OSError:
                pass
    except OSError:
        pass
    return files, total


# A package's `__init__` docstring is the only place the import graph is
# written down in prose. `core/` must not import Qt, `ui/` must not read the
# process, `geo/` must not import `core`, and `control_panel.py` must be the
# only importer of a page package - each of those is a rule a new contributor
# can only learn by reading every call site, and each has been broken in a way
# no linter sees. The runtime of the code is honest; the failure mode is a
# docstring that drifts out of date (naming a module that moved) or a NEW
# package shipping a bare one-liner, so that is what is pinned.
#
# Scope is every package in `farever_companion/`, layer packages and nested
# ones alike. The nested set is not a second class: `ui/overlays/` and
# `ui/pages/` had NO docstring at all, and `ui/overlays/`'s is the one file
# whose emptiness the frozen build depends on - it is why `FareverPal.spec` has
# to carry every overlay by hand. A file that reads as deliberate because it
# is empty is worse than one that was never written.
_DOC_MIN_LINES = 4
_PKG_INIT = re.compile(r"farever_companion(?:/[a-z_0-9]+)+/__init__\.py")
_BACKTICKED = re.compile(r"`([^`\n]+)`")
# The page packages list their residents as bare `- page.py  role` bullets
# rather than in backticks, so a backtick-only scan would convict the four
# best docstrings in the tree. Any filename shape counts as a citation.
_FILENAME = re.compile(r"\b[\w]+(?:/[\w]+)*\.py\b")
_IMPORT_WORD = re.compile(r"import", re.IGNORECASE)


def _doc_citations(doc: str) -> list[str]:
    """Every token in a docstring that could be naming a module."""
    return _BACKTICKED.findall(doc) + _FILENAME.findall(doc)


def _doc_resident_resolves(token: str, prefix: str,
                           sources: dict[str, str]) -> bool:
    """True when a backticked token names a module that really exists.

    Resolution is done against `sources`, never the filesystem, so the pin
    below can drive it from a synthetic tree. Each token is tried as written
    and with its dots read as path separators, under the package's own
    directory and its parent's, and each spelling is accepted as a module, as
    a package, or verbatim - so `catalog.py`, `chests`, `dps` and
    `constants` all resolve the way a reader would expect, from anywhere in
    the tree. The verbatim form matters: the page packages list their
    residents by filename (`page.py`, `grid.py`), and appending `.py` to
    those asks for a file called `page.py.py`.
    """
    token = token.strip().rstrip("/")
    if not token or " " in token or token.startswith("."):
        return False
    bases = [prefix]
    parent = prefix.rsplit("/", 1)[0]
    if parent.startswith("farever_companion"):
        bases.append(parent)
    for base in bases:
        for spelling in (token, token.replace(".", "/")):
            probe = f"{base}/{spelling}"
            if (probe in sources
                    or f"{probe}.py" in sources
                    or f"{probe}/__init__.py" in sources):
                return True
    return False


def _subpackage_doc_gaps(sources: dict[str, str]) -> list[str]:
    """One message per package whose `__init__` docstring falls short.

    Three gaps, in the order they were all true at once on 2026-09-27: too
    thin to say anything (`geo`, `core`, `data`, `ui`, `ui/combat`,
    `ui/overlays/dps` and `ui/pages/server` were one-liners, and
    `ui/overlays/` and `ui/pages/` had nothing at all), naming no resident so
    a reader cannot tell what is in the package, and stating no import
    direction - which layer imports this, and which way round. The direction
    half is checked as the bare word "import" rather than a phrase list: any
    sentence carrying the claim uses it, and a keyword soup would rot the
    first time someone rewords a docstring.
    """
    problems: list[str] = []
    for rel in sorted(sources):
        m = _PKG_INIT.fullmatch(rel)
        if not m:
            continue
        prefix = rel[: -len("/__init__.py")]
        text = sources[rel]
        try:
            doc = ast.get_docstring(ast.parse(text, filename=rel)) or ""
        except SyntaxError:
            problems.append(f"{rel}: does not parse")
            continue
        body = [line for line in doc.splitlines() if line.strip()]
        if len(body) < _DOC_MIN_LINES:
            problems.append(
                f"{rel}: {len(body)}-line docstring, under the {_DOC_MIN_LINES} "
                f"needed to name what the package holds and which way the "
                f"imports run")
            continue
        if not any(_doc_resident_resolves(t, prefix, sources)
                   for t in _doc_citations(doc)):
            problems.append(
                f"{rel}: names no resident that exists - cite at least one "
                f"module of {prefix}/ (or of its parent) so a reader can tell "
                f"what the package holds")
        if not _IMPORT_WORD.search(doc):
            problems.append(
                f"{rel}: states no import direction - say who imports this "
                f"and which way round the dependency runs")
    return problems


# The spec's `hiddenimports` is a hand-maintained list whose entire job is to
# cover modules PyInstaller's static analysis cannot see, and it is the one
# declaration in the repo with no test at all - which is how it kept a module
# name that stopped existing. `ui/overlays/__init__.py` is empty, so the
# overlays have no static edge for the analyser to follow, and the list is the
# only thing standing between a frozen build and five missing HUDs.
#
# The failure is silent by construction: `OverlayManager.request()` wraps the
# import in a try/except and reverts the toggle with a log line, so a stale
# entry ships as "the DPS meter quietly stopped opening for the people who had
# it on at launch" rather than as a build error.
SPEC_NAME = "FareverPal.spec"
OVERLAY_MANAGER = "farever_companion/ui/overlay_manager.py"
OVERLAY_PKG = "farever_companion.ui.overlays"
PKG_PREFIX = "farever_companion."


def _spec_hiddenimports(tree: ast.Module) -> list[str] | None:
    """The `hiddenimports=[...]` literal of the spec's Analysis() call.

    None when the spec has no such call or the list is not a literal - either
    way the caller must stop rather than pass vacuously.
    """
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call)
                and getattr(node.func, "id", None) == "Analysis"):
            continue
        for kw in node.keywords:
            if kw.arg == "hiddenimports":
                try:
                    found = ast.literal_eval(kw.value)
                except ValueError:
                    return None
                return [str(m) for m in found] if isinstance(found, list) else None
    return None


def _overlay_registry_modules(text: str) -> set[str] | None:
    """The module names in `overlay_manager._OVERLAY_SOURCES`, read off the AST.

    `overlay_manager` pulls in PySide6 and the settings stack at import time, so
    the registry is read statically rather than imported. The values are
    `("<module>", "<Class>")` tuples and only the module half matters here; the
    class half is already covered by the relative-import guard, which resolves
    `from .overlay import DpsOverlay` inside the package. The literal itself is
    parsed once, by `_overlay_registry_entries` below, so this and the ownership
    guard can never be reading two different registries.
    """
    entries = _overlay_registry_entries(text)
    if entries is None:
        return None
    return {f"{OVERLAY_PKG}.{mod}" for mod, _ in entries.values()}


def _unresolvable_hiddenimports(pkg: Path, hidden: list[str]) -> list[str]:
    """Entries naming a module the package does not contain.

    Only `farever_companion.*` is resolved. Third-party entries (`farever_native`
    is built by maturin and may legitimately be absent from a bare checkout) are
    the operator's business, and resolving them here would make the guard fail
    for a reason that has nothing to do with the app.
    """
    out = []
    for name in hidden:
        if not name.startswith(PKG_PREFIX):
            continue
        if not _module_exists(pkg, name[len(PKG_PREFIX):].split(".")):
            out.append(name)
    return out


def _uncovered_overlays(hidden: list[str], registry: set[str]) -> list[str]:
    """Overlay modules the runtime-string import needs and the spec omits."""
    return sorted(registry - set(hidden))


def test_every_hiddenimport_resolves_and_covers_the_overlay_registry():
    """Both directions of the spec's hiddenimports, in one guard.

    Forward: every `farever_companion.*` entry must be a module that exists, so
    a deleted one (`net/api.py`, or the DPS overlay after `dps_overlay.py`
    became `dps/overlay.py`) cannot linger as a name that resolves nowhere.

    Backward: every module `overlay_manager._OVERLAY_SOURCES` names must be
    listed, because that registry is the set of imports no static analysis will
    ever see. Forward alone is not enough - the stale `dps_overlay` entry
    shipped precisely because nothing asked whether the registry's five modules
    were the five modules in the list.
    """
    spec_path = ROOT / SPEC_NAME
    assert spec_path.is_file(), f"{SPEC_NAME} is gone; packaging is defined by it"
    hidden = _spec_hiddenimports(ast.parse(source_guard.read_text(spec_path, encoding="utf-8")))
    assert hidden, (
        f"{SPEC_NAME} has no literal hiddenimports=[] on its Analysis() call - "
        f"this guard cannot see what the build would ship, and the overlays "
        f"depend on that list entirely")
    assert len(hidden) >= 8, (
        f"{SPEC_NAME} lists only {len(hidden)} hiddenimports (8 when this guard "
        f"was added) - a list that lost its overlays reads as covered here")

    dead = _unresolvable_hiddenimports(PKG, hidden)
    assert not dead, (
        f"{SPEC_NAME} lists hiddenimports that name no module: {dead}. In a "
        f"frozen build these are silently useless - the module can never be "
        f"found - and if one replaced a real entry the HUD it protected goes "
        f"missing without a build error.")

    manager = _tracked_sources(_tracked_paths(ROOT) or set()).get(OVERLAY_MANAGER)
    assert manager is not None, f"{OVERLAY_MANAGER} is not tracked"
    registry = _overlay_registry_modules(manager)
    assert registry, (
        f"{OVERLAY_MANAGER}'s _OVERLAY_SOURCES could not be read statically; "
        f"without it the backward half of this guard would pass vacuously")
    assert len(registry) >= 5, (
        f"only {len(registry)} overlay modules found in _OVERLAY_SOURCES (5 "
        f"when this guard was added) - a partial read of the registry would "
        f"quietly shrink what this guard covers")

    missing = _uncovered_overlays(hidden, registry)
    assert not missing, (
        f"{SPEC_NAME} does not list {missing}, which overlay_manager imports by "
        f"RUNTIME STRING. PyInstaller's static analysis never sees them "
        f"(ui/overlays/__init__.py is empty), so a missing entry means that "
        f"overlay silently fails to build in the frozen exe - the toggle "
        f"reverts and the Activity Log carries the ModuleNotFoundError.")


def test_the_hiddenimport_detector_actually_detects(tmp_path):
    """Pin all four findings: a dead entry, a dropped overlay, a spec with no
    list, and the shape that must pass.

    A forward-only check would have shipped the stale `dps_overlay` entry
    happily, so the backward half is drilled on its own: a registry module the
    spec no longer names fires just as loudly as a name that resolves to
    nothing.
    """
    pkg = tmp_path / "farever_companion"
    (pkg / "ui" / "overlays" / "dps").mkdir(parents=True)
    (pkg / "data").mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    for rel in ("data/raw_data.py", "ui/overlays/__init__.py",
                "ui/overlays/entity_overlay.py", "ui/overlays/dps/__init__.py"):
        (pkg / rel).write_text("", encoding="utf-8")

    spec = ('a = Analysis(["run.py"], hiddenimports=[%s], excludes=[])\n')

    def hidden_list(*names):
        return spec % ", ".join(f'"{n}"' for n in names)

    good = [f"{PKG_PREFIX}ui.overlays.entity_overlay", f"{PKG_PREFIX}ui.overlays.dps",
            f"{PKG_PREFIX}data.raw_data", "farever_native"]
    assert _unresolvable_hiddenimports(pkg, good) == []
    assert _unresolvable_hiddenimports(pkg, [f"{PKG_PREFIX}ui.overlays.dps_overlay"]) \
        == [f"{PKG_PREFIX}ui.overlays.dps_overlay"]
    assert _unresolvable_hiddenimports(pkg, [f"{PKG_PREFIX}net.api"]) \
        == [f"{PKG_PREFIX}net.api"]
    # a third-party entry is never resolved here - maturin may not have run
    assert _unresolvable_hiddenimports(pkg, ["farever_native", "pygame"]) == []

    registry = {f"{OVERLAY_PKG}.entity_overlay", f"{OVERLAY_PKG}.dps"}
    assert _uncovered_overlays(set(good), registry) == []
    assert _uncovered_overlays([good[0], good[2]], registry) == [f"{OVERLAY_PKG}.dps"]

    manager = ("_OVERLAY_SOURCES = {\n"
               '    "entity": ("entity_overlay", "EntityOverlay"),\n'
               '    "dps": ("dps", "DpsOverlay"),\n}\n')
    assert _overlay_registry_modules(manager) == registry
    assert _overlay_registry_modules("x = 1\n") is None
    assert _overlay_registry_modules("_OVERLAY_SOURCES = {}\n") == set()

    # the spec shapes that would make the guard blind
    assert _spec_hiddenimports(ast.parse(spec % "")) == []
    assert _spec_hiddenimports(ast.parse("a = Analysis(['run.py'])\n")) is None
    assert _spec_hiddenimports(ast.parse("a = Analysis(['r.py'], hiddenimports=x)\n")) is None


# --- one home per HUD surface ----------------------------------------------
# The overlay-package classes and top-level builders deliberately used by MORE
# than one window: the shared HUD furniture, not a surface of their own.
# Everything else there belongs to exactly one window, which is what stops a
# second window from growing its own copy of a feature - the mistake that gave
# the Top DPS meter and the Test Dummy HUD a dummy test each.
_SHARED_HUD_SURFACES = {
    "_Section",            # the titled section box every HUD stacks its rows in
    "RowSpec",             # the row spec the dungeon and entity gatherers emit
    "_PlayerRowWidget",    # the meter's per-player row: the dummy HUD shows the same rows
    "_StatChip",           # the meter's channel chip: the dummy HUD mounts HEALING/TAKEN so its sustain reads like the meter's
    "_CaptureBadge",       # the title-bar capture badge, on the meter and the dummy HUD
    "_scroll_body",        # the scrolled body every HUD hangs its sections on
    "build_loot_specs",    # the loot rows, shared by the dungeon and entity HUDs
    "insert_into_titlebar",  # title-bar slotting, used by every badge
    # The meter and the dummy board open the same way, and must keep opening
    # the same way: overlays/dps/widgets.py is the library both are assembled
    # from, so its assembly helpers have two owners on purpose.
    "install_dense_chrome",     # the dense body margins both boards read numbers in
    "install_dps_sources",      # the model/settings/tracker handoff and both log sinks
    "install_titlebar_chrome",  # the flat title bar, rich text and heavy title font
    "aim_log_sinks",            # the tracker + damage Activity-Log wiring
    "adopt_live_tracker",       # the per-tick tracker re-adopt after a reattach
}


OVERLAYS_PREFIX = "farever_companion/ui/overlays"
_OVERLAYS_DEPTH = len(OVERLAYS_PREFIX.split("/"))


def _overlay_registry_entries(text: str) -> dict[str, tuple[str, str]] | None:
    """`_OVERLAY_SOURCES` as {key: (module, class)} - the class half included.

    The same static read as `_overlay_registry_modules` (importing
    `overlay_manager` pulls in PySide6 and the settings stack), but this caller
    needs to know WHICH class each module contributes, because the ownership
    walk below starts at that class's own bases.
    """
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return None
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == "_OVERLAY_SOURCES"
                   for t in node.targets):
            continue
        try:
            mapping = ast.literal_eval(node.value)
        except ValueError:
            return None
        if not isinstance(mapping, dict):
            return None
        entries: dict[str, tuple[str, str]] = {}
        for key, val in mapping.items():
            if (isinstance(key, str) and isinstance(val, tuple) and len(val) == 2
                    and all(isinstance(v, str) for v in val)):
                entries[key] = (val[0], val[1])
        return entries
    return None


def _overlay_defs(sources: dict[str, str]) -> dict[str, set[str]]:
    """Top-level name -> the overlay-package files that define it.

    Classes and functions alike: a HUD surface is whatever a window builds, and
    this package draws most of its sections with `*_render` functions rather
    than widgets, so a class-only census would leave the bigger half of the
    package unowned. Methods are not top level and so are never counted - a
    `build()` on two mixins is not two homes for one surface.
    """
    out: dict[str, set[str]] = {}
    for rel, src in sources.items():
        if not rel.startswith(f"{OVERLAYS_PREFIX}/"):
            continue
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for node in tree.body:
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                out.setdefault(node.name, set()).add(rel)
    return out


def _overlay_import_targets(rel: str, src: str) -> dict[str, str]:
    """Bound name -> the overlay-package-relative module it came from.

    Only RELATIVE imports that land inside the overlay package are resolved: the
    windows sit at the bottom of `ui/`, so `from ...overlay_base import
    OverlayWindow` leaves the package for a base that can never be a HUD surface,
    and a name we cannot place is a base we do not walk into. A module resolving
    to `""` is the package itself and is skipped, like an unresolvable name.
    """
    pkg = rel.split("/")[_OVERLAYS_DEPTH:-1]
    targets: dict[str, str] = {}
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return targets
    for node in ast.walk(tree):
        if not (isinstance(node, ast.ImportFrom) and node.level >= 1):
            continue
        up = node.level - 1
        if up > len(pkg):
            continue                       # climbs above the overlay package
        target = "/".join([*pkg[:len(pkg) - up],
                           *(node.module.split(".") if node.module else [])])
        if not target:
            continue
        for alias in node.names:
            targets[alias.asname or alias.name] = target
    return targets


def _overlay_module_file(target: str, sources: dict[str, str]) -> str | None:
    """`dps/dummy_ui` -> the file that module names, or None if not here."""
    for cand in (f"{OVERLAYS_PREFIX}/{target}.py",
                 f"{OVERLAYS_PREFIX}/{target}/__init__.py"):
        if cand in sources:
            return cand
    return None


def _overlay_class_file(mod: str, cls: str, sources: dict[str, str],
                        defs: dict[str, set[str]]) -> str | None:
    """The file defining `cls` for the window whose registry module is `mod`.

    A registry entry may name a package (`dps`) whose `__init__` only re-exports
    the class, so the definition is looked for in the package's own files when
    the module file itself does not hold it.
    """
    base = f"{OVERLAYS_PREFIX}/{mod}"
    owned = defs.get(cls, set())
    for cand in (f"{base}.py", f"{base}/__init__.py"):
        if cand in owned:
            return cand
    for rel in sorted(owned):
        if rel.startswith(f"{base}/"):
            return rel
    return None


def _overlay_window_files(sources: dict[str, str]) -> dict[str, tuple[str, str]] | None:
    """Overlay key -> (file, class) for its window, for the whole registry.

    None when the registry or the package is not in `sources` (not a checkout,
    or a caller with a partial tree); a key whose class cannot be found is left
    out, and the guard asserts the mapping is complete so one missing window
    cannot quietly shrink what it covers.
    """
    manager = sources.get(OVERLAY_MANAGER)
    if manager is None or f"{OVERLAYS_PREFIX}/__init__.py" not in sources:
        return None
    entries = _overlay_registry_entries(manager)
    if not entries:
        return None
    defs = _overlay_defs(sources)
    windows: dict[str, tuple[str, str]] = {}
    for key, (mod, cls) in entries.items():
        rel = _overlay_class_file(mod, cls, sources, defs)
        if rel is not None:
            windows[key] = (rel, cls)
    return windows


def _overlay_surface_files(start: str, cls: str, sources: dict[str, str]) -> set[str]:
    """Every file a window is BUILT from: its own, plus each mixin it inherits.

    This is the half that makes the census see a surface at all: the widgets of
    a shared mixin are instantiated in the MIXIN's file, so a walk that stopped
    at the window's own module would report one builder for a surface two
    windows render. Bases outside the overlay package (and classes whose file
    cannot be placed) simply end the walk.
    """
    seen: set[tuple[str, str]] = set()
    stack = [(start, cls)]
    while stack:
        rel, name = stack.pop()
        if (rel, name) in seen:
            continue
        seen.add((rel, name))
        src = sources.get(rel)
        if src is None:
            continue
        targets = _overlay_import_targets(rel, src)
        for base in _overlay_class_bases(src, name):
            target = targets.get(base)
            if target is None:
                continue
            cand = _overlay_module_file(target, sources)
            if cand is not None:
                stack.append((cand, base))
    return {rel for rel, _ in seen}


def _overlay_class_bases(src: str, cls: str) -> list[str]:
    """The base names in `class cls(...)` - simple names only, as inline ones
    are the shape every window and mixin in the package uses."""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == cls:
            return [b.id for b in node.bases if isinstance(b, ast.Name)]
    return []


def _overlay_built_names(src: str, defs: dict[str, set[str]]) -> set[str]:
    """Overlay-package names a file builds: `TargetSplit(...)` or `_scroll_body(...)`.

    Blunt on purpose, like the censuses: a call through any name that matches a
    definition in the package counts, whether or not the file imported that
    exact one, and a call through an attribute (`render.rows(...)`) is invisible
    here. Two windows can only be accused between them, so the worst this can do
    is over-report a name nobody builds, and a missed duplicate costs the same
    as it did before this guard existed.
    """
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return set()
    return {node.func.id for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            } & set(defs)


def _overlay_surface_owners(sources: dict[str, str]) -> dict[str, set[str]] | None:
    """Overlay definition -> the overlay keys whose windows build it. None when
    the registry or the package cannot be read at all (the guard must skip
    rather than pass vacuously)."""
    windows = _overlay_window_files(sources)
    if not windows:
        return None
    defs = _overlay_defs(sources)
    owners: dict[str, set[str]] = {}
    for key, (rel, cls) in windows.items():
        built: set[str] = set()
        for src_rel in _overlay_surface_files(rel, cls, sources):
            built |= _overlay_built_names(sources[src_rel], defs)
        for name in built:
            owners.setdefault(name, set()).add(key)
    return owners


def _doubled_hud_surfaces(owners: dict[str, set[str]],
                          shared: set[str]) -> dict[str, list[str]]:
    """Definitions built by more than one window and NOT declared shared."""
    return {name: sorted(keys) for name, keys in sorted(owners.items())
            if len(keys) > 1 and name not in shared}


def _stale_shared_hud_surfaces(owners: dict[str, set[str]],
                               shared: set[str]) -> list[str]:
    """Allowlist entries no longer built by two windows: debt, not an exemption."""
    return sorted(n for n in shared if len(owners.get(n, set())) < 2)


def test_every_hud_surface_is_built_by_exactly_one_window():
    """No two overlay windows may each build the same HUD surface.

    The Top DPS meter and the Test Dummy HUD both mounted the entire dummy test
    from `dps/dummy_ui.py`: the meter's copy was the fallback for when the dummy
    HUD was off, so every dummy feature had two renderings, two empty states and
    two chances to disagree - and the boards could stack over one dummy, which
    is what the separate window was split out to prevent. Deleting the meter's
    copy is a one-off; this guard is what keeps it from growing back, for the
    dummy test or for any future surface.

    `_SHARED_HUD_WIDGETS` is the declared exception - the section box, the row
    spec, the player row and the capture badge are furniture every HUD draws - so
    a genuinely shared widget is allowed to say so, and the allowlist fails when
    it goes stale instead of quietly covering a surface that stopped being
    shared.
    """
    tracked = _tracked_paths(ROOT)
    if tracked is None:
        pytest.skip("not a git checkout; tracked sources cannot be enumerated")
    sources = _tracked_sources(tracked)

    windows = _overlay_window_files(sources)
    assert windows, (
        f"{OVERLAY_MANAGER}'s _OVERLAY_SOURCES and the {OVERLAYS_PREFIX}/ "
        f"package could not both be read; without them this guard would pass "
        f"vacuously")
    assert len(windows) >= 6, (
        f"only {len(windows)} overlay windows resolved (6 when this guard was "
        f"added) - a window whose class cannot be found is one whose surface "
        f"this guard would not check")

    owners = _overlay_surface_owners(sources)
    assert owners, (
        "no overlay-package definition could be attributed to any window; the "
        "ownership census is blind and this guard is passing vacuously")
    assert len(owners) >= 20, (
        f"only {len(owners)} overlay definitions attributed to windows (26 "
        f"when this guard was added) - a partial read covers less than it "
        f"claims")

    doubled = _doubled_hud_surfaces(owners, _SHARED_HUD_SURFACES)
    assert not doubled, (
        "these HUD surfaces are built by more than one overlay window:\n"
        + "\n".join(f"  - {name}: {keys}" for name, keys in doubled.items())
        + "\nGive the surface one home (the window it belongs to, with the "
          "others reusing it or leaving it out), or declare it shared "
          "furniture in _SHARED_HUD_SURFACES with the reason. Two windows "
          "rendering one surface means two implementations that can disagree, "
          "and two boards stacked over the same game state.")

    stale = _stale_shared_hud_surfaces(owners, _SHARED_HUD_SURFACES)
    assert not stale, (
        f"_SHARED_HUD_SURFACES names {stale}, which no longer two windows "
        f"build (or which no longer exist). Delete the entry: an allowlist that "
        f"outlives its reason is how a shared surface quietly becomes a "
        f"duplicated one.")


def test_the_hud_surface_detector_actually_detects():
    """Pin the shape that shipped, and the walk that is the whole point of it.

    A mixin carrying one window's widgets, mixed into a second window: the
    widget is instantiated in the MIXIN's file, so neither the window class nor
    the class census in this module can see two builders. Only walking the bases
    finds it, which is why the drill checks the owners map directly.
    """
    base = f"{OVERLAYS_PREFIX}/"
    manager = ("_OVERLAY_SOURCES = {\n"
               '    "dps": ("dps", "DpsOverlay"),\n'
               '    "dummy": ("dummy_overlay", "DummyOverlay"),\n'
               "}\n")
    dummy_ui = ("class TargetSplit:\n"
                "    pass\n"
                "\n"
                "def build_split():\n"
                "    return TargetSplit()\n"
                "\n"
                "class DummyTestControlMixin:\n"
                "    def build(self):\n"
                "        return TargetSplit(), build_split()\n")
    window = ("from {dummy_mod} import DummyTestControlMixin\n"
              "from .rows import _PlayerRowWidget\n"
              "from ..entity_rows import _Section\n"
              "\n"
              "class {cls}(DummyTestControlMixin):\n"
              "    def __init__(self):\n"
              "        self.row = _PlayerRowWidget()\n"
              "        self.box = _Section()\n")
    sources = {
        OVERLAY_MANAGER: manager,
        base + "__init__.py": "",
        base + "dps/__init__.py": "",
        base + "entity_rows.py": "class _Section:\n    pass\n",
        base + "dps/rows.py": "class _PlayerRowWidget:\n    pass\n",
        base + "dps/dummy_ui.py": dummy_ui,
        base + "dummy_overlay.py": window.format(
            dummy_mod=".dps.dummy_ui", cls="DummyOverlay"),
        # the shape that shipped: the meter mounts the dummy HUD's mixin
        base + "dps/overlay.py": window.format(
            dummy_mod=".dummy_ui", cls="DpsOverlay"),
    }

    assert _overlay_window_files(sources) == {
        "dps": (base + "dps/overlay.py", "DpsOverlay"),
        "dummy": (base + "dummy_overlay.py", "DummyOverlay"),
    }
    owners = _overlay_surface_owners(sources)
    assert owners is not None
    # the mixin's file is part of BOTH windows' surfaces, so what it builds
    # - the widget and the builder function - has two owners
    assert owners["TargetSplit"] == {"dps", "dummy"}
    assert owners["build_split"] == {"dps", "dummy"}
    assert owners["_Section"] == {"dps", "dummy"}
    assert owners["_PlayerRowWidget"] == {"dps", "dummy"}
    assert _doubled_hud_surfaces(owners, _SHARED_HUD_SURFACES) == {
        "TargetSplit": ["dps", "dummy"],
        "build_split": ["dps", "dummy"],
    }

    # the fix: the meter stops mounting the mixin, so the surface has one home
    sources[base + "dps/overlay.py"] = window.format(
        dummy_mod="..entity_rows", cls="DpsOverlay")
    owners = _overlay_surface_owners(sources)
    assert owners["TargetSplit"] == {"dummy"}
    assert owners["build_split"] == {"dummy"}
    assert _doubled_hud_surfaces(owners, _SHARED_HUD_SURFACES) == {}

    # an allowlist entry nothing two windows build any more is debt
    assert _stale_shared_hud_surfaces(owners, {"_Section", "_GhostChart"}) \
        == ["_GhostChart"]
    assert _stale_shared_hud_surfaces(owners, {"_Section", "TargetSplit"}) \
        == ["TargetSplit"]

    # a registry whose class resolves nowhere is not read at all
    assert _overlay_registry_entries(manager) == {
        "dps": ("dps", "DpsOverlay"),
        "dummy": ("dummy_overlay", "DummyOverlay"),
    }
    assert _overlay_registry_entries("_OVERLAY_SOURCES = {}\n") == {}
    assert _overlay_registry_entries("x = 1\n") is None
    assert _overlay_window_files({OVERLAY_MANAGER: manager}) is None


# AGENTS.md's page table is a hand-maintained list of files, in a document
# nothing checks, listing modules by relative name inside a per-row base
# directory. It listed a `speedrun_page.py` that has not existed for releases
# and read fine the whole time, so it needs the same contract as the spec's
# hiddenimports: every filename it names must resolve. Each row carries its own
# base in the second column, so moving a page's mixin file moves the row's
# base with it rather than silently re-basing every name in it.
AGENTS_MD = "AGENTS.md"
_PAGE_TABLE_HEAD = "| Page | Mixin file |"


def _page_table_rows(md: str) -> list[tuple[str, str, str]]:
    """[(page, base, composed_from)] for the page table's data rows.

    The third column is optional. The table was trimmed to `Page | Mixin file`
    once the per-row module lists proved to be the bulk of the file for little
    routing value, so a two-cell row yields an empty `composed_from` and only
    the anchor is checked. Keeping the third slot means re-adding the module
    lists later needs no test change.

    Only the third column is checked for filenames: the table also lists class
    names (`CombatHistoryMixin`) and those live inside a module rather than
    beside one, so resolving them would mean reading every page's source for
    no extra safety on a document that is a map, not a contract.

    Row parsing keys on cell *count*, not on the header text, so a header change
    that leaves rows with fewer cells than the parser requires would match
    nothing at all. That is caught, not silent: the guard asserts
    `len(rows) >= 7` and names the header in the failure.
    """
    rows: list[tuple[str, str, str]] = []
    seen_head = False
    for line in md.splitlines():
        stripped = line.strip()
        if stripped == _PAGE_TABLE_HEAD:
            seen_head = True
            continue
        if not seen_head:
            continue
        if not stripped.startswith("|"):
            break                      # end of the table
        if set(stripped) <= set("|- :"):
            continue                   # the |---|---| separator row
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if len(cells) >= 2:
            rows.append((cells[0], cells[1], cells[2] if len(cells) > 2 else ""))
    return rows


def _doc_token_resolves(token: str, base: str, sources: dict[str, str]) -> bool:
    """True when a `.py` citation resolves, from `base` or from the repo root.

    `base` is the row's own directory (repo-relative, no leading slash), so a
    token with a slash in it is treated as repo-relative and one without as
    relative to the row - which is exactly how the table is written.
    """
    token = token.strip().strip("`")
    if token.endswith("*.py"):         # `ui/pages/*.py` is a glob, not a file
        token = token[:-3] + "x.py"
    roots = [f"farever_companion/{base}"] if base else []
    roots += ["farever_companion", ""]
    for root in roots:
        probe = f"{root}/{token}" if root else token
        if probe in sources or f"{probe}/__init__.py" in sources:
            return True
    return False


def _agents_page_table_gaps(md: str, sources: dict[str, str]) -> list[str]:
    """Filenames the page table names that are not in the tree."""
    problems: list[str] = []
    for page, base_cell, composed in _page_table_rows(md):
        base = _BACKTICKED.findall(base_cell)
        base_path = base[0] if base else ""
        base_dir = base_path.rsplit("/", 1)[0] if "/" in base_path else ""
        if (base_path.endswith(".py") and "*" not in base_path
                and not _doc_token_resolves(base_path, "", sources)):
            problems.append(
                f"{AGENTS_MD} page table: the '{page}' row is anchored on "
                f"`{base_path}`, which is not in the tree")
        for token in _BACKTICKED.findall(composed):
            if not token.endswith(".py"):
                continue
            if not _doc_token_resolves(token, base_dir, sources):
                problems.append(
                    f"{AGENTS_MD} page table: the '{page}' row names "
                    f"`{token}`, which resolves to no module under "
                    f"{base_dir or 'farever_companion/'}")
    return problems


def test_the_agents_page_table_only_names_modules_that_exist():
    """Every file AGENTS.md's page table names must be in the tree.

    The table is how a newcomer finds the page mixins, and it drifted: a
    `speedrun_page.py` row survived for releases after the file stopped
    existing, because AGENTS.md is prose and nothing resolves the names in it.
    The sibling contracts here all guard hand-maintained lists for the same
    reason - a list that names reality only by accident is worse than no list.
    """
    tracked = _tracked_paths(ROOT)
    if tracked is None:
        pytest.skip("not a git checkout; tracked sources cannot be enumerated")

    md_path = ROOT / AGENTS_MD
    assert md_path.is_file(), f"{AGENTS_MD} is missing"
    md = source_guard.read_text(md_path, encoding="utf-8")
    sources = _tracked_sources(tracked)

    rows = _page_table_rows(md)
    assert len(rows) >= 7, (
        f"only {len(rows)} page-table rows found (7 when this guard was added) "
        f"- the table header `|{_PAGE_TABLE_HEAD}|` must have changed, or this "
        f"guard is scanning nothing")

    problems = _agents_page_table_gaps(md, sources)
    assert not problems, (
        f"{len(problems)} filename(s) in AGENTS.md's page table do not exist:\n"
        + "\n".join(f"  - {p}" for p in problems))


def test_the_page_table_guard_actually_detects():
    """Pin the two shapes: a row anchored on a missing file, and a row naming
    one. A guard that only scanned the third column would miss a renamed page
    mixin, which is the half that breaks first."""
    sources = {
        "farever_companion/__init__.py": "",
        "farever_companion/ui/pages/combat_page.py": "",
        "farever_companion/ui/pages/combat_rail.py": "",
        "farever_companion/ui/pages/codex/__init__.py": "",
        "farever_companion/ui/pages/codex/grid.py": "",
    }
    head = f"\n{_PAGE_TABLE_HEAD}\n"
    sep = "|---|---|---|\n"

    good = (head + sep
            + "| Combat | `ui/pages/combat_page.py` | `combat_rail.py` (skills rail) |\n"
            + "| Codex | `ui/pages/codex/__init__.py` | `grid.py` |\n")
    assert _page_table_rows(good) == [
        ("Combat", "`ui/pages/combat_page.py`", "`combat_rail.py` (skills rail)"),
        ("Codex", "`ui/pages/codex/__init__.py`", "`grid.py`"),
    ]
    assert _agents_page_table_gaps(good, sources) == []

    # the shape that shipped: a row naming a file nobody has
    ghost = good + "| Speedrun | `ui/pages/combat_page.py` | `speedrun_page.py` |\n"
    found = _agents_page_table_gaps(ghost, sources)
    assert len(found) == 1 and "speedrun_page.py" in found[0], found

    # the shape that breaks first: the row's own anchor renamed
    moved = good.replace("ui/pages/codex/__init__.py", "ui/pages/codex.py")
    found = _agents_page_table_gaps(moved, sources)
    assert any("anchored on" in f and "codex.py" in f for f in found), found

    # a glob is not a file: `ui/pages/*.py` must not be resolved literally
    globbed = (head + sep
               + "| Everything else | `ui/pages/*.py` | `log.py` |\n")
    assert _agents_page_table_gaps(globbed, dict(sources, **{"farever_companion/ui/pages/log.py": ""})) == []


def test_every_subpackage_docstring_names_its_resident_and_import_direction():
    """Each package must document its residents and its import edge.

    The trees in AGENTS.md describe the layering; these docstrings are the
    version a reader has in their editor when they are about to add an import
    and need to know whether it is allowed. The contracts that make the
    layering testable (`core` imports no Qt, `ui/pages` reads no memory,
    `geo` imports neither `core` nor `ui`, only `control_panel.py` imports a
    page package) are only written down anywhere at all in these paragraphs.

    Until 2026-09-27 four of the eight layer packages were bare one-liners and
    two nested ones - `ui/overlays/` and `ui/pages/`, the second of them the
    entire content of the file - had no docstring at all. Covering the nested
    packages is not a second-class relaxation: `ui/overlays/__init__.py` being
    empty is exactly what forces `FareverPal.spec` to carry every overlay by
    hand, so a reader who cannot see that in the file has to rediscover it
    from a frozen build that is missing a HUD.

    Fails rather than warns, unlike the stray/census guards: a docstring gap
    is a thing someone has to write, and nothing in a normal work-in-progress
    run produces one by accident.
    """
    tracked = _tracked_paths(ROOT)
    if tracked is None:
        pytest.skip("not a git checkout; tracked sources cannot be enumerated")

    sources = _tracked_sources(tracked)
    audited = [r for r in sources if _PKG_INIT.fullmatch(r)]
    assert len(audited) >= 18, (
        f"scan looks wrong: only {len(audited)} packages audited (18 when the "
        f"nested packages were brought in: 8 layer + 10 nested) - a glob that "
        f"stopped matching would let every docstring gap through silently")

    problems = _subpackage_doc_gaps(sources)
    assert not problems, (
        f"{len(problems)} layer package(s) document neither their residents nor "
        f"their import direction:\n" + "\n".join(f"  - {p}" for p in problems))


def test_the_subpackage_doc_detector_actually_detects():
    """Pin the detector on the three gaps, and on the shape that must pass.

    A guard that only fires on a missing docstring is a guard nobody can keep
    green while editing prose, so each clause is drilled on its own: too thin,
    too vague, no direction - and a docstring that cites a module which has
    since been renamed or moved, which is the drift this is really here for.
    """
    ok = ('"""Layer package.\n\n`chests` holds the positions and `pois` the fixed\n'
          'markers.\n\n`zones` resolves a live position to a zone id. `core`\n'
          'imports this, never the other way round.\n"""\n')
    thin = '"""Just a one-liner."""\n'
    vague = ('"""Spatial layer.\n\n`manifests` and `anchors` describe the world and how\n'
             'it is laid out.\n\n`core` imports this, never the other way round.\n'
             '"""\n')
    silent = ('"""Static world geometry.\n\n`chests` and `pois` are the position\n'
              'indexes.\n\n`zones` resolves a live position to a zone id.\n'
              '"""\n')
    ok_ui = ('"""The Qt layer.\n\n`theme` holds the palette and `components` the\n'
             'widgets.\n\n`core` is imported from here, never the other way\n'
             'round.\n"""\n')
    tree = {
        "farever_companion/__init__.py": "",
        "farever_companion/constants.py": "",
        "farever_companion/geo/__init__.py": ok,
        "farever_companion/geo/chests.py": "",
        "farever_companion/geo/pois.py": "",
        "farever_companion/geo/zones.py": "",
        "farever_companion/geo/orb_sync.py": "",
        # cites `core`, a top-level sibling rather than a ui/ child
        "farever_companion/ui/__init__.py": ok_ui,
        "farever_companion/ui/theme.py": "",
        "farever_companion/ui/components.py": "",
    }
    assert _subpackage_doc_gaps(tree) == []

    for label, body, want in (("thin", thin, "line docstring"),
                              ("vague", vague, "names no resident"),
                              ("silent", silent, "import direction")):
        bad = dict(tree, **{"farever_companion/geo/__init__.py": body})
        found = _subpackage_doc_gaps(bad)
        assert any(want in f for f in found), (
            f"the {label} shape produced {found!r}, expected one naming {want!r}")

    # Drift is caught by the count, not by policing every backtick: a docstring
    # whose ONLY resident has been renamed or moved fires, but one that still
    # names a real module passes even alongside a stale citation. The tolerance
    # is load-bearing - `data` cites `raw_*` and `config` cites
    # `config._PROP_MAP`, neither of which is a module - so demanding that every
    # backtick resolve would redden the tree over prose, not over drift.
    stale = ('"""Static world geometry.\n\n`poi_manifests` holds the old index and\n'
             'is what `chests` used to be called.\n\n`core` imports this, never the\n'
             'other way round.\n"""\n')
    found = _subpackage_doc_gaps(dict(tree, **{"farever_companion/geo/__init__.py": stale}))
    assert found == [], (
        f"a docstring that still names a real resident was failed: {found!r}")

    # Resolution is two-sided: a child of the package, or of its parent, and
    # a filename citation resolves verbatim rather than as `page.py.py`.
    assert _doc_resident_resolves("chests", "farever_companion/geo", tree)
    assert _doc_resident_resolves("constants", "farever_companion/geo", tree)
    assert not _doc_resident_resolves("proc", "farever_companion/geo", tree)
    assert not _doc_resident_resolves("core/geo/chests", "farever_companion/geo", tree)
    assert _doc_resident_resolves("chests.py", "farever_companion/geo", tree)
    assert _doc_resident_resolves("geo", "farever_companion/core", tree)
    assert not _doc_resident_resolves("chests", "farever_companion/core", tree)


def test_the_stray_detector_actually_detects(tmp_path):
    """Pin both detectors: package strays and repo-root strays.

    A guard that quietly scans the wrong path — or the wrong thing — is worse
    than no guard at all, so a fake tree proves it flags the strays while
    leaving real modules, package markers, committed source and cache
    directories alone.
    """
    pkg = tmp_path / "farever_companion"
    (pkg / "ui").mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "py.typed").write_text("", encoding="utf-8")
    (pkg / "ui" / "page.py").write_text("", encoding="utf-8")
    (pkg / "ui" / "stray - copy.text").write_text("scratch", encoding="utf-8")
    cache = pkg / "__pycache__"
    cache.mkdir()
    (cache / "page.cpython-314.pyc").write_bytes(b"\x00")
    (cache / "notes.txt").write_text("scratch in a cache dir", encoding="utf-8")

    found = _stray_files(pkg)

    assert [p.name for p in found] == ["stray - copy.text"], (
        "detector must flag a stray .text and ignore .py, py.typed and "
        f"anything under a cache dir; got {[p.name for p in found]}")

    # --- repo-root detector, against a synthetic tree + tracked set ---------
    root = tmp_path / "repo"
    (root / "farever_companion" / "ui").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "ai" / "workspace" / "tool").mkdir(parents=True)
    (root / "__pycache__").mkdir()
    (root / ".idea").mkdir()
    (root / "tmp_preview" / "mockups").mkdir(parents=True)
    for rel in ("farever_companion/ui/page.py", "tests/test_x.py",
                "compiler.py", "AGENTS.md", "README.md"):
        (root / rel).write_text("", encoding="utf-8")
    (root / "aio-bk.bat.lnk").write_text("", encoding="utf-8")   # stray file
    (root / "tmp_preview" / "shot.png").write_text("", encoding="utf-8")
    (root / "tmp_preview" / "mockups" / "index.html").write_text("", encoding="utf-8")
    (root / "ai" / "workspace" / "tool" / "probe.py").write_text("", encoding="utf-8")
    (root / "__pycache__" / "x.pyc").write_bytes(b"\x00")
    (root / ".idea" / "workspace.xml").write_text("", encoding="utf-8")

    tracked = {"farever_companion/ui/page.py", "tests/test_x.py",
               "compiler.py", "AGENTS.md", "README.md"}

    strays = [p.relative_to(root).as_posix() for p in _root_strays(root, tracked)]

    assert strays == ["aio-bk.bat.lnk", "tmp_preview"], (
        "root detector must flag an untracked root file and an untracked root "
        "folder as one stray, while leaving committed source, ai/workspace "
        f"scratch, build output and hidden config alone; got {strays}")
    # ... and the folder's size report counts the files a person would move.
    n, size = _stray_size(root / "tmp_preview")
    assert (n, size) == (2, 0), f"expected 2 files, got {n} ({size} bytes)"

    # Compile this module from scratch too: the stray detector was the first
    # thing seen when a test ran without conftest, so make sure the module
    # imports cleanly through the real suite's path (repo root on sys.path).
    r = subprocess.run([sys.executable, "-c", "import tests.test_repo_hygiene"],
                       cwd=str(ROOT), capture_output=True, text=True)
    assert r.returncode == 0, (
        "the stray detector module must import cleanly; got:\n"
        f"{r.stderr}{r.stdout}")


def test_root_strays_spare_files_git_already_ignores(tmp_path):
    """A root file git ignores is output, not scratch.

    `*.log` is in .gitignore and run-log.bat writes run.log at the repo root on
    every development run, so without this the guard warned about its own
    launcher's output in every single run - a warning that is always there is
    one nobody reads. An ignored FOLDER stays reported: an ignore rule is how a
    tree marks throwaway output, not what makes scratch legitimate.
    """
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    (repo / "tests" / "test_x.py").write_text("", encoding="utf-8")
    (repo / ".gitignore").write_text("*.log\n", encoding="utf-8")
    (repo / "run.log").write_text("log", encoding="utf-8")      # ignored
    (repo / "probe.tmp").write_text("x", encoding="utf-8")      # not ignored
    (repo / "scratch_logs").mkdir()
    (repo / "scratch_logs" / "a.log").write_text("x", encoding="utf-8")
    try:
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True,
                       capture_output=True)
    except (OSError, subprocess.SubprocessError):
        pytest.skip("git unavailable; ignore rules cannot be exercised")

    strays = [p.name for p in _root_strays(repo, {"tests/test_x.py"})]
    assert strays == ["probe.tmp", "scratch_logs"], (
        "an ignored root FILE is output and must be spared, while an "
        "untracked folder and a plain untracked file are still strays; got "
        f"{strays}")


def test_stray_guards_warn_locally_and_fail_under_ci(monkeypatch):
    """Pin both modes of `_report`: warn locally, fail under CI.

    The CI gate is the whole point of the guard, and a quiet downgrade - an
    override that reads as "set but blank", an empty `CI`, a warning path that
    swallows everything - would turn it back into a message nobody reads. Both
    directions, plus the override, are asserted here.
    """
    for var in ("CI", "FAREVER_HYGIENE_STRICT"):
        monkeypatch.delenv(var, raising=False)

    # Local default: report, do not fail. A clean tree is silent in either mode.
    assert _strict() is False
    _report([])
    with pytest.warns(StrayFileWarning, match="DEAD/UNUSED"):
        _report(["DEAD/UNUSED FILE in the shipped package: x (1 bytes)."])

    # CI (the variable GitHub Actions and friends set) makes it a failure, and
    # one that names every stray rather than only the first.
    monkeypatch.setenv("CI", "true")
    assert _strict() is True
    with pytest.raises(AssertionError, match=r"2 problem\(s\)") as exc:
        _report(["stray one", "stray two"])
    assert "stray one" in str(exc.value) and "stray two" in str(exc.value)

    # The documented escape hatch wins even under CI.
    monkeypatch.setenv("FAREVER_HYGIENE_STRICT", "0")
    assert _strict() is False
    with pytest.warns(StrayFileWarning):
        _report(["stray one"])

    # ...as does the other direction locally, with the usual truthy spellings.
    monkeypatch.delenv("CI")
    for value in ("1", "true", "YES", " on "):
        monkeypatch.setenv("FAREVER_HYGIENE_STRICT", value)
        assert _strict() is True, f"{value!r} should mean strict"
        with pytest.raises(AssertionError):
            _report(["stray one"])

    # A set-but-blank variable is not a signal in either direction.
    monkeypatch.setenv("FAREVER_HYGIENE_STRICT", "  ")
    monkeypatch.setenv("CI", "")
    assert _strict() is False


def test_package_holds_no_dead_non_code_files():
    """Report every dead non-code file inside the shipped package.

    Warns locally and fails under CI (see `_report`), so each stray is named as
    DEAD/UNUSED in the warnings summary of every run instead of sitting in the
    tree unnoticed - and cannot reach a merge while it is still there.
    """
    assert PKG.is_dir(), f"package not found at {PKG}"
    modules = sum(1 for _ in PKG.rglob("*.py"))
    assert modules > 50, (
        f"scan looks wrong: only {modules} modules found under {PKG}, so the "
        "guard would pass vacuously")

    # A stray that is also committed needs a git step, not just a delete: it
    # stays in every checkout (CI included) until the removal is committed, and a
    # CI failure that says "delete it" to someone who already did is a puzzle.
    tracked = _tracked_paths(ROOT) or set()
    problems: list[str] = []
    for p in _stray_files(PKG):
        rel = p.relative_to(ROOT).as_posix()
        committed = (
            " It is COMMITTED as well as on disk, so remove it from git too "
            "(git rm) - until that removal is committed every checkout, CI "
            "included, still gets the file." if rel in tracked else "")
        problems.append(
            f"DEAD/UNUSED FILE in the shipped package: "
            f"{rel} "
            f"({p.stat().st_size} bytes). Not importable code and not "
            f"referenced by any module. Delete it, or move it to "
            f"ai/workspace/<tool>/ if it is scratch.{committed}")
    _report(problems)


def test_no_tracked_file_defines_a_dead_class():
    """Report every tracked class nothing in the repo references.

    Pyflakes reports unused imports, never unused classes, so dead code of this
    shape is invisible to every other guard in the suite: `components.py` held a
    `ClickableCard` with no consumer in the app, the tests or the dev tools, and
    `widgets.py` a `ColorButton` and a `Sparkline` likewise. Warns locally and
    fails under CI (see `_report`), so a newly orphaned class is named in every
    run instead of quietly accumulating.
    """
    tracked = _tracked_paths(ROOT)
    if tracked is None:
        pytest.skip("not a git checkout; tracked sources cannot be enumerated")

    texts = _tracked_sources(tracked)
    assert len(texts) > 100, (
        f"scan looks wrong: only {len(texts)} python files read, so the guard "
        "would pass vacuously")

    problems = [
        f"DEAD CLASS: {rel}:{line} class {name} - the definition is the only "
        f"mention of that name in the repo. Delete it, or if it is constructed "
        f"from outside the tracked tree, say so in a comment here."
        for rel, line, name in _dead_classes(texts)
    ]
    _report(problems, label="dead class guard", warning=DeadCodeWarning,
            tail="Delete each dead class, or wire it up. ")


def test_the_dead_class_detector_actually_detects():
    """Pin the detector on all four cases, so the gate cannot pass vacuously.

    A guard that scans the wrong thing is worse than none: it must flag the
    unreferenced class, and stay quiet for a class another file imports, one
    used inside its own file (there are ~95 of those, all legitimate), and one
    whose name merely appears in a comment.
    """
    sources = {
        "a.py": "class Used:\n    pass\n",
        "b.py": "from a import Used\nprint(Used)\n",          # alive: another file
        "c.py": "class _Local:\n    pass\nx = _Local()\n",   # alive: used in place
        "d.py": "class Gone:\n    pass\n",                   # dead
        "e.py": "class Mentioned:\n    pass\n# Mentioned, once\n",
        "f.py": "class Twin:\n    pass\n",
        "g.py": "class Twin:\n    pass\n",                    # two files, one name
        "broken.py": "class Nope(:\n",                       # will not parse
    }
    found = {(rel, name) for rel, _line, name in _dead_classes(sources)}
    assert found == {("d.py", "Gone")}, (
        "detector must flag only the unreferenced class; got "
        f"{sorted(found)}")


def test_no_tracked_file_defines_an_orphan_function():
    """Report every tracked top-level function or fixture nothing references.

    Pyflakes reports unused imports, never unused functions, so an orphan of
    this shape is invisible to every other guard: `_qapp`, a module fixture
    whose last three users were each handed a local copy of it, sat in
    tests/dps_fakes.py with nothing left pointing at it. Same bar as the
    dead-class census - the `def` line is the only mention of the name anywhere
    in the tracked tree - and the names pytest claims by convention are skipped
    inside tests/ only.
    """
    tracked = _tracked_paths(ROOT)
    if tracked is None:
        pytest.skip("not a git checkout; tracked sources cannot be enumerated")

    texts = _tracked_sources(tracked)
    assert len(texts) > 100, (
        f"scan looks wrong: only {len(texts)} python files read, so the guard "
        "would pass vacuously")

    problems = [
        f"ORPHAN FUNCTION: {rel}:{line} def {name} - the definition is the only "
        f"mention of that name in the repo. Delete it, or wire it up; a fixture "
        f"nothing requests counts here too. If it is consumed from outside the "
        f"tracked tree, say so in _ORPHAN_DEF_BASELINE."
        for rel, line, name in _dead_functions(texts, _ORPHAN_DEF_BASELINE)
    ]
    _report(problems, label="orphan function guard", warning=DeadCodeWarning,
            tail=f"Delete each orphan, wire it up, or add its name to "
                 f"_ORPHAN_DEF_BASELINE ({len(_ORPHAN_DEF_BASELINE)} entries, "
                 f"all pre-existing). ")

    stale = _stale_ratchet_entries(texts, _ORPHAN_DEF_BASELINE, _dead_functions)
    assert not stale, (
        f"_ORPHAN_DEF_BASELINE names {stale}, which the function census no "
        f"longer finds dead - the name is wired up, or gone. Delete the entry.")


def test_the_orphan_function_detector_actually_detects():
    """Pin the census on each way a name can be claimed alive.

    It must flag the unreferenced def and stay quiet for one another file
    calls, one used in its own file, one merely mentioned, one pytest collects
    by name, an autouse fixture (used by the framework), a `pytest_*` hook, and
    a name the baseline already owns.
    """
    sources = {
        "a.py": "def used():\n    pass\n",
        "b.py": "from a import used\nused()\n",
        "c.py": "def local():\n    pass\nx = local\n",
        "d.py": "def mentioned():\n    pass\n# mentioned, once\n",
        "tests/test_x.py": "def test_collected():\n    pass\n",
        "tests/conftest.py":
            "@pytest.fixture(autouse=True)\ndef _auto():\n    pass\n",
        "tests/hooks.py": "def pytest_configure(config):\n    pass\n",
        "e.py": "def gone():\n    pass\n",
        "f.py": "def known():\n    pass\n",
    }
    found = {(rel, name) for rel, _line, name in
             _dead_functions(sources, {"known"})}
    assert found == {("e.py", "gone")}, (
        f"detector must flag only the truly unreferenced def; got {sorted(found)}")


def test_no_shipped_module_is_kept_alive_only_by_a_ratchet():
    """Report every shipped module whose only way in is a ratcheted name.

    The three censuses above can only ever report names, so the file that holds
    them is invisible to all of them: `geo/critters.py` exists solely to define
    `resolve_spawner_unit`, which `_ORPHAN_DEF_BASELINE` already records as
    dead, plus the private helper only that dead function calls. Deleting the
    name and deleting the file are different acts, and only this census can see
    that the second one is what the first one implied.
    """
    tracked = _tracked_paths(ROOT)
    if tracked is None:
        pytest.skip("not a git checkout; tracked sources cannot be enumerated")

    texts = _tracked_sources(tracked)
    assert len(texts) > 100, (
        f"scan looks wrong: only {len(texts)} python files read, so the guard "
        "would pass vacuously")

    problems = [
        f"DEAD FILE: {rel} - every way into it is already recorded as dead "
        f"({', '.join(names)}), and nothing else it defines is read from "
        f"another file. Delete the file and its ratchet entry, or wire it up."
        for rel, names in _fully_dead_files(texts, _ORPHAN_DEF_BASELINE,
                                            _DEAD_CONSTANT_BASELINE,
                                            _DEAD_FILE_BASELINE)
    ]
    _report(problems, label="dead file guard", warning=DeadCodeWarning,
            tail=f"Delete each file, or wire it up; if it is reached from "
                 f"outside the tracked tree, say so in _DEAD_FILE_BASELINE "
                 f"({len(_DEAD_FILE_BASELINE)} entries, all pre-existing). ")


def test_the_fully_dead_file_detector_actually_detects():
    """Pin the census on every way a file is spared, so the gate cannot pass
    vacuously - and on the one shape it must convict.

    The conviction needs both halves: a ratcheted way in, and no other name in
    the file read from anywhere else. Each source below fails exactly one half.
    """
    sources = {
        # convict: `gone` is ratcheted and `helper_dead` is read by nothing else
        "farever_companion/pkg/dead.py":
            "def gone():\n    pass\n\n\ndef helper_dead():\n    return gone()\n",
        # convict: same shape with a ratcheted constant instead of a def
        "farever_companion/pkg/consts.py":
            "DEAD_TUNING = 1\n\n\ndef helper_consts():\n    return DEAD_TUNING\n",
        # spared: `used` is imported from another file
        "farever_companion/pkg/live.py":
            "def gone():\n    pass\n\n\ndef used():\n    pass\n",
        "farever_companion/pkg/caller.py":
            "from farever_companion.pkg.live import used\n\nused()\n",
        # spared: nothing here is ratcheted, so the name censuses own the verdict
        "farever_companion/pkg/fresh.py":
            "def unratcheted():\n    pass\n\n\ndef helper_fresh():\n    return unratcheted()\n",
        # spared: reached by path, not by name (the loader never spells a def)
        "farever_companion/pkg/by_path.py":
            "def gone():\n    pass\n",
        "farever_companion/pkg/loader.py":
            'PLUGINS = ["by_path.py"]\n',
        # spared: a package __init__ runs for any import of the package
        "farever_companion/pkg/__init__.py": "def gone():\n    pass\n",
        # spared: reached by path - pytest collects it
        "tests/test_pkg.py": "def gone():\n    pass\n",
    }
    found = {rel for rel, _names in
             _fully_dead_files(sources, {"gone"}, {"DEAD_TUNING"})}
    assert found == {"farever_companion/pkg/dead.py",
                     "farever_companion/pkg/consts.py"}, (
        f"detector must convict exactly the two files kept alive only by a "
        f"ratchet; got {sorted(found)}")

    # The baseline is the ratchet, so an entry silences it exactly like the two
    # name lists do.
    baselined = {rel for rel, _names in
                 _fully_dead_files(sources, {"gone"}, {"DEAD_TUNING"},
                                   {"farever_companion.pkg.dead"})}
    assert baselined == {"farever_companion/pkg/consts.py"}, (
        f"a ratcheted file must go quiet; got {sorted(baselined)}")


def test_no_tracked_file_defines_a_dead_constant():
    """Report every tracked ALL_CAPS module constant nothing reads.

    The third shape pyflakes cannot see: it reports unused imports and unused
    locals, never an unread module-level assignment, so a tunable whose reader
    was deleted - or a table whose feature was never wired - sits in the file
    looking load-bearing. `theme.LIME`, `dps_bridge.COUNT_WINDOW_S` and the
    `constants.HL_ANCHOR_*` scan anchors were all in that state. Same bar as the
    two censuses above (the assignment is the only mention of the name anywhere
    in the tracked tree) and the same policy: warn locally, fail under CI.
    """
    tracked = _tracked_paths(ROOT)
    if tracked is None:
        pytest.skip("not a git checkout; tracked sources cannot be enumerated")

    texts = _tracked_sources(tracked)
    assert len(texts) > 100, (
        f"scan looks wrong: only {len(texts)} python files read, so the guard "
        "would pass vacuously")

    problems = [
        f"DEAD CONSTANT: {rel}:{line} defines {name}, which nothing in the repo "
        f"reads. Delete it, or wire up what should read it. If it is consumed "
        f"from outside the tracked tree, say so in _DEAD_CONSTANT_BASELINE."
        for rel, line, name in _dead_constants(texts, _DEAD_CONSTANT_BASELINE)
    ]
    _report(problems, label="dead constant guard", warning=DeadCodeWarning,
            tail=f"Delete each unread constant, or add its name to "
                 f"_DEAD_CONSTANT_BASELINE ({len(_DEAD_CONSTANT_BASELINE)} "
                 f"entries, all pre-existing). ")

    stale = _stale_ratchet_entries(texts, _DEAD_CONSTANT_BASELINE,
                                   _dead_constants)
    assert not stale, (
        f"_DEAD_CONSTANT_BASELINE names {stale}, which the constant census no "
        f"longer finds dead - the constant is read now, or gone. Delete the "
        f"entry.")


def test_the_dead_constant_detector_actually_detects():
    """Pin the detector on each way an assignment is accounted for, and on its
    scope line: a dunder, a lowercase name and a name assigned inside a function
    are not this census's business at all, while a name the baseline owns stays
    quiet."""
    sources = {
        "a.py": "LIVE = 1\nprint(LIVE)\n",
        "b.py": "SHARED = 1\n",
        "c.py": "from b import SHARED\nprint(SHARED)\n",
        "d.py": "DEAD = 1\n",
        "e.py": "MENTIONED = 1\n# MENTIONED, once\n",
        "f.py": "__version__ = '1'\n",
        "g.py": "lower = 1\n",
        "h.py": "def f():\n    LOCAL = 1\n    return LOCAL\n",
        "i.py": "EXPORTED = 1\n__all__ = ['EXPORTED']\n",
        "j.py": "KNOWN = 1\n",
        "k.py": "LOOKED_UP = 1\nprint(globals()['LOOKED_UP'])\n",
    }
    found = {(rel, name) for rel, _line, name in
             _dead_constants(sources, {"KNOWN"})}
    assert found == {("d.py", "DEAD")}, (
        f"detector must flag only the unread tunable; got {sorted(found)}")


def test_a_ratchet_entry_is_its_own_record_not_its_own_reader():
    """A ratchet entry names debt; it must never be the evidence that clears it.

    The declaration, the comment beside it and any test in this file that
    asserts the name are all records, so the staleness check asks its question
    of a copy of the tree with THIS file's mentions taken out (see
    `_without_ratchet_mentions`). Pinned on both censuses: `wired` is listed
    but another file reads it, so its entry is stale and must go, while `dead`
    is listed and read by nothing, so its entry stands.
    """
    sources = {
        _GUARD_SELF: ('_ORPHAN_DEF_BASELINE = {"wired", "dead"}\n'
                      '# recorded here, and named again: wired, dead.\n'
                      'def test_pins_the_names():\n'
                      '    assert "dead" in _ORPHAN_DEF_BASELINE\n'),
        "app.py": "def wired():\n    pass\n\n\ndef dead():\n    pass\n",
        "user.py": "from app import wired\n\nwired()\n",
    }
    assert _stale_ratchet_entries(sources, {"wired", "dead"},
                                  _dead_functions) == ["wired"]

    consts = {
        _GUARD_SELF: '_DEAD_CONSTANT_BASELINE = {"LIVE_TUNING", "DEAD_TUNING"}\n',
        "app.py": "LIVE_TUNING = 1\n\nDEAD_TUNING = 2\n",
        "user.py": "from app import LIVE_TUNING\nprint(LIVE_TUNING)\n",
    }
    assert _stale_ratchet_entries(consts, {"LIVE_TUNING", "DEAD_TUNING"},
                                  _dead_constants) == ["LIVE_TUNING"]


def test_no_definition_is_kept_alive_only_by_a_doc():
    """A definition nothing in real code names, whose only mentions are prose.

    The three censuses above count a comment or a docstring as a mention, so a
    name can survive on prose alone and be invisible to every one of them:
    `constants.CALIBRATION_MAP` is named only in the two comments around it, and
    `dps_dummy.DUMMY_TARGET_DEFAULT_S` only in its own module header. This
    census is those three scopes on a sharper bar - a mention must be CODE (an
    identifier, an attribute, an import, a parameter, an `__all__` entry, a
    registry key) rather than prose - which is what makes "documented but dead"
    visible when a doc is the only thing keeping it alive.

    Warns locally and fails under CI (see `_report`), like the three censuses it
    is the fourth of.
    """
    tracked = _tracked_paths(ROOT)
    if tracked is None:
        pytest.skip("not a git checkout; tracked sources cannot be enumerated")

    texts = _tracked_sources(tracked)
    docs = _tracked_doc_texts(tracked)
    assert len(texts) > 100, (
        f"scan looks wrong: only {len(texts)} python files read, so the guard "
        "would pass vacuously")
    assert docs, (
        "no tracked non-Python text was read, so a doc mention could not be "
        "told from a code one and this guard would pass vacuously")

    detected = _prose_only_definitions(texts, docs)
    problems = [
        f"DOC-ONLY DEFINITION: {rel}:{line} {name} is named nowhere in code - "
        f"the only mentions are a comment, a docstring or a tracked doc. Delete "
        f"it, wire up what should read it, or record it in "
        f"_PROSE_ONLY_DEF_BASELINE."
        for rel, line, name in detected
        if name not in _PROSE_ONLY_DEF_BASELINE
    ]
    _report(problems, label="doc-only definition guard", warning=DeadCodeWarning,
            tail=f"Delete each documented-but-dead definition, or add its name "
                 f"to _PROSE_ONLY_DEF_BASELINE "
                 f"({len(_PROSE_ONLY_DEF_BASELINE)} entries, all pre-existing). ")

    # The ratchet is checked with the census itself, which only works because
    # the declaration's own strings are not counted as code (see
    # `_ratchet_literal_ids`): the day a listed name is wired up or deleted it
    # stops being detected, and that is the day its entry must go. An entry
    # that outlives its reason is how a dead definition quietly becomes
    # load-bearing.
    stale = sorted(_PROSE_ONLY_DEF_BASELINE
                   - {name for _rel, _line, name in detected})
    assert not stale, (
        f"_PROSE_ONLY_DEF_BASELINE names {stale}, which the census no longer "
        f"finds kept alive by prose alone - the name is wired up, or gone. "
        f"Delete the entry.")


def test_the_doc_only_detector_actually_detects():
    """Pin the line between a code mention and a prose one.

    `_prose_only_definitions` is only worth its warning if code really does
    excuse a name and prose really does not, so each way in is pinned: a call, a
    call inside the definition's own file, an import alias, a parameter name, an
    `__all__` entry; against a bare comment, a docstring, a doc file, an autouse
    fixture pytest runs unnamed, a name the baseline owns, and a lowercase
    assignment that is out of scope.
    """
    sources = {
        "a.py": "def called():\n    pass\n\ncalled()\n",
        "b.py": "def aliased():\n    pass\n",
        "c.py": "from b import aliased as al\nprint(al)\n",
        "d.py": "def commented():\n    pass\n\n# commented was wired up once\n",
        "e.py": "def promised():\n    \"\"\"promised to the sidebar.\"\"\"\n",
        "f.py": "GONE = 1\n# GONE was the old default\n",
        "g.py": "KNOWN = 1\n",
        "h.py": "lower_case = 1\n# lower_case once\n",
        "i.py": "def exported():\n    pass\n\n__all__ = ['exported']\n",
        "j.py": "def ondoc():\n    pass\n",
        "k.py": "def asparam(x):\n    pass\n\n\ndef use(asparam):\n    pass\n",
        "m.py": "class Documented:\n    \"\"\"Documented, and nothing else.\"\"\"\n",
        "tests/l.py": ("import pytest\n\n\n@pytest.fixture(autouse=True)\n"
                       "def onprose():\n    \"\"\"onprose runs by itself.\"\"\"\n"),
    }
    doc_texts = {"README.md": "see ondoc for the details\n"}
    found = {(rel, name) for rel, _line, name in
             _prose_only_definitions(sources, doc_texts, {"KNOWN"})}
    assert found == {
        ("d.py", "commented"),      # a comment is not a reader
        ("e.py", "promised"),       # a docstring is not a reader
        ("f.py", "GONE"),           # ...nor for a constant
        ("j.py", "ondoc"),          # ...nor is a tracked doc
        ("m.py", "Documented"),     # ...nor for a class
    }, f"detector flagged the wrong set: {sorted(found)}"


def test_the_doc_only_detector_reads_a_ratchet_as_a_record():
    """A `_PROSE_ONLY_DEF_BASELINE` declaration names debt; it never reads it.

    Pinned separately from the corpus above because it is the one shape a read
    of the tree alone cannot catch: the ratchet lists the names the census has
    already recorded, as strings in this same file, and a non-docstring string
    is a CODE mention - so without `_ratchet_literal_ids` every entry would
    excuse itself and the debt could never be re-examined. `selfnamed` is
    written in the declaration and used nowhere, so it stays reported;
    `alsoratchet` is declared too but imported by another file, so CODE still
    excuses it.
    """
    sources = {
        "_baseline.py": """_PROSE_ONLY_DEF_BASELINE = {'selfnamed', 'alsoratchet'}
""",
        "n.py": """def selfnamed():
    pass
""",
        "o.py": """def alsoratchet():
    pass
""",
        "p.py": """from o import alsoratchet
""",
    }
    doc_texts = {"README.md": "selfnamed is documented and nothing else"}
    found = {(rel, name) for rel, _line, name in
             _prose_only_definitions(sources, doc_texts)}
    assert found == {("n.py", "selfnamed")}, (
        f"the ratchet's own declaration must not excuse a name; "
        f"got {sorted(found)}")


def test_every_dunder_all_name_resolves():
    """Every `__all__` entry must exist in the module that exports it.

    `__all__` is how a re-export hub (ui/components.py, ui/pages/items/
    support.py) states its contract - and it is also the only thing that
    silences the linter for a name the module does not define, so a stale entry
    (a rename, a typo, an import dropped later) buys that silence for a name
    that does not exist. Checking each one is bound at module level is what
    turns the hub into a contract instead of a comment.
    """
    tracked = _tracked_paths(ROOT)
    if tracked is None:
        pytest.skip("not a git checkout; tracked sources cannot be enumerated")

    problems: list[str] = []
    for rel, text in sorted(_tracked_sources(tracked).items()):
        for line, name in _unresolved_all_names(text):
            problems.append(
                f"STALE __all__ ENTRY: {rel}:{line} exports {name!r}, which "
                f"the module neither defines nor imports.")
    _report(problems, label="__all__ contract guard", warning=DeadCodeWarning,
            tail="Fix the entry, or drop it from __all__. ")


def test_the_all_contract_detector_actually_detects():
    """Pin the `__all__` checker on all three shapes: a module that binds
    everything it exports passes, a name it does not bind is reported, and a
    computed `__all__` is left alone rather than guessed at."""
    ok = "from .x import A\nB = 1\n__all__ = ['A', 'B']\n"
    bad = "from .x import A\n__all__ = ['A', 'Typo']\n"
    computed = "from .x import *\n__all__ = [n for n in dir()]\n"
    assert _unresolved_all_names(ok) == []
    assert [n for _line, n in _unresolved_all_names(bad)] == ["Typo"]
    assert _unresolved_all_names(computed) == []


def test_every_dunder_all_hub_declares_or_uses_its_imports():
    """An import in a module that declares `__all__` must be exported or used.

    The backward half of the guard above: that one checks every entry is real,
    this one checks every real re-export is an entry. Both failure modes are
    silent, and both were live here - `data/items/__init__.py` re-exported 16
    names through the hub while the linter called every one of them an unused
    import, and the only two names nothing reached through it were stray
    imports nobody had declared either. A hub is exactly where "unused import"
    cannot be trusted, so the declared list is what does the talking; this test
    is what keeps that list complete as names come and go.
    """
    tracked = _tracked_paths(ROOT)
    if tracked is None:
        pytest.skip("not a git checkout; tracked sources cannot be enumerated")

    texts = _tracked_sources(tracked)
    assert len(texts) > 100, (
        f"scan looks wrong: only {len(texts)} python files read, so the guard "
        "would pass vacuously")

    problems = [
        f"UNDECLARED RE-EXPORT: {rel}:{line} imports {name!r}, which is neither "
        f"in __all__ nor used in the module. Add it to __all__ if something "
        f"reaches it through this module, or delete it if nothing does."
        for rel, text in sorted(texts.items())
        for line, name in _undeclared_all_imports(text)
    ]
    _report(problems, label="__all__ import guard", warning=DeadCodeWarning,
            tail="Add each name to __all__, use it, delete it, or mark its "
                 "import `# noqa` with the reason it is deliberate. ")


def test_the_dunder_all_import_detector_actually_detects():
    """Pin the detector on every way an import of a hub is accounted for:
    exported, used, and renamed-and-exported stay quiet, and the four skips
    (`*`, `__future__`, `# noqa`, a computed `__all__`) do too, while an import
    that is none of those is reported."""
    cases = {
        "exported": "from .x import A\n__all__ = ['A']\n",
        "used": "from .x import A\n__all__ = []\nA()\n",
        "renamed": "from .x import A as B\n__all__ = ['B']\n",
        "star": "from .x import *\n__all__ = []\n",
        "future": "from __future__ import annotations\n__all__ = []\n",
        "noqa": "from .x import probe  # noqa: F401  (availability probe)\n"
                "__all__ = []\n",
        "computed": "from .x import A\n__all__ = [n for n in globals()]\n",
        "no list": "from .x import A\n",
    }
    for label, src in cases.items():
        assert _undeclared_all_imports(src) == [], f"{label} must stay quiet"

    dead = "from .x import stray, kept\n__all__ = ['kept']\n"
    assert [n for _line, n in _undeclared_all_imports(dead)] == ["stray"]


def test_no_shim_ships_a_baked_key_nobody_reads():
    """Every key the compiler bakes into a raw_* shim must have a reader.

    A baked field is a constant by another name: named once in compiler.py,
    shipped compressed to every user, invisible to pyflakes and to every
    other census here. That is exactly how 482 `atlas_x`/`atlas_y` row keys
    shipped unread for two months. A key counts as read when ANY tracked file
    outside its shim and the compiler mentions it - writers, tests and dev
    tools count, so the census can only under-report. The escapes are
    evidence-based, not vibes: atlas blobs are id-keyed lookups, raw_skills'
    two sections are test-pinned contracts (one verbatim, one resolver
    fuel read by token name), `__meta__` is human-read, and the keys found
    dead on day one sit in `_BAKED_KEY_BASELINE` with their findings.

    Skipped when git is unavailable, like every census here: without the
    tracked set the reader index would be a lie.
    """
    tracked = _tracked_paths(ROOT)
    if tracked is None:
        pytest.skip("not a git checkout; tracked sources cannot be enumerated")

    sources = _tracked_sources(tracked)
    assert len(sources) > 100, (
        f"scan looks wrong: only {len(sources)} python files read, so the "
        "guard would pass vacuously")
    domain = _baked_key_domain_size(sources)
    assert domain > 300, (
        f"scan looks wrong: {domain} baked keys found across the five shims "
        "(over 400 when this guard was added) - a payload that stopped "
        "decoding would otherwise pass this silently")

    problems = [
        f"UNREAD BAKED KEY: {shim} ships {key!r} (under {where}) and no "
        "tracked file outside the compiler mentions it. Delete the baking, "
        "wire the reader, or add the key to _BAKED_KEY_BASELINE with the "
        "reason it must stay."
        for shim, key, where in _unresolved_baked_keys(sources)
    ]
    _report(problems, label="baked-key census", warning=DeadCodeWarning,
            tail="Dead baked keys are shipped bytes every user decompresses. ")


def test_the_baked_key_detector_actually_detects():
    """Pin the census on the shapes that decide a conviction: an unread row
    key fires, a key some file mentions does not, a key only the compiler
    itself names does not (the writer is not a reader), an id-keyed atlas
    child stays quiet, and so does everything under raw_skills' two
    contract sections."""
    base = {
        "compiler.py": 'SHEET = ["id", "coldField"]\n',
        "farever_companion/data/raw_units.py": (
            "DATA = {'units': [{'id': 1, 'coldField': 2}],\n"
            "        'atlas': {'SomeMob': {'x': 1, 'y': 2}},\n"
            "        '__meta__': {'data_version': 'v1'}}\n"),
        "farever_companion/data/raw_skills.py": (
            "DATA = {'skill_rows': [{'id': 1, 'anythingAtAll': 2}],\n"
            "        'skills': [{'id': 2, 'alsoFree': 3}]}\n"),
        "farever_companion/data/units.py": (
            "def load(rows):\n"
            "    return [r.get('id') for r in rows]\n"),
    }

    # 'id' is read (data/units.py), 'coldField' is read by nobody.
    hits = {(s, k) for s, k, _w in _unresolved_baked_keys(base)}
    assert ("raw_units", "coldField") in hits
    assert ("raw_units", "id") not in hits
    # atlas children and __meta__ children never fire.
    assert ("raw_units", "SomeMob") not in hits
    assert ("raw_units", "x") not in hits
    assert ("raw_units", "data_version") not in hits
    # both raw_skills contract sections never fire, however odd the key.
    assert ("raw_skills", "anythingAtAll") not in hits
    assert ("raw_skills", "alsoFree") not in hits
    # the compiler naming a key is not a reader: with every app file gone
    # from the index (shims only - `data/units.py` is app code that names
    # 'id' and must go too), 'id' convicts as well.
    compiler_only = {k: v for k, v in base.items()
                     if k.startswith("farever_companion/data/raw_")}
    compiler_only["compiler.py"] = base["compiler.py"]
    assert any(k == "id" for _s, k, _w in _unresolved_baked_keys(compiler_only))


# --- the hand-recorded game facts -------------------------------------------#
# A hand-recorded game fact is a value the code DECLARES as one with a
# `# HAND-FACT:` comment: a game observation the app cannot derive from any
# sheet or from the compiled shim — a vendor's price, a box's prose, an
# ordering the game never exposes. Those are exactly the values that go stale
# silently when a patch moves the data under them, so each one has to name the
# test that pins it.
#
# The registry below is that list. `guard` is `<relative test path>::<test
# name>`; `None` means the fact has no contract test yet and its label must be
# in `_UNGUARDED_HAND_FACTS` — the ratchet that makes the debt VISIBLE instead
# of silently trusted, the same shape as every other baseline in here. The
# detector is bidirectional: a registered fact must still carry its marker, its
# guard must resolve to a test the runner actually collects, and a NEW marker
# must be registered (so a fresh hand fact cannot be added without a test or a
# line in the debt list).
HAND_FACT_MARKER = "HAND-FACT:"


class HandFactWarning(UserWarning):
    """A hand-recorded game fact whose contract test is missing, misnamed, not
    yet written, or whose marker no longer exists."""


# The crawl reads THIS repo's tracked sources, so a fact consumed or verified by
# the out-of-repo GameFiles tools has to name a test here that still runs.
HAND_GAME_FACTS: tuple[dict, ...] = (
    {
        "label": "_HEROIC_CACHE_VENDORS",
        "where": "farever_companion/data/items/sources.py",
        "guard": "tests/test_item_drops.py"
                 "::test_chaotic_gear_cache_name_is_shared_by_two_different_boxes",
        "detail": "Shiro James sells the two Hero caches for 100 Medal of "
                  "Glory — the seller, currency and PRICE no sheet row carries",
    },
    {
        "label": "_HEROIC_CACHE_SPECS",
        "where": "farever_companion/data/items/sources.py",
        "guard": "tests/test_item_drops.py"
                 "::test_heroic_cache_specs_match_the_compiled_gain_item",
        "detail": "each cache's loot table, level range and maxItems — without "
                  "the game dump the compiled `gain_item` is the only other read",
    },
    {
        "label": "_CACHE_DISPLAY_NAME",
        "where": "farever_companion/data/items/sources.py",
        "guard": "tests/test_item_drops.py"
                 "::test_chaotic_gear_cache_name_is_shared_by_two_different_boxes",
        "detail": "the disambiguating list name for the two boxes the game "
                  "both calls \"Chaotic Gear Cache\"",
    },
    {
        "label": "_CURRENCY_LABEL",
        "where": "farever_companion/data/items/sources.py",
        "guard": "tests/test_items_tab.py"
                 "::test_mog_card_shows_the_shiro_james_exchange",
        "detail": "the player-facing names of the scan's currency ids (the "
                  "sheet records `BadgeOfGlory`, not \"Medal of Glory\")",
    },
    {
        "label": "_CLASS_GLIDERS",
        "where": "farever_companion/data/items/sources.py",
        "guard": "tests/test_item_drops.py::test_no_source_reason_fact_checked",
        "detail": "which glider id belongs to which class starter loadout",
    },
    {
        "label": "_STARTER_TOOLS",
        "where": "farever_companion/data/items/sources.py",
        "guard": "tests/test_item_drops.py::test_no_source_reason_fact_checked",
        "detail": "the gather tools every class starts with, which no row drops",
    },
    {
        "label": "_BASE_CLOTHES_ID",
        "where": "farever_companion/data/items/sources.py",
        "guard": "tests/test_item_drops.py::test_no_source_reason_is_specific",
        "detail": "the default shirt worn at character creation",
    },
    {
        "label": "_LOOT_LISTED_UNANCHORED",
        "where": "farever_companion/data/items/sources.py",
        "guard": "tests/test_item_drops.py::test_no_source_reason_fact_checked",
        "detail": "the loot tables the scan lists but cannot anchor to a "
                  "source (Rift_Tier4, Blacksmith_Starter, the Coyotes table)",
    },
    {
        "label": "_TOWN_ITEM_LEVELS",
        "where": "farever_companion/data/items/sources.py",
        "guard": "tests/test_item_drops.py::test_item_vendor_levels",
        "detail": "the item level each Guild Merchant town sells gear at "
                  "(Tyrna L20, Lower Ramburg L25)",
    },
    {
        "label": "_DUNGEON_POOL_SIZES",
        "where": "farever_companion/data/items/sources.py",
        "guard": None,
        "detail": "the per-faction WorldLoot token pool sizes (Manfish 28, "
                  "Kobold 28, Bee 30)",
    },
    {
        "label": "_CRAFT_POOL_SIZE",
        "where": "farever_companion/data/items/sources.py",
        "guard": None,
        "detail": "how many craft variants the WorldLoot token can expand to",
    },
    {
        "label": "_UPGRADE_RARE_POOL_SIZE",
        "where": "farever_companion/data/items/sources.py",
        "guard": None,
        "detail": "the UpgradeRare token pool size, read as a 1-in-N chance",
    },
    {
        "label": "_SLOT_ORDER",
        "where": "farever_companion/data/items/sources.py",
        "guard": None,
        "detail": "the gear-slot order every contents list sorts by",
    },
)

# The hand facts with NO contract test, ratcheted here so the debt is visible —
# and so a NEW unguarded fact (which lands in the report instead) is told apart
# from the pre-existing ones. Delete an entry once its fact is guarded; these
# four are only exercised INDIRECTLY (the pool sizes, for instance, are never
# asserted, only divided by), which is precisely why they are on the list.
_UNGUARDED_HAND_FACTS: dict[str, str] = {
    "_DUNGEON_POOL_SIZES": "no test asserts a per-faction pool size; only "
                           "divided by",
    "_CRAFT_POOL_SIZE": "no test asserts the craft-variant pool size",
    "_UPGRADE_RARE_POOL_SIZE": "no test asserts the UpgradeRare pool size",
    "_SLOT_ORDER": "no test pins the slot tuple's contents; the order is only "
                   "exercised through a rendered contents list",
}


def _hand_fact_markers(texts: dict[str, str]) -> dict[str, str]:
    """label -> the tracked file its `# HAND-FACT:` comment sits in.

    The marker is the census's DOMAIN: a hand-recorded game fact is one the code
    says is one, so a new fact cannot be added without this seeing it. The
    census file itself is skipped — it prints the convention and would match its
    own literal.
    """
    out: dict[str, str] = {}
    for rel, src in texts.items():
        if rel.endswith("tests/test_repo_hygiene.py"):
            continue
        for m in re.finditer(r"#\s*HAND-FACT:\s*([A-Za-z_][\w.]*)", src):
            out.setdefault(m.group(1), rel)
    return out


def _tracked_test_functions(texts: dict[str, str]) -> set[str]:
    """Every top-level `def test_...` in the tracked tests, as `<rel>::<name>`.

    Only module-level tests count: a guard the registry names has to be one the
    runner actually collects, and a name nested in a class or helper is not.
    """
    out: set[str] = set()
    for rel, src in texts.items():
        if not (rel.startswith("tests/") and rel.endswith(".py")):
            continue
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for node in tree.body:
            if (isinstance(node, ast.FunctionDef)
                    and node.name.startswith("test_")):
                out.add(f"{rel}::{node.name}")
    return out


def _hand_fact_findings(texts: dict[str, str],
                        facts: tuple[dict, ...] = HAND_GAME_FACTS,
                        unguarded: dict[str, str] = _UNGUARDED_HAND_FACTS,
                        markers: dict[str, str] | None = None,
                        guards: set[str] | None = None) -> list[str]:
    """Every way a hand-recorded game fact is not accounted for.

    `markers`/`guards` are injectable so the detector's own test can pin it on a
    tiny tree instead of the whole repo.
    """
    markers = _hand_fact_markers(texts) if markers is None else markers
    guards = _tracked_test_functions(texts) if guards is None else guards
    known = {f["label"] for f in facts}
    problems: list[str] = []
    for f in facts:
        label, rel = f["label"], f["where"]
        if markers.get(label) != rel:
            problems.append(
                f"HAND FACT NOT DECLARED: {label!r} is registered at {rel}, but "
                f"no `# {HAND_FACT_MARKER} {label}` marker is there (found in "
                f"{markers.get(label) or 'nowhere'}). Put the marker back next "
                f"to the fact, or drop the entry."
            )
            continue
        guard = f.get("guard")
        if guard is None:
            if label not in unguarded:
                problems.append(
                    f"UNGUARDED HAND FACT: {label!r} ({rel}) records a game "
                    f"fact that no test pins. Write the contract test, or list "
                    f"it in _UNGUARDED_HAND_FACTS with the reason it is "
                    f"trusted."
                )
        elif guard not in guards:
            problems.append(
                f"HAND FACT GUARD MISSING: {label!r} names guard {guard!r}, "
                f"which is not a module-level test in the tracked tree. Fix the "
                f"name, or move the fact to _UNGUARDED_HAND_FACTS."
            )
    for label, where in sorted(markers.items()):
        if label not in known:
            problems.append(
                f"NEW HAND FACT: {where} declares `# {HAND_FACT_MARKER} "
                f"{label}` but HAND_GAME_FACTS does not register it. Add an "
                f"entry naming its contract test (or list it in "
                f"_UNGUARDED_HAND_FACTS)."
            )
    for label in sorted(unguarded):
        if label not in known:
            problems.append(
                f"STALE RATCHET: _UNGUARDED_HAND_FACTS names {label!r}, which "
                f"no registered hand fact uses. Remove it."
            )
    return problems


def test_every_hand_recorded_game_fact_names_its_contract_test():
    """Every `# HAND-FACT:` in the tree is registered, and every registered
    fact is either pinned by a named test or recorded as debt.

    A hand-recorded game fact (the cache vendor's price, a box's prose, a pool
    size the game never exposes) is a claim nothing recomputes, so it goes
    stale in silence. This puts the claim and its test side by side, and turns a
    NEW one into a report until it has a test or a line in
    `_UNGUARDED_HAND_FACTS`. Same policy as the censuses above: warn locally,
    fail under CI.
    """
    tracked = _tracked_paths(ROOT)
    if tracked is None:
        pytest.skip("not a git checkout; tracked sources cannot be enumerated")

    texts = _tracked_sources(tracked)
    assert len(texts) > 100, (
        f"scan looks wrong: only {len(texts)} python files read, so the guard "
        "would pass vacuously")
    markers = _hand_fact_markers(texts)
    assert len(markers) >= len(HAND_GAME_FACTS), (
        f"only {len(markers)} `{HAND_FACT_MARKER}` markers found for "
        f"{len(HAND_GAME_FACTS)} registered facts — the marker scan is not "
        "seeing the tree")

    problems = _hand_fact_findings(texts)
    _report(problems, label="hand-recorded game fact guard",
            warning=HandFactWarning,
            tail="Give each fact a contract test, or list it in "
                 "_UNGUARDED_HAND_FACTS with the reason it is trusted. ")


def test_the_hand_fact_guard_actually_detects():
    """Pin the detector on each way a fact is accounted for.

    A registered fact with its marker and a resolvable guard stays quiet, while
    a missing marker, a dangling guard, a fresh marker with no entry and a
    stale ratchet entry each report. A fact on the ratchet stays quiet, like
    every baseline here.
    """
    texts = {
        "tests/test_pkg.py": "def test_it():\n    pass\n",
        "pkg/facts.py": (
            f"# {HAND_FACT_MARKER} KNOWN\nKNOWN = 1\n"
            f"# {HAND_FACT_MARKER} DANGLING\nDANGLING = 2\n"
            f"# {HAND_FACT_MARKER} RATCHETED\nRATCHETED = 3\n"
            f"# {HAND_FACT_MARKER} NEW_ONE\nNEW_ONE = 4\n"),
    }
    facts = (
        {"label": "KNOWN", "where": "pkg/facts.py",
         "guard": "tests/test_pkg.py::test_it"},
        {"label": "DANGLING", "where": "pkg/facts.py",
         "guard": "tests/test_pkg.py::gone"},
        {"label": "RATCHETED", "where": "pkg/facts.py", "guard": None},
        {"label": "WRONG_FILE", "where": "pkg/other.py",
         "guard": "tests/test_pkg.py::test_it"},
    )
    found = _hand_fact_findings(texts, facts, {"RATCHETED": "known debt",
                                              "STALE": "known debt"})
    blob = "\n".join(found)
    assert "WRONG_FILE" in blob and "NOT DECLARED" in blob, blob
    assert "DANGLING" in blob and "GUARD MISSING" in blob, blob
    assert "NEW_ONE" in blob and "NEW HAND FACT" in blob, blob
    assert "STALE" in blob and "STALE RATCHET" in blob, blob
    # a registered fact with its marker and a live guard, and one on the
    # ratchet, are both quiet — the detector must not cry wolf
    assert "KNOWN" not in blob, blob
    assert "RATCHETED" not in blob, blob
    assert len(found) == 4, found


def test_every_relative_import_resolves_to_a_module_that_exists():
    """No relative import may point at a module the package does not have.

    pyflakes reports undefined names, never a module path that does not exist
    and never a name the target module does not bind, and a failed import is
    only loud when nothing catches it: the five the config split broke all sat
    inside `try/except` in deferred imports, where the failure is a feature
    quietly doing less (shared speedrun history stopped merging, companion
    categories fell back to `pets`), and the `core/dps_data.py` one that
    predated the split had never once run. The module half is exact - the dots
    and the path name one target, so it needs no baseline - while the name half
    reads the target's module-level bindings and stays quiet whenever they
    cannot be enumerated (see `_unresolved_relative_imports`). It was written
    from a real find: `from ...geo import geo_zones` in the speedrun overlay
    named a module and a binding that do not exist, so its dungeon name had
    been "--" behind a bare except since the line was typed.
    """
    tracked = _tracked_paths(ROOT)
    if tracked is None:
        pytest.skip("not a git checkout; tracked sources cannot be enumerated")

    sources = {rel: text for rel, text in _tracked_sources(tracked).items()
               if rel.startswith("farever_companion/")}
    assert len(sources) > 100, (
        f"scan looks wrong: only {len(sources)} package files read, so the "
        "guard would pass vacuously")
    scanned = _relative_import_count(sources)
    assert scanned > 400, (
        f"scan looks wrong: {len(sources)} package files read but only "
        f"{scanned} relative imports found (883 when this guard was added), "
        "so a broken walk is passing it vacuously")

    problems = [
        f"UNRESOLVED RELATIVE IMPORT: {rel}:{line} {spelling!r} "
        + ("names a module the package does not have"
           if why == "module" else
           "names something its module does not bind")
        + " - the import raises, or, inside a try/except, quietly degrades "
          "whatever it guards."
        for rel, line, spelling, why in _unresolved_relative_imports(PKG, sources)
    ]
    _report(problems, label="relative-import guard",
            warning=DeadCodeWarning,
            tail="Fix the dots, the module path or the imported name. ")


def test_the_relative_import_detector_actually_detects(tmp_path):
    """Pin the resolver on the shapes that must stay quiet - a submodule, an
    attribute of a real module, a re-export reached through a package's
    __init__, a name the target mentions, a target whose names cannot be
    enumerated (a star import, a module `__getattr__`) - and the three that
    must not: a single-dot path landing on a package that is not there, a name
    the target module does not bind, and `from . import name` naming nothing at
    all (the shape every page uses to reach its mixin modules)."""
    pkg = tmp_path / "farever_companion"
    init_text = ("from .model import Settings\n"
                 "# clockwork lives here one day\n")      # a mention, not a name
    model_text = ("Settings = 1\n"
                  "if True:\n    Late = 1\n"
                  "try:\n    import json as _json\nexcept ImportError:\n"
                  "    _json = None\n")
    star_text = "from .model import *\n"
    lazy_text = "def __getattr__(name):\n    return name\n"
    (pkg / "core").mkdir(parents=True)
    (pkg / "core" / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "core" / "real.py").write_text("thing = 1\n", encoding="utf-8")
    (pkg / "config").mkdir()
    (pkg / "config" / "__init__.py").write_text(init_text, encoding="utf-8")
    (pkg / "config" / "model.py").write_text(model_text, encoding="utf-8")
    (pkg / "config" / "star.py").write_text(star_text, encoding="utf-8")
    (pkg / "config" / "lazy.py").write_text(lazy_text, encoding="utf-8")

    # Every synthetic file travels with the case, or the resolver cannot see
    # the target's source and would silently skip the name half.
    sources = {
        "farever_companion/core/__init__.py": "",
        "farever_companion/core/real.py": "thing = 1\n",
        "farever_companion/config/__init__.py": init_text,
        "farever_companion/config/model.py": model_text,
        "farever_companion/config/star.py": star_text,
        "farever_companion/config/lazy.py": lazy_text,
    }

    def scan(rel: str, text: str):
        return _unresolved_relative_imports(pkg, {**sources, rel: text})

    def why(rel: str, text: str):
        return [(line, kind) for _rel, line, _spelling, kind in scan(rel, text)]

    quiet = ("from .real import thing\n"
             "from ..config import Settings\n"
             "from . import real\n"
             "from . import *\n"
             "from ..config.model import Settings\n"
             "from ..config.model import Late\n"       # bound under `if`
             "from ..config.model import _json\n"      # bound under try/except
             "from ..config.star import Anything\n"
             "from ..config.lazy import Anything\n")
    assert scan("farever_companion/core/user.py", quiet) == []
    assert scan("farever_companion/config/other.py",
                "from . import Settings\n") == []

    assert why("farever_companion/core/user.py",
               "from .data.items import catalog\n") == [(1, "module")]
    assert why("farever_companion/core/user.py",
               "from ..config.model import Setings\n") == [(1, "name")]
    assert why("farever_companion/config/other.py",
               "from ..config import Missing\n") == [(1, "name")]
    assert why("farever_companion/config/other.py",
               "from . import planner\n") == [(1, "name")]
    # Named by a COMMENT only - the shape that let a moved module hide behind
    # its own package docstring as long as the prose still mentioned it.
    assert why("farever_companion/config/other.py",
               "from . import clockwork\n") == [(1, "name")]


def test_tracked_lookup_folds_case_like_the_filesystem(tmp_path, monkeypatch):
    """A tracked `Run.bat` must not be reported because the tree spells it
    `run.bat`.

    The committed developer launcher is `Run.bat`; a tool that rewrites the file
    leaves the directory entry lowercase, and on Windows that is the same file -
    `git status` says nothing about it, while a raw string compare against the
    index reported "DEAD/UNUSED SCRATCH outside ai/workspace/: run.bat" in every
    run. Folding both sides with `os.path.normcase` fixes the report without
    weakening the guard on a case-sensitive filesystem, where the two spellings
    really are two files.
    """
    root = tmp_path / "repo"
    root.mkdir()
    (root / "run.bat").write_text("@echo off\n", encoding="utf-8")
    tracked = {"Run.bat", "compiler.py"}

    # Case-insensitive (Windows): `run.bat` IS the tracked `Run.bat`.
    monkeypatch.setattr(os.path, "normcase", lambda p: p.lower())
    assert _root_strays(root, tracked) == []

    # Case-sensitive: nothing tracked owns that name here, so it is a stray.
    monkeypatch.setattr(os.path, "normcase", lambda p: p)
    assert [p.name for p in _root_strays(root, tracked)] == ["run.bat"]


def test_repo_root_holds_no_stray_files():
    """Report every repo-root strays: untracked, not build output, not scratch.

    Tools that render mockups, probes and screenshots write folders at the repo
    root (`tmp_preview/` was one), and nothing else in this suite notices them —
    the package guard only looks inside `farever_companion/`. The allowed scratch
    location is `ai/workspace/`, so anything else untracked at the root is named
    in the warnings summary on every run (and fails the run under CI).

    Skipped when git is unavailable (or this is not a checkout): without the
    tracked set there is no way to tell committed source from scratch, and a
    guard that guesses is worse than one that stays quiet.
    """
    tracked = _tracked_paths(ROOT)
    if tracked is None:
        pytest.skip("not a git checkout (or git unavailable) - cannot tell "
                    "committed source from repo-root scratch")

    problems: list[str] = []
    for p in _root_strays(ROOT, tracked):
        n, size = _stray_size(p)
        what = f"{n} file(s), {size} bytes" if p.is_dir() else f"{size} bytes"
        rel = p.relative_to(ROOT).as_posix()
        problems.append(
            f"DEAD/UNUSED SCRATCH outside {SCRATCH_DIR}/: "
            f"{rel}{'/' if p.is_dir() else ''} ({what}). Not tracked by git, so "
            f"this repo does not own it: delete it, or move it into "
            f"{SCRATCH_DIR}/<tool>/.")
    _report(problems)


def test_tests_never_run_against_the_live_moddata_dir():
    """The suite must never resolve config_dir() to the user's real moddata.

    That folder is where settings.json, collection.json and every profile
    live. A test that builds a real `Settings`/`ControlPanel` while pointing
    there writes a defaults file over the user's own settings — which is
    exactly what happened on 2026-09-17: the live settings.json was left
    differing from fresh defaults in one key (`farever_game_dir`), and every
    toggle the user had set was gone after each test run.

    tests/conftest.py redirects FAREVER_MODDATA_DIR for the whole session;
    this pins that it stays redirected, so removing the fixture fails loudly
    here instead of quietly costing the user their settings again.
    """
    from farever_companion.config import config_dir

    live = (ROOT / "dist" / "moddata").resolve()
    got = Path(config_dir()).resolve()
    assert got != live, (
        "the test session is pointed at the LIVE moddata folder "
        f"({live}) - any test that saves Settings would overwrite the user's "
        "settings.json with defaults. Check tests/conftest.py's "
        "_isolated_moddata fixture.")
    assert live not in got.parents, (
        f"config_dir() resolves inside the live moddata folder ({got}); the "
        "session must run against a throwaway folder.")


def test_conftest_moddata_redirect_outlives_the_session():
    """The redirect must survive PAST session teardown, not just the tests.

    `test_tests_never_run_against_the_live_moddata_dir` above checks the state
    DURING a test, which the old fixture satisfied: it only removed
    FAREVER_MODDATA_DIR in a teardown `finally`. Session teardown runs BEFORE
    Qt teardown, so the widgets and OverlayManagers destroyed afterwards
    resolved config_dir() back to the live folder.

    Measured 2026-09-19: every plain `pytest tests/test_ui_utils.py` run ended
    with ~10 `defaults-instance ... open=[]` audit lines in the LIVE
    settings_audit.log and left open_overlays empty, so the HUDs stopped
    restoring and every run looked like "my settings turned off again". The
    leak is only observable after the process's tests are done, so this pins
    the fixture SOURCE rather than a runtime value.
    """
    src = source_guard.read_text(ROOT / "tests" / "conftest.py", encoding="utf-8")
    assert "os.environ.pop(\"FAREVER_MODDATA_DIR\"" not in src
    assert "FAREVER_MODDATA_DIR\"] = prev" not in src


def test_every_settings_write_path_resolves_outside_live_moddata():
    """Sharper than the config_dir() check: the paths that actually write.

    settings.json, the audit log and the dps history folder must all resolve
    into the throwaway folder while the suite runs.
    """
    from farever_companion import config as cfg

    live = (ROOT / "dist" / "moddata").resolve()
    for fn in (cfg.config_dir, cfg._settings_path, cfg._audit_path, cfg.dps_dir):
        got = Path(fn()).resolve()
        assert got != live and live not in got.parents, (
            f"{fn.__name__}() resolves to the live moddata folder ({got})")


# AGENTS.md names files in prose as well as in the page table, and that prose had
# no contract at all. It carried `combat_state_overlay.py` for releases after the
# file was deleted, and cited `tests/test_ui_file_under_line_budget` as though it
# were a file when it is a function inside tests/test_ui_utils.py. The page table
# has a guard for exactly this reason (a row anchored on a file that stopped
# existing), so the same reasoning applies to every other `.py` in the document.
#
# The invariant is the weak one on purpose: a cited `.py` must exist *somewhere*
# in the tracked tree. Prose legitimately cites a bare basename next to the
# sentence that gives its directory, and pinning an exact path would force the
# doc to repeat paths it has no room for.

# Outside the repo by design: the calibrator's copy of constants.py. Never
# tracked, so it can never resolve, but the doc must keep naming it or the
# two-file sync hazard stops being actionable.
AGENTS_DOC_EXTERNAL_PATHS = frozenset({
    "GameFiles/Farever/Offsets/constants.py",
    # The bridge offsets GENERATOR lives in the sibling GameFiles repo, which is
    # a separate git tree: AGENTS.md documents it because it is the other half
    # of the "offsets live in constants.py and nowhere else" rule (the C tree
    # has no other source), not because it belongs to this repo.
    "GameFiles/Farever/hooks/gen_bridge_offsets.py",
})


def _agents_doc_citations(md: str) -> list[str]:
    """Every `.py` token in AGENTS.md meant to name a real file.

    Drops the three shapes that are not citations: a command inside a code span
    (`git log -- farever_companion/constants.py`), a placeholder
    (`tests/test_<area>.py`), and a glob (`ui/pages/*.py`).
    """
    out: list[str] = []
    for raw in _BACKTICKED.findall(md):
        tok = raw.strip()
        if not tok.endswith(".py"):
            continue
        if any(ch in tok for ch in " <>*") or " " in tok:
            continue
        out.append(tok)
    return out


def _doc_citation_resolves(token: str, sources: dict[str, str]) -> bool:
    """True when a cited `.py` names a real file.

    The document is not consistent about what a path is rooted at: the page
    table is repo-relative (`ui/pages/combat_page.py`) while the prose is
    package-relative (`core/dps_tracker.py`, `ui/overlay_manager.py`). Both
    spellings are correct prose, so both roots are tried before a path is
    called missing. A bare basename matches on its last segment.
    """
    for root in ("", "farever_companion/"):
        probe = root + token
        if probe in sources or f"{probe}/__init__.py" in sources:
            return True
    if "/" in token:
        return False                    # an explicit path that missed is a miss
    return any(p.endswith("/" + token) for p in sources)


def _agents_doc_gaps(md: str, sources: dict[str, str]) -> list[str]:
    problems: list[str] = []
    for tok in dict.fromkeys(_agents_doc_citations(md)):
        if tok in AGENTS_DOC_EXTERNAL_PATHS:
            continue
        if not _doc_citation_resolves(tok, sources):
            problems.append(
                f"{AGENTS_MD} names `{tok}`, which is not in the tracked tree")
    return problems


def test_agents_md_only_names_files_that_exist():
    """Every `.py` AGENTS.md cites must be a real file.

    Prose drifts the same way the page table did. This one is prose, so it is
    more likely: a module is renamed or dropped and the sentence describing it
    keeps its backticked name, reading fine the whole time.
    """
    tracked = _tracked_paths(ROOT)
    if tracked is None:
        pytest.skip("not a git checkout; tracked sources cannot be enumerated")

    md_path = ROOT / AGENTS_MD
    assert md_path.is_file(), f"{AGENTS_MD} is missing"
    md = source_guard.read_text(md_path, encoding="utf-8")

    cited = _agents_doc_citations(md)
    assert len(cited) >= 30, (
        f"only {len(cited)} `.py` citations found in {AGENTS_MD} (30+ when this "
        f"guard was added) - the token pattern changed, or this guard is "
        f"scanning nothing")

    problems = _agents_doc_gaps(md, _tracked_sources(tracked))
    assert not problems, (
        f"{len(problems)} file(s) named in {AGENTS_MD} do not exist:\n  "
        + "\n  ".join(problems)
        + "\nFix the document or restore the file. If a path is deliberately "
          "outside the repo, add it to AGENTS_DOC_EXTERNAL_PATHS with a reason.")


def test_the_agents_doc_guard_actually_detects():
    """Pin the shapes: a deleted module, and a test cited as a file.

    The first is the shape that shipped; the second is the one that hid in
    plain sight, because `test_ui_file_under_line_budget` is a real name - just
    a function, not a file.
    """
    sources = {
        "farever_companion/__init__.py": "",
        "farever_companion/ui/overlays/dummy_overlay.py": "",
        "farever_companion/core/scene.py": "",
        "tests/test_ui_utils.py": "",
    }
    dead = "`combat_state_overlay.py` is a leftover."
    assert _agents_doc_gaps(dead, sources) == [
        f"{AGENTS_MD} names `combat_state_overlay.py`, which is not in the "
        f"tracked tree"
    ], "a deleted module cited in prose must be reported"

    # A bare basename that does exist anywhere resolves.
    ok = "read `scene.py` and `dummy_overlay.py` first."
    assert _agents_doc_gaps(ok, sources) == [], "a real basename must resolve"

    # An explicit repo-relative path that misses is a miss, not a basename hunt.
    miss = "see `ui/overlays/gone_overlay.py`."
    assert _agents_doc_gaps(miss, sources) != [], "a dead explicit path must fail"

    # The three non-citation shapes are ignored, not reported as dead.
    noise = ("`git log -- farever_companion/constants.py`",
             "`tests/test_<area>.py`", "`ui/pages/*.py`",
             "`constants.py`")
    assert _agents_doc_citations(" ".join(noise)) == ["constants.py"]

    # A deliberately external path is exempt, and only if listed.
    ext = f"`{sorted(AGENTS_DOC_EXTERNAL_PATHS)[0]}` holds the calibrator copy."
    assert _agents_doc_gaps(ext, sources) == [], "listed external path is exempt"


# --- scripts name only files that exist ----------------------------------
# A CI workflow, a .bat launcher or a release .ps1 is wiring: it names the file
# it runs. `ci.yml` kept running `pytest tests/test_scan_guards.py` for months
# after that test was deleted - the step exited 4 on every CI run, and nothing
# in the repo could see it: prose and code were both censused, a script was
# neither. So the wiring gets the same treatment.
#
# Only tokens rooted at a directory this repo actually has count as repo
# references. That keeps the sibling `GameFiles/...` tree, the VS toolchain,
# `.venv\...`, a URL tail and build output (`dist\...`) out by construction
# rather than by a per-file skip list - a skip list rots exactly the way the
# reference did.
SCRIPT_REF_BASES = frozenset({
    "tests", "farever_companion", "build_tools", "dps_bridge", "native",
    "assets", "docs",                                  # repo-root directories
    "core", "ui", "data", "runtime", "backup", "geo", "config",  # layer names
})

# Extensions a script can point at. `.exe`/`.dll` are deliberately absent: those
# are build output (`dist/FareverPal.exe`, `dps_bridge/farever_dps.dll`) and are
# never a tracked file, so listing them would only create false failures.
SCRIPT_REF_SUFFIXES = ("py", "bat", "ps1", "spec", "toml", "cfg", "ini",
                       "json", "yml", "yaml", "md")

_SCRIPT_REF_RE = re.compile(
    r"[A-Za-z0-9_][A-Za-z0-9_.\\/-]*\.(?:"
    + "|".join(SCRIPT_REF_SUFFIXES) + r")")

# Refs that MUST stay missing. release.ps1's pre-flight refuses to cut a release
# when `core/inject.py` exists - the read-only guarantee is asserted BY that
# absence, so the guard would otherwise accuse the check of naming a file it
# needs gone. Add a name here only with the check that requires it absent.
SCRIPT_REF_ABSENT_BY_DESIGN = {
    "core/inject.py": "release.ps1 asserts the read-only package has no injector",
}


def _script_files(tracked: set[str]) -> list[str]:
    """Tracked CI workflows + launchers: where a rename breaks the build."""
    return sorted(
        rel for rel in tracked
        if rel.endswith((".bat", ".ps1"))
        or (rel.startswith(".github/") and rel.endswith((".yml", ".yaml"))))


def _script_ref_tokens(text: str) -> list[str]:
    """Path-shaped tokens in a script that could name a file in this repo.

    Drops the shapes that are not repo paths: a `%~dp0`/`dp0..\\` expansion, any
    `..` hop, and anything not rooted at a directory this repo has.
    """
    out: list[str] = []
    for raw in _SCRIPT_REF_RE.findall(text):
        tok = raw.replace("\\", "/")
        while tok.startswith("./"):
            tok = tok[2:]
        if ".." in tok or "dp0" in tok or "%~" in tok:
            continue
        if tok.split("/", 1)[0] not in SCRIPT_REF_BASES:
            continue
        out.append(tok)
    return out


def _repo_ref_resolves(token: str, tracked: set[str],
                       package_ok: bool = False) -> bool:
    """A repo file under either root: repo-relative (`tests/...`) or
    package-relative (`core/...` == `farever_companion/core/...`).

    A doubled separator is collapsed first: a Windows path written inside a
    string literal shows its escape through, so such a token arrives here as
    `build_tools//doctor.py` - one path written twice over, not a name that
    resolves nowhere.

    `package_ok` also accepts `a/b.py` when `a/b/__init__.py` exists: prose that
    says "split from ui/pages/settings.py" names a module that has since become
    the package, and still points at that code. The script guard leaves it off,
    because a launcher that RUNS `a/b.py` needs that file, not a package.
    """
    token = re.sub(r"/{2,}", "/", token)
    for root in ("", "farever_companion/"):
        if root + token in tracked:
            return True
        if (package_ok and token.endswith(".py")
                and f"{root}{token[:-3]}/__init__.py" in tracked):
            return True
    return False


def _script_ref_gaps(scripts: dict[str, str], tracked: set[str]) -> list[str]:
    problems: list[str] = []
    for rel, text in sorted(scripts.items()):
        for tok in dict.fromkeys(_script_ref_tokens(text)):
            if tok in SCRIPT_REF_ABSENT_BY_DESIGN:
                continue
            if not _repo_ref_resolves(tok, tracked):
                problems.append(
                    f"{rel} names `{tok}`, which is not in the tracked tree")
    return problems


def test_scripts_name_only_files_that_exist():
    """Every repo file a CI workflow, .bat or .ps1 names must be tracked.

    The failure this exists for: `ci.yml` held a step running
    `pytest tests/test_scan_guards.py` after that test was deleted, so CI failed
    on every push with pytest's exit 4 while the workflow itself still read as
    fine. A launcher has the same failure mode - a renamed script or spec stays
    invisible until someone runs the build.
    """
    tracked = _tracked_paths(ROOT)
    if tracked is None:
        pytest.skip("not a git checkout; tracked sources cannot be enumerated")

    scripts: dict[str, str] = {}
    for rel in _script_files(tracked):
        try:
            scripts[rel] = source_guard.read_text(ROOT / rel, encoding="utf-8",
                                                  errors="replace")
        except OSError:
            continue
    assert len(scripts) >= 10, (
        f"only {len(scripts)} scripts scanned - the extension or directory "
        f"filter changed, or this guard is scanning nothing: {sorted(scripts)}")

    problems = _script_ref_gaps(scripts, tracked)
    assert not problems, (
        f"{len(problems)} reference(s) in CI workflows / launchers name a file "
        "that does not exist:\n  " + "\n  ".join(problems)
        + "\nFix the script or restore the file. Only tokens rooted at a repo "
          "directory are checked, so a deliberately external path needs no "
          "entry; a file that must stay ABSENT goes in "
          "SCRIPT_REF_ABSENT_BY_DESIGN with the check that requires it gone.")


def test_the_script_reference_detector_actually_detects():
    r"""Pin the shapes: the deleted test, the renamed launcher, and the noise.

    The first shipped: `python -m pytest tests/test_scan_guards.py` in ci.yml.
    The second is the launcher case - a `build_tools\` script renamed while the
    `.bat` that calls it keeps the old name. Everything on the quiet side must
    stay quiet, or the guard would fail a correct script: a live repo ref, a
    package-relative ref, the sibling tree, the toolchain, a URL tail and build
    output.
    """
    tracked = {"tests/test_ok.py", "farever_companion/core/scene.py",
               "build_tools/doctor.bat", "native/Cargo.toml"}

    ci = ("      - name: Scan-guard tests must run (not skip)\n"
          "        run: |\n"
          "          $out = python -m pytest tests/test_scan_guards.py -q 2>&1\n")
    assert _script_ref_gaps({".github/workflows/ci.yml": ci}, tracked) == [
        ".github/workflows/ci.yml names `tests/test_scan_guards.py`, "
        "which is not in the tracked tree"], "the shipped break must be caught"

    launch = "call build_tools\\env_doctor.bat\r\n"
    assert _script_ref_gaps({"build_tools/doctor.bat": launch}, tracked) == [
        "build_tools/doctor.bat names `build_tools/env_doctor.bat`, "
        "which is not in the tracked tree"], "a renamed launcher must be caught"

    quiet = ("python -m pytest tests/test_ok.py\n"
             "call build_tools\\doctor.bat\n"
             "set REF=%CD%\\..\\GameFiles\\Farever\\hooks\\build_all.bat\n"
             "call .venv\\Scripts\\activate\n"
             "call \"%VS%\\VC\\Auxiliary\\Build\\vcvars64.bat\"\n"
             "pyinstaller --noconfirm FareverPal.spec\n"
             "path: dist/FareverPal.exe\n"
             "url: https://example.test/releases/download/v1/FareverPal.exe\n")
    assert _script_ref_gaps({".github/workflows/ci.yml": quiet}, tracked) == [], (
        "a correct script must not be accused")

    # A file that must stay absent is exempt only because it is listed, and its
    # neighbour is still reported.
    assert _script_ref_tokens("core\\inject.py\n") == ["core/inject.py"]
    assert "core/inject.py" in SCRIPT_REF_ABSENT_BY_DESIGN
    assert _script_ref_gaps({"release.ps1": "core\\gone.py\n"}, tracked) == [
        "release.ps1 names `core/gone.py`, which is not in the tracked tree"]


# --- code and docs name only files that exist -----------------------------
# The script guard above covers wiring: a workflow, a `.bat` or a `.ps1` naming
# the file it runs. The same drift lives in the two other places a path gets
# written down - a code comment that says "see X", and a document that points at
# the module behind a rule - and both read as correct while naming a file that
# was renamed or deleted. So both are censused here, reusing the script guard's
# token scanner and repo-directory rule; only the file scope differs. (`md`
# joined the shared suffix set when this census landed: a workflow can name a
# document as easily as it names a module, and none does today.)
#
# Scope is what keeps this about *paths* and not prose:
#
#   * Test modules are not read. A test legitimately writes paths that were
#     never files - a shape invented to assert on (`tests/test_a.py`), a helper
#     that never existed - and names the test it absorbed in a merge ("Merged
#     without changing any test, from: tests/test_source_guard.py"). The wiring
#     half of that concern, a workflow naming a deleted test, is the script
#     guard's job, and it is the half that actually broke CI.
#   * Dated handoff records are not read. `docs/HANDOFF_2026-10-05_*.md` names
#     files as they stood the day it was written; a rename the next day is not
#     an error in the record, and editing the record to match today would
#     falsify it.
#   * A path git IGNORES is out of scope. `docs/ARCHITECTURE.md` and the loose
#     `assets/data/*.json` sheets are local-only by `.gitignore` - they exist
#     for nobody but the maintainer - so a citation of one is not a dangling
#     reference to a file this repo carries. The rule is `.gitignore`'s, read
#     through `git check-ignore`, never a list of names here, so a newly ignored
#     area is covered the day it is ignored.
#
# What is left is the request, exactly: a path that names nothing.

# A handoff is a record of a day, not a live pointer to today's tree.
_HANDOFF_RECORD_RE = re.compile(r"^docs/HANDOFF_\d{4}-\d{2}-\d{2}_")


def _code_doc_ref_files(tracked: set[str]) -> tuple[list[str], list[str]]:
    """The (code, docs) this census reads: every tracked `.py` outside `tests/`
    and every tracked `.md` that is not a dated handoff record."""
    code = sorted(rel for rel in tracked
                  if rel.endswith(".py") and not rel.startswith("tests/"))
    docs = sorted(rel for rel in tracked
                  if rel.endswith(".md") and not _HANDOFF_RECORD_RE.match(rel))
    return code, docs


def _worktree_paths(root: Path) -> set[str] | None:
    """Every path git can see in the working tree: tracked, plus untracked files
    the repo does not ignore. None when git cannot answer.

    Deliberately wider than `_tracked_paths`, because a citation needs the file
    to BE there: one just written but not yet committed still answers "see X",
    which keeps a work-in-progress tree from failing on its own uncommitted
    work. In CI there are no uncommitted files, so that same citation becomes
    exactly the failure this guard is for - a reference the repository does not
    carry.
    """
    try:
        r = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z", "-c", "-o",
             "--exclude-standard"],
            capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    return {p for p in r.stdout.split("\0") if p}


def _ignored_refs(tokens: set[str], root: Path) -> set[str]:
    """Which of `tokens` git ignores, in one batched `check-ignore`.

    `-z` on both sides: git reads stdin in text mode on Windows and would
    otherwise hand back a carriage return on every path it echoes, an answer
    that matches no token. A non-zero exit only means nothing matched.
    """
    if not tokens:
        return set()
    try:
        r = subprocess.run(["git", "-C", str(root), "check-ignore", "--stdin",
                            "-z"], input="\0".join(sorted(tokens)) + "\0",
                           capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return set()
    return {p for p in r.stdout.split("\0") if p}


def _code_doc_ref_candidates(files: dict[str, str],
                             paths: set[str]) -> list[tuple[str, str]]:
    """(citing file, token) pairs whose token names nothing in `paths`."""
    out: list[tuple[str, str]] = []
    for rel, text in sorted(files.items()):
        for tok in dict.fromkeys(_script_ref_tokens(text)):
            if not _repo_ref_resolves(tok, paths, package_ok=True):
                out.append((rel, tok))
    return out


def _code_doc_ref_gaps(files: dict[str, str], paths: set[str],
                       ignored: set[str] | frozenset[str] = frozenset()
                       ) -> list[str]:
    return [
        f"{rel} names `{tok}`, which does not exist in the working tree"
        for rel, tok in _code_doc_ref_candidates(files, paths)
        if tok not in ignored]


def test_code_and_docs_name_only_files_that_exist():
    """Every repo path named in code or a document must name a real file.

    The failure this exists for is one rename away from the CI break that
    started it: a module moves or a test is deleted, and the comment saying
    "see `core/dps_source.py`" - or the document naming the test that pins a
    rule - keeps reading as correct while pointing at nothing.
    """
    tracked = _tracked_paths(ROOT)
    if tracked is None:
        pytest.skip("not a git checkout; tracked sources cannot be enumerated")
    paths = _worktree_paths(ROOT)
    if paths is None:
        pytest.skip("git cannot enumerate the working tree")

    code, docs = _code_doc_ref_files(tracked)
    assert len(code) >= 200, (
        f"only {len(code)} code files scanned of {len(tracked)} tracked paths "
        f"- the scope filter or the tracked set changed, or this guard is "
        f"scanning nothing")
    assert len(docs) >= 5, f"only {len(docs)} documents scanned: {docs}"

    files: dict[str, str] = {}
    for rel in code + docs:
        try:
            files[rel] = source_guard.read_text(ROOT / rel, encoding="utf-8",
                                                errors="replace")
        except OSError:
            continue

    ignored = _ignored_refs(
        {tok for _, tok in _code_doc_ref_candidates(files, paths)}, ROOT)
    problems = _code_doc_ref_gaps(files, paths, ignored)
    assert not problems, (
        f"{len(problems)} path(s) named in code or docs do not exist:\n  "
        + "\n  ".join(problems)
        + "\nPoint at the file's new path, or drop the name. A path git "
          "ignores (a local note under docs/, a loose data sheet) is out of "
          "scope; test modules and dated handoffs are not read at all.")


def test_the_code_doc_ref_detector_actually_detects():
    """Pin the shapes: a deleted module and a deleted citation in a document,
    plus everything that must stay quiet - the package a module became, an
    ignored local path, a test module, a dated handoff."""
    paths = {
        "farever_companion/core/scene.py",
        "farever_companion/ui/pages/settings/__init__.py",
        "docs/ARCHITECTURE.md",
        "tests/test_ok.py",
    }

    gone = {"farever_companion/core/group_reader.py":
            "read-only by design (see tests/test_readonly_default.py)."}
    assert _code_doc_ref_gaps(gone, paths) == [
        "farever_companion/core/group_reader.py names "
        "`tests/test_readonly_default.py`, which does not exist in the "
        "working tree"], "a comment citing a deleted test must be caught"

    doc = {"CONTRIBUTING.md":
           "The death line is described in\n`core/dps_source.py`.\n"}
    assert _code_doc_ref_gaps(doc, paths) == [
        "CONTRIBUTING.md names `core/dps_source.py`, which does not exist in "
        "the working tree"], "a document must be caught too"

    # A module that became a package still resolves; a name that never was a
    # file does not, however close it looks.
    assert _code_doc_ref_gaps(
        {"farever_companion/ui/pages/settings/shell.py":
         "split from the old ui/pages/settings.py."}, paths) == []
    assert _code_doc_ref_gaps(
        {"farever_companion/ui/pages/settings/shell.py":
         "split from the old ui/pages/settings_page.py."}, paths) == [
        "farever_companion/ui/pages/settings/shell.py names "
        "`ui/pages/settings_page.py`, which does not exist in the working "
        "tree"]

    # A doubled separator is one path written twice over, not a missing file.
    assert _repo_ref_resolves("build_tools//doctor.py",
                              {"build_tools/doctor.py"}) is True
    assert _repo_ref_resolves("build_tools//gone.py",
                              {"build_tools/doctor.py"}) is False

    # An ignored path is exempt only because git ignores it; the neighbouring
    # token in the same file is still reported.
    mixed = {"farever_companion/__init__.py":
             "see docs/notes.md and core/gone.py"}
    assert _code_doc_ref_gaps(mixed, paths, ignored={"docs/notes.md"}) == [
        "farever_companion/__init__.py names `core/gone.py`, which does not "
        "exist in the working tree"]
    assert _code_doc_ref_gaps(mixed, paths) == [
        "farever_companion/__init__.py names `docs/notes.md`, which does not "
        "exist in the working tree",
        "farever_companion/__init__.py names `core/gone.py`, which does not "
        "exist in the working tree"]

    # The census reads code and documents, not tests or dated records.
    code, docs = _code_doc_ref_files({
        "farever_companion/core/scene.py", "tests/test_a.py",
        "build_tools/dev/preview.py", "AGENTS.md", "docs/index.md",
        "docs/HANDOFF_2026-10-05_shop-source-and-ui-fixes.md",
    })
    assert code == ["build_tools/dev/preview.py",
                    "farever_companion/core/scene.py"]
    assert docs == ["AGENTS.md", "docs/index.md"]


BUILD_SCRIPT = "build-mod.bat"


def test_the_build_names_rust_as_missing_instead_of_letting_rustup_fail():
    r"""No Rust must print a fix, not cmd's own "rustup is not recognized".

    Recurring failure: `build-mod.bat` ran `rustup default stable` to cover a
    fresh rustup with no default toolchain, guarded only by `rustc --version`.
    On a box where Rust itself was gone - a stale `%USERPROFILE%\.cargo\bin`
    PATH entry whose directory had been removed - the guard fired, `rustup` was
    then not found, and the last line before the generic red banner was cmd's
    own error. The actual cause (Rust is not installed) was never said, so the
    build *did* fail non-zero but read like a toolchain quirk.

    The contract this pins: probe `rustup` BEFORE using it, and when both are
    absent name the problem and the install command.
    """
    text = (ROOT / BUILD_SCRIPT).read_text(encoding="utf-8", errors="replace")

    # REM comments *describe* the old `rustup default stable` call; only the
    # executable lines decide the order, so the census ignores comments.
    lines = [
        ln for ln in text.splitlines()
        if not ln.lstrip().upper().startswith("REM")
    ]
    probe = next((i for i, ln in enumerate(lines) if "where rustup" in ln), -1)
    run_default = next(
        (i for i, ln in enumerate(lines)
         if ln.strip().lower() == "rustup default stable"),
        -1,
    )
    assert probe != -1, (
        f"{BUILD_SCRIPT} must probe `where rustup` before it runs rustup, or a "
        "machine with no Rust only sees cmd's own error instead of a fix"
    )
    assert run_default != -1, f"{BUILD_SCRIPT} no longer sets a default toolchain"
    assert probe < run_default, (
        "the `where rustup` probe must precede `rustup default stable`, else the "
        "missing-Rust path runs an absent command again"
    )

    for needle in ("Rust is not installed", "Rustlang.Rustup", "rustup.rs"):
        assert needle in text, (
            f"{BUILD_SCRIPT} must tell the user how to install Rust - missing "
            f"{needle!r}"
        )


def test_the_build_reports_every_missing_toolchain_in_one_run():
    """All three toolchain preflights must run before the build gives up.

    Regression: each preflight used to `goto err` on its first miss, so a machine
    with no Rust was never told its MSVC or SDK was missing too - one build run
    per problem. The preflights must now RECORD failures (no early abort) and the
    summary must name every flag that was set, then stop once.
    """
    text = (ROOT / BUILD_SCRIPT).read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()

    def find(pred, what):
        for i, line in enumerate(lines):
            if pred(line):
                return i
        raise AssertionError(f"{what} not found in {BUILD_SCRIPT}")

    start = find(lambda l: l.strip() == 'set "_MISS_RUST="', "preflight flag reset")
    summary = find(lambda l: "REM -- Preflight summary" in l, "preflight summary")
    done = find(lambda l: l.strip() == ":preflight_ok", ":preflight_ok label")
    assert start < summary < done, "preflight / summary / skip-label order changed"

    preflights = "\n".join(lines[start:summary])
    assert "goto err" not in preflights, (
        "a preflight must not abort the script: record the miss and let the other "
        "toolchain checks still run, then summarize"
    )

    block = "\n".join(lines[summary:done])
    assert block.count("goto err") == 1, "the summary must stop the build exactly once"
    for flag in ("_MISS_RUST", "_MISS_MSVC", "_MISS_SDK"):
        assert re.search(rf'set "{flag}=[^"]+"', preflights), (
            f"{flag} is never recorded as a failure"
        )
        assert f"%{flag}%" in block, f"the summary never reports {flag}"
        assert f"if defined {flag}" in block, f"{flag} is reported unconditionally"

