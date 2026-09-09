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

def _forget_attrs(obj, names) -> None:
    """Delete `names` off `obj`, ignoring the ones that are not there.

    Page teardown and page rebuild both clear refs a previous pass may or
    may not have created, so the missing case is normal, not an error.
    """
    for name in names:
        try:
            delattr(obj, name)
        except AttributeError:
            pass


# The Settings page's own local state, and the empty value each is reset to.
# ONE table: the builder and the page teardown both have to cover it, and the
# teardown copy is the one that silently drifts when a builder gains a cache.
_SETTINGS_PAGE_STATE = (
        ("_settings_toggles", list),
        ("_hud_toggles", list),
        ("_top_dps_toggles", list),
        ("_minimap_toggles", list),
        ("_dungeon_toggles", list),
        ("_sidebar_element_toggles", list),
        ("_page_element_toggles", list),
        ("_gather_segs", list),
        ("_settings_steppers", list),
        ("_matrix_rows", dict),
        ("_row_for_attr", dict),
        ("_expanders", dict),
        ("_dps_mode_cards", dict),
        ("_dps_mode_select_btns", dict),
)


def _reset_page_state(obj, spec=_SETTINGS_PAGE_STATE) -> None:
    """Restore every page-local container in `spec` to a fresh empty value."""
    for name, factory in spec:
        setattr(obj, name, factory())


class MemoryReclaimerMixin:
    """Mixin managing LRU page caching, idle cache sweeps, and working-set trimming."""

    _PAGE_CACHE_BUDGET = 2
    _IDLE_AFTER_S = 20
    _PAGE_IDLE_EVICT_S = 120

    @staticmethod
    def _widget_alive(w) -> bool:
        """True when a widget reference still points at a live (non-deleted) Qt object."""
        return w is not None and _is_valid(w)

    def _drop_page_owned_attrs(self, page) -> None:
        """Drop panel attributes that point INTO an evicted page's widget tree.

        The Python references outlive the C++ objects: `deleteLater()` destroys
        the tree on the next event-loop pass, but `self._codex_map_widget` (and
        every other page-local widget attr) keeps pointing at a dead C++ object
        until the page is rebuilt. `hasattr` still says True, so any later call
        into one raises `RuntimeError: Internal C++ object ... already deleted`
        — which is exactly how the combat tick faulted on every character
        change after a Codex page had been evicted. Ownership is resolved by
        identity against the subtree, so no per-page attribute list is needed
        (attrs parented to the panel itself, e.g. a QButtonGroup built with
        `self`, are deliberately left alone for `_on_page_evicted`).
        """
        if page is None:
            return
        owned = {id(o) for o in page.findChildren(QtCore.QObject)}
        owned.add(id(page))
        for name, val in list(vars(self).items()):
            if id(val) in owned:
                try:
                    delattr(self, name)
                except AttributeError:
                    pass

    def _evict_page(self, key: str) -> None:
        """Shed one built page's widget tree (swapped for a fresh placeholder)."""
        order = self._nav_order()
        if key not in order or not self._pages_built.get(key):
            return
        idx = order.index(key)
        w = self.stack.widget(idx)
        if w is not None:
            self._drop_page_owned_attrs(w)
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

    def stop_periodic_timers(self) -> None:
        """Stop every periodic driver the panel owns (called from closeEvent).

        Closing is a shutdown — `closeEvent` detaches from the game — but only
        the attach watcher used to stop with it, so a closed panel went on
        probing for the game (500 ms locate + 2 s attach) and, every 10 s,
        evicting pages and calling `_trim_working_set()` (a gc.collect plus a
        Windows working-set syscall). Anything holding a closed panel burning
        the event loop pays for that: measured 2026-09-21, one panel left
        closed by an earlier test file made each later `processEvents()` drain
        ~2 s slower, which drifted the DPS overlay's live-rate assertion
        (10,000 DPS read back as 4,217) with nothing wrong in the DPS at all.
        """
        for obj, method in ((getattr(self, "attach_ctl", None), "stop"),):
            fn = getattr(obj, method, None)
            if callable(fn):
                try:
                    fn()
                except Exception:
                    pass
        for attr in ("_mem_timer", "_prewarm_timer"):
            timer = getattr(self, attr, None)
            if timer is None:
                continue
            try:
                timer.stop()
            except Exception:
                pass

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
        """Unload every page the user is NOT looking at, plus the sprite
        caches, so memory drops while minimized.

        The page you are ON stays resident (2026-09-26). It used to be evicted
        with the rest, which meant minimizing and restoring rebuilt it from
        scratch every time — the page you had open was the one page guaranteed
        to be destroyed and re-made, losing its scroll position, its selection
        and any state the user had set (an open fold tab, a scrolled list).
        Every OTHER page is still shed, which is where nearly all the memory
        is, and `_on_restored`'s `_select_nav` is a no-op for a page that is
        still marked built, so restoring does no work at all.
        """
        self._was_minimized = True
        self._minimized_page = self._current_page()
        order = self._nav_order()
        # the sub-tab ("settings:dps") is not part of the page key
        cur = (self._minimized_page or "").partition(":")[0]
        for k in order:
            if k != cur and self._pages_built.get(k):
                self._evict_page(k)
        if not (getattr(self, "model", None) is not None and self._live_overlays_active()):
            icons.trim_caches()
            QtGui.QPixmapCache.clear()
        _trim_working_set()

    def _on_restored(self) -> None:
        """Restore the active page lazily when window is restored from minimized.

        A no-op for the common case: the page was deliberately left built by
        `_on_minimized`, so `_select_nav` finds `_pages_built[key]` true and
        only re-points the stack at it. The rebuild here is the fallback for a
        page that was evicted some other way.
        """
        self._was_minimized = False
        target = getattr(self, "_minimized_page", None) or "overlays"
        self._minimized_page = None
        self._select_nav(target)

    def _on_page_evicted(self, key: str) -> None:
        """Per-page cleanup when a page's widget tree is shed."""
        if key == "settings":
            _reset_page_state(self)
            self._dummy_range = None
            self._settings_tab_pages = {}
            self._settings_placeholders = {}
            _forget_attrs(self, (
                "_top_dps_card",
                "_dps_view_seg",
                "_dps_diag_pid_lbl",
                "_dps_diag_dll_lbl",
                "_dps_diag_source_lbl",
                "_dps_diag_status_lbl",
                "_settings_stack",
                "_settings_tabs",
            ))
        if key == "combat":
            self._browse_sessions = None
            self._browse_char = ""
            self._selected_history_idx = 0
            self._auto_last_fight = False
            self._auto_last_suppressed = True
        if key == "pb":
            _forget_attrs(self, (
                "_pb_table", "_pb_empty", "_pb_page",
                "_pb_char_chips", "_pb_dungeon_chips",
                "_pb_char_host", "_pb_dungeon_host",
                "_pb_card_runs", "_pb_card_chars",
                "_pb_card_full", "_pb_card_boss",
            ))
            self._pb_char_opts = []
            self._pb_dungeon_opts = []
            self._pb_all_rows = []
