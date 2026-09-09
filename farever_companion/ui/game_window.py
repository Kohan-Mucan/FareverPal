"""Win32 probes + the gates that decide overlay hiding and mouse recentering.

Everything here answers one of two questions, and both must agree:

* is the game's main window the one in focus (`_foreground_state`,
  `_is_valid_game_window`)?  - and is that window the game itself, not a crash
  dialog or a modal popup;
* is the gameplay screen covered (`_menu_gates`)?  - ONE walk of the model's
  window list answering both rules (escape menu / any gameplay window) as
  tri-states, so callers can tell "proven clear" apart from "couldn't read",
  and the two rules can never describe two different window lists;
* is the game holding the cursor for mouse-look (`_cursor_lock_state`,
  `_cursor_visible`, `_clip_confines`)?  - the game's own answer to "am I in
  gameplay", used to gate the mouse recentre (`_should_center_mouse`) and the
  mouse-park click-through.

POLICY - a read that FAILS is not evidence about the world. Every gate here
answers True / False / None, and every consumer must act only on a definite
value: an unreadable state may never *change* what the HUD is doing, because
the wrong guess is user-visible (a recentre fights the mouse, a released
click-through lock hands the player's clicks to an overlay, a hidden-
on-unknown menu makes the HUD flash over a window). Where a caller needs an
answer every tick, it holds the last PROVEN value (core.game_state.Held, applied
by TickReads) and only acts when this tick's read agrees or upgrades it. The
deliberate exception is
hiding on focus loss: that path may fail closed, because a hidable overlay
self-heals on the next tick whereas a wrong recentre or a stolen click does not.

Kept out of OverlayManager (a Qt manager that was already at the ui/ line
budget) and free of Qt, so it stays testable without a widget tree: the pure
halves (`_clip_confines`, `_cursor_locked`, `_should_center_mouse`,
`_click_through_decision`) take their inputs as arguments, and the Win32 readers
degrade to None instead of raising.
"""
from __future__ import annotations


def _is_valid_game_window(active_hwnd: int, game_pid: int) -> bool:
    """Returns True ONLY if active_hwnd is the main game rendering window."""
    if not active_hwnd or not game_pid:
        return False
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32

    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(active_hwnd, ctypes.byref(pid))
    if pid.value != game_pid:
        return False

    # GW_OWNER = 4: modal dialogs/popups have an owner window set; main window does not
    if user32.GetWindow(active_hwnd, 4):
        return False

    buf = ctypes.create_unicode_buffer(512)
    user32.GetClassNameW(active_hwnd, buf, 256)
    cls_name = buf.value.lower()
    if cls_name == "#32770" or "dialog" in cls_name or "crash" in cls_name:
        return False

    user32.GetWindowTextW(active_hwnd, buf, 512)
    title = buf.value.lower()
    if any(k in title for k in ("crash", "error", "exception", "fatal", "assert", "dump", "unhandled", "fault", "stopped")):
        return False

    rect = wintypes.RECT()
    return not (user32.GetClientRect(active_hwnd, ctypes.byref(rect)) and (rect.right < 400 or rect.bottom < 300))


def _menu_gates(m) -> tuple[bool | None, bool | None]:
    """Both menu answers - `(any window, gameplay window)` - from ONE walk.

    `LiveModel.menu_windows()` classifies `ui.GameUI.windows` once and returns
    both flags, so the escape-menu rule (Rule A) and the auto-hide rule (Rule
    B) can never disagree about the same tick, and the tick pays for one walk
    of the window list instead of two. Either flag is None when the walk
    couldn't be evaluated - never False by default.

    Models without `menu_windows` (duck-typed tests) fall back to two
    `_menu_state` reads - still tri-state, so unknown stays unknown.
    """
    fn = getattr(m, "menu_windows", None)
    if callable(fn):
        try:
            pair = fn()
        except Exception:
            return None, None
        if pair is None:
            return None, None
        return bool(pair[0]), bool(pair[1])
    return (_menu_state(m, include_escape=True),
            _menu_state(m, include_escape=False))


def _menu_state(m, include_escape: bool = True) -> bool | None:
    """The model's menu gate as a tri-state (None = could not be evaluated).

    `LiveModel.menu_state` is the only menu API now - the boolean alias is
    gone, so there is nothing to fall back to that could dress an unreadable
    gate up as "clear". A model without it, or a read that raises, is unknown.
    """
    if not callable(getattr(m, "menu_state", None)):
        return None
    try:
        st = m.menu_state(include_escape=include_escape)
        return None if st is None else bool(st)
    except Exception:            # a raising read, or one that can't be coerced
        return None


def _cursor_visible() -> bool | None:
    """Whether Windows is drawing the cursor, None when the read fails.

    The game hides the cursor while the camera follows the mouse (mouse-look)
    and shows it again the moment it releases the cursor for a menu, alt-tab or
    a loading screen - so this is the game's own "I own the cursor" signal.
    """
    try:
        import ctypes
        from ctypes import wintypes

        class CURSORINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("flags", wintypes.DWORD),
                ("hCursor", wintypes.HCURSOR),
                ("ptScreenPos", wintypes.POINT),
            ]

        cinfo = CURSORINFO()
        cinfo.cbSize = ctypes.sizeof(CURSORINFO)
        if not ctypes.windll.user32.GetCursorInfo(ctypes.byref(cinfo)):
            return None
        return bool(cinfo.flags & 0x00000001)       # CURSOR_SHOWING
    except Exception:
        return None


