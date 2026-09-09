"""Speedrun timer overlay.

A big mono stopwatch driven by the DUNGEON, not by the player: it starts on
zone-in (never mid-fight, never on movement), stops the instant the boss dies,
and clears itself when you leave the instance. Player death does not stop the
RUN clock — only the boss kill does — but the BOSS split resets and re-arms on
the next engage. The companion binds no keys and drives no input.
Tracks a per-boss best (PB) + last time, persisted in Settings.

Out-of-process like every overlay: it only reads game memory (boss HP) to detect
the kill, it never writes.

The HUD is instance-only (Rule D — the `speedrun` row of `overlay_rules.HUD_PLACES`
hides it in the open world),
so the zone-in edge is tracked from hidden ticks as well: the FALSE half of the
transition only ever happens while this window is off screen. See _tick.
"""
from __future__ import annotations

import time

from PySide6 import QtCore, QtWidgets

from .. import theme
from ..copy_utils import copy_text as clipboard_copy
from ..dps_source_text import resolved_mode
from ..overlay_base import OverlayWindow
from ...core import attributes, game_state
from ...core.speedrun import (
    SpeedrunTimer, BossTimer, BossAttachGate, Encounter, ModeLatch, RearmGate,
    WipeWatcher, GroupWipeWatcher, ZoneEdge, kill_entry_with_boss, append_kill_log,
    SHARED_HISTORY_LIMIT, plausible_split, clock_pb_line, fmt_time)
from ...data import names, icons

TICK_MS = 50           # centisecond-smooth display + boss poll while running
DETECT_EVERY = 10      # run the (heavier) dungeon detection every Nth tick (~500ms)

# Proven-outside must HOLD this many consecutive ticks (~0.5s at 50ms) before
# it may ABORT a live run. A void-fall/respawn tick can briefly read outside
# or make the scene unreadable and unload the boss for a tick, and one such
# tick used to wipe the RUN and then let the re-entry start a fresh one — a
# whole run silently reset because the player fell off a ledge. Same reasoning
# the DPS tracker's INSTANCE_EDGE_DEBOUNCE_S uses for its zone-in reset; the
# value is small — a genuine exit stays outside for many seconds and is never
# missed.
OUTSIDE_CONFIRM_TICKS = 10

# (back-to-back re-arm timing lives in core.speedrun.RearmGate)
# Tile sub-row vs tone — tone carries the meaning, this map owns the paint.
_TONE_COLOR = {"new_best": theme.GOLD, "ahead": theme.GOOD,
               "behind": theme.DANGER, "none": theme.MUTED}

_SUB_STYLE = "font-size:11px;letter-spacing:0px;background:transparent;"


class SpeedrunRenderMixin:
    # Provided by the overlay.
    timer: BossTimer
    boss_timer: BossTimer
    s: object
    _scale: float

    def _fit(self):  # type: ignore[no-untyped-def]
        """Height adapts; WIDTH is pinned so the HUD never resizes mid-run."""
        w = max(self.minimumWidth(), round(self._base_w * self._scale))
        lay = self.layout()
        if lay:
            lay.activate()
        h = max(round(self._base_h * self._scale), self.sizeHint().height())
        if (w - self.width()) ** 2 > 4 or (h - self.height()) ** 2 > 4:
            self.resize(w, h)

    def _digits_style(self, color: str, size_px: int) -> str:  # type: ignore[no-untyped-def]
        """Shared stylesheet for a tile's digits (scaled with the overlay zoom)."""
        size = max(14, int(size_px))
        return (f"color:{color};font-family:'{theme.MONO_FONT}','Consolas';"
                f"font-size:{size}px;font-weight:800;background:transparent;letter-spacing:1px;")

    def _render(self) -> None:  # type: ignore[no-untyped-def]
        t = self.timer
        bt = self.boss_timer
        boss_armed = bt.state != BossTimer.READY

        if t.state == t.RUNNING:
            fc = self.s.hud_accent or theme.ACCENT
        elif t.state == t.DONE:
            fc = theme.GOLD if t.is_new_best else theme.GOOD
        else:
            fc = theme.MUTED
        self.time_lbl.setText(fmt_time(t.elapsed()))
        self.time_lbl.setStyleSheet(self._digits_style(fc, 20))

        if boss_armed:
            self.sub_lbl.setText(fmt_time(bt.elapsed()))
            bc = theme.GOLD if (t.state != t.DONE or self.boss_is_new_best) else theme.GOOD
        else:
            self.sub_lbl.setText("--:--.---")
            bc = theme.MUTED
        self.sub_lbl.setStyleSheet(self._digits_style(bc, 20))

        for btn, has in ((self._run_copy, t.last is not None),
                         (self._kill_copy, self.boss_timer.last is not None)):
            btn.setEnabled(has)
            btn.setIcon(icons.ui_qicon("copy", theme.ACCENT if has else theme.MUTED, 14))
        self._render_tile_subs(boss_armed)  # type: ignore[attr-defined]
        self._set_title_provenance()  # type: ignore[attr-defined]
        self._fit()

    def _render_tile_subs(self, boss_armed: bool) -> None:  # type: ignore[no-untyped-def]
        """Each tile's PB / pace / LAST line via the pure clock_pb_line helper."""
        t = self.timer
        bid = t.boss_id or "_"
        profile = self._safe_profile()  # type: ignore[attr-defined]

        text, tone = clock_pb_line(self.s.get_speedrun_best(profile).get(bid),
                                   t.last, is_new_best=t.is_new_best)
        if t.last is not None:
            text += "\nLAST " + fmt_time(t.last)
        self.time_sub.setText(text or "no run yet")
        self.time_sub.setStyleSheet(f"color:{_TONE_COLOR[tone]};{_SUB_STYLE}")

        boss_best = self.s.get_speedrun_boss_best(profile).get(bid)  # type: ignore[attr-defined]
        if boss_armed or self.boss_timer.last is not None or boss_best is not None:
            text, tone = clock_pb_line(boss_best, self.boss_timer.last,  # type: ignore[arg-type]
                                       is_new_best=self.boss_is_new_best)  # type: ignore[attr-defined]
            if self.boss_timer.last is not None:
                text += "\nLAST " + fmt_time(self.boss_timer.last)
            self.sub_sub.setText(text or "no split yet")
            self.sub_sub.setStyleSheet(f"color:{_TONE_COLOR[tone]};{_SUB_STYLE}")
        else:
            self.sub_sub.setText("no split yet")
            self.sub_sub.setStyleSheet(f"color:{theme.MUTED};{_SUB_STYLE}")

    def _title_provenance(self) -> tuple[str | None, str | None]:  # type: ignore[no-untyped-def]
        """(difficulty, source) shown in the title bar: frozen once finished, else live."""
        t = self.timer
        if t.state == t.DONE and self._run_mode:  # type: ignore[attr-defined]
            return self._run_mode, (self._run_mode_src or "manual")  # type: ignore[attr-defined]
        return self._live_mode, self._live_mode_src  # type: ignore[attr-defined]

    def _set_title_provenance(self) -> None:  # type: ignore[no-untyped-def]
        """Difficulty plainly, auto/manual context in the tooltip."""
        mode, src = self._title_provenance()
        if not mode or not self._in_dungeon:  # type: ignore[attr-defined]
            self.titlebar.title.setToolTip("")
            self._set_title(self.model.dungeon_mode() if self.model is not None else None)  # type: ignore[attr-defined]
            return
        tip = ("Difficulty read from the live boss level." if src == "auto" else
               "Manual fallback - the live boss level can't be read on this build "
               "(level offset not calibrated), so the title shows Hard. Hard is "
               "the only difficulty shown; a detected Normal run is "
               "saved to your profile only.")
        self._set_title(mode)  # type: ignore[attr-defined]
        self.titlebar.title.setToolTip(tip)


