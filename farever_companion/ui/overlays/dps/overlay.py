"""Top DPS Meter Overlay (Dungeon-HUD style).

Floating overlay displaying real-time combat metrics, target health bar,
group statistics chips, and ranked player meters with expandable skill
breakdowns. The player rows live in ui/overlays/dps/rows.py; this module
keeps the overlay window, chrome and the per-tick pipeline. It is the
lightweight sibling of the Combat & DPS Analysis page — the HUD shows the
same real events in a compact, HUD-shaped form.
"""
from __future__ import annotations

import time

from PySide6 import QtCore, QtWidgets

from ... import theme
from ...components import ElideLabel as _ElideLabel
from ...overlay_base import OverlayWindow
from ....core.dps_tracker import DpsTracker, PlayerParse, solo_status
from ....data import icons
from ...dps_source_text import empty_hint
from ..entity_rows import _Section, _scroll_body
from .rows import _PlayerRowWidget

TICK_MS = 100  # 10 FPS smooth, flicker-free UI refresh
# Global skill budget: total skill rows across all players, top rank first.
GLOBAL_SKILL_CAP = 8


class _StatChip(QtWidgets.QFrame):
    """Compact group-metric card: dim label, bold value, muted /s sub-rate."""
    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setStyleSheet(
            f"_StatChip {{ background-color: rgba(22, 27, 34, 0.85); "
            f"border: 1px solid rgba(255, 255, 255, 0.08); border-radius: 6px; padding: 4px 6px; }}"
        )
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(8, 4, 8, 5)
        lay.setSpacing(0)
        self.title_lbl = QtWidgets.QLabel(title.upper())
        self.title_lbl.setStyleSheet(
            f"font-size: 8px; font-weight: 800; color: #8b9bb4; "
            f"letter-spacing: 1.2px;")
        lay.addWidget(self.title_lbl)
        self.val_lbl = QtWidgets.QLabel("0")
        self.val_lbl.setStyleSheet(
            f"font-size: 15px; font-weight: 900; color: #ffffff; "
            f"font-family: 'Consolas', monospace;")
        lay.addWidget(self.val_lbl)
        self.sub_lbl = QtWidgets.QLabel("0/s")
        self.sub_lbl.setStyleSheet(
            f"font-size: 9px; font-weight: 700; color: #00e5ff; font-family: 'Consolas', monospace;")
        lay.addWidget(self.sub_lbl)

    def set_value(self, val: str, sub: str = ""):
        self.val_lbl.setText(val)
        if sub:
            self.sub_lbl.setText(sub)

class _ModePill(QtWidgets.QFrame):
    """Compact DMG / HEAL / BOTH segmented pill."""
    currentChanged = QtCore.Signal(str)

    def __init__(self, current: str = "DMG", parent=None):
        super().__init__(parent)
        self._accent = theme.ACCENT
        self.setStyleSheet(
            f"QFrame {{ background: {theme.SURFACE}; border: 1px solid {theme.BORDER}; "
            f"border-radius: 9px; }}")
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(2, 1, 2, 1)
        lay.setSpacing(2)
        self._group = QtWidgets.QButtonGroup(self)
        self._group.setExclusive(True)
        self._btns: dict[str, QtWidgets.QPushButton] = {}
        for opt in ("DMG", "HEAL", "BOTH"):
            b = QtWidgets.QPushButton(opt)
            b.setCheckable(True)
            b.setCursor(QtCore.Qt.PointingHandCursor)
            b.setFixedHeight(16)
            b.setMinimumWidth(34)
            self._group.addButton(b)
            self._btns[opt] = b
            lay.addWidget(b)
        self._group.buttonClicked.connect(
            lambda b: self.currentChanged.emit(b.text()))
        self.setCurrentText(current)

    def _accent_color(self) -> str:
        return self._accent or theme.ACCENT

    def _style_btn(self, b: QtWidgets.QPushButton, checked: bool) -> None:
        if checked:
            b.setStyleSheet(
                f"QPushButton {{ border: 0; border-radius: 7px; padding: 0 8px; "
                f"font-size: 9px; font-weight: 800; letter-spacing: 0.5px; "
                f"background: {self._accent_color()}; color: {theme.ON_ACCENT}; }}")
        else:
            b.setStyleSheet(
                f"QPushButton {{ border: 0; border-radius: 7px; padding: 0 8px; "
                f"font-size: 9px; font-weight: 800; letter-spacing: 0.5px; "
                f"background: transparent; color: {theme.DIM}; }}"
                f"QPushButton:hover {{ color: {theme.TEXT}; }}")

    def setCurrentText(self, text: str) -> None:
        for opt, b in self._btns.items():
            on = (opt.upper() == str(text or "").upper())
            b.setChecked(on)
            self._style_btn(b, on)

    def currentText(self) -> str:
        b = self._group.checkedButton()
        return b.text() if b else ""

    def retint(self, accent: str) -> None:
        self._accent = accent or theme.ACCENT
        for b in self._btns.values():
            self._style_btn(b, b.isChecked())


