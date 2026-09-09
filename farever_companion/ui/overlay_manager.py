"""Overlay window manager, owns the HUD overlay widgets + their open/close,
global opacity/lock, and the toggle-cards that mirror each overlay's open state.

Pulled out of ControlPanel: the panel registers each overlay toggle card and
hands in the live model on attach, then drives everything through this object.
Where each HUD exists is declared once, in `overlay_rules.HUD_PLACES`;
It communicates UI-ward only via signals (`log`, `request_config`) so it holds no
reference back to the panel. Read-only: the overlays only consume the model.
"""
from __future__ import annotations

import sys

from PySide6 import QtCore, QtWidgets
from shiboken6 import isValid

from .tracker import TrackController, _VolatileTrackSettings
from ..geo import orb_sync
from ..core.game_state import (GameState, TickReads, in_instance,
                               read_instance)
from ..core.game_state import _instance_gate_line  # noqa: F401  (re-exported: tests import it through here)


from .overlay_rules import (_at_training_dummy, hud_instance_only,
                            hud_place_hidden, hud_rule_owns_showing)
from .game_window import (_click_through_decision, _cursor_lock_state,
                          _cursor_visible, _foreground_state, _menu_gates,
                          _recenter_cursor, _should_center_mouse)

# The game HUD overlays that share the global opacity + lock. One place so new overlays inherit both.
# "devscanner" joins them for LOCKING (so it doesn't steal mouse clicks while
# playing) but keeps its own always-visible rule in the visibility loop below.
HUD_OVERLAYS = ("entity", "map", "speedrun", "dungeon", "dps", "dummy",
                "devscanner")

