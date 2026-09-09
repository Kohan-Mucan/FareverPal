"""In-app self-updater UI workflow, download worker, and relaunch coordinator."""
from __future__ import annotations

from PySide6 import QtWidgets
from ..core import updater
from .workers import CallWorker, retire
from .. import __version__


def update_pill_text(info, suffix: str = "") -> str:
    """The sidebar pill's label for this release, or "" when it has none.

    Built from the ONE field that can be trusted - a version the updater itself
    validated (`updater.clean_version`) - because the text is drawn verbatim in
    the sidebar. "" means "do not show this at all": a pill reading "Update to v",
    or showing a raw payload string, is the misleading button this guard exists
    to prevent. Every label in the update flow is built here, the retry label
    included.
    """
    ver = updater.clean_version(getattr(info, "version", ""))
    return f"↑  Update to v{ver}{suffix}" if ver else ""


class UpdaterMixin:
    """Mixin providing self-update notification pill and interactive install dialog."""

    def _start_update_check(self):
        """Ask the release oracle once, on a worker thread; a newer release
        reaches `_on_update_found` and raises the sidebar pill.

        Idempotent, like the DPS drain: a second call while a check is in
        flight does nothing. Off the Qt thread because `updater.check` waits on
        the network; `ControlPanel` starts it for the packaged exe only.
        """
        if getattr(self, "_update_check_worker", None) is not None:
            return
        self._update_check_worker = CallWorker(lambda _w: updater.check())
        self._update_check_worker.done.connect(self._on_update_checked)
        self._update_check_worker.start()

    def _on_update_checked(self, _tag, info):
        """Worker-thread handoff for the startup check: a bound method so the
        result is delivered on the Qt thread, not the worker's (see
        `game_attach` for the same `CallWorker` idiom).

        The finished worker is retired and the slot cleared, so the guard in
        `_start_update_check` is really "while a check is in flight" and a
        later caller (Settings -> Dev picking a polling mode) starts a fresh
        check instead of a silent no-op. `retire` keeps the QThread referenced
        until its thread has actually finished: dropping the last reference
        inside the worker's own `done` handler is the still-running-QThread
        destruction `workers.retire` exists to prevent.
        """
        retire(self._update_check_worker)
        self._update_check_worker = None
        self._on_update_found(info)

    def _on_update_found(self, info):
        """Raise the sidebar pill for a release, or show NOTHING at all.

        `updater.check` normalizes what the oracle sends, but this is the
        WIDGET's own contract and the last gate before text lands on screen: a
        caller handing over a release with no displayable version (a hand-built
        UpdateInfo, a future code path) leaves the button hidden rather than
        painting "Update to v" over a live sidebar, and no state is recorded for
        it - so `_on_update_clicked` cannot act on a release nobody can name.
        """
        if info is None:
            return
        text = update_pill_text(info)
        if not text:
            return
        self._update_info = info
        self._update_btn.setText(text)
        self._update_btn.setEnabled(True)
        self._update_btn.setVisible(True)
        self.log(f"Update available: v{__version__} → "
                 f"v{updater.clean_version(info.version)}. "
                 "Click the sidebar button to install.")

    def _on_update_clicked(self):
        info = getattr(self, "_update_info", None)
        if info is None or getattr(self, "_updating", False):
            return
        if not updater.is_frozen():
            import webbrowser
            # `page_url`, never `info.html_url` raw: the payload does not get to
            # choose a `javascript:` or `file:` target for a button we drew.
            webbrowser.open(updater.page_url(info.html_url))
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

        # The previous download worker is retired before the slot is
        # reassigned: a retry after a failed download would otherwise drop the
        # last reference to a QThread whose thread may still be winding down
        # (see workers.retire).
        retire(self._update_dl_worker)
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
            retry = update_pill_text(self._update_info, " (retry)")
            if retry:
                self._update_btn.setText(retry)
            else:
                # No version to name: the pill must not sit there reading
                # something else ("Downloading..." with nothing downloading).
                self._update_btn.setVisible(False)
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
