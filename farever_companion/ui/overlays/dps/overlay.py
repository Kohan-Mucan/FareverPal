"""Top DPS Meter Overlay (Dungeon-HUD style).

Floating overlay displaying real-time combat metrics, target health bar,
group statistics chips, and ranked player meters with expandable skill
breakdowns. The player rows live in ui/overlays/dps/rows.py; this module
keeps the overlay window, chrome and the per-tick pipeline. It is the
lightweight sibling of the Combat & DPS Analysis page — the HUD shows the
same real events in a compact, HUD-shaped form.

The training-dummy TEST is deliberately not here: no Stop / Start control, no
per-dummy split, no vs-last-test baseline, no dummy-specific empty state. All
of that is the standalone Test Dummy HUD (`overlays/dummy_overlay.py`,
rendering through `dps/dummy_ui.py`). The meter used to carry a second copy of
that surface for the case where the dummy HUD was off, which meant every dummy
feature existed twice and the two boards could stack on one dummy; the dummy
HUD is now the only surface that renders a dummy test.
"""
from __future__ import annotations

from PySide6 import QtCore, QtWidgets

from ... import theme
from ...components import ElideLabel as _ElideLabel
from ...overlay_base import OverlayWindow
from ....core import game_state
from ....core.dps_tracker import (PlayerParse, own_row, solo_status,
                                  solo_only_view, view_totals)
from ...dps_source_text import (capture_badge, dungeon_mode_badge, empty_hint,
                               instance_mode, mode_tooltip,
                               emit_capture_diagnostic, with_report_hint)
from . import widgets as _dps_widgets
from .widgets import _CaptureBadge, insert_into_titlebar
from .widgets import (adopt_live_tracker, aim_log_sinks, install_dense_chrome,
                      install_dps_sources, install_titlebar_chrome)
from ..entity_rows import _Section, _scroll_body
from .rows import _PlayerRowWidget
from .widgets import _ModePill, _StatChip  # noqa: F401  (re-exported)

TICK_MS = 100  # 10 FPS smooth, flicker-free UI refresh
# Global skill budget: total skill rows across all players, top rank first.
GLOBAL_SKILL_CAP = 8


