"""The DPS tab's satellites, in one shard beside `dps_engine.py`.

`dps_engine.py` assembles the DPS tab; these four were carved out of it (and of
`dps_proxy.py`) one at a time, each as its own module, because the ui line
budget measures ONE file. That is a reason to split, not a reason to scatter a
family across a module per idea, so they live here together.

* `SettingsDpsMeterMixin` - the Combat DPS Meter card's preferences (default
  view, row limits, keep-logs, the Fights-list day window) and the resync that
  re-surfaces them when the tab is re-shown, so an overlay-side change (the
  meter's own view chips) appears without a restart.
* `SettingsDpsMemoryMixin` - the Option 3 (pure memory reader) card, kept apart
  from Options 1 and 2: it needs no DLL, no injector and no write to the game
  process, which is the one thing the other two cannot offer.
* `SettingsDpsBridgeLogMixin` - the bridge's own log tail inside Live
  Diagnostics. A bridge that loaded but is not arming used to say only "no event
  yet this run", while the answer sat in the DLL's own log file in the game
  folder ("table not present yet", "hooks partial arm", "hlboot mismatch").
* `SettingsDpsDiagMixin` and its scan job / worker - the off-thread Live
  Diagnostics scan. Deliberately NOT a QThread: the card re-runs it every few
  seconds while the tab is open, and the process it shares also holds the game's
  native reader and the DPS capture threads, so the recurring construction of a
  Qt thread object is where the main-thread access violation in run.log landed.
  A daemon threading.Thread whose result hops back over a queued signal is the
  same shape the DPS drain already uses in ui/dps_background_tick.py.

Imports the single shared namespace (constants, theme, Qt aliases) from
`._shared`, so the moved method bodies keep their original globals.
"""

import threading

from ._shared import *  # noqa: F401,F403

