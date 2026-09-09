"""Read back the bridge's own log file for diagnostics.

Bridge logging is DEV-OPT-IN. By default no bridge binary writes a log file at
all: a user must never find one in the Steam folder. Each binary (the three
proxies and the injected DLL) looks for a ``FAREVERMOD_DEBUG_LOG`` file next to
itself and, only when that file exists, writes to the path named on its first
line. So:

* ``run-log.bat`` sets ``FAREVER_DEV_CRASH_LOG``; in that dev run the app
  writes the flag (pointing into the app's own folder) next to the DLL it
  is about to load, and the log lands in the repo next to ``run.log`` -
  never in the game folder. Every other run only ever *removes* a leftover
  flag, so a normal session litters nothing behind.
* With no flag there is no log anywhere - not in the game folder, not in
  ``%APPDATA%``.

Older builds wrote ``<name>_proxy.log`` / ``farever_dps.log`` straight into the
game folder; that legacy path is still read (and its tail snapshotted before
the game-folder cleanup deletes it) so an un-updated DLL keeps explaining
itself. The tail is where the reason a loaded-but-silent bridge is silent
appears - ``[hunt] table not present yet``, ``hooks partial arm: ...``,
``hlboot verified ... 4/4``, ``NOT ARMED``.

Only the tail is read (the last few KB, parsed from the end) and the result is
cached on ``(path, mtime, size)``, because Live Diagnostics repaints once a
second and must not re-read an unchanged file.

Pure stdlib and no Qt, so it stays testable and usable from the UI thread.
"""
from __future__ import annotations

import os
import time

from .pe_imports import (PROXY_DLL_NAMES, proxy_debug_flag_name,
                         proxy_log_name)

#: Env var ``run-log.bat`` sets (to its own ``run.log``) to mark a developer
#: run. Its presence is the ONLY thing that turns bridge file logging on.
DEV_LOG_ENV = "FAREVER_DEV_CRASH_LOG"

#: Optional env var naming the FOLDER the bridge dev log goes in, so it can sit
#: somewhere other than the crash log. The crash log's own variable doubles as
#: a directory hint when this is unset, which is what the first version did -
#: and why moving the bridge log also dragged ``run.log`` out of the repo root.
DEV_LOG_DIR_ENV = "FAREVER_BRIDGE_DEV_LOG_DIR"

#: One shared dev log for every bridge binary. The flag file the binaries read
#: holds a single path, so all of them append here; only one capture mode is
#: live at a time in practice.
DEV_LOG_NAME = "bridge_dev.log"


def dev_mode() -> bool:
    """True when the app was started by ``run-log.bat`` (developer run)."""
    return bool((os.environ.get(DEV_LOG_ENV) or "").strip())


def dev_log_dir() -> str:
    """Folder the dev bridge log is written to - beside the app, not the game.

    ``FAREVER_BRIDGE_DEV_LOG_DIR`` wins when set, so the bridge log can live
    next to the bridge binaries in ``dps_bridge\\`` while the crash log stays
    at the repo root. Without it the crash log's own variable doubles as a
    directory hint - its folder is where a dev expects a log either way.
    Outside a dev run there is no destination at all (frozen builds fall back
    to the app's moddata folder, which nothing uses when the flag is never
    written).
    """
    override = (os.environ.get(DEV_LOG_DIR_ENV) or "").strip()
    if override:
        return os.path.abspath(override)
    env = (os.environ.get(DEV_LOG_ENV) or "").strip()
    if env:
        folder = os.path.dirname(os.path.abspath(env))
        if folder:
            return folder
    try:
        from ..config import config_dir
        return str(config_dir())
    except Exception:
        return ""


def dev_log_path() -> str:
    """Full path the dev log is written to, or '' when there is no folder."""
    folder = dev_log_dir()
    return os.path.join(folder, DEV_LOG_NAME) if folder else ""


def dev_flag_path(folder: str) -> str:
    """Path of the flag file a bridge binary in `folder` looks for."""
    if not folder:
        return ""
    return os.path.join(folder, proxy_debug_flag_name())


def _flag_log_path(folder: str) -> str:
    """Log path named by the flag in `folder` (first line), or ''."""
    flag = dev_flag_path(folder)
    if not flag:
        return ""
    try:
        with open(flag, "r", encoding="utf-8", errors="replace") as f:
            return (f.readline() or "").strip()
    except OSError:
        return ""


def write_dev_flag(folder: str, log_path: str = "") -> str:
    """Write the dev-only flag beside the DLLs in `folder`. Returns its path.

    The content is the log path the binary should append to; the DLLs re-read
    it on every log call, so this can be dropped in mid-session. Never raises:
    a failed write just means no log, never a broken attach.
    """
    flag = dev_flag_path(folder)
    target = log_path or dev_log_path()
    if not flag or not target:
        return ""
    try:
        os.makedirs(folder, exist_ok=True)
        with open(flag, "w", encoding="utf-8") as f:
            f.write(target + "\n")
        return flag
    except OSError:
        return ""


