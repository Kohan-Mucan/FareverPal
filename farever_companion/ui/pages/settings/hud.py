"""DPS Settings module - split from ui/pages/settings.py.
Imports the single shared namespace (constants, theme, Qt aliases)
from ._shared so moved method bodies keep their original globals.
"""

from ._shared import *  # noqa: F401,F403


def _refresh_flow_min(card: QtWidgets.QWidget) -> None:
    """Post-polish floor: a wrapping flow row can lay a card out before its
    sizeHint is computed and collapse it to height 1 (Qt never re-asks a
    widget without a height-for-width policy). Once shown, pin the card's
    minimum height to its real minimum so the row can never crush it."""
    from shiboken6 import isValid
    if not isValid(card):
        return
    mh = card.minimumSizeHint().height()
    if mh > 1:
        card.setMinimumHeight(mh)


class SettingsHudMixin:
    """HUD's tab: Entity HUD, Dungeon HUD and Minimap Radar cards plus their
per-surface setter helpers (the Top DPS and Speedrun cards are built
onto the DPS tab's bottom row)."""

    def _tab_overlays(self):
        w, lv = self._tab_page()
        lv.setSpacing(12)

        self._loot_segs = []
        if not hasattr(self, "_settings_steppers") or self._settings_steppers is None:
            self._settings_steppers = []

        cols = QtWidgets.QHBoxLayout()
        cols.setSpacing(16)
        cols.setAlignment(QtCore.Qt.AlignTop)

        # Left Column: Entity HUD (Top DPS moved to the DPS tab)
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
        # The card must advertise height-for-width, or a wrapping flow
        # row that lays it out before its sizeHint is polished assigns
        # height 1 and the card never re-invalidates (invisible cards).
        pol = card.sizePolicy()
        pol.setHeightForWidth(True)
        card.setSizePolicy(pol)
        QtCore.QTimer.singleShot(0, lambda c=card: _refresh_flow_min(c))

        hdr_row = QtWidgets.QHBoxLayout()
        hdr_row.setSpacing(8)
        if sprite_marker:
            ico = QtWidgets.QLabel()
            ico.setFixedSize(20, 20)
            pm = icons.marker(sprite_marker, 20, accent=theme.ACCENT)
            if not pm or pm.isNull():
                pm = icons.ui_icon(sprite_marker, theme.ACCENT, 20)
            if pm and not pm.isNull():
                # ui_icon can hand back a non-null pixmap with zero visible
                # pixels (atlas entry present but blank) — that reads as a
                # "squashed" header. Only accept it if it actually drew.
                img = pm.toImage()
                if not any(img.pixelColor(x, y).alpha()
                           for y in range(img.height())
                           for x in range(img.width())):
                    pm = None
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
            ("minimap_compass_labels", "N/E/S/W Labels",
             getattr(self.s, "minimap_compass_labels", True),
             lambda on: (self._set("minimap_compass_labels", on), self._touch_minimap())),
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
            "• Hold right-click & drag: Pan around the minimap radar")
        note.setStyleSheet(f"color:{theme.TEXT};font-size:13px;line-height:1.5;background:transparent;")
        note.setWordWrap(True)
        tip_lay.addWidget(note)
        v.addWidget(tip_box)

    # --- Top DPS overlay card (bottom-left slot of the HUD's tab) ---------
    def _build_top_dps_card(self) -> QtWidgets.QFrame:
        """Top DPS overlay chrome + scope toggles. Borderless / Transparent /
        Solo-Only reuse the same dps_* settings that the DPS tab's Combat DPS
        Meter card and the overlay's own buttons read and write, so flipping
        any surface live-applies to the open overlay."""
        card, lay = self._overlay_card_frame(
            "Top DPS", tag="COMBAT METER", sprite_marker="activity")
        items = [
            ("dps_bare", "Borderless",
             bool(getattr(self.s, "dps_bare", False)), self._set_dps_bare),
            ("dps_transparent", "Transparent",
             bool(getattr(self.s, "dps_transparent", False)), self._set_dps_transparent),
            ("dps_solo_only", "Solo Only",
             bool(getattr(self.s, "dps_solo_only", True)), self._set_dps_solo_only),
            ("dps_pin_me", "Pin Me Top",
             bool(getattr(self.s, "dps_pin_me", True)), self._set_dps_pin_me),
            ("dps_instances_only", "Instances Only",
             bool(getattr(self.s, "dps_instances_only", False)), self._set_dps_instances_only),
            ("dps_auto_reset_zone", "Auto-Reset Zone",
             bool(getattr(self.s, "dps_auto_reset_zone", True)), self._set_dps_auto_reset_zone),
        ]
        grid_widget = C.SegmentedGrid(items, cols=4)
        # This card is the shortest in its StretchFlow row (the Test Dummy card
        # beside it is ~2x its height), so the row hands it its neighbour's
        # height and every pixel of that slack used to land INSIDE the card:
        # the header band grew from 18px to ~144px (the "TOP DPS" title left
        # floating in the middle of it, which is what inspecting that QLabel
        # shows as a stretched block), the chip cell to 128px for a 71px stack
        # of chips, and the Solo Only chip sat 4px off its own row. Give the
        # leftover to a trailing stretch instead — the same treatment the Run
        # Timer card (same row) gets, so the chips stay stacked under the
        # divider and the air collects below them.
        lay.addWidget(grid_widget)
        # Boss Clear Range: how close you have to be to a boss before the trash
        # run you are on closes and archives. A stepper, not a toggle, because
        # the answer is a distance and "close enough" is a judgement the player
        # makes per encounter — the game's own BossArea trigger handles most
        # rooms, and this is the fallback for one it has never heard of.
        # 0 = never close on proximity (the trigger alone decides).
        #
        # It sits here beside Auto-Reset Zone because that is its sibling: both
        # are fight-BOUNDARY rules, not meter chrome, and both answer "when
        # does one fight end and the next begin".
        bcr = C.Stepper(int(getattr(self.s, "dps_boss_clear_range", 30)), 0, 200, 5)
        bcr.valueChanged.connect(
            lambda v, a="dps_boss_clear_range": self._set(a, int(v)))
        self._boss_clear_range = bcr
        bcr_row = QtWidgets.QHBoxLayout()
        bcr_row.setContentsMargins(0, 6, 0, 0)
        bcr_row.addWidget(C.Field("Boss Clear Range (m)", bcr), 1)
        lay.addLayout(bcr_row)
        # The one sentence this card would otherwise need a hover bubble for
        # (`test_dps_settings_page_carries_no_hover_tooltips`).
        bcr_note = QtWidgets.QLabel(
            "Boss Clear Range closes and archives the trash run you are on "
            "once you are this close to a boss, so the meter starts the boss "
            "fight clean. Most rooms are caught by the game's own arena "
            "trigger first; this is the fallback. 0 = proximity never closes "
            "a run.")
        bcr_note.setWordWrap(True)
        bcr_note.setStyleSheet(
            f"font-size: 10px; font-weight: 600; color: {theme.DIM};")
        lay.addWidget(bcr_note)
        lay.addStretch(1)
        if not hasattr(self, "_top_dps_toggles") or self._top_dps_toggles is None:
            self._top_dps_toggles = []
        self._top_dps_toggles = [
            (k, grid_widget.btn(k)) for k, _, _, _ in items
        ]

        # Solo Only gets a taller chip: it is the one toggle whose behaviour
        # depends on context, so it stands out from the other five.
        solo = grid_widget.btn("dps_solo_only")
        if solo is not None:
            solo.setFixedHeight(40)
        return card

    def _build_dummy_card(self) -> QtWidgets.QFrame:
        """Test Dummy HUD card: Auto On, Dummy Range, Test Length, chrome.

        Its own card because the dummy feature has its own settings, and two of
        them are NOT Top DPS settings: the range decides when the dummy HUD
        surfaces at a dummy, when a dummy test engages, and when an abandoned
        one ends, and the target length is what ENDS a measured test. They lived
        on the Top DPS card, which is a meter's card, and read like one of the
        meter's options.

        One placement rule belongs in words rather than in a number: the window
        is open-world only (the `dummy` row of `overlay_rules.HUD_PLACES`
        refuses an instance whatever the toggles say), so the note says so.

        Borderless / Transparent write the SAME `dummy_*` settings the Overlays
        page card writes, so flipping either surface live-applies to the open
        window — the pattern the Top DPS card already uses.
        """
        card, lay = self._overlay_card_frame(
            "Test Dummy", tag="DUMMY TEST", sprite_marker="target-dummy")
        items = [
            ("dps_dummy_auto", "Auto On",
             bool(getattr(self.s, "dps_dummy_auto", True)), self._set_dummy_auto),
            ("dummy_bare", "Borderless",
             bool(getattr(self.s, "dummy_bare", False)), self._set_dummy_bare),
            ("dummy_transparent", "Transparent",
             bool(getattr(self.s, "dummy_transparent", False)),
             self._set_dummy_transparent),
        ]
        grid_widget = C.SegmentedGrid(items, cols=3)
        lay.addWidget(grid_widget)
        if not hasattr(self, "_dummy_toggles") or self._dummy_toggles is None:
            self._dummy_toggles = []
        self._dummy_toggles = [
            (k, grid_widget.btn(k)) for k, _, _, _ in items
        ]
        # How close a training dummy must be for the HUD to surface (and for a
        # test to engage) — see ui/overlay_rules.py. 0 = no distance cap.
        rng = C.Stepper(int(getattr(self.s, "dps_dummy_range", 5)), 0, 100, 5)
        rng.valueChanged.connect(lambda v: self._set_dummy_range(int(v)))
        self._dummy_range = rng
        # How long a test runs before it ends itself. A dummy fight is the one
        # encounter nothing in the scene concludes, and two runs of different
        # lengths are not comparable — so this is what makes "the same
        # rotation, twice" mean anything. 0 = free-running (Stop Test only).
        tgt = C.Stepper(int(getattr(self.s, "dps_dummy_target", 60)), 0, 300, 5)
        tgt.valueChanged.connect(
            lambda v, a="dps_dummy_target": self._set(a, int(v)))
        self._dummy_target = tgt
        # How much incoming damage (as a share of the test's own) still counts
        # as a clean measurement. A dummy does not fight back, so anything that
        # lands is the rotation or the world walking in — and either way the
        # DPS on screen is not the DPS of the build.
        lim = C.Stepper(int(getattr(self.s, "dps_dummy_taken_pct", 5)), 0, 100, 5)
        lim.valueChanged.connect(
            lambda v, a="dps_dummy_taken_pct": self._set(a, int(v)))
        self._dummy_taken_limit = lim
        # How long a running test may go with no hit before it ends itself and
        # re-arms. The game keeps the player "in combat" at any range inside a
        # training yard, so without this a test the player has finished sat
        # there until they walked ~30 m out. 0 = never auto-end (Stop Test /
        # target length only).
        rearm = C.Stepper(int(getattr(self.s, "dps_dummy_rearm", 5)), 0, 60, 1)
        rearm.valueChanged.connect(
            lambda v, a="dps_dummy_rearm": self._set(a, int(v)))
        self._dummy_rearm = rearm
        span = QtWidgets.QHBoxLayout()
        span.setSpacing(8)
        span.addWidget(C.Field("Dummy Range (m)", rng), 1)
        span.addWidget(C.Field("Test Length (s)", tgt), 1)
        lay.addLayout(span)
        warn_row = QtWidgets.QHBoxLayout()
        warn_row.setSpacing(8)
        warn_row.addWidget(C.Field("Flag taken over (%)", lim), 1)
        warn_row.addWidget(C.Field("Auto Re-arm (s)", rearm), 1)
        # Free Test: record nothing. A dummy test is the one measurement the
        # player fully controls, and most tests are practice runs whose numbers
        # have no business becoming an all-time best - it would then be compared
        # against itself forever. Off by default, next to the other
        # measurement-hygiene controls because that is what it is.
        free = C.LabeledToggle(
            "Free Test", bool(getattr(self.s, "dps_dummy_free", False)))
        free.toggled.connect(
            lambda on, a="dps_dummy_free": self._set(a, bool(on)))
        self._dummy_free = free
        warn_row.addWidget(free, 0)
        lay.addLayout(warn_row)
        # The numbers above need one sentence between them, and this page
        # carries it as a line on the card, not as a hover bubble
        # (test_dps_settings_page_carries_no_hover_tooltips). The second line
        # is where the sheet lives: a run is appended to a CSV in moddata/dps
        # (per day, per character) so twenty runs can be compared at once.
        note = QtWidgets.QLabel(
            "Dummy Range is how close a training dummy has to be. The HUD "
            "surfaces at a dummy inside it — and only at a dummy: it is an "
            "open-world window, so it never appears inside a dungeon or a "
            "rift, whatever else this card says.\n"
            "The test ends itself at the target length, so every run is the "
            "same length. 0 = run free until Stop Test.\n"
            "Auto Re-arm ends a test that has stopped landing hits, so it "
            "resets without walking away from the dummy. 0 = never auto-end.\n"
            "A test that takes more than this share of its own damage back is "
            "flagged as not a clean measurement. 0 = flag any damage taken.\n"
            "Free Test records nothing: the run still shows on the HUD, but it "
            "is not archived and never becomes the best to beat.\n"
            "Each finished test is appended to a CSV in moddata/dps (per day, "
            "per character), so runs can be compared in a spreadsheet.")
        note.setWordWrap(True)
        note.setStyleSheet(
            f"font-size: 10px; font-weight: 600; color: {theme.DIM};")
        lay.addWidget(note)
        return card

    def _set_dummy_auto(self, on: bool) -> None:
        """Auto On: the dummy HUD surfaces by itself at a dummy (Rule F).

        Off makes it an ordinary overlay — the user opens it from the Overlays
        card and it stays up wherever they are, outside a dungeon/rift, which
        Rule F's instance gate closes either way. Persisted only: both rules
        read the setting on their next tick, so there is nothing to push live.
        """
        self._set("dps_dummy_auto", bool(on))

    def _set_dummy_range(self, metres: int) -> None:
        """Dummy Range: the one number the core dummy read is gated by.

        `dummy_in_range` is `dummy_within(dummy_range)`, so this is not a
        display knob — it decides the HUD's auto-show, whether a dummy in a
        yard owns the meter's board, and when an abandoned test ends. The 1 Hz
        drain usually carries it to the tracker a second late; a stepper
        dragged while the answer is on screen would show the old verdict for
        that second, so the setting is handed over and the HUD repainted as it
        moves (see `OverlayManager.apply_dummy_range`).
        """
        self._set("dps_dummy_range", int(metres))
        mgr = getattr(self, "overlay_mgr", None)
        apply_live = getattr(mgr, "apply_dummy_range", None)
        if apply_live is not None:
            apply_live(int(metres))

    def _set_dummy_bare(self, on: bool) -> None:
        if hasattr(self, "_set_bare"):
            self._set_bare("dummy", on)      # live-applies to the open HUD
        else:
            self._set("dummy_bare", on)
            ov = getattr(self, "overlays", {}).get("dummy")
            if ov is not None and hasattr(ov, "set_bare"):
                ov.set_bare(on)

    def _set_dummy_transparent(self, on: bool) -> None:
        if hasattr(self, "_set_transparent"):
            self._set_transparent("dummy", on)
        else:
            self._set("dummy_transparent", on)
            ov = getattr(self, "overlays", {}).get("dummy")
            if ov is not None and hasattr(ov, "set_transparent"):
                ov.set_transparent(on)

    # --- Run Timer card (the old sidebar Speedrun page, condensed) --------
    def _build_speedrun_card(self) -> QtWidgets.QFrame:
        """Run-timer chrome + behaviour: Borderless / Transparent reuse the
        same speedrun_* settings the Overlays card and the overlay's own
        buttons read, so flipping any surface live-applies to the open HUD."""
        card, lay = self._overlay_card_frame(
            "Run Timer", tag="DUNGEON TIMER", sprite_marker="timer")
        items = [
            ("speedrun_bare", "Borderless",
             bool(getattr(self.s, "speedrun_bare", False)), self._set_speedrun_bare),
            ("speedrun_transparent", "Transparent",
             bool(getattr(self.s, "speedrun_transparent", False)), self._set_speedrun_transparent),
        ]
        grid_widget = C.SegmentedGrid(items, cols=2)
        # This is the shortest card in its StretchFlow row (it shares that row
        # with the Top DPS card, which is ~2x its height), so the row hands it
        # its neighbour's width AND its neighbour's height. Left free, the chip
        # grid absorbed every spare pixel of both axes: measured 495x77 for a
        # 3+32+3 = 38px chip row (hint 166x38) on a 2200px window, i.e. the
        # chips ballooned to ~230px each and the row floated in ~40px of air.
        # Cap it at its own hint and give the slack to a trailing stretch, so
        # the chips hug their labels and the card's leftover space collects
        # below them instead.
        grid_widget.setSizePolicy(QtWidgets.QSizePolicy.Maximum,
                                  QtWidgets.QSizePolicy.Fixed)
        lay.addWidget(grid_widget, 0, QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop)
        lay.addStretch(1)
        if not hasattr(self, "_speedrun_toggles") or self._speedrun_toggles is None:
            self._speedrun_toggles = []
        self._speedrun_toggles = [
            (k, grid_widget.btn(k)) for k, _, _, _ in items
        ]
        return card

    def _set_speedrun_bare(self, on: bool) -> None:
        if hasattr(self, "_set_bare"):
            self._set_bare("speedrun", on)   # live-applies to the open overlay
        else:
            self._set("speedrun_bare", on)
            ov = getattr(self, "overlays", {}).get("speedrun")
            if ov is not None and hasattr(ov, "set_bare"):
                ov.set_bare(on)

    def _set_speedrun_transparent(self, on: bool) -> None:
        if hasattr(self, "_set_transparent"):
            self._set_transparent("speedrun", on)
        else:
            self._set("speedrun_transparent", on)
            ov = getattr(self, "overlays", {}).get("speedrun")
            if ov is not None and hasattr(ov, "set_transparent"):
                ov.set_transparent(on)

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
            # Per-section visibility (2026-09-19) — mirrors the HUD's own
            # titlebar buttons (same Settings attrs, both surfaces live-sync).
            # Every other section is already a World Layers matrix row.
            ("entity_show_rift", "Rift Timer",
             getattr(self.s, "entity_show_rift", True),
             lambda on: self._set("entity_show_rift", on)),
            ("show_loot", "Loot", bool(getattr(self.s, "show_loot", True)),
             lambda on: self._set("show_loot", on)),
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

    # --- dungeon overlay (borderless + per-section visibility + loot) ------
    def _build_dungeon_overlay(self, v) -> None:
        items = [
            ("dungeon_bare", "Borderless", self.s.dungeon_bare, self._set_dungeon_bare),
            ("dungeon_transparent", "Transparent", getattr(self.s, "dungeon_transparent", False), self._set_dungeon_transparent),
            ("dungeon_hide_collected", "Hide Collected", getattr(self.s, "dungeon_hide_collected", True),
             lambda on: self._set("dungeon_hide_collected", on)),
            ("rift_clone_track", "Rift Clone Auto-Track", self.s.rift_clone_track,
             lambda on: self._set("rift_clone_track", on)),
            # Per-section visibility (2026-09-18) — mirrors the overlay's
            # titlebar buttons: same Settings attrs, both surfaces live-sync.
            ("dungeon_show_food", "Food", getattr(self.s, "dungeon_show_food", True),
             lambda on: self._set("dungeon_show_food", on)),
            ("dungeon_show_rift", "Rift Timer", getattr(self.s, "dungeon_show_rift", True),
             lambda on: self._set("dungeon_show_rift", on)),
            ("dungeon_show_boss", "Bosses", getattr(self.s, "dungeon_show_boss", True),
             lambda on: self._set("dungeon_show_boss", on)),
            ("dungeon_show_enemies", "Enemies", getattr(self.s, "dungeon_show_enemies", True),
             lambda on: self._set("dungeon_show_enemies", on)),
            ("dungeon_show_players", "Players", getattr(self.s, "dungeon_show_players", False),
             lambda on: self._set("dungeon_show_players", on)),
            ("dungeon_show_loots", "Loot", getattr(self.s, "dungeon_show_loots", True),
             lambda on: self._set("dungeon_show_loots", on)),
            ("dungeon_show_orbs", "Secret Orbs", getattr(self.s, "dungeon_show_orbs", True),
             lambda on: self._set("dungeon_show_orbs", on)),
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
        # Live-apply the needle exactly like the Active Overlays & Radars
        # toggle: show_compass is the single setting behind ALL compass
        # surfaces (the Overlays-page card, this chip, the minimap rose, and
        # the Entity HUD rows that start the needle) — flipping it anywhere
        # must show/hide the 3D pointer right away.
        mgr = getattr(self, "overlay_mgr", None)
        tracker = getattr(mgr, "tracker", None) if mgr is not None else None
        if tracker is None:
            return
        try:
            if on:
                if (tracker.model is not None
                        and tracker.s.track_kind and tracker.s.track_id):
                    tracker._start()
            else:
                tracker.clear()
        except Exception:
            pass

    def _set_dps_bare(self, on: bool) -> None:
        if hasattr(self, "_set_bare"):
            self._set_bare("dps", on)     # live-applies to the open overlay
        else:
            self._set("dps_bare", on)
            ov = getattr(self, "overlays", {}).get("dps")
            if ov is not None and hasattr(ov, "set_bare"):
                ov.set_bare(on)

    def _set_dps_transparent(self, on: bool) -> None:
        if hasattr(self, "_set_transparent"):
            self._set_transparent("dps", on)  # live-applies to the open overlay
        else:
            self._set("dps_transparent", on)
            ov = getattr(self, "overlays", {}).get("dps")
            if ov is not None and hasattr(ov, "set_transparent"):
                ov.set_transparent(on)

    def _set_dps_pin_me(self, on: bool) -> None:
        """Pin the local player's row to the top of the DPS meter list."""
        self._set("dps_pin_me", bool(on))

    def _set_dps_instances_only(self, on: bool) -> None:
        """Show the Top DPS overlay only inside dungeons/rifts; the overlay
        manager's visibility loop applies it live (open world hides it)."""
        self._set("dps_instances_only", bool(on))

    def _set_dps_auto_reset_zone(self, on: bool) -> None:
        """Persist the zone-entry auto-reset toggle and push it live to every
        tracker, so the meter stops wiping on dungeon/rift entry when off."""
        self._set("dps_auto_reset_zone", bool(on))
        model = getattr(self, "model", None)
        trackers: list = []
        if model is not None and getattr(model, "dps", None) is not None:
            trackers.append(model.dps)
        ov = getattr(self, "overlays", {}).get("dps") if isinstance(
            getattr(self, "overlays", None), dict) else None
        if ov is not None and getattr(ov, "tracker", None) is not None:
            trackers.append(ov.tracker)
        for t in trackers:
            try:
                if hasattr(t, "auto_reset_zone"):
                    t.auto_reset_zone = bool(on)
            except Exception:
                pass

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

