"""Party page, the left-hand PLAYERS roster: the hero cards and the eliding
name label they use.

Split out of `party_profile.py` (which holds the paper-doll grid + piece
panel) the same way the other page splits were made — one coherent concern
per file, so neither reabsorbs the other past the 840-line budget.
`PartyProfileMixin` re-exports this mixin, so the page's own class
composition (`class PartyPageMixin(PartyProfileMixin)`) and every call site
stay exactly as they were.

The cards are rebuilt from the live walk on every refresh, so there is no
per-card update path: `_party_roster_fill` owns the whole column. Every
number it draws is best-effort — a card never fails to build because a level
or a session rate did not resolve.
"""
from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from .. import theme
from ...data import icons as gicons

# The roster avatar: the SAME tinted tile the Entity HUD gives every hero
# (`ui_icon="player"` -> `IconTile.set_ui_icon` -> `icons.tile_ui`, tinted with
# `theme.HERO[hero_class]`). 2026-09-26: the avatars used a class->glyph guess
# (swords/orb/arrow), which is not what a hero looks like anywhere else in the
# app. 2px up from 34 with the rest of the page's icons, then 2px more with the
# final font pass.
_AVATAR_PX = 38
_AVATAR_GLYPH = "player"        # the Entity HUD's hero glyph

# How tall the Players block may grow before it starts scrolling (2026-09-26).
# Its height is the roster's own height below this — the block hugs a small
# party instead of hanging a big empty frame under it.
_ROSTER_MAX_H = 520

# How much width a roster card's NAME will ask for (2026-09-26). The Players
# column is 240px minimum; the card spends 18 on its own padding, 38 on the
# avatar, 9+9 on spacing and ~40 on the rate, which leaves ~120 for the name
# — and this cap is what asks for exactly that, so a 60-character name elides
# instead of widening the column and shoving the paper doll sideways.
_NAME_MAX_W = 120

# How much WIDER the Players column is made, in CHARACTERS of the hero name
# (2026-09-26). It is counted in characters rather than pixels on purpose: the
# name elides because the column is too narrow, "5 characters wider" is the
# request, and a character is not a fixed number of pixels — it is whatever the
# real name font measures once the fonts are resolved. So the column grows by
# five measured characters, on whatever machine is running it, rather than by a
# magic 45px that would be five characters on one screen and three on another.
_ROSTER_EXTRA_CHARS = 5

# The roster name's type size (2026-09-26). It was 22px, from the "+2px on
# everything" pass, and that was simply too big: it made the name the largest
# text on the page (the next largest is the 16px captions) inside a column
# that is 240px wide, so most real names elided — a 4-character name measured
# 88px of a 120px budget, and anything longer became "BobT…". 18px keeps the
# name the clear primary of its card, one step above the captions, and fits
# roughly eight characters in the same box.
_NAME_PX = 18


def _has_ink(pm) -> bool:
    """True when a pixmap actually drew something.

    `tile_ui` paints its tinted square whether or not the glyph inside it
    resolved, so `isNull()` is no answer for "did the icon draw" (that mistake
    is what left every class avatar an empty box — see `icons.ui_icon`, which
    now returns a NULL pixmap for a name it cannot resolve).
    """
    if pm is None or pm.isNull():
        return False
    img = pm.toImage().convertToFormat(QtGui.QImage.Format_ARGB32)
    return any(img.pixelColor(x, y).alpha() > 24
               for y in range(img.height()) for x in range(img.width()))


