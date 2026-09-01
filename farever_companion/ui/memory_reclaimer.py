"""Memory reclamation, LRU page eviction, and Windows working-set management."""
from __future__ import annotations

import time
from PySide6 import QtCore, QtGui, QtWidgets
from shiboken6 import isValid as _is_valid

from ..data import icons


def _trim_working_set() -> None:
    """Flush Python garbage and release unreferenced pages from the Windows
    process working set back to the OS.

    Without SetProcessWorkingSetSize(-1, -1), the Windows memory manager keeps
    allocated/freed heap pages in the process's physical RAM (Working Set).
    Calling this brings idle RAM from ~250MB+ down to ~30-50MB.
    """
    import gc
    import sys
    gc.collect()
    if sys.platform == "win32":
        try:
            import ctypes
            k32 = ctypes.windll.kernel32
            k32.GetCurrentProcess.restype = ctypes.c_void_p
            h = k32.GetCurrentProcess()
            k32.SetProcessWorkingSetSize(h, ctypes.c_size_t(-1), ctypes.c_size_t(-1))
        except Exception:
            pass


class MemoryReclaimerMixin:
    """Mixin managing LRU page caching, idle cache sweeps, and working-set trimming."""

    _PAGE_CACHE_BUDGET = 2
    _IDLE_AFTER_S = 20
    _PAGE_IDLE_EVICT_S = 120

    @staticmethod
    def _widget_alive(w) -> bool:
        """True when a widget reference still points at a live (non-deleted) Qt object."""
        return w is not None and _is_valid(w)

    def _evict_page(self, key: str) -> None:
        """Shed one built page's widget tree (swapped for a fresh placeholder)."""
        order = self._nav_order()
        if key not in order or not self._pages_built.get(key):
            return
        idx = order.index(key)
        w = self.stack.widget(idx)
        if w is not None:
            self.stack.removeWidget(w)
            w.deleteLater()
            self.stack.insertWidget(idx, QtWidgets.QWidget())
        self._pages_built[key] = False
        self._page_last_used.pop(key, None)
        self._on_page_evicted(key)

    def _evict_idle_pages(self, current_key: str) -> None:
        """LRU page-cache eviction. Keeps only the currently viewed page resident."""
        if not hasattr(self, "_pages_built"):
            return
        order = self._nav_order()
        built = [k for k in order if self._pages_built.get(k)]
        evicted = False
        while len(built) > self._PAGE_CACHE_BUDGET:
            candidates = [k for k in built if k != current_key]
            if not candidates:
                break
            lru = min(candidates, key=lambda k: self._page_last_used.get(k, 0.0))
            self._evict_page(lru)
            evicted = True
            built = [k for k in order if self._pages_built.get(k)]
        if evicted and not self._live_session_rendering():
            _trim_working_set()

    def _memory_tick(self) -> None:
        self._evict_stale_pages()
        self._idle_sweep_check()

    def _evict_stale_pages(self) -> None:
        """Unload built pages the user navigated away from more than `_PAGE_IDLE_EVICT_S` ago."""
        if self.model is not None:
            return
        if not hasattr(self, "_pages_built"):
            return
        now = time.monotonic()
        cur_key = (self._current_page() or "").partition(":")[0]
        evicted = False
        for k in self._nav_order():
            if k == cur_key or not self._pages_built.get(k):
                continue
            if now - self._page_last_used.get(k, 0.0) >= self._PAGE_IDLE_EVICT_S:
                self._evict_page(k)
                evicted = True
        if evicted:
            _trim_working_set()

    def _live_overlays_active(self) -> bool:
        """True when any HUD overlay / compass needle is on screen."""
        om = getattr(self, "overlay_mgr", None)
        if not om:
            return False
        for ov in om.overlays.values():
            if ov is not None and ov.isVisible():
                return True
        for tr in (om.tracker, om.dungeon_tracker):
            needle = getattr(tr, "_needle", None)
            if needle is not None and needle.isVisible():
                return True
        return False

    def _live_session_rendering(self) -> bool:
        """True while the game is attached AND a HUD overlay / needle is on screen."""
        return getattr(self, "model", None) is not None and self._live_overlays_active()

    def _idle_sweep_check(self) -> None:
        """When nobody has touched the panel for a while, unload rebuildable items."""
        if time.monotonic() - getattr(self, "_last_interaction", 0.0) < self._IDLE_AFTER_S:
            return
        order = self._nav_order()
        cur_key = (self._current_page() or "").partition(":")[0]
        if getattr(self, "model", None) is not None:
            unloaded = []
        else:
            unloaded = [k for k in order if k != cur_key and self._pages_built.get(k)]
        for k in unloaded:
            self._evict_page(k)

        shed_caches = False
        if not (getattr(self, "model", None) is not None and self._live_overlays_active()):
            icons.trim_caches()
            QtGui.QPixmapCache.clear()
            shed_caches = True

        if unloaded or shed_caches:
            _trim_working_set()
        if unloaded and hasattr(self, "log"):
            self.log(f"Idle {self._IDLE_AFTER_S}s — unloaded {len(unloaded)} page(s) to free memory.")

    def _on_minimized(self) -> None:
        """Unload ALL pages and sprite caches when minimized so memory drops to minimum."""
        self._was_minimized = True
        self._minimized_page = self._current_page()
        order = self._nav_order()
        for k in order:
            if self._pages_built.get(k):
                self._evict_page(k)
        if not (getattr(self, "model", None) is not None and self._live_overlays_active()):
            icons.trim_caches()
            QtGui.QPixmapCache.clear()
        _trim_working_set()

    def _on_restored(self) -> None:
        """Restore the active page lazily when window is restored from minimized."""
        self._was_minimized = False
        target = getattr(self, "_minimized_page", None) or "overlays"
        self._minimized_page = None
        self._select_nav(target)

    def _on_page_evicted(self, key: str) -> None:
        """Per-page cleanup when a page's widget tree is shed."""
        if key == "friends" and hasattr(self, "_friends_timer"):
            self._friends_timer.stop()
        if key == "settings":
            self._settings_toggles = []
            self._hud_toggles = []
            self._minimap_toggles = []
            self._dungeon_toggles = []
            self._sidebar_element_toggles = []
            self._page_element_toggles = []
            self._gather_segs = []
            self._settings_steppers = []
            self._matrix_rows = {}
            self._row_for_attr = {}
            self._expanders = {}
            self._settings_tab_pages = {}
            self._settings_placeholders = {}
            self._master_link = None
            self._dps_mode_cards = {}
            self._dps_mode_select_btns = {}
            for attr in (
                "_dps_hp_est_toggle",
                "_dps_solo_only_toggle",
                "_dps_mode_seg",
                "_dps_view_seg",
                "_dps_range_seg",
                "_dps_diag_pid_lbl",
                "_dps_diag_dll_lbl",
                "_dps_diag_source_lbl",
                "_dps_diag_status_lbl",
                "_settings_stack",
                "_settings_tabs",
            ):
                if hasattr(self, attr):
                    try:
                        delattr(self, attr)
                    except AttributeError:
                        pass
        if key == "combat":
            self._browse_sessions = None
            self._browse_char = ""
            self._selected_history_idx = 0
            self._auto_last_fight = False
            self._auto_last_suppressed = True
