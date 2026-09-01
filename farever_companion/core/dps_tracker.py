"""DPS tracking engine: multi-segment combat tracking fed by real damage events.

HP is polled for display/segmentation only, never to fabricate numbers.
The pure data model lives in ``core/dps_data.py``; this module owns the
per-tick update loop, name resolution, pet attribution, boss segmentation,
and opt-in history/arrival persistence.
"""

from __future__ import annotations

import copy
import json
import logging
import math
import time
from collections import deque
from functools import lru_cache
from pathlib import Path

from ..data import names, units as udata
from ..persist import atomic_write_json
from .dps_data import (
    BOSS_GONE_IDLE_S,
    BOSS_IDLE_AUX_S,
    BOSS_KILL_GRACE_S,
    GAME_COMBAT_END_CONFIRM_S,
    GAME_COMBAT_POLL_S,
    GROUP_ROSTER_TTL_S,
    K_DAMAGE,
    K_HEAL,
    K_SHIELD,
    PET_PROBE_S,
    PARTY_RETRY_S,
    OWNED_ITEM_PROC_SKILLS,
    _slug_profile,
    _roster_change_key,
    _roster_line,
    _session_from_dict,
    _session_to_dict,
    CombatSession,
    DamageEvent,
    HitEvent,
    PlayerParse,
    SkillParse,
    read_history_file,
    weapon_families_used,
    weapon_family_of,
    weapons_used,
    hero_cls_to_class,
    infer_class_from_skill,
    log,
)

# Backward-compatible re-exports: every symbol that was importable from
# farever_companion.core.dps_tracker before the split still resolves here.
__all__ = [
    "DpsTracker",
    "solo_status",
    "CombatSession",
    "PlayerParse",
    "SkillParse",
    "HitEvent",
    "DamageEvent",
    "weapon_families_used",
    "weapon_family_of",
    "weapons_used",
    "read_history_file",
    "infer_class_from_skill",
    "hero_cls_to_class",
    "GAME_COMBAT_END_CONFIRM_S",
    "BOSS_IDLE_AUX_S",
]


CO_COMBAT_WINDOW_S = 10.0   # a player this fresh counts as fighting alongside you


# A boss that vanishes from the unit scan for less than this long is treated
# as scan flicker (rift arenas can carry a second boss-tagged entity, and a
# boss can drop out of the scan for a single poll). The meter keeps its pinned
# target instead of hopping between boss entities every tick; only after the
# pinned boss has stayed gone past this window does the tracker re-target.
BOSS_RELATCH_GRACE_S = 1.5


# Damage events can name a unit by its RAW internal id (the bridge/DLL, e.g.
# "DemonSuperElite") while the unit scan stores the resolved display name
# ("Nightking Maat Demon"). Everything written onto a session must use ONE
# spelling, or the Top DPS crown label alternates between the two every tick.
@lru_cache(maxsize=8192)
def _canonical_unit_display(name: str) -> str:
    """The display spelling a scene scan would store for this raw unit id.

    Id-shaped tokens are resolved exactly like the scan's readable name:
    the unit tables first ("DemonSuperElite" -> "Nightking Maat Demon",
    "TrainingDummy" -> "Training Dummy"), else the humanized label. Names
    that are already display text (spaces/emblems/slashes) and non-unit
    literals pass through untouched, so hero names are never rewritten.
    """
    if not name:
        return name
    stripped = name.replace("👑 ", "").replace("🎯 ", "").strip()
    # ids never contain spaces/slashes; anything else is already display text
    if not stripped or " " in stripped or "/" in stripped or "\\" in stripped:
        return name
    resolved = names.unit_name(stripped)
    return resolved if resolved != stripped else name


def _read_roster(model):
    """The model's current group roster (None when unattached/undecodable).

    Prefers the live ``group_roster()`` read (the game reader rate-limits
    itself to ~1 Hz so every consumer in a tick shares one snapshot) and
    falls back to a plain ``roster`` attribute for fakes/tests.
    """
    if model is None:
        return None
    try:
        fn = getattr(model, "group_roster", None)
        if callable(fn):
            return fn()
    except Exception:
        pass
    try:
        return getattr(model, "roster", None)
    except Exception:
        return None


def _in_instance(model) -> bool:
    """True when the player is inside a dungeon/rift instance.

    Accepts both the real model's ``is_in_dungeon_or_rift()`` read and the
    plain ``is_dungeon``/``is_rift`` flags fakes/tests set.
    """
    try:
        fn = getattr(model, "is_in_dungeon_or_rift", None)
        if callable(fn):
            return bool(fn())
    except Exception:
        pass
    try:
        return bool(getattr(model, "is_dungeon", False)
                    or getattr(model, "is_rift", False))
    except Exception:
        return False


def solo_status(model, tracker=None) -> bool | None:
    """True = player is solo (no other party members); False = grouped with
    party members (or inside a dungeon/rift, where everyone always shows);
    None = unknown (unattached or not loaded into game).

    - Inside a dungeon/rift instance -> False (always show all members).
    - A decoded party roster with other members means grouped -> False.
    - An empty/self-only group or no roster while in open world means solo -> True.
    - No attached model -> None (unknown).
    """
    if model is None:
        return None
    pa = getattr(model, "player_addr", 0)
    if not pa and not getattr(model, "is_attached", False):
        return None
    if _in_instance(model):
        return False
    roster = _read_roster(model)
    if roster is not None:
        members = list(getattr(roster, "members", []) or [])
        other_members = [m for m in members if not getattr(m, "is_me", False)]
        if other_members:
            return False
    return True


