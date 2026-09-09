"""Enchants-page assembly: the full enchant database on one dedicated
page — the Enchants tab of the Items page. Lists the +2 / corrupted
enchant scrolls, the Jeweller gem augments, the demon-gear stat
conversions, the gear-upgrade materials, the craftable Elixirs and Food
dishes, the heroic Infusion Patterns (the Infusion System's crucible
recipes), and the merged profession augments card. A search box and a Stat
chip row narrow every card; a tab bar switches one full-width group per
category. Builds into a caller layout or standalone via _page_container().

The family is this file plus ONE shard: the tables all live in
`enchants_rows.py`, and the Infusions tab's own render code is here. The
line budget measures one file, so a family this size needs a second - a shard
of real size, not a module per idea per tab.
"""
from __future__ import annotations

from PySide6 import QtCore, QtWidgets

from ... import theme
from ... import components as C
from ...chips import ChoiceChips
from ....data import items as idata
from .enchants_rows import (EnchantsRowsMixin, EnchantsUpgradesMixin,
                            _AUG_SLOT, _AUG_SLOT_COLOR)
from .support import FACTION_EMOJI, GLYPH_DOWN, GLYPH_UP, faction_rank


# the three roles in the order the pool sorts them, and their colors: DPS
# red, Tank cyan, Support green — the class / Compare bar's own language, so
# no infusion-only palette is invented
_ROLES = ("DPS", "Tank", "Support")
_ROLE_COLOR = {"DPS": theme.DANGER, "Tank": theme.ACCENT,
               "Support": theme.GOOD}
# the row is ONE block, not a grid: the pattern's name sits INLINE at
# the head of the granted skill (the name + skill are the row's title line),
# and the (2)/(4)/(6) ladder runs full width under it. It was two columns —
# a fixed identity column beside the tier column — which spent ~150px of
# every row on a name box at most 120px wide and boxed the tier prose into a
# column narrower than the card. The name is text, not a chip: clicking it
# still opens the item card, but it wears no box, no hover and no hand cursor
# (see _ench_infusion_pill)
# the ladder's size ceiling: the largest tier size at which a faction's
# three rows still clear the default window (see the measurement on the size
# below — 15px already fails it)
_TIER_FOLD_PX = 14
# the tier prose size, for the tags and the bodies alike. 13px is set by the
# FOLD, not by taste: a faction is three rows, and above them the card owes
# the page a search box, the stat chips and the tab strip — at the previous
# 18px a three-row faction needed 645px where the default window (1180x740)
# opens the tab with 641, so the ladder scrolled 187px past the fold and the
# three pattern boxes read as tall cards holding a line of text each.
# Measured at that window: 15px still scrolls 16px, 14px and 13px land on 0.
# The item card prints the same three tiers a notch up (_INFUSION_PX in
# detail.py): that surface scrolls by design, so it keeps the reading size
_TIER_PX = 14
# the granted skill's own size — the row's title, one step above the tiers
_SKILL_PX = 15
_MONO = f'font-family:"{theme.MONO_FONT}","Consolas",monospace;'
# NO tier is gold: the (2)/(4)/(6) tags all wear one label color (the
# ladder's shape is not news, and a gold (4) tag made the label read as part
# of the affix), and the (4) BODY — the only tier carrying numbers — is the
# app's plain text color rather than a hue. What is left stands out by
# brightness: (4) is the brightest line in the block, the (2) prose sits at
# MUTED, and the (6) rank text at DIM
_TIER_TAG = theme.DIM


