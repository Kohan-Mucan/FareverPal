"""The Settings dataclass: every user-facing field and the read/save path.

The per-profile accessors it carries are inherited from `profiles`; the paths,
audit trail and history helpers it calls come from `store`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, fields, asdict, MISSING

from ..runtime.persist import atomic_write_json, backup_corrupt

from .store import (
    ALL_GATHER_TYPES,
    _DICT_PROPS,
    _PROP_MAP,
    _categorize_companion,
    _collection_path,
    _group_settings_keys,
    _migrate_speedrun_history,
    _purge_nonsense_pbs,
    _restore_collection_from_backup,
    _settings_path,
    audit_settings_event,
    config_dir,
)


from .profiles import _ProfileAccessors


@dataclass
class Settings(_ProfileAccessors):
    # ===================== App / Core =====================
    app_options: list = field(default_factory=lambda: ["auto_attach", "auto_hide_menus", "snap_mouse_to_player"])
    ui_scale: float = 1.0
    opacity: float = 0.9
    hud_accent: str = "#38bdf8"
    icon_size: int = 28
    mouse_snap_rate: int = 500
    geometry: dict = field(default_factory=dict)
    # Sidebar extras and pinned shortcuts — compact list of active items only.
    sidebar_elements: list = field(default_factory=lambda: ["rift_box", "launch_button"])
    # Startup update check, one of `core.updater.UPDATE_CHECK_MODES`: "Always"
    # polls the release oracle even in a source run, "Packaged only" (default)
    # only where a downloaded release can be installed, "Never" opts out
    # everywhere - including in a packaged build, which is the opt-out that did
    # not exist while this was a boolean. Settings → Dev exposes the picker.
    # The literal is a copy of `updater.DEFAULT_UPDATE_CHECK` because this layer
    # must not import core at module import time (a frozen-app import cycle);
    # `test_the_update_check_default_matches_the_updater` pins the two together.
    # It is the CONSERVATIVE default on purpose: an unset source run — and the
    # test suite — must not touch the network unless a dev asks for it.
    check_updates: str = "Packaged only"

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
    # Entity HUD rift-timer card's own on/off (2026-09-19) — the section switch
    # the Dungeon HUD has as dungeon_show_rift. The `show_rift_timer` MODE
    # (Off / Active / Always) still applies on top, so the card shows only when
    # both say yes. Loot reuses the existing show_loot layer toggle.
    entity_show_rift: bool = True
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
    # Minimap N/E/S/W direction labels: their OWN toggle, independent of the
    # main compass needle (show_compass) — off hides the letters only.
    minimap_compass_labels: bool = True

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
    # Per-section visibility (same pattern as the Entity HUD's show_* bools):
    # FOOD / RIFT schedule / BOSS / ENEMIES cards — the four sections that had
    # no toggle at all. Orbs / players / loots stay in dungeon_layers via the
    # existing dungeon_show_* props.
    dungeon_show_food: bool = True
    dungeon_show_rift: bool = True
    dungeon_show_boss: bool = True
    dungeon_show_enemies: bool = True

    # ===================== Speedrun =====================
    # There is no master switch any more: the run timer is ALWAYS automatic in a
    # dungeon or rift (zone-in start, auto stop on the kill, difficulty
    # detection, the boss split, PBs), and re-arm after a finish is always on.
    # "Auto Detect Boss Run" was removed 2026-09-21 — as an opt-in default it read
    # as "the timer is broken" rather than "the feature is off".
    # Saved "auto" / "auto_rearm" keys are stripped on load (see Settings.load).
    speedrun_options: list = field(default_factory=list)

    # ===================== DPS =====================
    dps_options: list = field(default_factory=list)
    dps_view: str = "damage"             # "damage", "healing", "both"
    dps_top_count: int = 8               # default top 8 DPS players
    heals_top_count: int = 2             # default top 2 healers
    # Solo-only meter view: while ungrouped in the open world, the DPS meter
    # and Combat page show the local player's parse only. Party members
    # always show when grouped, and dungeons/rifts always show everyone.
    dps_solo_only: bool = True
    # Pin the local player to the top of the DPS meter list, regardless of
    # their true rank (toggleable; default on).
    dps_pin_me: bool = True
    # Show the Top DPS overlay only inside a dungeon/rift (hidden in the
    # open world); default off = always show while the overlay is open.
    dps_instances_only: bool = True
    # Training-dummy radius, in metres, and whether the Test Dummy HUD puts
    # itself on screen at one. A dummy is exactly where you test a build, and it
    # lives in the open world, so the radius is what surfaces a board there (and
    # what starts, and ends, a dummy test). 0 = no distance cap. Defaults are
    # "close enough to be standing at it". Settings > DPS > Test Dummy.
    dps_dummy_range: int = 5
    dps_boss_clear_range: int = 30
    dps_dummy_free: bool = False
    # Auto On: the Test Dummy HUD appears by itself while a dummy is in range
    # (Rule F). Off makes it an ordinary overlay — the user opens it from the
    # Overlays card and it stays up wherever they are — and the Top DPS meter
    # then keeps its own dummy exception, so one of the two always shows.
    dps_dummy_auto: bool = True
    # How long a dummy test runs before it ends itself, in seconds. The dummy
    # fight is the one encounter nothing in the scene concludes, and two runs of
    # different lengths are not comparable — a target length is what makes "the
    # same rotation, twice" mean anything, and it is the end signal the test
    # can reach without the player. 0 = free-running (Stop Test / walking away),
    # the pre-target behaviour. Settings > DPS > Test Dummy.
    dps_dummy_target: int = 60
    # How long a running dummy test may go with no hit before it ends itself and
    # re-arms, in seconds. The dummy keeps the game "in combat" at any range
    # inside its yard, so this is what lets a test the player has finished
    # reset without walking ~30 m away. 0 = never auto-end. Settings > DPS >
    # Test Dummy.
    dps_dummy_rearm: int = 5
    # Incoming damage, as a percentage of the test's own damage, above which a
    # dummy test is flagged as not a clean measurement. A dummy does not fight
    # back, so damage taken during a test is the rotation or the world walking
    # in — either way the DPS on screen is not the DPS of the build. 0 flags any
    # damage at all. Settings > DPS > Test Dummy.
    dps_dummy_taken_pct: int = 5
    # How many days of archived combat history to keep in moddata/dps, used by
    # the Combat page's "Clear Old Logs" button. 0 = keep everything, which is
    # the default on purpose: the logs are the player's record of their own
    # progress, and nothing is deleted until they ask for it.
    dps_log_keep_days: int = 0
    # How many most-recent DAYS the Combat page's Past Fights list draws on
    # first open, before its "Show all days" control reveals the rest. Counted
    # in days (not logs) because a player thinks in sessions. 0 = show every
    # day, matching the Keep-logs convention above. Settings > DPS > Combat DPS
    # Meter > Fights list window.
    dps_fights_recent_days: int = 7
    # Auto-reset the Top DPS meter when zoning into a dungeon/rift (the
    # open-world run is archived first); default on = current behavior.
    dps_auto_reset_zone: bool = True
    # Located DamageDisplay / DamageResult class pointers, cached after the
    # first successful calibration so an app restart against the same running
    # game skips the multi-minute type scan. Each is class-name-verified on
    # load (find_type / find_result_type) and re-scanned + re-persisted when
    # the game relaunches and the saved address goes stale.
    dmg_display_type: str = ""           # hex st.ui.DamageDisplay hl_type ptr
    dmg_result_type: str = ""            # hex st.skill.DamageResult hl_type ptr
    dps_mode: str = "injector"          # "proxy" (Option 1) | "injector" (Option 2) | "memory" (Option 3)
    dps_proxy_dll: str = "dinput8.dll"  # pe_imports.PROXY_RECOMMENDED (winmm retired 2026-09-23)
    dps_hook_enabled: bool = True        # legacy mirror: True only when dps_mode == "injector"
    farever_game_dir: str = ""           # detected or user-specified path to Farever game folder

    # ===================== Layers / World (Settings → Layers) =====================
    layer_links: list = field(default_factory=list)  # per-row linked layer keys

    # ===================== Overlay Open States =====================
    open_overlays: list = field(default_factory=lambda: ["entity", "map", "dungeon"])
    # Per-overlay chrome for the Test Dummy HUD (bare / transparent), in the
    # same list-membership shape speedrun_options and dps_options use: the
    # Overlays card's toggles resolve through _PROP_MAP as `dummy_bare` /
    # `dummy_transparent` and round-trip through this list.
    dummy_options: list = field(default_factory=list)

    # ===================== Pages (Settings → Pages) =====================
    # Nav keys the user turned off: removed from the sidebar and never built.
    disabled_pages: list = field(default_factory=list)

    # ===================== Server / Pings =====================
    server_pings: list = field(default_factory=list)  # enabled region codes only
    server_custom_hosts: dict = field(default_factory=dict)

    # ===================== Caches / Heavy Data =====================
    dps_best: dict = field(default_factory=dict)
    # The remembered training-dummy test the next one is measured against (its
    # per-skill DPS), per profile. A reference, not a record: finishing a new
    # test replaces it. Carried in and out by the 1 Hz drain, since the rules
    # live in core/dps_dummy.py and core has no settings.
    dps_baseline: dict = field(default_factory=dict)
    # The best dummy test this character has ever run, per profile. A different
    # lifetime from `dps_baseline` (which every run replaces): the best is only
    # ever beaten, so a gear upgrade is measured against the ceiling the player
    # has actually reached rather than against whatever they happened to run
    # last. Carried by the same 1 Hz drain.
    dps_best_dummy: dict = field(default_factory=dict)
    speedrun_best: dict = field(default_factory=dict)
    speedrun_boss_best: dict = field(default_factory=dict)
    speedrun_history: list = field(default_factory=list)
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
    # True only for instances built by load() from a readable settings.json.
    # A bare Settings() holds every field at its dataclass default, so saving
    # one wholesale replaces the user's real settings with defaults — the
    # 2026-09-17 reset. save() uses this flag to keep the stored values
    # instead (see _preserve_stored_values).
    _loaded_from_disk: bool = field(default=False, init=False, repr=False)

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
        existed = p.exists()
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            load_detail = "ok"
        except (OSError, json.JSONDecodeError):
            # A corrupt/unreadable file must never be silently overwritten by
            # the next save(): move it aside so it survives for recovery.
            backup_corrupt(p)
            data = {}
            load_detail = "missing" if not existed else "corrupt-kept-as-bak"
        known = {f.name for f in fields(cls)}
        inst = cls(**{k: v for k, v in data.items() if k in known})
        # Only a successful read counts as "this instance owns what is on
        # disk". A missing/corrupt file was moved aside by backup_corrupt,
        # so there is nothing left to protect and defaults may be written.
        inst._loaded_from_disk = load_detail == "ok"

        # Removed settings, stripped rather than left as flags that read as if a
        # feature still existed: "auto" (the Auto Detect master switch),
        # "auto_rearm" (the Re-arm toggle — re-arm is always on), "dummy_show"
        # (showing the HUD at a training dummy) and `hotkeys` (the global bindings).
        # The run timer is automatic now, it only shows inside an instance, and the
        # app binds no keys at all.
        try:
            stale = {"auto", "auto_rearm", "dummy_show"}
            opts = [k for k in inst.speedrun_options if k not in stale]
            if opts != inst.speedrun_options:
                inst.speedrun_options = opts
        except Exception:
            pass
        try:
            if inst.hotkeys:
                inst.hotkeys = {}
        except Exception:
            pass

        # A proxy name that no longer ships must not survive a load as if it
        # were a choice: version.dll stopped shipping 2026-09-24 (code stays
        # ready in GameFiles/Farever/hooks; it is just not packed or offered).
        # Left alone, the saved name would silently fall back in the picker
        # (which clamps an unknown name to index 0) while the Install/Remove
        # paths kept honouring it - the UI would say dinput8.dll while the
        # installer kept copying version.dll. The shipped registry is the
        # source of truth, imported lazily: the config layer must not import the
        # core layer at module import time (a frozen-app import cycle).
        # The update check is a MODE now, not a boolean: a stored `true`/`false`
        # (or a token from a newer build, or a hand-edit) loads into the mode it
        # means rather than sitting in the field as something no reader
        # understands. Lazy import, like the proxy clamp below, because the
        # config layer must not import core at module import time.
        try:
            from ..core.updater import normalize_update_check
            inst.check_updates = normalize_update_check(inst.check_updates)
        except Exception:
            pass

        try:
            from ..core.pe_imports import PROXY_DLL_NAMES, PROXY_RECOMMENDED
            if inst.dps_proxy_dll not in PROXY_DLL_NAMES:
                inst.dps_proxy_dll = PROXY_RECOMMENDED
        except Exception:
            pass

        # Open-overlay hygiene strips unknown or removed overlay keys so a
        # stale settings entry cannot resurrect a window.
        try:
            known_overlays = {"entity", "map", "dungeon", "speedrun", "dps",
                              "dummy"}
            cleaned = [k for k in inst.open_overlays if k in known_overlays]
            if cleaned != inst.open_overlays:
                inst.open_overlays = cleaned
        except Exception:
            pass

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
        # Migrate per-character speedrun kill logs into the shared
        # cross-character history (speedrun_history.json). One source of
        # truth; PBs stay per-character. Once per data folder — Settings.load
        # is on the hot path and must not rescan every call.
        try:
            _migrate_speedrun_history(inst)
        except Exception:
            pass
        # Delete the Run Timer's sub-second records (the 00:00 boss-split bug)
        # from every profile file and the shared kill log. Once per data
        # folder; the floor is enforced on every write from here on.
        try:
            _purge_nonsense_pbs(inst)
        except Exception:
            pass
        # Baseline fingerprint = cleaned state as it stands right after load,
        # so an unchanged session never rewrites settings.json on its first
        # save() (the migration above may already have written).
        try:
            inst._saved_sig = json.dumps(inst._serialized_settings(),
                                         sort_keys=True, ensure_ascii=False)
        except Exception:
            inst._saved_sig = None
        # Audit only abnormal loads (missing/corrupt file): the hot path
        # (Settings.load() per hero-name lookup) must stay a pure read with
        # zero log I/O, while a fallback-to-defaults is exactly the event a
        # reset investigation needs to see.
        if load_detail != "ok":
            audit_settings_event("load", inst, load_detail)
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

    def _preserve_stored_values(self, serialized: dict) -> dict:
        """Keep stored settings this instance has no value of its own for.

        Two cases, both of which silently reset the user's settings before:

        1. **An instance that never loaded from disk** (a bare `Settings()`) —
           every field is at its dataclass default, so a wholesale save
           replaces the real file with defaults. On 2026-09-17 a test built a
           real `ControlPanel(Settings())`; the Settings page's own `_set()`
           then saved, and the live settings.json was left differing from
           fresh defaults in exactly one key (`farever_game_dir`). Where the
           file holds a non-default value and this instance is still at its
           default, the value on disk wins; anything this instance actually
           set is left alone.
        2. **Empty geometry** — overlay positions are only populated once an
           overlay restores or is dragged, and the first save of a session
           fires from the startup tracking clear. A session that has not
           placed an overlay yet must not delete the stored positions (that
           is an overlay jumping back to its default corner).

        Reads the file only when one of those can apply, and never raises:
        an unreadable file simply means nothing to preserve.
        """
        try:
            on_disk = json.loads(_settings_path().read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return serialized
        if not isinstance(on_disk, dict):
            return serialized
        merged = dict(serialized)
        if not self._loaded_from_disk:
            # Overlay INTENT is not a preference to merge field-by-field, so
            # the per-field loop below cannot see it: a stray instance's
            # `open_overlays` is whatever it opened and closed, never a
            # default, so the "still at default" test never fires and its
            # junk list wins (2026-09-19: a stray defaults save wrote `open=[]`
            # over a five-overlay list and the HUDs stopped restoring).
            #
            # Narrow on purpose: only the FIRST save of an instance that never
            # owned this file. Once it has saved, the file is its own and a
            # later save legitimately reflects its own intent — gating every
            # save instead would freeze the list for a first-run instance that
            # creates the file and then closes an overlay.
            if self._saved_sig is None:
                for intent in ("open_overlays", "geometry"):
                    stored_intent = on_disk.get(intent)
                    if stored_intent:
                        merged[intent] = stored_intent
            for f in fields(self):
                name = f.name
                if name.startswith("_") or name not in on_disk:
                    continue
                if f.default is not MISSING:
                    default = f.default
                elif f.default_factory is not MISSING:  # type: ignore[misc]
                    default = f.default_factory()        # type: ignore[misc]
                else:
                    continue
                if getattr(self, name, None) == default and on_disk[name] != default:
                    merged[name] = on_disk[name]
        stored_geo = on_disk.get("geometry")
        if not merged.get("geometry") and isinstance(stored_geo, dict) and stored_geo:
            merged["geometry"] = stored_geo
        return merged

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
        if not self._loaded_from_disk or not serialized.get("geometry"):
            # Rare paths only: a defaults instance, or one that has not
            # populated any overlay position yet.
            serialized = self._preserve_stored_values(serialized)
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
        audit_settings_event("save", self,
                             "" if self._loaded_from_disk else "defaults-instance")

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
        serialized.pop("dps_baseline", None)
        serialized.pop("dps_best_dummy", None)
        serialized.pop("speedrun_best", None)
        serialized.pop("speedrun_boss_best", None)
        serialized.pop("speedrun_history", None)
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
        for k in ("show_companions_debug",
                  "dmg_display_type", "dmg_result_type"):
            if not serialized.get(k):
                serialized.pop(k, None)
        for k in ("combat_options", "disabled_pages", "layer_links", "server_pings"):
            if not serialized.get(k):
                serialized.pop(k, None)
        return serialized

