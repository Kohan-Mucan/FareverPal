"""Craft-page detail pane: the recipe card (materials, cost, drops) and the
raw-material card (drops + what uses it — reached by clicking a material
name inside a recipe card). The builder support constants and the
CraftDetailMixin (the _craft_show render pipeline + scroll refresh) live
here so page.py stays a slim assembly.
"""
from __future__ import annotations

from PySide6 import QtCore, QtWidgets

import html

from ... import theme
from ... import components as C
from ...layout import WrapLabel
from ....data import items as idata
from ..items import detail as idetail   # items-list row roles for the jump
from ..items.support import (is_enchant_item,  # wrap-safe labels
                             slot_label)  # (never squashed)
from .queue import (CraftQueueMixin, _crafted_tag,  # the queue + bill rows
                    _craft_source_hint)  # farmable drop hints (no chests)
from .tiles import (CraftTileDelegate,  # re-exported: page.py + tests use detail.*
                    ID_ROLE, HEADER_ROLE, DATA_ROLE, CAT_ROLE, NAME_ROLE,
                    QUEUED_ROLE,
                    HEADER_W, HEADER_H, LIST_ICON,
                    _craft_row_height)


class CraftDetailMixin(CraftQueueMixin):
    def _craft_show(self, current):
        if not hasattr(self, "_craft_scroll"):
            return
        if current is not None and current.data(HEADER_ROLE):
            return            # section headers are inert — keep the detail
        iid = current.data(ID_ROLE) if current is not None else None
        # the shown-id guard stops redundant rebuilds while clicking around
        # (material card -> recipe jump back and current-row clicks)
        if getattr(self, "_craft_shown_id", None) == iid and self._craft_detail_lay.count() > 0:
            return
        self._craft_shown_id = iid

        # To eliminate the "flash", we render the new content into a fresh
        # container and swap it into the scroll area in one go, rather than
        # clearing the live layout widget-by-widget.
        sc = self._craft_scroll
        sc.setUpdatesEnabled(False)
        try:
            container = QtWidgets.QFrame()
            container.setObjectName("Card")
            lay = QtWidgets.QVBoxLayout(container)
            lay.setContentsMargins(18, 16, 18, 16)

            if current is None or not iid:
                lbl = QtWidgets.QLabel(
                    "Select a recipe to see its materials, cost and drop sources. "
                    "Use the job chips to narrow the list, or search.")
                lbl.setObjectName("Muted")
                lbl.setWordWrap(True)
                lay.addWidget(lbl)
                lay.addStretch(1)
            else:
                self._craft_render_recipe_into(iid, lay)

            # Update references to the new live layout before swapping
            self._craft_detail = container
            self._craft_detail_lay = lay
            sc.setWidget(container)
            self._craft_refresh_scroll()
        finally:
            sc.setUpdatesEnabled(True)
            sc.update()

    def _craft_render_recipe_into(self, item_id: str, lay: QtWidgets.QLayout):
        """Helper to render a recipe (or material if prefixed) into a layout."""
        if item_id.startswith("mat:"):
            it = idata.item(item_id[4:])
            if it:
                self._craft_render_material(lay, it)
            return

        r = idata.recipe(item_id)
        if not r:
            return
        self._craft_render_recipe(lay, r,
                                  qty_selector=True,
                                  mat_jump=self._craft_open_material,
                                  split=True)

    def _craft_render_recipe(self, lay, r: dict, jump=None, opener=None,
                             qty_selector: bool = False,
                             qty: int | None = None,
                             mat_jump=None,
                             split: bool = False) -> None:
        """The full recipe card rendered into `lay` — the craft detail pane on
        this page, or an inline drawer on the Gear page (the In Recipes
        section). `jump`/`opener` override where the crafted-material and
        OPEN IN GEAR / OPEN IN ENCHANTS links lead; `mat_jump` (craft page
        only) makes raw-material names links to their own card; `split`
        (craft page only) renders Materials and the Total Needed bill side
        by side."""
        jump = jump or self._craft_jump_to_recipe
        opener = opener or self._craft_open_in_items
        col = theme.rarity_color(r.get("rarity") or "")

        # header: icon + name (+ output count) + rarity tag
        hrow = QtWidgets.QHBoxLayout()
        hrow.setSpacing(12)
        tile = C.IconTile(48)
        tile.set("item", r["item"], col)
        hrow.addWidget(tile, 0, QtCore.Qt.AlignTop)
        ncol = QtWidgets.QVBoxLayout()
        ncol.setSpacing(4)
        # the output name is a link into its full page (gear stats,
        # upgrades, drops) — rich text so long names still wrap. Only gear
        # and enchant outputs open elsewhere: gear's full page is the Gear
        # tab, enchant scrolls/gems the Enchants tab. Food, elixirs,
        # augments and components live on the Craft page — for them this
        # recipe card IS the item's page, so the name renders plain and
        # there's no OPEN IN GEAR button (the dedup: the same info no
        # longer shows on two pages).
        nm = html.escape(r["name"] + (f"  ×{r['count']}"
                                       if r["count"] > 1 else ""))
        out_it = idata.item(r["item"]) or {}
        can_open = bool(out_it) and (idata.is_gear(out_it)
                                     or is_enchant_item(out_it))
        name = WrapLabel(
            nm if not can_open else
            f'<a href="open" style="color:{col};text-decoration:none;">{nm}</a>')
        name.setMaximumWidth(640)
        name.setOpenExternalLinks(False)
        name.setTextInteractionFlags(QtCore.Qt.TextBrowserInteraction)
        if can_open:
            name.linkActivated.connect(lambda _: opener(r["item"]))
        bits = [r.get("type") or "?", r["item"]]
        if r.get("classes"):
            bits.append("CLASSES: " + " · ".join(r["classes"]))
        meta = WrapLabel("   ·   ".join(bits))
        meta.setObjectName("Mono")
        meta.setMaximumWidth(620)
        ncol.addWidget(name)
        ncol.addWidget(meta)
        if can_open:
            # explicit affordance: jump to the item's page — the Gear tab
            # for gear, the Enchants tab for enchant outputs
            open_btn = QtWidgets.QPushButton(
                "OPEN IN ENCHANTS  →" if is_enchant_item(out_it)
                else "OPEN IN GEAR  →")
            open_btn.setFlat(True)
            open_btn.setCursor(QtCore.Qt.PointingHandCursor)
            open_btn.setStyleSheet(
                f"QPushButton {{ color: {theme.ACCENT}; "
                f'font-family: "{theme.MONO_FONT}", "Consolas", monospace; '
                "font-size: 10px; font-weight: 700; letter-spacing: 1px; "
                "background: transparent; border: 0; padding: 0; text-align: left; }\n"
                f"QPushButton:hover {{ color: {theme.TEXT}; }}")
            open_btn.clicked.connect(lambda: opener(r["item"]))
            ncol.addWidget(open_btn, 0, QtCore.Qt.AlignLeft)
        hrow.addLayout(ncol, 1)
        if r.get("rarity"):
            hrow.addWidget(C.RarityTag(r["rarity"]), 0, QtCore.Qt.AlignTop)
        lay.addLayout(hrow)

        # job + level + gold cost line
        jrow = QtWidgets.QHBoxLayout()
        jrow.setSpacing(10)
        jl = QtWidgets.QLabel(f"{r['job_name'].upper()}  ·  LV {r['level']}")
        jl.setObjectName("Mono")
        jl.setStyleSheet(
            f"color:{theme.ACCENT};font-weight:700;background:transparent;")
        jrow.addWidget(jl)
        if r.get("cost"):
            gold = QtWidgets.QLabel(f"{r['cost']:g} GOLD")
            gold.setStyleSheet(
                f'color:{theme.ORANGE};font-family:"{theme.MONO_FONT}", "Consolas", monospace;'
                "font-size:12px;font-weight:700;background:transparent;")
            jrow.addWidget(gold)
        jrow.addStretch(1)
        lay.addLayout(jrow)

        # quantity — the batch count: every count below (materials,
        # gold, the Total Needed list) scales with CRAFT × N, updating in
        # place as the stepper changes so it keeps focus between clicks. All
        # render-local state lives in the `st` closure — the craft page and
        # the Items drawer can host cards simultaneously without one
        # clobbering the other's stepper refs.
        if qty_selector:
            qty = qty if qty is not None else max(1, getattr(self, "_craft_qty", 1))
            st: dict = {"recipe": r, "jump": jump,
                        "mats": [], "mat_head": None,
                        "shop_head": None, "shop_body": None}
            qrow = QtWidgets.QHBoxLayout()
            qrow.setSpacing(10)
            ql = QtWidgets.QLabel("CRAFT ×")
            ql.setStyleSheet(
                f'color:{theme.MUTED};font-family:"{theme.MONO_FONT}", "Consolas", monospace;'
                "font-size:11px;font-weight:700;letter-spacing:1px;"
                "background:transparent;")
            step = C.Stepper(value=qty, lo=1, hi=999)
            step.setFixedWidth(84)
            batch_gold = QtWidgets.QLabel("")
            batch_gold.setStyleSheet(
                f'color:{theme.ORANGE};font-family:"{theme.MONO_FONT}", "Consolas", monospace;'
                "font-size:13px;font-weight:700;background:transparent;")
            qrow.addWidget(ql)
            qrow.addWidget(step)
            qrow.addStretch(1)
            qrow.addWidget(batch_gold)
            add_btn = QtWidgets.QPushButton("ADD TO QUEUE")
            add_btn.setCursor(QtCore.Qt.PointingHandCursor)
            add_btn.setStyleSheet(
                f"QPushButton{{color:{theme.ACCENT};"
                f"background:{theme.with_alpha(theme.ACCENT, 18)};"
                f"border:1px solid {theme.with_alpha(theme.ACCENT, 90)};"
                "border-radius:4px;padding:6px 12px;font-size:11px;"
                "font-weight:700;letter-spacing:1px;}"
                f"QPushButton:hover{{color:{theme.TEXT};"
                f"background:{theme.with_alpha(theme.ACCENT, 45)};}}")
            add_btn.clicked.connect(
                lambda: self._craft_queue_add(r["item"], st))
            st["add_btn"] = add_btn
            st["gold"] = batch_gold
            qrow.addWidget(add_btn)
            lay.addLayout(qrow)
            step.valueChanged.connect(
                lambda v, s=st: self._craft_qty_changed(s, v))
            self._craft_qty_set_gold(r, qty, batch_gold)

        # the output's own authored stat affixes (starter gear, augments)
        own = idata.own_stats(idata.item(r["item"]) or {})
        if own:
            chips = QtWidgets.QHBoxLayout()
            chips.setSpacing(6)
            for s in own:
                c = QtWidgets.QLabel(f"{'−' if s['v'] < 0 else '+'}{abs(s['v'])} {s['n']}")
                cc = "#3fae6a" if s["v"] >= 0 else "#d95a5a"
                c.setStyleSheet(
                    f"color:{cc};background:{theme.with_alpha(cc, 22)};"
                    f"border:1px solid {theme.with_alpha(cc, 80)};border-radius:4px;"
                    "padding:2px 8px;font-weight:600;font-size:11px;")
                chips.addWidget(c)
            chips.addStretch(1)
            lay.addLayout(chips)

        # materials — the recipe's cost in items, scaled by the batch count
        mat_head = C.SectionHeader(
            "Materials",
            tag=f"{len(r['materials'])} ITEMS"
                + (f" · ×{qty}" if qty and qty > 1 else ""))
        if qty_selector:
            st["mat_head"] = mat_head

        if split:
            # variant B: materials | total needed side by side — the Total
            # Needed section header runs FULL WIDTH above the two
            # columns: the mono section header is ~350px on its own, so
            # squeezed into the right column it would force the card wider
            # than the pane and pop a horizontal scrollbar on the detail
            # (the checkbox bill stays right of the materials below)
            halves = QtWidgets.QHBoxLayout()
            halves.setSpacing(18)
            mleft = QtWidgets.QVBoxLayout()
            mleft.setSpacing(4)
            mleft.addWidget(mat_head)
            self._craft_materials_rows(mleft, r, qty, jump, mat_jump,
                                       st, qty_selector)
            mright = QtWidgets.QVBoxLayout()
            mright.setSpacing(4)
            if qty_selector:
                self._craft_bill_block(st, mright, r, qty, jump,
                                       header_lay=lay)
            mright.addStretch(1)
            halves.addLayout(mleft, 1)
            halves.addLayout(mright, 1)
            lay.addLayout(halves)
            self._craft_recipe_footer(lay, r, jump)
            lay.addStretch(1)
            return

        lay.addWidget(mat_head)
        body = QtWidgets.QVBoxLayout()
        body.setSpacing(4)
        # `st` exists only with the stepper (qty_selector); the materials
        # helper only touches it when qty_selector is set, so None is safe
        self._craft_materials_rows(body, r, qty, jump, mat_jump,
                                   st if qty_selector else None,
                                   qty_selector)
        lay.addLayout(body)

        # the queue view: every material in the chain summed into one
        # deduped line per item — the batch total (and its gold). The
        # Gear page's drawer keeps the indented Supply Chain tree below
        # instead (qty_selector=False).
        if qty_selector:
            st["shop_head"] = None
            st["shop_body"] = None
            self._craft_bill_block(st, lay, r, qty, jump)
        else:
            # supply chain — every material expanded down to raw (only when
            # the recipe has intermediate crafted materials worth expanding)
            chain = idata.craft_chain(r["item"])
            if chain and any(c["crafted"] for c in chain):
                leaves = sum(1 for c in chain if not c["crafted"])
                h = C.SectionHeader("Supply Chain", collapsible=True,
                                    tag=f"{leaves} RAW")
                lay.addWidget(h)
                chain_body = QtWidgets.QWidget()
                cb = QtWidgets.QVBoxLayout(chain_body)
                cb.setContentsMargins(0, 0, 0, 0)
                cb.setSpacing(2)
                for c in chain:
                    pad = 12 + (c["depth"] - 1) * 18
                    if c["crafted"]:
                        lbl = WrapLabel(
                            f'<a href="open" style="color:{theme.ACCENT};'
                            'text-decoration:none;">'
                            f"{c['count']:g}×  {c['name']}</a>")
                        lbl.setOpenExternalLinks(False)
                        lbl.setTextInteractionFlags(
                            QtCore.Qt.TextBrowserInteraction)
                        lbl.linkActivated.connect(
                            lambda _=None, mid=c["item"]: jump(mid))
                    else:
                        lbl = WrapLabel(f"{c['count']:g}×  {c['name']}")
                    lbl.setMaximumWidth(600)
                    lbl.setStyleSheet(
                        f'font-family:"{theme.MONO_FONT}", "Consolas", monospace;font-size:11px;'
                        f"color:{theme.TEXT};background:transparent;"
                        f"padding-left:{pad}px;")
                    cb.addWidget(lbl)
                h.collapsedChanged.connect(
                    lambda collapsed: chain_body.setVisible(not collapsed))
                lay.addWidget(chain_body)

        self._craft_recipe_footer(lay, r, jump)

        # drops from — the boss rows when the piece has a boss, else every
        # merged source (rift/dungeon first)
        drops = idata.shown_drops(r["item"])
        # the tag counts the sources the body actually shows (node size
        # variants merged) and pluralizes
        shown = len(idetail._deduped_drops(drops))
        lay.addWidget(C.SectionHeader(
            "Drops From",
            tag=f"{shown} SOURCE{'S' if shown != 1 else ''}"))
        if not drops:
            m = QtWidgets.QLabel("No drop sources — purchased or quest item.")
            m.setObjectName("Muted")
            m.setWordWrap(True)
            m.setMaximumWidth(620)
            lay.addWidget(m)
        else:
            lay.addLayout(idetail.build_drops_body(
                drops, on_codex_click=self._codex_jump_to_unit,
                on_expand=self._craft_refresh_scroll))

        lay.addStretch(1)

    def _craft_materials_rows(self, lay, r: dict, qty, jump, mat_jump,
                              st, qty_selector: bool) -> None:
        """Fill `lay` with the recipe's material rows: count + name (a link
        to the material's own card on the craft page), a CRAFTED pill that
        jumps to the material's recipe, and the raw materials' inline drop
        hints (long source lists become the collapsible kind groups)."""
        for m in r["materials"]:
            cell = QtWidgets.QVBoxLayout()
            cell.setSpacing(2)
            row = QtWidgets.QHBoxLayout()
            row.setSpacing(10)
            cnt = m["count"] * qty if qty else m["count"]
            # raw materials on the craft page are links to the material's
            # own card (drops + what uses it) — the Craftables view, merged
            # into the recipe card; crafted materials keep the CRAFTED pill
            # that jumps to their own recipe instead
            is_link = mat_jump is not None and not m["crafted"]
            if is_link:
                txt = WrapLabel(
                    f'<a href="open" style="color:{theme.ACCENT};'
                    'text-decoration:none;">'
                    f"{cnt:g}×  {html.escape(m['name'])}</a>")
                txt.setOpenExternalLinks(False)
                txt.setTextInteractionFlags(QtCore.Qt.TextBrowserInteraction)
                txt.linkActivated.connect(
                    lambda _=None, mid=m["item"]: mat_jump(mid))
            else:
                txt = WrapLabel(f"{cnt:g}×  {m['name']}")
                txt.setStyleSheet(f"color:{theme.TEXT};background:transparent;")
            if qty_selector:
                st["mats"].append((txt, m["name"], m["count"], is_link))
            row.addWidget(txt, 1)
            if m["crafted"]:
                row.addWidget(_crafted_tag(m["item"], jump), 0,
                              QtCore.Qt.AlignVCenter)
            cell.addLayout(row)
            # raw (non-crafted) materials: where they come from — the first
            # drop source inline, the full list in the tooltip (merged per
            # source, human location names only). The hints are the
            # FARMABLE sources: chests are one-time RNG opens (the
            # material's own card still lists them), and GATHER reads
            # first, then mobs.
            if not m["crafted"]:
                _craft_source_hint(cell, idata.shown_drops(m["item"]),
                                   self._codex_jump_to_unit,
                                   self._craft_refresh_scroll)
            lay.addLayout(cell)

    def _craft_bill_block(self, st, lay, r: dict, qty, jump,
                          header_lay=None) -> bool:
        """Append the Total Needed section — the summed shopping list across
        the whole chain, with COPY and the total gold — into `lay`.
        `header_lay` (the split card) hosts the section header full-width
        elsewhere. Returns False when the recipe has no intermediate crafted
        materials (nothing to aggregate)."""
        bill = idata.craft_bill(r["item"], qty)
        if not bill or not any(i["crafted"] for i in bill["items"]):
            return False

        # Toggles are removed from the recipe card to stop the UI reflash;
        # progress is tracked and interacted with only in the main Craft List.
        h = C.SectionHeader("Total Needed",
                            tag=self._craft_bill_tag(bill, checkable=False))

        def _export() -> tuple[str, dict]:
            qty = max(1, getattr(self, "_craft_qty", 1))
            return (f"{qty}×  {r['name']}",
                    idata.craft_bill(r["item"], qty))

        hrow = QtWidgets.QHBoxLayout()
        hrow.setSpacing(8)
        hrow.addWidget(h, 1)
        hrow.addWidget(self._craft_copy_btn(_export), 0,
                       QtCore.Qt.AlignVCenter)
        (header_lay or lay).addLayout(hrow)
        body = QtWidgets.QWidget()
        bl = QtWidgets.QVBoxLayout(body)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(2)
        st["shop_head"] = h
        st["shop_body"] = body
        self._craft_fill_bill(bl, bill, jump, checkable=False, head=h)
        lay.addWidget(body)
        return True

    def _craft_recipe_footer(self, lay, r: dict, jump=None) -> None:
        """The slim footer line under the card: the unlock recipe scroll.
        Clicking the line expands/collapses an inline drawer directly beneath
        it showing all drop sources for the recipe scroll."""
        unlock_id = r.get("unlock")
        if not unlock_id:
            return

        scroll_name = r.get("unlock_name") or idata.item_name(unlock_id) or unlock_id
        job_name = (r.get("job_name") or "").upper()
        lvl = r.get("level", 1)

        box = QtWidgets.QWidget()
        bl = QtWidgets.QVBoxLayout(box)
        bl.setContentsMargins(0, 4, 0, 0)
        bl.setSpacing(6)

        btn_row = QtWidgets.QHBoxLayout()
        btn_row.setSpacing(6)

        prefix = QtWidgets.QLabel("UNLOCKED BY  ·")
        prefix.setObjectName("Mono")
        prefix.setStyleSheet(
            f"color:{theme.MUTED};font-size:11px;font-family:'{theme.MONO_FONT}';background:transparent;")
        btn_row.addWidget(prefix)

        link = QtWidgets.QLabel(
            f'<a href="toggle" style="color:{theme.ACCENT};text-decoration:none;font-weight:700;">'
            f"{html.escape(scroll_name)}</a>")
        link.setTextInteractionFlags(QtCore.Qt.TextBrowserInteraction)
        link.setStyleSheet("background:transparent;")
        btn_row.addWidget(link)

        meta = QtWidgets.QLabel(f"·  {job_name}  ·  LV {lvl}")
        meta.setObjectName("Mono")
        meta.setStyleSheet(
            f"color:{theme.MUTED};font-size:11px;font-family:'{theme.MONO_FONT}';background:transparent;")
        btn_row.addWidget(meta)

        chevron = QtWidgets.QLabel("▸")
        chevron.setStyleSheet(
            f"color:{theme.ACCENT};font-size:12px;font-weight:bold;background:transparent;")
        btn_row.addWidget(chevron)
        btn_row.addStretch(1)
        bl.addLayout(btn_row)

        drawer = QtWidgets.QWidget()
        drawer.setVisible(False)
        dl = QtWidgets.QVBoxLayout(drawer)
        dl.setContentsMargins(8, 4, 8, 4)
        dl.setSpacing(6)

        drops = idata.shown_drops(unlock_id)
        if not drops:
            no_src = QtWidgets.QLabel(idata.no_source_reason(unlock_id))
            no_src.setObjectName("Muted")
            no_src.setWordWrap(True)
            dl.addWidget(no_src)
        else:
            dl.addLayout(idetail.build_drops_body(
                drops, on_codex_click=self._codex_jump_to_unit,
                on_expand=self._craft_refresh_scroll))

        bl.addWidget(drawer)

        def _toggle(_=None):
            is_vis = not drawer.isVisible()
            drawer.setVisible(is_vis)
            chevron.setText("▾" if is_vis else "▸")
            self._craft_refresh_scroll()

        link.linkActivated.connect(_toggle)
        lay.addWidget(box)

    def _craft_qty_changed(self, st: dict, qty: int) -> None:
        """CRAFT × N stepper handler: scale the already-rendered card in place
        (material counts, batch gold, the Total Needed list) instead of
        rebuilding it — a rebuild would destroy the stepper mid-click and
        lose the increment."""
        r = st["recipe"]
        self._craft_qty = qty
        self._craft_qty_set_gold(r, qty, st["gold"])
        for lbl, name, cnt, is_link in st["mats"]:
            if is_link:
                # keep the link markup — a plain setText would strip it
                lbl.setText(
                    f'<a href="open" style="color:{theme.ACCENT};'
                    'text-decoration:none;">'
                    f"{cnt * qty:g}×  {html.escape(name)}</a>")
            else:
                lbl.setText(f"{cnt * qty:g}×  {name}")
        h = st["mat_head"]
        if h is not None:
            h.set_tag(f"{len(r['materials'])} ITEMS"
                      + (f" · ×{qty}" if qty > 1 else ""))
        sh, body = st["shop_head"], st["shop_body"]
        if sh is not None and body is not None:
            bill = idata.craft_bill(r["item"], qty)
            if bill:
                bl = body.layout()
                # Toggles are removed from the recipe card; we use in-place
                # update for the text/tag to prevent flickering.
                if not self._craft_update_bill_in_place(bl, bill, sh, checkable=False):
                    self._craft_fill_bill(bl, bill, st["jump"],
                                          checkable=False, head=sh)
        # refresh whichever scroll area hosts the live card — the craft
        # page's, the Items drawer's, or both (each self-guards when the
        # other page isn't built)
        self._craft_refresh_scroll()
        ir = getattr(self, "_items_refresh_scroll", None)
        if ir is not None:
            ir()

    def _craft_qty_set_gold(self, r: dict, qty: int, gold) -> None:
        """The batch gold readout next to the stepper: recipe cost × qty
        (what you pay at the station). Blank when the recipe has no cost."""
        cost = r.get("cost")
        gold.setText(f"BATCH · {cost * qty:,.0f} GOLD" if cost else "")

    def _craft_open_material(self, item_id: str) -> None:
        """Open a raw material's card — where it drops + every recipe that
        uses it — in the detail pane, replacing the recipe card. Reached by
        clicking a raw material's name inside a recipe card; its Used In
        rows jump back to the consuming recipes, so the whole crafting loop
        stays browsable from the one flat list."""
        if not hasattr(self, "_craft_scroll"):
            return
        iid = f"mat:{item_id}"
        if getattr(self, "_craft_shown_id", None) == iid and self._craft_detail_lay.count() > 0:
            return
        self._craft_shown_id = iid
        it = idata.item(item_id)
        if not it:
            return

        sc = self._craft_scroll
        sc.setUpdatesEnabled(False)
        try:
            container = QtWidgets.QFrame()
            container.setObjectName("Card")
            lay = QtWidgets.QVBoxLayout(container)
            lay.setContentsMargins(18, 16, 18, 16)
            self._craft_render_material(lay, it)

            self._craft_detail = container
            self._craft_detail_lay = lay
            sc.setWidget(container)
            self._craft_refresh_scroll()
        finally:
            sc.setUpdatesEnabled(True)
            sc.update()

    def _craft_render_material(self, lay, it: dict) -> None:
        """A raw material's card: identity header, Drops From (the farming
        info), and the recipes that consume it — each a link that opens the
        recipe's card on this page."""
        col = theme.rarity_color(it.get("rarity") or "")
        hrow = QtWidgets.QHBoxLayout()
        hrow.setSpacing(12)
        tile = C.IconTile(48)
        tile.set("item", it["id"], col)
        hrow.addWidget(tile, 0, QtCore.Qt.AlignTop)
        ncol = QtWidgets.QVBoxLayout()
        ncol.setSpacing(4)
        nm = WrapLabel(it.get("name") or it["id"])
        nm.setMaximumWidth(640)
        nm.setStyleSheet(
            f"color:{col};font-size:19px;font-weight:700;background:transparent;")
        meta = WrapLabel(slot_label(it.get("type") or "?"))
        meta.setObjectName("Mono")
        meta.setMaximumWidth(620)
        ncol.addWidget(nm)
        ncol.addWidget(meta)
        hrow.addLayout(ncol, 1)
        if it.get("rarity"):
            hrow.addWidget(C.RarityTag(it["rarity"]), 0, QtCore.Qt.AlignTop)
        lay.addLayout(hrow)

        drops = idata.shown_drops(it["id"])
        shown = len(idetail._deduped_drops(drops))
        lay.addWidget(C.SectionHeader(
            "Drops From",
            tag=f"{shown} SOURCE{'S' if shown != 1 else ''}"))
        if not drops:
            m = QtWidgets.QLabel(idata.no_source_reason(it["id"]))
            m.setObjectName("Muted")
            m.setWordWrap(True)
            m.setMaximumWidth(620)
            lay.addWidget(m)
        else:
            lay.addLayout(idetail.build_drops_body(
                drops, on_codex_click=self._codex_jump_to_unit,
                on_expand=self._craft_refresh_scroll))

        uses = idata.recipes_using(it["id"])
        lay.addWidget(C.SectionHeader(
            "Used In",
            tag=f"{len(uses)} RECIPE{'S' if len(uses) != 1 else ''}"))
        body = QtWidgets.QVBoxLayout()
        body.setSpacing(2)
        for r in uses:
            row = QtWidgets.QHBoxLayout()
            row.setSpacing(10)
            cnt = next((m["count"] for m in r["materials"]
                        if m["item"] == it["id"]), 1)
            link = WrapLabel(
                f'<a href="open" style="color:{theme.ACCENT};'
                'text-decoration:none;">'
                f"{cnt:g}×  {html.escape(r['name'])}</a>")
            link.setOpenExternalLinks(False)
            link.setTextInteractionFlags(QtCore.Qt.TextBrowserInteraction)
            link.linkActivated.connect(
                lambda _=None, rid=r["item"]:
                self._craft_jump_to_recipe(rid))
            row.addWidget(link, 1)
            jl = QtWidgets.QLabel(
                f"{r['job_name'].upper()}  ·  LV {r['level']}")
            jl.setObjectName("Mono")
            jl.setStyleSheet(
                f'color:{theme.DIM};font-family:"{theme.MONO_FONT}", "Consolas", monospace;'
                "font-size:11px;background:transparent;")
            row.addWidget(jl, 0, QtCore.Qt.AlignVCenter)
            body.addLayout(row)
        lay.addLayout(body)
        lay.addStretch(1)

    def _craft_jump_to_recipe(self, item_id: str) -> None:
        """Jump to a crafted material's OWN recipe on this page: reset the job
        chips and the search so the tile is visible, select it, and scroll
        it into view. If the jump came from the Craft List tab, switch back
        to the Recipes tab first."""
        tabs = getattr(self, "_craft_tabs", None)
        if tabs is not None and tabs.currentText() != "Recipes":
            tabs.setCurrentText("Recipes")
            switch = getattr(self, "_craft_tab_changed", None)
            if switch is not None:
                switch("Recipes")
        if getattr(self, "_craft_job", "") or "":
            # no ALL chip — reset to the every-recipe default directly
            self._craft_job = ""
            for c in getattr(self, "_craft_chip_btns", {}).values():
                c.setChecked(False)
            self._craft_refilter()
        search = getattr(self, "_craft_search", None)
        if search is not None and search.text():
            search.setText("")
        for i in range(self._craft_list.count()):
            li = self._craft_list.item(i)
            if li.data(ID_ROLE) == item_id and not li.isHidden():
                prev = self._craft_list.currentRow()
                self._craft_list.setCurrentRow(i)
                self._craft_list.scrollToItem(li)
                # setCurrentRow on an already-selected row fires no signal
                # (jumping back from a material card), so render explicitly
                if self._craft_list.currentRow() == prev:
                    self._craft_show(li)
                break

    def _craft_open_in_items(self, item_id: str) -> None:
        """Open the recipe's output on its full page — the Gear tab for
        gear, the Enchants tab for enchant scrolls/gems. Switches the page
        to the tab that contains the item, clears its filters and unfolds
        its category so the row is guaranteed visible, then selects it.
        The gear page builds lazily, so _select_nav builds it on first
        use. Non-gear outputs stay here (their recipe card IS their page)."""
        it = idata.item(item_id)
        if not it:
            return
        if is_enchant_item(it):
            self._select_nav("gear")
            self._items_set_mode("Enchants")
            return
        if not idata.is_gear(it):
            if idata.is_craftable(item_id):
                self._craft_jump_to_recipe(item_id)
                return
            self._select_nav("gear")
            if hasattr(self, "_items_show_id"):
                self._items_show_id(item_id)
            return
        self._select_nav("gear")
        want = "Gear"
        if getattr(self, "_items_mode", "gear") != "gear":
            self._items_set_mode(want)
        if self._items_search.text():
            self._items_search.setText("")
        self._items_clear_filters()
        cat = idata.category(it.get("type"))
        if cat in getattr(self, "_items_folded", set()):
            self._items_folded.discard(cat)
            self._items_refilter()
        for i in range(self._items_list.count()):
            li = self._items_list.item(i)
            if li.data(idetail.ID_ROLE) == item_id:
                self._items_list.setCurrentRow(i)
                break

    def _craft_refresh_scroll(self) -> None:
        """Update the scroll area geometry and layout smoothly."""
        sc = getattr(self, "_craft_scroll", None)
        if sc is None:
            return
        w = sc.widget()
        if w is not None:
            w.updateGeometry()
