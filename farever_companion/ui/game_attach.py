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
import logging
import os
import time

from PySide6 import QtCore

from ..config import Settings
from ..core.bridge_log import remove_dev_flag
from ..core.proc import (Proc, ProcError, backend_name, find_pid,
                         sync_bridge_dev_flag)
from ..core.model import LiveModel
from ..core.autoattach import AutoAttach, Act
from .inject_retry import RETRY_TICK_S, InjectRetryState
from .inject_watchdog import InjectWatchdogMixin
from .workers import register as _register_worker, retire as _retire_worker

logger = logging.getLogger("farever_companion")


def dmg_hints_from(settings: Settings) -> tuple[str, str]:
    """(DamageDisplay ptr, DamageResult ptr) to seed the reader with.

    Precedence: an explicit FAREVER_DMG_* env override (dev runs) wins,
    otherwise the class-cached pointers from the last calibration in settings.
    Either value is class-name-verified on use and falls back to scanning when
    the game relaunched and the saved address went stale."""
    return (os.environ.get("FAREVER_DMG_TYPE", "") or getattr(
                settings, "dmg_display_type", "") or "",
            os.environ.get("FAREVER_DMG_RESULT_TYPE", "") or getattr(
                settings, "dmg_result_type", "") or "")


def _attach_capture_mode(requested: str, bridge: dict) -> tuple[str, bool]:
    """Keep DLL capture modes mutually exclusive at process attach.

    Option 1 is a file-backed proxy: if its FareverPal DLL remains in the game
    folder, Farever can load it even when settings request Option 2/3. Prefer
    the already-present proxy until the user explicitly removes it; never start
    an injector alongside it or label the passive scanner as DLL-free while a
    proxy can still boot.
    """
    mode = (requested or "proxy").strip().lower()
    if mode not in ("proxy", "injector", "memory"):
        mode = "proxy"
    proxy_present = bool(
        bridge.get("proxy_in_game_dir")
        or (bridge.get("detected") and bridge.get("option") == 1)
    )
    if mode != "proxy" and proxy_present:
        return "proxy", True
    return mode, False


_INJECTED_PIDS: set[int] = set()

# The prebuilt helper binary ships in dps_bridge/ next to the DLLs, both in
# source checkouts and inside the one-file frozen bundle (sys._MEIPASS).
#
# injector.c used to pick its mode from its OWN FILENAME
# (`strstr(exePath, "uninject")`), and build_all.bat published it under both
# names - so the two copies were different programs. That dispatch is gone (a
# mode needs an explicit --uninject argument), but the name is still
# structural: one name is built and shipped, and a second one would mean this
# list could be handed a copy nothing here intends to run. When it was, the app
# ran the uninjector, which printed "Nothing to uninject" and left the DLL
# unloaded - the capture then sat at status=need_inject for the whole session
# with zero events, reading as "the injector is broken".
#
# Removal does not use this binary at all: _try_uninject_hook calls the
# crash-safe core.proc.safe_uninject_hook instead.
_INJECT_EXE_NAMES = ("farever_dps.exe",)


def _hook_exe_candidates() -> list:
    """Existing helper-binary paths to try, frozen bundle first."""
    import sys
    from pathlib import Path
    out: list = []
    meipass = getattr(sys, "_MEIPASS", "")
    if getattr(sys, "frozen", False) and meipass:
        out.append(Path(meipass) / "dps_bridge")
        out.append(Path(meipass))
    return out


def _hook_launch_candidates() -> list:
    """Ordered inject-command paths: frozen bundle first, then dev layouts.

    Frozen-bundle hits are PREPENDED in order (the dev paths below are
    meaningless on a user's PC). The old `candidates.insert(0, c)` reversed
    that priority and, worse, let the uninjector copy sort ahead of the
    injector; this builds the list explicitly so the first existing entry is
    always the one that gets run.
    """
    from pathlib import Path
    import sys
    dev = [
        Path(__file__).resolve().parents[2] / "dps_bridge" / "farever_dps.exe",
        Path("D:/1MobileApp/vs/FareverPal-SC/GameFiles/Farever/hooks/hook/farever_dps/farever_dps.exe"),
        Path("D:/1MobileApp/vs/FareverPal-SC/GameFiles/Farever/hooks/hook/farever_dps.exe"),
        Path("D:/1MobileApp/vs/FareverPal-SC/GameFiles/Farever/hooks/farever_dps.exe"),
        Path(__file__).resolve().parents[2] / "hook" / "farever_dps.exe",
        Path(__file__).resolve().parents[2] / "dist" / "dps_bridge" / "farever_dps.exe",
    ]
    if getattr(sys, "frozen", False):
        dev += [Path(sys.executable).parent / "dps_bridge" / "farever_dps.exe",
                Path(sys.executable).parent / "farever_dps.exe"]
    frozen = [d / name for d in _hook_exe_candidates() for name in _INJECT_EXE_NAMES
              if (d / name).is_file()]
    return frozen + dev


