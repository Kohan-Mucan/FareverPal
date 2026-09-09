"""Lazy index over the archived-fight folder.

`scan_history_dir` builds one row per log FILE from its name and its stat
alone — no JSON is parsed — and wraps each file's fights in `_LazySessions`,
which reads on first access. That keeps opening the Past Fights dialog cheap
over a season of logs, while every other caller (the encounter combo, the
inline list, the records strip) simply asks for the fights and pays for
exactly the files it touches.

Split out of `past_fights.py` so the dialog module stays under the 840-line
budget AGENTS.md enforces; `past_fights` re-exports these names, so no caller
moved.
"""
from __future__ import annotations

import json
import re
from collections import OrderedDict
from pathlib import Path

from ...core.dps_tracker import (
    CombatSession, HISTORY_KIND_TOKENS, read_history_file)
from ...core.dps_records import SUMMARY_VERSION as _SUMMARY_VERSION, summarize
from ...core.dps_tracker_history import (
    DAY_DIR_RE as _DAY_DIR_RE, MONTH_DIR_RE as _MONTH_DIR_RE,
    MODE_TOKENS as _MODE_TOKENS, OLD_DAY_DIR_RE as _OLD_DAY_DIR_RE,
    OUTCOME_TOKENS as _OUTCOME_TOKENS,
    boss_file_slug, strip_mode_suffix as _strip_mode_suffix, summary_path_for)
from ...config import dps_dir
from ...runtime.persist import atomic_write_json
from .history_cache import (cached_summary, day_rows, day_stamps,
                            forget_day_cache, remember_summary)


# The pre-month-split layout (`<YYYY-MM-DD>/`, one folder per day) is still on
# disk from before `DpsTrackerHistory.day_dir` switched to `<YYYY-MM>/<DD>/`;
# the scanner reads those folders rather than hide every fight they hold. The
# pattern is the WRITER's (imported as `_OLD_DAY_DIR_RE` above), like the
# day/month patterns beside it, so a layout change cannot leave this scanner
# reading a shape the delete path no longer knows.


def _in_day_folder(path: Path) -> bool:
    """Whether `path` sits in a day-layout folder (current or old)."""
    return bool(_DAY_DIR_RE.match(path.parent.name)
                or _OLD_DAY_DIR_RE.match(path.parent.name))


def _day_stamp(path: Path) -> str:
    """The ``YYYY-MM-DD`` folder stamp a log lives under, or "" if undated.

    Read from the path alone (``<YYYY-MM>/<DD>/<file>``), so a caller can
    filter to one day WITHOUT stat-ing or parsing a log — the whole point of a
    name-only index. Legacy flat ``dps_history*.json`` files sit in the root
    with no day folder and return "" (see `scan_history_dir`'s `day=`).
    """
    parent = path.parent
    month = parent.parent
    if _DAY_DIR_RE.match(parent.name) and _MONTH_DIR_RE.match(month.name):
        return f"{month.name}-{parent.name}"
    # A pre-month-split `<YYYY-MM-DD>/` folder names its own day, so the day
    # window can include or exclude it like any other — rather than reading it
    # as undated and always drawing it, which would defeat the window on an
    # install that upgraded mid-season.
    if _OLD_DAY_DIR_RE.match(parent.name):
        return parent.name
    return ""


