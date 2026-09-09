"""Per-boss records: what you have actually achieved against each boss.

A folder of per-boss archives is the raw material; this is the reading of it.
Every archived run of a boss, grouped by boss, reduced to the handful of
numbers a player is really asking about after a run: how many times have I
killed this, what is my fastest kill, what did that kill do to me.

Two rules run through the whole module.

**A wipe is not a kill.** The headline number is the fastest CONFIRMED kill
(`CombatSession.boss_killed`, set when a boss's HP reaches zero). Without that
gate the "personal best" for every boss would be the pull somebody abandoned at
10% health, because giving up early always beats fighting to the end — a record
that reports it would be confidently wrong, which is worse than reporting
nothing. Wipes are still counted, as `attempts`, because how often you fail is
part of the same story and hiding it would just move the dishonesty.

**A record never invents a boss.** Runs whose boss cannot be named are grouped
under `UNKNOWN_BOSS` rather than lumped into a real one, so a mislabelled run
shows up as a hole in the records instead of quietly flattering a neighbour.

Pure and offline: it reads `CombatSession` lists and touches no disk, so the
same function serves the Past Fights dialog, the overlay and a test alike. The
caller supplies the sessions (and, implicitly, whose they are — a personal best
belongs to one character, so group per character before calling).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .dps_data import CombatSession

#: Runs with no readable boss name collect here rather than under a real boss.
UNKNOWN_BOSS = "Unknown Boss"


def was_killed(session: CombatSession) -> bool:
    """Whether this boss fight ended with the boss dying.

    Two records of the same fact, because the fact started being recorded at
    two different times: `boss_killed` is the explicit flag the tracker sets
    now, and ``(Boss Defeated)`` in the archive's name is where the SAME code
    path wrote the outcome before the flag existed. Either is evidence — this
    is not a guess off a string, it is reading the older of two fields the
    tracker itself wrote. Without the fallback, every run archived before the
    flag would read as a wipe forever and the records would only describe runs
    made after the upgrade.
    """
    if bool(getattr(session, "boss_killed", False)):
        return True
    return "boss defeated" in (getattr(session, "name", "") or "").lower()


def fight_failed(session: CombatSession) -> bool:
    """Whether this session is a boss fight that ended WITHOUT a kill.

    A "failed fights" filter is only meaningful for boss fights — a trash pull
    that lulls out is not a failure, and a dummy test cannot fail — so every
    other kind reads as False no matter what its name says.
    """
    if getattr(session, "kind", "") not in ("boss", "boss_adds"):
        return False
    return not was_killed(session)


def boss_of(session: CombatSession) -> str:
    """The boss a session belongs to, without the crown the board draws.

    `boss_label` is the routed filename label and is preferred; `target_name` is
    the fallback for archives written before per-boss routing existed. Both can
    carry the 👑 emblem, and an emoji in a record's name sorts nowhere and
    reads as a bug.
    """
    label = getattr(session, "boss_label", "") or ""
    if not label:
        label = (getattr(session, "target_name", "") or "").replace("\U0001f451", "")
    return label.strip() or UNKNOWN_BOSS


@dataclass
class BossRecord:
    """Everything one boss's archived runs add up to."""
    boss: str = UNKNOWN_BOSS
    #: Confirmed kills. The number the record is about.
    kills: int = 0
    #: Boss fights that did damage, killed or not.
    attempts: int = 0
    #: Fastest confirmed kill, seconds. 0.0 when there are none.
    best_time: float = 0.0
    #: Highest confirmed-kill group DPS. 0.0 when there are none.
    best_dps: float = 0.0
    #: Highest damage in any confirmed kill.
    best_damage: float = 0.0
    #: Mean confirmed-kill time, seconds.
    avg_time: float = 0.0
    #: Damage summed over confirmed kills.
    total_damage: float = 0.0
    #: When the first and most recent confirmed kill happened (wall clock).
    first_kill: float = 0.0
    last_kill: float = 0.0
    #: The run behind `best_time`, so a surface can open it.
    best_session: CombatSession | None = None
    #: Every confirmed kill, oldest first.
    kill_times: list[float] = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.attempts > 0


