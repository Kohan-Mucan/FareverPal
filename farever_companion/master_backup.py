"""Master Backup manager for FareverPal user data.

Maintains up to 5 rolling timestamped snapshots in master_backup.json.
Features:
- Fast (<1ms) timestamp check on app exit — only creates a snapshot if data changed.
- Compact list formatting for readable single-line arrays.
- Automatic data loss detection on startup (>20% drop in chests/orbs/pets or missing profile).
- 1-click full restore or surgical single-character restore.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import config_dir
from .persist import atomic_write_json

MAX_BACKUPS = 5
DEFAULT_LOSS_THRESHOLD_PCT = 20.0


def master_backup_path() -> Path:
    return config_dir() / "master_backup.json"


def load_master_backup() -> dict:
    p = master_backup_path()
    if not p.exists():
        return {"version": 1, "max_backups": MAX_BACKUPS, "backups": []}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("backups"), list):
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return {"version": 1, "max_backups": MAX_BACKUPS, "backups": []}


def _read_json_safe(path: Path) -> dict | None:
    if not path.exists() or path.stat().st_size == 0:
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def collect_moddata_state() -> tuple[dict[str, Any], dict[str, str]]:
    """Scan config_dir() and gather current user data + file modification timestamps.

    Returns (state_dict, file_timestamps).
    """
    cdir = config_dir()
    file_timestamps: dict[str, str] = {}
    
    # 1. Settings
    settings_path = cdir / "settings.json"
    settings_data = _read_json_safe(settings_path) or {}
    if settings_path.exists():
        file_timestamps["settings.json"] = datetime.fromtimestamp(
            settings_path.stat().st_mtime
        ).isoformat(timespec="seconds")

    # 2. Collection
    col_path = cdir / "collection.json"
    col_raw = _read_json_safe(col_path) or {}
    col_data = {
        "pets": sorted(set(col_raw.get("pets", []))),
        "mounts": sorted(set(col_raw.get("mounts", []))),
        "gliders": sorted(set(col_raw.get("gliders", []))),
    }
    if col_path.exists():
        file_timestamps["collection.json"] = datetime.fromtimestamp(
            col_path.stat().st_mtime
        ).isoformat(timespec="seconds")

    # 3. Planner
    planner_path = cdir / "planner.json"
    planner_raw = _read_json_safe(planner_path) or {}
    planner_data = {
        "farm": list(planner_raw.get("farm", [])),
        "craft_queue": dict(planner_raw.get("craft_queue", {})),
    }
    if planner_path.exists():
        file_timestamps["planner.json"] = datetime.fromtimestamp(
            planner_path.stat().st_mtime
        ).isoformat(timespec="seconds")

    # 4. Profiles (progress_*.json)
    profiles_data: dict[str, dict] = {}
    for p in sorted(cdir.glob("progress_*.json")):
        if p.name == "progress_default.json":
            continue
        pname = p.stem[len("progress_"):]
        raw = _read_json_safe(p)
        if raw is not None:
            prof_entry: dict[str, Any] = {}
            if "entity_hidden_units" in raw:
                prof_entry["entity_hidden_units"] = sorted(set(raw.get("entity_hidden_units", [])))
            if "poi_done" in raw:
                prof_entry["poi_done"] = sorted(set(raw.get("poi_done", [])))
            if "dungeon_orb_done" in raw:
                prof_entry["dungeon_orb_done"] = sorted(set(raw.get("dungeon_orb_done", [])))
            for best_key in ("dps_best", "speedrun_best", "speedrun_boss_best"):
                if best_key in raw and raw[best_key]:
                    prof_entry[best_key] = raw[best_key]
            profiles_data[pname] = prof_entry
            file_timestamps[p.name] = datetime.fromtimestamp(
                p.stat().st_mtime
            ).isoformat(timespec="seconds")

    state = {
        "settings": settings_data,
        "collection": col_data,
        "planner": planner_data,
        "profiles": profiles_data,
    }
    return state, file_timestamps


def create_snapshot_if_changed(max_backups: int = MAX_BACKUPS) -> bool:
    """Create a new timestamped backup snapshot if data or timestamps changed.

    Executed on app exit. Fast check (<1ms). Returns True if a new snapshot was added.
    """
    try:
        master = load_master_backup()
        backups: list[dict] = master.get("backups", [])
        
        state, file_timestamps = collect_moddata_state()
        
        # Don't backup if moddata is completely empty
        has_any_data = bool(
            state["settings"]
            or any(state["collection"].values())
            or state["planner"]["farm"]
            or state["profiles"]
        )
        if not has_any_data:
            return False

        if backups:
            latest = backups[0]
            latest_ts = latest.get("file_timestamps", {})
            # Deep equality of contents first: a file rewritten within the
            # same second (identical mtime) still counts as a change and must
            # snapshot - the timestamp-only fast path used to skip it.
            same_content = (
                latest.get("settings") == state["settings"]
                and latest.get("collection") == state["collection"]
                and latest.get("planner") == state["planner"]
                and latest.get("profiles") == state["profiles"]
            )
            if latest_ts == file_timestamps and same_content:
                return False
            if same_content:
                # Update timestamps only if content is identical
                latest["file_timestamps"] = file_timestamps
                atomic_write_json(master_backup_path(), {
                    "version": 1,
                    "max_backups": max_backups,
                    "backups": backups,
                }, compact_lists=True)
                return False

        now = datetime.now()
        snapshot = {
            "timestamp": now.isoformat(timespec="seconds"),
            "label": now.strftime("%Y-%m-%d %H:%M:%S"),
            "file_timestamps": file_timestamps,
            "settings": state["settings"],
            "collection": state["collection"],
            "planner": state["planner"],
            "profiles": state["profiles"],
        }
        backups.insert(0, snapshot)
        backups = backups[:max_backups]

        atomic_write_json(master_backup_path(), {
            "version": 1,
            "max_backups": max_backups,
            "backups": backups,
        }, compact_lists=True)
        return True
    except Exception:
        return False


def _profile_max_counts(backups: list[dict]) -> dict[str, dict[str, int]]:
    """Per-profile historical maxima (poi / orbs / hidden) across ALL
    snapshots. A snapshot written by a session that already lost data must
    not hide the loss - the newest snapshot can itself be the lossy one
    (e.g. progress_Kartik wiped mid-session, then the exit snapshot records
    the wiped state as the newest "truth")."""
    maxima: dict[str, dict[str, int]] = {}
    for b in backups:
        for pname, prof in (b.get("profiles") or {}).items():
            m = maxima.setdefault(pname, {"poi": 0, "orb": 0, "hidden": 0})
            m["poi"] = max(m["poi"], len(prof.get("poi_done", []) or []))
            m["orb"] = max(m["orb"], len(prof.get("dungeon_orb_done", []) or []))
            m["hidden"] = max(m["hidden"], len(prof.get("entity_hidden_units", []) or []))
    return maxima


def _snapshot_total(snapshot: dict) -> int:
    """How much tracked data a snapshot holds - used to pick the fullest
    snapshot as the restore source so "Restore Missing Only" merges from a
    snapshot that actually contains the missing entries."""
    col = snapshot.get("collection", {}) or {}
    n = sum(len(col.get(k, []) or []) for k in ("pets", "mounts", "gliders"))
    for prof in (snapshot.get("profiles") or {}).values():
        n += len(prof.get("poi_done", []) or [])
        n += len(prof.get("dungeon_orb_done", []) or [])
        n += len(prof.get("entity_hidden_units", []) or [])
    n += len((snapshot.get("planner") or {}).get("farm", []) or [])
    return n


# --- write-time data-loss guard --------------------------------------------
# master_backup.json rolls only 5 exit snapshots and can itself be overwritten
# by lossy ones (the Kartik incident: the exit snapshot recorded the already-
# wiped state as the newest "truth" and masked the loss). state_guard.json is
# a SEPARATE, never-rotating safety file that only ever receives GOOD state:
# the best-known profile (it grows on healthy writes) plus a preserved copy
# the moment a lossy overwrite is attempted. So even if every master snapshot
# rotates into lossy states, the last good state survives and startup can
# still report the loss. Written at SAVE time (before the overwrite lands),
# surfaced at startup through check_for_data_loss().

_GUARD_FILE = "state_guard.json"
_PROFILE_DONE_FIELDS = ("poi_done", "dungeon_orb_done", "entity_hidden_units")


def _guard_path() -> Path:
    return config_dir() / _GUARD_FILE


def _load_safety_guards() -> dict:
    """state_guard.json -> {"version": 1, "best": {}, "guards": {}}.
    Best = last known GOOD full profile dict per character; guards = preserved
    good state recorded when a lossy overwrite was attempted. Defensive load:
    a corrupt/missing safety file degrades to empty, never raises."""
    p = _guard_path()
    if not p.exists():
        return {"version": 1, "best": {}, "guards": {}}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data.setdefault("best", {})
            data.setdefault("guards", {})
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return {"version": 1, "best": {}, "guards": {}}


def _profile_done_counts(prof: dict) -> dict[str, int]:
    return {f: len(prof.get(f, []) or []) for f in _PROFILE_DONE_FIELDS}


def _fullest_backup_profile(pname: str, backups: list[dict]) -> dict | None:
    """The fullest profile dict for ``pname`` across all snapshots (restore
    source when the safety file has no reference yet). None if the profile
    never appears in any snapshot."""
    best: dict | None = None
    best_n = -1
    for b in backups:
        prof = (b.get("profiles") or {}).get(pname)
        if not prof:
            continue
        n = sum(_profile_done_counts(prof).values())
        if n > best_n:
            best, best_n = prof, n
    return best


def guard_profile_write(profile: str, data: dict) -> dict | None:
    """Write-time tripwire: called with the FULL profile dict just before it
    is written to progress_<profile>.json.

    - No reference yet (fresh install / first save of a character): seed
      ``best`` from this write and let it through.
    - Healthy write (every done-list keeps >= threshold% of the best known):
      update ``best`` when this write is fuller, clear any pending guard for
      the profile (it recovered), and let it through.
    - Lossy write (a done-list would drop below threshold% of the best
      known): preserve the last good state into state_guard.json and RETURN
      it. The write itself still proceeds - the preserved copy is what makes
      the wipe recoverable even if every exit snapshot records the lossy
      state afterward.

    Returns the preserved good-state dict when a lossy overwrite was
    attempted, else None. Never raises."""
    try:
        guards = _load_safety_guards()
        ratio = (100.0 - DEFAULT_LOSS_THRESHOLD_PCT) / 100.0

        best_entry = guards["best"].get(profile)
        if best_entry is None:
            best_entry = _fullest_backup_profile(
                profile, load_master_backup().get("backups", []))
        if best_entry is None:
            # first reference: this write becomes the best-known state
            guards["best"][profile] = dict(data)
            atomic_write_json(_guard_path(), guards)
            return None

        best_counts = _profile_done_counts(best_entry)
        new_counts = _profile_done_counts(data)
        lossy = any(
            best_counts[f] > 0 and new_counts[f] < ratio * best_counts[f]
            for f in _PROFILE_DONE_FIELDS)
        if lossy:
            guards["guards"][profile] = {
                "preserved": dict(best_entry),
                "attempted": new_counts,
                "best_counts": best_counts,
                "ts": datetime.now().isoformat(timespec="seconds"),
                "reason": (f"progress_{profile}.json was about to be "
                            f"overwritten with {new_counts} items vs the "
                            f"best-known {best_counts} (<{DEFAULT_LOSS_THRESHOLD_PCT:.0f}% "
                            f"kept) - the last good state was preserved here"),
            }
            if profile not in guards["best"]:
                # seed the safety file's own reference so it stays a complete
                # independent copy even if every master snapshot rotates away
                guards["best"][profile] = dict(best_entry)
            # best is NEVER downgraded to the lossy write
            atomic_write_json(_guard_path(), guards)
            return dict(best_entry)

        dirty = False
        if sum(new_counts.values()) > sum(best_counts.values()):
            guards["best"][profile] = dict(data)
            dirty = True
        if profile in guards["guards"]:
            # recovered (or restored): drop the pending alert
            del guards["guards"][profile]
            dirty = True
        if dirty:
            atomic_write_json(_guard_path(), guards)
        return None
    except Exception:
        # the guard must never break a save
        return None


def check_for_data_loss(threshold_pct: float = DEFAULT_LOSS_THRESHOLD_PCT) -> dict | None:
    """Check active moddata files against the historical backup snapshots.

    Each live dataset is compared against its BEST (largest) historical count
    across every snapshot, never just the newest one - a lossy snapshot
    written by a session that already lost data would otherwise mask the loss
    and the recovery dialog would never appear. Returns diagnostic dict if
    significant data loss (> threshold_pct) or missing files are detected,
    or None if healthy.
    """
    master = load_master_backup()
    backups = master.get("backups", [])
    ignored_profiles = set(master.get("ignored_profiles", []))
    guards = _load_safety_guards().get("guards", {})
    if not backups and not guards:
        return None

    cur_state, _ = collect_moddata_state()
    reasons: list[str] = []
    ratio = (100.0 - threshold_pct) / 100.0

    # 1. Check Collection (historical max across all snapshots)
    best_col_count = max(
        (sum(len((b.get("collection") or {}).get(k, []))
             for k in ("pets", "mounts", "gliders"))
        for b in backups
    )) if backups else 0
    cur_col = cur_state.get("collection", {})
    cur_col_count = sum(len(cur_col.get(k, [])) for k in ("pets", "mounts", "gliders"))
    if best_col_count > 0 and cur_col_count < ratio * best_col_count:
        loss_pct = int(((best_col_count - cur_col_count) / best_col_count) * 100)
        reasons.append(
            f"Collection lost {best_col_count - cur_col_count} items ({loss_pct}% drop: {cur_col_count}/{best_col_count} remaining)"
        )

    # 2. Check Profiles (per-profile historical maxima)
    maxima = _profile_max_counts(backups)
    cur_profiles = cur_state.get("profiles", {})
    for pname, m in maxima.items():
        if pname in ignored_profiles:
            continue
        cprof = cur_profiles.get(pname)
        if cprof is None:
            b_total = m["poi"] + m["orb"]
            if b_total > 0:
                reasons.append(f"Character profile '{pname}' is missing ({b_total} completed items lost)")
            continue

        c_poi = len(cprof.get("poi_done", []) or [])
        if m["poi"] > 0 and c_poi < ratio * m["poi"]:
            loss_pct = int(((m["poi"] - c_poi) / m["poi"]) * 100)
            reasons.append(
                f"'{pname}' POIs/chests dropped from {m['poi']} to {c_poi} ({loss_pct}% missing)"
            )

        c_orb = len(cprof.get("dungeon_orb_done", []) or [])
        if m["orb"] > 0 and c_orb < ratio * m["orb"]:
            loss_pct = int(((m["orb"] - c_orb) / m["orb"]) * 100)
            reasons.append(
                f"'{pname}' dungeon orbs dropped from {m['orb']} to {c_orb} ({loss_pct}% missing)"
            )

        c_hid = len(cprof.get("entity_hidden_units", []) or [])
        if m["hidden"] > 0 and c_hid < ratio * m["hidden"]:
            loss_pct = int(((m["hidden"] - c_hid) / m["hidden"]) * 100)
            reasons.append(
                f"'{pname}' hidden units dropped from {m['hidden']} to {c_hid} ({loss_pct}% missing)"
            )

    # 3. Check Planner (historical max farm list)
    best_farm = max(len((b.get("planner") or {}).get("farm", []) or []) for b in backups) if backups else 0
    cur_farm = len(cur_state.get("planner", {}).get("farm", []) or [])
    if best_farm > 0 and cur_farm < ratio * best_farm:
        reasons.append(f"Planner farm list dropped from {best_farm} to {cur_farm} items")

    # 4. Write-guard safety file: a lossy overwrite was attempted and the
    #    last good state was preserved in state_guard.json. Only report when
    #    the live file is STILL lossy vs the preserved state - a healthy save
    #    clears the guard, and a restored file simply compares equal.
    for pname, g in guards.items():
        if pname in ignored_profiles:
            continue
        pres = g.get("preserved") or {}
        cprof = cur_profiles.get(pname)
        if cprof is None:
            continue
        pres_counts = _profile_done_counts(pres)
        if any(
            pres_counts[f] > 0
            and len(cprof.get(f, []) or []) < ratio * pres_counts[f]
            for f in _PROFILE_DONE_FIELDS
        ):
            reasons.append(
                f"'{pname}' was being overwritten with fewer items - the "
                f"last good state is preserved in {_GUARD_FILE} "
                f"({pres_counts['poi_done']} POIs, "
                f"{pres_counts['dungeon_orb_done']} orbs)")

    if reasons:
        # Point recovery at the fullest snapshot: "Restore Missing Only" then
        # merges from a snapshot that actually holds the missing entries.
        best = max(backups, key=_snapshot_total) if backups else None
        affected = []
        for pname in cur_profiles.keys() | maxima.keys() | guards.keys():
            if any(f"'{pname}'" in r for r in reasons):
                affected.append(pname)
        return {
            "has_loss": True,
            "reasons": reasons,
            "latest_snapshot": best,
            "backups": backups,
            "guards": guards,
            "affected_profiles": sorted(affected),
        }
    return None


def clear_safety_guard(profile: str | None = None) -> None:
    """Clear pending alert guards from state_guard.json."""
    try:
        guards = _load_safety_guards()
        if profile is not None:
            if profile in guards.get("guards", {}):
                del guards["guards"][profile]
                atomic_write_json(_guard_path(), guards)
        else:
            if guards.get("guards"):
                guards["guards"] = {}
                atomic_write_json(_guard_path(), guards)
    except Exception:
        pass


def dismiss_profile_alert(profile: str, purge_from_backups: bool = False) -> None:
    """Permanently dismiss data-loss alerts for a specific profile.

    1. Removes any active guard for profile from state_guard.json.
    2. Resets state_guard.json['best'][profile] to current on-disk state.
    3. Adds profile to ignored_profiles in master_backup.json so check_for_data_loss()
       never warns about it being missing again.
    4. If purge_from_backups is True, also deletes the profile from all historical snapshots.
    """
    try:
        guards = _load_safety_guards()
        if profile in guards.get("guards", {}):
            del guards["guards"][profile]
        cur_prof = _read_json_safe(config_dir() / f"progress_{profile}.json")
        if cur_prof:
            guards["best"][profile] = dict(cur_prof)
        elif profile in guards.get("best", {}):
            del guards["best"][profile]
        atomic_write_json(_guard_path(), guards)

        master = load_master_backup()
        ignored = set(master.get("ignored_profiles", []))
        ignored.add(profile)
        master["ignored_profiles"] = sorted(ignored)

        if purge_from_backups:
            for b in master.get("backups", []):
                if "profiles" in b and profile in b["profiles"]:
                    del b["profiles"][profile]

        atomic_write_json(master_backup_path(), master, compact_lists=True)
    except Exception:
        pass


def accept_current_moddata_state() -> None:
    """Accept the active moddata files as the intended good state.

    Called when the user clicks 'Skip' or 'Keep Current' in BackupScanDialog:
    - Clears all pending write guards from state_guard.json.
    - Synchronizes state_guard.json['best'] with current on-disk profiles.
    - Marks any missing profiles that were in historical backups as ignored so
      they are never repeatedly flagged as missing.
    - Caps historical snapshot counts to the current count for any intentionally
      reduced profile on disk, permanently silencing count-drop warnings.
    """
    try:
        cur_state, _ = collect_moddata_state()
        cur_profiles = cur_state.get("profiles", {})

        # 1. Clear guards and update best
        guards = _load_safety_guards()
        guards["guards"] = {}
        new_best = {}
        for p, d in cur_profiles.items():
            new_best[p] = dict(d)
        guards["best"] = new_best
        atomic_write_json(_guard_path(), guards)

        # 2. Master backup: ignore missing profiles, normalize historical snapshots
        master = load_master_backup()
        backups = master.get("backups", [])
        maxima = _profile_max_counts(backups)
        ignored = set(master.get("ignored_profiles", []))

        for pname in maxima:
            if pname not in cur_profiles:
                ignored.add(pname)
            else:
                cur_counts = _profile_done_counts(cur_profiles[pname])
                for b in backups:
                    b_prof = (b.get("profiles") or {}).get(pname)
                    if b_prof:
                        for f in _PROFILE_DONE_FIELDS:
                            if len(b_prof.get(f, []) or []) > cur_counts[f]:
                                b_prof[f] = list(cur_profiles[pname].get(f, []) or [])

        master["ignored_profiles"] = sorted(ignored)
        atomic_write_json(master_backup_path(), master, compact_lists=True)
    except Exception:
        pass


def restore_from_snapshot(snapshot: dict, target_profile: str | None = None) -> bool:
    """Restore moddata files from a backup snapshot.

    If target_profile is specified, restores only that character's profile.
    Otherwise, performs a full restore of settings, collection, planner, and all profiles.
    """
    cdir = config_dir()
    cdir.mkdir(parents=True, exist_ok=True)
    
    try:
        if target_profile is not None:
            prof_data = snapshot.get("profiles", {}).get(target_profile)
            if prof_data is None:
                return False
            path = cdir / f"progress_{target_profile}.json"
            atomic_write_json(path, prof_data, compact_lists=True)
            return True

        # Full restore
        if "settings" in snapshot and snapshot["settings"]:
            atomic_write_json(cdir / "settings.json", snapshot["settings"], compact_lists=True)

        if "collection" in snapshot and snapshot["collection"]:
            atomic_write_json(cdir / "collection.json", snapshot["collection"], compact_lists=True)

        if "planner" in snapshot and snapshot["planner"]:
            atomic_write_json(cdir / "planner.json", snapshot["planner"], compact_lists=True)

        for pname, pdata in snapshot.get("profiles", {}).items():
            atomic_write_json(cdir / f"progress_{pname}.json", pdata, compact_lists=True)

        return True
    except OSError:
        return False


_PROFILE_MERGE_KEYS = ("poi_done", "dungeon_orb_done", "entity_hidden_units")
_COLLECTION_KEYS = ("pets", "mounts", "gliders")


def restore_missing_from_snapshot(snapshot: dict) -> list[str]:
    """Additive recovery: merge ONLY the entries missing from the live files.

    Unlike the full restore (which overwrites current data with the snapshot
    contents), this never removes or downgrades anything the live files
    already hold:
      * collection.json - adds pets/mounts/gliders that exist in the snapshot
        but not in the current file (e.g. the "collection lost N items" case),
      * profiles - recreates progress_<name>.json files that are missing
        entirely, and for existing files unions only the tracked per-character
        lists (poi_done / dungeon_orb_done / entity_hidden_units) into the
        FULL current file, leaving waypoints, bests and every other field
        untouched,
      * planner.json - unions farm items missing from the current list.

    Settings are never touched (there is no additive meaning for them).
    Returns human-readable lines describing what was restored; an empty list
    means nothing was missing (the live files already contain the backup's
    data - e.g. a transient false-positive loss check).
    """
    cdir = config_dir()
    notes: list[str] = []

    # 1. Collection ------------------------------------------------------
    col_path = cdir / "collection.json"
    cur_col = _read_json_safe(col_path) or {}
    bak_col = snapshot.get("collection") or {}
    new_col = dict(cur_col)
    for key in _COLLECTION_KEYS:
        have = set(cur_col.get(key, []) or [])
        missing = sorted(set(bak_col.get(key, []) or []) - have)
        if missing:
            new_col[key] = sorted(have | set(missing))
            preview = ", ".join(missing[:3])
            if len(missing) > 3:
                preview += "…"
            notes.append(f"Collection {key}: restored {len(missing)} missing "
                         f"({preview})")
    if new_col != cur_col:
        atomic_write_json(col_path, new_col, compact_lists=True)

    # 2. Planner ---------------------------------------------------------
    plan_path = cdir / "planner.json"
    cur_plan = _read_json_safe(plan_path) or {}
    bak_plan = snapshot.get("planner") or {}
    cur_farm = list(cur_plan.get("farm") or [])
    bak_farm = list(bak_plan.get("farm") or [])

    def _stable(x: Any) -> str:      # JSON-stable identity (farm items may be dicts)
        return json.dumps(x, sort_keys=True, ensure_ascii=False)

    have_farm = {_stable(x) for x in cur_farm}
    missing_farm = [x for x in bak_farm if _stable(x) not in have_farm]
    if missing_farm:
        new_plan = dict(cur_plan)
        new_plan["farm"] = cur_farm + missing_farm
        atomic_write_json(plan_path, new_plan, compact_lists=True)
        notes.append(f"Planner farm: restored {len(missing_farm)} missing item(s)")

    # 3. Profiles --------------------------------------------------------
    for pname, bprof in (snapshot.get("profiles") or {}).items():
        path = cdir / f"progress_{pname}.json"
        raw = _read_json_safe(path)
        if raw is None:
            # File is missing entirely - recreate it from the snapshot view.
            atomic_write_json(path, dict(bprof), compact_lists=True)
            n = sum(len(bprof.get(k, []) or []) for k in _PROFILE_MERGE_KEYS)
            notes.append(f"Profile '{pname}' was missing - recreated "
                         f"({n} tracked item(s) from the backup)")
            continue
        merged = dict(raw)
        for key in _PROFILE_MERGE_KEYS:
            bset = set(bprof.get(key, []) or [])
            if not bset:
                continue
            cset = set(raw.get(key, []) or [])
            add = sorted(bset - cset)
            if add:
                merged[key] = sorted(cset | bset)
        if merged != raw:
            atomic_write_json(path, merged, compact_lists=True)
            add_all = sum(
                len(set(bprof.get(k, []) or []) - set(raw.get(k, []) or []))
                for k in _PROFILE_MERGE_KEYS)
            notes.append(f"Profile '{pname}': merged {add_all} missing tracked "
                         f"item(s) (everything current preserved)")

    return notes