class _InfusionRowHost(QtWidgets.QWidget):
    """The pattern rows' host — it keeps the row cards level.

    Each card sizes to its own content, so a tier sentence that wrapped to a
    second line made its card a line taller than its neighbours and the
    column's bottom edge went ragged. Leveling runs on every resize (a
    wrapped height only exists once a width is known) and once after each
    rebuild, and it pins a MINIMUM rather than a fixed height: a window
    narrow enough to need more room grows the card instead of clipping the
    sentence inside it.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._level_armed = False

    def cards(self) -> list:
        """This host's row cards. Searched rather than read off the layout:
        the cards sit in a layout NESTED in the host's own, and the host's
        other children (the MORE link, the empty-state message) carry no
        `item_id`, so neither is mistaken for a card."""
        return [w for w in self.findChildren(QtWidgets.QFrame)
                if w.property("item_id")]

    def level_later(self) -> None:
        """Level after the layout has run: a freshly built card only learns
        its wrapped height once Qt has given it the host's width, which is a
        pass later than the build."""
        if self._level_armed:
            return
        self._level_armed = True
        QtCore.QTimer.singleShot(0, self._level_now)

    def _level_now(self) -> None:
        self._level_armed = False
        self.level()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.level()

    def level(self) -> None:
        """Every card takes the tallest card's natural height.

        `heightForWidth` is the wrapped height at the width the card really
        has, plus the card's own frame border (the QSS `border` on the Card
        frame is not part of the layout, so leaving it out pins every card
        2px shy of the tallest one). Derived from the width and the style
        rather than from the rendered heights, which is what makes this
        idempotent and ratchet-free: widening the window re-levels them all
        shorter, narrowing it re-levels them taller."""
        try:
            cards = self.cards()
            if len(cards) < 2:
                return
            tall = 0
            for c in cards:
                lay = c.layout()
                if lay is None or c.width() <= 1:
                    return          # not laid out yet: the next pass levels
                tall = max(tall, lay.heightForWidth(c.width())
                           + 2 * c.frameWidth())
            if tall <= 0:
                return
            for c in cards:
                if c.minimumHeight() != tall:   # no redundant layout pass
                    c.setMinimumHeight(tall)
        except RuntimeError:
            # the deferred pass can outlive the page it was armed from
            # (the page cache sheds trees with deleteLater)
            return


class EnchantsInfusionsMixin:
    """The Infusions card: the faction chips and the pattern rows."""

    def _ench_infusion_matches(self, p: dict, q: str) -> bool:
        """Search-query match for one infusion pattern: its display name,
        faction, role, the skill it grants (incl. the ultimate), or the
        heroic boss / dungeon that drops it."""
        hay = (f"{p['name']} {p['faction']} {p['role']} "
               f"{p.get('skill') or ''} {p.get('ultimate') or ''} "
               f"{p['boss']} {p['unlock_boss']} {p['dungeon']}")
        return q in hay.lower()

    # --- mount ----------------------------------------------------------
    def _ench_infusion_mount(self, lay, pats: list[dict]) -> None:
        """Show the filtered patterns in the Infusions card. The card's chip
        rows are built once and kept (`_ench_infusion_host`); only the
        pattern rows rebuild, so a chip click can't delete the chip that
        fired it. A host a previous pass cleared away (the layout goes empty
        when nothing matches) is rebuilt here."""
        host = getattr(self, "_ench_infusion_host", None)
        if host is not None:
            try:
                alive = host.parent() is not None
            except RuntimeError:        # the previous pass deleted it
                alive = False
            if not alive:
                host = None
        if host is None:
            self._clear_lay(lay)
            host = self._ench_infusion_host_build()
            self._ench_infusion_host = host
            lay.addWidget(host)
        self._ench_infusion_refresh(pats)

    def _ench_infusion_host_build(self) -> QtWidgets.QWidget:
        """The card's fixed furniture: the faction chips (radio — exactly one
        lit, never an all-factions state) over the pattern rows."""
        host = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(host)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)

        # every faction the pool holds gets a chip, in the Dungeons tab's
        # order (a faction a future patch adds appears here on its own).
        # A QButtonGroup makes them a real radio: Qt unchecks the others for
        # us, so each chip's own toggled -> restyle() runs and exactly ONE is
        # ever painted as selected (and clicking the lit chip can't uncheck
        # it, which is the behaviour we want here).
        self._ench_infusion_chips: dict[str, C.FilterChip] = {}
        self._ench_infusion_group = QtWidgets.QButtonGroup(host)
        self._ench_infusion_group.setExclusive(True)
        self._ench_infusion_fac = getattr(self, "_ench_infusion_fac", "")
        fac_row = QtWidgets.QWidget()
        fl = QtWidgets.QHBoxLayout(fac_row)
        fl.setContentsMargins(0, 0, 0, 0)
        fl.setSpacing(6)
        for fac in sorted((p["faction"] for p in self._ench_infusions),
                          key=faction_rank):
            if fac in self._ench_infusion_chips:
                continue
            chip = C.FilterChip(self._ench_infusion_chip_text(fac, 0),
                                checked=(fac == self._ench_infusion_fac),
                                color=theme.GOLD, parent=fac_row)
            self._ench_infusion_group.addButton(chip)
            # clicked, not toggled: the group owns the checked state now, and
            # a click is what can mean "release this faction" (see the
            # handler) — toggled can't, because Qt never unchecks the radio
            chip.clicked.connect(
                lambda _=False, f=fac: self._ench_infusion_pick_faction(f))
            fl.addWidget(chip)
            self._ench_infusion_chips[fac] = chip
        fl.addStretch(1)
        v.addWidget(fac_row)

        rows = _InfusionRowHost()
        self._ench_infusion_rows = rows          # the rows' host (rebuilds)
        self._ench_infusion_rows_lay = QtWidgets.QVBoxLayout(rows)
        self._ench_infusion_rows_lay.setContentsMargins(0, 0, 0, 0)
        self._ench_infusion_rows_lay.setSpacing(6)
        v.addWidget(rows)
        v.addStretch(1)
        return host

    def _ench_infusion_chip_text(self, fac: str, n: int) -> str:
        """A faction chip's label: emoji · faction · how many of its patterns
        the live search leaves (the same count the rows show)."""
        emo = FACTION_EMOJI.get(fac, "🏰")
        return f"{emo} {fac} · {n}" if n else f"{emo} {fac}"

    def _ench_infusion_pick_faction(self, fac: str) -> None:
        """A faction chip was clicked: show that faction's rows. The group is
        exclusive, so Qt has already cleared the chip that was lit — and its
        own toggled signal fired, which is what re-paints it. Clicking the
        LIT chip does nothing: those rows need a faction to show."""
        if getattr(self, "_ench_infusion_sync", False):
            return                       # a programmatic set, not a user click
        if fac == self._ench_infusion_fac:
            return
        self._ench_infusion_fac = fac
        self._ench_refilter()

    def _ench_infusion_check_only(self, fac: str) -> None:
        """Light exactly one faction chip (the refresh moves the pick on its
        own when a search empties the picked faction). Exclusivity has to
        come off for the sweep — Qt refuses to clear an exclusive group —
        and it comes straight back on; every chip repaints from its own
        toggled signal, which a signals-blocked setChecked would skip."""
        group = getattr(self, "_ench_infusion_group", None)
        self._ench_infusion_sync = True
        try:
            if group is not None:
                group.setExclusive(False)
            for key, chip in self._ench_infusion_chips.items():
                chip.setChecked(key == fac)
            if group is not None:
                group.setExclusive(True)
        finally:
            self._ench_infusion_sync = False

    # --- rows -----------------------------------------------------------
    def _ench_infusion_refresh(self, pats: list[dict]) -> None:
        """Rebuild the pattern rows and re-label the chips.

        The faction chips are the card's only filter, over the same twelve
        patterns: exactly one is lit and its three rows (DPS → Tank →
        Support) are what the card shows. Chip counts say how many rows that
        faction contributes under the live search, so a chip the search
        empties hides — and if the picked faction is the one that emptied,
        the pick falls to the first faction still standing rather than
        leaving an empty card under a hidden chip."""
        by_fac: dict[str, list[dict]] = {}
        for p in pats:
            by_fac.setdefault(p["faction"], []).append(p)
        for fac, chip in self._ench_infusion_chips.items():
            n = len(by_fac.get(fac, ()))
            text = self._ench_infusion_chip_text(fac, n)
            if chip.text() != text:
                chip.setText(text)
            chip.setVisible(bool(n))
        if self._ench_infusion_fac not in by_fac:
            self._ench_infusion_fac = (sorted(by_fac, key=faction_rank)[0]
                                       if by_fac else "")
        rows = list(by_fac.get(self._ench_infusion_fac, ()))
        self._ench_infusion_check_only(self._ench_infusion_fac)
        self._clear_lay(self._ench_infusion_rows_lay)
        if not rows:
            self._empty_row(self._ench_infusion_rows_lay,
                            "No infusion pattern matches the current "
                            "filters.")
            return
        rows.sort(key=lambda p: (faction_rank(p["faction"]),
                                 _ROLES.index(p["role"])
                                 if p["role"] in _ROLES else len(_ROLES)))
        self._ench_infusion_rows_lay.addLayout(self._build_infusion_rows(rows))
        # the new cards are level once they have a width: one wrapped tier (a
        # (6) sentence that runs to two lines) used to leave its card taller
        self._ench_infusion_rows.level_later()

    def _build_infusion_rows(self, pats: list[dict]
                             ) -> QtWidgets.QVBoxLayout:
        """The filtered patterns as full-width rows — one CARD per pattern,
        the app's own row treatment (the Dungeons tab's boss rows wear
        exactly this frame): its title line (the role-colored name, the
        granted skill, and the heroic source at the line's end — see
        _ench_infusion_header) over the full-width (2)/(4)/(6) ladder.

        Each card carries its own layout rather than one grid spanning the
        rows: the cards still line up because every card spans the same
        width and every name reserves the same width, and a card is what
        makes a row read as one block instead of a striped table line."""
        lay = QtWidgets.QVBoxLayout()
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        expanded = bool(getattr(self, "_ench_infusion_expand", False))
        # build every name first so they can share one width: the name sits
        # inline before the skill, and ragged name widths would start each
        # card's skill at its own indent (BEE DPS vs MANFISH SUPPORT)
        pills = {p["item"]: self._ench_infusion_pill(p) for p in pats}
        pill_w = max((b.sizeHint().width() for b in pills.values()), default=0)
        for pill in pills.values():
            pill.setMinimumWidth(pill_w)
        clipped = False
        for p in pats:
            card = QtWidgets.QFrame()
            card.setObjectName("Card")
            card.setProperty("item_id", p["item"])
            v = QtWidgets.QVBoxLayout(card)
            v.setContentsMargins(10, 8, 10, 8)
            v.setSpacing(3)
            v.addWidget(self._ench_infusion_header(p, pills[p["item"]]))
            cell, cut = self._ench_infusion_body_cell(p, expanded)
            clipped = clipped or cut
            v.addWidget(cell)
            lay.addWidget(card)
        if clipped or expanded:
            lay.addWidget(self._ench_infusion_more_link(expanded))
        return lay

    def _ench_infusion_pill(self, p: dict) -> QtWidgets.QPushButton:
        """The pattern's name as PLAIN role-colored text at the head of the
        row's title line — it is the link into the pattern's item card, but
        it does not advertise it: no box, no fill, no border, no hover, and
        the mouse stays an arrow over it.

        It is still a QPushButton (the click opens the card), which is why
        every state needs naming: the app sheet owns QPushButton:hover's
        PANEL_HI fill and QPushButton:pressed's bright ACCENT_DIM one for
        every button, and a widget's own stylesheet only takes a state back
        where it names it. Leaving them unnamed grew the chip a tonal fill and
        then flashed a brighter box on click, and an element picker probing
        the running app synthesizes a mouse-over — so the row lit up under a
        mouse nobody moved. What is left is type: the role color, nothing to
        fill and nothing to press. No item icon either — the pattern art is a
        scroll glyph that said nothing the name and role don't."""
        color = _ROLE_COLOR.get(p["role"], theme.MUTED)
        pill = QtWidgets.QPushButton(p["name"].upper())
        pill.setFlat(True)
        # a click must not leave the button focused, or the next mouse-in
        # repaints the focus frame it kept (the same "was I clicked?" look)
        pill.setFocusPolicy(QtCore.Qt.NoFocus)
        # NO cursor: a hand over a line of words is itself the "clickable"
        # tell this is getting rid of — the words look like words, and the
        # click still lands
        base = (f"color:{color};background:transparent;border:0;padding:0;"
                "text-align:left;font-size:11.5px;font-weight:700;"
                "letter-spacing:1px;")
        pill.setStyleSheet(
            f"QPushButton, QPushButton:hover, QPushButton:pressed, "
            f"QPushButton:checked, QPushButton:focus{{{base}}}")
        iid = p["item"]
        pill.clicked.connect(lambda _=False, i=iid: self._items_show_id(i))
        return pill

    def _ench_infusion_source(self, p: dict) -> str:
        """The pattern's heroic source as 'Boss — Dungeon'.

        The unlock boss' DISPLAY name leads when the scan resolved one ('Lady
        Bee' — the dungeons pane's boss_name, which is also what the
        crucible's own unlock text says), falling back to the unit id
        ('Mokshi'); 'source unresolved' when neither the boss nor the dungeon
        is known (a scan that predates the patch)."""
        boss = p.get("unlock_boss") or p.get("boss") or ""
        dungeon = p.get("dungeon") or ""
        if boss and dungeon:
            return f"{boss} — {dungeon}"
        return boss or dungeon or "source unresolved"

    def _ench_infusion_header(self, p: dict,
                              pill: QtWidgets.QPushButton) -> QtWidgets.QWidget:
        """The row's title line: the name, the granted skill (the ladder's
        header, so the skill is named once, at the top of the tiers it
        belongs to) with its ★ ultimate, then the heroic source pinned to
        the line's end.

        The source rides the header rather than a line of its own under the
        tiers: it is one short fact about the WHOLE pattern (the boss whose
        heroic *_LT2 table guarantees the drop, and that boss's dungeon), it
        is what the name opens into — the item card's Drops From — and it is
        what reads two same-named rows of different factions apart at a
        glance. Muted and small, so it sits beside the title as provenance
        instead of competing with the skill's name."""
        w = QtWidgets.QWidget()
        h = QtWidgets.QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)
        h.addWidget(pill, 0, QtCore.Qt.AlignVCenter)
        sk = QtWidgets.QLabel()
        sk.setTextFormat(QtCore.Qt.RichText)
        sk.setWordWrap(True)
        name = p.get("skill") or ""
        txt = (f'<b style="color:{theme.GOLD};">{name}</b>' if name
               else f'<span style="color:{theme.DIM};">unresolved skill</span>')
        if p.get("ultimate"):
            txt += (f' <span style="color:{theme.GOLD};">· ★ '
                    f'{p["ultimate"]}</span>')
        sk.setText(txt)
        sk.setStyleSheet(
            f"color:{theme.TEXT};font-size:{_SKILL_PX}px;"
            "background:transparent;")
        h.addWidget(sk, 1)
        # the source never wraps or stretches: it is the line's right-hand
        # fact, so the skill's own text absorbs the width instead
        src = QtWidgets.QLabel(self._ench_infusion_source(p))
        src.setObjectName("Mono")
        src.setStyleSheet(
            f"color:{theme.DIM};{_MONO}font-size:11px;"
            "letter-spacing:0.5px;background:transparent;")
        h.addWidget(src, 0, QtCore.Qt.AlignVCenter)
        return w

    def _ench_infusion_body_cell(self, p: dict, expanded: bool
                                 ) -> tuple[QtWidgets.QWidget, bool]:
        """The crucible's three tiers, full width under the title line: (2)
        the skill's base description, (4) its threshold stat affixes, (6) its
        rank description — the order the in-game InfusionUI skill panel uses.

        Returns the cell and whether any tier was clipped to its preview."""
        w = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(3)
        clipped = False
        for tag, text, color in (
                ("(2) Set :", p.get("set2") or "", theme.MUTED),
                ("(4) Set :", "  ·  ".join(p.get("set4") or ()), theme.TEXT),
                ("(6) Set :", p.get("set6") or "", theme.GOLD)):
            if not text:
                continue
            row = QtWidgets.QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(6)
            lbl = QtWidgets.QLabel(tag)
            lbl.setStyleSheet(
                f"color:{_TIER_TAG};{_MONO}font-size:{_TIER_PX}px;"
                "font-weight:700;background:transparent;")
            lbl.setAlignment(QtCore.Qt.AlignTop)
            row.addWidget(lbl, 0, QtCore.Qt.AlignTop)
            body = QtWidgets.QLabel(text)
            body.setWordWrap(True)
            body.setStyleSheet(
                f"color:{color};font-size:{_TIER_PX}px;background:transparent;")
            if not expanded:
                # a two-line preview: the sheet's own 'X' slots and long
                # paragraphs would otherwise double every row's height, and
                # the full text stays one hover (or one MORE) away. The room
                # is generous here — this column is the card's widest — so a
                # tier only clips when it is genuinely long
                shown = self._ench_infusion_preview(body, text, lbl)
                if shown != text:
                    clipped = True
                    body.setToolTip(text)
                body.setText(shown)
            row.addWidget(body, 1)
            v.addLayout(row)
        v.addStretch(1)
        return w, clipped

    def _ench_infusion_preview(self, body: QtWidgets.QLabel, text: str,
                               tag: QtWidgets.QLabel) -> str:
        """`text` elided to two lines' worth at the tier block's width."""
        room = max(240, (_TIER_PREVIEW_W - tag.sizeHint().width() - 6) * 2)
        return body.fontMetrics().elidedText(text, QtCore.Qt.ElideRight, room)

    def _ench_infusion_more_link(self, expanded: bool
                                 ) -> QtWidgets.QPushButton:
        """The card's single MORE / LESS link — every row's tiers widen or
        collapse together, so the state is one flag on the page and survives
        a search or a tab flip."""
        more = QtWidgets.QPushButton(
            f"LESS {GLYPH_UP}" if expanded else f"MORE DETAIL {GLYPH_DOWN}")
        more.setFlat(True)
        more.setFocusPolicy(QtCore.Qt.NoFocus)
        # the name label's rule, cursor included: the app sheet's
        # QPushButton:hover / :pressed fills are pinned back here too (or the
        # link grows a box under the mouse and a bigger one on click), and no
        # hand cursor, so the row's links are a line of text end to end
        base = (f"color:{theme.ACCENT};background:transparent;border:0;"
                "text-align:left;font-size:11.5px;font-weight:700;"
                "letter-spacing:1px;padding:0 0 0 2px;")
        more.setStyleSheet(
            f"QPushButton, QPushButton:hover, QPushButton:pressed{{{base}}}"
            f"QPushButton:hover{{color:{theme.ACCENT_LIGHT};}}")
        more.clicked.connect(lambda _=False: self._ench_infusion_toggle_more())
        return more

    def _ench_infusion_toggle_more(self) -> None:
        """Swap every tier between its two-line preview and the sheet's full
        text (the flag rides the page)."""
        self._ench_infusion_expand = not getattr(
            self, "_ench_infusion_expand", False)
        self._ench_refilter()


