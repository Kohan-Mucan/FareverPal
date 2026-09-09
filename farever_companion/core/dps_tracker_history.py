"""Fight-history persistence for the shared DPS tracker.

This module owns archive selection, profile/day file layout, migration, and
loading. It deliberately operates on a tracker-like object so the public
DpsTracker API and existing session model remain unchanged.
"""
from __future__ import annotations

import copy
import json
import re
import time
from collections import deque
from pathlib import Path

from ..runtime.persist import atomic_write_json, atomic_write_text
from .dps_data import (
    KNOWN_HERO_CLASSES,
    CombatSession,
    PlayerParse,
    SkillParse,
    _session_from_dict,
    _session_to_dict,
    _slug_profile,
    read_history_file,
)
from .dps_dummy import DUMMY_CSV_COLUMNS, dummy_test_csv
from ..data import dungeons as dungeons_data
from ..data import units as udata
from .dps_records import fight_failed, summarize
from . import game_state

# The dummy sheet shares the FIGHT file's stem and differs only by extension:
# `dummy_Mage.json` (the archived test) sits beside `dummy_Mage.csv` (the day's
# runs, as rows). The fight analysis only ever opens `*.json` and only accepts
# the kind stems in HISTORY_KIND_STEMS, so the sheet is invisible to it by
# construction — a spreadsheet is not a fight, and adding a kind for it would
# invite exactly that confusion. One stem, one vocabulary, two extensions.
DUMMY_CSV_STEM = "dummy"
# An earlier build (never released) wrote the sheet as `dummy_tests_<profile>.csv`.
# It is still recognised so the pruner never mistakes a player's own sheet for
# another character's, and it is renamed on the next load so a day of runs is
# not abandoned in a file nothing appends to any more.
_DUMMY_CSV_LEGACY = "dummy_tests"

HISTORY_KIND_STEMS = {
    "boss": "boss",
    "boss_adds": "boss",
    "trash": "trash",
    "dummy": "dummy",
    "overall": "overall",
}
HISTORY_KIND_TOKENS = frozenset(HISTORY_KIND_STEMS.values())

# The log folder is <YYYY-MM>/<DD>/ (see DpsTrackerHistory.day_dir). These
# patterns are the contract between the writer and every reader — the tracker's
# own load, the UI's folder scan, and the age prune — so they live here, once,
# next to the layout they describe rather than being re-spelled in each.
MONTH_DIR_RE = re.compile(r"^\d{4}-\d{2}$")
DAY_DIR_RE = re.compile(r"^\d{2}$")
# The pre-month-split day folder (`<YYYY-MM-DD>/`), still on disk on installs
# that upgraded mid-season. A delete prunes an emptied day folder whether it is
# the current `<YYYY-MM>/<DD>/` shape or that old one, and the UI scanner reads
# it from here (`history_index`) rather than spelling its own copy.
OLD_DAY_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _sheet_append(path: Path, text: str) -> Path | None:
    """Append CSV `text` to `path`, writing the header only on the first write.

    Shared by the per-test append and the pre-release file's migration, so both
    go through the same rules: the header rides the first write (a sheet with
    one header per test is a broken sheet), a trailing newline is repaired, and
    a file whose first line is not our header is refused rather than having two
    schemas glued together. Returns the path, or None when it refused.
    """
    try:
        existing = path.read_text(encoding="utf-8-sig") if path.exists() else ""
        if existing.strip() and not existing.lstrip().startswith(
                DUMMY_CSV_COLUMNS[0]):
            return None
        if existing and not existing.endswith("\n"):
            existing += "\n"
        block = text if not existing else text.split("\n", 1)[1]
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, existing + block, encoding="utf-8-sig")
        return path
    except OSError:
        return None


def _rmdir_if_empty(path: Path) -> None:
    try:
        if path.is_dir() and not any(path.iterdir()):
            path.rmdir()
    except OSError:
        pass


def prune_history_older_than(directory: str | Path, days: float,
                             now: float | None = None) -> dict:
    """Delete every day folder under `directory` older than `days`. Returns a
    summary of what went.

    A deliberate, user-triggered cleanup of the whole log dir — not the
    per-profile prune that runs on every archive (that one only drops another
    character's files, so a folder never holds two heroes' data). Whole day
    folders go, and a month folder that is left empty goes with them, so the
    dir shrinks back to the shape the writer expects rather than filling with
    husks.

    `days <= 0` removes nothing and says so: "keep everything" is a real
    setting, and a button that quietly deleted a year of logs because the
    number was zero would be the worst possible reading of it.
    """
    out = {"folders": 0, "files": 0, "cutoff": ""}
    try:
        days = float(days)
    except (TypeError, ValueError):
        return out
    if days <= 0:
        return out
    base = Path(directory)
    cutoff = (now if now is not None else time.time()) - days * 86400.0
    out["cutoff"] = time.strftime("%Y-%m-%d", time.localtime(cutoff))
    try:
        months = sorted(base.iterdir())
    except OSError:
        return out
    for month in months:
        if not month.is_dir() or not MONTH_DIR_RE.match(month.name):
            continue
        try:
            days_in = sorted(month.iterdir())
        except OSError:
            continue
        for day in days_in:
            if not day.is_dir() or not DAY_DIR_RE.match(day.name):
                continue
            try:
                stamp = time.mktime((int(month.name[:4]), int(month.name[5:7]),
                                     int(day.name), 0, 0, 0, 0, 1, -1))
            except (ValueError, OverflowError):
                continue
            if stamp >= cutoff:
                continue                       # still inside the window
            files = 0
            try:
                for path in day.iterdir():
                    if path.is_file():
                        files += 1
                        path.unlink()
                day.rmdir()
            except OSError:
                continue
            out["folders"] += 1
            out["files"] += files
        _rmdir_if_empty(month)
    return out


