from __future__ import annotations

import threading
import webbrowser

from PySide6 import QtCore, QtGui, QtWidgets

from . import theme
from .workers import CallWorker
from ..api import FareverAPI, login_with_profile, oauth_with_profile
from ..data import icons


def _fetch_pixmap(url: str):
    try:
        import urllib.request
        req = urllib.request.Request(url, headers={"User-Agent": "FareverPal-Companion"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = r.read()
    except Exception:
        return None
    pm = QtGui.QPixmap()
    pm.loadFromData(data)
    return pm if not pm.isNull() else None


class LoginDialog(QtWidgets.QDialog):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.s = settings
        self.result_user: dict | None = None
        self.result_token: str = ""
        self._worker: CallWorker | None = None
        self._oauth_worker: CallWorker | None = None

        self.setWindowTitle("Sign in to Farever Pal")
        self.setObjectName("Card")
        self.setMinimumWidth(360)
        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(22, 20, 22, 20)
        v.setSpacing(12)

        head = QtWidgets.QLabel("Sign in")
        head.setObjectName("H1")
        v.addWidget(head)
        sub = QtWidgets.QLabel("Use your farever-pals.com account to sync runs + settings.")
        sub.setObjectName("Muted")
        sub.setWordWrap(True)
        v.addWidget(sub)

        self.btn_google = self._provider_button(
            "Continue with Google", "google", bg="#ffffff", fg="#1f2328", hover="#e8eaed",
            icon=icons.brand_qicon("google-g", 18))
        self.btn_discord = self._provider_button(
            "Continue with Discord", "discord", bg="#5865f2", fg="#ffffff", hover="#4752c4",
            icon=icons.ui_qicon("discord", "#ffffff", 18))
        v.addWidget(self.btn_google)
        v.addWidget(self.btn_discord)

        div = QtWidgets.QLabel("or sign in with a password")
        div.setObjectName("Muted")
        div.setAlignment(QtCore.Qt.AlignCenter)
        v.addWidget(div)

        self.user = QtWidgets.QLineEdit()
        self.user.setPlaceholderText("Username")
        v.addWidget(self.user)
        self.pw = QtWidgets.QLineEdit()
        self.pw.setPlaceholderText("Password")
        self.pw.setEchoMode(QtWidgets.QLineEdit.Password)
        self.pw.returnPressed.connect(self._submit)
        v.addWidget(self.pw)

        self.err = QtWidgets.QLabel("")
        self.err.setStyleSheet(f"color:{theme.DANGER};background:transparent;")
        self.err.setWordWrap(True)
        self.err.hide()
        v.addWidget(self.err)

        self.btn = QtWidgets.QPushButton("Sign in")
        self.btn.setObjectName("Accent")
        self.btn.setMinimumHeight(34)
        self.btn.clicked.connect(self._submit)
        v.addWidget(self.btn)

        web = QtWidgets.QLabel(
            "Google/Discord create your account automatically. Prefer the website? "
            f"<a href='{self.s.api_base}/login.php' style='color:{theme.ACCENT};'>Open farever-pals.com</a>.")
        web.setObjectName("Muted")
        web.setWordWrap(True)
        web.setOpenExternalLinks(False)
        web.linkActivated.connect(lambda u: webbrowser.open(u))
        v.addWidget(web)

    def _provider_button(self, label: str, provider: str, *, bg: str, fg: str,
                         hover: str, icon=None) -> QtWidgets.QPushButton:
        b = QtWidgets.QPushButton(label)
        b.setMinimumHeight(36)
        b.setCursor(QtCore.Qt.PointingHandCursor)
        if icon is not None:
            b.setIcon(icon)
            b.setIconSize(QtCore.QSize(18, 18))
        b.setAutoDefault(False)   # keep the password "Sign in" as the default button
        b.setDefault(False)
        b.setStyleSheet(
            f"QPushButton{{background:{bg};color:{fg};border:none;font-weight:600;}}"
            f"QPushButton:hover{{background:{hover};}}"
            f"QPushButton:disabled{{background:{bg};color:{fg}99;}}")
        b.clicked.connect(lambda: self._start_oauth(provider))
        return b

    def _set_oauth_busy(self, busy: bool, active: str = ""):
        for prov, btn in (("google", self.btn_google), ("discord", self.btn_discord)):
            btn.setEnabled(not busy)
            base = f"Continue with {prov.capitalize()}"
            btn.setText("Waiting for browser…" if (busy and prov == active) else base)
        self.user.setEnabled(not busy)
        self.pw.setEnabled(not busy)
        self.btn.setEnabled(not busy)

    def _start_oauth(self, provider: str):
        if self._oauth_worker is not None:
            return
        self.err.hide()
        self._set_oauth_busy(True, provider)
        base = self.s.api_base
        self._oauth_worker = CallWorker(lambda _w: oauth_with_profile(base, provider))
        self._oauth_worker.done.connect(lambda _t, res: self._on_oauth_done(res))
        self._oauth_worker.start()

    def _on_oauth_done(self, res: dict):
        self._oauth_worker = None
        self._set_oauth_busy(False)
        if res.get("ok") and res.get("token"):
            self.result_token = res["token"]
            self.result_user = res.get("user", {})
            self.accept()
            return
        errs = {
            "timeout": "Timed out waiting for the browser. Try again.",
            "browser": "Could not open your browser. Sign in on the website instead.",
            "loopback": "Couldn't open the local sign-in port. Try again.",
            "unconfigured": "This sign-in method isn't enabled on the server yet.",
            "network": "Could not reach farever-pals.com. Check your connection.",
        }
        self._show_err(errs.get(res.get("error", ""), "Sign-in failed. Please try again."))

    def _submit(self):
        u, p = self.user.text().strip(), self.pw.text()
        if not u or not p:
            self._show_err("Enter your username and password.")
            return
        self.err.hide()
        self.btn.setEnabled(False)
        self.btn.setText("Signing in…")
        base = self.s.api_base
        self._worker = CallWorker(lambda _w: login_with_profile(base, u, p))
        self._worker.done.connect(lambda _t, res: self._on_done(res))
        self._worker.start()

    def _on_done(self, res: dict):
        self.btn.setEnabled(True)
        self.btn.setText("Sign in")
        if res.get("ok") and res.get("token"):
            self.result_token = res["token"]
            self.result_user = res.get("user", {})
            self.accept()
            return
        errs = {
            "bad_credentials": "Wrong username or password.",
            "missing_credentials": "Enter your username and password.",
            "network": "Could not reach farever-pals.com. Check your connection.",
        }
        self._show_err(errs.get(res.get("error", ""), "Sign-in failed. Please try again."))

    def _show_err(self, msg: str):
        self.err.setText(msg)
        self.err.show()


class AccountMixin:
    """Top-bar account button, sign-in dialog, avatar, and accent sync.

    Mixed into ControlPanel; relies on the panel for `self.s`, `self.log`,
    `self._set_accent`, and the per-page gating refreshers.
    """

    def _build_account_host(self) -> QtWidgets.QWidget:
        self._acct_host = QtWidgets.QWidget()
        self._acct_host.setStyleSheet("background:transparent;")
        self._acct_box = QtWidgets.QHBoxLayout(self._acct_host)
        self._acct_box.setContentsMargins(0, 0, 0, 0)
        self._acct_box.setSpacing(0)
        self._avatar_pm: QtGui.QPixmap | None = None
        self._avatar_url_loaded = ""
        self._avatar_loader: CallWorker | None = None
        return self._acct_host

    def _refresh_account_button(self):
        while self._acct_box.count():
            it = self._acct_box.takeAt(0)
            w = it.widget()
            if w is not None:
                w.deleteLater()
        if self.s.account_token and self.s.account_name:
            btn = QtWidgets.QPushButton(f"  {self.s.account_name}")
            btn.setObjectName("Outline")
            btn.setCursor(QtCore.Qt.PointingHandCursor)
            btn.setMinimumHeight(32)
            btn.setIcon(QtGui.QIcon(self._account_avatar_pixmap(20)))
            btn.setIconSize(QtCore.QSize(20, 20))
            btn.clicked.connect(self._show_account_menu)
            self._acct_btn = btn
            self._acct_box.addWidget(btn)
            self._load_avatar()
        else:
            btn = QtWidgets.QPushButton("SIGN IN")
            btn.setObjectName("Outline")
            btn.setCursor(QtCore.Qt.PointingHandCursor)
            btn.setMinimumHeight(32)
            btn.clicked.connect(self._open_login)
            self._acct_btn = btn
            self._acct_box.addWidget(btn)
        # Mouse-only: a focused button would otherwise re-open sign-in / the
        # account menu when Space is pressed (Qt activates the focused button).
        self._acct_btn.setFocusPolicy(QtCore.Qt.NoFocus)
        self._refresh_speedrun_gating()
        if hasattr(self, "_build_profile_lbl"):
            self._refresh_profile_build()
        self._refresh_friends_gating()
        self._refresh_collection_gating()
        if hasattr(self, "_refresh_nav_visibility"):
            self._refresh_nav_visibility()

    def _account_avatar_pixmap(self, size: int) -> QtGui.QPixmap:
        if self._avatar_pm is not None and not self._avatar_pm.isNull():
            return self._avatar_pm.scaled(size, size, QtCore.Qt.KeepAspectRatioByExpanding,
                                          QtCore.Qt.SmoothTransformation)
        pm = QtGui.QPixmap(size, size)
        pm.fill(QtCore.Qt.transparent)
        p = QtGui.QPainter(pm)
        p.setRenderHint(QtGui.QPainter.Antialiasing, False)
        p.fillRect(0, 0, size, size, QtGui.QColor(theme.with_alpha(theme.ACCENT, 60)))
        p.setPen(QtGui.QColor(theme.ACCENT))
        p.drawRect(0, 0, size - 1, size - 1)
        initial = (self.s.account_name[:1] or "?").upper()
        f = QtGui.QFont(theme.UI_FONT, max(8, int(size * 0.5)))
        f.setBold(True)
        p.setFont(f)
        p.drawText(pm.rect(), QtCore.Qt.AlignCenter, initial)
        p.end()
        return pm

    def _load_avatar(self):
        url = self.s.account_avatar
        if not url or url == self._avatar_url_loaded:
            return
        self._avatar_url_loaded = url
        self._avatar_loader = CallWorker(lambda _w: _fetch_pixmap(url))
        self._avatar_loader.done.connect(lambda _t, pm: self._on_avatar_loaded(pm))
        self._avatar_loader.start()

    def _on_avatar_loaded(self, pm):
        if pm is None or pm.isNull():
            return
        self._avatar_pm = pm
        if self.s.account_token and hasattr(self, "_acct_btn"):
            self._acct_btn.setIcon(QtGui.QIcon(self._account_avatar_pixmap(20)))

    def _show_account_menu(self):
        m = QtWidgets.QMenu(self)
        if self.s.account_code:
            act_prof = m.addAction("View profile on web")
            act_prof.triggered.connect(
                lambda: webbrowser.open(f"{self.s.api_base}/u.php?c={self.s.account_code}"))
        act_out = m.addAction("Sign out")
        act_out.triggered.connect(self._sign_out)
        m.exec(self._acct_btn.mapToGlobal(self._acct_btn.rect().bottomLeft()))

    def _avatar_url_for(self, user: dict) -> str:
        path = (user or {}).get("avatar_path")
        if not path:
            return ""
        from urllib.parse import quote
        return f"{self.s.api_base}/uploads/avatars/{quote(str(path))}"

    def _open_login(self):
        dlg = LoginDialog(self.s, self)
        if dlg.exec() == QtWidgets.QDialog.Accepted and dlg.result_user:
            self.s.account_name = dlg.result_user.get("username", "")
            self.s.account_code = dlg.result_user.get("public_id", "")
            self.s.account_token = dlg.result_token
            self.s.account_avatar = self._avatar_url_for(dlg.result_user)
            self.s.save()
            self._avatar_pm = None
            self._avatar_url_loaded = ""
            self._refresh_account_button()
            self.log(f"Signed in as {self.s.account_name}.")
            accent = dlg.result_user.get("accent_color")
            if accent:
                self._set_accent(accent)
            else:
                self._sync_accent_to_account()

    def _sign_out(self):
        self.s.account_name = ""
        self.s.account_code = ""
        self.s.account_token = ""
        self.s.account_avatar = ""
        self.s.save()
        self._avatar_pm = None
        self._avatar_url_loaded = ""
        self._refresh_account_button()
        self.log("Signed out.")

    def _sync_accent_to_account(self):
        if not self.s.account_token:
            return
        api = FareverAPI(self.s.api_base, self.s.account_token)
        accent = self.s.hud_accent
        threading.Thread(target=lambda: api.set_accent(accent), daemon=True).start()
