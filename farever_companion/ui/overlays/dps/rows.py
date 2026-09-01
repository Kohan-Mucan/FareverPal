"""Per-player rows for the Top DPS HUD (Dungeon-HUD style rows).

Split out of the old flat dps_overlay.py so each ui file stays under the
line budget. The row renders class icon tile + name/sub + mono value columns
with an expandable per-skill breakdown; `TOP_SKILLS` is the fallback per-row
cap when the overlay passes no global skill budget.
"""
from __future__ import annotations

from PySide6 import QtCore, QtWidgets

from ... import theme
from ...components import ElideLabel as _ElideLabel, IconTile
from ....core.dps_tracker import PlayerParse
from ...skill_row import SkillRow

TOP_SKILLS = 5  # fallback per-player skill cap for rows built without a budget

class _PlayerRowWidget(QtWidgets.QFrame):
    """Player row with expandable per-skill breakdown; persists user toggles."""
    # Emitted with the NEW value so the overlay can persist it per player.
    collapseChanged = QtCore.Signal(bool)
    showAllChanged = QtCore.Signal(bool)

    def __init__(self, name: str, is_me: bool, parent=None,
                 expanded: bool | None = None,
                 show_all_skills: bool = False):
        super().__init__(parent)
        self.name = name
        self.is_me = is_me
        # User chevron choice (persisted); auto-collapse is tracked separately.
        self.expanded = is_me if expanded is None else expanded
        self._show_all_skills = show_all_skills
        self._accent = theme.ACCENT
        self._rank_idx = 0
        self._is_top = False

        self.setStyleSheet(self._frame_qss())
        self.setCursor(QtCore.Qt.PointingHandCursor)

        self.main_lay = QtWidgets.QVBoxLayout(self)
        self.main_lay.setContentsMargins(6, 4, 6, 4)
        self.main_lay.setSpacing(2)

        # Header row (Dungeon-HUD PLAYERS anatomy): class icon tile + name/sub
        # column + mono value columns. No chevron / rank badge / share bars.
        h_lay = QtWidgets.QHBoxLayout()
        h_lay.setContentsMargins(4, 2, 4, 2)
        h_lay.setSpacing(8)

        self._tile = IconTile(22)
        h_lay.addWidget(self._tile)

        mid = QtWidgets.QVBoxLayout()
        mid.setContentsMargins(0, 0, 0, 0)
        mid.setSpacing(0)

        name_row = QtWidgets.QHBoxLayout()
        name_row.setContentsMargins(0, 0, 0, 0)
        name_row.setSpacing(4)
        self.name_lbl = _ElideLabel(name, 140)
        self.name_lbl.setStyleSheet(
            f"font-weight: 700; color: {'#00e5ff' if is_me else theme.TEXT}; font-size: 12px;")
        self.name_lbl.setSizePolicy(QtWidgets.QSizePolicy.Ignored,
                                    QtWidgets.QSizePolicy.Preferred)
        name_row.addWidget(self.name_lbl, 1)

        self.cls_lbl = QtWidgets.QLabel("")
        self.cls_lbl.setVisible(False)
        self.cls_lbl.setStyleSheet(
            f"font-size: 8px; font-weight: 800; color: {theme.ACCENT_LIGHT}; "
            f"background: {theme.ACCENT_DIM}; border-radius: 3px; padding: 1px 4px;")
        name_row.addWidget(self.cls_lbl, 0)
        mid.addLayout(name_row)

        self.sub_lbl = QtWidgets.QLabel("")
        self.sub_lbl.setObjectName("Mono")
        self.sub_lbl.setStyleSheet(f"font-size: 9px; color: {theme.DIM};")
        mid.addWidget(self.sub_lbl)
        h_lay.addLayout(mid, 1)

        self.total_lbl = QtWidgets.QLabel("0 DPS")
        self.total_lbl.setObjectName("PlayerTotal")
        self.total_lbl.setStyleSheet(
            f"font-size: 12px; font-weight: 800; color: #ffffff; "
            f"font-family: 'Consolas', monospace;")
        self.total_lbl.setMinimumWidth(50)
        self.total_lbl.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        h_lay.addWidget(self.total_lbl)

        self.rate_lbl = QtWidgets.QLabel("0.0%")
        self.rate_lbl.setObjectName("PlayerRate")
        self.rate_lbl.setFixedWidth(46)
        self.rate_lbl.setStyleSheet(
            f"font-size: 10px; font-weight: 700; color: {theme.DIM}; font-family: 'Consolas', monospace;")
        self.rate_lbl.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        h_lay.addWidget(self.rate_lbl)

        self.main_lay.addLayout(h_lay)

        self.skills_box = QtWidgets.QWidget()
        self.skills_lay = QtWidgets.QVBoxLayout(self.skills_box)
        self.skills_lay.setContentsMargins(26, 0, 2, 2)   # under the name column
        self.skills_lay.setSpacing(2)
        self.skills_box.show()
        self.main_lay.addWidget(self.skills_box)

        self._skill_row_widgets: dict[str, QtWidgets.QWidget] = {}
        self._auto_collapsed = False
        self._sync_visibility()

        # Inserted below the sorted rows only when the list exceeds its cap.
        self._more_btn = QtWidgets.QPushButton("")
        self._more_btn.setObjectName("MoreSkills")
        self._more_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self._more_btn.setFixedHeight(16)
        self._more_btn.setStyleSheet(
            f"QPushButton {{ border: 0; border-radius: 3px; background: transparent; "
            f"color: {theme.DIM}; font-size: 9px; font-weight: 700; "
            f"padding: 0 2px; text-align: left; }}"
            f"QPushButton:hover {{ color: {theme.ACCENT_LIGHT}; }}")
        self._more_btn.clicked.connect(self._toggle_more_skills)

    def _toggle_more_skills(self):
        """Flip full-list vs capped skill view (applied on the next tick)."""
        self._show_all_skills = not self._show_all_skills
        self.showAllChanged.emit(self._show_all_skills)

    def _frame_qss(self) -> str:
        # Dungeon-HUD row look: no per-row border cards, just a faint rank tint
        # so the icon tiles carry the visual weight.
        if self.is_me:
            bg = "rgba(0, 229, 255, 0.05)"
        elif self._rank_idx == 0:
            bg = "rgba(250, 204, 21, 0.04)"
        else:
            bg = "rgba(22, 24, 29, 0.35)"
        return f"_PlayerRowWidget {{ background-color: {bg}; border-radius: 4px; }}"

    def retint(self, accent: str) -> None:
        """Re-tint hook (the leader accent line is gone; kept for the API)."""
        self._accent = accent or theme.ACCENT

    def _sync_visibility(self):
        """Apply expand choice plus transient zero-ally auto-collapse."""
        show = self.expanded and not self._auto_collapsed
        self.skills_box.setVisible(show)

    def mousePressEvent(self, e):
        if e.button() == QtCore.Qt.LeftButton:
            self.expanded = not self.expanded
            self._sync_visibility()
            self.collapseChanged.emit(self.expanded)
        super().mousePressEvent(e)

    def update_stats(self, rank_idx: int, player: PlayerParse, duration: float,
                     group_damage: float, group_heals: float, mode: str = "damage",
                     skill_budget: int | None = None):
        self._rank_idx = rank_idx
        self._is_top = (rank_idx == 0)

        frame_qss = self._frame_qss()
        if getattr(self, "_last_frame_qss", None) != frame_qss:
            self.setStyleSheet(frame_qss)
            self._last_frame_qss = frame_qss

        zero_ally = (not player.is_me and player.total_damage <= 0 and player.heals <= 0)
        if zero_ally:
            self._auto_collapsed = True
            self._sync_visibility()
            self.total_lbl.setText("0 DPS")
            self.total_lbl.setStyleSheet(
                f"color: {theme.DIM}; font-size: 11px; font-family: monospace;")
            self.rate_lbl.setText("0.0%")
            self.sub_lbl.setText("")
            self.name_lbl.set_full(f"{rank_idx + 1} · {player.name}")
            self.name_lbl.setStyleSheet(
                f"font-weight: 600; color: {theme.DIM}; font-size: 11px;")
            self.cls_lbl.setVisible(False)
            return

        if self._auto_collapsed:
            self._auto_collapsed = False
            self._sync_visibility()

        # Class color drives the name, the icon tile and the damage total —
        # the class-colored PLAYERS-row look shared with the Dungeon HUD.
        if player.hero_class:
            col = theme.class_color(player.hero_class)
            self.cls_lbl.setText(player.hero_class.upper())
            self.cls_lbl.setVisible(True)
        else:
            col = "#00e5ff" if self.is_me else theme.GOLD
            self.cls_lbl.setVisible(False)
        self.name_lbl.set_full(f"{rank_idx + 1} · {player.name}")
        self.name_lbl.setStyleSheet(
            f"font-weight: 700; color: {'#00e5ff' if self.is_me else col}; font-size: 12px;")
        self.rate_lbl.setStyleSheet(
            f"font-size: 10px; font-weight: 700; color: {theme.DIM}; font-family: 'Consolas', monospace;")
        if getattr(self, "_last_tile_col", None) != col:
            self._last_tile_col = col
            self._tile.set_ui_icon("player", col)

        if mode == "healing":
            total = player.heals
            share = player.heal_share_pct(group_heals)
            rate = player.hps(duration)
            self.total_lbl.setText(f"{rate:,.0f} HPS")
            self.rate_lbl.setText(f"{share:.1f}%")
            total_color = theme.GOOD
        elif mode == "both":
            total = player.total_damage + player.heals
            share = player.share_pct(group_damage)
            rate = player.dps(duration)
            self.total_lbl.setText(f"{rate:,.0f} DPS")
            self.rate_lbl.setText(f"{share:.1f}%")
            total_color = theme.ACCENT_LIGHT
        else:  # damage
            total = player.total_damage
            share = player.share_pct(group_damage)
            rate = player.dps(duration)
            self.total_lbl.setText(f"{rate:,.0f} DPS")
            self.rate_lbl.setText(f"{share:.1f}%")
            total_color = col

        if getattr(self, "_last_total_color", None) != total_color:
            self.total_lbl.setStyleSheet(
                f"font-size: 12px; font-weight: 800; color: {total_color}; "
                f"font-family: 'Consolas', monospace;")
            self._last_total_color = total_color
        self.total_lbl.setToolTip(
            f"{player.name}: {total:,.0f} total · {rate:,.1f}/s · {share:.1f}% group share")

        # HUD second line: lifetime total + skill count.
        n_skills = len(player.skills)
        self.sub_lbl.setText(
            f"{total:,.0f} total · {n_skills} skills" if n_skills
            else f"{total:,.0f} total")

        # Per-skill rows use the active mode's channel.
        if self.expanded and player.skills:
            is_heal_mode = (mode == "healing")
            if is_heal_mode:
                skills = player.ranked_heal_skills()
                total_base = player.heals
            elif mode == "both":
                skills = sorted(player.skills.values(),
                                key=lambda s: s.total, reverse=True)
                total_base = max(player.total_damage, player.heals, 1.0)
            else:
                skills = player.ranked_skills()
                total_base = player.total_damage
            # Global skill budget share by rank; expanded lists are exempt.
            cap = skill_budget if skill_budget is not None else TOP_SKILLS
            visible = (skills if self._show_all_skills else skills[:cap])
            active_skills = set()
            for sp in visible:
                active_skills.add(sp.skill_id)
                if is_heal_mode:
                    s_share = sp.heal_share_pct(total_base)
                elif mode == "both":
                    s_share = (min(100.0, (sp.total / total_base) * 100.0)
                                if total_base > 0 else 0.0)
                else:
                    s_share = sp.share_pct(total_base)

                if sp.skill_id not in self._skill_row_widgets:
                    row = SkillRow(density="compact", parent=self.skills_box)
                    row.set_skill(sp.skill_id, sp.name)
                    self._skill_row_widgets[sp.skill_id] = row
                    self.skills_lay.addWidget(row)
                else:
                    row = self._skill_row_widgets[sp.skill_id]
                    row.show()

                row.update(sp, share_pct=s_share, mode=mode)
                row.set_bar_color(theme.skill_color(sp.skill_id))

            # Re-sort via re-add ONLY if the current order in the layout doesn't match visible
            curr_widgets = [
                self.skills_lay.itemAt(i).widget()
                for i in range(self.skills_lay.count())
                if self.skills_lay.itemAt(i) and self.skills_lay.itemAt(i).widget() != self._more_btn
            ]
            target_widgets = [
                self._skill_row_widgets[sp.skill_id]
                for sp in visible
                if sp.skill_id in self._skill_row_widgets
            ]
            if curr_widgets != target_widgets:
                for sw in target_widgets:
                    self.skills_lay.addWidget(sw)
                if self._more_btn.isVisible():
                    self.skills_lay.addWidget(self._more_btn)

            for sid, sw in self._skill_row_widgets.items():
                if sid not in active_skills:
                    sw.hide()

            # Toggle only when the list exceeds the cap; pinned below the rows.
            if len(skills) > cap:
                new_text = "▴ show fewer" if self._show_all_skills else f"▾ {len(skills) - cap} more skills"
                if self._more_btn.text() != new_text:
                    self._more_btn.setText(new_text)
                if self.skills_lay.indexOf(self._more_btn) < 0:
                    self.skills_lay.addWidget(self._more_btn)
                self._more_btn.setVisible(True)
            else:
                idx = self.skills_lay.indexOf(self._more_btn)
                if idx >= 0:
                    self.skills_lay.takeAt(idx)
                self._more_btn.setVisible(False)