def history_kind_stem(kind: str | None) -> str:
    return HISTORY_KIND_STEMS.get((kind or "overall").lower(), "overall")


def summary_path_for(path: Path) -> Path:
    """The advisory sidecar beside a log: ``<log>.json.summary``.

    Appended, not substituted: a boss label may contain a dot and `with_suffix`
    would then rewrite the label instead of the extension. The `.summary`
    extension also keeps the sidecar out of every ``*.json`` glob — the reader,
    the pruner and the tracker's own loader all look for JSON logs, and a
    sidecar is not one, so it can never be mistaken for a fight file.
    """
    return Path(str(path) + ".summary")


def _prune_empty_day_dirs(path: Path) -> None:
    """Drop the day folder holding `path` — and its month, once both are bare.

    Guarded to day-shaped folder names so a stray call can never rmdir an
    arbitrary parent (the moddata root, a user's own folder).
    """
    day = path.parent
    month = day.parent
    if DAY_DIR_RE.match(day.name) or OLD_DAY_DIR_RE.match(day.name):
        _rmdir_if_empty(day)
        if MONTH_DIR_RE.match(month.name):
            _rmdir_if_empty(month)


def remove_log_file(path: str | Path) -> bool:
    """Delete one archived log, its sidecar and any folder it leaves empty.

    A log whose LAST fight was just deleted is removed outright rather than
    rewritten to `{"history": []}`. The stub is still valid JSON, so every
    scanner keeps listing it — an empty row in the Fights list and a husk on
    disk the player then has to delete by hand (live 2026-10-05: "it renders
    empty folders and the files are still on the pc"). The advisory sidecar
    (`<log>.json.summary`) goes with it so no orphan summary outlives its log,
    and an emptied day folder (then month folder) is pruned so the archive keeps
    the shape the writer makes instead of filling with empty folders.

    Returns True when the log is gone (or already was), False when it is still
    there — a locked file must never be reported as deleted.
    """
    p = Path(path)
    gone = True
    try:
        p.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        gone = False
    try:
        summary_path_for(p).unlink()
    except OSError:
        pass
    if gone:
        _prune_empty_day_dirs(p)
    return gone


def boss_file_label(boss_name: str) -> str:
    """A boss name reduced to something safe to put in a filename.

    Names come from the game's own unit table, so they are data, not input:
    strip the crown the meter puts on the label, drop the characters Windows
    refuses in a filename, and collapse whitespace. Never returns empty — a
    fight whose boss could not be named still needs somewhere to land.
    """
    s = (boss_name or "").replace("\U0001f451", "").replace("\U0001f3af", "")
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', " ", s)
    s = re.sub(r"\s+", " ", s).strip(" .")
    return s or "Unknown Boss"


#: The difficulty token a log's NAME carries, upper-cased. Kept here beside
#: the naming it belongs to so the writer, the pruner and the UI's index all
#: agree on what a trailing mode segment looks like.
MODE_TOKENS = frozenset({"NORMAL", "HARD", "HEROIC"})

#: The outcome token a per-boss file's NAME carries, upper-cased. One kill in
#: the file upgrades it to PASS — see `file_for`.
OUTCOME_TOKENS = frozenset({"PASS", "FAILED"})


def mode_suffix(mode: str | None) -> str:
    """The ``_HARD`` tail a log's name gets, or "" when the mode is unknown.

    Written ONLY for a mode the game itself reported (see
    `game_state.instance_mode`): the token outlives the session, so a guess
    here would be a permanent claim about a run nobody can re-read.
    """
    m = (mode or "").strip().upper()
    return f"_{m}" if m in MODE_TOKENS else ""


def strip_mode_suffix(slug: str) -> str:
    """A file name (or slug) with a trailing difficulty token removed.

    The pruner reads a file's profile slug out of its name, and a mode token
    sits after it (``<kind>_<slug>_HARD.json``); comparing the raw tail would
    read every named log as another character's file and sweep it away.
    """
    if slug in MODE_TOKENS:
        return ""                             # `boss_HARD.json` — no profile
    head, sep, tail = slug.rpartition("_")
    return head if sep and tail in MODE_TOKENS else slug


def is_boss_file(path: Path) -> bool:
    """Whether `path` is one of the per-boss files (see `boss_file_label`).

    The day-wide files are `<kind>_<slug>.json`; these are named after the
    boss instead, so the two are told apart by whether the stem starts with a
    known kind stem.
    """
    kind, _, _rest = path.stem.partition("_")
    return kind not in HISTORY_KIND_TOKENS