# Lines the injector prints when it did NOT inject. injector.c exits 0 on
# several of these paths (notably the uninject branch, which is what made a
# whole capture session look healthy), so the exit code alone cannot decide
# success - the text has to be read too.
_INJECT_REFUSALS = (
    "hot uninjector",              # the REMOVAL path ran: wrong binary or flag
    "nothing to uninject",
    "not injecting",               # safe-unload refused a reload
    "safe unload failed",
    "already resident",            # a reload it could not complete
    "not found at",                # farever_dps.dll missing beside the exe
    "could not open process",
    "could not reopen process",
    "loadlibraryw failed",
    "is not running",
)


def _classify_inject_output(returncode: int | None, out: str) -> tuple[bool, str]:
    """``(ok, reason)`` for one finished injector run.

    A run FAILED when it printed a refusal or exited non-zero; it succeeded on
    a clean exit otherwise. The injector's own wording is the contract, so an
    unrecognised silence is reported ok rather than as a false failure - the
    module probe settles a genuine no-op.
    """
    text = (out or "").lower()
    if not text.strip():
        clean = returncode in (0, None)
        return clean, "" if clean else f"injector exited {returncode} with no output"
    for marker in _INJECT_REFUSALS:
        if marker in text:
            for line in text.splitlines():
                if marker in line:
                    return False, line.strip(" .*")[:160]
            return False, marker
    if returncode not in (0, None):
        return False, f"injector exited {returncode}"
    return True, ""


def _try_launch_hook(pid: int, force: bool = False, log_cb: object = None,
                     on_result: object = None, proc: object = None,
                     model: object = None) -> None:
    """Launch dps_bridge/farever_dps.exe <pid> asynchronously in the background.

    ``on_result(ok, reason)`` fires once the injector has finished, so a
    refusal is reported as a failure instead of being logged and forgotten.

    ``proc``/``model`` are the CALLER's own handles on the game process and the
    live model - the Settings mode switch has both, and passes them so the
    launch carries its context instead of the callee having to be told it again.
    The launcher needs neither to RUN the injector (the pid is the whole
    subprocess argument list), so both are optional and the controller's call
    site passes neither:

    * ``proc`` supplies the pid when the caller hands over the process rather
      than its id.
    * ``model`` is the fallback verdict sink when the caller supplies no
      ``on_result``. ``model.damage.note_inject_result`` is the same sink the
      controller's ``_on_inject_result`` writes to, so a refusal still reaches
      the bridge status instead of only the dev log. Callers that pass an
      ``on_result`` (both of today's) keep owning the verdict themselves.
    """
    if not pid and proc is not None:
        pid = int(getattr(proc, "pid", 0) or 0)
    if not pid:
        return None
    if not force and pid in _INJECTED_PIDS:
        return None  # Never execute repeatedly for the same process
    _INJECTED_PIDS.add(pid)
    import threading

    def _report(ok: bool, reason: str) -> None:
        if not ok:
            _INJECTED_PIDS.discard(pid)   # allow a retry once the cause is fixed
        cb = on_result
        if not callable(cb):
            cb = getattr(getattr(model, "damage", None),
                         "note_inject_result", None)
        if not callable(cb):
            return
        try:
            cb(ok, reason)
        except Exception:
            pass

    def _worker():
        try:
            import subprocess
            candidates = _hook_launch_candidates()
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
            import logging
            logger = logging.getLogger("farever_companion")
            found = False
            for c in candidates:
                if c and c.is_file():
                    found = True
                    # Dev-only: the DLL reads the flag beside ITSELF, so it has
                    # to sit next to the injector we are about to run - and only
                    # in a run-log.bat session (sync writes the flag in dev
                    # mode, clears any leftover otherwise).
                    sync_bridge_dev_flag(str(c.parent))
                    logger.info("[dps-hook-launch] Launching injector %s for PID %d", c, pid)
                    if callable(log_cb):
                        log_cb(f"Combat Bridge: Launching injector {c.name} for PID {pid}…")
                    proc = subprocess.Popen([str(c), str(pid)], cwd=str(c.parent),
                                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                            creationflags=flags, text=True)
                    try:
                        out, _ = proc.communicate(timeout=2.5)
                        out_clean = (out or "").strip()
                        if out_clean:
                            logger.info("[dps-hook-launch] %s", out_clean)
                            if callable(log_cb):
                                log_cb(f"Combat Bridge Injector: {out_clean}")
                        ok, reason = _classify_inject_output(
                            proc.returncode, out_clean)
                        if ok:
                            logger.info("[dps-hook-launch] inject OK (rc=%s)",
                                        proc.returncode)
                        else:
                            logger.warning("[dps-hook-launch] inject FAILED: %s", reason)
                            if callable(log_cb):
                                log_cb(f"Combat Bridge: INJECT FAILED — {reason}. "
                                        "No damage events will be captured; see the "
                                        "Activity Log or switch capture mode in Settings.")
                        _report(ok, reason)
                    except Exception as ex:
                        logger.warning("[dps-hook-launch] Injector wait error: %s", ex)
                        if callable(log_cb):
                            log_cb(f"Combat Bridge Injector timeout/error: {ex}")
                        # A timeout is NOT proof the inject failed - the
                        # injector may still be mid-LoadLibrary - so report
                        # unknown-ok and let the module probe decide.
                        _report(True, "")
                    break
            if not found:
                msg = ("Combat Bridge Notice: farever_dps.exe injector not found "
                       "in project folders.")
                if callable(log_cb):
                    log_cb(msg)
                _report(False, "farever_dps.exe injector not found")
        except Exception as e:
            import logging
            logging.getLogger("farever_companion").warning("[dps-hook-launch] Failed: %s", e)
            if callable(log_cb):
                log_cb(f"Combat Bridge Injector failed: {e}")

    thread = threading.Thread(target=_worker, daemon=True, name="dps-hook-launcher")
    thread.start()
    return thread      # returned so a caller (and tests) can join the verdict


