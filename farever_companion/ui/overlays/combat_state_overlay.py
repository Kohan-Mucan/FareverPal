"""Combat State debug overlay.

Live readout of the game's OWN combat fields (``Hero.isInCombat``,
``combatId``, ``combatStartTime``, ``combatEndTime``) as resolved by
``PlayerLocator.combat_state`` (reflection-first, fixed-offset fallback).
This is the \"does it do anything?\" window for the DPS tracker's auxiliary
encounter signals: watch ``in_combat`` flip, ``combatId`` rotate between
pulls, and the engine times move while fighting. Reads the model directly
at ~10 Hz (POLL_MS 100, the same cadence as the Top DPS overlay tick) so
it works even when the Top DPS overlay is closed. Read-only.
"""
from __future__ import annotations

import time

from PySide6 import QtCore, QtWidgets

from .. import theme
from ..overlay_base import OverlayWindow

POLL_MS = 100


class _Row(QtWidgets.QFrame):
    """One key/value line: dim uppercase key on the left, monospace value
    (optionally colored) on the right."""

    def __init__(self, key: str, parent=None):
        super().__init__(parent)
        self.setStyleSheet(
            f"_Row {{ background: {theme.PANEL_LOW}; border: 1px solid "
            f"{theme.BORDER}; border-radius: 3px; }}")
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(8, 3, 8, 3)
        lay.setSpacing(6)
        self.key_lbl = QtWidgets.QLabel(key.upper())
        self.key_lbl.setStyleSheet(
            f"font-size: 9px; font-weight: 800; color: {theme.DIM}; "
            f"letter-spacing: 1px;")
        lay.addWidget(self.key_lbl)
        lay.addStretch(1)
        self.val_lbl = QtWidgets.QLabel("—")
        self.val_lbl.setStyleSheet(
            f"font-size: 11px; font-weight: 700; color: {theme.TEXT}; "
            f"font-family: monospace;")
        lay.addWidget(self.val_lbl)

    def set_value(self, text: str, color: str | None = None) -> None:
        self.val_lbl.setText(text or "—")
        col = color or theme.TEXT
        self.val_lbl.setStyleSheet(
            f"font-size: 11px; font-weight: 700; color: {col}; "
            f"font-family: monospace;")


