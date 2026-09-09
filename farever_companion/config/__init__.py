"""User settings + persistence, split by concern.

`model` owns the Settings dataclass, `profiles` the per-profile accessors it
inherits, `store` the paths, the audit trail and the history/collection helpers.
Every name importable from the old single module is re-exported here, so call
sites did not have to move (AGENTS.md's `config._PROP_MAP` included). No Qt
and no process reads: the only edges out are `data` (the collection sheet) and
two deferred borrows from `core` - a constant and a history merge - never `ui`.
"""
from __future__ import annotations

from .model import (
    Settings,
)
from .profiles import (
    _ProfileAccessors,
)
from .store import (
    ALL_GATHER_TYPES,
    OPEN_STATE_OVERLAYS,
    _AUDIT_MAX_LINES,
    _DICT_PROPS,
    _PROP_MAP,
    _adopt_legacy_frozen_dir,
    _audit_caller,
    _audit_path,
    _categorize_companion,
    _collection_path,
    _dev_dist_root,
    _group_settings_keys,
    _is_app_process,
    _merge_history,
    _migrate_speedrun_history,
    _purge_nonsense_pbs,
    _read_collection_disk,
    _restore_collection_from_backup,
    _settings_path,
    _shared_history_limit,
    _speedrun_history_path,
    _speedrun_migrated_dirs,
    _speedrun_purged_dirs,
    audit_settings_event,
    config_dir,
    dps_dir,
    experimental_enabled,
    find_backup_files,
    load_speedrun_history,
    write_speedrun_history,
)

__all__ = [
    "ALL_GATHER_TYPES",
    "OPEN_STATE_OVERLAYS",
    "Settings",
    "_AUDIT_MAX_LINES",
    "_DICT_PROPS",
    "_PROP_MAP",
    "_ProfileAccessors",
    "_adopt_legacy_frozen_dir",
    "_audit_caller",
    "_audit_path",
    "_categorize_companion",
    "_collection_path",
    "_dev_dist_root",
    "_group_settings_keys",
    "_is_app_process",
    "_merge_history",
    "_migrate_speedrun_history",
    "_purge_nonsense_pbs",
    "_read_collection_disk",
    "_restore_collection_from_backup",
    "_settings_path",
    "_shared_history_limit",
    "_speedrun_history_path",
    "_speedrun_migrated_dirs",
    "_speedrun_purged_dirs",
    "audit_settings_event",
    "config_dir",
    "dps_dir",
    "experimental_enabled",
    "find_backup_files",
    "load_speedrun_history",
    "write_speedrun_history",
]