class SettingsDpsMeterMixin:
    """Combat DPS Meter card preferences (view / row limits) and the resync
that surfaces external changes."""

    # --- Combat DPS Meter (Top DPS overlay) -------------------------------
    # Card name shown in the header badge (constant — never swaps to
    # "ACTIVE ENGINE"); the Select buttons stay the short "Option N" form.
    _DPS_MODE_NAMES = {
        "proxy": "Option 1: Drop-In Proxy",
        "injector": "Option 2: Hot Injector",
        "memory": "Option 3: Memory Reader",
    }

    def _build_dps_card(self) -> QtWidgets.QFrame:
        """Top DPS meter settings: default view + row limits.

        There is no tracking-range control: capture is zone-wide everywhere
        (see ``DpsTracker`` — instances and the open world both take every
        unit the game reports, and distance never affected a damage number).
        """
        # No sprite icon here: SectionHeader already draws the accent tick, and
        # an icon ahead of it pushed the title ~40px right of the card content
        # (the diagnostics card next to it has no icon either).
        card, lay = self._overlay_card_frame("Combat DPS Meter",
                                             tag="TOP DPS OVERLAY")

        # Default view + row limits sit side by side (two columns), matching
        # the toggles below — nothing stacks full-width anymore.
        pref_cols = QtWidgets.QHBoxLayout()
        pref_cols.setSpacing(10)

        view_col = QtWidgets.QVBoxLayout()
        view_col.setSpacing(6)
        view_lbl = QtWidgets.QLabel("Default meter view")
        view_lbl.setStyleSheet(f"font-size: 12px; font-weight: 600; color: {theme.MUTED};")
        view_col.addWidget(view_lbl)
        self._dps_view_seg = C.SegmentedControl(
            ["Damage", "Healing", "Both"],
            current=getattr(self.s, "dps_view", "damage"))
        self._dps_view_seg.currentChanged.connect(self._set_dps_view)
        view_col.addWidget(self._dps_view_seg)
        view_col.addStretch(1)
        pref_cols.addLayout(view_col, 1)

        rows_col = QtWidgets.QVBoxLayout()
        rows_col.setSpacing(6)
        # The log-retention window, beside the two row limits: both are "how
        # much of this do I keep" numbers, and the cleanup button on the Combat
        # page reads this one (0 = keep everything, which is the default — the
        # archived fights are the player's own record and nothing is deleted
        # until they press the button). The Fights-list day window lives
        # alongside it (see below).
        keep = C.Stepper(int(getattr(self.s, "dps_log_keep_days", 0)), 0, 365, 5)
        keep.valueChanged.connect(
            lambda v_, a="dps_log_keep_days": self._set(a, int(v_)))
        self._settings_steppers.append(("dps_log_keep_days", keep))
        # The Fights list's default day window sits BESIDE Keep logs: both are
        # "how much history do I see" numbers and a player tuning one is right
        # there to tune the other. 0 = show every day (the list then draws the
        # whole season with no "Show all days" control), matching Keep logs' 0
        # = keep everything.
        window = C.Stepper(int(getattr(self.s, "dps_fights_recent_days", 7)),
                           0, 90, 1)
        window.valueChanged.connect(
            lambda v_, a="dps_fights_recent_days": self._set(a, int(v_)))
        self._settings_steppers.append(("dps_fights_recent_days", window))
        keep_row = QtWidgets.QHBoxLayout()
        keep_row.setSpacing(10)
        keep_row.addWidget(C.Field("Keep logs (days)", keep), 1)
        keep_row.addWidget(C.Field("Fights window (days)", window), 1)
        # No heading here: the steppers are self-describing ("Top damage rows",
        # "Top heal rows") and "Row limits" was the only header in the card. The
        # spacer keeps the stepper row level with the segmented control beside it
        # (the view column's caption height, since the two share a spacing).
        rows_col.addSpacing(view_lbl.sizeHint().height())
        rows_grid = QtWidgets.QGridLayout()
        rows_grid.setHorizontalSpacing(10)
        for i, (attr, label, lo, hi) in enumerate((
                ("dps_top_count", "Top damage rows", 1, 30),
                ("heals_top_count", "Top heal rows", 1, 10))):
            st = C.Stepper(int(getattr(self.s, attr)), lo, hi, 1)
            st.valueChanged.connect(lambda v_, a=attr: self._set(a, v_))
            self._settings_steppers.append((attr, st))
            rows_grid.addWidget(C.Field(label, st), 0, i)
        rows_col.addLayout(rows_grid)
        rows_col.addLayout(keep_row)
        rows_col.addStretch(1)
        pref_cols.addLayout(rows_col, 1)

        lay.addLayout(pref_cols)

        return card

    def _set_dps_solo_only(self, on: bool) -> None:
        """Persist the solo-only meter/combat-page view toggle."""
        self._set("dps_solo_only", bool(on))
        if hasattr(self, "_render_combat_breakdown"):
            try:
                self._render_combat_breakdown()
            except Exception:
                pass
        ov = getattr(self, "overlay_mgr", None)
        ov = ov.overlays.get("dps") if ov is not None else None
        if ov is not None:
            try:
                # Keep the overlay's Solo button and rows in sync with the
                # settings toggle (the button reflects the same setting).
                solo_btn = getattr(ov, "solo_btn", None)
                if solo_btn is not None:
                    solo_btn.blockSignals(True)
                    solo_btn.setChecked(bool(on))
                    solo_btn.blockSignals(False)
                tick = getattr(ov, "_tick", None)
                if callable(tick):
                    tick()
            except Exception:
                pass

    def _set_dps_view(self, label: str) -> None:
        self._set("dps_view", label.lower())


    def _resync_dps_settings_widgets(self) -> None:
        """Re-surface the Combat DPS Meter card when the DPS tab is re-shown,
        so overlay-side changes (e.g. the meter's view chips) appear here too
        without a restart."""
        seg = getattr(self, "_dps_view_seg", None)
        cur = getattr(self.s, "dps_view", "damage")
        if seg is not None and _is_valid(seg):
            try:
                if seg.currentText().lower() != str(cur).lower():
                    seg.blockSignals(True)
                    seg.setCurrentText(str(cur).capitalize())
                    seg.blockSignals(False)
            except Exception:
                pass
        for attr, st in getattr(self, "_settings_steppers", []) or []:
            if attr in ("dps_top_count", "heals_top_count",
                        "dps_log_keep_days", "dps_fights_recent_days"):
                if st is not None and _is_valid(st):
                    try:
                        if int(st.value()) != int(getattr(self.s, attr)):
                            st.blockSignals(True)
                            st.setValue(int(getattr(self.s, attr)))
                            st.blockSignals(False)
                    except Exception:
                        pass


