"""Speedrun timer overlay.

A big mono stopwatch driven by the global hotkeys (toggle = start/stop, plus a
reset key). While running it watches the live dungeon boss and auto-stops the
instant it dies (configurable). Tracks a per-boss best (PB) + last time,
persisted in Settings.

Out-of-process like every overlay: it only reads game memory (boss HP) to detect
the kill, it never writes.
"""
from __future__ import annotations

import secrets
import threading

from PySide6 import QtCore, QtGui, QtWidgets

from . import theme
from .overlay_base import OverlayWindow
from .. import __version__
from ..core.speedrun import (
    SpeedrunTimer, BossTimer, Encounter, AutoStarter, ModeLatch, RearmGate,
    fmt_time, record_lines)
from ..data import names, icons
from ..api import FareverAPI

TICK_MS = 50           # centisecond-smooth display + boss poll while running
DETECT_EVERY = 10      # run the (heavier) dungeon detection every Nth tick (~500ms)
# (back-to-back re-arm timing lives in core.speedrun.RearmGate)


class SpeedrunOverlay(OverlayWindow):
    uploaded = QtCore.Signal(str)   # cross-thread upload-result text

    def __init__(self, model, settings, parent=None):
        super().__init__("SPEEDRUN", settings, geo_key="speedrun", parent=parent)
        self._page_key = "speedrun"
        self.model = model
        self.s = settings
        self.timer = SpeedrunTimer()             # the FULL-dungeon run
        self.boss_timer = BossTimer()            # the BOSS-only split (arms on first hit)
        self.encounter: Encounter | None = None  # current dungeon's boss-fight tracker
        self._prev_state = self.timer.state
        self._detect_ctr = 0
        self._dungeon_bid: str | None = None   # detected dungeon (kill) boss (sticky)
        self._in_dungeon = False                # boss currently present in the scene
        self._starter = AutoStarter()           # settle-baseline auto-start decision (pure)
        self._latch = ModeLatch()               # last good auto-detected difficulty this run
        self._rearm = RearmGate()               # holds the finished result until the player leaves
        self._uploaded = False                  # guard: one upload per finished run
        self._completion_sent = False           # guard: one per-dungeon completion ping per run
        self._client_run_id = ""                # shared id tying the upload + completion ping
        self.boss_is_new_best = False           # set True when a finish beats the BOSS-split PB
        self._run_mode: str | None = None       # difficulty captured at finish
        self._run_mode_src: str | None = None    # "auto" (read from boss level) | "manual"
        self._live_mode: str | None = None       # difficulty resolved live (for the HUD readout)
        self._live_mode_src: str | None = None

        # dungeon-instance icon, shown next to the panel header when detected
        self._dg_icon = QtWidgets.QLabel()
        self._dg_icon.setFixedSize(22, 22)
        self._dg_icon.setScaledContents(True)
        self._dg_icon.hide()
        self.titlebar.extra.insertWidget(0, self._dg_icon)

        # reset button in the title bar
        rst = QtWidgets.QPushButton()
        rst.setObjectName("Icon")
        rst.setIcon(icons.ui_qicon("refresh-cw", theme.MUTED, 14))
        rst.setToolTip("Reset timer")
        rst.clicked.connect(self.reset)
        self.titlebar.extra.insertWidget(self.titlebar.extra.count() - 3, rst)

        self.uploaded.connect(self._on_uploaded)

        self.time_lbl = QtWidgets.QLabel("00:00.00")
        self.time_lbl.setAlignment(QtCore.Qt.AlignCenter)
        self._style_time(theme.MUTED)
        self.content.addWidget(self.time_lbl)

        # Secondary split: the FULL-run time, shown small below once the boss timer
        # takes over as the headline (the boss fight has started). Hidden otherwise.
        self.sub_lbl = QtWidgets.QLabel("")
        self.sub_lbl.setObjectName("Mono")
        self.sub_lbl.setAlignment(QtCore.Qt.AlignCenter)
        self.sub_lbl.hide()
        self.content.addWidget(self.sub_lbl)

        self.state_lbl = QtWidgets.QLabel("READY")
        self.state_lbl.setObjectName("Mono")
        self.state_lbl.setAlignment(QtCore.Qt.AlignCenter)
        self.content.addWidget(self.state_lbl)

        # Difficulty readout - shows what a finished run will upload as, and whether
        # it was read from the live boss level ("detected") or is the manual
        # fallback ("manual", tinted gold so a guess is never mistaken for a read).
        self.mode_lbl = QtWidgets.QLabel("")
        self.mode_lbl.setObjectName("Mono")
        self.mode_lbl.setAlignment(QtCore.Qt.AlignCenter)
        self.mode_lbl.hide()
        self.content.addWidget(self.mode_lbl)

        # In-run advisory (gold): Normal run won't score, or a manual-start run
        # can't be auto-uploaded. Distinct from upload_lbl (which carries results).
        self.warn_lbl = QtWidgets.QLabel("")
        self.warn_lbl.setObjectName("Mono")
        self.warn_lbl.setAlignment(QtCore.Qt.AlignCenter)
        self.warn_lbl.setWordWrap(True)
        self.warn_lbl.hide()
        self.content.addWidget(self.warn_lbl)

        # Record line: the BOSS-split PB/LAST is the headline metric (boss time is
        # what matters), with the full-run PB on a smaller secondary line below.
        self.pb_lbl = QtWidgets.QLabel("no record yet")
        self.pb_lbl.setObjectName("Mono")
        self.pb_lbl.setAlignment(QtCore.Qt.AlignCenter)
        self.content.addWidget(self.pb_lbl)

        self.full_pb_lbl = QtWidgets.QLabel("")
        self.full_pb_lbl.setObjectName("Mono")
        self.full_pb_lbl.setAlignment(QtCore.Qt.AlignCenter)
        self.full_pb_lbl.hide()
        self.content.addWidget(self.full_pb_lbl)

        self.upload_lbl = QtWidgets.QLabel("")
        self.upload_lbl.setObjectName("Mono")
        self.upload_lbl.setAlignment(QtCore.Qt.AlignCenter)
        self.upload_lbl.setWordWrap(True)
        self.upload_lbl.hide()
        self.content.addWidget(self.upload_lbl)

        # Manual upload (shown after a finished run only when auto-upload is off).
        self._upload_btn = QtWidgets.QPushButton("Upload run")
        self._upload_btn.setObjectName("Accent")
        self._upload_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self._upload_btn.clicked.connect(self._do_upload)
        self._upload_btn.hide()
        self.content.addWidget(self._upload_btn)

        # Fully automatic: starts on movement in a dungeon, stops on boss death,
        # uploads on finish. Manual control via hotkeys + the titlebar reset icon.
        self._base_w, self._base_h = 250, 120
        self.setMinimumWidth(210)
        self.apply_scale(self.s.speedrun_scale)

        self._poll = QtCore.QTimer(self)
        self._poll.timeout.connect(self._tick)
        self._poll.start(TICK_MS)
        self._render()

    # --- actions (hotkeys + buttons) ------------------------------------
    def toggle(self):
        self.timer.toggle()
        self._render()

    def reset(self):
        self.timer.reset()
        self.boss_timer.reset()
        if self.encounter is not None:
            self.encounter.reset()
        self._starter.reset()
        self._latch.reset()
        self._rearm.reset()
        self._uploaded = False
        self._completion_sent = False
        self.boss_is_new_best = False
        self._run_mode = None
        self._run_mode_src = None
        self.upload_lbl.hide()
        self.warn_lbl.hide()
        self.sub_lbl.hide()
        self.full_pb_lbl.hide()
        self._upload_btn.hide()
        self._render()

    # --- loop ------------------------------------------------------------
    def _tick(self):
        t = self.timer
        running = (t.state == t.RUNNING)
        auto = bool(self.s.speedrun_auto)   # one master toggle: detect + start + stop

        # When fully manual (auto off), the overlay is a plain stopwatch: no scene
        # reads, no dungeon icon, no auto start/stop.
        if not auto:
            self._dg_icon.hide()
            self._prev_state = t.state
            self._render()
            return

        # ONE scene read per tick: every tick while running (responsive auto-stop),
        # else on a slower cadence for dungeon detection.
        # enc = (members, kill_id, states) where states = [(unit_id, present, hp)].
        enc = None
        if self.model is not None:
            self._detect_ctr += 1
            detect_now = self._detect_ctr >= DETECT_EVERY
            if running or detect_now:
                if detect_now:
                    self._detect_ctr = 0
                try:
                    enc = self.model.encounter_state()
                except Exception:
                    enc = None

        if enc is not None:
            members, kill_id, states, engage_any = enc
            # (Re)build the tracker when the dungeon's kill boss changes, so a prior
            # dungeon's encounter can't linger into the next.
            if kill_id and (self.encounter is None or self.encounter.kill_id != kill_id):
                self.encounter = Encounter(members, kill_id, engage_any)
            if kill_id:
                self._dungeon_bid = kill_id
            # Feed the tracker: it returns the kill boss's (id, present, hp) for the
            # existing kill detection and latches "engaged" on the first hit.
            if self.encounter is not None:
                kill_tuple = self.encounter.feed(states)
            else:
                kill_tuple = (kill_id, False, None)
            self._in_dungeon = bool(kill_tuple[1])
            self._update_dungeon_icon()
            # Refresh the live difficulty readout (cheap) and, while the boss is
            # alive and fighting, latch a confident auto read so the finish-frame
            # (boss dying) can't flip Normal<->Hard.
            self._live_mode, self._live_mode_src = self._resolve_mode()
            if running:
                self._latch.observe(self._live_mode, self._live_mode_src)
                # Boss-only split: arm on the first hit landed on the fight (any
                # encounter member taking damage); both timers run from here.
                if self.encounter is not None and self.encounter.engaged:
                    self.boss_timer.arm()
                # Auto-stop the FULL run on the kill boss's death, or CANCEL if the
                # player left (boss despawns at full HP). The boss split ends with
                # the same kill.
                res = t.feed_boss(*kill_tuple)
                if res == t.LEFT:
                    self._on_run_aborted()
                elif res == t.KILL:
                    self.boss_timer.stop()

        # Auto-start: only INSIDE a dungeon, and only on real physical movement
        # from a settled spawn baseline (consistent - never on the teleport-in).
        if t.state == t.READY and self.model is not None:
            self._check_auto_start()

        # Run just started -> arm a fresh upload + a fresh difficulty latch + a
        # fresh boss split.
        if t.state == t.RUNNING and self._prev_state != t.RUNNING:
            self._uploaded = False
            self._completion_sent = False
            self.boss_is_new_best = False
            self._latch.reset()
            self.boss_timer.reset()
            if self.encounter is not None:
                self.encounter.reset()
            self.upload_lbl.hide()
            self.warn_lbl.hide()
            self.sub_lbl.hide()
            self.full_pb_lbl.hide()
            self._upload_btn.hide()

        # Run just finished -> capture difficulty, PB, upload. Prefer the value
        # latched while the boss was alive over a fresh read (the boss is dead
        # now, so a fresh read often can't see it). PB + auto-upload only count a
        # CONFIRMED kill (a manual stop must never set a bogus PB or auto-submit).
        if t.state == t.DONE and self._prev_state != t.DONE:
            self._rearm.reset()                          # grace counts from the finish
            self._client_run_id = secrets.token_hex(8)   # ties the upload + completion ping
            # Freeze the boss split too (a manual stop won't have hit the kill path).
            if self.boss_timer.state == BossTimer.RUNNING:
                self.boss_timer.stop()
            self._run_mode, self._run_mode_src = self._latch.resolve(*self._resolve_mode())
            if t.is_kill:
                self._record_best()
            self._maybe_upload()
            self._record_completion()

        # Back-to-back: after a finished run, re-arm for the next one without the
        # user clicking reset. NOT keyed on boss presence - the boss despawns on
        # death, which used to wipe the finished times within a tick of the kill.
        # The RearmGate holds the result until the player actually leaves (exit
        # teleport / load screen) or a generous grace passes. Toggleable.
        if t.state == t.DONE and self.s.speedrun_auto_rearm:
            try:
                pos = self.model.player_xyz() if self.model is not None else None
            except Exception:
                pos = None
            if self._rearm.feed(pos):
                self.reset()

        self._prev_state = t.state
        self._render()

    def _check_auto_start(self):
        # Delegate the (settle-baseline) decision to the pure AutoStarter so it
        # behaves identically every run: it only fires on deliberate movement
        # away from a stationary spawn baseline, never on the teleport-in jitter.
        try:
            pos = self.model.player_xyz()
        except Exception:
            pos = None
        if self._starter.feed(self._in_dungeon, pos):
            self.timer.start("auto")

    def _update_dungeon_icon(self):
        bid = self._dungeon_bid
        if not bid:
            self._dg_icon.hide()
            return
        try:
            pm = icons.pixmap("unit", bid, 22)
            if pm is not None and not pm.isNull():
                self._dg_icon.setPixmap(pm)
                self._dg_icon.setToolTip(names.unit_name(bid) or bid)
                self._dg_icon.show()
            else:
                self._dg_icon.hide()
        except Exception:
            self._dg_icon.hide()

    def _record_best(self):
        """Persist new PBs for a confirmed kill: the FULL-run best and the
        BOSS-split best are tracked separately (boss time is the headline metric)."""
        bid = self.timer.boss_id or "_"
        saved = False
        cur = self.timer.last
        if cur is not None:
            best = self.s.speedrun_best.get(bid)
            if best is None or cur < best:
                self.s.speedrun_best[bid] = cur
                self.timer.is_new_best = True
                saved = True
        bcur = self.boss_timer.last
        if bcur is not None and self.boss_timer.state == BossTimer.DONE:
            bbest = self.s.speedrun_boss_best.get(bid)
            if bbest is None or bcur < bbest:
                self.s.speedrun_boss_best[bid] = bcur
                self.boss_is_new_best = True
                saved = True
        if saved:
            self.s.save()

    def _maybe_upload(self):
        """On finish: auto-upload ONLY a confirmed boss kill (and only if enabled);
        otherwise offer a manual Upload button. A finish without a detected kill
        (a manual stop, or anything that wasn't a real kill) is never auto-sent."""
        if self._uploaded:
            return
        bid = self.timer.boss_id or self._dungeon_bid
        if not bid or self.timer.last is None:
            return
        # Manually-started runs (hotkey/button) are never auto-uploaded: there's no
        # trusted auto-detected context, so they must be submitted by hand with proof.
        if self.timer.source == "manual":
            self._uploaded = True
            self.upload_lbl.setText("manual run — submit on the website with a "
                                    "screenshot/video. auto-tracked runs upload "
                                    "instantly and are trusted.")
            self.upload_lbl.setStyleSheet(f"color:{theme.GOLD};background:transparent;")
            self.upload_lbl.show()
            return
        if not self.s.account_token:
            self.upload_lbl.setText("sign in (control panel) to upload")
            self.upload_lbl.setStyleSheet(f"color:{theme.MUTED};background:transparent;")
            self.upload_lbl.show()
            return
        if not self.timer.is_kill:
            # finished without a detected kill - never auto-submit; let the user
            # decide via the manual button (the leaderboard also screens times).
            self.upload_lbl.setText("no boss kill detected — upload manually if this was a real run")
            self.upload_lbl.setStyleSheet(f"color:{theme.GOLD};background:transparent;")
            self.upload_lbl.show()
            self._upload_btn.show()
            return
        if self.s.speedrun_auto_upload:
            self._do_upload()
        else:
            self._upload_btn.show()   # manual: user clicks to upload

    def _record_completion(self):
        """Bump the player's true per-dungeon run counter on the website for any
        CONFIRMED clear (auto-detected boss kill), independent of the leaderboard:
        a non-PB or Normal run still counts as "you ran this dungeon". Fire-and-
        forget, one ping per run, idempotent server-side via the shared run id."""
        if self._completion_sent or not self.s.account_token:
            return
        if not self.timer.is_kill:                 # only genuine clears count
            return
        bid = self.timer.boss_id or self._dungeon_bid
        last = self.timer.last
        if not bid or last is None:
            return
        self._completion_sent = True
        slug = bid.lower()
        mode = self._run_mode or self._resolve_mode()[0]
        full_ms = int(round(last * 1000))
        boss_ms = int(round(self.boss_timer.last * 1000)) if self.boss_timer.last is not None else None
        base, token, cid = self.s.api_base, self.s.account_token, self._client_run_id

        def work():
            FareverAPI(base, token).record_completion(
                slug, mode=mode, full_ms=full_ms, boss_ms=boss_ms, client_run_id=cid)

        threading.Thread(target=work, daemon=True).start()

    def _on_run_aborted(self):
        """Player left the dungeon / hit the main menu mid-run: cancel the run
        without recording a time or uploading anything."""
        self.timer.reset()
        self.boss_timer.reset()
        if self.encounter is not None:
            self.encounter.reset()
        self._starter.reset()
        self._latch.reset()
        self._rearm.reset()
        self._uploaded = False
        self._completion_sent = False
        self.boss_is_new_best = False
        self._run_mode = None
        self._run_mode_src = None
        self._upload_btn.hide()
        self.warn_lbl.hide()
        self.sub_lbl.hide()
        self.full_pb_lbl.hide()
        self.upload_lbl.setText("run cancelled — left the dungeon (no upload)")
        self.upload_lbl.setStyleSheet(f"color:{theme.MUTED};background:transparent;")
        self.upload_lbl.show()

    def _resolve_mode(self) -> tuple[str, str]:
        """(difficulty, source) to upload as. source is "auto" when read from the
        live boss level (Auto Detect Boss Run on + level readable), else "manual"
       , the fallback selector, used when auto is off OR the level can't be read
        (e.g. the level offset isn't calibrated on this build)."""
        if self.s.speedrun_auto and self.model is not None:
            try:
                m = self.model.detected_mode()
            except Exception:
                m = None
            if m in ("normal", "hard"):
                return m, "auto"
        # Fallback is fixed to Hard (the boards are Hard-only); a true Normal run
        # is still detected above and lands profile-only.
        return "hard", "manual"

    def _do_upload(self):
        """Push the finished run to the web leaderboard (background thread). A run
        with a boss split sends BOTH the full and boss times, tied together by a
        shared client_run_id so the site can show them as one run."""
        bid = self.timer.boss_id or self._dungeon_bid
        last = self.timer.last
        if self._uploaded or not bid or last is None or not self.s.account_token:
            return
        self._uploaded = True
        self._upload_btn.hide()
        slug = bid.lower()
        base, token = self.s.api_base, self.s.account_token
        mode = self._run_mode or self._resolve_mode()[0]
        src = self._run_mode_src or "manual"
        co_runners = list(self.s.speedrun_corunners or [])
        # Per-run build override (Speedrun tab): when on, attach this build code;
        # otherwise the server links the player's profile featured build.
        build_code = (self.s.speedrun_build_override or "").strip() if self.s.speedrun_build_override_on else ""
        cid = self._client_run_id or secrets.token_hex(8)
        splits = [("full", int(round(last * 1000)))]
        if self.boss_timer.last is not None:
            splits.append(("boss", int(round(self.boss_timer.last * 1000))))
        self.upload_lbl.setText(f"uploading… ({mode} · {src})")
        self.upload_lbl.setStyleSheet(f"color:{theme.MUTED};background:transparent;")
        self.upload_lbl.show()

        def work():
            api = FareverAPI(base, token)
            primary: dict = {}
            boss_ok = False
            for rtype, ms in splits:
                res = api.submit_run(
                    slug, ms, mode=mode, run_type=rtype, co_runners=co_runners,
                    build_code=build_code, companion_version=__version__, client_run_id=cid)
                if rtype == "full":
                    primary = res if isinstance(res, dict) else {}
                elif isinstance(res, dict) and res.get("ok"):
                    boss_ok = True
            self.uploaded.emit(self._upload_message(primary, boss_ok))

        threading.Thread(target=work, daemon=True).start()

    @staticmethod
    def _upload_message(res: dict, boss_ok: bool) -> str:
        if not res.get("ok"):
            return "upload failed: " + str(res.get("error", "error"))
        if res.get("on_board") is False:
            if res.get("board_reason") == "companion_outdated":
                base = "uploaded · profile only — update the companion to score"
            else:
                base = "uploaded · profile only (normal run)"
        elif res.get("flagged"):
            base = "uploaded · pending review"
        else:
            base = "uploaded · live ✓"
        return base + ("  ·  boss split sent" if boss_ok else "")

    def _on_uploaded(self, msg: str):
        if "failed" in msg:
            color = theme.DANGER
        elif "✓" in msg:
            color = theme.GOOD
        else:                       # pending / profile-only — advisory, not an error
            color = theme.GOLD
        self.upload_lbl.setText(msg)
        self.upload_lbl.setStyleSheet(f"color:{color};background:transparent;")
        self.upload_lbl.show()

    # --- render ----------------------------------------------------------
    def _fit(self):
        """Responsive sizing: grow/shrink the panel to the widest visible line so
        nothing clips (the FINISHED · BOSS · <name> line, PB/LAST, the full-PB
        line), and let the height follow the content (word-wrapped status labels
        included via heightForWidth). The base size is the floor, never a cap."""
        pad = 22   # content margins (8+8) + card border (1+1) + breathing room
        lines = (self.time_lbl, self.sub_lbl, self.state_lbl, self.mode_lbl,
                 self.pb_lbl, self.full_pb_lbl)
        need = max((l.sizeHint().width() for l in lines if not l.isHidden()), default=0) + pad
        w = max(self.minimumWidth(), round(self._base_w * self._scale), need)
        lay = self.layout()
        h = lay.totalHeightForWidth(w) if lay.hasHeightForWidth() else self.sizeHint().height()
        h = max(h, round(self._base_h * self._scale))
        if w != self.width() or h != self.height():
            self.resize(w, h)

    def _style_time(self, color: str):
        size = max(18, int(40 * float(self.s.speedrun_scale or 1.0)))  # scale with the overlay zoom
        self.time_lbl.setStyleSheet(
            f"color:{color};font-family:'{theme.MONO_FONT}','Consolas';"
            f"font-size:{size}px;font-weight:800;background:transparent;letter-spacing:1px;")

    def _render(self):
        t = self.timer
        bt = self.boss_timer
        boss_armed = bt.state != BossTimer.READY
        # Headline (big) timer = the boss split once the fight is on, else the full
        # run; the full run then drops to the small secondary line below.
        if boss_armed:
            self.time_lbl.setText(fmt_time(bt.elapsed()))
            self._style_time(theme.GOLD if (t.state != t.DONE or self.boss_is_new_best) else theme.GOOD)
            self.sub_lbl.setText(f"FULL  {fmt_time(t.elapsed())}")
            self.sub_lbl.setStyleSheet(
                f"color:{theme.MUTED};font-family:'{theme.MONO_FONT}','Consolas';"
                f"font-size:13px;background:transparent;")
            self.sub_lbl.show()
        else:
            self.time_lbl.setText(fmt_time(t.elapsed()))
            if t.state == t.RUNNING:
                self._style_time(self.s.hud_accent or theme.ACCENT)
            elif t.state == t.DONE:
                self._style_time(theme.GOLD if t.is_new_best else theme.GOOD)
            else:
                self._style_time(theme.MUTED)
            self.sub_lbl.hide()

        boss = names.unit_name(t.boss_id) if t.boss_id else "—"
        tag = "BOSS" if boss_armed else "FULL"
        label = {t.READY: "READY", t.RUNNING: "RUNNING", t.DONE: "FINISHED"}[t.state]
        self.state_lbl.setText(f"{label}   ·   {tag}   ·   {boss}")
        self._render_mode()
        self._render_warn()

        self._render_records(boss_armed)
        self._fit()

    def _render_records(self, boss_armed: bool):
        """Draw the record lines from the pure record_lines() decision: BOSS PB/LAST
        as the headline, the full-run PB on a smaller secondary line below."""
        t = self.timer
        bid = t.boss_id or "_"
        primary, secondary = record_lines(
            done=(t.state == t.DONE),
            boss_armed=boss_armed,
            boss_last=self.boss_timer.last,
            full_last=t.last,
            boss_best=self.s.speedrun_boss_best.get(bid),
            full_best=self.s.speedrun_best.get(bid),
            boss_is_new_best=self.boss_is_new_best,
            full_is_new_best=t.is_new_best,
        )
        self.pb_lbl.setText(primary)
        if secondary:
            self.full_pb_lbl.setText(secondary)
            self.full_pb_lbl.setStyleSheet(
                f"color:{theme.MUTED};background:transparent;font-size:11px;")
            self.full_pb_lbl.show()
        else:
            self.full_pb_lbl.hide()

    def _render_mode(self):
        """The difficulty readout: what a finished run uploads as + how it was
        resolved. After a finish it shows the captured value; otherwise the live
        one. Hidden when there's no dungeon context to talk about."""
        t = self.timer
        if t.state == t.DONE and self._run_mode:
            mode, src = self._run_mode, (self._run_mode_src or "manual")
        else:
            mode, src = self._live_mode, self._live_mode_src
        # Show an AUTO-detected difficulty wherever it resolved (a recognized boss
        # OR the enemy-fleet read in a boss-less dungeon like the bee one). Only the
        # MANUAL fallback needs a boss/dungeon context to avoid showing in town.
        if not mode or (src != "auto" and not (self._dungeon_bid or t.boss_id)):
            self.mode_lbl.hide()
            return
        if src == "auto":
            tag, color = "detected", (self.s.hud_accent or theme.ACCENT)
            tip = "Difficulty read from the live boss level."
        else:
            tag, color = "manual", theme.GOLD
            tip = ("Manual fallback — the live boss level can't be read on this "
                   "build (level offset not calibrated), so the Upload-as selector "
                   "in the control panel is used. Set it to match your run.")
        self.mode_lbl.setText(f"{mode.upper()}  ·  {tag}")
        self.mode_lbl.setStyleSheet(f"color:{color};background:transparent;")
        self.mode_lbl.setToolTip(tip)
        self.mode_lbl.show()

    def _render_warn(self):
        """In-run advisory (gold): a manual-started run can't be auto-uploaded, or a
        detected Normal run won't reach any board. Cleared on finish (the result
        message in upload_lbl takes over)."""
        t = self.timer
        if t.state != t.RUNNING:
            self.warn_lbl.hide()
            return
        if t.source == "manual":
            self.warn_lbl.setText("manual start — won't auto-upload (submit on the site with proof)")
        elif self._live_mode == "normal":
            self.warn_lbl.setText("normal run — won't count on any leaderboard")
        else:
            self.warn_lbl.hide()
            return
        self.warn_lbl.setStyleSheet(f"color:{theme.GOLD};background:transparent;")
        self.warn_lbl.show()

    def closeEvent(self, e):
        self._poll.stop()
        super().closeEvent(e)
