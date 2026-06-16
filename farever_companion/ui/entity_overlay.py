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

from collections import defaultdict
from dataclasses import dataclass

from PySide6 import QtCore, QtGui, QtWidgets

from . import theme
from .overlay_base import OverlayWindow
from .components import SectionHeader, IconTile
from .widgets import fmt_pct
from ..data import names, classes, tokens, icons
from ..geo import orbs as geo_orbs

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


class _EntityRow(QtWidgets.QFrame):
    """IconTile + name (+ optional mono sub-label) + right value. Pooled."""
    clicked = QtCore.Signal()

    def __init__(self, icon_size: int):
        super().__init__()
        self._highlight = ""
        self._cb = None
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(6, 3, 6, 3)
        lay.setSpacing(9)
        self.tile = IconTile(icon_size)
        mid = QtWidgets.QVBoxLayout()
        mid.setContentsMargins(0, 0, 0, 0)
        mid.setSpacing(0)
        self.name = QtWidgets.QLabel()
        self.sub = QtWidgets.QLabel()
        self.sub.setObjectName("Mono")
        mid.addWidget(self.name)
        mid.addWidget(self.sub)
        self.value = QtWidgets.QLabel()
        self.value.setObjectName("Mono")
        self.value.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        self.value.setMinimumWidth(56)            # reserve room so % never clips
        self.name.setSizePolicy(QtWidgets.QSizePolicy.Ignored,
                                QtWidgets.QSizePolicy.Preferred)  # name yields, value wins
        lay.addWidget(self.tile)
        lay.addLayout(mid, 1)
        lay.addWidget(self.value, 0)

    def apply(self, spec: RowSpec, icon_size: int):
        self.tile.set_size(icon_size)
        if spec.marker:
            self.tile.set_marker(spec.marker, spec.accent)
        else:
            self.tile.set(spec.sheet, spec.id_, spec.accent)
        weight = "700" if spec.bold else "500"
        self.name.setText(spec.name)
        self.name.setStyleSheet(
            f"color:{spec.name_color};font-weight:{weight};background:transparent;")
        self.sub.setText(spec.sub)
        self.sub.setVisible(bool(spec.sub))
        if spec.sub:
            self.sub.setStyleSheet(f"color:{theme.MUTED};background:transparent;")
        self.value.setText(spec.value)
        self.value.setStyleSheet(f"color:{spec.name_color};background:transparent;")
        self._highlight = spec.accent if spec.highlight else ""
        self.set_callback(spec.cb)
        self.update()

    def set_callback(self, cb):
        if self._cb is not None:
            self.clicked.disconnect(self._cb)
            self._cb = None
        if cb is not None:
            self._cb = cb
            self.clicked.connect(cb)
        self.setCursor(QtCore.Qt.PointingHandCursor if cb else QtCore.Qt.ArrowCursor)

    def paintEvent(self, e):
        if self._highlight:
            p = QtGui.QPainter(self)
            p.setRenderHint(QtGui.QPainter.Antialiasing, False)
            c = QtGui.QColor(self._highlight)
            bg = QtGui.QColor(c); bg.setAlpha(30)
            p.fillRect(self.rect(), bg)
            p.fillRect(0, 0, 2, self.height(), c)     # accent left bar
            p.end()
        super().paintEvent(e)

    def mousePressEvent(self, e):
        if e.button() == QtCore.Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(e)


class _Section(QtWidgets.QWidget):
    def __init__(self, title, color):
        super().__init__()
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 5, 0, 5)
        lay.setSpacing(2)
        self.header = SectionHeader(title, color, colored_label=True)
        lay.addWidget(self.header)
        self.rows = QtWidgets.QVBoxLayout()
        self.rows.setSpacing(1)
        lay.addLayout(self.rows)
        self._pool: list[_EntityRow] = []

    def fill(self, specs: list[RowSpec], icon_size):
        while len(self._pool) < len(specs):
            r = _EntityRow(icon_size)
            self._pool.append(r)
            self.rows.addWidget(r)
        for i, spec in enumerate(specs):
            r = self._pool[i]
            r.apply(spec, icon_size)
            r.show()
        for j in range(len(specs), len(self._pool)):
            self._pool[j].hide()


