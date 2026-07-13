"""Game attachment controller, attach / locate / detach + the auto-attach watcher.

Pulled out of ControlPanel: this owns the read-only lifecycle (the `Proc` handle,
the `LiveModel`, the locate worker, and the 2 s `AutoAttach` poll) and nothing
about the UI. It talks to the panel only through signals, so the panel stays a
view: it shows the busy bar, status line, log, overlay-card gating, and closes
the overlays, driven entirely by these signals.

Read-only throughout: the locate runs on a worker thread (a pure memory scan),
and detach cleanly stops the model's background threads and closes the handle.
"""
from __future__ import annotations

import time

from PySide6 import QtCore

from ..config import Settings
from ..core.proc import Proc, ProcError, backend_name, find_pid
from ..core.model import LiveModel
from ..core.autoattach import AutoAttach, Act


class _LocateWorker(QtCore.QThread):
    done = QtCore.Signal(object)   # address, None, or Exception

    def __init__(self, model: LiveModel):
        super().__init__()
        self.model = model
        self._stop_requested = False

    def run(self):
        try:
            # Check periodically if we should stop
            addr = self.model.locate_player()
            if not self._stop_requested:
                self.done.emit(addr)
        except Exception as e:
            if not self._stop_requested:
                self.done.emit(e)

    def stop(self):
        """Request the thread to stop and wait for it to finish."""
        self._stop_requested = True
        # For threads with custom run(), quit() has no effect - wait for natural completion
        if self.isRunning():
            self.wait(30000)  # Generous timeout for long scans (30 seconds)


