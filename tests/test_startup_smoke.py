"""Startup import smoke test (headless, no game, no catalog data needed).

A broken import in the entry chain only ever surfaced as the GUI "Startup
Error" dialog at launch: `format_item_stats_summary` and several siblings
disappeared from `data/items/stats.py` while `data/items/__init__.py` kept
re-exporting them, so `run.py -> app -> control_panel -> combat_page ->
data.items` raised ImportError before any UI existed. These tests reproduce
that chain and every re-export the items package promises, so the failure
lands in the test suite instead of on the user's first launch.

They import modules only — no window is shown and no event loop runs — so
they stay fast and safe to run everywhere.

Driven by `Check_Startup.bat`, which runs exactly this module before a launch.
"""
import os

# Qt needs a platform plugin before PySide6 is imported anywhere in the chain
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import importlib  # noqa: E402

import pytest  # noqa: E402


def test_entry_point_imports_the_app_main():
    """run.py's launch import: `from farever_companion.app import main`."""
    pytest.importorskip("PySide6")          # the UI chain needs Qt
    from farever_companion.app import main
    assert callable(main)


def test_app_to_control_panel_to_combat_page_chain_imports():
    """The traceback's hops — app -> ui.control_panel -> pages.combat_page —
    must each import cleanly (the last one is what pulls in data.items)."""
    pytest.importorskip("PySide6")
    control_panel = importlib.import_module("farever_companion.ui.control_panel")
    assert hasattr(control_panel, "ControlPanel")
    combat_page = importlib.import_module(
        "farever_companion.ui.pages.combat_page")
    assert hasattr(combat_page, "CombatPageMixin")


def test_items_package_imports_and_every_export_resolves():
    """Importing the package runs its `from .X import (...)` statements, and
    every name it advertises in `__all__` must actually exist on the package
    — the exact contract the startup crash violated."""
    idata = importlib.import_module("farever_companion.data.items")
    exported = getattr(idata, "__all__", None)
    assert exported, "items package should declare __all__"
    missing = [name for name in exported if not hasattr(idata, name)]
    assert not missing, f"items re-exports that no longer resolve: {missing}"
