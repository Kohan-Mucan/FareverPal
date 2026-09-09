"""Party page: click a player name to inspect live buffs + equipped gear.

Left: every hero in the loaded scene (+ far-away group members via the
replicated st.Group roster). Middle: the paper doll (worn | carried) with the
selected piece, live buffs and gear notes stacking under the carried column,
read by core/inspect.py out of the hero's st.Loadout (see hero_gear there for why
a slot's item sits behind a per-slot record, and hero_pet for why the pet is a
String on the loadout rather than one of the game's 30 slots). Read-only; an empty section means
the read didn't verify, never fabricated rows. Auto-refreshes ~every 2 s
while visible.
"""
from __future__ import annotations

import logging
import threading
import time

from PySide6 import QtCore, QtWidgets

from .. import theme

from .party_profile import PartyProfileMixin
from .party_rows import (_buff_label, _buff_tail, _gear_slot_labels,  # noqa: F401  (re-exported: tests import it through here)
                         _live_pet)

logger = logging.getLogger("farever_companion")

# How often the party roster (the far-away group members) is re-decoded while
# the page is up. The roster only changes on join/leave, and the decode is the
# one read too slow to do on the UI thread (see _warm_party_roster).
_ROSTER_WARM_S = 20.0

# TEMP DEMO: removed 2026-09-26. Detached (no game model), the page used to
# fill itself with sample data through the real renderers so the layout could
# be reviewed with every value populated — five invented heroes, a full set of
# invented gear, invented buffs. It was the fastest way to review a layout and
# the easiest way to ship a lie: a detached window showed a page full of items
# and names that were in no sheet, which is exactly the "never fabricate rows"
# rule this file is built on. Detached now draws what it has: nothing.

# Icon sizes on this page (2026-09-26): every sprite on Player Inspect went up
# 2px — the buff/gear-note glyphs read a touch small beside the text they sit
# next to. Named so the test that pins the buff sprite resolves the same size
# the row does. 2026-09-26 (later): +2px AGAIN, with the fonts, so a glyph is
# never smaller than the type it annotates.
_BUFF_ICON_PX = 22
_GEAR_ICON_PX = 24


def _party_sprite_tile(sheet: str, sid: str, size: int):
    """A sheet sprite on the app's STANDARD accent tile — the same
    `icons.tile` square the loot table and both HUDs use for every row's
    leading icon, rather than the bare pixmap.

    Why the bare pixmap read as "washed out" (2026-09-26, the Water Infusion
    buff row). An atlas cell is fully OPAQUE: the game's sprites carry their
    own painted background, and a third of the skills sheet is dark. Measured
    on `Shield_OrbitWater_P_StatusBlockBuffed` at the row's 22px, the cell's
    corner is RGB(10,25,59) — luma 23 against this card's PANEL #1b2024 at
    luma 32. So the sprite had no edge to read against: a near-invisible dark
    square holding a few dim strokes, which is what "washed" is. Only 5% of
    that cell is bright at all, and it is not an isolated case — 217 of the
    652 skill cells average under luma 90.

    The tile is the remedy this codebase already reached for on the paper
    doll, where the note reads "the dark consumable artwork had nothing to
    read against": an accent fill plus a 1px accent border at 55% gives the
    sprite a defined frame whatever its own background is, and `trim=True`
    scales sprites that float small in their cell up to fill it. The accent
    is the same ACCENT the no-sprite fallback glyph beside it is drawn in, so
    an icon looks the same either way.
    """
    from ...data import icons as gicons
    return gicons.tile(sheet, sid, size, theme.ACCENT, trim=True)


