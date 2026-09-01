"""In-app self-updater UI workflow, download worker, and relaunch coordinator."""
from __future__ import annotations

from PySide6 import QtWidgets
from ..core import updater
from .workers import CallWorker
from .. import __version__


class UpdaterMixin:
    """Mixin providing self-update notification pill and interactive install dialog."""

    def _on_update_found(self, info):
        if info is None:
            return
        self._update_info = info
        self._update_btn.setText(f"↑  Update to v{info.version}")
        self._update_btn.setEnabled(True)
        self._update_btn.setVisible(True)
        self.log(f"Update available: v{__version__} → v{info.version}. "
                 "Click the sidebar button to install.")

    def _on_update_clicked(self):
        info = getattr(self, "_update_info", None)
        if info is None or getattr(self, "_updating", False):
            return
        if not updater.is_frozen():
            import webbrowser
            webbrowser.open(info.html_url or f"{self.s.api_base}/download.php")
            return
        notes = (info.notes or "").strip()
        if len(notes) > 600:
            notes = notes[:600] + "…"
        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle("Update Farever Pal")
        box.setIcon(QtWidgets.QMessageBox.Question)
        box.setText(f"Update from v{__version__} to v{info.version}?\n\n"
                    "The app will download the new version, restart, and reconnect automatically.")
        if notes:
            box.setInformativeText(notes)
        box.setStandardButtons(QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No)
        box.setDefaultButton(QtWidgets.QMessageBox.Yes)
        if box.exec() != QtWidgets.QMessageBox.Yes:
            return
        self._updating = True
        self._update_btn.setEnabled(False)
        self._update_btn.setText("Downloading…  0%")
        self.log(f"Downloading v{info.version}…")

        def _download(w):
            cb = lambda d, t: w.progress.emit(int(d * 100 / t)) if t else None
            try:
                return updater.download_and_stage(info, cb)
            except Exception as e:
                return e

        self._update_dl_worker = CallWorker(_download)
        self._update_dl_worker.progress.connect(self._on_update_progress)
        self._update_dl_worker.done.connect(lambda _t, r: self._on_update_downloaded(r))
        self._update_dl_worker.start()

    def _on_update_progress(self, pct: int):
        self._update_btn.setText(f"Downloading…  {pct}%")

    def _on_update_downloaded(self, result):
        if isinstance(result, Exception):
            self._updating = False
            self._update_btn.setEnabled(True)
            self._update_btn.setText(f"↑  Update to v{self._update_info.version} (retry)")
            self.log(f"Update failed: {result}")
            QtWidgets.QMessageBox.warning(
                self, "Update failed",
                f"Couldn't install the update:\n{result}\n\n"
                "You can retry, or download it from the website.")
            return

        self.log(f"Installing v{self._update_info.version} and restarting…")
        try:
            self.detach()
            updater.apply_and_relaunch(result)
        except Exception as e:
            self._updating = False
            self._update_btn.setEnabled(True)
            self.log(f"Update install failed: {e}")
            QtWidgets.QMessageBox.warning(self, "Update failed", str(e))
            return
        QtWidgets.QApplication.quit()
