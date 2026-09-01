"""User settings + persistence.

Stored as JSON inside moddata/ folder next to the app executable (or dist/moddata in dev mode).
No core/Qt imports here (pure data) so any layer can read it. The crash-safe
write/backup primitives live in .persist and every writer (settings,
collection, profiles, and the fields they carry — dps bests, server pings,
geometry) funnels through them.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict, fields, MISSING, is_dataclass
from pathlib import Path

from .persist import (
    atomic_write_json,
    backup_corrupt,
    find_backup_files as _find_backup_files,
)


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


def config_dir() -> Path:
    # Tooling/tests can point the app at a throwaway config dir so smoke runs
    # never touch the real user data (dist/moddata or the frozen moddata).
    override = os.environ.get("FAREVER_MODDATA_DIR", "").strip()
    if override:
        d = Path(override)
    else:
        import sys
        if getattr(sys, "frozen", False):
            d = Path(sys.executable).parent / "moddata"
        else:
            d = Path(__file__).resolve().parent.parent / "dist" / "moddata"
    d.mkdir(parents=True, exist_ok=True)
    return d


def dps_dir() -> Path:
    """Dedicated directory for combat & DPS history files (moddata/dps/)."""
    d = config_dir() / "dps"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _settings_path() -> Path:
    return config_dir() / "settings.json"


def _collection_path() -> Path:
    return config_dir() / "collection.json"


def find_backup_files() -> list[Path]:
    """Every *.bak file preserved in config_dir() — settings.json.bak,
    collection.json.bak, progress_*.json.bak — oldest first. Empty when none
    were preserved (fresh install or nothing ever corrupted).

    Thin wrapper over persist.find_backup_files() bound to the app's moddata
    folder; the primitive itself lives in .persist."""
    return _find_backup_files(config_dir())


def _categorize_companion(uid: str) -> str:
    if uid.startswith("Mount_"):
        return "mounts"
    if uid.startswith("Glider_"):
        return "gliders"
    try:
        from .data import collections as col
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
    "open_overlay_combat": ("open_overlays", "combat"),
    "dps_bare": ("dps_options", "bare"),
    "dps_transparent": ("dps_options", "transparent"),
    # Sidebar Elements
    "show_rift_box": ("sidebar_elements", "rift_box"),
    "show_launch_button": ("sidebar_elements", "launch_button"),
    "show_account_button": ("sidebar_elements", "account_button"),
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
    "speedrun_auto": ("speedrun_options", "auto"),
    "speedrun_auto_rearm": ("speedrun_options", "auto_rearm"),
    "speedrun_auto_upload": ("speedrun_options", "auto_upload"),
    "speedrun_bare": ("speedrun_options", "bare"),
    "speedrun_transparent": ("speedrun_options", "transparent"),
    "speedrun_build_override_on": ("speedrun_options", "build_override_on"),
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
    "share_presence": ("app_options", "share_presence"),
    "disable_web_features": ("app_options", "disable_web_features"),
    "disable_speedrun_upload": ("app_options", "disable_speedrun_upload"),
    "layer_link_master": ("app_options", "layer_link_master"),
}

_DICT_PROPS: dict[str, tuple[str, str, object]] = {
    "chest_count": ("entity_counts", "chests", 4),
    "companion_count": ("entity_counts", "companions", 2),
    "enemy_count": ("entity_counts", "enemies", 4),
    "gatherable_count": ("entity_counts", "gatherables", 4),
    "group_count": ("entity_counts", "group", 5),
    "orb_count": ("entity_counts", "orbs", 4),
    "hotkey_speedrun_toggle": ("hotkeys", "speedrun_toggle", "Ctrl+Alt+T"),
    "hotkey_speedrun_reset": ("hotkeys", "speedrun_reset", "Ctrl+Alt+R"),
    "account_name": ("account", "name", ""),
    "account_code": ("account", "code", ""),
    "account_token": ("account", "token", ""),
    "account_avatar": ("account", "avatar", ""),
    "api_base": ("account", "api_base", "https://farever-pals.com"),
}


@dataclass
class Settings:
    # ===================== App / Core =====================
    app_options: list = field(default_factory=lambda: ["auto_attach", "auto_hide_menus", "snap_mouse_to_player", "share_presence"])
    ui_scale: float = 1.0
    opacity: float = 0.9
    hud_accent: str = "#38bdf8"
    icon_size: int = 28
    mouse_snap_rate: int = 500
    geometry: dict = field(default_factory=dict)
    # Sidebar extras and pinned shortcuts — compact list of active items only.
    sidebar_elements: list = field(default_factory=lambda: ["rift_box", "launch_button"])

    # ===================== Codex =====================
    codex_options: list = field(default_factory=lambda: ["pets_compact"])

    # ===================== Entity Overlay (HUD) =====================
    entity_layers: list = field(default_factory=lambda: ["chests", "loot", "companions", "orbs", "obelisks", "group_members"])
    entity_options: list = field(default_factory=lambda: ["hide_collected", "auto_select_next_collectible", "limit_by_zone"])
    entity_counts: dict = field(default_factory=lambda: {"chests": 4, "companions": 2, "enemies": 4, "gatherables": 4, "group": 5, "orbs": 4})
    loot_filter: str = "Rare+"           # loot rarity floor (Off / All / Uncommon+ / Rare+ / Epic+ / Legendary)
    show_companions_debug: bool | int = False
    max_dist: float = 0.0
    entity_width: int = 350
    entity_font_size: int = 15
    # Enabled gatherable type keys (see ALL_GATHER_TYPES). Defaults to all.
    show_gatherable_types: list = field(default_factory=lambda: list(ALL_GATHER_TYPES))
    # Targeting
    track_kind: str = ""
    track_id: str = ""

    # ===================== Minimap =====================
    minimap_layers: list = field(default_factory=lambda: ["spark_mobs", "chests", "obelisks", "orbs", "dungeons", "companions", "players", "soulstones"])
    minimap_options: list = field(default_factory=lambda: ["texture", "icons"])
    minimap_icon_size: int = 24
    minimap_size: int = 400
    minimap_zoom: float = 8.0
    minimap_shape: str = "Square"

    # ===================== Character =====================
    player_class: str = "Auto"
    level: int = 25

    # ===================== Rift =====================
    show_rift_timer: str = "Always"
    rift_ding: list = field(default_factory=lambda: ["15", "10", "5", "1"])
    rift_sound: str = "ding"

    # ===================== Dungeon =====================
    dungeon_layers: list = field(default_factory=lambda: ["orbs", "players", "loots", "rift_clone_track"])
    dungeon_options: list = field(default_factory=lambda: ["hide_collected"])
    dungeon_loot_filter: str = "Rare+"   # loot rarity floor: Off / All / Uncommon+ / Rare+ / Epic+ / Legendary

    # ===================== Speedrun =====================
    speedrun_options: list = field(default_factory=lambda: ["auto_rearm"])
    speedrun_mode: str = "hard"
    speedrun_scale: float = 1.0
    speedrun_build_override: str = ""
    speedrun_corunners: list = field(default_factory=list)

    # ===================== DPS =====================
    dps_options: list = field(default_factory=list)
    dps_max_dist: float = 400.0          # 0 = zone/unlimited; 400m default for dungeon/rift
    dps_view: str = "damage"             # "damage", "healing", "both"
    dps_top_count: int = 8               # default top 8 DPS players
    heals_top_count: int = 2             # default top 2 healers
    # Solo-only meter view: while ungrouped in the open world, the DPS meter
    # and Combat page show the local player's parse only. Party members
    # always show when grouped, and dungeons/rifts always show everyone.
    dps_solo_only: bool = True
    # Located DamageDisplay / DamageResult class pointers, cached after the
    # first successful calibration so an app restart against the same running
    # game skips the multi-minute type scan. Each is class-name-verified on
    # load (find_type / find_result_type) and re-scanned + re-persisted when
    # the game relaunches and the saved address goes stale.
    dmg_display_type: str = ""           # hex st.ui.DamageDisplay hl_type ptr
    dmg_result_type: str = ""            # hex st.skill.DamageResult hl_type ptr
    dps_mode: str = "injector"          # "proxy" (Option 1) | "injector" (Option 2) | "memory" (Option 3)
    dps_proxy_dll: str = "version.dll"   # "version.dll" (default, conflict-free) | "dinput8.dll"
    dps_hook_enabled: bool = True        # legacy mirror: True only when dps_mode == "injector"
    dps_hp_est: bool = False             # HP-diff fallback: estimate damage from enemy HP drops when the event source is silent (off by default; real event sources are the norm)
    farever_game_dir: str = ""           # detected or user-specified path to Farever game folder

    # ===================== Layers / World (Settings → Layers) =====================
    layer_links: list = field(default_factory=list)  # per-row linked layer keys

    # ===================== Overlay Open States =====================
    open_overlays: list = field(default_factory=lambda: ["entity", "map", "dungeon"])

    # ===================== Hotkeys =====================
    hotkeys: dict = field(default_factory=lambda: {"speedrun_toggle": "Ctrl+Alt+T", "speedrun_reset": "Ctrl+Alt+R"})

    # ===================== Web & Account =====================
    account: dict = field(default_factory=lambda: {"api_base": "https://farever-pals.com"})

    # ===================== Pages (Settings → Pages) =====================
    # Nav keys the user turned off: removed from the sidebar and never built.
    disabled_pages: list = field(default_factory=list)

    # ===================== Server / Pings =====================
    server_pings: list = field(default_factory=list)  # enabled region codes only
    server_custom_hosts: dict = field(default_factory=dict)

    # ===================== Caches / Heavy Data =====================
    dps_best: dict = field(default_factory=dict)
    speedrun_best: dict = field(default_factory=dict)
    speedrun_boss_best: dict = field(default_factory=dict)
    poi_done: list = field(default_factory=list)
    dungeon_orb_done: list = field(default_factory=list)
    entity_hidden_units: list = field(default_factory=list)
    pet_hidden_units: list = field(default_factory=list)
    mount_hidden_units: list = field(default_factory=list)
    glider_hidden_units: list = field(default_factory=list)
    # Fingerprint of the last state written to settings.json (None = never
    # written). save() compares against this and skips the disk write when
    # nothing actually changed — overlay timers / drag events / repeated UI
    # refreshes must not rewrite the file every time they fire.
    _saved_sig: str | None = field(default=None, init=False, repr=False)

    @property
    def companion_hidden_units(self) -> list[str]:
        return sorted(set(self.pet_hidden_units + self.mount_hidden_units + self.glider_hidden_units))

    @companion_hidden_units.setter
    def companion_hidden_units(self, val: list[str]) -> None:
        p_list, m_list, g_list = [], [], []
        for u in (val or []):
            cat = _categorize_companion(u)
            if cat == "mounts": m_list.append(u)
            elif cat == "gliders": g_list.append(u)
            else: p_list.append(u)
        self.pet_hidden_units = sorted(set(p_list))
        self.mount_hidden_units = sorted(set(m_list))
        self.glider_hidden_units = sorted(set(g_list))

    def __getattr__(self, name: str):
        if name in _PROP_MAP:
            target_list, key = _PROP_MAP[name]
            return key in getattr(self, target_list, [])
        if name in _DICT_PROPS:
            target_dict, key, default_val = _DICT_PROPS[name]
            d = getattr(self, target_dict, None)
            if isinstance(d, dict):
                return d.get(key, default_val)
            return default_val
        raise AttributeError(f"'Settings' object has no attribute '{name}'")

    def __setattr__(self, name: str, value):
        if name in _PROP_MAP:
            target_list, key = _PROP_MAP[name]
            lst = getattr(self, target_list, None)
            if lst is None:
                lst = []
                super().__setattr__(target_list, lst)
            s = set(lst)
            (s.add if bool(value) else s.discard)(key)
            super().__setattr__(target_list, sorted(s))
            return
        if name in _DICT_PROPS:
            target_dict, key, default_val = _DICT_PROPS[name]
            d = getattr(self, target_dict, None)
            if not isinstance(d, dict):
                d = {}
                super().__setattr__(target_dict, d)
            d[key] = value
            return
        super().__setattr__(name, value)

    def __post_init__(self):
        # Runtime-only cache of loaded profile progress lists
        self._profile_progress: dict[str, dict] = {}
        self._saved_sig: str | None = None

    @classmethod
    def load(cls) -> "Settings":
        p = _settings_path()
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            # A corrupt/unreadable file must never be silently overwritten by
            # the next save(): move it aside so it survives for recovery.
            backup_corrupt(p)
            data = {}
        known = {f.name for f in fields(cls)}
        inst = cls(**{k: v for k, v in data.items() if k in known})

        # Load collection data from collection.json (separate from settings.json)
        coll_path = _collection_path()
        coll_read_ok = False
        if coll_path.exists():
            try:
                coll_data = json.loads(coll_path.read_text(encoding="utf-8"))
                if isinstance(coll_data, dict):
                    inst.pet_hidden_units = coll_data.get("pets", [])
                    inst.mount_hidden_units = coll_data.get("mounts", [])
                    inst.glider_hidden_units = coll_data.get("gliders", [])
                    coll_read_ok = True
            except (OSError, json.JSONDecodeError):
                # preserve a corrupt file instead of clobbering it on save
                backup_corrupt(coll_path)
        if not coll_read_ok:
            # collection.json is MISSING or CORRUPT — never start with empty
            # lists: the very first save() would rewrite an empty file and
            # the wipe would stick (the load-empty -> save-empty loop that
            # made losses recur). Self-heal from the newest master backup
            # when it holds companion data. A valid-but-empty file (user
            # legitimately unhid everything) reads OK and is never touched.
            _restore_collection_from_backup(inst)

        # Migrate the old no-profile fallback file: hidden mobs toggled while
        # no character was attached used to live in progress_default.json.
        # Fold them into the global settings list and remove the file —
        # profile data is per-character only, collection is global.
        try:
            def_path = config_dir() / "progress_default.json"
            if def_path.exists():
                def_data = json.loads(def_path.read_text(encoding="utf-8"))
                merged = sorted(set(inst.entity_hidden_units)
                                | set(def_data.get("entity_hidden_units", [])))
                if merged != inst.entity_hidden_units:
                    inst.entity_hidden_units = merged
                inst.save()          # persist before deleting so nothing is lost
                def_path.unlink()
        except (OSError, json.JSONDecodeError):
            pass
        # Baseline fingerprint = cleaned state as it stands right after load,
        # so an unchanged session never rewrites settings.json on its first
        # save() (the migration above may already have written).
        try:
            inst._saved_sig = json.dumps(inst._serialized_settings(),
                                         sort_keys=True, ensure_ascii=False)
        except Exception:
            inst._saved_sig = None
        return inst

    def save_collection(self) -> None:
        """Write the in-memory companion lists to collection.json.

        Used by the backup self-heal to persist recovered state; companion
        toggles use a disk-merged write instead of this wholesale dump.
        """
        try:
            data = {
                "pets": sorted(set(self.pet_hidden_units)),
                "mounts": sorted(set(self.mount_hidden_units)),
                "gliders": sorted(set(self.glider_hidden_units))
            }
            # Wipe guard: an all-empty dump must never clobber a populated
            # collection.json (the only live copy), no matter the caller.
            if not any(data.values()):
                existing = {}
                cp = _collection_path()
                if cp.exists():
                    try:
                        existing = json.loads(cp.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError):
                        existing = {}
                if any(existing.get(k, []) for k in ("pets", "mounts", "gliders")):
                    return
            atomic_write_json(_collection_path(), data, compact_lists=True)
        except OSError:
            pass

    def save(self) -> None:
        """Persist settings.json ONLY when something actually changed.

        The cleaned in-memory state is fingerprinted against the last written
        state; unchanged saves (overlay timers, drag events, repeated UI
        refreshes) skip the disk write entirely.

        collection.json is deliberately NEVER written here. Companion hidden
        state persists only through toggle_companion_hidden(), which applies
        a disk-merged read-modify-write — so a settings save can never
        rewrite — or wipe — the collection from an in-memory copy.
        """
        serialized = self._serialized_settings()
        sig = json.dumps(serialized, sort_keys=True, ensure_ascii=False)
        if sig == self._saved_sig:
            return                       # nothing changed — no disk write
        try:
            atomic_write_json(_settings_path(),
                              _group_settings_keys(serialized),
                              compact_lists=True)
        except OSError:
            return                       # keep the old sig so we retry later
        self._saved_sig = sig

    def _serialized_settings(self) -> dict:
        """Current settings as a cleaned dict (drops runtime/legacy keys).

        Pure: does not mutate the instance, safe to call for fingerprinting.
        """
        serialized = {k: v for k, v in asdict(self).items()
                      if not k.startswith("_")}
        # Clean up server_pings if it was ever set as a dict
        if isinstance(self.server_pings, dict):
            serialized["server_pings"] = [k for k, v in self.server_pings.items() if v]
        # Keep settings.json clean by removing collection lists and per-character profile caches
        serialized.pop("pet_hidden_units", None)
        serialized.pop("mount_hidden_units", None)
        serialized.pop("glider_hidden_units", None)
        serialized.pop("companion_hidden_units", None)
        serialized.pop("dps_best", None)
        serialized.pop("speedrun_best", None)
        serialized.pop("speedrun_boss_best", None)
        serialized.pop("poi_done", None)
        serialized.pop("dungeon_orb_done", None)
        serialized.pop("entity_hidden_units", None)
        if not serialized.get("speedrun_corunners"):
            serialized.pop("speedrun_corunners", None)
        if not serialized.get("server_custom_hosts"):
            serialized.pop("server_custom_hosts", None)
        if not serialized.get("geometry"):
            serialized.pop("geometry", None)
        # Runtime tracking state is in-memory only — never persist to disk
        serialized.pop("track_kind", None)
        serialized.pop("track_id", None)
        # Remove empty strings/lists that don't need to be persisted when empty
        for k in ("account_name", "account_code", "account_token", "account_avatar",
                  "speedrun_build_override", "show_companions_debug",
                  "dmg_display_type", "dmg_result_type"):
            if not serialized.get(k):
                serialized.pop(k, None)
        for k in ("combat_options", "disabled_pages", "layer_links", "server_pings"):
            if not serialized.get(k):
                serialized.pop(k, None)
        return serialized

    # --- profile data helpers -------------------------------------------
    def _ensure_profile_loaded(self, profile: str) -> None:
        if profile not in self._profile_progress:
            path = config_dir() / f"progress_{profile}.json"
            if path.exists():
                try:
                    self._profile_progress[profile] = json.loads(path.read_text(encoding="utf-8"))
                except Exception:
                    # never silently overwrite a corrupt per-character file
                    backup_corrupt(path)
                    self._profile_progress[profile] = {}
            else:
                self._profile_progress[profile] = {}

    def _fresh_profile(self, profile: str) -> dict | None:
        """Current on-disk profile dict (FULL), or None when missing/unreadable.
        Writers that must never clobber data from partial memory use this as
        their base: the file is the truth, the memory cache is only a speed-up
        (a second instance / fresh Settings / a failed load must not overwrite
        a populated file with whatever happened to be cached)."""
        path = config_dir() / f"progress_{profile}.json"
        try:
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
        except (OSError, json.JSONDecodeError):
            pass
        return None

    def save_profile_data(self, profile: str) -> None:
        try:
            path = config_dir() / f"progress_{profile}.json"
            raw = self._profile_progress.get(profile, {})
            if not raw:
                # A writer with NO in-memory reference (fresh/second instance
                # or a failed load) must never clobber a populated file with a
                # one-key/empty write: seed the base from the CURRENT file so
                # the write lands on top of the real data.
                fresh = self._fresh_profile(profile)
                if fresh:
                    raw = fresh
                    self._profile_progress[profile] = fresh
            # Clean, deduplicate and compact progress data
            data = {}
            for k, v in raw.items():
                if isinstance(v, list):
                    if v:
                        data[k] = sorted(set(v))
                elif isinstance(v, dict):
                    if v:
                        data[k] = v
                elif v:
                    data[k] = v
            # Write-time loss guard: if this save would drop a done-list below
            # 80% of the best-known state, the last good state is preserved
            # into state_guard.json BEFORE the overwrite lands (the rolling
            # master backups can be masked by a lossy exit snapshot - the
            # safety file cannot). Never blocks the save itself.
            from .master_backup import guard_profile_write
            guard_profile_write(profile, data)
            atomic_write_json(path, data, compact_lists=True)
        except OSError:
            pass

    def save_profile_progress(self, profile: str, done_list: list[str]) -> None:
        # Disk-authoritative base: the done_list may be fresh while the cache
        # is stale - writing onto the cached dict would drag the OTHER keys
        # (orbs / hidden) back to an older state. The file is the truth.
        base = self._fresh_profile(profile)
        if base is None:
            base = dict(self._profile_progress.get(profile, {}))
        base["poi_done"] = list(done_list)
        self._profile_progress[profile] = base
        self.save_profile_data(profile)

    def get_poi_done_authoritative(self, profile: str | None = None) -> list[str]:
        """Disk-authoritative poi_done for the sync writers (orb/entity ticks).

        Re-reads progress_<profile>.json FRESH so a second instance or a
        fresh Settings object can never build a sync on stale/empty in-memory
        state: the file is the truth, the memory cache is only a speed-up.
        Refreshes the whole cached profile dict too, so a save right after
        keeps the other done-lists (orbs / hidden) intact. Falls back to the
        cache when the file is missing or unreadable.
        """
        if not profile:
            return self.poi_done
        path = config_dir() / f"progress_{profile}.json"
        try:
            if path.exists():
                disk = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(disk, dict):
                    self._profile_progress[profile] = disk
                    return disk.setdefault("poi_done", [])
        except (OSError, json.JSONDecodeError):
            pass
        self._ensure_profile_loaded(profile)
        return self._profile_progress[profile].setdefault("poi_done", [])

    def sync_done_list_safe(self, profile: str | None, done_list: list) -> bool:
        """False when the about-to-be-synced done-list is EMPTY while the
        on-disk profile file still holds done data - the partial-memory wipe
        signature (a second instance / fresh Settings that never loaded the
        profile must not wipe a populated file by syncing an empty base).
        True for missing/empty files and for any non-empty write. The >20%
        write-time guard in save_profile_data covers the partial-but-not-
        empty variants on top of this."""
        if not profile:
            return True
        path = config_dir() / f"progress_{profile}.json"
        try:
            if path.exists():
                disk = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(disk, dict) and disk.get("poi_done"):
                    return bool(done_list)
        except (OSError, json.JSONDecodeError):
            pass
        return True

    def get_dps_best(self, profile: str | None = None) -> dict:
        if not profile:
            return self.dps_best
        self._ensure_profile_loaded(profile)
        prof_best = self._profile_progress[profile].get("dps_best")
        if prof_best is not None:
            return prof_best
        return self.dps_best

    def save_dps_best(self, profile: str | None, dps_best: dict) -> None:
        if not profile:
            self.dps_best = dps_best
            self.save()
            return
        # Disk-authoritative base: a stale in-memory copy must never drag the
        # whole profile back to an older state when this one key is written.
        base = self._fresh_profile(profile)
        if base is None:
            base = dict(self._profile_progress.get(profile, {}))
        base["dps_best"] = dps_best
        self._profile_progress[profile] = base
        self.save_profile_data(profile)

    def get_speedrun_best(self, profile: str | None = None) -> dict:
        if not profile:
            return self.speedrun_best
        self._ensure_profile_loaded(profile)
        prof_best = self._profile_progress[profile].get("speedrun_best")
        if prof_best is not None:
            return prof_best
        return self.speedrun_best

    def save_speedrun_best(self, profile: str | None, speedrun_best: dict) -> None:
        if not profile:
            self.speedrun_best = speedrun_best
            self.save()
            return
        # Disk-authoritative base (see save_dps_best)
        base = self._fresh_profile(profile)
        if base is None:
            base = dict(self._profile_progress.get(profile, {}))
        base["speedrun_best"] = speedrun_best
        self._profile_progress[profile] = base
        self.save_profile_data(profile)

    def get_speedrun_boss_best(self, profile: str | None = None) -> dict:
        if not profile:
            return self.speedrun_boss_best
        self._ensure_profile_loaded(profile)
        prof_best = self._profile_progress[profile].get("speedrun_boss_best")
        if prof_best is not None:
            return prof_best
        return self.speedrun_boss_best

    def save_speedrun_boss_best(self, profile: str | None, boss_best: dict) -> None:
        if not profile:
            self.speedrun_boss_best = boss_best
            self.save()
            return
        # Disk-authoritative base (see save_dps_best)
        base = self._fresh_profile(profile)
        if base is None:
            base = dict(self._profile_progress.get(profile, {}))
        base["speedrun_boss_best"] = boss_best
        self._profile_progress[profile] = base
        self.save_profile_data(profile)

    def get_poi_done(self, profile: str | None = None) -> list[str]:
        if not profile:
            return self.poi_done
        self._ensure_profile_loaded(profile)
        return self._profile_progress[profile].setdefault("poi_done", [])

    def is_done(self, poi_id: str, profile: str | None = None) -> bool:
        return poi_id in self.get_poi_done(profile)

    # --- dungeon orbs ---------------------------------------------------------
    # Dedicated per-character done-group for dungeon/rift secret orbs so they
    # never collide with overworld poi_done entries.
    def get_dungeon_orb_done(self, profile: str | None = None) -> list[str]:
        if profile:
            self._ensure_profile_loaded(profile)
            return self._profile_progress[profile].setdefault("dungeon_orb_done", [])
        return self.dungeon_orb_done

    def is_dungeon_orb_done(self, orb_id: str, profile: str | None = None) -> bool:
        return orb_id in self.get_dungeon_orb_done(profile)

    def toggle_dungeon_orb_done(self, orb_id: str, profile: str | None = None) -> bool:
        if profile:
            # Disk-authoritative: read the CURRENT file, apply exactly this
            # one toggle, write back - a stale/partial in-memory copy must
            # never drag the whole profile to an older state (same pattern
            # as toggle_companion_hidden).
            base = self._fresh_profile(profile)
            if base is None:
                base = dict(self._profile_progress.get(profile, {}))
            done_list = list(base.get("dungeon_orb_done", []) or [])
            if orb_id in done_list:
                done_list.remove(orb_id)
                done = False
            else:
                done_list.append(orb_id)
                done = True
            base["dungeon_orb_done"] = done_list
            self._profile_progress[profile] = base
            self.save_profile_data(profile)
            return done
        done_list = self.dungeon_orb_done
        if orb_id in done_list:
            done_list.remove(orb_id)
            done = False
        else:
            done_list.append(orb_id)
            done = True
        self.save()
        return done

    def toggle_done(self, poi_id: str, profile: str | None = None) -> bool:
        if profile:
            # Disk-authoritative (see toggle_dungeon_orb_done)
            base = self._fresh_profile(profile)
            if base is None:
                base = dict(self._profile_progress.get(profile, {}))
            done_list = list(base.get("poi_done", []) or [])
            if poi_id in done_list:
                done_list.remove(poi_id)
                done = False
            else:
                done_list.append(poi_id)
                done = True
            base["poi_done"] = done_list
            self._profile_progress[profile] = base
            self.save_profile_data(profile)
            return done
        done_list = self.poi_done
        if poi_id in done_list:
            done_list.remove(poi_id)
            done = False
        else:
            done_list.append(poi_id)
            done = True
        self.save()
        return done

    def get_entity_hidden_units(self, profile: str | None = None) -> list[str]:
        """Hidden mobs: per-character when a profile is attached, otherwise the
        global settings list (never a progress_default.json)."""
        if profile:
            self._ensure_profile_loaded(profile)
            return self._profile_progress[profile].setdefault("entity_hidden_units", [])
        return self.entity_hidden_units

    def toggle_unit_hidden(self, uid: str, hidden: bool, profile: str | None = None) -> None:
        if profile:
            # Disk-authoritative (see toggle_dungeon_orb_done)
            base = self._fresh_profile(profile)
            if base is None:
                base = dict(self._profile_progress.get(profile, {}))
            cur = set(base.get("entity_hidden_units", []) or [])
            (cur.add if hidden else cur.discard)(uid)
            base["entity_hidden_units"] = sorted(cur)
            self._profile_progress[profile] = base
            self.save_profile_data(profile)
        else:
            cur = set(self.entity_hidden_units)
            (cur.add if hidden else cur.discard)(uid)
            self.entity_hidden_units = sorted(cur)
            self.save()

    def bulk_toggle_units_hidden(self, uids: list[str], hidden: bool, profile: str | None = None) -> None:
        if profile:
            # Disk-authoritative (see toggle_dungeon_orb_done)
            base = self._fresh_profile(profile)
            if base is None:
                base = dict(self._profile_progress.get(profile, {}))
            cur = set(base.get("entity_hidden_units", []) or [])
            for uid in uids:
                (cur.add if hidden else cur.discard)(uid)
            base["entity_hidden_units"] = sorted(cur)
            self._profile_progress[profile] = base
            self.save_profile_data(profile)
        else:
            cur = set(self.entity_hidden_units)
            for uid in uids:
                (cur.add if hidden else cur.discard)(uid)
            self.entity_hidden_units = sorted(cur)
            self.save()

    def set_all_units_hidden(self, uids: list[str], hidden: bool, profile: str | None = None) -> None:
        if profile:
            # Disk-authoritative base so the other keys survive and the clear
            # applies to what is CURRENTLY on disk, not a stale copy.
            base = self._fresh_profile(profile)
            if base is None:
                base = dict(self._profile_progress.get(profile, {}))
            base["entity_hidden_units"] = sorted(uids) if hidden else []
            self._profile_progress[profile] = base
            self.save_profile_data(profile)
        else:
            self.entity_hidden_units = sorted(uids) if hidden else []
            self.save()

    # --- companions ----------------------------------------------------------
    def get_companion_hidden_units(self, profile: str | None = None) -> list[str]:
        return self.companion_hidden_units

    def toggle_companion_hidden(self, uid: str, hidden: bool,
                                profile: str | None = None) -> None:
        """Disk-authoritative companion hide/show toggle.

        collection.json is the single source of truth and is never rewritten
        wholesale from memory (a stale in-memory copy is how the file got
        wiped before). This reads the CURRENT file, applies exactly this one
        unit to its category, and writes it back; the in-memory lists are
        synced to the written result so the UI stays consistent.
        """
        if not uid:
            return
        key = _categorize_companion(uid)
        cur = _read_collection_disk()
        lst = set(cur.get(key, []) or [])
        (lst.add if hidden else lst.discard)(uid)
        cur[key] = sorted(lst)
        try:
            atomic_write_json(_collection_path(), cur, compact_lists=True)
        except OSError:
            pass
        self.pet_hidden_units = sorted(set(cur.get("pets", []) or []))
        self.mount_hidden_units = sorted(set(cur.get("mounts", []) or []))
        self.glider_hidden_units = sorted(set(cur.get("gliders", []) or []))


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
        from .master_backup import load_master_backup
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

