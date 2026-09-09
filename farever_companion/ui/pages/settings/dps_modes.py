"""DPS Settings module - split from ui/pages/settings.py.
Imports the single shared namespace (constants, theme, Qt aliases)
from ._shared so moved method bodies keep their original globals.
"""

from ._shared import *  # noqa: F401,F403


class SettingsDpsModesMixin:
    """Capture-mode switching: mode selection and the option-card highlight
states."""

    def _dps_proxy_blocks_non_proxy(self, mode: str) -> bool:
        """Option 1 is file-backed; never start Option 2/3 beside it.

        An empty diagnostics snapshot means the folder has not been checked
        yet, so the safe answer is locked rather than allowing a stale click to
        inject or advertise a DLL-free memory session.
        """
        if mode == "proxy":
            return False
        binfo = getattr(self, "_dps_diag_binfo", None)
        if not isinstance(binfo, dict) or not binfo:
            return True
        return bool(binfo.get("proxy_in_game_dir")
                    or (binfo.get("detected") and binfo.get("option") == 1))

    def _reject_dps_mode_for_proxy(self, mode: str) -> None:
        self._dps_pending_mode = None
        self._dps_mode_retry_pending = False
        # The disabled Option 2/3 buttons already show the required action;
        # keep the card click quiet and leave only a concise audit line.
        self._set_dps_feedback("")
        if hasattr(self, "log"):
            self.log("DPS mode blocked: remove Option 1 DLL first.")
        self._update_dps_card_highlights()

    def _set_dps_mode_key(self, mode: str) -> None:
        """Switch capture only after diagnostics/native probes have quiesced.

        Live Diagnostics uses process/module inspection off-thread. Starting or
        stopping the bridge while that probe is inside the game process can race
        the injector/uninject path; on Windows that can terminate Farever with
        an access violation. Queue the user choice until the probe is finished,
        and never perform a second synchronous probe on the GUI thread.
        """
        mode = (mode or "proxy").strip().lower()
        if mode not in ("proxy", "injector", "memory"):
            mode = "proxy"
        if self._dps_proxy_blocks_non_proxy(mode):
            self._reject_dps_mode_for_proxy(mode)
            return
        # Persist the user's selection immediately, but delay touching the
        # native capture engine until every diagnostics probe has stopped.
        # Settings should not appear to ignore a click while the safe handoff
        # is waiting for a worker to unwind.
        self._set("dps_mode", mode)
        self._set("dps_hook_enabled", (mode == "injector"))
        worker = getattr(self, "_dps_diag_worker", None)
        live_workers = getattr(self, "_dps_diag_workers", None) or []
        transition_sensitive = (
            getattr(self, "model", None) is not None
            or getattr(self, "proc", None) is not None
        )
        try:
            has_live_scan = transition_sensitive and any(
                w.isRunning() for w in live_workers)
        except RuntimeError:
            has_live_scan = False
        if has_live_scan:
            self._dps_pending_mode = mode
        if worker is None and not has_live_scan:
            has_live_scan = False
        if transition_sensitive and worker is not None:
            try:
                if worker.isRunning():
                    self._dps_pending_mode = mode
                    worker.requestInterruption()
                    if not getattr(self, "_dps_mode_retry_pending", False):
                        self._dps_mode_retry_pending = True
                        QtCore.QTimer.singleShot(100, self._retry_dps_mode_change)
                    if hasattr(self, "log"):
                        self.log("DPS mode change queued until Live Diagnostics finishes safely.")
                    return
            except RuntimeError:
                pass
        self._dps_mode_retry_pending = False
        self._dps_pending_mode = None
        if hasattr(self, "model") and self.model and hasattr(self.model, "damage"):
            self.model.damage.set_mode(mode)
            tracker = getattr(self.model, "dps", None)
            if tracker is not None and hasattr(tracker, "reset_foe_max_hp"):
                tracker.reset_foe_max_hp()
        self._update_dps_card_highlights()
        self._refresh_dps_diagnostics()
        if hasattr(self, "log"):
            opt_n = 1 if mode == "proxy" else 2 if mode == "injector" else 3
            self.log(f"DPS Capture mode changed to Option {opt_n} ({mode.capitalize()}).")
            proc = getattr(self, "proc", None)
            if proc:
                # Use only the latest asynchronous diagnostics result here.
                # Calling detect_combat_bridge synchronously during a live mode
                # transition was an avoidable native-process race.
                binfo = getattr(self, "_dps_diag_binfo", None) or {}
                if binfo:
                    self.log(f"Combat Bridge: {binfo.get('status_summary', '')}")
                elif mode == "proxy":
                    self.log("Combat Bridge: checking Option 1 proxy status in the background…")
                if mode == "injector" and binfo.get("option") != 2:
                    from ...game_attach import _try_launch_hook
                    log_cb = getattr(self, "log", None)
                    self.log(f"Combat Bridge: Auto-launching injector for PID {proc.pid}…")
                    self._connect_dps_inject_result()
                    _try_launch_hook(proc.pid, force=True, log_cb=log_cb,
                                     on_result=self._on_dps_inject_result,
                                     proc=proc, model=getattr(self, "model", None))
                    # Optimistic: the injector has not answered yet, so this
                    # claims only that a command was sent. A refusal revises
                    # the same line (see _on_dps_inject_ui_result).
                    self._dps_inject_failed = False
                    self._set_dps_feedback(
                        f"✓ Inject command sent to PID {proc.pid}",
                        self._DPS_FEEDBACK_OK)

    # Worker -> UI bridge for the injector verdict, the same shape as the
    # uninject path's _uninject_result (see dps_proxy.py): the launcher calls
    # back on its worker thread and Qt forbids touching widgets off the UI
    # thread. Queued delivery also orders the verdict AFTER the optimistic
    # "command sent" line above, so a refusal that lands in milliseconds
    # cannot be overwritten by it.
    _inject_result = QtCore.Signal(bool, str)

    def _connect_dps_inject_result(self) -> None:
        """Wire the injector verdict to the UI thread — once per page."""
        if getattr(self, "_inject_result_connected", False):
            return
        try:
            self._inject_result.connect(self._on_dps_inject_ui_result)
            self._inject_result_connected = True
        except Exception:
            pass

    def _on_dps_inject_result(self, ok: bool, reason: str) -> None:
        """Injector verdict (WORKER thread) -> the model, the log, the card.

        Nothing here may touch a widget: the feedback line is written by
        ``_on_dps_inject_ui_result``, on the UI thread.
        """
        dmg = getattr(getattr(self, "model", None), "damage", None)
        if dmg is not None:
            try:
                dmg.note_inject_result(ok, reason)
            except Exception:
                pass
        if not ok and hasattr(self, "log"):
            self.log(f"Combat Bridge: INJECT FAILED — {reason}.")
        try:
            self._inject_result.emit(ok, reason)
        except Exception:
            pass

    def _on_dps_inject_ui_result(self, ok: bool, reason: str) -> None:
        """Revise the feedback line when the injector refuses (UI thread).

        "Inject command sent" is written before the injector has run, so on a
        refusal the card contradicted the status line right below it — green
        "sent" over red "Inject FAILED" — until something else happened to
        repaint. Only a refusal needs revising: a success already reads
        correctly, and the real attach is the status line's job.
        """
        if ok:
            return
        if getattr(self.s, "dps_mode", "") != "injector":
            # A stale verdict: the injector takes seconds to answer, and the
            # user can move off Option 2 in the meantime, so a failure line
            # here would be about a mode that is no longer selected.
            return
        self._dps_inject_failed = True
        why = reason or "the injector refused"
        self._set_dps_feedback(
            f"✕ Inject failed — {why}. No damage events will be captured; "
            "see the Activity Log.", self._DPS_FEEDBACK_ERR)

    def _clear_dps_inject_failure(self) -> None:
        """Retire a failure line once the model stops reporting that failure.

        A red line is a claim about the present, not a receipt for the past.
        Option 2 retries (see ui/inject_watchdog.py) and the model drops
        ``inject_problem`` the moment the module or its heartbeat shows up
        (core/dps_bridge.status), so without this the card would keep shouting
        a failure that a retry already fixed. Callers only report *that* the
        bridge came back — the claim being retracted is this line's own.
        """
        if not getattr(self, "_dps_inject_failed", False):
            return
        self._dps_inject_failed = False
        self._set_dps_feedback("")

    def _retry_dps_mode_change(self) -> None:
        """Apply a queued mode after the diagnostics worker has fully stopped."""
        self._dps_mode_retry_pending = False
        mode = getattr(self, "_dps_pending_mode", None)
        if not mode:
            return
        live_workers = getattr(self, "_dps_diag_workers", None) or []
        try:
            if any(w.isRunning() for w in live_workers):
                self._dps_mode_retry_pending = True
                QtCore.QTimer.singleShot(100, self._retry_dps_mode_change)
                return
        except RuntimeError:
            pass
        self._set_dps_mode_key(mode)

    def _dps_proxy_installed(self) -> bool:
        """True when Option 1's drop-in proxy is actually present.

        Uses the same hash-verified check as Live Diagnostics: the DLL sitting
        in the game folder must match a FareverPal reference DLL (a 3rd-party
        mod's file that merely shares the name never counts), or the verified
        proxy must already be loaded by the running Farever.exe.
        """
        binfo = getattr(self, "_dps_diag_binfo", None) or {}
        if binfo.get("detected") and binfo.get("option") == 1:
            return True
        return bool(binfo.get("proxy_in_game_dir"))

    def _paint_dps_proxy_build(self) -> None:
        """Proxy identity is shown only in the inline replacement row."""
        return

    def _update_dps_card_highlights(self) -> None:
        self._paint_dps_proxy_build()
        current_mode = getattr(self.s, "dps_mode", "proxy")
        mode_colors = {
            "proxy": "#38BDF8",      # Sky blue
            "injector": "#8B5CF6",   # Violet
            "memory": "#10B981",     # Emerald
        }
        mode_badges = {k: v.upper() for k, v in self._DPS_MODE_NAMES.items()}
        proxy_installed = self._dps_proxy_installed()

        # Option 2's Uninject button only exists when the DLL is actually
        # detected in the running game (option 2 = injector module loaded).
        # Anything else - game closed, another mode's DLL detected - and the
        # button would offer to remove something that is not there.
        binfo = getattr(self, "_dps_diag_binfo", None) or {}
        hook_loaded = bool(binfo.get("detected") and binfo.get("option") == 2)
        uninject_btn = getattr(self, "_dps_uninject_btn", None)
        if uninject_btn is not None and _is_valid(uninject_btn):
            try:
                uninject_btn.setVisible(hook_loaded)
            except Exception:
                pass

        for mode_key, card in (getattr(self, "_dps_mode_cards", None) or {}).items():
            if card is None or not _is_valid(card):
                continue
            is_active = (mode_key == current_mode)
            accent = mode_colors.get(mode_key, "#38BDF8")

            # Update card frame styling with header top highlight
            try:
                if is_active:
                    card.setStyleSheet(
                        f"QFrame#{card.objectName()} {{ background-color: {theme.PANEL}; "
                        f"border: 1px solid {accent}88; border-top: 4px solid {accent}; "
                        f"border-radius: 8px; }}"
                    )
                else:
                    card.setStyleSheet(
                        f"QFrame#{card.objectName()} {{ background-color: {theme.PANEL}; "
                        f"border: 1px solid {theme.BORDER}; border-top: 3px solid rgba(255, 255, 255, 0.14); "
                        f"border-radius: 8px; }} "
                        f"QFrame#{card.objectName()}:hover {{ border-top: 3px solid {accent}; "
                        f"border-color: rgba(255, 255, 255, 0.2); }}"
                    )
            except Exception:
                pass

            # Update header badge
            badge = (getattr(self, "_dps_mode_badges", None) or {}).get(mode_key)
            if badge is not None and _is_valid(badge):
                try:
                    # Badge text stays the card name on every state — the
                    # active engine is indicated by the frame + Select button.
                    badge.setText(mode_badges.get(mode_key, ""))
                    if is_active:
                        badge.setStyleSheet(
                            f"font-size: 15px; font-weight: 800; color: #FFFFFF; "
                            f"background: {accent}; border: 1px solid {accent}; "
                            f"border-radius: 6px; padding: 3px 10px;"
                        )
                    else:
                        badge.setStyleSheet(
                            f"font-size: 15px; font-weight: 800; color: {accent}; "
                            f"background: rgba(255, 255, 255, 0.05); border: 1px solid {accent}44; "
                            f"border-radius: 6px; padding: 3px 10px;"
                        )
                except Exception:
                    pass

            # Update action button. For Option 1 the button mirrors whether
            # the drop-in proxy is actually installed: "✓ ACTIVE MODE" only
            # when a hash-verified FareverPal DLL sits in the game folder (or
            # is live in the running game); with no DLL installed it reads
            # "✗ No DLL Installed" so the card never claims to be active
            # before the proxy can fire.
            btn = (getattr(self, "_dps_mode_select_btns", None) or {}).get(mode_key)
            if btn is not None and _is_valid(btn):
                try:
                    blocked = (mode_key != "proxy"
                               and self._dps_proxy_blocks_non_proxy(mode_key))
                    if blocked:
                        known = bool(getattr(self, "_dps_diag_binfo", None))
                        btn.setText("Remove Option 1 DLL first" if known
                                    else "Checking Farever folder…")
                        btn.setEnabled(False)
                        btn.setStyleSheet(
                            "QPushButton { background: rgba(245, 158, 11, 0.10); "
                            "border: 1px solid rgba(245, 158, 11, 0.45); color: #FBBF24; "
                            "font-weight: 800; border-radius: 6px; font-size: 13px; }"
                        )
                    else:
                        btn.setEnabled(True)
                        if not is_active:
                            opt_num = 1 if mode_key == "proxy" else 2 if mode_key == "injector" else 3
                            btn.setText(f"Select Option {opt_num}")
                            btn.setStyleSheet(
                                f"QPushButton {{ background: rgba(255, 255, 255, 0.06); border: 1px solid {theme.BORDER}; "
                                f"color: {theme.MUTED}; font-weight: 600; border-radius: 6px; font-size: 14px; }} "
                                f"QPushButton:hover {{ background: rgba(255, 255, 255, 0.12); color: {theme.TEXT}; border-color: {accent}; }}"
                            )
                        elif mode_key == "proxy" and not proxy_installed:
                            btn.setText("✗ No DLL Installed")
                            btn.setStyleSheet(
                                "QPushButton { background: rgba(245, 158, 11, 0.16); border: 1px solid #F59E0B; "
                                "color: #FBBF24; font-weight: 800; border-radius: 6px; font-size: 14px; } "
                                "QPushButton:hover { background: rgba(245, 158, 11, 0.30); }"
                            )
                        else:
                            btn.setText("✓ ACTIVE MODE")
                            btn.setStyleSheet(
                                f"QPushButton {{ background: {accent}; border: 1px solid {accent}; "
                                f"color: #FFFFFF; font-weight: 800; border-radius: 6px; font-size: 14px; }}"
                            )
                except Exception:
                    pass

        # Remember the state that affects card styling so the 1 s diagnostics
        # poll only re-styles when readiness or module loading changes. The
        # installed identity has its own cheap painter on completed scans.
        self._last_dps_hl_key = (current_mode, proxy_installed, hook_loaded)

    def _inject_test_hit(self) -> None:
        """Push a fake damage event through the active manager for UI wiring checks."""
        dm = getattr(self.model, "damage", None) if hasattr(self, "model") and self.model else None
        if dm is None:
            if hasattr(self, "log"):
                self.log("Test Hit: cannot inject — model.damage is None (not attached?)")
            return

        from ....core.damage_events import DamageEvent, K_DAMAGE
        import time

        ev = DamageEvent(
            amount=9999.0,
            skill="TestHit",
            skill_name="Test Hit ⚡",
            crit=True,
            kill=False,
            kind=K_DAMAGE,
            source_name="You",
            target_name="Test Dummy",
            is_me=True,
            incoming=False,
            t=time.time(),
        )
        try:
            dm._push([ev])
            if hasattr(self, "log"):
                self.log("Test Hit: injected 9999 dmg → Test Dummy (check overlay)")
        except Exception as e:
            if hasattr(self, "log"):
                self.log(f"Test Hit: injection failed ({e})")

    def _resync_dps_mode_widgets(self) -> None:
        self._update_dps_card_highlights()
        self._refresh_dps_diagnostics()

