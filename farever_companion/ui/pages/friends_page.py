from __future__ import annotations

import webbrowser

from PySide6 import QtCore, QtGui, QtWidgets
from shiboken6 import isValid

from .. import theme
from .. import components as C
from ..layout import clear_layout
from ..workers import CallWorker
from ...api import presence_and_friends

class FriendsPageMixin:
    def _page_friends(self):
        page, v = self._page_container()
        v.addWidget(C.SectionHeader("Friends", tag="ONLINE STATUS"))

        intro = QtWidgets.QLabel(
            "Your friends and who's online. Add them on the website.")
        intro.setObjectName("Muted")
        intro.setWordWrap(True)
        v.addWidget(intro)

        self._friends_signedout = C.SignInCard(
            "Sign in to your Farever Pal account to see your friends and share your online status.",
            self._open_login)
        v.addWidget(self._friends_signedout)

        self._friends_main = QtWidgets.QWidget()
        fm = QtWidgets.QVBoxLayout(self._friends_main)
        fm.setContentsMargins(0, 0, 0, 0)
        fm.setSpacing(12)

        self._share_toggle = C.LabeledToggle(
            "Share my online status with friends", self.s.share_presence)
        self._share_toggle.toggled.connect(self._toggle_share_presence)
        fm.addWidget(self._share_toggle)

        actions = QtWidgets.QHBoxLayout()
        actions.setSpacing(8)
        self._friends_refresh_btn = QtWidgets.QPushButton("Refresh")
        self._friends_refresh_btn.setObjectName("Outline")
        self._friends_refresh_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self._friends_refresh_btn.clicked.connect(lambda: self._friends_poll(force=True))
        manage = QtWidgets.QPushButton("Manage on web")
        manage.setObjectName("Outline")
        manage.setCursor(QtCore.Qt.PointingHandCursor)
        manage.clicked.connect(lambda: webbrowser.open(f"{self.s.api_base}/friends.php"))
        actions.addWidget(self._friends_refresh_btn)
        actions.addWidget(manage)
        actions.addStretch(1)
        self._friends_pending = QtWidgets.QLabel("")
        self._friends_pending.setObjectName("Mono")
        self._friends_pending.setStyleSheet(
            f"color:{theme.GOLD};background:transparent;")
        actions.addWidget(self._friends_pending)
        fm.addLayout(actions)

        fm.addWidget(C.SectionHeader("Friend list"))
        self._friends_list_host = QtWidgets.QWidget()
        self._friends_list_box = QtWidgets.QVBoxLayout(self._friends_list_host)
        self._friends_list_box.setContentsMargins(0, 0, 0, 0)
        self._friends_list_box.setSpacing(8)
        fm.addWidget(self._friends_list_host)

        self._friends_empty = QtWidgets.QLabel(
            "No friends yet — add some on the website with their friend code.")
        self._friends_empty.setObjectName("Muted")
        self._friends_empty.setWordWrap(True)
        fm.addWidget(self._friends_empty)

        v.addWidget(self._friends_main)
        v.addStretch(1)
        self._render_friends()
        return page

    def _presence_pill(self, state: str) -> QtWidgets.QWidget:
        color, label = {
            "companion": (theme.GOOD, "In-game"),
            "web": (theme.ACCENT, "Online (web)"),
        }.get(state, (theme.MUTED, "Offline"))
        w = QtWidgets.QWidget()
        w.setStyleSheet("background:transparent;")
        lay = QtWidgets.QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        lay.setAlignment(QtCore.Qt.AlignVCenter)
        dot = QtWidgets.QFrame()
        dot.setFixedSize(8, 8)
        dot.setStyleSheet(f"background:{color};border:0;")
        lbl = QtWidgets.QLabel(label)
        lbl.setStyleSheet(
            f"color:{color};font-family:'{theme.MONO_FONT}','Consolas';"
            "font-size:11px;background:transparent;")
        lay.addWidget(dot)
        lay.addWidget(lbl)
        return w

    def _initial_tile(self, name: str, size: int) -> QtGui.QPixmap:
        pm = QtGui.QPixmap(size, size)
        pm.fill(QtCore.Qt.transparent)
        p = QtGui.QPainter(pm)
        p.setRenderHint(QtGui.QPainter.Antialiasing, False)
        fill = QtGui.QColor(theme.ACCENT); fill.setAlpha(38)
        edge = QtGui.QColor(theme.ACCENT); edge.setAlpha(120)
        p.fillRect(0, 0, size, size, fill)
        p.setPen(edge)
        p.drawRect(0, 0, size - 1, size - 1)
        p.setPen(QtGui.QColor(theme.ACCENT))
        f = QtGui.QFont(theme.UI_FONT)
        f.setBold(True)
        f.setPointSize(max(8, int(size * 0.4)))
        p.setFont(f)
        p.drawText(pm.rect(), QtCore.Qt.AlignCenter, (name[:1] or "?").upper())
        p.end()
        return pm

    def _friend_row(self, f: dict) -> QtWidgets.QFrame:
        frame = QtWidgets.QFrame()
        frame.setObjectName("Cell")
        lay = QtWidgets.QHBoxLayout(frame)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(12)
        lay.setAlignment(QtCore.Qt.AlignVCenter)
        av = QtWidgets.QLabel()
        av.setFixedSize(34, 34)
        av.setPixmap(self._initial_tile(f.get("username", ""), 34))
        col = QtWidgets.QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(2)
        name = QtWidgets.QLabel(f.get("username", "?"))
        name.setStyleSheet(f"color:{theme.TEXT};font-weight:600;background:transparent;")
        code = QtWidgets.QLabel(f.get("public_id", ""))
        code.setObjectName("Mono")
        code.setStyleSheet(
            f"color:{theme.MUTED};font-family:'{theme.MONO_FONT}','Consolas';"
            "font-size:10px;background:transparent;")
        col.addWidget(name)
        col.addWidget(code)
        lay.addWidget(av)
        lay.addLayout(col, 1)
        lay.addWidget(self._presence_pill(f.get("presence", "offline")))
        view = QtWidgets.QPushButton("View")
        view.setObjectName("Outline")
        view.setCursor(QtCore.Qt.PointingHandCursor)
        code_str = f.get("public_id", "")
        view.clicked.connect(lambda _=False, c=code_str: webbrowser.open(f"{self.s.api_base}/u.php?c={c}"))
        lay.addWidget(view)
        return frame

    def _render_friends(self):
        # the Friends page may have been evicted from the page cache while a
        # poll worker was in flight
        if not getattr(self, "_widget_alive", lambda w: False)(
                getattr(self, "_friends_main", None)):
            return
        signed = bool(self.s.account_token)
        self._friends_signedout.setVisible(not signed)
        self._friends_main.setVisible(signed)
        if not signed:
            return
        clear_layout(self._friends_list_box)
        for fr in self._friends:
            self._friends_list_box.addWidget(self._friend_row(fr))
        self._friends_empty.setVisible(not self._friends)
        self._friends_list_host.setVisible(bool(self._friends))

    def _refresh_friends_gating(self):
        """Start/stop presence polling with sign-in state; refresh the page."""
        if not hasattr(self, "_friends_timer"):
            return
        if self.s.account_token:
            if not self._friends_timer.isActive():
                self._friends_timer.start()
            self._friends_poll(force=True)
        else:
            self._friends_timer.stop()
            self._friends = []
        self._render_friends()
        self._render_corunners()

    def _friends_poll(self, force: bool = False):
        if not self.s.account_token:
            return
        if self._friends_worker is not None and self._friends_worker.isRunning():
            return
        base, token, share = self.s.api_base, self.s.account_token, bool(self.s.share_presence)
        self._friends_worker = CallWorker(lambda _w: presence_and_friends(base, token, share))
        self._friends_worker.done.connect(lambda _t, res: self._on_friends_done(res))
        self._friends_worker.start()

    def _on_friends_done(self, res: dict):
        self._friends_worker = None
        if not res.get("ok"):
            return
        self._friends = res.get("friends") or []
        n = int(res.get("incoming_count") or 0)
        if hasattr(self, "_friends_pending"):
            self._friends_pending.setText(
                f"{n} pending request{'s' if n != 1 else ''}" if n else "")
        self._render_friends()
        self._render_corunners()

    def _toggle_share_presence(self, on: bool):
        self._set("share_presence", on)
        self._friends_poll(force=True)
