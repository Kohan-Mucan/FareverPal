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
from ...data import names, tokens, icons, units as udata
from ...geo import orbs as geo_orbs, zones as geo_zones

POLL_MS = 350
_RARITY_ORDER = {"Legendary": 0, "Epic": 1, "Rare": 2, "Uncommon": 3, "Common": 4}


@dataclass
class RowSpec:
    sheet: str | None
    id_: str | None
    accent: str
    name: str
    name_color: str
    sub: str = ""
    value: str = ""
    bold: bool = False
    highlight: bool = False
    cb: object = None
    marker: str = ""      # map-marker icon name instead of a game-sheet icon
    ui_icon: str = ""     # UI SVG icon name instead of a game-sheet icon
    outlined: bool = False   # use outlined style (minimap style) instead of tile
    gold_border: bool = False  # draw a gold CSS border around rare/special rows


class _EntityRow(QtWidgets.QFrame):
    """IconTile + name (+ optional mono sub-label) + right value. Pooled."""
    clicked = QtCore.Signal()

    def __init__(self, icon_size: int):
        super().__init__()
        self._highlight, self._cb = "", None
        lay = QtWidgets.QHBoxLayout(self); lay.setContentsMargins(6, 2, 6, 2); lay.setSpacing(9)
        self.icon_box = QtWidgets.QFrame(); self.icon_box.setObjectName("IconBox")
        box_lay = QtWidgets.QHBoxLayout(self.icon_box); box_lay.setContentsMargins(0, 0, 0, 0)
        self.tile = IconTile(icon_size); mid = QtWidgets.QVBoxLayout()
        mid.setContentsMargins(0, 0, 0, 0); mid.setSpacing(0)
        self.name, self.sub = QtWidgets.QLabel(), QtWidgets.QLabel()
        self.sub.setObjectName("Mono")
        mid.addWidget(self.name); mid.addWidget(self.sub)
        box_lay.addWidget(self.tile)
        self.value = QtWidgets.QLabel(); self.value.setObjectName("Mono")
        self.value.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter); self.value.setMinimumWidth(72)
        lay.addWidget(self.icon_box); lay.addLayout(mid, 1); lay.addWidget(self.value, 0)

    def apply(self, spec: RowSpec, icon_size: int):
        self.tile.set_size(icon_size)
        if spec.ui_icon: self.tile.set_ui_icon(spec.ui_icon, spec.accent)
        elif spec.marker: self.tile.set_marker(spec.marker, spec.accent)
        elif spec.outlined: self.tile.set_outlined(spec.sheet, spec.id_, spec.accent)
        else: self.tile.set(spec.sheet, spec.id_, spec.accent)
        weight = "700" if spec.bold else "500"
        self.name.setText(spec.name)
        self.name.setStyleSheet(f"color:{spec.name_color};font-weight:{weight};background:transparent;")
        self.sub.setText(spec.sub); self.sub.setVisible(bool(spec.sub))
        if spec.sub: self.sub.setStyleSheet(f"color:{theme.MUTED};background:transparent;")
        self.value.setText(spec.value); self.value.setStyleSheet(f"color:{spec.name_color};background:transparent;")
        self._highlight = spec.accent if spec.highlight else ""
        # Gold border via stylesheet — reliable, survives child-widget repaint
        gold = getattr(spec, "gold_border", False)
        if gold != getattr(self, "_gold_border", False):
            self._gold_border = gold
            if gold:
                self.icon_box.setStyleSheet(f"QFrame#IconBox {{ border: 1px solid {theme.GOLD}; }}")
                self.icon_box.layout().setContentsMargins(2, 2, 2, 2)
            else:
                self.icon_box.setStyleSheet("QFrame#IconBox { border: none; }")
                self.icon_box.layout().setContentsMargins(0, 0, 0, 0)
        self.set_callback(spec.cb); self.update()

    def set_callback(self, cb):
        if self._cb is not None: self.clicked.disconnect(self._cb); self._cb = None
        if cb is not None: self._cb = cb; self.clicked.connect(cb)
        self.setCursor(QtCore.Qt.PointingHandCursor if cb else QtCore.Qt.ArrowCursor)

    def paintEvent(self, e):
        super().paintEvent(e)          # draw QSS background/frame first
        if self._highlight:
            p = QtGui.QPainter(self); p.setRenderHint(QtGui.QPainter.Antialiasing, False)
            c = QtGui.QColor(self._highlight); bg = QtGui.QColor(c); bg.setAlpha(30)
            p.fillRect(self.rect(), bg); p.fillRect(0, 0, 2, self.height(), c); p.end()

    def mousePressEvent(self, e):
        if e.button() == QtCore.Qt.LeftButton: self.clicked.emit()
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
    scroll.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded); scroll.verticalScrollBar().setStyleSheet("margin:0;")
    body = QtWidgets.QWidget(); lay = QtWidgets.QVBoxLayout(body)
    lay.setContentsMargins(0, 0, 13, 0); lay.setSpacing(0); scroll.setWidget(body)
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
        self._owned: set | None = None    # account collection (None = signed out)
        self._sel = None                  # ("enemy", addr) | ("chest", chest_id)
        self._tracker = None              # shared compass-needle TrackController
        self._drop_win: DropTableOverlay | None = None
        self.setFocusPolicy(QtCore.Qt.StrongFocus)

        coords = QtWidgets.QHBoxLayout()
        self.pos = QtWidgets.QLabel("waiting for player…")
        self.pos.setObjectName("MonoText")
        tag = QtWidgets.QLabel("LIVE")
        tag.setObjectName("Mono")
        tag.setStyleSheet(
            f"color:{theme.GOOD};background:{theme.with_alpha(theme.GOOD,30)};"
            f"padding:1px 6px;font-weight:700;")
        coords.addWidget(self.pos)
        coords.addStretch(1)
        coords.addWidget(tag)
        self.content.addLayout(coords)

        scroll, self._body = _scroll_body()
        self.content.addWidget(scroll, 1)
        self.enemy_box = _Section("ENEMIES", theme.DANGER)
        self.group_box = _Section("GROUP MEMBERS", theme.MUTED)
        self.comp_box = _Section("COMPANIONS", theme.GOOD)
        self.gather_box = _Section("GATHERABLES", "#c2c6d0")
        self.orb_box = _Section("SECRET ORBS", theme.KIND_COLOR["orb"])
        self.chest_box = _Section("CHESTS", theme.CHEST)
        for b in (self.enemy_box, self.group_box, self.comp_box, self.gather_box, self.orb_box, self.chest_box):
            self._body.addWidget(b)
        self._body.addStretch(1)

        self.enable_resize_grip()
        self._base_w, self._base_h = 320, 440
        self.setMinimumWidth(round(280 * self.s.entity_scale))
        self.apply_scale(self.s.entity_scale)     # scaled QSS + resize to base*scale
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

    def _targets(self):
        """Ordered selectable keys across every visible section: enemies,
        companions, orbs, chests (matching the HUD layout)."""
        t = [("enemy", getattr(e, "addr", None)) for e, _ in self._enemies
             if getattr(e, "addr", None) is not None]
        t += [("hero", getattr(e, "addr", None)) for e, _ in self._group_members
              if getattr(e, "addr", None) is not None]
        t += [("comp", getattr(e, "addr", None)) for e, _ in self._comps
              if getattr(e, "addr", None) is not None]
        t += [("gather", g[5]) for g, _ in self._gatherables]
        t += [("orb", o.orb_id) for o, _ in self._orbs]
        for c in self._chests:
            is_r = "recipe" in c.chest_id.lower() or (c.loot_table and "recipe" in (c.loot_table or "").lower())
            t.append(("recipe" if is_r else "chest", c.chest_id))
        return t

    def _selected_source(self):
        """(near, name) for the current selection, or (None, ''). Companions
        and orbs aren't loot sources - they drive the compass instead."""
        if self._sel is None:
            return None, ""
        kind, key = self._sel
        if kind in ("comp", "orb", "hero", "gather"):
            return None, ""
        if kind == "enemy":
            for e, d in self._enemies:
                if getattr(e, "addr", None) == key:
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
            e = next((e for e, _ in self._enemies if getattr(e, "addr", None) == key), None)
            if e: self._track("unit", e.unit_id, addr=key, force=True)
            self._open_drops()
        elif kind in ("chest", "recipe"):
            c = next((c for c in self._chests if c.chest_id == key), None)
            if c:
                cx, cy, cz = c.x, c.y, c.z
                label = names.chest_label(c.chest_id, c.loot_table)
                self._track(kind, f"{cx:.1f},{cy:.1f},{cz:.1f}|{label}", force=True)
            self._open_drops()
        elif kind == "comp":
            e = next((e for e, _ in self._comps if getattr(e, "addr", None) == key), None)
            if e: self._track("unit", e.unit_id, addr=key, force=True)
        elif kind == "hero":
            e = next((e for e, _ in self._group_members if getattr(e, "addr", None) == key), None)
            if e: self._track("hero", e.unit_id, addr=key, force=True)
        elif kind == "orb":
            self._track("orb", key, force=True)
        elif kind == "gather":
            g_data = next((g for g, _ in self._gatherables if g[5] == key), None)
            if g_data:
                self._track("gather", f"{g_data[0]:.1f},{g_data[1]:.1f},{g_data[2]:.1f}|{g_data[4]}", force=True)

        self._refresh()

    def _select(self, kind, key):
        if self._sel == (kind, key):
            # Re-click the same row → deselect and close drop table
            self._sel = None
            self.close_drops()
            if self._tracker is not None:
                self._tracker.clear()
        else:
            self._sel = (kind, key)
            if self._tracker is not None:
                if kind == "enemy":
                    e = next((e for e, _ in self._enemies if getattr(e, "addr", None) == key), None)
                    if e: self._track("unit", e.unit_id, addr=key, force=True)
                elif kind == "hero":
                    e = next((e for e, _ in self._group_members if getattr(e, "addr", None) == key), None)
                    if e: self._track("hero", e.unit_id, addr=key, force=True)
                elif kind == "comp":
                    e = next((e for e, _ in self._comps if getattr(e, "addr", None) == key), None)
                    if e: self._track("unit", e.unit_id, addr=key, force=True)
                elif kind == "orb":
                    self._track("orb", key, force=True)
                elif kind == "gather":
                    g_data = next((g[0] for g in self._gatherables if g[0][5] == key), None)
                    if g_data:
                        self._track("gather", f"{g_data[0]:.1f},{g_data[1]:.1f},{g_data[2]:.1f}|{g_data[4]}", force=True)
                elif kind in ("chest", "recipe"):
                    c = next((c for c in self._chests if c.chest_id == key), None)
                    if c:
                        cx, cy, cz = c.x, c.y, c.z
                        label = names.chest_label(c.chest_id, c.loot_table)
                        self._track(kind, f"{cx:.1f},{cy:.1f},{cz:.1f}|{label}", force=True)

            if kind in ("enemy", "chest"):
                self._open_drops()
        self._refresh()

    def select_by_key(self, kind, key, open_drops=True) -> bool:
        """Select an item from the HUD list by kind and key (from external clicks)."""
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
            traceback.print_exc()
            self.pos.setText(f"read error: {e}")

    def _refresh(self):
        xyz = self.model.player_xyz()
        if xyz is None:
            self.pos.setText("waiting for player…")
            return

        # Clear all lists while inside a dungeon/rift — overworld enemies, chests
        # and orbs are irrelevant there and stale data would be confusing.
        try:
            if self.model.is_in_dungeon() or self.model.is_in_rift():
                self._enemies = []
                self._group_members = []
                self._comps = []
                self._orbs = []
                self._gatherables = []
                self._chests = []
                self.pos.setText(f"X {xyz[0]:.0f}   Y {xyz[1]:.0f}   Z {xyz[2]:.0f}")
                return
        except Exception:
            pass

        self.pos.setText(f"X {xyz[0]:.0f}   Y {xyz[1]:.0f}   Z {xyz[2]:.0f}")
        isz = round(self.s.icon_size * self._scale)
        profile = self.model.player_profile()

        # Logic:
        # ON (limit_by_zone=True) -> 300m range limit + same zone filtering.
        # OFF (limit_by_zone=False) -> Distance limit from slider (no zone filtering).
        player_zone = geo_zones.resolve_zone(xyz[0], xyz[1], xyz[2]) if self.s.limit_by_zone else None
        eff_max_dist = 300.0 if self.s.limit_by_zone else self.s.max_dist

        # Companion range: False = 250m, Number = that range, True = eff_max_dist (fallback)
        c_debug = getattr(self.s, "show_companions_debug", False)
        if isinstance(c_debug, (int, float)) and not isinstance(c_debug, bool):
            comp_max_dist = float(c_debug)
        else:
            comp_max_dist = 250.0 if c_debug is False else eff_max_dist

        # 1) gather every section's list (selection cycles across all of them)
        # Using 2D distance for filtering ensures consistency with the Minimap's top-down view
        self._enemies = self.model.nearest_enemies(
            xyz, self.s.enemy_count, eff_max_dist, player_zone=player_zone, use_2d=True,
            hide_units=set(self.s.get_entity_hidden_units(profile))) \
            if self.s.show_enemies else []
        if self._enemies:
            self._enemies = [x for x in self._enemies if x[0].unit_id != "CannonPlant"]
        self._group_members = self.model.nearest_group_members(
            xyz, self.s.enemy_count, eff_max_dist, player_zone=player_zone, use_2d=True) \
            if getattr(self.s, "show_group_members", False) else []
        self._comps = self.model.nearest_companions(
            xyz, self.s.companion_count, comp_max_dist, player_zone=player_zone, use_2d=True,
            hide_units=set(self.s.get_companion_hidden_units(profile))) \
            if self.s.show_companions else []
        self._orbs = self._ranked_orbs(xyz, profile, player_zone=player_zone, max_dist=eff_max_dist) if self.s.show_orbs else []
        
        if getattr(self.s, "show_gatherables", True):
            from ...geo import gatherables as geo_gatherables
            raw_g = []
            
            # 1. Live memory elements first (Priority: accurate and currently present)
            for g in self.model.gatherables(player_zone=player_zone, max_dist=eff_max_dist, use_2d=True):
                eid = g.elem_id or ""
                eid_l = eid.lower()
                # Filter out TRULY generic internal names, but keep anything resource-like
                is_resource = any(s in eid_l for s in ("ore", "flower", "madrigold", "zealotus", "lavendula", "thyme", "tungstene", "tin", "copper", "r2plant"))
                is_internal = any(s in eid_l for s in ("spawn", "trigger", "point", "marker", "area", "volume", "target", "bumper", "idle"))
                if is_internal and not is_resource:
                    continue
                dg = g.dist2d(xyz[0], xyz[1])
                raw_g.append(((g.x, g.y, g.z, "gather", eid, f"gl{g.addr}"), dg))

            # 2. Static nodes from JSON (Fallback: shows potential spawns not yet in memory)
            for g in geo_gatherables.load_nodes():
                d = math.hypot(g.x - xyz[0], g.y - xyz[1])
                if d <= eff_max_dist:
                    # Avoid duplicates with live memory nodes
                    if any(math.hypot(g.x - ex, g.y - ey) < 2.0 for (ex, ey, ez, ek, el, ei), ed in raw_g):
                        continue
                    raw_g.append(((g.x, g.y, g.z, "gather", g.name, f"g{g.x}{g.y}"), d))

            raw_g.sort(key=lambda t: t[1])
            self._gatherables = raw_g[:getattr(self.s, "gatherable_count", 5)]
        else:
            self._gatherables = []

        # Force tracked unit/hero/gather into the list so it doesn't auto-flip when leaving range
        if self._tracker is not None:
            tk, tid, taddr = self.s.track_kind, self.s.track_id, self._tracker.locked_addr
            if tk == "unit" and tid and taddr:
                if not any(getattr(e, "addr", None) == taddr for e, _ in self._enemies) and \
                   not any(getattr(e, "addr", None) == taddr for e, _ in self._comps):
                    # Find the entity by address directly
                    ent = next((e for e in self.model.units() if e.addr == taddr), None)
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
            elif tk == "gather" and tid:
                try:
                    coords_str, _, label = tid.partition("|")
                    tx, ty, tz = (float(v) for v in coords_str.split(","))
                    if not any(math.hypot(tx - g[0], ty - g[1]) < 0.5 for g, _ in self._gatherables):
                        dg = math.hypot(tx - xyz[0], ty - xyz[1])
                        self._gatherables.append(((tx, ty, tz, "gather", label, f"g{tx}{ty}"), dg))
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
            xyz, self.s.chest_count, eff_max_dist, player_zone=player_zone, use_2d=True) if self.s.show_chests else []
            
        if getattr(self.s, "entity_hide_collected", True):
            self._chests = [c for c in raw_chests if c.chest_id not in done_list]
        else:
            self._chests = raw_chests

        if self.s.track_kind in ("chest", "recipe") and self.s.track_id:
            try:
                from ...core.chest_resolver import ChestRow
                coords_str, _, label = self.s.track_id.partition("|")
                tx, ty, tz = (float(v) for v in coords_str.split(","))
                chest_id = label or "chest"
                matching_c = next((c for c in self.model.chests if c.chest_id == chest_id), None)
                if not matching_c:
                    matching_c = next((c for c in self.model.chests if math.hypot(tx - c.x, ty - c.y) < 1.0), None)
                raw_id = matching_c.chest_id if matching_c else chest_id
                
                if raw_id not in done_list:
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

        # 2) keep selection valid (auto-flip to first available target)
        targets = self._targets()
        if self._sel is not None and self._sel not in targets:
            old_sel = self._sel
            self._sel = None
            
            # ONLY auto-select the next one if the toggle is actually ON in settings
            if getattr(self.s, "auto_select_next_collectible", False):
                old_kind = old_sel[0]
                if old_kind in ("orb", "chest", "recipe"):
                    # Prioritize finding the exact same kind first
                    next_coll = next((t for t in targets if t[0] == old_kind), None)
                    
                    # Fallback to similar types only if the current category is empty
                    if not next_coll and old_kind in ("chest", "recipe"):
                        other_kind = "recipe" if old_kind == "chest" else "chest"
                        next_coll = next((t for t in targets if t[0] == other_kind), None)
                        if next_coll: # Update kind for the tracker call below
                            old_kind = next_coll[0]

                    if next_coll:
                        limit = eff_max_dist if eff_max_dist > 0 else 500.0
                        if next_coll[0] == "orb":
                            o = next((orb for orb, _dist in self._orbs if orb.orb_id == next_coll[1]), None)
                            dist = o.dist2d(xyz[0], xyz[1]) if o else 9999.0
                            if dist <= limit:
                                self._sel = next_coll
                                self._track("orb", next_coll[1], force=True)
                        elif next_coll[0] in ("chest", "recipe"):
                            c = next((ch for ch in self._chests if ch.chest_id == next_coll[1]), None)
                            if c and c.dist <= limit:
                                self._sel = next_coll
                                label = names.chest_label(c.chest_id, c.loot_table)
                                self._track(next_coll[0], f"{c.x:.1f},{c.y:.1f},{c.z:.1f}|{label}", force=True)

            # If the selected target is gone and no auto-replacement was found, clear the compass
            if self._sel is None and self._tracker is not None:
                self._tracker.clear()

        # 3) fallback: clear tracker if the target is now "done" (handles minimap clicks without HUD selection)
        if self._tracker is not None and self.s.track_kind in ("orb", "chest", "recipe", "gather", "pos"):
            tk, tid = self.s.track_kind, self.s.track_id
            target_id = tid.partition("|")[2] if tk in ("chest", "recipe", "gather", "pos") else tid
            if tk in ("chest", "recipe", "gather", "pos") and not target_id:
                try: # resolve from coords
                    coords, _, label = tid.partition("|")
                    tx, ty, _ = (float(v) for v in coords.split(","))
                    if tk in ("chest", "recipe"):
                        matching_c = next((c for c in self.model.chests if math.hypot(tx - c.x, ty - c.y) < 1.0), None)
                        target_id = matching_c.chest_id if matching_c else label
                    else: target_id = label
                except: target_id = label
            if target_id and self.s.is_done(target_id, profile):
                self._tracker.clear()

        # 3) render enemies
        if self.s.show_enemies:
            self.enemy_box.show()
            specs = []
            for e, d in self._enemies:
                addr = getattr(e, "addr", None)
                key = ("enemy", addr)
                # Only uniquely selectable when we have a valid address
                sel = addr is not None and key == self._sel
                # Badge follows the selected row — not the tracker's closest-mob lock
                tracked = sel and self._is_tracked("unit", e.unit_id)
                col = self.s.hud_accent if sel else theme.KIND_COLOR.get(e.kind, theme.TEXT)
                specs.append(RowSpec(
                    "Units", e.unit_id, col, names.unit_name(e.unit_id) or "?", col,
                    sub="◈ TRACKING" if tracked else "",
                    value=f"{d:>6.0f}m", bold=sel, highlight=sel or tracked, outlined=True,
                    cb=(lambda k=key: self._select(*k)) if addr is not None else None))
            self.enemy_box.fill(specs, isz)
            tag = ""
            if not getattr(self.model, "units_ok", True):
                tag = "READ FAILED · RETRYING"
            self.enemy_box.header.set_tag(tag)
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
                col_hex = {
                    "warrior": "#f1a02b",
                    "rogue": "#4ade80",
                    "mage": "#38bdf8",
                    "priest": "#eac331",
                }.get(hero_class, theme.TEXT)
                col = self.s.hud_accent if sel else col_hex
                specs.append(RowSpec(
                    None, None, col, self.model.hero_display_name(e), col,
                    sub="◈ TRACKING" if tracked else "",
                    value=f"{d:>6.0f}m", bold=sel, highlight=sel or tracked, outlined=True,
                    ui_icon="player",
                    cb=(lambda k=key: self._select(*k)) if addr is not None else None))
            self.group_box.fill(specs, isz)
            tag = ""
            if not getattr(self.model, "units_ok", True):
                tag = "READ FAILED · RETRYING"
            self.group_box.header.set_tag(tag)
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
                col = theme.GOLD if (missing or is_spark) else theme.GOOD
                sub_bits = [b for b in (
                    "★ SPARK" if is_spark else "",
                    "★ NOT COLLECTED" if missing else "",
                    "◈ TRACKING" if tracked else "") if b]
                if sel:
                    sub_bits.append(unit_id)
                specs.append(RowSpec(
                    "Units", unit_id, col,
                    display_name, col,
                    sub="  ·  ".join(sub_bits),
                    value=f"{d:>6.0f}m", bold=missing or sel or is_spark,
                    highlight=missing or tracked or sel, outlined=True,
                    gold_border=is_spark,
                    cb=(lambda k=("comp", addr): self._select(*k)) if addr is not None else None))
            self.comp_box.fill(specs, isz)
            tag = "WILD"
            if not getattr(self.model, "units_ok", True):
                tag = "READ FAILED · RETRYING"
            self.comp_box.header.set_tag(tag)
        else:
            self.comp_box.hide()

        # 4.5) render gatherables (Ores/Plants)
        if getattr(self.s, "show_gatherables", True):
            self.gather_box.show()
            specs = []
            for g_data, d in self._gatherables:
                label = g_data[4]
                key = ("gather", g_data[5])
                sel = self._sel == key
                tracked = self._is_tracked("pos", f"{g_data[0]:.1f},{g_data[1]:.1f},{g_data[2]:.1f}|{label}") or \
                          self._is_tracked("gather", f"{g_data[0]:.1f},{g_data[1]:.1f},{g_data[2]:.1f}|{label}") or \
                          self.s.track_kind == "gather" and self.s.track_id.startswith(f"{g_data[0]:.1f},{g_data[1]:.1f}")
                
                name_l = label.lower()
                is_ore = "ore" in name_l or "tungstene" in name_l or "tin" in name_l or "copper" in name_l
                accent = theme.KIND_COLOR["ore" if is_ore else "flower"]
                col = self.s.hud_accent if (sel or tracked) else accent
                
                # Auto-scale logic match (for sub-text or visual emphasis)
                is_large = "_large" in name_l or "_big" in name_l or "tungstene" in name_l
                is_small = "_small" in name_l
                
                # Humanize the display name (remove technical suffixes)
                # Use shared functions from gatherables.py
                from ...geo import gatherables as geo_gatherables
                display_name = geo_gatherables.get_display_name(label)
                marker = geo_gatherables.get_icon_name(label)
                setting_attr = geo_gatherables.get_setting_attr(label)
                
                # Check if this gatherable type is enabled in settings
                if setting_attr and not getattr(self.s, setting_attr, True):
                    continue
                
                # Fallback to generic flower/ore if icon not found
                if not icons.asset_icon(marker, 16):
                    marker = "ore" if is_ore else "flower"
                
                specs.append(RowSpec(
                    None, None, accent, display_name, col,
                    sub="◈ TRACKING" if tracked else ("★ LARGE" if is_large else "SMALL" if is_small else ""),
                    value=f"{d:>6.0f}m", bold=sel or tracked or is_large,
                    highlight=sel or tracked,
                    cb=(lambda k=key: self._select(*k)),
                    marker=marker))
            self.gather_box.fill(specs, isz)
            self.gather_box.header.set_tag("")
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
                col = self.s.hud_accent if (tracked or sel) else orb_col
                specs.append(RowSpec(
                    None, None, orb_col, geo_orbs.orb_label(o.orb_id), col,
                    sub="◈ TRACKING" if tracked else "",
                    value=f"{d:>6.0f}m", bold=tracked or sel,
                    highlight=tracked or sel,
                    cb=(lambda oid=o.orb_id: self._select("orb", oid)),
                    marker="orb2" if done else "orb"))
            self.orb_box.fill(specs, isz)
            self.orb_box.header.set_tag(
                "" if self._orbs else "ALL MARKED")
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
                anomaly = bool(c.live and c.anomaly)
                color = self.s.hud_accent if (sel or anomaly) else theme.CHEST
                
                done = c.chest_id in done_list
                label = names.chest_label(c.chest_id, c.loot_table)

                cx, cy, cz = c.x, c.y, c.z
                # check if this specific chest is the tracked target
                is_tracked = self._is_tracked(kind, f"{cx:.1f},{cy:.1f},{cz:.1f}|{label}") or \
                             self._is_tracked("pos", f"{cx:.1f},{cy:.1f},{cz:.1f}|{label}")

                sub_bits = []
                sub_bits.append((c.state or ("OPENED" if done else "CLOSED")).upper())
                if is_tracked:
                    sub_bits.append("◈ TRACKING")
                if anomaly:
                    sub_bits.append("★ ANOMALY")
                accent = theme.CHEST

                marker = "Recipe" if is_recipe else "chest"
                if done:
                    marker += "2"

                specs.append(RowSpec(
                    None, None, accent, label, color,
                    sub="  ·  ".join(sub_bits), value=f"{c.dist:>6.0f}m",
                    bold=sel, highlight=sel,
                    cb=(lambda k=(kind, c.chest_id): self._select(*k)),
                    marker=marker))
            self.chest_box.fill(specs, isz)
            self.chest_box.header.set_tag("")
        else:
            self.chest_box.hide()

        # 7) keep the open drop window synced with live selection / auto-flip
        self._update_drops()

    def closeEvent(self, e):
        self._timer.stop()
        if self._drop_win is not None:
            self._drop_win.close()
            self._drop_win = None
        super().closeEvent(e)