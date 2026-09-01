"""Startup scan and recovery dialog for preserved *.bak files and Master Backups.

When corrupted files or significant data loss (>20% drop / wipe) are detected,
this dialog allows the user to restore full master backup snapshots, specific
character profiles, or individual corrupted .bak files.
"""
from __future__ import annotations

import difflib
import json
from pathlib import Path
from typing import Any

from PySide6 import QtCore, QtGui, QtWidgets

from . import theme
from ..backup_summary import summarize, summarize_snapshot
from ..config import config_dir
from ..master_backup import (
    load_master_backup,
    master_backup_path,
    restore_from_snapshot,
    restore_missing_from_snapshot,
    accept_current_moddata_state,
    dismiss_profile_alert,
)
from ..persist import (
    atomic_write_json,
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


def _row_text_bak(p: Path) -> str:
    try:
        st = p.stat()
        size = _human_size(st.st_size)
        mtime = QtCore.QDateTime.fromSecsSinceEpoch(
            int(st.st_mtime)
        ).toString("yyyy-MM-dd HH:mm")
    except OSError:
        size, mtime = "?", "?"
    return f"[Corrupted File]  {p.name}   ·   {size}   ·   {mtime}"


def _row_text_snapshot(snap: dict) -> str:
    label = snap.get("label") or snap.get("timestamp", "Backup")
    summary = summarize_snapshot(snap)
    return f"[Master Backup]  {label}   ·   {summary}"


def _restore_with_confirm(bak: Path, parent) -> tuple[Path | None, Path | None]:
    target = bak.with_suffix("")
    if target.exists():
        ret = QtWidgets.QMessageBox.warning(
            parent,
            "Replace current file?",
            f"Restoring {target.name} from its corrupted copy replaces the "
            "current file. The current version is saved as a timestamped "
            "backup first, so nothing is lost.\n\nRestore anyway?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.Cancel,
            QtWidgets.QMessageBox.Cancel,
        )
        if ret != QtWidgets.QMessageBox.Yes:
            return None, None
    if bak.name.endswith(".json.bak"):
        try:
            json.loads(bak.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            ret = QtWidgets.QMessageBox.warning(
                parent,
                "Backup looks corrupt",
                f"{bak.name} doesn't look like valid JSON, so restoring it "
                "may not recover anything usable.\n\nRestore anyway?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.Cancel,
                QtWidgets.QMessageBox.Cancel,
            )
            if ret != QtWidgets.QMessageBox.Yes:
                return None, None
    preserved = None
    if target.exists():
        preserved = preserve_file_aside(target, config_dir() / "cache" / "restore")
    target = restore_backup_file(bak)
    if target is None:
        QtWidgets.QMessageBox.warning(
            parent, "Restore failed", f"Could not restore {bak.name}."
        )
        return None, None
    return target, preserved


class BackupScanDialog(QtWidgets.QDialog):
    """Lists master backup snapshots and preserved *.bak files for 1-click recovery."""

    def __init__(
        self,
        baks: list[Path] | None = None,
        loss_info: dict | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.baks = baks or []
        self.loss_info = loss_info
        self.setWindowTitle("Data Recovery & Backups")
        self.setObjectName("Card")
        self.setMinimumWidth(580)

        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(22, 20, 22, 20)
        v.setSpacing(12)

        head = QtWidgets.QLabel(
            "Data Recovery & Backups" if not loss_info else "Data Loss Warning & Recovery"
        )
        head.setObjectName("H1")
        v.addWidget(head)

        if loss_info and loss_info.get("reasons"):
            warn_card = QtWidgets.QFrame()
            warn_card.setStyleSheet(
                f"QFrame {{ background: rgba(239, 68, 68, 0.15); border: 1px solid {theme.DANGER}; "
                f"border-radius: 6px; padding: 8px; }}"
            )
            warn_lay = QtWidgets.QVBoxLayout(warn_card)
            warn_lay.setContentsMargins(8, 6, 8, 6)
            warn_lay.setSpacing(6)
            warn_title = QtWidgets.QLabel("<b>The following missing or reduced data was detected:</b>")
            warn_title.setStyleSheet(f"color: {theme.DANGER};")
            warn_lay.addWidget(warn_title)
            for r in loss_info["reasons"]:
                lbl = QtWidgets.QLabel(f"• {r}")
                lbl.setStyleSheet(f"color: {theme.TEXT};")
                lbl.setWordWrap(True)
                warn_lay.addWidget(lbl)

            affected = loss_info.get("affected_profiles", [])
            if affected:
                btn_flow = QtWidgets.QHBoxLayout()
                btn_flow.setSpacing(6)
                for prof in affected:
                    btn_ign = QtWidgets.QPushButton(f"Don't Ask Again for '{prof}'")
                    btn_ign.setCursor(QtCore.Qt.PointingHandCursor)
                    btn_ign.setStyleSheet(
                        f"QPushButton {{ font-size: 11px; font-weight: 600; padding: 4px 10px; color: {theme.GOLD}; border: 1px solid {theme.GOLD}; border-radius: 4px; background: transparent; }}"
                        f"QPushButton:hover {{ background: rgba(251, 191, 36, 0.15); }}"
                    )
                    btn_ign.clicked.connect(lambda _, p=prof: self._ignore_profile(p))
                    btn_flow.addWidget(btn_ign)
                btn_flow.addStretch(1)
                warn_lay.addLayout(btn_flow)

            v.addWidget(warn_card)

        sub = QtWidgets.QLabel(
            "Select a Master Backup snapshot or corrupted file to restore your settings, collection, "
            "planner, and character profiles."
        )
        sub.setObjectName("Muted")
        sub.setWordWrap(True)
        v.addWidget(sub)

        self.list = QtWidgets.QListWidget()
        self.list.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.list.setMinimumHeight(200)
        self.list.setStyleSheet(
            f"QListWidget {{ background:{theme.PANEL}; border:1px solid {theme.BORDER}; "
            f"border-radius:4px; }}"
            f"QListWidget::item {{ padding:6px 8px; color:{theme.TEXT}; }}"
            f"QListWidget::item:hover {{ background:{theme.PANEL_HI}; }}"
            f"QListWidget::item:selected {{ background:{theme.ACCENT_DIM}; color:{theme.TEXT}; }}"
            f"QListWidget::item:selected:hover {{ background:{theme.ACCENT_DIM}; }}"
        )
        self._populate_list()
        self.list.currentRowChanged.connect(self._on_selection)
        v.addWidget(self.list)

        self.empty_lbl = QtWidgets.QLabel("All files handled — nothing left to restore.")
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

        self.btn_restore = QtWidgets.QPushButton("Restore All")
        self.btn_restore.setObjectName("Accent")
        self.btn_restore.setMinimumHeight(34)
        self.btn_restore.clicked.connect(self._restore_selected)

        self.btn_restore_profile = QtWidgets.QPushButton("Restore Profile...")
        self.btn_restore_profile.setObjectName("Outline")
        self.btn_restore_profile.setMinimumHeight(34)
        self.btn_restore_profile.clicked.connect(self._restore_specific_profile)

        self.btn_restore_missing = QtWidgets.QPushButton("Restore Missing Only")
        self.btn_restore_missing.setObjectName("Outline")
        self.btn_restore_missing.setMinimumHeight(34)
        self.btn_restore_missing.setToolTip(
            "Adds back ONLY the items missing from the live files (collection "
            "entries, tracked profile progress, planner farm) - never "
            "overwrites or removes current data."
        )
        self.btn_restore_missing.clicked.connect(self._restore_missing)

        self.btn_delete = QtWidgets.QPushButton("Delete")
        self.btn_delete.setObjectName("Outline")
        self.btn_delete.setStyleSheet(
            f"QPushButton{{color:{theme.DANGER};}}"
            f"QPushButton:hover{{color:{theme.DANGER};background:{theme.PANEL_HI};}}"
        )
        self.btn_delete.clicked.connect(self._delete_selected)

        self.btn_keep = QtWidgets.QPushButton("Keep Current (Don't Ask Again)")
        self.btn_keep.setObjectName("Outline")
        self.btn_keep.setCursor(QtCore.Qt.PointingHandCursor)
        self.btn_keep.setToolTip("Accept current files and permanently suppress these warnings on next launch.")
        self.btn_keep.setStyleSheet(f"QPushButton {{ color: {theme.GOLD}; }}")
        self.btn_keep.clicked.connect(self._accept_current)

        self.btn_folder = QtWidgets.QPushButton("Open folder")
        self.btn_folder.setObjectName("Outline")
        self.btn_folder.clicked.connect(self._open_folder)

        self.btn_skip = QtWidgets.QPushButton("Skip")
        self.btn_skip.setObjectName("Outline")
        self.btn_skip.clicked.connect(self._on_skip)
        self.btn_close = self.btn_skip

        row = QtWidgets.QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(self.btn_inspect)
        row.addWidget(self.btn_restore)
        row.addWidget(self.btn_restore_profile)
        row.addWidget(self.btn_restore_missing)
        row.addWidget(self.btn_delete)
        row.addStretch(1)
        row.addWidget(self.btn_keep)
        row.addWidget(self.btn_folder)
        row.addWidget(self.btn_skip)
        v.addLayout(row)

        if self.list.count():
            self.list.setCurrentRow(0)
        else:
            self._set_empty()

        min_size = self.layout().totalMinimumSize()
        self.resize(max(min_size.width(), 620), min_size.height() + 120)

    def _populate_list(self):
        self.list.clear()
        # 1. Master Backup snapshots
        mb = load_master_backup()
        for snap in mb.get("backups", []):
            item = QtWidgets.QListWidgetItem(_row_text_snapshot(snap))
            item.setData(QtCore.Qt.UserRole, snap)
            self.list.addItem(item)
        
        # 2. Corrupted .bak files
        for bak in self.baks:
            item = QtWidgets.QListWidgetItem(_row_text_bak(bak))
            item.setData(QtCore.Qt.UserRole, bak)
            self.list.addItem(item)

    def _selected_data(self) -> Path | dict | None:
        item = self.list.currentItem()
        if item is None:
            return None
        return item.data(QtCore.Qt.UserRole)

    def _on_selection(self, _row: int):
        has_items = self.list.count() > 0
        data = self._selected_data()
        is_snap = isinstance(data, dict)
        self.btn_inspect.setEnabled(has_items)
        self.btn_restore.setEnabled(has_items)
        self.btn_restore_profile.setEnabled(has_items and is_snap and bool(data.get("profiles")))
        self.btn_restore_missing.setEnabled(has_items and is_snap)
        self.btn_delete.setEnabled(has_items)
        if is_snap:
            self.btn_restore.setText("Restore All")
        else:
            self.btn_restore.setText("Restore")

    def _set_empty(self):
        self.empty_lbl.show()
        self.btn_inspect.setEnabled(False)
        self.btn_restore.setEnabled(False)
        self.btn_restore_profile.setEnabled(False)
        self.btn_restore_missing.setEnabled(False)
        self.btn_delete.setEnabled(False)

    def _show_status(self, text: str):
        self.status_lbl.setText(text)
        self.status_lbl.show()

    def _restore_selected(self):
        data = self._selected_data()
        if data is None:
            return

        if isinstance(data, Path):
            target, preserved = _restore_with_confirm(data, self)
            if target is None:
                return
            self._note_restored(target, preserved)
            self._remove_current_row()
        elif isinstance(data, dict):
            label = data.get("label") or data.get("timestamp", "Backup")
            ret = QtWidgets.QMessageBox.warning(
                self,
                "Restore All Files?",
                f"Restoring Master Backup ({label}) will overwrite current settings, collection, "
                "planner, and all character profiles with the snapshot data.\n\nProceed with full restore?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.Cancel,
                QtWidgets.QMessageBox.Cancel,
            )
            if ret != QtWidgets.QMessageBox.Yes:
                return
            if restore_from_snapshot(data):
                self._show_status(f"Fully restored all files from backup: {label}")
                QtWidgets.QMessageBox.information(
                    self, "Restore complete", f"All data was successfully restored from {label}."
                )
            else:
                QtWidgets.QMessageBox.warning(self, "Restore failed", "Could not restore backup files.")

    def _restore_missing(self):
        data = self._selected_data()
        if not isinstance(data, dict):
            return
        label = data.get("label") or data.get("timestamp", "Backup")
        ret = QtWidgets.QMessageBox.question(
            self,
            "Restore only what's missing?",
            f"Merge from backup ({label})?\n\nAdds back ONLY the items the "
            "live files are missing (collection entries, tracked profile "
            "progress, planner farm). Current data is never overwritten or "
            "removed - this is safe to run any time.\n\nProceed?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.Cancel,
            QtWidgets.QMessageBox.Cancel,
        )
        if ret != QtWidgets.QMessageBox.Yes:
            return
        try:
            notes = restore_missing_from_snapshot(data)
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "Merge failed",
                                          f"Could not restore missing items: {e}")
            return
        if not notes:
            QtWidgets.QMessageBox.information(
                self, "Nothing missing",
                "The live files already contain everything this backup has - "
                "no changes were made."
            )
            self._show_status(f"Nothing was missing from backup {label} - no changes.")
            return
        self._show_status("Restored missing items:\n" + "\n".join(f"• {n}" for n in notes))
        QtWidgets.QMessageBox.information(
            self, "Missing items restored",
            "Restored only what was missing:\n\n" + "\n".join(f"• {n}" for n in notes)
        )

    def _restore_specific_profile(self):
        data = self._selected_data()
        if not isinstance(data, dict):
            return
        profiles = list(data.get("profiles", {}).keys())
        if not profiles:
            QtWidgets.QMessageBox.information(self, "No profiles", "This snapshot contains no character profiles.")
            return

        item, ok = QtWidgets.QInputDialog.getItem(
            self,
            "Select Character Profile",
            "Choose which character profile to restore from this backup:",
            profiles,
            0,
            False,
        )
        if ok and item:
            if restore_from_snapshot(data, target_profile=item):
                self._show_status(f"Restored profile '{item}' from backup.")
                QtWidgets.QMessageBox.information(
                    self, "Profile restored", f"Profile '{item}' was successfully restored."
                )
            else:
                QtWidgets.QMessageBox.warning(
                    self, "Restore failed", f"Could not restore profile '{item}'."
                )

    def _delete_selected(self):
        data = self._selected_data()
        if data is None:
            return

        if isinstance(data, Path):
            ret = QtWidgets.QMessageBox.warning(
                self,
                "Delete backup?",
                f"{data.name} may be the only preserved copy of that data.\n\nDelete anyway?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.Cancel,
                QtWidgets.QMessageBox.Cancel,
            )
            if ret != QtWidgets.QMessageBox.Yes:
                return
            if not delete_backup_file(data):
                QtWidgets.QMessageBox.warning(self, "Delete failed", f"Could not delete {data.name}.")
                return
            self._show_status(f"Deleted: {data.name}.")
            self._remove_current_row()
        elif isinstance(data, dict):
            label = data.get("label") or data.get("timestamp", "Backup")
            ret = QtWidgets.QMessageBox.warning(
                self,
                "Delete snapshot?",
                f"Delete Master Backup snapshot '{label}'?\n\nDelete anyway?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.Cancel,
                QtWidgets.QMessageBox.Cancel,
            )
            if ret != QtWidgets.QMessageBox.Yes:
                return
            mb = load_master_backup()
            mb["backups"] = [b for b in mb.get("backups", []) if b.get("timestamp") != data.get("timestamp")]
            atomic_write_json(master_backup_path(), mb, compact_lists=True)
            self._show_status(f"Deleted snapshot: {label}")
            self._remove_current_row()

    def _remove_current_row(self):
        row = self.list.currentRow()
        if row >= 0:
            self.list.takeItem(row)
            if self.list.count() == 0:
                self._set_empty()
            else:
                self.list.setCurrentRow(min(row, self.list.count() - 1))

    def _accept_current(self):
        accept_current_moddata_state()
        QtWidgets.QMessageBox.information(
            self,
            "Current State Accepted",
            "Your current files have been accepted as the intended state.\n\n"
            "You will not be asked about these profiles again on startup.",
        )
        self.accept()

    def _ignore_profile(self, profile: str):
        ret = QtWidgets.QMessageBox.question(
            self,
            "Forget Profile?",
            f"Permanently forget character profile '{profile}'?\n\n"
            f"This stops backup warnings for '{profile}' on future launches.",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.Cancel,
            QtWidgets.QMessageBox.Yes,
        )
        if ret == QtWidgets.QMessageBox.Yes:
            dismiss_profile_alert(profile, purge_from_backups=True)
            self._show_status(f"Profile '{profile}' forgotten.")
            QtWidgets.QMessageBox.information(
                self,
                "Profile Forgotten",
                f"Profile '{profile}' has been forgotten from backup warnings.",
            )
            self.accept()

    def _on_skip(self):
        if self.loss_info:
            accept_current_moddata_state()
        self.accept()

    def _inspect_selected(self):
        data = self._selected_data()
        if data is None:
            return
        if isinstance(data, Path):
            dlg = InspectDialog(data, self)
            dlg.backup_deleted.connect(self._on_backup_deleted)
            dlg.backup_restored.connect(self._on_backup_restored)
            dlg.exec()
        elif isinstance(data, dict):
            dlg = InspectSnapshotDialog(data, self)
            dlg.exec()

    def _on_backup_deleted(self, bak: Path):
        self._show_status(f"Deleted: {bak.name}.")
        self._remove_current_row()

    def _on_backup_restored(self, bak: Path, target: Path, preserved: Path | None):
        self._note_restored(target, preserved)
        self._remove_current_row()

    def _note_restored(self, target: Path, preserved: Path | None):
        kept = " Previous version kept in cache/restore/." if preserved else ""
        self._show_status(f"Restored: {target.name}.{kept}")

    def _open_folder(self):
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(config_dir())))


