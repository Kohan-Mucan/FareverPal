"""Startup scan for preserved *.bak files in moddata.

When a corrupt settings/collection/profile file is moved aside to *.bak, this
dialog lists every preserved backup at launch and offers to restore it (the
copy replaces the current file), inspect its raw contents, or open the moddata
folder. Shown BEFORE Settings.load() so a restored settings.json or
collection.json is picked up by the normal load path.
"""
from __future__ import annotations

import difflib
import json
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from . import theme
from ..backup_summary import summarize
from ..config import config_dir
from ..persist import (
    delete_backup_file,
    preserve_file_aside,
    restore_backup_file,
)


def _human_size(n: int) -> str:
    for unit in ("B", "KB", "MB"):
        if n < 1024:
            return f"{n:.0f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def _row_text(p: Path) -> str:
    try:
        st = p.stat()
        size = _human_size(st.st_size)
        mtime = QtCore.QDateTime.fromSecsSinceEpoch(
            int(st.st_mtime)).toString("yyyy-MM-dd HH:mm")
    except OSError:
        size, mtime = "?", "?"
    return f"{p.name}   ·   {size}   ·   {mtime}"


def _restore_with_confirm(bak: Path, parent) -> tuple[Path | None, Path | None]:
    """Run the full restore flow for `bak`: overwrite warning, corrupt-JSON
    warning, preserve the current file aside, then restore.

    Returns (restored_target, preserved_path) or (None, None) when the user
    cancels or the restore fails. Shared by the main dialog and the Inspect
    dialog so both offer the exact same restore.
    """
    target = bak.with_suffix("")
    # The current file may hold newer data (e.g. re-login after the original
    # was corrupted); restoring overwrites it, so warn first.
    if target.exists():
        ret = QtWidgets.QMessageBox.warning(
            parent, "Replace current file?",
            f"Restoring {target.name} from its corrupted copy replaces the "
            "current file. The current version is saved as a timestamped "
            "backup first, so nothing is lost.\n\nRestore anyway?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.Cancel,
            QtWidgets.QMessageBox.Cancel)
        if ret != QtWidgets.QMessageBox.Yes:
            return None, None
    # A .json backup that doesn't parse would just be re-backed-up on the
    # next load; ask before restoring something unusable.
    if bak.name.endswith(".json.bak"):
        try:
            json.loads(bak.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            ret = QtWidgets.QMessageBox.warning(
                parent, "Backup looks corrupt",
                f"{bak.name} doesn't look like valid JSON, so restoring it "
                "may not recover anything usable.\n\nRestore anyway?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.Cancel,
                QtWidgets.QMessageBox.Cancel)
            if ret != QtWidgets.QMessageBox.Yes:
                return None, None
    # Nothing is ever destroyed: keep the current version aside (timestamped)
    # before the restore overwrites it.
    preserved = None
    if target.exists():
        preserved = preserve_file_aside(target, config_dir() / "cache" / "restore")
    target = restore_backup_file(bak)
    if target is None:
        QtWidgets.QMessageBox.warning(parent, "Restore failed",
                                      f"Could not restore {bak.name}.")
        return None, None
    return target, preserved


class BackupScanDialog(QtWidgets.QDialog):
    """Lists preserved *.bak files; the user can restore, delete, or inspect
    each one."""

    def __init__(self, baks: list[Path], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Restore corrupted files")
        self.setObjectName("Card")
        self.setMinimumWidth(520)

        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(22, 20, 22, 20)
        v.setSpacing(12)

        head = QtWidgets.QLabel("Restore corrupted files")
        head.setObjectName("H1")
        v.addWidget(head)
        sub = QtWidgets.QLabel(
            "Some files in your moddata folder were corrupted and moved aside "
            "so they couldn't be overwritten. Restore one to put it back in "
            "place, inspect it first, or delete it.")
        sub.setObjectName("Muted")
        sub.setWordWrap(True)
        v.addWidget(sub)

        self.list = QtWidgets.QListWidget()
        self.list.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.list.setMinimumHeight(180)
        self.list.setStyleSheet(
            f"QListWidget {{ background:{theme.PANEL}; border:1px solid {theme.BORDER}; "
            f"border-radius:4px; }}"
            f"QListWidget::item {{ padding:4px 6px; color:{theme.TEXT}; }}"
            f"QListWidget::item:hover {{ background:{theme.PANEL_HI}; }}"
            # :selected must win over :hover (hover after selected would
            # darken the active row), so the selected row stays accent-blue.
            f"QListWidget::item:selected {{ background:{theme.ACCENT_DIM}; "
            f"color:{theme.TEXT}; }}"
            f"QListWidget::item:selected:hover {{ background:{theme.ACCENT_DIM}; }}")
        for bak in baks:
            item = QtWidgets.QListWidgetItem(_row_text(bak))
            item.setData(QtCore.Qt.UserRole, bak)
            self.list.addItem(item)
        self.list.currentRowChanged.connect(self._on_selection)
        v.addWidget(self.list)

        self.empty_lbl = QtWidgets.QLabel("All corrupted files handled — "
                                          "nothing left to restore.")
        self.empty_lbl.setObjectName("Muted")
        self.empty_lbl.setWordWrap(True)
        self.empty_lbl.hide()
        v.addWidget(self.empty_lbl)

        self.status_lbl = QtWidgets.QLabel("")
        self.status_lbl.setObjectName("Muted")
        self.status_lbl.setWordWrap(True)
        self.status_lbl.hide()
        v.addWidget(self.status_lbl)

        self.btn_inspect = QtWidgets.QPushButton("Inspect")
        self.btn_inspect.setObjectName("Outline")
        self.btn_inspect.clicked.connect(self._inspect_selected)
        self.btn_restore = QtWidgets.QPushButton("Restore")
        self.btn_restore.setObjectName("Accent")
        self.btn_restore.setMinimumHeight(34)
        self.btn_restore.clicked.connect(self._restore_selected)
        self.btn_delete = QtWidgets.QPushButton("Delete")
        self.btn_delete.setObjectName("Outline")
        self.btn_delete.setStyleSheet(
            f"QPushButton{{color:{theme.DANGER};}}"
            f"QPushButton:hover{{color:{theme.DANGER};background:{theme.PANEL_HI};}}")
        self.btn_delete.clicked.connect(self._delete_selected)
        self.btn_folder = QtWidgets.QPushButton("Open folder")
        self.btn_folder.setObjectName("Outline")
        self.btn_folder.clicked.connect(self._open_folder)
        self.btn_close = QtWidgets.QPushButton("Close")
        self.btn_close.setObjectName("Outline")
        self.btn_close.clicked.connect(self.accept)

        row = QtWidgets.QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(self.btn_inspect)
        row.addWidget(self.btn_restore)
        row.addWidget(self.btn_delete)
        row.addStretch(1)
        row.addWidget(self.btn_folder)
        row.addWidget(self.btn_close)
        v.addLayout(row)

        if self.list.count():
            self.list.setCurrentRow(0)
        else:
            self._set_empty()

        # Size the window ONCE here, with headroom for the states that appear
        # later (status line + empty label after a restore/delete). The dialog
        # must never resize itself after this: a mid-modal resize is what made
        # Qt log "QWindowsWindow::setGeometry" AND ghost the button row (the
        # "doubled buttons"). With the headroom, later content changes fit
        # inside the existing window and Qt never has to fight.
        min_size = self.layout().totalMinimumSize()
        self.resize(min_size.width(), min_size.height() + 100)

    # --- actions ----------------------------------------------------------
    def _selected_bak(self) -> Path | None:
        item = self.list.currentItem()
        if item is None:
            return None
        return item.data(QtCore.Qt.UserRole)

    def _on_selection(self, _row: int):
        self.btn_inspect.setEnabled(self.list.count() > 0)
        self.btn_restore.setEnabled(self.list.count() > 0)
        self.btn_delete.setEnabled(self.list.count() > 0)

    def _set_empty(self):
        self.empty_lbl.show()
        self.btn_inspect.setEnabled(False)
        self.btn_restore.setEnabled(False)
        self.btn_delete.setEnabled(False)
        # No resize here — the window was built with headroom for this state.

    def _show_status(self, text: str):
        """Small confirmation line shown after a restore/delete."""
        self.status_lbl.setText(text)
        self.status_lbl.show()
        # No resize here — the window was built with headroom for this state.

    def _restore_selected(self):
        bak = self._selected_bak()
        if bak is None:
            return
        target, preserved = _restore_with_confirm(bak, self)
        if target is None:
            return
        self._note_restored(target, preserved)
        self._remove_row_for(bak)

    def _delete_selected(self):
        bak = self._selected_bak()
        if bak is None:
            return
        # The .bak is often the ONLY remaining copy of the user's data (the
        # original failed to parse), so deleting is permanent — confirm hard.
        ret = QtWidgets.QMessageBox.warning(
            self, "Delete backup?",
            f"{bak.name} may be the only preserved copy of that data. "
            "Deleting it permanently loses any settings or profile data it "
            "contains — it cannot be recovered.\n\nDelete anyway?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.Cancel,
            QtWidgets.QMessageBox.Cancel)
        if ret != QtWidgets.QMessageBox.Yes:
            return
        if not delete_backup_file(bak):
            QtWidgets.QMessageBox.warning(self, "Delete failed",
                                          f"Could not delete {bak.name}.")
            return
        self._show_status(f"Deleted: {bak.name}.")
        self._remove_row_for(bak)

    def _note_restored(self, target: Path, preserved: Path | None):
        """Status line after a successful restore — shared by the main list
        and the Inspect dialog."""
        kept = " Previous version kept in cache/restore/." if preserved else ""
        if target.name in ("settings.json", "collection.json"):
            # These are read by Settings.load() right after this dialog, so a
            # restored file is applied on THIS launch — no restart needed. If
            # the startup order ever changes (load before the scan), re-add a
            # relaunch prompt here.
            self._show_status(f"Restored: {target.name} — will be applied "
                              f"on this launch.{kept}")
        else:
            # profile files are read lazily later, so the restored data shows
            # up whenever that profile is next used.
            self._show_status(f"Restored: {target.name}.{kept}")

    def _remove_row_for(self, bak: Path):
        """Drop the list row for `bak` and fix up the empty/selection state."""
        row = -1
        for i in range(self.list.count()):
            if self.list.item(i).data(QtCore.Qt.UserRole) == bak:
                row = i
                self.list.takeItem(i)
                break
        if self.list.count() == 0:
            self._set_empty()
        elif row >= 0:
            self.list.setCurrentRow(min(row, self.list.count() - 1))

    def _inspect_selected(self):
        bak = self._selected_bak()
        if bak is None:
            return
        dlg = InspectDialog(bak, self)
        dlg.backup_deleted.connect(self._on_backup_deleted)
        dlg.backup_restored.connect(self._on_backup_restored)
        dlg.exec()

    def _on_backup_deleted(self, bak: Path):
        """A corrupted file was deleted from inside the Inspect dialog:
        drop it from the list and update the status/empty state."""
        self._show_status(f"Deleted: {bak.name}.")
        self._remove_row_for(bak)

    def _on_backup_restored(self, bak: Path, target: Path,
                            preserved: Path | None):
        """A corrupted file was restored from inside the Inspect dialog:
        drop it from the list and note the restore."""
        self._note_restored(target, preserved)
        # Use the real .bak path from the signal — reconstructing it from the
        # target (with_suffix) would turn settings.json.bak into settings.bak.
        self._remove_row_for(bak)

    def _open_folder(self):
        QtGui.QDesktopServices.openUrl(
            QtCore.QUrl.fromLocalFile(str(config_dir())))


# Line-diff tints: identical lines read yellow on both panes; differing
# lines read green on the backup pane (what restore brings back) and red on
# the current pane (what restore overwrites).
_SAME_TINT = QtGui.QColor(255, 200, 0, 42)
_BACKUP_TINT = QtGui.QColor(80, 210, 120, 70)
_CURRENT_TINT = QtGui.QColor(255, 90, 90, 70)

# Solid versions for the legend chips (the pane tints are translucent).
_LEGEND_SAME = "#d4a017"
_LEGEND_BACKUP = "#4ade80"
_LEGEND_CURRENT = "#f87171"


def _tint_diff(pane: QtWidgets.QPlainTextEdit, text: str, other: str,
               diff_color: QtGui.QColor) -> None:
    """Tint the lines of `pane` against `other` (line-based diff): identical
    lines yellow, differing lines in `diff_color`."""
    a, b = text.splitlines(), other.splitlines()
    selections = []
    for tag, i1, i2, _j1, _j2 in difflib.SequenceMatcher(None, a, b).get_opcodes():
        color = _SAME_TINT if tag == "equal" else diff_color
        for ln in range(i1, i2):
            block = pane.document().findBlockByNumber(ln)
            if not block.isValid():
                continue
            es = QtWidgets.QTextEdit.ExtraSelection()
            es.format.setBackground(color)
            # Select the whole line explicitly: a cursor built from a block
            # alone has no selection range, so the tint would never paint.
            cur = QtGui.QTextCursor(pane.document())
            cur.setPosition(block.position())
            cur.setPosition(block.position() + block.length() - 1,
                             QtGui.QTextCursor.KeepAnchor)
            es.cursor = cur
            selections.append(es)
    if selections:
        pane.setExtraSelections(selections)


class InspectDialog(QtWidgets.QDialog):
    """Side-by-side view of a corrupted file vs the current file (when one
    exists), with a one-line summary of each above the panes and Restore /
    Delete actions for the corrupted file."""

    backup_deleted = QtCore.Signal(Path)            # emitted after the *.bak is removed
    backup_restored = QtCore.Signal(object, object, object)  # (bak, target, preserved)

    def __init__(self, bak: Path, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"{bak.name} — corrupted file contents")
        self.setObjectName("Card")
        self.resize(780, 480)

        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(16, 16, 16, 16)
        v.setSpacing(10)

        target = bak.with_suffix("")
        try:
            bak_text = bak.read_text(encoding="utf-8", errors="replace")
        except OSError:
            bak_text = ""
        try:
            cur_text = (target.read_text(encoding="utf-8", errors="replace")
                        if target.exists() else None)
        except OSError:
            cur_text = None

        if cur_text is not None:
            head = QtWidgets.QLabel(
                f"<b>Corrupted:</b> {summarize(bak.name, bak_text)}<br>"
                f"<b>Current:</b> {summarize(target.name, cur_text)}<br>"
                "<b>Restore</b> puts the corrupted file's contents back over "
                "the current file.")
        else:
            head = QtWidgets.QLabel(
                f"<b>Corrupted:</b> {summarize(bak.name, bak_text)}<br>"
                "<b>Current:</b> (none)<br>"
                "<b>Restore</b> puts the corrupted file's contents back in "
                "place.")
        head.setObjectName("Muted")
        head.setTextFormat(QtCore.Qt.RichText)
        head.setWordWrap(True)
        v.addWidget(head)

        if cur_text is not None:
            v.addWidget(self._legend())
            split = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
            split.addWidget(self._pane("Corrupted file — will be restored",
                                       bak_text, cur_text, _BACKUP_TINT))
            split.addWidget(self._pane("Current — will be replaced",
                                       cur_text, bak_text, _CURRENT_TINT))
            split.setSizes([380, 380])
            v.addWidget(split, 1)
        else:
            v.addWidget(self._pane("Corrupted file — will be restored",
                                   bak_text, None, _BACKUP_TINT), 1)

        row = QtWidgets.QHBoxLayout()
        row.setSpacing(8)
        btn_restore = QtWidgets.QPushButton("Restore corrupted file")
        btn_restore.setObjectName("Accent")
        btn_restore.setMinimumHeight(34)
        btn_restore.clicked.connect(lambda: self._restore(bak))
        row.addWidget(btn_restore)
        btn_delete = QtWidgets.QPushButton("Delete corrupted file")
        btn_delete.setObjectName("Outline")
        btn_delete.setStyleSheet(
            f"QPushButton{{color:{theme.DANGER};}}"
            f"QPushButton:hover{{color:{theme.DANGER};background:{theme.PANEL_HI};}}")
        btn_delete.clicked.connect(lambda: self._delete(bak))
        row.addWidget(btn_delete)
        row.addStretch(1)
        close = QtWidgets.QPushButton("Close")
        close.setObjectName("Outline")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        v.addLayout(row)

        # Same as the main dialog: never request less than the layout minimum,
        # or Qt logs the setGeometry warning and resizes the window anyway.
        self.resize(max(self.minimumSizeHint().width(), 780),
                    max(self.minimumSizeHint().height(), 480))

    def _restore(self, bak: Path):
        """Restore the corrupted file right here (same warnings + preservation
        as the main dialog), then tell the parent dialog to drop the row."""
        target, preserved = _restore_with_confirm(bak, self)
        if target is None:
            return
        self.backup_restored.emit(bak, target, preserved)
        self.accept()

    def _delete(self, bak: Path):
        """Delete the corrupted file (same permanent-loss confirmation as the
        main dialog), then tell the parent dialog to drop the row."""
        ret = QtWidgets.QMessageBox.warning(
            self, "Delete corrupted file?",
            f"{bak.name} may be the only preserved copy of that data. "
            "Deleting it permanently loses any settings or profile data it "
            "contains — it cannot be recovered.\n\nDelete anyway?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.Cancel,
            QtWidgets.QMessageBox.Cancel)
        if ret != QtWidgets.QMessageBox.Yes:
            return
        if not delete_backup_file(bak):
            QtWidgets.QMessageBox.warning(self, "Delete failed",
                                          f"Could not delete {bak.name}.")
            return
        self.backup_deleted.emit(bak)
        self.accept()

    def _pane(self, title: str, text: str, other: str | None,
              diff_color: QtGui.QColor) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        lbl = QtWidgets.QLabel(title)
        lbl.setObjectName("Muted")
        lay.addWidget(lbl)
        edit = QtWidgets.QPlainTextEdit()
        edit.setReadOnly(True)
        edit.setPlainText(text)
        lay.addWidget(edit, 1)
        if other is not None:
            _tint_diff(edit, text, other, diff_color)
        return w

    def _legend(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        lay = QtWidgets.QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        for color, text in ((_LEGEND_SAME, "same in both"),
                            (_LEGEND_BACKUP, "only in corrupted file — comes back"),
                            (_LEGEND_CURRENT, "only in current — lost")):
            item = QtWidgets.QWidget()
            h = QtWidgets.QHBoxLayout(item)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(4)
            chip = QtWidgets.QLabel()
            chip.setFixedSize(14, 14)
            chip.setStyleSheet(f"background:{color};border-radius:2px;")
            lbl = QtWidgets.QLabel(text)
            lbl.setObjectName("Muted")
            h.addWidget(chip)
            h.addWidget(lbl)
            lay.addWidget(item)
        lay.addStretch(1)
        return w
