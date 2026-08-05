from __future__ import annotations

from PySide6 import QtCore


class CallWorker(QtCore.QThread):
    done = QtCore.Signal(str, object)
    progress = QtCore.Signal(int)

    def __init__(self, fn, tag: str = ""):
        super().__init__()
        self._fn = fn
        self._tag = tag

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
