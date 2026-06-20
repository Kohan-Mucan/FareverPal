"""Overlay window manager, owns the HUD overlay widgets + their open/close,
global opacity/lock, and the toggle-cards that mirror each overlay's open state.

Pulled out of ControlPanel: the panel registers each overlay toggle card and
hands in the live model on attach, then drives everything through this object.
It communicates UI-ward only via signals (`log`, `request_config`) so it holds no
reference back to the panel. Read-only: the overlays only consume the model.
"""
from __future__ import annotations

from PySide6 import QtCore, QtWidgets

from .entity_overlay import EntityOverlay
from .dps_overlay import DpsOverlay
from .skill_overlay import SkillOverlay
from .minimap import MinimapOverlay
from .speedrun_overlay import SpeedrunOverlay
from .crosshair import CrosshairOverlay
from .tracker import TrackController
from ..geo import orb_sync

# The game HUD overlays that share the global opacity + lock (crosshair has its
# own opacity/click-through). One place so new overlays inherit both.
HUD_OVERLAYS = ("entity", "dps", "skills", "map", "speedrun")

# Overlay key -> widget class (crosshair is special-cased: no model).
_OVERLAY_CLASSES = {
    "entity": EntityOverlay, "dps": DpsOverlay, "skills": SkillOverlay,
    "map": MinimapOverlay, "speedrun": SpeedrunOverlay,
}


