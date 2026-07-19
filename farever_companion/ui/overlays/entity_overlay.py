"""Entity overlay + a separate Drop Table window (Tactical Overlay HUD).

The entity HUD lists nearby enemies (selectable with ↑/↓ or click) and chests -
short, no endless scrolling. Selecting an enemy opens a separate, closable
**Drop Table** window showing that target's full predicted loot (rarity-grouped,
collapsible; class-usable items starred). If the selected target leaves range we
flip to the first available one. Clicking an enemy, wild companion or secret
orb also points the centre-screen compass needle at it (ui/tracker.py); click
the tracked row again to stop. Reads the shared LiveModel. Procedural
generators shown honestly as "🎲 random X". Read-only.
"""
from __future__ import annotations

import math
import re
import traceback
from collections import defaultdict
from dataclasses import dataclass

from PySide6 import QtCore, QtGui, QtWidgets
from .. import theme
from ..overlay_base import OverlayWindow
from ..components import SectionHeader, IconTile
from ..widgets import fmt_pct
from ...data import names, tokens, icons, dungeons, units as udata
from ...geo import orbs as geo_orbs, zones as geo_zones

POLL_MS = 350
_RARITY_ORDER = {"Legendary": 0, "Epic": 1, "Rare": 2, "Uncommon": 3, "Common": 4}


@dataclass
class RowSpec:
    sheet: str | None
    id_: str | None
    accent: str | None
    name: str
    name_color: str
    sub: str = ""
    sub_color: str | None = None
    value: str = ""
    bold: bool = False
    highlight: bool = False
    cb: object = None
    right_cb: object = None
    marker: str = ""      # map-marker icon name instead of a game-sheet icon
    ui_icon: str = ""     # UI SVG icon name instead of a game-sheet icon
    outlined: bool = False   # use outlined style (minimap style) instead of tile
    outline_border: int = 2
    border_color: str | None = None  # draw a CSS border of this color around the icon
    extra_icon: object = None  # tuple: (sheet, id, marker, outlined)
    key: object = None         # (kind, key) for selection mapping
    font_size: int | None = None
    size_override: int | None = None


class _EntityRow(QtWidgets.QFrame):
    """IconTile + name (+ optional mono sub-label) + right value. Pooled."""
    clicked = QtCore.Signal()
    rightClicked = QtCore.Signal()

    def __init__(self, icon_size: int):
        super().__init__()
        self._highlight, self._cb, self._right_cb = "", None, None
        lay = QtWidgets.QHBoxLayout(self); lay.setContentsMargins(6, 2, 6, 2); lay.setSpacing(9)
        self.icon_box = QtWidgets.QFrame(); self.icon_box.setObjectName("IconBox")
        box_lay = QtWidgets.QHBoxLayout(self.icon_box); box_lay.setContentsMargins(0, 0, 0, 0); box_lay.setSpacing(0)
        self.tile = IconTile(icon_size)
        self.tile2 = IconTile(icon_size)
        self.tile2.hide()
        mid = QtWidgets.QVBoxLayout()
        mid.setContentsMargins(0, 0, 0, 0); mid.setSpacing(0)
        self.name, self.sub = QtWidgets.QLabel(), QtWidgets.QLabel()
        self.name.setWordWrap(True)
        self.name.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
        self.sub.setWordWrap(True)
        self.sub.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
        self.sub.setObjectName("Mono")
        mid.addWidget(self.name); mid.addWidget(self.sub)
        box_lay.addWidget(self.tile)
        box_lay.addWidget(self.tile2)
        self.value = QtWidgets.QLabel(); self.value.setObjectName("Mono")
        self.value.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter); self.value.setMinimumWidth(70)
        lay.addWidget(self.icon_box); lay.addLayout(mid, 1); lay.addWidget(self.value, 0)

    def apply(self, spec: RowSpec, icon_size: int):
        self._key = spec.key
        isz = spec.size_override if spec.size_override else icon_size
        icons_to_draw = [(self.tile, spec.sheet, spec.id_, spec.marker, spec.ui_icon, spec.outlined, spec.accent, spec.outline_border)]
        if spec.extra_icon:
            ex_sheet, ex_id, ex_marker, ex_outlined = spec.extra_icon[:4]
            ex_acc = spec.extra_icon[4] if len(spec.extra_icon) > 4 else spec.accent
            ex_border = spec.extra_icon[5] if len(spec.extra_icon) > 5 else 2
            icons_to_draw.append((self.tile2, ex_sheet, ex_id, ex_marker, None, ex_outlined, ex_acc, ex_border))
            
        for i, (t, s, id_, m, u, o, a, b) in enumerate(icons_to_draw):
            t.set_size(isz); t.show()
            if u: t.set_ui_icon(u, a or theme.ACCENT)
            elif m: t.set_marker(m, a, o)
            elif o: t.set_outlined(s, id_, a or theme.ACCENT, border=b)
            else: t.set(s, id_, a or theme.ACCENT)
        if not spec.extra_icon: self.tile2.hide()

        weight = "700" if spec.bold else "500"
        fs = f"font-size:{spec.font_size}px;" if spec.font_size else ""
        self.name.setText(spec.name)
        self.name.setStyleSheet(f"color:{spec.name_color};font-weight:{weight};{fs}background:transparent;")
        self.sub.setText(spec.sub); self.sub.setVisible(bool(spec.sub))
        if spec.sub:
            scol = spec.sub_color or theme.MUTED
            self.sub.setStyleSheet(f"color:{scol};{fs}background:transparent;")
        self.value.setText(spec.value); self.value.setStyleSheet(f"color:{spec.name_color};{fs}background:transparent;")
        self._highlight = spec.accent if spec.highlight else ""
        # Colored border via stylesheet — reliable, survives child-widget repaint
        bcol = getattr(spec, "border_color", None)
        if bcol != getattr(self, "_border_color", "__RESET__"):
            self._border_color = bcol
            if bcol:
                self.icon_box.setStyleSheet(f"QFrame#IconBox {{ border: 1px solid {bcol}; }}")
                self.icon_box.layout().setContentsMargins(2, 2, 2, 2)
            else:
                self.icon_box.setStyleSheet("QFrame#IconBox { border: none; }")
                self.icon_box.layout().setContentsMargins(0, 0, 0, 0)
        self.set_callback(spec.cb, spec.right_cb); self.update()

    def set_callback(self, cb, right_cb=None):
        if self._cb is not None: self.clicked.disconnect(self._cb); self._cb = None
        if cb is not None: self._cb = cb; self.clicked.connect(cb)
        
        if self._right_cb is not None: self.rightClicked.disconnect(self._right_cb); self._right_cb = None
        if right_cb is not None: self._right_cb = right_cb; self.rightClicked.connect(right_cb)
        
        self.setCursor(QtCore.Qt.PointingHandCursor if (cb or right_cb) else QtCore.Qt.ArrowCursor)

    def paintEvent(self, e):
        super().paintEvent(e)          # draw QSS background/frame first
        if self._highlight:
            p = QtGui.QPainter(self); p.setRenderHint(QtGui.QPainter.Antialiasing, False)
            c = QtGui.QColor(self._highlight); bg = QtGui.QColor(c); bg.setAlpha(30)
            p.fillRect(self.rect(), bg); p.fillRect(0, 0, 2, self.height(), c); p.end()

    def mousePressEvent(self, e):
        if e.button() == QtCore.Qt.LeftButton: self.clicked.emit()
        elif e.button() == QtCore.Qt.RightButton: self.rightClicked.emit()
        super().mousePressEvent(e)


class _Section(QtWidgets.QWidget):
    def __init__(self, title, color):
        super().__init__()
        lay = QtWidgets.QVBoxLayout(self); lay.setContentsMargins(0, 5, 0, 5); lay.setSpacing(2)
        self.header = SectionHeader(title, color, colored_label=True); lay.addWidget(self.header)
        self.rows = QtWidgets.QVBoxLayout(); self.rows.setSpacing(1); lay.addLayout(self.rows)
        self._pool: list[_EntityRow] = []

    def fill(self, specs: list[RowSpec], icon_size):
        while len(self._pool) < len(specs):
            r = _EntityRow(icon_size); self._pool.append(r); self.rows.addWidget(r)
        for i, spec in enumerate(specs):
            r = self._pool[i]; r.apply(spec, icon_size); r.show()
        for j in range(len(specs), len(self._pool)): self._pool[j].hide()


def _scroll_body():
    scroll = QtWidgets.QScrollArea()
    scroll.setWidgetResizable(True); scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
    scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
    scroll.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
    body = QtWidgets.QWidget(); lay = QtWidgets.QVBoxLayout(body)
    lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(0); scroll.setWidget(body)
    return scroll, lay


