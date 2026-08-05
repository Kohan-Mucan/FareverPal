"""Drop Table — its own closable window for the selected target's loot."""
from __future__ import annotations

from collections import defaultdict

from PySide6 import QtCore, QtWidgets

from .. import theme
from ..overlay_base import OverlayWindow
from ..widgets import fmt_pct
from ...data import names, tokens
from .entity_rows import RowSpec, _Section, _scroll_body

_RARITY_ORDER = {"Legendary": 0, "Epic": 1, "Rare": 2, "Uncommon": 3, "Common": 4}


class DropTableOverlay(OverlayWindow):
    def __init__(self, model, settings, parent=None):
        super().__init__("DROP TABLE", settings, geo_key="droptable", parent=parent)
        self._page_key, self.model, self.s = "entity", model, settings
        self._collapsed, self._near, self._name = set(), None, ""
        scroll, self._body = _scroll_body()
        self.box = _Section("DROPS", self.s.hud_accent or theme.ACCENT)
        self._body.addWidget(self.box)
        self._body.addStretch(1)
        self.content.addWidget(scroll, 1)
        self._base_w, self._base_h = 392, 480
        self.setMinimumWidth(360)
        self.resize(392, 480)

    def set_target(self, near, name: str):
        self._near, self._name = near, name or "?"
        self._render()

    def _retint(self, accent: str) -> None:
        if accent:
            self.box.header.set_color(accent)

    def _toggle(self, rarity):
        self._collapsed.symmetric_difference_update({rarity})
        self._render()

    def _active_class(self):
        pc = self.s.player_class
        return None if pc in ("Off", "Auto") else pc

    def _render(self):
        near = self._near
        self.titlebar.title.setText(self._name)
        self.box.header.set_text(self._name)
        if near is None:
            self.box.header.set_tag("NO TABLE")
            self.box.fill([], self.s.icon_size)
            return
        self.box.header.set_tag(f"{near.dist:.0f}m · L{near.level}")
        active, drops = self._active_class(), self.model.drop_table_effective(near)
        groups = defaultdict(list)
        for item, prob, rar, _typ in drops:
            groups[rar or "Common"].append((item, prob))
        specs: list[RowSpec] = []
        first_item = True
        for rarity in sorted(groups, key=lambda r: _RARITY_ORDER.get(r, 9)):
            items = groups[rarity]
            total = sum(p for _, p in items)
            col = theme.rarity_color(rarity)
            collapsed = rarity in self._collapsed
            caret = "▸" if collapsed else "▾"
            specs.append(RowSpec(None, None, col, f"{caret} {rarity.upper()}", col,
                                 value=f"Σ {fmt_pct(total)} · {len(items)}", bold=True,
                                 cb=(lambda r=rarity: self._toggle(r))))
            if collapsed:
                continue
            ordered = sorted(items, key=lambda t: -t[1])
            for item, prob in ordered:
                if tokens.is_token(item):
                    specs.append(RowSpec("Items", item, col, f"🎲 {names.item_name(item)}", theme.MUTED,
                                         sub="PROCEDURAL", value=fmt_pct(prob)))
                else:
                    specs.append(RowSpec("Items", item, col, names.item_name(item), col,
                                         sub=(rarity.upper() if first_item else ""), value=fmt_pct(prob), highlight=first_item))
                first_item = False
        self.box.fill(specs, round(self.s.icon_size * self._scale))