def _clip_confines(clip, desktop) -> bool:
    """True when `clip` is strictly inside `desktop` - some app confined the
    cursor (a mouse-look lock). Rectangles are (left, top, right, bottom)."""
    if clip is None or desktop is None:
        return False
    return (clip[0] > desktop[0] or clip[1] > desktop[1]
            or clip[2] < desktop[2] or clip[3] < desktop[3])


def _cursor_locked(cursor_visible: bool | None,
                   clip_confined: bool | None) -> bool | None:
    """Whether the game is holding the cursor for mouse-look. Pure.

    A hidden cursor is authoritative in BOTH directions: the game draws it
    again the moment it frees it, so `True` (visible) is as conclusive as
    `False` (hidden). Clipping is only the fallback for a failed visibility
    read - an UNCLIPPED cursor can report the primary screen as its clip rect
    on a multi-monitor desktop, which would fake a lock.
    """
    if cursor_visible is not None:
        return not cursor_visible
    if clip_confined is None:
        return None
    return clip_confined


def _cursor_lock_state(hwnd: int) -> bool | None:
    """Whether the foreground game window (`hwnd`) currently owns the cursor
    for mouse-look: True = locked (gameplay), False = released (menu, alt-tab,
    loading), None = nothing to judge / the reads failed."""
    if not hwnd:
        return None
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        clip = wintypes.RECT()
        confined = None
        if user32.GetClipCursor(ctypes.byref(clip)):
            vx = user32.GetSystemMetrics(76)        # SM_XVIRTUALSCREEN
            vy = user32.GetSystemMetrics(77)        # SM_YVIRTUALSCREEN
            vw = user32.GetSystemMetrics(78)        # SM_CXVIRTUALSCREEN
            vh = user32.GetSystemMetrics(79)        # SM_CYVIRTUALSCREEN
            confined = _clip_confines(
                (clip.left, clip.top, clip.right, clip.bottom),
                (vx, vy, vx + vw, vy + vh))
        return _cursor_locked(_cursor_visible(), confined)
    except Exception:
        return None


def _recenter_cursor(hwnd: int) -> bool:
    """Park the cursor at the centre of the game's client area (the mouse-look
    recentre). False when it couldn't be read/moved; the caller has already
    decided this is allowed."""
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        rect = wintypes.RECT()
        if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
            return False
        pt = wintypes.POINT((rect.left + rect.right) // 2,
                            (rect.top + rect.bottom) // 2)
        user32.ClientToScreen(hwnd, ctypes.byref(pt))
        return bool(user32.SetCursorPos(pt.x, pt.y))
    except Exception:
        return False


def _foreground_state(game_pid: int) -> tuple[int, bool, bool]:
    """(foreground hwnd, is the game's main window focused, is our app focused).

    `game_pid` 0 means the game isn't attached, so it can't be the foreground
    window whatever the OS reports."""
    try:
        import ctypes
        import os

        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return 0, False, False
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        app_focused = (pid.value == os.getpid())
        game_focused = bool(game_pid) and _is_valid_game_window(hwnd, game_pid)
        return hwnd, game_focused, app_focused
    except Exception:
        return 0, False, False


def _click_through_decision(sources) -> bool | None:
    """Should the overlays be click-through right now? Tri-state, so the caller
    can tell "no" apart from "don't know".

    True  - some source has POSITIVE evidence the player is playing (in combat,
            or the game is holding the cursor for mouse-look): lock.
    False - every source answered and none of them says so: restore.
    None  - a source could not be read and nothing else is positive: leave the
            lock exactly as it is. A failed read must never *release* it - that
            is the one direction the player feels, because the overlay starts
            eating the clicks they meant for the game.

    `sources` holds only the checks that are switched on: a disabled feature is
    absent, not an unknown, or it would freeze the lock forever.
    """
    unknown = False
    for s in sources:
        if s is True:
            return True
        if s is None:
            unknown = True
    return None if unknown else False


def _should_center_mouse(enabled: bool, game_focused: bool, has_player: bool,
                         menu_state: bool | None,
                         cursor_locked: bool | None) -> bool:
    """Whether this tick may recenter the cursor on the game screen.

    Two independent gates must both come back POSITIVE:

    * ``menu_state is False`` - the model's window list was read and is empty
      (True is an open menu; None is 'couldn't read', e.g. a stale UI pointer,
      a failed read, a zone swap, the game relaunching).
    * ``cursor_locked is True`` - the game itself is holding the cursor for
      mouse-look (hidden, and usually clipped to the client area). That is the
      game's own answer to 'am I in gameplay', so a window list that misses a
      menu can't yank the cursor any more, and a menu that frees the cursor
      stops the recentre even if the window-list read lags.

    Anything unknown on either gate is hands off: a recentre we can't justify
    would fight the user's mouse exactly like a menu would (yanking the cursor
    to the middle while they click a menu button or load in). Pure, so the rule
    is testable without a live game.
    """
    return (bool(enabled) and bool(game_focused) and bool(has_player)
            and menu_state is False and cursor_locked is True)
