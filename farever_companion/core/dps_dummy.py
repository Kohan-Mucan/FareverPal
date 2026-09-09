"""The training-dummy test: one home for the tracker's half of it.

A dummy is the game's awkward encounter and every one of its oddities is a rule
the tracker has to carry:

* It is **not a boss** (no loot table, no boss flag) yet it gets its own session
  group like one, so its damage never lands in trash.
* It is the **only fight with no natural end in the game**: nothing dies and
  nothing despawns, so its session stayed in COMBAT and its clock ran on for
  the rest of the evening, the test's DPS decaying toward zero while the player
  stood idle. The tracker therefore supplies every end: the player's
  `stop_dummy_test`, the target length running out (`DUMMY_TARGET_DEFAULT_S`,
  the countdown the HUD draws), an idle re-arm (`DUMMY_REARM_S`), and the dummy
  leaving the radius.
* A training yard is **several dummies at once and they are ONE group** (that is
  what keeps the group total right), so the board pins the nearest one, only an
  in-progress test stays pinned, and the per-target split in
  `dps_data.TargetParse` says which of them took what.

Those rules were spread across the tick, the event path and the tracker facade,
so changing one meant finding the other two. This module owns them, in the same
mixed-in shape as `DpsTrackerTick` (`DpsTracker` inherits both, and these
methods read the tracker's own state directly rather than through `self.tracker`).

What deliberately stays where it is: the *recognition* ("is this unit a training
dummy at all", and the difference between the parse bench and the prop rows
called `Dummy_Runner` / `Dummy_Support` / `Dummy_FX`) is a data-layer fact in
`data/units.py`; the per-target buckets belong to the session model in
`core/dps_data.py`; whether the player is somewhere a dummy can BE is a zone
question (`dummy_zone_ok` below — an instance is never a training yard, both for
the scan that reports `dummy_near` and for a decoded hit); `dummy_near` is
filled by the tick's own single scan pass
(filling it costs one distance per foe and finds the nearest boss in the same
walk); and the HUD surfaces are in `ui/overlays/dps/dummy_ui.py` (the Stop / Start Test
control, the per-dummy split and the vs-last-test baseline) and the standalone
window itself is `ui/overlays/dummy_overlay.py`.
"""
from __future__ import annotations

import csv
import io
import time
from dataclasses import dataclass, field

# How long a dummy test survives with no dummy inside the radius before it is
# declared over. Short enough that walking away ends the test promptly, long
# enough that one unreadable/an empty scan pass cannot end a live test.
DUMMY_GONE_GRACE_S = 3.0

# How long a RUNNING dummy test may go with no hit before the tracker concludes
# it on its own and re-arms. A dummy has no game end signal, and standing at one
# keeps the client "in combat" until the player is roughly 30 m out — so without
# this a test (and the meter's IN COMBAT) ran until the player walked out of the
# radius. The live value is the `dps_dummy_rearm` setting (`dummy_rearm_s` on
# the tracker, pushed by the 1 Hz drain); this is only the default. Same window
# the trash pull's lull uses (dps_tracker_tick._auto_pause_lull).
DUMMY_REARM_S = 5.0

# After the tracker ITSELF ends a test (Dummy Idle / Out Of Range), how long a
# hit on the same dummy re-arms nothing. The auto end fires MID-COMBAT by
# design — a rotation lull or a brief step out of the radius is all it takes —
# and the very next decoded hit used to re-open a test instantly, so heals and
# stray ticks between the end and that hit landed in NO test (live 2026-10-03:
# the split showed "Dummy #1" and "Dummy #2" from one continuous rotation).
# The field asked for the shape explicitly: "needs to be a wait 5-10s with a
# msg test ended starting new one / ready then reset it when dmg has started
# again". 7 s sits inside that range, is longer than the idle window that ended
# the test (so a lull cannot ping-pong), and is short enough to feel like a
# pause rather than a stop.
DUMMY_REENGAGE_COOLDOWN_S = 7.0

# The dummy test is the only place in the game where the same rotation can be
# measured twice against something that never moves, which is what makes a gear
# swap or a changed rotation a NUMBER instead of an opinion. So a finished test
# is kept as the reference the next one is measured against — see
# `dummy_baseline_deltas` for the comparison and `DpsTracker.reset` /
# `stop_dummy_test` for the capture.
#
# Core owns the arithmetic and the memory; the setting lives in config and is
# carried both ways by the 1 Hz drain (ui/dps_background_tick.py), the same
# courier the dummy radius uses. A test that ends with every HUD closed must
# still leave a reference behind, which is why the tracker hands it over rather
# than a surface writing it on the way out.

# A test shorter than this is a swing or two, not a measurement: too short a
# clock makes the per-skill DPS a coin flip, and a coin flip is worse than no
# baseline at all.
BASELINE_MIN_S = 3.0

# A dummy test is only comparable to another one over the SAME span of time: a
# 30 s rotation and a 3-minute one have different ramp-up, different buff
# uptime and a different number of opportunities, so their DPS figures are
# different measurements. The target length turns "hit the dummy for a bit" into
# a run with an end — the one thing the dummy fight has no natural signal for
# (see this module's header). 0 means no target at all: free-running, ended by
# the player or by walking away, the pre-existing behaviour.
DUMMY_TARGET_DEFAULT_S = 60.0

# How much incoming damage (as a share of what the test dealt) makes a run not
# a clean measurement. A dummy does not fight back, so anything that lands is
# either the rotation itself or the world walking in — and either way the run's
# DPS is not the number the player thinks it is. A SHARE rather than an
# absolute, because the two runs being compared can be different lengths. The
# default is a judgement call: 5% is a handful of hits at a 30 s rotation and
# a whole pack of adds at a 3-minute one, which is about where "that run was
# not clean" becomes true rather than fussy.
DUMMY_TAKEN_WARN_PCT = 5.0


@dataclass
class TakenShare:
    """How much of a test's own damage came back at the player.

    `pct` is taken as a percentage of DEALT, so it means the same thing at any
    test length; `dirty` is that share against the limit, and is the flag a
    surface shows. It is a warning and not a rejection: a run that was messy may
    still be the highest the player has ever hit, and silently refusing to
    record it would lose exactly the number they came to the yard for.
    """

    taken: float = 0.0
    dealt: float = 0.0
    pct: float = 0.0
    limit_pct: float = DUMMY_TAKEN_WARN_PCT
    dirty: bool = False


# --- the spreadsheet export ----------------------------------------------#
#
# One CSV per day per profile, next to the history JSON it was written with
# (`DpsTrackerHistory.append_dummy_test_csv`), because the point of a training
# yard is a NUMBER you can compare: a rotation change, a gear swap, a buff
# uptime, run after run. A HUD can show you the last two; a spreadsheet is what
# shows you the twenty runs and the trend.
#
# The grain is deliberately ONE table with a `row_kind` discriminator rather
# than three files or a second header: `test` (the run's totals and its
# timings), `target` (each dummy's share), `skill` (each player's per-skill
# damage, hits and DPS). Every row repeats the test-level columns, so a
# spreadsheet can filter or pivot on `test_start` and read any one of the three
# without joining anything.
DUMMY_CSV_COLUMNS = (
    "row_kind", "test_start", "test_end", "duration_s", "profile", "reason",
    "player", "is_me", "skill", "skill_id", "target",
    "hits", "damage", "heals", "taken", "dps", "share_pct",
)


