"""Item-page list support: row roles, display constants, quick-tab sync,
the matched-stat tag delegate, flow layout, and the stat-card builder.
The Drops From body lives in drops.py and the UPGRADE LADDER matrix in
upgrades_matrix.py, so each file stays under the line budget.
"""
from __future__ import annotations

from functools import lru_cache

from PySide6 import QtCore, QtGui, QtWidgets

from ... import theme
from ....data import items as idata
from ....data import names


# toggle / marker glyphs shared by the items pages — the right chevron (a
# collapsed row that opens to the right, like the In Recipes drawer), the
# down/up triangles for expand/collapse blocks (▾ on a collapsed header
# invites expansion, ▴ on an expanded one invites collapse), and the ◆
# that marks rift-acquired gear
GLYPH_RIGHT = "▸"
GLYPH_DOWN = "▾"
GLYPH_UP = "▴"
GLYPH_RIFT = "◆"


# --- quick gear-category tabs -------------------------------------------
# tab label -> category value: the app speaks "Jewelry" (the main page's
# equipment-tab name) where the data layer calls the category "Accessory".
QUICK_TABS = (("Weapons", "Weapons"), ("Armor", "Armor"),
              ("Jewelry", "Accessory"))
CAT_LABEL = {"Accessory": "Jewelry"}   # category display names
# jewelry slots read as Ring / Neck / Trinket rather than the raw
# 'GearFinger' style ids — used for the type chips and sub-sections
_SLOT_LABELS = {"GearFinger": "Ring", "GearNeck": "Neck",
                "GearTrinket": "Trinket"}


def slot_label(t: str) -> str:
    return _SLOT_LABELS.get(t, names.humanize(t))


# weapon variants -> family: all axes / swords / maces share one chip so
# the chip row stays short (Fighter's 10 weapon types collapse to 4)
_WEAPON_FAMILIES = {"Axe": "Axes", "GreatAxe": "Axes",
                     "DualAxes": "Axes", "Sword": "Swords",
                     "GreatSword": "Swords", "DualSwords": "Swords",
                     "Mace": "Maces", "GreatMace": "Maces",
                     "DualMaces": "Maces"}


def type_family(t: str) -> str:
    """The family key a weapon type belongs to (filtering and chips use
    families)."""
    return _WEAPON_FAMILIES.get(t, t)


def family_label(t: str) -> str:
    """Display label for a type's family ('DualAxes' -> 'Axes')."""
    return _WEAPON_FAMILIES.get(t, slot_label(t))


def class_type_map() -> dict[str, set[str]]:
    """Class -> gear families visible to it: equipable + universal ones."""
    items = idata.items()
    universal = {type_family(it.get("type")) for it in items
                 if idata.is_gear(it) and not it.get("classes")}
    out: dict[str, set[str]] = {}
    for cls in idata.gear_classes():
        usable = {type_family(it.get("type")) for it in items
                  if cls in (it.get("classes") or [])}
        out[cls] = usable | universal
    return out


def family_counts(cls: str = "", only: bool = False, mn: str = "",
                  rating: str = "") -> dict[str, int]:
    """Family -> gear item count for the type chips, mirroring the gear-tab
    matcher (class, Only, Min floor, rating)."""
    out: dict[str, int] = {}
    for it in idata.items():
        if not idata.is_gear(it) \
                or idata.category(it.get("type")) == "Crafting":
            continue
        if idata.is_shop_item(it.get("id")):
            continue              # cash-shop cosmetics never show in the list
        if mn and idata.category(it.get("type")) != "Accessory" \
                and rarity_rank(idata.item_display_rarity(it.get("id") or "")) \
                < rarity_rank(mn):
            continue
        if rating and rating not in idata.gear_ratings(it.get("id") or ""):
            continue
        classes = it.get("classes") or []
        if classes:
            if cls and cls not in classes:
                continue         # not usable by the picked class
            if only and cls and len(classes) > 1:
                continue         # Only: dual-class gear drops out
        fam = type_family(it.get("type"))
        out[fam] = out.get(fam, 0) + 1
    return out
# compact stat names for the upgrade-gain chips / search tags — short
# forms keep the step cells narrow
CHIP_SHORT = {
    "Armor Penetration": "A.Pen", "Magic Penetration": "M.Pen",
    "Critical": "Crit", "Fervor": "Fervor", "Health": "HP",
    "Dexterity": "Dex", "Strength": "Str", "Faith": "Faith",
    "Intelligence": "Int", "Vitality": "Vit", "Parry": "Parry",
    "Armor": "Armor", "Spirit": "Spirit", "Will": "Will"}
