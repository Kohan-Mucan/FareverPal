"""Base class for the game overlays.

A frameless, translucent, always-on-top window with a custom draggable title
bar and optional Win32 click-through. Out-of-process: it sits over the game in
its own window and can never freeze or crash the game (unlike an in-process
DirectX-hook overlay). Position persists via Settings.geometry.
"""
from __future__ import annotations

import sys

from PySide6 import QtCore, QtGui, QtWidgets

from . import theme


class _TitleBar(QtWidgets.QFrame):
    def __init__(self, title: str, window: "OverlayWindow"):
        super().__init__(window)
        self.setObjectName("TitleBar")
        self._win = window
        # fixed height so the bar never balloons to absorb a window's slack space
        self.setFixedHeight(34)
        self.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Fixed)
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(10, 3, 6, 3)
        lay.setSpacing(4)
        self.title = QtWidgets.QLabel(title)
        self.title.setObjectName("Title")
        lay.addWidget(self.title)
        lay.addStretch(1)
        self.extra = lay   # callers can insert controls before the close btn
        min_btn = QtWidgets.QPushButton("🗕")
        min_btn.setObjectName("Icon")
        min_btn.clicked.connect(window.toggle_minimize)
        lay.addWidget(min_btn)
        close = QtWidgets.QPushButton("✕")
        close.setObjectName("Icon")
        close.clicked.connect(window.close)
        lay.addWidget(close)
        self._drag = None


    def mousePressEvent(self, e):
        if getattr(self._win, "_locked", False):
            return                                   # no drag while locked
        if e.button() == QtCore.Qt.LeftButton:
            self._drag = e.globalPosition().toPoint() - self._win.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._drag is not None and e.buttons() & QtCore.Qt.LeftButton:
            self._win.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, _e):
        self._drag = None
        self._win.persist_geometry()