# ---------------------------------------------------------------------------
#  Drop Table - its own closable window for the selected target's loot
# ---------------------------------------------------------------------------
class DropTableOverlay(OverlayWindow):
    def __init__(self, model, settings, parent=None):
        super().__init__("DROP TABLE", settings, geo_key="droptable", parent=parent)
        self._page_key, self.model, self.s = "entity", model, settings
        self._collapsed, self._near, self._name = set(), None, ""
        scroll, self._body = _scroll_body(); self.box = _Section("DROPS", self.s.hud_accent or theme.ACCENT)
        self._body.addWidget(self.box); self._body.addStretch(1); self.content.addWidget(scroll, 1)
        self._base_w, self._base_h = 392, 480; self.setMinimumWidth(360); self.resize(392, 480)

    def set_target(self, near, name: str):
        self._near, self._name = near, name or "?"; self._render()

    def _retint(self, accent: str) -> None:
        if accent: self.box.header.set_color(accent)

    def _toggle(self, rarity):
        self._collapsed.symmetric_difference_update({rarity}); self._render()

    def _active_class(self):
        pc = self.s.player_class
        return None if pc in ("Off", "Auto") else pc

    def _render(self):
        near = self._near
        self.titlebar.title.setText(self._name)
        self.box.header.set_text(self._name)
        if near is None:
            self.box.header.set_tag("NO TABLE"); self.box.fill([], self.s.icon_size); return
        self.box.header.set_tag(f"{near.dist:.0f}m · L{near.level}")
        active, drops = self._active_class(), self.model.drop_table_effective(near)
        groups = defaultdict(list)
        for item, prob, rar, _typ in drops: groups[rar or "Common"].append((item, prob))
        specs: list[RowSpec] = []; first_item = True
        for rarity in sorted(groups, key=lambda r: _RARITY_ORDER.get(r, 9)):
            items = groups[rarity]
            total = sum(p for _, p in items)
            col = theme.rarity_color(rarity); collapsed = rarity in self._collapsed
            caret = "▸" if collapsed else "▾"
            specs.append(RowSpec(None, None, col, f"{caret} {rarity.upper()}", col,
                                 value=f"Σ {fmt_pct(total)} · {len(items)}", bold=True,
                                 cb=(lambda r=rarity: self._toggle(r))))
            if collapsed: continue
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


