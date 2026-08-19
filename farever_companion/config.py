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
from dataclasses import dataclass, field, asdict, fields
from pathlib import Path

from .persist import (
    atomic_write_json,
    backup_corrupt,
    find_backup_files as _find_backup_files,
)


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


@dataclass
class Settings:
    # --- Core Application Settings ---
    auto_attach: bool = True
    auto_check_updates: bool = False  # Disabled automatic update checks on launch
    ui_scale: float = 1.0
    opacity: float = 0.9
    hud_accent: str = "#38bdf8"
    icon_size: int = 28
    codex_compact: bool = False
    codex_pets_compact: bool = True

    click_through: bool = False
    lock_overlays: bool = False
    auto_hide_menus: bool = True
    snap_mouse_to_player: bool = False
    mouse_snap_rate: int = 500

    # --- Overlay General ---
    show_enemies: bool =False
    show_spark_mobs: bool = False
    enemy_count: int = 4
    show_chests: bool = True
    chest_count: int = 4

    gatherable_count: int = 4
    show_companions: bool = True
    companion_count: int = 2
    show_companions_debug: bool | int = False
    show_orbs: bool = True
    orb_count: int = 4
    show_obelisks: bool = True
    show_compass: bool = False
    max_dist: float = 0.0
    limit_by_zone: bool = True
    entity_hide_collected: bool = True
    entity_width: int = 350
    entity_font_size: int = 15
    entity_bare: bool = False
    PlayerNames: bool = False
    show_group_members: bool = True
    group_count: int = 5
    show_gatherables: bool = False
    # --- Specific Entity Toggles ---
    show_lavendula: bool = True
    show_madrigold: bool = True
    show_zealotus: bool = True
    show_ancientthyme: bool = True
    show_copperore: bool = True
    show_tinore: bool = True
    show_tungstene: bool = True

    # --- Combat & DPS ---
    player_class: str = "Auto"
    level: int = 25
    dps_scale: float = 1.0
    dps_mode: str = "default"
    dps_radius: int = 30
    dps_window: float = 5.0
    dps_survival: bool = False
    dps_skill_totals: bool = False
    dps_per_skill: bool = False
    dps_bare: bool = False
    skills_bare: bool = False
    dps_columns: list = field(default_factory=lambda: ["pct", "dps", "hits", "crit", "max"])
    show_rift_timer: str = "Always"
    combat_click_through: bool = False

    # --- Minimap ---
    minimap_texture: bool = True
    minimap_icons: bool = True
    minimap_icon_size: int = 24
    minimap_size: int = 400
    minimap_zoom: float = 8.0
    minimap_shape: str = "Square"
    minimap_rotate: bool = False
    minimap_bare: bool = False
    minimap_limit_by_zone: bool = False
    minimap_hide_collected: bool = False
    minimap_enemies: bool = False
    minimap_spark_mobs: bool = True
    minimap_chests: bool = True
    minimap_gatherables: bool = False
    minimap_obelisks: bool = True
    minimap_orbs: bool = True
    minimap_dungeons: bool = True
    minimap_companions: bool = True
    minimap_vendors: bool = True
    minimap_soulstones: bool = True

    # --- Speedrun ---
    speedrun_auto: bool = False
    speedrun_auto_rearm: bool = True
    speedrun_mode: str = "hard"
    speedrun_scale: float = 1.0
    speedrun_bare: bool = False
    speedrun_auto_upload: bool = False
    speedrun_build_override_on: bool = False
    speedrun_build_override: str = ""
    open_overlay_speedrun: bool = False

    # --- Overlay Open States ---
    open_overlay_entity: bool = True
    open_overlay_dps: bool = False
    open_overlay_skills: bool = False
    open_overlay_map: bool = True

    # --- Navigation & Targeting ---
    track_kind: str = ""
    track_id: str = ""
    auto_select_next_collectible: bool = True

    # --- Global Hotkeys ---
    hotkey_speedrun_toggle: str = "Ctrl+Alt+T"
    hotkey_speedrun_reset: str = "Ctrl+Alt+R"

    # --- Web & Account ---
    api_base: str = "https://farever-pals.com"
    account_name: str = ""
    account_code: str = ""
    account_token: str = ""
    account_avatar: str = ""
    share_presence: bool = True
    disable_web_features: bool = False
    disable_speedrun_upload: bool = True
    speedrun_corunners: list = field(default_factory=list)

    # --- Heavy Data / Lists ---
    geometry: dict = field(default_factory=dict)
    server_pings: dict = field(default_factory=dict)
    server_custom_hosts: dict = field(default_factory=dict)
    dps_best: dict = field(default_factory=dict)
    speedrun_best: dict = field(default_factory=dict)
    speedrun_boss_best: dict = field(default_factory=dict)
    poi_done: list = field(default_factory=list)
    entity_hidden_units: list = field(default_factory=list)
    pet_hidden_units: list = field(default_factory=list)
    mount_hidden_units: list = field(default_factory=list)
    glider_hidden_units: list = field(default_factory=list)

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

    def __post_init__(self):
        # Runtime-only cache of loaded profile progress lists
        self._profile_progress: dict[str, dict] = {}

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
        if coll_path.exists():
            try:
                coll_data = json.loads(coll_path.read_text(encoding="utf-8"))
                if isinstance(coll_data, dict):
                    inst.pet_hidden_units = coll_data.get("pets", [])
                    inst.mount_hidden_units = coll_data.get("mounts", [])
                    inst.glider_hidden_units = coll_data.get("gliders", [])
            except (OSError, json.JSONDecodeError):
                # preserve a corrupt file instead of clobbering it on save
                backup_corrupt(coll_path)

        # The minimap layer used to be named "Pet shops" (only pet-vendor
        # NPCs); it now covers ALL vendor NPCs (pet + mount shops), so migrate
        # the old key so existing users keep their toggle state.
        if "minimap_petshops" in data and "minimap_vendors" not in data:
            inst.minimap_vendors = bool(data["minimap_petshops"])

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
        return inst

    def save_collection(self) -> None:
        """Save collection data (pets, mounts, gliders hidden state) to collection.json."""
        try:
            data = {
                "pets": sorted(set(self.pet_hidden_units)),
                "mounts": sorted(set(self.mount_hidden_units)),
                "gliders": sorted(set(self.glider_hidden_units))
            }
            atomic_write_json(_collection_path(), data)
        except OSError:
            pass

    def save(self) -> None:
        try:
            # Exclude runtime-only cached attributes starting with underscore
            serialized = {k: v for k, v in asdict(self).items() if not k.startswith("_")}
            # Keep settings.json clean by removing collection lists
            serialized.pop("pet_hidden_units", None)
            serialized.pop("mount_hidden_units", None)
            serialized.pop("glider_hidden_units", None)
            serialized.pop("companion_hidden_units", None)
            atomic_write_json(_settings_path(), serialized)
        except OSError:
            pass
        self.save_collection()

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

    def save_profile_data(self, profile: str) -> None:
        try:
            path = config_dir() / f"progress_{profile}.json"
            data = self._profile_progress.get(profile, {})
            atomic_write_json(path, data)
        except OSError:
            pass

    def save_profile_progress(self, profile: str, done_list: list[str]) -> None:
        self._ensure_profile_loaded(profile)
        self._profile_progress[profile]["poi_done"] = done_list
        self.save_profile_data(profile)

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
        self._ensure_profile_loaded(profile)
        self._profile_progress[profile]["dps_best"] = dps_best
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
        self._ensure_profile_loaded(profile)
        self._profile_progress[profile]["speedrun_best"] = speedrun_best
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
        self._ensure_profile_loaded(profile)
        self._profile_progress[profile]["speedrun_boss_best"] = boss_best
        self.save_profile_data(profile)

    def get_poi_done(self, profile: str | None = None) -> list[str]:
        if not profile:
            return self.poi_done
        self._ensure_profile_loaded(profile)
        return self._profile_progress[profile].setdefault("poi_done", [])

    def is_done(self, poi_id: str, profile: str | None = None) -> bool:
        return poi_id in self.get_poi_done(profile)

    def toggle_done(self, poi_id: str, profile: str | None = None) -> bool:
        done_list = self.get_poi_done(profile)
        if poi_id in done_list:
            done_list.remove(poi_id)
            done = False
        else:
            done_list.append(poi_id)
            done = True
            
        if profile:
            self._profile_progress[profile]["poi_done"] = done_list
            self.save_profile_data(profile)
        else:
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
            hidden_list = self.get_entity_hidden_units(profile)
            cur = set(hidden_list)
            (cur.add if hidden else cur.discard)(uid)
            new_list = sorted(cur)
            self._profile_progress[profile]["entity_hidden_units"] = new_list
            self.save_profile_data(profile)
        else:
            cur = set(self.entity_hidden_units)
            (cur.add if hidden else cur.discard)(uid)
            self.entity_hidden_units = sorted(cur)
            self.save()

    def bulk_toggle_units_hidden(self, uids: list[str], hidden: bool, profile: str | None = None) -> None:
        if profile:
            hidden_list = self.get_entity_hidden_units(profile)
            cur = set(hidden_list)
            for uid in uids:
                (cur.add if hidden else cur.discard)(uid)
            new_list = sorted(cur)
            self._profile_progress[profile]["entity_hidden_units"] = new_list
            self.save_profile_data(profile)
        else:
            cur = set(self.entity_hidden_units)
            for uid in uids:
                (cur.add if hidden else cur.discard)(uid)
            self.entity_hidden_units = sorted(cur)
            self.save()

    def set_all_units_hidden(self, uids: list[str], hidden: bool, profile: str | None = None) -> None:
        if profile:
            new_list = sorted(uids) if hidden else []
            self._ensure_profile_loaded(profile)
            self._profile_progress[profile]["entity_hidden_units"] = new_list
            self.save_profile_data(profile)
        else:
            self.entity_hidden_units = sorted(uids) if hidden else []
            self.save()

    # --- companions ----------------------------------------------------------
    def get_companion_hidden_units(self, profile: str | None = None) -> list[str]:
        return self.companion_hidden_units

    def toggle_companion_hidden(self, uid: str, hidden: bool, profile: str | None = None) -> None:
        cur = set(self.companion_hidden_units)
        (cur.add if hidden else cur.discard)(uid)
        self.companion_hidden_units = sorted(cur)
        self.save_collection()