# list-row roles (single source of truth for the page and its helpers)
ID_ROLE = QtCore.Qt.UserRole          # item id on every data row
HEADER_ROLE = QtCore.Qt.UserRole + 1  # category section rows
CAT_ROLE = QtCore.Qt.UserRole + 2     # category on every list row
TAG_ROLE = QtCore.Qt.UserRole + 3     # matched-stat labels for tag chips
TYPE_HEADER_ROLE = QtCore.Qt.UserRole + 4  # type sub-section rows
TYPE_ROLE = QtCore.Qt.UserRole + 5         # item type on data + header rows

HEADER_CLASS_ROLE = QtCore.Qt.UserRole + 8  # per-class count on a header


def max_item_level(it: dict) -> int:
    """The item's max iLevel — its fully upgraded value at the level it can
    actually reach. Drives the jewelry sort and tile badge."""
    fixed = idata.item_fixed_level(it)
    lvl = fixed if fixed else idata.item_scale_max_level(it.get("id") or "")
    path = idata.upgrade_path(
        it, level=lvl,
        rarity=idata.item_display_rarity(it.get("id") or ""))
    return path[-1] if path else 0


_RARITY_RANK = {"Common": 0, "Uncommon": 1, "Rare": 2, "Epic": 3,
                "Legendary": 4}


def rarity_rank(rarity: str) -> int:
    """Numeric rarity order for the Min floor filter (Common 0 ..
    Legendary 4)."""
    return _RARITY_RANK.get(rarity or "", 0)


def type_header_label(typ: str, count: int) -> str:
    """Type section text: 'SWORD · 12' (one section per gear type)."""
    base = slot_label(typ).upper()
    return f"{base} · {count}" if count else base


def chip_style(color: str, size: int = 11) -> str:
    """A value chip's stylesheet: tinted background, matching border,
    rounded corners. Shared by the Enchants page's signed stat cells and
    rarity trades."""
    return (f"color:{color};font-family:\"{theme.MONO_FONT}\", "
            "\"Consolas\", monospace;"
            f"font-size:{size}px;font-weight:700;"
            f"background:{theme.with_alpha(color, 25)};"
            f"border:1px solid {theme.with_alpha(color, 90)};"
            "border-radius:3px;padding:1px 5px;")


# --- tile-grid sizing -------------------------------------------------
# icon-above-name tiles (the codex look); type headers are full-width items
# whose huge width makes the icon grid wrap to a fresh row (the classic
# grouped-icon-view trick: a header wider than the viewport takes its line)
TILE_W = 84             # tile width — 4 fit per row in the list column
# (84px + 6px spacing = 354px of flow width; with the scrollbar
# reserve that's any window >= ~1028px wide, incl. the 1180px default.
# The old 108px tiles only fit 3 rows at the default window)
TILE_ICON = 40          # icon size inside the tile (matches setIconSize)
TILE_TEXT_MAX = 4       # hard cap — longer names elide instead of growing
# header class-line font: fixed PIXEL size (DPI-independent)
_CLASS_FONT_PX = 11
HEADER_W = 3000         # header width — wider than any viewport
HEADER_H = 26           # header row height (single-line: title only)
HEADER_EXTRA_CLASS = 20 # extra height for the per-class count line


def tile_height(name: str, font: QtGui.QFont | None = None) -> int:
    """Tile height: icon + the wrapped name (1-4 lines), measured with
    QTextLayout so the count never clips the last line."""
    f = font or QtWidgets.QApplication.font()
    fm = QtGui.QFontMetrics(f)
    avail = TILE_W - 16 - 4     # 8px padding each side, minus safety
    tl = QtGui.QTextLayout(name or "", f)
    tl.beginLayout()
    lines = 0
    while True:
        ln = tl.createLine()
        if not ln.isValid():
            break
        ln.setLineWidth(avail)
        lines += 1
    tl.endLayout()
    lines = min(max(lines, 1), TILE_TEXT_MAX)
    return TILE_ICON + 6 + lines * fm.lineSpacing() + 10


