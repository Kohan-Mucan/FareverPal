"""Dungeon overlay — memory-scanner HUD for dungeons & rifts (no minimap).
Sections: FOOD / RIFT / BOSS / PLAYERS / ENEMIES / LOOT / SECRET ORBS
(labels hidden on RIFT & SECRET ORBS — content only).
"""
from __future__ import annotations

import math

from PySide6 import QtCore, QtGui, QtWidgets

from .. import theme
from ..overlay_base import OverlayWindow
from ...core import attributes
from ...core.proc import ProcError
from ...data import names, units as udata
from ...data.items import catalog
from .loot_rows import DungeonLootMixin
from .entity_rows import _Section, _scroll_body, RowSpec

POLL_MS = 350

# Shaarlize clones: FalseClone = decoy to AVOID, TrueClone = real boss
SHAARLIZE_TRUE_CLONE = "DemonSuperElite_Fairy_TrueClone"
SHAARLIZE_FALSE_CLONE = "DemonSuperElite_Fairy_FalseClone"
DG_REAL_CLONES = {SHAARLIZE_TRUE_CLONE}
DG_AVOID_CLONES = {SHAARLIZE_FALSE_CLONE}

_AVOID_COLOR = "#ff5252"
_MAX_FOOD_ROWS = 4


def _food_item_id(name: str | None) -> str | None:
    """Stable item id behind a placed-food display name ('Plainswalker Feast'
    -> 'Feast'); None when unknown or the name never resolved."""
    if not name:
        return None
    try:
        return catalog.resolve_food_info(name)[1]
    except Exception:
        return None


def _dg_is_elite(unit_id: str | None) -> bool:
    """Elite check with variant fallback: the codex flags elites on their
    canonical id ('…_E', uniques), but live spawns often carry extra variant
    tokens ('…_E_2', '…_Leg'). Drop trailing tokens until something matches —
    styling only, never used for filtering."""
    if not unit_id:
        return False
    if udata.is_elite(unit_id):
        return True
    parts = unit_id.split("_")
    while len(parts) > 1:
        parts.pop()
        if udata.is_elite("_".join(parts)):
            return True
    return False