def dummy_test_csv(session, profile: str = "", reason: str = "") -> str:
    """One finished dummy test as CSV text (header + rows).

    Pure: builds the text and touches no disk — the write belongs to
    `DpsTrackerHistory`, which owns the folder layout. Numbers are written at
    the precision the meter shows them (2 dp) so a spreadsheet total and the
    HUD total agree, and every text field goes through the `csv` module rather
    than string joins, because a skill name with a comma in it is a normal
    thing to find in this game's data and a hand-rolled join would silently
    shift every column after it.

    Rows are emitted strongest-first within each kind (the order a player reads
    them in the HUD) and the test row comes first, so a truncated file still
    says what the run was.
    """
    if session is None or getattr(session, "kind", "") != "dummy":
        return ""
    dur = max(0.0, float(getattr(session, "duration", 0.0) or 0.0))
    start = float(getattr(session, "start_time", 0.0) or 0.0)
    end = float(getattr(session, "end_time", 0.0) or 0.0)
    group = float(getattr(session, "group_damage", 0.0) or 0.0)
    if group <= 0.0:
        return ""                       # a test with no damage is not a test
    taken = float(getattr(session, "group_taken", 0.0) or 0.0)
    when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(start)) if start else ""
    when_end = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(end)) if end else ""

    def row(kind, **kw):
        base = {
            "row_kind": kind,
            "test_start": when,
            "test_end": when_end,
            "duration_s": f"{dur:.2f}",
            "profile": profile or "",
            "reason": reason or "",
            "player": "", "is_me": "", "skill": "", "skill_id": "",
            "target": "", "hits": "", "damage": "", "heals": "",
            "taken": "", "dps": "", "share_pct": "",
        }
        base.update(kw)
        return [base.get(c, "") for c in DUMMY_CSV_COLUMNS]

    rows = [row("test", hits=sum(int(p.hit_count) for p in session.players.values()),
                damage=f"{group:.2f}",
                heals=f"{float(getattr(session, 'group_heals', 0.0) or 0.0):.2f}",
                taken=f"{taken:.2f}",
                dps=f"{group / max(1.0, dur):.2f}", share_pct="100.00")]
    for tgt in session.ranked_targets():
        share = (tgt.damage / group * 100.0) if group > 0 else 0.0
        rows.append(row("target", target=tgt.name, hits=int(tgt.hits),
                        damage=f"{tgt.damage:.2f}",
                        dps=f"{tgt.damage / max(1.0, dur):.2f}",
                        share_pct=f"{share:.2f}"))
    for player in session.ranked_players():
        p_total = float(player.total_damage or 0.0)
        p_taken = float(player.total_damage_taken or 0.0)
        for skill in player.ranked_skills():
            share = (skill.damage / p_total * 100.0) if p_total > 0 else 0.0
            rows.append(row("skill", player=player.name,
                            is_me="1" if player.is_me else "0",
                            skill=skill.name, skill_id=skill.skill_id,
                            hits=int(skill.hit_count),
                            damage=f"{skill.damage:.2f}",
                            taken=f"{p_taken:.2f}",
                            dps=f"{skill.damage / max(1.0, dur):.2f}",
                            share_pct=f"{share:.2f}"))

    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(DUMMY_CSV_COLUMNS)
    writer.writerows(rows)
    return buf.getvalue()


@dataclass
class BestDelta:
    """The live test against the profile's all-time best.

    `is_best` is the headline case and has no percentage: a run that has just
    beaten the record has nothing to be measured against any more, and printing
    "+0.0%" beside the words NEW BEST would be the least useful thing the HUD
    could say. `gap_pct` is for everything else — how far short of the ceiling
    this run fell, which is the number that makes a regression obvious.
    """

    best_dps: float = 0.0
    live_dps: float = 0.0
    age_s: float = 0.0
    best_duration_s: float = 0.0
    is_best: bool = False
    best_taken_pct: float = 0.0

    @property
    def gap_pct(self) -> float | None:
        """Percent below the best, or None when there is no best to miss."""
        if self.is_best or self.best_dps <= 0.0:
            return None
        return ((self.live_dps - self.best_dps) / self.best_dps) * 100.0

    @property
    def has_best(self) -> bool:
        return self.best_dps > 0.0


def _snapshot_taken_pct(snapshot) -> float:
    """A stored test's incoming damage as a % of its own damage (0 if unknown).

    The read side of what `dummy_baseline_snapshot` records: a record saved
    before this existed, or a corrupt one, has no such key and reads as 0 —
    clean, which is the right default for something not known.
    """
    if not isinstance(snapshot, dict):
        return 0.0
    try:
        return max(0.0, float(snapshot.get("taken_pct", 0.0) or 0.0))
    except (TypeError, ValueError):
        return 0.0


@dataclass
class SkillDelta:
    """One skill's live DPS against the reference test's."""

    name: str = ""
    skill_id: str = ""
    live_dps: float = 0.0
    base_dps: float = 0.0

    @property
    def delta(self) -> float:
        return self.live_dps - self.base_dps

    @property
    def delta_pct(self) -> float | None:
        """Percent change, or None when the reference never used this skill —
        there is no honest percentage against zero, and showing +inf% would be
        noise dressed as a result."""
        if self.base_dps <= 0.0:
            return None
        return (self.delta / self.base_dps) * 100.0


@dataclass
class DummyBaseline:
    """The live test measured against the remembered one (empty = no baseline).

    `rows == []` is the "nothing to compare" signal a surface checks first: no
    reference yet, no damage landed yet, or the session on screen is not a
    dummy test at all.
    """

    live_dps: float = 0.0
    base_dps: float = 0.0
    age_s: float = 0.0
    # How much damage the REFERENCE test took, as a % of its own damage. The
    # comparison below is only as good as the run it is against, so a reference
    # that was itself messy has to be sayable (0 = an old record saved before
    # this existed, or one that was clean).
    taken_pct: float = 0.0
    rows: list[SkillDelta] = field(default_factory=list)

    @property
    def delta(self) -> float:
        return self.live_dps - self.base_dps

    @property
    def delta_pct(self) -> float | None:
        if self.base_dps <= 0.0:
            return None
        return (self.delta / self.base_dps) * 100.0


@dataclass
class DummyClock:
    """How a running test is doing against its target length.

    `started` is the honest answer to "why is the countdown not moving": the
    session's clock does not start until the first event lands on the dummy
    (CombatSession.start_time is set by the first recorded hit), so standing at
    an idle dummy leaves the whole target ahead of the player instead of burning
    it. `reached` means the target is spent — the test is over, or is about to
    be ended by the tick that noticed.
    """

    target_s: float = 0.0
    elapsed_s: float = 0.0
    started: bool = False
    ended_on_target: bool = False

    @property
    def enabled(self) -> bool:
        """Whether a target length is set at all (0 = free-running)."""
        return self.target_s > 0.0

    @property
    def remaining_s(self) -> float:
        return max(0.0, self.target_s - self.elapsed_s) if self.enabled else 0.0

    @property
    def over_s(self) -> float:
        """Seconds past the target (0 while the test is still inside it)."""
        return max(0.0, self.elapsed_s - self.target_s) if self.enabled else 0.0

    @property
    def reached(self) -> bool:
        """Whether the target has been spent.

        `ended_on_target` ORs in for a FINISHED test: a session's `pause()`
        stamps its end with the last EVENT's timestamp, not the wall clock, so
        a test the target ended can be archived a few hundredths of a second
        under the target it ran to. Without the flag the HUD would sit on
        "0:00" for a run that completed, which is the one state the countdown
        exists to rule out.
        """
        return self.enabled and self.started and (
            self.elapsed_s >= self.target_s or self.ended_on_target)

    @property
    def fraction(self) -> float:
        """0..1 progress toward the target, for a progress bar."""
        if not self.enabled:
            return 0.0
        return max(0.0, min(1.0, self.elapsed_s / self.target_s))