class InspectSnapshotDialog(QtWidgets.QDialog):
    """Detailed inspector for a Master Backup snapshot."""

    def __init__(self, snap: dict, parent=None):
        super().__init__(parent)
        label = snap.get("label") or snap.get("timestamp", "Backup")
        self.setWindowTitle(f"Snapshot Details — {label}")
        self.setObjectName("Card")
        self.resize(720, 480)

        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(16, 16, 16, 16)
        v.setSpacing(10)

        head = QtWidgets.QLabel(f"<b>Snapshot:</b> {label}<br><b>Summary:</b> {summarize_snapshot(snap)}")
        head.setObjectName("Muted")
        head.setTextFormat(QtCore.Qt.RichText)
        v.addWidget(head)

        edit = QtWidgets.QPlainTextEdit()
        edit.setReadOnly(True)
        edit.setStyleSheet(f"background:{theme.PANEL}; color:{theme.TEXT}; border:1px solid {theme.BORDER};")
        
        # Nicely format the snapshot content for reading
        text = json.dumps(snap, indent=2)
        edit.setPlainText(text)
        v.addWidget(edit, 1)

        row = QtWidgets.QHBoxLayout()
        row.addStretch(1)
        btn_close = QtWidgets.QPushButton("Close")
        btn_close.setObjectName("Outline")
        btn_close.clicked.connect(self.accept)
        row.addWidget(btn_close)
        v.addLayout(row)