def boss_records(sessions) -> list[BossRecord]:
    """Reduce archived sessions to one `BossRecord` per boss.

    Ordered by kills first, then by fastest kill: a record for a boss you have
    killed once in a week matters more than one for a boss you wipe on, and
    among equals the better kill is the more interesting row to read. Bosses
    with no confirmed kill sort last but are never dropped — an all-wipe boss
    still has a record, and that record is the interesting one.
    """
    records: dict[str, BossRecord] = {}
    for s in sessions or ():
        if getattr(s, "kind", "") != "boss":
            continue                      # trash/dummy/overall are not boss runs
        if (getattr(s, "group_damage", 0.0) or 0.0) <= 0.0:
            continue                      # a fight that dealt nothing says nothing
        boss = boss_of(s)
        rec = records.get(boss)
        if rec is None:
            rec = records[boss] = BossRecord(boss=boss)
        rec.attempts += 1

        started = float(getattr(s, "start_time", 0.0) or 0.0)
        if not was_killed(s):
            continue                      # a wipe: counted, never a record
        rec.kills += 1
        duration = float(getattr(s, "duration", 0.0) or 0.0)
        if duration <= 0.0:
            # A kill with no clock is still a kill, and is counted as one — but
            # it cannot be ranked. Treating the missing clock as 0.0 s would
            # make every real run look slow by comparison, and the record would
            # be reporting a time nobody achieved.
            continue
        rec.kill_times.append(duration)
        rec.total_damage += float(s.group_damage)
        if rec.best_time <= 0.0 or duration < rec.best_time:
            rec.best_time = duration
            rec.best_session = s
        dps = float(getattr(s, "group_dps", 0.0) or 0.0)
        if dps > rec.best_dps:
            rec.best_dps = dps
        if float(s.group_damage) > rec.best_damage:
            rec.best_damage = float(s.group_damage)
        if started:
            rec.first_kill = started if not rec.first_kill else min(rec.first_kill,
                                                                   started)
            rec.last_kill = max(rec.last_kill, started)
    for rec in records.values():
        if rec.kill_times:
            rec.avg_time = sum(rec.kill_times) / len(rec.kill_times)
    return sorted(records.values(), key=_record_sort_key)


def _record_sort_key(rec: BossRecord):
    """Kills first, then fastest kill, then name — the record board's order.

    Shared by `boss_records` and `records_from_rollups` so a record built from
    an archived log and a record built from a sidecar sort identically.
    """
    return (-rec.kills,
            rec.best_time if rec.best_time > 0.0 else float("inf"),
            rec.boss)


#: Bumped when the sidecar's shape changes; a reader refuses any other value,
#: so an old sidecar simply falls back to parsing instead of being misread.
SUMMARY_VERSION = 1


def summarize(sessions) -> dict:
    """A tiny projection of one archived log's fights, for a sidecar file.

    The log itself holds everything (per-player parses, skills, timelines,
    hit streams); a list answering "how many fights, how big was the best, what
    did it take, was anything failed" needs none of that. This reduces a log to
    the handful of numbers the filename cannot carry, so the Fights list can
    draw its aggregate line and the records strip can total a season WITHOUT
    opening a single log.

    `bosses` is per-BOSS (mirroring `boss_records`: kind ``boss``, damage dealt
    only), keyed by boss name, each carrying exactly what merging records needs
    — attempts, kills, ranked kills, best/avg inputs — so a sidecar-backed
    record is the same number a parsed one would be. `fights`/`best_damage`/
    `best_duration`/`has_failure` describe the WHOLE log (every kind), which is
    what its collapsed row shows.

    `best_duration` is the duration of the best-damage fight, so the row can
    read "best X damage in T" without a second lookup. Pure: no disk, no Qt.
    """
    fights = 0
    best_damage = 0.0
    best_duration = 0.0
    has_failure = False
    bosses: dict[str, dict] = {}
    for s in sessions or ():
        damage = float(getattr(s, "group_damage", 0.0) or 0.0)
        if damage <= 0.0:
            continue                      # a fight that dealt nothing says nothing
        fights += 1
        if damage > best_damage:
            best_damage = damage
            best_duration = float(getattr(s, "duration", 0.0) or 0.0)
        if fight_failed(s):
            has_failure = True
        if getattr(s, "kind", "") != "boss":
            continue                      # trash/dummy/overall are not boss runs
        boss = boss_of(s)
        roll = bosses.get(boss)
        if roll is None:
            roll = bosses[boss] = {
                "attempts": 0, "kills": 0, "ranked_kills": 0,
                "best_time": 0.0, "kill_total_time": 0.0, "best_dps": 0.0,
                "best_damage": 0.0, "total_damage": 0.0,
                "first_kill": 0.0, "last_kill": 0.0}
        roll["attempts"] += 1
        if not was_killed(s):
            continue                      # a wipe: counted, never a record
        roll["kills"] += 1
        duration = float(getattr(s, "duration", 0.0) or 0.0)
        if duration <= 0.0:
            continue                      # a kill with no clock cannot be ranked
        roll["ranked_kills"] += 1
        roll["kill_total_time"] += duration
        roll["total_damage"] += damage
        if roll["best_time"] <= 0.0 or duration < roll["best_time"]:
            roll["best_time"] = duration
        dps = float(getattr(s, "group_dps", 0.0) or 0.0)
        if dps > roll["best_dps"]:
            roll["best_dps"] = dps
        if damage > roll["best_damage"]:
            roll["best_damage"] = damage
        started = float(getattr(s, "start_time", 0.0) or 0.0)
        if started:
            roll["first_kill"] = (started if not roll["first_kill"]
                                  else min(roll["first_kill"], started))
            roll["last_kill"] = max(roll["last_kill"], started)
    return {"version": SUMMARY_VERSION, "fights": fights,
            "best_damage": best_damage, "best_duration": best_duration,
            "has_failure": has_failure, "bosses": bosses}


