"""Application entry point: QApplication + theme + fonts + control panel."""
from __future__ import annotations

import sys

from PySide6 import QtWidgets, QtGui

from .config import Settings, find_backup_files
from .ui import theme
from .ui.backup_scan import BackupScanDialog
from .ui.control_panel import ControlPanel
from .data import icons
from . import paths


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
    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName("Farever Pal")
    app.setOrganizationName("FareverPal")
    theme.apply(app)                 # loads bundled fonts + installs QSS
    app.setWindowIcon(_app_icon())

    # Startup scan: files moved aside to *.bak after a corrupt read are listed
    # here BEFORE settings load, so a restored settings.json / collection.json
    # is picked up by the normal load path — no restart needed.
    baks = find_backup_files()
    if baks:
        BackupScanDialog(baks).exec()

    settings = Settings.load()
    # apply the saved Highlight color app-wide before building the UI
    if settings.hud_accent:
        theme.set_accent(settings.hud_accent)
        app.setStyleSheet(theme.QSS)
    win = ControlPanel(settings)
    win.setWindowIcon(_app_icon())
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