class DpsTracker:
    """Multi-segment combat tracking fed by real damage events.

    HP is display/segmentation only. With no reader nothing is recorded;
    the meter shows its empty state rather than fabricated numbers.
    """

    def __init__(self, model, max_dist: float = 400.0):
        self.model = model
        self.max_dist: float = max_dist

        self.overall_session = CombatSession("Overall Run", kind="overall")
        self.trash_session = CombatSession("Trash Mobs", kind="trash")
        self.boss_session = CombatSession("Boss Fight", kind="boss")
        # Adds hit while the boss is up (own segment beside boss/trash).
        self.boss_adds_session = CombatSession("Boss Adds", kind="boss_adds")
        # Training Dummy has its own group like a boss fight.
        self.dummy_session = CombatSession("Training Dummy", kind="dummy")

        self.segment_view: str = "auto"  # "auto", "boss", "boss_adds", "trash", "dummy", "overall"
        self.in_boss_fight: bool = False
        self.in_dummy_fight: bool = False

        # Encounter Fight History (archived finished sessions)
        self.history: list[CombatSession] = []
        self._history_path: Path | None = None   # explicit file (tests/tools)
        # Dedicated moddata/dps/ folder — OFF by default so headless runs and
        # probes never touch disk. The app opts in when it attaches to the
        # game (game_attach -> set_history_dir(dps_dir())).
        self._history_dir: Path | None = None
        self._active_profile: str | None = None  # profile the loaded file is for
        self._profile_ts: float = 0.0

        self._started_at: float = time.time()

        self._hero_name_cache: dict[int, str] = {}
        # Authoritative class per hero; _sync_known_classes() writes it onto rows.
        self._hero_classes: dict[int, str] = {}
        self._hero_pointers: dict[int, tuple[str, bool]] = {}
        self._foe_names: dict[int, str] = {}
        self._foe_raw_ids: dict[int, str] = {}   # addr -> raw unit id (kill-uid matching)
        self._src_cursor: int = 0
        self._boss_kill_pending: float = 0.0
        self._boss_last_seen: float = 0.0
        self._boss_missing_at: float = 0.0   # wall clock the pinned boss went absent

        # Game combat state (auxiliary to idle heuristics; see _poll_combat_state).
        self.combat_state: dict | None = None
        self._combat_id: int | None = None
        self._combat_was_in: bool | None = None
        self._game_combat_end_at: float = 0.0
        self._combat_poll_at: float = 0.0
        # Live encounter timer: anchor wall time when combatStartTime moves (see _poll_combat_state).
        self._combat_start: float = 0.0      # last seen combatStartTime (engine s)
        self._combat_wall_start_at: float = 0.0  # wall clock at that fight start

        # Pets report the pet as serverSource; map pet addr -> owning hero addr (Details-style merge).
        self._pet_owners: dict[int, int] = {}
        self._pet_last_seen: dict[int, float] = {}
        # Source addrs learned to be the local hero's item/summon proc.
        self._owned_proc_sources: dict[int, int] = {}
        # addr -> Party_XXXX row; folds into the owner once the owner is learned.
        self._fallback_rows: dict[int, str] = {}
        self._pet_probe_at: dict[int, float] = {}    # last probe time per addr
        self._party_retry_at: dict[int, float] = {}  # last name retry per addr
        self._roster_ts: float = 0.0                 # last st.Group roster seed
                                                     # (see _seed_group_roster)
        # Roster change-log state (log_line sink wired to the Activity Log).
        self._last_roster_key: tuple | None = None
        # Last decoded st.Group snapshot (the tracker seeds it every 2s);
        # the display path reads this cache instead of polling the game.
        self.last_roster = None
        self.log_line = None                         # Callable[[str], None] | None

        # Hero-HP damage-taken FALLBACK: diffed only during reader lulls, never mixed with authoritative events.
        self._hero_hp: dict[int, float] = {}
        self._foe_hp: dict[int, tuple[float, str]] = {}   # addr -> (last_hp, unit_id)
        self._foe_max_hp: dict[int, float] = {}
        self._incoming_tick: set[int] = set()
        self._last_events_ts: float = 0.0
        self.hp_est_lull_s: float = 3.0
        self.hp_est_allowed: bool = False    # user toggle: HP-diff fallback on/off (off by default)
        self._hp_diff_enabled: bool = True   # set False when real events are flowing

        # Character change detection state
        self._last_local_hero_addr: int | None = None
        self._last_local_hero_name: str | None = None
        self._hero_was_absent: bool = False

    # --- damage source (model-owned manager) ------------------------------#
    @property
    def source(self):
        """The model's DamageSourceManager (or None when unattached/tests)."""
        m = self.model
        return getattr(m, "damage", None) if m is not None else None

    @property
    def session(self) -> CombatSession:
        if self.segment_view == "boss":
            return self.boss_session
        elif self.segment_view == "boss_adds":
            return self.boss_adds_session
        elif self.segment_view == "trash":
            return self.trash_session
        elif self.segment_view == "dummy":
            return self.dummy_session
        elif self.segment_view == "overall":
            return self.overall_session

        if self.in_dummy_fight or self.dummy_session.group_damage > 0:
            return self.dummy_session
        if self.in_boss_fight or self.boss_session.group_damage > 0:
            return self.boss_session
        if self.trash_session.group_damage > 0:
            return self.trash_session
        return self.overall_session

    def active_session(self) -> CombatSession:
        """Convenience method returning the active combat session."""
        return self.session

    def archive_current_encounter(self, reason: str = "Encounter Finished"):
        """Archives the current combat session(s) into the Fight History."""
        sessions: list[CombatSession] = []
        if self.in_dummy_fight or self.dummy_session.group_damage > 0:
            sessions.append(self.dummy_session)
        elif self.in_boss_fight or self.boss_session.group_damage > 0:
            sessions.append(self.boss_session)
            # Adds hit DURING the boss fight ride along as their own segment
            if self.boss_adds_session.group_damage > 0:
                sessions.append(self.boss_adds_session)
        elif self.trash_session.group_damage > 0:
            sessions.append(self.trash_session)
        for sess in sessions:
            if sess and sess.group_damage > 0:
                archived = copy.deepcopy(sess)
                archived.pause()
                archived.name = f"{sess.name} ({reason})" if reason else sess.name
                # Avoid duplicate archive of identical timestamp
                if not self.history or self.history[-1].start_time != archived.start_time:
                    self.history.append(archived)
                    # Keep up to 25 past fights
                    if len(self.history) > 25:
                        self.history.pop(0)
                    self._persist_history()

    # --- fight-history persistence (opt-in) -------------------------------#
    def set_history_path(self, path: str | Path | None) -> None:
        """Enable on-disk fight history at one explicit path (tests/tools)."""
        self._history_path = Path(path) if path else None
        self._history_dir = None
        if self._history_path is not None:
            try:
                self.load_history()
            except Exception:
                pass

    def set_history_dir(self, path: str | Path | None) -> None:
        """Enable per-profile fight history under a directory (off by default)."""
        self._history_path = None
        self._history_dir = Path(path) if path else None
        self._active_profile = None
        if self._history_dir is not None:
            try:
                self.load_history()
            except Exception:
                pass

    def _resolve_history_file(self) -> Path | None:
        """Current on-disk file (None when persistence is disabled)."""
        if self._history_path is not None:
            return self._history_path
        if self._history_dir is None:
            return None
        prof = self._active_profile
        if prof:
            return self._history_dir / f"dps_history_{_slug_profile(prof)}.json"
        return self._history_dir / "dps_history.json"

    def _persist_history(self) -> None:
        """Write archived fights to the current file (no-op unless enabled)."""
        path = self._resolve_history_file()
        if not path:
            return
        try:
            atomic_write_json(path, {"history": [_session_to_dict(s)
                                                 for s in self.history]})
        except OSError:
            pass

    def load_history(self) -> None:
        """Read archived fights from the current profile's file."""
        path = self._resolve_history_file()
        if not path or not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        loaded = []
        for d in (data.get("history") or []):
            try:
                loaded.append(_session_from_dict(d))
            except Exception:
                continue
        if loaded:
            self.history = loaded[-25:]   # still capped at 25 past fights

    def reset(self, reason: str = "Manual Reset"):
        # Archive before wipe if there was data
        if self.overall_session.group_damage > 0:
            self.archive_current_encounter(reason)

        self.overall_session.reset()
        self.trash_session.reset()
        self.boss_session.reset()
        self.boss_adds_session.reset()
        self.dummy_session.reset()
        self.in_boss_fight = False
        self.in_dummy_fight = False
        self._hero_name_cache.clear()
        self._hero_pointers.clear()
        self._foe_names.clear()
        self._foe_raw_ids.clear()
        self._boss_kill_pending = 0.0
        self._boss_last_seen = 0.0
        self._boss_missing_at = 0.0
        self.combat_state = None
        self._combat_id = None
        self._combat_was_in = None
        self._game_combat_end_at = 0.0
        self._combat_poll_at = 0.0
        self._combat_start = 0.0
        self._combat_wall_start_at = 0.0
        self._pet_owners.clear()
        self._pet_last_seen.clear()
        self._owned_proc_sources.clear()
        self._fallback_rows.clear()
        self._pet_probe_at.clear()
        self._party_retry_at.clear()
        self._roster_ts = 0.0    # re-seed the st.Group roster on the next tick
        self._last_roster_key = None   # ...and log the new group fresh
        self.last_roster = None
        self._hero_classes.clear()
        self._hero_hp.clear()
        self._foe_hp.clear()
        self._foe_max_hp.clear()
        self._incoming_tick.clear()
        self._last_events_ts = 0.0

    def reset_hp_baselines(self, reason: str = "capture mode changed") -> None:
        """Drop cached foe/hero HP snapshots so HP-diff re-baselines cleanly.

        Called when the DPS capture mode switches (e.g. proxy -> memory) and
        when a combat encounter ends (boss defeated, trash lull, boss engage):
        baselines recorded under the old source/fight are stale for the next
        fight, so without this the first HP drop would look like a respawn
        jump and get skipped (or damage from before the switch would be
        double-counted).
        """
        self._foe_hp.clear()
        self._foe_max_hp.clear()
        self._hero_hp.clear()
        self._incoming_tick.clear()
        if callable(self.log_line):
            try:
                self.log_line(f"Top DPS: HP-diff baselines reset ({reason})")
            except Exception:
                pass

    def set_hp_est_allowed(self, allowed: bool) -> None:
        """Live-toggle the HP-diff fallback so both surfaces reflect it now.

        Re-baselines the HP snapshots either way; when turning the fallback
        OFF it also strips already-recorded ``(HP)`` estimate rows from the
        live sessions, so the Top DPS meter and the Combat page never keep
        showing estimated damage the user just switched off. Archived fight
        history is untouched. No-op when the value doesn't change.
        """
        allowed = bool(allowed)
        if allowed == self.hp_est_allowed:
            return
        self.hp_est_allowed = allowed
        self.reset_hp_baselines(reason="HP-diff toggle")
        if not allowed:
            self.clear_hp_estimates()

    def clear_hp_estimates(self) -> None:
        """Strip recorded ``HP diff (est.)`` rows from the LIVE sessions.

        Unwinds the estimate skill's damage/hit contributions from every
        player parse and drops its entries from the recent-events feed.
        The per-second burst timeline keeps its recorded shape (estimates
        were folded into the same buckets as real damage, so they cannot
        be separated after the fact); rows, totals and chips are corrected.
        Archived history (and the on-disk files) keep what they recorded.
        """
        for sess in (self.overall_session, self.trash_session,
                     self.boss_session, self.boss_adds_session):
            for p in sess.players.values():
                sp = p.skills.pop("(HP)", None)
                if sp is None:
                    continue
                p.total_damage = max(0.0, p.total_damage - sp.damage)
                p.hit_count = max(0, p.hit_count - sp.hit_count)
            kept = [h for h in sess.hits if h.skill_id != "(HP)"]
            if len(kept) != len(sess.hits):
                sess.hits.clear()
                sess.hits.extend(kept)

    # --- name resolution --------------------------------------------------#
    def _resolve_hero_name(self, hero_addr: int, is_me: bool) -> str:
        if is_me:
            try:
                name = self.model.player_name(hero_addr)
                if name:
                    return f"{name} (You)"
            except Exception:
                pass
            return "You"

        cached = self._hero_name_cache.get(hero_addr)
        if cached and not cached.startswith("Party_"):
            return cached

        name = None
        try:
            name = self.model.player_name(hero_addr)
            if not name:
                p = self.model._hero_player_ptr(hero_addr)
                if p:
                    name = self.model._player_name_at(p)
        except Exception:
            pass

        if not name:
            name = cached or f"Party_{hero_addr & 0xFFFF:04X}"
        elif cached and cached.startswith("Party_") and name != cached:
            # Fold the Party_XXXX row into the real name (merge, not clobber).
            self._fold_party_row(cached, name)
            self._fallback_rows.pop(hero_addr, None)

        self._hero_name_cache[hero_addr] = name
        return name

    def _resolve_caster(self, src_addr: int, pa: int,
                        heroes: dict[int, tuple[str, bool, tuple[float, float, float], str]]
                        ) -> tuple[str | None, bool]:
        """(caster_name, is_me) for an event's authoritative source object."""
        if not src_addr:
            # Solo-credit guard: only with no other known hero anywhere (allies may be out of scan range).
            if len(heroes) == 1 and pa in heroes:
                known_others = [
                    n for a, (n, _me) in self._hero_pointers.items()
                    if a != pa and n and not n.startswith("Party_")
                ]
                if not known_others:
                    return heroes[pa][0], True
            return None, False

        # 1. Direct hero lookup
        if src_addr in heroes:
            name, is_me, *_ = heroes[src_addr]
            return name, is_me

        # 2. Local hero check
        if src_addr == pa:
            return self._resolve_hero_name(pa, True), True

        # 3. Dual-pointer cache (skips stale Party_XXXX so retry below can fold it)
        hp = self._hero_pointers.get(src_addr)
        if hp and not hp[0].startswith("Party_"):
            return hp

        # 4. Name cache; stale Party_XXXX retries live resolution on cadence and folds on success.
        cached = self._hero_name_cache.get(src_addr)
        if cached and not cached.startswith("Party_"):
            return cached, False
        if cached is not None:
            if time.time() - self._party_retry_at.get(src_addr, 0.0) < PARTY_RETRY_S:
                return cached, False
        self._party_retry_at[src_addr] = time.time()

        # 5. Live resolution (ent.Hero, st.Player, or unit)
        try:
            name = self.model.player_name(src_addr)
            if name:
                is_me = (src_addr == pa or "(You)" in name)
                party = self._fallback_rows.get(src_addr)
                if party and party != name:
                    self._fold_party_row(party, name)
                    self._fallback_rows.pop(src_addr, None)
                self._hero_name_cache[src_addr] = name
                self._hero_pointers[src_addr] = (name, is_me)
                return name, is_me
        except Exception:
            pass

        # 6. Fallback party tag (remembered so a later fold can find the row)
        fallback_name = cached or f"Party_{src_addr & 0xFFFF:04X}"
        self._hero_name_cache[src_addr] = fallback_name
        self._hero_pointers[src_addr] = (fallback_name, False)
        self._fallback_rows[src_addr] = fallback_name
        return fallback_name, False

    def _hero_name(self, addr: int, pa: int,
                   heroes: dict[int, tuple[str, bool, tuple[float, float, float], str]]
                   ) -> str | None:
        if addr == pa:
            return self._resolve_hero_name(pa, True)
        h = heroes.get(addr)
        if h:
            return h[0]
        cached = self._hero_name_cache.get(addr)
        if cached:
            return cached
        # Off-scene roster heroes live in _hero_pointers before the name cache.
        hp = self._hero_pointers.get(addr)
        if hp is not None and not hp[0].startswith("Party_"):
            return hp[0]
        return None

    def _foe_name(self, addr: int,
                  foes: dict[int, tuple[float, str, bool, float, float, float]]) -> str | None:
        f = foes.get(addr)
        if f:
            return f[1]
        return self._foe_names.get(addr)

    # --- group roster (st.Group) name seeding ----------------------------#
    def _seed_group_roster(self, pa: int) -> None:
        """Pre-seed party names from the game's st.Group roster; Party_XXXX rows fold into real names."""
        m = self.model
        if m is None:
            return
        try:
            roster = m.group_roster()
        except Exception:
            return
        # Cache for the display path (group line + solo check): the UI never
        # polls the game's group object itself.
        self.last_roster = roster

        # Roster-change log: one line per join/leave/leader/solo transition.
        key = _roster_change_key(roster)
        if key != self._last_roster_key:
            self._last_roster_key = key
            line = _roster_line(roster)
            log.info("[group-roster] %s", line)
            if self.log_line is not None:
                try:
                    self.log_line(f"[group] {line}")
                except Exception:
                    pass

        if roster is None or not roster.members:
            return
        me_player = 0
        try:
            me_player = m._hero_player_ptr(pa) or 0
        except Exception:
            pass
        for mem in roster.members:
            name = (mem.name or "").strip()
            if not name:
                continue
            # A roster alias must never clobber the local "X (You)".
            if mem.hero == pa or (me_player and mem.player == me_player):
                continue
            for addr in (mem.hero, mem.player):
                if not addr:
                    continue
                cached = self._hero_pointers.get(addr)
                if cached is not None and not cached[0].startswith("Party_"):
                    continue
                # Fold this addr's own fallback row into the real name.
                fallback = self._fallback_rows.get(addr)
                if fallback and fallback != name:
                    self._fold_party_row(fallback, name)
                    self._fallback_rows.pop(addr, None)
                elif fallback is None:
                    oldc = self._hero_name_cache.get(addr)
                    if oldc and oldc.startswith("Party_") and oldc != name:
                        self._fold_party_row(oldc, name)
                self._hero_name_cache[addr] = name
                self._hero_pointers[addr] = (name, False)

        # Class the party and self from equipped skill slots / scene entity
        if pa and pa not in self._hero_classes:
            try:
                cls = m.hero_class_of(pa) or m.player_class()
            except Exception:
                cls = None
            if cls:
                self._hero_classes[pa] = cls

        for mem in roster.members:
            addr = getattr(mem, "hero", 0) or 0
            if not addr or addr in self._hero_classes:
                continue
            try:
                cls = m.hero_class_of(addr)
            except Exception:
                cls = None
            if cls:
                self._hero_classes[addr] = cls

    def _sync_known_classes(self) -> None:
        """Write game-known classes onto session rows (truth replaces skill-guess)."""
        if not self._hero_classes:
            return
        sessions = (self.overall_session, self.trash_session,
                    self.boss_session, self.boss_adds_session)
        pa = getattr(self.model, "player_addr", 0) if self.model else 0
        for addr, cls in list(self._hero_classes.items()):
            hp = self._hero_pointers.get(addr)
            is_me = (hp[1] if hp else False) or (addr == pa)
            name = hp[0] if hp else None
            if not name or name.startswith("Party_"):
                name = self._hero_name_cache.get(addr)
            for sess in sessions:
                if name and not name.startswith("Party_"):
                    p = sess.players.get(name)
                    if p is not None and p.hero_class != cls:
                        p.hero_class = cls
                # If this is the local player, also update any row flagged is_me
                if is_me:
                    for p_cand in sess.players.values():
                        if p_cand.is_me and p_cand.hero_class != cls:
                            p_cand.hero_class = cls

    # --- game combat signals (auxiliary) ---------------------------------#
    @property
    def game_encounter_elapsed(self) -> float | None:
        """Wall seconds since combatStartTime moved (live encounter timer; None when idle)."""
        st = self.combat_state
        if not st or not st.get("in_combat") or self._combat_wall_start_at <= 0:
            return None
        return max(0.0, time.time() - self._combat_wall_start_at)

    def _poll_combat_state(self, pa: int) -> None:
        """Sample Hero combat fields; AUXILIARY end-confirm only, never starts a fight."""
        now = time.time()
        if now - self._combat_poll_at < GAME_COMBAT_POLL_S:
            return
        self._combat_poll_at = now

        st = None
        m = self.model
        if m is not None and pa:
            try:
                st = m.combat_state()
            except Exception:
                st = None
        self.combat_state = st
        if not st:
            return

        in_combat = bool(st.get("in_combat"))
        cid = st.get("combat_id")
        start = float(st.get("combat_start") or 0.0)

        # Anchor wall time when combatStartTime moves (the game's "fight began" tick).
        if in_combat:
            if start > 0 and (self._combat_start <= 0
                              or abs(start - self._combat_start) > 1e-6):
                self._combat_wall_start_at = now
            if start <= 0:
                self._combat_wall_start_at = 0.0
        else:
            self._combat_wall_start_at = 0.0
        self._combat_start = start

        if self._combat_was_in is True and not in_combat:
            self._game_combat_end_at = now
            log.info("[combat] game declares combat over (last id=%s)",
                     self._combat_id)
        if in_combat:
            if (cid is not None and self._combat_id is not None
                    and cid != self._combat_id):
                # combatId rotated: previous fight ended.
                self._game_combat_end_at = now
                log.info("[combat] combat id rotated %s -> %s "
                         "(previous fight ended)", self._combat_id, cid)
            else:
                # In (a) fight right now; no pending end signal.
                self._game_combat_end_at = 0.0
        self._combat_was_in = in_combat
        if cid is not None:
            self._combat_id = cid

    # --- event intake -----------------------------------------------------#
    def update(self):
        m = self.model
        if m is None:
            return

        src = self.source
        if src is not None:
            try:
                src.ensure_started()
            except Exception:
                pass

        pa = getattr(m, "player_addr", None)
        heroes: dict[int, tuple[str, bool, tuple[float, float, float], str]] = {}
        hero_hp_now: dict[int, float] = {}
        current_foes: dict[int, tuple[float, str, bool, float, float, float]] = {}
        boss_present = None
        boss_present_d = math.inf   # nearest boss-candidate distance
        dummy_present = None

        if pa is None:
            self.last_roster = None    # detached/menu: the group line goes blank
        else:
            # Check for character change
            curr_name = None
            try:
                curr_name = m.player_name(pa)
            except Exception:
                curr_name = None

            char_changed = False
            if self._last_local_hero_name is not None and curr_name:
                if curr_name != self._last_local_hero_name:
                    char_changed = True
            elif self._last_local_hero_addr is not None:
                if self._hero_was_absent and pa != self._last_local_hero_addr:
                    char_changed = True

            if char_changed:
                self.reset(f"Character Change: {curr_name or 'New Character'}")
                if src is not None:
                    try:
                        new_cur, _ = src.events_after(0)
                        self._src_cursor = new_cur
                    except Exception:
                        pass

            self._last_local_hero_addr = pa
            if curr_name:
                self._last_local_hero_name = curr_name
            self._hero_was_absent = False

            # Game-authoritative combat signals (auxiliary; slow cadence).
            self._poll_combat_state(pa)

            # Per-profile history: swap to the new profile's file (reset already archived the old one).
            if self._history_dir is not None:
                now = time.time()
                if now - self._profile_ts >= 2.0:
                    self._profile_ts = now
                    try:
                        prof = m.player_profile()
                    except Exception:
                        prof = None
                    if prof and prof != self._active_profile:
                        self._active_profile = prof
                        self.history = []   # old file's fights already persisted
                        try:
                            self.load_history()
                        except Exception:
                            pass

            # Seed roster names so off-scene members resolve instead of minting Party_XXXX rows.
            if time.time() - self._roster_ts >= GROUP_ROSTER_TTL_S:
                self._roster_ts = time.time()
                self._seed_group_roster(pa)

            pxyz = m.player_xyz()

            units = []
            try:
                units = m.units() or []
            except Exception:
                units = []

            me_in_scene = False
            for u in units:
                addr = getattr(u, "addr", 0)
                if not addr:
                    continue
                if not getattr(u, "is_hero", False):
                    continue
                is_me = (addr == pa)
                if is_me:
                    me_in_scene = True
                h_name = self._resolve_hero_name(addr, is_me)
                xyz = (getattr(u, "x", 0.0), getattr(u, "y", 0.0), getattr(u, "z", 0.0))
                h_cls = getattr(u, "cls", "ent.Hero") or "ent.Hero"
                u_id = getattr(u, "unit_id", "") or ""
                heroes[addr] = (h_name, is_me, xyz, h_cls)
                hero_hp_now[addr] = float(getattr(u, "hp", 0.0) or 0.0)

                # Authoritative entity class (exact same as Entity HUD)
                known_cls = getattr(u, "hero_class", "") or hero_cls_to_class(h_cls, u_id)
                if known_cls:
                    self._hero_classes[addr] = known_cls

                # Dual-pointer registration: map ent.Hero and st.Player
                self._hero_pointers[addr] = (h_name, is_me)
                if m is not None:
                    p_ptr = m._hero_player_ptr(addr)
                    if p_ptr:
                        self._hero_pointers[p_ptr] = (h_name, is_me)
                        if known_cls:
                            self._hero_classes[p_ptr] = known_cls

                # Only pre-register the local player so the meter isn't blank;
                # allies are registered dynamically when combat events arrive for them.
                if is_me:
                    me_in_scene = True
                    self.overall_session.register_player(h_name, is_me=True, hero_class=known_cls)
                    self.trash_session.register_player(h_name, is_me=True, hero_class=known_cls)
                    self.boss_session.register_player(h_name, is_me=True, hero_class=known_cls)

            if curr_name and not me_in_scene:
                # Missing self entity (loads); register under the same display name so the row never duplicates.
                me_key = self._resolve_hero_name(pa, True)
                my_cls = self._hero_classes.get(pa, "")
                self.overall_session.register_player(me_key, is_me=True, hero_class=my_cls)
                self.trash_session.register_player(me_key, is_me=True, hero_class=my_cls)
                self.boss_session.register_player(me_key, is_me=True, hero_class=my_cls)

            # Pets: non-hero owner resolving to a live hero; despawned pets linger briefly for in-flight events.
            now_pet_scan = time.time()
            learned: list[tuple[int, int]] = []
            for u in units:
                addr = getattr(u, "addr", 0)
                if not addr or getattr(u, "is_hero", False):
                    continue
                if not (getattr(u, "is_foe", False)
                        and getattr(u, "is_player_owned", False)):
                    continue
                own = getattr(u, "owner_addr", 0)
                own_key = own
                if own and own not in heroes and own != pa:
                    # Owner may be the st.Player pointer; translate to its hero entity.
                    want = self._hero_pointers.get(own)
                    if want:
                        for h_addr, (h_name, is_me, *_rest) in heroes.items():
                            if self._hero_pointers.get(h_addr) == want:
                                own_key = h_addr
                                break
                if own_key in heroes or own_key == pa:
                    if addr not in self._pet_owners:
                        learned.append((addr, own_key))
                    self._pet_owners[addr] = own_key
                    self._pet_last_seen[addr] = now_pet_scan
            # Late-learned mapping folds the uid fallback row into the owner.
            for addr, own_key in learned:
                party = self._fallback_rows.get(addr)
                if not party:
                    continue
                if own_key == pa:
                    owner_name = self._resolve_hero_name(pa, True)
                elif own_key in heroes:
                    owner_name = heroes[own_key][0]
                else:
                    continue
                self._fold_party_row(party, owner_name, pet_skills=True)
                self._fallback_rows.pop(addr, None)
            cut = now_pet_scan - 60.0
            for a in [a for a, ts in self._pet_last_seen.items() if ts < cut]:
                self._pet_owners.pop(a, None)
                self._pet_last_seen.pop(a, None)
                self._fallback_rows.pop(a, None)

            # Foes snapshot: naming/boss display/segmentation only; never diff HP into damage.
            # The game's own activity system decides "instance" — dungeon/rift
            # fights are zone-wide so every trash mob is tracked. (The old
            # getattr(m, "is_dungeon") pair read attributes that never existed
            # on the model, so the 400m cap silently stayed on in instances
            # and trash outside it was never recorded.)
            try:
                in_instance = bool(m.is_in_dungeon_or_rift())
            except Exception:
                in_instance = False
            effective_dist = 0.0 if in_instance else self.max_dist
            for u in units:
                addr = getattr(u, "addr", 0)
                if not addr or getattr(u, "is_hero", False):
                    continue

                x, y, z = getattr(u, "x", 0.0), getattr(u, "y", 0.0), getattr(u, "z", 0.0)
                if effective_dist > 0 and pxyz:
                    dist = math.dist(pxyz, (x, y, z))
                    if dist > effective_dist:
                        continue

                hp = getattr(u, "hp", 0.0) or 0.0
                raw_id = getattr(u, "unit_id", "") or "TrainingDummy"
                readable_name = names.unit_name(raw_id) or raw_id
                is_dummy = udata.is_training_dummy(raw_id)
                is_boss = (udata.is_boss(raw_id)
                           or (raw_id.startswith("DemonSuperElite") and "FalseClone" not in raw_id)
                           or getattr(u, "is_boss", False)
                           or (m.dungeon_boss and raw_id == m.dungeon_boss)
                           or "boss" in raw_id.lower()
                           or (getattr(u, "cls", None) and "boss" in str(u.cls).lower()))
                if is_boss and not readable_name.startswith("👑 "):
                    readable_name = f"👑 {readable_name}"
                if is_dummy and not readable_name.startswith("🎯 "):
                    readable_name = f"🎯 {readable_name}"

                current_foes[addr] = (hp, readable_name, is_boss, is_dummy, x, y, z)
                self._foe_names[addr] = readable_name
                self._foe_raw_ids[addr] = raw_id
                if is_boss and hp > 0:
                    # Rift/dungeon scans are instance-wide, so a second
                    # boss-tagged entity (staged boss, clone, ...) can sit in
                    # the same snapshot. Prefer the NEAREST one — the boss
                    # being fought — instead of whichever unit iterates first.
                    d_b = math.dist(pxyz, (x, y, z)) if pxyz else 0.0
                    if boss_present is None or d_b < boss_present_d:
                        boss_present = (addr, readable_name, hp)
                        boss_present_d = d_b
                if is_dummy and hp > 0 and dummy_present is None:
                    dummy_present = (addr, readable_name, hp)

            if len(self._foe_names) > 4096:
                self._foe_names = {a: n for a, n in self._foe_names.items()
                                   if a in current_foes}
                self._foe_raw_ids = {a: r for a, r in self._foe_raw_ids.items()
                                     if a in current_foes}

        # --- boss engagement (scene-driven, HP only for display) ----------#
        if boss_present and not self.in_boss_fight:
            b_addr, b_name, b_hp = boss_present
            if self.trash_session.group_damage > 0:
                if self.trash_session.state == "COMBAT":
                    self.trash_session.pause()
                self.archive_current_encounter("Trash Mobs Cleared")

            self.in_boss_fight = True
            self.boss_session.reset()
            self.boss_adds_session.reset()
            self.boss_session.target_addr = b_addr
            self.boss_session.target_name = b_name
            self.boss_session.target_hp = b_hp
            self.boss_session.target_max_hp = b_hp
            self._boss_kill_pending = 0.0
            self._boss_missing_at = 0.0
            # Fresh engagement clears stale game-declared end signals.
            self._game_combat_end_at = 0.0
            # New encounter: drop HP baselines so the boss fight re-baselines.
            self.reset_hp_baselines(reason="boss engaged")

            for h_addr, (h_name, is_me, *_) in heroes.items():
                if is_me:
                    self.boss_session.register_player(h_name, is_me=is_me)

        # --- dummy engagement (scene-driven, own group like boss) ---#
        if dummy_present and not self.in_dummy_fight and not self.in_boss_fight:
            d_addr, d_name, d_hp = dummy_present
            if self.trash_session.group_damage > 0:
                if self.trash_session.state == "COMBAT":
                    self.trash_session.pause()
                self.archive_current_encounter("Trash Mobs Cleared")

            self.in_dummy_fight = True
            self.dummy_session.reset()
            self.dummy_session.target_addr = d_addr
            self.dummy_session.target_name = d_name
            self.dummy_session.target_hp = d_hp
            self.dummy_session.target_max_hp = d_hp
            self.reset_hp_baselines(reason="dummy engaged")
            for h_addr, (h_name, is_me, *_) in heroes.items():
                if is_me:
                    self.dummy_session.register_player(h_name, is_me=is_me)

        if self.in_dummy_fight and not self.in_boss_fight:
            foe = current_foes.get(self.dummy_session.target_addr)
            if foe is None:
                for f_addr, f_info in current_foes.items():
                    if f_info[3] and f_info[0] > 0:  # is_dummy and hp > 0
                        self.dummy_session.target_addr = f_addr
                        foe = f_info
                        break
            if foe is not None:
                self.dummy_session.target_hp = foe[0]
                self.dummy_session.target_max_hp = max(self.dummy_session.target_max_hp, foe[0])
                if foe[1]:
                    self.dummy_session.target_name = foe[1]

        if self.in_boss_fight:
            now_b = time.time()
            foe = current_foes.get(self.boss_session.target_addr)
            if foe is None:
                # The pinned boss is missing from this scan. Don't hop to
                # another boss-tagged entity on a single miss — that made the
                # meter label alternate between rift bosses every tick.
                # Re-target only once the pinned boss has stayed gone past
                # BOSS_RELATCH_GRACE_S (tests can shrink it via
                # _boss_relatch_grace_s), and then pick the NEAREST remaining
                # boss (the one being fought / the phase-swapped respawn).
                # A raw-id tie can't disambiguate — every rift boss shares the
                # same raw id — so distance is the only stable signal.
                if self._boss_missing_at <= 0.0:
                    self._boss_missing_at = now_b
                grace = getattr(self, "_boss_relatch_grace_s",
                                BOSS_RELATCH_GRACE_S)
                if now_b - self._boss_missing_at >= grace:
                    best = None
                    best_d = math.inf
                    for f_addr, f_info in current_foes.items():
                        if not (f_info[2] and f_info[0] > 0):  # is_boss, alive
                            continue
                        d = (math.dist(pxyz, (f_info[4], f_info[5], f_info[6]))
                             if pxyz else 0.0)
                        if d < best_d:
                            best_d, best = d, (f_addr, f_info)
                    if best is not None:
                        b_addr, b_info = best
                        self.boss_session.target_addr = b_addr
                        self.boss_session.target_hp = b_info[0]
                        self.boss_session.target_max_hp = max(
                            self.boss_session.target_max_hp, b_info[0])
                        self.boss_session.target_name = b_info[1] or \
                            self.boss_session.target_name
                        foe = b_info
                        self._boss_missing_at = 0.0
            else:
                self._boss_missing_at = 0.0
            if foe is not None:
                self.boss_session.target_hp = foe[0]
                self.boss_session.target_max_hp = max(self.boss_session.target_max_hp, foe[0])
                if foe[1]:
                    self.boss_session.target_name = foe[1]
                self._boss_last_seen = now_b

        # --- consume REAL damage events ----------------------------------#
        self._incoming_tick = set()
        now = time.time()
        if src is not None:
            try:
                new_cursor, events = src.events_after(self._src_cursor)
                self._src_cursor = new_cursor
                if events:
                    self._last_events_ts = now   # any event = reader is alive
                    self._hp_diff_enabled = False   # bridge is live; skip HP-diff
                for ev in events:
                    self._apply_event(ev, pa, heroes, current_foes, now)
            except Exception:
                pass
        else:
            # No source at all — enable HP-diff so the meter isn't dead
            # (unless the user turned the fallback off).
            self._hp_diff_enabled = self.hp_est_allowed

        # Re-enable HP-diff after a lull (bridge went silent).
        if (self._hp_diff_enabled is False and self.hp_est_allowed
                and now - self._last_events_ts > self.hp_est_lull_s):
            self._hp_diff_enabled = True

        # Authoritative classes win over record-time skill guesses.
        self._sync_known_classes()

        # --- ENEMY HP-DIFF: infer outgoing damage from foe HP drops -----#
        # Old-school fallback (v0.3.4 style): when the bridge is silent, diff enemy
        # HP snapshots into outgoing damage. Only the local player's DPS is estimated
        # (self-only, same assumption as the old DpsMeter). Real events always win.
        if self._hp_diff_enabled and self.hp_est_allowed and pa is not None:
            me_name = self._resolve_hero_name(pa, True)
            for f_addr, foe in current_foes.items():
                hp, uid = foe[0], foe[1]
                if hp <= 0:
                    continue
                prev = self._foe_hp.get(f_addr)
                if prev is not None:
                    prev_hp, prev_uid = prev
                    # Guard: unit changed or implausible HP jump = new entity, not a drop.
                    if uid != prev_uid or (prev_hp and hp > prev_hp * 1.5):
                        self._foe_hp[f_addr] = (hp, uid)
                        continue
                    drop = prev_hp - hp
                    if 1.0 < drop <= 5_000_000 and drop == drop:
                        if self.in_boss_fight and f_addr == self.boss_session.target_addr:
                            self.boss_session.record_hit(
                                target_name=self.boss_session.target_name or uid,
                                target_addr=f_addr,
                                target_hp=hp,
                                target_max_hp=self.boss_session.target_max_hp,
                                damage=drop,
                                caster_name=me_name or "You",
                                is_me=True,
                                skill_id="(HP)",
                                skill_name="HP diff (est.)",
                                is_crit=False,
                            )
                        elif self.in_boss_fight:
                            self.boss_adds_session.record_hit(
                                target_name=uid, target_addr=f_addr,
                                target_hp=hp, target_max_hp=max(self._foe_max_hp.get(f_addr, hp), hp),
                                damage=drop, caster_name=me_name or "You",
                                is_me=True, skill_id="(HP)", skill_name="HP diff (est.)",
                                is_crit=False,
                            )
                        else:
                            self.trash_session.record_hit(
                                target_name=uid, target_addr=f_addr,
                                target_hp=hp, target_max_hp=max(self._foe_max_hp.get(f_addr, hp), hp),
                                damage=drop, caster_name=me_name or "You",
                                is_me=True, skill_id="(HP)", skill_name="HP diff (est.)",
                                is_crit=False,
                            )
                        self.overall_session.record_hit(
                            target_name=uid, target_addr=f_addr,
                            target_hp=hp, target_max_hp=max(self._foe_max_hp.get(f_addr, hp), hp),
                            damage=drop, caster_name=me_name or "You",
                            is_me=True, skill_id="(HP)", skill_name="HP diff (est.)",
                            is_crit=False,
                        )
                # Update tracking.
                self._foe_hp[f_addr] = (hp, uid)
                self._foe_max_hp[f_addr] = max(self._foe_max_hp.get(f_addr, hp), hp)

        # --- damage-taken FALLBACK signal: hero HP drops -----------------#
        # Lull-only; never mixes with authoritative events. Labeled ≈ in the UI.
        lull = now - self._last_events_ts > self.hp_est_lull_s
        for h_addr, hp in hero_hp_now.items():
            prev = self._hero_hp.get(h_addr)
            drop = (prev - hp) if prev is not None else 0.0
            if prev is not None and prev > 0 and hp <= 0:
                h_name = self._hero_name(h_addr, pa, heroes)
                if h_name:
                    if self.in_dummy_fight:
                        self.dummy_session.record_death(h_name)
                    elif self.in_boss_fight:
                        self.boss_session.record_death(h_name)
                    else:
                        self.trash_session.record_death(h_name)
                    self.overall_session.record_death(h_name)

            if (prev is not None and prev > 0 and hp > 0
                    and lull
                    and h_addr not in self._incoming_tick
                    and 1.0 < drop <= 5_000_000 and drop == drop):
                if solo_status(self.model, self) is True and h_addr != pa:
                    continue
                self._apply_damage_taken(h_addr, drop, pa, heroes, now,
                                         is_est=True)
        self._hero_hp = hero_hp_now

        # --- boss defeat / end (kill flags + boss HP/despawn) -------------#
        if self.in_boss_fight and self.boss_session.group_damage > 0:
            now = time.time()
            foe = current_foes.get(self.boss_session.target_addr)
            killed = self._boss_kill_pending and (now - self._boss_kill_pending <= BOSS_KILL_GRACE_S)
            if foe is not None and foe[0] <= 0:
                self._end_boss_fight("Boss Defeated")
            elif foe is None:
                idle = now - max(self.boss_session.last_event_time,
                                 self._boss_last_seen or 0.0)
                # Game signals confirm an end only once also damage-idle for BOSS_IDLE_AUX_S.
                game_confirmed = (self._game_combat_end_at > 0
                                  and (now - self._game_combat_end_at)
                                  >= GAME_COMBAT_END_CONFIRM_S)
                if killed or idle > BOSS_GONE_IDLE_S \
                        or (game_confirmed and idle >= BOSS_IDLE_AUX_S):
                    self._end_boss_fight("Boss Defeated" if killed else "Boss Fight")

        # --- combat lull auto-pause (trash / general fights) -------------#
        if not self.in_boss_fight and not self.in_dummy_fight:
            now_lull = time.time()
            trash_lulled = self.trash_session.check_combat_lull(
                now_lull, timeout_s=6.0)
            overall_lulled = self.overall_session.check_combat_lull(
                now_lull, timeout_s=6.0)
            if trash_lulled or overall_lulled:
                # Fight over (idle past the lull): next pull starts fresh.
                self.reset_hp_baselines(reason="combat lull")

    def _end_boss_fight(self, reason: str):
        self.boss_session.pause()
        self.archive_current_encounter(reason)
        self.in_boss_fight = False
        self._boss_kill_pending = 0.0
        self._boss_missing_at = 0.0
        # Encounter over: next fight starts from a clean HP baseline.
        self.reset_hp_baselines(reason=f"{reason}")

    def _end_dummy_fight(self, reason: str):
        self.dummy_session.pause()
        self.archive_current_encounter(reason)
        self.in_dummy_fight = False
        self.reset_hp_baselines(reason=f"{reason}")

    # --- pet/summon owner attribution ------------------------------------#
    @staticmethod
    def _merge_player_into(dst: PlayerParse, src: PlayerParse) -> None:
        """Fold one PlayerParse into another (Party_XXXX row into a known player)."""
        dst.total_damage += src.total_damage
        dst.damage_taken += src.damage_taken
        dst.damage_taken_est += src.damage_taken_est
        dst.heals += src.heals
        dst.hit_count += src.hit_count
        dst.heal_count += src.heal_count
        dst.deaths += src.deaths
        dst.last_hit_ts = max(dst.last_hit_ts, src.last_hit_ts)
        if not dst.hero_class:
            dst.hero_class = src.hero_class
        for sid, ssp in src.skills.items():
            dsp = dst.skills.get(sid)
            if dsp is None:
                dst.skills[sid] = ssp
                continue
            dsp.damage += ssp.damage
            dsp.hit_count += ssp.hit_count
            dsp.crit_count += ssp.crit_count
            dsp.min_hit = min(dsp.min_hit, ssp.min_hit)
            dsp.max_hit = max(dsp.max_hit, ssp.max_hit)
            dsp.heals += ssp.heals
            dsp.heal_count += ssp.heal_count
            dsp.min_heal = min(dsp.min_heal, ssp.min_heal)
            dsp.max_heal = max(dsp.max_heal, ssp.max_heal)
            dsp.damage_taken += ssp.damage_taken
            dsp.taken_count += ssp.taken_count
            dsp.last_hit_ts = max(dsp.last_hit_ts, ssp.last_hit_ts)

    def _fold_party_row(self, old_name: str, owner_name: str,
                        pet_skills: bool = False) -> None:
        """Fold a Party_XXXX row into the owner row (merge + timeline sync; pet rows gain "(pet)")."""
        if not old_name or old_name == owner_name:
            return
        for sess in (self.overall_session, self.trash_session,
                     self.boss_session):
            old = sess.players.pop(old_name, None)
            if old is None:
                continue
            if pet_skills:
                for ssp in old.skills.values():
                    nm = ssp.name or ssp.skill_id
                    if not nm.endswith("(pet)"):
                        ssp.name = f"{nm} (pet)"
            owner = sess.players.get(owner_name)
            if owner is None:
                old.name = owner_name
                sess.players[owner_name] = old
            else:
                self._merge_player_into(owner, old)
            pt = sess.player_timeline
            bucket = pt.pop(old_name, None)
            if bucket:
                ob = pt.setdefault(owner_name, {})
                for sec, amt in bucket.items():
                    ob[sec] = ob.get(sec, 0.0) + amt

    def _map_pet_source(self, src: int, pa: int,
                        heroes: dict[int, tuple[str, bool, tuple[float, float, float], str]],
                        now: float) -> tuple[bool, int]:
        """Re-credit a non-hero source to its owning hero (foes never resolve). Returns ``(from_pet, source)``."""
        if not src or src in heroes or src == pa:
            return False, src
        owner = self._pet_owners.get(src)
        if owner:
            return True, owner
        if now - self._pet_probe_at.get(src, 0.0) < PET_PROBE_S:
            return False, src
        self._pet_probe_at[src] = now
        owner = 0
        try:
            resolve = getattr(self.model, "resolve_pet_owner", None)
            if resolve is not None:
                owner = resolve(src) or 0
        except Exception:
            owner = 0
        if not owner:
            return False, src
        self._pet_owners[src] = owner
        self._pet_last_seen[src] = now
        self._pet_probe_at.pop(src, None)   # resolved: stop probing
        # Fold anything that already stacked under the uid fallback row.
        party = self._fallback_rows.get(src)
        if party:
            try:
                oname, _ome = self._resolve_caster(owner, pa, heroes)
            except Exception:
                oname = None
            if oname and not oname.startswith("Party_"):
                self._fold_party_row(party, oname, pet_skills=True)
                self._fallback_rows.pop(src, None)
        return True, owner

    def _own_proc_source(self, src: int, skill_raw: str, pa: int) -> int:
        """Re-credit an owned-item proc source to the local hero (``pa`` or 0)."""
        if not src or not pa or src == pa:
            return 0
        if src in self._owned_proc_sources:
            return self._owned_proc_sources[src]
        name = names.skill_name(skill_raw or "") or skill_raw or ""
        if name not in OWNED_ITEM_PROC_SKILLS:
            return 0
        cached = self._hero_pointers.get(src)
        if cached and cached[1]:
            return 0
        self._owned_proc_sources[src] = pa
        # Fold this source's uid row into you.
        party = self._fallback_rows.get(src)
        if party:
            try:
                oname, _ome = self._resolve_caster(pa, pa, {})
            except Exception:
                oname = None
            if oname:
                self._fold_party_row(party, oname)
                self._fallback_rows.pop(src, None)
        return pa

    # --- per-event application --------------------------------------------#
    def _confirm_boss_kill(self, ev: DamageEvent, now: float) -> None:
        """Pure kill notification (st.Player.notifyUnitKilled__impl).

        It carries only the killed unit's raw id — no killer, no amount — so
        it can never feed damage rows. Its value is boss-KILL CONFIRMATION:
        when the id matches the ACTIVE boss, arm the boss-end grace, so a boss
        that dies and despawns before its HP ever reads 0 still closes the
        encounter as "Boss Defeated" instead of "Boss Fight".
        """
        if not self.in_boss_fight or self.boss_session.group_damage <= 0:
            return
        uid = (getattr(ev, "target_name", "") or "").strip().lower()
        if not uid:
            return
        boss = self.boss_session
        raw = (self._foe_raw_ids.get(boss.target_addr) or "").strip().lower()
        disp = (boss.target_name or "").replace("👑 ", "").strip().lower()
        if (raw and uid == raw) or (disp and uid == disp):
            if self._boss_kill_pending <= 0.0 and callable(self.log_line):
                try:
                    self.log_line("Top DPS: boss defeat confirmed by kill notification")
                except Exception:
                    pass
            self._boss_kill_pending = now

    def _apply_event(self, ev: DamageEvent, pa: int,
                     heroes: dict[int, tuple[str, bool, tuple[float, float, float], str]],
                     foes: dict[int, tuple[float, str, bool, float, float, float]],
                     now: float):
        amt = ev.amount
        if not amt or amt != amt:
            # Zero-amount kill notification: confirm a boss kill, nothing else.
            if ev.kill:
                self._confirm_boss_kill(ev, now)
            return
        amount = amt
        tgt = ev.target_addr
        src = ev.source_addr
        # Pets carry the pet as serverSource; re-credit to the owning hero.
        from_pet = bool(src and src in self._pet_owners)
        if from_pet:
            src = self._pet_owners[src] or 0

        # Solo Open-World Filter: when ungrouped in open world, only process
        # events involving the local player or local player's pet.
        # Bystanders/strangers fighting nearby are strictly filtered out so
        # combat sessions only start and run when YOU are in combat,
        # guaranteeing 100% local player stats with zero stranger noise.
        if solo_status(self.model, self) is True and pa:
            is_me_ev = bool(getattr(ev, "is_me", False))
            if not is_me_ev:
                is_me_addr = (
                    src == pa
                    or tgt == pa
                    or (from_pet and src == pa)
                    or (tgt in self._pet_owners and self._pet_owners[tgt] == pa)
                    or (src in self._owned_proc_sources and self._owned_proc_sources[src] == pa)
                )
                if not is_me_addr:
                    src_ptr = self._hero_pointers.get(src)
                    tgt_ptr = self._hero_pointers.get(tgt)
                    if (src_ptr and src_ptr[1]) or (tgt_ptr and tgt_ptr[1]):
                        is_me_addr = True
                if not is_me_addr:
                    own_proc = self._own_proc_source(src, getattr(ev, "skill", ""), pa)
                    if own_proc == pa:
                        is_me_addr = True
                        src = pa
                if not is_me_addr:
                    my_name = self._resolve_hero_name(pa, True)
                    s_nm = getattr(ev, "source_name", "")
                    t_nm = getattr(ev, "target_name", "")
                    if (s_nm and (s_nm == my_name or s_nm in ("Player", "You"))) or \
                       (t_nm and (t_nm == my_name or t_nm in ("Player", "You"))):
                        is_me_addr = True
                if not is_me_addr:
                    return

        # Direct name path: hooked events already have authoritative caster/target names.
        if getattr(ev, "source_name", ""):
            if ev.incoming:
                t_name = getattr(ev, "target_name", "") or "Player"
                self._apply_damage_taken(tgt, amount, pa, heroes, now,
                                         target_name=t_name, is_me=getattr(ev, "is_me", False))
            elif ev.kind in (K_HEAL, K_SHIELD) or amt < 0:
                self._apply_heal(ev, abs(amount), src, tgt, pa, heroes, now,
                                 from_pet=from_pet)
            else:
                self._apply_damage(ev, amount, src, tgt, pa, heroes, foes, now,
                                   from_pet=from_pet)
            return

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
            self._incoming_tick.add(h_addr)
            self._apply_damage_taken(h_addr, amount, pa, heroes, now,
                                     target_name=getattr(ev, "target_name", ""),
                                     is_me=getattr(ev, "is_me", None))
            return
        elif tgt in self._pet_owners and self._pet_owners[tgt] in heroes:
            # a hit on my pet counts as damage taken by its owner
            owner = self._pet_owners[tgt]
            self._incoming_tick.add(owner)
            self._apply_damage_taken(owner, amount, pa, heroes, now,
                                     is_me=(owner == pa))
            return
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
            self._apply_heal(ev, amount, src, tgt, pa, heroes, now,
                             from_pet=from_pet)
            return
        self._apply_damage(ev, amount, src, tgt, pa, heroes, foes, now,
                           from_pet=from_pet)

    def _apply_damage_taken(self, tgt: int, amount: float, pa: int,
                            heroes: dict[int, tuple[str, bool, tuple[float, float, float], str]],
                            now: float, is_est: bool = False,
                            target_name: str = "", is_me: bool | None = None):
        """Record damage taken by a hero into the active segment + overall."""
        if target_name and target_name not in ("Enemy", ""):
            name = target_name
            if is_me is None:
                is_me = (target_name in ("Player", "You")
                         or (pa and self._resolve_hero_name(pa, True) == target_name))
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
            return
        if self.in_boss_fight:
            self.boss_session.record_damage_taken(name, amount, is_est=is_est,
                                                  is_me=is_me)
        else:
            self.trash_session.record_damage_taken(name, amount, is_est=is_est,
                                                   is_me=is_me)
        self.overall_session.record_damage_taken(name, amount, is_est=is_est,
                                                 is_me=is_me)

    def _apply_heal(self, ev: DamageEvent, amount: float, src: int, tgt: int, pa: int,
                    heroes: dict[int, tuple[str, bool, tuple[float, float, float], str]],
                    now: float, from_pet: bool = False):
        if getattr(ev, "source_name", ""):
            caster = ev.source_name
            is_me = bool(getattr(ev, "is_me", False))
            if is_me and pa:
                loc = self._resolve_hero_name(pa, True)
                if loc:
                    caster = loc
        else:
            caster, is_me = self._resolve_caster(src, pa, heroes)
        if caster is None:
            return
        if getattr(ev, "target_name", ""):
            target_name = ev.target_name
            if (target_name in ("Player", "Hero") or not target_name) and is_me and pa:
                loc = self._resolve_hero_name(pa, True)
                if loc:
                    target_name = loc
        else:
            target_name = self._hero_name(tgt, pa, heroes) or (self._foe_names.get(tgt) or "Hero")
        sid, sname = self._skill_labels(ev, fallback=("Regen", "Heal"))
        if from_pet:
            sname = f"{sname} (pet)"
        is_dummy = udata.is_training_dummy(target_name) or (
            tgt and udata.is_training_dummy(self._foe_raw_ids.get(tgt)))
        boss = self.in_boss_fight
        # Engage dummy fight on event path (sceneless injection)
        # Canonical display spelling (same rule as _apply_damage): a heal
        # event can name the dummy by raw id; its session label must match
        # the scan's "🎯 <display name>" form instead of flipping spellings.
        if is_dummy:
            target_name = _canonical_unit_display(target_name)
        if is_dummy and not self.in_dummy_fight and not self.in_boss_fight:
            self.in_dummy_fight = True
            self.dummy_session.reset()
            self.dummy_session.target_name = f"🎯 {target_name}"
            self.reset_hp_baselines(reason="dummy engaged (event)")
        if is_dummy:
            self.dummy_session.record_heal(target_name=target_name, heal_amount=amount,
                                              caster_name=caster, is_me=is_me,
                                              skill_id=sid, skill_name=sname)
        elif boss:
            self.boss_session.record_heal(target_name=target_name, heal_amount=amount,
                                              caster_name=caster, is_me=is_me,
                                              skill_id=sid, skill_name=sname)
        else:
            self.trash_session.record_heal(target_name=target_name, heal_amount=amount,
                                              caster_name=caster, is_me=is_me,
                                              skill_id=sid, skill_name=sname)
        self.overall_session.record_heal(target_name=target_name, heal_amount=amount,
                                         caster_name=caster, is_me=is_me,
                                         skill_id=sid, skill_name=sname)

    def _apply_damage(self, ev: DamageEvent, amount: float, src: int, tgt: int, pa: int,
                      heroes: dict[int, tuple[str, bool, tuple[float, float, float], str]],
                      foes: dict[int, tuple[float, str, bool, float, float, float]],
                      now: float, from_pet: bool = False):
        if getattr(ev, "source_name", ""):
            caster = ev.source_name
            is_me = bool(getattr(ev, "is_me", False))
            if is_me and pa:
                loc = self._resolve_hero_name(pa, True)
                if loc:
                    caster = loc
        else:
            caster, is_me = self._resolve_caster(src, pa, heroes)
        if caster is None:
            return
        if getattr(ev, "target_name", ""):
            target_name = ev.target_name
        else:
            target_name = self._foe_name(tgt, foes) or self._hero_name(tgt, pa, heroes) or "Enemy"

        clean_tgt = (target_name or "").replace("👑 ", "").strip().lower()
        clean_boss = (self.boss_session.target_name or "").replace("👑 ", "").strip().lower()

        is_boss_named = bool(
            clean_tgt and clean_boss and clean_boss != "none" and (
                clean_tgt == clean_boss
                or clean_tgt in clean_boss
                or clean_boss in clean_tgt
            )
        )
        is_dummy_unit = bool(
            udata.is_training_dummy(target_name)
            or udata.is_training_dummy(clean_tgt)
            or udata.is_training_dummy(self._foe_raw_ids.get(tgt))
        )
        is_boss_unit = bool(
            udata.is_boss(target_name)
            or udata.is_boss(clean_tgt)
            or (clean_tgt.startswith("demonsuperelite") and "falseclone" not in clean_tgt)
            or "boss" in clean_tgt
        )
        # NOTE: is_dummy_unit intentionally excluded from is_boss_unit —
        # dummies route to their own group, not the boss group.

        foe = foes.get(tgt)

        # Stored/displayed names use the canonical display spelling; the
        # routing flags above deliberately keep the raw event spelling (unit
        # family checks) and stay untouched.
        disp_name = _canonical_unit_display(target_name or "Enemy")

        # Auto-engage dummy fight first (own group, parallel to boss)
        if not self.in_dummy_fight and not self.in_boss_fight and (
                is_dummy_unit or (foe is not None and foe[3])):
            self.in_dummy_fight = True
            if self.trash_session.group_damage > 0:
                if self.trash_session.state == "COMBAT":
                    self.trash_session.pause()
                self.archive_current_encounter("Trash Mobs Cleared")
            self.dummy_session.reset()
            dummy_name = (disp_name or "").replace("🎯", "").strip()
            if not dummy_name.startswith("🎯 "):
                dummy_name = f"🎯 {dummy_name}"
            self.dummy_session.target_name = dummy_name
            self.dummy_session.target_addr = tgt or (getattr(foe, "addr", 0) if foe else 0)
            if foe is not None and foe[0] > 0:
                self.dummy_session.target_hp = foe[0]
                self.dummy_session.target_max_hp = foe[0]
            if not self.dummy_session.target_addr:
                for f_a, f_info in foes.items():
                    if f_info[3]:  # is_dummy
                        self.dummy_session.target_addr = f_a
                        self.dummy_session.target_hp = f_info[0]
                        self.dummy_session.target_max_hp = f_info[0]
                        break
            self.reset_hp_baselines(reason="dummy engaged (damage)")
            for h_addr, (h_name, is_m, *_) in heroes.items():
                if is_m:
                    self.dummy_session.register_player(h_name, is_me=is_m)

        # Auto-engage boss fight if hitting a boss even if scene scanner missed boss_present
        # (e.g. after manual reset during boss encounter or delayed spawn)
        if not self.in_boss_fight and (is_boss_unit or (foe is not None and foe[2])):
            self.in_boss_fight = True
            if self.trash_session.group_damage > 0:
                if self.trash_session.state == "COMBAT":
                    self.trash_session.pause()
                self.archive_current_encounter("Trash Mobs Cleared")
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
            self.reset_hp_baselines(reason="boss engaged (damage)")
            for h_addr, (h_name, is_m, *_) in heroes.items():
                if is_m:
                    self.boss_session.register_player(h_name, is_me=is_m)

        is_dummy = is_dummy_unit
        on_dummy = self.in_dummy_fight and (
            (tgt and tgt == self.dummy_session.target_addr)
            or is_dummy
            or (self.dummy_session.target_name in ("None", "") and not is_dummy)
        )
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

        sid, sname = self._skill_labels(ev, fallback=("Attack", "Attack"))
        if from_pet:
            sname = f"{sname} (pet)"

        if on_dummy:
            self.dummy_session.record_hit(target_name=disp_name, target_addr=tgt,
                                          target_hp=target_hp, target_max_hp=target_max,
                                          damage=amount, caster_name=caster, is_me=is_me,
                                          skill_id=sid, skill_name=sname, is_crit=ev.crit)
        elif on_boss:
            self.boss_session.record_hit(target_name=disp_name, target_addr=tgt,
                                          target_hp=target_hp, target_max_hp=target_max,
                                          damage=amount, caster_name=caster, is_me=is_me,
                                          skill_id=sid, skill_name=sname, is_crit=ev.crit)
        elif self.in_boss_fight:
            self.boss_adds_session.record_hit(target_name=disp_name, target_addr=tgt,
                                              target_hp=target_hp, target_max_hp=target_max,
                                              damage=amount, caster_name=caster, is_me=is_me,
                                              skill_id=sid, skill_name=sname, is_crit=ev.crit)
        else:
            self.trash_session.record_hit(target_name=disp_name, target_addr=tgt,
                                          target_hp=target_hp, target_max_hp=target_max,
                                          damage=amount, caster_name=caster, is_me=is_me,
                                          skill_id=sid, skill_name=sname, is_crit=ev.crit)
        self.overall_session.record_hit(target_name=disp_name, target_addr=tgt,
                                        target_hp=target_hp, target_max_hp=target_max,
                                        damage=amount, caster_name=caster, is_me=is_me,
                                        skill_id=sid, skill_name=sname, is_crit=ev.crit)

        # Kill flags arm the boss end; HP/despawn check above confirms before archiving.
        if ev.kill and on_boss:
            self._boss_kill_pending = now

    @staticmethod
    def _skill_labels(ev: DamageEvent, fallback: tuple[str, str]) -> tuple[str, str]:
        sid = (ev.skill or "").strip()
        if not sid or sid == "?":
            # Unread skills stay "Unknown", never masquerade as basic attacks.
            return "?", "Unknown"
        return sid, names.skill_name(sid) or sid
