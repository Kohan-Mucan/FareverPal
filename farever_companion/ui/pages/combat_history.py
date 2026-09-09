"""Combat History & Past Fights Mixin.

Handles encounter history dropdown, archived fights browsing,
past fights filtering, batch deletion, and historical session loading.
"""
from __future__ import annotations

import time
from pathlib import Path
from PySide6 import QtCore, QtGui, QtWidgets

from .. import theme
from ...data import icons, units as udata
from ...core.dps_tracker import DpsTracker, CombatSession
from ...config import dps_dir
from ..combat import (
    scan_history_dir,
    _clean_session_name,
    delete_past_fight,
    forget_cached_sessions,
)
from .combat_fights_list import CombatFightsListMixin
from .combat_fights_parts import build_fights_search_box


class _RichComboDelegate(QtWidgets.QStyledItemDelegate):
    """Item delegate for QComboBox rendering HTML formatted rich text entries with multi-part colors."""

    def paint(self, painter, option, index):
        options = QtWidgets.QStyleOptionViewItem(option)
        self.initStyleOption(options, index)

        html_text = index.data(QtCore.Qt.UserRole + 1) or index.data(QtCore.Qt.DisplayRole) or ""

        painter.save()
        if options.state & QtWidgets.QStyle.State_Selected:
            painter.fillRect(options.rect, QtGui.QColor(theme.ACCENT_DIM))
        else:
            painter.fillRect(options.rect, QtGui.QColor(theme.PANEL))

        doc = QtGui.QTextDocument()
        doc.setDefaultFont(options.font)
        doc.setHtml(f"<div style='color: {theme.TEXT}; font-family: sans-serif; font-size: 11px;'>{html_text}</div>")

        painter.translate(options.rect.left() + 6, options.rect.top() + 3)
        clip = QtCore.QRectF(0, 0, options.rect.width() - 12, options.rect.height() - 6)
        doc.drawContents(painter, clip)
        painter.restore()

    def sizeHint(self, option, index):
        options = QtWidgets.QStyleOptionViewItem(option)
        self.initStyleOption(options, index)
        doc = QtGui.QTextDocument()
        doc.setDefaultFont(options.font)
        html_text = index.data(QtCore.Qt.UserRole + 1) or index.data(QtCore.Qt.DisplayRole) or ""
        doc.setHtml(f"<div style='font-size: 11px;'>{html_text}</div>")
        return QtCore.QSize(int(doc.idealWidth()) + 24, 28)


def _today_stamp() -> str:
    """Today's ``YYYY-MM-DD`` folder stamp, in local time.

    Matches `DpsTrackerHistory.day_dir`, which stamps both levels with
    `time.localtime`, so the combo's cold-start scan asks for exactly the
    folder the writer filed today's fights in.
    """
    return time.strftime("%Y-%m-%d")


def _fit_history_popup(combo: QtWidgets.QComboBox) -> None:
    """Widen the fight-selector's DROPDOWN to its widest entry.

    The closed combo is fine; the popup was not. A QComboBox's popup is sized
    to the combo itself, and this one carries `max-width: 640px` in its
    stylesheet (see combat_page._page_combat) — so no matter how wide the
    delegate asks for, the list was clamped at 640px and every long fight label
    (boss + zone + timestamp + duration + difficulty) was cut off. Measured
    offscreen: the delegate wanted 835px, the popup got 640px, so roughly 200px
    of text — the timestamp and difficulty — never rendered. Reported live
    2026-10-03: "DPS Analysis make the dropdown list bigger to fix all the text".

    `setMaximumWidth` on the widget does NOT help (the popup still inherits the
    combo's width); only the VIEW's minimum width does, which is what this
    sets. Called after every repopulate, because the widest entry changes with
    the fight list, and clamped so one enormous custom session name cannot
    open a popup wider than the screen.
    """
    view = combo.view()
    delegate = combo.itemDelegate()
    if view is None or delegate is None or combo.count() == 0:
        return
    option = QtWidgets.QStyleOptionViewItem()
    widest = 0
    for i in range(combo.count()):
        idx = combo.model().index(i, 0)
        if idx.isValid():
            widest = max(widest, delegate.sizeHint(option, idx).width())
    if widest <= 0:
        return
    screen = QtWidgets.QApplication.primaryScreen()
    limit = int(screen.availableGeometry().width() * 0.9) if screen else 4000
    view.setMinimumWidth(max(combo.width(), min(widest + 12, limit)))


