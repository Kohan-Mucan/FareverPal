"""Past fights archiving, history indexing, and modal inspection.

Handles loading archived combat sessions from disk, scanning profile folders,
rendering the Past Fights modal dialog, and deleting individual fight records.
"""
from __future__ import annotations

import json as _json
import re
import time
from pathlib import Path
from PySide6 import QtCore, QtGui, QtWidgets

from .. import theme
from ...data import icons, units as udata
from ...core.dps_tracker import CombatSession, weapons_used, read_history_file
from ...config import dps_dir


def _char_label_from_history_path(path: Path) -> str:
    """Friendly character tag from a dps_history_<profile>.json file name."""
    name = path.name
    if name.startswith("dps_history_"):
        name = name[len("dps_history_"):]
    name = name[:-5] if name.endswith(".json") else name
    if name == "dps_history":
        return "Current Character"
    parts = name.split("_")
    # drop the trailing account/server hash (e.g. S8c4ba8) from the profile
    if len(parts) > 1 and re.match(r"^S[0-9a-fA-F]{5,}$", parts[-1]):
        parts = parts[:-1]
    return " ".join(p.capitalize() for p in parts) if parts else path.stem


def scan_history_dir(directory: str | Path | None = None) -> list[dict]:
    """Every archived fight under dps_dir(), newest first; skips bad files."""
    if directory is None:
        directory = dps_dir()
    directory = Path(directory)
    search_dirs = [directory]
    if (directory / "dps").is_dir():
        search_dirs.insert(0, directory / "dps")
    rows = []
    seen_paths = set()
    for sdir in search_dirs:
        for p in sorted(sdir.glob("dps_history*.json")):
            if p in seen_paths:
                continue
            seen_paths.add(p)
            try:
                sessions = read_history_file(p)
            except Exception:
                continue
            if not sessions:
                continue
            rows.append({
                "path": p,
                "char": _char_label_from_history_path(p),
                "sessions": sessions
            })
    rows.sort(key=lambda r: max((s.start_time or 0.0) for s in r["sessions"]),
              reverse=True)
    return rows


def _clean_session_name(name: str) -> str:
    """Strip the kind-duplicating parenthetical from archived fight names."""
    name = (name or "Fight").strip()
    return re.sub(r"\s*\([^)]*\)\s*$", "", name).strip() or "Fight"


_KIND_STYLE = {
    "boss": (theme.GOLD, "BOSS"),
    "boss_adds": (theme.ORANGE, "ADDS"),
    "trash": (theme.DIM, "TRASH"),
    "dummy": (theme.ACCENT_LIGHT, "TEST DUMMY"),
    "overall": (theme.ACCENT, "OVERALL"),
}


def delete_past_fight(parent_widget: QtWidgets.QWidget, path: str | Path, sess: CombatSession,
                      active_tracker, browse_holder: dict | None = None,
                      after_delete_cb=None) -> bool:
    """Delete one archived fight from its history file + in-memory copies."""
    path = Path(path)
    confirm = QtWidgets.QMessageBox.question(
        parent_widget, "Delete fight?",
        f"Delete '{_clean_session_name(sess.name)}'?\n"
        f"Boss: {(sess.target_name or 'None').replace('👑 ', '')}\n"
        "This cannot be undone.",
        QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
        QtWidgets.QMessageBox.No)
    if confirm != QtWidgets.QMessageBox.Yes:
        return False

    try:
        raw = _json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return False
    hist = raw.get("history") or []
    kept = [d for d in hist
            if not (d.get("start_time") == sess.start_time
                    and d.get("name") == sess.name)]
    if len(kept) == len(hist):
        kept = [d for d in hist if d.get("start_time") != sess.start_time]

    try:
        from ...persist import atomic_write_json as _aw
        _aw(path, {"history": kept})
    except OSError:
        return False

    # Purge in-memory copies
    try:
        if active_tracker and getattr(active_tracker, "history", None):
            active_tracker.history = [
                s for s in active_tracker.history
                if not (s.start_time == sess.start_time and s.name == sess.name)]
            try:
                if active_tracker._resolve_history_file() == path:
                    active_tracker._persist_history()
            except Exception:
                pass
        if browse_holder and getattr(browse_holder, "_browse_sessions", None):
            browse_holder._browse_sessions = [
                s for s in browse_holder._browse_sessions
                if not (s.start_time == sess.start_time and s.name == sess.name)]
            if not browse_holder._browse_sessions:
                browse_holder._browse_sessions = None
                browse_holder._browse_char = ""
                browse_holder._selected_history_idx = 0
    except Exception:
        pass

    if after_delete_cb:
        after_delete_cb()
    return True