# the tier block's width the two-line preview is measured against. The
# block now spans the whole row card (the name pill moved into the title
# line), so this is the card's content width at a normal window — and it is
# deliberately a little conservative, because eliding early costs one hover
# while eliding late costs a clipped sentence
_TIER_PREVIEW_W = 900

__all__ = ["EnchantsInfusionsMixin"]


class EnchantsPageBase(EnchantsUpgradesMixin, EnchantsRowsMixin,
                       EnchantsInfusionsMixin):
    def _page_enchants(self, v=None):
        """Build the enchant database. With a layout `v` given, builds into
        it in place (the Items Enchants tab); without one, builds a
        standalone page through _page_container()."""
        standalone = v is None
        if standalone:
            page, v = self._page_container()

        if not idata.available():
            v.addWidget(C.InfoCard("archive", "Data missing",
                                   "item_drops.json isn't bundled in this build — "
                                   "re-run the data compile to ship the enchant "
                                   "database."))
            return page if standalone else None

        self._ench_scrolls = idata.enchant_scrolls()
        self._ench_corrupt = idata.corrupted_scrolls()
        # item id -> corrupted-scroll entry, for the augment rows' grant
        self._ench_corrupt_by_item = {c["item"]: c
                                      for c in self._ench_corrupt}
        # stat -> item id for the +2 scrolls, so a row can open its recipe
        # (Intellect's scroll isn't a stat concat: it's ScrollOfIntelligence)
        self._ench_scroll_items: dict[str, str] = {}
        for it in idata.items():
            iid = it.get("id") or ""
            if not iid.startswith("ScrollOf"):
                continue
            pos = next((s for s in (idata.own_stats(it) or [])
                        if s["v"] > 0), None)
            if pos:
                self._ench_scroll_items[pos["n"]] = iid
        self._ench_gems = idata.gem_augments()
        self._ench_convs = idata.enchant_conversions()
        # the gear-upgrade materials (Spark Dust / Shard / Crystal) with
        # their live caps + sheet costs — the Upgrades tab's card
        self._ench_upgrade_mats = idata.upgrade_materials()
        # the gem table's fixed column set — every stat any gem grants, so
        # the columns never reflow when the stat/search filters narrow rows
        self._ench_gem_cols = sorted(
            {s for g in self._ench_gems for s in g["stats"]})
        # the crafting consumables — crafted Elixirs (Alchemist) and cooked
        # Food dishes (Cook); raw ingredients have no recipe, stay listable
        self._ench_elixirs = [
            it for it in idata.items()
            if (it.get("type") or "") == "Elixir"
            and idata.recipe(it.get("id") or "")]
        self._ench_foods = [
            it for it in idata.items()
            if (it.get("type") or "") == "Food"
            and idata.recipe(it.get("id") or "")]
        # the Enchanter-crafted enchant items — the +2/corrupted scrolls
        # plus the Magic Formula enchants (AugmentEnchant*). Raw Enchanter
        # materials have no enchant info and stay on the Items list.
        self._ench_enchanter = [
            it for it in idata.items()
            if (it.get("id") or "").startswith(("ScrollOf", "Formula"))
            and idata.recipe(it.get("id") or "")]
        # the Outfitter-crafted augment embroideries (AugmentOutfitter*),
        # the same recipe-jump card as the Enchanter recipes
        self._ench_outfitter = [
            it for it in idata.items()
            if (it.get("type") or "") == "AugmentOutfitter"
            and idata.recipe(it.get("id") or "")]
        # the Blacksmith-crafted augment plates (AugmentBlacksmith*), the
        # same recipe-jump card — completing the profession trio
        self._ench_blacksmith = [
            it for it in idata.items()
            if (it.get("type") or "") == "AugmentBlacksmith"
            and idata.recipe(it.get("id") or "")]
        # the heroic Infusion System's pattern pool — one per heroic
        # boss (the *_LT2 tables), faction + role parsed from the id
        self._ench_infusions = list(idata.heroic_infusions())

        head = QtWidgets.QHBoxLayout()
        h1 = QtWidgets.QLabel("ENCHANTS & GEMS")
        h1.setObjectName("H1")
        # search: matches scrolls / gems / conversions by item name or stat
        # text — composes with the stat chips below (narrows within them);
        # sits left beside the heading, the stretch pushes it to the start
        self._ench_search = C.SearchInput(
            "Search name or stat…", on_text_changed=self._ench_refilter)
        self._ench_search.setFixedWidth(240)
        self._ench_search.setFixedHeight(30)
        head.addWidget(self._ench_search)
        head.addStretch(1)
        v.addLayout(head)

        # the stat filter — one labeled chip row (the shared dimension:
        # every stat any scroll / gem / conversion / crafting consumable
        # touches), combo-like so the gear-page ENCHANTS jump pre-sets it
        stats = sorted({s for s in self._ench_scrolls}
                       | {s for c in self._ench_corrupt
                          for s in (c["stat"], c["penalty"])}
                       | {s for g in self._ench_gems for s in g["stats"]}
                       | {s for c in self._ench_convs
                          for s in (c["source"], c["target"])}
                       | {s["n"] for it in self._ench_elixirs
                          + self._ench_foods + self._ench_enchanter
                          + self._ench_outfitter + self._ench_blacksmith
                          for s in (idata.own_stats(it) or [])})
        self._ench_stat_names = stats
        self._ench_stat = ChoiceChips([(s, s) for s in stats],
                                      any_label="All stats",
                                      colors={s: theme.stat_color(s) for s in stats})
        v.addWidget(self._ench_stat)
        self._ench_stat.currentIndexChanged.connect(self._ench_refilter)

        # the seven cards — the row bodies are rebuilt in place on filter.
        # The scrolls' 30M craft duration rides the card title — every
        # scroll is visible at once, so the count adds nothing
        scard, self._ench_scrolls_lay, self._ench_scrolls_head = \
            self._ench_card("Enchant Scrolls · 30M")
        # no card header — the zone rows (CURSED · JEWELLER LV 6 …) and
        # the stat columns already say what's inside
        gcard, self._ench_gems_lay, _ = self._ench_card(
            "Gem Augments", header=False)
        # no legend under the gem table — the Cursed Eyes' danger-red −9
        # cells and accent +9s read from the signed values themselves
        # every DemonGearUpgrade_* id shares the display name 'Corrupted Gift'
        ccard, self._ench_convs_lay, _ = self._ench_card(
            "Corrupted Gift · Demon-Gear Conversions")
        # the gear-upgrade cost table — one summary tile per upgradable rarity
        # over the per-step matrix (jewelry is hidden, it has no upgrades; armor
        # is hidden too, its upgrades are not in the game yet)
        ucard, self._ench_upgrades_lay, _ = self._ench_card(
            "Gear Upgrades", header=False)
        self._ench_upgrade_type = "Weapon"
        # the cost level the Upgrades table prices at, and the chips that pick
        # it. The levels are the ones the costs were actually MEASURED at (the
        # anchor rarity's readings, capped at the game's max — 20 and 25 today),
        # not a schedule: a level with no measurement behind it would show a
        # table of extrapolated figures, so it is not offered. Adding a
        # measured row in stats.py adds the chip here; see docs/UPGRADE_COSTS.md.
        # The default is the highest, i.e. the cap the game actually allows.
        self._ench_upgrade_levels = idata.upgrade_cost_levels()
        self._ench_upgrade_level = self._ench_upgrade_levels[-1]
        # the last built table, as data (see EnchantsUpgradesMixin in
        # enchants_rows.py): the tiles and
        # the matrix render from it and the tests assert on it
        self._ench_upgrade_table: dict | None = None
        type_row = QtWidgets.QWidget()
        tl = QtWidgets.QHBoxLayout(type_row)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.setSpacing(6)
        gl = QtWidgets.QLabel("GEAR")
        gl.setObjectName("FieldLabel")
        tl.addWidget(gl)
        self._ench_upgrade_btns = {}
        wb = QtWidgets.QPushButton("WEAPON")
        wb.setCursor(QtCore.Qt.PointingHandCursor)
        wb.clicked.connect(
            lambda _=False: self._ench_upgrade_set_type("Weapon"))
        tl.addWidget(wb)
        self._ench_upgrade_btns["Weapon"] = wb
        # the level chips, after the gear chips: LV 20 / LV 25 — which cost
        # table the tab prices at (see _ench_upgrade_set_level)
        self._ench_upgrade_lvl_btns = {}
        for lv in self._ench_upgrade_levels:
            lb = QtWidgets.QPushButton(f"LV {lv}")
            lb.setCursor(QtCore.Qt.PointingHandCursor)
            lb.clicked.connect(
                lambda _=False, v=lv: self._ench_upgrade_set_level(v))
            tl.addWidget(lb)
            self._ench_upgrade_lvl_btns[lv] = lb
        tl.addStretch(1)
        ucard.layout().insertWidget(1, type_row)
        # the matrix's rarity swap — the EPIC / RARE chips choose which
        # rarity's item the marker cells open (the identical trade
        # numbers live in the chips; exactly one rarity is active, Epic
        # by default)
        self._ench_conv_rar = "epic"
        # the merged augments card — the three profession recipe lists in
        # one table (profession order), each row badged with its job ·
        # slot. Its Slot chip row narrows the long table; the stat
        # dimension is shared with the page-wide Stat chips.
        acard = QtWidgets.QWidget()
        avl = QtWidgets.QVBoxLayout(acard)
        avl.setContentsMargins(0, 0, 0, 0)
        avl.setSpacing(8)
        slots = sorted(_AUG_SLOT_COLOR)
        self._ench_aug_slot = ChoiceChips(
            [(s, s) for s in slots],
            any_label="All slots", label="Slot",
            colors=dict(_AUG_SLOT_COLOR))
        self._ench_aug_slot.currentIndexChanged.connect(self._ench_refilter)
        avl.addWidget(self._ench_aug_slot)
        self._ench_augments_lay = QtWidgets.QVBoxLayout()
        avl.addLayout(self._ench_augments_lay)
        # job + craft duration ride the card title, so the rows drop the
        # repeated job and duration
        ecard, self._ench_elixirs_lay, self._ench_elixirs_head = \
            self._ench_card("Elixirs · Alchemist · 1H")
        fcard, self._ench_foods_lay, self._ench_foods_head = \
            self._ench_card("Food · Cook · 15M")
        # the heroic Infusion System's patterns — boss-loot recipes for
        # the Infusion crucible (the 2026 patch), their own tab: they are
        # not Enchanter crafts, so they don't join the Scrolls card. No
        # SectionHeader: the tab strip already reads "Infusions", so an
        # "Infusion Patterns · heroic" title over the faction chips was a
        # second name for the thing the tab had just named
        icard, self._ench_infusions_lay, _ = self._ench_card("", header=False)
        self._ench_augments_card = acard
        self._ench_elixirs_card = ecard
        self._ench_foods_card = fcard
        self._ench_infusions_card = icard
        self._ench_scrolls_card = scard
        self._ench_gems_card = gcard
        self._ench_convs_card = ccard
        self._ench_upgrades_card = ucard
        # category tabs (mockup B) — one full-width group per tab, the
        # tabs ARE the section picker; the page opens on Gems, Scrolls last
        self._ench_tabs = C.UnderlineTabs(
            ["Gems", "Augments", "Conversions", "Elixirs", "Food",
             "Scrolls", "Infusions", "Upgrades"])
        self._ench_tabs.currentChanged.connect(self._ench_refilter)
        v.addWidget(self._ench_tabs)
        # the seven cards, one per tab — full width, shown by the active tab
        v.addWidget(scard)
        v.addWidget(gcard)
        v.addWidget(ccard)
        v.addWidget(ecard)
        v.addWidget(fcard)
        v.addWidget(icard)
        v.addWidget(acard)
        v.addWidget(ucard)
        v.addStretch(1)

        self._ench_refilter()
        return page if standalone else None

    # --- filter ---------------------------------------------------------
    def _ench_selected_stat(self) -> str:
        """The active filter stat ("" = all)."""
        return self._ench_stat.currentData() or ""

    def _ench_stat_counts(self) -> dict[str, int]:
        """How many rows on the ACTIVE tab touch each stat — the chips show
        the count ('Strength · 10'); a stat with zero rows on the page is
        hidden rather than shown as '· 0'."""
        tab = self._ench_tabs.currentText()
        out: dict[str, int] = {}

        def _grants(it: dict, s: str) -> bool:
            return any(x["n"] == s for x in (idata.own_stats(it) or []))

        def _aug_grants(it: dict, s: str) -> bool:
            if _grants(it, s):
                return True
            c = self._ench_corrupt_by_item.get(it.get("id") or "")
            return c is not None and s in (c["stat"], c["penalty"])

        for s in self._ench_stat_names:
            if tab == "Scrolls":
                n = sum(1 for ss in self._ench_scrolls if ss == s)
                n += sum(1 for c in self._ench_corrupt
                         if s in (c["stat"], c["penalty"]))
            elif tab == "Gems":
                n = sum(1 for g in self._ench_gems if s in g["stats"])
            elif tab == "Conversions":
                n = sum(1 for c in self._ench_convs
                        if s in (c["source"], c["target"]))
            elif tab == "Elixirs":
                n = sum(1 for it in self._ench_elixirs if _grants(it, s))
            elif tab == "Food":
                n = sum(1 for it in self._ench_foods if _grants(it, s))
            elif tab == "Upgrades":
                n = 0               # materials grant no stats; the tab
                                    # leaves the chips untouched (see
                                    # _ench_refilter)
            else:                                   # Augments
                n = sum(1 for it in (self._ench_enchanter
                                     + self._ench_outfitter
                                     + self._ench_blacksmith)
                        if _aug_grants(it, s))
            out[s] = n
        return out

    def _ench_refilter(self) -> None:
        stat = self._ench_selected_stat()
        q = self._ench_search.text().strip().lower()
        slot = self._ench_aug_slot.currentData() or ""
        scrolls = self._ench_scrolls if not stat else {
            s: v for s, v in self._ench_scrolls.items() if s == stat}
        corrupt = (list(self._ench_corrupt) if not stat
                   else [c for c in self._ench_corrupt
                         if stat in (c["stat"], c["penalty"])])
        gems = self._ench_gems if not stat else [
            g for g in self._ench_gems if stat in g["stats"]]
        convs = self._ench_convs if not stat else [
            c for c in self._ench_convs
            if stat in (c["source"], c["target"])]
        if q:
            # the search narrows within the picked stat: scrolls by their
            # stat name, gems/conversions by display name or stat text
            scrolls = {s: v for s, v in scrolls.items() if q in s.lower()}
            corrupt = [c for c in corrupt
                       if self._ench_corrupt_matches(c, q)]
            gems = [g for g in gems if self._ench_gem_matches(g, q)]
            convs = [c for c in convs if self._ench_conv_matches(c, q)]
        # the upgrade materials grant no stats, so the stat filter never
        # narrows them — only the search does (name / item id / rarity).
        # Inactive rarities (armor is Epic-only) stay visible as full-'—'
        # columns so the lock reads at a glance.
        active = {"Weapon": {"Rare", "Epic", "Legendary"},
                  "Armor": {"Epic"}}[self._ench_upgrade_type]
        upgrades = []
        for m in self._ench_upgrade_mats:
            if q and not self._ench_upgrade_matches(m, q):
                continue
            upgrades.append({**m, "active": m["rarity"] in active})
        # the infusion patterns grant no stats (they are crucible
        # recipes), so like the Upgrades materials only the search
        # narrows them — name, faction, role, boss or dungeon
        infusions = list(self._ench_infusions)
        if q:
            infusions = [p for p in infusions
                         if self._ench_infusion_matches(p, q)]
        # the crafting consumables joined the stat dimension once the
        # compiler stamped their granted stats — a picked stat narrows
        # them to the items that grant it, composing with the search
        def _grants(it: dict, s: str) -> bool:
            return any(x["n"] == s for x in (idata.own_stats(it) or []))

        def _aug_grants(it: dict, s: str) -> bool:
            """An augments row grants `s` via its own stats OR a corrupted
            scroll's +N / −N trade (those carry none)."""
            if _grants(it, s):
                return True
            c = self._ench_corrupt_by_item.get(it.get("id") or "")
            return c is not None and s in (c["stat"], c["penalty"])

        def _aug_slot(it: dict) -> str:
            """The slot an augment applies to — a gear slot for the
            permanent augments, TEMP for the temp-buff scrolls (which
            enchant any gear)."""
            return _AUG_SLOT.get(it.get("type") or "", "")
        augments = [it for it in (self._ench_enchanter
                                  + self._ench_outfitter
                                  + self._ench_blacksmith)
                    if (not stat or _aug_grants(it, stat))
                    and (not slot or _aug_slot(it) == slot)]
        elixirs = [it for it in self._ench_elixirs
                   if not stat or _grants(it, stat)]
        foods = [it for it in self._ench_foods
                 if not stat or _grants(it, stat)]
        if q:
            augments = [it for it in augments
                        if self._ench_food_matches(it, q)]
            elixirs = [it for it in elixirs
                       if self._ench_food_matches(it, q)]
            foods = [it for it in foods
                     if self._ench_food_matches(it, q)]

        # rebuild the card bodies
        self._clear_lay(self._ench_scrolls_lay)
        self._clear_lay(self._ench_gems_lay)
        self._clear_lay(self._ench_convs_lay)
        self._clear_lay(self._ench_upgrades_lay)
        self._clear_lay(self._ench_augments_lay)
        self._clear_lay(self._ench_elixirs_lay)
        self._clear_lay(self._ench_foods_lay)
        # the infusions card clears itself: its faction / role chip rows are
        # built once and kept, so a chip click never deletes the chip that
        # fired it (see _ench_infusion_mount)
        if scrolls or corrupt:
            # highest level first — corrupted (LV 6) lead plain (LV 1)
            rows = [(s, v) for s, v in scrolls.items()]
            rows += [(c["stat"], c) for c in corrupt]

            def _scroll_level(r: tuple) -> int:
                v = r[1]
                iid = (v["item"] if isinstance(v, dict)
                       else self._ench_scroll_items[r[0]])
                return (idata.recipe(iid) or {}).get("level") or 0
            rows.sort(key=lambda r: (-_scroll_level(r), r[0].lower(),
                                     isinstance(r[1], dict)))
            self._ench_scrolls_lay.addLayout(
                self._build_scroll_table(rows))
        else:
            self._empty_row(self._ench_scrolls_lay,
                            "No enchant scroll matches the current filters.")
        by_zone: dict[str, list[dict]] = {}
        for g in gems:
            by_zone.setdefault(g["zone"], []).append(g)
        if by_zone:
            self._ench_gems_lay.addLayout(self._build_gem_table(by_zone))
        else:
            self._empty_row(self._ench_gems_lay,
                            "No gem augment matches the current filters.")
        if convs:
            self._ench_convs_lay.addLayout(self._build_conv_table(convs))
        else:
            self._empty_row(self._ench_convs_lay,
                            "No demon-gear conversion matches the current "
                            "filters.")
        if upgrades:
            lvl = self._ench_upgrade_level
            # the whole table as data FIRST — the tiles and the matrix render
            # from it and the tests assert on it, so a restyle cannot move a
            # number. The tiles answer the total bill; the matrix keeps the
            # per-step detail under them.
            self._ench_upgrade_table = self._upgrade_matrix_data(upgrades, lvl)
            # the tab fills the page width, and it scales there instead of
            # stretching: the tiles share the width and flow their contents into
            # it, while the matrix caps its data columns and sends the surplus to
            # the gutters between the rarity blocks (see _build_upgrade_matrix)
            body = QtWidgets.QVBoxLayout()
            body.setContentsMargins(0, 0, 0, 0)
            body.setSpacing(20)
            body.addLayout(self._build_upgrade_tiles(self._ench_upgrade_table))
            body.addLayout(self._build_upgrade_matrix(self._ench_upgrade_table))
            self._ench_upgrades_lay.addLayout(body)
        else:
            self._ench_upgrade_table = None
            self._empty_row(self._ench_upgrades_lay,
                            "No upgrade material matches the current "
                            "filters.")
        self._ench_upgrade_style_btns()
        if elixirs:
            # each card is ONE scrolls-style table — name · LV · grant,
            # rows tinted like the scrolls (job + duration ride the
            # title; a row whose duration differs shows its own tag)
            self._ench_elixirs_lay.addLayout(self._build_consumable_table(
                sorted(elixirs, key=self._ench_food_key), "1H"))
        else:
            self._empty_row(self._ench_elixirs_lay,
                            "No crafting elixir matches the current filters.")
        if foods:
            self._ench_foods_lay.addLayout(self._build_consumable_table(
                sorted(foods, key=self._ench_food_key), "15M"))
        else:
            self._empty_row(self._ench_foods_lay,
                            "No crafted food matches the current filters.")
        if infusions:
            self._ench_infusion_mount(self._ench_infusions_lay, infusions)
        else:
            self._clear_lay(self._ench_infusions_lay)
            self._empty_row(self._ench_infusions_lay,
                            "No infusion pattern matches the current "
                            "filters.")
        # the augments card is ONE table — rows in the profession order
        # (Enchanter → Outfitter → Blacksmith, highest level first); its
        # STATS column takes the leftover width
        if augments:
            self._ench_augments_lay.addLayout(
                self._build_aug_table(augments))
        else:
            self._empty_row(self._ench_augments_lay,
                            "No craftable augment matches the current "
                            "filters.")

        # the Stat chips show how many rows on the active tab touch each
        # stat, so a pick always lands on something; a stat with nothing
        # on the tab is HIDDEN (no '· 0' clutter). The active pick stays
        # visible even at '· 0' — a gear page's ENCHANTS chip jumps here
        # pre-filtered and the pick must survive landing on a tab with
        # no rows for it (the user then flips to the right tab). The
        # Upgrades tab grants no stats, so it leaves the chips exactly as
        # the previous tab left them instead of hiding everything.
        tab = self._ench_tabs.currentText()
        if tab not in ("Upgrades", "Infusions"):
            counts = self._ench_stat_counts()
            self._ench_stat.set_option_labels(
                {s: f"{s} · {n}" for s, n in counts.items()})
            sel = self._ench_selected_stat()
            self._ench_stat.set_visible_options(
                {s for s, n in counts.items() if n}
                | ({sel} if sel else set()))

        # the active tab shows its card — the stat filter applies
        # everywhere except Upgrades (materials grant no stats)
        for key, card in (("Scrolls", self._ench_scrolls_card),
                          ("Gems", self._ench_gems_card),
                          ("Conversions", self._ench_convs_card),
                          ("Upgrades", self._ench_upgrades_card),
                          ("Elixirs", self._ench_elixirs_card),
                          ("Food", self._ench_foods_card),
                          ("Infusions", self._ench_infusions_card),
                          ("Augments", self._ench_augments_card)):
            card.setVisible(tab == key)

        # the filtered totals — the old top count tag ('OFFLINE · N
        # SCROLLS …') is gone per the UI cleanup, but the numbers stay
        # available on the page for tests and future UI
        self._ench_counts = {
            "SCROLLS": len(scrolls) + len(corrupt),
            "GEMS": len(gems),
            "CONVERSIONS": len(convs),
            "UPGRADES": len(upgrades),
            "INFUSIONS": len(infusions),
        }

    def _ench_conv_set_rar(self, key: str) -> None:
        """An EPIC / RARE matrix chip was clicked — swap the matrix to
        that rarity (exactly one is active at a time)."""
        if self._ench_conv_rar == key:
            return
        self._ench_conv_rar = key
        self._ench_refilter()

    # --- search matching ------------------------------------------------
    def _ench_corrupt_matches(self, c: dict, q: str) -> bool:
        """Search-query match for one corrupted scroll: its name or the
        Vitality penalty it drains."""
        hay = f"corrupted {c['stat']} {c['penalty']}"
        return q in hay.lower()

    def _ench_gem_matches(self, g: dict, q: str) -> bool:
        """Search-query match for one gem augment: the gem's display name
        or any stat it grants."""
        name = ((idata.item(g["item"]) or {}).get("name") or g["item"]).lower()
        return q in name or any(q in s.lower() for s in g["stats"])

    def _ench_upgrade_matches(self, m: dict, q: str) -> bool:
        """Search-query match for one upgrade material: its display name,
        item id, or served rarity ('crystal', 'epic', 'shard', ...)."""
        hay = (f"{m.get('name') or ''} {m.get('item') or ''} "
               f"{m.get('rarity') or ''}")
        return q in hay.lower()

    def _ench_upgrade_set_type(self, key: str) -> None:
        """The WEAPON filter chip was clicked — rebuild the matrix."""
        if self._ench_upgrade_type == key:
            return
        self._ench_upgrade_type = key
        self._ench_refilter()

    def _ench_upgrade_set_level(self, lv: int) -> None:
        """A LV chip was clicked — reprice the table at that level."""
        if self._ench_upgrade_level == lv:
            return
        self._ench_upgrade_level = lv
        self._ench_refilter()

    def _ench_upgrade_style_btns(self) -> None:
        """Restyle the WEAPON and LV chips for the active gear + level."""
        mono = f'font-family:"{theme.MONO_FONT}","Consolas";'
        for btns, active_key in ((self._ench_upgrade_btns,
                                  self._ench_upgrade_type),
                                 (self._ench_upgrade_lvl_btns,
                                  self._ench_upgrade_level)):
            for key, b in btns.items():
                if key == active_key:
                    col = theme.ACCENT
                    b.setStyleSheet(
                        f"QPushButton{{color:{col};"
                        f"background:{theme.with_alpha(col, 45)};"
                        f"border:1px solid {theme.with_alpha(col, 90)};"
                        f"border-radius:3px;padding:2px 8px;font-size:11px;"
                        f"font-weight:700;letter-spacing:1px;{mono}}}"
                        f"QPushButton:hover{{color:{theme.ACCENT};}}")
                else:
                    b.setStyleSheet(
                        f"QPushButton{{color:{theme.DIM};background:transparent;"
                        f"border:1px solid {theme.BORDER};border-radius:3px;"
                        f"padding:2px 8px;font-size:11px;font-weight:700;"
                        f"letter-spacing:1px;{mono}}}"
                        f"QPushButton:hover{{color:{theme.ACCENT};}}")

    def _ench_conv_matches(self, c: dict, q: str) -> bool:
        """Search-query match for one conversion: a traded stat, or the
        Rare/Epic item's name/id (the id search matches its shortened
        suffix — 'crittoap' → the Crit→A.Pen rows)."""
        if q in c["source"].lower() or q in c["target"].lower():
            return True
        return any(
            q in ((idata.item(c[k]["item"]) or {}).get("name")
                  or c[k]["item"]).lower()
            or q in c[k]["item"].lower()
            for k in ("rare", "epic") if c.get(k))