def prewarm_tiles(parent=None, batch: int = 160) -> QtCore.QTimer | None:
    """Warm the Items page's tile icons off the critical path, chunked so
    startup never blocks on them. Returns the driving timer (None when
    there's nothing to warm); the caller keeps it alive."""
    from ....data import icons
    from ....data import items as idata
    from ... import theme
    items = [it for it in idata.items() if is_listable_item(it)]
    if not items:
        return None
    timer = QtCore.QTimer(parent)
    timer.setInterval(0)
    idx = 0

    def _tick():
        nonlocal idx
        end = min(idx + batch, len(items))
        for it in items[idx:end]:
            iid = it["id"]
            rar = idata.item_display_rarity(iid)
            icons.tile("item", iid, TILE_ICON, theme.rarity_color(rar))
        idx = end
        if idx >= len(items):
            timer.stop()

    timer.timeout.connect(_tick)
    timer.start()
    return timer


class TileDelegate(QtWidgets.QStyledItemDelegate):
    """Paints the item tile grid: type headers as full-width accent bars,
    item tiles as the codex-style icon-above-name, with the matched-stat
    chips at the tile's top-right on search results."""

    def paint(self, painter, option, index):
        if index.data(TYPE_HEADER_ROLE):
            self._paint_header(painter, option, index)
            return
        opt = QtWidgets.QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        widget = option.widget
        style = widget.style() if widget else QtWidgets.QApplication.style()
        style.drawControl(QtWidgets.QStyle.CE_ItemViewItem, opt, painter,
                          widget)
        tags = index.data(TAG_ROLE) or ()
        if tags:
            self._paint_tags(painter, option, tags)

    def _paint_header(self, painter, option, index):
        """A full-width accent-tinted bar: the type label, with a per-class
        count line under it when the section has class gear."""
        painter.save()
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        r = option.rect
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(QtGui.QColor(theme.with_alpha(theme.ACCENT, 14)))
        painter.drawRoundedRect(r, 4, 4)
        f = QtGui.QFont(option.font)
        f.setBold(True)
        f.setPixelSize(12)
        painter.setFont(f)
        painter.setPen(QtGui.QColor(theme.MUTED))
        title = index.data() or ""
        segs = index.data(HEADER_CLASS_ROLE) or ()
        if not segs:
            painter.drawText(r.adjusted(8, 0, -8, 0),
                             QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
                             title)
            painter.restore()
            return
        painter.drawText(r.adjusted(8, 3, -8, -r.height() // 2),
                         QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop, title)
        cf = QtGui.QFont(option.font)
        cf.setPixelSize(_CLASS_FONT_PX)
        fm = QtGui.QFontMetrics(cf)
        y = r.bottom() - fm.height() - 3
        x = r.left() + 8
        painter.setFont(cf)
        for i, (lbl, col, n) in enumerate(segs):
            txt = f"{lbl} {n}"
            tr = QtCore.QRect(x, y, fm.horizontalAdvance(txt) + 2, fm.height())
            painter.setPen(QtGui.QColor(col))
            painter.drawText(tr, QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop, txt)
            x += tr.width()
            if i < len(segs) - 1:
                sep = " · "
                sr = QtCore.QRect(x, y, fm.horizontalAdvance(sep) + 2,
                                  fm.height())
                painter.setPen(QtGui.QColor(theme.MUTED))
                painter.drawText(sr, QtCore.Qt.AlignLeft
                                | QtCore.Qt.AlignTop, sep)
                x += sr.width()
        painter.restore()

    def _paint_tags(self, painter, option, tags):
        """The matched-stat chips at the tile's top-right corner."""
        f = QtGui.QFont(option.font)
        f.setPixelSize(10)
        f.setBold(True)
        fm = QtGui.QFontMetrics(f)
        x = option.rect.right() - 8
        y = option.rect.top() + 4
        painter.save()
        for t in tags[:2]:
            w = fm.horizontalAdvance(t) + 10
            r = QtCore.QRect(x - w, y, w, 16)
            painter.setBrush(QtGui.QBrush(QtGui.QColor(theme.with_alpha(theme.ACCENT, 25))))
            painter.setPen(QtGui.QPen(QtGui.QColor(theme.with_alpha(theme.ACCENT, 90))))
            painter.drawRoundedRect(r, 3, 3)
            painter.setFont(f)
            painter.setPen(QtGui.QColor(theme.ACCENT))
            painter.drawText(r, QtCore.Qt.AlignCenter, CHIP_SHORT.get(t, t))
            x -= w + 4
        if len(tags) > 2:
            more = "+%d" % (len(tags) - 2)
            w = fm.horizontalAdvance(more) + 10
            r = QtCore.QRect(x - w, y, w, 16)
            painter.setBrush(QtGui.QBrush(QtGui.QColor(theme.with_alpha(theme.MUTED, 30))))
            painter.setPen(QtGui.QPen(QtGui.QColor(theme.with_alpha(theme.MUTED, 70))))
            painter.drawRoundedRect(r, 3, 3)
            painter.setPen(QtGui.QColor(theme.MUTED))
            painter.drawText(r, QtCore.Qt.AlignCenter, more)
        painter.restore()


def quick_pick(page, text: str) -> None:
    """Quick-tab click: set gear category filter and scroll list to top."""
    cat = dict(QUICK_TABS)[text]
    page._items_category = "" if page._items_category == cat else cat
    page._items_type_filter.clear()
    for chip in getattr(page, "_items_type_chips", {}).values():
        chip.setChecked(False)
    page._items_refilter()
    if hasattr(page, "_items_list"):
        page._items_list.scrollToTop()


def sync_quick_tabs(page) -> None:
    """Check the quick tab matching the current category filter, else none."""
    if not hasattr(page, "_items_quick"):
        return
    cat = getattr(page, "_items_category", "")
    for text, c in QUICK_TABS:
        if c == cat:
            page._items_quick.setCurrentText(text)
            return
    page._items_quick.clear()


class CardScroll(QtWidgets.QScrollArea):
    """Detail-pane scroll area whose card hugs its content: it fills the
    pane width, sizes to the content's sizeHint, and only scrolls when the
    content is genuinely taller than the pane."""

    def __init__(self, card: QtWidgets.QWidget):
        super().__init__()
        self.setWidgetResizable(False)
        self.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.setStyleSheet(
            "QScrollArea{background:transparent;border:0;}")
        self.setWidget(card)
        self._card = card

    def _fit(self) -> None:
        """Card = pane width x content height, sized from the layout's real
        heightForWidth (sizeHint only reports single-line label heights)."""
        if self._card is None:
            return
        w = self.viewport().width()
        if w <= 0:
            return
        card = self._card
        lay = card.layout()
        # size from the layout's real heightForWidth (sizeHint only reports
        # single-line label heights, which squashed wrapped Drops From text)
        h = -1
        if lay is not None and lay.hasHeightForWidth():
            h = lay.heightForWidth(w)
        if h <= 0:
            h = card.sizeHint().height()
        card.resize(w, max(h, 1))
        if lay is not None:
            lay.activate()
        deficit = 0
        for lbl in card.findChildren(QtWidgets.QLabel):
            if lbl.isHidden() or not lbl.wordWrap() or not lbl.text():
                continue
            w_lbl = lbl.width()
            if w_lbl <= 30:
                continue
            need = lbl.heightForWidth(w_lbl)
            if need > 0 and lbl.height() < need:
                deficit = max(deficit, need - lbl.height())
        if deficit > 2:
            card.resize(w, card.height() + deficit)
            if lay is not None:
                lay.activate()

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._fit()


class WrapLabel(QtWidgets.QLabel):
    """Word-wrapping label that enforces its wrapped text height at the
    ACTUAL assigned width as a minimum height, so nested box/grid layouts
    can never squash its lines. Plain QLabels report a single-line sizeHint
    and QGridLayout's heightForWidth is only approximate through nested
    layouts, so Drops From source names and location lists would clip
    their last line otherwise."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setWordWrap(True)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # once the layout assigns the width, pin the height to the real
        # wrapped height — updateGeometry reflows the row above so it grows
        # instead of squeezing the text, and relaxes when the pane widens
        # (the column width is layout-stable, so this converges)
        w = self.width()
        if w > 30 and self.text():
            need = self.heightForWidth(w)
            if need > 0 and abs(need - self.minimumHeight()) > 1:
                self.setMinimumHeight(need)


class FlowLayout(QtWidgets.QLayout):
    """Wrapping horizontal layout — active-filter chips flow onto a new line
    when the row fills (the classic Qt flow layout)."""

    def __init__(self, parent=None, margin=0, spacing=4):
        super().__init__(parent)
        if parent is not None:
            self.setContentsMargins(margin, margin, margin, margin)
        self.setSpacing(spacing)
        self._items: list = []

    def addItem(self, item):
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self):
        return QtCore.Qt.Orientations(QtCore.Qt.Orientation(0))

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._do_layout(QtCore.QRect(0, 0, width, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, False)

    def sizeHint(self):
        """Height for the layout's CURRENT width — a flow layout wraps, so
        the height depends on how many chips fit per line."""
        parent = self.parentWidget()
        width = parent.width() if parent is not None else 0
        m = self.contentsMargins()
        if width <= 0:
            return self.minimumSize()
        return QtCore.QSize(width, self.heightForWidth(width))

    def minimumSize(self):
        size = QtCore.QSize()
        for it in self._items:
            w = it.widget()
            if w is None or w.isHidden():
                continue                # hidden chips take no space
            size = size.expandedTo(it.minimumSize())
        m = self.contentsMargins()
        return size + QtCore.QSize(m.left() + m.right(), m.top() + m.bottom())

    def _do_layout(self, rect: QtCore.QRect, test_only: bool) -> int:
        m = self.contentsMargins()
        x, y = rect.x() + m.left(), rect.y() + m.top()
        line_h, used = 0, 0
        space = self.spacing()
        right = rect.right() - m.right()
        for it in self._items:
            w = it.widget()
            if w is None or w.isHidden():
                continue                # hidden chips take no space
            hint = w.sizeHint()
            if x + hint.width() > right and line_h > 0:
                x = rect.x() + m.left()
                y += line_h + space
                line_h = 0
            if not test_only:
                it.setGeometry(QtCore.QRect(QtCore.QPoint(x, y), hint))
            x += hint.width() + space
            line_h = max(line_h, hint.height())
            used = max(used, y + hint.height() + m.bottom())
        return used


class FlowRow(QtWidgets.QWidget):
    """A widget hosting a wrapping FlowLayout whose sizeHint mirrors its
    container's width (the grandparent widget, less that container's layout
    margins), so nested wrapping layouts can't size it to a stale or
    default width. The collapsible drop-kind groups use this for their
    revealed name rows: under a plain QVBoxLayout the group already spans
    full width, and inside a wrapping FlowLayout the expanded group
    reports the container width — taking its own full-width line instead
    of squeezing the names into the collapsed header's width."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._flow = FlowLayout(self)
        self._flow.setContentsMargins(0, 0, 0, 0)
        self._flow.setSpacing(6)
        self.setLayout(self._flow)

    def layout(self) -> FlowLayout:
        return self._flow

    def sizeHint(self):
        gp = self.parentWidget()
        container = gp.parentWidget() if gp is not None else None
        w = container.width() if container is not None else self.width()
        lm = QtCore.QMargins()
        if container is not None and container.layout() is not None:
            lm = container.layout().contentsMargins()
        m = self._flow.contentsMargins()
        avail = max(0, w - lm.left() - lm.right() - m.left() - m.right())
        if avail <= 0:
            return self.minimumSize()
        return QtCore.QSize(avail, self._flow.heightForWidth(avail))