class OverlayWindow(QtWidgets.QWidget):
    """Subclass and fill `self.content` (a QVBoxLayout)."""

    def __init__(self, title: str, settings=None, geo_key: str = "", parent=None):
        super().__init__(parent)
        self._settings = settings
        self._geo_key = geo_key or title
        self._locked = False
        self._scale = 1.0
        # Per-overlay accent (the "Highlight color" setting). Overlays re-tint
        # their own QSS to it; the control panel keeps the default cyan.
        self._accent = getattr(settings, "hud_accent", None) if settings else None
        self.setWindowFlags(
            QtCore.Qt.FramelessWindowHint
            | QtCore.Qt.WindowStaysOnTopHint
            | QtCore.Qt.Tool                       # no taskbar entry
        )
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        if settings is not None:
            self.setWindowOpacity(max(0.3, min(1.0, settings.opacity)))

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        # 1px border frame -> dark card
        self._frame = QtWidgets.QFrame()
        self._frame.setObjectName("Card")
        root.addWidget(self._frame)
        inner = QtWidgets.QVBoxLayout(self._frame)
        inner.setContentsMargins(1, 1, 1, 1)
        inner.setSpacing(0)
        self.titlebar = _TitleBar(title, self)
        inner.addWidget(self.titlebar)
        self.content_container = QtWidgets.QWidget()
        self.content_container.setObjectName("ContentContainer")
        self.content_container.setStyleSheet("background: transparent;")
        inner.addWidget(self.content_container)
        self.content = QtWidgets.QVBoxLayout(self.content_container)
        self.content.setContentsMargins(8, 6, 8, 8)
        self.content.setSpacing(6)


        self._apply_style()        # tint to the saved accent (no-op if default)
        self._restore_geometry()

    def toggle_minimize(self) -> None:
        self.set_minimized(not getattr(self, "_is_minimized", False))

    def set_minimized(self, on: bool) -> None:
        self._is_minimized = on
        self.content_container.setVisible(not on)
        if on:
            self._normal_height = self.height()
            self.setFixedHeight(36)
        else:
            self.setMinimumHeight(0)
            self.setMaximumHeight(16777215)
            h = getattr(self, "_normal_height", 0)
            if h > 36:
                self.resize(self.width(), h)
            else:
                self.adjustSize()

    # --- click-through (Win32) ------------------------------------------
    def set_click_through(self, on: bool) -> None:
        if not sys.platform.startswith("win"):
            return
        import ctypes
        GWL_EXSTYLE = -20
        WS_EX_LAYERED = 0x00080000
        WS_EX_TRANSPARENT = 0x00000020
        hwnd = int(self.winId())
        user32 = ctypes.windll.user32
        ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        if on:
            ex |= WS_EX_LAYERED | WS_EX_TRANSPARENT
        else:
            ex &= ~WS_EX_TRANSPARENT
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex)

    def apply_scale(self, scale: float) -> None:
        """Uniform per-overlay zoom via a scaled QSS (text + metrics); also grows
        the window from the subclass's base size. The first call after a restore
        re-applies the persisted (drag-resized) size instead, so a relaunch
        doesn't snap a resizable overlay back to base*scale; later calls (the
        scale slider, DPS mode switches) resize normally."""
        self._scale = scale or 1.0
        self._apply_style()
        saved = getattr(self, "_saved_size", None)
        if saved is not None:
            self._saved_size = None
            self.resize(*saved)
            return
        bw, bh = getattr(self, "_base_w", 0), getattr(self, "_base_h", 0)
        if bw and bh:
            self.resize(round(bw * self._scale), round(bh * self._scale))

    # --- accent (the live "Highlight color") ----------------------------
    def _apply_style(self) -> None:
        self.setStyleSheet(theme.scaled_qss(self._scale))

    def apply_accent(self, accent: str) -> None:
        """Re-tint this overlay (and its non-QSS accent widgets) to `accent`."""
        self._accent = accent
        self._apply_style()
        self._retint(accent)

    def _retint(self, accent: str) -> None:
        """Hook: subclasses recolor their non-QSS accent widgets (bars, charts,
        section ticks). No-op by default."""

    def set_locked(self, on: bool) -> None:
        """Locked = click-through (mouse passes to the game) + no titlebar drag.
        Unlock from the control panel (a click-through window can't be clicked)."""
        self._locked = on
        self.set_click_through(on)

    # --- resize grip ------------------------------------------------------
    def enable_resize_grip(self) -> None:
        """Bottom-right drag-to-resize grip; pinned by the base resizeEvent.
        Also opts the overlay into size persistence (geometry saves w,h too)."""
        self._resizable = True
        self._grip = QtWidgets.QSizeGrip(self)
        self._grip.setFixedSize(16, 16)
        self._grip.setToolTip("Drag to resize")

    def resizeEvent(self, e):
        super().resizeEvent(e)
        g = getattr(self, "_grip", None)
        if g is not None:
            g.move(self.width() - g.width() - 2, self.height() - g.height() - 2)
            g.raise_()

    def set_opacity(self, value: float) -> None:
        """Apply the global overlay opacity. Subclasses with child windows (e.g.
        the entity overlay's drop table) override this to forward it."""
        self.setWindowOpacity(max(0.3, min(1.0, value)))

    # --- geometry persistence -------------------------------------------
    def persist_geometry(self) -> None:
        """Position always; size too for grip-resizable overlays, so a drag-
        resize survives the relaunch (it used to snap back to base*scale)."""
        if self._settings is None:
            return
        g = f"{self.x()},{self.y()}"
        if getattr(self, "_resizable", False):
            g += f",{self.width()},{self.height()}"
        self._settings.geometry[self._geo_key] = g
        self._settings.save()

    def _restore_geometry(self) -> None:
        if self._settings is None:
            return
        g = self._settings.geometry.get(self._geo_key)
        if g:
            try:
                parts = [int(v) for v in g.split(",")]
                self.move(parts[0], parts[1])
                if len(parts) == 4 and parts[2] >= 120 and parts[3] >= 80:
                    # stash for the subclass's first apply_scale (which would
                    # otherwise resize to base*scale over this) + apply now for
                    # overlays that never call apply_scale
                    self._saved_size = (parts[2], parts[3])
                    self.resize(parts[2], parts[3])
                return
            except ValueError:
                pass
        self.move(60, 60)

    def closeEvent(self, e):
        self.persist_geometry()
        super().closeEvent(e)