class DpsTrackerDummy:
    """The training-dummy test's rules for one tracker (mixed into DpsTracker).

    All state it works on lives on the tracker itself — `dummy_test_armed`,
    `in_dummy_fight`, `dummy_near`, `dummy_session` — the convention every
    mixed-in component here follows; the defaults are set in DpsTracker.__init__
    with pointers back to this module.
    """

    def dummy_zone_ok(self) -> bool:
        """Whether where the player is can be a training yard at all.

        A dummy is an OPEN-WORLD bench. The yards live in the hubs, and no
        dungeon or rift is one, so a scan that reports a dummy in there is
        either a stale open-world read or a unit that merely looks like one —
        and a test engaged off it would sit on the board (and over the Dungeon
        HUD) with no dummy to hit.

        This is the ONE answer both halves of the feature read: the scene scan
        refuses to recognize a dummy in an instance, and the event path refuses
        the same unit on a decoded hit (`_foe_raw_ids` keeps the raw id of every
        foe, dummy or not, so the raw-id route has to ask too). It reads the
        tick's own instance fact (`_instance_now`, set once per tick by
        `_watch_zone_edge`), so neither path can disagree with the other — or
        with the HUD's Rule F gate, which reads the same instance state.

        Never ticked means False, i.e. open world: this gate can only ever
        REMOVE a dummy report, never invent one, so an unattached or brand-new
        tracker behaves exactly as it did before it existed.
        """
        return not bool(getattr(self, "_instance_now", False))

    def dummy_may_engage(self) -> bool:
        """Whether a dummy may be engaged at all right now.

        ONE predicate for both engagement paths (the scene scan in the tick and
        a decoded damage/heal event) so the two can never disagree about whether
        the player wants a dummy test: `dummy_test_armed` is the meter's Stop
        Test latch, and a fight already running needs no re-engaging. The zone
        is part of the answer: an instance is not a training yard, whatever the
        latch says (see `dummy_zone_ok`).
        """
        return (bool(self.dummy_test_armed) and self.dummy_zone_ok()
                and not self.in_dummy_fight and not self.in_boss_fight)

    def dummy_in_range(self) -> bool:
        """Whether the nearest dummy is inside the test's own radius.

        ONE radius for the whole feature — engaging a dummy, the meter's
        auto-show (Rule E), the Test Dummy HUD (Rule F) and ending the test when
        the dummy is gone all read it. It mirrors the `dps_dummy_range`
        setting; the 1 Hz drain pushes the live value onto the tracker, and 0
        means no distance cap, the codebase's usual convention.

        This is why the radius is not just a display rule: a training dummy the
        scan can see from across the zone used to engage a dummy fight the
        player was nowhere near, and since a dummy fight has no end signal that
        empty board sat on top of the meter's real numbers for the rest of the
        session (live 2026-09-29 — "top dps has no damage").
        """
        return self.dummy_within(self.dummy_range)

    def dummy_owns_board(self) -> bool:
        """Whether the dummy session is what the meter should be showing.

        A test that is RUNNING owns the board — engaging one already requires a
        dummy inside the radius, so `in_dummy_fight` is itself evidence the
        player is at a dummy (see `dummy_in_range`; the sceneless event path
        engages on a hit landing on a dummy, which is the same evidence by
        another route).

        A FINISHED test keeps the board only while the dummy is still in front
        of the player (a training yard is not an instance), so the numbers are
        still on screen to read — UNLESS the test ended ITSELF (`_dummy_board_
        released`, set by the lull and the out-of-radius end): there is nothing
        for the player to read through, and the client still reports IN COMBAT
        near the dummy, so the board is handed straight back to a READY
        open-world view (live 2026-10-03, "the app does not reset"). The Test
        Dummy HUD is unaffected either way: it asks for the `"dummy"` segment
        directly and still shows the finished run.

        `session_for("auto")` is the only caller.
        """
        if self.in_dummy_fight:
            return True
        if self._dummy_board_released:
            return False
        return (self.dummy_session.group_damage > 0
                and self.dummy_in_range())

    def dummy_split_targets(self, session) -> list[TargetParse]:
        """Every dummy in the yard as a split row, damage attached when known.

        The rows come from the SCENE's cluster (`dummy_cluster`, filled by the
        same scan pass that picks the nearest), not only from the buckets the
        decoded damage happened to name. A live three-dummy yard collapsed to a
        single `🎯 Dummy` row because the hits carried no per-dummy address, so
        the split could not show which dummy was being hit at all (live
        2026-10-03). Listing the scan's dummies means all of them appear, with
        their address and live HP; each shows the per-address damage bucket when
        the events named one (`session.targets`), and 0 when they did not.

        A bucket the cluster does not cover is kept as its own row, so no damage
        is silently dropped — but the two reasons a bucket is uncovered are not
        the same, and they are treated differently:

        * an ADDRESS that left the scan, keyed by that address, is a real target
          the cluster no longer lists, so it becomes its own row;
        * an ADDRESS-LESS bucket (keyed by the label, which is what the bridge
          produces when a hit carries no target pointer) cannot belong to any
          particular dummy, so it is folded onto the row for the dummy the
          tracker already believes is the target (`session.target_addr` — the
          nearest at engagement, or the row the player clicked), and THAT ROW
          is reported as ESTIMATED — its key is in
          `dummy_split_estimated_keys`, and `dummy_split_is_estimated` answers
          whether any row is.

        The estimate is per row, not per split: the other dummies in the yard
        were measured, and a split that called all three uncertain because one
        hit arrived without a pointer threw away the measurements it did have
        (2026-10-03).

        The fold is the fallback the `[dummy] hit target <none>` diagnostic
        exists to explain: without an address the yard cannot be measured
        per-dummy, and the alternative — a fourth row that is not a dummy at
        all — makes a three-dummy yard read as four. Folding onto the yard's
        own row keeps the rows summing to `target_total` (so the split can
        never contradict the total above it) and agrees with the target bar,
        which follows the same pinned dummy. An EMPTY cluster keeps every
        bucket as its own row: there is nothing to fall back to.

        Sorted by damage, the leader first; the sort is stable, so a tie keeps
        the cluster's distance order.
        """
        from .dps_data import TargetParse   # local: avoids an import cycle
        buckets = session.targets
        rows: list[TargetParse] = []
        seen: set = set()
        for addr, name, hp, _dist in getattr(self, "dummy_cluster", None) or []:
            bucket = buckets.get(addr)
            rows.append(TargetParse(
                addr=int(addr or 0), name=name,
                damage=float(getattr(bucket, "damage", 0.0) or 0.0),
                hits=int(getattr(bucket, "hits", 0) or 0),
                hp=float(hp or 0.0),
                max_hp=float(getattr(bucket, "max_hp", 0.0) or 0.0)))
            seen.add(addr)
        # The row an address-less hit folds onto: the pinned dummy while it is
        # still in the yard, else the nearest one (the cluster's own order).
        pinned = int(getattr(session, "target_addr", 0) or 0)
        fallback = None
        if rows:
            fallback = next((r for r in rows if pinned and r.addr == pinned),
                            rows[0])
        estimated_keys: set = set()
        for key, bucket in buckets.items():
            if key in seen:
                continue
            if fallback is not None and not isinstance(key, int):
                fallback.damage += float(getattr(bucket, "damage", 0.0) or 0.0)
                fallback.hits += int(getattr(bucket, "hits", 0) or 0)
                if getattr(bucket, "max_hp", 0.0):
                    fallback.max_hp = max(fallback.max_hp,
                                          float(bucket.max_hp))
                # Keyed, not flagged: the fold lands on ONE row and the caller
                # has to be able to point at that row, not at the split.
                estimated_keys.add(fallback.key)
                continue
            rows.append(bucket)
        self._dummy_split_estimated_keys = estimated_keys
        rows.sort(key=lambda t: t.damage, reverse=True)
        return rows

    def dummy_split_is_estimated(self) -> bool:
        """Whether the last `dummy_split_targets` had to fold address-less
        damage onto a scene row — i.e. whether ANY row is an estimate.

        The per-row answer is `dummy_split_estimated_keys`; this is the coarse
        one, for a caller that only needs to know whether the split is a
        measurement at all. The Activity Log's `[dummy] hit target <none>`
        lines are the raw evidence for both.
        """
        return bool(self.dummy_split_estimated_keys())

    def dummy_split_estimated_keys(self) -> set:
        """The keys (`TargetParse.key`) of the rows whose damage is a GUESS.

        A row is here when it absorbed hits that arrived with no target
        address, so which dummy they landed on is not knowable and they were
        pinned to this one (see `dummy_split_targets`). Every OTHER row in the
        same split is a measurement: three dummies in a yard do not become
        uncertain because a fourth hit lost its pointer.
        """
        return set(getattr(self, "_dummy_split_estimated_keys", None) or ())

    # --- split diagnostics (Activity Log) ----------------------------------#
    def _dummy_diag(self, msg: str) -> None:
        """One safe Activity Log line, prefixed so a run's dummy diagnostics
        are greppable ("[dummy] ..."). No-op with no sink wired."""
        cb = getattr(self, "log_line", None)
        if not callable(cb):
            return
        try:
            cb(f"[dummy] {msg}")
        except Exception:
            pass

    def log_dummy_cluster_diag(self) -> None:
        """Activity Log the scene's dummy-cluster addresses, once per CHANGE.

        The dummy split is only as good as the decoded hits' target addresses
        (see `dummy_split_targets`): the scene scan knows every dummy in the
        yard by address, so this line is the reference a run's hit lines are
        read against - one test run then answers "can the bridge tell the
        dummies apart?". Keyed on the address SET, not distances, so ordinary
        movement does not re-log the same cluster at 10 Hz.
        """
        cluster = list(getattr(self, "dummy_cluster", None) or [])
        key = tuple(sorted(int(c[0]) for c in cluster))
        prev = getattr(self, "_dummy_diag_cluster_key", None)
        if key == prev:
            return
        self._dummy_diag_cluster_key = key
        if not cluster:
            if prev:                       # the yard emptied, not first sight
                self._dummy_diag("scene cluster: no dummies in range")
            return
        parts = " | ".join(
            f"{int(c[0]):#x} {c[1]!r} {float(c[3]):.1f}m" for c in cluster)
        self._dummy_diag(f"scene cluster ({len(cluster)}): {parts}")

    def log_dummy_hit_target(self, tgt: int, skill: str = "") -> None:
        """Activity Log the target address of ONE dummy hit, once per address.

        The one question a training-yard test run has to answer: does a decoded
        hit carry a per-dummy address at all, or do every hit read one address
        (or none) while the scene cluster lists three? A line per DISTINCT
        address keeps a 200-hit test readable, and `_log_dummy_test_summary`
        closes the run with the count. `_dummy_diag_addrs` is cleared per test.
        """
        addr = int(tgt or 0)
        seen = getattr(self, "_dummy_diag_addrs", None)
        if seen is None:
            seen = self._dummy_diag_addrs = set()
        if addr in seen:
            return
        if len(seen) >= 64:                # pathological run: never grow forever
            seen.clear()
        seen.add(addr)
        what = f"{addr:#x}" if addr else "<none>"
        sk = f" ({skill})" if skill else ""
        self._dummy_diag(f"hit target {what}{sk}")

    def _log_dummy_test_summary(self, reason: str) -> None:
        """Activity Log the finished test's hit-address spread vs the scene.

        The verdict line: a test that landed on as many distinct addresses as
        the scene cluster means the split can separate the yard; one address for
        three dummies means the bridge cannot, and the split collapses. Read
        BEFORE the session is archived, so its hits are still here.
        """
        sess = getattr(self, "dummy_session", None)
        hits = sum(int(getattr(t, "hits", 0) or 0)
                   for t in (getattr(sess, "targets", None) or {}).values())
        addrs = len(getattr(self, "_dummy_diag_addrs", None) or ())
        cluster = len(getattr(self, "dummy_cluster", None) or ())
        self._dummy_diag(
            f"test end ({reason}): {hits} hit(s) over {addrs} distinct target "
            f"address(es); scene cluster had {cluster}")

    def dummy_within(self, range_m: float | None) -> bool:
        """True when the last scene scan saw a training dummy within `range_m`
        metres of the local player.

        ONE predicate for both callers: the overlay manager's Rule E (a dummy in
        range keeps the Top DPS meter visible while "Instances Only" is on,
        because dummy tests happen in the open world) and the meter's own
        empty-state text. Two copies of this comparison is exactly how a HUD
        ends up prompting "attack the dummy" while the gate hides it.
        `None` or a non-positive range means no distance cap, the codebase's
        usual 0 = uncapped convention.
        """
        near = self.dummy_near
        if near is None:
            return False
        if not range_m or float(range_m) <= 0.0:
            return True
        try:
            return float(near[3]) <= float(range_m)
        except (IndexError, TypeError, ValueError):
            return False

    # --- the user's end signal --------------------------------------------#
    def finish_dummy_test(self, reason: str = "Dummy Idle",
                          release_board: bool = True,
                          clear_dummy: bool = False) -> None:
        """The tracker's own auto-end, public: the idle/out-of-range gate.

        A thin public wrapper so the auto-end rules inside the tick read as
        what they are — the tracker concluding a test on its own — and so the
        disarm, the cooldown clock (`_dummy_finished_at`) and the READY
        message the player is promised share one code path with every other
        way a test ends.
        """
        self.dummy_test_armed = False
        self._finish_dummy_test(reason, release_board=release_board,
                                clear_dummy=clear_dummy)

    def stop_dummy_test(self, reason: str = "Test Stopped") -> None:
        """End the training-dummy test and hold off engaging one again.

        The dummy is the only fight in the game with no end of its own: a boss
        dies or despawns and a trash pull lulls out, but a dummy just stands
        there regenerating. This is the player's own end (the lull and the
        radius supply the automatic ones) — close the live dummy session (archived like any other
        encounter, so the finished test shows up in Fight History) AND latch the
        engagement off, or the very next scene scan would start a fresh test on
        the very same dummy.

        `start_dummy_test` re-arms it, and so does `DpsTracker.reset()`: a fresh
        meter is an armed one.
        """
        self.dummy_test_armed = False
        if self.in_dummy_fight:
            self._finish_dummy_test(reason)
        # The player's own end signal: a hit on the dummy must NOT re-arm this
        # one (only Start Test does). Set AFTER the finish, which is what
        # clears it for the auto ends — every OTHER way a test ends re-arms on
        # the next hit (see `_watch_dummy_reengage`).
        self._dummy_stopped_by_hand = True


    def start_dummy_test(self) -> None:
        """Re-arm the dummy test: the next scene scan engages a dummy again.

        The mirror of :meth:`stop_dummy_test` — the meter only stops offering a
        test until the player asks for one.
        """
        self.dummy_test_armed = True

    def _watch_dummy_reengage(self, hit_t: float, hit_addr: int) -> None:
        """Re-arm a FINISHED test when the player swings at the same dummy.

        The dummy has no end signal in the game, so every way a test ends — the
        target length, walking out of the radius, Stop Test — leaves the
        engagement latched off, and the only thing that ever cleared it was the
        Start Test button. That made hitting the dummy again read as ordinary
        open-world damage: you finish a run, keep attacking, and the board shows
        the OLD numbers plus a slowly decaying rate with no new test starting
        (reported live 2026-10-02).

        So a decoded hit ON THE SAME DUMMY re-arms. Two guards keep that from
        being the thing the latch exists to prevent (a test re-opening itself
        behind the player's back):

        - an explicit Stop Test is the player's own end signal, so it keeps the
          latch until Start Test says otherwise;
        - the hit must land AFTER the run that just ended, compared against the
          first hit seen since — a test's own last hit is part of its parse, so
          it must not also be the hit that starts the next one.

        Only ever re-arms: it does not engage here, so the single engagement
        path (the event router) stays the only place a dummy test starts.
        """
        if (self.in_dummy_fight or self.in_boss_fight
                or not self.dummy_zone_ok() or not self.dummy_in_range()):
            return
        if not getattr(self, "_dummy_finished_at", 0.0):
            return                       # nothing finished here: not our business
        if not self.dummy_test_armed:
            if getattr(self, "_dummy_stopped_by_hand", False):
                return                   # Stop Test is the player's call to undo
            if not self._dummy_finished_addr or hit_addr != self._dummy_finished_addr:
                return                   # a different dummy: a different test
            # The hit has to land AFTER the run that just ended. The session's
            # own last event IS that run's final hit, so this is exactly the
            # "is this a new swing or the same one being replayed" question —
            # and it is why a test ended on a tick cannot re-open itself on
            # the hit that same tick was already carrying.
            if hit_t <= self.dummy_session.last_event_time:
                return
            # A tracker-concluded end (idle, out-of-range) holds the door for
            # a cooldown before a hit may open the next test: the end fired
            # mid-rotation, so the hits immediately after it are the tail of
            # the OLD rotation (and the heals around it belong to neither).
            # An explicit Stop Test skips this — the player ended it on
            # purpose, and their next swing means exactly what it says.
            cooldown = DUMMY_REENGAGE_COOLDOWN_S
            if (time.monotonic() - self._dummy_finished_at) < cooldown:
                return
        self.start_dummy_test()

    # --- the target length -----------------------------------------------#

    def dummy_clock(self, session=None) -> DummyClock:
        """The test's clock against its target length, for a surface to draw.

        One function so a countdown can never disagree with the end signal it
        is counting down to: the number the HUD shows is the same
        `elapsed_s` the auto-end compares. `elapsed_s` is the session's own
        duration, which only starts moving on the first hit, so the target is
        never spent by standing next to an idle dummy.
        """
        sess = session if session is not None else self.dummy_session
        try:
            target = float(getattr(self, "dummy_target_s", 0.0) or 0.0)
        except (TypeError, ValueError):
            target = 0.0
        elapsed = 0.0
        try:
            # The countdown must advance with no hits landing, so this is the
            # wall-clock span; `duration` is the rate denominator.
            elapsed = float(sess.wall_duration or 0.0)
        except (TypeError, ValueError):
            elapsed = 0.0
        ended = bool(getattr(self, "dummy_target_reached", False)) and \
            getattr(sess, "kind", "") == "dummy"
        return DummyClock(target_s=max(0.0, target), elapsed_s=max(0.0, elapsed),
                          started=elapsed > 0.0, ended_on_target=ended)

    def _dummy_target_due(self, session) -> bool:
        """Whether this test has spent its target length and must end.

        The target length is the dummy fight's end signal, and it belongs in
        the same place as the other two (walking out of the radius, Stop Test):
        here, in the tracker's own tick, so the test ends whether or not any
        window is open to show the countdown. It ends through
        `_finish_dummy_test`, so a run that hits its target is archived and
        remembered exactly like a run the player stopped by hand — which is what
        makes the next comparison an apples-to-apples one.
        """
        clock = self.dummy_clock(session)
        return clock.reached

    # --- was the run clean? ----------------------------------------------#

    def dummy_taken_share(self, session=None) -> TakenShare:
        """The live test's incoming damage as a share of its own damage.

        The flag a surface shows, and the rule behind it: a dummy does not
        fight back, so damage taken during a test is either the rotation
        (a dot, a reflect) or the world (a boss you wandered into, a mob that
        aggroed) — and in both cases the DPS on screen is not the DPS of the
        build. Flagged rather than refused; see `TakenShare`.
        """
        sess = session if session is not None else self.dummy_session
        dealt = float(getattr(sess, "group_damage", 0.0) or 0.0)
        try:
            taken = float(getattr(sess, "group_taken", 0.0) or 0.0)
        except (TypeError, ValueError):
            taken = 0.0
        try:
            limit = float(getattr(self, "dummy_taken_limit_pct",
                                 DUMMY_TAKEN_WARN_PCT))
        except (TypeError, ValueError):
            limit = DUMMY_TAKEN_WARN_PCT
        limit = max(0.0, limit)
        pct = (taken / dealt * 100.0) if dealt > 0.0 else 0.0
        return TakenShare(taken=taken, dealt=dealt, pct=pct,
                          limit_pct=limit,
                          dirty=dealt > 0.0 and taken > 0.0 and pct > limit)

    # --- the reference test -----------------------------------------------#

    @staticmethod
    def _dummy_me(session):
        """The local player's parse in a dummy session (None until they hit)."""
        from .dps_data import own_row
        return own_row(session)

    def dummy_baseline_snapshot(self, session=None) -> dict:
        """This test as the next test's reference: the local player's per-skill
        DPS, the total it came to, and when it ran.

        Empty (`{}`) when there is nothing worth remembering — no hits, or a
        clock too short to divide by. Keys are SKILL IDS, not names, because a
        name is what the game spells today and an id is what it will still be
        after the next patch renames a skill.
        """
        sess = session if session is not None else self.dummy_session
        me = self._dummy_me(sess)
        dur = sess.duration
        if me is None or me.total_damage <= 0.0 or dur < BASELINE_MIN_S:
            return {}
        return {
            "ts": time.time(),
            "duration": round(dur, 2),
            "dps": round(me.dps(dur), 2),
            # The run's own incoming damage, so a LATER comparison can say that
            # the run it is measuring against was itself messy. Old records
            # have no such key and read as 0 (clean), which is the right default:
            # nothing is known, and an unknown is not a warning.
            "taken": round(float(getattr(sess, "group_taken", 0.0) or 0.0), 1),
            "taken_pct": round(self.dummy_taken_share(sess).pct, 2),
            "skills": {s.skill_id: {"name": s.name, "dps": round(s.dps(dur), 2)}
                       for s in me.ranked_skills()},
        }

    def dummy_baseline_deltas(self, session=None) -> DummyBaseline:
        """The live test against the remembered one, per skill.

        This is the whole point of a training yard: the same rotation, twice,
        against something that does not move. Each row is one skill's live DPS
        next to the reference's, and a skill the REFERENCE did not use still
        gets a row (live vs nothing) because "I added a second rotation" is
        exactly the change a player is trying to measure.

        An empty result means there is honestly nothing to compare: no reference
        yet, no damage in the live test yet, or the session on screen is not the
        dummy test. A surface must not render a comparison it cannot make.
        """
        sess = session if session is not None else self.dummy_session
        if sess.kind != "dummy":
            return DummyBaseline()
        base = self.dummy_baseline
        me = self._dummy_me(sess)
        if not base or me is None or me.total_damage <= 0.0:
            return DummyBaseline()
        dur = sess.duration
        live = {s.skill_id: s for s in me.ranked_skills()}
        bskills = base.get("skills") or {}
        rows: list[SkillDelta] = []
        for sid, entry in bskills.items():
            entry = entry if isinstance(entry, dict) else {}
            skill = live.get(sid)
            rows.append(SkillDelta(
                name=(skill.name if skill is not None else
                      str(entry.get("name", "") or "")),
                skill_id=str(sid),
                live_dps=(skill.dps(dur) if skill is not None else 0.0),
                base_dps=float(entry.get("dps", 0.0) or 0.0)))
        for sid, skill in live.items():
            if sid not in bskills:
                rows.append(SkillDelta(name=skill.name, skill_id=str(sid),
                                       live_dps=skill.dps(dur), base_dps=0.0))
        rows = [r for r in rows if r.live_dps > 0.0 or r.base_dps > 0.0]
        # Strongest live skill first — the row order a player reads top-down —
        # and a reference-only skill falls in by its own DPS.
        rows.sort(key=lambda r: (r.live_dps, r.base_dps), reverse=True)
        try:
            age = max(0.0, time.time() - float(base.get("ts") or 0.0))
        except (TypeError, ValueError):
            age = 0.0
        try:
            base_dps = float(base.get("dps", 0.0) or 0.0)
        except (TypeError, ValueError):
            base_dps = 0.0
        return DummyBaseline(live_dps=me.dps(dur), base_dps=base_dps,
                             age_s=age, rows=rows,
                             taken_pct=_snapshot_taken_pct(base))

    # --- the personal best ------------------------------------------------#
    #
    # The last test and the best test answer different questions, and mixing
    # them up is the mistake this keeps separate: "did that change help" is
    # about the run before this one (replaced every time), while "is this gear
    # actually better" is about the whole history (only ever beaten). A
    # reference that only remembers the last run quietly lowers a player's own
    # ceiling every time they have a bad evening.

    def dummy_best_deltas(self, session=None) -> BestDelta:
        """The live test against this profile's best-ever run.

        Empty (all zeros) when there is honestly nothing to say: no best yet, no
        damage in the live test, or a session on screen that is not the dummy
        test. A surface must not draw a comparison it cannot make.
        """
        sess = session if session is not None else self.dummy_session
        if sess.kind != "dummy":
            return BestDelta()
        best = self.dummy_best
        me = self._dummy_me(sess)
        if not best or me is None or me.total_damage <= 0.0:
            return BestDelta()
        dur = sess.duration
        live = me.dps(dur)
        try:
            best_dps = float(best.get("dps", 0.0) or 0.0)
        except (TypeError, ValueError):
            best_dps = 0.0
        try:
            age = max(0.0, time.time() - float(best.get("ts") or 0.0))
        except (TypeError, ValueError):
            age = 0.0
        try:
            best_dur = float(best.get("duration", 0.0) or 0.0)
        except (TypeError, ValueError):
            best_dur = 0.0
        return BestDelta(best_dps=best_dps, live_dps=live, age_s=age,
                         best_duration_s=best_dur,
                         best_taken_pct=_snapshot_taken_pct(best),
                         # Compared at the record's own precision: the stored
                         # DPS is rounded to 2 dp by `dummy_baseline_snapshot`,
                         # so an exact-float comparison would announce NEW BEST
                         # for a re-run of the very test that set the record —
                         # a few hundredths of a second of clock drift is not an
                         # improvement, and calling it one teaches the player
                         # to distrust the number.
                         is_best=best_dps > 0.0 and round(live, 2) > best_dps)

    def _is_dummy_best(self, snapshot: dict) -> bool:
        """Whether `snapshot` beats the remembered best.

        A STRICT improvement, for the same reason the damage-leader pin is
        strict: a tie must not rewrite the record. Two 30 s runs landing within
        a rounding error of each other are the same result, and a "best" that
        flickers between two equal runs (and resets the age to zero each time)
        is a record nobody can read.
        """
        if not snapshot:
            return False
        try:
            dps = float(snapshot.get("dps", 0.0) or 0.0)
        except (TypeError, ValueError):
            return False
        if dps <= 0.0 or not snapshot.get("skills"):
            return False
        best = self.dummy_best
        if not best:
            return True
        try:
            best_dps = float(best.get("dps", 0.0) or 0.0)
        except (TypeError, ValueError):
            return True
        return dps > best_dps

    def set_dummy_best(self, snapshot) -> None:
        """Adopt a stored personal best (the drain's job; see `set_dummy_baseline`
        for the same once-only contract). A snapshot without skills is treated
        as no best, so a corrupt file leaves the feature off instead of raising
        on every tick."""
        self.dummy_best = (snapshot
                           if isinstance(snapshot, dict) and snapshot.get("skills")
                           else None)

    def take_dummy_best_pending(self) -> dict | None:
        """Hand a new record over for the UI to persist — once (see
        `take_dummy_baseline_pending`)."""
        pending, self.dummy_best_pending = self.dummy_best_pending, None
        return pending

    def set_dummy_baseline(self, snapshot) -> None:
        """Adopt a stored reference test. Called once per session by the drain.

        A snapshot without skills is treated as "no baseline": a corrupt or
        half-written file must leave the feature off, not raise on every tick.
        """
        self.dummy_baseline = (snapshot
                               if isinstance(snapshot, dict) and snapshot.get("skills")
                               else None)

    def take_dummy_baseline_pending(self) -> dict | None:
        """Hand a just-finished test over for the UI to persist — once.

        The tracker cannot write settings (core has no config), so the finished
        test waits here until the drain picks it up. Popping it here is what
        makes the hand-off exactly-once: a crash between capture and write loses
        one reference, a repeated drain cannot write the same one twice.
        """
        pending, self.dummy_baseline_pending = self.dummy_baseline_pending, None
        return pending

    def capture_dummy_reference(self) -> dict:
        """Snapshot the live test and queue it as the next reference — and as
        the new personal best, if it beat one.

        One entry point for both routes that capture a finished test. Stop Test,
        the target length and walking out of the radius all go through
        `_finish_dummy_test`; Reset is the odd one out, because it captures a
        test it never ended. Duplicating the best check in the tracker's reset
        would be exactly the kind of two-copies-of-one-rule that lets the two
        paths drift.
        """
        snapshot = self.dummy_baseline_snapshot()
        if not snapshot:
            return {}
        self.dummy_baseline_pending = snapshot
        if self._is_dummy_best(snapshot):
            # Only a strict improvement replaces the record, and the snapshot
            # is kept whole: the best's per-skill split is what a future run
            # is compared against skill by skill, not just in total.
            self.dummy_best = snapshot
            self.dummy_best_pending = snapshot
        return snapshot

    def _finish_dummy_test(self, reason: str, release_board: bool = False,
                           clear_dummy: bool = True) -> None:
        """End the test AND remember it as the next test's reference.

        Every path that concludes a dummy test comes through here — Stop Test,
        the target length, the lull, and walking out of the radius — so the
        reference cannot be captured by one route and missed by the other. The
        snapshot is taken before the session is archived, and only for a test
        long enough to be one.

        ``release_board`` is set by the ends the TRACKER concludes on its own
        (the lull and the out-of-radius end): those hand the live board back to
        a READY open-world view instead of holding the finished run (see
        `_release_dummy_board`). Stop Test and the target length are left to
        hold the numbers, as they always did — but every end suppresses the
        client's stale IN COMBAT (see `suppress_game_combat`), because a dummy
        keeps the flag set wherever it is.

        Releasing the board is about the OPEN-WORLD aggregate only. It never
        touched the dummy session, and must not: the finished run staying on the
        Test Dummy HUD is what the player reads after a test ends. Resetting it
        too (as this once did) left the window blank for as long as they stood
        there — three minutes out of combat, nothing swinging, an empty board
        and no sign the run had been measured at all (live 2026-10-03: "the
        auto restart did not work"). The next hit re-arms and opens a fresh
        session anyway, so nothing stale can survive a new test.
        """
        # Say the end out loud, and how a new one begins. The auto ends fire
        # mid-rotation, so "what happened to my test" needs an answer in the
        # Activity Log at the moment it happens — and the re-engage cooldown
        # below only makes sense if the player was told it is running.
        auto_end = reason in ("Dummy Idle", "Dummy Out Of Range")
        if auto_end:
            self._dummy_diag(
                f"test ended ({reason.lower()}) — READY: the next damage "
                f"after {DUMMY_REENGAGE_COOLDOWN_S:.0f}s starts a new test")
        # The split diagnostic closes here, while the hits are still on the
        # session (before the archive / reset below).
        self._log_dummy_test_summary(reason)
        # Which dummy this finished test belongs to, and that it was not a
        # hand Stop — both read by `_watch_dummy_reengage` when the player
        # swings at the same dummy again.
        self._dummy_finished_addr = int(getattr(self.dummy_session, "target_addr", 0) or 0)
        self._dummy_finished_at = time.monotonic()
        self._dummy_stopped_by_hand = False
        # A FREE test is not a result: it is not archived (see
        # `_end_dummy_fight`), and it must not become the personal best or the
        # reference the next run is measured against either - otherwise a lucky
        # practice run silently becomes the number every later test is judged
        # by, which is worse than recording it.
        snapshot = (None if self.dummy_free
                    else self.capture_dummy_reference())
        if self.dummy_clock().reached:
            # Latched, because the finished session's own duration is stamped
            # from its last event and can sit a hair under the target that ended
            # it (see DummyClock.reached) — a HUD that then reads "0:00" on a
            # completed run is telling the player the test did not finish.
            self.dummy_target_reached = True
        self._end_dummy_fight(reason)
        # A dummy test's own end is the honest combat boundary, whatever
        # concluded it: the client keeps isInCombat set anywhere inside a
        # training yard, so the badge and the armed clock are held down until
        # the game catches up or a real swing lands (see
        # DpsTracker.suppress_game_combat).
        self.suppress_game_combat()
        if release_board:
            self._release_dummy_board(clear_dummy=clear_dummy)
        if snapshot:
            self.dummy_baseline_pending = snapshot

    def _release_dummy_board(self, clear_dummy: bool = True) -> None:
        """Clear the open-world run a self-concluded dummy test leaves behind.

        Dummy damage also lands on the overall session (the aggregate records
        every hit), and the client keeps the player "in combat" anywhere inside
        a training yard — so after a test the auto board showed the finished
        run's aggregate, reading IN COMBAT, until the player walked ~30 m out
        (live 2026-10-03: "the game/app does not reset since combat never ends
        if you are close to it"). Ending the test hands the board back: the
        open-world sessions reset to READY.

        `clear_dummy` is the one thing that is NOT the same for every end. The
        out-of-radius end resets the dummy session too — the player walked away
        and the Test Dummy HUD should go back to its out-of-range hint. The
        idle end must NOT: they are still standing at the dummy, and blanking
        the window left them looking at nothing for as long as they waited,
        three minutes out of combat with no sign the run had been measured at
        all (live 2026-10-03: "the auto restart did not work"). The next hit
        re-arms and opens a fresh session, so nothing stale survives a new test.

        Never touches `dummy_session` otherwise, and skips a live boss fight.
        """
        if self.in_boss_fight:
            return
        self._dummy_board_released = True
        self.overall_session.reset()
        self.trash_session.reset()
        if clear_dummy:
            self.dummy_session.reset()

    # --- engagement, off the scene tick -----------------------------------#

    def _begin_dummy_test(self, name: str, addr: int = 0,
                          hp: float = 0.0) -> None:
        """Open a fresh dummy test. The ONE place a session is started.

        Three callers can open one: the scene scan (`_engage_dummy`) and the
        damage and heal routes in `dps_tracker_events`, which can see a dummy
        hit before the next scan lands. They used to repeat this bookkeeping
        each, and two of the three forgot `dummy_target_reached` — so a test
        that had ended on its target length left the latch True, the pet's next
        hit re-engaged through the event path, and `_update_dummy` ended the
        brand-new session on the same tick reading that stale latch. The result
        was an endless chain of zero-length "Target Time Reached" runs
        (live 2026-10-03: 25 archived in 8 seconds, every one 0.0s).

        `DummyClock.reached` deliberately ORs in the latch so a FINISHED test
        can still show a completed countdown; that only stays honest if every
        fresh test clears it, which is why the reset lives here and nowhere
        else.
        """
        self.in_dummy_fight = True
        self._dummy_board_released = False        # a fresh test owns the board
        self.dummy_target_reached = False        # a fresh test starts over
        self._dummy_diag_addrs = set()           # fresh hit-address set (diag)
        self.dummy_session.reset()
        self.dummy_session.target_addr = int(addr or 0)
        self.dummy_session.target_name = name
        self.dummy_session.target_hp = float(hp or 0.0)
        self.dummy_session.target_max_hp = float(hp or 0.0)
        self.reset_foe_max_hp()

    def _engage_dummy(self, tick) -> None:
        """Start a training-dummy fight (its own group, like a boss)."""
        # Standing next to a dummy keeps it in `dummy_near` (so the meter stays
        # visible and can offer Start Test again), but a stopped test must not
        # re-open itself — hence `dummy_may_engage` rather than a bare presence
        # check.
        if (not tick.dummy_present or not self.dummy_in_range()
                or not self.dummy_may_engage()):
            return
        # Pin the NEAREST dummy — the one the player is standing at. The scan's
        # `dummy_present` is whichever dummy it met first, which in a cluster of
        # three is arbitrary, and the pinned target is what the board's name and
        # bar follow for the whole test.
        near = self.dummy_near
        if near is not None:
            d_addr, d_name, d_hp = near[0], near[1], near[2]
        else:
            d_addr, d_name, d_hp = tick.dummy_present
        if self.trash_session.group_damage > 0:
            if self.trash_session.state == "COMBAT":
                self.trash_session.pause()
            self.archive_current_encounter("Trash Mobs Cleared")

        self._begin_dummy_test(d_name, d_addr, d_hp)
        for h_addr, (h_name, is_me, *_) in tick.heroes.items():
            if is_me:
                self.dummy_session.register_player(h_name, is_me=is_me)

    def _track_dummy_target(self, tick) -> None:
        """Keep the dummy fight pointed at a live dummy and its live HP.

        "Which dummy" is the rule above; "its live HP" is the pinned dummy's,
        read from the scan every pass so the top bar tracks the target it
        actually names. The split rows carry every other dummy's health.
        """
        if not (self.in_dummy_fight and not self.in_boss_fight):
            return
        # The radius end: the dummy leaves the scan, or the player walks out of
        # the radius. Ended here after a short grace, or the fight outlived the
        # dummy — the tracker kept telling capture "combat" and the board kept
        # the finished test pinned over whatever the player did next.
        if not self.dummy_in_range():
            if self._dummy_missing_at <= 0.0:
                self._dummy_missing_at = time.monotonic()
            grace = getattr(self, "_dummy_gone_grace_s", DUMMY_GONE_GRACE_S)
            if time.monotonic() - self._dummy_missing_at >= grace:
                self._dummy_missing_at = 0.0
                self.finish_dummy_test("Dummy Out Of Range",
                                       release_board=True,
                                       clear_dummy=True)
                return
        else:
            self._dummy_missing_at = 0.0
        # The end the game never gives a dummy: a run that stops landing hits
        # concludes after a short idle (the `dps_dummy_rearm` setting). Standing
        # at a dummy keeps the client flagged in combat until the player is
        # ~30 m out, so without this the test (and the meter's IN COMBAT) ran
        # until then (live 2026-10-03). Held off like the target-length end so
        # the next scan cannot wipe the finished run; the next swing re-arms it
        # (`_watch_dummy_reengage`). 0 disables the auto end.
        #
        # `clear_dummy=False`: the player is still AT the dummy, so the open-
        # world board is released but the Test Dummy HUD keeps the finished run
        # on screen. Clearing it here is what left the window blank for the
        # whole idle stretch — the run was archived and visible nowhere
        # (live 2026-10-03: "the auto restart did not work").
        last = self.dummy_session.last_event_time
        now = getattr(tick, "now", 0.0) or time.time()
        rearm_s = getattr(self, "dummy_rearm_s", DUMMY_REARM_S)
        if rearm_s > 0 and last and (now - last) >= rearm_s:
            self.finish_dummy_test("Dummy Idle", release_board=True,
                                   clear_dummy=False)
            return
        # The other end signal, and the one a measured test actually wants: a
        # target length. Checked BEFORE the pin work so a test that runs out of
        # time on this pass archives here rather than after a frame of
        # bookkeeping on a session that is already over.
        if self._dummy_target_due(self.dummy_session):
            # Hold off engaging again, exactly as `stop_dummy_test` does: the
            # next scene scan is 100 ms away and would open a fresh test on the
            # very same dummy, wiping the finished run's numbers off the board
            # before they could be read (and the player pressed nothing). Start
            # Test / Reset is what asks for the next run.
            self.dummy_test_armed = False
            self._finish_dummy_test("Target Time Reached")
            return
        foes = tick.current_foes
        sess = self.dummy_session
        pinned = sess.target_addr
        foe = foes.get(pinned)
        near = self.dummy_near
        # ONE pin rule, in this order (see CombatSession.damage_leader /
        # select_target for the data half):
        #   1. a row the player CLICKED in the split outranks everything, until
        #      they click it again or the test ends;
        #   2. otherwise the header follows the DAMAGE LEADER — the dummy that
        #      has actually taken the most, which is the only answer that
        #      matters when the point is to compare two dummies. A leader only
        #      takes the pin on a STRICT improvement, so an AoE that credits
        #      the whole cluster equally cannot walk the bar around the yard;
        #   3. before any damage has landed, or when the pinned dummy left the
        #      scan, the NEAREST one — so walking from dummy to dummy before
        #      starting still follows the player.
        # Never "whichever the scan iterates first": that is what made a cluster
        # of three hop between its members several times a second.
        if not sess.target_locked:
            leader = sess.damage_leader()
            if leader is not None:
                sess.pin_bucket(leader)
            elif near is not None and (foe is None
                                       or (near[0] != pinned
                                           and sess.group_damage <= 0)):
                sess.target_addr = near[0]
        pinned = sess.target_addr
        foe = foes.get(pinned)
        if foe is not None:
            self.dummy_session.target_hp = foe[0]
            self.dummy_session.target_max_hp = max(self.dummy_session.target_max_hp, foe[0])
            if foe[1]:
                self.dummy_session.target_name = foe[1]

        # Per-dummy HP for the split rows: the scan is the only thing that knows
        # every dummy's health, not just the pinned one's.
        for tgt in self.dummy_session.targets.values():
            f = foes.get(tgt.addr) if tgt.addr else None
            if f is not None:
                tgt.hp = max(0.0, f[0])
                tgt.max_hp = max(tgt.max_hp, f[0])

    def select_dummy_target(self, key: int | str) -> bool:
        """Pin the dummy test's header target to split row `key`, or release it.

        This is the click a player makes in the "SPLIT BY TARGET" section: the
        row they pick becomes the board's target — its name, its health bar, the
        HP the session tracks — and it stays there while they go on hitting a
        different dummy, which is the entire point of a training yard. Clicking
        the already-pinned row hands the pin back to the automatic rule (the
        damage leader).

        Returns True when something changed, so a surface knows to re-render.
        No-ops unless a dummy test has actually split: with a single dummy there
        is no choice to make and the one target bar is already the answer.
        """
        return self.dummy_session.select_target(key)
