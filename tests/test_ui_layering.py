"""UI structural guards.

(a) No ui module grows back into a god-object: each ui/*.py stays under a line
    budget. control_panel.py was 2045 lines before it was split into the page
    mixins; this keeps it from creeping back.
(b) The page modules read from LiveModel only. They must not import the raw
    memory layer (core.proc / core.hl / core.scene / core.attributes), so the
    "ui never reads the process" boundary can't erode one page at a time.

Pure source/AST inspection: runs headless in CI with no Qt and no game.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

PKG_DIR = pathlib.Path(__file__).resolve().parents[1] / "farever_companion"
UI_DIR = PKG_DIR / "ui"

MAX_UI_LINES = 800

# The memory layer (matched on the dotted tail so relative and absolute imports
# both count). Pages drive self.model; they never touch these directly.
# game_attach.py owns the session lifecycle (it may import Proc) and is exempt.
FORBIDDEN_TAILS = (
    "core.proc", "core.hl", "core.scene", "core.attributes",
    "core.damage", "core.player",
)
PAGE_FILES = sorted((UI_DIR / "pages").glob("*.py")) + [
    UI_DIR / "account.py",
]


@pytest.mark.parametrize("path", sorted(UI_DIR.rglob("*.py")), ids=lambda p: p.name)
def test_ui_file_under_line_budget(path):
    n = len(path.read_text(encoding="utf-8").splitlines())
    assert n <= MAX_UI_LINES, (
        f"{path.relative_to(UI_DIR)} is {n} lines (budget {MAX_UI_LINES}). "
        "Split it into a page mixin under ui/pages/ rather than letting the "
        "control panel reabsorb a concern.")


@pytest.mark.parametrize("path", PAGE_FILES, ids=lambda p: p.name)
def test_pages_do_not_import_memory_layer(path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    targets = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            targets.add(mod)
            targets.update(f"{mod}.{a.name}" for a in node.names)
        elif isinstance(node, ast.Import):
            targets.update(a.name for a in node.names)
    offenders = [t for t in targets if any(t.endswith(tail) for tail in FORBIDDEN_TAILS)]
    assert not offenders, (
        f"{path.name} imports the memory layer {sorted(offenders)}; pages read "
        "from LiveModel (self.model) only.")
