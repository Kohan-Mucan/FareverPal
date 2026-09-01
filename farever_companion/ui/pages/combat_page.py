"""Combat & DPS Analysis Page.

Comprehensive DPS Breakdown, Combat Timeline & Burst Plotter,
and live encounter analysis.
"""
from __future__ import annotations

import time
from pathlib import Path
from PySide6 import QtCore, QtGui, QtWidgets

from .. import theme
from .. import components as C
from ..skill_row import SkillRow
from ...data import icons, items as idata, skills as skdata, units as udata
from ...core.dps_tracker import (DpsTracker, CombatSession, weapons_used,
                                  solo_status, _in_instance)
from .items.skills_section import skill_color
from ..dps_source_text import source_status, empty_hint
from ...config import dps_dir

# Modular combat components
from ..combat import (
    _CombatTimelinePlotter,
    COMPARE_COLORS,
    _ranked_skill_total,
    _cp_card_alive,
    _StatCardWidget,
    _StatStrip,
    _StackedSkillBar,
    _PlayerCardWidget,
    _char_label_from_history_path,
    scan_history_dir,
    _clean_session_name,
    _KIND_STYLE,
    delete_past_fight,
    delete_past_fights_batch,
    open_past_fights_dialog,
    refresh_rail_compare,
)


# Below this width the fight-detail rail auto-hides so the list keeps room.
CP_RAIL_AUTO_MIN_WIDTH = 1040

# Past-fights kind filter labels ("all" shows every archived fight).
_FIGHTS_FILTER_LABELS = {
    "all": "All",
    "boss": "Boss",
    "trash": "Trash",
    "dummy": "Test Dummy",
}

# Weapon-family chip glyphs for the skills rail (family name -> emoji).
_WEAPON_FAMILY_GLYPHS = {
    "Sword": "⚔", "Great Sword": "⚔", "Dual Swords": "⚔",
    "Shield": "🛡", "Axe": "🪓", "Great Axe": "🪓", "Dual Axes": "🪓",
    "Mace": "🔨", "Great Mace": "🔨", "Dual Maces": "🔨",
    "Dagger": "🗡", "Daggers": "🗡", "Fists": "👊",
    "Spear": "🔱", "Staff": "✨", "Scepter": "✨",
    "Bow": "🏹", "Thrown": "🎯", "Crescent": "🌙",
    "Halos": "💫", "Book": "📖",
}