class _ElideLabel(QtWidgets.QLabel):
    """A single-line label that elides ("BobTheVeryLongChar…") instead of
    wrapping — mockup A's roster name, and a buff row's name line. Eliding
    needs the label's REAL width, so this overrides resizeEvent to re-trim
    the text as the widget shrinks and grows (a QFontMetrics elide computed once
    goes stale on the next layout pass).

    `max_w` caps how much width the label will ASK for (0 = as much as the text
    wants). It is what keeps a long name from widening the 240px Players
    column, now that the policy below is not doing that job by accident.
    """

    lines = 1

    def __init__(self, text: str = "", max_w: int = 0) -> None:
        super().__init__(text)
        self._full = text
        self.max_w = max_w
        self.setWordWrap(self.lines > 1)
        # Preferred, NOT Ignored (2026-09-26). A layout reads an Ignored
        # widget's size hint as WIDTH 0, so the label was handed a 0px box,
        # `elidedText` on that returned "", and the text measured itself as
        # 0 wide — so the layout gave it 0 again, forever. Every roster card
        # showed a blank where the name belongs, and every buff name with it,
        # while the full text sat in the tooltip the whole time (which is why
        # every test that read the tooltip kept passing). Ignored was here to
        # stop a long name widening the column; `max_w` plus the ellipsis-width
        # minimum below do that honestly, and the name is VISIBLE.
        #
        # Fixed vertically for a multi-line label: with Preferred, a wrapped
        # QLabel is asked heightForWidth and collapses back to one line the
        # moment its text happens to fit — which is the opposite of the point.
        self.setSizePolicy(QtWidgets.QSizePolicy.Preferred,
                           QtWidgets.QSizePolicy.Fixed if self.lines > 1
                           else QtWidgets.QSizePolicy.Preferred)

    def hasHeightForWidth(self):                          # noqa: N802 (Qt)
        """Off, or the layout measures this label one line tall (2026-09-26).

        A word-wrapped QLabel advertises heightForWidth, and a QBoxLayout
        then asks `heightForWidth(width)` INSTEAD of `sizeHint()` — which
        returns the wrapped height of the current text, i.e. one line for a
        short name. Overriding `sizeHint` alone therefore did nothing: the
        two-line name area collapsed to a line exactly when the name was
        short enough to fit one. `sizeHint()` is the answer once this is off.
        """
        return False

    def sizeHint(self):                                   # noqa: N802 (Qt)
        """`lines` lines tall, always (2026-09-26), so the card is the same
        height for every item and short names do not shrink the block.

        The WIDTH is measured off `_full` with the font metrics, NOT taken
        from `super().sizeHint()` — QLabel measures whatever text it is
        holding, and by the time a layout asks, the text is usually already
        elided (2026-09-26). That is a loop with no way out: elide shrinks
        the text, the shrunken text shrinks the hint, the hint gets the
        layout to hand back less room, and the name ends up one ellipsis
        wide in a card with 120px to spare.
        """
        base = super().sizeHint()
        fm = self.fontMetrics()
        w = fm.horizontalAdvance(self._full) + 4
        if self.max_w:
            w = min(w, self.max_w)
        if self.lines <= 1:
            return QtCore.QSize(w, base.height())
        return QtCore.QSize(w, (fm.height() + 2) * self.lines)

    def minimumSizeHint(self):                            # noqa: N802 (Qt)
        """The floor is one ELLIPSIS wide (2026-09-26), not the unwrapped
        text: a long name must be able to shrink to nothing readable rather
        than force its column wider, and the elide above takes over from
        there."""
        base = super().minimumSizeHint()
        return QtCore.QSize(self.fontMetrics().horizontalAdvance("…") + 4,
                            base.height())

    def _box_w(self) -> int:
        """Usable text width: the label's, less the 1px each side so a word
        that exactly fills it does not wrap on a hair — and never more than
        `max_w`, so a name elides at its cap even in a wide column."""
        w = self.width() - 2
        if self.max_w:
            w = min(w, self.max_w - 2)
        return max(10, w)

    def _wrap_lines(self, text: str, width: int) -> int:
        """How many lines `text` occupies when word-wrapped at `width`.

        Measured by hand rather than with `QFontMetrics.boundingRect(rect,…)`:
        that overload CLAMPS its result to the rect it is handed, so a 3-line
        name in a 2-line box measured back as "fits" and was never clipped.
        A greedy wrap is what QLabel's own layout does, and it answers the
        same question the painter will.
        """
        fm = self.fontMetrics()
        count, cur = 1, ""
        for word in text.split(" "):
            cand = f"{cur} {word}".strip()
            if cur and fm.horizontalAdvance(cand) > width:
                count += 1
                cur = word
            else:
                cur = cand
        return count

    def _fits(self, text: str) -> bool:
        """Does this text fit the box — in lines AND in words?

        A word wider than the box is not broken by TextWordWrap: it overflows
        sideways and still measures as one line, so the width is checked
        separately or a long single word would be left unclipped.
        """
        fm = self.fontMetrics()
        width = self._box_w()
        if any(fm.horizontalAdvance(word) > width
               for word in text.split(" ")):
            return False
        return self._wrap_lines(text, width) <= self.lines

    def _trim(self) -> None:
        # No box yet — do NOT trim (2026-09-26).
        #
        # A label is resized to 0 by the first layout pass, before the layout
        # hands out real widths, and trimming into a 0px box ELIDES THE TEXT
        # AWAY: `elidedText` on a box nothing fits in returns "". The label
        # then measures itself from that empty text, so its sizeHint is ~0,
        # so the layout gives it ~0 again — and the name never comes back.
        # The visible symptom was the roster showing a class · level line with
        # a blank where the name belongs, for every hero, while the full name
        # sat happily in the tooltip the whole time (which is why the tests
        # that read the tooltip kept passing). The text is left alone until
        # there is a real box to trim into; the next resize trims properly.
        if self.width() <= 0:
            return
        fm = self.fontMetrics()
        if self.lines <= 1:
            self.setText(fm.elidedText(self._full, QtCore.Qt.ElideRight,
                                       self._box_w()))
            return
        # two lines: use them, then clip. Qt has no multi-line elide, so the
        # words are dropped off the end one at a time until the rest fits with
        # an ellipsis (2026-09-26: "Beefury, Blessed Blade of the Farseeker"
        # used to lose "seeker" to a one-line elide while the card had a whole
        # second line free).
        if self._fits(self._full):
            self.setText(self._full)
        else:
            words = self._full.split(" ")
            shown = ""
            for i in range(len(words) - 1, 0, -1):
                cand = " ".join(words[:i]) + "…"
                if self._fits(cand):
                    shown = cand
                    break
            if not shown:
                # two lines are not enough even for two words: fill the first
                # line by eliding the first word itself, which is the only way
                # to drop into the box.
                shown = fm.elidedText(words[0], QtCore.Qt.ElideRight,
                                      self._box_w())
                for word in words[1:]:
                    cand = f"{shown} {word}".strip()
                    if self._fits(cand):
                        shown = cand
            self.setText(shown)

    def set_full(self, text: str) -> None:
        """Swap the text this label elides FROM — setText() would be undone by
        the next resize, since the full string is what gets re-trimmed."""
        self._full = text
        self._trim()

    def resizeEvent(self, e) -> None:                      # noqa: N802 (Qt)
        self._trim()
        super().resizeEvent(e)