class GameAttachmentController(QtCore.QObject):
    # UI-ward signals (the panel connects these to its widgets).
    status = QtCore.Signal(bool, str)     # (attached, text) -> status line
    log = QtCore.Signal(str)              # status-log line
    locating = QtCore.Signal(bool)        # show/hide the locate busy bar
    located_changed = QtCore.Signal(bool) # enable/disable the overlay cards
    model_changed = QtCore.Signal(object) # the live model (or None) -> OverlayManager
    detaching = QtCore.Signal()           # about to release: close overlays FIRST
    goto_log = QtCore.Signal()           # manual attach failure -> open the Log tab

    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent)
        self.s = settings
        self.proc: Proc | None = None
        self.model: LiveModel | None = None
        self._worker: _LocateWorker | None = None
        self._auto = AutoAttach()
        self._busy = False
        self._located_shown = False        # has the UI applied the 'located' transition?
        self._located_fail_logged = False
        self._cooldown = 0                 # ticks to skip after an auto-attach failure
        self._stopped = False              # flag to prevent new work during shutdown
        # locate elapsed-time counter (the busy bar widget lives in the panel)
        self._locate_t0: float | None = None
        self._locate_timer = QtCore.QTimer(self)
        self._locate_timer.setInterval(500)
        self._locate_timer.timeout.connect(self._tick_locate)
        # 2 s auto-attach watcher
        self._attach_timer = QtCore.QTimer(self)
        self._attach_timer.setInterval(2000)
        self._attach_timer.timeout.connect(self._attach_tick)

    def start(self) -> None:
        """Begin watching for the game (call once the panel's UI exists)."""
        self._stopped = False
        self._attach_timer.start()
        self.refresh_status()

    def stop(self) -> None:
        """Stop timers and wait for any running locate worker to finish cleanly."""
        self._stopped = True
        self._attach_timer.stop()
        self._locate_timer.stop()
        # Wait for locate worker to finish before continuing shutdown
        if self._worker is not None:
            if self._worker.isRunning():
                self._worker.stop()
            # Disconnect signal to prevent potential segfaults during destruction
            try:
                self._worker.done.disconnect()
            except Exception:
                pass
            self._worker = None

    # --- locate progress feedback ---------------------------------------
    def _start_locate(self) -> None:
        if self._locate_t0 is None:
            self._locate_t0 = time.monotonic()
        self.locating.emit(True)
        if not self._locate_timer.isActive():
            self._locate_timer.start()
        self._tick_locate()

    def _stop_locate(self) -> None:
        self._locate_t0 = None
        self._locate_timer.stop()
        self.locating.emit(False)

    def _tick_locate(self) -> None:
        if self._locate_t0 is None or self.proc is None:
            return
        el = time.monotonic() - self._locate_t0
        self.status.emit(True, f"attached PID {self.proc.pid} · locating player… ({el:.0f}s)")

    # --- attach / locate -------------------------------------------------
    def attach(self, auto: bool = False) -> None:
        if self._stopped:
            return
        self._busy = True
        try:
            self.proc = Proc.attach()
        except ProcError as e:
            self.proc = None
            self._busy = False
            if auto:
                # Don't yank the user to the Log tab or spam: one line, then back
                # off a few ticks (e.g. another memory tool conflicting).
                self._cooldown = 5
                self.log.emit(f"Auto-attach deferred: {e} (retrying shortly).")
                self.refresh_status()
            else:
                self.log.emit(f"ATTACH FAILED: {e}")
                self.goto_log.emit()
            return
        self.model = LiveModel(self.proc)
        self.model_changed.emit(self.model)
        self._located_fail_logged = False
        self.status.emit(True, f"attached PID {self.proc.pid} · locating player…")
        self._start_locate()
        self.log.emit(f"Attached PID {self.proc.pid}. {len(self.model.chests)} chests. "
                      "Locating the player by scanning memory (read-only)…")
        self._worker = _LocateWorker(self.model)
        self._worker.done.connect(self._on_located)
        self._worker.start()

    def _on_located(self, result) -> None:
        self._busy = False
        if isinstance(result, Exception):
            self._stop_locate()
            self.log.emit(f"Locate failed: {result}")
            self.status.emit(True, "attached · player NOT found")
            self._mark_lost()
            return
        if not result:
            # The watcher's RELOCATE retries each tick until the player loads in;
            # log once so menu time doesn't fill the log. The slow scan is done
            # (type is cached now), so hide the busy bar; retries are cheap.
            self._stop_locate()
            if not self._located_fail_logged:
                self.log.emit("Player not found. Be fully loaded in-world (auto-retrying).")
                self._located_fail_logged = True
            self.status.emit(True, "attached · waiting for world")
            self._mark_lost()      # MUST reset so the next locate re-applies 'located'
            return
        self._stop_locate()
        self.status.emit(True, f"attached PID {self.proc.pid} · player @ {result:#x}")
        self._located_fail_logged = False
        if not self._located_shown:
            self._located_shown = True
            self.located_changed.emit(True)
            self.log.emit(f"Player located @ {result:#x}. Overlays unlocked.")

    def _mark_lost(self) -> None:
        """Player no longer resolvable (main menu / between worlds). Reset the
        'located' latch so the moment it resolves again the located transition
        (status + overlay-card gating) is re-applied. Without this the status can
        stick on 'waiting for world' after a menu->world round-trip even though
        the tools (which read player_addr live) are working.
        """
        if self._located_shown:
            self._located_shown = False
            self.located_changed.emit(False)

    # --- auto-attach watcher --------------------------------------------
    def _attach_tick(self) -> None:
        """2 s poll: attach/locate/detach with the game, via the manual paths."""
        if self._stopped:
            return
        if backend_name() == "none":
            return
        if self._cooldown > 0:
            self._cooldown -= 1
            return
        try:
            pid = find_pid()
        except Exception:
            return
        act = self._auto.decide(
            enabled=self.s.auto_attach,
            running_pid=pid,
            attached_pid=(self.proc.pid if self.proc else None),
            located=bool(self.model and self.model.player_addr is not None),
            busy=self._busy,
        )
        if act is Act.ATTACH:
            self.attach(auto=True)
        elif act is Act.DETACH:
            self._auto_detach()
        elif act is Act.RELOCATE:
            self._relocate()
        if act is Act.NONE:
            self.refresh_status()
        # `player_addr` can become set between locate workers, right as decide()
        # flips to NONE (it stops issuing RELOCATE once located). The 'located' UI
        # transition normally only fires from a worker's _on_located, so without
        # this reconciliation the top bar can stay stuck at "waiting for world"
        # while the player IS in fact located. Apply the transition once.
        if self.proc is not None and not self._busy:
            pa = self.model.player_addr if self.model else None
            if pa is not None and not self._located_shown:
                self._on_located(pa)           # back in-world -> re-apply 'located'
            elif pa is None and self._located_shown:
                self.status.emit(True, "attached · waiting for world")
                self._mark_lost()              # left to menu -> re-lock the tools

    def _relocate(self) -> None:
        """Quietly re-run the locate worker (attached but not yet in-world)."""
        if self._stopped or self.model is None or self._busy:
            return
        if self._worker is not None and self._worker.isRunning():
            return
        self._busy = True
        self._worker = _LocateWorker(self.model)
        self._worker.done.connect(self._on_located)
        self._worker.start()

    def _auto_detach(self) -> None:
        """Detach because the game closed/restarted; the watcher re-attaches
        automatically when it relaunches.
        """
        self.detach()
        self.log.emit("Game closed — detached. Watching for Farever…")
        self.refresh_status()

    def refresh_status(self) -> None:
        """When detached, reflect the watcher state in the top-bar status line.

        Auto-attach is always on (no UI toggle); `auto_attach` survives as a
        hidden settings.json escape hatch, if a user sets it false there, the
        watcher stops attaching and we just read 'detached'.
        """
        if self.proc is not None:
            return
        if self.s.auto_attach and backend_name() != "none":
            self.status.emit(False, "watching for Farever…")
        else:
            self.status.emit(False, "detached")

    def detach(self) -> None:
        """Tear down the live session. Emits `detaching` FIRST so the panel closes
        the overlays before the model's background threads stop (overlays read the
        model). Called by the watcher (game closed), on update install, and on app
        close, there is no manual detach button.
        """
        self._busy = False
        self._located_shown = False
        self._stop_locate()
        # Wait for locate worker to finish before continuing shutdown
        if self._worker is not None:
            if self._worker.isRunning():
                self._worker.stop()
            try:
                self._worker.done.disconnect()
            except Exception:
                pass
            self._worker = None
        self.detaching.emit()              # panel closes overlays + clears their model
        if self.model is not None:
            try:
                self.model.shutdown()      # stop background threads before detaching
            except Exception as e:
                self.log.emit(f"shutdown warning: {e}")
        if self.proc:
            self.proc.close()
        self.proc = None
        self.model = None
        self.model_changed.emit(None)
        self.located_changed.emit(False)   # gate the overlay cards again
        self.status.emit(False, "detached")  # refresh_status sets the watch line