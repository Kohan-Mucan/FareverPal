"""Application entry point: QApplication + theme + fonts + control panel."""
from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6 import QtWidgets, QtGui, QtCore

from .config import Settings, audit_settings_event, find_backup_files
from .backup.master import check_for_data_loss, create_snapshot_if_changed
from .ui import theme
from .ui.backup_scan import BackupScanDialog
from .ui.copy_menu import install_copy_menu
from .ui.control_panel import ControlPanel
from .data import icons
from . import paths


# Session lifecycle audit (settings_audit.log): a CLEAN exit writes a
# `shutdown` line for its pid, a crash writes `crash` via the excepthook.
# The next launch then reports `prev-session=<state>` — so a HARD kill
# (closed console window, power loss, hard crash with no excepthook) is
# visible as the *absence* of a shutdown line for the previous pid. This
# is what answered "did you close my app?" on 2026-09-19: a leftover lock
# file named the pid, but only a missing shutdown line proves the kill.
_SESSION_AUDITED = False


def _audit_session_end(event: str, detail: str = "") -> None:
    """Write one end-of-session audit line for this pid (once per event)."""
    global _SESSION_AUDITED
    if event != "crash" and _SESSION_AUDITED:
        return          # shutdown already written; a crash after it is moot
    try:
        audit_settings_event(event, None, f"session=end {detail}".strip())
        _SESSION_AUDITED = True
    except Exception:
        pass


def _write_dev_crash(text: str) -> None:
    """Append an uncaught Python exception to the opt-in developer log.

    Stamped like run.py's writer: faulthandler dumps (the access-violation
    traces) have no timestamp, so every entry this file gains needs its own
    or the order of multiple crashes is unknowable.

    Routed through ``crash_log``'s single writer rather than opening the path
    here: this, run.py and the session marker were three text-mode handles
    beside faulthandler's descriptor and the VEH's, which is why run.log mixed
    ``\r\n`` in among ``\n`` entries. The label stays distinct so an uncaught
    exception is still distinguishable from a startup failure.
    """
    path = os.environ.get("FAREVER_DEV_CRASH_LOG", "").strip()
    if not path:
        return
    try:
        from .runtime import crash_log
        crash_log.write_python_exception(
            path, text, label="Uncaught Python exception")
    except Exception:
        pass


def _install_crash_audithook() -> None:
    """Route uncaught exceptions through the crash audit before Qt's default
    abort path. Chained: the previous excepthook still runs."""
    prev = sys.excepthook

    def _hook(etype, value, tb) -> None:
        import traceback
        text = "".join(traceback.format_exception(etype, value, tb))
        _write_dev_crash(text)
        top = "".join(traceback.format_exception_only(etype, value)).strip()
        _audit_session_end("crash", f"{etype.__name__}: {top[:120]}")
        if prev is not None:
            prev(etype, value, tb)

    sys.excepthook = _hook


def _prev_session_end_state() -> str:
    """How the PREVIOUS session ended: "clean", "unclean", or "unknown".

    The previous session is the pid with the latest `startup` line (the
    definitive session opener; `launch` also fires for refused instances,
    so it does not open a session). That session ended cleanly iff a
    `shutdown` or `crash` line exists for the same pid after its startup.
    A pid with startup but no end line = hard-killed (unclean). Never raises.
    """
    try:
        # the definition site, not the package hub: the audit writer in
        # config.store resolves the same name in ITS namespace, so one patch
        # seam (tests/test_guards.py) covers reader and writer.
        from .config.store import _audit_path
        p = _audit_path()
        if not p.exists():
            return "unknown"
        my_pid = os.getpid()
        last_startup_pid: int | None = None
        ended: set[int] = set()
        for raw in p.read_text(encoding="utf-8", errors="replace").splitlines():
            if "pid=" not in raw:
                continue
            try:
                pid = int(raw.split("pid=")[1].split()[0])
            except (ValueError, IndexError):
                continue
            # Line shape: "<ts> pid=<pid> <event> ..." — the event token is
            # the SECOND field after "pid=" (the first is the pid itself).
            rest = raw.split("pid=")[1].split()
            ev = rest[1] if len(rest) > 1 else ""
            if ev == "startup":
                last_startup_pid = pid
            elif ev in ("shutdown", "crash"):
                ended.add(pid)
        if last_startup_pid is None or last_startup_pid == my_pid:
            return "unknown"
        return "clean" if last_startup_pid in ended else "unclean"
    except Exception:
        return "unknown"