def rebuild_filter_chips(box: QtWidgets.QWidget, lay, active: list,
                         on_clear, clear_all: bool = False) -> None:
    """Rebuild the removable filter-chip row: one chip per active filter
    plus a Clear all link. `active` is [(control, label, value)]; the row
    hides itself when nothing is active unless `clear_all`."""
    while lay.count():
        item = lay.takeAt(0)
        w = item.widget()
        if w is not None:
            w.hide()
            w.setParent(None)
            w.deleteLater()
    for entry in active:
        combo, label, val = entry[0], entry[1], entry[2]
        chip = QtWidgets.QPushButton(f"{label.upper()} · {val}  ×")
        chip.setCursor(QtCore.Qt.PointingHandCursor)
        chip.setStyleSheet(
            f"QPushButton {{ background: {theme.with_alpha(theme.ACCENT, 16)}; "
            f"color: {theme.TEXT}; border: 1px solid {theme.with_alpha(theme.ACCENT, 55)}; "
            "border-radius: 4px; padding: 3px 9px; font-size: 10px; font-weight: 600; }\n"
            f"QPushButton:hover {{ background: {theme.with_alpha(theme.ACCENT, 32)}; }}")

        def _clear_chip(_, ctl=combo):
            if hasattr(ctl, "setCurrentIndex"):
                ctl.setCurrentIndex(0)
            else:
                ctl.clear()

        chip.clicked.connect(_clear_chip)
        lay.addWidget(chip)
    if clear_all:
        clear = QtWidgets.QPushButton("Clear all")
        clear.setCursor(QtCore.Qt.PointingHandCursor)
        clear.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {theme.MUTED}; "
            "border: 0; padding: 3px 6px; font-size: 10px; font-weight: 600; }\n"
            f"QPushButton:hover {{ color: {theme.ACCENT}; }}")
        clear.clicked.connect(on_clear)
        lay.addWidget(clear)
    box.setVisible(bool(active) or clear_all)


