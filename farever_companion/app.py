"""Application entry point: QApplication + theme + fonts + control panel."""
from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6 import QtWidgets, QtGui, QtCore

from .config import Settings, find_backup_files
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


def main() -> int:
    _probe(f"app start argv={sys.argv} qpa={os.environ.get('QT_QPA_PLATFORM', '')}")
    app = QtWidgets.QApplication(sys.argv)
    _probe(f"qapp created platform={app.platformName()}")
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

    # Startup scan: files moved aside to *.bak after a corrupt read are listed
    # here BEFORE settings load, so a restored settings.json / collection.json
    # is picked up by the normal load path — no restart needed.
    baks = find_backup_files()
    if baks:
        BackupScanDialog(baks).exec()

    settings = Settings.load()
    _probe("settings loaded")
    # apply the saved Highlight color app-wide before building the UI
    if settings.hud_accent:
        theme.set_accent(settings.hud_accent)
        app.setStyleSheet(theme.QSS)
    win = ControlPanel(settings)
    _probe("control panel built")
    win.setWindowIcon(_app_icon())
    win.show()
    _probe("window shown")
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
