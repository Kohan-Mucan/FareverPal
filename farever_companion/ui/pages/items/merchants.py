"""The Merchants view — every recorded vendor's counter, side by side.

Reached from a chip on the Items tab's category row, not a tab of its own:
it answers a question about items, so it belongs beside the other item
filters rather than a level above them.

The item page answers "where does THIS come from". This answers the other
half: "what does each merchant actually stock, and what does it charge".
`data/items/sources.py` holds the evidence — the drops index's own npc rows,
grouped per vendor (`vendor_stock_lists()`) — and this is the only view that
keeps the vendors apart; everywhere else those rows are flattened into "who
sells this item", which cannot answer a shopping question because two shops'
rows would interleave into one list.

Two things the index carries that the old scanned sheet could not:

* A CURRENCY. The shop's cost rides the index as `[{kind, amount}]` (Gold,
  Nightblood, Demonic Souls, Medal of Glory), so the charge column names what
  the number is. A bare price would read as Gold, and Rumi's Rift Stone and
  Zoey's Demonic Souls are not the same unit.
* REAL NAMES. The index already writes each vendor's display name, so a row
  can never be attributed to a scanner-shaped id.

Locations come from the index's own location strings, resolved to their town;
a vendor with no resolvable position says "town not recorded" rather than
guessing the nearest town to a 0,0.
"""
from __future__ import annotations

from PySide6 import QtCore, QtWidgets

from ... import components as C
from ... import theme
from ....data import codex, icons, items as idata
from .drops import vendor_sprite
from .support import WrapLabel

# the offer tile, matching the Farm tab's row icon
_ROW_ICON = 32


