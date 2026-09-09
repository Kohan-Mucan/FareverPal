"""DPS Settings module - split from ui/pages/settings.py.
Imports the single shared namespace (constants, theme, Qt aliases)
from ._shared so moved method bodies keep their original globals.
"""

from ._shared import *  # noqa: F401,F403
from ...copy_utils import copy_with_feedback

# Seconds between full process/folder rescans of the Live Diagnostics card
# while the DPS tab stays open: the 1 s poll repaints labels from cached scan
# state every tick and only re-runs the module snapshot / disk hash scan on
# this interval (or immediately when the tab regains focus), so an open tab
# never stutters on scans.
_DPS_DIAG_SCAN_INTERVAL = 5.0


class SettingsDpsEngineMixin:
    """Combat DPS Engine: the three capture-mode option cards and the
Live Diagnostics card."""

    # =========================================================================
    # --- DPS & Combat Engine Settings Tab (Options 1, 2, 3) -----------------
    # =========================================================================
    def _tab_dps(self):
        w, lv = self._tab_page()
        if not hasattr(self, "_settings_steppers") or self._settings_steppers is None:
            self._settings_steppers = []
        self._build_dps_engine_settings(lv)
        lv.addStretch(1)
        return w

    # Inject / uninject result colours (the diagnostics card's feedback line).
    _DPS_FEEDBACK_OK = "#10B981"
    _DPS_FEEDBACK_WARN = "#F59E0B"
    _DPS_FEEDBACK_ERR = "#EF4444"

    def _set_dps_feedback(self, text: str, color: str = "") -> None:
        """Write the inject/uninject result line on the Live Diagnostics card.

        One place owns text + colour + visibility: the writers used to set the
        text and the colour and then *sometimes* remember `setVisible(True)`,
        so the injector-mode confirmation never appeared at all, and an empty
        line kept a blank row open. `text=""` clears and collapses it.
        """
        fb = getattr(self, "_dps_inject_feedback", None)
        if fb is None:
            return
        fb.setText(text or "")
        if color:
            fb.setStyleSheet(
                f"color: {color}; font-size: 12px; font-weight: 700;")
        fb.setVisible(bool(text))

    def _build_dps_engine_settings(self, v) -> None:
        v.setSpacing(10)

        # 1. Main Section Header
        v.addWidget(C.SectionHeader("Combat DPS Engine", tag="DATA CAPTURE ARCHITECTURE"))

        # A little extra breathing room under the section header.
        v.addSpacing(8)

        # Single compact 3rd-party DLL notice — one line in the DPS header
        # instead of separate boxes on the Option 1 card and Live Diagnostics.
        self._dps_conflict_line = QtWidgets.QLabel("")
        self._dps_conflict_line.setWordWrap(True)
        self._dps_conflict_line.setStyleSheet(
            "font-size: 12px; font-weight: 700; color: #F59E0B;")
        self._dps_conflict_line.setVisible(False)
        v.addWidget(self._dps_conflict_line)

        curr_mode = getattr(self.s, "dps_mode", "proxy")
        self._dps_mode_cards = {}
        self._dps_mode_select_btns = {}
        self._dps_mode_badges = {}

        # Flow layout: the three cards share one row when the window is wide
        # and wrap onto extra rows on narrower windows instead of overflowing
        # horizontally (same behavior as the meter + diagnostics row below).
        cards_flow = StretchFlow()
        cards_flow.setContentsMargins(0, 0, 0, 0)
        cards_flow.setSpacing(12)
        cards_flow.addWidget(self._build_option1_card())
        cards_flow.addWidget(self._build_option2_card())
        cards_flow.addWidget(self._build_option3_card())
        v.addLayout(cards_flow)

        # All three header badges share one width, so they line up across
        # the cards even though the option names differ in length.
        badges = [b for b in (getattr(self, "_dps_mode_badges", None) or {}).values()
                  if b is not None]
        if badges:
            badge_w = max(b.sizeHint().width() for b in badges)
            for b in badges:
                b.setFixedWidth(badge_w)

        # Clear air gap between the option cards and the meter/diagnostics
        # row below (10px layout spacing + 14px extra = ~24px).
        v.addSpacing(14)

        # 4. Meter preferences & live diagnostics, side by side (as before the
        # cards were merged into the DPS tab); they wrap on narrow windows too.
        self._dps_diag_card = self._build_dps_diagnostics_card()
        self._dps_card = self._build_dps_card()
        self._top_dps_card = self._build_top_dps_card()
        self._dummy_card = self._build_dummy_card()
        self._speedrun_card = self._build_speedrun_card()
        btm_flow = StretchFlow()
        btm_flow.setContentsMargins(0, 0, 0, 0)
        btm_flow.setSpacing(12)
        btm_flow.addWidget(self._dps_diag_card)
        btm_flow.addWidget(self._dps_card)
        btm_flow.addWidget(self._top_dps_card)
        btm_flow.addWidget(self._dummy_card)
        btm_flow.addWidget(self._speedrun_card)
        v.addLayout(btm_flow)

        # Wire the selected mode to the live model once the DPS tab is shown.
        # Idempotent on purpose: this runs on every tab BUILD (the shell
        # pre-builds tabs shortly after Settings opens, and page eviction
        # rebuilds them), so an unconditional set_mode here used to reset
        # foe max-HP mid-fight just because the user glanced at Settings
        # (set_mode logs + the tracker reset below fired every time). Only a
        # REAL mode change may reset.
        existing_model = getattr(self, "model", None)
        if existing_model is not None:
            try:
                dm = getattr(existing_model, "damage", None)
            except Exception:
                dm = None
            if dm is not None and hasattr(dm, "set_mode"):
                tracker = getattr(existing_model, "dps", None)
                mode_changed = getattr(dm, "mode", curr_mode) != curr_mode
                dm.set_mode(curr_mode)
                if mode_changed and tracker is not None \
                        and hasattr(tracker, "reset_foe_max_hp"):
                    tracker.reset_foe_max_hp()

        self._update_dps_card_highlights()
        # Defer the first diagnostics refresh a beat after the tab builds so the
        # widgets paint immediately and opening Settings / the DPS tab never
        # blocks: the scan (detect_combat_bridge -> module snapshot + DLL
        # hashes) runs on a CallWorker thread via _request_dps_diag_scan and
        # the repaint lands in _on_dps_diag_scan_done. The live poll below
        # then keeps the card fresh while the tab is on screen. This initial
        # refresh also starts the throttle window (_dps_diag_last_scan),
        # so the first poll tick repaints from this cache instead of scanning
        # twice in a row.
        self._dps_diag_tab_visible = True   # opened / building: treat as on-screen
        QtCore.QTimer.singleShot(0, self._refresh_dps_diagnostics)

        # Live poll timer: repaint diagnostics labels every 1s. The tick is
        # gated on the DPS tab being on screen (see _on_dps_live_diag_tick),
        # so no folder/module scanning ever runs in the background after you
        # leave the Live Diagnostics card — and while you're on it, the full
        # scan is throttled to _DPS_DIAG_SCAN_INTERVAL seconds.
        if not hasattr(self, "_dps_live_diag_timer") or self._dps_live_diag_timer is None:
            self._dps_live_diag_timer = QtCore.QTimer(self if isinstance(self, QtCore.QObject) else None)
            self._dps_live_diag_timer.setInterval(1000)
            self._dps_live_diag_timer.timeout.connect(self._on_dps_live_diag_tick)
            self._dps_live_diag_timer.start()

    def _on_dps_live_diag_tick(self) -> None:
        """1 Hz poll behind Live Diagnostics — only while the DPS tab is
        actually visible. The cheap label repaint (_paint_dps_diagnostics)
        runs every tick from cached scan state; the expensive module-snapshot
        / folder-hash scan (_request_dps_diag_scan) is throttled to
        _DPS_DIAG_SCAN_INTERVAL and re-runs immediately when the tab regains
        focus, so an open tab never stutters on scans.
        """
        tab = (getattr(self, "_settings_tab_pages", None) or {}).get("DPS")
        if tab is None or not _is_valid(tab):
            return   # DPS tab not built yet — nothing to keep live
        try:
            visible = tab.isVisible()
        except (RuntimeError, Exception):
            return
        if not visible:
            self._dps_diag_tab_visible = False
            return
        import time
        was_visible = getattr(self, "_dps_diag_tab_visible", False)
        self._dps_diag_tab_visible = True
        now = time.monotonic()
        last = getattr(self, "_dps_diag_last_scan", 0.0)
        if (not was_visible) or (now - last) >= _DPS_DIAG_SCAN_INTERVAL:
            # Off-thread scan (lands in _on_dps_diag_scan_done, which stamps
            # the throttle window for poll origin). Never stack: while one is
            # in flight the tick just repaints from cache and retries next
            # second.
            worker = getattr(self, "_dps_diag_worker", None)
            if worker is None or not worker.isRunning():
                self._request_dps_diag_scan(origin="poll")
        self._paint_dps_diagnostics()

    def _build_option1_card(self) -> QtWidgets.QFrame:
        from ....core.pe_imports import (PROXY_DLL_INFO, PROXY_DLL_NAMES,
                                         PROXY_RECOMMENDED)

        card = QtWidgets.QFrame()
        card.setObjectName("DpsOptCard1")
        card.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
        card.setCursor(QtCore.Qt.PointingHandCursor)
        card.mousePressEvent = lambda ev: self._set_dps_mode_key("proxy")
        lay = QtWidgets.QVBoxLayout(card)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(8)

        # Header badge carries the card name (kept constant; active state is
        # shown by the card frame + Select button instead).
        badge = QtWidgets.QLabel(self._DPS_MODE_NAMES["proxy"].upper())
        badge.setStyleSheet(
            "font-size: 15px; font-weight: 800; color: #38BDF8; "
            "background: rgba(56, 189, 248, 0.15); border: 1px solid rgba(56, 189, 248, 0.4); "
            "border-radius: 6px; padding: 3px 10px;"
        )
        # Fixed height: without this, the card layout stretches the pill to
        # fill leftover space, making the three badges wildly different sizes.
        badge.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Fixed)
        lay.addWidget(badge, 0, QtCore.Qt.AlignHCenter)
        self._dps_mode_badges["proxy"] = badge

        # One lead line (card accent), then the picker with a line about the
        # current pick under it. All three binaries do the same job, so the only
        # thing worth reading is *whose import loads the chosen one*.
        desc = QtWidgets.QLabel(
            "<span style=\"color:#eac331;font-weight:600;\">Auto-loads on boot "
            "from the game folder.</span> No injector, no helper program."
        )
        desc.setTextFormat(QtCore.Qt.RichText)
        desc.setStyleSheet(f"font-size: 12px; color: {theme.MUTED}; line-height: 1.3;")
        desc.setWordWrap(True)
        lay.addWidget(desc)

        # Proxy DLL Selection row
        sel_row = QtWidgets.QHBoxLayout()
        sel_row.setSpacing(8)
        sel_lbl = QtWidgets.QLabel("Proxy DLL:")
        sel_lbl.setStyleSheet(f"font-size: 12px; font-weight: 700; color: {theme.TEXT};")
        sel_row.addWidget(sel_lbl)

        # Fallback matches config's default (pe_imports.PROXY_RECOMMENDED), so a
        # missing key cannot preselect a proxy with a known crash.
        curr_dll = getattr(self.s, "dps_proxy_dll", PROXY_RECOMMENDED) or PROXY_RECOMMENDED
        self._dps_proxy_combo = QtWidgets.QComboBox()
        self._dps_proxy_combo_normal_style = (
            f"QComboBox {{ background: rgba(255, 255, 255, 0.08); border: 1px solid {theme.BORDER}; "
            f"border-radius: 6px; color: {theme.TEXT}; font-size: 12px; font-weight: 600; padding: 3px 8px; }} "
            f"QComboBox::drop-down {{ border: none; }} "
            f"QComboBox QAbstractItemView {{ background: #181B20; color: {theme.TEXT}; selection-background-color: #38BDF8; selection-color: #000; }}"
        )
        self._dps_proxy_combo_conflict_style = (
            "QComboBox { background: rgba(239, 68, 68, 0.22); border: 2px solid #EF4444; "
            "border-radius: 6px; color: #FFFFFF; font-size: 12px; font-weight: 800; padding: 3px 8px; } "
            "QComboBox::drop-down { border: none; } "
            "QComboBox QAbstractItemView { background: #181B20; color: #FFFFFF; selection-background-color: #EF4444; selection-color: #FFF; }"
        )
        self._dps_proxy_combo.setStyleSheet(self._dps_proxy_combo_normal_style)
        for name in PROXY_DLL_NAMES:
            when = PROXY_DLL_INFO.get(name, ("", ""))[0]
            if name == PROXY_RECOMMENDED:
                tag = "Recommended"
            elif when.startswith("Loads every launch"):
                tag = "Backup"
            else:
                tag = "Last resort"
            self._dps_proxy_combo.addItem(f"{name} — {tag}", name)
        idx = PROXY_DLL_NAMES.index(curr_dll) if curr_dll in PROXY_DLL_NAMES else 0
        self._dps_proxy_combo.setCurrentIndex(idx)
        # Let the combo shrink below its longest item's text width so the card
        # doesn't force the tab wider than the window (the current item elides
        # when narrow; the combo still expands when there is room).
        self._dps_proxy_combo.setSizeAdjustPolicy(
            QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self._dps_proxy_combo.setMinimumContentsLength(6)
        self._dps_proxy_combo.currentIndexChanged.connect(self._on_proxy_dll_changed)
        sel_row.addWidget(self._dps_proxy_combo, 1)
        lay.addLayout(sel_row)

        # One line about the *selected* proxy, not three stacked rows. The three
        # cards share the window width, so a wall of prose about names nobody
        # picked was the tallest thing on the card; the text still comes from
        # pe_imports.PROXY_DLL_INFO, so picker and description cannot disagree.
        self._dps_proxy_desc = QtWidgets.QLabel("")
        self._dps_proxy_desc.setTextFormat(QtCore.Qt.RichText)
        self._dps_proxy_desc.setStyleSheet(
            f"font-size: 12px; color: {theme.MUTED}; line-height: 1.3;")
        self._dps_proxy_desc.setWordWrap(True)
        lay.addWidget(self._dps_proxy_desc)
        self._paint_proxy_desc()   # the label must exist before the first paint

        # The feature list becomes the inline replacement prompt when an
        # installed proxy needs attention; a real button lives beside the text
        # instead of opening a modal dialog.
        features_box = QtWidgets.QWidget()
        features_box.setObjectName("ProxyInlineUpdate")
        features_box.setStyleSheet("background: transparent; border: none;")
        features_row = QtWidgets.QHBoxLayout(features_box)
        features_row.setContentsMargins(0, 0, 0, 0)
        features_row.setSpacing(8)
        self._dps_proxy_features_default = (
            "✓ No external helper programs run\n"
            "✓ 0ms direct engine event streaming\n"
            "✓ Offline & permanent across all game launches\n"
            "✓ Coexists with other mods (a foreign DLL is never overwritten)"
        )
        self._dps_proxy_features_lbl = QtWidgets.QLabel(self._dps_proxy_features_default)
        self._dps_proxy_features_lbl.setStyleSheet(
            f"font-size: 12px; color: {theme.TEXT}; line-height: 1.4;")
        self._dps_proxy_features_lbl.setWordWrap(True)
        features_row.addWidget(self._dps_proxy_features_lbl, 1)
        self._dps_inline_update_btn = QtWidgets.QPushButton("Replace it")
        self._dps_inline_update_btn.setFixedHeight(28)
        self._dps_inline_update_btn.setVisible(False)
        self._dps_inline_update_btn.setStyleSheet(
            "QPushButton { background: rgba(56, 189, 248, 0.12); border: 1px solid rgba(56, 189, 248, 0.4); "
            "border-radius: 6px; color: #38BDF8; font-size: 12px; font-weight: 700; padding: 0 8px; } "
            "QPushButton:hover { background: rgba(56, 189, 248, 0.22); }")
        self._dps_inline_update_btn.clicked.connect(
            lambda: self._install_proxy_dll(_confirmed=True))
        features_row.addWidget(self._dps_inline_update_btn, 0, QtCore.Qt.AlignBottom)
        lay.addWidget(features_box)

        # All three proxy actions share one row; tight padding keeps the card
        # narrow enough for three cards on a wide window.
        actions_row = QtWidgets.QHBoxLayout()
        actions_row.setSpacing(4)

        open_game_btn = QtWidgets.QPushButton("📁 Open")
        open_game_btn.setCursor(QtCore.Qt.PointingHandCursor)
        open_game_btn.setFixedHeight(30)
        open_game_btn.setStyleSheet(
            f"QPushButton {{ background: rgba(255, 255, 255, 0.06); border: 1px solid {theme.BORDER}; "
            f"border-radius: 6px; color: {theme.TEXT}; font-size: 12px; font-weight: 600; padding: 0 6px; }} "
            f"QPushButton:hover {{ background: rgba(255, 255, 255, 0.12); }}"
        )
        open_game_btn.clicked.connect(self._open_farever_game_dir)
        actions_row.addWidget(open_game_btn)

        install_dll_btn = QtWidgets.QPushButton("📋 Install")
        install_dll_btn.setCursor(QtCore.Qt.PointingHandCursor)
        install_dll_btn.setFixedHeight(30)
        install_dll_btn.setStyleSheet(
            f"QPushButton {{ background: rgba(255, 255, 255, 0.06); border: 1px solid {theme.BORDER}; "
            f"border-radius: 6px; color: {theme.TEXT}; font-size: 12px; font-weight: 600; padding: 0 4px; }} "
            f"QPushButton:hover {{ background: rgba(255, 255, 255, 0.12); }}"
        )
        install_dll_btn.clicked.connect(self._install_proxy_dll)
        actions_row.addWidget(install_dll_btn)

        remove_dll_btn = QtWidgets.QPushButton("🗑 Remove")
        remove_dll_btn.setCursor(QtCore.Qt.PointingHandCursor)
        remove_dll_btn.setFixedHeight(30)
        remove_dll_btn.setStyleSheet(
            "QPushButton { background: rgba(239, 68, 68, 0.08); border: 1px solid rgba(239, 68, 68, 0.25); "
            "border-radius: 6px; color: #F87171; font-size: 12px; font-weight: 600; padding: 0 6px; } "
            "QPushButton:hover { background: rgba(239, 68, 68, 0.18); border-color: #EF4444; color: #FECACA; }"
        )
        remove_dll_btn.clicked.connect(self._remove_proxy_dll)
        actions_row.addWidget(remove_dll_btn)
        lay.addLayout(actions_row)

        sel_btn = QtWidgets.QPushButton("Select Option 1")
        sel_btn.setCursor(QtCore.Qt.PointingHandCursor)
        sel_btn.setFixedHeight(36)
        sel_btn.setMinimumWidth(190)   # uniform across the three cards
        sel_btn.clicked.connect(lambda: self._set_dps_mode_key("proxy"))
        lay.addWidget(sel_btn)
        self._dps_mode_select_btns["proxy"] = sel_btn

        self._dps_proxy_feedback = QtWidgets.QLabel("")
        self._dps_proxy_feedback.setStyleSheet(f"font-size: 12px; color: {theme.MUTED}; font-weight: 600;")
        self._dps_proxy_feedback.setWordWrap(True)
        self._dps_proxy_feedback.setVisible(False)
        lay.addWidget(self._dps_proxy_feedback)

        self._dps_mode_cards["proxy"] = card
        return card

    def _paint_proxy_desc(self) -> None:
        """Describe whichever proxy is in the dropdown right now.

        The three names are identical inside - only the importer, and therefore
        *when* it attaches, differs - so one line about the current pick says
        everything the old three-row list did, at a third of the height."""
        from ....core.pe_imports import PROXY_DLL_CAVEATS, PROXY_DLL_INFO
        lbl = getattr(self, "_dps_proxy_desc", None)
        combo = getattr(self, "_dps_proxy_combo", None)
        try:
            if lbl is None or not _is_valid(lbl):
                return
            name = (combo.currentData() if combo is not None else "") or ""
            when, why = PROXY_DLL_INFO.get(name, ("", ""))
            if not when:
                lbl.setVisible(False)
                return
            lbl.setVisible(True)
            # A caveat is the reason NOT to pick the name, so it wears the
            # warning colour and sits outside the muted sentence before it.
            caveat = PROXY_DLL_CAVEATS.get(name, "")
            tail = (f" <span style=\"color:{theme.DANGER};\">{caveat}</span>"
                    if caveat else "")
            lbl.setText(
                f"<span style=\"font-weight:600; color:{theme.TEXT};\">{when}</span>"
                f"<span> — {why}</span>{tail}"
            )
        except Exception:
            pass

    def _on_proxy_dll_changed(self, idx: int) -> None:
        from ....core.pe_imports import PROXY_RECOMMENDED
        self._paint_proxy_desc()
        combo = getattr(self, "_dps_proxy_combo", None)
        if combo is not None:
            chosen = combo.currentData() or PROXY_RECOMMENDED
            self._set("dps_proxy_dll", chosen)
            if hasattr(self, "log"):
                self.log(f"DPS Proxy DLL preference set to {chosen}.")
            self._refresh_dps_diagnostics()

    def _build_option2_card(self) -> QtWidgets.QFrame:
        card = QtWidgets.QFrame()
        card.setObjectName("DpsOptCard2")
        card.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
        card.setCursor(QtCore.Qt.PointingHandCursor)
        card.mousePressEvent = lambda ev: self._set_dps_mode_key("injector")
        lay = QtWidgets.QVBoxLayout(card)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(8)

        # Header badge carries the card name (kept constant; active state is
        # shown by the card frame + Select button instead).
        badge = QtWidgets.QLabel(self._DPS_MODE_NAMES["injector"].upper())
        badge.setStyleSheet(
            "font-size: 15px; font-weight: 800; color: #8B5CF6; "
            "background: rgba(139, 92, 246, 0.15); border: 1px solid rgba(139, 92, 246, 0.4); "
            "border-radius: 6px; padding: 3px 10px;"
        )
        # Fixed height: without this, the card layout stretches the pill to
        # fill leftover space, making the three badges wildly different sizes.
        badge.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Fixed)
        lay.addWidget(badge, 0, QtCore.Qt.AlignHCenter)
        self._dps_mode_badges["injector"] = badge

        # One lead+body block (lead phrase in the card's accent color) keeps
        # all three option cards the same shape: badge, description, features,
        # one action row, Select button.
        desc = QtWidgets.QLabel(
            "<span style=\"color:#8B5CF6;font-weight:600;\">farever_dps.exe "
            "hot-attaches on game start.</span> Leaves your game folder 100% "
            "vanilla — it injects farever_dps.dll into memory when Farever.exe "
            "is detected, guarded to never repeat."
        )
        desc.setTextFormat(QtCore.Qt.RichText)
        desc.setStyleSheet(f"font-size: 12px; color: {theme.MUTED}; line-height: 1.3;")
        desc.setWordWrap(True)
        lay.addWidget(desc)

        feats = QtWidgets.QLabel(
            "✓ Zero files placed in game directory\n"
            "✓ Runs once per process (never spams or loops)\n"
            "✓ Attaches on-the-fly without game restarts"
        )
        feats.setStyleSheet(f"font-size: 12px; color: {theme.TEXT}; line-height: 1.4;")
        feats.setWordWrap(True)
        lay.addWidget(feats)

        # Test Inject was removed: Live Diagnostics' 🧪 Test Hit already
        # verifies the event pipeline end-to-end, so this card keeps only the
        # destructive action — on its own row, mirroring Option 1's Remove DLL.
        uninject_btn = QtWidgets.QPushButton("⚡ Uninject")
        uninject_btn.setCursor(QtCore.Qt.PointingHandCursor)
        uninject_btn.setFixedHeight(30)
        uninject_btn.setStyleSheet(
            "QPushButton { background: rgba(239, 68, 68, 0.08); border: 1px solid rgba(239, 68, 68, 0.25); "
            "border-radius: 6px; color: #F87171; font-size: 12px; font-weight: 600; padding: 0 8px; } "
            "QPushButton:hover { background: rgba(239, 68, 68, 0.18); border-color: #EF4444; color: #FECACA; }"
        )
        uninject_btn.clicked.connect(self._test_uninject_now)
        # Visibility is driven by the diagnostics poll (see
        # _update_dps_card_highlights): the button only means something while
        # farever_dps.dll is actually detected in the running game.
        uninject_btn.setVisible(False)
        self._dps_uninject_btn = uninject_btn
        uninject_row = QtWidgets.QHBoxLayout()
        uninject_row.setSpacing(6)
        uninject_row.addWidget(uninject_btn)
        lay.addLayout(uninject_row)

        sel_btn = QtWidgets.QPushButton("Select Option 2")
        sel_btn.setCursor(QtCore.Qt.PointingHandCursor)
        sel_btn.setFixedHeight(36)
        sel_btn.setMinimumWidth(190)   # uniform across the three cards
        sel_btn.clicked.connect(lambda: self._set_dps_mode_key("injector"))
        lay.addWidget(sel_btn)
        self._dps_mode_select_btns["injector"] = sel_btn

        # No inject/uninject feedback label here: the diagnostics card owns
        # that one (it was built twice, and the second build reassigned the
        # attribute, leaving this copy a widget nothing could ever write to).
        self._dps_mode_cards["injector"] = card
        return card

    def _build_dps_diagnostics_card(self) -> QtWidgets.QFrame:
        card = QtWidgets.QFrame()
        # Standard QFrame#Card rule (PANEL bg + 1px BORDER outline). An inline
        # `border` stylesheet here makes Qt draw a box line around every child
        # QLabel, which reads as messy "inline boxes" around each diagnostic row.
        card.setObjectName("Card")
        lay = QtWidgets.QVBoxLayout(card)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(6)

        lay.addWidget(C.SectionHeader("Live Diagnostics", tag="CONNECTION & IPC STATUS"))

        # Two columns: Status and Active Source get the full-width top rows
        # (the conflict text is the longest line and shouldn't wrap into a
        # tall half-width block); the short rows pair up side by side, and
        # DLL sits next to the feedback cell (which collapses to zero when
        # empty).
        grid = QtWidgets.QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(4)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)

        self._dps_diag_status_lbl = QtWidgets.QLabel("Status: Checking…")
        self._dps_diag_status_lbl.setStyleSheet(f"font-size: 13px; font-weight: 700; color: {theme.TEXT};")
        self._dps_diag_status_lbl.setWordWrap(True)
        grid.addWidget(self._dps_diag_status_lbl, 0, 0, 1, 2)

        self._dps_diag_source_lbl = QtWidgets.QLabel("Active Source: -")
        self._dps_diag_source_lbl.setStyleSheet(f"font-size: 13px; color: {theme.MUTED};")
        self._dps_diag_source_lbl.setWordWrap(True)
        grid.addWidget(self._dps_diag_source_lbl, 1, 0, 1, 2)

        self._dps_diag_port_lbl = QtWidgets.QLabel("UDP Port: 127.0.0.1:49152 (Listening)")
        # EVERY label in this card wraps, this one included. An unwrapped label
        # makes its own text the card's MINIMUM width, and StretchFlow wraps the
        # row when a card's minimum passes its share of the row — so one long
        # status line used to push the meter card under this one. This line was
        # the last exception: a fixed 481px of a 613px card minimum, i.e. the
        # hole stayed open for any writer that ever prefixes or lengthens the
        # port text. Wrapped, its minimum is its longest word instead.
        self._dps_diag_port_lbl.setWordWrap(True)
        self._dps_diag_port_lbl.setStyleSheet(f"font-size: 13px; color: {theme.MUTED};")
        grid.addWidget(self._dps_diag_port_lbl, 2, 0)

        self._dps_diag_pid_lbl = QtWidgets.QLabel("Attached Game: Not attached")
        self._dps_diag_pid_lbl.setStyleSheet(f"font-size: 13px; color: {theme.MUTED};")
        self._dps_diag_pid_lbl.setWordWrap(True)
        grid.addWidget(self._dps_diag_pid_lbl, 2, 1)

        self._dps_diag_dll_lbl = QtWidgets.QLabel("DLL Detection: Checking…")
        self._dps_diag_dll_lbl.setStyleSheet(f"font-size: 13px; color: {theme.MUTED};")
        self._dps_diag_dll_lbl.setWordWrap(True)
        grid.addWidget(self._dps_diag_dll_lbl, 3, 0)

        # The inject/uninject result line. Its own full-width row (a sentence
        # next to "DLL Detection:" would be squeezed into half a card), wrapped
        # so its length never becomes this card's minimum width, and hidden
        # until a writer has something to say (see _set_dps_feedback).
        self._dps_inject_feedback = QtWidgets.QLabel("")
        self._dps_inject_feedback.setStyleSheet("font-size: 12px;")
        self._dps_inject_feedback.setWordWrap(True)
        self._dps_inject_feedback.setVisible(False)
        grid.addWidget(self._dps_inject_feedback, 4, 0, 1, 2)

        lay.addLayout(grid)

        scan_btn = QtWidgets.QPushButton("🔍 Scan Folder")
        scan_btn.setCursor(QtCore.Qt.PointingHandCursor)
        scan_btn.setFixedHeight(28)
        scan_btn.setStyleSheet(
            f"QPushButton {{ background: rgba(255, 255, 255, 0.06); border: 1px solid {theme.BORDER}; "
            f"border-radius: 6px; color: {theme.TEXT}; font-size: 12px; font-weight: 600; padding: 0 10px; }} "
            f"QPushButton:hover {{ background: rgba(255, 255, 255, 0.12); }}"
        )
        scan_btn.clicked.connect(self._scan_farever_game_dir)
        self._dps_diag_scan_btn = scan_btn

        copy_btn = QtWidgets.QPushButton("📋 Copy Diagnostics")
        copy_btn.setCursor(QtCore.Qt.PointingHandCursor)
        copy_btn.setFixedHeight(28)
        copy_btn.setStyleSheet(
            f"QPushButton {{ background: rgba(255, 255, 255, 0.06); border: 1px solid {theme.BORDER}; "
            f"border-radius: 6px; color: {theme.TEXT}; font-size: 12px; font-weight: 600; padding: 0 10px; }} "
            f"QPushButton:hover {{ background: rgba(255, 255, 255, 0.12); }}"
        )
        copy_btn.clicked.connect(self._copy_dps_diagnostics)
        # Keep the button available to the callback for its brief visual
        # confirmation after the clipboard copy succeeds.
        self._dps_diag_copy_btn = copy_btn

        test_btn = QtWidgets.QPushButton("🧪 Test Hit")
        test_btn.setCursor(QtCore.Qt.PointingHandCursor)
        test_btn.setFixedHeight(28)
        test_btn.setStyleSheet(
            f"QPushButton {{ background: rgba(255, 255, 255, 0.06); border: 1px solid {theme.BORDER}; "
            f"border-radius: 6px; color: {theme.TEXT}; font-size: 12px; font-weight: 600; padding: 0 10px; }} "
            f"QPushButton:hover {{ background: rgba(255, 255, 255, 0.12); }}"
        )
        test_btn.clicked.connect(self._inject_test_hit)

        # All diagnostics actions are developer tools: source/run.bat only.
        # The frozen app shows the status card but no action row.
        if not is_frozen():
            btn_row = QtWidgets.QHBoxLayout()
            btn_row.setSpacing(6)
            btn_row.addWidget(scan_btn)
            btn_row.addWidget(copy_btn)
            btn_row.addWidget(test_btn)
            lay.addLayout(btn_row)

        lay.addStretch(1)
        return card

    def _copy_dps_diagnostics(self) -> None:
        """Build a text snapshot of the DPS diagnostics card and copy it to clipboard."""
        dm = getattr(self.model, "damage", None) if hasattr(self, "model") and self.model else None
        pid = getattr(self.model.proc, "pid", None) if hasattr(self, "model") and self.model and hasattr(self.model, "proc") else None
        mode = getattr(self.s, "dps_mode", "proxy")
        hook = getattr(self.s, "dps_hook_enabled", False)

        lines = []
        lines.append("=== FareverPal DPS Diagnostics ===")
        lines.append(f"Mode: {mode.upper()} (" + ("injector hook ON" if hook else "proxy fallback") + ")")
        if pid:
            lines.append(f"Attached Game: Farever.exe (PID {pid})")
        else:
            lines.append("Attached Game: Not attached")

        if dm is not None:
            try:
                st = dm.status()
            except Exception:
                st = "unknown"
            try:
                src = dm.live_source_name()
            except Exception:
                src = "unknown"
            try:
                n, rows = dm.counts()
            except Exception:
                n, rows = 0, 0
            try:
                t = dm.timing_text()
            except Exception:
                t = ""
            lines.append(f"Source: {src}")
            lines.append(f"Status: {st}" + (f" ({t})" if t else ""))
            lines.append(f"Events received: {n}" + (f" (last poll: {rows} rows)" if rows else ""))
            try:
                dp = dm.locate_progress()
                if dp:
                    lines.append(f"Locate progress: {dp}")
            except Exception:
                pass
            try:
                diag = dm.diagnostics()
                if diag:
                    lines.append(f"Reader diagnostics: {diag}")
            except Exception:
                pass
        else:
            lines.append("Source: Not available (model.damage is None)")
            lines.append("Status: offline")

        if hasattr(self, "proc"):
            proc = self.proc
            if proc:
                bridge_fn = getattr(proc, "detect_combat_bridge", None)
                if callable(bridge_fn):
                    try:
                        binfo = bridge_fn()
                        lines.append(f"Bridge detection: {binfo.get('status_summary', binfo)}")
                        _owner = {2: "injector", 1: "proxy"}.get(binfo.get("option"))
                        if binfo.get("detected") and mode == "memory":
                            lines.append(f"Note: {binfo.get('dll_name') or 'bridge DLL'} present but ignored "
                                         f"(Memory Reader uses no DLL)")
                        elif _owner is not None and mode != _owner:
                            lines.append(f"Note: {binfo.get('dll_name') or 'bridge DLL'} still loaded in game "
                                         f"but capture mode is {mode.upper()} - relaunch the game to unload it")
                    except Exception as e:
                        lines.append(f"Bridge detection: error ({e})")

        # The bridge DLL's own log explains a loaded-but-silent bridge, so the
        # snapshot carries its tail: a pasted diagnostic report should not need
        # the user to know a log file exists.
        try:
            lines.extend(self._bridge_log_lines())
        except Exception:
            pass

        text = "\n".join(lines)
        copy_btn = getattr(self, "_dps_diag_copy_btn", None)
        try:
            copied = copy_with_feedback(
                text, copy_btn if copy_btn is not None and _is_valid(copy_btn) else None,
                copied_text="✓ Copied!", restore_text="📋 Copy Diagnostics",
                restore_ms=2000)
            if copied and hasattr(self, "log"):
                self.log("DPS diagnostics copied to clipboard.")
            elif not copied and hasattr(self, "log"):
                self.log("Failed to copy DPS diagnostics.")
        except Exception as e:
            if hasattr(self, "log"):
                self.log(f"Failed to copy DPS diagnostics: {e}")

