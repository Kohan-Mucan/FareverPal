"""Weapon-skills ACTION BAR section for the item detail pane.

One square tile per skill in the game's authored order, each with the
skill's icon and name; clicking a tile focuses that skill's full effect
in the detail bar below. The compiler stamps each weapon's skill ids and
data/skills.py resolves the ::token:: templates into readable text.
"""
from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from ... import theme
from ... import components as C
from ....data import icons, skills
from .support import GLYPH_DOWN, GLYPH_UP

# skill type chip/tile accents — the nature labels from data/skills.py map
# onto the palette: base attacks neutral, combos cyan, actives orange,
# powers gold, passives green (statuses blue if one ever appears)
_SKILL_TYPE_COLORS = {
    "Base Attack": theme.DIM,
    "Combo": theme.ACCENT,
    "Active": theme.ORANGE,
    "Power": theme.GOLD,
    "Passive": theme.GOOD,
    "Status": theme.BLUE,
}


def skill_color(typ: str) -> str:
    """The accent color of a skill type (Base Attack / Combo / Active /
    Power / Passive). Unknown types fall back to the muted text color."""
    return _SKILL_TYPE_COLORS.get(typ, theme.MUTED)


_ROMAN = ("", "I", "II", "III", "IV", "V", "VI", "VII", "VIII",
          "IX", "X")


def _roman(n: int) -> str:
    """Roman numeral for the base-chain hit labels (the card-grid look).
    Chains never exceed a handful of hits; beyond X the plain number is
    shown instead of inventing numerals."""
    return _ROMAN[n] if 0 < n < len(_ROMAN) else str(n)


def _merge_base_attacks(skills: list[dict]) -> list[dict]:
    """Collapse every Base Attack entry into ONE tile (the base chain is N
    hits of the same attack). The merged entry keeps the first name/icon,
    the first described hit, a `count` of merged hits, `names` for the
    matched-search ring, and `hits` for the focus panel's per-hit
    breakdown."""
    out: list[dict] = []
    merged: dict | None = None
    for s in skills:
        if s.get("type") == "Base Attack":
            if merged is None:
                merged = dict(s)
                merged["names"] = [s.get("name")]
                merged["count"] = 1
                merged["hits"] = [dict(s)]
            else:
                merged["count"] += 1
                if s.get("name") not in merged["names"]:
                    merged["names"].append(s["name"])
                # keep every hit so the focus panel can break the chain
                # down per hit (the card-grid info) — a later hit carries
                # the chain's only description, so it shows there too
                merged["hits"].append(dict(s))
                if s.get("description") and not merged.get("description"):
                    merged["description"] = s["description"]
            continue
        if merged is not None:
            out.append(merged)
            merged = None
        out.append(s)
    if merged is not None:
        out.append(merged)
    return out


def _matches(entry: dict, matched: set[str]) -> bool:
    """True when the search query matches this tile's skill — any of a
    merged base chain's constituent hits included."""
    return any(n in matched
               for n in (entry.get("names") or [entry.get("name")]))


def _tile_style(col: str, matched: bool) -> str:
    """One action-bar tile's stylesheet: a 1px type-colored border at
    rest; when the skill is matched by the search query, an accent ring +
    tint (the tile-chip feedback translated to a tile)."""
    if matched:
        return (
            f"QPushButton{{background:{theme.with_alpha(theme.ACCENT, 22)};"
            f"border:2px solid {theme.ACCENT};border-radius:4px;}}"
            f"QPushButton:hover{{border:2px solid {theme.ACCENT_LIGHT};}}")
    return (
        f"QPushButton{{background:{theme.PANEL_HI};"
        f"border:1px solid {theme.with_alpha(col, 130)};border-radius:4px;}}"
        f"QPushButton:hover{{border:1px solid {theme.ACCENT_LIGHT};"
        f"background:{theme.PANEL};}}")


