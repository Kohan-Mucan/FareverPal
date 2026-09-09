"""Settings module: the Dev tab (developer diagnostics).

This tab exists for a developer run (``run-log.bat``, i.e.
``FAREVER_DEV_CRASH_LOG``) and gathers the switches that used to be
environment variables or file-presence checks, so a session can be explained
without reading a batch file:

* the bridge's own dev log - the ``FAREVERMOD_DEBUG_LOG`` flag beside the
  installed bridge. Every bridge binary re-reads that flag on each log call,
  so the switch applies immediately and removing it stops the writes;
* what this run already has on: verbose Activity Log lines, the crash log
  destination, the experimental-feature gate, the dev icons page.

The shell hides the tab in a normal run (``shell.SettingsShellMixin``), since
none of it applies there. Its builder still works in both modes: the test
suite builds every tab through the same lazy path the page uses.

Imports the single shared namespace (constants, theme, Qt aliases) from
._shared so moved method bodies keep their original globals.
"""

from ._shared import *  # noqa: F401,F403


class SettingsDevMixin:
    """The Dev tab: developer diagnostics for the current run."""

    def _tab_dev(self):
        page, v = self._tab_page()
        v.addWidget(C.SectionHeader("Developer Run", tag="DEV DIAGNOSTICS"))

        self._dev_flag_note = QtWidgets.QLabel("")
        self._dev_flag_note.setWordWrap(True)
        self._dev_flag_note.setStyleSheet(
            f"font-size: 12px; color: {theme.MUTED}; background: transparent;")

        g_dir = self._dev_game_dir()
        tile, sw = self._page_tile(
            "dev_bridge_log", "terminal", "Bridge Dev Log",
            "Writes the flag the bridge binaries look for while logging: with "
            "it they append bridge_dev.log, without it they write nothing. "
            "Re-read on every log call, so it applies immediately.",
            bool(self._dev_flag_path()) and os.path.isfile(self._dev_flag_path()),
            self._on_dev_bridge_log_toggle)
        self._dev_flag_toggle = sw
        if not g_dir:
            sw.setEnabled(False)
        v.addWidget(tile)
        v.addWidget(self._dev_flag_note)

        from ....core import updater
        umode = updater.normalize_update_check(
            getattr(self.s, "check_updates", None))
        useg = C.SegmentedControl(list(updater.UPDATE_CHECK_MODES), current=umode)
        useg.currentChanged.connect(self._on_update_check_mode)
        ut, usw = self._page_tile(
            "check_updates", "download", "Update Check",
            "How the startup update check polls the release oracle, which is "
            "what raises the update pill. Always: even in a source run. "
            "Packaged only: only where a downloaded release can be installed - "
            "the default, and what keeps source runs and the test suite off the "
            "network. Never: no run polls, packaged or not. Changing it applies "
            "to this session right now.",
            control=useg)
        self._dev_update_seg = usw
        v.addWidget(ut)

        v.addWidget(C.SectionHeader("This Run", tag="WHAT IS ALREADY ON"))
        grid = QtWidgets.QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(4)
        grid.setColumnStretch(1, 1)
        for i, (label, value) in enumerate(self._dev_run_rows()):
            name = QtWidgets.QLabel(label)
            name.setStyleSheet(f"font-size: 13px; color: {theme.MUTED};")
            val = QtWidgets.QLabel(value)
            val.setWordWrap(True)
            val.setStyleSheet(f"font-size: 13px; color: {theme.TEXT};")
            grid.addWidget(name, i, 0, QtCore.Qt.AlignTop)
            grid.addWidget(val, i, 1)
        v.addLayout(grid)
        v.addStretch(1)
        self._refresh_dev_tab()
        return page

    # --- the bridge's own log flag ------------------------------------------
    def _dev_game_dir(self) -> str:
        """The game folder the bridge loads from ('' when none is known)."""
        try:
            binfo = getattr(self, "_dps_diag_binfo", None) or {}
            g_dir = binfo.get("game_dir") or self._find_farever_game_dir()
        except Exception:
            return ""
        return str(g_dir or "")

    def _dev_flag_path(self) -> str:
        """Path of the bridge dev-log flag in the game folder ('' if unknown)."""
        from ....core.bridge_log import dev_flag_path
        try:
            return dev_flag_path(self._dev_game_dir())
        except Exception:
            return ""

    def _refresh_dev_tab(self) -> None:
        """Repaint the flag row from disk: the flag is the state, and another
        tool (or the launcher) may have written or removed it since."""
        from ....core.bridge_log import dev_log_path
        g_dir = self._dev_game_dir()
        flag = self._dev_flag_path()
        exists = bool(flag) and os.path.isfile(flag)
        sw = getattr(self, "_dev_flag_toggle", None)
        if sw is not None and _is_valid(sw):
            sw.set_checked_silent(exists)
        upd = getattr(self, "_dev_update_seg", None)
        if upd is not None and _is_valid(upd):
            from ....core import updater
            upd.setCurrentText(updater.normalize_update_check(
                getattr(self.s, "check_updates", None)))
        note = getattr(self, "_dev_flag_note", None)
        if note is None or not _is_valid(note):
            return
        target = ""
        try:
            target = dev_log_path()
        except Exception:
            target = ""
        if not g_dir:
            note.setText("Game folder not found: there is nothing to put the "
                         "flag beside. Scan the folder on the DPS tab first.")
        elif exists:
            where = target or "the path named on the flag's first line"
            note.setText(f"On: flag {flag} -> log {where}. Every bridge binary "
                         "appends there while it exists.")
        else:
            where = (f" It would point at {target}." if target
                     else " No dev-log folder is set for this run.")
            note.setText(f"Off: no flag at {flag}; the bridge writes no log."
                         f"{where}")

    def _on_dev_bridge_log_toggle(self, on: bool) -> None:
        """Write/remove the flag. No Settings field: the flag file IS the
        state, and it lives beside the DLL, not in settings.json."""
        from ....core.bridge_log import remove_dev_flag, write_dev_flag
        g_dir = self._dev_game_dir()
        if not g_dir:
            self.log("Dev: no game folder found - nothing to put the bridge "
                     "log flag beside.")
            self._refresh_dev_tab()
            return
        try:
            if on:
                written = write_dev_flag(g_dir)
                self.log(f"Dev: bridge dev log enabled ({written})" if written
                         else "Dev: could not write the bridge log flag "
                              "(no dev-log path for this run?)")
            else:
                self.log("Dev: bridge dev log disabled"
                         if remove_dev_flag(g_dir)
                         else "Dev: bridge dev log was already off")
        except Exception as exc:  # logging is a diagnostic: never a crash
            self.log(f"Dev: bridge log toggle failed ({exc})")
        self._refresh_dev_tab()

    def _on_update_check_mode(self, mode: str) -> None:
        """Persist the startup update-check MODE and apply it to THIS session.

        The startup gate (in `ControlPanel`) is read once, so a dev picking a
        polling mode would otherwise have to restart before the pill could
        appear; `_start_update_check` is idempotent, so calling it here (when
        the host provides it, and only when this run's mode actually polls) just
        starts the one check now. A mode that does NOT poll starts nothing - and
        does not cancel a request already in flight either: that is one call to
        the oracle, and killing it would leave the pill half-drawn.
        """
        from ....core import updater
        mode = updater.normalize_update_check(mode)
        self._set("check_updates", mode)
        if (updater.should_check(mode, updater.is_frozen())
                and hasattr(self, "_start_update_check")):
            self._start_update_check()
        self.log(f"Dev: startup update check: {mode}.")

    # --- what this run already has on ---------------------------------------
    def _dev_run_rows(self) -> list[tuple[str, str]]:
        """(label, value) for the switches that decide this run's noise."""
        from ....config import experimental_enabled
        from ....core import updater
        from ....core.bridge_log import DEV_LOG_ENV, dev_mode
        from ...control_panel import dev_icons_enabled
        verbose = dev_mode()
        crash = (os.environ.get(DEV_LOG_ENV) or "").strip()
        mode = updater.normalize_update_check(
            getattr(self.s, "check_updates", None))
        polls = updater.should_check(mode, updater.is_frozen())
        return [
            ("Activity Log",
             "Verbose: every bridge stage line is logged, because this run "
             "was started by run-log.bat." if verbose else
             "Normal: failures, the arm summary, canary verdicts and the "
             "damage-pipeline line only. run-log.bat turns the full bridge "
             "stream on."),
            ("Crash log",
             f"{crash} (set by {DEV_LOG_ENV})" if crash
             else f"not set - run-log.bat is what sets {DEV_LOG_ENV}"),
            ("Experimental features",
             "On: FAREVER_EXPERIMENTAL is set." if experimental_enabled()
             else "Off: set FAREVER_EXPERIMENTAL=1 to reveal in-progress "
                  "features."),
            ("Dev icons page",
             "Available: build_tools/dev/ is present, so the page loads by "
             "file path." if dev_icons_enabled() else
             "Not present: the dev-only page lives in build_tools/dev/ and is "
             "never packaged."),
            ("Startup update check",
             f"{mode}: " + (
                 "this run polls the release oracle at startup."
                 if polls else
                 ("a packaged build would poll; this source run does not."
                  if mode == "Packaged only" else
                  "no run polls the release oracle, packaged or not."))),
        ]