def _scroll_body():
    scroll = QtWidgets.QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
    scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
    scroll.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
    scroll.verticalScrollBar().setStyleSheet("margin:0;")
    body = QtWidgets.QWidget()
    lay = QtWidgets.QVBoxLayout(body)
    lay.setContentsMargins(0, 0, 13, 0)   # clear the scrollbar gutter
    lay.setSpacing(0)
    scroll.setWidget(body)
    return scroll, lay


# ---------------------------------------------------------------------------
#  Drop Table - its own closable window for the selected target's loot
# ---------------------------------------------------------------------------
class DropTableOverlay(OverlayWindow):
    def __init__(self, model, settings, parent=None):
        super().__init__("DROP TABLE", settings, geo_key="droptable", parent=parent)
        self.model = model
        self.s = settings
        self._collapsed = set()  # all rarities expanded by default
        self._near = None        # the loot source (enemy or chest) being shown
        self._name = ""
        scroll, self._body = _scroll_body()
        self.box = _Section("DROPS", self.s.hud_accent or theme.ACCENT)
        self._body.addWidget(self.box)
        self._body.addStretch(1)
        self.content.addWidget(scroll, 1)
        # Wider than the entity HUD: drop rows carry long item names AND a long
        # right-side value ("Σ 0.001% · 1 · ★1"), plus the header shows the full
        # target name + "Nm · Ln" tag - at 320 the right side clipped.
        self._base_w, self._base_h = 392, 480
        self.setMinimumWidth(360)
        self.resize(392, 480)

    def set_target(self, near, name: str):
        """`near` is a model.Nearest (enemy or chest) or None; `name` is the
        readable target label."""
        self._near = near
        self._name = name or "?"
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
        self.titlebar.title.setText(f"DROP TABLE · {self._name}")
        self.box.header.set_text(self._name)
        if near is None:
            self.box.header.set_tag("NO TABLE")
            self.box.fill([], self.s.icon_size)
            return
        self.box.header.set_tag(f"{near.dist:.0f}m · L{near.level}")
        active = self._active_class()
        drops = self.model.drop_table_effective(near)
        groups = defaultdict(list)
        for item, prob, rar, _typ in drops:
            groups[rar or "Common"].append((item, prob))

        def rel(it):
            return classes.is_for_class(it, active)

        specs: list[RowSpec] = []
        first_item = True
        for rarity in sorted(groups, key=lambda r: _RARITY_ORDER.get(r, 9)):
            items = groups[rarity]
            total = sum(p for _, p in items)
            n_cls = sum(1 for it, _ in items if rel(it))
            col = theme.rarity_color(rarity)
            collapsed = rarity in self._collapsed
            caret = "▸" if collapsed else "▾"
            tag = f"  ·  ★{n_cls}" if n_cls else ""
            specs.append(RowSpec(
                None, None, col, f"{caret} {rarity.upper()}", col,
                value=f"Σ {fmt_pct(total)} · {len(items)}{tag}", bold=True,
                cb=(lambda r=rarity: self._toggle(r))))
            if collapsed:
                continue
            ordered = sorted(items, key=lambda t: (not rel(t[0]), -t[1]))
            if self.s.class_only and active:
                ordered = [t for t in ordered if rel(t[0])]
            for item, prob in ordered:
                if tokens.is_token(item):
                    specs.append(RowSpec("item", item, col,
                                         f"🎲 {names.item_name(item)}", theme.MUTED,
                                         sub="PROCEDURAL", value=fmt_pct(prob)))
                    first_item = False
                    continue
                star = rel(item)
                specs.append(RowSpec(
                    "item", item, col,
                    f"{'★ ' if star else ''}{names.item_name(item)}", col,
                    sub=(rarity.upper() if first_item else ""),
                    value=fmt_pct(prob), bold=star, highlight=first_item))
                first_item = False
        self.box.fill(specs, round(self.s.icon_size * self._scale))