_PROBE = bool(os.environ.get("FAREVER_SCREENSHOT", "").strip()
             or os.environ.get("FAREVER_SCREENSHOT_LOG", "").strip())

# Qt logs "QFont::setPointSize: Point size <= 0 (-1), must be greater than 0"
# when its internal font resolution meets a stylesheet pixel font — the app
# QSS sets font-size in px, so every widget font has pointSize -1. No app
# code passes a non-positive point size (all setPointSize calls use positive
# constants); this is a cosmetic Qt-internal warning that fires on some
# Windows font-resolution paths.
# Windows also logs "QWindowsWindow::setGeometry: Unable to set geometry ..."
# when a top-level window (typically a QMessageBox) races its layout minimum
# on first show with a requested client size smaller than the enforced
# minimum: the Win32 window manager clamps it (see the message's own
# "Resulting geometry" line) and the window displays fine. No app code
# requests an undersize geometry — every explicit resize() is clamped to
# minimumSize/minimumSizeHint first — so this is the same class of cosmetic
# Qt-internal noise. Drop exactly those two messages and forward everything
# else to the previous handler.
_prev_msg_handler = None


def _filter_cosmetic_qt_warnings(msg_type, context, msg) -> None:
    if msg_type == QtCore.QtMsgType.QtWarningMsg:
        if "QFont::setPointSize" in msg \
                and "Point size <= 0" in msg:
            return
        if "QWindowsWindow::setGeometry" in msg \
                and "Unable to set geometry" in msg:
            return
    if _prev_msg_handler is not None:
        _prev_msg_handler(msg_type, context, msg)
    else:
        # mirror the default handler: warnings go to stderr
        sys.stderr.write(msg + "\n")
        sys.stderr.flush()


def _probe(msg: str) -> None:
    """Startup-stage trace, only when the headless screenshot hook is active:
    pinpoints where a frozen run hangs when the render never lands."""
    if _PROBE:
        _emit(f"[probe] {msg}")


def _app_icon() -> QtGui.QIcon:
    """Window / taskbar icon. Prefers a user-supplied logo
    (`assets/app_icon.png`/`.ico`); otherwise falls back to the tinted brand
    glyph so the app always has a recognizable mark."""
    for name in ("app_icon.ico", "app_icon.png"):
        p = paths.assets_dir() / name
        if p.exists():
            return QtGui.QIcon(str(p))
    ic = QtGui.QIcon()
    for sz in (16, 24, 32, 48, 64, 128, 256):
        ic.addPixmap(icons.ui_icon("radio", theme.ACCENT, sz))
    return ic


def _instance_lock() -> tuple[str, QtCore.QLockFile | None]:
    """Single-instance guard for the data folder.

    collection.json / settings.json / progress_*.json are rewritten wholesale
    from in-memory state, so two app instances on the same moddata folder can
    wipe each other's data (the recurring collection-loss incidents). Returns
    ("ok", lock) when this launch holds the lock (keep `lock` referenced for
    the app's lifetime), ("conflict", None) when another live instance holds
    it, or ("skip", None) when locking is unavailable — startup must never be
    bricked by an unwritable data folder.

    Detects orphaned locks left by crashes by checking the locking PID's
    aliveness before declaring a conflict.
    """
    try:
        from .config import config_dir
    except Exception:
        return "skip", None
    try:
        lock = QtCore.QLockFile(str(config_dir() / "fareverpal.lock"))
        # In Qt, setStaleLockTime(0) means 'never consider stale', which broke
        # crash recovery. Default 30s + active PID verification handles orphaned locks.
        lock.setStaleLockTime(30000)
        if lock.tryLock(0):
            return "ok", lock
        err = lock.error()
        if err == QtCore.QLockFile.PermissionError:
            return "skip", None
        if err == QtCore.QLockFile.LockFailedError:
            # Check if the process holding the lock is dead (orphaned crash lock)
            try:
                owner_info = lock.getLockInfo()
                pid = owner_info[0] if owner_info else 0
                if pid > 0:
                    import ctypes
                    SYNCHRONIZE = 0x00100000
                    h = ctypes.windll.kernel32.OpenProcess(SYNCHRONIZE, False, pid)
                    if not h:
                        # Owner process is dead — remove orphaned lock and claim
                        lock.removeStaleLockFile()
                        if lock.tryLock(0):
                            return "ok", lock
                    else:
                        ctypes.windll.kernel32.CloseHandle(h)
            except Exception:
                pass
        return "conflict", None
    except Exception:
        return "skip", None


