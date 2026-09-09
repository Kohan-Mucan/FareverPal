"""Dungeon HUD section rendering — the rift / boss / players / enemies / loot /
secret-orb cards.

Split out of `dungeon_overlay.py` so that overlay stays under the ui line budget,
mirroring the entity HUD's `entity_render.py`: the overlay owns the tick and the
state, this owns turning that state into RowSpec lists.

The renderers call back into the overlay for the pieces that stayed there
(`food_specs`, `loot_specs`, `_dg_display_name`, `_dg_orb_label`, tracking), so
this is a mixin rather than a free-function library — composed onto
`DungeonOverlay` as a base.
"""
from __future__ import annotations

from .. import theme
from ...core import game_state
from ...data import units as udata
from .entity_rows import RowSpec

_AVOID_COLOR = "#ff5252"


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


class DungeonRenderMixin:
    """The dungeon HUD's section renderers."""

    def _render_dg_hidden(self):
        for box in (self.dg_rift_box, self.dg_boss_box,
                    self.dg_player_box, self.dg_enemy_box,
                    self.dg_loot_box, self.dg_food_box, self.dg_orb_box):
            box.hide()

    def _render_dg_rift(self, isz: int):
        """Rift card (hidden inside a rift): CLOSING red / WARNING gold /
        SCHEDULED purple with the exact remaining time.

        dungeon_show_rift (2026-09-18): the schedule card is toggleable like
        every other section; off means never show it regardless of state."""
        if not getattr(self.s, "dungeon_show_rift", True):
            self.dg_rift_box.hide()
            return
        mode = str(getattr(self.s, "show_rift_timer", "Always")).lower()
        if mode in ("off", "false"):
            self.dg_rift_box.hide()
            return
        if game_state.in_rift(self.model):
            self.dg_rift_box.hide()
            return
        rst = game_state.rift(self.model)
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
        # dungeon_show_food: the FOOD card is toggleable like every other
        # section (2026-09-18 — Entity-HUD-style per-item toggles).
        food = self.food_specs(isz) if getattr(self.s, "dungeon_show_food", True) else None
        if food:
            self.dg_food_box.show()
            self.dg_food_box.fill(food, isz)
            self.dg_food_box.header.set_tag("")
        else:
            self.dg_food_box.hide()

        self._render_dg_rift(isz)

        self._render_dg_players(isz)

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
        # dungeon_show_boss (2026-09-18): toggleable like every other section.
        boss_specs = [_enemy_row(n, v) for n, v in sorted(
            boss_groups.items(), key=lambda kv: min(t[1] for t in kv[1]))]
        if boss_specs and getattr(self.s, "dungeon_show_boss", True):
            self.dg_boss_box.show()
            self.dg_boss_box.fill(boss_specs, isz)
        else:
            self.dg_boss_box.hide()

        # Mob list: nearest first; >100m cut once 5 groups shown (spam guard).
        # dungeon_show_enemies (2026-09-18): toggleable like every other
        # section; off hides the whole mob list (bosses have their own toggle).
        specs = []
        shown_groups = 0
        sorted_trash = sorted(trash_groups.items(),
                              key=lambda kv: min(t[1] for t in kv[1])) if getattr(
                                  self.s, "dungeon_show_enemies", True) else []
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

    def _render_dg_players(self, isz: int) -> None:
        """PLAYERS section router.

        Dungeons: one row per player with their real name — a dungeon party
        is at most five, so the list stays compact (2026-09-18 request).
        Rifts: grouped by class — rifts host rival parties too, so per-name
        rows would spill past the HUD.
        """
        if not self._dg_players or not getattr(self.s, "dungeon_show_players", False):
            self.dg_player_box.hide()
            return
        if game_state.in_rift(self.model):
            self._render_dg_players_class(isz)
        else:
            self._render_dg_players_named(isz)

    @staticmethod
    def _dg_hero_class(e) -> str:
        """Same class resolution as the entity HUD's PLAYERS rows."""
        return (getattr(e, "hero_class", None)
                or udata.resolve_hero_class(getattr(e, "cls", None),
                                            getattr(e, "unit_id", None))
                or "Hero")

    def _render_dg_players_named(self, isz: int) -> None:
        """Dungeon PLAYERS: one row per hero with their real name.

        A dungeon party is at most five, so per-name rows stay compact — and
        they match the Entity HUD's group section, which always names rows.
        My own hero never passes are_in_same_group(self), so
        hero_display_name would fall back to the class label there; resolve
        me directly instead (same pattern as the DPS tracker).
        """
        deaths = getattr(self, "_dg_death_counts", {})
        pa = getattr(self.model, "player_addr", 0)
        specs = []
        for e, d in self._dg_players:
            addr = getattr(e, "addr", None)
            is_me = bool(addr and pa and addr == pa)
            if is_me and pa:
                name = self.model.player_name(pa) or "You"
                name = f"{name} (You)"
            else:
                name = self.model.hero_display_name(e) or self._dg_hero_class(e)
            n_dead = deaths.get(addr, 0)
            if n_dead:
                name += f" ☠{n_dead}"
            col = theme.HERO.get(self._dg_hero_class(e).lower(), "#c2c6d0")
            key = ("player", addr)
            specs.append(RowSpec(
                None, None, col, name, col,
                value=f"{d:>6.0f}m",
                ui_icon="player",
                cb=(lambda k=key, u=getattr(e, "unit_id", None), a=addr:
                    self._dg_select(k, u, a)) if addr is not None else None,
                key=key))
        self.dg_player_box.show()
        self.dg_player_box.fill(specs, isz)
        tot_dead = sum(deaths.get(getattr(e, "addr", None), 0)
                       for e, _dd in self._dg_players)
        hdr = f"PLAYERS · {len(specs)}" + (f" · ☠{tot_dead}" if tot_dead else "")
        self.dg_player_box.header.set_text(hdr)

    def _render_dg_players_class(self, isz: int) -> None:
        """Rift PLAYERS: grouped by class (the pre-2026-09-18 rendering).

        Rifts host rival parties too — dozens of heroes — so per-name rows
        would spill past the HUD; class grouping stays informative.
        """
        deaths = getattr(self, "_dg_death_counts", {})
        classes: dict[str, list] = {}
        for e, d in self._dg_players:
            classes.setdefault(self._dg_hero_class(e), []).append((e, d))
        specs = []
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


__all__ = ["DungeonRenderMixin"]
