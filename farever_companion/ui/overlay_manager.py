"""Overlay window manager, owns the HUD overlay widgets + their open/close,
global opacity/lock, and the toggle-cards that mirror each overlay's open state.

Pulled out of ControlPanel: the panel registers each overlay toggle card and
hands in the live model on attach, then drives everything through this object.
It communicates UI-ward only via signals (`log`, `request_config`) so it holds no
reference back to the panel. Read-only: the overlays only consume the model.
"""
from __future__ import annotations

import sys

from PySide6 import QtCore, QtWidgets
from shiboken6 import isValid

from .tracker import TrackController, _VolatileTrackSettings
from ..geo import orb_sync

# The game HUD overlays that share the global opacity + lock. One place so new overlays inherit both.
# "devscanner" joins them for LOCKING (so it doesn't steal mouse clicks while
# playing) but keeps its own always-visible rule in the visibility loop below.
HUD_OVERLAYS = ("entity", "map", "speedrun", "dungeon", "dps",
                "combat", "devscanner")

# Overlay key -> (module, class). Resolved lazily in _make() so opening the
# app idle doesn't import overlay modules' worth of code + PySide
# registrations — they only load when the user actually opens an overlay.
_OVERLAY_SOURCES = {
    "entity": ("entity_overlay", "EntityOverlay"),
    "map": ("minimap", "MinimapOverlay"),
    "speedrun": ("speedrun_overlay", "SpeedrunOverlay"),
    "dungeon": ("dungeon_overlay", "DungeonOverlay"),
    "dps": ("dps_overlay", "DpsOverlay"),
    "combat": ("combat_state_overlay", "CombatStateOverlay"),
}

_OVERLAY_CLASSES: dict[str, type] = {}


def _overlay_class(key: str):
    cls = _OVERLAY_CLASSES.get(key)
    if cls is None:
        mod_name, cls_name = _OVERLAY_SOURCES[key]
        from importlib import import_module
        cls = getattr(import_module(f".overlays.{mod_name}", __package__),
                      cls_name)
        _OVERLAY_CLASSES[key] = cls
    return cls

# Optional, git-ignored dev tool (ai/dev_scanner/). Loaded by path so a
# checkout without it behaves exactly like a release build.
_DEV_SCANNER_CLS = False   # False = not probed yet; None = absent/broken


def _dev_scanner_class():
    global _DEV_SCANNER_CLS
    if _DEV_SCANNER_CLS is not False:
        return _DEV_SCANNER_CLS
    import importlib.util
    from pathlib import Path
    here = Path(__file__).resolve()
    candidates = [
        here.parents[2] / "ai" / "dev_scanner" / "loader.py",
        Path.cwd() / "ai" / "dev_scanner" / "loader.py",
        # frozen builds keep source next to the exe for dev tooling
        Path(getattr(sys, "executable", "")).parent / "ai" / "dev_scanner"
        / "loader.py",
    ]
    cls = None
    for p in candidates:
        if not p.exists():
            continue
        try:
            spec = importlib.util.spec_from_file_location(
                "farever_dev_scanner_loader", p)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            cls = getattr(mod, "OVERLAY_CLASS", None)
            # silent on success — the Overlays-page card IS the confirmation.
            # Only failures print, with the reason.
            if cls is None and getattr(mod, "IMPORT_ERROR", None):
                print(f"[DevScanner] import failed:\n{mod.IMPORT_ERROR}",
                      file=sys.stderr, flush=True)
            break
        except Exception as e:
            print(f"[DevScanner] loader failed: {e}", file=sys.stderr,
                  flush=True)
    _DEV_SCANNER_CLS = cls
    return cls


def dev_scanner_available() -> bool:
    return _dev_scanner_class() is not None


