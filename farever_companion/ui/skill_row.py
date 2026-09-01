"""Shared per-skill row widget for the DPS meters.

Both DpsOverlay (compact) and CombatPage (detailed) build the same anatomy:
an icon, a name, a bar, and per-skill stats with a tooltip. This widget
encapsulates that layout once and lets callers pick a density.

Usage::

    row = SkillRow(density="detailed", parent=self)
    layout.addWidget(row)
    row.update(skill_parse, share_pct=0.42, mode="damage")
"""

from __future__ import annotations

import re
from PySide6 import QtCore, QtGui, QtWidgets

from . import theme
from ..data import icons
from .components import ElideLabel


def _skill_icon(skill_id: str, size: int = 18, fallback_color: str | None = None) -> QtWidgets.QLabel:
    """Icon label for a skill: resolves pixmap or falls back to a zap glyph."""
    ico = QtWidgets.QLabel()
    ico.setFixedSize(size, size)
    clean_id = re.sub(r"\s*\([Pp]et\)$", "", skill_id or "").strip()
    pm = None
    if clean_id:
        cands = [clean_id, clean_id.lower(), clean_id.replace(" ", "_"), clean_id.replace("_", " ")]
        for cand in cands:
            if icons.has_icon("skills", cand):
                pm = icons.pixmap("skills", cand, size)
                break
            if icons.has_icon("items", cand):
                pm = icons.pixmap("items", cand, size)
                break
    if pm and not pm.isNull():
        ico.setPixmap(pm)
    else:
        ico.setPixmap(icons.ui_icon("zap", fallback_color or theme.ACCENT, max(8, size - 2)))
    return ico


