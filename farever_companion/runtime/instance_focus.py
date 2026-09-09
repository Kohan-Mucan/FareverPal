"""Named single-instance mutex, and the focus hand-off when a second launch loses.

The app already refuses a second instance on the same data folder
(``QLockFile`` on ``fareverpal.lock``, ``app._instance_lock``). That guard
exists for DATA integrity: settings.json / collection.json / progress_*.json are
rewritten wholesale from memory, so two writers lose data. It is not a
UX guard, and the difference showed up live on 2026-10-01.

Two ``run.py`` processes started in the same second. One won the exclusive bind
on UDP 49152 - the socket the injected DLL streams every combat event to - and
the other sat in a bind-retry loop. The loser's window came up looking
perfectly healthy: outgoing DPS still filled in, because that channel is fed by
the MEMORY reader, which needs no socket at all. Only damage taken and healing
went dead, because those event types exist only on the bridge path. So the
meter read as two-thirds working, and the Activity Log line naming the port
clash was the only evidence - on a window the user was not looking at.

A named mutex closes that differently: the second launch does not get to
become a half-dead window at all. It hands focus to the instance that holds the
data folder and exits, so there is never a second meter to misread.

Deliberately scoped per data folder, exactly like the QLockFile guard. A source
launch (``dist/dev-moddata``) and a frozen build (``<exe>/moddata``) are two
different installs that are SUPPOSED to run side by side, and a machine-wide
mutex would refuse the second one. The folder path is hashed into the name, so
two folders never collide and one folder never splits.

Windows-only in its focus hand-off (SetForegroundWindow needs the Win32 input
queue attached); on every other platform the mutex still runs and the second
launch simply exits, which is the behaviour that mattered.
"""
from __future__ import annotations

import ctypes
import hashlib
import os
import sys
from ctypes import wintypes

#: The window title the main window sets (ui/control_panel.py). Matching on it
#: is what lets the loser focus the winner instead of just exiting.
WINDOW_TITLE = "Farever Pal"

#: Prefix for the kernel object name. "Local\" scopes it to the session, so a
#: second user on the same machine (a shared PC, a service account running the
#: app for tests) is a separate namespace and is never refused.
_MUTEX_PREFIX = "Local\\FareverPal.SingleInstance."


def mutex_name(folder: str) -> str:
    """The kernel object name guarding `folder`.

    The path is NORMALISED first: ``D:\\\\a\\\\b`` and ``D:\\\\a\\\\.\\\\b`` are the
    same folder and must not get two different mutexes, or the guard that stops
    a double launch stops working exactly when it is needed.
    """
    try:
        norm = os.path.normcase(os.path.abspath(os.path.normpath(folder or "")))
    except Exception:
        norm = str(folder or "")
    digest = hashlib.sha256(norm.encode("utf-8", "replace")).hexdigest()[:16]
    return f"{_MUTEX_PREFIX}{digest}"


