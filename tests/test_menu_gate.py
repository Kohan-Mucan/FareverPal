"""Regression tests for the overlay menu gate (LiveModel.menu_windows).

The 2026-09-11 patch inserted `stutters` into ui.GameUI, which pushed
`additionalTips` 0x88 -> 0x90 and `windows` 0x90 -> 0x98 (verified from
hlboot.dat: the Aug-4 build has windows @0x90, the Sep-11 build @0x98).

The gate used to read a hardcoded +0x90, which after the patch holds
`additionalTips` - an array that is normally empty - so no menu ever registered
and every overlay stayed visible over open menus. These tests pin both halves of
the fix: the offset is resolved by field name from the type's runtime layout
(which survives the next shift too), and OFF_UI_WINDOWS is the fallback when
reflection is unavailable.

Nothing here touches a live game: a FakeProc + HeapBuilder lay out ui.GameUI and
its window array exactly as the HL runtime does (see tests/menu_fakes.py).

The last section is the *consumer* of this gate - the mouse recentre rule, which
shares the gate's tri-state and must never move the cursor on an answer it could
not read. It lives here because the two are one contract: a gate that reports
"unknown" is only safe if nothing downstream treats unknown as "no menu"."""

from __future__ import annotations

import pytest

from farever_companion import constants as C
from farever_companion.core.model import MAX_UI_WINDOWS
from tests.fakemem import FakeProc, HeapBuilder
from tests.menu_fakes import (NEW_WINDOWS as _NEW_WINDOWS,
                              OLD_WINDOWS as _OLD_WINDOWS,
                              menu_model as _model,
                              shifted_fields as _shifted_fields,
                              ui_with_windows as _ui,
                              ui_without_a_window_list)


def test_menu_gate_follows_windows_when_it_shifts():
    """The regression: a menu is open, but 0x90 (where the reader used to look)
    now holds the tips array. Resolving `windows` by name must still see it."""
    proc, ui = _ui(_shifted_fields(), ("ui.win.InventoryUI",),
                   and_at=(_OLD_WINDOWS, ()))
    m = _model(proc, ui)
    assert m.hl.field_offset(m.hl.ptr(ui), "windows") == _NEW_WINDOWS
    assert m.menu_windows() == (True, True)


def test_menu_gate_reads_the_named_slot_not_the_constant():
    """A real window parked at the pre-patch 0x90 slot - with `windows` itself
    empty - reads as NO menu. An offset-blind reader would say True here."""
    proc, ui = _ui(_shifted_fields(), (), and_at=(_OLD_WINDOWS, ("ui.win.EscapeMenu",)))
    m = _model(proc, ui)
    assert m.hl.ptr(ui + _NEW_WINDOWS) != 0          # an (empty) windows array
    assert m.menu_state() is False


def test_menu_gate_falls_back_to_the_constant_when_layout_is_unreadable():
    """No runtime layout table -> field_offset returns None -> OFF_UI_WINDOWS."""
    assert C.OFF_UI_WINDOWS == _NEW_WINDOWS
    proc, ui = _ui(None, ("ui.win.InventoryUI",))
    m = _model(proc, ui)
    assert m.hl.field_offset(m.hl.ptr(ui), "windows") is None
    assert m.menu_state() is True
    # ...and it is the fallback that found it: nothing sits at the old slot.
    assert not m.hl.ptr(ui + _OLD_WINDOWS)


def test_empty_windows_is_no_menu():
    """A populated tips array must not be mistaken for an open menu."""
    proc, ui = _ui(_shifted_fields(), (), and_at=(_OLD_WINDOWS, ("ui.win.EscapeMenu",)))
    assert _model(proc, ui).menu_state() is False


def test_escape_menu_honours_include_escape():
    proc, ui = _ui(_shifted_fields(), ("ui.win.EscapeMenu",))
    m = _model(proc, ui)
    assert m.menu_state(include_escape=True) is True
    assert m.menu_state(include_escape=False) is False

    proc, ui = _ui(_shifted_fields(), ("ui.win.EscapeMenu", "ui.win.InventoryUI"))
    m = _model(proc, ui)
    assert m.menu_state(include_escape=False) is True