def _audit_launch(outcome: str) -> None:
    """One forensic line per launch (see settings_audit.log): how this
    process got past the single-instance guard.

    The refused ("conflict") case is the valuable one — it is direct
    evidence of the second-writer hazard this guard exists to prevent, so
    every outcome is audited, not just the ones that reach startup.
    """
    try:
        audit_settings_event("launch", None, f"lock={outcome}")
    except Exception:
        pass


def _acquire_instance_guard() -> tuple[str, QtCore.QLockFile | None]:
    """Single-instance guard + forensic audit for one launch.

    Returns ("ok", lock) when this launch holds the data-folder lock (keep
    it referenced for the app's lifetime), ("conflict", None) when another
    live instance holds it (main() shows _warn_conflict and exits), or
    ("skip", None) when locking is unavailable. FAREVER_ALLOW_MULTI opts
    out entirely for tooling/CI runs (audited as bypassed-allow-multi).
    """
    allow_multi = os.environ.get("FAREVER_ALLOW_MULTI", "").strip().lower() \
        in ("1", "true", "yes")
    if allow_multi:
        _audit_launch("bypassed-allow-multi")
        return "ok", None
    status, lock = _instance_lock()
    if status == "conflict":
        _audit_launch("conflict")
        return status, None
    _audit_launch(status)          # "ok" or "skip"
    return status, lock


def _focus_running_instance() -> int:
    """A live instance owns this folder: raise it, say so, and exit.

    Preferred over _warn_conflict because the thing the user wants from a
    second launch is the app they already have. The warning still runs when no
    window can be found (the winner may be a headless/tooling run), so the
    refusal is never silent either way.
    """
    shown = False
    try:
        from .config import config_dir
        from .runtime.instance_focus import claim

        def _lost(guard) -> None:
            nonlocal shown
            shown = guard.focus_existing()

        claim(config_dir(), on_lost=_lost)
        if shown:
            print("[startup] another instance owns this data folder - "
                  "raised its window and exiting", flush=True)
            return 0
    except Exception:
        pass
    return _warn_conflict()


def _warn_conflict(parent: QtWidgets.QWidget | None = None,
                    timeout_ms: int = 6000) -> int:
    """Warn about a second instance on the same data folder, then exit.

    The dialog auto-closes after ``timeout_ms`` (or on OK click), so a stray
    double-launch can never leave a parked 4 MB warning process behind -
    every loser of the single-instance race exits on its own."""
    box = QtWidgets.QMessageBox(QtWidgets.QMessageBox.Warning,
                                "Farever Pal is already running",
                                "Another Farever Pal instance is already "
                                "open on this data folder.\n\nTwo instances "
                                "writing the same settings can wipe your "
                                "collection and progress data.\n\nClose the "
                                "other instance and start again.",
                                parent=parent)
    box.setStandardButtons(QtWidgets.QMessageBox.Ok)
    QtCore.QTimer.singleShot(timeout_ms, box.close)   # never lingers
    box.exec()
    return 2