def _char_label_from_history_path(path: Path) -> str:
    """Friendly character tag read straight off a history file name.

    Every history file is labeled with the profile it was written from: the
    day-wide files are ``<kind>_<char>_<class>_<hash>.json`` and the per-boss
    ones ``<Dungeon> - <Boss>_<char>_<class>_<PASS|FAILED>[_MODE].json`` (the
    trash half ``<Dungeon> - <Boss> trash_<...>.json``, or the pre-2026-10-04
    ``Trash pack <Boss>_<...>.json``), all inside a ``<YYYY-MM>/<DD>/`` folder;
    legacy flat files keep the ``dps_history[_<profile>].json`` naming. So the
    tag parses without opening the file at all.

    The per-boss SLUG comes from the shared `boss_file_slug`, which owns the
    underscore boundary and the trailing outcome/difficulty tokens; re-deriving
    it here is exactly how the index drifts from the writer's naming. Only the
    day-wide shape is not a per-boss slug, so only it reads its own tail.
    """
    stem = path.stem
    if stem in HISTORY_KIND_TOKENS and not _in_day_folder(path):
        return "Current Character"              # bare <kind>.json — no profile
    kind, sep, rest = stem.partition("_") if "_" in stem else (stem, "", "")
    if sep and kind in HISTORY_KIND_TOKENS:      # day-wide <kind>_<slug>[_MODE]
        slug = rest
    elif _in_day_folder(path):
        # per-boss file: the shared helper reads the slug boundary (after the
        # boss name's first underscore, minus any outcome/difficulty tail)
        slug = boss_file_slug(stem)
    elif stem == "dps_history":
        return "Current Character"              # legacy flat file, no profile
    else:                                       # legacy flat dps_history_<slug>
        slug = stem[len("dps_history_"):] if stem.startswith(
            "dps_history_") else stem
    # A difficulty token can sit after a day-wide slug (`trash_<slug>_HARD`);
    # a per-boss slug already had it removed by `boss_file_slug`, so this is a
    # no-op there and the only strip the day-wide shape needs.
    slug = _strip_mode_suffix(slug)
    parts = slug.split("_") if slug else []
    # The kill outcome is NOT handled here: only per-boss files carry one, and
    # `boss_file_slug` already took it off. The trailing class word and the
    # account hash are not part of the character's name, so they come off the
    # label — the class list is the LABEL's wider one (the writer's slug may
    # carry any hero class, not just the four the tracker knows by name).
    if parts and parts[-1].capitalize() in (
            "Warrior", "Rogue", "Mage", "Priest", "Ranger", "Paladin",
            "Druid", "Shaman", "Warlock", "Monk", "Hunter", "Cleric"):
        parts = parts[:-1]
    # drop the trailing account/server hash (e.g. S8c4ba8)
    if len(parts) > 1 and re.match(r"^S[0-9a-fA-F]{5,}$", parts[-1]):
        parts = parts[:-1]
    return " ".join(p.capitalize() for p in parts) if parts \
        else "Current Character"


class _LazySessions:
    """One history file's archived fights, parsed on FIRST access.

    `scan_history_dir` builds a row per FILE from its name alone; the fights
    inside are wrapped here so a caller that never opens the file never parses
    it. The behaviour matches the plain list it replaces for every operation
    the app uses — iteration, indexing, slicing, `len`, `bool`, `reversed`,
    `in` and `index` — and the read happens once and is cached.
    """

    __slots__ = ("_path", "_items")

    def __init__(self, path: Path) -> None:
        self._path = path
        self._items: list | None = None

    def _load(self) -> list:
        if self._items is None:
            try:
                self._items = list(read_history_file(self._path) or [])
            except Exception:
                self._items = []
        return self._items

    def __bool__(self) -> bool:
        return bool(self._load())

    def __len__(self) -> int:
        return len(self._load())

    def __iter__(self):
        return iter(self._load())

    def __reversed__(self):
        return reversed(self._load())

    def __getitem__(self, index):
        return self._load()[index]

    def __contains__(self, value) -> bool:
        return value in self._load()

    def index(self, *args):
        return self._load().index(*args)

    def __repr__(self) -> str:
        return f"<_LazySessions {self._path.name}>"


def load_history_sessions(path: str | Path) -> list[CombatSession]:
    """Read one archived log's fights — the on-demand half of the lazy scan.

    Everything that wants a file's contents (the dialog's View and delete, the
    records strip) goes through here rather than assuming `scan_history_dir`
    already read them, so the read happens exactly when a caller asks.
    """
    return list(read_history_file(Path(path)) or [])


#: Parsed logs held for the session, keyed by `(path, mtime, size)` — the same
#: fingerprint `scan_history_dir` stamps on a row, so a log rewritten in place
#: (a new mtime or size) is re-read and a stale entry can never be served.
#: Bounded on purpose: the Fights view rebuilds the whole list whenever the
#: folder changes, and without this every rebuild discarded the parsed fights
#: and re-read each log the player expanded again just to draw its rows.
_LOADED: "OrderedDict[tuple, list[CombatSession]]" = OrderedDict()
_LOADED_MAX = 8