class DpsOverlay(OverlayWindow):
    # Roster join/leave lines forwarded to the app's Activity Log.
    log = QtCore.Signal(str)

    def __init__(self, model, settings, parent=None):
        super().__init__("Top DPS", settings, geo_key="dps", parent=parent)
        # Dense meter: tighter chrome margins so more rows fit.
        self.content.setContentsMargins(4, 2, 4, 2)
        self.content.setSpacing(4)
        self.model = model
        self.s = settings
        max_d = getattr(settings, "dps_max_dist", 400.0) if settings else 400.0
        self.tracker = getattr(model, "dps", None) or DpsTracker(model, max_dist=max_d)
        if self.tracker is not None:
            self.tracker.log_line = self.log.emit
        if hasattr(model, "damage") and model.damage is not None:
            model.damage.log_line = self.log.emit
        self._page_key = "settings:dps"
        self._view_mode = getattr(settings, "dps_view", "damage") if settings else "damage"
        self._row_widgets: dict[str, _PlayerRowWidget] = {}
        # Overlay-owned per-player UI state; survives ticks and rebuilt rows.
        self._row_collapsed: dict[str, bool] = {}      # name -> chevron collapsed
        self._skills_show_all: dict[str, bool] = {}    # name -> top-5 list expanded

        self._VIEW_TO_PILL = {"damage": "DMG", "healing": "HEAL", "both": "BOTH"}
        self._PILL_TO_VIEW = {"DMG": "damage", "HEAL": "healing", "BOTH": "both"}

        self.titlebar.setStyleSheet(
            f"QFrame#TitleBar {{ background: {theme.PANEL}; border: 0; }}")
        # green status dot + TOP DPS (mockup header) in the title bar
        self.titlebar.title.setText(
            f"<span style='color:{theme.GOOD}; font-size:11px;'>●</span>"
            f"&nbsp;&nbsp;TOP DPS")
        self.titlebar.title.setTextFormat(QtCore.Qt.RichText)
        self.titlebar.title.setStyleSheet(
            f"color: {theme.TEXT}; font-weight: 800; font-size: 13px; "
            f"letter-spacing: 1px;")

        # Bridge status pill tucked right after the green-dot title.
        self._bridge_status_lbl = QtWidgets.QLabel("OFF")
        self._bridge_status_lbl.setStyleSheet(
            f"font-size: 9px; font-weight: 800; color: {theme.MUTED}; "
            f"background: rgba(255,255,255,0.05); border: 1px solid {theme.BORDER}; "
            f"border-radius: 4px; padding: 1px 6px; "
            f"letter-spacing: 0.5px;")
        self.titlebar.extra.insertWidget(0, self._bridge_status_lbl)

        self.mode_pill = _ModePill(
            current=self._VIEW_TO_PILL.get(
                (getattr(self.s, "dps_view", "damage") or "damage"), "DMG")
            if self.s else "DMG")
        self.mode_pill.currentChanged.connect(self._set_mode)
        self.titlebar.extra.insertWidget(0, self.mode_pill)

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
            f"QProgressBar {{ background: rgba(0, 0, 0, 0.4); border: 1px solid rgba(255, 255, 255, 0.08); border-radius: 3px; }}"
            f"QProgressBar::chunk {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:0, "
            f"stop:0 #ef4444, stop:0.7 #f97316, stop:1 #eab308); border-radius: 2px; }}")
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

        # Solo: mirrors Settings > DPS > Combat DPS Meter's "show only my
        # DPS" toggle — flipping either side updates the other (button state
        # is re-synced from settings every tick).
        self.solo_btn = QtWidgets.QPushButton("👤 Solo")
        self.solo_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self.solo_btn.setCheckable(True)
        self.solo_btn.setChecked(bool(getattr(self.s, "dps_solo_only", True)))
        solo_sel_qss = _btn_qss + (
            f"QPushButton:checked {{ background: {theme.ACCENT_DIM}; color: {theme.ACCENT}; "
            f"border: 1px solid {theme.ACCENT}; }}")
        self.solo_btn.setStyleSheet(solo_sel_qss)
        self.solo_btn.setToolTip(
            "Solo: show only your DPS. Ungrouped rivals are dropped from the "
            "meter in the open world; groups / dungeons / rifts always show everyone.")
        self.solo_btn.clicked.connect(self._toggle_solo)
        btn_row.addWidget(self.solo_btn)

        self.content.addLayout(btn_row)

        self.enable_resize_grip()
        self.setMinimumWidth(260)
        self.setMaximumWidth(500)
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
        if model is not None:
            if hasattr(model, "dps") and model.dps is not None:
                self.tracker = model.dps
                self.tracker.log_line = self.log.emit
            elif self.tracker is not None:
                self.tracker.model = model
            if hasattr(model, "damage") and model.damage is not None:
                model.damage.log_line = self.log.emit

    def _dm(self):
        m = self.model
        return getattr(m, "damage", None) if m is not None else None

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
        self.state_lbl.setStyleSheet(f"font-size: 10px; font-weight: 800; color: {theme.MUTED};")
        self.game_lbl.set_full("")
        self.game_lbl.setToolTip("Game combat fields not read yet (10 Hz poll)")
        self.chip_dmg.set_value("0", "0/s")
        self.chip_heal.set_value("0", "0/s")
        self.chip_taken.set_value("0")
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

    def _set_bridge_status_lbl(self, text: str, color: str) -> None:
        """Update the small status pill in the titlebar (BRIDGE / MEM / METER / OFF / ...)."""
        try:
            self._bridge_status_lbl.setText(text)
            self._bridge_status_lbl.setStyleSheet(
                f"font-size: 9px; font-weight: 800; color: {color}; "
                f"background: rgba(255,255,255,0.05); border: 1px solid {theme.BORDER}; "
                f"border-radius: 4px; padding: 1px 6px; "
                f"letter-spacing: 0.5px;")
        except Exception:
            pass

    def _retint(self, accent: str) -> None:
        """Re-tint pill and leader-row accents."""
        if hasattr(self, "mode_pill"):
            self.mode_pill.retint(accent)
        for w in getattr(self, "_row_widgets", {}).values():
            w.retint(accent)

    def _tick(self):
        if self.model is None:
            return

        self.tracker.update()

        if self.s is not None:
            want_view = getattr(self.s, "dps_view", "damage") or "damage"
            if want_view != self._view_mode:
                self._view_mode = want_view
                self.mode_pill.setCurrentText(
                    self._VIEW_TO_PILL.get(want_view, "DMG"))
            want_dist = float(getattr(self.s, "dps_max_dist", 400.0) or 0.0)
            if want_dist != self.tracker.max_dist:
                self.tracker.max_dist = want_dist
            want_hp_est = bool(getattr(self.s, "dps_hp_est", False))
            if want_hp_est != self.tracker.hp_est_allowed:
                self.tracker.hp_est_allowed = want_hp_est
                # Toggle flips the capture source: re-baseline so the fallback
                # doesn't compare against HP snapshots from while it was off.
                self.tracker.reset_hp_baselines(reason="HP-diff toggle")
            want_solo = bool(getattr(self.s, "dps_solo_only", True))
            if self.solo_btn.isChecked() != want_solo:
                self.solo_btn.blockSignals(True)
                self.solo_btn.setChecked(want_solo)
                self.solo_btn.blockSignals(False)

        session = self.tracker.session

        dur = session.duration
        mins = int(dur // 60)
        secs = dur % 60
        self.time_lbl.setText(f"{mins:02d}:{secs:04.1f}")

        seg_name = (session.name or "Fight").upper()
        self.seg_lbl.setText(seg_name)
        if session.state == "COMBAT":
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
                f"font-size: 10px; font-weight: 800; color: {theme.MUTED};")

        self._update_game_combat_lbl()
        self._update_group_lbl()

        # Solo play: the meter is self-only. Ungrouped rivals' stray events
        # are still tracked, but display (rows + chips) uses your own parse
        # until the game reports a party again. Gated by the dps_solo_only
        # toggle (Settings > DPS > Combat DPS Meter).
        solo = (self._is_solo() is True
                and bool(getattr(self.s, "dps_solo_only", True)))
        me = next((p for p in session.players.values() if p.is_me), None)
        if solo and me is not None:
            grp_dmg = me.total_damage
            grp_heals = me.heals
            taken = me.damage_taken
            taken_est = me.damage_taken_est
        else:
            grp_dmg = session.group_damage
            grp_heals = session.group_heals
            taken = session.group_taken
            taken_est = session.group_taken_est
        grp_dps = grp_dmg / max(1.0, dur)
        grp_hps = grp_heals / max(1.0, dur)

        # Chips always show real totals regardless of view mode.
        self.chip_dmg.set_value(f"{grp_dmg:,.0f}", f"{grp_dps:,.1f}/s")
        self.chip_heal.set_value(f"{grp_heals:,.0f}", f"{grp_hps:,.1f}/s")
        if taken > 0 and taken_est > 0:
            self.chip_taken.set_value(f"{taken:,.0f}", "≈ HP est")
        else:
            self.chip_taken.set_value(f"{taken:,.0f}")

        if session.target_name != "None" and session.target_max_hp > 0:
            pct = max(0.0, min(100.0, (session.target_hp / session.target_max_hp) * 100.0))
            clean_name = session.target_name.replace("👑 ", "")
            prefix = "👑 " if "👑" in session.target_name else "🎯 "
            self.target_lbl.setText(f"{prefix}{clean_name}")
            self.target_lbl.setStyleSheet(
                f"font-size: 11px; font-weight: 700; color: {theme.TEXT};")
            self.target_hp_lbl.setText(
                f"{session.target_hp:,.0f} / {session.target_max_hp:,.0f} · {pct:.1f}%")
            self.target_bar.setValue(int(pct))
        else:
            self.target_lbl.setText("Target: None")
            self.target_lbl.setStyleSheet(
                f"font-size: 11px; font-weight: 600; color: {theme.MUTED};")
            self.target_hp_lbl.setText("")
            self.target_bar.setValue(0)

        # Update Player Meters
        d_limit = getattr(self.s, "dps_top_count", 8) if self.s else 8
        h_limit = getattr(self.s, "heals_top_count", 2) if self.s else 2

        # True rank order: never pin "you" to the top.
        if self._view_mode == "healing":
            candidates = session.ranked_players(view="healing", pin_me=False)[:h_limit]
        elif self._view_mode == "both":
            top_d = session.ranked_players(view="damage", pin_me=False)[:d_limit]
            top_h = [p for p in session.ranked_players(view="healing", pin_me=False)[:h_limit] if p not in top_d]
            candidates = top_d + top_h
        else:  # damage
            candidates = session.ranked_players(view="damage", pin_me=False)[:d_limit]

        # Active participants only (or local player) so 0-damage bystanders don't clutter the meter
        ranked = [p for p in candidates if (p.total_damage > 0 or p.heals > 0 or p.is_me)]
        if not ranked and session.players:
            active = [p for p in session.players.values()
                      if p.is_me or p.total_damage > 0 or p.heals > 0 or p.damage_taken > 0]
            ranked = active[:d_limit] if active else ([p for p in session.players.values() if p.is_me][:1])

        # Solo: drop every ungrouped rival row — only your own parse shows.
        if solo:
            ranked = [p for p in ranked if p.is_me]
            if not ranked and me is not None:
                ranked = [me]

        self._update_meters(ranked, dur, grp_dmg, grp_heals)

    def _update_game_combat_lbl(self):
        """Refresh the game combat badge from the tracker's combat_state."""
        st = getattr(self.tracker, "combat_state", None)
        if not st:
            self.game_lbl.set_full("")
            self.game_lbl.setToolTip("Game combat fields not read yet (10 Hz poll)")
            return
        in_combat = st.get("in_combat")
        cid = st.get("combat_id")
        start = float(st.get("combat_start") or 0.0)
        end = float(st.get("combat_end") or 0.0)
        tip = (f"Hero.isInCombat={in_combat} · combatId={cid} · "
               f"start={start:.1f}s · end={end:.1f}s (engine seconds)")
        if in_combat:
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
        elif end > start:
            self.game_lbl.set_full(f"⚔ GAME OVER · {end - start:.1f}s")
            self.game_lbl.setStyleSheet(
                f"font-size: 10px; font-weight: 700; color: {theme.GOLD};")
        elif in_combat is False:
            self.game_lbl.set_full("⚔ GAME out")
            self.game_lbl.setStyleSheet(
                f"font-size: 10px; font-weight: 700; color: {theme.DIM};")
        else:
            self.game_lbl.set_full("⚔ GAME ?")
            self.game_lbl.setStyleSheet(
                f"font-size: 10px; font-weight: 700; color: {theme.DIM};")
        self.game_lbl.setToolTip(tip)

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

    def _is_solo(self) -> bool | None:
        """True when ungrouped, False when grouped (or inside a dungeon/rift,
        where party members always show), None while unknown (no readable
        roster — keep showing all rows). The tracker supplies the recent-hit
        safety rule so a roster misread never hides a live fight-mate."""
        return solo_status(self.model, getattr(self, "tracker", None))

    def _update_group_lbl(self):
        """Refresh the live party line from model.group_roster()."""
        roster = self._group_roster()
        if roster is None:
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

    def _update_meters(self, ranked: list[PlayerParse], duration: float,
                       group_damage: float, group_heals: float):
        if not ranked:
            self.empty_lbl.setText(
                empty_hint(self._dm(), "Hit an enemy or heal party to start."))
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

        for idx, p in enumerate(ranked):
            active_names.add(p.name)

            # Only the local player starts expanded; saved choice wins.
            saved_collapsed = self._row_collapsed.get(p.name)
            row_expanded = ((not saved_collapsed)
                            if saved_collapsed is not None else p.is_me)
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
            row_widget.show()
            true_rank = self.tracker.session.get_rank(p, view=self._view_mode)

            if row_expanded and not list_expanded:
                n_skills = len(self._ranked_skills_for(p, self._view_mode))
                alloc = min(budget, n_skills)
                budget -= alloc
            else:
                alloc = None   # full list (user override) or nothing to render

            row_widget.update_stats(true_rank - 1, p, duration, group_damage,
                                    group_heals, mode=self._view_mode,
                                    skill_budget=alloc)

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
