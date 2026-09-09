"""Archived-fight deletion — the write side of the Past Fights panel.

The panel itself lives on the Combat & DPS page (`combat_fights_list` owns the
list, `history_index` the scan and the on-demand read); this module is only
what the panel cannot do read-only: delete one archived fight or a batch of
them from disk and from every in-memory copy. The `_KIND_STYLE` badge table
lives here because the panel imports it from the `..combat` hub.
"""
from __future__ import annotations

import json as _json
import re
from pathlib import Path

from PySide6 import QtWidgets

from .. import theme
from ...core.dps_tracker import CombatSession
from ...core.dps_data import _session_from_dict
from ...core.dps_tracker_history import remove_log_file
from .history_index import write_log_summary


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


def _rewrite_summary(path: Path, kept: list[dict]) -> None:
    """Re-derive a log's sidecar after an edit, so its row keeps its aggregates.

    A deletion changes the file, which invalidates the old sidecar by
    fingerprint and would leave the row with no aggregate line until the next
    archive. Best-effort: the log is the record, the sidecar is only its cache.
    """
    try:
        write_log_summary(path, [_session_from_dict(d) for d in kept])
    except Exception:
        pass


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

    if not kept:
        # The log held nothing else: remove the file itself (and its sidecar,
        # and any folder the removal empties) rather than leave an empty
        # `{"history": []}` stub the list would keep showing as an empty row.
        remove_log_file(path)
    else:
        try:
            from ...runtime.persist import atomic_write_json as _aw
            _aw(path, {"history": kept})
        except OSError:
            return False
        _rewrite_summary(path, kept)

    # Purge in-memory copies
    try:
        if active_tracker and getattr(active_tracker, "history", None):
            active_tracker.history = [
                s for s in active_tracker.history
                if not (s.start_time == sess.start_time and s.name == sess.name)]
            try:
                hd = getattr(active_tracker, "_history_dir", None)
                if (hd is not None and str(path).startswith(str(hd))
                        or active_tracker._resolve_history_file() == path):
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
        except Exception:
            continue
        hist = raw.get("history") or []
        delete_sigs = {(s.start_time, s.name) for s in sess_list}
        kept = [d for d in hist
                if (d.get("start_time"), d.get("name")) not in delete_sigs]
        if not kept:
            # Last fight in this log: REMOVE the file (and its sidecar) instead
            # of rewriting an empty stub — the exact reason a bulk delete used
            # to leave "empty folders with the files still on the pc".
            remove_log_file(path)
        else:
            try:
                from ...runtime.persist import atomic_write_json as _aw
                _aw(path, {"history": kept})
            except OSError:
                continue
            _rewrite_summary(path, kept)

        # Purge in-memory copies
        try:
            if active_tracker and getattr(active_tracker, "history", None):
                active_tracker.history = [
                    s for s in active_tracker.history
                    if not any(s.start_time == cs.start_time and s.name == cs.name for cs in sess_list)
                ]
                try:
                    hd = getattr(active_tracker, "_history_dir", None)
                    if (hd is not None
                            and any(str(Path(p)).startswith(str(hd))
                                    for p, _ in sess_list)
                            or active_tracker._resolve_history_file() in
                            [Path(p) for p, _ in sess_list]):
                        active_tracker._persist_history()
                except Exception:
                    pass
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