def _is_valid_game_window(active_hwnd: int, game_pid: int) -> bool:
    """Returns True ONLY if active_hwnd is the main game rendering window."""
    if not active_hwnd or not game_pid:
        return False
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32

    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(active_hwnd, ctypes.byref(pid))
    if pid.value != game_pid or (hasattr(user32, "IsHungAppWindow") and user32.IsHungAppWindow(active_hwnd)):
        return False

    # GW_OWNER = 4: modal dialogs/popups have an owner window set; main window does not
    if user32.GetWindow(active_hwnd, 4):
        return False

    buf = ctypes.create_unicode_buffer(512)
    user32.GetClassNameW(active_hwnd, buf, 256)
    cls_name = buf.value.lower()
    if cls_name == "#32770" or "dialog" in cls_name or "crash" in cls_name:
        return False

    user32.GetWindowTextW(active_hwnd, buf, 512)
    title = buf.value.lower()
    if any(k in title for k in ("crash", "error", "exception", "fatal", "assert", "dump", "unhandled", "fault", "stopped")):
        return False

    rect = wintypes.RECT()
    return not (user32.GetClientRect(active_hwnd, ctypes.byref(rect)) and (rect.right < 400 or rect.bottom < 300))


class OverlayManager(QtCore.QObject):
    log = QtCore.Signal(str)            # status-log line for the panel
    request_page = QtCore.Signal(str)   # an overlay asked to raise a specific control panel page

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.s = settings
        self.model = None       # set via set_model on attach; None when detached
        self.overlays: dict[str, QtWidgets.QWidget | None] = {}
        self.cards: dict[str, list] = {}
        self._detaching = False
        self._pending_opens: set[str] = set()   # keys whose open was deferred by the player-locate gate
        # True once the player has been located at least once this session.
        # Overlay windows are pre-built (hidden) at app start so they open
        # instantly; this latch keeps them from surfacing before that first
        # locate (see the visibility loop in _combat_tick).
        self._ever_located = False
        # the one compass-needle target, shared by every overlay
        self.tracker = TrackController(settings, self)
        # second needle for the Dungeon HUD: volatile target (never persisted),
        # and exempt from the hide-on-app-focus rule so clicking a dungeon row
        # keeps the needle up while the panel is focused.
        self.dungeon_tracker = TrackController(_VolatileTrackSettings(settings), self)
        # auto-sync collected orbs from the live glow-fx signal (1 Hz, only
        # while attached; see geo/orb_sync.py)
        self._orb_sync = orb_sync.FxSync()
        self._orb_timer = QtCore.QTimer(self)
        self._orb_timer.timeout.connect(self._orb_tick)
        # account collection state (None = signed out / unknown); the entity
        # overlay marks wild companions that aren't collected yet
        self.collection_owned: set | None = None
        self._combat_lock_active = False
        self._combat_timer = QtCore.QTimer(self)
        self._combat_timer.setInterval(getattr(self.s, "mouse_snap_rate", 500))
        self._combat_timer.timeout.connect(self._combat_tick)
        self._last_prof = None
        self._last_had_addr = False
        self._last_map_id = None

    def update_combat_timer_rate(self) -> None:
        rate = getattr(self.s, "mouse_snap_rate", 500)
        self._combat_timer.setInterval(rate)
        if self._combat_timer.isActive():
            self._combat_timer.start(rate)

    def set_model(self, model) -> None:
        if model is not None and getattr(model, "player_addr", None) is not None:
            self._ever_located = True
        # Check for character change to clear compass tracker, selection, and hidden mob undo buffer
        if model:
            prof = model.player_profile()
            if self._last_prof and prof and prof != self._last_prof:
                self.tracker.clear()
                entity_ov = getattr(self, "overlays", {}).get("entity")
                if entity_ov:
                    entity_ov._sel = None
                map_ov = getattr(self, "overlays", {}).get("map")
                if map_ov and hasattr(map_ov, "canvas"):
                    map_ov.canvas._pan_x = map_ov.canvas._pan_y = 0.0
                    map_ov.canvas.update()
                panel = self.parent() if hasattr(self, "parent") else None
                if panel:
                    if hasattr(panel, "_last_hidden_unit"): panel._last_hidden_unit = None
                    if hasattr(panel, "_last_hidden_comp"): panel._last_hidden_comp = None
                    if hasattr(panel, "_last_hidden_chest_orb"): panel._last_hidden_chest_orb = None
                    # Plotted codex map pins belong to the old character's
                    # context — clear them alongside the tracker.
                    if hasattr(panel, "_clear_codex_map_selection"):
                        panel._clear_codex_map_selection()
                    # Profile-driven codex views (remaining Chests/Orbs) must
                    # re-render against the new character's collected state.
                    if hasattr(panel, "_refresh_codex_grid"):
                        panel._refresh_codex_grid()
            self._last_prof = prof

        self.model = model
        self.tracker.set_model(model)
        self.dungeon_tracker.set_model(model)
        for ov in self.overlays.values():
            if ov is not None:
                if hasattr(ov, "set_model"):
                    ov.set_model(model)
                elif hasattr(ov, "model"):
                    ov.model = model

        if model is None:
            self.tracker.clear()
            self.dungeon_tracker.clear()
            map_ov = getattr(self, "overlays", {}).get("map")
            if map_ov and hasattr(map_ov, "canvas"):
                map_ov.canvas._pan_x = map_ov.canvas._pan_y = 0.0
                map_ov.canvas.update()
            panel = self.parent() if hasattr(self, "parent") else None
            if panel:
                if hasattr(panel, "_last_hidden_unit"): panel._last_hidden_unit = None
                if hasattr(panel, "_last_hidden_comp"): panel._last_hidden_comp = None
            self._orb_timer.stop()
            self._combat_timer.stop()
            if self._combat_lock_active:
                self._restore_click_through()
        else:
            self._orb_sync = orb_sync.FxSync()
            self._orb_timer.start(1000)
            self._combat_timer.start(getattr(self.s, "mouse_snap_rate", 500))

    def _orb_tick(self) -> None:
        """Mark loaded world orbs with no glow fx as collected (debounced);
        un-mark any seen glowing (live truth wins)."""
        m = self.model
        if m is None:
            return
        try:
            profile = m.player_profile()
            # Base on the DISK file, never on possibly-stale/empty memory: a
            # second instance (or a fresh Settings) that never loaded the
            # profile must not sync-wipe a populated file to whatever the
            # live scan currently sees (the 17:33 partial-memory wipe).
            done_list = self.s.get_poi_done_authoritative(profile)
            if not self.s.sync_done_list_safe(profile, done_list):
                return   # disk has data but our base is empty - refuse
            # resolve each live element to the nearest STATIC placement —
            # done lists (and the entity-list filter) key on prefab ids,
            # not the live instance ids
            from ..geo import orbs as geo_orbs
            live = []
            for oid, fx, ox, oy in m.world_orb_fx():
                o = geo_orbs.nearest_orb(ox, oy)
                if o is not None:
                    live.append((o.orb_id, fx))
            mark, unmark = self._orb_sync.update(live, set(done_list))
        except Exception:
            return
        if not mark and not unmark:
            return
        done = set(done_list)
        done.update(mark)
        done.difference_update(unmark)
        done_list.clear()
        done_list.extend(sorted(done))
        if profile:
            self.s.save_profile_progress(profile, done_list)
        else:
            self.s.save()
        for oid in mark:                    # collected the needle's target
            if self.tracker.is_tracked("orb", oid):
                self.tracker.clear()
        self.tracker.changed.emit()         # overlays re-render their orb rows

    # --- toggle cards ----------------------------------------------------
    def register_card(self, key: str, card) -> None:
        self.cards.setdefault(key, []).append(card)

    def set_cards_enabled(self, on: bool) -> None:
        """Cards remain always enabled and interactive so users can configure them anytime."""
        pass

    def sync_cards(self, key: str) -> None:
        ov = self.overlays.get(key)
        active = bool(ov is not None) or bool(getattr(self.s, f"open_overlay_{key}", False))

        cards = self.cards.get(key)
        if not cards:
            return
        alive = []
        for card in cards:
            # pages may have been evicted from the page cache and their
            # widgets destroyed — drop dead references before touching them
            if card is not None and isValid(card):
                card.set_checked_silent(active)
                alive.append(card)
        self.cards[key] = alive

    def get(self, key: str):
        return self.overlays.get(key)

    # --- open / close ----------------------------------------------------
    def _make(self, key: str):
        panel = self.parent()  # The ControlPanel QWidget
        if key == "devscanner":
            cls = _dev_scanner_class()
            if cls is None:
                return None
            ov = cls(self.model, self.s, parent=panel)
        else:
            ov = _overlay_class(key)(self.model, self.s, parent=panel)
        ov._main_win = panel
        if hasattr(ov, "request_page"):
            ov.request_page.connect(self.request_page)
        elif hasattr(ov, "request_config"):
            # backwards compatibility for older overlays
            ov.request_config.connect(lambda: self.request_page.emit("overlays"))
        if hasattr(ov, "log") and hasattr(ov.log, "connect"):
            # An overlay's own log signal (DpsOverlay: st.Group roster
            # join/leave lines) feeds the same Activity Log as the manager's.
            ov.log.connect(self.log)
        if hasattr(ov, "set_collection_owned"):
            ov.set_collection_owned(self.collection_owned)
        if hasattr(ov, "set_tracker"):
            # the Dungeon HUD drives its own volatile-target needle
            ov.set_tracker(self.dungeon_tracker if key == "dungeon" else self.tracker)
        # Wire the minimap's eye-button → map-page toggle sync callback
        if key == "map":
            panel = self.parent()
            if panel is not None:
                if hasattr(ov, "_sync_fn") and hasattr(panel, "_hide_collected_toggle"):
                    ov._sync_fn = panel._hide_collected_toggle.set_checked_silent
                if hasattr(ov, "_zoom_sync_fn") and hasattr(panel, "_zoom_slider"):
                    ov._zoom_sync_fn = panel._zoom_slider.setValueSilent

        # Wire universal "Borderless" (bare) toggle sync
        panel = self.parent()
        if panel is not None:
            # Every OverlayCard in the overlays page (and the map page card)
            # registers with the manager. We find the card(s) and wire the sync.
            for card in self.cards.get(key, []):
                if hasattr(card, "set_bare_checked_silent"):
                    # This is a bit tricky since an overlay can have multiple cards
                    # (like map). We'll use a wrapper to sync all of them.
                    def sync_all_cards(on, k=key):
                        for c in self.cards.get(k, []):
                            if c is not None and isValid(c) and \
                                    hasattr(c, "set_bare_checked_silent"):
                                c.set_bare_checked_silent(on)
                    ov._bare_sync_fn = sync_all_cards
                    break

        # Wire universal "Transparent" toggle sync
        if panel is not None:
            for card in self.cards.get(key, []):
                if hasattr(card, "set_transparent_checked_silent"):
                    def sync_all_trans_cards(on, k=key):
                        for c in self.cards.get(k, []):
                            if c is not None and isValid(c) and \
                                    hasattr(c, "set_transparent_checked_silent"):
                                c.set_transparent_checked_silent(on)
                    ov._transparent_sync_fn = sync_all_trans_cards
                    break

        # Apply the Borderless (bare) & Transparent settings from config on creation
        geo_key = getattr(ov, "_geo_key", key)
        if hasattr(ov, "set_bare"):
            if getattr(self.s, f"{geo_key}_bare", False):
                ov.set_bare(True)
        if hasattr(ov, "set_transparent"):
            if getattr(self.s, f"{geo_key}_transparent", False):
                ov.set_transparent(True)

        return ov

    def request(self, key: str, on: bool) -> None:
        setting_name = f"open_overlay_{key}"
        if hasattr(self.s, setting_name):
            setattr(self.s, setting_name, on)
            self.s.save()

        if not on:
            ov = self.overlays.get(key)
            if ov is not None:
                ov.close()      # WA_DeleteOnClose -> _on_closed
            self.overlays[key] = None
            self.sync_cards(key)
            return

        # Build the window up front so construction (module imports + widget
        # tree) happens at app launch / toggle time — never while the UI is
        # busy catching up after a fresh player locate. The player-locate gate
        # below then only decides VISIBILITY, so an enabled overlay is already
        # built (and shows instantly) the moment the player is located.
        ov = self.overlays.get(key)
        if ov is None:
            ov = self._make(key)
            if ov is None:
                self.sync_cards(key)   # revert the toggle
                return
            ov.setAttribute(QtCore.Qt.WA_DeleteOnClose, True)
            ov.destroyed.connect(lambda *_: self._on_closed(key))
            if self.s.lock_overlays and hasattr(ov, "set_locked"):
                ov.set_locked(True)
            self.overlays[key] = ov

        # Player-locate gate: the window stays HIDDEN until the player is
        # located. The overlay cards keep their checked state (the intent is
        # saved) and the window only surfaces once player_addr resolves —
        # on_located retries then. Matches the pre-3afb584 behaviour where
        # overlays were never visible before locate finished.
        located = (self.model is not None
                   and getattr(self.model, "player_addr", None) is not None)
        if not located:
            if key not in self._pending_opens:
                self._pending_opens.add(key)
            self.sync_cards(key)
            return
        self._ever_located = True

        # If it already exists but is hidden (Alt-tabbed/Menu), show it.
        if not ov.isVisible():
            # Dungeon HUD only opens when actually inside a dungeon/rift.
            if key != "dungeon" or (self.model and self.model.is_in_dungeon_or_rift()):
                ov.show()
                self.log.emit(f"Opened {key} overlay.")
        self.sync_cards(key)

    def on_located(self, located: bool) -> None:
        """Retry any overlay opens that were deferred by the player-locate gate
        or lost during a game restart/detach.

        Called when the controller's ``located_changed`` signal fires — i.e.
        when the player address resolves (or is lost). Only retries when
        ``located`` flips to True; a locate failure is not retried here (the
        watcher will re-issue RELOCATE ticks and eventually re-emit
        ``located_changed(True)`` once the player loads in).
        """
        if not located:
            return
        self._ever_located = True

        # Collect keys deferred by the player-locate gate, plus any enabled
        # overlays that are currently missing (e.g. after a detach/reconnect cycle).
        keys_to_open = set(self._pending_opens)
        self._pending_opens.clear()
        for key in HUD_OVERLAYS:
            if key == "devscanner":
                continue
            setting_name = f"open_overlay_{key}"
            if getattr(self.s, setting_name, False):
                ov = self.overlays.get(key)
                if ov is None or not ov.isVisible():
                    keys_to_open.add(key)

        for key in keys_to_open:
            setting_name = f"open_overlay_{key}"
            if getattr(self.s, setting_name, False):
                self.request(key, True)

    def open_startup_overlays(self) -> None:
        """Re-apply the user's open-overlay preferences on app launch.

        ``request()`` pre-builds every enabled overlay immediately (hidden) so
        the expensive construction runs at launch instead of while the UI is
        recovering from a fresh player locate. Windows only become VISIBLE
        once the player is located (the pre-3afb584 rule) — ``on_located``
        flips the pre-built windows to visible the instant ``located_changed``
        fires, and the visibility loop keeps them hidden until then.
        """
        for key in HUD_OVERLAYS:
            if key == "devscanner":
                continue
            setting_name = f"open_overlay_{key}"
            if getattr(self.s, setting_name, False):
                if self.overlays.get(key) is None:
                    # request() pre-builds the window hidden and only surfaces
                    # it once the player is located AND inside a dungeon/rift
                    # (its dungeon gate + the visibility loop's Rule D), so
                    # building at launch outside a dungeon is safe — and
                    # required, or nothing ever creates it on dungeon entry.
                    self.request(key, True)

    def _on_closed(self, key: str) -> None:
        self.overlays[key] = None
        self.sync_cards(key)
        if not self._detaching:
            setting_name = f"open_overlay_{key}"
            if hasattr(self.s, setting_name):
                setattr(self.s, setting_name, False)
                self.s.save()

    def close_all(self) -> None:
        """Tear down every open overlay (detach / app close)."""
        self._detaching = True
        for key, ov in list(self.overlays.items()):
            if ov is not None:
                try:
                    ov.destroyed.disconnect()
                except Exception:
                    pass
                ov.close()
            self.overlays[key] = None
            if getattr(self.s, f"open_overlay_{key}", False):
                self._pending_opens.add(key)
        QtCore.QTimer.singleShot(500, self._reset_detaching)

    def _reset_detaching(self) -> None:
        self._detaching = False

    # --- global HUD settings ---------------------------------------------
    def set_opacity(self, value) -> None:
        self.s.opacity = value / 100.0
        self.s.save()
        for key in HUD_OVERLAYS:
            ov = self.overlays.get(key)
            if ov and ov.isVisible():
                ov.set_opacity(self.s.opacity)   # forwards to child windows (drop table)
        self.tracker.set_opacity(self.s.opacity)
        self.dungeon_tracker.set_opacity(self.s.opacity)

    def set_lock(self, on: bool) -> None:
        self.s.lock_overlays = on
        self.s.save()
        for key in HUD_OVERLAYS:
            ov = self.overlays.get(key)
            if ov is not None and hasattr(ov, "set_locked"):
                ov.set_locked(on)
        self.log.emit("Overlays LOCKED (click-through)." if on
                      else "Overlays unlocked (interactive).")

    def set_collection_owned(self, owned: set | None) -> None:
        self.collection_owned = owned
        for ov in self.overlays.values():
            if ov is not None and hasattr(ov, "set_collection_owned"):
                ov.set_collection_owned(owned)

    def apply_accent(self, color) -> None:
        for ov in self.overlays.values():
            if ov is not None and hasattr(ov, "apply_accent"):
                ov.apply_accent(color)

    def set_combat_click_through(self, on: bool) -> None:
        self.s.combat_click_through = on
        self.s.save()
        if not on and self._combat_lock_active:
            self._restore_click_through()

    def _combat_tick(self) -> None:
        m = self.model
        if m is None:
            if self._combat_lock_active:
                self._restore_click_through()
            return

        # 1. Focus check
        import ctypes
        import os
        from ctypes import wintypes
        active_hwnd = ctypes.windll.user32.GetForegroundWindow()
        is_strictly_game = False
        is_app_focused = False
        if active_hwnd and m.proc:
            pid = ctypes.c_ulong()
            ctypes.windll.user32.GetWindowThreadProcessId(active_hwnd, ctypes.byref(pid))
            is_strictly_game = _is_valid_game_window(active_hwnd, m.proc.pid)
            is_app_focused = (pid.value == os.getpid())

        # 2. Menu Detection
        try:
            menu_any = bool(m.is_game_menu_open(include_escape=True))
            menu_hide = bool(m.is_game_menu_open(include_escape=False))
        except Exception:
            menu_any = False
            menu_hide = False

        # 3. Mouse Snapping (only if enabled, attached, player addr is valid, and not in any menu)
        if getattr(self.s, "snap_mouse_to_player", False) and is_strictly_game and m.player_addr is not None and not menu_any:
            rect = wintypes.RECT()
            if ctypes.windll.user32.GetClientRect(active_hwnd, ctypes.byref(rect)):
                # center of client area
                cx = (rect.left + rect.right) // 2
                cy = (rect.top + rect.bottom) // 2
                pt = wintypes.POINT(cx, cy)
                ctypes.windll.user32.ClientToScreen(active_hwnd, ctypes.byref(pt))
                ctypes.windll.user32.SetCursorPos(pt.x, pt.y)

        # 3.5 Character/Profile change detection
        try:
            curr_prof = m.player_profile()
        except Exception:
            curr_prof = None

        if curr_prof and self._last_prof and curr_prof != self._last_prof:
            # Character changed — clear active tracking and selection
            self.tracker.clear()
            panel = self.parent() if hasattr(self, "parent") else None
            if panel:
                if hasattr(panel, "_last_hidden_unit"): panel._last_hidden_unit = None
                if hasattr(panel, "_last_hidden_comp"): panel._last_hidden_comp = None
                if hasattr(panel, "_last_hidden_chest_orb"): panel._last_hidden_chest_orb = None
                # Plotted codex map pins belong to the old character's
                # context — clear them alongside the tracker.
                if hasattr(panel, "_clear_codex_map_selection"):
                    panel._clear_codex_map_selection()
                # Profile-driven codex views (remaining Chests/Orbs) must
                # re-render against the new character's collected state.
                if hasattr(panel, "_refresh_codex_grid"):
                    panel._refresh_codex_grid()

        # Logged out check: if player address is gone, but was previously there
        if m.player_addr is None and getattr(self, "_last_had_addr", False):
            self.tracker.clear()
            panel = self.parent() if hasattr(self, "parent") else None
            if panel:
                if hasattr(panel, "_last_hidden_unit"): panel._last_hidden_unit = None
                if hasattr(panel, "_last_hidden_comp"): panel._last_hidden_comp = None
        
        self._last_prof = curr_prof
        self._last_had_addr = (m.player_addr is not None)

        # 4. Dungeon/Rift detection
        in_dungeon = False
        in_rift = False
        try:
            in_dungeon = m.is_in_dungeon()
            in_rift = m.is_in_rift()
        except Exception:
            pass

        # 4.5 Zone/Map change detection (auto-reset on zone swap)
        try:
            curr_map = m.scene.map_id(m.player_addr) or m.scene.activity_id(m.player_addr)
        except Exception:
            curr_map = None

        if curr_map:
            if self._last_map_id and curr_map != self._last_map_id:
                # Clear active compass tracking on map/zone/dungeon/rift swap
                # This ensures the tracker doesn't point to non-existent objects in new scenes.
                self.tracker.clear()
                # Reset Speedrun Timer
                sr_ov = self.overlays.get("speedrun")
                if sr_ov is not None and hasattr(sr_ov, "reset"):
                    try:
                        sr_ov.reset()
                    except Exception:
                        pass
                # Reset Dungeon HUD death counters on new instance
                dg_ov = self.overlays.get("dungeon")
                if dg_ov is not None:
                    dg_ov._dg_death_counts = {}
                    dg_ov._dg_prev_alive = {}
                # Dungeon needle target dies with the instance too
                self.dungeon_tracker.clear()
            self._last_map_id = curr_map

        # 5. Visibility Loop
        # We don't hide on Alt-Tab anymore as requested ("nothing hidden when 
        # the game is running"), which also makes restoration much more reliable.
        items = list(self.overlays.items())
        if self.tracker._needle is not None:
            items.append(("compass", self.tracker._needle))
        if self.dungeon_tracker._needle is not None:
            items.append(("dcompass", self.dungeon_tracker._needle))

        for key, ov in items:
            if ov is not None:
                # DEV SCANNER: always visible — ignores ESC/gameplay menus,
                # dungeons/rifts and focus loss. Never force-clicked.
                if key == "devscanner":
                    if not ov.isVisible():
                        ov.show()
                    continue
                # Player-locate gate for the PRE-BUILT overlays: windows are
                # constructed hidden at app start (so they open instantly once
                # located) and must not surface before that first locate — the
                # rule the old deferred build enforced by not existing yet.
                if not self._ever_located:
                    if ov.isVisible():
                        ov.hide()
                    continue
                # 1. Player / Focus check: Immediately hide map, entity, and compasses if player address is missing (character screen, loading) or neither game nor app is focused
                if m.player_addr is None and key in ("map", "entity", "compass", "dcompass"):
                    if ov.isVisible():
                        ov.hide()
                    continue

                if not (is_strictly_game or is_app_focused):
                    if ov.isVisible():
                        ov.hide()
                    continue

                # 2. Visibility Rules (Menu & Dungeon/Rift)
                # Menu rules (A/B) are bypassed if the companion app itself is focused,
                # so you can move/configure overlays while in a menu.
                # Dungeon/Rift Rule C is NOT bypassed when app-focused — clicking on an
                # overlay (e.g. a DPS history row) sets is_app_focused=True but must NOT
                # cause entity/minimap to reappear while in a dungeon/rift.
                # (Main compass is always hidden when app is focused as requested;
                #  the DUNGEON needle is exempt so clicking a dungeon row keeps it up.)
                should_hide = False
                if is_app_focused:
                    should_hide = (key == "compass")
                    # Still suppress entity/map in dungeons/rifts even when
                    # an overlay window (e.g. DPS) has app focus. Compass
                    # stays visible: the Dungeon HUD tracks rift bosses.
                    if not should_hide:
                        should_hide = (in_dungeon or in_rift) and key in ("map", "entity")
                else:
                    is_esc_menu = menu_any and not menu_hide
                    auto_hide_enabled = getattr(self.s, "auto_hide_menus", False)
                    
                    # Rule A: Escape menu hides everything except Entity, Minimap, Dungeon HUD, DPS
                    if is_esc_menu:
                        should_hide = (key not in ("entity", "map", "dungeon", "dps", "speedrun"))
                    # Rule B: Gameplay menus hide everything if enabled
                    elif menu_hide and auto_hide_enabled:
                        should_hide = True
                    
                    # Rule C: Dungeons/Rifts hide Minimap and Entity
                    # (Compass stays — the Dungeon HUD tracks rift bosses in there; DPS stays active)
                    if not should_hide:
                        should_hide = (in_dungeon or in_rift) and key in ("map", "entity")

                    # Exception: Speedrun and DPS never auto-hide in dungeons/rifts (ignore menus)
                    if (in_dungeon or in_rift) and key in ("speedrun", "dps"):
                        should_hide = False

                # Rule D: Dungeon HUD only exists inside dungeons/rifts — hide
                # it anywhere else, even when the app has focus. Also honor
                # the user's card intent: if they toggled it off mid-session
                # (window destroyed, setting off), never resurrect it here.
                if not should_hide and key == "dungeon":
                    if not getattr(self.s, "open_overlay_dungeon", False):
                        should_hide = True
                    else:
                        should_hide = not (in_dungeon or in_rift)

                if should_hide:
                    if ov.isVisible():
                        ov.hide()
                else:
                    _tk_s = self.dungeon_tracker.s if key == "dcompass" else self.tracker.s
                    if key in ("compass", "dcompass") and not (_tk_s.track_kind and _tk_s.track_id):
                        if ov.isVisible():
                            ov.hide()
                    else:
                        if not ov.isVisible():
                            ov.show()


        # Determine if we should temporarily force click-through (combat or cursor-hidden/Mouse-park)
        should_force_lock = False
        
        # 1. Combat check (if enabled)
        if self.s.combat_click_through:
            try:
                if m.locator and hasattr(m.locator, "in_combat") and bool(m.locator.in_combat()):
                    should_force_lock = True
            except Exception:
                pass
                
        # 2. Mouse-park check (cursor hidden while playing in game)
        if not should_force_lock and is_strictly_game:
            try:
                class CURSORINFO(ctypes.Structure):
                    _fields_ = [
                        ("cbSize", wintypes.DWORD),
                        ("flags", wintypes.DWORD),
                        ("hCursor", wintypes.HCURSOR),
                        ("ptScreenPos", wintypes.POINT),
                    ]
                cinfo = CURSORINFO()
                cinfo.cbSize = ctypes.sizeof(CURSORINFO)
                if ctypes.windll.user32.GetCursorInfo(ctypes.byref(cinfo)):
                    # CURSOR_SHOWING = 0x00000001
                    if (cinfo.flags & 0x00000001) == 0:
                        should_force_lock = True
            except Exception:
                pass

        if should_force_lock:
            if not self._combat_lock_active:
                for key in HUD_OVERLAYS:
                    ov = self.overlays.get(key)
                    if ov is not None and hasattr(ov, "set_click_through"):
                        ov.set_click_through(True)
                self._combat_lock_active = True
        else:
            if self._combat_lock_active:
                self._restore_click_through()


    def _restore_click_through(self) -> None:
        for key in HUD_OVERLAYS:
            ov = self.overlays.get(key)
            if ov is not None and hasattr(ov, "set_click_through"):
                ov.set_click_through(self.s.lock_overlays)
        self._combat_lock_active = False
