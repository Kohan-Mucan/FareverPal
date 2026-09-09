"""Collection-manager half of the Loadout page.

Split out of `loadout.py` so that file stays a page, not a page plus three
sub-systems. Composed onto the same page as `LoadoutMixin`.
"""
from __future__ import annotations

from PySide6 import QtCore, QtWidgets
from shiboken6 import isValid as _is_valid

from ... import theme
from ... import components as C
from ....data import items as idata
from ....craft import planner as pdata
from ....runtime import reentry

# Tile builds per event-loop tick while the collection view streams in.
# One tick of 24 tiles is ~35 ms of widget construction (measured 2026-09-28,
# in-process) — under a frame, so the UI stays responsive while ~245 tiles
# appear section by section instead of the click handler freezing on all of
# them at once (the ~2 s Collection Manager stall).
_COLL_TILES_PER_TICK = 24

# Weapons are one collection slot, however many types the catalog splits
# them into (see _SLOT_ORDER).
_WEAPON_SLOT = "Weapons"

_SLOT_ICONS = {
    "Head": "🪖", "Shoulders": "🛡️", "Chest": "🥋", "Hands": "🥊",
    "Waist": "🎗️", "Legs": "👖", "Feet": "🥾", "Back": "🧣",
    "GearNeck": "📿", "GearFinger": "💍", "GearTrinket": "🧿",
    _WEAPON_SLOT: "⚔️",
}
# Back belongs in here: every dungeon set carries three capes and the
# slot was missing, so 24 tiles per loadout were dropped silently
# Weapons come last, as ONE pseudo-slot: the catalog splits them across a
# dozen `Gear*` types (GearSword, GearStaff, …) while the collection groups
# by slot, and "the weapons" is the slot a person thinks in. No armour type
# is ever the string "Weapons", so the dungeon pass cannot spill into it.
_SLOT_ORDER = ("Head", "Shoulders", "Chest", "Hands", "Waist",
               "Legs", "Feet", "Back", "GearNeck", "GearFinger",
               "GearTrinket", _WEAPON_SLOT)
_SLOT_DISPLAY = {
    "Head": "HEAD", "Shoulders": "SHOULDERS", "Chest": "CHEST",
    "Hands": "HANDS", "Waist": "WAIST", "Legs": "LEGS",
    "Feet": "FEET", "Back": "BACK", "GearNeck": "NECK",
    "GearFinger": "RINGS", "GearTrinket": "TRINKETS",
    _WEAPON_SLOT: "WEAPONS",
}
# The tiers the collection can show, in ladder order. Whether a tier gets a
# chip depends on what it can mean for the current SLOT filter, because the
# two mean different things:
#
#   gear slot / All — a real rarity filter. Only tiers the visible set
#     actually contains are offered. The catalog has 160 Rare and 127 Epic
#     gear pieces and NO Legendary at all, so a Legendary chip there could
#     only ever open the empty state: a filter that shows nothing is worse
#     than no filter.
#   WEAPONS slot — a rarity PROJECTION. Every one of the 36 weapons is
#     display-Rare, so a real Epic/Legendary filter would match none of
#     them, yet weapons are exactly the gear that carries per-rarity stats
#     in-game (`gear_rarity_tiers` expands them up the ladder, Rare +3 /
#     Epic +4 / Legendary +5). So on that slot the tier is what to READ the
#     weapons at, and the tile says so: "Epic +4" beside the Epic numbers.
_COLL_RARITIES = ("Rare", "Epic", "Legendary")
_WEAPON_PROJECTION_RARITIES = _COLL_RARITIES


def _coll_filter_tag(text: str) -> QtWidgets.QLabel:
    """Tiny mono label introducing a filter chip group on the toolbar row."""
    lbl = QtWidgets.QLabel(text)
    lbl.setStyleSheet(
        f'color: {theme.MUTED}; font-family: "{theme.MONO_FONT}", "Consolas", '
        f'monospace; font-size: 10px; font-weight: 700;')
    return lbl


