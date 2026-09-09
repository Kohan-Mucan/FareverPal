"""Decoded combat-event application service for the shared DPS tracker."""
from __future__ import annotations

from ..data import units as udata
from .damage_events import clean_affinity, clean_wire_name
from .dps_data import (
    DamageEvent,
    K_DAMAGE,
    K_HEAL,
    K_SHIELD,
)
from .dps_tracker_attribution import (
    _GENERIC_PLAYER_LABELS,
    _canonical_unit_display,
    _in_instance,
    solo_status,
)

# What the event path did with one event. Named rather than boolean because the
# capture diagnostic counts them, and the difference between the two refusals IS
# the diagnosis: "unresolved" means a good event arrived and the reading of WHO
# did what failed (a name/roster problem), while a plain "dropped" is the event
# path declining to mint a row it cannot stand behind. A zero-reading meter
# cannot tell those apart from the outside.
EVENT_ATTRIBUTED = "attributed"
EVENT_UNRESOLVED = "unresolved"
EVENT_DROPPED = "dropped"
# Deliberately NOT recorded, and not a fault either: the Solo Open-World filter
# (a DISPLAY filter — the events are still counted as not-yours), the post-wipe
# re-arm hold, and zero-amount kill notifications, which confirm a boss kill and
# carry no damage. They get their own bucket because folding them into "dropped"
# would make a healthy solo session look lossy — the difference between "the
# meter is working and is not showing a stranger" and "the meter is losing
# events" is the whole point of the report.
EVENT_FILTERED = "filtered"


