"""Skills panel, the per-skill damage breakdown, split out from the DPS meter.

One combined, sortable table (icon + real skill name + bar + toggleable columns:
%, DPS, hits, crit%, max, avg, min) with DMG / HEAL / SHIELD tabs. Replaces the
old meter's duplicated "by skill analysis" + "skill totals" sections. Falls back
to a by-target (HP-diff) list with a CALIBRATING hint until the DamageDisplay
source is located. Self-DPS only. Read-only.
"""
from __future__ import annotations

import time

from PySide6 import QtCore, QtGui, QtWidgets

from .. import theme
from ..overlay_base import OverlayWindow
from ..components import SectionHeader, IconTile
from ..widgets import Bar
from ..skill_table import (KindTabs, TableHeader, SkillRow, TABLE_COLUMNS,
                          COLUMN_ORDER, DEFAULT_COLUMNS, col_value, abbr)
from ...data import names, icons
from ...combat import encounter as enc_mod

POLL_MS = 120           # fast refresh so per-skill + feed update smoothly (no stutter)
N_ROWS = 8               # per-skill table rows (wide-not-tall default; resizable)
FEED_ROWS = 5            # live recent-hits feed below the table


def _fade(hexcol: str, frac: float) -> str:
    """Accent colour at an age-based alpha (newest bright -> oldest dim)."""
    c = QtGui.QColor(hexcol)
    a = int(55 + 200 * max(0.0, min(1.0, frac)))
    return f"rgba({c.red()},{c.green()},{c.blue()},{a})"


class _FeedRow(QtWidgets.QWidget):
    """One recent-hit line: skill icon + fading text (name + amount)."""
    def __init__(self):
        super().__init__()
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.tile = IconTile(16)
        self.text = QtWidgets.QLabel("")
        self.text.setObjectName("Mono")
        self.text.setStyleSheet("background:transparent;")
        lay.addWidget(self.tile)
        lay.addWidget(self.text, 1)

    def set(self, skill_id: str, text: str, color: str, accent: str) -> None:
        if skill_id and icons.has_icon("Skills", skill_id):
            self.tile.set("Skills", skill_id, accent)
            self.tile.show()
        else:
            self.tile.hide()
        self.text.setText(text)
        self.text.setStyleSheet(f"color:{color};background:transparent;")


