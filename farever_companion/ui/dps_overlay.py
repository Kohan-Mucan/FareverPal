"""DPS meter overlay, the headline panel.

Big live DPS, a sparkline, a survivability strip (incoming DTPS, own HP, death
recap), and reviewable recent-cycle history with a local personal-best parse per
boss. The per-skill breakdown lives in its own Skills panel (skill_overlay.py) so
this stays uncrowded. Self DPS only (self-only by design, the game only renders
a DamageDisplay for your own client). Read-only.
"""
from __future__ import annotations

import time
from collections import deque

from PySide6 import QtCore, QtWidgets

from . import theme
from .overlay_base import OverlayWindow
from .widgets import Sparkline, Bar
from .components import SectionHeader
from .skill_table import abbr as _abbr, SkillRow
from ..data import names, icons
from ..combat import encounter as enc_mod

POLL_MS = 400
HIST = 80
N_CYCLES = 6
N_ENEMY = 5             # by-enemy-type breakdown rows (HP-diff)
IDLE_GAP = 3.0          # encounter ends after this long with no new damage


class _HistRow(QtWidgets.QWidget):
    """One past combat cycle: dps / peak / time / kills, mono columns."""
    _COLS = (("dps", 64, theme.ACCENT), ("peak", 58, theme.MUTED),
             ("time", 44, theme.MUTED), ("k", 26, theme.GOLD))

    def __init__(self, header: bool = False):
        super().__init__()
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(2, 0, 2, 0)
        lay.setSpacing(4)
        self._labels = {}
        for key, w, col in self._COLS:
            lbl = QtWidgets.QLabel(key.upper() if header else "")
            lbl.setObjectName("Mono")
            lbl.setFixedWidth(w)
            lbl.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            lbl.setStyleSheet(f"color:{theme.DIM if header else col};background:transparent;")
            self._labels[key] = lbl
            lay.addWidget(lbl)
        lay.addStretch(1)

    def set(self, dps, peak, tm, k):
        self._labels["dps"].setText(dps)
        self._labels["peak"].setText(peak)
        self._labels["time"].setText(tm)
        self._labels["k"].setText(k)


class _Survival(QtWidgets.QWidget):
    """Incoming-damage / survivability strip: own-HP bar, DTPS / taken / deaths,
    and a death-recap line of the last few hits taken."""
    def __init__(self):
        super().__init__()
        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)
        self.header = SectionHeader("SURVIVABILITY", color=theme.DANGER)
        v.addWidget(self.header)
        hp_row = QtWidgets.QHBoxLayout()
        hp_row.setSpacing(8)
        lab = QtWidgets.QLabel("HP")
        lab.setObjectName("Mono")
        lab.setStyleSheet(f"color:{theme.MUTED};background:transparent;")
        self.hp_bar = Bar(theme.GOOD)
        self.hp_bar.setMinimumHeight(10)
        hp_row.addWidget(lab)
        hp_row.addWidget(self.hp_bar, 1)
        v.addLayout(hp_row)
        self.stat = QtWidgets.QLabel("")
        self.stat.setObjectName("Mono")
        self.stat.setStyleSheet(f"color:{theme.MUTED};background:transparent;")
        v.addWidget(self.stat)
        self.recap = QtWidgets.QLabel("")
        self.recap.setObjectName("Mono")
        self.recap.setWordWrap(True)
        self.recap.setStyleSheet(f"color:{theme.DIM};background:transparent;")
        v.addWidget(self.recap)

    def update_from(self, m, hp_frac, now):
        dtps = m.dtps(now)
        deaths = getattr(m, "_deaths_view", 0)
        self.stat.setText(f"DTPS {dtps:,.0f}   TAKEN {m.taken_total / 1000:,.1f}K"
                          f"   DEATHS {deaths}")
        if hp_frac is not None:
            self.hp_bar.set(hp_frac)
            col = theme.GOOD if hp_frac > 0.5 else (theme.GOLD if hp_frac > 0.2 else theme.DANGER)
            self.hp_bar.set_color(col)
        recap = list(m.recent_incoming)[-4:]
        if recap:
            parts = [f"{names.skill_name(ev.skill) or ev.skill} {ev.amount:,.0f}" for ev in recap]
            self.recap.setText("RECAP: " + " · ".join(parts))
        else:
            self.recap.setText("")

    def has_data(self, m) -> bool:
        return bool(m.taken_total > 0 or m.recent_incoming)