# ---------------------------------------------------------------------------
#  Entity HUD - nearby enemies (selectable) + chests
# ---------------------------------------------------------------------------
class EntityOverlay(OverlayWindow):
    def __init__(self, model, settings, parent=None):
        super().__init__("Entity", settings, geo_key="entity", parent=parent)
        self._page_key = "entity"
        self.titlebar.codex_btn.setVisible(True)
        self.model = model
        self.s = settings
        self._enemies: list = []          # [(entity, dist)]
        self._group_members: list = []    # [(entity, dist)]
        self._comps: list = []            # [(entity, dist)] wild companions
        self._gatherables: list = []      # [((x,y,z,kind,label,id), dist)]
        self._orbs: list = []             # [(Orb, dist)] uncollected secret orbs
        self._chests: list = []           # [ChestRow]
        self._static_pois: list = []      # [((x,y,z,kind,label,id), dist)] tracked dungeons/obelisks
        self._owned: set | None = None    # account collection (None = signed out)
        self._sel = None                  # ("enemy", addr) | ("chest", chest_id)
        self._tracker = None              # shared compass-needle TrackController
        self._last_was_in_dungeon = False # track zone transitions to clear tracker
        self._drop_win: DropTableOverlay | None = None
        self.setFocusPolicy(QtCore.Qt.StrongFocus)

        scroll, self._body = _scroll_body()
        self._scroll = scroll
        self.content.addWidget(scroll, 1)
        self.rift_box = _Section("", theme.GOLD)
        self.rift_box.header.hide()
        self.enemy_box = _Section("ENEMIES", theme.DANGER)
        self.comp_box = _Section("COMPANIONS", theme.GOOD)
        self.group_box = _Section("PLAYERS", theme.MUTED)
        self.gather_box = _Section("GATHERABLES", "#c2c6d0")
        self.orb_box = _Section("SECRET ORBS", theme.KIND_COLOR["orb"])
        self.chest_box = _Section("CHESTS", theme.CHEST)
        self.static_box = _Section("WAYPOINT", theme.ACCENT)
        for b in (self.rift_box, self.static_box, self.enemy_box, self.comp_box, self.group_box, self.gather_box, self.orb_box, self.chest_box):
            self._body.addWidget(b)
        self._body.addStretch(1)

        self.enable_resize_grip()
        self._base_w, self._base_h = self.s.entity_width, 440
        self.setMinimumWidth(300)
        self.setMaximumWidth(800)
        self.apply_scale(self.s.entity_font_size / 14.0)     # scaled QSS + resize to base*scale
        self._last_h_config = None
        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(POLL_MS)

    # --- lock + opacity forward to the drop child -----------------------
    def set_locked(self, on: bool) -> None:
        super().set_locked(on)
        if self._drop_win is not None:
            self._drop_win.set_locked(on)

    def set_opacity(self, value: float) -> None:
        super().set_opacity(value)
        if self._drop_win is not None:
            self._drop_win.set_opacity(value)

    def reset_state(self) -> None:
        """Completely reset all HUD selection, entity caches, and sub-windows on relog/logout."""
        self._sel = None
        self._enemies = []
        self._group_members = []
        self._comps = []
        self._gatherables = []
        self._orbs = []
        self._chests = []
        self._static_pois = []
        if self._drop_win is not None:
            self._drop_win.hide()

    def hideEvent(self, e):
        super().hideEvent(e)
        if self._drop_win is not None:
            self._drop_win_was_visible = self._drop_win.isVisible()
            self._drop_win.hide()
        
    def showEvent(self, e):
        super().showEvent(e)
        if self._drop_win is not None and getattr(self, "_drop_win_was_visible", False):
            self._drop_win.show()

    # --- selection -------------------------------------------------------
    def keyPressEvent(self, e):
        if e.key() == QtCore.Qt.Key_Up:
            self._move_selection(-1)
        elif e.key() == QtCore.Qt.Key_Down:
            self._move_selection(1)
        else:
            super().keyPressEvent(e)

    def _get_rift_mode(self) -> str:
        val = getattr(self.s, "show_rift_timer", "Always")
        if isinstance(val, bool):
            return "Always" if val else "Off"
        s = str(val).lower()
        if s in ("active",): return "Active"
        if s in ("off", "false"): return "Off"
        return "Always"

    def _targets(self):
        """Ordered selectable keys across every visible section: enemies,
        companions, orbs, chests (matching the HUD layout)."""
        seen_enemy_names = set()
        t = []
        for e, _ in self._enemies:
            name = names.unit_name(e.unit_id) or "?"
            if name not in seen_enemy_names:
                seen_enemy_names.add(name)
                t.append(("enemy", e.unit_id))

        t += [("comp", getattr(e, "addr", None)) for e, _ in self._comps
              if getattr(e, "addr", None) is not None]
        t += [("hero", getattr(e, "addr", None)) for e, _ in self._group_members
              if getattr(e, "addr", None) is not None]
        
        seen_gather_types = set()
        for g, _ in self._gatherables:
            from ...geo import gatherables as geo_gatherables
            base = geo_gatherables.get_display_name(g[4])
            if base not in seen_gather_types:
                seen_gather_types.add(base)
                t.append(("gather", base))

        t += [("orb", o.orb_id) for o, _ in self._orbs]
        t += [("recipe" if ("recipe" in c.chest_id.lower() or (c.loot_table and "recipe" in (c.loot_table or "").lower())) else "chest", c.chest_id) for c in self._chests]
        t += [("static", p[5]) for p, _ in self._static_pois]
        rift_mode = self._get_rift_mode()

        rst = self.model.rift_status() if rift_mode != "Off" else None
        if rst and rst.state in ("WARNING", "ACTIVE", "CLOSING", "SCHEDULED"):
            if rift_mode == "Always" or (rift_mode == "Active" and rst.state in ("WARNING", "ACTIVE", "CLOSING")):
                r_key = f"s{rst.poi_x:.1f},{rst.poi_y:.1f}" if rst.poi_x is not None else "rift_schedule"
                t.append(("rift_active", r_key))

        return t

    def _selected_source(self):
        """(near, name) for the current selection, or (None, ''). Companions
        and orbs aren't loot sources - they drive the compass instead."""
        if self._sel is None:
            return None, ""
        kind, key = self._sel
        if kind in ("comp", "orb", "hero", "gather", "static"):
            return None, ""
        if kind == "enemy":
            for e, d in self._enemies:
                if e.unit_id == key or getattr(e, "addr", None) == key:
                    return (self.model.enemy_drop_source(e, d, self.s.level),
                            names.unit_name(e.unit_id) or "?")
        else:
            # chest or recipe
            for c in self._chests:
                if c.chest_id == key:
                    name = names.chest_label(c.chest_id, c.loot_table)
                    return self.model.chest_drop_source(c, self.s.level), name
        return None, ""

    def _move_selection(self, delta):
        targets = self._targets()
        if not targets:
            return
        try:
            i = targets.index(self._sel)
        except ValueError:
            i = 0
        i = max(0, min(len(targets) - 1, i + delta))
        self._sel = targets[i]
        kind, key = self._sel

        # Ensure the compass follows the selection for all trackable types
        if kind == "enemy":
            self._track("unit", key, force=True)
            self._open_drops()
        elif kind in ("chest", "recipe"):
            c = next((c for c in self._chests if c.chest_id == key), None)
            if c:
                cx, cy, cz = c.x, c.y, c.z
                label = names.chest_label(c.chest_id, c.loot_table)
                self._track(kind, f"{cx:.1f},{cy:.1f},{cz:.1f}|{label}|{c.chest_id}", force=True)
            self._open_drops()
        elif kind == "comp":
            e = next((e for e, _ in self._comps if getattr(e, "addr", None) == key), None)
            if e: self._track("unit", e.unit_id, addr=key, force=True)
        elif kind == "hero":
            e = next((e for e, _ in self._group_members if getattr(e, "addr", None) == key), None)
            if e: self._track("hero", e.unit_id, addr=key, force=True)
        elif kind == "orb":
            self._track("orb", key, force=True)
        elif kind == "static":
            p_data = next((p[0] for p in self._static_pois if p[0][5] == key), None)
            if p_data:
                self._track(p_data[3], f"{p_data[0]:.1f},{p_data[1]:.1f},{p_data[2]:.1f}|{p_data[4]}|{key}", force=True)
        elif kind == "gather":
            # key is the display name (e.g. "Madrigold")
            self._track("gather", key, force=True)

        self._refresh()
        self._pan_minimap_to_sel()
        self._scroll_to_selected()

    def _scroll_to_selected(self):
        if self._sel is None: return
        # Give UI a moment to layout
        QtCore.QTimer.singleShot(10, self._do_scroll)

    def _do_scroll(self):
        if self._sel is None: return
        for box in (self.static_box, self.enemy_box, self.comp_box, self.group_box, self.gather_box, self.orb_box, self.chest_box):
            for row in box._pool:
                if row.isVisible() and getattr(row, "_key", None) == self._sel:
                    self._scroll.ensureWidgetVisible(row)
                    return

    def _select(self, kind, key):
        # Determine if this is a "deselect" action.
        # We consider it a "re-click" if it's already the HUD selection OR if it's already the tracked target
        tk = "unit" if kind == "enemy" else kind
        is_tracked = self._tracker and self._tracker.is_tracked(tk, key)
        is_reclick = (self._sel == (kind, key)) or (kind == "enemy" and self._sel and self._sel[0] == "enemy" and self._sel[1] == key)
        
        # If we re-click the same item or click something that's already tracked, deselect and clear.
        if is_reclick or is_tracked:
            self._sel = None
            self.close_drops()
            if self._tracker is not None:
                # Only clear if it matches the item we are unselecting (handle background tracking)
                if not self.s.track_id or self._tracker.is_tracked(tk, key):
                    self._tracker.clear()
            
            mgr = self._tracker.parent() if self._tracker else None
            if mgr:
                map_ov = getattr(mgr, "overlays", {}).get("map")
                if map_ov and hasattr(map_ov, "canvas"):
                    map_ov.canvas._pan_x = map_ov.canvas._pan_y = 0.0
                    map_ov.canvas.update()
            self._refresh()
            return

        self._sel = (kind, key)
        if self._tracker is not None:
            if kind == "enemy":
                self._tracker.track("unit", key)
            elif kind == "hero":
                e = next((e for e, _ in self._group_members if getattr(e, "addr", None) == key), None)
                if e: self._tracker.track("hero", e.unit_id, addr=key)
            elif kind == "comp":
                e = next((e for e, _ in self._comps if getattr(e, "addr", None) == key), None)
                if e: self._tracker.track("unit", e.unit_id, addr=key)
            elif kind == "orb":
                self._tracker.track("orb", key)
            elif kind == "static":
                p_data = next((p[0] for p in self._static_pois if p[0][5] == key), None)
                if p_data:
                    self._tracker.track(p_data[3], f"{p_data[0]:.1f},{p_data[1]:.1f},{p_data[2]:.1f}|{p_data[4]}|{key}")
            elif kind == "rift_active":
                rst = self.model.rift_status() if self.model else None
                px, py, pz, label = None, None, None, "Rift"
                if rst and rst.poi_x is not None and rst.poi_y is not None:
                    px, py, pz = rst.poi_x, rst.poi_y, rst.poi_z
                    label = getattr(rst, "subzone", None) or rst.rift_name or "Rift"
                elif isinstance(key, str) and key.startswith("s"):
                    try:
                        parts = key[1:].split(",")
                        px, py, pz = float(parts[0]), float(parts[1]), 0.0
                    except Exception:
                        pass
                if px is not None and py is not None:
                    self._tracker.track("rift", f"{px:.1f},{py:.1f},{pz or 0.0:.1f}|{label}|{key}")
            elif kind == "gather":
                # key is now a display name for type-tracking (e.g. "Madrigold")
                self._tracker.track("gather", key)
            elif kind in ("chest", "recipe"):
                c = next((c for c in self._chests if c.chest_id == key), None)
                if c:
                    cx, cy, cz = c.x, c.y, c.z
                    label = names.chest_label(c.chest_id, c.loot_table)
                    self._tracker.track(kind, f"{cx:.1f},{cy:.1f},{cz:.1f}|{label}|{c.chest_id}")

        if kind in ("enemy", "chest"):
            self._open_drops()
        
        # Pan minimap to the selected entity
        self._pan_minimap_to_sel()
        self._refresh()
        self._scroll_to_selected()

    def _pan_minimap_to_sel(self):
        if self._sel is None or self._tracker is None: return
        kind, key = self._sel
        mgr = self._tracker.parent()
        if not mgr: return
        map_ov = getattr(mgr, "overlays", {}).get("map")
        if not map_ov or not map_ov.isVisible(): return

        # Find coordinates for the selected item
        wx, wy = None, None
        if kind == "enemy":
            ent = next((e for e, _ in self._enemies if getattr(e, "unit_id", None) == key or getattr(e, "addr", None) == key), None)
            if ent: wx, wy = ent.x, ent.y
        elif kind == "hero":
            ent = next((e for e, _ in self._group_members if getattr(e, "addr", None) == key or getattr(e, "unit_id", None) == key), None)
            if ent: wx, wy = ent.x, ent.y
        elif kind == "comp":
            ent = next((e for e, _ in self._comps if getattr(e, "addr", None) == key or getattr(e, "unit_id", None) == key), None)
            if ent: wx, wy = ent.x, ent.y
        elif kind == "orb":
            o = next((orb for orb, _dist in self._orbs if orb.orb_id == key), None)
            if not o: o = geo_orbs.by_id().get(key)
            if o: wx, wy = o.x, o.y
        elif kind == "static":
            p_data = next((p[0] for p in self._static_pois if p[0][5] == key), None)
            if p_data: wx, wy = p_data[0], p_data[1]
        elif kind == "rift_active":
            rst = self.model.rift_status() if self.model else None
            if rst and rst.poi_x is not None and rst.poi_y is not None:
                wx, wy = rst.poi_x, rst.poi_y
            elif isinstance(key, str) and key.startswith("s"):
                try:
                    parts = key[1:].split(",")
                    wx, wy = float(parts[0]), float(parts[1])
                except Exception:
                    pass
        elif kind == "gather":
            from ...geo import gatherables as geo_gatherables
            # key is type name, find nearest instance currently in self._gatherables
            match = None
            for g_data, d in self._gatherables:
                if geo_gatherables.get_display_name(g_data[4]) == key:
                    if match is None or d < match[1]:
                        match = (g_data, d)
            if match:
                wx, wy = match[0][0], match[0][1]
        elif kind in ("chest", "recipe"):
            c = next((ch for ch in self._chests if ch.chest_id == key), None)
            if c: wx, wy = c.x, c.y

        if wx is not None and wy is not None:
            map_ov.pan_to(wx, wy)

    def select_by_key(self, kind, key, open_drops=True) -> bool:
        """Select an item from the HUD list by kind and key (from external clicks)."""
        # Resolve gatherable IDs to type names for grouped selection
        if kind == "gather" and isinstance(key, str) and (key.startswith("gl") or key.startswith("g")):
            g_data = next((g[0] for g in self._gatherables if g[0][5] == key), None)
            if g_data:
                from ...geo import gatherables as geo_gatherables
                key = geo_gatherables.get_display_name(g_data[4])

        targets = self._targets()
        match = None
        for t_kind, t_key in targets:
            if t_kind == kind:
                if isinstance(t_key, int) and str(t_key) == str(key):
                    match = (t_kind, t_key)
                    break
                elif str(t_key).lower() == str(key).lower():
                    match = (t_kind, t_key)
                    break
        
        if not match:
            # If not in the current visible list, force it as the selection.
            # The next _refresh will ensure it's added to the list and properly rendered.
            try:
                self._sel = (kind, int(key) if isinstance(key, (int, str)) and str(key).isdigit() else key)
                if open_drops and kind in ("enemy", "chest"):
                    self._open_drops()
                self._refresh()
                self._scroll_to_selected()
                return True
            except Exception:
                return False

        if match:
            if not open_drops:
                old_show = self.s.show_drop_window
                self.s.show_drop_window = False
                try:
                    self._select(*match)
                finally:
                    self.s.show_drop_window = old_show
            else:
                self._select(*match)
            self._scroll_to_selected()
            return True
        return False

    # public actions for global hotkeys (selection works while the game is focused)
    def select_prev(self):
        self._move_selection(-1)

    def select_next(self):
        self._move_selection(1)

    def close_drops(self):
        if self._drop_win is not None:
            self._drop_win.close()
            self._drop_win = None

    def _retint(self, accent: str) -> None:
        # selected-row / icon-tile accents are read live each refresh; just keep
        # the open drop-table sub-window in sync.
        if self._drop_win is not None:
            self._drop_win.apply_accent(accent)

    def set_scale(self, scale):
        self.apply_scale(scale)
        if self._drop_win is not None:
            self._drop_win.apply_scale(scale)

    def set_collection_owned(self, owned: set | None) -> None:
        self._owned = owned

    def set_tracker(self, tracker) -> None:
        self._tracker = tracker
        tracker.changed.connect(self._tick)

    def _track(self, kind: str, key: str | None, addr=None, force=False) -> None:
        if self._tracker is not None and key:
            if force:
                self._tracker.track(kind, key, addr=addr)
            else:
                self._tracker.toggle(kind, key, addr=addr)

    def _is_tracked(self, kind: str, key: str | None) -> bool:
        return self._tracker is not None and key is not None \
            and self._tracker.is_tracked(kind, key)

    def _is_tracked_instance(self, kind: str, unit_id: str | None, addr) -> bool:
        """Only the live instance the needle is locked on shows TRACKING -
        not every unit of the tracked type."""
        if not self._is_tracked(kind, unit_id):
            return False
        locked = self._tracker.locked_addr
        return locked is None or locked == addr

    def _hide_unit(self, unit_id: str):
        if self._tracker and self.s.track_kind == "unit":
            tid = self.s.track_id
            if tid == unit_id:
                self._tracker.clear()
        panel = getattr(self, "_main_win", None)
        if panel and hasattr(panel, "_set_unit_hidden"):
            panel._set_unit_hidden(unit_id, True)
        else:
            profile = self.model.player_profile()
            self.s.toggle_unit_hidden(unit_id, True, profile)
        self._refresh()

    def _hide_companion(self, unit_id: str):
        if self._tracker and self.s.track_kind == "unit":
            tid = self.s.track_id
            if tid == unit_id:
                self._tracker.clear()
        panel = getattr(self, "_main_win", None)
        if panel and hasattr(panel, "_set_companion_hidden"):
            panel._set_companion_hidden(unit_id, True)
        else:
            profile = self.model.player_profile()
            self.s.toggle_companion_hidden(unit_id, True, profile)
        self._refresh()

    def _open_drops(self):
        if not self.s.show_drop_window:
            return
        if self._sel is None:
            return
        if self._drop_win is None or not self._drop_win.isVisible():
            self._drop_win = DropTableOverlay(self.model, self.s)
            self._drop_win.set_locked(self._locked)
            self._drop_win.set_opacity(self.s.opacity)
            self._drop_win.apply_scale(self._scale)
            # Only move to the default side-car position if there's no saved geometry
            if not self.s.geometry.get("droptable"):
                self._drop_win.move(self.x() + self.width() + 8, self.y())
            self._drop_win.show()
        self._drop_win.set_target(*self._selected_source())

    def _update_drops(self):
        if not self.s.show_drop_window:
            self.close_drops()
            return
        if self._drop_win is not None and self._drop_win.isVisible():
            if self._sel is None:
                self.close_drops()
                return
            kind, key = self._sel
            if kind not in ("enemy", "chest", "recipe"):
                self.close_drops()
                return
            self._drop_win.set_target(*self._selected_source())

    # --- helpers ---------------------------------------------------------
    def _ranked_orbs(self, xyz, profile, player_zone=None, max_dist=0.0):
        """Nearest orbs, plain distance order."""
        hide_coll = getattr(self.s, "entity_hide_collected", True)
        pool = geo_orbs.load_orbs()
        if hide_coll:
            pool = [o for o in pool if not self.s.is_done(o.orb_id, profile)]
        
        cands = []
        for o in pool:
            if player_zone:
                from ...geo import zones as geo_zones
                ozone = geo_zones.get_area_id(o.zone)
                if ozone != player_zone:
                    continue
            d = o.dist2d(xyz[0], xyz[1])
            if max_dist > 0 and d > max_dist:
                continue
            cands.append((o, d))
        return sorted(cands, key=lambda t: t[1])[:self.s.orb_count]

    def _tick(self):
        try:
            self._refresh()
        except Exception as e:
            import sys, traceback
            print(f"[EntityOverlay Error] {e}", file=sys.stderr, flush=True)
            traceback.print_exc(file=sys.stderr)
            sys.stderr.flush()

    def _refresh(self):
        xyz = self.model.player_xyz()
        if xyz is None:
            return

        # Clear all lists while inside a dungeon/rift — overworld enemies, chests
        # and orbs are irrelevant there and stale data would be confusing.
        try:
            in_dg = self.model.is_in_dungeon() or self.model.is_in_rift()
            
            # If we just entered or just left a dungeon, clear ALL tracking
            if in_dg != self._last_was_in_dungeon:
                self._last_was_in_dungeon = in_dg # Update first to prevent recursion
                if self._tracker is not None:
                    self._tracker.blockSignals(True)
                    try:
                        self._tracker.clear()
                    finally:
                        self._tracker.blockSignals(False)
                self._sel = None

            if in_dg:
                self._enemies = []
                self._group_members = []
                self._comps = []
                self._orbs = []
                self._gatherables = []
                self._chests = []
                self._static_pois = []
                return
        except Exception:
            pass

        _zid = geo_zones.resolve_zone(xyz[0], xyz[1], xyz[2])
        _zname = names.zone_name(_zid) if _zid else ""
        # Drop any raw technical IDs like "Z1", "Z2", "Z1_Enripit" that weren't mapped
        if _zname and re.fullmatch(r"Z\d+.*", _zname, re.IGNORECASE):
            _zname = ""
        isz = round(self.s.icon_size * self._scale)
        profile = self.model.player_profile()

        # Limit by range (400m) toggle restricts everything in the list to 400m.
        # Zone filtering was removed as it was unreliable.
        eff_max_dist = 400.0 if self.s.limit_by_zone else self.s.max_dist
        if eff_max_dist <= 0: eff_max_dist = 1000.0  # Plausible world-load limit if off
        enemy_max_dist = eff_max_dist

        # Companion range: False = 300m, Number = that range, True = eff_max_dist (fallback)
        c_debug = getattr(self.s, "show_companions_debug", False)
        if isinstance(c_debug, (int, float)) and not isinstance(c_debug, bool):
            comp_max_dist = float(c_debug)
        else:
            comp_max_dist = 300.0 if c_debug is False else eff_max_dist

        # 1) gather every section's list (selection cycles across all of them)
        # Using 2D distance for filtering ensures consistency with the Minimap's top-down view
        
        # ENEMIES: Grouping first to ensure the 'enemy_count' limit applies to groups, not raw units
        raw_enemies = self.model.nearest_enemies(
            xyz, 200, enemy_max_dist, player_zone=None, use_2d=True,
            hide_units=set(self.s.get_entity_hidden_units(profile))) \
            if self.s.show_enemies else []
        
        self._enemies = []
        if raw_enemies:
            # Group by name
            groups = {} # name -> [(entity, dist)]
            for e, d in raw_enemies:
                name = names.unit_name(e.unit_id) or "?"
                if name not in groups:
                    groups[name] = []
                groups[name].append((e, d))
            
            # Sort groups by nearest distance and take top N
            sorted_group_names = sorted(groups.keys(), key=lambda n: min(v[1] for v in groups[n]))
            limited_names = sorted_group_names[:self.s.enemy_count]
            
            # Flatten back to the list structure used by the HUD logic
            for name in limited_names:
                self._enemies.extend(groups[name])

        # All items in the entity list respect the 400m limit if enabled.
        # Zone filtering was removed as it was unreliable.
        self._group_members = self.model.nearest_group_members(
            xyz, self.s.group_count, eff_max_dist, use_2d=True) \
            if getattr(self.s, "show_group_members", False) else []
        self._comps = self.model.nearest_companions(
            xyz, self.s.companion_count, eff_max_dist, player_zone=None, use_2d=True,
            hide_units=set(self.s.get_companion_hidden_units(profile))) \
            if self.s.show_companions else []
        self._orbs = self._ranked_orbs(xyz, profile, player_zone=None, max_dist=eff_max_dist) if self.s.show_orbs else []
        self._static_pois = []
        
        if getattr(self.s, "show_gatherables", True):
            from ...geo import gatherables as geo_gatherables
            raw_g = []
            
            # 1. Live memory elements first
            for g in self.model.gatherables(player_zone=None, max_dist=eff_max_dist, use_2d=True):
                eid = g.elem_id or ""
                eid_l = eid.lower()
                is_resource = any(s in eid_l for s in ("ore", "flower", "madrigold", "zealotus", "lavendula", "thyme", "tungstene", "tin", "copper", "r2plant"))
                is_internal = any(s in eid_l for s in ("spawn", "trigger", "point", "marker", "area", "volume", "target", "bumper", "idle"))
                if is_internal and not is_resource:
                    continue
                dg = g.dist2d(xyz[0], xyz[1])
                raw_g.append(((g.x, g.y, g.z, "gather", eid, f"gl{g.addr}"), dg))

            # 2. Static nodes from JSON
            for g in geo_gatherables.load_nodes():
                d = math.hypot(g.x - xyz[0], g.y - xyz[1])
                if d <= eff_max_dist:
                    if any(math.hypot(g.x - ex, g.y - ey) < 2.0 for (ex, ey, ez, ek, el, ei), ed in raw_g):
                        continue
                    raw_g.append(((g.x, g.y, g.z, "gather", g.name, f"g{g.x}{g.y}"), d))

            # Sort groups by nearest member distance and take top N types
            raw_g.sort(key=lambda t: t[1])
            # Limit search to 20 nearest nodes to keep grouped counts small and relevant
            near_g = raw_g[:20]

            groups = defaultdict(list)
            for g_data, d in near_g:
                base = geo_gatherables.get_display_name(g_data[4])
                groups[base].append((g_data, d))

            sorted_group_names = sorted(groups.keys(), key=lambda n: min(v[1] for v in groups[n]))
            limited_names = sorted_group_names[:getattr(self.s, "gatherable_count", 5)]
            
            self._gatherables = []
            for name in limited_names:
                self._gatherables.extend(groups[name])
        else:
            self._gatherables = []

        # Force tracked unit/hero/gather into the list so it doesn't auto-flip when leaving range
        if self._tracker is not None:
            tk, tid, taddr = self.s.track_kind, self.s.track_id, self._tracker.locked_addr
            if tk == "unit" and tid:
                # 1. Check if we have a specific address match already in the lists
                has_tracked = (taddr and (any(getattr(e, "addr", None) == taddr for e, _ in self._enemies) or 
                                         any(getattr(e, "addr", None) == taddr for e, _ in self._comps))) or \
                              (not taddr and (any(e.unit_id == tid for e, _ in self._enemies) or 
                                              any(e.unit_id == tid for e, _ in self._comps)))
                
                if not has_tracked:
                    # Find the best match in memory
                    ent = None
                    if taddr:
                        ent = next((e for e in self.model.units() if e.addr == taddr), None)
                    else:
                        # Find the nearest one of this type
                        cands = [(e, e.dist2d(xyz[0], xyz[1])) for e in self.model.units() if e.unit_id == tid]
                        if cands:
                            ent = min(cands, key=lambda x: x[1])[0]

                    if ent:
                        dist = ent.dist2d(xyz[0], xyz[1])
                        if udata.is_companion(ent.unit_id):
                            self._comps.append((ent, dist))
                        else:
                            self._enemies.append((ent, dist))
            elif tk == "hero" and tid and taddr:
                if not any(getattr(e, "addr", None) == taddr for e, _ in self._group_members):
                    ent = next((e for e in self.model.units() if e.addr == taddr), None)
                    if ent:
                        self._group_members.append((ent, ent.dist2d(xyz[0], xyz[1])))
            elif tk == "enemy" and tid:
                # Force position-based enemies (from minimap clicks) into the list
                try:
                    coords_str, _, label = tid.partition("|")
                    tx, ty, tz = (float(v) for v in coords_str.split(","))
                    if not any(math.hypot(tx - e.x, ty - e.y) < 1.0 for e, _ in self._enemies):
                        # Use a dummy entity-like object or just skip if it's not live?
                        # For clean separation, we only show live units in Enemies group,
                        # but we want the minimap click to stick.
                        pass
                except (ValueError, IndexError):
                    pass
            elif tk in ("pos", "dungeon", "rift", "obelisk") and tid:
                try:
                    parts = tid.split("|")
                    coords_str = parts[0]
                    label = parts[1] if len(parts) > 1 else ""
                    tx, ty, tz = (float(v) for v in coords_str.split(","))
                    if not any(math.hypot(tx - p[0], ty - p[1]) < 0.5 for p, _ in self._static_pois):
                        dg = math.hypot(tx - xyz[0], ty - xyz[1])
                        
                        # Use the dungeon data for boss info and clean names
                        boss_id, boss_level, dungeon_name = None, None, ""
                        if tk == "rift":
                            d_info = dungeons.get_dungeon_info(label)
                            if d_info:
                                display_name = d_info['name']
                                if not display_name.lower().startswith("rift"):
                                    display_name = f"Rift {display_name}"
                                boss_id = d_info['boss_id']
                            else:
                                display_name = label if (label and label.lower() != "rift") else "Rift"
                                if display_name.lower() != "rift" and not display_name.lower().startswith("rift"):
                                    display_name = f"Rift {display_name}"
                        elif tk == "respawn":
                            display_name = label if (label and label.lower() != "respawn") else "Respawn"
                        elif tk == "obelisk":
                            display_name = names.poi_label(label or tk)
                            dungeon_name = "Obelisk"
                        elif tk == "dungeon":
                            d_info = dungeons.get_dungeon_info(label)
                            if d_info:
                                display_name = d_info['boss_name']
                                dungeon_name = d_info['name']
                                boss_id = d_info['boss_id']
                                boss_level = d_info['level']
                            else:
                                # Use cleaned name from poi_locs if dungeon lookup fails
                                display_name = names.poi_label(label or tk)
                                for prefix in ("Dungeon ", "Boss "):
                                    if display_name.startswith(prefix):
                                        display_name = display_name[len(prefix):]
                                # Simple typo fix for display if we still don't have d_info
                                display_name = display_name.replace("Abbandoned", "Abandoned")
                                dungeon_name = "Dungeon"
                        else:
                            display_name = names.poi_label(label or tk)
                        
                        sid = parts[2] if len(parts) > 2 else f"s{tx:.1f},{ty:.1f}"
                        self._static_pois.append(((tx, ty, tz, tk, display_name, sid, boss_id, boss_level, dungeon_name), dg))
                except (ValueError, IndexError):
                    pass
            elif tk == "gather" and tid:
                from ...geo import gatherables as geo_gatherables
                if "|" not in tid:
                    # Tracking by type name
                    if not any(geo_gatherables.get_display_name(g[4]) == tid for g, _ in self._gatherables):
                        # Find matching ones in memory/static and add at least the nearest one
                        cands = []
                        for g in self.model.gatherables(max_dist=eff_max_dist, use_2d=True):
                            if geo_gatherables.get_display_name(g.elem_id or "") == tid:
                                cands.append(((g.x, g.y, g.z, "gather", g.elem_id, f"gl{g.addr}"), g.dist2d(*xyz[:2])))
                        for node in geo_gatherables.load_nodes():
                            if geo_gatherables.get_display_name(node.name) == tid:
                                if not any(math.hypot(node.x - p[0], node.y - p[1]) < 2.0 for p, _ in cands):
                                    cands.append(((node.x, node.y, node.z, "gather", node.name, f"g{node.x}{node.y}"), math.hypot(node.x - xyz[0], node.y - xyz[1])))
                        if cands:
                            self._gatherables.append(min(cands, key=lambda x: x[1]))
                else:
                    # Tracking specific instance
                    try:
                        parts = tid.split("|")
                        coords_str = parts[0]
                        label = parts[1] if len(parts) > 1 else ""
                        tx, ty, tz = (float(v) for v in coords_str.split(","))
                        if not any(math.hypot(tx - g[0], ty - g[1]) < 0.5 for g, _ in self._gatherables):
                            dg = math.hypot(tx - xyz[0], ty - xyz[1])
                            gid = parts[2] if len(parts) > 2 else f"g{tx}{ty}"
                            self._gatherables.append(((tx, ty, tz, "gather", label, gid), dg))
                    except (ValueError, IndexError):
                        pass

        if self.s.track_kind == "orb" and self.s.track_id:
            if not any(o.orb_id == self.s.track_id for o, _ in self._orbs):
                o = geo_orbs.by_id().get(self.s.track_id)
                if o and not self.s.is_done(o.orb_id, profile):
                    self._orbs.append((o, o.dist2d(xyz[0], xyz[1])))

        # Automatically mark opened chests and collected orbs as done in settings.
        # One-way only: we add to done_list when a chest/orb is opened, but NEVER
        # auto-remove. Live state is unreliable during load transitions (e.g. dungeon
        # exit) — chests reappear in memory as "closed" before the game syncs their
        # state, which would wipe collected progress. Un-marking is manual only
        # (right-click on the minimap).
        done_list = self.s.get_poi_done(profile)
        changed = False
        
        # 1. Chests
        # Do not limit live chests check to player_zone here (scans all loaded elements)
        for e in self.model.live_chests(max_dist=eff_max_dist, use_2d=True):
            if e.elem_id:
                is_open = e.state and e.state.lower() in ("opened", "open", "looted")
                if is_open and e.elem_id not in done_list:
                    done_list.append(e.elem_id)
                    changed = True
                # Never auto-remove: the user must manually un-mark via right-click


        # 2. World Orbs (RedOrb_World)
        # Glow disappears reliably when collected
        for oid, glow in self.model.world_orb_fx():
            if not glow:
                if oid not in done_list:
                    done_list.append(oid)
                    changed = True
            # Note: We don't automatically un-mark orbs because glow can be missing
            # during load transitions or distance LOD. Manual reset only.

        if changed:
            if profile:
                self.s.save_profile_progress(profile, done_list)
            else:
                self.s.save()

        raw_chests = self.model.nearest_chests_merged(
            xyz, self.s.chest_count + 20, eff_max_dist, player_zone=None, use_2d=True) if self.s.show_chests else []
            
        # Filter out recipes and collected chests
        raw_chests = [c for c in raw_chests if "recipe" not in c.chest_id.lower() and "recipe" not in (c.loot_table or "").lower()]
        if getattr(self.s, "entity_hide_collected", True):
            raw_chests = [c for c in raw_chests if c.chest_id not in done_list]
            
        self._chests = raw_chests[:self.s.chest_count]

        if self.s.track_kind == "chest" and self.s.track_id:
            try:
                from ...core.chest_resolver import ChestRow
                parts = self.s.track_id.split("|")
                coords_str = parts[0]
                label = parts[1] if len(parts) > 1 else "chest"
                raw_id = parts[2] if len(parts) > 2 else label
                
                tx, ty, tz = (float(v) for v in coords_str.split(","))
                
                # Filter out recipes from the HUD list even if tracked
                if "recipe" in label.lower():
                    pass
                else:
                    if len(parts) <= 2:
                        matching_c = next((c for c in self.model.chests if c.chest_id == label), None)
                        if not matching_c:
                            matching_c = next((c for c in self.model.chests if math.hypot(tx - c.x, ty - c.y) < 1.0), None)
                        raw_id = matching_c.chest_id if matching_c else label

                    if not getattr(self.s, "entity_hide_collected", True) or raw_id not in done_list:
                        is_in_list = False
                        for c in self._chests:
                            if c.chest_id == raw_id or math.hypot(tx - c.x, ty - c.y) < 1.0:
                                is_in_list = True
                                break
                        if not is_in_list:
                            dist = math.hypot(tx - xyz[0], ty - xyz[1])
                            # If we're tracking it but it's obscenely far (despawned/unloaded), 
                            # just let it go if it's not the active selection.
                            if dist > 800.0 and self._sel != (self.s.track_kind, raw_id):
                                self._tracker.clear()
                            else:
                                matching_c = next((c for c in self.model.chests if c.chest_id == raw_id), None)
                                loot_table = matching_c.loot_table if matching_c else label
                                level = matching_c.level if matching_c else None
                                self._chests.append(ChestRow(raw_id, dist, loot_table, level, None, False, tx, ty, tz))
            except (ValueError, IndexError):
                pass

        # Force active selection into target lists so selection persists across ticks
        if self._sel is not None:
            skind, skey = self._sel
            if skind == "hero" and not any(str(getattr(e, "addr", None)) == str(skey) for e, _ in self._group_members):
                ent = next((e for e in self.model.units() if str(getattr(e, "addr", None)) == str(skey)), None)
                if ent:
                    self._group_members.append((ent, ent.dist2d(xyz[0], xyz[1])))
            elif skind == "comp" and not any(str(getattr(e, "addr", None)) == str(skey) for e, _ in self._comps):
                ent = next((e for e in self.model.units() if str(getattr(e, "addr", None)) == str(skey)), None)
                if ent:
                    self._comps.append((ent, ent.dist2d(xyz[0], xyz[1])))
            elif skind == "enemy":
                # For enemies, skey is the unit_id. If no unit of this type is in the list, force the nearest one.
                if not any(e.unit_id == skey for e, _ in self._enemies):
                    cands = [(e, e.dist2d(xyz[0], xyz[1])) for e in self.model.units() if e.unit_id == skey]
                    if cands:
                        ent, dist = min(cands, key=lambda x: x[1])
                        self._enemies.append((ent, dist))
            elif skind == "gather":
                from ...geo import gatherables as geo_gatherables
                # For gatherables, skey is the display name
                if not any(geo_gatherables.get_display_name(g[4]) == skey for g, _ in self._gatherables):
                    # Find matching ones in memory/static and add at least the nearest one
                    cands = []
                    for g in self.model.gatherables(max_dist=eff_max_dist, use_2d=True):
                        if geo_gatherables.get_display_name(g.elem_id or "") == skey:
                            cands.append(((g.x, g.y, g.z, "gather", g.elem_id, f"gl{g.addr}"), g.dist2d(*xyz[:2])))
                    for node in geo_gatherables.load_nodes():
                        if geo_gatherables.get_display_name(node.name) == skey:
                            if not any(math.hypot(node.x - p[0], node.y - p[1]) < 2.0 for p, _ in cands):
                                cands.append(((node.x, node.y, node.z, "gather", node.name, f"g{node.x}{node.y}"), math.hypot(node.x - xyz[0], node.y - xyz[1])))
                    if cands:
                        self._gatherables.append(min(cands, key=lambda x: x[1]))
            elif skind == "orb" and not any(str(o.orb_id) == str(skey) for o, _ in self._orbs):
                if not getattr(self.s, "entity_hide_collected", True) or not self.s.is_done(skey, profile):
                    o = geo_orbs.by_id().get(skey)
                    if o:
                        self._orbs.append((o, o.dist2d(xyz[0], xyz[1])))
            elif skind in ("chest", "recipe") and not any(str(c.chest_id) == str(skey) for c in self._chests):
                if not getattr(self.s, "entity_hide_collected", True) or skey not in done_list:
                    from ...core.chest_resolver import ChestRow
                    matching_c = next((c for c in self.model.chests if str(c.chest_id) == str(skey)), None)
                    if matching_c:
                        dist = matching_c.dist2d(xyz[0], xyz[1])
                        self._chests.append(ChestRow(matching_c.chest_id, dist, matching_c.loot_table, matching_c.level, None, False, matching_c.x, matching_c.y, matching_c.z))

        # Deduplicate unit lists by address to prevent duplicate HUD rows
        def _dedup_units(lst):
            seen, res = set(), []
            for item, dist in lst:
                addr = getattr(item, "addr", id(item))
                if addr not in seen:
                    seen.add(addr)
                    res.append((item, dist))
            return res

        self._enemies = _dedup_units(self._enemies)
        self._comps = _dedup_units(self._comps)
        self._group_members = _dedup_units(self._group_members)

        # 2) keep selection valid (auto-flip to first available target)
        targets = self._targets()
        if self._sel is not None and self._sel not in targets:
            old_sel = self._sel
            self._sel = None
            
            # ONLY auto-select the next one if the toggle is actually ON in settings
            if getattr(self.s, "auto_select_next_collectible", False):
                old_kind = old_sel[0]
                if old_kind in ("orb", "chest", "gather"):
                    # Prioritize finding the exact same kind first
                    next_coll = next((t for t in targets if t[0] == old_kind), None)
                    
                    # Fallback to similar types only if the current category is empty
                    if not next_coll and old_kind in ("chest", "gather"):
                        # Gatherables can fall back to Orbs/Chests and vice versa
                        next_coll = next((t for t in targets if t[0] in ("orb", "chest", "gather")), None)

                    if next_coll:
                        limit = eff_max_dist if eff_max_dist > 0 else 500.0
                        if next_coll[0] == "orb":
                            o = next((orb for orb, _dist in self._orbs if orb.orb_id == next_coll[1]), None)
                            dist = o.dist2d(xyz[0], xyz[1]) if o else 9999.0
                            if dist <= limit:
                                self._sel = next_coll
                                self._track("orb", next_coll[1], force=True)
                        elif next_coll[0] == "chest":
                            c = next((ch for ch in self._chests if ch.chest_id == next_coll[1]), None)
                            if c and c.dist <= limit:
                                label = names.chest_label(c.chest_id, c.loot_table)
                                self._sel = next_coll
                                self._track("chest", f"{c.x:.1f},{c.y:.1f},{c.z:.1f}|{label}|{c.chest_id}", force=True)
                        elif next_coll[0] == "gather":
                            # next_coll[1] is the type name (e.g. "Madrigold")
                            self._sel = next_coll
                            self._track("gather", next_coll[1], force=True)

            # If the selected target is gone and no auto-replacement was found, clear the compass & map pan
            if self._sel is None and self._tracker is not None:
                self._tracker.clear()
                mgr = self._tracker.parent()
                if mgr:
                    map_ov = getattr(mgr, "overlays", {}).get("map")
                    if map_ov and hasattr(map_ov, "canvas") and (map_ov.canvas._pan_x != 0.0 or map_ov.canvas._pan_y != 0.0):
                        map_ov.canvas._pan_x = map_ov.canvas._pan_y = 0.0
                        map_ov.canvas.update()

        # 3) fallback: clear tracker if the target is now "done" (handles minimap clicks without HUD selection)
        if self._tracker is not None and self.s.track_kind in ("orb", "chest", "recipe", "gather", "pos"):
            tk, tid = self.s.track_kind, self.s.track_id
            parts = tid.split("|")
            target_id = parts[-1] if (tk in ("chest", "recipe", "gather", "pos") and len(parts) >= 3) else (tid if tk == "orb" else "")
            
            if tk in ("chest", "recipe", "gather", "pos") and not target_id:
                try: # resolve from coords
                    coords_str = parts[0]
                    label = parts[1] if len(parts) > 1 else ""
                    tx, ty, _ = (float(v) for v in coords_str.split(","))
                    if tk in ("chest", "recipe"):
                        matching_c = next((c for c in self.model.chests if math.hypot(tx - c.x, ty - c.y) < 1.0), None)
                        target_id = matching_c.chest_id if matching_c else label
                    else: target_id = label
                except: target_id = label
            
            # Only auto-clear the tracker if we are hiding collected items. 
            # If the user is showing collected items, they likely want to be able to point at them.
            if target_id and self.s.is_done(target_id, profile) and getattr(self.s, "entity_hide_collected", True):
                self._tracker.clear()

        # 3) render enemies
        if self.s.show_enemies:
            self.enemy_box.show()
            
            # Sort groups by nearest member distance
            # (Note: self._enemies is already limited to N groups by the logic in _refresh)
            groups = defaultdict(list)
            for e, d in self._enemies:
                name = names.unit_name(e.unit_id) or "?"
                groups[name].append((e, d))
            
            sorted_groups = sorted(groups.items(), key=lambda x: min(v[1] for v in x[1]))

            specs = []
            for name, variants in sorted_groups:
                variants.sort(key=lambda x: x[1])
                # Check if the user selected a specific member of this group
                sel_pair = next((v for v in variants if ("enemy", getattr(v[0], "addr", None)) == self._sel), None)
                if sel_pair:
                    e, d = sel_pair
                else:
                    e, d = variants[0]
                count = len(variants)
                
                addr = getattr(e, "addr", None)
                key = ("enemy", e.unit_id)
                # Badge shows if this unit type is tracked or selected
                sel = self._sel == key or (self._sel and self._sel[0] == "enemy" and self._sel[1] == e.unit_id)
                tracked = self._is_tracked("unit", e.unit_id)

                is_boss = udata.is_boss(e.unit_id)
                is_elite = udata.is_elite(e.unit_id)
                border_col = theme.GOLD if is_boss else None

                # Base color for this row (Default: Danger/Red for enemies)
                base_col = theme.KIND_COLOR.get(e.kind, theme.DANGER)

                # Icon and Outline color
                if is_boss:
                    col = theme.GOLD
                elif is_elite:
                    col = theme.SILVER
                else:
                    col = base_col

                # Name color - Accent if selected/tracked, otherwise Gold for boss, normal (Red) for elite/trash
                if is_boss:
                    name_col = theme.GOLD
                elif sel or tracked:
                    name_col = self.s.hud_accent or theme.ACCENT
                else:
                    name_col = base_col
                
                display_name = name if count <= 1 else f"{name} (x{count})"
                specs.append(RowSpec(
                    "Units", e.unit_id, col, display_name, name_col,
                    sub="◈ TRACKING" if tracked else "",
                    value=f"{d:>6.0f}m", bold=sel or is_boss or tracked, highlight=sel or tracked, 
                    outlined=True, outline_border=4 if (is_boss or is_elite) else 2,
                    border_color=border_col,
                    cb=(lambda k=key: self._select(*k)),
                    right_cb=(lambda u=e.unit_id: self._hide_unit(u)),
                    key=key))

            # Undo slot for enemies
            panel = getattr(self, "_main_win", None)
            last = getattr(panel, "_last_hidden_unit", None) if panel else None
            if last:
                hidden_units = set(self.s.get_entity_hidden_units(profile))
                if last[0] in hidden_units:
                    specs.append(RowSpec(
                        "Units", last[0], theme.MUTED, last[1], theme.MUTED,
                        sub="Undo Last Hidden", value="HIDDEN",
                        highlight=True, outlined=True,
                        cb=lambda: panel._unhide_last_unit() if panel else None,
                        right_cb=lambda: panel._unhide_last_unit() if panel else None,
                        key=("hidden", last[0])))

            self.enemy_box.fill(specs, isz)
            tag = ""
            if not getattr(self.model, "units_ok", True):
                tag = "READ FAILED · RETRYING"
            self.enemy_box.header.set_tag(tag)
            self.enemy_box.setVisible(bool(specs) or bool(tag))
        else:
            self.enemy_box.hide()

        # 3.5) render group members
        if getattr(self.s, "show_group_members", False):
            self.group_box.show()
            specs = []
            for e, d in self._group_members:
                addr = getattr(e, "addr", None)
                key = ("hero", addr)
                sel = addr is not None and key == self._sel
                tracked = sel and self._is_tracked("hero", e.unit_id)
                cls_name = e.cls or ""
                hero_class = "warrior"
                if cls_name.startswith("ent.hero."):
                    hero_class = cls_name.replace("ent.hero.", "").lower()
                elif e.unit_id:
                    hero_class = e.unit_id.lower()
                    if "warrior" in hero_class: hero_class = "warrior"
                    elif "rogue" in hero_class: hero_class = "rogue"
                    elif "mage" in hero_class: hero_class = "mage"
                    elif "priest" in hero_class: hero_class = "priest"
                
                col_hex = theme.HERO.get(hero_class, theme.TEXT)
                col = col_hex # Keep class color even when selected/tracked
                specs.append(RowSpec(
                    None, None, col, self.model.hero_display_name(e), col,
                    sub="◈ TRACKING" if tracked else "",
                    value=f"{d:>6.0f}m", bold=sel, highlight=sel or tracked, outlined=True,
                    ui_icon="player",
                    cb=(lambda k=key: self._select(*k)) if addr is not None else None,
                    key=key))
            self.group_box.fill(specs, isz)
            tag = ""
            if not getattr(self.model, "units_ok", True):
                tag = "READ FAILED · RETRYING"
            self.group_box.header.set_tag(tag)
            self.group_box.setVisible(bool(specs) or bool(tag))
        else:
            self.group_box.hide()

        # 4) render wild companions (critters) - their own section, never
        # enemies, never distance-capped. Ones missing from the account
        # collection get flagged.
        if self.s.show_companions:
            self.comp_box.show()
            specs = []
            n_new = 0
            for e, d in self._comps:
                unit_id = e.unit_id
                display_name = names.unit_name(unit_id) or names.humanize(unit_id)

                missing = self._owned is not None and unit_id not in self._owned
                n_new += missing
                addr = getattr(e, "addr", None)
                sel = addr is not None and ("comp", addr) == self._sel
                # Badge follows the selected row — not the tracker's closest-mob lock
                tracked = sel and self._is_tracked("unit", unit_id)
                is_spark = "spark" in display_name.lower()
                
                if sel or tracked:
                    col = self.s.hud_accent or theme.ACCENT
                else:
                    col = theme.GOLD if (missing or is_spark) else theme.GOOD

                sub_bits = [b for b in (
                    "★ SPARK" if is_spark else "",
                    "★ NOT COLLECTED" if missing else "",
                    "◈ TRACKING" if tracked else "") if b]
                if sel:
                    sub_bits.append(unit_id)
                specs.append(RowSpec(
                    "collection", unit_id, col,
                    display_name, col,
                    sub="  ·  ".join(sub_bits),
                    value=f"{d:>6.0f}m", bold=missing or sel or is_spark,
                    highlight=missing or tracked or sel, outlined=True,
                    outline_border=2,
                    border_color=theme.GOLD if is_spark else None,
                    cb=(lambda k=("comp", addr): self._select(*k)) if addr is not None else None,
                    right_cb=(lambda u=unit_id: self._hide_companion(u)),
                    key=("comp", addr)))

            # Undo slot for companions
            panel = getattr(self, "_main_win", None)
            last = getattr(panel, "_last_hidden_comp", None) if panel else None
            if last:
                hidden_comps = set(self.s.get_companion_hidden_units(profile))
                if last[0] in hidden_comps:
                    specs.append(RowSpec(
                        "collection", last[0], theme.MUTED, last[1], theme.MUTED,
                        sub="Undo Last Hidden", value="HIDDEN",
                        highlight=True, outlined=True,
                        cb=lambda: panel._unhide_last_comp() if panel else None,
                        right_cb=lambda: panel._unhide_last_comp() if panel else None,
                        key=("hidden", last[0])))

            self.comp_box.fill(specs, isz)
            tag = "WILD"
            if not getattr(self.model, "units_ok", True):
                tag = "READ FAILED · RETRYING"
            self.comp_box.header.set_tag(tag)
            self.comp_box.setVisible(bool(specs))
        else:
            self.comp_box.hide()

        # 4.5) render gatherables (Ores/Plants)
        if getattr(self.s, "show_gatherables", True):
            self.gather_box.show()
            from ...geo import gatherables as geo_gatherables
            
            # Group them for rendering (similar to enemies)
            groups = defaultdict(list)
            for g_data, d in self._gatherables:
                base = geo_gatherables.get_display_name(g_data[4])
                groups[base].append((g_data, d))
            
            sorted_groups = sorted(groups.items(), key=lambda x: min(v[1] for v in x[1]))
            
            specs = []
            for name, variants in sorted_groups:
                variants.sort(key=lambda x: x[1])
                g_data, d = variants[0]
                count = len(variants)
                
                label = g_data[4]
                key = ("gather", name)
                sel = self._sel == key
                tracked = self._is_tracked("gather", name)
                
                name_l = label.lower()
                is_ore = "ore" in name_l or "tungstene" in name_l or "tin" in name_l or "copper" in name_l
                accent = theme.KIND_COLOR["ore" if is_ore else "flower"]
                col = self.s.hud_accent if (sel or tracked) else accent
                
                # Auto-scale logic match (for sub-text or visual emphasis)
                is_large = "_large" in name_l or "_big" in name_l or "tungstene" in name_l
                
                display_name = name if count <= 1 else f"{name} (x{count})"
                marker = geo_gatherables.get_icon_name(label)
                
                # Check if this gatherable type is enabled in settings
                setting_attr = geo_gatherables.get_setting_attr(label)
                if setting_attr and not getattr(self.s, setting_attr, True):
                    continue
                
                # Fallback to generic flower/ore if icon not found
                if not icons.asset_icon(marker, 16):
                    marker = "ore" if is_ore else "flower"
                
                sub = ""
                if tracked:
                    sub = "◈ TRACKING"

                specs.append(RowSpec(
                    None, None, accent, display_name, col,
                    sub=sub,
                    value=f"{d:>6.0f}m", bold=sel or tracked or is_large,
                    highlight=sel or tracked, outlined=True,
                    outline_border=2,
                    cb=(lambda k=key: self._select(*k)),
                    marker=marker,
                    key=key))
            self.gather_box.fill(specs, isz)
            self.gather_box.header.set_tag("")
            self.gather_box.setVisible(bool(specs))
        else:
            self.gather_box.hide()

        # 5) render the nearest uncollected secret orbs (click = compass needle)
        if self.s.show_orbs:
            self.orb_box.show()
            orb_col = theme.KIND_COLOR["orb"]
            specs = []
            for o, d in self._orbs:
                done = self.s.is_done(o.orb_id, profile)
                tracked = self._is_tracked("orb", o.orb_id)
                sel = ("orb", o.orb_id) == self._sel
                
                col = self.s.hud_accent if (sel or tracked) else orb_col
                
                specs.append(RowSpec(
                    None, None, col if (sel or tracked) else None, geo_orbs.orb_label(o.orb_id), col,
                    sub="◈ TRACKING" if tracked else "",
                    value=f"{d:>6.0f}m", bold=tracked or sel,
                    highlight=tracked or sel, outlined=True,
                    outline_border=2,
                    cb=(lambda oid=o.orb_id: self._select("orb", oid)),
                    marker="orb2" if done else "orb",
                    key=("orb", o.orb_id)))
            self.orb_box.fill(specs, isz)
            self.orb_box.header.set_tag(
                "" if self._orbs else "ALL MARKED")
            self.orb_box.setVisible(bool(specs))
        else:
            self.orb_box.hide()

        # 6) render chests (also selectable -> show their loot table)
        if self.s.show_chests:
            self.chest_box.show()
            specs = []
            for c in self._chests:
                is_recipe = "recipe" in c.chest_id.lower() or (c.loot_table and "recipe" in (c.loot_table or "").lower())
                kind = "recipe" if is_recipe else "chest"
                sel = (kind, c.chest_id) == self._sel
                color = theme.CHEST # Keep gold color even when selected/tracked

                done = c.chest_id in done_list
                label = names.chest_label(c.chest_id, c.loot_table)

                cx, cy, cz = c.x, c.y, c.z
                # check if this specific chest is the tracked target
                is_tracked = self._is_tracked(kind, f"{cx:.1f},{cy:.1f},{cz:.1f}|{label}") or \
                             self._is_tracked("pos", f"{cx:.1f},{cy:.1f},{cz:.1f}|{label}")

                accent = theme.CHEST
                marker = "Recipe" if is_recipe else "chest"
                if done:
                    marker += "2"

                sub = ""
                if is_tracked:
                    state_tag = (c.state or ("OPENED" if done else "CLOSED")).upper()
                    sub = f"◈ TRACKING  ·  {state_tag}"

                name_col = self.s.hud_accent if (sel or is_tracked) else color

                specs.append(RowSpec(
                    None, None, None if (done or is_recipe) else accent, label, name_col,
                    sub=sub, value=f"{c.dist:>6.0f}m",
                    bold=sel or is_tracked, highlight=sel or is_tracked, outlined=True,
                    outline_border=2,
                    cb=(lambda k=(kind, c.chest_id): self._select(*k)),
                    marker=marker,
                    key=(kind, c.chest_id)))
            self.chest_box.fill(specs, isz)
            self.chest_box.header.set_tag("")
            self.chest_box.setVisible(bool(specs))
        else:
            self.chest_box.hide()

        rift_mode = self._get_rift_mode()

        rst = self.model.rift_status() if rift_mode != "Off" else None
        if rst and rst.state in ("WARNING", "ACTIVE", "CLOSING", "SCHEDULED"):
            show_card = (rift_mode == "Always") or (rift_mode == "Active" and rst.state in ("WARNING", "ACTIVE", "CLOSING"))
            if show_card:
                self.rift_box.show()
                self.rift_box.header.set_tag("")
                r_key = ("rift_active", f"s{rst.poi_x:.1f},{rst.poi_y:.1f}" if rst.poi_x is not None else "rift_schedule")
                r_sel = self._sel == r_key
                
                # Highlight only during active/warning (15m + 3m window)
                is_active_window = rst.state in ("WARNING", "ACTIVE", "CLOSING")
                r_col = theme.GOLD if rst.state in ("WARNING", "SCHEDULED") else (theme.DANGER if rst.state == "CLOSING" else (theme.GOOD if rst.state == "ACTIVE" else theme.TEXT))
                
                in_range = getattr(rst, "in_range", False)
                subzone = getattr(rst, "subzone", rst.rift_name or "")
                
                area = subzone if subzone and subzone != "Rift" else ""
                t = rst.formatted_time

                # Determine labels based on range and state (7 granular states)
                if rst.state == "ACTIVE":
                    r_label = rst.status_label or (f"Rift · {area} (Active)" if area else "Rift Active")
                    r_sub = ""
                elif in_range:
                    if rst.state == "CLOSING":
                        # 3. CLOSING (In Range)
                        r_label = f"Rift · {area} Closing In · {t}" if area else f"Rift Closing In · {t}"
                        r_sub = ""
                    elif rst.state == "WARNING":
                        # 2. WARNING (In Range)
                        r_label = f"Rift · {area} Starting In · {t}" if area else f"Rift Starting In · {t}"
                        r_sub = ""
                    else:
                        # 1. SCHEDULED (In Range)
                        r_label = f"Next Rift · Starting In · {t}"
                        r_sub = ""
                else:
                    if rst.state == "CLOSING":
                        # 6. CLOSING (Out of Range)
                        r_label = f"Rift · {area} Closing In · {t}" if area else f"Rift Closing In · {t}"
                        r_sub = ""
                    elif rst.state == "WARNING":
                        # 5. WARNING (Out of Range)
                        r_label = f"Rift · {area} Starting In · {t}" if area else f"Rift Starting In · {t}"
                        r_sub = ""
                    else:
                        # 4. SCHEDULED (Out of Range)
                        r_label = f"Next Rift · {area} · {t}" if area else f"Next Rift In · {t}"
                        r_sub = ""


                r_val = ""
                if rst.poi_x is not None and self.model.player_xyz():
                    px, py, _ = self.model.player_xyz()
                    d = math.hypot(rst.poi_x - px, rst.poi_y - py)
                    r_val = f"{d:>5.0f}m"

                # Size: Active/Warning/Closing (The "Rift" is active) is 20px, Upcoming is 16px
                is_active_window = rst.state in ("WARNING", "ACTIVE", "CLOSING")
                r_fs = 16 if is_active_window else 17

                self.rift_box.fill([RowSpec(
                    None, None, "#C084FC" if is_active_window else None,
                    r_label, r_col,
                    sub=r_sub, value=r_val,
                    bold=is_active_window or r_sel,
                    highlight=is_active_window or r_sel,
                    outlined=True, marker="rift",
                    cb=(lambda k=r_key: self._select(*k)),
                    key=r_key,
                    font_size=r_fs
                )], isz)
            else:
                self.rift_box.hide()
        else:
            self.rift_box.hide()

        # 6.5) render tracked static POIs (Dungeons/Obelisks/Waypoints)
        if self._static_pois:
            self.static_box.show()
            self.static_box.header.set_text("WAYPOINT  ◈ TRACKING")
            specs = []

            for p_data, d in self._static_pois:
                label = p_data[4]
                kind = p_data[3]
                key = ("static", p_data[5])
                # boss_id is at index 6, level at index 7, dungeon_name at index 8
                boss_id = p_data[6] if len(p_data) > 6 else None
                level = p_data[7] if len(p_data) > 7 else None
                dname = p_data[8] if len(p_data) > 8 else ""
                sel = self._sel == key
                
                # Determine icon and accent based on kind
                ui_icon = ""
                marker_name = ("rift" if kind == "rift" else "dungeon") if kind in ("dungeon", "rift") else ("obelisk" if kind == "obelisk" else "respawnpoint" if kind == "respawn" else "map-pin")
                
                sub = ""
                sub_color = None
                
                if level:
                    # Line 1: Boss Name  ·  Level # (Boss Name bold, Level normal)
                    label = f"<b>{label}</b>  ·  Level {level}"
                elif dname:
                    # Line 1: Bold Title for Dungeons/Obelisks
                    label = f"<b>{label}</b>"
                
                if dname:
                    # Line 2: Dungeon Name / POI Type
                    sub = dname
                    sub_color = self.s.hud_accent if sel else theme.GOLD

                if boss_id:
                    acc = self.s.hud_accent if sel else None
                    boss_acc = self.s.hud_accent if sel else theme.GOLD
                    color = self.s.hud_accent if sel else theme.GOLD
                    specs.append(RowSpec(
                        None, None, acc, label or kind.capitalize(), color,
                        sub=sub, sub_color=sub_color, value=f"{d:>6.0f}m",
                        bold=False, highlight=sel, outlined=True,
                        cb=(lambda k=key: self._select(*k)),
                        ui_icon=ui_icon, marker=marker_name,
                        extra_icon=("Units", boss_id, "", True, boss_acc),
                        key=key,
                        size_override=isz + 2 if kind in ("dungeon", "rift") else None))
                else:
                    acc = self.s.hud_accent if sel else None
                    color = self.s.hud_accent if sel else theme.TEXT
                    specs.append(RowSpec(
                        None, None, acc, label or kind.capitalize(), color,
                        sub=sub, sub_color=sub_color, value=f"{d:>6.0f}m",
                        bold=False, highlight=sel, outlined=True,
                        cb=(lambda k=key: self._select(*k)),
                        marker=marker_name,
                        ui_icon=ui_icon,
                        key=key,
                        size_override=isz + 2 if kind in ("dungeon", "rift") else None))
            self.static_box.fill(specs, isz)
        else:
            self.static_box.hide()
            self.static_box.header.set_text("WAYPOINT")

        # 7) keep the open drop window synced with live selection / auto-flip
        self._update_drops()
        self._check_auto_height()

    def _check_auto_height(self):
        # Fingerprint includes counts and visibility state of items
        # Also include waypoint labels as they can have multi-line subtext
        grouped_enemy_count = len(set(names.unit_name(e.unit_id) for e, _ in self._enemies))
        
        # Undo row visibility
        profile = self.model.player_profile()
        last_u = getattr(self._main_win, "_last_hidden_unit", None) if hasattr(self, "_main_win") else None
        has_undo_u = bool(last_u and last_u[0] in set(self.s.get_entity_hidden_units(profile)))
        last_c = getattr(self._main_win, "_last_hidden_comp", None) if hasattr(self, "_main_win") else None
        has_undo_c = bool(last_c and last_c[0] in set(self.s.get_companion_hidden_units(profile)))

        # Rift visibility
        rift_vis = self.rift_box.isVisible()
        wp_fingerprint = tuple((p[4], p[8]) for p, _ in self._static_pois)

        curr = (
            grouped_enemy_count, len(self._comps), len(self._group_members),
            len(self._gatherables), len(self._orbs), len(self._chests), 
            len(self._static_pois), wp_fingerprint, has_undo_u, has_undo_c, rift_vis,
            self.s.icon_size, self.s.entity_font_size, self.s.entity_width
        )
        
        if curr != self._last_h_config:
            self._last_h_config = curr
            # Apply live font-size change — rebuilds the per-overlay QSS
            new_scale = self.s.entity_font_size / 14.0
            if abs(new_scale - self._scale) > 0.005:
                self._base_w = max(300, min(800, self.s.entity_width))
                self.apply_scale(new_scale)
            
            # Apply live width change (independent of font scale)
            desired_w = max(300, min(800, self.s.entity_width))
            if desired_w != self.width():
                self._base_w = desired_w
                self.resize(desired_w, self.height())

            # Use a singleShot to let the layout settle before measuring height
            QtCore.QTimer.singleShot(0, self._do_auto_height)

    def _do_auto_height(self):
        # Use Qt's own layout measurement: ask the scroll body for its
        # preferred height, then add the fixed chrome overhead:
        #   frame border (2) + title bar (34) + content margins top+bot (14) = 50
        CHROME = 50
        body_hint = self._body.sizeHint().height()
        target_h = body_hint + CHROME
        # Clamp to usable screen height
        screen_h = QtWidgets.QApplication.primaryScreen().availableGeometry().height()
        target_h = min(target_h, screen_h - 40)
        target_h = max(target_h, 160)
        if target_h != self.height():
            self._base_h = target_h
            self.resize(self.width(), target_h)

    def closeEvent(self, e):
        self._timer.stop()
        if self._drop_win is not None:
            self._drop_win.close()
            self._drop_win = None
        super().closeEvent(e)