class _SkillWeaponPopup(QtWidgets.QFrame):
    """Full skill detail for a skills-rail row, opened by clicking the row:
    name + type chip, the real stat line, the resolved description, every
    per-rank upgrade line, and the weapon(s) that grant the skill (the
    catalog reverse lookup — skill AND its source weapon in one place).
    Frameless Qt.Popup — clicking anywhere else closes it. Pure-cast
    skills (no weapon source) simply omit the weapon section."""

    def __init__(self, sp, parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        self.setWindowFlags(QtCore.Qt.Popup)
        self.setAttribute(QtCore.Qt.WA_StyledBackground, True)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        sid = getattr(sp, "skill_id", "") or ""
        self.skill_id = sid
        typ = skdata.skill_type(sid)
        col = skill_color(typ)
        self.setStyleSheet(f"QFrame{{border-left:3px solid {col};}}")
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(5)
        lay.setSizeConstraint(QtWidgets.QLayout.SetFixedSize)
        self.setMinimumWidth(300)
        self.setMaximumWidth(360)
        # header: name + type chip
        hd = QtWidgets.QHBoxLayout()
        hd.setSpacing(6)
        nm = QtWidgets.QLabel(sp.name or sid or "Skill")
        nm.setWordWrap(True)
        nm.setStyleSheet(
            f"color:{col};font-weight:700;font-size:14px;background:transparent;")
        hd.addWidget(nm, 1)
        if typ:
            chip = QtWidgets.QLabel(typ.upper())
            chip.setStyleSheet(
                f"color:{col};background:{theme.with_alpha(col, 16)};"
                f"border:1px solid {theme.with_alpha(col, 70)};"
                "border-radius:4px;padding:2px 6px;font-weight:700;"
                "font-size:10px;letter-spacing:1px;")
            hd.addWidget(chip, 0, QtCore.Qt.AlignTop)
        lay.addLayout(hd)
        # the real stat line (cooldown · range from the sheet)
        meta = skdata.skill_meta(sid)
        if meta:
            parts = []
            if "cooldown" in meta:
                parts.append(f"cooldown {meta['cooldown']:g}s")
            if "range" in meta:
                parts.append(f"range {meta['range']:g}m")
            if parts:
                ml = QtWidgets.QLabel(" · ".join(parts))
                ml.setObjectName("Mono")
                ml.setStyleSheet(
                    f'color:{theme.DIM};font-family:"{theme.MONO_FONT}", "Consolas", monospace;'
                    "font-size:11px;background:transparent;")
                lay.addWidget(ml)
        # the resolved description
        desc = skdata.skill_description(sid)
        if desc:
            de = QtWidgets.QLabel(desc)
            de.setObjectName("Mono")
            de.setStyleSheet(
                f'color:{theme.MUTED};font-family:"{theme.MONO_FONT}", "Consolas", monospace;'
                "font-size:12px;background:transparent;")
            de.setWordWrap(True)
            de.setTextFormat(QtCore.Qt.PlainText)
            lay.addWidget(de)
        # per-rank upgrade lines — real sheet data
        ranks = skdata.skill_rank_descriptions(sid)
        if ranks:
            lay.addSpacing(2)
            lay.addWidget(C.SectionHeader("Upgrades"))
            for i, line in enumerate(ranks, 1):
                row = QtWidgets.QHBoxLayout()
                row.setSpacing(8)
                badge = QtWidgets.QLabel(f"RANK {i}")
                badge.setStyleSheet(
                    f"color:{col};background:{theme.with_alpha(col, 16)};"
                    f"border:1px solid {theme.with_alpha(col, 70)};"
                    "border-radius:4px;padding:1px 5px;font-weight:700;"
                    "font-size:10px;letter-spacing:1px;")
                badge.setFixedWidth(56)
                row.addWidget(badge, 0, QtCore.Qt.AlignTop)
                tx = QtWidgets.QLabel(line)
                tx.setObjectName("Mono")
                tx.setStyleSheet(
                    f'color:{theme.MUTED};font-family:"{theme.MONO_FONT}", "Consolas", monospace;'
                    "font-size:12px;background:transparent;")
                tx.setWordWrap(True)
                tx.setTextFormat(QtCore.Qt.PlainText)
                row.addWidget(tx, 1)
                lay.addLayout(row)
        # the weapon(s) this skill comes from
        weapons = idata.weapons_for_skill(sid)
        if weapons:
            lay.addSpacing(2)
            lay.addWidget(C.SectionHeader("From Weapon"))
            for w in weapons:
                wrow = QtWidgets.QHBoxLayout()
                wrow.setSpacing(6)
                wl = QtWidgets.QLabel(
                    f"{_WEAPON_FAMILY_GLYPHS.get(w['type'], '')} {w['name']}")
                wl.setStyleSheet(
                    f"color:{theme.GOLD};font-weight:700;font-size:12px;"
                    "background:transparent;")
                wl.setWordWrap(True)
                wl.setTextFormat(QtCore.Qt.PlainText)
                wrow.addWidget(wl, 1)
                lay.addLayout(wrow)
        # footer: the skill's id (search matches it too — a useful handle)
        idl = QtWidgets.QLabel(sid)
        idl.setObjectName("Mono")
        idl.setStyleSheet(
            f'color:{theme.DIM};font-family:"{theme.MONO_FONT}", "Consolas", monospace;'
            "font-size:10px;background:transparent;")
        lay.addWidget(idl, 0, QtCore.Qt.AlignRight)

    def mousePressEvent(self, ev):
        if ev.button() == QtCore.Qt.LeftButton:
            self.close()
            ev.accept()
        else:
            super().mousePressEvent(ev)

    def hideEvent(self, ev):
        parent = self.parent()
        if hasattr(parent, "_on_skill_popup_closed") and self.skill_id:
            parent._on_skill_popup_closed(self.skill_id)
        super().hideEvent(ev)


class _RailResizeWatcher(QtCore.QObject):
    """Calls back on page resize so the rail can auto-hide on narrow windows."""

    def __init__(self, target: QtWidgets.QWidget, callback):
        super().__init__(target)
        self._cb = callback
        target.installEventFilter(self)

    def eventFilter(self, obj, ev):
        if ev.type() == QtCore.QEvent.Resize:
            try:
                self._cb()
            except Exception:
                pass
        return False


class _HeaderCellLabel(QtWidgets.QLabel):
    clicked = QtCore.Signal(str)

    def __init__(self, key: str, text: str, width=None, flex=0, align=QtCore.Qt.AlignLeft, parent=None):
        super().__init__(text.upper(), parent)
        self.key = key
        self.base_text = text.upper()
        self.setWordWrap(False)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setAlignment(align | QtCore.Qt.AlignVCenter)
        if width:
            self.setFixedWidth(width)

    def mousePressEvent(self, ev):
        if ev.button() == QtCore.Qt.LeftButton:
            self.clicked.emit(self.key)
            ev.accept()
        else:
            super().mousePressEvent(ev)


class _TableHeaderRow(QtWidgets.QFrame):
    """Header row sitting at index 0 inside the roster board layout with interactive column sorting."""

    sort_requested = QtCore.Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("TableHeaderRow")
        self.setFixedHeight(28)
        self.setStyleSheet(
            f"QFrame#TableHeaderRow {{ background: {theme.PANEL_LOW}; "
            f"border-bottom: 1px solid {theme.BORDER}; border-radius: 4px; }}"
        )

        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(8, 4, 8, 4)
        lay.setSpacing(6)

        def _cell(key: str, text: str, width=None, flex=0, align=QtCore.Qt.AlignLeft) -> _HeaderCellLabel:
            lbl = _HeaderCellLabel(key, text, width=width, flex=flex, align=align)
            lbl.setStyleSheet(
                f"QLabel {{ font-size: 11.5px; font-weight: 800; color: {theme.DIM}; letter-spacing: 0.5px; }}"
                f"QLabel:hover {{ color: {theme.TEXT}; }}"
            )
            lbl.clicked.connect(self._on_cell_clicked)
            if flex:
                lay.addWidget(lbl, flex)
            else:
                lay.addWidget(lbl)
            return lbl

        self.rank_lbl = _cell("rank", "Rank", width=30)
        self.name_lbl = _cell("name", "Players", flex=1, align=QtCore.Qt.AlignCenter)
        self.class_lbl = _cell("class", "Class", width=52, align=QtCore.Qt.AlignCenter)
        self.rate_lbl = _cell("dps", "DPS", width=52, align=QtCore.Qt.AlignCenter)
        self.total_lbl = _cell("total", "Total", width=62, align=QtCore.Qt.AlignCenter)
        self.share_lbl = _cell("share", "Share", width=48, align=QtCore.Qt.AlignCenter)
        self.crit_lbl = _cell("crit", "Crit %", width=42, align=QtCore.Qt.AlignCenter)

        self._active_metric = "damage"
        self._active_sort = "dps"

    def _on_cell_clicked(self, key: str):
        self.sort_requested.emit(key)

    def set_metric(self, metric: str):
        self._active_metric = metric
        if metric == "healing":
            self.rate_lbl.base_text = "HPS"
            self.total_lbl.base_text = "HEALS"
        elif metric in ("taken", "damage_taken"):
            self.rate_lbl.base_text = "DTPS"
            self.total_lbl.base_text = "TAKEN"
        else:
            self.rate_lbl.base_text = "DPS"
            self.total_lbl.base_text = "TOTAL"
        self.set_active_sort(self._active_sort)

    def set_active_sort(self, sort_key: str):
        self._active_sort = sort_key
        labels = [
            ("rank", self.rank_lbl),
            ("name", self.name_lbl),
            ("class", self.class_lbl),
            ("dps", self.rate_lbl),
            ("total", self.total_lbl),
            ("share", self.share_lbl),
            ("crit", self.crit_lbl),
        ]
        for key, lbl in labels:
            is_active = (key == sort_key)
            suffix = " ▼" if is_active else ""
            lbl.setText(f"{lbl.base_text}{suffix}")
            active_col = theme.GOLD if key == "class" else theme.ACCENT_LIGHT
            col = active_col if is_active else theme.DIM
            weight = "900" if is_active else "800"
            lbl.setStyleSheet(
                f"QLabel {{ font-size: 11.5px; font-weight: {weight}; color: {col}; letter-spacing: 0.5px; }}"
                f"QLabel:hover {{ color: {theme.TEXT}; }}"
            )


class _MeterTableRow(QtWidgets.QFrame):
    clicked = QtCore.Signal(str)
    compare_clicked = QtCore.Signal(str)

    def __init__(self, name: str, is_me: bool, parent=None):
        super().__init__(parent)
        self.name = name
        self.is_me = is_me
        self._is_selected = False

        self.setObjectName("MeterTableRow")
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setFixedHeight(38)

        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(8, 4, 8, 4)
        lay.setSpacing(6)

        # Rank
        self.rank_lbl = QtWidgets.QLabel("#1")
        self.rank_lbl.setFixedWidth(30)
        self.rank_lbl.setStyleSheet(f"font-size: 11.5px; font-weight: 800; color: {theme.GOLD}; font-family: monospace;")
        lay.addWidget(self.rank_lbl)

        # Name (Centered with Stretch on both sides)
        name_box = QtWidgets.QHBoxLayout()
        name_box.setSpacing(4)
        name_box.setContentsMargins(0, 0, 0, 0)
        name_box.addStretch(1)

        clean_nm = name.replace(" (You)", "").replace(" (YOU)", "")
        self.name_lbl = QtWidgets.QLabel(clean_nm)
        self.name_lbl.setStyleSheet(f"font-size: 12px; font-weight: 700; color: {theme.ACCENT_LIGHT if is_me else theme.TEXT};")
        name_box.addWidget(self.name_lbl)

        if is_me:
            you_tag = QtWidgets.QLabel("YOU")
            you_tag.setStyleSheet(
                f"font-size: 8px; font-weight: 800; color: {theme.ACCENT}; "
                f"background: {theme.ACCENT_DIM}; border-radius: 2px; padding: 1px 3px;"
            )
            name_box.addWidget(you_tag)

        name_box.addStretch(1)

        name_widget = QtWidgets.QWidget()
        name_widget.setLayout(name_box)
        lay.addWidget(name_widget, 1)

        # Class (52px - Centered)
        self.class_lbl = QtWidgets.QLabel("")
        self.class_lbl.setFixedWidth(52)
        self.class_lbl.setAlignment(QtCore.Qt.AlignCenter)
        self.class_lbl.setStyleSheet("font-size: 11px; font-weight: 700;")
        lay.addWidget(self.class_lbl)

        # DPS / HPS Rate (52px - Centered)
        self.rate_lbl = QtWidgets.QLabel("0/s")
        self.rate_lbl.setFixedWidth(52)
        self.rate_lbl.setAlignment(QtCore.Qt.AlignCenter)
        self.rate_lbl.setStyleSheet(f"font-size: 12.5px; font-weight: 800; color: {theme.ACCENT_LIGHT}; font-family: monospace;")
        lay.addWidget(self.rate_lbl)

        # Total (62px - Centered)
        self.total_lbl = QtWidgets.QLabel("0")
        self.total_lbl.setFixedWidth(62)
        self.total_lbl.setAlignment(QtCore.Qt.AlignCenter)
        self.total_lbl.setStyleSheet(f"font-size: 11px; color: {theme.TEXT}; font-family: monospace;")
        lay.addWidget(self.total_lbl)

        # Share + Bar (48px - Centered)
        share_box = QtWidgets.QVBoxLayout()
        share_box.setSpacing(2)
        share_box.setContentsMargins(0, 0, 0, 0)
        self.share_lbl = QtWidgets.QLabel("0.0%")
        self.share_lbl.setAlignment(QtCore.Qt.AlignCenter)
        self.share_lbl.setStyleSheet(f"font-size: 9.5px; font-weight: 700; color: {theme.TEXT};")
        self.bar = QtWidgets.QProgressBar()
        self.bar.setFixedHeight(4)
        self.bar.setTextVisible(False)
        self.bar.setRange(0, 100)
        self.bar.setStyleSheet(
            f"QProgressBar {{ background: {theme.SURFACE}; border: none; border-radius: 2px; }}"
            f"QProgressBar::chunk {{ background: {theme.ACCENT}; border-radius: 2px; }}"
        )
        share_box.addWidget(self.share_lbl)
        share_box.addWidget(self.bar)

        share_widget = QtWidgets.QWidget()
        share_widget.setLayout(share_box)
        share_widget.setFixedWidth(48)
        lay.addWidget(share_widget)

        # Crit % (42px - Centered)
        self.crit_lbl = QtWidgets.QLabel("--")
        self.crit_lbl.setFixedWidth(42)
        self.crit_lbl.setAlignment(QtCore.Qt.AlignCenter)
        self.crit_lbl.setStyleSheet(f"font-size: 10.5px; color: {theme.ORANGE}; font-family: monospace;")
        lay.addWidget(self.crit_lbl)
        self._last_bar_col = None
        self._last_cls_color = None

        self._update_style()

    def mousePressEvent(self, ev):
        if ev.button() == QtCore.Qt.LeftButton:
            self.clicked.emit(self.name)
            ev.accept()
        else:
            super().mousePressEvent(ev)

    def set_selected(self, sel: bool):
        if self._is_selected == sel:
            return
        self._is_selected = sel
        self._update_style()

    def _update_style(self):
        bg = "#0f2e3f" if self._is_selected else theme.PANEL
        border = theme.ACCENT if self._is_selected else theme.BORDER
        left_border = f"border-left: 3px solid {theme.ACCENT};" if self.is_me else ""
        self.setStyleSheet(
            f"QFrame#MeterTableRow {{ background-color: {bg}; border: 1px solid {border}; "
            f"{left_border} border-radius: 5px; }}"
            f"QFrame#MeterTableRow:hover {{ background-color: #334155; }}"
        )

    def update_stats(self, idx: int, p, duration: float, group_dmg: float, group_heals: float, tab: str, class_rank: int = 1, class_total: int = 1, group_taken: float = 0.0):
        cls_name = p.hero_class or "Hero"
        cls_color = theme.class_color(cls_name)
        self.class_lbl.setText(cls_name)
        if self._last_cls_color != cls_color:
            self._last_cls_color = cls_color
            self.class_lbl.setStyleSheet(f"font-size: 11.5px; font-weight: 700; color: {cls_color};")

        rank_txt = f"#{idx + 1}"
        self.rank_lbl.setText(rank_txt)

        dps_val = p.dps(duration)
        hps_val = p.hps(duration)
        dmg_share = p.share_pct(group_dmg)
        heal_share = p.heal_share_pct(group_heals)
        taken_val = getattr(p, "total_damage_taken", p.damage_taken + p.damage_taken_est)
        taken_share = (taken_val / max(1.0, group_taken)) * 100.0 if group_taken > 0 else 0.0

        if tab == "healing":
            total = p.heals
            rate = hps_val
            share_val = heal_share
            bar_col = theme.GOOD
        elif tab in ("taken", "damage_taken"):
            total = taken_val
            rate = total / max(1.0, duration)
            share_val = taken_share
            bar_col = theme.ORANGE
        else:
            total = p.total_damage
            rate = dps_val
            share_val = dmg_share
            bar_col = theme.ACCENT

        rate_str = f"{rate:,.0f}/s" if rate < 100_000 else f"{rate/1000:.1f}k/s"
        total_str = f"{total:,.0f}" if total < 10_000_000 else f"{total/1_000_000:.2f}M"

        self.rate_lbl.setText(rate_str)
        self.total_lbl.setText(total_str)
        self.share_lbl.setText(f"{share_val:.1f}%")
        self.bar.setValue(int(min(100.0, share_val)))
        if self._last_bar_col != bar_col:
            self._last_bar_col = bar_col
            self.bar.setStyleSheet(
                f"QProgressBar {{ background: {theme.SURFACE}; border: none; border-radius: 2px; }}"
                f"QProgressBar::chunk {{ background: {bar_col}; border-radius: 2px; }}"
            )

        total_crits = sum(getattr(s, "crit_count", 0) for s in p.skills.values())
        total_hits = sum(getattr(s, "hit_count", 0) for s in p.skills.values()) or getattr(p, "hit_count", 0)
        crit_pct = (total_crits / total_hits * 100.0) if total_hits > 0 else 0.0
        self.crit_lbl.setText(f"{crit_pct:.0f}%" if total_hits > 0 else "--")


class _RichComboDelegate(QtWidgets.QStyledItemDelegate):
    """Item delegate for QComboBox rendering HTML formatted rich text entries with multi-part colors."""

    def paint(self, painter, option, index):
        options = QtWidgets.QStyleOptionViewItem(option)
        self.initStyleOption(options, index)

        html_text = index.data(QtCore.Qt.UserRole + 1) or index.data(QtCore.Qt.DisplayRole) or ""

        painter.save()
        if options.state & QtWidgets.QStyle.State_Selected:
            painter.fillRect(options.rect, QtGui.QColor(theme.ACCENT_DIM))
        else:
            painter.fillRect(options.rect, QtGui.QColor(theme.PANEL))

        doc = QtGui.QTextDocument()
        doc.setDefaultFont(options.font)
        doc.setHtml(f"<div style='color: {theme.TEXT}; font-family: sans-serif; font-size: 11px;'>{html_text}</div>")

        painter.translate(options.rect.left() + 6, options.rect.top() + 3)
        clip = QtCore.QRectF(0, 0, options.rect.width() - 12, options.rect.height() - 6)
        doc.drawContents(painter, clip)
        painter.restore()

    def sizeHint(self, option, index):
        options = QtWidgets.QStyleOptionViewItem(option)
        self.initStyleOption(options, index)
        doc = QtGui.QTextDocument()
        doc.setDefaultFont(options.font)
        html_text = index.data(QtCore.Qt.UserRole + 1) or index.data(QtCore.Qt.DisplayRole) or ""
        doc.setHtml(f"<div style='font-size: 11px;'>{html_text}</div>")
        return QtCore.QSize(int(doc.idealWidth()) + 24, 28)


def _format_history_label_html(sess: CombatSession, char: str = "", index_num: int = 1) -> str:
    raw_boss = (sess.target_name or "").replace("👑 ", "").strip()
    boss_name = raw_boss if (raw_boss and raw_boss != "None") else _clean_session_name(sess.name)
    char_str = f"<span style='color: {theme.MUTED};'>[{char}]</span>" if char else ""
    if sess.start_time:
        time_str = f"<span style='color: {theme.DIM};'>{time.strftime('%b %d, %I:%M %p', time.localtime(sess.start_time))}</span>"
    else:
        time_str = ""
    mins = int(sess.duration // 60)
    secs = sess.duration % 60
    dur_str = f"<span style='color: {theme.TEXT}; font-family: monospace;'>⏱ {mins:02d}:{secs:02.0f}</span>"
    dmg = sess.group_damage
    dmg_val = f"{dmg:,.0f}" if dmg < 10_000_000 else f"{dmg/1_000_000:.2f}M"
    dmg_str = f"<span style='color: {theme.GOOD}; font-weight: 800; font-family: monospace;'>⚡ {dmg_val} DMG</span>"

    idx_html = f"<span style='color: {theme.GOLD}; font-weight: 800;'>#{index_num}</span>"
    boss_html = f"<span style='color: {theme.ACCENT_LIGHT}; font-weight: 800;'>⚔ {boss_name}</span>"

    parts = [idx_html, boss_html, char_str, time_str, dur_str, dmg_str]
    sep = f" <span style='color: {theme.BORDER};'>·</span> "
    return sep.join(p for p in parts if p)


def _format_history_label(sess: CombatSession, char: str = "") -> str:
    raw_boss = (sess.target_name or "").replace("👑 ", "").strip()
    boss_name = raw_boss if (raw_boss and raw_boss != "None") else _clean_session_name(sess.name)
    char_str = f"[{char}]" if char else ""
    if sess.start_time:
        time_str = time.strftime("%b %d, %I:%M %p", time.localtime(sess.start_time))
    else:
        time_str = ""
    mins = int(sess.duration // 60)
    secs = sess.duration % 60
    dur_str = f"{mins:02d}:{secs:02.0f}"
    dmg = sess.group_damage
    dmg_str = f"{dmg:,.0f} DMG" if dmg < 10_000_000 else f"{dmg/1_000_000:.2f}M DMG"
    parts = [f"⚔ {boss_name}", char_str, time_str, dur_str, dmg_str]
    return "  ·  ".join(p for p in parts if p)


class CombatPageMixin:
    """ControlPanel mixin providing the Combat & DPS page."""

    def _page_combat(self) -> QtWidgets.QWidget:
        return self._build_combat_page()

    def _build_combat_page(self) -> QtWidgets.QWidget:
        page, lay = self._page_container()
        self._cp_player_cards: dict[str, _PlayerCardWidget] = {}
        self._selected_history_idx: int = 0  # 0 = Live Encounter
        self._cp_selected_player: str = ""
        self._cp_compare_a: str = ""
        self._cp_compare_b: str = ""
        self._cp_rail_tab: str = "skills"
        self._cp_rail_skill_widgets: dict[str, SkillRow] = {}
        self._cp_cmp_row_widgets: list[tuple] = []
        # Lazy caches from a previous build (compare header labels, fingerprint)
        # survive page eviction as stale pointers into a deleted widget tree;
        # drop them so the rebuilt page recreates fresh widgets. Touching the
        # old refs would raise "Internal C++ object already deleted".
        for _attr in ("_cp_cmp_hdr_a", "_cp_cmp_hdr_b", "_last_cmp_fp"):
            try:
                delattr(self, _attr)
            except AttributeError:
                pass
        # Past-fights list kind filter: "all", "boss", "trash", "dummy".
        self._cp_fights_filter: str = "all"
        # Disk-browse mode: the Encounter combo lists these past fights instead.
        self._browse_sessions: list[CombatSession] | None = None
        self._browse_char: str = ""
        # Auto-adopt old fight disabled: default to the live encounter.
        self._auto_last_fight: bool = False
        self._auto_last_suppressed: bool = True
        self._cp_segment: str = "auto"  # "auto", "boss", "boss_adds", "trash", "overall", "past_fights"

        hdr_box = QtWidgets.QHBoxLayout()
        hdr_box.setSpacing(10)

        title_lbl = QtWidgets.QLabel("Combat & DPS Analysis")
        title_lbl.setStyleSheet(f"font-size: 15px; font-weight: 800; color: {theme.TEXT};")
        hdr_box.addWidget(title_lbl)

        hdr_box.addStretch(1)

        # Fight selector dropdown
        self.cp_history_combo = QtWidgets.QComboBox()
        self.cp_history_combo.setItemDelegate(_RichComboDelegate(self.cp_history_combo))
        self.cp_history_combo.addItem("● Live Encounter (Active)")
        self.cp_history_combo.setToolTip("Encounter shown: the live session or any archived fight")
        self.cp_history_combo.setStyleSheet(
            f"QComboBox {{ min-width: 280px; max-width: 500px; padding: 5px 10px; "
            f"font-weight: 700; font-size: 11.5px; border: 1px solid {theme.BORDER}; border-radius: 4px; "
            f"background: {theme.PANEL}; color: {theme.TEXT}; }}"
            f"QComboBox::drop-down {{ border: none; width: 20px; }}"
            f"QComboBox QAbstractItemView {{ background: {theme.PANEL}; border: 1px solid {theme.BORDER}; "
            f"selection-background-color: {theme.ACCENT_DIM}; selection-color: {theme.TEXT}; padding: 4px; }}"
        )
        self.cp_history_combo.setSizeAdjustPolicy(
            QtWidgets.QComboBox.AdjustToContents)
        self.cp_history_combo.currentIndexChanged.connect(self._on_history_selection_changed)
        hdr_box.addWidget(self.cp_history_combo)

        rst_btn = QtWidgets.QPushButton("Reset")
        rst_btn.setIcon(icons.ui_qicon("refresh-cw", theme.MUTED, 14))
        rst_btn.setStyleSheet("padding: 6px 10px; font-weight: 600;")
        rst_btn.clicked.connect(self._reset_combat_session)
        hdr_box.addWidget(rst_btn)

        ov_btn = QtWidgets.QPushButton()
        ov_btn.setIcon(icons.ui_qicon("layers", theme.ACCENT, 14))
        ov_btn.setToolTip("Show / hide the Top DPS overlay")
        ov_btn.setStyleSheet(f"padding: 6px 8px; color: {theme.ACCENT};")
        ov_btn.clicked.connect(lambda: self._request_overlay("dps", True))
        hdr_box.addWidget(ov_btn)

        dps_settings_btn = QtWidgets.QPushButton("⚙ DPS Settings")
        dps_settings_btn.setStyleSheet("padding: 6px 10px; font-weight: 600;")
        dps_settings_btn.clicked.connect(lambda: self._select_nav("settings:dps") if hasattr(self, "_select_nav") else None)
        hdr_box.addWidget(dps_settings_btn)

        lay.addLayout(hdr_box)

        # Shown while displaying an auto-adopted archived fight.
        self.cp_hist_banner = QtWidgets.QLabel("")
        self.cp_hist_banner.setVisible(False)
        self.cp_hist_banner.setWordWrap(True)
        self.cp_hist_banner.setStyleSheet(
            f"font-size: 10px; font-weight: 700; color: {theme.GOLD}; "
            f"background: {theme.PANEL_LOW}; border: 1px solid {theme.BORDER}; "
            f"border-radius: 4px; padding: 4px 8px;")
        lay.addWidget(self.cp_hist_banner)

        self.cp_stat_strip = _StatStrip()
        lay.addWidget(self.cp_stat_strip)
        # Compatibility aliases for existing refresh code.
        self.cp_card_dur = self.cp_card_dmg = self.cp_card_heal = \
            self.cp_card_taken = self.cp_card_target = self.cp_stat_strip

        # Combat Timeline & Burst Plotter
        plotter_box = QtWidgets.QVBoxLayout()
        plotter_box.setSpacing(4)

        plotter_hdr = QtWidgets.QHBoxLayout()
        plotter_hdr.addStretch(1)

        self.cp_plotter_info = QtWidgets.QLabel("")
        self.cp_plotter_info.setStyleSheet(f"font-size: 10px; color: {theme.MUTED}; font-family: monospace;")
        plotter_hdr.addWidget(self.cp_plotter_info)
        self.cp_plotter_info.hide()  # caption removed — the graph speaks for itself
        plotter_box.addLayout(plotter_hdr)

        self.cp_plotter = _CombatTimelinePlotter()
        plotter_box.addWidget(self.cp_plotter)
        lay.addLayout(plotter_box)

        # Left column (chips + board) vs rail: the split starts directly under
        # the full-width graph so the right side moves up a row.
        left_col = QtWidgets.QWidget()
        left_lay = QtWidgets.QVBoxLayout(left_col)
        left_lay.setContentsMargins(0, 0, 0, 0)
        left_lay.setSpacing(8)

        # View switcher tabs + segment filters
        filter_wrap = QtWidgets.QVBoxLayout()
        filter_wrap.setSpacing(6)

        self.cp_tab_dmg = QtWidgets.QPushButton("Damage Breakdown")
        self.cp_tab_heal = QtWidgets.QPushButton("Healing Breakdown")
        self.cp_tab_skills = QtWidgets.QPushButton("Skill Performance")
        self._cp_metric = "damage"
        self._cp_class_filter: set[str] = set()

        # Single unified control row
        control_row = QtWidgets.QHBoxLayout()
        control_row.setSpacing(6)

        def _chip(text: str, tip: str, active_color: str = theme.ACCENT) -> QtWidgets.QPushButton:
            b = QtWidgets.QPushButton(text)
            b.setCheckable(True)
            b.setToolTip(tip)
            b.setStyleSheet(
                f"QPushButton {{ padding: 4px 12px; font-size: 10.5px; "
                f"font-weight: 700; border-radius: 4px; border: 1px solid {theme.BORDER}; color: {theme.TEXT}; }}"
                f"QPushButton:checked {{ background-color: {theme.with_alpha(active_color, 40)}; "
                f"color: {active_color}; border: 1px solid {active_color}; }}"
            )
            return b

        self.cp_metric_chips: dict[str, QtWidgets.QPushButton] = {}
        chip_configs = [
            ("damage", "⚔ Damage", theme.ACCENT_LIGHT),
            ("healing", "✚ Healing", theme.GOOD),
            ("taken", "🛡 Taken", theme.ORANGE),
            ("compare", "⚖ Compare", theme.GOLD),
            ("fights", "📜 Fights", theme.ACCENT),
        ]
        for m, lbl, col in chip_configs:
            b = _chip(lbl, f"View {m}", active_color=col)
            b.clicked.connect(lambda _, mm=m: self._set_combat_metric(mm))
            self.cp_metric_chips[m] = b
            control_row.addWidget(b)

        # Default sort mode (DPS Rank vs Class)
        self._cp_sort_mode = "dps"

        control_row.addStretch(1)
        # CRITICAL: DO NOT MOVE OUT OF left_lay!
        # control_row MUST be added to left_lay (NOT parent lay). Adding to lay will
        # push the right rail (skills inspector) down by an entire row. Do not change
        # or move this without explicit user confirmation.
        left_lay.addLayout(control_row)
        self.cp_class_chips: dict[str, QtWidgets.QPushButton] = {}

        # 1. Past Fights full-width scroll container
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll.setStyleSheet("background: transparent;")

        self.cp_content_container = QtWidgets.QWidget()
        self.cp_content_lay = QtWidgets.QVBoxLayout(self.cp_content_container)
        self.cp_content_lay.setContentsMargins(0, 0, 0, 0)
        self.cp_content_lay.setSpacing(8)

        self.cp_empty_lbl = QtWidgets.QLabel("No combat data recorded yet. Attack enemies or dummy in-game.")
        self.cp_empty_lbl.setAlignment(QtCore.Qt.AlignCenter)
        self.cp_empty_lbl.setStyleSheet(f"color: {theme.DIM}; font-size: 12px; padding: 40px 0;")
        self.cp_content_lay.addWidget(self.cp_empty_lbl)

        scroll.setWidget(self.cp_content_container)
        scroll.hide()
        self._cp_past_scroll = scroll

        # 2. Unified Roster Board (Left Master View)
        self.cp_header_row = _TableHeaderRow()
        self.cp_header_row.sort_requested.connect(self._set_combat_sort_mode)

        self.cp_class_board = QtWidgets.QWidget()
        self.cp_board_lay = QtWidgets.QVBoxLayout(self.cp_class_board)
        self.cp_board_lay.setContentsMargins(0, 0, 0, 0)
        self.cp_board_lay.setSpacing(4)
        self.cp_board_lay.addStretch(1)

        self._cp_col_lays: dict[str, QtWidgets.QVBoxLayout] = {}
        self._cp_col_frames: dict[str, QtWidgets.QFrame] = {}
        self._cp_col_headers: dict[str, tuple[QtWidgets.QLabel, QtWidgets.QLabel]] = {}
        self._cp_col_scroll = QtWidgets.QScrollArea()
        self._cp_col_scroll.setWidgetResizable(True)
        self._cp_col_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        self._cp_col_scroll.setStyleSheet("background: transparent;")
        self._cp_col_scroll.setWidget(self.cp_class_board)

        self.cp_board_container = QtWidgets.QWidget()
        bc_lay = QtWidgets.QVBoxLayout(self.cp_board_container)
        bc_lay.setContentsMargins(0, 0, 0, 0)
        bc_lay.setSpacing(0)
        bc_lay.addWidget(self.cp_header_row, 0)
        bc_lay.addWidget(self._cp_col_scroll, 1)

        self._cp_column_tabs = ("damage",)

        # Right Rail Container: Player Skills Inspector
        rail = QtWidgets.QVBoxLayout()
        rail.setSpacing(6)
        rail.setContentsMargins(0, 0, 0, 0)

        self.cp_rail_box = QtWidgets.QWidget()
        self.cp_rail_box.setMinimumWidth(470)
        self.cp_rail_box.setMaximumWidth(620)
        r_lay = QtWidgets.QVBoxLayout(self.cp_rail_box)
        r_lay.setContentsMargins(0, 0, 0, 0)
        r_lay.setSpacing(4)

        def _stat_cell(caption: str, color: str = theme.TEXT):
            w = QtWidgets.QFrame()
            w.setObjectName("StatCell")
            w.setStyleSheet(f"QFrame#StatCell {{ background: {theme.PANEL_LOW}; border: 1px solid {theme.BORDER}; border-radius: 4px; }}")
            l = QtWidgets.QVBoxLayout(w)
            l.setContentsMargins(8, 6, 8, 6)
            l.setSpacing(2)
            c = QtWidgets.QLabel(caption.upper())
            c.setAlignment(QtCore.Qt.AlignCenter)
            c.setStyleSheet(f"font-size: 9.5px; font-weight: 800; color: {theme.DIM}; letter-spacing: 0.5px;")
            v = QtWidgets.QLabel("--")
            v.setAlignment(QtCore.Qt.AlignCenter)
            v.setStyleSheet(f"font-size: 13.5px; font-weight: 800; color: {color}; font-family: monospace;")
            l.addWidget(c)
            l.addWidget(v)
            return w, v, c

        # SKILLS INSPECTOR PANEL (Two distinct cards, aligned flush to top)
        self.cp_rail_panel_skills = QtWidgets.QWidget()
        sp_lay = QtWidgets.QVBoxLayout(self.cp_rail_panel_skills)
        sp_lay.setContentsMargins(0, 0, 0, 0)
        sp_lay.setSpacing(6)

        # 1. Top Card: Player Summary & Stats
        self.cp_hero_box = QtWidgets.QFrame()
        self.cp_hero_box.setStyleSheet(
            f"QFrame {{ background-color: transparent; border: none; }}"
        )
        hb_lay = QtWidgets.QVBoxLayout(self.cp_hero_box)
        hb_lay.setContentsMargins(8, 4, 8, 4)
        hb_lay.setSpacing(6)

        # Hidden dummy cp_hero_name to preserve property callbacks
        self.cp_hero_name = QtWidgets.QLabel("")
        self.cp_hero_name.hide()

        # Top Action & Badge Row (Copy + Compare on Left | Rank/Class Badge on Right)
        hb_top_bar = QtWidgets.QHBoxLayout()
        hb_top_bar.setContentsMargins(0, 0, 0, 0)
        hb_top_bar.setSpacing(6)

        copy_btn = QtWidgets.QPushButton("📋 Copy")
        copy_btn.setStyleSheet(
            f"QPushButton {{ padding: 3px 8px; font-weight: 700; font-size: 10px; background: {theme.SURFACE}; "
            f"color: {theme.TEXT}; border-radius: 4px; border: 1px solid {theme.BORDER}; }}"
            f"QPushButton:hover {{ border-color: {theme.ACCENT}; color: {theme.ACCENT_LIGHT}; }}"
        )
        copy_btn.setCursor(QtCore.Qt.PointingHandCursor)
        copy_btn.clicked.connect(self._copy_selected_player_parse)
        hb_top_bar.addWidget(copy_btn)

        self.cp_hero_compare_btn = QtWidgets.QPushButton("⚖ Compare with You")
        self.cp_hero_compare_btn.setStyleSheet(
            f"QPushButton {{ padding: 3px 8px; font-weight: 700; font-size: 10px; background: {theme.SURFACE}; "
            f"color: {theme.TEXT}; border-radius: 4px; border: 1px solid {theme.BORDER}; }}"
            f"QPushButton:hover {{ border-color: {theme.GOLD}; color: {theme.GOLD}; background: {theme.PANEL_LOW}; }}"
        )
        self.cp_hero_compare_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self.cp_hero_compare_btn.clicked.connect(self._on_hero_compare_clicked)
        hb_top_bar.addWidget(self.cp_hero_compare_btn)

        hb_top_bar.addStretch(1)

        self.cp_hero_badge = QtWidgets.QLabel("")
        self.cp_hero_badge.setStyleSheet(
            f"font-size: 9px; font-weight: 800; color: {theme.GOLD}; "
            f"background: {theme.PANEL_LOW}; border: 1px solid {theme.GOLD}; border-radius: 3px; padding: 2px 6px;"
        )
        hb_top_bar.addWidget(self.cp_hero_badge)
        hb_lay.addLayout(hb_top_bar)

        # Overall Hero Stats (borderless columns with dividers — no nested boxes)
        def _hero_stat(caption: str, color: str) -> tuple[QtWidgets.QWidget, QtWidgets.QLabel, QtWidgets.QLabel]:
            box = QtWidgets.QVBoxLayout()
            box.setContentsMargins(0, 0, 0, 0)
            box.setSpacing(1)
            cap = QtWidgets.QLabel(caption.upper())
            cap.setAlignment(QtCore.Qt.AlignCenter)
            cap.setStyleSheet(f"font-size: 9.5px; font-weight: 800; color: {theme.DIM}; letter-spacing: 0.5px;")
            val = QtWidgets.QLabel("--")
            val.setAlignment(QtCore.Qt.AlignCenter)
            val.setStyleSheet(f"font-size: 13.5px; font-weight: 800; color: {color}; font-family: monospace;")
            box.addWidget(cap)
            box.addWidget(val)
            holder = QtWidgets.QWidget()
            holder.setLayout(box)
            return holder, val, cap

        hb_stats = QtWidgets.QHBoxLayout()
        hb_stats.setSpacing(8)
        w_dps, self.cp_hero_dps, self.cp_hero_dps_cap = _hero_stat("DPS", theme.ACCENT_LIGHT)
        w_tot, self.cp_hero_tot, self.cp_hero_tot_cap = _hero_stat("Total Dmg", theme.TEXT)
        w_shr, self.cp_hero_shr, self.cp_hero_shr_cap = _hero_stat("Raid Share", theme.GOLD)
        w_crt, self.cp_hero_crt, self.cp_hero_crt_cap = _hero_stat("Crit Rate", theme.ORANGE)
        hb_stats.addWidget(w_dps, 1)
        hb_stats.addWidget(w_tot, 1)
        hb_stats.addWidget(w_shr, 1)
        hb_stats.addWidget(w_crt, 1)
        hb_lay.addLayout(hb_stats)

        sp_lay.addWidget(self.cp_hero_box)

        # 2. Bottom Card: Skills Breakdown Table
        self.cp_skills_box = QtWidgets.QFrame()
        self.cp_skills_box.setStyleSheet(
            f"QFrame {{ background-color: transparent; border: none; }}"
        )
        sk_box_lay = QtWidgets.QVBoxLayout(self.cp_skills_box)
        sk_box_lay.setContentsMargins(6, 4, 6, 4)
        sk_box_lay.setSpacing(4)

        # Weapon families the local player used this fight (derived from
        # the skills below) — the link between skills and Gear weapons.
        self.cp_weapons_used_lbl = QtWidgets.QLabel("")
        self.cp_weapons_used_lbl.setStyleSheet(
            f"font-size: 10px; font-weight: 800; color: {theme.GOLD}; "
            f"letter-spacing: 0.3px; background: transparent;")
        self.cp_weapons_used_lbl.setVisible(False)
        sk_box_lay.insertWidget(0, self.cp_weapons_used_lbl)

        tbl_hdr = QtWidgets.QHBoxLayout()
        tbl_hdr.setContentsMargins(0, 2, 0, 2)
        tbl_hdr.setSpacing(6)

        ico_spacer = QtWidgets.QLabel("")
        ico_spacer.setFixedWidth(20)
        tbl_hdr.addWidget(ico_spacer)

        th_skill = QtWidgets.QLabel("SKILL")
        th_skill.setMaximumWidth(240)  # mirror SkillRow's ElideLabel cap so columns can't drift
        th_skill.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
        th_skill.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        th_skill.setStyleSheet(f"font-size: 11.5px; font-weight: 800; color: {theme.DIM}; letter-spacing: 0.5px;")
        tbl_hdr.addWidget(th_skill, 1)

        th_tot = QtWidgets.QLabel("TOTAL")
        th_tot.setMinimumWidth(62)
        th_tot.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        th_tot.setStyleSheet(f"font-size: 11.5px; font-weight: 800; color: {theme.DIM};")
        tbl_hdr.addWidget(th_tot)

        th_bar = QtWidgets.QLabel("SHARE")
        th_bar.setFixedWidth(60)
        th_bar.setAlignment(QtCore.Qt.AlignCenter)
        th_bar.setStyleSheet(f"font-size: 11.5px; font-weight: 800; color: {theme.DIM};")
        tbl_hdr.addWidget(th_bar)

        th_hits = QtWidgets.QLabel("HITS")
        th_hits.setFixedWidth(34)
        th_hits.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        th_hits.setStyleSheet(f"font-size: 11.5px; font-weight: 800; color: {theme.DIM};")
        tbl_hdr.addWidget(th_hits)

        th_crit = QtWidgets.QLabel("CRIT%")
        th_crit.setFixedWidth(38)
        th_crit.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        th_crit.setStyleSheet(f"font-size: 11px; font-weight: 800; color: {theme.DIM};")
        tbl_hdr.addWidget(th_crit)

        th_avg = QtWidgets.QLabel("AVG")
        th_avg.setFixedWidth(104)
        th_avg.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        th_avg.setStyleSheet(f"font-size: 11px; font-weight: 800; color: {theme.DIM};")
        tbl_hdr.addWidget(th_avg)

        sk_box_lay.addLayout(tbl_hdr)

        self.cp_skills_scroll = QtWidgets.QScrollArea()
        self.cp_skills_scroll.setWidgetResizable(True)
        self.cp_skills_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.cp_skills_scroll.setStyleSheet("background: transparent;")
        self.cp_skills_container = QtWidgets.QWidget()
        self.cp_skills_rows_lay = QtWidgets.QVBoxLayout(self.cp_skills_container)
        self.cp_skills_rows_lay.setContentsMargins(0, 0, 0, 0)
        self.cp_skills_rows_lay.setSpacing(5)
        self.cp_skills_rows_lay.addStretch(1)
        self.cp_skills_scroll.setWidget(self.cp_skills_container)
        sk_box_lay.addWidget(self.cp_skills_scroll, 1)
        sp_lay.addWidget(self.cp_skills_box, 1)

        r_lay.addWidget(self.cp_rail_panel_skills, 1)

        rail.addWidget(self.cp_rail_box)
        rail_container = QtWidgets.QWidget()
        rail_container.setLayout(rail)
        self._cp_rail_container = rail_container

        # 3. Full-Width Compare Container
        self.cp_full_compare_container = QtWidgets.QScrollArea()
        self.cp_full_compare_container.setWidgetResizable(True)
        self.cp_full_compare_container.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.cp_full_compare_container.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.cp_full_compare_container.setStyleSheet("background: transparent;")

        cmp_widget = QtWidgets.QWidget()
        cmp_lay = QtWidgets.QVBoxLayout(cmp_widget)
        cmp_lay.setContentsMargins(0, 0, 0, 0)
        cmp_lay.setSpacing(8)

        # Single sleek toolbar for Compare controls
        cmp_bar = QtWidgets.QFrame()
        cmp_bar.setStyleSheet(
            f"QFrame {{ background: {theme.PANEL}; border: 1px solid {theme.BORDER}; border-radius: 6px; }}"
        )
        cmp_bar_lay = QtWidgets.QHBoxLayout(cmp_bar)
        cmp_bar_lay.setContentsMargins(10, 5, 10, 5)
        cmp_bar_lay.setSpacing(8)

        def _v_sep():
            f = QtWidgets.QFrame()
            f.setFrameShape(QtWidgets.QFrame.VLine)
            f.setStyleSheet(f"color: {theme.BORDER}; border: none; border-left: 1px solid {theme.BORDER};")
            f.setFixedHeight(16)
            return f

        self.cp_cmp_same_class_cb = QtWidgets.QCheckBox("Same Class")
        self.cp_cmp_same_class_cb.setChecked(True)
        self.cp_cmp_same_class_cb.setToolTip("Auto-filter comparison candidates to your same class")
        self.cp_cmp_same_class_cb.setStyleSheet(f"font-size: 11px; font-weight: 700; color: {theme.TEXT};")
        self.cp_cmp_same_class_cb.toggled.connect(lambda _: self._on_compare_selection_changed())
        cmp_bar_lay.addWidget(self.cp_cmp_same_class_cb)

        cmp_bar_lay.addWidget(_v_sep())

        lbl_a = QtWidgets.QLabel("A:")
        lbl_a.setStyleSheet(f"font-size: 11.5px; font-weight: 900; color: {COMPARE_COLORS[0].name()};")
        cmp_bar_lay.addWidget(lbl_a)

        self.cp_compare_combo_a = QtWidgets.QComboBox()
        self.cp_compare_combo_a.setStyleSheet("QComboBox { padding: 3px 8px; font-size: 11px; font-weight: 700; min-width: 96px; }")
        self.cp_compare_combo_a.currentIndexChanged.connect(self._on_compare_selection_changed)
        cmp_bar_lay.addWidget(self.cp_compare_combo_a)

        vs_lbl = QtWidgets.QLabel("VS")
        vs_lbl.setStyleSheet(f"font-size: 11.5px; font-weight: 900; color: {theme.GOLD};")
        cmp_bar_lay.addWidget(vs_lbl)

        lbl_b = QtWidgets.QLabel("B:")
        lbl_b.setStyleSheet(f"font-size: 11.5px; font-weight: 900; color: {COMPARE_COLORS[1].name()};")
        cmp_bar_lay.addWidget(lbl_b)

        self.cp_compare_combo_b = QtWidgets.QComboBox()
        self.cp_compare_combo_b.setStyleSheet("QComboBox { padding: 3px 8px; font-size: 11px; font-weight: 700; min-width: 96px; }")
        self.cp_compare_combo_b.currentIndexChanged.connect(self._on_compare_selection_changed)
        cmp_bar_lay.addWidget(self.cp_compare_combo_b)

        cmp_bar_lay.addWidget(_v_sep())

        lbl_more = QtWidgets.QLabel("+ Extra:")
        lbl_more.setStyleSheet(f"font-size: 11px; font-weight: 800; color: {theme.DIM};")
        cmp_bar_lay.addWidget(lbl_more)

        lbl_c = QtWidgets.QLabel("C:")
        lbl_c.setStyleSheet(f"font-size: 11.5px; font-weight: 900; color: {COMPARE_COLORS[2].name()};")
        cmp_bar_lay.addWidget(lbl_c)
        self.cp_compare_combo_c = QtWidgets.QComboBox()
        self.cp_compare_combo_c.setStyleSheet("QComboBox { padding: 2px 6px; font-size: 10.5px; font-weight: 700; min-width: 64px; }")
        self.cp_compare_combo_c.currentIndexChanged.connect(self._on_compare_selection_changed)
        cmp_bar_lay.addWidget(self.cp_compare_combo_c)

        lbl_d = QtWidgets.QLabel("D:")
        lbl_d.setStyleSheet(f"font-size: 11.5px; font-weight: 900; color: {COMPARE_COLORS[3].name()};")
        cmp_bar_lay.addWidget(lbl_d)
        self.cp_compare_combo_d = QtWidgets.QComboBox()
        self.cp_compare_combo_d.setStyleSheet("QComboBox { padding: 2px 6px; font-size: 10.5px; font-weight: 700; min-width: 64px; }")
        self.cp_compare_combo_d.currentIndexChanged.connect(self._on_compare_selection_changed)
        cmp_bar_lay.addWidget(self.cp_compare_combo_d)

        lbl_e = QtWidgets.QLabel("E:")
        lbl_e.setStyleSheet(f"font-size: 11.5px; font-weight: 900; color: {COMPARE_COLORS[4].name()};")
        cmp_bar_lay.addWidget(lbl_e)
        self.cp_compare_combo_e = QtWidgets.QComboBox()
        self.cp_compare_combo_e.setStyleSheet("QComboBox { padding: 2px 6px; font-size: 10.5px; font-weight: 700; min-width: 64px; }")
        self.cp_compare_combo_e.currentIndexChanged.connect(self._on_compare_selection_changed)
        cmp_bar_lay.addWidget(self.cp_compare_combo_e)

        cmp_bar_lay.addStretch(1)
        cmp_lay.addWidget(cmp_bar)

        self.cp_cmp_summary_box = QtWidgets.QFrame()
        self.cp_cmp_summary_box.setObjectName("PerfGapBox")
        self.cp_cmp_summary_box.setStyleSheet(
            f"QFrame#PerfGapBox {{ background: {theme.PANEL}; border: 1px solid {theme.BORDER}; border-radius: 6px; }}"
        )
        cs_lay = QtWidgets.QVBoxLayout(self.cp_cmp_summary_box)
        cs_lay.setContentsMargins(12, 10, 12, 10)
        cs_lay.setSpacing(8)

        cs_top = QtWidgets.QHBoxLayout()
        cs_top.setSpacing(8)
        cs_title = QtWidgets.QLabel("PERFORMANCE GAP")
        cs_title.setStyleSheet(f"font-size: 12px; font-weight: 900; color: {theme.TEXT}; letter-spacing: 0.8px;")
        cs_top.addWidget(cs_title)
        self.cp_cmp_insight = QtWidgets.QLabel("")
        self.cp_cmp_insight.setWordWrap(True)
        self.cp_cmp_insight.setStyleSheet(f"font-size: 12.5px; font-weight: 700; color: {theme.TEXT};")
        cs_top.addWidget(self.cp_cmp_insight, 1)
        self.cp_cmp_class_match = QtWidgets.QLabel("")
        self.cp_cmp_class_match.setStyleSheet(
            f"font-size: 10.5px; font-weight: 800; color: {theme.GOLD}; "
            f"border: 1px solid {theme.BORDER}; border-radius: 8px; padding: 2px 8px;"
        )
        cs_top.addWidget(self.cp_cmp_class_match)
        cs_lay.addLayout(cs_top)

        gap_grid = QtWidgets.QGridLayout()
        gap_grid.setSpacing(6)
        for _c in range(6):
            gap_grid.setColumnStretch(_c, 1)

        w_lead, self.cp_cmp_winner, _ = _stat_cell("Leader", theme.TEXT)
        w_dps, self.cp_cmp_dps_delta, _ = _stat_cell("DPS Lead", theme.GOOD)
        w_pct, self.cp_cmp_pct_delta, _ = _stat_cell("% Edge", theme.GOOD)
        w_cg, self.cp_cmp_crit_gap, _ = _stat_cell("Crit Gap", theme.ORANGE)
        w_hr, self.cp_cmp_hit_rate, _ = _stat_cell("Hits/sec", theme.TEXT)
        w_tg, self.cp_cmp_top_gap, _ = _stat_cell("Top Gap", theme.ACCENT_LIGHT)

        self.cp_cmp_winner.setText("—")
        self.cp_cmp_dps_delta.setText("+0/s")
        self.cp_cmp_dps_delta.setStyleSheet(
            f"font-size: 18px; font-weight: 900; font-family: monospace; color: {theme.GOOD};")
        self.cp_cmp_pct_delta.setText("+0.0%")
        self.cp_cmp_pct_delta.setStyleSheet(
            f"font-size: 14px; font-weight: 800; font-family: monospace; color: {theme.GOOD};")

        gap_grid.addWidget(w_lead, 0, 0)
        gap_grid.addWidget(w_dps, 0, 1)
        gap_grid.addWidget(w_pct, 0, 2)
        gap_grid.addWidget(w_cg, 0, 3)
        gap_grid.addWidget(w_hr, 0, 4)
        gap_grid.addWidget(w_tg, 0, 5)
        cs_lay.addLayout(gap_grid)

        cmp_lay.addWidget(self.cp_cmp_summary_box)

        self.cp_cmp_table_box = QtWidgets.QFrame()
        self.cp_cmp_table_box.setObjectName("CompareTableBox")
        self.cp_cmp_table_box.setStyleSheet(
            f"QFrame#CompareTableBox {{ background: {theme.PANEL}; border: 1px solid {theme.BORDER}; border-radius: 6px; }}"
        )
        ct_lay = QtWidgets.QVBoxLayout(self.cp_cmp_table_box)
        ct_lay.setContentsMargins(10, 8, 10, 8)
        ct_lay.setSpacing(6)

        ct_hdr = QtWidgets.QLabel("ALIGNED SKILL PERFORMANCE COMPARISON MATRIX")
        ct_hdr.setStyleSheet(f"font-size: 11.5px; font-weight: 900; color: {theme.TEXT}; letter-spacing: 0.8px;")
        ct_lay.addWidget(ct_hdr)

        self.cp_cmp_rows_container = QtWidgets.QWidget()
        self.cp_cmp_rows_lay = QtWidgets.QGridLayout(self.cp_cmp_rows_container)
        self.cp_cmp_rows_lay.setContentsMargins(0, 0, 0, 0)
        self.cp_cmp_rows_lay.setHorizontalSpacing(6)
        self.cp_cmp_rows_lay.setVerticalSpacing(2)
        self.cp_cmp_rows_lay.setColumnStretch(1, 1)
        self.cp_cmp_rows_lay.setColumnStretch(2, 1)
        self.cp_cmp_rows_lay.setColumnStretch(7, 1)
        ct_lay.addWidget(self.cp_cmp_rows_container, 1)
        cmp_lay.addWidget(self.cp_cmp_table_box, 1)

        self.cp_full_compare_container.setWidget(cmp_widget)
        self.cp_full_compare_container.hide()

        # Splitter body: left column (chips + board / compare / fights) vs rail, directly under the graph
        left_lay.addWidget(self.cp_board_container, 1)
        left_lay.addWidget(self.cp_full_compare_container, 1)
        left_lay.addWidget(self._cp_past_scroll, 1)

        self.cp_body_split = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        self.cp_body_split.setChildrenCollapsible(False)
        self.cp_body_split.setHandleWidth(6)
        self.cp_body_split.setStyleSheet(
            f"QSplitter::handle {{ background-color: {theme.BORDER}; border-radius: 2px; margin: 0px 2px; }}"
            f"QSplitter::handle:hover {{ background-color: {theme.ACCENT}; }}"
        )
        self.cp_body_split.addWidget(left_col)
        self.cp_body_split.addWidget(rail_container)
        self.cp_body_split.setStretchFactor(0, 3)
        self.cp_body_split.setStretchFactor(1, 2)

        lay.addWidget(self.cp_body_split, 1)

        page.consume_pane_back = self._on_combat_pane_back

        old_show = page.showEvent
        def _on_combat_show(ev):
            old_show(ev)
            if getattr(self, "_cp_metric", "damage") != "damage":
                self._set_combat_metric("damage")
        page.showEvent = _on_combat_show

        self._cp_timer = QtCore.QTimer(page)
        self._cp_timer.timeout.connect(self._refresh_combat_page_live)
        self._cp_timer.start(250)

        self._cp_page = page
        self._cp_resize_watch = _RailResizeWatcher(
            page, self._update_combat_rail_for_width)
        self._update_combat_rail_for_width()
        self._sync_history_combo()
        return page

    def _on_combat_pane_back(self) -> bool:
        """Handle mouse back button inside Combat Page: if not on Damage tab, switch back to Damage tab."""
        if getattr(self, "_cp_metric", "damage") != "damage":
            self._set_combat_metric("damage")
            return True
        return False

    def _set_combat_page_tab(self, tab: str):
        self._cp_combat_tab = tab
        self._update_combat_tab_buttons()
        self._render_combat_breakdown()

    def _set_combat_page_segment(self, segment: str) -> None:
        t = self._get_active_tracker()
        if t:
            t.segment_view = segment
        self._cp_segment = segment
        self._update_combat_tab_buttons()
        self._render_combat_breakdown()

    def _update_combat_tab_buttons(self):
        self.cp_tab_dmg.setChecked(self._cp_combat_tab == "damage")
        self.cp_tab_heal.setChecked(self._cp_combat_tab == "healing")
        self.cp_tab_skills.setChecked(self._cp_combat_tab == "skills")
        for b in (self.cp_tab_dmg, self.cp_tab_heal, self.cp_tab_skills):
            b.setStyleSheet(
                f"QPushButton {{ padding: 6px 14px; font-weight: 700; border-radius: 4px; font-size: 11px; }}"
                f"QPushButton:checked {{ background-color: {theme.ACCENT_DIM}; color: {theme.ACCENT}; border: 1px solid {theme.ACCENT}; }}"
            )

    def _get_active_tracker(self) -> DpsTracker | None:
        if self.model and getattr(self.model, "dps", None):
            return self.model.dps
        ov = getattr(self, "overlay_mgr", None)
        if ov and hasattr(ov, "overlays"):
            ov_dps = ov.overlays.get("dps")
            if ov_dps and getattr(ov_dps, "tracker", None):
                return ov_dps.tracker
        if getattr(self, "_fallback_dps_tracker", None) is None:
            self._fallback_dps_tracker = DpsTracker(
                self.model, max_dist=getattr(getattr(self, "s", None), "dps_max_dist", 400.0))
        return getattr(self, "_fallback_dps_tracker", None)

    def _reset_combat_session(self):
        t = self._get_active_tracker()
        if t:
            t.reset()
        self._browse_sessions = None
        self._browse_char = ""
        self._auto_last_fight = False
        self._auto_last_suppressed = True
        self._user_explicitly_chose_live = True
        self._update_hist_banner(False)
        if (hasattr(self, "cp_history_combo")
                and self.cp_history_combo.currentIndex() != 0):
            self.cp_history_combo.setCurrentIndex(0)
        for c in getattr(self, "_cp_player_cards", {}).values():
            try:
                c.hide()
            except RuntimeError:
                pass
        if hasattr(self, "cp_empty_lbl"):
            self.cp_empty_lbl.show()
        self._reset_combat_rail()
        self._sync_history_combo()
        self._render_combat_breakdown()

    def _reset_combat_rail(self):
        """Zero the detail rail on session reset."""
        if not hasattr(self, "cp_rail_boss_box"):
            return
        self.cp_rail_boss_box.setVisible(False)
        self.cp_rail_burst_lbl.setText("Peak burst: --")
        self.cp_rail_deaths_lbl.setText("")
        self.cp_rail_deaths_lbl.setVisible(False)
        self.cp_rail_top_lbl.setText("--")
        self.cp_rail_top_sub.setText("no players yet")
        self.cp_rail_ally_lbl.setText("No combat data yet.")
        if hasattr(self, "cp_hero_name"):
            self.cp_hero_name.setText("Select a player")
            self.cp_hero_badge.setText("")
            self.cp_hero_dps.setText("--")
            self.cp_hero_tot.setText("--")
            self.cp_hero_shr.setText("--")
            self.cp_hero_crt.setText("--")
            if hasattr(self, "cp_hero_skill_count"):
                self.cp_hero_skill_count.setText("0 skills")
            if hasattr(self, "cp_stacked_bar"):
                self.cp_stacked_bar.set_skills([], 0.0)
            for row in getattr(self, "_cp_rail_skill_widgets", {}).values():
                row.hide()

    def _update_combat_rail_for_width(self) -> None:
        """Auto-hide the detail rail below CP_RAIL_AUTO_MIN_WIDTH."""
        if not hasattr(self, "cp_rail_box") or not hasattr(self, "_cp_rail_slot"):
            return
        dlg = getattr(self, "_cp_details_dlg", None)
        if dlg is not None and dlg.isVisible():
            return
        page = getattr(self, "_cp_page", None)
        width = page.width() if page is not None else self.cp_rail_box.width()
        if width <= 100:
            show = True
        else:
            show = width >= 860
        if self.cp_rail_box.isVisible() != show:
            self.cp_rail_box.setVisible(show)
        if hasattr(self, "cp_details_btn"):
            self.cp_details_btn.setVisible(not show)

    def _open_fight_details(self) -> None:
        """Open the auto-hidden rail as its own small window."""
        if not hasattr(self, "cp_rail_box"):
            return
        dlg = getattr(self, "_cp_details_dlg", None)
        if dlg is not None and dlg.isVisible():
            dlg.raise_()
            dlg.activateWindow()
            return
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("Fight Details")
        dlg.setWindowFlag(QtCore.Qt.WindowCloseButtonHint, True)
        lay = QtWidgets.QVBoxLayout(dlg)
        lay.setContentsMargins(0, 0, 0, 0)
        self.cp_rail_box.setParent(dlg)
        lay.addWidget(self.cp_rail_box)
        self.cp_rail_box.show()
        dlg.setFixedWidth(480)
        dlg.finished.connect(self._close_fight_details)
        self._cp_details_dlg = dlg
        dlg.open()

    def _close_fight_details(self, _result: int = 0) -> None:
        """Return the rail to the page body and re-run the width decision."""
        self._cp_details_dlg = None
        slot = getattr(self, "_cp_rail_slot", None)
        if slot is not None and hasattr(self, "cp_rail_box"):
            self.cp_rail_box.hide()
            slot.addWidget(self.cp_rail_box)
        self._update_combat_rail_for_width()

    def _history_source(self, t: DpsTracker | None) -> tuple[str, list[CombatSession]]:
        """Encounter-combo source: browsed archive, else live history."""
        if getattr(self, "_browse_sessions", None):
            return getattr(self, "_browse_char", ""), self._browse_sessions
        if t and getattr(t, "history", None) and len(t.history) > 0:
            return "", t.history
        all_disk = []
        for r in scan_history_dir(dps_dir()):
            for s in r.get("sessions", []):
                if s and (s.group_damage > 0 or s.group_heals > 0):
                    all_disk.append(s)
        all_disk.sort(key=lambda s: (s.start_time or 0.0))
        return "All Fights", all_disk

    def _sync_history_combo(self):
        if not hasattr(self, "cp_history_combo"):
            return
        t = self._get_active_tracker()

        items: list[tuple[str, str, CombatSession, str]] = []  # (plain_label, html_label, sess, tip)
        if getattr(self, "_browse_sessions", None):
            char = getattr(self, "_browse_char", "")
            for i, sess in enumerate(reversed(self._browse_sessions)):
                label = f"#{i+1}  " + _format_history_label(sess, char)
                html_label = _format_history_label_html(sess, char, i + 1)
                weaps = weapons_used(sess)
                tip = (f"{label}\nWeapons: {', '.join(w['name'] for w in weaps)}"
                       if weaps else label)
                items.append((label, html_label, sess, tip))
            mode_tag = f"browse_{char}_{len(self._browse_sessions)}"
        elif t and getattr(t, "history", None) and len(t.history) > 0:
            char = getattr(getattr(self, "model", None), "character_name", "") or ""
            for i, sess in enumerate(reversed(t.history)):
                label = f"#{i+1}  " + _format_history_label(sess, char)
                html_label = _format_history_label_html(sess, char, i + 1)
                weaps = weapons_used(sess)
                tip = (f"{label}\nWeapons: {', '.join(w['name'] for w in weaps)}"
                       if weaps else label)
                items.append((label, html_label, sess, tip))
            mode_tag = f"live_{len(t.history)}"
        else:
            now_scan = time.monotonic()
            if now_scan - getattr(self, "_last_disk_scan_ts", 0.0) > 2.0 or not hasattr(self, "_cached_all_disk"):
                self._last_disk_scan_ts = now_scan
                all_disk: list[tuple[str, CombatSession]] = []
                for r in scan_history_dir(dps_dir()):
                    ch = r.get("char", "")
                    for sess in r.get("sessions", []):
                        if sess and (sess.group_damage > 0 or sess.group_heals > 0):
                            all_disk.append((ch, sess))
                all_disk.sort(key=lambda cs: (cs[1].start_time or 0.0), reverse=True)
                self._cached_all_disk = all_disk
            else:
                all_disk = self._cached_all_disk
            for i, (ch, sess) in enumerate(all_disk):
                label = f"#{i+1}  " + _format_history_label(sess, ch)
                html_label = _format_history_label_html(sess, ch, i + 1)
                weaps = weapons_used(sess)
                tip = (f"{label}\nTarget: {(sess.target_name or '').replace('👑 ', '')}"
                       + (f"\nWeapons: {', '.join(w['name'] for w in weaps)}"
                          if weaps else ""))
                items.append((label, html_label, sess, tip))
            mode_tag = f"disk_{len(all_disk)}"

        # Restrict top fight selector dropdown to the last 3 past fights only
        items = items[:3]

        expected_count = len(items) + 1
        cur_count = self.cp_history_combo.count()
        if cur_count != expected_count or getattr(self, "_combo_tag", None) != mode_tag:
            cur_idx = self.cp_history_combo.currentIndex()
            self._combo_sessions = [None] + [sess for _, _, sess, _ in items]
            self.cp_history_combo.blockSignals(True)
            self.cp_history_combo.clear()
            live_html = f"<span style='color: {theme.GOOD}; font-weight: 800;'>● Live Encounter</span> <span style='color: {theme.MUTED};'>(Active)</span>"
            self.cp_history_combo.addItem("● Live Encounter (Active)")
            self.cp_history_combo.setItemData(0, live_html, QtCore.Qt.UserRole + 1)
            self.cp_history_combo.setItemData(0, "Live in-game encounter", QtCore.Qt.ToolTipRole)

            for label, html_label, sess, tip in items:
                idx = self.cp_history_combo.count()
                self.cp_history_combo.addItem(label)
                self.cp_history_combo.setItemData(idx, html_label, QtCore.Qt.UserRole + 1)
                self.cp_history_combo.setItemData(idx, tip, QtCore.Qt.ToolTipRole)

            target_idx = min(max(0, cur_idx), expected_count - 1) if cur_idx >= 0 else 0
            self.cp_history_combo.setCurrentIndex(target_idx)
            self._selected_history_idx = target_idx
            self._combo_tag = mode_tag
            self.cp_history_combo.blockSignals(False)

            target_idx = min(max(0, cur_idx), expected_count - 1) if cur_idx >= 0 else 0
            self.cp_history_combo.setCurrentIndex(target_idx)
            self._selected_history_idx = target_idx
            self._combo_tag = mode_tag
            self.cp_history_combo.blockSignals(False)

    def _on_history_selection_changed(self, idx: int):
        if idx < 0:
            return
        self._selected_history_idx = idx
        if idx == 0:
            self._user_explicitly_chose_live = True
            self._auto_last_fight = False
            self._auto_last_suppressed = True
            self._update_hist_banner(False)
            if getattr(self, "_browse_sessions", None):
                self._browse_sessions = None
                self._browse_char = ""
                self._sync_history_combo()
        else:
            self._user_explicitly_chose_live = False
            self._auto_last_fight = False
        self._update_hist_banner(False)
        session = self._get_displayed_session()
        if session and hasattr(self, "cp_plotter"):
            self.cp_plotter.set_session(session)
        self._render_combat_breakdown()

    def _browse_fights(self, char: str, sessions: list[CombatSession], select_idx: int = 0) -> None:
        """Show archived ``sessions`` in the Encounter combo (0 = oldest)."""
        self._browse_char = char or ""
        self._browse_sessions = list(sessions)
        self._sync_history_combo()
        idx = min(len(sessions) - select_idx, self.cp_history_combo.count() - 1)
        if self.cp_history_combo.currentIndex() != idx:
            self.cp_history_combo.setCurrentIndex(idx)
        else:
            self._on_history_selection_changed(idx)

    @staticmethod
    def _session_has_live_data(s: CombatSession) -> bool:
        return bool(s) and (s.group_damage > 0.0 or s.group_heals > 0.0)

    def _update_hist_banner(self, on: bool, session: CombatSession | None = None) -> None:
        if not hasattr(self, "cp_hist_banner"):
            return
        if on and session is not None:
            when = (time.strftime("%m-%d %H:%M", time.localtime(session.start_time))
                    if session.start_time else "earlier")
            nm = _clean_session_name(session.name)
            self.cp_hist_banner.setText(
                f"🕘 Showing your last fight · {when} · {nm} · "
                f"{session.group_damage:,.0f} DMG — no live combat yet. "
                f"It switches to LIVE the moment you start fighting.")
            self.cp_hist_banner.show()
        else:
            self.cp_hist_banner.setText("")
            self.cp_hist_banner.hide()

    def _newest_archive(self) -> tuple[str, list[CombatSession], int] | None:
        rows = scan_history_dir(dps_dir())
        best = None
        best_t = -1.0
        for r in rows:
            for si, sess in enumerate(r["sessions"]):
                if not self._session_has_live_data(sess):
                    continue
                ts = sess.start_time or 0.0
                if ts >= best_t:
                    best_t = ts
                    best = (r["char"], r["sessions"], si)
        return best

    def _maybe_show_last_fight(self, t: DpsTracker) -> None:
        return

    def _select_live_combat(self) -> None:
        self._selected_history_idx = 0
        self._browse_sessions = None
        self._browse_char = ""
        self._auto_last_fight = False
        self._auto_last_suppressed = True
        if hasattr(self, "cp_history_combo"):
            self.cp_history_combo.blockSignals(True)
            if self.cp_history_combo.count() > 0:
                self.cp_history_combo.setCurrentIndex(0)
            self.cp_history_combo.blockSignals(False)
        self._update_hist_banner(False)
        self._render_combat_breakdown()

    def _auto_exit_to_live(self, t: DpsTracker) -> None:
        if not (getattr(self, "_auto_last_fight", False) and self._browse_sessions is not None):
            return
        if not self._session_has_live_data(t.session):
            return
        self._auto_last_fight = False
        self._browse_sessions = None
        self._browse_char = ""
        self._selected_history_idx = 0
        if hasattr(self, "cp_history_combo"):
            self.cp_history_combo.blockSignals(True)
            self.cp_history_combo.setCurrentIndex(0)
            self.cp_history_combo.blockSignals(False)
        self._update_hist_banner(False)

    def _open_past_fights(self):
        """Open the Past Fights modal dialog."""
        open_past_fights_dialog(
            self,
            tracker_getter=self._get_active_tracker,
            browse_cb=lambda c, s, si: (
                setattr(self, "_auto_last_fight", False),
                setattr(self, "_auto_last_suppressed", False),
                self._update_hist_banner(False),
                self._browse_fights(c, s, select_idx=si)
            ),
            after_change_cb=lambda: (
                self._sync_history_combo(),
                self._render_combat_breakdown()
            )
        )

    def _get_displayed_session(self) -> CombatSession | None:
        t = self._get_active_tracker()
        idx = getattr(self, "_selected_history_idx", 0)
        if idx == 0:
            return t.session if t else None
        combo_sessions = getattr(self, "_combo_sessions", [])
        if 0 <= idx < len(combo_sessions) and combo_sessions[idx] is not None:
            return combo_sessions[idx]
        _, sessions = self._history_source(t)
        pos = idx - 1
        if 0 <= pos < len(sessions):
            return sessions[len(sessions) - 1 - pos]
        return t.session if t else None

    def _refresh_combat_page_live(self):
        cur_nav = getattr(self, "_current_page", lambda: "")()
        if "combat" not in str(cur_nav).lower():
            return
        t = self._get_active_tracker()
        if not t:
            return

        t.update()
        self._auto_exit_to_live(t)
        self._sync_history_combo()
        self._update_combat_rail_for_width()

        session = self._get_displayed_session()
        if not session:
            return

        dur = session.duration
        mins = int(dur // 60)
        secs = dur % 60
        self.cp_card_dur.set_value("dur", f"{mins:02d}:{secs:04.1f}")
        if self._selected_history_idx != 0 or self._browse_sessions is not None:
            self.cp_card_dur.set_sub("dur", f"ARCHIVED · {_clean_session_name(session.name)}")
        else:
            self.cp_card_dur.set_sub("dur", f"Status: {session.state} · [{session.name}]")

        if self._cp_solo_filter_on():
            me_p = next((p for p in session.players.values() if p.is_me), None)
            grp_dmg = me_p.total_damage if me_p else 0.0
            grp_heals = me_p.heals if me_p else 0.0
            grp_taken = getattr(me_p, "total_damage_taken", me_p.damage_taken + me_p.damage_taken_est) if me_p else 0.0
            grp_taken_est = me_p.damage_taken_est if me_p else 0.0
        else:
            grp_dmg = session.group_damage
            grp_heals = session.group_heals
            grp_taken = session.group_taken
            grp_taken_est = session.group_taken_est
        grp_dps = grp_dmg / max(1.0, dur)
        grp_hps = grp_heals / max(1.0, dur)
        self.cp_card_dmg.set_value("dmg", f"{grp_dmg:,.0f}")
        self.cp_card_dmg.set_sub("dmg", f"DPS: {grp_dps:,.1f}/s")

        self.cp_card_heal.set_value("heal", f"{grp_heals:,.0f}")
        self.cp_card_heal.set_sub("heal", f"HPS: {grp_hps:,.1f}/s")

        if grp_taken > 0:
            tag = ("≈ includes HP estimates" if grp_taken_est > 0 else "from damage events")
            self.cp_card_taken.set_value("taken", f"{grp_taken:,.0f}")
            self.cp_card_taken.set_sub("taken", tag)
        else:
            self.cp_card_taken.set_value("taken", "0")
            self.cp_card_taken.set_sub("taken", "from damage events")

        t_name = session.target_name.replace("👑 ", "")
        if session.target_max_hp > 0:
            pct = (session.target_hp / session.target_max_hp) * 100.0
            self.cp_card_target.set_value("target", t_name[:16])
            self.cp_card_target.set_sub("target", f"{session.target_hp:,.0f} / {session.target_max_hp:,.0f} ({pct:.0f}%)")
        else:
            self.cp_card_target.set_value("target", t_name[:16])
            if (t_name not in ("None", "") and (self._selected_history_idx != 0 or self._browse_sessions is not None)):
                self.cp_card_target.set_sub("target", "archived · HP not recorded")
            else:
                self.cp_card_target.set_sub("target", "No active target")

        if hasattr(self, "cp_plotter"):
            self.cp_plotter.set_session(session)
            peak_txt = f"⚡ {session.burst_peak:,.0f}/s" if session.burst_peak > 0 else "⚡ --"
            death_txt = f"☠ {len(session.deaths)}" if session.deaths else "☠ 0"
            if hasattr(self, "cp_stat_strip"):
                self.cp_stat_strip.set_value("burst", peak_txt)
                self.cp_stat_strip.set_value("deaths", death_txt)

        self._render_combat_breakdown()

    def _update_combat_rail(self, session) -> None:
        """Fill the detail rail from the displayed session."""
        if not hasattr(self, "cp_rail_boss_box"):
            return
        t_name = (session.target_name or "None").replace("👑 ", "")
        is_boss = bool(session.target_name and "👑" in session.target_name)
        self.cp_rail_boss_nm.setText(("👑 " if is_boss else "🎯 ") + t_name)
        if session.target_max_hp > 0:
            pct = max(0.0, min(100.0, (session.target_hp / session.target_max_hp) * 100.0))
            self.cp_rail_boss_hp.setText(
                f"{session.target_hp:,.0f} / {session.target_max_hp:,.0f} · {pct:.1f}%")
            self.cp_rail_boss_bar.setValue(int(pct))
            self.cp_rail_boss_box.setVisible(True)
        else:
            self.cp_rail_boss_box.setVisible(False)

        if session.burst_peak > 0:
            burst_txt = f"Peak burst: {session.burst_peak:,.0f}/s"
        else:
            burst_txt = "Peak burst: --"
        self.cp_rail_burst_lbl.setText(burst_txt)
        if session.deaths:
            lines = []
            for off, name in list(session.deaths)[-4:]:
                mm = int(off // 60)
                ss = off % 60
                lines.append(f"☠ {mm:02d}:{ss:04.1f}  {name}")
            self.cp_rail_deaths_lbl.setText("\n".join(lines))
            self.cp_rail_deaths_lbl.setVisible(True)
        else:
            self.cp_rail_deaths_lbl.setText("No deaths")
            self.cp_rail_deaths_lbl.setVisible(False)

        me = next((p for p in session.players.values() if p.is_me), None)
        me = me or (next(iter(session.players.values()), None))
        if me:
            sk = _ranked_skill_total(me)
            if sk:
                s0 = sk[0]
                uses = s0.hit_count + s0.heal_count
                self.cp_rail_top_lbl.setText(s0.name)
                if s0.damage > 0 and s0.heals > 0:
                    sub = f"D {s0.damage:,.0f} · H {s0.heals:,.0f} · {uses} uses"
                else:
                    sub = f"{s0.total:,.0f} · {uses} uses"
                self.cp_rail_top_sub.setText(sub)
            else:
                self.cp_rail_top_lbl.setText("--")
                self.cp_rail_top_sub.setText("no skills yet")
        else:
            self.cp_rail_top_lbl.setText("--")
            self.cp_rail_top_sub.setText("no players yet")

        allies = [p for p in session.players.values() if not p.is_me]
        if not allies:
            self.cp_rail_ally_lbl.setText("No other players in this session's parse.")
        else:
            decoded = [p for p in allies if p.total_damage > 0 or p.heals > 0]
            with_dmg = sum(1 for p in decoded if p.total_damage > 0)
            with_heal = sum(1 for p in decoded if p.heals > 0)
            if decoded:
                self.cp_rail_ally_lbl.setText(
                    f"{len(decoded)} of {len(allies)} allies decoded hits ({with_dmg} dmg, {with_heal} heal).")
            else:
                self.cp_rail_ally_lbl.setText(
                    f"Reader is LIVE but {len(allies)} nearby allies sent no floaties — server delivery limit, not a reader fault.")

    def _cp_solo_filter_on(self) -> bool:
        if (getattr(self, "_selected_history_idx", 0) != 0
                or getattr(self, "_browse_sessions", None) is not None):
            return False
        if not bool(getattr(self.s, "dps_solo_only", False)):
            return False
        return True

    def _render_past_fights(self, force: bool = False) -> None:
        """Render the Past Fights list in the content area with row selection & top delete toolbar."""
        rows = scan_history_dir(dps_dir())
        filter_kind = getattr(self, "_cp_fights_filter", "all")

        # Fingerprint check: avoid destroying and rebuilding widgets if files on disk have not changed
        fp_parts = []
        for r in rows:
            for s in r["sessions"]:
                fp_parts.append((r["char"], s.start_time, s.name, int(s.duration), int(s.group_damage)))
        past_fp = (len(rows), filter_kind, tuple(sorted(fp_parts, key=lambda x: (x[0], x[1]))))

        if not force and getattr(self, "_last_past_fights_fp", None) == past_fp:
            return
        self._last_past_fights_fp = past_fp

        for i in reversed(range(self.cp_content_lay.count())):
            w = self.cp_content_lay.itemAt(i).widget()
            if w and w is not self.cp_empty_lbl:
                w.setParent(None)
                w.deleteLater()

        if not rows:
            self.cp_content_lay.addWidget(QtWidgets.QLabel(
                "No archived fights found in moddata yet. Finish a fight "
                "and it is saved here automatically (per character)."))
            return

        # Top Batch Action Bar for Past Fights
        batch_bar = QtWidgets.QFrame()
        # Fixed height so the toolbar never stretches when few rows render
        batch_bar.setSizePolicy(QtWidgets.QSizePolicy.Expanding,
                                QtWidgets.QSizePolicy.Fixed)
        batch_bar.setStyleSheet(
            f"QFrame {{ background: {theme.PANEL}; border: 1px solid {theme.BORDER}; border-radius: 6px; }}"
        )
        bl = QtWidgets.QHBoxLayout(batch_bar)
        bl.setContentsMargins(12, 6, 12, 6)
        bl.setSpacing(10)

        select_all_cb = QtWidgets.QCheckBox("Select All")
        select_all_cb.setStyleSheet(f"font-size: 11.5px; font-weight: 700; color: {theme.TEXT};")
        bl.addWidget(select_all_cb)

        # Kind filter: a row of checkable chips (All / Boss / Trash / Test Dummy)
        filter_chips: dict[str, QtWidgets.QPushButton] = {}
        for fk, lbl, col in (("all", "All Fights", theme.ACCENT),
                             ("boss", "⚔ Boss", theme.GOLD),
                             ("trash", "🗑 Trash", theme.DIM),
                             ("dummy", "🎯 Test Dummy", theme.ACCENT_LIGHT)):
            b = QtWidgets.QPushButton(lbl)
            b.setCheckable(True)
            b.setChecked(fk == filter_kind)
            b.setCursor(QtCore.Qt.PointingHandCursor)
            b.setStyleSheet(
                f"QPushButton {{ background: {theme.PANEL_LOW}; color: {theme.TEXT}; "
                f"border: 1px solid {theme.BORDER}; border-radius: 4px; padding: 2px 10px; "
                f"font-weight: 700; font-size: 11px; }}"
                f"QPushButton:checked {{ background: {theme.with_alpha(col, 40)}; "
                f"color: {col}; border: 1px solid {col}; }}"
                f"QPushButton:hover {{ border-color: {col}; }}"
            )
            b.clicked.connect(lambda _=False, k=fk: self._set_fights_filter(k))
            filter_chips[fk] = b
            bl.addWidget(b)

        bl.addStretch(1)

        load_sel_btn = QtWidgets.QPushButton("📂 Load Selected")
        load_sel_btn.setEnabled(False)
        load_sel_btn.setCursor(QtCore.Qt.PointingHandCursor)
        load_sel_btn.setStyleSheet(
            f"QPushButton {{ background: {theme.PANEL_LOW}; color: {theme.DIM}; "
            f"border: 1px solid {theme.BORDER}; border-radius: 4px; padding: 5px 12px; font-weight: 800; font-size: 11.5px; }}"
            f"QPushButton:enabled {{ background: {theme.ACCENT_DIM}; color: {theme.ACCENT_LIGHT}; "
            f"border: 1px solid {theme.ACCENT}; }}"
            f"QPushButton:enabled:hover {{ background: {theme.ACCENT}; color: #00354a; }}"
        )
        bl.addWidget(load_sel_btn)

        delete_batch_btn = QtWidgets.QPushButton("🗑 Delete Selected (0)")
        delete_batch_btn.setEnabled(False)
        delete_batch_btn.setCursor(QtCore.Qt.PointingHandCursor)
        delete_batch_btn.setStyleSheet(
            f"QPushButton {{ background: {theme.PANEL_LOW}; color: {theme.DIM}; "
            f"border: 1px solid {theme.BORDER}; border-radius: 4px; padding: 5px 12px; font-weight: 800; font-size: 11.5px; }}"
            f"QPushButton:enabled {{ background: {theme.with_alpha(theme.DANGER, 25)}; color: {theme.DANGER}; "
            f"border: 1px solid {theme.DANGER}; }}"
            f"QPushButton:enabled:hover {{ background: {theme.DANGER}; color: #ffffff; }}"
        )
        bl.addWidget(delete_batch_btn)
        self.cp_content_lay.addWidget(batch_bar)

        row_states: list[dict] = []

        def _update_top_bar():
            sel_items = [st for st in row_states if st["selected"]]
            cnt = len(sel_items)
            delete_batch_btn.setText(f"🗑 Delete Selected ({cnt})" if cnt > 0 else "🗑 Delete Selected (0)")
            delete_batch_btn.setEnabled(cnt > 0)
            load_sel_btn.setEnabled(cnt > 0)
            select_all_cb.blockSignals(True)
            select_all_cb.setChecked(cnt == len(row_states) and cnt > 0)
            select_all_cb.blockSignals(False)

        def _on_select_all(on: bool):
            for st in row_states:
                st["selected"] = on
                st["update_style"]()
            _update_top_bar()

        select_all_cb.toggled.connect(_on_select_all)

        def _on_load_selected():
            sel_items = [st for st in row_states if st["selected"]]
            if sel_items:
                target = sel_items[0]
                self._load_past_fight(target["char"], target["sess"])

        load_sel_btn.clicked.connect(_on_load_selected)

        def _on_delete_batch():
            items_to_del = [(st["path"], st["sess"]) for st in row_states if st["selected"]]
            if items_to_del:
                delete_past_fights_batch(
                    self, items_to_del,
                    active_tracker=self._get_active_tracker(),
                    browse_holder=self,
                    after_delete_cb=lambda: (
                        setattr(self, "_cp_past_fights_dirty", True),
                        self._sync_history_combo(),
                        self._render_combat_breakdown()
                    )
                )

        delete_batch_btn.clicked.connect(_on_delete_batch)

        shown = 0
        for r in rows:
            for si, sess in enumerate(r["sessions"]):
                kind = (sess.kind or "overall").lower()
                # Kind filter: boss / trash / Test Dummy. Dummy matches by
                # kind OR target so older archives still group correctly.
                if filter_kind != "all":
                    if filter_kind == "dummy":
                        is_dummy = (kind == "dummy"
                                    or udata.is_training_dummy(sess.target_name))
                        if not is_dummy:
                            continue
                    elif kind != filter_kind:
                        continue
                shown += 1
                when = (time.strftime("%m-%d %H:%M", time.localtime(sess.start_time))
                        if sess.start_time else "--")
                mins = int(sess.duration // 60)
                secs = sess.duration % 60
                nm = _clean_session_name(sess.name)
                k_col, k_txt = _KIND_STYLE.get(kind, (theme.ACCENT, kind.upper()))
                raw_boss = (sess.target_name or "").replace("👑 ", "").replace("🎯 ", "").strip()
                is_boss = bool(sess.target_name and "👑" in sess.target_name)
                if not raw_boss or raw_boss == "None":
                    boss_txt = "no boss recorded"
                    boss_col = theme.DIM
                    boss_pre = "🎯 "
                elif is_boss:
                    boss_txt = raw_boss
                    boss_col = theme.GOLD
                    boss_pre = "👑 "
                else:
                    boss_txt = raw_boss
                    boss_col = theme.MUTED
                    boss_pre = "🎯 "

                row_w = QtWidgets.QFrame()
                row_w.setObjectName("PastFightRow")
                # Fixed height so rows keep their natural size even when a
                # filter leaves few rows — never stretched to fill the area
                row_w.setSizePolicy(QtWidgets.QSizePolicy.Expanding,
                                    QtWidgets.QSizePolicy.Fixed)
                row_w.setCursor(QtCore.Qt.PointingHandCursor)

                state_item = {
                    "path": r["path"],
                    "char": r["char"],
                    "sess": sess,
                    "selected": False,
                    "widget": row_w,
                }

                def _apply_row_style(rw=row_w, st=state_item):
                    is_sel = st["selected"]
                    bg_col = theme.ACCENT_DIM if is_sel else theme.PANEL_LOW
                    brd_col = theme.ACCENT if is_sel else theme.BORDER
                    rw.setStyleSheet(
                        f"QFrame#PastFightRow {{ background: {bg_col}; border: 1px solid {brd_col}; border-radius: 6px; }}"
                        f"QFrame#PastFightRow:hover {{ background: {theme.PANEL_HI}; border-color: {theme.ACCENT}; }}"
                    )

                state_item["update_style"] = _apply_row_style
                _apply_row_style()
                row_states.append(state_item)

                # Single click selects row; double click loads fight!
                def _bind_click(st=state_item, ch_nm=r["char"], s_obj=sess):
                    def _on_mouse_press(ev):
                        if ev.button() == QtCore.Qt.LeftButton:
                            st["selected"] = not st["selected"]
                            st["update_style"]()
                            _update_top_bar()
                            ev.accept()
                    def _on_double_click(ev):
                        if ev.button() == QtCore.Qt.LeftButton:
                            self._load_past_fight(ch_nm, s_obj)
                            ev.accept()
                    st["widget"].mousePressEvent = _on_mouse_press
                    st["widget"].mouseDoubleClickEvent = _on_double_click

                _bind_click()

                h = QtWidgets.QHBoxLayout(row_w)
                h.setContentsMargins(12, 8, 12, 8)
                h.setSpacing(12)

                txt_col = QtWidgets.QVBoxLayout()
                txt_col.setSpacing(3)

                top = QtWidgets.QHBoxLayout()
                top.setSpacing(8)
                name_lbl = QtWidgets.QLabel(nm)
                name_lbl.setStyleSheet(
                    f"font-size: 14px; font-weight: 800; color: {theme.TEXT}; background: transparent;")
                top.addWidget(name_lbl)
                kind_lbl = QtWidgets.QLabel(k_txt)
                kind_lbl.setStyleSheet(
                    f"font-size: 10px; font-weight: 800; color: {k_col}; "
                    f"background: {theme.with_alpha(k_col, 28)}; border-radius: 3px; padding: 2px 6px;")
                top.addWidget(kind_lbl)
                top.addStretch(1)
                dmg_lbl = QtWidgets.QLabel(f"{sess.group_damage:,.0f} DMG")
                dmg_lbl.setStyleSheet(
                    f"font-size: 14px; font-weight: 900; color: {theme.GOLD}; font-family: monospace; background: transparent;")
                top.addWidget(dmg_lbl)
                txt_col.addLayout(top)

                bot = QtWidgets.QHBoxLayout()
                bot.setSpacing(8)
                date_lbl = QtWidgets.QLabel(when)
                date_lbl.setStyleSheet(
                    f"font-size: 11.5px; color: {theme.DIM}; font-family: monospace; background: transparent;")
                bot.addWidget(date_lbl)
                char_lbl = QtWidgets.QLabel(f"[{r['char']}]")
                char_lbl.setStyleSheet(
                    f"font-size: 11.5px; font-weight: 700; color: {theme.ACCENT_LIGHT}; background: transparent;")
                bot.addWidget(char_lbl)

                if (udata.is_training_dummy(raw_boss) or udata.is_training_dummy(sess.target_name)):
                    dpm = icons.asset_icon("target-dummy", 16)
                    if dpm is None or dpm.isNull():
                        dpm = icons.ui_icon("target-dummy", boss_col, 16)
                    if dpm is not None and not dpm.isNull():
                        dummy_ic = QtWidgets.QLabel()
                        dummy_ic.setPixmap(dpm)
                        dummy_ic.setToolTip("Training dummy (own boss group)")
                        dummy_ic.setStyleSheet("background: transparent;")
                        bot.addWidget(dummy_ic)

                boss_lbl = QtWidgets.QLabel(f"{boss_pre}{boss_txt}")
                boss_lbl.setStyleSheet(
                    f"font-size: 13.5px; font-weight: 800; color: {boss_col}; background: transparent;")
                boss_lbl.setToolTip(f"Boss / target: {boss_pre}{boss_txt}")
                bot.addWidget(boss_lbl, 1)
                dur_lbl = QtWidgets.QLabel(f"{mins:02d}:{secs:02.0f}")
                dur_lbl.setStyleSheet(
                    f"font-size: 11.5px; color: {theme.MUTED}; font-family: monospace; background: transparent;")
                bot.addWidget(dur_lbl)
                txt_col.addLayout(bot)

                # Weapon loadout the local character used this fight, at a glance
                weaps = weapons_used(sess)
                if weaps:
                    weap_lbl = QtWidgets.QLabel(
                        "Weapons: " + " · ".join(
                            f"{_WEAPON_FAMILY_GLYPHS.get(w['type'], '')} {w['name']}"
                            for w in weaps))
                    weap_lbl.setStyleSheet(
                        f"font-size: 10px; font-weight: 700; color: {theme.GOLD}; "
                        f"background: transparent;")
                    txt_col.addWidget(weap_lbl)

                h.addLayout(txt_col, 1)

                # Clean Load Fight button
                load_btn = QtWidgets.QPushButton("📂 Load Fight")
                load_btn.setStyleSheet(
                    f"QPushButton {{ background: {theme.ACCENT_DIM}; color: {theme.ACCENT_LIGHT}; "
                    f"border: none; font-weight: 800; font-size: 11.5px; "
                    f"padding: 6px 14px; border-radius: 4px; }}"
                    f"QPushButton:hover {{ background: {theme.ACCENT}; color: #00354a; }}"
                )
                load_btn.setCursor(QtCore.Qt.PointingHandCursor)
                load_btn.setToolTip(f"Load '{nm}' into DPS analyzer")
                load_btn.clicked.connect(lambda _, ch=r["char"], s=sess: self._load_past_fight(ch, s))
                h.addWidget(load_btn, 0, QtCore.Qt.AlignVCenter)

                tip = (f"{nm} · {boss_pre}{boss_txt} · "
                       f"{mins:02d}:{secs:02.0f} · {sess.group_damage:,.0f} DMG"
                       + (f"\nWeapons: {', '.join(w['name'] for w in weaps)}"
                          if weaps else ""))
                row_w.setToolTip(tip)
                self.cp_content_lay.addWidget(row_w)

        if shown == 0 and rows:
            self.cp_content_lay.addWidget(QtWidgets.QLabel(
                f"No fights match the {_FIGHTS_FILTER_LABELS[filter_kind]} filter."))
        self.cp_empty_lbl.setVisible(not rows)

    def _set_fights_filter(self, fk: str) -> None:
        """Toggle the Past Fights kind filter (all / boss / trash / dummy)."""
        if fk == getattr(self, "_cp_fights_filter", "all"):
            return
        self._cp_fights_filter = fk
        self._render_past_fights()

    def _on_skill_popup_closed(self, skill_id: str) -> None:
        if skill_id:
            self._last_closed_skill_id = skill_id
            self._last_closed_skill_ts = time.monotonic()
        if getattr(self, "_cp_skill_popup", None) is not None:
            self._cp_skill_popup = None

    def _open_skill_popup(self, sp, row) -> None:
        """Open the skill-info popup under a skills-rail row.

        Shows the skill's full effect (type, stat line, description,
        upgrades) plus the weapon(s) that grant it, anchored just below
        the clicked row. Reopening replaces any open popup."""
        if not sp or not getattr(sp, "skill_id", None):
            return

        sid = sp.skill_id
        now = time.monotonic()

        # If clicking the currently open popup's row, toggle it closed
        cur_pop = getattr(self, "_cp_skill_popup", None)
        if cur_pop is not None and getattr(cur_pop, "skill_id", None) == sid:
            self._last_closed_skill_id = sid
            self._last_closed_skill_ts = now
            try:
                cur_pop.close()
                cur_pop.deleteLater()
            except RuntimeError:
                pass
            self._cp_skill_popup = None
            return

        # Cooldown: prevent reopening the same skill within 1 second after closing
        last_closed_id = getattr(self, "_last_closed_skill_id", None)
        last_closed_ts = getattr(self, "_last_closed_skill_ts", 0.0)
        if last_closed_id == sid and (now - last_closed_ts) < 1.0:
            return

        if cur_pop is not None:
            try:
                cur_pop.close()
                cur_pop.deleteLater()
            except RuntimeError:
                pass
            self._cp_skill_popup = None

        pop = _SkillWeaponPopup(sp, self)
        pop.adjustSize()
        self._cp_skill_popup = pop
        pos = row.mapToGlobal(QtCore.QPoint(0, row.height() + 4))
        scr = QtGui.QGuiApplication.screenAt(pos)
        if scr is None:
            scr = QtGui.QGuiApplication.primaryScreen()
        if scr is not None:
            geo = scr.availableGeometry()
            pos.setX(max(geo.left(), min(pos.x(),
                                        geo.right() - pop.width())))
            pos.setY(max(geo.top(), min(pos.y(),
                                        geo.bottom() - pop.height())))
        pop.move(pos)
        pop.show()

    def _load_past_fight(self, char_name: str, sess: CombatSession) -> None:
        """Load an archived fight into active view and switch to Damage breakdown."""
        self._auto_last_fight = False
        self._auto_last_suppressed = True
        self._browse_fights(char_name or "", [sess], select_idx=0)
        self._set_combat_metric("damage")

    def _delete_past_fight(self, path: str | Path, sess: CombatSession) -> None:
        """Delete one archived fight from its history file + in-memory copies."""
        delete_past_fight(
            self,
            path=path,
            sess=sess,
            active_tracker=self._get_active_tracker(),
            browse_holder=self,
            after_delete_cb=lambda: (
                setattr(self, "_cp_past_fights_dirty", True),
                self._sync_history_combo(),
                self._render_combat_breakdown()
            )
        )

    def _set_combat_metric(self, metric: str):
        """Chip handler: switch the class-board ranking metric."""
        self._cp_metric = metric
        for m, b in self.cp_metric_chips.items():
            b.setChecked(m == metric)
        if hasattr(self, "cp_header_row"):
            self.cp_header_row.set_metric(metric)
        self._render_combat_breakdown()

    def _set_combat_sort_mode(self, mode: str):
        """Switch player sorting mode between overall DPS rank vs class grouping."""
        self._cp_sort_mode = mode
        if hasattr(self, "cp_sort_dps_btn"):
            self.cp_sort_dps_btn.setChecked(mode == "dps")
        if hasattr(self, "cp_sort_class_btn"):
            self.cp_sort_class_btn.setChecked(mode == "class")
        self._render_combat_breakdown()

    def _toggle_class_filter(self, cls: str):
        """Chip handler: toggle one class in the filter set (empty = all)."""
        f = self._cp_class_filter
        if cls in f:
            f.discard(cls)
        else:
            f.add(cls)
        for c, b in self.cp_class_chips.items():
            b.setChecked(c in f)
        self._render_combat_breakdown()

    def _ensure_class_column(self, cls: str) -> QtWidgets.QVBoxLayout:
        """Get-or-create the column frame for one class."""
        col_lay = self._cp_col_lays.get(cls)
        if col_lay is not None:
            frame = self._cp_col_frames.get(cls)
            if frame is not None and frame.isHidden():
                frame.show()
            return col_lay
        frame = QtWidgets.QFrame()
        frame.setObjectName("ClassColumn")
        frame.setStyleSheet(
            f"QFrame#ClassColumn {{ background: {theme.SURFACE}; "
            f"border: 1px solid {theme.BORDER}; border-radius: 6px; }}")
        v = QtWidgets.QVBoxLayout(frame)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        color = theme.class_color(cls)

        hdr_w = QtWidgets.QFrame()
        hdr_w.setStyleSheet(
            f"background: {theme.PANEL}; border-top: 3px solid {color}; "
            f"border-bottom: 1px solid {theme.BORDER}; border-top-left-radius: 5px; border-top-right-radius: 5px;"
        )
        h_box = QtWidgets.QHBoxLayout(hdr_w)
        h_box.setContentsMargins(8, 6, 8, 6)

        title_lbl = QtWidgets.QLabel(f"{cls.upper()}S")
        title_lbl.setStyleSheet(
            f"font-size: 10.5px; font-weight: 800; letter-spacing: 0.5px; color: {color}; background: transparent;")
        h_box.addWidget(title_lbl)
        h_box.addStretch(1)

        stats_lbl = QtWidgets.QLabel("-- DPS • --%")
        stats_lbl.setStyleSheet(
            f"font-size: 9px; font-weight: 600; color: {theme.DIM}; font-family: ui-monospace, monospace; background: transparent;")
        h_box.addWidget(stats_lbl)
        v.addWidget(hdr_w)

        body = QtWidgets.QWidget()
        bl = QtWidgets.QVBoxLayout(body)
        bl.setContentsMargins(4, 4, 4, 4)
        bl.setSpacing(6)
        bl.addStretch(1)
        v.addWidget(body, 1)
        self.cp_board_lay.addWidget(frame, 1)
        self._cp_col_lays[cls] = bl
        self._cp_col_frames[cls] = frame
        self._cp_col_headers[cls] = (title_lbl, stats_lbl)
        return bl

    def _prune_class_columns(self, keep: set[str]):
        """Hide class columns whose players are not active in the current view."""
        for cls, frame in self._cp_col_frames.items():
            if not _cp_card_alive(frame):
                continue
            if cls in keep:
                if frame.isHidden():
                    frame.show()
            else:
                if not frame.isHidden():
                    frame.hide()

    def _render_combat_breakdown(self):
        metric = getattr(self, "_cp_metric", "damage")
        is_compare = (metric == "compare")
        is_fights = (metric == "fights") or (getattr(self, "_cp_segment", "auto") == "past_fights")

        if hasattr(self, "cp_board_container"):
            self.cp_board_container.setVisible(not (is_compare or is_fights))
        if hasattr(self, "_cp_rail_container"):
            self._cp_rail_container.setVisible(not (is_compare or is_fights))
        if hasattr(self, "cp_full_compare_container"):
            self.cp_full_compare_container.setVisible(is_compare)
        scroll = getattr(self, "_cp_past_scroll", None)
        if scroll is not None:
            scroll.setVisible(is_fights)

        if is_fights:
            if hasattr(self, "cp_plotter") and hasattr(self.cp_plotter, "set_compare_players"):
                self.cp_plotter.set_compare_players([])
            if hasattr(self, "cp_plotter_info"):
                self.cp_plotter_info.setText("📈 Encounter DPS Timeline")
            self._render_past_fights()
            return
        elif is_compare:
            session = self._get_displayed_session()
            if session:
                self._refresh_rail_compare(session, max(1.0, session.duration), "damage")
            return

        if hasattr(self, "cp_plotter") and hasattr(self.cp_plotter, "set_compare_players"):
            self.cp_plotter.set_compare_players([])
        if hasattr(self, "cp_plotter_info"):
            self.cp_plotter_info.setText("📈 Encounter DPS Timeline")

        session = self._get_displayed_session()
        if not session:
            return

        metric = getattr(self, "_cp_metric", "damage")
        if hasattr(self, "cp_header_row"):
            self.cp_header_row.set_metric(metric)

        duration = max(1.0, session.duration)
        solo_view = self._cp_solo_filter_on()
        me_p = next((p for p in session.players.values() if p.is_me), None) if solo_view else None

        players = session.ranked_players(view=metric, pin_me=True)
        if solo_view:
            players = [p for p in players if p.is_me]
            if not players and me_p is not None:
                players = [me_p]

        sort_mode = getattr(self, "_cp_sort_mode", "dps")
        if hasattr(self, "cp_header_row"):
            self.cp_header_row.set_active_sort(sort_mode)

        if not solo_view:
            if sort_mode == "class":
                by_cls: dict[str, list] = {}
                for p in players:
                    raw = (p.hero_class or "").strip()
                    cls = raw.capitalize() if raw.lower() in theme.HERO else "Other"
                    by_cls.setdefault(cls, []).append(p)

                def _cls_total(cls_nm):
                    return max((p.heals if metric == "healing" else p.total_damage for p in by_cls[cls_nm]), default=0.0)

                sorted_classes = sorted(by_cls.keys(), key=_cls_total, reverse=True)
                ordered_p = []
                for cls_nm in sorted_classes:
                    p_sorted = sorted(
                        by_cls[cls_nm],
                        key=lambda p: p.heals if metric == "healing" else p.total_damage,
                        reverse=True
                    )
                    ordered_p.extend(p_sorted)
                players = ordered_p

            elif sort_mode in ("dps", "total", "share"):
                def _val_func(p):
                    if metric == "healing":
                        return p.heals
                    elif metric in ("taken", "damage_taken"):
                        return getattr(p, "total_damage_taken", p.damage_taken)
                    return p.total_damage
                players = sorted(players, key=_val_func, reverse=True)

            elif sort_mode == "crit":
                def _crit_pct(p):
                    crits = sum(s.crit_count for s in p.skills.values())
                    hits = sum(s.hit_count for s in p.skills.values())
                    return (crits / hits * 100.0) if hits > 0 else 0.0
                players = sorted(players, key=_crit_pct, reverse=True)

            elif sort_mode == "name":
                players = sorted(players, key=lambda p: p.name.lower())

            elif sort_mode == "rank":
                players = sorted(players, key=lambda p: session.get_rank(p, view=metric))

        # Always pin the local character at the top (row 0) regardless of sort
        if not solo_view:
            me_p_list = [p for p in players if p.is_me]
            oth_p_list = [p for p in players if not p.is_me]
            players = me_p_list + oth_p_list

        if metric == "skills":
            def _top_skill_val(p):
                sk = _ranked_skill_total(p)
                return sk[0].total if sk else 0.0
            active = [p for p in session.players.values()
                      if p.is_me or p.total_damage > 0 or p.heals > 0
                      or getattr(p, "total_damage_taken",
                                 p.damage_taken + p.damage_taken_est) > 0]
            natural = sorted(active, key=_top_skill_val, reverse=True)
            me_list = [p for p in natural if p.is_me]
            others = [p for p in natural if not p.is_me]
            players = (me_list + others) if me_list else natural
            if solo_view:
                players = [p for p in players if p.is_me]

        cls_filter = getattr(self, "_cp_class_filter", set())
        by_class: dict[str, list] = {}
        for p in players:
            raw = (p.hero_class or "").strip()
            cls = raw.capitalize() if raw.lower() in theme.HERO else "Other"
            if cls_filter and cls not in cls_filter:
                continue
            by_class.setdefault(cls, []).append(p)

        if not by_class:
            if hasattr(self, "cp_empty_lbl"):
                dm = getattr(self.model, "damage", None) if self.model else None
                hint = empty_hint(
                    dm, "No combat data recorded yet. Attack enemies or dummy in-game.")
                if self.cp_empty_lbl.parent() is not getattr(self, "cp_class_board", None):
                    self.cp_empty_lbl.setParent(getattr(self, "cp_class_board", None))
                if self.cp_empty_lbl.text() != hint:
                    self.cp_empty_lbl.setText(hint)
                self.cp_empty_lbl.show()
            self._hide_all_column_cards()
            self._prune_class_columns(set())
            return

        if hasattr(self, "cp_empty_lbl"):
            self.cp_empty_lbl.hide()

        group_dmg = session.group_damage
        group_heals = session.group_heals
        group_taken = getattr(session, "group_taken", 0.0) or sum(p.total_damage_taken for p in session.players.values())
        if solo_view and me_p is not None:
            group_dmg = me_p.total_damage
            group_heals = me_p.heals
            group_taken = me_p.total_damage_taken

        active_names: set[str] = set()
        visible_idx = 0
        for p in players:
            raw = (p.hero_class or "").strip()
            cls = raw.capitalize() if raw.lower() in theme.HERO else "Other"
            if cls_filter and cls not in cls_filter:
                continue

            cache_key = f"meter\x1f{p.name}"
            active_names.add(cache_key)
            card = self._cp_player_cards.get(cache_key)
            if card is not None and not _cp_card_alive(card):
                del self._cp_player_cards[cache_key]
                card = None
            if card is None:
                card = _MeterTableRow(p.name, is_me=p.is_me)
                card.clicked.connect(self._on_player_card_clicked)
                card.compare_clicked.connect(self._on_player_compare_clicked)
                self._cp_player_cards[cache_key] = card
                self.cp_board_lay.insertWidget(visible_idx, card)
            if card.isHidden():
                card.show()

            true_rank = session.get_rank(p, view=metric)
            card.update_stats(true_rank - 1, p, duration,
                              group_dmg, group_heals, group_taken=group_taken, tab=metric,
                              class_rank=visible_idx + 1, class_total=len(players))
            card.set_selected(getattr(self, "_cp_selected_player", "") == p.name)

            cur = self.cp_board_lay.indexOf(card)
            if cur != visible_idx:
                self.cp_board_lay.insertWidget(visible_idx, card)
            visible_idx += 1

        for key, card in list(self._cp_player_cards.items()):
            if key not in active_names:
                try:
                    if not card.isHidden():
                        card.hide()
                except RuntimeError:
                    self._cp_player_cards.pop(key, None)

        self._update_rail_content(session, duration)

    def _hide_all_column_cards(self):
        """Hide every breakdown card (empty session / reset)."""
        for name, card in list(getattr(self, "_cp_player_cards", {}).items()):
            try:
                if not card.isHidden():
                    card.hide()
            except RuntimeError:
                del self._cp_player_cards[name]

    def _set_rail_tab(self, tab: str) -> None:
        self._cp_rail_tab = tab
        if hasattr(self, "cp_rail_btn_skills"):
            self.cp_rail_btn_skills.setChecked(tab == "skills")
        if hasattr(self, "cp_rail_btn_compare"):
            self.cp_rail_btn_compare.setChecked(tab == "compare")
        if hasattr(self, "cp_rail_btn_encounter"):
            self.cp_rail_btn_encounter.setChecked(tab == "encounter")

        if hasattr(self, "cp_rail_panel_skills"):
            self.cp_rail_panel_skills.setVisible(tab == "skills")
        if hasattr(self, "cp_rail_panel_compare"):
            self.cp_rail_panel_compare.setVisible(tab == "compare")
        if hasattr(self, "cp_rail_panel_encounter"):
            self.cp_rail_panel_encounter.setVisible(tab == "encounter")

        session = self._get_displayed_session()
        if session:
            self._update_rail_content(session, max(1.0, session.duration))

    def _on_player_card_clicked(self, name: str) -> None:
        self._cp_selected_player = name
        for card in getattr(self, "_cp_player_cards", {}).values():
            if _cp_card_alive(card):
                card.set_selected(card.name == name)
        if getattr(self, "_cp_rail_tab", "skills") == "compare":
            if getattr(self, "_cp_compare_a", "") != name:
                self._cp_compare_b = name
        else:
            self._set_rail_tab("skills")
        session = self._get_displayed_session()
        if session:
            self._update_rail_content(session, max(1.0, session.duration))

    def _on_player_compare_clicked(self, name: str) -> None:
        session = self._get_displayed_session()
        if not session:
            return

        ranked = session.ranked_players(view="damage")
        if not ranked:
            return

        me = next((p for p in session.players.values() if p.is_me), None)
        top1 = ranked[0]
        top2 = ranked[1] if len(ranked) > 1 else top1
        p_sel = session.players.get(name)

        if p_sel and p_sel.is_me:
            # You selected yourself! Compare YOU (A) vs #1 Rank (B), or #2 if you are #1
            self._cp_compare_a = p_sel.name
            if top1.name != p_sel.name:
                self._cp_compare_b = top1.name
            else:
                self._cp_compare_b = top2.name
        else:
            # You selected someone else! Compare YOU (A) vs Selected Player (B)
            if me and me.name != name:
                self._cp_compare_a = me.name
                self._cp_compare_b = name
            else:
                self._cp_compare_a = top1.name if top1.name != name else top2.name
                self._cp_compare_b = name

        # Switch to the primary full-width ⚖ Compare tab!
        self._set_combat_metric("compare")

    def _on_hero_compare_clicked(self) -> None:
        sel = getattr(self, "_cp_selected_player", "")
        if sel:
            self._on_player_compare_clicked(sel)

    def _copy_selected_player_parse(self) -> None:
        session = self._get_displayed_session()
        if not session:
            return
        p_name = getattr(self, "_cp_selected_player", "")
        p = session.players.get(p_name)
        if not p:
            return
        duration = max(1.0, session.duration)
        top = _ranked_skill_total(p)
        top_name = top[0].name if top else "None"
        share = p.share_pct(session.group_damage)
        text = f"{p.name} ({p.hero_class or 'Hero'}): {p.dps(duration):,.1f} DPS | {p.total_damage:,.0f} Total ({share:.1f}%) | Top: {top_name}"
        cb = QtWidgets.QApplication.clipboard()
        if cb:
            cb.setText(text)

    def _get_cb_val(self, cb) -> str:
        if not cb:
            return ""
        data = cb.currentData()
        if data and isinstance(data, str) and data not in ("(None)", "--", ""):
            return data
        txt = cb.currentText().strip()
        if not txt or txt in ("(None)", "--"):
            return ""
        return txt.split(" (")[0].strip()

    def _on_compare_selection_changed(self) -> None:
        if not hasattr(self, "cp_compare_combo_a") or not hasattr(self, "cp_compare_combo_b"):
            return
        self._cp_compare_a = self._get_cb_val(self.cp_compare_combo_a)
        self._cp_compare_b = self._get_cb_val(self.cp_compare_combo_b)
        self._cp_compare_c = self._get_cb_val(getattr(self, "cp_compare_combo_c", None))
        self._cp_compare_d = self._get_cb_val(getattr(self, "cp_compare_combo_d", None))
        self._cp_compare_e = self._get_cb_val(getattr(self, "cp_compare_combo_e", None))
        session = self._get_displayed_session()
        if session:
            self._refresh_rail_compare(session, max(1.0, session.duration), getattr(self, "_cp_metric", "damage"))

    def _update_rail_content(self, session: CombatSession, duration: float) -> None:
        if not session or not session.players:
            return
        if not getattr(self, "_cp_selected_player", "") or self._cp_selected_player not in session.players:
            me = next((p for p in session.players.values() if p.is_me), None)
            self._cp_selected_player = me.name if me else next(iter(session.players.keys()))

        tab = getattr(self, "_cp_rail_tab", "skills")
        metric = getattr(self, "_cp_metric", "damage")
        if tab == "skills":
            self._refresh_rail_skills(session, duration, metric)
        elif tab == "compare":
            self._refresh_rail_compare(session, duration, metric)

    def _refresh_rail_skills(self, session: CombatSession, duration: float, metric: str) -> None:
        p = session.players.get(getattr(self, "_cp_selected_player", ""))
        if not p:
            p = next((x for x in session.players.values() if x.is_me), None) or next(iter(session.players.values()))
            self._cp_selected_player = p.name

        rank = session.get_rank(p, view=metric)
        col = theme.class_color(p.hero_class)
        clean_name = p.name.replace(" (You)", "").replace(" (YOU)", "")
        self.cp_hero_name.setText(clean_name)
        self.cp_hero_name.setStyleSheet(f"font-size: 13.5px; font-weight: 800; color: {theme.ACCENT_LIGHT if p.is_me else theme.TEXT};")

        self.cp_hero_badge.setText(f"#{rank} · {p.hero_class or 'Hero'}")
        self.cp_hero_badge.setStyleSheet(
            f"font-size: 9px; font-weight: 800; color: {col}; "
            f"background: {theme.PANEL_LOW}; border: 1px solid {col}; border-radius: 3px; padding: 2px 6px;")

        if metric == "healing":
            rate_val = p.hps(duration)
            rate_txt = f"{rate_val:,.0f}/s" if rate_val < 100_000 else f"{rate_val/1000:.1f}k/s"
            total_val = p.heals
            tot_txt = f"{total_val:,.0f}" if total_val < 10_000_000 else f"{total_val/1_000_000:.2f}M"
            share = p.heal_share_pct(session.group_heals)
            if hasattr(self, "cp_hero_dps_cap"):
                self.cp_hero_dps_cap.setText("HPS")
                self.cp_hero_tot_cap.setText("TOTAL HEAL")
                self.cp_hero_shr_cap.setText("HEAL SHARE")
            self.cp_hero_dps.setStyleSheet(f"font-size: 13.5px; font-weight: 800; color: {theme.GOOD}; font-family: monospace;")
        elif metric in ("taken", "damage_taken"):
            group_taken = getattr(session, "group_taken", 0.0) or sum(pl.total_damage_taken for pl in session.players.values())
            rate_val = p.total_damage_taken / max(1.0, duration)
            rate_txt = f"{rate_val:,.0f}/s" if rate_val < 100_000 else f"{rate_val/1000:.1f}k/s"
            total_val = p.total_damage_taken
            tot_txt = f"{total_val:,.0f}" if total_val < 10_000_000 else f"{total_val/1_000_000:.2f}M"
            share = (p.total_damage_taken / max(1.0, group_taken)) * 100.0 if group_taken > 0 else 0.0
            if hasattr(self, "cp_hero_dps_cap"):
                self.cp_hero_dps_cap.setText("DTPS")
                self.cp_hero_tot_cap.setText("TOTAL TAKEN")
                self.cp_hero_shr_cap.setText("TAKEN SHARE")
            self.cp_hero_dps.setStyleSheet(f"font-size: 13.5px; font-weight: 800; color: {theme.ORANGE}; font-family: monospace;")
        else:
            rate_val = p.dps(duration)
            rate_txt = f"{rate_val:,.0f}/s" if rate_val < 100_000 else f"{rate_val/1000:.1f}k/s"
            total_val = p.total_damage
            tot_txt = f"{total_val:,.0f}" if total_val < 10_000_000 else f"{total_val/1_000_000:.2f}M"
            share = p.share_pct(session.group_damage)
            if hasattr(self, "cp_hero_dps_cap"):
                self.cp_hero_dps_cap.setText("DPS")
                self.cp_hero_tot_cap.setText("TOTAL DMG")
                self.cp_hero_shr_cap.setText("RAID SHARE")
            self.cp_hero_dps.setStyleSheet(f"font-size: 13.5px; font-weight: 800; color: {theme.ACCENT_LIGHT}; font-family: monospace;")

        self.cp_hero_dps.setText(rate_txt)
        self.cp_hero_tot.setText(tot_txt)
        self.cp_hero_shr.setText(f"{share:.1f}%")

        total_crits = sum(s.crit_count for s in p.skills.values())
        total_hits = sum(s.hit_count for s in p.skills.values())
        crit_pct = (total_crits / total_hits * 100.0) if total_hits > 0 else 0.0
        self.cp_hero_crt.setText(f"{crit_pct:.1f}%")

        weaps = weapons_used(session, player=p) if session and p else []
        if hasattr(self, "cp_weapons_used_lbl"):
            if weaps:
                self.cp_weapons_used_lbl.setText(
                    "Weapons used: " + " · ".join(
                        f"{_WEAPON_FAMILY_GLYPHS.get(w['type'], '')} {w['name']}"
                        for w in weaps))
                self.cp_weapons_used_lbl.setVisible(True)
            else:
                self.cp_weapons_used_lbl.setText("")
                self.cp_weapons_used_lbl.setVisible(False)

        if metric == "healing":
            channel_skills = p.ranked_heal_skills()
            skill_total = p.heals
            mode = "healing"
        elif metric == "skills":
            channel_skills = _ranked_skill_total(p)
            skill_total = max(p.total_damage, p.heals)
            mode = "both"
        else:
            channel_skills = p.ranked_skills()
            skill_total = p.total_damage
            mode = "damage"

        # Explicitly sort skills from highest total to lowest
        def _sk_total(sp):
            return getattr(sp, "heals" if metric == "healing" else "total", getattr(sp, "damage", 0.0))
        channel_skills = sorted(channel_skills, key=_sk_total, reverse=True)

        if hasattr(self, "cp_hero_skill_count"):
            self.cp_hero_skill_count.setText(f"{len(channel_skills)} skills")
        if hasattr(self, "cp_stacked_bar"):
            self.cp_stacked_bar.set_skills(channel_skills, skill_total)
        active_ids = set()
        for idx, sp in enumerate(channel_skills):
            active_ids.add(sp.skill_id)
            s_pct = sp.share_pct(skill_total) if metric != "healing" else sp.heal_share_pct(skill_total)
            row = self._cp_rail_skill_widgets.get(sp.skill_id)
            if row is None:
                row = SkillRow(density="detailed", parent=self.cp_skills_container)
                row.set_skill(sp.skill_id, sp.name)
                self._cp_rail_skill_widgets[sp.skill_id] = row
                self.cp_skills_rows_lay.insertWidget(idx, row)
            else:
                cur_pos = self.cp_skills_rows_lay.indexOf(row)
                if cur_pos != idx:
                    self.cp_skills_rows_lay.insertWidget(idx, row)
            if row.isHidden():
                row.show()
            row.update(sp, share_pct=s_pct, mode=mode)
            row.set_bar_color(theme.skill_color(sp.skill_id))
            # Clicking a skill row opens its info popup (effect + weapon)
            row.set_on_skill_click(
                lambda _sid, _r, sp=sp: self._open_skill_popup(sp, _r))

        for sid, row in self._cp_rail_skill_widgets.items():
            if sid not in active_ids:
                row.hide()

        if p.is_me:
            top_ranked = session.ranked_players(view="damage") if session else []
            top1 = top_ranked[0] if top_ranked else None
            if top1 and top1.name != p.name:
                self.cp_hero_compare_btn.setText(f"⚖ Compare with #1 ({top1.name})")
            else:
                top2 = top_ranked[1] if len(top_ranked) > 1 else None
                top2_nm = top2.name if top2 else "#2"
                self.cp_hero_compare_btn.setText(f"⚖ Compare with #2 ({top2_nm})")
        else:
            self.cp_hero_compare_btn.setText(f"⚖ Compare with You (vs {p.name})")

    def _refresh_rail_compare(self, session: CombatSession, duration: float, metric: str) -> None:
        refresh_rail_compare(self, session, duration, metric)
