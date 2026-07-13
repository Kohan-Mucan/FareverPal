from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from .. import theme
from .. import components as C
from ..workers import CallWorker
from ...api import FareverAPI

class SpeedrunPageMixin:
    def _page_speedrun(self):
        page, v = self._page_container()

        v.addWidget(C.SectionHeader("Timer"))
        card = C.OverlayCard("timer", "Open Speedrun Timer",
                             "Stopwatch with auto-stop on the boss kill.")
        card.toggled.connect(lambda on: self._request_overlay("speedrun", on))
        self._register_card("speedrun", card)
        v.addWidget(card)
        sc = C.SliderRow("Overlay scale", 70, 200, int(self.s.speedrun_scale * 100),
                         lambda x: f"{x / 100:.2f}x")
        sc.valueChanged.connect(self._set_speedrun_scale)
        v.addWidget(sc)

        v.addWidget(C.SectionHeader("Automation"))
        self._auto_toggle = C.LabeledToggle(
            "Auto Detect Boss Run", self.s.speedrun_auto)
        self._auto_toggle.toggled.connect(self._toggle_auto)
        v.addWidget(self._auto_toggle)

        self._auto_upload_toggle = C.LabeledToggle(
            "Auto-upload finished runs", self.s.speedrun_auto_upload)
        self._auto_upload_toggle.toggled.connect(self._toggle_auto_upload)
        v.addWidget(self._auto_upload_toggle)

        rearm = C.LabeledToggle(
            "Re-arm for back-to-back runs", self.s.speedrun_auto_rearm)
        rearm.toggled.connect(lambda on: self._set("speedrun_auto_rearm", on))
        v.addWidget(rearm)

        self._auto_upload_hint = QtWidgets.QLabel("")
        self._auto_upload_hint.setObjectName("Muted")
        self._auto_upload_hint.setWordWrap(True)
        v.addWidget(self._auto_upload_hint)
        self._refresh_speedrun_gating()

        # Boards are Hard-only, so the fallback difficulty is fixed to Hard (used
        # only when auto-detect is off or the live boss level can't be read). A
        # detected Normal run still uploads as Normal - it just lands on the
        # player's profile, never on a leaderboard.
        fb = QtWidgets.QLabel(
            "Fallback difficulty: Hard. Detected Normal runs are saved to your "
            "profile only — they don't appear on any leaderboard.")
        fb.setObjectName("Muted")
        fb.setWordWrap(True)
        v.addWidget(fb)

        v.addWidget(C.SectionHeader("Build"))
        self._build_profile_lbl = QtWidgets.QLabel("")
        self._build_profile_lbl.setObjectName("Muted")
        self._build_profile_lbl.setWordWrap(True)
        v.addWidget(self._build_profile_lbl)

        self._build_override_toggle = C.LabeledToggle(
            "Override build per run", self.s.speedrun_build_override_on)
        self._build_override_toggle.toggled.connect(self._toggle_build_override)
        v.addWidget(self._build_override_toggle)

        self._build_override_box = QtWidgets.QWidget()
        bob = QtWidgets.QVBoxLayout(self._build_override_box)
        bob.setContentsMargins(0, 0, 0, 0)
        bob.setSpacing(6)
        self._build_code_edit = QtWidgets.QLineEdit(self.s.speedrun_build_override or "")
        self._build_code_edit.setPlaceholderText("Build code, e.g. ABCD1234 (or paste a build link)")
        self._build_code_edit.setClearButtonEnabled(True)
        self._build_code_edit.textEdited.connect(self._on_build_code_edited)
        bob.addWidget(C.Field("Build code", self._build_code_edit))
        self._build_preview_lbl = QtWidgets.QLabel("")
        self._build_preview_lbl.setObjectName("Mono")
        self._build_preview_lbl.setWordWrap(True)
        bob.addWidget(self._build_preview_lbl)
        v.addWidget(self._build_override_box)
        self._build_override_box.setVisible(self.s.speedrun_build_override_on)

        self._build_lookup_worker: CallWorker | None = None
        self._refresh_profile_build()
        if self.s.speedrun_build_override and self.s.speedrun_build_override_on:
            self._lookup_build(self.s.speedrun_build_override)

        self._corunner_head = C.SectionHeader("Co-runners")
        v.addWidget(self._corunner_head)
        co_intro = QtWidgets.QLabel(
            "Picked friends ride along on auto-uploaded runs with their own build.")
        co_intro.setObjectName("Muted")
        co_intro.setWordWrap(True)
        v.addWidget(co_intro)
        self._corunner_hint = QtWidgets.QLabel("")
        self._corunner_hint.setObjectName("Muted")
        self._corunner_hint.setWordWrap(True)
        v.addWidget(self._corunner_hint)
        self._corunner_host = QtWidgets.QWidget()
        self._corunner_box = QtWidgets.QVBoxLayout(self._corunner_host)
        self._corunner_box.setContentsMargins(0, 0, 0, 0)
        self._corunner_box.setSpacing(8)
        v.addWidget(self._corunner_host)
        self._render_corunners()

        v.addWidget(C.SectionHeader("Hotkeys (global · work while in-game)"))
        hk = QtWidgets.QGridLayout()
        hk.setHorizontalSpacing(12)
        for i, (attr, label) in enumerate([
                ("hotkey_speedrun_toggle", "Start / Stop"),
                ("hotkey_speedrun_reset", "Reset")]):
            edit = QtWidgets.QKeySequenceEdit(QtGui.QKeySequence(getattr(self.s, attr)))
            try:
                edit.setMaximumSequenceLength(1)
            except (AttributeError, TypeError):
                pass
            edit.keySequenceChanged.connect(lambda seq, a=attr: self._set_hotkey(a, seq))
            hk.addWidget(C.Field(label, edit), 0, i)
        v.addLayout(hk)

        fair = QtWidgets.QLabel(
            "Clean runs post instantly; suspicious times need a video. The "
            "companion is read-only.")
        fair.setObjectName("Muted")
        fair.setWordWrap(True)
        fair.setStyleSheet(f"color:{theme.GOLD};background:transparent;")
        v.addWidget(fair)

        clr = QtWidgets.QPushButton("Clear best times")
        clr.setObjectName("Outline")
        clr.clicked.connect(self._clear_speedrun_best)
        v.addWidget(clr)
        v.addStretch(1)
        return page

    def _corunner_row(self, f: dict) -> QtWidgets.QFrame:
        frame = QtWidgets.QFrame()
        frame.setObjectName("Cell")
        lay = QtWidgets.QHBoxLayout(frame)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(10)
        lay.setAlignment(QtCore.Qt.AlignVCenter)
        name = QtWidgets.QLabel(f.get("username", "?"))
        name.setStyleSheet(f"color:{theme.TEXT};background:transparent;")
        code = f.get("public_id", "")
        lay.addWidget(name, 1)
        lay.addWidget(self._presence_pill(f.get("presence", "offline")))
        tog = C.ToggleSwitch(code in (self.s.speedrun_corunners or []))
        tog.toggled.connect(lambda on, c=code: self._toggle_corunner(c, on))
        lay.addWidget(tog)
        return frame

    def _render_corunners(self):
        if not hasattr(self, "_corunner_box"):
            return
        while self._corunner_box.count():
            it = self._corunner_box.takeAt(0)
            w = it.widget()
            if w is not None:
                w.deleteLater()
        signed = bool(self.s.account_token)
        if not signed:
            self._corunner_hint.setText("Sign in to pick friends as co-runners.")
            self._corunner_hint.setVisible(True)
            self._corunner_host.setVisible(False)
            return
        if not self._friends:
            self._corunner_hint.setText("No friends yet — add some on the Friends tab.")
            self._corunner_hint.setVisible(True)
            self._corunner_host.setVisible(False)
            return
        self._corunner_hint.setVisible(False)
        self._corunner_host.setVisible(True)
        for fr in self._friends:
            self._corunner_box.addWidget(self._corunner_row(fr))
        n = len(self.s.speedrun_corunners or [])
        self._corunner_head.set_tag(f"{n} PICKED" if n else "")

    def _toggle_corunner(self, code: str, on: bool):
        cur = list(self.s.speedrun_corunners or [])
        if on and code not in cur:
            cur.append(code)
        elif not on and code in cur:
            cur.remove(code)
        self._set("speedrun_corunners", cur)
        if hasattr(self, "_corunner_head"):
            n = len(cur)
            self._corunner_head.set_tag(f"{n} PICKED" if n else "")

    def _toggle_auto(self, on: bool):
        self._set("speedrun_auto", on)
        self._refresh_speedrun_gating()

    def _toggle_auto_upload(self, on: bool):
        self._set("speedrun_auto_upload", on)
        self._refresh_speedrun_gating()

    def _toggle_build_override(self, on: bool):
        self._set("speedrun_build_override_on", on)
        self._build_override_box.setVisible(on)
        if on and self.s.speedrun_build_override:
            self._lookup_build(self.s.speedrun_build_override)

    @staticmethod
    def _build_code_from(text: str) -> str:
        """Accept a bare code or a pasted build link; pull out the code token."""
        s = (text or "").strip()
        if "/" in s:
            s = s.rstrip("/").split("/")[-1]
        if "=" in s:
            s = s.split("=")[-1]
        return s.strip().upper()

    def _on_build_code_edited(self, text: str):
        code = self._build_code_from(text)
        self._set("speedrun_build_override", code)
        if not code:
            self._build_preview_lbl.setText("")
            return
        self._build_preview_lbl.setText("resolving…")
        self._build_preview_lbl.setStyleSheet(f"color:{theme.MUTED};background:transparent;")
        self._lookup_build(code)

    def _lookup_build(self, code: str):
        if not self.s.account_token:
            self._build_preview_lbl.setText("sign in to resolve build codes")
            self._build_preview_lbl.setStyleSheet(f"color:{theme.MUTED};background:transparent;")
            return
        if self._build_lookup_worker is not None and self._build_lookup_worker.isRunning():
            return
        base, token = self.s.api_base, self.s.account_token
        self._build_lookup_worker = CallWorker(
            lambda _w, c=code: FareverAPI(base, token).lookup_build(c), tag=code)
        self._build_lookup_worker.done.connect(self._on_build_lookup)
        self._build_lookup_worker.start()

    def _on_build_lookup(self, tag: str, res: dict):
        self._build_lookup_worker = None
        # Ignore a stale result if the field moved on since the request fired.
        if tag != (self.s.speedrun_build_override or "").strip().upper():
            return
        if res.get("ok"):
            cls = res.get("class")
            who = " · yours" if res.get("mine") else ""
            self._build_preview_lbl.setText(
                f"✓ {res.get('title') or res.get('code')}"
                + (f"  ({cls}{who})" if cls or who else ""))
            self._build_preview_lbl.setStyleSheet(f"color:{theme.GOOD};background:transparent;")
        else:
            self._build_preview_lbl.setText("✗ no build with that code")
            self._build_preview_lbl.setStyleSheet(f"color:{theme.GOLD};background:transparent;")

    def _refresh_profile_build(self):
        """Show the profile's featured build (the default linked to uploads)."""
        if not hasattr(self, "_build_profile_lbl"):
            return
        if not self.s.account_token:
            self._build_profile_lbl.setText("Sign in to link your profile build.")
            return
        self._build_profile_lbl.setText("Profile build: loading…")
        base, token = self.s.api_base, self.s.account_token
        w = CallWorker(lambda _w: FareverAPI(base, token).me(), tag="me")
        w.done.connect(self._on_profile_build)
        w.start()
        self._me_worker = w

    def _on_profile_build(self, tag: str, res: dict):
        if not hasattr(self, "_build_profile_lbl"):
            return
        fb = (res.get("user") or {}).get("featured_build") if res.get("ok") else None
        if fb:
            cls = fb.get("class")
            self._build_profile_lbl.setText(
                f"Profile build: {fb.get('title') or fb.get('code')}"
                + (f"  ({cls})" if cls else ""))
        else:
            self._build_profile_lbl.setText(
                "No featured build set. Override per run below, or set one on the site.")

    def _refresh_speedrun_gating(self):
        """Auto-upload is only usable when Auto Detect Boss Run is on AND signed in."""
        if not hasattr(self, "_auto_upload_toggle"):
            return
        logged_in = bool(self.s.account_token)
        self._auto_upload_toggle.setEnabled(bool(self.s.speedrun_auto) and logged_in)
        if not self.s.speedrun_auto:
            hint = "Turn on Auto Detect Boss Run to enable auto-upload."
        elif not logged_in:
            hint = "Sign in to auto-upload, or upload each run from the timer."
        else:
            hint = ""
        self._auto_upload_hint.setText(hint)
        self._auto_upload_hint.setVisible(bool(hint))

    def _set_speedrun_scale(self, vv):
        sc = vv / 100.0
        self._set("speedrun_scale", sc)
        ov = self.overlays.get("speedrun")
        if ov is not None and hasattr(ov, "apply_scale"):
            ov.apply_scale(sc)

    def _clear_speedrun_best(self):
        self.s.speedrun_best = {}
        self.s.save()
        ov = self.overlays.get("speedrun")
        if ov is not None:
            ov._render()
        self.log("Speedrun best times cleared.")
