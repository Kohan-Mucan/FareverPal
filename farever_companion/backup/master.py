"""Master Backup manager for FareverPal user data.

Maintains rolling timestamped snapshots in master_backup.json.
Features:
- Fast (<1ms) timestamp check on app exit — only creates a snapshot if data changed.
- Compact list formatting for readable single-line arrays.
- Automatic data loss detection on startup (>20% drop in chests/orbs/pets or missing profile).
- 1-click full restore or surgical single-character restore.

Settings-intent protection (2026-09-19): open_overlays/geometry live inside
settings.json, so a stray writer that empties the overlay-restore list used to
be snapshotted as the newest "truth" — and the good pre-loss snapshot rotated
away five exits later (the 2026-09-19 incident: newest backup held
``open_overlays: []`` while the only good copy was two slots down). The
snapshot path still refuses degraded settings, measuring against the fullest
snapshot on record, and the OLDEST snapshot is pinned so the pre-loss copy can
never rotate away — one rule instead of the old state_guard.json safety file.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from ..config import config_dir, load_speedrun_history, write_speedrun_history
from ..core.speedrun import row_key
from ..runtime.persist import atomic_write_json

MAX_BACKUPS = 5
DEFAULT_LOSS_THRESHOLD_PCT = 20.0

# Settings keys whose loss the snapshot path refuses to record. Overlay
# intent (which HUDs restore at launch, and where they sat) is the part of
# settings.json whose loss reads to the user as "my settings turned off".
_SETTINGS_INTENT_KEYS = ("open_overlays", "geometry")  # noqa: F841 (documents the rule)


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

    # 5. Shared speedrun history (moddata/speedrun_history.json — bare list;
    #    _read_json_safe only accepts dicts, so this is read on its own).
    speedrun_history: list = []
    srh_path = cdir / "speedrun_history.json"
    if srh_path.exists():
        try:
            if srh_path.stat().st_size:
                raw = json.loads(srh_path.read_text(encoding="utf-8"))
                if isinstance(raw, list):
                    speedrun_history = [r for r in raw if isinstance(r, dict)]
        except (OSError, json.JSONDecodeError):
            pass
        file_timestamps["speedrun_history.json"] = datetime.fromtimestamp(
            srh_path.stat().st_mtime
        ).isoformat(timespec="seconds")

    state = {
        "settings": settings_data,
        "collection": col_data,
        "planner": planner_data,
        "profiles": profiles_data,
        "speedrun_history": speedrun_history,
    }
    return state, file_timestamps


# --- settings-intent reference (grow-only) ----------------------------------

def _settings_intent_counts(settings: dict) -> dict[str, int]:
    """How much overlay intent a settings dict holds."""
    return {
        "overlays": len(settings.get("open_overlays") or []),
        "geom": len(settings.get("geometry") or {}),
    }


def _settings_intent_total(settings: dict) -> int:
    c = _settings_intent_counts(settings)
    return c["overlays"] + c["geom"]


def _fullest_settings_intent(candidates: list[dict]) -> dict:
    """The settings dict holding the most overlay intent among candidates.
    Falls back to the first candidate (or {}) when none hold any."""
    best: dict | None = None
    best_n = -1
    for s in candidates:
        n = _settings_intent_total(s or {})
        if n > best_n:
            best, best_n = s or {}, n
    return best or {}


def _settings_intent_lossy(new: dict, ref: dict) -> bool:
    """True when ``new`` COLLAPSED to zero overlay intent against a reference
    that holds some.

    Deliberately not a ratio rule: open_overlays legitimately shrinks every
    time the user closes a HUD (5 -> 3 is normal use, not data loss), so only
    the wipe shape - every overlay and every saved position gone while the
    reference holds intent - counts as degradation. That is exactly what a
    defaults-instance/stray-writer save looks like (2026-09-19: five-overlay
    list -> ``open=[] geom=[]``)."""
    return _settings_intent_total(ref) > 0 and _settings_intent_total(new) == 0


def create_snapshot_if_changed(max_backups: int = MAX_BACKUPS) -> bool:
    """Create a new timestamped backup snapshot if data or timestamps changed.

    Executed on app exit. Fast check (<1ms). Returns True if a new snapshot was added.

    Settings-intent refusal: when the live settings hold no overlay intent
    while the fullest snapshot on record does, the snapshot records the
    REFERENCE instead of the degraded live state - a wipe must never become
    the newest "truth".
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
            or (state.get("speedrun_history") or [])
        )
        if not has_any_data:
            return False

        # Settings-intent refusal (before any comparison/snapshot): swap the
        # degraded live settings for the fullest snapshot's settings so the
        # wipe can never be recorded as the newest truth.
        try:
            stored = _fullest_settings_intent(
                [(b.get("settings") or {}) for b in backups])
            if _settings_intent_lossy(state["settings"], stored):
                state = {**state, "settings": stored}
        except Exception:
            pass  # the refusal must never break the snapshot path

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
                and (latest.get("speedrun_history") or [])
                == (state.get("speedrun_history") or [])
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
            "speedrun_history": state.get("speedrun_history") or [],
        }
        backups.insert(0, snapshot)
        # Keep the OLDEST snapshot pinned once the list is full: a healthy
        # pre-loss snapshot must never rotate away just because a lossy
        # session kept exiting (the incident that motivated the old
        # state_guard.json file — the same guarantee, one rule instead).
        # Rotation drops the OLDEST-adjacent middles: the list always holds
        # the newest state plus the oldest known-good copy.
        if len(backups) > max_backups and max_backups >= 2:
            backups = ([backups[0]]
                       + backups[1:-1][:max_backups - 2]
                       + [backups[-1]])

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


