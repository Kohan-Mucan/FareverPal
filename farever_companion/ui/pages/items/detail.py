"""Item-page detail pane: the gear stat card grid, the staircase upgrade
matrix, the Drops From list and the In Recipes drawer. The stat / drops
builders live in support.py and upgrades_matrix.py.
"""
from __future__ import annotations

import html
import os

from PySide6 import QtCore, QtWidgets

from ... import theme
from ... import components as C
from ....data import items as idata
from . import support
from .skills_section import SkillBar
from .drops import (_deduped_drops, build_drops_body,
                    codex_drop_rows)
from .support import GLYPH_DOWN, GLYPH_RIGHT, GLYPH_RIFT, ID_ROLE
from .upgrades_matrix import build_upgrades_matrix_scroll

# Gear-scale slider cap failsafe — DORMANT until the game lets rare gear
# upgrade past its drop level. FAREVER_SOURCE_CAPS=1 re-enables it: the
# slider caps at each item's real-source level (the highest boss level in
# its drop data, or the authored zone-tier level for world-drop gear).
_USE_ITEM_SOURCE_CAPS = os.environ.get("FAREVER_SOURCE_CAPS") == "1"


def _item_scale_cap(item_id: str) -> int:
    """The top of the gear-scale slider: the global max gear level (35), or the
    item's real-source cap when the FAREVER_SOURCE_CAPS failsafe is on."""
    if _USE_ITEM_SOURCE_CAPS:
        return idata.item_scale_max_level(item_id)
    return 35


