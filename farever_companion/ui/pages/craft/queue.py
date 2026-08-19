"""The multi-recipe crafting queue — the Craft page's Craft List tab.

CraftQueueMixin owns the queue state and renders the added recipes (with
steppers) plus the summed, checkable Total Needed bill and total gold.
"""
from __future__ import annotations

import html
import time

from PySide6 import QtCore, QtWidgets

from ... import theme
from ... import components as C
from .... import planner
from ....data import icons
from ....data import items as idata
from ..items.drops import (_deduped_drops, _kind_group_rows, _row_label)
from ..items.support import WrapLabel
from ..items import support

def _craft_material_sources(rows: list[dict]) -> list[dict]:
    """Drop-hint rows under a raw craft material, farmability-first
    (GATHER before MOBS; one-time chest sources are dropped)."""
    rank = {"gatherable": 0, "unit": 1}
    out = [r for r in rows if r.get("kind") != "chest"]
    out.sort(key=lambda r: (rank.get(r.get("kind"), 2),
                            r.get("source", "").lower()))
    return out


def _craft_source_hint(lay, drops: list[dict], on_codex_click,
                       on_expand) -> None:
    """Append a material's farmable drop hints into `lay`: a single-source
    material reads as one compact '← source · N spots' line; longer lists
    become collapsible kind groups (GATHER first, MOBS second). The kind
    headers flow onto ONE wrapping line — 'GATHER · 2 ▾  MOBS · 6 ▾' —
    instead of one line each, and always start collapsed; only the compact
    single-source lines stay visible, so a long farming hint can't wall
    the list until a header is clicked."""
    drops = _craft_material_sources(drops)
    if not drops:
        return
    dedup = _deduped_drops(drops)
    if len({_row_label(d) for d in dedup}) == 1 \
            and not any(d.get("boss") for d in dedup):
        d = dedup[0]
        spots = sum(d.get("n_locs") or 0 for d in dedup)
        lbl = WrapLabel(f"← {_row_label(d)} · {spots} "
                        f"{'spot' if spots == 1 else 'spots'}")
        lbl.setStyleSheet(
            f"color:{theme.MUTED};font-size:12px;background:transparent;")
        lbl.setContentsMargins(34, 0, 0, 0)
        lay.addWidget(lbl)
        return
    grp = QtWidgets.QWidget()
    gl = support.FlowLayout(grp)
    gl.setContentsMargins(34, 0, 0, 0)
    gl.setSpacing(6)
    for w in _kind_group_rows(drops, on_codex_click, on_expand,
                              kind_order=("gatherable", "unit"),
                              collapse_kinds=("gatherable", "unit")):
        gl.addWidget(w)
    lay.addWidget(grp)


class _TrashButton(QtWidgets.QPushButton):
    """The per-line remove button: a trash glyph recolored muted -> danger
    on hover (icon colors can't follow a stylesheet :hover)."""

    def __init__(self):
        super().__init__()
        self.setObjectName("TrashButton")
        self.setFixedSize(26, 26)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setStyleSheet(
            f"QPushButton{{background:transparent;"
            f"border:1px solid {theme.BORDER};border-radius:4px;}}"
            f"QPushButton:hover{{border-color:"
            f"{theme.with_alpha(theme.DANGER, 80)};"
            f"background:{theme.with_alpha(theme.DANGER, 15)};}}")
        self._apply(theme.MUTED)

    def _apply(self, color: str) -> None:
        self.setIcon(icons.ui_qicon("trash", color, 14))
        self.setIconSize(QtCore.QSize(14, 14))

    def enterEvent(self, e):
        self._apply(theme.DANGER)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._apply(theme.MUTED)
        super().leaveEvent(e)


def _crafted_tag(item_id: str, jump) -> QtWidgets.QPushButton:
    """The 'CRAFTED' pill on a material / bill row — click opens the
    material's own recipe."""
    tag = QtWidgets.QPushButton("CRAFTED")
    tag.setFlat(True)
    tag.setCursor(QtCore.Qt.PointingHandCursor)
    tag.setStyleSheet(
        f"QPushButton{{color:{theme.ACCENT};"
        f"background:{theme.with_alpha(theme.ACCENT, 22)};"
        f"border:1px solid {theme.with_alpha(theme.ACCENT, 80)};"
        "border-radius:4px;padding:1px 7px;font-size:10px;"
        "font-weight:700;letter-spacing:1px;}"
        f"QPushButton:hover{{color:{theme.TEXT};"
        f"background:{theme.with_alpha(theme.ACCENT, 45)};}}")
    tag.clicked.connect(lambda *_, mid=item_id: jump(mid))
    return tag


