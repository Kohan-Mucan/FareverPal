"""Shared scaffolding for the menu tests: a real LiveModel over fake memory.

`ui.GameUI` and its open-window array are laid out exactly as the HL runtime
does (FakeProc + HeapBuilder), so the tests exercise the REAL
`LiveModel.menu_state` / `menu_windows` reads - no live game, and nothing
stubbed except the memory itself. Used by test_menu_gate.py (the gate) and
test_menu_behaviour_matrix.py (which overlays hide / whether the cursor moves).

The offsets are stand-ins for the real (drifting) layout. The gate resolves them
by field name; they only need the same shape - first field at HL_WSIZE (8),
strictly ascending - for `Hl.field_offset` to resolve them.
"""
from __future__ import annotations

from collections.abc import Iterable
from types import SimpleNamespace

from farever_companion import constants as C
from farever_companion.core.hl import Hl
from farever_companion.core.model import LiveModel
from tests.fakemem import FakeProc, HeapBuilder

OLD_WINDOWS = 0x90      # windows before the 2026-09-11 patch (now additionalTips)
NEW_WINDOWS = 0x98      # windows after it

PLAYER = 0xDEADBEEF


class App:
    """Stand-in for the `locator.app` reader: just the UI pointer."""

    def __init__(self, ui: int | None):
        self._ui = ui

    def ui(self) -> int | None:
        return self._ui


def menu_model(proc: FakeProc, ui_addr: int | None,
               player: int | None = PLAYER,
               in_dungeon: bool = False, in_rift: bool = False) -> LiveModel:
    """A LiveModel wired just enough for the menu gate (no live game, no scan).

    `instance_state` is stubbed because the real one is the LIVE tri-state scene
    read (it prefers that over the boolean `is_in_dungeon`/`is_in_rift` pair), so
    a test that wants to be inside an instance has to answer it directly.
    """
    m = LiveModel.__new__(LiveModel)
    m.hl = Hl(proc)
    m.locator = SimpleNamespace(app=App(ui_addr), live_address=lambda: player)
    m.instance_state = lambda: (in_dungeon, in_rift)
    return m


def shifted_fields() -> dict[str, int]:
    """GameUI as the 2026-09-11 build lays it out: additionalTips 0x90, windows 0x98."""
    return {"s2d": 0x08, "additionalTips": OLD_WINDOWS, "windows": NEW_WINDOWS}


def ui_with_windows(fields: dict[str, int] | None, windows: Iterable[str], *,
                    at: int | None = None,
                    and_at: tuple[int, Iterable[str]] | None = None):
    """A ui.GameUI instance holding an array of open `windows`.

    `fields` is the declared layout (name -> byte offset); None means the type has
    no usable runtime layout, so field_offset bails and the constant is used.
    `at` overrides the slot the window array is written to (defaults to the
    layout's `windows` offset, else OFF_UI_WINDOWS). `and_at` plants a second
    array at an explicit slot - the decoy the old hardcoded reader would find.
    """
    proc = FakeProc()
    hb = HeapBuilder(proc)
    base = hb.make_type("ui.win.BaseWindow")
    for cls in ("ui.win.InventoryUI", "ui.win.EscapeMenu"):
        hb.make_type(cls, super_type=base)
    hb.make_type("ui.GameUI", fields=fields)
    ui = hb.make_instance("ui.GameUI")

    def arr(classes: Iterable[str]) -> int:
        return hb.make_array([hb.make_instance(c) for c in classes])

    slot = at if at is not None else (fields["windows"] if fields else C.OFF_UI_WINDOWS)
    proc.put_u64(ui + slot, arr(windows))
    if and_at is not None:
        proc.put_u64(ui + and_at[0], arr(and_at[1]))
    return proc, ui


def ui_without_a_window_list():
    """A ui.GameUI whose windows slot was never populated - the stale/freed-
    object case: every read succeeds, but there is no array there."""
    proc = FakeProc()
    hb = HeapBuilder(proc)
    hb.make_type("ui.win.BaseWindow")
    hb.make_type("ui.GameUI", fields=shifted_fields())
    ui = hb.make_instance("ui.GameUI")
    return proc, ui
