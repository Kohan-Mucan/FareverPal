"""DPS Settings module - split from ui/pages/settings.py.
Imports the single shared namespace (constants, theme, Qt aliases)
from ._shared so moved method bodies keep their original globals.
"""

from ._shared import *  # noqa: F401,F403


class SettingsPagesMixin:
    """Pages tab: per-page on/off tiles and pinned Sidebar Elements."""

    # Pages that can never be switched off here — Settings is the page you're
    # on, Overlays is the main landing slot (disabling it would strand nav).
    # Log and Dev Icons are controlled via the Sidebar Elements section below.
    # PB History is stack-only (DPS Analysis header link), so a on/off tile
    # would be dead UI — same reason log/devicons are skipped.
    _PAGES_ALWAYS_ON = ("settings", "overlays", "log", "devicons", "pb")

    def _tab_pages(self):
        """Per-page on/off switches: a disabled page is never built, so its
        (potentially very large) widget tree and data never load at all."""
        w, lv = self._tab_page()
        lv.setSpacing(14)

        # ---- Application Pages Section -----------------------------------
        lv.addWidget(C.SectionHeader("Application Pages", tag="MODULAR ON/OFF · SAVES RAM"))

        from ...control_panel import NAV
        disabled = set(getattr(self.s, "disabled_pages", []) or [])
        self._page_element_toggles: list[tuple[str, C.ToggleSwitch]] = []

        _PAGE_INFO = {
            "codex": ("layout", "Codex", "Comprehensive database of units, item drops, abilities and spawn zones"),
            "gear": ("box", "Gear", "Equipment analyzer, drop sources, scaling stats and build planner"),
            "craft": ("settings", "Craft", "Crafting recipes, material solver, supply chain and craft list"),
            "party": ("users", "Player Inspect", "Live player inspect: click a name to see buffs and equipped gear"),
            "server": ("broadcast", "Server Ping", "Server latency monitor, ping diagnostics and network metrics"),
        }

        grid = QtWidgets.QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(10)
        idx = 0
        for key, nav_icon, label in NAV:
            if key in self._PAGES_ALWAYS_ON:
                continue
            icon_key, title, subtitle = _PAGE_INFO.get(key, (nav_icon, label, f"Manage {label} page"))
            tile, sw = self._page_tile(key, icon_key, title, subtitle, key not in disabled,
                                       lambda on, k=key: self._on_page_toggle(k, on))
            self._page_element_toggles.append((key, sw))
            grid.addWidget(tile, idx // 2, idx % 2)
            idx += 1
        span_trailing_row(grid)
        lv.addLayout(grid)
        lv.addSpacing(6)

        # ---- Sidebar Pinned Elements -------------------------------------
        self._build_sidebar_elements(lv)
        # This tab is the only one whose content is shorter than the viewport,
        # so it needs the trailing stretch the other builders end with. Without
        # it QVBoxLayout has nowhere to put the leftover height and hands it
        # to the SectionHeaders instead: they measured 186px tall for a 20px
        # one-line title, which is the dead band that sat between the header
        # and the first tile on both sections.
        lv.addStretch(1)
        return w

    def _page_tile(self, key: str, icon_name: str, title: str, subtitle: str,
                   checked: bool = False, on_toggled=None,
                   control: QtWidgets.QWidget | None = None,
                   ) -> tuple[QtWidgets.QFrame, QtWidgets.QWidget]:
        """A modern tactical tile for a navigation page.

        `control` replaces the on/off switch for a tile whose setting is not a
        boolean (Dev's three-state update check) and is what gets returned in
        the switch's place, so a caller always gets back the widget it has to
        keep in sync - `set_checked_silent` for a switch, `setCurrentText` for
        the segmented control.
        """
        card = QtWidgets.QFrame()
        card.setObjectName("PageTile")
        card.setStyleSheet(
            f"QFrame#PageTile {{ background-color: {theme.PANEL}; "
            f"border: 1px solid {theme.BORDER}; border-radius: 6px; }}"
            f"QFrame#PageTile:hover {{ border-color: {theme.with_alpha(theme.ACCENT, 90)}; }}")
        lay = QtWidgets.QHBoxLayout(card)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(12)

        # Icon box
        ic_box = QtWidgets.QFrame()
        ic_box.setFixedSize(38, 38)
        ic_box.setStyleSheet(
            f"background-color: {theme.with_alpha(theme.ACCENT, 20)}; "
            f"border: 1px solid {theme.with_alpha(theme.ACCENT, 60)}; border-radius: 4px;")
        ic_lay = QtWidgets.QVBoxLayout(ic_box)
        ic_lay.setContentsMargins(0, 0, 0, 0)
        ic_lay.setAlignment(QtCore.Qt.AlignCenter)

        ic_lbl = QtWidgets.QLabel()
        ic_lbl.setFixedSize(20, 20)
        ic_lbl.setPixmap(icons.ui_icon(icon_name, theme.ACCENT, 20) or icons.marker("rift", 20, theme.ACCENT))
        ic_lay.addWidget(ic_lbl)
        lay.addWidget(ic_box, 0, QtCore.Qt.AlignVCenter)

        # Text Column
        text_col = QtWidgets.QVBoxLayout()
        text_col.setSpacing(3)
        title_lbl = QtWidgets.QLabel(title)
        title_lbl.setStyleSheet(
            f"font-size:15px;font-weight:700;color:{theme.TEXT};background:transparent;")
        sub_lbl = QtWidgets.QLabel(subtitle)
        sub_lbl.setStyleSheet(
            f"font-size:13px;color:{theme.MUTED};background:transparent;line-height:1.3;")
        sub_lbl.setWordWrap(True)
        text_col.addWidget(title_lbl)
        text_col.addWidget(sub_lbl)
        lay.addLayout(text_col, 1)

        # The switch, or the caller's own control when the setting has more
        # than two states.
        if control is not None:
            lay.addWidget(control, 0, QtCore.Qt.AlignVCenter)
            return card, control
        sw = C.ToggleSwitch(checked)
        sw.toggled.connect(on_toggled)
        lay.addWidget(sw, 0, QtCore.Qt.AlignVCenter)
        return card, sw

    def _on_page_toggle(self, key: str, on: bool) -> None:
        self._set_page_disabled(key, not on)

    def _build_sidebar_elements(self, v) -> None:
        """Sidebar elements that aren't full pages — the rift schedule card,
        Launch Game button, and pinned shortcuts like Log & Dev Icons."""
        v.addWidget(C.SectionHeader("Sidebar Elements", tag="PINNED SHORTCUTS"))

        from ...control_panel import dev_icons_enabled
        _SIDEBAR_INFO = [
            ("show_rift_box", "rift", "Rift Schedule Card", "Live rift countdown timer and portal radar in the sidebar", True),
            ("show_launch_button", "play", "Launch Game Button", "Quick start button to launch the Farever game client", True),
            ("show_log", "terminal", "Log Console Button", "Diagnostic log console shortcut button in the sidebar footer", False),
        ]
        if dev_icons_enabled():
            _SIDEBAR_INFO.append(
                ("show_devicons", "eye", "Dev Icons", "Development icon atlas preview and asset inspector", False)
            )

        grid = QtWidgets.QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(10)
        self._sidebar_element_toggles: list[tuple[str, C.ToggleSwitch]] = []
        for i, (attr, icon_name, label, desc, default_val) in enumerate(_SIDEBAR_INFO):
            val = bool(getattr(self.s, attr, default_val))
            tile, sw = self._page_tile(attr, icon_name, label, desc, val,
                                       lambda on, a=attr: self._on_sidebar_element_toggle(a, on))
            self._sidebar_element_toggles.append((attr, sw))
            grid.addWidget(tile, i // 2, i % 2)
        span_trailing_row(grid)
        v.addLayout(grid)

    def _on_sidebar_element_toggle(self, attr: str, on: bool) -> None:
        """One of the Sidebar Elements switches: persist and apply live."""
        self._set(attr, on)
        if hasattr(self, "_apply_sidebar_element_visibility"):
            self._apply_sidebar_element_visibility()

