"""The Test Dummy HUD: the Top DPS meter stripped to a training-dummy parse.

A dummy is where a build gets tested, and a test has its own shape: ONE session
(the yard's dummies share it — that is what keeps the total right), one pinned
target whose health bar the board follows, a per-target split when the yard is a
cluster, the per-skill breakdown as the point of the exercise, and the test's own
Stop / Start control. None of the meter's dungeon furniture is here to read past:
no mode badge, no DMG / HEAL / BOTH view pill. The two CHANNEL chips it does
carry (HEALING and TAKEN) are the meter's own `_StatChip`, so a test's sustain
and its clean-run number read exactly like the meter's channels do.

It is a SEPARATE WINDOW rather than a meter mode, and the manager shows it only
while a dummy is in range — and never inside a dungeon/rift, where a dummy test
has nothing to attach to and the window would only sit over the Dungeon HUD
(Rule F: the `"dummy"` row of `overlay_rules.HUD_PLACES`, which carries Rule
D's instance gate too). This window is the only surface that renders a dummy
test, and the meter carries no dummy logic at all: with "Instances Only" on it
stays in instances even at a dummy, so the bench here never pops-then-closes
the meter when this board surfaces.

It sizes ITSELF to the rows it holds, the way the entity and dungeon HUDs do:
the body is a clipped scroll area, and a test board opens every skill row it has,
so a fixed height would have cut the bottom of the parse off. See
`_sync_auto_height` / `_ingame_height`.

It reuses rather than reimplements: the Stop / Start Test control, its latch sync,
the empty-state line, the per-dummy split and the vs-last-test baseline all come
from `dps/dummy_ui.py`, the ranked player rows from `dps/rows.py`, and the capture
badge from `dps/widgets.py`. Only the assembly and the per-tick render live
here; the dummy's RULES live in `core/dps_dummy.py` and its recognition in
`data/units.py`.
"""
from __future__ import annotations

import time

from PySide6 import QtCore, QtWidgets

from .. import theme
from ..overlay_base import OverlayWindow
from ...core.dps_dummy import DUMMY_REARM_S
from ...core.dps_tracker import (PlayerParse, own_row,
                                 solo_only_view, view_totals)
from ..dps_source_text import (capture_badge, emit_capture_diagnostic,
                               empty_hint, with_report_hint)
from .entity_rows import _Section, _scroll_body
from ..affinity_view import (affinity_rows, affinity_tooltip_html, pd_md_split,
                             player_affinity)
from ...data.items import labels as item_labels
from .dps.widgets import _CaptureBadge, insert_into_titlebar
from .dps.widgets import (adopt_live_tracker, aim_log_sinks,
                          install_dense_chrome, install_dps_sources,
                          install_titlebar_chrome)
from .dps.dummy_ui import DummyTestControlMixin
from .dps.rows import _PlayerRowWidget
from .dps.widgets import _StatChip

TICK_MS = 100   # the meter's cadence: both surfaces must agree tick for tick

# When the countdown to the target length starts warning, in seconds LEFT. The
# amber tier is deliberately generous: the last stretch of a dummy test is where
# a rotation can still be closed, and a warning that first appears in the final
# five seconds arrives too late to change what the run measures.
CLOCK_WARN_AMBER_S = 20.0
CLOCK_WARN_RED_S = 10.0


def _mmss(seconds: float) -> str:
    """`93` -> `1:33`, `27.4` -> `0:27` — a countdown reads in whole seconds.

    Whole seconds and no padded minutes, so the big clock and the elapsed
    readout below it read the same way: `0:48`, never `00:48.3`. A timer that
    reports tenths it cannot be acted on is harder to read at a glance than the
    second it actually holds.
    """
    total = max(0, int(round(float(seconds or 0.0))))
    return f"{total // 60}:{total % 60:02d}"