class DungeonOverlay(DungeonLootMixin, OverlayWindow):
    def __init__(self, model, settings, parent=None):
        # First launch: seed geometry from the entity HUD.
        if settings is not None:
            geo = getattr(settings, "geometry", None)
            if isinstance(geo, dict) and not geo.get("dungeon"):
                ent_geo = geo.get("entity")
                if ent_geo:
                    geo["dungeon"] = ent_geo
        super().__init__("HUD", settings, geo_key="dungeon", parent=parent)
        self._page_key = "settings:HUD's"
        self.model = model
        self.s = settings
        self._sel = None
        self._tracker = None
        self._dg_xyz = None
        self._clear_dg_lists()

        scroll, self._body = _scroll_body()
        self._scroll = scroll
        self.content.addWidget(scroll, 1)
        self.dg_rift_box = _Section("RIFT", "#C084FC")
        self.dg_rift_box.header.hide()
        self.dg_boss_box = _Section("BOSS", theme.GOLD)
        self.dg_boss_box.header.hide()
        self.dg_player_box = _Section("PLAYERS", theme.MUTED)
        self.dg_enemy_box = _Section("ENEMIES", theme.DANGER)
        self.dg_enemy_box.header.hide()
        self.dg_loot_box = _Section("LOOT", "#4ADE80")
        self.dg_food_box = _Section("FOOD", "#FB923C")
        self.dg_orb_box = _Section("SECRET ORBS", theme.KIND_COLOR["orb"])
        self.dg_orb_box.header.hide()
        # FOOD pinned to the top — first thing you want to see on entry.
        for b in (self.dg_food_box, self.dg_rift_box, self.dg_boss_box,
                  self.dg_player_box, self.dg_enemy_box,
                  self.dg_loot_box, self.dg_orb_box):
            self._body.addWidget(b)
        self._bottom_spacer = QtWidgets.QSpacerItem(0, 6, QtWidgets.QSizePolicy.Minimum, QtWidgets.QSizePolicy.Fixed)
        self._body.addSpacerItem(self._bottom_spacer)
        self._body.addStretch(1)

        # Orb section show/hide (eye icon, accent when shown)
        self._orb_btn = QtWidgets.QPushButton()
        self._orb_btn.setObjectName("Icon")
        self._orb_btn.setFixedSize(22, 22)
        self._orb_btn.clicked.connect(
            lambda: self.set_show_orbs(not getattr(self.s, "dungeon_show_orbs", True)))
        self._update_orb_btn()
        self.titlebar.extra.insertWidget(self.titlebar.extra.count() - 3, self._orb_btn)

        # PLAYERS show/hide (people icon, accent when shown; hidden by default)
        self._players_btn = QtWidgets.QPushButton()
        self._players_btn.setObjectName("Icon")
        self._players_btn.setFixedSize(22, 22)
        self._players_btn.clicked.connect(
            lambda: self.set_show_players(not getattr(self.s, "dungeon_show_players", False)))
        self._update_players_btn()
        self.titlebar.extra.insertWidget(self.titlebar.extra.count() - 3, self._players_btn)

        # LOOT show/hide (box icon, accent when shown)
        self._loot_btn = QtWidgets.QPushButton()
        self._loot_btn.setObjectName("Icon")
        self._loot_btn.setFixedSize(22, 22)
        self._loot_btn.clicked.connect(lambda:
            self.set_show_loots(not getattr(self.s, "dungeon_show_loots", True)))
        self._update_loot_btn()
        self.titlebar.extra.insertWidget(self.titlebar.extra.count() - 3,
                                         self._loot_btn)

        self.enable_resize_grip()
        self._base_w, self._base_h = 330, 420
        self.setMinimumWidth(260)
        self.setMaximumWidth(700)
        self.apply_scale(15 / 14.0)
        self._last_h_config = None

        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(POLL_MS)

    # --- titlebar toggles ---------------------------------------------------
    def set_show_orbs(self, on: bool):
        self.s.dungeon_show_orbs = bool(on)
        self.s.save()
        self._update_orb_btn()
        self._tick()

    def set_show_players(self, on: bool):
        self.s.dungeon_show_players = bool(on)
        self.s.save()
        self._update_players_btn()
        self._tick()

    def _update_players_btn(self) -> None:
        from ...data import icons
        on = getattr(self.s, "dungeon_show_players", False)
        self._players_btn.setIcon(QtGui.QIcon(icons.ui_icon(
            "users", self.s.hud_accent if on else theme.MUTED, 18)))
        self._players_btn.setIconSize(QtCore.QSize(18, 18))
        self._players_btn.setToolTip("Show players" if on else "Hide players")

    def _update_orb_btn(self) -> None:
        from ...data import icons
        on = getattr(self.s, "dungeon_show_orbs", True)
        self._orb_btn.setIcon(QtGui.QIcon(icons.ui_icon(
            "eye" if on else "eye-off", self.s.hud_accent if on else theme.MUTED, 18)))
        self._orb_btn.setIconSize(QtCore.QSize(18, 18))
        self._orb_btn.setToolTip(
            "Hide secret orbs" if on else "Show secret orbs")

    # --- lifecycle / tracker ------------------------------------------------
    def reset_state(self) -> None:
        self._sel = None
        self._clear_dg_lists()

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

    def _tick(self):
        # Re-entrancy guard: tracker signals (changed) can fire synchronously
        # from inside _refresh (track/clear), re-entering this slot.
        if getattr(self, "_dg_in_tick", False):
            return
        self._dg_in_tick = True
        try:
            self._refresh()
        except Exception as e:
            import sys
            import traceback
            print(f"[DungeonOverlay Error] {e}", file=sys.stderr, flush=True)
            traceback.print_exc(file=sys.stderr)
            sys.stderr.flush()
        finally:
            self._dg_in_tick = False

    # --- state ---------------------------------------------------------------
    def _clear_dg_lists(self):
        self._dg_enemies = []      # [(Entity, dist, kind)] kind: boss/clone_real/avoid/None
        self._dg_players = []      # [(Entity, dist)] other heroes -> grouped by class
        self._dg_loot = []         # [LootDrop] live dropped loot
        self._dg_food = []         # [FoodStation] player-placed food
        self._dg_orbs = []         # [Element] live, not-yet-done secret orbs
        self._dg_death_counts = {}  # addr -> deaths this instance (players)
        self._dg_prev_alive = {}    # addr -> bool (alive last tick)
        self._dg_boss_prev = None   # boss HP last tick (fight-reset detection)

    def _dg_classify(self, unit_id: str | None) -> str | None:
        if not unit_id:
            return None
        if unit_id in DG_AVOID_CLONES:
            return "avoid"
        if unit_id in DG_REAL_CLONES:
            return "clone_real"
        if udata.is_boss(unit_id):
            return "boss"
        return None

    @staticmethod
    def _dg_display_name(unit_id: str) -> str:
        return names.unit_name(unit_id) or unit_id or "?"

    # --- gather --------------------------------------------------------------
    def _refresh(self):
        if self.model is None:
            return

        # Gate: the HUD only gathers while inside a dungeon/rift.
        try:
            in_dg = self.model.is_in_dungeon_or_rift()
        except Exception:
            in_dg = False
        if not in_dg:
            self._clear_dg_lists()
            # Leaving the instance: drop the dungeon needle target.
            # GUARD: tracker.clear() emits `changed`, which re-enters _tick ->
            # _refresh -> here. Only clear when a target is actually set, and
            # latch a re-entrancy flag so we can't loop (RecursionError).
            if self._tracker is not None and not getattr(self, "_dg_clearing", False):
                ts = getattr(self._tracker, "s", self.s)
                if getattr(ts, "track_kind", "") or getattr(ts, "track_id", ""):
                    self._dg_clearing = True
                    try:
                        self._tracker.clear()
                        self._dg_auto_uid = None
                        self._dg_auto_prev = None
                    finally:
                        self._dg_clearing = False
                else:
                    self._dg_auto_uid = None
                    self._dg_auto_prev = None
            self._render_dg_hidden()
            self._check_auto_height()
            return

        xyz = self.model.player_xyz()
        if xyz is None:
            return
        self._dg_xyz = xyz
        profile = self.model.player_profile()

        # --- ENEMIES -------------------------------------------------------
        # nearest_enemies already applies the pet filter (OFF_FOE_OWNER +
        # is_player_owned + is_codex_unit).
        # ⚠ GAP: skill-summoned fighting pets can leak through as "enemies" —
        # add a summon whitelist when we get live samples.
        max_dist = 0  # 0 = no distance cap; show everything the scanner sees
        pool = self.model.nearest_enemies(xyz, 150, max_dist, use_2d=True)

        # Rescue sweep: TrueClone-style bosses don't descend from ent.Foe and
        # skip is_enemy — re-scan raw units for anything boss/clone.
        try:
            seen_addrs = {e.addr for e, _ in pool}
            for u in self.model.units():
                if getattr(u, "addr", None) in seen_addrs:
                    continue
                if getattr(u, "is_hero", False) or getattr(u, "is_player_owned", False):
                    continue
                if self._dg_classify(u.unit_id) in ("boss", "clone_real"):
                    pool.append((u, u.dist2d(xyz[0], xyz[1])))
        except ProcError:
            pass

        enemies = []
        peaks = getattr(self, "_dg_hp_peaks", {})
        for e, d in pool:
            kind = self._dg_classify(e.unit_id)
            # No dead-filter (hp=0.0 when unreadable); track peak HP per unit
            # so render can show an HP % (no MaxHealth offset).
            hp = getattr(e, "hp", 0.0) or 0.0
            if e.addr and hp > 0:
                if hp > peaks.get(e.addr, 0.0):
                    peaks[e.addr] = hp
            enemies.append((e, d, kind))
        self._dg_hp_peaks = peaks

        # Bosses (incl. real clones) pinned to top, then by distance.
        enemies.sort(key=lambda t: (
            0 if t[2] in ("boss", "clone_real") else 1,
            {"boss": 0, "clone_real": 1, "avoid": 2}.get(t[2], 3),
            t[1],
        ))
        self._dg_enemies = enemies

        # --- PLAYERS (other ent.Hero units + me, grouped by class) ---------
        try:
            players = list(self.model.nearest_group_members(
                xyz, 10, 0, use_2d=True))
        except ProcError:
            players = []
        pa = self.model.player_addr
        if pa:
            me = next((e for e in self.model.units()
                       if e.is_hero and e.addr == pa), None)
            if me is not None:
                # distance 0 -> my class always sorts first
                players.insert(0, (me, 0.0))
        self._dg_players = players

        # --- PLAYER DEATHS ----------------------------------------------------
        # alive(>0) -> dead(<=0) transition per tracked player this instance.
        counts = getattr(self, "_dg_death_counts", {})
        prev = getattr(self, "_dg_prev_alive", {})
        for e, _d in players:
            if not getattr(e, "addr", None):
                continue
            try:
                hp = attributes.health(self.model.hl, e.addr)
            except ProcError:
                continue
            if hp is None:
                continue
            alive = hp > 0
            was = prev.get(e.addr)
            if was is True and not alive:
                counts[e.addr] = counts.get(e.addr, 0) + 1
            prev[e.addr] = alive
        self._dg_death_counts = counts
        self._dg_prev_alive = prev

        # --- FIGHT RESET (deaths are per-fight: zero on boss respawn/wipe) --
        if getattr(self.model, "units_ok", False):
            try:
                bid = self.model.dungeon_boss
                boss_u = next((e for e in self.model.units()
                               if e.unit_id == bid), None) if bid else None
                bhp = attributes.health(self.model.hl, boss_u.addr) \
                    if boss_u is not None else None
            except ProcError:
                bhp = None
            prev_bhp = getattr(self, "_dg_boss_prev", None)
            if bhp is not None and bhp > 0:
                if prev_bhp is not None and (
                        prev_bhp <= 0 or bhp > prev_bhp * 1.25):
                    # boss spawned fresh or healed back after a wipe
                    self._dg_death_counts = {}
                    self._dg_prev_alive = {}
                    prev = self._dg_prev_alive
            self._dg_boss_prev = bhp

        # --- LOOT (LootDrop -> st.Item; rifts drop loot too, scan everywhere)
        try:
            self._dg_loot = self.model.loot_drops()
        except ProcError:
            self._dg_loot = []

        # --- FOOD (WorldConsumable -> skill name; also present in rifts) ----
        try:
            self._dg_food = self.model.food_stations()
        except Exception:
            self._dg_food = []

        # --- ORBS (InstanceOrb; glowing = not collected, fx-gone = collected;
        # "no fx" alone never marks done unless seen glowing this session).
        # Rifts have no orbs: skip the scan entirely in there.
        try:
            in_rift = self.model.is_in_rift()
        except Exception:
            in_rift = False
        if in_rift:
            orbs = []
        else:
            try:
                from ...constants import OFF_ELEM_FX
                orbs = self.model.live_orbs(max_dist=0, use_2d=False)
            except ProcError:
                orbs = []
        profile_key = profile or "_global"
        seen = getattr(self, "_dg_orb_seen_fx", {})
        seen = {k: v for k, v in seen.items() if k[0] == profile_key}
        lastpos = getattr(self, "_dg_orb_lastpos", {})
        done_set = set(self.s.get_dungeon_orb_done(profile))
        kept = []
        current_ids = set()
        for o in orbs:
            eid = o.elem_id
            if not eid:
                continue
            current_ids.add(eid)
            lastpos[eid] = (o.x, o.y)
            try:
                has_fx = bool(self.model.hl.u64(o.addr + OFF_ELEM_FX))
            except Exception:
                has_fx = None   # unreadable -> no opinion this tick
            if has_fx:
                seen[(profile_key, eid)] = True
                done_set.discard(eid)       # glowing = not collected
            elif has_fx is False and seen.get((profile_key, eid)):
                done_set.add(eid)           # glowed before, gone now = collected
                if getattr(self.s, "dungeon_hide_collected", True):
                    continue
            elif eid in done_set:
                if getattr(self.s, "dungeon_hide_collected", True):
                    continue
            kept.append(o)

        # Vanish fallback: a seen orb gone from the scan near its last pos was
        # collected (covers orbs whose fx pointer never reads).
        pxyz = xyz
        for eid in [k for (_p, k) in seen if k not in current_ids]:
            pos = lastpos.get(eid)
            if pos and pxyz and math.hypot(pos[0] - pxyz[0], pos[1] - pxyz[1]) <= 50.0:
                done_set.add(eid)
                seen.pop((profile_key, eid), None)
                lastpos.pop(eid, None)
        self._dg_orb_seen_fx = seen
        self._dg_orb_lastpos = lastpos
        if done_set != set(self.s.get_dungeon_orb_done(profile)):
            for eid in set(self.s.get_dungeon_orb_done(profile)) - done_set:
                self.s.toggle_dungeon_orb_done(eid, profile)   # remove stale
        for eid in done_set - set(self.s.get_dungeon_orb_done(profile)):
            self.s.toggle_dungeon_orb_done(eid, profile)       # add new
        self._dg_orbs = kept

        # --- TRACKED MOB DIED -------------------------------------------------
        # Needle's locked instance died: clear now (HP<=0).
        # (Clone lifecycle and restore is handled in the auto-track block below).
        ts = getattr(self._tracker, "s", self.s) if self._tracker is not None else self.s
        if self._tracker is not None and getattr(ts, "track_kind", "") == "unit":
            auto_uid = getattr(self, "_dg_auto_uid", None)
            if not auto_uid or getattr(ts, "track_id", "") != auto_uid:
                la = self._tracker.locked_addr
                if la:
                    ent = next((e for e in self.model.units() if e.addr == la), None)
                    if ent is not None and (getattr(ent, "hp", 0.0) or 0.0) <= 0:
                        self._tracker.clear()
                        self._dg_miss = 0
                    else:
                        self._dg_miss = 0

        # --- AUTO-TRACK REAL CLONE (needle locks the live TrueClone, never
        # the decoy; clears when it despawns) ---------------------------------
        if self._tracker is not None and getattr(
                self.s, "rift_clone_track", True):
            reals = [(e, d) for e, d, k in self._dg_enemies
                     if k == "clone_real" and (getattr(e, "hp", 0.0) or 0.0) > 0]
            auto_uid = getattr(self, "_dg_auto_uid", None)
            prev = getattr(self, "_dg_auto_prev", None)
            if reals:
                e, _d = min(reals, key=lambda t: t[1])
                # Track clone if not already tracked or if locked to a different instance
                if not self._tracker.is_tracked("unit", e.unit_id) or self._tracker.locked_addr != e.addr:
                    # Remember the user's manually-tracked target (if any) so we
                    # can hand control back when the clone despawns — never just
                    # drop it. Snapshot once per clone session.
                    if prev is None:
                        curr_k = getattr(ts, "track_kind", "")
                        curr_id = getattr(ts, "track_id", "")
                        if (curr_k, curr_id) != ("unit", e.unit_id) and curr_k and curr_id:
                            self._dg_auto_prev = (curr_k, curr_id)
                        else:
                            self._dg_auto_prev = None
                    self._tracker.track("unit", e.unit_id, addr=e.addr)
                    self._dg_auto_uid = e.unit_id
                else:
                    self._dg_auto_uid = e.unit_id
            elif auto_uid:
                # Clone gone: restore the target we overrode; otherwise clear cleanly.
                if prev is not None:
                    pk, pid = prev[0], prev[1]
                    if pk and pid:
                        try:
                            self._tracker.track(pk, pid)
                        except Exception:
                            self._tracker.clear()
                    else:
                        self._tracker.clear()
                    self._dg_auto_prev = None
                else:
                    self._tracker.clear()
                self._dg_auto_uid = None

        self._render_dg_sections(profile)
        self._check_auto_height()

    # --- render --------------------------------------------------------------
    def _render_dg_hidden(self):
        for box in (self.dg_rift_box, self.dg_boss_box,
                    self.dg_player_box, self.dg_enemy_box,
                    self.dg_loot_box, self.dg_food_box, self.dg_orb_box):
            box.hide()

    def _render_dg_rift(self, isz: int):
        """Rift card (hidden inside a rift): CLOSING red / WARNING gold /
        SCHEDULED purple with the exact remaining time."""
        mode = str(getattr(self.s, "show_rift_timer", "Always")).lower()
        if mode in ("off", "false"):
            self.dg_rift_box.hide()
            return
        try:
            if self.model.is_in_rift():
                self.dg_rift_box.hide()
                return
        except Exception:
            pass
        rst = self.model.rift_status()
        if not (rst and rst.state in ("ACTIVE", "WARNING", "CLOSING", "SCHEDULED")):
            self.dg_rift_box.hide()
            return

        subzone = getattr(rst, "subzone", rst.rift_name or "")
        area = self._clean_rift_area(subzone) if subzone and subzone != "Rift" else ""

        if rst.state in ("ACTIVE", "CLOSING"):
            # current rift is live/closing — clock math for exact seconds
            import time
            struct = time.gmtime()
            into = struct.tm_min * 60 + struct.tm_sec
            close_secs = (180 - into) if struct.tm_min < 3 else (3600 - into) + 180
            mm, ss = divmod(max(0, close_secs), 60)
            name = area or self._clean_rift_area(rst.rift_name or "")
            col = "#EF5350"
            label = f"{name} Closing" if name else "Rift Closing"
            val = f"{mm}m {ss}s"
            bold = True
        else:
            col = theme.GOLD if rst.state == "WARNING" else "#C084FC"
            if rst.state == "WARNING":
                label = (f"Rift · {area} Starting in" if area
                         else "Rift Starting in")
            else:
                label = (f"Next Rift · {area} Starts in" if area
                         else "Next Rift Starts in")
            val = rst.formatted_time
            bold = rst.state == "WARNING"

        self.dg_rift_box.show()
        self.dg_rift_box.header.set_tag("")
        self.dg_rift_box.fill([RowSpec(
            None, None, col, label, col,
            value=val, value_color=theme.GOOD if val else None, bold=bold,
            highlight=bold,
            outlined=True, marker="rift", font_size=15,
        )], isz)

    def _render_dg_sections(self, profile):
        isz = round(self.s.icon_size * self._scale)

        # --- FOOD (top of the HUD; player-placed feasts / cauldrons) --------
        food = self.food_specs(isz)
        if food:
            self.dg_food_box.show()
            self.dg_food_box.fill(food, isz)
            self.dg_food_box.header.set_tag("")
        else:
            self.dg_food_box.hide()

        self._render_dg_rift(isz)

        # --- PLAYERS (group members, grouped by class) ----------------------
        if self._dg_players and getattr(self.s, "dungeon_show_players", False):
            classes: dict[str, list] = {}

            def _hero_class(e) -> str:
                """Same class resolution as the entity HUD's PLAYERS rows."""
                cls_name = getattr(e, "cls", "") or ""
                if cls_name.startswith("ent.hero."):
                    return cls_name.replace("ent.hero.", "").title() or "Hero"
                unit = (getattr(e, "unit_id", "") or "").lower()
                for c in ("warrior", "rogue", "mage", "priest",
                          "paladin", "hunter", "bard", "druid"):
                    if c in unit:
                        return c.title()
                return "Hero"

            for e, d in self._dg_players:
                classes.setdefault(_hero_class(e), []).append((e, d))
            specs = []
            deaths = getattr(self, "_dg_death_counts", {})
            for cls, members in sorted(classes.items(),
                                       key=lambda kv: min(dd for _e, dd in kv[1])):
                count = len(members)
                d = min(dd for _e, dd in members)
                n_dead = sum(deaths.get(getattr(e, "addr", None), 0)
                             for e, _dd in members)
                # deaths ride inline ("Priest (x2 · ☠3)")
                display = cls if count <= 1 else f"{cls} (x{count})"
                if n_dead:
                    display += f" · ☠{n_dead}" if count > 1 else f" ☠{n_dead}"
                col = theme.HERO.get(cls.lower(), "#c2c6d0")
                specs.append(RowSpec(
                    None, None, col, display, col,
                    value=f"{d:>6.0f}m",
                    ui_icon="player",
                    key=("player", cls)))
            self.dg_player_box.show()
            self.dg_player_box.fill(specs, isz)
            total = sum(len(m) for m in classes.values())
            tot_dead = sum(deaths.get(getattr(e, "addr", None), 0)
                           for members in classes.values() for e, _dd in members)
            hdr = f"PLAYERS · {total}" + (f" · ☠{tot_dead}" if tot_dead else "")
            self.dg_player_box.header.set_text(hdr)
        else:
            self.dg_player_box.hide()

        # --- ENEMIES (grouped; clones keyed by raw id so TRUE/FALSE don't merge)
        groups: dict[str, list] = {}
        for e, d, kind in self._dg_enemies:
            gkey = (e.unit_id or "?") if kind in ("boss", "clone_real", "avoid") \
                else self._dg_display_name(e.unit_id or "")
            groups.setdefault(gkey, []).append((e, d, kind))

        # Bosses pinned above; avoid-clones stay in the mob list.
        boss_groups = {n: v for n, v in groups.items()
                       if any(k in ("boss", "clone_real") for _, _, k in v)}
        trash_groups = {n: v for n, v in groups.items() if n not in boss_groups}

        def _enemy_row(name, variants):
            variants.sort(key=lambda t: t[1])
            d = variants[0][1]
            count = len(variants)
            kinds = {k for _, _, k in variants}
            is_avoid = "avoid" in kinds
            is_boss = "boss" in kinds or "clone_real" in kinds
            if is_boss:
                # Bind to the biggest HP pool (rift mini-bosses share names).
                peaks = getattr(self, "_dg_hp_peaks", {})
                e = max(variants, key=lambda t: (
                    peaks.get(getattr(t[0], "addr", None), 0)
                    or getattr(t[0], "hp", 0) or 0))[0]
            else:
                e = variants[0][0]

            uid = e.unit_id or ""
            # Resolve human name (groups are keyed by raw unit id).
            name = self._dg_display_name(uid)
            key = ("enemy", uid)
            tracked = self._is_tracked("unit", uid)

            if is_avoid:
                # decoy: silver tile, red AVOID warning
                col = theme.SILVER
                name_col = _AVOID_COLOR
                sub = "⚠ AVOID — DECOY CLONE"
                border = None
            elif is_boss:
                col = name_col = theme.GOLD
                sub = "★ REAL BOSS" if "clone_real" in kinds else ""
                border = None          # no icon-box border on bosses (user pref)
            else:
                is_elite = _dg_is_elite(uid)
                # elite: silver tile, red name, 4px outline
                col = theme.SILVER if is_elite else theme.DANGER
                name_col = theme.DANGER
                sub = ""
                border = None
            if tracked:
                name_col = self.s.hud_accent or theme.ACCENT
                sub = f"{sub} · ◈ TRACKING" if sub else "◈ TRACKING"

            hp_txt = ""
            hp = getattr(e, "hp", 0.0) or 0.0
            peak = getattr(self, "_dg_hp_peaks", {}).get(getattr(e, "addr", None), 0.0)
            if hp > 0 and peak > 0:
                pct = max(1, round(hp / peak * 100))
                hp_txt = f"HP {hp:,.0f} ({pct}%)"

            display = name if count <= 1 else f"{name} (x{count})"
            if is_boss:
                # Boss value = live HP% (distance is irrelevant).
                value = hp_txt or "HP --"
            else:
                value = f"{d:>6.0f}m"
            thick = 4 if (is_boss or is_avoid or _dg_is_elite(uid)) else 2
            # plain trash gets no second line at all: no HP%, no noise
            if is_boss or is_avoid or _dg_is_elite(uid):
                # 2nd line = who the mob is targeting.
                tgt = self.model.foe_target_line(e)
                parts = [x for x in (sub, tgt) if x]
                row_sub = " · ".join(parts)
            else:
                row_sub = sub
            return RowSpec(
                "Units", uid, col, display, name_col,
                sub=row_sub,
                sub_color=_AVOID_COLOR if is_avoid else None,
                value=value, bold=is_boss or is_avoid,
                highlight=tracked, outlined=True,
                outline_border=thick,
                border_color=border,
                cb=(lambda k=key, u=uid, a=getattr(e, "addr", None):
                    self._dg_select(k, u, a)),
                right_cb=(lambda u=uid: self._dg_copy_id(u)),
                key=key)

        # BOSS section (always pinned at the top, outside the mob list)
        boss_specs = [_enemy_row(n, v) for n, v in sorted(
            boss_groups.items(), key=lambda kv: min(t[1] for t in kv[1]))]
        if boss_specs:
            self.dg_boss_box.show()
            self.dg_boss_box.fill(boss_specs, isz)
        else:
            self.dg_boss_box.hide()

        # Mob list: nearest first; >100m cut once 5 groups shown (spam guard).
        specs = []
        shown_groups = 0
        sorted_trash = sorted(trash_groups.items(),
                              key=lambda kv: min(t[1] for t in kv[1]))
        for name, variants in sorted_trash:
            dmin = min(v[1] for v in variants)
            if shown_groups >= 5 and dmin > 100.0:
                continue
            specs.append(_enemy_row(name, variants))
            shown_groups += 1

        if specs:
            self.dg_enemy_box.show()
            self.dg_enemy_box.fill(specs, isz)
            tag = "" if getattr(self.model, "units_ok", True) else "READ FAILED · RETRYING"
            self.dg_enemy_box.header.set_tag(tag)
        else:
            self.dg_enemy_box.hide()

        # --- LOOT (rarity floor; best rarity first — see DungeonLootMixin)
        loot = self.loot_specs(isz)
        if loot:
            self.dg_loot_box.show()
            self.dg_loot_box.fill(loot, isz)
        else:
            self.dg_loot_box.hide()

        # --- SECRET ORBS (titlebar eye-toggle; Settings.dungeon_show_orbs) ---
        if not getattr(self.s, "dungeon_show_orbs", True):
            self.dg_orb_box.hide()
            return
        orb_specs = []
        for o in self._dg_orbs:
            eid = o.elem_id or ""
            d = o.dist2d(*self._dg_xyz[:2]) if getattr(self, "_dg_xyz", None) else 0.0
            label = self._dg_orb_label(eid)
            # InstanceOrbs aren't registered world orbs — track as a pos waypoint.
            pos_key = f"{o.x:.1f},{o.y:.1f},{o.z:.1f}|{label}"
            tracked = self._is_tracked("pos", pos_key)
            orb_specs.append(RowSpec(
                None, None, theme.KIND_COLOR["orb"],
                label, self.s.hud_accent or theme.ACCENT if tracked else theme.KIND_COLOR["orb"],
                value=f"{d:>6.0f}m", bold=tracked,
                highlight=tracked, outlined=True, outline_border=2,
                cb=(lambda k=pos_key: self._track("pos", k)),
                right_cb=(lambda oid=eid: self._dg_mark_orb_done(oid)),
                marker="orb",
                key=("orb", eid)))
        if orb_specs:
            self.dg_orb_box.show()
            self.dg_orb_box.fill(orb_specs, isz)
        else:
            self.dg_orb_box.hide()

    def food_specs(self, isz: int) -> list[RowSpec] | None:
        """FOOD rows: nearest first, name + distance (+ item icon). None hides the section."""
        if not getattr(self, "_dg_food", None):
            return None
        xyz = getattr(self, "_dg_xyz", None)
        if xyz is None:
            return None
        specs = []
        for f in sorted(self._dg_food,
                        key=lambda f: f.dist2d(xyz[0], xyz[1]))[:_MAX_FOOD_ROWS]:
            d = f.dist2d(xyz[0], xyz[1])
            disp_name, iid = catalog.resolve_food_info(f.name)
            common = dict(
                accent="#FB923C", name=disp_name, name_color="#FDBA74",
                value=f"{d:>6.0f}m",
                key=("food", getattr(f, "addr", None)))
            specs.append(RowSpec("item", iid, **common))
        return specs or None

    # --- helpers -------------------------------------------------------------
    @staticmethod
    def _clean_rift_area(name: str) -> str:
        """Announcement names carry decorations like 'Rift ' prefix and a
        trailing 'LIVE' — strip them for clean display."""
        n = (name or "").strip()
        if n.lower().startswith("rift "):
            n = n[5:].strip()
        if n.lower().endswith(" live"):
            n = n[:-5].strip()
        return n

    @staticmethod
    def _dg_orb_label(elem_id: str) -> str:
        """Compact label — full elem_ids ('Z1_Dungeon_…_InstanceOrb_3') are
        just noise in the HUD."""
        tail = elem_id.rstrip("_").split("_")[-1]
        if tail.isdigit():
            return f"Secret Orb {int(tail)}"
        segs = [s for s in elem_id.split("_") if s][-2:]
        return "Orb " + " ".join(segs).title() if segs else "Secret Orb"

    def _dg_select(self, key, unit_id, addr=None):
        self._sel = key
        self._track("unit", unit_id, addr=addr)

    @staticmethod
    def _dg_copy_id(unit_id: str):
        """Right-click a mob row -> raw unit ID to clipboard (scan helper)."""
        if unit_id:
            QtWidgets.QApplication.clipboard().setText(unit_id)

    def _dg_mark_orb_done(self, elem_id: str):
        profile = self.model.player_profile() if self.model else None
        self.s.toggle_dungeon_orb_done(elem_id, profile)
        self._refresh()

    def _check_auto_height(self):
        # Maintain a clean bottom padding (subtle line break) for breathing room
        bottom_pad = max(3, round(6 * self._scale))
        if hasattr(self, "_bottom_spacer"):
            self._bottom_spacer.changeSize(0, bottom_pad, QtWidgets.QSizePolicy.Minimum, QtWidgets.QSizePolicy.Fixed)
        self._body.invalidate()

        # Visible section states
        food_count = len(self._dg_food) if getattr(self, "_dg_food", None) else 0
        rift_vis = self.dg_rift_box.isVisible()
        boss_count = len(getattr(self.dg_boss_box, "_rows", []))
        boss_vis = self.dg_boss_box.isVisible()
        player_vis = self.dg_player_box.isVisible()
        player_count = len(self._dg_players) if getattr(self, "_dg_players", None) else 0
        enemy_count = len(self._dg_enemies) if getattr(self, "_dg_enemies", None) else 0
        loot_count = len(self._dg_loot) if getattr(self, "_dg_loot", None) else 0
        loot_vis = self.dg_loot_box.isVisible()
        orb_count = len(self._dg_orbs) if getattr(self, "_dg_orbs", None) else 0
        orb_vis = self.dg_orb_box.isVisible()

        curr = (
            food_count, rift_vis, boss_count, boss_vis, player_vis, player_count,
            enemy_count, loot_count, loot_vis, orb_count, orb_vis,
            self._sel,
            getattr(self.s, "icon_size", 20),
            getattr(self.s, "entity_font_size", 14),
            getattr(self.s, "entity_width", 330),
        )

        if curr != getattr(self, "_last_h_config", None):
            self._last_h_config = curr
            # Apply live font-size scale if entity_font_size changes
            fsize = getattr(self.s, "entity_font_size", 14)
            new_scale = fsize / 14.0
            if abs(new_scale - self._scale) > 0.005:
                self._base_w = max(260, min(700, getattr(self.s, "entity_width", 330)))
                self.apply_scale(new_scale)

            # Use singleShot to let the layout settle before measuring height
            QtCore.QTimer.singleShot(0, self._do_auto_height)

    def _do_auto_height(self):
        try:
            if not hasattr(self, "_body") or self._body is None:
                return
            body_hint = self._body.sizeHint().height()
            CHROME = 50
            target_h = body_hint + CHROME
            screen = QtWidgets.QApplication.primaryScreen()
            screen_h = screen.availableGeometry().height() if screen else 900
            target_h = min(target_h, screen_h - 40)
            target_h = max(target_h, 60)
            if target_h != self.height():
                self._base_h = target_h
                self.resize(self.width(), target_h)
        except (RuntimeError, AttributeError):
            pass

    def closeEvent(self, e):
        self._timer.stop()
        super().closeEvent(e)