# --- data-loss detection ----------------------------------------------------
# Detection compares the live files against the HISTORICAL MAXIMA across every
# snapshot (the Kartik incident: the exit snapshot recorded the already-wiped
# state as the newest "truth" and masked the loss), and the oldest snapshot is
# pinned on rotation so a pre-loss copy always survives for recovery.

_PROFILE_DONE_FIELDS = ("poi_done", "dungeon_orb_done", "entity_hidden_units")


def _profile_done_counts(prof: dict) -> dict[str, int]:
    return {f: len(prof.get(f, []) or []) for f in _PROFILE_DONE_FIELDS}


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
    if not backups:
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

    # 4. Settings overlay intent (2026-09-19): the same comparison the
    #    snapshot path uses, surfaced at startup. The reference is the fullest
    #    snapshot's settings. Only overlay intent counts - ordinary toggles
    #    changing is not "settings turned off".
    ref_settings = _fullest_settings_intent(
        [(b.get("settings") or {}) for b in backups])
    if _settings_intent_total(ref_settings) > 0 \
            and _settings_intent_lossy(cur_state.get("settings") or {}, ref_settings):
        r = _settings_intent_counts(ref_settings)
        reasons.append(
            f"Settings lost their overlay restore list entirely (reference "
            f"holds {r['overlays']} overlays / {r['geom']} positions) - "
            f"restore from a backup snapshot; if you closed every HUD "
            f"deliberately, use Keep Current")

    if reasons:
        # Point recovery at the fullest snapshot: "Restore Missing Only" then
        # merges from a snapshot that actually holds the missing entries.
        best = max(backups, key=_snapshot_total) if backups else None
        affected = []
        for pname in cur_profiles.keys() | maxima.keys():
            if any(f"'{pname}'" in r for r in reasons):
                affected.append(pname)
        return {
            "has_loss": True,
            "reasons": reasons,
            "latest_snapshot": best,
            "backups": backups,
            "affected_profiles": sorted(affected),
        }
    return None