def delete_past_fights_batch(parent_widget: QtWidgets.QWidget, items: list[tuple[Path, CombatSession]],
                            active_tracker, browse_holder: dict | None = None,
                            after_delete_cb=None) -> bool:
    """Delete multiple archived fights in a single batch with 1 confirmation dialog."""
    if not items:
        return False

    confirm = QtWidgets.QMessageBox.question(
        parent_widget, "Delete selected fights?",
        f"Delete {len(items)} selected fight(s)?\n"
        "This cannot be undone.",
        QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
        QtWidgets.QMessageBox.No)
    if confirm != QtWidgets.QMessageBox.Yes:
        return False

    # Group items by file path
    by_path: dict[Path, list[CombatSession]] = {}
    for p, sess in items:
        p = Path(p)
        by_path.setdefault(p, []).append(sess)

    for path, sess_list in by_path.items():
        try:
            raw = _json.loads(path.read_text(encoding="utf-8"))
            hist = raw.get("history") or []
            delete_sigs = {(s.start_time, s.name) for s in sess_list}
            kept = [d for d in hist if (d.get("start_time"), d.get("name")) not in delete_sigs]
            from ...persist import atomic_write_json as _aw
            _aw(path, {"history": kept})
        except Exception:
            continue

        # Purge in-memory copies
        try:
            if active_tracker and getattr(active_tracker, "history", None):
                active_tracker.history = [
                    s for s in active_tracker.history
                    if not any(s.start_time == cs.start_time and s.name == cs.name for cs in sess_list)
                ]
            if browse_holder and getattr(browse_holder, "_browse_sessions", None):
                browse_holder._browse_sessions = [
                    s for s in browse_holder._browse_sessions
                    if not any(s.start_time == cs.start_time and s.name == cs.name for cs in sess_list)
                ]
                if not browse_holder._browse_sessions:
                    browse_holder._browse_sessions = None
                    browse_holder._browse_char = ""
                    browse_holder._selected_history_idx = 0
        except Exception:
            pass

    if after_delete_cb:
        after_delete_cb()
    return True