def main() -> int:
    _probe(f"app start argv={sys.argv} qpa={os.environ.get('QT_QPA_PLATFORM', '')}")
    app = QtWidgets.QApplication(sys.argv)
    _probe(f"qapp created platform={app.platformName()}")
    # Single-instance guard: refuse a second instance on the same data folder
    # (two writers = the data-loss hazard). Set FAREVER_ALLOW_MULTI=1 to
    # opt out for tooling/CI runs. Every outcome is audited as
    # `launch lock=<outcome>` so overlapping writers are visible in
    # settings_audit.log (ok / bypassed-allow-multi / conflict / skip).
    _lock_guard: QtCore.QLockFile | None = None
    status, _lock_guard = _acquire_instance_guard()
    if status == "conflict":
        # The lock file is the DATA guard and is authoritative about who owns
        # the folder. A named mutex says the same thing earlier and lets the
        # loser focus the winner, so try that hand-off first (it resolves to
        # this dialog when there is no window to raise).
        return _focus_running_instance()
    # silence the cosmetic Qt-internal pixel-font warning (see above); the
    # installed handler chain stays active for the whole app run
    global _prev_msg_handler
    _prev_msg_handler = QtCore.qInstallMessageHandler(
        _filter_cosmetic_qt_warnings)
    # Dev-only right-click Copy menu (element + text + parent chain for
    # pasting bug reports back verbatim); frozen builds skip it internally.
    install_copy_menu(app)
    app.setApplicationName("Farever Pal")
    app.setOrganizationName("FareverPal")
    theme.apply(app)                 # loads bundled fonts + installs QSS
    _probe("theme applied")
    app.setWindowIcon(_app_icon())

    # Startup recovery check:
    # 1. Any corrupt files moved aside to *.bak
    # 2. Any data loss (>20% loss or missing files) detected against Master Backup
    baks = find_backup_files()
    loss_info = check_for_data_loss()
    if baks or loss_info:
        BackupScanDialog(baks=baks, loss_info=loss_info).exec()

    settings = Settings.load()
    _probe("settings loaded")
    # File tripwire for out-of-app resets: record the raw settings.json hash
    # + mtime at startup. If a future launch shows a hash that no audited
    # save ever wrote, the file was swapped externally between sessions
    # (sync tool, AV, manual copy) — no in-app writer can explain that.
    try:
        import hashlib
        from .config import config_dir
        _sp = config_dir() / "settings.json"
        _raw = _sp.read_bytes()
        _mt = _sp.stat().st_mtime
        import datetime as _dt
        audit_settings_event(
            "startup", settings,
            f"prev-session={_prev_session_end_state()} "
            f"file={len(_raw)}B#{hashlib.md5(_raw).hexdigest()[:12]} "
            f"mtime={_dt.datetime.fromtimestamp(_mt).isoformat(timespec='seconds')}")
    except Exception:
        pass
    # apply the saved Highlight color app-wide before building the UI
    if settings.hud_accent:
        theme.set_accent(settings.hud_accent)
        app.setStyleSheet(theme.QSS)
    # Every work-thread registers itself; join them all before the app is torn
    # down (fires for window close AND programmatic quit) so no QThread is
    # destroyed while its thread is still running.
    from .ui.workers import shutdown_all
    app.aboutToQuit.connect(create_snapshot_if_changed)
    app.aboutToQuit.connect(shutdown_all)
    # Clean-exit audit: fires for window close AND programmatic quit. The
    # absence of this line for a pid is how the NEXT startup proves an
    # unclean kill (closed console, power loss, hard crash).
    app.aboutToQuit.connect(lambda: _audit_session_end("shutdown"))
    # Crash audit: any uncaught Python exception (a hard OS-level kill still
    # leaves NO line — which is itself the signal at the next startup).
    _install_crash_audithook()
    # Re-entrancy tripwire. ON by default: a layout or signal cycle
    # overflows the C stack below the interpreter, so the crash log for one
    # names nothing at all. FAREVER_REENTRY=off disables it, =abort turns
    # a would-be unlabelled crash into a labelled one. See
    # runtime/reentry.py.
    try:
        from .runtime import reentry
        reentry.install(app)
    except Exception as e:  # never let the watchdog break startup
        print(f"[reentry] watchdog not installed: {e}", file=sys.stderr,
              flush=True)
    win = ControlPanel(settings)
    _probe("control panel built")
    win.setWindowIcon(_app_icon())
    win.show()
    _probe("window shown")
    # dev-only idle-memory probe (ai/workspace/, never shipped): samples RSS +
    # tracemalloc growth into mem_probe_log.txt while the app runs.
    if os.environ.get("FAREVER_MEM_PROBE", "").strip():
        try:
            _install_mem_probe(win)
        except Exception as e:
            print(f"[mem-probe] failed to start: {e}", file=sys.stderr,
                  flush=True)
    # test hook: FAREVER_OPEN_PAGE=craft opens a page at startup so the
    # frozen exe can be smoke-tested headlessly (e.g. CI / build verify).
    # Adding FAREVER_SCREENSHOT=<path.png> dumps a PNG of the window after
    # the page paints, then exits 0/1 so the render can be verified visually.
    # Gear page extras: FAREVER_OPEN_ITEM=<item_id> selects a gear item
    # and FAREVER_OPEN_DRAWER=1 expands its first In Recipes drawer, so
    # that view can be captured too.
    page = os.environ.get("FAREVER_OPEN_PAGE", "").strip().lower()
    if page and page in win._pages:
        _probe(f"page '{page}' switched")
        win._select_nav(page)
        state_ok = _apply_open_page_state(win, page)
        _probe(f"page state applied ok={state_ok}")
        _schedule_screenshot(win, app, state_ok=state_ok)
    return app.exec()


