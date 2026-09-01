"""Application entry point: QApplication + theme + fonts + control panel."""
from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6 import QtWidgets, QtGui, QtCore

from .config import Settings, find_backup_files
from .master_backup import check_for_data_loss, create_snapshot_if_changed
from .ui import theme
from .ui.backup_scan import BackupScanDialog
from .ui.copy_menu import install_copy_menu
from .ui.control_panel import ControlPanel
from .data import icons
from . import paths


_PROBE = bool(os.environ.get("FAREVER_SCREENSHOT", "").strip()
             or os.environ.get("FAREVER_SCREENSHOT_LOG", "").strip())

# Qt logs "QFont::setPointSize: Point size <= 0 (-1), must be greater than 0"
# when its internal font resolution meets a stylesheet pixel font — the app
# QSS sets font-size in px, so every widget font has pointSize -1. No app
# code passes a non-positive point size (all setPointSize calls use positive
# constants); this is a cosmetic Qt-internal warning that fires on some
# Windows font-resolution paths. Drop exactly that message and forward
# everything else to the previous handler.
_prev_msg_handler = None


def _filter_cosmetic_font_warning(msg_type, context, msg) -> None:
    if msg_type == QtCore.QtMsgType.QtWarningMsg \
            and "QFont::setPointSize" in msg \
            and "Point size <= 0" in msg:
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
    # opt out for tooling/CI runs.
    _lock_guard: QtCore.QLockFile | None = None
    if os.environ.get("FAREVER_ALLOW_MULTI", "").strip().lower() \
            not in ("1", "true", "yes"):
        status, _lock_guard = _instance_lock()
        if status == "conflict":
            return _warn_conflict()
    # silence the cosmetic Qt-internal pixel-font warning (see above); the
    # installed handler chain stays active for the whole app run
    global _prev_msg_handler
    _prev_msg_handler = QtCore.qInstallMessageHandler(
        _filter_cosmetic_font_warning)
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
