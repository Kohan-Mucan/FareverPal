"""The Enchants page's tables: the scrolls, elixir / food, gem, conversions and
augments rows, and the Upgrades card's cost table.

This is the Enchants family's ONE shard, beside `enchants.py` (the page and
the Infusions tab). The line budget measures one file, so the family's tables
live here - one shard of real size, not a module per table.

`_upgrade_matrix_data` is the single source of the Upgrades table: the
summary tiles and the per-step matrix both render from it and the tests
assert on it, so the layout can be restyled without a cost going missing.
"""
from __future__ import annotations

from functools import lru_cache

from PySide6 import QtCore, QtGui, QtWidgets

from ... import theme
from ... import components as C
from ....data import icons
from ....data import items as idata
from ....data.items import stats as istats
from . import support
from .support import GLYPH_DOWN, GLYPH_RIGHT


# the augments card's job badges — color names the job, rank orders the
# groups (Enchanter → Outfitter → Blacksmith)
_AUG_RANK = {"Enchanter": 0, "Outfitter": 1, "Blacksmith": 2}
_AUG_COLOR = {"Enchanter": theme.ORANGE,
              "Outfitter": theme.rarity_color("Epic"),
              "Blacksmith": theme.rarity_color("Rare")}
# the slot each augment applies to -> its color, shared by the
# slot chips AND the table badges: BACK and CHEST wear their owning
# job's color, and the three Enchanter slots each get their own (FEET
# orange, HANDS green, WEAPON gold) so a JOB-sorted table reads every
# slot apart. The temp-buff scrolls get their own TEMP slot in the soft
# red — temporary, not a permanent gear augment.
_AUG_SLOT_COLOR = {"BACK": _AUG_COLOR["Outfitter"],
                    "CHEST": _AUG_COLOR["Blacksmith"],
                    "FEET": theme.ORANGE,
                    "HANDS": theme.GOOD,
                    "WEAPON": theme.GOLD,
                    "TEMP": theme.DANGER}
# the slot each craftable augment applies to, from itemType.json's
# augmentTargets — the Outfitter embroideries go on the Back, the
# Blacksmith plates on the Chest, and the Enchanter Magic Formulas on
# Feet (boots) / Hands / Weapon. Hardcoded like _SLOT_ATB: the loose
# sheet isn't bundled in the frozen build. The +2/corrupted scrolls
# (type Consumable) enchant any gear — they get the TEMP slot instead,
# so the temporary buffs read as their own category in the slot chips
# and badges.
_AUG_SLOT = {
    "AugmentEnchantFeet": "FEET",
    "AugmentEnchantHands": "HANDS",
    "AugmentEnchantWeapon": "WEAPON",
    "AugmentOutfitter": "BACK",
    "AugmentBlacksmith": "CHEST",
    "Consumable": "TEMP",
}


def _slot_badge(text: str, color: str,
                accent: str | None = None,
                icon: tuple[str, str] | None = None) -> QtWidgets.QLabel:
    """One bordered slot pill ('OUTFITTER · BACK', 'RING · NECK'), the tag
    every augments row wears. `accent` appends a gold trailing segment
    ('ENCHANTER · 30M') so a temp-buff timer stands apart from the job
    color while the pill keeps its single bordered box; `icon` (name,
    tint) embeds a glyph inside the pill, right before the slot text, so
    the temp buff's clock sits next to its TEMP label."""
    lb = QtWidgets.QLabel(text)
    lb.setObjectName("Mono")
    icon_url = None
    if icon:
        name, _ = icon
        icon_url = f"icon://{name}"
        parts = text.split(" · ")
        if len(parts) >= 2:
            parts[-1] = f'<img src="{icon_url}">' + parts[-1]
            text = " · ".join(parts)
    if accent or icon:
        lb.setTextFormat(QtCore.Qt.RichText)
    if accent:
        text += f'<span style="color:{theme.GOLD};"> · {accent}</span>'
    if accent or icon:
        lb.setText(text)
        if icon:
            # QLabel keeps its rich-text document as a child QObject that
            # only exists once rich text is set — register the glyph after
            # setText so the <img> tag above resolves to it at paint time
            doc = lb.findChild(QtGui.QTextDocument)
            if doc is not None:
                doc.addResource(QtGui.QTextDocument.ImageResource,
                                QtCore.QUrl(icon_url),
                                icons.ui_icon(*icon, 12))
    lb.setStyleSheet(
        f"color:{color};background:{theme.with_alpha(color, 40)};"
        f"border:1px solid {theme.with_alpha(color, 90)};"
        "border-radius:3px;padding:2px 7px;font-size:11px;font-weight:700;")
    return lb
# per-stat identity colors for the gem table's column headers — the
# values beneath keep the uniform +/− colors, the header names the stat
_STAT_COLORS = {
    "Critical": theme.ORANGE,
    "Fervor": theme.GOLD,
    "Armor Penetration": theme.rarity_color("Epic"),
    "Magic Penetration": theme.BLUE,
    "Vitality": theme.GOOD,
}


def _fmt_duration(secs: int) -> str:
    """A status-effect duration in seconds as the compact '1H' / '15M'."""
    if secs % 3600 == 0:
        return f"{secs // 3600}H"
    if secs % 60 == 0:
        return f"{secs // 60}M"
    return f"{secs}s"


def _golden_duration(sh: C.SectionHeader, title: str) -> None:
    """Gold a title's trailing craft timer ('Elixirs · Alchemist · 1H' ->
    'ELIXIRS · ALCHEMIST · <gold>1H</gold>'): the scrolls' 30M, elixirs'
    1H and foods' 15M ride their card titles, so the timer reads gold
    against the accent category · job text, like the temp-buff pills.
    The match is structural (the last ' · ' segment looks like a timer),
    so the durations stay in the titles alone — no second hardcoded
    list to drift."""
    base, _, dur = title.rpartition(" · ")
    if not (dur and dur[-1:] in "HM" and dur[:-1].isdigit()):
        return
    sh._label.setTextFormat(QtCore.Qt.RichText)
    sh._label.setText(
        f"{base.upper()} · "
        f'<span style="color:{theme.GOLD};">{dur}</span>')