#: The class words `file_for` may append to a per-boss name when the profile
#: slug does not already contain one (the hero's own read class).
_HERO_CLASS_TOKENS = frozenset(KNOWN_HERO_CLASSES)


def boss_file_slug(stem: str) -> str:
    """The profile slug a per-boss file NAME carries ('' when it carries none).

    The writer names a per-boss file
    ``<Boss>_<slug>[_<class>]_<PASS|FAILED>[_MODE]``, so the slug is everything
    after the boss name's FIRST underscore — the same boundary `is_boss_file`,
    the UI's `_file_descriptor` and `_char_label_from_history_path` all read —
    with the trailing outcome and difficulty tokens removed.

    The pruner used to take the LAST underscore segment alone, which for a real
    slug (`Kartik_Warrior_a1b2`) is the hash `a1b2`, or the outcome token
    `PASS`. It therefore never equalled the active profile, so another
    character's per-boss logs were never swept and a later restore kept listing
    a stranger's kills.
    """
    body = strip_mode_suffix(stem)
    head, sep, tail = body.rpartition("_")
    if sep and tail in OUTCOME_TOKENS:
        body = head
    if "_" not in body:
        return ""                             # `<Boss>` / `<Boss>_PASS`
    slug = body.partition("_")[2]
    # A bare trailing class word is the writer's addition, not a profile.
    return "" if slug in _HERO_CLASS_TOKENS else slug


def boss_file_is_mine(stem: str, slug: str) -> bool:
    """Whether the per-boss file named `stem` belongs to profile `slug`.

    Matched on the FULL slug, never on one underscore segment. It also allows
    the class word `file_for` appends when the slug lacks one (``Kartik_a1b2``
    -> ``Kartik_a1b2_Warrior``) — but only when dropping that word yields
    EXACTLY `slug`, so a slug that legitimately ends in a class word
    (``Bob_Warrior``) is never over-stripped into a stranger's name. The one
    ambiguity left — ``Bob`` + class Warrior versus a character actually slugged
    ``Bob_Warrior`` — is resolved toward KEEPING, so a file that might be ours is
    never deleted.
    """
    file_slug = boss_file_slug(stem)
    if not slug:
        return not file_slug                 # the unprofiled logs are ours
    if file_slug == slug:
        return True
    head, sep, tail = file_slug.rpartition("_")
    return bool(sep) and tail in _HERO_CLASS_TOKENS and head == slug


def _absorb_boss_adds(boss: CombatSession, adds: CombatSession) -> None:
    """Fold the adds segment into the boss's archived session, in place.

    One boss kill is one fight, so the archive is one entry: the
    players' damage on the boss and on its adds sum into one parse,
    one timeline and one hit stream. The boss session already carries
    the fight's per-TARGET split (`note_target` buckets every in-fight
    hit onto the boss session), so `targets` is deliberately NOT
    merged — the archived board keeps the boss-versus-adds split the
    rows already sum, and the adds session's own `targets` is empty.
    """
    for name, add_parse in adds.players.items():
        dest = boss.players.get(name)
        if dest is None:
            boss.players[name] = copy.deepcopy(add_parse)
        else:
            _absorb_player_parse(dest, add_parse)
    _absorb_seconds(boss.timeline, adds.timeline)
    for player, buckets in adds.player_timeline.items():
        _absorb_seconds(
            boss.player_timeline.setdefault(player, {}), buckets)
    boss.deaths.extend(adds.deaths)
    merged = list(boss.hits) + list(adds.hits)
    merged.sort(key=lambda h: h.timestamp)
    boss.hits = deque(merged, maxlen=boss.hits.maxlen or 60)
    if adds.burst_peak > boss.burst_peak:
        boss.burst_peak = adds.burst_peak
        boss.burst_peak_time = adds.burst_peak_time
    boss.last_event_time = max(boss.last_event_time, adds.last_event_time)
    if boss.end_time and adds.end_time:
        boss.end_time = max(boss.end_time, adds.end_time)


def _absorb_player_parse(dest: PlayerParse, src: PlayerParse) -> None:
    """Sum one player's add-segment parse into the boss's parse of them."""
    dest.total_damage += src.total_damage
    dest.damage_taken += src.damage_taken
    dest.heals += src.heals
    dest.self_heals += src.self_heals
    dest.group_heals += src.group_heals
    dest.hit_count += src.hit_count
    dest.heal_count += src.heal_count
    dest.deaths += src.deaths
    dest.last_hit_ts = max(dest.last_hit_ts, src.last_hit_ts)
    for skill_id, sp in src.skills.items():
        own = dest.skills.get(skill_id)
        if own is None:
            dest.skills[skill_id] = copy.deepcopy(sp)
        else:
            _absorb_skill_parse(own, sp)