def remove_dev_flag(folder: str) -> bool:
    """Delete a leftover dev flag so a normal run leaves no logging behind."""
    flag = dev_flag_path(folder)
    if not flag:
        return False
    try:
        if os.path.isfile(flag):
            os.unlink(flag)
            return True
    except OSError:
        pass
    return False


def sync_dev_flag(folder: str) -> str:
    """Make `folder` match the current run: flag in dev mode, gone otherwise.

    The one call every install / launch path uses, so a normal user's game
    folder can never keep a flag from an earlier developer run.
    """
    if not folder:
        return ""
    if dev_mode():
        return write_dev_flag(folder)
    remove_dev_flag(folder)
    return ""

#: Legacy per-proxy log names: what builds BEFORE the dev flag wrote straight
#: into the game folder. A current binary writes nothing there, but the name is
#: still read when present (an un-updated DLL keeps explaining itself). Derived
#: from the same helper the game-folder cleanup uses, so the reader and the
#: remover can never disagree about a file name.
PROXY_LOG_FILES: dict[str, str] = {name: proxy_log_name(name) for name in PROXY_DLL_NAMES}

#: The injected bridge DLL's legacy game-folder log.
INJECTOR_LOG_FILE = "farever_dps.log"

_MAX_TAIL_BYTES = 16 * 1024
_MAX_CACHE = 32

#: Logs the game-folder cleanup removed, keyed by (folder, file name). The file
#: is gone, but *why* a loaded-but-silent bridge was silent is the whole reason
#: this module exists - so the tail is kept in memory instead of being thrown
#: away with the file, and a cleanup no longer costs the evidence of the crash
#: that prompted it.
_MAX_SNAPSHOTS = 8
#: Snapshots keep more lines than the card shows, so a later reader asking for
#: three lines still gets the end of the log.
_SNAPSHOT_LINES = 40
_snapshots: dict[tuple[str, str], dict] = {}


def _folder_key(path: str) -> str:
    return os.path.normcase(os.path.abspath(os.path.dirname(path) or "."))


def _snapshot_key(path: str) -> tuple[str, str]:
    return (_folder_key(path), os.path.basename(path).lower())


def snapshot_tail(path: str, max_lines: int = _SNAPSHOT_LINES) -> bool:
    """Keep the tail of `path` before it is deleted. True if anything was kept.

    Called by the game-folder cleanup on the way to `unlink`. Never raises: a
    cleanup must not fail because a log could not be read.
    """
    try:
        st = os.stat(path)
        if st.st_size == 0:
            return False
        info = _parse_tail(path, st, max_lines)
        if not info.get("lines") and not info.get("heartbeats"):
            return False
        kept = dict(info)
        kept["snapshot"] = True
        kept["mtime"] = st.st_mtime
        key = _snapshot_key(path)
        _snapshots.pop(key, None)      # re-snapshotting moves it to the end
        _snapshots[key] = kept
        while len(_snapshots) > _MAX_SNAPSHOTS:
            _snapshots.pop(next(iter(_snapshots)))
        return True
    except Exception:
        return False


def snapshot_if_bridge_log(path: str) -> bool:
    """Snapshot `path` only when it is one of the bridge's own logs.

    The one call every remover should use: it keeps a stray `*_proxy.log` /
    `farever_dps.log` from being deleted without its tail being kept, and
    stops a flag or a grace override from being filed as if it were a log.
    """
    name = os.path.basename(path).lower()
    if not name.endswith("_proxy.log") and name != INJECTOR_LOG_FILE:
        return False
    return snapshot_tail(path)


def snapshot_for(path: str) -> dict | None:
    """The kept tail for `path`, or None. Only consulted when the file is gone."""
    return _snapshots.get(_snapshot_key(path))


def clear_snapshots() -> None:
    """Drop the kept tails (tests; a fresh session starts clean)."""
    _snapshots.clear()


def _as_snapshot_read(info: dict, max_lines: int) -> dict:
    """A kept tail read back: honest age, the caller's line budget, and a note
    saying where it came from so it is never mistaken for a live file."""
    out = dict(info)
    mtime = out.get("mtime")
    if isinstance(mtime, (int, float)):
        out["age_s"] = max(0.0, time.time() - mtime)
    out["lines"] = (out.get("lines") or [])[-max_lines:]
    out["note"] = ("kept from the previous session - the log file was removed "
                   "by the game-folder cleanup")
    return out

#: Heartbeats are the one line an unhealthy bridge still produces, so they are
#: counted and omitted rather than shown: keeping them would push the status
#: lines that explain the failure out of the visible tail.
_HEARTBEAT_MARKERS = ("[HEARTBEAT]", "heartbeat")

_cache: dict[tuple, dict] = {}


