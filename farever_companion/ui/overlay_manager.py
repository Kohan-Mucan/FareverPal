"""Overlay window manager, owns the HUD overlay widgets + their open/close,
global opacity/lock, and the toggle-cards that mirror each overlay's open state.

Pulled out of ControlPanel: the panel registers each overlay toggle card and
hands in the live model on attach, then drives everything through this object.
It communicates UI-ward only via signals (`log`, `request_config`) so it holds no
reference back to the panel. Read-only: the overlays only consume the model.
"""
from __future__ import annotations

from PySide6 import QtCore, QtWidgets

from .overlays.entity_overlay import EntityOverlay
from .overlays.dps_overlay import DpsOverlay
from .overlays.skill_overlay import SkillOverlay
from .overlays.minimap import MinimapOverlay
from .overlays.speedrun_overlay import SpeedrunOverlay
from .tracker import TrackController
from ..geo import orb_sync

# The game HUD overlays that share the global opacity + lock. One place so new overlays inherit both.
HUD_OVERLAYS = ("entity", "dps", "skills", "map", "speedrun")

# Overlay key -> widget class.
_OVERLAY_CLASSES = {
    "entity": EntityOverlay, "dps": DpsOverlay, "skills": SkillOverlay,
    "map": MinimapOverlay, "speedrun": SpeedrunOverlay,
}


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
        # the one compass-needle target, shared by every overlay
        self.tracker = TrackController(settings, self)
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
        self._last_map_id = None

    def update_combat_timer_rate(self) -> None:
        rate = getattr(self.s, "mouse_snap_rate", 500)
        self._combat_timer.setInterval(rate)
        if self._combat_timer.isActive():
            self._combat_timer.start(rate)

    def set_model(self, model) -> None:
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
            self._last_prof = prof

        self.model = model
        self.tracker.set_model(model)

        if model is None:
            self.tracker.clear()
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
            done_list = self.s.get_poi_done(profile)
            mark, unmark = self._orb_sync.update(
                m.world_orb_fx(), set(done_list))
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
        """Cards that need a live process (entity/dps/skills/map/compass/speedrun) are gated until
        attach+locate succeeds."""
        for key in ("entity", "dps", "skills", "map", "compass", "speedrun"):
            for card in self.cards.get(key, []):
                card.setEnabled(on)

    def sync_cards(self, key: str) -> None:
        ov = self.overlays.get(key)
        # If the overlay exists, it counts as "active" even if it's currently 
        # auto-hidden by a menu or alt-tabbed.
        active = bool(ov is not None)
        for card in self.cards.get(key, []):
            card.set_checked_silent(active)



    def get(self, key: str):
        return self.overlays.get(key)

    # --- open / close ----------------------------------------------------
    def _make(self, key: str):
        panel = self.parent()  # The ControlPanel QWidget
        ov = _OVERLAY_CLASSES[key](self.model, self.s, parent=panel)
        if hasattr(ov, "request_page"):
            ov.request_page.connect(self.request_page)
        elif hasattr(ov, "request_config"):
            # backwards compatibility for older overlays
            ov.request_config.connect(lambda: self.request_page.emit("overlays"))
        if hasattr(ov, "set_collection_owned"):
            ov.set_collection_owned(self.collection_owned)
        if hasattr(ov, "set_tracker"):
            ov.set_tracker(self.tracker)
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
                            if hasattr(c, "set_bare_checked_silent"):
                                c.set_bare_checked_silent(on)
                    ov._bare_sync_fn = sync_all_cards
                    break

        # Apply the Borderless (bare) setting from config on creation
        if hasattr(ov, "set_bare"):
            geo_key = getattr(ov, "_geo_key", key)
            if getattr(self.s, f"{geo_key}_bare", False):
                ov.set_bare(True)

        return ov

    def request(self, key: str, on: bool) -> None:
        if not on:
            ov = self.overlays.get(key)
            if ov is not None:
                ov.close()      # WA_DeleteOnClose -> _on_closed
            self.overlays[key] = None
            self.sync_cards(key)
            setting_name = f"open_overlay_{key}"
            if hasattr(self.s, setting_name):
                setattr(self.s, setting_name, False)
                self.s.save()
            return
        if self.model is None or self.model.player_addr is None:
            self.log.emit("Attach and locate the player first.")
            self.sync_cards(key)   # revert the toggle
            return
        ov = self.overlays.get(key)
        if ov is not None:
            # If it already exists but is hidden (Alt-tabbed/Menu), show it
            if not ov.isVisible():
                ov.show()
            return
        ov = self._make(key)
        ov.setAttribute(QtCore.Qt.WA_DeleteOnClose, True)
        ov.destroyed.connect(lambda *_: self._on_closed(key))
        ov.show()
        if self.s.lock_overlays and hasattr(ov, "set_locked"):
            ov.set_locked(True)
        self.overlays[key] = ov
        self.sync_cards(key)
        self.log.emit(f"Opened {key} overlay.")
        setting_name = f"open_overlay_{key}"
        if hasattr(self.s, setting_name):
            setattr(self.s, setting_name, True)
            self.s.save()

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
                ov.close()
            self.overlays[key] = None
        QtCore.QTimer.singleShot(0, self._reset_detaching)

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
            is_strictly_game = (pid.value == m.proc.pid)
            is_app_focused = (pid.value == os.getpid())

        # 2. Menu Detection
        try:
            menu_any = bool(m.is_game_menu_open(include_escape=True))
            menu_hide = bool(m.is_game_menu_open(include_escape=False))
        except Exception:
            menu_any = False
            menu_hide = False

        # 3. Mouse Snapping (only if enabled and not in any menu)
        if getattr(self.s, "snap_mouse_to_player", False) and is_strictly_game and not menu_any:
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
            self.tracker.clear()
            panel = self.parent() if hasattr(self, "parent") else None
            if panel:
                if hasattr(panel, "_last_hidden_unit"): panel._last_hidden_unit = None
                if hasattr(panel, "_last_hidden_comp"): panel._last_hidden_comp = None
        elif not curr_prof and self._last_prof:
            # Player logged out to menu/relog — clear active tracking and hidden undo buffers
            self.tracker.clear()
            panel = self.parent() if hasattr(self, "parent") else None
            if panel:
                if hasattr(panel, "_last_hidden_unit"): panel._last_hidden_unit = None
                if hasattr(panel, "_last_hidden_comp"): panel._last_hidden_comp = None
        
        self._last_prof = curr_prof

        # 4. Dungeon/Rift detection
        in_dungeon = False
        in_rift = False
        try:
            in_dungeon = m.is_in_dungeon()
            in_rift = m.is_in_rift()
        except Exception:
            pass

        # 4.5 Zone/Map change detection (auto-reset DPS/Speedrun on zone swap)
        try:
            curr_map = m.scene.map_id(m.player_addr) or m.scene.activity_id(m.player_addr)
        except Exception:
            curr_map = None

        if curr_map:
            if self._last_map_id and curr_map != self._last_map_id:
                # Clear active compass tracking on map/zone/dungeon/rift swap
                self.tracker.clear()
                # Reset combat session data
                try:
                    m.reset_combat()
                except Exception:
                    pass
                # Reset DPS overlay history cycles
                dps_ov = self.overlays.get("dps")
                if dps_ov is not None and hasattr(dps_ov, "_reset"):
                    try:
                        dps_ov._reset()
                    except Exception:
                        pass
                # Reset Speedrun Timer
                sr_ov = self.overlays.get("speedrun")
                if sr_ov is not None and hasattr(sr_ov, "reset"):
                    try:
                        sr_ov.reset()
                    except Exception:
                        pass
            self._last_map_id = curr_map

        # 5. Visibility Loop
        # We don't hide on Alt-Tab anymore as requested ("nothing hidden when 
        # the game is running"), which also makes restoration much more reliable.
        items = list(self.overlays.items())
        if self.tracker._needle is not None:
            items.append(("compass", self.tracker._needle))

        for key, ov in items:
            if ov is not None:
                # 1. Focus check (Show if game OR app is in focus)
                # Hide map, entity, and compass if the player is not located (e.g. loading screen)
                if not (is_strictly_game or is_app_focused) or (m.player_addr is None and key in ("map", "entity", "compass")):
                    if ov.isVisible():
                        ov.hide()
                    continue

                # 2. Visibility Rules (Menu & Dungeon/Rift)
                # Menu rules (A/B) are bypassed if the companion app itself is focused,
                # so you can move/configure overlays while in a menu.
                # Dungeon/Rift Rule C is NOT bypassed when app-focused — clicking on an
                # overlay (e.g. a DPS history row) sets is_app_focused=True but must NOT
                # cause entity/minimap to reappear while in a dungeon/rift.
                # (Compass is always hidden when app is focused as requested).
                should_hide = False
                if is_app_focused:
                    should_hide = (key == "compass")
                    # Still suppress entity/map/compass in dungeons/rifts even when
                    # an overlay window (e.g. DPS) has app focus.
                    if not should_hide:
                        should_hide = (in_dungeon or in_rift) and key in ("map", "entity", "compass")
                else:
                    is_esc_menu = menu_any and not menu_hide
                    auto_hide_enabled = getattr(self.s, "auto_hide_menus", False)
                    
                    # Rule A: Escape menu hides everything except Entity and Minimap
                    if is_esc_menu:
                        should_hide = (key not in ("entity", "map"))
                    # Rule B: Gameplay menus hide everything if enabled
                    elif menu_hide and auto_hide_enabled:
                        should_hide = True
                    
                    # Rule C: Dungeons/Rifts hide Minimap, Entity, and Compass
                    if not should_hide:
                        should_hide = (in_dungeon or in_rift) and key in ("map", "entity", "compass")

                    # Exception: DPS and Speedrun never auto-hide in dungeons/rifts (ignore menus)
                    if (in_dungeon or in_rift) and key in ("dps", "speedrun"):
                        should_hide = False

                if should_hide:
                    if ov.isVisible():
                        ov.hide()
                else:
                    if key == "compass" and not (self.tracker.s.track_kind and self.tracker.s.track_id):
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
