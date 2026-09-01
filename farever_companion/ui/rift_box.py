"""Sidebar next-rift predictor card and audio chime scheduler."""
from __future__ import annotations

import time
from PySide6 import QtCore, QtWidgets

from . import theme
from ..data import icons
from ..sound import play_sound


class ClickFrame(QtWidgets.QFrame):
    """A QFrame that emits `clicked` on left-click (sidebar rift card)."""
    clicked = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._selected = False

    def setSelected(self, on: bool) -> None:
        self._selected = on

    def mousePressEvent(self, e):
        if e.button() == QtCore.Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(e)


class RiftSidebarMixin:
    """Mixin providing the sidebar Rift card, real-time countdown, and chime alarms."""

    def _build_rift_box(self):
        """Compact next-rift predictor pinned above the sidebar footer.

        Pure clock + LCG (no memory reads); clicking it opens Settings · Rift.
        The whole card is state-tinted (red live / green warning / cyan idle).
        """
        from ..core.rift_tracker import rift_summary
        self._rift_summary = rift_summary
        box = ClickFrame()
        box.setObjectName("Card")
        self._rift_box = box
        box.setCursor(QtCore.Qt.PointingHandCursor)
        box.setToolTip("Rift schedule — click to open Settings · Rift")
        v = QtWidgets.QVBoxLayout(box)
        v.setContentsMargins(10, 8, 10, 8)
        v.setSpacing(3)
        head = QtWidgets.QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(6)
        self._rift_icon = QtWidgets.QLabel()
        self._rift_icon.setFixedSize(15, 15)
        self._rift_icon.setPixmap(icons.marker("rift", 15, accent=theme.ACCENT))
        head.addWidget(self._rift_icon)
        head_lbl = QtWidgets.QLabel("RIFT")
        head_lbl.setStyleSheet(
            f"color:{theme.ACCENT};font-size:11px;font-weight:700;"
            "letter-spacing:2px;background:transparent;border:none;")
        head.addWidget(head_lbl)
        head.addStretch(1)
        v.addLayout(head)
        self._rift_side_zone = QtWidgets.QLabel("--")
        self._rift_side_zone.setObjectName("Mono")
        self._rift_side_zone.setStyleSheet(
            "font-size:14px;font-weight:700;background:transparent;border:none;")
        v.addWidget(self._rift_side_zone)
        self._rift_side_status = QtWidgets.QLabel("")
        self._rift_side_status.setObjectName("Mono")
        self._rift_side_status.setStyleSheet(
            "font-size:12px;font-weight:600;background:transparent;border:none;")
        v.addWidget(self._rift_side_status)
        self._rift_side_follow = QtWidgets.QLabel("")
        self._rift_side_follow.setObjectName("Mono")
        self._rift_side_follow.setStyleSheet(
            "font-size:11px;background:transparent;border:none;")
        v.addWidget(self._rift_side_follow)
        box.clicked.connect(lambda: self._select_nav("settings:rift"))
        self._update_sidebar_rift()
        self._rift_side_timer = QtCore.QTimer(box)
        self._rift_side_timer.setInterval(1000)
        self._rift_side_timer.timeout.connect(self._update_sidebar_rift)
        self._rift_side_timer.start()
        return box

    def _update_sidebar_rift(self):
        r = self._rift_summary()
        self._check_rift_ding(r)
        color = r["color"]
        self._rift_side_zone.setText(r["zone"])
        self._rift_side_zone.setStyleSheet(
            f"color:{theme.GOLD};font-size:14px;font-weight:700;"
            "background:transparent;border:none;")

        GREEN = "#66BB6A"
        secs = r.get("secs_until", 10**9)
        time_green = secs < 900 and r["state"] not in ("ACTIVE", "CLOSING")

        if r["state"] == "ACTIVE":
            prefix, tstr = "LIVE NOW", ""
        elif r["state"] == "CLOSING":
            prefix, tstr = "Closing ", r["formatted"]
        elif r["state"] == "WARNING":
            prefix, tstr = "Starting ", r["formatted"]
        else:
            prefix, tstr = "In ", r["formatted"]

        tcolor = GREEN if time_green else color
        html = f'<font color="#bdc8d1">{prefix}</font>'
        if tstr:
            html += f'<font color="{tcolor}">{tstr}</font>'
        self._rift_side_status.setTextFormat(QtCore.Qt.RichText)
        self._rift_side_status.setText(html)
        self._rift_side_status.setStyleSheet(
            "font-size:12px;font-weight:600;"
            "background:transparent;border:none;")
        self._rift_side_follow.setText(f"Then: {r['follow_zone']} · {r['follow_in']}")
        self._rift_side_follow.setStyleSheet(
            "color:#87929a;font-size:11px;background:transparent;border:none;")
        self._rift_icon.setPixmap(icons.marker("rift", 15, accent=color))

        cur = (self._current_page() or "").lower()
        _stabs = getattr(self, "_settings_tabs", None)
        on_rift = cur in ("settings:rift", "rift") or (
            cur.startswith("settings") and _stabs is not None
            and self._widget_alive(_stabs)
            and (_stabs.currentText() or "").lower() == "rift"
        )
        self._rift_box._selected = on_rift
        if on_rift:
            border = theme.ACCENT
            bw = 2
        else:
            border = theme.BORDER
            bw = 1
        self._rift_box.setStyleSheet(
            f"QFrame#Card {{ background: {theme.PANEL}; border: {bw}px solid {border}; }}")

    def _refresh_rift_box_selected(self) -> None:
        """Force an immediate sync of the sidebar RIFT box highlight."""
        if hasattr(self, "_rift_box"):
            self._update_sidebar_rift()

    def _check_rift_ding(self, r: dict) -> None:
        """Play one chime per selected lead time (multi-select) before a rift."""
        if getattr(self.s, "show_rift_timer", "Always") == "Off":
            return
        raw = getattr(self.s, "rift_ding", ["15", "10", "5", "1"])
        if isinstance(raw, str):
            if raw.lower() == "off":
                return
            if raw.lower() == "all":
                thresholds = [15, 10, 5, 1, 0]
            else:
                try:
                    thresholds = [int(raw.replace("m", ""))]
                except ValueError:
                    thresholds = [15, 10, 5, 1, 0]
        else:
            try:
                thresholds = sorted({int(str(x).replace("m", "")) for x in raw})
            except Exception:
                thresholds = [15, 10, 5, 1, 0]
        if not thresholds:
            return
        secs = r.get("secs_until", 10**9)
        state = r["state"]
        if state != "WARNING":
            return

        now = time.time()
        target = (int(now // 3600) + 1) * 3600
        fired = getattr(self, "_rift_dinged", None)
        if not isinstance(fired, dict):
            fired = {}
        self._rift_dinged = fired

        for m in thresholds:
            due_secs = 1 if m == 0 else m * 60
            if secs > due_secs or fired.get(m) == target:
                continue
            if now - (target - due_secs) <= 2.0:
                play_sound(getattr(self.s, "rift_sound", "ding"))
                if hasattr(self, "log"):
                    try:
                        self.log(f"Rift chime: {'Live' if m == 0 else f'{m}m'} warning")
                    except Exception:
                        pass
            fired[m] = target

        cut = now - 7200
        for m in [m for m, t in fired.items() if t < cut]:
            fired.pop(m, None)