class LoadoutCollectionMixin:
    """Owned-items manager: the grouped collection view and its filters."""

    @reentry.guard("build_collection_view")
    def _build_collection_manager_view(self) -> None:
        """Build (or reuse) the Collection Manager tab, grouped by slot type.

        The view is kept between visits when NO filter changed: a build is
        ~245 item cards unfiltered (~300 ms of widget construction measured
        2026-09-28 warm, ~400 ms cold 2026-09-25), and the class / slot /
        rarity filters are the only things that change which items the view
        holds. Ownership can only be toggled from this very view — it
        relabels its own button in place — so re-entering the tab just
        shows the widget that is already there.

        Three chip groups narrow the listing (single-select each, an `All`
        chip clears its group): class (existing), SLOT and RARITY. The page
        opens pre-filtered to Warrior + Head so the manager opens on one
        small section instead of a 245-tile wall.

        The build itself is CHUNKED: this call constructs the header, the
        filter row, the scroll shell and the sections' data synchronously
        (a few ms — the stack can swap instantly), then streams in one
        `_COLL_TILES_PER_TICK` batch of tiles per event-loop tick via
        chained zero-timers. The timers are bound to the scroll body's
        context, so deleting the view (a rebuild or a page eviction) drops
        them instead of firing into a deleted tree (the
        `_craft_ensure_jobs` hazard), and a build sequence counter lets a
        newer build cancel an older stream.
        """
        filter_key = (self._collection_class_filter, self._coll_slot_filter,
                      self._coll_rarity_filter)
        if (getattr(self, "_coll_view_key", None) == filter_key
                and self._coll_container.count()):
            return

        seq = getattr(self, "_coll_build_seq", 0) + 1
        self._coll_build_seq = seq

        while self._coll_container.count():
            item = self._coll_container.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
                continue
            lay = item.layout()
            if lay is not None:
                # a taken layout no longer owns its children: delete the
                # widgets it held (the header + its chips) or they stay
                # parented to the container forever
                while lay.count():
                    sub = lay.takeAt(0)
                    if sub.widget() is not None:
                        sub.widget().deleteLater()

        # ---- data pass (no widgets): class-filtered universe by slot ----
        # The Epic twins belong in the collection too: the heroic boss
        # drops, the pieces their Heroic cache holds (the Nightling's whole
        # set) and the pieces no drop row resolves (Bee/Crimson's). They
        # are the same slots as the shared pieces, so a slot lists both
        # halves.
        by_slot: dict[str, list[tuple[str, str]]] = {}
        seen: set[str] = set()
        for grp in idata.dungeon_drop_groups():
            ids = list(grp.get("shared") or ())
            ids.extend(idata.epic_pieces(grp))
            for iid in ids:
                if iid in seen:
                    continue
                it = idata.item(iid) or {}
                stype = it.get("type") or ""
                if stype not in _SLOT_ORDER:
                    continue
                cls_list = it.get("classes") or []
                if (self._collection_class_filter and cls_list
                        and self._collection_class_filter not in cls_list):
                    continue
                seen.add(iid)
                by_slot.setdefault(stype, []).append(
                    (iid, idata.item_display_rarity(iid)))

        # Weapons, from the catalog rather than the dungeon sets: they are
        # not a set piece, so no drop group owns them, but the collection is
        # where you go to see what you can equip. Same class filter, and the
        # same "Rare and above" rule the pickers use — a Common weapon here
        # would be a slot the loadout throws straight back out.
        for it in idata.items():
            iid = it.get("id") or ""
            if not iid or iid in seen:
                continue
            if idata.category(it.get("type")) != "Weapons":
                continue
            if not (it.get("level") or 0):
                continue
            rar = idata.item_display_rarity(iid)
            if rar in ("Common", "Uncommon"):
                continue
            cls_list = it.get("classes") or []
            if (self._collection_class_filter and cls_list
                    and self._collection_class_filter not in cls_list):
                continue
            seen.add(iid)
            by_slot.setdefault(_WEAPON_SLOT, []).append((iid, rar))

        # ---- apply the slot + rarity filters ----
        sections: list[tuple[str, list[str]]] = []
        for stype in _SLOT_ORDER:
            pairs = by_slot.get(stype)
            if not pairs:
                continue
            if self._coll_slot_filter and stype != self._coll_slot_filter:
                continue
            # The rarity chip is a MATCH on a gear slot and a PROJECTION on
            # Weapons (see `_build_collection_item_tile`): every weapon in
            # the catalog is display-Rare, so matching on the tier filtered
            # the whole slot into the empty state the projection was
            # invented to avoid. The tile re-reads each piece at the tier, so
            # the section must keep every id.
            if stype == _WEAPON_SLOT:
                ids = [iid for iid, _rar in pairs]
            else:
                ids = [iid for iid, rar in pairs
                       if not self._coll_rarity_filter
                       or rar == self._coll_rarity_filter]
            if ids:
                sections.append((stype, ids))

        # ---- header: title + class chips, then the rarity group ----
        # RARITY sits up on this line with the class chips (the title is
        # pinned Fixed, so the row stays one line); SLOT keeps the next
        # row to itself.
        chdr = QtWidgets.QHBoxLayout()
        chdr.setSpacing(10)
        chdr.setContentsMargins(0, 0, 0, 0)
        # Just the name. The old "MANAGE OWNED DUNGEON GEAR" tag spelled out
        # what the view next to the section headers is (the cards carry their
        # own ✓ Collection button), and the two ran together into one run of
        # capitals.
        coll_hdr = C.SectionHeader("Collection Owned")
        coll_hdr.setSizePolicy(QtWidgets.QSizePolicy.Fixed,
                               QtWidgets.QSizePolicy.Fixed)
        chdr.addWidget(coll_hdr, 0, QtCore.Qt.AlignVCenter)

        for cls in idata.gear_classes():
            is_chk = (self._collection_class_filter == cls)
            chip = C.FilterChip(idata.class_label(cls), checked=is_chk, color=theme.class_color(cls))
            chip.toggled.connect(lambda on, c=cls: self._toggle_coll_class(c, on))
            chdr.addWidget(chip)

        chdr.addStretch(1)

        chdr.addWidget(_coll_filter_tag("RARITY"))
        all_rar = C.FilterChip("All", checked=not self._coll_rarity_filter)
        all_rar.toggled.connect(lambda on: self._toggle_coll_rarity("", on))
        chdr.addWidget(all_rar)
        # Only the tiers this slot can honour (`_coll_rarities_for`).
        #
        # This reverses an earlier decision here, which rendered EVERY tier
        # on the reasoning that "a filter that vanishes when it has nothing
        # to show is the one question a user cannot ask the UI". That
        # reasoning was right about the WEAPONS slot and wrong about gear:
        # with all 36 weapons display-Rare, Epic and Legendary there are
        # worth asking precisely because weapons carry per-rarity stats and
        # the tier is a projection ("read these at Epic +4"). On a gear slot
        # the same chips can only match real items, and no item in the
        # catalog is Legendary — so that chip opened the empty state and
        # stayed there. A tier that can never match is worse than no tier;
        # a tier that means something is a control, and the tile says which
        # it is ("Epic +4" beside the Epic numbers).
        for rar in self._coll_rarities_for():
            chip = C.FilterChip(rar, checked=self._coll_rarity_filter == rar,
                                color=theme.rarity_color(rar))
            chip.toggled.connect(lambda on, r=rar: self._toggle_coll_rarity(r, on))
            chdr.addWidget(chip)

        self._coll_container.addLayout(chdr)

        # ---- filter row: the SLOT group ----
        # Slot chips list every slot the CLASS filter leaves non-empty (they
        # ignore the slot/rarity filters themselves, so picking one slot
        # doesn't hide the way back to the others — `All` and the other
        # chips stay on the bar).
        frow = QtWidgets.QWidget()
        flay = QtWidgets.QHBoxLayout(frow)
        flay.setContentsMargins(0, 0, 0, 0)
        flay.setSpacing(6)

        flay.addWidget(_coll_filter_tag("SLOT"))
        all_slot = C.FilterChip("All", checked=not self._coll_slot_filter)
        all_slot.toggled.connect(lambda on: self._toggle_coll_slot("", on))
        flay.addWidget(all_slot)
        for stype in _SLOT_ORDER:
            if stype not in by_slot:
                continue
            chip = C.FilterChip(_SLOT_DISPLAY[stype],
                                checked=self._coll_slot_filter == stype)
            chip.toggled.connect(lambda on, s=stype: self._toggle_coll_slot(s, on))
            flay.addWidget(chip)

        flay.addStretch(1)

        self._coll_container.addWidget(frow)

        # one read of the saved collection for the whole view: asking per
        # tile re-resolves config_dir() (Path.resolve + mkdir) 192 times,
        # ~70 ms of the build
        owned = {str(i) for i in (pdata.load_global_loadout().get("collection")
                                  or ())}

        # The shell goes in NOW: the stack swap on click shows a real view
        # (header + filters + empty scroll body) immediately, sections
        # stream after.
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        # NO horizontal scrollbar, and this is load-bearing, not cosmetic.
        # The tile stat line wraps (it shows the whole summary), and a
        # wrapping label's height depends on its width. With the horizontal
        # bar on Qt's default `AsNeeded`, the content's minimum width is
        # allowed to exceed the viewport: wrapping grows the content, a
        # vertical scrollbar appears, the viewport narrows, the text wraps
        # more, the content grows — and each pass re-activates the layout
        # inside the previous one. That is a C-level re-entrancy cycle, and
        # it ends as `Windows fatal exception: stack overflow` with ONE
        # Python frame in the log (`app.py` / `return app.exec()`), because
        # the recursion never enters the interpreter.
        # `AlwaysOff` pins the content width to the viewport, so the height
        # is a plain function of a width that cannot move. This is what
        # `layout.make_scroll` does for every other scroll in the app
        # (h_scroll=False by default) — this one was built by hand and
        # missed it.
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        scroll_w = QtWidgets.QWidget()
        # and the body never asks for a width of its own
        scroll_w.setMinimumWidth(0)
        scroll_w.setSizePolicy(QtWidgets.QSizePolicy.Preferred,
                               QtWidgets.QSizePolicy.Preferred)
        slay = QtWidgets.QVBoxLayout(scroll_w)
        slay.setContentsMargins(0, 0, 0, 0)
        slay.setSpacing(16)
        scroll.setWidget(scroll_w)
        self._coll_container.addWidget(scroll)
        self._coll_view_key = filter_key

        if not sections:
            empty = QtWidgets.QLabel(
                "No pieces match these filters — tap an All chip to clear one.")
            empty.setWordWrap(True)
            empty.setStyleSheet(f"color: {theme.MUTED}; font-size: 12px;")
            slay.addWidget(empty)
            slay.addStretch(1)
            return

        # Stream the sections in: per tick, spend one `_COLL_TILES_PER_TICK`
        # budget of tiles, opening new section frames as the queue needs
        # them. `seq` cancels this stream if a newer build takes over.
        pending = list(sections)
        state = {"grid": None, "rest": []}

        def _step() -> None:
            # A stream that outlived its tree must die silently: test suites
            # pump processEvents() after pages are torn down and production
            # delivers DeferredDelete every loop pass — a tick firing into a
            # deleted grid must not raise inside the event loop (the
            # `_craft_ensure_jobs` lesson). `seq` covers a newer build
            # taking over; `_is_valid` covers the whole tree (page
            # eviction, holder teardown) dying under the stream.
            if (seq != getattr(self, "_coll_build_seq", 0)
                    or not _is_valid(scroll_w)):
                return
            try:
                budget = _COLL_TILES_PER_TICK
                while budget > 0:
                    if not state["rest"]:
                        if not pending:
                            slay.addStretch(1)
                            return
                        stype, items = pending.pop(0)
                        gbox, grid = self._build_collection_section_shell(
                            stype, len(items))
                        slay.addWidget(gbox)
                        state["grid"], state["rest"] = grid, list(items)
                        continue
                    take = state["rest"][:budget]
                    del state["rest"][:budget]
                    budget -= len(take)
                    grid = state["grid"]
                    for iid in take:
                        idx = grid.count()
                        grid.addWidget(
                            self._build_collection_item_tile(iid, owned),
                            idx // 2, idx % 2)
            except RuntimeError:
                # deleted mid-tick between the validity check and a layout
                # touch: leave the rest unstreamed; the next click / filter
                # toggle rebuilds the view from scratch anyway
                return
            QtCore.QTimer.singleShot(0, scroll_w, _step)

        # context-bound: deleting the scroll body drops the stream
        QtCore.QTimer.singleShot(0, scroll_w, _step)

    def _build_collection_section_shell(
            self, stype: str, count: int) -> tuple[QtWidgets.QFrame,
                                                   QtWidgets.QGridLayout]:
        """The slot section frame: header + empty 2-col tile grid. The tiles
        are streamed in by `_build_collection_manager_view`'s chunk timer."""
        gbox = QtWidgets.QFrame()
        gbox.setObjectName("Card")
        # `.QFrame` (exact class): a bare `QFrame` rule cascades to every
        # QLabel inside (QLabel IS-A QFrame) and boxed each text line
        gbox.setStyleSheet(
            f".QFrame {{ background: {theme.PANEL}; border: 1px solid {theme.BORDER}; border-radius: 8px; }}")
        glay = QtWidgets.QVBoxLayout(gbox)
        glay.setContentsMargins(14, 14, 14, 14)
        glay.setSpacing(10)

        icon = _SLOT_ICONS.get(stype, "📦")
        display = _SLOT_DISPLAY.get(stype, stype.upper())
        glay.addWidget(C.SectionHeader(f"{icon} {display}", tag=f"{count} ITEMS"))

        grid = QtWidgets.QGridLayout()
        grid.setSpacing(10)
        glay.addLayout(grid)
        return gbox, grid

    def _coll_prewarm(self) -> None:
        """Deferred first build of the collection view (the singleShot the
        loadout page schedules — see `_page_loadout`): the view is built off
        the interaction path, so the first 📦 Collection Manager click swaps
        the stack instantly instead of freezing on the build.

        Guards the deferred-timer hazard (`_craft_ensure_jobs`): the timer
        can fire after the page was evicted (the reclaimer drops the
        container attr) or its tree deleted, and touching either from
        Python must not raise inside the event loop.
        """
        if getattr(self, "_coll_container", None) is None:
            return
        stack = getattr(self, "_loadout_stack", None)
        if stack is None or not _is_valid(stack):
            return
        try:
            self._build_collection_manager_view()
        except (RuntimeError, AttributeError):
            return

    def _coll_rarities_for(self) -> tuple[str, ...]:
        """The rarity chips the current SLOT filter can honour.

        Weapons: every tier, because a tier is a projection there (see
        `_COLL_RARITIES`). Anything else: only the tiers the visible items
        actually carry, so a tier that cannot match anything — Legendary,
        which no item in the catalog is — is not offered at all.
        """
        if self._coll_slot_filter == _WEAPON_SLOT:
            return _WEAPON_PROJECTION_RARITIES
        present = set()
        for it in idata.items():
            if not idata.is_gear(it):
                continue
            if (self._coll_slot_filter
                    and (it.get("type") or "") != self._coll_slot_filter):
                continue
            rar = idata.item_display_rarity(it.get("id") or "")
            if rar in _COLL_RARITIES:
                present.add(rar)
        return tuple(r for r in _COLL_RARITIES if r in present)

    def _build_collection_item_tile(self, item_id: str,
                                    owned: set[str] | None = None
                                    ) -> QtWidgets.QFrame:
        """One collection card. `owned` is the saved collection read once by
        `_build_collection_manager_view`; without it the membership check
        falls back to the per-item lookup (see planner.is_in_owned_collection
        on why the whole view passes it down)."""
        card = QtWidgets.QFrame()
        # `.QFrame` (exact class): a bare `QFrame` rule cascades to the
        # card's QLabels (QLabel IS-A QFrame) — every stat line drew its
        # own 1px box. The card itself keeps its border; the lines don't.
        card.setStyleSheet(
            f".QFrame {{ background: {theme.PANEL_HI}; border: 1px solid {theme.BORDER}; border-radius: 6px; }}")
        clay = QtWidgets.QHBoxLayout(card)
        clay.setContentsMargins(10, 10, 10, 10)
        clay.setSpacing(10)

        rar = idata.item_display_rarity(item_id)
        # On the WEAPONS slot the rarity chips are a PROJECTION: the tier is
        # what to read the piece at, not a filter it has to match. Weapons
        # carry per-rarity stats in-game, so Epic is +4 and Legendary +5, and
        # the tile names the tier it is showing so the numbers cannot be
        # mistaken for the piece's own Rare ones. Everywhere else the chip is
        # a real filter and the shown item's rarity already IS the filter.
        projected = ""
        if (self._coll_slot_filter == _WEAPON_SLOT
                and self._coll_rarity_filter):
            projected = self._coll_rarity_filter
            rar = projected
        col = theme.rarity_color(rar)

        tile = C.IconTile(48)
        tile.set("item", item_id, col)
        clay.addWidget(tile, 0)

        ibox = QtWidgets.QWidget()
        ilay = QtWidgets.QVBoxLayout(ibox)
        ilay.setContentsMargins(0, 0, 0, 0)
        ilay.setSpacing(3)

        it = idata.item(item_id) or {}
        # A weapon card reads at its MAX upgrade rank, with the rank on the
        # card, because that is the piece you actually equip: picking a
        # weapon maxes it (`_on_weapon_picked` -> `_max_out_weapon`), so
        # comparing +0 numbers would be comparing a state you cannot end
        # up in. The cap is the LIVE one off the rarity's upgrade ladder
        # (Rare +3 / Epic +4 / Legendary +5), never a literal, so a patch
        # that moves a cap moves these numbers with it. Armour stays at
        # +0: the loadout does not auto-rank a piece you fill a slot with.
        # (The loadout's own weapon cards dropped their rank readout when the
        # +N stepper went — the rank is derived there too, so there is
        # nothing to read. This tile keeps its `+N` because it is a flat
        # list, not a card with a control.)
        max_rank = 0
        if idata.is_shield(item_id) or idata.category(it.get("type")) == "Weapons":
            max_rank = idata.upgrade_cap(projected or rar)
        name_txt = it.get("name") or item_id
        # the tier travels with the rank: "Epic +4" says the numbers below
        # are the Epic ones, "+3" alone would read as the piece's own Rare
        tag = f"{projected} +{max_rank}" if (projected and max_rank) else (
            f"+{max_rank}" if max_rank else "")
        iname = QtWidgets.QLabel(f"{name_txt}  {tag}" if tag else name_txt)
        iname.setStyleSheet(f"color: {col}; font-weight: 700; font-size: 13px;")
        ilay.addWidget(iname)

        stat_txt = idata.format_item_stats_summary(item_id, level=25,
                                                   upgrades=max_rank)
        # One capped line, never a wrap — and no tooltip: the gear page is
        # part of a game-companion HUD, so nothing here reacts to hover.
        # ElideLabel cuts at the cap with a trailing "…".
        stat_lbl = C.ElideLabel(stat_txt, 340, show_tooltip=False)
        stat_lbl.setStyleSheet(f'color: {theme.MUTED}; font-size: 11px;')
        ilay.addWidget(stat_lbl)

        clay.addWidget(ibox, 1)

        in_coll = (item_id in owned if owned is not None
                   else pdata.is_in_owned_collection(item_id))
        cbtn = QtWidgets.QPushButton("✓ Owned" if in_coll else "+ Add")
        cbtn.setCursor(QtCore.Qt.PointingHandCursor)
        ccol = theme.GOOD if in_coll else theme.GOLD
        cbtn.setStyleSheet(
            f"QPushButton {{ background: {theme.PANEL}; color: {ccol}; border: 1px solid {ccol}; border-radius: 4px; padding: 6px 12px; font-weight: 700; font-size: 11px; }}\n"
            f"QPushButton:hover {{ background: {theme.with_alpha(ccol, 30)}; }}")
        cbtn.clicked.connect(lambda _=False, i=item_id, b=cbtn: self._toggle_collection_item(i, b))
        clay.addWidget(cbtn, 0)

        return card

    def _toggle_coll_class(self, cls: str, on: bool) -> None:
        if on:
            self._collection_class_filter = cls
        elif self._collection_class_filter == cls:
            self._collection_class_filter = ""
        self._build_collection_manager_view()

    def _toggle_coll_slot(self, stype: str, on: bool) -> None:
        """Single-select slot filter; `All` (stype "") clears it. A rarity
        the new slot cannot honour is cleared with it (see
        `_toggle_coll_rarity`)."""
        if on:
            self._coll_slot_filter = stype
        elif self._coll_slot_filter == stype:
            self._coll_slot_filter = ""
        if self._coll_rarity_filter not in self._coll_rarities_for():
            self._coll_rarity_filter = ""
        self._build_collection_manager_view()

    def _toggle_coll_rarity(self, rar: str, on: bool) -> None:
        """Single-select rarity filter; `All` (rar "") clears it.

        A tier the new slot cannot honour is dropped rather than kept: the
        chips are rebuilt per slot, so a selection that no longer has a chip
        would otherwise stay selected and silently filter everything away.
        """
        if on:
            self._coll_rarity_filter = rar
        elif self._coll_rarity_filter == rar:
            self._coll_rarity_filter = ""
        if self._coll_rarity_filter not in self._coll_rarities_for():
            self._coll_rarity_filter = ""
        self._build_collection_manager_view()



    def _update_class_chips_for_weapon(self, weapon_id: str | None) -> None:
        """Show ONLY class chips that can equip weapon_id. Hide all non-matching class chips."""
        if not weapon_id:
            for cls, chip in getattr(self, "_class_chips", {}).items():
                chip.setVisible(True)
            return

        it = idata.item(weapon_id) or {}
        allowed = it.get("classes") or []

        self._updating_class_chips = True
        try:
            for cls, chip in getattr(self, "_class_chips", {}).items():
                is_valid = not allowed or cls in allowed
                chip.setVisible(is_valid)
                if not is_valid and chip.isChecked():
                    chip.setChecked(False)
                    if self._loadout_class_filter == cls:
                        self._loadout_class_filter = ""
        finally:
            self._updating_class_chips = False


    def _toggle_collection_item(self, iid: str, btn: QtWidgets.QPushButton) -> None:
        in_coll = pdata.is_in_owned_collection(iid)
        if in_coll:
            pdata.remove_from_owned_collection(iid)
            btn.setText("+ Collection")
            btn.setStyleSheet(
                f"QPushButton {{ background: {theme.PANEL_HI}; color: {theme.GOLD}; "
                f"border: 1px solid {theme.GOLD}; border-radius: 4px; padding: 2px 6px; font-size: 10px; font-weight: 600; }}")
        else:
            pdata.add_to_owned_collection(iid)
            btn.setText("✓ Collection")
            btn.setStyleSheet(
                f"QPushButton {{ background: {theme.PANEL_HI}; color: {theme.GOOD}; "
                f"border: 1px solid {theme.GOOD}; border-radius: 4px; padding: 2px 6px; font-size: 10px; font-weight: 600; }}")