class PartyPageMixin(PartyProfileMixin):
    def _page_party(self) -> QtWidgets.QWidget:
        """Tabbed Inspector: roster strip + tabs below.

        One clean page, no tab bar — the combined layout from the mockup
        (ai/workspace/opencode/mockups/player_page_mockups.html, "B ★"):
        roster cards | paper-doll grid (Companion box above Glider) + piece,
        with buffs + gear notes under the carried column. Buffs, Pet,
        Equipment and Combat all live here; standalone tabs for them were
        dupes. Caption bars are gone (2026-09-25): PLAYERS · LIVE SCENE,
        PAPER DOLL · CLICK A SLOT and BUFFS · LIVE STATUS restated what the
        content itself already says, so the columns open straight with the
        cards/tiles/rows. The hero strip above the grid is gone too
        (2026-09-18) and the header bar (title + Refresh + Diag) left the
        same day as the captions — same reasoning.
        """
        page, root = self._page_container()
        # The two-column body lives in one content column, so its width can be
        # capped as a whole (see `_party_content_width`).
        content = QtWidgets.QWidget()
        croot = QtWidgets.QVBoxLayout(content)
        croot.setContentsMargins(0, 0, 0, 0)
        croot.setSpacing(16)
        self._party_content = content
        self._party_selected_addr: int = 0
        # the roster cache + the last warm attempt (see _party_roster_cached)
        self._party_roster = None
        self._party_roster_busy = False
        self._party_roster_at: float = 0.0

        # --- Overview: roster | paper-doll (buffs stack under carried) ----
        ov = QtWidgets.QWidget()
        body = QtWidgets.QHBoxLayout(ov)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(12)

        left = QtWidgets.QVBoxLayout()
        left.setSpacing(6)
        # no margins on the column wrappers (2026-09-26): a QVBoxLayout inherits
        # the style's default 9px, and the roster's wrapper was one level
        # shallower than the doll's, so the three top-row blocks sat 9px out
        # of alignment with each other. Every wrapper on the page is now
        # margin-free, so the cards' own padding decides the alignment.
        left.setContentsMargins(0, 0, 0, 0)
        # The roster is a BLOCK now (2026-09-26): the same Card + quiet
        # caption the detail column's three readouts use, so the page is four
        # blocks of one family instead of a bare column of cards next to three
        # framed ones. The hero cards keep their own frames — the selected
        # hero's accent border and the hover state live there.
        players = QtWidgets.QFrame()
        players.setObjectName("Card")
        players.setStyleSheet(
            f"QFrame#Card{{background:{theme.PANEL};border:1px solid "
            f"{theme.BORDER};border-radius:6px;}}")
        pl = QtWidgets.QVBoxLayout(players)
        pl.setContentsMargins(8, 6, 8, 6)
        pl.setSpacing(4)
        pcap = QtWidgets.QLabel("Players")
        pcap.setStyleSheet(
            f"font-size: 16px; font-weight: 600; color: {theme.MUTED};")
        pl.addWidget(pcap)
        self._party_players_cap = pcap
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        # Vertical only: the column is 240–300px by design and the card
        # labels elide, so the list scrolls down rather than ever sideways.
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        roster_host = QtWidgets.QWidget()
        self._party_roster_body = QtWidgets.QVBoxLayout(roster_host)
        self._party_roster_body.setSpacing(6)
        self._party_roster_body.setContentsMargins(0, 0, 0, 0)
        scroll.setWidget(roster_host)
        pl.addWidget(scroll, 1)
        # The block HUGS its roster (2026-09-26): stretched to the column's
        # height it was a big framed box with a pool of empty card under three
        # heroes. Its own maximum height is set per refresh from the roster it
        # drew (`_party_roster_fill`), so a long party still scrolls inside.
        left.addWidget(players, 0, QtCore.Qt.AlignTop)
        self._party_players_card = players
        # compat: the 2 s timer guards on _party_list being alive
        self._party_list = roster_host
        left_wrap = QtWidgets.QWidget()
        left_wrap.setLayout(left)
        left_wrap.setMinimumWidth(240)
        left_wrap.setMaximumWidth(300)
        # the base the "+5 characters" is added to (2026-09-26) — stored so
        # _party_roster_width can re-derive the total every fill instead of
        # growing the column by 5 characters every 2 seconds
        self._party_roster_base_w = 240
        self._party_roster_wrap = left_wrap
        body.addWidget(left_wrap, 0)

        mid = QtWidgets.QVBoxLayout()
        mid.setSpacing(8)
        mid.setContentsMargins(0, 0, 0, 0)     # see `left` above
        # no Bags & Bank section here, by design: a party member's inventory is
        # not replicated to the client, so the only character whose bags this
        # process could ever read is your own — and the collection page is the
        # place that lists items. Reading the bags here bought nothing.
        self._party_profile_build(mid)
        mid.addStretch(1)
        mid_wrap = QtWidgets.QWidget()
        mid_wrap.setLayout(mid)
        self._party_doll_wrap = mid_wrap
        body.addWidget(mid_wrap, 0)

        # No column takes the slack any more. With a stretch factor the doll
        # column absorbed every spare pixel, and QGridLayout passed it on to
        # the paper doll's columns — the tiles stayed 64px while the gaps
        # between them grew from 25px at a 1280px window to 105px at 1920.
        # The spacer keeps the two columns at their own widths and parks the
        # slack outside them.
        self._party_body = body
        body.addStretch(1)

        croot.addWidget(ov, 1)
        root.addWidget(content, 0, QtCore.Qt.AlignLeft)

        QtCore.QTimer.singleShot(0, self._party_content_width)
        self._party_timer = QtCore.QTimer(self)
        self._party_timer.setInterval(2000)
        self._party_timer.timeout.connect(self._refresh_party_all)
        self._party_timer.start()

        QtCore.QTimer.singleShot(0, self._refresh_party_all)
        return page

    def _party_content_width(self) -> None:
        """Cap the content column at the width its own two columns need.

        Deliberately not a hand-picked number: the roster and the paper doll
        each report the width they need, and the cap is those two plus the
        spacing between them. A wider doll grid therefore moves the cap with
        it and nothing needs re-tuning —
        which is the point, since the number a person would guess (1180 in the
        mockup) is not a fact about the page.

        "The width they need" is `max(sizeHint, explicit minimum)`: the roster is
        clamped to a 240px minimum while its hint is smaller, so taking the hint
        alone would hand the doll ~36px less than its own hint and squeeze the
        grid's spacer columns (measured: 22px gaps collapsing to 11px).

        Wider than this, the extra space is margin; narrower, the columns keep
        their minimums and the middle one shrinks — the case the doll's fixed
        64px tiles already define.
        """
        content = getattr(self, "_party_content", None)
        body = getattr(self, "_party_body", None)
        wraps = [getattr(self, "_party_roster_wrap", None),
                 getattr(self, "_party_doll_wrap", None)]
        if body is None or any(not self._widget_alive(w) for w in wraps):
            return
        if not self._widget_alive(content):
            return
        want = sum(max(w.sizeHint().width(), w.minimumWidth()) for w in wraps)
        want += body.spacing() * (len(wraps) - 1)
        if want > 0:
            content.setMaximumWidth(want)

    # --- selection + tabs --------------------------------------------------
    def _party_pick_addr(self, addr: int, name: str = "") -> None:
        # `name` stays in the signature (the roster card passes it) but is
        # unread since the identity strip / headers went away — the selected
        # card highlights itself, which is identity enough.
        self._party_selected_addr = int(addr or 0)
        # The highlight moves with the CLICK, not with the next 2s tick
        # (2026-09-26). Nothing else styled the selection, so a click used to
        # repaint the whole detail column instantly while the card you had just
        # clicked stayed plain — the accent sat on the PREVIOUS hero for up to
        # two seconds while you read the new one's stats, which made a fast,
        # correct switch look broken. Re-styling two cards costs nothing next
        # to the read and the fill that follow.
        self._party_roster_select(self._party_selected_addr)
        self._refresh_party_detail()

    # --- data ------------------------------------------------------------
    def _party_heroes(self) -> list[tuple[int, str, str]]:
        """(addr, display name, class) for inspectable heroes + group members."""
        model = getattr(self, "model", None)
        if model is None:
            return []
        out: list[tuple[int, str, str]] = []
        seen: set[int] = set()
        try:
            units = model.units() or []
        except Exception:
            units = []
        me = getattr(model, "player_addr", None)
        for u in units:
            if not getattr(u, "is_hero", False):
                continue
            addr = getattr(u, "addr", 0)
            if not addr or addr in seen:
                continue
            seen.add(addr)
            try:
                name = model.player_name(addr) or ""
            except Exception:
                name = ""
            if not name:
                try:
                    name = model.hero_display_name(u) or ""
                except Exception:
                    name = getattr(u, "unit_id", None) or "Hero"
            if me and addr == me:
                name = f"{name} (You)" if name else "You"
            cls = getattr(u, "hero_class", "") or ""
            out.append((addr, name or "Hero", cls))
        # far-away group members (have a hero ptr even outside the scene). The
        # roster comes from the CACHE — decoding it is a heap sweep measured at
        # 6 s cold (and ~2.4 s per empty remap), so the page never starts one
        # while the user waits: it reads whatever the background warm has, and
        # asks for a fresh decode only every _ROSTER_WARM_S.
        members = getattr(self._party_roster_cached(model), "members", None) or []
        for m in members:
            h = getattr(m, "hero", 0) or 0
            if not h or h in seen:
                continue
            seen.add(h)
            nm = getattr(m, "name", "") or "Hero"
            if getattr(m, "is_me", False):
                nm = f"{nm} (You)"
            try:
                cls = model.hero_class_of(h) or ""
            except Exception:
                cls = ""
            out.append((h, nm, cls))
        # you first, then alphabetical
        out.sort(key=lambda t: (("(You)" not in t[1]), t[1].lower()))
        return out

    def _party_roster_cached(self, model):
        """The party roster, from the model's cache only — never a live decode.

        The roster decode is the one read here that is measured in SECONDS: a
        cold first decode sweeps the heap (~6 s) and, while no group decodes at
        all (solo), the reader remaps for ~2.4 s. Running that from the page's
        2 s timer is what made this page hang when it opened. So the page reads
        the cache and kicks a background decode at most every _ROSTER_WARM_S;
        the members list is best-effort (the scene is the real source), so a
        late roster is fine and a missing one is not an error.
        """
        snap = getattr(self, "_party_roster", None)
        if snap is None:
            # something else may already have decoded it (the DPS tracker and the
            # meter HUD poll the same reader) — reuse that before paying for one
            cached = getattr(model, "cached_group_roster", None)
            if callable(cached):
                try:
                    snap = cached()
                except Exception:
                    snap = None
        if not getattr(self, "_party_roster_busy", False) \
                and time.monotonic() - getattr(self, "_party_roster_at", 0.0) \
                > _ROSTER_WARM_S:
            self._warm_party_roster(model)
        return snap

    def _warm_party_roster(self, model) -> None:
        """Decode the roster on a one-shot background thread.

        A plain daemon thread, not a QThread, on purpose: this is
        fire-and-forget — the next tick picks up whatever it stored — so nothing
        here may be tied to a widget's lifetime. A QThread owned by a page can
        outlive the page (and, in tests, the panel that built it), and a QThread
        destroyed while its thread still runs takes the process with it. Same
        pattern as the speedrun overlay's kill-log writes.
        """
        read = getattr(model, "group_roster", None)
        if not callable(read):
            return
        self._party_roster_at = time.monotonic()
        self._party_roster_busy = True

        def work() -> None:
            try:
                snap = read()
            except Exception:                    # keep the cached roster as-is
                logger.exception("party roster warm failed")
            else:
                self._party_roster = snap        # the next tick lists it
            self._party_roster_busy = False

        try:
            threading.Thread(target=work, daemon=True,
                             name="party-roster").start()
        except Exception:
            self._party_roster_busy = False
            logger.exception("party roster warm failed to start")

    def _refresh_party_all(self) -> None:
        if not self._widget_alive(getattr(self, "_party_list", None)):
            timer = getattr(self, "_party_timer", None)
            if timer is not None:
                timer.stop()
            return
        # only poll while the party page is showing
        try:
            cur = self._current_page() or ""
        except Exception:
            cur = ""
        if not cur.startswith("party"):
            return
        heroes = self._party_heroes()
        prev = self._party_selected_addr
        if prev and any(a == prev for a, _, _ in heroes):
            pass  # keep selection
        elif heroes:
            self._party_selected_addr = heroes[0][0]
        elif not heroes:
            # An EMPTY read is not a deselection (2026-09-26). This used to set
            # the selection to 0, and one empty poll therefore painted "No
            # player selected." over a detail column that had a real hero in
            # it a moment ago, deleted every roster card, and then — when the
            # heroes came back — jumped the selection to whoever sorted first
            # instead of the hero the user was reading. A walk that returns
            # nothing for one tick is a walk that failed: a zone change, a
            # load, or the roster remap this page already knows costs seconds.
            # The game reporting "no heroes here" is a claim; the game
            # reporting nothing at all is not, and the page never invents one.
            #
            # So: keep the selection, keep the cards, and skip the rebuild
            # entirely. The next tick that DOES see the heroes reconciles it
            # through the two branches above, which is where a real change of
            # party is handled.
            return
        self._party_roster_fill(heroes)
        self._refresh_party_detail()

    def _refresh_party_detail(self) -> None:
        if not self._widget_alive(getattr(self, "_party_buffs", None)):
            return
        from ...core import inspect as hero_inspect
        from ...data.items.labels import equip_slot_label, is_bag_slot
        model = getattr(self, "model", None)
        addr = getattr(self, "_party_selected_addr", 0) or 0
        if not addr or model is None:
            # blank the grid (nothing selected = nothing to show). The spec
            # goes too: this branch owns the layouts' contents, so a later
            # tick that matched the old spec would skip the rebuild and leave
            # these placeholders standing.
            self._party_rows_spec = None
            self._party_rows_reset(self._party_buffs)
            self._party_rows_reset(self._party_gear)
            # The two row areas are now left EMPTY rather than carrying a
            # "No player selected." line (2026-09-26). The resets above are
            # the load-bearing part — they are what stops a stale buff or
            # gear row surviving the deselect — and the line only ever
            # described a state the blank layout already shows.
            self._party_profile_fill([])
            self._party_pet_fill("", None)
            return
        # live reads
        try:
            buffs = hero_inspect.hero_buffs(model.proc, model.hl, addr)
        except Exception:
            buffs = []
        try:
            # include_empty: the game's array index is its own slot order, so a
            # slot it reports as empty can still be NAMED (a shield goes in the
            # offhand, index 2) — omitting it left a piece looking absent
            gear = hero_inspect.hero_gear(model.proc, model.hl, addr,
                                          include_empty=True)
        except Exception:
            gear = []
        filled = [g for g in gear if g.get("state", "filled") == "filled"]
        # bag slots (Slot_Bag1-6) are skipped outright: they hold containers
        # (live: `Bag_Z2` in each), a bag's contents are per-character and not
        # wanted here — no row, no summary line.
        filled = [g for g in filled if not is_bag_slot(g.get("slot"))]
        not_read = [g for g in gear if g.get("state") == "unreadable"]
        # The pet's "no pet" note belongs to the gear rows, so it is part of
        # what this fill draws (2026-09-26: _fill_party_pet RETURNS it instead
        # of appending to the layout, so the one guarded rebuild below can
        # skip the lot without the note leaking a second copy every tick).
        pet_note = self._fill_party_pet(model, addr)
        # The buff + gear rows are rebuilt ONLY when their content actually
        # changes (2026-09-26). They used to be destroyed and re-created on
        # every 2s tick, which re-drew every status row and every NOT READ line
        # forever even when the hero's statuses never changed. `_buff_tail` is
        # deliberately free of a countdown (see its docstring), so a live
        # hero's buff list is stable between real changes and this skips
        # almost always.
        spec = (
            addr,
            tuple((b.get("kind") or "?", _buff_label(b), _buff_tail(b),
                   b.get("source_item")) for b in buffs),
            tuple((equip_slot_label(g.get("slot")) or f"Slot {g.get('slot')}",
                   g.get("cls") or "") for g in not_read),
            pet_note,
        )
        if spec == getattr(self, "_party_rows_spec", None):
            self._party_profile_fill(gear)
            self._party_content_width()
            return
        self._party_rows_spec = spec
        # The buff rows are diffed, not rebuilt (2026-09-26): a status coming
        # and going is an every-few-seconds event, and rebuilding the whole
        # list for it re-drew (and re-laid-out) every other status row too —
        # the same flash as the roster, one notch smaller. Each row is keyed by
        # the status it shows, so a row whose status is unchanged is only
        # re-texted, and one that is gone is the only thing destroyed.
        self._party_buffs_reset()
        for b in buffs:
            kind = b.get("kind") or "?"
            # named through the live row, so a SHARED status ('ItemStatus', every
            # elixir/food/potion) is named after the item that actually granted
            # it instead of one arbitrary sheet item — see _buff_label
            nm = _buff_label(b)
            tail = _buff_tail(b)
            row = self._party_buff_take(kind)
            if row is None:
                self._party_buffs.addWidget(self._party_buff_row(
                    kind, nm, tail, source_item=b.get("source_item")))
            else:
                self._party_row_set_name(row, nm)
                self._party_row_set_tail(row, tail)
            # The raw status id used to be printed UNDER every buff as a dim
            # mono second line ('ItemStatus', 'Axe_Boomerang_Skill_Passive_
            # Status', 'SomeExtremelyLongBuffStatusNameThatKeepsGoing'). It is
            # the sheet's key, not information about the hero: the row above
            # already names the status through the sheet, and the id is the
            # row's tooltip. Removed, not hidden (2026-09-26).
        self._party_buffs_drop_left()
        # the gear rows are few and their text is a per-hero constant, so they
        # still rebuild wholesale on a change (the spec above is what makes
        # that rare)
        self._party_rows_reset(self._party_gear)
        # the per-piece text rows are gone (the equipment grid shows the
        # pieces); what stays: NOT READ warnings below
        # the slots the game reports with NO item, so nothing can look absent by
        # omission: a live entry holding no item is called out as NOT READ (that
        # is a reader limit, not an empty slot), and the null slots are counted
        # and named with the game's own slot names
        for g in not_read:
            lbl = equip_slot_label(g.get("slot")) or f"Slot {g.get('slot')}"
            self._party_gear.addWidget(self._party_empty(
                f"{lbl} (live slot {g.get('slot')}) — NOT READ: the entry holds "
                f"no item this reader can unwrap ({g.get('cls') or 'no class'})"))
        # the game's EMPTY slots are not listed: nobody needs "Job Tool, Bag 6
        # are empty" — a piece you own is visible above, and an empty slot is
        # the game's own default state, not information. (The NOT READ lines
        # above stay: those are reader limits, which ARE worth knowing.)
        # The pet is the same case: no row for the creature (the grid's
        # Companion tile shows it) and no "No pet equipped." either — that was
        # the game's own answer restated in a different column. Only a loadout
        # with no pet FIELD is a reader limit, and that note still comes
        # through, because "I could not read it" is not the same as "there is
        # nothing there".
        if pet_note:
            self._party_gear.addWidget(self._party_empty(pet_note))
        self._party_profile_fill(gear)
        # the columns' own widths move with their content (a longer buff name,
        # the selected-piece panel), so re-derive the cap after a fill
        self._party_content_width()

    def _party_buffs_reset(self) -> None:
        """Start a buff-row diff: the rows currently in the block become the
        pool a later `_party_buff_take` claims from (2026-09-26)."""
        self._party_buff_pool = [
            self._party_buffs.itemAt(i).widget()
            for i in range(self._party_buffs.count())
            if self._party_buffs.itemAt(i).widget() is not None
            and hasattr(self._party_buffs.itemAt(i).widget(), "_party_kind")
        ]

    def _party_buff_take(self, kind: str) -> QtWidgets.QWidget | None:
        """Claim the pooled row already showing `kind`, or None to build a new
        one. The row is re-parented into the block's own order as it is
        claimed, so the block ends up in the live buff order either way."""
        pool = getattr(self, "_party_buff_pool", None)
        if not pool:
            return None
        for i, row in enumerate(pool):
            if row is not None and row._party_kind == kind:   # noqa: SLF001
                pool.pop(i)
                self._party_buffs.addWidget(row)
                return row
        return None

    def _party_buffs_drop_left(self) -> None:
        """Destroy whatever the diff did not claim — statuses that fell off.
        Cleared AFTER the loop, so a row is never orphaned while it is still a
        candidate."""
        for row in getattr(self, "_party_buff_pool", None) or []:
            if row is not None:
                self._party_buffs.removeWidget(row)
                row.setParent(None)
                row.deleteLater()
        self._party_buff_pool = []

    @staticmethod
    def _party_rows_reset(lay: QtWidgets.QLayout) -> None:
        """Empty a row layout, orphaning every widget so the next fill owns a
        clean slate (2026-09-26). `deleteLater` alone leaves the widget a child
        of its parent, where `findChildren` still finds it until the event
        loop delivers the delete — the ghost-row trap."""
        while lay.count():
            it = lay.takeAt(0)
            w = it.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()

    def _fill_party_section(self, filler, model, addr: int, label: str) -> None:
        """Render one section, and if the render itself blows up, SAY SO in the
        section instead of letting the exception escape the timer slot.

        Without this a section that raises on one live row prints a traceback
        every 2 s from the timer and leaves the section silently half-drawn
        (which is how a single missing dict key looked like a slow, broken page).
        The read side already guards itself this way; this is the render side.
        """
        try:
            filler(model, addr)
        except Exception as e:                       # noqa: BLE001 — see above
            logger.exception("party section %s failed", label)
            self._party_section_error(label, repr(e))

    def _fill_party_pet(self, model, addr: int) -> str | None:
        """The Companion tile: the pet the hero has equipped, plus a note ONLY
        when the read itself is the problem.

        `hero_pet` reads st.Loadout.pet (the game keeps no pet slot — see
        core.inspect.hero_pet), and the id it returns is joined to the live
        scene entity owned by this hero so the creature's own class and level
        come from the game. There is deliberately NO gear-list row for it: the
        grid's Companion tile already shows the pet, and a second row with the
        same creature is a dupe.

        "No pet equipped." used to be returned here too (2026-09-26, removed
        the same day). It is the game's own default state, which is exactly
        what the empty-slot rule above says is not information — and the
        Companion tile already draws an empty slot the same dim way as every
        other empty slot, so the line restated the doll in a different column
        and broke a rule printed three lines above it. What DOES stay is the
        reader limit: a loadout that exposes no pet field is not the same
        fact as a loadout with no pet in it, and only one of them is worth
        saying out loud.

        RETURNS that note rather than appending it (2026-09-26): the caller
        owns the one guarded rebuild of the gear rows, and a note written
        straight into the layout would slip past the "nothing changed, skip
        the rebuild" check and pile up a copy every tick.
        """
        from ...core import inspect as hero_inspect
        try:
            pet = hero_inspect.hero_pet(model.proc, model.hl, addr)
        except Exception:
            pet = {}
        pet_id = pet.get("id")
        if not pet_id:
            self._party_pet_fill("", None)
            return (None if pet.get("off") else
                    "No pet field on this hero's loadout — reader limit.")
        live = _live_pet(model, addr, pet_id)
        self._party_pet_fill(pet_id, live)
        return None

    @staticmethod
    def _party_buff_row(kind: str, name: str, tail: str,
                        source_item: str | None = None) -> QtWidgets.QWidget:
        """One buff row: the real atlas icon (the buff's source item, e.g.
        Whetstone_Status -> the Whetstone item sprite; for a SHARED status, the
        live item that granted it — the sheet's own answer for 'ItemStatus'
        would be one arbitrary item's sprite, the same mistake the name made;
        then the buff id on the skills sheet) + the readable name, with the
        parenthetical (stacks, granting item) on its OWN line under the name
        (2026-09-26) — run together they read as one long sentence ("Bloodrage
        Aura  (from Cheese Moon)") and wrapped at the card's edge, which is
        what put the source on a half line of its own.        The name is one line
        too (elided): "Some Extremely Long Buff Status Name That Keeps Going"
        used to wrap over three and ate three rows of the pinned Buffs block.
        Falls back to a glyph when the id has no sprite.

        The row is a small Card of its own (2026-09-26), the same treatment the
        equipment tiles and the stat blocks already get: bare icon-plus-text
        lines inside the Buffs card read as a paragraph while everything
        beside them was a bordered block, so the one list the eye scanned was
        the one with no structure. The frame is the SAME 8/6 padding and
        PANEL/BORDER paint as every other card, so a buff now stacks the way
        the tiles do."""
        from ...data import icons as gicons
        from ...data import names as gnames
        from .party_profile import _ElideLabel
        wrap = QtWidgets.QFrame()
        wrap.setObjectName("Card")
        wrap.setStyleSheet(
            f"QFrame#Card{{background:{theme.PANEL};border:1px solid "
            f"{theme.BORDER};border-radius:6px;}}")
        lay = QtWidgets.QHBoxLayout(wrap)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(8)
        col = QtWidgets.QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(1)
        lbl = _ElideLabel(name)
        lbl.setStyleSheet(f"color:{theme.TEXT};font-size:16px;")
        col.addWidget(lbl)
        sub = None
        if tail.strip():
            sub = _ElideLabel(tail.strip())
            sub.setStyleSheet(f"color:{theme.MUTED};font-size:14px;")
            col.addWidget(sub)
        icon = QtWidgets.QLabel()
        icon.setFixedSize(_BUFF_ICON_PX, _BUFF_ICON_PX)
        # resolve a sprite: source item first (food/potion buffs), then the
        # buff id itself on the skills sheet
        src_item = gnames.status_source_item(kind)
        if src_item is None and gnames.status_is_shared(kind):
            src_item = source_item
        sid = sheet = None
        for sh, si in (("item", src_item), ("skills", kind)):
            if si and gicons.has_icon(sh, si):
                sheet, sid = sh, si
                break
        if sid:
            icon.setPixmap(_party_sprite_tile(sheet, sid, _BUFF_ICON_PX))
        else:
            icon.setPixmap(gicons.emoji_pixmap("✨", _BUFF_ICON_PX,
                                               theme.ACCENT))
        lay.addWidget(icon, 0, QtCore.Qt.AlignVCenter)
        lay.addLayout(col, 1)
        # No tooltip: the raw status id is the SHEET's key, not information
        # about the hero, and it is not printed under the row either (see the
        # refresh loop). With hover popups gone page-wide (2026-09-26) it is
        # simply not on this page — the name above is the readable half.
        # what a later fill needs to update this row IN PLACE (2026-09-26):
        # the row is keyed by the status it shows, and only the parenthetical
        # can change under a status that is still the same status.
        wrap._party_kind = kind                        # type: ignore[attr-defined]
        wrap._party_name_lbl = lbl                     # type: ignore[attr-defined]
        wrap._party_sub_lbl = sub                      # type: ignore[attr-defined]
        # the text column, held directly: it used to be reached as
        # `row.layout().itemAt(1).layout()`, and the Card above changed the
        # row's child structure — index 1 is no longer the text layout, so the
        # positional read had to go rather than be re-pointed
        wrap._party_text_col = col                     # type: ignore[attr-defined]
        return wrap

    @staticmethod
    def _party_row_set_tail(row: QtWidgets.QWidget, tail: str) -> None:
        """Update a buff row's parenthetical line in place, adding or removing
        the line when the status itself stops (or starts) saying anything
        (2026-09-26)."""
        sub = getattr(row, "_party_sub_lbl", None)
        col = getattr(row, "_party_text_col", None)
        if col is None:                                # not our row
            return
        if not tail.strip():
            if sub is not None:
                col.removeWidget(sub)
                sub.setParent(None)
                sub.deleteLater()
                row._party_sub_lbl = None              # type: ignore[attr-defined]
            return
        if sub is None:
            from .party_profile import _ElideLabel
            sub = _ElideLabel(tail.strip())
            sub.setStyleSheet(f"color:{theme.MUTED};font-size:14px;")
            col.addWidget(sub)
            row._party_sub_lbl = sub                  # type: ignore[attr-defined]
        elif sub.text() != tail.strip():
            sub.set_full(tail.strip())

    @staticmethod
    def _party_row_set_name(row: QtWidgets.QWidget, name: str) -> None:
        """Update a buff row's name line in place (full text, so the elide
        re-runs)."""
        lbl = getattr(row, "_party_name_lbl", None)
        if lbl is not None and lbl._full != name:      # noqa: SLF001
            lbl.set_full(name)

    @staticmethod
    def _party_gear_icon(kind: str) -> QtWidgets.QLabel:
        """A gear row's leading atlas icon (the item sheet sprite), falling
        back to the shield glyph when the id has no sprite."""
        from ...data import icons as gicons
        lbl = QtWidgets.QLabel()
        lbl.setFixedSize(_GEAR_ICON_PX, _GEAR_ICON_PX)
        if kind and kind != "?" and gicons.has_icon("item", kind):
            lbl.setPixmap(_party_sprite_tile("item", kind, _GEAR_ICON_PX))
        else:
            lbl.setPixmap(gicons.emoji_pixmap("🛡", _GEAR_ICON_PX, theme.MUTED))
        return lbl

    def _party_section_error(self, label: str, detail: str) -> None:
        """The visible half of _fill_party_section: the section says it failed,
        in the section, so a broken read can't look like an empty one."""
        lay = getattr(self, "_party_gear", None)
        if lay is None:
            return
        lay.addWidget(self._party_empty(
            f"{label}: this section failed to render ({detail})."))

    @staticmethod
    def _party_empty(text: str) -> QtWidgets.QLabel:
        lbl = QtWidgets.QLabel(text)
        lbl.setObjectName("Muted")
        lbl.setWordWrap(True)
        return lbl