def _try_uninject_hook(pid: int, log_cb: object = None) -> tuple[bool, str]:
    """Strip the combat bridge's hooks from the running game, synchronously.

    Always uses the crash-safe Python path (``core.proc.safe_uninject_hook``).
    When the DLL exports SafeUnload (built with the hook source's safe_unload.c)
    it unloads ITSELF on its own thread — worker joined, hooks disabled,
    MH_Uninitialize, then FreeLibraryAndExitThread — a real unmap, no restart
    needed. Older DLLs fall back to disable -> grace -> MH_Uninitialize with
    no unmap (the inert DLL clears on the next game restart); a remote
    FreeLibrary there tore the image out from under live detours and crashed
    the game.

    Blocks up to a few seconds (unmap polling); the Settings Uninject handler
    already runs this off the UI thread. Returns the ``(ok, reason)`` tuple
    (also logged) so callers can report the real outcome instead of assuming
    success — the card feedback must never claim removal on a refusal.
    """
    if not pid:
        return False, "no game process"
    _INJECTED_PIDS.discard(pid)
    import logging
    logger = logging.getLogger("farever_companion")

    from ..core.proc import safe_uninject_hook
    try:
        ok, reason = safe_uninject_hook(pid, "farever_dps.dll")
        logger.info("[dps-hook-uninject] pid=%d ok=%s: %s", pid, ok, reason)
        if callable(log_cb):
            if ok:
                log_cb(f"Combat Bridge: {reason}.")
            else:
                log_cb(f"Combat Bridge: Uninject refused — {reason}.")
        return ok, reason
    except Exception as e:
        logger.warning("[dps-hook-uninject] Failed: %s", e)
        if callable(log_cb):
            log_cb(f"Combat Bridge Uninject failed: {e}")
        return False, str(e)