_SAME_TINT = QtGui.QColor(255, 200, 0, 42)
_BACKUP_TINT = QtGui.QColor(80, 210, 120, 70)
_CURRENT_TINT = QtGui.QColor(255, 90, 90, 70)
_LEGEND_SAME = "#d4a017"
_LEGEND_BACKUP = "#4ade80"
_LEGEND_CURRENT = "#f87171"


def _tint_diff(pane: QtWidgets.QPlainTextEdit, text: str, other: str, diff_color: QtGui.QColor) -> None:
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
            cur = QtGui.QTextCursor(pane.document())
            cur.setPosition(block.position())
            cur.setPosition(block.position() + block.length() - 1, QtGui.QTextCursor.KeepAnchor)
            es.cursor = cur
            selections.append(es)
    if selections:
        pane.setExtraSelections(selections)


class InspectDialog(QtWidgets.QDialog):
    """Side-by-side view of a corrupted file vs the current file."""

    backup_deleted = QtCore.Signal(Path)
    backup_restored = QtCore.Signal(object, object, object)

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
            cur_text = target.read_text(encoding="utf-8", errors="replace") if target.exists() else None
        except OSError:
            cur_text = None

        if cur_text is not None:
            head = QtWidgets.QLabel(
                f"<b>Corrupted:</b> {summarize(bak.name, bak_text)}<br>"
                f"<b>Current:</b> {summarize(target.name, cur_text)}<br>"
                "<b>Restore</b> puts the corrupted file's contents back over the current file."
            )
        else:
            head = QtWidgets.QLabel(
                f"<b>Corrupted:</b> {summarize(bak.name, bak_text)}<br>"
                "<b>Current:</b> (none)<br>"
                "<b>Restore</b> puts the corrupted file's contents back in place."
            )
        head.setObjectName("Muted")
        head.setTextFormat(QtCore.Qt.RichText)
        head.setWordWrap(True)
        v.addWidget(head)

        if cur_text is not None:
            v.addWidget(self._legend())
            split = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
            split.addWidget(self._pane("Corrupted file — will be restored", bak_text, cur_text, _BACKUP_TINT))
            split.addWidget(self._pane("Current — will be replaced", cur_text, bak_text, _CURRENT_TINT))
            split.setSizes([380, 380])
            v.addWidget(split, 1)
        else:
            v.addWidget(self._pane("Corrupted file — will be restored", bak_text, None, _BACKUP_TINT), 1)

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
            f"QPushButton:hover{{color:{theme.DANGER};background:{theme.PANEL_HI};}}"
        )
        btn_delete.clicked.connect(lambda: self._delete(bak))
        row.addWidget(btn_delete)
        row.addStretch(1)
        close = QtWidgets.QPushButton("Close")
        close.setObjectName("Outline")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        v.addLayout(row)

        self.resize(max(self.minimumSizeHint().width(), 780), max(self.minimumSizeHint().height(), 480))

    def _restore(self, bak: Path):
        target, preserved = _restore_with_confirm(bak, self)
        if target is None:
            return
        self.backup_restored.emit(bak, target, preserved)
        self.accept()

    def _delete(self, bak: Path):
        ret = QtWidgets.QMessageBox.warning(
            self,
            "Delete corrupted file?",
            f"{bak.name} may be the only preserved copy of that data.\n\nDelete anyway?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.Cancel,
            QtWidgets.QMessageBox.Cancel,
        )
        if ret != QtWidgets.QMessageBox.Yes:
            return
        if not delete_backup_file(bak):
            QtWidgets.QMessageBox.warning(self, "Delete failed", f"Could not delete {bak.name}.")
            return
        self.backup_deleted.emit(bak)
        self.accept()

    def _pane(self, title: str, text: str, other: str | None, diff_color: QtGui.QColor) -> QtWidgets.QWidget:
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
        for color, text in (
            (_LEGEND_SAME, "same in both"),
            (_LEGEND_BACKUP, "only in corrupted file — comes back"),
            (_LEGEND_CURRENT, "only in current — lost"),
        ):
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
