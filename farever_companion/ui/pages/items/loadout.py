"""Loadout Builder & Mix-Match Gear Optimizer tab for the Items page.

Ranks the ideal catalog and profile-owned gear setups for a Main Weapon,
Off-Hand and Arsenal (50% Arsenal stats, max 2 skills), by class AND by the
build's role. Includes weapon icon pickers, class/search filters,
real scaled item stats, Rare dungeon boss gear defaults, and offline global loadout persistence (loadout.json).

This is the assembly point plus the totals sidebar. The family is this file and
ONE shard, `loadout_weapons.py` (the weapon cards and the item picker dialog);
`loadout_collection.py` stays a peer page-half of its own. The line budget
measures one file, so the family was split once, not once per widget group.
"""
from __future__ import annotations

import time

from PySide6 import QtCore, QtWidgets

from ... import theme
from ... import components as C
from ...copy_utils import copy_text
from ....data import items as idata
from ....craft import planner as pdata

from .loadout_collection import LoadoutCollectionMixin
from .loadout_weapons import (LoadoutWeaponsMixin, _GEAR_SLOT_NAMES,
                              _ItemPickerDialog, is_2h_weapon)


# The Loadout tab runs its body type two pixels larger than the app default:
# the stat sheet, the slot rows and the collection grid are all dense 11px
# text, and at the app's 14px they read as a wall of hairlines. Widgets that
# set their own `font-size` (see the mixins) win over this; the rule exists
# for the ones that inherit the global `*` size — the mode buttons, the class
# chips, and SectionHeader's objectName-styled labels.
_PAGE_FONT_QSS = """
* { font-size: 16px; }
QLabel#Section, QLabel#Mono, QLabel#FieldLabel { font-size: 13px; }
"""


# The stat sheet's order after the build's primary stat: the optimizer's own
# weights (see data.items.stats._build_score) with armor last, so the panel
# reads as a build summary rather than a dict dump.
_TOTAL_ORDER = ("Critical", "Fervor", "Armor Penetration", "Magic Penetration",
                "Armor", "Vitality", "Health")


def _link_button(text: str) -> QtWidgets.QPushButton:
    """A borderless secondary action for the totals sidebar."""
    btn = QtWidgets.QPushButton(text)
    btn.setCursor(QtCore.Qt.PointingHandCursor)
    btn.setStyleSheet(
        f"QPushButton {{ background: transparent; border: 0; color: {theme.MUTED}; "
        f"padding: 2px 0; font-weight: 600; font-size: 13px; }}\n"
        f"QPushButton:hover {{ color: {theme.ACCENT}; }}")
    return btn


