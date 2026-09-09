"""Repeat-open caches for the Past Fights scan.

Opening the Fights list twice in a session used to re-do the same read-only
work both times. `history_days` globbed all sixty-odd day folders to ask "does
this one hold a log", and `scan_history_dir` then globbed them all AGAIN to
describe the files, reading a sidecar for every log inside the day window.
None of that changes unless the archive does, and the archive is quiet between
fights.

Three caches, each answering a different "has this moved?":

- the day listing (`day_stamps`) — turns sixty globs into sixty stats on a
  repeat open;
- the per-day rows (`day_rows`) — skips the descriptor rebuild and the sidecar
  reads for a day whose entries are all unchanged;
- the parsed sidecar (`cached_summary`) — the layer that actually removes
  READS. It is keyed on the log's own `(mtime, size)`, so it survives a day
  rebuild: adding one log to a cached day reads that log's sidecar and no
  others.

Measured on 60 days / 300 logs with a 7-day window (bench:
`ai/workspace/buffy/bench_fights_open.py`): a repeat open went from ~19 ms with
35 sidecar reads to ~6 ms with 0, and a full-season scan from ~58 ms with 300
reads to ~14 ms with 0. The residual cost is a scandir per visible day, which is
what keeps an added or rewritten log visible.

WHY THE ENTRY LIST, NOT THE FOLDER MTIME
----------------------------------------
The obvious key for "is the set of logs in here the same?" is the folder's own
mtime, and the first version of this module used it. It is not sound: Windows
updates a directory's mtime lazily, so creating a file often leaves it
untouched — measured on the dev box, 141 of 200 file creations did not move it.
A mtime-keyed cache therefore reuses a day's rows after a new log lands in it,
and the Past Fights list silently never shows that fight (it also missed an
in-place rewrite on the same lazy write). `_entry_signature` replaces it with
one `os.scandir` per day folder: the entry names answer add/remove/rename and
the per-entry stat answers an in-place rewrite. That is about one stat per log —
the cost the mtime key claimed — and it is sound. See `day_rows`.
"""
from __future__ import annotations

import os
from collections import OrderedDict
from pathlib import Path

#: Day folders whose rows are held. Each entry is one folder's descriptors, so
#: this is bounded by the number of DAYS the player has played, not by the
#: number of logs — 512 covers roughly a year and a half of daily play.
_DAY_ROWS_MAX = 512

#: day-folder path -> `{"sig": {name: (mtime, size)}, "rows": [desc…]}`, the
#: descriptor list last built for that folder plus the entry signature it was
#: built from. Ordering is access order, so the least-recently-seen day is the
#: one dropped when the bound is hit. Keyed by path alone: the signature is the
#: freshness check, so a stale mtime cannot leave two entries for one folder.
_DAY_ROWS: "OrderedDict[str, dict]" = OrderedDict()

#: `(search roots)` -> `(signature, stamps)` for the day listing.
_DAY_STAMPS: dict[tuple, tuple[tuple, frozenset]] = {}


def _dir_mtime(path: Path) -> float | None:
    """A folder's mtime, or None when it cannot be stat-ed (never raises).

    Used only by the day listing (`_stamps_signature`), where a coarse or lazy
    timestamp costs a redundant relist but never a wrong answer — a moved
    folder is always re-checked through its listing. The per-day rows use
    `_entry_signature` instead; see the module docstring.
    """
    try:
        return path.stat().st_mtime
    except OSError:
        return None


# --- the day listing -------------------------------------------------------

def _stamps_signature(search_dirs: list[Path], month_re, old_day_re,
                      has_logs) -> tuple:
    """Everything a change to the archive's shape would move, cheaply.

    One directory listing per search root and per month folder, then a stat per
    day folder. A glob per day folder is what this replaces: the old code
    opened every day folder to learn whether it held a log, and on a repeat
    open that was every folder, again, for an answer that had not moved.
    """
    sig = []
    for sdir in search_dirs:
        try:
            entries = sorted(sdir.iterdir())
        except OSError:
            sig.append((str(sdir), None))
            continue
        kids = []
        for e in entries:
            try:
                if not e.is_dir():
                    continue
            except OSError:
                continue
            if month_re.match(e.name):
                # A month folder's mtime covers day folders appearing or going;
                # its own listing is needed to learn WHICH day folders exist.
                try:
                    days = tuple(sorted(d.name for d in e.iterdir()
                                        if d.is_dir()))
                except OSError:
                    days = ()
                kids.append(("M", e.name, _dir_mtime(e), days))
            elif old_day_re.match(e.name):
                kids.append(("D", e.name, _dir_mtime(e)))
        sig.append((str(sdir), _dir_mtime(sdir), tuple(kids)))
    return tuple(sig)


def day_stamps(search_dirs: list[Path], month_re, day_re, old_day_re, has_logs
               ) -> frozenset:
    """The `YYYY-MM-DD` stamps of day folders holding a log, cached.

    `has_logs(day_dir)` answers "does this folder hold a log" from its contents;
    it is only called for a day folder whose mtime has moved, so an untouched
    archive pays no glob at all. The folder patterns are passed in rather than
    imported: `history_index` imports this module, so importing back the other
    way would be a cycle.
    """
    key = tuple(str(d) for d in search_dirs)
    sig = _stamps_signature(search_dirs, month_re, old_day_re, has_logs)
    hit = _DAY_STAMPS.get(key)
    if hit is not None and hit[0] == sig:
        return hit[1]

    stamps: set[str] = set()
    for sdir in search_dirs:
        try:
            entries = sorted(sdir.iterdir())
        except OSError:
            continue
        for e in entries:
            try:
                if not e.is_dir():
                    continue
            except OSError:
                continue
            if month_re.match(e.name):
                try:
                    for daydir in e.iterdir():
                        if (daydir.is_dir() and day_re.match(daydir.name)
                                and has_logs(daydir)):
                            stamps.add(f"{e.name}-{daydir.name}")
                except OSError:
                    pass
            elif old_day_re.match(e.name) and has_logs(e):
                stamps.add(e.name)        # pre-month-split `<YYYY-MM-DD>/`
    out = frozenset(stamps)
    _DAY_STAMPS[key] = (sig, out)
    return out