class SpeedrunDiagnosticsMixin:
    """Transition-only probe logging for instance, map, and start decisions."""

    def _set_run_status(self, text: str) -> None:
        label = getattr(self, "upload_lbl", None)
        if label is not None:
            label.setText(text)
            label.setStyleSheet(
                f"color:{theme.MUTED};background:transparent;")
            label.show()

    def _evaluate_boss_attach(self, current_hp: float | None) -> None:
        """Gate attach-only BOSS timing on pristine HP or explicit pre-combat."""
        try:
            reader = getattr(self.model, "boss_max_health", None)
            max_hp = reader() if callable(reader) else None
        except Exception:
            max_hp = None
        try:
            group_reader = getattr(self.model, "party_combat_state", None)
            if callable(group_reader):
                # The attach fallback is a group decision: every connected
                # member must explicitly report out of combat. None means an
                # unreadable member and must not be treated as False.
                in_combat = group_reader()
            else:
                combat = self.model.combat_state() if self.model is not None else None
                in_combat = combat.get("in_combat") if isinstance(combat, dict) else None
        except Exception:
            in_combat = None
        gate = self._boss_attach_gate
        if not gate.feed(current_hp, max_hp, in_combat):
            return
        self._boss_attach_eligible = gate.eligible
        if gate.state == gate.PRISTINE:
            self.log.emit(
                "Run Timer decision: BOSS pre-hit baseline verified at 100% "
                f"(HP={current_hp:g}/{max_hp:g}); RUN held, BOSS eligible")
            self._set_run_status(
                "RUN held — dungeon already active; BOSS eligible from first hit")
        elif gate.state == gate.PRECOMBAT:
            self.log.emit(
                "Run Timer decision: BOSS pre-combat baseline accepted "
                f"(HP={current_hp:g}; max HP unavailable, combat=False); "
                "RUN held, BOSS eligible")
            self._set_run_status(
                "RUN held — BOSS eligible from pre-combat baseline")
        elif gate.state == gate.DAMAGED:
            self.log.emit(
                "Run Timer decision: BOSS SKIP — fight already started "
                f"(HP={current_hp:g}/{max_hp:g})")
            self._set_run_status(
                "RUN held — BOSS skipped; fight already started")
        elif gate.state == gate.ACTIVE:
            self.log.emit(
                "Run Timer decision: BOSS SKIP — combat already active at "
                "attach; fight start cannot be proven")
            self._set_run_status(
                "RUN held — BOSS skipped; fight already started")
        else:
            self.log.emit(
                "Run Timer decision: BOSS eligibility unknown "
                f"(HP={current_hp if current_hp is not None else 'unreadable'}, "
                "max HP unreadable, combat unreadable); RUN held, BOSS held")
            self._set_run_status(
                "Open FareverPal before zoning in — BOSS starts on the first hit")

    def _log_probe_edge(self, in_dungeon: bool | None) -> None:
        """Emit one compact Activity Log line per proven state/map transition."""
        map_id = None
        if self.model is not None:
            try:
                map_id = game_state.zone(self.model)
            except Exception:
                map_id = None
        instance_changed = in_dungeon is not self._diag_instance
        map_changed = map_id != self._diag_map
        if instance_changed or map_changed:
            state = ("UNKNOWN" if in_dungeon is None
                     else ("INSIDE" if in_dungeon else "OUTSIDE"))
            self.log.emit(
                f"Run Timer probe: instance={state} (proven={in_dungeon is not None}); "
                f"map={map_id or '<none>'}")
            self._diag_instance = in_dungeon
            self._diag_map = map_id

        if in_dungeon is False:
            self._seen_outside_instance = True
            self._diag_hold_logged = False
        elif in_dungeon is True and not self._seen_outside_instance:
            if not self._diag_hold_logged:
                self.log.emit(
                    "Run Timer decision: first observation is INSIDE; "
                    "RUN held until a proven outside -> inside edge")
                self._set_run_status(
                    "Open FareverPal before zoning in — RUN starts on dungeon entry")
                self._diag_hold_logged = True