def test_attached_but_unlocated_player_reads_as_menu_open():
    """Overlays hide while the player isn't located yet - never the other way."""
    proc, ui = _ui(_shifted_fields(), ())
    assert _model(proc, ui, player=None).menu_state() is True


def test_no_ui_object_is_unknown():
    """No UI pointer means the gate can't be evaluated - not "no menu"."""
    proc, _addr = _ui(_shifted_fields(), ())
    assert _model(proc, None).menu_windows() is None


# --- menu_state: the tri-state the whole UI reads -----------------------------
def test_menu_state_reports_open_and_clear():
    proc, ui = _ui(_shifted_fields(), ("ui.win.InventoryUI",))
    assert _model(proc, ui).menu_state() is True

    proc, ui = _ui(_shifted_fields(), ())
    m = _model(proc, ui)
    assert m.menu_state() is False
    assert m.menu_windows() == (False, False)


def test_menu_state_is_unknown_without_a_ui_pointer():
    proc, _addr = _ui(_shifted_fields(), ())
    assert _model(proc, None).menu_state() is None


def test_menu_state_is_unknown_when_the_ui_pointer_is_stale():
    """The pointer resolves but nothing is mapped there any more (freed
    GameUI): the read raises, so the gate must not claim the screen is clear."""
    proc, _addr = _ui(_shifted_fields(), ())
    assert _model(proc, 0x7F000000).menu_state() is None


def test_menu_state_is_unknown_when_the_window_list_is_missing():
    """A live GameUI always owns its windows array; a readable-but-null slot
    means we followed a stale pointer, not that no menu is open."""
    proc, ui = ui_without_a_window_list()
    m = _model(proc, ui)
    assert not m.hl.ptr(ui + _NEW_WINDOWS)      # the read succeeds, value is null
    assert m.menu_state() is None


def test_menu_state_is_unknown_on_an_implausible_window_count():
    """Garbage in the length field must not be walked (or trusted)."""
    proc, ui = ui_without_a_window_list()
    hb = HeapBuilder(proc, base=0x400000)        # second arena, same FakeProc
    junk = hb.alloc(0x20)
    proc.put_i32(junk + 8, 0x40000000)           # absurd ArrayObj length
    proc.put_u64(ui + _NEW_WINDOWS, junk)
    assert _model(proc, ui).menu_state() is None


# --- menu_windows: both answers from a single walk ---------------------------
@pytest.mark.parametrize("windows,expected", [
    ((), (False, False)),
    (("ui.win.EscapeMenu",), (True, False)),
    (("ui.win.InventoryUI",), (True, True)),
    (("ui.win.InventoryUI", "ui.win.EscapeMenu"), (True, True)),
])
def test_menu_windows_answers_both_rules(windows, expected):
    """One walk classifies the whole window list both ways: Rule A counts the
    escape menu, Rule B does not."""
    proc, ui = _ui(_shifted_fields(), windows)
    assert _model(proc, ui).menu_windows() == expected


@pytest.mark.parametrize("windows", [(), ("ui.win.EscapeMenu",),
                                     ("ui.win.InventoryUI",),
                                     ("ui.win.EscapeMenu", "ui.win.InventoryUI")])
def test_the_two_answers_always_agree(windows):
    """`menu_state` is a wrapper over `menu_windows`, so the escape-menu rule
    and the gameplay rule can never describe two different window lists - and a
    gameplay window can never be reported with no window open."""
    m = _model(*_ui(_shifted_fields(), windows))
    pair = m.menu_windows()
    assert pair is not None
    assert m.menu_state(include_escape=True) is pair[0]
    assert m.menu_state(include_escape=False) is pair[1]
    assert not pair[1] or pair[0]


