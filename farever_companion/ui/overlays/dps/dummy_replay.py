"""Replay panel for a finished Test Dummy parse.

A dummy board says WHAT the rotation did — the final DPS, the skill split. It
cannot say WHEN the rotation went quiet, which is the other half of "why is my
number not higher": a three-second pause between casts costs exactly as much as
a weak skill, and the finished total hides it. This panel plays the test back
against the clock it was measured on.

The data is already there. `CombatSession.timeline` is a second -> group damage
map that `record_hit` fills on every hit, so a finished test can be replayed
without capturing anything extra: one bar per second of the run, a dashed line
at the run's own average DPS, and the seconds that landed NOTHING marked in
warning red and counted in the header. The bar under the playhead is the current
second; the readouts are the damage banked so far and the DPS it works out to,
so the number the player watches is the same number the HUD already reports —
just moving.

Only a FINISHED test replays (`DummyTestControlMixin._update_dummy_replay`
decides). A live test's timeline grows every second, so a strip of it would only
be a spikier copy of the total two lines above; the replay exists to read a run
that is over.
"""
from __future__ import annotations

import math

from PySide6 import QtCore, QtGui, QtWidgets

from ... import theme

# Strip playback cadence and speed. 40 ms is smooth enough for a bar this size
# without repainting 300 bars 25x a second; 2x keeps a 30 s test watchable
# (15 s) — the point is the SHAPE, not real time.
FRAME_MS = 40
SPEED = 2.0


def _mmss(seconds: float) -> str:
    total = max(0, int(round(float(seconds or 0.0))))
    return f"{total // 60}:{total % 60:02d}"


class _ReplayStrip(QtWidgets.QWidget):
    """The per-second bar strip: damage bars, the average line, the playhead.

    Painted rather than built from widgets on purpose — a 300 s test is 300
    bars, and 300 QFrames rebuilt on a 25 Hz timer is exactly the kind of board
    that makes a HUD feel heavy.
    """

    seeked = QtCore.Signal(float)          # seconds, from a click/drag

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(46)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.bars: list[float] = []
        self.cursor = 0.0
        self.avg = 0.0
        self.duration = 0.0

    def set_series(self, bars: list[float], duration: float, avg: float) -> None:
        self.bars = list(bars)
        self.duration = max(0.0, float(duration))
        self.avg = float(avg)
        self.cursor = 0.0
        self.update()

    def set_cursor(self, seconds: float) -> None:
        self.cursor = max(0.0, float(seconds))
        self.update()

    # --- input -------------------------------------------------------------#
    def _seek(self, x: float) -> None:
        if not self.bars or self.width() <= 0 or self.duration <= 0.0:
            return
        frac = min(1.0, max(0.0, x / self.width()))
        self.seeked.emit(frac * self.duration)

    def mousePressEvent(self, ev):                     # noqa: N802 (Qt naming)
        self._seek(ev.position().x())
        ev.accept()

    def mouseMoveEvent(self, ev):                      # noqa: N802
        if ev.buttons() & QtCore.Qt.LeftButton:
            self._seek(ev.position().x())
            ev.accept()

    # --- paint -------------------------------------------------------------#
    def paintEvent(self, ev):                          # noqa: N802
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, False)
        r = self.rect()
        p.fillRect(r, QtGui.QColor(theme.SURFACE))
        n = len(self.bars)
        if n <= 0:
            return
        peak = max(self.bars) or 1.0
        top = r.top() + 2
        usable = max(1, r.height() - 4)
        w = r.width() / n
        played = theme.ACCENT_LIGHT
        pending = theme.ACCENT_BORDER
        col_played = QtGui.QColor(played)
        col_pending = QtGui.QColor(pending)
        col_gap = QtGui.QColor(theme.DANGER)
        for i, v in enumerate(self.bars):
            x = r.left() + i * w
            bw = max(1.0, w - 1.0) if w >= 3 else w
            if v <= 0.0:
                # A dead second: a flat red stub, so a gap in the rotation is
                # visible even when the bar beside it is tall.
                p.fillRect(QtCore.QRectF(x, r.bottom() - 3, bw, 3), col_gap)
                continue
            h = max(2.0, (v / peak) * usable)
            col = col_played if i < self.cursor else col_pending
            p.fillRect(QtCore.QRectF(x, r.bottom() - h, bw, h), col)
        # The run's own average: bars below it are the seconds that lost ground.
        if peak > 0 and self.avg > 0:
            ay = r.bottom() - (self.avg / peak) * usable
            pen = QtGui.QPen(QtGui.QColor(theme.GOLD))
            pen.setStyle(QtCore.Qt.DashLine)
            pen.setWidth(1)
            p.setPen(pen)
            p.drawLine(QtCore.QPointF(r.left(), ay), QtCore.QPointF(r.right(), ay))
        # The playhead.
        if self.duration > 0:
            cx = r.left() + (self.cursor / self.duration) * r.width()
            p.setPen(QtGui.QPen(QtGui.QColor(theme.TEXT), 1))
            p.drawLine(QtCore.QPointF(cx, r.top()), QtCore.QPointF(cx, r.bottom()))
            p.fillRect(QtCore.QRectF(cx - 1.5, r.top(), 3, 3),
                       QtGui.QColor(theme.TEXT))