class ItemDetailMixin:
    # --- detail ----------------------------------------------------------
    def _items_show(self, current):
        if not hasattr(self, "_items_detail_lay"):
            return
        if current is not None and current.data(support.TYPE_HEADER_ROLE):
            return
        iid = current.data(ID_ROLE) if current is not None else None
        if not iid:
            self._items_shown_id = None
            self._items_clear_layout(self._items_detail_lay)
            lbl = QtWidgets.QLabel("Select an item to see its drops and "
                                   "max-level gear scaling.")
            lbl.setObjectName("Muted")
            lbl.setWordWrap(True)
            self._items_detail_lay.addWidget(lbl)
            self._items_detail_lay.addStretch(1)
            self._items_refresh_scroll()
            return
        if getattr(self, "_items_shown_id", None) == iid and self._items_detail_lay.count() > 1:
            return
        self._items_shown_id = iid
        self._items_clear_layout(self._items_detail_lay)
        lay = self._items_detail_lay
        self._items_stat_block = None   # stale ref after the teardown
        # any open In Recipes drawer dies with the rebuild too
        self._items_recipe_drawer = None
        self._items_recipe_drawer_ref = None
        self._items_inrecipe_rows = []
        self._items_skill_bar = None

        it = idata.item(iid) or {}
        if not it:
            return
        # the DISPLAY rarity — Guild Merchant gear (the starter weapons) is
        # sold as Rare by the town vendors, so the header shows the shop
        # quality, not the authored starter rarity
        rar = idata.item_display_rarity(it.get("id") or "")
        col = theme.rarity_color(rar)

        # header: icon + name + rarity tag + type
        hrow = QtWidgets.QHBoxLayout()
        hrow.setSpacing(12)
        tile = C.IconTile(48)
        tile.set("item", it["id"], col)
        hrow.addWidget(tile, 0, QtCore.Qt.AlignTop)
        ncol = QtWidgets.QVBoxLayout()
        ncol.setSpacing(4)
        name = QtWidgets.QLabel(it.get("name") or it["id"])
        # wrap long crafted names — an unbroken 700px+ line stretches the
        # window to the right (same fix as the Drops From location labels).
        # Cap too: when the display name IS the id (Recipe_Formula...) the
        # token can't wrap, so only a max-width keeps it from driving the
        # window's minimum size.
        name.setWordWrap(True)
        name.setAlignment(QtCore.Qt.AlignCenter)
        name.setMaximumWidth(640)
        name.setStyleSheet(
            f"color:{col};font-size:19px;font-weight:700;background:transparent;")
        meta = QtWidgets.QLabel()
        meta.setObjectName("Mono")
        typ_lbl = support.slot_label(it.get("type") or "?")
        bits = [typ_lbl]
        if it.get("classes"):
            bits.append(" · ".join(idata.class_label(c)
                                    for c in it["classes"]))
        elif it.get("faction"):
            bits.append(it["faction"])
        meta.setText(" — ".join(bits))
        meta.setWordWrap(True)
        meta.setAlignment(QtCore.Qt.AlignCenter)
        meta.setStyleSheet(
            f'color:{theme.MUTED};font-family:"{theme.MONO_FONT}", "Consolas", monospace;'
            "font-size:11px;letter-spacing:0.5px;background:transparent;")
        ncol.addWidget(name)
        ncol.addWidget(meta)
        hrow.addLayout(ncol, 1)
        lay.addLayout(hrow)

        # mounts/gliders: the codex card holds the real acquisition info (mob
        # drops, vendors, map spots) — a direct link into the Collection tab
        if (it.get("type") or "") in ("Mount", "GearGlider"):
            cx = QtWidgets.QPushButton("VIEW IN CODEX  →")
            cx.setFlat(True)
            cx.setCursor(QtCore.Qt.PointingHandCursor)
            cx.setStyleSheet(
                f"QPushButton {{ color: {theme.ACCENT}; "
                f'font-family: "{theme.MONO_FONT}", "Consolas", monospace; '
                "font-size: 10px; font-weight: 700; letter-spacing: 1px; "
                "background: transparent; border: 0; padding: 0; "
                "text-align: left; }\n"
                f"QPushButton:hover {{ color: {theme.TEXT}; }}")
            cx.clicked.connect(
                lambda: self._codex_jump_to_unit(
                    it["id"], it.get("name") or it["id"]))
            lay.addWidget(cx, 0, QtCore.Qt.AlignLeft)

        # recipe scrolls (type 'Recipe'): the item IS a recipe unlock — the
        # CRAFTED-style tag jumps to the recipe it teaches on the Craft page
        # (resolved through the scroll's unlockSource, like crafted gear's
        # CRAFTED tag jumps to its own recipe)
        if (it.get("type") or "") == "Recipe":
            unlocked = idata.recipe_unlocked_by(it["id"])
            if unlocked:
                utag = QtWidgets.QPushButton(
                    f"UNLOCKS · {unlocked['name']}")
                utag.setCursor(QtCore.Qt.PointingHandCursor)
                utag.setStyleSheet(
                    f"QPushButton{{color:{theme.GOLD};background:{theme.with_alpha(theme.GOLD, 18)};"
                    f"border:1px solid {theme.with_alpha(theme.GOLD, 80)};"
                    "border-radius:4px;padding:2px 9px;font-weight:700;"
                    "font-size:10px;letter-spacing:1px;}"
                    f"QPushButton:hover{{background:{theme.with_alpha(theme.GOLD, 32)};}}")
                utag.clicked.connect(
                    lambda: self._items_open_in_craft(unlocked["item"]))
                lay.addWidget(utag, 0, QtCore.Qt.AlignLeft)

        if idata.is_gear(it):
            lay.addWidget(self._items_farm_btn(it["id"]),
                          0, QtCore.Qt.AlignLeft)

        # the item's OWN stat values (authored affixes): chips like the viewer
        # (+2 Vitality · +2 Strength, +20 Critical / -20 Fervor on augments).
        # Gear skips them — a gear piece's own stats ARE the base row of the
        # UPGRADE LADDER below, so the chips would just repeat it (starter
        # weapons like Apprentice's Grimoire showed +3 Intellect twice).
        # Augments / consumables are not gear, so they keep the chips.
        own = idata.own_stats(it)
        if own and not idata.is_gear(it):
            chips = QtWidgets.QHBoxLayout()
            chips.setSpacing(6)
            for s in own:
                c = QtWidgets.QLabel(f"{'−' if s['v'] < 0 else '+'}{abs(s['v'])} {s['n']}")
                pos = s["v"] >= 0
                cc = "#3fae6a" if pos else "#d95a5a"
                c.setStyleSheet(
                    f"color:{cc};background:{theme.with_alpha(cc, 22)};"
                    f"border:1px solid {theme.with_alpha(cc, 80)};border-radius:4px;"
                    "padding:2px 8px;font-weight:600;font-size:11px;")
                chips.addWidget(c)
            chips.addStretch(1)
            lay.addLayout(chips)

        # weapons: the damage line — real game data (base-attack affinity +
        # WeaponPower ratio; the game derives the actual number at runtime)
        atk = idata.weapon_attack(it)
        if atk:
            arow = QtWidgets.QHBoxLayout()
            arow.setSpacing(10)
            acol = idata.affinity_color(atk["affinity"])
            dmg = QtWidgets.QLabel(
                f"{atk['affinity']} · {atk['ratio']:g}× Weapon Power")
            dmg.setStyleSheet(
                f'color:{acol};font-family:"{theme.MONO_FONT}", "Consolas", monospace;'
                f"font-size:13px;font-weight:700;background:transparent;")
            tag = QtWidgets.QLabel("DAMAGE")
            tag.setStyleSheet(
                f"color:{theme.DIM};font-size:10px;letter-spacing:1px;"
                "background:transparent;")
            arow.addWidget(tag)
            arow.addWidget(dmg)
            arow.addStretch(1)
            lay.addLayout(arow)

        # gear scaling (gear only) — level slider + the merged rarity ×
        # upgrade table (base states and step stats in one view, port of the
        # Item Lookup viewer's computeStats). Built by
        # _items_render_stat_block so level changes swap just this block in
        # place — rebuilding the whole detail pane on every change made the
        # UI flash.
        if idata.is_gear(it):
            # the ladder opens on default level 25 (scalable up to max 35).
            # Crafted gear is locked at its FIXED level instead, handled inside
            # _items_render_stat_block.
            max_lvl = _item_scale_cap(it["id"])
            if not hasattr(self, "_items_level") \
                    or self._items_item_id != it["id"]:
                self._items_level = min(25, max_lvl)
                self._items_item_id = it["id"]
            stat_block = self._items_render_stat_block(it, max_lvl)
            self._items_stat_block = stat_block
            lay.addWidget(stat_block)

        # drops from — the boss rows when the item has a boss (the random
        # faction crate/mob rows are noise), else every merged source
        drops = idata.shown_drops(it["id"])
        # mounts/gliders: the codex card authors the exact vendors and mobs
        # (the scan infers loot-table families) — prefer it when the card
        # has acquisition data; regular gear resolves no card rows and keeps
        # the scan
        if (it.get("type") or "") in ("Mount", "GearGlider"):
            drops = codex_drop_rows(it["id"]) or drops
        db = QtWidgets.QWidget()
        dl2 = QtWidgets.QVBoxLayout(db)
        dl2.setContentsMargins(0, 0, 0, 0)
        dl2.setSpacing(10)
        # the tag counts the sources the body actually shows (node size
        # variants merged) and pluralizes — boss-only rows are usually a
        # single source
        shown = len(_deduped_drops(drops))
        dl2.addWidget(C.SectionHeader(
            "Drops From",
            tag=f"{shown} SOURCE{'S' if shown != 1 else ''}"))
        # crafted gear: the CRAFTED · L tag (itself the craft jump) and the
        # job that makes the piece live UNDER the header, in the drop
        # location window — not on the title row.
        fixed = idata.item_fixed_level(it)
        recipe = idata.recipe(it["id"])
        if fixed or recipe:
            crow = QtWidgets.QHBoxLayout()
            crow.setSpacing(10)
            if fixed:
                # the crafted level tag IS the craft jump — clicking it
                # opens the recipe on the Craft page (the old button was
                # merged into the tag itself)
                ctag = QtWidgets.QPushButton(f"CRAFTED · L{fixed}")
                ctag.setCursor(QtCore.Qt.PointingHandCursor)
                ctag.setStyleSheet(
                    f"QPushButton{{color:{theme.GOLD};background:{theme.with_alpha(theme.GOLD, 18)};"
                    f"border:1px solid {theme.with_alpha(theme.GOLD, 80)};"
                    "border-radius:4px;padding:2px 9px;font-weight:700;"
                    "font-size:10px;letter-spacing:1px;}"
                    f"QPushButton:hover{{background:{theme.with_alpha(theme.GOLD, 32)};}}")
                ctag.clicked.connect(
                    lambda: self._items_open_in_craft(it["id"]))
                crow.addWidget(ctag, 0, QtCore.Qt.AlignVCenter)
            if recipe:
                # the job + station level that crafts the piece, next to the
                # CRAFTED tag (single copy — the gear block shows the level).
                # Clicking it jumps to the Craft page too (same as the tag)
                job = QtWidgets.QPushButton(
                    f"{recipe['job_name'].upper()} · LV {recipe['level']}")
                job.setFlat(True)
                job.setCursor(QtCore.Qt.PointingHandCursor)
                job.setStyleSheet(
                    f"QPushButton{{color:{theme.DIM};"
                    f'font-family:"{theme.MONO_FONT}", "Consolas", monospace;'
                    "font-size:10px;letter-spacing:0.5px;background:transparent;"
                    "border:0;padding:0;}"
                    f"QPushButton:hover{{color:{theme.ACCENT};}}")
                job.clicked.connect(
                    lambda: self._items_open_in_craft(it["id"]))
                crow.addWidget(job, 0, QtCore.Qt.AlignVCenter)
            crow.addStretch(1)
            dl2.addLayout(crow)
        # the acquisition note the scan stamps on cache-obtained gear (the
        # Rift Demon set, the Demon weapons, the Corrupted Gifts): the piece
        # is opened from a PLAYER-SCALE chest that rolls at your level, not
        # just bought — surfaced under the header so the real acquisition is
        # visible alongside the vendor row that sells the cache.
        note = idata.acquisition_note(it["id"])
        if note:
            nlab = QtWidgets.QLabel(GLYPH_RIFT + "  " + note)
            nlab.setObjectName("Muted")
            nlab.setWordWrap(True)
            nlab.setMaximumWidth(620)
            dl2.addWidget(nlab)
        if not drops:
            # crafted pieces skip the reason line — the CRAFTED tag above
            # already explains why there are no drop sources
            if not (fixed or recipe):
                m = QtWidgets.QLabel(idata.no_source_reason(it["id"]))
                m.setObjectName("Muted")
                m.setWordWrap(True)
                m.setMaximumWidth(620)
                dl2.addWidget(m)
        else:
            dl2.addLayout(build_drops_body(
                drops, on_codex_click=self._codex_jump_to_unit,
                on_expand=self._items_refresh_scroll,
                item_id=it["id"]))
        lay.addWidget(db)

        # weapons: the skill list — the ACTION BAR (mockup 02): one square
        # tile per skill in the game's authored order (base attacks ->
        # combo -> actives -> passive), each with the skill's atlas icon
        # and name beneath. Clicking a tile focuses that skill's full
        # effect (name, type chip, resolved description) in the detail bar
        # below — the compiler stamps each weapon's skill ids and
        # data/skills.py resolves the ::token:: templates into readable
        # text; skills the sheet gives no description to show name + type
        # only, never a fabricated line. Placed UNDER the Drops From list
        # — the skill section is the weapon's reference detail, not part
        # of the gear scaling block above it.
        ws = idata.weapon_skills(it["id"])
        if ws:
            # the ACTION BAR section (mockup 02) — tiles + focused skill
            # detail bar; which skills the current search query matches
            # (the same rule as the tile chips: query must NOT match the
            # item's own name/id/type) get the accent ring, so the
            # highlight explains a skill-driven match
            # trinkets are only ever Rare — their passive skill tiles
            # read in the rarity blue instead of the passive green
            force = (theme.rarity_color("Rare")
                     if (it.get("type") or "") == "GearTrinket" else None)
            self._items_skill_bar = SkillBar(
                ws, self._items_matched_skill_names(it),
                force_color=force,
                on_focus_changed=self._items_refresh_scroll)
            lay.addWidget(self._items_skill_bar)

        # in recipes — every recipe that consumes this item as a material
        # (the reverse of the craft page's material list). Clicking a row
        # toggles an inline drawer with the full recipe card (materials,
        # cost, drops) — rendered by the craft page's own renderer so the
        # two views stay identical, one drawer open at a time.
        uses = idata.recipes_using(it["id"])
        if uses:
            rb = QtWidgets.QWidget()
            rl = QtWidgets.QVBoxLayout(rb)
            rl.setContentsMargins(0, 0, 0, 0)
            rl.setSpacing(10)
            rl.addWidget(C.SectionHeader("In Recipes",
                                         tag=f"{len(uses)} RECIPES"))
            body = QtWidgets.QVBoxLayout()
            body.setSpacing(2)
            cap = 12
            for r in uses[:cap]:
                row = QtWidgets.QHBoxLayout()
                row.setSpacing(8)
                cnt = next((m["count"] for m in r["materials"]
                            if m["item"] == it["id"]), 1)
                chevron = QtWidgets.QLabel(GLYPH_RIGHT)
                chevron.setObjectName("Mono")
                chevron.setFixedWidth(14)
                chevron.setStyleSheet(
                    f"color:{theme.ACCENT};font-size:11px;"
                    "background:transparent;")
                nm = html.escape(r["name"])
                link = QtWidgets.QLabel(
                    f'<a href="open" style="color:{theme.ACCENT};'
                    f'text-decoration:none;">{cnt:g}×  {nm}</a>')
                link.setWordWrap(True)
                link.setMaximumWidth(600)
                link.setOpenExternalLinks(False)
                link.setTextInteractionFlags(
                    QtCore.Qt.LinksAccessibleByMouse)
                row.addWidget(chevron, 0, QtCore.Qt.AlignTop)
                row.addWidget(link, 1)
                jl = QtWidgets.QLabel(
                    f"{r['job_name'].upper()}  ·  LV {r['level']}")
                jl.setObjectName("Mono")
                jl.setStyleSheet(
                    f'color:{theme.DIM};font-family:"{theme.MONO_FONT}", "Consolas", monospace;'
                    "font-size:11px;background:transparent;")
                # keep the metadata at its full text width — when the row
                # is tight the recipe name (which wraps) gives way instead
                # of this label getting clipped
                jl.setMinimumWidth(jl.sizeHint().width())
                row.addWidget(jl, 0, QtCore.Qt.AlignVCenter)
                # the same right-aligned cost line as the craft list rows:
                # distinct material count + gold cost, so recipes compare at
                # a glance without expanding the drawer
                parts = [f"{len(r['materials'])} MAT"]
                if r.get("cost"):
                    parts.append(f"{r['cost']:g} GOLD")
                sm = QtWidgets.QLabel(" · ".join(parts))
                sm.setObjectName("Mono")
                sm.setStyleSheet(
                    f'color:{theme.MUTED};font-family:"{theme.MONO_FONT}", "Consolas", monospace;'
                    "font-size:11px;background:transparent;")
                sm.setMinimumWidth(sm.sizeHint().width())
                row.addWidget(sm, 0, QtCore.Qt.AlignVCenter)
                body.addLayout(row)
                ri = body.count() - 1
                # keep the row handles so the headless driver (FAREVER_OPEN_ITEM
                # / FAREVER_OPEN_DRAWER) can expand the drawer without walking
                # the layout graph
                self._items_inrecipe_rows.append(
                    (r["item"], chevron, body, ri))
                link.linkActivated.connect(
                    lambda _=None, rid=r["item"], ch=chevron, b=body,
                    idx=ri: self._items_toggle_recipe_drawer(rid, ch, b, idx))
            if len(uses) > cap:
                more = QtWidgets.QLabel(f"+ {len(uses) - cap} more recipes")
                more.setObjectName("Muted")
                body.addWidget(more)
            rl.addLayout(body)
            lay.addWidget(rb)

        lay.addStretch(1)
        self._items_refresh_scroll()

    def _items_matched_skill_names(self, it: dict) -> set[str]:
        """Skill names the current search query matches on this item — only
        when the query doesn't also match the item's own name/id/type (the
        tile chips use the same rule)."""
        q = self._items_search.text().strip()
        if not q:
            return set()
        hay = (f"{it.get('name') or ''} {it.get('id') or ''} "
               f"{it.get('type') or ''}").lower()
        if q.lower() in hay:
            return set()
        return set(idata.matched_skill_labels(it.get("id") or "", q))

    def _items_update_skill_highlight(self) -> None:
        """Re-apply the matched-skill ring to the action-bar tiles in place
        as the search query changes. No-op when no weapon is open."""
        bar = getattr(self, "_items_skill_bar", None)
        if bar is None:
            return
        cur = self._items_list.currentItem()
        it = idata.item(cur.data(ID_ROLE)) if cur is not None else {}
        bar.update_highlight(self._items_matched_skill_names(it))

    def _items_open_in_craft(self, item_id: str) -> None:
        """Jump to this item's recipe on the Craft page (Recipes mode,
        recipe selected + scrolled into view). The craft page builds
        lazily, so _select_nav both builds it and switches to it."""
        self._select_nav("craft")
        self._craft_jump_to_recipe(item_id)

    def _items_toggle_recipe_drawer(self, item_id: str, chevron, body,
                                    row_idx: int) -> None:
        """Toggle an inline recipe drawer under an In Recipes row (reusing
        the craft page's renderer). One drawer at a time."""
        ref = getattr(self, "_items_recipe_drawer_ref", None)
        drawer = getattr(self, "_items_recipe_drawer", None)
        if ref is not None and drawer is not None:
            for i in range(body.count()):
                if body.itemAt(i).widget() is drawer:
                    body.takeAt(i)
                    break
            drawer.hide()
            drawer.setParent(None)
            drawer.deleteLater()
            self._items_recipe_drawer = None
            ref[1].setText(GLYPH_RIGHT)
            self._items_recipe_drawer_ref = None
            if ref[0] == item_id:       # clicked the open row -> collapse
                self._items_refresh_scroll()
                return
        drawer = QtWidgets.QWidget()
        dl = QtWidgets.QVBoxLayout(drawer)
        dl.setContentsMargins(24, 4, 0, 6)
        dl.setSpacing(8)
        self._items_recipe_drawer = drawer
        self._items_recipe_drawer_ref = (item_id, chevron)
        r = idata.recipe(item_id)
        if r:
            self._craft_render_recipe(dl, r, jump=self._items_recipe_drill, qty_selector=True)
        chevron.setText(GLYPH_DOWN)
        body.insertWidget(row_idx + 1, drawer)
        self._items_refresh_scroll()

    def _items_recipe_drill(self, item_id: str) -> None:
        """Inline drill-down inside an open In Recipes drawer: re-render it
        with a crafted material's own recipe."""
        drawer = getattr(self, "_items_recipe_drawer", None)
        if drawer is None:
            return
        dl = drawer.layout()
        ItemDetailMixin._items_clear_layout(dl)
        r = idata.recipe(item_id)
        if r:
            self._craft_render_recipe(dl, r, jump=self._items_recipe_drill, qty_selector=True)
        self._items_refresh_scroll()

    def _items_headless_open(self, item_id: str, drawer: bool) -> bool:
        """Test hook: drive the Items page to a headless-screenshot state —
        select the item, optionally expand its first In Recipes drawer and
        scroll it on screen. Returns False when the state can't be built.
        Only rows VISIBLE on the current tab qualify: the leftover non-gear
        items (Gold, tools, ...) are no longer in the browse list, so the
        driver fails loudly for them instead of selecting a dead row."""
        if not hasattr(self, "_items_list") or not hasattr(self,
                                                           "_items_detail_lay"):
            return False
        for i in range(self._items_list.count()):
            row = self._items_list.item(i)
            if row is not None and not row.isHidden() \
                    and row.data(ID_ROLE) == item_id:
                self._items_list.setCurrentItem(row)
                break
        else:
            return False
        if drawer:
            rows = getattr(self, "_items_inrecipe_rows", [])
            if not rows:
                return False
            rid, chevron, body, idx = rows[0]
            self._items_toggle_recipe_drawer(rid, chevron, body, idx)
            # the In Recipes section sits below the fold — reveal it once the
            # scroll area has re-measured the taller detail card
            sc = getattr(self, "_items_scroll", None)
            if sc is not None:
                def _reveal():
                    sb = sc.verticalScrollBar()
                    sb.setValue(sb.maximum())
                QtCore.QTimer.singleShot(120, _reveal)
        return True

    def _items_render_stat_block(self, it: dict, max_lvl: int):
        """Build the gear-scale stat block: the UPGRADE LADDER header + the
        level slider, then the staircase matrix — every rarity's ladder in
        one compact table. Kept separate from _items_show so level changes
        re-render just this section in place (the old full-pane rebuild
        flashed the UI). Crafted gear renders a FIXED L{n} readout instead
        of the slider; armor/jewelry render base-only; Guild Merchant town
        weapons keep the slider but restricted to the towns that sell them."""
        fixed = idata.item_fixed_level(it)
        lvl = fixed if fixed else min(self._items_level, max_lvl)
        stat_block = QtWidgets.QWidget()
        stb = QtWidgets.QVBoxLayout(stat_block)
        stb.setContentsMargins(0, 0, 0, 0)
        stb.setSpacing(10)
        ladder = idata.upgrade_ladder(it, level=lvl)

        head = QtWidgets.QHBoxLayout()
        head.setSpacing(10)
        if ladder:
            hdr = C.SectionHeader("UPGRADE LADDER")
            head.addWidget(hdr)
        head.addStretch(1)

        if fixed:
            val = QtWidgets.QLabel(f"FIXED L{fixed}")
        else:
            val = QtWidgets.QLabel(f"L{lvl}")
        val.setObjectName("Mono")
        val.setStyleSheet(
            f'color:{theme.MUTED};font-family:"{theme.MONO_FONT}", "Consolas", monospace;'
            "font-size:12px;font-weight:700;letter-spacing:0.5px;background:transparent;padding-right:4px;")
        if fixed:
            # the FIXED L readout alone at the right — the recipe's job +
            # level already sits next to the CRAFTED tag under the header
            rcol = QtWidgets.QVBoxLayout()
            rcol.setSpacing(0)
            rcol.addWidget(val, 0, QtCore.Qt.AlignRight)
            head.addLayout(rcol)
            stb.addLayout(head)
        else:
            head.addWidget(val)
            steps = [1] + [x for x in range(5, max_lvl + 1, 5)]
            if len(steps) > 1:
                slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
                slider.setRange(0, len(steps) - 1)
                slider.setSingleStep(1)
                slider.setPageStep(1)
                closest = min(steps, key=lambda s: abs(s - lvl))
                slider.setValue(steps.index(closest))
                slider.setFixedWidth(140)
                slider.setStyleSheet(
                    f"QSlider::groove:horizontal{{height:4px;background:{theme.BORDER};"
                    f"border-radius:2px;}}"
                    f"QSlider::handle:horizontal{{width:14px;margin:-5px 0;"
                    f"background:{theme.ACCENT};border-radius:7px;}}")
                head.addWidget(slider)
                slider.valueChanged.connect(self._items_level_changed)
            stb.addLayout(head)

        if ladder:
            # crafted gear is made at a FIXED level with set stats, and
            # armor + jewelry can't be upgraded in-game yet (weapons can)
            # — render only the base column, not the step gains (the steps
            # stay in the data; they're hidden until those upgrades land)
            stb.addWidget(build_upgrades_matrix_scroll(
                ladder, level_label=None,
                show_steps=not (fixed or idata.upgrade_locked(it["id"]))))

        return stat_block

    def _items_rerender_stat_block(self) -> None:
        """Swap the gear-scale stat block in place on a level change — only
        that section is affected, so the header/drops/recipes (and the
        scroll position) stay untouched."""
        cur = self._items_list.currentItem()
        if cur is None:
            return
        it = idata.item(cur.data(ID_ROLE)) or {}
        if not it or not idata.is_gear(it):
            return
        old = getattr(self, "_items_stat_block", None)
        if old is None:
            return
        lay = self._items_detail_lay
        idx = lay.indexOf(old)
        if idx < 0:
            return
        self._items_stat_block = self._items_render_stat_block(
            it, _item_scale_cap(it["id"]))
        lay.removeWidget(old)
        old.hide()
        old.setParent(None)
        old.deleteLater()
        lay.insertWidget(idx, self._items_stat_block)
        self._items_refresh_scroll()

    def _items_refresh_scroll(self) -> None:
        """Refit the detail card to its content on the next event-loop pass
        (the layout recomputes its sizeHint a tick later)."""
        sc = getattr(self, "_items_scroll", None)
        if sc is None:
            return

        def _fix():
            fit = getattr(sc, "_fit", None)
            if fit is not None:
                fit()

        QtCore.QTimer.singleShot(0, _fix)

    @staticmethod
    def _items_clear_layout(lay: QtWidgets.QLayout) -> None:
        """Detach + schedule deletion of every widget in a layout, recursing
        into nested sub-layouts."""
        while lay.count():
            item = lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()
            elif item.layout() is not None:
                ItemDetailMixin._items_clear_layout(item.layout())

    def _items_level_changed(self, value: int) -> None:
        """Gear-scale level slider: map slider step index to allowed level
        (1, 5, 10, ..., 50) and recompute the stat block in place."""
        cur = self._items_list.currentItem()
        it = idata.item(cur.data(ID_ROLE)) if cur is not None else {}
        max_lvl = _item_scale_cap(it.get("id") or "")
        steps = [1] + [x for x in range(5, max_lvl + 1, 5)]
        if 0 <= value < len(steps):
            snapped = steps[value]
        else:
            snapped = min(25, max_lvl)
        if hasattr(self, "_items_level") and self._items_level == snapped:
            return
        self._items_level = snapped
        self._items_rerender_stat_block()