class SkillOverlay(OverlayWindow):
    def __init__(self, model, settings, parent=None):
        super().__init__("Skills", settings, geo_key="skills", parent=parent)
        self._page_key = "combat"
        self.model = model
        self.s = settings
        self._kind = "damage"
        self._columns = [c for c in (settings.dps_columns or DEFAULT_COLUMNS)
                         if c in TABLE_COLUMNS] or list(DEFAULT_COLUMNS)
        self._last_encounter = None

        # title bar: recalibrate + export + reset
        recal = QtWidgets.QPushButton(); recal.setObjectName("Icon")
        recal.setIcon(icons.ui_qicon("search", theme.MUTED, 14))
        recal.setToolTip("Recalibrate per-skill — attack something while it re-maps")
        recal.clicked.connect(self._recalibrate)
        exp = QtWidgets.QPushButton(); exp.setObjectName("Icon")
        exp.setIcon(icons.ui_qicon("download", theme.MUTED, 14))
        exp.setToolTip("Export encounter (CSV / JSON)")
        exp.clicked.connect(self._export)
        rst = QtWidgets.QPushButton(); rst.setObjectName("Icon")
        rst.setIcon(icons.ui_qicon("refresh-cw", theme.MUTED, 14))
        rst.setToolTip("Reset session")
        rst.clicked.connect(self._reset)
        for w in (recal, rst, exp):
            self.titlebar.extra.insertWidget(self.titlebar.extra.count() - 3, w)

        self._header = SectionHeader("SKILL BREAKDOWN")
        self.content.addWidget(self._header)
        self.summary = QtWidgets.QLabel("idle")
        self.summary.setObjectName("Mono")
        self.content.addWidget(self.summary)

        # Honesty caption: per-skill capture reads the live floating DamageDisplay
        # numbers, which are scattered across the GC heap - coverage is incomplete
        # under heavy multi-hit / status-effect load (measured ~30% of live numbers
        # at peak). The headline DPS + by-enemy view are exact (HP-diff); this
        # per-skill table is a representative SAMPLE. Marked experimental until the
        # GC page-walk lands complete enumeration (see core/damage_source.py).
        self._exp = QtWidgets.QLabel(
            "experimental — per-skill capture samples the live damage numbers and "
            "can undercount during intense fights; the DPS total and by-enemy view "
            "are exact.")
        self._exp.setObjectName("Muted")
        self._exp.setWordWrap(True)
        self.content.addWidget(self._exp)

        # one-time per-skill calibration progress (locate DamageDisplay type +
        # map the GC cluster). Hidden unless the cluster scan is calibrating.
        self._cal_bar = Bar(self._accent)
        self._cal_bar.setVisible(False)
        self.content.addWidget(self._cal_bar)

        self._tabs = KindTabs()
        self._tabs.changed.connect(self._on_kind)
        self.content.addWidget(self._tabs)
        self._table_head = TableHeader()
        self.content.addWidget(self._table_head)
        self._rows = []
        for _ in range(N_ROWS):
            r = SkillRow()
            self.content.addWidget(r)
            self._rows.append(r)
        self._apply_columns()

        # live recent-hits feed: newest at top, each row fading with age. Shows the
        # last few floating numbers as they land (per-skill labelled).
        self._feed_header = SectionHeader("RECENT HITS")
        self.content.addWidget(self._feed_header)
        self._feed = []
        for _ in range(FEED_ROWS):
            fr = _FeedRow()
            self.content.addWidget(fr)
            self._feed.append(fr)
        self.content.addStretch(1)

        self.enable_resize_grip()

        # wide-not-tall by default (the per-skill table is column-heavy); base is
        # UNSCALED so apply_scale (the overlay-scale slider) sizes it base*scale.
        self.setMinimumWidth(320)
        self._base_w = 500
        self._base_h = 340
        self._retint(self._accent)
        self.apply_scale(self.s.dps_scale)
        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(POLL_MS)

    # --- columns ---------------------------------------------------------
    def set_columns(self, keys: list[str]) -> None:
        self._columns = [c for c in COLUMN_ORDER if c in keys] or list(DEFAULT_COLUMNS)
        self.s.dps_columns = list(self._columns)
        self.s.save()
        self._apply_columns()

    def columns(self) -> list[str]:
        return list(self._columns)

    def _apply_columns(self) -> None:
        self._table_head.apply_columns(self._columns)
        for r in self._rows:
            r.apply_columns(self._columns)

    def _on_kind(self, kind: str) -> None:
        self._kind = kind

    def _retint(self, accent: str) -> None:
        if not accent:
            return
        for r in self._rows:
            r.bar.set_color(accent)
        self._header.set_color(accent)
        self._cal_bar.set_color(accent)
        self._feed_header.set_color(accent)
        self._tabs.restyle(accent)

    def _reset(self):
        reset = getattr(self.model, "reset_combat", None)
        (reset or self.model.dps.reset)()
        self._last_encounter = None

    def _recalibrate(self):
        fn = getattr(self.model, "recalibrate_skills", None)
        if fn:
            fn()
        self._header.set_tag("CALIBRATING…")
        self._cal_bar.set(0.62, "re-mapping — keep attacking")
        self._cal_bar.setVisible(True)
        self.summary.setText("recalibrating — attack something so it can re-map")

    # --- tick ------------------------------------------------------------
    def _tick(self):
        try:
            self.model.sample_combat(radius=float(self.s.dps_radius))
        except Exception as e:
            self.summary.setText(f"err: {e}")
            return
        # The Skill panel is per-skill ONLY (DamageDisplay events) - the HP-diff
        # by-enemy view lives in the DPS meter now. Read the dedicated events meter.
        m = getattr(self.model, "dps_events", None) or self.model.dps
        accent = self.s.hud_accent
        if m.has_events:
            self._tabs.set_available({k for k in ("heal", "shield") if m.has_kind(k)})
            self._draw_skills(m, accent)
        else:
            self._draw_empty()
        self._draw_history(m, accent)

    def _draw_skills(self, m, accent):
        kind = self._kind
        kt = m.kind_total(kind)
        rate = {"damage": m.current_dps(), "heal": m.hps(), "shield": m.sps()}.get(kind, 0.0)
        lbl = {"damage": "DPS", "heal": "HPS", "shield": "SPS"}.get(kind, "DPS")
        self._header.set_text("SKILL BREAKDOWN")
        self._header.set_tag("")
        self._cal_bar.setVisible(False)
        self.summary.setText(f"{kt / 1000:,.1f}K total   {rate:,.0f} {lbl}   "
                             f"{m.duration:.0f}s   K {m.kills}")
        self._tabs.setVisible(True)
        self._table_head.setVisible(True)
        for r in self._rows:
            r.apply_columns(self._columns)
        top = m.top_skills(N_ROWS, kind=kind)
        dur = m.duration
        mx = top[0][1].total if top else 1.0
        for i, r in enumerate(self._rows):
            if i < len(top):
                skill, st = top[i]
                vals = {k: col_value(k, st, kt, dur) for k in self._columns}
                r.set_row(names.skill_name(skill) or skill,
                          st.total / mx if mx else 0, vals,
                          sheet="Skills", id_=skill, accent=accent)
                r.show()
            else:
                r.hide()

    def _draw_empty(self):
        # No per-skill events yet: show the one-time calibration progress, or a
        # waiting hint. (The HP-diff by-enemy view is the DPS meter's job now.)
        phase, frac = getattr(self.model, "per_skill_progress", lambda: ("", 0.0))()
        calibrating = bool(phase) and 0.0 < frac < 1.0
        self._header.set_text("SKILL BREAKDOWN")
        self._header.set_tag("CALIBRATING…" if calibrating else "")
        if calibrating:
            self._cal_bar.set(frac, phase)
            self._cal_bar.setVisible(True)
            # mapping needs floating numbers on screen to anchor the GC pages
            self.summary.setText("calibrating — attack something so it can map skills")
        else:
            self._cal_bar.setVisible(False)
            self.summary.setText("no skills yet — attack to populate  (⌖ = recalibrate)")
        self._tabs.setVisible(False)
        self._table_head.setVisible(False)
        for r in self._rows:
            r.hide()

    def _draw_history(self, m, accent):
        # newest-first live feed of the last few outgoing hits, fading with age
        hits = list(getattr(m, "recent_hits", ()))[-FEED_ROWS:][::-1]
        self._feed_header.setVisible(bool(hits))
        for i, fr in enumerate(self._feed):
            if i < len(hits):
                ev = hits[i]
                nm = names.skill_name(ev.skill) or ev.skill
                tag = "✦ " if ev.crit else ("☠ " if ev.kill else "")
                fr.set(ev.skill, f"{tag}{nm}  {ev.amount:,.0f}",
                       _fade(accent, 1.0 - i / FEED_ROWS), accent)
                fr.show()
            else:
                fr.hide()

    def _export(self):
        m = self.model.dps
        if m.total <= 0 and not m.per_skill:
            self.summary.setText("nothing to export yet")
            return
        enc = self._last_encounter or enc_mod.snapshot(
            m, boss=self.model.dungeon_boss or "",
            deaths=getattr(self.model, "deaths", 0), ended_at=time.monotonic())
        label = (names.any_name(enc.boss) or enc.boss or "encounter").replace(" ", "_")
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Export encounter", f"{label}_skills.json",
            "JSON (*.json);;CSV (*.csv)")
        if not path:
            return
        try:
            enc_mod.export(enc, path)
            self.summary.setText(f"exported -> {path.rsplit('/', 1)[-1]}")
        except OSError as e:
            self.summary.setText(f"export failed: {e}")

    def closeEvent(self, e):
        self._timer.stop()
        super().closeEvent(e)