class _Elide2Label(_ElideLabel):
    """`_ElideLabel` with two lines of room before it clips: a piece name like
    "Beefury, Blessed Blade of the Farseeker" reads over two lines with only
    the tail dropped, and a short name simply sits on the first (the area is
    always two lines tall, so the card it lives in never changes height)."""

    lines = 2


class PartyRosterMixin:
    """The roster COLUMN: one card per live hero (mockup A), rebuilt whole on
    every refresh. Reads the scene units + the DPS session through the model;
    writes nothing back."""

    # ------------------------------------------------------------ roster card
    def _party_roster_card(self, addr: int, name: str, cls: str,
                           meta: str, rate: str, is_me: bool,
                           selected: bool) -> QtWidgets.QFrame:
        """One Overview-roster card (the mockup's A block): class-color
        avatar initial, name + YOU tag, class · level line, session rate.

        Clicking selects the hero (see _party_pick_addr on the page).

        The card is built ONCE per hero and then updated in place through
        `card._party_update` (2026-09-26). It used to be destroyed and rebuilt
        on every refresh, which is what made the whole page flicker: the
        roster runs off the same tick as everything else, and a hero's HP
        changes every tick, so the "content" changed every tick and every card
        was torn down, re-created and re-laid-out 2x a second. Only the two
        TEXT lines and the selected styling can change without the hero list
        changing, so only those are updated (see _party_roster_fill)."""
        card = QtWidgets.QFrame()
        card.setObjectName("Card")

        def _style(sel: bool) -> None:
            # Selection is the ONLY state a roster card has (2026-09-26): the
            # hover brighten and the pointing-hand cursor are gone from this
            # page, so a card looks the same under the mouse as it does at
            # rest. Which hero is selected is still carried by the accent fill
            # and border.
            base_bg = theme.ACCENT_DIM if sel else theme.PANEL
            border = theme.ACCENT if sel else theme.BORDER
            card.setStyleSheet(
                f"QFrame#Card{{background:{base_bg};border:1px solid "
                f"{border};border-radius:6px;}}")

        _style(selected)
        lay = QtWidgets.QHBoxLayout(card)
        lay.setContentsMargins(9, 7, 9, 7)
        lay.setSpacing(9)
        # the class colour from theme.HERO — the same map the Entity HUD tints
        # its hero rows with (and the page's own fallback for an unknown class)
        col = theme.class_color(cls)
        av = QtWidgets.QLabel()
        av.setFixedSize(_AVATAR_PX, _AVATAR_PX)
        av.setAlignment(QtCore.Qt.AlignCenter)
        av.setStyleSheet(f"color:{col};font-size:22px;font-weight:800;")
        # The Entity HUD's hero tile: the accent-tinted square with the player
        # glyph in the class colour (theme.HERO, the same map). The tile draws
        # its own fill and border, so the label draws none — a letter fallback
        # (no class, or an icon set without the glyph) gets the framed box it
        # needs here instead.
        pm = gicons.tile_ui(_AVATAR_GLYPH, _AVATAR_PX, col)
        glyph = gicons.ui_icon(_AVATAR_GLYPH, col, _AVATAR_PX - 6)
        if _has_ink(glyph):
            av.setPixmap(pm)
        else:
            av.setStyleSheet(
                f"color:{col};font-size:22px;font-weight:800;"
                f"border:1px solid {col};border-radius:4px;")
            av.setText((cls or name or "?")[:1].upper())
        lay.addWidget(av)
        mid = QtWidgets.QVBoxLayout()
        mid.setSpacing(1)
        mid.setContentsMargins(0, 0, 0, 0)
        top = QtWidgets.QHBoxLayout()
        top.setSpacing(4)
        top.setContentsMargins(0, 0, 0, 0)
        nm = _ElideLabel(name, max_w=_NAME_MAX_W)
        # mockup A: names stay on ONE line and elide ("BobTheVeryLongChar…")
        # instead of wrapping the card downward — the full name is the card's
        # tooltip. `max_w` is what keeps a long name from forcing the 240px
        # column wide now that the label asks for its real width (see
        # _ElideLabel on why Ignored was the wrong tool for that).
        nm.setStyleSheet(
            f"font-size:{_NAME_PX}px;font-weight:700;color:{theme.TEXT};")
        top.addWidget(nm, 1)
        mid.addLayout(top)
        sub = QtWidgets.QLabel(meta)
        sub.setWordWrap(True)
        # Ignored IS right here, unlike on the name above (2026-09-26): this
        # label WRAPS, so QLabel's minimumSizeHint is the unwrapped width and
        # would let one long "Priest · Lv 30 · HP 1,234,567" widen the whole
        # column. It is safe because nothing is elided out of it — a wrap
        # measures the text it is holding, and a 0px box loses nothing.
        sub.setSizePolicy(QtWidgets.QSizePolicy.Ignored,
                          QtWidgets.QSizePolicy.Preferred)
        sub.setStyleSheet(f"color:{theme.MUTED};font-size:14px;")
        # The YOU tag rides the META line, not the name's (2026-09-26). It used
        # to sit right after the name, where it cost 44px of a 131px line —
        # so YOUR OWN name was the most elided name in the column, which is the
        # one name nobody needs shortened. The meta line has the slack for it
        # (a class, a level and an HP read in 131px), and the name now gets
        # the whole line it was always being promised.
        bot = QtWidgets.QHBoxLayout()
        bot.setSpacing(4)
        bot.setContentsMargins(0, 0, 0, 0)
        bot.addWidget(sub, 1)
        if is_me:
            tag = QtWidgets.QLabel("YOU")
            tag.setStyleSheet(
                f"font-size:11px;font-weight:800;color:{theme.ACCENT};"
                f"background:{theme.ACCENT_DIM};border-radius:2px;"
                f"padding:1px 3px;")
            bot.addWidget(tag, 0, QtCore.Qt.AlignVCenter)
        mid.addLayout(bot)
        lay.addLayout(mid, 1)
        rt = QtWidgets.QLabel(rate)
        rt.setObjectName("Mono")
        rt.setStyleSheet(f"color:{theme.DIM};font-size:15px;")
        lay.addWidget(rt)

        def _pick(_e=None, a=addr, n=name):
            self._party_pick_addr(a, n)

        card.mousePressEvent = _pick                     # type: ignore[method-assign]
        # The in-place update path (2026-09-26): only the two text lines and
        # the selected styling can change while the hero list is the same.
        # `setText` is a no-op when the text is identical, so an unchanged tick
        # touches no repaint at all.
        card._party_addr = addr                        # type: ignore[attr-defined]
        card._party_name = name                        # type: ignore[attr-defined]

        def _update(new_meta: str, new_rate: str, sel: bool,
                    new_name: str | None = None) -> None:
            nonlocal selected
            if sub.text() != new_meta:
                sub.setText(new_meta)
            if rt.text() != new_rate:
                rt.setText(new_rate)
            # The NAME is part of the update, not just the build (2026-09-26).
            # A card is keyed by hero address + class, and the first fill often
            # happens before the name read succeeds — the page opens on the
            # tick that attaches, and a hero whose player pointer is not
            # resolved yet falls back to "Hero". The card was then frozen with
            # that placeholder FOREVER, because nothing but the class could
            # ever change. It is a one-word re-trim here, not a rebuild.
            if new_name is not None and new_name != card._party_name:
                card._party_name = new_name            # type: ignore[attr-defined]
                nm.set_full(new_name)
            if sel != selected:
                selected = sel
                _style(sel)

        def _select(sel: bool) -> None:
            """Move this card's selection highlight, and nothing else.

            Split out of `_update` (2026-09-26) so a CLICK can move the
            highlight on its own. The only caller that styled the selection
            was the 2s fill, so clicking a card repainted the whole detail
            column instantly while the card you had just clicked stayed
            un-highlighted for up to two more seconds — the switch looked
            like it had not taken, and the accent sat on the WRONG hero while
            you read the new one's stats. A selection is the one thing a
            click must show at once.
            """
            nonlocal selected
            if sel != selected:
                selected = sel
                _style(sel)

        card._party_update = _update                   # type: ignore[attr-defined]
        card._party_select = _select                   # type: ignore[attr-defined]
        return card

    def _party_roster_width(self) -> None:
        """Widen the Players column by `_ROSTER_EXTRA_CHARS` name characters.

        Measured off a REAL card's name label, so the number of pixels is the
        font's own (2026-09-26) — the column is set from the base width every
        time rather than from its current width, so repeated fills cannot walk
        it sideways. Called after a fill, because that is the first moment the
        name font is resolved and can be measured; before that the build's own
        240px minimum stands.
        """
        wrap = getattr(self, "_party_roster_wrap", None)
        if wrap is None or not self._widget_alive(wrap):
            return
        body = getattr(self, "_party_roster_body", None)
        if body is None:
            return
        char_w = 0.0
        for i in range(body.count()):
            card = body.itemAt(i).widget()
            if card is None or not hasattr(card, "_party_update"):
                continue
            labels = card.findChildren(_ElideLabel)
            if labels:
                fm = labels[0].fontMetrics()
                char_w = fm.horizontalAdvance("abcdefghij") / 10.0
                break
        if char_w <= 0:
            return
        base = int(getattr(self, "_party_roster_base_w", 0) or 0) or 240
        want = base + round(_ROSTER_EXTRA_CHARS * char_w)
        wrap.setMinimumWidth(want)
        wrap.setMaximumWidth(max(want, getattr(wrap, "maximumWidth", lambda: 0)()))

    def _party_roster_select(self, addr: int) -> None:
        """Move the roster's selection highlight to `addr`, right now (2026-09-26).

        The click path calls this before the detail refresh, so the accent
        follows the click instead of the next 2s tick. It walks the cards that
        are already there and re-styles at most two of them (the one leaving,
        the one arriving) — the text is untouched, so nothing repaints that
        did not have to.
        """
        body = getattr(self, "_party_roster_body", None)
        if body is None:
            return
        for i in range(body.count()):
            card = body.itemAt(i).widget()
            pick = getattr(card, "_party_select", None)
            if pick is not None:
                pick(getattr(card, "_party_addr", 0) == addr)

    def _party_roster_fill(
            self, heroes: list[tuple[int, str, str]]) -> None:
        """The Overview roster cards (mockup block A), built once per hero and
        then UPDATED IN PLACE (2026-09-26).

        This used to tear the whole column down and rebuild it on every tick.
        The roster is polled on the same 2s timer as everything else and a
        hero's HP moves every tick, so the column was destroyed and re-laid-out
        twice a second — visible as the whole page flickering, and it threw
        away hover state and the roster's scroll position each time.

        So the cards are keyed by hero address: while the party is the SAME
        party, each card is told its new meta/rate text and re-styled if the
        selection moved, and nothing is created, destroyed or re-laid-out. A
        card is only rebuilt when its hero leaves, joins, or changes class.

        Meta (level/HP) comes from the live scene units; the session rate
        from the DPS tracker's current session, matched by hero name. Every
        lookup is best-effort — a card never fails because a number didn't
        resolve. Lives here (not party_page) so the page stays under the
        ui/ line budget — same split as the grid cards above."""
        body = getattr(self, "_party_roster_body", None)
        if body is None or not self._widget_alive(
                getattr(self, "_party_list", None)):
            return
        model = getattr(self, "model", None)
        me = getattr(model, "player_addr", None) if model else None
        units: dict[int, object] = {}
        try:
            for u in (model.units() if model else []) or []:
                a = getattr(u, "addr", 0)
                if a:
                    units[a] = u
        except Exception:
            units = {}
        sess = None
        try:
            tracker = getattr(model, "dps", None) if model else None
            sess = tracker.session if tracker else None
        except Exception:
            sess = None
        sel = getattr(self, "_party_selected_addr", 0) or 0
        # the cards already in the column, in order, keyed by hero address
        old: list[QtWidgets.QFrame] = []
        for i in range(body.count()):
            w = body.itemAt(i).widget()
            if w is not None and hasattr(w, "_party_update"):
                old.append(w)
        old_by_addr = {getattr(w, "_party_addr", None): w for w in old}

        cards: list[QtWidgets.QFrame] = []
        added = 0
        for addr, name, cls in heroes:
            disp = name[:-6] if name.endswith(" (You)") else name
            is_me = bool(me and addr == me) or "(You)" in name
            lvl = hp = None
            u = units.get(addr)
            if u is not None:
                lvl = getattr(u, "level", 0) or None
                hp = getattr(u, "hp", 0.0) or None
            meta = "  ·  ".join(p for p in (
                cls,
                f"Lv {lvl}" if lvl else "",
                f"HP {hp:,.0f}" if hp else "",
            ) if p) or "Hero"
            rate = ""
            try:
                if sess is not None and sess.duration > 0:
                    for key in (disp, name):
                        p = (sess.players or {}).get(key)
                        if p is not None:
                            rate = f"{p.dps(sess.duration):,.0f}/s"
                            break
            except Exception:
                rate = ""
            card = old_by_addr.get(addr)
            if card is not None and getattr(card, "_party_cls", None) == cls:
                card._party_update(meta, rate, addr == sel, disp)
            else:
                card = self._party_roster_card(
                    addr, disp, cls, meta, rate, is_me, addr == sel)
                card._party_cls = cls                  # type: ignore[attr-defined]
                body.insertWidget(added, card)
                added += 1
            cards.append(card)
        # drop the cards of heroes who left, clear any spacer a previous fill
        # left behind, then re-assert exactly ONE trailing stretch
        keep = {id(c) for c in cards}
        for i in range(body.count() - 1, -1, -1):
            it = body.itemAt(i)
            w = it.widget()
            if w is None:                    # a spacer, not a hero card
                body.takeAt(i)
            elif id(w) not in keep:
                body.takeAt(i)
                w.setParent(None)
                w.deleteLater()
        body.addStretch(1)
        # the column grows by a measured number of name characters, now that
        # there is a real name label to measure (see _party_roster_width)
        self._party_roster_width()
        # The Players block hugs what it just drew: the new cards' own heights
        # plus the caption, capped so a 20-hero party scrolls INSIDE the block
        # instead of stretching the page (2026-09-26). Without the cap the
        # block was a tall framed box with a pool of empty card under a small
        # party; without the hug a long party grew the page. The heights are
        # summed from the cards just built — the roster host sits in a
        # widgetResizable scroller, which sizes the host to the viewport and
        # so never reports the content height itself.
        block = getattr(self, "_party_players_card", None)
        if block is not None and self._widget_alive(block):
            want = (sum(c.sizeHint().height() for c in cards)
                    + body.spacing() * max(0, len(cards) - 1)
                    + 36)                      # the caption + the card's padding
            # FIXED, not a maximum: the block is in the column at stretch 0,
            # so a maximum would only ever be its (tiny) sizeHint and the
            # roster would scroll inside 54px of viewport
            block.setFixedHeight(min(want, _ROSTER_MAX_H))