# mounts/gliders and the two collection critters live in the Codex
# Collection tab — the Items list doesn't carry them (they have no
# drops/recipes here; the codex card is the info)
CODEX_ONLY_TYPES = frozenset({"Mount", "GearGlider", "Collection"})


def in_codex_collection(typ: str | None) -> bool:
    """True for a type that lives in the Codex Collection tab instead of the
    Items list: mounts, gliders and the collection critters."""
    return (typ or "") in CODEX_ONLY_TYPES


def is_enchant_item(it: dict) -> bool:
    """True for items owned by the Enchants tab: the enchant scrolls
    (ScrollOf*), the Jeweller gem augments (Cut stones, Cursed Eyes) and
    the demon-gear conversions (DemonGearUpgrade*). Other augment families
    stay on the Items list."""
    iid = str(it.get("id") or "")
    if iid.startswith(("ScrollOf", "DemonGearUpgrade")):
        return True
    return iid in {g["item"] for g in idata.gem_augments()}


def enchant_depth(stat: str) -> int:
    """How many enchant-database items touch a stat (scrolls, gems,
    conversions), for the gear page's ENCHANTS chips."""
    n = int(stat in idata.enchant_scrolls())
    n += sum(1 for c in idata.corrupted_scrolls()
             if stat in (c["stat"], c["penalty"]))
    n += sum(1 for g in idata.gem_augments() if stat in g["stats"])
    n += sum(1 for c in idata.enchant_conversions()
             if stat in (c["source"], c["target"]))
    return n


