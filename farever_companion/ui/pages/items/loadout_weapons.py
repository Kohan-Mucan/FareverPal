"""Weapon-slot half of the Loadout page: the weapon cards, their enchants,
the ladder max they are read at, and the item picker dialog itself.

This is the Loadout family's ONE shard beside `loadout.py`. The budget measures
one file, so the family is two files, not a main file with a module per widget
group. The weapon cards and the item picker are one concern: the cards open
the dialog and read the weapon it returns.

Composed onto the same page as `LoadoutMixin`, so no call site moved.
"""
from __future__ import annotations

import time

from PySide6 import QtCore, QtGui, QtWidgets

from ... import theme
from ... import components as C
from ....data import icons
from ....data import items as idata
from ....data.items import labels as glabels
from ....craft import planner as pdata


# Weapon slot index -> the attribute + card it owns (one table, so a swap can
# refresh both sides without three copies of the mapping).
_WEAPON_ATTRS = {1: "_loadout_w1", 2: "_loadout_w2", 3: "_loadout_ars"}
_WEAPON_CARDS = {1: "_w1_card", 2: "_w2_card", 3: "_ars_card"}


class LoadoutWeaponsMixin:
    """The weapon cards: slots, the ladder max every piece is read at, the
    enchant line, and the picker wiring."""

    def _weapon_max_upgrades(self, iid: str | None) -> int:
        """Game upgrade cap for a weapon (0 when it can't upgrade).

        Uses DISPLAY rarity (shop gear sells as Rare) and the live caps
        (Rare +3 / Epic +4 / Legendary +5 — see stats.upgrade_cap)."""
        if not iid:
            return 0
        try:
            path = idata.upgrade_path(idata.item(iid) or {},
                                      rarity=idata.item_display_rarity(iid))
            return max(0, len(path) - 1) if path else 0
        except Exception:
            return 0

    def _max_out_weapon(self, iid: str | None) -> None:
        """Set a weapon to its MAX upgrade rank, always.

        There is no rank control any more: the card used to carry a `+N/max`
        stepper, and it was always parked at the cap because every pick —
        auto or hand — maxed the piece on the way in, so the control could
        only ever walk a piece back DOWN and three identical `+3/3` numbers
        read as a dead widget. A weapon in a build IS the piece you equip, and
        you equip it maxed (that is what grants the upgraded skill ranks), so
        the rank is now derived, never chosen: this reads the LIVE cap off the
        rarity's upgrade ladder (`_weapon_max_upgrades` → `stats.upgrade_path`
        → the `gearUpgrades` sheet — Rare +3 / Epic +4 / Legendary +5), never
        a literal, so a patch that moves a cap moves every number with it.
        The Collection Manager's weapon tiles already read at this same rank,
        so the two halves of the page now agree by construction.
        """
        if not iid:
            return
        maxr = self._weapon_max_upgrades(iid)
        if maxr > 0:
            self._loadout_upgrades[iid] = maxr
        else:
            self._loadout_upgrades.pop(iid, None)

    def _clear_weapons(self) -> None:
        """Clear all 3 equipped weapons back to Select Weapon..."""
        self._loadout_w1 = None
        self._loadout_w2 = None
        self._loadout_w2_auto = False
        self._loadout_ars = None
        self._loadout_w1_auto = False
        self._loadout_ars_auto = False
        self._loadout_slots = {}
        self._loadout_upgrades = {}
        if hasattr(self, "_w1_card"):
            self._update_weapon_card_display(self._w1_card, None, 1)
        if hasattr(self, "_w2_card"):
            self._update_weapon_card_display(self._w2_card, None, 2)
        if hasattr(self, "_ars_card"):
            self._update_weapon_card_display(self._ars_card, None, 3)
        self._update_class_chips_for_weapon(None)
        self._rebuild_slot_rows()
        self._update_stat_totals()

    def _build_weapon_slot_card(self, title: str, badge_text: str, badge_col: str, slot_idx: int) -> QtWidgets.QFrame:
        box = QtWidgets.QFrame()
        box.setObjectName("WeaponCard")
        box.setCursor(QtCore.Qt.PointingHandCursor)
        box.setStyleSheet(
            f"QFrame#WeaponCard {{ background: {theme.PANEL_HI}; border: 1px solid {theme.BORDER}; border-radius: 6px; }}\n"
            f"QFrame#WeaponCard:hover {{ border-color: {theme.ACCENT}; background: {theme.with_alpha(theme.ACCENT, 15)}; }}")
        lay = QtWidgets.QVBoxLayout(box)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)

        hdr = QtWidgets.QHBoxLayout()
        stitle = QtWidgets.QLabel(title)
        stitle.setStyleSheet(f"color: {theme.TEXT}; font-weight: 700; font-size: 13px;")
        badge = QtWidgets.QLabel(badge_text)
        badge.setStyleSheet(
            f"color: {badge_col}; background: {theme.with_alpha(badge_col, 20)}; "
            f"border-radius: 3px; padding: 1px 5px; font-family: '{theme.MONO_FONT}', monospace; font-size: 11px; font-weight: 700;")
        hdr.addWidget(stitle, 1)
        hdr.addWidget(badge, 0, QtCore.Qt.AlignRight)
        lay.addLayout(hdr)

        # Icon & Weapon Name Row
        prow = QtWidgets.QHBoxLayout()
        prow.setSpacing(8)

        tile = C.IconTile(36)
        prow.addWidget(tile, 0)

        wname_lbl = QtWidgets.QLabel("Select Weapon…")
        wname_lbl.setStyleSheet(f"color: {theme.TEXT}; font-weight: 700; font-size: 14px;")
        prow.addWidget(wname_lbl, 1)

        lay.addLayout(prow)

        # The weapon's stats at its ladder MAX rank — the numbers the loadout
        # actually counts (a pick always maxes, so the totals, the clipboard
        # and the augment plan all read the piece here) and the same end
        # state the item page's UPGRADE LADDER finishes on. The card was the
        # one place on the page showing a weapon with no numbers at all, so
        # the loadout and the item page read differently for the same piece.
        # One capped line, matching the gear slot rows; no tooltip (the gear
        # page is a game-companion HUD).
        stat_lbl = C.ElideLabel("", 330, show_tooltip=False)
        stat_lbl.setStyleSheet(
            f'color: {theme.MUTED}; font-family: "{theme.MONO_FONT}", monospace; '
            f'font-size: 12px; font-weight: 600; background: transparent;')
        stat_lbl.hide()
        lay.addWidget(stat_lbl)

        # Trait info label
        trait_lbl = QtWidgets.QLabel("⚡ Select weapon to view traits & skills")
        trait_lbl.setWordWrap(True)
        trait_lbl.setStyleSheet(
            f"color: {theme.GOLD}; font-size: 12px; background: {theme.with_alpha(theme.GOLD, 12)}; "
            f"border: 1px solid {theme.with_alpha(theme.GOLD, 40)}; border-radius: 4px; padding: 4px 6px;")
        lay.addWidget(trait_lbl)

        # Demon-gear conversion line (Enchants tab data, per weapon stats).
        # Elided single line: up to three `src→tgt +N` segments plus the
        # Arsenal note overflowed the card and wrapped mid-conversion.
        # show_tooltip=False: the gear page is a game-companion HUD —
        # no hover tooltips on it.
        ench_lbl = C.ElideLabel("", 210, show_tooltip=False)
        ench_lbl.setStyleSheet(
            f"color: {theme.MUTED}; font-size: 12px; background: transparent; "
            f"border: none; padding: 0px 2px;")
        ench_lbl.hide()
        lay.addWidget(ench_lbl)

        # WHY the role picked this weapon. The trait line above names the
        # skills, but a list of names says nothing about why THIS one was
        # recommended over the other thirteen the class can wield, and the
        # answer is never the stats — no weapon rolls Armor, vitality runs
        # 32-40 whatever the kit, and healing/blocking/threat are skills this
        # game has no stat for at all. So the card shows the signature
        # phrases the role actually matched, read out of the sheets.
        #
        # AUTO-PICKS ONLY, and that is not a shortcut: a hand-picked weapon
        # was nobody's recommendation, so there is nothing to justify. The
        # same reasoning hides it on the off-hand, which gets the shield
        # RANK block instead.
        why_lbl = C.ElideLabel("", 330, show_tooltip=False)
        why_lbl.setStyleSheet(
            f"color: {theme.ACCENT}; font-size: 11px; background: transparent; "
            f"border: none; padding: 0px 2px;")
        why_lbl.hide()
        lay.addWidget(why_lbl)

        # NO rank stepper here: a weapon is always read at its ladder max
        # (see `_max_out_weapon`), so a +N/max control had nothing to say.

        # Off-hand only: the shield ranking, so the recommendation shows its
        # work. The three Rare shields read an identical 564 Armor at their
        # caps, so the numbers cannot explain the pick — the kit can, so the
        # card lists the contenders with the skill that decided each one, for
        # the ROLE the build is on. Hidden on every other slot and whenever
        # the off-hand is empty or locked.
        fit_box = QtWidgets.QFrame()
        fit_box.setObjectName("ShieldRank")
        flay = QtWidgets.QVBoxLayout(fit_box)
        flay.setContentsMargins(0, 4, 0, 0)
        flay.setSpacing(1)
        fit_box.hide()
        lay.addWidget(fit_box)

        box._tile = tile
        box._wname_lbl = wname_lbl
        box._stat_lbl = stat_lbl
        box._trait_lbl = trait_lbl
        box._why_lbl = why_lbl
        box._ench_lbl = ench_lbl
        box._fit_box = fit_box
        box._fit_lay = flay
        box._badge = badge
        box._is_locked = False

        # Make entire weapon card clickable to open picker
        def _on_card_click(ev, s=slot_idx, b=box):
            if time.monotonic() < getattr(self, "_picker_cooldown_until", 0.0):
                ev.accept()
                return
            if not getattr(b, "_is_locked", False):
                self._open_weapon_picker(s, anchor_btn=b)
            ev.accept()

        box.mousePressEvent = _on_card_click

        # Set default weapon
        initial_id = self._loadout_w1 if slot_idx == 1 else (self._loadout_w2 if slot_idx == 2 else self._loadout_ars)
        self._update_weapon_card_display(box, initial_id, slot_idx)

        return box

    def _picker_dismissed(self) -> None:
        """Start the anti-reopen cooldown when any picker closes."""
        try:
            self._picker_cooldown_until = time.monotonic() + 0.6
        except Exception:
            pass

    def _open_weapon_picker(self, slot_idx: int, anchor_btn: QtWidgets.QWidget | None = None) -> None:
        parent_w = self if isinstance(self, QtWidgets.QWidget) else None
        dlg = _ItemPickerDialog(
            category="Weapons", parent=parent_w,
            # slot 2 is the off-hand, and the off-hand takes a shield and
            # nothing else — asking the popout for "Shield" is what keeps a
            # greatsword out of it. Slots 1 and 3 are main hands.
            slot_type="Shield" if slot_idx == 2 else None,
            class_filter=getattr(self, "_loadout_class_filter", "") or "",
            elsewhere_ids=self._weapons_elsewhere(slot_idx))
        dlg.item_selected.connect(lambda iid, s=slot_idx: self._on_weapon_picked(s, iid))
        dlg.closed.connect(self._picker_dismissed)
        self._active_picker_dlg = dlg
        dlg.popup_at(anchor_btn)

    def _weapons_elsewhere(self, slot_idx: int) -> tuple[str, ...]:
        """The weapons the OTHER two slots hold (see _on_weapon_picked)."""
        return tuple(i for idx, i in self._weapon_slots().items()
                     if idx != slot_idx and i)

    def _weapon_slots(self) -> dict[int, str | None]:
        return {1: self._loadout_w1, 2: self._loadout_w2,
                3: self._loadout_ars}

    def _on_weapon_picked(self, slot_idx: int, iid: str) -> None:
        """Equip a weapon, keeping the three slots DISTINCT.

        One weapon lives in one hand, the Arsenal included, so a pick of a
        weapon another slot already holds MOVES it here and hands this slot's
        old weapon to that slot — a swap, never a duplicate. A duplicate was
        both impossible in game and wrong here: the totals counted the same
        piece at 100% and at 50%, and Copy Loadout / Add to Farm listed it
        twice.

        A freshly picked weapon arrives at its rarity's max upgrade rank, the
        same as one the class auto-fill chooses, and re-picking the same
        piece re-derives that same max — the rank is derived, never chosen.
        """
        prev = self._weapon_slots()[slot_idx]
        # The rank is DERIVED, so re-picking re-derives it — this used to be
        # skipped for the piece already in the slot, to protect a rank the
        # user had dialled back with the (now removed) +N stepper. With no
        # control left that could leave a stale rank on a card that offers no
        # way to fix it, so the max is always (re-)applied.
        self._max_out_weapon(iid)
        if iid != prev:
            for idx, held in self._weapon_slots().items():
                if idx != slot_idx and held == iid:
                    setattr(self, _WEAPON_ATTRS[idx], prev)
                    break
        setattr(self, _WEAPON_ATTRS[slot_idx], iid)
        # a hand-picked weapon is the user's call: it stops being an auto-
        # pick, so a class or role chip never re-decides it
        setattr(self, f"{_WEAPON_ATTRS[slot_idx]}_auto", False)
        if slot_idx == 1:
            if is_2h_weapon(iid):
                # A two-hander frees the off-hand; the swap above may have
                # just moved the old Slot-1 weapon into it.
                self._loadout_w2 = None
                self._loadout_w2_auto = False
            self._update_class_chips_for_weapon(iid)
        # Every card re-reads its slot, so a swapped partner updates too.
        self._refresh_weapon_cards()
        self._rebuild_slot_rows()
        self._update_stat_totals()

    def _refresh_weapon_cards(self) -> None:
        """Re-render all three weapon cards from the current slot state.

        Anything that changes a weapon slot, or the CLASS behind them, has to
        come through here: the off-hand card ranks the shields for the
        selected class, so a class chip that changed without a re-render left
        the previous class's ranking on screen."""
        for idx, attr in _WEAPON_ATTRS.items():
            box = getattr(self, _WEAPON_CARDS.get(idx, ""), None)
            if box is not None:
                self._update_weapon_card_display(box, getattr(self, attr), idx)

    def _update_shield_rank(self, box: QtWidgets.QFrame,
                            iid: str | None) -> None:
        """The off-hand card's ranking block: the recommended shield first,
        then the runners-up, each with the skill that decided it. Hides
        itself on any other slot and on an empty or locked off-hand.

        The equipped piece is marked wherever it sits, so a hand-picked
        runner-up reads as a choice against a stated recommendation rather
        than as the recommendation itself.
        """
        try:
            fit_box = getattr(box, "_fit_box", None)
            if fit_box is None:
                return
            flay = box._fit_lay
            while flay.count():
                w = flay.takeAt(0).widget()
                if w is not None:
                    w.deleteLater()
            cls = getattr(self, "_loadout_class_filter", "") or None
            # the SAME role the pick was made with, or the card ranks a
            # different build than the one it is annotating — and no role
            # resolves to the damage build, matching `_auto_fill_slots_for_class`
            role = getattr(self, "_loadout_role", "") or "damage"
            rows = (idata.rank_offhand_shields(class_filter=cls, level=25,
                                               role=role)
                    if cls else [])
            # one contender is not a comparison
            if len(rows) < 2:
                fit_box.hide()
                return
            head = QtWidgets.QLabel("SHIELD RANK")
            head.setStyleSheet(
                f"color: {theme.MUTED}; font-size: 10px; font-weight: 700; "
                f"letter-spacing: 1px;")
            flay.addWidget(head)
            for n, row in enumerate(rows):
                winner = n == 0
                mine = row["id"] == iid
                # the annotation for a class fit, else the shield's own first
                # real skill (Block says nothing — every shield has it)
                detail = row["why"] or next(
                    (s["name"] for s in row["skills"]
                     if s["name"] and s["name"] != "Block"), "")
                mark = "★" if winner else ("▸" if mine else " ")
                name = f"{mark} {row['name']}"
                if mine and not winner:
                    name += "  (equipped)"
                col = theme.ACCENT if winner else (theme.GOOD if mine
                                                  else theme.MUTED)
                # The reason gets its OWN line, not a share of one: a single
                # line elided "Fortifying Cry buffs every ally's Armor" down
                # to "For…", and the reason is the entire point of the block.
                nlbl = C.ElideLabel(name, 330, show_tooltip=False)
                nlbl.setStyleSheet(
                    f"color: {col}; font-size: 11px; font-weight: 700; "
                    f"background: transparent; border: none; padding: 0px 2px;")
                flay.addWidget(nlbl)
                if detail:
                    dlbl = C.ElideLabel(f"   {detail}", 330, show_tooltip=False)
                    dlbl.setStyleSheet(
                        f"color: {theme.MUTED}; font-size: 10px; "
                        f"background: transparent; border: none; padding: 0px 2px;")
                    flay.addWidget(dlbl)
            fit_box.show()
        except Exception:
            try:
                box._fit_box.hide()
            except Exception:
                pass

    def _update_weapon_card_display(self, box: QtWidgets.QFrame, iid: str | None, slot_idx: int) -> None:
        if slot_idx == 2 and is_2h_weapon(self._loadout_w1):
            box._is_locked = True
            box.setCursor(QtCore.Qt.ForbiddenCursor)
            box._badge.setText("LOCKED (2H)")
            box._badge.setStyleSheet(
                f"color: {theme.MUTED}; background: {theme.with_alpha(theme.MUTED, 20)}; "
                f"border-radius: 3px; padding: 1px 5px; font-family: '{theme.MONO_FONT}', monospace; font-size: 11px; font-weight: 700;")
            box._wname_lbl.setText("🔒 2H Weapon Equipped (Off-Hand Locked)")
            box._wname_lbl.setStyleSheet(f"color: {theme.MUTED}; font-weight: 700; font-size: 13px;")
            box._tile.set(None, None)
            box._trait_lbl.setText("🔒 Off-Hand is locked while a 2H weapon is equipped in Slot 1")
            try:
                box._ench_lbl.hide()
                box._fit_box.hide()
                box._stat_lbl.hide()
                box._why_lbl.hide()
            except Exception:
                pass
            return

        if slot_idx == 2:
            box._is_locked = False
            box.setCursor(QtCore.Qt.PointingHandCursor)
            box._badge.setText("100% STATS")
            box._badge.setStyleSheet(
                f"color: {theme.ACCENT}; background: {theme.with_alpha(theme.ACCENT, 20)}; "
                f"border-radius: 3px; padding: 1px 5px; font-family: '{theme.MONO_FONT}', monospace; font-size: 11px; font-weight: 700;")

        if not iid:
            box._wname_lbl.setText("Select Weapon…")
            box._wname_lbl.setStyleSheet(f"color: {theme.TEXT}; font-weight: 700; font-size: 14px;")
            box._tile.set(None, None)
            box._trait_lbl.setText("⚡ Select weapon to view traits & skills")
            try:
                box._ench_lbl.hide()
                box._fit_box.hide()
                box._stat_lbl.hide()
                box._why_lbl.hide()
            except Exception:
                pass
            return

        it = idata.item(iid) or {}
        nm = it.get("name") or iid
        rar = idata.item_display_rarity(iid)
        col = theme.rarity_color(rar)

        box._wname_lbl.setText(nm)
        box._wname_lbl.setStyleSheet(f"color: {col}; font-weight: 700; font-size: 14px;")
        box._tile.set("item", iid, col)
        # the ladder max, which is the rank the totals read it at
        cap = self._weapon_max_upgrades(iid)
        stat_txt = idata.format_item_stats_summary(iid, level=25,
                                                   upgrades=cap)
        if stat_txt:
            box._stat_lbl.setText(f"+{cap} · {stat_txt}" if cap else stat_txt)
            box._stat_lbl.show()
        else:
            box._stat_lbl.hide()

        traits = idata.item_special_traits(iid)
        if traits:
            t = traits[0]
            txt = t["text"] if t["text"] != t["title"] else t["title"]
            box._trait_lbl.setText(f"⚡ {txt} ({t['type']})")
        else:
            skills = idata.weapon_skills(iid)
            if skills:
                sk_names = " · ".join(s["name"] for s in skills[:2 if slot_idx == 3 else 4])
                box._trait_lbl.setText(f"⚔️ Skills: {sk_names}")
            else:
                box._trait_lbl.setText("⚡ Basic Weapon")

        self._update_weapon_ench_line(box, iid, slot_idx)
        self._update_weapon_role_why(box, iid, slot_idx)
        if slot_idx == 2 and idata.is_shield(iid):
            self._update_shield_rank(box, iid)

    def _update_weapon_role_why(self, box: QtWidgets.QFrame, iid: str,
                                slot_idx: int) -> None:
        """The card's `WHY` line: the phrases that made the ROLE pick this
        weapon, in the sheets' own words.

        Shown only for a weapon WE auto-picked, and only for the two main-hand
        slots — a hand-picked weapon was nobody's recommendation, and the
        off-hand answers the same question with the SHIELD RANK block beside
        it, so a second explanation there would just be noise. An auto-pick
        with no matching phrase stays hidden: the fit scored it 0, so it won
        on stats alone, and claiming a role reason for that would be inventing
        one.

        `set_full`, not `setText` — see `_update_weapon_ench_line`.
        """
        lbl = getattr(box, "_why_lbl", None)
        if lbl is None:
            return
        auto = getattr(self, f"{_WEAPON_ATTRS.get(slot_idx, '')}_auto", False)
        if not auto or slot_idx == 2:
            lbl.hide()
            return
        # no role chip is a DAMAGE build, the same default the fill uses
        role = getattr(self, "_loadout_role", "") or "damage"
        try:
            reasons = idata.weapon_role_reasons(iid, role)
        except Exception:
            reasons = ()
        if not reasons:
            lbl.hide()
            return
        head = f"◆ {idata.role_label(role).upper()} FIT"
        lbl.set_full(f"{head}  " + " · ".join(reasons))
        lbl.show()

    def _update_weapon_ench_line(self, box: QtWidgets.QFrame, iid: str, slot_idx: int) -> None:
        """Show DemonGearUpgrade conversions matching the weapon's ratings
        (Enchants tab data). Arsenal applies augments at 50% like stats."""
        lbl = getattr(box, "_ench_lbl", None)
        if lbl is None:
            return
        try:
            ratings = set(idata.gear_ratings(iid) or ())
            convs = [c for c in (idata.enchant_conversions() or [])
                     if (c.get("source") or "") in ratings]
        except Exception:
            convs = []
        if not convs:
            lbl.hide()
            return
        parts = []
        for c in convs[:3]:
            try:
                val = (c.get("rare") or {}).get("value", 20)
                parts.append(f"{c['source']}→{c['target']} +{val}")
            except Exception:
                continue
        if not parts:
            lbl.hide()
            return
        extra = f" +{len(convs) - 3} more" if len(convs) > 3 else ""
        half = " · 50% on Arsenal" if slot_idx == 3 else ""
        # set_full, not setText: ElideLabel re-elides from its cached full
        # text on resize, so a plain setText would be wiped on the next pass
        lbl.set_full(f"🔄 {' · '.join(parts)}{extra}{half}")
        lbl.show()