def _chip_style(col: str) -> str:
    """The type-chip look: skill-colored text on a skill-tinted chip with a
    soft border — used by the focus panel and the skill popup alike."""
    return (f"color:{col};background:{theme.with_alpha(col, 16)};"
            f"border:1px solid {theme.with_alpha(col, 70)};"
            "border-radius:4px;padding:2px 8px;font-weight:700;"
            "font-size:9px;letter-spacing:1px;")


def _meta_text(s: dict) -> str | None:
    """The skill's real stat line — cooldown and range where the sheet has
    them (the same values the ::cooldown:: / ::range:: slots resolve to).
    Base attacks skip it (their per-hit breakdown covers them); None when
    the skill has neither stat."""
    if s.get("type") == "Base Attack":
        return None
    meta = skills.skill_meta(s["id"])
    if not meta:
        return None
    parts = []
    if "cooldown" in meta:
        parts.append(f"cooldown {meta['cooldown']:g}s")
    if "range" in meta:
        parts.append(f"range {meta['range']:g}m")
    return " · ".join(parts)


class _LinkLabel(QtWidgets.QLabel):
    """A label that reads as a link: skill-colored, pointing hand, underline
    on hover. Emits `clicked` — the focused skill's name opens the popup;
    `size` gives the chain block's show-all toggle the same look at 10px."""
    clicked = QtCore.Signal()

    def __init__(self, text: str, color: str, size: int = 12, parent=None):
        super().__init__(text, parent)
        self._color = color
        self._size = size
        self._hover = False
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self._apply()

    def _apply(self) -> None:
        deco = "underline" if self._hover else "none"
        self.setStyleSheet(
            f"color:{self._color};font-weight:700;font-size:{self._size}px;"
            f"text-decoration:{deco};background:transparent;")

    def enterEvent(self, e):
        self._hover = True
        self._apply()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover = False
        self._apply()
        super().leaveEvent(e)

    def mousePressEvent(self, e):
        if e.button() == QtCore.Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(e)


class _ChainBlock(QtWidgets.QWidget):
    """The merged base chain's per-hit breakdown, clamped to a few hits
    with an inline show-all toggle. Each hit is one discrete row, so
    clamping by count never clips mid-row."""

    _VISIBLE = 2

    def __init__(self, hits: list[dict], chain_desc: str | None,
                 parent=None):
        super().__init__(parent)
        self._expanded = False
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 4, 0, 0)
        lay.setSpacing(2)
        self._rows: list[QtWidgets.QWidget] = []
        for i, hit in enumerate(hits, 1):
            row = self._build_hit(i, hit, chain_desc)
            self._rows.append(row)
            lay.addWidget(row)
        self._more = _LinkLabel(
            f"show all {len(hits)} hits {GLYPH_DOWN}", theme.ACCENT, size=10)
        self._more.clicked.connect(self._toggle)
        self._more.hide()
        lay.addWidget(self._more, 0, QtCore.Qt.AlignRight)
        self._apply()

    @staticmethod
    def _build_hit(i: int, hit: dict, chain_desc: str | None) -> QtWidgets.QWidget:
        """One hit's block: the numeral + bright hit name row (with the
        wind-up · reach as a dim right-aligned suffix), plus the hit's
        own described line when the sheet has one and it isn't already
        the chain's main description."""
        w = QtWidgets.QWidget()
        vl = QtWidgets.QVBoxLayout(w)
        vl.setContentsMargins(0, 0, 0, 0)
        vl.setSpacing(2)
        hrow = QtWidgets.QHBoxLayout()
        hrow.setSpacing(8)
        num = QtWidgets.QLabel(_roman(i))
        num.setFixedWidth(16)
        num.setStyleSheet(
            f'color:{theme.DIM};font-family:"{theme.MONO_FONT}", "Consolas", monospace;'
            "font-size:10px;font-weight:700;background:transparent;")
        hrow.addWidget(num, 0, QtCore.Qt.AlignVCenter)
        hnm = QtWidgets.QLabel(hit.get("name") or hit.get("id")
                               or f"Hit {i}")
        hnm.setStyleSheet(
            f'color:{theme.TEXT};font-family:"{theme.MONO_FONT}", "Consolas", monospace;'
            "font-size:10px;background:transparent;")
        hnm.setWordWrap(True)
        hnm.setTextFormat(QtCore.Qt.PlainText)
        hrow.addWidget(hnm, 1)
        mv = skills.skill_moves(hit.get("id") or "")
        if mv:
            mv_lbl = QtWidgets.QLabel(
                f"wind-up {mv['duration']:g}s · reach {mv['range']:g}m")
            mv_lbl.setStyleSheet(
                f'color:{theme.DIM};font-family:"{theme.MONO_FONT}", "Consolas", monospace;'
                "font-size:10px;background:transparent;")
            mv_lbl.setAlignment(QtCore.Qt.AlignRight
                                | QtCore.Qt.AlignVCenter)
            hrow.addWidget(mv_lbl, 0, QtCore.Qt.AlignVCenter)
        vl.addLayout(hrow)
        # the chain's main line already carries the first described
        # hit's text — don't repeat it under the numeral
        if (hit.get("description")
                and hit["description"] != chain_desc):
            hde = QtWidgets.QLabel(hit["description"])
            hde.setObjectName("Mono")
            hde.setStyleSheet(
                f'color:{theme.MUTED};font-family:"{theme.MONO_FONT}", "Consolas", monospace;'
                "font-size:10px;background:transparent;")
            hde.setWordWrap(True)
            hde.setTextFormat(QtCore.Qt.PlainText)
            hde.setIndent(24)   # under the numeral, past the name
            vl.addWidget(hde)
        return w

    def _toggle(self) -> None:
        self._expanded = not self._expanded
        self._apply()

    def _apply(self) -> None:
        visible = len(self._rows) if self._expanded else self._VISIBLE
        for i, row in enumerate(self._rows):
            row.setVisible(i < visible)
        more_needed = len(self._rows) > self._VISIBLE
        self._more.setVisible(more_needed)
        if more_needed:
            self._more.setText(f"show fewer {GLYPH_UP}" if self._expanded
                               else f"show all {len(self._rows)} hits {GLYPH_DOWN}")