class EnchantsRowsMixin:
    def _ench_card(self, title: str, header: bool = True):
        """One enchant-group card: a SectionHeader over a tight row list
        (skipped when `header` is False). Returns (card, rows layout,
        header) so the filter can rebuild rows in place."""
        box = QtWidgets.QWidget()
        bl = QtWidgets.QVBoxLayout(box)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(8)
        sh = C.SectionHeader(title) if header else None
        if sh:
            _golden_duration(sh, title)
            bl.addWidget(sh)
        rows = QtWidgets.QVBoxLayout()
        bl.addLayout(rows)
        return box, rows, sh

    def _empty_row(self, lay: QtWidgets.QVBoxLayout, msg: str) -> None:
        m = QtWidgets.QLabel(msg)
        m.setObjectName("Muted")
        m.setWordWrap(True)
        lay.addWidget(m)

    def _build_scroll_table(self, rows: list) -> QtWidgets.QGridLayout:
        """The scrolls card as ONE table: clickable names with item icons,
        the ENCHANTER · LV tag, and every value left-anchored beside the
        name, on the faint alternating tint."""
        mono = f'font-family:"{theme.MONO_FONT}","Consolas";'
        grid = QtWidgets.QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(4)
        grid.setContentsMargins(0, 0, 0, 0)
        for r, (s, v) in enumerate(rows):
            bg = theme.with_alpha(theme.TEXT, 10) if r % 2 else "transparent"
            corrupt = isinstance(v, dict)
            iid = v["item"] if corrupt else self._ench_scroll_items[s]
            name = f"Scroll of Corrupted {s}" if corrupt else f"Scroll of {s}"
            grid.addWidget(self._name_with_icon(iid, name, bg), r, 0)
            # the job · level tag — the recipes' real craft levels (LV 1/6)
            rec = idata.recipe(iid) or {}
            tag = QtWidgets.QLabel(
                f"{(rec.get('job_name') or 'ENCHANTER').upper()} · "
                f"LV {rec.get('level', '')}")
            tag.setObjectName("Mono")
            tag.setStyleSheet(
                f"color:{theme.DIM};font-size:11px;background:{bg};")
            grid.addWidget(tag, r, 1)
            if corrupt:
                s_s = support.CHIP_SHORT.get(s, s)
                p_s = support.CHIP_SHORT.get(v["penalty"], v["penalty"])
                cell = QtWidgets.QWidget()
                cell.setStyleSheet(f"background:{bg};")
                val = QtWidgets.QHBoxLayout(cell)
                val.setContentsMargins(0, 0, 0, 0)
                val.setSpacing(4)
                for t, col in ((f"+{v['value']} {s_s}", theme.ACCENT),
                               ("→", theme.DIM),
                               (f"−{v['value']} {p_s}", theme.DANGER)):
                    lb = QtWidgets.QLabel(t)
                    lb.setStyleSheet(
                        f"color:{col};{mono}font-size:13px;"
                        "font-weight:700;background:transparent;")
                    val.addWidget(lb, 0, QtCore.Qt.AlignVCenter)
                grid.addWidget(cell, r, 2)
            else:
                val_l = QtWidgets.QLabel(f"+{v}")
                val_l.setStyleSheet(
                    f"color:{theme.ACCENT};{mono}font-size:13px;"
                    "font-weight:700;background:transparent;")
                grid.addWidget(val_l, r, 2)
        # the value column hugs the names — a trailing empty column absorbs
        # the leftover width so the table reads as a tight left block
        grid.setColumnMinimumWidth(0, 240)
        grid.setColumnStretch(3, 1)
        return grid

    def _build_consumable_table(self, items: list[dict], dur: str
                                ) -> QtWidgets.QGridLayout:
        """The Elixirs / Food cards as ONE scrolls-style table each:
        clickable names, the LV tag, and the granted stat as the value
        column. The job + group craft duration ride the card title (`dur`);
        a row whose real effect duration differs carries its own gold
        pill beside the name. The stat column takes the leftover width so
        long multi-stat grants fit on one line; statless rows leave the
        value cell empty."""
        mono = f'font-family:"{theme.MONO_FONT}","Consolas";'
        grid = QtWidgets.QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(4)
        grid.setContentsMargins(0, 0, 0, 0)
        for r, it in enumerate(items):
            bg = theme.with_alpha(theme.TEXT, 10) if r % 2 else "transparent"
            iid = it["id"]
            name = it.get("name") or iid
            rec = idata.recipe(iid) or {}
            grant = self._ench_grant(it)
            own = idata.item_effect_duration(iid)
            diff = bool(own) and _fmt_duration(own) != dur
            btn = QtWidgets.QPushButton(name)
            btn.setFlat(True)
            btn.setCursor(QtCore.Qt.PointingHandCursor)
            btn.setStyleSheet(
                f"QPushButton{{color:{theme.TEXT};background:{bg};border:0;"
                "padding:0;font-size:14px;text-align:left;}"
                f"QPushButton:hover{{color:{theme.ACCENT};}}")
            dt = None
            if diff:
                # the differing duration rides right beside the item name
                # as a gold pill — it stands out next to the dim level
                # tags without shifting the stat column (the Feast is
                # the one 1H dish)
                dt = QtWidgets.QLabel(_fmt_duration(own))
                dt.setObjectName("Mono")
                dt.setStyleSheet(
                    f"color:{theme.GOLD};"
                    f"background:{theme.with_alpha(theme.GOLD, 25)};"
                    f"border:1px solid {theme.with_alpha(theme.GOLD, 80)};"
                    "border-radius:3px;padding:1px 6px;font-size:11px;"
                    "font-weight:700;")
            grid.addWidget(
                self._name_with_icon(iid, name, bg, extra=dt), r, 0)
            # the level tag — the job rides the card title, so the rows
            # just show 'LV 5' (no repeated ALCHEMIST / COOK spam)
            tag = QtWidgets.QLabel(f"LV {rec.get('level', '')}")
            tag.setObjectName("Mono")
            tag.setStyleSheet(
                f"color:{theme.DIM};font-size:11px;background:{bg};")
            # cap the tag at its own text width: the STATS column takes
            # the leftover width, but its 540px cap means a wide window
            # spills the rest into this column (a 'LV 6' cell reads ~100px
            # wide); capping keeps the LV column hugging its content
            tag.setMaximumWidth(tag.sizeHint().width())
            grid.addWidget(tag, r, 1)
            if grant:
                # the granted stats read as the value column, in accent.
                # The STATS column takes the leftover width (like the
                # augments table), so a long multi-stat grant (the
                # Feast's five stats, the Cauldron's four) reads wide on
                # one line instead of wrapping tall; word-wrap stays on
                # so a narrow window degrades gracefully. No max width
                # cap: a cap would bind once the column passes it and
                # the grid would spill the rest into the name / LV
                # columns (a 'LV 6' cell read ~100px wide, the 1H pill
                # 200px+); uncapped, the STATS column absorbs it all
                val = QtWidgets.QLabel(grant)
                val.setObjectName("Mono")
                val.setWordWrap(True)
                val.setStyleSheet(
                    f"color:{theme.ACCENT};{mono}font-size:13px;"
                    "font-weight:700;background:transparent;")
                grid.addWidget(val, r, 2)
        # the STATS column takes the leftover width — long multi-stat
        # grants fit on one line instead of wrapping tall
        grid.setColumnMinimumWidth(0, 240)
        grid.setColumnMinimumWidth(2, 380)
        grid.setColumnStretch(2, 1)
        return grid

    def _build_gem_table(self, by_zone: dict[str, list[dict]]
                         ) -> QtWidgets.QGridLayout:
        """The gem card as a table: a GEM column plus one per stat the
        database grants, zone headers spanning the full width, signed
        value chips ('·' for stats a gem doesn't grant), alternating row
        tint, and a socket-slot badge (RING · NECK) per row. Groups read
        highest-tier first; a mixed-tier zone shows a per-row 'Lv N' tag."""
        cols = self._ench_gem_cols
        mono = f'font-family:"{theme.MONO_FONT}","Consolas",monospace;'
        grid = QtWidgets.QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(4)
        grid.setContentsMargins(0, 0, 0, 0)

        def head(text: str, color: str = theme.MUTED) -> QtWidgets.QLabel:
            h = QtWidgets.QLabel(text)
            h.setObjectName("Mono")
            h.setStyleSheet(
                f"color:{color};{mono}font-size:12px;font-weight:700;"
                "letter-spacing:1px;background:transparent;padding:2px 4px;")
            # long two-word stat names (ARMOR PENETRATION, MAGIC
            # PENETRATION) wrap onto two lines so the stat columns can
            # stay narrow — the header caps at its longest word, so the
            # text breaks at the space instead of forcing the column wide
            if " " in text:
                h.setWordWrap(True)
                fm = h.fontMetrics()
                longest = max(fm.horizontalAdvance(w) for w in text.split())
                h.setMaximumWidth(longest + 10)   # 2×4px padding + slack
            return h

        grid.addWidget(head("GEM"), 0, 0, QtCore.Qt.AlignLeft)
        # full-name stat headers in their identity colors, centered over
        # their value columns so the signed chips line up beneath
        for c, stat in enumerate(cols, start=1):
            h = head(stat.upper(),
                     _STAT_COLORS.get(stat, theme.MUTED))
            h.setAlignment(QtCore.Qt.AlignHCenter)
            grid.addWidget(h, 0, c)

        r = 1
        band = 0
        for zone, group in sorted(
                by_zone.items(),
                key=lambda kv: (-kv[1][0]["level"], kv[0])):
            # mixed-tier zones name the level per row, not in the header
            lvls = {g["level"] for g in group}
            tier = (f" · JEWELLER LV {next(iter(lvls))}"
                    if len(lvls) == 1 else "")
            zh = QtWidgets.QLabel(f"{zone.upper()}{tier}")
            zh.setObjectName("Mono")
            zh.setStyleSheet(
                f"color:{theme.MUTED};{mono}font-size:12px;font-weight:700;"
                "background:transparent;padding-top:8px;")
            grid.addWidget(zh, r, 0, 1, len(cols) + 1)
            r += 1
            mixed = len(lvls) > 1
            for g in sorted(group, key=lambda g: (-g["level"],
                                                  -max(g["stats"].values()))):
                # faint stripe on every other data row — a full-row band so
                # the aligned stat columns scan vertically
                shade = theme.with_alpha(theme.TEXT, 10) if band % 2 else None
                band += 1
                bg = shade or "transparent"
                name = ((idata.item(g["item"]) or {}).get("name")
                        or g["item"])
                # clickable name — opens the Jeweller recipe, like every row
                nm = self._name_button(name, g["item"])
                row_w = QtWidgets.QWidget()
                if shade:
                    row_w.setStyleSheet(f"background:{shade};")
                rl = QtWidgets.QHBoxLayout(row_w)
                rl.setContentsMargins(0, 0, 0, 0)
                rl.setSpacing(8)
                tile = C.IconTile(26)
                tile.set("item", g["item"], theme.ACCENT)
                rl.addWidget(tile, 0, QtCore.Qt.AlignVCenter)
                rl.addWidget(nm, 1, QtCore.Qt.AlignVCenter)
                # the socket slot the gem cuts into — every Jeweller gem
                # targets rings and necklaces (itemType.json's
                # augmentTargets for AugmentJeweller), in the jewelry gold
                sock = _slot_badge("RING · NECK", theme.GOLD)
                rl.addWidget(sock, 0, QtCore.Qt.AlignVCenter)
                if mixed:
                    tag = QtWidgets.QLabel(f"Lv {g['level']}")
                    tag.setObjectName("Mono")
                    tag.setStyleSheet(f"color:{theme.DIM};font-size:11px;"
                                      "background:transparent;")
                    rl.addWidget(tag, 0, QtCore.Qt.AlignVCenter)
                grid.addWidget(row_w, r, 0)
                for c, stat in enumerate(cols, start=1):
                    if stat in g["stats"]:
                        v = g["stats"][stat]
                        col = theme.ACCENT if v > 0 else theme.DANGER
                        cell = QtWidgets.QLabel(
                            f"+{v}" if v > 0 else f"−{abs(v)}")
                        cell.setObjectName("Mono")
                        cell.setStyleSheet(support.chip_style(col, size=13))
                        # the signed value centers in its column like the
                        # '·' empty cells, so the stat columns read as a
                        # clean matrix under the centered headers
                        cell.setAlignment(QtCore.Qt.AlignCenter)
                        grid.addWidget(cell, r, c)
                    else:
                        cell = QtWidgets.QLabel("·")
                        cell.setObjectName("Mono")
                        cell.setStyleSheet(
                            f"color:{theme.DIM};{mono}font-size:13px;"
                            f"background:{bg};padding:2px 4px;")
                        cell.setAlignment(QtCore.Qt.AlignCenter)
                        grid.addWidget(cell, r, c)
                r += 1
        # the stat columns hug the gem names — the trailing empty column
        # takes the leftover width instead of the name column
        grid.setColumnStretch(len(cols) + 1, 1)
        return grid

    def _ench_food_matches(self, it: dict, q: str) -> bool:
        """Search-query match for a crafted elixir/food: its name, job,
        materials, or the stat it grants."""
        r = idata.recipe(it["id"]) or {}
        hay = " ".join([it.get("name") or "", r.get("job_name") or "",
                        *[m["name"] for m in r.get("materials") or []],
                        *[s["n"] for s in (idata.own_stats(it) or [])]])
        return q in hay.lower()

    def _ench_grant(self, it: dict) -> str | None:
        """The crafting consumable's granted stat as a readable '+6
        Strength' line — the positive side only; statless / penalty-only
        rows show no grant."""
        pos = [s for s in (idata.own_stats(it) or []) if s["v"] > 0]
        if not pos:
            return None
        return " + ".join(f"+{s['v']} {s['n']}" for s in pos)

    def _ench_food_key(self, it: dict) -> tuple:
        """Sort key for a crafted consumable: highest craftable level
        first, then name."""
        r = idata.recipe(it["id"]) or {}
        return (-(r.get("level") or 0), (it.get("name") or "").lower())

    def _name_button(self, text: str, iid: str):
        btn = QtWidgets.QPushButton(text)
        btn.setFlat(True)
        btn.setCursor(QtCore.Qt.PointingHandCursor)
        btn.setStyleSheet(
            f"QPushButton{{color:{theme.TEXT};background:transparent;border:0;"
            "padding:0;font-size:14px;text-align:left;}"
            f"QPushButton:hover{{color:{theme.ACCENT};}}")
        btn.clicked.connect(lambda: self._items_open_in_craft(iid))
        return btn

    def _name_with_icon(self, iid: str, name: str, bg: str,
                        extra: QtWidgets.QWidget | None = None
                        ) -> QtWidgets.QWidget:
        """The clickable name with its 26px item icon, shared by the
        scrolls / elixirs / food rows. `extra` trails the name; `bg` is
        the row's tint."""
        w = QtWidgets.QWidget()
        w.setStyleSheet(f"background:{bg};")
        hl = QtWidgets.QHBoxLayout(w)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(6)
        tile = C.IconTile(26)
        tile.set("item", iid, theme.ACCENT)
        hl.addWidget(tile, 0, QtCore.Qt.AlignVCenter)
        hl.addWidget(self._name_button(name, iid),
                     0, QtCore.Qt.AlignVCenter)
        if extra is not None:
            # left-aligned so the trailing widget (the duration pill)
            # hugs its text — a horizontal stretch would inflate a
            # one-char tag ('1H') into a wide box when the name column
            # has spare width
            hl.addWidget(extra, 0,
                         QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft)
        return w

    def _aug_sort_key(self, it: dict, key: str, desc: bool) -> tuple:
        """Sort key for an augments row under the given column — the
        primary field flips with the header's direction, the name breaks
        ties alphabetically, and the default JOB sort keeps the
        profession order with the highest craft level first within."""
        r = idata.recipe(it["id"]) or {}
        job = r.get("job_name") or ""
        name = (it.get("name") or it["id"]).lower()
        lev = r.get("level") or 0
        if key == "name":
            return (name,)
        if key == "job":
            rank = _AUG_RANK.get(job, 9)
            return (rank if not desc else -rank, -lev, name)
        if key == "lv":
            return (-lev if desc else lev, name)
        if key == "stats":
            pos = sum(s["v"] for s in (idata.own_stats(it) or [])
                      if s["v"] > 0)
            return (-pos if desc else pos, name)
        return (name,)

    def _build_aug_table(self, augments: list[dict]) -> QtWidgets.QGridLayout:
        """The augments card as ONE table: rows in profession order, each
        with the icon + clickable name (a ▸ toggle reveals the recipe's
        materials), the job · slot badge, the craft level, and the
        granted stats in their colors on the faint alternating tint. No
        column headers; the STATS column takes the leftover width."""
        mono = f'font-family:"{theme.MONO_FONT}","Consolas";'
        grid = QtWidgets.QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(4)
        grid.setContentsMargins(0, 0, 0, 0)
        for r, it in enumerate(sorted(
                augments, key=lambda i: self._aug_sort_key(i, "job", False))):
            rec = idata.recipe(it["id"]) or {}
            job = rec.get("job_name") or ""
            name = it.get("name") or it["id"]
            bg = theme.with_alpha(theme.TEXT, 10) if r % 2 else "transparent"
            # the name cell — icon + clickable name, the weapon formulas'
            # effect note (no flat stats) under it, and the ▸ materials
            # toggle at the line's end
            name_w = QtWidgets.QWidget()
            name_w.setStyleSheet(f"background:{bg};")
            name_w.setMaximumWidth(280)
            nv = QtWidgets.QVBoxLayout(name_w)
            nv.setContentsMargins(0, 0, 0, 0)
            nv.setSpacing(2)
            line = QtWidgets.QHBoxLayout()
            line.setSpacing(8)
            tile = C.IconTile(26)
            tile.set("item", it["id"], theme.ACCENT)
            line.addWidget(tile, 0, QtCore.Qt.AlignVCenter)
            line.addWidget(self._name_button(name, it["id"]),
                           0, QtCore.Qt.AlignVCenter)
            detail = self._ench_craft_detail(rec)
            detail.hide()
            chev = QtWidgets.QPushButton(GLYPH_RIGHT)
            chev.setCursor(QtCore.Qt.PointingHandCursor)
            chev.setFlat(True)
            chev.setStyleSheet(
                f"QPushButton{{color:{theme.DIM};border:0;padding:0;"
                "font-size:12px;background:transparent;}"
                f"QPushButton:hover{{color:{theme.ACCENT};}}")

            def _flip(_, d=detail, c=chev):
                d.setVisible(not d.isVisible())
                c.setText(GLYPH_DOWN if d.isVisible() else GLYPH_RIGHT)
            chev.clicked.connect(_flip)
            line.addWidget(chev, 0, QtCore.Qt.AlignVCenter)
            line.addStretch(1)
            nv.addLayout(line)
            # the granted stats — the weapon formulas carry none, so their
            # effect note reads in the STATS column; the corrupted scrolls
            # show the '+4 Strength' grant AND the '−4 Vitality' drain
            grant = self._ench_grant(it)
            if grant is None and it["id"] in self._ench_corrupt_by_item:
                c = self._ench_corrupt_by_item[it["id"]]
                grant = [(f"+{c['value']} {c['stat']}", theme.ACCENT),
                         (f"−{c['value']} {c['penalty']}", theme.DANGER)]
            # the weapon Magic Formulas carry no flat stats — their
            # resolved effect reads in the STATS column (wrapped, capped
            # so the table stays tight), not under the name, so a weapon
            # enchant compares against the stat rows without opening Craft
            note = None
            if grant is None:
                descs = [s.get("description") for s in
                         (idata.weapon_skills(it["id"]) or [])
                         if s.get("description")]
                if descs:
                    note = QtWidgets.QLabel(descs[0])
                    note.setObjectName("Mono")
                    note.setWordWrap(True)
                    note.setMaximumWidth(400)
                    note.setStyleSheet(
                        f"color:{theme.MUTED};font-size:12px;"
                        "background:transparent;")
            nv.addWidget(detail)
            grid.addWidget(name_w, r, 0)
            # the job · slot badge, in the SLOT's color (matching the
            # slot chips — 'ENCHANTER · FEET' orange, '· HANDS' green,
            # '· WEAPON' gold). The temp-buff scrolls get their own TEMP
            # slot ('ENCHANTER · TEMP') with the effect's timer as the
            # gold accent, so a temporary enchant reads apart from the
            # permanent gear augments at a glance
            slot = _AUG_SLOT.get(it.get("type") or "", "")
            accent = None
            if slot == "TEMP":
                dur = idata.item_effect_duration(it["id"])
                if dur:
                    accent = _fmt_duration(dur)   # the timer reads gold
            # the temp buffs' clock embeds inside the pill, right next to
            # the TEMP slot text
            grid.addWidget(_slot_badge(
                job.upper() + (f" · {slot}" if slot else ""),
                _AUG_SLOT_COLOR.get(
                    slot, _AUG_COLOR.get(job, theme.ACCENT)),
                accent=accent,
                icon=("timer", theme.DANGER) if slot == "TEMP" else None),
                r, 1)
            # the craft level — 'LV 6', highest first under the default
            lv = QtWidgets.QLabel(f"LV {rec.get('level', '')}")
            lv.setObjectName("Mono")
            lv.setStyleSheet(
                f"color:{theme.DIM};font-size:11px;background:{bg};")
            grid.addWidget(lv, r, 2)
            # the granted stats read as the value column, in their colors;
            # the weapon formulas' effect note reads there too, aligned
            # with the stat rows
            if grant:
                stats_w = QtWidgets.QWidget()
                stats_w.setStyleSheet(f"background:{bg};")
                sl = QtWidgets.QHBoxLayout(stats_w)
                sl.setContentsMargins(0, 0, 0, 0)
                sl.setSpacing(4)
                segs = (grant if isinstance(grant, (list, tuple))
                        else [(grant, theme.ACCENT)])
                for txt, col in segs:
                    gl = QtWidgets.QLabel(txt)
                    gl.setObjectName("Mono")
                    gl.setStyleSheet(
                        f"color:{col};font-size:13px;font-weight:700;"
                        "background:transparent;")
                    sl.addWidget(gl, 0, QtCore.Qt.AlignVCenter)
                grid.addWidget(stats_w, r, 3)
            elif note is not None:
                grid.addWidget(note, r, 3, QtCore.Qt.AlignVCenter)
        # the STATS column takes the leftover width — long grants and
        # effect notes read wide instead of wrapping tall
        grid.setColumnMinimumWidth(0, 240)
        grid.setColumnMinimumWidth(3, 380)
        grid.setColumnStretch(3, 1)
        return grid

    def _ench_craft_detail(self, r: dict) -> QtWidgets.QLabel:
        """The sub-menu under a crafted row: the recipe's materials as one
        indented mono line behind a left rule, falling back to the job ·
        level when the recipe lists none."""
        mats = r.get("materials") or []
        if mats:
            text = "  ·  ".join(f"{m['name']} ×{m['count']}" for m in mats)
        else:
            text = f"{r.get('job_name') or ''} · LV {r.get('level') or ''}"
        d = QtWidgets.QLabel(text)
        d.setObjectName("Mono")
        d.setWordWrap(True)
        d.setMaximumWidth(280)
        d.setStyleSheet(
            f"color:{theme.MUTED};font-size:11px;background:transparent;"
            f"border-left:2px solid {theme.with_alpha(theme.MUTED, 90)};"
            "padding-left:8px;margin-left:36px;")
        return d

    def _build_conv_table(self, convs: list[dict]) -> QtWidgets.QGridLayout:
        """The conversion card as a deduplicated matrix: rows are the stat
        given up, the columns the stat gained, and each stat pair is ONE
        marker cell ('↘' = the conversion exists, '↑' = no self-trade).
        Every trade is identical, so the numbers live once in the corner
        line ('GIVE UP ↓ / GAIN → +40 −40'), chosen by the EPIC / RARE
        swap chips. The columns hug their content."""
        mono = f'font-family:"{theme.MONO_FONT}","Consolas";'
        grid = QtWidgets.QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(4)
        grid.setContentsMargins(0, 0, 0, 0)
        rar = self._ench_conv_rar
        # the top line is closed — the WEAPON badge and the EPIC / RARE
        # swap chips sit together on the left; the chips choose which
        # rarity's numbers the corner line below carries
        top = QtWidgets.QWidget()
        tl = QtWidgets.QHBoxLayout(top)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.setSpacing(8)
        tl.addWidget(_slot_badge("WEAPON", theme.ACCENT))
        chips = QtWidgets.QWidget()
        cl = QtWidgets.QHBoxLayout(chips)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(6)
        for key in ("epic", "rare"):
            colr = theme.rarity_color(key.title())
            chip = QtWidgets.QPushButton(key.upper())
            chip.setCursor(QtCore.Qt.PointingHandCursor)
            if rar == key:
                chip.setStyleSheet(
                    f"QPushButton{{color:{colr};"
                    f"background:{theme.with_alpha(colr, 45)};"
                    f"border:1px solid {theme.with_alpha(colr, 90)};"
                    f"border-radius:3px;padding:2px 8px;font-size:11px;"
                    f"font-weight:700;letter-spacing:1px;{mono}}}"
                    f"QPushButton:hover{{color:{theme.ACCENT};}}")
            else:
                chip.setStyleSheet(
                    f"QPushButton{{color:{theme.DIM};background:transparent;"
                    f"border:1px solid {theme.BORDER};border-radius:3px;"
                    f"padding:2px 8px;font-size:11px;font-weight:700;"
                    f"letter-spacing:1px;{mono}}}"
                    f"QPushButton:hover{{color:{theme.ACCENT};}}")
            chip.clicked.connect(
                lambda _=False, k=key: self._ench_conv_set_rar(k))
            cl.addWidget(chip)
        tl.addWidget(chips)
        tl.addStretch(1)
        grid.addWidget(top, 0, 0, 1, 5)
        # the corner names the axes and carries the active rarity's trade
        # — the markers say WHICH pairs convert, this line says the amounts
        corner = QtWidgets.QWidget()
        cl = QtWidgets.QHBoxLayout(corner)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(6)
        lab = QtWidgets.QLabel("GIVE UP ↓ / GAIN →")
        lab.setObjectName("Mono")
        lab.setStyleSheet(
            f"color:{theme.DIM};{mono}font-size:11px;letter-spacing:1px;"
            "background:transparent;")
        cl.addWidget(lab)
        v = max(c[rar]["value"] for c in convs)
        for txt, col in ((f"+{v}", theme.ACCENT), (f"−{v}", theme.DANGER)):
            tb = QtWidgets.QLabel(txt)
            tb.setObjectName("Mono")
            tb.setStyleSheet(
                f"color:{col};{mono}font-size:13px;font-weight:700;"
                "background:transparent;")
            cl.addWidget(tb)
        grid.addWidget(corner, 1, 0, QtCore.Qt.AlignLeft)
        cols = sorted({c["target"] for c in convs})
        by = {(c["source"], c["target"]): c for c in convs}
        for ci, target in enumerate(cols, start=1):
            # the column header: just the short stat name — every trade
            # is identical, so the numbers live once in the swap chips,
            # not repeated beside every column
            head = QtWidgets.QWidget()
            hl = QtWidgets.QHBoxLayout(head)
            hl.setContentsMargins(0, 0, 0, 0)
            hl.setSpacing(4)
            gname = QtWidgets.QLabel(target.upper().replace(
                "PENETRATION", "PEN"))
            gname.setObjectName("Mono")
            gname.setStyleSheet(
                f"color:{_STAT_COLORS.get(target, theme.MUTED)};{mono}"
                "font-size:12px;font-weight:700;letter-spacing:1px;"
                "background:transparent;padding-bottom:2px;")
            hl.addWidget(gname)
            hl.addStretch(1)
            grid.addWidget(head, 1, ci)
        rows = sorted({c["source"] for c in convs})
        data = 0
        for ri, source in enumerate(rows, start=2):
            # faint stripe on every other data row so the grid scans
            data += 1
            bg = (theme.with_alpha(theme.TEXT, 10)
                  if data % 2 else "transparent")
            rl = QtWidgets.QLabel(source)
            rl.setStyleSheet(
                f"color:{_STAT_COLORS.get(source, theme.TEXT)};"
                f"background:{bg};font-size:13px;font-weight:600;")
            rl.setAlignment(QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft)
            grid.addWidget(rl, ri, 0)
            for ci, target in enumerate(cols, start=1):
                c = by.get((source, target))
                cell_w = QtWidgets.QWidget()
                cell_w.setStyleSheet(f"background:{bg};")
                cl = QtWidgets.QHBoxLayout(cell_w)
                cl.setContentsMargins(0, 0, 0, 0)
                cl.setSpacing(4)
                if c is None:
                    # the diagonal — no self-trade (and any absent pair);
                    # blue so the 'no conversion here' cells read apart
                    # from the muted ↘ markers
                    dot = QtWidgets.QLabel("↑")
                    dot.setObjectName("Mono")
                    dot.setStyleSheet(
                        f"color:{theme.BLUE};{mono}font-size:13px;"
                        "background:transparent;")
                    cl.addWidget(dot, 0, QtCore.Qt.AlignVCenter)
                else:
                    # every trade is identical (40 on Epic, 20 on Rare),
                    # so the cells carry no numbers — the corner line
                    # above shows the active rarity's gain / loss. The
                    # marker just says the conversion exists.
                    mark = QtWidgets.QLabel("↘")
                    mark.setObjectName("Mono")
                    mark.setStyleSheet(
                        f"color:{theme.MUTED};{mono}font-size:13px;"
                        "background:transparent;")
                    cl.addWidget(mark, 0, QtCore.Qt.AlignVCenter)
                grid.addWidget(cell_w, ri, ci)
        # the columns hug their content — a closed matrix on the left,
        # not four columns stretched across the card
        return grid

    # the Upgrades card's cost table (_upgrade_matrix_data + the summary tiles
    # and per-step matrix drawn from it) is further down THIS file, in
    # EnchantsUpgradesMixin, mixed into EnchantsPageBase with this mixin.

    @staticmethod
    def _clear_lay(lay: QtWidgets.QLayout) -> None:
        while lay.count():
            item = lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
            elif item.layout() is not None:
                EnchantsRowsMixin._clear_lay(item.layout())


