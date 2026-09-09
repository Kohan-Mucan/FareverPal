"""Fight lifecycle service for the shared DPS tracker."""
from __future__ import annotations

import time

HERO_VANISH_WIPE_S = 3.0
BOSS_CORPSE_SUPPRESS_S = 6.0


class DpsTrackerLifecycle:
    """Own death, wipe, and encounter-end transitions for one tracker."""

    def __init__(self, tracker):
        self.tracker = tracker

    def on_local_death(self, name: str = "You"):
        return self._lifecycle_on_local_death(name)

    def watch_hero_deaths(self, pa, heroes, hero_hp_now):
        return self._lifecycle_watch_hero_deaths(pa, heroes, hero_hp_now)

    def watch_local_vanish(self, pa, hero_hp_now, in_instance_now, now_wall):
        return self._lifecycle_watch_local_vanish(pa, hero_hp_now, in_instance_now, now_wall)

    def end_boss_fight(self, reason: str):
        return self._lifecycle_end_boss_fight(reason)

    def end_dummy_fight(self, reason: str):
        return self._lifecycle_end_dummy_fight(reason)

    def _lifecycle_on_local_death(self, name: str = "You") -> None:
        self = self.tracker
        """The local player died.

        Death used to wipe everything: it called ``reset()``, which archived
        the pull and cleared every session. That is wrong for the trash run,
        which is ONE run across the whole instance — dying three packs in and
        losing the first two is losing the measurement, not ending it
        (reported live 2026-10-03: "it should be 1 run for trash even if i
        die").

        So the trash run is PAUSED, not reset: ``record_hit`` resumes a paused
        session on the next hit, so the clock picks up where it stopped instead
        of restarting at zero. A live BOSS pull really is over — that fight has
        its own end signals and a wipe is a real outcome — so it is ended and
        archived normally, and the dummy test keeps its own ends.

        Still edge-triggered by the hero-HP death watcher (prev>0 -> hp<=0), so
        corpse/respawn ticks never re-fire it, and still NOT gated on
        auto_reset_zone: that toggle is about zoning, not wiping.
        """
        self.last_local_death_m = time.monotonic()
        if callable(self.log_line):
            # Always logged, even with nothing fought (early death): the
            # speedrun timer keys its wipe off this same marker, so a missing
            # line here means the death was never seen at all.
            try:
                self.log_line(f"Top DPS: local death registered ({name})")
            except Exception:
                pass
        if (self.overall_session.group_damage <= 0
                and self.trash_session.group_damage <= 0
                and self.boss_session.group_damage <= 0
                and self.dummy_session.group_damage <= 0):
            return    # nothing fought — meter already fresh
        # Hold every event off until the respawn is alive again, so queued hits
        # from the corpse tick are not credited to the run that resumes.
        self._death_rearm_wait = True
        if self.in_boss_fight:
            # `self` is the tracker from here on, so this is the TRACKER's
            # `_end_boss_fight` — the lifecycle object's identically-named
            # method is not reachable from here, and reaching for it raises.
            self._end_boss_fight(f"Died ({name})")
        for sess in (self.trash_session, self.overall_session):
            if sess.state == "COMBAT":
                sess.pause()
        if callable(self.log_line):
            try:
                self.log_line(
                    "Top DPS: you died — trash run paused, not reset")
            except Exception:
                pass

    def _lifecycle_watch_hero_deaths(self, pa: int | None,
                           heroes: dict[int, tuple[str, bool, tuple[float, float, float], str]],
                           hero_hp_now: dict[int, float]) -> None:
        self = self.tracker
        """Edge-triggered death detection off hero HP (prev>0 -> hp<=0).

        Deaths are facts, not estimates: a hero at 0 HP died, so the death
        count lands on the active segment + overall, and a local death
        archives + resets the meter. Corpse/respawn ticks never re-fire: only
        the downward edge counts.
        """
        prev = self._death_prev_hp
        for h_addr, hp in hero_hp_now.items():
            p = prev.get(h_addr)
            if p is not None and p > 0 and hp <= 0:
                h_name = self._hero_name(h_addr, pa, heroes)
                if h_name:
                    if self.in_dummy_fight:
                        self.dummy_session.record_death(h_name)
                    elif self.in_boss_fight:
                        self.boss_session.record_death(h_name)
                    else:
                        self.trash_session.record_death(h_name)
                    self.overall_session.record_death(h_name)
                    if h_addr == pa:
                        try:
                            self.on_local_death(h_name)
                        except Exception:
                            pass
        self._death_prev_hp = dict(hero_hp_now)

    def _lifecycle_watch_local_vanish(self, pa: int | None,
                            hero_hp_now: dict[int, float],
                            in_instance_now: bool, now_wall: float) -> None:
        self = self.tracker
        """The local hero vanished from the scene mid-fight (wipe without a corpse).

        Death can remove the hero entity instead of leaving a 0-HP corpse to
        scan — then the HP edge in `_watch_hero_deaths` never fires and a wipe
        only surfaces ~20 s later via the boss leash/despawn abort, long after
        the moment that mattered. If the local hero was seen alive and then
        stays absent while the instance is stable and a fight is live, the
        persisting absence IS the death edge: route it through on_local_death
        (same archive + wipe marker) after HERO_VANISH_WIPE_S.

        Loads are excluded by the stable-instance gate (a transition flips the
        instance edge first) and idle vanish never fires (nothing fought means
        nobody can have wiped).
        """
        if not pa:
            self._pa_absent_since = 0.0
            return
        hp_now = hero_hp_now.get(pa)
        if hp_now is not None:
            if hp_now > 0.0:
                self._pa_last_alive_hp = hp_now
            self._pa_absent_since = 0.0
            return
        if self._pa_last_alive_hp <= 0.0:
            return  # never seen alive (or already counted): nothing to conclude
        if not in_instance_now or in_instance_now != self._in_instance_prev:
            self._pa_absent_since = 0.0
            self._pa_last_alive_hp = 0.0
            return  # zone transition / outside: loads, not deaths
        fight_live = bool(
            self.in_boss_fight or self.in_dummy_fight
            or (self._last_events_ts > 0.0
                and now_wall - self._last_events_ts < 10.0))
        if not fight_live:
            self._pa_absent_since = 0.0
            return
        if self._pa_absent_since <= 0.0:
            self._pa_absent_since = time.monotonic()
            return
        grace = getattr(self, "_vanish_grace_s", HERO_VANISH_WIPE_S)
        if time.monotonic() - self._pa_absent_since >= grace:
            self._pa_absent_since = 0.0
            self._pa_last_alive_hp = 0.0
            try:
                name = self._resolve_hero_name(pa, True)
            except Exception:
                name = ""
            self.on_local_death(name or "You")

    def _lifecycle_end_boss_fight(self, reason: str):
        self = self.tracker
        self.boss_session.pause()
        # Suppress re-engaging THIS boss while the corpse despawns (live:
        # the corpse matched the engagement scan next tick, re-armed the
        # session, and pinned the timer at 00:00.0). Long enough to outlive
        # any despawn animation; a genuinely new boss is a different addr.
        self._ended_boss_addr = self.boss_session.target_addr or None
        self._ended_boss_deadline = time.monotonic() + BOSS_CORPSE_SUPPRESS_S
        self.archive_current_encounter(reason)
        self.in_boss_fight = False
        self._boss_kill_pending = 0.0
        self._boss_missing_at = 0.0
        # Encounter over: next fight starts from a clean HP baseline.
        self.reset_foe_max_hp()

    def _lifecycle_end_dummy_fight(self, reason: str):
        self = self.tracker
        self.dummy_session.pause()
        if not getattr(self, "dummy_free", False):
            # FREE TEST: the run leaves no record. Skipping the archive also
            # skips the day sheet, which is appended from inside it, so one
            # gate covers every sink a finished test can write to.
            self.archive_current_encounter(reason)
        self.in_dummy_fight = False
        self.reset_foe_max_hp()
