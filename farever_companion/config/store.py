"""Paths, the settings audit trail and the shared history/collection
helpers for user settings. The bottom of the config package: no Qt, no core
imports, nothing that depends on the dataclass or its accessors.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING

from ..runtime.persist import (
    atomic_write_json,
    backup_corrupt,
    find_backup_files as _find_backup_files,
)

if TYPE_CHECKING:          # annotation-only: model imports this module
    from .model import Settings


def _group_settings_keys(data: dict) -> dict:
    """Reorder top-level settings keys so related ones cluster together, with
    verbose custom tables like `server_custom_hosts` placed at the very bottom.
    """
    def grp(key: str) -> str:
        if key == "server_custom_hosts":
            return "zzzz"
        return key.split("_", 1)[0] if "_" in key else key
    return dict(sorted(data.items(), key=lambda kv: (grp(kv[0]), kv[0])))


def experimental_enabled() -> bool:
    """Dev/hidden gate for in-progress features kept OUT of release builds."""
    return os.environ.get("FAREVER_EXPERIMENTAL", "").strip().lower() not in (
        "", "0", "false", "no", "off")


# Overlay keys that persist an open-state flag in settings. tests/
# test_ui_utils.py (test_every_hud_overlay_has_persistence_mapping) pins
# this tuple against config._PROP_MAP so a new overlay key can't silently
# miss its persistence mapping.
OPEN_STATE_OVERLAYS = ("entity", "map", "dungeon", "speedrun", "dps",
                       "dummy")


def _is_app_process() -> bool:
    """True only for a frozen release build.

    Source-tree ``run.py`` launches are intentionally development launches and
    resolve to ``dist/dev-moddata`` just like tests and tools. The explicit
    ``FAREVER_MODDATA_DIR`` override remains the escape hatch for a caller
    that needs a private or otherwise selected folder.
    """
    import sys
    if getattr(sys, "frozen", False):
        return True
    return os.environ.get("FAREVER_DATA_FLAVOR", "").strip().lower() == "user"


def _dev_dist_root() -> Path:
    """<repo>/dist — one more level than the old single-file config.py had.

    The depth is load-bearing: it decides which folder a source-tree launch
    writes to, and tests/test_guards.py mirrors it with a stub file.
    """
    return Path(__file__).resolve().parents[2] / "dist"


def config_dir() -> Path:
    """The data folder this process reads and writes.

    Resolution order:
    1. FAREVER_MODDATA_DIR — explicit override for tests/tools that want a
       private folder. Wins over everything.
    2. A frozen release build uses ``<exe>/moddata``.
    3. A source-tree app launch, pytest, or another tooling process uses
       ``dist/dev-moddata``. The source launchers intentionally do not opt
       into the main ``dist/moddata`` folder.
    """
    override = os.environ.get("FAREVER_MODDATA_DIR", "").strip()
    if override:
        d = Path(override)
    else:
        import sys
        if getattr(sys, "frozen", False):
            d = Path(sys.executable).parent / "moddata"
            _adopt_legacy_frozen_dir(d.parent, d)
        elif _is_app_process():
            d = _dev_dist_root() / "moddata"
        else:
            d = _dev_dist_root() / "dev-moddata"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _adopt_legacy_frozen_dir(root: Path, flavor_dir: Path) -> None:
    """One-time migration: frozen builds before 2026-09-20 used a separate
    <exe>/moddata-frozen folder. If it exists and the exe has no data of its
    own yet, adopt it (rename) so a release user's settings/progress is not
    stranded after upgrading. Never raises — a failed adoption just means
    both folders exist and the exe runs with defaults.
    """
    legacy = root / "moddata-frozen"
    try:
        if not flavor_dir.exists() and legacy.is_dir() \
                and (legacy / "settings.json").exists():
            legacy.rename(flavor_dir)
    except OSError:
        pass


def dps_dir() -> Path:
    """Dedicated directory for combat & DPS history files (moddata/dps/)."""
    d = config_dir() / "dps"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _settings_path() -> Path:
    return config_dir() / "settings.json"


def _collection_path() -> Path:
    return config_dir() / "collection.json"


def _speedrun_history_path() -> Path:
    return config_dir() / "speedrun_history.json"


def _audit_path() -> Path:
    return config_dir() / "settings_audit.log"


_AUDIT_MAX_LINES = 300


def _audit_caller() -> str:
    """First stack frame outside this module (module.qualname:line) — the
    code path that triggered the write. Bounded walk, never raises."""
    try:
        import traceback
        here = __file__
        for frame in reversed(traceback.extract_stack()):
            fn = frame.filename or ""
            if fn == here:
                continue
            base = fn.replace("/", "\\").rsplit("\\", 1)[-1]
            return f"{base}:{frame.name}:{frame.lineno}"
    except Exception:
        pass
    return "?"


def audit_settings_event(event: str, inst: "Settings | None" = None,
                         detail: str = "") -> None:
    """Append an opt-in forensic line per settings save or abnormal load.

    The normal user-facing application writes diagnostics to run.bat's
    console, not into the user's moddata folder. Tests and developer sessions
    may opt in with ``FAREVER_SESSION_AUDIT=1`` when they need lifecycle
    diagnostics.

    The detailed format below is retained for opt-in developer diagnostics.
    """
    if os.environ.get("FAREVER_SESSION_AUDIT", "").strip().lower() not in (
            "1", "true", "yes"):
        return

    try:
        from datetime import datetime
        if inst is not None:
            state = (f"open={list(getattr(inst, 'open_overlays', []))} "
                     f"geom={sorted((getattr(inst, 'geometry', None) or {}).keys())} "
                     f"sidebar={list(getattr(inst, 'sidebar_elements', []))} "
                     f"el={len(getattr(inst, 'entity_layers', []) or [])} "
                     f"ml={len(getattr(inst, 'minimap_layers', []) or [])}")
        else:
            state = ""
        line = (f"{datetime.now().isoformat(timespec='seconds')} "
                f"pid={os.getpid()} {event} {state} "
                f"{detail} by={_audit_caller()}").rstrip() + "\n"
        p = _audit_path()
        lines: list[str] = []
        try:
            if p.exists():
                lines = p.read_text(encoding="utf-8").splitlines()
        except OSError:
            lines = []
        lines.append(line.rstrip("\n"))
        if len(lines) > _AUDIT_MAX_LINES:
            lines = lines[-_AUDIT_MAX_LINES:]
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception:
        pass


def find_backup_files() -> list[Path]:
    """Every *.bak file preserved in config_dir() — settings.json.bak,
    collection.json.bak, progress_*.json.bak — oldest first. Empty when none
    were preserved (fresh install or nothing ever corrupted).

    Thin wrapper over persist.find_backup_files() bound to the app's moddata
    folder; the primitive itself lives in .persist."""
    return _find_backup_files(config_dir())


# --- shared speedrun history (moddata/speedrun_history.json) -----------------
# One cross-character kill log for every character: the speedrun overlay
# appends to it and the PB History page reads it. PBs (speedrun_best /
# speedrun_boss_best) stay per-character in progress_*.json.

# Data folders whose legacy per-profile `speedrun_history` keys have already
# been folded in this process. Settings.load() is on the hot path (hero-name
# lookup), so the scan runs once per folder, not once per call. Keyed by
# folder so a test that retargets FAREVER_MODDATA_DIR still migrates its own
# tmp folder.
_speedrun_migrated_dirs: set[str] = set()


def _shared_history_limit() -> int:
    """Row cap for the shared file — deferred import keeps config core-free."""
    try:
        from ..core.speedrun import SHARED_HISTORY_LIMIT
        return int(SHARED_HISTORY_LIMIT)
    except Exception:
        return 500


def _merge_history(*groups) -> list | None:
    """Merge shared-history row groups via core.speedrun (deferred import
    keeps config core-free). None on an import failure means "don't touch the
    file" — a merge that silently returns [] would wipe history."""
    try:
        from ..core.speedrun import merge_history_rows
        return merge_history_rows(*groups)
    except Exception:
        return None


