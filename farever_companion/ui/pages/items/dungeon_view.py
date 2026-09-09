"""The gear page's 🏰 DUNGEONS tab — weapons left, shared gear right.

LEFT COLUMN (this module): faction chips (one active at a time, Bee
default), then a "{Faction} Dungeons" row (the whole family) followed by
each boss's row with its signature weapon tiles — one lit at a time, so a
boss click always means that boss's own drops (its weapons + its heroic
Epic set) and the faction row is the only place showing the whole shared
pool. Its dungeon list (always shown, both views, 2 columns) carries each
boss's icon — every button picks its boss, the same single logic as the
left-column rows, while the SHARED SET header jumps back to the full list.

RIGHT PANE (`dungeon_render.py`, re-exported here): the picked boss's
signature weapons on top (when a boss row is clicked), then the faction's
shared armor/trinket set in a locked 2-column grid — every dungeon of a
family rolls the same pool, so the shared grid is family-wide and only the
faction view carries it (Nightling is the one family whose pool is BOUGHT,
not dropped: its header names Mira, Demon Huntress instead of claiming
"drops in: <bosses>") — plus the heroic EPIC sets, one block per boss until
a boss row is clicked, which narrows the pane to that boss alone. Both
tiers always show together (Rare pool + Epic heroic sets): this tab ignores
the Rarity buttons — only the class/Only/rating chips filter it — so there
is no tier switch to flip. Clicking a tile opens the item's detail card.
"""
from __future__ import annotations

from PySide6 import QtCore, QtWidgets

from ... import theme
from ... import components as C
from ...layout import FlowLayout
from ....data import icons
from ....data import items as idata
from .support import FACTION_EMOJI as _FACTION_EMOJI, faction_label
from .cache_box import vendor_line
from .dungeon_box import show_box, weapon_chip as _weapon_chip
from .dungeon_render import (  # noqa: F401  (re-exports, see __all__ below)
    _dungeon_grid,
    _dungeon_jump_button,
    _epic_header,
    _filter_stamp,
    _item_tile,
    _locked_grid,
    _picked_boss,
    _pool_block,
    _section_label,
    _usable,
    show_faction,
)

# the picked boss row in the left list (no pick = the pane speaks for every
# boss, so no row is lit). Subtle on purpose: the Card keeps its default
# neutral border so no gold "select box" is drawn around the row — only a
# faint tint plus a gold left edge marks the pick.
_BOSS_PICK_QSS = (f"QFrame#Card {{ background: {theme.with_alpha(theme.GOLD, 18)}; "
                  f"border: 1px solid {theme.BORDER}; "
                  f"border-left: 3px solid {theme.GOLD}; }}")


