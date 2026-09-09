"""DPS Settings module - split from ui/pages/settings.py.
Imports the single shared namespace (constants, theme, Qt aliases)
from ._shared so moved method bodies keep their original globals.
"""

from ._shared import *  # noqa: F401,F403


class SettingsRiftMixin:
    """Rift tab: schedule card, countdown timer and reminder chips."""

    def _tab_rift(self):
        w, lv = self._tab_page()
        self._build_rift(lv)
        lv.addStretch(1)
        return w

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
        legend.setStyleSheet(f"color:{theme.DIM};font-size:14px;line-height:1.4;background:transparent;")
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

        # Sound on/off sits inline at the right end of the subtitle row:
        # bare C.ToggleSwitch, no full-width bar. Off clears the lead-time
        # chimes (rift_ding=[]) while stashing the selection, so flipping it
        # back on restores exactly what was picked.
        ahdr = C.SectionHeader("Audio Alerts", tag="CHIME REMINDERS")
        alay.addWidget(ahdr)

        sub_row = QtWidgets.QHBoxLayout()
        sub_row.setContentsMargins(0, 0, 0, 0)
        sub_row.setSpacing(8)
        asub = QtWidgets.QLabel("Play a reminder chime before the rift opens.")
        asub.setObjectName("Muted")
        asub.setWordWrap(True)
        sub_row.addWidget(asub, 1)
        self._rift_sound_toggle = C.ToggleSwitch(bool(self._rift_ding_selected()))
        self._rift_sound_toggle.setToolTip("Rift alert sound on/off")
        self._rift_sound_toggle.toggled.connect(self._on_rift_sound_toggle)
        sub_row.addWidget(self._rift_sound_toggle, 0, QtCore.Qt.AlignVCenter)
        alay.addLayout(sub_row)

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
            return theme.checkable_btn_qss(padding="6px 8px",
                                          checked_hover_alpha=55,
                                          checked_border=True)

        def _on_snd(b: QtWidgets.QPushButton) -> None:
            for k, bb in self._rift_sound_btns.items():
                if bb is b:
                    self._set("rift_sound", k)
                    # An explicit click: play it even if the same tone is still
                    # sounding (re-hearing the one you just picked is the point
                    # of the picker). Alerts keep the burst guard.
                    play_sound(k, force=True)
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
        self._rift_hero_icon.setFixedSize(32, 32)
        self._rift_hero_icon.setPixmap(icons.marker("rift", 32, accent=theme.ACCENT))
        top_bar.addWidget(self._rift_hero_icon, 0, QtCore.Qt.AlignVCenter)

        self._rift_next_lbl = QtWidgets.QLabel("Next Rift: --")
        self._rift_next_lbl.setStyleSheet(
            f"font-family:'{theme.MONO_FONT}','Consolas',monospace;font-size:18px;"
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
        th_time.setFixedWidth(90)
        th_zone = QtWidgets.QLabel("ZONE / TARGET LOCATION")
        th_inc = QtWidgets.QLabel("STARTS IN")
        th_inc.setFixedWidth(115)
        th_inc.setAlignment(QtCore.Qt.AlignRight)
        for lbl in (th_time, th_zone, th_inc):
            lbl.setStyleSheet(
                f"font-family:'{theme.MONO_FONT}','Consolas',monospace;font-size:11px;"
                f"font-weight:700;letter-spacing:1px;color:{theme.DIM};background:transparent;")
        th.addWidget(th_time)
        th.addWidget(th_zone, 1)
        th.addWidget(th_inc)
        list_lay.addLayout(th)

        # 3. 10 Schedule Rows
        self._rift_rows: list[tuple[QtWidgets.QLabel, QtWidgets.QLabel, QtWidgets.QLabel]] = []
        for i in range(10):
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
            c.setFixedWidth(90)
            inc.setFixedWidth(115)
            inc.setAlignment(QtCore.Qt.AlignRight)

            c.setStyleSheet(
                f"font-family:'{theme.MONO_FONT}','Consolas',monospace;font-size:14px;"
                f"color:{theme.MUTED};background:transparent;border:none;")
            z.setStyleSheet(
                f"font-size:14px;font-weight:600;color:{theme.GOLD};background:transparent;border:none;")
            inc.setStyleSheet(
                f"font-family:'{theme.MONO_FONT}','Consolas',monospace;font-size:14px;"
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
        from ....core.rift_tracker import rift_summary, upcoming_rifts
        r = rift_summary()
        GREEN = "#66BB6A"
        secs = r.get("secs_until", 10**9)
        time_green = secs < 900 and r["state"] not in ("ACTIVE", "CLOSING")
        tcolor = GREEN if time_green else r["color"]

        # Update Target Zone and Icon Accent
        if hasattr(self, "_rift_hero_icon"):
            self._rift_hero_icon.setPixmap(icons.marker("rift", 32, accent=tcolor))

        # Update Next Rift status text (Blue label, Gold location, state-colored status)
        if hasattr(self, "_rift_next_lbl"):
            html = f'<font color="{theme.ACCENT}">{r["label"]}:  </font>'
            html += f'<font color="{theme.GOLD}">{r["zone"]}</font>'
            html += '<font color="#87929a">   —   </font>'
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
        """Selected lead times (minutes); shares the chime's own parser so the
        chips can never disagree with what actually rings."""
        from ....core.rift_tracker import parse_rift_ding
        return set(parse_rift_ding(getattr(self.s, "rift_ding", None)))

    def _toggle_rift_ding(self, minute: int, on: bool) -> None:
        sel = self._rift_ding_selected()
        if on:
            sel.add(minute)
        else:
            sel.discard(minute)
        self._set("rift_ding", sorted(sel))
        self._style_rift_chip(self._rift_ding_chips[minute])
        self._sync_rift_sound_toggle()

    def _on_rift_sound_toggle(self, on: bool) -> None:
        """Audio Alerts sound switch: off mutes (clears chimes), on restores."""
        if not on:
            cur = sorted(self._rift_ding_selected())
            if cur:
                self._rift_ding_stash = cur
            self._set("rift_ding", [])
        else:
            restore = sorted(getattr(self, "_rift_ding_stash", None) or [])
            if not restore:
                from ....core.rift_tracker import RIFT_DING_DEFAULT
                restore = list(RIFT_DING_DEFAULT)
            self._set("rift_ding", restore)
        self._refresh_rift_ding_chips()

    def _refresh_rift_ding_chips(self) -> None:
        sel = self._rift_ding_selected()
        for m, chip in getattr(self, "_rift_ding_chips", {}).items():
            chip.blockSignals(True)
            chip.setChecked(m in sel)
            chip.blockSignals(False)
            self._style_rift_chip(chip)
        self._sync_rift_sound_toggle()

    def _sync_rift_sound_toggle(self) -> None:
        tog = getattr(self, "_rift_sound_toggle", None)
        if tog is not None:
            tog.set_checked_silent(bool(self._rift_ding_selected()))

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

