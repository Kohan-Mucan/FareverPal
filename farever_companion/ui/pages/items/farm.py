"""The gear-farm tab — the Items page's Farm view.

FarmMixin renders the saved farm list (planner.json via the pure-data
planner module, fully offline): one row per piece with its class tag,
base stats, drop-source cell and trash button, plus class filter chips.
The ADD TO FARM pill on gear detail cards pushes into this list; the tab
rebuilds from the saved ids whenever shown.
"""
from __future__ import annotations

import time

from PySide6 import QtCore, QtWidgets

from ... import theme
from ... import components as C
from ....data import icons
from ....data import items as idata
from .... import planner as pdata
from .drops import _KIND_ICON, _NPC_SPRITES, _row_label
from .support import WrapLabel, FlowLayout

# the drop-source cell icon size: the pixmap and the tile's fixed size
# must match, so a bump here keeps the sprite and its 32px tile aligned
_ROW_ICON = 32


class FarmMixin:
    """The Farm tab: saved gear list + class chips + per-row remove."""

    def _items_farm_btn(self, item_id: str) -> QtWidgets.QPushButton:
        """The gold ADD TO FARM pill shown on gear detail cards."""
        btn = QtWidgets.QPushButton("ADD TO FARM")
        btn.setCursor(QtCore.Qt.PointingHandCursor)
        btn.setStyleSheet(
            f"QPushButton{{color:{theme.GOLD};"
            f"background:{theme.with_alpha(theme.GOLD, 14)};"
            f"border:1px solid {theme.with_alpha(theme.GOLD, 80)};"
            "border-radius:4px;padding:5px 12px;font-size:11px;"
            "font-weight:700;letter-spacing:1px;}"
            f"QPushButton:hover{{background:{theme.with_alpha(theme.GOLD, 26)};}}")
        btn.clicked.connect(lambda: self._items_farm_click(btn, item_id))
        return btn

    def _items_farm_click(self, btn, item_id: str) -> None:
        pdata.add_gear(item_id)
        self._items_farm_sync_tab()
        btn.setText("✓ FARMED")
        QtCore.QTimer.singleShot(
            1400, lambda b=btn: self._items_farm_restore(b))

    def _items_farm_restore(self, btn) -> None:
        try:
            btn.setText("ADD TO FARM")
        except RuntimeError:
            pass

    def _items_farm_setup(self) -> QtWidgets.QScrollArea:
        """Build the Farm tab body once (page assembly)."""
        self._farm_class: str = ""   # "" = All (no class filter)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        body = QtWidgets.QWidget()
        self._farm_lay = QtWidgets.QVBoxLayout(body)
        self._farm_lay.setContentsMargins(0, 0, 0, 0)
        self._farm_lay.setSpacing(12)
        scroll.setWidget(body)
        return scroll

    def _items_farm_sync_tab(self) -> None:
        """The Farm tab carries the saved count in gold, hidden at 0 — the
        header itself has no count tag (the tab IS the counter)."""
        tabs = getattr(self, "_items_tabs", None)
        if tabs is None or not hasattr(tabs, "set_text"):
            return
        n = len(pdata.gear_item_ids())
        if n:
            suffix = "Item" if n == 1 else "Items"
            tabs.set_text("Farm", f"Farm · {n} {suffix}")
        else:
            tabs.set_text("Farm", "Farm")

    def _items_show_farm(self) -> None:
        """Rebuild the Farm tab from the saved planner.json ids."""
        lay = getattr(self, "_farm_lay", None)
        if lay is None:
            return
        self._items_farm_sync_tab()
        self._items_clear_layout(lay)
        ids = pdata.gear_item_ids()
        hrow = QtWidgets.QHBoxLayout()
        hrow.setSpacing(8)
        hrow.addWidget(C.SectionHeader("Gear Farm"), 1)
        if ids:
            hrow.addWidget(self._items_farm_copy_btn(), 0,
                           QtCore.Qt.AlignVCenter)
        lay.addLayout(hrow)
        if not ids:
            msg = WrapLabel("Farm is empty — browse armor and gear on this "
                            "page and press ADD TO FARM to build a boss "
                            "farming list.")
            msg.setObjectName("Muted")
            msg.setMaximumWidth(620)
            lay.addWidget(msg)
            lay.addStretch(1)
            return

        # class filter chips: single-select over an always-available All
        # chip, rebuilt per render so the checked state tracks the pick
        classes = sorted(self._items_farm_classes(),
                         key=lambda c: idata.class_label(c).lower())
        if classes:
            chip_box = QtWidgets.QWidget()
            chips = FlowLayout(chip_box)
            chips.setContentsMargins(0, 0, 0, 0)
            all_chip = C.FilterChip("All", checked=not self._farm_class,
                                    parent=chip_box)
            all_chip.toggled.connect(self._farm_toggle_all)
            chips.addWidget(all_chip)
            for cls in classes:
                chip = C.FilterChip(idata.class_label(cls),
                                    checked=self._farm_class == cls,
                                    color=theme.class_color(cls),
                                    parent=chip_box)
                chip.toggled.connect(
                    lambda on, c=cls: self._farm_toggle_class(c, on))
                chips.addWidget(chip)
            lay.addWidget(chip_box)

        # muted mono column header (ICON | ITEM · CLASS | DROPS FROM) mirroring
        # the rows' geometry, so the rows read as a tidy grid
        hdr = QtWidgets.QHBoxLayout()
        hdr.setContentsMargins(12, 0, 12, 0)
        hdr.setSpacing(12)
        for i, text in enumerate(("ICON", "ITEM · CLASS", "DROPS FROM")):
            hl = QtWidgets.QLabel(text)
            hl.setObjectName("Mono")
            hl.setStyleSheet(
                f"color:{theme.DIM};font-size:9px;font-weight:700;"
                "letter-spacing:1px;background:transparent;")
            if i == 0:
                hl.setFixedWidth(36)
                hdr.addWidget(hl, 0, QtCore.Qt.AlignVCenter)
            elif i == 1:
                hdr.addWidget(hl, 1, QtCore.Qt.AlignVCenter)
            else:
                hdr.addWidget(hl, 0, QtCore.Qt.AlignRight)
        hdr.addSpacing(38)   # trash button + its spacing (rows only)
        lay.addLayout(hdr)

        for iid in ids:
            it = idata.item(iid)
            if not it:
                continue
            if self._farm_class and it.get("classes") \
                    and not (set(it["classes"]) & {self._farm_class}):
                continue
            lay.addWidget(self._items_farm_row(iid))
        lay.addStretch(1)

    def _farm_toggle_class(self, cls: str, on: bool) -> None:
        """Single-select: picking a class replaces the current pick;
        unchecking it falls back to All."""
        self._farm_class = cls if on else ""
        self._items_show_farm()

    def _farm_toggle_all(self, on: bool) -> None:
        if on:
            self._farm_class = ""
            self._items_show_farm()

    def _items_farm_classes(self) -> set[str]:
        """The classes of the saved farm items (the chip row's options)."""
        out: set[str] = set()
        for iid in pdata.gear_item_ids():
            it = idata.item(iid)
            if it:
                out.update(it.get("classes") or [])
        return out

    def _items_farm_remove(self, item_id: str) -> None:
        pdata.remove_gear(item_id)
        self._items_show_farm()

    # --- copy to clipboard -------------------------------------------------
    def _items_farm_copy_btn(self) -> QtWidgets.QPushButton:
        """A small flat COPY button (like Total Needed's) that puts the farm
        list on the clipboard."""
        btn = QtWidgets.QPushButton("COPY")
        btn.setCursor(QtCore.Qt.PointingHandCursor)
        btn.setStyleSheet(
            f"QPushButton{{color:{theme.MUTED};"
            f"background:{theme.with_alpha(theme.ACCENT, 12)};"
            f"border:1px solid {theme.BORDER};"
            "border-radius:4px;padding:2px 9px;font-size:10px;"
            "font-weight:700;letter-spacing:1px;}"
            f"QPushButton:hover{{color:{theme.TEXT};"
            f"background:{theme.with_alpha(theme.ACCENT, 30)};}}")
        btn.clicked.connect(lambda: self._items_farm_copy(btn))
        return btn

    def _items_farm_copy(self, btn) -> None:
        QtWidgets.QApplication.clipboard().setText(self._items_farm_export())
        btn.setText("✓ COPIED")
        QtCore.QTimer.singleShot(
            1400, lambda b=btn: self._items_farm_copy_restore(b))

    def _items_farm_copy_restore(self, btn) -> None:
        try:
            btn.setText("COPY")
        except RuntimeError:          # farm rebuilt while the flash was set
            pass

    def _items_farm_export(self) -> str:
        """The farm list as markdown-flavored plain text, paste-ready for a
        note (respecting the active class filter)."""
        out = ["**GEAR FARM**", ""]
        picked = getattr(self, "_farm_class", "")
        for iid in pdata.gear_item_ids():
            it = idata.item(iid)
            if not it:
                continue
            if picked and it.get("classes") \
                    and not (set(it["classes"]) & {picked}):
                continue
            name = it.get("name") or iid
            cls = it.get("classes") or []
            if cls:
                name += f" ({' · '.join(idata.class_label(c) for c in cls)})"
            src = self._farm_source_export(iid)
            out.append(f"- {name} — {src}" if src else f"- {name}")
        out += ["", f"Exported {time.strftime('%I:%M %p')}"]
        return "\n".join(out)

    # --- row rendering ---------------------------------------------------
    def _items_farm_row(self, item_id: str) -> QtWidgets.QWidget:
        """One farm row: class tag, name + base stats, drop-source cell,
        trash button."""
        it = idata.item(item_id) or {}
        rar = idata.item_display_rarity(item_id)
        col = theme.rarity_color(rar)
        row = QtWidgets.QFrame()
        row.setObjectName("Cell")
        h = QtWidgets.QHBoxLayout(row)
        h.setContentsMargins(12, 10, 12, 10)
        h.setSpacing(12)
        tile = C.IconTile(36)
        tile.set("item", item_id, col)
        h.addWidget(tile, 0, QtCore.Qt.AlignVCenter)
        cls = it.get("classes") or []
        for c in cls:
            c_col = theme.class_color(c)
            tag = QtWidgets.QLabel(idata.class_label(c))
            tag.setStyleSheet(
                f"color:{c_col};background:{theme.with_alpha(c_col, 22)};"
                f"border:1px solid {theme.with_alpha(c_col, 80)};"
                "border-radius:4px;padding:1px 7px;font-size:10px;"
                "font-weight:700;letter-spacing:1px;")
            h.addWidget(tag, 0, QtCore.Qt.AlignVCenter)

        ncol = QtWidgets.QVBoxLayout()
        ncol.setSpacing(2)
        name = WrapLabel(it.get("name") or item_id)
        name.setStyleSheet(
            f"color:{col};font-weight:700;font-size:13px;background:transparent;")
        ncol.addWidget(name)
        parts = self._farm_stats_parts(it)
        if parts:
            # two lines when the piece has 4+ stats, so a long single line
            # doesn't crowd the drop-source cell on the row's right
            mid = (len(parts) + 1) // 2
            text = " · ".join(parts[:mid])
            if len(parts) > mid:
                text += "\n" + " · ".join(parts[mid:])
            st = QtWidgets.QLabel(text)
            st.setStyleSheet(
                f'color:{theme.MUTED};font-family:"{theme.MONO_FONT}", "Consolas", monospace;'
                "font-size:11px;background:transparent;")
            ncol.addWidget(st)
        h.addLayout(ncol, 1)

        h.addWidget(self._farm_source_cell(item_id), 0,
                    QtCore.Qt.AlignVCenter)

        rm = QtWidgets.QPushButton()
        rm.setIcon(icons.ui_qicon("trash", theme.MUTED, 14))
        rm.setIconSize(QtCore.QSize(14, 14))
        rm.setFixedSize(26, 26)
        rm.setCursor(QtCore.Qt.PointingHandCursor)
        rm.setObjectName("FarmRemove")
        rm.setStyleSheet(
            f"QPushButton{{background:transparent;border:1px solid {theme.BORDER};"
            "border-radius:4px;}"
            f"QPushButton:hover{{border-color:{theme.with_alpha(theme.DANGER, 80)};"
            f"background:{theme.with_alpha(theme.DANGER, 15)};}}")
        rm.clicked.connect(
            lambda _=None, iid=item_id: self._items_farm_remove(iid))
        h.addWidget(rm, 0, QtCore.Qt.AlignVCenter)
        return row

    # --- drop-source cell (the row's right column) ----------------------
    def _farm_source_cell(self, item_id: str) -> QtWidgets.QWidget:
        """The right-side 'where to get it' cell of a farm row."""
        cell = QtWidgets.QWidget()
        lay = QtWidgets.QHBoxLayout(cell)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        rows = idata.shown_drops(item_id)
        bosses = [r for r in rows if r.get("boss")]
        if bosses:
            tile = QtWidgets.QLabel()
            tile.setPixmap(self._farm_source_icon(bosses[0]))
            tile.setFixedSize(_ROW_ICON, _ROW_ICON)
            lay.addWidget(tile, 0, QtCore.Qt.AlignVCenter)
            txt = QtWidgets.QVBoxLayout()
            txt.setSpacing(1)
            txt.setContentsMargins(0, 0, 0, 0)
            name = WrapLabel(" · ".join(b["source"] for b in bosses))
            name.setAlignment(QtCore.Qt.AlignRight)
            name.setStyleSheet(
                f"color:{theme.GOLD};font-weight:600;font-size:13px;"
                "background:transparent;")
            txt.addWidget(name, 0, QtCore.Qt.AlignRight)
            duns: list[str] = []
            for b in bosses:
                dg = b.get("dungeon")
                if dg and dg not in duns:
                    duns.append(dg)
            if duns:
                sub = WrapLabel(" · ".join(duns))
                sub.setObjectName("Mono")
                sub.setAlignment(QtCore.Qt.AlignRight)
                sub.setStyleSheet(
                    f'color:{theme.MUTED};font-family:"{theme.MONO_FONT}", "Consolas", monospace;'
                    "font-size:11px;background:transparent;")
                txt.addWidget(sub, 0, QtCore.Qt.AlignRight)
            lay.addLayout(txt, 1)
            return cell
        if rows:
            d = rows[0]
            tile = QtWidgets.QLabel()
            tile.setPixmap(self._farm_source_icon(d))
            tile.setFixedSize(_ROW_ICON, _ROW_ICON)
            lay.addWidget(tile, 0, QtCore.Qt.AlignVCenter)
            txt = QtWidgets.QVBoxLayout()
            txt.setSpacing(1)
            txt.setContentsMargins(0, 0, 0, 0)
            name = WrapLabel(_row_label(d))
            name.setAlignment(QtCore.Qt.AlignRight)
            name.setStyleSheet(
                f"color:{theme.TEXT};font-weight:600;font-size:12px;"
                "background:transparent;")
            txt.addWidget(name, 0, QtCore.Qt.AlignRight)
            sub_text = ""
            if d.get("kind") == "npc":
                sub_text = "Vendor"
            elif d.get("dungeon"):
                sub_text = d["dungeon"]
            elif d.get("location"):
                sub_text = d["location"]
            if sub_text:
                sub = WrapLabel(sub_text)
                sub.setObjectName("Mono")
                sub.setAlignment(QtCore.Qt.AlignRight)
                sub.setStyleSheet(
                    f'color:{theme.MUTED};font-family:"{theme.MONO_FONT}", "Consolas", monospace;'
                    "font-size:11px;background:transparent;")
                txt.addWidget(sub, 0, QtCore.Qt.AlignRight)
            lay.addLayout(txt, 1)
            return cell
        # crafted gear — no drop sources; the CRAFTED tag + the recipe's
        # job (the detail card's readout) instead of an empty column
        it = idata.item(item_id) or {}
        fixed = idata.item_fixed_level(it)
        recipe = idata.recipe(item_id)
        txt = QtWidgets.QVBoxLayout()
        txt.setSpacing(1)
        txt.setContentsMargins(0, 0, 0, 0)
        if fixed:
            tag = QtWidgets.QLabel(f"CRAFTED · L{fixed}")
            tag.setStyleSheet(
                f"color:{theme.GOLD};background:{theme.with_alpha(theme.GOLD, 18)};"
                f"border:1px solid {theme.with_alpha(theme.GOLD, 80)};"
                "border-radius:4px;padding:2px 9px;font-weight:700;"
                "font-size:10px;letter-spacing:1px;")
            txt.addWidget(tag, 0, QtCore.Qt.AlignRight)
        if recipe:
            job = QtWidgets.QLabel(
                f"{recipe['job_name'].upper()} · LV {recipe['level']}")
            job.setObjectName("Mono")
            job.setStyleSheet(
                f'color:{theme.MUTED};font-family:"{theme.MONO_FONT}", "Consolas", monospace;'
                "font-size:10px;letter-spacing:0.5px;background:transparent;")
            txt.addWidget(job, 0, QtCore.Qt.AlignRight)
        lay.addLayout(txt, 1)
        return cell

    @staticmethod
    def _farm_source_icon(d: dict):
        """The 32px leading icon for a farm drop source: boss sprite, mob,
        NPC, or the source kind's marker."""
        kind, sid = d["kind"], d.get("source_id") or ""
        if d.get("boss"):
            return icons.outlined("Units", sid, _ROW_ICON, theme.GOLD,
                                  border=2, trim=True)
        if kind == "unit" and icons.has_icon("Units", sid):
            return icons.outlined("Units", sid, _ROW_ICON, theme.BROWN, border=2)
        if kind == "npc":
            for prefix, sprite in _NPC_SPRITES:
                if sid.startswith(prefix) and icons.has_icon("Units", sprite):
                    return icons.pixmap("Units", sprite, _ROW_ICON)
        name, accent = _KIND_ICON.get(kind, ("sword", theme.BROWN))
        return icons.marker(name, _ROW_ICON, accent)

    @staticmethod
    def _farm_stats_parts(it: dict) -> list[str]:
        """The item's base stats at its display rarity, one string per stat
        ('Armor 78', 'Critical 10')."""
        lad = idata.upgrade_ladder(it)
        if not lad:
            return []
        rar = idata.item_display_rarity(it["id"])
        row = next((r for r in lad if r["rarity"] == rar), lad[0])
        base = row.get("base") or []
        return [
            f"{s['label']} {idata.stat_text(s['value'], s.get('raw'))}"
            for s in base]

    @staticmethod
    def _farm_bosses(item_id: str) -> list[str]:
        """The boss(es) that drop the item — 'Boss (Dungeon)', deduped in
        drop order."""
        out: list[str] = []
        seen: set[str] = set()
        for r in idata.resolve_drops(item_id):
            if not r.get("boss") or r["source"] in seen:
                continue
            seen.add(r["source"])
            dg = r.get("dungeon")
            out.append(f"{r['source']} ({dg})" if dg else r["source"])
        return out

    @staticmethod
    def _farm_source_export(item_id: str) -> str:
        """Single-line source summary for farm exports."""
        bosses = FarmMixin._farm_bosses(item_id)
        if bosses:
            return " · ".join(bosses)
        rows = idata.shown_drops(item_id)
        if rows:
            d = rows[0]
            src = d.get("source") or _row_label(d)
            if d.get("kind") == "npc":
                return f"{src} (Vendor)"
            if d.get("dungeon"):
                return f"{src} ({d['dungeon']})"
            if d.get("location"):
                return f"{src} ({d['location']})"
            return src
        recipe = idata.recipe(item_id)
        if recipe:
            return f"Crafted ({recipe['job_name']} · Lv {recipe['level']})"
        return ""