def merge_rollup(dest: dict, src: dict) -> dict:
    """Fold one log's per-boss rollup into another's, in place.

    Counts and totals sum; bests take the better of the two; the first/last
    kill straddle both. This is what makes a season of per-day logs total into
    the same record a single parse of all of them would.
    """
    for key in ("attempts", "kills", "ranked_kills"):
        dest[key] = int(dest.get(key, 0) or 0) + int(src.get(key, 0) or 0)
    for key in ("kill_total_time", "total_damage"):
        dest[key] = (float(dest.get(key, 0.0) or 0.0)
                     + float(src.get(key, 0.0) or 0.0))
    for key in ("best_dps", "best_damage"):
        dest[key] = max(float(dest.get(key, 0.0) or 0.0),
                        float(src.get(key, 0.0) or 0.0))
    best_time = float(src.get("best_time", 0.0) or 0.0)
    if best_time > 0.0 and (not dest.get("best_time")
                            or best_time < float(dest["best_time"])):
        dest["best_time"] = best_time
    first = float(src.get("first_kill", 0.0) or 0.0)
    if first:
        dest["first_kill"] = (first if not dest.get("first_kill")
                              else min(float(dest["first_kill"]), first))
    last = float(src.get("last_kill", 0.0) or 0.0)
    if last:
        dest["last_kill"] = max(float(dest.get("last_kill", 0.0) or 0.0), last)
    return dest


def records_from_rollups(rollups: dict) -> list[BossRecord]:
    """`boss_records`, but from the sidecars' per-boss rollups instead of logs.

    Produces the same records (and the same order) a parse would, without a
    session object in sight — which is the whole reason the sidecar carries
    `bosses`. `avg_time` divides by RANKED kills (the kills that had a clock),
    matching `boss_records`' mean of `kill_times`.
    """
    out: list[BossRecord] = []
    for boss, roll in (rollups or {}).items():
        ranked = int(roll.get("ranked_kills", 0) or 0)
        rec = BossRecord(
            boss=boss,
            kills=int(roll.get("kills", 0) or 0),
            attempts=int(roll.get("attempts", 0) or 0),
            best_time=float(roll.get("best_time", 0.0) or 0.0),
            best_dps=float(roll.get("best_dps", 0.0) or 0.0),
            best_damage=float(roll.get("best_damage", 0.0) or 0.0),
            total_damage=float(roll.get("total_damage", 0.0) or 0.0),
            first_kill=float(roll.get("first_kill", 0.0) or 0.0),
            last_kill=float(roll.get("last_kill", 0.0) or 0.0),
        )
        rec.avg_time = (float(roll.get("kill_total_time", 0.0) or 0.0) / ranked
                        if ranked else 0.0)
        out.append(rec)
    return sorted(out, key=_record_sort_key)