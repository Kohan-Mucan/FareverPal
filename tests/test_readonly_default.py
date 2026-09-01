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


def _py_sources():
    for path in PKG_DIR.rglob("*.py"):
        yield path, path.read_text(encoding="utf-8", errors="replace")


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
    offenders = [str(p.relative_to(PKG_DIR)) for p, txt in _py_sources()
                 if token in txt]
    assert not offenders, (
        f"forbidden write-path token {token!r} found in: {offenders}. "
        "The companion is read-only; no memory-write path may be introduced.")


# --- (e) the native Rust reader has no write primitive either --------------
# Win32 write APIs that must never appear in the read-only reader. A real call
# requires `use`-importing the symbol, so a substring match on the source is a
# reliable guard (and is robust to formatting).
RUST_FORBIDDEN_TOKENS = (
    "WriteProcessMemory",
    "VirtualAllocEx",
    "VirtualProtectEx",
    "PROCESS_VM_WRITE",
    "PROCESS_VM_OPERATION",
)


@pytest.mark.parametrize("token", RUST_FORBIDDEN_TOKENS)
def test_no_write_primitive_in_native(token):
    if not NATIVE_SRC.is_dir():  # pragma: no cover - native sources not present
        pytest.skip("native/src not present in this checkout")
    offenders = [str(p.relative_to(NATIVE_SRC)) for p in NATIVE_SRC.rglob("*.rs")
                 if token in p.read_text(encoding="utf-8", errors="replace")]
    assert not offenders, (
        f"forbidden Win32 write API {token!r} found in native source: {offenders}. "
        "The Rust reader opens the process read-only and must expose no write path.")


# --- (f) layering guard: the data/logic layers import no GUI toolkit -------
# core/, data/ and geo/ are the headless, unit-testable layers. They
# must stay importable with no Qt installed, so a Qt import at MODULE LEVEL in
# any of them is a boundary violation. (data/icons.py is UI-adjacent and may
# render pixmaps, but it lazy-imports Qt *inside functions* so the module still
# imports headless — that in-function import is the sanctioned exception, which
# this module-level check allows.)
HEADLESS_LAYERS = ("core", "data", "geo")
_QT_PKGS = ("PySide6", "PyQt5", "PyQt6", "PySide2")


def _module_level_imports(tree: ast.Module):
    """Yield Import/ImportFrom nodes that run at import time — i.e. at module
    scope, including inside top-level if/try/with — but NOT those nested inside a
    function or class body (a deferred/lazy import)."""
    def walk(body):
        for node in body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                yield node
            elif isinstance(node, (ast.If, ast.Try, ast.With)):
                yield from walk(node.body)
                yield from walk(getattr(node, "orelse", []))
                yield from walk(getattr(node, "finalbody", []))
                for handler in getattr(node, "handlers", []):
                    yield from walk(handler.body)
            # deliberately do not descend into FunctionDef/AsyncFunctionDef/ClassDef
    yield from walk(tree.body)


def _layer_py_files():
    for layer in HEADLESS_LAYERS:
        yield from (PKG_DIR / layer).rglob("*.py")


def test_headless_layers_have_no_module_level_qt_import():
    offenders = []
    for path in _layer_py_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in _module_level_imports(tree):
            mods = ([a.name for a in node.names] if isinstance(node, ast.Import)
                    else [node.module or ""])
            if any(m.split(".")[0] in _QT_PKGS for m in mods):
                offenders.append(f"{path.relative_to(PKG_DIR)}:{node.lineno}")
    assert not offenders, (
        f"GUI toolkit imported at module level in a headless layer: {offenders}. "
        "core/data/geo must import without Qt; defer any Qt use into a "
        "function (see data/icons.py) or move it to ui/.")