#: Every real figure wears ONE colour. Provenance used to be a second channel
#: (accent for measured, dim for derived), and at level 20 that painted eight of
#: the fifteen real cells dim — a number you still have to pay read as weaker
#: than the one beside it for a reason no one can see (the tab carries no
#: tooltips, so the colour was the only explanation and it explained nothing).
#: Measured, interpolated, extrapolated and derived are all on the measured
#: curve, so they are all the same figure to the player; what each provenance
#: actually means is in docs/UPGRADE_COSTS.md and in the `source` field of
#: `upgrade_costs`, not in the paint.
_FIGURE_COLOR = theme.ACCENT

#: The one exception, kept as a guard rather than something on screen: the
#: sheet's own `base x level^exp` formula, which is 3-5x off wherever it has
#: been checked. It cannot appear at any level the tab offers (every offered
#: level has the anchor rarity measured — see stats.upgrade_cost_levels), but a
#: placeholder guess must never wear the colour of a reading if that ever
#: changes.
_UNVERIFIED_COLOR = theme.TEXT

_MONO = f'font-family:"{theme.MONO_FONT}","Consolas";'


@lru_cache(maxsize=1)
def _material_art() -> dict[str, tuple[str, str]]:
    """Upgrade-material display name -> (the item id carrying that art, that
    item's OWN rarity colour).

    The cost table names materials by display name ('Spark Dust', 'Spark
    Shard', 'Spark Crystal'), so the art is resolved through the item sheet by
    name: Spark Dust is `UpgradeAll`, Spark Shard `UpgradeRare`, Spark Crystal
    `UpgradeEpic` in the current data. Resolved rather than restated, so a patch
    that renames or re-rarifies a material follows instead of leaving a stale id
    and a stale tint behind here.
    """
    out: dict[str, tuple[str, str]] = {}
    for it in idata.items():
        name = it.get("name") or ""
        if name:
            out[name] = (it.get("id") or "", theme.rarity_color(it.get("rarity")))
    return out