def _clean_history_rows(rows: list) -> list:
    """Kill-log rows with the sub-second 00:00 artifacts removed.

    The floor lives in core.speedrun (MIN_PLAUSIBLE_SPLIT_S); the import is
    deferred to keep config core-free, and on failure the rows pass through
    untouched - writing one odd row is better than refusing to write at all.
    """
    try:
        from ..core.speedrun import clean_kill_rows
        return clean_kill_rows(rows)[0]
    except Exception:
        return list(rows or [])


def load_speedrun_history() -> list:
    """Shared cross-character kill log, newest last (capped). Missing or
    corrupt file -> []. A corrupt file is preserved as *.bak, never clobbered.
    """
    p = _speedrun_history_path()
    try:
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, list):
                rows = [r for r in data if isinstance(r, dict)]
                return rows[-_shared_history_limit():]
            backup_corrupt(p)
    except (OSError, json.JSONDecodeError):
        backup_corrupt(p)
    return []


def write_speedrun_history(rows: list) -> None:
    """Replace the shared kill log (dedupe, newest last, capped, floored). A
    failed write keeps the previous file; the next save retries."""
    merged = _merge_history(_clean_history_rows(list(rows or [])))
    if merged is None:
        return
    try:
        atomic_write_json(_speedrun_history_path(), merged, compact_lists=True)
    except OSError:
        pass