class CraftQueueMixin:
    def _craft_got_get(self) -> dict[str, int]:
        """Gather-progress state: item_id -> units already gathered
        (lazily created; shared by the recipe card and the queue)."""
        got = getattr(self, "_craft_got", None)
        if got is None:
            got = {}
            self._craft_got = got
        return got

    @staticmethod
    def _craft_clear_layout(lay: QtWidgets.QLayout) -> None:
        """Detach + delete every widget in a layout, recursing into sub-layouts."""
        while lay.count():
            it = lay.takeAt(0)
            if it.layout() is not None:
                CraftQueueMixin._craft_clear_layout(it.layout())
            elif it.widget() is not None:
                w = it.widget()
                w.hide()
                w.setParent(None)
                w.deleteLater()

    @staticmethod
    def _craft_bill_row_text(total_count, remaining, name: str, gathered: bool) -> str:
        """The bill row's label: `remaining× name`, struck-through + dimmed
        once gathered (rich text only when gathered, so `&` in names isn't
        parsed as an HTML entity)."""
        if gathered or remaining <= 0:
            return (f'<span style="color:{theme.DIM};'
                    f'text-decoration:line-through;">{total_count:g}×  '
                    f"{html.escape(name)}</span>")
        return f"{remaining:g}×  {name}"

    def _craft_material_progress(self, bill: dict, got: dict | None = None) -> dict[str, int]:
        """Total fulfilled units per item from direct ticks and crafted
        sub-materials at any nesting depth."""
        if got is None:
            got = self._craft_got_get()
        fulfilled: dict[str, int] = {}
        for it in bill["items"]:
            iid = it["item"]
            if got.get(iid, 0) > 0:
                fulfilled[iid] = fulfilled.get(iid, 0) + got[iid]
        for it in bill["items"]:
            iid = it["item"]
            if it.get("crafted") and got.get(iid, 0) >= it["count"]:
                sub_b = idata.craft_bill(iid, it["count"])
                if sub_b:
                    for sub_it in sub_b.get("items", []):
                        sub_id = sub_it["item"]
                        if sub_id != iid:
                            fulfilled[sub_id] = fulfilled.get(sub_id, 0) + sub_it["count"]
        return fulfilled

    def _craft_bill_tag(self, bill: dict, got: dict | None = None,
                        checkable: bool = True) -> str:
        """The Total Needed tag: 'N ITEMS · M RAW', or 'K DONE · R RAW LEFT'
        once gathering starts. If `checkable` is False (recipe card), it
        stays static."""
        if not checkable:
            return f"{len(bill['items'])} ITEMS · {bill['raw_total']:g} RAW"
        fulfilled = self._craft_material_progress(bill, got)
        done = [it for it in bill["items"]
                if fulfilled.get(it["item"], 0) >= it["count"]]
        if not done:
            return f"{len(bill['items'])} ITEMS · {bill['raw_total']:g} RAW"
        done_raw = sum(min(it["count"], fulfilled.get(it["item"], 0))
                       for it in bill["items"] if not it["crafted"])
        raw_left = max(0, bill["raw_total"] - done_raw)
        return f"{len(done)} DONE · {raw_left:g} RAW LEFT"

    def _craft_update_bill_in_place(self, bl, bill: dict, head=None,
                                    got: dict | None = None,
                                    checkable: bool = True) -> bool:
        """Update existing bill rows in place without rebuilding widgets."""
        if bl is None or not hasattr(bl, "_bill_rows"):
            return False
        bl._bill = bill      # the live bill — flips recompute tags from it
        existing_keys = list(bl._bill_rows.keys())
        new_keys = [it["item"] for it in bill.get("items", [])]
        if existing_keys != new_keys:
            return False
        fulfilled = self._craft_material_progress(bill, got) if checkable else {}
        for it in bill["items"]:
            iid = it["item"]
            box, txt, got_tag, _, name = bl._bill_rows[iid]
            new_count = it["count"]
            bl._bill_rows[iid] = (box, txt, got_tag, new_count, name)
            done_cnt = fulfilled.get(iid, 0)
            g = done_cnt >= new_count
            rem = max(0, new_count - done_cnt)
            if box is not None:
                box.blockSignals(True)
                box.setChecked(g)
                box.blockSignals(False)
            txt.setText(self._craft_bill_row_text(new_count, rem, name, g))
            if got_tag is not None:
                got_tag.setVisible(g)
        if head is not None:
            head.set_tag(self._craft_bill_tag(bill, got, checkable=checkable))
        return True

    def _craft_fill_bill(self, bl, bill: dict, jump,
                         checkable: bool = False, head=None,
                         source_hints: bool = False,
                         got: dict | None = None) -> None:
        """Fill a bill layout with one row per aggregated material."""
        if self._craft_update_bill_in_place(bl, bill, head, got):
            return
        self._craft_clear_layout(bl)
        fulfilled = self._craft_material_progress(bill, got) if checkable else {}
        bl._bill = bill      # the live bill — flips recompute tags from it
        bl._bill_rows = {}
        for it in bill["items"]:
            row = QtWidgets.QHBoxLayout()
            row.setSpacing(10)
            done_cnt = fulfilled.get(it["item"], 0)
            gathered = done_cnt >= it["count"]
            rem = max(0, it["count"] - done_cnt)
            txt = QtWidgets.QLabel(self._craft_bill_row_text(
                it["count"], rem, it["name"], gathered))
            txt.setTextFormat(QtCore.Qt.RichText)
            txt.setStyleSheet(f"color:{theme.TEXT};background:transparent;")
            box = None
            if checkable:
                box = QtWidgets.QCheckBox()
                box.setChecked(gathered)
                box.setStyleSheet(
                    f"QCheckBox{{background:transparent;}}"
                    f"QCheckBox::indicator{{width:14px;height:14px;"
                    f"border:1px solid {theme.BORDER};border-radius:3px;"
                    f"background:{theme.PANEL};}}"
                    f"QCheckBox::indicator:checked{{background:{theme.ACCENT};"
                    f"border-color:{theme.ACCENT};}}")
                box.clicked.connect(
                    lambda checked, mid=it["item"], head=head, bill=bill, bl=bl, gdict=got:
                    self._craft_bill_flip(mid, head, bill, checked, bl, gdict))
                row.addWidget(box, 0, QtCore.Qt.AlignVCenter)
            row.addWidget(txt, 1)
            got_tag = QtWidgets.QLabel("✓")
            got_tag.setStyleSheet(
                f"color:{theme.GOOD};background:{theme.with_alpha(theme.GOOD, 16)};"
                f"border:1px solid {theme.with_alpha(theme.GOOD, 70)};"
                "border-radius:3px;padding:1px 6px;font-weight:700;"
                "font-size:11px;")
            got_tag.setVisible(gathered)
            row.addWidget(got_tag, 0, QtCore.Qt.AlignVCenter)
            if it["crafted"]:
                row.addWidget(_crafted_tag(it["item"], jump), 0,
                              QtCore.Qt.AlignVCenter)
            bl.addLayout(row)
            bl._bill_rows[it["item"]] = (box, txt, got_tag, it["count"], it["name"])
            if source_hints and not it["crafted"]:
                _craft_source_hint(bl, idata.shown_drops(it["item"]),
                                   getattr(self, "_codex_jump_to_unit", None),
                                   self._craft_refresh_scroll)

    def _craft_bill_flip(self, item_id: str, head, bill: dict,
                         checked: bool, bl=None,
                         got: dict | None = None) -> None:
        """Checkbox handler: record the gathered count, update the rows in
        place, refresh the summary. The lambda captures the bill from build
        time, so an in-place qty refill leaves it stale — prefer the live
        bill stored on the layout when present."""
        if bl is not None and hasattr(bl, "_bill"):
            bill = bl._bill
        target_got = got if got is not None else self._craft_got_get()
        cur_count = 0
        if bl is not None and hasattr(bl, "_bill_rows") and item_id in bl._bill_rows:
            cur_count = bl._bill_rows[item_id][3]
        else:
            for it in bill.get("items", []):
                if it["item"] == item_id:
                    cur_count = it["count"]
                    break

        if checked:
            # ticking sets the global progress to AT LEAST what this row needs
            target_got[item_id] = max(target_got.get(item_id, 0), cur_count)
        else:
            # un-ticking always clears the progress for this item
            target_got[item_id] = 0

        # Always save the progress to disk so it persists across restarts
        self._craft_queue_save()

        if head is not None:
            head.set_tag(self._craft_bill_tag(bill, target_got))
        if bl is not None and hasattr(bl, "_bill_rows"):
            fulfilled = self._craft_material_progress(bill, target_got)
            for iid, (box, txt, got_tag, count, name) in bl._bill_rows.items():
                done_cnt = fulfilled.get(iid, 0)
                g = done_cnt >= count
                rem = max(0, count - done_cnt)
                if box is not None:
                    box.blockSignals(True)
                    box.setChecked(g)
                    box.blockSignals(False)
                txt.setText(self._craft_bill_row_text(count, rem, name, g))
                if got_tag is not None:
                    got_tag.setVisible(g)

    def _craft_copy_btn(self, get_bill) -> QtWidgets.QPushButton:
        """A small flat COPY button that exports the Total Needed list as
        plain text to the clipboard (flashes ✓ COPIED). `get_bill` is
        called at click time so the copy always reflects the current state."""
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
        btn.clicked.connect(lambda: self._craft_copy_bill(btn, get_bill))
        return btn

    def _craft_copy_bill(self, btn, get_bill) -> None:
        """Copy the current bill export to the clipboard and flash the button."""
        title, bill = get_bill()
        if not bill:
            return
        QtWidgets.QApplication.clipboard().setText(
            self._craft_bill_export(title, bill))
        btn.setText("✓ COPIED")
        QtCore.QTimer.singleShot(
            1400, lambda b=btn: self._craft_copy_restore(b))

    def _craft_copy_restore(self, btn) -> None:
        """Restore the COPY label after the flash."""
        try:
            btn.setText("COPY")
        except RuntimeError:          # card rebuilt while the flash was set
            pass

    def _craft_bill_export(self, title: str, bill: dict) -> str:
        """The Total Needed list as markdown-flavored plain text, paste-ready
        for chat apps (bolded title, one bullet per entry and material)."""
        lines = title.splitlines()
        out = [f"**{lines[0]}**" if lines else ""]
        out += [f"- {l}" if l else "" for l in lines[1:]]
        out += [f"{len(bill['items'])} ITEMS · "
                f"{bill['raw_total']:g} RAW", ""]
        out += [f"- {it['count']:g}×  {it['name']}" for it in bill["items"]]
        if bill.get("gold"):
            out += ["", f"TOTAL GOLD · {bill['gold']:,.0f}"]
        out += ["", f"Exported {time.strftime('%I:%M %p')}"]
        return "\n".join(out)

    def _craft_queue_save(self) -> None:
        """Persist the queue + gathered state to planner.json."""
        planner.queue_save(self._craft_queue_get(), self._craft_got_get())

    def _craft_queue_get(self) -> list[dict]:
        """The queue entries [{item, qty, name}], lazily created."""
        queue = getattr(self, "_craft_queue", None)
        if queue is None:
            queue = []
            self._craft_queue = queue
        return queue

    def _craft_queue_title(self) -> str:
        """The queue's export title: 'CRAFTING QUEUE' over one `N×  name`
        line per entry."""
        return "CRAFTING QUEUE\n\n" + "\n".join(
            f"{e['qty']:g}×  {e['name']}" for e in self._craft_queue_get())

    def _craft_queue_add(self, item_id: str, st: dict | None = None) -> None:
        """Add the current CRAFT × batch of `item_id` to the queue, merging
        into an existing entry (quantities sum), flash the button and
        refresh the badge."""
        qty = max(1, getattr(self, "_craft_qty", 1))
        r = idata.recipe(item_id) or {}
        queue = self._craft_queue_get()
        for e in queue:
            if e["item"] == item_id:
                e["qty"] += qty
                break
        else:
            queue.append({"item": item_id, "qty": qty,
                         "name": r.get("name") or item_id})
        self._craft_rail_sync()       # no-op before the craft page builds
        self._craft_queue_save()
        btn = (st or {}).get("add_btn")
        if btn is not None:
            btn.setText("✓ IN QUEUE")
            QtCore.QTimer.singleShot(
                1400, lambda s=st: self._craft_queue_btn_restore(s))

    def _craft_queue_btn_restore(self, st: dict) -> None:
        """Restore the ADD TO QUEUE label after the flash."""
        btn = (st or {}).get("add_btn")
        if btn is None:
            return
        try:
            btn.setText("ADD TO QUEUE")
        except RuntimeError:          # card rebuilt since the flash was set
            pass

    def _craft_queue_set_qty(self, idx: int, qty: int) -> None:
        queue = self._craft_queue_get()
        if 0 <= idx < len(queue):
            queue[idx]["qty"] = qty
            self._craft_queue_save()
        self._craft_rail_sync_bill()

    def _craft_rail_sync_bill(self) -> None:
        """Update the Total Needed bill and total gold in place on stepper
        changes without rebuilding the active steppers."""
        queue = self._craft_queue_get()
        bill = idata.craft_bill_many(tuple((e["item"], e["qty"]) for e in queue))
        head = getattr(self, "_craft_rail_head", None)
        bl = getattr(self, "_craft_rail_bill_lay", None)
        if head is not None and bl is not None and bill is not None:
            self._craft_fill_bill(bl, bill, self._craft_jump_to_recipe,
                                  checkable=True, head=head, source_hints=True)
        tot = getattr(self, "_craft_rail_total_gold", None)
        if tot is not None:
            tot.setText(f"TOTAL GOLD · {bill['gold']:,.0f}" if bill and bill.get("gold") else "")
        self._craft_queue_badge()
        sync = getattr(self, "_craft_tiles_sync_queue", None)
        if sync is not None:
            sync()

    def _craft_queue_remove(self, idx: int) -> None:
        queue = self._craft_queue_get()
        if 0 <= idx < len(queue):
            del queue[idx]
            self._craft_queue_save()
        self._craft_rail_sync()

    def _craft_queue_clear(self) -> None:
        self._craft_queue_get().clear()
        self._craft_got_get().clear()
        self._craft_queue_save()
        self._craft_rail_sync()

    def _craft_queue_badge(self) -> None:
        """Keep the Craft List tab's count in sync with the queue size (the
        tab is the queue's counter — no separate header pill). Hidden at
        zero, like the Farm tab."""
        tabs = getattr(self, "_craft_tabs", None)
        if tabs is None or not hasattr(tabs, "set_text"):
            return
        n = len(self._craft_queue_get())
        tabs.set_text("Craft List",
                      f"Craft List · {n}" if n else "Craft List")

    def _craft_rail_sync(self) -> None:
        """Sync the queue badge, tile highlights, and craft list contents."""
        self._craft_queue_badge()
        sync = getattr(self, "_craft_tiles_sync_queue", None)
        if sync is not None:
            sync()
        if getattr(self, "_craft_rail_built", False):
            self._craft_rail_build()
            return
        rail = getattr(self, "_craft_rail", None)
        empty = getattr(self, "_craft_rail_empty", None)
        queue = self._craft_queue_get()
        if rail is not None:
            rail.setVisible(bool(queue))
        if empty is not None:
            empty.setVisible(not queue)

    def _craft_rail_build(self) -> None:
        """Rebuild the Craft List tab as TWO columns: the added recipes
        (qty stepper, name, gold, remove) on the LEFT, the summed checkable
        Total Needed bill on the RIGHT, with the total gold + CLEAR pinned
        under the entries — the full-width queue page (no cramped rail, no
        side scroll). While the queue is empty the rail card hides and the
        page's empty-state card shows; every mutation re-renders it in
        place (each stepper / remove click completes before the rebuild, so
        no increment is lost)."""
        rail = getattr(self, "_craft_rail", None)
        lay = getattr(self, "_craft_rail_lay", None)
        if rail is None or lay is None:
            return
        self._craft_rail_built = True
        # every mutation lands here — keep the header pill + tile rings in
        # sync (also renders them on first build with the persisted queue)
        self._craft_queue_badge()
        sync = getattr(self, "_craft_tiles_sync_queue", None)
        if sync is not None:
            sync()
        self._craft_clear_layout(lay)
        queue = self._craft_queue_get()
        if not queue:
            rail.hide()
            empty = getattr(self, "_craft_rail_empty", None)
            if empty is not None:
                empty.show()
            return
        rail.show()
        empty = getattr(self, "_craft_rail_empty", None)
        if empty is not None:
            empty.hide()

        cols = QtWidgets.QHBoxLayout()
        cols.setSpacing(24)

        left = QtWidgets.QVBoxLayout()
        left.setSpacing(12)      # same vertical rhythm as the Recipes tab
        # no "Craft List · N RECIPES" header — the tab already carries the
        # queue count, so the entries start the column directly
        for i, e in enumerate(queue):
            iid, iname = e["item"], e["name"]
            # Look up the item directly to get its display rarity and icon,
            # which is more robust than relying on the recipe lookup.
            it = idata.item(iid) or {}
            rar = idata.item_display_rarity(iid)
            col = theme.rarity_color(rar)
            row = QtWidgets.QHBoxLayout()
            row.setSpacing(8)
            icon = QtWidgets.QLabel()
            icon.setFixedSize(34, 34)

            # Smart icon resolution (Centralized in icons.item_tile)
            icon.setPixmap(icons.item_tile(iid, iname, 34, col))
            row.addWidget(icon, 0, QtCore.Qt.AlignVCenter)
            nm = WrapLabel(
                f'<a href="open" style="color:{theme.ACCENT};'
                'text-decoration:none;">'
                f"{html.escape(e['name'])}</a>")
            nm.setOpenExternalLinks(False)
            nm.setTextInteractionFlags(QtCore.Qt.TextBrowserInteraction)
            nm.linkActivated.connect(
                lambda _=None, mid=e["item"]: self._craft_jump_to_recipe(mid))
            row.addWidget(nm, 1)
            step = C.Stepper(value=e["qty"], lo=1, hi=999)
            step.setFixedWidth(84)
            step.valueChanged.connect(
                lambda v, idx=i: self._craft_queue_set_qty(idx, v))
            row.addWidget(step, 0, QtCore.Qt.AlignVCenter)
            rm = _TrashButton()
            rm.clicked.connect(lambda *_, idx=i: self._craft_queue_remove(idx))
            row.addWidget(rm, 0, QtCore.Qt.AlignVCenter)
            left.addLayout(row)
            if i < len(queue) - 1:
                rule = QtWidgets.QFrame()
                rule.setFixedHeight(1)
                rule.setStyleSheet(f"background:{theme.BORDER};")
                left.addWidget(rule)

        bill = idata.craft_bill_many(
            tuple((e["item"], e["qty"]) for e in queue))
        footer = QtWidgets.QHBoxLayout()
        footer.setSpacing(8)
        self._craft_rail_total_gold = None
        if bill:
            total = QtWidgets.QLabel(f"TOTAL GOLD · {bill['gold']:,.0f}" if bill.get("gold") else "")
            total.setStyleSheet(
                f'color:{theme.ORANGE};font-family:"{theme.MONO_FONT}", "Consolas", monospace;'
                "font-size:12px;font-weight:700;background:transparent;")
            self._craft_rail_total_gold = total
            footer.addWidget(total)
        footer.addStretch(1)
        clear = QtWidgets.QPushButton("CLEAR")
        clear.setIcon(icons.ui_qicon("trash", theme.DANGER, 14))
        clear.setIconSize(QtCore.QSize(14, 14))
        clear.setCursor(QtCore.Qt.PointingHandCursor)
        clear.setStyleSheet(
            f"QPushButton{{color:{theme.DANGER};background:transparent;"
            f"border:1px solid {theme.with_alpha(theme.DANGER, 80)};"
            "border-radius:4px;padding:5px 10px;font-size:11px;"
            "font-weight:700;letter-spacing:1px;}"
            f"QPushButton:hover{{background:{theme.with_alpha(theme.DANGER, 18)};}}")
        clear.clicked.connect(self._craft_queue_clear)
        footer.addWidget(clear)
        left.addLayout(footer)
        left.addStretch(1)
        cols.addLayout(left, 1)

        right = QtWidgets.QVBoxLayout()
        right.setSpacing(12)     # same vertical rhythm as the Recipes tab
        self._craft_rail_head = None
        self._craft_rail_bill_lay = None
        if bill:
            head = C.SectionHeader("Total Needed",
                                   tag=self._craft_bill_tag(bill))
            self._craft_rail_head = head
            hrow = QtWidgets.QHBoxLayout()
            hrow.setSpacing(8)
            hrow.addWidget(head, 1)
            hrow.addWidget(self._craft_copy_btn(
                lambda: (self._craft_queue_title(),
                         idata.craft_bill_many(
                             tuple((e["item"], e["qty"])
                                   for e in self._craft_queue_get())))),
                0, QtCore.Qt.AlignVCenter)
            right.addLayout(hrow)
            body = QtWidgets.QWidget()
            bl = QtWidgets.QVBoxLayout(body)
            bl.setContentsMargins(0, 0, 0, 0)
            bl.setSpacing(2)
            self._craft_rail_bill_lay = bl
            self._craft_fill_bill(bl, bill, self._craft_jump_to_recipe,
                                  checkable=True, head=head,
                                  source_hints=True)
            right.addWidget(body)
        right.addStretch(1)
        cols.addLayout(right, 1)

        lay.addLayout(cols)
        self._craft_refresh_scroll()