def test_menu_windows_is_unknown_when_the_walk_fails():
    proc, _addr = _ui(_shifted_fields(), ())
    for ui_addr in (None, 0x7F000000):
        assert _model(proc, ui_addr).menu_windows() is None
    assert _model(*ui_without_a_window_list()).menu_windows() is None


def test_menu_windows_counts_the_character_screen_as_a_menu():
    proc, ui = _ui(_shifted_fields(), ())
    assert _model(proc, ui, player=None).menu_windows() == (True, True)


def test_menu_state_does_not_walk_the_list_itself():
    """The single-walk contract lives in `menu_windows` alone: one question,
    one walk - so a caller that wants both answers asks once."""
    proc, ui = _ui(_shifted_fields(), ("ui.win.InventoryUI",))
    m = _model(proc, ui)
    walks: list[int] = []
    real = m.menu_windows
    m.menu_windows = lambda: (walks.append(1), real())[1]
    assert m.menu_state() is True
    assert len(walks) == 1


def test_every_unprovable_read_is_unknown():
    """None is the gate's answer whenever it can't back a claim - and it has to
    be None rather than False, because the mirror-image mistake (calling an
    unreadable screen "a menu") is just as wrong. The overlay-side handling of
    that None lives in core/game_state's TickReads (the manager's snapshot owner);
    tests/test_unreadable_state
    and test_menu_behaviour_matrix pin that the HUD holds instead of blinking."""
    proc, _addr = _ui(_shifted_fields(), ())
    for ui_addr in (None, 0x7F000000, *ui_without_a_window_list()[1:2]):
        assert _model(proc, ui_addr).menu_windows() is None
        assert _model(proc, ui_addr).menu_state() is None


# --- malformed memory: every case must be 'unknown', never a confident answer --
# The gate is the one read whose wrong answer is user-visible in both directions
# (a menu missed = HUD drawn over it; a screen wrongly called clear = the mouse
# recentred inside a window), so when the memory doesn't add up it must say so
# rather than pick the more likely-sounding answer.
UNMAPPED = 0x7F000000       # pointer-shaped, nothing mapped there


def _ui_holding(ptrs):
    """A ui.GameUI whose `windows` array holds exactly these raw pointers."""
    proc = FakeProc()
    hb = HeapBuilder(proc)
    hb.make_type("ui.win.BaseWindow")
    hb.make_type("ui.GameUI", fields=_shifted_fields())
    ui = hb.make_instance("ui.GameUI")
    proc.put_u64(ui + _NEW_WINDOWS, hb.make_array(ptrs))
    return proc, ui, hb


def _unknown(m):
    """The whole gate must be unknown on malformed memory - both answers of it,
    in both escape variants. That None is what the overlay-side hold and the
    mouse gate consume (tests/test_unreadable_state.py,
    tests/test_menu_behaviour_matrix.py), so a malformed read can neither blink
    the HUD out nor leave it drawn over a menu."""
    assert m.menu_windows() is None
    assert m.menu_state() is None
    assert m.menu_state(include_escape=False) is None


def test_a_window_list_pointer_into_unmapped_pages_is_unknown():
    proc = FakeProc()
    hb = HeapBuilder(proc)
    hb.make_type("ui.win.BaseWindow")
    hb.make_type("ui.GameUI", fields=_shifted_fields())
    ui = hb.make_instance("ui.GameUI")
    proc.put_u64(ui + _NEW_WINDOWS, UNMAPPED)     # the slot points nowhere
    _unknown(_model(proc, ui))


def test_a_backing_store_that_isnt_mapped_is_unknown():
    """The array header reads fine, but its element storage isn't there - so
    every slot reads as "empty" and the walk would say 'nothing is open'."""
    proc, ui, hb = _ui_holding([])
    arr = _model(proc, ui).hl.ptr(ui + _NEW_WINDOWS)
    proc.put_i32(arr + 8, 3)                      # ...claiming three windows
    proc.put_u64(arr + 0x10, UNMAPPED)            # ...with nowhere to read them
    _unknown(_model(proc, ui))