class DpsOverlay(OverlayWindow):
    def __init__(self, model, settings, parent=None):
        super().__init__("DPS", settings, geo_key="dps", parent=parent)
        self._page_key = "combat"
        self.model = model
        self.s = settings
        self.radius = settings.dps_radius
        self._hist: deque[float] = deque([0.0] * HIST, maxlen=HIST)
        self._last_encounter = None
        self._best_flash_until = 0.0

        # title bar: range −/+, reset, export
        self._range_lbl = QtWidgets.QLabel()
        self._range_lbl.setObjectName("Mono")
        minus = QtWidgets.QPushButton("−"); minus.setObjectName("Icon")
        plus = QtWidgets.QPushButton("+"); plus.setObjectName("Icon")
        exp = QtWidgets.QPushButton(); exp.setObjectName("Icon")
        exp.setIcon(icons.ui_qicon("download", theme.MUTED, 14))
        exp.setToolTip("Export last encounter (CSV / JSON)")
        rst = QtWidgets.QPushButton(); rst.setObjectName("Icon")
        rst.setIcon(icons.ui_qicon("refresh-cw", theme.MUTED, 14))
        rst.setToolTip("Reset session")
        minus.clicked.connect(lambda: self._bump(-5))
        plus.clicked.connect(lambda: self._bump(5))
        exp.clicked.connect(self._export)
        rst.clicked.connect(self._reset)
        for w in (rst, exp, minus, self._range_lbl, plus):
            self.titlebar.extra.insertWidget(self.titlebar.extra.count() - 3, w)
        self._update_range()

        row = QtWidgets.QHBoxLayout()
        row.setSpacing(6)
        self.big = QtWidgets.QLabel("0"); self.big.setObjectName("Big")
        self._unit = QtWidgets.QLabel("DPS"); self._unit.setObjectName("Section")
        row.addWidget(self.big)
        row.addWidget(self._unit, 0, QtCore.Qt.AlignBottom)
        row.addStretch(1)
        self.content.addLayout(row)
        self.sub = QtWidgets.QLabel("idle"); self.sub.setObjectName("Mono")
        self.content.addWidget(self.sub)
        self.chart = Sparkline(theme.ACCENT)
        self.content.addWidget(self.chart)

        # by-enemy-type breakdown (HP-diff) - which enemies your damage is going into
        self._tgt_header = SectionHeader("BY ENEMY")
        self.content.addWidget(self._tgt_header)
        self._tgt_rows = [SkillRow() for _ in range(N_ENEMY)]
        for r in self._tgt_rows:
            r.apply_columns([])
            self.content.addWidget(r)

        # survivability strip (incoming / HP / death recap)
        self._survival = _Survival()
        self.content.addWidget(self._survival)

        # recent-cycle history (encounters between idle gaps)
        self._cycles: deque[dict] = deque(maxlen=N_CYCLES)
        self._prev_total = 0.0
        self._cyc: dict | None = None
        self._last_dmg_t = 0.0
        self._hist_header = SectionHeader("RECENT CYCLES")
        self.content.addWidget(self._hist_header)
        self._hist_head_row = _HistRow(header=True)
        self.content.addWidget(self._hist_head_row)
        self._hist_rows = [_HistRow() for _ in range(N_CYCLES)]
        for r in self._hist_rows:
            self.content.addWidget(r)

        self.content.addStretch(1)

        self.enable_resize_grip()

        self.setMinimumWidth(round(220 * self.s.dps_scale))
        self.apply_dps_mode(self.s.dps_mode)
        self._retint(self._accent)
        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(POLL_MS)

    # --- size modes: small (number) / medium (+graph) / default (+survival+cycles) --
    _MODE_SIZE = {"small": (240, 120), "medium": (300, 230), "default": (320, 430)}

    def apply_dps_mode(self, mode: str) -> None:
        mode = mode if mode in self._MODE_SIZE else "default"
        self._mode = mode
        med = mode in ("medium", "default")    # + sparkline
        full = mode == "default"               # + survivability + recent cycles
        self.sub.setVisible(med)
        self.chart.setVisible(med)
        self._survival.setVisible(full)
        for w in (self._tgt_header, *self._tgt_rows):
            w.setVisible(med)               # by-enemy shows from medium up
        for w in (self._hist_header, self._hist_head_row, *self._hist_rows):
            w.setVisible(full)
        self._base_w, self._base_h = self._MODE_SIZE[mode]
        self.apply_scale(self._scale)

    def _retint(self, accent: str) -> None:
        if not accent:
            return
        self.chart.set_color(accent)
        self._tgt_header.set_color(accent)
        self._hist_header.set_color(accent)

    def _bump(self, d):
        self.radius = max(0, self.radius + d)
        self._update_range()

    def _update_range(self):
        self._range_lbl.setText("∞" if self.radius == 0 else str(self.radius))

    def _reset(self):
        reset = getattr(self.model, "reset_combat", None)
        (reset or self.model.dps.reset)()
        self._cycles.clear()
        self._cyc = None
        self._prev_total = 0.0
        self._last_encounter = None

    def _tick(self):
        try:
            m = self.model.sample_combat(radius=float(self.radius))
        except Exception as e:
            self.sub.setText(f"err: {e}")
            return
        m._deaths_view = getattr(self.model, "deaths", 0)
        dps = m.current_dps()
        self._hist.append(dps)
        self.chart.set_color(self.s.hud_accent)
        self.big.setText(f"{dps:,.0f}")
        now = time.monotonic()
        self._set_sub(m, now)
        self.chart.set_data(list(self._hist))
        if self._mode in ("medium", "default"):
            self._draw_targets(m, self.s.hud_accent)
        if self._mode == "default":
            self._draw_survival(m, now)
        self._update_cycles(m, now, dps)
        if self._mode == "default":
            self._render_history()

    def _set_sub(self, m, now):
        if now < self._best_flash_until:
            self.sub.setText("★ NEW PERSONAL BEST ★")
            return
        if m.total > 0:
            extra = f"   K {m.kills}" if m.kills else ""
            burst = m.burst(5.0)
            bstr = f"   BURST {burst / 1000:,.1f}K/5s" if burst else ""
            ttk = self._boss_ttk(m)
            tstr = f"   TTK {ttk:.1f}s" if ttk is not None else ""
            self.sub.setText(
                f"PEAK {m.peak:,.0f}   TOTAL {m.total / 1000:,.1f}K / {m.duration:.0f}S"
                f"{extra}{bstr}{tstr}")
        else:
            self.sub.setText("idle — no damage in range")

    def _boss_ttk(self, m):
        try:
            _bid, present, hp = self.model.boss_state()
        except Exception:
            return None
        if not present or not hp:
            return None
        return m.time_to_kill(hp)

    def _draw_targets(self, m, accent):
        """By-enemy-type breakdown (HP-diff). Which enemies your damage went into."""
        top = m.top_targets(len(self._tgt_rows))
        self._tgt_header.setVisible(bool(top))
        mx = top[0][1] if top else 1.0
        for i, r in enumerate(self._tgt_rows):
            if i < len(top):
                uid, dmg = top[i]
                r.set_row(names.any_name(uid) or uid, dmg / mx if mx else 0, {},
                          bar_label=_abbr(dmg), sheet="unit", id_=uid, accent=accent)
                r.show()
            else:
                r.hide()

    def _draw_survival(self, m, now):
        hp = self.model.player_hp_frac()
        # show the strip when we have own-HP (fast, always available) OR incoming
        # data (DTPS/recap, only with the per-skill source).
        if hp is None and not self._survival.has_data(m):
            self._survival.setVisible(False)
            return
        self._survival.setVisible(self.s.dps_survival)
        self._survival.update_from(m, hp, now)

    def _update_cycles(self, m, now, dps):
        total = m.total
        if total < self._prev_total - 1e-6:
            self._cyc = None
            self._prev_total = total
        elif total > self._prev_total + 1e-6:
            self._last_dmg_t = now
            if self._cyc is None:
                self._cyc = {"t0": now, "tot0": self._prev_total,
                             "k0": m.kills, "peak": 0.0}
            self._prev_total = total
        if self._cyc is not None:
            self._cyc["peak"] = max(self._cyc["peak"], dps)
            if now - self._last_dmg_t > IDLE_GAP:
                dur = max(0.1, self._last_dmg_t - self._cyc["t0"])
                tot = m.total - self._cyc["tot0"]
                if tot > 0:
                    self._cycles.appendleft({
                        "dps": tot / dur, "peak": self._cyc["peak"],
                        "tot": tot, "dur": dur, "k": m.kills - self._cyc["k0"]})
                    self._capture_encounter(m, now)
                self._cyc = None

    def _capture_encounter(self, m, now):
        boss = self.model.dungeon_boss or ""
        deaths = getattr(self.model, "deaths", 0)
        enc = enc_mod.snapshot(m, boss=boss, deaths=deaths, ended_at=now)
        self._last_encounter = enc
        if boss and enc_mod.update_best(self.s.dps_best, enc):
            self._best_flash_until = now + 4.0
            self.s.save()
        elif boss:
            self.s.save()

    def _render_history(self):
        for i, r in enumerate(self._hist_rows):
            if i < len(self._cycles):
                c = self._cycles[i]
                r.set(_abbr(c["dps"]), _abbr(c["peak"]), f"{c['dur']:.0f}s",
                      str(c["k"]) if c["k"] else "·")
                r.show()
            else:
                r.hide()
        self._hist_header.set_tag("" if self._cycles else "NONE YET")

    def _export(self):
        enc = self._last_encounter
        if enc is None:
            m = self.model.dps
            if m.total <= 0:
                self.sub.setText("nothing to export yet")
                return
            enc = enc_mod.snapshot(m, boss=self.model.dungeon_boss or "",
                                   deaths=getattr(self.model, "deaths", 0),
                                   ended_at=time.monotonic())
        label = (names.any_name(enc.boss) or enc.boss or "encounter").replace(" ", "_")
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Export encounter", f"{label}_dps.json",
            "JSON (*.json);;CSV (*.csv)")
        if not path:
            return
        try:
            enc_mod.export(enc, path)
            self.sub.setText(f"exported -> {path.rsplit('/', 1)[-1]}")
        except OSError as e:
            self.sub.setText(f"export failed: {e}")

    def closeEvent(self, e):
        self._timer.stop()
        super().closeEvent(e)