def _install_mem_probe(win: QtWidgets.QWidget) -> None:
    """Load ai/workspace/opencode/mem_probe/mem_probe.py by path (same pattern
    as the optional dev_scanner) and start its sampler against the panel."""
    here = Path(__file__).resolve()
    candidates = [
        here.parents[1] / "ai" / "workspace" / "opencode" / "mem_probe"
        / "mem_probe.py",
        Path.cwd() / "ai" / "workspace" / "opencode" / "mem_probe"
        / "mem_probe.py",
    ]
    for p in candidates:
        if not p.exists():
            continue
        import importlib.util
        spec = importlib.util.spec_from_file_location("farever_mem_probe", p)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        probe_cls = getattr(mod, "install", None)
        if probe_cls is None:
            raise RuntimeError(f"{p} has no install()")
        win._mem_probe = probe_cls(win)    # keep a reference: no GC
        print(f"[mem-probe] active -> {p}", flush=True)
        return
    raise FileNotFoundError(
        "mem_probe.py not found (expected ai/workspace/opencode/mem_probe/)")


def _apply_open_page_state(win: QtWidgets.QWidget, page: str) -> bool:
    """Apply the optional FAREVER_OPEN_ITEM / FAREVER_OPEN_DRAWER state to
    the opened page before the screenshot fires. Returns False when a
    requested state couldn't be built, so the run still fails loudly even
    though the (partial) render is captured for debugging."""
    item = os.environ.get("FAREVER_OPEN_ITEM", "").strip()
    if page != "gear" or not item:
        return True
    driver = getattr(win, "_items_headless_open", None)
    if driver is None:
        _emit("[open-page] Items page driver not available", err=True)
        return False
    drawer = os.environ.get("FAREVER_OPEN_DRAWER", "").strip().lower() \
        in ("1", "true", "yes")
    _probe(f"items driver: item='{item}' drawer={drawer}")
    ok = bool(driver(item, drawer))
    if not ok:
        _emit(f"[open-page] FAILED to open item '{item}' "
              f"(drawer={drawer})", err=True)
    return ok


def _emit(line: str, err: bool = False) -> None:
    """Mirror a result line to the console when one exists (dev runs) and to
    FAREVER_SCREENSHOT_LOG when set (the frozen exe is windowed, so its
    stdout is invisible — the log file is the reliable channel there)."""
    lp = os.environ.get("FAREVER_SCREENSHOT_LOG", "").strip()
    if lp:
        try:
            with open(lp, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass
    try:
        (sys.stderr if err else sys.stdout).write(line + "\n")
    except Exception:
        pass


def _schedule_screenshot(win: QtWidgets.QWidget, app: QtWidgets.QApplication,
                         delay_ms: int = 500, state_ok: bool = True) -> None:
    """Dump FAREVER_SCREENSHOT after the opened page has painted, then quit.

    The window needs a few event-loop cycles before its first paint, so the
    grab runs on a timer instead of inline. Exits 0 on a successful write
    (and a fully-applied requested state) and 1 on any failure, which is
    what makes this usable as a headless render-check: CI / build verify
    can eyeball the PNG (or diff it) without a display.
    """
    shot = os.environ.get("FAREVER_SCREENSHOT", "").strip()
    if not shot:
        return
    _emit(f"[screenshot] scheduled in {delay_ms} ms -> {shot}")

    def _grab() -> None:
        try:
            pix = win.grab()
            if pix.isNull():
                _emit(f"[screenshot] FAILED: grab returned an empty image ({shot})",
                      err=True)
                os._exit(1)
            out = Path(shot)
            out.parent.mkdir(parents=True, exist_ok=True)
            if not pix.save(str(out)):
                _emit(f"[screenshot] FAILED to write {out}", err=True)
                os._exit(1)
            _emit(f"[screenshot] saved {out} "
                  f"({pix.width()}x{pix.height()}, {out.stat().st_size} B)")
            # hard exit: a frozen windowed exe can linger in interpreter
            # shutdown waiting on background threads — os._exit is deterministic
            os._exit(0 if state_ok else 1)
        except OSError as e:
            _emit(f"[screenshot] FAILED: {e}", err=True)
            os._exit(1)

    QtCore.QTimer.singleShot(delay_ms, _grab)


if __name__ == "__main__":
    sys.exit(main())