class SkillRow(QtWidgets.QWidget):
    """One per-skill row: icon · name · value · bar · (optional hits/crit/avg).

    ``density`` controls which columns appear:

    * ``"compact"`` — icon, elided name, single stat line, bar, pct.
    * ``"detailed"`` — icon, elided name, total, bar, hits, crit, avg.
    """

    def __init__(self, density: str = "detailed", parent=None):
        super().__init__(parent)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding,
                            QtWidgets.QSizePolicy.Preferred)
        self._density = density
        self._layout = QtWidgets.QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(4 if density == "compact" else 6)

        # Columns that exist in both densities.
        self._icon_lbl = _skill_icon("", size=(14 if density == "compact" else 20))
        self._layout.addWidget(self._icon_lbl)

        # Name is the flexible column.
        max_name = (118 if density == "compact" else 240)
        self._name_lbl: QtWidgets.QLabel = ElideLabel("", max_name)
        self._name_lbl.setStyleSheet(
            f"font-size: {10 if density == 'compact' else 12}px;"
            f"font-weight: 600; color: {theme.TEXT};"
        )
        self._name_lbl.setSizePolicy(QtWidgets.QSizePolicy.Ignored,
                                      QtWidgets.QSizePolicy.Preferred)
        self._layout.addWidget(self._name_lbl, 1)

        # The click callback (detailed density only): when wired, clicking
        # the row opens the skill's info popup (skill effect + the weapon
        # it comes from). Overlay rows never wire it.
        self._on_skill_click = None
        self._skill_id = ""

        # The primary value label.
        self._total_lbl = QtWidgets.QLabel("")
        self._total_lbl.setObjectName("SkillTotal")
        self._total_lbl.setStyleSheet(
            f"font-size: {10 if density == 'compact' else 12}px;"
            f"font-weight: {'600' if density == 'compact' else '800'};"
            f"color: {theme.TEXT}; font-family: monospace;"
        )
        self._total_lbl.setAlignment(
            QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        self._total_lbl.setMinimumWidth(
            (54 if density == "compact" else 62))
        self._layout.addWidget(self._total_lbl)

        # Progress bar.
        self._bar = QtWidgets.QProgressBar()
        self._bar.setObjectName("SkillBar")
        self._bar.setFixedWidth((46 if density == "compact" else 60))
        self._bar.setFixedHeight((3 if density == "compact" else 5))
        self._bar.setTextVisible(False)
        self._bar.setRange(0, 100)
        self._layout.addWidget(self._bar)

        # Optional columns, only in detailed mode.
        self._hits_lbl = None
        self._crit_lbl = None
        self._avg_lbl = None
        if density == "detailed":
            self._hits_lbl = QtWidgets.QLabel("")
            self._hits_lbl.setObjectName("SkillHits")
            self._hits_lbl.setFixedWidth(34)
            self._hits_lbl.setAlignment(
                QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            self._hits_lbl.setStyleSheet(
                f"font-size: 11px; color: {theme.DIM}; font-family: monospace;"
            )
            self._layout.addWidget(self._hits_lbl)

            self._crit_lbl = QtWidgets.QLabel("")
            self._crit_lbl.setObjectName("SkillCrit")
            self._crit_lbl.setFixedWidth(38)
            self._crit_lbl.setAlignment(
                QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            self._crit_lbl.setStyleSheet(
                f"font-size: 11px; color: {theme.ORANGE}; font-family: monospace;"
            )
            self._layout.addWidget(self._crit_lbl)

            self._avg_lbl = QtWidgets.QLabel("")
            self._avg_lbl.setObjectName("SkillAvg")
            self._avg_lbl.setFixedWidth(104)
            self._avg_lbl.setAlignment(
                QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            self._avg_lbl.setStyleSheet(
                f"font-size: 11px; color: {theme.DIM}; font-family: monospace;"
            )
            self._layout.addWidget(self._avg_lbl)

        # Percentage chip (only in compact mode).
        if density == "compact":
            self._pct_lbl = QtWidgets.QLabel("")
            self._pct_lbl.setObjectName("SkillPct")
            self._pct_lbl.setFixedWidth(30)
            self._pct_lbl.setAlignment(
                QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            self._pct_lbl.setStyleSheet(
                f"font-size: 10px; color: {theme.DIM}; font-family: monospace;"
            )
            self._layout.addWidget(self._pct_lbl)
        else:
            self._pct_lbl = None

    # --- public API -------------------------------------------------------#

    def set_skill(self, skill_id: str, name: str) -> None:
        """Set the icon + name (call once or when the skill identity changes)."""
        self._skill_id = skill_id or ""
        new_icon = _skill_icon(skill_id,
                               size=(14 if self._density == "compact" else 20))
        self._layout.replaceWidget(self._icon_lbl, new_icon)
        self._icon_lbl.deleteLater()
        self._icon_lbl = new_icon
        if getattr(self, "_on_skill_click", None) is not None and self._density == "detailed":
            self._icon_lbl.setCursor(QtCore.Qt.PointingHandCursor)
        self._name_lbl.set_full(name)

    def update(self, sp, share_pct: float, mode: str = "damage") -> None:
        """Refresh one row from a SkillParse + its share of the parent total.

        ``mode`` is one of ``"damage"``, ``"healing"``, ``"both"`` and
        controls how the stat line and tooltip are rendered.
        """
        is_heal = (mode == "healing")
        is_both = (mode == "both")

        # Normalize share: ratio for bar (0..1) and pct for text (0..100)
        pct_val = share_pct * 100.0 if share_pct <= 1.0 else share_pct
        ratio_val = share_pct if share_pct <= 1.0 else (share_pct / 100.0)

        if is_heal:
            total = sp.heals
            hits = sp.heal_count
            min_val = sp.min_heal
            max_val = sp.max_heal
            avg_val = sp.heals / max(1, sp.heal_count)
            stat = (f"{sp.heals:,.0f} · {sp.heal_count} heals"
                    if self._density == "compact" else f"{sp.heals:,.0f}")
            tooltip = (
                f"<b>{sp.name}</b> ({sp.skill_id})<br>"
                f"Heals: <b>{sp.heals:,.1f}</b> ({pct_val:.1f}%)<br>"
                f"Casts: <b>{sp.heal_count}</b><br>"
                f"Min: <b>{self._fmt_minmax(min_val)}</b> | "
                f"Avg: <b>{avg_val:,.1f}</b> | "
                f"Max: <b>{self._fmt_minmax(max_val)}</b>"
            )
            # In detailed mode the hits column is heal-counts, crit/avg as appropriate.
            if self._hits_lbl is not None:
                self._hits_lbl.setText(f"{hits}")
                self._crit_lbl.setText("—")
                self._avg_lbl.setText(
                    f"{avg_val:,.0f} [{self._fmt_minmax(min_val)}–"
                    f"{self._fmt_minmax(max_val)}]"
                )
            if self._pct_lbl is not None:
                self._pct_lbl.setText(f"{pct_val:.0f}%")
            self._bar.setValue(int(ratio_val * 100))
            self._total_lbl.setText(stat)  # compact: "6,000 · 4 heals"
            self._bar.setToolTip(self._bar.toolTip())  # no-op keep
            self.setToolTip(tooltip)
            return

        if is_both:
            uses = sp.hit_count + sp.heal_count
            total = sp.total
            if sp.damage > 0 and sp.heals > 0:
                stat = f"D {sp.damage:,.0f} · H {sp.heals:,.0f}"
            else:
                crit_info = f"{sp.crit_pct:.0f}%" if sp.crit_count > 0 else "—"
                min_str = self._fmt_minmax(sp.min_hit)
                max_str = self._fmt_minmax(sp.max_hit)
                stat = f"{sp.total:,.0f}"
                avg_val = sp.avg_hit()
                if self._hits_lbl is not None:
                    self._hits_lbl.setText(f"{uses}")
                    self._crit_lbl.setText(crit_info)
                    self._avg_lbl.setText(f"{avg_val:,.0f} [{min_str}–{max_str}]")
            if self._pct_lbl is not None:
                self._pct_lbl.setText(f"{pct_val:.0f}%")
            if self._hits_lbl is not None and not (sp.damage > 0 and sp.heals > 0):
                pass  # already filled above
            tooltip = (
                f"<b>{sp.name}</b> ({sp.skill_id})<br>"
                f"Combined: <b>{sp.total:,.1f}</b> ({pct_val:.1f}%)<br>"
                f"Damage: <b>{sp.damage:,.1f}</b> ({sp.hit_count} hits) | "
                f"Heals: <b>{sp.heals:,.1f}</b> ({sp.heal_count} casts)"
            )
            self._total_lbl.setText(stat)
            self._bar.setValue(int(ratio_val * 100))
            self.setToolTip(tooltip)
            return

        # --- damage mode ---
        crit_info = f"{sp.crit_pct:.0f}%" if sp.crit_count > 0 else "—"
        min_str = self._fmt_minmax(sp.min_hit)
        max_str = self._fmt_minmax(sp.max_hit)
        stat = f"{sp.damage:,.0f} · {sp.hit_count}" if self._density == "compact" else f"{sp.damage:,.0f}"
        if self._hits_lbl is not None:
            self._hits_lbl.setText(f"{sp.hit_count}")
            self._crit_lbl.setText(crit_info)
            self._avg_lbl.setText(
                f"{sp.avg_hit():,.0f} [{min_str}–{max_str}]"
            )
        if self._pct_lbl is not None:
            self._pct_lbl.setText(f"{pct_val:.0f}%")
        tooltip = (
            f"<b>{sp.name}</b> ({sp.skill_id})<br>"
            f"Total: <b>{sp.damage:,.1f}</b> ({pct_val:.1f}%)<br>"
            f"Hits: <b>{sp.hit_count}</b> | Crits: <b>{sp.crit_count}</b> "
            f"({sp.crit_pct:.1f}%)<br>"
            f"Min: <b>{min_str}</b> | Avg: <b>{sp.avg_hit():,.1f}</b> | "
            f"Max: <b>{max_str}</b>"
        )
        self._total_lbl.setText(stat)
        self._bar.setValue(int(ratio_val * 100))
        self.setToolTip(tooltip)

    def set_on_skill_click(self, on_click) -> None:
        """Wire (or clear) the skill-click callback for detailed rows.

        When set, clicking the skill column (icon or name) fires
        ``on_click(skill_id, row)`` — opening the skill-info popup.
        Other columns (total, bar, hits, crit, avg) are not clickable.
        """
        self._on_skill_click = on_click
        is_clickable = (on_click is not None and self._density == "detailed")
        cursor = QtCore.Qt.PointingHandCursor if is_clickable else QtCore.Qt.ArrowCursor
        self._icon_lbl.setCursor(cursor)
        self._name_lbl.setCursor(cursor)
        self.setCursor(QtCore.Qt.ArrowCursor)

    def mouseReleaseEvent(self, e: QtGui.QMouseEvent) -> None:
        if (e.button() == QtCore.Qt.LeftButton
                and self._density == "detailed"
                and self._on_skill_click is not None):
            child = self.childAt(e.pos())
            if (child in (self._icon_lbl, self._name_lbl)
                    or e.pos().x() <= self._name_lbl.geometry().right()):
                self._on_skill_click(self._skill_id, self)
                e.accept()
                return
        super().mouseReleaseEvent(e)

    def set_bar_color(self, color: str) -> None:
        if getattr(self, "_current_bar_color", None) == color:
            return
        self._current_bar_color = color
        self._bar.setStyleSheet(
            f"QProgressBar {{ background: {theme.SURFACE}; border: none; "
            f"border-radius: {'1' if self._density == 'compact' else '2'}px; }}"
            f"QProgressBar::chunk {{ background: {color}; "
            f"border-radius: {'1' if self._density == 'compact' else '2'}px; }}"
        )

    def setVisible(self, visible: bool) -> None:
        super().setVisible(visible)

    @staticmethod
    def _fmt_minmax(v: float) -> str:
        return f"{v:,.0f}" if v != float("inf") else "—"