class OverlayManager(QtCore.QObject):
    log = QtCore.Signal(str)            # status-log line for the panel
    request_config = QtCore.Signal()    # an overlay asked to raise the control panel

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
        self._combat_timer.setInterval(500)
        self._combat_timer.timeout.connect(self._combat_tick)

    def set_model(self, model) -> None:
        self.model = model
        self.tracker.set_model(model)
        if model is None:
            self._orb_timer.stop()
            self._combat_timer.stop()
            if self._combat_lock_active:
                self._restore_click_through()
        else:
            self._orb_sync = orb_sync.FxSync()
            self._orb_timer.start(1000)
            self._combat_timer.start(500)

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
        """Cards that need a live process (entity/dps/skills/map) are gated until
        attach+locate succeeds. Crosshair stays available (cosmetic)."""
        for key in ("entity", "dps", "skills", "map"):
            for card in self.cards.get(key, []):
                card.setEnabled(on)

    def sync_cards(self, key: str) -> None:
        ov = self.overlays.get(key)
        visible = bool(ov is not None and (ov.isVisible() or getattr(ov, "_auto_hidden", False) or getattr(ov, "_alt_tabbed", False)))
        for card in self.cards.get(key, []):
            card.set_checked_silent(visible)



    def get(self, key: str):
        return self.overlays.get(key)

    # --- open / close ----------------------------------------------------
    def _make(self, key: str):
        if key == "crosshair":
            return CrosshairOverlay(self.s)
        ov = _OVERLAY_CLASSES[key](self.model, self.s)
        if hasattr(ov, "request_config"):
            ov.request_config.connect(self.request_config)
        if hasattr(ov, "set_collection_owned"):
            ov.set_collection_owned(self.collection_owned)
        if hasattr(ov, "set_tracker"):
            ov.set_tracker(self.tracker)
        # Wire the minimap's eye-button \u2192 map-page toggle sync callback
        if key == "map":
            panel = self.parent()
            if panel is not None:
                if hasattr(ov, "_sync_fn") and hasattr(panel, "_hide_collected_toggle"):
                    ov._sync_fn = panel._hide_collected_toggle.set_checked_silent
                if hasattr(ov, "_bare_sync_fn") and hasattr(panel, "_bare_toggle"):
                    ov._bare_sync_fn = panel._bare_toggle.set_checked_silent
        return ov

    def request(self, key: str, on: bool) -> None:
        if not on:
            ov = self.overlays.get(key)
            if ov is not None:
                ov.close()      # WA_DeleteOnClose -> _on_closed
            setting_name = f"open_overlay_{key}"
            if hasattr(self.s, setting_name):
                setattr(self.s, setting_name, False)
                self.s.save()
            return
        if key != "crosshair" and (self.model is None or self.model.player_addr is None):
            self.log.emit("Attach and locate the player first.")
            self.sync_cards(key)   # revert the toggle
            return
        ov = self.overlays.get(key)
        if ov is not None and ov.isVisible():
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

        # Alt-tab focus check
        import ctypes
        import os
        from ctypes import wintypes
        active_hwnd = ctypes.windll.user32.GetForegroundWindow()
        is_strictly_game = False
        game_focused = True
        if active_hwnd:
            pid = ctypes.c_ulong()
            ctypes.windll.user32.GetWindowThreadProcessId(active_hwnd, ctypes.byref(pid))
            is_strictly_game = (m.proc is not None and pid.value == m.proc.pid)
            game_focused = is_strictly_game or (pid.value == os.getpid())
        else:
            game_focused = False

        # Auto-hide overlays when game menus are open (only check if game is active)
        # Mouse snap releases for ANY menu; auto-hide only for non-Escape menus
        menu_any = False
        menu_hide = False
        if (getattr(self.s, "auto_hide_menus", False) or getattr(self.s, "snap_mouse_to_player", False)) and game_focused:
            try:
                menu_any = bool(m.is_game_menu_open(include_escape=True))
                menu_hide = bool(m.is_game_menu_open(include_escape=False))
            except Exception:
                menu_any = False
                menu_hide = False

        # Snap mouse to center of player window when not in menus
        if getattr(self.s, "snap_mouse_to_player", False) and is_strictly_game and not menu_any:
            rect = wintypes.RECT()
            if ctypes.windll.user32.GetClientRect(active_hwnd, ctypes.byref(rect)):
                # center of client area
                cx = (rect.left + rect.right) // 2
                cy = (rect.top + rect.bottom) // 2
                pt = wintypes.POINT(cx, cy)
                ctypes.windll.user32.ClientToScreen(active_hwnd, ctypes.byref(pt))
                ctypes.windll.user32.SetCursorPos(pt.x, pt.y)

        # Check if in a dungeon (uses GameLayer.mainActivity class name — reliable
        # for all dungeon types, no element scanning or POI string matching needed)
        in_dungeon = False
        try:
            in_dungeon = m.is_in_dungeon()
        except Exception:
            pass


        # Build list of overlays to check (including the compass needle if active)
        items = list(self.overlays.items())
        if self.tracker._needle is not None:
            items.append(("compass", self.tracker._needle))

        for key, ov in items:
            if ov is not None:
                # 1. Alt-Tab handling
                is_alt_tabbed = getattr(ov, "_alt_tabbed", False)
                if not game_focused:
                    if not is_alt_tabbed and ov.isVisible():
                        ov.hide()
                        ov._alt_tabbed = True
                    continue
                else:
                    if is_alt_tabbed:
                        ov._alt_tabbed = False
                        # Only show if not hidden by menu or dungeon
                        if not getattr(ov, "_auto_hidden", False) and not getattr(ov, "_dungeon_hidden", False):
                            ov.show()

                # 2. Menu Auto-hide handling
                is_hidden_by_menu = getattr(ov, "_auto_hidden", False)
                if menu_hide:
                    if not is_hidden_by_menu and ov.isVisible():
                        ov.hide()
                        ov._auto_hidden = True
                        self.log.emit(f"Hiding {key} (menu open)")
                else:
                    if is_hidden_by_menu:
                        ov._auto_hidden = False
                        self.log.emit(f"Restoring {key} (menu closed)")
                        # Only show if not hidden by dungeon
                        if not getattr(ov, "_dungeon_hidden", False):
                            ov.show()

                # 3. Dungeon Auto-hide handling
                # Auto hide minimap ("map"), entity ("entity"), and compass ("compass") in dungeon.
                # Do not hide dps meter ("dps") or speedrun meter ("speedrun").
                hide_in_dungeon = in_dungeon and key in ("map", "entity", "compass")
                is_hidden_by_dungeon = getattr(ov, "_dungeon_hidden", False)
                
                if hide_in_dungeon:
                    if not is_hidden_by_dungeon and ov.isVisible():
                        ov.hide()
                        ov._dungeon_hidden = True
                        self.log.emit(f"Hiding {key} (in dungeon)")
                else:
                    if is_hidden_by_dungeon:
                        ov._dungeon_hidden = False
                        self.log.emit(f"Restoring {key} (left dungeon)")
                        # Only show if not hidden by menu
                        if not getattr(ov, "_auto_hidden", False):
                            ov.show()


        if not self.s.combat_click_through:
            if self._combat_lock_active:
                self._restore_click_through()
            return
        
        try:
            in_combat = False
            if m.locator and hasattr(m.locator, "in_combat"):
                in_combat = bool(m.locator.in_combat())
        except Exception:
            in_combat = False
            
        if in_combat:
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