class _SkillPopup(QtWidgets.QFrame):
    """Full skill detail, opened by clicking the focused skill's name: the
    name + type chip, the stat line, the resolved description, and every
    per-rank upgrade line the sheets carry. Frameless Qt.Popup — clicking
    anywhere else closes it."""

    def __init__(self, s: dict, force: str | None = None, parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        self.setWindowFlags(QtCore.Qt.Popup)
        self.setAttribute(QtCore.Qt.WA_StyledBackground, True)
        # `force` overrides the type color — trinkets are only ever Rare,
        # so their passive skills read in the rarity blue, not passive green
        col = force or skill_color(s.get("type"))
        self.setStyleSheet(f"QFrame{{border-left:3px solid {col};}}")
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(8)
        lay.setSizeConstraint(QtWidgets.QLayout.SetFixedSize)
        self.setMinimumWidth(340)
        # header: name + type chip (the tile above already shows the icon,
        # so the popup doesn't duplicate it)
        hd = QtWidgets.QHBoxLayout()
        hd.setSpacing(8)
        nm = QtWidgets.QLabel(s["name"])
        nm.setWordWrap(True)
        nm.setFixedWidth(340)
        nm.setStyleSheet(
            f"color:{col};font-weight:700;font-size:13px;background:transparent;")
        hd.addWidget(nm)
        if s.get("type"):
            chip = QtWidgets.QLabel(s["type"].upper())
            chip.setStyleSheet(_chip_style(col))
            hd.addWidget(chip, 0, QtCore.Qt.AlignTop)
        lay.addLayout(hd)
        # the real stat line
        meta = _meta_text(s)
        if meta:
            ml = QtWidgets.QLabel(meta)
            ml.setObjectName("Mono")
            ml.setStyleSheet(
                f'color:{theme.DIM};font-family:"{theme.MONO_FONT}", "Consolas", monospace;'
                "font-size:10px;background:transparent;")
            lay.addWidget(ml)
        # the resolved description
        if s.get("description"):
            de = QtWidgets.QLabel(s["description"])
            de.setObjectName("Mono")
            de.setStyleSheet(
                f'color:{theme.MUTED};font-family:"{theme.MONO_FONT}", "Consolas", monospace;'
                "font-size:11px;background:transparent;")
            de.setWordWrap(True)
            de.setFixedWidth(340)
            de.setTextFormat(QtCore.Qt.PlainText)
            lay.addWidget(de)
        # per-rank upgrade lines — real sheet data, resolved at each rank
        ranks = skills.skill_rank_descriptions(s["id"])
        if ranks:
            lay.addSpacing(4)
            lay.addWidget(C.SectionHeader("Upgrades"))
            for i, line in enumerate(ranks, 1):
                row = QtWidgets.QHBoxLayout()
                row.setSpacing(10)
                badge = QtWidgets.QLabel(f"RANK {i}")
                badge.setStyleSheet(
                    f"color:{col};background:{theme.with_alpha(col, 16)};"
                    f"border:1px solid {theme.with_alpha(col, 70)};"
                    "border-radius:4px;padding:1px 6px;font-weight:700;"
                    "font-size:9px;letter-spacing:1px;")
                badge.setFixedWidth(56)
                row.addWidget(badge, 0, QtCore.Qt.AlignTop)
                tx = QtWidgets.QLabel(line)
                tx.setObjectName("Mono")
                tx.setStyleSheet(
                    f'color:{theme.MUTED};font-family:"{theme.MONO_FONT}", "Consolas", monospace;'
                    "font-size:11px;background:transparent;")
                tx.setWordWrap(True)
                tx.setFixedWidth(280)
                tx.setTextFormat(QtCore.Qt.PlainText)
                row.addWidget(tx, 1)
                lay.addLayout(row)
        # footer: the skill's id (search matches it too — a useful handle)
        idl = QtWidgets.QLabel(s["id"])
        idl.setObjectName("Mono")
        idl.setStyleSheet(
            f'color:{theme.DIM};font-family:"{theme.MONO_FONT}", "Consolas", monospace;'
            "font-size:9px;background:transparent;")
        lay.addWidget(idl, 0, QtCore.Qt.AlignRight)


class SkillBar(QtWidgets.QWidget):
    """The weapon-skills ACTION BAR section: the tile row + the focused
    skill's detail bar. `matched` is the set of skill names the search
    query currently matches (accent rings); `on_focus_changed` fires when
    the user clicks a tile. The mixin calls update_highlight as the query
    changes live."""

    def __init__(self, skills: list[dict], matched: set[str],
                 on_focus_changed=None, force_color: str | None = None,
                 parent=None):
        super().__init__(parent)
        # the base chain (Base_Attack / 2 / 3) is N hits of ONE attack —
        # collapse it into a single tile so the bar shows one tile per
        # real skill, not one per hit
        skills = _merge_base_attacks(skills)
        self._skills = skills
        self._matched = set(matched)
        self._on_focus_changed = on_focus_changed
        self._force = force_color
        self._rows: list[tuple[dict, QtWidgets.QPushButton, str]] = []
        self._popup: _SkillPopup | None = None
        self._focus_name: QtWidgets.QLabel | None = None
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        lay.addWidget(C.SectionHeader("Skills"))
        bar = QtWidgets.QHBoxLayout()
        bar.setSpacing(8)
        prev_base = False
        for s in skills:
            # the base chain flows into the combo — a small arrow between
            # the two tiles (only when the combo directly follows the
            # chain in the game's authored order; Staff_Censer's chain
            # ends on Castigate and DM_Multispin blocks first, so no
            # arrow there)
            if prev_base and s.get("type") == "Combo":
                ar = QtWidgets.QLabel("→")
                ar.setAlignment(QtCore.Qt.AlignCenter)
                ar.setFixedWidth(16)
                ar.setStyleSheet(
                    f'color:{theme.ACCENT};font-size:14px;font-weight:700;'
                    f'background:transparent;'
                    f'font-family:"{theme.MONO_FONT}", "Consolas", monospace;')
                bar.addWidget(ar, 0, QtCore.Qt.AlignVCenter)
            prev_base = s.get("type") == "Base Attack"
            col = self._force or skill_color(s.get("type"))
            slot = QtWidgets.QVBoxLayout()
            slot.setSpacing(4)
            btn = QtWidgets.QPushButton()
            btn.setFixedSize(44, 44)
            btn.setIcon(QtGui.QIcon(
                icons.tile("Skills", s["id"], 44, col)))
            btn.setIconSize(QtCore.QSize(40, 40))
            btn.setFlat(True)
            btn.setCursor(QtCore.Qt.PointingHandCursor)
            btn.setStyleSheet(
                _tile_style(col, _matches(s, self._matched)))
            btn.clicked.connect(
                lambda _=False, sid=s["id"]: self.focus_sid(sid))
            slot.addWidget(btn, 0, QtCore.Qt.AlignHCenter)
            # the name under the tile, elided to the tile width — the
            # full name appears in the focus bar on click, so no tooltip
            nm = QtWidgets.QLabel()
            nm.setText(nm.fontMetrics().elidedText(
                s["name"], QtCore.Qt.ElideRight, 44))
            nm.setAlignment(QtCore.Qt.AlignHCenter)
            nm.setStyleSheet(
                f"color:{col};font-size:9px;background:transparent;")
            slot.addWidget(nm)
            bar.addLayout(slot, 1)
            self._rows.append((s, btn, col))
        lay.addLayout(bar)
        # the focused-skill detail bar — border-left in the skill's type
        # color, like the mockup's focus panel; re-filled on tile click
        self._focus = QtWidgets.QFrame()
        self._focus.setAttribute(QtCore.Qt.WA_StyledBackground, True)
        self._focus_lay = QtWidgets.QVBoxLayout(self._focus)
        self._focus_lay.setContentsMargins(10, 8, 10, 8)
        self._focus_lay.setSpacing(0)
        lay.addWidget(self._focus)
        self.focus_sid(self._default_focus(skills, self._matched).get("id"))

    def update_highlight(self, matched: set[str]) -> None:
        """Re-apply the matched-search rings as the query changes."""
        self._matched = set(matched)
        for s, btn, col in self._rows:
            btn.setStyleSheet(_tile_style(col, _matches(s, self._matched)))

    def focus_sid(self, sid: str) -> None:
        """Focus a skill by id: its full effect renders in the detail bar,
        and the detail pane re-fits to the new bar height."""
        for s in self._skills:
            if s["id"] == sid:
                self._render_focus(s)
                if self._on_focus_changed:
                    self._on_focus_changed()
                return

    @staticmethod
    def _default_focus(skills: list[dict], matched: set[str]) -> dict:
        """The skill the bar opens on: a matched skill first, else the
        first described Active/Power, else the first described skill."""
        return (next((s for s in skills
                      if _matches(s, matched) and s.get("description")),
                     None)
                or next((s for s in skills
                         if s.get("type") in ("Active", "Power")
                         and s.get("description")), None)
                or next((s for s in skills if s.get("description")), None)
                or skills[0])

    def _open_popup(self, s: dict) -> None:
        """Show the full skill detail popup under the focused name, clamped
        to the screen. Reopening replaces any open popup."""
        if self._popup is not None:
            self._popup.close()
            self._popup.deleteLater()
            self._popup = None
        pop = _SkillPopup(s, self._force, self.window())
        self._popup = pop
        pop.adjustSize()
        anchor = self._focus_name
        if anchor is not None:
            pos = anchor.mapToGlobal(
                QtCore.QPoint(0, anchor.height() + 6))
        else:
            pos = self.window().mapToGlobal(QtCore.QPoint(16, 16))
        scr = QtGui.QGuiApplication.screenAt(pos)
        if scr is None:
            scr = QtGui.QGuiApplication.primaryScreen()
        if scr is not None:
            geo = scr.availableGeometry()
            pos.setX(max(geo.left(), min(pos.x(),
                                        geo.right() - pop.width())))
            pos.setY(max(geo.top(), min(pos.y(),
                                        geo.bottom() - pop.height())))
        pop.move(pos)
        pop.show()

    def _render_focus(self, s: dict) -> None:
        """Fill the detail bar: name + type chip, the stat line and the
        resolved description, with the type color's left accent tying it
        to the focused tile."""
        _clear_layout(self._focus_lay)
        col = self._force or skill_color(s.get("type"))
        self._focus.setStyleSheet(
            f"QFrame{{border-left:3px solid {col};}}")
        ncol = QtWidgets.QVBoxLayout()
        ncol.setSpacing(2)
        hd = QtWidgets.QHBoxLayout()
        hd.setSpacing(8)
        # the name is a link — clicking opens the full skill popup (it
        # already reads as a link: underlined on hover, pointing hand)
        nm = _LinkLabel(s["name"], col)
        nm.clicked.connect(lambda _s=s: self._open_popup(_s))
        self._focus_name = nm
        hd.addWidget(nm)
        if s.get("type"):
            chip = QtWidgets.QLabel(s["type"].upper())
            chip.setStyleSheet(_chip_style(col))
            hd.addWidget(chip, 0, QtCore.Qt.AlignVCenter)
        if s.get("count", 1) > 1:
            # the merged base chain's hit count — real data (the weapon's
            # authored list has N base-attack entries), not a guess
            hits = QtWidgets.QLabel(f"· {s['count']} HITS")
            hits.setObjectName("Mono")
            hits.setStyleSheet(
                f'color:{theme.DIM};font-family:"{theme.MONO_FONT}", "Consolas", monospace;'
                "font-size:10px;background:transparent;")
            hd.addWidget(hits, 0, QtCore.Qt.AlignVCenter)
        hd.addStretch(1)
        ncol.addLayout(hd)
        # the real stat line: cooldown and range where the sheet has them
        # (the same values the ::cooldown:: / ::range:: slots resolve to,
        # so it can't disagree with the description). Base attacks skip it
        # — their per-hit breakdown covers them.
        meta = _meta_text(s)
        if meta:
            ml = QtWidgets.QLabel(meta)
            ml.setObjectName("Mono")
            ml.setStyleSheet(
                f'color:{theme.DIM};font-family:"{theme.MONO_FONT}", "Consolas", monospace;'
                "font-size:10px;background:transparent;")
            ncol.addWidget(ml)
        if s.get("description"):
            # the full resolved description, always shown in full — real
            # sheet text, plain and word-wrapped, never truncated
            de = QtWidgets.QLabel(s["description"])
            de.setObjectName("Mono")
            de.setStyleSheet(
                f'color:{theme.MUTED};font-family:"{theme.MONO_FONT}", "Consolas", monospace;'
                "font-size:11px;background:transparent;")
            de.setWordWrap(True)
            de.setTextFormat(QtCore.Qt.PlainText)
            ncol.addWidget(de)
        if s.get("count", 1) > 1:
            # the chain broken down per hit — the card-grid info, real
            # data only (the weapon's authored list + each hit's lunge
            # step; never a fabricated line). Clamped to the first two
            # hits with an inline show-all toggle (see _ChainBlock).
            ncol.addWidget(_ChainBlock(s.get("hits") or [],
                                       s.get("description")))
        self._focus_lay.addLayout(ncol)


def _clear_layout(lay: QtWidgets.QLayout) -> None:
    """Detach + schedule deletion of every widget in a layout, recursing
    into nested sub-layouts (the focus bar's rows live in one)."""
    while lay.count():
        item = lay.takeAt(0)
        w = item.widget()
        if w is not None:
            w.hide()
            w.setParent(None)
            w.deleteLater()
        elif item.layout() is not None:
            _clear_layout(item.layout())