class MerchantsMixin:
    """The Merchants tab: one section per recorded vendor, one row per offer."""

    # --- the chip, and the body swap it drives ---------------------------

    def _items_add_merchants_chip(self, chips_lay) -> None:
        """Add the Merchants chip to the Items tab's category row.

        One section per recorded vendor, each vendor's own counter. It was a
        sixth top-level tab, which is two levels of navigation for a question
        this page already answers — and it made the strip wide enough to crowd
        the tabs a player actually uses. As a chip it sits beside the other
        pseudo-categories (Unreleased) and is reached the same way.

        Only added when a scan has actually recorded a shop: a chip that opens
        an empty view teaches nothing, and the sheet grows the moment someone
        runs the scan.
        """
        n = len(idata.vendor_stock_lists())
        if not n:
            return
        chip = C.FilterChip(f"Merchants · {n}", checked=False,
                            color=theme.GOLD,
                            parent=self._items_allcat_chips_box)
        chip.setToolTip(
            "Every recorded vendor's counter, one section per vendor — what "
            "they stock and what they charged for it.")
        chip.toggled.connect(
            lambda on: self._items_toggle_allcat("@merchants", on))
        chips_lay.addWidget(chip)
        self._items_allcat_chips["@merchants"] = chip

    def _items_host_merchants(self, v) -> None:
        """Put the Merchants body in the Items view, hidden.

        It lives beside the pool list rather than on the page's stack: it is
        reached by a chip, not a tab, so the two share one view and only one
        is ever visible. Built here rather than in `_page_gear` because the
        Items view is assembled first.
        """
        self._items_merchants_scroll = self._items_merchants_setup()
        v.addWidget(self._items_merchants_scroll, 1)
        self._items_merchants_scroll.hide()

    def _items_sync_allcat_view(self) -> None:
        """Show the body the active Items-tab chip asks for: the pool list for
        every real category, the per-vendor view for the Merchants chip.

        Unlike Unreleased, Merchants is not a slice of the pool list. A
        shopping question has no answer in a flat list, because two shops'
        rows interleave and you cannot tell whose stock you are reading — so
        the chip swaps the body rather than filtering it.

        The Merchants body is rebuilt on every open rather than cached: it
        reads a sheet a scan rewrites, so a stock list frozen at the first
        click is exactly the stale view that sheet exists to prevent.

        The shared header follows too. It is the one count the left column
        carries, and left alone it would read "ITEMS 0 / N" beside a list that
        is not on screen — so while the Merchants view is up it names the
        vendors instead.
        """
        on_merchants = getattr(self, "_items_allcat", "@items") == "@merchants"
        lst = getattr(self, "_items_all_list", None)
        if lst is not None:
            lst.setVisible(not on_merchants)
        scr = getattr(self, "_items_merchants_scroll", None)
        if scr is not None:
            scr.setVisible(on_merchants)
        if not on_merchants:
            return
        self._items_ensure_merchants()
        hdr = getattr(self, "_items_search_header", None)
        n = len(idata.vendor_stock_lists())
        if hdr is not None:
            hdr.set_tag(f"{n} VENDOR SHOP{'S' if n != 1 else ''}")

    def _items_merchants_setup(self) -> QtWidgets.QScrollArea:
        """Build the Merchants container once (page assembly).

        Only the container: the body is filled when the chip is first picked,
        so opening the Items page never pays for a view most visits skip.
        """
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        body = QtWidgets.QWidget()
        self._merchants_lay = QtWidgets.QVBoxLayout(body)
        self._merchants_lay.setContentsMargins(0, 0, 0, 0)
        self._merchants_lay.setSpacing(14)
        self._merchants_built = False
        scroll.setWidget(body)
        return scroll

    def _items_ensure_merchants(self) -> None:
        """Build the Merchants body the first time the chip is picked."""
        if getattr(self, "_merchants_built", False):
            return
        if getattr(self, "_merchants_lay", None) is None:
            return
        self._items_show_merchants()
        self._merchants_built = True

    # --- the tab body ----------------------------------------------------

    def _items_show_merchants(self) -> None:
        """Rebuild the Merchants view from the merged scan sheet."""
        lay = getattr(self, "_merchants_lay", None)
        if lay is None:
            return
        self._items_clear_layout(lay)
        # ONE entry per vendor, unread offers included: the two accessors are
        # separate on purpose at the data level, but they are two views of the
        # same counter, and rendering them as separate blocks listed Zoey
        # twice — 26 offers here, 2 there — which reads as two shops and hides
        # that the 2 unread rows are hers.
        sections = idata.vendor_sections()
        # the vendor count, not the offer count: each vendor below carries its
        # own, so an offer total here would only repeat what they already say
        lay.addWidget(C.SectionHeader(
            "Merchant Shops",
            tag=f"{len(sections)} VENDOR{'S' if len(sections) != 1 else ''}"))
        lay.addWidget(self._merchants_note())
        if not sections:
            msg = WrapLabel("No merchant stock is indexed yet — the drops "
                            "index carries the vendors' counters, so "
                            "re-running the data capture fills this view.")
            msg.setObjectName("Muted")
            msg.setMaximumWidth(680)
            lay.addWidget(msg)
            lay.addStretch(1)
            return
        for entry in sections:
            lay.addLayout(self._vendor_block(entry))
        lay.addStretch(1)

    def _merchants_note(self) -> QtWidgets.QLabel:
        """The one-time explanation of what the numbers are — and are not.

        A bare price column reads as Gold until something says otherwise, and
        "until something says otherwise" is exactly how a wrong currency gets
        quoted back at the user. This is the only place that says it.
        """
        note = WrapLabel(
            "Each row is what the vendor's counter charges, in the currency "
            "the drops index records. Cheapest offer first; a vendor that "
            "keeps two offers apart (the Guild Merchants' per-town prices) "
            "lists its counter once.")
        note.setObjectName("Muted")
        note.setMaximumWidth(700)
        return note

    def _vendor_icon(self, key: str, name: str, resolved: bool) -> QtWidgets.QWidget:
        """The leading tile on a vendor's header: their own NPC portrait where
        one exists, else the shop marker.

        `vendor_sprite` is the SAME resolution an item's drop row uses, so a
        vendor wears the face she wears on the item page rather than a second,
        slightly different answer — Zoey is the case that proves it. `key` is
        the scanner's id and `name` what the game calls them; the lookup wants
        both, because the scan names vendors after things like the currency
        they take.

        Unread sections get the DIM accent: a counter the app cannot attach to
        an item is a weaker claim than one it can, and the tile says so
        before the tag does.
        """
        accent = theme.GOLD if resolved else theme.DIM
        tile = C.IconTile(_ROW_ICON)
        sprite = vendor_sprite(key, name)
        if sprite and icons.has_icon("Units", sprite):
            tile.set("Units", sprite, accent)
        else:
            tile.set_marker("npc", accent)
        tile.setToolTip(name or "Unidentified merchant")
        return tile

    def _vendor_block(self, entry: dict) -> QtWidgets.QVBoxLayout:
        """One vendor: their icon and header line, then their offers.

        THREE kinds of offer live in this one section. `rows` name items;
        `pet_rows` are the critter-unit offers, which resolved onto codex
        units rather than items and so open a Codex card rather than an item
        page — they keep their own sub-section because what they open is a
        different page, but they are the SAME counter, listed under the same    vendor. `unread_rows` are the offers no sheet names at all, listed
    last rather than in a second section.

    The header deliberately carries NO tag: a stock count says less
    than the charges the rows below it show, and every row already
    states its own price — so a count beside the name is decoration
    the player has to read past.
    """
        # the sheet's key is the scanner's id, not a name: header the section
        # with what the game calls them, and keep the key only for the icon
        # lookup and the tooltip, where the id is the thing that resolves
        name = idata.vendor_display_name(entry.get("vendor"))
        key = entry.get("vendor") or ""
        n = entry.get("n_items") or 0
        n_pets = entry.get("n_pets") or 0
        n_unread = entry.get("n_unread") or 0
        # a vendor whose scan named nothing we can resolve is DIM throughout:
        # a counter the app cannot attach to an item is a weaker claim than
        # one it can, and the header should not claim otherwise. A pet IS
        # resolved — onto its codex unit — so a counter stocking only pets
        # still reads as a resolved one.
        resolved = bool(n or n_pets)
        lay = QtWidgets.QVBoxLayout()
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        # the icon rides BESIDE the header rather than inside it: a
        # SectionHeader lays out its own label and right-aligned tag, and a
        # tile dropped in there would be re-stretched on every reflow. One
        # row holding the tile and the header keeps both at their own sizes.
        head = QtWidgets.QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(8)
        head.addWidget(self._vendor_icon(key, name, resolved), 0,
                       QtCore.Qt.AlignVCenter)
        hdr = C.SectionHeader(
            name or "Unidentified merchant",
            color=theme.TEXT if resolved else theme.DIM)
        head.addWidget(hdr, 1)
        lay.addLayout(head)

        sub_bits = []
        towns = list(entry.get("towns") or ())
        sub_bits.append(" · ".join(towns) if towns else "town not recorded")
        scanned = entry.get("scanned_at") or ""
        if scanned:
            sub_bits.append(f"scanned {scanned}")
        sub = WrapLabel(" · ".join(sub_bits))
        sub.setStyleSheet(
            f"color:{theme.DIM};font-size:11px;background:transparent;")
        lay.addWidget(sub)

        for row in self._vendor_rows(entry.get("rows") or (), True):
            lay.addWidget(row)
        if n_pets:
            # the pets resolved onto codex UNITS, not items: the same
            # counter and the same section, but a row here opens a Codex
            # card rather than an item page, so they keep their own
            # sub-section — mixing them into the offers would make one
            # click mean two different things in a single list
            lay.addWidget(C.SectionHeader(
                "Pets", color=theme.TEXT))
            for row in self._vendor_rows(entry.get("pet_rows") or (), True):
                lay.addWidget(row)
        if n_unread:
            # said in the section, not per row: these are the vendor's too,
            # they are simply the ones no ITEM sheet names. That is a weaker
            # statement than it looks — the pets and critters have no item row
            # by design and are full codex entries, which the rows below now
            # link to. Say which is which rather than calling them unnamed.
            # a row the sheet tagged as a critter is a codex entry
            # the merge already identified: count it from the row
            # instead of asking the codex again, and query only the
            # rows the sheet says nothing about
            in_codex = sum(1 for r in (entry.get("unread_rows") or ())
                           if r.get("codex") or idata.codex_entry_for(r))
            n_cx, n_none = in_codex, n_unread - in_codex
            bits = []
            if n_cx:
                bits.append(f"{n_cx} of them are Codex entries "
                            f"(pets and critters carry no item row) and open "
                            f"their Codex card")
            if n_none:
                bits.append(f"{n_none} {'is' if n_none == 1 else 'are'} named "
                            f"by no sheet at all, so {'it has' if n_none == 1 else 'they have'} "
                            f"nothing to open")
            warn = WrapLabel(" · ".join(bits) + ".")
            warn.setStyleSheet(
                f"color:{theme.DIM};font-size:11px;background:transparent;")
            lay.addWidget(warn)
            for row in self._vendor_rows(entry.get("unread_rows") or (), False):
                lay.addWidget(row)
        return lay

    def _vendor_rows(self, rows: tuple, resolved: bool):
        """That vendor's offers, cheapest first.

        A shopping question has no answer in scan order. Rows with no recorded
        charge sort last rather than first, so a shop that priced everything
        reads top to bottom and the unpriced remainder collects at the bottom.
        Ties fall back to name so two rows at the same charge keep a stable,
        alphabetical order rather than whatever the sheet happened to list.
        """
        def sort_key(row: dict):
            charge = idata.vendor_charge(row)
            return (charge is None, charge if charge is not None else 0,
                    (row.get("name") or row.get("item") or "").lower())

        return [self._vendor_offer_row(r, resolved)
                for r in sorted(rows, key=sort_key)]

    def _vendor_offer_row(self, row: dict,
                          resolved: bool = True) -> QtWidgets.QFrame:
        """One offer: icon, item (or the raw scanned label), stack, charge.

        Clicking an offer opens that item's page, the same gesture a drop
        source makes.

        An offer the ITEM sheets cannot name is not automatically unknown. The
        pets and critters have no `item.json` row at all, yet each is a full
        codex entry with art and a card — so one of those draws its own icon
        and opens its codex page, and only a name the game itself does not
        carry falls back to the bare shop glyph with nothing to click. A
        resolved pet row is the same case with a real id behind it: its art
        is filed under the unit id, its name keeps the resolved brightness,
        and its click opens the Codex card the id names. The sheet
        records that codex entry on the row itself (`kind` and
        `codex`), so a pet is labeled and routed from the row rather
        than by querying the codex on every render.
        """
        frame = QtWidgets.QFrame()
        frame.setObjectName("Card")
        h = QtWidgets.QHBoxLayout(frame)
        h.setContentsMargins(8, 6, 8, 6)
        h.setSpacing(10)

        rarity = row.get("rarity") or ""
        # a pet's `item` IS a codex unit id (`DemonDog_Blue`): the row
        # resolved onto the critter unit, so what it opens is the codex
        # card, not an item page
        unit_id = str(row.get("unit") or "") if resolved else ""
        # the sheet stamps a pet's codex entry onto its row, so an
        # unread offer is labeled and routed from the row itself; the
        # query remains for rows merged before the stamp existed
        cx = None if resolved else (row.get("codex")
                                    or idata.codex_entry_for(row))
        tile = C.IconTile(_ROW_ICON)
        tile.setFocusPolicy(QtCore.Qt.NoFocus)
        if resolved or cx:
            # `icons.item_tile`, NOT a hardcoded "Items" sheet. Half of what a
            # counter sells is art that never lived there: the mounts and
            # gliders are in the Collection and Units sheets, so pinning the
            # sheet drew a blank tile for Niflelian Goat and every other
            # mount in Zoey's stock. `item_tile` is what the rest of the app
            # uses (cache_box, dungeon_box, the craft lists) and it tries the
            # id, then the display NAME for assets filed with spaces, then the
            # unit sheet — one resolver, so a mount wears the same icon here
            # that it wears on its own page. A codex entry's art is filed
            # under its own unit id, which resolves the same way.
            key = (row.get("item") or "") if resolved else (cx or {}).get("id", "")
            tile.setPixmap(icons.item_tile(
                key, row.get("name") or "", _ROW_ICON,
                theme.rarity_color(rarity) if resolved else theme.DIM))
            tile.setToolTip(key or (row.get("name") or ""))
        else:
            tile.set_marker("npc", theme.DIM)
        h.addWidget(tile, 0, QtCore.Qt.AlignVCenter)

        col = QtWidgets.QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(1)
        # a resolved offer's game name, else the label the shop drew — the raw
        # label is the honest one to show when no sheet names the item, and it
        # is what makes an unread row worth keeping on screen at all
        nm = WrapLabel(row.get("name") or row.get("item") or "")
        nm.setStyleSheet(
            f"color:{theme.rarity_color(rarity) if resolved else theme.DIM};"
            "font-weight:600;font-size:12.5px;background:transparent;")
        col.addWidget(nm)
        bits = [b for b in (rarity, str(row.get("category") or "")) if b]
        if cx:
            # say WHERE it lives, because "no item page" is the whole reason
            # this row is dim and a reader deserves to know it is not missing
            bits.insert(0, f"Codex · {cx['region']}" if cx.get("region")
                        else "Codex entry")
        elif unit_id:
            # a pet says where it lives too, for the same reason: the id
            # is a unit's, and "no item page" must not read as "nowhere".
            # The merge records the region on the row, so the label comes
            # from the sheet; a row from before the stamp still asks the
            # codex here
            region = ((row.get("codex") or {}).get("region")
                      or codex.find_unit_region(unit_id))
            bits.insert(0, f"Codex · {region}" if region else "Codex entry")
        lvl = row.get("level")
        if isinstance(lvl, (int, float)) and lvl > 0:
            bits.append(f"item {int(lvl)}")
        if bits:
            sub = WrapLabel(" · ".join(bits))
            sub.setStyleSheet(
                f"color:{theme.DIM};font-size:11px;background:transparent;")
            col.addWidget(sub)
        h.addLayout(col, 3)

        qty = row.get("qty") or 1
        if isinstance(qty, int) and qty > 1:
            stack = QtWidgets.QLabel(f"×{qty:,}")
            stack.setStyleSheet(
                f"color:{theme.MUTED};font-size:11px;background:transparent;")
            h.addWidget(stack, 0, QtCore.Qt.AlignVCenter)

        h.addWidget(self._charge_cell(row), 0, QtCore.Qt.AlignVCenter)

        if unit_id and hasattr(self, "_codex_jump_to_unit"):
            # a pet: the id is the codex UNIT's, so the card it opens is
            # the codex one — the same gesture a codex-backed unread row
            # makes, at the same brightness an item offer wears
            frame.setCursor(QtCore.Qt.PointingHandCursor)
            frame.setToolTip(
                f"{row.get('name') or unit_id}\nOpen the Codex entry")

            def _open_pet(ev, _uid=unit_id,
                          _nm=row.get("name") or unit_id):
                self._codex_jump_to_unit(_uid, _nm)
                ev.accept()
            frame.mouseReleaseEvent = _open_pet
        elif resolved and hasattr(self, "_items_show_id"):
            frame.setCursor(QtCore.Qt.PointingHandCursor)
            iid = row.get("item") or ""
            frame.setToolTip(f"{row.get('name') or iid}\nOpen the item page")

            def _open(ev, _iid=iid):
                self._items_show_id(_iid)
                ev.accept()
            frame.mouseReleaseEvent = _open
        elif cx and hasattr(self, "_codex_jump_to_unit"):
            # not an item, but a real codex entry with a card — open that
            # instead of leaving the reader at a dead row
            frame.setCursor(QtCore.Qt.PointingHandCursor)
            frame.setToolTip(f"{cx['name']}\nOpen the Codex entry")
            cid, cname = cx["id"], cx["name"]

            def _open_cx(ev, _cid=cid, _cname=cname):
                self._codex_jump_to_unit(_cid, _cname)
                ev.accept()
            frame.mouseReleaseEvent = _open_cx
        return frame

    def _charge_cell(self, row: dict) -> QtWidgets.QWidget:
        """The price cell: the charge, and the listed price when it differs.

        `vendor_charge` is what the vendor took after its budget scale; the
        listed price is what the row said before that. Both matter — a row
        where they differ says the vendor marks its stock up, and showing only
        the charge would hide that the number on screen is not the number in
        the shop window.
        """
        charge = idata.vendor_charge(row)
        cell = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(cell)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(1)
        if charge is None:
            none = QtWidgets.QLabel("NO PRICE")
            none.setStyleSheet(
                f"color:{theme.DIM};font-size:11px;letter-spacing:1px;"
                "background:transparent;")
            none.setAlignment(QtCore.Qt.AlignRight)
            lay.addWidget(none)
            cell.setToolTip("The drops index recorded no price for this row.")
            return cell

        lab = QtWidgets.QLabel(f"{charge:,}")
        lab.setObjectName("Mono")
        lab.setStyleSheet(
            f"color:{theme.GOLD};"
            f'font-family:"{theme.MONO_FONT}", "Consolas", monospace;'
            "font-size:13px;font-weight:700;background:transparent;"
            "border:0;outline:0;")
        lab.setAlignment(QtCore.Qt.AlignRight)
        lay.addWidget(lab)

        listed, scaled = row.get("price"), row.get("scaled_price")
        if isinstance(listed, (int, float)) \
                and isinstance(scaled, (int, float)) and listed != scaled:
            was = QtWidgets.QLabel(f"of {int(listed):,}")
            was.setStyleSheet(
                f"color:{theme.DIM};font-size:10px;background:transparent;")
            was.setAlignment(QtCore.Qt.AlignRight)
            lay.addWidget(was)
        cell.setMinimumWidth(lab.sizeHint().width())
        kinds = [idata.currency_label(c.get("kind") or "") if isinstance(c, dict)
                 else "" for c in (row.get("cost") or ())]
        kinds = [k for k in kinds if k]
        cur = " · ".join(kinds)
        cell.setToolTip(f"Charged {charge:,} {cur}." if cur
                        else f"Charged {charge:,}.")
        return cell