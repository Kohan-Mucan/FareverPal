"""DPS Settings module - split from ui/pages/settings.py.
Imports the single shared namespace (constants, theme, Qt aliases)
from ._shared so moved method bodies keep their original globals.
"""

from ._shared import *  # noqa: F401,F403
from ....core.pe_imports import PROXY_RECOMMENDED


class SettingsDpsProxyMixin:
    """Live Diagnostics refresh and the drop-in proxy lifecycle:
game-folder discovery, Install / Remove DLL and scan helpers. The discovery
step lives further down this class rather than in a module of its own - it
was split out only to keep the file under the ui line budget."""

    # --- game-folder discovery -------------------------------------------
    # Finding the Farever install is its own concern (settings cache, Steam
    # registry + libraryfolders, the live process, or a manual pick), separate
    # from what we do once it is found (install / remove the drop-in proxy).

    def _find_farever_game_dir(self) -> str:
        """Find the game installation directory containing Farever.exe.

        Cached in memory and settings to ensure instantaneous repeated lookups.
        """
        import os
        from pathlib import Path

        # 0. In-memory cache (0ms)
        cached = getattr(self, "_cached_farever_game_dir", "")
        if cached and os.path.isdir(cached):
            return cached

        # 1. Check saved setting (0ms)
        saved = getattr(self.s, "farever_game_dir", "")
        if saved and os.path.isdir(saved):
            self._cached_farever_game_dir = saved
            return saved

        # 2. Check standard common Steam install paths on Windows
        common_candidates = [
            r"D:\Steam\steamapps\common\Farever",
            r"C:\Program Files (x86)\Steam\steamapps\common\Farever",
            r"C:\Steam\steamapps\common\Farever",
            r"E:\Steam\steamapps\common\Farever",
            r"F:\Steam\steamapps\common\Farever",
        ]
        for cand in common_candidates:
            if os.path.isdir(cand):
                self._cached_farever_game_dir = cand
                self._set("farever_game_dir", cand)
                return cand

        # 3. Check live process if attached
        proc = getattr(self.model, "proc", None) if hasattr(self, "model") and self.model else getattr(self, "proc", None)
        if proc:
            bfn = getattr(proc, "detect_combat_bridge", None)
            if callable(bfn):
                try:
                    binfo = bfn()
                    g_dir = binfo.get("game_dir", "")
                    if g_dir and os.path.isdir(g_dir):
                        self._cached_farever_game_dir = g_dir
                        self._set("farever_game_dir", g_dir)
                        return g_dir
                except Exception:
                    pass

        # 4. Check Steam via Windows Registry and libraryfolders.vdf
        steam_roots = []
        try:
            import winreg
            for hkey in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                for subkey in (
                    r"Software\Valve\Steam",
                    r"SOFTWARE\WOW6432Node\Valve\Steam",
                ):
                    try:
                        with winreg.OpenKey(hkey, subkey) as key:
                            for val_name in ("SteamPath", "InstallPath"):
                                try:
                                    val, _ = winreg.QueryValueEx(key, val_name)
                                    if val and os.path.isdir(val):
                                        p_val = Path(val)
                                        if p_val not in steam_roots:
                                            steam_roots.append(p_val)
                                except Exception:
                                    pass
                    except Exception:
                        pass
        except Exception:
            pass

        # Check libraryfolders.vdf from detected Steam roots
        all_library_folders = set(steam_roots)
        for s_root in steam_roots:
            vdf_path = s_root / "steamapps" / "libraryfolders.vdf"
            if vdf_path.is_file():
                try:
                    import re
                    text = vdf_path.read_text(encoding="utf-8", errors="ignore")
                    for match in re.finditer(r'"path"\s+"([^"]+)"', text, re.IGNORECASE):
                        raw = match.group(1).replace("\\\\", "\\")
                        lib_dir = Path(raw)
                        if lib_dir.is_dir():
                            all_library_folders.add(lib_dir)
                except Exception:
                    pass

        # Direct existence checks in library folders
        for lib in all_library_folders:
            cand = lib / "steamapps" / "common" / "Farever"
            if cand.is_dir():
                found = str(cand.resolve())
                self._cached_farever_game_dir = found
                self._set("farever_game_dir", found)
                return found

        return ""

    def _scan_farever_game_dir(self) -> None:
        """Manually point the DPS DLL check at the game folder.

        For custom install paths auto-detection can't find (non-standard Steam
        libraries, launchers, symlinks): pick the folder, persist it as
        farever_game_dir and re-run the diagnostics immediately so the DLL
        check uses it. A soft warning appears if the folder doesn't contain
        Farever.exe, but the choice is still saved.
        """
        import os
        start_dir = self._find_farever_game_dir() or "C:\\"
        dlg_dir = QtWidgets.QFileDialog.getExistingDirectory(
            self if isinstance(self, QtWidgets.QWidget) else None,
            "Select Farever Game Folder (contains Farever.exe)",
            start_dir,
        )
        if not dlg_dir or not os.path.isdir(dlg_dir):
            return
        norm = os.path.normpath(dlg_dir)
        # Drop the in-memory cache so the newly saved path is used immediately.
        self._cached_farever_game_dir = ""
        self._set("farever_game_dir", norm)
        if hasattr(self, "log"):
            self.log(f"DPS DLL check folder set to {norm}")
        if not os.path.isfile(os.path.join(norm, "Farever.exe")):
            fb = getattr(self, "_dps_proxy_feedback", None)
            if fb is not None:
                fb.setText(f"ℹ {norm} does not contain Farever.exe — is this the game folder?")
                fb.setStyleSheet("color: #F59E0B; font-size: 12px;")
        self._refresh_dps_diagnostics()

    def _open_farever_game_dir(self) -> None:
        """Open the game install folder in Windows Explorer."""
        import os
        game_dir = self._find_farever_game_dir()
        if not game_dir:
            dlg_dir = QtWidgets.QFileDialog.getExistingDirectory(
                self if isinstance(self, QtWidgets.QWidget) else None,
                "Locate Farever Installation Folder (containing Farever.exe)",
                "C:\\"
            )
            if dlg_dir and os.path.isdir(dlg_dir):
                game_dir = dlg_dir
                self._cached_farever_game_dir = ""
                self._set("farever_game_dir", game_dir)
                if hasattr(self, "log"):
                    self.log(f"Set Farever game folder: {game_dir}")
                self._refresh_dps_diagnostics()
            else:
                try:
                    QtGui.QDesktopServices.openUrl(QtCore.QUrl("steam://nav/games/details/3672400"))
                except Exception:
                    pass
                return

        try:
            norm_path = os.path.normpath(game_dir)
            if os.path.isdir(norm_path):
                if hasattr(os, "startfile"):
                    os.startfile(norm_path)
                else:
                    QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(norm_path))
                if hasattr(self, "log"):
                    self.log(f"Opened game folder: {norm_path}")
                if hasattr(self, "_dps_proxy_feedback") and self._dps_proxy_feedback:
                    self._dps_proxy_feedback.setText(f"📁 Opened: {norm_path}")
                    self._dps_proxy_feedback.setStyleSheet("color: #38BDF8; font-size: 12px;")
        except Exception as e:
            if hasattr(self, "log"):
                self.log(f"Could not open game folder: {e}")

    # Worker -> UI bridge for the background uninject. Qt forbids touching
    # widgets (or starting timers through repaint paths) off the UI thread;
    # doing it directly lagged the whole panel with QBasicTimer warnings.
    _uninject_result = QtCore.Signal(bool, str, object)

    def _refresh_dps_diagnostics(self) -> None:
        """Full refresh: re-scan the game process/folder, then repaint the
        Live Diagnostics card. User actions (install/remove/scan/mode change)
        and the deferred first scan call this directly; the 1 s poll throttles
        the scan half (see _on_dps_live_diag_tick) so an open tab never
        stutters on module snapshots.

        The scan itself runs on a CallWorker thread (see
        _request_dps_diag_scan): this method returns immediately and the
        repaint lands in _on_dps_diag_scan_done. Results and throttle
        semantics are unchanged — only the thread moved."""
        import time
        self._dps_diag_last_scan = time.monotonic()
        # User actions (install/remove/uninject/mode change) must see the new
        # module state immediately, so drop the per-PID snapshot cache before
        # scanning. The throttled poll path (_request_dps_diag_scan with
        # origin="poll") does NOT invalidate, so repeated polls share one
        # snapshot per TTL window. (Dict pop: safe to run inline.)
        try:
            from ....core.proc import invalidate_module_cache
            proc = getattr(self.model, "proc", None) if hasattr(self, "model") and self.model else getattr(self, "proc", None)
            pid = getattr(proc, "pid", None) if proc else None
            if pid:
                invalidate_module_cache(pid)
        except Exception:
            pass
        self._request_dps_diag_scan(origin="direct")

    def _paint_dps_diagnostics(self) -> None:
        """Cheap half of the refresh: repaint the Live Diagnostics card from
        the cached scan state (_dps_diag_binfo) and live model status. No
        process snapshot, no disk hashing — safe to run every 1 s tick."""
        dm = getattr(self.model, "damage", None) if hasattr(self, "model") and self.model else None
        pid = getattr(self.model.proc, "pid", None) if hasattr(self, "model") and self.model and hasattr(self.model, "proc") else None
        mode = getattr(self.s, "dps_mode", "proxy")
        binfo = getattr(self, "_dps_diag_binfo", None) or {}
        g_dir = binfo.get("game_dir") or self._find_farever_game_dir()
        if binfo.get("farevermod_update_available"):
            update_dll = (str(binfo.get("farevermod_installed_dll")
                              or binfo.get("dll_name")
                              or getattr(self.s, "dps_proxy_dll", PROXY_RECOMMENDED)))
            self._show_inline_proxy_update(
                False, update_dll,
                str(binfo.get("farevermod_installed_version") or "unknown"),
                str(binfo.get("farevermod_current_version") or "unknown"))
        else:
            self._hide_inline_proxy_update()

        pid_lbl = getattr(self, "_dps_diag_pid_lbl", None)
        if pid_lbl is not None and _is_valid(pid_lbl):
            try:
                if pid:
                    pid_lbl.setText(f"Attached Game: Farever.exe (PID {pid})")
                    pid_lbl.setStyleSheet(f"font-size: 13px; color: {theme.GOOD}; font-weight: 600;")
                else:
                    pid_lbl.setText("Attached Game: Not attached")
                    pid_lbl.setStyleSheet(f"font-size: 12px; color: {theme.MUTED};")
            except Exception:
                pass

        tp_dinput = bool(binfo.get("third_party_dinput8"))
        tp_version = bool(binfo.get("third_party_version"))
        pref_dll = getattr(self.s, "dps_proxy_dll", PROXY_RECOMMENDED) or PROXY_RECOMMENDED

        tp_info_parts = []
        if tp_dinput:
            tp_info_parts.append("dinput8.dll")
        if tp_version:
            tp_info_parts.append("version.dll")
        tp_summary = " and ".join(tp_info_parts)

        has_active_conflict = False
        if tp_dinput and tp_version:
            has_active_conflict = True
        elif tp_dinput and pref_dll == "dinput8.dll":
            has_active_conflict = True
        elif tp_version and pref_dll == "version.dll":
            has_active_conflict = True

        combo = getattr(self, "_dps_proxy_combo", None)
        if combo is not None and _is_valid(combo):
            normal_st = getattr(self, "_dps_proxy_combo_normal_style", "")
            conflict_st = getattr(self, "_dps_proxy_combo_conflict_style", "")
            if has_active_conflict and conflict_st:
                combo.setStyleSheet(conflict_st)
            elif normal_st:
                combo.setStyleSheet(normal_st)

        dll_lbl = getattr(self, "_dps_diag_dll_lbl", None)
        if dll_lbl is not None and _is_valid(dll_lbl):
            try:
                # Option 3 never loads a DLL: a mapped module is a leftover
                # from an earlier injector session (the capture engine for it
                # is stopped on mode switch), so report it as ignored — never
                # as something the memory capture is using.
                if mode == "memory":
                    if binfo.get("detected"):
                        dll_lbl.setText(
                            f"DLL: ○ {binfo.get('dll_name')} present but ignored "
                            f"(Memory Reader uses no DLL)")
                        dll_lbl.setStyleSheet("font-size: 13px; color: #F59E0B; font-weight: 700;")
                    elif pid:
                        dll_lbl.setText("DLL: ○ None in process (not needed — memory mode)")
                        dll_lbl.setStyleSheet(f"font-size: 13px; color: {theme.MUTED};")
                    else:
                        dll_lbl.setText("DLL: ○ Not attached")
                        dll_lbl.setStyleSheet(f"font-size: 13px; color: {theme.MUTED};")
                else:
                    # A mapped DLL the active bridge mode does NOT use (e.g.
                    # injected on Option 2, then switched to Option 1): warn
                    # plainly instead of green "Active" below.
                    _dll_owner = {2: "injector", 1: "proxy"}.get(binfo.get("option"))
                    if binfo.get("detected") and _dll_owner is not None and mode != _dll_owner:
                        dll_lbl.setText(
                            f"DLL: ! {binfo.get('dll_name')} still loaded "
                            f"(capture: {mode}) - relaunch game to unload")
                        dll_lbl.setStyleSheet("font-size: 13px; color: #F59E0B; font-weight: 700;")
                    elif binfo.get("detected"):
                        dll_lbl.setText(f"DLL: ● {binfo.get('dll_name')} (Active)")
                        dll_lbl.setStyleSheet(f"font-size: 13px; color: {theme.GOOD}; font-weight: 600;")
                    elif binfo.get("proxy_in_game_dir"):
                        if tp_dinput or tp_version:
                            dll_lbl.setText(f"DLL: ● {binfo.get('dll_name')} (FareverPal) + 3rd-party {tp_summary}")
                            dll_lbl.setStyleSheet("font-size: 13px; color: #38BDF8; font-weight: 700;")
                        else:
                            # Status already reports READY with the same DLL;
                            # don't repeat it in the separate DLL row.
                            dll_lbl.setText("")
                            dll_lbl.setStyleSheet("font-size: 13px; color: #38BDF8; font-weight: 600;")
                    elif tp_dinput or tp_version:
                        if has_active_conflict:
                            # version.dll stopped shipping (2026-09-24): the
                            # only remaining alt for an occupied dinput8 is
                            # userenv.dll, and an occupied version.dll has no
                            # alt left - Options 2/3 are the answer there.
                            alt_suggest = ("userenv.dll" if tp_dinput else None)
                            if alt_suggest:
                                dll_lbl.setText(f"DLL: 🚨 3rd-party {tp_summary} in folder (Conflict — switch to {alt_suggest})")
                            else:
                                dll_lbl.setText(f"DLL: 🚨 3rd-party {tp_summary} in folder (Conflict — use Option 2 or 3)")
                            dll_lbl.setStyleSheet("font-size: 13px; color: #EF4444; font-weight: 800;")
                        else:
                            dll_lbl.setText(f"DLL: ℹ 3rd-party {tp_summary} in folder (Coexisting via {pref_dll})")
                            dll_lbl.setStyleSheet("font-size: 13px; color: #38BDF8; font-weight: 700;")
                    elif pid:
                        dll_lbl.setText("DLL: ○ None detected in process")
                        dll_lbl.setStyleSheet(f"font-size: 13px; color: {theme.MUTED};")
                    else:
                        dll_lbl.setText("DLL: ○ Not attached")
                        dll_lbl.setStyleSheet(f"font-size: 13px; color: {theme.MUTED};")
            except Exception:
                pass

        src_lbl = getattr(self, "_dps_diag_source_lbl", None)
        if src_lbl is not None and _is_valid(src_lbl):
            try:
                src_name = getattr(dm, "live_source_name", lambda: f"Option: {mode}")() if dm else f"Option: {mode}"
                src_lbl.setText(f"Active Source: {src_name}")
            except Exception:
                pass

        status_lbl = getattr(self, "_dps_diag_status_lbl", None)
        if status_lbl is not None and _is_valid(status_lbl):
            try:
                st = getattr(dm, "status", lambda: "ready")() if dm else "ready"
                # A red "Inject failed" line is a claim about the present, and
                # these two states are exactly the ones in which the model has
                # stopped reporting the problem (a watchdog retry attached the
                # DLL, or events started arriving) — so it retires with them
                # instead of shouting beside a live status.
                if st in ("live", "bridge_ready"):
                    self._clear_dps_inject_failure()
                if has_active_conflict:
                    which_tp = "both proxy DLLs" if (tp_dinput and tp_version) else ("dinput8.dll" if tp_dinput else "version.dll")
                    status_lbl.setText(f"Status: 🚨 Mod conflict: another mod uses {which_tp}")
                    status_lbl.setStyleSheet("font-size: 13px; font-weight: 700; color: #EF4444;")
                elif st == "live":
                    status_lbl.setText("Status: ● LIVE (Streaming Hits)")
                    status_lbl.setStyleSheet("font-size: 13px; font-weight: 700; color: #10B981;")
                elif binfo.get("detected") and mode != "memory":
                    # Bridge modes only: in memory mode a leftover mapped DLL
                    # is stopped and ignored, so it must never read as ACTIVE.
                    status_lbl.setText(f"Status: ● ACTIVE IN GAME ({binfo.get('dll_name')})")
                    status_lbl.setStyleSheet("font-size: 13px; font-weight: 700; color: #10B981;")
                elif binfo.get("proxy_in_game_dir") and mode != "memory":
                    # Bridge modes only: a proxy DLL on disk means nothing
                    # while the Memory Reader owns the capture.
                    if tp_dinput or tp_version:
                        status_lbl.setText(f"Status: ● READY ({binfo.get('dll_name')} in folder + 3rd-party mod preserved)")
                        status_lbl.setStyleSheet("font-size: 13px; font-weight: 700; color: #10B981;")
                    else:
                        status_lbl.setText(f"Status: ● READY ({binfo.get('dll_name')} in game folder)")
                        status_lbl.setStyleSheet("font-size: 13px; font-weight: 700; color: #10B981;")
                elif tp_dinput or tp_version:
                    which_tp = "dinput8.dll" if tp_dinput else "version.dll"
                    alt_needed = "userenv.dll" if tp_dinput else None
                    if alt_needed:
                        status_lbl.setText(f"Status: ⚠ 3rd-party {which_tp} in folder (Click 'Install DLL' to add {alt_needed})")
                    else:
                        status_lbl.setText(f"Status: ⚠ 3rd-party {which_tp} in folder (Use Option 2 or 3)")
                    status_lbl.setStyleSheet("font-size: 13px; font-weight: 700; color: #F59E0B;")
                elif st == "missing_proxy":
                    target_dll = getattr(self.s, "dps_proxy_dll", PROXY_RECOMMENDED) or PROXY_RECOMMENDED
                    status_lbl.setText(f"Status: ⚠ {target_dll} not in game folder")
                    status_lbl.setStyleSheet("font-size: 13px; font-weight: 700; color: #F59E0B;")
                elif st == "need_inject":
                    status_lbl.setText("Status: ⚠ farever_dps.dll not injected")
                    status_lbl.setStyleSheet("font-size: 13px; font-weight: 700; color: #F59E0B;")
                elif st == "inject_failed":
                    why = (getattr(getattr(dm, "bridge", None), "inject_problem", "")
                           or "the injector refused")
                    status_lbl.setText(f"Status: ⚠ Inject FAILED — {why}")
                    status_lbl.setStyleSheet("font-size: 13px; font-weight: 700; color: #EF4444;")
                elif st == "locating":
                    try:
                        prog = dm.locate_progress() if dm is not None else ""
                    except Exception:
                        prog = ""
                    tail = f" ({prog})" if prog else ""
                    status_lbl.setText(f"Status: locating damage type{tail}")
                    status_lbl.setStyleSheet("font-size: 13px; font-weight: 700; color: #F59E0B;")
                elif st == "mapping":
                    status_lbl.setText("Status: mapping damage range - attack an enemy to finish")
                    status_lbl.setStyleSheet("font-size: 13px; font-weight: 700; color: #F59E0B;")
                else:
                    status_lbl.setText("Status: Standing by")
                    status_lbl.setStyleSheet(f"font-size: 13px; font-weight: 700; color: {theme.MUTED};")
            except Exception:
                pass

        # Single compact status notice in the DPS header.
        conflict_line = getattr(self, "_dps_conflict_line", None)
        if conflict_line is not None and _is_valid(conflict_line):
            try:
                if binfo.get("farevermod_update_available"):
                    # The installed/new DLL text is rendered in the feature row;
                    # don't repeat the same update notice in the header.
                    conflict_line.setVisible(False)
                elif tp_dinput or tp_version:
                    if tp_dinput and tp_version:
                        conflict_line.setText(
                            "🚨 Other mods use both version.dll and dinput8.dll in the game folder — "
                            "use Option 2 (Injector) or Option 3 (Memory Reader)."
                        )
                        conflict_line.setStyleSheet(
                            "font-size: 12px; font-weight: 700; color: #F59E0B;")
                    elif has_active_conflict:
                        alt_suggest = "userenv.dll" if tp_dinput else None
                        if alt_suggest:
                            conflict_line.setText(
                                f"🚨 Another mod uses {tp_summary} in the game folder — "
                                f"switch the Proxy DLL to {alt_suggest} or use Option 2 / 3."
                            )
                        else:
                            conflict_line.setText(
                                f"🚨 Another mod uses {tp_summary} in the game folder — "
                                "use Option 2 (Injector) or Option 3 (Memory Reader)."
                            )
                        conflict_line.setStyleSheet(
                            "font-size: 12px; font-weight: 700; color: #F59E0B;")
                    else:
                        conflict_line.setText(
                            f"ℹ 3rd-party {tp_summary} found in the game folder — "
                            f"FareverPal is safely on {pref_dll}."
                        )
                        conflict_line.setStyleSheet(
                            "font-size: 12px; font-weight: 700; color: #38BDF8;")
                    conflict_line.setVisible(True)
                else:
                    conflict_line.setVisible(False)
            except Exception:
                pass

        # Log to Activity Log once when a 3rd-party DLL is detected
        if tp_dinput or tp_version:
            if not getattr(self, "_logged_conflict_disk", False):
                self._logged_conflict_disk = True
                which = "dinput8.dll and version.dll" if (tp_dinput and tp_version) else ("dinput8.dll" if tp_dinput else "version.dll")
                if hasattr(self, "log"):
                    self.log(f"Combat Bridge: 🚨 Detected another mod using {which} in game directory ({g_dir})")
        else:
            self._logged_conflict_disk = False

        # Keep the Option 1 select button honest: it shows "✓ ACTIVE MODE"
        # only when a FareverPal proxy DLL is actually in the game folder (or
        # already loaded by the running game), otherwise "✗ No DLL Installed".
        # Re-style the cards only when that readiness (or Option 2's
        # hook-loaded state, which drives the Uninject button) changed, so the
        # 1 s poll never churns stylesheets for an unchanged state.
        hl_key = (mode, self._dps_proxy_installed(),
                  bool(binfo.get("detected") and binfo.get("option") == 2))
        if getattr(self, "_last_dps_hl_key", None) != hl_key:
            self._update_dps_card_highlights()

        # (The bridge DLL's own log tail is NOT painted on the card any more -
        # a raw C log blob made the diagnostics card unreadable. It still ships
        # in "Copy Diagnostics" via _bridge_log_lines, so a pasted report keeps
        # the loaded-but-not-arming explanation.)


    def _show_inline_proxy_update(
            self, same_build: bool, dll_name: str, installed_release: str,
            current_release: str, game_running: bool = False) -> None:
        """Show replacement confirmation in the feature row, not a modal."""
        if same_build:
            installed = (f"{dll_name} v{installed_release}"
                         if installed_release != "unknown" else
                         f"{dll_name} legacy")
            text = f"Installed: {installed}"
            button_text = "Reinstall it"
        else:
            installed = (f"{dll_name} v{installed_release}"
                         if installed_release != "unknown" else
                         f"{dll_name} legacy")
            new_dll = (f"{dll_name} v{current_release}"
                       if current_release != "unknown" else
                       f"{dll_name} (version unavailable)")
            text = f"⚠ Old DLL: {installed}\n  Install new: {new_dll}"
            button_text = "Replace it"
        if game_running:
            text += "\n\nFarever.exe must be fully closed before replacing this DLL."
        label = getattr(self, "_dps_proxy_features_lbl", None)
        if label is not None and _is_valid(label):
            label.setText(text)
            label.setStyleSheet(
                "font-size: 12px; color: #F59E0B; font-weight: 700; line-height: 1.4;")
        button = getattr(self, "_dps_inline_update_btn", None)
        if button is not None and _is_valid(button):
            button.setText(button_text)
            button.setVisible(True)

    def _hide_inline_proxy_update(self) -> None:
        label = getattr(self, "_dps_proxy_features_lbl", None)
        if label is not None and _is_valid(label):
            label.setText(getattr(self, "_dps_proxy_features_default", ""))
        button = getattr(self, "_dps_inline_update_btn", None)
        if button is not None and _is_valid(button):
            button.setVisible(False)

    def _install_proxy_dll(self, _confirmed: bool = False) -> None:
        """Copy the chosen proxy DLL (pe_imports.PROXY_DLL_NAMES) into the game folder."""
        import os
        import shutil
        from pathlib import Path
        from ....core.proc import (
            is_farever_proxy, get_bridge_dir,
            get_farevermod_proxy_identity, sync_bridge_dev_flag,
        )
        from ....core.bridge_log import snapshot_if_bridge_log
        from ....core.pe_imports import (proxy_flag_name, proxy_grace_name,
                                         proxy_log_name)

        chosen_dll = getattr(self.s, "dps_proxy_dll", PROXY_RECOMMENDED) or PROXY_RECOMMENDED

        b_dir = get_bridge_dir()
        src_dll = Path(b_dir) / chosen_dll if b_dir else None

        if not src_dll or not src_dll.is_file():
            if hasattr(self, "_dps_proxy_feedback") and self._dps_proxy_feedback:
                self._dps_proxy_feedback.setText(
                    f"⚠ Source {chosen_dll} not found in {b_dir or 'dps_bridge/'}"
                    + " — rebuild it with GameFiles\\Farever\\hooks\\build_all.bat.")
                self._dps_proxy_feedback.setStyleSheet("color: #EF4444; font-size: 12px;")
            if hasattr(self, "log"):
                self.log(f"Install Proxy: source {chosen_dll} not found in {b_dir or 'dps_bridge/'} "
                         "— rebuild it with GameFiles\\Farever\\hooks\\build_all.bat")
            return

        game_dir = self._find_farever_game_dir()
        if not game_dir:
            dlg_dir = QtWidgets.QFileDialog.getExistingDirectory(
                self if isinstance(self, QtWidgets.QWidget) else None,
                f"Select Farever Folder to copy {chosen_dll} into",
                "C:\\"
            )
            if dlg_dir and os.path.isdir(dlg_dir):
                game_dir = dlg_dir
                self._set("farever_game_dir", game_dir)
            else:
                if hasattr(self, "_dps_proxy_feedback") and self._dps_proxy_feedback:
                    self._dps_proxy_feedback.setText("⚠ Game folder not found. Click 'Open Folder' to locate.")
                    self._dps_proxy_feedback.setStyleSheet("color: #F59E0B; font-size: 12px;")
                return

        # Would Windows even load this name? Only if the game imports it, and
        # that is readable from the game's own PE headers - so the user learns
        # it here instead of after a wasted game launch. `None` means unknown
        # (headers unreadable), never "no".
        proxy_loadable: bool | None = None
        proxy_importers: list[str] = []
        try:
            from ....core.pe_imports import (
                clear_cache as _pe_clear, proxy_import_map,
            )
            _pe_clear()
            import_map = proxy_import_map(game_dir)
            known_proxies = {n for n, mods in import_map.items() if mods}
            if known_proxies:
                proxy_loadable = chosen_dll in known_proxies
            proxy_importers = import_map.get(chosen_dll) or []
        except Exception:
            proxy_loadable = None

        # Existing marked FareverMod binaries are ours, but replacement is
        # ALWAYS explicit. The confirmation lives in the feature row, not in
        # a modal popup.
        dst_target = Path(game_dir) / chosen_dll
        installed_id = get_farevermod_proxy_identity(str(dst_target))
        current_id = get_farevermod_proxy_identity(str(src_dll))
        if dst_target.is_file() and is_farever_proxy(str(dst_target)):
            import hashlib
            try:
                src_hash = hashlib.sha256(src_dll.read_bytes()).hexdigest()
                dst_hash = hashlib.sha256(dst_target.read_bytes()).hexdigest()
            except OSError:
                src_hash = dst_hash = ""
            same_build = bool(src_hash and dst_hash and src_hash == dst_hash)
            installed_release = installed_id.get(
                "FAREVERMOD_PROXY_VERSION", "unknown")
            current_release = current_id.get(
                "FAREVERMOD_PROXY_VERSION", "unknown")
            try:
                from ....core.proc import find_pid
                game_pid = find_pid("Farever.exe") or find_pid("farever.exe")
            except Exception:
                game_pid = None
            if not _confirmed:
                self._show_inline_proxy_update(
                    same_build, chosen_dll, installed_release, current_release,
                    game_running=bool(game_pid))
                return
            if game_pid:
                self._show_inline_proxy_update(
                    same_build, chosen_dll, installed_release, current_release,
                    game_running=True)
                return

        # Conflict check: an unmarked DLL belongs to another mod and is never
        # overwritten by FareverMod. No auto-pick: the other mod is probably
        # doing the same kind of thing (hooks/overlays/its own bridge), so the
        # right move is the user removing one of the two, not this app adding
        # a second hook DLL under a different name.
        if dst_target.is_file() and not is_farever_proxy(str(dst_target)):
            err = (f"⚠ Another mod is using {chosen_dll} in this folder! "
                   "Overwriting it would break your other mod. Remove that "
                   "mod first if you want to use the FareverMod proxy.")
            if hasattr(self, "_dps_proxy_feedback") and self._dps_proxy_feedback:
                self._dps_proxy_feedback.setText(err)
                self._dps_proxy_feedback.setStyleSheet("color: #EF4444; font-size: 12px; font-weight: 700;")
            if hasattr(self, "log"):
                self.log(f"Combat Bridge: {err}")
            self._refresh_dps_diagnostics()
            return

        try:
            # Clean up OUR other proxies so a switch never leaves a dead DLL
            # behind (a proxy the game does not import is pure clutter).
            # A file still mapped by the running game stays (Windows locks it).
            for other_name in ("dinput8.dll", "userenv.dll",
                               "version.dll"):
                if other_name == chosen_dll:
                    continue
                other_path = Path(game_dir) / other_name
                if other_path.is_file() and is_farever_proxy(str(other_path)):
                    try:
                        other_path.unlink()
                        if hasattr(self, "log"):
                            self.log(f"Combat Bridge: Cleaned up previous Farever proxy ({other_name})")
                    except Exception:
                        pass
                # ...and what it left next to itself. A stale
                # <name>_proxy.log is not just clutter: Live Diagnostics quotes
                # the bridge's own log, so a file from the proxy we just left
                # would explain the wrong session. The flag/grace files are
                # diagnostics of that proxy too.
                for stale in (proxy_log_name(other_name),
                              proxy_flag_name(other_name),
                              proxy_grace_name(other_name)):
                    stale_path = Path(game_dir) / stale
                    try:
                        if stale_path.is_file():
                            snapshot_if_bridge_log(str(stale_path))
                            stale_path.unlink()
                            if hasattr(self, "log"):
                                self.log(f"Combat Bridge: Cleaned up {stale} "
                                         f"(belonged to {other_name})")
                    except Exception:
                        pass

            dst_dll = Path(game_dir) / chosen_dll
            shutil.copy2(str(src_dll), str(dst_dll))
            self._hide_inline_proxy_update()
            # Dev-only bridge logging: the freshly installed proxy reads the
            # flag sitting next to itself, and only in a run-log.bat session
            # (sync writes the flag in dev mode, clears leftovers otherwise).
            sync_bridge_dev_flag(game_dir)
            if proxy_loadable is False:
                # Installed, but provably unloadable: Windows only loads a
                # game-directory DLL whose name the game imports.
                msg = (
                    f"⚠ Installed {chosen_dll}, but Farever never imports it, so "
                    "Windows will not load it. Install dinput8.dll instead "
                    "(the game's DirectX binding imports it)."
                )
                if hasattr(self, "_dps_proxy_feedback") and self._dps_proxy_feedback:
                    self._dps_proxy_feedback.setText(msg)
                    self._dps_proxy_feedback.setStyleSheet("color: #F59E0B; font-size: 12px; font-weight: 700;")
            else:
                via = (f" Loaded by {', '.join(proxy_importers[:2])}."
                       if proxy_importers else "")
                msg = (f"✓ Installed {chosen_dll} to game folder! "
                       f"Restart Farever.exe to activate.{via}")
                if hasattr(self, "_dps_proxy_feedback") and self._dps_proxy_feedback:
                    self._dps_proxy_feedback.setText(msg)
                    self._dps_proxy_feedback.setStyleSheet("color: #10B981; font-size: 12px; font-weight: 700;")
            if hasattr(self, "log"):
                self.log(f"Combat Bridge: {msg} ({dst_dll})")
            self._refresh_dps_diagnostics()
        except PermissionError:
            err = f"Farever.exe is running and locking {chosen_dll}. Please close the game first, then click Install DLL."
            if hasattr(self, "_dps_proxy_feedback") and self._dps_proxy_feedback:
                self._dps_proxy_feedback.setText(f"⚠ {err}")
                self._dps_proxy_feedback.setStyleSheet("color: #EF4444; font-size: 12px;")
            if hasattr(self, "log"):
                self.log(f"Combat Bridge: {err}")
        except Exception as e:
            err = f"Failed to copy {chosen_dll}: {e}"
            if hasattr(self, "_dps_proxy_feedback") and self._dps_proxy_feedback:
                self._dps_proxy_feedback.setText(f"⚠ {err}")
                self._dps_proxy_feedback.setStyleSheet("color: #EF4444; font-size: 12px;")
            if hasattr(self, "log"):
                self.log(f"Combat Bridge: {err}")

    def _remove_proxy_dll(self) -> None:
        """Remove FareverPal proxy DLL from the Farever game folder (preserves 3rd-party mods)."""
        import os
        from pathlib import Path
        from ....core.proc import is_farever_proxy, find_pid

        game_dir = self._find_farever_game_dir()
        if not game_dir:
            dlg_dir = QtWidgets.QFileDialog.getExistingDirectory(
                self if isinstance(self, QtWidgets.QWidget) else None,
                "Select Farever Folder to remove proxy DLL from",
                "C:\\"
            )
            if dlg_dir and os.path.isdir(dlg_dir):
                game_dir = dlg_dir
                self._set("farever_game_dir", game_dir)
            else:
                if hasattr(self, "_dps_proxy_feedback") and self._dps_proxy_feedback:
                    self._dps_proxy_feedback.setText("⚠ Game folder not found.")
                    self._dps_proxy_feedback.setStyleSheet("color: #F59E0B; font-size: 12px;")
                return

        from ....core.bridge_log import remove_dev_flag, snapshot_if_bridge_log
        from ....core.pe_imports import (proxy_flag_name, proxy_grace_name,
                                         proxy_log_name)

        candidates = ["dinput8.dll", "userenv.dll", "version.dll"]
        farever_files_to_remove: list[Path] = []
        third_party_files: list[str] = []

        # Everything else of ours goes with the DLLs: the diagnostic files (a
        # forgotten forward-only flag leaves the bridge inert, a grace override
        # changes when it starts) and the bridge logs, since Live Diagnostics
        # quotes the active mode's log and a leftover from the proxy we are
        # removing would explain the wrong session.
        # The dev logging flag is shared by every name, so it goes once with
        # all of them - leaving it behind would make a later normal run log.
        remove_dev_flag(game_dir)
        for cand in candidates:
            for name in (proxy_flag_name(cand), proxy_grace_name(cand),
                         proxy_log_name(cand)):
                path = Path(game_dir) / name
                try:
                    if path.is_file():
                        snapshot_if_bridge_log(str(path))
                        path.unlink()
                        if hasattr(self, "log"):
                            self.log(f"Combat Bridge: Removed {name} "
                                     f"(belongs to the proxy being removed).")
                except Exception:
                    pass

        for cand in candidates:
            cand_path = Path(game_dir) / cand
            if cand_path.is_file():
                if is_farever_proxy(str(cand_path)):
                    farever_files_to_remove.append(cand_path)
                else:
                    third_party_files.append(cand)

        if not farever_files_to_remove:
            if third_party_files:
                msg = f"ℹ No FareverPal proxy found. Preserved 3rd-party mod: {', '.join(third_party_files)}."
            else:
                msg = "ℹ No FareverPal proxy DLL found in the game folder (already clean)."
            if hasattr(self, "_dps_proxy_feedback") and self._dps_proxy_feedback:
                self._dps_proxy_feedback.setText(msg)
                self._dps_proxy_feedback.setStyleSheet("color: #38BDF8; font-size: 12px;")
            if hasattr(self, "log"):
                self.log(f"Combat Bridge: {msg}")
            # A stale card must not keep Options 2/3 locked after an external
            # deletion (or a previous removal that completed off to the side).
            self._refresh_dps_diagnostics()
            return

        proc_pid = None
        try:
            proc_pid = find_pid("Farever.exe") or find_pid("farever.exe")
        except Exception:
            proc = getattr(self, "proc", None)
            if proc and hasattr(proc, "pid"):
                proc_pid = proc.pid

        removed_any = False
        locked_files = []

        for fpath in farever_files_to_remove:
            try:
                fpath.unlink()
                removed_any = True
                if hasattr(self, "log"):
                    self.log(f"Combat Bridge: Removed {fpath.name} from {game_dir}")
            except PermissionError:
                locked_files.append(fpath)
            except Exception as e:
                if hasattr(self, "log"):
                    self.log(f"Combat Bridge: Error removing {fpath.name}: {e}")

        # If locked and process is running, attempt a SAFE hook removal.
        # The old path ran a bare remote FreeLibrary on a DLL with live
        # MinHook detours — that unmapping is what crashed the game. The
        # safe path unhooks first and refuses when the DLL offers no
        # MinHook cleanup exports; only then is the file deleted (which
        # still fails while the image is mapped — the close-game message
        # below covers that).
        if locked_files and proc_pid:
            import time
            from ....core.proc import safe_uninject_hook
            for lf in list(locked_files):
                try:
                    if hasattr(self, "log"):
                        self.log(f"Combat Bridge: Attempting safe hook removal of {lf.name} from PID {proc_pid}…")
                    ok, reason = safe_uninject_hook(proc_pid, lf.name)
                    if ok:
                        time.sleep(0.25)
                        lf.unlink()
                        removed_any = True
                        locked_files.remove(lf)
                        if hasattr(self, "log"):
                            self.log(f"Combat Bridge: ✓ Unhooked and removed {lf.name}!")
                    elif hasattr(self, "log"):
                        self.log(f"Combat Bridge: Safe removal refused — {reason}")
                except Exception:
                    pass

        if locked_files:
            names = ", ".join(f.name for f in locked_files)
            msg = f"⚠ Farever.exe (PID {proc_pid}) has {names} locked. Please close Farever.exe to release the file lock."
            if hasattr(self, "_dps_proxy_feedback") and self._dps_proxy_feedback:
                self._dps_proxy_feedback.setText(msg)
                self._dps_proxy_feedback.setStyleSheet("color: #EF4444; font-size: 12px; font-weight: 600;")
            if hasattr(self, "log"):
                self.log(f"Combat Bridge: Cannot remove {names} while locked.")
            return

        if removed_any:
            msg = "✓ Removed FareverPal proxy DLL from game folder! Your game directory is clean."
            if third_party_files:
                msg += f" (Preserved 3rd-party {', '.join(third_party_files)})"
            if hasattr(self, "_dps_proxy_feedback") and self._dps_proxy_feedback:
                self._dps_proxy_feedback.setText(msg)
                self._dps_proxy_feedback.setStyleSheet("color: #10B981; font-size: 12px; font-weight: 700;")
            if hasattr(self, "log"):
                self.log(f"Combat Bridge: {msg}")
            self._refresh_dps_diagnostics()


    def _test_uninject_now(self) -> None:
        # Immediately switch DPS capture mode to Option 3 (Memory Reader)
        # This persists to settings.json so next time FareverPal/game opens,
        # it does NOT auto-inject farever_dps.dll!
        self._set_dps_mode_key("memory")

        proc = getattr(self, "proc", None)
        if proc is None and hasattr(self, "model"):
            proc = getattr(self.model, "proc", None)
        pid = getattr(proc, "pid", None) if proc else None
        if not pid:
            try:
                from ....core.proc import find_pid
                pid = find_pid("Farever.exe") or find_pid("farever.exe")
            except Exception:
                pass
        if not pid:
            self._set_dps_feedback(
                "✓ Switched to Option 3 (Memory Reader) · Game not running",
                self._DPS_FEEDBACK_OK)
            self._refresh_dps_diagnostics()
            return
        from ...game_attach import _try_uninject_hook
        log_cb = getattr(self, "log", None)
        # The safe uninject blocks up to a few seconds (SafeUnload export or
        # disable hooks -> grace pause -> MH_Uninitialize): run it off the UI
        # thread so the button never freezes the panel. Feedback shows
        # immediately; the Activity Log gets the authoritative result.
        self._set_dps_feedback(
            f"⚙ Removing bridge hooks from PID {pid} safely… (switched to "
            "Option 3 — Memory Reader)", self._DPS_FEEDBACK_WARN)
        import threading

        # Route the worker's verdict back through the UI thread (see
        # _uninject_result): touching widgets from the worker lagged the
        # panel and raised QBasicTimer threading warnings.
        if not getattr(self, "_uninject_result_connected", False):
            try:
                self._uninject_result.connect(self._on_uninject_result)
                self._uninject_result_connected = True
            except Exception:
                pass

        def _uninject_worker():
            res = _try_uninject_hook(pid, log_cb=log_cb)
            ok = bool(res and res[0])
            reason = res[1] if (res and len(res) > 1 and res[1]) else "uninject returned no result"
            # Post-state verify: is the image actually gone from the game?
            # Module snapshots can lag a couple of seconds, so the (ok,
            # reason) above stays authoritative — this only tunes the wording
            # (fully unmapped vs hooks-dead-but-resident vs still loaded).
            # No widget access here: the verdict is emitted to the UI thread.
            still_mapped: bool | None = None
            try:
                import time as _t
                _t.sleep(2.0)
                det = getattr(proc, "detect_combat_bridge", None) if proc is not None else None
                if callable(det):
                    binfo = det() or {}
                    still_mapped = bool(binfo.get("detected") and binfo.get("option") == 2)
            except Exception:
                still_mapped = None
            try:
                self._uninject_result.emit(ok, reason, still_mapped)
            except Exception:
                pass

        threading.Thread(target=_uninject_worker, daemon=True,
                         name="dps-uninject-ui").start()
        self._refresh_dps_diagnostics()

    def _on_uninject_result(self, ok: bool, reason: str, still_mapped) -> None:
        """UI-thread landing for the background uninject worker."""
        try:
            if ok and still_mapped is not True:
                self._set_dps_feedback(
                    f"✓ {reason} · switched to Option 3 (Memory Reader)",
                    self._DPS_FEEDBACK_OK)
            elif ok:
                # Tier-2 disable-only path: hooks dead, image resident
                # (heartbeats may still flow) until game restart.
                self._set_dps_feedback(
                    f"⚠ {reason} · image still mapped but inert — clears on "
                    "game restart (Option 3 active)", self._DPS_FEEDBACK_WARN)
            else:
                self._set_dps_feedback(
                    f"✕ Uninject failed: {reason} · bridge still loaded "
                    "(Option 3 active) — see Activity Log",
                    self._DPS_FEEDBACK_ERR)
            try:
                self._refresh_dps_diagnostics()
            except Exception:
                pass
        except Exception:
            pass