class CombatStateOverlay(OverlayWindow):
    def __init__(self, model, settings, parent=None):
        super().__init__("Combat State", settings, geo_key="combat",
                         parent=parent)
        self.model = model
        self.s = settings
        self._polls = 0
        self._changes = 0
        self._last_sig: tuple | None = None
        self._last_change_at = time.time()
        # Live-duration anchor: the engine's combatStartTime is a monotonic
        # clock ("14697.7 s engine") with no wall-time epoch, so we anchor a
        # wall timestamp the moment the field moves to a (new) fight start
        # and display REAL elapsed seconds from it (same rule as the DPS
        # badge). Raw engine values stay on the START/END rows for diagnosis.
        self._engine_start: float = 0.0    # last combatStartTime (engine s)
        self._wall_start_at: float = 0.0   # wall clock at that fight start

        self.titlebar.title.setText(
            f"<span style='color:{theme.ACCENT}; font-size:11px;'>⚔</span>"
            f"&nbsp;&nbsp;COMBAT STATE")
        self.titlebar.title.setTextFormat(QtCore.Qt.RichText)
        self.titlebar.title.setStyleSheet(
            f"color: {theme.TEXT}; font-weight: 800; font-size: 13px; "
            f"letter-spacing: 1px;")

        # Hero location line: dot + address, so a missing hero lock is
        # obvious (all rows below read as '—' then).
        self.hero_lbl = QtWidgets.QLabel("")
        self.hero_lbl.setStyleSheet(
            f"font-size: 10px; font-weight: 700; color: {theme.MUTED}; "
            f"font-family: monospace;")
        self.content.addWidget(self.hero_lbl)

        self.row_in = _Row("in combat")
        self.row_id = _Row("combat id")
        self.row_start = _Row("combat start")
        self.row_end = _Row("combat end")
        self.row_dur = _Row("encounter dur")
        for r in (self.row_in, self.row_id, self.row_start,
                  self.row_end, self.row_dur):
            self.content.addWidget(r)

        self.row_polls = _Row("polls")
        self.row_changes = _Row("changes")
        self.row_age = _Row("last change")
        for r in (self.row_polls, self.row_changes, self.row_age):
            self.content.addWidget(r)

        self.src_lbl = QtWidgets.QLabel(
            "via PlayerLocator.combat_state — reflection first, fixed "
            "offsets fallback · ~10 Hz (0.1 s)")
        self.src_lbl.setWordWrap(True)
        self.src_lbl.setStyleSheet(
            f"font-size: 9px; color: {theme.DIM};")
        self.content.addWidget(self.src_lbl)

        self.enable_resize_grip()
        self.setMinimumWidth(230)
        self.resize(270, 330)

        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(POLL_MS)

    def _tick(self):
        m = self.model
        if m is None:
            return

        pa = None
        st = None
        try:
            pa = getattr(m, "player_addr", None)
        except Exception:
            pa = None
        try:
            st = m.combat_state()
        except Exception:
            st = None

        self._polls += 1

        if not pa:
            self.hero_lbl.setText("○ hero not located")
            self.hero_lbl.setStyleSheet(
                f"font-size: 10px; font-weight: 700; color: {theme.DIM}; "
                f"font-family: monospace;")
        else:
            self.hero_lbl.setText(f"● hero 0x{pa:X}")
            self.hero_lbl.setStyleSheet(
                f"font-size: 10px; font-weight: 700; color: {theme.GOOD}; "
                f"font-family: monospace;")

        in_combat = None if st is None else st.get("in_combat")
        cid = None if st is None else st.get("combat_id")
        start = float((st or {}).get("combat_start") or 0.0)
        end = float((st or {}).get("combat_end") or 0.0)

        # Change detection: any field moving bumps the counter + timestamp,
        # so a dead/never-changing read is instantly visible. (If the engine
        # combatStartTime turns out to tick CONTINUOUSLY during a fight, the
        # counter will climb every poll - that itself is the diagnosis.)
        sig = (in_combat, cid, round(start, 2), round(end, 2))
        if sig != self._last_sig:
            self._last_sig = sig
            self._changes += 1
            self._last_change_at = time.time()

        # Live-duration anchor (mirrors the tracker's): re-anchor wall time
        # only when combatStartTime moves to a new fight start; reset it
        # when not in combat. A stable field during the fight keeps the
        # timer running instead of resetting every poll.
        if in_combat and start > 0:
            if (self._engine_start <= 0
                    or abs(start - self._engine_start) > 1e-6):
                self._wall_start_at = time.time()
        elif not in_combat:
            self._wall_start_at = 0.0
        self._engine_start = start if start > 0 else 0.0

        if in_combat is True:
            self.row_in.set_value("TRUE", theme.GOOD)
        elif in_combat is False:
            self.row_in.set_value("FALSE", theme.MUTED)
        else:
            self.row_in.set_value("—", theme.DIM)

        self.row_id.set_value(str(cid) if cid is not None else "—")

        if start > 0:
            self.row_start.set_value(f"{start:.1f} s")
        else:
            self.row_start.set_value("0 (no fight)", theme.DIM)
        if end > 0:
            self.row_end.set_value(f"{end:.1f} s")
        else:
            self.row_end.set_value("0 (no fight)", theme.DIM)

        if end > start > 0:
            # fight over: the game's own recorded duration
            self.row_dur.set_value(f"{end - start:.1f} s (game)", theme.GOLD)
        elif in_combat is True and self._wall_start_at > 0:
            # LIVE ticking elapsed since the game's fight start
            live = max(0.0, time.time() - self._wall_start_at)
            self.row_dur.set_value(f"{live:.1f} s", theme.GOOD)
        else:
            self.row_dur.set_value("—", theme.DIM)

        self.row_polls.set_value(str(self._polls))
        self.row_changes.set_value(
            str(self._changes),
            theme.ACCENT_LIGHT if self._changes else theme.DIM)
        age = time.time() - self._last_change_at
        self.row_age.set_value(f"{age:.1f} s ago")

    def closeEvent(self, e):
        self._timer.stop()
        super().closeEvent(e)