# Data folders whose sub-second speedrun records have already been purged.
_speedrun_purged_dirs: set[str] = set()


def _purge_nonsense_pbs(inst) -> list[str]:
    """One-time: delete speedrun records too short to be a real fight.

    The Run Timer's 00:00 bug stored sub-second boss splits as personal bests
    (and the kill-log rows carrying them), so every real kill afterwards was
    measured against a 00:00.00 PB and read as "behind". The floor is enforced
    on every write now (see core.speedrun.MIN_PLAUSIBLE_SPLIT_S); this clears
    what earlier builds already wrote - every profile file, the shared kill log
    and the in-memory settings fallback the overlay reads.

    Idempotent, and once per data folder per process because Settings.load is
    on the hot path. Returns human-readable notes, like the other migrations.
    """
    folder = str(config_dir())
    if folder in _speedrun_purged_dirs:
        return []
    _speedrun_purged_dirs.add(folder)     # marked first: no retry storm
    try:
        from ..core.speedrun import clean_best_dict, clean_kill_rows
    except Exception:
        return []                         # core unavailable: keep, retry next launch

    notes: list[str] = []

    try:
        paths = sorted(config_dir().glob("progress_*.json"))
    except OSError:
        paths = []
    for path in paths:
        if path.name == "progress_default.json":
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        changed = False
        for key in ("speedrun_best", "speedrun_boss_best"):
            if key not in data:
                continue
            cleaned, dropped = clean_best_dict(data[key])
            if not dropped:
                continue
            notes.append(f"{path.name}: dropped {key} {', '.join(dropped)}")
            if cleaned:
                data[key] = cleaned
            else:
                data.pop(key, None)
            changed = True
        if changed:
            try:
                atomic_write_json(path, data, compact_lists=True)
            except OSError:
                pass

    shared = _speedrun_history_path()
    try:
        raw = json.loads(shared.read_text(encoding="utf-8")) if shared.exists() else None
    except (OSError, json.JSONDecodeError):
        raw = None
    if isinstance(raw, list):
        kept, dropped = clean_kill_rows(raw)
        if dropped:
            notes.append(f"speedrun_history.json: dropped {len(dropped)} "
                         "sub-second kill row(s)")
        # `kept != raw` covers both outcomes: whole rows dropped AND a row that
        # only lost its boss_ms (same length, different content).
        if kept != raw:
            try:
                atomic_write_json(shared, kept, compact_lists=True)
            except OSError:
                pass

    # The in-memory fallback used when no profile is known.
    for attr in ("speedrun_best", "speedrun_boss_best"):
        cleaned, dropped = clean_best_dict(getattr(inst, attr, None) or {})
        if dropped:
            notes.append(f"settings {attr}: dropped {', '.join(dropped)}")
            try:
                setattr(inst, attr, cleaned)
            except Exception:
                pass
    return notes