class DpsOverlay(OverlayWindow):
    # Roster join/leave lines forwarded to the app's Activity Log.
    log = QtCore.Signal(str)

    def __init__(self, model, settings, parent=None):
        super().__init__("Top DPS", settings, geo_key="dps", parent=parent)
        install_dense_chrome(self)     # dense meter: more rows fit
        install_dps_sources(self, model, settings)
        self._page_key = "settings:dps"
        self._view_mode = getattr(settings, "dps_view", "damage") if settings else "damage"
        self._row_widgets: dict[str, _PlayerRowWidget] = {}
        # Overlay-owned per-player UI state; survives ticks and rebuilt rows.
        self._row_collapsed: dict[str, bool] = {}      # name -> chevron collapsed
        self._skills_show_all: dict[str, bool] = {}    # name -> top-5 list expanded

        self._VIEW_TO_PILL = {"damage": "DMG", "healing": "HEAL", "both": "BOTH"}
        self._PILL_TO_VIEW = {"DMG": "damage", "HEAL": "healing", "BOTH": "both"}

        install_titlebar_chrome(self)
        # green status dot + TOP DPS (mockup header) in the title bar.
        # _set_title() owns the text: the base header plus the live dungeon
        # mode badge (· NORMAL / HARD / HEROIC) once the model can read one.
        self._title_base = (
            f"<span style='color:{theme.GOOD}; font-size:11px;'>●</span>"
            f"&nbsp;&nbsp;TOP DPS")
        self._title_state = object()   # sentinel: forces the first paint
        self._set_title(None)

        # Capture-source badge: LIVE / CAL / IDLE / ⚠ at a glance, next to the
        # title. Maps dm.status() through dps_source_text.capture_badge so it
        # can never disagree with the settings-page status line. Hidden when
        # there is nothing to say (no source attached, capture off).
        self.cap_badge = _CaptureBadge()
        insert_into_titlebar(self.cap_badge, self.titlebar.extra)

        # View-mode pill. It used to live in the title bar, but that bar only
        # has ~60px free at the default 320px width, which squeezed the three
        # DMG/HEAL/BOTH chips into each other (overlapping, so mouse clicks
        # hit the wrong chip). It now owns the top row of the body where the
        # full overlay width is available.
        self.mode_pill = _ModePill(
            current=self._VIEW_TO_PILL.get(
                (getattr(self.s, "dps_view", "damage") or "damage"), "DMG")
            if self.s else "DMG")
        self.mode_pill.currentChanged.connect(self._set_mode)
        self.content.insertWidget(0, self.mode_pill)

        status_row = QtWidgets.QHBoxLayout()
        status_row.setContentsMargins(0, 0, 0, 0)
        status_row.setSpacing(4)
        self.seg_lbl = QtWidgets.QLabel("")
        self.seg_lbl.setStyleSheet(
            f"font-size: 10px; font-weight: 700; color: {theme.DIM};")
        sep_lbl = QtWidgets.QLabel("·")
        sep_lbl.setStyleSheet(
            f"font-size: 10px; font-weight: 700; color: {theme.BORDER};")
        self.state_lbl = QtWidgets.QLabel("READY")
        self.state_lbl.setStyleSheet(
            f"font-size: 10px; font-weight: 800; color: {theme.MUTED};")
        self.time_lbl = QtWidgets.QLabel("00:00.0")
        self.time_lbl.setStyleSheet(
            f"font-size: 17px; font-weight: 700; color: {theme.ACCENT_LIGHT}; "
            f"font-family: monospace;")
        status_row.addWidget(self.seg_lbl)
        status_row.addWidget(sep_lbl)
        status_row.addWidget(self.state_lbl)
        # Game's own combat state (independent of our session heuristic).
        self.game_lbl = _ElideLabel("", 130)
        self.game_lbl.setStyleSheet(
            f"font-size: 10px; font-weight: 700; color: {theme.DIM};")
        status_row.addWidget(self.game_lbl)
        # Elides instead of crowding the timer out.
        self.group_lbl = _ElideLabel("", 170)
        self.group_lbl.setStyleSheet(
            f"font-size: 10px; font-weight: 600; color: {theme.DIM};")
        status_row.addWidget(self.group_lbl)
        status_row.addStretch(1)
        status_row.addWidget(self.time_lbl)
        self.content.addLayout(status_row)

        chip_row = QtWidgets.QHBoxLayout()
        chip_row.setContentsMargins(0, 0, 0, 0)
        chip_row.setSpacing(4)
        self.chip_dmg = _StatChip("GROUP DMG")
        self.chip_heal = _StatChip("HEALING")
        self.chip_taken = _StatChip("TAKEN")
        for c in (self.chip_dmg, self.chip_heal, self.chip_taken):
            chip_row.addWidget(c, 1)
        self.content.addLayout(chip_row)

        boss_row = QtWidgets.QHBoxLayout()
        boss_row.setContentsMargins(0, 0, 0, 0)
        boss_row.setSpacing(6)
        self.target_lbl = QtWidgets.QLabel("Target: None")
        self.target_lbl.setStyleSheet(
            f"font-size: 11px; font-weight: 700; color: {theme.TEXT};")
        self.target_hp_lbl = QtWidgets.QLabel("")
        self.target_hp_lbl.setStyleSheet(
            f"font-size: 10px; color: {theme.DIM}; font-family: monospace;")
        boss_row.addWidget(self.target_lbl)
        boss_row.addStretch(1)
        boss_row.addWidget(self.target_hp_lbl)
        self.content.addLayout(boss_row)

        self.target_bar = QtWidgets.QProgressBar()
        self.target_bar.setFixedHeight(7)
        self.target_bar.setTextVisible(False)
        self.target_bar.setStyleSheet(
            "QProgressBar { background: rgba(0, 0, 0, 0.4); border: 1px solid rgba(255, 255, 255, 0.08); border-radius: 3px; }"
            "QProgressBar::chunk { background: qlineargradient(x1:0,y1:0,x2:1,y2:0, "
            "stop:0 #ef4444, stop:0.7 #f97316, stop:1 #eab308); border-radius: 2px; }")
        self.target_bar.setRange(0, 100)
        self.target_bar.setValue(0)
        self.content.addWidget(self.target_bar)

        # Dungeon-HUD body: a scroll area holding one section per metric group.
        self.scroll, self._body = _scroll_body()
        self.content.addWidget(self.scroll, 1)

        self.dps_top_box = _Section("TOP DPS", theme.GOLD)
        self._body.addWidget(self.dps_top_box)

        self.empty_lbl = QtWidgets.QLabel("Hit an enemy or heal party to start.")
        self.empty_lbl.setAlignment(QtCore.Qt.AlignCenter)
        self.empty_lbl.setStyleSheet(f"color: {theme.DIM}; font-size: 11px; padding: 20px 0;")
        self._body.addWidget(self.empty_lbl)

        self._bottom_spacer = QtWidgets.QSpacerItem(
            0, 6, QtWidgets.QSizePolicy.Minimum, QtWidgets.QSizePolicy.Fixed)
        self._body.addSpacerItem(self._bottom_spacer)
        self._body.addStretch(1)

        btn_row = QtWidgets.QHBoxLayout()
        btn_row.setContentsMargins(0, 0, 0, 0)
        btn_row.setSpacing(4)
        _btn_qss = (
            f"QPushButton {{ background: {theme.PANEL_LOW}; border: 1px solid {theme.BORDER}; "
            f"border-radius: 3px; color: {theme.MUTED}; font-size: 10px; "
            f"font-weight: 700; padding: 4px 6px; }}"
            f"QPushButton:hover {{ color: {theme.TEXT}; border-color: {theme.ACCENT}; }}")
        self.reset_btn = QtWidgets.QPushButton("↺ Reset")
        self.reset_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self.reset_btn.setStyleSheet(_btn_qss)
        self.reset_btn.clicked.connect(self.reset)
        btn_row.addWidget(self.reset_btn)

        self.pause_btn = QtWidgets.QPushButton("❚❚ Pause")
        self.pause_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self.pause_btn.setStyleSheet(_btn_qss)
        self.pause_btn.clicked.connect(self.toggle_pause)
        btn_row.addWidget(self.pause_btn)

        # Solo: only-my-DPS applies to open-world solo play. Mirrors the
        # HUD's tab > Top DPS card toggle ("Solo Only"); flipping either side
        # updates the other (button state is re-synced from settings every
        # tick). Inside a dungeon/rift everyone always shows, so the button
        # auto-disables there.
        self.solo_btn = QtWidgets.QPushButton("👤 Solo")
        self.solo_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self.solo_btn.setCheckable(True)
        self.solo_btn.setChecked(bool(getattr(self.s, "dps_solo_only", True)))
        solo_sel_qss = _btn_qss + (
            f"QPushButton:checked {{ background: {theme.ACCENT_DIM}; color: {theme.ACCENT}; "
            f"border: 1px solid {theme.ACCENT}; }}"
            f"QPushButton:disabled {{ color: rgba(255,255,255,0.30); "
            f"border: 1px solid rgba(255,255,255,0.06); background: transparent; }}")
        self.solo_btn.setStyleSheet(solo_sel_qss)
        self.solo_btn.clicked.connect(self._toggle_solo)
        btn_row.addWidget(self.solo_btn)

        self.content.addLayout(btn_row)

        self.enable_resize_grip()
        self.setMinimumWidth(260)
        self.setMaximumWidth(500)
        if getattr(self, "_saved_size", None) is None:
            # A drag-resized size was restored above (_restore_geometry);
            # don't stomp it back to the default on every rebuild.
            self.resize(320, 420)

        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(TICK_MS)

    def _set_mode(self, opt: str):
        mode = self._PILL_TO_VIEW.get((opt or "").upper())
        if not mode:
            return
        self._view_mode = mode
        if self.s:
            self.s.dps_view = mode
            self.s.save()

    def _toggle_solo(self):
        """Flip the solo-only view from the overlay and persist it; the
        Settings > DPS toggle picks it up via the shared settings object."""
        on = self.solo_btn.isChecked()
        if self.s:
            self.s.dps_solo_only = on
            self.s.save()
        self._tick()

    def set_model(self, model) -> None:
        self.model = model
        if model is None:
            return
        if getattr(model, "dps", None) is not None:
            self.tracker = model.dps
        elif self.tracker is not None:
            self.tracker.model = model
        aim_log_sinks(self, model, self.tracker)

    def _dm(self):
        m = self.model
        return getattr(m, "damage", None) if m is not None else None

    def _ready_color(self) -> str:
        """READY label color by capture state: green when the meter is ready
        to record, gold while the capture is still loading/calibrating (or
        needs its DLL installed), red when this build cannot capture at all,
        muted when there is no source to judge."""
        dm = self._dm()
        if dm is None:
            return theme.MUTED
        try:
            st = dm.status()
        except Exception:
            return theme.MUTED
        if st in ("locating", "mapping", "missing_proxy", "need_inject"):
            return theme.GOLD
        if st == "inject_failed":
            return theme.DANGER
        if st == "noscan":
            return theme.DANGER
        return theme.GOOD

    def reset(self):
        self.tracker.reset()
        for w in list(self._row_widgets.values()):
            w.deleteLater()
        self._row_widgets.clear()
        self._row_collapsed.clear()
        self._skills_show_all.clear()
        self.empty_lbl.show()
        self.time_lbl.setText("00:00.0")
        self.seg_lbl.setText("")
        self.state_lbl.setText("READY")
        self.state_lbl.setStyleSheet(
            f"font-size: 10px; font-weight: 800; color: {self._ready_color()};")
        self.game_lbl.set_full("")
        self.chip_dmg.set_rate("0.0/s", "0 total")
        self.chip_heal.set_rate("0.0/s", "0 total")
        self.chip_taken.set_rate("0.0/s", "0 total")
        self.target_lbl.setText("Target: None")
        self.target_hp_lbl.setText("")
        self.target_bar.setValue(0)

    def toggle_pause(self):
        session = self.tracker.session
        if session.state == "COMBAT":
            session.pause()
            self.pause_btn.setText("▶ Resume")
        elif session.state == "PAUSED":
            session.resume()
            self.pause_btn.setText("❚❚ Pause")

    def _retint(self, accent: str) -> None:
        """Re-tint pill and leader-row accents."""
        if hasattr(self, "mode_pill"):
            self.mode_pill.retint(accent)
        for w in getattr(self, "_row_widgets", {}).values():
            w.retint(accent)

    def _tick(self):
        if self.model is None:
            return

        # Self-heal tracker binding: the Combat page reads model.dps live,
        # but this overlay can be left ticking an orphaned tracker across
        # reattaches/mode churn (frozen 00:00.0 while HITs flow).
        try:
            adopt_live_tracker(self)
        except Exception:
            pass

        self.tracker.update()

        # Settings sync runs even while hidden: the view mode must be right
        # the instant the overlay is shown rather than one tick later. Only
        # the rendering below is skipped when hidden.
        if self.s is not None:
            want_view = getattr(self.s, "dps_view", "damage") or "damage"
            if want_view != self._view_mode:
                self._view_mode = want_view
                self.mode_pill.setCurrentText(
                    self._VIEW_TO_PILL.get(want_view, "DMG"))

        # The target line and the segment name describe WHERE THE RUN IS, not
        # which rows are on screen, so they refresh even while the meter is
        # hidden - a board opened mid-pull shows the right fight instead of
        # whatever was current the last time it was visible. The rows below
        # stay tick-only (that is the expensive half).
        self._update_target_lbl(self.tracker.session)
        self.seg_lbl.setText((self.tracker.session.name or "Fight").upper())

        if not self.isVisible():
            return

        # The meter is often restricted to instances (dps_instances_only), and
        # in an instance BOTH halves of solo are inert: the open-world data
        # filter returns "not solo", and solo_only_view declines, so the button
        # was a live-looking control that could not do anything - greyed out
        # and unchecked, still occupying the titlebar. With the meter pinned to
        # instances it is not part of this HUD at all, and gone is the honest
        # state. It returns if instances-only is switched off, because out in
        # the open world the filter is the only thing keeping a stranger's fight
        # two fields over out of your session.
        self.solo_btn.setVisible(
            not bool(getattr(self.s, "dps_instances_only", False)))

        # Solo filter applies to the open world only: inside a dungeon/rift
        # the meter always shows everyone. Auto-disable + uncheck the button
        # there (the saved preference is untouched) so it re-arms outside.
        if self._in_instance():
            self.solo_btn.setEnabled(False)
            if self.solo_btn.isChecked():
                self.solo_btn.blockSignals(True)
                self.solo_btn.setChecked(False)
                self.solo_btn.blockSignals(False)
        else:
            self.solo_btn.setEnabled(True)
            want_solo = bool(getattr(self.s, "dps_solo_only", True))
            if self.solo_btn.isChecked() != want_solo:
                self.solo_btn.blockSignals(True)
                self.solo_btn.setChecked(want_solo)
                self.solo_btn.blockSignals(False)

        session = self.tracker.session

        # The timer ticks from COMBAT ENTRY, not from the first decoded hit:
        # a fight whose capture is still blind is still a fight, so the armed
        # clock owns the display until a session does. The session then starts
        # from that same anchor, so the number does not jump when the first
        # hit lands.
        armed = None
        try:
            armed = self.tracker.armed_fight()
        except Exception:
            armed = None

        # TWO clocks, and they are not interchangeable (2026-10-03).
        #
        # `timer` drives the time LABEL: it is the wall-clock span, which is
        # the whole point of a fight timer - it must keep advancing between
        # hits, and the "armed" clock carries it before the first hit decodes.
        #
        # `dur` is the RATE denominator for the chips and every player row, so
        # it is the event-anchored `duration` - the same span DPS Analysis
        # divides by, and the same one the archive will use. Feeding the wall
        # span into a rate is exactly the 600-vs-637 split this session fixed
        # in CombatSession.duration: after a pause the wall span is longer,
        # so the overlay read low while DPS Analysis read right.
        timer = session.wall_duration
        if armed is not None:
            timer = armed[0]
        dur = session.duration
        if armed is not None and dur <= 0.0:
            # Nothing decoded yet: the armed clock is the only span there is,
            # and a displayed 0/s under a running timer would be a lie.
            dur = armed[0]
        mins = int(timer // 60)
        secs = timer % 60
        self.time_lbl.setText(f"{mins:02d}:{secs:04.1f}")

        seg_name = ((armed[1] if armed is not None else session.name)
                    or "Fight").upper()
        self.seg_lbl.setText(seg_name)
        if session.state == "COMBAT" or armed is not None:
            self.state_lbl.setText("IN COMBAT")
            self.state_lbl.setStyleSheet(
                f"font-size: 10px; font-weight: 800; color: {theme.GOOD};")
        elif session.state == "PAUSED":
            self.state_lbl.setText("PAUSED")
            self.state_lbl.setStyleSheet(
                f"font-size: 10px; font-weight: 800; color: {theme.GOLD};")
        else:
            self.state_lbl.setText("READY")
            self.state_lbl.setStyleSheet(
                f"font-size: 10px; font-weight: 800; color: {self._ready_color()};")

        self._update_game_combat_lbl()
        self._update_group_lbl()
        self._update_capture_badge()
        # Dungeon mode badge in the title bar (· NORMAL / HARD / HEROIC), by
        # the SAME resolution the Run Timer uses (see
        # dps_source_text.instance_mode): inside an instance that is the game's
        # own read when it has one and the manual fallback when it does not.
        # Reading dungeon_mode() alone left this blank on a build whose config
        # read is dead, while the Run Timer still named a mode (2026-09-30).
        try:
            self._set_title(*instance_mode(self.model))
        except Exception:
            pass

        # Solo play: the meter is self-only. Ungrouped rivals' stray events
        # are still tracked, but display (rows + chips) uses your own parse
        # until the game reports a party again. Gated by the dps_solo_only
        # toggle (Settings > DPS > Combat DPS Meter) through the SAME
        # predicate the Combat & DPS page uses, so the two surfaces can't
        # disagree about the rows or the group totals.
        solo = solo_only_view(self.model, self.s, getattr(self, "tracker", None))
        me = own_row(session)
        # Same totals the Combat & DPS page shows for the same view.
        grp_dmg, grp_heals, taken = view_totals(session, solo)
        grp_dps = grp_dmg / max(1.0, dur)
        grp_hps = grp_heals / max(1.0, dur)
        taken_rate = taken / max(1.0, dur)

        # The PER-SECOND numbers are the focus line and the totals sit under
        # them (live 2026-10-03: "the dp/s h/s damage taken /s should be the
        # main focus numbers"). While a fight is running the rate is the
        # number being read; the total is the receipt. Damage taken gets a
        # rate too - it was the one chip with no /s at all, so the same figure
        # the Test Dummy HUD already computes had nowhere to show here.
        # Chips always show real totals regardless of view mode.
        self.chip_dmg.set_rate(f"{grp_dps:,.1f}/s", f"{grp_dmg:,.0f} total")
        self.chip_heal.set_rate(f"{grp_hps:,.1f}/s", f"{grp_heals:,.0f} total")
        self.chip_taken.set_rate(f"{taken_rate:,.1f}/s", f"{taken:,.0f} total")

        self._update_target_lbl(session)

        # Update Player Meters
        d_limit = getattr(self.s, "dps_top_count", 8) if self.s else 8
        h_limit = getattr(self.s, "heals_top_count", 2) if self.s else 2

        # Inside a dungeon/rift while idle (between pulls) the tracker has
        # pre-registered every party member with 0 stats — auto-load the
        # whole group onto the meter (uncapped) instead of only the active
        # participants. Once combat starts the activity filter applies again.
        in_instance_idle = bool(self._in_instance()
                                and session.state != "COMBAT")
        if in_instance_idle:
            d_limit = max(d_limit, len(session.players))
            h_limit = max(h_limit, len(session.players))
            include_empty = True
        else:
            include_empty = False

        # Pin the local player's row to the top when enabled (Settings > HUD's
        # > Top DPS > Pin Me Top); otherwise true rank order.
        pin_me = bool(getattr(self.s, "dps_pin_me", True)) if self.s else True
        if self._view_mode == "healing":
            candidates = session.ranked_players(view="healing", pin_me=pin_me,
                                                include_empty_allies=include_empty)[:h_limit]
        elif self._view_mode == "both":
            top_d = session.ranked_players(view="damage", pin_me=pin_me,
                                           include_empty_allies=include_empty)[:d_limit]
            top_h = [p for p in session.ranked_players(view="healing", pin_me=pin_me,
                                                       include_empty_allies=include_empty)[:h_limit]
                     if p not in top_d]
            candidates = top_d + top_h
        else:  # damage
            candidates = session.ranked_players(view="damage", pin_me=pin_me,
                                                include_empty_allies=include_empty)[:d_limit]

        # Active participants only (or local player) so 0-damage bystanders
        # don't clutter the meter — unless we're auto-loading the party in an
        # instance between pulls.
        ranked = [p for p in candidates
                  if include_empty or (p.total_damage > 0 or p.heals > 0 or p.is_me)]
        if not ranked and session.players:
            active = session.active_players()   # dummy: no chip-damage-only rows
            ranked = active[:d_limit] if active else ([me] if me else [])

        # Solo: drop every ungrouped rival row — only your own parse shows.
        if solo:
            ranked = [p for p in ranked if p.is_me]
            if not ranked and me is not None:
                ranked = [me]

        self._update_meters(ranked, dur, grp_dmg, grp_heals,
                            include_empty=include_empty)

    def _update_game_combat_lbl(self):
        """Refresh the game combat badge from the tracker's combat_state."""
        st = getattr(self.tracker, "combat_state", None)
        if not st:
            self.game_lbl.set_full("")
            return
        in_combat = st.get("in_combat")
        start = float(st.get("combat_start") or 0.0)
        end = float(st.get("combat_end") or 0.0)
        # A settled combat_end outranks the boolean. The badge used to test
        # `in_combat` first, so a client that had not cleared the flag yet
        # kept reading "IN COMBAT · <ever-growing>s" over an encounter the
        # game had already stamped closed - the same defect the armed clock
        # had, in the badge beside it.
        if end > start:
            self.game_lbl.set_full(f"⚔ GAME OVER · {end - start:.1f}s")
            self.game_lbl.setStyleSheet(
                f"font-size: 10px; font-weight: 700; color: {theme.GOLD};")
        elif in_combat:
            elapsed = getattr(self.tracker, "game_encounter_elapsed", None)
            try:
                elapsed = self.tracker.game_encounter_elapsed
            except Exception:
                elapsed = None
            if elapsed is not None:
                self.game_lbl.set_full(f"⚔ IN COMBAT · {elapsed:.1f}s")
            else:
                self.game_lbl.set_full("⚔ IN COMBAT")
            self.game_lbl.setStyleSheet(
                f"font-size: 10px; font-weight: 800; color: {theme.GOOD};")
        elif in_combat is False:
            self.game_lbl.set_full("⚔ GAME out")
            self.game_lbl.setStyleSheet(
                f"font-size: 10px; font-weight: 700; color: {theme.DIM};")
        else:
            self.game_lbl.set_full("⚔ GAME ?")
            self.game_lbl.setStyleSheet(
                f"font-size: 10px; font-weight: 700; color: {theme.DIM};")

    def _group_roster(self):
        """The live party roster (None when unattached/undecodable).

        The game reader rate-limits itself to ~1 Hz, so multiple overlay
        reads per tick share the same snapshot.
        """
        try:
            fn = getattr(self.model, "group_roster", None)
            return fn() if callable(fn) else None
        except Exception:
            return None

    def _in_instance(self) -> bool:
        """True inside a dungeon/rift - the tick's shared snapshot when there is
        one, else the model's own read (see core/game_state.py)."""
        return game_state.in_instance(self.model)


    def _is_solo(self) -> bool | None:
        """True only when the game DECODED a solo roster, False when grouped
        (or inside a dungeon/rift, where party members always show), None
        while unknown — no readable roster, which means keep showing every
        row. An unreadable roster is not evidence of being alone."""
        return solo_status(self.model, getattr(self, "tracker", None))

    def _set_title(self, mode: str | None, source: str = "") -> None:
        """Title text = the base header + the dungeon-mode badge. Only the
        badge half changes, and only on a (mode, source) CHANGE (the tick runs
        ~10x/s; per-tick rich-text reparse would churn the label for nothing).
        ``source`` only reaches the tooltip — the bar itself never spells out
        auto/manual, so the wording matches the Dungeon HUD's badge."""
        if (mode, source) == self._title_state:
            return
        self._title_state = (mode, source)
        self.titlebar.title.setText(self._title_base + dungeon_mode_badge(mode))
        self.titlebar.title.setToolTip(mode_tooltip(mode, source))

    def _update_capture_badge(self):
        """Title-bar badge: whether real damage events are actually arriving.

        A fight can start with capture dead (stale pool, unarmed DLL, reader
        still calibrating) and the meter then just shows zeros — this makes
        that state visible at a glance instead of discoverable in Settings.
        The badge is also the capture DIAGNOSTIC: every state is clickable, and
        a click writes the event-path report to the Activity Log — how many
        bytes arrived, how many events decoded, how many the tracker pulled, how
        many attribution kept. "The board reads zero" has at least three causes
        (nothing arrived / nothing decoded / attribution dropped it) and the
        engine status cannot tell them apart. When the capture is also stuck
        (stale pool / calibrating) the same click forces a real rescan (forced
        re-hunt + re-derive), which is the actual "unstick capture" action (the
        Reset button only clears the meter — it never touches capture).
        """
        text, color, tip = capture_badge(self._dm())
        if text:
            self.cap_badge.apply(text, color, with_report_hint(tip))
        else:
            self.cap_badge.clear()
        self.cap_badge.set_click(
            self._capture_diagnostic if text else None)

    def _capture_diagnostic(self):
        """Badge click: report the event path, then rescan if capture is stuck."""
        emit_capture_diagnostic(self.model, self.log.emit)
        if capture_badge(self._dm())[0] not in ("⚠", "CAL"):
            return                      # healthy capture: the report was the ask
        dm = self._dm()
        if dm is None:
            return
        try:
            dm.force_wide_rehunt("meter badge rescan")
        except Exception:
            pass
        try:
            dm.recalibrate()
        except Exception:
            pass
        try:
            self.log.emit("DPS capture: manual rescan requested from the meter badge")
        except Exception:
            pass

    def _update_group_lbl(self):
        """Refresh the live party line from model.group_roster()."""
        roster = self._group_roster()
        if roster is None:
            if self._is_solo() is True:
                self.group_lbl.set_full("Group: solo")
            else:
                self.group_lbl.set_full("")
            return
        members = list(getattr(roster, "members", []) or [])
        if not members or getattr(roster, "solo", False):
            self.group_lbl.set_full("Group: solo")
            return
        parts = []
        for m in members:
            nm = getattr(m, "name", "") or "?"
            tags = []
            if getattr(m, "is_me", False):
                tags.append("you")
            if getattr(m, "is_leader", False):
                tags.append("leader")
            if tags:
                nm = f"{nm} ({', '.join(tags)})"
            parts.append(nm)
        self.group_lbl.set_full(
            f"Group: {len(members)} — {', '.join(parts)}")

    @staticmethod
    def _ranked_skills_for(p: PlayerParse, mode: str) -> list:
        """Per-skill rows for a player under the active meter mode."""
        if mode == "healing":
            return p.ranked_heal_skills()
        if mode == "both":
            return sorted(p.skills.values(),
                          key=lambda s: s.total, reverse=True)
        return p.ranked_skills()

    def _empty_hint_default(self) -> str:
        """Empty-state text for an idle meter.

        There is no dummy half any more: prompting "attack the dummy" is the
        Test Dummy HUD's line, and the meter is not the board at a dummy — it
        just shows whatever session is live.
        """
        return "Hit an enemy or heal party to start."

    def _update_target_lbl(self, session) -> None:
        """The target line: the live boss/dummy HP bar, or the boss that is
        still waiting behind the pre-boss trash.

        Split out of `_tick` so the segment label and this line refresh even
        while the meter is hidden; the player rows below stay tick-only.
        Painting lives in `widgets.render` (see the file budget).
        """
        _dps_widgets.render(self, session)

    def _update_meters(self, ranked: list[PlayerParse], duration: float,
                       group_damage: float, group_heals: float,
                       include_empty: bool = False):
        if not ranked:
            self.empty_lbl.setText(
                empty_hint(self._dm(), self._empty_hint_default()))
            self.empty_lbl.show()
            self.dps_top_box.hide()
            for w in list(self._row_widgets.values()):
                w.deleteLater()
            self._row_widgets.clear()
            return

        self.empty_lbl.hide()
        self.dps_top_box.show()
        active_names = set()

        # Global skill budget: top-ranked players get rows first.
        cap = int(getattr(self.s, "dps_skill_cap", GLOBAL_SKILL_CAP)
                  or GLOBAL_SKILL_CAP)
        budget = max(0, cap)
        rows_lay = self.dps_top_box.rows
        # Live per-hero HP (name -> (hp, is_me)) from the last scene scan, so
        # a player at 0 HP shows a death skull on their row.
        hero_status = getattr(self.tracker, "last_hero_status", {}) or {}

        # Solo meter: your row is the only ACTIVE parse on screen — expand the
        # per-skill breakdown by default so it is visible. Count active rows
        # (with damage or heals), not total rows: a second row carrying no
        # parse of its own (a phantom Party_XXXX, a pre-registered idle ally)
        # must not keep your skills collapsed when you are the only one
        # fighting. A saved chevron choice always wins over these defaults.
        _active = [p for p in ranked if p.total_damage > 0 or p.heals > 0]
        solo_view = bool(len(_active) == 1 and _active[0].is_me)

        for idx, p in enumerate(ranked):
            active_names.add(p.name)

            saved_collapsed = self._row_collapsed.get(p.name)
            if saved_collapsed is not None:
                row_expanded = not saved_collapsed
            else:
                row_expanded = bool(solo_view and p.is_me)
            list_expanded = self._skills_show_all.get(p.name, False)

            if p.name not in self._row_widgets:
                row_widget = _PlayerRowWidget(
                    p.name, is_me=p.is_me, parent=self.dps_top_box,
                    expanded=row_expanded,
                    show_all_skills=list_expanded)
                row_widget.collapseChanged.connect(
                    lambda on, nm=p.name: self._row_collapsed.__setitem__(
                        nm, not on))
                row_widget.showAllChanged.connect(
                    lambda on, nm=p.name: self._skills_show_all.__setitem__(
                        nm, on))
                self._row_widgets[p.name] = row_widget
                rows_lay.addWidget(row_widget)
            else:
                row_widget = self._row_widgets[p.name]
            # Scope flips (solo <-> group) apply the default live: leaving a
            # solo meter collapses my skills at once; going solo re-expands
            # them. Rows with a saved chevron choice are never overridden.
            if row_widget.expanded != row_expanded:
                row_widget.expanded = row_expanded
                row_widget._sync_visibility()
            row_widget.show()

            if row_expanded and not list_expanded:
                n_skills = len(self._ranked_skills_for(p, self._view_mode))
                alloc = min(budget, n_skills)
                budget -= alloc
            else:
                alloc = None   # full list (user override) or nothing to render

            st = hero_status.get(p.name)
            dead = bool(st is not None and st[0] <= 0.0)
            # True rank by the active metric (get_rank ignores pin_me): with
            # "Pin Me Top" on your row sits at the top of the list, but the
            # number must show your real DPS position — not "1". The #1 tint
            # follows the real leader too.
            true_rank = self.tracker.session.get_rank(
                p, view=self._view_mode, include_empty_allies=include_empty)
            row_widget.update_stats(true_rank - 1, p, duration, group_damage,
                                    group_heals, mode=self._view_mode,
                                    skill_budget=alloc, dead=dead)

            current_idx = rows_lay.indexOf(row_widget)
            if current_idx != idx:
                rows_lay.insertWidget(idx, row_widget)

        for name, widget in self._row_widgets.items():
            if name not in active_names:
                widget.hide()
                if rows_lay.indexOf(widget) >= 0:
                    rows_lay.removeWidget(widget)

        # HUD header: player count + group totals in the active channel.
        self.dps_top_box.header.set_text(f"TOP DPS · {len(ranked)}")
        if self._view_mode == "healing":
            tag = f"{group_heals:,.0f} heal"
        elif self._view_mode == "both":
            tag = f"{group_damage:,.0f} dmg · {group_heals:,.0f} heal"
        else:
            tag = f"{group_damage:,.0f} dmg"
        self.dps_top_box.header.set_tag(tag)

    def keyPressEvent(self, e):
        if e.key() == QtCore.Qt.Key_Escape:
            e.ignore()
            return
        super().keyPressEvent(e)

    def closeEvent(self, e):
        self._timer.stop()
        super().closeEvent(e)