class EnchantsUpgradesMixin:
    """The Gear Upgrades tiles + cost matrix (composed into the enchants page)."""

    def _upgrade_matrix_data(self, cols: list[dict], level: int) -> dict:
        """The whole Upgrades table as DATA — the tiles and the matrix render
        from this, and the tests assert on it.

        One cell per (rank, rarity, material), in rank-major order:
        `{"rank", "rarity", "cap", "material", "short", "count", "text",
        "source", "live"}`. `text` is the count a step charges, an explicit "0"
        for a material that step does not charge, and "—" past that rarity's cap
        (or on a rarity with no live upgrade) — the same three states the old
        grid drew, so no restyle can hide one.
        """
        maxrows = max([c.get("cap") or 0 for c in cols] + [0])
        steps: dict[tuple[int, str], tuple[dict, str]] = {}
        roster: list[str] = []
        for rank in range(1, maxrows + 1):
            for c in cols:
                live = bool(c.get("active")) and rank <= (c.get("cap") or 0)
                costs, source = (istats.upgrade_step_cost(
                    c["rarity"], rank, level, c.get("name")) if live else ([], ""))
                by = {m["material"]: m["count"] for m in costs}
                steps[(rank, c["rarity"])] = (by, source)
                if live:
                    for m in costs:
                        if m["material"] not in roster:
                            roster.append(m["material"])
        cells = []
        for rank in range(1, maxrows + 1):
            for c in cols:
                by, source = steps[(rank, c["rarity"])]
                live = bool(c.get("active")) and rank <= (c.get("cap") or 0)
                for m in roster:
                    if not live:
                        text = "—"
                    elif m in by:
                        text = f"{by[m]:g}"
                    else:
                        text = "0"
                    cells.append({
                        "rank": rank, "rarity": c["rarity"],
                        "cap": c.get("cap") or 0, "material": m,
                        "short": m.split()[-1], "count": by.get(m),
                        "text": text, "source": source, "live": live})
        return {
            "level": level, "max_rank": maxrows, "roster": roster, "cells": cells,
            "columns": [{
                "rarity": c["rarity"], "cap": c.get("cap") or 0,
                "item": c.get("item"), "name": c.get("name"),
                "active": bool(c.get("active")),
                "color": (theme.rarity_color(c["rarity"]) if c.get("active")
                          else theme.DIM),
            } for c in cols],
        }

    def _upgrade_totals(self, table: dict, rarity: str) -> dict[str, float]:
        """What one rarity's whole ladder costs in each material, summed from the
        same cells the matrix prints — so a tile and the grid cannot disagree."""
        out: dict[str, float] = {}
        for c in table["cells"]:
            if c["rarity"] == rarity and c["count"]:
                out[c["material"]] = out.get(c["material"], 0) + c["count"]
        return out

    # --- the summary tiles ------------------------------------------------
    def _build_upgrade_tiles(self, table: dict) -> QtWidgets.QGridLayout:
        """One summary tile per rarity, sharing the width evenly.

        A tile is a THREE-COLUMN SLOT however many tiles there are: the grid
        stretches the tiles and then an empty column for every tile that is
        missing, so a search that narrows the table to one rarity leaves it the
        width of one tile with the rest of the row empty — instead of one tile
        stretched across the page with its totals smeared over it.
        """
        grid = QtWidgets.QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(12)
        cols = table["columns"]
        for ci, col in enumerate(cols):
            grid.addWidget(self._upgrade_tile(table, col), 0, ci)
            grid.setColumnStretch(ci, 1)
        if len(cols) < 3:
            grid.setColumnStretch(len(cols), 3 - len(cols))
        return grid

    def _upgrade_tile(self, table: dict, col: dict) -> QtWidgets.QFrame:
        """A rarity's total bill: name and cap, one line per material with its
        icon and real total, and how many steps that takes."""
        rc = col["color"]
        tot = self._upgrade_totals(table, col["rarity"])
        tile = QtWidgets.QFrame()
        tile.setObjectName("UpgradeTile")
        # 200 keeps three tiles on one row at the window's 1000px minimum (the
        # content area is ~744 there); there is no maximum — the tiles share
        # whatever width the page has and their contents flow into it
        tile.setMinimumWidth(200)
        tile.setStyleSheet(
            f"QFrame#UpgradeTile{{background:{theme.PANEL};"
            f"border:1px solid {theme.BORDER};"
            f"border-top:2px solid {theme.with_alpha(rc, 160)};"
            "border-radius:4px;}")
        v = QtWidgets.QVBoxLayout(tile)
        v.setContentsMargins(14, 12, 14, 12)
        v.setSpacing(8)
        head = QtWidgets.QHBoxLayout()
        head.setSpacing(8)
        name = QtWidgets.QLabel(col["rarity"].upper())
        name.setObjectName("Mono")
        name.setStyleSheet(f"color:{rc};{_MONO}font-size:17px;font-weight:800;"
                           "letter-spacing:2px;background:transparent;")
        head.addWidget(name)
        cap = QtWidgets.QLabel(f"→ +{col['cap']}")
        cap.setObjectName("Mono")
        cap.setStyleSheet(f"color:{theme.DIM};{_MONO}font-size:14px;"
                          "font-weight:700;letter-spacing:1px;"
                          "background:transparent;")
        head.addWidget(cap)
        head.addStretch(1)
        # the steps footer rides the header line too, but RIGHT-ALIGNED: name
        # and cap left, `3 steps · at level 25` at the card's right edge
        steps = QtWidgets.QLabel(f"{col['cap']} steps · at level "
                                 f"{table['level']}")
        steps.setObjectName("Mono")
        steps.setStyleSheet(f"color:{theme.DIM};{_MONO}font-size:11.5px;"
                            "letter-spacing:.6px;background:transparent;")
        head.addWidget(steps)
        v.addLayout(head)
        # the material totals as THREE COLUMNS, one per material in the
        # roster: icon over figure over name. Stacked lines left the card
        # ragged — the figures sat in a fixed 58px column with a name of
        # varying length after them, so the three equal-width cards read as
        # three different widths. One column per material ends every card
        # flush, and it echoes the ladder's three icon-headed columns right
        # underneath, so the card row and the ladder row are the same table at
        # two zoom levels. A rarity that never charges a material (Rare has no
        # Spark Crystal) keeps its column with a dim 0 rather than dropping
        # out, so the three cards stay the same shape.
        mats = QtWidgets.QHBoxLayout()
        mats.setContentsMargins(0, 0, 0, 0)
        mats.setSpacing(6)
        for m in table["roster"]:
            iid, mcol = _material_art().get(m, ("", theme.ACCENT))
            n = tot.get(m, 0)
            cell = QtWidgets.QVBoxLayout()
            cell.setContentsMargins(0, 0, 0, 0)
            cell.setSpacing(3)
            if iid:
                icon = C.IconTile(30)
                icon.set("item", iid, mcol)
                centred = QtWidgets.QHBoxLayout()
                centred.setContentsMargins(0, 0, 0, 0)
                centred.addStretch(1)
                centred.addWidget(icon, 0)
                centred.addStretch(1)
                cell.addLayout(centred)
            count = QtWidgets.QLabel(f"{n:g}")
            count.setObjectName("Mono")
            count.setAlignment(QtCore.Qt.AlignCenter)
            count.setStyleSheet(
                f"color:{mcol if n else theme.DIM};{_MONO}font-size:20px;"
                "font-weight:800;background:transparent;")
            cell.addWidget(count)
            label = QtWidgets.QLabel(m)
            label.setAlignment(QtCore.Qt.AlignCenter)
            label.setStyleSheet(f"color:{theme.MUTED};font-size:11.5px;"
                                "background:transparent;")
            cell.addWidget(label)
            mats.addLayout(cell, 1)
        v.addLayout(mats)
        return tile

    # --- the per-step ladders ---------------------------------------------
    def _build_upgrade_matrix(self, table: dict) -> QtWidgets.QGridLayout:
        """The per-step ladders: one per rarity, aligned under its tile.

        Same grid as the tile row — three slots, the same 12px gaps, a lone
        ladder keeping a third of the row. Each ladder is an OUTLINED table: a
        plain 1px border around the rows (no header, no top rule, no card of
        its own — the tile above already shows the rarity's name and frame).
        """
        cols = table["columns"]
        grid = QtWidgets.QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(12)          # the tiles' own spacing
        grid.setVerticalSpacing(12)
        for ci, col in enumerate(cols):
            grid.addWidget(self._upgrade_ladder(table, col), 0, ci)
            grid.setColumnStretch(ci, 1)
        if len(cols) < 3:
            # a lone ladder keeps the three-slot layout, the way a lone tile
            # does: the ladder itself is stretched to ONE, the empty slots to
            # (3 - n) each, so the lone one takes a third of the row and the
            # rest stays empty instead of the ladder smearing across the page
            for ci in range(3):
                grid.setColumnStretch(ci, 1 if ci < len(cols)
                                      else 3 - len(cols))
        return grid

    def _upgrade_ladder(self, table: dict, col: dict) -> QtWidgets.QWidget:
        """One rarity's ladder as an OUTLINED table: a plain 1px border around
        the rows — the tile above carries the card chrome (name, cap, top
        rule), so the ladder keeps only its outline and its contents."""
        rc = col["color"]
        roster = table["roster"]
        box = QtWidgets.QFrame()
        box.setObjectName("UpgradeLadderBox")
        box.setStyleSheet(
            f"QFrame#UpgradeLadderBox{{background:transparent;"
            f"border:1px solid {theme.BORDER};border-radius:4px;}}")
        grid = QtWidgets.QGridLayout(box)
        # a small gutter on ALL FOUR sides: with none on the left/right the
        # row rules ran into the 1px outline and the head icons sat flush
        # against it, so the frame read as uneven against the tile above —
        # one side touching, the others not. Symmetric margins give the
        # outline a clear band to itself on every edge.
        grid.setContentsMargins(6, 4, 6, 4)
        grid.setHorizontalSpacing(0)
        grid.setVerticalSpacing(0)
        grid.setColumnMinimumWidth(0, 40)      # the +N rail
        for mi, m in enumerate(roster):
            grid.addWidget(self._upgrade_material_head(m), 0, 1 + mi)
            # the material columns share the ladder's width
            grid.setColumnStretch(1 + mi, 1)
        for rank in range(1, table["max_rank"] + 1):
            tag = QtWidgets.QLabel(f"+{rank}")
            tag.setObjectName("Mono")
            tag.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            # the rank wears the PROVENANCE accent, like the figure beside it,
            # not the rarity colour: a purple or orange +N next to blue values
            # read as a different kind of number, when it is the same table.
            # The rarity still owns this block — the rules, the outline and
            # the tile above it say which.
            tag.setStyleSheet(
                f"color:{theme.ACCENT};{_MONO}font-size:12px;font-weight:800;"
                "background:transparent;border-bottom:1px solid "
                f"{theme.with_alpha(rc, 50) if rank < table['max_rank'] else 'transparent'};"
                "padding:6px 8px;")
            grid.addWidget(tag, rank, 0)
            by_short = {c["short"]: c for c in table["cells"]
                        if c["rank"] == rank and c["rarity"] == col["rarity"]}
            for mi, m in enumerate(roster):
                grid.addWidget(
                    self._upgrade_cell(by_short[m.split()[-1]],
                                       col["color"], table["level"],
                                       rank < table["max_rank"]),
                    rank, 1 + mi)
        return box

    @staticmethod
    def _upgrade_material_head(name: str) -> QtWidgets.QLabel:
        """One material's column heading: its real game icon ALONE. The name is
        NOT repeated here — the tile columns directly above print it under the
        same icon, so a tooltip would only restate what is on screen. The band
        is the panel's own background; a 1px rule closes it.
        """
        iid, mcol = _material_art().get(name, ("", theme.ACCENT))
        head = QtWidgets.QLabel()
        head.setObjectName("Mono")
        head.setAlignment(QtCore.Qt.AlignCenter)
        if iid:
            # the icon rides the label's own rich-text document, the same way
            # _slot_badge embeds glyphs: one cell, no nested widget. 32px — the
            # icon IS the column header, so it reads at a glance.
            url = f"icon://{iid}"
            tile = C.IconTile(32)
            tile.set("item", iid, mcol)
            head.setTextFormat(QtCore.Qt.RichText)
            head.setText(f'<img src="{url}">')
            doc = head.findChild(QtGui.QTextDocument)
            if doc is not None:
                doc.addResource(QtGui.QTextDocument.ImageResource,
                                QtCore.QUrl(url), tile.pixmap())
        head.setStyleSheet(
            "background:transparent;"
            f"border-bottom:1px solid {theme.BORDER};padding:5px 10px;")
        return head

    def _upgrade_cell(self, c: dict, rail: str, level: int,
                      rule: bool = True) -> QtWidgets.QLabel:
        """One cost cell: the number on the panel's own background, closed by a
        faint rule in the rarity colour (the last rank omits it, so the ladder
        ends clean). Every real figure is the SAME accent whatever its
        provenance — measured, interpolated and derived alike, because they are
        all amounts the player pays. Only the two non-prices are dim: an
        uncharged 0 and a past-cap —, neither of which is a figure.

        No tooltip, by design: nothing in this tab has one, so the colour
        carries no provenance story at all (see _FIGURE_COLOR).
        """
        cell = QtWidgets.QLabel(c["text"])
        cell.setObjectName("Mono")
        cell.setAlignment(QtCore.Qt.AlignCenter)
        if not c["live"] or not c["count"]:
            fg = theme.DIM
        elif c["source"] == istats.UNVERIFIED:
            fg = _UNVERIFIED_COLOR
        else:
            fg = _FIGURE_COLOR
        cell.setStyleSheet(
            f"color:{fg};{_MONO}font-size:15px;font-weight:800;"
            "background:transparent;border-bottom:1px solid "
            f"{theme.with_alpha(rail, 50) if rule else 'transparent'};"
            "padding:6px 14px;")
        return cell