class SettingsDpsMemoryMixin:
    """Option 3 (Pure Memory Reader) settings card and configuration."""

    def _build_option3_card(self) -> QtWidgets.QFrame:
        card = QtWidgets.QFrame()
        card.setObjectName("DpsOptCard3")
        card.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
        card.setCursor(QtCore.Qt.PointingHandCursor)
        card.mousePressEvent = lambda ev: self._set_dps_mode_key("memory")
        lay = QtWidgets.QVBoxLayout(card)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(8)

        # Header badge carries the card name (kept constant; active state is
        # shown by the card frame + Select button instead).
        badge = QtWidgets.QLabel(self._DPS_MODE_NAMES["memory"].upper())
        badge.setStyleSheet(
            "font-size: 15px; font-weight: 800; color: #10B981; "
            "background: rgba(16, 185, 129, 0.15); border: 1px solid rgba(16, 185, 129, 0.4); "
            "border-radius: 6px; padding: 3px 10px;"
        )
        # Fixed height: without this, the card layout stretches the pill to
        # fill leftover space, making the three badges wildly different sizes.
        badge.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Fixed)
        lay.addWidget(badge, 0, QtCore.Qt.AlignHCenter)
        self._dps_mode_badges["memory"] = badge

        # One lead+body block (lead phrase in the card's accent color) keeps
        # all three option cards the same shape: badge, description, features,
        # one action row, Select button.
        desc = QtWidgets.QLabel(
            "<span style=\"color:#10B981;font-weight:600;\">Pure passive memory "
            "scan (DamageReader).</span> Zero DLLs, zero injectors, and zero "
            "foreign code inside the game process — reads floating combat text "
            "directly from HashLink memory structures."
        )
        desc.setTextFormat(QtCore.Qt.RichText)
        desc.setStyleSheet(f"font-size: 12px; color: {theme.MUTED}; line-height: 1.3;")
        desc.setWordWrap(True)
        lay.addWidget(desc)

        feats = QtWidgets.QLabel(
            "✓ 100% passive memory reading (read-only handle)<br>"
            "✓ Immune to DLL blocks and injector detection<br>"
            "✓ No files copied, no injectors executed<br>"
            "<span style=\"color:#F59E0B;\">~ Group DPS can undercount / blind to non-floaty hits</span><br>"
            "<span style=\"color:#F59E0B;\">~ Starts within 1–3 s of the first hit (no startup scan)</span><br>"
            "<span style=\"color:#F59E0B;\">~ Less reliable than the hooked bridge</span>"
        )
        feats.setTextFormat(QtCore.Qt.RichText)
        feats.setStyleSheet(f"font-size: 12px; color: {theme.TEXT}; line-height: 1.4;")
        feats.setWordWrap(True)
        lay.addWidget(feats)

        sel_btn = QtWidgets.QPushButton("Select Option 3")
        sel_btn.setCursor(QtCore.Qt.PointingHandCursor)
        sel_btn.setFixedHeight(36)
        sel_btn.setMinimumWidth(190)   # uniform across the three cards
        sel_btn.clicked.connect(lambda: self._set_dps_mode_key("memory"))
        lay.addWidget(sel_btn)
        self._dps_mode_select_btns["memory"] = sel_btn

        self._dps_mode_cards["memory"] = card
        return card


#: Lines shown in the card. The snapshot keeps more; the card stays compact.
_CARD_LINES = 6


