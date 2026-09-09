from __future__ import annotations

import threading
import time
import weakref

from PySide6 import QtCore

# Registry of every live work-thread the app owns. A shutdown hook calls
# `shutdown_all()` so no QThread is destroyed while its thread is still
# running — PySide6 prints "QThread: Destroyed while thread '' is still
# running" when an app exit drops the object mid-`run()`.
_LIVE = weakref.WeakSet()
_LOCK = threading.Lock()


def register(worker: "QtCore.QThread") -> None:
    """Track a QThread so `shutdown_all()` can join it before app exit."""
    with _LOCK:
        _LIVE.add(worker)


#: Strong references to workers whose thread has NOT finished yet. `_LIVE` is a
#: WeakSet on purpose (it must not keep a finished worker alive), but that also
#: means it cannot stop a *running* QThread from being destroyed the instant the
#: last attribute holding it is reassigned or cleared. Destroying a QThread
#: while its C++ thread is still executing frees Qt's `QThreadPrivate` out from
#: under `run()`; nothing raises at that moment, and the damage only surfaces
#: later as an access violation on some unrelated allocation.
_RETIRING: "list[QtCore.QThread]" = []


def retire(worker: "QtCore.QThread | None") -> None:
    """Keep `worker` referenced until its thread has actually finished.

    Call this INSTEAD of simply dropping the last reference to a QThread whose
    thread may still be running — in `detach()`/`stop()` when a cooperative
    stop timed out, or at any site that replaces `self._worker`. That dropped
    reference is the 2026-10-03 22:57 run.log crash: an access violation INSIDE
    `QThread.__init__` (game_attach.py:323, main thread) seconds after a game
    close, i.e. the next allocation to fault after a still-running locate
    worker had been freed. A finished worker is released by the next
    `retire()`, so this holds at most the handful of workers actually in
    flight.
    """
    if worker is None:
        return
    if worker not in _RETIRING:
        _RETIRING.append(worker)
    _prune_retired()


def _prune_retired() -> None:
    """Release retired workers whose thread has ended.

    Only then is destroying the QThread safe, so nothing leaves this list
    while `isRunning()` — the reference is what keeps it alive.
    """
    if not _RETIRING:
        return
    still: "list[QtCore.QThread]" = []
    for w in _RETIRING:
        try:
            if w.isRunning():
                still.append(w)
                continue
            w.wait(0)          # finished: join so the C++ teardown is done
        except RuntimeError:
            continue           # wrapper already deleted: just drop the entry
        except Exception:
            still.append(w)
    _RETIRING[:] = still


def shutdown_all(timeout_ms: int = 2500) -> None:
    """Stop and join every registered work-thread.

    Intended to be connected to `QApplication.aboutToQuit` (fires for window
    close *and* programmatic quit), and is idempotent: finished workers are
    skipped. `stop()` is the cooperative hook each worker type provides. A
    worker that misses its stop window is left to finish on its own —
    deliberately NO terminate() fallback (see the comment in the join loop
    below for the heap-corruption crash that decided this).
    """
    live = list(_LIVE)
    for w in live:
        try:
            stop = getattr(w, "stop", None)
            if stop is not None:
                stop()
            else:
                w.requestInterruption()
        except Exception:
            pass
    deadline = time.monotonic() + timeout_ms
    for w in live:
        try:
            if not w.isRunning():
                continue
            rem = int((deadline - time.monotonic()) * 1000) or 50
            # Deliberately NO terminate() fallback here. TerminateThread on a
            # thread that is mid-allocation leaves the process heap lock held
            # or the heap in a torn state; the next allocation on ANY thread
            # then faults. That was the main-thread access violation in
            # CallWorker.__init__ right after closing the game: the game-close
            # detach had terminate()d a locate worker inside the native
            # reader, and the next CallWorker the 1 Hz diagnostics tick
            # spawned died in the C++ constructor.
            if not w.wait(rem):
                pass
        except Exception:
            pass


class CallWorker(QtCore.QThread):
    done = QtCore.Signal(str, object)
    progress = QtCore.Signal(int)

    def __init__(self, fn, tag: str = ""):
        super().__init__()
        self._fn = fn
        self._tag = tag
        register(self)

    def run(self):
        try:
            if not self.isInterruptionRequested():
                res = self._fn(self)
            else:
                res = {"ok": False, "error": "interrupted"}
        except Exception as e:
            res = {"ok": False, "error": str(e)}
        if not self.isInterruptionRequested():
            self.done.emit(self._tag, res)

    def stop(self, timeout: int = 800):
        """Cleanly stop worker thread before destruction.

        No terminate() fallback, same reason as shutdown_all(): a thread
        killed mid-allocation corrupts the heap, and the crash surfaces later
        somewhere unrelated. A worker stuck past its stop window finishes in
        the background and is simply not reused.
        """
        self.requestInterruption()
        if self.isRunning():
            self.quit()
            self.wait(timeout)