class DummyOverlay(DummyTestControlMixin, OverlayWindow):
    """Compact training-dummy parse HUD (the "Test Dummy HUD" card)."""

    # Diagnostics forwarded to the app's Activity Log (same sink as the meter).
    log = QtCore.Signal(str)

    def __init__(self, model, settings, parent=None):
        super().__init__("Test Dummy", settings, geo_key="dummy", parent=parent)
        # Dense like the meter: a test parse is the numbers, not the chrome.
        install_dense_chrome(self)
        install_dps_sources(self, model, settings)
        # Per-player UI state, owned here so it survives row rebuilds (the meter
        # keeps the same two maps for the same reason).
        self._row_widgets: dict[str, _PlayerRowWidget] = {}
        self._row_collapsed: dict[str, bool] = {}
        self._skills_show_all: dict[str, bool] = {}

        install_titlebar_chrome(self)
        self.titlebar.title.setText(
            f"<span style='color:{theme.GOLD}; font-size:11px;'>●</span>"
            f"&nbsp;&nbsp;TEST DUMMY")

        # Capture-source badge, same widget and same status vocabulary as the
        # meter: a dummy test that reads zeros is the worst possible failure.
        self.cap_badge = _CaptureBadge()
        insert_into_titlebar(self.cap_badge, self.titlebar.extra)

        tgt_row = QtWidgets.QHBoxLayout()
        tgt_row.setContentsMargins(0, 0, 0, 0)
        tgt_row.setSpacing(6)
        self.target_lbl = QtWidgets.QLabel("Target: —")
        self.target_lbl.setStyleSheet(
            f"font-size: 11px; font-weight: 700; color: {theme.TEXT};")
        self.target_hp_lbl = QtWidgets.QLabel("")
        self.target_hp_lbl.setStyleSheet(
            f"font-size: 10px; color: {theme.DIM}; font-family: monospace;")
        tgt_row.addWidget(self.target_lbl)
        tgt_row.addStretch(1)
        tgt_row.addWidget(self.target_hp_lbl)
        self.content.addLayout(tgt_row)

        self.target_bar = QtWidgets.QProgressBar()
        self.target_bar.setFixedHeight(7)
        self.target_bar.setTextVisible(False)
        self.target_bar.setStyleSheet(
            "QProgressBar { background: rgba(0, 0, 0, 0.4); border: 1px solid "
            "rgba(255, 255, 255, 0.08); border-radius: 3px; }"
            "QProgressBar::chunk { background: qlineargradient(x1:0,y1:0,x2:1,y2:0, "
            "stop:0 #ef4444, stop:0.7 #f97316, stop:1 #eab308); border-radius: 2px; }")
        self.target_bar.setRange(0, 100)
        self.target_bar.setValue(0)
        self.content.addWidget(self.target_bar)

        # The test's headline numbers: DPS first and biggest, then total damage,
        # the clock, hits. The total carries its own unit because the number
        # beside it is a RATE: a bare "39,900" next to "39,900.0/s" is two
        # numbers that look like the same measure twice, not a rate and the
        # total it was averaged from.
        stats_row = QtWidgets.QHBoxLayout()
        stats_row.setContentsMargins(0, 2, 0, 0)
        stats_row.setSpacing(10)
        self.dps_lbl = QtWidgets.QLabel("0.0/s")
        self.dps_lbl.setStyleSheet(
            f"font-size: 20px; font-weight: 800; color: {theme.ACCENT_LIGHT}; "
            f"font-family: monospace;")
        self.dmg_lbl = QtWidgets.QLabel("0 dmg")
        self.dmg_lbl.setStyleSheet(
            f"font-size: 12px; font-weight: 800; color: {theme.TEXT}; "
            f"font-family: monospace;")
        # The countdown to the test's target length — the number that makes a
        # run comparable to the last one. It counts the SESSION's clock, which
        # does not start until the first hit lands, so walking up to an idle
        # dummy leaves the whole target ahead of you.
        self.clock_lbl = QtWidgets.QLabel("0:30")
        self.clock_lbl.setStyleSheet(
            f"font-size: 15px; font-weight: 800; color: {theme.ACCENT_LIGHT}; "
            f"font-family: monospace;")
        self.clock_lbl.setToolTip(
            "Time left in this test's target length.\nThe test ends itself at "
            "zero, so the run is always the same length.\nSet the length to 0 "
            "in Settings > DPS > Test Dummy to run free.")
        stats_row.addWidget(self.dps_lbl, 0, QtCore.Qt.AlignBottom)
        stats_row.addWidget(self.dmg_lbl, 0, QtCore.Qt.AlignBottom)
        stats_row.addStretch(1)
        stats_row.addWidget(self.clock_lbl, 0, QtCore.Qt.AlignBottom)
        self.content.addLayout(stats_row)

        # Progress to the target: the bar under the clock that shares the target
        # HP bar's slot, so "how much of the test is left" is a glance, not a
        # subtraction.
        self.time_bar = QtWidgets.QProgressBar()
        self.time_bar.setFixedHeight(4)
        self.time_bar.setTextVisible(False)
        self.time_bar.setRange(0, 100)
        self.time_bar.setValue(0)
        self.time_bar.setStyleSheet(
            "QProgressBar { background: rgba(0, 0, 0, 0.4); border: 0; "
            "border-radius: 2px; }"
            "QProgressBar::chunk { background: qlineargradient(x1:0,y1:0,x2:1,y2:0, "
            "stop:0 #38bdf8, stop:1 #eac331); border-radius: 2px; }")
        self.content.addWidget(self.time_bar)

        # PD / MD split: the test's damage merged into physical (Physical +
        # Raw) vs magic (every other tagged school). Hidden until something is
        # classified — an untagged capture grows no line. Hover for the full
        # per-school breakdown.
        self.pdmd_lbl = QtWidgets.QLabel("")
        self.pdmd_lbl.setTextFormat(QtCore.Qt.RichText)
        self.pdmd_lbl.setStyleSheet(
            f"font-size: 11px; font-weight: 700; font-family: monospace; "
            f"color: {theme.DIM}; background: transparent; border: none;")
        self.pdmd_lbl.hide()
        self.content.addWidget(self.pdmd_lbl)

        # The test's two CHANNEL chips — the meter's own widget, each with its
        # per-second rate — because damage alone cannot say whether the rotation
        # sustained itself or whether the yard really was quiet.
        #
        # HEALING is the test's own sustain: yours, and anything landing on you
        # (core/dps_tracker_events routes both into the test, the same way it
        # already routes a hit ON you — a dummy never needs healing itself).
        # TAKEN is the number behind the clean-run strip: a dummy does not fight
        # back, so anything here is the rotation (a dot, a reflect) or the world
        # walking in, and "I measured a clean 30 s" is only true if it stayed at
        # zero. Damage gets no chip: its total and rate are the headline above.
        chip_row = QtWidgets.QHBoxLayout()
        chip_row.setContentsMargins(0, 1, 0, 0)
        chip_row.setSpacing(4)
        self.chip_heal = _StatChip("HEALING")
        self.chip_heal.setToolTip(
            "Healing during this test — yours, and anything landing on you.\n"
            "A dummy never needs healing, so this is the rotation's own "
            "sustain: the same channel the Top DPS meter shows.")
        self.chip_taken = _StatChip("TAKEN")
        self.chip_taken.setToolTip(
            "Damage you took during this test. A clean dummy test takes none — "
            "a non-zero number means the run was not a clean measurement.")
        for c in (self.chip_heal, self.chip_taken):
            chip_row.addWidget(c, 1)
        self.content.addLayout(chip_row)

        # The test's elapsed clock and hit count, on the thin line the taken
        # readout used to share with it.
        sub_row = QtWidgets.QHBoxLayout()
        sub_row.setContentsMargins(0, 1, 0, 0)
        sub_row.setSpacing(8)
        # The idle countdown to the test's own re-arm. The game keeps the player
        # "in combat" at any range inside the yard, so without a test end the
        # board just looked frozen; the rule lives in core (see `_update_rearm`).
        self.rearm_lbl = QtWidgets.QLabel("")
        self.rearm_lbl.setStyleSheet(
            f"font-size: 10px; font-weight: 700; color: {theme.ORANGE};")
        sub_row.addWidget(self.rearm_lbl, 0)
        sub_row.addStretch(1)
        self.sub_lbl = QtWidgets.QLabel("0:00 · 0 hits")
        self.sub_lbl.setStyleSheet(
            f"font-size: 10px; font-weight: 700; color: {theme.DIM};")
        sub_row.addWidget(self.sub_lbl, 0)
        self.content.addLayout(sub_row)

        self.scroll, self._body = _scroll_body()
        self.content.addWidget(self.scroll, 1)

        self.top_box = _Section("TOP DPS", theme.GOLD, header=False)
        # Header-less section: the rows speak for themselves. This board
        # already names itself TEST DUMMY in the title bar and shows the test's
        # total and DPS directly above the rows, so a "TOP DPS · N" title with
        # a second copy of the total in its tag repeated what is two lines up —
        # and a hidden header is still a widget (the devicons element inventory
        # kept listing it), so none is built at all.
        self._body.addWidget(self.top_box)
        # The per-target split (a dummy cluster): built with the rest of the
        # dummy surface, in dummy_control.py.
        self._build_dummy_split(self._body)

        self.empty_lbl = QtWidgets.QLabel("")
        self.empty_lbl.setWordWrap(True)
        self.empty_lbl.setAlignment(QtCore.Qt.AlignCenter)
        self.empty_lbl.setStyleSheet(
            f"color: {theme.DIM}; font-size: 11px; padding: 16px 4px;")
        self._body.addWidget(self.empty_lbl)
        self._body.addStretch(1)

        btn_row = QtWidgets.QHBoxLayout()
        btn_row.setContentsMargins(0, 0, 0, 0)
        btn_row.setSpacing(4)
        _btn_qss = (
            f"QPushButton {{ background: {theme.PANEL_LOW}; border: 1px solid "
            f"{theme.BORDER}; border-radius: 3px; color: {theme.MUTED}; "
            f"font-size: 10px; font-weight: 700; padding: 4px 6px; }}"
            f"QPushButton:hover {{ color: {theme.TEXT}; border-color: {theme.ACCENT}; }}")
        self.reset_btn = QtWidgets.QPushButton("↺ Reset")
        self.reset_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self.reset_btn.setStyleSheet(_btn_qss)
        self.reset_btn.clicked.connect(self.reset)
        btn_row.addWidget(self.reset_btn)
        self.content.addLayout(btn_row)

        # Stop / Start Test. Sits in the same row, so it is built after it.
        self._build_dummy_control(btn_row, _btn_qss)

        self.enable_resize_grip()
        self.setMinimumWidth(240)
        self.setMaximumWidth(460)
        if getattr(self, "_saved_size", None) is None:
            # A drag-resized size was restored above (_restore_geometry);
            # don't stomp it back to the default on every rebuild.
            # 400 tall rather than 360: the HEALING / TAKEN chips took a row of
            # chrome, and a first-run board should show the same number of rows
            # it did before them.
            self.resize(300, 400)

        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(TICK_MS)

    # --- model plumbing ----------------------------------------------------#

    def _dm(self):
        m = self.model
        return getattr(m, "damage", None) if m is not None else None

    def set_model(self, model) -> None:
        self.model = model
        if model is None:
            return
        if getattr(model, "dps", None) is not None:
            self.tracker = model.dps
        elif self.tracker is not None:
            self.tracker.model = model
        aim_log_sinks(self, model, self.tracker)

    # --- per-tick render ---------------------------------------------------#

    def _tick(self):
        if self.model is None:
            return

        # Self-heal tracker binding across reattaches, exactly like the meter:
        # this window can outlive a detach and must not sit on an orphaned
        # tracker showing frozen zeros while hits are flowing.
        try:
            adopt_live_tracker(self)
        except Exception:
            pass

        self.tracker.update()
        # Latch state is synced even while hidden, so the button is right the
        # instant the window surfaces.
        self._sync_dummy_btn()
        if not self.isVisible():
            return

        self.cap_badge_refresh()

        # This HUD is dummy-only by construction: the dummy session IS the test,
        # even while an unrelated open-world fight is in progress elsewhere.
        session = self.tracker.session_for("dummy")
        # `dur` divides damage, so it is the RATE denominator and stays on
        # `duration`; the "m:ss . hits" line below is a clock and takes
        # `wall_duration`, which keeps ticking with no hits landing.
        dur = session.duration
        wall = session.wall_duration
        solo = solo_only_view(self.model, self.s, self.tracker)
        grp_dmg, grp_heals, taken = view_totals(session, solo)
        hits = sum(int(t.hits) for t in session.targets.values())

        self.dmg_lbl.setText(f"{grp_dmg:,.0f} dmg")
        self.dps_lbl.setText(f"{grp_dmg / max(1.0, dur):,.1f}/s")
        self.chip_heal.set_value(f"{grp_heals:,.0f}",
                                 f"{grp_heals / max(1.0, dur):,.1f}/s")
        self.sub_lbl.setText(f"{_mmss(wall)} · {hits:,} hits")
        self._update_clock(session, taken)
        self._update_rearm()

        self._update_target(session)
        self._update_dummy_split(session, dur)
        ranked = self._row_candidates(session, solo)
        self._update_pd_md(ranked)
        self._update_rows(ranked, dur, grp_dmg, grp_heals, solo)
        self._sync_auto_height()

    def _update_clock(self, session, taken: float) -> None:
        """Draw the countdown to the target length and the test's damage taken.

        Both come from the tracker rather than from this window's own clock, so
        the number counting down and the rule that ends the test are the same
        number: `dummy_clock` is what the tracker's auto-end compares. Defensive
        for the same reason as every other render here — this runs at 10 Hz.
        """
        try:
            clock = self.tracker.dummy_clock(session)
        except Exception:
            return
        if not clock.enabled:
            # No target length: free-running, so there is nothing to count down
            # to and a bar stuck at zero would only lie. The elapsed clock and
            # the totals above are the whole report.
            self.clock_lbl.hide()
            self.time_bar.hide()
        else:
            self.clock_lbl.show()
            self.time_bar.show()
            self.time_bar.setValue(int(clock.fraction * 100))
            if clock.reached:
                self._set_clock_text(
                    f"DONE {_mmss(clock.elapsed_s)}", theme.GOLD)
            elif not clock.started:
                # No hit yet, so the session clock has not started and the whole
                # target is still ahead — say so instead of showing a frozen 0:00.
                self._set_clock_text(f"{_mmss(clock.target_s)} ready",
                                     theme.DIM)
            else:
                left = clock.remaining_s
                self._set_clock_text(
                    _mmss(left),
                    (theme.DANGER if left <= CLOCK_WARN_RED_S
                     else theme.ORANGE if left <= CLOCK_WARN_AMBER_S
                     else theme.ACCENT_LIGHT))
        self._set_taken(taken)

    def _update_rearm(self) -> None:
        """Draw the idle countdown to the test's own re-arm.

        A RUNNING test that stops landing hits ends itself after `dummy_rearm_s`
        seconds (core/dps_dummy.py owns that rule), so the board says how long
        rather than looking frozen — the game keeps the player "in combat" at
        any range inside the yard, so there is no fight end on screen to read
        instead. Shown only once the idle window has really begun (a hit within
        the last second is still a fight, not a countdown). 10 Hz, so defensive:
        a surface that raises here kills the timer.
        """
        lbl = self.rearm_lbl
        try:
            if not bool(getattr(self.tracker, "in_dummy_fight", False)):
                lbl.hide()
                return
            rearm_s = float(getattr(self.tracker, "dummy_rearm_s", DUMMY_REARM_S))
            last = float(getattr(self.tracker.dummy_session,
                                 "last_event_time", 0.0) or 0.0)
            if rearm_s <= 0.0 or last <= 0.0:
                lbl.hide()
                return
            left = rearm_s - (time.time() - last)
            if left <= 0.0 or left > rearm_s - 1.0:
                lbl.hide()
                return
            lbl.setText(f"⏳ auto re-arm in {max(0, int(round(left)))}s")
            lbl.show()
        except Exception:
            lbl.hide()

    def _set_clock_text(self, text: str, color: str) -> None:
        self.clock_lbl.setText(text)
        self.clock_lbl.setStyleSheet(
            f"font-size: 15px; font-weight: 800; color: {color}; "
            f"font-family: monospace;")

    def _set_taken(self, taken: float) -> None:
        """Incoming damage for the test: the number behind the clean-run flag.

        Rendered as the meter renders its own TAKEN chip — value plus rate —
        with the value turning red the moment anything lands: a dummy does not
        fight back, so a non-zero number here is the rotation or the world, and
        the board has to say so before the player reads the comparison below it.

        The number, not the flag: the flag is core's rule (`dummy_taken_share`)
        rendered by the shared mixin (see `DirtyRunStrip`), so the meter and this
        window cannot disagree about the same 3%. Two copies of "is this run
        dirty" is how a HUD ends up warning about a run while a record beside it
        quietly believes the same run.
        """
        try:
            amount = max(0.0, float(taken or 0.0))
        except (TypeError, ValueError):
            amount = 0.0
        try:
            dur = max(1.0, float(self.tracker.dummy_clock().elapsed_s))
        except Exception:
            dur = 1.0
        self.chip_taken.set_value(f"{amount:,.0f}", f"{amount / dur:,.1f}/s")
        self.chip_taken.val_lbl.setStyleSheet(
            f"font-size: 15px; font-weight: 900; font-family: 'Consolas', "
            f"monospace; color: {theme.DANGER if amount > 0.0 else '#ffffff'};")

    def cap_badge_refresh(self) -> None:
        """Title-bar badge: whether real events are actually arriving.

        Same widget, same mapping and same click action as the meter's — a dummy
        test that reads zeros must not look like a working test, and a click
        reports the event path to the Activity Log (the stages a test cannot
        distinguish by looking at it), then rescans a stuck capture.
        """
        text, color, tip = capture_badge(self._dm())
        if text:
            self.cap_badge.apply(text, color, with_report_hint(tip))
        else:
            self.cap_badge.clear()
        self.cap_badge.set_click(self._capture_diagnostic if text else None)

    def _capture_diagnostic(self) -> None:
        """Badge click: report the event path, then rescan if capture is stuck."""
        emit_capture_diagnostic(self.model, self.log.emit)
        if capture_badge(self._dm())[0] not in ("⚠", "CAL"):
            return
        dm = self._dm()
        if dm is None:
            return
        try:
            dm.force_wide_rehunt("dummy HUD badge rescan")
        except Exception:
            pass
        try:
            dm.recalibrate()
        except Exception:
            pass

    def _update_target(self, session) -> None:
        """The pinned dummy: its name and its bar (the test's own target)."""
        name = (session.target_name or "").replace("👑 ", "").replace("🎯 ", "")
        if name and name != "None" and session.target_max_hp > 0:
            pct = max(0.0, min(100.0,
                               session.target_hp / session.target_max_hp * 100.0))
            self.target_lbl.setText(f"🎯 {name}")
            self.target_hp_lbl.setText(
                f"{session.target_hp:,.0f} / {session.target_max_hp:,.0f} · "
                f"{pct:.1f}%")
            self.target_bar.setValue(int(pct))
        else:
            self.target_lbl.setText("Target: —")
            self.target_hp_lbl.setText("")
            self.target_bar.setValue(0)

    def _update_pd_md(self, ranked: list[PlayerParse]) -> None:
        """The test's PD / MD line: tagged damage merged into physical
        (Physical + Raw) vs magic (every other tagged school).

        Summed over the same ranked players the rows below show, so the split
        can never disagree with the board. Hidden until something is
        classified; the hover carries the full per-school breakdown.
        """
        damage: dict[str, float] = {}
        total = 0.0
        for p in ranked:
            d, _h, t = player_affinity(p)
            total += t
            for label, amount in d.items():
                damage[label] = damage.get(label, 0.0) + amount
        pd, md, untagged = pd_md_split(damage, total)
        if pd <= 0.0 and md <= 0.0:
            self.pdmd_lbl.hide()
            return
        self.pdmd_lbl.show()
        pd_c = item_labels.affinity_color("Physical")
        md_c = item_labels.affinity_color("Magic")
        html = (f"<font color=\"{pd_c}\">PD {pd:,.0f}</font>"
                f"<font color=\"{theme.DIM}\"> &nbsp;·&nbsp; </font>"
                f"<font color=\"{md_c}\">MD {md:,.0f}</font>")
        if untagged > 0.0:
            html += (f"<font color=\"{theme.DIM}\"> &nbsp;·&nbsp; </font>"
                     f"<font color=\"{theme.MUTED}\">untagged {untagged:,.0f}</font>")
        self.pdmd_lbl.setText(html)
        self.pdmd_lbl.setToolTip(
            affinity_tooltip_html(affinity_rows(damage, total),
                                  title="PD / MD split"))

    def _row_candidates(self, session, solo: bool) -> list[PlayerParse]:
        """Who belongs on a dummy-test board.

        Ranked by damage, capped by the same `dps_top_count` the meter reads;
        a 0-damage bystander only keeps a row when it is the local player (so a
        test never shows an empty board while you are the one hitting).
        """
        limit = int(getattr(self.s, "dps_top_count", 8) or 8) if self.s else 8
        pin_me = bool(getattr(self.s, "dps_pin_me", True)) if self.s else True
        ranked = [p for p in session.ranked_players(view="damage", pin_me=pin_me)[:limit]
                  if p.total_damage > 0 or p.heals > 0 or p.is_me]
        if not ranked and session.players:
            me = own_row(session)
            ranked = [me] if me else []
        if solo:
            ranked = [p for p in ranked if p.is_me]
        return ranked

    def _update_rows(self, ranked: list[PlayerParse], duration: float,
                     group_damage: float, group_heals: float,
                     solo: bool) -> None:
        """Render the ranked rows (the meter's rows, without the modes)."""
        if not ranked:
            # The fallback is the OUT-OF-RANGE line: a dummy the scan can see
            # across the yard is not one this HUD's gate counts (Dummy Range),
            # and an open window at one has to say so rather than invite a test
            # the core read will not own. `_dummy_test_hint` supplies the
            # in-range half.
            self.empty_lbl.setText(
                empty_hint(self._dm(), self._dummy_test_hint(
                    "No training dummy in range.")))
            self.empty_lbl.show()
            self.top_box.hide()
            for w in list(self._row_widgets.values()):
                w.deleteLater()
            self._row_widgets.clear()
            return

        self.empty_lbl.hide()
        self.top_box.show()
        active_names = set()
        # Same global skill budget the meter applies (Settings > HUD > Top DPS >
        # Skill Cap): the two surfaces must not disagree about how many skill
        # rows a player shows.
        budget = max(0, int(getattr(self.s, "dps_skill_cap", 8) or 8) if self.s else 8)
        rows_lay = self.top_box.rows
        hero_status = getattr(self.tracker, "last_hero_status", {}) or {}
        # A solo test shows ONE row, so its skills are the whole point: expand
        # them unless the user has collapsed that row themselves.
        solo_view = bool(len(ranked) == 1 and ranked[0].is_me)

        for idx, p in enumerate(ranked):
            active_names.add(p.name)
            saved = self._row_collapsed.get(p.name)
            row_expanded = (not saved) if saved is not None else bool(
                solo_view and p.is_me)
            if p.name not in self._row_widgets:
                w = _PlayerRowWidget(
                    p.name, is_me=p.is_me, parent=self.top_box,
                    expanded=row_expanded, skill_type_cell=True,
                    show_all_skills=self._skills_show_all.get(p.name, False))
                w.collapseChanged.connect(
                    lambda on, nm=p.name: self._row_collapsed.__setitem__(nm, not on))
                w.showAllChanged.connect(
                    lambda on, nm=p.name: self._skills_show_all.__setitem__(nm, on))
                self._row_widgets[p.name] = w
                rows_lay.addWidget(w)
            else:
                w = self._row_widgets[p.name]
            if w.expanded != row_expanded:
                w.expanded = row_expanded
                w._sync_visibility()
            w.show()

            alloc = None
            if row_expanded:
                alloc = min(budget, len(p.ranked_skills()))
                budget -= alloc
            st = hero_status.get(p.name)
            dead = bool(st is not None and st[0] <= 0.0)
            w.update_stats(idx, p, duration, group_damage, group_heals,
                           mode="damage", skill_budget=alloc, dead=dead)

            current = rows_lay.indexOf(w)
            if current != idx:
                rows_lay.insertWidget(idx, w)

        for name, w in self._row_widgets.items():
            if name not in active_names:
                w.hide()
                if rows_lay.indexOf(w) >= 0:
                    rows_lay.removeWidget(w)

    # --- fit the window to the rows it holds -------------------------------#

    def _sync_auto_height(self) -> None:
        """Ask for a fit-to-content resize when the body no longer measures the
        height the window was fitted to.

        The body is a clipped scroll area with the scrollbar OFF (see
        `_scroll_body`), so a board taller than the window did not scroll — it
        simply lost its bottom rows, which on this surface is the expanded
        per-skill list the window exists to show. The entity and dungeon HUDs
        size themselves the same way; this board needs it more than either,
        because a solo test opens every row it has.

        `_last_body_h` and `_last_chrome` hold what the window's CURRENT size
        was fitted to, written by `_do_auto_height` when that size is applied
        (not here). Asking "does the body still measure that?" rather than "did
        the number change since the last tick" is what makes this
        self-correcting: a tick can read the pre-layout value (Qt caches a
        layout's size hint until the layout is redone, and a row that just
        expanded only dirties its ancestors for the NEXT layout pass), and
        comparing changes against a value that was never applied left an
        expanded row clipped for good. `activate()` below is the other half of
        that: measure the rows now rather than the last layout pass.

        Chrome is watched too, because it is not a constant here: bare mode
        hides the title bar and the overlay font scale grows it, and either one
        leaves the rows short of room without a single row changing. A drag
        does not: it moves the window and the viewport together, so the chrome
        it sees is the one the fit already accounted for, which is what lets a
        drag-resize stick.

        The resize itself is deferred by one event-loop turn (the same
        `singleShot(0)` both of those use): the chrome height is measured off
        the scroll VIEWPORT, and the viewport only has its new size after the
        layout pass this tick scheduled.
        """
        try:
            self._body.activate()
            body_h = self._body.sizeHint().height()
            chrome = self._chrome()
        except (RuntimeError, AttributeError):
            return
        if (body_h == getattr(self, "_last_body_h", None)
                and chrome == getattr(self, "_last_chrome", None)):
            return
        QtCore.QTimer.singleShot(0, self._do_auto_height)

    def _chrome(self) -> int:
        """Everything this board draws that is not its rows: title bar, target
        line and bar, the headline, the channel chips and the buttons. Measured
        as the window minus its scroll viewport rather than guessed as a
        constant, because this HUD has four chrome rows of its own and two of
        them can change size on the user (bare mode strips the title bar, the
        overlay scale grows the rest). A window that has not been laid out yet
        reports zero.
        """
        return max(0, self.height() - self.scroll.viewport().height())

    def _ingame_height(self) -> int:
        """The height this board needs for the rows it holds, capped to the screen.

        The rows plus `_chrome()`, so a chrome row added or removed is part of
        the fit rather than height the rows silently lose. Zero means "no
        answer" (no body yet, or a viewport that has not been laid out), which
        the caller reads as "leave the window alone" — that is what keeps this
        quiet while the window is minimized, where the body is hidden and there
        is no viewport to measure against.
        """
        try:
            body = self._body
            scroll = self.scroll
        except AttributeError:
            return 0
        try:
            if scroll.viewport().height() <= 0:   # not laid out (mid-show)
                return 0
            screen = QtWidgets.QApplication.primaryScreen()
            screen_h = screen.availableGeometry().height() if screen else 900
            needed = body.sizeHint().height() + self._chrome()
            return max(60, min(needed, screen_h - 40))
        except (RuntimeError, AttributeError):
            return 0

    def _do_auto_height(self) -> None:
        """Resize the window to `_ingame_height()` (a no-op when it already
        fits), and record the rows-plus-chrome measurement that size fits so
        `_sync_auto_height` stops asking for it. Also records the height on
        `_base_h`, the same field the Entity HUD keeps its grown size in."""
        try:
            target_h = self._ingame_height()
            self._last_body_h = self._body.sizeHint().height()
            self._last_chrome = self._chrome()
            if target_h and target_h != self.height():
                self._base_h = target_h
                self.resize(self.width(), target_h)
        except (RuntimeError, AttributeError):
            pass

    def refresh_now(self) -> None:
        """Repaint on the spot rather than on the next 100 ms tick.

        Called when a setting this window's own gate reads changes (Dummy
        Range), so the board the player just changed a number under never shows
        the verdict it had before the edit. The window ticks at 10 Hz anyway, so
        this is not about keeping up — it is about the repaint landing in the
        same frame as the edit, which is what makes the setting's effect
        visible at all.

        Hidden windows do nothing: there is no board to correct, and `_tick`
        would return at that same line. The tracker is left alone as well — the
        caller has already handed the new number to the core read, and a second
        `update()` here would only re-enter a pass it did not ask for.
        """
        if not self.isVisible():
            return
        self._tick()

    def reset(self):
        """Clear the parse (the tracker archives it first) and re-arm the test."""
        self.tracker.reset()
        for w in list(self._row_widgets.values()):
            w.deleteLater()
        self._row_widgets.clear()
        self._row_collapsed.clear()
        self._skills_show_all.clear()
        self.empty_lbl.show()
        self.dmg_lbl.setText("0 dmg")
        self.dps_lbl.setText("0.0/s")
        self.chip_heal.set_value("0", "0/s")
        self.pdmd_lbl.setText("")
        self.pdmd_lbl.hide()
        self.sub_lbl.setText("0:00 · 0 hits")
        self.rearm_lbl.setText("")
        self.rearm_lbl.hide()
        self._set_taken(0.0)
        self.target_lbl.setText("Target: —")
        self.target_hp_lbl.setText("")
        self.target_bar.setValue(0)
        self._reset_dummy_split()
        # tracker.reset() re-arms (a fresh meter is an armed one), so the control
        # must flip back to "Stop Test" without waiting for the timer.
        self._sync_dummy_btn()

    def keyPressEvent(self, e):
        if e.key() == QtCore.Qt.Key_Escape:
            e.ignore()
            return
        super().keyPressEvent(e)

    def closeEvent(self, e):
        self._timer.stop()
        super().closeEvent(e)