class DpsTrackerEvents:
    """Apply decoded events to the owning tracker sessions."""

    def __init__(self, tracker):
        self.tracker = tracker

    def apply_event(self, ev, pa, heroes, foes, now):
        """Route one decoded event; returns its DISPOSITION (see _events_apply_event)."""
        return self._events_apply_event(ev, pa, heroes, foes, now)

    def confirm_boss_kill(self, ev, now):
        return self._events_confirm_boss_kill(ev, now)

    def apply_damage_taken(self, tgt, amount, pa, heroes, now, **kwargs):
        return self._events_apply_damage_taken(tgt, amount, pa, heroes, now, **kwargs)

    def apply_heal(self, ev, amount, src, tgt, pa, heroes, now, **kwargs):
        return self._events_apply_heal(ev, amount, src, tgt, pa, heroes, now, **kwargs)

    def apply_damage(self, ev, amount, src, tgt, pa, heroes, foes, now, **kwargs):
        return self._events_apply_damage(ev, amount, src, tgt, pa, heroes, foes, now, **kwargs)

    def _proxy_attribution_key(self, ev, source_name: str):
        mode = str(getattr(ev, "capture_mode", "") or "")
        if mode not in ("proxy", "injector"):
            return None
        target = str(getattr(ev, "target_name", "") or "unknown target")
        return (mode, (source_name or "missing caster").strip().lower(),
                target.strip().lower())

    def _warn_proxy_unresolved(self, ev, source_name: str,
                               disposition: str) -> None:
        """One Activity Log line per unresolved bridge source/target."""
        tracker = self.tracker
        key = self._proxy_attribution_key(ev, source_name)
        if key is None or key in tracker._proxy_unresolved_logged:
            return
        if len(tracker._proxy_unresolved_logged) >= 32:
            tracker._proxy_unresolved_logged.clear()
        tracker._proxy_unresolved_logged.add(key)
        mode = "proxy" if key[0] == "proxy" else "injector bridge"
        source = source_name or "missing caster"
        target = str(getattr(ev, "target_name", "") or "unknown target")
        skill = str(getattr(ev, "skill_name", "") or getattr(ev, "skill", "")
                    or "unknown skill")
        if callable(tracker.log_line):
            try:
                tracker.log_line(
                    f"Top DPS: {mode} attribution unresolved — {source} on "
                    f"{target} ({skill}); {disposition}")
            except Exception:
                pass

    def _clear_proxy_unresolved(self, ev, source_name: str) -> None:
        key = self._proxy_attribution_key(ev, source_name)
        if key is not None:
            self.tracker._proxy_unresolved_logged.discard(key)

    def _events_confirm_boss_kill(self, ev: DamageEvent, now: float) -> None:
        self = self.tracker
        """Pure kill notification: the killed unit's raw id and nothing else.

        The bridge's death hook (ent.Foe.onDie) sends it once per death, with
        no killer and no amount, so it can never feed damage rows. Its value is
        boss-KILL CONFIRMATION:
        when the id matches the ACTIVE boss, arm the boss-end grace, so a boss
        that dies and despawns before its HP ever reads 0 still closes the
        encounter as "Boss Defeated" instead of "Boss Fight".

        The wire id is RAW and the stored one is canonical, so the id compare
        reduces both with `data.units.canonical_unit_id` first - see the note
        in the body.
        """
        if not self.in_boss_fight or self.boss_session.group_damage <= 0:
            return
        uid_raw = (getattr(ev, "target_name", "") or "").strip()
        if not uid_raw:
            return
        boss = self.boss_session
        # Two shapes of the SAME field meet here. What the tracker stored is
        # already canonical - the scene walk cleaned the unitId on the way in -
        # while the bridge sends it RAW: it reads the dying unit's own unitId
        # in C and puts it on the wire as it found it (`Units/Enemies/Wolf/
        # Wolf_Z1W.prefab`, `Boss_Foo(Clone)`). Comparing those verbatim is why
        # path-like or clone-suffixed boss could only ever confirm by display
        # name - the id route was silently failing on precisely the deaths the
        # notification exists for. Reducing both with the one canonicalizer
        # makes the two agree by construction, for a name that shares nothing
        # with the id included.
        uid = udata.canonical_unit_id(uid_raw) or uid_raw
        raw = udata.canonical_unit_id(self._foe_raw_ids.get(boss.target_addr))
        # The display name is a DIFFERENT field, not a second id: left as the
        # last resort, for the deaths where the bridge could not read a unitId
        # at all and sent the unit's name instead. It is compared raw - a name
        # may legitimately carry its own parentheses.
        disp = (boss.target_name or "").replace("👑 ", "").strip()
        if (raw and uid.lower() == raw.lower()) \
                or (disp and uid_raw.lower() == disp.lower()):
            if self._boss_kill_pending <= 0.0 and callable(self.log_line):
                try:
                    self.log_line("Top DPS: boss defeat confirmed by kill notification")
                except Exception:
                    pass
            self._boss_kill_pending = now

    def _events_apply_event(self, ev: DamageEvent, pa: int,
                     heroes: dict[int, tuple[str, bool, tuple[float, float, float], str]],
                     foes: dict[int, tuple[float, str, bool, float, float, float]],
                     now: float):
        self = self.tracker
        if self._death_rearm_wait:
            return EVENT_FILTERED
        amt = ev.amount
        if not amt or amt != amt:
            # Zero-amount kill notification: confirm a boss kill, nothing else.
            if ev.kill:
                self._confirm_boss_kill(ev, now)
            return EVENT_FILTERED
        amount = amt
        ev_t = getattr(ev, "t", 0.0)   # capture stamp: keeps drain delays honest
        tgt = ev.target_addr
        src = ev.source_addr
        # Pets carry the pet as serverSource; re-credit to the owning hero.
        from_pet = bool(src and src in self._pet_owners)
        if from_pet:
            src = self._pet_owners[src] or 0

        # Solo Open-World Filter: when ungrouped in the open world, an event
        # that is NOT yours may only be recorded while you are actually in
        # combat. That is enough to keep a stranger's fight two fields over
        # from opening (or padding) your session while you stand idle.
        #
        # A rival who is genuinely in YOUR fight stays tracked even though the
        # meter hides the row: the Solo filter is a DISPLAY filter, not a data
        # filter. Discarding their events here used to leave a hole - leave a
        # group, or flip Solo Only off, and the ally's parse was simply gone
        # instead of reappearing, while the analysis page (which never
        # filtered) showed numbers the overlay could not explain.
        if not _in_instance(self.model) and solo_status(self.model, self) is True and pa:
            # A bridge event the game itself attributes to the local player
            # (the DLL's isMe record) — or addressed to/from the local hero —
            # is combat proof on its own: the isInCombat memory flag can go
            # stale after a game update while the hook stream stays live, and
            # requiring it would blackhole real solo DPS (Activity Log shows
            # HITs, meter stays empty). Other events still need the flag.
            _self_proven = (bool(getattr(ev, "is_me", False))
                            or src == pa or tgt == pa)
            if not _self_proven:
                st = self.combat_state
                in_cb = st.get("in_combat") if st is not None else None
                if in_cb is not True:
                    try:
                        live_st = self.model.combat_state() if self.model else None
                        if live_st:
                            in_cb = live_st.get("in_combat")
                    except Exception:
                        pass
                if in_cb is not True:
                    return EVENT_FILTERED
        # Direct name path: hooked events already have authoritative caster/target names.
        if getattr(ev, "source_name", ""):
            if ev.incoming:
                t_name = getattr(ev, "target_name", "") or "Player"
                return self._apply_damage_taken(
                    tgt, amount, pa, heroes, now, target_name=t_name,
                    is_me=getattr(ev, "is_me", False), when=ev_t,
                    ev=ev, caster_addr=src)
            if ev.kind in (K_HEAL, K_SHIELD) or amt < 0:
                return self._apply_heal(ev, abs(amount), src, tgt, pa, heroes,
                                        now, from_pet=from_pet, when=ev_t)
            return self._apply_damage(ev, amount, src, tgt, pa, heroes, foes,
                                      now, from_pet=from_pet, when=ev_t)

        # --- classify: heal / incoming damage / outgoing damage ----------#
        kind = None
        if amt < 0:
            kind = K_HEAL
            amount = -amt
        elif ev.kind in (K_HEAL, K_SHIELD):
            kind = K_HEAL
            amount = abs(amount)
        # Known heroes include roster/pointer/cache heroes, not just the scene (far-ally guard).
        def _is_known_hero(addr: int) -> bool:
            if not addr:
                return False
            if addr == pa or addr in heroes:
                return True
            hp = self._hero_pointers.get(addr)
            if hp is not None and not hp[0].startswith("Party_"):
                return True
            cached = self._hero_name_cache.get(addr)
            return bool(cached and not cached.startswith("Party_"))

        if src in heroes and (tgt in heroes or tgt == pa):
            kind = K_HEAL                    # friendly number over a hero
        elif _is_known_hero(src) and _is_known_hero(tgt):
            kind = K_HEAL                    # far-ally heal (off-scene hero)
        elif kind == K_HEAL:
            # A heal/shield onto a hero from a source that is not a known
            # hero: never "incoming damage". Re-credit pets/summons to their
            # owner (an unscanned pet heal resolves through the model exactly
            # like pet damage does); a probe miss leaves src untouched so the
            # heal still lands on its own channel instead of a damage-taken row.
            if not from_pet:
                from_pet, src = self._map_pet_source(src, pa, heroes, now)
        elif tgt == pa or (tgt in heroes) or ev.incoming or _is_known_hero(tgt):
            # Incoming: enemy hit on a hero (hero-on-hero already classified as heal).
            if tgt and (tgt in heroes or _is_known_hero(tgt)):
                h_addr = tgt
            else:
                h_addr = pa
            return self._apply_damage_taken(
                h_addr, amount, pa, heroes, now,
                target_name=getattr(ev, "target_name", ""),
                is_me=getattr(ev, "is_me", None), when=ev_t,
                ev=ev, caster_addr=src)
        elif tgt in self._pet_owners and self._pet_owners[tgt] in heroes:
            # a hit on my pet counts as damage taken by its owner
            owner = self._pet_owners[tgt]
            return self._apply_damage_taken(owner, amount, pa, heroes, now,
                                             is_me=(owner == pa), when=ev_t,
                                             ev=ev, caster_addr=src)
        else:
            # Outgoing: try owned-proc re-credit, then pet probe (foes never resolve).
            if not from_pet:
                own = self._own_proc_source(src, ev.skill, pa)
                if own:
                    src = own
                else:
                    from_pet, src = self._map_pet_source(src, pa, heroes, now)
            kind = K_DAMAGE

        if kind == K_HEAL:
            # Pet heals report the pet too; hero sources pass through untouched.
            if not from_pet:
                from_pet, src = self._map_pet_source(src, pa, heroes, now)
            return self._apply_heal(ev, amount, src, tgt, pa, heroes, now,
                                    from_pet=from_pet, when=ev_t)
        return self._apply_damage(ev, amount, src, tgt, pa, heroes, foes, now,
                                  from_pet=from_pet, when=ev_t)

    def _events_apply_damage_taken(self, tgt: int, amount: float, pa: int,
                            heroes: dict[int, tuple[str, bool, tuple[float, float, float], str]],
                            now: float,
                            target_name: str = "", is_me: bool | None = None,
                            when: float = 0.0, ev: DamageEvent | None = None,
                            caster_addr: int = 0):
        self = self.tracker
        """Record damage taken by a hero into the active segment + overall.

        ``caster_addr``/``ev`` are what the boss's OWN hits arrive with. The
        outgoing path only ever sees damage dealt to a foe, so everything a boss
        does to the party lands here — and without the caster this half of the
        fight was simply not recorded anywhere: the logs said what the party did
        and nothing about what it cost them.
        """
        if target_name and target_name not in ("Enemy", ""):
            name = target_name
            if is_me is None:
                is_me = (target_name in ("Player", "You")
                         or (pa and self._resolve_hero_name(pa, True) == target_name))
            if not is_me:
                # Bridge names you correctly while its isMe record lies.
                me_match = self._match_me(name, pa)
                if me_match is not None:
                    name, is_me = me_match, True
            if is_me and pa:
                loc = self._resolve_hero_name(pa, True)
                if loc:
                    name = loc
        else:
            name = self._hero_name(tgt, pa, heroes)
            if not name and pa:
                name = self._resolve_hero_name(pa, True)
            if not name:
                name = "You" if (is_me or (tgt and tgt == pa)) else "Player"
            if is_me is None:
                is_me = (tgt == pa or name == "You")

        if not name:
            return EVENT_DROPPED
        # A stranger's incoming damage that we can only name "Player" (the
        # game's generic label for an ungrouped rival) must not mint a fake
        # row on the meter — that surfaces someone else's world DPS before
        # solo filtering runs. Real party members always resolve to real names.
        if not is_me and name in _GENERIC_PLAYER_LABELS:
            return EVENT_DROPPED
        # `Party_XXXX` is not a name — it means we failed to identify the hero
        # at this address, so minting it as a player row invents a party member
        # (live 2026-09-19: zone-in listed 2-3 placeholder rows carrying taken
        # damage). Party members resolve through the st.Group roster, which the
        # tracker seeds into `_hero_pointers`, so an unidentified target here is
        # a proc/status object or an ungrouped rival — never a nameable team
        # member. Named rows are untouched; only the placeholder is refused.
        if not is_me and str(name).startswith("Party_"):
            return EVENT_DROPPED
        if self.in_boss_fight:
            self.boss_session.record_damage_taken(name, amount,
                                                  is_me=is_me, when=when)
            # ...and the same hit credited to the BOSS's own row, so the fight
            # records both sides (see `CombatSession.note_boss_damage`). Only
            # the engaged boss's own hits qualify: a random foe's swing at a
            # player is not the boss, and crediting it to the boss row would
            # inflate the one number that is supposed to be about the boss.
            if ev is not None and self.events._is_engaged_boss_hit(ev, caster_addr):
                sid, sname, _pet_tag = self._skill_labels(
                    ev, fallback=("Attack", "Attack"))
                self.boss_session.note_boss_damage(sid, sname, amount,
                                                   is_crit=bool(ev.crit))
        elif self.in_dummy_fight:
            # A dummy test is its own segment on the TAKEN channel too, exactly
            # as the outgoing path routes dummy damage (`on_dummy` in
            # _events_apply_damage). Without this half the two channels of one
            # test disagreed: your hits were on the dummy board while anything
            # that hit you went to trash, so a test's "damage taken" read 0.0
            # no matter what actually landed on you — and a clean-run claim
            # built on that number would be a claim the tracker could not make.
            self.dummy_session.record_damage_taken(name, amount,
                                                   is_me=is_me, when=when)
        elif getattr(self, "_instance_now", False):
            # Trash is a DUNGEON/RIFT segment only (live 2026-10-02): an
            # open-world swing at the player has no trash board to belong
            # to, so the TAKEN channel leaves it on the overall board
            # below — exactly as the outgoing path already gates its own
            # trash branch on the same flag.
            self.trash_session.record_damage_taken(name, amount,
                                                   is_me=is_me, when=when)
        self.overall_session.record_damage_taken(name, amount,
                                                 is_me=is_me, when=when)
        return EVENT_ATTRIBUTED

    def _is_engaged_boss_hit(self, ev: DamageEvent, caster_addr: int) -> bool:
        """Did the ENGAGED boss land this hit on the party?

        Two ways to answer, because the two event sources disagree about what
        they can prove:

        * by address, when the event carries one (the scan path, and any
          caller that resolved the caster through the scene);
        * by name, because the BRIDGE never sends addresses at all. The DLL
          resolves the caster's name inside the game and puts it in ``src``
          (bridge_core.h `send_hit_event`), and `parse_hook_event` leaves
          ``source_addr`` at 0 - so an address-only test could never be
          satisfied on the wire, and the boss row stayed empty for every real
          fight even though `note_boss_damage` and its whole archive round-trip
          existed to fill it.

        The name is matched against the engaged boss's RAW ID first (the wire
        speaks ids; the session label is the display spelling, which need not
        match), then against that label, with the same containment the damage
        path uses for `is_boss_named`. `"Enemy"` and friends never match: the
        bridge's own placeholder is not evidence that the boss did it.
        """
        tr = self.tracker
        b_addr = tr.boss_session.target_addr
        if b_addr and caster_addr == b_addr:
            return True
        caster = clean_wire_name(getattr(ev, "source_name", ""))
        if not caster or caster in _GENERIC_PLAYER_LABELS:
            return False
        c = caster.lower()
        raw = (tr._foe_raw_ids.get(b_addr) or "").strip().lower()
        if raw and c == raw:
            return True
        boss_label = (tr.boss_session.target_name or "").replace("👑", "").strip().lower()
        if not boss_label or boss_label == "none":
            return False
        return c == boss_label or c in boss_label or boss_label in c

    def _events_apply_heal(self, ev: DamageEvent, amount: float, src: int, tgt: int, pa: int,
                    heroes: dict[int, tuple[str, bool, tuple[float, float, float], str]],
                    now: float, from_pet: bool = False, when: float = 0.0):
        self = self.tracker
        # Junk off the wire ("\u0003", "") is not a name: filtered here as well
        # as in the parser, so no path can mint a row under a control character.
        caster, is_me, event = self._attribute_caster(ev, src, pa, heroes)
        if event is not None:
            return event
        if getattr(ev, "target_name", ""):
            target_name = ev.target_name
            if (target_name in ("Player", "Hero") or not target_name) and is_me and pa:
                loc = self._resolve_hero_name(pa, True)
                if loc:
                    target_name = loc
        else:
            target_name = self._hero_name(tgt, pa, heroes) or (self._foe_names.get(tgt) or "Hero")
        sid, sname, wire_pet = self._skill_labels(ev, fallback=("Regen", "Heal"))
        from_pet = from_pet or wire_pet
        if from_pet:
            sname = f"{sname} (pet)"
        # The zone has to be one a dummy can exist in at all: an instance unit
        # that merely LOOKS like one is trash in there, and the scan applies the
        # same gate to the same unit (core/dps_dummy.py, `dummy_zone_ok`).
        # Without it the raw-id route below would engage a dummy test nothing
        # else in the tick reports.
        is_dummy = self.dummy_zone_ok() and (
            udata.is_training_dummy(target_name)
            or (tgt and udata.is_training_dummy(self._foe_raw_ids.get(tgt))))
        boss = self.in_boss_fight
        # Engage dummy fight on event path (sceneless injection)
        # Canonical display spelling (same rule as _apply_damage): a heal
        # event can name the dummy by raw id; its session label must match
        # the scan's "🎯 <display name>" form instead of flipping spellings.
        if is_dummy:
            target_name = _canonical_unit_display(target_name)
        # A hit on the dummy a FINISHED test belongs to re-arms it (the test's
        # own end signals latch the engagement off; see
        # dps_dummy._watch_dummy_reengage).
        if is_dummy:
            self._watch_dummy_reengage(getattr(ev, "t", 0.0) or 0.0, tgt or 0)
        # The dummy half of the gate: a heal on a STOPPED test must not
        # resurrect it behind the user's back (see core/dps_dummy.py).
        if is_dummy and self.dummy_may_engage():
            # Guarded prepend: the canonical display can pass through an
            # already-emblemed scan form ("🎯 Training Dummy"), which stored
            # a doubled "🎯 🎯 ..." label (same guard as the damage path).
            _dummy_label = target_name if target_name.startswith("🎯 ") \
                else f"🎯 {target_name}"
            # One engage, one reset (core/dps_dummy._begin_dummy_test): the
            # target-reached latch MUST be cleared here too, or a pet's next
            # hit after a finished test ends the fresh one instantly.
            self._begin_dummy_test(_dummy_label)
        # Which segment owns this heal. A dummy test is its own segment on the
        # HEALING channel exactly as it already is on TAKEN (the incoming branch
        # above routes a hit on you into the test): a dummy never needs healing,
        # so a test's heals are the rotation's own sustain - yours, and anything
        # landing on you. Without this the Test Dummy HUD's healing read 0.0
        # through a whole sustain run, because the heal landed on you and went
        # to the open-world trash segment instead of to the test being measured
        # (live 2026-10-01: "healing and damage taken not working" on a board
        # that showed neither number).
        test_heal = (bool(self.in_dummy_fight) and not is_dummy
                     and (bool(is_me)
                          or bool(self._match_me(target_name, pa))))
        if is_dummy:
            segment = self.dummy_session
        elif boss:
            segment = self.boss_session
        elif test_heal:
            segment = self.dummy_session
        elif getattr(self, "_instance_now", False):
            # Trash is a DUNGEON/RIFT segment on the HEALING channel too (live
            # 2026-10-02): an open-world heal has no trash board to belong to,
            # so it stays on the overall board below — exactly as the outgoing
            # (damage) and incoming (damage-taken) paths already gate their own
            # trash branches on the same flag. Without this gate open-world
            # sustain (a HoT rolling through a zone transition included) was
            # the one event family that still fed `trash_session` outside an
            # instance, so the DPS Analysis Trash segment opened carrying
            # heals from a fight that never happened in the dungeon.
            segment = self.trash_session
        else:
            segment = None
        if segment is not None:
            segment.record_heal(target_name=target_name, heal_amount=amount,
                                caster_name=caster, is_me=is_me,
                                skill_id=sid, skill_name=sname, when=when)
        self.overall_session.record_heal(target_name=target_name, heal_amount=amount,
                                         caster_name=caster, is_me=is_me,
                                         skill_id=sid, skill_name=sname, when=when)
        return EVENT_ATTRIBUTED

    def _events_apply_damage(self, ev: DamageEvent, amount: float, src: int, tgt: int,
                      pa: int,
                      heroes: dict[int, tuple[str, bool, tuple[float, float, float], str]],
                      foes: dict[int, tuple[float, str, bool, float, float, float]],
                      now: float, from_pet: bool = False, when: float = 0.0):
        self = self.tracker
        # Junk off the wire ("\u0003", "") is not a name: filtered here as well
        # as in the parser, so no path can mint a row under a control character.
        caster, is_me, event = self._attribute_caster(ev, src, pa, heroes)
        if event is not None:
            return event
        if getattr(ev, "target_name", ""):
            target_name = ev.target_name
        else:
            target_name = self._foe_name(tgt, foes) or self._hero_name(tgt, pa, heroes) or "Enemy"

        clean_tgt = (target_name or "").replace("👑 ", "").strip().lower()
        clean_boss = (self.boss_session.target_name or "").replace("👑 ", "").strip().lower()

        # The containment arms are a SPELLING fallback (one side a fragment of
        # the other). A target that names a DIFFERENT unit the data layer knows
        # is not a spelling of the boss: encounters name their adds after the
        # boss they belong to (`Nepsilon_Totem` is not `Nepsilon`), and the
        # containment used to route the totem's hits onto the boss board and
        # (before the session pin was fixed) into the archived name - live
        # 2026-10-02, `Tidal Totem_<profile>.json` holding a Nepsilon pull.
        tgt_uid = udata.canonical_unit_id(target_name) if target_name else None
        boss_uid = udata.canonical_unit_id(
            self._foe_raw_ids.get(self.boss_session.target_addr))
        other_known_unit = bool(
            tgt_uid and udata.is_known_unit(tgt_uid)
            and (not boss_uid or tgt_uid.lower() != boss_uid.lower()))
        is_boss_named = bool(
            clean_tgt and clean_boss and clean_boss != "none" and (
                clean_tgt == clean_boss
                or (not other_known_unit
                    and (clean_tgt in clean_boss or clean_boss in clean_tgt))
            )
        )
        # ...and only outside an instance: the same unit is ordinary trash
        # inside a dungeon/rift (core/dps_dummy.py, `dummy_zone_ok`), which is
        # also the gate the scan applied to it earlier in the same tick.
        is_dummy_unit = self.dummy_zone_ok() and bool(
            udata.is_training_dummy(target_name)
            or udata.is_training_dummy(clean_tgt)
            or udata.is_training_dummy(self._foe_raw_ids.get(tgt))
        )
        # udata.is_boss is the ONE authority for "is this a boss" (see
        # DpsTrackerTick._scan_foes). This used to repeat its DemonSuperElite
        # prefix rule minus the _Guardian exclusion, so the Guardian - an add
        # by the data layer's own note - routed its damage to boss_session and
        # split the real boss's fight. `"boss" in clean_tgt` is gone too: a
        # substring is not a boss test, and across all 530 shipped units it
        # matched exactly one id - R1KoboldBoss_Sparkling, "Snack of Ratsar
        # (Spark)", a Golem trash mob. Boss ids the sheets predate belong in
        # `units._CURATED_RIFT_BOSSES`; an instance's declared boss comes from
        # `dungeons.declared_boss_id`.
        # `clean_tgt` is lower-cased (above) and `declared_boss_id` returns the
        # activity table's own spelling ("Nepsilon", "DemonSuperElite_Fairy") -
        # so the declared id has to be folded the same way or this arm is dead
        # code: every shipped boss_id carries a capital, so an exact compare
        # matched 0 of 14 and a declared boss the sheets had never heard of
        # still filed its damage as trash.
        is_boss_unit = bool(
            udata.is_boss(target_name)
            or udata.is_boss(clean_tgt)
            or (self._declared_boss is not None
                and clean_tgt == self._declared_boss.strip().lower())
        )
        # NOTE: is_dummy_unit intentionally excluded from is_boss_unit —
        # dummies route to their own group, not the boss group.

        # A real swing clears the dummy's combat hold (see
        # DpsTracker.suppress_game_combat): a hit on anything but a dummy is
        # combat the meter should show again.
        if not is_dummy_unit:
            self._dummy_combat_suppressed = False

        foe = foes.get(tgt)

        # Stored/displayed names use the canonical display spelling; the
        # routing flags above deliberately keep the raw event spelling (unit
        # family checks) and stay untouched.
        disp_name = _canonical_unit_display(target_name or "Enemy")

        # Swinging at the dummy a FINISHED test belongs to starts the next one:
        # the test's own end signals (target length, walking away) latch the
        # engagement off, and nothing else would ever clear it, so the board
        # sat on the old numbers while you kept hitting the dummy (see
        # dps_dummy._watch_dummy_reengage). An explicit Stop Test is exempt.
        if is_dummy_unit:
            self._watch_dummy_reengage(getattr(ev, "t", 0.0) or 0.0, tgt or 0)

        # Auto-engage dummy fight first (own group, parallel to boss). Held off
        # by the meter's Stop Test latch (core/dps_dummy.py): hitting the dummy
        # after a stop is then ordinary open-world damage, not a new test the
        # player never asked for. Only the meter's Start Test / Reset re-arms.
        if self.dummy_may_engage() and (is_dummy_unit or (foe is not None and foe[3])):
            if self.trash_session.group_damage > 0:
                if self.trash_session.state == "COMBAT":
                    self.trash_session.pause()
                # Seal the last pack before the archive copies the session, so
                # the archived trash carries every pull complete (see
                # `PackSplit`). Same rule as the scene-driven boss engage in
                # core/dps_tracker_tick.py.
                self.trash_session.close_pack()
                self.archive_current_encounter("Trash Mobs Cleared")
            dummy_name = (disp_name or "").replace("🎯", "").strip()
            if not dummy_name.startswith("🎯 "):
                dummy_name = f"🎯 {dummy_name}"
            self._begin_dummy_test(
                dummy_name,
                tgt or (getattr(foe, "addr", 0) if foe else 0),
                foe[0] if foe is not None else 0.0)
            if not self.dummy_session.target_addr:
                for f_a, f_info in foes.items():
                    if f_info[3]:  # is_dummy
                        self.dummy_session.target_addr = f_a
                        self.dummy_session.target_hp = f_info[0]
                        self.dummy_session.target_max_hp = f_info[0]
                        break
            self.reset_foe_max_hp()
            for h_addr, (h_name, is_m, *_) in heroes.items():
                if is_m:
                    self.dummy_session.register_player(h_name, is_me=is_m)

        # Auto-engage boss fight if hitting a boss even if scene scanner missed boss_present
        # (e.g. after manual reset during boss encounter or delayed spawn)
        #
        # The same declared-boss narrowing the scene scan applies (see
        # `_scan_foes`): when the instance NAMES its boss, an engine-flagged
        # unit that is not that boss may not open a boss fight from a hit
        # either. Without it this path was a way around the scan's guard -
        # the hit on a hard-mode champion mob re-engaged whatever the scan
        # had decided, and re-archived the trash run.
        if not self.in_boss_fight and (is_boss_unit or (foe is not None and foe[2])):
            self.in_boss_fight = True
            if (getattr(self, "_boss_claim_uncertain", False)
                    and self.trash_session.group_damage > 0):
                # Engine-only boss claim: keep the run whole (see `_engage_boss`
                # and `_scan_foes`). Archiving here is what reset the meter
                # after every pack on hard/heroic (live 2026-10-03).
                self.trash_session.close_pack()
            elif self.trash_session.group_damage > 0:
                if self.trash_session.state == "COMBAT":
                    self.trash_session.pause()
                # Seal the last pack before the archive copies the session, so
                # the archived trash carries every pull complete (see
                # `PackSplit`).
                self.trash_session.close_pack()
                # The boss being engaged names the pulls that led to it, so
                # they are filed beside this boss rather than in the day-wide
                # trash file (see `file_for`). Read off the event, because
                # `boss_session.target_name` is only set just below.
                self.archive_current_encounter("Trash Mobs Cleared",
                                                boss_name=disp_name)
            self.boss_session.reset()
            self.boss_adds_session.reset()
            crown_name = disp_name if disp_name.startswith("👑 ") else f"👑 {disp_name}"
            self.boss_session.target_name = crown_name
            self.boss_session.target_addr = tgt or (getattr(foe, "addr", 0) if foe else 0)
            if foe is not None and foe[0] > 0:
                self.boss_session.target_hp = foe[0]
                self.boss_session.target_max_hp = foe[0]
            if not self.boss_session.target_addr:
                for f_a, f_info in foes.items():
                    if f_info[2]:  # is_boss
                        self.boss_session.target_addr = f_a
                        self.boss_session.target_hp = f_info[0]
                        self.boss_session.target_max_hp = f_info[0]
                        break
            self.reset_foe_max_hp()
            for h_addr, (h_name, is_m, *_) in heroes.items():
                if is_m:
                    self.boss_session.register_player(h_name, is_me=is_m)

        is_dummy = is_dummy_unit
        on_dummy = self.in_dummy_fight and (
            (tgt and tgt == self.dummy_session.target_addr)
            or is_dummy
            or (self.dummy_session.target_name in ("None", "") and not is_dummy)
        )
        if on_dummy:
            # Activity Log: the split's one open question is whether the decoded
            # hit carries a per-dummy address (core/dps_dummy.
            # log_dummy_hit_target). Once per distinct address, not per hit.
            self.log_dummy_hit_target(
                tgt, getattr(ev, "skill_name", "") or getattr(ev, "skill", ""))
        on_boss = self.in_boss_fight and (
            (tgt and tgt == self.boss_session.target_addr)
            or (foe is not None and foe[2])
            or is_boss_named
            or is_boss_unit
            or (self.boss_session.target_name in ("None", "") and not clean_boss)
        )

        target_hp = foe[0] if foe else 0.0
        if tgt and foe:
            self._foe_max_hp[tgt] = max(self._foe_max_hp.get(tgt, foe[0]), foe[0])
            target_max = self._foe_max_hp[tgt]
        else:
            target_max = foe[0] if foe else 0.0

        # When hitting boss via bridge (tgt == 0), inherit boss HP from live session
        if on_boss:
            if target_hp <= 0 and self.boss_session.target_hp > 0:
                target_hp = self.boss_session.target_hp
            if target_max <= 0 and self.boss_session.target_max_hp > 0:
                target_max = self.boss_session.target_max_hp

        sid, sname, wire_pet = self._skill_labels(ev, fallback=("Attack", "Attack"))
        # Pet-ness is the OR of both taggers, decided once (see _skill_labels):
        # the wire's tag is a per-hit memory read and drops out on its own, so
        # trusting either alone mints two buckets for one pet skill.
        from_pet = from_pet or wire_pet
        if from_pet:
            sname = f"{sname} (pet)"
        # Per-hit damage type; normalized here as well as at the readers so a
        # hand-built or third-party event cannot inject a junk bucket key.
        aff = clean_affinity(getattr(ev, "affinity", ""))

        if on_dummy:
            self.dummy_session.record_hit(target_name=disp_name, target_addr=tgt,
                                          target_hp=target_hp, target_max_hp=target_max,
                                          damage=amount, caster_name=caster, is_me=is_me,
                                          skill_id=sid, skill_name=sname, is_crit=ev.crit, when=when,
                                          affinity=aff)
        elif on_boss:
            self.boss_session.record_hit(target_name=disp_name, target_addr=tgt,
                                          target_hp=target_hp, target_max_hp=target_max,
                                          damage=amount, caster_name=caster, is_me=is_me,
                                          skill_id=sid, skill_name=sname, is_crit=ev.crit, when=when,
                                          affinity=aff)
        elif self.in_boss_fight:
            self.boss_adds_session.record_hit(target_name=disp_name, target_addr=tgt,
                                              target_hp=target_hp, target_max_hp=target_max,
                                              damage=amount, caster_name=caster, is_me=is_me,
                                              skill_id=sid, skill_name=sname, is_crit=ev.crit, when=when,
                                              affinity=aff)
        elif getattr(self, "_instance_now", False):
            # Trash is a DUNGEON/RIFT segment only (live 2026-10-02). The
            # open-world run's trash used to record here, and the zone-in
            # auto-reset then archived it into the day-wide trash file — so
            # the rift's DPS Analysis opened with an entry for a fight that
            # never happened inside the instance. Open-world damage stays on
            # the overall board below, which records unconditionally.
            self.trash_session.record_hit(target_name=disp_name, target_addr=tgt,
                                          target_hp=target_hp, target_max_hp=target_max,
                                          damage=amount, caster_name=caster, is_me=is_me,
                                          skill_id=sid, skill_name=sname, is_crit=ev.crit, when=when,
                                          affinity=aff)
        # The boss fight's PER-TARGET split, fed once per hit rather than from
        # inside either session. The fight is two sessions - the boss's damage
        # goes to boss_session, an add's to boss_adds_session - so bucket them
        # together here, where both sides are visible at once: "how much of the
        # boss board went to the boss versus its adds" is one question with one
        # answer, and the rows sum to the fight's damage rather than splitting
        # across two segments the reader has to add up. `on_dummy` is excluded
        # because a dummy test owns its own split (and is not a boss fight).
        if self.in_boss_fight and not on_dummy and amount > 0.0:
            self.boss_session.note_target(target_name=disp_name,
                                          target_addr=tgt, damage=amount,
                                          target_hp=target_hp,
                                          target_max_hp=target_max)
        self.overall_session.record_hit(target_name=disp_name, target_addr=tgt,
                                        target_hp=target_hp, target_max_hp=target_max,
                                        damage=amount, caster_name=caster, is_me=is_me,
                                        skill_id=sid, skill_name=sname, is_crit=ev.crit, when=when,
                                        affinity=aff)

        # Kill flags arm the boss end; HP/despawn check above confirms before archiving.
        if ev.kill and on_boss:
            self._boss_kill_pending = now
        return EVENT_ATTRIBUTED
