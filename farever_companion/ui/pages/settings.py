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

from PySide6 import QtCore, QtGui, QtWidgets

from .. import components as C
from .. import theme
from ..layout import make_scroll
from ...data import icons, names, units
from ...geo.gatherables import get_display_name
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
    _TAB_ORDER = ["Layers", "HUD's", "Rift", "Pages"]

    # Pages that can never be switched off here — Settings is the page you're
    # on, Overlays is the main landing slot (disabling it would strand nav).
    # Log and Dev Icons are controlled via the Sidebar Elements section below.
    _PAGES_ALWAYS_ON = ("settings", "overlays", "log", "devicons")

    def _page_settings(self):
        page, v = self._page_container()

        self._settings_tabs = C.UnderlineTabs(self._TAB_ORDER)
        v.addWidget(self._settings_tabs)

        self._settings_stack = QtWidgets.QStackedWidget()
        v.addWidget(self._settings_stack, 1)
        # Each tab is its own scroll area (content-sized): short tabs like Pages
        # sit at their natural height with no scrollbar, tall tabs like the
        # layer matrix scroll inside their own view instead of inflating every
        # other tab (which used to force a blank footer below short tabs).
        for tab in (self._tab_layers, self._tab_overlays,
                    self._tab_rift, self._tab_pages):
            self._settings_stack.addWidget(self._tab_scroll(tab()))
        self._settings_tabs.currentChanged.connect(self._on_settings_tab)
        return page

    def _tab_scroll(self, w: QtWidgets.QWidget) -> QtWidgets.QScrollArea:
        return make_scroll(w)

    def _on_settings_tab(self, text: str) -> None:
        # Match case-insensitively; "hud", "huds", "hud's", "map", "overlay", "overlays", "entity", "dungeon", "minimap" route to "HUD's"
        if text.lower() in ("hud", "huds", "hud's", "map", "overlay", "overlays", "entity", "dungeon", "minimap"):
            target = "HUD's"
        elif text.lower() in ("layer", "layers", "matrix"):
            target = "Layers"
        elif text.lower() in ("rift",):
            target = "Rift"
        elif text.lower() in ("page", "pages"):
            target = "Pages"
        else:
            target = text
        key = next((t for t in self._TAB_ORDER if t.lower() == target.lower()), None)
        if key is not None:
            self._settings_stack.setCurrentIndex(self._TAB_ORDER.index(key))
            if hasattr(self, "_settings_tabs") and self._settings_tabs.currentText() != key:
                self._settings_tabs.setCurrentText(key)
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
            "combat": ("swords", "Combat / DPS", "Real-time DPS meter, skill damage breakdowns and combat log events"),
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
        self._master_link = None

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

                top_row.addLayout(ctrl_lay)
                card_lay.addLayout(top_row)

                self._matrix_rows[key] = {
                    "key": key,
                    "icon": ic,
                    "hud_attr": hud_a,
                    "map_attr": map_a,
                    "hud": hud_t,
                    "map": map_t,
                    "link": None,
                    "linkable": False,
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
        if getattr(self, "_master_link", None) is not None:
            self._master_link.blockSignals(True)
            self._master_link.setChecked(master)
            self._master_link.blockSignals(False)
            if hasattr(self, "_restyle_master_btn"):
                self._restyle_master_btn(master)
        for key, r in self._matrix_rows.items():
            b = r.get("link")
            if b is None:
                continue
            b.blockSignals(True)
            b.setChecked(master or key in links)
            b.setEnabled(not master)
            b.blockSignals(False)
            if hasattr(b, "_restyle"):
                b._restyle()
        self._update_link_status()

    def _update_link_status(self) -> None:
        if getattr(self, "_master_link", None) is not None:
            n = len(getattr(self.s, "layer_links", []))
            if self.s.layer_link_master:
                self._master_link.setToolTip(f"MASTER link ON ({n} rows synchronized) — click to disable")
            elif n:
                self._master_link.setToolTip(f"{n} row(s) linked — click to enable MASTER link")
            else:
                self._master_link.setToolTip("Click to enable MASTER link (syncs all HUD and Map layers)")

    def _refresh_settings_page(self) -> None:
        """Re-sync every matrix + section toggle and link state from Settings."""
        for attr, t in getattr(self, "_settings_toggles", []):
            t.set_checked_silent(bool(getattr(self.s, attr)))
        for attr, t in getattr(self, "_hud_toggles", []):
            t.set_checked_silent(bool(getattr(self.s, attr)))
        for attr, t in getattr(self, "_minimap_toggles", []):
            t.set_checked_silent(bool(getattr(self.s, attr)))
        for attr, t in getattr(self, "_dungeon_toggles", []):
            t.set_checked_silent(bool(getattr(self.s, attr)))
        for attr, t in getattr(self, "_sidebar_element_toggles", []):
            def_val = False if attr in ("show_log", "show_devicons") else True
            t.set_checked_silent(bool(getattr(self.s, attr, def_val)))
        for key, t in getattr(self, "_page_element_toggles", []):
            disabled = set(getattr(self.s, "disabled_pages", []) or [])
            t.set_checked_silent(key not in disabled)
        if getattr(self, "_master_link", None) is not None:
            self._sync_link_buttons()
        for seg in getattr(self, "_gather_segs", []):
            enabled = set(getattr(self.s, "show_gatherable_types", []))
            for disp in seg._btns:
                key = self._gather_disp_to_key.get(disp)
                if key is not None:
                    seg.set_checked(disp, key in enabled, silent=True)

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
            ("show_loot", "Loot", self.s.show_loot,
             lambda on: self._set("show_loot", on)),
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
            ("dungeon_show_loots", "Loot", getattr(self.s, "dungeon_show_loots", True),
             lambda on: self._set("dungeon_show_loots", on)),
            ("dungeon_hide_collected", "Hide Collected", getattr(self.s, "dungeon_hide_collected", True),
             lambda on: self._set("dungeon_hide_collected", on)),
            ("rift_clone_track", "Rift Clone Auto-Track", self.s.rift_clone_track,
             lambda on: self._set("rift_clone_track", on)),
            ("dungeon_show_orbs", "Secret Orbs", getattr(self.s, "dungeon_show_orbs", True),
             lambda on: self._set("dungeon_show_orbs", on)),
            ("dungeon_show_players", "Party Players", getattr(self.s, "dungeon_show_players", True),
             lambda on: self._set("dungeon_show_players", on)),
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