# --- clipboard line --------------------------------------------------------
# The tiles are captioned RUN / BOSS: clear next to their own digits, meaningless
# in a paste. The clipboard line spells the clock out in the words the rest of
# the feature already uses (PB History's "Full" / "Boss split" columns, the wipe
# message's "boss split reset"), so a line pasted into chat says which clock it
# is rather than echoing whichever short noun the tile happens to wear. A token
# that is not one of these falls through unchanged.
_CLOCK_LABEL = {"RUN": "full run", "BOSS": "boss split"}


def _dungeon_display(model) -> str:
    """Dungeon/area name from position; '--' if unknown."""
    try:
        xyz = model.player_xyz()
        if xyz:
            from ...geo import zones as geo_zones
            from ...data import names
            zid = geo_zones.resolve_zone(*xyz)
            return names.zone_name(zid) or zid or "--"
    except Exception:
        pass
    return "--"


def copy_text(model, clock: str, seconds: float | None,
              boss_id: str | None, mode: str | None) -> str:
    """Clipboard line for one finished clock.

    Player + class, boss, dungeon and difficulty, then the clock and its time.
    ``clock`` is the tile's own short caption; it is translated here rather than
    copied through, because the caption only means anything beside its digits.
    """
    from ...data import names

    player, cls = "--", "--"
    if model is not None:
        try:
            pa = getattr(model, "player_addr", None) or 0
            player = model.player_name(pa) or "--"
        except Exception:
            pass
        try:
            cls = model.player_class() or "--"
        except Exception:
            pass

    from ...core.speedrun import fmt_time
    boss = names.unit_name(boss_id) if boss_id else None
    dungeon = _dungeon_display(model)
    mode_disp = (mode or "?").capitalize()
    time_disp = fmt_time(seconds) if seconds is not None else "--:--.---"
    return (f"{player} ({cls}) · {boss or '--'} · "
            f"{dungeon} ({mode_disp}) · {_CLOCK_LABEL.get(clock, clock)} {time_disp}")

