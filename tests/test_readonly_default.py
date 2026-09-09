"""Read-only guarantee: the memory-WRITE path is structurally absent.

The tool can only ever read the game's memory from another process: there is no
injector and no write primitive anywhere in the tree. These tests lock that
contract in place so a future edit can't quietly introduce a write path:

  (a) no injector module exists -- importing one fails.
  (b) core.player exposes the pure-read PlayerLocator.
  (c) model.py selects the pure-read locator.
  (d) the strong backstop: no memory-write primitive (WriteProcessMemory,
      PROCESS_VM_WRITE) appears anywhere in the farever_companion package source.
  (e) the native Rust reader (native/src) exposes no write primitive either:
      no WriteProcessMemory / VirtualAllocEx / VirtualProtectEx / PROCESS_VM_WRITE.
      The handle is opened read-only, so the shipped binary cannot write at all.
  (f) layering guard: the headless layers import no GUI toolkit at module level,
      so they stay importable with Qt absent (a lazy import inside a function,
      or inside `if TYPE_CHECKING:`, is the sanctioned exception).

Everything here is pure source/text inspection plus a couple of guarded imports,
so it runs headless in CI with no live game, no Qt widget, and no native build.
"""
from __future__ import annotations

import ast
import importlib
import pathlib

import pytest

REPO_DIR = pathlib.Path(__file__).resolve().parents[1]
PKG_DIR = REPO_DIR / "farever_companion"
NATIVE_SRC = REPO_DIR / "native" / "src"

# Tokens that would signal a memory-WRITE capability creeping back in. None of
# these may appear anywhere in the package source.
FORBIDDEN_TOKENS = (
    "WriteProcessMemory",   # the Win32 write primitive
    "PROCESS_VM_WRITE",     # the write access right on the process handle
)

# Win32 write APIs that must never appear in the read-only Rust reader. A real
# call requires `use`-importing the symbol, so a substring match on the source
# is a reliable guard (and is robust to formatting).
RUST_FORBIDDEN_TOKENS = (
    "WriteProcessMemory",
    "VirtualAllocEx",
    "VirtualProtectEx",
    "PROCESS_VM_WRITE",
    "PROCESS_VM_OPERATION",
)

# core/, data/ and geo/ are the headless, unit-testable layers. A Qt import at
# MODULE LEVEL in any of them is a boundary violation; a lazy import inside a
# function, or an annotation-only `if TYPE_CHECKING:` import, is allowed.
HEADLESS_LAYERS = ("core", "data", "geo")
_QT_PKGS = ("PySide6", "PyQt5", "PyQt6", "PySide2")


def write_token_offenders(root, token, suffix="*.py"):
    """Paths (relative to `root`) whose source carries `token`."""
    root = pathlib.Path(root)
    return [str(path.relative_to(root))
            for path in root.rglob(suffix)
            if token in path.read_text(encoding="utf-8", errors="replace")]


def _module_level_imports(body):
    """Yield Import/ImportFrom nodes that run at import time -- i.e. at module
    scope, including inside top-level if/try/with -- but NOT those nested inside
    a function or class body (a deferred/lazy import). A `if TYPE_CHECKING:`
    block never executes, so its imports are annotation-only and skipped."""
    for node in body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            yield node
        elif isinstance(node, ast.If):
            if _is_type_checking(node.test):
                continue
            yield from _module_level_imports(node.body)
            yield from _module_level_imports(getattr(node, "orelse", []))
        elif isinstance(node, (ast.Try, ast.With)):
            yield from _module_level_imports(node.body)
            yield from _module_level_imports(getattr(node, "orelse", []))
            yield from _module_level_imports(getattr(node, "finalbody", []))
            for handler in getattr(node, "handlers", []):
                yield from _module_level_imports(handler.body)
        # deliberately do not descend into FunctionDef/AsyncFunctionDef/ClassDef


def _is_type_checking(test):
    """True for `if TYPE_CHECKING:` / `if typing.TYPE_CHECKING:`."""
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    if isinstance(test, ast.Attribute):
        return test.attr == "TYPE_CHECKING"
    return False