class DungeonView(QtWidgets.QScrollArea):
    """Left column of the Dungeons tab: faction chips + boss list."""

    def __init__(self, page: QtWidgets.QWidget):
        super().__init__()
        self._page = page
        self.setWidgetResizable(True)
        self.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)

        body = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(body)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)

        groups = idata.dungeon_drop_groups()
        self._groups = {g["faction"]: g for g in groups}
        self._rows: dict[str, QtWidgets.QFrame] = {}
        # vendor lines, kept out of _rows on purpose: they are not clickable
        # boss picks, so the highlight/toggle pass must not treat them as one
        self._vendor_rows: dict[str, QtWidgets.QFrame] = {}
        self._all_row: QtWidgets.QFrame | None = None
        self._sel: tuple[str, str] | None = None
        self._fac = ""
        self._faction_history: list[str] = []

        # faction chips — one active at a time, and WRAPPING onto a second
        # row: five labels + their counts need ~940 px while this pane is
        # 579 px wide (it never scrolls horizontally), so a single row pushed
        # the last faction off the bar. The count rides each chip's tooltip
        # instead of its label (see _chip_count) — the pieces with no drop
        # row are drawn nowhere (show_faction), so they are not counted
        chips = QtWidgets.QWidget(body)
        cl = FlowLayout(chips, margin=0, spacing=6)
        self._chips: dict[str, C.Chip] = {}
        first = groups[0]["faction"] if groups else ""
        for g in groups:
            fac = g["faction"]
            # GOLD is this page's own accent — the boss rows, their sprites
            # and the pane's faction title all use it. Only the LIT chip wears
            # it: class_color() was the old source and it is for real classes,
            # a faction name has none, so every chip came out MUTED grey as if
            # the row were multi-select
            chip = C.FilterChip(self._chip_text(fac), checked=(fac == first),
                                color=theme.GOLD if fac == first else None,
                                parent=chips)
            chip.setToolTip(self._chip_tip(fac))
            # both edges are handled: an un-check is refused (see _pick), so
            # the row is radio-style — always exactly one faction lit
            chip.toggled.connect(lambda on, f=fac: self._pick(f, on))
            cl.addWidget(chip)
            self._chips[fac] = chip
        v.addWidget(chips)

        # the boss list for the active faction
        self._list_host = QtWidgets.QWidget(body)
        self._list_lay = QtWidgets.QVBoxLayout(self._list_host)
        self._list_lay.setContentsMargins(0, 0, 0, 0)
        self._list_lay.setSpacing(6)
        v.addWidget(self._list_host)
        v.addStretch(1)
        self.setWidget(body)

        if first:
            self._fac = first
            self._fill_list(first)

    # ------------------------------------------------------------------
    def _chip_count(self, faction: str) -> int:
        """How many of the faction's pieces the LIVE filters let through —
        the class chip, Only and the rating chip count, the same gate the
        tiles use, so the number says what picking that faction would show.
        The Rarity buttons do NOT count here: this tab shows both tiers at
        once, so a tier gate would undercount its own tiles."""
        return sum(1 for iid in self._pool(faction)
                   if _usable(self._page, iid, ignore_rarity=True))

    def _chip_text(self, faction: str) -> str:
        """A faction chip's label: the faction alone. The live count rides
        the tooltip (_chip_tip) — five labels plus their counts needed
        ~940 px in a 579 px pane, which pushed the last faction off the
        bar."""
        return (f"{_FACTION_EMOJI.get(faction, '🏰')} "
                f"{faction_label(faction)}")

    def _chip_tip(self, faction: str) -> str:
        """The chip's tooltip: what picking that faction would show under
        the live filters."""
        return (f"{self._chip_count(faction)} "
                f"{faction_label(faction)} pieces under the current "
                "filters")

    def _pool(self, faction: str) -> tuple[str, ...]:
        """Everything that faction's dungeons carry: the shared set, the
        bosses' signature weapons and their heroic Epic drops. The pieces
        with no drop row are never drawn, so they are not counted."""
        g = self._groups.get(faction) or {}
        bosses = g.get("bosses") or ()
        return tuple(dict.fromkeys(
            list(g.get("shared") or ())
            + [w for b in bosses for w in b.get("weapons") or ()]
            + [e for b in bosses for e in b.get("epic") or ()]))

    def _sync_chip_texts(self) -> None:
        """Re-label the faction chips — their counts follow the live filters
        (filled in by _dg_refresh and every faction pick)."""
        for fac, chip in self._chips.items():
            text = self._chip_text(fac)
            if chip.text() != text:
                chip.setText(text)
            tip = self._chip_tip(fac)
            if chip.toolTip() != tip:
                chip.setToolTip(tip)

    def _check_only(self, faction: str) -> None:
        """Light exactly one faction chip, silently (the sweep must not fire a
        pick per chip — that is what made re-picking need 2+ clicks). The lit
        chip is also the only one wearing the gold accent."""
        for f, c in self._chips.items():
            want = f == faction
            if c.isChecked() != want:
                c.blockSignals(True)
                c.setChecked(want)
                c.blockSignals(False)
            c.set_color(theme.GOLD if want else None)

    def _pick(self, faction: str = "", on: bool = True,
              record_history: bool = True) -> None:
        """Chip click: switch the boss list to that faction. Single-select —
        `self._fac` is the truth, so the sweep's programmatic unchecks are
        ignored instead of re-triggering a pick ping-pong between factions.
        Re-clicking the lit chip goes back to the faction overview from an
        open item card or a boss pick (a no-op when already there): it is
        the way back for users without a mouse BACK button."""
        chip = self._chips.get(faction)
        if not faction or chip is None:
            return
        if not on:
            if faction == self._fac:
                self._check_only(faction)
                self.select(faction, "")
            else:
                self._check_only(self._fac)
            return
        if faction == self._fac:
            self._check_only(faction)      # snap the active chip back on
            self.select(faction, "")
            return
        if record_history and self._fac:
            self._faction_history.append(self._fac)
        self._fac = faction
        self._check_only(faction)
        g = self._groups.get(faction)
        if not g:
            return
        self._fill_list(faction)
        # keep the Rarity buttons' counts following THIS faction's drops
        # (the row hides on this tab — both tiers always show — but the
        # numbers stay sane for the tier-filtered tabs)
        sync_counts = getattr(self._page, "_items_sync_rarity_counts", None)
        if callable(sync_counts):
            sync_counts("@dungeon")
        if g["bosses"]:
            # no boss is picked to start with: the pane opens on EVERY boss's
            # set, and a click on one of the rows narrows it (toggle())
            self.select(g["faction"], "")

    # ------------------------------------------------------------------
    def ensure_card(self) -> None:
        """Make sure the right pane shows the active faction's gear card —
        called when the Dungeons tab is first shown / returned to."""
        fac = getattr(self, "_fac", "") or next(iter(self._groups), "")
        g = self._groups.get(fac)
        if not g or not g["bosses"]:
            return
        sel = getattr(self, "_sel", None)
        # "" is a real state (every boss), so only a pick for ANOTHER faction
        # is dropped
        bid = sel[1] if sel and sel[0] == fac else ""
        self.select(fac, bid)

    def _fill_list(self, faction: str) -> None:
        """Rebuild the faction's rows: the "{Faction} Dungeons" row on top
        (the whole family — lit whenever no boss is picked), then one row
        per boss — boss name and its signature weapons side by side on the
        same line (class-filtered)."""
        self._rows.clear()          # drop refs to about-to-be-deleted rows
        self._vendor_rows.clear()
        self._all_row = None
        while self._list_lay.count():
            it = self._list_lay.takeAt(0)
            if w := it.widget():
                w.deleteLater()
        g = self._groups[faction]
        self._all_row = self._all_dungeons_row(g)
        self._list_lay.addWidget(self._all_row)
        for b in g["bosses"]:
            row = self._boss_row(g, b)
            self._list_lay.addWidget(row)
            self._rows[(faction, b["boss_id"])] = row
        # the faction's VENDOR gets its own line below the bosses. A box is
        # not a boss drop — Mira sells hers for Nightblood and no boss hands
        # them out — so putting her caches on a boss row claimed a kill drops
        # them. Separate row, same column, so the list reads: bosses drop
        # weapons, the vendor sells boxes.
        vrow = self._vendor_row(g)
        if vrow is not None:
            self._list_lay.addWidget(vrow)
        self._sync_chip_texts()
        self._sync_boss_highlight()

    def _all_dungeons_row(self, g: dict) -> QtWidgets.QFrame:
        """The top row of the boss list: the whole faction (every dungeon's
        pool at once, the only view carrying the shared set). Clicking it
        releases a boss pick, so the list reads as one lit entry at a time —
        the faction, or exactly one boss."""
        fac = g["faction"]
        row = QtWidgets.QFrame()
        row.setObjectName("Card")
        h = QtWidgets.QHBoxLayout(row)
        h.setContentsMargins(8, 6, 8, 6)
        h.setSpacing(10)
        emo = QtWidgets.QLabel(_FACTION_EMOJI.get(fac, "🏰"))
        emo.setStyleSheet("font-size:22px;background:transparent;")
        emo.setFocusPolicy(QtCore.Qt.NoFocus)
        h.addWidget(emo)
        nm = QtWidgets.QLabel(f"{faction_label(fac)} Dungeons")
        nm.setStyleSheet(
            f"color:{theme.GOLD};font-weight:700;font-size:13px;"
            "background:transparent;border:0;outline:0;")
        nm.setFocusPolicy(QtCore.Qt.NoFocus)
        nm.setTextInteractionFlags(QtCore.Qt.NoTextInteraction)
        h.addWidget(nm, 2)
        n = sum(1 for iid in self._pool(fac)
                if _usable(self._page, iid, ignore_rarity=True))
        cnt = QtWidgets.QLabel(f"{n} pieces")
        cnt.setObjectName("Mono")
        cnt.setStyleSheet(
            f'color:{theme.MUTED};font-family:"{theme.MONO_FONT}", monospace;'
            "font-size:11.5px;background:transparent;")
        cnt.setFocusPolicy(QtCore.Qt.NoFocus)
        cnt.setTextInteractionFlags(QtCore.Qt.NoTextInteraction)
        h.addWidget(cnt)
        row.setCursor(QtCore.Qt.PointingHandCursor)
        row.setFocusPolicy(QtCore.Qt.NoFocus)
        row.mouseReleaseEvent = lambda ev: (self.select(fac, ""),
                                            ev.accept())
        return row

    def _vendor_row(self, g: dict) -> QtWidgets.QFrame | None:
        """The faction's vendor line: who they buy boxes from, and the boxes.
        A box is not a boss drop — Mira sells hers for Nightblood and no boss
        hands them out — so her caches sit on this line, never on a boss row.
        None when the family has no vendor-sold stock, so the list carries no
        empty line for it."""
        fac = g["faction"]
        boxes = idata.faction_caches(fac)
        v = idata.cache_vendor(next(
            (b["cache"] for b in boxes if b.get("vendor")), "")) or {}
        row = vendor_line(self._page, boxes, v.get("source", ""),
                          v.get("source_id", ""))
        if row is not None:
            self._vendor_rows[fac] = row
        return row

    def _boss_row(self, g: dict, b: dict) -> QtWidgets.QFrame:
        fac = g["faction"]
        row = QtWidgets.QFrame()
        row.setObjectName("Card")
        h = QtWidgets.QHBoxLayout(row)
        h.setContentsMargins(8, 6, 8, 6)
        h.setSpacing(10)
        sprite = QtWidgets.QLabel()
        sprite.setPixmap(icons.outlined("Units", b["boss_id"], 38,
                                        theme.GOLD, border=2, trim=True))
        sprite.setFocusPolicy(QtCore.Qt.NoFocus)
        sprite.setTextInteractionFlags(QtCore.Qt.NoTextInteraction)
        h.addWidget(sprite)
        nm = QtWidgets.QLabel(b["boss"])
        nm.setStyleSheet(
            f"color:{theme.GOLD};font-weight:700;font-size:13px;"
            "background:transparent;border:0;outline:0;")
        nm.setWordWrap(True)
        nm.setFocusPolicy(QtCore.Qt.NoFocus)
        nm.setTextInteractionFlags(QtCore.Qt.NoTextInteraction)
        h.addWidget(nm, 2)
        for wid in b["weapons"]:
            # this tab shows both tiers at once — the Rarity buttons don't
            # gate it, only the class/Only/rating chips do
            if not _usable(self._page, wid, ignore_rarity=True):
                continue
            h.addWidget(_weapon_chip(self._page, wid), 3)
        row.setCursor(QtCore.Qt.PointingHandCursor)
        row.setFocusPolicy(QtCore.Qt.NoFocus)
        key = (fac, b["boss_id"])
        row.mouseReleaseEvent = lambda ev, k=key: (self.toggle(*k),
                                                   ev.accept())
        return row

    # ------------------------------------------------------------------
    def toggle(self, faction: str, boss_id: str) -> None:
        """Click on a boss row: narrow the pane to that boss's own set.
        Clicking the lit row again lets go, so the pane goes back to every
        boss of the faction."""
        same = self._sel == (faction, boss_id)
        self.select(faction, "" if same else boss_id)

    def _sync_boss_highlight(self) -> None:
        """Light exactly one row in the boss list, so the pane's scope is
        visible in the list — the picked boss's row, or the All Dungeons row
        when the pane carries every boss."""
        sel = getattr(self, "_sel", None) or ("", "")
        for key, row in self._rows.items():
            try:
                row.setStyleSheet(
                    _BOSS_PICK_QSS if key == tuple(sel) and sel[1] else "")
            except RuntimeError:            # row already deleted
                continue
        all_row = getattr(self, "_all_row", None)
        if all_row is not None:
            try:
                all_row.setStyleSheet("" if sel[1] else _BOSS_PICK_QSS)
            except RuntimeError:            # row already deleted
                pass

    def select_box(self, faction: str, cache_id: str) -> None:
        """Open a cache in the pane, the way a boss pick narrows the pane.
        Clicking the same box again releases it back to the faction view."""
        if self._sel == (faction, cache_id):
            self._sel = (faction, "")
            self.select(faction, "")
            return
        g = self._groups.get(faction)
        if g is None:
            return
        self._sel = (faction, cache_id)
        self._sync_boss_highlight()
        show_box(self._page, cache_id)

    def select(self, faction: str, boss_id: str) -> None:
        """Show one boss's gear in the pane — `boss_id` "" means the whole
        faction (every boss's set, which is how a faction pick starts), a
        real id narrows it to that boss. Highlights the picked row."""
        self._sel = (faction, boss_id)
        g = self._groups.get(faction)
        if g is None:
            return
        self._sync_boss_highlight()
        # one card per family — rebuild when the faction changes, another
        # boss of the same family is picked (the pane carries that boss's
        # Epic heroic drops), the pane is showing something else (an item
        # detail cleared the card), or the class/Only/rarity filters moved
        # since this card was rendered
        stamp = _filter_stamp(self._page)
        if getattr(self._page, "_dg_shown_faction", "") != faction \
                or getattr(self._page, "_dg_rendered_boss", None) != boss_id \
                or getattr(self._page, "_dg_pane_state", "") != "boss" \
                or getattr(self._page, "_dg_rendered_class", None) != stamp:
            show_faction(self._page, g, boss_id)


__all__ = [
    "DungeonView",
    # pane renderers split into dungeon_render.py — re-exported so every
    # caller (dungeon_box / filters import inside their functions, page.py,
    # the tests) keeps resolving them from this module
    "show_faction",
    "_pool_block",
    "_dungeon_grid",
    "_picked_boss",
    "_locked_grid",
    "_dungeon_jump_button",
    "_epic_header",
    "_filter_stamp",
    "_usable",
    "_section_label",
    "_item_tile",
    "show_box",
    "vendor_line",
]