class SpeedrunOverlay(SpeedrunRenderMixin, SpeedrunDiagnosticsMixin, OverlayWindow):
    log = QtCore.Signal(str)

    def __init__(self, model, settings, parent=None):
        super().__init__("Run Timer", settings, geo_key="speedrun", parent=parent)
        # Title-bar difficulty is written as readable title case, e.g. Hard Mode.
        # Detection provenance belongs in the tooltip, not in the HUD text.
        self._title_mode = object()   # sentinel: forces the first paint
        self._set_title(None)
        self._page_key = "speedrun"
        self.model = model
        self.s = settings
        self.timer = SpeedrunTimer()             # the FULL-dungeon run
        self._run_is_timed = False              # full RUN owns a proven zone-in
        self._boss_attach_eligible = False     # observed pristine boss without RUN
        self._boss_attach_gate = BossAttachGate()
        self.boss_timer = BossTimer()            # the BOSS-only split (arms on first hit)
        self.encounter: Encounter | None = None  # current dungeon's boss-fight tracker
        self._prev_state = self.timer.state
        self._detect_ctr = 0
        self._dungeon_bid: str | None = None   # detected dungeon (kill) boss (sticky)
        self._in_dungeon = False                # boss currently present in the scene
        self._zone = ZoneEdge()                 # the ONLY full-run start: zone-in edge
        self._wipe = WipeWatcher()              # solo-death edge: resets only BOSS
        self._group_wipe = GroupWipeWatcher()  # party-wipe edge: resets only BOSS
        self._latch = ModeLatch()               # last good auto-detected difficulty this run
        self._rearm = RearmGate()               # holds the finished result until the player leaves
        self.boss_is_new_best = False           # set True when a finish beats the BOSS-split PB
        self._run_mode: str | None = None       # difficulty captured at finish
        self._run_mode_src: str | None = None    # "auto" (read from boss level) | "manual"
        self._run_start_death_m: float = 0.0    # tracker's wipe marker at run start
        self._live_mode: str | None = None       # difficulty resolved live (for the HUD readout)
        self._live_mode_src: str | None = None
        self._outside_ticks = 0                 # consecutive proven-outside ticks (for the RUN abort gate)
        self._boss_heal_prev: float | None = None  # last kill-boss HP for heal/leash reset
        # Edge diagnostics are transition-only: Activity Log shows the decision
        # timeline without one line per 50 ms timer tick.
        self._diag_instance = object()
        self._diag_map = object()
        self._seen_outside_instance = False
        self._diag_hold_logged = False

        # dungeon-instance icon, shown next to the panel header when detected
        self._dg_icon = QtWidgets.QLabel()
        self._dg_icon.setFixedSize(22, 22)
        self._dg_icon.setScaledContents(True)
        self._dg_icon.hide()
        self.titlebar.extra.insertWidget(0, self._dg_icon)

        # Metric tiles: one per clock, digits + PB/pace/LAST stacked inside so the
        # record data stops running ACROSS the window (the old headline record row
        # was what stretched the HUD to 600+ px on a finish). Both records are
        # permanently on screen - there is no view mode left to switch.
        grid = QtWidgets.QHBoxLayout()
        grid.setContentsMargins(0, 2, 0, 0)
        grid.setSpacing(8)
        self.time_lbl = QtWidgets.QLabel("00:00.00")          # RUN digits
        self.time_sub = QtWidgets.QLabel("")                  # PB / pace / LAST
        self.sub_lbl = QtWidgets.QLabel("--:--.---")          # BOSS digits
        self.sub_sub = QtWidgets.QLabel("")
        # RUN is the whole dungeon (zone-in -> boss kill); BOSS is the split
        # from the fight's first damage to that same kill. Both clocks stop
        # together, so neither is nested in the other - the labels only say
        # which stopwatch each one is.
        self._run_tile, self._run_copy = self._build_tile(
            "RUN", self.s.hud_accent or theme.ACCENT,
            self.time_lbl, self.time_sub,
            lambda: self._copy_clock("RUN", self.timer.last))
        self._kill_tile, self._kill_copy = self._build_tile(
            "BOSS", theme.GOLD, self.sub_lbl, self.sub_sub,
            lambda: self._copy_clock("BOSS", self.boss_timer.last))
        grid.addWidget(self._run_tile, 1)
        grid.addWidget(self._kill_tile, 1)
        self.content.addLayout(grid)

        # Local status line (death / abort messages — not network upload).
        self.upload_lbl = QtWidgets.QLabel("")
        self.upload_lbl.setObjectName("Mono")
        self.upload_lbl.setAlignment(QtCore.Qt.AlignCenter)
        self.upload_lbl.setWordWrap(True)
        self.upload_lbl.hide()
        self.content.addWidget(self.upload_lbl)

        # Automatic: starts on zone-in, stops on boss death.
        self._base_w, self._base_h = 330, 200
        self.setMinimumWidth(300)
        self.apply_scale(1.0)

        self._poll = QtCore.QTimer(self)
        self._poll.timeout.connect(self._tick)
        self._poll.start(TICK_MS)
        self._render()

    def _build_tile(self, caption: str, color: str,
                    digits: QtWidgets.QLabel, sub: QtWidgets.QLabel,
                    copy_cb) -> tuple[QtWidgets.QFrame, QtWidgets.QPushButton]:
        """One metric tile: caption + copy btn, digits, PB/pace/LAST stacked.

        Returns the tile AND its copy button, so the caller keeps each button
        under its own name. Matching on the caption text to tell the two apart
        (the previous shape) silently swapped them the moment a label changed.
        """
        frame = QtWidgets.QFrame()
        frame.setObjectName("SpeedrunCell")
        frame.setStyleSheet("QFrame#SpeedrunCell { background: transparent; border: 0; }")
        v = QtWidgets.QVBoxLayout(frame)
        v.setContentsMargins(10, 6, 10, 7)
        v.setSpacing(2)
        cap_row = QtWidgets.QHBoxLayout()
        cap_row.setSpacing(4)
        cap = QtWidgets.QLabel(caption)
        cap.setStyleSheet(f"color:{color};font-size:10px;letter-spacing:2px;background:transparent;")
        cap_row.addWidget(cap)
        cap_row.addStretch(1)
        cp = QtWidgets.QPushButton()
        cp.setObjectName("Icon")
        cp.setFixedSize(18, 18)
        cp.setIcon(icons.ui_qicon("copy", theme.MUTED, 14))
        cp.setToolTip(f"Copy this {caption} result to the clipboard")
        cp.clicked.connect(lambda _checked=False: copy_cb())
        cap_row.addWidget(cp, 0, QtCore.Qt.AlignVCenter)
        v.addLayout(cap_row)
        digits.setAlignment(QtCore.Qt.AlignCenter)
        digits.setObjectName("Mono")
        sub.setAlignment(QtCore.Qt.AlignCenter)
        sub.setObjectName("Mono")
        sub.setStyleSheet(f"color:{theme.MUTED};{_SUB_STYLE}")
        v.addWidget(digits)
        v.addWidget(sub)
        return frame, cp

    def _run_bid(self) -> str | None:
        """Run boss id: tracked boss, else detected dungeon boss."""
        return self.timer.boss_id or self._dungeon_bid

    def _safe_profile(self) -> str:
        """Character profile string; '' on read failure."""
        try:
            return self.model.player_profile() if self.model is not None else ""
        except Exception:
            return ""

    # --- clipboard -------------------------------------------------------
    def _copy_clock(self, clock: str, seconds: float | None) -> None:
        """Copy one finished clock as a full result line: player, class, boss, dungeon, mode, time."""
        t = self.timer
        bid = t.boss_id or self._dungeon_bid
        done = t.state == t.DONE
        mode = (self._run_mode if done and self._run_mode else None) or self._live_mode
        if not mode:
            mode = self._resolve_mode()[0]
        clipboard_copy(copy_text(self.model, clock, seconds, bid, mode))

    # --- actions ---------------------------------------------------------

    def reset(self, prime_zone: bool | None = None):
        """Reset both clocks while preserving the real instance edge.

        ``True`` means the caller has a proven outside observation, so the next
        inside tick is a genuine zone-in. ``False`` means already inside (or a
        same-instance post-finish re-arm), which must not ghost-start. ``None``
        means the map read was unknown; leave the edge neutral so the first
        inside observation only arms it.
        """
        self.timer.reset()
        self._dungeon_bid = None
        self._in_dungeon = False
        self._run_is_timed = False
        self._boss_attach_eligible = False
        self._boss_attach_gate.reset()
        self._zone.reset()
        if prime_zone is True:
            self._zone.feed(False)
        elif prime_zone is False:
            self._zone.prime_inside()
        self._seen_outside_instance = prime_zone is True
        self._diag_hold_logged = False
        self._outside_ticks = 0
        self._boss_heal_prev = None
        try:
            map_id = game_state.zone(self.model) or "<none>" if self.model else "<none>"
        except Exception:
            map_id = "<none>"
        edge = ("OUTSIDE" if prime_zone is True else
                "INSIDE" if prime_zone is False else "UNKNOWN")
        self.log.emit(
            f"Run Timer reset: both clocks cleared; preserved edge={edge}; map={map_id}")
        if self.encounter is not None:
            # Fresh fight tracking: without this a stale engaged latch would
            # start the next run the instant it shows READY, with no new hit.
            self.encounter.reset()
        self._wipe.reset()
        self._group_wipe.reset()
        self._latch.reset()
        self._rearm.reset()
        self.boss_is_new_best = False
        self._run_mode = None
        self._run_mode_src = None
        self.upload_lbl.hide()
        self._dg_icon.hide()
        self._render()

    # --- loop ------------------------------------------------------------
    def _set_title(self, mode: str | None) -> None:
        """Readable fixed-size title: 'Run Timer · Hard Mode'."""
        if mode == self._title_mode:
            return
        self._title_mode = mode
        title = "Run Timer"
        if mode:
            title += f" · {mode.capitalize()} Mode"
        # Do not use rich-text spans here. Their tiny suffix size interacted
        # badly with per-overlay QSS scaling and could render near 2px. A plain
        # label with one direct style keeps the complete title readable at
        # every HUD zoom level.
        self.titlebar.title.setText(title)
        self.titlebar.title.setStyleSheet(
            f"color:{theme.TEXT};background:transparent;"
            f"font-family:'{theme.UI_FONT}','Segoe UI',sans-serif;"
            "font-size:13px;font-weight:700;")

    def _instance_now(self) -> bool | None:
        """Proven `in_instance` for this tick, or None when the read failed.

        The shared snapshot is deliberately tri-state here. An unreadable scene
        tick must not become a synthetic False and then a later True, because
        that False→True pair looks exactly like a zone-in and would start RUN
        while the player is already fighting.
        """
        if self.model is None:
            return None
        st = game_state.published_state(self.model)
        if st is not None:
            if not st.located:
                return None
            return st.in_instance if st.instance_known else None
        # Before the shared snapshot exists, an unlocated real model is still
        # UNKNOWN — never a proven outside baseline. Test doubles may use 0 as
        # their synthetic located address, so only None suppresses the read.
        if getattr(self.model, "player_addr", None) is None:
            return None
        try:
            reader = getattr(self.model, "instance_state", None)
            if callable(reader):
                pair = reader()
                if pair is None:
                    return None
                return bool(pair[0] or pair[1])
            return bool(game_state.in_instance(self.model))
        except Exception:
            return None

    def _tick(self):
        # The instance edge is tracked BEFORE the visibility gate, and that is
        # load-bearing. Outside an instance Rule D hides this window, so _tick
        # used to return right here and the FALSE half of the zone-in edge was
        # never observed: ZoneEdge saw nothing but True, armed without firing,
        # and the RUN clock never started. The manager's map-change reset was
        # the only priming path left, which made the start depend on a map id
        # change rather than on the player actually entering a dungeon.
        in_dungeon = self._instance_now()
        self._log_probe_edge(in_dungeon)
        # Proven-outside must HOLD before it may abort a live run — one void-fall
        # tick that briefly reads outside / unloads the boss used to wipe the RUN.
        # None (unreadable) is never evidence and restarts the streak (see the
        # constant above). This gate covers both the hidden abort here and the
        # boss-absence abort fed via feed_boss.
        if in_dungeon is False:
            self._outside_ticks += 1
        elif in_dungeon is None:
            self._outside_ticks = 0
        else:
            self._outside_ticks = 0
        confirmed_outside = self._outside_ticks >= OUTSIDE_CONFIRM_TICKS
        if not self.isVisible():
            if in_dungeon is not None:
                self._in_dungeon = in_dungeon
            if in_dungeon is False:
                # Outside an instance: arm the zone-in edge so the first
                # VISIBLE tick inside starts the run (on every proven-outside
                # tick so the edge is certainly armed). A clock still counting
                # when the player is back out in the world is the same
                # "left the dungeon" abort the visible tick detects, so it is
                # cancelled here too once the outside reading is sustained —
                # instead of on the very first outside tick (which a void-fall
                # tick can fake). Nothing is consumed while hidden INSIDE an
                # instance: the edge stays armed, so a zone-in that happens
                # while the HUD is hidden (alt-tab, another app focused) still
                # starts the run when the HUD comes back.
                if confirmed_outside and self.timer.state == self.timer.RUNNING:
                    self._cancel_run("run cancelled — left the dungeon")
                self._zone.feed(False)
            return
        # Live dungeon-mode badge, straight off the model read (independent
        # of the dungeon: it is context, not run state).
        try:
            self._set_title(self.model.dungeon_mode())
        except Exception:
            pass
        t = self.timer
        running = (t.state == t.RUNNING)

        # ONE scene read per tick: every tick while running (responsive auto-stop),
        # else on a slower cadence for dungeon detection.
        # enc = (members, kill_id, states) where states = [(unit_id, present, hp)].
        enc = None
        if self.model is not None:
            # Update dungeon status every tick so RUN sees enter/leave promptly.
            # Only a proven outside -> inside edge starts RUN. Unknown ticks and
            # a first observation while already inside merely arm the edge.
            # Movement and boss damage never start RUN.
            if in_dungeon is not None:
                self._in_dungeon = in_dungeon
                zone_in = self._zone.feed(in_dungeon)
                if zone_in and t.state != t.RUNNING:
                    t.start("zone")
                    self._run_is_timed = True
                    map_id = game_state.zone(self.model) or "<none>"
                    self.log.emit(
                        "Run Timer decision: RUN START on proven outside -> inside; "
                        f"map={map_id}")

            self._detect_ctr += 1
            detect_now = self._detect_ctr >= DETECT_EVERY
            if running or detect_now or self._boss_attach_eligible:
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
            if (not self._run_is_timed and self._in_dungeon
                    and self.encounter is not None):
                self._evaluate_boss_attach(kill_tuple[2])

            self._update_dungeon_icon()
            # Boss respawn / leash after a wipe: RUN keeps timing, BOSS goes
            # back to READY and re-arms on the next hit. Only a heal that
            # brings the boss back to ~100% while out of combat trips it —
            # bosses that self-heal to full mid-fight stay in combat, so the
            # split keeps timing.
            cur_boss_hp = kill_tuple[2] if kill_tuple[1] else None
            if (self._boss_heal_prev is not None and cur_boss_hp is not None
                    and cur_boss_hp > 0 and cur_boss_hp > self._boss_heal_prev
                    and (running or self.boss_timer.state != BossTimer.READY)):
                ref = None
                try:
                    fn = getattr(self.model, "boss_max_health", None)
                    if callable(fn):
                        cand = fn()
                        if cand is not None and cand > 0:
                            ref = float(cand)
                except Exception:
                    ref = None
                if ref is None and self.encounter is not None:
                    try:
                        peak = float(self.encounter._kill_peak or 0)
                        if peak > 0:
                            ref = peak
                    except Exception:
                        ref = None
                is_full = False
                if ref is not None and ref > 0:
                    is_full = cur_boss_hp >= ref * (1.0 - BossAttachGate.FULL_HP_TOLERANCE)
                    if not is_full and cur_boss_hp >= ref * 0.99:
                        is_full = True
                if is_full:
                    in_combat = None
                    try:
                        grp = getattr(self.model, "party_combat_state", None)
                        if callable(grp):
                            in_combat = grp()
                        else:
                            cs = getattr(self.model, "combat_state", None)
                            if callable(cs):
                                st = cs()
                                if isinstance(st, dict):
                                    in_combat = st.get("in_combat")
                    except Exception:
                        in_combat = None
                    if in_combat is False:
                        self._on_boss_reset("healed")
            if kill_tuple[1] and cur_boss_hp is not None:
                self._boss_heal_prev = cur_boss_hp
            # Refresh the live difficulty readout (cheap) and, while the boss is
            # alive and fighting, latch a confident auto read so the finish-frame
            # (boss dying) can't flip Normal<->Hard.
            self._live_mode, self._live_mode_src = self._resolve_mode()
            # ARM only a LIVE fight of an UNFINISHED run. Both halves are
            # load-bearing: `engaged` is a sticky latch that survives the kill,
            # and `_run_is_timed` survives the finish - so damage landing after
            # the boss died (its adds, or the next pull in the same instance)
            # armed a fresh split under the already-finished RUN. That split
            # then had nothing that could stop it (every stop path needs a live
            # RUN or a fresh kill), so the BOSS tile counted on under the frozen
            # total; and with the kill already confirmed it was "finished" at
            # 00:00 and recorded as a boss best, i.e. a gold 00:00.00 NEW BEST.
            # RUN and BOSS stop together - that is how the tiles are labelled.
            boss_live = cur_boss_hp is not None and cur_boss_hp > 0
            if (self.encounter is not None and self.encounter.engaged
                    and boss_live and t.state != t.DONE
                    and (self._run_is_timed or self._boss_attach_eligible)):
                was_ready = self.boss_timer.state == BossTimer.READY
                self.boss_timer.arm()
                if was_ready and self.boss_timer.state == BossTimer.RUNNING:
                    self.log.emit(
                        "Run Timer decision: BOSS ARM on fresh encounter damage")
                    if not self._run_is_timed:
                        self._set_run_status(
                            "RUN held — BOSS is recording from the first fresh hit")

            if running:
                self._latch.observe(self._live_mode, self._live_mode_src)
                if in_dungeon is True:
                    outside_for_boss = False
                elif in_dungeon is False:
                    outside_for_boss = confirmed_outside
                else:
                    outside_for_boss = None
                res = t.feed_boss(*kill_tuple, outside=outside_for_boss)
                if res == t.LEFT:
                    self._on_run_aborted()
                elif res == t.KILL:
                    boss_was_running = self.boss_timer.state == BossTimer.RUNNING
                    self.boss_timer.stop()
                    boss_result = "FINISHED" if boss_was_running else "not armed"
                    self.log.emit(
                        "Run Timer decision: RUN FINISH on confirmed boss kill; "
                        f"BOSS={boss_result}")
            elif (self.encounter is not None and self.encounter.kill_confirmed
                  and self.boss_timer.state == BossTimer.RUNNING):
                self.boss_timer.stop()
                self.boss_is_new_best = self._record_best()
                self.log.emit(
                    "Run Timer decision: BOSS FINISH without RUN (app attached "
                    "before first hit)")
                self._set_run_status("BOSS finished — RUN was not started")

        # Death / boss-reset never stop RUN. Reset BOSS for solo death,
        # a full-group wipe, or the boss healing/leashing.
        if (running or self.boss_timer.state == BossTimer.RUNNING) and self.model is not None:
            reset_reason = self._death_reset_reason()
            if reset_reason:
                self._on_player_death(reset_reason)

        # Run just started -> arm a fresh difficulty latch + boss split.
        if t.state == t.RUNNING and self._prev_state != t.RUNNING:
            self.boss_is_new_best = False
            self._latch.reset()
            self._wipe.reset()
            self._group_wipe.reset()
            self._run_start_death_m = self._run_death_baseline()
            self.boss_timer.reset()
            if self.encounter is not None:
                self.encounter.reset()
            self.upload_lbl.hide()

        # Run just finished -> capture difficulty, PB. Prefer the value
        # latched while the boss was alive over a fresh read (the boss is dead
        # now, so a fresh read often can't see it). PB only counts a
        # CONFIRMED kill (nothing else may set a bogus PB).
        if t.state == t.DONE and self._prev_state != t.DONE:
            self._rearm.reset()                          # grace counts from the finish
            # Freeze the boss split too (a non-kill finish won't have hit it).
            if self.boss_timer.state == BossTimer.RUNNING:
                self.boss_timer.stop()
            self._run_mode, self._run_mode_src = self._latch.resolve(*self._resolve_mode())
            if t.is_kill:
                self._record_best()
                self._record_kill_log()

        # Back-to-back: after a finished run, re-arm for the next one without the
        # user clicking reset. NOT keyed on boss presence - the boss despawns on
        # death, which used to wipe the finished times within a tick of the kill.
        # The RearmGate holds the result until the player actually leaves (exit
        # teleport / load screen) or a generous grace passes. Always on.
        if t.state == t.DONE:
            try:
                pos = self.model.player_xyz() if self.model is not None else None
            except Exception:
                pos = None
            if self._rearm.feed(pos):
                self.reset(prime_zone=False)

        self._prev_state = t.state
        self._render()

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

    def _record_best(self) -> bool:
        """Persist FULL/BOSS PBs independently; return whether either improved.

        A sub-second time is not a PB: it is the 00:00 bug's artifact (a split
        armed and stopped inside one tick, or armed under an already finished
        run), and storing one showed a gold `00:00.00 NEW BEST` that every
        later real kill was then measured against as "behind". The floor is
        core.speedrun.plausible_split, also enforced at the store.
        """
        bid = self.timer.boss_id or self._dungeon_bid or "_"
        saved = False
        cur = self.timer.last
        profile = self._safe_profile()
        if plausible_split(cur):
            best_dict = self.s.get_speedrun_best(profile)
            best = best_dict.get(bid)
            if best is None or cur < best:
                best_dict[bid] = cur
                self.s.save_speedrun_best(profile, best_dict)
                self.timer.is_new_best = True
                saved = True
        bcur = self.boss_timer.last
        if plausible_split(bcur) and self.boss_timer.state == BossTimer.DONE:
            boss_best_dict = self.s.get_speedrun_boss_best(profile)
            bbest = boss_best_dict.get(bid)
            if bbest is None or bcur < bbest:
                boss_best_dict[bid] = bcur
                self.s.save_speedrun_boss_best(profile, boss_best_dict)
                self.boss_is_new_best = True
                saved = True
        if saved and not profile:
            self.s.save()
        return saved

    def _local_hero_hp(self) -> float | None:
        """Local hero HP for the wipe edge; None when unreadable (never a death)."""
        try:
            pa = getattr(self.model, "player_addr", 0) or                 getattr(getattr(self.model, "dps", None), "_last_local_hero_addr", 0)
            return attributes.health(self.model.hl, pa) if pa else None
        except Exception:   # ProcError included: an unreadable read is never a death
            return None

    def _run_death_baseline(self) -> float:
        """The tracker's wipe marker right now (0.0 when no tracker)."""
        try:
            dps = getattr(self.model, "dps", None)
            return float(getattr(dps, "last_local_death_m", 0.0) or 0.0)
        except Exception:
            return 0.0

    def _wipe_detected(self) -> bool:
        """True once the local player died this run: the tracker's wipe marker (the same edge the meter uses) with a direct HP read as fallback."""
        try:
            dps = getattr(self.model, "dps", None)
            marker = getattr(dps, "last_local_death_m", None)
            if marker is not None:
                return float(marker or 0.0) > self._run_start_death_m
        except Exception:
            pass
        return self._wipe.feed(self._local_hero_hp())

    def _death_reset_reason(self) -> str | None:
        """Whether this tick completes a solo death or full-group wipe."""
        try:
            state = self.model.party_health_state()
        except Exception:
            state = None
        if state is not None:
            grouped, alive, present = state
            if grouped:
                if self._group_wipe.feed(grouped, alive, present):
                    return "group wipe"
                return None
        elif self.model is not None:
            return None
        return "death" if self._wipe_detected() else None

    def _record_kill_log(self) -> None:
        """Append a confirmed kill to the shared cross-character kill log
        (newest last, capped at SHARED_HISTORY_LIMIT). Only confirmed kills
        land here — wipes and aborts stops never do."""
        bid = self._run_bid()
        last = self.timer.last
        # A sub-second full run describes no fight, so it never reaches the
        # kill log (the 00:00 bug's row had boss_ms=0 on it).
        if not bid or not plausible_split(last):
            return
        profile = self._safe_profile()
        dungeon = _dungeon_display(self.model)
        if dungeon in ("", "--"):
            dungeon = None
        boss_ms = self.boss_timer.last
        entry = kill_entry_with_boss(
            bid, int(round(last * 1000)),
            int(round(boss_ms * 1000)) if boss_ms is not None else None,
            self._run_mode, time.time(),
            char=profile or None, dungeon=dungeon)
        try:
            runs = self.s.get_speedrun_history()
        except Exception:
            runs = []
        try:
            # Read-modify-write on the shared file: safe because exactly one
            # companion process ever writes it (no file lock is needed unless
            # two writers ever exist at once).
            self.s.save_speedrun_history(
                append_kill_log(runs, entry, limit=SHARED_HISTORY_LIMIT))
        except Exception:
            pass

    def _cancel_run(self, msg: str) -> None:
        """Reset a live run, recording nothing (wipe / left). The zone edge is
        cleared so the next start is a real zone-in, never a same-pull ghost."""
        self.timer.reset()
        self._run_is_timed = False
        self._boss_attach_eligible = False
        self._boss_attach_gate.reset()
        self.boss_timer.reset()
        if self.encounter is not None:
            self.encounter.reset()
        self._zone.reset()
        self._wipe.reset()
        self._group_wipe.reset()
        self._latch.reset()
        self._rearm.reset()
        self.boss_is_new_best = False
        self._run_mode = None
        self._run_mode_src = None
        self._outside_ticks = 0
        self._boss_heal_prev = None
        self.upload_lbl.setText(msg)
        self.upload_lbl.setStyleSheet(f"color:{theme.MUTED};background:transparent;")
        self.upload_lbl.show()


    def _on_player_death(self, reason: str = "death"):
        """Death/wipe mid-run: RUN keeps timing — only the boss ends it.
        BOSS resets and re-arms on the next fight engage. Re-baselines the
        local wipe marker so corpse/respawn ticks do not re-fire every frame."""
        self.boss_timer.reset()
        if self.encounter is not None:
            # Clear engaged so the split waits for a real next hit, not the
            # corpse still sitting at the damage level from before death.
            self.encounter.reset()
        self._wipe.reset()
        self._group_wipe.reset()
        self._boss_heal_prev = None
        self._run_start_death_m = self._run_death_baseline()
        prefix = "group wipe —" if reason == "group wipe" else "died —"
        self.log.emit(
            f"Run Timer decision: BOSS RESET ({reason}); RUN continues")
        self.upload_lbl.setText(f"{prefix} boss split reset (run continues)")
        self.upload_lbl.setStyleSheet(f"color:{theme.MUTED};background:transparent;")
        self.upload_lbl.show()
        self._render()

    def _on_boss_reset(self, reason: str = "heal/leash") -> None:
        """Boss healed / leashed mid-run: RUN keeps timing, BOSS split resets.

        Mirrors the dungeon HUD's fight-reset heuristic (prev_bhp <= 0 or
        bhp > prev*1.25 means the boss spawned fresh or fully healed after a
        wipe/leash). The split goes back to READY and re-arms on the next hit,
        exactly like a wipe. No PB is recorded."""
        self.boss_timer.reset()
        if self.encounter is not None:
            self.encounter.reset()
        self._boss_heal_prev = None
        self.log.emit(f"Run Timer decision: BOSS RESET ({reason}); RUN continues")
        self.upload_lbl.setText("boss reset — boss split reset (run continues)")
        self.upload_lbl.setStyleSheet(f"color:{theme.MUTED};background:transparent;")
        self.upload_lbl.show()
        self._render()

    def _on_run_aborted(self):
        """Player left the dungeon / hit the main menu mid-run: cancel the run without recording a time."""
        self.log.emit(
            "Run Timer decision: RUN CANCEL — boss disappeared while healthy; "
            "nothing recorded")
        self._cancel_run("run cancelled — left the dungeon")

    def _resolve_mode(self) -> tuple[str, str]:
        """(difficulty, source) shown on the title bar. source is "auto" when
        the game itself gave the difficulty (instance config or live boss
        level), else "manual" — the fallback, used when neither can be read on
        this build, so a guess is never presented as a read.

        Delegates to `dps_source_text.resolved_mode`: the DPS surfaces show the
        same difficulty, and three surfaces answering one question must not be
        able to drift apart."""
        return resolved_mode(self.model)

    def set_model(self, model) -> None:
        self.model = model
        self._diag_instance = object()
        self._diag_map = object()
        self._seen_outside_instance = False
        self._diag_hold_logged = False
        self._outside_ticks = 0
        self.log.emit("Run Timer probe: model attached" if model is not None
                      else "Run Timer probe: model detached")

    def closeEvent(self, e):
        self._poll.stop()
        super().closeEvent(e)