class LoadoutTotalsMixin:
    """The build's stat sheet: the sidebar card, its rows and its actions."""

    def _build_totals_sidebar(self) -> QtWidgets.QFrame:
        """The right-hand card: stat sheet, then the actions that use it."""
        side_card = QtWidgets.QFrame()
        side_card.setFixedWidth(280)
        side_card.setObjectName("Card")
        side_card.setStyleSheet(
            # `.QFrame` (exact class): a bare `QFrame` rule cascades to every
            # QLabel inside (QLabel IS-A QFrame) and boxed each stat row
            f".QFrame {{ background: {theme.PANEL}; "
            f"border: 1px solid {theme.BORDER}; border-radius: 8px; }}")
        slay = QtWidgets.QVBoxLayout(side_card)
        slay.setContentsMargins(14, 12, 14, 12)
        slay.setSpacing(10)

        slay.addWidget(C.SectionHeader("Loadout Totals",
                                       tag="L25 · INCL. 50% ARSENAL"))

        # Metadata above the sheet (which pieces are ranked), kept OUT of
        # `_totals_container`: that container is stat rows and nothing else,
        # which is the contract its own tests read.
        self._totals_header = QtWidgets.QVBoxLayout()
        self._totals_header.setSpacing(2)
        slay.addLayout(self._totals_header)

        self._totals_container = QtWidgets.QVBoxLayout()
        self._totals_container.setSpacing(2)
        self._update_stat_totals()
        slay.addLayout(self._totals_container)

        slay.addStretch(1)

        # Global Farm Action Button — the one filled action on the tab
        add_farm_btn = QtWidgets.QPushButton("➕ Add Missing Items to Farm")
        add_farm_btn.setCursor(QtCore.Qt.PointingHandCursor)
        add_farm_btn.setStyleSheet(
            f"QPushButton {{ background: {theme.GOLD}; color: #000; "
            f"border: 0; border-radius: 6px; padding: 8px; "
            f"font-weight: 700; font-size: 13px; }}\n"
            f"QPushButton:hover {{ background: #fde047; }}")
        add_farm_btn.clicked.connect(self._add_loadout_to_farm)
        slay.addWidget(add_farm_btn)

        # The two copies are borderless links on one row: they were two of
        # the five outlines, and they are the same action on two payloads.
        copy_row = QtWidgets.QHBoxLayout()
        copy_row.setSpacing(8)
        copy_btn = _link_button("📋 Copy Loadout")
        copy_btn.clicked.connect(self._copy_loadout_to_clipboard)
        aug_btn = _link_button("✨ Copy Augment Plan")
        aug_btn.clicked.connect(self._copy_augment_plan_to_clipboard)
        copy_row.addWidget(copy_btn)
        copy_row.addWidget(aug_btn)
        copy_row.addStretch(1)
        slay.addLayout(copy_row)

        return side_card

    def _loadout_primary_stat(self) -> str | None:
        """The stat this build scales: the equipped weapon's answer for the
        class being built, else the class's own primary."""
        cls = getattr(self, "_loadout_class_filter", "") or None
        return (idata.weapon_primary_stat(getattr(self, "_loadout_w1", None), cls)
                or (idata.class_primary_stat(cls) if cls else None))

    @staticmethod
    def _rank_bonus(bonus: float) -> QtWidgets.QLabel | None:
        """The gold `+N` marking how much of a stat came from upgrade rank,
        or None when the rank added nothing to it.

        A weapon in this page is ALWAYS at its ladder max (a pick maxes it),
        so every weapon stat arrived pre-inflated and the sheet could not say
        which numbers were earned by the piece and which by the ranks. The
        figure is exact rather than an estimate: it is the totals read again
        with the ranks dropped.
        """
        if abs(bonus) < 0.005:
            return None
        lbl = QtWidgets.QLabel(f"+{bonus:g}")
        # named so the sheet's gold shares are findable without matching a
        # stylesheet string (the page has other gold text)
        lbl.setObjectName("RankBonus")
        lbl.setStyleSheet(
            f'color: {theme.GOLD}; background: transparent; '
            f'font-family: "{theme.MONO_FONT}", monospace; font-size: 11px;')
        return lbl

    @staticmethod
    def _stat_row(stat: str, val: float, bonus: float = 0.0) -> QtWidgets.QWidget:
        """One stat line: name left, value right, rank share in gold before
        the value. Fixed height on purpose — as free widgets these rows soaked
        up every spare pixel in the 280px column (a 15px line rendered 29px
        tall)."""
        w = QtWidgets.QWidget()
        w.setStyleSheet("background: transparent; border: 0;")
        r = QtWidgets.QHBoxLayout(w)
        r.setContentsMargins(0, 0, 0, 0)
        r.setSpacing(6)
        lbl = QtWidgets.QLabel(stat)
        lbl.setStyleSheet(f"color: {theme.MUTED}; font-size: 13px; background: transparent;")
        r.addWidget(lbl)
        r.addStretch(1)
        gold = LoadoutTotalsMixin._rank_bonus(bonus)
        if gold is not None:
            r.addWidget(gold, 0, QtCore.Qt.AlignRight)
        num = QtWidgets.QLabel(f"+{val:g}")
        num.setStyleSheet(f'color: {theme.TEXT}; background: transparent; '
                          f'font-family: "{theme.MONO_FONT}", monospace; font-size: 13px;')
        r.addWidget(num, 0, QtCore.Qt.AlignRight)
        w.setFixedHeight(20)
        return w

    def _primary_stat_row(self, stat: str, val: float,
                          bonus: float = 0.0) -> QtWidgets.QWidget:
        """The build's scaling stat, as a hero line above the rest."""
        w = QtWidgets.QWidget()
        w.setStyleSheet("background: transparent; border: 0;")
        r = QtWidgets.QHBoxLayout(w)
        r.setContentsMargins(0, 0, 0, 0)
        r.setSpacing(6)
        lbl = QtWidgets.QLabel(stat.upper())
        lbl.setStyleSheet(
            f'color: {theme.MUTED}; font-family: "{theme.MONO_FONT}", monospace; '
            f'font-size: 11px; font-weight: 800; letter-spacing: 1px; background: transparent;')
        r.addWidget(lbl, 0, QtCore.Qt.AlignBottom)
        r.addStretch(1)
        gold = self._rank_bonus(bonus)
        if gold is not None:
            r.addWidget(gold, 0, QtCore.Qt.AlignBottom)
        num = QtWidgets.QLabel(f"+{val:g}")
        num.setStyleSheet(
            f'color: {theme.ACCENT}; font-family: "{theme.MONO_FONT}", monospace; '
            f'font-size: 23px; font-weight: 900; background: transparent;')
        r.addWidget(num, 0, QtCore.Qt.AlignRight)
        w.setFixedHeight(31)
        return w

    def _upgrade_rank_line(self) -> QtWidgets.QWidget | None:
        """The one line that says which pieces are ranked and how far: the
        stat sheet's gold `+N` shares are only legible next to the ranks that
        produced them. Hidden when nothing in the build upgrades."""
        parts = []
        for label, iid in (("W1", getattr(self, "_loadout_w1", None)),
                           ("W2", getattr(self, "_loadout_w2", None)),
                           ("ARS", getattr(self, "_loadout_ars", None))):
            rank = (getattr(self, "_loadout_upgrades", {}) or {}).get(iid or "", 0)
            if iid and rank:
                parts.append(f"{label} +{rank}")
        if not parts:
            return None
        w = QtWidgets.QWidget()
        w.setObjectName("RankLine")
        w.setStyleSheet("background: transparent; border: 0;")
        lay = QtWidgets.QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        lbl = QtWidgets.QLabel("⚒ RANK MAX")
        lbl.setStyleSheet(
            f'color: {theme.GOLD}; font-family: "{theme.MONO_FONT}", monospace; '
            f'font-size: 10px; font-weight: 800; letter-spacing: 1px; background: transparent;')
        val = QtWidgets.QLabel(" · ".join(parts))
        val.setStyleSheet(
            f'color: {theme.MUTED}; font-family: "{theme.MONO_FONT}", monospace; '
            f'font-size: 10px; background: transparent;')
        lay.addWidget(lbl)
        lay.addStretch(1)
        lay.addWidget(val, 0, QtCore.Qt.AlignRight)
        w.setFixedHeight(14)
        return w

    def _update_stat_totals(self) -> None:
        """Rebuild the stat sheet: the build's primary stat leads as a hero
        line, then the rest in the optimizer's own order (offense first,
        armor last), then anything unexpected alphabetically.

        `loadout_stat_totals` returns a plain dict, and the old panel listed
        it in exactly that order — a Fighter build read Dexterity, Strength,
        Critical … Magic Penetration, so the number the whole loadout is
        built around sat second and the least relevant one last.
        """
        if not hasattr(self, "_totals_container"):
            return

        while self._totals_container.count():
            item = self._totals_container.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        while self._totals_header.count():
            item = self._totals_header.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        totals = idata.loadout_stat_totals(
            weapon1_id=self._loadout_w1,
            weapon2_id=self._loadout_w2,
            arsenal_id=self._loadout_ars,
            slot_items=self._loadout_slots,
            level=25,
            upgrades=getattr(self, "_loadout_upgrades", {}),
        )
        # The same build read with the ranks dropped: the gap is exactly what
        # the upgrade ranks contributed, per stat, so the sheet can mark it
        # rather than leaving every weapon number pre-inflated and unexplained.
        unranked = idata.loadout_stat_totals(
            weapon1_id=self._loadout_w1,
            weapon2_id=self._loadout_w2,
            arsenal_id=self._loadout_ars,
            slot_items=self._loadout_slots,
            level=25,
            upgrades={},
        )
        bonus = {k: round(totals[k] - unranked.get(k, 0.0), 2) for k in totals}

        rank_line = self._upgrade_rank_line()
        if rank_line is not None:
            self._totals_header.addWidget(rank_line)

        primary = self._loadout_primary_stat()
        if primary and primary in totals:
            self._totals_container.addWidget(
                self._primary_stat_row(primary, totals[primary],
                                       bonus.get(primary, 0.0)))
        rest = [k for k in totals if k != primary]
        rest.sort(key=lambda k: ((_TOTAL_ORDER.index(k),) if k in _TOTAL_ORDER
                                 else (len(_TOTAL_ORDER), k)))
        for stat in rest:
            self._totals_container.addWidget(
                self._stat_row(stat, totals[stat], bonus.get(stat, 0.0)))

        if not totals:
            hint = QtWidgets.QLabel("Pick a class to fill the slots.")
            hint.setWordWrap(True)
            hint.setStyleSheet(f"color: {theme.MUTED}; font-size: 13px;")
            self._totals_container.addWidget(hint)