@lru_cache(maxsize=None)
def _listable(item_id: str) -> bool:
    """Cached per-id core of is_listable_item — the build checks every
    item up to three times and the catalog is static. Drops truthiness
    uses has_drops, never the copy-returning shown_drops."""
    it = idata.item(item_id) or {}
    if in_codex_collection(it.get("type")):
        return False
    if is_enchant_item(it):
        return False            # the Enchants tab owns these
    if (it.get("type") or "") == "Currency" \
            and not idata.has_drops(item_id):
        return False            # currency counters that never drop
    if item_id.startswith("LP_"):
        return False            # Lost Package scan rows — no name or info
    nm = it.get("name") or ""
    if nm == item_id or (nm.startswith("[") and nm.endswith("]")):
        # raw-id placeholder name — keep when the row has drops/recipes
        if not (idata.has_drops(item_id) or idata.recipe(item_id)
                or idata.recipe_unlocked_by(item_id)):
            return False
    return True


def is_listable_item(it: dict) -> bool:
    """True when the Items list should carry the catalog row: gear, mats
    and consumables with real info to show. False for codex-only types,
    enchant items, currency counters that never drop, and unreachable scan
    artifacts with raw-id names and no acquisition data."""
    return _listable(it.get("id") or "")


class HScrollCard(QtWidgets.QScrollArea):
    """Horizontal-only scroll card for a wide stat table: the content keeps
    its natural width (scrolling when the pane is narrower) and its height
    plus the scrollbar's reserve so the last row never clips."""

    def __init__(self, widget: QtWidgets.QWidget):
        super().__init__()
        self.setWidgetResizable(True)
        self.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.setStyleSheet(
            "QScrollArea{background:transparent;border:0;}")
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.setMinimumWidth(0)
        widget.setMinimumWidth(widget.sizeHint().width())
        self.setWidget(widget)
        self._content = widget
        # reserve the scrollbar height so the last row never clips
        self.setFixedHeight(widget.sizeHint().height()
                            + self.horizontalScrollBar().sizeHint().height())

    def sizeHint(self) -> QtCore.QSize:
        sh = super().sizeHint()
        return QtCore.QSize(sh.width(), sh.height()
                            + self.horizontalScrollBar().sizeHint().height())


