"""DPS Settings module - split from ui/pages/settings.py.
Imports the single shared namespace (constants, theme, Qt aliases)
from ._shared so moved method bodies keep their original globals.
"""

from ._shared import *  # noqa: F401,F403

from ...memory_reclaimer import _reset_page_state


class SettingsShellMixin:
    """Settings page shell: page lifecycle, lazy tab plumbing and the
cross-tab re-sync (_refresh_settings_page)."""

    _TAB_ORDER = ["Layers", "HUD's", "DPS", "Rift", "Pages", "Dev"]

    _TAB_BUILDERS = ("_tab_layers", "_tab_overlays", "_tab_dps",
                     "_tab_rift", "_tab_pages", "_tab_dev")

    def _page_settings(self):
        page, v = self._page_container()

        _reset_page_state(self)

        self._settings_tabs = C.UnderlineTabs(self._TAB_ORDER)
        v.addWidget(self._settings_tabs)
        # Dev is a developer-run tab (run-log.bat): its content is the env-var
        # switches that session already uses, so a normal run hides the button.
        # The tab stays in _TAB_ORDER and in the stack, which is what keeps
        # every index stable for the lazy builders.
        from ....core.bridge_log import dev_mode
        if not dev_mode():
            self._settings_tabs.set_tab_visible("Dev", False)

        self._settings_stack = QtWidgets.QStackedWidget()
        v.addWidget(self._settings_stack, 1)
        # Each tab is its own scroll area (content-sized): short tabs like Pages
        # sit at their natural height with no scrollbar, tall tabs like the
        # layer matrix scroll inside their own view instead of inflating every
        # other tab (which used to force a blank footer below short tabs).
        #
        # Tabs are built lazily: the page opens showing only the active tab and
        # constructs the rest on first use (then pre-warms them a beat later in
        # the background), so entering Settings — and in particular the large
        # HUD's tab — doesn't pay to build every sub-page up front. Placeholder
        # widgets keep each tab's stack index stable until it is replaced.
        self._settings_tab_pages: dict[str, QtWidgets.QScrollArea] = {}
        self._settings_placeholders: dict[str, QtWidgets.QWidget] = {}
        for i, key in enumerate(self._TAB_ORDER):
            ph = QtWidgets.QWidget()
            self._settings_stack.insertWidget(i, ph)
            self._settings_placeholders[key] = ph
        self._settings_tabs.currentChanged.connect(self._on_settings_tab)

        current = self._settings_tabs.currentText() or self._TAB_ORDER[0]
        self._ensure_settings_tab(current)
        # Pre-warm the other tabs one at a time shortly after the page shows.
        # The timer is parented to this page, so evicting the page (widget tree
        # teardown) stops it automatically.
        self._settings_prebuild_timer = QtCore.QTimer(page)
        self._settings_prebuild_timer.setSingleShot(True)
        self._settings_prebuild_timer.timeout.connect(
            self._prebuild_next_settings_tab)
        self._settings_prebuild_timer.start(150)
        return page

    def _tab_scroll(self, w: QtWidgets.QWidget) -> QtWidgets.QScrollArea:
        return make_scroll(w)

    def _ensure_settings_tab(self, key: str) -> None:
        """Build a Settings sub-tab on first use, swapping out its placeholder
        so the stack index never shifts. Returns quickly when already built."""
        if key not in self._TAB_ORDER or key in self._settings_tab_pages:
            return
        fn = getattr(self, self._TAB_BUILDERS[self._TAB_ORDER.index(key)], None)
        if fn is None:
            return
        w = self._tab_scroll(fn())
        idx = self._TAB_ORDER.index(key)
        cur_w = self._settings_stack.currentWidget()
        self._settings_stack.insertWidget(idx, w)
        ph = self._settings_placeholders.pop(key, None)
        if ph is not None:
            self._settings_stack.removeWidget(ph)
            ph.deleteLater()
        # QStackedWidget tracks a POSITION, not a widget. Inserting at or
        # below the current index slides the current widget along, and
        # removing the placeholder then leaves currentIndex pointing at
        # whatever moved into that slot — so building a tab silently switched
        # pages. It showed up as: opening Settings left the stack on the HUD's
        # placeholder (a blank pane under a "Layers" tab strip) laid out at
        # Qt's default 630x480, and the background pre-warm then painted
        # another tab's content there. Capture the WIDGET and put it back, so
        # only the tab being opened can change what is on screen.
        if cur_w is not None and cur_w is not ph:
            back = self._settings_stack.indexOf(cur_w)
            if back >= 0:
                self._settings_stack.setCurrentIndex(back)
        elif cur_w is ph:
            # the placeholder we just replaced WAS what was showing: land on
            # the tab that replaced it.
            self._settings_stack.setCurrentIndex(idx)
        self._settings_tab_pages[key] = w

    def _prebuild_next_settings_tab(self) -> None:
        """Background pre-warm: build one unopened Settings sub-tab per tick
        (cheap individually, so the UI only stutters briefly off-screen), then
        reschedule while any tab remains. No-ops once they are all built or if
        the page's widget tree was evicted (timer dies with the page)."""
        alive = getattr(self, "_widget_alive", None)
        if alive is not None and not alive(getattr(self, "_settings_stack", None)):
            return
        for key in self._TAB_ORDER:
            if key not in self._settings_tab_pages:
                self._ensure_settings_tab(key)
                break
        for key in self._TAB_ORDER:
            if key not in self._settings_tab_pages:
                self._settings_prebuild_timer.start(50)
                break

    def _on_settings_tab(self, text: str, record_history: bool = True) -> None:
        # Match case-insensitively; "hud", "huds", "hud's", "map", "overlay", "overlays", "entity", "dungeon", "minimap" route to "HUD's"
        if text.lower() in ("hud", "huds", "hud's", "map", "overlay", "overlays", "entity", "dungeon", "minimap"):
            target = "HUD's"
        elif text.lower() in ("layer", "layers", "matrix"):
            target = "Layers"
        elif text.lower() in ("dps", "damage", "combat", "meter", "speedrun", "timer", "run timer"):
            target = "DPS"
        elif text.lower() in ("rift",):
            target = "Rift"
        elif text.lower() in ("page", "pages"):
            target = "Pages"
        elif text.lower() in ("dev", "developer", "diag", "diagnostics"):
            target = "Dev"
        else:
            target = text
        key = next((t for t in self._TAB_ORDER if t.lower() == target.lower()), None)
        if key is not None:
            old_key = getattr(self, "_settings_active_tab", None)
            if not old_key and hasattr(self, "_settings_tabs") and _is_valid(self._settings_tabs):
                old_key = self._settings_tabs.currentText()
            if record_history and old_key and old_key.lower() != key.lower() and hasattr(self, "_nav_history") and not getattr(self._nav_history, "_restoring", False):
                self._nav_history.record(f"settings:{old_key.lower()}", f"settings:{key.lower()}")
            self._settings_active_tab = key
            self._ensure_settings_tab(key)
            if hasattr(self, "_settings_stack") and _is_valid(self._settings_stack):
                self._settings_stack.setCurrentIndex(self._TAB_ORDER.index(key))
            if hasattr(self, "_settings_tabs") and _is_valid(self._settings_tabs) and self._settings_tabs.currentText() != key:
                self._settings_tabs.setCurrentText(key)
            if key == "DPS":
                self._resync_dps_settings_widgets()
                self._resync_dps_mode_widgets()
        # Keep the sidebar RIFT box highlight synced to the active sub-tab.
        if hasattr(self, "_refresh_rift_box_selected"):
            self._refresh_rift_box_selected()

    def _tab_page(self):
        w = QtWidgets.QWidget()
        lv = QtWidgets.QVBoxLayout(w)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(16)
        return w, lv

    def _refresh_settings_page(self) -> None:
        """Re-sync every matrix + section toggle and link state from Settings."""
        for attr, t in getattr(self, "_settings_toggles", []):
            try:
                if t is not None and _is_valid(t):
                    t.set_checked_silent(bool(getattr(self.s, attr)))
            except (RuntimeError, Exception):
                pass
        for attr, t in getattr(self, "_hud_toggles", []):
            try:
                if t is not None and _is_valid(t):
                    t.set_checked_silent(bool(getattr(self.s, attr)))
            except (RuntimeError, Exception):
                pass
        for attr, t in getattr(self, "_top_dps_toggles", []):
            try:
                if t is not None and _is_valid(t):
                    t.set_checked_silent(bool(getattr(self.s, attr)))
            except (RuntimeError, Exception):
                pass
        for attr, t in getattr(self, "_minimap_toggles", []):
            try:
                if t is not None and _is_valid(t):
                    t.set_checked_silent(bool(getattr(self.s, attr)))
            except (RuntimeError, Exception):
                pass
        for attr, t in getattr(self, "_dungeon_toggles", []):
            try:
                if t is not None and _is_valid(t):
                    t.set_checked_silent(bool(getattr(self.s, attr)))
            except (RuntimeError, Exception):
                pass
        for attr, t in getattr(self, "_sidebar_element_toggles", []):
            try:
                if t is not None and _is_valid(t):
                    def_val = False if attr in ("show_log", "show_devicons") else True
                    t.set_checked_silent(bool(getattr(self.s, attr, def_val)))
            except (RuntimeError, Exception):
                pass
        for key, t in getattr(self, "_page_element_toggles", []):
            try:
                if t is not None and _is_valid(t):
                    disabled = set(getattr(self.s, "disabled_pages", []) or [])
                    t.set_checked_silent(key not in disabled)
            except (RuntimeError, Exception):
                pass
        # Row LINK buttons live on the matrix (the MASTER LINK bar is gone),
        # so resync them whenever the page rebuilds its layer toggles.
        if getattr(self, "_matrix_rows", None):
            try:
                self._sync_link_buttons()
            except (RuntimeError, Exception):
                pass
        for seg in getattr(self, "_gather_segs", []):
            try:
                if seg is not None and _is_valid(seg):
                    enabled = set(getattr(self.s, "show_gatherable_types", []))
                    for disp in seg._btns:
                        key = self._gather_disp_to_key.get(disp)
                        if key is not None:
                            seg.set_checked(disp, key in enabled, silent=True)
            except (RuntimeError, Exception):
                pass