def dismiss_profile_alert(profile: str, purge_from_backups: bool = False) -> None:
    """Permanently dismiss data-loss alerts for a specific profile.

    1. Adds profile to ignored_profiles in master_backup.json so check_for_data_loss()
       never warns about it being missing again.
    2. If purge_from_backups is True, also deletes the profile from all historical snapshots.
    """
    try:
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
    - Marks any missing profiles that were in historical backups as ignored so
      they are never repeatedly flagged as missing.
    - Caps historical snapshot counts to the current count for any intentionally
      reduced profile on disk, permanently silencing count-drop warnings.
    - Caps the snapshots' settings intent to the current settings.json, so a
      user who deliberately closed every HUD is not warned about it again
      (the settings-intent reference IS the snapshots).
    """
    try:
        cur_state, _ = collect_moddata_state()
        cur_profiles = cur_state.get("profiles", {})
        cur_settings = cur_state.get("settings") or {}

        # Master backup: ignore missing profiles, normalize historical snapshots
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

        # A deliberate "Keep Current" also covers settings: cap the
        # snapshots' overlay intent down to the accepted state (the
        # settings-intent reference IS the snapshots), surgically on the two
        # intent fields so the rest of the snapshot's settings history is
        # untouched.
        for b in backups:
            b_settings = b.get("settings") or {}
            if _settings_intent_total(b_settings) > _settings_intent_total(cur_settings):
                b_settings["open_overlays"] = list(cur_settings.get("open_overlays") or [])
                b_settings["geometry"] = dict(cur_settings.get("geometry") or {})
                b["settings"] = b_settings

        master["ignored_profiles"] = sorted(ignored)
        atomic_write_json(master_backup_path(), master, compact_lists=True)
    except Exception:
        pass


def _sanitized_profile(prof_data: dict) -> dict:
    """Profile data with sub-second PB entries removed (MIN_PLAUSIBLE_SPLIT_S).

    A full restore replays the snapshot verbatim, and the snapshots on disk
    still carry the Run Timer's 00:00 records - restoring one would put a
    00:00.00 boss PB back in front of every real time. The import is deferred
    so a failure here can never block a restore; without it the snapshot is
    used exactly as it stands.
    """
    try:
        from ..core.speedrun import clean_best_dict
    except Exception:
        return dict(prof_data or {})
    out = dict(prof_data or {})
    for key in ("speedrun_best", "speedrun_boss_best"):
        if key not in out:
            continue
        cleaned = clean_best_dict(out[key])[0]
        if cleaned:
            out[key] = cleaned
        else:
            out.pop(key, None)      # an empty dict would shadow the global PB
    return out


def _sanitized_history(hist: list) -> list:
    """Kill-log rows with the sub-second 00:00 rows removed."""
    try:
        from ..core.speedrun import clean_kill_rows
        return clean_kill_rows(hist)[0]
    except Exception:
        return list(hist or [])


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
            atomic_write_json(path, _sanitized_profile(prof_data),
                              compact_lists=True)
            return True

        # Full restore
        if "settings" in snapshot and snapshot["settings"]:
            atomic_write_json(cdir / "settings.json", snapshot["settings"], compact_lists=True)

        if "collection" in snapshot and snapshot["collection"]:
            atomic_write_json(cdir / "collection.json", snapshot["collection"], compact_lists=True)

        if "planner" in snapshot and snapshot["planner"]:
            atomic_write_json(cdir / "planner.json", snapshot["planner"], compact_lists=True)

        for pname, pdata in snapshot.get("profiles", {}).items():
            atomic_write_json(cdir / f"progress_{pname}.json",
                              _sanitized_profile(pdata), compact_lists=True)

        # Bare JSON list — only when the snapshot carries the key (an older
        # snapshot without it must not wipe the live shared history). FULL
        # restore replays the snapshot raw (authoritative overwrite); the
        # additive, deduped path lives in restore_missing_from_snapshot.
        hist = snapshot.get("speedrun_history")
        if isinstance(hist, list):
            atomic_write_json(cdir / "speedrun_history.json",
                              _sanitized_history(hist), compact_lists=True)

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

    Settings are never touched (there is no additive meaning for them) - use
    the full restore for a settings-intent recovery.
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

    # 4. Shared speedrun history (bare list; additive by row identity) ----
    # Contrast with restore_from_snapshot's raw replay: this path MERGES, so a
    # run already present (same row_key — char/boss/at/full_ms) is never
    # double-counted. Dedupe identity lives in core.speedrun.row_key — the same
    # contract the config writer and the migration use.
    bak_hist = snapshot.get("speedrun_history")
    if isinstance(bak_hist, list) and bak_hist:
        cur_hist = load_speedrun_history()
        have = {row_key(r) for r in cur_hist if isinstance(r, dict)}
        missing = [r for r in bak_hist
                   if isinstance(r, dict) and row_key(r) not in have]
        if missing:
            write_speedrun_history(cur_hist + missing)
            notes.append(f"Run Timer history: restored {len(missing)} missing "
                         f"run(s)")

    return notes