# --- the per-day rows ------------------------------------------------------

def _entry_signature(day_dir: Path) -> dict | None:
    """`{name: (mtime, size)}` for a folder's entries, from ONE `scandir`.

    The sound replacement for the folder-mtime key this module first used. The
    names answer "did the SET of logs change" (a new fight, a delete, a rename)
    and each entry's stat answers "was a log rewritten in place" — the two
    questions the folder mtime and the old per-file fingerprint split between
    them, answered in a single listing. `DirEntry.stat` is served from the
    directory buffer, so this is about one stat per log, the cost the mtime key
    claimed. None when the folder cannot be listed, so the caller builds
    without remembering.
    """
    try:
        with os.scandir(day_dir) as it:
            out: dict[str, tuple[float, int]] = {}
            for de in it:
                try:
                    st = de.stat()
                except OSError:
                    continue
                out[de.name] = (st.st_mtime, st.st_size)
    except OSError:
        return None
    return out


def day_rows(day_dir: Path, build) -> list[dict]:
    """One day folder's descriptors, reusing the last scan while it is still true.

    Freshness is the folder's ENTRY SIGNATURE — one `os.scandir` per call — not
    its mtime. The set of entries is how an added, removed or renamed log is
    noticed, which a lazily-updated directory mtime misses (see the module
    docstring); each entry's `(mtime, size)` covers an in-place rewrite. Both of
    the old scheme's checks live in that one listing, so a change can neither be
    missed nor need a second pass, and a mtime that does not move can no longer
    strand a day on stale rows.

    What the cache still removes is the expensive part: `build` re-stats and
    re-describes every log only when the signature moved, and the sidecar reads
    underneath are prevented by `cached_summary`, which is keyed per file — so
    adding one log to a cached day re-describes that day but reads one sidecar,
    not the whole day's. A stat is metadata, not a read: the repeat open still
    opens no log and no sidecar.

    `build` runs only when the signature differs, and its result is stored
    as-is: the caller must treat the rows as read-only and copy anything it
    hands to a widget, because `summary_for` writes `row["summary"]` onto the
    row it is given.
    """
    sig = _entry_signature(day_dir)
    if sig is None:
        return build()                   # unlistable: build, do not remember
    key = str(day_dir)
    hit = _DAY_ROWS.get(key)
    if hit is not None and hit["sig"] == sig:
        _DAY_ROWS.move_to_end(key)
        return hit["rows"]
    rows = build()
    _DAY_ROWS[key] = {"sig": sig, "rows": rows}
    while len(_DAY_ROWS) > _DAY_ROWS_MAX:
        _DAY_ROWS.popitem(last=False)
    return rows


#: Parsed sidecars held for the session, keyed by `(log path, mtime, size)` —
#: the same fingerprint the writer stamps into the sidecar itself, so a log
#: that changed under it is simply a different key. Ordered and bounded: this
#: is the layer that lets ONE new log in a cached day cost one sidecar read
#: instead of a whole day's.
_SUMMARY: "OrderedDict[tuple, dict | None]" = OrderedDict()
_SUMMARY_MAX = 2048


def cached_summary(path: Path, mtime: float, size: int, read) -> dict | None:
    """A log's parsed sidecar, remembered against the log's own fingerprint.

    `read` is called only on a miss. `None` is cached too, and deliberately:
    a log with no sidecar yet would otherwise be re-probed on every render,
    which is the common case for a fresh archive.
    """
    key = (str(path), mtime, size)
    if key in _SUMMARY:
        _SUMMARY.move_to_end(key)
        return _SUMMARY[key]
    summary = read()
    _SUMMARY[key] = summary
    while len(_SUMMARY) > _SUMMARY_MAX:
        _SUMMARY.popitem(last=False)
    return summary


def remember_summary(path: Path, mtime: float, size: int, payload) -> None:
    """Record a sidecar this process has just written.

    Without this a backfill would write the file and leave the cached `None` in
    place, so the aggregate line would stay missing until a restart even though
    the sidecar is sitting on disk.
    """
    key = (str(path), mtime, size)
    _SUMMARY[key] = payload
    _SUMMARY.move_to_end(key)
    while len(_SUMMARY) > _SUMMARY_MAX:
        _SUMMARY.popitem(last=False)


def forget_day_cache(day_dir: str | Path | None = None) -> None:
    """Drop cached rows for one day folder, or all of them when None.

    Rare: every mutation the app performs moves the folder's mtime and so
    invalidates itself. This exists for the paths that edit the archive from
    memory — the bulk delete, and a test asserting on a rebuild.
    """
    if day_dir is None:
        _DAY_ROWS.clear()
        _DAY_STAMPS.clear()
        _SUMMARY.clear()
        return
    target = str(day_dir)
    for k in [k for k in _DAY_ROWS if k == target]:
        del _DAY_ROWS[k]
    for k in [k for k in _SUMMARY if Path(k[0]).parent == Path(target)]:
        del _SUMMARY[k]
    _DAY_STAMPS.clear()      # cheap, and the listing spans every day folder