def test_a_null_backing_store_pointer_is_unknown():
    proc, ui, hb = _ui_holding([])
    arr = _model(proc, ui).hl.ptr(ui + _NEW_WINDOWS)
    proc.put_i32(arr + 8, 2)
    proc.put_u64(arr + 0x10, 0)
    _unknown(_model(proc, ui))


def test_a_freed_zeroed_window_list_is_unknown():
    """Pages zeroed on free: the slot still reads as a pointer and every byte
    at the far end reads as 0 - i.e. exactly like a live, empty list. The
    missing hl_type at +0 is the only thing that gives it away."""
    proc, ui, hb = _ui_holding([])
    dead = hb.alloc(0x20)                         # zeroed: no type, length 0
    proc.put_u64(ui + _NEW_WINDOWS, dead)
    m = _model(proc, ui)
    assert m.hl.ptr(ui + _NEW_WINDOWS) == dead    # the pointer itself is fine
    assert m.hl.i32(dead + 8) == 0                # ...and reads as an empty list
    _unknown(m)


def test_a_freed_zeroed_gameui_object_is_unknown():
    """The whole UI object freed and zeroed: the offset walk falls back to the
    constant, finds a null, and reports unknown."""
    proc = FakeProc()
    hb = HeapBuilder(proc)
    dead = hb.alloc(0x200)                        # no hl_type, no windows slot
    _unknown(_model(proc, dead))


@pytest.mark.parametrize("length", [0x40000000, 0x7FFFFFFF, -1, MAX_UI_WINDOWS + 1])
def test_a_garbage_window_count_is_unknown(length):
    proc, ui = _ui(_shifted_fields(), ("ui.win.InventoryUI",))
    arr = _model(proc, ui).hl.ptr(ui + _NEW_WINDOWS)
    proc.put_i32(arr + 8, length)
    _unknown(_model(proc, ui))


def test_the_count_bound_is_inclusive_not_a_wall():
    """A list at exactly MAX_UI_WINDOWS is walked (and a window in the last slot
    still counts); one past it is refused rather than walked."""
    proc = FakeProc()
    hb = HeapBuilder(proc)
    hb.make_type("ui.win.BaseWindow")
    hb.make_type("ui.win.InventoryUI", super_type=hb.types["ui.win.BaseWindow"])
    window = hb.make_instance("ui.win.InventoryUI")
    hb.make_type("ui.GameUI", fields=_shifted_fields())
    ui = hb.make_instance("ui.GameUI")
    proc.put_u64(ui + _NEW_WINDOWS,
                 hb.make_array([0] * (MAX_UI_WINDOWS - 1) + [window]))
    m = _model(proc, ui)
    assert m.menu_windows() == (True, True)

    arr = m.hl.ptr(ui + _NEW_WINDOWS)
    proc.put_i32(arr + 8, MAX_UI_WINDOWS + 1)
    _unknown(m)


def test_a_non_window_in_the_list_is_unknown():
    """`ui.GameUI.windows` holds windows; a live object of another class means
    we followed the wrong array (the 0x90-style drift) or the memory is junk."""
    proc = FakeProc()
    hb = HeapBuilder(proc)
    hb.make_type("ui.win.BaseWindow")
    hb.make_type("ui.GameUI", fields=_shifted_fields())
    ui = hb.make_instance("ui.GameUI")
    other = hb.make_string("not a window")
    proc.put_u64(ui + _NEW_WINDOWS, hb.make_array([other]))
    m = _model(proc, ui)
    assert m.hl.class_of(other) == "String"      # it IS a live, named object
    _unknown(m)


def test_a_null_or_unreadable_slot_is_unknown():
    """A hole (or a pointer into unmapped pages) is not a window, so a list of
    nothing but holes can't support 'no menu' either."""
    proc, ui, _hb = _ui_holding([0, UNMAPPED, 0])
    _unknown(_model(proc, ui))


