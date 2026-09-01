"""Settings page — the new consolidated overlay hub.

Built incrementally from the World-page mockup (tmp_preview/
entity_map_merge_mockup.html): every overlay's visibility layers in one
matrix with HUD / Minimap / Dungeon columns side by side, so the six layers
shared between the Entity HUD and the minimap are configured once. The old
Entity and Map pages stay untouched until every section is ported here —
sections not moved yet keep their header with a "coming next" note.

Linking: rows with both a HUD and a MAP switch can be linked (chain button,
or the MASTER bar above the matrix) so flipping one drives the other.
"""
from __future__ import annotations

import os
from PySide6 import QtCore, QtGui, QtWidgets
from shiboken6 import isValid as _is_valid

from .. import components as C
from .. import theme
from ..layout import make_scroll, StretchFlow
from ...data import icons, names, units
from ...geo.gatherables import get_display_name
from ...core.updater import is_frozen
from ...sound import play_ding, play_sound, list_sounds, SOUND_LABELS

_CHIP_COLS = 3      # filter chip grid columns

# (group label, [(key, layer, color, hud_attr, map_attr, dungeon_attr)])
# None attr = that surface has no such layer (a dash is rendered instead).
_LAYER_GROUPS = [
    ("HUD & MINIMAP", [
        ("enemies", "Enemies", theme.DANGER,
         "show_enemies", "minimap_enemies", None),
        ("spark_mobs", "Spark Mobs", theme.ORANGE,
         "show_spark_mobs", "minimap_spark_mobs", None),
        ("companions", "Companions", "#7aa2f7",
         "show_companions", "minimap_companions", None),
        ("chests", "Chests", theme.GOLD,
         "show_chests", "minimap_chests", None),
        ("gatherables", "Gatherables", theme.GOOD,
         "show_gatherables", "minimap_gatherables", None),
        ("orbs", "Secret orbs", theme.KIND_COLOR["orb"],
         "show_orbs", "minimap_orbs", "dungeon_show_orbs"),
    ]),
    ("MAP POINTS OF INTEREST", [
        ("obelisks", "Obelisks / Respawn", theme.KIND_COLOR["obelisk"],
         None, "minimap_obelisks", None),
        ("dungeons", "Dungeons", theme.KIND_COLOR["dungeon"],
         None, "minimap_dungeons", None),
        ("vendors", "Vendors", theme.KIND_COLOR["vendor"],
         None, "minimap_vendors", None),
        ("soulstones", "Soulstone Bosses", theme.KIND_COLOR["soulstone"],
         None, "minimap_soulstones", None),
    ]),
    ("DUNGEON HUD", [
        ("players", "Players", theme.GOOD,
         "show_group_members", "minimap_players", "dungeon_show_players"),
        ("loot", "Dropped loot", theme.KIND_COLOR["activity"],
         "show_loot", None, "dungeon_show_loots"),
    ]),
]

# Extra per-layer options revealed by clicking a matrix row's name.
# key -> [(attr, label, lo, hi, step)]
_LAYER_EXTRA = {
    "gatherables": [
        ("gatherable_count", "Gatherables shown", 1, 30, 1),
    ],
}

# Gatherable type picker shown when the Gatherables matrix row is expanded.
# (group label, [type_key, ...]) — display names come from geo.gatherables.
# Flowers vs Ore (rendered as ore-orb nodes) so the long flat list folds away.
_GATHER_GROUPS = [
    ("Flowers", ["lavendula", "madrigold", "zealotus", "ancientthyme"]),
    ("Ore", ["copperore", "tinore", "tungstene"]),
]

# Layers drawn from the single master glyph recolored to the layer's accent
# (same approach as the minimap's ore rendering in minimap_render).
_RECOLOR_GLYPHS = {
    "gatherables": "ore",
}

# Real atlas sprite (assets/atlas/atlas_minimap_01.webp) for each layer row
# that has one — rendered via icons.marker() with the layer color outline, so
# a missing sheet still degrades to a plain color dot. Layers without a
# suitable sprite (enemies, companions, players, loot) keep the dot.
_LAYER_SPRITES = {
    "enemies": "sword",
    "spark_mobs": "sparkdust",
    "companions": "heart",        # overridden by the cat emoji below
    "chests": "chest",
    "gatherables": "gatherable",  # recolored to the ore glyph (see _RECOLOR_GLYPHS)
    "orbs": "orb",
    "obelisks": "respawnpoint",
    "dungeons": "dungeon",
    "vendors": "shop",
    "soulstones": "soulstone",
    "players": "player",
    "loot": "box",
}

# A few layers have no matching atlas sprite, so they get an inline glyph/emoji
# drawn in the row's accent color instead (cheaper than a one-off SVG).
_LAYER_EMOJI = {
    "companions": "😺",
}

_LAYER_DESCS = {
    "enemies": "Hostile enemy radar, level & health bars",
    "spark_mobs": "Special spark dust elite mob tracking",
    "companions": "Tamed pets, companions & summon minions",
    "chests": "Treasure chests, loot containers & locks",
    "gatherables": "Harvestable flowers, herbs & ore nodes",
    "orbs": "Secret realm discovery orbs",
    "obelisks": "Fast travel waypoints & respawn beacons",
    "dungeons": "Instanced dungeon & raid portal entrances",
    "vendors": "NPC merchants, Mounts, Gliders, Pets, Recipe shops",
    "soulstones": "Soulstone Boss arenas & summon shrines",
    "players": "Multiplayer co-op party member radar",
    "loot": "Ground item drops & gear loot beacons",
}



# Rarity floor for both LOOT sections: Off / All show every drop, the rest
# hide anything below the picked floor (matches loot_rows.build_loot_specs).
_LOOT_OPTS = ["Off", "All", "Uncommon+", "Rare+", "Epic+", "Legendary"]

_LOOT_COLORS = {
    "Off": theme.MUTED,
    "All": theme.TEXT,
    "Common+": theme.RARITY.get("Common", "#c2c6d0"),
    "Uncommon+": theme.RARITY.get("Uncommon", "#56d364"),
    "Rare+": theme.RARITY.get("Rare", "#539bf5"),
    "Epic+": theme.RARITY.get("Epic", "#c297ff"),
    "Legendary": theme.RARITY.get("Legendary", "#f0a836"),
}