def _format_history_label_html(sess: CombatSession, char: str = "", index_num: int = 1) -> str:
    raw_boss = (sess.target_name or "").replace("👑 ", "").strip()
    if (sess.kind or "").lower() == "trash":
        # A trash pack is named for the pack, not for whichever
        # mob happened to be hit last: the encounter dropdown
        # showed one random Invader where the fight is "Trash
        # Mobs" (live 2026-10-02: the merged rift pack read as
        # "Warbringer Invader" in the selector). The last mob
        # stays in the item's tooltip.
        raw_boss = ""
    boss_name = raw_boss if (raw_boss and raw_boss != "None") else _clean_session_name(sess.name)
    char_str = f"<span style='color: {theme.MUTED};'>[{char}]</span>" if char else ""
    if sess.start_time:
        time_str = f"<span style='color: {theme.DIM};'>{time.strftime('%b %d, %I:%M %p', time.localtime(sess.start_time))}</span>"
    else:
        time_str = ""
    mins = int(sess.duration // 60)
    secs = sess.duration % 60
    dur_str = f"<span style='color: {theme.TEXT}; font-family: monospace;'>⏱ {mins:02d}:{secs:02.0f}</span>"
    dmg = sess.group_damage
    dmg_val = f"{dmg:,.0f}" if dmg < 10_000_000 else f"{dmg/1_000_000:.2f}M"
    dmg_str = f"<span style='color: {theme.GOOD}; font-weight: 800; font-family: monospace;'>⚡ {dmg_val} DMG</span>"

    idx_html = f"<span style='color: {theme.GOLD}; font-weight: 800;'>#{index_num}</span>"
    boss_html = f"<span style='color: {theme.ACCENT_LIGHT}; font-weight: 800;'>⚔ {boss_name}</span>"

    parts = [idx_html, boss_html, char_str, time_str, dur_str, dmg_str]
    sep = f" <span style='color: {theme.BORDER};'>·</span> "
    return sep.join(p for p in parts if p)


def _format_history_label(sess: CombatSession, char: str = "") -> str:
    raw_boss = (sess.target_name or "").replace("👑 ", "").strip()
    if (sess.kind or "").lower() == "trash":
        # See _format_history_label_html: a trash pack is named
        # for the pack, never for its last-hit mob.
        raw_boss = ""
    boss_name = raw_boss if (raw_boss and raw_boss != "None") else _clean_session_name(sess.name)
    char_str = f"[{char}]" if char else ""
    if sess.start_time:
        time_str = time.strftime("%b %d, %I:%M %p", time.localtime(sess.start_time))
    else:
        time_str = ""
    mins = int(sess.duration // 60)
    secs = sess.duration % 60
    dur_str = f"{mins:02d}:{secs:02.0f}"
    dmg = sess.group_damage
    dmg_str = f"{dmg:,.0f} DMG" if dmg < 10_000_000 else f"{dmg/1_000_000:.2f}M DMG"
    parts = [f"⚔ {boss_name}", char_str, time_str, dur_str, dmg_str]
    return "  ·  ".join(p for p in parts if p)


class CombatHistoryMixin(CombatFightsListMixin):
    """Encounter history combo, past fights browser, and session lifecycle."""

    def _build_past_fights_view(self) -> QtWidgets.QWidget:
        """Fights view: a FROZEN action bar over a scrolling list of logs.

        The batch bar (Select All / filters / delete) used to live INSIDE the
        scroll area, so scrolling down to older files carried the controls off
        the top of the page — and a bulk action you have to scroll back up to
        reach is exactly the one this view cannot afford (live 2026-10-04: "the
        bar goes up the page"). The bar now has its own slot ABOVE the scroll
        area; only the day headers and log rows move.
        """
        outer = QtWidgets.QWidget()
        outer_lay = QtWidgets.QVBoxLayout(outer)
        outer_lay.setContentsMargins(0, 0, 0, 0)
        outer_lay.setSpacing(6)

        # The slot `_place_fights_toolbar` fills. It is empty (and so takes no
        # height) until a render has a bar to show, which keeps the whole pane
        # with the list on the empty state.
        self.cp_fights_toolbar_host = QtWidgets.QWidget()
        self._cp_fights_toolbar_lay = QtWidgets.QVBoxLayout(
            self.cp_fights_toolbar_host)
        self._cp_fights_toolbar_lay.setContentsMargins(0, 0, 0, 0)
        self._cp_fights_toolbar_lay.setSpacing(0)
        outer_lay.addWidget(self.cp_fights_toolbar_host)

        # Search box: created ONCE and kept OUT of the re-rendered tree. The
        # list rebuilds its body (and the batch bar) on every change, so a box
        # inside either would lose focus mid-typing; this one is a sibling that
        # the render only reads. See `combat_fights_parts` for the read-free
        # matching rule.
        self.cp_fights_search = build_fights_search_box(
            getattr(self, "_cp_fights_query", ""),
            self._on_fights_query_changed)
        outer_lay.addWidget(self.cp_fights_search)

        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll.setStyleSheet("background: transparent;")

        self.cp_content_container = QtWidgets.QWidget()
        self.cp_content_lay = QtWidgets.QVBoxLayout(self.cp_content_container)
        self.cp_content_lay.setContentsMargins(0, 0, 0, 0)
        self.cp_content_lay.setSpacing(8)

        self.cp_empty_lbl = QtWidgets.QLabel("No combat data recorded yet. Attack enemies or dummy in-game.")
        self.cp_empty_lbl.setAlignment(QtCore.Qt.AlignCenter)
        self.cp_empty_lbl.setStyleSheet(f"color: {theme.DIM}; font-size: 12px; padding: 40px 0;")
        self.cp_content_lay.addWidget(self.cp_empty_lbl)

        scroll.setWidget(self.cp_content_container)
        outer_lay.addWidget(scroll, 1)
        outer.hide()
        # `_cp_past_scroll` is the WHOLE view (bar + list), so the tab switch
        # shows/hides both together.
        self._cp_past_scroll = outer
        return outer



    def _history_source(self, t: DpsTracker | None) -> tuple[str, list[CombatSession]]:
        """Encounter-combo source: browsed archive, else live history."""
        if getattr(self, "_browse_sessions", None):
            return getattr(self, "_browse_char", ""), self._browse_sessions
        if t and getattr(t, "history", None) and len(t.history) > 0:
            return "", t.history
        all_disk = []
        # TODAY ONLY. This fallback fills the recent-fights combo/breakdown on a
        # cold start, and walking every archived day here iterated each row's
        # `_LazySessions` — parsing all 66 logs on the GUI thread (~44 ms, live
        # 2026-10-03) to keep at most three. Older days are the Past Fights
        # dialog's job, which the 📜 Fights button opens and which reads them by
        # name without parsing.
        for r in scan_history_dir(dps_dir(), day=_today_stamp()):
            for s in r.get("sessions", []):
                if s and (s.group_damage > 0 or s.group_heals > 0):
                    all_disk.append(s)
        all_disk.sort(key=lambda s: (s.start_time or 0.0))
        return "All Fights", all_disk

    def _sync_history_combo(self):
        if not hasattr(self, "cp_history_combo"):
            return
        t = self._get_active_tracker()

        items: list[tuple[str, str, CombatSession, str]] = []  # (plain_label, html_label, sess, tip)
        if getattr(self, "_browse_sessions", None):
            char = getattr(self, "_browse_char", "")
            for i, sess in enumerate(reversed(self._browse_sessions)):
                label = f"#{i+1}  " + _format_history_label(sess, char)
                html_label = _format_history_label_html(sess, char, i + 1)
                tip = label
                items.append((label, html_label, sess, tip))
            mode_tag = f"browse_{char}_{len(self._browse_sessions)}"
        elif t and getattr(t, "history", None) and len(t.history) > 0:
            char = getattr(getattr(self, "model", None), "character_name", "") or ""
            for i, sess in enumerate(reversed(t.history)):
                label = f"#{i+1}  " + _format_history_label(sess, char)
                html_label = _format_history_label_html(sess, char, i + 1)
                tip = label
                items.append((label, html_label, sess, tip))
            mode_tag = f"live_{len(t.history)}"
        else:
            now_scan = time.monotonic()
            if now_scan - getattr(self, "_last_disk_scan_ts", 0.0) > 2.0 or not hasattr(self, "_cached_all_disk"):
                self._last_disk_scan_ts = now_scan
                all_disk: list[tuple[str, CombatSession]] = []
                # Same today-only rule as `_history_source`: the 📜 Fights
                # dialog lists the rest by name, so the combo never parses them.
                for r in scan_history_dir(dps_dir(), day=_today_stamp()):
                    ch = r.get("char", "")
                    for sess in r.get("sessions", []):
                        if sess and (sess.group_damage > 0 or sess.group_heals > 0):
                            all_disk.append((ch, sess))
                all_disk.sort(key=lambda cs: (cs[1].start_time or 0.0), reverse=True)
                self._cached_all_disk = all_disk
            else:
                all_disk = self._cached_all_disk
            for i, (ch, sess) in enumerate(all_disk):
                label = f"#{i+1}  " + _format_history_label(sess, ch)
                html_label = _format_history_label_html(sess, ch, i + 1)
                tip = (f"{label}\nTarget: {(sess.target_name or '').replace('👑 ', '')}")
                items.append((label, html_label, sess, tip))
            mode_tag = f"disk_{len(all_disk)}"

        # Restrict top fight selector dropdown to the last 3 past fights only
        items = items[:3]

        expected_count = len(items) + 1
        cur_count = self.cp_history_combo.count()
        if cur_count != expected_count or getattr(self, "_combo_tag", None) != mode_tag:
            cur_idx = self.cp_history_combo.currentIndex()
            self._combo_sessions = [None] + [sess for _, _, sess, _ in items]
            self.cp_history_combo.blockSignals(True)
            self.cp_history_combo.clear()
            live_html = f"<span style='color: {theme.GOOD}; font-weight: 800;'>● Live Encounter</span> <span style='color: {theme.MUTED};'>(Active)</span>"
            self.cp_history_combo.addItem("● Live Encounter (Active)")
            self.cp_history_combo.setItemData(0, live_html, QtCore.Qt.UserRole + 1)
            self.cp_history_combo.setItemData(0, "Live in-game encounter", QtCore.Qt.ToolTipRole)

            for label, html_label, sess, tip in items:
                idx = self.cp_history_combo.count()
                self.cp_history_combo.addItem(label)
                self.cp_history_combo.setItemData(idx, html_label, QtCore.Qt.UserRole + 1)
                self.cp_history_combo.setItemData(idx, tip, QtCore.Qt.ToolTipRole)

            target_idx = min(max(0, cur_idx), expected_count - 1) if cur_idx >= 0 else 0
            self.cp_history_combo.setCurrentIndex(target_idx)
            _fit_history_popup(self.cp_history_combo)
            self._selected_history_idx = target_idx
            self._combo_tag = mode_tag
            self.cp_history_combo.blockSignals(False)

    def _on_history_selection_changed(self, idx: int):
        if idx < 0:
            return
        self._selected_history_idx = idx
        # The segment chips only apply to the live run, so their enabled state
        # follows the fight selector (see CombatPageMixin._sync_segment_chips).
        if callable(getattr(self, "_sync_segment_chips", None)):
            self._sync_segment_chips()
        if idx == 0:
            self._user_explicitly_chose_live = True
            self._auto_last_fight = False
            self._auto_last_suppressed = True
            self._update_hist_banner(False)
            if getattr(self, "_browse_sessions", None):
                self._browse_sessions = None
                self._browse_char = ""
                self._sync_history_combo()
        else:
            self._user_explicitly_chose_live = False
            self._auto_last_fight = False
        self._update_hist_banner(False)
        session = self._get_displayed_session()
        if session and hasattr(self, "cp_plotter"):
            self.cp_plotter.set_session(session)
        self._render_combat_breakdown()

    def _browse_fights(self, char: str, sessions: list[CombatSession], select_idx: int = 0) -> None:
        """Show archived ``sessions`` in the Encounter combo (0 = oldest)."""
        self._browse_char = char or ""
        self._browse_sessions = list(sessions)
        self._sync_history_combo()
        idx = min(len(sessions) - select_idx, self.cp_history_combo.count() - 1)
        if self.cp_history_combo.currentIndex() != idx:
            self.cp_history_combo.setCurrentIndex(idx)
        else:
            self._on_history_selection_changed(idx)

    @staticmethod
    def _session_has_live_data(s: CombatSession) -> bool:
        return bool(s) and (s.group_damage > 0.0 or s.group_heals > 0.0)

    def _update_hist_banner(self, on: bool, session: CombatSession | None = None) -> None:
        if not hasattr(self, "cp_hist_banner"):
            return
        if on and session is not None:
            when = (time.strftime("%m-%d %H:%M", time.localtime(session.start_time))
                    if session.start_time else "earlier")
            nm = _clean_session_name(session.name)
            self.cp_hist_banner.setText(
                f"🕘 Showing your last fight · {when} · {nm} · "
                f"{session.group_damage:,.0f} DMG — no live combat yet. "
                f"It switches to LIVE the moment you start fighting.")
            self.cp_hist_banner.show()
        else:
            self.cp_hist_banner.setText("")
            self.cp_hist_banner.hide()


    def _select_live_combat(self) -> None:
        self._selected_history_idx = 0
        self._browse_sessions = None
        self._browse_char = ""
        self._auto_last_fight = False
        self._auto_last_suppressed = True
        if hasattr(self, "cp_history_combo"):
            self.cp_history_combo.blockSignals(True)
            if self.cp_history_combo.count() > 0:
                self.cp_history_combo.setCurrentIndex(0)
            self.cp_history_combo.blockSignals(False)
        self._update_hist_banner(False)
        self._render_combat_breakdown()

    def _auto_exit_to_live(self, t: DpsTracker) -> None:
        if not (getattr(self, "_auto_last_fight", False) and self._browse_sessions is not None):
            return
        if not self._session_has_live_data(t.session):
            return
        self._auto_last_fight = False
        self._browse_sessions = None
        self._browse_char = ""
        self._selected_history_idx = 0
        if hasattr(self, "cp_history_combo"):
            self.cp_history_combo.blockSignals(True)
            self.cp_history_combo.setCurrentIndex(0)
            self.cp_history_combo.blockSignals(False)
        self._update_hist_banner(False)

    def _clear_old_logs(self) -> None:
        """Delete archived fights older than the Keep Logs window (bulk).

        Deliberately manual and deliberately explicit: the archived fights and
        the dummy sheets beside them are the player's own record, so nothing
        prunes on a timer. The window is read from the setting rather than
        asked for here, so the same number governs the button and the docs.
        """
        days = int(getattr(self.s, "dps_log_keep_days", 0) or 0)
        if days <= 0:
            QtWidgets.QMessageBox.information(
                self, "Nothing to clear",
                "Keep Logs is set to 0 — every archived fight is kept.\n\n"
                "Set it in Settings > DPS > Combat DPS Meter to a number of "
                "days, then come back to clear anything older.")
            return
        confirm = QtWidgets.QMessageBox.question(
            self, "Clear old logs?",
            f"Delete every archived fight older than {days} days?\n\n"
            "This covers the fight history and the dummy test sheets in "
            "moddata/dps. It cannot be undone.",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No)
        if confirm != QtWidgets.QMessageBox.Yes:
            return
        try:
            tracker = self._get_active_tracker()
            out = (tracker.prune_history_older_than(days, directory=dps_dir())
                   if tracker is not None else {"folders": 0, "files": 0})
        except Exception as exc:               # a locked file, a bad path
            try:
                self.log(f"Clear old logs failed: {exc}")
            except Exception:
                pass
            return
        self._cp_past_fights_dirty = True
        self._sync_history_combo()
        self._render_combat_breakdown()
        note = (f"Cleared {out.get('folders', 0)} day folders "
                f"({out.get('files', 0)} files) older than {days} days.")
        try:
            self.log(note)
        except Exception:
            pass


    def _get_displayed_session(self) -> CombatSession | None:
        t = self._get_active_tracker()
        idx = getattr(self, "_selected_history_idx", 0)
        if idx == 0:
            return t.session_for(self._cp_segment) if t else None
        combo_sessions = getattr(self, "_combo_sessions", [])
        if 0 <= idx < len(combo_sessions) and combo_sessions[idx] is not None:
            return combo_sessions[idx]
        _, sessions = self._history_source(t)
        pos = idx - 1
        if 0 <= pos < len(sessions):
            return sessions[len(sessions) - 1 - pos]
        return t.session if t else None

    def _load_past_fight(self, char_name: str, sess: CombatSession) -> None:
        """Load an archived fight into active view and switch to Damage breakdown."""
        self._auto_last_fight = False
        self._auto_last_suppressed = True
        self._browse_fights(char_name or "", [sess], select_idx=0)
        self._set_combat_metric("damage")

    def _delete_past_fight(self, path: str | Path, sess: CombatSession) -> None:
        """Delete one archived fight from its history file + in-memory copies."""
        delete_past_fight(
            self,
            path=path,
            sess=sess,
            active_tracker=self._get_active_tracker(),
            browse_holder=self,
            after_delete_cb=lambda: (
                # Drop the parse cache for this log before the rebuild: a
                # rewrite the (mtime, size) fingerprint cannot see must never
                # leave the deleted fight on screen.
                forget_cached_sessions(path),
                setattr(self, "_cp_past_fights_dirty", True),
                self._sync_history_combo(),
                self._render_combat_breakdown()
            )
        )
