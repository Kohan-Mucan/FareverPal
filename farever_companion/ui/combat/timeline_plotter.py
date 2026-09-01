"""DPS burst timeline plotter widget.

Renders an antialiased 2D vector graph of encounter DPS over time.
Supports single encounter timeline view and multi-player compare grouped-bar mode.
"""
from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from .. import theme
from ...core.dps_tracker import CombatSession

# Color palette for compare line graphs (up to 5 players)
COMPARE_COLORS = [
    QtGui.QColor("#4ECDC4"),  # Cyan / Teal
    QtGui.QColor("#FFD166"),  # Gold / Yellow
    QtGui.QColor("#06D6A0"),  # Emerald Green
    QtGui.QColor("#FF6B6B"),  # Coral Red
    QtGui.QColor("#A663CC"),  # Purple / Lavender
]


class _CombatTimelinePlotter(QtWidgets.QFrame):
    """DPS burst timeline with phase, death markers, and multi-player compare mode."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("TimelinePlotter")
        self.setStyleSheet(
            f"_CombatTimelinePlotter {{ background-color: {theme.PANEL}; "
            f"border: 1px solid {theme.BORDER}; border-radius: 6px; }}"
        )
        self.setFixedHeight(140)
        self.setMouseTracking(True)
        self.session: CombatSession | None = None
        self.hover_x: float | None = None
        self.compare_players: list[str] = []

    def set_compare_players(self, player_names: list[str] | None):
        """Set active compare players for multi-line graph mode."""
        names = [n for n in (player_names or []) if n]
        if getattr(self, "compare_players", []) != names:
            self.compare_players = names
            self.update()

    def set_session(self, session: CombatSession | None):
        changed = (
            session is not self.session
            or (session and (
                len(session.timeline) != getattr(self, "_last_timeline_len", 0)
                or int(session.duration) != getattr(self, "_last_plot_dur", 0)
                or len(session.deaths) != getattr(self, "_last_deaths_len", 0)
            ))
        )
        self.session = session
        if changed:
            self._last_timeline_len = len(session.timeline) if session else 0
            self._last_plot_dur = int(session.duration) if session else 0
            self._last_deaths_len = len(session.deaths) if session else 0
            self.update()

    def mouseMoveEvent(self, e: QtGui.QMouseEvent):
        self.hover_x = e.position().x()
        self.update()
        super().mouseMoveEvent(e)

    def leaveEvent(self, e: QtCore.QEvent):
        self.hover_x = None
        self.update()
        super().leaveEvent(e)

    def paintEvent(self, event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)

        w = self.width()
        h = self.height()
        pad_l, pad_r, pad_t, pad_b = 50, 20, 22, 22
        plot_w = max(10, w - pad_l - pad_r)
        plot_h = max(10, h - pad_t - pad_b)

        grid_pen = QtGui.QPen(QtGui.QColor(theme.BORDER), 1, QtCore.Qt.DashLine)
        p.setPen(grid_pen)
        for y_frac in (0.25, 0.5, 0.75):
            gy = pad_t + plot_h * y_frac
            p.drawLine(pad_l, int(gy), pad_l + plot_w, int(gy))

        if not self.session or (not self.session.timeline and not self.session.player_timeline):
            p.setPen(QtGui.QColor(theme.DIM))
            font = p.font()
            font.setPointSize(10)
            p.setFont(font)
            p.drawText(QtCore.QRectF(0, 0, w, h), QtCore.Qt.AlignCenter,
                       "📈 Waiting for combat events to plot burst timeline...")
            return

        active_compare = []
        if self.session and self.compare_players:
            known = set(self.session.players.keys()) | set(getattr(self.session, "player_timeline", {}).keys())
            known_map = {_clean_player_name(k): k for k in known}
            for cp in self.compare_players:
                cp_clean = _clean_player_name(cp)
                if cp_clean in known_map:
                    active_compare.append(known_map[cp_clean])
                elif cp:
                    active_compare.append(cp)

        is_compare_mode = len(active_compare) >= 1

        if is_compare_mode:
            self._paint_compare_lines(p, w, h, pad_l, pad_r, pad_t, pad_b, plot_w, plot_h, active_compare)
        else:
            self._paint_single_timeline(p, w, h, pad_l, pad_r, pad_t, pad_b, plot_w, plot_h)

    def _paint_single_timeline(self, p: QtGui.QPainter, w, h, pad_l, pad_r, pad_t, pad_b, plot_w, plot_h):
        dur = max(1.0, self.session.duration)
        max_sec = max(int(dur), max(self.session.timeline.keys(), default=0) + 1)

        sel_name = getattr(self.session, "selected_player", "")
        if sel_name and self.session and hasattr(self.session, "players"):
            raw_tl = _get_player_timeline_flow(self.session, sel_name)
        else:
            raw_tl = self.session.timeline

        timeline = _smooth_timeline(raw_tl, max_sec, window=3)
        axis_max = _capped_axis_max(list(timeline.values()))
        max_scale = axis_max * 1.10

        font = p.font()
        font.setPointSize(8)
        p.setFont(font)
        p.setPen(QtGui.QColor(theme.MUTED))
        p.drawText(4, int(pad_t + 10), f"{axis_max:,.0f}/s")
        p.drawText(4, int(pad_t + plot_h / 2), f"{axis_max/2:,.0f}/s")
        p.drawText(4, int(pad_t + plot_h), "0/s")

        mins = int(max_sec // 60)
        secs = max_sec % 60
        p.drawText(pad_l, h - 6, "00:00")
        p.drawText(pad_l + plot_w - 36, h - 6, f"{mins:02d}:{secs:02d}")

        path = QtGui.QPainterPath()
        fill_path = QtGui.QPainterPath()

        points = []
        for sec in range(max_sec + 1):
            val = timeline.get(sec, 0.0)
            px = pad_l + (sec / max(1, max_sec)) * plot_w
            py = max(float(pad_t), pad_t + plot_h - (val / max_scale) * plot_h)
            points.append((px, py, val, sec))

        if points:
            path.moveTo(points[0][0], points[0][1])
            fill_path.moveTo(points[0][0], points[0][1])

            for k in range(1, len(points)):
                x0, y0, _, _ = points[k - 1]
                x1, y1, _, _ = points[k]
                cx = (x0 + x1) / 2.0
                path.quadTo(cx, y0, x1, y1)
                fill_path.quadTo(cx, y0, x1, y1)

            fill_path.lineTo(points[-1][0], pad_t + plot_h)
            fill_path.lineTo(pad_l, pad_t + plot_h)
            fill_path.closeSubpath()

            grad = QtGui.QLinearGradient(0, pad_t, 0, pad_t + plot_h)
            c_top = QtGui.QColor(theme.ACCENT)
            c_top.setAlpha(90)
            c_bot = QtGui.QColor(theme.ACCENT)
            c_bot.setAlpha(20)
            grad.setColorAt(0.0, c_top)
            grad.setColorAt(1.0, c_bot)
            p.fillPath(fill_path, QtGui.QBrush(grad))

            line_pen = QtGui.QPen(QtGui.QColor(theme.ACCENT_LIGHT), 2)
            p.setPen(line_pen)
            p.drawPath(path)

        if self.session.burst_peak > 0:
            peak_x = pad_l + (self.session.burst_peak_time / max(1, max_sec)) * plot_w
            peak_y = max(float(pad_t), pad_t + plot_h - (self.session.burst_peak / max_scale) * plot_h)
            p.setBrush(QtGui.QColor(theme.GOLD))
            p.setPen(QtGui.QPen(QtGui.QColor("#000000"), 1))
            p.drawEllipse(QtCore.QPointF(peak_x, peak_y), 4.5, 4.5)

            p.setPen(QtGui.QColor(theme.GOLD))
            font = p.font()
            font.setPointSize(8)
            font.setBold(True)
            p.setFont(font)
            p.drawText(int(peak_x - 30), int(peak_y - 7),
                       f"⚡ Burst: {self.session.burst_peak:,.0f}/s")

        for d_t, p_name in self.session.deaths:
            dx = pad_l + (d_t / max(1, max_sec)) * plot_w
            death_pen = QtGui.QPen(QtGui.QColor(theme.DANGER), 1, QtCore.Qt.DashLine)
            p.setPen(death_pen)
            p.drawLine(int(dx), pad_t, int(dx), pad_t + plot_h)

            p.setPen(QtGui.QColor(theme.DANGER))
            font = p.font()
            font.setPointSize(8)
            font.setBold(True)
            p.setFont(font)
            p.drawText(int(dx - 12), pad_t + 12, f"☠ {p_name}")

        if self.hover_x is not None and pad_l <= self.hover_x <= pad_l + plot_w:
            cursor_pen = QtGui.QPen(QtGui.QColor(theme.TEXT), 1, QtCore.Qt.DotLine)
            p.setPen(cursor_pen)
            p.drawLine(int(self.hover_x), pad_t, int(self.hover_x), pad_t + plot_h)

            rel_sec = int(((self.hover_x - pad_l) / plot_w) * max_sec)
            cur_dmg = timeline.get(rel_sec, 0.0)
            m_s = int(rel_sec // 60)
            s_s = rel_sec % 60
            tooltip_txt = f"{m_s:02d}:{s_s:02d} · {cur_dmg:,.0f} DPS"

            p.setBrush(QtGui.QColor(theme.SURFACE))
            p.setPen(QtGui.QPen(QtGui.QColor(theme.ACCENT), 1))
            box_x = min(self.hover_x + 8, w - 125)
            p.drawRoundedRect(QtCore.QRectF(box_x, pad_t + 8, 115, 20), 3, 3)
            p.setPen(QtGui.QColor(theme.TEXT))
            font = p.font()
            font.setPointSize(8)
            font.setBold(True)
            p.setFont(font)
            p.drawText(QtCore.QRectF(box_x, pad_t + 8, 115, 20), QtCore.Qt.AlignCenter, tooltip_txt)

    def _paint_compare_lines(self, p: QtGui.QPainter, w, h, pad_l, pad_r, pad_t, pad_b, plot_w, plot_h, active_compare: list[str]):
        dur = max(1.0, self.session.duration)
        max_sec = max(int(dur), max(self.session.timeline.keys(), default=0) + 1)

        # Grouped bar chart: bucket each player's RAW per-second damage into
        # 10s windows. Raw bin totals — no EMA smoothing — so each window
        # shows the real winner. Binning also tames single-second opener
        # spikes without hiding them.
        bin_width = 10
        nbins = max(1, -(-(max_sec + 1) // bin_width))  # ceil div

        bin_totals: dict[str, list[float]] = {}
        all_bins: list[float] = []
        for p_name in active_compare:
            raw_tl = _get_player_timeline_flow(self.session, p_name)
            bins = [0.0] * nbins
            for sec in range(max_sec + 1):
                v = raw_tl.get(sec, 0.0)
                if v:
                    bins[min(nbins - 1, sec // bin_width)] += v
            bin_totals[p_name] = bins
            all_bins.extend(bins)

        axis_max = _capped_axis_max(all_bins)
        max_scale = axis_max * 1.15

        font = p.font()
        font.setPointSize(8)
        p.setFont(font)
        p.setPen(QtGui.QColor(theme.MUTED))
        p.drawText(4, int(pad_t + 10), f"{axis_max:,.0f}")
        p.drawText(4, int(pad_t + plot_h / 2), f"{axis_max/2:,.0f}")
        p.drawText(4, int(pad_t + plot_h), "0")

        mins = int(max_sec // 60)
        secs = max_sec % 60
        p.drawText(pad_l, h - 6, "00:00")
        p.drawText(pad_l + plot_w - 36, h - 6, f"{mins:02d}:{secs:02d}")

        # Render Legend at Top Right
        leg_x = pad_l + plot_w - 10
        for i, p_name in enumerate(reversed(active_compare)):
            idx = len(active_compare) - 1 - i
            color = COMPARE_COLORS[idx % len(COMPARE_COLORS)]
            clean_nm = _clean_player_display(p_name)
            fm = QtGui.QFontMetrics(font)
            txt_w = fm.horizontalAdvance(clean_nm) + 18
            leg_x -= txt_w
            p.setBrush(QtGui.QBrush(color))
            p.setPen(QtCore.Qt.NoPen)
            p.drawRoundedRect(QtCore.QRectF(leg_x, 4, 10, 10), 2, 2)
            p.setPen(QtGui.QColor(theme.TEXT))
            p.drawText(int(leg_x + 14), 13, clean_nm)
            leg_x -= 10

        # Draw grouped bars: one group per time window, one bar per player.
        # The window winner renders full-opacity, losers dimmed.
        n = len(active_compare)
        slot_w = plot_w / max(1, nbins)
        grp_w = slot_w * 0.72
        bar_w = max(2.0, (grp_w - (n - 1) * 1.5) / max(1, n))
        base_y = pad_t + plot_h

        for b in range(nbins):
            vals = [bin_totals[pn][b] for pn in active_compare]
            best = max(vals) if vals else 0.0
            gx = pad_l + b * slot_w + (slot_w - (bar_w * n + 1.5 * (n - 1))) / 2.0
            for i, p_name in enumerate(active_compare):
                val = vals[i]
                if val <= 0:
                    continue
                color = COMPARE_COLORS[i % len(COMPARE_COLORS)]
                bar_col = QtGui.QColor(color)
                bar_col.setAlpha(255 if val >= best else 140)
                bh = min(float(plot_h), (val / max_scale) * plot_h)
                bx = gx + i * (bar_w + 1.5)
                p.setBrush(QtGui.QBrush(bar_col))
                p.setPen(QtCore.Qt.NoPen)
                p.drawRoundedRect(QtCore.QRectF(bx, base_y - bh, bar_w, bh), 1.5, 1.5)

        for d_t, p_name in self.session.deaths:
            if any(_clean_player_name(p_name) == _clean_player_name(cp) for cp in active_compare):
                dx = pad_l + (d_t / max(1, max_sec)) * plot_w
                death_pen = QtGui.QPen(QtGui.QColor(theme.DANGER), 1, QtCore.Qt.DashLine)
                p.setPen(death_pen)
                p.drawLine(int(dx), pad_t, int(dx), pad_t + plot_h)

                p.setPen(QtGui.QColor(theme.DANGER))
                font = p.font()
                font.setPointSize(8)
                font.setBold(True)
                p.setFont(font)
                p.drawText(int(dx - 12), pad_t + 12, f"☠ {p_name}")

        # Hover Tooltip
        if self.hover_x is not None and pad_l <= self.hover_x <= pad_l + plot_w:
            cursor_pen = QtGui.QPen(QtGui.QColor(theme.TEXT), 1, QtCore.Qt.DotLine)
            p.setPen(cursor_pen)
            p.drawLine(int(self.hover_x), pad_t, int(self.hover_x), pad_t + plot_h)

            rel_sec = int(((self.hover_x - pad_l) / plot_w) * max_sec)
            b_idx = min(nbins - 1, max(0, rel_sec // bin_width))
            w0 = b_idx * bin_width
            w1 = min(max_sec, w0 + bin_width - 1)

            tt_lines = [f"⏱ {w0//60:02d}:{w0%60:02d}–{w1//60:02d}:{w1%60:02d} ({bin_width}s)"]
            for idx, p_name in enumerate(active_compare):
                val = bin_totals[p_name][b_idx]
                clean_nm = _clean_player_display(p_name)
                tt_lines.append(f"{clean_nm}: {val:,.0f}")

            tt_h = len(tt_lines) * 15 + 8
            tt_w = 145
            box_x = min(self.hover_x + 8, w - tt_w - 8)
            box_y = pad_t + 4

            p.setBrush(QtGui.QColor(theme.SURFACE))
            p.setPen(QtGui.QPen(QtGui.QColor(theme.ACCENT), 1))
            p.drawRoundedRect(QtCore.QRectF(box_x, box_y, tt_w, tt_h), 4, 4)

            p.setFont(p.font())
            for ly_idx, line in enumerate(tt_lines):
                if ly_idx == 0:
                    p.setPen(QtGui.QColor(theme.TEXT))
                    font = p.font()
                    font.setPointSize(8)
                    font.setBold(True)
                    p.setFont(font)
                else:
                    c = COMPARE_COLORS[(ly_idx - 1) % len(COMPARE_COLORS)]
                    p.setPen(c)
                    font = p.font()
                    font.setBold(False)
                    p.setFont(font)
                p.drawText(QtCore.QRectF(box_x + 6, box_y + 4 + ly_idx * 15, tt_w - 12, 14),
                           QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, line)


import re


def _clean_player_name(name: str) -> str:
    """Strip parenthetical tags like (Priest), (Mage), (You), (YOU), (pet) to get canonical name."""
    if not name:
        return ""
    return re.sub(r"\s*\([^)]*\)", "", name).strip().lower()


def _clean_player_display(name: str) -> str:
    """Format player name for display in legend/tooltips e.g. 'PlayerOne'."""
    if not name:
        return ""
    return re.sub(r"\s*\([^)]*\)", "", name).strip()


def _capped_axis_max(values: list[float], pct: float = 0.95, floor_frac: float = 0.20) -> float:
    """Y-axis top that ignores extreme opener spikes so the rest of the fight stays readable.

    Uses a high percentile of the plotted values as the axis anchor instead of the
    single biggest second; anything above it is drawn clipped at the top edge (peak
    dots still mark the true maxima, hover still shows true values). The floor keeps
    flat/noise-only fights from zooming in on nothing.
    """
    vals = [v for v in values if v > 0]
    if not vals:
        return 1.0
    true_max = max(vals)
    ordered = sorted(vals)
    anchor = ordered[min(len(ordered) - 1, int(len(ordered) * pct))]
    anchor = max(anchor, true_max * floor_frac)
    return max(1.0, anchor)


def _smooth_timeline(raw_tl: dict[int, float], max_sec: int, window: int = 3) -> dict[int, float]:
    """Compute Exponential Moving Average (EMA) gliding DPS curve so graph curves up smoothly without 0-drops."""
    if not raw_tl:
        return {}
    smoothed = {}
    current_dps = 0.0
    alpha = 0.28  # Response factor for smooth gliding curve without 0-drops between GCDs
    for sec in range(max_sec + 1):
        inst_dmg = raw_tl.get(sec, 0.0)
        current_dps = (alpha * inst_dmg) + ((1.0 - alpha) * current_dps)
        smoothed[sec] = current_dps
    return smoothed


def _calculate_dps_timeline(raw_tl: dict[int, float], max_sec: int) -> dict[int, float]:
    """Calculate cumulative DPS over time (00:00 -> max_sec) so graph rises up smoothly and ends at true DPS."""
    dps_curve = {}
    cum_dmg = 0.0
    for sec in range(max_sec + 1):
        cum_dmg += raw_tl.get(sec, 0.0)
        if sec == 0:
            dps_curve[0] = cum_dmg
        else:
            dps_curve[sec] = cum_dmg / float(sec)
    return dps_curve


def _get_player_timeline_flow(session: CombatSession | None, p_name: str, metric: str = "damage") -> dict[int, float]:
    """Retrieve exact second-by-second damage or healing flow for a player from hits event log or player_timeline."""
    if not session:
        return {}

    clean_p = _clean_player_name(p_name)
    dur = max(1.0, session.duration)
    max_sec = max(int(dur), 1)
    flow: dict[int, float] = {sec: 0.0 for sec in range(max_sec + 1)}

    # 1. Aggregate from session.hits log for real hit-by-hit event flow
    found_hits = False
    if hasattr(session, "hits") and session.hits:
        start_ts = session.start_time or (session.hits[-1].timestamp if session.hits else 0.0)
        is_heal_mode = (metric == "healing")

        for hit in session.hits:
            if is_heal_mode != getattr(hit, "is_heal", False):
                continue
            if _clean_player_name(hit.caster_name) == clean_p:
                sec = int(hit.timestamp - start_ts)
                if 0 <= sec <= max_sec:
                    flow[sec] = flow.get(sec, 0.0) + hit.damage
                    found_hits = True

    if found_hits and any(v > 0 for v in flow.values()):
        return flow

    # 2. Check session.player_timeline
    if hasattr(session, "player_timeline") and session.player_timeline:
        for k, v in session.player_timeline.items():
            if _clean_player_name(k) == clean_p and v:
                for sec, amt in v.items():
                    if 0 <= sec <= max_sec:
                        flow[sec] = flow.get(sec, 0.0) + amt
                if any(v > 0 for v in flow.values()):
                    return flow

    # 3. Fallback for mock/test data: build realistic burst spike flow based on skills
    p_obj = session.players.get(p_name)
    if not p_obj and hasattr(session, "players"):
        for k, p in session.players.items():
            if _clean_player_name(k) == clean_p:
                p_obj = p
                break

    if p_obj:
        tot = p_obj.heals if metric == "healing" else p_obj.total_damage
        if tot > 0:
            skills_list = p_obj.ranked_skills() if metric != "healing" else p_obj.ranked_heal_skills()
            if skills_list:
                step = max(1, max_sec // max(1, len(skills_list)))
                for idx, sp in enumerate(skills_list):
                    sk_sec = min(max_sec, (idx * step) + 1)
                    sk_val = getattr(sp, "heals" if metric == "healing" else "damage", sp.total)
                    flow[sk_sec] = flow.get(sk_sec, 0.0) + sk_val
            else:
                flow[1] = tot * 0.4
                if max_sec >= 3:
                    flow[max_sec // 2] = tot * 0.35
                if max_sec >= 5:
                    flow[max_sec - 1] = tot * 0.25

    return flow