def cached_history_sessions(path: str | Path, mtime: float | None = None,
                            size: int | None = None) -> list[CombatSession]:
    """`load_history_sessions`, but a log is parsed once per fingerprint.

    Pass the row's own `mtime`/`size` when the caller has them (the list already
    read them) to skip a stat. Returns `[]`, uncached, for a file that cannot be
    stat-ed — a missing log is not worth remembering.
    """
    p = Path(path)
    if mtime is None or size is None:
        try:
            st = p.stat()
            mtime, size = st.st_mtime, st.st_size
        except OSError:
            return []
    key = (str(p), mtime, size)
    hit = _LOADED.get(key)
    if hit is not None:
        _LOADED.move_to_end(key)
        return hit
    sessions = load_history_sessions(p)
    _LOADED[key] = sessions
    while len(_LOADED) > _LOADED_MAX:
        _LOADED.popitem(last=False)
    return sessions


#: A sidecar is a handful of numbers; anything larger is not ours (or is a
#: corrupt file) and is refused rather than parsed into memory.
_SUMMARY_MAX_BYTES = 64 * 1024


def read_log_summary(path: str | Path, mtime: float | None,
                     size: int | None) -> dict | None:
    """A log's advisory sidecar, or None when it is missing/stale/unreadable.

    "Stale" is decided by the fingerprint the writer stamped into the sidecar:
    the log's own `(mtime, size)` at the moment it was written. A log edited
    since — a fight deleted, a twin absorbed — no longer matches, and the
    caller falls back to parsing. This is deliberately advisory: the `.json` is
    the record and always wins, and every failure mode here (absent, truncated,
    old version, wrong size) reads as "no sidecar" rather than as a wrong answer.
    """
    try:
        sp = summary_path_for(Path(path))
        if sp.stat().st_size > _SUMMARY_MAX_BYTES:
            return None
        data = json.loads(sp.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("version") != _SUMMARY_VERSION:
        return None
    src = data.get("src") or {}
    if src.get("size") != size or src.get("mtime") != mtime:
        return None
    return data


def write_log_summary(path: str | Path, sessions, mtime: float | None = None,
                      size: int | None = None) -> dict | None:
    """Write the sidecar for one log now (the backfill / write half).

    Used at archive time by the tracker, and here to convert an existing log
    the first time an explicit "reads every log" action touches it. Returns the
    payload, or None when the file cannot be stat-ed or written.
    """
    p = Path(path)
    try:
        if mtime is None or size is None:
            st = p.stat()
            mtime, size = st.st_mtime, st.st_size
        payload = summarize(sessions)
        payload["src"] = {"mtime": mtime, "size": size}
        atomic_write_json(summary_path_for(p), payload)
        # The scan may already have cached "no sidecar" for this exact
        # fingerprint; record what was just written so the row does not keep
        # claiming the aggregate is missing.
        remember_summary(p, mtime, size, payload)
        return payload
    except OSError:
        return None


def summary_for(row: dict, backfill: bool = False) -> dict | None:
    """The row's sidecar summary; with `backfill`, parse + write one if absent.

    The collapsed list asks with `backfill=False` — it must never read a log
    just to draw its row. The records strip and the Failed filter ask with
    `backfill=True`: they are explicit "look at everything" actions that would
    read every log anyway, so the first such click converts a season of legacy
    logs to sidecars and every later one is read-free.
    """
    summary = row.get("summary")
    if summary is not None or not backfill:
        return summary
    sessions = cached_history_sessions(row["path"], row.get("mtime"),
                                       row.get("size"))
    summary = write_log_summary(row["path"], sessions, row.get("mtime"),
                                row.get("size"))
    row["summary"] = summary
    return summary


def summary_line(summary: dict | None) -> str:
    """The aggregate line a collapsed log row draws: count, best damage, time.

    "" for a log that holds nothing to say (no sidecar, or no fights), so the
    row simply omits the line rather than printing zeroes. The duration is the
    best-damage fight's, and a `☠` marks a log holding a failed boss pull —
    the same fact the Failed filter selects on.
    """
    if not summary:
        return ""
    count = int(summary.get("fights") or 0)
    if count <= 0:
        return ""
    parts = [f"{count} fight" + ("" if count == 1 else "s")]
    damage = float(summary.get("best_damage") or 0.0)
    if damage > 0.0:
        parts.append(f"best {damage:,.0f} DMG")
    duration = float(summary.get("best_duration") or 0.0)
    if duration > 0.0:
        parts.append(f"{int(duration // 60):02d}:{duration % 60:02.0f}")
    if summary.get("has_failure"):
        parts.append("\u2620")
    return " \u00b7 ".join(parts)


def forget_cached_sessions(path: str | Path | None = None) -> None:
    """Drop cached parses (one log, or all of them).

    For a delete or a rewrite that the fingerprint cannot see — same size and a
    same-second mtime — so stale fights never outlive the change.
    """
    if path is None:
        _LOADED.clear()
        forget_day_cache()
        return
    p = Path(path)
    key_prefix = str(p)
    for k in [k for k in _LOADED if k[0] == key_prefix]:
        _LOADED.pop(k, None)
    # The row caches for this log's DAY go too: a bulk delete removes the file
    # (which moves the folder's mtime) but a caller may also be holding rows it
    # built before the change.
    forget_day_cache(p.parent)


#: The difficulty/outcome token sets are the WRITER's (imported at the top as
#: `_MODE_TOKENS` / `_OUTCOME_TOKENS`), not re-spelled here: a token the writer
#: adds but the index does not know would silently leak into a row's name.
_KIND_FILE_LABEL = {
    "boss": "Boss fights",
    "trash": "Trash pulls",
    "dummy": "Test dummy",
    "overall": "Overall",
}


def _file_descriptor(path: Path) -> dict:
    """A history file described from its NAME alone, without reading it.

    The writer's naming is load-bearing here: the day-wide files are
    ``<kind>_<slug>.json`` and the per-boss ones ``<Boss>_<slug>.json`` (the
    trash half ``<Boss> trash_<slug>.json``, or its legacy
    ``Trash pack <Boss>_<slug>.json`` name), all inside ``<YYYY-MM>/<DD>/``.
    So the kind, the boss a log belongs to and the character are all legible
    from the path — which is what lets the dialog list a folder of logs without
    opening one of them.
    """
    stem = path.stem
    head = stem.partition("_")[0]
    boss = ""
    dungeon = ""
    if head in HISTORY_KIND_TOKENS:
        kind = head
        name = _KIND_FILE_LABEL.get(kind, kind.title())
    elif stem.startswith("Trash pack ") or head.endswith(" trash"):
        kind = "trash"
        # The boss label leads and the profile slug follows the FIRST underscore
        # (the same boundary `_char_label_from_history_path` reads), so the boss
        # is not the whole tail — it used to come out as
        # "Nightqueen Shaarlize Te'ror_Kohan_Warrior_S8c4ba8".
        #
        # The writer names a pull `<Dungeon> - <Boss> trash_<slug>` (the marker
        # AFTER the boss, so the pulls file sorts beside the boss's own). Files
        # written before 2026-10-04 spell it `Trash pack <Dungeon> - <Boss>_<slug>`;
        # both are read here and both render as "<Boss> trash".
        inner = (stem[len("Trash pack "):] if stem.startswith("Trash pack ")
                 else head[:-len(" trash")]).partition("_")[0].strip()
        # Split the dungeon out exactly like the per-boss branch below. Without
        # it `boss` read as "Kobolds Mines - Reblochonk" — the dungeon glued onto
        # the target line the row draws — and `dungeon` was left empty even
        # though the name carries it.
        if " - " in inner:
            dungeon, _, boss = inner.partition(" - ")
            dungeon, boss = dungeon.strip(), boss.strip() or inner
        else:
            boss = inner or "Unknown Boss"
        name = f"{inner or 'Unknown Boss'} trash"
    elif stem.startswith("dps_history"):
        kind = "overall"
        name = "Legacy log"
    else:
        # A per-boss file. The modern writer names it
        # `<Dungeon> - <Boss>_<player>_<class>_<PASS|FAILED>[_MODE].json`, so
        # the dungeon (when the data knows one) leads before " - " and the
        # outcome rides near the end — all legible without opening the file.
        kind = "boss"
        head = stem.partition("_")[0]
        dungeon = ""
        if " - " in head:
            dungeon, _, boss = head.partition(" - ")
            boss = boss.strip() or head
        else:
            boss = head
        name = head
    try:
        st = path.stat()
        mtime, size = st.st_mtime, st.st_size
    except OSError:
        mtime, size = 0.0, 0
    # The archive folder IS the date the log belongs to (`<YYYY-MM>/<DD>/`),
    # so a list can group by day without reading a file — and without the
    # mtime drift a file copied or restored later would introduce. A legacy
    # flat file has no day folder and falls back to its own mtime.
    day = ""
    if _DAY_DIR_RE.match(path.parent.name):
        month = path.parent.parent.name
        if _MONTH_DIR_RE.match(month):
            day = f"{month}-{path.parent.name}"
    elif _OLD_DAY_DIR_RE.match(path.parent.name):
        day = path.parent.name          # pre-month-split `<YYYY-MM-DD>/`
    # The difficulty the game reported when the fight was archived, when the
    # writer could name it. Read off the NAME like the rest of the row, so the
    # list can badge a log without opening it.
    mode = ""
    _head, sep, tail = stem.rpartition("_")
    if sep and tail in _MODE_TOKENS:
        mode = tail.lower()
        stem = _head                     # read the next token below off the rest
    # The kill outcome the per-boss name carries (`_PASS` / `_FAILED`), when
    # the writer knew it: a file that holds a kill reads PASS even if an
    # earlier wipe is stored in it — "if the group killed it, it's not a fail".
    outcome = ""
    _head2, sep2, tail2 = stem.rpartition("_")
    if sep2 and tail2 in _OUTCOME_TOKENS:
        outcome = tail2.lower()
    return {"path": path, "char": _char_label_from_history_path(path),
            "kind": kind, "name": name, "boss": boss, "mtime": mtime,
            "size": size, "day": day, "mode": mode, "dungeon": dungeon,
            "outcome": outcome}


def history_days(directory: str | Path | None = None) -> list[str]:
    """The day folders holding archives, newest first — WITHOUT one stat.

    The Past Fights list opens showing only its newest `_FIGHTS_RECENT_DAYS`
    days, and it needs to know both how MANY days exist and WHICH ones that
    window keeps before describing a single log. This is that cheap listing: it
    reads the month/day folder NAMES (the ``<YYYY-MM>/<DD>/`` layout the writer
    already stamps into every path) and nothing else — no file is opened,
    stat-ed or parsed. Legacy flat ``dps_history*.json`` files sit in the root
    with no day folder and are not counted here; they are always shown, so they
    neither fill nor shorten the window.
    """
    if directory is None:
        directory = dps_dir()
    directory = Path(directory)
    search_dirs = [directory]
    if (directory / "dps").is_dir():
        search_dirs.insert(0, directory / "dps")

    def _has_logs(folder: Path) -> bool:
        """Whether a day folder holds any log at all — a name match, no stat.

        An EMPTY day folder (left after a delete) must not fill the window: if
        it did, the default view could point its newest N days at empty folders
        and show nothing while older days sat full. Called only for a folder
        whose mtime has moved (see `history_cache.day_stamps`), so an untouched
        archive opens without globging a single day folder.
        """
        try:
            return next(folder.glob("*.json"), None) is not None
        except OSError:
            return False

    stamps = day_stamps(search_dirs, _MONTH_DIR_RE, _DAY_DIR_RE,
                        _OLD_DAY_DIR_RE, _has_logs)
    return sorted(stamps, reverse=True)


def scan_history_dir(directory: str | Path | None = None,
                     day: str | None = None,
                     days: set[str] | None = None) -> list[dict]:
    """Every archived log under dps_dir(), newest first — WITHOUT reading one.

    Walks the month/day subfolders (``<YYYY-MM>/<DD>/<kind>[_<profile>].json``,
    the layout `DpsTrackerHistory.day_dir` writes) plus any legacy flat
    ``dps_history*.json`` files still in the root. The two folder patterns are
    imported from the writer rather than re-spelled here, which is what keeps a
    layout change from leaving this scanner quietly finding nothing.

    Every row is built from the file's NAME (`_file_descriptor`) and carries a
    `_LazySessions` — the JSON is parsed only when something asks for the
    fights. The Past Fights dialog lists files and opens one only when a row is
    viewed, so a folder of a thousand logs costs a thousand directory entries
    and stats instead of a thousand parses.

    ``day`` (a ``YYYY-MM-DD`` stamp) restricts the walk to that one day folder,
    skipping every other day's files entirely. That is what the encounter combo
    and the breakdown use on a cold start: their disk fallback only ever wants
    TODAY'S fights, and without this it iterated every row's `_LazySessions` —
    parsing all 66 logs, ~44 ms, on the GUI thread (live 2026-10-03) — to keep
    three. The Past Fights dialog passes no `day` and still sees the season.
    Undated legacy flat files are excluded when `day` is set (they carry no
    day folder to match); the dialog remains the place they show up.

    ``days`` is a SET of ``YYYY-MM-DD`` stamps — the day window the Past Fights
    list keeps — and a file outside it is skipped BEFORE `_file_descriptor`, so
    an out-of-window log costs no stat and no sidecar read at all. Unlike
    ``day``, an undated legacy flat file (which has no stamp) is KEPT: the
    window is a view choice, and it must never hide a player's only log.
    """
    if directory is None:
        directory = dps_dir()
    directory = Path(directory)
    search_dirs = [directory]
    if (directory / "dps").is_dir():
        search_dirs.insert(0, directory / "dps")
    rows = []
    seen_paths = set()

    def _describe(p: Path) -> dict | None:
        """One log described from its NAME plus its sidecar — no log is opened.

        Returns None for an empty stub, which is not a fight.
        """
        desc = _file_descriptor(p)
        if desc["size"] <= 0:
            return None
        # The advisory sidecar, read from its own tiny file — never by parsing
        # the log. Absent/stale reads as None and every caller falls back to the
        # log itself; the scan still opens no LOG. Cached against the log's own
        # fingerprint, so a day that gained one log reads one sidecar rather
        # than all of them.
        desc["summary"] = cached_summary(
            p, desc["mtime"], desc["size"],
            lambda: read_log_summary(p, desc["mtime"], desc["size"]))
        return desc

    def _emit(descs) -> None:
        """Filter one folder's described logs into the result, newest sort last."""
        for desc in descs:
            p = desc["path"]
            if p in seen_paths:
                continue
            seen_paths.add(p)
            if day is not None and _day_stamp(p) != day:
                continue                    # another day: skip before it is used
            if days is not None:
                stamp = _day_stamp(p)
                # Undated legacy files (no stamp) are always kept; only a
                # DATED day outside the window is skipped.
                if stamp and stamp not in days:
                    continue                # outside the day window
            # A FRESH dict and a FRESH _LazySessions per render: the cached
            # descriptor list is shared, and `summary_for(backfill=True)` writes
            # `row["summary"]` onto the row it is handed.
            rows.append({**desc, "sessions": _LazySessions(p)})

    for sdir in search_dirs:
        try:
            entries = sorted(sdir.iterdir())
        except OSError:
            continue
        for e in entries:
            cands: list[Path] = []
            if e.is_dir():
                if _MONTH_DIR_RE.match(e.name):
                    # <YYYY-MM>/<DD>/* — the live layout
                    try:
                        # `daydir`, NOT `day`: `day` is the requested day-filter
                        # parameter, and shadowing it here made every file look
                        # like it belonged to a different day.
                        daydirs = [d for d in sorted(e.iterdir())
                                   if d.is_dir() and _DAY_DIR_RE.match(d.name)]
                    except OSError:
                        daydirs = []
                    for daydir in daydirs:
                        stamp = f"{e.name}-{daydir.name}"
                        # The day filter is pushed DOWN to the folder. Every log
                        # in here carries this stamp — it is the folder name —
                        # so a day the caller did not ask for is never globbed,
                        # let alone described or sidecar-read. This is what makes
                        # a windowed scan cost the visible days rather than the
                        # season.
                        if days is not None and stamp not in days:
                            continue
                        if day is not None and stamp != day:
                            continue
                        # Cached on the folder's entry signature: a repeat open
                        # of an untouched day pays one scandir instead of a glob,
                        # a stat per log and a sidecar read per log.
                        _emit(day_rows(
                            daydir,
                            lambda d=daydir: [d2 for d2 in (
                                _describe(p)
                                for p in sorted(d.glob("*.json"))) if d2]))
                elif (_DAY_DIR_RE.match(e.name)
                        or _OLD_DAY_DIR_RE.match(e.name)):
                    # A <DD> folder at the root: an interrupted migration, or
                    # a month folder that never got written; a <YYYY-MM-DD>
                    # folder is the pre-month-split layout. Read any of them
                    # rather than hide those fights.
                    try:
                        cands = sorted(e.glob("*.json"))
                    except OSError:
                        cands = []
                    _emit([d2 for d2 in (_describe(p) for p in cands) if d2])
            elif (e.is_file() and e.name.startswith("dps_history")
                    and e.suffix == ".json"):
                cands = [e]
                _emit([d2 for d2 in (_describe(p) for p in cands) if d2])
    rows.sort(key=lambda r: r.get("mtime") or 0.0, reverse=True)
    return rows