class SettingsDpsBridgeLogMixin:
    """Shows the bridge DLL's own log tail for the active capture mode."""

    def _bridge_log_tail(self) -> dict:
        """Tail of the active mode's bridge log (cached; no disk read unless
        the file changed). Never raises - diagnostics must not fail to paint."""
        from ....core.bridge_log import tail_bridge_log
        try:
            binfo = getattr(self, "_dps_diag_binfo", None) or {}
            mode = getattr(self.s, "dps_mode", "proxy")
            g_dir = binfo.get("game_dir") or self._find_farever_game_dir()
            proxy = ""
            if mode == "proxy":
                proxy = (binfo.get("dll_name")
                         or getattr(self.s, "dps_proxy_dll", "") or "")
            return tail_bridge_log(g_dir, mode, str(proxy).lower())
        except Exception:
            return {"found": False, "name": "", "path": "", "lines": [],
                    "heartbeats": 0, "age_s": None, "note": ""}

    def _bridge_log_lines(self) -> list[str]:
        """Clipboard lines: the log tail, or why there is no log to show."""
        info = self._bridge_log_tail()
        if not info.get("found"):
            note = info.get("note")
            return [f"Bridge log: {note}"] if note else []
        age = info.get("age_s")
        stamp = f", last write {age:.0f}s ago" if isinstance(age, (int, float)) else ""
        # A kept tail is still this mode's evidence, just no longer on disk -
        # say so, or it reads as a live file in a pasted report.
        kept = ", kept after cleanup" if info.get("snapshot") else ""
        out = [f"Bridge log ({info.get('name', '')}{stamp}{kept}):"]
        out.extend(f"  {ln}" for ln in (info.get("lines") or []))
        heartbeats = info.get("heartbeats") or 0
        if heartbeats:
            out.append(f"  (+{heartbeats} heartbeat line(s) omitted)")
        return out


def _scan_dps_combat_bridge_job(pid: int | None, g_dir: str) -> dict:
    """Pure worker body for the Live Diagnostics scan: snapshot the running
    game's loaded modules and hash-check the game folder's proxy DLLs.

    Everything the scan needs arrives as plain values (no Qt, no Settings
    access), so this is safe off the UI thread on a CallWorker. Returns the
    same binfo dict the old synchronous scan produced.
    """
    import os
    from ....core.proc import (
        detect_combat_bridge, is_farever_proxy,
        get_farevermod_proxy_identity, get_bridge_dir,
    )
    try:
        binfo = detect_combat_bridge(pid=pid or 0, game_dir=g_dir)
    except Exception:
        binfo = {}
    if not g_dir and binfo.get("game_dir"):
        g_dir = binfo.get("game_dir")

    # Compare the marked FareverMod build in the game folder with the
    # currently bundled proxy. This is metadata-only; replacement remains an
    # explicit user action through the Update button.
    # Shipped names only (2026-09-24): version.dll's reference binary is no
    # longer packed, so comparing against it could report an "update" whose
    # Install would then fail with "source not found". An installed copy is
    # still hash-recognised as ours everywhere else (cleanup, module probes).
    current_dir = get_bridge_dir()
    for proxy_name in ("dinput8.dll", "userenv.dll"):
        installed_p = os.path.join(g_dir, proxy_name) if g_dir else ""
        current_p = os.path.join(current_dir, proxy_name) if current_dir else ""
        installed_id = get_farevermod_proxy_identity(installed_p)
        if not installed_id:
            continue

        # The installed identity is useful even when this app build does not
        # ship the matching reference DLL: show what is actually in the game
        # folder, but only compare it when a current reference is available.
        binfo["farevermod_installed_build"] = installed_id.get(
            "FAREVERMOD_PROXY_HLBOOT", "unknown")
        binfo["farevermod_installed_abi"] = installed_id.get(
            "FAREVERMOD_PROXY_ABI", "unknown")
        binfo["farevermod_installed_version"] = installed_id.get(
            "FAREVERMOD_PROXY_VERSION", "unknown")
        binfo["farevermod_installed_dll"] = proxy_name

        current_id = get_farevermod_proxy_identity(current_p)
        if current_id:
            binfo["farevermod_current_build"] = current_id.get(
                "FAREVERMOD_PROXY_HLBOOT", "unknown")
            binfo["farevermod_current_abi"] = current_id.get(
                "FAREVERMOD_PROXY_ABI", "unknown")
            binfo["farevermod_current_version"] = current_id.get(
                "FAREVERMOD_PROXY_VERSION", "unknown")
            # A native code release can change without a game patch, and an
            # ABI bump can change without either moving. Any identity field
            # differing means the bundled reference should be offered.
            identity_fields = (
                "FAREVERMOD_PROXY_VERSION",
                "FAREVERMOD_PROXY_ABI",
                "FAREVERMOD_PROXY_HLBOOT",
            )
            binfo["farevermod_update_available"] = any(
                installed_id.get(field) != current_id.get(field)
                for field in identity_fields)
        break

    if g_dir and os.path.isdir(g_dir):
        dinput_p = os.path.join(g_dir, "dinput8.dll")
        if os.path.isfile(dinput_p) and not is_farever_proxy(dinput_p):
            binfo["third_party_dinput8"] = True
            binfo["third_party_dinput8_path"] = dinput_p
            try:
                binfo["third_party_dinput8_size"] = os.path.getsize(dinput_p)
            except Exception:
                pass
        for other_name in ("userenv.dll",):
            other_p = os.path.join(g_dir, other_name)
            if os.path.isfile(other_p) and not is_farever_proxy(other_p):
                binfo[f"third_party_{other_name.split('.')[0]}"] = True
                binfo[f"third_party_{other_name.split('.')[0]}_path"] = other_p

        version_p = os.path.join(g_dir, "version.dll")
        if os.path.isfile(version_p) and not is_farever_proxy(version_p):
            binfo["third_party_version"] = True
            binfo["third_party_version_path"] = version_p
            try:
                binfo["third_party_version_size"] = os.path.getsize(version_p)
            except Exception:
                pass
    return binfo


