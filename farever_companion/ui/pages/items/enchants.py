"""Enchants-page assembly: the full enchant database on one dedicated
page — the Enchants tab of the Items page. Lists the +2 / corrupted
enchant scrolls, the Jeweller gem augments, the demon-gear stat
conversions, the craftable Elixirs and Food dishes, and the merged
profession augments card. A search box and a Stat chip row narrow every
card; a tab bar switches one full-width group per category. Builds into
a caller layout or standalone via _page_container().
"""
from __future__ import annotations

from PySide6 import QtWidgets

from ... import theme
from ... import components as C
from ...chips import ChoiceChips
from ....data import items as idata
from .enchants_rows import (EnchantsRowsMixin, _AUG_SLOT,
                            _AUG_SLOT_COLOR)


class EnchantsPageBase(EnchantsRowsMixin):
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

        head = QtWidgets.QHBoxLayout()
        h1 = QtWidgets.QLabel("ENCHANTS & GEMS")
        h1.setObjectName("H1")
        # search: matches scrolls / gems / conversions by item name or stat
        # text — composes with the stat chips below (narrows within them);
        # sits left beside the heading, the stretch pushes it to the start
        self._ench_search = QtWidgets.QLineEdit()
        self._ench_search.setPlaceholderText("Search name or stat…")
        self._ench_search.setClearButtonEnabled(True)
        self._ench_search.setFixedWidth(240)
        self._ench_search.setFixedHeight(30)
        self._ench_search.textChanged.connect(self._ench_refilter)
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
                                      any_label="All stats", label="Stat",
                                      colors={s: theme.stat_color(s) for s in stats})
        self._ench_stat.setMinimumHeight(62)
        v.addWidget(self._ench_stat)
        self._ench_stat.currentIndexChanged.connect(self._ench_refilter)

        # the six cards — the row bodies are rebuilt in place on filter.
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
        self._ench_augments_card = acard
        self._ench_elixirs_card = ecard
        self._ench_foods_card = fcard
        self._ench_scrolls_card = scard
        self._ench_gems_card = gcard
        self._ench_convs_card = ccard
        # category tabs (mockup B) — one full-width group per tab, the
        # tabs ARE the section picker; the page opens on Gems, Scrolls last
        self._ench_tabs = C.UnderlineTabs(
            ["Gems", "Augments", "Conversions", "Elixirs", "Food",
             "Scrolls"])
        self._ench_tabs.currentChanged.connect(self._ench_refilter)
        v.addWidget(self._ench_tabs)
        # the six cards, one per tab — full width, shown by the active tab
        v.addWidget(scard)
        v.addWidget(gcard)
        v.addWidget(ccard)
        v.addWidget(ecard)
        v.addWidget(fcard)
        v.addWidget(acard)
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
        self._clear_lay(self._ench_augments_lay)
        self._clear_lay(self._ench_elixirs_lay)
        self._clear_lay(self._ench_foods_lay)
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
        # no rows for it (the user then flips to the right tab)
        counts = self._ench_stat_counts()
        self._ench_stat.set_option_labels(
            {s: f"{s} · {n}" for s, n in counts.items()})
        sel = self._ench_selected_stat()
        self._ench_stat.set_visible_options(
            {s for s, n in counts.items() if n}
            | ({sel} if sel else set()))

        # the active tab shows its card — the stat filter applies everywhere
        tab = self._ench_tabs.currentText()
        for key, card in (("Scrolls", self._ench_scrolls_card),
                          ("Gems", self._ench_gems_card),
                          ("Conversions", self._ench_convs_card),
                          ("Elixirs", self._ench_elixirs_card),
                          ("Food", self._ench_foods_card),
                          ("Augments", self._ench_augments_card)):
            card.setVisible(tab == key)

        # the filtered totals — the old top count tag ('OFFLINE · N
        # SCROLLS …') is gone per the UI cleanup, but the numbers stay
        # available on the page for tests and future UI
        self._ench_counts = {
            "SCROLLS": len(scrolls) + len(corrupt),
            "GEMS": len(gems),
            "CONVERSIONS": len(convs),
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

