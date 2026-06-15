"""User settings + persistence.

Stored as JSON under %APPDATA%/FareverCompanion/ so it survives across runs and
the packaged exe. No core/Qt imports here (pure data) so any layer can read it.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict, fields
from pathlib import Path


def experimental_enabled() -> bool:
    """Dev/hidden gate for in-progress features kept OUT of release builds.

    Currently gates the per-skill **Skill Breakdown**: it's read-only-honest but
    incomplete (samples the scattered live DamageDisplay numbers, ~30% coverage
    under heavy load) and slow to calibrate (~5 min cold type-locate), so it isn't
    shipped to normal users. The exact features (headline DPS, by-enemy, the
    survivability strip) are unaffected and always on. All the per-skill code stays
    in-tree, set FAREVER_EXPERIMENTAL=1 to expose it (e.g. for dev / once the GC
    page-walk makes it accurate + fast). See core/damage_source.py.
    """
    return os.environ.get("FAREVER_EXPERIMENTAL", "").strip().lower() not in (
        "", "0", "false", "no", "off")


def config_dir() -> Path:
    import sys
    if getattr(sys, "frozen", False):
        d = Path(sys.executable).parent
    else:
        base = os.environ.get("APPDATA") or str(Path.home())
        d = Path(base) / "FareverCompanion"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _settings_path() -> Path:
    return config_dir() / "settings.json"


@dataclass
class Settings:
    # what the overlays show
    show_enemies: bool = True
    show_chests: bool = True
    show_drops: bool = True
    enemies_only: bool = True
    enemy_count: int = 8
    chest_count: int = 6
    # wild catchable companions (critters) as their own HUD section
    show_companions: bool = True
    companion_count: int = 6
    # unitType ids hidden from the entity HUD's enemy list ([] = show all)
    entity_hidden_types: list = field(default_factory=list)
    # individual unit ids hidden from the enemy list ([] = show all)
    entity_hidden_units: list = field(default_factory=list)
    # nearest uncollected secret orbs as their own HUD section (click = compass)
    show_orbs: bool = True
    orb_count: int = 5
    max_dist: float = 0.0            # 0 = no cap
    icon_size: int = 28
    opacity: float = 0.92
    ui_scale: float = 1.0
    click_through: bool = False
    lock_overlays: bool = False      # lock HUD position + make click-through (mouse passes to game)
    auto_attach: bool = True         # watch for Farever.exe -> attach/locate/detach with zero clicks
    auto_check_updates: bool = True  # check GitHub Releases (via the website) for a newer exe on startup
    entity_scale: float = 1.0        # per-overlay UI zoom
    dps_scale: float = 1.0
    hud_accent: str = "#38bdf8"      # overlay accent/highlight color (per HUD tab)
    # drop prediction
    level: int = 20
    player_class: str = "Auto"       # Auto | Warrior | Rogue | Mage | Priest | Off
    class_only: bool = False
    rows_per_rarity: int = 6
    # dps
    dps_radius: int = 30             # bosses are always tracked regardless
    dps_window: float = 5.0
    # dps overlay size mode: small (just the DPS number), medium (number + graph
    # + per-entity bars), default (everything incl. recent-cycle history).
    dps_mode: str = "default"
    # per-skill table columns shown (order fixed; subset of TABLE_COLUMNS in
    # ui/skill_table.py), used by the Skill Breakdown panel. Toggle in Combat page.
    dps_columns: list = field(default_factory=lambda: ["pct", "dps", "hits", "crit", "max"])
    # show the survivability strip (incoming DTPS + HP + death recap) when data exists
    dps_survival: bool = True
    # show the full cumulative SKILL TOTALS breakdown (icons + dmg per skill) below
    # recent cycles, in Full mode
    dps_skill_totals: bool = True
    # personal-best parse per boss unit_id -> {"dps": float, "hps": float}
    dps_best: dict = field(default_factory=dict)
    # live per-skill breakdown via the DamageDisplay cluster scan (see
    # core/damage_source.py). Default OFF until validated live; HP-diff stays the
    # fallback regardless.
    dps_per_skill: bool = False
    # minimap
    minimap_zoom: float = 8.0
    minimap_size: int = 400
    minimap_shape: str = "Square"     # Circle | Square
    minimap_rotate: bool = True       # rotate map with player heading (else north-up)
    minimap_bare: bool = True         # chromeless: just the map, no titlebar/panel
    minimap_texture: bool = True      # draw the W1 world map under the POIs
    minimap_icons: bool = True        # POIs as real icons (else plain dots)
    minimap_icon_size: int = 28       # POI icon size on the minimap (px)
    # minimap layer visibility (independent of the entity overlay's show_* flags)
    minimap_enemies: bool = True
    minimap_chests: bool = True       # chests, crates & world-activity loot drops
    minimap_gatherables: bool = True
    minimap_obelisks: bool = True
    minimap_orbs: bool = True         # secret orbs (Collector achievements)
    minimap_dungeons: bool = True     # dungeon entrances / teleporters
    # compass-needle target ("" = none): kind "orb" tracks a static secret orb,
    # kind "unit" locks onto the nearest live instance of that unit id
    track_kind: str = ""
    track_id: str = ""
    show_gatherables: bool = True
    show_obelisks: bool = True
    # crosshair overlay
    crosshair_style: str = "Cross + Dot"   # Cross + Dot | Circle | Cross | Dot | T-Shape
    crosshair_size: int = 10               # arm length (px)
    crosshair_gap: int = 4                  # center gap (px)
    crosshair_thickness: int = 2
    crosshair_dot: int = 2                  # dot radius (px)
    crosshair_color: str = "#62ff88"
    crosshair_outline: bool = True
    crosshair_opacity: float = 0.9
    # global hotkeys (system-wide; work while the game is focused)
    hotkey_loot_prev: str = "Ctrl+Alt+Z"        # select previous target
    hotkey_loot_next: str = "Ctrl+Alt+X"        # select next target
    hotkey_loot_close: str = "Ctrl+Alt+C"       # close the drop-table window
    # (avoid Ctrl+Alt+Arrows - those rotate the screen on many GPUs)
    # speedrun timer
    hotkey_speedrun_toggle: str = "Ctrl+Alt+T"   # start / stop the run timer
    hotkey_speedrun_reset: str = "Ctrl+Alt+R"    # reset to 00:00
    speedrun_scale: float = 1.0
    speedrun_best: dict = field(default_factory=dict)  # boss unit_id -> best FULL-run seconds
    speedrun_boss_best: dict = field(default_factory=dict)  # boss unit_id -> best BOSS-split seconds
    # speedrun automation - ONE master toggle: detect the dungeon (boss in scene),
    # auto-start the timer on the player's first move, auto-stop on boss kill, AND
    # auto-detect the Normal/Hard difficulty from the live boss level (Hard scales
    # the dungeon to lvl 20, so live boss level > its normal level = Hard).
    speedrun_auto: bool = True
    # auto-upload finished runs to the web leaderboard (needs login + speedrun_auto;
    # when off, the overlay shows a manual Upload button after a finished run).
    speedrun_auto_upload: bool = False
    # difficulty finished runs are uploaded as ("normal" | "hard"). Used as the
    # FALLBACK when Auto Detect Boss Run is off, or when the live boss level can't
    # be read (offset uncalibrated). Defaults to hard - the difficulty most
    # speedruns are run on.
    speedrun_mode: str = "hard"
    # After a finished run, auto re-arm for the next one (back-to-back) without
    # the user clicking reset - re-arms once they leave the dungeon or after a
    # short grace. Off = the finished time stays until manually reset.
    speedrun_auto_rearm: bool = True
    # Per-run build override. When OFF (default) an uploaded run links the build
    # set on the player's farever-pals.com profile (its featured build). When ON,
    # `speedrun_build_override` (a build code) is attached to uploads instead, so
    # you can log different builds per run from the Speedrun tab.
    speedrun_build_override_on: bool = False
    speedrun_build_override: str = ""            # build code (e.g. ABCD1234), "" = none
    # --- web account (Farever Pal site link) ---
    api_base: str = "https://farever-pals.com"   # web platform base URL
    account_name: str = ""                       # logged-in username ("" = not signed in)
    account_code: str = ""                       # friend code (FRVR-XXXX-XXXX), for display
    account_token: str = ""                      # API bearer token (stored locally)
    account_avatar: str = ""                     # avatar image URL (for the top-right account button)
    # --- friends + presence ---
    share_presence: bool = True                  # broadcast "online (companion)" to friends (hideable)
    # friend codes (FRVR-XXXX-XXXX) pre-picked as co-runners for the NEXT auto-uploaded run
    speedrun_corunners: list = field(default_factory=list)
    # window geometry (per window key -> "x,y")
    geometry: dict = field(default_factory=dict)
    # collectibles marked done (set of ids, stored as list)
    poi_done: list = field(default_factory=list)

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
            _settings_path().write_text(json.dumps(asdict(self), indent=1), encoding="utf-8")
        except OSError:
            pass

    # --- poi done set helpers -------------------------------------------
    def is_done(self, poi_id: str) -> bool:
        return poi_id in self.poi_done

    def toggle_done(self, poi_id: str) -> bool:
        if poi_id in self.poi_done:
            self.poi_done.remove(poi_id)
            done = False
        else:
            self.poi_done.append(poi_id)
            done = True
        self.save()
        return done