def test_positive_evidence_still_wins_over_junk():
    """Junk entries must not turn a real menu into 'unknown': the walk keeps
    looking, and one window settles both answers."""
    proc = FakeProc()
    hb = HeapBuilder(proc)
    base = hb.make_type("ui.win.BaseWindow")
    hb.make_type("ui.win.InventoryUI", super_type=base)
    hb.make_type("ui.GameUI", fields=_shifted_fields())
    ui = hb.make_instance("ui.GameUI")
    junk = hb.make_string("junk")
    proc.put_u64(ui + _NEW_WINDOWS,
                 hb.make_array([0, UNMAPPED, junk, hb.make_instance("ui.win.InventoryUI")]))
    m = _model(proc, ui)
    assert m.menu_windows() == (True, True)
    assert m.menu_state() is True


def test_an_escape_menu_after_junk_is_still_escape_only():
    proc = FakeProc()
    hb = HeapBuilder(proc)
    base = hb.make_type("ui.win.BaseWindow")
    hb.make_type("ui.win.EscapeMenu", super_type=base)
    hb.make_type("ui.GameUI", fields=_shifted_fields())
    ui = hb.make_instance("ui.GameUI")
    proc.put_u64(ui + _NEW_WINDOWS,
                 hb.make_array([UNMAPPED, hb.make_instance("ui.win.EscapeMenu")]))
    assert _model(proc, ui).menu_windows() == (True, False)


# ==========================================================================
# the cursor rule that consumes the gate (was test_mouse_centering_gate.py)
# ==========================================================================

from types import SimpleNamespace


from farever_companion.ui.game_window import (_clip_confines, _cursor_locked,
                                             _menu_gates, _menu_state,
                                             _should_center_mouse)


# --- the decision ------------------------------------------------------------
def test_clear_screen_with_a_game_held_cursor_centers():
    assert _should_center_mouse(True, True, True, False, True) is True


@pytest.mark.parametrize("menu_state", [True, None])
def test_open_menu_and_unknown_state_both_leave_the_cursor_alone(menu_state):
    """A menu is obvious; None is the fail-safe half - a stale UI pointer or a
    failed read (zone swap, game relaunching) must behave like a menu."""
    assert _should_center_mouse(True, True, True, menu_state, True) is False


@pytest.mark.parametrize("cursor_locked", [False, None])
def test_a_released_or_unreadable_cursor_leaves_it_alone(cursor_locked):
    """The game's own state gates the recentre: it only runs while the game
    holds the cursor, and an unreadable cursor is hands off - never assumed."""
    assert _should_center_mouse(True, True, True, False, cursor_locked) is False


@pytest.mark.parametrize("args", [
    (False, True, True, False, True),      # feature off
    (True, False, True, False, True),      # game not the foreground window
    (True, True, False, False, True),      # player not located yet
])
def test_every_other_precondition_still_blocks(args):
    assert _should_center_mouse(*args) is False


def test_unknown_beats_a_later_true():
    """Order can't accidentally let an unknown state through: only an explicit
    False menu state AND an explicit True cursor lock ever center."""
    assert _should_center_mouse(True, True, True, None, True) is False
    assert _should_center_mouse(True, True, True, False, None) is False
    assert _should_center_mouse(False, False, False, None, None) is False


# --- the cursor-lock read (its two pure halves) ------------------------------
@pytest.mark.parametrize("visible,expected", [(False, True), (True, False)])
def test_cursor_visibility_decides_both_ways(visible, expected):
    """The game hides the cursor while the camera follows the mouse and draws it
    again the moment it frees it, so visibility is authoritative in BOTH
    directions - even when the clip read is unavailable or misleading."""
    assert _cursor_locked(visible, None) is expected
    assert _cursor_locked(visible, True) is expected
    assert _cursor_locked(visible, False) is expected


def test_a_visible_cursor_beats_a_confined_clip_rect():
    """Regression guard: on a multi-monitor desktop an UNCLIPPED cursor can
    report the primary screen as its clip rect, which would fake a lock. A menu
    that shows the cursor must read as released whatever the clip rect says."""
    assert _cursor_locked(True, True) is False