def _migrate_speedrun_history(inst: "Settings") -> None:
    """Fold legacy per-profile `speedrun_history` keys (and the old settings
    field) into moddata/speedrun_history.json, then strip them.

    One source of truth: the shared file is written FIRST, then each dirty
    profile is rewritten without the key — a crash between the two re-runs a
    merge that dedupes on row identity, so nothing double-counts. Rows from a
    profile get ``char`` stamped from the filename when missing. Once per data
    folder per process (see _speedrun_migrated_dirs).
    """
    folder = str(config_dir())
    if folder in _speedrun_migrated_dirs:
        return

    legacy = inst.speedrun_history
    legacy_had = bool(legacy)
    seeds: list = [dict(r) for r in (legacy or []) if isinstance(r, dict)]

    # Profile files that still carry the old key: (path, data-without-key).
    dirty: list[tuple[Path, dict]] = []
    try:
        paths = sorted(config_dir().glob("progress_*.json"))
    except OSError:
        paths = []
    for path in paths:
        if path.name == "progress_default.json":
            continue          # the entity migration above owns (and deletes) it
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue          # unreadable — leave it for backup_corrupt elsewhere
        if not isinstance(data, dict) or "speedrun_history" not in data:
            continue
        hist = data.pop("speedrun_history")
        if isinstance(hist, list) and hist:
            profile = path.stem[len("progress_"):]
            for r in hist:
                if isinstance(r, dict):
                    row = dict(r)
                    row.setdefault("char", profile)
                    seeds.append(row)
        dirty.append((path, data))

    if not seeds and not dirty and not legacy_had:
        _speedrun_migrated_dirs.add(folder)
        return                # already migrated — nothing left to fold or strip

    # Merge with whatever the shared file already holds (a prior crash may
    # have written it without stripping the profiles).
    if seeds:
        shared_path = _speedrun_history_path()
        existing: list = []
        if shared_path.exists():
            try:
                raw = json.loads(shared_path.read_text(encoding="utf-8"))
                if isinstance(raw, list):
                    existing = [r for r in raw if isinstance(r, dict)]
                else:
                    backup_corrupt(shared_path)
            except (OSError, json.JSONDecodeError):
                backup_corrupt(shared_path)
        merged = _merge_history(existing, seeds)
        if merged is None:
            return            # core import failed — keep legacy + profile keys for retry
        try:
            atomic_write_json(shared_path, merged, compact_lists=True)
        except OSError:
            return            # shared write failed: keep legacy + profile keys for retry

    # Shared file is durable (or there was nothing to put in it) — strip.
    for path, data in dirty:
        try:
            atomic_write_json(path, data, compact_lists=True)
        except OSError:
            pass
        profile = path.stem[len("progress_"):]
        cached = inst._profile_progress.get(profile)
        if isinstance(cached, dict):
            cached.pop("speedrun_history", None)   # never resurrect on save

    if legacy_had:
        inst.speedrun_history = []
        try:
            inst.save()       # strip the field from settings.json now
        except Exception:
            pass

    _speedrun_migrated_dirs.add(folder)


def _categorize_companion(uid: str) -> str:
    if uid.startswith("Mount_"):
        return "mounts"
    if uid.startswith("Glider_"):
        return "gliders"
    try:
        from ..data import collections as col
        for m in col.items("mounts"):
            if m["id"] == uid:
                return "mounts"
        for g in col.items("gliders"):
            if g["id"] == uid:
                return "gliders"
    except Exception:
        pass
    return "pets"


ALL_GATHER_TYPES: list[str] = [
    "lavendula", "madrigold", "zealotus", "ancientthyme",
    "copperore", "tinore", "tungstene",
]