class SingleInstance:
    """Owns the named mutex for one launch, or reports that it lost the race.

    Construct it once, early. ``acquired`` is True for the instance that owns
    the folder; False for the one that must hand focus over and exit. Every
    Win32 failure resolves to ``acquired=True``: refusing to start because the
    mutex could not be created would brick the app on a locked-down machine,
    and the QLockFile guard behind it still protects the data folder.
    """

    def __init__(self, folder: str) -> None:
        self.folder = folder or ""
        self.name = mutex_name(folder)
        self.acquired = True
        self._handle = None
        self._kernel32 = None
        if sys.platform != "win32":
            return
        try:
            self._acquire()
        except Exception:
            self.acquired = True          # never block startup on this
            self._handle = None

    def _acquire(self) -> None:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        k32.CreateMutexW.restype = wintypes.HANDLE
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        k32.CloseHandle.restype = wintypes.BOOL
        self._kernel32 = k32
        handle = k32.CreateMutexW(None, False, self.name)
        if not handle:
            return                      # could not create -> not our problem
        self._handle = handle
        # ERROR_ALREADY_EXISTS means a live instance holds it. That instance may
        # still be starting up, so the loser waits briefly for a window to
        # appear rather than focusing nothing and exiting.
        if ctypes.get_last_error() == 183:      # ERROR_ALREADY_EXISTS
            self.acquired = False

    def release(self) -> None:
        if self._handle and self._kernel32 is not None:
            try:
                self._kernel32.CloseHandle(self._handle)
            except Exception:
                pass
            self._handle = None

    # --- the hand-off ------------------------------------------------------
    def focus_existing(self, timeout_s: float = 3.0) -> bool:
        """Raise and focus the running instance's window. True when one was shown.

        Never raises: a failed focus still leaves the caller correct, because
        the caller exits either way.
        """
        if sys.platform != "win32":
            return False
        try:
            hwnd = self._find_window(timeout_s)
            if not hwnd:
                return False
            return self._raise(hwnd)
        except Exception:
            return False

    def _find_window(self, timeout_s: float):
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        # HWNDs are pointer-sized. Without an explicit restype ctypes assumes a
        # 32-bit int and SILENTLY truncates, which turns a valid handle into a
        # different (usually NULL) one on any machine whose handles run past
        # 2^31 - the call then returns 0 and the loser exits without focusing
        # anything, on exactly the desktops where handles get large.
        user32.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
        user32.FindWindowW.restype = wintypes.HWND
        deadline = timeout_s
        step = 0.15
        while True:
            # The winner may still be importing; a window that is not there yet
            # is the normal case for the first few hundred milliseconds.
            hwnd = user32.FindWindowW(None, WINDOW_TITLE)
            if hwnd:
                return hwnd
            if deadline <= 0:
                return None
            import time
            time.sleep(step)
            deadline -= step

    def _raise(self, hwnd) -> bool:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        # Same truncation trap as above, on every handle- and thread-returning
        # call in this block.
        for fn, args, restype in (
            (user32.ShowWindow, [wintypes.HWND, ctypes.c_int], None),
            (user32.GetForegroundWindow, [], wintypes.HWND),
            (user32.BringWindowToTop, [wintypes.HWND], wintypes.BOOL),
            (user32.SetForegroundWindow, [wintypes.HWND], wintypes.BOOL),
            (user32.SetActiveWindow, [wintypes.HWND], wintypes.HWND),
            (user32.AttachThreadInput,
             [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL], wintypes.BOOL),
        ):
            fn.argtypes = args
            if restype is not None:
                fn.restype = restype
        kernel32.GetCurrentThreadId.argtypes = []
        kernel32.GetCurrentThreadId.restype = wintypes.DWORD
        # GetWindowThreadProcessId is a USER32 export, not a kernel32 one -
        # asking kernel32 for it raises AttributeError, which the caller above
        # cannot distinguish from "the focus failed".
        user32.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD

        user32.ShowWindow(hwnd, 9)            # SW_RESTORE: also un-minimises
        # SetForegroundWindow is refused when the caller is not the foreground
        # process. Attaching to the foreground thread's input queue is the
        # documented way to earn that right, and it must be balanced by a
        # matching DetachThreadInput or the queue stays joined.
        fg = user32.GetForegroundWindow()
        fg_tid = 0
        attached = False
        current_tid = kernel32.GetCurrentThreadId()
        if fg and int(fg) != int(hwnd or 0):
            fg_tid = user32.GetWindowThreadProcessId(fg, None)
            if fg_tid and fg_tid != current_tid and \
                    user32.AttachThreadInput(current_tid, fg_tid, True):
                attached = True
        try:
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
            user32.SetActiveWindow(hwnd)
        finally:
            if attached:
                user32.AttachThreadInput(current_tid, fg_tid, False)
        return True


def claim(folder: str, on_lost=None) -> SingleInstance:
    """Claim `folder`, or hand focus to the winner and return the lost claim.

    `on_lost` is called with the SingleInstance when another instance already
    owns the folder; the caller decides how to report it. Kept as a callback so
    this module never imports Qt - it runs before the QApplication exists.
    """
    guard = SingleInstance(folder)
    if not guard.acquired and callable(on_lost):
        try:
            on_lost(guard)
        except Exception:
            pass
    return guard