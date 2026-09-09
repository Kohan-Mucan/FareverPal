"""Dungeon overlay — memory-scanner HUD for dungeons & rifts (no minimap).
Sections: FOOD / RIFT / BOSS / PLAYERS / ENEMIES / LOOT / SECRET ORBS
(labels hidden on RIFT & SECRET ORBS — content only).
"""
from __future__ import annotations

import math

from PySide6 import QtCore, QtGui, QtWidgets

from .. import theme
from ..dps_source_text import dungeon_mode_badge, instance_mode, mode_tooltip
from ..overlay_base import OverlayWindow, TrackerHostMixin
from ...core import attributes, game_state
from ...core.proc import ProcError
from ...data import names, units as udata
from ...data.items import catalog
from .dungeon_render import DungeonRenderMixin
from .loot_rows import DungeonLootMixin
from .entity_rows import _Section, _scroll_body, RowSpec

POLL_MS = 350

# Shaarlize clones: FalseClone = decoy to AVOID, TrueClone = real boss
SHAARLIZE_TRUE_CLONE = "DemonSuperElite_Fairy_TrueClone"
SHAARLIZE_FALSE_CLONE = "DemonSuperElite_Fairy_FalseClone"
DG_REAL_CLONES = {SHAARLIZE_TRUE_CLONE}
DG_AVOID_CLONES = {SHAARLIZE_FALSE_CLONE}

_MAX_FOOD_ROWS = 4

# Every Dungeon HUD section's titlebar toggle: key -> (settings attr, ui_icon
# glyph, tooltip shown / tooltip hidden). Order = titlebar left-to-right.
_SECTION_TOGGLES = (
    ("food",    "dungeon_show_food",    "drumstick", "Hide food",   "Show food"),
    ("rift",    "dungeon_show_rift",    "radio",     "Hide rift timer", "Show rift timer"),
    ("boss",    "dungeon_show_boss",    "crown",     "Hide bosses", "Show bosses"),
    ("enemies", "dungeon_show_enemies", "swords",    "Hide enemies", "Show enemies"),
    ("players", "dungeon_show_players", "users",     "Hide players", "Show players"),
    ("loots",   "dungeon_show_loots",   "box",       "Hide loot",   "Show loot"),
    ("orbs",    "dungeon_show_orbs",    "eye",       "Hide secret orbs", "Show secret orbs"),
)
_SECTION_TOGGLES_BY_KEY = {k: (a, i, ton, toff)
                           for k, a, i, ton, toff in _SECTION_TOGGLES}


def _food_item_id(name: str | None) -> str | None:
    """Stable item id behind a placed-food display name ('Plainswalker Feast'
    -> 'Feast'); None when unknown or the name never resolved."""
    if not name:
        return None
    try:
        return catalog.resolve_food_info(name)[1]
    except Exception:
        return None