def _absorb_skill_parse(dest: SkillParse, src: SkillParse) -> None:
    """Sum one skill's add-segment parse into the boss's parse of it."""
    dest.damage += src.damage
    dest.hit_count += src.hit_count
    dest.crit_count += src.crit_count
    dest.min_hit = min(dest.min_hit, src.min_hit)
    dest.max_hit = max(dest.max_hit, src.max_hit)
    dest.heals += src.heals
    dest.self_heals += src.self_heals
    dest.group_heals += src.group_heals
    dest.heal_count += src.heal_count
    dest.min_heal = min(dest.min_heal, src.min_heal)
    dest.max_heal = max(dest.max_heal, src.max_heal)
    dest.damage_taken += src.damage_taken
    dest.taken_count += src.taken_count
    dest.last_hit_ts = max(dest.last_hit_ts, src.last_hit_ts)
    for affinity, value in src.affinity_damage.items():
        dest.affinity_damage[affinity] = \
            dest.affinity_damage.get(affinity, 0.0) + value
    for affinity, count in src.affinity_hits.items():
        dest.affinity_hits[affinity] = \
            dest.affinity_hits.get(affinity, 0) + count


def _absorb_seconds(dest: dict[int, float], src: dict[int, float]) -> None:
    """Sum per-second timeline buckets (group and per-player alike)."""
    for second, damage in src.items():
        dest[second] = dest.get(second, 0.0) + damage


