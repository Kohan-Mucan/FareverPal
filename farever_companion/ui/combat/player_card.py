"""Player cards and metric summary components for the Combat page.

Includes the player cards displayed in class columns, the proportional stacked
skill bar, the compact stat strip, and single-metric cards.
"""
from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from .. import theme
from .. import components as C
from ...core.dps_tracker import PlayerParse, SkillParse


def _ranked_skill_total(p: PlayerParse) -> list[SkillParse]:
    """Skills ranked by combined damage + heals (hybrid skills rank by both)."""
    return sorted(p.skills.values(), key=lambda s: s.total, reverse=True)


def _cp_card_alive(card) -> bool:
    """True when a cached card still has a live C++ object.

    Cached references can outlive their widgets (page eviction deletes the
    whole tree, column prunes deleteLater frames) — touching them raises
    RuntimeError, so guard every cache hit.
    """
    try:
        card.isHidden()
        return True
    except RuntimeError:
        return False


class _StatCardWidget(QtWidgets.QFrame):
    """Clean stat card for one combat metric."""

    def __init__(self, title: str, value: str = "--", sub: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("StatCardFrame")
        self.setStyleSheet(
            f"_StatCardWidget {{ background-color: {theme.PANEL}; "
            f"border: 1px solid {theme.BORDER}; border-radius: 6px; padding: 6px; }}"
        )
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(3)

        self.title_lbl = QtWidgets.QLabel(title.upper())
        self.title_lbl.setStyleSheet(
            f"font-size: 10px; font-weight: 700; color: {theme.DIM}; letter-spacing: 1px;")
        lay.addWidget(self.title_lbl)

        self.val_lbl = QtWidgets.QLabel(value)
        self.val_lbl.setStyleSheet(
            f"font-size: 16px; font-weight: 800; color: {theme.TEXT}; font-family: monospace;")
        lay.addWidget(self.val_lbl)

        self.sub_lbl = QtWidgets.QLabel(sub)
        self.sub_lbl.setStyleSheet(f"font-size: 11px; color: {theme.MUTED};")
        lay.addWidget(self.sub_lbl)

    def set_value(self, val: str):
        self.val_lbl.setText(val)

    def set_sub(self, sub: str):
        self.sub_lbl.setText(sub)


class _StatStrip(QtWidgets.QFrame):
    """Color-coded encounter stat strip containing Duration, Target, Damage, Healing, Damage Taken, Burst, and Deaths."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("StatStrip")
        self.setStyleSheet(
            f"QFrame#StatStrip {{ background: {theme.PANEL}; "
            f"border: 1px solid {theme.BORDER}; border-radius: 6px; }}")
        self._row = QtWidgets.QHBoxLayout(self)
        self._row.setContentsMargins(12, 6, 12, 6)
        self._row.setSpacing(12)
        self._items: dict[str, tuple[QtWidgets.QLabel, QtWidgets.QLabel]] = {}

        configs = [
            ("dur", "FIGHT", theme.ACCENT_LIGHT),
            ("target", "TARGET", theme.GOLD),
            ("dmg", "DMG", theme.ACCENT_LIGHT),
            ("heal", "HEAL", theme.GOOD),
            ("taken", "TAKEN", theme.ORANGE),
            ("burst", "BURST", theme.DANGER),
            ("deaths", "DEATHS", theme.DANGER),
        ]

        for i, (key, caption, col) in enumerate(configs):
            cap = QtWidgets.QLabel(caption)
            cap.setStyleSheet(
                f"font-size: 9.5px; font-weight: 800; color: {theme.DIM}; "
                f"letter-spacing: 0.8px; background: transparent;")
            val = QtWidgets.QLabel("--")
            val.setStyleSheet(
                f"font-size: 13px; font-weight: 800; color: {col}; "
                f"font-family: monospace; background: transparent;")
            self._row.addWidget(cap)
            self._row.addWidget(val)
            self._items[key] = (cap, val)
            if i < len(configs) - 1:
                sep = QtWidgets.QLabel("·")
                sep.setStyleSheet(
                    f"color: {theme.BORDER}; font-size: 13px; background: transparent;")
                self._row.addWidget(sep)

        self._row.addStretch(1)

    def _vals(self):
        return {k: v for k, (c, v) in self._items.items()}

    def set_value(self, key: str, val: str):
        v = self._vals().get(key)
        if v is not None:
            v.setText(val)

    def set_sub(self, key: str, sub: str):
        pair = self._items.get(key)
        if pair is not None:
            pair[1].setToolTip(sub)
            pair[0].setToolTip(sub)


class _StackedSkillBar(QtWidgets.QWidget):
    """Proportional multi-colored segmented horizontal bar of top skills."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(12)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.segments: list[tuple[str, float, str]] = []

    def set_skills(self, skills: list, total_dmg: float):
        if total_dmg <= 0 or not skills:
            self.segments = []
            self.setToolTip("No skill damage recorded")
            self.update()
            return
        segs = []
        tooltip_lines = ["Skill Damage Distribution:"]
        for sp in skills[:8]:
            val = getattr(sp, "total", getattr(sp, "damage", 0.0))
            if val <= 0:
                continue
            pct = (val / total_dmg) * 100.0
            col = theme.skill_color(sp.skill_id)
            segs.append((sp.name, pct, col))
            tooltip_lines.append(f"• {sp.name}: {val:,.0f} ({pct:.1f}%)")
        self.segments = segs
        self.setToolTip("\n".join(tooltip_lines))
        self.update()

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        rect = self.rect()
        w = rect.width()
        h = rect.height()
        bg_path = QtGui.QPainterPath()
        bg_path.addRoundedRect(QtCore.QRectF(0, 0, w, h), 3, 3)
        painter.fillPath(bg_path, QtGui.QColor(theme.SURFACE))

        if not self.segments:
            return

        painter.setClipPath(bg_path)
        cur_x = 0.0
        for name, pct, col in self.segments:
            seg_w = (pct / 100.0) * w
            if seg_w < 1.0:
                continue
            painter.fillRect(QtCore.QRectF(cur_x, 0, seg_w, h), QtGui.QColor(col))
            cur_x += seg_w


class _PlayerCardWidget(QtWidgets.QFrame):
    """Compact player breakdown card in the class columns board.

    Clicking anywhere on the card selects it to inspect their skill breakdown on the right.
    Also has a direct ⚖ Compare shortcut button.
    """
    clicked = QtCore.Signal(str)          # emits player name
    compare_clicked = QtCore.Signal(str)  # emits player name

    def __init__(self, name: str, is_me: bool, parent=None):
        super().__init__(parent)
        self.name = name
        self.is_me = is_me
        self._is_selected = False
        self._zero_collapsed = False
        self._hero_class = ""

        self.setObjectName("PlayerCombatCard")
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self._update_card_style()

        self.c_lay = QtWidgets.QVBoxLayout(self)
        self.c_lay.setContentsMargins(8, 7, 8, 7)
        self.c_lay.setSpacing(4)

        # Header row: [#1] Name [YOU] ... Rate/s (top) / Total (Share) (bottom)
        h_row = QtWidgets.QHBoxLayout()
        h_row.setSpacing(6)

        self.r_lbl = QtWidgets.QLabel("#1")
        self.r_lbl.setStyleSheet("font-size: 11.5px; font-weight: 800; color: #facc15; font-family: monospace;")
        h_row.addWidget(self.r_lbl)

        info_box = QtWidgets.QVBoxLayout()
        info_box.setSpacing(1)
        info_box.setContentsMargins(0, 0, 0, 0)

        name_sub = QtWidgets.QHBoxLayout()
        name_sub.setSpacing(4)
        name_sub.setContentsMargins(0, 0, 0, 0)

        self.n_lbl = C.ElideLabel(name, 280)
        self.n_lbl.setStyleSheet(
            f"font-size: 11.5px; font-weight: 700; color: {theme.ACCENT_LIGHT if is_me else theme.TEXT};")
        name_sub.addWidget(self.n_lbl)

        self.you_tag = QtWidgets.QLabel("YOU")
        self.you_tag.setVisible(is_me)
        self.you_tag.setStyleSheet(
            f"font-size: 8px; font-weight: 800; color: {theme.ACCENT}; "
            f"background: {theme.ACCENT_DIM}; border-radius: 2px; padding: 1px 3px;")
        name_sub.addWidget(self.you_tag)
        name_sub.addStretch(1)
        info_box.addLayout(name_sub)

        self.cls_rank_lbl = QtWidgets.QLabel("")
        self.cls_rank_lbl.setStyleSheet(
            f"font-size: 8.5px; font-weight: 600; color: {theme.DIM};")
        info_box.addWidget(self.cls_rank_lbl)
        h_row.addLayout(info_box, 1)

        # Right side vertical metrics (Rate on top, Total & Share underneath)
        metric_col = QtWidgets.QVBoxLayout()
        metric_col.setSpacing(1)
        metric_col.setContentsMargins(0, 0, 0, 0)

        self.rate_lbl = QtWidgets.QLabel("0/s")
        self.rate_lbl.setObjectName("PlayerRate")
        self.rate_lbl.setStyleSheet(
            f"font-size: 12.5px; font-weight: 800; color: {theme.TEXT}; "
            f"font-family: ui-monospace, monospace;")
        self.rate_lbl.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        metric_col.addWidget(self.rate_lbl)

        self.total_lbl = QtWidgets.QLabel("0 (0%)")
        self.total_lbl.setObjectName("PlayerTotal")
        self.total_lbl.setStyleSheet(
            f"font-size: 9px; color: {theme.DIM}; "
            f"font-family: ui-monospace, monospace;")
        self.total_lbl.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        metric_col.addWidget(self.total_lbl)

        h_row.addLayout(metric_col)
        self.c_lay.addLayout(h_row)

        # Progress bar
        self.bar = QtWidgets.QProgressBar()
        self.bar.setFixedHeight(4)
        self.bar.setTextVisible(False)
        self.bar.setRange(0, 100)
        self.c_lay.addWidget(self.bar)

        # Bottom row: [⚡ 42% Crit] [✦ TopSkill] ... [⚖ Compare]
        self.b_row_widget = QtWidgets.QWidget()
        b_row = QtWidgets.QHBoxLayout(self.b_row_widget)
        b_row.setContentsMargins(0, 0, 0, 0)
        b_row.setSpacing(5)

        self.crit_lbl = QtWidgets.QLabel("⚡ 0%")
        self.crit_lbl.setStyleSheet(f"font-size: 9px; color: {theme.ORANGE}; font-weight: 700;")
        b_row.addWidget(self.crit_lbl)

        self.top_skill_lbl = QtWidgets.QLabel("✦ None")
        self.top_skill_lbl.setStyleSheet(f"font-size: 9px; color: {theme.MUTED}; font-weight: 600;")
        b_row.addWidget(self.top_skill_lbl)

        b_row.addStretch(1)

        self.comp_btn = QtWidgets.QPushButton("⚖ Compare")
        self.comp_btn.setToolTip("Compare this player head-to-head")
        self.comp_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; border: 1px solid {theme.BORDER}; "
            f"color: {theme.DIM}; font-size: 8.5px; font-weight: 700; border-radius: 3px; padding: 1px 4px; }}"
            f"QPushButton:hover {{ background: {theme.ACCENT_DIM}; color: {theme.ACCENT}; border-color: {theme.ACCENT}; }}"
        )
        self.comp_btn.clicked.connect(lambda: self.compare_clicked.emit(self.name))
        b_row.addWidget(self.comp_btn)

        self.c_lay.addWidget(self.b_row_widget)

    def mousePressEvent(self, event: QtGui.QMouseEvent):
        if event.button() == QtCore.Qt.LeftButton:
            self.clicked.emit(self.name)
        super().mousePressEvent(event)

    def set_selected(self, selected: bool):
        if self._is_selected == selected:
            return
        self._is_selected = selected
        self._update_card_style()

    def _update_card_style(self):
        if self._is_selected:
            bg = "#14232d"
            border = theme.ACCENT
        else:
            bg = theme.PANEL
            border = theme.BORDER
        left_border = f"border-left: 3px solid {theme.ACCENT};" if self.is_me else ""
        self.setStyleSheet(
            f"_PlayerCardWidget {{ background-color: {bg}; border: 1px solid {border}; "
            f"{left_border} border-radius: 5px; padding: 2px; }}"
        )

    def set_zero_collapsed(self, on: bool):
        """Collapse a zero-data ally row to one quiet line."""
        if on == self._zero_collapsed:
            return
        self._zero_collapsed = on
        self.bar.setVisible(not on)
        self.b_row_widget.setVisible(not on)
        if on:
            self.rate_lbl.setText("—")
            self.total_lbl.setText("no events")
        else:
            self.rate_lbl.setText("0/s")
            self.total_lbl.setText("0 (0%)")

    def update_stats(self, idx: int, p: PlayerParse, duration: float,
                     group_dmg: float, group_heals: float, tab: str,
                     class_rank: int = 1, class_total: int = 1,
                     group_taken: float = 0.0):
        self._hero_class = p.hero_class or ""
        if idx == 0:
            rank_col = "#facc15"
        elif idx == 1:
            rank_col = "#e2e8f0"
        elif idx == 2:
            rank_col = "#f97316"
        else:
            rank_col = theme.MUTED
        rank_txt = f"#{idx + 1}"
        if self.r_lbl.text() != rank_txt:
            self.r_lbl.setText(rank_txt)
        if getattr(self, "_last_rank_col", None) != rank_col:
            self.r_lbl.setStyleSheet(
                f"font-size: 11.5px; font-weight: 800; color: {rank_col}; font-family: monospace;")
            self._last_rank_col = rank_col

        cls_name = p.hero_class or "Hero"
        cls_tag = f"#{class_rank} {cls_name}"
        if self.cls_rank_lbl.text() != cls_tag:
            self.cls_rank_lbl.setText(cls_tag)

        dps_val = p.dps(duration)
        hps_val = p.hps(duration)
        dmg_share = p.share_pct(group_dmg)
        heal_share = p.heal_share_pct(group_heals)
        taken_val = getattr(p, "total_damage_taken", p.damage_taken + p.damage_taken_est)
        taken_share = (taken_val / max(1.0, group_taken)) * 100.0 if group_taken > 0 else 0.0

        if tab == "healing":
            total = p.heals
            rate = hps_val
            total_color = bar_color = theme.GOOD
            share_val = heal_share
        elif tab in ("taken", "damage_taken"):
            total = taken_val
            rate = total / max(1.0, duration)
            total_color = bar_color = theme.ORANGE
            share_val = taken_share
        elif tab == "skills":
            total = p.total_damage + p.heals
            rate = total / max(1.0, duration)
            total_color = bar_color = theme.ACCENT
            share_val = dmg_share
        else:  # damage
            total = p.total_damage
            rate = dps_val
            if p.hero_class:
                total_color = bar_color = theme.class_color(p.hero_class)
            else:
                total_color = theme.ACCENT_LIGHT if self.is_me else theme.GOLD
                bar_color = theme.ACCENT if self.is_me else theme.GOLD
            share_val = dmg_share

        rate_txt = f"{rate:,.0f}/s" if rate < 100_000 else f"{rate/1_000:.1f}k/s"
        total_num_txt = f"{total:,.0f}" if total < 10_000_000 else f"{total/1_000_000:.1f}M"
        total_txt = f"{total_num_txt} ({share_val:.1f}%)"

        if self.rate_lbl.text() != rate_txt:
            self.rate_lbl.setText(rate_txt)
        if getattr(self, "_last_total_color", None) != total_color:
            self.rate_lbl.setStyleSheet(
                f"font-size: 12.5px; font-weight: 800; color: {total_color}; font-family: ui-monospace, monospace;")
            self._last_total_color = total_color

        if self.total_lbl.text() != total_txt:
            self.total_lbl.setText(total_txt)

        bar_qss = (
            f"QProgressBar {{ background: {theme.SURFACE}; border: none; border-radius: 2px; }}"
            f"QProgressBar::chunk {{ background: {bar_color}; border-radius: 2px; }}"
        )
        if getattr(self, "_last_bar_qss", None) != bar_qss:
            self.bar.setStyleSheet(bar_qss)
            self._last_bar_qss = bar_qss
        self.bar.setValue(int(min(100.0, max(0.0, share_val))))

        total_crits = sum(s.crit_count for s in p.skills.values())
        total_hits = sum(s.hit_count for s in p.skills.values())
        crit_pct = (total_crits / total_hits * 100.0) if total_hits > 0 else 0.0
        crit_txt = f"⚡ {crit_pct:.1f}%"
        if self.crit_lbl.text() != crit_txt:
            self.crit_lbl.setText(crit_txt)

        top_skills = _ranked_skill_total(p)
        top_name = top_skills[0].name if top_skills else "None"
        if len(top_name) > 13:
            top_name = top_name[:12] + "…"
        top_txt = f"✦ {top_name}"
        if self.top_skill_lbl.text() != top_txt:
            self.top_skill_lbl.setText(top_txt)