# ---------------------------------------------------------------------------
#  Entity HUD - nearby enemies (selectable) + chests
# ---------------------------------------------------------------------------
class EntityOverlay(OverlayWindow):
    request_config = QtCore.Signal()

    def __init__(self, model, settings, parent=None):
        super().__init__("FAREVER · ENTITY", settings, geo_key="entity", parent=parent)
        self.model = model
        self.s = settings
        self._enemies: list = []          # [(entity, dist)]
        self._comps: list = []            # [(entity, dist)] wild companions
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
        self.comp_box = _Section("COMPANIONS", theme.GOOD)
        self.orb_box = _Section("SECRET ORBS", theme.KIND_COLOR["orb"])
        self.chest_box = _Section("CHESTS", theme.CHEST)
        for b in (self.enemy_box, self.comp_box, self.orb_box, self.chest_box):
            self._body.addWidget(b)
        self._body.addStretch(1)

        gear = QtWidgets.QPushButton(); gear.setObjectName("Icon")
        gear.setIcon(icons.ui_qicon("settings", theme.MUTED, 16))
        gear.setToolTip("Open the config panel")
        gear.clicked.connect(self.request_config.emit)
        layb = QtWidgets.QPushButton(); layb.setObjectName("Icon")
        layb.setIcon(icons.ui_qicon("layout", theme.MUTED, 16))
        layb.setToolTip("Reset overlay to top-left")
        layb.clicked.connect(self._reset_pos)
        for b in (gear, layb):
            self.titlebar.extra.insertWidget(self.titlebar.extra.count() - 1, b)

        self.enable_resize_grip()
        self._base_w, self._base_h = 320, 440
        self.setMinimumWidth(round(280 * self.s.entity_scale))
        self.apply_scale(self.s.entity_scale)     # scaled QSS + resize to base*scale
        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(POLL_MS)

    def _reset_pos(self):
        self.move(60, 60)
        self.persist_geometry()

    # --- lock + opacity forward to the drop child -----------------------
    def set_locked(self, on: bool) -> None:
        super().set_locked(on)
        if self._drop_win is not None:
            self._drop_win.set_locked(on)

    def set_opacity(self, value: float) -> None:
        super().set_opacity(value)
        if self._drop_win is not None:
            self._drop_win.set_opacity(value)

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
        t += [("comp", getattr(e, "addr", None)) for e, _ in self._comps
              if getattr(e, "addr", None) is not None]
        t += [("orb", o.orb_id) for o, _ in self._orbs]
        t += [("chest", c.chest_id) for c in self._chests]
        return t

    def _selected_source(self):
        """(near, name) for the current selection, or (None, ''). Companions
        and orbs aren't loot sources - they drive the compass instead."""
        if self._sel is None:
            return None, ""
        kind, key = self._sel
        if kind in ("comp", "orb"):
            return None, ""
        if kind == "enemy":
            for e, d in self._enemies:
                if getattr(e, "addr", None) == key:
                    return (self.model.enemy_drop_source(e, d, self.s.level),
                            names.unit_name(e.unit_id) or "?")
        else:
            for c in self._chests:
                if c.chest_id == key:
                    name = (names.loot_table_label(c.loot_table) if c.loot_table
                            else names.humanize(c.chest_id))
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
        if kind in ("enemy", "chest"):
            self._open_drops()              # loot sources -> drop table
        elif kind == "comp":
            e = next((e for e, _ in self._comps
                      if getattr(e, "addr", None) == key), None)
            if e is not None and self._tracker is not None:
                self._tracker.track("unit", e.unit_id)
        elif kind == "orb" and self._tracker is not None:
            self._tracker.track("orb", key)  # collectibles -> compass needle
        self._refresh()

    def _select(self, kind, key):
        self._sel = (kind, key)
        if kind in ("enemy", "chest"):
            self._open_drops()
        elif kind == "comp" and self._tracker is not None:
            e = next((e for e, _ in self._comps
                      if getattr(e, "addr", None) == key), None)
            if e is not None:
                self._tracker.track("unit", e.unit_id)
        elif kind == "orb" and self._tracker is not None:
            self._tracker.track("orb", key)
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

    def _track(self, kind: str, key: str | None) -> None:
        if self._tracker is not None and key:
            self._tracker.toggle(kind, key)

    def _is_tracked(self, kind: str, key: str | None) -> bool:
        return self._tracker is not None and key is not None \
            and self._tracker.is_tracked(kind, key)

    def _is_tracked_instance(self, unit_id: str | None, addr) -> bool:
        """Only the live instance the needle is locked on shows TRACKING -
        not every unit of the tracked type."""
        if not self._is_tracked("unit", unit_id):
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
            if kind not in ("enemy", "chest"):
                self.close_drops()
                return
            self._drop_win.set_target(*self._selected_source())

    # --- helpers ---------------------------------------------------------
    def _ranked_orbs(self, xyz, profile):
        """Nearest uncollected orbs, plain distance order."""
        pool = [o for o in geo_orbs.load_orbs() if not self.s.is_done(o.orb_id, profile)]
        return sorted(((o, o.dist(*xyz)) for o in pool),
                      key=lambda t: t[1])[:self.s.orb_count]

    def _tick(self):
        try:
            self._refresh()
        except Exception as e:
            self.pos.setText(f"read error: {e}")

    def _refresh(self):
        xyz = self.model.player_xyz()
        if xyz is None:
            self.pos.setText("waiting for player…")
            return
        self.pos.setText(f"X {xyz[0]:.0f}   Y {xyz[1]:.0f}   Z {xyz[2]:.0f}")
        isz = round(self.s.icon_size * self._scale)
        profile = self.model.player_profile()

        # 1) gather every section's list (selection cycles across all of them)
        self._enemies = self.model.nearest_enemies(
            xyz, self.s.enemy_count, self.s.max_dist, self.s.enemies_only,
            hide_types=set(self.s.entity_hidden_types),
            hide_units=set(self.s.entity_hidden_units)) \
            if self.s.show_enemies else []
        self._comps = self.model.nearest_companions(xyz, self.s.companion_count) \
            if self.s.show_companions else []
        self._orbs = self._ranked_orbs(xyz, profile) if self.s.show_orbs else []
        # Automatically mark opened chests as done in settings so they persist
        done_list = self.s.get_poi_done(profile)
        chest_changed = False
        for e in self.model.live_chests():
            if e.elem_id and e.state and e.state.lower() in ("opened", "open", "looted"):
                if e.elem_id not in done_list:
                    done_list.append(e.elem_id)
                    chest_changed = True
        if chest_changed:
            if profile:
                self.s.save_profile_progress(profile, done_list)
            else:
                self.s.save()

        raw_chests = self.model.nearest_chests_merged(
            xyz, self.s.chest_count, self.s.max_dist) if self.s.show_chests else []
        self._chests = [c for c in raw_chests if c.chest_id not in done_list]

        # 2) keep selection valid (auto-flip to first available target)
        targets = self._targets()
        if self._sel not in targets:
            self._sel = targets[0] if targets else None

        # 3) render enemies
        if self.s.show_enemies:
            self.enemy_box.show()
            specs = []
            for e, d in self._enemies:
                key = ("enemy", getattr(e, "addr", None))
                sel = key == self._sel
                tracked = self._is_tracked_instance(e.unit_id, getattr(e, "addr", None))
                col = self.s.hud_accent if sel else theme.KIND_COLOR.get(e.kind, theme.TEXT)
                specs.append(RowSpec(
                    "unit", e.unit_id, col, names.unit_name(e.unit_id) or "?", col,
                    sub="◈ TRACKING" if tracked else "",
                    value=f"{d:.0f}m", bold=sel, highlight=sel or tracked,
                    cb=(lambda k=key, uid=e.unit_id:
                        (self._select(*k), self._track("unit", uid)))))
            self.enemy_box.fill(specs, isz)
            # honest empty-state: a failed read (zone swap) is not "no enemies"
            if not getattr(self.model, "units_ok", True):
                tag = "READ FAILED · RETRYING"
            elif not self._enemies:
                tag = "0 · NONE LOADED"
            else:
                tag = f"{len(self._enemies)} · ↑↓ SELECT"
            self.enemy_box.header.set_tag(tag)
        else:
            self.enemy_box.hide()

        # 4) render wild companions (critters) - their own section, never
        # enemies, never distance-capped. Ones missing from the account
        # collection get flagged.
        if self.s.show_companions:
            self.comp_box.show()
            specs = []
            n_new = 0
            for e, d in self._comps:
                missing = self._owned is not None and e.unit_id not in self._owned
                n_new += missing
                addr = getattr(e, "addr", None)
                tracked = self._is_tracked_instance(e.unit_id, addr)
                sel = ("comp", addr) == self._sel
                col = theme.GOLD if missing else theme.GOOD
                sub_bits = [b for b in ("★ NOT COLLECTED" if missing else "",
                                        "◈ TRACKING" if tracked else "") if b]
                specs.append(RowSpec(
                    "unit", e.unit_id, col,
                    names.unit_name(e.unit_id) or names.humanize(e.unit_id), col,
                    sub="  ·  ".join(sub_bits),
                    value=f"{d:.0f}m", bold=missing or sel,
                    highlight=missing or tracked or sel,
                    cb=(lambda uid=e.unit_id: self._track("unit", uid))))
            self.comp_box.fill(specs, isz)
            if not getattr(self.model, "units_ok", True):
                tag = "READ FAILED · RETRYING"
            elif not self._comps:
                tag = "0 · NONE LOADED"
            else:
                tag = f"{len(self._comps)} · WILD"
                if n_new:
                    tag += f" · ★{n_new} NEW"
            self.comp_box.header.set_tag(tag)
        else:
            self.comp_box.hide()

        # 5) render the nearest uncollected secret orbs (click = compass needle)
        if self.s.show_orbs:
            self.orb_box.show()
            orb_col = theme.KIND_COLOR["orb"]
            specs = []
            for o, d in self._orbs:
                tracked = self._is_tracked("orb", o.orb_id)
                sel = ("orb", o.orb_id) == self._sel
                col = self.s.hud_accent if (tracked or sel) else orb_col
                specs.append(RowSpec(
                    None, None, orb_col, geo_orbs.orb_label(o.orb_id), col,
                    sub="◈ TRACKING" if tracked else geo_orbs.orb_region_name(o),
                    value=f"{d:.0f}m", bold=tracked or sel,
                    highlight=tracked or sel,
                    cb=(lambda oid=o.orb_id: self._track("orb", oid)),
                    marker="orb"))
            self.orb_box.fill(specs, isz)
            self.orb_box.header.set_tag(
                f"{len(self._orbs)}" if self._orbs else "ALL MARKED")
        else:
            self.orb_box.hide()

        # 6) render chests (also selectable -> show their loot table)
        if self.s.show_chests:
            self.chest_box.show()
            specs = []
            for c in self._chests:
                sel = ("chest", c.chest_id) == self._sel
                anomaly = bool(c.live and c.anomaly)
                color = self.s.hud_accent if (sel or anomaly) else theme.CHEST
                label = names.humanize(c.loot_table) if c.loot_table \
                    else names.humanize(c.chest_id)
                sub_bits = []
                if c.state:
                    sub_bits.append(c.state.upper())
                if anomaly:
                    sub_bits.append("★ ANOMALY")
                accent = theme.CHEST
                cx, cy, cz = 0.0, 0.0, 0.0
                sc = next((sc for sc in self.model.chests if sc.chest_id == c.chest_id), None)
                if sc:
                    cx, cy, cz = sc.x, sc.y, sc.z
                else:
                    lc = next((lc for lc in self.model.live_chests() if lc.elem_id == c.chest_id), None)
                    if lc:
                        cx, cy, cz = lc.x, lc.y, lc.z
                specs.append(RowSpec(
                    None, None, accent, label, color,
                    sub="  ·  ".join(sub_bits), value=f"{c.dist:.0f}m",
                    bold=sel, highlight=sel,
                    cb=(lambda cid=c.chest_id, lbl=label, x=cx, y=cy, z=cz:
                        (self._select("chest", cid),
                         self._track("pos", f"{x:.1f},{y:.1f},{z:.1f}|{lbl}"))),
                    marker="chest"))
            self.chest_box.fill(specs, isz)
            self.chest_box.header.set_tag(f"{len(self._chests)}")
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