class DpsTrackerHistory:
    """Persistence service for one DpsTracker instance."""

    def __init__(self, tracker):
        self.tracker = tracker

    def archive_current_encounter(self, reason: str = "Encounter Finished",
                                     boss_name: str = "") -> None:
        """Archive whichever sessions the current fight owns.

        ``boss_name`` is a HINT, passed by the caller that knows which boss is
        being engaged. It cannot be read off the session: the trash half is
        archived at the moment the boss appears, which is BEFORE
        `boss_session.target_name` is set — so without the hint the pulls
        leading to a boss would land in the day-wide trash file and the pairing
        the folder is built on would never happen.
        """
        t = self.tracker
        # NAME the unit that caused this split, because the whole recurring bug
        # is "a trash mob was treated as the boss" and the culprit is only ever
        # visible here (live 2026-10-03, heroic dungeon: the meter reset after
        # every pack and the log showed resets with no subject). `boss_name`
        # is the hint the engaging caller holds; the engaged session's own
        # target name is used when the caller had none.
        if reason == "Trash Mobs Cleared" and callable(t.log_line):
            who = (boss_name or "").strip() or (
                t.boss_session.target_name or "").strip() or "unknown unit"
            try:
                t.log_line(f"Top DPS: trash run archived as a boss split by "
                           f"'{who}'")
            except Exception:
                pass
        if t.in_dummy_fight or t.dummy_session.group_damage > 0:
            sessions = [t.dummy_session]
        elif t.in_boss_fight or t.boss_session.group_damage > 0:
            # ONE fight, ONE archive: the adds segment folds into the
            # boss's entry below (_absorb_boss_adds), so a boss kill is a
            # single DPS Analysis row. It used to append the adds as a
            # second session and one kill read as two fights (live
            # 2026-10-02: the rift's boss showed a boss entry and a
            # separate boss_adds entry for the same 50 seconds). Only the
            # 2026-09-19 edge — every point of damage on the adds and an
            # empty boss board — keeps the adds session as its own archive.
            if t.boss_session.group_damage > 0:
                sessions = [t.boss_session]
            else:
                sessions = [t.boss_adds_session]
        elif t.trash_session.group_damage > 0:
            sessions = [t.trash_session]
        else:
            sessions = []
        # The label EVERY session of this fight files under. One label for the
        # whole fight, not one per session: the adds segment is a different
        # `kind` from the boss's, so deriving the label per session skipped it
        # and it filed itself under the day-wide `boss_<profile>.json` — a THIRD
        # file for a single kill, with the boss's adds split away from the boss
        # they belong to. Read from the fight's own boss session, which is
        # `sessions[0]` whenever this is a boss fight.
        label = boss_file_label(boss_name) if boss_name else ""
        if not label and sessions:
            head = sessions[0]
            if head.kind in ("boss", "boss_adds"):
                label = boss_file_label(head.target_name)
        for session in sessions:
            if session.group_damage <= 0:
                continue
            archived = copy.deepcopy(session)
            if session is t.boss_session and t.boss_adds_session.group_damage > 0:
                _absorb_boss_adds(archived, t.boss_adds_session)
            archived.pause()
            if reason:
                archived.name = f"{session.name} ({reason})"
            # Every half of one fight carries the SAME boss, so the fight, its
            # adds and the packs that led to it land in a matching pair of
            # files instead of scattering across the day-wide ones.
            if label:
                archived.boss_label = label
            # The instance's difficulty, read the same way the badges read it
            # but WITHOUT the manual fallback: it decides the log's file name
            # (`file_for`), so a guess would be a permanent label on a fight
            # nobody can re-measure. "" simply leaves the name as it was.
            archived.mode = game_state.instance_mode(t.model)
            if any(x.start_time == archived.start_time for x in t.history):
                continue
            t.history.append(archived)
            if len(t.history) > 25:
                t.history.pop(0)
            if archived.kind == "dummy":
                self.append_dummy_test_csv(archived, reason)
            self.persist()

    def set_path(self, path: str | Path | None) -> None:
        t = self.tracker
        t._history_path = Path(path) if path else None
        t._history_dir = None
        if t._history_path is not None:
            self.load()

    def set_dir(self, path: str | Path | None) -> None:
        t = self.tracker
        t._history_path = None
        t._history_dir = Path(path) if path else None
        t._active_profile = None
        if t._history_dir is not None:
            self.load()

    def resolve_file(self) -> Path | None:
        t = self.tracker
        if t._history_path is not None:
            return t._history_path
        if t._history_dir is None:
            return None
        if t._active_profile:
            return t._history_dir / f"dps_history_{_slug_profile(t._active_profile)}.json"
        return t._history_dir / "dps_history.json"

    def profile_slug(self) -> str:
        return _slug_profile(self.tracker._active_profile) if self.tracker._active_profile else ""

    def day_dir(self, start_time: float | None) -> Path:
        """The folder a session's own date lands in: ``<YYYY-MM>/<DD>/``.

        Month then day, not a flat ``<YYYY-MM-DD>/``: a season of logs is a
        handful of month folders you can read at a glance, and a single busy
        day (a raid night, a dummy session) stops scattering a hundred files
        across one enormous directory. Both consumers of the layout — this
        module and `scan_history_dir` in the UI — walk the same two levels.
        """
        t = self.tracker
        lt = time.localtime(start_time or time.time())
        return t._history_dir / time.strftime("%Y-%m", lt) / time.strftime("%d", lt)

    def file_for(self, kind: str | None, start_time: float | None,
                    boss_label: str = "", mode: str = "",
                    outcome: str = "", hero_class: str = "",
                    dungeon: str = "") -> Path:
        """The file a session of `kind` belongs in.

        A run against a KNOWN boss is written beside that boss's other runs —
        ``<Boss>.json`` for the fight, ``<Boss> trash.json`` for the pulls
        that led to it — instead of into the day-wide file. That is what makes a
        folder read as "this boss, and everything it took": one pair of files
        per boss, accumulating across repeat kills, rather than a day's runs
        sorted by when they happened. The trash half carries every pack of the
        run grouped together (see `PackSplit`), so the two files are exactly the
        two halves of one run.

        The profile slug is still appended: two characters who both kill King
        Ratsar on the same day would otherwise write the same filename and one
        would silently overwrite the other. A difficulty the game reported is
        appended after it (`_HARD`), so a NORMAL and a HEROIC run of one boss
        are two logs rather than one, and the Fights list can show which is
        which without opening either.

        Per-boss files also carry the facts the field asked to read off the
        NAME (live 2026-10-03, after the whole-tree load freeze: "the app does
        not need to open the files, it can just do a dir list"):

            <Dungeon> - <Boss>_<player>_<class>_<PASS|FAILED>[_MODE].json

        The date needs no token — the file lives in ``<YYYY-MM>/<DD>/``. The
        outcome is the FILE's, not one attempt's: the file accumulates every
        pull on that boss, and the field's own rule is "if the group killed it,
        it's not a fail" — so one kill upgrades the file to PASS even when an
        earlier failed pull is stored in it (`persist` does the upgrade, and
        retires the old FAILED name when it happens).
        """
        slug = self.profile_slug()
        stem = history_kind_stem(kind)
        suffix = mode_suffix(mode)
        if boss_label:
            name = boss_file_label(boss_label)
            if dungeon:
                name = f"{dungeon} - {name}"
            if stem == "trash":
                # The marker rides AFTER the boss - `<Dungeon> - <Boss> trash_<slug>`
                # - so a run's pulls file sorts right beside that boss's own file
                # in the day folder instead of falling under "Trash pack". Files
                # written before 2026-10-04 keep the old `Trash pack <Boss>` name
                # and stay readable (`_file_descriptor` accepts both).
                name = f"{name} trash"
                return self.day_dir(start_time) / (f"{name}_{slug}{suffix}.json" if slug
                                                   else f"{name}{suffix}.json")
            tail = f"_{slug}" if slug else ""
            # The profile slug usually ALREADY carries the class (the game's
            # profile is "Name_Class_Hash"), so the token is only added when
            # it would say something the slug does not.
            if hero_class and hero_class.lower() not in slug.lower().split("_"):
                tail += f"_{hero_class}"
            if outcome in OUTCOME_TOKENS:
                tail += f"_{outcome}"
            return self.day_dir(start_time) / f"{name}{tail}{suffix}.json"
        name = f"{stem}_{slug}{suffix}.json" if slug else f"{stem}{suffix}.json"
        return self.day_dir(start_time) / name

    def dummy_csv_file(self, start_time: float | None) -> Path:
        """The day's sheet for this profile — beside the history file it belongs
        with, one file per day so a season of tests never becomes one huge
        sheet and the day folders the pruner already owns also own these."""
        t = self.tracker
        slug = self.profile_slug()
        name = f"{DUMMY_CSV_STEM}_{slug}.csv" if slug else f"{DUMMY_CSV_STEM}.csv"
        return self.file_for("dummy", start_time).parent / name

    @staticmethod
    def dummy_csv_slug(path: Path) -> str:
        """The character slug in a dummy sheet's name ('' = the shared file).

        Handles the pre-release `dummy_tests_<slug>` name as well, so a legacy
        file is never read as belonging to a character called "tests" — which
        is what the pruner would conclude, and it would delete it as a
        stranger's file.
        """
        stem = path.stem
        # The LEGACY stem first: `dummy_tests_Mage` also starts with the current
        # `dummy`, and matching the short one first would read the character as
        # "tests_Mage".
        for base in (_DUMMY_CSV_LEGACY, DUMMY_CSV_STEM):
            if stem == base or stem.startswith(base + "_"):
                return stem[len(base):].lstrip("_")
        return ""

    def _migrate_dummy_csv(self) -> None:
        """Fold a pre-release `dummy_tests_*.csv` into the current sheet.

        A rename when nothing is in the way; an APPEND when the current sheet
        already exists, because abandoning a day of the player's runs in a file
        nothing writes to any more is the one outcome the rename must not have.
        The append is the same path a normal run takes, so the header is written
        once and the rows keep their order. Anything unreadable or foreign is
        left exactly where it is.
        """
        t = self.tracker
        base = t._history_dir
        if base is None or not base.is_dir():
            return
        try:
            legacy_files = sorted(base.rglob(f"{_DUMMY_CSV_LEGACY}*.csv"))
        except OSError:
            return
        for path in legacy_files:
            if path.stem == _DUMMY_CSV_LEGACY or \
                    not path.stem.startswith(_DUMMY_CSV_LEGACY + "_"):
                continue
            slug = self.dummy_csv_slug(path)
            target = path.with_name(
                f"{DUMMY_CSV_STEM}_{slug}.csv" if slug
                else f"{DUMMY_CSV_STEM}.csv")
            try:
                if not target.exists():
                    path.replace(target)
                    continue
                text = path.read_text(encoding="utf-8-sig")
            except OSError:
                continue
            if _sheet_append(target, text) is None:
                continue                       # foreign file: leave it alone
            try:
                path.unlink()
            except OSError:
                pass

    def append_dummy_test_csv(self, session, reason: str = "") -> Path | None:
        """Append one finished dummy test to the day's CSV. Returns the path.

        Called from `archive_current_encounter`, so it happens for EVERY route
        that ends a test — the target length, Stop Test, walking out of the
        radius, Reset — which is the only way a player can trust that the sheet
        has every run they did rather than the ones that happened to end a
        particular way. Appending (rather than one file per test) keeps the day
        folder the pruner already manages, and the whole day's tests in one
        sheet is what a pivot table wants.

        utf-8-sig on purpose: these files are opened by Excel on Windows, where
        a BOM is the difference between a skill name and a row of mojibake. The
        reader is pandas/Sheets/LibreOffice, all of which take the BOM as a
        no-op.
        """
        t = self.tracker
        if t._history_dir is None or t._history_path is not None:
            return None                      # single-file mode writes no CSV
        text = dummy_test_csv(session, profile=self.tracker._active_profile or "",
                              reason=reason)
        if not text:
            return None
        path = self.dummy_csv_file(session.start_time)
        return _sheet_append(path, text)

    def _write_summary(self, path: Path, sessions) -> None:
        """Write the advisory sidecar for one just-archived log.

        Best-effort by construction: the log is the record and the sidecar is
        only a cache of it, so a failure here must never fail the archive. The
        fingerprint is the log's own `(mtime, size)` AFTER the write — the same
        `(path, mtime, size)` the reader keys on, so a later edit invalidates it
        and falls back to parsing rather than serving a stale aggregate.
        """
        try:
            st = path.stat()
            payload = summarize(sessions)
            payload["src"] = {"mtime": st.st_mtime, "size": st.st_size}
            atomic_write_json(summary_path_for(path), payload)
        except OSError:
            pass

    def _drop_summary(self, path: Path) -> None:
        """Remove a log's sidecar (best-effort) when the log itself goes."""
        try:
            summary_path_for(path).unlink(missing_ok=True)
        except OSError:
            pass

    def persist(self) -> None:
        t = self.tracker
        if t._history_path is not None:
            try:
                atomic_write_json(t._history_path, {"history": [_session_to_dict(s) for s in t.history]})
                self._write_summary(t._history_path, t.history)
            except OSError:
                pass
            return
        if t._history_dir is None:
            return
        # Group by FILE IDENTITY first (kind, day, boss, mode), then name the
        # file from the GROUP's facts: the outcome is the file's, not one
        # attempt's — one kill upgrades it to PASS even though an earlier
        # failed pull sits in the same file. The class and dungeon come from
        # the first session that knows them.
        buckets: dict[tuple, list[CombatSession]] = {}
        for session in t.history:
            key = (history_kind_stem(session.kind),
                   self.day_dir(session.start_time),
                   getattr(session, "boss_label", ""),
                   getattr(session, "mode", ""))
            buckets.setdefault(key, []).append(session)
        groups: dict[Path, list[dict]] = {}
        # The session OBJECTS behind each written file, kept so the sidecar can
        # be summarised without converting the dicts back. They must include any
        # folded-in FAILED twin (see `_absorb_failed_twin`), or the sidecar would
        # describe a smaller log than the one on disk.
        group_sessions: dict[Path, list] = {}
        for (stem, day, boss_label, mode), sessions in buckets.items():
            start_time = sessions[0].start_time
            boss_kind = boss_label and stem == "boss"
            outcome = ""
            hero_class = ""
            dungeon = ""
            if boss_kind:
                outcome = ("PASS" if any(not fight_failed(s) for s in sessions)
                           else "FAILED")
                for s in sessions:
                    if not hero_class:
                        hero_class = getattr(s, "hero_class", "") or self._me_class(s)
                    if not dungeon:
                        dungeon = getattr(s, "dungeon_name", "") or self._dungeon_for(s)
            path = self.file_for(sessions[0].kind, start_time, boss_label, mode,
                                 outcome=outcome, hero_class=hero_class,
                                 dungeon=dungeon)
            dicts = [_session_to_dict(s) for s in sessions]
            kept = sessions
            written_failed = None
            if boss_kind and outcome == "PASS":
                dicts, kept, written_failed = self._absorb_failed_twin(
                    path, dicts, sessions)
            groups[path] = dicts
            group_sessions[path] = kept
            if written_failed is not None:
                try:
                    written_failed.unlink()
                except OSError:
                    pass
                self._drop_summary(written_failed)
        written: set[Path] = set()
        for path, dicts in groups.items():
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                atomic_write_json(path, {"history": dicts})
                written.add(path)
                self._write_summary(path, group_sessions[path])
            except OSError:
                pass
        self._prune(written)

    def _absorb_failed_twin(self, pass_path: Path, dicts: list[dict],
                            sessions: list) -> tuple[list[dict], list, Path | None]:
        """Retire the FAILED file a first wipe wrote, now that a kill exists.

        The outcome lives in the NAME, and it can only move one way: a boss
        that was wiped and later killed accumulates both attempts in one file,
        which the field's rule reads as PASS ("if the group killed it, it's
        not a fail"). The old FAILED file's fights fold into the PASS write so
        no attempt is lost, and the stale name is removed by the caller.

        Returns the merged dicts AND the merged session objects (the old file's
        survivors + the current group), so the caller can summarise exactly
        what it wrote rather than only what this run archived.
        """
        tail = pass_path.stem.rpartition("_")
        if tail[1] and tail[2] not in OUTCOME_TOKENS:
            return dicts, sessions, None
        failed_stem = f"{tail[0]}_FAILED" if tail[1] else pass_path.stem
        failed = pass_path.with_name(f"{failed_stem}{pass_path.suffix}")
        if not failed.exists():
            return dicts, sessions, None
        try:
            old = read_history_file(failed)
        except Exception:
            old = []
        # Only the fights the new group does not already carry: the failed
        # pull is normally IN t.history too, so absorbing blindly would
        # double every attempt the moment the file upgrades.
        have = {d.get("start_time") for d in dicts}
        extra_dicts: list[dict] = []
        extra_sessions: list = []
        for s in old:
            s_d = _session_to_dict(s)
            if s_d.get("start_time") not in have:
                extra_dicts.append(s_d)
                extra_sessions.append(s)
        return extra_dicts + dicts, extra_sessions + sessions, failed

    def _me_class(self, session: CombatSession) -> str:
        """The archiving hero's class, off their own parse ("Priest")."""
        me = None
        if me is None:
            for p in getattr(session, "players", {}).values():
                if getattr(p, "is_me", False):
                    me = p
                    break
        return (getattr(me, "hero_class", "") or "") if me is not None else ""

    def _dungeon_for(self, session: CombatSession) -> str:
        """The dungeon a boss belongs to, from its unit id — name only.

        `unit_to_dungeon_map` keys on the data's boss ids, and the tracker
        keeps the raw id of every foe it has seen; the canonical form of that
        raw id is what the kill matcher already compares. A boss the data does
        not know (a new raid, an event spawn) resolves to "" and the file
        simply omits the dungeon segment rather than guessing one.
        """
        t = self.tracker
        addr = getattr(session, "target_addr", 0)
        raw = t._foe_raw_ids.get(addr, "") if addr else ""
        if not raw:
            return ""
        try:
            cid = udata.canonical_unit_id(raw)
            d = dungeons_data.unit_to_dungeon_map().get(cid)
        except Exception:
            return ""
        return (d.get("name", "") if d else "") or ""

    def _prune(self, written: set[Path]) -> None:
        t = self.tracker
        base = t._history_dir
        if base is None or not base.is_dir():
            return
        slug = self.profile_slug()
        try:
            for month in base.iterdir():
                if not month.is_dir():
                    continue
                for day in month.iterdir():
                    if not day.is_dir():
                        continue
                    # The dummy sheet rides the same day folders as the history
                    # JSON, so it has to be pruned with them — otherwise a day
                    # folder could never empty and a character switch would
                    # leave the previous character's tests sitting in moddata.
                    for path in day.glob(f"{DUMMY_CSV_STEM}*.csv"):
                        file_slug = self.dummy_csv_slug(path)
                        if (slug and file_slug == slug) or (not slug and not file_slug):
                            continue
                        try:
                            path.unlink()
                        except OSError:
                            pass
                    for path in day.glob("*.json"):
                        if path in written:
                            continue
                        kind, _, file_slug = path.stem.partition("_")
                        # `<kind>_<slug>_HARD.json`: the trailing difficulty is
                        # not part of the profile, or every named log would
                        # read as a stranger's and be swept away here.
                        file_slug = strip_mode_suffix(file_slug)
                        if kind in HISTORY_KIND_TOKENS:
                            # The day-wide `<kind>_<slug>.json` files.
                            if (slug and file_slug != slug) or (not slug and file_slug):
                                continue
                        elif is_boss_file(path):
                            # A per-boss file (`<Boss>_<slug>[_<class>]_<PASS|
                            # FAILED>[_MODE].json`). Same one-profile-per-folder
                            # rule, but matched on the FULL slug: the trailing
                            # token alone is `PASS` or the hash, never the
                            # profile, so reading it that way kept every
                            # stranger's boss log forever (see `boss_file_slug`).
                            if boss_file_is_mine(path.stem, slug):
                                continue
                        else:
                            continue
                        try:
                            path.unlink()
                        except OSError:
                            pass
                        # The sidecar describes a log that is now gone: leaving
                        # it would orphan a file the pruner no longer matches
                        # (a `.summary` is not a `*.json`).
                        self._drop_summary(path)
                    _rmdir_if_empty(day)
                _rmdir_if_empty(month)
        except OSError:
            pass

    def prune_older_than(self, days: float, directory: str | Path | None = None) -> dict:
        """Clear the log dir down to the last `days`, memory included.

        `directory` is passed in by the UI (which knows `dps_dir()`) so the
        cleanup works whether or not a game is attached — clearing last
        month's logs is not something that should require the game running.

        The in-memory history is pruned with the disk one, and that is the
        part that is easy to miss: `persist()` writes `t.history` back out, so
        a cleanup that only touched the files would have the very next archive
        re-create every folder it just deleted.
        """
        t = self.tracker
        base = Path(directory) if directory else t._history_dir
        if base is None:
            return {"folders": 0, "files": 0, "cutoff": ""}
        out = prune_history_older_than(base, days)
        try:
            keep_from = (time.time() - float(days) * 86400.0) if float(days) > 0 else 0.0
        except (TypeError, ValueError):
            keep_from = 0.0
        if keep_from and t.history:
            t.history = [s for s in t.history if (s.start_time or 0.0) >= keep_from]
        return out

    def _migrate_legacy(self) -> None:
        t = self.tracker
        base = t._history_dir
        if base is None or not base.is_dir():
            return
        slug = self.profile_slug()
        for path in sorted(base.glob("dps_history*.json")):
            file_slug = path.stem[len("dps_history_"):] if path.stem.startswith("dps_history_") else ""
            if (slug and file_slug != slug) or (not slug and file_slug):
                continue
            try:
                sessions = read_history_file(path)
            except Exception:
                continue
            for session in sessions:
                target = self.file_for(session.kind, session.start_time,
                                       mode=getattr(session, "mode", ""))
                try:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    old = read_history_file(target) if target.exists() else []
                    merged = {(s.start_time, s.name): s for s in old}
                    merged[(session.start_time, session.name)] = session
                    atomic_write_json(target, {"history": [_session_to_dict(s) for s in merged.values()]})
                except (OSError, ValueError):
                    pass
            try:
                path.unlink()
            except OSError:
                pass
            self._drop_summary(path)

    def load(self) -> None:
        t = self.tracker
        if t._history_path is not None:
            path = t._history_path
            if not path or not path.exists():
                return
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                loaded = [_session_from_dict(item) for item in data.get("history", [])]
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                return
            if loaded:
                t.history = loaded[-25:]
            return
        if t._history_dir is None:
            return
        self._migrate_legacy()
        self._migrate_dummy_csv()
        slug = self.profile_slug()
        loaded: list[CombatSession] = []
        # TODAY ONLY. This used to walk EVERY month/day folder and parse every
        # JSON under it, on whatever thread called load() - the GUI thread, at
        # attach. It kept the newest 25 sessions, so a veteran player's attach
        # re-read months of archives to throw almost all of it away, and the
        # window sat "Not Responding" while it ran (live 2026-10-03). The
        # in-memory window only feeds the dropdown's recent fights, and the
        # field confirmed the intent: "it should just load todays list".
        # Older days remain on disk for Past Fights / the All Fights scan,
        # which open one file at a time.
        today = self.day_dir(None)
        if today.is_dir():
            for path in sorted(today.glob("*.json")):
                kind, _, file_slug = path.stem.partition("_")
                if kind not in HISTORY_KIND_TOKENS:
                    continue
                # `<kind>_<slug>_HARD.json`: the difficulty token is not
                # part of the profile, and reading it as one would make a
                # player's own named log look like another character's and
                # silently drop their history on the next load.
                file_slug = strip_mode_suffix(file_slug)
                if (slug and file_slug != slug) or (not slug and file_slug):
                    continue
                try:
                    loaded.extend(read_history_file(path))
                except Exception:
                    pass
        loaded.sort(key=lambda session: session.start_time or 0.0)
        if loaded:
            t.history = loaded[-25:]