def module_level_qt_offenders(root):
    """Paths (relative to `root`) that import a Qt toolkit at module level."""
    root = pathlib.Path(root)
    offenders = []
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in _module_level_imports(tree.body):
            mods = ([alias.name for alias in node.names]
                    if isinstance(node, ast.Import) else [node.module or ""])
            if any(mod.split(".")[0] in _QT_PKGS for mod in mods):
                offenders.append(f"{path.relative_to(root)}:{node.lineno}")
    return offenders


# --- (a) no injector module exists -----------------------------------------
def test_inject_module_does_not_exist():
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("farever_companion.core.inject")


# --- (b) player exposes the pure-read locator ------------------------------
def test_player_has_pure_read_locator():
    try:
        from farever_companion.core import player
    except (OSError, ImportError) as e:  # pragma: no cover - non-Windows hosts
        pytest.skip(f"core.player unavailable on this platform: {e}")
    assert hasattr(player, "PlayerLocator")


# --- (c) model selects the pure-read locator -------------------------------
def test_model_source_uses_pure_read_locator():
    src = (PKG_DIR / "core" / "model.py").read_text(encoding="utf-8")
    assert "PlayerLocator" in src


# --- (d) strong backstop: no write primitive anywhere in the package -------
@pytest.mark.parametrize("token", FORBIDDEN_TOKENS)
def test_no_write_primitive_in_package(token):
    offenders = write_token_offenders(PKG_DIR, token)
    assert not offenders, (
        f"forbidden write-path token {token!r} found in: {offenders}. "
        "The companion is read-only; no memory-write path may be introduced.")


# --- (e) the native Rust reader has no write primitive either --------------
@pytest.mark.parametrize("token", RUST_FORBIDDEN_TOKENS)
def test_no_write_primitive_in_native(token):
    if not NATIVE_SRC.is_dir():  # pragma: no cover - native sources not present
        pytest.skip("native/src not present in this checkout")
    offenders = write_token_offenders(NATIVE_SRC, token, suffix="*.rs")
    assert not offenders, (
        f"forbidden Win32 write API {token!r} found in native source: "
        f"{offenders}. The Rust reader opens the process read-only and must "
        "expose no write path.")


# --- (f) layering guard: the headless layers import no GUI toolkit ---------
def test_headless_layers_have_no_module_level_qt_import():
    offenders = []
    for layer in HEADLESS_LAYERS:
        d = PKG_DIR / layer
        if d.is_dir():
            offenders.extend(module_level_qt_offenders(d))
    assert not offenders, (
        f"GUI toolkit imported at module level in a headless layer: {offenders}. "
        "core/data/geo must import without Qt; defer any Qt use into a "
        "function (see data/icons.py) or move it to ui/.")


# --- the detectors actually detect (the gates must not pass vacuously) ------
def test_the_write_token_detector_actually_detects(tmp_path):
    (tmp_path / "clean.py").write_text("value = 1\n", encoding="utf-8")
    (tmp_path / "dirty.py").write_text(
        "handle = open_process(PROCESS_VM_WRITE)\n", encoding="utf-8")
    assert write_token_offenders(tmp_path, "PROCESS_VM_WRITE") == ["dirty.py"]
    assert write_token_offenders(tmp_path, "WriteProcessMemory") == []


def test_the_qt_layering_detector_actually_detects(tmp_path):
    (tmp_path / "module_level.py").write_text(
        "from PySide6 import QtGui\n", encoding="utf-8")
    (tmp_path / "lazy.py").write_text(
        "def paint():\n    from PySide6 import QtGui\n    return QtGui\n",
        encoding="utf-8")
    (tmp_path / "annotations.py").write_text(
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n    from PySide6 import QtGui\n",
        encoding="utf-8")
    # only the module-level import trips; a function-local or annotation-only
    # import must stay quiet
    assert module_level_qt_offenders(tmp_path) == ["module_level.py:1"]