def dev_log_candidates(game_dir: str = "") -> list[str]:
    """Dev-flag log path(s) for this session, most specific first.

    The flag sitting in `game_dir` is the DLLs' own source of truth (a dev may
    have pointed it anywhere by hand); when the app itself is in a dev run the
    path it would write the flag with is offered too, so the log is found even
    before the flag lands.
    """
    out: list[str] = []
    named = _flag_log_path(game_dir)
    if named:
        out.append(named)
    if dev_mode():
        path = dev_log_path()
        if path and os.path.normcase(path) not in {os.path.normcase(p) for p in out}:
            out.append(path)
    return out


def bridge_log_candidates(game_dir: str, mode: str, proxy_name: str = "") -> list[str]:
    """Log paths worth reading for `mode`, most specific first.

    An empty list means this capture mode writes no log at all (the memory
    reader does not live inside the game, so it has nothing to say there).
    """
    if not game_dir:
        return []
    mode = (mode or "").lower()
    if mode not in ("proxy", "injector"):
        return []
    # The dev-flag log first: a current bridge writes nothing into the game
    # folder at all. The legacy names stay behind it so an un-updated DLL still
    # explains itself.
    out: list[str] = list(dev_log_candidates(game_dir))
    if mode == "injector":
        out.append(os.path.join(game_dir, INJECTOR_LOG_FILE))
    else:
        wanted = PROXY_LOG_FILES.get((proxy_name or "").lower())
        if wanted:
            # The active name is known, so read only its log. A leftover file
            # from a different proxy belongs to a different session and would
            # explain the wrong thing - the same way a leftover DLL used to
            # mask the working one.
            out.append(os.path.join(game_dir, wanted))
        else:
            out.extend(os.path.join(game_dir, n) for n in PROXY_LOG_FILES.values())
    seen: set[str] = set()
    unique: list[str] = []
    for path in out:
        key = os.path.normcase(path)
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def _missing_note(mode: str, proxy_name: str = "") -> str:
    mode = (mode or "").lower()
    if mode not in ("proxy", "injector"):
        return ""
    if dev_mode():
        # No binary writes into the game folder any more, so the legacy note
        # would send a developer looking for a file that is never created.
        path = dev_log_path() or DEV_LOG_NAME
        return (f"no {DEV_LOG_NAME} at {path} yet - developer logging is on, "
                "so the bridge writes there as soon as it runs")
    if mode == "proxy":
        wanted = PROXY_LOG_FILES.get((proxy_name or "").lower())
        if wanted:
            return (f"no {wanted} in the game folder yet - the game has not "
                    f"loaded {proxy_name.lower()} (see the DLL Detection line above)")
        return ("no proxy log in the game folder yet - the game has not loaded "
                "the proxy DLL (see the DLL Detection line above)")
    return ("no farever_dps.log in the game folder yet - the injected DLL has "
            "not started up in this session")


def tail_bridge_log(game_dir: str, mode: str, proxy_name: str = "",
                    max_lines: int = 14) -> dict:
    """Tail of the bridge log for the active mode.

    Returns a dict with ``found``, ``name``, ``path``, ``lines`` (oldest first,
    heartbeats removed), ``heartbeats`` (how many were dropped), ``age_s``
    (seconds since the file was last written) and ``note`` (why there is
    nothing when there is nothing).
    """
    for path in bridge_log_candidates(game_dir, mode, proxy_name):
        try:
            st = os.stat(path)
        except OSError:
            # Gone - but if the cleanup kept its tail, that is still this
            # mode's own evidence, merely older. A live file always wins.
            kept = snapshot_for(path)
            if kept is not None:
                return _as_snapshot_read(kept, max_lines)
            continue
        key = (path, st.st_mtime_ns, st.st_size, max_lines)
        cached = _cache.get(key)
        if cached is None:
            cached = _parse_tail(path, st, max_lines)
            if len(_cache) >= _MAX_CACHE:
                _cache.clear()
            _cache[key] = cached
        return cached
    return {
        "found": False, "name": "", "path": "", "lines": [],
        "heartbeats": 0, "age_s": None, "snapshot": False,
        "note": _missing_note(mode, proxy_name),
    }


def _parse_tail(path: str, st: os.stat_result, max_lines: int) -> dict:
    """Last `max_lines` informative lines of `path`, read from the end."""
    start = max(0, st.st_size - _MAX_TAIL_BYTES)
    try:
        with open(path, "rb") as f:
            f.seek(start)
            blob = f.read()
    except OSError as e:
        return {"found": False, "name": os.path.basename(path), "path": path,
                "lines": [], "heartbeats": 0, "age_s": None, "note": f"unreadable ({e})"}

    text = blob.decode("utf-8", "replace")
    lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    if start > 0 and lines:
        lines.pop(0)      # first line may be cut mid-entry: it is not evidence
    heartbeats = 0
    informative: list[str] = []
    for ln in lines:
        if any(marker in ln for marker in _HEARTBEAT_MARKERS):
            heartbeats += 1
        else:
            informative.append(ln)
    return {
        "found": True,
        "name": os.path.basename(path),
        "path": path,
        "lines": informative[-max_lines:],
        "heartbeats": heartbeats,
        "age_s": max(0.0, time.time() - st.st_mtime),
        "snapshot": False,
        "note": "",
    }