class DummyReplay(QtWidgets.QFrame):
    """Play / scrub the finished test's per-second damage.

    Public surface used by the mixin: `update_session(session)` (every 10 Hz
    tick; rebuilds only when the finished run changes), `clear()`. The widget
    decides its own visibility — nothing to replay means hidden, so the board
    does not grow an empty section.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        # Class-scoped like `_StatChip`: a bare `QFrame {{...}}` rule would also
        # hit every QLabel inside (QLabel IS-A QFrame) with a stray border.
        self.setStyleSheet(
            f"DummyReplay {{ background: {theme.PANEL}; border: 1px solid "
            f"{theme.BORDER}; border-radius: 6px; }}")
        self._sig: tuple | None = None
        self._bars: list[float] = []
        self._dur = 0.0
        self._total = 0.0
        self._playing = False
        self._since = 0.0

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(6, 4, 6, 5)
        outer.setSpacing(3)

        head = QtWidgets.QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(6)
        self.play_btn = QtWidgets.QPushButton("▶")
        self.play_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self.play_btn.setFixedWidth(24)
        self.play_btn.setToolTip("Replay this test's damage over time")
        self.play_btn.setStyleSheet(
            f"QPushButton {{ background: {theme.PANEL_LOW}; border: 1px solid "
            f"{theme.BORDER}; border-radius: 3px; color: {theme.ACCENT_LIGHT}; "
            f"font-size: 11px; font-weight: 800; padding: 1px 0; }}"
            f"QPushButton:hover {{ border-color: {theme.ACCENT}; }}")
        self.play_btn.clicked.connect(self.toggle)
        head.addWidget(self.play_btn)

        self.title_lbl = QtWidgets.QLabel("REPLAY")
        self.title_lbl.setStyleSheet(
            f"font-size: 9px; font-weight: 800; letter-spacing: 1px; "
            f"color: {theme.DIM}; background: transparent;")
        head.addWidget(self.title_lbl)
        head.addStretch(1)

        self.gap_lbl = QtWidgets.QLabel("")
        self.gap_lbl.setStyleSheet(
            f"font-size: 9.5px; font-weight: 700; color: {theme.DANGER}; "
            f"background: transparent;")
        head.addWidget(self.gap_lbl)
        outer.addLayout(head)

        self.strip = _ReplayStrip()
        self.strip.seeked.connect(self.seek)
        outer.addWidget(self.strip)

        self.read_lbl = QtWidgets.QLabel("")
        self.read_lbl.setStyleSheet(
            f"font-size: 10px; font-weight: 700; color: {theme.MUTED}; "
            f"font-family: 'Consolas', monospace; background: transparent;")
        outer.addWidget(self.read_lbl)

        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(FRAME_MS)
        self._timer.timeout.connect(self._advance)
        self.hide()

    # --- visibility --------------------------------------------------------#
    def hideEvent(self, ev):                           # noqa: N802 (Qt naming)
        # The panel is hidden whenever the HUD is out of range or the board is
        # live; repainting a hidden strip 25x a second is work nobody asked for.
        self._timer.stop()
        super().hideEvent(ev)

    def showEvent(self, ev):                           # noqa: N802
        if self._playing and len(self._bars) >= 2:
            self._timer.start()
        super().showEvent(ev)

    # --- data --------------------------------------------------------------#
    def update_session(self, session) -> None:
        """Feed the finished test; rebuilds the strip only when it changes."""
        try:
            dur = float(getattr(session, "duration", 0.0) or 0.0)
            total = float(getattr(session, "group_damage", 0.0) or 0.0)
            tl = getattr(session, "timeline", None) or {}
        except Exception:
            return self.clear()
        if dur < 2.0 or total <= 0.0 or not tl:
            return self.clear()
        sig = (round(dur, 1), int(total), len(tl))
        if sig == self._sig:
            return                                   # same finished run
        self._sig = sig
        self._bars = self._dense(tl, dur)
        # The strip is measured in the SECONDS IT HOLDS, not the session's
        # timestamp span: the last hit opens a final second the span stops
        # short of, and a cursor that cannot reach the last bar would hide it.
        self._dur = float(len(self._bars))
        self._total = total
        self._set_gaps()
        self.strip.set_series(self._bars, self._dur, total / max(1.0, self._dur))
        self.show()
        self.seek(0.0)
        self.play()                                  # a new finished run plays

    def clear(self) -> None:
        self._timer.stop()
        self._playing = False
        self._sig = None
        self._bars = []
        self._dur = self._total = 0.0
        self.strip.set_series([], 0.0, 0.0)
        self.hide()

    @staticmethod
    def _dense(timeline: dict, dur: float) -> list[float]:
        """Second -> damage map as a dense series (a missing second is 0.0).

        Spans the LARGER of the timestamp duration and the last recorded
        second: `record_hit` stamps `int(now - start)`, so a hit at the end of
        a 4.0 s test lands in second 4 — a ceil(dur)=4 series would drop it.
        """
        end = int(math.ceil(dur))
        try:
            end = max(end, int(max(timeline)) + 1)
        except (TypeError, ValueError):
            pass
        return [float(timeline.get(i, 0.0) or 0.0) for i in range(max(1, end))]

    def _set_gaps(self) -> None:
        """Name the run's worst dead stretch: how long, and where it started."""
        best = (0, 0)                                # (length, start second)
        run_start = None
        for i, v in enumerate(self._bars):
            if v <= 0.0:
                run_start = i if run_start is None else run_start
            else:
                if run_start is not None and (i - run_start) > best[0]:
                    best = (i - run_start, run_start)
                run_start = None
        if run_start is not None and (len(self._bars) - run_start) > best[0]:
            best = (len(self._bars) - run_start, run_start)
        if best[0] <= 0:
            self.gap_lbl.setText("no dead seconds")
            self.gap_lbl.setStyleSheet(
                f"font-size: 9.5px; font-weight: 700; color: {theme.GOOD}; "
                f"background: transparent;")
        else:
            self.gap_lbl.setText(f"worst gap {best[0]}s @ {_mmss(best[1])}")
            self.gap_lbl.setStyleSheet(
                f"font-size: 9.5px; font-weight: 700; color: {theme.DANGER}; "
                f"background: transparent;")

    # --- playback ----------------------------------------------------------#
    def toggle(self) -> None:
        if self._playing:
            self._pause()
        else:
            self.play()

    def play(self) -> None:
        if len(self._bars) < 2:
            return
        if self._since >= self._dur:
            self.seek(0.0)
        self._playing = True
        self.play_btn.setText("❚❚")
        self._timer.start()
        self._render()

    def _pause(self) -> None:
        self._playing = False
        self.play_btn.setText("▶")
        self._timer.stop()

    def seek(self, seconds: float) -> None:
        self._since = min(self._dur, max(0.0, float(seconds)))
        self.strip.set_cursor(self._since)
        self._render()

    def _advance(self) -> None:
        self._since += (FRAME_MS / 1000.0) * SPEED
        if self._since >= self._dur:
            self._since = self._dur
            self.strip.set_cursor(self._since)
            self._render()
            self._pause()
            return
        self.strip.set_cursor(self._since)
        self._render()

    def _render(self) -> None:
        sec = int(self._since)
        banked = sum(self._bars[:sec])
        dps = banked / max(1.0, self._since)
        self.read_lbl.setText(
            f"{_mmss(self._since)} / {_mmss(self._dur)}   "
            f"{banked:,.0f} dmg   {dps:,.0f}/s")