class DungeonOverlay(DungeonRenderMixin, DungeonLootMixin, TrackerHostMixin,
                     OverlayWindow):
    def __init__(self, model, settings, parent=None):
        # First launch: seed geometry from the entity HUD.
        if settings is not None:
            geo = getattr(settings, "geometry", None)
            if isinstance(geo, dict) and not geo.get("dungeon"):
                ent_geo = geo.get("entity")
                if ent_geo:
                    geo["dungeon"] = ent_geo
        super().__init__("HUD", settings, geo_key="dungeon", parent=parent)
        # Title bar carries the live dungeon mode badge (· NORMAL/HARD/HEROIC);
        # _set_title() is change-guarded, so the tick can call it every pass.
        self._title_state = object()   # sentinel: forces the first paint
        self._set_title(None)
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

        # Per-section titlebar toggles (2026-09-18) — the Entity-HUD-style
        # set: every section gets its own show/hide button, icon-lit when the
        # section shows, muted when hidden. Data-driven so a new section is
        # one tuple, not three copy-pasted blocks.
        self._section_btns: dict[str, QtWidgets.QPushButton] = {}
        for spec in _SECTION_TOGGLES:
            key, icon = spec[0], spec[2]
            btn = QtWidgets.QPushButton()
            btn.setObjectName("Icon")
            btn.setFixedSize(22, 22)
            btn.clicked.connect(
                lambda _=False, k=key: self.set_show_section(
                    k, not bool(getattr(self.s, _SECTION_TOGGLES_BY_KEY[k][0], True))))
            self._update_section_btn(key, btn, icon)
            btn._dg_icon = icon
            self._section_btns[key] = btn
            self.titlebar.extra.insertWidget(self.titlebar.extra.count() - 3, btn)

        self.enable_resize_grip()
        self._base_w, self._base_h = 330, 420
        self.setMinimumWidth(260)
        self.setMaximumWidth(700)
        self.apply_scale(15 / 14.0)
        self._last_h_config = None

        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(POLL_MS)

    def _set_title(self, mode: str | None, source: str = "") -> None:
        """Title text = 'HUD' + the dungeon-mode badge; empty badge outside an
        instance. Only rewritten on a (mode, source) CHANGE (per-tick rich-text
        reparse would churn the label for nothing); ``source`` only reaches the
        tooltip."""
        if (mode, source) == self._title_state:
            return
        self._title_state = (mode, source)
        self.titlebar.title.setText("HUD" + dungeon_mode_badge(mode))
        self.titlebar.title.setToolTip(mode_tooltip(mode, source))

    # --- titlebar toggles ---------------------------------------------------
    def set_show_section(self, key: str, on: bool) -> None:
        """Persist one section's visibility (Settings attr) + re-render now."""
        spec = _SECTION_TOGGLES_BY_KEY.get(key)
        if spec is None:
            return
        attr = spec[0]
        setattr(self.s, attr, bool(on))
        self.s.save()
        self._sync_section_btns()
        self._tick()

    # Back-compat names (other code / the loot mixin may call these):
    def set_show_orbs(self, on: bool):
        self.set_show_section("orbs", on)

    def set_show_players(self, on: bool):
        self.set_show_section("players", on)

    def set_show_loots(self, on: bool):
        self.set_show_section("loots", on)

    def _sync_section_btns(self) -> None:
        """Re-tint every section button from the current settings."""
        for key, btn in self._section_btns.items():
            self._update_section_btn(key, btn)

    def _update_section_btn(self, key: str, btn: QtWidgets.QPushButton,
                            icon: str | None = None) -> None:
        from ...data import icons
        _attr, icon_default, tip_on, tip_off = _SECTION_TOGGLES_BY_KEY[key]
        glyph = icon or getattr(btn, "_dg_icon", None) or icon_default
        on = bool(getattr(self.s, _attr, True))
        if glyph == "eye" and not on:
            glyph = "eye-off"          # orbs keep the eye/eye-off pair
        btn.setIcon(QtGui.QIcon(icons.ui_icon(
            glyph, self.s.hud_accent if on else theme.MUTED, 18)))
        btn.setIconSize(QtCore.QSize(18, 18))
        btn.setToolTip(tip_on if on else tip_off)

    # --- lifecycle / tracker ------------------------------------------------
    def reset_state(self) -> None:
        self._sel = None
        self._clear_dg_lists()

    def _tick(self):
        if not self.isVisible():
            return
        # Re-entrancy guard: tracker signals (changed) can fire synchronously
        # from inside _refresh (track/clear), re-entering this slot.
        if getattr(self, "_dg_in_tick", False):
            return
        self._dg_in_tick = True
        try:
            # Same resolution as the Top DPS HUD, the DPS Analysis page and the
            # Run Timer (ui/dps_source_text.instance_mode): this HUD only ever
            # shows inside an instance, so its badge names the difficulty the
            # timer names even when the game's own reads are dead, instead of
            # sitting blank beside a titled meter (2026-09-30).
            self._set_title(*instance_mode(self.model))
        except Exception:
            pass
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

    def _refresh_dg_orbs(self, xyz, profile):
        """SECRET ORBS gather: InstanceOrb elements; collected ones are done.

        2026-09-19 ("dungeon Hide Collected does nothing"): the game's own
        element state is the authoritative signal — `Enabled` = up for
        collection, `Disabled` = collected (docs/CHEST_ORB_LOGIC.md, verified
        live). It is read every tick and needs no history, so collected orbs
        are marked the moment you enter the instance. The `currentFx` glow
        stays as the second signal for orbs whose state can't be read (world
        orbs, fresh loads): glowing = up, and dark only counts as collected if
        that orb glowed earlier in the session.

        2026-09-18 hardening (the 'red collected orbs keep coming back' fix):
        the fx pointer is read ONLY when the read can be trusted — the
        reflected `currentFx` offset (model.orb_fx_present), and the value
        itself a plausible pointer. An untrusted read is "no opinion": it
        can neither un-done a marked orb nor newly mark one. Before this, a
        garbage read at the drifted hardcoded slot read True for EVERY orb
        and un-did the persisted done list one tick at a time, so collected
        orbs re-appeared red and Hide Collected looked dead. A marked orb
        LIVE on two consecutive trusted reads is the only honest un-done
        (debounced like geo/orb_sync.FxSync: a single lying tick must never
        wipe a persisted mark); a vanish near the orb's last position is the
        fallback for orbs whose reads never turn. The disk delta re-reads the
        file first (the disk-authoritative pattern the world-orb sync uses).
        """
        model = self.model
        if game_state.in_rift(model):
            orbs = []
        else:
            try:
                orbs = model.live_orbs(max_dist=0, use_2d=False)
            except ProcError:
                orbs = []
        profile_key = profile or "_global"
        seen = getattr(self, "_dg_orb_seen_fx", {})
        seen = {k: v for k, v in seen.items() if k[0] == profile_key}
        pending = getattr(self, "_dg_orb_fx_pending", {})
        pending = {k: v for k, v in pending.items() if k[0] == profile_key}
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
            pk = (profile_key, eid)
            has_fx = model.orb_fx_present(o)        # True / False / None
            # The element's OWN state is the game's verified done flag
            # (docs/CHEST_ORB_LOGIC.md: an event orb reads 'Enabled' while it
            # is up for collection and 'Disabled' once collected). It needs no
            # history, so a trusted 'Disabled' marks on FIRST sight. Without
            # it, entering a dungeon whose orbs were collected long ago left
            # every one "undecided" (dark, never seen glowing), the whole list
            # stayed red and Hide Collected had nothing to hide. Unreadable
            # state (None) keeps the fx rules.
            st = str(getattr(o, "state", None) or "").strip().lower()
            if st == "disabled":
                collected = True
            elif st == "enabled":
                collected = False                  # up for grabs — live
            elif has_fx is True:
                collected = False                  # glowing = up for grabs
            elif has_fx is False:
                # Dark AND glowed before = collected. Dark with no glow
                # history (fresh load, LOD) stays undecided.
                collected = True if seen.get(pk) else None
            else:
                collected = None                   # untrusted: no opinion

            if collected is False:
                # A glowing/live observation is not sufficient evidence to
                # remove a persisted mark. Chunk activation and stale live
                # state can make a collected orb glow temporarily.
                seen[pk] = True
                pending.pop(pk, None)
            elif collected is True:
                pending.pop(pk, None)
                done_set.add(eid)
            # else: no opinion. A persisted done orb stays hidden below; an
            # undecided one keeps showing until a trusted read says otherwise.
            if eid in done_set:
                if getattr(self.s, "dungeon_hide_collected", True):
                    continue
            kept.append(o)
        self._dg_orb_fx_pending = pending

        # Vanish fallback: a seen orb gone from the scan near its last pos
        # was collected (covers orbs whose fx read never turns dark).
        for eid in [k for (_p, k) in seen if k not in current_ids]:
            pos = lastpos.get(eid)
            if pos and xyz and math.hypot(pos[0] - xyz[0], pos[1] - xyz[1]) <= 50.0:
                done_set.add(eid)
                seen.pop((profile_key, eid), None)
                pending.pop((profile_key, eid), None)
                lastpos.pop(eid, None)
        self._dg_orb_seen_fx = seen
        self._dg_orb_lastpos = lastpos
        # Disk-authoritative delta: apply only newly observed collections.
        # Persisted marks are never removed by this live-state scanner.
        disk_done = set(self.s.get_dungeon_orb_done(profile))
        if done_set != disk_done:
            for eid in done_set - disk_done:
                self.s.toggle_dungeon_orb_done(eid, profile)   # add new
        self._dg_orbs = kept

    # --- gather --------------------------------------------------------------
    def _refresh(self):
        if self.model is None:
            return

        # Gate: the HUD only gathers while inside a dungeon/rift (one shared
        # answer per tick - core/game_state.py).
        if not game_state.in_instance(self.model):
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
                bid = game_state.boss(self.model)
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

        # --- ORBS ------------------------------------------------------
        self._refresh_dg_orbs(xyz, profile)

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

    # --- render: see dungeon_render.py (DungeonRenderMixin) --------------------
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

    def _ingame_height(self) -> int:
        """Body + titlebar chrome, capped to the screen — shared with the live
        auto-height below and with the 1:1 preview, which pins this."""
        try:
            body = getattr(self, "_body", None)
            if body is None:
                return 0
            screen = QtWidgets.QApplication.primaryScreen()
            screen_h = screen.availableGeometry().height() if screen else 900
            return max(60, min(body.sizeHint().height() + 50, screen_h - 40))
        except (RuntimeError, AttributeError):
            return 0

    def _do_auto_height(self):
        try:
            if not hasattr(self, "_body") or self._body is None:
                return
            target_h = self._ingame_height()
            if target_h and target_h != self.height():
                self._base_h = target_h
                self.resize(self.width(), target_h)
        except (RuntimeError, AttributeError):
            pass

    def closeEvent(self, e):
        self._timer.stop()
        super().closeEvent(e)