class _DiagScanBridge(QtCore.QObject):
    """Queued result bridge: scan thread -> GUI thread.

    A plain worker thread must never touch a widget, so the finished scan
    crosses back as a queued signal on this object (which lives on the GUI
    thread) — the same pattern ``ui/dps_background_tick._WorkerLogBridge``
    uses for the DPS drain's log lines.
    """

    scanned = QtCore.Signal(str, object)


class _DpsDiagScanWorker:
    """One Live Diagnostics scan, on a plain daemon OS thread.

    NOT a QThread on purpose (see the module docstring): the card re-runs
    this scan every few seconds while the DPS tab is open, so the scan runs
    the way the DPS drain already does — a thread whose result crosses back
    through ``bridge.scanned`` — and no Qt thread object is constructed or
    finalised off the GUI thread.

    Exposes the call surface the previous QThread-based callers expect
    (``start`` / ``isRunning`` / ``requestInterruption`` / ``stop`` /
    ``wait``), so the DPS-mode handoff in ``dps_modes.py`` is unchanged.
    """

    def __init__(self, job, tag: str, bridge) -> None:
        self._job = job
        self._tag = tag
        self._bridge = bridge
        self._interrupted = False
        self._thread = threading.Thread(
            target=self._run, name=f"dps-diag:{tag}", daemon=True)

    def _run(self) -> None:
        try:
            res = self._job()
        except Exception as e:
            res = {"ok": False, "error": str(e)}
        if self._interrupted:
            res = {"ok": False, "error": "interrupted"}
        bridge = self._bridge
        if bridge is None:
            return
        try:
            # Interrupted scans emit too: the landing side uses the arrival
            # (not the payload) to release a DPS-mode change that was queued
            # behind the scan, and it drops ok=False results from painting.
            bridge.scanned.emit(self._tag, res)
        except Exception:
            pass        # bridge/panel already gone (teardown)

    def start(self) -> None:
        self._thread.start()

    def isRunning(self) -> bool:
        return self._thread.is_alive()

    def requestInterruption(self) -> None:
        self._interrupted = True

    def wait(self, timeout_ms: int = 0) -> bool:
        """Join the scan thread (0 = wait indefinitely, like QThread.wait)."""
        self._thread.join(None if not timeout_ms else timeout_ms / 1000.0)
        return not self._thread.is_alive()

    def stop(self, timeout_ms: int = 800) -> None:
        self._interrupted = True
        self.wait(timeout_ms)


