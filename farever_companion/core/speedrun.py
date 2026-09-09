"""Speedrun timer + dungeon-boss-kill watcher.

A tiny, process-free state machine. The overlay drives it: it ticks the elapsed
time for display and feeds the tracked boss's liveness each poll so the run can
auto-stop the instant the dungeon boss dies (was alive, now dead/gone).

States: ready -> running -> done. Nothing here starts or stops a run by hand:
both edges come from the dungeon (first boss damage, then the kill).
"""
from __future__ import annotations

import time


def fmt_time(secs: float) -> str:
    """`mm:ss.cs` (centiseconds), the speedrun-readable format."""
    secs = max(0.0, secs)
    m = int(secs // 60)
    s = secs - m * 60
    return f"{m:02d}:{s:05.2f}"


def delta_text(current: float | None, best: float | None) -> str | None:
    """`Δ +1.98` / `Δ −1.90` for a clock against its PB, or None when either
    side is unknown. The sign IS the message — plus means behind the PB, minus
    means ahead — which is why it is drawn rather than left to colour alone.
    A real minus glyph, not a hyphen."""
    if current is None or best is None:
        return None
    d = current - best
    return f"Δ {'+' if d >= 0 else '−'}{abs(d):.2f}"


def clock_pb_line(best: float | None, current: float | None,
                  is_new_best: bool = False) -> tuple[str, str]:
    """One clock's PB line for the HUD as ``(text, tone)``.

    ``text`` is what the overlay draws beside that clock's digits: its PB and,
    once there is a time to compare it with, the signed gap to it. ``tone``
    carries the MEANING so the caller owns the colours — ``"new_best"`` (gold),
    ``"ahead"`` (green), ``"behind"`` (red), ``"none"``.

    A clock with no stored PB says so rather than showing nothing: with the
    headline record line gone from the HUD, this is the only place that still
    answers "have I ever run this?". Pure/headless so the
    readout is testable without Qt.
    """
    if is_new_best:
        t = best if best is not None else current
        if t is not None:
            return f"PB {fmt_time(t)}   ★ NEW BEST", "new_best"
    if best is None:
        return ("no PB yet" if current is not None else ""), "none"
    text = f"PB {fmt_time(best)}"
    d = delta_text(current, best)
    if d is None:
        return text, "none"
    return f"{text}   {d}", ("behind" if current > best else "ahead")


class SpeedrunTimer:
    READY, RUNNING, DONE = "ready", "running", "done"

    # feed_boss outcomes
    ALIVE, KILL, LEFT = "alive", "kill", "left"

    # A vanished boss counts as a KILL only if its last-seen HP was within this
    # fraction of the max HP we observed (i.e. it was at death's door). A boss
    # that disappears while still healthy means the PLAYER left - leaving the
    # dungeon / going to the main menu despawns the boss at full HP - so that is
    # an aborted run, never a kill. (Bias is deliberately toward missing an
    # auto-stop, which the user can finish by hand, over recording a non-kill.)
    KILL_HP_FRAC = 0.05

    # A healthy boss that vanished while the scene is unreadable is only an
    # ABORT after this many consecutive absent ticks (~2s at the overlay's
    # 50ms tick). A void-fall / respawn tick can briefly say "outside" or make
    # the scene unreadable and unload the arena's boss for a tick or two -
    # cancelling the whole RUN on that one blip is what silently reset long
    # runs when the player fell off a ledge. Proven-inside absence never
    # aborts at all (see feed_boss `outside`), so this debounce only covers
    # the UNKNOWN (unreadable) case - the main-menu path that has no proven
    # outside reading to lean on.
    GONE_TICKS = 40

    def __init__(self):
        self.state = self.READY
        self._t0 = 0.0
        self._frozen = 0.0
        self._boss_seen_alive = False
        self._last_hp: float | None = None    # boss HP at its last in-scene read
        self._max_hp = 0.0                     # max boss HP observed this run
        self._gone_ticks = 0                  # consecutive healthy-absence ticks when outside is unknown
        self.boss_id: str | None = None     # boss tracked for this run (display)
        self.last: float | None = None       # last finished time (s)
        self.is_new_best = False             # set True on a finish that beat PB
        self.is_kill = False                 # True iff this finish was a real boss kill
    # --- control ---------------------------------------------------------
    def start(self, mode: str | None = None) -> None:
        # ``mode`` is accepted for compatibility with the overlay's existing
        # ``start("auto")`` calls. The timer is always dungeon-driven; the
        # value is informational only and does not change timing behavior.
        # The overlay starts a run only from a proven dungeon/rift zone-in or
        # the first proven in-instance observation after app attach. The app
        # binds no keys and the HUD has no manual start control.
        self.state = self.RUNNING
        self._t0 = time.monotonic()
        self._frozen = 0.0
        self._boss_seen_alive = False
        self._last_hp = None
        self._max_hp = 0.0
        self.is_new_best = False
        self.is_kill = False

    def stop(self, kill: bool = False) -> None:
        if self.state == self.RUNNING:
            self._frozen = time.monotonic() - self._t0
            self.state = self.DONE
            self.last = self._frozen
            self.is_kill = kill

    def reset(self) -> None:
        self.state = self.READY
        self._frozen = 0.0
        self._boss_seen_alive = False
        self._last_hp = None
        self._max_hp = 0.0
        self._gone_ticks = 0
        self.boss_id = None
        self.last: float | None = None      # last finished time (s)
        self.is_new_best = False
        self.is_kill = False

    # --- query -----------------------------------------------------------
    def elapsed(self) -> float:
        if self.state == self.RUNNING:
            return time.monotonic() - self._t0
        return self._frozen

    # --- auto-stop on boss kill -----------------------------------------
    def feed_boss(self, boss_id: str | None, present: bool, hp: float | None,
                  outside: bool | None = None) -> str:
        """Update boss tracking for the active run. Returns:
          KILL , the tracked boss just died (auto-stops the timer, kill=True),
          LEFT , the boss vanished while still healthy => the player left the
                  dungeon / hit the main menu; the run must be cancelled, NOT
                  uploaded,
          ALIVE, nothing conclusive yet (keep running).
        Only ALIVE/KILL change the timer here; the caller handles LEFT.

        ``outside`` is the caller's proven instance answer for THIS tick:
        True (proven outside) -> a healthy despawn IS leaving; False (proven
        inside) -> a healthy despawn is a scene blip (void-fall / respawn /
        arena unload) and the run must keep timing; None (unreadable) -> only
        abort after GONE_TICKS consecutive healthy absences, so one bad tick
        never cancels a run while a genuine quit-to-menu (sustained absence)
        still does.
        """
        if self.state != self.RUNNING:
            return self.ALIVE
        if boss_id:
            self.boss_id = boss_id
        if present:
            self._boss_seen_alive = True
            self._gone_ticks = 0
            if hp is not None:
                if hp <= 0:                       # died in scene (corpse at 0 HP)
                    self.stop(kill=True)
                    return self.KILL
                self._last_hp = hp
                self._max_hp = max(self._max_hp, hp)
            return self.ALIVE
        # boss not present (despawned). Nothing to conclude unless we saw it alive.
        if not self._boss_seen_alive:
            return self.ALIVE
        # Despawned after being tracked: a KILL only if it was near death when
        # last seen; otherwise the player left (full/high HP) - abort, no record.
        if (self._last_hp is not None and self._max_hp > 0
                and self._last_hp <= self._max_hp * self.KILL_HP_FRAC):
            self.stop(kill=True)
            return self.KILL
        if outside is True:
            # Proven outside: the overlay's OUTSIDE_CONFIRM_TICKS gate already ensured
            # this wasn't a one-tick void-fall blip.
            self._gone_ticks = 0
            return self.LEFT
        if outside is False:
            # Proven inside: a healthy boss vanishing is a scene blip (void-fall /
            # respawn / arena unload), never leaving the dungeon - keep the RUN.
            self._gone_ticks = 0
            return self.ALIVE
        # Unreadable scene (outside is None): only treat a sustained healthy
        # absence as leaving, so a transient tick never resets a long run while
        # a real quit-to-menu still aborts.
        self._gone_ticks += 1
        if self._gone_ticks >= self.GONE_TICKS:
            self._gone_ticks = 0
            return self.LEFT
        return self.ALIVE


class AutoStarter:
    """Decides when a run auto-starts: pure, headless, unit-testable.

    Fed `(in_dungeon, pos)` once per overlay tick, it returns True exactly once -
    on the tick the player makes *real* movement away from a settled spawn
    baseline. Separated from the overlay (which only does I/O) so the start
    behaviour can be tested without the game or Qt.

    Why a settle gate: arriving in a dungeon teleports the player in, and the
    position can jitter for a few ticks while the scene loads/the camera settles.
    The old logic baselined the very first in-dungeon position, so that settling
    jitter could exceed the move threshold and trip the timer "on enter" some
    runs but not others (the inconsistency Hooch reported). We instead wait until
    the player has been essentially stationary for a few consecutive ticks, then
    baseline *that* resting spawn point, so only deliberate walking starts the
    run, every time.
    """

    # "Standing still" = staying within SETTLE_EPS of where the still-streak began
    # for SETTLE_TICKS consecutive ticks. Measuring against the streak ANCHOR (not
    # the per-tick step) is what rejects the teleport-in LANDING: when you arrive
    # you spawn slightly above the floor and settle down a couple of units over
    # ~1s. Each tick of that drop is a tiny step (well under any per-tick epsilon),
    # so the old per-tick test counted the fall as 'still', baselined mid-fall, and
    # then the remaining settle crossed the move threshold - a false start with no
    # real input. Anchored distance grows as the fall continues, so it never
    # settles until the landing actually stops. (Trace: X/Y fixed, Z 17.4->15.2.)
    SETTLE_EPS = 0.6        # radius (from the streak anchor) that still counts as "still"
    SETTLE_TICKS = 10       # ~0.5s held inside that radius before the baseline is trusted
    MOVE_THRESHOLD = 2.0    # distance from the baseline that counts as "the run began"
    TELEPORT_STEP = 50.0    # a single-tick jump bigger than this == load/teleport, not walking

    def __init__(self):
        self.reset()

    def reset(self) -> None:
        self._base = None       # settled spawn position (baseline) once trusted
        self._last = None       # previous tick position
        self._anchor = None     # position where the current still-streak began
        self._settle = 0        # consecutive ticks held within SETTLE_EPS of the anchor

    @staticmethod
    def _dist(a, b) -> float:
        # HORIZONTAL (ground-plane x,y) distance only - deliberately ignore the
        # vertical axis. The teleport-in LANDING settles the player downward by a
        # couple of units in the 3rd coord while x,y stay fixed (observed:
        # (-98.8,347.8,17.4)->(-98.8,347.8,15.2)); counting that as movement was
        # the false auto-start. The minimap's world->map transform confirms x,y are
        # the ground plane and the 3rd coord is height. Real walking moves x,y.
        return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5

    def feed(self, in_dungeon: bool, pos) -> bool:
        """Return True on the tick a real run-start movement is detected."""
        # Outside a dungeon (or no position yet) -> disarm; never start in town.
        if not in_dungeon or not pos:
            self.reset()
            return False
        if self._last is None:
            self._last = pos
            self._anchor = pos
            self._settle = 0
            return False
        step = self._dist(pos, self._last)
        self._last = pos
        # A big single-tick jump is a scene load / teleport-in, not walking:
        # throw away any tentative baseline and wait for things to settle.
        if step > self.TELEPORT_STEP:
            self._base = None
            self._anchor = pos
            self._settle = 0
            return False
        if self._base is None:
            # Trust the baseline only once the player has genuinely come to rest:
            # held within SETTLE_EPS of the streak anchor for SETTLE_TICKS. A slow
            # drift (the landing) walks out of that radius and re-anchors, so it
            # never settles until the motion truly stops.
            if self._anchor is None:
                self._anchor = pos
            if self._dist(pos, self._anchor) < self.SETTLE_EPS:
                self._settle += 1
                if self._settle >= self.SETTLE_TICKS:
                    self._base = pos
            else:
                self._anchor = pos      # drifted out -> restart the streak here
                self._settle = 0
            return False
        # Baseline established -> deliberate movement away from it starts the run.
        if self._dist(pos, self._base) >= self.MOVE_THRESHOLD:
            self.reset()
            return True
        return False


class RearmGate:
    """Decides when a FINISHED run re-arms (resets) for a back-to-back next run.

    The old rule reset the HUD as soon as the kill boss was no longer in the
    scene - but the boss DESPAWNS on death, so the finished boss+full times were
    wiped within a tick of the kill, before they could be read. Instead the
    result is held on screen and the HUD re-arms only when the player actually
    LEAVES the dungeon: the exit is a teleport (a big single-tick position jump,
    same scale AutoStarter uses) or a load screen (position unreadable for a
    couple of seconds). A long grace is the fallback - re-running a dungeon
    requires leaving it anyway, so holding the result costs nothing.
    Pure + headless, like AutoStarter."""

    TELEPORT_STEP = 50.0    # single-tick jump bigger than this = left via teleport
    NONE_TICKS = 40         # ~2s of unreadable position = load screen / main menu
    GRACE_TICKS = 600       # ~30s fallback: result stays readable while looting

    def __init__(self):
        self.reset()

    def reset(self) -> None:
        self._last = None       # previous tick position
        self._none = 0          # consecutive unreadable-position ticks
        self._ticks = 0         # ticks since the finish

    def feed(self, pos) -> bool:
        """One overlay tick while the timer is DONE; True = re-arm now."""
        self._ticks += 1
        if self._ticks >= self.GRACE_TICKS:
            return True
        if pos is None:
            self._none += 1
            return self._none >= self.NONE_TICKS
        self._none = 0
        if self._last is not None and AutoStarter._dist(pos, self._last) > self.TELEPORT_STEP:
            return True
        self._last = pos
        return False


class ZoneEdge:
    """Detects the zone-IN edge for auto-starting the full-run timer.

    The full run starts when the player crosses from a proven outside state
    into a dungeon/rift and ends when the boss dies. A first observation while
    already inside only arms the edge: launching or attaching mid-instance
    must not start RUN until the player has actually left and re-entered.
    Pure + headless.
    """

    def __init__(self):
        self.reset()

    def reset(self) -> None:
        self._prev: bool | None = None

    def prime_inside(self) -> None:
        """Suppress a start until a real exit/re-entry edge (post-result re-arm)."""
        self._prev = True

    def feed(self, in_dungeon: bool) -> bool:
        prev = self._prev
        now = bool(in_dungeon)
        self._prev = now
        return now and prev is False


class BossAttachGate:
    """Allows a standalone BOSS split from a trustworthy attach baseline.

    A reflected current/max pair is the strongest proof. When max HP is not
    exposed by the build, an explicit pre-combat state is the safe fallback:
    a readable boss HP while the player is definitely out of combat is a useful
    baseline, whereas active or unknown combat is not. The first HP seen after
    attaching is never silently treated as full on its own.
    """

    PENDING, UNKNOWN, PRISTINE, PRECOMBAT, DAMAGED, ACTIVE = (
        "pending", "unknown", "pristine", "precombat", "damaged", "active")
    FULL_HP_TOLERANCE = 1e-6

    def __init__(self):
        self.reset()

    def reset(self) -> None:
        self.state = self.PENDING
        self._combat_seen = False

    @property
    def eligible(self) -> bool:
        return self.state in (self.PRISTINE, self.PRECOMBAT)

    def feed(self, current: float | None, maximum: float | None,
             in_combat: bool | None = None) -> bool:
        """Observe one attach-time HP pair and combat state.

        Returns True only when the verdict changes. ``PRECOMBAT`` is an
        explicitly labelled fallback, not a claim that the reflected maximum
        was read; it is accepted only before any active-combat observation.
        """
        if self.state in (self.DAMAGED, self.PRISTINE, self.PRECOMBAT,
                          self.ACTIVE):
            return False
        if in_combat is True:
            self._combat_seen = True
            verdict = self.ACTIVE
        elif (current is not None and current > 0.0
              and maximum is not None and maximum > 0.0):
            if (maximum * (1.0 - self.FULL_HP_TOLERANCE)
                    <= current <= maximum * (1.0 + self.FULL_HP_TOLERANCE)):
                verdict = self.PRISTINE
            else:
                verdict = self.DAMAGED
        elif (current is not None and current > 0.0
              and in_combat is False and not self._combat_seen):
            verdict = self.PRECOMBAT
        else:
            verdict = self.UNKNOWN
        changed = verdict != self.state
        self.state = verdict
        return changed


class WipeWatcher:
    """Edge-triggered local-death detection for resetting the boss split.

    Fed the local hero's HP (None when unreadable) once per overlay tick
    while a run is live; returns True exactly once on the downward edge
    (prev > 0 -> hp <= 0). Unreadable ticks never fire and never move the
    baseline, so a load-screen read gap can't fake a death — and the corpse
    ticks after a real death can't re-fire it. Pure + headless, mirroring
    the DPS tracker's hero-HP death edge.
    """

    def __init__(self):
        self.reset()

    def reset(self) -> None:
        self._prev: float | None = None

    def feed(self, hp: float | None) -> bool:
        if hp is None:
            return False
        prev = self._prev
        self._prev = hp
        return prev is not None and prev > 0 and hp <= 0


class GroupWipeWatcher:
    """Fire only after every group member seen during a run is down.

    The boss split belongs to the group attempt: one player dying is not a
    wipe, but the full party dying should reset the split so the next pull
    arms a fresh boss time. Participants latch from the live scene rather
    than the whole account-wide roster, so a party member staying in town
    cannot keep a dungeon split alive forever. A short no-visible-members
    streak covers corpses despawning together without trusting a single
    transient scene gap as a wipe.
    """

    EMPTY_STREAK = 3

    def __init__(self):
        self.reset()

    def reset(self) -> None:
        self._participants: set[int] = set()
        self._saw_alive = False
        self._empty_ticks = 0

    def feed(self, grouped: bool, alive: set[int], present: set[int]) -> bool:
        if not grouped:
            self.reset()
            return False
        self._participants.update(present)
        if self._participants.intersection(alive):
            self._saw_alive = True
            self._empty_ticks = 0
            return False
        if not self._saw_alive:
            return False
        self._empty_ticks += 1
        if self._empty_ticks < self.EMPTY_STREAK:
            return False
        self.reset()
        return True


KILL_LOG_LIMIT = 50

# Shared cross-character history store (moddata/speedrun_history.json).
# One source of truth for every character's confirmed kills, newest last,
# capped so the PB History page never has to load an unbounded file.
SHARED_HISTORY_LIMIT = 500

# The boss split keeps its own last time and state independently of the full-run
# timer, so a finish can carry BOTH a full-run PB and a boss-run PB. When a
# finished run is a confirmed boss kill, the kill log records the boss split
# separately (that is the headline metric speedkillers care about).
# kills kept per character (newest last)


# Sanity floor for any time that is about to be STORED as a record.
#
# A fight cannot be finished in under a second, so neither a full run nor a
# boss split can. A sub-second value is never a fast kill: it is a clock that
# armed and stopped inside the same tick (or a split armed under an already
# finished run), and the 00:00 bug wrote exactly those as personal bests - a
# gold `00:00.00 NEW BEST` that then became the PB every later kill was
# compared against, so the real times all read as "behind". Such a value is
# kept out of every record; the measured time itself is still shown.
MIN_PLAUSIBLE_SPLIT_S = 1.0


def plausible_split(seconds) -> bool:
    """True when `seconds` is a duration a real fight could have produced.

    The one predicate behind every "may this be recorded" decision: the PB
    store, the kill log and the purge of already-recorded nonsense all ask
    this, so they cannot drift apart. None and non-numbers are never
    plausible (a NaN fails the comparison on its own).
    """
    if seconds is None:
        return False
    try:
        return float(seconds) >= MIN_PLAUSIBLE_SPLIT_S
    except (TypeError, ValueError):
        return False


def _plausible_ms(ms) -> bool:
    """Kill-log rows carry milliseconds, not seconds."""
    if ms is None:
        return True          # absent = this row has no such split (not junk)
    try:
        return float(ms) >= MIN_PLAUSIBLE_SPLIT_S * 1000.0
    except (TypeError, ValueError):
        return False


def clean_best_dict(best) -> tuple[dict, list[str]]:
    """`best` (boss_id -> seconds) without the sub-second entries.

    Returns ``(cleaned, dropped_ids)`` so a caller can say what it removed.
    """
    out: dict = {}
    dropped: list[str] = []
    for bid, secs in (best or {}).items():
        if plausible_split(secs):
            out[bid] = secs
        else:
            dropped.append(str(bid))
    return out, dropped


def clean_kill_rows(rows) -> tuple[list, list[dict]]:
    """Kill-log rows with the 00:00 artifacts taken out.

    A row whose FULL run is sub-second describes no fight at all, so it is
    dropped. A row whose boss split is sub-second keeps the run and loses only
    ``boss_ms`` - which is exactly the shape `kill_entry` already uses for "the
    split never finished on its own", so a downstream reader needs no new case.
    Returns ``(kept, dropped_rows)``.
    """
    kept: list = []
    dropped: list[dict] = []
    for row in (rows or []):
        if not isinstance(row, dict):
            continue
        if not _plausible_ms(row.get("full_ms")):
            dropped.append(dict(row))
            continue
        if "boss_ms" in row and not _plausible_ms(row.get("boss_ms")):
            row = {k: v for k, v in row.items() if k != "boss_ms"}
        kept.append(row)
    return kept, dropped


def kill_entry(boss_id: str, full_ms: int, mode: str | None, at: float,
               char: str | None = None, dungeon: str | None = None) -> dict:
    """One JSON-able kill-log row for a confirmed boss kill with no boss split.

    The bits-per-second row shape: the boss split did not finish independently,
    so the row has no ``boss_ms`` and must be skipped when a boss PB is needed.
    Delegates to kill_entry_with_boss — one row builder, one contract.
    ``char`` / ``dungeon`` are optional stamps used by the shared history.
    """
    return kill_entry_with_boss(boss_id, full_ms, None, mode, at,
                                char=char, dungeon=dungeon)


def kill_entry_with_boss(boss_id: str, full_ms: int,
                         boss_last: float | None, mode: str | None, at: float,
                         char: str | None = None, dungeon: str | None = None) -> dict:
    """One JSON-able kill-log row for a confirmed boss kill WITH a boss split.

    The boss-split ms is the headline metric speedkillers care about; the
    per-dungeon boss PB is read off its own timer, never the full run.
    ``char`` / ``dungeon`` are optional stamps used by the shared history.

    A sub-second ``boss_last`` is DROPPED rather than stored: the row builder
    is the one choke point every kill-log row passes through (the overlay, the
    shared store and the additive restore), so the 00:00 bug's gift - a `0`
    boss split attached to a real kill - cannot be recorded from any of them.
    Note ``boss_last`` arrives in MILLISECONDS here (as ``full_ms`` does) - the
    caller converts from the timers' seconds - so the floor is applied in ms.
    """
    row = {"boss": boss_id, "full_ms": int(full_ms),
           "mode": mode or "hard", "at": float(at)}
    if boss_last is not None and _plausible_ms(boss_last):
        row["boss_ms"] = int(float(boss_last))
    if char is not None:
        row["char"] = char
    if dungeon is not None:
        row["dungeon"] = dungeon
    return row


def append_kill_log(existing: list | None, entry: dict,
                    limit: int = KILL_LOG_LIMIT) -> list:
    """`existing` + `entry`, capped to the newest `limit` rows (pure)."""
    rows = list(existing or [])
    rows.append(dict(entry))
    return rows[-limit:]


def row_key(r: dict) -> tuple:
    """A kill-row's identity: (char, boss, at, full_ms).

    This is the integrity contract of the shared history — the config writer,
    the migration and the backup merge all dedupe on it, so it must never
    drift between files.
    """
    return (r.get("char") or "", r.get("boss") or "",
            float(r.get("at") or 0.0), int(r.get("full_ms") or 0))


def merge_history_rows(*groups, limit: int = SHARED_HISTORY_LIMIT) -> list:
    """Merge kill-log row groups into one list: deduped on row_key, sorted by
    ``at`` ascending (rows without a timestamp sort first — treated as
    oldest), capped to the newest ``limit`` rows. Pure — the ONE merge used by
    config.write_speedrun_history, the legacy migration and the backup merge.
    """
    seen: set = set()
    out: list = []
    for g in groups:
        if not g:
            continue
        for r in g:
            if not isinstance(r, dict):
                continue
            key = row_key(r)
            if key in seen:
                continue
            seen.add(key)
            out.append(dict(r))
    out.sort(key=lambda r: float(r.get("at") or 0.0))
    return out[-limit:]


class ModeLatch:
    """Latches the last confidently auto-detected difficulty *during* a run.

    The Normal↔Hard mixups Hooch saw came from resolving difficulty at the
    finish frame, the exact moment the boss dies/despawns, when its level is
    flaky or unreadable, so it fell back to the manual selector (often the wrong
    one). The boss's level is reliable while it's alive and fighting, so we
    observe it every tick of the run and remember the last good *auto* read. At
    finish we prefer that latched value over a fresh (possibly empty) read.
    """

    def __init__(self):
        self.reset()

    def reset(self) -> None:
        self.mode: str | None = None
        self.src: str | None = None

    def observe(self, mode: str | None, src: str | None) -> None:
        """Record a live reading; only confident auto reads are latched."""
        if src == "auto" and mode in ("normal", "hard", "heroic"):
            self.mode, self.src = mode, "auto"

    def resolve(self, fresh_mode: str | None, fresh_src: str | None) -> tuple[str | None, str | None]:
        """Final (mode, src): a fresh auto read wins; else the latched auto read;
        else the fresh fallback (manual selector / default)."""
        if fresh_src == "auto" and fresh_mode in ("normal", "hard", "heroic"):
            return fresh_mode, fresh_src
        if self.src == "auto":
            return self.mode, "auto"
        return fresh_mode, fresh_src


class BossTimer:
    """The boss-only split: a second stopwatch that ARMS on the first hit landed on
    the boss fight and FREEZES when the boss dies. It can run independently when
    the app attaches before the first hit, while the full RUN remains held until a
    genuine outside -> inside edge. Pure + headless.
    """
    READY, RUNNING, DONE = "ready", "running", "done"

    def __init__(self):
        self.reset()

    def reset(self) -> None:
        self.state = self.READY
        self._t0 = 0.0
        self._frozen = 0.0
        self.last: float | None = None      # last finished boss-split time (s)

    def arm(self) -> None:
        """Start the split on the first hit (no-op once armed/finished)."""
        if self.state == self.READY:
            self.state = self.RUNNING
            self._t0 = time.monotonic()

    def stop(self) -> None:
        if self.state == self.RUNNING:
            self._frozen = time.monotonic() - self._t0
            self.state = self.DONE
            self.last = self._frozen

    def elapsed(self) -> float:
        if self.state == self.RUNNING:
            return time.monotonic() - self._t0
        return self._frozen


class Encounter:
    """The boss-only split's view of a (possibly multi-unit) boss fight.

    `members` are the unit_ids that count as the fight; `kill_id` is the unit whose
    death ends the run. With `engage_any` (a trashless room - only the boss + adds)
    any enemy counts, so the split arms on the first hit to anything. Fed each tick
    with every member's live `(unit_id, present, hp)`, it latches ENGAGED on the
    first damage to a counted unit - hp dropping below the peak HP seen for that unit
    (MaxHealth has no offset, so we track the peak) - and returns the kill boss's
    tuple for the existing SpeedrunTimer.feed_boss kill detection. Pure/headless so
    the dual-timer logic is testable without the game.
    """

    def __init__(self, members, kill_id: str | None, engage_any: bool = False):
        self.members = set(members)
        self.kill_id = kill_id
        self.engage_any = engage_any
        self._peak: dict[str, float] = {}
        self.engaged = False
        self._kill_seen = False
        self._kill_present = False
        self._kill_last_hp: float | None = None
        self._kill_peak = 0.0

    def reset(self) -> None:
        self._peak.clear()
        self.engaged = False
        self._kill_seen = False
        self._kill_present = False
        self._kill_last_hp = None
        self._kill_peak = 0.0

    @property
    def kill_confirmed(self) -> bool:
        """Confirmed corpse/death, including a near-death despawn."""
        if self._kill_last_hp is not None and self._kill_last_hp <= 0:
            return True
        return bool(self._kill_seen and not self._kill_present
                    and self._kill_peak > 0
                    and self._kill_last_hp is not None
                    and self._kill_last_hp <= self._kill_peak * 0.05)

    def feed(self, states) -> tuple[str | None, bool, float | None]:
        """states: iterable of (unit_id, present, hp) for the encounter units in
        scene. Latches self.engaged on the first HP drop; returns the kill boss's
        (id, present, hp) tuple (defaults to not-present when it isn't in scene)."""
        kill = (self.kill_id, False, None)
        self._kill_present = False
        for uid, present, hp in states:
            if uid == self.kill_id:
                kill = (uid, present, hp)
                self._kill_present = present
                if present:
                    self._kill_seen = True
                    if hp is not None:
                        self._kill_last_hp = hp
                        self._kill_peak = max(self._kill_peak, hp)
            counts = self.engage_any or uid in self.members
            if counts and present and hp is not None and hp > 0:
                peak = self._peak.get(uid, 0.0)
                if hp > peak:
                    self._peak[uid] = hp
                elif peak > 0.0 and hp < peak:
                    self.engaged = True
        return kill


def should_auto_start(timer_state: str, in_dungeon: bool, engaged: bool) -> bool:
    """RETIRED (kept for the tests that still describe it): a run does NOT start
    on boss damage.

    This was the retired full-run auto-start: ready + in an instance + the fight
    engaged. The overlay no longer calls it. RUN starts only on a proven
    outside -> inside edge (see ZoneEdge); BOSS may still arm independently from
    a pristine boss baseline when the app attaches before the first hit. Pure +
    headless.
    """
    return timer_state == SpeedrunTimer.READY and bool(in_dungeon) and bool(engaged)