_GEAR_SLOT_NAMES = (
    ("Head", "🪖", "Head"),
    ("Shoulders", "🛡️", "Shoulders"),
    ("Chest", "🥋", "Chest"),
    ("Hands", "🥊", "Hands"),
    ("Waist", "🎗️", "Waist"),
    ("Legs", "👖", "Legs"),
    ("Feet", "🥾", "Feet"),
    ("Neck", "📿", "GearNeck"),
    ("Ring 1", "💍", "GearFinger"),
    ("Ring 2", "💍", "GearFinger"),
    ("Trinket", "🧿", "GearTrinket"),
)


def is_2h_weapon(item_id: str | None) -> bool:
    """True if item_id is a 2-Handed weapon (Greatsword, Staff, GreatAxe,
    Greathammer, Polearm, Bow, Crossbow).

    A re-export (2026-09-26): the rule moved to the data layer
    (`data.items.labels.is_two_handed`) because the Player Inspect doll needs
    it too, and two pages should not both import the other's dialog. The name
    stays so no call site in the Items page moved.
    """
    return glabels.is_two_handed(item_id)


class _ItemPickerDialog(QtWidgets.QFrame):
    """Filterable item picker popout for selecting weapons or gear slots with icons & class chips."""

    item_selected = QtCore.Signal(str)   # item_id
    closed = QtCore.Signal()             # emitted on any close (pick, x, click-away)

    def closeEvent(self, ev) -> None:
        try:
            self.closed.emit()
        finally:
            super().closeEvent(ev)

    def __init__(self, category: str, slot_type: str | None = None,
                 current_id: str | None = None,
                 elsewhere_ids: tuple[str, ...] = (),
                 class_filter: str = "", parent=None):
        """`elsewhere_ids` are the pieces already equipped in a slot that
        shares this one's group (the other hand / the Arsenal, the other
        finger): they stay listed — picking one MOVES it here — but the row
        says so, so the move is never a surprise.

        `class_filter` is the Loadout page's class, applied to the list. The
        page already knows which class the build is before this popout ever
        exists, and picking a weapon is a per-class decision — so the list
        opens on that class instead of every class's, which used to cost a
        hand-click on the popout's own chip bar to narrow. That bar is gone:
        the page's chip bar is the single control (it is what auto-fills the
        slots and what the gear rows keep), so the popout shows its effect
        and no longer carries a second control that could disagree with it.
        An unknown class is ignored rather than emptying the list."""
        self._elsewhere = {str(i) for i in elsewhere_ids if i}
        super().__init__(parent)
        self.setObjectName("ItemPopout")
        self.setWindowFlags(QtCore.Qt.Popup | QtCore.Qt.FramelessWindowHint)
        self.setAttribute(QtCore.Qt.WA_StyledBackground, True)
        self.resize(580, 460)
        self.setStyleSheet(
            f"#ItemPopout {{ background: {theme.BG}; border: 1px solid {theme.ACCENT}; border-radius: 8px; }}\n"
            # `.QFrame` (exact class): a bare `QFrame` rule cascades to every
            # QLabel inside (QLabel IS-A QFrame) — the SELECT <SLOT> title
            # and the result count drew their own 1px boxes
            f".QFrame {{ background: {theme.PANEL}; border: 1px solid {theme.BORDER}; border-radius: 6px; }}\n"
            f"QListWidget {{ background: {theme.PANEL}; border: 1px solid {theme.BORDER}; border-radius: 6px; padding: 4px; }}\n"
            f"QListWidget::item {{ padding: 6px 10px; border-radius: 4px; color: {theme.TEXT}; }}\n"
            f"QListWidget::item:hover {{ background: {theme.PANEL_HI}; }}\n"
            f"QListWidget::item:selected {{ background: {theme.with_alpha(theme.ACCENT, 40)}; color: #fff; }}")

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(10)

        # Header
        hdr = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel(f"SELECT {slot_type or category}".upper())
        title.setStyleSheet(f"color: {theme.ACCENT}; font-weight: 800; font-size: 13px; background: transparent; border: none;")
        hdr.addWidget(title, 1)

        close_btn = QtWidgets.QPushButton("Close ✕")
        close_btn.setToolTip("Close")
        close_btn.setCursor(QtCore.Qt.PointingHandCursor)
        close_btn.setMinimumHeight(28)
        close_btn.setStyleSheet(
            f"QPushButton {{ background: {theme.PANEL_HI}; color: {theme.TEXT}; border: 1px solid {theme.ACCENT}; border-radius: 4px; padding: 4px 12px; font-weight: 800; font-size: 12px; }}\n"
            f"QPushButton:hover {{ background: {theme.with_alpha(theme.ACCENT, 30)}; color: {theme.ACCENT}; }}")
        close_btn.clicked.connect(self.close)
        hdr.addWidget(close_btn)
        lay.addLayout(hdr)

        # The class filter is the Loadout page's chip bar, not this popout's:
        # both were on screen at once, side by side, controlling the same
        # thing. The page's chip is the one control — it is what auto-fills
        # the slots and what the gear rows keep — so the popout takes its
        # value and offers no second, diverging one.
        self._class_filter = (class_filter
                              if class_filter in idata.gear_classes() else "")

        # Search Bar
        self._search = C.SearchInput("Search item name, stat or skill…", on_text_changed=self._refilter)
        self._search.setFixedHeight(30)
        lay.addWidget(self._search)

        # Item List
        self._list = QtWidgets.QListWidget()
        self._list.setIconSize(QtCore.QSize(32, 32))
        self._list.itemClicked.connect(self._on_item_clicked)
        self._list.itemDoubleClicked.connect(self._on_item_clicked)
        lay.addWidget(self._list, 1)

        # Fetch items
        all_items = idata.items()
        if category == "Weapons":
            # same junk rule the gear slots use: no Common, no Uncommon, no
            # level-0 rows. A no-op on today's catalog (all 36 weapons are
            # display-Rare — the town ones are authored Uncommon but sell as
            # Rare), and it is here so a Common weapon added later cannot
            # turn up in a slot the loadout would throw straight back out.
            #
            # `slot_type` narrows WITHIN the category: the off-hand asks for
            # "Shield", so the popout lists shields instead of every weapon
            # in the game. Without it the off-hand is a one-hander slot
            # wearing a shield slot's name.
            self._items = [it for it in all_items
                           if idata.category(it.get("type")) == "Weapons"
                           and self._is_viable_gear(it, False)
                           and (not slot_type
                                or (it.get("type") or "") == slot_type)]
        else:
            is_jewelry = (slot_type or "") in ("GearNeck", "GearFinger", "GearTrinket")
            if slot_type:
                self._items = [it for it in all_items
                               if (it.get("type") or "") == slot_type
                               and self._is_viable_gear(it, is_jewelry)]
            else:
                self._items = [it for it in all_items
                               if idata.is_gear(it) and self._is_viable_gear(it, False)]

        self._refilter()

    @staticmethod
    def _is_viable_gear(it: dict, is_jewelry: bool) -> bool:
        """Same junk-exclusion rule as the main loadout select logic: drop
        Common/Uncommon level-0 gear (Uncommon jewelry is kept for flex
        choices)."""
        iid = it.get("id") or ""
        if not iid or (it.get("level") or 0) == 0:
            return False
        rar = idata.item_display_rarity(iid)
        if is_jewelry:
            return rar != "Common"
        return rar not in ("Common", "Uncommon")

    def popup_at(self, anchor_widget: QtWidgets.QWidget | None = None) -> None:
        """Position and present the popout anchored near the trigger button or centered over parent window."""
        self.adjustSize()
        w, h = 580, 460
        self.resize(w, h)

        if anchor_widget and anchor_widget.isVisible():
            global_pos = anchor_widget.mapToGlobal(QtCore.QPoint(0, anchor_widget.height() + 4))
            screen = QtWidgets.QApplication.screenAt(global_pos) or QtWidgets.QApplication.primaryScreen()
            if screen:
                sg = screen.availableGeometry()
                x = global_pos.x()
                if x + w > sg.right() - 10:
                    x = anchor_widget.mapToGlobal(QtCore.QPoint(anchor_widget.width(), 0)).x() - w
                x = max(sg.left() + 10, min(x, sg.right() - w - 10))

                y = global_pos.y()
                if y + h > sg.bottom() - 10:
                    y = anchor_widget.mapToGlobal(QtCore.QPoint(0, 0)).y() - h - 4
                y = max(sg.top() + 10, min(y, sg.bottom() - h - 10))
                self.move(x, y)
        else:
            parent = self.parentWidget() or QtWidgets.QApplication.activeWindow()
            if parent:
                geo = parent.geometry()
                global_center = parent.mapToGlobal(QtCore.QPoint(geo.width() // 2, geo.height() // 2))
                self.move(global_center.x() - w // 2, global_center.y() - h // 2)

        self._clicks_locked_until = time.monotonic() + 1.0
        self.show()
        self.raise_()
        self.activateWindow()

    def _refilter(self, query: str = "") -> None:
        q = (self._search.text() or "").strip().lower()
        self._list.clear()

        for it in self._items:
            iid = it["id"]
            nm = it.get("name") or iid
            cls_list = it.get("classes") or []

            # Class filter match
            if self._class_filter and cls_list and self._class_filter not in cls_list:
                continue

            # Text search match
            if q:
                match_nm = q in nm.lower() or q in iid.lower()
                match_sk = idata.matches_skill(iid, q)
                match_st = idata.matches_stat(iid, q)
                if not (match_nm or match_sk or match_st):
                    continue

            rar = idata.item_display_rarity(iid)
            col = theme.rarity_color(rar)
            stats_txt = idata.format_item_stats_summary(iid, level=25)
            in_coll = pdata.is_in_owned_collection(iid)
            coll_tag = "  [✓ Collection]" if in_coll else ""

            moved = "  [⇄ equipped]" if iid in self._elsewhere else ""
            # The rarity LEADS the row in words, not just in the row's
            # colour: the colour is the one thing a glance cannot resolve,
            # and a popout of thirty weapons is exactly where you want to
            # know the tier before the name. The stats beside it are the
            # piece's own, scaled to 25 — the level the loadout is locked to.
            head = f"{rar} {nm}" if rar else nm
            li = QtWidgets.QListWidgetItem(f"{head}  ({stats_txt}){coll_tag}{moved}")
            li.setForeground(QtGui.QColor(col))
            li.setIcon(QtGui.QIcon(icons.tile("item", iid, 36, col)))
            li.setData(QtCore.Qt.UserRole, iid)
            self._list.addItem(li)

    def _on_item_clicked(self, item: QtWidgets.QListWidgetItem) -> None:
        if time.monotonic() < getattr(self, "_clicks_locked_until", 0.0):
            return
        iid = item.data(QtCore.Qt.UserRole)
        if iid:
            self.item_selected.emit(iid)
            self.close()