class _LinkButton(QtWidgets.QPushButton):
    """Chain toggle for one matrix row — accent when the row is linked."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setFixedSize(26, 22)
        self.setObjectName("Icon")
        self.toggled.connect(self._restyle)
        self._restyle()

    def _restyle(self) -> None:
        on = self.isChecked()
        col = theme.ACCENT if on else theme.DIM
        self.setIcon(QtGui.QIcon(icons.ui_icon("link", col, 15)))
        self.setIconSize(QtCore.QSize(15, 15))
        self.setToolTip(
            "Linked — flipping HUD also flips Map" if on
            else "Link this layer's HUD and Map switches")


class SettingsPageMixin:
    _TAB_ORDER = ["Layers", "HUD's", "DPS", "Rift", "Pages"]
    _TAB_BUILDERS = ("_tab_layers", "_tab_overlays", "_tab_dps",
                     "_tab_rift", "_tab_pages")

    # Pages that can never be switched off here — Settings is the page you're
    # on, Overlays is the main landing slot (disabling it would strand nav).
    # Log and Dev Icons are controlled via the Sidebar Elements section below.
    _PAGES_ALWAYS_ON = ("settings", "overlays", "log", "devicons")

    def _page_settings(self):
        page, v = self._page_container()

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
        self._master_link = None
        self._dps_mode_cards = {}
        self._dps_mode_select_btns = {}

        self._settings_tabs = C.UnderlineTabs(self._TAB_ORDER)
        v.addWidget(self._settings_tabs)

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
        self._settings_stack.insertWidget(idx, w)
        ph = self._settings_placeholders.pop(key, None)
        if ph is not None:
            self._settings_stack.removeWidget(ph)
            ph.deleteLater()
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

    def _on_settings_tab(self, text: str) -> None:
        # Match case-insensitively; "hud", "huds", "hud's", "map", "overlay", "overlays", "entity", "dungeon", "minimap" route to "HUD's"
        if text.lower() in ("hud", "huds", "hud's", "map", "overlay", "overlays", "entity", "dungeon", "minimap"):
            target = "HUD's"
        elif text.lower() in ("layer", "layers", "matrix"):
            target = "Layers"
        elif text.lower() in ("dps", "damage", "combat", "meter"):
            target = "DPS"
        elif text.lower() in ("rift",):
            target = "Rift"
        elif text.lower() in ("page", "pages"):
            target = "Pages"
        else:
            target = text
        key = next((t for t in self._TAB_ORDER if t.lower() == target.lower()), None)
        if key is not None:
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

    def _tab_layers(self):
        w, lv = self._tab_page()
        self._build_layer_matrix(lv)
        lv.addStretch(1)
        return w

    def _tab_overlays(self):
        w, lv = self._tab_page()
        lv.setSpacing(12)

        self._loot_segs = []
        if not hasattr(self, "_settings_steppers") or self._settings_steppers is None:
            self._settings_steppers = []

        cols = QtWidgets.QHBoxLayout()
        cols.setSpacing(16)
        cols.setAlignment(QtCore.Qt.AlignTop)

        # Left Column: Entity HUD
        left = QtWidgets.QVBoxLayout()
        left.setSpacing(14)
        self._entity_card = self._build_entity_hud_card()
        left.addWidget(self._entity_card)
        left.addStretch(1)

        # Right Column: Dungeon HUD + Minimap Radar
        right = QtWidgets.QVBoxLayout()
        right.setSpacing(14)
        self._dungeon_card = self._build_dungeon_hud_card()
        self._minimap_card = self._build_minimap_card()
        right.addWidget(self._dungeon_card)
        right.addWidget(self._minimap_card)
        right.addStretch(1)

        cols.addLayout(left, 1)
        cols.addLayout(right, 1)
        lv.addLayout(cols)
        return w

    def _tab_rift(self):
        w, lv = self._tab_page()
        self._build_rift(lv)
        lv.addStretch(1)
        return w

    def _tab_pages(self):
        """Per-page on/off switches: a disabled page is never built, so its
        (potentially very large) widget tree and data never load at all."""
        w, lv = self._tab_page()
        lv.setSpacing(14)

        # ---- Application Pages Section -----------------------------------
        lv.addWidget(C.SectionHeader("Application Pages", tag="MODULAR ON/OFF · SAVES RAM"))

        from ..control_panel import NAV
        disabled = set(getattr(self.s, "disabled_pages", []) or [])
        self._page_element_toggles: list[tuple[str, C.ToggleSwitch]] = []

        _PAGE_INFO = {
            "codex": ("layout", "Codex", "Comprehensive database of units, item drops, abilities and spawn zones"),
            "speedrun": ("timer", "Speedrun", "Automated boss split timer, room checkpoints and personal records"),
            "gear": ("box", "Gear", "Equipment analyzer, drop sources, scaling stats and build planner"),
            "craft": ("settings", "Craft", "Crafting recipes, material solver, vendor blueprints and ingredients"),
            "collection": ("archive", "Collection", "Achievement tracker, secret relics and collectible completion"),
            "friends": ("users", "Friends", "Online party members, friend list status and coordinate sync"),
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
        lv.addLayout(grid)
        lv.addSpacing(6)

        # ---- Sidebar Pinned Elements -------------------------------------
        self._build_sidebar_elements(lv)
        return w

    def _page_tile(self, key: str, icon_name: str, title: str, subtitle: str,
                   checked: bool, on_toggled) -> tuple[QtWidgets.QFrame, C.ToggleSwitch]:
        """A modern tactical tile for a navigation page."""
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

        # Switch
        sw = C.ToggleSwitch(checked)
        sw.toggled.connect(on_toggled)
        lay.addWidget(sw, 0, QtCore.Qt.AlignVCenter)
        return card, sw

    def _on_page_toggle(self, key: str, on: bool) -> None:
        self._set_page_disabled(key, not on)

    def _build_sidebar_elements(self, v) -> None:
        """Sidebar elements that aren't full pages — the rift schedule card,
        Launch Game button, account row, and pinned shortcuts like Log & Dev Icons."""
        v.addWidget(C.SectionHeader("Sidebar Elements", tag="PINNED SHORTCUTS"))

        from ..control_panel import dev_icons_enabled
        _SIDEBAR_INFO = [
            ("show_rift_box", "rift", "Rift Schedule Card", "Live rift countdown timer and portal radar in the sidebar", True),
            ("show_launch_button", "play", "Launch Game Button", "Quick start button to launch the Farever game client", True),
            ("show_account_button", "users", "Account / Sign In Row", "Legacy FareverPal website cloud synchronization sign-in row", False),
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
        v.addLayout(grid)

    def _on_sidebar_element_toggle(self, attr: str, on: bool) -> None:
        """One of the Sidebar Elements switches: persist and apply live."""
        self._set(attr, on)
        if hasattr(self, "_apply_sidebar_element_visibility"):
            self._apply_sidebar_element_visibility()

    # --- world layers matrix (2-column tactical cards) --------------------
    def _switch_with_label(self, name: str, toggle: QtWidgets.QWidget) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        hl = QtWidgets.QHBoxLayout(w)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(5)
        lbl = QtWidgets.QLabel(name)
        lbl.setStyleSheet(
            f"font-family:'{theme.MONO_FONT}','Consolas',monospace;font-size:10px;"
            f"font-weight:700;letter-spacing:0.5px;color:{theme.MUTED};background:transparent;")
        hl.addWidget(lbl)
        hl.addWidget(toggle)
        return w

    def _build_layer_matrix(self, v) -> None:
        # --- MASTER link bar ---------------------------------------------
        master_bar = QtWidgets.QHBoxLayout()
        master_bar.setSpacing(10)
        self._master_link = QtWidgets.QPushButton("MASTER LINK")
        self._master_link.setCheckable(True)
        self._master_link.setCursor(QtCore.Qt.PointingHandCursor)
        self._master_link.setStyleSheet(
            f"QPushButton {{ background: {theme.with_alpha(theme.ACCENT, 14)}; "
            f"border: 1px solid {theme.ACCENT}; border-radius: 4px; "
            f"color: {theme.ACCENT}; font-size: 10px; font-weight: 800; "
            f"letter-spacing: 1px; padding: 4px 12px; }}"
            f"QPushButton:checked {{ background: {theme.ACCENT}; "
            f"color: {theme.BG}; }}"
            f"QPushButton:hover {{ background: {theme.with_alpha(theme.ACCENT, 26)}; }}")
        self._master_link.toggled.connect(self._set_master_link)
        master_bar.addWidget(self._master_link)
        master_lbl = QtWidgets.QLabel(
            "Synchronize every HUD layer with its Minimap Radar layer")
        master_lbl.setStyleSheet(
            f"color: {theme.MUTED}; font-size: 11px; background: transparent;")
        master_bar.addWidget(master_lbl, 1)
        v.addLayout(master_bar)
        v.addSpacing(4)

        # 2-Column Tactical Layer Cards Grid
        grid = QtWidgets.QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(10)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)

        self._settings_toggles: list[tuple[str, C.ToggleSwitch]] = []
        self._matrix_rows: dict[str, dict] = {}
        self._row_for_attr: dict[str, dict] = {}
        self._expanders: dict[str, tuple] = {}

        grid_row = 0
        for group, layers in _LAYER_GROUPS:
            g_lbl = QtWidgets.QLabel(f"// {group.upper()}")
            g_lbl.setStyleSheet(
                f"color:{theme.ACCENT};font-family:'{theme.MONO_FONT}',"
                "'Consolas',monospace;font-size:11px;font-weight:700;"
                "letter-spacing:1.5px;background:transparent;padding-top:8px;padding-bottom:2px;")
            grid.addWidget(g_lbl, grid_row, 0, 1, 2)
            grid_row += 1

            for i, (key, label, color, hud_a, map_a, dg_a) in enumerate(layers):
                card = QtWidgets.QFrame()
                card.setObjectName("LayerCard")
                card.setStyleSheet(
                    f"QFrame#LayerCard {{ background-color: {theme.PANEL}; "
                    f"border: 1px solid {theme.BORDER}; border-radius: 6px; }}"
                    f"QFrame#LayerCard:hover {{ border-color: {theme.with_alpha(theme.ACCENT, 90)}; }}")
                card_lay = QtWidgets.QVBoxLayout(card)
                card_lay.setContentsMargins(12, 10, 12, 10)
                card_lay.setSpacing(6)

                # Main Row: Icon + Title/Desc + Controls
                top_row = QtWidgets.QHBoxLayout()
                top_row.setSpacing(10)

                # Icon in rounded badge
                icon_badge = QtWidgets.QFrame()
                icon_badge.setFixedSize(30, 30)
                icon_badge.setStyleSheet(
                    f"background: {theme.with_alpha(color, 25)}; "
                    f"border: 1px solid {theme.with_alpha(color, 80)}; border-radius: 4px;")
                ib_lay = QtWidgets.QHBoxLayout(icon_badge)
                ib_lay.setContentsMargins(0, 0, 0, 0)
                ib_lay.setAlignment(QtCore.Qt.AlignCenter)
                ic = self._layer_icon(key, color)
                ib_lay.addWidget(ic)
                top_row.addWidget(icon_badge)

                # Title & Description
                text_lay = QtWidgets.QVBoxLayout()
                text_lay.setSpacing(1)
                nm = QtWidgets.QLabel(label)
                nm.setStyleSheet(
                    f"color:{theme.TEXT};font-size:15px;font-weight:700;background:transparent;")
                desc = QtWidgets.QLabel(_LAYER_DESCS.get(key, ""))
                desc.setStyleSheet(
                    f"color:{theme.MUTED};font-size:11px;background:transparent;")
                desc.setWordWrap(True)
                text_lay.addWidget(nm)
                text_lay.addWidget(desc)
                top_row.addLayout(text_lay, 1)

                # Switch controls
                ctrl_lay = QtWidgets.QHBoxLayout()
                ctrl_lay.setSpacing(8)
                ctrl_lay.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)

                hud_t = self._layer_toggle(hud_a) if hud_a else None
                map_t = self._layer_toggle(map_a) if map_a else None
                dg_t = self._layer_toggle(dg_a) if dg_a else None

                if hud_t:
                    ctrl_lay.addWidget(self._switch_with_label("HUD", hud_t))
                if map_t:
                    ctrl_lay.addWidget(self._switch_with_label("MAP", map_t))
                if dg_t:
                    ctrl_lay.addWidget(self._switch_with_label("DUNGEON", dg_t))

                # Rows that exist on both HUD and Minimap get a per-row LINK
                # button so flipping one surface mirrors the other.
                link_btn = None
                if hud_a and map_a:
                    link_btn = QtWidgets.QPushButton("LINK")
                    link_btn.setCheckable(True)
                    link_btn.setCursor(QtCore.Qt.PointingHandCursor)
                    link_btn.setFixedWidth(44)
                    link_btn.setStyleSheet(
                        f"QPushButton {{ background: transparent; "
                        f"border: 1px solid {theme.BORDER}; border-radius: 3px; "
                        f"color: {theme.DIM}; font-size: 9px; font-weight: 800; "
                        f"letter-spacing: 0.5px; padding: 2px 0; }}"
                        f"QPushButton:checked {{ border-color: {theme.ACCENT}; "
                        f"color: {theme.ACCENT}; }}"
                        f"QPushButton:hover {{ border-color: {theme.MUTED}; }}")
                    link_btn.toggled.connect(
                        lambda on, k=key: self._on_row_link(k, on))
                    ctrl_lay.addWidget(link_btn)

                top_row.addLayout(ctrl_lay)
                card_lay.addLayout(top_row)

                self._matrix_rows[key] = {
                    "key": key,
                    "icon": ic,
                    "hud_attr": hud_a,
                    "map_attr": map_a,
                    "hud": hud_t,
                    "map": map_t,
                    "link": link_btn,
                    "linkable": bool(hud_a and map_a),
                }
                if hud_a:
                    self._row_for_attr[hud_a] = self._matrix_rows[key]
                if map_a:
                    self._row_for_attr[map_a] = self._matrix_rows[key]

                # Gatherables: collapsible per-type picker
                if key == "gatherables":
                    exp_btn = QtWidgets.QPushButton("▾ Filter Herbs & Ores")
                    exp_btn.setCursor(QtCore.Qt.PointingHandCursor)
                    exp_btn.setStyleSheet(
                        f"QPushButton {{ background: transparent; border: none; color: {theme.GOLD}; "
                        f"font-size: 11px; font-weight: 700; text-align: left; padding: 2px 0px; }}"
                        f"QPushButton:hover {{ color: {theme.TEXT}; }}")
                    exp_btn.clicked.connect(lambda: self._toggle_gather_expander())
                    card_lay.addWidget(exp_btn)
                    self._gather_caret = exp_btn

                    self._gather_expander = self._build_gatherable_expander()
                    self._gather_expander.setVisible(False)
                    card_lay.addWidget(self._gather_expander)

                grid.addWidget(card, grid_row + (i // 2), i % 2)

            grid_row += (len(layers) + 1) // 2

        v.addLayout(grid)
        v.addSpacing(6)
        self._sync_link_buttons()

        # Callout summary card
        info_box = QtWidgets.QFrame()
        info_box.setObjectName("Cell")
        info_box.setStyleSheet(
            f"QFrame#Cell {{ background-color: {theme.with_alpha(theme.PANEL_HI, 30)}; "
            f"border: 1px solid {theme.BORDER}; border-radius: 6px; }}")
        info_lay = QtWidgets.QVBoxLayout(info_box)
        info_lay.setContentsMargins(12, 10, 12, 10)
        info_lay.setSpacing(4)

        info_hdr = QtWidgets.QLabel("LAYER SYNCHRONIZATION & BEHAVIOR")
        info_hdr.setStyleSheet(
            f"font-family:'{theme.MONO_FONT}','Consolas',monospace;font-size:11px;"
            f"font-weight:700;letter-spacing:1px;color:{theme.GOLD};background:transparent;")
        info_lay.addWidget(info_hdr)

        note = QtWidgets.QLabel(
            "• Overlay Switches: Toggle HUD, Minimap Radar, and Dungeon tracking independently per layer.\n"
            "• Gatherable Filtering: Expand/Collapse to filter individual Flower & Ore types.\n"
            "• Instance HUD: Dungeon HUD automatically activates inside instanced dungeons and rifts.")
        note.setStyleSheet(f"color:{theme.TEXT};font-size:13px;line-height:1.5;background:transparent;")
        note.setWordWrap(True)
        info_lay.addWidget(note)
        v.addWidget(info_box)

    # --- gatherables expander ---------------------------------------------
    def _build_gatherable_expander(self) -> QtWidgets.QWidget:
        """Per-type picker revealed by clicking the Gatherables matrix row.

        Flowers and Ore (ore-orb nodes) are each a smooth multi-select segmented
        control (same flat feel as the Entity Loot Filter); any number of types
        can be on. A type is drawn only when its key is present in
        `show_gatherable_types` AND the Gatherables HUD/Map switch is on."""
        box = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(box)
        lay.setContentsMargins(10, 4, 10, 4)
        lay.setSpacing(8)
        enabled = set(getattr(self.s, "show_gatherable_types", []))
        self._gather_segs: list[C.MultiSegmentedControl] = []
        self._gather_disp_to_key: dict[str, str] = {}
        for grp, keys in _GATHER_GROUPS:
            hdr = QtWidgets.QLabel(grp)
            hdr.setStyleSheet(
                f"color:{theme.ACCENT};font-family:'{theme.MONO_FONT}',"
                "'Consolas',monospace;font-size:10px;font-weight:700;"
                "letter-spacing:2px;background:transparent;")
            lay.addWidget(hdr)
            opts = [get_display_name(k) for k in keys]
            col_map = {}
            for k in keys:
                disp = get_display_name(k)
                col_map[disp] = theme.FLOWER_COLOR.get(k.lower(), theme.GOOD if grp == "Flowers" else theme.ORANGE)
            seg = C.MultiSegmentedControl(opts, colors=col_map)
            for k in keys:
                disp = get_display_name(k)
                seg.set_checked(disp, k in enabled, silent=True)
                self._gather_disp_to_key[disp] = k
            seg.optionToggled.connect(
                lambda o, on: self._on_gather_type_toggle(
                    self._gather_disp_to_key[o], on))
            lay.addWidget(seg)
            self._gather_segs.append(seg)
        return box

    def _toggle_gather_expander(self) -> None:
        if not hasattr(self, "_gather_expander"):
            return
        vis = not self._gather_expander.isVisible()
        self._gather_expander.setVisible(vis)
        if hasattr(self, "_gather_caret"):
            self._gather_caret.setText("▴ Hide Herbs & Ores" if vis else "▾ Filter Herbs & Ores")

    def _on_gather_type_toggle(self, key: str, on: bool) -> None:
        cur = list(getattr(self.s, "show_gatherable_types", []))
        if on and key not in cur:
            cur.append(key)
        elif not on and key in cur:
            cur.remove(key)
        # Keep the on-disk list in canonical ALL_GATHER_TYPES order.
        from ...config import ALL_GATHER_TYPES
        self._set("show_gatherable_types",
                  [t for t in ALL_GATHER_TYPES if t in cur])

    def _layer_icon(self, key: str, color: str) -> QtWidgets.QLabel:
        """Leading cell for a matrix row: the real atlas_minimap_01.webp
        sprite with a `color` outline; falls back to the loose icons folder
        (assets/icons); a red dot marks a layer whose icon is missing so the
        gap is obvious instead of silently blending in."""
        lbl = QtWidgets.QLabel()
        lbl.setFixedSize(16, 16)
        lbl.setAlignment(QtCore.Qt.AlignCenter)
        glyph = _RECOLOR_GLYPHS.get(key)
        if glyph:
            pm = icons.ui_icon(glyph, color, 16)
            if pm and not pm.isNull():
                lbl.setPixmap(pm)
                return lbl
        emoji = _LAYER_EMOJI.get(key)
        if emoji:
            lbl.setPixmap(icons.emoji_pixmap(emoji, 16, color))
            return lbl
        sprite = _LAYER_SPRITES.get(key)
        if sprite and icons.has_icon("minimap", sprite):
            lbl.setPixmap(icons.marker(sprite, 16, color))
            return lbl
        if sprite:
            fb = icons.asset_icon(sprite, 16)
            if fb and not fb.isNull():
                lbl.setPixmap(fb)
                return lbl
        pm = QtGui.QPixmap(16, 16)
        pm.fill(QtGui.QColor(0, 0, 0, 0))
        p = QtGui.QPainter(pm)
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(QtGui.QColor(color or theme.ACCENT))
        p.drawEllipse(3, 3, 10, 10)
        p.end()
        lbl.setPixmap(pm)
        return lbl

    # --- layer switches ---------------------------------------------------
    def _dash(self) -> QtWidgets.QLabel:
        d = QtWidgets.QLabel("—")
        d.setAlignment(QtCore.Qt.AlignCenter)
        d.setStyleSheet(
            f"color:{theme.DIM};font-size:12px;background:transparent;")
        return d

    def _layer_toggle(self, attr):
        """A HUD / MAP / DUNGEON switch for one layer. Dashes when the surface
        has no such layer. Minimap toggles also refresh the minimap canvas."""
        if attr is None:
            return self._dash()
        t = C.ToggleSwitch(bool(getattr(self.s, attr)))
        t.toggled.connect(lambda on, a=attr: self._on_layer_toggle(a, on))
        self._settings_toggles.append((attr, t))
        return t

    def _on_layer_toggle(self, attr: str, on: bool) -> None:
        if attr.startswith("minimap_"):
            self._set_minimap_layer(attr, on)
        else:
            self._set(attr, on)
        # Mirror to the row's other switch when this row is linked.
        row = self._row_for_attr.get(attr)
        if row is None or not row["linkable"]:
            return
        linked = bool(self.s.layer_link_master) \
            or row["key"] in getattr(self.s, "layer_links", [])
        if not linked:
            return
        if attr == row["hud_attr"]:
            self._set_minimap_layer(row["map_attr"], on)
            row["map"].set_checked_silent(on)
        else:
            self._set(row["hud_attr"], on)
            row["hud"].set_checked_silent(on)

    # --- linking ----------------------------------------------------------
    def _set_master_link(self, on: bool) -> None:
        self._set("layer_link_master", on)
        linkable = [k for k, r in self._matrix_rows.items() if r["linkable"]]
        self._set("layer_links", sorted(linkable) if on else [])
        if on:
            # The minimap immediately matches the HUD for every linked row.
            for r in self._matrix_rows.values():
                if r["linkable"] and r["map_attr"]:
                    state = bool(getattr(self.s, r["hud_attr"]))
                    self._set_minimap_layer(r["map_attr"], state)
                    r["map"].set_checked_silent(state)
        self._sync_link_buttons()

    def _on_row_link(self, key: str, on: bool) -> None:
        if self.s.layer_link_master:
            return  # master owns every link while it is on
        keys = list(getattr(self.s, "layer_links", []))
        if on and key not in keys:
            keys.append(key)
        elif not on and key in keys:
            keys.remove(key)
        self._set("layer_links", sorted(keys))
        self._update_link_status()

    def _sync_link_buttons(self) -> None:
        master = bool(self.s.layer_link_master)
        links = set(getattr(self.s, "layer_links", []))
        ml = getattr(self, "_master_link", None)
        if ml is not None and _is_valid(ml):
            try:
                ml.blockSignals(True)
                ml.setChecked(master)
                ml.blockSignals(False)
                if hasattr(self, "_restyle_master_btn"):
                    self._restyle_master_btn(master)
            except (RuntimeError, Exception):
                pass
        for key, r in getattr(self, "_matrix_rows", {}).items():
            b = r.get("link")
            if b is None or not _is_valid(b):
                continue
            try:
                b.blockSignals(True)
                b.setChecked(master or key in links)
                b.setEnabled(not master)
                b.blockSignals(False)
                if hasattr(b, "_restyle"):
                    b._restyle()
            except (RuntimeError, Exception):
                pass
        self._update_link_status()

    def _update_link_status(self) -> None:
        ml = getattr(self, "_master_link", None)
        if ml is not None and _is_valid(ml):
            try:
                n = len(getattr(self.s, "layer_links", []))
                if self.s.layer_link_master:
                    ml.setToolTip(f"MASTER link ON ({n} rows synchronized) — click to disable")
                elif n:
                    ml.setToolTip(f"{n} row(s) linked — click to enable MASTER link")
                else:
                    ml.setToolTip("Click to enable MASTER link (syncs all HUD and Map layers)")
            except (RuntimeError, Exception):
                pass

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
        ml = getattr(self, "_master_link", None)
        if ml is not None and _is_valid(ml):
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

    def _overlay_card_frame(self, title: str, tag: str = "", sprite_marker: str | None = None) -> tuple[QtWidgets.QFrame, QtWidgets.QVBoxLayout]:
        """Styled modular container frame for an overlay module."""
        card = QtWidgets.QFrame()
        card.setObjectName("OverlayCardFrame")
        card.setStyleSheet(
            f"QFrame#OverlayCardFrame {{ background-color: {theme.PANEL}; "
            f"border: 1px solid {theme.BORDER}; border-radius: 6px; }}")
        lay = QtWidgets.QVBoxLayout(card)
        lay.setContentsMargins(14, 12, 14, 14)
        lay.setSpacing(12)

        hdr_row = QtWidgets.QHBoxLayout()
        hdr_row.setSpacing(8)
        if sprite_marker:
            ico = QtWidgets.QLabel()
            ico.setFixedSize(20, 20)
            pm = icons.ui_icon(sprite_marker, theme.ACCENT, 20)
            if not pm or pm.isNull():
                pm = icons.marker(sprite_marker, 20, accent=theme.ACCENT)
            if pm and not pm.isNull():
                ico.setPixmap(pm)
            hdr_row.addWidget(ico, 0, QtCore.Qt.AlignVCenter)
        hdr_row.addWidget(C.SectionHeader(title, tag=tag), 1, QtCore.Qt.AlignVCenter)
        lay.addLayout(hdr_row)

        div = QtWidgets.QFrame()
        div.setFixedHeight(1)
        div.setStyleSheet(f"background:{theme.BORDER};border:none;")
        lay.addWidget(div)
        return card, lay

    def _build_minimap_card(self) -> QtWidgets.QFrame:
        card, lay = self._overlay_card_frame("Minimap Radar", tag="RADAR DISPLAY", sprite_marker="map")
        self._build_minimap(lay)
        return card

    # --- Combat DPS Meter (Top DPS overlay) -------------------------------
    _DPS_RANGE_OPTIONS = ("Zone", "400m", "100m", "50m")
    _DPS_RANGE_VALUES = {"Zone": 0.0, "400m": 400.0, "100m": 100.0, "50m": 50.0}
    _DPS_RANGE_BY_VALUE = {v: k for k, v in _DPS_RANGE_VALUES.items()}

    # Card name shown in the header badge (constant — never swaps to
    # "ACTIVE ENGINE"); the Select buttons stay the short "Option N" form.
    _DPS_MODE_NAMES = {
        "proxy": "Option 1: Drop-In Proxy",
        "injector": "Option 2: Hot Injector",
        "memory": "Option 3: Memory Reader",
    }

    def _dps_range_label(self, d: float) -> str:
        return self._DPS_RANGE_BY_VALUE.get(float(d or 0.0), "400m")

    def _build_dps_card(self) -> QtWidgets.QFrame:
        """Top DPS meter settings: tracking range, default view, row limits.
        The tracking range here is the home for the cycle button removed from
        the overlay titlebar (which used to toggle 400m -> Zone -> 100m -> 50m
        with no visible feedback in instances)."""
        # No sprite icon here: SectionHeader already draws the accent tick, and
        # an icon ahead of it pushed the title ~40px right of the card content
        # (the diagnostics card next to it has no icon either).
        card, lay = self._overlay_card_frame("Combat DPS Meter",
                                             tag="TOP DPS OVERLAY")

        # Default view + row limits sit side by side (two columns), matching
        # the toggles below — nothing stacks full-width anymore.
        pref_cols = QtWidgets.QHBoxLayout()
        pref_cols.setSpacing(10)

        view_col = QtWidgets.QVBoxLayout()
        view_col.setSpacing(6)
        view_lbl = QtWidgets.QLabel("Default meter view")
        view_lbl.setStyleSheet(f"font-size: 12px; font-weight: 600; color: {theme.MUTED};")
        view_col.addWidget(view_lbl)
        self._dps_view_seg = C.SegmentedControl(
            ["Damage", "Healing", "Both"],
            current=getattr(self.s, "dps_view", "damage"))
        self._dps_view_seg.currentChanged.connect(self._set_dps_view)
        view_col.addWidget(self._dps_view_seg)
        view_col.addStretch(1)
        pref_cols.addLayout(view_col, 1)

        rows_col = QtWidgets.QVBoxLayout()
        rows_col.setSpacing(6)
        rows_lbl = QtWidgets.QLabel("Row limits")
        rows_lbl.setStyleSheet(f"font-size: 12px; font-weight: 600; color: {theme.MUTED};")
        rows_col.addWidget(rows_lbl)
        rows_grid = QtWidgets.QGridLayout()
        rows_grid.setHorizontalSpacing(10)
        for i, (attr, label, lo, hi) in enumerate((
                ("dps_top_count", "Top damage rows", 1, 30),
                ("heals_top_count", "Top heal rows", 1, 10))):
            st = C.Stepper(int(getattr(self.s, attr)), lo, hi, 1)
            st.valueChanged.connect(lambda v_, a=attr: self._set(a, v_))
            self._settings_steppers.append((attr, st))
            rows_grid.addWidget(C.Field(label, st), 0, i)
        rows_col.addLayout(rows_grid)
        rows_col.addStretch(1)
        pref_cols.addLayout(rows_col, 1)

        lay.addLayout(pref_cols)

        # Solo + HP-diff toggles sit side by side (two columns), each with its
        # info text underneath, so the card ends compactly instead of two
        # full-width stacked rows.
        toggle_cols = QtWidgets.QHBoxLayout()
        toggle_cols.setSpacing(10)

        def _toggle_column(toggle: QtWidgets.QWidget, hint_text: str) -> QtWidgets.QVBoxLayout:
            col = QtWidgets.QVBoxLayout()
            col.setSpacing(6)
            col.addWidget(toggle)
            hint = QtWidgets.QLabel(hint_text)
            hint.setObjectName("Muted")
            hint.setWordWrap(True)
            col.addWidget(hint)
            col.addStretch(1)
            return col

        self._dps_solo_only_toggle = C.LabeledToggle(
            "Solo: show only my DPS",
            bool(getattr(self.s, "dps_solo_only", True)),
            label_style="font-size: 12px; font-weight: 600;")
        self._dps_solo_only_toggle.toggled.connect(self._set_dps_solo_only)
        toggle_cols.addLayout(_toggle_column(
            self._dps_solo_only_toggle,
            "Ungrouped in the open world, only your parse shows; "
            "grouped or in dungeons/rifts, everyone appears."), 1)

        self._dps_hp_est_toggle = C.LabeledToggle(
            "HP-diff fallback (est. damage)",
            bool(getattr(self.s, "dps_hp_est", False)),
            label_style="font-size: 12px; font-weight: 600;")
        self._dps_hp_est_toggle.toggled.connect(self._set_dps_hp_est)
        toggle_cols.addLayout(_toggle_column(
            self._dps_hp_est_toggle,
            "Estimates damage from enemy HP drops when events go silent."), 1)

        lay.addLayout(toggle_cols)

        return card

    def _set_dps_range(self, label: str) -> None:
        self._set("dps_max_dist", self._DPS_RANGE_VALUES.get(label, 400.0))

    def _set_dps_solo_only(self, on: bool) -> None:
        """Persist the solo-only meter/combat-page view toggle."""
        self._set("dps_solo_only", bool(on))
        if hasattr(self, "_render_combat_breakdown"):
            try:
                self._render_combat_breakdown()
            except Exception:
                pass
        ov = getattr(getattr(self, "overlay_mgr", None), "dps_overlay", None)
        if ov is not None:
            try:
                # Keep the overlay's Solo button and rows in sync with the
                # settings toggle (the button reflects the same setting).
                solo_btn = getattr(ov, "solo_btn", None)
                if solo_btn is not None:
                    solo_btn.blockSignals(True)
                    solo_btn.setChecked(bool(on))
                    solo_btn.blockSignals(False)
                tick = getattr(ov, "_tick", None)
                if callable(tick):
                    tick()
            except Exception:
                pass

    def _set_dps_view(self, label: str) -> None:
        self._set("dps_view", label.lower())

    def _resync_dps_settings_widgets(self) -> None:
        """Re-surface the Combat DPS Meter card when the DPS tab is re-shown,
        so overlay-side changes (e.g. the meter's view chips) appear here too
        without a restart."""
        for seg, cur in ((getattr(self, "_dps_view_seg", None),
                          getattr(self.s, "dps_view", "damage")),
                         (getattr(self, "_dps_range_seg", None),
                          self._dps_range_label(getattr(self.s, "dps_max_dist", 400.0)))):
            if seg is not None and _is_valid(seg):
                try:
                    target_text = str(cur).capitalize() if seg == getattr(self, "_dps_view_seg", None) else str(cur)
                    if seg.currentText().lower() != str(cur).lower():
                        seg.blockSignals(True)
                        seg.setCurrentText(target_text)
                        seg.blockSignals(False)
                except Exception:
                    pass
        for attr, st in getattr(self, "_settings_steppers", []) or []:
            if attr in ("dps_top_count", "heals_top_count"):
                if st is not None and _is_valid(st):
                    try:
                        if int(st.value()) != int(getattr(self.s, attr)):
                            st.blockSignals(True)
                            st.setValue(int(getattr(self.s, attr)))
                            st.blockSignals(False)
                    except Exception:
                        pass
        hp_est = getattr(self, "_dps_hp_est_toggle", None)
        if hp_est is not None and _is_valid(hp_est):
            try:
                want = bool(getattr(self.s, "dps_hp_est", False))
                toggle = getattr(hp_est, "_toggle", None)
                if toggle is not None and _is_valid(toggle) and toggle.isChecked() != want:
                    toggle.blockSignals(True)
                    toggle.setChecked(want)
                    toggle.blockSignals(False)
            except Exception:
                pass
        solo_toggle = getattr(self, "_dps_solo_only_toggle", None)
        if solo_toggle is not None and _is_valid(solo_toggle):
            try:
                want = bool(getattr(self.s, "dps_solo_only", True))
                toggle = getattr(solo_toggle, "_toggle", None)
                if toggle is not None and _is_valid(toggle) and toggle.isChecked() != want:
                    toggle.blockSignals(True)
                    toggle.setChecked(want)
                    toggle.blockSignals(False)
            except Exception:
                pass

    # =========================================================================
    # --- DPS & Combat Engine Settings Tab (Options 1, 2, 3) -----------------
    # =========================================================================
    def _tab_dps(self):
        w, lv = self._tab_page()
        if not hasattr(self, "_settings_steppers") or self._settings_steppers is None:
            self._settings_steppers = []
        self._build_dps_engine_settings(lv)
        lv.addStretch(1)
        return w

    def _build_dps_engine_settings(self, v) -> None:
        v.setSpacing(10)

        # 1. Main Section Header
        v.addWidget(C.SectionHeader("Combat DPS Engine", tag="DATA CAPTURE ARCHITECTURE"))

        # A little extra breathing room under the section header.
        v.addSpacing(8)

        # Single compact 3rd-party DLL notice — one line in the DPS header
        # instead of separate boxes on the Option 1 card and Live Diagnostics.
        self._dps_conflict_line = QtWidgets.QLabel("")
        self._dps_conflict_line.setWordWrap(True)
        self._dps_conflict_line.setStyleSheet(
            "font-size: 12px; font-weight: 700; color: #F59E0B;")
        self._dps_conflict_line.setVisible(False)
        v.addWidget(self._dps_conflict_line)

        # The 3 Option Cards — one row when the tab is wide enough
        self._DPS_MODES = [
            ("Option 1: Drop-In Proxy (version.dll / dinput8.dll)", "proxy"),
            ("Option 2: Hot-Attach Injector (farever_dps.exe)", "injector"),
            ("Option 3: Pure Memory Reader (DamageReader)", "memory"),
        ]
        curr_mode = getattr(self.s, "dps_mode", "proxy")
        self._dps_mode_seg = None
        self._dps_mode_cards = {}
        self._dps_mode_select_btns = {}
        self._dps_mode_badges = {}

        # Flow layout: the three cards share one row when the window is wide
        # and wrap onto extra rows on narrower windows instead of overflowing
        # horizontally (same behavior as the meter + diagnostics row below).
        cards_flow = StretchFlow()
        cards_flow.setContentsMargins(0, 0, 0, 0)
        cards_flow.setSpacing(12)
        cards_flow.addWidget(self._build_option1_card())
        cards_flow.addWidget(self._build_option2_card())
        cards_flow.addWidget(self._build_option3_card())
        v.addLayout(cards_flow)

        # All three header badges share one width, so they line up across
        # the cards even though the option names differ in length.
        badges = [b for b in (getattr(self, "_dps_mode_badges", None) or {}).values()
                  if b is not None]
        if badges:
            badge_w = max(b.sizeHint().width() for b in badges)
            for b in badges:
                b.setFixedWidth(badge_w)

        # Clear air gap between the option cards and the meter/diagnostics
        # row below (10px layout spacing + 14px extra = ~24px).
        v.addSpacing(14)

        # 4. Meter preferences & live diagnostics, side by side (as before the
        # cards were merged into the DPS tab); they wrap on narrow windows too.
        self._dps_diag_card = self._build_dps_diagnostics_card()
        self._dps_card = self._build_dps_card()
        btm_flow = StretchFlow()
        btm_flow.setContentsMargins(0, 0, 0, 0)
        btm_flow.setSpacing(12)
        btm_flow.addWidget(self._dps_diag_card)
        btm_flow.addWidget(self._dps_card)
        v.addLayout(btm_flow)

        # Wire the selected mode to the live model once the DPS tab is shown.
        existing_model = getattr(self, "model", None)
        if existing_model is not None:
            try:
                dm = getattr(existing_model, "damage", None)
            except Exception:
                dm = None
            if dm is not None and hasattr(dm, "set_mode"):
                dm.set_mode(curr_mode)
                tracker = getattr(existing_model, "dps", None)
                if tracker is not None and hasattr(tracker, "reset_hp_baselines"):
                    tracker.reset_hp_baselines(reason=f"capture mode -> {curr_mode}")

        self._update_dps_card_highlights()
        self._refresh_dps_diagnostics()

        # Live poll timer: refresh diagnostics & conflict checks every 1s
        if not hasattr(self, "_dps_live_diag_timer") or self._dps_live_diag_timer is None:
            self._dps_live_diag_timer = QtCore.QTimer(self if isinstance(self, QtCore.QObject) else None)
            self._dps_live_diag_timer.setInterval(1000)
            self._dps_live_diag_timer.timeout.connect(self._refresh_dps_diagnostics)
            self._dps_live_diag_timer.start()

    def _build_option1_card(self) -> QtWidgets.QFrame:
        card = QtWidgets.QFrame()
        card.setObjectName("DpsOptCard1")
        card.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
        card.setCursor(QtCore.Qt.PointingHandCursor)
        card.mousePressEvent = lambda ev: self._set_dps_mode_key("proxy")
        lay = QtWidgets.QVBoxLayout(card)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(8)

        # Header badge carries the card name (kept constant; active state is
        # shown by the card frame + Select button instead).
        badge = QtWidgets.QLabel(self._DPS_MODE_NAMES["proxy"].upper())
        badge.setStyleSheet(
            f"font-size: 15px; font-weight: 800; color: #38BDF8; "
            f"background: rgba(56, 189, 248, 0.15); border: 1px solid rgba(56, 189, 248, 0.4); "
            f"border-radius: 6px; padding: 3px 10px;"
        )
        # Fixed height: without this, the card layout stretches the pill to
        # fill leftover space, making the three badges wildly different sizes.
        badge.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Fixed)
        lay.addWidget(badge, 0, QtCore.Qt.AlignHCenter)
        self._dps_mode_badges["proxy"] = badge

        # One lead+body block (lead phrase in the card's accent color) keeps
        # all three option cards the same shape: badge, description, features,
        # one action row, Select button.
        desc = QtWidgets.QLabel(
            "<span style=\"color:#eac331;font-weight:600;\">Auto-loads on boot "
            "via game folder proxy DLL.</span> Windows loads it when Farever.exe "
            "starts. The farever_dps.exe injector stays permanently disabled "
            "in this mode."
        )
        desc.setTextFormat(QtCore.Qt.RichText)
        desc.setStyleSheet(f"font-size: 12px; color: {theme.MUTED}; line-height: 1.3;")
        desc.setWordWrap(True)
        lay.addWidget(desc)

        # Proxy DLL Selection row
        sel_row = QtWidgets.QHBoxLayout()
        sel_row.setSpacing(8)
        sel_lbl = QtWidgets.QLabel("Proxy DLL:")
        sel_lbl.setStyleSheet(f"font-size: 12px; font-weight: 700; color: {theme.TEXT};")
        sel_row.addWidget(sel_lbl)

        curr_dll = getattr(self.s, "dps_proxy_dll", "version.dll") or "version.dll"
        self._dps_proxy_combo = QtWidgets.QComboBox()
        self._dps_proxy_combo_normal_style = (
            f"QComboBox {{ background: rgba(255, 255, 255, 0.08); border: 1px solid {theme.BORDER}; "
            f"border-radius: 6px; color: {theme.TEXT}; font-size: 12px; font-weight: 600; padding: 3px 8px; }} "
            f"QComboBox::drop-down {{ border: none; }} "
            f"QComboBox QAbstractItemView {{ background: #181B20; color: {theme.TEXT}; selection-background-color: #38BDF8; selection-color: #000; }}"
        )
        self._dps_proxy_combo_conflict_style = (
            "QComboBox { background: rgba(239, 68, 68, 0.22); border: 2px solid #EF4444; "
            "border-radius: 6px; color: #FFFFFF; font-size: 12px; font-weight: 800; padding: 3px 8px; } "
            "QComboBox::drop-down { border: none; } "
            "QComboBox QAbstractItemView { background: #181B20; color: #FFFFFF; selection-background-color: #EF4444; selection-color: #FFF; }"
        )
        self._dps_proxy_combo.setStyleSheet(self._dps_proxy_combo_normal_style)
        self._dps_proxy_combo.addItem("version.dll (Default · Conflict-Free)", "version.dll")
        self._dps_proxy_combo.addItem("dinput8.dll (Alternative · DirectInput)", "dinput8.dll")
        idx = 1 if curr_dll == "dinput8.dll" else 0
        self._dps_proxy_combo.setCurrentIndex(idx)
        # Let the combo shrink below its longest item's text width so the card
        # doesn't force the tab wider than the window (the current item elides
        # when narrow; the combo still expands when there is room).
        self._dps_proxy_combo.setSizeAdjustPolicy(
            QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self._dps_proxy_combo.setMinimumContentsLength(6)
        self._dps_proxy_combo.currentIndexChanged.connect(self._on_proxy_dll_changed)
        sel_row.addWidget(self._dps_proxy_combo, 1)
        lay.addLayout(sel_row)

        feats = QtWidgets.QLabel(
            "✓ No external helper programs run\n"
            "✓ 0ms direct engine event streaming\n"
            "✓ Offline & permanent across all game launches\n"
            "✓ version.dll coexists safely with other mods"
        )
        feats.setStyleSheet(f"font-size: 12px; color: {theme.TEXT}; line-height: 1.4;")
        feats.setWordWrap(True)
        lay.addWidget(feats)

        # All three proxy actions share one row; tight padding keeps the card
        # narrow enough for three cards on a wide window.
        actions_row = QtWidgets.QHBoxLayout()
        actions_row.setSpacing(4)

        open_game_btn = QtWidgets.QPushButton("📁 Open")
        open_game_btn.setCursor(QtCore.Qt.PointingHandCursor)
        open_game_btn.setFixedHeight(30)
        open_game_btn.setStyleSheet(
            f"QPushButton {{ background: rgba(255, 255, 255, 0.06); border: 1px solid {theme.BORDER}; "
            f"border-radius: 6px; color: {theme.TEXT}; font-size: 12px; font-weight: 600; padding: 0 6px; }} "
            f"QPushButton:hover {{ background: rgba(255, 255, 255, 0.12); }}"
        )
        open_game_btn.setToolTip("Open game install folder where Farever.exe is located")
        open_game_btn.clicked.connect(self._open_farever_game_dir)
        actions_row.addWidget(open_game_btn)

        install_dll_btn = QtWidgets.QPushButton("📋 Install")
        install_dll_btn.setCursor(QtCore.Qt.PointingHandCursor)
        install_dll_btn.setFixedHeight(30)
        install_dll_btn.setStyleSheet(
            f"QPushButton {{ background: rgba(255, 255, 255, 0.06); border: 1px solid {theme.BORDER}; "
            f"border-radius: 6px; color: {theme.TEXT}; font-size: 12px; font-weight: 600; padding: 0 4px; }} "
            f"QPushButton:hover {{ background: rgba(255, 255, 255, 0.12); }}"
        )
        install_dll_btn.setToolTip("Copy selected proxy DLL (version.dll or dinput8.dll) into Farever game directory")
        install_dll_btn.clicked.connect(self._install_proxy_dll)
        actions_row.addWidget(install_dll_btn)

        remove_dll_btn = QtWidgets.QPushButton("🗑 Remove")
        remove_dll_btn.setCursor(QtCore.Qt.PointingHandCursor)
        remove_dll_btn.setFixedHeight(30)
        remove_dll_btn.setStyleSheet(
            f"QPushButton {{ background: rgba(239, 68, 68, 0.08); border: 1px solid rgba(239, 68, 68, 0.25); "
            f"border-radius: 6px; color: #F87171; font-size: 12px; font-weight: 600; padding: 0 6px; }} "
            f"QPushButton:hover {{ background: rgba(239, 68, 68, 0.18); border-color: #EF4444; color: #FECACA; }}"
        )
        remove_dll_btn.setToolTip("Remove FareverPal proxy DLL from game directory (leaves 3rd-party mods untouched)")
        remove_dll_btn.clicked.connect(self._remove_proxy_dll)
        actions_row.addWidget(remove_dll_btn)
        lay.addLayout(actions_row)

        sel_btn = QtWidgets.QPushButton("Select Option 1")
        sel_btn.setCursor(QtCore.Qt.PointingHandCursor)
        sel_btn.setFixedHeight(36)
        sel_btn.setMinimumWidth(190)   # uniform across the three cards
        sel_btn.clicked.connect(lambda: self._set_dps_mode_key("proxy"))
        lay.addWidget(sel_btn)
        self._dps_mode_select_btns["proxy"] = sel_btn

        self._dps_proxy_feedback = QtWidgets.QLabel("")
        self._dps_proxy_feedback.setStyleSheet(f"font-size: 12px; color: {theme.MUTED}; font-weight: 600;")
        self._dps_proxy_feedback.setWordWrap(True)
        self._dps_proxy_feedback.setVisible(False)
        lay.addWidget(self._dps_proxy_feedback)

        self._dps_mode_cards["proxy"] = card
        return card

    def _on_proxy_dll_changed(self, idx: int) -> None:
        combo = getattr(self, "_dps_proxy_combo", None)
        if combo is not None:
            chosen = combo.currentData() or ("dinput8.dll" if idx == 1 else "version.dll")
            self._set("dps_proxy_dll", chosen)
            if hasattr(self, "log"):
                self.log(f"DPS Proxy DLL preference set to {chosen}.")
            self._refresh_dps_diagnostics()

    def _build_option2_card(self) -> QtWidgets.QFrame:
        card = QtWidgets.QFrame()
        card.setObjectName("DpsOptCard2")
        card.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
        card.setCursor(QtCore.Qt.PointingHandCursor)
        card.mousePressEvent = lambda ev: self._set_dps_mode_key("injector")
        lay = QtWidgets.QVBoxLayout(card)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(8)

        # Header badge carries the card name (kept constant; active state is
        # shown by the card frame + Select button instead).
        badge = QtWidgets.QLabel(self._DPS_MODE_NAMES["injector"].upper())
        badge.setStyleSheet(
            f"font-size: 15px; font-weight: 800; color: #8B5CF6; "
            f"background: rgba(139, 92, 246, 0.15); border: 1px solid rgba(139, 92, 246, 0.4); "
            f"border-radius: 6px; padding: 3px 10px;"
        )
        # Fixed height: without this, the card layout stretches the pill to
        # fill leftover space, making the three badges wildly different sizes.
        badge.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Fixed)
        lay.addWidget(badge, 0, QtCore.Qt.AlignHCenter)
        self._dps_mode_badges["injector"] = badge

        # One lead+body block (lead phrase in the card's accent color) keeps
        # all three option cards the same shape: badge, description, features,
        # one action row, Select button.
        desc = QtWidgets.QLabel(
            "<span style=\"color:#8B5CF6;font-weight:600;\">farever_dps.exe "
            "hot-attaches on game start.</span> Leaves your game folder 100% "
            "vanilla — it injects farever_dps.dll into memory when Farever.exe "
            "is detected, guarded to never repeat."
        )
        desc.setTextFormat(QtCore.Qt.RichText)
        desc.setStyleSheet(f"font-size: 12px; color: {theme.MUTED}; line-height: 1.3;")
        desc.setWordWrap(True)
        lay.addWidget(desc)

        feats = QtWidgets.QLabel(
            "✓ Zero files placed in game directory\n"
            "✓ Runs once per process (never spams or loops)\n"
            "✓ Attaches on-the-fly without game restarts"
        )
        feats.setStyleSheet(f"font-size: 12px; color: {theme.TEXT}; line-height: 1.4;")
        feats.setWordWrap(True)
        lay.addWidget(feats)

        # Test Inject was removed: Live Diagnostics' 🧪 Test Hit already
        # verifies the event pipeline end-to-end, so this card keeps only the
        # destructive action — on its own row, mirroring Option 1's Remove DLL.
        uninject_btn = QtWidgets.QPushButton("⚡ Uninject")
        uninject_btn.setCursor(QtCore.Qt.PointingHandCursor)
        uninject_btn.setFixedHeight(30)
        uninject_btn.setStyleSheet(
            f"QPushButton {{ background: rgba(239, 68, 68, 0.08); border: 1px solid rgba(239, 68, 68, 0.25); "
            f"border-radius: 6px; color: #F87171; font-size: 12px; font-weight: 600; padding: 0 8px; }} "
            f"QPushButton:hover {{ background: rgba(239, 68, 68, 0.18); border-color: #EF4444; color: #FECACA; }}"
        )
        uninject_btn.setToolTip("Uninject farever_dps.dll from running game & switch to Option 3 (Memory Reader)")
        uninject_btn.clicked.connect(self._test_uninject_now)
        uninject_row = QtWidgets.QHBoxLayout()
        uninject_row.setSpacing(6)
        uninject_row.addWidget(uninject_btn)
        lay.addLayout(uninject_row)

        sel_btn = QtWidgets.QPushButton("Select Option 2")
        sel_btn.setCursor(QtCore.Qt.PointingHandCursor)
        sel_btn.setFixedHeight(36)
        sel_btn.setMinimumWidth(190)   # uniform across the three cards
        sel_btn.clicked.connect(lambda: self._set_dps_mode_key("injector"))
        lay.addWidget(sel_btn)
        self._dps_mode_select_btns["injector"] = sel_btn

        self._dps_inject_feedback = QtWidgets.QLabel("")
        self._dps_inject_feedback.setStyleSheet(f"font-size: 12px; color: {theme.MUTED}; font-weight: 600;")
        self._dps_inject_feedback.setWordWrap(True)
        self._dps_inject_feedback.setVisible(False)
        lay.addWidget(self._dps_inject_feedback)

        self._dps_mode_cards["injector"] = card
        return card

    def _build_option3_card(self) -> QtWidgets.QFrame:
        card = QtWidgets.QFrame()
        card.setObjectName("DpsOptCard3")
        card.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
        card.setCursor(QtCore.Qt.PointingHandCursor)
        card.mousePressEvent = lambda ev: self._set_dps_mode_key("memory")
        lay = QtWidgets.QVBoxLayout(card)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(8)

        # Header badge carries the card name (kept constant; active state is
        # shown by the card frame + Select button instead).
        badge = QtWidgets.QLabel(self._DPS_MODE_NAMES["memory"].upper())
        badge.setStyleSheet(
            f"font-size: 15px; font-weight: 800; color: #10B981; "
            f"background: rgba(16, 185, 129, 0.15); border: 1px solid rgba(16, 185, 129, 0.4); "
            f"border-radius: 6px; padding: 3px 10px;"
        )
        # Fixed height: without this, the card layout stretches the pill to
        # fill leftover space, making the three badges wildly different sizes.
        badge.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Fixed)
        lay.addWidget(badge, 0, QtCore.Qt.AlignHCenter)
        self._dps_mode_badges["memory"] = badge

        # One lead+body block (lead phrase in the card's accent color) keeps
        # all three option cards the same shape: badge, description, features,
        # one action row, Select button.
        desc = QtWidgets.QLabel(
            "<span style=\"color:#10B981;font-weight:600;\">Pure passive memory "
            "scan (DamageReader).</span> Zero DLLs, zero injectors, and zero "
            "foreign code inside the game process — reads floating combat text "
            "directly from HashLink memory structures."
        )
        desc.setTextFormat(QtCore.Qt.RichText)
        desc.setStyleSheet(f"font-size: 12px; color: {theme.MUTED}; line-height: 1.3;")
        desc.setWordWrap(True)
        lay.addWidget(desc)

        feats = QtWidgets.QLabel(
            "✓ 100% passive memory reading\n"
            "✓ Immune to DLL blocks, antivirus, or hooks\n"
            "✓ No files copied, no injectors executed"
        )
        feats.setStyleSheet(f"font-size: 12px; color: {theme.TEXT}; line-height: 1.4;")
        feats.setWordWrap(True)
        lay.addWidget(feats)

        actions_placeholder = QtWidgets.QHBoxLayout()
        actions_placeholder.setSpacing(6)
        lbl_ph = QtWidgets.QLabel("✓ Passive · Zero injections")
        lbl_ph.setFixedHeight(30)
        lbl_ph.setStyleSheet(f"font-size: 12px; font-weight: 600; color: {theme.MUTED};")
        lbl_ph.setAlignment(QtCore.Qt.AlignCenter)
        actions_placeholder.addWidget(lbl_ph)
        lay.addLayout(actions_placeholder)

        sel_btn = QtWidgets.QPushButton("Select Option 3")
        sel_btn.setCursor(QtCore.Qt.PointingHandCursor)
        sel_btn.setFixedHeight(36)
        sel_btn.setMinimumWidth(190)   # uniform across the three cards
        sel_btn.clicked.connect(lambda: self._set_dps_mode_key("memory"))
        lay.addWidget(sel_btn)
        self._dps_mode_select_btns["memory"] = sel_btn

        self._dps_mode_cards["memory"] = card
        return card

    def _build_dps_diagnostics_card(self) -> QtWidgets.QFrame:
        card = QtWidgets.QFrame()
        # Standard QFrame#Card rule (PANEL bg + 1px BORDER outline). An inline
        # `border` stylesheet here makes Qt draw a box line around every child
        # QLabel, which reads as messy "inline boxes" around each diagnostic row.
        card.setObjectName("Card")
        lay = QtWidgets.QVBoxLayout(card)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(6)

        lay.addWidget(C.SectionHeader("Live Diagnostics", tag="CONNECTION & IPC STATUS"))

        # Two columns: Status and Active Source get the full-width top rows
        # (the conflict text is the longest line and shouldn't wrap into a
        # tall half-width block); the short rows pair up side by side, and
        # DLL sits next to the feedback cell (which collapses to zero when
        # empty).
        grid = QtWidgets.QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(4)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)

        self._dps_diag_status_lbl = QtWidgets.QLabel("Status: Checking…")
        self._dps_diag_status_lbl.setStyleSheet(f"font-size: 13px; font-weight: 700; color: {theme.TEXT};")
        self._dps_diag_status_lbl.setWordWrap(True)
        grid.addWidget(self._dps_diag_status_lbl, 0, 0, 1, 2)

        self._dps_diag_source_lbl = QtWidgets.QLabel("Active Source: -")
        self._dps_diag_source_lbl.setStyleSheet(f"font-size: 13px; color: {theme.MUTED};")
        self._dps_diag_source_lbl.setWordWrap(True)
        grid.addWidget(self._dps_diag_source_lbl, 1, 0, 1, 2)

        self._dps_diag_port_lbl = QtWidgets.QLabel("UDP Port: 127.0.0.1:49152 (Listening)")
        self._dps_diag_port_lbl.setStyleSheet(f"font-size: 13px; color: {theme.MUTED};")
        grid.addWidget(self._dps_diag_port_lbl, 2, 0)

        self._dps_diag_pid_lbl = QtWidgets.QLabel("Attached Game: Not attached")
        self._dps_diag_pid_lbl.setStyleSheet(f"font-size: 13px; color: {theme.MUTED};")
        self._dps_diag_pid_lbl.setWordWrap(True)
        grid.addWidget(self._dps_diag_pid_lbl, 2, 1)

        self._dps_diag_dll_lbl = QtWidgets.QLabel("DLL Detection: Checking…")
        self._dps_diag_dll_lbl.setStyleSheet(f"font-size: 13px; color: {theme.MUTED};")
        self._dps_diag_dll_lbl.setWordWrap(True)
        grid.addWidget(self._dps_diag_dll_lbl, 3, 0)

        self._dps_inject_feedback = QtWidgets.QLabel("")
        self._dps_inject_feedback.setStyleSheet("font-size: 12px;")
        grid.addWidget(self._dps_inject_feedback, 3, 1)

        lay.addLayout(grid)

        scan_btn = QtWidgets.QPushButton("🔍 Scan Folder")
        scan_btn.setCursor(QtCore.Qt.PointingHandCursor)
        scan_btn.setFixedHeight(28)
        scan_btn.setStyleSheet(
            f"QPushButton {{ background: rgba(255, 255, 255, 0.06); border: 1px solid {theme.BORDER}; "
            f"border-radius: 6px; color: {theme.TEXT}; font-size: 12px; font-weight: 600; padding: 0 10px; }} "
            f"QPushButton:hover {{ background: rgba(255, 255, 255, 0.12); }}"
        )
        scan_btn.setToolTip(
            "Point the DLL check at your Farever game folder manually "
            "(for custom install paths) — saved for next time")
        scan_btn.clicked.connect(self._scan_farever_game_dir)
        lay.addWidget(scan_btn)
        self._dps_diag_scan_btn = scan_btn

        copy_btn = QtWidgets.QPushButton("📋 Copy Diagnostics")
        copy_btn.setCursor(QtCore.Qt.PointingHandCursor)
        copy_btn.setFixedHeight(28)
        copy_btn.setStyleSheet(
            f"QPushButton {{ background: rgba(255, 255, 255, 0.06); border: 1px solid {theme.BORDER}; "
            f"border-radius: 6px; color: {theme.TEXT}; font-size: 12px; font-weight: 600; padding: 0 10px; }} "
            f"QPushButton:hover {{ background: rgba(255, 255, 255, 0.12); }}"
        )
        copy_btn.setToolTip("Copy full diagnostic state to clipboard (for Discord / help forums)")
        copy_btn.clicked.connect(self._copy_dps_diagnostics)
        lay.addWidget(copy_btn)

        test_btn = QtWidgets.QPushButton("🧪 Test Hit")
        test_btn.setCursor(QtCore.Qt.PointingHandCursor)
        test_btn.setFixedHeight(28)
        test_btn.setStyleSheet(
            f"QPushButton {{ background: rgba(255, 255, 255, 0.06); border: 1px solid {theme.BORDER}; "
            f"border-radius: 6px; color: {theme.TEXT}; font-size: 12px; font-weight: 600; padding: 0 10px; }} "
            f"QPushButton:hover {{ background: rgba(255, 255, 255, 0.12); }}"
        )
        test_btn.setToolTip("Inject a fake damage event to verify the overlay is receiving events")
        test_btn.clicked.connect(self._inject_test_hit)

        # One compact action row: Scan / Copy always, Test Hit is dev-only
        # (run.bat / source), hidden in the frozen exe.
        btn_row = QtWidgets.QHBoxLayout()
        btn_row.setSpacing(6)
        btn_row.addWidget(scan_btn)
        btn_row.addWidget(copy_btn)
        if not is_frozen():
            btn_row.addWidget(test_btn)
        lay.addLayout(btn_row)

        # Tracking range sits under the action buttons, so the diagnostics
        # text and the buttons read as one block and the capture range is the
        # final control before the card ends.
        rng_lbl = QtWidgets.QLabel("Tracking range")
        rng_lbl.setStyleSheet(f"font-size: 12px; font-weight: 600; color: {theme.MUTED};")
        lay.addWidget(rng_lbl)
        self._dps_range_seg = C.SegmentedControl(
            list(self._DPS_RANGE_OPTIONS),
            current=self._dps_range_label(getattr(self.s, "dps_max_dist", 400.0)))
        self._dps_range_seg.currentChanged.connect(self._set_dps_range)
        lay.addWidget(self._dps_range_seg)

        rng_hint = QtWidgets.QLabel(
            "Instances are always zone-wide. This sets how far out enemy targets count in open zones.")
        rng_hint.setWordWrap(True)
        rng_hint.setStyleSheet(f"font-size: 11px; color: {theme.DIM};")
        lay.addWidget(rng_hint)

        lay.addStretch(1)
        return card

    def _copy_dps_diagnostics(self) -> None:
        """Build a text snapshot of the DPS diagnostics card and copy it to clipboard."""
        dm = getattr(self.model, "damage", None) if hasattr(self, "model") and self.model else None
        pid = getattr(self.model.proc, "pid", None) if hasattr(self, "model") and self.model and hasattr(self.model, "proc") else None
        mode = getattr(self.s, "dps_mode", "proxy")
        hook = getattr(self.s, "dps_hook_enabled", False)

        lines = []
        lines.append("=== FareverPal DPS Diagnostics ===")
        lines.append(f"Mode: {mode.upper()} (" + ("injector hook ON" if hook else "proxy fallback") + ")")
        if pid:
            lines.append(f"Attached Game: Farever.exe (PID {pid})")
        else:
            lines.append("Attached Game: Not attached")

        if dm is not None:
            try:
                st = dm.status()
            except Exception:
                st = "unknown"
            try:
                src = dm.live_source_name()
            except Exception:
                src = "unknown"
            try:
                n, rows = dm.counts()
            except Exception:
                n, rows = 0, 0
            try:
                t = dm.timing_text()
            except Exception:
                t = ""
            lines.append(f"Source: {src}")
            lines.append(f"Status: {st}" + (f" ({t})" if t else ""))
            lines.append(f"Events received: {n}" + (f" (last poll: {rows} rows)" if rows else ""))
            try:
                dp = dm.locate_progress()
                if dp:
                    lines.append(f"Locate progress: {dp}")
            except Exception:
                pass
            try:
                diag = dm.diagnostics()
                if diag:
                    lines.append(f"Reader diagnostics: {diag}")
            except Exception:
                pass
        else:
            lines.append("Source: Not available (model.damage is None)")
            lines.append("Status: offline")

        if hasattr(self, "proc"):
            proc = self.proc
            if proc:
                bridge_fn = getattr(proc, "detect_combat_bridge", None)
                if callable(bridge_fn):
                    try:
                        binfo = bridge_fn()
                        lines.append(f"Bridge detection: {binfo.get('status_summary', binfo)}")
                    except Exception as e:
                        lines.append(f"Bridge detection: error ({e})")

        text = "\n".join(lines)
        try:
            from PySide6 import QtGui
            clipboard = QtGui.QGuiApplication.clipboard()
            clipboard.setText(text)
            if hasattr(self, "log"):
                self.log("DPS diagnostics copied to clipboard.")
            # Brief visual confirmation on the button itself
            copy_btn.setText("✓ Copied!")
            copy_btn.setStyleSheet(
                f"QPushButton {{ background: rgba(16, 185, 129, 0.2); border: 1px solid #10B981; "
                f"border-radius: 6px; color: #10B981; font-size: 12px; font-weight: 700; padding: 0 10px; }}"
            )
            QtCore.QTimer.singleShot(2000, lambda: copy_btn.setText("📋 Copy Diagnostics"))
        except Exception as e:
            if hasattr(self, "log"):
                self.log(f"Failed to copy DPS diagnostics: {e}")

    def _set_dps_hp_est(self, on: bool) -> None:
        """Persist the HP-diff fallback toggle and push it live to every
        tracker, so the Top DPS overlay and the Combat page reflect the
        change immediately (estimates stripped on off, re-baselined on on)."""
        self._set("dps_hp_est", bool(on))
        self._apply_dps_hp_est_live(bool(on))

    def _apply_dps_hp_est_live(self, on: bool) -> None:
        """Push the HP-diff toggle to every live tracker instance (model,
        overlay, Combat-page fallback) and refresh both surfaces now."""
        trackers: list = []
        model = getattr(self, "model", None)
        if model is not None and getattr(model, "dps", None) is not None:
            trackers.append(model.dps)
        om = getattr(self, "overlay_mgr", None)
        overlays = getattr(om, "overlays", None) if om is not None else None
        ov = overlays.get("dps") if isinstance(overlays, dict) else None
        if ov is not None and getattr(ov, "tracker", None) is not None:
            trackers.append(ov.tracker)
        fb = getattr(self, "_fallback_dps_tracker", None)
        if fb is not None:
            trackers.append(fb)

        seen: set[int] = set()
        for t in trackers:
            if id(t) in seen:
                continue          # the overlay usually shares the model tracker
            seen.add(id(t))
            try:
                if hasattr(t, "set_hp_est_allowed"):
                    t.set_hp_est_allowed(bool(on))
                elif hasattr(t, "hp_est_allowed"):
                    if bool(on) != t.hp_est_allowed:
                        t.hp_est_allowed = bool(on)
                        if hasattr(t, "reset_hp_baselines"):
                            t.reset_hp_baselines(reason="HP-diff toggle")
            except Exception:
                pass

        # Immediate re-render. The overlay's 10 Hz timer and the Combat
        # page's timer would catch up anyway, but a settings flip should
        # not wait a beat (and the Combat page only self-ticks while its
        # nav page is active).
        if ov is not None and hasattr(ov, "_tick"):
            try:
                ov._tick()
            except Exception:
                pass
        if hasattr(self, "_refresh_combat_page_live"):
            try:
                self._refresh_combat_page_live()
            except Exception:
                pass

    def _set_dps_mode_key(self, mode: str) -> None:
        self._set("dps_mode", mode)
        self._set("dps_hook_enabled", (mode == "injector"))
        if hasattr(self, "model") and self.model and hasattr(self.model, "damage"):
            self.model.damage.set_mode(mode)
            # Capture source changed: clear HP-diff baselines so the fallback
            # re-baselines instead of carrying stale snapshots into the next fight.
            tracker = getattr(self.model, "dps", None)
            if tracker is not None and hasattr(tracker, "reset_hp_baselines"):
                tracker.reset_hp_baselines(reason=f"capture mode -> {mode}")
        label = next((lbl for lbl, m in getattr(self, "_DPS_MODES", []) if m == mode), None)
        if label and getattr(self, "_dps_mode_seg", None) is not None:
            self._dps_mode_seg.blockSignals(True)
            self._dps_mode_seg.setCurrentText(label)
            self._dps_mode_seg.blockSignals(False)
        self._update_dps_card_highlights()
        self._refresh_dps_diagnostics()
        if hasattr(self, "log"):
            opt_n = 1 if mode == "proxy" else 2 if mode == "injector" else 3
            self.log(f"DPS Capture mode changed to Option {opt_n} ({mode.capitalize()}).")
            proc = getattr(self, "proc", None)
            if proc:
                bfn = getattr(proc, "detect_combat_bridge", None)
                if callable(bfn):
                    binfo = bfn()
                    self.log(f"Combat Bridge: {binfo.get('status_summary', '')}")
                    # When proxy mode is selected and the DLL is NOT in the game folder,
                    # open the GAME directory (not the dps_bridge mod folder) so the user
                    # can copy dinput8.dll next to Farever.exe.
                    if mode == "proxy" and binfo.get("option") != 1:
                        self._prompt_proxy_dll_copy(binfo)
                    elif mode == "injector" and binfo.get("option") != 2:
                        from ..game_attach import _try_launch_hook
                        log_cb = getattr(self, "log", None)
                        self.log(f"Combat Bridge: Auto-launching injector for PID {proc.pid}…")
                        _try_launch_hook(proc.pid, force=True, log_cb=log_cb)
                        if hasattr(self, "_dps_inject_feedback"):
                            self._dps_inject_feedback.setText(f"✓ Inject command sent to PID {proc.pid}")
                            self._dps_inject_feedback.setStyleSheet("color: #10B981; font-size: 12px; font-weight: 700;")

    def _prompt_proxy_dll_copy(self, binfo: dict, open_folder: bool = False) -> None:
        """When proxy mode is selected but the proxy DLL is missing from the game folder,
        notify the user and update the card feedback label."""
        game_dir = (
            binfo.get("game_dir", "")
            or getattr(self, "_cached_farever_game_dir", "")
            or getattr(self.s, "farever_game_dir", "")
        )
        game_exe = binfo.get("game_exe", "") or "Farever.exe"
        proxy_on_disk = bool(binfo.get("proxy_in_game_dir", False))
        chosen_dll = binfo.get("dll_name") or getattr(self.s, "dps_proxy_dll", "version.dll") or "version.dll"

        if proxy_on_disk:
            msg = f"{chosen_dll} found in game folder but not loaded. Restart {game_exe} to activate."
            if hasattr(self, "log"):
                self.log(f"Combat Bridge: {msg}")
            if hasattr(self, "_dps_proxy_feedback") and self._dps_proxy_feedback:
                self._dps_proxy_feedback.setText(f"⚠ {msg}")
                self._dps_proxy_feedback.setStyleSheet("color: #F59E0B; font-size: 12px; font-weight: 600;")
            return

        if not game_dir:
            msg = "Could not locate game directory. Click 'Open Folder' to locate Farever.exe."
            if hasattr(self, "log"):
                self.log(f"Combat Bridge: {msg}")
            if hasattr(self, "_dps_proxy_feedback") and self._dps_proxy_feedback:
                self._dps_proxy_feedback.setText(f"⚠ {msg}")
                self._dps_proxy_feedback.setStyleSheet("color: #F59E0B; font-size: 12px; font-weight: 600;")
            return

        if binfo.get("third_party_dinput8") and chosen_dll == "dinput8.dll":
            msg = "Another mod is using dinput8.dll. Switch to version.dll to avoid conflicts."
            if hasattr(self, "_dps_proxy_feedback") and self._dps_proxy_feedback:
                self._dps_proxy_feedback.setText(f"⚠ {msg}")
                self._dps_proxy_feedback.setStyleSheet("color: #EF4444; font-size: 12px; font-weight: 700;")
            return
        elif binfo.get("third_party_version") and chosen_dll == "version.dll":
            msg = "Another mod is using version.dll. Switch to dinput8.dll to avoid conflicts."
            if hasattr(self, "_dps_proxy_feedback") and self._dps_proxy_feedback:
                self._dps_proxy_feedback.setText(f"⚠ {msg}")
                self._dps_proxy_feedback.setStyleSheet("color: #EF4444; font-size: 12px; font-weight: 700;")
            return

        msg = f"{chosen_dll} missing. Click 'Install DLL' to copy it next to {game_exe}."
        if hasattr(self, "log"):
            self.log(f"Combat Bridge: {msg} (Target: {game_dir})")
        if hasattr(self, "_dps_proxy_feedback") and self._dps_proxy_feedback:
            self._dps_proxy_feedback.setText(f"ℹ {msg}")
            self._dps_proxy_feedback.setStyleSheet("color: #38BDF8; font-size: 12px; font-weight: 600;")

        if open_folder:
            try:
                from pathlib import Path
                gd = Path(game_dir)
                if gd.exists():
                    QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(gd)))
            except Exception:
                pass

    def _on_dps_mode_selected(self, label: str) -> None:
        label_lower = (label or "").strip().lower()
        if "injector" in label_lower or "option 2" in label_lower:
            mode = "injector"
        elif "memory" in label_lower or "option 3" in label_lower:
            mode = "memory"
        elif "proxy" in label_lower or "option 1" in label_lower:
            mode = "proxy"
        else:
            mode = next((m for lbl, m in getattr(self, "_DPS_MODES", []) if m == label or lbl == label), "proxy")
        self._set_dps_mode_key(mode)

    def _update_dps_card_highlights(self) -> None:
        current_mode = getattr(self.s, "dps_mode", "proxy")
        mode_colors = {
            "proxy": "#38BDF8",      # Sky blue
            "injector": "#8B5CF6",   # Violet
            "memory": "#10B981",     # Emerald
        }
        mode_badges = {k: v.upper() for k, v in self._DPS_MODE_NAMES.items()}

        for mode_key, card in (getattr(self, "_dps_mode_cards", None) or {}).items():
            if card is None or not _is_valid(card):
                continue
            is_active = (mode_key == current_mode)
            accent = mode_colors.get(mode_key, "#38BDF8")

            # Update card frame styling with header top highlight
            try:
                if is_active:
                    card.setStyleSheet(
                        f"QFrame#{card.objectName()} {{ background-color: {theme.PANEL}; "
                        f"border: 1px solid {accent}88; border-top: 4px solid {accent}; "
                        f"border-radius: 8px; }}"
                    )
                else:
                    card.setStyleSheet(
                        f"QFrame#{card.objectName()} {{ background-color: {theme.PANEL}; "
                        f"border: 1px solid {theme.BORDER}; border-top: 3px solid rgba(255, 255, 255, 0.14); "
                        f"border-radius: 8px; }} "
                        f"QFrame#{card.objectName()}:hover {{ border-top: 3px solid {accent}; "
                        f"border-color: rgba(255, 255, 255, 0.2); }}"
                    )
            except Exception:
                pass

            # Update header badge
            badge = (getattr(self, "_dps_mode_badges", None) or {}).get(mode_key)
            if badge is not None and _is_valid(badge):
                try:
                    # Badge text stays the card name on every state — the
                    # active engine is indicated by the frame + Select button.
                    badge.setText(mode_badges.get(mode_key, ""))
                    if is_active:
                        badge.setStyleSheet(
                            f"font-size: 15px; font-weight: 800; color: #FFFFFF; "
                            f"background: {accent}; border: 1px solid {accent}; "
                            f"border-radius: 6px; padding: 3px 10px;"
                        )
                    else:
                        badge.setStyleSheet(
                            f"font-size: 15px; font-weight: 800; color: {accent}; "
                            f"background: rgba(255, 255, 255, 0.05); border: 1px solid {accent}44; "
                            f"border-radius: 6px; padding: 3px 10px;"
                        )
                except Exception:
                    pass

            # Update action button
            btn = (getattr(self, "_dps_mode_select_btns", None) or {}).get(mode_key)
            if btn is not None and _is_valid(btn):
                try:
                    if is_active:
                        btn.setText("✓ ACTIVE MODE")
                        btn.setStyleSheet(
                            f"QPushButton {{ background: {accent}; border: 1px solid {accent}; "
                            f"color: #FFFFFF; font-weight: 800; border-radius: 6px; font-size: 14px; }}"
                        )
                    else:
                        opt_num = 1 if mode_key == "proxy" else 2 if mode_key == "injector" else 3
                        btn.setText(f"Select Option {opt_num}")
                        btn.setStyleSheet(
                            f"QPushButton {{ background: rgba(255, 255, 255, 0.06); border: 1px solid {theme.BORDER}; "
                            f"color: {theme.MUTED}; font-weight: 600; border-radius: 6px; font-size: 14px; }} "
                            f"QPushButton:hover {{ background: rgba(255, 255, 255, 0.12); color: {theme.TEXT}; border-color: {accent}; }}"
                        )
                except Exception:
                    pass

    def _resync_dps_mode_widgets(self) -> None:
        self._update_dps_card_highlights()
        self._refresh_dps_diagnostics()

    def _refresh_dps_diagnostics(self) -> None:
        import os
        from ...core.proc import detect_combat_bridge, is_farever_proxy, invalidate_proxy_cache
        invalidate_proxy_cache()
        dm = getattr(self.model, "damage", None) if hasattr(self, "model") and self.model else None
        pid = getattr(self.model.proc, "pid", None) if hasattr(self, "model") and self.model and hasattr(self.model, "proc") else None
        mode = getattr(self.s, "dps_mode", "proxy")

        pid_lbl = getattr(self, "_dps_diag_pid_lbl", None)
        if pid_lbl is not None and _is_valid(pid_lbl):
            try:
                if pid:
                    pid_lbl.setText(f"Attached Game: Farever.exe (PID {pid})")
                    pid_lbl.setStyleSheet(f"font-size: 13px; color: {theme.GOOD}; font-weight: 600;")
                else:
                    pid_lbl.setText("Attached Game: Not attached")
                    pid_lbl.setStyleSheet(f"font-size: 12px; color: {theme.MUTED};")
            except Exception:
                pass

        from ...core.proc import detect_combat_bridge, is_farever_proxy
        proc = getattr(self.model, "proc", None) if hasattr(self, "model") and self.model else getattr(self, "proc", None)
        pid = getattr(proc, "pid", None) if proc else None
        g_dir = self._find_farever_game_dir()
        try:
            binfo = detect_combat_bridge(pid=pid or 0, game_dir=g_dir)
        except Exception:
            binfo = {}
        if not g_dir and binfo.get("game_dir"):
            g_dir = binfo.get("game_dir")

        if g_dir and os.path.isdir(g_dir):
            dinput_p = os.path.join(g_dir, "dinput8.dll")
            if os.path.isfile(dinput_p) and not is_farever_proxy(dinput_p):
                binfo["third_party_dinput8"] = True
                binfo["third_party_dinput8_path"] = dinput_p
                try:
                    binfo["third_party_dinput8_size"] = os.path.getsize(dinput_p)
                except Exception:
                    pass
            version_p = os.path.join(g_dir, "version.dll")
            if os.path.isfile(version_p) and not is_farever_proxy(version_p):
                binfo["third_party_version"] = True
                binfo["third_party_version_path"] = version_p
                try:
                    binfo["third_party_version_size"] = os.path.getsize(version_p)
                except Exception:
                    pass

        tp_dinput = bool(binfo.get("third_party_dinput8"))
        tp_version = bool(binfo.get("third_party_version"))
        pref_dll = getattr(self.s, "dps_proxy_dll", "version.dll") or "version.dll"

        tp_info_parts = []
        if tp_dinput:
            tp_info_parts.append("dinput8.dll")
        if tp_version:
            tp_info_parts.append("version.dll")
        tp_summary = " and ".join(tp_info_parts)

        has_active_conflict = False
        if tp_dinput and tp_version:
            has_active_conflict = True
        elif tp_dinput and pref_dll == "dinput8.dll":
            has_active_conflict = True
        elif tp_version and pref_dll == "version.dll":
            has_active_conflict = True

        combo = getattr(self, "_dps_proxy_combo", None)
        if combo is not None and _is_valid(combo):
            normal_st = getattr(self, "_dps_proxy_combo_normal_style", "")
            conflict_st = getattr(self, "_dps_proxy_combo_conflict_style", "")
            if has_active_conflict and conflict_st:
                combo.setStyleSheet(conflict_st)
            elif normal_st:
                combo.setStyleSheet(normal_st)

        dll_lbl = getattr(self, "_dps_diag_dll_lbl", None)
        if dll_lbl is not None and _is_valid(dll_lbl):
            try:
                if binfo.get("detected"):
                    dll_lbl.setText(f"DLL: ● {binfo.get('dll_name')} (Active)")
                    dll_lbl.setStyleSheet(f"font-size: 13px; color: {theme.GOOD}; font-weight: 600;")
                elif binfo.get("proxy_in_game_dir"):
                    if tp_dinput or tp_version:
                        dll_lbl.setText(f"DLL: ● {binfo.get('dll_name')} (FareverPal) + 3rd-party {tp_summary}")
                        dll_lbl.setStyleSheet("font-size: 13px; color: #38BDF8; font-weight: 700;")
                    else:
                        dll_lbl.setText(f"DLL: ● {binfo.get('dll_name')} in folder (Ready for launch)")
                        dll_lbl.setStyleSheet("font-size: 13px; color: #38BDF8; font-weight: 600;")
                elif tp_dinput or tp_version:
                    if has_active_conflict:
                        alt_suggest = "version.dll" if tp_dinput else "dinput8.dll"
                        dll_lbl.setText(f"DLL: 🚨 3rd-party {tp_summary} in folder (Conflict — switch to {alt_suggest})")
                        dll_lbl.setStyleSheet("font-size: 13px; color: #EF4444; font-weight: 800;")
                    else:
                        dll_lbl.setText(f"DLL: ℹ 3rd-party {tp_summary} in folder (Coexisting via {pref_dll})")
                        dll_lbl.setStyleSheet("font-size: 13px; color: #38BDF8; font-weight: 700;")
                elif pid:
                    dll_lbl.setText("DLL: ○ None detected in process")
                    dll_lbl.setStyleSheet(f"font-size: 13px; color: {theme.MUTED};")
                else:
                    dll_lbl.setText("DLL: ○ Not attached")
                    dll_lbl.setStyleSheet(f"font-size: 13px; color: {theme.MUTED};")
            except Exception:
                pass

        src_lbl = getattr(self, "_dps_diag_source_lbl", None)
        if src_lbl is not None and _is_valid(src_lbl):
            try:
                src_name = getattr(dm, "live_source_name", lambda: f"Option: {mode}")() if dm else f"Option: {mode}"
                src_lbl.setText(f"Active Source: {src_name}")
            except Exception:
                pass

        status_lbl = getattr(self, "_dps_diag_status_lbl", None)
        if status_lbl is not None and _is_valid(status_lbl):
            try:
                st = getattr(dm, "status", lambda: "ready")() if dm else "ready"
                if has_active_conflict:
                    which_tp = "both proxy DLLs" if (tp_dinput and tp_version) else ("dinput8.dll" if tp_dinput else "version.dll")
                    status_lbl.setText(f"Status: 🚨 Mod conflict: another mod uses {which_tp}")
                    status_lbl.setStyleSheet("font-size: 13px; font-weight: 700; color: #EF4444;")
                elif st == "live":
                    status_lbl.setText("Status: ● LIVE (Streaming Hits)")
                    status_lbl.setStyleSheet("font-size: 13px; font-weight: 700; color: #10B981;")
                elif binfo.get("detected"):
                    status_lbl.setText(f"Status: ● ACTIVE IN GAME ({binfo.get('dll_name')})")
                    status_lbl.setStyleSheet("font-size: 13px; font-weight: 700; color: #10B981;")
                elif binfo.get("proxy_in_game_dir"):
                    if tp_dinput or tp_version:
                        status_lbl.setText(f"Status: ● READY ({binfo.get('dll_name')} in folder + 3rd-party mod preserved)")
                        status_lbl.setStyleSheet("font-size: 13px; font-weight: 700; color: #10B981;")
                    else:
                        status_lbl.setText(f"Status: ● READY ({binfo.get('dll_name')} in game folder)")
                        status_lbl.setStyleSheet("font-size: 13px; font-weight: 700; color: #10B981;")
                elif tp_dinput or tp_version:
                    which_tp = "dinput8.dll" if tp_dinput else "version.dll"
                    alt_needed = "version.dll" if tp_dinput else "dinput8.dll"
                    status_lbl.setText(f"Status: ⚠ 3rd-party {which_tp} in folder (Click 'Install DLL' to add {alt_needed})")
                    status_lbl.setStyleSheet("font-size: 13px; font-weight: 700; color: #F59E0B;")
                elif st == "missing_proxy":
                    target_dll = getattr(self.s, "dps_proxy_dll", "version.dll") or "version.dll"
                    status_lbl.setText(f"Status: ⚠ {target_dll} not in game folder")
                    status_lbl.setStyleSheet("font-size: 13px; font-weight: 700; color: #F59E0B;")
                elif st == "need_inject":
                    status_lbl.setText("Status: ⚠ farever_dps.dll not injected")
                    status_lbl.setStyleSheet("font-size: 13px; font-weight: 700; color: #F59E0B;")
                else:
                    status_lbl.setText("Status: Standing by")
                    status_lbl.setStyleSheet(f"font-size: 13px; font-weight: 700; color: {theme.MUTED};")
            except Exception:
                pass

        # Single 3rd-party DLL notice in the DPS header (compact, no boxes)
        conflict_line = getattr(self, "_dps_conflict_line", None)
        if conflict_line is not None and _is_valid(conflict_line):
            try:
                if tp_dinput or tp_version:
                    if tp_dinput and tp_version:
                        conflict_line.setText(
                            "🚨 Other mods use both version.dll and dinput8.dll in the game folder — "
                            "use Option 2 (Injector) or Option 3 (Memory Reader)."
                        )
                        conflict_line.setStyleSheet(
                            "font-size: 12px; font-weight: 700; color: #F59E0B;")
                    elif has_active_conflict:
                        alt_suggest = "version.dll" if tp_dinput else "dinput8.dll"
                        conflict_line.setText(
                            f"🚨 Another mod uses {tp_summary} in the game folder — "
                            f"switch the Proxy DLL to {alt_suggest} or use Option 2 / 3."
                        )
                        conflict_line.setStyleSheet(
                            "font-size: 12px; font-weight: 700; color: #F59E0B;")
                    else:
                        conflict_line.setText(
                            f"ℹ 3rd-party {tp_summary} found in the game folder — "
                            f"FareverPal is safely on {pref_dll}."
                        )
                        conflict_line.setStyleSheet(
                            "font-size: 12px; font-weight: 700; color: #38BDF8;")
                    conflict_line.setVisible(True)
                else:
                    conflict_line.setVisible(False)
            except Exception:
                pass

        # Log to Activity Log once when a 3rd-party DLL is detected
        if tp_dinput or tp_version:
            if not getattr(self, "_logged_conflict_disk", False):
                self._logged_conflict_disk = True
                which = "dinput8.dll and version.dll" if (tp_dinput and tp_version) else ("dinput8.dll" if tp_dinput else "version.dll")
                if hasattr(self, "log"):
                    self.log(f"Combat Bridge: 🚨 Detected another mod using {which} in game directory ({g_dir})")
        else:
            self._logged_conflict_disk = False

    def _find_farever_game_dir(self) -> str:
        """Find the game installation directory containing Farever.exe.
        
        Cached in memory and settings to ensure instantaneous repeated lookups.
        """
        import os
        from pathlib import Path

        # 0. In-memory cache (0ms)
        cached = getattr(self, "_cached_farever_game_dir", "")
        if cached and os.path.isdir(cached):
            return cached

        # 1. Check saved setting (0ms)
        saved = getattr(self.s, "farever_game_dir", "")
        if saved and os.path.isdir(saved):
            self._cached_farever_game_dir = saved
            return saved

        # 2. Check standard common Steam install paths on Windows
        common_candidates = [
            r"D:\Steam\steamapps\common\Farever",
            r"C:\Program Files (x86)\Steam\steamapps\common\Farever",
            r"C:\Steam\steamapps\common\Farever",
            r"E:\Steam\steamapps\common\Farever",
            r"F:\Steam\steamapps\common\Farever",
        ]
        for cand in common_candidates:
            if os.path.isdir(cand):
                self._cached_farever_game_dir = cand
                self._set("farever_game_dir", cand)
                return cand

        # 3. Check live process if attached
        proc = getattr(self.model, "proc", None) if hasattr(self, "model") and self.model else getattr(self, "proc", None)
        if proc:
            bfn = getattr(proc, "detect_combat_bridge", None)
            if callable(bfn):
                try:
                    binfo = bfn()
                    g_dir = binfo.get("game_dir", "")
                    if g_dir and os.path.isdir(g_dir):
                        self._cached_farever_game_dir = g_dir
                        self._set("farever_game_dir", g_dir)
                        return g_dir
                except Exception:
                    pass

        # 4. Check Steam via Windows Registry and libraryfolders.vdf
        steam_roots = []
        try:
            import winreg
            for hkey in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                for subkey in (
                    r"Software\Valve\Steam",
                    r"SOFTWARE\WOW6432Node\Valve\Steam",
                ):
                    try:
                        with winreg.OpenKey(hkey, subkey) as key:
                            for val_name in ("SteamPath", "InstallPath"):
                                try:
                                    val, _ = winreg.QueryValueEx(key, val_name)
                                    if val and os.path.isdir(val):
                                        p_val = Path(val)
                                        if p_val not in steam_roots:
                                            steam_roots.append(p_val)
                                except Exception:
                                    pass
                    except Exception:
                        pass
        except Exception:
            pass

        # Check libraryfolders.vdf from detected Steam roots
        all_library_folders = set(steam_roots)
        for s_root in steam_roots:
            vdf_path = s_root / "steamapps" / "libraryfolders.vdf"
            if vdf_path.is_file():
                try:
                    import re
                    text = vdf_path.read_text(encoding="utf-8", errors="ignore")
                    for match in re.finditer(r'"path"\s+"([^"]+)"', text, re.IGNORECASE):
                        raw = match.group(1).replace("\\\\", "\\")
                        lib_dir = Path(raw)
                        if lib_dir.is_dir():
                            all_library_folders.add(lib_dir)
                except Exception:
                    pass

        # Direct existence checks in library folders
        for lib in all_library_folders:
            cand = lib / "steamapps" / "common" / "Farever"
            if cand.is_dir():
                found = str(cand.resolve())
                self._cached_farever_game_dir = found
                self._set("farever_game_dir", found)
                return found

        return ""

    def _scan_farever_game_dir(self) -> None:
        """Manually point the DPS DLL check at the game folder.

        For custom install paths auto-detection can't find (non-standard Steam
        libraries, launchers, symlinks): pick the folder, persist it as
        farever_game_dir and re-run the diagnostics immediately so the DLL
        check uses it. A soft warning appears if the folder doesn't contain
        Farever.exe, but the choice is still saved.
        """
        import os
        start_dir = self._find_farever_game_dir() or "C:\\"
        dlg_dir = QtWidgets.QFileDialog.getExistingDirectory(
            self if isinstance(self, QtWidgets.QWidget) else None,
            "Select Farever Game Folder (contains Farever.exe)",
            start_dir,
        )
        if not dlg_dir or not os.path.isdir(dlg_dir):
            return
        norm = os.path.normpath(dlg_dir)
        # Drop the in-memory cache so the newly saved path is used immediately.
        self._cached_farever_game_dir = ""
        self._set("farever_game_dir", norm)
        if hasattr(self, "log"):
            self.log(f"DPS DLL check folder set to {norm}")
        if not os.path.isfile(os.path.join(norm, "Farever.exe")):
            fb = getattr(self, "_dps_proxy_feedback", None)
            if fb is not None:
                fb.setText(f"ℹ {norm} does not contain Farever.exe — is this the game folder?")
                fb.setStyleSheet("color: #F59E0B; font-size: 12px;")
        self._refresh_dps_diagnostics()

    def _open_farever_game_dir(self) -> None:
        """Open the game install folder in Windows Explorer."""
        import os
        from pathlib import Path
        game_dir = self._find_farever_game_dir()
        if not game_dir:
            dlg_dir = QtWidgets.QFileDialog.getExistingDirectory(
                self if isinstance(self, QtWidgets.QWidget) else None,
                "Locate Farever Installation Folder (containing Farever.exe)",
                "C:\\"
            )
            if dlg_dir and os.path.isdir(dlg_dir):
                game_dir = dlg_dir
                self._cached_farever_game_dir = ""
                self._set("farever_game_dir", game_dir)
                if hasattr(self, "log"):
                    self.log(f"Set Farever game folder: {game_dir}")
                self._refresh_dps_diagnostics()
            else:
                try:
                    QtGui.QDesktopServices.openUrl(QtCore.QUrl("steam://nav/games/details/3672400"))
                except Exception:
                    pass
                return

        try:
            norm_path = os.path.normpath(game_dir)
            if os.path.isdir(norm_path):
                if hasattr(os, "startfile"):
                    os.startfile(norm_path)
                else:
                    QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(norm_path))
                if hasattr(self, "log"):
                    self.log(f"Opened game folder: {norm_path}")
                if hasattr(self, "_dps_proxy_feedback") and self._dps_proxy_feedback:
                    self._dps_proxy_feedback.setText(f"📁 Opened: {norm_path}")
                    self._dps_proxy_feedback.setStyleSheet("color: #38BDF8; font-size: 12px;")
        except Exception as e:
            if hasattr(self, "log"):
                self.log(f"Could not open game folder: {e}")

    def _install_proxy_dll(self) -> None:
        """Copy chosen proxy DLL (version.dll or dinput8.dll) into the Farever game folder."""
        import os
        import shutil
        from pathlib import Path
        from ...core.proc import is_farever_proxy, get_bridge_dir

        chosen_dll = getattr(self.s, "dps_proxy_dll", "version.dll") or "version.dll"

        b_dir = get_bridge_dir()
        src_dll = Path(b_dir) / chosen_dll if b_dir else None

        if not src_dll or not src_dll.is_file():
            if hasattr(self, "_dps_proxy_feedback") and self._dps_proxy_feedback:
                self._dps_proxy_feedback.setText(f"⚠ Source {chosen_dll} not found in dps_bridge/")
                self._dps_proxy_feedback.setStyleSheet("color: #EF4444; font-size: 12px;")
            if hasattr(self, "log"):
                self.log(f"Install Proxy: source {chosen_dll} not found in dps_bridge/")
            return

        game_dir = self._find_farever_game_dir()
        if not game_dir:
            dlg_dir = QtWidgets.QFileDialog.getExistingDirectory(
                self if isinstance(self, QtWidgets.QWidget) else None,
                f"Select Farever Folder to copy {chosen_dll} into",
                "C:\\"
            )
            if dlg_dir and os.path.isdir(dlg_dir):
                game_dir = dlg_dir
                self._set("farever_game_dir", game_dir)
            else:
                if hasattr(self, "_dps_proxy_feedback") and self._dps_proxy_feedback:
                    self._dps_proxy_feedback.setText("⚠ Game folder not found. Click 'Open Folder' to locate.")
                    self._dps_proxy_feedback.setStyleSheet("color: #F59E0B; font-size: 12px;")
                return

        # Conflict check: if user wants to install a DLL, but a 3rd party mod is already using that DLL
        dst_target = Path(game_dir) / chosen_dll
        if dst_target.is_file() and not is_farever_proxy(str(dst_target)):
            alt_dll = "version.dll" if chosen_dll == "dinput8.dll" else "dinput8.dll"
            err = f"⚠ Another mod is using {chosen_dll} in this folder! Overwriting would break your other mod. Install {alt_dll} instead."
            if hasattr(self, "_dps_proxy_feedback") and self._dps_proxy_feedback:
                self._dps_proxy_feedback.setText(err)
                self._dps_proxy_feedback.setStyleSheet("color: #EF4444; font-size: 12px; font-weight: 700;")
            if hasattr(self, "log"):
                self.log(f"Combat Bridge: {err}")
            self._refresh_dps_diagnostics()
            return

        try:
            # Clean up old Farever proxy of the other type if present, so we don't duplicate proxies
            other_name = "dinput8.dll" if chosen_dll == "version.dll" else "version.dll"
            other_path = Path(game_dir) / other_name
            if other_path.is_file() and is_farever_proxy(str(other_path)):
                try:
                    other_path.unlink()
                    if hasattr(self, "log"):
                        self.log(f"Combat Bridge: Cleaned up previous Farever proxy ({other_name})")
                except Exception:
                    pass

            dst_dll = Path(game_dir) / chosen_dll
            shutil.copy2(str(src_dll), str(dst_dll))
            msg = f"✓ Installed {chosen_dll} to game folder! Restart Farever.exe to activate."
            if hasattr(self, "_dps_proxy_feedback") and self._dps_proxy_feedback:
                self._dps_proxy_feedback.setText(msg)
                self._dps_proxy_feedback.setStyleSheet("color: #10B981; font-size: 12px; font-weight: 700;")
            if hasattr(self, "log"):
                self.log(f"Combat Bridge: {msg} ({dst_dll})")
            self._refresh_dps_diagnostics()
        except PermissionError:
            err = f"Farever.exe is running and locking {chosen_dll}. Please close the game first, then click Install DLL."
            if hasattr(self, "_dps_proxy_feedback") and self._dps_proxy_feedback:
                self._dps_proxy_feedback.setText(f"⚠ {err}")
                self._dps_proxy_feedback.setStyleSheet("color: #EF4444; font-size: 12px;")
            if hasattr(self, "log"):
                self.log(f"Combat Bridge: {err}")
        except Exception as e:
            err = f"Failed to copy {chosen_dll}: {e}"
            if hasattr(self, "_dps_proxy_feedback") and self._dps_proxy_feedback:
                self._dps_proxy_feedback.setText(f"⚠ {err}")
                self._dps_proxy_feedback.setStyleSheet("color: #EF4444; font-size: 12px;")
            if hasattr(self, "log"):
                self.log(f"Combat Bridge: {err}")

    def _remove_proxy_dll(self) -> None:
        """Remove FareverPal proxy DLL from the Farever game folder (preserves 3rd-party mods)."""
        import os
        from pathlib import Path
        from ...core.proc import is_farever_proxy, unload_module, find_pid

        game_dir = self._find_farever_game_dir()
        if not game_dir:
            dlg_dir = QtWidgets.QFileDialog.getExistingDirectory(
                self if isinstance(self, QtWidgets.QWidget) else None,
                "Select Farever Folder to remove proxy DLL from",
                "C:\\"
            )
            if dlg_dir and os.path.isdir(dlg_dir):
                game_dir = dlg_dir
                self._set("farever_game_dir", game_dir)
            else:
                if hasattr(self, "_dps_proxy_feedback") and self._dps_proxy_feedback:
                    self._dps_proxy_feedback.setText("⚠ Game folder not found.")
                    self._dps_proxy_feedback.setStyleSheet("color: #F59E0B; font-size: 12px;")
                return

        candidates = ["version.dll", "dinput8.dll"]
        farever_files_to_remove: list[Path] = []
        third_party_files: list[str] = []

        for cand in candidates:
            cand_path = Path(game_dir) / cand
            if cand_path.is_file():
                if is_farever_proxy(str(cand_path)):
                    farever_files_to_remove.append(cand_path)
                else:
                    third_party_files.append(cand)

        if not farever_files_to_remove:
            if third_party_files:
                msg = f"ℹ No FareverPal proxy found. Preserved 3rd-party mod: {', '.join(third_party_files)}."
            else:
                msg = "ℹ No FareverPal proxy DLL found in the game folder (already clean)."
            if hasattr(self, "_dps_proxy_feedback") and self._dps_proxy_feedback:
                self._dps_proxy_feedback.setText(msg)
                self._dps_proxy_feedback.setStyleSheet("color: #38BDF8; font-size: 12px;")
            if hasattr(self, "log"):
                self.log(f"Combat Bridge: {msg}")
            return

        proc_pid = None
        try:
            proc_pid = find_pid("Farever.exe") or find_pid("farever.exe")
        except Exception:
            proc = getattr(self, "proc", None)
            if proc and hasattr(proc, "pid"):
                proc_pid = proc.pid

        removed_any = False
        locked_files = []

        for fpath in farever_files_to_remove:
            try:
                fpath.unlink()
                removed_any = True
                if hasattr(self, "log"):
                    self.log(f"Combat Bridge: Removed {fpath.name} from {game_dir}")
            except PermissionError:
                locked_files.append(fpath)
            except Exception as e:
                if hasattr(self, "log"):
                    self.log(f"Combat Bridge: Error removing {fpath.name}: {e}")

        # If locked and process is running, attempt hot-unload
        if locked_files and proc_pid:
            import time
            for lf in list(locked_files):
                try:
                    if hasattr(self, "log"):
                        self.log(f"Combat Bridge: Attempting hot unload of {lf.name} from PID {proc_pid}…")
                    unloaded = unload_module(proc_pid, lf.name)
                    if unloaded:
                        time.sleep(0.25)
                        lf.unlink()
                        removed_any = True
                        locked_files.remove(lf)
                        if hasattr(self, "log"):
                            self.log(f"Combat Bridge: ✓ Hot-unloaded and removed {lf.name}!")
                except Exception:
                    pass

        if locked_files:
            names = ", ".join(f.name for f in locked_files)
            msg = f"⚠ Farever.exe (PID {proc_pid}) has {names} locked. Please close Farever.exe to release the file lock."
            if hasattr(self, "_dps_proxy_feedback") and self._dps_proxy_feedback:
                self._dps_proxy_feedback.setText(msg)
                self._dps_proxy_feedback.setStyleSheet("color: #EF4444; font-size: 12px; font-weight: 600;")
            if hasattr(self, "log"):
                self.log(f"Combat Bridge: Cannot remove {names} while locked.")
            return

        if removed_any:
            msg = "✓ Removed FareverPal proxy DLL from game folder! Your game directory is clean."
            if third_party_files:
                msg += f" (Preserved 3rd-party {', '.join(third_party_files)})"
            if hasattr(self, "_dps_proxy_feedback") and self._dps_proxy_feedback:
                self._dps_proxy_feedback.setText(msg)
                self._dps_proxy_feedback.setStyleSheet("color: #10B981; font-size: 12px; font-weight: 700;")
            if hasattr(self, "log"):
                self.log(f"Combat Bridge: {msg}")
            self._refresh_dps_diagnostics()

    def _open_dps_bridge_dir(self) -> None:
        try:
            from pathlib import Path
            d = Path(__file__).resolve().parents[2] / "dps_bridge"
            if not d.exists():
                d = Path("D:/1MobileApp/vs/FareverPal-SC/FareverPal/dps_bridge")
            if d.exists():
                QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(d)))
        except Exception as e:
            if hasattr(self, "log"):
                self.log(f"Open dps_bridge folder failed: {e}")

    def _test_uninject_now(self) -> None:
        # Immediately switch DPS capture mode to Option 3 (Memory Reader)
        # This persists to settings.json so next time FareverPal/game opens,
        # it does NOT auto-inject farever_dps.dll!
        self._set_dps_mode_key("memory")

        proc = getattr(self, "proc", None)
        if proc is None and hasattr(self, "model"):
            proc = getattr(self.model, "proc", None)
        pid = getattr(proc, "pid", None) if proc else None
        if not pid:
            try:
                from ...core.proc import find_pid
                pid = find_pid("Farever.exe") or find_pid("farever.exe")
            except Exception:
                pass
        if not pid:
            if hasattr(self, "_dps_inject_feedback"):
                self._dps_inject_feedback.setText("✓ Switched to Option 3 (Memory Reader) · Game not running")
                self._dps_inject_feedback.setStyleSheet("color: #10B981; font-size: 12px; font-weight: 700;")
                self._dps_inject_feedback.setVisible(True)
            self._refresh_dps_diagnostics()
            return
        from ..game_attach import _try_uninject_hook
        log_cb = getattr(self, "log", None)
        _try_uninject_hook(pid, log_cb=log_cb)
        if hasattr(self, "_dps_inject_feedback"):
            self._dps_inject_feedback.setText(f"✓ Uninjected from PID {pid} & switched to Option 3 (Memory Reader)")
            self._dps_inject_feedback.setStyleSheet("color: #10B981; font-size: 12px; font-weight: 700;")
            self._dps_inject_feedback.setVisible(True)
        self._refresh_dps_diagnostics()

    def _inject_test_hit(self) -> None:
        """Push a fake damage event into the DamageSourceManager's ring so the overlay
        can display it without needing real combat. Useful for verifying wiring."""
        dm = getattr(self.model, "damage", None) if hasattr(self, "model") and self.model else None
        if dm is None:
            if hasattr(self, "log"):
                self.log("Test Hit: cannot inject — model.damage is None (not attached?)")
            return

        from ...core.damage_events import DamageEvent, K_DAMAGE
        import time

        ev = DamageEvent(
            amount=9999.0,
            skill="TestHit",
            skill_name="Test Hit ⚡",
            crit=True,
            kill=False,
            kind=K_DAMAGE,
            source_name="You",
            target_name="Test Dummy",
            is_me=True,
            incoming=False,
            t=time.time(),
        )
        try:
            dm._push([ev])
            if hasattr(self, "log"):
                self.log("Test Hit: injected 9999 dmg → Test Dummy (check overlay)")
        except Exception as e:
            if hasattr(self, "log"):
                self.log(f"Test Hit: injection failed ({e})")

    def _build_minimap(self, v) -> None:
        if not hasattr(self, "_settings_steppers") or self._settings_steppers is None:
            self._settings_steppers = []

        shape = C.SegmentedControl(["Circle", "Square"], self.s.minimap_shape)
        shape.currentChanged.connect(self._set_minimap_shape)
        self._minimap_shape = shape
        v.addWidget(shape)

        items = [
            ("minimap_bare", "Borderless", self.s.minimap_bare, self._set_minimap_bare),
            ("minimap_transparent", "Transparent", getattr(self.s, "minimap_transparent", False), self._set_minimap_transparent),
            ("minimap_rotate", "Rotate Map", self.s.minimap_rotate, self._set_minimap_rotate),
            ("minimap_texture", "Map Texture", self.s.minimap_texture, self._set_minimap_texture),
            ("minimap_icons", "POI Icons", self.s.minimap_icons,
             lambda on: (self._set("minimap_icons", on), self._touch_minimap())),
            ("minimap_hide_collected", "Hide Collected", self.s.minimap_hide_collected,
             self._set_minimap_hide_collected),
            ("minimap_limit_by_zone", "Limit Range (400m)", self.s.minimap_limit_by_zone,
             lambda on: (self._set("minimap_limit_by_zone", on), self._touch_minimap())),
        ]
        grid_widget = C.SegmentedGrid(items, cols=4)
        v.addWidget(grid_widget)
        self._minimap_toggles: list[tuple[str, QtWidgets.QPushButton]] = [
            (k, grid_widget.btn(k)) for k, _, _, _ in items
        ]
        v.addSpacing(6)

        # --- SCALE & ZOOM ---
        sz_lbl = QtWidgets.QLabel("SCALE & ZOOM")
        sz_lbl.setStyleSheet(
            f"font-family:'{theme.MONO_FONT}','Consolas',monospace;font-size:10px;"
            f"font-weight:700;letter-spacing:1px;color:{theme.ACCENT};background:transparent;")
        v.addWidget(sz_lbl)

        szrow = QtWidgets.QHBoxLayout()
        szrow.setSpacing(8)

        size = C.Stepper(self.s.minimap_size, 160, 640, 20)
        size.valueChanged.connect(self._set_minimap_size)
        isz = C.Stepper(self.s.minimap_icon_size, 8, 40, 2)
        isz.valueChanged.connect(
            lambda v_: (self._set("minimap_icon_size", v_),
                        self._touch_minimap()))
        self._settings_steppers.append(("minimap_size", size))
        self._settings_steppers.append(("minimap_icon_size", isz))
        szrow.addWidget(C.Field("Size (px)", size), 1)
        szrow.addWidget(C.Field("POI icon (px)", isz), 1)
        v.addLayout(szrow)

        zoom = C.SliderRow("Map Zoom", 2, 60, int(self.s.minimap_zoom), str)
        zoom.valueChanged.connect(self._set_minimap_zoom)
        self._settings_zoom = zoom
        v.addWidget(zoom)
        v.addSpacing(6)

        # Callout helper box
        tip_box = QtWidgets.QFrame()
        tip_box.setObjectName("Cell")
        tip_box.setStyleSheet(
            f"QFrame#Cell {{ background-color: {theme.with_alpha(theme.PANEL_HI, 40)}; "
            f"border: 1px solid {theme.BORDER}; border-radius: 4px; }}")
        tip_lay = QtWidgets.QVBoxLayout(tip_box)
        tip_lay.setContentsMargins(10, 8, 10, 8)
        tip_lay.setSpacing(4)

        note_hdr = QtWidgets.QLabel("MINIMAP CONTROLS")
        note_hdr.setStyleSheet(
            f"font-family:'{theme.MONO_FONT}','Consolas',monospace;font-size:11px;"
            f"font-weight:700;letter-spacing:1px;color:{theme.GOLD};background:transparent;")
        tip_lay.addWidget(note_hdr)

        note = QtWidgets.QLabel(
            "• Right-click marker: Toggle done / undone (persists across sessions)\n"
            "• Hold right-click & drag: Pan around the minimap radar\n"
            "• Auto-sync: Nearby chests & orbs sync status in real time\n"
            "• POI visibility: Toggle specific layers in the World Layers tab above")
        note.setStyleSheet(f"color:{theme.TEXT};font-size:13px;line-height:1.5;background:transparent;")
        note.setWordWrap(True)
        tip_lay.addWidget(note)
        v.addWidget(tip_box)

    # --- rift schedule (modern tactical cards & offline predictor) ---------
    # --- rift schedule (modern tactical cards & offline predictor) ---------
    def _build_rift(self, v) -> None:
        v.setSpacing(14)

        # ---- Configuration Cards (Dual Columns at the top) ---------------
        config_grid = QtWidgets.QGridLayout()
        config_grid.setHorizontalSpacing(14)
        config_grid.setVerticalSpacing(14)

        # 1. Overlay Display Settings Card
        display_card = QtWidgets.QFrame()
        display_card.setObjectName("RiftCardBody")
        display_card.setStyleSheet(
            f"QFrame#RiftCardBody {{ background-color: {theme.PANEL}; "
            f"border: 1px solid {theme.BORDER}; border-radius: 6px; }}")
        dlay = QtWidgets.QVBoxLayout(display_card)
        dlay.setContentsMargins(14, 12, 14, 14)
        dlay.setSpacing(10)

        dhdr = C.SectionHeader("Overlay Display", tag="HUD & NOTIFICATIONS")
        dlay.addWidget(dhdr)

        dsub = QtWidgets.QLabel("Choose when the rift countdown appears on your in-game HUD.")
        dsub.setObjectName("Muted")
        dsub.setWordWrap(True)
        dlay.addWidget(dsub)

        raw = getattr(self.s, "show_rift_timer", "Always")
        if isinstance(raw, bool):
            curr = "Always" if raw else "Off"
        elif str(raw).lower() in ("true", "always"):
            curr = "Always"
        elif str(raw).lower() in ("false", "off"):
            curr = "Off"
        elif str(raw).lower() == "active":
            curr = "Active"
        else:
            curr = "Always"
        self._rift_seg = C.SegmentedControl(["Off", "Active", "Always"], current=curr)
        self._rift_seg.currentChanged.connect(lambda k: self._set("show_rift_timer", k))
        dlay.addWidget(C.Field("Display Mode", self._rift_seg))

        # Mode quick info legend
        legend = QtWidgets.QLabel(
            "• Always: Visible on HUD at all times\n"
            "• Active: Visible during 15m warning & live event\n"
            "• Off: Completely hidden on overlays")
        legend.setStyleSheet(f"color:{theme.DIM};font-size:11px;line-height:1.4;background:transparent;")
        legend.setWordWrap(True)
        dlay.addWidget(legend)
        dlay.addStretch(1)
        config_grid.addWidget(display_card, 0, 0)

        # 2. Audio Chimes & Alert Settings Card
        alerts_card = QtWidgets.QFrame()
        alerts_card.setObjectName("RiftCardBody")
        alerts_card.setStyleSheet(
            f"QFrame#RiftCardBody {{ background-color: {theme.PANEL}; "
            f"border: 1px solid {theme.BORDER}; border-radius: 6px; }}")
        alay = QtWidgets.QVBoxLayout(alerts_card)
        alay.setContentsMargins(14, 12, 14, 14)
        alay.setSpacing(10)

        ahdr = C.SectionHeader("Audio Alerts", tag="CHIME REMINDERS")
        alay.addWidget(ahdr)

        asub = QtWidgets.QLabel("Play a reminder chime before the rift opens.")
        asub.setObjectName("Muted")
        asub.setWordWrap(True)
        alay.addWidget(asub)

        # Lead time toggle chips (housed in QFrame#Cell matching SegmentedControl)
        selected = self._rift_ding_selected()
        self._rift_ding_chips: dict[int, QtWidgets.QPushButton] = {}
        chip_widget = QtWidgets.QFrame()
        chip_widget.setObjectName("Cell")
        chip_lay = QtWidgets.QHBoxLayout(chip_widget)
        chip_lay.setContentsMargins(3, 3, 3, 3)
        chip_lay.setSpacing(3)
        for m in (15, 10, 5, 1, 0):
            chip = QtWidgets.QPushButton("Live" if m == 0 else f"{m}m")
            chip.setCheckable(True)
            chip.setChecked(m in selected)
            chip.setCursor(QtCore.Qt.PointingHandCursor)
            chip.setFixedHeight(32)
            chip.clicked.connect(lambda on, mm=m: self._toggle_rift_ding(mm, on))
            self._style_rift_chip(chip)
            self._rift_ding_chips[m] = chip
            chip_lay.addWidget(chip, 1)
        alay.addWidget(C.Field("Lead Time", chip_widget))

        # Audio chime picker (housed in QFrame#Cell matching SegmentedControl)
        self._rift_sound_keys = list_sounds()
        sound_opts = [SOUND_LABELS.get(k, k) for k in self._rift_sound_keys]
        rows = 1 if len(self._rift_sound_keys) <= 8 else 2
        cols = (len(self._rift_sound_keys) + rows - 1) // rows
        picker = QtWidgets.QFrame()
        picker.setObjectName("Cell")
        pg = QtWidgets.QGridLayout(picker)
        pg.setContentsMargins(3, 3, 3, 3)
        pg.setHorizontalSpacing(3)
        pg.setVerticalSpacing(3)
        pgroup = QtWidgets.QButtonGroup(picker)
        pgroup.setExclusive(True)
        self._rift_sound_btns: dict[str, QtWidgets.QPushButton] = {}

        def _snd_qss() -> str:
            return (
                f"QPushButton {{"
                f"  background: transparent;"
                f"  border: 0;"
                f"  padding: 6px 8px;"
                f"  color: {theme.MUTED};"
                f"  font-size: 13px;"
                f"  font-weight: 600;"
                f"}}"
                f"QPushButton:hover {{"
                f"  color: {theme.TEXT};"
                f"  background: {theme.with_alpha(theme.PANEL_HI, 90)};"
                f"}}"
                f"QPushButton:checked {{"
                f"  background: {theme.with_alpha(theme.ACCENT, 40)};"
                f"  border: 0;"
                f"  color: {theme.ACCENT};"
                f"  font-weight: 600;"
                f"}}"
                f"QPushButton:checked:hover {{"
                f"  background: {theme.with_alpha(theme.ACCENT, 55)};"
                f"}}"
            )

        def _on_snd(b: QtWidgets.QPushButton) -> None:
            for k, bb in self._rift_sound_btns.items():
                if bb is b:
                    self._set("rift_sound", k)
                    play_sound(k)
                    return

        for i, k in enumerate(self._rift_sound_keys):
            b = QtWidgets.QPushButton(sound_opts[i])
            b.setCheckable(True)
            b.setCursor(QtCore.Qt.PointingHandCursor)
            b.setFixedHeight(32)
            b.setStyleSheet(_snd_qss())
            pgroup.addButton(b)
            self._rift_sound_btns[k] = b
            pg.addWidget(b, i // cols, i % cols)
        pgroup.buttonClicked.connect(_on_snd)
        cur_key = getattr(self.s, "rift_sound", "ding")
        if cur_key in self._rift_sound_btns:
            self._rift_sound_btns[cur_key].setChecked(True)
        self._rift_sound_seg = picker
        alay.addWidget(C.Field("Chime Sound", picker))
        alay.addStretch(1)
        config_grid.addWidget(alerts_card, 0, 1)

        v.addLayout(config_grid)

        # ---- Upcoming Schedule & Live Predictor (Unified Section) --------
        v.addWidget(C.SectionHeader("Upcoming Rift Schedule", tag="LIVE RADAR & TIMETABLE"))

        self._rift_card = QtWidgets.QFrame()
        self._rift_card.setObjectName("RiftHeroCard")
        self._rift_card.setStyleSheet(
            f"QFrame#RiftHeroCard {{ background-color: {theme.with_alpha(theme.PANEL, 230)}; "
            f"border: 1px solid rgba(79, 195, 247, 0.35); border-radius: 6px; }}")
        list_lay = QtWidgets.QVBoxLayout(self._rift_card)
        list_lay.setContentsMargins(14, 12, 14, 12)
        list_lay.setSpacing(10)

        # 1. Hero Live Status Banner in Schedule Card
        top_bar = QtWidgets.QHBoxLayout()
        top_bar.setContentsMargins(6, 6, 6, 8)
        top_bar.setSpacing(12)

        self._rift_hero_icon = QtWidgets.QLabel()
        self._rift_hero_icon.setFixedSize(28, 28)
        self._rift_hero_icon.setPixmap(icons.marker("rift", 28, accent=theme.ACCENT))
        top_bar.addWidget(self._rift_hero_icon, 0, QtCore.Qt.AlignVCenter)

        self._rift_next_lbl = QtWidgets.QLabel("Next Rift: --")
        self._rift_next_lbl.setStyleSheet(
            f"font-family:'{theme.MONO_FONT}','Consolas',monospace;font-size:16px;"
            f"font-weight:700;letter-spacing:0.5px;background:transparent;")
        top_bar.addWidget(self._rift_next_lbl, 1, QtCore.Qt.AlignVCenter)
        list_lay.addLayout(top_bar)

        # Divider
        th_div = QtWidgets.QFrame()
        th_div.setFixedHeight(1)
        th_div.setStyleSheet(f"background:{theme.BORDER};border:none;")
        list_lay.addWidget(th_div)

        # 2. Schedule Table Header
        th = QtWidgets.QHBoxLayout()
        th.setContentsMargins(8, 2, 8, 4)
        th.setSpacing(12)
        th_time = QtWidgets.QLabel("TIME")
        th_time.setFixedWidth(80)
        th_zone = QtWidgets.QLabel("ZONE / TARGET LOCATION")
        th_inc = QtWidgets.QLabel("STARTS IN")
        th_inc.setFixedWidth(100)
        th_inc.setAlignment(QtCore.Qt.AlignRight)
        for lbl in (th_time, th_zone, th_inc):
            lbl.setStyleSheet(
                f"font-family:'{theme.MONO_FONT}','Consolas',monospace;font-size:10px;"
                f"font-weight:700;letter-spacing:1px;color:{theme.DIM};background:transparent;")
        th.addWidget(th_time)
        th.addWidget(th_zone, 1)
        th.addWidget(th_inc)
        list_lay.addLayout(th)

        # 3. 7 Schedule Rows
        self._rift_rows: list[tuple[QtWidgets.QLabel, QtWidgets.QLabel, QtWidgets.QLabel]] = []
        for i in range(7):
            roww = QtWidgets.QWidget()
            roww.setObjectName("RiftRow")
            roww.setStyleSheet(
                "QWidget#RiftRow:hover { background-color: rgba(255, 255, 255, 0.03); border-radius: 4px; }")
            hl = QtWidgets.QHBoxLayout(roww)
            hl.setContentsMargins(8, 5, 8, 5)
            hl.setSpacing(12)

            c = QtWidgets.QLabel("--")
            z = QtWidgets.QLabel("--")
            inc = QtWidgets.QLabel("--")
            c.setFixedWidth(80)
            inc.setFixedWidth(100)
            inc.setAlignment(QtCore.Qt.AlignRight)

            c.setStyleSheet(
                f"font-family:'{theme.MONO_FONT}','Consolas',monospace;font-size:13px;"
                f"color:{theme.MUTED};background:transparent;border:none;")
            z.setStyleSheet(
                f"font-size:13px;font-weight:600;color:{theme.GOLD};background:transparent;border:none;")
            inc.setStyleSheet(
                f"font-family:'{theme.MONO_FONT}','Consolas',monospace;font-size:13px;"
                f"font-weight:600;color:{theme.ACCENT};background:transparent;border:none;")

            hl.addWidget(c)
            hl.addWidget(z, 1)
            hl.addWidget(inc)
            list_lay.addWidget(roww)
            self._rift_rows.append((c, z, inc))

        v.addWidget(self._rift_card)

        # Keep predictor ticking offline (pure clock + LCG, no memory).
        self._rift_page_timer = QtCore.QTimer(self._settings_stack)
        self._rift_page_timer.setInterval(1000)
        self._rift_page_timer.timeout.connect(self._update_settings_rift)
        self._rift_page_timer.start()
        self._update_settings_rift()

    def _card(self, title: str, subtitle: str):
        """A framed settings card: small caption row on top, then the body.
        Returns (frame, inner_layout) so the caller can add fields to the
        body and add the frame to the page."""
        frame = QtWidgets.QFrame()
        frame.setObjectName("RiftCardBody")
        frame.setStyleSheet(
            f"QFrame#RiftCardBody {{ background-color: {theme.PANEL}; "
            f"border: 1px solid {theme.BORDER}; border-radius: 6px; }}")
        lay = QtWidgets.QVBoxLayout(frame)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(10)
        title_lbl = QtWidgets.QLabel(title.upper())
        title_lbl.setObjectName("Section")
        title_lbl.setStyleSheet("color:" + theme.ACCENT + ";font-weight:700;")
        sub_lbl = QtWidgets.QLabel(subtitle)
        sub_lbl.setObjectName("Mono")
        sub_lbl.setStyleSheet("color:" + theme.MUTED + ";")
        lay.addWidget(title_lbl)
        lay.addWidget(sub_lbl)
        self._rift_cards = getattr(self, "_rift_cards", []) + [frame]
        return frame, lay

    def _update_settings_rift(self) -> None:
        if not hasattr(self, "_rift_next_lbl"):
            return
        from ...core.rift_tracker import rift_summary, upcoming_rifts
        r = rift_summary()
        GREEN = "#66BB6A"
        RED = "#EF5350"
        CYAN = theme.ACCENT
        secs = r.get("secs_until", 10**9)
        time_green = secs < 900 and r["state"] not in ("ACTIVE", "CLOSING")
        tcolor = GREEN if time_green else r["color"]

        # Update Target Zone and Icon Accent
        if hasattr(self, "_rift_hero_icon"):
            self._rift_hero_icon.setPixmap(icons.marker("rift", 28, accent=tcolor))

        # Update Next Rift status text (Blue label, Gold location, state-colored status)
        if hasattr(self, "_rift_next_lbl"):
            html = f'<font color="{theme.ACCENT}">{r["label"]}:  </font>'
            html += f'<font color="{theme.GOLD}">{r["zone"]}</font>'
            html += f'<font color="#87929a">   —   </font>'
            html += f'<font color="{tcolor}">{r["status"]}</font>'
            self._rift_next_lbl.setTextFormat(QtCore.Qt.RichText)
            self._rift_next_lbl.setText(html)

        # Hero Card glow border
        if hasattr(self, "_rift_card"):
            self._rift_card.setStyleSheet(
                f"QFrame#RiftHeroCard {{ background-color: {theme.with_alpha(theme.PANEL, 230)}; "
                f"border: 1px solid {theme.with_alpha(tcolor, 160)}; "
                f"border-radius: 6px; }}")

        # Upcoming Schedule Rows (skip 1st since it's already in the Next Rift header above)
        for (c, z, inc), ev in zip(self._rift_rows, upcoming_rifts(count=len(self._rift_rows), offset=1)):
            c.setText(ev["clock"])
            z.setText(ev["zone"])
            inc.setText("in " + ev["in"])

    def _rift_ding_selected(self) -> set[int]:
        """Selected lead times (minutes), tolerant of the old string format."""
        raw = getattr(self.s, "rift_ding", ["15", "10", "5", "1"])
        if isinstance(raw, str):
            if raw.lower() == "off":
                return set()
            if raw.lower() == "all":
                return {15, 10, 5, 1}
            raw = [raw]
        try:
            return {int(str(x).replace("m", "")) for x in raw}
        except Exception:
            return {15, 10, 5, 1}

    def _toggle_rift_ding(self, minute: int, on: bool) -> None:
        sel = self._rift_ding_selected()
        if on:
            sel.add(minute)
        else:
            sel.discard(minute)
        self._set("rift_ding", sorted(sel))
        self._style_rift_chip(self._rift_ding_chips[minute])

    def _style_rift_chip(self, chip: QtWidgets.QPushButton) -> None:
        if chip.isChecked():
            chip.setStyleSheet(
                f"QPushButton {{"
                f"  background: {theme.with_alpha(theme.ACCENT, 40)};"
                f"  border: 0;"
                f"  color: {theme.ACCENT};"
                f"  font-size: 13px;"
                f"  font-weight: 600;"
                f"  padding: 6px 12px;"
                f"}}"
                f"QPushButton:hover {{ background: {theme.with_alpha(theme.ACCENT, 55)}; }}")
        else:
            chip.setStyleSheet(
                f"QPushButton {{"
                f"  background: transparent;"
                f"  border: 0;"
                f"  color: {theme.MUTED};"
                f"  font-size: 13px;"
                f"  font-weight: 600;"
                f"  padding: 6px 12px;"
                f"}}"
                f"QPushButton:hover {{ color: {theme.TEXT}; background: {theme.with_alpha(theme.PANEL_HI, 90)}; }}")

    # --- Entity HUD Card --------------------------------------------------
    def _build_entity_hud_card(self) -> QtWidgets.QFrame:
        card, lay = self._overlay_card_frame("Entity HUD", tag="TACTICAL SENSORS", sprite_marker="layers")
        self._build_display_config(lay)
        self._build_sizing_metrics(lay)
        self._build_loot_filters(lay)
        return card

    # --- display configuration (overlay behavior & toggles) --------------
    def _build_display_config(self, v) -> None:
        items = [
            ("entity_bare", "Borderless", self.s.entity_bare, self._set_entity_bare),
            ("entity_transparent", "Transparent", getattr(self.s, "entity_transparent", False), self._set_entity_transparent),
            ("show_compass", "Compass Needle", self.s.show_compass, self._set_show_compass),
            ("auto_select_next_collectible", "Auto-Track Next", self.s.auto_select_next_collectible,
             lambda on: self._set("auto_select_next_collectible", on)),
            ("limit_by_zone", "Limit by Zone", self.s.limit_by_zone,
             lambda on: self._set("limit_by_zone", on)),
            ("entity_hide_collected", "Hide Collected", self.s.entity_hide_collected,
             lambda on: self._set("entity_hide_collected", on)),
            ("PlayerNames", "Player Names", getattr(self.s, "PlayerNames", False),
             lambda on: self._set("PlayerNames", on)),
        ]
        grid_widget = C.SegmentedGrid(items, cols=4)
        v.addWidget(grid_widget)
        v.addSpacing(4)

        self._hud_toggles: list[tuple[str, QtWidgets.QPushButton]] = [
            (k, grid_widget.btn(k)) for k, _, _, _ in items
        ]

    # --- sizing & metrics (from the Entity page) --------------------------
    def _build_sizing_metrics(self, v) -> None:
        if not hasattr(self, "_settings_steppers") or self._settings_steppers is None:
            self._settings_steppers = []

        # 1. Dimensions & Distance (Compact 4-col single row)
        dim_lbl = QtWidgets.QLabel("DIMENSIONS & RANGE")
        dim_lbl.setStyleSheet(
            f"font-family:'{theme.MONO_FONT}','Consolas',monospace;font-size:10px;"
            f"font-weight:700;letter-spacing:1px;color:{theme.ACCENT};background:transparent;")
        v.addWidget(dim_lbl)

        dim_grid = QtWidgets.QGridLayout()
        dim_grid.setHorizontalSpacing(6)
        dim_grid.setVerticalSpacing(4)

        dim_steppers = [
            ("entity_width", "Width (px)", 300, 800, 10),
            ("max_dist", "Max Dist (m)", 0, 1000, 25),
            ("entity_font_size", "Font (px)", 10, 24, 1),
            ("icon_size", "Icon (px)", 12, 64, 1),
        ]
        for i, (attr, label, lo, hi, step) in enumerate(dim_steppers):
            st = C.Stepper(int(getattr(self.s, attr)), lo, hi, step)
            st.valueChanged.connect(lambda v_, a=attr: self._set(a, v_))
            self._settings_steppers.append((attr, st))
            dim_grid.addWidget(C.Field(label, st), 0, i)
        v.addLayout(dim_grid)
        v.addSpacing(2)

        # 2. Tracked Entity Limits
        limits_box = QtWidgets.QFrame()
        limits_box.setObjectName("Cell")
        limits_box.setStyleSheet(
            f"QFrame#Cell {{ background-color: {theme.with_alpha(theme.PANEL_HI, 40)}; "
            f"border: 1px solid {theme.BORDER}; border-radius: 4px; }}")
        lbl_lay = QtWidgets.QVBoxLayout(limits_box)
        lbl_lay.setContentsMargins(8, 6, 8, 8)
        lbl_lay.setSpacing(6)

        limits_hdr = QtWidgets.QLabel("TRACKED ENTITY LIMITS (MAX ROWS)")
        limits_hdr.setStyleSheet(
            f"font-family:'{theme.MONO_FONT}','Consolas',monospace;font-size:10px;"
            f"font-weight:700;letter-spacing:1px;color:{theme.MUTED};background:transparent;")
        lbl_lay.addWidget(limits_hdr)

        limits_grid = QtWidgets.QGridLayout()
        limits_grid.setHorizontalSpacing(6)
        limits_grid.setVerticalSpacing(4)

        limits_steppers = [
            ("enemy_count", "Enemies", 1, 30, 1),
            ("chest_count", "Chests", 1, 30, 1),
            ("gatherable_count", "Gatherables", 1, 30, 1),
            ("companion_count", "Companions", 1, 30, 1),
            ("group_count", "Players", 1, 30, 1),
            ("orb_count", "Orbs", 1, 30, 1),
        ]
        for i, (attr, label, lo, hi, step) in enumerate(limits_steppers):
            st = C.Stepper(int(getattr(self.s, attr)), lo, hi, step)
            st.valueChanged.connect(lambda v_, a=attr: self._set(a, v_))
            self._settings_steppers.append((attr, st))
            limits_grid.addWidget(C.Field(label, st), i // 3, i % 3)
        lbl_lay.addLayout(limits_grid)
        v.addWidget(limits_box)
        v.addSpacing(2)

    # --- loot filter (rarity floor for Entity HUD) ------------------------
    def _build_loot_filters(self, v) -> None:
        loot_lbl = QtWidgets.QLabel("LOOT DROP FLOOR")
        loot_lbl.setStyleSheet(
            f"font-family:'{theme.MONO_FONT}','Consolas',monospace;font-size:10px;"
            f"font-weight:700;letter-spacing:1px;color:{theme.ACCENT};background:transparent;")
        v.addWidget(loot_lbl)

        curr_eloot = getattr(self.s, "loot_filter", "Off")
        if curr_eloot not in _LOOT_OPTS:
            curr_eloot = "Off"
        eloot = C.SegmentedControl(_LOOT_OPTS, current=curr_eloot, colors=_LOOT_COLORS)
        eloot.currentChanged.connect(lambda mode: self._set("loot_filter", mode))
        v.addWidget(eloot)
        if not hasattr(self, "_loot_segs") or self._loot_segs is None:
            self._loot_segs = []
        if eloot not in self._loot_segs:
            self._loot_segs.append(eloot)

    # --- Dungeon HUD Card -------------------------------------------------
    def _build_dungeon_hud_card(self) -> QtWidgets.QFrame:
        card, lay = self._overlay_card_frame("Dungeon HUD", tag="DUNGEON RADAR", sprite_marker="swords")
        self._build_dungeon_overlay(lay)
        return card

    # --- dungeon overlay (borderless + boss auto-track + loot) ------------
    def _build_dungeon_overlay(self, v) -> None:
        items = [
            ("dungeon_bare", "Borderless", self.s.dungeon_bare, self._set_dungeon_bare),
            ("dungeon_transparent", "Transparent", getattr(self.s, "dungeon_transparent", False), self._set_dungeon_transparent),
            ("dungeon_hide_collected", "Hide Collected", getattr(self.s, "dungeon_hide_collected", True),
             lambda on: self._set("dungeon_hide_collected", on)),
            ("rift_clone_track", "Rift Clone Auto-Track", self.s.rift_clone_track,
             lambda on: self._set("rift_clone_track", on)),
        ]
        grid_widget = C.SegmentedGrid(items, cols=4)
        v.addWidget(grid_widget)
        v.addSpacing(6)
        self._dungeon_toggles: list[tuple[str, QtWidgets.QPushButton]] = [
            (k, grid_widget.btn(k)) for k, _, _, _ in items
        ]

        loot_lbl = QtWidgets.QLabel("DUNGEON LOOT FLOOR")
        loot_lbl.setStyleSheet(
            f"font-family:'{theme.MONO_FONT}','Consolas',monospace;font-size:10px;"
            f"font-weight:700;letter-spacing:1px;color:{theme.ACCENT};background:transparent;")
        v.addWidget(loot_lbl)

        curr_dloot = getattr(self.s, "dungeon_loot_filter", "Off")
        if curr_dloot not in _LOOT_OPTS:
            curr_dloot = "Off"
        dloot = C.SegmentedControl(_LOOT_OPTS, current=curr_dloot, colors=_LOOT_COLORS)
        dloot.currentChanged.connect(
            lambda mode: self._set("dungeon_loot_filter", mode))
        v.addWidget(dloot)
        if not hasattr(self, "_loot_segs") or self._loot_segs is None:
            self._loot_segs = []
        if dloot not in self._loot_segs:
            self._loot_segs.append(dloot)

    def _set_entity_bare(self, on: bool) -> None:
        if hasattr(self, "_set_bare"):
            self._set_bare("entity", on)     # live-applies to the open overlay
        else:
            self._set("entity_bare", on)
            ov = getattr(self, "overlays", {}).get("entity")
            if ov is not None and hasattr(ov, "set_bare"):
                ov.set_bare(on)

    def _set_entity_transparent(self, on: bool) -> None:
        self._set("entity_transparent", on)
        ov = getattr(self, "overlays", {}).get("entity")
        if ov is not None and hasattr(ov, "set_transparent"):
            ov.set_transparent(on)

    def _set_show_compass(self, on: bool) -> None:
        self._set("show_compass", on)
        ov = getattr(self, "overlays", {}).get("entity")
        if ov is not None and hasattr(ov, "canvas"):
            ov.canvas.update()

    def _set_dungeon_bare(self, on: bool) -> None:
        if hasattr(self, "_set_bare"):
            self._set_bare("dungeon", on)   # live-applies to the open overlay
        else:
            self._set("dungeon_bare", on)
            ov = getattr(self, "overlays", {}).get("dungeon")
            if ov is not None and hasattr(ov, "set_bare"):
                ov.set_bare(on)

    def _set_dungeon_transparent(self, on: bool) -> None:
        self._set("dungeon_transparent", on)
        ov = getattr(self, "overlays", {}).get("dungeon")
        if ov is not None and hasattr(ov, "set_transparent"):
            ov.set_transparent(on)

    def _set_minimap_bare(self, on: bool) -> None:
        if hasattr(self, "_set_bare"):
            self._set_bare("map", on)       # live-applies to the open overlay
        else:
            self._set("minimap_bare", on)
            ov = getattr(self, "overlays", {}).get("map")
            if ov is not None and hasattr(ov, "set_bare"):
                ov.set_bare(on)

    def _set_minimap_transparent(self, on: bool) -> None:
        self._set("minimap_transparent", on)
        ov = getattr(self, "overlays", {}).get("map")
        if ov is not None and hasattr(ov, "set_transparent"):
            ov.set_transparent(on)

    def _set_minimap_shape(self, shape: str) -> None:
        self._set("minimap_shape", shape)
        self._touch_minimap()

    def _set_minimap_rotate(self, on: bool) -> None:
        self._set("minimap_rotate", on)
        self._touch_minimap()

    def _set_minimap_texture(self, on: bool) -> None:
        self._set("minimap_texture", on)
        self._touch_minimap()

    def _set_minimap_zoom(self, v: float | int) -> None:
        self._set("minimap_zoom", float(v))
        self._touch_minimap()

    def _set_minimap_size(self, v: int) -> None:
        self._set("minimap_size", v)
        ov = getattr(self, "overlays", {}).get("map")
        if ov is not None:
            ov.resize(v, v + 40)

    def _touch_minimap(self) -> None:
        ov = getattr(self, "overlays", {}).get("map")
        if ov is not None and hasattr(ov, "canvas"):
            ov.canvas.update()

    def _set_minimap_layer(self, attr: str, on: bool) -> None:
        """Toggle a minimap POI layer + re-scan POIs immediately."""
        self._set(attr, on)
        ov = getattr(self, "overlays", {}).get("map")
        if ov is not None and hasattr(ov, "canvas"):
            ov.canvas.refresh()

    def _set_minimap_hide_collected(self, on: bool) -> None:
        """Toggle hide-collected: update setting, overlay canvas, and its eye button."""
        self._set("minimap_hide_collected", on)
        ov = getattr(self, "overlays", {}).get("map")
        if ov is not None:
            if hasattr(ov, "set_hide_collected"):
                ov.set_hide_collected(on)
            elif hasattr(ov, "canvas"):
                ov.canvas.refresh()