# Dynamic attribute mapping: maps legacy boolean attribute names to their compact list container and key
_PROP_MAP: dict[str, tuple[str, str]] = {
    # Overlays
    "open_overlay_entity": ("open_overlays", "entity"),
    "open_overlay_map": ("open_overlays", "map"),
    "open_overlay_dungeon": ("open_overlays", "dungeon"),
    "open_overlay_speedrun": ("open_overlays", "speedrun"),
    "open_overlay_dps": ("open_overlays", "dps"),
    "open_overlay_dummy": ("open_overlays", "dummy"),
    "dps_bare": ("dps_options", "bare"),
    "dps_transparent": ("dps_options", "transparent"),
    # Test Dummy HUD chrome (Overlays card: Bare / Transparent).
    "dummy_bare": ("dummy_options", "bare"),
    "dummy_transparent": ("dummy_options", "transparent"),
    # Sidebar Elements
    "show_rift_box": ("sidebar_elements", "rift_box"),
    "show_launch_button": ("sidebar_elements", "launch_button"),
    "show_log": ("sidebar_elements", "log"),
    "show_devicons": ("sidebar_elements", "devicons"),
    # Entity HUD
    "show_enemies": ("entity_layers", "enemies"),
    "show_spark_mobs": ("entity_layers", "spark_mobs"),
    "show_chests": ("entity_layers", "chests"),
    "show_loot": ("entity_layers", "loot"),
    "show_gatherables": ("entity_layers", "gatherables"),
    "show_companions": ("entity_layers", "companions"),
    "show_orbs": ("entity_layers", "orbs"),
    "show_obelisks": ("entity_layers", "obelisks"),
    "show_compass": ("entity_layers", "compass"),
    "show_group_members": ("entity_layers", "group_members"),
    "entity_bare": ("entity_options", "bare"),
    "entity_transparent": ("entity_options", "transparent"),
    "entity_hide_collected": ("entity_options", "hide_collected"),
    "limit_by_zone": ("entity_options", "limit_by_zone"),
    "auto_select_next_collectible": ("entity_options", "auto_select_next_collectible"),
    "PlayerNames": ("entity_options", "player_names"),
    # Minimap
    "minimap_enemies": ("minimap_layers", "enemies"),
    "minimap_spark_mobs": ("minimap_layers", "spark_mobs"),
    "minimap_chests": ("minimap_layers", "chests"),
    "minimap_gatherables": ("minimap_layers", "gatherables"),
    "minimap_obelisks": ("minimap_layers", "obelisks"),
    "minimap_orbs": ("minimap_layers", "orbs"),
    "minimap_dungeons": ("minimap_layers", "dungeons"),
    "minimap_companions": ("minimap_layers", "companions"),
    "minimap_players": ("minimap_layers", "players"),
    "minimap_vendors": ("minimap_layers", "vendors"),
    "minimap_soulstones": ("minimap_layers", "soulstones"),
    "minimap_texture": ("minimap_options", "texture"),
    "minimap_icons": ("minimap_options", "icons"),
    "minimap_rotate": ("minimap_options", "rotate"),
    "minimap_bare": ("minimap_options", "bare"),
    "minimap_transparent": ("minimap_options", "transparent"),
    "minimap_limit_by_zone": ("minimap_options", "limit_by_zone"),
    "minimap_hide_collected": ("minimap_options", "hide_collected"),
    # Dungeon
    "dungeon_show_orbs": ("dungeon_layers", "orbs"),
    "dungeon_show_players": ("dungeon_layers", "players"),
    "dungeon_show_loots": ("dungeon_layers", "loots"),
    "rift_clone_track": ("dungeon_layers", "rift_clone_track"),
    "dungeon_bare": ("dungeon_options", "bare"),
    "dungeon_transparent": ("dungeon_options", "transparent"),
    "dungeon_hide_collected": ("dungeon_options", "hide_collected"),
    # Speedrun Options
    "speedrun_bare": ("speedrun_options", "bare"),
    "speedrun_transparent": ("speedrun_options", "transparent"),
    # Behavior Options
    "combat_click_through": ("app_options", "combat_click_through"),
    # Codex Options
    "codex_compact": ("codex_options", "compact"),
    "codex_pets_compact": ("codex_options", "pets_compact"),
    # App / Core Options
    "auto_attach": ("app_options", "auto_attach"),
    "click_through": ("app_options", "click_through"),
    "lock_overlays": ("app_options", "lock_overlays"),
    "auto_hide_menus": ("app_options", "auto_hide_menus"),
    "snap_mouse_to_player": ("app_options", "snap_mouse_to_player"),
    "disable_web_features": ("app_options", "disable_web_features"),
    "layer_link_master": ("app_options", "layer_link_master"),
}

_DICT_PROPS: dict[str, tuple[str, str, object]] = {
    "chest_count": ("entity_counts", "chests", 4),
    "companion_count": ("entity_counts", "companions", 2),
    "enemy_count": ("entity_counts", "enemies", 4),
    "gatherable_count": ("entity_counts", "gatherables", 4),
    "group_count": ("entity_counts", "group", 5),
    "orb_count": ("entity_counts", "orbs", 4),
}




def _read_collection_disk() -> dict:
    """Current collection.json split into pets/mounts/gliders lists.

    Returns empty lists on any read/parse failure — never raises. Companion
    toggles start from this FILE state (not from memory), so a stale or empty
    in-memory copy can never overwrite the only live copy of the data.
    """
    cp = _collection_path()
    try:
        data = json.loads(cp.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {
                "pets": sorted(set(data.get("pets", []) or [])),
                "mounts": sorted(set(data.get("mounts", []) or [])),
                "gliders": sorted(set(data.get("gliders", []) or [])),
            }
    except (OSError, json.JSONDecodeError):
        pass
    return {"pets": [], "mounts": [], "gliders": []}


def _restore_collection_from_backup(inst: "Settings") -> None:
    """Repopulate hidden-companion lists from the newest backup snapshot.

    Called only when collection.json was missing or unreadable at load time.
    Persists the recovered lists immediately so memory and disk agree before
    any later save() can run. Safe by construction: backups hold only data
    this app itself wrote, and a valid empty file never reaches this path.
    """
    try:
        from ..backup.master import load_master_backup
        backups = load_master_backup().get("backups", [])
        if not backups:
            return
        bak = backups[0].get("collection", {})
        pets = bak.get("pets", [])
        mounts = bak.get("mounts", [])
        gliders = bak.get("gliders", [])
        if not any((pets, mounts, gliders)):
            return
        inst.pet_hidden_units = sorted(set(pets))
        inst.mount_hidden_units = sorted(set(mounts))
        inst.glider_hidden_units = sorted(set(gliders))
        inst.save_collection()
    except Exception:
        pass

