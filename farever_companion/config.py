"""User settings + persistence.

Stored as JSON inside moddata/ folder next to the app executable (or dist/moddata in dev mode).
No core/Qt imports here (pure data) so any layer can read it.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict, fields
from pathlib import Path


def experimental_enabled() -> bool:
    """Dev/hidden gate for in-progress features kept OUT of release builds."""
    return os.environ.get("FAREVER_EXPERIMENTAL", "").strip().lower() not in (
        "", "0", "false", "no", "off")


def config_dir() -> Path:
    import sys
    if getattr(sys, "frozen", False):
        d = Path(sys.executable).parent / "moddata"
    else:
        d = Path(__file__).resolve().parent.parent / "dist" / "moddata"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _settings_path() -> Path:
    return config_dir() / "settings.json"


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
    show_enemies: bool = False
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
    show_group_members: bool = False
    group_count: int = 5
    show_gatherables: bool = True
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
    show_drop_window: bool = False
    show_rift_timer: str = "Always"
    combat_click_through: bool = False
    rows_per_rarity: int = 6

    # --- Minimap ---
    minimap_texture: bool = True
    minimap_icons: bool = True
    minimap_icon_size: int = 24
    minimap_size: int = 400
    minimap_zoom: float = 8.0
    minimap_shape: str = "Square"
    minimap_rotate: bool = True
    minimap_bare: bool = False
    minimap_limit_by_zone: bool = True
    minimap_hide_collected: bool = False
    minimap_enemies: bool = False
    minimap_chests: bool = True
    minimap_gatherables: bool = False
    minimap_obelisks: bool = True
    minimap_orbs: bool = True
    minimap_dungeons: bool = True
    minimap_companions: bool = True

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
    open_overlay_entity: bool = False
    open_overlay_dps: bool = False
    open_overlay_skills: bool = False
    open_overlay_map: bool = False

    # --- Navigation & Targeting ---
    track_kind: str = ""
    track_id: str = ""
    auto_select_next_collectible: bool = True

    # --- Global Hotkeys ---
    hotkey_speedrun_toggle: str = "Ctrl+Alt+T"
    hotkey_speedrun_reset: str = "Ctrl+Alt+R"
    hotkey_loot_prev: str = "Ctrl+Alt+Z"
    hotkey_loot_next: str = "Ctrl+Alt+X"
    hotkey_loot_close: str = "Ctrl+Alt+C"

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
    companion_hidden_units: list = field(default_factory=list)

    def __post_init__(self):
        # Runtime-only cache of loaded profile progress lists
        self._profile_progress: dict[str, dict] = {}

    @classmethod
    def load(cls) -> "Settings":
        try:
            data = json.loads(_settings_path().read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls()
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})

    def save(self) -> None:
        try:
            # Exclude runtime-only cached attributes starting with underscore
            serialized = {k: v for k, v in asdict(self).items() if not k.startswith("_")}
            _settings_path().write_text(json.dumps(serialized, indent=1), encoding="utf-8")
        except OSError:
            pass

    # --- profile data helpers -------------------------------------------
    def _ensure_profile_loaded(self, profile: str) -> None:
        if profile not in self._profile_progress:
            path = config_dir() / f"progress_{profile}.json"
            if path.exists():
                try:
                    self._profile_progress[profile] = json.loads(path.read_text(encoding="utf-8"))
                except Exception:
                    self._profile_progress[profile] = {}
            else:
                self._profile_progress[profile] = {}

    def save_profile_data(self, profile: str) -> None:
        try:
            path = config_dir() / f"progress_{profile}.json"
            data = self._profile_progress.get(profile, {})
            path.write_text(json.dumps(data, indent=1), encoding="utf-8")
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
        if not profile:
            return []
        self._ensure_profile_loaded(profile)
        return self._profile_progress[profile].setdefault("entity_hidden_units", [])

    def toggle_unit_hidden(self, uid: str, hidden: bool, profile: str | None = None) -> None:
        if not profile:
            return
        hidden_list = self.get_entity_hidden_units(profile)
        cur = set(hidden_list)
        (cur.add if hidden else cur.discard)(uid)
        new_list = sorted(cur)
        
        self._profile_progress[profile]["entity_hidden_units"] = new_list
        self.save_profile_data(profile)

    def bulk_toggle_units_hidden(self, uids: list[str], hidden: bool, profile: str | None = None) -> None:
        if not profile:
            return
        hidden_list = self.get_entity_hidden_units(profile)
        cur = set(hidden_list)
        for uid in uids:
            (cur.add if hidden else cur.discard)(uid)
        new_list = sorted(cur)
        
        self._profile_progress[profile]["entity_hidden_units"] = new_list
        self.save_profile_data(profile)

    def set_all_units_hidden(self, uids: list[str], hidden: bool, profile: str | None = None) -> None:
        if not profile:
            return
        new_list = sorted(uids) if hidden else []
        self._ensure_profile_loaded(profile)
        self._profile_progress[profile]["entity_hidden_units"] = new_list
        self.save_profile_data(profile)

    # --- companions ----------------------------------------------------------
    def get_companion_hidden_units(self, profile: str | None = None) -> list[str]:
        return self.companion_hidden_units

    def toggle_companion_hidden(self, uid: str, hidden: bool, profile: str | None = None) -> None:
        cur = set(self.companion_hidden_units)
        (cur.add if hidden else cur.discard)(uid)
        self.companion_hidden_units = sorted(cur)
        self.save()
