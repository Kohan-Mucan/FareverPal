"""System-wide hotkeys (Win32 RegisterHotKey).

The HUD overlays are frameless always-on-top windows that don't hold keyboard
focus while the game is in front, so in-overlay key events never arrive. Global
hotkeys (e.g. the Speedrun start/stop) work while the game is focused.
Read-only: this only listens for key combos; it injects nothing into the game.

`GlobalHotkeys.triggered(action)` fires for each bound combo. Bindings are
QKeySequence strings (e.g. "Ctrl+Alt+Right") edited via QKeySequenceEdit.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes

from PySide6 import QtCore, QtGui, QtWidgets

WM_HOTKEY = 0x0312
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000

# Qt keyboard-modifier bit values (stable across Qt6); avoids enum int() issues
_M_SHIFT, _M_CTRL, _M_ALT, _M_META = 0x02000000, 0x04000000, 0x08000000, 0x10000000
_MOD_MASK = _M_SHIFT | _M_CTRL | _M_ALT | _M_META


def _kv(name: str) -> int:
    k = getattr(QtCore.Qt, name)
    return int(k.value if hasattr(k, "value") else k)


# Qt special keys -> Win32 virtual-key codes (letters/digits map 1:1 already)
_QT2VK = {
    _kv("Key_Left"): 0x25, _kv("Key_Up"): 0x26, _kv("Key_Right"): 0x27,
    _kv("Key_Down"): 0x28, _kv("Key_PageUp"): 0x21, _kv("Key_PageDown"): 0x22,
    _kv("Key_Home"): 0x24, _kv("Key_End"): 0x23, _kv("Key_Insert"): 0x2D,
    _kv("Key_Delete"): 0x2E, _kv("Key_Space"): 0x20, _kv("Key_Return"): 0x0D,
    _kv("Key_Enter"): 0x0D, _kv("Key_Tab"): 0x09, _kv("Key_Escape"): 0x1B,
    _kv("Key_Backspace"): 0x08,
}
_QT2VK.update({_kv(f"Key_F{i}"): 0x70 + (i - 1) for i in range(1, 13)})


def _vk_for_char(qt_key: int) -> int:
    """Layout-aware fallback: map a printable Qt key that isn't a plain letter/
    digit/known special (ü, ä, ö, ß, and other OEM/layout keys) to its Win32
    virtual-key on the *current* keyboard layout via VkKeyScanW. Qt's enum value
    for a Latin-1 printable equals its Unicode codepoint, so chr() recovers the
    character. Returns 0 if it can't be mapped. Without this, non-US-layout keys
    bind to VK 0 and silently never fire."""
    if not hasattr(ctypes, "windll") or not (0x20 <= qt_key <= 0x10FFFF):
        return 0
    try:
        ch = chr(qt_key)
    except ValueError:
        return 0
    fn = ctypes.windll.user32.VkKeyScanW
    fn.restype = ctypes.c_short          # SHORT: -1 means "no key for this char"
    fn.argtypes = [ctypes.c_wchar]
    res = fn(ch)
    if res == -1:
        return 0
    return res & 0xFF                    # low byte = VK (high byte = shift state)


def parse_sequence(seq_str: str) -> tuple[int, int]:
    """QKeySequence string -> (win32 modifiers, virtual-key). (0,0) if empty/bad.
    Letters/digits map 1:1 to their VK; known special keys via _QT2VK; anything
    else (layout-specific chars) via VkKeyScanW so non-US layouts work."""
    if not seq_str:
        return (0, 0)
    ks = QtGui.QKeySequence(seq_str)
    if ks.isEmpty():
        return (0, 0)
    combo = ks[0]
    if hasattr(combo, "key"):                 # Qt6: QKeyCombination
        kk, mm = combo.key(), combo.keyboardModifiers()
        key = int(kk.value if hasattr(kk, "value") else kk)
        qmods = int(mm.value if hasattr(mm, "value") else mm)
    else:                                      # fallback: packed int
        v = int(combo)
        key = v & ~_MOD_MASK
        qmods = v & _MOD_MASK
    vk = key if 0x30 <= key <= 0x5A else _QT2VK.get(key, 0)
    if not vk:
        vk = _vk_for_char(key)        # layout-aware (ü, ä, ö, ß, OEM keys, …)
    mods = 0
    if qmods & _M_CTRL:
        mods |= MOD_CONTROL
    if qmods & _M_ALT:
        mods |= MOD_ALT
    if qmods & _M_SHIFT:
        mods |= MOD_SHIFT
    if qmods & _M_META:
        mods |= MOD_WIN
    return (mods, vk)


class _HotkeyFilter(QtCore.QAbstractNativeEventFilter):
    """The native-event filter must be a *standalone* QAbstractNativeEventFilter:
    a QObject+QAbstractNativeEventFilter multiple-inheritance object is silently
    NOT invoked by Qt's installNativeEventFilter (PySide6). It forwards every
    WM_HOTKEY to its owner GlobalHotkeys."""
    def __init__(self, owner: "GlobalHotkeys"):
        super().__init__()
        self._owner = owner

    def nativeEventFilter(self, etype, message):
        try:
            if bytes(etype) in (b"windows_generic_MSG", b"windows_dispatcher_MSG"):
                msg = wintypes.MSG.from_address(int(message))
                if msg.message == WM_HOTKEY:
                    self._owner._dispatch(int(msg.wParam), int(msg.time))
        except Exception:
            pass
        return False, 0


class GlobalHotkeys(QtCore.QObject):
    triggered = QtCore.Signal(str)            # action id

    def __init__(self, app):
        super().__init__()
        self._u32 = ctypes.windll.user32 if hasattr(ctypes, "windll") else None
        self._ids: dict[int, str] = {}        # hotkey id -> action
        self._next = 1
        self._last = None                     # (wParam, time) of the last WM_HOTKEY
        self._host = None
        self._hwnd = None
        self._filter = None
        if self._u32 is not None:
            # Two Win32/Qt gotchas, both required for global hotkeys to fire:
            #  1. RegisterHotKey(NULL) posts WM_HOTKEY as a *thread* message Qt's
            #     native filter never sees - bind to a real (hidden) window HWND.
            #  2. The filter must be a standalone QAbstractNativeEventFilter
            #     (see _HotkeyFilter); installNativeEventFilter does NOT take
            #     ownership, so keep a reference.
            self._u32.RegisterHotKey.argtypes = [
                wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
            self._u32.RegisterHotKey.restype = wintypes.BOOL
            self._u32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
            self._u32.UnregisterHotKey.restype = wintypes.BOOL
            self._host = QtWidgets.QWidget()
            self._host.resize(0, 0)
            self._hwnd = wintypes.HWND(int(self._host.winId()))   # forces native HWND
            self._filter = _HotkeyFilter(self)
            app.installNativeEventFilter(self._filter)

    def set_binding(self, action: str, seq_str: str) -> bool:
        """(Re)bind `action` to a QKeySequence string. Returns True if registered."""
        if self._u32 is None:
            return False
        for hid, a in list(self._ids.items()):       # drop old binding for this action
            if a == action:
                self._u32.UnregisterHotKey(self._hwnd, hid)
                del self._ids[hid]
        mods, vk = parse_sequence(seq_str)
        if not vk:
            return False
        hid = self._next
        self._next += 1
        if self._u32.RegisterHotKey(self._hwnd, hid, mods | MOD_NOREPEAT, vk):
            self._ids[hid] = action
            return True
        return False

    def _dispatch(self, wparam: int, time: int) -> None:
        # Qt can invoke the filter twice for one message; collapse a repeat with
        # the same (id, timestamp) so one press = one action.
        stamp = (wparam, time)
        if stamp == self._last:
            return
        self._last = stamp
        action = self._ids.get(wparam)
        if action:
            self.triggered.emit(action)

    def clear(self):
        if self._u32 is None:
            return
        for hid in list(self._ids):
            self._u32.UnregisterHotKey(self._hwnd, hid)
        self._ids.clear()