# Overlay key -> (module, class). Resolved lazily in _make() so opening the
# app idle doesn't import overlay modules' worth of code + PySide
# registrations — they only load when the user actually opens an overlay.
_OVERLAY_SOURCES = {
    "entity": ("entity_overlay", "EntityOverlay"),
    "map": ("minimap", "MinimapOverlay"),
    "speedrun": ("speedrun_overlay", "SpeedrunOverlay"),
    "dungeon": ("dungeon_overlay", "DungeonOverlay"),
    "dps": ("dps", "DpsOverlay"),
    "dummy": ("dummy_overlay", "DummyOverlay"),
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

# Optional, git-ignored dev tool (ai/workspace/dev_scanner/). Loaded by path so
# a checkout without it behaves exactly like a release build.
_DEV_SCANNER_CLS = False   # False = not probed yet; None = absent/broken


def _dev_scanner_class():
    global _DEV_SCANNER_CLS
    if _DEV_SCANNER_CLS is not False:
        return _DEV_SCANNER_CLS
    import importlib.util
    from pathlib import Path
    here = Path(__file__).resolve()
    roots = [
        here.parents[2],                      # repo root (normal run)
        Path.cwd(),                           # launched from elsewhere
        # frozen builds keep source next to the exe for dev tooling
        Path(getattr(sys, "executable", "")).parent,
    ]
    # `ai/workspace/dev_scanner/` is where it lives now; `ai/dev_scanner/` is
    # where it lived before the 2026-09-17 move, kept first-class so an unmoved
    # copy on an older checkout still loads instead of silently vanishing.
    rels = ("ai/workspace/dev_scanner/loader.py", "ai/dev_scanner/loader.py")
    candidates = [root / rel for rel in rels for root in roots]
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


def _app_tearing_down() -> bool:
    """True while Qt is destroying app objects (console close, Ctrl+C,
    logoff — paths that skip closeEvent). Dying windows then aren't intent."""
    try:
        inst = QtCore.QCoreApplication.instance()
        return inst is not None and bool(inst.closingDown())
    except Exception:
        return False


class OverlayLifecycleMixin:
    """Close lifecycle for OverlayManager (needs its s/overlays/cards,
    _pending_opens, _detaching and sync_cards)."""

    def _on_closed(self, key: str) -> None:
        self.overlays[key] = None
        if _app_tearing_down():
            # Console close / Ctrl+C / logoff: closeEvent never ran, so no
            # detach guard was armed — a dying window still isn't user intent.
            self.sync_cards(key)
            return
        if key in self._pending_opens:
            # Lifecycle close (detach teardown, pre-locate deferral): intent
            # is preserved — never persist OFF. Timing-proof half of the
            # _detaching guard (late delete vs its 500 ms reset can't save).
            self.sync_cards(key)
            return
        if not self._detaching:
            setting_name = f"open_overlay_{key}"
            if hasattr(self.s, setting_name):
                setattr(self.s, setting_name, False)
                self.s.save()
        # Sync AFTER the flip: sync_cards() reads the setting, so syncing
        # first left the card checked-ON while the disk went OFF (looked
        # enabled until the next launch rebuilt it as OFF).
        self.sync_cards(key)

    def close_all(self) -> None:
        """Tear down every open overlay (detach / app close)."""
        self._detaching = True
        # A staggered restore in flight must not open windows after detach.
        try:
            self._stagger_queue.clear()
            self._stagger_scheduled = False
        except AttributeError:
            pass
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


class OverlayRestoreMixin:
    """Post-locate restore for OverlayManager (needs its s/model/overlays,
    _pending_opens, _stagger_queue/_scheduled/_interval, _detaching,
    _ever_located, request() and sync_cards())."""

    def on_located(self, located: bool) -> None:
        """Retry any overlay opens that were deferred by the player-locate gate
        or lost during a game restart/detach.

        Called when the controller's ``located_changed`` signal fires — i.e.
        when the player address resolves (or is lost). Only retries when
        ``located`` flips to True; a locate failure is not retried here (the
        watcher will re-issue RELOCATE ticks and eventually re-emit
        ``located_changed(True)`` once the player loads in).

        Bulk restores are STAGGERED (one overlay per tick) so a 3-4 overlay
        restore does not jank the UI in the same tick as the locate + DPS
        engine start. Manual single toggles via ``request()`` stay immediate.

        Only MISSING windows are queued. The old test included any enabled
        window that merely happened to be HIDDEN, which is not a reason to
        build anything: hidden is the placement table's normal answer (the
        Dungeon HUD in the open world, the minimap inside an instance), and
        the tick's visibility loop is what surfaces a window once its place
        allows it. So every zone round-trip — which is a locate flap, because
        the loading screen loses the player — re-requested four windows the
        rules had already placed, and each ``request()`` SHOWED one of them for
        the single tick before the loop hid it again (the flash), all in the
        same pass that a fresh zone was already loading. """
        if not located:
            return
        self._ever_located = True
        from .overlay_manager import HUD_OVERLAYS

        # Collect keys deferred by the player-locate gate, plus any enabled
        # overlays that are genuinely MISSING (built-and-hidden is not missing:
        # e.g. after a detach/reconnect cycle close_all() left them None).
        keys_to_open = set(self._pending_opens)
        self._pending_opens.clear()
        for key in HUD_OVERLAYS:
            if key == "devscanner":
                continue
            setting_name = f"open_overlay_{key}"
            if getattr(self.s, setting_name, False):
                if self.overlays.get(key) is None:
                    keys_to_open.add(key)

        self._enqueue_staggered(keys_to_open)

    def _enqueue_staggered(self, keys) -> None:
        """Queue bulk overlay opens in stable HUD order, one per tick."""
        from .overlay_manager import HUD_OVERLAYS
        ordered = [k for k in HUD_OVERLAYS
                   if k in keys and getattr(self.s, f"open_overlay_{k}", False)]
        for key in ordered:
            if key not in self._stagger_queue:
                self._stagger_queue.append(key)
        if self._stagger_queue and not self._stagger_scheduled:
            self._stagger_scheduled = True
            QtCore.QTimer.singleShot(0, self._pump_stagger)

    def _pump_stagger(self) -> None:
        """Build the next queued overlay, then schedule the following one.

        A key the placement table says cannot exist where the player is right
        now is DROPPED rather than built: a window that the next tick's loop
        would immediately hide is pure cost, and that cost is the freeze this
        queue exists to prevent. The tick re-queues it (``_queue_missing_huds``)
        the moment a place that allows it arrives — e.g. on zone-in.
        """
        self._stagger_scheduled = False
        if not self._stagger_queue:
            return
        located = (self.model is not None
                   and getattr(self.model, "player_addr", None) is not None)
        if not located or self._detaching:
            # Lost the world (or detaching) mid-restore: park the rest back
            # in pending so the next locate retries them.
            self._pending_opens.update(self._stagger_queue)
            self._stagger_queue.clear()
            return
        key = self._stagger_queue.pop(0)
        if key in self._build_failed or not self._build_allowed_here(key):
            pass                      # placement owns it; the tick will re-queue
        elif getattr(self.s, f"open_overlay_{key}", False):
            try:
                self.request(key, True)
            except Exception:
                # One broken overlay must not stop the others from
                # restoring (request() already guards _make(); this is
                # the belt over those braces).
                pass
        if self._stagger_queue:
            self._stagger_scheduled = True
            QtCore.QTimer.singleShot(self._stagger_interval_ms,
                                     self._pump_stagger)

    def _build_allowed_here(self, key: str) -> bool:
        """Whether `key` may exist where the player is right now.

        The same question the visibility loop asks, read off the same table, so
        the two can never disagree about it. An unreadable scene answers "not
        hidden" (the loop's own reading), which keeps a failed read from
        silently starving a window of its build.
        """
        m = self.model
        try:
            here_in_instance = in_instance(m)
        except Exception:
            here_in_instance = False
        at_dummy = _at_training_dummy(m, self.s)
        try:
            return not hud_place_hidden(self.s, key,
                                        in_instance=here_in_instance,
                                        at_dummy=at_dummy)
        except Exception:
            return True

    def _queue_missing_huds(self, st, at_dummy: bool) -> None:
        """Queue the ONE window a rule wants that does not exist yet.

        The other producer of the stagger queue, and the half the locate
        restore cannot supply: that runs ONCE, so a window whose place is
        INSTANCE (the Dungeon HUD, the Run Timer) is deliberately never built
        while the player stands in the open world — and with no other builder,
        nothing would ever create it on zone-in. Called from the manager's tick
        with the SAME placement answers the visibility loop just read, it
        queues at most one window per tick, through the same queue, so a zone
        edge builds one HUD per tick instead of all of them at once.

        Gated on the user's own card setting, which is the only thing that
        means "I want this window": a key without a card row in HUD_PLACES
        (entity, map) would otherwise have no intent to read at all.
        """
        if self._stagger_queue:
            return                    # one at a time: the queue is already working
        if not self._ever_located or self._detaching:
            return
        m = self.model
        if m is None or getattr(m, "player_addr", None) is None:
            return
        from .overlay_manager import HUD_OVERLAYS
        for key in HUD_OVERLAYS:
            if key == "devscanner" or key in self._build_failed:
                continue
            if not getattr(self.s, f"open_overlay_{key}", False):
                continue
            if self.overlays.get(key) is not None:
                continue
            if hud_place_hidden(self.s, key,
                                in_instance=st.in_instance,
                                at_dummy=at_dummy):
                continue
            self._enqueue_staggered({key})
            return

    def _drain_stagger_sync(self) -> None:
        """Synchronously open every queued overlay (tests only).

        The production path staggers via singleShot timers; a headless test
        that just pumped the loop would otherwise need wall-clock waits for
        each 250 ms step. This runs the same queue through ``request()``
        without timers.
        """
        while self._stagger_queue:
            key = self._stagger_queue.pop(0)
            self._stagger_scheduled = False
            try:
                self.request(key, True)
            except Exception:
                pass

    def open_startup_overlays(self) -> None:
        """Record the user's open-overlay intent on app launch (no builds).

        Nothing is constructed here: windows only build (staggered, one per
        tick) once the player is located, via ``on_located``. Pre-building at
        launch lagged startup and showed nothing until locate anyway; cards
        keep their checked state from settings in the meantime.
        """
        from .overlay_manager import HUD_OVERLAYS
        located = (self.model is not None
                   and getattr(self.model, "player_addr", None) is not None)
        keys = set()
        for key in HUD_OVERLAYS:
            if key == "devscanner":
                continue
            setting_name = f"open_overlay_{key}"
            if getattr(self.s, setting_name, False):
                if self.overlays.get(key) is None:
                    keys.add(key)
                    self._pending_opens.add(key)
                self.sync_cards(key)
        if located and keys:
            pending = set(self._pending_opens)
            self._pending_opens.clear()
            self._ever_located = True
            self._enqueue_staggered(pending | keys)


class OverlayManager(OverlayLifecycleMixin, OverlayRestoreMixin,
                     QtCore.QObject):
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
        # Staggered restore queue: bulk opens after a locate are built/shown
        # one per tick (not all in one pass) so a 3-4 overlay restore does not
        # jank the UI in the same tick as the locate + DPS engine start.
        self._stagger_queue: list[str] = []
        self._stagger_scheduled = False
        # Keys whose _make() raised or gave up (the frozen-build safety net).
        # The single-strike memory matters because the tick re-offers a missing
        # window every 500 ms: without it a module the build could not import
        # would be retried forever, filling the log with the same failure.
        # Cleared when the user switches that card off, so a retry after a fix
        # (or a repair) is still possible.
        self._build_failed: set[str] = set()
        # One overlay per tick, clearly sequential: at 250 ms a 4-overlay
        # restore still landed inside one second and read as "all at once".
        self._stagger_interval_ms = 700
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
        # The per-tick snapshot (core/game_state.py): it owns the last PROVEN
        # menu/instance flags - a failed read must not change what the HUD does -
        # and the tick's one GameState object that every overlay and rule reads
        # instead of re-deriving the same answers from memory.
        self._reads = TickReads(log_line=self.log.emit)   # logs gate verdicts
        self.state: GameState | None = None
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
        prev = self.model
        if prev is not None and prev is not model:
            clear = getattr(prev, "clear_state", None)
            if callable(clear):
                clear()      # a detach must not leave a snapshot for the next one
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
            self._reads.reset()          # a new session must not inherit these
            if self._combat_lock_active:
                self._restore_click_through()
        else:
            self._orb_sync = orb_sync.FxSync()
            self._orb_timer.start(1000)
            self._combat_timer.start(getattr(self.s, "mouse_snap_rate", 500))

    def _orb_tick(self) -> None:
        """Mark loaded world orbs with no glow fx as collected.

        Persisted collection marks are never removed by live observation. A
        glowing orb can be a stale/chunk-activation read; only an explicit
        user un-mark action may remove a collection.
        """
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
            # Write only on a real change: startup restore re-requests the
            # persisted intent, and a full save from a stale copy would
            # clobber newer disk state (second instance launch wiping toggles).
            if bool(getattr(self.s, setting_name, False)) != bool(on):
                setattr(self.s, setting_name, on)
                self.s.save()

        if not on:
            ov = self.overlays.get(key)
            if ov is not None:
                ov.close()      # WA_DeleteOnClose -> _on_closed
            self.overlays[key] = None
            if key in self._stagger_queue:
                self._stagger_queue.remove(key)
            self._pending_opens.discard(key)
            # A card switched off is a fresh start: the next "on" may build what
            # a broken/missing module could not the last time.
            self._build_failed.discard(key)
            self.sync_cards(key)
            return

        # A manual toggle while a staggered restore is queued must not build
        # twice: the queued copy is dropped, this immediate open wins.
        if key in self._stagger_queue:
            self._stagger_queue.remove(key)
        # Lazy build: construction (module imports + widget tree) happens on
        # first open — at toggle time, or staggered after the player locate —
        # never pre-built at app launch while there is nothing to show yet.
        ov = self.overlays.get(key)
        if ov is None:
            try:
                ov = self._make(key)
            except Exception as e:
                # Frozen-build safety net: a module PyInstaller missed (imported
                # by runtime string) degrades to a log line instead of unwinding
                # the caller — else the restore loop dies and NO overlay opens
                # while their toggles still show remembered-checked.
                self.log.emit(f"Overlay '{key}' failed to build: {e}")
                ov = None
            if ov is None:
                self._build_failed.add(key)
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
            # HUDs whose own RULE opens them are left to it: the instance-only
            # pair (Dungeon, Speed Run) on zone-in, and the dummy-only Test
            # Dummy HUD on walking up to a dummy. Showing one here would only
            # flash a window with nothing to show until the next tick hid it.
            # Both halves are read off the placement table, not off key names.
            if hud_instance_only(key):
                show_now = in_instance(self.model)
            else:
                show_now = not hud_rule_owns_showing(key)
            if show_now:
                ov.show()
                self.log.emit(f"Opened {key} overlay.")
        self.sync_cards(key)

    # --- global HUD settings ---------------------------------------------
    def apply_dummy_range(self, value) -> None:
        """The Dummy Range changed: hand it to the core read, repaint the HUD.

        The range is the setting the CORE dummy read is gated by —
        `DummyTracker.dummy_in_range` is `dummy_within(dummy_range)`, and the
        HUD's own row of `overlay_rules.HUD_PLACES` plus the meter's dummy-board
        ownership both consult it — while the number itself lives in Settings.
        Its usual courier is the 1 Hz drain in `dps_background_tick`; waiting for
        that leaves the window painting the verdict the OLD radius produced for
        up to a second after the edit, which is exactly how a setting looks like
        it did nothing. So the number is handed over here, in the same frame as
        the change, and the window is repainted from it.

        Only the repaint is done here, not the hide/show: which windows exist
        where stays the tick's job (`hud_place_hidden`), because that answer
        depends on the menu and focus gates too, and a second, narrower copy of
        it here is how the two drift apart.
        """
        tracker = getattr(getattr(self, "model", None), "dps", None)
        if tracker is not None:
            try:
                tracker.dummy_range = float(value)
            except (TypeError, ValueError):
                pass
        ov = self.overlays.get("dummy")
        refresh = getattr(ov, "refresh_now", None)
        if refresh is not None:
            refresh()

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
        proc = getattr(m, "proc", None)
        active_hwnd, is_strictly_game, is_app_focused = _foreground_state(
            proc.pid if proc else 0)

        # 2. Menu Detection. ONE walk of the UI's window list answers both Rule
        # A (escape menu) and Rule B (auto-hide_menus), so the two can't
        # disagree about the same tick; the raw tri-state stays for the mouse,
        # and both flags are HELD across a failed read (a stale UI pointer must
        # not flash the HUD back over an open menu - see ui/game_window.py).
        menu_state, menu_gameplay = _menu_gates(m)   # raw tri-states this tick

        # 3. Mouse Snapping (only if enabled, attached, player addr is valid,
        # the screen is proven menu-free AND the game itself still holds the
        # cursor for mouse-look - an unreadable gate counts as "don't").
        snap = getattr(self.s, "snap_mouse_to_player", False)
        has_player = m.player_addr is not None
        # The cursor probe costs Win32 calls and none of them can rescue a gate
        # the cheaper checks already blocked, so only make it when they pass.
        cursor_locked = (_cursor_lock_state(active_hwnd)
                         if (snap and is_strictly_game and has_player
                             and menu_state is False) else None)
        if _should_center_mouse(snap, is_strictly_game, has_player, menu_state,
                                cursor_locked):
            _recenter_cursor(active_hwnd)

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

        # 4. Dungeon/Rift detection. Read here, held by TickReads (step 4.7): an
        # unreadable scene must not un-hide the minimap inside an instance or
        # hide the Dungeon HUD.
        inst = read_instance(m)

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
                # Run Timer deliberately does NOT reset from a map-ID change.
                # Its clock is owned only by the proven outside -> instance edge;
                # map IDs can resolve late or change within one dungeon and must
                # never manufacture that edge or clear a valid live run.
                # Reset Dungeon HUD death counters on new instance
                dg_ov = self.overlays.get("dungeon")
                if dg_ov is not None:
                    dg_ov._dg_death_counts = {}
                    dg_ov._dg_prev_alive = {}
                # Dungeon needle target dies with the instance too
                self.dungeon_tracker.clear()
            self._last_map_id = curr_map

        # 4.7 ONE snapshot per tick: the menu/instance flags above (held across a
        # failed read), the zone, the boss and the rift schedule in a single
        # object, published on the model so every overlay and rule reads the
        # same answers for the rest of the tick instead of re-deriving them (and
        # so no two rules can disagree). See core/game_state.py.
        self.state = st = self._reads.read(
            m, menu=menu_state, menu_gameplay=menu_gameplay,
            instance=inst, map_id=curr_map)

        # 5. Visibility Loop
        # We don't hide on Alt-Tab anymore as requested ("nothing hidden when 
        # the game is running"), which also makes restoration much more reliable.
        #
        # Read ONCE for the whole tick: whether the tracker's last scan has a
        # training dummy in range. The dummy HUD's row and each window's own
        # empty state share this one answer, which is how they can't disagree.
        at_dummy = _at_training_dummy(m, self.s)
        # Build the one window a RULE wants but that does not exist yet (the
        # staggered queue's second producer — see _queue_missing_huds). It reads
        # the same placement answers as the loop below and queues at most one
        # per tick, so zoning into a rift builds its HUDs one tick at a time
        # instead of all of them in the tick the instance read flips.
        self._queue_missing_huds(st, at_dummy)
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
                # so you can move/configure overlays while in a menu. Placement is NOT
                # bypassed: it is asked below, outside this branch, so clicking on an
                # overlay (e.g. a DPS history row) sets is_app_focused=True but can NOT
                # cause entity/minimap to reappear while in a dungeon/rift — they exist
                # in every place but an instance (Rule C, the `entity`/`map` rows).
                # (Main compass is always hidden when app is focused as requested;
                #  the DUNGEON needle is exempt so clicking a dungeon row keeps it up.)
                should_hide = False
                if is_app_focused:
                    should_hide = (key == "compass")
                else:
                    is_esc_menu = st.held_menu and not st.held_menu_gameplay
                    auto_hide_enabled = getattr(self.s, "auto_hide_menus", False)
                    
                    # Rule A: Escape menu hides everything except Entity, Minimap, Dungeon HUD, DPS and the Test Dummy HUD
                    if is_esc_menu:
                        should_hide = (key not in ("entity", "map", "dungeon", "dps", "speedrun", "dummy"))
                    # Rule B: Gameplay menus hide everything if enabled
                    elif st.held_menu_gameplay and auto_hide_enabled:
                        should_hide = True
                    
                    # Exception: Speedrun and DPS never auto-hide in dungeons/rifts (ignore menus)
                    if st.in_instance and key in ("speedrun", "dps"):
                        should_hide = False

                # Where this window exists at all: ONE row per HUD in
                # `overlay_rules.HUD_PLACES`, read by one function below the
                # focus/menu branch (so no rule can be bypassed by app focus),
                # which is what stops a new window arriving with a gate that
                # quietly differs from its neighbours'. Four code paths live
                # here no longer: Rule C's `key in ("map", "entity")` — written
                # twice, once per branch — the instance-only pair (Dungeon HUD,
                # Run Timer), the meter's "Instances Only" toggle, and the
                # dummy-only Test Dummy HUD.
                if not should_hide:
                    should_hide = hud_place_hidden(
                        self.s, key,
                        in_instance=st.in_instance, at_dummy=at_dummy)

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


        # Click-through while the player is actually playing: in combat (when
        # enabled) or while the game holds the cursor for mouse-look. The
        # decision is tri-state, so an unreadable source leaves the lock alone
        # (releasing it is the half the player feels - clicks go to the game).
        sources: list[bool | None] = []
        if self.s.combat_click_through:
            sources.append(self._combat_source(m))
        if is_strictly_game and True not in sources:   # no probe once combat locked
            visible = _cursor_visible()
            sources.append(None if visible is None else not visible)

        decision = _click_through_decision(sources)
        if decision is True and not self._combat_lock_active:
            for key in HUD_OVERLAYS:
                ov = self.overlays.get(key)
                if ov is not None and hasattr(ov, "set_click_through"):
                    ov.set_click_through(True)
            self._combat_lock_active = True
        elif decision is False and self._combat_lock_active:
            self._restore_click_through()

    def _combat_source(self, m) -> bool | None:
        """Tri-state `the game says we are in a fight`, for the click-through lock.

        `locator.in_combat()` already answers None (no located hero, or the
        combat fields didn't read); the old `bool(...)` collapsed that to False
        and released the lock on exactly the reads it could not judge. A hero
        that IS located and reports no fight is real evidence, so it passes
        through. No located hero at all counts as no fight - that is knowledge,
        not a missing read, and it keeps the hold above bounded.
        """
        if getattr(m, "player_addr", None) is None:
            return False
        loc = getattr(m, "locator", None)
        if loc is None or not hasattr(loc, "in_combat"):
            return None
        try:
            st = loc.in_combat()
        except Exception:
            return None
        return None if st is None else bool(st)

    def _restore_click_through(self) -> None:
        for key in HUD_OVERLAYS:
            ov = self.overlays.get(key)
            if ov is not None and hasattr(ov, "set_click_through"):
                ov.set_click_through(self.s.lock_overlays)
        self._combat_lock_active = False
