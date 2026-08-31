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


def shutdown_all(timeout_ms: int = 2500) -> None:
    """Stop and join every registered work-thread.

    Intended to be connected to `QApplication.aboutToQuit` (fires for window
    close *and* programmatic quit), and is idempotent: finished workers are
    skipped. `stop()` is the cooperative hook each worker type provides; as a
    last resort the thread is terminated, matching the app's existing
    close-cleanup behavior.
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
            if not w.wait(rem):
                w.terminate()
                w.wait(500)
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
        """Cleanly stop worker thread before destruction."""
        self.requestInterruption()
        if self.isRunning():
            self.quit()
            self.wait(timeout)
            if self.isRunning():
                self.terminate()
                self.wait(200)