def test_clipping_is_only_the_fallback():
    assert _cursor_locked(None, True) is True
    assert _cursor_locked(None, False) is False
    assert _cursor_locked(None, None) is None      # nothing to go on


@pytest.mark.parametrize("clip,desktop,expected", [
    (None, (0, 0, 1920, 1080), False),                     # no clip read
    ((0, 0, 1920, 1080), None, False),                     # no desktop read
    ((0, 0, 1920, 1080), (0, 0, 1920, 1080), False),       # unclipped
    ((0, 0, 3840, 1080), (0, 0, 3840, 1080), False),       # unclipped, wide
    ((100, 50, 1820, 1030), (0, 0, 1920, 1080), True),     # client-area lock
    ((0, 0, 1920, 1080), (0, 0, 3840, 1080), True),        # locked to a screen
])
def test_clip_confines_compares_against_the_whole_desktop(clip, desktop, expected):
    assert _clip_confines(clip, desktop) is expected


# --- the gate reader: both answers from one walk -----------------------------
def test_menu_gates_reads_both_answers_from_the_models_single_walk():
    class _Walk:
        def __init__(self, pair):
            self.pair, self.calls = pair, 0

        def menu_windows(self):
            self.calls += 1
            return self.pair

    m = _Walk((True, False))
    assert _menu_gates(m) == (True, False)
    assert m.calls == 1                     # one walk, both rules

    m = _Walk(None)
    assert _menu_gates(m) == (None, None)   # a failed walk is never "clear"


def test_menu_gates_normalises_to_bools():
    class _Walk:
        def menu_windows(self):
            return (1, 0)

    assert _menu_gates(_Walk()) == (True, False)


def test_menu_gates_survives_a_raising_walk():
    class _Walk:
        def menu_windows(self):
            raise RuntimeError("stale read")

    assert _menu_gates(_Walk()) == (None, None)


def test_menu_gates_falls_back_to_two_reads_without_the_walk():
    """A model that can only answer per-variant is asked twice - and a model
    that answers neither, or raises, is unknown rather than clear."""
    assert _menu_gates(_Model(state=True)) == (True, True)
    assert _menu_gates(_Model(state=False)) == (False, False)
    assert _menu_gates(_Model(raises=True)) == (None, None)
    assert _menu_gates(object()) == (None, None)   # no gate at all


# --- the tri-state reader ----------------------------------------------------
class _Model:
    """A model with the tri-state gate but no single-walk `menu_windows`."""

    def __init__(self, state=None, raises=False):
        self._state, self._raises = state, raises

    def menu_state(self, include_escape: bool = True):
        if self._raises:
            raise RuntimeError("stale read")
        return self._state


@pytest.mark.parametrize("state,expected", [(True, True), (False, False), (None, None)])
def test_menu_state_is_passed_through(state, expected):
    assert _menu_state(_Model(state=state)) is expected


@pytest.mark.parametrize("state,expected", [(1, True), (0, False)])
def test_menu_state_is_normalised_to_bool(state, expected):
    """Strict `is False` comparisons downstream need real bools."""
    assert _menu_state(_Model(state=state)) is expected


def test_a_raising_tri_state_is_unknown_not_clear():
    assert _menu_state(_Model(raises=True)) is None


def test_a_model_without_the_gate_is_unknown_not_clear():
    """There is no boolean alias to fall back to any more, so a model that
    can't answer the tri-state must not be read as "no menu"."""
    assert _menu_state(object()) is None
    assert _menu_state(SimpleNamespace()) is None


class _Raises:
    """A gate whose answer itself can't be trusted to be a bool."""

    def __bool__(self):
        raise RuntimeError("stale read")


def test_a_gate_answer_that_cannot_be_coerced_is_unknown():
    assert _menu_state(_Model(state=_Raises())) is None