class _LocateWorker(QtCore.QThread):
    done = QtCore.Signal(object)   # address, None, or Exception

    def __init__(self, model: LiveModel):
        super().__init__()
        self.model = model
        self._stop_requested = False
        _register_worker(self)

    def run(self):
        try:
            # Check periodically if we should stop
            addr = self.model.locate_player()
            if not self._stop_requested and not self.isInterruptionRequested():
                self.done.emit(addr)
        except Exception as e:
            if not self._stop_requested and not self.isInterruptionRequested():
                self.done.emit(e)

    def stop(self, timeout: int = 1500):
        """Request the thread to stop and wait for it to finish cleanly.

        Deliberately NO terminate() fallback: TerminateThread on a thread
        inside the native reader can leave the process heap lock held, and the
        next allocation on any thread then access-violates (seen as the
        main-thread AV inside CallWorker.__init__ right after a game close,
        when the 1 Hz diagnostics tick spawned the next worker). The next
        locate simply waits for the previous one instead; a worker that
        ignores its stop flag is a bug to fix in the worker, not a reason to
        kill threads mid-instruction.
        """
        self._stop_requested = True
        self.requestInterruption()
        if self.isRunning():
            self.quit()
            self.wait(timeout)


class GameAttachmentController(InjectWatchdogMixin, QtCore.QObject):
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
        self._hook_pending = False         # Option 2 waits for a located player
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
        # Option 2 inject watchdog. The first attempt happens once, on the
        # located transition; this re-runs it when a boss fight is live and
        # not one event has been decoded. The policy (budget, interval, and
        # the located gate it must NOT bypass) lives in inject_retry.py.
        self._inject_retry = InjectRetryState()
        self._inject_timer = QtCore.QTimer(self)
        self._inject_timer.setInterval(int(RETRY_TICK_S * 1000))
        self._inject_timer.timeout.connect(self._tick_inject_retry)

    def start(self) -> None:
        """Begin watching for the game (call once the panel's UI exists)."""
        self._stopped = False
        self._attach_timer.start()
        self._inject_timer.start()
        self.refresh_status()

    def stop(self) -> None:
        """Stop timers and wait for any running locate worker to finish cleanly."""
        self._stopped = True
        self._attach_timer.stop()
        self._locate_timer.stop()
        self._inject_timer.stop()
        # Wait for locate worker to finish before continuing shutdown
        if self._worker is not None:
            if self._worker.isRunning():
                self._worker.stop()
            # Disconnect signal to prevent potential segfaults during destruction
            try:
                self._worker.done.disconnect()
            except Exception:
                pass
            # `stop()` may have timed out with the thread still scanning; keep
            # a reference until it really ends rather than freeing it now.
            _retire_worker(self._worker)
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
        # Detect before publishing the model so a saved Option 2/3 selection
        # cannot start alongside a proxy file left in the game folder.
        bridge = self.proc.detect_combat_bridge()
        requested_mode = getattr(self.s, "dps_mode", "proxy")
        dps_mode, proxy_conflict = _attach_capture_mode(requested_mode, bridge)
        if proxy_conflict:
            self.s.dps_mode = "proxy"
            self.s.dps_hook_enabled = False
            self.s.save()
            self.log.emit(
                f"Combat Bridge: {bridge.get('dll_name') or 'Option 1 proxy'} "
                "is installed; Options 2/3 are locked until it is removed. "
                "Using the existing proxy for this session.")
        self.model = LiveModel(self.proc)
        # Configure the capture source before publishing the model. The
        # model_changed subscribers can tick the tracker immediately; leaving
        # DamageSourceManager's default at proxy here starts the bridge even
        # when Settings already selected Option 3.
        if hasattr(self.model, "damage"):
            self.model.damage.set_mode(dps_mode)
        self.model_changed.emit(self.model)
        # Past-fight history survives restarts: enable on-disk persistence at
        # the shared moddata location. Files are per-character
        # (dps_history_<profile>.json) so different heroes keep separate
        # history - the tracker swaps files on character change.
        try:
            from ..config import dps_dir
            self.model.dps.set_history_dir(dps_dir())
        except Exception:
            pass
        # Seed the located DamageDisplay / DamageResult type pointers so a cold
        # start skips the multi-minute scan: an explicit env override wins (dev
        # runs), otherwise the class-cached pointers from the last calibration
        # (settings). Either is verified by class name inside find_type /
        # find_result_type and falls back to scanning when the game relaunched
        # and the saved address went stale.
        hint, rhint = dmg_hints_from(self.s)
        if hint:
            self.model.damage.set_type_hint(hint)
        if rhint:
            self.model.damage.set_result_type_hint(rhint)
        if hasattr(self.model, "damage"):
            self.model.damage.log_line = self.log.emit
        if hasattr(self.model, "dps"):
            self.model.dps.log_line = self.log.emit
            # Fresh attach under a capture mode: drop cached foe max-HP
            # snapshots from any previous process.
            tracker = getattr(self.model, "dps", None)
            if tracker is not None and hasattr(tracker, "reset_foe_max_hp"):
                tracker.reset_foe_max_hp()

        # Report the already-resolved bridge state. Proxy removal is explicit;
        # attach never deletes a game-folder DLL behind the user's back.
        # Dev-only bridge logging, and only where a binary can read it. In a
        # run-log.bat session the flag is written (sync is dev-gated: it
        # clears leftovers in normal runs). Proxy DLLs load from the game
        # folder and look next to themselves, so the flag belongs there in a
        # proxy session - but a pure injector session must not leave one:
        # farever_dps.dll loads from dps_bridge and never reads the game dir.
        if dps_mode == "proxy" or bridge.get("proxy_in_game_dir"):
            sync_bridge_dev_flag(bridge.get("game_dir", ""))
        else:
            remove_dev_flag(bridge.get("game_dir", "") or "")
        if bridge.get("detected"):
            self.log.emit(f"Combat Bridge: {bridge['status_summary']}")
        else:
            if dps_mode == "injector":
                if getattr(self.s, "dps_hook_enabled", True):
                    # Defer injection until the read-only player locate
                    # succeeds. Farever may have a process while its world and
                    # runtime table are still booting; injecting at that point
                    # only makes the DLL wait for a table that is not ready.
                    self._hook_pending = True
                    self.log.emit("Combat Bridge: farever_dps.dll not detected. Waiting for player location before injecting…")
                else:
                    self.log.emit("Combat Bridge: Option 2 (Injector) selected, but hook is disabled in settings.")
            elif dps_mode == "proxy":
                self.log.emit(f"Combat Bridge: {bridge['status_summary']}")
            elif dps_mode == "memory":
                self.log.emit("Combat Bridge: Option 3 (Memory Reader fallback active, no DLL required).")

        self._located_fail_logged = False
        # Clear a GO flag a previous session left behind BEFORE any locate can
        # write a fresh one - a stale flag would arm the proxy at boot time,
        # which is the crash window the handshake exists to close.
        if dps_mode == "proxy":
            try:
                from ..core.proc import clear_stale_proxy_go
                clear_stale_proxy_go(bridge.get("game_dir", ""),
                                     getattr(self.s, "dps_proxy_dll", ""))
            except Exception:
                pass
        self.status.emit(True, f"attached PID {self.proc.pid} · locating player…")
        self._start_locate()
        # "Attached" = the read-only memory reader bound to the game process
        # (no injection, no writes — this line used to read as if the app had
        # injected something). Chest count included: the scan is done already.
        self.log.emit(f"Reading game memory — PID {self.proc.pid} (read-only, "
                      f"nothing injected). {len(self.model.chests)} chests mapped. "
                      "Locating the player…")
        self._swap_worker(_LocateWorker(self.model))
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
        # GO handshake (retired with winmm, kept inert): the world is proven up
        # by the player locate itself. PROXY_GO_ENABLED is empty now, so this is
        # a no-op for every shipped proxy; the call stays so a future t=0 proxy
        # is gated the same way the injector is, without re-inventing it.
        try:
            self._signal_proxy_go()
        except Exception:
            pass
        if (getattr(self, "_hook_pending", False)
                and getattr(self.s, "dps_mode", "proxy") == "injector"
                and getattr(self.s, "dps_hook_enabled", True)):
            self._hook_pending = False
            self.log.emit(f"Combat Bridge: player located — launching injector for PID {self.proc.pid}…")
            self._inject_retry.note_attempt()
            self._launch_injector()
        else:
            # A mode/settings change while the locate worker was running must
            # not accidentally inject after the user disabled Option 2.
            self._hook_pending = False
        if not self._located_shown:
            self._located_shown = True
            # Log FIRST: located_changed fans out to the overlay restore
            # (now staggered), whose "Opened …" lines must read after the
            # locate they follow — not before it.
            self.log.emit(f"Player located @ {result:#x}. Overlays unlocked.")
            self.located_changed.emit(True)

    def _on_inject_result(self, ok: bool, reason: str) -> None:
        """Forward the injector's verdict to the capture model (worker thread).

        Without this a refusal is only in the Activity Log: the DPS card keeps
        saying "injector not attached" and the session ends with zero events
        and no explanation anywhere the user is actually looking.
        """
        dm = getattr(self.model, "damage", None)
        if dm is None:
            return
        try:
            dm.note_inject_result(ok, reason)
        except Exception:
            pass

    # --- Option 2 inject watchdog ------------------------------------------
    def _launch_injector(self, force: bool = False) -> None:
        """Run the injector in the background — the watchdog's only side effect.

        Kept here (not in the watchdog mixin) so that mixin stays free of the
        launcher and of this module, which imports it. `force=True` on a retry:
        the per-PID one-shot guard was consumed by the first attempt, and the
        whole point of a retry is to run it again.
        """
        if self.proc is None:
            return
        _try_launch_hook(self.proc.pid, force=force, log_cb=self.log.emit,
                         on_result=self._on_inject_result)

    def _signal_proxy_go(self) -> None:
        """Write the GO flag into the game folder (best-effort, now inert).

        Retired with winmm (2026-09-23): no shipped proxy loads at t=0 any
        more, so PROXY_GO_ENABLED is empty and signal_proxy_go writes nothing.
        The call stays as the hook point for a future t=0 proxy. Failure is
        deliberately silent.
        """
        if self.proc is None or self.s.dps_mode != "proxy":
            return
        try:
            from ..core.proc import signal_proxy_go
            signal_proxy_go(self.proc.pid, self.s.dps_proxy_dll,
                            log_cb=(self.log.emit if hasattr(self, "log") else None))
        except Exception:
            pass

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
        # Persist the located DamageDisplay/DamageResult pointers once the
        # reader has calibrated (2 s watcher cadence keeps checking cheaply;
        # save() only writes when the value actually changed, so a successful
        # cache just stops touching disk). Stale pointers from a game relaunch
        # are replaced the moment the reader re-locates.
        if self.proc is not None and self.model is not None:
            try:
                dp, rp = self.model.damage.located_pointers()
            except Exception:
                dp = rp = None
            if dp and rp:
                d_hex, r_hex = f"{dp:#x}", f"{rp:#x}"
                if (self.s.dmg_display_type != d_hex
                        or self.s.dmg_result_type != r_hex):
                    self.s.dmg_display_type = d_hex
                    self.s.dmg_result_type = r_hex
                    try:
                        self.s.save()
                    except Exception:
                        pass

    def _swap_worker(self, worker: "_LocateWorker") -> None:
        """Install a fresh locate worker, retiring the one it replaces.

        The previous worker is released only through `workers.retire()`: if its
        stop timed out it is still running, and letting go of a running QThread
        frees Qt's internals under it (see `workers.retire` for the 2026-10-03
        crash this fixed). Not stopped here on purpose — `stop()` waits up to
        1.5 s, which would block the UI thread that builds the replacement.
        """
        previous = self._worker
        self._worker = worker
        if previous is not None and previous is not worker:
            try:
                previous.done.disconnect()
            except Exception:
                pass
            _retire_worker(previous)

    def _relocate(self) -> None:
        """Quietly re-run the locate worker (attached but not yet in-world)."""
        if self._stopped or self.model is None or self._busy:
            return
        if self._worker is not None and self._worker.isRunning():
            return
        self._busy = True
        self._swap_worker(_LocateWorker(self.model))
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
        self._hook_pending = False
        self._located_shown = False
        self._inject_retry.reset()      # a new game process gets a new budget
        self._stop_locate()
        # Wait for locate worker to finish before continuing shutdown
        if self._worker is not None:
            if self._worker.isRunning():
                self._worker.stop()
            try:
                self._worker.done.disconnect()
            except Exception:
                pass
            # A timed-out stop() leaves the thread running; keep it alive until
            # it finishes instead of freeing a live QThread (workers.retire).
            _retire_worker(self._worker)
            self._worker = None
        self.detaching.emit()              # panel closes overlays + clears their model
        # The hook DLL writes farever_dps.log next to Farever.exe while it
        # runs. The game is (usually) gone by detach time, so the file is
        # unlocked — remove it so nothing of ours lingers in the game folder.
        # Best-effort: a still-running game keeps it locked and it stays.
        try:
            from ..core.proc import cleanup_game_dir
            cleanup_game_dir(include_proxies=False, log_line=self.log.emit)
        except Exception:
            pass
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