class LoadoutMixin(LoadoutCollectionMixin, LoadoutWeaponsMixin,
                   LoadoutTotalsMixin):
    """Mixin for the Items page providing the Loadout Builder tab view."""

    def _page_loadout(self, v=None):
        standalone = v is None
        if standalone:
            page, v = self._page_container()

        if not idata.available():
            v.addWidget(C.InfoCard("archive", "Data missing",
                                   "item_drops.json isn't bundled in this build — "
                                   "re-run data compile."))
            return page if standalone else None

        self._gear_slot_names = _GEAR_SLOT_NAMES

        # Initialize clean state. loadout.json is strictly an owned collection lookup file!
        self._loadout_mode = "bis"
        self._loadout_w1 = None
        self._loadout_w2 = None
        # True only while a weapon slot holds OUR auto-pick, so a class or
        # role chip can re-decide it; a hand-picked weapon is never replaced
        # (the flags are cleared in _on_weapon_picked).
        self._loadout_w1_auto = False
        self._loadout_w2_auto = False
        self._loadout_ars_auto = False
        # What the build is FOR ("tank" / "healer" / "damage"). A stat sheet
        # cannot see healing, blocking or threat — they are skills — so this
        # is stated, not inferred from the class chip.
        self._loadout_role = ""
        self._loadout_ars = None
        self._loadout_slots: dict[str, str] = {}
        self._loadout_upgrades: dict[str, int] = {}
        self._updating_class_chips = False
        self._loadout_class_filter = ""
        self._collection_class_filter = ""
        # Collection Manager narrow-by-default: open on Warrior's HEAD
        # section (a dozen tiles) instead of the 245-tile wall. Both
        # filters are single-select with an `All` chip on their bar.
        self._collection_class_filter = "Fighter"
        self._coll_slot_filter = "Head"
        self._coll_rarity_filter = ""
        # the Collection Manager view is kept between tab switches (see
        # _build_collection_manager_view); this container is brand new, so
        # the cached key must not look like it already matches it.
        # Build-sequence for the chunked stream: a stale stream (from a
        # re-created page holding an old closure) must not keep feeding a
        # container it was severed from.
        self._coll_build_seq = 0
        # Anti-reopen guard: Qt.Popup delivers the closing click to the
        # card underneath, which would instantly reopen the picker on
        # small screens. Card/row clicks within _PICKER_COOLDOWN_S of a
        # picker close are ignored.
        self._picker_cooldown_until = 0.0

        container = QtWidgets.QWidget()
        container.setStyleSheet(_PAGE_FONT_QSS)
        lay = QtWidgets.QVBoxLayout(container)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)

        # 1. Mode Switch Bar (right padding keeps the Gear Lock label
        # off the panel edge)
        mbar = QtWidgets.QHBoxLayout()
        mbar.setContentsMargins(0, 0, 10, 0)
        mbar.setSpacing(10)

        # NOT labelled "BiS": the catalog ranking is the SHEET's math, not a
        # confirmed best-in-slot — claiming otherwise overstates it.
        self._loadout_bis_btn = QtWidgets.QPushButton("Ideal Catalog")
        self._loadout_bis_btn.setCheckable(True)
        self._loadout_bis_btn.setChecked(self._loadout_mode == "bis")

        self._loadout_owned_btn = QtWidgets.QPushButton("🎒 Profile Owned Gear")
        self._loadout_owned_btn.setCheckable(True)
        self._loadout_owned_btn.setChecked(self._loadout_mode == "owned")

        self._loadout_coll_btn = QtWidgets.QPushButton("📦 Collection Manager")
        self._loadout_coll_btn.setCheckable(True)
        self._loadout_coll_btn.setChecked(self._loadout_mode == "collection")

        self._loadout_bis_btn.setStyleSheet(
            f"QPushButton {{ background: {theme.PANEL_HI}; color: {theme.MUTED}; "
            f"border: 1px solid {theme.BORDER}; border-radius: 6px; padding: 6px 14px; font-weight: 700; }}\n"
            f"QPushButton:checked {{ background: {theme.with_alpha(theme.ACCENT, 30)}; color: {theme.ACCENT}; border-color: {theme.ACCENT}; }}")

        self._loadout_owned_btn.setStyleSheet(
            f"QPushButton {{ background: {theme.PANEL_HI}; color: {theme.MUTED}; "
            f"border: 1px solid {theme.BORDER}; border-radius: 6px; padding: 6px 14px; font-weight: 700; }}\n"
            f"QPushButton:checked {{ background: {theme.with_alpha(theme.GOOD, 30)}; color: {theme.GOOD}; border-color: {theme.GOOD}; }}")

        self._loadout_coll_btn.setStyleSheet(
            f"QPushButton {{ background: {theme.PANEL_HI}; color: {theme.MUTED}; "
            f"border: 1px solid {theme.BORDER}; border-radius: 6px; padding: 6px 14px; font-weight: 700; }}\n"
            f"QPushButton:checked {{ background: {theme.with_alpha(theme.GOLD, 30)}; color: {theme.GOLD}; border-color: {theme.GOLD}; }}")

        self._loadout_bis_btn.clicked.connect(lambda: self._loadout_set_mode("bis"))
        self._loadout_owned_btn.clicked.connect(lambda: self._loadout_set_mode("owned"))
        self._loadout_coll_btn.clicked.connect(lambda: self._loadout_set_mode("collection"))

        mbar.addWidget(self._loadout_bis_btn)
        mbar.addWidget(self._loadout_owned_btn)
        mbar.addWidget(self._loadout_coll_btn)

        # Class Filter Chips (triggers auto-fill), sitting right of the mode
        # buttons. They hide in collection mode, which carries its own set
        # for its own view. Fixed height keeps them a chip row, not a bar.
        self._cgroup = QtWidgets.QWidget()
        self._cgroup.setSizePolicy(QtWidgets.QSizePolicy.Preferred,
                                   QtWidgets.QSizePolicy.Fixed)
        clay = QtWidgets.QHBoxLayout(self._cgroup)
        clay.setContentsMargins(0, 0, 0, 0)
        clay.setSpacing(8)

        self._class_chips: dict[str, C.FilterChip] = {}
        for cls in idata.gear_classes():
            chip = C.FilterChip(idata.class_label(cls), checked=False, color=theme.class_color(cls))
            chip.toggled.connect(lambda on, c=cls: self._loadout_toggle_class(c, on))
            clay.addWidget(chip)
            self._class_chips[cls] = chip
        clay.addStretch(1)
        mbar.addWidget(self._cgroup, 0, QtCore.Qt.AlignVCenter)

        # Role chips, right of the class chips, on the same bar. A class does
        # not say what the build is FOR: the sheet's healing, blocking and
        # threat are SKILLS, so the stat score counts them at zero and ranks
        # the two best healing Cleric weapons LAST of fourteen. The role has
        # to be stated (`stats.weapon_role_fit` reads the fit off the skills).
        self._role_group = QtWidgets.QWidget()
        self._role_group.setSizePolicy(QtWidgets.QSizePolicy.Preferred,
                                       QtWidgets.QSizePolicy.Fixed)
        rlay = QtWidgets.QHBoxLayout(self._role_group)
        rlay.setContentsMargins(0, 0, 0, 0)
        rlay.setSpacing(8)
        self._role_chips: dict[str, C.FilterChip] = {}
        for role in idata.LOADOUT_ROLES:
            chip = C.FilterChip(idata.role_label(role), checked=False,
                                color=theme.GOLD)
            chip.toggled.connect(
                lambda on, r=role: self._loadout_toggle_role(r, on))
            rlay.addWidget(chip)
            self._role_chips[role] = chip
        rlay.addStretch(1)
        mbar.addWidget(self._role_group, 0, QtCore.Qt.AlignVCenter)

        mbar.addStretch(1)

        self._loadout_owned_info = QtWidgets.QLabel("🔒 Level 25 Gear Lock")
        self._loadout_owned_info.setStyleSheet(
            f'color: {theme.GOLD}; font-family: "{theme.MONO_FONT}", "Consolas", monospace; font-size: 13px; font-weight: 700;')
        mbar.addWidget(self._loadout_owned_info)

        lay.addLayout(mbar)

        # Content Stack Widget (Switch between Loadout Builder & Collection Manager)
        self._loadout_stack = QtWidgets.QStackedWidget()

        # Page 0: Main Loadout Builder View
        builder_widget = QtWidgets.QWidget()
        blay = QtWidgets.QVBoxLayout(builder_widget)
        blay.setContentsMargins(0, 0, 0, 0)
        blay.setSpacing(12)

        # 2. Weapons Setup Box
        wbox = QtWidgets.QFrame()
        wbox.setObjectName("Card")
        # `.QFrame` (exact class): a bare `QFrame` rule cascades to every
        # QLabel inside (QLabel IS-A QFrame) and boxed each text line
        wbox.setStyleSheet(f".QFrame {{ background: {theme.PANEL}; border: 1px solid {theme.BORDER}; border-radius: 8px; }}")
        wlay = QtWidgets.QVBoxLayout(wbox)
        wlay.setContentsMargins(12, 12, 12, 12)
        wlay.setSpacing(10)

        whead = QtWidgets.QHBoxLayout()
        # NO "Equipped Weapons Layout" title: the three cards below already
        # read "Main Weapon 1" / "Off-Hand" / "Arsenal Weapon", so the header
        # only repeated them and cost the card a row.

        clear_w_btn = QtWidgets.QPushButton("🧹 Clear Weapons")
        clear_w_btn.setCursor(QtCore.Qt.PointingHandCursor)
        clear_w_btn.setStyleSheet(
            f"QPushButton {{ background: {theme.PANEL_HI}; color: {theme.MUTED}; border: 1px solid {theme.BORDER}; border-radius: 4px; padding: 2px 8px; font-weight: 600; font-size: 12px; }}\n"
            f"QPushButton:hover {{ color: {theme.TEXT}; border-color: {theme.TEXT}; }}")
        clear_w_btn.clicked.connect(self._clear_weapons)
        whead.addWidget(clear_w_btn)

        ars_tag = QtWidgets.QLabel("ARSENAL: 50% STATS · MAX 2 SKILLS")
        ars_tag.setStyleSheet(
            f'color: {theme.ORANGE}; font-family: "{theme.MONO_FONT}", "Consolas", monospace; font-size: 12px; font-weight: 700;')
        whead.addWidget(ars_tag, 0, QtCore.Qt.AlignRight)
        wlay.addLayout(whead)

        wgrid = QtWidgets.QHBoxLayout()
        wgrid.setSpacing(12)

        # Weapon Cards
        self._w1_card = self._build_weapon_slot_card("🗡️ Main Weapon 1", "100% STATS", theme.ACCENT, 1)
        self._w2_card = self._build_weapon_slot_card("🛡️ Off-Hand", "100% STATS", theme.ACCENT, 2)
        self._ars_card = self._build_weapon_slot_card("🏹 Arsenal Weapon", "50% STATS", theme.ORANGE, 3)

        wgrid.addWidget(self._w1_card, 1)
        wgrid.addWidget(self._w2_card, 1)
        wgrid.addWidget(self._ars_card, 1)
        wlay.addLayout(wgrid)

        blay.addWidget(wbox)

        # 3. Main Layout: Equipment Slots vs Stat Totals Sidebar
        mgrid = QtWidgets.QHBoxLayout()
        mgrid.setSpacing(16)

        # Left Column: the 11 gear slot rows. NO "Equipment Slots" title —
        # the slot labels already say what they are, and the header cost the
        # column a row (it also grew into a 40px box around an 18px label
        # when the column was shorter than the totals sidebar beside it).
        left_col = QtWidgets.QVBoxLayout()
        left_col.setSpacing(8)

        self._slot_rows_container = QtWidgets.QVBoxLayout()
        self._slot_rows_container.setSpacing(6)

        self._rebuild_slot_rows()
        left_col.addLayout(self._slot_rows_container)
        mgrid.addLayout(left_col, 1)

        # Right Sidebar: the build's stat sheet plus its actions
        # (the totals block above, in this file).
        mgrid.addWidget(self._build_totals_sidebar(), 0)

        blay.addLayout(mgrid)
        self._loadout_stack.addWidget(builder_widget)

        # Page 1: Collection Manager View
        self._coll_container = QtWidgets.QVBoxLayout()
        coll_widget = QtWidgets.QWidget()
        coll_widget.setLayout(self._coll_container)
        self._loadout_stack.addWidget(coll_widget)

        lay.addWidget(self._loadout_stack)

        # Prewarm the collection view off the interaction path: building it
        # on the first 📦 click was the ~2 s stall (245 tiles in the click
        # handler). ~1.2 s after the page shows, the view is already
        # streamed in and the click is an instant stack swap.
        QtCore.QTimer.singleShot(1200, self._loadout_stack, self._coll_prewarm)

        v.addWidget(container)
        return page if standalone else None

    def _loadout_toggle_class(self, cls: str, on: bool) -> None:
        if getattr(self, "_updating_class_chips", False):
            return
        self._updating_class_chips = True
        try:
            if on:
                self._loadout_class_filter = cls
                for c_name, chip in getattr(self, "_class_chips", {}).items():
                    if c_name != cls:
                        chip.setChecked(False)
                self._sync_role_chips(cls)
                # WEAPONS WE PICKED are re-decided for the new class, the
                # same way a role tap re-decides them: a Rogue was being left
                # holding the Cleric's staff because the auto-fill only ever
                # fills EMPTY slots, so browsing to another class showed the
                # previous class's build. A hand-picked weapon is the user's
                # and stays.
                for attr, auto in (("_loadout_w1", "_loadout_w1_auto"),
                                   ("_loadout_ars", "_loadout_ars_auto"),
                                   ("_loadout_w2", "_loadout_w2_auto")):
                    if getattr(self, auto, False):
                        setattr(self, attr, None)
                self._auto_fill_slots_for_class(cls)
            else:
                if self._loadout_class_filter == cls:
                    self._loadout_class_filter = ""
                self._sync_role_chips("")

            # the off-hand card ranks the shields for the SELECTED class, so
            # a class change has to re-render it or it keeps the old ranking
            self._refresh_weapon_cards()
            self._rebuild_slot_rows()
            self._update_stat_totals()
        finally:
            self._updating_class_chips = False

    def _sync_role_chips(self, cls: str) -> None:
        """Enable only the roles this class can play, and drop a selected role
        the new class cannot.

        A Warrior or Rogue cannot heal and a mage or Rogue cannot tank (see
        `stats._CLASS_ROLES`), so those chips go dead for those classes and a
        role carried over from the previous class is cleared rather than left
        selected-but-ignored.
        """
        for role, chip in getattr(self, "_role_chips", {}).items():
            allowed = idata.class_can_role(cls, role)
            chip.setEnabled(allowed)
            if not allowed and getattr(self, "_loadout_role", "") == role:
                self._loadout_role = ""
                chip.setChecked(False)
            chip.restyle()

    def _loadout_toggle_role(self, role: str, on: bool) -> None:
        """A role chip re-runs the fill, same as a class chip.

        The whole set is re-decided, gear included. This used to re-pick only
        the WEAPONS, on the reasoning that armour, rings and neck have no role
        in the sheets — but the sheets do record it, in the RATING column:
        Fervor is what every healing infusion in the catalog builds on
        (`Infusion_Manfish_Support`, `Priest_BlessingOfFervor`) and Critical
        plus the two penetrations are the damage ratings, and armor /
        primary / Vitality all scale off one curve so a survivability weight
        alone could never separate two candidates. So the role profiles in
        `stats._PROFILE_WEIGHTS` reach the 11 gear slots too, and a healer is
        no longer handed a damage build's set.

        Weapons WE picked are cleared and re-picked (the role is exactly what
        re-ranks them); a hand-picked weapon is the user's call and stays, in
        which case only the auto-picked slots around it move.
        """
        if getattr(self, "_updating_class_chips", False):
            return
        self._updating_class_chips = True
        try:
            if on:
                self._loadout_role = role
                for r_name, chip in getattr(self, "_role_chips", {}).items():
                    if r_name != role:
                        chip.setChecked(False)
            elif getattr(self, "_loadout_role", "") == role:
                self._loadout_role = ""
            if self._loadout_class_filter:
                for attr, auto in (("_loadout_w1", "_loadout_w1_auto"),
                                   ("_loadout_ars", "_loadout_ars_auto")):
                    if getattr(self, auto, False):
                        setattr(self, attr, None)   # ours: re-decide it
                self._auto_fill_slots_for_class(self._loadout_class_filter)
            self._refresh_weapon_cards()
            self._rebuild_slot_rows()
            self._update_stat_totals()
        finally:
            self._updating_class_chips = False

    def _auto_fill_slots_for_class(self, cls: str) -> None:
        """Auto-find and populate gear slots for class.

        Single path: optimize_loadout_build only (Rare+ dungeon armor at
        L25 + best jewelry, game-file rates). No rarity-only fallback — if
        the optimizer finds nothing (e.g. empty owned collection), slots
        stay empty instead of filling with wrong-stat gear. Empty weapon
        slots are auto-picked too (W1, then W2 unless W1 is 2H, then
        Arsenal at 50% stats); user-picked weapons are never replaced.

        The weapon picks read the ROLE chips (`_loadout_role`) as well as the
        class, and W1 is told when a shield is actually available for this
        class — a 2H main hand locks the off-hand, so a build that wants a
        shield should not be handed a two-hander on a 2-point stat margin.
        """
        pool = pdata.load_global_loadout().get("collection") if self._loadout_mode == "owned" else None
        upg = getattr(self, "_loadout_upgrades", {})
        # NO role chip means a DAMAGE build, not "no role". The kit term has
        # to be live by default or the weapons go back to being ranked blind
        # — the stat sum cannot see healing, blocking or threat at all, which
        # is what handed a Cleric a two-handed staff on a 2-point margin. It
        # also matches the score already in use: the default weights are
        # damage-shaped (Fervor 1.1, penetration 1.0, Armor 0.2), so "damage"
        # is what this page has always assumed, now stated rather than
        # implied. A stated Tank chip overrides it.
        role = getattr(self, "_loadout_role", "") or "damage"
        # the same shield rows the off-hand draws from, asked BEFORE W1 so
        # the main hand knows whether an off-hand is even on the table
        shields = [it["id"] for it in idata.items()
                   if idata.is_shield(it.get("id"))
                   and (it.get("level") or 0)
                   and (pool is None or it["id"] in pool)]

        # One weapon per slot: an auto-picked main hand must not be the weapon
        # a manually chosen Arsenal already holds (the arsenal pick below
        # excludes both hands of its own accord).
        try:
            if not self._loadout_w1:
                pick = idata.suggest_main_weapon(
                    exclude_ids=tuple(i for i in (self._loadout_w2,
                                                  self._loadout_ars) if i),
                    class_filter=cls,
                    candidate_pool=pool,
                    level=25,
                    upgrades=upg,
                    role=role,
                    prefer_one_handed=bool(shields),
                )
                if pick:
                    self._loadout_w1 = pick
                    self._loadout_w1_auto = True
                    self._max_out_weapon(pick)
                    if hasattr(self, "_w1_card"):
                        self._update_weapon_card_display(self._w1_card, pick, 1)
                    self._update_class_chips_for_weapon(pick)
            # A hand-picked off-hand stays; an AUTO-picked one is re-decided,
            # because the shield that is right for a tank is not the right one
            # for a healer (see _SHIELD_KIT_FIT) and a stale auto-pick from
            # the previous chip is simply wrong for the class now shown.
            w2_open = (not self._loadout_w2
                       or getattr(self, "_loadout_w2_auto", False))
            if w2_open and not is_2h_weapon(self._loadout_w1):
                # shields only: the off-hand is a shield slot, so the
                # auto-fill draws from the shield rows and not from the
                # whole weapon catalog. No shield for this class (or none
                # owned) leaves the off-hand EMPTY rather than falling back
                # to a one-hander — an empty slot is a visible gap, a
                # greatsword in the off-hand is a wrong build that reads
                # like a right one.
                pick = idata.suggest_offhand_shield(
                    weapon1_id=self._loadout_w1,
                    class_filter=cls,
                    candidate_pool=shields,
                    level=25,
                    role=role,
                ) if shields else None
                if pick and idata.is_shield(pick):
                    self._loadout_w2 = pick
                    self._loadout_w2_auto = True
                    self._max_out_weapon(pick)
                    if hasattr(self, "_w2_card"):
                        self._update_weapon_card_display(self._w2_card, pick, 2)
        except Exception:
            pass

        self._loadout_slots = dict(idata.optimize_loadout_build(
            weapon1_id=self._loadout_w1,
            weapon2_id=self._loadout_w2,
            arsenal_id=self._loadout_ars,
            class_filter=cls,
            candidate_pool=pool,
            level=25,
            upgrades=upg,
            role=role,
        ))

        if not self._loadout_ars:
            try:
                pick = idata.suggest_arsenal_weapon(
                    weapon1_id=self._loadout_w1,
                    weapon2_id=self._loadout_w2,
                    slot_items=self._loadout_slots,
                    class_filter=cls,
                    candidate_pool=pool,
                    level=25,
                    upgrades=upg,
                    role=role,
                )
            except Exception:
                pick = None
            if pick:
                self._loadout_ars = pick
                self._loadout_ars_auto = True
                self._max_out_weapon(pick)
                if hasattr(self, "_ars_card"):
                    self._update_weapon_card_display(self._ars_card, pick, 3)

    def _rebuild_slot_rows(self) -> None:
        while self._slot_rows_container.count():
            item = self._slot_rows_container.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        slot_names = getattr(self, "_gear_slot_names", _GEAR_SLOT_NAMES)
        for label, icon, stype in slot_names:
            row = QtWidgets.QFrame()
            row.setObjectName("SlotRow")
            row.setCursor(QtCore.Qt.PointingHandCursor)
            row.setStyleSheet(
                f"QFrame#SlotRow {{ background: {theme.PANEL}; border: 1px solid {theme.BORDER}; border-radius: 6px; }}\n"
                f"QFrame#SlotRow:hover {{ border-color: {theme.ACCENT}; background: {theme.PANEL_HI}; }}")

            def _on_row_click(ev, l=label, st=stype, r=row):
                if time.monotonic() < getattr(self, "_picker_cooldown_until", 0.0):
                    ev.accept()
                    return
                self._open_gear_picker(l, st, anchor_btn=r)
                ev.accept()

            row.mousePressEvent = _on_row_click

            rlay = QtWidgets.QHBoxLayout(row)
            rlay.setContentsMargins(10, 6, 10, 6)
            rlay.setSpacing(10)

            # Slot Icon & Title
            s_icon = QtWidgets.QLabel(icon)
            s_icon.setFixedSize(24, 24)
            s_icon.setStyleSheet(f"background: {theme.PANEL_HI}; border-radius: 4px; font-size: 15px;")
            s_icon.setAlignment(QtCore.Qt.AlignCenter)
            rlay.addWidget(s_icon)

            s_title = QtWidgets.QLabel(label.upper())
            s_title.setFixedWidth(65)
            s_title.setStyleSheet(f"color: {theme.MUTED}; font-weight: 700; font-size: 13px;")
            rlay.addWidget(s_title)

            # Current item for slot. A piece that resolves no stats at the
            # loadout level cannot be scored or shown, and is the ONLY thing
            # that disqualifies a non-jewelry slot: the dungeon sets carry no
            # `level` field at all, so listing that as junk ("Common, or
            # level 0") silently dropped 7 of the 11 slots BiS had just
            # filled — the optimizer picked them, this prune deleted them and
            # the totals never saw them.
            selected_id = self._loadout_slots.get(label)
            stat_txt = idata.format_item_stats_summary(selected_id, level=25) \
                if selected_id else ""
            if selected_id:
                sel_item = idata.item(selected_id) or {}
                sel_rar = idata.item_display_rarity(selected_id)
                sel_classes = sel_item.get("classes") or []
                w_stat = idata.weapon_primary_stat(self._loadout_w1, self._loadout_class_filter) or (idata.class_primary_stat(self._loadout_class_filter) if self._loadout_class_filter else None)
                mismatched_w_stat = w_stat and stype not in ("GearNeck", "GearFinger", "GearTrinket") and not idata.matches_stat(selected_id, w_stat)

                is_jewelry = stype in ("GearNeck", "GearFinger", "GearTrinket")
                if not is_jewelry and sel_rar in ("Common", "Uncommon"):
                    selected_id = None
                    self._loadout_slots.pop(label, None)
                elif not stat_txt:
                    selected_id = None
                    self._loadout_slots.pop(label, None)
                elif self._loadout_class_filter and sel_classes and self._loadout_class_filter not in sel_classes:
                    selected_id = None
                    self._loadout_slots.pop(label, None)
                elif mismatched_w_stat:
                    selected_id = None
                    self._loadout_slots.pop(label, None)
                elif self._loadout_mode == "owned" and not pdata.is_in_owned_collection(selected_id):
                    selected_id = None
                    self._loadout_slots.pop(label, None)

            if selected_id:
                top_item = idata.item(selected_id) or {}
                rar = idata.item_display_rarity(selected_id)
                col = theme.rarity_color(rar)

                tile = C.IconTile(28)
                tile.set("item", selected_id, col)
                rlay.addWidget(tile, 0)

                # Info Box (2 bounded lines). Transparent: the app stylesheet
                # paints every bare QWidget SURFACE, which over this row's
                # PANEL background reads as a block behind the item name.
                ibox = QtWidgets.QWidget()
                ibox.setStyleSheet("background: transparent; border: 0;")
                ilay = QtWidgets.QVBoxLayout(ibox)
                ilay.setContentsMargins(0, 0, 0, 0)
                ilay.setSpacing(2)

                iname = QtWidgets.QLabel(top_item.get("name") or selected_id)
                iname.setStyleSheet(f"color: {col}; font-weight: 700; font-size: 13px; background: transparent;")
                ilay.addWidget(iname)

                # one capped line, never a wrap: a wide piece's summary
                # spilled onto a second line mid-segment. ElideLabel cuts
                # at the cap with a trailing "…". The gear page is part of
                # a game-companion HUD: no hover tooltips anywhere on it.
                stat_lbl = C.ElideLabel(stat_txt, 260, show_tooltip=False)
                stat_lbl.setStyleSheet(
                    f'color: {theme.MUTED}; font-family: "{theme.MONO_FONT}", monospace; font-size: 12px; font-weight: 600; background: transparent;')
                ilay.addWidget(stat_lbl)

                rlay.addWidget(ibox, 1)

                # Right Action Buttons
                act_box = QtWidgets.QWidget()
                act_box.setStyleSheet("background: transparent; border: 0;")
                act_lay = QtWidgets.QHBoxLayout(act_box)
                act_lay.setContentsMargins(0, 0, 0, 0)
                act_lay.setSpacing(4)

                farm_btn = QtWidgets.QPushButton("+ Farm")
                farm_btn.setCursor(QtCore.Qt.PointingHandCursor)
                farm_btn.setStyleSheet(
                    f"QPushButton {{ background: {theme.PANEL_HI}; color: {theme.TEXT}; "
                    f"border: 1px solid {theme.BORDER}; border-radius: 4px; padding: 2px 6px; font-size: 12px; font-weight: 600; }}\n"
                    f"QPushButton:hover {{ background: {theme.with_alpha(theme.GOOD, 30)}; color: {theme.GOOD}; border-color: {theme.GOOD}; }}")
                farm_btn.clicked.connect(lambda _=False, iid=selected_id: pdata.add_gear(iid))
                act_lay.addWidget(farm_btn)

                rlay.addWidget(act_box, 0, QtCore.Qt.AlignRight)
            else:
                # Clean Empty Slot Row
                empty_lbl = QtWidgets.QLabel("No Collection Item Owned" if self._loadout_mode == "owned" else "Select Gear…")
                empty_lbl.setStyleSheet(f"color: {theme.MUTED}; font-style: italic; font-size: 13px;")
                rlay.addWidget(empty_lbl, 1)

            self._slot_rows_container.addWidget(row)

    def _open_gear_picker(self, slot_label_key: str, stype: str, anchor_btn: QtWidgets.QWidget | None = None) -> None:
        parent_w = self if isinstance(self, QtWidgets.QWidget) else None
        # the page's class chip, same as the weapon picker: a slot is filled
        # from the class the build is, and the slot rows only ever show that
        # class's pieces, so opening on every class's list was a dead end
        dlg = _ItemPickerDialog(category="Gear", slot_type=stype, parent=parent_w,
                                class_filter=getattr(self, "_loadout_class_filter", "") or "")
        dlg.item_selected.connect(lambda iid, k=slot_label_key: self._on_gear_picked(k, iid))
        dlg.closed.connect(self._picker_dismissed)
        self._active_picker_dlg = dlg
        dlg.popup_at(anchor_btn)

    def _on_gear_picked(self, slot_label_key: str, iid: str) -> None:
        """Assign a piece to a slot, keeping same-type slots distinct.

        Ring 1 and Ring 2 are the same item type and no character wears one
        ring twice: picking a piece the other finger holds MOVES it here and
        hands this slot's old piece over, exactly like the weapon slots (see
        LoadoutWeaponsMixin._on_weapon_picked). Without it the pair could hold
        one ring twice and the totals counted it twice.
        """
        types = {lbl: st for lbl, _icon, st in
                 getattr(self, "_gear_slot_names", _GEAR_SLOT_NAMES)}
        mine = types.get(slot_label_key)
        prev = self._loadout_slots.get(slot_label_key)
        if iid != prev:
            for label, held in list(self._loadout_slots.items()):
                if (label != slot_label_key and held == iid
                        and types.get(label) == mine):
                    if prev:
                        self._loadout_slots[label] = prev
                    else:
                        self._loadout_slots.pop(label, None)
                    break
        self._loadout_slots[slot_label_key] = iid
        self._rebuild_slot_rows()
        self._update_stat_totals()


    def _loadout_set_mode(self, mode: str) -> None:
        self._loadout_mode = mode
        if hasattr(self, "_loadout_bis_btn"):
            self._loadout_bis_btn.setChecked(mode == "bis")
        if hasattr(self, "_loadout_owned_btn"):
            self._loadout_owned_btn.setChecked(mode == "owned")
        if hasattr(self, "_loadout_coll_btn"):
            self._loadout_coll_btn.setChecked(mode == "collection")

        if mode == "collection":
            self._cgroup.hide()
            self._build_collection_manager_view()
            self._loadout_stack.setCurrentIndex(1)
        else:
            self._cgroup.show()
            self._loadout_stack.setCurrentIndex(0)
            self._rebuild_slot_rows()

    def _add_loadout_to_farm(self) -> None:
        for iid in self._loadout_slots.values():
            if iid:
                pdata.add_gear(iid)

    def _loadout_text(self) -> str:
        """Plain-text loadout for clipboard paste (weapons + slots + totals)."""
        upg = getattr(self, "_loadout_upgrades", {})

        def _nm(iid: str | None, rank: int = 0) -> str:
            if not iid:
                return "—"
            it = idata.item(iid) or {}
            rar = idata.item_display_rarity(iid)
            stats = idata.format_item_stats_summary(iid, level=25, upgrades=rank)
            tag = f" +{rank}" if rank else ""
            return f"{it.get('name') or iid} [{rar}]{tag} ({iid}) — {stats}"
        cls = getattr(self, "_loadout_class_filter", "") or "—"
        lines = [f"Loadout [class={cls}] (armor L25, Neck/Rings at source cap max L17)"]
        lines.append(f"W1: {_nm(getattr(self, '_loadout_w1', None), upg.get(getattr(self, '_loadout_w1', None) or '', 0))}")
        lines.append(f"W2: {_nm(getattr(self, '_loadout_w2', None), upg.get(getattr(self, '_loadout_w2', None) or '', 0))}")
        lines.append(f"Arsenal (50%): {_nm(getattr(self, '_loadout_ars', None), upg.get(getattr(self, '_loadout_ars', None) or '', 0))}")
        for label, _icon, _stype in getattr(self, "_gear_slot_names", _GEAR_SLOT_NAMES):
            lines.append(f"{label}: {_nm((getattr(self, '_loadout_slots', {}) or {}).get(label))}")
        try:
            totals = idata.loadout_stat_totals(
                getattr(self, "_loadout_w1", None),
                getattr(self, "_loadout_w2", None),
                getattr(self, "_loadout_ars", None),
                getattr(self, "_loadout_slots", {}),
                level=25,
                upgrades=upg,
            )
            lines.append("Totals: " + " · ".join(f"+{v:g} {k}" for k, v in totals.items()))
        except Exception:
            pass
        return "\n".join(lines)

    def _copy_loadout_to_clipboard(self) -> None:
        copy_text(self._loadout_text())

    def _copy_augment_plan_to_clipboard(self) -> None:
        try:
            plan = idata.suggest_augment_plan(
                getattr(self, "_loadout_w1", None),
                getattr(self, "_loadout_w2", None),
                getattr(self, "_loadout_ars", None),
                getattr(self, "_loadout_slots", {}),
                class_filter=getattr(self, "_loadout_class_filter", "") or None,
                candidate_pool=(pdata.load_global_loadout().get("collection")
                                if getattr(self, "_loadout_mode", "bis") == "owned" else None),
                level=25,
                upgrades=getattr(self, "_loadout_upgrades", {}),
            )
        except Exception:
            return
        lines = ["Augment plan (1 socket/piece assumed; arsenal at 50%)"]
        for lbl, pick in (plan.get("slots") or {}).items():
            grants = " · ".join(f"+{v:g} {n}" for n, v in pick["grants"])
            lines.append(f"{lbl}: {pick['name']} ({pick['item']}) — {grants}")
        for c in plan.get("conversions") or []:
            lines.append(f"{c['slot']}: convert {c['source']}→{c['target']} +{c['value']}")
        if len(lines) == 1:
            lines.append("(no scoring augment found for this loadout)")
        lines.append("With plan: " + " · ".join(
            f"+{v:g} {k}" for k, v in (plan.get("totals_with_plan") or {}).items()))
        copy_text("\n".join(lines))