class SettingsDpsDiagMixin:
    """Off-thread Live Diagnostics scan (request / land / release)."""

    def _request_dps_diag_scan(self, origin: str = "direct") -> None:
        """Start a background combat-bridge scan; never blocks the UI thread.

        `origin` is "direct" (user action / first paint — the caller already
        opened the throttle window) or "poll" (the 1 s tick stamps the window
        when the scan lands in _on_dps_diag_scan_done). A newer request
        supersedes an in-flight one: the stale result is dropped by token, so
        overlapping scans can never paint out of order.
        """
        proc = getattr(self.model, "proc", None) if hasattr(self, "model") and self.model else getattr(self, "proc", None)
        pid = getattr(proc, "pid", None) if proc else None
        g_dir = self._find_farever_game_dir()
        token = getattr(self, "_dps_diag_scan_token", 0) + 1
        self._dps_diag_scan_token = token
        self._dps_diag_scan_origin = origin
        # One queued bridge per panel, created on the GUI thread: the scan's
        # result crosses back through its signal (queued delivery) instead of
        # the scan thread reaching into the panel itself.
        bridge = getattr(self, "_dps_diag_bridge", None)
        if bridge is None:
            parent = self if isinstance(self, QtCore.QObject) else None
            bridge = self._dps_diag_bridge = _DiagScanBridge(parent)
            bridge.scanned.connect(self._on_dps_diag_scan_done)
        worker = _DpsDiagScanWorker(
            lambda: _scan_dps_combat_bridge_job(pid, g_dir),
            tag=f"dps-diag:{token}", bridge=bridge)
        # Strong ref while running, so the slot swap below can never drop a
        # worker mid-scan. Finished scans are pruned here on the GUI thread:
        # the old per-worker `finished` lambda ran ON the scan thread and its
        # closure captured the worker, so a QThread could be finalised from a
        # thread that did not own it — the heap-tearing pattern this module
        # now avoids by not using QThread at all.
        live = getattr(self, "_dps_diag_workers", None)
        if live is None:
            live = self._dps_diag_workers = []
        live[:] = [w for w in live if w.isRunning()]
        live.append(worker)
        # Supersedes any in-flight scan: the old worker keeps running to
        # completion but its result is dropped by token, so at most one paint
        # lands per request generation.
        self._dps_diag_worker = worker
        worker.start()

    def _on_dps_diag_scan_done(self, tag: str, res) -> None:
        """UI-thread landing for a finished scan: store + repaint, unless a
        newer request already superseded it.

        Interrupted scans land here too (flagged ok=False and therefore
        dropped below), which is what lets this single landing point release
        a DPS-mode change that was queued behind the scan. That release used
        to be a QThread `finished` lambda, i.e. it ran on the scan thread.
        """
        if getattr(self, "_dps_pending_mode", None):
            retry = getattr(self, "_retry_dps_mode_change", None)
            if callable(retry):
                QtCore.QTimer.singleShot(0, retry)
        import time
        try:
            token = int(str(tag).rsplit(":", 1)[-1])
        except (ValueError, TypeError):
            return
        if token != getattr(self, "_dps_diag_scan_token", 0):
            return
        if not isinstance(res, dict) or res.get("ok") is False:
            return
        self._dps_diag_binfo = res
        # A settings edit or startup may have selected Option 2/3 before the
        # first folder scan landed. Now that the proxy conflict is known, fall
        # back to the file-backed mode that is actually present; the card locks
        # 2/3 until the user explicitly removes that file.
        current_mode = getattr(getattr(self, "s", None), "dps_mode", "proxy")
        if (current_mode != "proxy"
                and self._dps_proxy_blocks_non_proxy(current_mode)):
            self._set_dps_mode_key("proxy")
        if getattr(self, "_dps_diag_scan_origin", "direct") == "poll":
            self._dps_diag_last_scan = time.monotonic()
        self._paint_dps_diagnostics()
        # Proxy identity can change without changing readiness (for example,
        # replacing one build with another), so refresh its dedicated line on
        # every completed scan rather than folding it into the card-state key.
        self._paint_dps_proxy_build()