def open_past_fights_dialog(parent_widget: QtWidgets.QWidget, tracker_getter,
                            browse_cb, after_change_cb):
    """Dialog listing every archived fight on disk."""
    dlg = QtWidgets.QDialog(parent_widget)
    dlg.setWindowTitle("Past Fights")
    dlg.resize(780, 480)
    lay = QtWidgets.QVBoxLayout(dlg)

    info = QtWidgets.QLabel(
        "Archived encounters read from disk — no game needed. "
        "Double-click a fight (or select it and press View) to load "
        "it into the breakdown below. Use the trash button to delete "
        "a single fight.")
    info.setWordWrap(True)
    info.setStyleSheet(f"font-size: 11px; color: {theme.MUTED};")
    lay.addWidget(info)

    pick: list[tuple[Path, str, list, int]] = []
    lst = QtWidgets.QListWidget()
    lst.setStyleSheet(
        f"QListWidget {{ background: {theme.SURFACE}; border: 1px solid "
        f"{theme.BORDER}; border-radius: 6px; padding: 4px; outline: 0; }}"
        f"QListWidget::item {{ padding: 2px; border-radius: 4px; }}"
        f"QListWidget::item:hover {{ background: {theme.PANEL_LOW}; }}"
        f"QListWidget::item:selected {{ background: {theme.ACCENT_DIM}; }}")
    lay.addWidget(lst)

    empty = QtWidgets.QLabel(
        "No archived fights found in moddata yet. Finish a fight "
        "and it is saved here automatically (per character).")
    empty.setAlignment(QtCore.Qt.AlignCenter)
    empty.setStyleSheet(f"font-size: 12px; color: {theme.MUTED};")
    lay.addWidget(empty)

    def _adopt(row: tuple[Path, str, list, int]):
        _path, char, sessions, si = row
        browse_cb(char, sessions, si)
        dlg.accept()

    def _delete_row(path: Path, sess) -> None:
        t = tracker_getter()
        if delete_past_fight(dlg, path, sess, t, parent_widget, after_change_cb):
            _rebuild()

    def _rebuild() -> None:
        lst.clear()
        pick.clear()
        rows = scan_history_dir(dps_dir())
        for r in rows:
            for si, sess in enumerate(r["sessions"]):
                when = (time.strftime("%m-%d %H:%M",
                                      time.localtime(sess.start_time))
                        if sess.start_time else "--")
                mins = int(sess.duration // 60)
                secs = sess.duration % 60
                nm = _clean_session_name(sess.name)
                kind = (sess.kind or "overall").lower()
                k_col, k_txt = _KIND_STYLE.get(kind, (theme.ACCENT, kind.upper()))
                raw_boss = (sess.target_name or "").replace("👑 ", "").replace("🎯 ", "").strip()
                is_boss = bool(sess.target_name and "👑" in sess.target_name)
                if not raw_boss or raw_boss == "None":
                    boss_txt = "no boss recorded"
                    boss_col = theme.DIM
                    boss_pre = "🎯 "
                elif is_boss:
                    boss_txt = raw_boss
                    boss_col = theme.GOLD
                    boss_pre = "👑 "
                else:
                    boss_txt = raw_boss
                    boss_col = theme.MUTED
                    boss_pre = "🎯 "

                item = QtWidgets.QListWidgetItem()
                item.setSizeHint(QtCore.QSize(0, 52))
                row_w = QtWidgets.QWidget()
                h = QtWidgets.QHBoxLayout(row_w)
                h.setContentsMargins(6, 4, 4, 4)
                h.setSpacing(6)

                txt_col = QtWidgets.QVBoxLayout()
                txt_col.setSpacing(1)

                top = QtWidgets.QHBoxLayout()
                top.setSpacing(6)
                name_lbl = QtWidgets.QLabel(nm)
                name_lbl.setStyleSheet(
                    f"font-size: 12px; font-weight: 800; "
                    f"color: {theme.TEXT}; background: transparent;")
                top.addWidget(name_lbl)
                kind_lbl = QtWidgets.QLabel(k_txt)
                kind_lbl.setStyleSheet(
                    f"font-size: 9px; font-weight: 800; color: {k_col}; "
                    f"background: {theme.with_alpha(k_col, 28)}; "
                    f"border-radius: 3px; padding: 1px 5px;")
                top.addWidget(kind_lbl)
                top.addStretch(1)
                dmg_lbl = QtWidgets.QLabel(f"{sess.group_damage:,.0f} DMG")
                dmg_lbl.setStyleSheet(
                    f"font-size: 12px; font-weight: 800; color: {theme.GOLD}; "
                    f"font-family: monospace; background: transparent;")
                top.addWidget(dmg_lbl)
                txt_col.addLayout(top)

                bot = QtWidgets.QHBoxLayout()
                bot.setSpacing(6)
                date_lbl = QtWidgets.QLabel(when)
                date_lbl.setStyleSheet(
                    f"font-size: 10px; color: {theme.DIM}; "
                    f"font-family: monospace; background: transparent;")
                bot.addWidget(date_lbl)
                char_lbl = QtWidgets.QLabel(f"[{r['char']}]")
                char_lbl.setStyleSheet(
                    f"font-size: 10px; font-weight: 700; "
                    f"color: {theme.ACCENT_LIGHT}; background: transparent;")
                bot.addWidget(char_lbl)
                if (udata.is_training_dummy(raw_boss)
                        or udata.is_training_dummy(sess.target_name)):
                    dpm = icons.asset_icon("target-dummy", 16)
                    if dpm is None or dpm.isNull():
                        dpm = icons.ui_icon("target-dummy", boss_col, 16)
                    if dpm is not None and not dpm.isNull():
                        dummy_ic = QtWidgets.QLabel()
                        dummy_ic.setPixmap(dpm)
                        dummy_ic.setToolTip("Training dummy (own boss group)")
                        dummy_ic.setStyleSheet("background: transparent;")
                        bot.addWidget(dummy_ic)
                boss_lbl = QtWidgets.QLabel(f"{boss_pre}{boss_txt}")
                boss_lbl.setStyleSheet(
                    f"font-size: 11px; font-weight: 700; color: {boss_col}; "
                    f"background: transparent;")
                boss_lbl.setToolTip(f"Boss / target: {boss_pre}{boss_txt}")
                bot.addWidget(boss_lbl, 1)
                dur_lbl = QtWidgets.QLabel(f"{mins:02d}:{secs:02.0f}")
                dur_lbl.setStyleSheet(
                    f"font-size: 10px; color: {theme.MUTED}; "
                    f"font-family: monospace; background: transparent;")
                bot.addWidget(dur_lbl)
                txt_col.addLayout(bot)

                h.addLayout(txt_col, 1)

                trash_btn = QtWidgets.QPushButton()
                trash_btn.setIcon(icons.ui_qicon("trash", theme.DANGER, 16))
                trash_btn.setIconSize(QtCore.QSize(16, 16))
                trash_btn.setFixedSize(28, 28)
                trash_btn.setCursor(QtCore.Qt.PointingHandCursor)
                trash_btn.setToolTip("Delete this fight")
                trash_btn.setStyleSheet(
                    f"QPushButton {{ background: transparent; "
                    f"border: 1px solid {theme.BORDER}; border-radius: 4px; }}"
                    f"QPushButton:hover {{ border-color: {theme.DANGER}; "
                    f"background: {theme.with_alpha(theme.DANGER, 30)}; }}")
                trash_btn.clicked.connect(
                    lambda _=None, p=r["path"], s=sess: _delete_row(p, s))
                h.addWidget(trash_btn, 0, QtCore.Qt.AlignVCenter)

                lst.addItem(item)
                lst.setItemWidget(item, row_w)
                weaps = weapons_used(sess)
                tip = (f"{nm} · {boss_pre}{boss_txt} · "
                       f"{mins:02d}:{secs:02.0f} · {sess.group_damage:,.0f} DMG"
                       + (f"\nWeapons: {', '.join(w['name'] for w in weaps)}"
                          if weaps else ""))
                item.setToolTip(tip)
                item.setData(QtCore.Qt.UserRole, tip)
                pick.append((r["path"], r["char"], r["sessions"], si))
        has_any = bool(pick)
        lst.setVisible(has_any)
        empty.setVisible(not has_any)

    lst.itemDoubleClicked.connect(
        lambda it: _adopt(pick[lst.row(it)]) if 0 <= lst.row(it) < len(pick) else None)

    btns = QtWidgets.QHBoxLayout()
    btns.addStretch(1)
    view_btn = QtWidgets.QPushButton("View Selected")
    view_btn.setStyleSheet(
        f"padding: 6px 14px; font-weight: 700; color: {theme.ACCENT};")
    view_btn.clicked.connect(
        lambda: (_adopt(pick[lst.currentRow()])
                 if 0 <= lst.currentRow() < len(pick) else None))
    btns.addWidget(view_btn)
    close_btn = QtWidgets.QPushButton("Close")
    close_btn.setStyleSheet("padding: 6px 14px; font-weight: 600;")
    close_btn.clicked.connect(dlg.reject)
    btns.addWidget(close_btn)
    lay.addLayout(btns)

    _rebuild()
    dlg.exec()
